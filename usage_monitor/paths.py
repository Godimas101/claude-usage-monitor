"""Every filesystem location the monitor touches, computed in one place.

Only app_data_dir() creates a folder, and only when it is called. Two environment variables
redirect locations: CUM_APPDATA_DIR replaces the app data folder (tests and validation runs),
and CLAUDE_CONFIG_DIR is Claude Code's own config-folder override, honoured the same way.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from usage_monitor.log import get_logger

log = get_logger(__name__)

APP_FOLDER = "ClaudeUsageMonitor"
APP_DATA_OVERRIDE_ENV = "CUM_APPDATA_DIR"
CLAUDE_CONFIG_ENV = "CLAUDE_CONFIG_DIR"


def app_data_dir() -> Path:
    """The app data folder: $CUM_APPDATA_DIR when set, else %APPDATA%\\ClaudeUsageMonitor.

    Created on demand. A folder that cannot be created is logged and its path is still
    returned, so the failure surfaces at the caller's own file I/O.
    """
    override = os.environ.get(APP_DATA_OVERRIDE_ENV)
    folder = Path(override).expanduser() if override else _roaming_root() / APP_FOLDER
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning("cannot create app data folder %s: %s", folder, exc)
    return folder


def claude_dir() -> Path:
    """Claude Code's config folder: $CLAUDE_CONFIG_DIR, else ~/.claude. Never created."""
    override = os.environ.get(CLAUDE_CONFIG_ENV)
    return Path(override).expanduser() if override else Path.home() / ".claude"


def projects_dirs() -> list[Path]:
    """Transcript roots that exist: claude_dir()/projects, then ~/.config/claude/projects."""
    candidates = [claude_dir() / "projects", Path.home() / ".config" / "claude" / "projects"]
    found: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(os.path.abspath(candidate))
        if key not in seen and _is_dir(candidate):
            seen.add(key)
            found.append(candidate)
    return found


def fs_path(path: str | os.PathLike[str]) -> str:
    """``path`` in the form that opens past Windows' 260-character limit.

    Python's open() and os.stat() raise FileNotFoundError for a path of 260 or more characters unless the machine has
    LongPathsEnabled, which Windows leaves off. Claude Code (Node) writes such files without trouble (a subagent
    transcript sits four folders below the project folder) and os.scandir lists them, so reading them needs the Win32
    extended-length form: ``\\\\?\\C:\\folder\\file``, or ``\\\\?\\UNC\\server\\share\\file`` for a network path. Use the
    result for the I/O call only; keep the plain path as a key, in a log line and in a message. Off Windows, and for a
    path that is already in the extended form, the path comes back unchanged.
    """
    text = os.fspath(path)
    if sys.platform != "win32" or text.startswith(("\\\\?\\", "\\\\.\\")):
        return text
    full = os.path.abspath(text)    # the extended form is passed on as it is: it must be absolute, with plain backslashes
    if full.startswith("\\\\"):
        return "\\\\?\\UNC\\" + full[2:]
    return "\\\\?\\" + full


def credentials_path() -> Path:
    return claude_dir() / ".credentials.json"


def settings_path() -> Path:
    return app_data_dir() / "settings.json"


def db_path() -> Path:
    return app_data_dir() / "usage.db"


def log_path() -> Path:
    return app_data_dir() / "monitor.log"


def resource_dir() -> Path:
    """Read-only bundled resources: the PyInstaller unpack folder when frozen, else the repo root."""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        return Path(meipass) if meipass else Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _roaming_root() -> Path:
    appdata = os.environ.get("APPDATA")
    return Path(appdata) if appdata else Path.home()


def _is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:  # e.g. permission denied on a parent folder
        return False
