"""Tests for the Win32 UI layer: usage_monitor/ui/host.py, layered.py, menu.py, tray.py, win32.py.

The pure helpers (callback arity, premultiplied pixels, clipping, icon choice, menu planning,
lparam arithmetic) run anywhere on Windows. Everything that creates a real window, menu, icon or
tray icon is skipped unless CUM_UI_TESTS=1, because CI has no interactive desktop. Those tests
use off-screen windows and a tray tooltip ending in "(dev)"; none opens a dashboard or touches
real settings.
"""
from __future__ import annotations

import ctypes
import functools
import os
import signal
import sys
import threading
import time
import uuid
from ctypes import wintypes

import pytest
from PIL import Image

if sys.platform != "win32":
    pytest.skip("the Win32 UI layer is Windows only", allow_module_level=True)

from usage_monitor.ui import menu as menu_mod
from usage_monitor.ui import platform as plat
from usage_monitor.ui import tray as tray_mod
from usage_monitor.ui import win32
from usage_monitor.ui.host import (
    UIHost,
    controller_class_name,
    find_controller,
    invoke,
    signal_running_instance,
)
from usage_monitor.ui.layered import LayeredWindow, premultiplied_bgra
from usage_monitor.ui.menu import SEPARATOR, MenuItem, build_entries, popup_flags, show_menu, tidy_items
from usage_monitor.ui.platform import Rect
from usage_monitor.ui.tray import TrayIcon, clip, create_hicon, pick_image

UI = pytest.mark.skipif(
    os.environ.get("CUM_UI_TESTS") != "1", reason="needs an interactive desktop: set CUM_UI_TESTS=1"
)

OFFSCREEN = (-30000, -30000)


# ── Helpers ──────────────────────────────────────────────────────────────────

def new_host() -> UIHost:
    return UIHost(controller_class=f"CUM.Test.Controller.{uuid.uuid4().hex[:8]}")


@pytest.fixture
def host():
    h = new_host()
    try:
        yield h
    finally:
        h.close()


def pump(until=None, *, seconds: float = 0.0, timeout: float = 5.0) -> bool:
    """Dispatch this thread's messages without run(): until ``until()`` is true (or ``timeout``),
    or for ``seconds``. Returns whether ``until`` became true (True when there was none)."""
    msg = wintypes.MSG()
    deadline = time.monotonic() + (timeout if until is not None else seconds)
    while time.monotonic() < deadline:
        if until is not None and until():
            return True
        while win32.PeekMessageW(ctypes.byref(msg), None, 0, 0, win32.PM_REMOVE):
            if msg.message == win32.WM_QUIT:
                return until is None or bool(until())
            win32.TranslateMessage(ctypes.byref(msg))
            win32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.002)
    return True if until is None else bool(until())


def run_loop(host: UIHost, timeout: float = 10.0, **kwargs) -> int:
    """host.run() guarded by a watchdog, so a bug fails the test instead of hanging the run."""
    watchdog = threading.Timer(timeout, host.quit)
    watchdog.daemon = True
    watchdog.start()
    try:
        return host.run(**kwargs)
    finally:
        watchdog.cancel()


def gdi_objects() -> int:
    return win32.GetGuiResources(win32.GetCurrentProcess(), win32.GR_GDIOBJECTS)


def user_objects() -> int:
    return win32.GetGuiResources(win32.GetCurrentProcess(), win32.GR_USEROBJECTS)


def solid(width: int, height: int, color=(200, 50, 50, 255)) -> Image.Image:
    return Image.new("RGBA", (width, height), color)


def exstyle(hwnd: int) -> int:
    return win32.GetWindowLongPtrW(hwnd, win32.GWL_EXSTYLE) & 0xFFFFFFFF


def utf16_units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


# ── win32 helpers ────────────────────────────────────────────────────────────

def test_lparam_helpers_sign_extend_coordinates():
    assert win32.make_lparam(100, 200) == (200 << 16) | 100
    for x, y in [(0, 0), (5, -7), (-1, 1), (-1920, 1080), (32767, -32768)]:
        lparam = win32.make_lparam(x, y)
        assert (win32.get_x_lparam(lparam), win32.get_y_lparam(lparam)) == (x, y)
    assert [win32.signed16(v) for v in (0xFFFF, 0x8000, 0x7FFF)] == [-1, -32768, 32767]
    assert (win32.loword(0x12345678), win32.hiword(0x12345678)) == (0x5678, 0x1234)


def test_structure_sizes_match_the_windows_headers():
    wide = ctypes.sizeof(ctypes.c_void_p) == 8
    assert ctypes.sizeof(win32.NOTIFYICONDATAW) == (976 if wide else 956)
    assert ctypes.sizeof(win32.APPBARDATA) == (48 if wide else 36)
    assert ctypes.sizeof(win32.MONITORINFOEXW) == 104
    assert ctypes.sizeof(win32.BITMAPINFOHEADER) == 40
    assert ctypes.sizeof(win32.BLENDFUNCTION) == 4


def test_private_messages_do_not_collide():
    ids = {win32.WM_APP_POST, win32.WM_APP_SETTINGS, win32.WM_APP_OPEN_DASHBOARD, win32.WM_APP_TRAY}
    assert len(ids) == 4 and all(i >= win32.WM_APP for i in ids)


# ── invoke ───────────────────────────────────────────────────────────────────

class Listener:
    def __init__(self):
        self.calls = []

    def one(self, a):
        self.calls.append(("one", a))

    def __call__(self, a, b):
        self.calls.append(("call", a, b))


def test_invoke_passes_only_the_arguments_the_callback_accepts():
    calls = []
    invoke(lambda: calls.append("none"), 1, 2)
    invoke(lambda a: calls.append(("one", a)), 1, 2)
    invoke(lambda a, b: calls.append(("two", a, b)), 1, 2)
    invoke(lambda *a: calls.append(("star", a)), 1, 2)
    invoke(lambda a, *, key=0: calls.append(("kwonly", a)), 1, 2)
    invoke(lambda a, b, c=9: calls.append(("default", a, b, c)), 1, 2)
    assert calls == [
        "none", ("one", 1), ("two", 1, 2), ("star", (1, 2)), ("kwonly", 1), ("default", 1, 2, 9),
    ]


def test_invoke_handles_methods_partials_callable_objects_and_builtins():
    listener = Listener()
    invoke(listener.one, "x", "y")
    invoke(listener, "x", "y")
    invoke(functools.partial(listener.one), "p", "q")
    seen = []
    invoke(seen.append, "b", "ignored")
    assert listener.calls == [("one", "x"), ("call", "x", "y"), ("one", "p")]
    assert seen == ["b"]


def test_invoke_returns_the_callback_result():
    assert invoke(lambda a: a * 2, 21, "extra") == 42


# ── premultiplied pixels ─────────────────────────────────────────────────────

def test_premultiplied_bgra_orders_the_channels_and_scales_by_alpha():
    img = Image.new("RGBA", (2, 1))
    img.putpixel((0, 0), (200, 100, 50, 128))
    img.putpixel((1, 0), (10, 20, 30, 0))
    assert premultiplied_bgra(img) == bytes([25, 50, 100, 128, 0, 0, 0, 0])


def test_premultiplied_bgra_keeps_opaque_pixels_exact():
    assert premultiplied_bgra(Image.new("RGBA", (1, 1), (1, 2, 3, 255))) == bytes([3, 2, 1, 255])


def test_premultiplied_bgra_accepts_other_modes():
    assert premultiplied_bgra(Image.new("RGB", (1, 1), (1, 2, 3))) == bytes([3, 2, 1, 255])
    assert premultiplied_bgra(Image.new("L", (1, 1), 7)) == bytes([7, 7, 7, 255])
    assert premultiplied_bgra(Image.new("RGBa", (1, 1), (10, 20, 30, 40))) == bytes([30, 20, 10, 40])


def test_premultiplied_bgra_has_four_bytes_per_pixel():
    assert len(premultiplied_bgra(solid(17, 9))) == 17 * 9 * 4


# ── tray helpers ─────────────────────────────────────────────────────────────

def test_clip_leaves_short_text_alone():
    assert clip("Claude Usage Monitor", 127) == "Claude Usage Monitor"
    assert clip("x" * 127, 127) == "x" * 127


def test_clip_cuts_long_text_with_an_ellipsis_within_the_limit():
    clipped = clip("x" * 500, 127)
    assert utf16_units(clipped) == 127 and clipped.endswith("…") and clipped.startswith("x" * 126)


def test_clip_counts_utf16_units_and_never_splits_a_pair():
    clipped = clip("\U0001F600" * 100, 127)  # each emoji is two units
    assert utf16_units(clipped) <= 127
    assert clipped.endswith("…") and set(clipped[:-1]) == {"\U0001F600"}


def test_clip_drops_nul_characters():
    assert clip("a\0b", 10) == "ab"


def sized(*sizes: int) -> dict[int, Image.Image]:
    return {size: Image.new("RGBA", (size, size)) for size in sizes}


def test_pick_image_prefers_the_exact_size():
    images = sized(16, 24, 32)
    assert pick_image(images, 24)[0] == 24


def test_pick_image_scales_down_from_the_next_larger_size():
    assert pick_image(sized(16, 24, 32), 20)[0] == 24
    assert pick_image(sized(16, 32), 17)[0] == 32


def test_pick_image_falls_back_to_the_largest_when_all_are_smaller():
    assert pick_image(sized(16, 24), 32)[0] == 24


def test_pick_image_with_nothing_is_none():
    assert pick_image({}, 16) is None


# ── menu planning ────────────────────────────────────────────────────────────

def labels(items):
    return [i.label if not i.separator else "-" for i in items]


def test_tidy_items_drops_stray_separators():
    a, b = MenuItem("A"), MenuItem("B")
    assert labels(tidy_items([SEPARATOR, a, SEPARATOR, SEPARATOR, b, SEPARATOR])) == ["A", "-", "B"]
    assert tidy_items([SEPARATOR, SEPARATOR]) == []
    assert tidy_items([]) == []


def test_build_entries_numbers_rows_by_position_and_sets_flags():
    entries = build_entries([
        MenuItem("Floating", checked=True),
        SEPARATOR,
        MenuItem("Settings", enabled=False),
        MenuItem("Exit"),
    ])
    assert [e.command for e in entries] == [1, 0, 3, 4]
    assert entries[0].flags == win32.MF_STRING | win32.MF_CHECKED
    assert entries[1].flags == win32.MF_SEPARATOR
    assert entries[2].flags == win32.MF_STRING | win32.MF_GRAYED | win32.MF_DISABLED
    assert entries[3].flags == win32.MF_STRING
    assert [e.text for e in entries] == ["Floating", "", "Settings", "Exit"]


def test_build_entries_escapes_ampersands_so_they_draw_literally():
    (entry,) = build_entries([MenuItem("Update & restart")])
    assert entry.text == "Update && restart"


def test_build_entries_of_nothing_is_empty():
    assert build_entries([]) == [] and build_entries([SEPARATOR]) == []


def test_menu_item_defaults():
    item = MenuItem("Open")
    assert item.action is None and not item.checked and item.enabled and not item.separator
    assert SEPARATOR.separator


WORK = Rect(0, 0, 1920, 1032)
BASE = win32.TPM_RETURNCMD | win32.TPM_RIGHTBUTTON | win32.TPM_NONOTIFY


def test_popup_flags_open_away_from_the_screen_edge():
    assert popup_flags(10, 10, WORK) == BASE
    assert popup_flags(10, 1000, WORK) == BASE | win32.TPM_BOTTOMALIGN
    assert popup_flags(1900, 10, WORK) == BASE | win32.TPM_RIGHTALIGN
    assert popup_flags(1900, 1000, WORK) == BASE | win32.TPM_BOTTOMALIGN | win32.TPM_RIGHTALIGN
    assert popup_flags(1900, 1000, None) == BASE


# ── controller window naming ─────────────────────────────────────────────────

def test_a_dev_instance_gets_its_own_controller_class(monkeypatch):
    monkeypatch.delenv("CUM_DEV_INSTANCE", raising=False)
    normal = controller_class_name()
    monkeypatch.setenv("CUM_DEV_INSTANCE", "1")
    dev = controller_class_name()
    assert dev != normal and dev.startswith(normal)
    monkeypatch.setenv("CUM_DEV_INSTANCE", "0")
    assert controller_class_name() == normal


# ── UIHost: posting, timers, quitting ────────────────────────────────────────

@UI
def test_post_from_another_thread_runs_on_the_ui_thread_in_order(host):
    ran = []

    def worker():
        for i in range(50):
            host.post(lambda i=i: ran.append((i, threading.get_ident())))
        host.post(host.quit)

    thread = threading.Thread(target=worker)
    thread.start()
    assert run_loop(host) == 0
    thread.join()
    assert [i for i, _ in ran] == list(range(50))
    assert {tid for _, tid in ran} == {threading.get_ident()}


@UI
def test_many_threads_posting_at_once_lose_nothing(host):
    ran = []

    def hammer(n):
        for i in range(500):
            host.post(lambda n=n, i=i: ran.append((n, i)))

    threads = [threading.Thread(target=hammer, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    host.post(host.quit)  # the queue is FIFO, so this runs after everything above
    assert run_loop(host) == 0
    assert len(ran) == 8 * 500
    for n in range(8):
        assert [i for who, i in ran if who == n] == list(range(500))  # per-thread order survives


@UI
def test_a_burst_of_posts_wakes_the_loop_once(host):
    ran = []
    for i in range(1000):
        host.post(lambda i=i: ran.append(i))
    msg = wintypes.MSG()
    wakeups = 0
    wake = win32.WM_APP_POST
    while win32.PeekMessageW(ctypes.byref(msg), host.hwnd, wake, wake, win32.PM_REMOVE):
        wakeups += 1
    assert wakeups == 1 and ran == []
    win32.SendMessageW(host.hwnd, win32.WM_APP_POST, 0, 0)  # deliver it by hand
    assert ran == list(range(1000))


@UI
def test_work_posted_while_draining_waits_for_the_next_round(host):
    order = []

    def first():
        order.append("first")
        host.post(lambda: order.append("second"))

    host.post(first)
    win32.SendMessageW(host.hwnd, win32.WM_APP_POST, 0, 0)
    assert order == ["first"]  # a chain of posts must not starve the loop
    assert pump(until=lambda: len(order) == 2)


@UI
def test_a_failing_posted_call_does_not_stop_the_others(host, caplog):
    ran = []

    def bad():
        raise RuntimeError("boom")

    host.post(bad)
    host.post(lambda: ran.append("after"))
    with caplog.at_level("ERROR", logger="usage_monitor"):
        win32.SendMessageW(host.hwnd, win32.WM_APP_POST, 0, 0)
    assert ran == ["after"]
    assert any("posted call failed" in r.getMessage() for r in caplog.records)


@UI
def test_a_repeating_timer_ticks_until_it_is_killed(host):
    ticks = []
    timer_id = host.set_timer(20, lambda: ticks.append(1))
    assert pump(until=lambda: len(ticks) >= 3)
    host.kill_timer(timer_id)
    count = len(ticks)
    pump(seconds=0.2)
    assert len(ticks) == count
    host.kill_timer(timer_id)  # killing it again is harmless


@UI
def test_a_one_shot_timer_fires_once(host):
    fired = []
    host.set_timer(20, lambda: fired.append(1), repeat=False)
    pump(seconds=0.3)
    assert fired == [1] and host._timers == {}


@UI
def test_a_tiny_interval_is_raised_to_the_minimum(host):
    ticks = []
    host.set_timer(0, lambda: ticks.append(1), repeat=False)
    assert pump(until=lambda: ticks)


@UI
def test_timers_can_be_set_and_killed_from_another_thread(host):
    ticks, box = [], {}
    thread = threading.Thread(
        target=lambda: box.update(id=host.set_timer(20, lambda: ticks.append(threading.get_ident())))
    )
    thread.start()
    thread.join()
    assert pump(until=lambda: len(ticks) >= 2)
    assert set(ticks) == {threading.get_ident()}
    thread = threading.Thread(target=lambda: host.kill_timer(box["id"]))
    thread.start()
    thread.join()
    pump(seconds=0.1)
    count = len(ticks)
    pump(seconds=0.2)
    assert len(ticks) == count


@UI
def test_a_failing_timer_callback_is_logged_once_and_keeps_ticking(host, caplog):
    calls = []

    def bad():
        calls.append(1)
        raise RuntimeError("boom")

    host.set_timer(20, bad)
    with caplog.at_level("ERROR", logger="usage_monitor"):
        assert pump(until=lambda: len(calls) >= 3)
    assert sum("callback failed" in r.getMessage() for r in caplog.records) == 1


@UI
def test_quit_ends_run_and_destroys_the_controller(host):
    hwnd = host.hwnd
    threading.Timer(0.1, host.quit).start()
    assert run_loop(host) == 0
    assert host.hwnd == 0 and not win32.IsWindow(hwnd)
    host.post(lambda: None)  # after shutdown: dropped, not an error
    host.quit()
    host.close()


@UI
def test_quit_passes_the_exit_code_to_run(host):
    host.post(lambda: host.quit(7))
    assert run_loop(host) == 7


@UI
def test_wm_close_quits(host):
    win32.PostMessageW(host.hwnd, win32.WM_CLOSE, 0, 0)
    assert run_loop(host) == 0 and host.hwnd == 0


@UI
def test_run_must_be_called_on_the_thread_that_made_the_host(host):
    errors = []

    def attempt():
        try:
            host.run()
        except RuntimeError as exc:
            errors.append(str(exc))

    thread = threading.Thread(target=attempt)
    thread.start()
    thread.join(5)
    assert len(errors) == 1 and "thread" in errors[0]


@UI
def test_run_on_a_closed_host_raises(host):
    host.close()
    with pytest.raises(RuntimeError):
        host.run()


@UI
def test_two_hosts_can_run_one_after_the_other_on_one_thread():
    for _ in range(2):  # a stray WM_QUIT from the first would end the second at once
        host = new_host()
        host.set_timer(50, host.quit, repeat=False)
        assert run_loop(host) == 0


@UI
def test_cleanups_run_newest_first_and_a_failure_does_not_stop_the_rest(host, caplog):
    order = []

    def broken():
        order.append("broken")
        raise RuntimeError("boom")

    host.add_cleanup(lambda: order.append("first"))
    host.add_cleanup(broken)
    remove = host.add_cleanup(lambda: order.append("removed"))
    host.add_cleanup(lambda: order.append("last"))
    remove()
    with caplog.at_level("ERROR", logger="usage_monitor"):
        host.close()
    assert order == ["last", "broken", "first"]


@UI
def test_ctrl_c_quits_the_loop_when_asked_to(host):
    previous = signal.getsignal(signal.SIGINT)
    seen = {}

    def interrupt():
        seen["handler"] = signal.getsignal(signal.SIGINT)
        signal.raise_signal(signal.SIGINT)

    host.set_timer(30, interrupt, repeat=False)
    assert run_loop(host, handle_sigint=True) == 0
    assert seen["handler"] is not previous
    assert signal.getsignal(signal.SIGINT) is previous


# ── UIHost: notifications ────────────────────────────────────────────────────

@UI
def test_session_lock_and_unlock_reach_listeners(host):
    seen = []
    host.on_session_change(seen.append)
    win32.PostMessageW(host.hwnd, win32.WM_WTSSESSION_CHANGE, win32.WTS_SESSION_LOCK, 1)
    win32.PostMessageW(host.hwnd, win32.WM_WTSSESSION_CHANGE, 5, 1)  # console connect: ignored
    win32.PostMessageW(host.hwnd, win32.WM_WTSSESSION_CHANGE, win32.WTS_SESSION_UNLOCK, 1)
    assert pump(until=lambda: len(seen) == 2)
    assert seen == [True, False] and host.locked is False


@UI
def test_display_changes_reach_listeners_with_or_without_arguments(host):
    seen = []
    host.on_display_change(lambda: seen.append("a"))
    host.on_display_change(lambda *args: seen.append(("b", args)))
    win32.SendMessageW(host.hwnd, win32.WM_DISPLAYCHANGE, 32, win32.make_lparam(3440, 1440))
    assert seen == ["a", ("b", ())]


@UI
def test_setting_changes_name_the_area(host):
    named, bare = [], []
    host.on_settings_change(named.append)
    host.on_settings_change(lambda: bare.append(1))
    win32.SendMessageW(host.hwnd, win32.WM_SETTINGCHANGE, win32.SPI_SETWORKAREA, 0)
    text = ctypes.create_unicode_buffer("ImmersiveColorSet")
    win32.SendMessageW(host.hwnd, win32.WM_SETTINGCHANGE, 0, ctypes.addressof(text))
    win32.SendMessageW(host.hwnd, win32.WM_SETTINGCHANGE, 0x1234, 0)
    assert named == ["WorkArea", "ImmersiveColorSet", ""]
    assert len(bare) == 3


@UI
def test_app_settings_changes_reach_the_same_listeners_from_any_thread(host):
    areas = []
    host.on_settings_change(areas.append)
    host.on_settings_change(lambda: areas.append("bare"))
    thread = threading.Thread(target=lambda: [host.notify_settings_changed() for _ in range(5)])
    thread.start()
    thread.join()
    assert pump(until=lambda: len(areas) >= 2)
    pump(seconds=0.05)
    assert areas == ["app", "bare"]  # five notices in a row became one
    host.notify_settings_changed()
    assert pump(until=lambda: len(areas) >= 4)
    assert areas == ["app", "bare", "app", "bare"]


@UI
def test_taskbar_created_reaches_listeners(host):
    seen = []
    host.on_taskbar_created(lambda: seen.append("created"))
    win32.SendMessageW(host.hwnd, win32.RegisterWindowMessageW("TaskbarCreated"), 0, 0)
    assert seen == ["created"]


@UI
def test_unsubscribing_stops_notifications_and_a_failing_listener_is_isolated(host, caplog):
    seen = []

    def broken():
        raise RuntimeError("boom")

    off = host.on_display_change(lambda: seen.append("gone"))
    host.on_display_change(broken)
    host.on_display_change(lambda: seen.append("kept"))
    off()
    off()
    with caplog.at_level("ERROR", logger="usage_monitor"):
        win32.SendMessageW(host.hwnd, win32.WM_DISPLAYCHANGE, 32, 0)
    assert seen == ["kept"]
    assert any("listener" in r.getMessage() for r in caplog.records)


@UI
def test_a_second_launch_can_signal_the_running_instance(host):
    seen = []
    host.on_open_dashboard(lambda: seen.append("open"))
    cls = host._class_name
    assert find_controller(cls) == host.hwnd
    assert signal_running_instance(class_name=cls) is True
    assert pump(until=lambda: seen)
    assert seen == ["open"]
    assert find_controller("CUM.NoSuchController") == 0
    assert signal_running_instance(class_name="CUM.NoSuchController") is False


@UI
def test_private_messages_can_be_routed_to_a_handler(host):
    got = []
    msg = win32.WM_APP + 50
    remove = host.add_message_handler(msg, lambda w, l: got.append((w, l)) or 99)
    assert win32.SendMessageW(host.hwnd, msg, 5, 6) == 99
    with pytest.raises(ValueError):
        host.add_message_handler(msg, lambda w, l: None)
    with pytest.raises(ValueError):
        host.add_message_handler(win32.WM_TIMER, lambda w, l: None)
    remove()
    assert win32.SendMessageW(host.hwnd, msg, 5, 6) == 0
    assert got == [(5, 6)]


@UI
def test_end_session_flushes_then_shuts_down(host):
    seen = []
    host.on_end_session(lambda: seen.append("flush"))
    win32.SendMessageW(host.hwnd, win32.WM_ENDSESSION, 0, 0)  # shutdown was cancelled
    assert seen == [] and host.hwnd
    win32.SendMessageW(host.hwnd, win32.WM_ENDSESSION, 1, 0)
    assert seen == ["flush"] and host.hwnd == 0


@UI
def test_the_host_makes_the_process_per_monitor_aware(host):
    assert plat.dpi_awareness() == "per_monitor_v2"


@UI
def test_the_controller_is_a_hidden_top_level_window(host):
    assert win32.IsWindow(host.hwnd) and not win32.IsWindowVisible(host.hwnd)
    style = win32.GetWindowLongPtrW(host.hwnd, win32.GWL_STYLE) & 0xFFFFFFFF
    assert style & win32.WS_POPUP
    parent = ctypes.WinDLL("user32").GetParent
    parent.restype, parent.argtypes = wintypes.HWND, [wintypes.HWND]
    assert not parent(host.hwnd)  # not HWND_MESSAGE: broadcasts would skip it


# ── LayeredWindow ────────────────────────────────────────────────────────────

@UI
def test_layered_window_styles(host):
    win = LayeredWindow(host, draggable=True)
    ex = exstyle(win.hwnd)
    for flag in (win32.WS_EX_LAYERED, win32.WS_EX_TOOLWINDOW, win32.WS_EX_TOPMOST, win32.WS_EX_NOACTIVATE):
        assert ex & flag
    assert not ex & win32.WS_EX_TRANSPARENT
    assert win32.GetClassLongW(win.hwnd, win32.GCL_STYLE) & win32.CS_DBLCLKS
    style = win32.GetWindowLongPtrW(win.hwnd, win32.GWL_STYLE) & 0xFFFFFFFF
    assert style & win32.WS_POPUP and not style & win32.WS_CAPTION
    assert not win.visible and win.dpi >= 96 and win.draggable


@UI
def test_a_child_layered_window_takes_screen_coordinates_and_dies_with_its_parent(host):
    parent = LayeredWindow(host, draggable=False)
    assert parent.update(Image.new("RGBA", (100, 50), (0, 0, 0, 255)), *OFFSCREEN)
    child = LayeredWindow(host, draggable=False, parent=parent.hwnd)
    ex = exstyle(child.hwnd)
    assert ex & win32.WS_EX_LAYERED and ex & win32.WS_EX_NOPARENTNOTIFY and not ex & win32.WS_EX_TOPMOST
    assert win32.GetWindowLongPtrW(child.hwnd, win32.GWL_STYLE) & win32.WS_CHILD
    assert child.parent == parent.hwnd and child.alive
    x, y = OFFSCREEN[0] + 20, OFFSCREEN[1] + 10
    assert child.update(Image.new("RGBA", (10, 10), (255, 0, 0, 255)), x, y)
    assert (child.rect.left, child.rect.top) == (x, y)                 # screen in, screen out
    child.move(x + 5, y + 5)
    assert (child.rect.left, child.rect.top) == (x + 5, y + 5)
    parent.destroy()
    assert not child.alive                                             # gone with its parent, as with Explorer


@UI
def test_a_window_that_is_not_topmost_has_no_topmost_style(host):
    win = LayeredWindow(host, draggable=False, topmost=False)
    assert not exstyle(win.hwnd) & win32.WS_EX_TOPMOST
    win.bring_to_top()
    assert not exstyle(win.hwnd) & win32.WS_EX_TOPMOST


@UI
def test_update_places_the_window_and_stores_premultiplied_pixels(host):
    win = LayeredWindow(host, draggable=False)
    img = Image.new("RGBA", (3, 2), (200, 100, 50, 128))
    assert win.update(img, *OFFSCREEN) is True
    assert win.rect == Rect(-30000, -30000, -29997, -29998)
    assert ctypes.string_at(win._bits, 3 * 2 * 4) == bytes([25, 50, 100, 128] * 6)


@UI
def test_update_without_a_position_keeps_the_window_where_it_is(host):
    win = LayeredWindow(host, draggable=True)
    assert win.update(solid(40, 30), -29000, -28000)
    win.move(-28500, -27500)  # as a drag would
    assert win.update(solid(60, 20))
    assert win.rect == Rect(-28500, -27500, -28440, -27480)
    win.show(solid(10, 10))  # show(image) also keeps the position
    assert win.rect == Rect(-28500, -27500, -28490, -27490)


@UI
def test_update_accepts_rgb_images(host):
    win = LayeredWindow(host, draggable=False)
    assert win.update(Image.new("RGB", (4, 4), (9, 8, 7)), *OFFSCREEN)
    assert ctypes.string_at(win._bits, 4) == bytes([7, 8, 9, 255])


@UI
def test_update_rejects_an_empty_image(host):
    win = LayeredWindow(host, draggable=False)
    assert win.update(Image.new("RGBA", (0, 0)), *OFFSCREEN) is False


@UI
def test_300_updates_of_one_size_do_not_grow_the_gdi_or_user_object_counts(host):
    win = LayeredWindow(host, draggable=True)
    img = solid(120, 80)
    assert win.update(img, *OFFSCREEN)
    gdi, user = gdi_objects(), user_objects()
    for i in range(300):
        img.paste((i % 256, 20, 40, 255), (0, 0, 120, 80))
        assert win.update(img, *OFFSCREEN)
    assert gdi_objects() == gdi and user_objects() == user


@UI
def test_updates_that_change_size_follow_the_image_and_do_not_leak(host):
    win = LayeredWindow(host, draggable=True)
    first, second = solid(100, 60, (10, 20, 30, 255)), solid(220, 40, (200, 100, 50, 128))
    assert win.update(first, *OFFSCREEN) and win.update(second, *OFFSCREEN)  # back to back
    assert (win.rect.width, win.rect.height) == (220, 40)
    assert ctypes.string_at(win._bits, 220 * 40 * 4) == premultiplied_bgra(second)
    gdi, user = gdi_objects(), user_objects()
    for i in range(300):
        size = (100, 60) if i % 2 else (220, 40)
        assert win.update(solid(*size), *OFFSCREEN)
        assert (win.rect.width, win.rect.height) == size
    assert gdi_objects() == gdi and user_objects() == user


@UI
def test_move_show_hide_and_visibility(host):
    win = LayeredWindow(host, draggable=True)
    win.update(solid(40, 30), *OFFSCREEN)
    win.move(-29000, -28000)
    assert (win.rect.left, win.rect.top) == (-29000, -28000)
    assert not win.visible
    win.show()
    assert win.visible
    win.hide()
    assert not win.visible
    win.show(solid(50, 20), -29500, -28500)  # show(image, x, y) updates first
    assert win.visible and win.rect == Rect(-29500, -28500, -29450, -28480)


@UI
def test_click_through_toggles_the_transparent_style(host):
    win = LayeredWindow(host, draggable=False)
    win.set_click_through(True)
    assert exstyle(win.hwnd) & win32.WS_EX_TRANSPARENT and exstyle(win.hwnd) & win32.WS_EX_LAYERED
    win.set_click_through(False)
    assert not exstyle(win.hwnd) & win32.WS_EX_TRANSPARENT and exstyle(win.hwnd) & win32.WS_EX_LAYERED


@UI
def test_bring_to_top_keeps_a_topmost_window_topmost(host):
    win = LayeredWindow(host, draggable=True)
    win.update(solid(30, 30), *OFFSCREEN)
    win.show()
    win.bring_to_top()
    assert exstyle(win.hwnd) & win32.WS_EX_TOPMOST and win.visible


@UI
def test_clicks_never_activate_the_window(host):
    win = LayeredWindow(host, draggable=True)
    assert win32.SendMessageW(win.hwnd, win32.WM_MOUSEACTIVATE, 0, 0) == win32.MA_NOACTIVATE


@UI
def test_a_draggable_window_is_all_caption(host):
    win = LayeredWindow(host, draggable=True)
    assert win32.SendMessageW(win.hwnd, win32.WM_NCHITTEST, 0, win32.make_lparam(5, 5)) == win32.HTCAPTION


@UI
def test_a_draggable_window_reports_double_and_right_clicks_in_screen_coordinates(host):
    win = LayeredWindow(host, draggable=True)
    seen = []
    win.on_double_click = lambda x, y: seen.append(("double", x, y))
    win.on_right_click = lambda x, y: seen.append(("right", x, y))
    # The double click is swallowed (return 0), so nothing maximizes.
    double = win32.SendMessageW(win.hwnd, win32.WM_NCLBUTTONDBLCLK, win32.HTCAPTION, win32.make_lparam(-100, 50))
    right = win32.SendMessageW(win.hwnd, win32.WM_NCRBUTTONUP, win32.HTCAPTION, win32.make_lparam(1900, -20))
    assert (double, right) == (0, 0)
    assert seen == [("double", -100, 50), ("right", 1900, -20)]


@UI
def test_a_double_click_without_a_callback_is_still_swallowed(host):
    win = LayeredWindow(host, draggable=True)
    assert win32.SendMessageW(win.hwnd, win32.WM_NCLBUTTONDBLCLK, win32.HTCAPTION, 0) == 0
    assert win32.SendMessageW(win.hwnd, win32.WM_NCRBUTTONUP, win32.HTCAPTION, 0) == 0


@UI
def test_the_end_of_a_drag_reports_the_new_top_left(host):
    win = LayeredWindow(host, draggable=True)
    win.update(solid(60, 40), *OFFSCREEN)
    ends = []
    win.on_drag_end = lambda x, y: ends.append((x, y))
    win32.SendMessageW(win.hwnd, win32.WM_ENTERSIZEMOVE, 0, 0)
    assert win.dragging
    win.move(-29000, -28000)  # what the native drag does to the window
    win32.SendMessageW(win.hwnd, win32.WM_EXITSIZEMOVE, 0, 0)
    assert not win.dragging
    assert ends == [(-29000, -28000)]


@UI
def test_a_click_on_the_caption_that_moves_nothing_is_not_a_drag(host):
    win = LayeredWindow(host, draggable=True)
    win.update(solid(60, 40), *OFFSCREEN)
    ends = []
    win.on_drag_end = lambda x, y: ends.append((x, y))
    win32.SendMessageW(win.hwnd, win32.WM_ENTERSIZEMOVE, 0, 0)  # Windows enters the loop on any caption click
    win32.SendMessageW(win.hwnd, win32.WM_EXITSIZEMOVE, 0, 0)
    assert ends == [] and not win.dragging
    win32.SendMessageW(win.hwnd, win32.WM_EXITSIZEMOVE, 0, 0)  # an exit without an enter still reports
    assert ends == [OFFSCREEN]


@UI
def test_a_draggable_window_reports_hover_enter_and_leave_once(host):
    win = LayeredWindow(host, draggable=True)
    win.update(solid(60, 40), *OFFSCREEN)
    events = []
    win.on_hover = lambda inside: events.append(inside)
    win.on_hover_dwell = lambda: events.append("dwell")
    for _ in range(3):
        win32.SendMessageW(win.hwnd, win32.WM_NCMOUSEMOVE, win32.HTCAPTION, win32.make_lparam(10, 10))
    assert events == [True]
    win32.SendMessageW(win.hwnd, win32.WM_NCMOUSEHOVER, win32.HTCAPTION, 0)
    win32.SendMessageW(win.hwnd, win32.WM_NCMOUSELEAVE, 0, 0)
    win32.SendMessageW(win.hwnd, win32.WM_NCMOUSELEAVE, 0, 0)
    assert events == [True, "dwell", False]


@UI
def test_hiding_a_hovered_window_ends_the_hover(host):
    win = LayeredWindow(host, draggable=True)
    win.update(solid(60, 40), *OFFSCREEN)
    events = []
    win.on_hover = events.append
    win32.SendMessageW(win.hwnd, win32.WM_NCMOUSEMOVE, win32.HTCAPTION, 0)
    win.hide()
    assert events == [True, False]


@UI
def test_a_plain_window_is_all_client(host):
    win = LayeredWindow(host, draggable=False)
    assert win32.SendMessageW(win.hwnd, win32.WM_NCHITTEST, 0, win32.make_lparam(5, 5)) == win32.HTCLIENT


def client_click(win, x, y, *, down=True, up=True):
    lparam = win32.make_lparam(x, y)
    if down:
        win32.SendMessageW(win.hwnd, win32.WM_LBUTTONDOWN, 1, lparam)
    if up:
        win32.SendMessageW(win.hwnd, win32.WM_LBUTTONUP, 0, lparam)


@UI
def test_a_plain_window_reports_a_click_in_screen_coordinates(host):
    win = LayeredWindow(host, draggable=False)
    win.update(solid(100, 50), -30000, -29000)
    clicks = []
    win.on_click = lambda x, y: clicks.append((x, y))
    client_click(win, 10, 20)
    assert clicks == [(-29990, -28980)]


@UI
def test_a_release_outside_the_window_or_without_a_press_is_not_a_click(host):
    win = LayeredWindow(host, draggable=False)
    win.update(solid(100, 50), *OFFSCREEN)
    clicks = []
    win.on_click = lambda: clicks.append(1)
    client_click(win, 150, 20)  # pressed, released outside
    client_click(win, 10, 20, down=False)  # released without a press
    client_click(win, -5, 20)
    assert clicks == []
    client_click(win, 99, 49)
    assert clicks == [1]


@UI
def test_a_double_click_is_reported_once_when_there_is_a_handler(host):
    win = LayeredWindow(host, draggable=False)
    win.update(solid(100, 50), *OFFSCREEN)
    events = []
    win.on_click = lambda: events.append("click")
    win.on_double_click = lambda: events.append("double")
    lparam = win32.make_lparam(10, 10)
    client_click(win, 10, 10)
    win32.SendMessageW(win.hwnd, win32.WM_LBUTTONDBLCLK, 1, lparam)
    win32.SendMessageW(win.hwnd, win32.WM_LBUTTONUP, 0, lparam)
    assert events == ["click", "double"]


@UI
def test_a_double_click_without_a_handler_counts_as_two_clicks(host):
    win = LayeredWindow(host, draggable=False)
    win.update(solid(100, 50), *OFFSCREEN)
    events = []
    win.on_click = lambda: events.append("click")
    lparam = win32.make_lparam(10, 10)
    client_click(win, 10, 10)
    win32.SendMessageW(win.hwnd, win32.WM_LBUTTONDBLCLK, 1, lparam)
    win32.SendMessageW(win.hwnd, win32.WM_LBUTTONUP, 0, lparam)
    assert events == ["click", "click"]


@UI
def test_a_plain_window_reports_right_clicks_and_hover(host):
    win = LayeredWindow(host, draggable=False)
    win.update(solid(100, 50), -30000, -29000)
    events = []
    win.on_right_click = lambda x, y: events.append(("right", x, y))
    win.on_hover = lambda inside: events.append(("hover", inside))
    win.on_hover_dwell = lambda: events.append("dwell")
    win32.SendMessageW(win.hwnd, win32.WM_RBUTTONUP, 0, win32.make_lparam(7, 9))
    for _ in range(3):
        win32.SendMessageW(win.hwnd, win32.WM_MOUSEMOVE, 0, win32.make_lparam(7, 9))
    win32.SendMessageW(win.hwnd, win32.WM_MOUSEHOVER, 0, 0)
    win32.SendMessageW(win.hwnd, win32.WM_MOUSELEAVE, 0, 0)
    assert events == [("right", -29993, -28991), ("hover", True), "dwell", ("hover", False)]


@UI
def test_a_failing_callback_does_not_break_the_window(host, caplog):
    win = LayeredWindow(host, draggable=True)

    def boom(x, y):
        raise RuntimeError("boom")

    win.on_double_click = boom
    with caplog.at_level("ERROR", logger="usage_monitor"):
        assert win32.SendMessageW(win.hwnd, win32.WM_NCLBUTTONDBLCLK, win32.HTCAPTION, 0) == 0
    assert any("callback" in r.getMessage() for r in caplog.records)


@UI
def test_a_dpi_change_reports_the_new_dpi_and_the_suggested_rect(host):
    win = LayeredWindow(host, draggable=True)
    seen = []
    win.on_dpi_changed = lambda dpi, rect: seen.append((dpi, rect))
    suggested = wintypes.RECT(10, 20, 310, 120)
    win32.SendMessageW(win.hwnd, win32.WM_DPICHANGED, 144 | (144 << 16), ctypes.addressof(suggested))
    assert seen == [(144, Rect(10, 20, 310, 120))]


@UI
def test_destroy_frees_everything_and_can_be_repeated(host):
    warm = LayeredWindow(host, draggable=True)  # the first window also registers the class
    warm.update(solid(10, 10), *OFFSCREEN)
    warm.destroy()
    gdi, user = gdi_objects(), user_objects()
    win = LayeredWindow(host, draggable=True)
    win.update(solid(50, 50), *OFFSCREEN)
    hwnd = win.hwnd
    win.destroy()
    assert win.hwnd == 0 and not win32.IsWindow(hwnd)
    assert gdi_objects() == gdi and user_objects() == user
    win.destroy()
    assert win.update(solid(5, 5), 0, 0) is False
    win.move(1, 1)
    win.show()
    win.hide()
    win.bring_to_top()
    win.set_click_through(True)
    assert win.dpi == 96 and win.rect == Rect(0, 0, 0, 0) and not win.visible


@UI
def test_the_host_destroys_leftover_windows_when_it_shuts_down():
    host = new_host()
    first, second = LayeredWindow(host, draggable=True), LayeredWindow(host, draggable=False)
    first.update(solid(10, 10), *OFFSCREEN)
    hwnds = (first.hwnd, second.hwnd)
    host.close()
    assert not any(win32.IsWindow(h) for h in hwnds)
    assert first.hwnd == 0 and second.hwnd == 0


@UI
def test_calls_from_another_thread_are_forwarded_to_the_ui_thread(host):
    win = LayeredWindow(host, draggable=True)
    img = Image.new("RGBA", (10, 10), (1, 2, 3, 255))
    results = []

    def worker():
        results.append(win.update(img, *OFFSCREEN))
        win.move(-29500, -29500)
        win.show()
        win.set_click_through(True)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert pump(until=lambda: win.visible and exstyle(win.hwnd) & win32.WS_EX_TRANSPARENT)
    assert results == [True]
    assert (win.rect.left, win.rect.top) == (-29500, -29500)
    assert ctypes.string_at(win._bits, 4) == bytes([3, 2, 1, 255])
    thread = threading.Thread(target=win.destroy)
    thread.start()
    thread.join()
    assert pump(until=lambda: win.hwnd == 0)


# ── Menus ────────────────────────────────────────────────────────────────────

@UI
def test_show_menu_builds_the_win32_menu_and_runs_the_chosen_action(host, monkeypatch):
    done = []
    items = [
        MenuItem("&Open", lambda: done.append("open"), checked=True),
        SEPARATOR,
        MenuItem("Off", lambda: done.append("off"), enabled=False),
        MenuItem("Exit", lambda: done.append("exit")),
    ]
    seen = {}

    def fake_track(h, hmenu, x, y):
        count = win32.GetMenuItemCount(hmenu)
        buffer = ctypes.create_unicode_buffer(64)
        win32.GetMenuStringW(hmenu, 0, buffer, 64, win32.MF_BYPOSITION)
        seen.update(
            count=count, first=buffer.value, at=(x, y),
            states=[win32.GetMenuState(hmenu, i, win32.MF_BYPOSITION) for i in range(count)],
        )
        return 4  # the Exit row

    destroyed = []
    real_destroy = win32.DestroyMenu
    monkeypatch.setattr(menu_mod, "_track", fake_track)
    monkeypatch.setattr(win32, "DestroyMenu", lambda hmenu: destroyed.append(hmenu) or real_destroy(hmenu))
    chosen = show_menu(host, items, 120, 340)
    assert chosen is items[3] and done == ["exit"]
    assert seen["count"] == 4 and seen["at"] == (120, 340) and seen["first"] == "&&Open"
    checked, separator, grayed, plain = seen["states"]
    assert checked & win32.MF_CHECKED
    assert separator & win32.MF_SEPARATOR
    assert grayed & win32.MF_GRAYED
    assert not plain & (win32.MF_CHECKED | win32.MF_GRAYED)
    assert len(destroyed) == 1  # the menu never leaks


@UI
def test_a_dismissed_menu_runs_nothing(host, monkeypatch):
    done = []
    monkeypatch.setattr(menu_mod, "_track", lambda *args: 0)
    assert show_menu(host, [MenuItem("A", lambda: done.append("a"))], 0, 0) is None
    assert done == []


@UI
def test_a_failing_menu_action_is_logged_not_raised(host, monkeypatch, caplog):
    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(menu_mod, "_track", lambda *args: 1)
    with caplog.at_level("ERROR", logger="usage_monitor"):
        chosen = show_menu(host, [MenuItem("A", boom)], 0, 0)
    assert chosen is not None
    assert any("menu action" in r.getMessage() for r in caplog.records)


@UI
def test_only_one_menu_is_shown_at_a_time(host, monkeypatch):
    inner = []

    def fake_track(h, hmenu, x, y):
        inner.append(show_menu(host, [MenuItem("Nested")], 0, 0))
        return 0

    monkeypatch.setattr(menu_mod, "_track", fake_track)
    show_menu(host, [MenuItem("Outer")], 0, 0)
    assert inner == [None]
    assert show_menu(host, [], 0, 0) is None  # nothing to show
    assert menu_mod._showing is False


@UI
def test_show_menu_from_another_thread_is_forwarded_to_the_ui_thread(host, monkeypatch):
    threads = []
    monkeypatch.setattr(menu_mod, "_track", lambda *args: threads.append(threading.get_ident()) or 0)
    thread = threading.Thread(target=lambda: show_menu(host, [MenuItem("A")], 0, 0))
    thread.start()
    thread.join()
    assert pump(until=lambda: threads)
    assert threads == [threading.get_ident()]


@UI
def test_a_real_menu_opens_and_closes_when_ended(host):
    done, shown = [], []

    def open_menu():
        host.set_timer(300, win32.EndMenu, repeat=False)  # runs inside the menu's own loop
        shown.append(show_menu(host, [MenuItem("Test item (dev)", lambda: done.append(1))], 0, 0))
        host.quit()

    host.post(open_menu)
    assert run_loop(host) == 0
    assert shown == [None] and done == []
    assert menu_mod._showing is False


# ── Tray icon ────────────────────────────────────────────────────────────────

def circle(size: int, color=(255, 160, 0, 255)) -> Image.Image:
    from PIL import ImageDraw

    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse([1, 1, size - 2, size - 2], fill=color)
    return image


def icon_set(color):
    return {16: circle(16, color), 24: circle(24, color), 32: circle(32, color)}


@pytest.fixture
def tray(host):
    icon = TrayIcon(host, "CUM test (dev)", None, None)
    try:
        yield icon
    finally:
        icon.remove()


@UI
def test_create_hicon_makes_a_32_bit_icon_of_the_image_size():
    win32.DestroyIcon(create_hicon(circle(24)))  # the first icon creates a few system-side GDI objects
    gdi, user = gdi_objects(), user_objects()
    hicon = create_hicon(circle(24))
    try:
        info = win32.ICONINFO()
        assert win32.GetIconInfo(hicon, ctypes.byref(info))
        bitmap = win32.BITMAP()
        assert win32.GetObjectW(info.hbmColor, ctypes.sizeof(bitmap), ctypes.byref(bitmap))
        assert (bitmap.bmWidth, bitmap.bmHeight, bitmap.bmBitsPixel) == (24, 24, 32)
        win32.DeleteObject(info.hbmColor)
        win32.DeleteObject(info.hbmMask)
    finally:
        win32.DestroyIcon(hicon)
    assert gdi_objects() == gdi and user_objects() == user


@UI
@pytest.mark.parametrize("background", [(0, 0, 0), (255, 255, 255)])
def test_icon_alpha_is_straight_not_premultiplied(background):
    """A half-transparent pixel must blend as src*a + bg*(1-a); premultiplied input would not."""
    image = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    image.putpixel((0, 0), (200, 100, 50, 128))
    image.putpixel((1, 0), (200, 100, 50, 255))
    hicon = create_hicon(image)
    bitmap, bits = win32.create_dib_section(16, 16)
    dc = win32.CreateCompatibleDC(None)
    previous = win32.SelectObject(dc, bitmap)
    try:
        fill = bytes([background[2], background[1], background[0], 255]) * 256
        ctypes.memmove(bits, fill, len(fill))
        assert win32.DrawIconEx(dc, 0, 0, hicon, 16, 16, 0, None, win32.DI_NORMAL)
        pixels = ctypes.string_at(bits, 16 * 16 * 4)
    finally:
        win32.SelectObject(dc, previous)
        win32.DeleteDC(dc)
        win32.DeleteObject(bitmap)
        win32.DestroyIcon(hicon)

    def rgb(x):
        b, g, r = pixels[x * 4: x * 4 + 3]
        return r, g, b

    expected_half = tuple(
        round(c * 128 / 255 + b * (1 - 128 / 255)) for c, b in zip((200, 100, 50), background)
    )
    assert all(abs(a - e) <= 1 for a, e in zip(rgb(0), expected_half))
    assert rgb(1) == (200, 100, 50)
    assert rgb(5) == background  # fully transparent pixels show the background


@UI
def test_tray_icon_is_added_updated_and_removed(host, tray):
    assert tray.ensure_added() is True and tray.added
    assert tray._version4  # the shell accepted NOTIFYICON_VERSION_4
    tray.set_images(icon_set((255, 160, 0, 255)))
    assert tray.icon_size in (16, 20, 24, 28, 32)
    user = user_objects()
    for i in range(30):
        tray.set_images(icon_set((i * 8, 160, 0, 255)))
        tray.set_tooltip(f"CUM test (dev) {i}")
    assert user_objects() == user  # every replaced HICON was destroyed
    tray.remove()
    assert not tray.added and tray._hicon is None
    tray.remove()  # repeatable


@UI
def test_an_unchanged_image_set_does_not_rebuild_the_icon(tray):
    tray.ensure_added()
    tray.set_images(icon_set((255, 160, 0, 255)))
    first = tray._hicon
    tray.set_images(icon_set((255, 160, 0, 255)))
    assert tray._hicon == first
    tray.set_images(icon_set((0, 160, 255, 255)))
    assert tray._hicon != first


@UI
def test_the_icon_size_follows_the_taskbar_dpi(tray, monkeypatch):
    tray.ensure_added()
    images = icon_set((255, 160, 0, 255))
    for wanted in (24, 32, 20):  # 20 is not provided: it is scaled down from 24
        monkeypatch.setattr(tray, "_wanted_size", lambda wanted=wanted: wanted)
        tray.set_images({k: v.copy() for k, v in images.items()})
        tray._apply_icon(force=True)
        assert tray.icon_size == wanted
    user = user_objects()
    tray._refresh_icon()  # same wanted size: nothing to rebuild
    assert user_objects() == user


@UI
def test_a_single_large_image_is_scaled_to_the_wanted_size(tray, monkeypatch):
    tray.ensure_added()
    monkeypatch.setattr(tray, "_wanted_size", lambda: 16)
    tray.set_images({32: circle(32)})
    assert tray.icon_size == 16


@UI
def test_the_tooltip_is_cut_to_127_characters(tray):
    tray.ensure_added()
    tray.set_tooltip("x" * 500)
    assert len(tray._tooltip) == 127 and tray._tooltip.endswith("…")
    tray.set_tooltip("short (dev)")
    assert tray._tooltip == "short (dev)"


@UI
def test_notify_sends_a_clipped_info_balloon(host, tray, monkeypatch):
    tray.ensure_added()
    sent = []

    def fake(message, data_ref):
        data = data_ref._obj
        sent.append((message, data.uFlags, data.szInfoTitle, data.szInfo, data.dwInfoFlags))
        return True

    monkeypatch.setattr(win32, "Shell_NotifyIconW", fake)  # no real toast in a test run
    assert tray.notify("T" * 100, "B" * 400) is True
    message, flags, title, body, info_flags = sent[0]
    assert message == win32.NIM_MODIFY and flags & win32.NIF_INFO
    assert (len(title), len(body)) == (63, 255)
    assert info_flags & win32.NIIF_INFO and info_flags & win32.NIIF_RESPECT_QUIET_TIME
    monkeypatch.undo()  # let tray.remove() reach the real shell


@UI
def test_the_icon_adds_itself_again_when_explorer_restarts(host, tray, monkeypatch):
    assert tray.ensure_added()
    calls = []
    real = win32.Shell_NotifyIconW

    def spy(message, data_ref):
        result = real(message, data_ref)
        calls.append((message, bool(result)))
        return result

    monkeypatch.setattr(win32, "Shell_NotifyIconW", spy)
    win32.SendMessageW(host.hwnd, win32.RegisterWindowMessageW("TaskbarCreated"), 0, 0)
    assert tray.added
    assert calls[0][0] == win32.NIM_ADD
    assert (win32.NIM_SETVERSION, True) in calls


@UI
def test_the_icon_keeps_retrying_until_the_shell_accepts_it(host, tray, monkeypatch):
    monkeypatch.setattr(tray_mod, "RETRY_MS", 30)
    real = win32.Shell_NotifyIconW
    shell_ready = {"yes": False}

    def flaky(message, data_ref):
        if message in (win32.NIM_ADD, win32.NIM_MODIFY) and not shell_ready["yes"]:
            return False
        return real(message, data_ref)

    monkeypatch.setattr(win32, "Shell_NotifyIconW", flaky)
    assert tray.ensure_added() is False and not tray.added
    assert tray._retry_timer is not None
    shell_ready["yes"] = True
    assert pump(until=lambda: tray.added)
    assert tray._retry_timer is None


# A toast the shell never showed is not a toast that was shown. These need no window: a host double and a notification
# area double stand in for the real ones, so they run in the plain suite.

class _ToastHost:
    """The part of UIHost that TrayIcon uses; everything runs inline on the calling thread."""

    hwnd = 0

    def __init__(self):
        self.timers = {}
        self._next = 1

    def on_ui_thread(self):
        return True

    def post(self, fn):
        fn()

    def add_message_handler(self, msg, fn):
        return lambda: None

    def on_taskbar_created(self, cb):
        return lambda: None

    def on_display_change(self, cb):
        return lambda: None

    def on_settings_change(self, cb):
        return lambda: None

    def add_cleanup(self, fn):
        return lambda: None

    def set_timer(self, ms, fn, repeat=True):
        self._next += 1
        self.timers[self._next] = fn
        return self._next

    def kill_timer(self, timer_id):
        self.timers.pop(timer_id, None)


class _NotificationArea:
    """One notification-area icon behind Shell_NotifyIconW. A balloon is shown only when the shell accepts a NIM_MODIFY
    that carries NIF_INFO for an icon it holds; ``refuse_balloons`` makes it refuse that many such calls first (Explorer
    busy or restarting), and ``dead`` makes it refuse everything."""

    def __init__(self):
        self.has_icon = False
        self.refuse_balloons = 0
        self.dead = False
        self.balloons = []

    def __call__(self, message, data_ref):
        data = data_ref._obj
        if self.dead:
            return False
        if message == win32.NIM_ADD:
            if self.has_icon:
                return False
            self.has_icon = True
            return True
        if message == win32.NIM_MODIFY:
            if not self.has_icon:
                return False
            if data.uFlags & win32.NIF_INFO:
                if self.refuse_balloons:
                    self.refuse_balloons -= 1
                    return False
                self.balloons.append(data.szInfo)
            return True
        if message == win32.NIM_DELETE:
            self.has_icon = False
        return message in (win32.NIM_SETVERSION, win32.NIM_DELETE)


def _toast_tray(monkeypatch, area):
    monkeypatch.setattr(win32, "Shell_NotifyIconW", area)
    icon = TrayIcon(_ToastHost(), "CUM test (dev)", None, None)
    assert icon.added and area.has_icon
    return icon


@pytest.mark.parametrize(("setup", "shown", "balloons"), [
    pytest.param({}, True, 1, id="the shell shows it"),
    pytest.param({"has_icon": False}, True, 1, id="explorer restarted: icon added again, toast sent again"),
    pytest.param({"refuse_balloons": 1}, True, 1, id="the shell was busy once: the second try is shown"),
    pytest.param({"refuse_balloons": 2}, False, 0, id="the shell refuses the toast even after the icon is added again"),
    pytest.param({"dead": True}, False, 0, id="the shell refuses everything"),
])
def test_notify_says_true_only_when_the_shell_took_the_balloon(monkeypatch, setup, shown, balloons):
    area = _NotificationArea()
    icon = _toast_tray(monkeypatch, area)
    for name, value in setup.items():
        setattr(area, name, value)
    assert icon.notify("Claude Usage Monitor v2.1.0 is available", "Open the tray menu or the dashboard to update") is shown
    assert len(area.balloons) == balloons
    icon.remove()


def test_a_lost_toast_leaves_the_icon_in_place_and_the_next_toast_can_be_shown(monkeypatch):
    """The icon is re-added whether or not the retried toast was accepted, so a later notify() (the app tries again at the
    next state) reaches an icon that is there."""
    area = _NotificationArea()
    icon = _toast_tray(monkeypatch, area)
    area.refuse_balloons = 2
    assert icon.notify("T", "B") is False and icon.added and area.has_icon
    assert icon.notify("T", "B") is True and len(area.balloons) == 1
    icon.remove()


def shell_message(host, event, x=0, y=0, icon_id=1):
    """Deliver one NOTIFYICON_VERSION_4 callback message to the controller, as the shell would."""
    return win32.SendMessageW(
        host.hwnd, win32.WM_APP_TRAY, win32.make_lparam(x, y), win32.make_lparam(event, icon_id)
    )


@UI
def test_a_left_click_calls_back_once_even_when_the_shell_sends_two_messages(host, monkeypatch):
    clicks = []
    icon = TrayIcon(host, "CUM test (dev)", lambda x, y: clicks.append((x, y)), None)
    try:
        assert icon.ensure_added() and icon._version4
        shell_message(host, win32.WM_LBUTTONUP, 640, 1410)
        shell_message(host, win32.NIN_SELECT, 640, 1410)  # the same click, a moment later
        assert clicks == [(640, 1410)]
        monkeypatch.setattr(tray_mod, "CLICK_MERGE_S", 0.0)
        shell_message(host, win32.NIN_KEYSELECT, 1, 2)  # a later, separate selection
        assert clicks == [(640, 1410), (1, 2)]
    finally:
        icon.remove()


@UI
def test_the_menu_callback_gets_the_click_position_with_signs(host):
    seen = []
    icon = TrayIcon(host, "CUM test (dev)", None, lambda x, y: seen.append((x, y)))
    try:
        assert icon.ensure_added() and icon._version4
        shell_message(host, win32.WM_CONTEXTMENU, -50, 1200)
        shell_message(host, win32.WM_CONTEXTMENU, 3400, -3)
        assert seen == [(-50, 1200), (3400, -3)]
    finally:
        icon.remove()


@UI
def test_messages_for_another_icon_or_other_events_are_ignored(host):
    seen = []
    icon = TrayIcon(host, "CUM test (dev)", lambda: seen.append("click"), lambda: seen.append("menu"))
    icon.on_notify_click = lambda: seen.append("toast")
    try:
        assert icon.ensure_added() and icon._version4
        shell_message(host, win32.NIN_SELECT, icon_id=2)
        shell_message(host, win32.WM_CONTEXTMENU, icon_id=2)
        shell_message(host, win32.WM_MOUSEMOVE)
        shell_message(host, win32.WM_RBUTTONUP)  # the menu comes from WM_CONTEXTMENU in version 4
        assert seen == []
        shell_message(host, win32.NIN_BALLOONUSERCLICK)
        assert seen == ["toast"]
    finally:
        icon.remove()


@UI
def test_a_shell_without_version_4_uses_the_legacy_messages(host):
    seen = []
    icon = TrayIcon(host, "CUM test (dev)", lambda: seen.append("click"), lambda x, y: seen.append("menu"))
    try:
        assert icon.ensure_added()
        icon._version4 = False  # as if NIM_SETVERSION had been refused
        win32.SendMessageW(host.hwnd, win32.WM_APP_TRAY, 1, win32.WM_LBUTTONUP)  # wParam is the icon id
        win32.SendMessageW(host.hwnd, win32.WM_APP_TRAY, 1, win32.WM_RBUTTONUP)
        win32.SendMessageW(host.hwnd, win32.WM_APP_TRAY, 9, win32.WM_RBUTTONUP)  # another icon
        assert seen == ["click", "menu"]
    finally:
        icon.remove()


@UI
def test_the_host_removes_the_tray_icon_when_it_shuts_down():
    host = new_host()
    icon = TrayIcon(host, "CUM test (dev)", None, None)
    assert icon.ensure_added()
    host.close()
    assert not icon.added and icon._removed
