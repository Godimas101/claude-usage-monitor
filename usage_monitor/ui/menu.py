"""The native popup menu, shared by the tray icon and the widgets.

show_menu() builds a Win32 popup menu from MenuItem rows, tracks it with TrackPopupMenuEx and runs
the chosen item's action once the menu is gone. The classic tray-menu recipe applies: the host's
controller window is made foreground first (so the menu dismisses when the user clicks away) and a
WM_NULL is posted after tracking.

build_entries() and popup_flags() hold the decisions (separator tidying, mnemonic escaping,
which way the menu opens) and are tested without a desktop.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from usage_monitor.log import get_logger
from usage_monitor.ui import win32
from usage_monitor.ui.host import UIHost
from usage_monitor.ui.platform import Rect, work_area_at

log = get_logger(__name__)


@dataclass(frozen=True)
class MenuItem:
    """One menu row. ``action`` runs after the menu closes. A separator ignores everything else."""

    label: str = ""
    action: Callable[[], None] | None = None
    checked: bool = False
    enabled: bool = True
    separator: bool = False


SEPARATOR = MenuItem(separator=True)


@dataclass(frozen=True)
class MenuEntry:
    """A planned Win32 menu row: its command id, AppendMenu flags, text and the item behind it."""

    command: int
    flags: int
    text: str
    item: MenuItem


# ── Pure helpers ──────────────────────────────────────────────────────────────

def tidy_items(items: Sequence[MenuItem]) -> list[MenuItem]:
    """Drop leading, trailing and repeated separators, which Windows draws as stray lines."""
    tidy: list[MenuItem] = []
    for item in items:
        if item.separator and (not tidy or tidy[-1].separator):
            continue
        tidy.append(item)
    while tidy and tidy[-1].separator:
        tidy.pop()
    return tidy


def build_entries(items: Sequence[MenuItem]) -> list[MenuEntry]:
    """Plan the Win32 rows for ``items``. A row's command id is its 1-based position (a separator
    takes a position and has command 0), and an "&" in a label is doubled so it is drawn literally
    instead of becoming a keyboard mnemonic."""
    entries: list[MenuEntry] = []
    for item in tidy_items(items):
        if item.separator:
            entries.append(MenuEntry(0, win32.MF_SEPARATOR, "", item))
            continue
        flags = win32.MF_STRING
        if item.checked:
            flags |= win32.MF_CHECKED
        if not item.enabled:
            flags |= win32.MF_GRAYED | win32.MF_DISABLED
        entries.append(MenuEntry(len(entries) + 1, flags, item.label.replace("&", "&&"), item))
    return entries


def popup_flags(x: int, y: int, area: Rect | None) -> int:
    """TrackPopupMenuEx flags. The menu opens upward from a point in the lower half of ``area``
    and leftward from one in the right half, so a tray or taskbar click never pushes it
    off-screen."""
    flags = win32.TPM_RETURNCMD | win32.TPM_RIGHTBUTTON | win32.TPM_NONOTIFY
    if area is not None:
        center_x, center_y = area.center
        if y >= center_y:
            flags |= win32.TPM_BOTTOMALIGN
        if x >= center_x:
            flags |= win32.TPM_RIGHTALIGN
    return flags


# ── Showing a menu ────────────────────────────────────────────────────────────

_showing = False  # one menu at a time; the UI thread is the only caller


def _create_menu(entries: Sequence[MenuEntry]) -> int:
    hmenu = win32.CreatePopupMenu()
    if not hmenu:
        raise OSError(f"CreatePopupMenu failed: {win32.error_text()}")
    for entry in entries:
        text = None if entry.item.separator else entry.text
        if not win32.AppendMenuW(hmenu, entry.flags, entry.command, text):
            log.warning("AppendMenuW failed for a menu row: %s", win32.error_text())
    return int(hmenu)


def _track(host: UIHost, hmenu: int, x: int, y: int) -> int:
    """Show the menu at (x, y) and return the chosen command id, 0 when it was dismissed."""
    win32.SetForegroundWindow(host.hwnd)
    flags = popup_flags(x, y, work_area_at(x, y))
    command = win32.TrackPopupMenuEx(hmenu, flags, x, y, host.hwnd, None)
    win32.PostMessageW(host.hwnd, win32.WM_NULL, 0, 0)  # lets the menu close properly (KB135788)
    return int(command)


def show_menu(host: UIHost, items: Sequence[MenuItem], x: int, y: int) -> MenuItem | None:
    """Pop up a menu at screen (x, y), wait for it to close, run the chosen action and return the
    chosen item (None when dismissed). Call it on the UI thread; from another thread it is
    forwarded and returns None."""
    global _showing
    if not host.on_ui_thread():
        host.post(lambda: show_menu(host, items, x, y))
        return None
    entries = build_entries(items)
    if not entries or _showing or not host.hwnd:
        return None
    _showing = True
    try:
        hmenu = _create_menu(entries)
        try:
            command = _track(host, hmenu, int(x), int(y))
        finally:
            win32.DestroyMenu(hmenu)
    finally:
        _showing = False
    chosen = next((entry.item for entry in entries if command and entry.command == command), None)
    if chosen is not None and chosen.action is not None:
        try:
            chosen.action()
        except Exception:
            log.exception("menu action %r failed", chosen.label)
    return chosen
