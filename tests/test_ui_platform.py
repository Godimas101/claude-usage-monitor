"""Tests for usage_monitor/ui/platform.py.

The arithmetic helpers and the decision logic run anywhere on Windows, with the Win32 calls faked
where needed. The queries that need Explorer and an interactive desktop are skipped unless
CUM_UI_TESTS=1 (CI has no desktop).
"""
from __future__ import annotations

import ctypes
import os
import re
import sys

import pytest

if sys.platform != "win32":
    pytest.skip("the Win32 UI layer is Windows only", allow_module_level=True)

from usage_monitor.ui import platform as plat
from usage_monitor.ui import win32
from usage_monitor.ui.platform import Rect

UI = pytest.mark.skipif(
    os.environ.get("CUM_UI_TESTS") != "1", reason="needs an interactive desktop: set CUM_UI_TESTS=1"
)

HD = Rect(0, 0, 1920, 1080)
RIGHT_HD = Rect(1920, 0, 3840, 1080)
LEFT_HD = Rect(-1920, 0, 0, 1080)


# ── Rect ─────────────────────────────────────────────────────────────────────

def test_rect_geometry():
    r = Rect(10, 20, 110, 70)
    assert (r.x, r.y, r.width, r.height) == (10, 20, 100, 50)
    assert r.center == (60, 45)
    assert r == (10, 20, 110, 70)  # still a plain tuple


def test_rect_contains_excludes_the_right_and_bottom_edge():
    r = Rect(0, 0, 10, 10)
    assert r.contains(0, 0) and r.contains(9, 9)
    assert not r.contains(10, 5) and not r.contains(5, 10) and not r.contains(-1, 5)


def test_rect_overlap_area():
    assert Rect(0, 0, 10, 10).overlap_area(Rect(5, 5, 20, 20)) == 25
    assert Rect(0, 0, 10, 10).overlap_area(Rect(10, 0, 20, 10)) == 0  # touching edges only
    assert Rect(0, 0, 10, 10).overlap_area(Rect(50, 50, 60, 60)) == 0
    assert Rect(0, 0, 100, 100).overlap_area(Rect(10, 10, 20, 20)) == 100


def test_rect_from_win_rect():
    assert Rect.from_win(win32.wintypes.RECT(1, 2, 3, 4)) == Rect(1, 2, 3, 4)


# ── abgr_to_hex ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value, expected", [
    (0xFFD47800, "#0078d4"),  # the stock Windows blue, stored as 0xAABBGGRR
    (0xFF0C00D0, "#d0000c"),
    (0x00D47800, "#0078d4"),  # alpha is ignored
    (0xFF010203, "#030201"),  # red is the low byte
    (0xFF000000, "#000000"),
    (0xFFFFFFFF, "#ffffff"),
    (0, "#000000"),
])
def test_abgr_to_hex(value, expected):
    assert plat.abgr_to_hex(value) == expected


# ── covers / edge_from_rects ─────────────────────────────────────────────────

def test_covers():
    assert plat.covers(HD, HD)
    assert plat.covers(Rect(-8, -8, 1928, 1088), HD)
    assert not plat.covers(Rect(0, 0, 1920, 1079), HD)
    assert not plat.covers(Rect(1, 0, 1920, 1080), HD)


@pytest.mark.parametrize("bar, monitor, edge", [
    (Rect(0, 1032, 1920, 1080), HD, "bottom"),
    (Rect(0, 0, 1920, 48), HD, "top"),
    (Rect(0, 0, 60, 1080), HD, "left"),
    (Rect(1860, 0, 1920, 1080), HD, "right"),
    (Rect(0, 1078, 1920, 1080), HD, "bottom"),  # auto-hide sliver
    (Rect(1920, 1032, 3840, 1080), RIGHT_HD, "bottom"),  # secondary monitor
    (Rect(-1920, 0, -1860, 1080), LEFT_HD, "left"),  # monitor left of the primary
])
def test_edge_from_rects(bar, monitor, edge):
    assert plat.edge_from_rects(bar, monitor) == edge


# ── nearest_rect / clamp_to_rects ────────────────────────────────────────────

def test_nearest_rect_prefers_the_containing_rect():
    assert plat.nearest_rect(2000, 10, [HD, RIGHT_HD]) == RIGHT_HD
    assert plat.nearest_rect(5, 5, [HD, RIGHT_HD]) == HD


def test_nearest_rect_falls_back_to_the_closest_one():
    assert plat.nearest_rect(5000, 500, [HD, RIGHT_HD]) == RIGHT_HD
    assert plat.nearest_rect(-300, 500, [HD, RIGHT_HD]) == HD
    assert plat.nearest_rect(10, 10, []) is None


def test_clamp_leaves_a_window_that_fits_alone():
    assert plat.clamp_to_rects(100, 200, 280, 126, [HD]) == (100, 200)


def test_clamp_pins_a_window_to_each_edge():
    assert plat.clamp_to_rects(1800, 1000, 280, 126, [HD]) == (1640, 954)  # right and bottom
    assert plat.clamp_to_rects(-50, -30, 280, 126, [HD]) == (0, 0)  # left and top


def test_clamp_chooses_the_monitor_with_the_larger_overlap():
    # 20 px on the left monitor, 80 on the right one: it belongs to the right one.
    assert plat.clamp_to_rects(1900, 100, 100, 50, [HD, RIGHT_HD]) == (1920, 100)
    # 80 px on the left monitor, 20 on the right one.
    assert plat.clamp_to_rects(1840, 100, 100, 50, [HD, RIGHT_HD]) == (1820, 100)


def test_clamp_handles_monitors_with_negative_coordinates():
    assert plat.clamp_to_rects(-2500, 100, 300, 100, [HD, LEFT_HD]) == (-1920, 100)
    assert plat.clamp_to_rects(-1000, 100, 300, 100, [HD, LEFT_HD]) == (-1000, 100)


def test_clamp_brings_an_offscreen_window_back_to_the_nearest_monitor():
    assert plat.clamp_to_rects(9000, 9000, 280, 126, [HD, RIGHT_HD]) == (3560, 954)
    assert plat.clamp_to_rects(-9000, 300, 280, 126, [HD, RIGHT_HD]) == (0, 300)


def test_clamp_keeps_the_top_left_in_view_when_the_window_is_larger_than_the_rect():
    assert plat.clamp_to_rects(500, 500, 3000, 2000, [HD]) == (0, 0)


def test_clamp_without_monitors_changes_nothing():
    assert plat.clamp_to_rects(-5, 99999, 10, 10, []) == (-5, 99999)


def test_clamp_to_work_area_reads_the_current_work_areas(monkeypatch):
    work = [Rect(0, 0, 1920, 1032)]
    monkeypatch.setattr(plat, "work_areas", lambda: work)
    assert plat.clamp_to_work_area(1800, 1000, 280, 126) == (1640, 906)
    assert plat.work_area_at(100, 100) == work[0]


# ── Registry readers ─────────────────────────────────────────────────────────

def test_windows_accent_hex_converts_the_stored_value(monkeypatch):
    seen = []

    def fake(subkey, name):
        seen.append((subkey, name))
        return 0xFFD47800

    monkeypatch.setattr(plat, "_read_dword", fake)
    assert plat.windows_accent_hex() == "#0078d4"
    assert seen == [(r"Software\Microsoft\Windows\DWM", "AccentColor")]


def test_windows_accent_hex_is_none_without_a_value(monkeypatch):
    monkeypatch.setattr(plat, "_read_dword", lambda subkey, name: None)
    assert plat.windows_accent_hex() is None


@pytest.mark.parametrize("stored, expected", [(1, True), (0, False), (None, False)])
def test_apps_use_light_theme(monkeypatch, stored, expected):
    monkeypatch.setattr(plat, "_read_dword", lambda subkey, name: stored)
    assert plat.apps_use_light_theme() is expected
    assert plat.system_uses_light_theme() is expected


def test_read_dword_returns_none_for_a_missing_value():
    assert plat._read_dword(r"Software\Microsoft\Windows\DWM", "CumNoSuchValue") is None
    assert plat._read_dword(r"Software\CumNoSuchKey", "AccentColor") is None


def test_the_registry_readers_return_the_documented_types():
    accent = plat.windows_accent_hex()
    assert accent is None or re.fullmatch(r"#[0-9a-f]{6}", accent)
    assert isinstance(plat.apps_use_light_theme(), bool)
    assert isinstance(plat.system_uses_light_theme(), bool)


# ── Lock, screensaver, pause ─────────────────────────────────────────────────

def test_is_locked_when_the_input_desktop_cannot_be_opened(monkeypatch):
    monkeypatch.setattr(win32, "OpenInputDesktop", lambda *args: None)
    monkeypatch.setattr(plat, "_session_flags_say_locked", lambda: pytest.fail("not needed"))
    assert plat.is_locked() is True


@pytest.mark.parametrize("session_locked", [True, False])
def test_is_locked_falls_back_to_the_session_flag(monkeypatch, session_locked):
    closed = []
    monkeypatch.setattr(win32, "OpenInputDesktop", lambda *args: 1234)
    monkeypatch.setattr(win32, "CloseDesktop", lambda handle: closed.append(handle) or True)
    monkeypatch.setattr(plat, "_session_flags_say_locked", lambda: session_locked)
    assert plat.is_locked() is session_locked
    assert closed == [1234]  # the desktop handle never leaks


def test_is_locked_is_false_when_the_query_raises(monkeypatch):
    def boom(*args):
        raise OSError("no desktop")

    monkeypatch.setattr(win32, "OpenInputDesktop", boom)
    assert plat.is_locked() is False


@pytest.mark.parametrize("flags, level, expected", [(0, 1, True), (1, 1, False), (0, 2, False)])
def test_session_flags_say_locked_reads_wtsinfoex(monkeypatch, flags, level, expected):
    info = win32.WTSINFOEX_HEAD(Level=level, SessionId=1, SessionState=0, SessionFlags=flags)
    freed = []

    def query(server, session, info_class, buffer_ref, size_ref):
        assert session == win32.WTS_CURRENT_SESSION and info_class == win32.WTSSessionInfoEx
        buffer_ref._obj.value = ctypes.addressof(info)
        size_ref._obj.value = ctypes.sizeof(info) + 200
        return True

    monkeypatch.setattr(win32, "WTSQuerySessionInformationW", query)
    monkeypatch.setattr(win32, "WTSFreeMemory", freed.append)
    assert plat._session_flags_say_locked() is expected
    assert len(freed) == 1


def test_session_flags_say_locked_is_false_when_the_query_fails(monkeypatch):
    monkeypatch.setattr(win32, "WTSQuerySessionInformationW", lambda *args: False)
    assert plat._session_flags_say_locked() is False


def test_screensaver_running_passes_a_four_byte_bool(monkeypatch):
    """v1 passed a 1-byte c_bool; SPI_GETSCREENSAVERRUNNING writes a 4-byte BOOL."""
    sizes = []

    def fake(action, param, target, flags):
        assert action == win32.SPI_GETSCREENSAVERRUNNING
        sizes.append(ctypes.sizeof(target._obj))
        target._obj.value = 1
        return True

    monkeypatch.setattr(win32, "SystemParametersInfoW", fake)
    assert plat.screensaver_running() is True
    assert sizes == [4]


def test_screensaver_running_is_false_when_the_call_fails(monkeypatch):
    monkeypatch.setattr(win32, "SystemParametersInfoW", lambda *args: False)
    assert plat.screensaver_running() is False


@pytest.mark.parametrize("saver, locked, expected", [
    (False, False, False), (True, False, True), (False, True, True), (True, True, True),
])
def test_should_pause(monkeypatch, saver, locked, expected):
    monkeypatch.setattr(plat, "screensaver_running", lambda: saver)
    monkeypatch.setattr(plat, "is_locked", lambda: locked)
    assert plat.should_pause() is expected


# ── Fullscreen detection (Win32 calls faked) ─────────────────────────────────

class FakeForeground:
    """Stands in for the window calls behind is_fullscreen_foreground()."""

    def __init__(self, monkeypatch, *, bounds=HD, style=win32.WS_POPUP, ignored=False, cloaked=False,
                 iconic=False, hwnd=77, monitor=HD, notification_state=1):
        monkeypatch.setattr(win32, "GetForegroundWindow", lambda: hwnd)
        monkeypatch.setattr(plat, "_is_ignored_window", lambda h: ignored)
        monkeypatch.setattr(plat, "_is_cloaked", lambda h: cloaked)
        monkeypatch.setattr(win32, "IsIconic", lambda h: iconic)
        monkeypatch.setattr(win32, "GetWindowLongPtrW", lambda h, index: style)
        monkeypatch.setattr(win32, "MonitorFromWindow", lambda h, flags: 5)
        monkeypatch.setattr(
            plat, "_monitor_info", lambda h: plat.Monitor(monitor, monitor, True, "fake", 5)
        )
        monkeypatch.setattr(plat, "_visible_bounds", lambda h: bounds)

        def state(ref):
            ref._obj.value = notification_state
            return 0

        monkeypatch.setattr(win32, "SHQueryUserNotificationState", state)


def test_a_borderless_window_over_the_whole_monitor_is_fullscreen(monkeypatch):
    FakeForeground(monkeypatch)
    assert plat.is_fullscreen_foreground() is True


def test_exclusive_fullscreen_and_presentation_mode_count_without_geometry(monkeypatch):
    for state in (3, 4):
        FakeForeground(monkeypatch, bounds=Rect(100, 100, 400, 300), notification_state=state)
        assert plat.is_fullscreen_foreground() is True


def test_an_ordinary_window_is_not_fullscreen(monkeypatch):
    FakeForeground(monkeypatch, bounds=Rect(100, 100, 1200, 800), style=win32.WS_CAPTION)
    assert plat.is_fullscreen_foreground() is False


def test_a_maximized_window_with_a_caption_is_not_fullscreen(monkeypatch):
    # Its visible frame can reach the monitor edge when the taskbar auto-hides.
    FakeForeground(monkeypatch, style=win32.WS_MAXIMIZE | win32.WS_CAPTION)
    assert plat.is_fullscreen_foreground() is False


def test_a_window_that_stops_short_of_the_taskbar_edge_is_not_fullscreen(monkeypatch):
    FakeForeground(monkeypatch, bounds=Rect(0, 0, 1920, 1078))
    assert plat.is_fullscreen_foreground() is False


@pytest.mark.parametrize("option", [{"ignored": True}, {"cloaked": True}, {"iconic": True}, {"hwnd": None}])
def test_windows_that_must_not_count_are_skipped(monkeypatch, option):
    FakeForeground(monkeypatch, **option)
    assert plat.is_fullscreen_foreground() is False


def test_fullscreen_is_judged_against_the_window_s_own_monitor(monkeypatch):
    FakeForeground(monkeypatch, bounds=RIGHT_HD, monitor=RIGHT_HD)
    assert plat.is_fullscreen_foreground() is True


def test_is_fullscreen_foreground_is_false_when_a_call_raises(monkeypatch):
    def boom(*args):
        raise OSError("gone")

    monkeypatch.setattr(plat, "_notification_state_is_fullscreen", boom)
    assert plat.is_fullscreen_foreground() is False


def test_monitors_can_be_listed_without_touching_the_dpi_awareness(monkeypatch):
    """The dashboard child does not own its DPI awareness (pywebview sets it when the GUI starts), and the
    awareness can only be set once, so its monitor query must not set it."""
    def refuse():
        raise AssertionError("the DPI awareness must be left alone")

    monkeypatch.setattr(plat, "ensure_dpi_awareness", refuse)
    monkeypatch.setattr(win32, "EnumDisplayMonitors", lambda *args: True)   # no monitors reported
    assert plat.monitors(set_awareness=False) == []
    assert plat.work_areas(set_awareness=False) == []
    with pytest.raises(AssertionError):
        plat.monitors()                                                      # the default still sets it first


# ── Live queries (need Explorer and a desktop) ───────────────────────────────

@UI
def test_the_process_is_per_monitor_aware():
    assert plat.ensure_dpi_awareness() == "per_monitor_v2"
    assert plat.dpi_awareness() == "per_monitor_v2"


@UI
def test_monitors_lists_the_displays_with_the_primary_first():
    monitors = plat.monitors()
    assert monitors and monitors[0].primary
    assert sum(m.primary for m in monitors) == 1
    for monitor in monitors:
        assert monitor.rect.width > 0 and monitor.rect.height > 0
        assert plat.covers(monitor.rect, monitor.work)
        assert monitor.dpi >= 96 and monitor.device
    assert plat.work_areas() == [m.work for m in monitors]


@UI
def test_dpi_for_point_matches_the_monitor_under_it():
    primary = plat.monitors()[0]
    assert plat.dpi_for_point(*primary.rect.center) == primary.dpi
    assert plat.dpi_for_point(-90000, -90000) >= 96  # off-screen: nearest monitor


@UI
def test_taskbar_info_describes_the_primary_taskbar():
    info = plat.taskbar_info()
    assert info is not None
    assert info.edge in ("bottom", "top", "left", "right")
    assert info.rect.width > 0 and info.rect.height > 0
    assert plat.covers(info.monitor_rect, info.work_area)
    assert info.dpi >= 96
    assert isinstance(info.autohide, bool)
    assert info.is_horizontal == (info.edge in ("top", "bottom"))
    if info.tray_rect is not None:
        assert plat.covers(info.rect, info.tray_rect)


@UI
def test_taskbar_infos_starts_with_the_primary():
    infos = plat.taskbar_infos()
    assert infos and infos[0] == plat.taskbar_info()


@UI
def test_cursor_pos_is_a_pair_of_screen_coordinates():
    pos = plat.cursor_pos()
    assert pos is None or (isinstance(pos, tuple) and len(pos) == 2 and all(isinstance(v, int) for v in pos))


@UI
def test_state_queries_return_booleans():
    assert isinstance(plat.is_fullscreen_foreground(), bool)
    assert isinstance(plat.is_locked(), bool)
    assert isinstance(plat.screensaver_running(), bool)
    assert isinstance(plat.should_pause(), bool)
