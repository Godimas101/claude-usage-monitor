"""Tests for the app layer: main.py, usage_monitor/app.py and usage_monitor/ui/widgets.py.

Most of it runs on fakes: a fake host with hand-fired timers, fake layered windows, a fake platform
(monitors, taskbar, fullscreen) and a fake skin, so placement, wiring, shutdown order and the
animation timers are checked without a desktop. Tests that create real windows, tray icons or
processes are skipped unless CUM_UI_TESTS=1 (CI has no interactive desktop). Those park every
widget off-screen, tag the tray tooltip "(dev)", use a fake data service and never open the
dashboard or touch the real settings, mutex or controller window.
"""
from __future__ import annotations

import collections
import dataclasses
import functools
import json
import logging
import os
import re
import sqlite3
import sys
import tempfile
import threading
import time
import types
import urllib.error
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

if sys.platform != "win32":
    pytest.skip("the monitor's UI layer is Windows only", allow_module_level=True)

import main as entry
import usage_monitor.app as app_mod
import version
from usage_monitor import themes
from usage_monitor.data.alerts import AlertEngine
from usage_monitor.data.store import Store
from usage_monitor.settings import DEFAULTS as SETTINGS_DEFAULTS
from usage_monitor.app import (
    Blinker,
    MonitorApp,
    SingleInstance,
    SmokeTest,
    UpdateTracker,
    blink_cycle,
    bug_report_url,
    is_dev_instance,
    menu_items,
    mutex_name,
    release_url,
    tick_delay_ms,
    tray_tooltip,
)
from usage_monitor.data import models as m
from usage_monitor.render.dump import sample_model, sample_state
from usage_monitor.render.model import build_model
from usage_monitor.render.skins import RenderOptions
from usage_monitor.settings import Settings
from usage_monitor.ui import platform as plat
from usage_monitor.ui import win32
from usage_monitor.ui.host import invoke
from usage_monitor.ui.platform import Rect, TaskbarInfo
from usage_monitor.ui.widgets import (
    FloatingWidget,
    FrameCache,
    RenderContext,
    TaskbarWidget,
    Tooltip,
    floating_origin,
    saved_point,
    scale_for,
    steady,
    strip_origin,
    strip_supported,
    tooltip_origin,
)

UI = pytest.mark.skipif(
    os.environ.get("CUM_UI_TESTS") != "1", reason="needs an interactive desktop: set CUM_UI_TESTS=1",
)
NOW = datetime(2026, 10, 21, 20, 12, tzinfo=timezone.utc)


# ── Isolation ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Nothing here may reach the real app data, the real mutex or the installed copy's window."""
    app_dir = tmp_path / "app"
    monkeypatch.setenv("CUM_APPDATA_DIR", str(app_dir))
    monkeypatch.setenv("CUM_DEV_INSTANCE", "1")   # the dev controller class: never the installed copy's window
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    return app_dir


@pytest.fixture(autouse=True)
def clean_logging():
    """setup_logging() attaches a file handler to the package logger; do not leave it behind."""
    logger = logging.getLogger("usage_monitor")
    before, level = list(logger.handlers), logger.level
    yield
    for handler in list(logger.handlers):
        if handler not in before:
            logger.removeHandler(handler)
            handler.close()
    logger.setLevel(level)


@pytest.fixture
def settings(tmp_path: Path):
    s = Settings(tmp_path / "settings.json", save_delay_s=0.0)
    yield s
    s.close()


# ── Fakes ─────────────────────────────────────────────────────────────────────

class FakeHost:
    """Stands in for UIHost: timers fire when the test says so, posted calls wait to be run."""

    def __init__(self) -> None:
        self.locked = False
        self.timers: dict[int, tuple[int, Any, bool]] = {}
        self.posted: list[Any] = []
        self.cleanups: list[Any] = []
        self.listeners: dict[str, list[Any]] = collections.defaultdict(list)
        self._ids = iter(range(1, 10_000))
        self.hwnd = 1

    def on_ui_thread(self) -> bool:
        return True

    def set_timer(self, ms: int, fn: Any, *, repeat: bool = True) -> int:
        timer_id = next(self._ids)
        self.timers[timer_id] = (ms, fn, repeat)
        return timer_id

    def kill_timer(self, timer_id: int) -> None:
        self.timers.pop(timer_id, None)

    def fire(self, timer_id: int) -> None:
        ms, fn, repeat = self.timers[timer_id]
        if not repeat:
            del self.timers[timer_id]
        fn()

    def timer_ms(self) -> list[int]:
        return sorted(ms for ms, _fn, _repeat in self.timers.values())

    def post(self, fn: Any) -> None:
        self.posted.append(fn)

    def run_posted(self) -> int:
        count = 0
        while self.posted:
            self.posted.pop(0)()
            count += 1
        return count

    def add_cleanup(self, fn: Any) -> Any:
        self.cleanups.append(fn)
        return lambda: self.cleanups.remove(fn)

    def _subscribe(self, kind: str, cb: Any) -> Any:
        self.listeners[kind].append(cb)
        return lambda: self.listeners[kind].remove(cb)

    def on_session_change(self, cb: Any) -> Any:
        return self._subscribe("session", cb)

    def on_display_change(self, cb: Any) -> Any:
        return self._subscribe("display", cb)

    def on_taskbar_created(self, cb: Any) -> Any:
        return self._subscribe("taskbar", cb)

    def on_settings_change(self, cb: Any) -> Any:
        return self._subscribe("settings", cb)

    def on_open_dashboard(self, cb: Any) -> Any:
        return self._subscribe("open_dashboard", cb)

    def on_end_session(self, cb: Any) -> Any:
        return self._subscribe("end_session", cb)

    def emit(self, kind: str, *args: Any) -> None:
        for cb in list(self.listeners[kind]):
            cb(*args)

    def quit(self, exit_code: int = 0) -> None:
        self.posted.append(lambda: [c() for c in reversed(list(self.cleanups))])


class FakeWindow:
    """A layered window that only remembers what it was asked to do."""

    def __init__(self, host: Any = None, *, draggable: bool, topmost: bool = True) -> None:
        self.draggable = draggable
        self.visible = False
        self.dpi = 96
        self.dragging = False
        self.hwnd = 1
        self.rect = Rect(0, 0, 0, 0)
        self.updates: list[tuple[tuple[int, int], int, int]] = []
        self.moves: list[tuple[int, int]] = []
        self.raised = 0
        self.click_through = False
        self.destroyed = False
        self.on_click = self.on_double_click = self.on_right_click = self.on_drag_end = None
        self.on_hover = self.on_hover_dwell = self.on_dpi_changed = None

    def fire(self, name: str, *args: Any) -> Any:
        """Call one of the on_* callbacks as LayeredWindow does: with as many arguments as it accepts."""
        callback = getattr(self, name)
        assert callback is not None, f"nothing is listening to {name}"
        return invoke(callback, *args)

    def update(self, image: Image.Image, x: int | None = None, y: int | None = None) -> bool:
        if x is None or y is None:
            x, y = self.rect.left, self.rect.top
        self.rect = Rect(x, y, x + image.width, y + image.height)
        self.updates.append((image.size, x, y))
        return True

    def move(self, x: int, y: int) -> None:
        self.rect = Rect(x, y, x + self.rect.width, y + self.rect.height)
        self.moves.append((x, y))

    def show(self, image: Image.Image | None = None, x: int | None = None, y: int | None = None) -> None:
        self.visible = True

    def hide(self) -> None:
        self.visible = False

    def bring_to_top(self) -> None:
        self.raised += 1

    def set_click_through(self, on: bool) -> None:
        self.click_through = on

    def destroy(self) -> None:
        self.destroyed = True
        self.visible = False


class WindowFactory:
    """Creates FakeWindows and keeps them, so a test can reach the one a widget made."""

    def __init__(self) -> None:
        self.windows: list[FakeWindow] = []

    def __call__(self, host: Any, *, draggable: bool, topmost: bool = True) -> FakeWindow:
        window = FakeWindow(host, draggable=draggable, topmost=topmost)
        self.windows.append(window)
        return window


class FakePlatform:
    """A 1920x1080 screen with the taskbar at the bottom, 100 % DPI, nothing fullscreen."""

    def __init__(self) -> None:
        self.areas = [Rect(0, 0, 1920, 1032)]
        self.dpi = 96
        self.fullscreen = False
        self.cursor: tuple[int, int] | None = None
        self.info: TaskbarInfo | None = taskbar(self.dpi)

    def work_areas(self) -> list[Rect]:
        return list(self.areas)

    def work_area_at(self, x: int, y: int) -> Rect | None:
        return plat.nearest_rect(x, y, self.areas)

    def dpi_for_point(self, x: int, y: int) -> int:
        return self.dpi

    def taskbar_info(self) -> TaskbarInfo | None:
        return self.info

    def is_fullscreen_foreground(self) -> bool:
        return self.fullscreen

    def cursor_pos(self) -> tuple[int, int] | None:
        return self.cursor

    def should_pause(self) -> bool:
        return False


def taskbar(dpi: int = 96, *, edge: str = "bottom", autohide: bool = False, tray: bool = True) -> TaskbarInfo:
    scale = dpi / 96
    height = round(48 * scale)
    return TaskbarInfo(
        hwnd=1,
        rect=Rect(0, 1080 - height, 1920, 1080),
        edge=edge,
        tray_rect=Rect(1694, 1080 - height, 1920, 1080) if tray else None,
        monitor_rect=Rect(0, 0, 1920, 1080),
        work_area=Rect(0, 0, 1920, 1080 - height),
        dpi=dpi,
        autohide=autohide,
    )


class FakeSkin:
    """Counts what is asked of it and returns solid images of the real surfaces' sizes."""

    def __init__(self) -> None:
        self.calls: collections.Counter[str] = collections.Counter()
        self.args: dict[str, tuple[Any, ...]] = {}

    def render_floating(self, model, theme, variant, scale, opts=None):
        self.calls["floating"] += 1
        self.args["floating"] = (scale, opts)
        return Image.new("RGBA", (round(280 * scale), round(126 * scale)), (200, 120, 0, 255))

    def render_taskbar(self, model, theme, variant, height_px, scale, opts=None):
        self.calls["taskbar"] += 1
        self.args["taskbar"] = (height_px, scale, opts)
        return Image.new("RGBA", (round(264 * scale), max(height_px, round(34 * scale))), (0, 120, 200, 255))

    def render_tooltip(self, model, theme, variant, scale):
        self.calls["tooltip"] += 1
        self.args["tooltip"] = (scale,)
        return Image.new("RGBA", (round(280 * scale), round(134 * scale)), (0, 200, 120, 255))

    def render_tray_icon(self, model, theme, variant, size_px):
        self.calls["tray"] += 1
        return Image.new("RGBA", (size_px, size_px), (255, 255, 255, 255))


def context(skin: FakeSkin | None = None, *, state: str = "normal3", blink_on: bool = True,
            hide_in_fullscreen: bool = False, theme_id: str = "nostromo") -> RenderContext:
    theme = themes.get_theme(theme_id)
    model = dataclasses.replace(sample_model(state), blink_on=blink_on)
    return RenderContext(
        model=model, theme=theme, variant=theme.default_variant, skin=skin or FakeSkin(),
        floating=RenderOptions(), taskbar=RenderOptions(), hide_floating_in_fullscreen=hide_in_fullscreen,
    )


class Rig:
    """A FloatingWidget or TaskbarWidget on fakes, with the pieces a test pokes at."""

    def __init__(self, kind: str, *, position: dict[str, int] | None = None) -> None:
        self.host = FakeHost()
        self.plat = FakePlatform()
        self.windows = WindowFactory()
        self.skin = FakeSkin()
        self.opened = 0
        self.menus: list[tuple[int, int]] = []
        self.saved: list[tuple[int, int]] = []
        self.position = position
        self.visibility_changes = 0
        self.tooltip = Tooltip(self.host, platform=self.plat, window_factory=self.windows)
        common = dict(
            on_open=self._open, on_menu=lambda x, y: self.menus.append((x, y)), tooltip=self.tooltip,
            on_visibility=self._visibility, platform=self.plat, window_factory=self.windows,
        )
        if kind == "floating":
            self.widget: Any = FloatingWidget(
                self.host, get_position=lambda: self.position, set_position=self._save, **common)
        else:
            self.widget = TaskbarWidget(self.host, **common)
        self.window: FakeWindow = self.windows.windows[0]

    def _open(self) -> None:
        self.opened += 1

    def _visibility(self) -> None:
        self.visibility_changes += 1

    def _save(self, x: int, y: int) -> None:
        self.saved.append((x, y))
        self.position = {"x": x, "y": y}

    def ctx(self, **kw: Any) -> RenderContext:
        return context(self.skin, **kw)

    @property
    def tooltip_window(self) -> FakeWindow:
        return self.windows.windows[1]


# ── main.py: the command line ─────────────────────────────────────────────────

@pytest.mark.parametrize("argv, kind", [
    ([], "app"),
    (["--whatever", "x"], "app"),
    (["--dashboard", "--port", "5000"], "dashboard"),
    (["--dashboard"], "dashboard"),
    (["--print-version", "v.txt"], "print_version"),
    (["--print-version"], "print_version"),
    (["--dump-frames", "out"], "dump_frames"),
    (["--smoke-test", "45", "report.json"], "smoke_test"),
])
def test_parse_args_picks_the_command(argv, kind):
    assert entry.parse_args(argv).kind == kind


def test_parse_args_carries_the_values():
    assert entry.parse_args(["--dashboard", "--port", "5000"]).port == 5000
    assert entry.parse_args(["--dashboard"]).port == 0              # the dashboard itself rejects a missing port
    assert entry.parse_args(["--print-version", "v.txt"]).path == "v.txt"
    assert entry.parse_args(["--print-version"]).path is None       # v1: no file, nothing to write
    assert entry.parse_args(["--dump-frames", "out dir"]).path == "out dir"
    smoke = entry.parse_args(["--smoke-test", "45", "r.json"])
    assert (smoke.seconds, smoke.path) == (45.0, "r.json")
    assert entry.parse_args(["--smoke-test", "7.5", "r.json"]).seconds == 7.5


@pytest.mark.parametrize("argv", [
    ["--dashboard", "--port", "x"],
    ["--dashboard", "--port", "0"],
    ["--dashboard", "--port", "70000"],
    ["--dashboard", "--port"],
    ["--dump-frames"],
    ["--dump-frames", "--port"],
    ["--smoke-test"],
    ["--smoke-test", "45"],
    ["--smoke-test", "abc", "r.json"],
    ["--smoke-test", "0", "r.json"],
    ["--smoke-test", "-5", "r.json"],
    ["--smoke-test", "nan", "r.json"],
    ["--smoke-test", "45", "--report"],
])
def test_parse_args_rejects_malformed_values(argv):
    command = entry.parse_args(argv)
    assert command.kind == "error" and command.error


def test_parse_args_mode_precedence():
    # The frozen-build hook must win over everything, the dashboard child over the dev tools.
    assert entry.parse_args(["--dump-frames", "d", "--print-version", "v"]).kind == "print_version"
    assert entry.parse_args(["--smoke-test", "5", "r", "--dashboard", "--port", "9"]).kind == "dashboard"
    assert entry.parse_args(["--smoke-test", "5", "r", "--dump-frames", "d"]).kind == "dump_frames"


def test_main_print_version_writes_the_bundled_version(tmp_path):
    target = tmp_path / "version.txt"
    assert entry.main(["--print-version", str(target)]) == 0
    assert target.read_text(encoding="utf-8") == version.APP_VERSION
    assert entry.main(["--print-version"]) == 0                      # no file: nothing to do, still success


def _log_text(folder: Path, name: str = "monitor.log") -> str:
    for handler in logging.getLogger("usage_monitor").handlers:
        handler.flush()
    return (folder / name).read_text(encoding="utf-8")


def test_print_version_fails_when_the_file_cannot_be_written(tmp_path, capsys, isolated_env):
    """The flag is the frozen-build check: a caller that trusts the exit code must not be told 0 for a
    write that never happened."""
    missing_folder = tmp_path / "no such folder" / "version.txt"
    assert entry.main(["--print-version", str(missing_folder)]) == 1
    assert not missing_folder.exists()
    assert "could not write the version" in capsys.readouterr().err
    assert "could not write the version" in _log_text(isolated_env)

    assert entry.main(["--print-version", str(tmp_path)]) == 1          # a folder is not a file either


def test_print_version_fails_when_the_build_has_no_version_file(monkeypatch, tmp_path, capsys, isolated_env):
    """version.py falls back to 0.0.0 when it cannot read VERSION. Exit 0 with that text would pass a
    frozen build that never bundled the file."""
    target = tmp_path / "version.txt"
    monkeypatch.setattr(version, "APP_VERSION", entry.UNKNOWN_VERSION)
    assert entry.main(["--print-version", str(target)]) == 1
    assert not target.exists()
    assert "no readable VERSION" in capsys.readouterr().err
    assert "no readable VERSION" in _log_text(isolated_env)


def test_print_version_failure_is_quiet_without_stderr(monkeypatch, tmp_path, isolated_env):
    monkeypatch.setattr(sys, "stderr", None)                              # the windowed build has no console
    assert entry.main(["--print-version", str(tmp_path / "gone" / "v.txt")]) == 1
    assert "could not write the version" in _log_text(isolated_env)


def test_main_reports_a_bad_command_line_with_exit_2(capsys):
    assert entry.main(["--dashboard", "--port", "nope"]) == 2
    assert "--port" in capsys.readouterr().err


def test_main_dispatches_each_command(monkeypatch, tmp_path):
    import usage_monitor.render.dump as dump_mod
    import usage_monitor.ui.dashboard as dashboard_mod

    seen: dict[str, Any] = {}

    class Report:
        files, sheets, out_dir = 12, [1], tmp_path

    def fake_dump(folder):
        seen["dump"] = folder
        return Report()

    def fake_dashboard(argv):
        seen["dashboard"] = argv
        return 7

    def fake_smoke(seconds, report):
        seen["smoke"] = (seconds, report)
        return 9

    def fake_run(self):
        seen["app"] = True
        return 5

    monkeypatch.setattr(dump_mod, "dump_frames", fake_dump)
    monkeypatch.setattr(dashboard_mod, "dashboard_main", fake_dashboard)
    monkeypatch.setattr(app_mod, "run_smoke_test", fake_smoke)
    monkeypatch.setattr(app_mod.MonitorApp, "run", fake_run)
    monkeypatch.setattr(entry, "_enable_faulthandler", lambda: None)

    assert entry.main(["--dump-frames", str(tmp_path / "frames")]) == 0
    assert seen["dump"] == str(tmp_path / "frames")
    assert entry.main(["--dashboard", "--port", "4242"]) == 7
    assert seen["dashboard"] == ["--dashboard", "--port", "4242"]
    assert entry.main(["--smoke-test", "30", "r.json"]) == 9
    assert seen["smoke"] == (30.0, "r.json")
    assert entry.main([]) == 5 and seen["app"] is True


def test_the_dashboard_child_logs_to_its_own_file(monkeypatch, isolated_env):
    import usage_monitor.ui.dashboard as dashboard_mod

    monkeypatch.setattr(dashboard_mod, "dashboard_main", lambda argv: 0)
    monkeypatch.setattr(entry, "_enable_faulthandler", lambda: None)
    entry.main(["--dashboard", "--port", "4242"])
    handlers = [h for h in logging.getLogger("usage_monitor").handlers if hasattr(h, "baseFilename")]
    assert any(Path(h.baseFilename).name == entry.DASHBOARD_LOG for h in handlers)


def test_a_thread_that_crashes_in_the_dashboard_child_is_logged(monkeypatch, isolated_env):
    """pywebview runs the event handlers and js_api calls on threads of its own. In the windowed build
    sys.stderr is None, so the default threading.excepthook printed nothing and the error was lost."""
    import usage_monitor.ui.dashboard as dashboard_mod

    def dashboard_with_a_crashing_thread(argv):
        worker = threading.Thread(target=lambda: 1 / 0, name="dashboard-probe-thread")
        worker.start()
        worker.join()
        return 0

    monkeypatch.setattr(dashboard_mod, "dashboard_main", dashboard_with_a_crashing_thread)
    monkeypatch.setattr(entry, "_enable_faulthandler", lambda: None)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)     # restored when the test ends
    monkeypatch.setattr(sys, "stderr", None)
    assert entry.main(["--dashboard", "--port", "4242"]) == 0
    text = _log_text(isolated_env, entry.DASHBOARD_LOG)
    assert "unhandled exception in thread dashboard-probe-thread" in text
    assert "ZeroDivisionError" in text and threading.excepthook is entry._log_thread_crash


def test_an_unhandled_exception_is_written_to_the_log(monkeypatch, isolated_env):
    def boom(self):
        raise RuntimeError("kaboom for the log")

    monkeypatch.setattr(app_mod.MonitorApp, "run", boom)
    monkeypatch.setattr(entry, "_enable_faulthandler", lambda: None)
    assert entry.main([]) == 1
    for handler in logging.getLogger("usage_monitor").handlers:
        handler.flush()
    text = (isolated_env / "monitor.log").read_text(encoding="utf-8")
    assert "unhandled exception in the app command" in text
    assert "RuntimeError: kaboom for the log" in text and "Traceback" in text


# ── Single instance ───────────────────────────────────────────────────────────

def test_the_mutex_names():
    assert mutex_name(False) == "Global\\ClaudeUsageMonitor"
    assert mutex_name(True) == "Global\\ClaudeUsageMonitor-dev"


@pytest.mark.parametrize("env, expected", [({}, False), ({"CUM_DEV_INSTANCE": "1"}, True),
                                           ({"CUM_DEV_INSTANCE": "0"}, False), ({"CUM_DEV_INSTANCE": "true"}, False),
                                           ({"CUM_DEV_INSTANCE": ""}, False)])
def test_only_cum_dev_instance_1_is_a_dev_instance(env, expected):
    assert is_dev_instance(env) is expected


def test_a_dev_app_uses_the_dev_mutex_and_a_normal_one_does_not(monkeypatch):
    assert MonitorApp()._mutex == "Global\\ClaudeUsageMonitor-dev"             # the fixture sets CUM_DEV_INSTANCE=1
    monkeypatch.delenv("CUM_DEV_INSTANCE")
    assert MonitorApp()._mutex == "Global\\ClaudeUsageMonitor"
    assert MonitorApp(dev=True)._mutex == "Global\\ClaudeUsageMonitor-dev"
    assert MonitorApp(mutex="Local\\custom")._mutex == "Local\\custom"


def test_the_smoke_test_is_always_a_dev_instance(monkeypatch, tmp_path):
    """The documented command sets no environment. The run still takes the dev mutex and controller window,
    never repairs the sign-in entry, and the dashboard child it starts inherits the same environment."""
    seen: dict[str, Any] = {}

    class RecordingApp:
        def __init__(self, **kwargs):
            seen.update(kwargs)
            seen["env"] = os.environ.get(app_mod.DEV_ENV)

        def run(self):
            return 0

    monkeypatch.delenv(app_mod.DEV_ENV, raising=False)
    monkeypatch.setattr(app_mod, "MonitorApp", RecordingApp)
    server_log = logging.getLogger("usage_monitor.ui.server")
    level = server_log.level                                              # SmokeTest.install() raises it
    try:
        app_mod.run_smoke_test(5, tmp_path / "smoke.json")
    finally:
        server_log.setLevel(level)
    assert seen["dev"] is True and seen["env"] == "1"
    assert seen["animate_when_locked"] is True and seen["smoke"] is not None


def test_the_second_holder_of_a_mutex_is_refused_until_the_first_lets_go():
    name = f"Local\\CUM-test-{uuid.uuid4().hex}"
    first, second = SingleInstance(name), SingleInstance(name)
    assert first.acquire() is True
    assert second.acquire() is False
    first.release()
    assert second.acquire() is True
    second.release()
    second.release()                                                           # repeating is harmless


def test_a_second_launch_signals_the_first_and_exits_without_starting(monkeypatch):
    name = f"Local\\CUM-test-{uuid.uuid4().hex}"
    holder = SingleInstance(name)
    assert holder.acquire()
    signalled: list[bool] = []
    monkeypatch.setattr(app_mod, "signal_running_instance", lambda: signalled.append(True) or True)
    monkeypatch.setattr(app_mod, "UIHost", lambda: pytest.fail("a second instance must not build a UI"))
    try:
        assert MonitorApp(mutex=name).run() == 0
    finally:
        holder.release()
    assert signalled == [True]


def test_a_second_launch_waits_briefly_for_a_first_instance_still_starting(monkeypatch):
    answers = iter([False, False, True])
    monkeypatch.setattr(app_mod, "signal_running_instance", lambda: next(answers))
    assert app_mod.signal_first_instance(wait_s=5.0, poll_s=0.0) is True
    monkeypatch.setattr(app_mod, "signal_running_instance", lambda: False)
    assert app_mod.signal_first_instance(wait_s=0.0, poll_s=0.0) is False


# ── Settings migration on startup ─────────────────────────────────────────────

class StopAfterMigration(Exception):
    pass


def start_until_the_ui(monkeypatch, settings_obj: Settings) -> MonitorApp:
    """Run MonitorApp.run() up to the point where it would build the UI, then stop."""
    def no_host():
        raise StopAfterMigration

    monkeypatch.setattr(app_mod, "UIHost", no_host)
    monkeypatch.setattr(app_mod.startup, "repair_if_enabled",
                        lambda: pytest.fail("a dev instance must not touch the Run entry"))
    app = MonitorApp(settings=settings_obj, mutex=f"Local\\CUM-test-{uuid.uuid4().hex}")
    with pytest.raises(StopAfterMigration):
        app.run()
    return app


@pytest.mark.parametrize("v1_name, variant", [("Default", "amber"), ("Blue", "blue"), ("White", "white"),
                                              ("Purple", "purple"), ("nonsense", "amber")])
def test_startup_migrates_the_v1_theme_name(monkeypatch, tmp_path, v1_name, variant):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"theme": v1_name, "poll_interval_ms": 120000}), encoding="utf-8")
    s = Settings(path, save_delay_s=0.0)
    start_until_the_ui(monkeypatch, s)
    assert (s.get("theme_id"), s.get("theme_variant")) == ("nostromo", variant)
    assert s.get("poll_interval_ms") == 120000                                  # the rest of the v1 file is kept
    s.close()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["theme_id"] == "nostromo" and saved["theme_variant"] == variant


def test_startup_migration_leaves_a_chosen_v2_theme_alone(monkeypatch, tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"theme": "Red", "theme_id": "gates", "theme_variant": "blue"}), encoding="utf-8")
    s = Settings(path, save_delay_s=0.0)
    start_until_the_ui(monkeypatch, s)
    assert (s.get("theme_id"), s.get("theme_variant")) == ("gates", "blue")
    s.close()


def test_a_fresh_install_starts_on_nostromo_amber(monkeypatch, tmp_path):
    s = Settings(tmp_path / "fresh.json", save_delay_s=0.0)
    start_until_the_ui(monkeypatch, s)
    assert (s.get("theme_id"), s.get("theme_variant")) == ("nostromo", "amber")
    s.close()


def test_the_installed_build_repairs_its_startup_entry_and_a_dev_build_does_not(monkeypatch, tmp_path):
    calls: list[str] = []
    monkeypatch.setattr(app_mod.startup, "repair_if_enabled", lambda: calls.append("repair") or False)
    monkeypatch.setattr(app_mod, "UIHost", lambda: (_ for _ in ()).throw(StopAfterMigration()))
    for dev in (False, True):
        s = Settings(tmp_path / f"s{dev}.json", save_delay_s=0.0)
        app = MonitorApp(dev=dev, settings=s, mutex=f"Local\\CUM-test-{uuid.uuid4().hex}")
        with pytest.raises(StopAfterMigration):
            app.run()
        s.close()
    assert calls == ["repair"]


class RunEntry:
    """A recording stand-in for the HKCU Run value, below startup.repair_if_enabled and set_enabled."""

    def __init__(self, monkeypatch, value):
        self.value = value
        self.reads = 0
        self.writes: list[str] = []
        self.deletes = 0
        monkeypatch.setattr(app_mod.startup, "_read_value", self.read)
        monkeypatch.setattr(app_mod.startup, "_write_value", self.write)
        monkeypatch.setattr(app_mod.startup, "_delete_value", self.delete)

    def read(self):
        self.reads += 1
        return self.value

    def write(self, command):
        self.writes.append(command)
        self.value = command

    def delete(self):
        self.deletes += 1
        self.value = None


INSTALLED_ENTRY = '"C:\\Users\\me\\AppData\\Local\\Programs\\Claude Usage Monitor\\ClaudeUsageMonitor.exe"'


def test_a_source_run_leaves_an_installed_copys_startup_entry_alone(monkeypatch, tmp_path):
    """launch.bat and python main.py are not dev instances. Quit the installed copy, run from source, and
    the entry used to be rewritten to the checkout's pythonw main.py for good."""
    entry = RunEntry(monkeypatch, INSTALLED_ENTRY)
    monkeypatch.delattr(sys, "frozen", raising=False)
    s = Settings(tmp_path / "s.json", save_delay_s=0.0)
    MonitorApp(dev=False, settings=s, mutex=f"Local\\CUM-test-{uuid.uuid4().hex}")._repair_startup()
    s.close()
    assert (entry.reads, entry.writes, entry.deletes) == (0, [], 0)
    assert entry.value == INSTALLED_ENTRY


def test_the_frozen_build_repairs_a_moved_startup_entry_and_the_dev_build_never_reads_it(monkeypatch, tmp_path):
    entry = RunEntry(monkeypatch, INSTALLED_ENTRY)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    s = Settings(tmp_path / "s.json", save_delay_s=0.0)
    MonitorApp(dev=True, settings=s, mutex=f"Local\\CUM-test-{uuid.uuid4().hex}")._repair_startup()
    assert (entry.reads, entry.writes) == (0, [])                        # dev: not even a look
    MonitorApp(dev=False, settings=s, mutex=f"Local\\CUM-test-{uuid.uuid4().hex}")._repair_startup()
    s.close()
    assert entry.writes == [app_mod.startup.command_line(frozen=True)]    # the installed build points it at itself


def test_a_registry_error_while_repairing_the_startup_entry_does_not_stop_the_app(monkeypatch, tmp_path):
    def refuse():
        raise OSError("registry refused")

    monkeypatch.setattr(app_mod.startup, "repair_if_enabled", refuse)
    monkeypatch.setattr(app_mod, "UIHost", lambda: (_ for _ in ()).throw(StopAfterMigration()))
    s = Settings(tmp_path / "s.json", save_delay_s=0.0)
    app = MonitorApp(dev=False, settings=s, mutex=f"Local\\CUM-test-{uuid.uuid4().hex}")
    with pytest.raises(StopAfterMigration):                                      # got past the repair to the UI
        app.run()
    s.close()


# ── Placement arithmetic ──────────────────────────────────────────────────────

WORK = [Rect(0, 0, 1920, 1032)]


def test_scale_follows_the_dpi():
    assert [scale_for(d) for d in (96, 120, 144, 192)] == [1.0, 1.25, 1.5, 2.0]
    assert scale_for(0) == scale_for(None) == 1.0


def test_saved_point_reads_settings_positions():
    assert saved_point({"x": 10, "y": -20}) == (10, -20)
    assert saved_point({"x": 10.7, "y": 3.2}) == (10, 3)
    for bad in (None, {}, {"x": 1}, {"x": "1", "y": 2}, {"x": True, "y": 2}, "x", [1, 2],
                {"x": float("nan"), "y": 2}, {"x": 1, "y": float("inf")}):
        assert saved_point(bad) is None


def test_the_default_floating_position_is_the_bottom_right_corner_above_the_tray():
    assert floating_origin(None, 280, 126, 1.0, WORK) == (1920 - 280 - 12, 1032 - 126 - 12)
    assert floating_origin(None, 420, 189, 1.5, WORK) == (1920 - 420 - 18, 1032 - 189 - 18)
    secondary_first = [Rect(0, 0, 1920, 1032), Rect(1920, 0, 3840, 1080)]
    assert floating_origin(None, 280, 126, 1.0, secondary_first)[0] < 1920       # on the primary monitor


def test_a_saved_floating_position_inside_a_work_area_is_kept():
    assert floating_origin((300, 400), 280, 126, 1.0, WORK) == (300, 400)


@pytest.mark.parametrize("saved, expected", [
    ((5000, 5000), (1640, 906)),     # far off the bottom right: the nearest in-bounds spot
    ((-500, -500), (0, 0)),          # far off the top left
    ((1800, 10), (1640, 10)),        # hanging off the right edge
    ((10, 1000), (10, 906)),         # down over the taskbar
])
def test_a_saved_floating_position_off_the_screen_is_pulled_back_in(saved, expected):
    assert floating_origin(saved, 280, 126, 1.0, WORK) == expected


def test_floating_position_across_monitors_with_a_negative_origin():
    areas = [Rect(0, 0, 1920, 1032), Rect(-1280, 0, 0, 984)]
    assert floating_origin((-1000, 100), 280, 126, 1.0, areas) == (-1000, 100)
    assert floating_origin((-1500, 100), 280, 126, 1.0, areas) == (-1280, 100)    # mostly on the left monitor
    assert floating_origin((-900, 2000), 280, 126, 1.0, areas) == (-900, 858)     # below the left one: pulled up
    assert floating_origin((-100, 2000), 280, 126, 1.0, areas) == (0, 906)        # nearer the primary monitor


def test_floating_position_without_any_monitor_info_is_left_alone():
    assert floating_origin((12, 34), 280, 126, 1.0, []) == (12, 34)
    assert floating_origin(None, 280, 126, 1.0, []) == (0, 0)


def test_the_strip_sits_just_left_of_the_notification_area_at_every_scale():
    info = taskbar(96)
    assert strip_origin(info, 264, 48) == (1694 - 4 - 264, 1032)
    info = taskbar(144)                                           # 150 %: taskbar 72 px, tray gap 6 px
    assert strip_origin(info, 396, 72) == (info.tray_rect.left - 6 - 396, info.rect.top)


def test_the_strip_is_centred_on_a_taller_or_shorter_window():
    info = taskbar(96)
    assert strip_origin(info, 264, 34)[1] == 1032 + 7             # a 34 px strip in a 48 px taskbar
    assert strip_origin(info, 264, 51)[1] == 1032 - 2             # a window taller than the taskbar


def test_the_strip_never_starts_left_of_the_taskbar():
    info = taskbar(96)
    narrow = TaskbarInfo(**{**info.__dict__, "tray_rect": Rect(100, 1032, 1920, 1080)})
    assert strip_origin(narrow, 264, 48)[0] == 0


@pytest.mark.parametrize("info", [
    taskbar(96, edge="left"), taskbar(96, edge="right"), taskbar(96, autohide=True), taskbar(96, tray=False),
])
def test_the_strip_has_no_place_on_a_vertical_or_auto_hiding_taskbar(info):
    assert strip_supported(info) is False
    assert strip_origin(info, 264, 48) is None


def test_a_top_taskbar_is_supported():
    top = taskbar(96, edge="top")
    assert strip_supported(top) is True


def test_a_tooltip_goes_above_its_widget_and_below_when_there_is_no_room():
    bounds = Rect(0, 0, 1920, 1032)
    assert tooltip_origin(Rect(1628, 894, 1908, 1020), 280, 134, bounds, 8) == (1628, 894 - 8 - 134)
    assert tooltip_origin(Rect(100, 10, 380, 136), 280, 134, bounds, 8) == (100, 136 + 8)


def test_a_tooltip_is_centred_on_a_narrower_strip_and_kept_inside_the_screen():
    bounds = Rect(0, 0, 1920, 1032)
    assert tooltip_origin(Rect(1426, 1032, 1690, 1080), 280, 134, bounds, 8) == (1418, 1032 - 8 - 134)
    assert tooltip_origin(Rect(1700, 1032, 1964, 1080), 280, 134, bounds, 8)[0] == 1640     # clamped to the right edge
    assert tooltip_origin(Rect(-30, 500, 250, 626), 280, 134, bounds, 8)[0] == 0             # and the left edge


def test_a_tooltip_that_fits_nowhere_is_clamped_into_the_bounds():
    short = Rect(0, 0, 1920, 120)
    x, y = tooltip_origin(Rect(100, 20, 380, 100), 280, 134, short, 8)
    assert short.top <= y and (x, y) == (100, 0)


def test_a_tooltip_without_bounds_is_just_centred_above():
    assert tooltip_origin(Rect(100, 300, 380, 426), 280, 134, None, 8) == (100, 300 - 8 - 134)


# ── The frame cache ───────────────────────────────────────────────────────────

def test_the_frame_cache_renders_each_blink_phase_once_per_key():
    cache, renders = FrameCache(), []

    def render(tag):
        renders.append(tag)
        return Image.new("RGBA", (2, 2))

    lit = cache.get(("a",), True, lambda: render("lit"))
    dark = cache.get(("a",), False, lambda: render("dark"))
    assert cache.get(("a",), True, lambda: render("lit again")) is lit
    assert cache.get(("a",), False, lambda: render("dark again")) is dark
    assert renders == ["lit", "dark"]
    cache.get(("b",), True, lambda: render("new key"))
    cache.get(("a",), False, lambda: render("back to a"))          # a different key forgot the old frames
    assert renders == ["lit", "dark", "new key", "back to a"]


def test_steady_ignores_the_blink_phase_and_the_publish_time():
    model = sample_model("normal3")
    dark = dataclasses.replace(model, blink_on=False, updated_at=model.updated_at + timedelta(minutes=5))
    assert steady(model) == steady(dark)
    other = sample_model("warning")
    assert steady(model) != steady(other)


# ── FloatingWidget on fakes ───────────────────────────────────────────────────

def test_the_floating_widget_draws_places_and_shows_its_window():
    rig = Rig("floating")
    assert not rig.window.visible                                      # nothing flashes up before the first frame
    rig.widget.render(rig.ctx())
    assert rig.window.updates == [((280, 126), 1920 - 280 - 12, 1032 - 126 - 12)]
    assert rig.window.visible and rig.visibility_changes == 1
    assert rig.skin.args["floating"][0] == 1.0
    assert rig.window.draggable is True


def test_drawing_the_same_thing_again_costs_no_render_and_no_window_update():
    rig = Rig("floating")
    ctx = rig.ctx()
    rig.widget.render(ctx)
    rig.widget.render(ctx)
    rig.widget.render(rig.ctx())                                       # an equal context, built again
    assert rig.skin.calls["floating"] == 1 and len(rig.window.updates) == 1


def test_a_new_publish_time_alone_redraws_nothing():
    rig = Rig("floating")
    rig.widget.render(rig.ctx())
    model = sample_model("normal3")
    later = dataclasses.replace(model, updated_at=model.updated_at + timedelta(minutes=1))
    rig.widget.render(RenderContext(later, rig.ctx().theme, rig.ctx().variant, rig.skin))
    assert rig.skin.calls["floating"] == 1


def test_blinking_renders_each_phase_once_and_then_only_swaps_frames():
    rig = Rig("floating")
    for lit in (True, False, True, False, True):
        rig.widget.render(rig.ctx(blink_on=lit))
    assert rig.skin.calls["floating"] == 2                             # one lit frame, one dark frame
    assert len(rig.window.updates) == 5                                # ...and every toggle reached the window


def test_changed_data_renders_a_new_frame():
    rig = Rig("floating")
    rig.widget.render(rig.ctx(state="normal3"))
    rig.widget.render(rig.ctx(state="warning"))
    assert rig.skin.calls["floating"] == 2


def test_the_saved_floating_position_is_used_and_clamped_to_the_screen():
    rig = Rig("floating", position={"x": 300, "y": 400})
    rig.widget.render(rig.ctx())
    assert rig.window.rect.left == 300 and rig.window.rect.top == 400
    rig = Rig("floating", position={"x": 9000, "y": 9000})
    rig.widget.render(rig.ctx())
    assert (rig.window.rect.left, rig.window.rect.top) == (1640, 906)
    assert rig.saved == []                                             # a clamp on display is not written back


def test_a_floating_widget_that_was_moved_away_is_put_back_where_its_settings_say():
    """Windows pulls windows off a monitor that went away; the next re-place must undo that."""
    rig = Rig("floating", position={"x": 300, "y": 200})
    rig.widget.render(rig.ctx())
    home, updates = rig.window.rect, len(rig.window.updates)
    rig.window.rect = Rect(0, 0, home.width, home.height)
    rig.widget.render(rig.ctx())
    assert rig.window.rect == home and len(rig.window.updates) == updates + 1
    rig.widget.render(rig.ctx())
    assert len(rig.window.updates) == updates + 1, "in place again: nothing more to hand to Windows"
    assert rig.skin.calls["floating"] == 1, "put back, not redrawn"


def test_a_taller_frame_keeps_the_default_corner():
    rig = Rig("floating")
    rig.widget.render(rig.ctx())
    bottom = rig.window.rect.bottom
    rig.skin.render_floating = lambda *a, **k: Image.new("RGBA", (280, 158))        # a third row appears
    rig.widget.render(rig.ctx(state="warning"))
    assert rig.window.rect.bottom == bottom and rig.window.rect.height == 158


def test_the_widget_follows_a_changed_display_layout():
    rig = Rig("floating")
    rig.widget.render(rig.ctx())
    rig.plat.areas = [Rect(0, 0, 1280, 984)]
    rig.widget.render(rig.ctx())                                       # the app re-renders on WM_DISPLAYCHANGE
    assert (rig.window.rect.right, rig.window.rect.bottom) == (1280 - 12, 984 - 12)


def test_the_floating_widget_renders_at_the_dpi_of_its_monitor():
    rig = Rig("floating")
    rig.plat.dpi = 144
    rig.window.dpi = 144
    rig.widget.render(rig.ctx())
    assert rig.skin.args["floating"][0] == 1.5
    assert rig.window.rect.width == 420


def test_a_first_guess_at_the_dpi_is_corrected_once_the_window_is_on_its_monitor():
    rig = Rig("floating")
    rig.plat.dpi = 96                                                  # the point lookup says 100 %...
    rig.window.dpi = 192                                               # ...Windows says it is on a 200 % monitor
    rig.widget.render(rig.ctx())
    assert rig.skin.calls["floating"] == 2
    assert rig.window.rect.width == 560


def test_a_dpi_change_redraws_in_place_at_the_new_scale():
    rig = Rig("floating", position={"x": 1700, "y": 600})
    rig.widget.render(rig.ctx())
    rig.window.fire("on_dpi_changed", 192, Rect(0, 0, 0, 0))
    assert rig.skin.args["floating"][0] == 2.0
    assert rig.window.rect.width == 560
    assert rig.window.rect.right <= 1920                                # re-clamped: it would hang off the screen
    assert rig.window.moves


def test_a_redraw_during_a_drag_leaves_the_window_where_the_user_has_it():
    rig = Rig("floating", position={"x": 300, "y": 400})
    rig.widget.render(rig.ctx(state="normal3"))
    rig.window.dragging = True
    rig.window.rect = Rect(777, 555, 777 + 280, 555 + 126)
    rig.widget.render(rig.ctx(state="warning"))                         # new data arrives mid-drag
    assert (rig.window.rect.left, rig.window.rect.top) == (777, 555)
    assert rig.window.updates[-1][1:] == (777, 555)


def test_a_double_click_opens_the_dashboard_and_a_right_click_opens_the_menu():
    rig = Rig("floating")
    rig.widget.render(rig.ctx())
    rig.window.fire("on_double_click", 100, 100)
    assert rig.opened == 1
    rig.window.fire("on_right_click", 321, 654)
    assert rig.menus == [(321, 654)]


def test_dropping_the_widget_saves_its_position():
    rig = Rig("floating")
    rig.widget.render(rig.ctx())
    rig.window.rect = Rect(640, 300, 920, 426)
    rig.window.fire("on_drag_end", 640, 300)
    assert rig.saved == [(640, 300)] and rig.window.moves == []


def test_dropping_the_widget_half_off_the_screen_pulls_it_in_and_saves_the_clamped_spot():
    rig = Rig("floating")
    rig.widget.render(rig.ctx())
    rig.window.rect = Rect(1800, 950, 2080, 1076)
    rig.window.fire("on_drag_end", 1800, 950)
    assert rig.saved == [(1640, 906)]
    assert rig.window.moves == [(1640, 906)]


def test_the_saved_position_survives_a_redraw():
    rig = Rig("floating")
    rig.widget.render(rig.ctx())
    rig.window.rect = Rect(640, 300, 920, 426)
    rig.window.fire("on_drag_end", 640, 300)
    rig.widget.render(rig.ctx(state="warning"))
    assert (rig.window.rect.left, rig.window.rect.top) == (640, 300)


def test_hide_in_fullscreen_follows_the_foreground_app_and_runs_a_timer_only_while_on():
    rig = Rig("floating")
    rig.widget.render(rig.ctx(hide_in_fullscreen=True))
    assert rig.host.timer_ms() == [500] and rig.window.visible
    rig.plat.fullscreen = True
    rig.widget.keep()
    assert not rig.window.visible
    rig.plat.fullscreen = False
    rig.widget.keep()
    assert rig.window.visible
    rig.widget.render(rig.ctx(hide_in_fullscreen=False))
    assert rig.host.timers == {}                                       # the option went off: no timer left running


def test_with_hide_in_fullscreen_off_the_widget_stays_up_over_a_fullscreen_app():
    rig = Rig("floating")
    rig.plat.fullscreen = True
    rig.widget.render(rig.ctx(hide_in_fullscreen=False))
    assert rig.window.visible and rig.host.timers == {}


def test_a_locked_session_pauses_the_fullscreen_poll():
    rig = Rig("floating")
    rig.widget.render(rig.ctx(hide_in_fullscreen=True))
    rig.host.locked = True
    rig.plat.fullscreen = True
    rig.widget.keep()
    assert rig.window.visible


def test_destroying_the_floating_widget_stops_its_timer_and_destroys_the_window():
    rig = Rig("floating")
    rig.widget.render(rig.ctx(hide_in_fullscreen=True))
    rig.widget.destroy()
    assert rig.window.destroyed and rig.host.timers == {}
    rig.widget.destroy()                                               # repeating is harmless
    rig.widget.render(rig.ctx())                                       # and drawing a destroyed widget is a no-op


# ── TaskbarWidget on fakes ────────────────────────────────────────────────────

def test_the_strip_is_drawn_to_the_taskbar_height_and_placed_left_of_the_tray():
    rig = Rig("taskbar")
    rig.widget.render(rig.ctx())
    height_px, scale, _opts = rig.skin.args["taskbar"]
    assert (height_px, scale) == (48, 1.0)
    assert rig.window.updates == [((264, 48), 1694 - 4 - 264, 1032)]
    assert rig.window.visible and rig.window.draggable is False


def test_the_strip_follows_the_taskbar_dpi_and_height():
    rig = Rig("taskbar")
    rig.plat.info = taskbar(144)
    rig.widget.render(rig.ctx())
    assert rig.skin.args["taskbar"][:2] == (72, 1.5)
    assert rig.window.rect.right == rig.plat.info.tray_rect.left - 6


def test_the_strip_moves_when_the_tray_area_grows():
    rig = Rig("taskbar")
    rig.widget.render(rig.ctx())
    first = rig.window.rect.left
    rig.plat.info = TaskbarInfo(**{**rig.plat.info.__dict__, "tray_rect": Rect(1600, 1032, 1920, 1080)})
    rig.widget.render(rig.ctx())
    assert rig.window.rect.left == first - 94 and rig.skin.calls["taskbar"] == 1      # moved, not redrawn


def test_a_strip_that_was_moved_away_is_put_back_by_the_next_re_place():
    rig = Rig("taskbar")
    rig.widget.render(rig.ctx())
    home, updates = rig.window.rect, len(rig.window.updates)
    rig.window.rect = Rect(10, 10, 10 + home.width, 10 + home.height)
    rig.widget.render(rig.ctx())                                       # what a display or taskbar event does
    assert rig.window.rect == home and len(rig.window.updates) == updates + 1
    rig.widget.render(rig.ctx())
    assert len(rig.window.updates) == updates + 1, "in place again: nothing more to hand to Windows"
    assert rig.skin.calls["taskbar"] == 1, "put back, not redrawn"


def test_the_three_second_re_place_also_fixes_a_displaced_strip():
    rig = Rig("taskbar")
    rig.widget.render(rig.ctx())
    home = rig.window.rect
    rig.window.rect = Rect(0, 0, home.width, home.height)
    for _ in range(6):                                                 # six 500 ms keep ticks
        rig.host.fire(next(iter(rig.host.timers)))
    assert rig.window.rect == home


@pytest.mark.parametrize("info", [None, taskbar(96, edge="left"), taskbar(96, autohide=True), taskbar(96, tray=False)])
def test_the_strip_hides_itself_where_it_has_no_place(info):
    rig = Rig("taskbar")
    rig.widget.render(rig.ctx())
    assert rig.window.visible
    rig.plat.info = info
    rig.widget.render(rig.ctx())
    assert not rig.window.visible
    rig.widget.render(rig.ctx())                                       # asked again: still quiet, no crash
    rig.plat.info = taskbar(96)
    rig.widget.render(rig.ctx())
    assert rig.window.visible


def test_a_click_on_the_strip_opens_the_dashboard_and_a_right_click_the_menu():
    rig = Rig("taskbar")
    rig.widget.render(rig.ctx())
    rig.window.fire("on_click", 1500, 1050)
    assert rig.opened == 1
    rig.window.fire("on_right_click", 1500, 1050)
    assert rig.menus == [(1500, 1050)]


def test_the_strip_is_raised_over_the_taskbar_every_500_ms():
    rig = Rig("taskbar")
    assert rig.host.timer_ms() == [500]
    rig.widget.render(rig.ctx())
    for _ in range(4):
        rig.widget.keep()
    assert rig.window.raised == 4


def test_the_strip_is_placed_again_every_third_second():
    rig = Rig("taskbar")
    rig.widget.render(rig.ctx())
    rig.plat.info = TaskbarInfo(**{**rig.plat.info.__dict__, "tray_rect": Rect(1500, 1032, 1920, 1080)})
    for _ in range(5):
        rig.widget.keep()
    assert rig.window.rect.right == 1694 - 4                           # not yet: five ticks is 2.5 s
    rig.widget.keep()
    assert rig.window.rect.right == 1500 - 4                           # the sixth tick re-placed it


def test_the_strip_hides_in_fullscreen_and_comes_back():
    rig = Rig("taskbar")
    rig.widget.render(rig.ctx())
    rig.plat.fullscreen = True
    rig.widget.keep()
    assert not rig.window.visible
    rig.widget.keep()
    assert not rig.window.visible and rig.window.raised == 0
    rig.plat.fullscreen = False
    rig.widget.keep()
    assert rig.window.visible


def test_a_locked_session_leaves_the_strip_alone():
    rig = Rig("taskbar")
    rig.widget.render(rig.ctx())
    rig.host.locked = True
    rig.plat.fullscreen = True
    rig.widget.keep()
    assert rig.window.visible and rig.window.raised == 0


def test_a_dpi_change_redraws_the_strip():
    rig = Rig("taskbar")
    rig.widget.render(rig.ctx())
    rig.plat.info = taskbar(192)
    rig.window.fire("on_dpi_changed", 192, Rect(0, 0, 0, 0))
    assert rig.skin.args["taskbar"][1] == 2.0


def test_destroying_the_strip_stops_its_timer():
    rig = Rig("taskbar")
    rig.widget.destroy()
    assert rig.host.timers == {} and rig.window.destroyed


# ── Tooltip on fakes ──────────────────────────────────────────────────────────

def hover(rig: Rig, *, over: bool = True) -> None:
    """Park the fake pointer over (or away from) the widget and let the layered window report the dwell."""
    rect = rig.window.rect
    rig.plat.cursor = (rect.left + 5, rect.top + 5) if over else (0, 0)
    rig.window.fire("on_hover", True)
    rig.window.fire("on_hover_dwell")


def test_resting_on_the_widget_shows_a_click_through_tooltip_above_it():
    rig = Rig("floating")
    rig.widget.render(rig.ctx())
    hover(rig)
    tip = rig.tooltip_window
    assert tip.visible and tip.click_through and tip.draggable is False and tip.raised == 1
    anchor = rig.window.rect
    assert tip.rect == Rect(anchor.left, anchor.top - 8 - 134, anchor.left + 280, anchor.top - 8)
    assert rig.skin.calls["tooltip"] == 1 and rig.tooltip.owner is rig.widget


def test_the_pointer_leaving_hides_the_tooltip():
    rig = Rig("floating")
    rig.widget.render(rig.ctx())
    hover(rig)
    rig.window.fire("on_hover", False)
    assert not rig.tooltip_window.visible and rig.tooltip.owner is None


def test_the_strip_tooltip_stands_on_the_taskbar_and_is_drawn_at_the_taskbar_dpi():
    rig = Rig("taskbar")
    rig.plat.info = taskbar(144)
    rig.window.dpi = 144
    rig.widget.render(rig.ctx())
    hover(rig)
    assert rig.skin.args["tooltip"] == (1.5,)
    assert rig.tooltip_window.rect.bottom == rig.plat.info.rect.top - 12


def test_a_click_a_right_click_and_a_double_click_all_dismiss_the_tooltip():
    for gesture in ("on_double_click", "on_right_click"):
        rig = Rig("floating")
        rig.widget.render(rig.ctx())
        hover(rig)
        rig.window.fire(gesture, 10, 10)
        assert not rig.tooltip_window.visible, gesture
    rig = Rig("taskbar")
    rig.widget.render(rig.ctx())
    hover(rig)
    rig.window.fire("on_click", 10, 10)
    assert not rig.tooltip_window.visible


def test_no_tooltip_while_dragging_or_when_the_widget_is_hidden():
    rig = Rig("floating")
    rig.widget.render(rig.ctx())
    rig.window.dragging = True
    hover(rig)
    assert len(rig.windows.windows) == 1                               # the tooltip window was never even created
    rig.window.dragging = False
    rig.window.hide()
    hover(rig)
    assert len(rig.windows.windows) == 1


def test_only_the_widget_that_showed_the_tooltip_can_hide_it():
    rig = Rig("floating")
    rig.widget.render(rig.ctx())
    hover(rig)
    other = object()
    rig.tooltip.hide(other)
    assert rig.tooltip_window.visible
    rig.tooltip.hide(rig.widget)
    assert not rig.tooltip_window.visible


def test_new_data_redraws_a_tooltip_that_is_up_and_cached_frames_are_not_rendered_again():
    rig = Rig("floating")
    rig.widget.render(rig.ctx(state="normal3"))
    hover(rig)
    updates = len(rig.tooltip_window.updates)
    rig.tooltip.update(rig.ctx(state="normal3"))                        # nothing changed
    assert len(rig.tooltip_window.updates) == updates and rig.skin.calls["tooltip"] == 1
    rig.tooltip.update(rig.ctx(state="warning"))
    assert len(rig.tooltip_window.updates) == updates + 1 and rig.skin.calls["tooltip"] == 2
    rig.tooltip.update(rig.ctx(state="normal3"))                        # the cache keeps one key: this renders again
    assert rig.skin.calls["tooltip"] == 3


def test_the_tooltip_watches_for_a_pointer_that_slipped_away():
    rig = Rig("floating")
    rig.widget.render(rig.ctx())
    hover(rig)
    assert rig.host.timer_ms() == [200]
    (timer_id,) = rig.host.timers
    rig.host.fire(timer_id)                                             # the pointer is still over the widget
    assert rig.tooltip_window.visible
    rig.plat.cursor = (0, 0)                                            # a missed WM_MOUSELEAVE
    rig.host.fire(timer_id)
    assert not rig.tooltip_window.visible and rig.host.timers == {}


def test_the_tooltip_goes_when_a_drag_starts_under_it():
    rig = Rig("floating")
    rig.widget.render(rig.ctx())
    hover(rig)
    rig.window.dragging = True
    (timer_id,) = rig.host.timers
    rig.host.fire(timer_id)
    assert not rig.tooltip_window.visible


def test_hiding_a_widget_or_destroying_it_takes_its_tooltip_with_it():
    rig = Rig("taskbar")
    rig.widget.render(rig.ctx())
    hover(rig)
    rig.plat.fullscreen = True
    rig.widget.keep()
    assert not rig.tooltip_window.visible
    rig = Rig("floating")
    rig.widget.render(rig.ctx())
    hover(rig)
    rig.widget.destroy()
    assert not rig.tooltip_window.visible


def test_the_tooltip_window_is_destroyed_with_the_tooltip():
    rig = Rig("floating")
    rig.widget.render(rig.ctx())
    hover(rig)
    rig.tooltip.destroy()
    assert rig.tooltip_window.destroyed and rig.host.timers == {}


# ── Text, menu, clock helpers ─────────────────────────────────────────────────

def model_for(state: str):
    return sample_model(state)


@pytest.mark.parametrize("state, text", [
    ("normal3", "Claude usage - session 13% - weekly 2%"),
    ("warning", "Claude usage - session 78% - weekly 21%"),
    ("paused", "Claude usage - session 13% - weekly 2%"),
    ("stale", "Claude usage - session 13% - weekly 2% - stale"),
    ("offline", "Claude usage - session 13% - weekly 2% - offline"),
    ("rate_limited", "Claude usage - session 13% - weekly 2% - rate limited"),
    ("loading", "Claude usage - loading"),
    ("no_credentials", "Claude usage - no login"),
])
def test_the_tray_tooltip_reads_session_then_weekly(state, text):
    assert tray_tooltip(model_for(state)) == text
    assert tray_tooltip(model_for(state), dev=True) == text + " (dev)"


def test_the_tray_tooltip_rounds_like_the_widget_and_fits_the_shell_limit():
    state = sample_state("normal3")
    state.usage.limits[0].percent = 12.5
    state.usage.limits[1].percent = 99.6
    text = tray_tooltip(build_model(state, NOW), dev=True)
    assert text == "Claude usage - session 13% - weekly 100% (dev)"
    assert len(text) < 127


def test_the_menu_lists_the_actions_in_order_and_marks_the_toggles():
    calls: list[str] = []
    items = menu_items(
        floating_on=True, taskbar_on=False, update_version=None,
        open_dashboard=lambda: calls.append("open"), toggle_floating=lambda: calls.append("floating"),
        toggle_taskbar=lambda: calls.append("taskbar"), refresh=lambda: calls.append("refresh"),
        report_bug=lambda: calls.append("bug"), update=lambda: calls.append("update"),
        quit_app=lambda: calls.append("quit"),
    )
    labelled = [(i.label, i.checked) for i in items if not i.separator]
    assert labelled == [("Open dashboard", False), ("Floating widget", True), ("Taskbar widget", False),
                        ("Refresh now", False), ("Report a bug", False), ("Exit", False)]
    assert items[0].label == "Open dashboard" and items[-1].label == "Exit"
    assert items[1].separator and items[-2].separator
    assert not any(a.separator and b.separator for a, b in zip(items, items[1:]))
    for item in items:
        if item.action:
            item.action()
    assert calls == ["open", "floating", "taskbar", "refresh", "bug", "quit"]


def test_the_menu_offers_the_update_only_when_there_is_one():
    kwargs = dict(floating_on=True, taskbar_on=True, open_dashboard=lambda: None, toggle_floating=lambda: None,
                  toggle_taskbar=lambda: None, refresh=lambda: None, report_bug=lambda: None,
                  update=lambda: None, quit_app=lambda: None)
    plain = [i.label for i in menu_items(update_version=None, **kwargs)]
    offered = [i.label for i in menu_items(update_version="2.1.0", **kwargs)]
    assert "Update to v2.1.0" in offered and not any(label.startswith("Update") for label in plain)
    assert offered.index("Update to v2.1.0") == offered.index("Report a bug") + 1


def test_links_stay_on_github():
    assert bug_report_url() == f"https://github.com/{version.REPO}/issues/new?template=bug_report.md&labels=bug"
    good = "https://github.com/Godimas101/claude-usage-monitor/releases/tag/v9.9.9"
    assert release_url({"url": good}) == good
    latest = f"https://github.com/{version.REPO}/releases/latest"
    for bad in ({"url": "https://evil.example/x"}, {"url": "file:///c:/x"}, {"url": None}, {}, None):
        assert release_url(bad) == latest


@pytest.mark.parametrize("age, expected", [
    (None, 30000), (-3.0, 30000), (0.0, 1025), (12.3, 725), (59.9, 125),
    (60.0, 30000), (95.0, 25025), (3599.5, 525),
])
def test_the_clock_tick_lands_just_after_the_next_change_of_the_age_text(age, expected):
    assert tick_delay_ms(age) == pytest.approx(expected, abs=2)


def test_the_age_readout_changes_exactly_when_the_tick_fires():
    """The tick for an age of 12.3 s lands just after 13.0 s: the readout reads 13 then, not 12."""
    state = sample_state("normal3", NOW)
    state.usage_fetched_at = NOW - timedelta(seconds=12.3)
    later = NOW + timedelta(milliseconds=tick_delay_ms(12.3))
    assert build_model(state, NOW).age_text == "12 S AGO"
    assert build_model(state, later).age_text == "13 S AGO"


def test_only_nostromo_blinks_and_its_cycle_is_2_4_seconds_85_percent_lit():
    assert blink_cycle(themes.get_theme("nostromo")) == (2040, 360)
    assert blink_cycle(themes.get_theme("night_city")) is None          # a smooth pulse, never a stepped blink
    assert blink_cycle(themes.get_theme("gates")) is None


def test_the_blink_cycle_is_read_from_the_theme_and_survives_odd_input():
    class T:
        def __init__(self, extras):
            self.extras = extras

    assert blink_cycle(T({"motion": {"alive_blink": "1.2 s steps(1), on 50 percent"}})) == (600, 600)
    assert blink_cycle(T({"motion": {"alive_blink": "blinks"}})) == (2040, 360)           # defaults
    # A 400 ms period floor and a 95 % lit-share cap:
    assert blink_cycle(T({"motion": {"alive_blink": "0.1 s on 99 percent"}})) == (380, 20)
    for none in (T({}), T({"motion": {}}), T({"motion": {"alive_blink": 5}}), T(None), object()):
        assert blink_cycle(none) is None


# ── Blinker ───────────────────────────────────────────────────────────────────

def test_the_blinker_alternates_lit_and_dark_with_one_shot_timers():
    host, seen = FakeHost(), []
    blinker = Blinker(host, seen.append)
    assert not blinker.running and host.timers == {}
    blinker.start((2040, 360))
    assert blinker.running and blinker.lit and host.timer_ms() == [2040]
    (timer,) = host.timers
    host.fire(timer)
    assert seen == [False] and not blinker.lit and host.timer_ms() == [360]
    host.fire(next(iter(host.timers)))
    assert seen == [False, True] and blinker.lit and host.timer_ms() == [2040]
    assert blinker.toggles == 2


def test_stopping_the_blinker_kills_its_timer_and_leaves_the_mark_lit():
    host = FakeHost()
    blinker = Blinker(host, lambda lit: None)
    blinker.start((2040, 360))
    host.fire(next(iter(host.timers)))
    assert not blinker.lit
    blinker.stop()
    assert host.timers == {} and blinker.lit and not blinker.running
    blinker.stop()                                                      # harmless twice


def test_starting_a_running_blinker_does_not_double_its_timers():
    host = FakeHost()
    blinker = Blinker(host, lambda lit: None)
    blinker.start((2040, 360))
    blinker.start((1000, 200))
    assert len(host.timers) == 1
    host.fire(next(iter(host.timers)))
    assert host.timer_ms() == [200]                                     # the new cycle applies from the next change


def test_a_blinker_stopped_from_its_own_callback_does_not_re_arm():
    host = FakeHost()
    holder: dict[str, Blinker] = {}

    def on_toggle(lit):
        holder["b"].stop()

    holder["b"] = Blinker(host, on_toggle)
    holder["b"].start((2040, 360))
    host.fire(next(iter(host.timers)))
    assert host.timers == {}


def test_a_failing_toggle_callback_still_keeps_the_blinker_going():
    host = FakeHost()

    def boom(lit):
        raise RuntimeError("draw failed")

    blinker = Blinker(host, boom)
    blinker.start((2040, 360))
    with pytest.raises(RuntimeError):
        host.fire(next(iter(host.timers)))
    assert len(host.timers) == 1


# ── Alerts ────────────────────────────────────────────────────────────────────

# (the alert engine has its own tests in test_alerts.py; the wiring is tested with the wired app below)


# ── Updates ───────────────────────────────────────────────────────────────────

class FakeChecker:
    """Stands in for update_check.check_async. ``error``: a lookup that fails, as check_async reports it: on_error(error)
    first, then on_result(None). ``synchronous=False``: nothing is answered until a test calls ``pending[0]``."""

    def __init__(self, result: dict[str, Any] | None, *, synchronous: bool = True, error: BaseException | None = None) -> None:
        self.result, self.synchronous, self.error, self.calls, self.pending = result, synchronous, error, [], []

    def __call__(self, repo: str, current: str, on_result: Any, on_error: Any = None) -> None:
        self.calls.append((repo, current))
        if not self.synchronous:
            self.pending.append(on_result)
        elif self.error is not None:
            if on_error is not None:
                on_error(self.error)
            on_result(None)
        else:
            on_result(self.result)


INFO = {"version": "2.1.0", "tag": "v2.1.0", "url": f"https://github.com/{version.REPO}/releases/tag/v2.1.0",
        "asset_url": f"https://github.com/{version.REPO}/releases/download/v2.1.0/Setup.exe", "notes": ""}


def test_an_update_check_stores_what_it_finds(settings):
    checker = FakeChecker(INFO)
    updates = UpdateTracker(settings, checker)
    assert updates.info is None and not updates.checked
    seen: list[Any] = []
    updates.start_check(seen.append)
    assert updates.checked and updates.info == INFO and seen == [INFO]
    assert checker.calls == [(version.REPO, version.APP_VERSION)]


def test_a_check_that_finds_nothing_keeps_an_earlier_find(settings):
    checker = FakeChecker(INFO)
    updates = UpdateTracker(settings, checker)
    updates.start_check()
    checker.result = None                                                # offline the second time
    updates.start_check()
    assert updates.info == INFO


def test_a_skipped_version_is_hidden_from_the_menu_but_not_from_the_api(settings):
    updates = UpdateTracker(settings, FakeChecker(INFO))
    updates.start_check()
    assert updates.offered() == INFO
    assert updates.skip() == {"skipped": "2.1.0"}
    assert settings.get("skip_version") == "2.1.0"
    assert updates.offered() is None and updates.info == INFO
    newer = dict(INFO, version="2.2.0")
    updates._check = FakeChecker(newer)
    updates.start_check()
    assert updates.offered()["version"] == "2.2.0"                       # a newer release is offered again


def test_skipping_with_nothing_to_skip_does_nothing(settings):
    assert UpdateTracker(settings, FakeChecker(None)).skip() == {"skipped": None}
    assert settings.get("skip_version") == ""


def test_the_api_check_waits_for_the_answer(settings):
    updates = UpdateTracker(settings, FakeChecker(INFO))
    assert updates.check_now() == {"available": True, "version": "2.1.0", "checked": True}
    assert UpdateTracker(settings, FakeChecker(None)).check_now() == {"available": False, "version": None, "checked": True}


def test_the_api_check_gives_up_waiting_for_a_slow_check(settings):
    started = time.monotonic()
    result = UpdateTracker(settings, FakeChecker(INFO, synchronous=False)).check_now(timeout=0.05)
    assert result == {"available": False, "version": None, "checked": False} and time.monotonic() - started < 2


@pytest.mark.parametrize("failure", [urllib.error.URLError("offline"), TimeoutError(), OSError("rate limited (HTTP 403)"), ValueError("bad answer")])
def test_the_api_check_says_when_the_lookup_failed(settings, failure):
    """Offline, DNS, GitHub's rate limit and a 5xx all reach the tracker as one failed lookup. It is not an answer of
    "nothing newer", and the dashboard must not say "you are up to date" for it."""
    updates = UpdateTracker(settings, FakeChecker(None, error=failure))
    assert updates.check_now() == {"available": False, "version": None, "checked": False}
    assert updates.info is None


def test_a_failed_lookup_keeps_what_an_earlier_check_found_and_still_says_it_failed(settings):
    checker = FakeChecker(INFO)
    updates = UpdateTracker(settings, checker)
    updates.start_check()
    checker.result, checker.error = None, urllib.error.URLError("offline")          # offline the second time
    assert updates.check_now() == {"available": True, "version": "2.1.0", "checked": False}
    assert updates.info == INFO


def test_each_check_has_its_own_verdict(settings):
    """A failure of one check says nothing about the next: the verdict is that of the lookup just made."""
    checker = FakeChecker(None, error=OSError("offline"))
    updates = UpdateTracker(settings, checker)
    assert updates.check_now()["checked"] is False
    checker.error = None
    assert updates.check_now() == {"available": False, "version": None, "checked": True}
    checker.error = OSError("offline again")
    assert updates.check_now()["checked"] is False


def test_start_check_hands_the_failure_to_the_caller_before_the_done_callback(settings):
    updates = UpdateTracker(settings, FakeChecker(None, error=OSError("offline")))
    order: list[Any] = []
    updates.start_check(lambda info: order.append(("done", info)), lambda exc: order.append(("error", type(exc).__name__)))
    assert order == [("error", "OSError"), ("done", None)]
    order.clear()
    UpdateTracker(settings, FakeChecker(None)).start_check(lambda info: order.append(("done", info)), lambda exc: order.append("error"))
    assert order == [("done", None)], "a lookup that got an answer is never an error"


def test_a_late_answer_after_the_wait_still_counts_for_the_next_look(settings):
    """The wait ended, so the dashboard was told "could not check"; the lookup finishing later still records its find."""
    checker = FakeChecker(INFO, synchronous=False)
    updates = UpdateTracker(settings, checker)
    assert updates.check_now(timeout=0.05)["checked"] is False
    checker.pending[0](INFO)
    assert updates.info == INFO


def test_applying_an_update_opens_the_release_page_when_not_frozen(settings, monkeypatch):
    opened: list[str] = []
    app = MonitorApp(settings=settings, open_url_fn=opened.append)
    app.updates = UpdateTracker(settings, FakeChecker(INFO))
    assert app._apply_update() == {"started": False, "mode": "none"}
    app.updates.start_check()
    monkeypatch.setattr(app_mod.update_check, "can_self_update", lambda info: False)
    assert app._apply_update() == {"started": True, "mode": "browser"}
    assert opened == [INFO["url"]]


def test_applying_an_update_in_the_installed_build_downloads_the_installer(settings, monkeypatch):
    app = MonitorApp(settings=settings)
    app.updates = UpdateTracker(settings, FakeChecker(INFO))
    app.updates.start_check()
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(app_mod.update_check, "can_self_update", lambda info: True)
    monkeypatch.setattr(app_mod.update_check, "apply_update",
                        lambda url, **kw: calls.append((url, sorted(kw))))
    assert app._apply_update() == {"started": True, "mode": "installer"}
    assert calls == [(INFO["asset_url"], ["digest", "on_before_exit", "on_error"])]


def test_an_update_with_a_foreign_installer_link_is_offered_as_the_release_page(settings, monkeypatch):
    """The link comes from the release JSON. update_check.can_self_update, the one gate, refuses a link that is not
    this project's own release download, so the user gets the release page and nothing is downloaded or run."""
    hostile = dict(INFO, asset_url="https://evil.example/Setup.exe", asset_digest="sha256:" + "0" * 64)
    opened: list[str] = []
    app = MonitorApp(settings=settings, open_url_fn=opened.append)
    app.updates = UpdateTracker(settings, FakeChecker(hostile))
    app.updates.start_check()
    monkeypatch.setattr(app_mod.update_check, "is_frozen", lambda: True)      # the real can_self_update decides
    monkeypatch.setattr(app_mod.update_check, "apply_update",
                        lambda url, **kw: pytest.fail("a foreign link must never be downloaded and run"))
    assert app._apply_update() == {"started": True, "mode": "browser"}
    assert opened == [hostile["url"]]


@pytest.mark.parametrize("temp", [
    r"C:\Users\JANEDO~1\AppData\Local\Temp", r"C:\Users\Jane Doe\AppData\Local\Temp",
    r"C:\Users\Tom&Jerry\AppData\Local\Temp", r"C:\Users\Tom & Jerry\Temp", r"C:\Users\100%\Temp",
    r"C:\Users\a^b\Temp", r"C:\Users\Smith,John\Temp", r"C:\Users\a=b\Temp", r"C:\Users\(work)\Temp",
])
def test_an_update_does_not_depend_on_what_the_temp_folder_is_called(settings, monkeypatch, temp):
    """update_check runs a constant helper from a folder of its own with every path in the environment (its tests run it
    under the real cmd.exe in folders like these), so the monitor no longer second-guesses the temp folder's name."""
    monkeypatch.setattr(tempfile, "gettempdir", lambda: temp)
    asset = dict(INFO, asset_digest="sha256:" + "0" * 64)
    app = MonitorApp(settings=settings, open_url_fn=lambda url: pytest.fail("the release page, not the installer"))
    app.updates = UpdateTracker(settings, FakeChecker(asset))
    app.updates.start_check()
    calls: list[str] = []
    monkeypatch.setattr(app_mod.update_check, "is_frozen", lambda: True)
    monkeypatch.setattr(app_mod.update_check, "apply_update", lambda url, **kw: calls.append(url))
    assert app._apply_update() == {"started": True, "mode": "installer"}
    assert calls == [INFO["asset_url"]]


def found(number: str = "2.1.0", **extra: Any) -> dict[str, Any]:
    """What the update check reports for release ``number``."""
    page = f"https://github.com/{version.REPO}/releases/tag/v{number}"
    return {**INFO, "version": number, "tag": f"v{number}", "url": page,
            "asset_url": f"https://github.com/{version.REPO}/releases/download/v{number}/Setup.exe", **extra}


def test_the_update_as_the_dashboard_gets_it_adds_the_release_page_and_whether_it_installs_in_place(settings, monkeypatch):
    updates = UpdateTracker(settings, FakeChecker(INFO))
    assert updates.api_info() is None
    updates.start_check()
    monkeypatch.setattr(app_mod.update_check, "can_self_update", lambda info: False)
    shown = updates.api_info()
    assert shown == dict(INFO, url=INFO["url"], can_self_update=False)
    monkeypatch.setattr(app_mod.update_check, "can_self_update", lambda info: True)
    assert updates.api_info()["can_self_update"] is True
    assert updates.info == INFO and "can_self_update" not in updates.info          # the tracker's own copy is untouched


def test_update_now_installs_in_place_only_where_the_real_gate_says_so(settings, monkeypatch):
    """can_self_update (update_check) is the one gate: the installed build, this project's own installer, a SHA-256."""
    def installs(info: dict[str, Any], frozen: bool) -> bool:
        monkeypatch.setattr(app_mod.update_check, "is_frozen", lambda: frozen)
        updates = UpdateTracker(settings, FakeChecker(info))
        updates.start_check()
        return updates.api_info()["can_self_update"]

    digest = "sha256:" + "a" * 64
    assert installs(found(asset_digest=digest), frozen=True) is True
    assert installs(found(asset_digest=digest), frozen=False) is False                       # from source: the release page
    assert installs(found(), frozen=True) is False                                           # no checksum: never silently
    assert installs(found(asset_digest=digest, asset_url="https://evil.example/Setup.exe"), frozen=True) is False
    assert installs(found(asset_digest=digest, asset_url=None), frozen=True) is False


@pytest.mark.parametrize("bad", [{"url": "https://evil.example/x"}, {"url": "javascript:alert(1)"}, {"url": None},
                                 {"url": ""}, {"url": 5}, {}])
def test_the_release_page_the_dashboard_gets_is_always_a_github_page(settings, bad):
    without_url = {key: value for key, value in INFO.items() if key != "url"}
    updates = UpdateTracker(settings, FakeChecker({**without_url, **bad}))
    updates.start_check()
    assert updates.api_info()["url"] == f"https://github.com/{version.REPO}/releases/latest"


def test_nothing_found_is_nothing_to_announce(settings):
    updates = UpdateTracker(settings, FakeChecker(None))
    updates.start_check()
    assert updates.announcement() is None


def test_a_found_release_is_announced_once_and_then_never_again(settings):
    updates = UpdateTracker(settings, FakeChecker(INFO))
    updates.start_check()
    assert updates.announcement() == INFO
    assert updates.announcement() == INFO                                           # asking does not use it up
    updates.mark_announced(INFO)
    assert updates.announcement() is None
    assert settings.get("notified_version") == "2.1.0"
    updates.start_check()                                                           # found again by a later check
    assert updates.announcement() is None


def test_a_newer_release_is_announced_again_and_an_older_one_never(settings):
    checker = FakeChecker(found("2.1.0"))
    updates = UpdateTracker(settings, checker)
    updates.start_check()
    updates.mark_announced()
    checker.result = found("2.2.0")
    updates.start_check()
    assert updates.announcement()["version"] == "2.2.0"
    updates.mark_announced()
    checker.result = found("2.1.5")                                                 # 2.2.0 was pulled: an older release is the latest again
    updates.start_check()
    assert updates.info["version"] == "2.1.5"
    assert updates.announcement() is None                                           # 2.2.0 was already told about: 2.1.5 is not news
    updates.mark_announced()
    assert settings.get("notified_version") == "2.2.0", "the mark is only ever raised"


def test_a_skipped_release_is_not_announced_and_the_next_one_is(settings):
    updates = UpdateTracker(settings, FakeChecker(found("2.1.0")))
    updates.start_check()
    assert updates.skip() == {"skipped": "2.1.0"}
    assert updates.announcement() is None and updates.info is not None               # still known, never announced
    updates._check = FakeChecker(found("2.2.0"))
    updates.start_check()
    assert updates.announcement()["version"] == "2.2.0"


def test_nothing_is_announced_while_the_update_check_is_off(settings):
    updates = UpdateTracker(settings, FakeChecker(INFO))
    updates.start_check()
    settings.set("check_updates", False)
    assert updates.announcement() is None
    settings.set("check_updates", True)
    assert updates.announcement() == INFO


@pytest.mark.parametrize("number", ["2.1.0<b>", "2.1.0 beta", "x" * 41, "", None, 5])
def test_a_version_that_is_not_plain_is_never_put_in_a_toast(settings, number):
    updates = UpdateTracker(settings, FakeChecker(dict(INFO, version=number)))
    updates.start_check()
    assert updates.announcement() is None
    updates.mark_announced()
    assert settings.get("notified_version") is None


@pytest.mark.parametrize("stored", [5, None, ["2.1.0"], "", "nightly", "garbage"])
def test_a_notified_version_that_is_not_a_version_announces_the_release(settings, stored):
    """The settings file keeps unknown keys exactly as written, so a hand edit can put anything there."""
    settings.set("notified_version", stored)
    updates = UpdateTracker(settings, FakeChecker(INFO))
    updates.start_check()
    assert updates.announcement() == INFO
    updates.mark_announced()
    assert settings.get("notified_version") == "2.1.0"


def test_the_toast_title_names_the_version():
    assert app_mod.update_toast_title("2.1.0") == "Claude Usage Monitor v2.1.0 is available"
    assert app_mod.UPDATE_TOAST_BODY == "Open the tray menu or the dashboard to update"
    assert len(app_mod.update_toast_title("3.10.12-rc.1")) < 64                      # the toast title limit (tray.TITLE_LIMIT)


def test_marking_from_many_threads_leaves_the_newest_version(settings, monkeypatch):
    """The UI thread (a toast) and an API thread (a check by hand) both mark; whatever the order, the mark only rises.
    Reading the mark is slowed down so that the threads really do overlap: without the tracker's lock around the
    read-compare-write, the last writer (the lowest version here) would win."""
    updates = UpdateTracker(settings, FakeChecker(None))
    real_get = settings.get

    def slow_get(key, default=None):
        value = real_get(key, default)
        if key == "notified_version":
            time.sleep(0.003)
        return value

    monkeypatch.setattr(settings, "get", slow_get)
    numbers = [f"2.{n}.0" for n in range(1, 40)]
    threads = [threading.Thread(target=updates.mark_announced, args=({"version": number},)) for number in reversed(numbers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert real_get("notified_version") == "2.39.0"


def local_time(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, 21, hour, minute).astimezone()


@pytest.mark.parametrize("quiet, hour, minute, expected", [
    ({"start": "22:00", "end": "07:00"}, 23, 0, True), ({"start": "22:00", "end": "07:00"}, 3, 30, True),
    ({"start": "22:00", "end": "07:00"}, 22, 0, True), ({"start": "22:00", "end": "07:00"}, 7, 0, False),
    ({"start": "22:00", "end": "07:00"}, 12, 0, False), ({"start": "22:00", "end": "07:00"}, 21, 59, False),
    ({"start": "09:00", "end": "17:00"}, 9, 0, True), ({"start": "09:00", "end": "17:00"}, 16, 59, True),
    ({"start": "09:00", "end": "17:00"}, 17, 0, False), ({"start": "09:00", "end": "17:00"}, 8, 59, False),
    ({"start": "08:00", "end": "08:00"}, 8, 0, False),                          # equal times: no quiet hours at all
    (None, 3, 0, False), ("22:00-07:00", 3, 0, False), ({"start": "22:00"}, 23, 0, False),
    ({"start": "25:00", "end": "07:00"}, 3, 0, False), ({"start": 22, "end": 7}, 3, 0, False), ([], 3, 0, False),
])
def test_quiet_hours_are_read_the_way_the_alert_engine_reads_them(quiet, hour, minute, expected):
    assert app_mod.in_quiet_hours(quiet, local_time(hour, minute)) is expected


def test_quiet_hours_use_local_time_not_utc():
    quiet = {"start": "10:00", "end": "11:00"}
    inside = datetime(2026, 10, 21, 10, 30).astimezone()                           # 10:30 on this PC's clock
    assert app_mod.in_quiet_hours(quiet, inside) is True
    assert app_mod.in_quiet_hours(quiet, inside.astimezone(timezone.utc)) is True   # the same instant, written in UTC


def test_the_data_service_pauses_on_a_locked_desktop_unless_the_caller_says_otherwise(monkeypatch, settings):
    made: list[Any] = []

    class CapturingService:
        def __init__(self, settings, *, pause_check, status_dir):
            made.append(pause_check)
            assert status_dir == app_mod.paths.app_data_dir()  # it writes the status file Claude sessions read

    monkeypatch.setattr(app_mod, "DataService", CapturingService)
    fake_plat = FakePlatform()
    MonitorApp(settings=settings, platform_api=fake_plat)._service_factory(settings)
    MonitorApp(settings=settings, platform_api=fake_plat, pause_check=lambda: False)._service_factory(settings)
    assert made[0] == fake_plat.should_pause                                  # locked or screensaver (v1's rule)
    assert made[1]() is False                                                 # the smoke run never pauses


# ── MonitorApp wired to fakes ─────────────────────────────────────────────────

class Events:
    """An ordered record of what the fakes were asked to do."""

    def __init__(self) -> None:
        self.log: list[str] = []

    def __call__(self, name: str) -> None:
        self.log.append(name)


class FakeService:
    def __init__(self, events: Events, state: m.MonitorState | None = None) -> None:
        self.events, self.state, self.subscribers, self.refreshes = events, state, [], 0

    def subscribe(self, cb):
        self.subscribers.append(cb)
        return lambda: self.subscribers.remove(cb)

    def start(self):
        self.events("service.start")

    def stop(self, timeout: float = 5.0):
        self.events("service.stop")

    def refresh_now(self):
        self.refreshes += 1

    def publish(self, state):
        self.state = state
        for cb in list(self.subscribers):
            cb(state)


class FakeTray:
    instances: list[FakeTray] = []
    events: Events | None = None

    def __init__(self, host, tooltip, on_left_click, on_menu, **_kw) -> None:
        self.tooltip, self.on_left_click, self.on_menu = tooltip, on_left_click, on_menu
        self.images: list[dict[int, Image.Image]] = []
        self.tooltips = [tooltip]
        self.notices: list[tuple[str, str]] = []
        self.removed = False
        self.added = True
        self.icon_size = 16
        self.on_notify_click = None
        FakeTray.instances.append(self)

    def set_images(self, images):
        self.images.append(dict(images))

    def set_tooltip(self, text):
        if text != self.tooltips[-1]:
            self.tooltips.append(text)

    def notify(self, title, body):
        self.notices.append((title, body))
        return True

    def remove(self):
        self.removed = True
        if self.events:
            self.events("tray.remove")


class FakeServer:
    events: Events | None = None

    def __init__(self, state_provider, settings, **kwargs) -> None:
        self.state_provider, self.kwargs = state_provider, kwargs
        self.token, self.port, self.stopped = "tok", 4321, False

    def start(self):
        return self.port

    def stop(self):
        self.stopped = True
        if self.events:
            self.events("server.stop")


class FakeLauncher:
    events: Events | None = None

    def __init__(self, server, settings) -> None:
        self.opened = 0
        self.closed = 0
        self.pid = None
        self.running = False

    def open(self):
        self.opened += 1
        return True

    def close(self, timeout: float = 5.0):
        self.closed += 1
        if self.events:
            self.events("dashboard.close")


@dataclasses.dataclass
class Wired:
    """A MonitorApp built (not run) on fakes, with the fakes within reach."""

    app: MonitorApp
    host: FakeHost
    service: FakeService
    events: Events
    plat: FakePlatform
    windows: WindowFactory
    tray: FakeTray
    settings: Settings


def build_app(monkeypatch, settings, **app_kwargs) -> Wired:
    """Build a MonitorApp the way run() does, with every Win32-facing piece replaced by a fake."""
    FakeTray.instances.clear()
    events = Events()
    factory = WindowFactory()
    fake_plat = FakePlatform()
    host = FakeHost()
    for fake in (FakeTray, FakeServer, FakeLauncher):
        fake.events = events
    monkeypatch.setattr(app_mod, "TrayIcon", FakeTray)
    monkeypatch.setattr(app_mod, "ApiServer", FakeServer)
    monkeypatch.setattr(app_mod, "DashboardLauncher", FakeLauncher)
    monkeypatch.setattr(app_mod, "FloatingWidget", functools.partial(FloatingWidget, window_factory=factory))
    monkeypatch.setattr(app_mod, "TaskbarWidget", functools.partial(TaskbarWidget, window_factory=factory))
    monkeypatch.setattr(app_mod, "Tooltip", functools.partial(Tooltip, window_factory=factory))
    service = FakeService(events)
    kwargs: dict[str, Any] = dict(
        dev=True, settings=settings, service_factory=lambda s: service, update_check_fn=FakeChecker(None),
        platform_api=fake_plat, clock=lambda: NOW, open_url_fn=lambda url: events(f"url {url}"),
    )
    kwargs.update(app_kwargs)
    app = MonitorApp(**kwargs)
    app.host = host
    app.settings = settings
    app._build()
    host.run_posted()
    return Wired(app, host, service, events, fake_plat, factory, FakeTray.instances[-1], settings)


@pytest.fixture
def wired(monkeypatch, settings) -> Wired:
    return build_app(monkeypatch, settings)


def test_building_the_app_draws_the_widgets_the_tray_and_starts_the_service(wired):
    app = wired.app
    assert app.floating is not None and app.taskbar is not None
    assert wired.service.subscribers and wired.events.log == ["service.start"]
    assert wired.tray.tooltips == ["Claude usage - loading (dev)"]
    assert {16, 20, 24, 32} == set(wired.tray.images[-1])                 # all four DPI sizes
    assert app.floating.visible and app.taskbar.visible
    assert wired.host.cleanups[-1] == app._teardown, "the app's shutdown runs before the windows' own"


def test_the_server_is_given_the_real_callbacks(wired):
    kwargs = wired.app.server.kwargs
    assert kwargs["refresh_cb"] == wired.service.refresh_now
    assert set(kwargs["update_cb"]) == {"info", "check", "apply", "skip"}
    assert kwargs["dev"] is True and kwargs["version"] == version.APP_VERSION
    assert kwargs["update_cb"]["info"]() is None


def test_the_statistics_store_is_a_new_connection_on_the_usage_database(wired, isolated_env):
    """The server opens its own Store on the file the data service writes, and only when the first
    statistics request comes. (The fake service here writes nothing, so the test makes the file.)"""
    factory = wired.app.server.kwargs["analytics_store"]
    assert factory is app_mod.analytics_store
    Store(isolated_env / "usage.db").close()
    first, second = factory(), factory()
    try:
        assert first is not second and first.table_counts()["requests"] == 0
    finally:
        first.close()
        second.close()


def test_the_statistics_store_never_creates_the_database(isolated_env):
    """Only the data service makes usage.db: a statistics request that came first would race its migration."""
    with pytest.raises(sqlite3.OperationalError, match="not been created"):
        app_mod.analytics_store()
    assert list(isolated_env.glob("usage.db*")) == []


def test_the_statistics_store_never_upgrades_the_database(isolated_env):
    """A database from before this build's schema is the data service's to upgrade; opening a Store on it
    would migrate it from the API's thread and could collide with the service's own migration."""
    db = isolated_env / "usage.db"
    Store(db).close()
    connection = sqlite3.connect(db)
    connection.execute("UPDATE meta SET value = '2' WHERE key = 'schema_version'")
    connection.commit()
    connection.close()
    with pytest.raises(sqlite3.OperationalError, match="schema 2"):
        app_mod.analytics_store()
    connection = sqlite3.connect(db)
    try:
        assert connection.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == ("2",)
    finally:
        connection.close()                                                      # still 2: nothing migrated it


def test_the_statistics_store_refuses_an_empty_file_and_a_newer_schema(isolated_env):
    from usage_monitor.data.store import StoreError

    isolated_env.mkdir(parents=True, exist_ok=True)
    db = isolated_env / "usage.db"
    db.write_bytes(b"")
    with pytest.raises(sqlite3.OperationalError):                                # no meta table yet: mid-creation
        app_mod.analytics_store()
    db.unlink()
    Store(db).close()
    connection = sqlite3.connect(db)
    connection.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
    connection.commit()
    connection.close()
    with pytest.raises(StoreError):
        app_mod.analytics_store()


def test_the_server_reads_the_pricing_table_from_the_data_service(wired):
    provider = wired.app.server.kwargs["pricing_provider"]
    assert provider() is None                                     # the fake service has loaded none
    wired.service._pricing = "the table the service keeps"
    assert provider() == "the table the service keeps"
    wired.service.pricing = "a public accessor wins when the service grows one"
    assert provider() == "a public accessor wins when the service grows one"
    assert app_mod.service_pricing(object()) is None


def test_the_window_finder_the_app_uses_is_the_dashboards_public_one():
    from usage_monitor.ui import dashboard

    assert app_mod.dashboard_window_of is dashboard.find_window_of is dashboard._find_window_of


def test_a_state_from_the_data_thread_is_marshalled_to_the_ui_thread_once(wired):
    posted = len(wired.host.posted)
    done = threading.Event()

    def publish_from_another_thread():
        for percent in (20, 30, 40):
            state = sample_state("normal3", NOW)
            state.usage.limits[0].percent = percent
            wired.service.publish(state)
        done.set()

    threading.Thread(target=publish_from_another_thread).start()
    assert done.wait(5)
    assert len(wired.host.posted) == posted + 1, "three publishes, one pull"
    assert wired.tray.tooltips[-1] == "Claude usage - loading (dev)"         # nothing drawn until the UI thread runs it
    wired.host.run_posted()
    assert wired.tray.tooltips[-1] == "Claude usage - session 40% - weekly 2% (dev)"   # the latest state won


def test_new_data_redraws_every_surface_and_the_tray(wired):
    app = wired.app
    before = app.stats.snapshot()["rendered"]
    wired.service.publish(sample_state("warning", NOW))
    wired.host.run_posted()
    after = app.stats.snapshot()["rendered"]
    assert after["floating"] == before["floating"] + 1 and after["taskbar"] == before["taskbar"] + 1
    assert after["tray"] == before["tray"] + 1
    assert wired.tray.tooltips[-1] == "Claude usage - session 78% - weekly 21% (dev)"


def test_the_tray_icon_is_rebuilt_only_when_what_it_shows_changes(wired):
    wired.service.publish(sample_state("normal3", NOW))
    wired.host.run_posted()
    images = len(wired.tray.images)
    for seconds in (1, 2, 3):                                              # the age text moves, the icon does not
        wired.app._clock = lambda s=seconds: NOW + timedelta(seconds=s)
        wired.app._refresh()
    assert len(wired.tray.images) == images
    wired.service.publish(sample_state("warning", NOW))
    wired.host.run_posted()
    assert len(wired.tray.images) == images + 1


def test_settings_changes_are_coalesced_and_unrelated_keys_are_ignored(wired):
    host, settings = wired.host, wired.settings
    host.run_posted()
    settings.set("poll_interval_ms", 120000)                              # the data service's business
    settings.set("dashboard_bounds", {"x": 1, "y": 2, "w": 800, "h": 600})
    settings.set("_runtime", 1)
    assert host.posted == []
    settings.update({"float_text_fx": False, "taskbar_text_fx": False, "reduce_motion": True})
    assert len(host.posted) == 1, "three changes in one update are one redraw"
    before = wired.app.stats.snapshot()["rendered"]
    host.run_posted()
    after = wired.app.stats.snapshot()["rendered"]
    assert after["floating"] > before["floating"] and after["taskbar"] > before["taskbar"]


def test_a_theme_switch_redraws_everything_live_in_the_new_theme(wired):
    app, host, settings = wired.app, wired.host, wired.settings
    assert app._ctx.theme.id == "nostromo"
    settings.update({"theme_id": "night_city", "theme_variant": "blackwall"})
    host.run_posted()
    assert (app._ctx.theme.id, app._ctx.variant.id) == ("night_city", "blackwall")
    assert app._ctx.skin is not None
    settings.update({"theme_id": "gates", "theme_variant": "blue", "theme_mode": "dark"})
    host.run_posted()
    assert (app._ctx.theme.id, app._ctx.variant.id) == ("gates", "blue-dark")
    assert wired.tray.images, "the tray icon follows the theme too"


def test_a_theme_switch_rebuilds_the_tray_icon_even_when_the_numbers_are_the_same(wired):
    wired.service.publish(sample_state("normal3", NOW))
    wired.host.run_posted()
    images = len(wired.tray.images)
    wired.settings.update({"theme_id": "night_city", "theme_variant": "blackwall"})
    wired.host.run_posted()
    assert len(wired.tray.images) == images + 1


def test_the_widget_toggles_create_and_destroy_windows(wired):
    app, host, settings = wired.app, wired.host, wired.settings
    floating_window = app.floating.window
    settings.set("show_floating", False)
    host.run_posted()
    assert app.floating is None and floating_window.destroyed
    settings.set("show_floating", True)
    host.run_posted()
    assert app.floating is not None and app.floating.window is not floating_window and app.floating.visible
    settings.set("show_taskbar", False)
    host.run_posted()
    assert app.taskbar is None
    settings.set("show_taskbar", True)
    host.run_posted()
    assert app.taskbar is not None and app.taskbar.visible


def test_the_menu_reflects_and_flips_the_widget_toggles(wired):
    app, host, settings = wired.app, wired.host, wired.settings
    items = {i.label: i for i in app.menu_items() if not i.separator}
    assert items["Floating widget"].checked and items["Taskbar widget"].checked
    items["Floating widget"].action()
    host.run_posted()
    assert settings.get("show_floating") is False and app.floating is None
    items = {i.label: i for i in app.menu_items() if not i.separator}
    assert not items["Floating widget"].checked and items["Taskbar widget"].checked


def test_the_menu_actions_open_the_dashboard_refresh_report_and_exit(wired):
    app = wired.app
    items = {i.label: i for i in app.menu_items() if not i.separator}
    items["Open dashboard"].action()
    assert app.launcher.opened == 1
    items["Refresh now"].action()
    assert wired.service.refreshes == 1
    items["Report a bug"].action()
    assert wired.events.log[-1] == f"url {bug_report_url()}"
    items["Exit"].action()
    assert wired.host.posted, "Exit asks the host to quit"


def test_the_tray_and_the_widgets_open_the_dashboard_and_show_the_same_menu(wired, monkeypatch):
    app = wired.app
    shown: list[tuple[int, int, list[str]]] = []
    monkeypatch.setattr(app_mod, "show_menu", lambda host, items, x, y: shown.append((x, y, [i.label for i in items])))
    wired.tray.on_left_click()
    app.floating.window.fire("on_double_click", 1, 1)
    app.taskbar.window.fire("on_click", 1, 1)
    assert app.launcher.opened == 3
    wired.tray.on_menu(100, 200)
    app.floating.window.fire("on_right_click", 300, 400)
    app.taskbar.window.fire("on_right_click", 500, 600)
    assert [(x, y) for x, y, _ in shown] == [(100, 200), (300, 400), (500, 600)]
    assert shown[0][2] == shown[1][2] == shown[2][2]


def test_the_update_item_appears_in_the_menu_when_an_update_is_found_and_not_when_skipped(wired):
    app = wired.app
    assert "Update to v2.1.0" not in [i.label for i in app.menu_items()]
    app.updates._info = dict(INFO)
    assert "Update to v2.1.0" in [i.label for i in app.menu_items()]
    app.updates.skip()
    assert "Update to v2.1.0" not in [i.label for i in app.menu_items()]


@pytest.mark.parametrize("number", ["2.1.0&Exit", "2.1.0\n", "x" * 41, "", None])
def test_a_version_that_is_not_plain_gets_no_menu_item(wired, number):
    """The version comes off the network; the menu label is built from it, so it has to be a plain version."""
    wired.app.updates._info = dict(INFO, version=number)
    assert not any(item.label.startswith("Update") for item in wired.app.menu_items())


def test_a_second_launch_message_opens_the_dashboard(wired):
    wired.host.emit("open_dashboard")
    assert wired.app.launcher.opened == 1


def test_a_windows_taskbar_or_display_change_re_places_the_widgets(wired):
    app = wired.app
    app.taskbar.window.hide()
    wired.plat.info = taskbar(96)
    wired.host.emit("taskbar")
    assert app.taskbar.visible
    app.floating.window.hide()
    wired.plat.areas = [Rect(0, 0, 1280, 984)]
    wired.host.emit("display")
    assert app.floating.window.rect.right == 1280 - 12


def test_a_windows_colour_change_re_resolves_the_theme(wired, monkeypatch):
    app = wired.app
    wired.settings.update({"theme_id": "gates", "theme_variant": "system", "theme_mode": "dark"})
    wired.host.run_posted()
    first = app._ctx.variant.colors["accent"]
    monkeypatch.setattr(plat, "windows_accent_hex", lambda: "#12ab34")
    wired.host.emit("settings", "ImmersiveColorSet")
    # the accent in use is the new Windows accent made readable on a dark card (lifted in lightness), not the old one
    expected = themes.resolve({"theme_id": "gates", "theme_variant": "system", "theme_mode": "dark"},
                              accent_provider=lambda: "#12ab34", light_provider=lambda: False)[1]
    assert app._ctx.variant.colors["accent"] == expected.colors["accent"] != first
    assert app._ctx.variant.extras["accent_fill"] == expected.extras["accent_fill"]
    wired.host.emit("settings", "app")                                       # the app's own broadcasts are ignored here


def test_the_clock_tick_redraws_and_re_arms_with_the_right_delay(wired):
    app, host = wired.app, wired.host
    state = sample_state("normal3", NOW)
    state.usage_fetched_at = NOW - timedelta(seconds=12.3)
    wired.service.publish(state)
    host.run_posted()
    ms, _fn, repeat = host.timers[app._tick_timer]
    assert ms == tick_delay_ms(12.3) and repeat is False
    rendered = app.stats.snapshot()["rendered"]["floating"]
    app._clock = lambda: NOW + timedelta(seconds=ms / 1000)
    host.fire(app._tick_timer)
    assert app.stats.snapshot()["rendered"]["floating"] == rendered + 1       # the age readout moved on
    assert len([t for t in host.timers.values() if t[2] is False]) >= 1       # and the next tick is armed


def test_with_nothing_to_age_the_clock_ticks_every_thirty_seconds(wired):
    ms, _fn, _repeat = wired.host.timers[wired.app._tick_timer]
    assert ms == 30000


def test_locking_the_session_stops_the_animation_and_the_clock_and_unlocking_redraws(wired):
    app, host = wired.app, wired.host
    wired.service.publish(sample_state("normal3", NOW))
    host.run_posted()
    assert app.blinker.running and app._tick_timer is not None
    host.locked = True
    host.emit("session", True)
    assert not app.blinker.running and app._tick_timer is None
    assert host.timer_ms() == [500, app_mod.UPDATE_RECHECK_S * 1000], \
        "left: the taskbar strip's keep-alive and the six-hourly update re-check (a locked PC still hears about a release)"
    rendered = app.stats.snapshot()["rendered"]["floating"]
    host.locked = False
    host.emit("session", False)
    assert app.blinker.running and app._tick_timer is not None
    assert wired.service.refreshes == 1, "unlock asks the data service for fresh numbers"
    assert app.stats.snapshot()["rendered"]["floating"] >= rendered


def test_the_blink_runs_only_for_a_live_nostromo_with_a_visible_widget_and_motion_allowed(wired):
    app, host, settings = wired.app, wired.host, wired.settings

    def blinking() -> bool:
        return app.blinker.running

    wired.service.publish(sample_state("normal3", NOW))
    host.run_posted()
    assert blinking()
    settings.set("reduce_motion", True)
    host.run_posted()
    assert not blinking() and app.blinker.lit
    settings.set("reduce_motion", False)
    host.run_posted()
    assert blinking()
    settings.update({"theme_id": "night_city", "theme_variant": "blackwall"})
    host.run_posted()
    assert not blinking(), "Night City pulses; it never blinks"
    settings.update({"theme_id": "nostromo", "theme_variant": "amber"})
    host.run_posted()
    assert blinking()
    wired.service.publish(sample_state("stale", NOW))
    host.run_posted()
    assert not blinking(), "a stale mark is a hollow ring, not a blinking dot"
    wired.service.publish(sample_state("normal3", NOW))
    host.run_posted()
    assert blinking()
    settings.set("show_floating", False)
    host.run_posted()
    assert blinking(), "the strip is still there to blink"
    wired.plat.fullscreen = True
    app.taskbar.keep()                                                        # a fullscreen app hides the strip
    assert not blinking(), "nothing visible, nothing to animate"
    wired.plat.fullscreen = False
    app.taskbar.keep()                                                        # and the strip comes back with it
    assert blinking()


def test_each_blink_toggle_swaps_cached_frames_and_renders_nothing_after_the_first_cycle(wired):
    app, host = wired.app, wired.host
    wired.service.publish(sample_state("normal3", NOW))
    host.run_posted()
    blink_timer = next(t for t, v in host.timers.items() if v[0] == 2040)
    host.fire(blink_timer)                                                   # to dark: one new render per surface
    rendered = app.stats.snapshot()["rendered"]
    clock = [0.0]
    app._clock = lambda: NOW + timedelta(seconds=clock[0])                   # the clock keeps moving between toggles,
    for _ in range(6):                                                       # as it does for real: the time rails move
        clock[0] += 2.4
        host.fire(next(t for t, v in host.timers.items() if v[0] == 360))
        clock[0] += 0.4
        host.fire(next(t for t, v in host.timers.items() if v[0] == 2040))
    assert app.stats.snapshot()["rendered"] == rendered, "a blink must not rebuild the model or render again"
    assert app.blinker.toggles == 13
    host.fire(app._tick_timer)                                               # a clock tick does render the new minute
    assert app.stats.snapshot()["rendered"]["floating"] == rendered["floating"] + 1


def test_a_blink_leaves_the_published_numbers_alone_between_ticks(wired):
    app, host = wired.app, wired.host
    wired.service.publish(sample_state("normal3", NOW))
    host.run_posted()
    model_before = app._ctx.model
    host.fire(next(t for t, v in host.timers.items() if v[0] == 2040))
    assert app._ctx.model == dataclasses.replace(model_before, blink_on=False)
    assert app._ctx.model.rows is model_before.rows or app._ctx.model.rows == model_before.rows


def usage_state(session: float, weekly: float = 2.0, *, now: datetime = NOW, resets_in_hours: float = 3.0,
                status: m.ApiStatus = m.ApiStatus.LIVE, spend: m.Spend | None = None) -> m.MonitorState:
    """A published state: SESSION and WEEKLY at the given percents, the session window ending in some hours."""
    state = sample_state("normal3", now)
    state.usage.limits[0].percent = session
    state.usage.limits[0].resets_at = now + timedelta(hours=resets_in_hours)
    state.usage.limits[1].percent = weekly
    state.usage.spend = spend
    state.status = status
    return state


def publish(wired, state) -> list[tuple[str, str]]:
    """Publish ``state`` from the data thread, let the UI thread pick it up, return the toasts it raised."""
    before = len(wired.tray.notices)
    wired.service.publish(state)
    wired.host.run_posted()
    return wired.tray.notices[before:]


def test_the_app_checks_alerts_with_the_alert_engine_built_on_its_settings(wired):
    assert isinstance(wired.app._alerts, AlertEngine)
    wired.settings.set("alert_thresholds", [40])
    assert publish(wired, usage_state(10)) == []                                   # the baseline
    assert [title for title, _ in publish(wired, usage_state(45))] == ["Session limit at 40%"]


def test_an_alert_toast_shows_the_alerts_title_and_message(wired):
    publish(wired, usage_state(13))
    toasts = publish(wired, usage_state(85))
    assert [title for title, _ in toasts] == ["Session limit at 80%"]
    assert re.fullmatch(r"Resets \d{1,2}:\d{2} (AM|PM)", toasts[0][1])
    assert wired.tray.notices == toasts


def test_each_threshold_crossing_is_one_toast_and_a_level_already_passed_is_not_repeated(wired):
    publish(wired, usage_state(13))
    assert [t for t, _ in publish(wired, usage_state(85))] == ["Session limit at 80%"]
    assert publish(wired, usage_state(86)) == [] and publish(wired, usage_state(89.9)) == []
    assert [t for t, _ in publish(wired, usage_state(93))] == ["Session limit at 90%"]
    assert [t for t, _ in publish(wired, usage_state(100))] == ["Session limit at 100%"]
    assert publish(wired, usage_state(100)) == [] and publish(wired, usage_state(104)) == []
    assert len(wired.tray.notices) == 3


def test_a_limit_that_is_already_high_when_the_app_starts_does_not_toast(wired):
    assert publish(wired, usage_state(95)) == []                                   # the first reading is the baseline
    assert publish(wired, usage_state(96)) == []
    assert wired.tray.notices == []


def test_the_weekly_limit_toasts_with_its_reset_day(wired):
    publish(wired, usage_state(5, weekly=60))
    toasts = publish(wired, usage_state(5, weekly=82))
    assert [title for title, _ in toasts] == ["Weekly limit at 80%"]
    assert re.fullmatch(r"Resets (Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{1,2}:\d{2} (AM|PM)", toasts[0][1])


def test_a_new_window_after_a_high_reading_toasts_the_reset(wired):
    publish(wired, usage_state(92, resets_in_hours=1))
    publish(wired, usage_state(93, resets_in_hours=1))
    toasts = publish(wired, usage_state(4, resets_in_hours=5))
    assert [title for title, _ in toasts] == ["Session limit reset"]
    assert publish(wired, usage_state(5, resets_in_hours=5)) == []


def test_credits_in_use_toast_once_a_day(wired):
    publish(wired, usage_state(5, spend=m.Spend(enabled=True, used=1.0, limit=50.0, currency="USD")))
    toasts = publish(wired, usage_state(5, spend=m.Spend(enabled=True, used=2.5, limit=50.0, currency="USD")))
    assert [title for title, _ in toasts] == ["Usage credits in use"] and "2.50" in toasts[0][1]
    assert publish(wired, usage_state(5, spend=m.Spend(enabled=True, used=3.5, limit=50.0, currency="USD"))) == []


def test_a_fullscreen_app_in_front_holds_the_toast_back_for_good(wired):
    publish(wired, usage_state(13))
    wired.plat.fullscreen = True
    assert publish(wired, usage_state(85)) == []                                   # crossed behind the game
    wired.plat.fullscreen = False
    assert publish(wired, usage_state(86)) == []                                   # and not toasted in a burst later
    assert [t for t, _ in publish(wired, usage_state(93))] == ["Session limit at 90%"]   # the next level still is


def test_the_engine_is_told_about_the_fullscreen_app_on_every_state(wired, monkeypatch):
    seen: list[bool] = []
    real = wired.app._alerts.evaluate
    monkeypatch.setattr(wired.app._alerts, "evaluate",
                        lambda state, *, suppressed=False: seen.append(suppressed) or real(state, suppressed=suppressed))
    publish(wired, usage_state(13))
    wired.plat.fullscreen = True
    publish(wired, usage_state(14))
    wired.plat.fullscreen = False
    publish(wired, usage_state(15))
    assert seen == [False, True, False]


def test_a_foreground_check_that_fails_does_not_cost_the_alert(wired, monkeypatch):
    def broken() -> bool:
        raise OSError("no foreground window")

    publish(wired, usage_state(13))
    monkeypatch.setattr(wired.plat, "is_fullscreen_foreground", broken)
    assert [t for t, _ in publish(wired, usage_state(85))] == ["Session limit at 80%"]


def test_the_alert_settings_apply_to_the_next_state(wired):
    settings = wired.settings
    publish(wired, usage_state(13))
    settings.set("alerts_enabled", False)
    assert publish(wired, usage_state(85)) == []                                   # the master switch
    settings.set("alerts_enabled", True)
    settings.set("alert_on_threshold", False)
    assert publish(wired, usage_state(93)) == []                                   # the threshold kind alone
    settings.set("alert_on_threshold", True)
    settings.set("alert_thresholds", [95, 99])
    assert publish(wired, usage_state(94)) == []                                   # raised above the reading
    assert [t for t, _ in publish(wired, usage_state(97))] == ["Session limit at 95%"]


def test_quiet_hours_hold_the_toast_back(wired):
    wired.settings.set("alert_quiet_hours", {"start": "00:00", "end": "23:59"})
    publish(wired, usage_state(13))
    assert publish(wired, usage_state(85)) == []
    wired.settings.set("alert_quiet_hours", None)
    assert [t for t, _ in publish(wired, usage_state(93))] == ["Session limit at 90%"]


def test_alerts_come_from_live_data_only(wired):
    assert publish(wired, usage_state(95, status=m.ApiStatus.STALE)) == []
    nothing_yet = usage_state(0, status=m.ApiStatus.LOADING)
    nothing_yet.usage = None
    assert publish(wired, nothing_yet) == []
    publish(wired, usage_state(40))                                                # the first live reading seeds
    assert [t for t, _ in publish(wired, usage_state(94))] == ["Session limit at 90%"]


def test_a_toast_that_fails_does_not_stop_the_others_or_the_redraw(wired, caplog):
    def refuse(title, body):
        raise OSError("the shell refused the balloon")

    publish(wired, usage_state(13, weekly=10))
    wired.tray.notify = refuse
    rendered = wired.app.stats.snapshot()["rendered"]["floating"]
    with caplog.at_level(logging.ERROR, logger="usage_monitor.app"):
        wired.service.publish(usage_state(93, weekly=95))                          # two limits cross at once
        wired.host.run_posted()
    assert sum("could not show a threshold alert" in r.getMessage() for r in caplog.records) == 2
    assert wired.app.stats.snapshot()["rendered"]["floating"] == rendered + 1      # the redraw came first


def test_an_engine_that_raises_is_logged_and_the_ui_carries_on(wired, caplog):
    def boom(state, *, suppressed=False):
        raise RuntimeError("engine bug")

    wired.app._alerts.evaluate = boom
    with caplog.at_level(logging.ERROR, logger="usage_monitor.app"):
        assert publish(wired, usage_state(85)) == []
    assert "alert evaluation failed" in caplog.text
    assert wired.tray.tooltips[-1] == "Claude usage - session 85% - weekly 2% (dev)"   # still redrawn


def test_both_limits_crossing_at_once_are_two_toasts(wired):
    publish(wired, usage_state(13, weekly=10))
    toasts = publish(wired, usage_state(93, weekly=95))
    assert sorted(title for title, _ in toasts) == ["Session limit at 90%", "Weekly limit at 90%"]


def test_there_is_no_notifications_setting_any_more():
    from usage_monitor.ui.server import SETTING_SPECS

    assert "notifications_enabled" not in SETTING_SPECS
    assert "notifications_enabled" not in SETTINGS_DEFAULTS
    assert {"alerts_enabled", "alert_thresholds", "alert_on_threshold", "alert_on_reset", "alert_on_credits",
            "alert_quiet_hours"} <= set(SETTING_SPECS)
    assert not hasattr(app_mod, "AlertTracker") and not hasattr(app_mod, "same_window")


def test_clicking_the_toast_opens_the_dashboard(wired):
    wired.tray.on_notify_click()
    assert wired.app.launcher.opened == 1


def test_the_update_check_runs_at_startup_only_when_it_is_on(monkeypatch, settings):
    for on in (True, False):
        settings.set("check_updates", on)
        checker = FakeChecker(None)
        built = build_app(monkeypatch, settings, update_check_fn=checker)
        assert len(checker.calls) == (1 if on else 0)
        built.app._teardown()


def test_turning_the_update_check_on_later_runs_it_once(monkeypatch, settings):
    settings.set("check_updates", False)
    checker = FakeChecker(None)
    built = build_app(monkeypatch, settings, update_check_fn=checker)
    assert checker.calls == []
    for value in (True, False, True):
        settings.set("check_updates", value)
        built.host.run_posted()
    assert len(checker.calls) == 1


# ── Update notices on the wired app ───────────────────────────────────────────
#
# A release has to be noticed: a tray toast once per version, the menu item and the dashboard banner for as long as it
# is on offer, a new check every six hours. build_app's FakeChecker answers at once (synchronous) unless a test says not.

TOAST = ("Claude Usage Monitor v2.1.0 is available", "Open the tray menu or the dashboard to update")


def publish_state(built: Wired) -> None:
    built.service.publish(sample_state("normal3", NOW))
    built.host.run_posted()


def toast_titles(built: Wired) -> list[str]:
    return [title for title, _body in built.tray.notices]


def test_a_release_found_at_start_is_announced_by_one_toast_that_opens_the_dashboard(monkeypatch, settings):
    built = build_app(monkeypatch, settings, update_check_fn=FakeChecker(INFO))
    assert built.tray.notices == [TOAST]
    assert settings.get("notified_version") == "2.1.0"
    built.tray.on_notify_click()
    assert built.app.launcher.opened == 1


def test_the_update_toast_does_not_depend_on_the_usage_alert_switches(monkeypatch, settings):
    """alerts_enabled and the alert_on_* kinds are about limits; a release is not one."""
    settings.update({"alerts_enabled": False, "alert_on_threshold": False, "alert_on_reset": False,
                     "alert_on_credits": False})
    built = build_app(monkeypatch, settings, update_check_fn=FakeChecker(INFO))
    assert built.tray.notices == [TOAST]


def test_no_release_found_is_no_toast(monkeypatch, settings):
    built = build_app(monkeypatch, settings, update_check_fn=FakeChecker(None))
    publish_state(built)
    assert built.tray.notices == [] and settings.get("notified_version") is None


def test_the_toast_is_not_repeated_by_later_states_or_by_a_restart(monkeypatch, settings):
    checker = FakeChecker(INFO)
    first = build_app(monkeypatch, settings, update_check_fn=checker)
    for _ in range(3):
        publish_state(first)
    assert first.tray.notices == [TOAST]
    first.app._teardown()
    second = build_app(monkeypatch, settings, update_check_fn=checker)       # the next launch finds the same release
    publish_state(second)
    assert second.tray.notices == [] and second.app.updates.info == INFO
    assert "Update to v2.1.0" in [item.label for item in second.app.menu_items()], "told once, on offer for good"


def test_a_newer_release_is_announced_when_a_later_check_finds_it(monkeypatch, settings):
    checker = FakeChecker(INFO)
    built = build_app(monkeypatch, settings, update_check_fn=checker)
    checker.result = found("2.2.0")
    built.host.fire(built.app._update_timer)
    built.host.run_posted()
    assert toast_titles(built) == ["Claude Usage Monitor v2.1.0 is available", "Claude Usage Monitor v2.2.0 is available"]
    assert settings.get("notified_version") == "2.2.0"


def test_a_release_the_user_skipped_is_never_announced_and_the_next_one_is(monkeypatch, settings):
    settings.set("skip_version", "2.1.0")
    checker = FakeChecker(INFO)
    built = build_app(monkeypatch, settings, update_check_fn=checker)
    publish_state(built)
    assert built.tray.notices == [] and settings.get("notified_version") is None
    assert built.app.updates.info == INFO                                    # known: the dashboard says it is skipped
    checker.result = found("2.2.0")
    built.host.fire(built.app._update_timer)
    built.host.run_posted()
    assert toast_titles(built) == ["Claude Usage Monitor v2.2.0 is available"]


def test_a_release_skipped_while_its_toast_waits_is_not_announced_after_all(monkeypatch, settings):
    built = build_app(monkeypatch, settings, update_check_fn=FakeChecker(INFO))   # announced at start ...
    first = len(built.tray.notices)
    assert first == 1
    built.app.updates._info = found("2.2.0")                                  # ... and a newer one is found
    built.plat.fullscreen = True                                              # while a game is in front
    publish_state(built)
    assert len(built.tray.notices) == 1
    built.app.updates.skip()                                                  # the user skips 2.2.0 from the dashboard
    built.plat.fullscreen = False
    publish_state(built)
    assert len(built.tray.notices) == 1, "a skipped release is not announced when the game closes"


def test_nothing_is_announced_while_the_update_check_is_off(monkeypatch, settings):
    built = build_app(monkeypatch, settings, update_check_fn=FakeChecker(None))
    built.plat.fullscreen = True
    built.app.updates._info = dict(INFO)                                       # found, but the toast has to wait
    publish_state(built)
    settings.set("check_updates", False)                                       # and then the user switches the check off
    built.host.run_posted()
    built.plat.fullscreen = False
    publish_state(built)
    assert built.tray.notices == []
    settings.set("check_updates", True)
    publish_state(built)
    assert built.tray.notices == [TOAST], "switched on again, the release is announced once"


def test_the_update_check_repeats_every_six_hours_while_the_monitor_runs(monkeypatch, settings):
    checker = FakeChecker(None)
    built = build_app(monkeypatch, settings, update_check_fn=checker)
    assert len(checker.calls) == 1                                             # at start
    milliseconds, _fn, repeat = built.host.timers[built.app._update_timer]
    assert milliseconds == 6 * 60 * 60 * 1000 == app_mod.UPDATE_RECHECK_S * 1000 and repeat is True
    for expected in (2, 3, 4):
        built.host.fire(built.app._update_timer)
        assert len(checker.calls) == expected
    assert set(checker.calls) == {(version.REPO, version.APP_VERSION)}


def test_a_failed_recheck_is_silent_and_keeps_what_was_known(monkeypatch, settings, caplog):
    checker = FakeChecker(INFO)
    built = build_app(monkeypatch, settings, update_check_fn=checker)
    checker.result = None                                                      # offline, rate limited, GitHub down
    with caplog.at_level(logging.DEBUG, logger="usage_monitor"):
        built.host.fire(built.app._update_timer)
        built.host.run_posted()
    assert built.app.updates.info == INFO and built.tray.notices == [TOAST]
    assert [record for record in caplog.records if record.levelno >= logging.WARNING] == []
    assert any(item.label == "Update to v2.1.0" for item in built.app.menu_items()), "still on offer"


def test_a_recheck_that_cannot_even_start_is_swallowed(monkeypatch, settings, caplog):
    def no_thread(repo, current, on_result):
        raise RuntimeError("can't start new thread")

    built = build_app(monkeypatch, settings, update_check_fn=FakeChecker(None))
    built.app.updates._check = no_thread
    with caplog.at_level(logging.DEBUG, logger="usage_monitor"):
        built.host.fire(built.app._update_timer)                                # raises nothing into the timer
    assert [record for record in caplog.records if record.levelno >= logging.WARNING] == []
    assert any("could not start the update check" in record.getMessage() for record in caplog.records)


def test_the_recheck_waits_while_the_update_check_is_off_and_works_once_it_is_on(monkeypatch, settings):
    settings.set("check_updates", False)
    checker = FakeChecker(None)
    built = build_app(monkeypatch, settings, update_check_fn=checker)
    assert checker.calls == [] and built.app._update_timer is not None         # the timer is cheap; the gate is its callback
    built.host.fire(built.app._update_timer)
    assert checker.calls == []
    settings.set("check_updates", True)
    built.host.run_posted()                                                    # the first check, as when it was switched on
    assert len(checker.calls) == 1
    built.host.fire(built.app._update_timer)
    assert len(checker.calls) == 2
    settings.set("check_updates", False)
    built.host.run_posted()
    built.host.fire(built.app._update_timer)
    assert len(checker.calls) == 2, "switched off again: no more checks"


def test_shutdown_stops_the_recheck_and_drops_a_late_answer(monkeypatch, settings):
    built = build_app(monkeypatch, settings, update_check_fn=FakeChecker(None))
    timer = built.app._update_timer
    assert timer in built.host.timers
    built.app._teardown()
    assert timer not in built.host.timers and built.app._update_timer is None
    built.app._on_update_result(INFO)                                          # a check that was still running finishes
    assert built.host.posted == [] and built.tray.notices == []


def test_the_answer_of_a_check_is_handed_to_the_ui_thread_not_acted_on_where_it_arrives(monkeypatch, settings):
    checker = FakeChecker(INFO, synchronous=False)
    built = build_app(monkeypatch, settings, update_check_fn=checker)
    assert len(checker.pending) == 1 and built.tray.notices == []
    answered = threading.Event()

    def check_thread():
        checker.pending[0](INFO)                                               # what update_check.check_async does
        answered.set()

    threading.Thread(target=check_thread, name="update-check").start()
    assert answered.wait(5)
    assert built.tray.notices == [] and len(built.host.posted) == 1            # nothing was drawn off the UI thread
    built.host.run_posted()
    assert built.tray.notices == [TOAST]


def hold_back(built: Wired, reason: str, on: bool, monkeypatch, settings) -> None:
    """Make (or end) a moment when a toast would go unseen."""
    if reason == "locked":
        built.host.locked = on
    elif reason == "screensaver":
        monkeypatch.setattr(built.plat, "should_pause", lambda: on)
    elif reason == "fullscreen":
        built.plat.fullscreen = on
    else:
        settings.set("alert_quiet_hours", {"start": "00:00", "end": "23:59"} if on else None)


@pytest.mark.parametrize("reason", ["locked", "screensaver", "fullscreen", "quiet hours"])
def test_a_toast_nobody_would_see_waits_until_somebody_could(monkeypatch, settings, reason):
    built = build_app(monkeypatch, settings, update_check_fn=FakeChecker(None))
    hold_back(built, reason, True, monkeypatch, settings)
    built.app.updates._info = dict(INFO)
    built.app._on_update_result(INFO)                                          # a check found it at the wrong moment
    built.host.run_posted()
    assert built.tray.notices == [] and settings.get("notified_version") is None, "held back, and not used up"
    publish_state(built)
    assert built.tray.notices == [], "still held back at the next state"
    hold_back(built, reason, False, monkeypatch, settings)
    publish_state(built)
    assert built.tray.notices == [TOAST], "announced as soon as it could be seen"
    publish_state(built)
    assert built.tray.notices == [TOAST]


def test_a_release_found_while_the_session_was_locked_is_announced_at_unlock(monkeypatch, settings):
    built = build_app(monkeypatch, settings, update_check_fn=FakeChecker(None))
    built.host.locked = True
    built.host.emit("session", True)
    built.host.fire(built.app._update_timer)                                   # the re-check still runs while locked ...
    built.app.updates._info = dict(INFO)
    built.app._on_update_result(INFO)
    built.host.run_posted()
    assert built.tray.notices == []                                            # ... but the toast waits for somebody
    built.host.locked = False
    built.host.emit("session", False)                                          # no state needed: the unlock itself is the cue
    assert built.tray.notices == [TOAST]


def test_a_failure_to_tell_whether_a_toast_would_be_seen_does_not_cost_it(monkeypatch, settings):
    built = build_app(monkeypatch, settings, update_check_fn=FakeChecker(None))

    def broken() -> bool:
        raise OSError("no session information")

    monkeypatch.setattr(built.plat, "should_pause", broken)
    built.app.updates._info = dict(INFO)
    built.app._announce_update()
    assert built.tray.notices == [TOAST]


def test_a_toast_that_raises_is_logged_once_and_not_tried_at_every_state(monkeypatch, settings, caplog):
    built = build_app(monkeypatch, settings, update_check_fn=FakeChecker(None))

    def refuse(title, body):
        raise OSError("the shell refused the balloon")

    built.tray.notify = refuse
    built.app.updates._info = dict(INFO)
    with caplog.at_level(logging.ERROR, logger="usage_monitor.app"):
        built.app._announce_update()
        publish_state(built)
        publish_state(built)
    assert sum("could not show the update notice" in record.getMessage() for record in caplog.records) == 1
    assert built.app.stats.snapshot()["failed"] == 0                           # and the redraw was not touched


def test_a_toast_the_shell_could_not_attach_is_tried_again_at_the_next_state(monkeypatch, settings):
    built = build_app(monkeypatch, settings, update_check_fn=FakeChecker(None))
    answers = iter([False, True])                                              # Explorer is restarting, then it is back
    tried: list[str] = []

    def notify(title, body):
        tried.append(title)
        return next(answers)

    built.tray.notify = notify
    built.app.updates._info = dict(INFO)
    built.app._announce_update()
    assert settings.get("notified_version") is None
    publish_state(built)
    assert len(tried) == 2 and settings.get("notified_version") == "2.1.0"
    publish_state(built)
    assert len(tried) == 2


def test_checking_by_hand_counts_as_being_told(monkeypatch, settings):
    checker = FakeChecker(None)
    built = build_app(monkeypatch, settings, update_check_fn=checker)
    assert built.app._check_updates_for_api() == {"available": False, "version": None, "checked": True}
    assert settings.get("notified_version") is None
    checker.result = INFO                                                      # the user presses "Check for updates now"
    assert built.app._check_updates_for_api() == {"available": True, "version": "2.1.0", "checked": True}
    assert settings.get("notified_version") == "2.1.0"
    built.host.fire(built.app._update_timer)                                   # the automatic check finds the same release
    built.host.run_posted()
    assert built.tray.notices == [], "they just saw it in the dashboard: no toast on top"
    assert any(item.label == "Update to v2.1.0" for item in built.app.menu_items())


def test_the_servers_update_info_is_the_trackers_with_the_page_and_the_install_mode(wired, monkeypatch):
    info = wired.app.server.kwargs["update_cb"]["info"]
    assert info() is None
    wired.app.updates._info = dict(INFO)
    monkeypatch.setattr(app_mod.update_check, "can_self_update", lambda i: True)
    assert info() == dict(INFO, url=INFO["url"], can_self_update=True)
    assert wired.app.server.kwargs["update_cb"]["check"] == wired.app._check_updates_for_api


def test_the_real_server_serves_the_update_block_the_banner_reads(wired, tmp_path, monkeypatch):
    """The app's own callbacks behind the real ApiServer: /api/state's update block for a found release."""
    from usage_monitor.ui.server import ApiServer

    wired.app.updates._info = dict(INFO)
    monkeypatch.setattr(app_mod.update_check, "can_self_update", lambda i: False)
    server = ApiServer(lambda: None, wired.settings, refresh_cb=None, update_cb=wired.app.server.kwargs["update_cb"],
                       version="t", dev=True)
    server.start()
    try:
        smoke = SmokeTest(45, tmp_path / "r.json")
        smoke._app = types.SimpleNamespace(server=server)
        block = smoke._get_json("/api/state")["update"]
    finally:
        server.stop()
    assert block == {"available": True, "version": "2.1.0", "url": INFO["url"], "can_self_update": False,
                     "skipped": False, "info": dict(INFO, can_self_update=False), "skip_version": ""}


def test_shutdown_runs_in_the_documented_order_and_only_once(wired, monkeypatch):
    app, events = wired.app, wired.events
    events.log.clear()
    app.launcher.pid = 4242
    closed: list[int] = []
    monkeypatch.setattr(app_mod, "post_close_to_dashboard", lambda pid: closed.append(pid) or True)
    for name, target in (("floating.destroy", app.floating), ("taskbar.destroy", app.taskbar),
                         ("tooltip.destroy", app.tooltip)):
        original = target.destroy
        monkeypatch.setattr(target, "destroy", lambda original=original, name=name: (events(name), original())[1])
    monkeypatch.setattr(wired.settings, "close", lambda: events("settings.close"))
    app._teardown()
    app._teardown()                                                           # a second call changes nothing
    assert events.log == ["dashboard.close", "service.stop", "server.stop", "tray.remove", "floating.destroy",
                          "taskbar.destroy", "tooltip.destroy", "settings.close"]
    assert closed == [4242]


def test_a_failing_shutdown_step_does_not_skip_the_rest(wired):
    app, events = wired.app, wired.events
    events.log.clear()

    def boom(timeout=5.0):
        raise RuntimeError("stuck")

    wired.service.stop = boom
    wired.tray.remove = lambda: events("tray.remove")
    app.floating.destroy = lambda: events("floating.destroy")
    app._teardown()
    assert events.log == ["server.stop", "tray.remove", "floating.destroy"]
    assert app.server.stopped


def test_teardown_removes_the_listeners_and_the_timers(wired):
    app, host = wired.app, wired.host
    wired.service.publish(sample_state("normal3", NOW))
    host.run_posted()
    app._teardown()
    assert wired.service.subscribers == []
    assert all(not callbacks for callbacks in host.listeners.values())
    assert not app.blinker.running and app._tick_timer is None
    wired.service.publish(sample_state("warning", NOW))                       # a late publish is dropped
    assert wired.service.subscribers == []
    host.run_posted()


def test_a_draw_that_raises_is_logged_once_and_does_not_stop_the_other_surfaces(wired, caplog):
    app = wired.app

    def boom(ctx):
        raise RuntimeError("skin bug")

    app.floating.render = boom
    before = wired.app.stats.snapshot()["rendered"]["taskbar"]
    with caplog.at_level(logging.ERROR, logger="usage_monitor"):
        wired.service.publish(sample_state("warning", NOW))
        wired.host.run_posted()
        wired.service.publish(sample_state("normal3", NOW))
        wired.host.run_posted()
    assert app.stats.snapshot()["rendered"]["taskbar"] == before + 2
    assert sum("drawing the floating failed" in r.getMessage() for r in caplog.records) == 1
    assert wired.tray.tooltips[-1] == "Claude usage - session 13% - weekly 2% (dev)"


def test_the_ui_call_helper_runs_work_on_the_ui_thread_and_returns_its_result(wired):
    results: list[Any] = []
    thread = threading.Thread(target=lambda: results.append(wired.app.call_on_ui(lambda: threading.get_ident())))
    thread.start()
    deadline = time.monotonic() + 5
    while not wired.host.posted and time.monotonic() < deadline:
        time.sleep(0.01)
    wired.host.run_posted()
    thread.join(5)
    assert results == [threading.get_ident()]


def test_the_ui_call_helper_reports_an_error_and_a_timeout(wired):
    def fail():
        raise ValueError("nope")

    caught: list[BaseException] = []

    def worker(fn, timeout):
        try:
            wired.app.call_on_ui(fn, timeout)
        except BaseException as exc:
            caught.append(exc)

    thread = threading.Thread(target=worker, args=(fail, 5))
    thread.start()
    while not wired.host.posted:
        time.sleep(0.01)
    wired.host.run_posted()
    thread.join(5)
    assert isinstance(caught[0], ValueError)
    slow = threading.Thread(target=worker, args=(lambda: None, 0.05))
    slow.start()
    slow.join(5)
    assert isinstance(caught[1], TimeoutError)


def test_describe_summarises_the_live_ui(wired):
    wired.service.publish(sample_state("normal3", NOW))
    wired.host.run_posted()
    info = wired.app.describe()
    assert info["theme"] == ["nostromo", "amber"]
    assert info["floating"]["visible"] and info["taskbar"]["visible"]
    assert info["tray"] == {"added": True, "icon_px": 16}
    assert info["update_recheck"] is True                                    # the six-hourly update check is armed
    assert info["frames"]["rendered"]["floating"] >= 1 and info["frames"]["failed"] == 0
    json.dumps(info)                                                         # the report must be serialisable
    assert info["gdi_objects"] >= 0 and info["user_objects"] >= 0


# ── Dashboard window helper, smoke report ─────────────────────────────────────

def test_closing_a_dashboard_that_has_no_window_is_a_no_op():
    assert app_mod.post_close_to_dashboard(2**31 - 5) is False


def test_the_dashboard_is_closed_through_the_window_its_own_process_owns(monkeypatch):
    """Two monitors can each have a window titled "Claude Usage Monitor"; only the child's is closed."""
    posted: list[tuple[int, int]] = []
    monkeypatch.setattr(app_mod, "dashboard_window_of", lambda pid: 777 if pid == 42 else None)
    monkeypatch.setattr(win32, "PostMessageW", lambda hwnd, msg, w, l: posted.append((hwnd, msg)) or 1)
    assert app_mod.post_close_to_dashboard(42) is True
    assert posted == [(777, win32.WM_CLOSE)]
    assert app_mod.post_close_to_dashboard(43) is False
    assert posted == [(777, win32.WM_CLOSE)], "a process without a window must not close anyone else's"


def test_the_collector_keeps_errors_warnings_and_counts_server_requests():
    collector = app_mod._Collector()
    log = logging.getLogger("usage_monitor.test_collector")
    log.addHandler(collector)
    log.setLevel(logging.DEBUG)
    try:
        log.warning("careful %d", 1)
        log.error("broken")
        try:
            raise KeyError("missing")
        except KeyError:
            log.exception("with a traceback")
        log.debug("not a request")
        server = logging.getLogger("usage_monitor.test_collector.ui.server")
        server.debug("api GET /api/state")
        server.debug("api POST /api/refresh")
    finally:
        log.removeHandler(collector)
    assert collector.warnings == ["usage_monitor.test_collector: careful 1"]
    assert collector.errors[0] == "usage_monitor.test_collector: broken"
    assert "KeyError" in collector.errors[1] and "Traceback" not in collector.errors[1]
    assert collector.request_count() == 2
    assert (collector.count_of("GET /api/state"), collector.count_of("POST /api/refresh"),
            collector.count_of("GET /api/themes")) == (1, 1, 0)


def _dashboard_step_rig(tmp_path, monkeypatch, *, page_requests):
    """A smoke test whose dashboard child is a fake that is up for the whole step and whose server log shows
    ``page_requests`` (log lines such as "GET /api/themes") while it is open."""
    smoke = SmokeTest(45, tmp_path / "r.json")
    monkeypatch.setattr(SmokeTest, "DASHBOARD_OPEN_S", 0.0)
    monkeypatch.setattr(SmokeTest, "DASHBOARD_CLOSE_WAIT_S", 0.5)
    monkeypatch.setattr(SmokeTest, "PAGE_BOOT_WAIT_S", 0.3)
    state = {"up": False}

    class Launcher:
        pid = 4321

        @property
        def running(self):
            return state["up"]

        def open(self):
            state["up"] = True
            for line in page_requests:
                smoke.collector.requests += 1
                smoke.collector.seen[line] = smoke.collector.seen.get(line, 0) + 1
            return True

        def close(self):
            state["up"] = False

    monkeypatch.setattr(app_mod, "post_close_to_dashboard", lambda pid: state.update(up=False) or True)
    smoke._app = types.SimpleNamespace(launcher=Launcher())
    monkeypatch.setattr(smoke, "_get_json", lambda path: {"settings": {"dashboard_bounds": {"x": 0}}})
    return smoke


def test_the_smoke_dashboard_step_wants_a_page_that_started_not_only_one_that_was_fetched(tmp_path, monkeypatch):
    """The dashboard process reads /api/state itself before it opens a window, and a web view that cannot run the page
    (pywebview's IE11 fallback, a WebView2 that failed to start) still fetches index.html: only a running page asks for
    the themes."""
    booted = _dashboard_step_rig(tmp_path, monkeypatch, page_requests=["GET /api/state", "GET /api/themes"])
    assert booted._step_dashboard()["page_booted"] is True
    blank = _dashboard_step_rig(tmp_path, monkeypatch, page_requests=["GET /api/state"])
    with pytest.raises(RuntimeError, match="never started"):
        blank._step_dashboard()
    silent = _dashboard_step_rig(tmp_path, monkeypatch, page_requests=[])
    with pytest.raises(RuntimeError, match="never loaded the page"):
        silent._step_dashboard()


def test_a_smoke_run_that_never_started_fails_and_says_why(tmp_path):
    report = tmp_path / "out" / "smoke.json"
    smoke = SmokeTest(45, report)
    assert smoke.finish(0) == 1
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["ok"] is False and "never started" in data["errors"][0]
    assert data["seconds_requested"] == 45 and not (tmp_path / "out" / "smoke.json.tmp").exists()


def test_a_clean_smoke_run_exits_zero_and_a_logged_error_makes_it_one(tmp_path):
    smoke = SmokeTest(45, tmp_path / "ok.json")
    smoke.began = True
    assert smoke.finish(0) == 0
    assert json.loads((tmp_path / "ok.json").read_text(encoding="utf-8"))["ok"] is True
    smoke = SmokeTest(45, tmp_path / "bad.json")
    smoke.began = True
    smoke.collector.errors.append("usage_monitor.x: something broke")
    assert smoke.finish(0) == 1
    bad = json.loads((tmp_path / "bad.json").read_text(encoding="utf-8"))
    assert bad["errors"] == ["usage_monitor.x: something broke"]


def test_the_smoke_summary_holds_numbers_and_words_but_not_usage_content():
    state = sample_state("normal3", NOW)
    payload = {
        "state": {"status": "live", "usage": {"limits": [{"kind": "session", "percent": 13},
                                                         {"kind": "weekly_all", "percent": 2}], "raw": {"x": "secret"}},
                  "ingest": {"files_done": 3, "files_total": 4, "running": True, "last_error": None}},
        "theme": {"id": "nostromo", "variant": "amber"}, "update": {"available": False},
    }
    summary = SmokeTest._state_summary(payload)
    assert summary["status"] == "live" and (summary["session_pct"], summary["weekly_pct"]) == (13, 2)
    assert "secret" not in json.dumps(summary) and state is not None
    assert SmokeTest._state_summary({})["status"] is None                    # a missing state is not a crash


def test_a_short_smoke_run_still_gets_a_minimum_length():
    assert SmokeTest(1, "x.json").seconds == 5.0


def test_the_smoke_analytics_step_reads_every_statistics_route_and_keeps_only_sizes(monkeypatch, tmp_path):
    from usage_monitor.ui.server import ApiServer

    db = tmp_path / "usage.db"
    Store(db).close()
    settings = Settings(tmp_path / "s.json", save_delay_s=0.0)
    server = ApiServer(lambda: None, settings, refresh_cb=None, update_cb={}, version="t", dev=True,
                       analytics_store=lambda: Store(db))
    server.start()
    smoke = SmokeTest(45, tmp_path / "r.json")
    smoke._app = types.SimpleNamespace(server=server)
    monkeypatch.setattr(SmokeTest, "DATABASE_WAIT_S", 0.0)
    try:
        result = smoke._step_analytics()
    finally:
        server.stop()
        settings.close()
    assert set(result) == {route for route, _keys in SmokeTest.ANALYTICS_ROUTES}
    assert len(result) == 7 and all(item["status"] == 200 and set(item) == {"status", "bytes", "ms"}
                                    for item in result.values())
    json.dumps(result)                                                         # and it can go into the report


def test_the_smoke_analytics_step_fails_naming_each_route_that_does_not_answer(monkeypatch, tmp_path):
    from usage_monitor.ui.server import ApiServer

    settings = Settings(tmp_path / "s.json", save_delay_s=0.0)
    server = ApiServer(lambda: None, settings, refresh_cb=None, update_cb={}, version="t", dev=True)
    server.start()                                                             # no statistics store: every route is a 501
    smoke = SmokeTest(45, tmp_path / "r.json")
    smoke._app = types.SimpleNamespace(server=server)
    monkeypatch.setattr(SmokeTest, "DATABASE_WAIT_S", 0.0)
    try:
        with pytest.raises(RuntimeError) as caught:
            smoke._step_analytics()
    finally:
        server.stop()
        settings.close()
    assert str(caught.value).count("answered 501") == 7 and "/api/analytics/cost" in str(caught.value)


def test_the_smoke_api_step_checks_the_update_block_the_dashboard_reads(tmp_path):
    from usage_monitor.ui.server import ApiServer

    settings = Settings(tmp_path / "s.json", save_delay_s=0.0)
    server = ApiServer(lambda: None, settings, refresh_cb=lambda: None, update_cb={"info": lambda: None},
                       version="t", dev=True)
    server.start()
    smoke = SmokeTest(45, tmp_path / "r.json")
    smoke._app = types.SimpleNamespace(server=server)
    try:
        result = smoke._step_api()
        assert result["state"]["update_available"] is False
        assert (result["index_status"], result["token_in_page"], result["keyless_page_status"]) == (200, True, 401)
        assert set(server._update_view()) == set(SmokeTest.UPDATE_KEYS), "the smoke check and the server agree on the block"
        server._update_view = lambda: {"available": False, "info": None}              # a build that lost keys of the block
        with pytest.raises(RuntimeError, match="update has no version, url, can_self_update, skipped, skip_version"):
            smoke._step_api()
    finally:
        server.stop()
        settings.close()


def test_the_smoke_windows_step_wants_the_update_recheck_armed(tmp_path):
    def stub(recheck: bool) -> types.SimpleNamespace:
        info = {"floating": {"visible": True}, "taskbar": None, "tray": {"added": True}, "frames": {}, "update_recheck": recheck}
        widget = types.SimpleNamespace(show_tooltip=lambda: None, hide_tooltip=lambda: None)
        return types.SimpleNamespace(call_on_ui=lambda fn: fn(), describe=lambda: info, floating=widget, taskbar=None,
                                     tooltip=types.SimpleNamespace(visible=True))

    smoke = SmokeTest(45, tmp_path / "r.json")
    smoke._app = stub(True)
    assert smoke._step_windows()["tooltip_shown"] is True
    smoke._app = stub(False)
    with pytest.raises(RuntimeError, match="six-hourly update check is not armed"):
        smoke._step_windows()


def test_the_smoke_shape_check_names_what_is_missing():
    check = SmokeTest._shape_problem
    assert check("/r", b'{"a": 1, "b": 2}', ("a", "b")) is None
    assert check("/r", b'{"a": 1}', ("a", "b", "c")) == "GET /r lacks b, c"
    assert check("/r", b"<html>", ("a",)) == "GET /r did not answer JSON"
    assert check("/r", b"[1]", ("a",)) == "GET /r lacks a"


def test_the_analytics_step_runs_after_the_dashboard_step_in_a_full_smoke_run():
    """The probe schedule is inside _probe; read it from the source so a re-ordering is noticed."""
    import inspect

    source = inspect.getsource(SmokeTest._probe)
    assert source.index('"dashboard"') < source.index('"analytics"')


# ── Real windows (CUM_UI_TESTS=1) ─────────────────────────────────────────────

OFF = -30000


class OffscreenPlatform:
    """The real platform module with every geometry answer moved far off-screen, so real windows
    are created, drawn and shown where nobody sees them."""

    def __getattr__(self, name: str) -> Any:
        return getattr(plat, name)

    def work_areas(self):
        return [Rect(OFF, OFF, OFF + 3000, OFF + 1400)]

    def work_area_at(self, x, y):
        return self.work_areas()[0]

    def dpi_for_point(self, x, y):
        return 96

    def taskbar_info(self):
        bar = Rect(OFF, OFF + 1352, OFF + 3000, OFF + 1400)
        return TaskbarInfo(1, bar, "bottom", Rect(OFF + 2774, bar.top, OFF + 3000, bar.bottom),
                           Rect(OFF, OFF, OFF + 3000, OFF + 1400), Rect(OFF, OFF, OFF + 3000, OFF + 1352), 96, False)

    def is_fullscreen_foreground(self):
        return False

    def should_pause(self):
        return False


def wait_for(condition, timeout: float = 10.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return bool(condition())


class AppRunner:
    """MonitorApp.run() on a worker thread, with a fake data service and off-screen widgets."""

    def __init__(self, settings: Settings, *, service: FakeService | None = None, mutex: str | None = None) -> None:
        self.events = Events()
        self.service = service or FakeService(self.events, sample_state("normal3", datetime.now(timezone.utc)))
        self.app = MonitorApp(
            settings=settings, service_factory=lambda s: self.service, update_check_fn=lambda repo, cur, cb, on_error=None: cb(None),
            platform_api=OffscreenPlatform(), mutex=mutex or f"Local\\CUM-test-{uuid.uuid4().hex}",
            animate_when_locked=True,
        )
        self.exit_code: list[int] = []
        self.error: list[BaseException] = []
        self.thread = threading.Thread(target=self._run, name="app-under-test", daemon=True)

    def _run(self) -> None:
        try:
            self.exit_code.append(self.app.run())
        except BaseException as exc:
            self.error.append(exc)

    def start(self) -> AppRunner:
        self.thread.start()
        assert self.app.started.wait(15), f"the app did not start: {self.error}"
        return self

    def stop(self) -> int:
        self.app.quit()
        self.thread.join(15)
        assert not self.thread.is_alive(), "the app did not exit"
        assert not self.error, self.error
        return self.exit_code[0]


def gui_counts() -> tuple[int, int]:
    process = win32.GetCurrentProcess()
    return win32.GetGuiResources(process, win32.GR_GDIOBJECTS), win32.GetGuiResources(process, win32.GR_USEROBJECTS)


@UI
def test_the_app_runs_draws_follows_settings_and_shuts_down_cleanly(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"theme": "Blue", "poll_interval_ms": 900000}), encoding="utf-8")
    settings = Settings(tmp_path / "settings.json", save_delay_s=0.0)
    runner = AppRunner(settings).start()
    app = runner.app
    try:
        assert (settings.get("theme_id"), settings.get("theme_variant")) == ("nostromo", "blue")   # migrated at startup
        runner.service.publish(sample_state("warning", datetime.now(timezone.utc)))
        assert wait_for(lambda: app.stats.snapshot()["rendered"].get("floating", 0) >= 2)
        info = app.call_on_ui(app.describe)
        assert info["floating"]["visible"] and info["taskbar"]["visible"] and info["tray"]["added"]
        assert info["update_recheck"] is True       # the re-check timer is armed on the real host
        assert info["floating"]["rect"][0] < -20000 and info["taskbar"]["rect"][0] < -20000       # parked off-screen
        assert info["theme"] == ["nostromo", "blue"]
        floating_hwnd = app.floating.window.hwnd
        assert win32.IsWindow(floating_hwnd)

        settings.update({"theme_id": "night_city", "theme_variant": "blackwall"})   # a live switch, from this thread
        assert wait_for(lambda: (app.call_on_ui(app.describe)["theme"] or [None])[0] == "night_city")
        settings.update({"theme_id": "gates", "theme_variant": "blue", "theme_mode": "dark"})
        assert wait_for(lambda: (app.call_on_ui(app.describe)["theme"] or [None])[0] == "gates")
        assert app.stats.snapshot()["failed"] == 0

        settings.set("show_floating", False)
        assert wait_for(lambda: app.call_on_ui(app.describe)["floating"] is None)
        assert not win32.IsWindow(floating_hwnd)
        settings.set("show_floating", True)
        assert wait_for(lambda: app.call_on_ui(app.describe)["floating"] is not None)
        taskbar_hwnd = app.taskbar.window.hwnd
    finally:
        code = runner.stop()
    assert code == 0
    assert not win32.IsWindow(taskbar_hwnd), "the windows are destroyed on shutdown"
    assert runner.events.log[0] == "service.start" and "service.stop" in runner.events.log
    assert runner.service.subscribers == []
    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert (saved["theme_id"], saved["theme_variant"]) == ("gates", "blue"), "settings are flushed on exit"


@UI
def test_a_steady_stream_of_data_leaks_no_window_objects(tmp_path):
    settings = Settings(tmp_path / "settings.json", save_delay_s=0.0)
    runner = AppRunner(settings).start()
    try:
        for percent in range(10):                                           # warm up: fonts, caches, first frames
            state = sample_state("normal3", datetime.now(timezone.utc))
            state.usage.limits[0].percent = float(percent * 9)
            runner.service.publish(state)
            time.sleep(0.05)
        assert wait_for(lambda: runner.app.stats.snapshot()["rendered"].get("floating", 0) >= 4)
        before = runner.app.call_on_ui(runner.app.describe)
        rendered_before = before["frames"]["rendered"]["floating"]
        for percent in range(40):
            state = sample_state("normal3", datetime.now(timezone.utc))
            state.usage.limits[0].percent = float(percent % 100)
            state.usage.limits[1].percent = float((percent * 7) % 100)
            runner.service.publish(state)
            time.sleep(0.05)
        assert wait_for(lambda: runner.app.stats.snapshot()["rendered"]["floating"] >= rendered_before + 20)
        after = runner.app.call_on_ui(runner.app.describe)
    finally:
        runner.stop()
    assert after["gdi_objects"] - before["gdi_objects"] <= 3
    assert after["user_objects"] - before["user_objects"] <= 3
    assert after["frames"]["failed"] == 0


@UI
def test_a_second_launch_opens_the_running_instances_dashboard(tmp_path, monkeypatch):
    name = f"Local\\CUM-test-{uuid.uuid4().hex}"
    settings = Settings(tmp_path / "settings.json", save_delay_s=0.0)
    runner = AppRunner(settings, mutex=name).start()
    opened: list[str] = []
    runner.app.launcher = type("L", (), {"open": lambda self: opened.append("open") or True,
                                         "close": lambda self, timeout=5.0: None, "pid": None, "running": False})()
    try:
        second = MonitorApp(settings=Settings(tmp_path / "other.json", save_delay_s=0.0), mutex=name)
        assert second.run() == 0
        assert wait_for(lambda: opened == ["open"])
    finally:
        runner.stop()


@UI
def test_real_layered_windows_carry_the_widgets_frames(tmp_path):
    """The real LayeredWindow behind FloatingWidget and TaskbarWidget: the skin's pixels reach it."""
    from usage_monitor.render.skins import get_skin
    from usage_monitor.ui.host import UIHost

    host = UIHost(controller_class=f"CUM.Test.Controller.{uuid.uuid4().hex[:8]}")
    pieces: list[Any] = []
    try:
        fake_plat = OffscreenPlatform()
        tooltip = Tooltip(host, platform=fake_plat)
        floating = FloatingWidget(host, get_position=lambda: None, set_position=lambda x, y: None,
                                  on_open=lambda: None, on_menu=lambda x, y: None, tooltip=tooltip, platform=fake_plat)
        strip = TaskbarWidget(host, on_open=lambda: None, on_menu=lambda x, y: None, tooltip=tooltip,
                              platform=fake_plat)
        pieces += [floating, strip, tooltip]
        theme = themes.get_theme("nostromo")
        ctx = RenderContext(sample_model("normal3"), theme, theme.default_variant, get_skin("nostromo"))
        floating.render(ctx)
        strip.render(ctx)
        floating.show_tooltip()
        assert floating.visible and strip.visible and tooltip.visible
        scale = scale_for(floating.window.dpi)                         # whatever DPI this machine's monitor has
        assert floating.window.rect.width == round(280 * scale) and floating.window.rect.height == round(126 * scale)
        assert strip.window.rect.width == 264 and strip.window.rect.height == 48
        assert tooltip.owner is floating
        before = gui_counts()
        for blink in (False, True) * 20:
            floating.render(ctx.with_blink(blink))
            strip.render(ctx.with_blink(blink))
        assert gui_counts() == before, "blinking leaks no GDI or USER objects"
    finally:
        for piece in pieces:
            piece.destroy()
        host.close()
