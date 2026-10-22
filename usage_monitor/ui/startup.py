"""Start at sign-in: the HKCU Run entry named ClaudeUsageMonitor.

The entry runs the frozen executable alone when the app is frozen. From source it runs pythonw.exe
on main.py in the repository. Both paths are quoted. set_enabled(True) overwrites an entry that
points somewhere else on request. repair_if_enabled() does the same by itself at launch after an
update or a move, but only in the installed (frozen) build: a source checkout must never repoint
the entry of an installed copy. Registry access is Windows-only; anywhere else the entry simply
reads as absent.
"""
from __future__ import annotations

import sys
from pathlib import Path

from usage_monitor import paths
from usage_monitor.log import get_logger

log = get_logger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "ClaudeUsageMonitor"


def command_line(*, frozen: bool | None = None, executable: str | None = None,
                 main_script: Path | None = None) -> str:
    """The command the Run entry should hold. Arguments exist so tests can pin them."""
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    exe = executable or sys.executable
    if is_frozen:
        return _quoted(exe)
    script = main_script if main_script is not None else paths.resource_dir() / "main.py"
    return f"{_quoted(pythonw_for(exe))} {_quoted(str(script))}"


def is_enabled() -> bool:
    """True when the Run entry exists, whatever it points at. Never raises."""
    try:
        return _read_value() is not None
    except OSError as exc:
        log.warning("could not read the startup entry: %s", exc)
        return False


def set_enabled(on: bool) -> None:
    """Add or remove the entry. Raises OSError when the registry refuses the change."""
    if on:
        _write_value(command_line())
    else:
        _delete_value()


def repair_if_enabled(*, frozen: bool | None = None) -> bool:
    """Rewrite an existing entry that points somewhere else. Returns True when it changed.

    Only the installed (frozen) build repairs. From source it does nothing, not even a read: the
    command would be this checkout's, and the entry may belong to an installed copy.
    """
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if not is_frozen:
        return False
    current = _read_value()
    if current is None:
        return False
    expected = command_line(frozen=True)
    if current == expected:
        return False
    log.info("repairing the startup entry (it pointed at another location)")
    _write_value(expected)
    return True


def _quoted(path: str) -> str:
    return f'"{path}"'  # a Windows path cannot contain a double quote, so no escaping is needed


def pythonw_for(python: str) -> str:
    """pythonw.exe beside python.exe, so no console window opens at sign-in. Falls back to python."""
    candidate = Path(python).with_name("pythonw.exe")
    if Path(python).name.lower() == "python.exe" and candidate.is_file():
        return str(candidate)
    return str(python)


# ── Registry (Windows only; imported lazily so the module loads anywhere) ──

def _winreg():  # type: ignore[no-untyped-def]
    if sys.platform != "win32":
        raise OSError("the startup entry is Windows-only")
    import winreg  # noqa: PLC0415
    return winreg


def _read_value() -> str | None:
    winreg = _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, VALUE_NAME)
    except FileNotFoundError:
        return None
    return str(value)


def _write_value(command: str) -> None:
    winreg = _winreg()
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)


def _delete_value() -> None:
    winreg = _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        pass  # already off
