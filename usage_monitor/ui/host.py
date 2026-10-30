"""UIHost: the hub of the UI thread.

One UIHost owns a hidden top-level controller window and runs the Win32 message loop on the
thread that created it. The controller must be a real top-level window, not HWND_MESSAGE:
WM_SETTINGCHANGE, WM_DISPLAYCHANGE and the registered "TaskbarCreated" message are broadcast
to top-level windows only. The host gives the rest of the UI

* post(fn): run a callable on the UI thread from any thread (a queue plus one WM_APP_POST),
* timers: SetTimer on the controller, an id -> callback table,
* on_session_change / on_settings_change / on_display_change / on_taskbar_created: broadcast
  notifications, plus lock and unlock from WTSRegisterSessionNotification; on_settings_change
  also carries the app's own settings changes (notify_settings_changed, from any thread),
* the shared window procedure and window-class helpers the other ui modules build on.

Threading: construct the host and call run() on the same thread. After that every method that
touches a window runs on that thread; post(), quit(), set_timer(), kill_timer() and the on_*
registrations are safe from any thread.

Callbacks are called with as many of their arguments as they accept (see invoke), so
``on_settings_change(lambda: ...)`` and ``on_settings_change(lambda area: ...)`` both work.
"""
from __future__ import annotations

import collections
import ctypes
import inspect
import itertools
import os
import signal
import threading
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any, Protocol

from usage_monitor.log import get_logger
from usage_monitor.ui import win32
from usage_monitor.ui.platform import ensure_dpi_awareness, is_locked

log = get_logger(__name__)

CONTROLLER_CLASS = "ClaudeUsageMonitor.Controller"
CONTROLLER_TITLE = "Claude Usage Monitor Controller"

_DEV_ENV = "CUM_DEV_INSTANCE"
_SIGINT_POLL_MS = 250


def controller_class_name() -> str:
    """Window class of the controller. A dev instance (CUM_DEV_INSTANCE=1) gets its own, so a
    test build never answers the installed copy's "open the dashboard" signal."""
    return CONTROLLER_CLASS + (".dev" if os.environ.get(_DEV_ENV) == "1" else "")


def find_controller(class_name: str | None = None) -> int:
    """The controller window of a running instance, or 0. Used by a second launch."""
    return int(win32.FindWindowW(class_name or controller_class_name(), None) or 0)


def signal_running_instance(
    message: int = win32.WM_APP_OPEN_DASHBOARD, class_name: str | None = None
) -> bool:
    """Post ``message`` (default: open the dashboard) to a running instance. False if none runs."""
    hwnd = find_controller(class_name)
    return bool(hwnd and win32.PostMessageW(hwnd, message, 0, 0))


# ── Calling callbacks ─────────────────────────────────────────────────────────

def _positional_capacity(fn: Callable[..., Any]) -> int | None:
    """How many positional arguments ``fn`` takes; None when unbounded or unknown."""
    try:
        parameters = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return None
    count = 0
    for parameter in parameters:
        if parameter.kind is parameter.VAR_POSITIONAL:
            return None
        if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD):
            count += 1
    return count


def invoke(callback: Callable[..., Any], *args: Any) -> Any:
    """Call ``callback`` with the leading ``args`` it can accept, so one event can carry more
    detail than most listeners want."""
    capacity = _positional_capacity(callback)
    return callback(*(args if capacity is None else args[:capacity]))


# ── Window classes and the shared window procedure ────────────────────────────

class WindowHandler(Protocol):
    """What a window object provides: return an LRESULT to take a message, None for the default."""

    def handle_message(self, msg: int, wparam: int, lparam: int) -> int | None: ...


_handlers: dict[int, WindowHandler] = {}
_registered_classes: set[str] = set()
_class_lock = threading.Lock()


def _wndproc(hwnd: int | None, msg: int, wparam: int, lparam: int) -> int:
    """The one window procedure of every window the UI creates. It never raises: an exception in
    a handler is logged and the message falls through to the default procedure."""
    handler = _handlers.get(hwnd or 0)
    try:
        if handler is not None:
            result = handler.handle_message(msg, wparam, lparam)
            if result is not None:
                return result
    except Exception:
        log.exception("window message %#06x failed", msg)
    finally:
        if msg == win32.WM_NCDESTROY:
            _handlers.pop(hwnd or 0, None)
    return win32.DefWindowProcW(hwnd, msg, wparam, lparam)


_WNDPROC = win32.WNDPROC(_wndproc)  # module level: ctypes callbacks must stay referenced


def register_window_class(name: str, *, style: int = 0) -> None:
    """Register ``name`` with the shared window procedure. Safe to repeat."""
    with _class_lock:
        if name in _registered_classes:
            return
        wc = win32.WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(wc)
        wc.style = style
        wc.lpfnWndProc = _WNDPROC
        wc.hInstance = win32.GetModuleHandleW(None)
        wc.hCursor = win32.LoadCursorW(None, win32.IDC_ARROW)
        wc.lpszClassName = name
        ctypes.set_last_error(0)
        if not win32.RegisterClassExW(ctypes.byref(wc)):
            if win32.last_error() != win32.ERROR_CLASS_ALREADY_EXISTS:
                raise OSError(f"RegisterClassExW({name!r}) failed: {win32.error_text()}")
        _registered_classes.add(name)


def create_window(
    class_name: str, ex_style: int, handler: WindowHandler, *, title: str = "",
    style: int = win32.WS_POPUP, parent: int | None = None,
) -> int:
    """Create a zero-sized window of ``class_name`` and route its messages to ``handler``: a WS_POPUP
    unless ``style`` says otherwise, the child of ``parent`` when one is given. Must be called on the
    thread that will pump its messages."""
    ctypes.set_last_error(0)
    hwnd = win32.CreateWindowExW(
        ex_style, class_name, title, style, 0, 0, 0, 0,
        parent or None, None, win32.GetModuleHandleW(None), None,
    )
    if not hwnd:
        raise OSError(f"CreateWindowExW({class_name!r}) failed: {win32.error_text()}")
    _handlers[hwnd] = handler
    return int(hwnd)


# ── The host ──────────────────────────────────────────────────────────────────

@dataclass
class _Timer:
    fn: Callable[[], None]
    repeat: bool
    failures: int = 0


def _setting_area(wparam: int, lparam: int) -> str:
    """What changed in a WM_SETTINGCHANGE: "WorkArea", the broadcaster's area string (for
    example "ImmersiveColorSet" or "TraySettings"), or "" when the message names none."""
    if wparam == win32.SPI_SETWORKAREA:
        return "WorkArea"
    if wparam == 0 and lparam:  # only then is lParam a string pointer
        try:
            return ctypes.wstring_at(lparam)
        except (OSError, ValueError):
            return ""
    return ""


class UIHost:
    """The UI thread's controller window, message loop, timers and broadcast notifications."""

    def __init__(self, *, controller_class: str | None = None) -> None:
        self._thread_id = threading.get_ident()
        self._exit_code = 0
        self._running = False
        self._closing = False
        self._closed = False
        self._locked = False  # set from the session state once the controller exists
        self._queue: collections.deque[Callable[[], None]] = collections.deque()
        self._wake_lock = threading.Lock()
        self._wake_pending = False
        self._settings_pending = False
        self._timers: dict[int, _Timer] = {}
        self._timer_ids = itertools.count(1)
        self._listeners: dict[str, list[Callable[..., Any]]] = collections.defaultdict(list)
        self._cleanups: list[Callable[[], None]] = []
        self._taskbar_created = win32.RegisterWindowMessageW("TaskbarCreated")
        self._dispatch: dict[int, Callable[[int, int], int | None]] = {
            win32.WM_APP_POST: self._on_post,
            win32.WM_TIMER: self._on_timer_message,
            win32.WM_WTSSESSION_CHANGE: self._on_session_message,
            win32.WM_DISPLAYCHANGE: self._on_display_message,
            win32.WM_SETTINGCHANGE: self._on_settings_message,
            win32.WM_APP_SETTINGS: self._on_app_settings_message,
            win32.WM_APP_OPEN_DASHBOARD: self._on_open_dashboard_message,
            win32.WM_CLOSE: self._on_close_message,
            win32.WM_ENDSESSION: self._on_end_session_message,
            win32.WM_DESTROY: self._on_destroy_message,
            win32.WM_NCDESTROY: self._on_ncdestroy_message,
        }
        if self._taskbar_created:  # 0 would mean the registration failed, and 0 is WM_NULL
            self._dispatch[self._taskbar_created] = self._on_taskbar_created_message

        awareness = ensure_dpi_awareness()
        self._class_name = controller_class or controller_class_name()
        register_window_class(self._class_name)
        self._hwnd = create_window(
            self._class_name, win32.WS_EX_TOOLWINDOW, self, title=CONTROLLER_TITLE
        )
        self._session_registered = bool(
            win32.WTSRegisterSessionNotification(self._hwnd, win32.NOTIFY_FOR_THIS_SESSION)
        )
        if not self._session_registered:
            log.warning("WTSRegisterSessionNotification failed: %s", win32.error_text())
        self._locked = is_locked()
        log.info("UI host ready (DPI awareness: %s, session locked: %s)", awareness, self._locked)

    # ── State ──

    @property
    def hwnd(self) -> int:
        """The controller window (0 once it is destroyed). Owner of tray icons and menus."""
        return self._hwnd

    @property
    def locked(self) -> bool:
        """Whether the session is locked: read when the host was created, then kept current by the
        session notifications."""
        return self._locked

    def on_ui_thread(self) -> bool:
        return threading.get_ident() == self._thread_id

    # ── Loop ──

    def run(self, *, handle_sigint: bool = False) -> int:
        """Pump messages until quit() and return the exit code.

        With ``handle_sigint`` Ctrl+C in a console calls quit() (main thread only; for dev runs,
        it costs a 250 ms wake-up so Python gets to run the signal handler).
        """
        if not self.on_ui_thread():
            raise RuntimeError("UIHost.run() must be called on the thread that created the host")
        if self._closed or self._running:
            raise RuntimeError("UIHost is closed or already running")
        self._running = True
        restore = self._install_sigint() if handle_sigint else None
        msg = wintypes.MSG()
        try:
            while True:
                status = win32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if status == 0:
                    self._exit_code = ctypes.c_int(msg.wParam).value
                    break
                if status == -1:
                    log.error("GetMessageW failed: %s", win32.error_text())
                    break
                win32.TranslateMessage(ctypes.byref(msg))
                win32.DispatchMessageW(ctypes.byref(msg))
        finally:
            self._running = False
            if restore is not None:
                restore()
            self._shutdown(self._exit_code)
        return self._exit_code

    def quit(self, exit_code: int = 0) -> None:
        """Clean up and end run(). Safe from any thread, and safe to repeat."""
        self.post(lambda: self._shutdown(exit_code))

    def close(self) -> None:
        """Clean up and destroy the controller right now, without the loop: for a host whose run()
        never started or has ended. From another thread it is forwarded. Safe to repeat."""
        if self.on_ui_thread():
            self._shutdown(self._exit_code)
        else:
            self.post(lambda: self._shutdown(self._exit_code))

    def _install_sigint(self) -> Callable[[], None] | None:
        if threading.current_thread() is not threading.main_thread():
            return None
        previous = signal.signal(signal.SIGINT, lambda *_: self.quit())
        poll = self.set_timer(_SIGINT_POLL_MS, lambda: None)

        def restore() -> None:
            self.kill_timer(poll)
            signal.signal(signal.SIGINT, previous)

        return restore

    def _shutdown(self, exit_code: int = 0) -> None:
        """UI thread: run the cleanups, drop timers and the session registration, destroy the
        controller (its WM_DESTROY ends the loop while one is running)."""
        if self._closing:
            return
        self._closing = True
        self._exit_code = exit_code
        cleanups, self._cleanups = self._cleanups, []  # each one also tries to remove itself
        for cleanup in reversed(cleanups):
            try:
                cleanup()
            except Exception:
                name = getattr(cleanup, "__qualname__", cleanup)
                log.exception("shutdown cleanup %s failed", name)
        for timer_id in list(self._timers):
            self.kill_timer(timer_id)
        if self._session_registered and self._hwnd:
            win32.WTSUnRegisterSessionNotification(self._hwnd)
            self._session_registered = False
        if self._hwnd:
            if not win32.DestroyWindow(self._hwnd):
                log.warning("DestroyWindow(controller) failed: %s", win32.error_text())
                self._closed = True
                if self._running:
                    win32.PostQuitMessage(self._exit_code)

    def add_cleanup(self, fn: Callable[[], None]) -> Callable[[], None]:
        """Run ``fn`` on the UI thread when the host shuts down, newest first. Returns a remover.
        Windows and the tray icon register themselves, so none outlives the loop."""
        self._cleanups.append(fn)

        def remove() -> None:
            try:
                self._cleanups.remove(fn)
            except ValueError:
                pass

        return remove

    # ── Posting ──

    def post(self, fn: Callable[[], None]) -> None:
        """Run ``fn()`` on the UI thread, soon. Thread-safe. Dropped after shutdown."""
        if self._closed:
            log.debug("post() after shutdown ignored")
            return
        self._queue.append(fn)
        self._wake()

    def _wake(self) -> None:
        """Post at most one WM_APP_POST at a time; one drain empties the whole queue."""
        with self._wake_lock:
            if self._wake_pending:
                return
            self._wake_pending = True
        if not win32.PostMessageW(self._hwnd, win32.WM_APP_POST, 0, 0):
            with self._wake_lock:
                self._wake_pending = False
            log.debug("could not wake the UI thread: %s", win32.error_text())

    def _on_post(self, _wparam: int, _lparam: int) -> int:
        with self._wake_lock:
            self._wake_pending = False  # later posts must wake the loop again
        for _ in range(len(self._queue)):  # only what was queued now; new work wakes us again
            try:
                fn = self._queue.popleft()
            except IndexError:
                break
            try:
                fn()
            except Exception:
                log.exception("posted call failed")
        return 0

    def _run_on_ui(self, fn: Callable[[], None]) -> None:
        if self.on_ui_thread():
            fn()
        else:
            self.post(fn)

    # ── Timers ──

    def set_timer(self, ms: int, fn: Callable[[], None], *, repeat: bool = True) -> int:
        """Call ``fn()`` on the UI thread every ``ms`` milliseconds (once with repeat=False).
        Returns the timer id for kill_timer. The resolution is about 15 ms."""
        timer_id = next(self._timer_ids)
        self._timers[timer_id] = _Timer(fn, repeat)
        interval = min(max(int(ms), win32.USER_TIMER_MINIMUM), win32.USER_TIMER_MAXIMUM)
        self._run_on_ui(lambda: self._arm_timer(timer_id, interval))
        return timer_id

    def kill_timer(self, timer_id: int) -> None:
        """Stop a timer. Unknown ids are ignored."""
        if self._timers.pop(timer_id, None) is not None:
            self._run_on_ui(lambda: self._disarm_timer(timer_id))

    def _arm_timer(self, timer_id: int, interval: int) -> None:
        if timer_id not in self._timers or not self._hwnd:
            return
        if not win32.SetTimer(self._hwnd, timer_id, interval, None):
            log.warning("SetTimer(%d ms) failed: %s", interval, win32.error_text())
            self._timers.pop(timer_id, None)

    def _disarm_timer(self, timer_id: int) -> None:
        if self._hwnd:
            win32.KillTimer(self._hwnd, timer_id)

    def _on_timer_message(self, timer_id: int, _lparam: int) -> int:
        timer = self._timers.get(timer_id)
        if timer is None:  # a tick that was already queued when the timer was killed
            self._disarm_timer(timer_id)
            return 0
        if not timer.repeat:
            self._timers.pop(timer_id, None)
            self._disarm_timer(timer_id)
        try:
            timer.fn()
        except Exception:
            timer.failures += 1
            if timer.failures == 1 or timer.failures % 100 == 0:  # an animation must not flood the log
                log.exception("timer %d callback failed (%d so far)", timer_id, timer.failures)
        return 0

    # ── Notifications ──

    def on_session_change(self, cb: Callable[..., None]) -> Callable[[], None]:
        """``cb(locked: bool)`` when the session locks (True) or unlocks (False)."""
        return self._subscribe("session", cb)

    def on_settings_change(self, cb: Callable[..., None]) -> Callable[[], None]:
        """``cb(area: str)`` when settings change, whose are named by ``area``. Windows' own
        (WM_SETTINGCHANGE): "WorkArea" (taskbar or work area moved), the broadcaster's string such
        as "ImmersiveColorSet" (theme or accent) or "TraySettings", or "". The app's own
        (WM_APP_SETTINGS, see notify_settings_changed): "app". The argument is optional."""
        return self._subscribe("settings", cb)

    def notify_settings_changed(self) -> None:
        """Tell the UI thread the app's settings changed: its on_settings_change listeners get
        area "app". Safe from any thread, and coalesced with an identical pending notice."""
        if self._closed:
            return
        with self._wake_lock:
            if self._settings_pending:
                return
            self._settings_pending = True
        if not win32.PostMessageW(self._hwnd, win32.WM_APP_SETTINGS, 0, 0):
            with self._wake_lock:
                self._settings_pending = False

    def on_display_change(self, cb: Callable[..., None]) -> Callable[[], None]:
        """``cb()`` on WM_DISPLAYCHANGE: resolution, monitor layout or DPI changed."""
        return self._subscribe("display", cb)

    def on_taskbar_created(self, cb: Callable[..., None]) -> Callable[[], None]:
        """``cb()`` when Explorer (re)creates the taskbar: re-add tray icons, re-place widgets."""
        return self._subscribe("taskbar", cb)

    def on_open_dashboard(self, cb: Callable[..., None]) -> Callable[[], None]:
        """``cb()`` when WM_APP_OPEN_DASHBOARD arrives, which is what a second launch posts."""
        return self._subscribe("open_dashboard", cb)

    def on_end_session(self, cb: Callable[..., None]) -> Callable[[], None]:
        """``cb()`` when Windows is logging off or shutting down: flush state now. The host
        then shuts down."""
        return self._subscribe("end_session", cb)

    def add_message_handler(
        self, msg: int, handler: Callable[[int, int], int | None]
    ) -> Callable[[], None]:
        """Route a private message (WM_APP range) sent to the controller to
        ``handler(wparam, lparam)``. The tray icon uses this for its callback message. Returns a
        remover."""
        if msg in self._dispatch:
            raise ValueError(f"message {msg:#06x} already has a handler")
        self._dispatch[msg] = handler

        def remove() -> None:
            if self._dispatch.get(msg) is handler:
                del self._dispatch[msg]

        return remove

    def _subscribe(self, kind: str, cb: Callable[..., None]) -> Callable[[], None]:
        self._listeners[kind].append(cb)

        def unsubscribe() -> None:
            try:
                self._listeners[kind].remove(cb)
            except ValueError:
                pass

        return unsubscribe

    def _notify(self, kind: str, *args: Any) -> None:
        for cb in list(self._listeners.get(kind, ())):
            try:
                invoke(cb, *args)
            except Exception:
                log.exception("%s listener %s failed", kind, getattr(cb, "__qualname__", cb))

    # ── Messages ──

    def handle_message(self, msg: int, wparam: int, lparam: int) -> int | None:
        handler = self._dispatch.get(msg)
        return handler(wparam, lparam) if handler is not None else None

    def _on_session_message(self, wparam: int, _lparam: int) -> int:
        if wparam in (win32.WTS_SESSION_LOCK, win32.WTS_SESSION_UNLOCK):
            self._locked = wparam == win32.WTS_SESSION_LOCK
            self._notify("session", self._locked)
        return 0

    def _on_display_message(self, _wparam: int, _lparam: int) -> int:
        self._notify("display")
        return 0

    def _on_settings_message(self, wparam: int, lparam: int) -> int:
        self._notify("settings", _setting_area(wparam, lparam))
        return 0

    def _on_app_settings_message(self, _wparam: int, _lparam: int) -> int:
        with self._wake_lock:
            self._settings_pending = False
        self._notify("settings", "app")
        return 0

    def _on_taskbar_created_message(self, _wparam: int, _lparam: int) -> int:
        self._notify("taskbar")
        return 0

    def _on_open_dashboard_message(self, _wparam: int, _lparam: int) -> int:
        self._notify("open_dashboard")
        return 0

    def _on_close_message(self, _wparam: int, _lparam: int) -> int:
        self.quit()
        return 0

    def _on_end_session_message(self, wparam: int, _lparam: int) -> int:
        if wparam:  # the session really is ending
            self._notify("end_session")
            self._shutdown(self._exit_code)
        return 0

    def _on_destroy_message(self, _wparam: int, _lparam: int) -> int:
        if self._running:  # a WM_QUIT with nobody listening would stop a later loop on this thread
            win32.PostQuitMessage(self._exit_code)
        return 0

    def _on_ncdestroy_message(self, _wparam: int, _lparam: int) -> None:
        self._hwnd = 0
        self._closed = True
        return None
