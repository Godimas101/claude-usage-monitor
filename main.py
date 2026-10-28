"""Entry point of Claude Usage Monitor v2.

    main.py                              run the monitor: tray icon, widgets, local API
    main.py --dashboard --port N         the dashboard window (a child process of the monitor)
    main.py --print-version FILE         write the version to FILE and exit (frozen-build check;
                                         exit code 1 when it cannot be written or the build has no VERSION)
    main.py --dump-frames DIR            render every theme, variant, state and scale to PNGs
    main.py --smoke-test SECONDS FILE    run the real app for SECONDS, exercise it, write a report
    main.py --status [--json]            print the plan usage for a Claude session (see docs/AGENT_HOOK.md)
    main.py --hook [--threshold N] [--always]   a Claude Code hook that adds the usage to the context
    main.py --gate [--session N] [--weekly N]   a PreToolUse hook that refuses new agents past a cap
    main.py --mcp                        an MCP server on stdio with the tool get_plan_usage
    main.py --wait [--session ID]        wait until the usage guard lets work resume (a Claude session's
                                         background command; its exit wakes the session)

The build is windowed, so there is no console: results go to files, and an unhandled exception is
written to the log. --status, --hook, --gate, --mcp and --wait answer on stdout, which exists when the caller
gives the process a pipe, as Claude Code does. Dev isolation: CUM_APPDATA_DIR puts settings, database and log in another
folder, and CUM_DEV_INSTANCE=1 uses its own single-instance mutex and tags the tray tooltip.
"""
from __future__ import annotations

import faulthandler
import math
import re
import sys
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import version

DASHBOARD_LOG = "dashboard.log"
AGENT_LOG = "agent.log"             # --status, --hook, --gate and --mcp: many can run at once, beside the app
AGENT_KINDS = ("status", "hook", "gate", "mcp", "wait")
HOOK_KINDS = ("hook", "gate")       # Claude Code hooks: exit 0 whatever happens, never blocking a prompt or a tool
FAULT_LOG = "faults.log"
UNKNOWN_VERSION = "0.0.0"           # what version.py reports when it cannot read the VERSION file
_fault_file: IO[str] | None = None  # faulthandler needs its file open for the life of the process


@dataclass(frozen=True)
class Command:
    """What the command line asks for. ``kind`` is "app", "dashboard", "print_version",
    "dump_frames", "smoke_test", "status", "hook", "gate", "mcp", "wait" or "error" (then ``error`` says why)."""

    kind: str = "app"
    port: int = 0
    path: str | None = None
    seconds: float = 0.0
    error: str | None = None
    as_json: bool = False
    threshold: float | None = None   # --hook --threshold N; None is the hook's default
    always: bool = False
    session_cap: float | None = None  # --gate --session N / --weekly N; None is the gate's default
    weekly_cap: float | None = None
    session_id: str | None = None    # --wait --session ID


def _values(args: list[str], flag: str, count: int) -> list[str] | None:
    """The ``count`` words after ``flag``, or None when they are missing or look like other flags."""
    at = args.index(flag)
    words = args[at + 1:at + 1 + count]
    if len(words) < count or any(word.startswith("--") for word in words):
        return None
    return words


def _port(args: list[str]) -> tuple[int, str | None]:
    """(port, error) from ``--port N``. A missing option is port 0, which the dashboard rejects."""
    if "--port" not in args:
        return 0, None
    words = _values(args, "--port", 1)
    if words is None or not words[0].isdigit() or not 0 < int(words[0]) < 65536:
        return 0, "--port needs a number from 1 to 65535"
    return int(words[0]), None


def _percent(args: list[str], flag: str) -> float | None:
    """``flag N`` as a percent, or None. A bad value is ignored rather than refused: a hook that
    exits with code 2 blocks the user's prompt or the tool call."""
    if flag not in args:
        return None
    words = _values(args, flag, 1)
    try:
        value = float(words[0]) if words else math.nan
    except ValueError:
        return None
    return value if math.isfinite(value) and 0 <= value <= 1000 else None


def _session_id(args: list[str]) -> str | None:
    """``--session ID`` for --wait: a Claude session id, kept to letters, digits, dashes and underscores."""
    words = _values(args, "--session", 1) if "--session" in args else None
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", words[0])[:64] if words else ""
    return cleaned or None


def parse_args(argv: Sequence[str]) -> Command:
    """Read the command line. Unknown words are ignored (a windowed app must not die on them), and
    when several modes are given the first of print-version, dashboard, dump-frames, smoke-test,
    status, hook, gate, wait, mcp wins."""
    args = list(argv)
    if "--print-version" in args:
        words = _values(args, "--print-version", 1)
        return Command("print_version", path=words[0] if words else None)  # v1: no file, nothing to do
    if "--dashboard" in args:
        port, error = _port(args)
        return Command("error", error=error) if error else Command("dashboard", port=port)
    if "--dump-frames" in args:
        words = _values(args, "--dump-frames", 1)
        if words is None:
            return Command("error", error="--dump-frames needs an output folder")
        return Command("dump_frames", path=words[0])
    if "--smoke-test" in args:
        words = _values(args, "--smoke-test", 2)
        try:
            seconds = float(words[0]) if words else math.nan
        except ValueError:
            seconds = math.nan
        if words is None or not math.isfinite(seconds) or seconds <= 0:
            return Command("error", error="--smoke-test needs SECONDS (a positive number) and a report file")
        return Command("smoke_test", path=words[1], seconds=seconds)
    if "--status" in args:
        return Command("status", as_json="--json" in args)
    if "--hook" in args:
        return Command("hook", threshold=_percent(args, "--threshold"), always="--always" in args)
    if "--gate" in args:
        return Command("gate", session_cap=_percent(args, "--session"), weekly_cap=_percent(args, "--weekly"))
    if "--wait" in args:
        return Command("wait", session_id=_session_id(args))
    if "--mcp" in args:
        return Command("mcp")
    return Command("app")


# ── Commands ──────────────────────────────────────────────────────────────────

def _say(text: str) -> None:
    """Print when there is a console (a windowed build has none)."""
    if sys.stdout is not None:
        print(text)


def _print_version(path: str | None) -> int:
    """Headless hook for the frozen build: write the bundled version to a file. If it ever read the
    wrong VERSION, the updater would think it was always behind, so a version that was not read
    (a build without VERSION) and a file that cannot be written both end in exit code 1."""
    if not path:
        return 0
    if version.APP_VERSION == UNKNOWN_VERSION:
        return _fail("this build has no readable VERSION file")
    try:
        Path(path).write_text(version.APP_VERSION, encoding="utf-8")
    except OSError as exc:
        return _fail(f"could not write the version to {path}: {exc.strerror or type(exc).__name__}")
    return 0


def _fail(message: str) -> int:
    """Report a failed command on stderr (a windowed build has none) and in the log. Returns 1."""
    if sys.stderr is not None:
        print(message, file=sys.stderr)
    try:
        from usage_monitor.log import get_logger, setup_logging

        setup_logging()
        get_logger("main").error("%s", message)
    except Exception:  # the exit code still tells the caller; nothing else can be done
        pass
    return 1


def _dump_frames(folder: str) -> int:
    from usage_monitor.render.dump import dump_frames

    report = dump_frames(folder)
    _say(f"wrote {report.files} frames and {len(report.sheets)} contact sheets to {report.out_dir}")
    return 0


def _dashboard(argv: Sequence[str]) -> int:
    from usage_monitor import paths
    from usage_monitor.log import mirror_logger, setup_logging
    from usage_monitor.ui.dashboard import dashboard_main

    # Its own file: two processes must not rotate the same log.
    setup_logging(path=paths.app_data_dir() / DASHBOARD_LOG)
    mirror_logger("pywebview")  # what the web view reports about itself (an unusable renderer, a failed start) goes there too
    _install_crash_hooks()      # the pywebview event threads run here: without a hook they die silently
    return dashboard_main(list(argv))


def _app() -> int:
    from usage_monitor.app import MonitorApp

    _install_crash_hooks()
    return MonitorApp().run()


def _smoke_test(seconds: float, report: str) -> int:
    from usage_monitor.app import run_smoke_test

    _install_crash_hooks()
    return run_smoke_test(seconds, report)


def _agent(command: Command) -> int:
    """--status, --hook, --gate, --mcp and --wait. No UI and no single-instance mutex, so they run beside the app."""
    from datetime import datetime, timezone

    from usage_monitor import paths
    from usage_monitor.agent import cli
    from usage_monitor.log import setup_logging

    setup_logging("WARNING", path=paths.app_data_dir() / AGENT_LOG)
    folder = paths.app_data_dir()

    def clock() -> datetime:
        return datetime.now(timezone.utc)

    if command.kind == "wait":  # its ending wakes the session, so it runs even with nowhere to answer
        return cli.run_wait(session_id=command.session_id, folder=folder, clock=clock,
                            app_version=version.APP_VERSION, out=sys.stdout.buffer if sys.stdout else None)
    if sys.stdout is None:  # a windowed build started without a pipe has nowhere to answer
        return 0 if command.kind in HOOK_KINDS else 1
    out = sys.stdout.buffer
    stdin = sys.stdin.buffer if sys.stdin is not None else None
    if command.kind == "status":
        return cli.run_status(as_json=command.as_json, folder=folder, clock=clock,
                              app_version=version.APP_VERSION, out=out)
    if command.kind == "hook":
        threshold = cli.HOOK_THRESHOLD if command.threshold is None else command.threshold
        return cli.run_hook(threshold=threshold, always=command.always, folder=folder, clock=clock,
                            app_version=version.APP_VERSION, stdin=stdin, out=out)
    if command.kind == "gate":
        return cli.run_gate(
            session_cap=cli.GATE_SESSION_CAP if command.session_cap is None else command.session_cap,
            weekly_cap=cli.GATE_WEEKLY_CAP if command.weekly_cap is None else command.weekly_cap,
            folder=folder, clock=clock, app_version=version.APP_VERSION, stdin=stdin, out=out)
    if stdin is None:
        return 1
    return cli.run_mcp(folder=folder, clock=clock, app_version=version.APP_VERSION, stdin=stdin, out=out)


def run_command(command: Command, argv: Sequence[str]) -> int:
    if command.kind == "print_version":
        return _print_version(command.path)
    if command.kind == "dashboard":
        return _dashboard(argv)
    if command.kind == "dump_frames":
        return _dump_frames(command.path or "")
    if command.kind == "smoke_test":
        return _smoke_test(command.seconds, command.path or "")
    if command.kind in AGENT_KINDS:
        return _agent(command)
    return _app()


# ── Crash handling ────────────────────────────────────────────────────────────

def _enable_faulthandler() -> None:
    """Write the Python stack of a hard crash (an access violation in a ctypes call) to faults.log."""
    global _fault_file
    try:
        from usage_monitor import paths

        _fault_file = open(paths.app_data_dir() / FAULT_LOG, "a", encoding="utf-8")
        faulthandler.enable(file=_fault_file, all_threads=True)
    except Exception:  # diagnostics only; the app must start without them
        _fault_file = None


def _log_thread_crash(args: threading.ExceptHookArgs) -> None:
    from usage_monitor.log import get_logger

    get_logger("main").error(
        "unhandled exception in thread %s", args.thread.name if args.thread else "?",
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )


def _install_crash_hooks() -> None:
    _enable_faulthandler()
    threading.excepthook = _log_thread_crash


def _log_crash(kind: str) -> None:
    """Write the exception being handled, with its traceback, to the log."""
    try:
        from usage_monitor.log import get_logger, setup_logging

        if kind != "dashboard" and kind not in AGENT_KINDS:  # these set up their own log file already
            setup_logging()
        get_logger("main").critical("unhandled exception in the %s command", kind, exc_info=True)
    except Exception:  # nothing else can be done when logging itself fails
        pass


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = parse_args(args)
    if command.kind == "error":
        if sys.stderr is not None:
            sys.stderr.write(f"{command.error}\n")
        return 2
    try:
        return run_command(command, args)
    except KeyboardInterrupt:
        return 130
    except Exception:
        _log_crash(command.kind)
        return 0 if command.kind in HOOK_KINDS else 1


if __name__ == "__main__":
    sys.exit(main())
