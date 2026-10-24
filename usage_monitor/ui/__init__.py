"""Win32 UI layer for Claude Usage Monitor v2. No Tk: every window is created through ctypes.

    win32.py     typed ctypes bindings and constants
    host.py      UIHost: controller window, message loop, timers, cross-thread post(), session
                 / display / settings / TaskbarCreated notifications
    layered.py   LayeredWindow: per-pixel-alpha window fed with Pillow images
    platform.py  taskbar geometry, monitors, DPI, fullscreen and lock detection, theme colours
    menu.py      native popup menu shared by the tray icon and the widgets
    tray.py      TrayIcon (Shell_NotifyIconW)

Everything touching a window runs on the UI thread. Other threads call UIHost.post().

The classes are also importable from the package (``from usage_monitor.ui import UIHost``); they
load on first use, so importing the package costs nothing.
"""
from __future__ import annotations

import importlib
from typing import Any

_EXPORTS = {
    "UIHost": "host",
    "LayeredWindow": "layered",
    "MenuItem": "menu",
    "show_menu": "menu",
    "TrayIcon": "tray",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(f"{__name__}.{module}"), name)
    globals()[name] = value  # later lookups skip this hook
    return value
