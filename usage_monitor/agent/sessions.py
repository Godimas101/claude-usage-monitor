"""Which Claude sessions the usage guard has told, and which are waiting (#27).

Three small files in the app data folder, shared by the hook and --wait processes, which write
them, and the dashboard, which reads them:

    hook-seen.json        {session_id: {"at": ISO, "steps": {...}, "guard": {"episode": key, "told_at": ISO}}}
                          (--hook's memory; "guard" is the holding episode that session was told of)
    waiting/<pid>.json    {"pid": int, "started": int | None, "session_id": str | None, "since": ISO,
                          "cwd": str | None} one per running --wait; removed when it ends. "started" is
                          the process's creation time, since Windows hands a dead process's id to the
                          next one. A file whose process is gone is left over from a wait that was
                          killed: it counts for nothing and the next reader removes it.
    wait-released         {"at": ISO}: when the last --wait was let go, so the next one goes a stagger later
    resume-now            an empty marker: every --wait that started before its time ends at once
                          ("Resume now" in the tray or the dashboard)
"""
from __future__ import annotations

import contextlib
import ctypes
import json
import os
import sys
from collections.abc import Mapping
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from usage_monitor.agent import status

SEEN_NAME = "hook-seen.json"
WAITING_DIR = "waiting"
RELEASED_NAME = "wait-released"
RESUME_NAME = "resume-now"
ALIVE, GONE, UNKNOWN = "alive", "gone", "unknown"
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_ERROR_INVALID_PARAMETER = 87  # OpenProcess on an id that no process has


def told(folder: Path, episode: str) -> list[dict]:
    """[{"session_id", "told_at"}] for the sessions told of ``episode``, oldest first."""
    if not episode:
        return []
    seen = _load(folder / SEEN_NAME)
    entries = [{"session_id": key, "told_at": value["guard"].get("told_at")}
               for key, value in seen.items()
               if isinstance(value, Mapping) and isinstance(value.get("guard"), Mapping)
               and same_episode(value["guard"].get("episode"), episode)]
    return sorted(entries, key=lambda entry: entry["told_at"] or "")


def same_episode(told_key: Any, key: str) -> bool:
    """Whether a session told of ``told_key`` has heard of ``key`` (guard.episode keys). It has when
    every limit over its cap now was over it then: a window that resets while another limit stays
    over its cap ends no episode, and only a limit that newly goes over one starts a new one."""
    if not isinstance(told_key, str) or not told_key or not key:
        return False
    return set(key.split("|")) <= set(told_key.split("|"))


def waiting(folder: Path) -> list[dict]:
    """The running --wait processes, oldest first: [{"pid", "session_id", "since", "cwd"}].

    The file of a wait whose process is gone, or whose id now belongs to another process, is removed.
    A file that cannot be read is skipped and left alone: its wait may be writing it."""
    found = []
    for path in (folder / WAITING_DIR).glob("*.json"):
        entry = _load(path)
        pid = entry.get("pid")
        if not isinstance(pid, int):
            continue
        state = _state(pid, entry.get("started"))
        if state == GONE:
            with contextlib.suppress(OSError):
                path.unlink()
        if state != ALIVE:
            continue
        found.append({"pid": pid, "session_id": entry.get("session_id"), "since": entry.get("since"),
                      "cwd": entry.get("cwd")})
    return sorted(found, key=lambda entry: (entry["since"] or "", entry["pid"]))


def register_wait(folder: Path, session_id: str | None, now: datetime) -> Path:
    """Record this process as waiting. Returns the file to remove when the wait ends."""
    path = folder / WAITING_DIR / f"{os.getpid()}.json"
    status.write({"pid": os.getpid(), "started": _started(os.getpid()), "session_id": session_id,
                  "since": _iso(now), "cwd": os.getcwd()}, path)
    return path


def mark_released(folder: Path, now: datetime) -> None:
    """Record that a --wait is being let go now. A wait that cannot record it still goes: the next
    one then goes without the stagger, which is how waits went before the stagger existed."""
    try:
        status.write({"at": _iso(now)}, folder / RELEASED_NAME)
    except OSError:
        pass


def released_at(folder: Path) -> datetime | None:
    """When the last --wait was let go, or None."""
    return status.parse_time(_load(folder / RELEASED_NAME).get("at"))


def unregister_wait(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink()


def resume_now(folder: Path) -> None:
    """Release every --wait that is running now, whatever the limits say."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / RESUME_NAME).touch()


def resumed_since(folder: Path, started_epoch: float) -> bool:
    """Whether "Resume now" was pressed after ``started_epoch`` (time.time())."""
    try:
        return (folder / RESUME_NAME).stat().st_mtime >= started_epoch
    except OSError:
        return False


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _state(pid: int, started: Any = None) -> str:
    """ALIVE, GONE (it exited, or ``started``, its creation time as recorded, shows that its id now
    belongs to another process) or UNKNOWN (it cannot be looked at)."""
    if sys.platform != "win32":
        if pid == os.getpid():
            return ALIVE
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return GONE
        except OSError:
            return UNKNOWN
        return ALIVE
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return GONE if ctypes.get_last_error() == _ERROR_INVALID_PARAMETER else UNKNOWN
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code)):
            return UNKNOWN
        if code.value != _STILL_ACTIVE:
            return GONE
        created = _creation(kernel32, handle)
        return GONE if isinstance(started, int) and created is not None and created != started else ALIVE
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def _started(pid: int) -> int | None:
    """The creation time of process ``pid`` (a FILETIME as one number), or None off Windows or when
    it cannot be read."""
    if sys.platform != "win32":
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        return _creation(kernel32, handle)
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def _creation(kernel32: Any, handle: int) -> int | None:
    created, ended, kernel, user = (wintypes.FILETIME() for _ in range(4))
    if not kernel32.GetProcessTimes(ctypes.c_void_p(handle), ctypes.byref(created), ctypes.byref(ended),
                                    ctypes.byref(kernel), ctypes.byref(user)):
        return None
    return (created.dwHighDateTime << 32) | created.dwLowDateTime


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

