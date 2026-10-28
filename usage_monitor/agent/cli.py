"""The command-line modes for Claude sessions (#24): --status, --hook, --gate, --mcp and --wait (#27).

They start without the UI and without the single-instance mutex, so they run beside the app.
A windowed build has a stdout only when the caller hands it a pipe, as Claude Code always does.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO

from usage_monitor.agent import guard, mcp, sessions, status
from usage_monitor.log import get_logger

log = get_logger(__name__)

HOOK_THRESHOLD = 80.0      # --hook speaks once a limit reaches this percent...
HOOK_STEP = 5.0            # ...and again each time one climbs another step
HOOK_MEMORY = sessions.SEEN_NAME
HOOK_MEMORY_DAYS = 7
SESSION_START = "SessionStart"
GATE_SESSION_CAP = 95.0    # --gate refuses new agents and workflows from these percents
GATE_WEEKLY_CAP = 90.0
GATE_WAIT_S = 5.0          # the gate waits less for a paused monitor: it runs before every agent launch
WAIT_POLL_S = 30.0         # --wait looks at the reading this often (a file read: the app does the fetching)
WAIT_FETCH = timedelta(minutes=15)  # with the app closed, --wait asks the API itself at most this often


def run_status(*, as_json: bool, folder: Path, clock: Callable[[], datetime], app_version: str,
               out: BinaryIO) -> int:
    """--status [--json]: the summary line, or the whole reading. Exit code 1 when there is none."""
    doc, running = status.current(folder, now=clock(), fetch=True, wait_s=status.WAIT_S, app_version=app_version)
    report = status.report(doc, now=clock(), running=running)
    _write(out, json.dumps(report, indent=1) if as_json else report["summary"])
    return 0 if doc and doc.get("usage_fetched_at") else 1  # the same test as the summary's "no reading"


def run_hook(*, threshold: float, always: bool, folder: Path, clock: Callable[[], datetime],
             app_version: str, stdin: BinaryIO | None, out: BinaryIO) -> int:
    """--hook: a Claude Code hook (SessionStart, UserPromptSubmit, PostToolUse) that adds to the context.

    - The summary line, when a limit first reaches ``threshold`` in a session and again each time one
      climbs another HOOK_STEP, or every time with ``always``.
    - The usage guard's notice (#27) while the guard holds: once per holding episode for each agent of
      a session (the main conversation and each subagent), and at every SessionStart, which also comes
      after a compaction. Only the main conversation is offered --wait.

    It never blocks the prompt or the tool: it always exits 0 and stays silent when there is nothing
    to say. Only SessionStart may fetch a reading of its own.
    """
    event = _hook_input(stdin)
    name = event.get("hook_event_name") if isinstance(event.get("hook_event_name"), str) else "UserPromptSubmit"
    try:
        now = clock()
        doc, running = status.current(folder, now=now, fetch=name == SESSION_START, app_version=app_version)
        memory = _load_memory(folder, now)
        session, agent = str(event.get("session_id") or ""), str(event.get("agent_id") or "")
        parts = []
        heard = doc is not None and not always and _should_speak(memory, session, doc, threshold, now)
        if doc is not None and (always or heard):
            parts.append(status.summary(doc, now=now, running=running))
        notice = _guard_notice(memory, folder, session, agent, doc, now, every_time=name == SESSION_START)
        if notice:
            parts.append(notice)
        if heard or notice:
            _save_memory(folder, memory)
    except Exception:  # a broken hook must never get in the way of the prompt
        log.exception("the usage hook failed")
        return 0
    if parts:
        _write(out, json.dumps({"hookSpecificOutput": {"hookEventName": name, "additionalContext": "\n\n".join(parts)}}))
    return 0


def run_wait(*, session_id: str | None, folder: Path, clock: Callable[[], datetime], app_version: str,
             out: BinaryIO | None, sleep: Callable[[float], None] = time.sleep,
             epoch: Callable[[], float] = time.time) -> int:
    """--wait (#27): a background command a Claude session runs after it wound down for the usage
    guard. It exits when work can resume, and the exit wakes the session:

    - the guard no longer holds: every watched limit is under its cap (or its window has reset), or
      the guard was turned off. Several waits are released ``stagger_s`` apart, oldest first;
    - "Resume now" was pressed in the tray or the dashboard: every wait ends at once.

    With no reading at all it keeps waiting. It only reads: with the app running the app fetches,
    and with it closed a stale reading is replaced by one request, as for --status.
    """
    started = epoch()
    entry = sessions.register_wait(folder, session_id, clock())
    try:
        due: datetime | None = None
        asked: datetime | None = None
        while True:
            now = clock()
            doc, running = status.current(folder, now=now, fetch=False, app_version=app_version)
            if sessions.resumed_since(folder, started):
                return _released(out, "Resume now was pressed (the limits were not checked)", doc, now, running)
            if not running and status.is_stale(doc, now) and (asked is None or now - asked >= WAIT_FETCH):
                asked = now
                doc, running = status.current(folder, now=now, fetch=True, app_version=app_version)
            verdict = guard.for_reader(doc, now, folder) if doc and doc.get("usage_fetched_at") else None
            if verdict is None or verdict["state"] == guard.HOLDING:
                due = None
            elif due is None:
                due = now + timedelta(seconds=_slot(folder) * _stagger(verdict))
            if due is not None and now >= due:
                reason = ("the usage guard was turned off" if verdict and verdict["state"] == guard.OFF
                          else "every watched limit is under its cap again")
                return _released(out, reason, doc, now, running)
            sleep(WAIT_POLL_S if due is None else min(WAIT_POLL_S, max(1.0, (due - now).total_seconds())))
    except Exception:
        log.exception("the usage wait failed")
        _say(out, "Claude Usage Monitor: the wait stopped because of an error (agent.log in the app data "
                  "folder says which). Check the plan usage before resuming.")
        return 1
    finally:
        sessions.unregister_wait(entry)


def wait_command(session_id: str | None) -> str:
    """The command line that starts --wait, with forward slashes, which every shell on Windows takes."""
    exe = Path(sys.executable).as_posix()
    words = [exe] if getattr(sys, "frozen", False) else [exe, (Path(__file__).resolve().parents[2] / "main.py").as_posix()]
    words += ["--wait"] + (["--session", session_id] if session_id else [])
    return " ".join(f'"{word}"' if " " in word else word for word in words)


def run_gate(*, session_cap: float, weekly_cap: float, folder: Path, clock: Callable[[], datetime],
             app_version: str, stdin: BinaryIO | None, out: BinaryIO) -> int:
    """--gate: a PreToolUse hook (matcher "Agent|Workflow") that refuses new agents and workflows
    while the session or weekly limit is at or past its cap.

    Only a limit whose window has not reset counts. Anything that goes wrong lets the tool run:
    the gate must never stop work for want of a reading.
    """
    _hook_input(stdin)  # read it all, so the caller never meets a closed pipe
    try:
        now = clock()
        doc, _running = status.current(folder, now=now, fetch=True, wait_s=GATE_WAIT_S, app_version=app_version)
        over = _over_caps(doc, now, session_cap, weekly_cap) if doc else []
    except Exception:  # fail open
        log.exception("the usage gate failed; the tool runs")
        return 0
    if not over:
        return 0
    reason = ("Claude Usage Monitor gate: " + "; ".join(over) + ". New agents and workflows are refused "
              "until the limit resets or the cap in the hook is raised.")
    _write(out, json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                   "permissionDecision": "deny",
                                                   "permissionDecisionReason": reason}}))
    return 0


def _over_caps(doc: Mapping[str, Any], now: datetime, session_cap: float, weekly_cap: float) -> list[str]:
    over = []
    for name, cap in (("session", session_cap), ("weekly", weekly_cap)):
        entry = doc.get(name)
        percent = entry.get("percent") if isinstance(entry, Mapping) else None
        resets = status.parse_time(entry.get("resets_at")) if isinstance(entry, Mapping) else None
        if isinstance(percent, (int, float)) and percent >= cap and resets is not None and resets > now:
            over.append(f"{name} usage is {percent:.0f}%, at or past the {cap:.0f}% cap; "
                        f"it resets {status.when(resets, now)}")
    return over


def run_mcp(*, folder: Path, clock: Callable[[], datetime], app_version: str,
            stdin: BinaryIO, out: BinaryIO) -> int:
    return mcp.serve(stdin, out, folder=folder, clock=clock, app_version=app_version)


def _hook_input(stdin: BinaryIO | None) -> dict:
    if stdin is None:
        return {}
    try:
        event = json.loads(stdin.read() or b"{}")
    except (OSError, ValueError):
        return {}
    return event if isinstance(event, dict) else {}


def _should_speak(memory: dict, session_id: str, doc: Mapping[str, Any], threshold: float,
                  now: datetime) -> bool:
    """Whether a limit has reached ``threshold``, or climbed a step, since this session last heard.
    When it has, ``memory`` records it."""
    steps = _steps(doc, threshold, now)
    if not steps:
        return False
    entry = memory.setdefault(session_id, {})
    heard = entry.get("steps") if isinstance(entry.get("steps"), dict) else {}
    if all(heard.get(name, -1) >= step for name, step in steps.items()):
        return False
    entry.update(at=now.isoformat(), steps={**heard, **steps})
    return True


def _guard_notice(memory: dict, folder: Path, session_id: str, agent_id: str, doc: Mapping[str, Any] | None,
                  now: datetime, *, every_time: bool) -> str | None:
    """The usage guard's notice for this agent of the session, when the guard holds and the agent has
    not heard of this episode (or ``every_time``). ``memory`` records who was told."""
    verdict = guard.for_reader(doc, now, folder)
    if verdict["state"] != guard.HOLDING:
        return None
    episode = guard.episode(verdict)
    entry = memory.setdefault(session_id, {})
    told = entry.get("guard") if isinstance(entry.get("guard"), dict) else {}
    same = told.get("episode") == episode
    agents = [str(item) for item in told.get("agents") or []] if same else []
    if agent_id in agents and not every_time:
        return None
    entry.update(at=now.isoformat(), guard={
        "episode": episode, "told_at": told.get("told_at") if same and told.get("told_at") else now.isoformat(),
        "agents": agents if agent_id in agents else [*agents, agent_id]})
    return guard_text(verdict, now, wait=None if agent_id else wait_command(session_id or None))


def guard_text(verdict: Mapping[str, Any], now: datetime, *, wait: str | None) -> str:
    """What a session is told when the guard holds: the facts, then how to be woken. It only informs."""
    over = ", ".join(f"{item['label']} {item['percent']:.0f}% (cap {item['cap']}%)" for item in verdict["over"])
    resume = status.parse_time(verdict.get("resume_at"))
    when = f" {status.when(resume, now)}, when" if resume is not None else " when"
    text = (f"Claude Usage Monitor: plan usage has reached the wind-down point set on this machine: {over}. "
            f"Nothing is blocked. Wrap up at a safe point. Work can resume{when} every watched limit is under "
            f"its cap again.")
    if wait:
        text += (f" To be woken then, run this as a background command: `{wait}` (in PowerShell, put & in "
                 f"front). It exits when work can resume, or when the user presses Resume now.")
    return text


def _slot(folder: Path) -> int:
    """This wait's place among the running waits, oldest first: 0 is released first."""
    pids = [entry["pid"] for entry in sessions.waiting(folder)]
    return pids.index(os.getpid()) if os.getpid() in pids else 0


def _stagger(verdict: Mapping[str, Any]) -> float:
    value = (verdict.get("policy") or {}).get("stagger_s")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        value = guard.DEFAULTS["guard_resume_stagger_s"]
    return float(min(max(value, 0), 600))


def _released(out: BinaryIO | None, reason: str, doc: Mapping[str, Any] | None, now: datetime,
              running: bool) -> int:
    _say(out, f"Claude Usage Monitor: the wait is over: {reason}. Work can resume. "
              + status.summary(doc, now=now, running=running))
    return 0


def _say(out: BinaryIO | None, text: str) -> None:
    if out is not None:
        _write(out, text)


def _steps(doc: Mapping[str, Any], threshold: float, now: datetime) -> dict[str, int]:
    """{limit and its window: steps above the threshold} for each limit at or past it.

    The window's reset time is part of the name, so a new window starts from nothing, and a window
    that has reset since the reading no longer counts.
    """
    entries = [("session", doc.get("session")), ("weekly", doc.get("weekly"))]
    entries += [(str(item.get("label")), item) for item in doc.get("scoped") or [] if isinstance(item, Mapping)]
    steps: dict[str, int] = {}
    for name, entry in entries:
        percent = entry.get("percent") if isinstance(entry, Mapping) else None
        resets = status.parse_time(entry.get("resets_at")) if isinstance(entry, Mapping) else None
        if isinstance(percent, (int, float)) and percent >= threshold and (resets is None or resets > now):
            steps[f"{name}@{entry.get('resets_at')}"] = math.floor((percent - threshold) / HOOK_STEP)
    return steps


def _load_memory(folder: Path, now: datetime) -> dict:
    try:
        memory = json.loads((folder / HOOK_MEMORY).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(memory, dict):
        return {}
    cutoff = now - timedelta(days=HOOK_MEMORY_DAYS)
    return {key: value for key, value in memory.items()
            if isinstance(value, dict) and _after(value.get("at"), cutoff)}


def _save_memory(folder: Path, memory: dict) -> None:
    try:
        status.write(memory, folder / HOOK_MEMORY)
    except OSError as exc:  # the hook then speaks again next time, which is harmless
        log.warning("could not save what the usage hook said (%s)", type(exc).__name__)


def _after(stamp: Any, cutoff: datetime) -> bool:
    try:
        return datetime.fromisoformat(stamp) >= cutoff
    except (TypeError, ValueError):
        return False


def _write(out: BinaryIO, text: str) -> None:
    out.write(text.encode("utf-8") + b"\n")
    out.flush()
