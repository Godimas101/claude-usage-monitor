"""TrayIcon: the notification-area icon, through Shell_NotifyIconW.

The icon is owned by the host's controller window and speaks NOTIFYICON_VERSION_4: the callback
message carries the event in the low word of lParam and the click position in wParam. A left click
(NIN_SELECT, or WM_LBUTTONUP, whichever the shell sends; they are merged into one) calls
``on_left_click(x, y)`` and WM_CONTEXTMENU calls ``on_menu(x, y)``. Callbacks take as many
arguments as they accept.

Icons come from Pillow RGBA images (set_images): HICONs are built with CreateIconIndirect from a
32-bit BGRA DIB (straight alpha) plus an empty mask, in the size the taskbar's DPI wants, and the
replaced HICON is destroyed. The icon re-adds itself when Explorer restarts (TaskbarCreated) and
retries for a minute if the shell was not ready at startup. notify() raises a Windows toast
(NIF_INFO). Create it, and call everything else, on the UI thread; from another thread the
mutating calls are forwarded with UIHost.post().
"""
from __future__ import annotations

import ctypes
import time
from collections.abc import Callable, Mapping
from typing import Any

from PIL import Image

from usage_monitor.log import get_logger
from usage_monitor.ui import win32
from usage_monitor.ui.host import UIHost, invoke
from usage_monitor.ui.platform import DEFAULT_DPI, cursor_pos, taskbar_info

log = get_logger(__name__)

TIP_LIMIT = 127  # szTip holds 128 UTF-16 units including the terminator
TITLE_LIMIT = 63
BODY_LIMIT = 255
CLICK_MERGE_S = 0.25  # NIN_SELECT and WM_LBUTTONUP for one click arrive within milliseconds
RETRY_MS = 2000
RETRY_LIMIT = 30


# ── Pure helpers ──────────────────────────────────────────────────────────────

def _units(char: str) -> int:
    """UTF-16 code units in one character: the Win32 string fields are sized in those."""
    return 2 if ord(char) > 0xFFFF else 1


def clip(text: str, limit: int) -> str:
    """``text`` cut to ``limit`` UTF-16 units, ending in an ellipsis when something was cut. NUL
    characters are dropped (they would end the string early) and no surrogate pair is split."""
    text = text.replace("\0", "")
    if sum(map(_units, text)) <= limit:
        return text
    kept: list[str] = []
    used = 0
    for char in text:
        if used + _units(char) > limit - 1:
            break
        kept.append(char)
        used += _units(char)
    return "".join(kept) + "…"


def pick_image(images: Mapping[int, Image.Image], size: int) -> tuple[int, Image.Image] | None:
    """The best source for a ``size`` px icon: that exact size, else the smallest larger one
    (scaling down looks better than up), else the largest smaller one. None for no images."""
    if not images:
        return None
    if size in images:
        return size, images[size]
    larger = [s for s in images if s > size]
    best = min(larger) if larger else max(images)
    return best, images[best]


# ── HICON from a Pillow image ─────────────────────────────────────────────────

def create_hicon(image: Image.Image) -> int:
    """Build an HICON from an RGBA image. The caller owns it and must DestroyIcon it.

    The colour bitmap is a top-down 32-bit BGRA DIB with straight (not premultiplied) alpha; the
    AND mask is all zeros and is ignored by Windows for an icon that carries alpha.
    """
    rgba = image.convert("RGBA")
    width, height = rgba.size
    color, bits = win32.create_dib_section(width, height)
    mask = None
    try:
        pixels = rgba.tobytes("raw", "BGRA")
        ctypes.memmove(bits, pixels, len(pixels))
        mask_rows = ctypes.create_string_buffer(((width + 15) // 16 * 2) * height)
        mask = win32.CreateBitmap(width, height, 1, 1, mask_rows)
        if not mask:
            raise OSError(f"CreateBitmap failed: {win32.error_text()}")
        info = win32.ICONINFO(True, 0, 0, mask, color)
        ctypes.set_last_error(0)
        hicon = win32.CreateIconIndirect(ctypes.byref(info))
        if not hicon:
            raise OSError(f"CreateIconIndirect failed: {win32.error_text()}")
        return int(hicon)
    finally:
        win32.DeleteObject(color)
        if mask:
            win32.DeleteObject(mask)


# ── The icon ──────────────────────────────────────────────────────────────────

class TrayIcon:
    """One notification-area icon with a tooltip, a left-click callback and a menu callback."""

    def __init__(
        self,
        host: UIHost,
        tooltip: str,
        on_left_click: Callable[..., Any] | None,
        on_menu: Callable[..., Any] | None,
        *,
        icon_id: int = 1,
    ) -> None:
        self._host = host
        self._uid = icon_id
        self._tooltip = clip(tooltip, TIP_LIMIT)
        self._on_left_click = on_left_click
        self._on_menu = on_menu
        # Called when the user clicks a toast raised by notify().
        self.on_notify_click: Callable[..., Any] | None = None
        self._images: dict[int, Image.Image] = {}
        self._images_key: tuple[Any, ...] = ()
        self._hicon: int | None = None
        self._icon_px = 0
        self._placeholder = win32.LoadIconW(None, win32.IDI_APPLICATION)
        self._added = False
        self._version4 = False
        self._removed = False
        self._last_click = float("-inf")
        self._retry_timer: int | None = None
        self._retries = 0

        self._remove_handler = host.add_message_handler(win32.WM_APP_TRAY, self._on_tray_message)
        self._unsubscribers = [
            host.on_taskbar_created(self._on_taskbar_created),
            host.on_display_change(self._refresh_icon),
            host.on_settings_change(self._refresh_icon),
        ]
        self._remove_cleanup = host.add_cleanup(self.remove)
        host.post(self.ensure_added)  # after the app has had the chance to call set_images()

    # ── State ──

    @property
    def added(self) -> bool:
        """True while the shell holds the icon."""
        return self._added

    @property
    def icon_size(self) -> int:
        """Pixel size of the HICON currently shown (0 before set_images)."""
        return self._icon_px

    # ── Public operations ──

    def ensure_added(self) -> bool:
        """Add the icon to the notification area if it is not there. Returns True when it is."""
        if self._removed:
            return False
        if not self._host.on_ui_thread():
            self._host.post(self.ensure_added)
            return self._added
        if self._added or self._add():
            self._cancel_retry()
            return True
        self._schedule_retry()
        return False

    def set_images(self, images: Mapping[int, Image.Image]) -> None:
        """Provide the icon artwork as {pixel size: RGBA image}, for example 16, 24 and 32. The one
        that suits the taskbar's DPI is shown; a size that is missing is scaled from the nearest."""
        if not self._host.on_ui_thread():
            self._host.post(lambda: self.set_images(images))
            return
        cleaned = {int(size): image.convert("RGBA") for size, image in images.items()}
        key = tuple(sorted((size, hash(image.tobytes())) for size, image in cleaned.items()))
        if not cleaned or key == self._images_key:
            return
        self._images, self._images_key = cleaned, key
        self._apply_icon(force=True)

    def set_tooltip(self, text: str) -> None:
        """Set the hover tooltip (cut to 127 characters)."""
        if not self._host.on_ui_thread():
            self._host.post(lambda: self.set_tooltip(text))
            return
        text = clip(text, TIP_LIMIT)
        if text != self._tooltip:
            self._tooltip = text
            self._modify(win32.NIF_TIP | win32.NIF_SHOWTIP)

    def notify(self, title: str, body: str) -> bool:
        """Show a Windows toast (balloon). The title is cut to 63 and the body to 255 characters.
        Returns False when the shell refused it."""
        if not self._host.on_ui_thread():
            self._host.post(lambda: self.notify(title, body))
            return True
        data = self._data(win32.NIF_INFO)
        data.szInfoTitle = clip(title, TITLE_LIMIT)
        data.szInfo = clip(body, BODY_LIMIT)
        data.dwInfoFlags = win32.NIIF_INFO | win32.NIIF_RESPECT_QUIET_TIME
        return self._modify_with(data)

    def remove(self) -> None:
        """Take the icon out of the notification area and release everything. Safe to repeat."""
        if not self._host.on_ui_thread():
            self._host.post(self.remove)
            return
        if self._removed:
            return
        self._removed = True
        self._cancel_retry()
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._remove_handler()
        self._remove_cleanup()
        if self._added:
            win32.Shell_NotifyIconW(win32.NIM_DELETE, ctypes.byref(self._data(0)))
            self._added = False
        if self._hicon:
            win32.DestroyIcon(self._hicon)
            self._hicon = None

    # ── Shell calls ──

    def _data(self, flags: int) -> win32.NOTIFYICONDATAW:
        data = win32.NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(data)
        data.hWnd = self._host.hwnd
        data.uID = self._uid
        data.uFlags = flags
        data.uCallbackMessage = win32.WM_APP_TRAY
        data.hIcon = self._hicon or self._placeholder
        data.szTip = self._tooltip
        return data

    def _add(self) -> bool:
        flags = win32.NIF_MESSAGE | win32.NIF_ICON | win32.NIF_TIP | win32.NIF_SHOWTIP
        data = self._data(flags)
        ctypes.set_last_error(0)
        if not win32.Shell_NotifyIconW(win32.NIM_ADD, ctypes.byref(data)):
            # NIM_ADD can time out although the shell did add the icon; a modify tells which.
            if not win32.Shell_NotifyIconW(win32.NIM_MODIFY, ctypes.byref(data)):
                log.debug("NIM_ADD failed: %s", win32.error_text())
                return False
        data.uVersion = win32.NOTIFYICON_VERSION_4
        self._version4 = bool(win32.Shell_NotifyIconW(win32.NIM_SETVERSION, ctypes.byref(data)))
        self._added = True
        log.info("tray icon added (NOTIFYICON_VERSION_4: %s)", "yes" if self._version4 else "refused")
        return True

    def _modify(self, flags: int) -> bool:
        return self._modify_with(self._data(flags | win32.NIF_MESSAGE))

    def _modify_with(self, data: win32.NOTIFYICONDATAW) -> bool:
        """Apply ``data`` to the icon. True only when the shell accepted that change.

        A refused change usually means the shell lost the icon (Explorer restarted) or was busy, so the icon is added
        again and the same change is tried once more. Re-adding alone is not success: it brings the icon back without
        the balloon, the tooltip or the artwork this call carried, and a caller that raised a toast (``notify``) must
        not be told it was shown when nobody saw it."""
        if not self._added:
            return self.ensure_added() and self._modify_with(data)
        if win32.Shell_NotifyIconW(win32.NIM_MODIFY, ctypes.byref(data)):
            return True
        log.debug("NIM_MODIFY failed (%s); adding the icon again", win32.error_text())
        self._added = False
        return self.ensure_added() and bool(win32.Shell_NotifyIconW(win32.NIM_MODIFY, ctypes.byref(data)))

    # ── Artwork ──

    def _wanted_size(self) -> int:
        info = taskbar_info()
        dpi = info.dpi if info else DEFAULT_DPI
        return win32.GetSystemMetricsForDpi(win32.SM_CXSMICON, dpi) or 16

    def _refresh_icon(self) -> None:
        """The taskbar's DPI may have changed: rebuild the HICON when the wanted size did."""
        if not self._removed and self._images:
            self._apply_icon(force=False)

    def _apply_icon(self, *, force: bool) -> None:
        size = self._wanted_size()
        if not force and size == self._icon_px:
            return
        choice = pick_image(self._images, size)
        if choice is None:
            return
        image = choice[1]
        if image.size != (size, size):
            image = image.resize((size, size), Image.LANCZOS)
        try:
            hicon = create_hicon(image)
        except OSError as exc:
            log.warning("cannot build the %d px tray icon: %s", size, exc)
            return
        previous, self._hicon, self._icon_px = self._hicon, hicon, size
        self._modify(win32.NIF_ICON)
        if previous:
            win32.DestroyIcon(previous)

    # ── Re-adding ──

    def _on_taskbar_created(self) -> None:
        """Explorer restarted and the icon is gone: add it again."""
        self._added = False
        self.ensure_added()

    def _schedule_retry(self) -> None:
        if self._retry_timer is None and not self._removed:
            self._retries = 0
            self._retry_timer = self._host.set_timer(RETRY_MS, self._retry)

    def _retry(self) -> None:
        self._retries += 1
        if self._removed or self._add():
            self._cancel_retry()
        elif self._retries >= RETRY_LIMIT:
            log.warning("the tray icon could not be added; waiting for Explorer to restart")
            self._cancel_retry()

    def _cancel_retry(self) -> None:
        if self._retry_timer is not None:
            self._host.kill_timer(self._retry_timer)
            self._retry_timer = None

    # ── Shell events ──

    def _on_tray_message(self, wparam: int, lparam: int) -> int:
        event = win32.loword(lparam)
        if self._version4:
            icon_id = win32.hiword(lparam)
            x, y = win32.signed16(wparam), win32.signed16(wparam >> 16)
        else:
            icon_id = wparam
            x, y = cursor_pos() or (0, 0)
        if icon_id != self._uid:
            return 0
        if event in (win32.WM_LBUTTONUP, win32.NIN_SELECT, win32.NIN_KEYSELECT):
            self._left_click(x, y)
        elif event == (win32.WM_CONTEXTMENU if self._version4 else win32.WM_RBUTTONUP):
            self._fire(self._on_menu, x, y)
        elif event == win32.NIN_BALLOONUSERCLICK:
            self._fire(self.on_notify_click)
        return 0

    def _left_click(self, x: int, y: int) -> None:
        now = time.monotonic()
        if now - self._last_click < CLICK_MERGE_S:
            return
        self._last_click = now
        self._fire(self._on_left_click, x, y)

    @staticmethod
    def _fire(callback: Callable[..., Any] | None, *args: Any) -> None:
        if callback is None:
            return
        try:
            invoke(callback, *args)
        except Exception:
            log.exception("tray callback %s failed", getattr(callback, "__qualname__", callback))
