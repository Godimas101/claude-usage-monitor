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

Content insets. A frame may carry a transparent margin round its visible surface (Gates draws a soft
shadow there). A skin says how much through the optional ``content_insets(surface, theme, model,
scale)``, and the placement arithmetic uses the visible card, not the padded frame: the floating
widget's default corner and its screen-edge clamp, and the tooltip's gap to the widget it belongs to
and to the screen edge, measure from what the user sees, so the gap is the same in every theme.

Pointer response. When the pointer enters the floating widget or the strip, a skin that offers
``hover_frames(frame, surface, scale, opts)`` gets to answer with a few frames (model.HoverPlay), which
are derived once from the normal frame, kept (the next hover is a lookup) and played with one-shot
timers: Nostromo brightens, Night City glitches for three 60 ms frames, Gates lifts on a small spring.
The swap of a frame is a lookup and a window update, with the frames already in the premultiplied
format Windows takes, so it costs about what any other redraw does. Nothing runs while the pointer is
away: no timer, no redraw. A response does not start while reduce_motion is on, while Windows' own
"show animations" switch is off, or while the widget is hidden or being dragged, and one under way ends
at once when reduce_motion goes on or the widget is hidden. Windows lets the pointer through pixels with
no coverage at all, so every response frame covers (by one 255th, invisibly) whatever the normal frame
covered: a card that lifts away from under a resting pointer does not end the hover it is answering.
"""
from __future__ import annotations

import collections
import ctypes
import dataclasses
import math
from collections.abc import Callable, Mapping
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from PIL import Image, ImageChops

from usage_monitor.log import get_logger
from usage_monitor.render.model import (
    NO_INSETS,
    SURFACE_FLOATING,
    SURFACE_TASKBAR,
    SURFACE_TOOLTIP,
    HoverPlay,
    Insets,
    WidgetModel,
)
from usage_monitor.render.skins import RenderOptions
from usage_monitor.ui import platform as default_platform
from usage_monitor.ui import win32
from usage_monitor.ui.layered import LayeredWindow
from usage_monitor.ui.platform import DEFAULT_DPI, Rect, TaskbarInfo, clamp_to_rects

log = get_logger(__name__)

KEEP_INTERVAL_MS = 500      # raise the strip over the taskbar and follow fullscreen apps
REPLACE_EVERY = 6           # keep ticks between taskbar re-placements: 6 x 500 ms = 3 s
WATCH_INTERVAL_MS = 200     # while a tooltip shows: has the pointer left, has the widget gone away
FLOAT_MARGIN = 12           # default floating position: px (at 100 %) in from the work-area corner
STRIP_GAP = 4               # px (at 100 %) between the strip and the notification area
TOOLTIP_GAP = 8             # px (at 100 %) between a widget and its tooltip
HOVER_PLANS_KEPT = 2        # hover responses kept per surface: one for each blink phase of the frame on screen
SPI_GETCLIENTAREAANIMATION = 0x1042     # Windows' "Show animations in Windows" switch

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


def inset_rect(rect: Rect, insets: Insets) -> Rect:
    """The visible part of a window: ``rect`` less the transparent margin the skin drew round the card."""
    left, top, right, bottom = insets
    return Rect(rect.left + left, rect.top + top, rect.right - right, rect.bottom - bottom)


def skin_insets(skin: Any, surface: str, theme: Any, model: WidgetModel, scale: float,
                size: tuple[int, int]) -> Insets:
    """The transparent margin (left, top, right, bottom) of a frame of ``size`` the skin drew for
    ``surface``, in pixels. A skin without ``content_insets`` has none, and so does one whose answer
    is unusable (negative, not numbers, or as large as the frame): a widget must still be placeable."""
    query = getattr(skin, "content_insets", None)
    if not callable(query):
        return NO_INSETS
    try:
        left, top, right, bottom = (max(0, int(v)) for v in query(surface, theme, model, scale))
    except Exception:
        log.debug("content_insets failed for the %s", surface, exc_info=True)
        return NO_INSETS
    width, height = size
    if left + right >= width or top + bottom >= height:
        return NO_INSETS
    return left, top, right, bottom


def floating_origin(
    saved: tuple[int, int] | None, width: int, height: int, scale: float, work_areas: list[Rect],
    insets: Insets = NO_INSETS,
) -> tuple[int, int]:
    """Where the floating widget's window goes: the saved top-left moved inside a monitor work
    area, or with nothing saved the bottom-right corner of the primary work area, a margin in. The
    work area excludes the taskbar, so the default sits just above the tray.

    ``insets`` is the transparent margin of the frame. The margin in and the screen edge are measured
    from the visible card, so a card with a shadow margin sits as close to the edge as one without.
    The result is still the position of the window, which is what is saved."""
    if not work_areas:
        return saved if saved is not None else (0, 0)
    left, top, right, bottom = insets
    card_w, card_h = width - left - right, height - top - bottom
    if saved is None:
        margin = round(FLOAT_MARGIN * scale)
        card = (work_areas[0].right - card_w - margin, work_areas[0].bottom - card_h - margin)
    else:
        card = (saved[0] + left, saved[1] + top)
    x, y = clamp_to_rects(card[0], card[1], card_w, card_h, work_areas)
    return x - left, y - top


def strip_supported(info: TaskbarInfo) -> bool:
    """The strip needs a horizontal taskbar that stays put and a known notification area. An
    auto-hiding taskbar is left alone: a window glued to a hidden bar would sit on the screen edge."""
    return info.is_horizontal and not info.autohide and info.tray_rect is not None


def strip_origin(info: TaskbarInfo, width: int, height: int, insets: Insets = NO_INSETS) -> tuple[int, int] | None:
    """Top-left of the strip's window: just left of the notification area, vertically centred on the
    taskbar. None when strip_supported() is False. ``insets`` (the frame's transparent margin) is
    left out of the centring and the gap, so the visible strip is what sits beside the tray."""
    if not strip_supported(info) or info.tray_rect is None:
        return None
    left, top, right, bottom = insets
    card_w, card_h = width - left - right, height - top - bottom
    gap = round(STRIP_GAP * scale_for(info.dpi))
    x = max(info.rect.left, info.tray_rect.left - gap - card_w)
    y = info.rect.top + (info.rect.height - card_h) // 2
    return x - left, y - top


def tooltip_origin(
    anchor: Rect, width: int, height: int, bounds: Rect | None, gap: int, insets: Insets = NO_INSETS
) -> tuple[int, int]:
    """Top-left of a tooltip: centred on its widget, above it when there is room (below otherwise),
    and always inside ``bounds`` (a monitor work area) when one is known.

    ``anchor`` is what the user sees of the widget (its window less the widget's own margin) and
    ``insets`` the transparent margin of the tooltip's frame: ``gap`` is then the distance between
    the two visible cards, whatever shadow either frame carries."""
    left, top, right, bottom = insets
    card_w, card_h = width - left - right, height - top - bottom
    x = anchor.left + (anchor.width - card_w) // 2
    above = anchor.top - gap - card_h
    below = anchor.bottom + gap
    if bounds is None:
        return x - left, above - top
    if above >= bounds.top:
        y = above
    elif below + card_h <= bounds.bottom:
        y = below
    else:
        y = max(bounds.top, min(above, bounds.bottom - card_h))
    return max(bounds.left, min(x, bounds.right - card_w)) - left, y - top


# ── Pointer response ──────────────────────────────────────────────────────────

_IDLE, _PLAY, _HOLD, _LEAVE = "idle", "play", "hold", "leave"
_COVERED = [0] + [1] * 255      # look-up table: any coverage at all becomes 1/255


def os_animations_enabled() -> bool:
    """Windows' "Show animations in Windows" switch (Settings, Accessibility, Visual effects): the
    system's own "reduce motion". True when it cannot be read."""
    try:
        flag = wintypes.BOOL(1)
        if not win32.SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(flag), 0):
            return True
        return bool(flag.value)
    except Exception:
        log.debug("reading the animations switch failed", exc_info=True)
        return True


def _always() -> bool:
    return True


def _animation_switch(platform: Any, explicit: Callable[[], bool] | None) -> Callable[[], bool]:
    """Where a widget asks whether the system allows animation: the caller's own switch, else the
    platform's ``animations_enabled``, else Windows' setting when the platform is the real one, else yes
    (a fake platform has no opinion)."""
    if explicit is not None:
        return explicit
    query = getattr(platform, "animations_enabled", None)
    if callable(query):
        return query
    return os_animations_enabled if platform is default_platform else _always


@dataclass(frozen=True, slots=True)
class _Plan:
    """A skin's HoverPlay with its frames made ready for Windows (premultiplied, so a swap only copies)."""

    frames: tuple[Image.Image, ...]
    delays: tuple[int, ...]
    hold: bool
    leave: tuple[Image.Image, ...]
    leave_ms: int


def _ready(frame: Image.Image, covered: Image.Image, size: tuple[int, int]) -> Image.Image:
    """``frame`` in the format Windows takes. Wherever the normal frame covered a pixel, this one
    covers at least 1/255 of it: Windows lets the pointer through pixels with no coverage at all, so a
    frame that moved its card away from under a resting pointer would end the hover (and begin the
    next one a moment later). One 255th is invisible and keeps the pointer where it is."""
    if frame.size != size:
        raise ValueError(f"a hover frame is {frame.size}, the frame it answers is {size}")
    picture = frame.convert("RGBA")
    picture.putalpha(ImageChops.lighter(picture.getchannel("A"), covered))
    return picture.convert("RGBa")


def _prepare(play: HoverPlay, base: Image.Image) -> _Plan:
    covered = (base if base.mode in ("RGBA", "RGBa") else base.convert("RGBA")).getchannel(3).point(_COVERED)
    return _Plan(
        frames=tuple(_ready(f, covered, base.size) for f in play.frames),
        delays=tuple(max(0, int(ms)) for ms in play.delays_ms),
        hold=bool(play.hold),
        leave=tuple(_ready(f, covered, base.size) for f in play.leave),
        leave_ms=max(0, int(play.leave_ms)),
    )


class _Hover:
    """The pointer response of one widget (see the module docstring).

    The surface tells it which normal frame is on screen (frame_for) and when the pointer comes and
    goes (enter, leave). It asks the skin for the response once per normal frame, keeps it, and plays
    it with one-shot timers; each swap is one window update with a frame that is already in Windows'
    format. Phases:

        idle    the normal frame shows; no timer, nothing to do
        play    stepping through the response; one timer pending
        hold    the last frame of the response stays while the pointer is over the widget; no timer
        leave   the way out of a held response; one timer pending
    """

    def __init__(self, surface: _Surface) -> None:
        self._surface = surface
        self._phase = _IDLE
        self._step = 0
        self._timer: int | None = None
        self._base: Image.Image | None = None
        self._scale = 1.0
        self._key: Any = None
        self._plans: dict[int, tuple[Image.Image, _Plan | None]] = {}
        self._warned = False

    @property
    def phase(self) -> str:
        return self._phase

    # What the surface tells it

    def frame_for(self, base: Image.Image, scale: float, key: Any) -> Image.Image:
        """The picture the window should show for the normal frame ``base``: that frame, or while the
        response runs or holds, the response's frame derived from it."""
        if key != self._key:
            self._key, self._plans = key, {}
        self._base, self._scale = base, scale
        if self._phase is _IDLE:
            return base
        plan = self._plan(base) if self._may_continue() else None
        if plan is None or (self._phase is _HOLD and not plan.hold) or (self._phase is _LEAVE and not plan.leave):
            self.stop(restore=False)    # nothing to continue with: another skin may have answered (a theme switch)
            return base
        return self._picture(plan)

    def enter(self) -> None:
        """The pointer came onto the widget: begin the response."""
        window, base = self._surface._window, self._base
        if window is None or base is None or self._phase in (_PLAY, _HOLD):
            return
        if window.dragging or not window.visible or not self._motion_ok():
            return
        plan = self._plan(base)
        if plan is None:
            return
        self._cancel()
        self._phase, self._step = _PLAY, 0
        self._show(plan)

    def leave(self) -> None:
        """The pointer left: a held response plays its way out, any other ends at once."""
        if self._phase is _IDLE:
            return
        plan = self._plan_on_screen()
        self._cancel()
        if plan is not None and plan.hold and plan.leave and self._phase in (_PLAY, _HOLD) and self._may_continue():
            self._phase, self._step = _LEAVE, 0
            self._show(plan)
        else:
            self.stop()

    def stop(self, *, restore: bool = True) -> None:
        """End the response now. With ``restore`` the normal frame goes back on the window."""
        self._cancel()
        was, self._phase, self._step = self._phase, _IDLE, 0
        if restore and was is not _IDLE:
            self._restore()

    def destroy(self) -> None:
        self._cancel()
        self._phase, self._base, self._plans = _IDLE, None, {}

    # Playing

    def _show(self, plan: _Plan) -> None:
        self._surface._present(self._picture(plan), None)
        if self._phase is _PLAY:
            if self._step + 1 >= len(plan.frames) and plan.hold:
                self._phase = _HOLD
            else:
                self._arm(plan.delays[self._step])
        elif self._phase is _LEAVE:
            self._arm(plan.leave_ms)

    def _tick(self) -> None:
        """A one-shot timer fired: the next frame of the response, or the end of it."""
        self._timer = None
        try:
            plan = self._plan_on_screen()
            if self._surface._window is None or plan is None or self._phase not in (_PLAY, _LEAVE) or not self._may_continue():
                self.stop()
                return
            frames = plan.frames if self._phase is _PLAY else plan.leave
            if self._step + 1 >= len(frames):
                self.stop()  # the one-shot is over (or the way out): the normal frame returns
                return
            self._step += 1
            self._show(plan)
        except Exception:
            # Never leave a response half played with no timer to finish it: the next redraw shows the normal frame.
            log.warning("the pointer response failed on the %s; back to the normal frame", self._surface.surface_name,
                        exc_info=True)
            self._cancel()
            self._phase, self._step = _IDLE, 0

    def _arm(self, ms: int) -> None:
        self._timer = self._surface._host.set_timer(max(1, ms), self._tick, repeat=False)

    def _cancel(self) -> None:
        timer, self._timer = self._timer, None
        if timer is not None:
            self._surface._host.kill_timer(timer)

    def _picture(self, plan: _Plan) -> Image.Image:
        if self._phase is _LEAVE:
            return plan.leave[min(self._step, len(plan.leave) - 1)]
        if self._phase is _HOLD:
            return plan.frames[-1]
        return plan.frames[min(self._step, len(plan.frames) - 1)]

    def _restore(self) -> None:
        surface, base = self._surface, self._base
        if base is None or surface._window is None:
            return
        shown = surface._shown
        if shown is None or shown[0] is not base:
            surface._present(base, None)

    # What may run

    def _motion_ok(self) -> bool:
        surface = self._surface
        ctx = surface._ctx
        return ctx is not None and not surface._options(ctx).reduce_motion and surface._animations_on()

    def _may_continue(self) -> bool:
        window = self._surface._window
        return window is not None and bool(window.visible) and self._motion_ok()

    # The frames

    def _plan(self, base: Image.Image) -> _Plan | None:
        entry = self._plans.get(id(base))
        if entry is not None and entry[0] is base:
            return entry[1]
        plan = self._derive(base)
        if len(self._plans) >= HOVER_PLANS_KEPT:
            self._plans.pop(next(iter(self._plans)))
        self._plans[id(base)] = (base, plan)  # a failure is kept too: it is not tried again for this frame
        return plan

    def _plan_on_screen(self) -> _Plan | None:
        entry = self._plans.get(id(self._base))
        return entry[1] if entry is not None and entry[0] is self._base else None

    def _derive(self, base: Image.Image) -> _Plan | None:
        surface = self._surface
        ctx = surface._ctx
        make = getattr(ctx.skin, "hover_frames", None) if ctx is not None else None
        if not callable(make):
            return None
        try:
            play = make(base, surface.surface_name, self._scale, surface._options(ctx))
            return _prepare(play, base) if play is not None else None
        except Exception:
            if not self._warned:
                self._warned = True
                log.warning("the %s skin's hover frames failed; no response on the %s",
                            getattr(ctx.theme, "id", "?"), surface.surface_name, exc_info=True)
            return None


# ── Shared surface plumbing ───────────────────────────────────────────────────

class _Surface:
    """What FloatingWidget and TaskbarWidget share: the layered window, frame cache, presenting a
    frame only when it changed, visibility, the pointer response and the tooltip hand-off."""

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
        animations_enabled: Callable[[], bool] | None = None,
    ) -> None:
        self._host = host
        self._plat = platform
        self._tooltip = tooltip
        self._on_visibility = on_visibility
        self._stats = stats if stats is not None else FrameStats()
        self._animations_on = _animation_switch(platform, animations_enabled)
        self._window_factory = window_factory
        self._window: Any = self._new_window(draggable)
        self._ctx: RenderContext | None = None
        self._cache = FrameCache()
        self._frame_key: Any = None
        self._insets: Insets = NO_INSETS
        self._response = _Hover(self)
        self._shown: tuple[Image.Image, tuple[int, int] | None] | None = None
        self._wire(self._window)

    @property
    def window(self) -> Any:
        return self._window

    @property
    def visible(self) -> bool:
        return bool(self._window is not None and self._window.visible)

    @property
    def insets(self) -> Insets:
        """The transparent margin of the frame on screen: the card the user sees is the window less this."""
        return self._insets

    @property
    def hover_phase(self) -> str:
        """Where the pointer response is: "idle", "play", "hold" or "leave"."""
        return self._response.phase

    def destroy(self) -> None:
        """Destroy the window. Safe to repeat."""
        self.hide_tooltip()
        self._response.destroy()
        window, self._window = self._window, None
        if window is not None:
            window.destroy()

    def _options(self, ctx: RenderContext) -> RenderOptions:
        """The per-surface render options of ``ctx`` for this widget."""
        raise NotImplementedError

    def _new_window(self, draggable: bool) -> Any:
        return self._window_factory(self._host, draggable=draggable)

    def _wire(self, window: Any) -> None:
        """Connect the window's callbacks to this widget."""
        window.on_hover = self._hover
        window.on_hover_dwell = self.show_tooltip

    # Frames

    def _cached(self, key: tuple[Any, ...], ctx: RenderContext, draw: Callable[[], Image.Image]) -> Image.Image:
        def counted() -> Image.Image:
            self._stats.rendered[self.surface_name] += 1
            return draw()

        self._frame_key = key
        return self._cache.get(key, ctx.model.blink_on, counted)

    def _display(self, frame: Image.Image, scale: float) -> Image.Image:
        """What the window shows for the normal ``frame``: the frame itself, or while the pointer
        response is under way the response's frame made from it."""
        return self._response.frame_for(frame, scale, self._frame_key)

    def _measure(self, ctx: RenderContext, surface: str, frame: Image.Image, scale: float) -> Insets:
        """Ask the skin for the frame's transparent margin and remember it."""
        self._insets = skin_insets(ctx.skin, surface, ctx.theme, ctx.model, scale, frame.size)
        return self._insets

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
            self._response.stop()  # the normal frame goes back first: a hidden window keeps its last picture
            window.hide()
            self.hide_tooltip()
        if self._on_visibility is not None:
            self._on_visibility()

    # Pointer: response and tooltip

    def _hover(self, inside: bool) -> None:
        """The pointer came onto the window or left it (the layered window reports both)."""
        if inside:
            self._response.enter()
        else:
            self.hide_tooltip()
            self._response.leave()

    def show_tooltip(self) -> None:
        """Show the tooltip as the pointer's 400 ms rest does (the layered window calls this)."""
        window, ctx = self._window, self._ctx
        if self._tooltip is None or ctx is None or window is None or not window.visible or window.dragging:
            return
        self._tooltip.show(self, ctx, inset_rect(window.rect, self._insets), window.dpi, self._pointer_over)

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
        animations_enabled: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__(
            host, draggable=True, tooltip=tooltip, on_visibility=on_visibility,
            platform=platform, window_factory=window_factory, stats=stats,
            animations_enabled=animations_enabled,
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
            insets = self._measure(ctx, SURFACE_FLOATING, frame, scale)
            picture = self._display(frame, scale)
            if window.dragging:
                self._present(picture, None)
                break
            origin = floating_origin(saved, *frame.size, scale, self._plat.work_areas(), insets)
            self._present(picture, origin)
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

    def _options(self, ctx: RenderContext) -> RenderOptions:
        return ctx.floating

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
        """The user dropped the widget: keep its card inside a work area and remember where the window is."""
        self.hide_tooltip()
        cx, cy = self._clamped(x, y, self._window.rect)
        if (cx, cy) != (x, y):
            self._window.move(cx, cy)
        self._set_position(cx, cy)

    def _dpi_changed(self, dpi: int, _suggested: Rect) -> None:
        """Windows moved the widget onto a monitor of another DPI: draw at that scale, in place."""
        ctx, window = self._ctx, self._window
        if ctx is None or window is None:
            return
        scale = scale_for(dpi)
        frame = self._frame(ctx, scale)
        self._measure(ctx, SURFACE_FLOATING, frame, scale)
        self._present(self._display(frame, scale), None)
        rect = window.rect
        cx, cy = self._clamped(rect.left, rect.top, rect)
        if (cx, cy) != (rect.left, rect.top):
            window.move(cx, cy)

    def _clamped(self, x: int, y: int, rect: Rect) -> tuple[int, int]:
        """The window position nearest (x, y) that keeps the visible card (the window less its
        transparent margin) inside a work area."""
        left, top, right, bottom = self._insets
        cx, cy = clamp_to_rects(
            x + left, y + top, rect.width - left - right, rect.height - top - bottom, self._plat.work_areas()
        )
        return cx - left, cy - top


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
        animations_enabled: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__(
            host, draggable=False, tooltip=tooltip, on_visibility=on_visibility,
            platform=platform, window_factory=window_factory, stats=stats,
            animations_enabled=animations_enabled,
        )
        self._on_open = on_open
        self._on_menu = on_menu
        self._ticks = 0
        self._fullscreen = False
        self._warned = False
        self._timer: int | None = host.set_timer(KEEP_INTERVAL_MS, self.keep)

    # The window: a child of the taskbar (#29)

    def _new_window(self, draggable: bool) -> Any:
        info = self._plat.taskbar_info()
        return self._window_factory(self._host, draggable=draggable, parent=info.hwnd if info else None)

    def _wire(self, window: Any) -> None:
        super()._wire(window)
        window.on_click = self._clicked
        window.on_right_click = self._right_clicked
        window.on_dpi_changed = self._dpi_changed

    def _follow_taskbar(self, info: TaskbarInfo) -> None:
        """Keep the strip a child of the taskbar it sits on.

        While Start, Search or a tray flyout is open, Windows 11 lifts the taskbar above every
        always-on-top window until the shell lets it down again, which buried a top-level strip
        behind it. A layered child of the taskbar is composed with it and stays in sight. A new
        taskbar (Explorer restarted), or a strip window that died with the old one, gets a new window.
        """
        window = self._window
        if window is not None and window.parent == info.hwnd and window.alive:
            return
        self._response.stop()
        self.hide_tooltip()
        self._window = self._window_factory(self._host, draggable=False, parent=info.hwnd)
        self._wire(self._window)
        self._shown = None
        if window is not None:
            window.destroy()
        if self._on_visibility is not None:
            self._on_visibility()

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
        self._follow_taskbar(info)
        scale = scale_for(info.dpi)
        frame = self._frame(ctx, scale, info.rect.height)
        insets = self._measure(ctx, SURFACE_TASKBAR, frame, scale)
        self._present(self._display(frame, scale), strip_origin(info, *frame.size, insets))
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

    def _options(self, ctx: RenderContext) -> RenderOptions:
        return ctx.taskbar

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
    that widget can hide it), the context to draw, the screen rectangle of what the user sees of
    the widget (its window less its transparent margin) and its DPI, and a predicate the tooltip
    polls while it is up (the pointer is still over the widget). The window is click-through, so it
    never takes the mouse from the widget under it.
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
        """Show the tooltip for ``owner`` beside ``anchor`` (the visible card of the widget, on screen)."""
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
        insets = skin_insets(ctx.skin, SURFACE_TOOLTIP, ctx.theme, ctx.model, scale, frame.size)
        origin = tooltip_origin(anchor, *frame.size, bounds, round(TOOLTIP_GAP * scale), insets)
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
    "inset_rect",
    "os_animations_enabled",
    "saved_point",
    "scale_for",
    "skin_insets",
    "steady",
    "strip_origin",
    "strip_supported",
    "tooltip_origin",
]
