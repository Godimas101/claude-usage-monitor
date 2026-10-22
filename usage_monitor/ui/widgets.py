"""The Pillow-drawn windows of the monitor: FloatingWidget, TaskbarWidget and Tooltip.

Each widget owns one LayeredWindow and draws it from a RenderContext: the view model, theme,
variant and skin the app resolved for this moment, plus the per-surface render options. The widgets
draw through ``context.skin`` at the DPI scale of the monitor they sit on, keep the frames they have
rendered (the blink phase is a cache lookup, not a render) and hand a frame to Windows only when it
changed. Everything here runs on the UI thread.

    FloatingWidget  draggable; the position lives in settings and is clamped to a monitor work area
                    (default: bottom-right corner above the tray); double click opens the dashboard
    TaskbarWidget   the strip beside the notification area: as tall as the taskbar, re-placed every
                    3 s, raised over the taskbar every 500 ms, hidden while an app is fullscreen;
                    click opens the dashboard
    Tooltip         one click-through layered window, shown after the pointer rests on a widget for
                    about 400 ms (the layered window reports the dwell) and hidden when it leaves

Platform queries, the window class and settings access arrive as constructor arguments, so the
placement and the wiring are tested with fakes and no desktop. The placement arithmetic is in plain
functions (floating_origin, strip_origin, tooltip_origin) for the same reason.
"""
from __future__ import annotations

import collections
import dataclasses
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from PIL import Image

from usage_monitor.log import get_logger
from usage_monitor.render.model import WidgetModel
from usage_monitor.render.skins import RenderOptions
from usage_monitor.ui import platform as default_platform
from usage_monitor.ui.layered import LayeredWindow
from usage_monitor.ui.platform import DEFAULT_DPI, Rect, TaskbarInfo, clamp_to_rects

log = get_logger(__name__)

KEEP_INTERVAL_MS = 500      # raise the strip over the taskbar and follow fullscreen apps
REPLACE_EVERY = 6           # keep ticks between taskbar re-placements: 6 x 500 ms = 3 s
WATCH_INTERVAL_MS = 200     # while a tooltip shows: has the pointer left, has the widget gone away
FLOAT_MARGIN = 12           # default floating position: px (at 100 %) in from the work-area corner
STRIP_GAP = 4               # px (at 100 %) between the strip and the notification area
TOOLTIP_GAP = 8             # px (at 100 %) between a widget and its tooltip

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


# ── Render context ────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class RenderContext:
    """What the widgets draw from. The app builds one per refresh and hands it to every surface.

    theme, variant and skin come from usage_monitor.themes and usage_monitor.render.skins. The
    options are the per-surface switches (transparent plate, text effects, reduced motion).
    """

    model: WidgetModel
    theme: Any
    variant: Any
    skin: Any
    floating: RenderOptions = RenderOptions()
    taskbar: RenderOptions = RenderOptions()
    hide_floating_in_fullscreen: bool = False

    def with_blink(self, blink_on: bool) -> RenderContext:
        """The same context with the alive mark lit or dark."""
        return dataclasses.replace(self, model=dataclasses.replace(self.model, blink_on=blink_on))


class FrameStats:
    """Counters the smoke test reads: frames rendered by a skin, frames handed to Windows."""

    def __init__(self) -> None:
        self.rendered: collections.Counter[str] = collections.Counter()
        self.presented: collections.Counter[str] = collections.Counter()
        self.failed = 0

    def snapshot(self) -> dict[str, Any]:
        return {"rendered": dict(self.rendered), "presented": dict(self.presented), "failed": self.failed}


def steady(model: WidgetModel) -> WidgetModel:
    """The model without the fields that never change what is drawn: the blink phase (a separate
    lookup in the frame cache) and the publish time (no skin draws it)."""
    return dataclasses.replace(model, blink_on=True, updated_at=_EPOCH)


class FrameCache:
    """The frames of one surface, kept while what they show stays the same.

    ``key`` describes everything but the blink phase. While it is unchanged, both blink phases are
    rendered at most once, so animating the alive mark costs a window update and no rendering.
    """

    def __init__(self) -> None:
        self._key: Any = None
        self._frames: dict[bool, Image.Image] = {}

    def get(self, key: Any, blink_on: bool, render: Callable[[], Image.Image]) -> Image.Image:
        if self._key is None or key != self._key:
            self._key, self._frames = key, {}
        frame = self._frames.get(blink_on)
        if frame is None:
            frame = self._frames[blink_on] = render()
        return frame


# ── Placement arithmetic (pure) ───────────────────────────────────────────────

def scale_for(dpi: int | None) -> float:
    """Render scale of a monitor: 1.0 at 96 DPI, 1.25 at 120, 2.0 at 192."""
    return (dpi if dpi and dpi > 0 else DEFAULT_DPI) / DEFAULT_DPI


def saved_point(saved: Mapping[str, Any] | None) -> tuple[int, int] | None:
    """The top-left stored in settings as {"x", "y"}, or None when there is none."""
    if not isinstance(saved, Mapping):
        return None
    x, y = saved.get("x"), saved.get("y")
    numbers = (int, float)
    if isinstance(x, numbers) and isinstance(y, numbers) and not isinstance(x, bool) and not isinstance(y, bool):
        if math.isfinite(x) and math.isfinite(y):  # a hand-edited settings file can hold NaN or Infinity
            return int(x), int(y)
    return None


def floating_origin(
    saved: tuple[int, int] | None, width: int, height: int, scale: float, work_areas: list[Rect]
) -> tuple[int, int]:
    """Where the floating widget's window goes: the saved top-left moved inside a monitor work
    area, or with nothing saved the bottom-right corner of the primary work area, a margin in. The
    work area excludes the taskbar, so the default sits just above the tray."""
    if not work_areas:
        return saved if saved is not None else (0, 0)
    if saved is None:
        margin = round(FLOAT_MARGIN * scale)
        saved = (work_areas[0].right - width - margin, work_areas[0].bottom - height - margin)
    return clamp_to_rects(saved[0], saved[1], width, height, work_areas)


def strip_supported(info: TaskbarInfo) -> bool:
    """The strip needs a horizontal taskbar that stays put and a known notification area. An
    auto-hiding taskbar is left alone: a window glued to a hidden bar would sit on the screen edge."""
    return info.is_horizontal and not info.autohide and info.tray_rect is not None


def strip_origin(info: TaskbarInfo, width: int, height: int) -> tuple[int, int] | None:
    """Top-left of the strip's window: just left of the notification area, vertically centred on the
    taskbar. None when strip_supported() is False."""
    if not strip_supported(info) or info.tray_rect is None:
        return None
    gap = round(STRIP_GAP * scale_for(info.dpi))
    x = max(info.rect.left, info.tray_rect.left - gap - width)
    y = info.rect.top + (info.rect.height - height) // 2
    return x, y


def tooltip_origin(anchor: Rect, width: int, height: int, bounds: Rect | None, gap: int) -> tuple[int, int]:
    """Top-left of a tooltip: centred on its widget, above it when there is room (below otherwise),
    and always inside ``bounds`` (a monitor work area) when one is known."""
    x = anchor.left + (anchor.width - width) // 2
    above = anchor.top - gap - height
    below = anchor.bottom + gap
    if bounds is None:
        return x, above
    if above >= bounds.top:
        y = above
    elif below + height <= bounds.bottom:
        y = below
    else:
        y = max(bounds.top, min(above, bounds.bottom - height))
    return max(bounds.left, min(x, bounds.right - width)), y


# ── Shared surface plumbing ───────────────────────────────────────────────────

class _Surface:
    """What FloatingWidget and TaskbarWidget share: the layered window, frame cache, presenting a
    frame only when it changed, visibility, and the tooltip hand-off."""

    surface_name = "surface"

    def __init__(
        self,
        host: Any,
        *,
        draggable: bool,
        tooltip: Tooltip | None,
        on_visibility: Callable[[], None] | None,
        platform: Any,
        window_factory: Callable[..., Any],
        stats: FrameStats | None,
    ) -> None:
        self._host = host
        self._plat = platform
        self._tooltip = tooltip
        self._on_visibility = on_visibility
        self._stats = stats if stats is not None else FrameStats()
        self._window: Any = window_factory(host, draggable=draggable)
        self._ctx: RenderContext | None = None
        self._cache = FrameCache()
        self._shown: tuple[Image.Image, tuple[int, int] | None] | None = None
        self._window.on_hover = self._hover
        self._window.on_hover_dwell = self.show_tooltip

    @property
    def window(self) -> Any:
        return self._window

    @property
    def visible(self) -> bool:
        return bool(self._window is not None and self._window.visible)

    def destroy(self) -> None:
        """Destroy the window. Safe to repeat."""
        self.hide_tooltip()
        window, self._window = self._window, None
        if window is not None:
            window.destroy()

    # Frames

    def _cached(self, key: tuple[Any, ...], ctx: RenderContext, draw: Callable[[], Image.Image]) -> Image.Image:
        def counted() -> Image.Image:
            self._stats.rendered[self.surface_name] += 1
            return draw()

        return self._cache.get(key, ctx.model.blink_on, counted)

    def _present(self, frame: Image.Image, origin: tuple[int, int] | None) -> bool:
        """Hand ``frame`` to Windows unless it is what the window already shows, where it belongs.
        With no origin the window keeps its position, which is what a redraw during a drag wants."""
        shown = self._shown
        if shown is not None and shown[0] is frame and shown[1] == origin and self._is_at(origin):
            return True
        x, y = origin if origin is not None else (None, None)
        if self._window.update(frame, x, y):
            self._shown = (frame, origin)
            self._stats.presented[self.surface_name] += 1
            return True
        self._stats.failed += 1
        return False

    def _is_at(self, origin: tuple[int, int] | None) -> bool:
        """Whether the window really sits at ``origin``. Something may have moved it since it was
        placed (Windows pulls windows off a monitor that went away), and a re-place must fix that
        even when the frame and the computed position are the same as before."""
        if origin is None:
            return True
        rect = self._window.rect
        return (rect.left, rect.top) == origin

    # Visibility

    def _set_visible(self, want: bool) -> None:
        window = self._window
        if window is None or bool(window.visible) == want:
            return
        if want and self._shown is None:
            return  # nothing has been drawn yet; an empty window must not flash up
        if want:
            window.show()
        else:
            window.hide()
            self.hide_tooltip()
        if self._on_visibility is not None:
            self._on_visibility()

    # Tooltip

    def _hover(self, inside: bool) -> None:
        if not inside:
            self.hide_tooltip()

    def show_tooltip(self) -> None:
        """Show the tooltip as the pointer's 400 ms rest does (the layered window calls this)."""
        window, ctx = self._window, self._ctx
        if self._tooltip is None or ctx is None or window is None or not window.visible or window.dragging:
            return
        self._tooltip.show(self, ctx, window.rect, window.dpi, self._pointer_over)

    def _pointer_over(self) -> bool:
        window = self._window
        if window is None or not window.visible or window.dragging:
            return False
        position = self._plat.cursor_pos()
        return position is not None and window.rect.contains(*position)

    def hide_tooltip(self) -> None:
        """Hide the tooltip if this widget is the one showing it."""
        if self._tooltip is not None:
            self._tooltip.hide(self)


# ── Floating widget ───────────────────────────────────────────────────────────

class FloatingWidget(_Surface):
    """The draggable always-on-top widget.

    ``get_position()`` returns the saved {"x", "y"} (or None), ``set_position(x, y)`` stores a new
    one after a drag. ``on_open()`` runs on a double click and ``on_menu(x, y)`` on a right click.
    """

    surface_name = "floating"

    def __init__(
        self,
        host: Any,
        *,
        get_position: Callable[[], Mapping[str, Any] | None],
        set_position: Callable[[int, int], None],
        on_open: Callable[[], None],
        on_menu: Callable[[int, int], None],
        tooltip: Tooltip | None = None,
        on_visibility: Callable[[], None] | None = None,
        platform: Any = default_platform,
        window_factory: Callable[..., Any] = LayeredWindow,
        stats: FrameStats | None = None,
    ) -> None:
        super().__init__(
            host, draggable=True, tooltip=tooltip, on_visibility=on_visibility,
            platform=platform, window_factory=window_factory, stats=stats,
        )
        self._get_position = get_position
        self._set_position = set_position
        self._on_open = on_open
        self._on_menu = on_menu
        self._placed = False
        self._timer: int | None = None
        window = self._window
        window.on_double_click = self._double_clicked
        window.on_right_click = self._right_clicked
        window.on_drag_end = self._dropped
        window.on_dpi_changed = self._dpi_changed

    # Drawing

    def render(self, ctx: RenderContext) -> None:
        """Draw ``ctx`` and place the window. Cheap when nothing visible changed."""
        self._ctx = ctx
        window = self._window
        if window is None:
            return
        saved = saved_point(self._get_position())
        scale = self._scale(saved)
        for _ in range(2):  # a second pass when the monitor's DPI is not the one first guessed
            frame = self._frame(ctx, scale)
            if window.dragging:
                self._present(frame, None)
                break
            origin = floating_origin(saved, *frame.size, scale, self._plat.work_areas())
            self._present(frame, origin)
            self._placed = True
            actual = scale_for(window.dpi)
            if abs(actual - scale) < 0.001:
                break
            scale = actual
        self._sync_timer(ctx)
        self._sync_visibility()

    def keep(self) -> None:
        """Timer tick while "hide in fullscreen" is on: follow the foreground app."""
        if self._ctx is not None and self._window is not None and not self._host.locked:
            self._sync_visibility()

    def destroy(self) -> None:
        if self._timer is not None:
            self._host.kill_timer(self._timer)
            self._timer = None
        super().destroy()

    def _frame(self, ctx: RenderContext, scale: float) -> Image.Image:
        key = (ctx.theme.id, ctx.variant, round(scale, 3), ctx.floating, steady(ctx.model))
        return self._cached(
            key, ctx,
            lambda: ctx.skin.render_floating(ctx.model, ctx.theme, ctx.variant, scale, ctx.floating),
        )

    def _scale(self, saved: tuple[int, int] | None) -> float:
        """DPI scale of the monitor the window is on, or before it was ever placed, of the one it
        is about to appear on."""
        if self._placed:
            return scale_for(self._window.dpi)
        if saved is None:
            areas = self._plat.work_areas()
            saved = (areas[0].right - 1, areas[0].bottom - 1) if areas else (0, 0)
        return scale_for(self._plat.dpi_for_point(*saved))

    def _sync_visibility(self) -> None:
        ctx = self._ctx
        hide = bool(ctx is not None and ctx.hide_floating_in_fullscreen and self._plat.is_fullscreen_foreground())
        self._set_visible(not hide)

    def _sync_timer(self, ctx: RenderContext) -> None:
        if ctx.hide_floating_in_fullscreen and self._timer is None:
            self._timer = self._host.set_timer(KEEP_INTERVAL_MS, self.keep)
        elif not ctx.hide_floating_in_fullscreen and self._timer is not None:
            self._host.kill_timer(self._timer)
            self._timer = None

    # Window events

    def _double_clicked(self) -> None:
        self.hide_tooltip()
        self._on_open()

    def _right_clicked(self, x: int, y: int) -> None:
        self.hide_tooltip()
        self._on_menu(x, y)

    def _dropped(self, x: int, y: int) -> None:
        """The user dropped the widget: keep it inside a work area and remember where it is."""
        self.hide_tooltip()
        rect = self._window.rect
        cx, cy = clamp_to_rects(x, y, rect.width, rect.height, self._plat.work_areas())
        if (cx, cy) != (x, y):
            self._window.move(cx, cy)
        self._set_position(cx, cy)

    def _dpi_changed(self, dpi: int, _suggested: Rect) -> None:
        """Windows moved the widget onto a monitor of another DPI: draw at that scale, in place."""
        ctx, window = self._ctx, self._window
        if ctx is None or window is None:
            return
        frame = self._frame(ctx, scale_for(dpi))
        self._present(frame, None)
        rect = window.rect
        cx, cy = clamp_to_rects(rect.left, rect.top, rect.width, rect.height, self._plat.work_areas())
        if (cx, cy) != (rect.left, rect.top):
            window.move(cx, cy)


# ── Taskbar strip ─────────────────────────────────────────────────────────────

class TaskbarWidget(_Surface):
    """The strip beside the notification area. ``on_open()`` runs on a click, ``on_menu(x, y)`` on a
    right click. A timer keeps it above the taskbar, in place, and out of the way of fullscreen apps."""

    surface_name = "taskbar"

    def __init__(
        self,
        host: Any,
        *,
        on_open: Callable[[], None],
        on_menu: Callable[[int, int], None],
        tooltip: Tooltip | None = None,
        on_visibility: Callable[[], None] | None = None,
        platform: Any = default_platform,
        window_factory: Callable[..., Any] = LayeredWindow,
        stats: FrameStats | None = None,
    ) -> None:
        super().__init__(
            host, draggable=False, tooltip=tooltip, on_visibility=on_visibility,
            platform=platform, window_factory=window_factory, stats=stats,
        )
        self._on_open = on_open
        self._on_menu = on_menu
        self._ticks = 0
        self._fullscreen = False
        self._warned = False
        window = self._window
        window.on_click = self._clicked
        window.on_right_click = self._right_clicked
        window.on_dpi_changed = self._dpi_changed
        self._timer: int | None = host.set_timer(KEEP_INTERVAL_MS, self.keep)

    # Drawing

    def render(self, ctx: RenderContext) -> None:
        """Draw ``ctx`` and place the strip beside the notification area."""
        self._ctx = ctx
        window = self._window
        if window is None:
            return
        info = self._plat.taskbar_info()
        if info is None or not strip_supported(info):
            self._explain_hidden(info)
            self._set_visible(False)
            return
        self._warned = False
        scale = scale_for(info.dpi)
        frame = self._frame(ctx, scale, info.rect.height)
        self._present(frame, strip_origin(info, *frame.size))
        self._fullscreen = bool(self._plat.is_fullscreen_foreground())
        self._set_visible(not self._fullscreen)

    def keep(self) -> None:
        """Timer tick (500 ms): follow fullscreen apps, re-place every 3 s, stay above the taskbar."""
        if self._ctx is None or self._window is None or self._host.locked:
            return
        self._ticks += 1
        if self._plat.is_fullscreen_foreground():
            self._fullscreen = True
            self._set_visible(False)
        elif self._fullscreen or self._ticks % REPLACE_EVERY == 0:
            self.render(self._ctx)
        elif self._window.visible:
            self._window.bring_to_top()

    def destroy(self) -> None:
        if self._timer is not None:
            self._host.kill_timer(self._timer)
            self._timer = None
        super().destroy()

    def _frame(self, ctx: RenderContext, scale: float, height_px: int) -> Image.Image:
        key = (ctx.theme.id, ctx.variant, round(scale, 3), height_px, ctx.taskbar, steady(ctx.model))
        return self._cached(
            key, ctx,
            lambda: ctx.skin.render_taskbar(ctx.model, ctx.theme, ctx.variant, height_px, scale, ctx.taskbar),
        )

    def _explain_hidden(self, info: TaskbarInfo | None) -> None:
        if self._warned:
            return
        self._warned = True
        if info is None:
            log.info("taskbar strip waits: the shell has no taskbar window yet")
        elif info.autohide:
            log.info("taskbar strip hidden: the taskbar is set to auto-hide")
        else:
            log.info("taskbar strip hidden: unsupported taskbar (edge %s, tray area %s)",
                     info.edge, "known" if info.tray_rect is not None else "unknown")

    # Window events

    def _clicked(self) -> None:
        self.hide_tooltip()
        self._on_open()

    def _right_clicked(self, x: int, y: int) -> None:
        self.hide_tooltip()
        self._on_menu(x, y)

    def _dpi_changed(self, _dpi: int, _suggested: Rect) -> None:
        if self._ctx is not None:
            self.render(self._ctx)


# ── Tooltip ───────────────────────────────────────────────────────────────────

class Tooltip:
    """The themed hover tooltip: exact percentages, reset times and pace.

    One layered window is shared by every widget. ``show`` takes the widget that asked (so only
    that widget can hide it), the context to draw, the widget's screen rectangle and DPI, and a
    predicate the tooltip polls while it is up (the pointer is still over the widget). The window
    is click-through, so it never takes the mouse from the widget under it.
    """

    def __init__(
        self,
        host: Any,
        *,
        platform: Any = default_platform,
        window_factory: Callable[..., Any] = LayeredWindow,
        stats: FrameStats | None = None,
    ) -> None:
        self._host = host
        self._plat = platform
        self._factory = window_factory
        self._stats = stats if stats is not None else FrameStats()
        self._window: Any = None
        self._cache = FrameCache()
        self._owner: object | None = None
        self._ctx: RenderContext | None = None
        self._anchor: Rect | None = None
        self._dpi = DEFAULT_DPI
        self._still_over: Callable[[], bool] | None = None
        self._timer: int | None = None
        self._visible = False
        self._shown: tuple[Image.Image, tuple[int, int]] | None = None

    @property
    def visible(self) -> bool:
        return self._visible

    @property
    def owner(self) -> object | None:
        return self._owner if self._visible else None

    def show(
        self, owner: object, ctx: RenderContext, anchor: Rect, dpi: int, still_over: Callable[[], bool]
    ) -> None:
        """Show the tooltip for ``owner`` beside ``anchor`` (the widget's rectangle on screen)."""
        self._owner, self._ctx, self._anchor, self._dpi, self._still_over = owner, ctx, anchor, dpi, still_over
        self._draw()
        if self._timer is None:
            self._timer = self._host.set_timer(WATCH_INTERVAL_MS, self._watch)

    def update(self, ctx: RenderContext) -> None:
        """New data while the tooltip is up: draw it again (cached frames cost nothing)."""
        if self._visible:
            self._ctx = ctx
            self._draw()

    def hide(self, owner: object | None = None) -> None:
        """Hide the tooltip. With an ``owner`` only that widget's tooltip is hidden."""
        if owner is not None and owner is not self._owner:
            return
        if self._timer is not None:
            self._host.kill_timer(self._timer)
            self._timer = None
        if self._visible and self._window is not None:
            self._window.hide()
        self._visible = False
        self._owner = None
        self._shown = None

    def destroy(self) -> None:
        self.hide()
        window, self._window = self._window, None
        if window is not None:
            window.destroy()

    def _draw(self) -> None:
        ctx, anchor = self._ctx, self._anchor
        if ctx is None or anchor is None:
            return
        scale = scale_for(self._dpi)
        key = (ctx.theme.id, ctx.variant, round(scale, 3), steady(ctx.model))

        def draw() -> Image.Image:
            self._stats.rendered["tooltip"] += 1
            return ctx.skin.render_tooltip(ctx.model, ctx.theme, ctx.variant, scale)

        frame = self._cache.get(key, ctx.model.blink_on, draw)
        window = self._ensure_window()
        bounds = self._plat.work_area_at(*anchor.center)
        origin = tooltip_origin(anchor, *frame.size, bounds, round(TOOLTIP_GAP * scale))
        shown = self._shown
        if self._visible and shown is not None and shown[0] is frame and shown[1] == origin:
            return  # the same picture in the same place: nothing to hand to Windows
        if window.update(frame, *origin):
            self._shown = (frame, origin)
            self._stats.presented["tooltip"] += 1
            if not self._visible:
                window.show()
                window.bring_to_top()
            self._visible = True
        else:
            self._stats.failed += 1

    def _ensure_window(self) -> Any:
        if self._window is None:
            self._window = self._factory(self._host, draggable=False)
            self._window.set_click_through(True)
        return self._window

    def _watch(self) -> None:
        """Safety net for a missed WM_MOUSELEAVE or a drag: hide once the pointer is gone."""
        still_over = self._still_over
        if self._visible and (still_over is None or not still_over()):
            self.hide()


__all__ = [
    "FloatingWidget",
    "FrameCache",
    "FrameStats",
    "RenderContext",
    "TaskbarWidget",
    "Tooltip",
    "floating_origin",
    "saved_point",
    "scale_for",
    "steady",
    "strip_origin",
    "strip_supported",
    "tooltip_origin",
]
