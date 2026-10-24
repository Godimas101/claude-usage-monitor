"""Windows facts the UI needs: taskbar geometry, monitors, DPI, fullscreen and lock detection,
and the system theme colours.

Every query here is best effort. A failed call logs at debug level and returns a safe default
(None, an empty list, False), so a timer can call these freely. Geometry is in physical pixels,
because the process is PER_MONITOR_AWARE_V2. As in Win32, a Rect's right and bottom edges are
exclusive.

The pure helpers (abgr_to_hex, clamp_to_rects, edge_from_rects, covers) hold the arithmetic and
are tested without a desktop.
"""
from __future__ import annotations

import ctypes
import threading
import winreg
from collections.abc import Sequence
from ctypes import wintypes
from dataclasses import dataclass
from typing import NamedTuple

from usage_monitor.log import get_logger
from usage_monitor.ui import win32

log = get_logger(__name__)

DEFAULT_DPI = 96

EDGE_LEFT, EDGE_TOP, EDGE_RIGHT, EDGE_BOTTOM = "left", "top", "right", "bottom"
_APPBAR_EDGES = {
    win32.ABE_LEFT: EDGE_LEFT, win32.ABE_TOP: EDGE_TOP,
    win32.ABE_RIGHT: EDGE_RIGHT, win32.ABE_BOTTOM: EDGE_BOTTOM,
}

# The desktop and the shell's own windows are never "a fullscreen app".
_IGNORED_CLASSES = frozenset({
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Shell_LightDismissOverlay",
})

_DWM_KEY = r"Software\Microsoft\Windows\DWM"
_PERSONALIZE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"


# ── DPI awareness ─────────────────────────────────────────────────────────────

_dpi_lock = threading.Lock()
_awareness: str | None = None


def dpi_awareness() -> str:
    """The calling thread's DPI awareness: "per_monitor_v2", "per_monitor", "system", "unaware"
    or "unknown"."""
    try:
        context = win32.GetThreadDpiAwarenessContext()
        for name, value in (
            ("per_monitor_v2", win32.DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2),
            ("per_monitor", win32.DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE),
            ("system", win32.DPI_AWARENESS_CONTEXT_SYSTEM_AWARE),
        ):
            if win32.AreDpiAwarenessContextsEqual(context, ctypes.c_void_p(value)):
                return name
        return "unaware"
    except Exception:
        log.debug("could not read the DPI awareness", exc_info=True)
        return "unknown"


def ensure_dpi_awareness() -> str:
    """Make the process PER_MONITOR_AWARE_V2, once, and return the awareness in effect.

    UIHost does this before it creates any window, and the geometry queries below do it first, so
    their numbers are physical pixels. A refusal (a manifest or an earlier call already fixed the
    awareness) is fine.
    """
    global _awareness
    with _dpi_lock:
        if _awareness is None:
            ctypes.set_last_error(0)
            context = ctypes.c_void_p(win32.DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
            if not win32.SetProcessDpiAwarenessContext(context):
                code = win32.last_error()
                if code != win32.ERROR_ACCESS_DENIED:
                    log.warning("SetProcessDpiAwarenessContext failed: %s", win32.error_text(code))
            _awareness = dpi_awareness()
            if _awareness != "per_monitor_v2":
                log.info("DPI awareness is %s; widgets cannot follow per-monitor scaling", _awareness)
        return _awareness


# ── Geometry types ────────────────────────────────────────────────────────────

class Rect(NamedTuple):
    """A screen rectangle: (left, top, right, bottom), right and bottom exclusive."""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def x(self) -> int:
        return self.left

    @property
    def y(self) -> int:
        return self.top

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def center(self) -> tuple[int, int]:
        return (self.left + self.right) // 2, (self.top + self.bottom) // 2

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom

    def overlap_area(self, other: Rect) -> int:
        """Area of the intersection with ``other`` (0 when they do not overlap)."""
        width = min(self.right, other.right) - max(self.left, other.left)
        height = min(self.bottom, other.bottom) - max(self.top, other.top)
        return width * height if width > 0 and height > 0 else 0

    @classmethod
    def from_win(cls, rect: wintypes.RECT) -> Rect:
        return cls(rect.left, rect.top, rect.right, rect.bottom)


@dataclass(frozen=True)
class Monitor:
    """One display: its full ``rect``, its ``work`` area (minus the taskbar) and its DPI."""

    rect: Rect
    work: Rect
    primary: bool
    device: str
    handle: int
    dpi: int = DEFAULT_DPI


@dataclass(frozen=True)
class TaskbarInfo:
    """A taskbar window. ``edge`` is "bottom", "top", "left" or "right".

    ``tray_rect`` is the notification area (TrayNotifyWnd; None on secondary taskbars or when the
    shell does not expose it). ``monitor_rect`` and ``work_area`` belong to the taskbar's monitor.
    """

    hwnd: int
    rect: Rect
    edge: str
    tray_rect: Rect | None
    monitor_rect: Rect
    work_area: Rect
    dpi: int
    autohide: bool

    @property
    def is_horizontal(self) -> bool:
        return self.edge in (EDGE_TOP, EDGE_BOTTOM)


# ── Pure helpers ──────────────────────────────────────────────────────────────

def abgr_to_hex(value: int) -> str:
    """Windows 0xAABBGGRR colour (the DWM AccentColor DWORD) as "#rrggbb"; alpha is dropped."""
    red = value & 0xFF
    green = (value >> 8) & 0xFF
    blue = (value >> 16) & 0xFF
    return f"#{red:02x}{green:02x}{blue:02x}"


def covers(outer: Rect, inner: Rect) -> bool:
    """True when ``outer`` contains every pixel of ``inner``."""
    return (
        outer.left <= inner.left and outer.top <= inner.top
        and outer.right >= inner.right and outer.bottom >= inner.bottom
    )


def edge_from_rects(bar: Rect, monitor: Rect) -> str:
    """Which side of ``monitor`` the taskbar ``bar`` is docked to, judged from the two rectangles."""
    if bar.width >= bar.height:
        near_top = abs(bar.top - monitor.top)
        near_bottom = abs(monitor.bottom - bar.bottom)
        return EDGE_TOP if near_top < near_bottom else EDGE_BOTTOM
    near_left = abs(bar.left - monitor.left)
    near_right = abs(monitor.right - bar.right)
    return EDGE_LEFT if near_left < near_right else EDGE_RIGHT


def _distance_sq(rect: Rect, px: int, py: int) -> int:
    dx = max(rect.left - px, 0, px - (rect.right - 1))
    dy = max(rect.top - py, 0, py - (rect.bottom - 1))
    return dx * dx + dy * dy


def _best_rect(x: int, y: int, w: int, h: int, rects: Sequence[Rect]) -> Rect:
    """The rect the window overlaps most; when it overlaps none, the one nearest its centre."""
    window = Rect(x, y, x + w, y + h)
    best = max(rects, key=window.overlap_area)
    if window.overlap_area(best) > 0:
        return best
    cx, cy = window.center
    return min(rects, key=lambda r: _distance_sq(r, cx, cy))


def nearest_rect(x: int, y: int, rects: Sequence[Rect]) -> Rect | None:
    """The rect containing the point (x, y), else the one nearest to it; None when there are none."""
    for rect in rects:
        if rect.contains(x, y):
            return rect
    return min(rects, key=lambda r: _distance_sq(r, x, y), default=None)


def clamp_to_rects(x: int, y: int, w: int, h: int, rects: Sequence[Rect]) -> tuple[int, int]:
    """Move a w x h window at (x, y) fully inside the most suitable rect and return its new (x, y).

    A window larger than the rect keeps its top-left corner in view. With no rects the position is
    returned unchanged.
    """
    if not rects:
        return x, y
    target = _best_rect(x, y, w, h, rects)
    new_x = max(target.left, min(x, target.right - w))
    new_y = max(target.top, min(y, target.bottom - h))
    return new_x, new_y


# ── Monitors and DPI ──────────────────────────────────────────────────────────

def _dpi_for_monitor(hmonitor: int | None) -> int:
    if not hmonitor:
        return DEFAULT_DPI
    dpi_x, dpi_y = wintypes.UINT(), wintypes.UINT()
    result = win32.GetDpiForMonitor(
        hmonitor, win32.MDT_EFFECTIVE_DPI, ctypes.byref(dpi_x), ctypes.byref(dpi_y)
    )
    return int(dpi_x.value) if result == 0 and dpi_x.value else DEFAULT_DPI


def _monitor_info(hmonitor: int | None) -> Monitor | None:
    if not hmonitor:
        return None
    info = win32.MONITORINFOEXW()
    info.cbSize = ctypes.sizeof(info)
    if not win32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
        return None
    return Monitor(
        rect=Rect.from_win(info.rcMonitor),
        work=Rect.from_win(info.rcWork),
        primary=bool(info.dwFlags & win32.MONITORINFOF_PRIMARY),
        device=info.szDevice,
        handle=int(hmonitor),
        dpi=_dpi_for_monitor(hmonitor),
    )


def monitors(*, set_awareness: bool = True) -> list[Monitor]:
    """Every attached display, the primary one first. Empty when enumeration fails.

    ``set_awareness=False`` leaves the process's DPI awareness alone, for a process that does not
    own it (the dashboard child: pywebview sets it) and wants coordinates in that process's space.
    """
    if set_awareness:
        ensure_dpi_awareness()
    found: list[Monitor] = []

    @win32.MONITORENUMPROC
    def collect(hmonitor, _hdc, _rect, _lparam):  # type: ignore[no-untyped-def]
        try:
            monitor = _monitor_info(hmonitor)
            if monitor is not None:
                found.append(monitor)
        except Exception:  # an exception must never cross back into EnumDisplayMonitors
            log.debug("monitor query failed", exc_info=True)
        return True

    try:
        if not win32.EnumDisplayMonitors(None, None, collect, 0):
            log.debug("EnumDisplayMonitors failed: %s", win32.error_text())
    except Exception:
        log.debug("EnumDisplayMonitors raised", exc_info=True)
    found.sort(key=lambda m: not m.primary)
    return found


def work_areas(*, set_awareness: bool = True) -> list[Rect]:
    """The work area (monitor minus taskbar) of every display, primary first."""
    return [m.work for m in monitors(set_awareness=set_awareness)]


def dpi_for_point(x: int, y: int) -> int:
    """Effective DPI of the monitor under (x, y), or the nearest one. 96 when it cannot be read."""
    try:
        ensure_dpi_awareness()
        point = wintypes.POINT(int(x), int(y))
        hmonitor = win32.MonitorFromPoint(point, win32.MONITOR_DEFAULTTONEAREST)
        return _dpi_for_monitor(hmonitor)
    except Exception:
        log.debug("dpi_for_point failed", exc_info=True)
        return DEFAULT_DPI


def cursor_pos() -> tuple[int, int] | None:
    """The mouse pointer's screen position, or None when Windows will not say (a locked desktop)."""
    point = wintypes.POINT()
    if not win32.GetCursorPos(ctypes.byref(point)):
        return None
    return point.x, point.y


def work_area_at(x: int, y: int) -> Rect | None:
    """The work area of the monitor holding (x, y), or of the nearest one; None without monitors."""
    return nearest_rect(x, y, work_areas())


def clamp_to_work_area(x: int, y: int, w: int, h: int) -> tuple[int, int]:
    """Return (x, y) moved so a w x h window lies inside a monitor's work area."""
    return clamp_to_rects(x, y, w, h, work_areas())


# ── Taskbar ───────────────────────────────────────────────────────────────────

def _window_rect(hwnd: int) -> Rect | None:
    rect = wintypes.RECT()
    if not win32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return Rect.from_win(rect)


def _appbar_data(hwnd: int) -> win32.APPBARDATA:
    data = win32.APPBARDATA()
    data.cbSize = ctypes.sizeof(data)
    data.hWnd = hwnd
    return data


def _taskbar_edge(hwnd: int) -> str | None:
    """The primary taskbar's edge as the shell reports it (ABM_GETTASKBARPOS)."""
    data = _appbar_data(hwnd)
    if not win32.SHAppBarMessage(win32.ABM_GETTASKBARPOS, ctypes.byref(data)):
        return None
    return _APPBAR_EDGES.get(data.uEdge)


def _autohide_enabled(hwnd: int) -> bool:
    data = _appbar_data(hwnd)
    return bool(win32.SHAppBarMessage(win32.ABM_GETSTATE, ctypes.byref(data)) & win32.ABS_AUTOHIDE)


def _tray_notify_rect(taskbar_hwnd: int) -> Rect | None:
    tray = win32.FindWindowExW(taskbar_hwnd, None, "TrayNotifyWnd", None)
    rect = _window_rect(tray) if tray else None
    return rect if rect is not None and rect.width > 0 and rect.height > 0 else None


def _build_taskbar(
    hwnd: int, edge: str | None, with_tray: bool, autohide: bool
) -> TaskbarInfo | None:
    rect = _window_rect(hwnd)
    if rect is None:
        return None
    monitor = _monitor_info(win32.MonitorFromWindow(hwnd, win32.MONITOR_DEFAULTTONEAREST))
    monitor_rect = monitor.rect if monitor else rect
    return TaskbarInfo(
        hwnd=int(hwnd),
        rect=rect,
        edge=edge or edge_from_rects(rect, monitor_rect),
        tray_rect=_tray_notify_rect(hwnd) if with_tray else None,
        monitor_rect=monitor_rect,
        work_area=monitor.work if monitor else monitor_rect,
        dpi=monitor.dpi if monitor else DEFAULT_DPI,
        autohide=autohide,
    )


def taskbar_info() -> TaskbarInfo | None:
    """The primary taskbar (Shell_TrayWnd), or None when the shell is not running."""
    try:
        ensure_dpi_awareness()
        hwnd = win32.FindWindowW("Shell_TrayWnd", None)
        if not hwnd:
            return None
        return _build_taskbar(hwnd, _taskbar_edge(hwnd), True, _autohide_enabled(hwnd))
    except Exception:
        log.debug("taskbar_info failed", exc_info=True)
        return None


def taskbar_infos() -> list[TaskbarInfo]:
    """The primary taskbar followed by every secondary one (Shell_SecondaryTrayWnd)."""
    found: list[TaskbarInfo] = []
    primary = taskbar_info()
    if primary is not None:
        found.append(primary)
    try:
        autohide = primary.autohide if primary else False
        hwnd = None
        for _ in range(16):  # more taskbars than any desktop has; bounds the loop
            hwnd = win32.FindWindowExW(None, hwnd, "Shell_SecondaryTrayWnd", None)
            if not hwnd:
                break
            info = _build_taskbar(hwnd, None, False, autohide)
            if info is not None:
                found.append(info)
    except Exception:
        log.debug("secondary taskbar scan failed", exc_info=True)
    return found


# ── Fullscreen, lock, screensaver ─────────────────────────────────────────────

def _class_name(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    return buffer.value if win32.GetClassNameW(hwnd, buffer, len(buffer)) else ""


def _is_ignored_window(hwnd: int) -> bool:
    if _class_name(hwnd) in _IGNORED_CLASSES:
        return True
    pid = wintypes.DWORD()
    win32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value == win32.GetCurrentProcessId()


def _is_cloaked(hwnd: int) -> bool:
    cloaked = wintypes.DWORD()
    result = win32.DwmGetWindowAttribute(
        hwnd, win32.DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
    )
    return result == 0 and cloaked.value != 0


def _visible_bounds(hwnd: int) -> Rect | None:
    """The window's frame without the invisible resize border Windows adds around it."""
    rect = wintypes.RECT()
    result = win32.DwmGetWindowAttribute(
        hwnd, win32.DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect)
    )
    return Rect.from_win(rect) if result == 0 else _window_rect(hwnd)


def _notification_state_is_fullscreen() -> bool:
    state = ctypes.c_int(0)
    result = win32.SHQueryUserNotificationState(ctypes.byref(state))
    return result == 0 and state.value in (
        win32.QUNS_RUNNING_D3D_FULL_SCREEN, win32.QUNS_PRESENTATION_MODE,
    )


def _foreground_covers_its_monitor() -> bool:
    hwnd = win32.GetForegroundWindow()
    if not hwnd or _is_ignored_window(hwnd) or win32.IsIconic(hwnd) or _is_cloaked(hwnd):
        return False
    style = win32.GetWindowLongPtrW(hwnd, win32.GWL_STYLE) & 0xFFFFFFFF
    if style & win32.WS_MAXIMIZE and style & win32.WS_CAPTION == win32.WS_CAPTION:
        return False  # an ordinary maximized window, not a fullscreen one
    monitor = _monitor_info(win32.MonitorFromWindow(hwnd, win32.MONITOR_DEFAULTTONEAREST))
    bounds = _visible_bounds(hwnd)
    return monitor is not None and bounds is not None and covers(bounds, monitor.rect)


def is_fullscreen_foreground() -> bool:
    """True while a fullscreen or borderless-fullscreen app has the foreground.

    Exclusive Direct3D fullscreen and presentation mode come from SHQueryUserNotificationState.
    Anything else counts when the foreground window covers the whole of its own monitor. The
    desktop, the taskbars and this process's windows never count.
    """
    try:
        return _notification_state_is_fullscreen() or _foreground_covers_its_monitor()
    except Exception:
        log.debug("is_fullscreen_foreground failed", exc_info=True)
        return False


def _session_flags_say_locked() -> bool:
    """The session's own record (WTSSessionInfoEx): SessionFlags is WTS_SESSIONSTATE_LOCK."""
    buffer = ctypes.c_void_p()
    size = wintypes.DWORD()
    queried = win32.WTSQuerySessionInformationW(
        None, win32.WTS_CURRENT_SESSION, win32.WTSSessionInfoEx,
        ctypes.byref(buffer), ctypes.byref(size),
    )
    if not queried or not buffer.value:
        return False
    try:
        if size.value < ctypes.sizeof(win32.WTSINFOEX_HEAD):
            return False
        head = ctypes.cast(buffer, ctypes.POINTER(win32.WTSINFOEX_HEAD)).contents
        return head.Level == 1 and head.SessionFlags == win32.WTS_SESSIONSTATE_LOCK
    finally:
        win32.WTSFreeMemory(buffer)


def is_locked() -> bool:
    """True while the workstation is locked.

    Two signals, either is enough. The input desktop cannot be opened (the classic check; the
    secure desktop of a UAC prompt reads as locked too, which suits a monitor). And the session's
    own lock flag: on recent Windows 11 builds the lock screen is drawn on the default desktop,
    so OpenInputDesktop keeps succeeding while the screen is locked.
    """
    try:
        desktop = win32.OpenInputDesktop(0, False, win32.DESKTOP_SWITCHDESKTOP)
        if not desktop:
            return True
        win32.CloseDesktop(desktop)
        return _session_flags_say_locked()
    except Exception:
        log.debug("is_locked failed", exc_info=True)
        return False


def screensaver_running() -> bool:
    """True while a screensaver is running.

    SPI_GETSCREENSAVERRUNNING writes a 4-byte BOOL; v1 passed a 1-byte c_bool, so the call wrote
    three bytes past it.
    """
    try:
        running = wintypes.BOOL(0)
        if not win32.SystemParametersInfoW(
            win32.SPI_GETSCREENSAVERRUNNING, 0, ctypes.byref(running), 0
        ):
            return False
        return bool(running.value)
    except Exception:
        log.debug("screensaver_running failed", exc_info=True)
        return False


def should_pause() -> bool:
    """v1's pause rule: the screensaver is running or the workstation is locked."""
    return screensaver_running() or is_locked()


# ── System theme ──────────────────────────────────────────────────────────────

def _read_dword(subkey: str, name: str) -> int | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as key:
            value, kind = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    return value if kind == winreg.REG_DWORD and isinstance(value, int) else None


def windows_accent_hex() -> str | None:
    """The user's Windows accent colour as "#rrggbb", or None when the registry has none."""
    value = _read_dword(_DWM_KEY, "AccentColor")
    return None if value is None else abgr_to_hex(value)


def apps_use_light_theme() -> bool:
    """True when apps follow the light Windows theme. False (dark) when the value is missing, the
    default of the theme layer."""
    return bool(_read_dword(_PERSONALIZE_KEY, "AppsUseLightTheme"))


def system_uses_light_theme() -> bool:
    """True when the taskbar and Start follow the light theme (what a taskbar widget sits on).
    False (dark) when the value is missing."""
    return bool(_read_dword(_PERSONALIZE_KEY, "SystemUsesLightTheme"))
