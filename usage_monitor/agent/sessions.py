"""Which Claude sessions the usage guard has told, and which are waiting (#27).

Three small files in the app data folder, shared by the hook and --wait processes, which write
them, and the dashboard, which reads them:

    hook-seen.json        {session_id: {"at": ISO, "steps": {...}, "guard": {"episode": key, "told_at": ISO}}}
                          (--hook's memory; "guard" is the holding episode that session was told of)
    waiting/<pid>.json    {"pid": int, "session_id": str | None, "since": ISO, "cwd": str | None}
                          one per running --wait; removed when it ends. A file whose process is
                          gone is left over from a crash and counts for nothing.
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
from datetime import datetime, timezone
from pathlib import Path

SEEN_NAME = "hook-seen.json"
WAITING_DIR = "waiting"
RESUME_NAME = "resume-now"
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259


def told(folder: Path, episode: str) -> list[dict]:
    """[{"session_id", "told_at"}] for the sessions told of ``episode``, oldest first."""
    if not episode:
        return []
    seen = _load(folder / SEEN_NAME)
    entries = [{"session_id": key, "told_at": value["guard"].get("told_at")}
               for key, value in seen.items()
               if isinstance(value, Mapping) and isinstance(value.get("guard"), Mapping)
               and value["guard"].get("episode") == episode]
    return sorted(entries, key=lambda entry: entry["told_at"] or "")


def waiting(folder: Path) -> list[dict]:
    """The running --wait processes, oldest first: [{"pid", "session_id", "since", "cwd"}]."""
    found = []
    for path in (folder / WAITING_DIR).glob("*.json"):
        entry = _load(path)
        pid = entry.get("pid")
        if isinstance(pid, int) and _alive(pid):
            found.append({"pid": pid, "session_id": entry.get("session_id"), "since": entry.get("since"),
                          "cwd": entry.get("cwd")})
    return sorted(found, key=lambda entry: (entry["since"] or "", entry["pid"]))


def register_wait(folder: Path, session_id: str | None, now: datetime) -> Path:
    """Record this process as waiting. Returns the file to remove when the wait ends."""
    path = folder / WAITING_DIR / f"{os.getpid()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": os.getpid(), "session_id": session_id, "since": _iso(now),
                                "cwd": os.getcwd()}), encoding="utf-8")
    return path


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


def _alive(pid: int) -> bool:
    if pid == os.getpid():
        return True
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))) and code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

