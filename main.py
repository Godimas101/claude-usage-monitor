"""Entry point of Claude Usage Monitor v2.

    main.py                              run the monitor: tray icon, widgets, local API
    main.py --dashboard --port N         the dashboard window (a child process of the monitor)
    main.py --print-version FILE         write the version to FILE and exit (frozen-build check;
                                         exit code 1 when it cannot be written or the build has no VERSION)
    main.py --dump-frames DIR            render every theme, variant, state and scale to PNGs
    main.py --smoke-test SECONDS FILE    run the real app for SECONDS, exercise it, write a report

The build is windowed, so there is no console: results go to files, and an unhandled exception is
written to the log. Dev isolation: CUM_APPDATA_DIR puts settings, database and log in another
folder, and CUM_DEV_INSTANCE=1 uses its own single-instance mutex and tags the tray tooltip.
"""
from __future__ import annotations

import faulthandler
import math
import sys
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import version

DASHBOARD_LOG = "dashboard.log"
FAULT_LOG = "faults.log"
UNKNOWN_VERSION = "0.0.0"           # what version.py reports when it cannot read the VERSION file
_fault_file: IO[str] | None = None  # faulthandler needs its file open for the life of the process


@dataclass(frozen=True)
class Command:
    """What the command line asks for. ``kind`` is "app", "dashboard", "print_version",
    "dump_frames", "smoke_test" or "error" (then ``error`` says why)."""

    kind: str = "app"
    port: int = 0
    path: str | None = None
    seconds: float = 0.0
    error: str | None = None


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


def parse_args(argv: Sequence[str]) -> Command:
    """Read the command line. Unknown words are ignored (a windowed app must not die on them), and
    when several modes are given the first of print-version, dashboard, dump-frames, smoke-test wins."""
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
    from usage_monitor.log import setup_logging
    from usage_monitor.ui.dashboard import dashboard_main

    # Its own file: two processes must not rotate the same log.
    setup_logging(path=paths.app_data_dir() / DASHBOARD_LOG)
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


def run_command(command: Command, argv: Sequence[str]) -> int:
    if command.kind == "print_version":
        return _print_version(command.path)
    if command.kind == "dashboard":
        return _dashboard(argv)
    if command.kind == "dump_frames":
        return _dump_frames(command.path or "")
    if command.kind == "smoke_test":
        return _smoke_test(command.seconds, command.path or "")
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

        if kind != "dashboard":  # the dashboard set up its own log file already
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
        return 1


if __name__ == "__main__":
    sys.exit(main())
