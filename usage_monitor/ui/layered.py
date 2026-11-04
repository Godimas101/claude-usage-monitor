"""LayeredWindow: a per-pixel-alpha popup window that shows Pillow images.

The window is WS_POPUP with WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE (and
WS_EX_TOPMOST) and the CS_DBLCLKS class style. update() converts the image to premultiplied BGRA,
copies it into a DIB section and hands the DIB to UpdateLayeredWindow. The memory DC and DIB are
reused until the image size changes, so a steady stream of same-size frames allocates nothing and
leaks no GDI objects.

Two input modes, chosen by ``draggable``:

* draggable (the floating widget): the whole window is the caption (WM_NCHITTEST -> HTCAPTION), so
  Windows runs the native drag. Double click, right click, hover and the end of a drag arrive as
  non-client messages. Nothing maximizes.
* not draggable (the taskbar strip, tooltips): HTCLIENT, with ordinary client mouse messages.

Pixels whose alpha is 0 let the mouse through to whatever lies behind, as for any layered window.

With ``parent`` (the taskbar strip, #29) the window is instead a layered child of that window:
WS_CHILD | WS_CLIPSIBLINGS with WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_NOPARENTNOTIFY. Windows 11
lifts the taskbar above every always-on-top window while Start, Search or a tray flyout is open, and
a child is composed with it, so it stays in sight. Callers keep using screen coordinates; the window
converts them to the parent's, which is what UpdateLayeredWindow and SetWindowPos take for a child.
NOPARENTNOTIFY keeps creating and destroying it from waiting on the parent's process. A top-level
window is excluded from Aero Peek, so peeking at a window from its taskbar thumbnail leaves it shown.

Callbacks are plain attributes and are called with as many arguments as they accept:

    on_click(x, y)            left click, screen coordinates (not draggable windows only)
    on_double_click(x, y)     left double click, screen coordinates
    on_right_click(x, y)      right click, screen coordinates
    on_drag_end(x, y)         a drag moved the window; its new top-left (draggable only). A click
                              on the caption that moves nothing does not report.
    on_hover(inside: bool)    the pointer entered (True) or left (False)
    on_hover_dwell()          the pointer rested over the window for about 400 ms
    on_dpi_changed(dpi, rect) Windows moved the window to a monitor of another DPI; ``rect`` is
                              the size and position Windows suggests. Re-render at the new scale
                              and call update(image) to stay put, or update(image, rect.left,
                              rect.top) to take the suggested spot.

Create and use a LayeredWindow on the UI thread. update(), move(), show(), hide(), bring_to_top(),
set_click_through() and destroy() called from another thread are forwarded with UIHost.post().
"""
from __future__ import annotations

import ctypes
from collections.abc import Callable
from ctypes import wintypes
from typing import Any

from PIL import Image

from usage_monitor.log import get_logger
from usage_monitor.ui import win32
from usage_monitor.ui.host import UIHost, create_window, invoke, register_window_class
from usage_monitor.ui.platform import DEFAULT_DPI, Rect

log = get_logger(__name__)

LAYERED_CLASS = "ClaudeUsageMonitor.Layered"
HOVER_DWELL_MS = 400


def _exclude_from_peek(hwnd: int) -> None:
    """Keep the window shown while Aero Peek previews another window. Best effort."""
    on = wintypes.BOOL(True)
    result = win32.DwmSetWindowAttribute(hwnd, win32.DWMWA_EXCLUDED_FROM_PEEK, ctypes.byref(on), ctypes.sizeof(on))
    if result != 0:
        log.debug("could not exclude window %s from Peek (HRESULT %#x)", hwnd, result & 0xFFFFFFFF)


def premultiplied_bgra(image: Image.Image) -> bytes:
    """The pixels of ``image`` as premultiplied BGRA, the format UpdateLayeredWindow expects."""
    if image.mode == "RGBa":  # already premultiplied; converting would round twice
        return image.tobytes("raw", "BGRa")
    rgba = image if image.mode == "RGBA" else image.convert("RGBA")
    return rgba.convert("RGBa").tobytes("raw", "BGRa")


class LayeredWindow:
    """A layered popup window showing a Pillow image at a screen position."""

    def __init__(self, host: UIHost, *, draggable: bool, topmost: bool = True,
                 parent: int | None = None) -> None:
        self._host = host
        self._draggable = draggable
        self._parent = int(parent or 0)
        self._topmost = topmost and not self._parent
        self._dc: int | None = None
        self._stock_bitmap: int | None = None
        self._bitmap: int | None = None
        self._bits = 0
        self._size: tuple[int, int] | None = None
        self._blend = win32.BLENDFUNCTION(win32.AC_SRC_OVER, 0, 255, win32.AC_SRC_ALPHA)
        self._tracking = False
        self._hover_armed = False
        self._hovering = False
        self._pressed = False
        self._swallow_click = False
        self._dragging = False
        self._drag_origin: tuple[int, int] | None = None
        self._update_failures = 0
        self._dispatch = self._build_dispatch()

        self.on_click: Callable[..., Any] | None = None
        self.on_double_click: Callable[..., Any] | None = None
        self.on_right_click: Callable[..., Any] | None = None
        self.on_drag_end: Callable[..., Any] | None = None
        self.on_hover: Callable[..., Any] | None = None
        self.on_hover_dwell: Callable[..., Any] | None = None
        self.on_dpi_changed: Callable[..., Any] | None = None

        register_window_class(LAYERED_CLASS, style=win32.CS_DBLCLKS)
        if self._parent:
            ex_style = win32.WS_EX_LAYERED | win32.WS_EX_NOACTIVATE | win32.WS_EX_NOPARENTNOTIFY
            self._hwnd = create_window(LAYERED_CLASS, ex_style, self, parent=self._parent,
                                       style=win32.WS_CHILD | win32.WS_CLIPSIBLINGS)
        else:
            ex_style = win32.WS_EX_LAYERED | win32.WS_EX_TOOLWINDOW | win32.WS_EX_NOACTIVATE
            if self._topmost:
                ex_style |= win32.WS_EX_TOPMOST
            self._hwnd = create_window(LAYERED_CLASS, ex_style, self)
            _exclude_from_peek(self._hwnd)
        self._remove_cleanup = host.add_cleanup(self.destroy)

    # ── State ──

    @property
    def hwnd(self) -> int:
        return self._hwnd

    @property
    def draggable(self) -> bool:
        return self._draggable

    @property
    def parent(self) -> int | None:
        """The window this one is a child of, or None for a top-level window."""
        return self._parent or None

    @property
    def alive(self) -> bool:
        """False once the window is gone, as a child is when its parent is destroyed (Explorer restarting)."""
        return bool(self._hwnd and win32.IsWindow(self._hwnd))

    @property
    def dragging(self) -> bool:
        """True while the user drags the window (between WM_ENTERSIZEMOVE and WM_EXITSIZEMOVE)."""
        return self._dragging

    @property
    def dpi(self) -> int:
        """DPI of the monitor the window is on (96 when unknown)."""
        return int(win32.GetDpiForWindow(self._hwnd) or DEFAULT_DPI) if self._hwnd else DEFAULT_DPI

    @property
    def rect(self) -> Rect:
        """Where the window is on screen, (left, top, right, bottom)."""
        rect = wintypes.RECT()
        if self._hwnd and win32.GetWindowRect(self._hwnd, ctypes.byref(rect)):
            return Rect.from_win(rect)
        return Rect(0, 0, 0, 0)

    @property
    def visible(self) -> bool:
        return bool(self._hwnd and win32.IsWindowVisible(self._hwnd))

    # ── Drawing and placement ──

    def update(self, image: Image.Image, x: int | None = None, y: int | None = None) -> bool:
        """Show ``image`` with its top-left at screen (x, y). The window changes size with the image.

        Without x and y the window stays where it is, which is what a redraw during a drag or after
        a DPI change wants. Does not show a hidden window; call show(). Returns False when Windows
        refused the frame (logged, rate limited).
        """
        if not self._host.on_ui_thread():
            self._host.post(lambda: self.update(image, x, y))
            return True
        if not self._hwnd:
            return False
        rgba = image if image.mode in ("RGBA", "RGBa") else image.convert("RGBA")
        width, height = rgba.size
        if width <= 0 or height <= 0 or not self._ensure_surface(width, height):
            return False
        pixels = premultiplied_bgra(rgba)
        ctypes.memmove(self._bits, pixels, len(pixels))
        if x is None or y is None:
            origin = self.rect
            x, y = origin.left if x is None else x, origin.top if y is None else y
        return self._present(int(x), int(y), width, height)

    def move(self, x: int, y: int) -> None:
        """Move the window without redrawing it."""
        self._on_ui(self._move, int(x), int(y))

    def show(
        self, image: Image.Image | None = None, x: int | None = None, y: int | None = None
    ) -> None:
        """Show the window without activating it. With an image, update() it first."""
        if image is not None:
            self.update(image, x, y)
        self._on_ui(self._show, win32.SW_SHOWNOACTIVATE)

    def hide(self) -> None:
        self._on_ui(self._hide)

    def bring_to_top(self) -> None:
        """Raise the window over other topmost windows (the taskbar) without activating it."""
        self._on_ui(self._raise)

    def set_click_through(self, on: bool) -> None:
        """While on, the mouse passes through the window to whatever is behind it."""
        self._on_ui(self._set_click_through, bool(on))

    def destroy(self) -> None:
        """Destroy the window and free its bitmap. Safe to repeat."""
        self._on_ui(self._destroy)

    # ── Internals: UI-thread operations ──

    def _on_ui(self, fn: Callable[..., None], *args: Any) -> None:
        if self._host.on_ui_thread():
            fn(*args)
        else:
            self._host.post(lambda: fn(*args))

    def _move(self, x: int, y: int) -> None:
        if self._hwnd:
            x, y = self._from_screen(x, y)
            flags = win32.SWP_NOSIZE | win32.SWP_NOZORDER | win32.SWP_NOACTIVATE
            if not win32.SetWindowPos(self._hwnd, None, x, y, 0, 0, flags):
                log.debug("move failed: %s", win32.error_text())

    def _show(self, command: int) -> None:
        if self._hwnd:
            win32.ShowWindow(self._hwnd, command)

    def _hide(self) -> None:
        if self._hwnd:
            win32.ShowWindow(self._hwnd, win32.SW_HIDE)
            self._reset_hover()

    def _raise(self) -> None:
        if self._hwnd:
            order = win32.HWND_TOPMOST if self._topmost else win32.HWND_TOP
            flags = win32.SWP_NOMOVE | win32.SWP_NOSIZE | win32.SWP_NOACTIVATE
            if not win32.SetWindowPos(self._hwnd, order, 0, 0, 0, 0, flags):
                log.debug("bring_to_top failed: %s", win32.error_text())

    def _set_click_through(self, on: bool) -> None:
        if not self._hwnd:
            return
        style = win32.GetWindowLongPtrW(self._hwnd, win32.GWL_EXSTYLE)
        new_style = style | win32.WS_EX_TRANSPARENT if on else style & ~win32.WS_EX_TRANSPARENT
        if new_style != style:
            win32.SetWindowLongPtrW(self._hwnd, win32.GWL_EXSTYLE, new_style)
        if on:
            self._reset_hover()

    def _destroy(self) -> None:
        self._remove_cleanup()
        hwnd, self._hwnd = self._hwnd, 0
        if hwnd:
            win32.DestroyWindow(hwnd)
        self._free_surface()

    # ── Internals: the bitmap ──

    def _ensure_surface(self, width: int, height: int) -> bool:
        """Make the memory DC hold a DIB of exactly width x height (kept while the size repeats)."""
        if self._size == (width, height):
            return True
        try:
            bitmap, bits = win32.create_dib_section(width, height)
        except OSError as exc:
            log.warning("cannot allocate a %dx%d layered bitmap: %s", width, height, exc)
            return False
        if self._dc is None:
            self._dc = win32.CreateCompatibleDC(None)
            if not self._dc:
                win32.DeleteObject(bitmap)
                self._dc = None
                log.warning("CreateCompatibleDC failed: %s", win32.error_text())
                return False
        previous = win32.SelectObject(self._dc, bitmap)
        if self._stock_bitmap is None:
            self._stock_bitmap = previous  # the DC's own 1x1 bitmap, put back before DeleteDC
        if self._bitmap:
            win32.DeleteObject(self._bitmap)  # deselected by the line above
        self._bitmap, self._bits, self._size = bitmap, bits, (width, height)
        return True

    def _free_surface(self) -> None:
        if self._dc:
            if self._stock_bitmap:
                win32.SelectObject(self._dc, self._stock_bitmap)
            if self._bitmap:
                win32.DeleteObject(self._bitmap)
            win32.DeleteDC(self._dc)
        self._dc = self._stock_bitmap = self._bitmap = None
        self._bits, self._size = 0, None

    def _from_screen(self, x: int, y: int) -> tuple[int, int]:
        """A screen point in the coordinates this window is placed in: the parent's for a child."""
        if not self._parent:
            return x, y
        point = wintypes.POINT(x, y)
        win32.ScreenToClient(self._parent, ctypes.byref(point))
        return point.x, point.y

    def _present(self, x: int, y: int, width: int, height: int) -> bool:
        position = wintypes.POINT(*self._from_screen(x, y))
        size = wintypes.SIZE(width, height)
        origin = wintypes.POINT(0, 0)
        ctypes.set_last_error(0)
        if win32.UpdateLayeredWindow(
            self._hwnd, None, ctypes.byref(position), ctypes.byref(size), self._dc,
            ctypes.byref(origin), 0, ctypes.byref(self._blend), win32.ULW_ALPHA,
        ):
            self._update_failures = 0
            return True
        self._update_failures += 1
        if self._update_failures == 1 or self._update_failures % 100 == 0:
            log.warning("UpdateLayeredWindow failed (%d in a row): %s",
                        self._update_failures, win32.error_text())
        return False

    # ── Internals: messages ──

    def handle_message(self, msg: int, wparam: int, lparam: int) -> int | None:
        handler = self._dispatch.get(msg)
        return handler(wparam, lparam) if handler is not None else None

    def _build_dispatch(self) -> dict[int, Callable[[int, int], int | None]]:
        return {
            win32.WM_NCHITTEST: self._on_hit_test,
            win32.WM_MOUSEACTIVATE: lambda _w, _l: win32.MA_NOACTIVATE,
            win32.WM_NCLBUTTONDBLCLK: self._on_nc_double_click,
            win32.WM_NCRBUTTONUP: self._on_nc_right_up,
            win32.WM_NCMOUSEMOVE: self._on_nc_mouse_move,
            win32.WM_NCMOUSEHOVER: self._on_hover_dwell,
            win32.WM_NCMOUSELEAVE: self._on_mouse_leave,
            win32.WM_ENTERSIZEMOVE: self._on_enter_size_move,
            win32.WM_EXITSIZEMOVE: self._on_exit_size_move,
            win32.WM_LBUTTONDOWN: self._on_left_down,
            win32.WM_LBUTTONUP: self._on_left_up,
            win32.WM_LBUTTONDBLCLK: self._on_left_double_click,
            win32.WM_RBUTTONUP: self._on_right_up,
            win32.WM_MOUSEMOVE: self._on_mouse_move,
            win32.WM_MOUSEHOVER: self._on_hover_dwell,
            win32.WM_MOUSELEAVE: self._on_mouse_leave,
            win32.WM_CAPTURECHANGED: self._on_capture_changed,
            win32.WM_DPICHANGED: self._on_dpi_message,
            win32.WM_DPICHANGED_AFTERPARENT: self._on_dpi_after_parent,
            win32.WM_NCDESTROY: self._on_ncdestroy,
        }

    def _fire(self, callback: Callable[..., Any] | None, *args: Any) -> None:
        if callback is None:
            return
        try:
            invoke(callback, *args)
        except Exception:
            name = getattr(callback, "__qualname__", callback)
            log.exception("layered window callback %s failed", name)

    def _on_hit_test(self, _wparam: int, _lparam: int) -> int:
        return win32.HTCAPTION if self._draggable else win32.HTCLIENT

    # Non-client side (draggable windows)

    def _on_nc_double_click(self, wparam: int, lparam: int) -> int:
        if wparam == win32.HTCAPTION:
            self._fire(self.on_double_click, win32.get_x_lparam(lparam), win32.get_y_lparam(lparam))
        return 0  # swallowed either way: the default would maximize

    def _on_nc_right_up(self, wparam: int, lparam: int) -> int:
        if wparam == win32.HTCAPTION:
            self._fire(self.on_right_click, win32.get_x_lparam(lparam), win32.get_y_lparam(lparam))
        return 0

    def _on_nc_mouse_move(self, _wparam: int, _lparam: int) -> None:
        self._track_pointer(nonclient=True)

    def _on_enter_size_move(self, _wparam: int, _lparam: int) -> None:
        self._dragging = True
        rect = self.rect
        self._drag_origin = (rect.left, rect.top)

    def _on_exit_size_move(self, _wparam: int, _lparam: int) -> int:
        """Windows also enters this loop for a plain click on the caption; only report real moves."""
        self._dragging = False
        rect = self.rect
        origin, self._drag_origin = self._drag_origin, None
        if origin != (rect.left, rect.top):
            self._fire(self.on_drag_end, rect.left, rect.top)
        return 0

    # Client side (windows that are not draggable)

    def _client_to_screen(self, lparam: int) -> tuple[int, int]:
        point = wintypes.POINT(win32.get_x_lparam(lparam), win32.get_y_lparam(lparam))
        win32.ClientToScreen(self._hwnd, ctypes.byref(point))
        return point.x, point.y

    def _inside_client(self, lparam: int) -> bool:
        width, height = self._size or (0, 0)
        return 0 <= win32.get_x_lparam(lparam) < width and 0 <= win32.get_y_lparam(lparam) < height

    def _on_left_down(self, _wparam: int, _lparam: int) -> int:
        self._pressed = True
        win32.SetCapture(self._hwnd)  # so the release arrives here even if the pointer slipped off
        return 0

    def _on_left_up(self, _wparam: int, lparam: int) -> int:
        pressed, self._pressed = self._pressed, False
        swallow, self._swallow_click = self._swallow_click, False
        win32.ReleaseCapture()
        if pressed and not swallow and self._inside_client(lparam):
            self._fire(self.on_click, *self._client_to_screen(lparam))
        return 0

    def _on_left_double_click(self, _wparam: int, lparam: int) -> int:
        self._pressed = True
        win32.SetCapture(self._hwnd)
        if self.on_double_click is not None:  # otherwise the release counts as a second click
            self._swallow_click = True
            self._fire(self.on_double_click, *self._client_to_screen(lparam))
        return 0

    def _on_right_up(self, _wparam: int, lparam: int) -> int:
        self._fire(self.on_right_click, *self._client_to_screen(lparam))
        return 0

    def _on_capture_changed(self, _wparam: int, _lparam: int) -> None:
        self._pressed = False
        self._swallow_click = False

    def _on_mouse_move(self, _wparam: int, _lparam: int) -> None:
        self._track_pointer(nonclient=False)

    # Hover (both sides)

    def _track_pointer(self, *, nonclient: bool) -> None:
        """Enter on the first move; ask Windows for the leave and a hover dwell, and re-arm the
        dwell after it fired."""
        if self._tracking and self._hover_armed:
            return
        flags = win32.TME_LEAVE | win32.TME_HOVER  # re-armed for the dwell; the leave stays pending
        if nonclient:
            flags |= win32.TME_NONCLIENT
        request = win32.TRACKMOUSEEVENT(
            ctypes.sizeof(win32.TRACKMOUSEEVENT), flags, self._hwnd, HOVER_DWELL_MS
        )
        if not win32.TrackMouseEvent(ctypes.byref(request)):
            log.debug("TrackMouseEvent failed: %s", win32.error_text())
            return
        self._tracking = self._hover_armed = True
        if not self._hovering:
            self._hovering = True
            self._fire(self.on_hover, True)

    def _on_hover_dwell(self, _wparam: int, _lparam: int) -> int:
        self._hover_armed = False  # TME_HOVER is one-shot
        self._fire(self.on_hover_dwell)
        return 0

    def _on_mouse_leave(self, _wparam: int, _lparam: int) -> int:
        self._reset_hover()
        return 0

    def _reset_hover(self) -> None:
        self._tracking = self._hover_armed = False
        if self._hovering:
            self._hovering = False
            self._fire(self.on_hover, False)

    # Lifecycle

    def _on_dpi_message(self, wparam: int, lparam: int) -> int:
        suggested = Rect.from_win(ctypes.cast(lparam, ctypes.POINTER(wintypes.RECT)).contents)
        self._fire(self.on_dpi_changed, win32.loword(wparam), suggested)
        return 0

    def _on_dpi_after_parent(self, _wparam: int, _lparam: int) -> int:
        """A child window never gets WM_DPICHANGED: its parent (the taskbar) moved to another DPI, and this one with it,
        where it already is."""
        self._fire(self.on_dpi_changed, self.dpi, self.rect)
        return 0

    def _on_ncdestroy(self, _wparam: int, _lparam: int) -> None:
        self._hwnd = 0
        self._free_surface()
