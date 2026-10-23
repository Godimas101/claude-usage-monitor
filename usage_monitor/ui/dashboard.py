"""The dashboard window (docs/UI_ARCHITECTURE.md, "Dashboard process").

DashboardLauncher runs in the main process. It starts one child, or focuses the child's window when
one is already open. The child belongs to a kill-on-close job object that the monitor owns, so it
ends with the monitor however the monitor ends (End task, taskkill /F, a crash). dashboard_main runs
in that child: a pywebview window on the local API URL, with the window caption coloured from the
theme and a saved position that is off every screen pulled back onto one. When pywebview or WebView2
is missing, the same URL opens in the default browser instead and the exit code says so. The child
talks to the API over HTTP with the per-run token it receives in the CUM_DASHBOARD_TOKEN
environment variable, to 127.0.0.1 only and without following a redirect. Its js_api object
(DashboardApi) is what the page can call: the token, the window chrome, a link opener and
save_export, which writes a daily-rollup download through the native save dialog.
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Callable, Sequence
from ctypes import wintypes
from datetime import date
from pathlib import Path
from typing import Any

from usage_monitor import paths
from usage_monitor.log import get_logger
from usage_monitor.settings import Settings
from usage_monitor.ui.startup import pythonw_for

log = get_logger(__name__)

WINDOW_TITLE = "Claude Usage Monitor"
TOKEN_ENV = "CUM_DASHBOARD_TOKEN"
DEFAULT_SIZE = (1200, 780)
MIN_SIZE = (720, 480)
# What the window shows until the page has painted, when the active theme's own page colour is not known (Nostromo's black).
DEFAULT_BACKGROUND = "#090909"

# Exit codes of the child. 0 is a window that was shown and closed. The two fallbacks differ on
# purpose: a missing web view is the machine's doing, a failing window call is ours (a test must
# be able to tell them apart). In both the page is opened in the default browser.
EXIT_NO_ARGS = 2
EXIT_WINDOW_ERROR = 1
EXIT_BROWSER_FALLBACK = 3

# A saved window counts as reachable while this much of its title bar lies on a screen (work area).
CAPTION_HEIGHT = 32
GRAB_WIDTH = 120
GRAB_HEIGHT = 28

EXTERNAL_HOSTS = frozenset({"github.com", "patreon.com", "www.patreon.com"})
_HEX_COLOUR = re.compile(r"#[0-9a-fA-F]{6}")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# save_export: the formats /api/export offers, the largest download taken, and what a safe name looks like.
EXPORT_FORMATS = ("csv", "json")
MAX_EXPORT_BYTES = 16 * 1024 * 1024
EXPORT_TIMEOUT_S = 20.0
_EXPORT_NAME = re.compile(r"claude-usage-daily-\d{4}-\d{2}-\d{2}\.(?:csv|json)")
_LOOPBACK_BASE = re.compile(r"http://127\.0\.0\.1:[0-9]{1,5}")
_EXPORT_FILTERS = {"csv": "CSV file (*.csv)", "json": "JSON file (*.json)"}

SW_RESTORE = 9
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36

# ── Win32 bindings: every function gets explicit argtypes and restype ──────────

_WNDENUMPROC = (ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
                if sys.platform == "win32" else None)
_user32_cache: Any = None
_dwm_cache: Any = None


def _user32() -> Any:
    global _user32_cache
    if _user32_cache is None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.FindWindowW.restype = wintypes.HWND
        user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.IsIconic.restype = wintypes.BOOL
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        _user32_cache = user32
    return _user32_cache


def _dwmapi() -> Any:
    global _dwm_cache
    if _dwm_cache is None:
        dwm = ctypes.WinDLL("dwmapi")
        dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        dwm.DwmSetWindowAttribute.restype = ctypes.c_long  # HRESULT
        _dwm_cache = dwm
    return _dwm_cache


def _window_pid(user32: Any, hwnd: int) -> int:
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.pointer(pid))
    return int(pid.value)


def _window_title(user32: Any, hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def find_window_of(pid: int) -> int | None:
    """The visible dashboard window owned by process ``pid``, or None. FindWindowW first, then a scan.

    Looked up by owner, not by title alone: a second monitor (an installed copy beside a dev build)
    can have a window with the same title, and FindWindowW names only one of them.
    """
    user32 = _user32()
    hwnd = user32.FindWindowW(None, WINDOW_TITLE)
    if hwnd and _window_pid(user32, hwnd) == pid:
        return int(hwnd)
    found: list[int] = []

    def visit(handle: int, _lparam: int) -> bool:
        try:
            if (_window_pid(user32, handle) == pid and user32.IsWindowVisible(handle)
                    and _window_title(user32, handle) == WINDOW_TITLE):
                found.append(int(handle))
                return False
        except Exception:  # a callback must never raise into Win32
            log.exception("window scan failed")
            return False
        return True

    callback = _WNDENUMPROC(visit)  # referenced until EnumWindows returns
    user32.EnumWindows(callback, 0)
    return found[0] if found else None


_find_window_of = find_window_of     # the name this function had while it was private


def focus_window(pid: int) -> bool:
    """Bring the dashboard window of process ``pid`` to the front. False when no such window is found."""
    if sys.platform != "win32":
        return False
    hwnd = find_window_of(pid)
    if hwnd is None:
        return False
    user32 = _user32()
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)
    return True


def colorref(hex_colour: str) -> int:
    """'#rrggbb' as a COLORREF (0x00BBGGRR). Raises ValueError for anything else."""
    if not isinstance(hex_colour, str) or not _HEX_COLOUR.fullmatch(hex_colour):
        raise ValueError("colour must be #rrggbb")
    red, green, blue = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return red | (green << 8) | (blue << 16)


def _dwm_set(hwnd: int, attribute: int, value: int) -> bool:
    cell = ctypes.c_int(value)
    result = _dwmapi().DwmSetWindowAttribute(hwnd, attribute, ctypes.addressof(cell), ctypes.sizeof(cell))
    return result == 0  # Windows 10 has no caption colours and returns an error; that is fine


# ── Parent side ──────────────────────────────────────────────────────────────────

def build_command(port: int, *, frozen: bool | None = None, executable: str | None = None,
                  main_script: Path | None = None) -> list[str]:
    """The child's command line: the frozen exe, or pythonw.exe on main.py from the repository."""
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    exe = executable or sys.executable
    if is_frozen:
        return [exe, "--dashboard", "--port", str(port)]
    script = main_script if main_script is not None else paths.resource_dir() / "main.py"
    return [pythonw_for(exe), str(script), "--dashboard", "--port", str(port)]


class DashboardLauncher:
    """Opens the dashboard in its own process, or brings the open one forward. Thread-safe."""

    def __init__(self, server: Any, settings: Settings) -> None:
        self._server = server
        self._settings = settings
        self._lock = threading.Lock()
        self._child: subprocess.Popen[bytes] | None = None
        self._job: int | None = None    # kill-on-close job handle: closing it (or dying) ends the child

    @property
    def running(self) -> bool:
        with self._lock:
            return self._alive()

    @property
    def pid(self) -> int | None:
        with self._lock:
            return self._child.pid if self._child is not None and self._alive() else None

    @property
    def exit_code(self) -> int | None:
        """The last child's exit code, or None while it runs or when none was ever started."""
        with self._lock:
            return self._child.poll() if self._child is not None else None

    def close(self, timeout: float = 5.0) -> None:
        """Terminate the child if it is running. The dashboard saves its own window size when it closes."""
        with self._lock:
            child = self._child if self._alive() else None
            job, self._job = self._job, None
        try:
            if child is not None:
                child.terminate()
                try:
                    child.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    child.kill()
        finally:
            _close_job(job)     # also ends anything the child left behind (WebView2 helpers)

    def open(self) -> bool:
        """Show the dashboard. Returns True when a window was focused or a child was started."""
        with self._lock:
            if self._child is not None and self._alive():
                if not focus_window(self._child.pid):
                    log.info("dashboard process is running but its window is not visible yet")
                return True
            env = dict(os.environ)
            env[TOKEN_ENV] = self._server.token
            command = build_command(self._server.port)
            try:
                self._child = subprocess.Popen(command, env=env, close_fds=True, creationflags=_NO_WINDOW)
            except OSError:
                log.exception("could not start the dashboard process")
                self._child = None
                return False
            log.info("dashboard process started (pid %d)", self._child.pid)
            self._tie_to_monitor(self._child)
            return True

    def _tie_to_monitor(self, child: Any) -> None:
        """Put the child in a kill-on-close job that only this process holds. When the monitor dies
        without tearing down (End task, taskkill /F, a native crash) Windows closes the job and ends
        the dashboard, instead of leaving a window that polls a dead API. Best effort: without the
        job the dashboard works the same."""
        handle = getattr(child, "_handle", None)   # Popen's own process handle; test doubles have none
        if handle is None or sys.platform != "win32":
            return
        try:
            from usage_monitor.ui import win32  # noqa: PLC0415  (Windows only, loaded when needed)
            if self._job is None:
                self._job = win32.create_kill_on_close_job()
            win32.assign_to_job(self._job, int(handle))
        except (OSError, ValueError, TypeError) as exc:
            log.warning("could not tie the dashboard to the monitor; it may outlive a crash (%s)", exc)

    def _alive(self) -> bool:
        return self._child is not None and self._child.poll() is None


def _close_job(job: int | None) -> None:
    """Close a job handle from DashboardLauncher; every process still in the job is terminated."""
    if not job or sys.platform != "win32":
        return
    try:
        from usage_monitor.ui import win32  # noqa: PLC0415
        win32.CloseHandle(job)
    except (OSError, ValueError, TypeError):
        log.debug("could not close the dashboard job handle", exc_info=True)


# ── Child side ───────────────────────────────────────────────────────────────────

def _external_allowed(url: Any) -> bool:
    if not isinstance(url, str) or len(url) > 2048:
        return False
    parts = urllib.parse.urlsplit(url)
    return parts.scheme == "https" and (parts.hostname or "") in EXTERNAL_HOSTS


def _export_result(*, ok: bool = False, path: str | None = None, error: str | None = None) -> dict[str, Any]:
    return {"ok": ok, "path": path, "error": error}


class DashboardApi:
    """The methods the page can call as pywebview.api.<name>. Names starting with _ are not exposed.

    ``base`` is the monitor's address, http://127.0.0.1:<port>, which save_export reads the export from.
    """

    def __init__(self, token: str, base: str = "") -> None:
        self._token = token
        self._base = base
        self._window: Any = None
        self._saving = threading.Lock()

    def _bind(self, window: Any) -> None:
        self._window = window

    def get_token(self) -> str:
        return self._token

    def save_export(self, fmt: str) -> dict[str, Any]:
        """Save the daily rollups ("csv" or "json") where the user says, through the native save dialog.

        Returns {"ok", "path", "error"}. A cancelled dialog is ok False with no error. The page
        names only the format: the monitor produces the data, and the user's dialog choice is the
        only path written to.
        """
        if not self._saving.acquire(blocking=False):
            return _export_result(error="a save dialog is already open")
        try:
            return self._save_export(fmt)
        except Exception:  # the page gets an answer, never a stack trace
            log.exception("save_export failed")
            return _export_result(error="could not save the export")
        finally:
            self._saving.release()

    def _save_export(self, fmt: str) -> dict[str, Any]:
        if fmt not in EXPORT_FORMATS:
            return _export_result(error="the format must be csv or json")
        if self._window is None:
            return _export_result(error="the dashboard window is not ready")
        try:
            body, name = fetch_export(self._base, self._token, fmt)
        except urllib.error.HTTPError as exc:
            log.warning("the monitor refused the export (HTTP %d)", exc.code)
            return _export_result(error=f"the monitor answered {exc.code}")
        except ValueError as exc:       # our own words: a base that is not the monitor's, an export that is too big
            log.warning("the export was refused: %s", exc)
            return _export_result(error=str(exc))
        except OSError as exc:
            log.warning("could not fetch the export: %s", type(exc).__name__)
            return _export_result(error="the monitor could not be reached")
        chosen = self._ask_where(name, fmt)
        if chosen is None:
            return _export_result()
        path = Path(chosen)
        if not path.suffix:
            path = path.with_suffix("." + fmt)
        try:
            _write_atomic(path, body)
        except OSError as exc:
            reason = exc.strerror or type(exc).__name__
            log.warning("could not write the export: %s", reason)
            return _export_result(error=f"could not write {path.name}: {reason}")
        log.info("export saved (%s, %d bytes)", fmt, len(body))
        return _export_result(ok=True, path=str(path))

    def _ask_where(self, name: str, fmt: str) -> str | None:
        """The path chosen in the native save dialog, or None when the user cancelled it."""
        chosen = self._window.create_file_dialog(
            _save_dialog_type(), save_filename=name, file_types=(_EXPORT_FILTERS[fmt], "All files (*.*)"))
        if isinstance(chosen, (list, tuple)):
            chosen = chosen[0] if chosen else None
        return str(chosen) if chosen else None

    def set_chrome(self, caption_hex: str, text_hex: str, border_hex: str, dark: bool) -> bool:
        """Colour the native caption, caption text and border from the theme. False when unsupported."""
        try:
            values = [colorref(caption_hex), colorref(text_hex), colorref(border_hex)]
            hwnd = self._hwnd()
        except (ValueError, OSError, AttributeError, TypeError):
            log.debug("set_chrome skipped", exc_info=True)
            return False
        if not hwnd:
            return False
        results = [
            _dwm_set(hwnd, DWMWA_BORDER_COLOR, values[2]),
            _dwm_set(hwnd, DWMWA_CAPTION_COLOR, values[0]),
            _dwm_set(hwnd, DWMWA_TEXT_COLOR, values[1]),
            _dwm_set(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if dark else 0),
        ]
        return all(results)

    def open_external(self, url: str) -> bool:
        """Open an https link on the allow-list in the default browser."""
        if not _external_allowed(url):
            log.warning("refused to open a link outside the allow-list")
            return False
        return bool(webbrowser.open(url))

    def _hwnd(self) -> int | None:
        return _native_handle(self._window) or find_window_of(os.getpid())


def _native_handle(window: Any) -> int | None:
    """The HWND of pywebview's native WinForms window, or None while it does not exist."""
    handle = getattr(getattr(window, "native", None), "Handle", None)
    if handle is None:
        return None
    for name in ("ToInt64", "ToInt32"):
        convert = getattr(handle, name, None)
        if convert is not None:
            return int(convert())
    return int(handle)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="dashboard", add_help=False)
    parser.add_argument("--dashboard", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    args, _unknown = parser.parse_known_args(argv if argv is not None else sys.argv[1:])
    return args


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuses every redirect (urllib then raises the 3xx as an HTTPError), so the token header
    cannot follow an answer to some other address."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def _local_opener() -> urllib.request.OpenerDirector:
    # ProxyHandler({}) so a system proxy never captures a request to 127.0.0.1.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def _export_name(fmt: str) -> str:
    """The file name the monitor suggests for a download made today (local date)."""
    return f"claude-usage-daily-{date.today().isoformat()}.{fmt}"


def fetch_export(base: str, token: str, fmt: str, *, timeout: float = EXPORT_TIMEOUT_S) -> tuple[bytes, str]:
    """GET /api/export from the monitor on this machine: (body, the file name it suggests).

    Only http://127.0.0.1:<port> is ever called. ValueError for another address, an unknown format
    or an answer over MAX_EXPORT_BYTES; OSError (HTTPError and URLError included) when the monitor
    cannot be reached or says no.
    """
    if _LOOPBACK_BASE.fullmatch(base) is None or fmt not in EXPORT_FORMATS:
        raise ValueError("the export is only read from the monitor on this machine")
    request = urllib.request.Request(f"{base}/api/export?format={fmt}", headers={"X-Monitor-Token": token})
    with _local_opener().open(request, timeout=timeout) as response:
        body = response.read(MAX_EXPORT_BYTES + 1)
        suggested = response.headers.get_filename() or ""
    if len(body) > MAX_EXPORT_BYTES:
        raise ValueError("the export is larger than 16 MB")
    return body, suggested if _EXPORT_NAME.fullmatch(suggested) else _export_name(fmt)


def _save_dialog_type() -> Any:
    """pywebview's "save file" dialog constant: FileDialog.SAVE, or the older SAVE_DIALOG when there is no FileDialog."""
    import webview  # noqa: PLC0415  (pywebview is optional at import time)
    kinds = getattr(webview, "FileDialog", None)
    return kinds.SAVE if kinds is not None else webview.SAVE_DIALOG


def _write_atomic(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` through a temporary file beside it, so a failed write leaves any
    file the user chose to overwrite as it was."""
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _api_call(base: str, token: str, method: str, path: str, body: Any = None, timeout: float = 5.0) -> Any:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"X-Monitor-Token": token}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    with _local_opener().open(request, timeout=timeout) as response:
        return json.loads(response.read(256 * 1024).decode("utf-8"))


def _read_state(base: str, token: str) -> dict[str, Any] | None:
    """GET /api/state from the monitor: what the window needs before it opens (its saved bounds and the theme's page
    colour). None when it cannot be read; the window then opens at the default size on the default background."""
    try:
        state = _api_call(base, token, "GET", "/api/state")
    except (OSError, ValueError):
        log.warning("dashboard could not read the monitor's state; opening at the default size")
        return None
    return state if isinstance(state, dict) else None


def _saved_bounds(state: Any) -> dict[str, int] | None:
    settings = state.get("settings") if isinstance(state, dict) else None
    bounds = settings.get("dashboard_bounds") if isinstance(settings, dict) else None
    return bounds if isinstance(bounds, dict) else None


def _window_background(state: Any) -> str:
    """The colour the window shows until the page has painted (pywebview's ``background_color``): the active theme's page
    background, ``--bg`` of the state's ``theme.css_vars``, which the server also bakes into the first paint. A light Gates
    window then opens light instead of black and jumping to light. DEFAULT_BACKGROUND when the state has no usable one
    (pywebview takes a ``#rrggbb`` colour only)."""
    theme = state.get("theme") if isinstance(state, dict) else None
    css = theme.get("css_vars") if isinstance(theme, dict) else None
    value = css.get("--bg") if isinstance(css, dict) else None
    return value if isinstance(value, str) and _HEX_COLOUR.fullmatch(value) else DEFAULT_BACKGROUND


def _save_bounds(base: str, token: str, bounds: dict[str, int]) -> None:
    try:
        _api_call(base, token, "POST", "/api/settings", {"dashboard_bounds": bounds}, timeout=3.0)
    except (OSError, ValueError):
        log.warning("dashboard could not save its window size")


class _BoundsTracker:
    """Remembers the last window geometry while the window is open; saved once, on close."""

    def __init__(self, window: Any) -> None:
        self._window = window
        self._last: dict[str, int] | None = None

    def note(self, *_args: Any) -> None:
        try:
            self._last = {"x": int(self._window.x), "y": int(self._window.y),
                          "w": int(self._window.width), "h": int(self._window.height)}
        except Exception:  # the window may already be gone during close; keep the last good value
            log.debug("window geometry unavailable", exc_info=True)

    @property
    def last(self) -> dict[str, int] | None:
        return self._last


def _geometry(saved: dict[str, int] | None) -> dict[str, int | None]:
    if saved is None or not {"x", "y", "w", "h"} <= set(saved):
        return {"x": None, "y": None, "width": DEFAULT_SIZE[0], "height": DEFAULT_SIZE[1]}
    return {"x": saved["x"], "y": saved["y"], "width": saved["w"], "height": saved["h"]}


# ── Keeping the window on a screen ───────────────────────────────────────────────

def reachable(left: int, top: int, width: int, height: int, areas: Sequence[Any]) -> bool:
    """Whether enough of the title bar of a window at (left, top) lies inside one of the work
    ``areas`` (anything with left, top, right, bottom) for the user to grab it."""
    right, bottom = left + width, top + min(CAPTION_HEIGHT, height)
    for area in areas:
        across = min(right, area.right) - max(left, area.left)
        down = min(bottom, area.bottom) - max(top, area.top)
        if across >= min(GRAB_WIDTH, width) and down >= min(GRAB_HEIGHT, bottom - top):
            return True
    return False


def on_screen_position(left: int, top: int, width: int, height: int, areas: Sequence[Any]) -> tuple[int, int] | None:
    """Where to move a window whose title bar is off every screen: fully inside the work area it
    overlaps most, or the nearest one. None when it can be grabbed already (or no screen is known)."""
    if not areas or reachable(left, top, width, height, areas):
        return None
    from usage_monitor.ui.platform import clamp_to_rects  # noqa: PLC0415  (Windows only)
    return clamp_to_rects(left, top, width, height, areas)


def _window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    from usage_monitor.ui import win32  # noqa: PLC0415
    rect = wintypes.RECT()
    if not win32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return rect.left, rect.top, rect.right, rect.bottom


def _move_window(hwnd: int, x: int, y: int) -> bool:
    from usage_monitor.ui import win32  # noqa: PLC0415
    flags = win32.SWP_NOSIZE | win32.SWP_NOZORDER | win32.SWP_NOACTIVATE
    return bool(win32.SetWindowPos(hwnd, win32.HWND_TOP, x, y, 0, 0, flags))


def _screen_areas() -> list[Any]:
    """Work areas in this process's own coordinates. pywebview owns the DPI awareness here (it sets
    it when the GUI starts), so the query must not change it."""
    from usage_monitor.ui import platform  # noqa: PLC0415
    return platform.work_areas(set_awareness=False)


def keep_on_screen(window: Any) -> bool:
    """Move a window that would open where nobody can reach it onto a screen. True when it moved.

    A saved position survives a monitor being unplugged or rearranged, and the window would then
    open off every screen again and again. This runs before the window is shown and works in the
    physical pixels of the live window and of the monitors, so it does not depend on the display
    scale (the saved numbers are scaled by pywebview, the monitor numbers are not).
    """
    try:
        hwnd = _native_handle(window)
        rect = _window_rect(hwnd) if hwnd else None
        if rect is None:
            return False
        left, top, right, bottom = rect
        target = on_screen_position(left, top, right - left, bottom - top, _screen_areas())
        if target is None:
            return False
        log.info("the saved dashboard position is off every screen; moving the window onto one")
        return _move_window(hwnd, *target)
    except Exception:  # an event handler must never keep the window from opening
        log.exception("could not check that the dashboard is on a screen")
        return False


def dashboard_main(argv: list[str] | None = None) -> int:
    """Child entry point (``main.py --dashboard --port N``). Returns the process exit code."""
    args = _parse_args(argv)
    token = os.environ.get(TOKEN_ENV, "")
    if not token or not 0 < args.port < 65536:
        log.error("dashboard needs %s and --port", TOKEN_ENV)
        return EXIT_NO_ARGS
    base = f"http://127.0.0.1:{args.port}"
    url = base + "/"
    state = _read_state(base, token)
    saved = _saved_bounds(state)

    try:
        import webview  # noqa: PLC0415  (pywebview is optional at import time)
    except ImportError:
        log.warning("pywebview is not installed; opening the dashboard in the browser")
        webbrowser.open(url)
        return EXIT_BROWSER_FALLBACK

    api = DashboardApi(token, base)
    try:
        window = webview.create_window(
            WINDOW_TITLE, url, js_api=api, min_size=MIN_SIZE, background_color=_window_background(state),
            **_geometry(saved),
        )
    except Exception:  # our own call failed (a bug or a pywebview change): still show the page, but say so
        log.exception("could not create the dashboard window; opening the browser")
        webbrowser.open(url)
        return EXIT_WINDOW_ERROR
    api._bind(window)
    tracker = _BoundsTracker(window)
    window.events.moved += tracker.note
    window.events.resized += tracker.note
    window.events.closing += tracker.note

    def place_on_screen() -> None:
        keep_on_screen(window)

    try:
        window.events.before_show += place_on_screen
    except AttributeError:  # a pywebview without the event: the window opens where it was saved
        log.warning("this pywebview has no before_show event; the saved position is used as it is")

    def on_closed(*_args: Any) -> None:
        if tracker.last is not None:
            _save_bounds(base, token, tracker.last)

    window.events.closed += on_closed
    try:
        webview.start()
    except Exception:
        log.exception("the web view could not start (WebView2 missing?); opening the browser")
        webbrowser.open(url)
        return EXIT_BROWSER_FALLBACK
    return 0


def _cli() -> int:  # pragma: no cover - manual entry: python -m usage_monitor.ui.dashboard --port N
    return dashboard_main(sys.argv[1:])


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
