"""The command-line modes for Claude sessions (#24): --status, --hook, --gate and --mcp.

They start without the UI and without the single-instance mutex, so they run beside the app.
A windowed build has a stdout only when the caller hands it a pipe, as Claude Code always does.
"""
from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO

from usage_monitor.agent import mcp, status
from usage_monitor.log import get_logger

log = get_logger(__name__)

HOOK_THRESHOLD = 80.0      # --hook speaks once a limit reaches this percent...
HOOK_STEP = 5.0            # ...and again each time one climbs another step
HOOK_MEMORY = "hook-seen.json"
HOOK_MEMORY_DAYS = 7
SESSION_START = "SessionStart"
GATE_SESSION_CAP = 95.0    # --gate refuses new agents and workflows from these percents
GATE_WEEKLY_CAP = 90.0
GATE_WAIT_S = 5.0          # the gate waits less for a paused monitor: it runs before every agent launch


def run_status(*, as_json: bool, folder: Path, clock: Callable[[], datetime], app_version: str,
               out: BinaryIO) -> int:
    """--status [--json]: the summary line, or the whole reading. Exit code 1 when there is none."""
    doc, running = status.current(folder, now=clock(), fetch=True, wait_s=status.WAIT_S, app_version=app_version)
    report = status.report(doc, now=clock(), running=running)
    _write(out, json.dumps(report, indent=1) if as_json else report["summary"])
    return 0 if doc and doc.get("usage_fetched_at") else 1  # the same test as the summary's "no reading"


def run_hook(*, threshold: float, always: bool, folder: Path, clock: Callable[[], datetime],
             app_version: str, stdin: BinaryIO | None, out: BinaryIO) -> int:
    """--hook: a Claude Code hook (SessionStart, UserPromptSubmit) that adds the summary line to the context.

    It speaks when a limit first reaches ``threshold`` in a session and again each time one climbs
    another HOOK_STEP, or every time with ``always``. It never blocks the prompt: it always exits 0
    and stays silent when there is nothing to say. Only SessionStart may fetch a reading of its own.
    """
    event = _hook_input(stdin)
    name = event.get("hook_event_name") if isinstance(event.get("hook_event_name"), str) else "UserPromptSubmit"
    try:
        doc, running = status.current(folder, now=clock(), fetch=name == SESSION_START, app_version=app_version)
        if doc is None or not (always or _should_speak(folder, event.get("session_id"), doc, threshold, clock())):
            return 0
        text = status.summary(doc, now=clock(), running=running)
    except Exception:  # a broken hook must never get in the way of the prompt
        log.exception("the usage hook failed")
        return 0
    _write(out, json.dumps({"hookSpecificOutput": {"hookEventName": name, "additionalContext": text}}))
    return 0


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


def _should_speak(folder: Path, session_id: Any, doc: Mapping[str, Any], threshold: float,
                  now: datetime) -> bool:
    """Whether a limit has reached ``threshold``, or climbed a step, since this session last heard."""
    steps = _steps(doc, threshold, now)
    if not steps:
        return False
    key = str(session_id or "")
    memory = _load_memory(folder, now)
    heard = memory.get(key, {}).get("steps", {})
    if all(heard.get(name, -1) >= step for name, step in steps.items()):
        return False
    memory[key] = {"at": now.isoformat(), "steps": {**heard, **steps}}
    _save_memory(folder, memory)
    return True


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
