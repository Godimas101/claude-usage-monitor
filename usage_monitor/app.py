"""MonitorApp: wires settings, the data service, the UI host, the widgets, the tray and the local API.

    run()  logging, single-instance mutex, Settings (+ theme migration), UIHost, DataService, the
           API server, the widgets the settings ask for, the tray icon, then the message loop.

Threads (docs/UI_ARCHITECTURE.md): the UI thread (this one) owns every window. The data service
publishes MonitorState on its own thread, and the callback only parks the latest state and posts a
pull to the UI thread. Settings changes from the API threads do the same. The API server and the
dashboard child never touch a window.

The UI is redrawn when the state, the settings, the DPI or the display change, and by one adaptive
timer for the text that moves with the clock (the age readout, the time rails). The alive mark
blinks on a second timer, only while a widget is visible, only for a theme that blinks, and never
with reduce_motion or a locked session. Idle cost is a few wake-ups a second.

Usage alerts: every state the UI thread picks up goes through data.alerts.AlertEngine, and each
alert it returns is shown as a tray toast. The engine is told when a fullscreen app has the
foreground, and holds the toast back then.

Shutdown order: close the dashboard, stop the data service, stop the API server, remove the tray
icon, destroy the windows, flush settings.

Also here: SmokeTest, the --smoke-test run (see main.py), and the small pure helpers the tests cover
(mutex_name, tray_tooltip, menu_items, blink_cycle, tick_delay_ms, UpdateTracker).
"""
from __future__ import annotations

import ctypes
import json
import logging
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Callable, Mapping
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import update_check
import version
from usage_monitor import paths, themes
from usage_monitor.data.alerts import AlertEngine
from usage_monitor.data.models import ApiStatus, MonitorState
from usage_monitor.data.service import DataService
from usage_monitor.data.store import Store
from usage_monitor.log import get_logger, setup_logging
from usage_monitor.render.model import WidgetModel, build_model
from usage_monitor.render.skins import RenderOptions, get_skin
from usage_monitor.settings import DEFAULTS as SETTINGS_DEFAULTS
from usage_monitor.settings import Settings
from usage_monitor.ui import platform as plat
from usage_monitor.ui import startup, win32
from usage_monitor.ui.dashboard import DashboardLauncher
from usage_monitor.ui.dashboard import find_window_of as dashboard_window_of
from usage_monitor.ui.host import UIHost, signal_running_instance
from usage_monitor.ui.menu import SEPARATOR, MenuItem, show_menu
from usage_monitor.ui.server import DEV_ENV, SETTING_SPECS, ApiServer, open_statistics_store
from usage_monitor.ui.tray import TrayIcon
from usage_monitor.ui.widgets import (
    FloatingWidget,
    FrameStats,
    RenderContext,
    TaskbarWidget,
    Tooltip,
)

log = get_logger(__name__)

MUTEX_NAME = "Global\\ClaudeUsageMonitor"
ERROR_ALREADY_EXISTS = 183

TRAY_SIZES = (16, 20, 24, 32)       # 100 %, 125 %, 150 % and 200 % taskbars; others scale from these
TICK_CAP_S = 30.0                   # the clock-driven redraw never waits longer than this
SERVICE_STOP_S = 3.0                # how long shutdown waits for a data cycle that is running
DASHBOARD_CLOSE_S = 3.0             # how long shutdown waits for the dashboard to save its bounds
SECOND_INSTANCE_WAIT_S = 3.0        # a second launch waits this long for the first one's window
UI_CALL_TIMEOUT_S = 5.0
GITHUB_PREFIX = "https://github.com/"

# Settings that change what the UI shows. Anything else (poll interval, dashboard bounds) is ignored here.
THEME_KEYS = frozenset({"theme", "theme_id", "theme_variant", "theme_mode"})
WIDGET_KEYS = frozenset({"show_floating", "show_taskbar"})
LOOK_KEYS = frozenset({
    "float_transparent_bg", "float_text_fx", "taskbar_transparent_bg", "taskbar_text_fx",
    "show_scoped_rows", "reduce_motion", "hide_floating_in_fullscreen", "position",
})
SCOPED_MODES = ("auto", "always", "never")


# ── Single instance ───────────────────────────────────────────────────────────

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_CreateMutexW = _kernel32.CreateMutexW
_CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
_CreateMutexW.restype = wintypes.HANDLE
_CloseHandle = _kernel32.CloseHandle
_CloseHandle.argtypes = [wintypes.HANDLE]
_CloseHandle.restype = wintypes.BOOL


def is_dev_instance(env: Mapping[str, str] | None = None) -> bool:
    """True when CUM_DEV_INSTANCE=1: a test build that runs beside the installed copy."""
    return (os.environ if env is None else env).get(DEV_ENV) == "1"


def mutex_name(dev: bool) -> str:
    """The single-instance mutex: Global\\ClaudeUsageMonitor, or ...-dev for a dev instance."""
    return MUTEX_NAME + ("-dev" if dev else "")


class SingleInstance:
    """A named mutex that lets one monitor run per machine. Keep the object alive while running."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._handle: int | None = None

    def acquire(self) -> bool:
        """True when this process now owns the name; False when another instance holds it. A mutex
        that cannot be created at all (another user's session may hold the name) lets the app run."""
        ctypes.set_last_error(0)
        handle = _CreateMutexW(None, False, self.name)
        error = ctypes.get_last_error()
        if not handle:
            log.warning("single-instance mutex %s unavailable (error %d); starting anyway", self.name, error)
            return True
        if error == ERROR_ALREADY_EXISTS:
            _CloseHandle(handle)
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle:
            _CloseHandle(handle)


def signal_first_instance(wait_s: float = SECOND_INSTANCE_WAIT_S, poll_s: float = 0.1) -> bool:
    """Ask the running instance to open its dashboard. The first instance may still be creating its
    controller window, so a missing one is retried for a few seconds."""
    deadline = time.monotonic() + wait_s
    while True:
        if signal_running_instance():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll_s)


# ── Settings ──────────────────────────────────────────────────────────────────

def setting(settings: Any, key: str) -> Any:
    """A setting with the default the dashboard shows for it (the stored file may lack the key)."""
    spec = SETTING_SPECS.get(key)
    return settings.get(key, SETTINGS_DEFAULTS.get(key, spec.default if spec is not None else None))


def show_scoped(settings: Any) -> str:
    """The show_scoped_rows setting, with anything unknown read as "auto"."""
    value = setting(settings, "show_scoped_rows")
    return value if value in SCOPED_MODES else "auto"


# ── Text and menu (pure) ──────────────────────────────────────────────────────

def _percent_text(percent: float) -> str:
    """Whole percent, rounded the way the widgets round it."""
    return f"{int(percent + 0.5) if percent >= 0 else 0}%"


def tray_tooltip(model: WidgetModel, *, dev: bool = False) -> str:
    """"Claude usage - session 13% - weekly 2%", with the status after it when the numbers are not
    live ("... - stale") and " (dev)" for a dev instance."""
    rows = {row.key: row for row in model.rows}
    parts = ["Claude usage"]
    for key in ("session", "weekly"):
        row = rows.get(key)
        if row is not None and row.percent is not None:
            parts.append(f"{key} {_percent_text(row.percent)}")
    if len(parts) == 1 or model.status not in (ApiStatus.LIVE, ApiStatus.PAUSED):
        parts.append(model.status_text.lower())
    return " - ".join(parts) + (" (dev)" if dev else "")


def menu_items(
    *,
    floating_on: bool,
    taskbar_on: bool,
    update_version: str | None,
    open_dashboard: Callable[[], None],
    toggle_floating: Callable[[], None],
    toggle_taskbar: Callable[[], None],
    refresh: Callable[[], None],
    report_bug: Callable[[], None],
    update: Callable[[], None],
    quit_app: Callable[[], None],
) -> list[MenuItem]:
    """The tray and widget menu. "Update to vX" appears only while an update is on offer."""
    items = [
        MenuItem("Open dashboard", open_dashboard),
        SEPARATOR,
        MenuItem("Floating widget", toggle_floating, checked=floating_on),
        MenuItem("Taskbar widget", toggle_taskbar, checked=taskbar_on),
        SEPARATOR,
        MenuItem("Refresh now", refresh),
        MenuItem("Report a bug", report_bug),
    ]
    if update_version:
        items.append(MenuItem(f"Update to v{update_version}", update))
    items += [SEPARATOR, MenuItem("Exit", quit_app)]
    return items


def bug_report_url() -> str:
    return f"{GITHUB_PREFIX}{version.REPO}/issues/new?template=bug_report.md&labels=bug"


def release_url(info: Mapping[str, Any] | None) -> str:
    """The release page for an update. Only a github.com link is trusted; anything else (the value
    came off the network) falls back to the repository's latest release."""
    url = info.get("url") if info else None
    if isinstance(url, str) and url.startswith(GITHUB_PREFIX):
        return url
    return f"{GITHUB_PREFIX}{version.REPO}/releases/latest"


def open_url(url: str) -> None:
    """Open a link in the default browser without blocking the UI thread."""
    def work() -> None:
        try:
            webbrowser.open(url)
        except Exception:  # best effort: no browser is not an error worth a dialog
            log.exception("could not open %s", url)

    threading.Thread(target=work, name="open-url", daemon=True).start()


# ── Animation and clock (pure helpers and a timer-driven blinker) ─────────────

def blink_cycle(theme: Any) -> tuple[int, int] | None:
    """(lit ms, dark ms) of the theme's alive-mark blink, or None when the theme does not blink.

    A theme opts in with extras.motion.alive_blink, a sentence such as "2.4 s steps(1), on 85
    percent of the cycle". The period and the lit share are read from it, with those values as the
    defaults. Night City pulses smoothly and Gates has no animation, so neither blinks.
    """
    extras = getattr(theme, "extras", None)
    motion = extras.get("motion") if isinstance(extras, Mapping) else None
    spec = motion.get("alive_blink") if isinstance(motion, Mapping) else None
    if not isinstance(spec, str):
        return None
    seconds = re.search(r"(\d+(?:\.\d+)?)\s*s\b", spec)
    share = re.search(r"(\d+(?:\.\d+)?)\s*(?:%|percent)", spec)
    period_ms = max(400.0, float(seconds.group(1)) * 1000) if seconds else 2400.0
    lit_share = min(0.95, max(0.05, float(share.group(1)) / 100)) if share else 0.85
    lit = round(period_ms * lit_share)
    return lit, round(period_ms) - lit


def tick_delay_ms(age_seconds: float | None) -> int:
    """Milliseconds until the text on the widgets can next change.

    The age readout steps once a second for the first minute after a fetch and once a minute after
    that, so the wait ends just past the next step. The time rails and reset times need a redraw at
    least every TICK_CAP_S. With no age to show, only the cap applies.
    """
    cap = int(TICK_CAP_S * 1000)
    if age_seconds is None or age_seconds < 0:
        return cap
    step = 1.0 if age_seconds < 60 else 60.0
    wait = step - (age_seconds % step)
    return min(cap, int(wait * 1000) + 25)


class Blinker:
    """Alternates ``lit`` between True and False with one-shot timers (lit for lit_ms, dark for
    dark_ms), calling ``on_toggle(lit)`` at each change. It wakes only at the changes."""

    def __init__(self, host: Any, on_toggle: Callable[[bool], None]) -> None:
        self._host = host
        self._on_toggle = on_toggle
        self._cycle: tuple[int, int] = (2040, 360)
        self._timer: int | None = None
        self._running = False
        self.lit = True
        self.toggles = 0

    @property
    def running(self) -> bool:
        return self._running

    def start(self, cycle: tuple[int, int]) -> None:
        if self._running:
            self._cycle = cycle
            return
        self._cycle, self._running, self.lit = cycle, True, True
        self._arm()

    def stop(self) -> None:
        self._running = False
        self.lit = True
        if self._timer is not None:
            self._host.kill_timer(self._timer)
            self._timer = None

    def _arm(self) -> None:
        self._timer = self._host.set_timer(self._cycle[0] if self.lit else self._cycle[1], self._fire, repeat=False)

    def _fire(self) -> None:
        self._timer = None
        if not self._running:
            return
        self.lit = not self.lit
        self.toggles += 1
        try:
            self._on_toggle(self.lit)
        finally:
            if self._running and self._timer is None:
                self._arm()


# ── Statistics ────────────────────────────────────────────────────────────────

def analytics_store() -> Store:
    """A Store on the usage database for the API's statistics routes, separate from the data service's.
    It never creates or upgrades the file (see server.open_statistics_store): the data service does."""
    return open_statistics_store(paths.db_path())


def service_pricing(service: Any) -> Any:
    """The data service's pricing table, or None while it has none (the service loads it in its first
    cycle). DataService has no public accessor for it yet: a ``pricing`` attribute is preferred, else
    the ``_pricing`` it keeps."""
    for name in ("pricing", "_pricing"):
        found = getattr(service, name, None)
        if found is not None:
            return found
    return None


# ── Updates ───────────────────────────────────────────────────────────────────

class UpdateTracker:
    """The newest release the update check found, shared by the UI thread and the API threads."""

    def __init__(self, settings: Any, check: Callable[..., Any] | None = None) -> None:
        self._settings = settings
        self._check = check if check is not None else update_check.check_async
        self._lock = threading.Lock()
        self._info: dict[str, Any] | None = None
        self.checked = False

    @property
    def info(self) -> dict[str, Any] | None:
        """The update that was found, whether or not the user skipped it."""
        with self._lock:
            return dict(self._info) if self._info else None

    def offered(self) -> dict[str, Any] | None:
        """The update to offer in the tray menu: None when the user skipped that version."""
        info = self.info
        if info is None or info.get("version") == setting(self._settings, "skip_version"):
            return None
        return info

    def start_check(self, on_done: Callable[[dict[str, Any] | None], None] | None = None) -> None:
        """Look for a newer release on a worker thread. A check that finds nothing (or fails)
        keeps what an earlier check found."""
        self.checked = True

        def done(info: dict[str, Any] | None) -> None:
            if info:
                with self._lock:
                    self._info = dict(info)
            if on_done is not None:
                on_done(info)

        self._check(version.REPO, version.APP_VERSION, done)

    def check_now(self, timeout: float = 8.0) -> dict[str, Any]:
        """Run a check and wait for it (an API thread calls this). Returns {"available", "version"}."""
        finished = threading.Event()
        self.start_check(lambda _info: finished.set())
        finished.wait(timeout)
        info = self.info
        return {"available": info is not None, "version": info.get("version") if info else None}

    def skip(self) -> dict[str, Any]:
        """Stop offering the version that was found."""
        info = self.info
        if info is None:
            return {"skipped": None}
        self._settings.set("skip_version", str(info.get("version") or ""))
        return {"skipped": info.get("version")}


# ── Dashboard window ──────────────────────────────────────────────────────────

def post_close_to_dashboard(pid: int) -> bool:
    """Ask the dashboard window of process ``pid`` to close (WM_CLOSE), so it saves its bounds.

    The window is looked up by owner, not by title alone: a second monitor (an installed copy beside
    a dev build) can have a window with the same title, and FindWindowW names only one of them.
    """
    hwnd = dashboard_window_of(pid)
    return bool(hwnd) and bool(win32.PostMessageW(hwnd, win32.WM_CLOSE, 0, 0))


# ── The app ───────────────────────────────────────────────────────────────────

class MonitorApp:
    """The whole monitor. ``run()`` blocks until the user exits and returns the exit code.

    Every constructor argument is for tests and the smoke run: a fake data service or settings, a
    private mutex name, a fake platform, a clock, a pause check. The defaults are the real thing.
    """

    def __init__(
        self,
        *,
        dev: bool | None = None,
        settings: Settings | None = None,
        service_factory: Callable[[Settings], Any] | None = None,
        update_check_fn: Callable[..., Any] | None = None,
        open_url_fn: Callable[[str], None] | None = None,
        pause_check: Callable[[], bool] | None = None,
        platform_api: Any = plat,
        clock: Callable[[], datetime] | None = None,
        mutex: str | None = None,
        smoke: SmokeTest | None = None,
        animate_when_locked: bool = False,
    ) -> None:
        self.dev = is_dev_instance() if dev is None else dev
        self._settings_arg = settings
        pause = pause_check or platform_api.should_pause    # locked or screensaver: the data service waits
        self._service_factory = service_factory or (lambda s: DataService(s, pause_check=pause))
        self._update_check_fn = update_check_fn
        self._open_url = open_url_fn or open_url
        self._plat = platform_api
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._mutex = mutex or mutex_name(self.dev)
        self._smoke = smoke
        self._animate_when_locked = animate_when_locked

        self.settings: Any = None
        self.host: UIHost | None = None
        self.service: Any = None
        self.server: ApiServer | None = None
        self.launcher: DashboardLauncher | None = None
        self.tray: TrayIcon | None = None
        self.tooltip: Tooltip | None = None
        self.floating: FloatingWidget | None = None
        self.taskbar: TaskbarWidget | None = None
        self.updates: UpdateTracker | None = None
        self.blinker: Blinker | None = None
        self.stats = FrameStats()
        self.started = threading.Event()        # set once the message loop is running
        self.exit_code: int | None = None       # a forced exit code (the smoke run sets it)

        self._lock = threading.Lock()           # guards the state hand-off from the data thread
        self._incoming: MonitorState | None = None
        self._pull_queued = False
        self._dirty: set[str] = set()
        self._apply_queued = False
        self._state: MonitorState | None = None
        self._ctx: RenderContext | None = None
        self._resolved: tuple[Any, Any] | None = None
        self._tray_key: Any = None
        self._tick_timer: int | None = None
        self._alerts: AlertEngine | None = None     # built with the settings, in _build
        self._failures: dict[str, int] = {}
        self._torn_down = False
        self._unsubscribers: list[Callable[[], None]] = []

    # ── Lifecycle ──

    def run(self) -> int:
        """Run until Exit. Returns 0 on a normal exit (also for a second launch, which hands over)."""
        guard = SingleInstance(self._mutex)
        if not guard.acquire():
            signal_first_instance()  # not logged: the first instance owns the log file and may be rotating it
            return 0
        setup_logging()
        log.info("Claude Usage Monitor %s starting%s", version.APP_VERSION, " (dev instance)" if self.dev else "")
        try:
            return self._run()
        finally:
            guard.release()

    def quit(self, exit_code: int = 0) -> None:
        """End run(). Safe from any thread."""
        if self.host is not None:
            self.host.quit(exit_code)

    def _run(self) -> int:
        self.settings = self._settings_arg if self._settings_arg is not None else Settings()
        themes.migrate_v1(self.settings)
        self._repair_startup()
        self.host = UIHost()
        try:
            self._build()
            code = self.host.run(handle_sigint=self.dev and _has_console())
        finally:
            self._finish()
        return self.exit_code if self.exit_code is not None else code

    def _repair_startup(self) -> None:
        """An installed copy that moved or updated rewrites its sign-in entry. A dev instance never
        touches the real one, and neither does a source checkout (startup.repair_if_enabled does
        nothing unless the build is frozen), so a test run cannot repoint an installed copy."""
        if self.dev:
            return
        try:
            startup.repair_if_enabled()
        except OSError as exc:
            log.warning("could not check the startup entry: %s", exc)

    def _build(self) -> None:
        settings, host = self.settings, self.host
        assert host is not None
        self.updates = UpdateTracker(settings, self._update_check_fn)
        self._alerts = AlertEngine(settings)
        self.service = self._service_factory(settings)
        self._unsubscribers.append(self.service.subscribe(self._on_service_state))
        self.server = ApiServer(
            lambda: self.service.state,
            settings,
            refresh_cb=self.service.refresh_now,
            update_cb={
                "info": lambda: self.updates.info,
                "check": lambda: self.updates.check_now(),
                "apply": self._apply_update,
                "skip": lambda: self.updates.skip(),
            },
            version=version.APP_VERSION,
            dev=self.dev,
            analytics_store=analytics_store,
            pricing_provider=lambda: service_pricing(self.service),
        )
        self.server.start()
        self.launcher = DashboardLauncher(self.server, settings)
        self.tooltip = Tooltip(host, platform=self._plat, stats=self.stats)
        self.blinker = Blinker(host, self._on_blink)
        self.tray = TrayIcon(
            host, tray_tooltip(build_model(None, self._clock()), dev=self.dev), self.open_dashboard, self._show_menu,
        )
        self.tray.on_notify_click = self.open_dashboard
        self._sync_widgets()
        self._unsubscribers += [
            host.on_session_change(self._on_session),
            host.on_display_change(self._on_display),
            host.on_taskbar_created(self._on_display),
            host.on_settings_change(self._on_windows_setting),
            host.on_open_dashboard(self.open_dashboard),
            host.on_end_session(settings.save_now),
            settings.on_change(self._on_setting),
        ]
        # Registered last, so it runs first: before the windows and the tray remove themselves.
        host.add_cleanup(self._teardown)
        self._state = self.service.state
        self._refresh()
        if setting(settings, "check_updates"):
            self._start_update_check()
        self.service.start()
        host.post(self.started.set)
        if self._smoke is not None:
            host.post(lambda: self._smoke.start(self))

    def _finish(self) -> None:
        """After the loop (or after a failed start): stop whatever is still running and flush."""
        self._teardown()
        if self.host is not None:
            self.host.close()
        if self.settings is not None:
            self.settings.close()

    def _teardown(self) -> None:
        """Shutdown order: dashboard, data service, API server, tray, windows, settings. Runs on the
        UI thread (a host cleanup); every step is guarded so one failure does not skip the rest."""
        if self._torn_down:
            return
        self._torn_down = True
        steps: list[tuple[str, Callable[[], None]]] = [
            ("animation", self._stop_timers),
            ("dashboard", self._close_dashboard),
            ("data service", self._stop_service),
            ("api server", lambda: self.server and self.server.stop()),
            ("tray icon", lambda: self.tray and self.tray.remove()),
            ("floating widget", lambda: self.floating and self.floating.destroy()),
            ("taskbar widget", lambda: self.taskbar and self.taskbar.destroy()),
            ("tooltip", lambda: self.tooltip and self.tooltip.destroy()),
            ("settings", lambda: self.settings and self.settings.close()),
        ]
        for name, step in steps:
            try:
                step()
            except Exception:
                log.exception("shutdown step %r failed", name)
        for unsubscribe in self._unsubscribers:
            try:
                unsubscribe()
            except Exception:
                log.exception("could not unsubscribe a listener")
        self._unsubscribers.clear()
        log.info("shut down")

    def _stop_timers(self) -> None:
        if self.blinker is not None:
            self.blinker.stop()
        self._cancel_tick()

    def _stop_service(self) -> None:
        if self.service is not None:
            self.service.stop(timeout=SERVICE_STOP_S)

    def _close_dashboard(self) -> None:
        """Close the dashboard window the polite way, so it saves its size through the API server,
        then make sure the child is gone."""
        launcher = self.launcher
        pid = launcher.pid if launcher is not None else None
        if launcher is None or pid is None:
            return
        post_close_to_dashboard(pid)
        deadline = time.monotonic() + DASHBOARD_CLOSE_S
        while launcher.running and time.monotonic() < deadline:
            time.sleep(0.05)
        launcher.close()

    # ── Public actions ──

    def open_dashboard(self) -> bool:
        """Show the dashboard: focus its window, or start it."""
        if self.launcher is None:
            return False
        opened = self.launcher.open()
        if not opened:
            log.warning("the dashboard could not be opened")
        return opened

    def menu_items(self) -> list[MenuItem]:
        offered = self.updates.offered() if self.updates is not None else None
        return menu_items(
            floating_on=bool(setting(self.settings, "show_floating")),
            taskbar_on=bool(setting(self.settings, "show_taskbar")),
            update_version=str(offered.get("version")) if offered else None,
            open_dashboard=self.open_dashboard,
            toggle_floating=lambda: self._toggle("show_floating"),
            toggle_taskbar=lambda: self._toggle("show_taskbar"),
            refresh=self.refresh_now,
            report_bug=lambda: self._open_url(bug_report_url()),
            update=self._apply_update,
            quit_app=self.quit,
        )

    def refresh_now(self) -> None:
        if self.service is not None:
            self.service.refresh_now()

    def call_on_ui(self, fn: Callable[[], Any], timeout: float = UI_CALL_TIMEOUT_S) -> Any:
        """Run ``fn`` on the UI thread and return its result (for other threads; never call it from
        the UI thread)."""
        assert self.host is not None
        outcome: dict[str, Any] = {}
        finished = threading.Event()

        def work() -> None:
            try:
                outcome["value"] = fn()
            except Exception as exc:
                outcome["error"] = exc
            finally:
                finished.set()

        self.host.post(work)
        if not finished.wait(timeout):
            raise TimeoutError("the UI thread did not answer")
        if "error" in outcome:
            raise outcome["error"]
        return outcome.get("value")

    def _toggle(self, key: str) -> None:
        self.settings.set(key, not setting(self.settings, key))

    def _show_menu(self, x: int, y: int) -> None:
        if self.host is not None:
            show_menu(self.host, self.menu_items(), x, y)

    # ── Updates ──

    def _start_update_check(self) -> None:
        if self.updates is not None:
            self.updates.start_check()

    def _apply_update(self) -> dict[str, Any]:
        """Install the update that was found: silently when this is the installed build, else open
        the release page. Returns how it went (for the API)."""
        info = self.updates.info if self.updates is not None else None
        if info is None:
            return {"started": False, "mode": "none"}
        # can_self_update is the one gate: a frozen build, this project's own release download and a SHA-256 to check
        # it against. What happens after that (the checks, the helper, whatever the temp folder is called) is
        # update_check's business, and it opens the release page itself when anything is not right.
        if update_check.can_self_update(info):
            update_check.apply_update(
                info["asset_url"], digest=info.get("asset_digest"),
                on_error=self._update_failed, on_before_exit=self._prepare_exit,
            )
            return {"started": True, "mode": "installer"}
        self._open_url(release_url(info))
        return {"started": True, "mode": "browser"}

    @staticmethod
    def _update_failed(exc: BaseException) -> None:
        log.warning("the update could not be installed: %s", type(exc).__name__)

    def _prepare_exit(self) -> None:
        """Runs on the updater's thread just before it hard-exits the process: tear everything down
        on the UI thread first, so the tray icon is removed and settings are written."""
        try:
            self.call_on_ui(self._teardown, timeout=SERVICE_STOP_S + DASHBOARD_CLOSE_S + 2)
        except Exception:
            log.exception("could not shut down before the update")
        if self.settings is not None:
            self.settings.save_now()

    # ── Data and settings arriving from other threads ──

    def _on_service_state(self, state: MonitorState) -> None:
        """Data thread: park the newest state and ask the UI thread to pick it up."""
        with self._lock:
            self._incoming = state
            if self._pull_queued:
                return
            self._pull_queued = True
        if self.host is not None:
            self.host.post(self._pull_state)

    def _pull_state(self) -> None:
        with self._lock:
            state, self._incoming = self._incoming, None
            self._pull_queued = False
        if state is None or self._torn_down:
            return
        self._state = state
        self._refresh()
        self._raise_alerts(state)

    def _on_setting(self, key: str, _value: Any) -> None:
        """Settings listener, on whichever thread changed the setting."""
        if key.startswith("_") or key not in (THEME_KEYS | WIDGET_KEYS | LOOK_KEYS | {"check_updates"}):
            return
        with self._lock:
            self._dirty.add(key)
            if self._apply_queued:
                return
            self._apply_queued = True
        if self.host is not None:
            self.host.post(self._apply_settings)

    def _apply_settings(self) -> None:
        with self._lock:
            keys, self._dirty = self._dirty, set()
            self._apply_queued = False
        if self._torn_down or not keys:
            return
        if keys & THEME_KEYS:
            self._resolved = None
        if keys & WIDGET_KEYS:
            self._sync_widgets()
        if "check_updates" in keys and setting(self.settings, "check_updates") and not self.updates.checked:
            self._start_update_check()
        self._refresh()

    # ── Windows' own notifications ──

    def _on_session(self, locked: bool) -> None:
        """Lock stops the animation and the clock; unlock redraws, re-places and asks for fresh data."""
        if locked:
            self._stop_timers()
            return
        self._resolved = None
        self.refresh_now()
        self._refresh()

    def _on_display(self) -> None:
        """The display layout or the taskbar changed: place the widgets again."""
        self._refresh()

    def _on_windows_setting(self, area: str) -> None:
        """WM_SETTINGCHANGE: a moved work area or taskbar re-places the widgets, and a colour change
        (ImmersiveColorSet) may change the Gates system accent."""
        if area == "app":
            return
        if area == "ImmersiveColorSet":
            self._resolved = None
        self._refresh()

    # ── Widgets ──

    def _sync_widgets(self) -> None:
        """Create or destroy the widgets to match show_floating and show_taskbar."""
        host = self.host
        assert host is not None
        want_floating = bool(setting(self.settings, "show_floating"))
        want_taskbar = bool(setting(self.settings, "show_taskbar"))
        if want_floating and self.floating is None:
            self.floating = FloatingWidget(
                host,
                get_position=lambda: self.settings.get("position"),
                set_position=lambda x, y: self.settings.set("position", {"x": x, "y": y}),
                on_open=self.open_dashboard,
                on_menu=self._show_menu,
                tooltip=self.tooltip,
                on_visibility=self._sync_animation,
                platform=self._plat,
                stats=self.stats,
            )
        elif not want_floating and self.floating is not None:
            self.floating.destroy()
            self.floating = None
        if want_taskbar and self.taskbar is None:
            self.taskbar = TaskbarWidget(
                host,
                on_open=self.open_dashboard,
                on_menu=self._show_menu,
                tooltip=self.tooltip,
                on_visibility=self._sync_animation,
                platform=self._plat,
                stats=self.stats,
            )
        elif not want_taskbar and self.taskbar is not None:
            self.taskbar.destroy()
            self.taskbar = None

    # ── Drawing ──

    def _theme(self) -> tuple[Any, Any]:
        if self._resolved is None:
            self._resolved = themes.resolve(self.settings)
        return self._resolved

    def _build_context(self) -> RenderContext:
        settings = self.settings
        theme, variant = self._theme()
        reduce_motion = bool(setting(settings, "reduce_motion"))
        lit = True if reduce_motion or self.blinker is None else self.blinker.lit
        model = build_model(self._state, self._clock(), show_scoped=show_scoped(settings), blink_on=lit)
        return RenderContext(
            model=model,
            theme=theme,
            variant=variant,
            skin=get_skin(theme.id),
            floating=RenderOptions(
                transparent_bg=bool(setting(settings, "float_transparent_bg")),
                text_fx=bool(setting(settings, "float_text_fx")),
                reduce_motion=reduce_motion,
            ),
            taskbar=RenderOptions(
                transparent_bg=bool(setting(settings, "taskbar_transparent_bg")),
                text_fx=bool(setting(settings, "taskbar_text_fx")),
                reduce_motion=reduce_motion,
            ),
            hide_floating_in_fullscreen=bool(setting(settings, "hide_floating_in_fullscreen")),
        )

    def _refresh(self) -> None:
        """Rebuild the context from the latest state, settings and clock, and redraw every surface.
        Unchanged surfaces cost almost nothing, so this is safe to call often."""
        if self._torn_down or self.host is None:
            return
        try:
            ctx = self._build_context()
        except Exception:
            self._failed("context")
            return
        self._show(ctx)
        self._schedule_tick()

    def _show(self, ctx: RenderContext) -> None:
        """Draw ``ctx`` on every surface. A surface that fails to draw is logged and does not stop the
        rest. The blink redraws through here with the context it already has (only the mark's phase
        differs), which is why a toggle finds both frames in the surfaces' caches."""
        self._ctx = ctx
        draws: list[tuple[str, Callable[[], None]]] = [
            ("floating", lambda: self.floating and self.floating.render(ctx)),
            ("taskbar", lambda: self.taskbar and self.taskbar.render(ctx)),
            ("tooltip", lambda: self.tooltip and self.tooltip.update(ctx)),
            ("tray", lambda: self._update_tray(ctx)),
        ]
        for name, draw in draws:
            try:
                draw()
            except Exception:
                self._failed(name)
        self._sync_animation()

    def _failed(self, name: str) -> None:
        """Log a drawing failure: the first, then every hundredth, so a timer cannot flood the log."""
        count = self._failures[name] = self._failures.get(name, 0) + 1
        if count == 1 or count % 100 == 0:
            log.exception("drawing the %s failed (%d so far)", name, count)

    def _update_tray(self, ctx: RenderContext) -> None:
        tray = self.tray
        if tray is None:
            return
        first = ctx.model.rows[0] if ctx.model.rows else None
        key = (ctx.theme.id, ctx.variant, ctx.model.stale, first.percent if first else None,
               first.severity if first else None)
        if key != self._tray_key:
            images = {size: ctx.skin.render_tray_icon(ctx.model, ctx.theme, ctx.variant, size) for size in TRAY_SIZES}
            tray.set_images(images)
            self._tray_key = key
            self.stats.rendered["tray"] += 1
        tray.set_tooltip(tray_tooltip(ctx.model, dev=self.dev))

    def _raise_alerts(self, state: MonitorState) -> None:
        """Show a tray toast for each alert the engine finds in ``state``. UI thread. Nothing in here
        raises: a failing check or toast must not cost the redraw that has already happened."""
        engine, tray = self._alerts, self.tray
        if engine is None or tray is None:
            return
        try:
            alerts = engine.evaluate(state, suppressed=self._fullscreen())
        except Exception:  # the engine promises not to raise; this path does not depend on the promise
            log.exception("alert evaluation failed")
            return
        for alert in alerts:
            try:
                tray.notify(alert.title, alert.message)
            except Exception:
                log.exception("could not show a %s alert", alert.kind)

    def _fullscreen(self) -> bool:
        """Whether a fullscreen app has the foreground, when a toast would land on top of it."""
        try:
            return bool(self._plat.is_fullscreen_foreground())
        except Exception:  # an unreadable foreground is no reason to lose an alert
            log.debug("could not tell whether a fullscreen app is in front", exc_info=True)
            return False

    # ── Animation and the clock ──

    def _widgets_visible(self) -> bool:
        return any(w is not None and w.visible for w in (self.floating, self.taskbar))

    def _animation_allowed(self) -> bool:
        return self._animate_when_locked or not (self.host is not None and self.host.locked)

    def _wants_blink(self) -> bool:
        ctx = self._ctx
        if ctx is None or not self._animation_allowed() or ctx.model.status is not ApiStatus.LIVE:
            return False
        return (not ctx.floating.reduce_motion and blink_cycle(ctx.theme) is not None
                and self._widgets_visible())

    def _sync_animation(self) -> None:
        """Start the blinker when a visible widget has a blinking mark to show, stop it otherwise."""
        blinker, ctx = self.blinker, self._ctx
        if blinker is None or ctx is None:
            return
        cycle = blink_cycle(ctx.theme) if self._wants_blink() else None
        if cycle is not None:
            blinker.start(cycle)
        elif blinker.running:
            blinker.stop()
            self._show(ctx.with_blink(True))  # back to the steady, lit frame

    def _on_blink(self, lit: bool) -> None:
        if self._ctx is not None and not self._torn_down:
            self._show(self._ctx.with_blink(lit))

    def _schedule_tick(self) -> None:
        self._cancel_tick()
        if self._torn_down or self.host is None or not self._animation_allowed():
            return
        self._tick_timer = self.host.set_timer(tick_delay_ms(self._age_seconds()), self._on_tick, repeat=False)

    def _cancel_tick(self) -> None:
        if self._tick_timer is not None and self.host is not None:
            self.host.kill_timer(self._tick_timer)
        self._tick_timer = None

    def _on_tick(self) -> None:
        self._tick_timer = None
        self._refresh()

    def _age_seconds(self) -> float | None:
        fetched = self._state.usage_fetched_at if self._state is not None else None
        if fetched is None or self._ctx is None or self._ctx.model.age_text is None:
            return None
        return (self._clock() - fetched).total_seconds()

    # ── Diagnostics ──

    def describe(self) -> dict[str, Any]:
        """A snapshot of the live UI for the smoke report. UI thread only."""
        def window_info(widget: Any) -> dict[str, Any] | None:
            if widget is None:
                return None
            window = widget.window
            return {"visible": bool(window.visible), "rect": list(window.rect), "dpi": window.dpi}

        theme = self._resolved
        return {
            "locked": bool(self.host and self.host.locked),
            "theme": [theme[0].id, theme[1].id] if theme else None,
            "floating": window_info(self.floating),
            "taskbar": window_info(self.taskbar),
            "tray": {"added": bool(self.tray and self.tray.added), "icon_px": self.tray.icon_size if self.tray else 0},
            "tooltip_visible": bool(self.tooltip and self.tooltip.visible),
            "frames": self.stats.snapshot(),
            "blink": {"running": bool(self.blinker and self.blinker.running),
                      "toggles": self.blinker.toggles if self.blinker else 0},
            "gdi_objects": win32.GetGuiResources(win32.GetCurrentProcess(), win32.GR_GDIOBJECTS),
            "user_objects": win32.GetGuiResources(win32.GetCurrentProcess(), win32.GR_USEROBJECTS),
            "ui_thread_cpu_s": round(time.thread_time(), 3),
        }


def _has_console() -> bool:
    stream = sys.stderr
    return bool(stream is not None and getattr(stream, "isatty", lambda: False)())


# ── The smoke run ─────────────────────────────────────────────────────────────

class _Collector(logging.Handler):
    """Keeps the warnings and errors the app logs, and counts the server's request lines."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.requests = 0
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = f"{record.name}: {record.getMessage()}"
            if record.exc_info and record.exc_info[0] is not None:
                text += f" [{record.exc_info[0].__name__}: {str(record.exc_info[1])[:200]}]"
            with self._lock:
                if record.levelno >= logging.ERROR:
                    self.errors.append(text[:600])
                elif record.levelno >= logging.WARNING:
                    self.warnings.append(text[:600])
                elif record.name.endswith("ui.server") and record.getMessage().startswith("api "):
                    self.requests += 1
        except Exception:  # a logging handler must never raise into the code that logged
            self.handleError(record)

    def request_count(self) -> int:
        with self._lock:
            return self.requests


class SmokeTest:
    """``main.py --smoke-test SECONDS REPORT.json``: run the real app, exercise it, write a report.

    A probe thread starts once the message loop runs. It reads the page and /api/state through the
    real server with the per-run token, changes settings the way the dashboard does (theme switches,
    widget toggles), opens the dashboard child for about five seconds and closes it, reads every
    statistics route and both exports, and takes snapshots of the live windows. The report holds
    counts, ports, statuses, sizes and error text, never usage content or tokens. The exit code is 0
    when no errors were recorded.
    """

    DASHBOARD_OPEN_S = 5.0
    DASHBOARD_CLOSE_WAIT_S = 6.0
    DATABASE_WAIT_S = 20.0
    # The routes the analytics step reads and the top-level keys each JSON answer must have.
    ANALYTICS_ROUTES: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("/api/analytics/cost", ("windows", "plan", "value", "monthly", "breakdowns")),
        ("/api/analytics/efficiency", ("cache", "thinking", "tools", "productivity")),
        ("/api/analytics/pacing", ("session", "weekly", "scoped")),
        ("/api/analytics/history", ("days", "hour_weekday", "streaks", "odometer")),
        ("/api/analytics/sessions", ("sessions",)),
        ("/api/export?format=json", ("rows",)),
        ("/api/export?format=csv", ()),             # a download, not JSON: only the status and size count
    )

    def __init__(self, seconds: float, report_path: str | Path) -> None:
        self.seconds = max(5.0, float(seconds))
        self.report_path = Path(report_path)
        self.collector = _Collector()
        self.report: dict[str, Any] = {"ok": False, "steps": {}, "errors": [], "warnings": []}
        self.began = False
        self._app: MonitorApp | None = None
        self._t0 = 0.0
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    # Wiring

    def install(self) -> None:
        """Start collecting log records. Called before the app starts."""
        root = logging.getLogger("usage_monitor")
        root.addHandler(self.collector)
        logging.getLogger("usage_monitor.ui.server").setLevel(logging.DEBUG)

    def start(self, app: MonitorApp) -> None:
        """Called on the UI thread when the loop is running: start the probe thread."""
        self._app = app
        self.began = True
        self._t0 = time.monotonic()
        threading.Thread(target=self._probe, name="smoke-probe", daemon=True).start()

    def exit_code(self, run_code: int) -> int:
        """The process exit code, after the report is written (see finish)."""
        return 1 if (self.report["errors"] or not self.report["ok"]) else run_code

    def finish(self, run_code: int) -> int:
        """Write the report for a run that ended (or never began) and return the exit code."""
        if not self.began:
            self.report["errors"].append("the app never started its loop (another dev instance running?)")
        self._merge_logs()
        self.report["ok"] = self.began and not self.report["errors"]
        self.report["run_exit_code"] = run_code
        self._write()
        return self.exit_code(run_code)

    # The probe

    def _probe(self) -> None:
        app = self._app
        assert app is not None
        schedule = [
            (1.5, "api", self._step_api),
            (3.0, "windows", self._step_windows),
            (5.0, "live_settings", self._step_live_settings),
            (13.0, "dashboard", self._step_dashboard),
            (26.0, "analytics", self._step_analytics),
        ]
        try:
            for offset, name, step in schedule:
                if offset + 2 > self.seconds:
                    self.report["steps"][name] = "skipped: the run is too short"
                    continue
                self._sleep_until(offset)
                self._step(name, step)
            self._poll_until(self.seconds - 1.5)
            self._step("final", self._step_final)
        except Exception as exc:  # the probe itself must still end the run
            self.report["errors"].append(f"probe crashed: {type(exc).__name__}: {exc}"[:300])
        finally:
            self._merge_logs()
            self.report["ok"] = not self.report["errors"]
            self.report["run_exit_code"] = 0
            self.report["seconds_run"] = round(time.monotonic() - self._t0, 1)
            self._write()
            app.quit(0 if self.report["ok"] else 1)

    def _sleep_until(self, offset: float) -> None:
        wait = self._t0 + offset - time.monotonic()
        if wait > 0:
            time.sleep(wait)

    def _poll_until(self, offset: float) -> None:
        """Read /api/state every five seconds until ``offset`` seconds into the run."""
        polls = self.report.setdefault("state_polls", [])
        while time.monotonic() - self._t0 < offset:
            self._sleep_until(min(offset, time.monotonic() - self._t0 + 5.0))
            try:
                polls.append(self._state_summary(self._get_json("/api/state")))
            except Exception as exc:
                self.report["errors"].append(f"state poll failed: {type(exc).__name__}: {exc}"[:300])
                return

    def _step(self, name: str, fn: Callable[[], dict[str, Any] | None]) -> None:
        began = time.monotonic()
        try:
            self.report["steps"][name] = {"result": fn() or {}, "seconds": round(time.monotonic() - began, 2)}
        except Exception as exc:
            message = f"step {name} failed: {type(exc).__name__}: {exc}"[:400]
            self.report["steps"][name] = {"error": message}
            self.report["errors"].append(message)

    # Talking to the real server

    def _call(self, method: str, path: str, *, body: Any = None, token: bool = True) -> tuple[int, bytes]:
        app = self._app
        assert app is not None and app.server is not None
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"X-Monitor-Token": app.server.token} if token else {}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(f"http://127.0.0.1:{app.server.port}{path}", data=data,
                                         method=method, headers=headers)
        try:
            with self._opener.open(request, timeout=10) as response:
                return response.status, response.read(1024 * 1024)
        except urllib.error.HTTPError as err:
            return err.code, err.read(65536)

    def _get_json(self, path: str) -> dict[str, Any]:
        status, body = self._call("GET", path)
        if status != 200:
            raise RuntimeError(f"GET {path} answered {status}")
        return json.loads(body.decode("utf-8"))

    def _post(self, path: str, body: Any) -> dict[str, Any]:
        status, raw = self._call("POST", path, body=body)
        if status != 200:
            raise RuntimeError(f"POST {path} answered {status}")
        return json.loads(raw.decode("utf-8"))

    @staticmethod
    def _state_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
        """Numbers and words only: the API status, the two limits, the theme and the ingest counts."""
        state = payload.get("state") or {}
        usage = state.get("usage") or {}
        limits = {item.get("kind"): item.get("percent") for item in usage.get("limits", []) if isinstance(item, dict)}
        ingest = state.get("ingest") or {}
        theme = payload.get("theme") or {}
        return {
            "status": state.get("status"),
            "session_pct": limits.get("session"),
            "weekly_pct": limits.get("weekly_all"),
            "theme": [theme.get("id"), theme.get("variant")],
            "ingest_files": [ingest.get("files_done"), ingest.get("files_total")],
            "ingest_running": ingest.get("running"),
            "ingest_error": ingest.get("last_error"),
            "update_available": (payload.get("update") or {}).get("available"),
        }

    def _ui(self) -> dict[str, Any]:
        app = self._app
        assert app is not None
        return app.call_on_ui(app.describe)

    # Steps

    def _step_api(self) -> dict[str, Any]:
        app = self._app
        assert app is not None and app.server is not None
        status, page = self._call("GET", "/", token=False)
        token_in_page = app.server.token.encode("ascii") in page
        unauthorised, _ = self._call("GET", "/api/state", token=False)
        payload = self._get_json("/api/state")
        themes_payload = self._get_json("/api/themes")
        refreshed = self._post("/api/refresh", {})
        if status != 200 or not token_in_page:
            raise RuntimeError("the page did not load with the monitor token")
        if unauthorised != 401:
            raise RuntimeError(f"/api/state without the token answered {unauthorised}, not 401")
        for key in ("state", "settings", "theme", "version", "update", "dev"):
            if key not in payload:
                raise RuntimeError(f"/api/state has no {key!r}")
        self.report["server_port"] = app.server.port
        self.report["api_status"] = self._state_summary(payload)["status"]
        self.report["version"] = payload["version"]
        return {
            "index_status": status,
            "token_in_page": token_in_page,
            "unauthorised_status": unauthorised,
            "state": self._state_summary(payload),
            "themes": [item.get("id") for item in themes_payload.get("themes", [])],
            "refresh_ok": bool(refreshed.get("ok")),
        }

    def _step_windows(self) -> dict[str, Any]:
        app = self._app
        assert app is not None
        before = self._ui()
        shown = app.call_on_ui(self._show_tooltip)
        after = self._ui()
        if before["floating"] is not None and not before["floating"]["visible"]:
            raise RuntimeError("the floating widget is not visible")
        if before["tray"]["added"] is not True:
            raise RuntimeError("the tray icon was not added")
        self.report["windows"] = before
        return {"tooltip_shown": shown, "frames_before": before["frames"], "frames_after": after["frames"]}

    def _show_tooltip(self) -> bool:
        """Show the tooltip the way a hover dwell does, then hide it again."""
        app = self._app
        assert app is not None and app.tooltip is not None
        widget = app.floating or app.taskbar
        if widget is None:
            return False
        widget.show_tooltip()
        visible = app.tooltip.visible
        widget.hide_tooltip()
        return visible

    def _step_live_settings(self) -> dict[str, Any]:
        """Switch themes and toggle widgets through the API, and check that the UI follows live."""
        original = self._get_json("/api/state")["settings"]
        wanted = {key: original[key] for key in ("theme_id", "theme_variant", "theme_mode", "show_taskbar",
                                                  "reduce_motion") if key in original}
        seen: dict[str, Any] = {}
        try:
            for label, change in (
                ("night_city", {"theme_id": "night_city", "theme_variant": "blackwall"}),
                ("gates", {"theme_id": "gates", "theme_variant": "blue", "theme_mode": "dark"}),
                ("nostromo", {"theme_id": "nostromo", "theme_variant": "amber"}),
                ("taskbar_off", {"show_taskbar": False}),
                ("taskbar_on", {"show_taskbar": True}),
                ("reduce_motion_on", {"reduce_motion": True}),
                ("reduce_motion_off", {"reduce_motion": False}),
            ):
                self._post("/api/settings", change)
                time.sleep(0.8)
                seen[label] = self._ui()
        finally:
            self._post("/api/settings", wanted)  # leave the dev settings as they were
        expect = {"night_city": "night_city", "gates": "gates", "nostromo": "nostromo"}
        for label, theme_id in expect.items():
            if (seen[label]["theme"] or [None])[0] != theme_id:
                raise RuntimeError(f"the UI did not switch to {theme_id}")
        if seen["taskbar_off"]["taskbar"] is not None or seen["taskbar_on"]["taskbar"] is None:
            raise RuntimeError("the taskbar widget did not follow show_taskbar")
        return {label: {"theme": snap["theme"], "taskbar": snap["taskbar"] is not None,
                        "frames": snap["frames"]["rendered"]} for label, snap in seen.items()}

    def _step_dashboard(self) -> dict[str, Any]:
        app = self._app
        assert app is not None and app.launcher is not None
        launcher = app.launcher
        before = self.collector.request_count()
        opened = launcher.open()
        began = time.monotonic()
        pid = launcher.pid
        time.sleep(self.DASHBOARD_OPEN_S)
        alive = launcher.running
        requests = self.collector.request_count() - before
        closed_politely = bool(pid and post_close_to_dashboard(pid))
        exited = self._wait_for(lambda: not launcher.running, self.DASHBOARD_CLOSE_WAIT_S)
        if not exited:
            launcher.close()
        bounds = self._get_json("/api/state")["settings"].get("dashboard_bounds")
        result = {
            "opened": opened, "alive_after_5s": alive, "requests_while_open": requests,
            "close_posted": closed_politely, "exited_after_close": exited,
            "bounds_saved": bounds is not None, "open_seconds": round(time.monotonic() - began, 1),
        }
        if not (opened and alive):
            raise RuntimeError(f"the dashboard did not stay open: {result}")
        if requests < 1:
            raise RuntimeError(f"the dashboard window never loaded the page: {result}")
        if not exited:
            raise RuntimeError(f"the dashboard did not close when asked: {result}")
        return result

    def _step_analytics(self) -> dict[str, Any]:
        """Read every statistics route and both downloads as the dashboard does, and check their shape.

        Nothing but statuses, sizes and timings goes into the report.
        """
        self._wait_for(self._statistics_ready, self.DATABASE_WAIT_S)
        results: dict[str, Any] = {}
        problems: list[str] = []
        for route, keys in self.ANALYTICS_ROUTES:
            began = time.monotonic()
            status, body = self._call("GET", route)
            results[route] = {"status": status, "bytes": len(body),
                              "ms": round((time.monotonic() - began) * 1000)}
            if status != 200:
                problems.append(f"GET {route} answered {status}")
            elif keys:
                problem = self._shape_problem(route, body, keys)
                if problem:
                    problems.append(problem)
        if problems:
            raise RuntimeError("; ".join(problems))
        return results

    def _statistics_ready(self) -> bool:
        """Whether the statistics answer yet. The data service makes and upgrades the database in its first
        cycle, and until then the routes say 503 (they never create it themselves)."""
        try:
            return self._call("GET", "/api/analytics/pacing")[0] == 200
        except OSError:
            return False

    @staticmethod
    def _shape_problem(route: str, body: bytes, keys: tuple[str, ...]) -> str | None:
        try:
            payload = json.loads(body.decode("utf-8"))
        except ValueError:
            return f"GET {route} did not answer JSON"
        missing = [key for key in keys if not isinstance(payload, dict) or key not in payload]
        return f"GET {route} lacks {', '.join(missing)}" if missing else None

    @staticmethod
    def _wait_for(done: Callable[[], bool], timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if done():
                return True
            time.sleep(0.1)
        return bool(done())

    def _step_final(self) -> dict[str, Any]:
        app = self._app
        assert app is not None
        final = self._ui()
        first = self.report.get("windows", {})
        self.report["final_windows"] = final
        elapsed = time.monotonic() - self._t0
        self.report["ui_thread_cpu_percent"] = round(100 * final["ui_thread_cpu_s"] / max(elapsed, 1.0), 2)
        self.report["gdi_objects"] = [first.get("gdi_objects"), final["gdi_objects"]]
        self.report["user_objects"] = [first.get("user_objects"), final["user_objects"]]
        self.report["frames"] = final["frames"]
        self.report["blink_toggles"] = final["blink"]["toggles"]
        service_error = (app.service.state.ingest.last_error if app.service and app.service.state else None)
        if service_error:
            self.report["warnings"].append(f"data service: {service_error}"[:300])
        return {}

    # Report

    def _merge_logs(self) -> None:
        for text in self.collector.errors:
            if text not in self.report["errors"]:
                self.report["errors"].append(text)
        self.report["warnings"] = list(dict.fromkeys(self.report["warnings"] + self.collector.warnings))[:50]

    def _write(self) -> None:
        self.report.setdefault("python", sys.version.split()[0])
        self.report.setdefault("dev", True)
        self.report["seconds_requested"] = self.seconds
        try:
            self.report_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.report_path.with_name(self.report_path.name + ".tmp")
            tmp.write_text(json.dumps(self.report, indent=2, default=str), encoding="utf-8")
            os.replace(tmp, self.report_path)
        except OSError as exc:
            log.error("could not write the smoke report: %s", exc)


def run_smoke_test(seconds: float, report_path: str | Path) -> int:
    """Run the app for ``seconds`` under SmokeTest. Returns the exit code (0 = clean).

    The run is always a dev instance, whatever the environment says: it takes the dev mutex and
    controller window (an installed copy can run beside it) and never touches the sign-in entry.
    """
    smoke = SmokeTest(seconds, report_path)
    smoke.install()
    os.environ[DEV_ENV] = "1"   # the controller window class and the dashboard child read it too
    code = 1
    try:
        # An unattended run is often on a locked desktop, where the real pause check would keep the
        # data service idle all along. The run must exercise the data path, so it never pauses.
        code = MonitorApp(dev=True, smoke=smoke, animate_when_locked=True, pause_check=lambda: False).run()
    except Exception as exc:
        smoke.report["errors"].append(f"the app crashed: {type(exc).__name__}: {exc}"[:400])
        log.exception("smoke test: the app crashed")
    return smoke.finish(code)


__all__ = [
    "Blinker",
    "MonitorApp",
    "SingleInstance",
    "SmokeTest",
    "UpdateTracker",
    "analytics_store",
    "blink_cycle",
    "menu_items",
    "mutex_name",
    "run_smoke_test",
    "service_pricing",
    "tick_delay_ms",
    "tray_tooltip",
]
