"""Tests for the frame margin and the pointer response: skins (content_insets, hover_frames) and the
widgets that use them (ui/widgets.py).

Content insets. A frame may carry a transparent margin round the card the user sees (Gates draws its
soft shadow there). The skin says how much, and the widgets place the visible card, not the padded
frame: the floating widget's default corner and screen-edge clamp, and the tooltip's gap to the widget
it belongs to, are measured from pixels the user sees. The checks below measure the real frames.

Pointer response. When the pointer enters the floating widget or the strip, the skin answers with a few
frames (Nostromo brightens, Night City glitches for 180 ms, Gates lifts on a spring) that the widget
plays with one-shot timers. Nothing runs while the pointer is away, nothing plays under reduce_motion,
the animations switch of Windows, a hidden window or a drag, and a swap is one window update of a frame
already in Windows' format.

Everything runs on fakes (host with hand-fired timers, windows that remember what they were given, a
platform with a screen) while the real skins draw. The test that times a swap on a real layered window
is skipped unless CUM_UI_TESTS=1.
"""
from __future__ import annotations

import collections
import ctypes
import dataclasses
import itertools
import os
import statistics
import sys
import time
import uuid
from typing import Any

import pytest
from PIL import Image, ImageChops

if sys.platform != "win32":
    pytest.skip("the monitor's UI layer is Windows only", allow_module_level=True)

from usage_monitor import themes
from usage_monitor.render import dump
from usage_monitor.render.model import (
    NO_INSETS,
    SURFACE_FLOATING,
    SURFACE_TASKBAR,
    SURFACE_TOOLTIP,
    HoverPlay,
)
from usage_monitor.render.skins import RenderOptions, get_skin
from usage_monitor.ui import platform as plat
from usage_monitor.ui import widgets
from usage_monitor.ui.layered import premultiplied_bgra
from usage_monitor.ui.platform import Rect, TaskbarInfo
from usage_monitor.ui.widgets import (
    FloatingWidget,
    FrameStats,
    RenderContext,
    TaskbarWidget,
    Tooltip,
    floating_origin,
    inset_rect,
    skin_insets,
    strip_origin,
    tooltip_origin,
)

UI = pytest.mark.skipif(
    os.environ.get("CUM_UI_TESTS") != "1", reason="needs an interactive desktop: set CUM_UI_TESTS=1",
)
THEME_IDS = ("nostromo", "night_city", "gates")
SCALES = (1.0, 1.5, 2.0)
SURFACES = (SURFACE_FLOATING, SURFACE_TASKBAR)


# ── Helpers ───────────────────────────────────────────────────────────────────

def theme_of(theme_id: str) -> Any:
    return themes.get_theme(theme_id)


def model_of(state: str = "normal3", **changes: Any) -> Any:
    return dataclasses.replace(dump.sample_model(state), **changes)


def frame_of(theme_id: str, surface: str, scale: float, *, state: str = "normal3", **options: Any) -> Image.Image:
    """The real frame a skin draws for a surface (the strip on a taskbar 48 px tall at 100 %)."""
    theme, skin, model = theme_of(theme_id), get_skin(theme_id), model_of(state)
    opts = RenderOptions(**options)
    if surface == SURFACE_FLOATING:
        return skin.render_floating(model, theme, theme.default_variant, scale, opts)
    if surface == SURFACE_TASKBAR:
        return skin.render_taskbar(model, theme, theme.default_variant, round(48 * scale), scale, opts)
    return skin.render_tooltip(model, theme, theme.default_variant, scale)


def card_box(image: Image.Image, threshold: int = 128) -> tuple[int, int, int, int]:
    """Where the card is in a frame: the box of the pixels at least ``threshold`` opaque."""
    alpha = image.getchannel(3) if image.mode in ("RGBA", "RGBa") else image.convert("RGBA").getchannel(3)
    box = alpha.point(lambda v: 255 if v >= threshold else 0).getbbox()
    assert box is not None, "the frame has no card"
    return box


def on_screen(box: tuple[int, int, int, int], x: int, y: int) -> Rect:
    return Rect(x + box[0], y + box[1], x + box[2], y + box[3])


def same_picture(shown: Image.Image, expected: Image.Image) -> bool:
    """Whether ``shown`` (what the widget handed to Windows, premultiplied) is ``expected`` (a skin's frame)."""
    if shown.mode != "RGBa" or shown.size != expected.size:
        return False
    want = expected.convert("RGBa").convert("RGBA")
    diff = ImageChops.difference(shown.convert("RGBA"), want)
    return max(hi for _lo, hi in diff.getextrema()) <= 1


def alpha_of(image: Image.Image) -> Image.Image:
    return image.getchannel(3)


def differs(a: Image.Image, b: Image.Image) -> bool:
    """Whether two frames differ in any pixel, in colour or in coverage."""
    return any(band.getbbox() is not None for band in ImageChops.difference(a, b).split())


# ── Fakes ─────────────────────────────────────────────────────────────────────

class Host:
    """UIHost stand-in: timers fire when the test says so."""

    def __init__(self) -> None:
        self.locked = False
        self.timers: dict[int, tuple[int, Any, bool]] = {}
        self._ids = itertools.count(1)

    def on_ui_thread(self) -> bool:
        return True

    def set_timer(self, ms: int, fn: Any, *, repeat: bool = True) -> int:
        timer_id = next(self._ids)
        self.timers[timer_id] = (ms, fn, repeat)
        return timer_id

    def kill_timer(self, timer_id: int) -> None:
        self.timers.pop(timer_id, None)

    def post(self, fn: Any) -> None:
        fn()

    def add_cleanup(self, fn: Any) -> Any:
        return lambda: None

    def one_shots(self) -> list[tuple[int, int]]:
        """(id, ms) of the timers that fire once: the pointer response's, nothing else uses them here."""
        return [(timer_id, ms) for timer_id, (ms, _fn, repeat) in self.timers.items() if not repeat]

    def fire(self, timer_id: int) -> None:
        _ms, fn, repeat = self.timers[timer_id]
        if not repeat:
            del self.timers[timer_id]
        fn()


class Window:
    """A layered window that remembers every picture it was given, and reports the pointer like the real one."""

    def __init__(self, host: Any = None, *, draggable: bool, topmost: bool = True, dpi: int = 96,
                 parent: int | None = None) -> None:
        self.draggable = draggable
        self.parent = parent
        self.alive = True
        self.visible = False
        self.dragging = False
        self.dpi = dpi
        self.hovering = False
        self.rect = Rect(0, 0, 0, 0)
        self.updates: list[tuple[Image.Image, int, int]] = []
        self.moves: list[tuple[int, int]] = []
        self.destroyed = False
        self.on_click = self.on_double_click = self.on_right_click = self.on_drag_end = None
        self.on_hover = self.on_hover_dwell = self.on_dpi_changed = None

    def update(self, image: Image.Image, x: int | None = None, y: int | None = None) -> bool:
        if x is None or y is None:
            x, y = self.rect.left, self.rect.top
        self.rect = Rect(x, y, x + image.width, y + image.height)
        self.updates.append((image, x, y))
        return True

    def move(self, x: int, y: int) -> None:
        self.rect = Rect(x, y, x + self.rect.width, y + self.rect.height)
        self.moves.append((x, y))

    def show(self, image: Image.Image | None = None, x: int | None = None, y: int | None = None) -> None:
        self.visible = True

    def hide(self) -> None:
        self.visible = False
        if self.hovering:               # LayeredWindow reports the leave of a window that was hidden
            self.pointer(False)

    def bring_to_top(self) -> None:
        pass

    def set_click_through(self, on: bool) -> None:
        pass

    def destroy(self) -> None:
        self.visible = False
        self.destroyed = True

    def pointer(self, inside: bool) -> None:
        self.hovering = inside
        self.on_hover(inside)

    @property
    def shown(self) -> Image.Image:
        return self.updates[-1][0]

    @property
    def origin(self) -> tuple[int, int]:
        return self.updates[-1][1], self.updates[-1][2]


class Windows:
    """The window factory: keeps what it made, in order."""

    def __init__(self, dpi: int = 96) -> None:
        self.made: list[Window] = []
        self.dpi = dpi

    def __call__(self, host: Any, *, draggable: bool, topmost: bool = True, parent: int | None = None) -> Window:
        window = Window(host, draggable=draggable, topmost=topmost, dpi=self.dpi, parent=parent)
        self.made.append(window)
        return window


def taskbar_at(dpi: int) -> TaskbarInfo:
    height = round(48 * dpi / 96)
    return TaskbarInfo(
        hwnd=1, rect=Rect(0, 1080 - height, 1920, 1080), edge="bottom",
        tray_rect=Rect(1694, 1080 - height, 1920, 1080), monitor_rect=Rect(0, 0, 1920, 1080),
        work_area=Rect(0, 0, 1920, 1080 - height), dpi=dpi, autohide=False,
    )


class Platform:
    """A 1920x1080 screen with the taskbar at the bottom."""

    def __init__(self, dpi: int = 96) -> None:
        self.dpi = dpi
        self.areas = [Rect(0, 0, 1920, 1080 - round(48 * dpi / 96))]
        self.info: TaskbarInfo | None = taskbar_at(dpi)
        self.fullscreen = False
        self.cursor: tuple[int, int] | None = None

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


class Rig:
    """A FloatingWidget or TaskbarWidget on the fakes, drawn once by a skin."""

    def __init__(self, kind: str = "floating", theme_id: str = "nostromo", *, dpi: int = 96, skin: Any = None,
                 state: str = "normal3", position: dict[str, int] | None = None, animations: bool = True,
                 **options: Any) -> None:
        self.kind = kind
        self.scale = dpi / 96
        self.host = Host()
        self.plat = Platform(dpi)
        self.windows = Windows(dpi)
        self.stats = FrameStats()
        self.animations = animations
        self.position = position
        self.saved: list[tuple[int, int]] = []
        self.tooltip = Tooltip(self.host, platform=self.plat, window_factory=self.windows, stats=self.stats)
        common: dict[str, Any] = dict(
            on_open=lambda: None, on_menu=lambda x, y: None, tooltip=self.tooltip, platform=self.plat,
            window_factory=self.windows, stats=self.stats, animations_enabled=lambda: self.animations,
        )
        if kind == "floating":
            self.widget: Any = FloatingWidget(
                self.host, get_position=lambda: self.position, set_position=self._save, **common)
        else:
            self.widget = TaskbarWidget(self.host, **common)
        self.window: Window = self.windows.made[0]
        theme = theme_of(theme_id)
        self.options = RenderOptions(**options)
        self.skin = skin if skin is not None else get_skin(theme_id)
        self.ctx = RenderContext(
            model=model_of(state), theme=theme, variant=theme.default_variant, skin=self.skin,
            floating=self.options, taskbar=self.options,
        )
        self.widget.render(self.ctx)
        self.base: Image.Image = self.window.shown

    def _save(self, x: int, y: int) -> None:
        self.saved.append((x, y))
        self.position = {"x": x, "y": y}

    def render(self, **changes: Any) -> None:
        """Draw again with a changed model (blink_on, status ...) or changed options (reduce_motion ...)."""
        options = changes.pop("options", None)
        ctx = dataclasses.replace(self.ctx, model=dataclasses.replace(self.ctx.model, **changes)) if changes else self.ctx
        if options is not None:
            self.options = options
            ctx = dataclasses.replace(ctx, floating=options, taskbar=options)
        self.ctx = ctx
        self.widget.render(ctx)

    def switch(self, theme_id: str) -> None:
        """Draw again with another theme and its skin, as a live theme change does."""
        theme = theme_of(theme_id)
        self.skin = get_skin(theme_id)
        self.ctx = dataclasses.replace(self.ctx, theme=theme, variant=theme.default_variant, skin=self.skin)
        self.widget.render(self.ctx)

    def enter(self) -> None:
        self.window.pointer(True)

    def leave(self) -> None:
        self.window.pointer(False)

    def pending(self) -> list[tuple[int, int]]:
        return self.host.one_shots()

    def step(self) -> int:
        """Fire the one pending timer of the pointer response; returns its delay."""
        (timer_id, ms), = self.pending()
        self.host.fire(timer_id)
        return ms

    def finish(self) -> list[int]:
        """Fire timers until none is pending; returns their delays in order."""
        fired: list[int] = []
        while self.pending():
            fired.append(self.step())
            assert len(fired) < 50, "the response never ends"
        return fired

    @property
    def pictures(self) -> list[Image.Image]:
        return [image for image, _x, _y in self.window.updates]

    def visible_card(self, image: Image.Image | None = None) -> Rect:
        """The card on screen: the alpha box of a frame placed where the window is."""
        x, y = self.window.origin
        return on_screen(card_box(image if image is not None else self.base), x, y)


class CardSkin:
    """A skin that draws plain cards of a set size and answers the hover and insets calls as scripted."""

    def __init__(self, *, size: tuple[int, int] = (120, 60), insets: Any = NO_INSETS, plan: Any = None) -> None:
        self.size = size
        self.insets = insets
        self.plan = plan
        self.calls: collections.Counter[str] = collections.Counter()
        self.last: Image.Image | None = None      # the frame it drew last

    def _card(self, scale: float) -> Image.Image:
        w, h = round(self.size[0] * scale), round(self.size[1] * scale)
        self.last = Image.new("RGBA", (w, h), (200, 120, 0, 255))
        return self.last

    def render_floating(self, model, theme, variant, scale, opts=None):
        self.calls["floating"] += 1
        return self._card(scale)

    def render_taskbar(self, model, theme, variant, height_px, scale, opts=None):
        self.calls["taskbar"] += 1
        image = self._card(scale)
        return image.resize((image.width, max(height_px, 1)))

    def render_tooltip(self, model, theme, variant, scale):
        self.calls["tooltip"] += 1
        return self._card(scale)

    def render_tray_icon(self, model, theme, variant, size_px):
        return Image.new("RGBA", (size_px, size_px), (255, 255, 255, 255))

    def content_insets(self, surface, theme, model, scale):
        if isinstance(self.insets, Exception):
            raise self.insets
        return self.insets

    def hover_frames(self, frame, surface, scale, opts=None):
        self.calls["hover"] += 1
        plan = self.plan(frame, surface, scale, opts) if callable(self.plan) else self.plan
        if isinstance(plan, Exception):
            raise plan
        return plan


def tinted(frame: Image.Image, shade: int) -> Image.Image:
    """``frame`` with its colour moved, same shape: a response frame a test can tell from the others."""
    out = frame.copy()
    out.paste(Image.new("RGBA", out.size, (shade, 40, 90, 255)), mask=alpha_of(frame))
    return out


def scripted(count: int = 3, *, ms: int = 50, hold: bool = False, leave: int = 0) -> Any:
    """A plan for CardSkin: ``count`` tinted frames ``ms`` apart, optionally held with ``leave`` frames on the way out."""
    def make(frame, surface, scale, opts):
        frames = tuple(tinted(frame, 10 + 30 * i) for i in range(count))
        out = tuple(tinted(frame, 200 + 10 * i) for i in range(leave))
        return HoverPlay(frames=frames, delays_ms=(ms,) * count, hold=hold, leave=out, leave_ms=ms)
    return make


# ── content_insets: what the skins say about their margin ────────────────────

@pytest.mark.parametrize("scale", SCALES)
@pytest.mark.parametrize("surface", (SURFACE_FLOATING, SURFACE_TOOLTIP))
@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_the_insets_a_skin_reports_are_the_margin_of_the_frames_it_draws(theme_id, surface, scale):
    theme, skin = theme_of(theme_id), get_skin(theme_id)
    frame = frame_of(theme_id, surface, scale)
    insets = skin.content_insets(surface, theme, model_of(), scale)
    left, top, right, bottom = card_box(frame)[0], card_box(frame)[1], frame.width - card_box(frame)[2], frame.height - card_box(frame)[3]
    for said, measured in zip(insets, (left, top, right, bottom)):
        assert abs(said - measured) <= 1, (insets, (left, top, right, bottom))


@pytest.mark.parametrize("scale", SCALES)
@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_the_strip_is_the_taskbar_band_and_says_it_has_no_margin(theme_id, scale):
    theme, skin = theme_of(theme_id), get_skin(theme_id)
    height = round(48 * scale)
    frame = skin.render_taskbar(model_of(), theme, theme.default_variant, height, scale, None)
    assert frame.height == height
    assert skin.content_insets(SURFACE_TASKBAR, theme, model_of(), scale) == NO_INSETS


def test_only_gates_draws_a_shadow_margin_and_it_scales_with_the_screen():
    nostromo, night, gates = (get_skin(t) for t in THEME_IDS)
    for scale in SCALES:
        for surface in (SURFACE_FLOATING, SURFACE_TOOLTIP):
            assert nostromo.content_insets(surface, theme_of("nostromo"), model_of(), scale) == NO_INSETS
            assert night.content_insets(surface, theme_of("night_city"), model_of(), scale) == NO_INSETS
            margin = round(24 * scale)
            assert gates.content_insets(surface, theme_of("gates"), model_of(), scale) == (margin,) * 4


def test_the_shadow_margin_is_the_same_in_every_row_count_and_state():
    skin, theme = get_skin("gates"), theme_of("gates")
    for state in ("normal2", "normal3", "stale3", "offline3", "critical"):
        assert skin.content_insets(SURFACE_FLOATING, theme, model_of(state), 1.5) == (36,) * 4


# ── skin_insets: a widget must be placeable whatever a skin answers ──────────

def test_a_skin_without_content_insets_has_none():
    assert skin_insets(object(), SURFACE_FLOATING, None, model_of(), 1.0, (100, 50)) == NO_INSETS


@pytest.mark.parametrize("answer", [
    RuntimeError("boom"),             # the skin raises
    (1, 2, 3),                        # too few numbers
    ("a", "b", "c", "d"),             # not numbers
    (60, 0, 60, 0),                   # as wide as the frame
    (0, 30, 0, 30),                   # as tall as the frame
])
def test_unusable_insets_become_none(answer):
    assert skin_insets(CardSkin(insets=answer), SURFACE_FLOATING, None, model_of(), 1.0, (120, 60)) == NO_INSETS


def test_negative_and_fractional_insets_are_made_usable():
    assert skin_insets(CardSkin(insets=(-4, 2.9, 5, 0)), SURFACE_FLOATING, None, model_of(), 1.0, (120, 60)) == (0, 2, 5, 0)


def test_inset_rect_is_the_window_less_its_margin():
    assert inset_rect(Rect(100, 200, 400, 300), (10, 20, 30, 40)) == Rect(110, 220, 370, 260)
    assert inset_rect(Rect(100, 200, 400, 300), NO_INSETS) == Rect(100, 200, 400, 300)


# ── Placement arithmetic with a margin ────────────────────────────────────────

AREA = Rect(0, 0, 1920, 1032)


def test_the_default_corner_is_measured_from_the_visible_card():
    insets = (36, 36, 36, 36)
    x, y = floating_origin(None, 500, 300, 1.5, [AREA], insets)
    card = inset_rect(Rect(x, y, x + 500, y + 300), insets)
    assert (AREA.right - card.right, AREA.bottom - card.bottom) == (18, 18)


def test_without_insets_the_placement_is_what_it_always_was():
    assert floating_origin(None, 280, 126, 1.0, [AREA]) == (1920 - 280 - 12, 1032 - 126 - 12)
    assert floating_origin((50, 60), 280, 126, 1.0, [AREA]) == (50, 60)
    assert floating_origin((5000, 5000), 280, 126, 1.0, [AREA]) == (1920 - 280, 1032 - 126)
    assert tooltip_origin(Rect(1000, 900, 1280, 1026), 280, 134, AREA, 8) == (1000, 900 - 8 - 134)
    assert tooltip_origin(Rect(1000, 10, 1280, 130), 280, 134, AREA, 8) == (1000, 130 + 8)
    assert strip_origin(taskbar_at(96), 264, 48) == (1694 - 4 - 264, 1032)


def test_a_saved_position_is_the_windows_and_the_clamp_keeps_the_card_on_screen():
    insets = (24, 24, 24, 24)
    # far past the bottom right: the card touches the corner, the window hangs over by its margin
    assert floating_origin((9000, 9000), 400, 200, 1.0, [AREA], insets) == (1920 - 400 + 24, 1032 - 200 + 24)
    # far past the top left
    assert floating_origin((-9000, -9000), 400, 200, 1.0, [AREA], insets) == (-24, -24)
    # inside: untouched
    assert floating_origin((300, 300), 400, 200, 1.0, [AREA], insets) == (300, 300)


def test_the_tooltip_gap_is_between_the_visible_cards():
    insets = (36, 36, 36, 36)
    anchor = Rect(1000, 800, 1300, 900)                     # what the user sees of the widget
    x, y = tooltip_origin(anchor, 480, 280, AREA, 12, insets)
    card = inset_rect(Rect(x, y, x + 480, y + 280), insets)
    assert anchor.top - card.bottom == 12                   # above, 12 px off
    assert card.left + card.width // 2 == anchor.left + anchor.width // 2
    low = Rect(1000, 100, 1300, 200)                        # no room above: below, 12 px off
    x, y = tooltip_origin(low, 480, 280, AREA, 12, insets)
    assert inset_rect(Rect(x, y, x + 480, y + 280), insets).top - low.bottom == 12


def test_the_tooltip_card_stays_inside_the_work_area():
    insets = (36, 36, 36, 36)
    x, y = tooltip_origin(Rect(1800, 900, 1900, 1000), 480, 280, AREA, 12, insets)
    card = inset_rect(Rect(x, y, x + 480, y + 280), insets)
    assert card.right == AREA.right and card.left >= AREA.left
    x, y = tooltip_origin(Rect(0, 900, 100, 1000), 480, 280, AREA, 12, insets)
    assert inset_rect(Rect(x, y, x + 480, y + 280), insets).left == AREA.left


def test_the_strip_is_centred_and_set_off_by_its_card_when_a_skin_gives_it_a_margin():
    info = taskbar_at(96)
    x, y = strip_origin(info, 300, 60, (10, 6, 10, 6))
    card = inset_rect(Rect(x, y, x + 300, y + 60), (10, 6, 10, 6))
    assert info.tray_rect.left - 4 - card.right == 0
    assert card.top - info.rect.top == info.rect.bottom - card.bottom


# ── The widgets place what the user sees ─────────────────────────────────────

@pytest.mark.parametrize("dpi", [96, 144, 192])
@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_the_floating_widget_starts_a_set_margin_from_the_corner_whatever_its_theme(theme_id, dpi):
    rig = Rig("floating", theme_id, dpi=dpi)
    card, area = rig.visible_card(), rig.plat.areas[0]
    margin = round(12 * dpi / 96)
    assert abs(area.right - card.right - margin) <= 1
    assert abs(area.bottom - card.bottom - margin) <= 1


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_a_saved_position_past_the_screen_edge_leaves_the_card_touching_it(theme_id):
    far = Rig("floating", theme_id, dpi=144, position={"x": 100000, "y": 100000})
    card, area = far.visible_card(), far.plat.areas[0]
    assert abs(card.right - area.right) <= 1 and abs(card.bottom - area.bottom) <= 1
    near = Rig("floating", theme_id, dpi=144, position={"x": -5000, "y": -5000})
    card = near.visible_card()
    assert abs(card.left - near.plat.areas[0].left) <= 1 and abs(card.top - near.plat.areas[0].top) <= 1


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_dropping_the_widget_off_the_screen_pulls_the_card_back_and_saves_the_window(theme_id):
    rig = Rig("floating", theme_id, dpi=144)
    area, box = rig.plat.areas[0], card_box(rig.base)
    window = rig.window
    window.on_drag_end(area.right + 300, area.bottom + 300)         # dropped beyond the bottom right
    x, y = rig.saved[-1]
    assert window.moves[-1] == (x, y)
    card = on_screen(box, x, y)
    assert abs(card.right - area.right) <= 1 and abs(card.bottom - area.bottom) <= 1
    inside = (400, 300)
    window.on_drag_end(*inside)                                     # dropped where it fits: not touched
    assert rig.saved[-1] == inside and window.moves[-1] != inside


def test_the_shadow_margin_may_hang_over_the_screen_edge_but_the_card_may_not():
    rig = Rig("floating", "gates", dpi=144, position={"x": 100000, "y": 100000})
    area, window = rig.plat.areas[0], rig.window
    assert window.rect.right > area.right and window.rect.bottom > area.bottom       # the shadow's room
    assert rig.visible_card().right <= area.right + 1


@pytest.mark.parametrize("dpi", [96, 144, 192])
@pytest.mark.parametrize("kind", ["floating", "taskbar"])
@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_the_tooltip_stands_a_set_gap_off_the_widget_whatever_the_theme(theme_id, kind, dpi):
    rig = Rig(kind, theme_id, dpi=dpi)
    rig.widget.show_tooltip()
    assert rig.tooltip.visible
    tip = rig.windows.made[1]
    tip_card = on_screen(card_box(tip.shown), *tip.origin)
    gap = round(8 * dpi / 96)
    if kind == "taskbar":
        assert rig.window.rect.top - tip_card.bottom == gap          # the strip is the taskbar band
    else:
        assert abs(rig.visible_card().top - tip_card.bottom - gap) <= 1
    assert abs((tip_card.left + tip_card.right) // 2 - (rig.visible_card().left + rig.visible_card().right) // 2) <= 1


def test_the_gates_tooltip_no_longer_stands_its_shadow_margin_further_off():
    """Before the insets, the tooltip's own 36 px of transparent frame were added to the 12 px gap."""
    rig = Rig("taskbar", "gates", dpi=144)
    rig.widget.show_tooltip()
    tip = rig.windows.made[1]
    frame_gap = rig.window.rect.top - tip.rect.bottom
    assert frame_gap == 12 - 36                                       # the frames overlap by the margin less the gap
    assert rig.window.rect.top - on_screen(card_box(tip.shown), *tip.origin).bottom == 12


def test_a_screen_that_changes_resolution_re_measures_the_margin():
    rig = Rig("floating", "gates", dpi=96)
    assert rig.widget.insets == (24, 24, 24, 24)
    rig.window.dpi = 192
    rig.window.on_dpi_changed(192, Rect(0, 0, 0, 0))
    assert rig.widget.insets == (48, 48, 48, 48)
    assert rig.window.shown.size == frame_of("gates", SURFACE_FLOATING, 2.0).size


def test_a_skin_with_no_insets_leaves_every_widget_where_it_was():
    rig = Rig("floating", skin=CardSkin(size=(280, 126)))
    assert rig.widget.insets == NO_INSETS
    assert (rig.window.rect.left, rig.window.rect.top) == (1920 - 280 - 12, 1032 - 126 - 12)
    rig.widget.show_tooltip()
    tip = rig.windows.made[1]
    assert tip.rect.bottom == rig.window.rect.top - 8


def test_an_uneven_margin_is_measured_side_by_side():
    skin = CardSkin(size=(280, 126), insets=(10, 20, 30, 40))
    rig = Rig("floating", skin=skin)
    card = inset_rect(rig.window.rect, rig.widget.insets)
    assert rig.widget.insets == (10, 20, 30, 40)
    assert (rig.plat.areas[0].right - card.right, rig.plat.areas[0].bottom - card.bottom) == (12, 12)
    rig.widget.show_tooltip()
    tip = rig.windows.made[1]
    assert inset_rect(tip.rect, (10, 20, 30, 40)).bottom == card.top - 8


def test_a_strip_with_a_margin_is_centred_and_set_off_by_what_is_seen():
    skin = CardSkin(size=(260, 40), insets=(0, 2, 8, 10))
    rig = Rig("taskbar", skin=skin)
    bar, tray = rig.plat.info.rect, rig.plat.info.tray_rect
    card = inset_rect(rig.window.rect, rig.widget.insets)
    assert tray.left - card.right == 4
    assert card.top - bar.top == bar.bottom - card.bottom


def test_a_skin_that_cannot_say_its_insets_still_gets_a_placed_widget():
    rig = Rig("floating", skin=CardSkin(size=(280, 126), insets=RuntimeError("no margin for you")))
    assert rig.widget.insets == NO_INSETS and rig.window.visible


# ── hover_frames: what each skin answers ─────────────────────────────────────

def scaled(logical: float, scale: float) -> int:
    return int(logical * scale + 0.5)


@pytest.mark.parametrize("scale", SCALES)
@pytest.mark.parametrize("surface", SURFACES)
def test_nostromo_answers_with_one_brighter_frame_that_is_held(surface, scale):
    skin, base = get_skin("nostromo"), frame_of("nostromo", surface, scale)
    play = skin.hover_frames(base, surface, scale, RenderOptions())
    assert isinstance(play, HoverPlay)
    assert len(play.frames) == 1 and play.hold and play.leave == ()
    lit = play.frames[0]
    assert lit.size == base.size
    before = sum(base.convert("RGBa").convert("L").getdata())
    after = sum(lit.convert("RGBa").convert("L").getdata())
    assert after >= before * 1.10, "the phosphor is brighter, by at least a tenth"
    assert all(a >= b for a, b in zip(alpha_of(lit).getdata(), alpha_of(base).getdata())), "the glow never takes a pixel away"


@pytest.mark.parametrize("scale", SCALES)
@pytest.mark.parametrize("surface", SURFACES)
def test_night_city_answers_with_a_three_frame_glitch_that_ends_on_its_own(surface, scale):
    skin, base = get_skin("night_city"), frame_of("night_city", surface, scale)
    play = skin.hover_frames(base, surface, scale, RenderOptions())
    assert isinstance(play, HoverPlay)
    assert len(play.frames) == 3 and play.delays_ms == (60, 60, 60) and not play.hold
    assert all(f.size == base.size for f in play.frames)
    assert all(differs(f, base) for f in play.frames), "each frame is a glitch"
    assert len({f.tobytes() for f in play.frames}) == 3
    assert ImageChops.difference(alpha_of(play.frames[2]), alpha_of(base)).getbbox() is None, "the last one only fringes"
    first = ImageChops.difference(play.frames[0], base)
    last = ImageChops.difference(play.frames[2], base)
    assert sum(first.convert("L").getdata()) > sum(last.convert("L").getdata()), "the glitch settles"


@pytest.mark.parametrize("scale", SCALES)
@pytest.mark.parametrize("surface", SURFACES)
def test_gates_lifts_its_card_on_a_four_step_spring_and_settles_back(surface, scale):
    skin, base = get_skin("gates"), frame_of("gates", surface, scale)
    play = skin.hover_frames(base, surface, scale, RenderOptions())
    assert isinstance(play, HoverPlay)
    assert len(play.frames) == 4 and play.delays_ms[:3] == (40, 40, 40) and play.hold
    assert len(play.leave) == 1 and play.leave_ms == 40
    top = card_box(base)[1]
    rises = [top - card_box(f)[1] for f in (*play.frames, *play.leave)]
    wanted = [min(scaled(logical, scale), top) for logical in (1, 3, 2, 2, 1)]
    assert rises == wanted
    assert all(f.size == base.size for f in (*play.frames, *play.leave))
    assert all(card_box(f)[0] == card_box(base)[0] for f in play.frames), "the card only moves up"


def test_the_gates_lift_stays_inside_its_frame_and_leaves_a_shadow_on_the_ground():
    skin, base = get_skin("gates"), frame_of("gates", SURFACE_FLOATING, 1.5)
    play = skin.hover_frames(base, SURFACE_FLOATING, 1.5, RenderOptions())
    lifted = play.frames[1]
    box = card_box(base)
    ground = alpha_of(lifted).crop((box[0] + 40, box[3] - 3, box[2] - 40, box[3] + 3))
    assert max(ground.getdata()) > 0, "the card's old place under it is shaded"
    flat = skin.hover_frames(frame_of("gates", SURFACE_FLOATING, 1.5, transparent_bg=True), SURFACE_FLOATING, 1.5,
                             RenderOptions(transparent_bg=True))
    assert flat is not None, "a transparent widget lifts too, without a shadow"


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_the_tooltip_has_no_pointer_response(theme_id):
    skin = get_skin(theme_id)
    assert skin.hover_frames(frame_of(theme_id, SURFACE_TOOLTIP, 1.0), SURFACE_TOOLTIP, 1.0, RenderOptions()) is None


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_a_response_is_made_from_the_frame_without_touching_it_and_the_same_every_time(theme_id):
    skin, base = get_skin(theme_id), frame_of(theme_id, SURFACE_FLOATING, 1.5)
    before = base.tobytes()
    one = skin.hover_frames(base, SURFACE_FLOATING, 1.5, RenderOptions())
    two = skin.hover_frames(base, SURFACE_FLOATING, 1.5, RenderOptions())
    assert base.tobytes() == before
    assert [f.tobytes() for f in one.frames] == [f.tobytes() for f in two.frames]


@pytest.mark.parametrize("state", ["warning", "critical", "stale3", "offline3", "paused3", "rate_limited3", "loading"])
@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_every_state_has_a_response(theme_id, state):
    skin, base = get_skin(theme_id), frame_of(theme_id, SURFACE_FLOATING, 1.0, state=state)
    play = skin.hover_frames(base, SURFACE_FLOATING, 1.0, RenderOptions())
    assert play is not None and all(f.size == base.size for f in play.frames)


# ── The widget plays the response ────────────────────────────────────────────

def expected_enter(play: HoverPlay) -> tuple[list[Image.Image], list[int]]:
    """The frames shown on entering and the timers set, from the skin's plan."""
    delays = list(play.delays_ms[:-1]) if play.hold else list(play.delays_ms)
    return list(play.frames), delays


@pytest.mark.parametrize("kind", ["floating", "taskbar"])
@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_entering_plays_the_skins_frames_in_order_and_leaving_returns_the_normal_frame(theme_id, kind):
    rig = Rig(kind, theme_id, dpi=144)
    play = rig.skin.hover_frames(rig.base, kind, rig.scale, rig.options)
    frames, delays = expected_enter(play)
    before = len(rig.window.updates)
    rig.enter()
    assert rig.widget.hover_phase == ("hold" if play.hold and len(frames) == 1 else "play")
    assert rig.finish() == delays
    shown = rig.pictures[before:]
    assert len(shown) == len(frames) + (0 if play.hold else 1)
    assert all(same_picture(got, want) for got, want in zip(shown, frames))
    if play.hold:
        assert rig.widget.hover_phase == "hold" and not rig.pending()
        rig.leave()
        waited = rig.finish()
        assert waited == [play.leave_ms] * len(play.leave)
        assert all(same_picture(got, want) for got, want in zip(rig.pictures[before + len(frames):], play.leave))
    else:
        assert shown[-1] is rig.base
    assert rig.window.shown is rig.base
    assert rig.widget.hover_phase == "idle" and not rig.pending()


def test_nostromo_holds_its_bright_frame_without_any_timer():
    rig = Rig("floating", "nostromo")
    rig.enter()
    assert rig.widget.hover_phase == "hold"
    assert not rig.pending(), "a held frame costs nothing while the pointer rests on it"
    rig.leave()
    assert rig.window.shown is rig.base and rig.widget.hover_phase == "idle"


def test_night_city_glitches_once_per_visit_in_three_60_ms_steps():
    rig = Rig("floating", "night_city")
    rig.enter()
    assert rig.pending() and rig.pending()[0][1] == 60
    assert rig.finish() == [60, 60, 60]                   # frame 1 at once; frame 2, frame 3 and the normal frame follow
    assert rig.window.shown is rig.base                   # at 60, 120 and 180 ms
    assert rig.widget.hover_phase == "idle" and not rig.pending()
    rig.leave()                                           # leaving afterwards changes nothing
    assert rig.window.shown is rig.base
    rig.enter()
    assert rig.widget.hover_phase == "play", "a second visit glitches again"


def test_gates_springs_up_over_120_ms_holds_and_settles_when_the_pointer_goes():
    rig = Rig("floating", "gates")
    updates = len(rig.window.updates)
    rig.enter()
    assert rig.finish() == [40, 40, 40]
    assert rig.widget.hover_phase == "hold" and not rig.pending()
    assert len(rig.window.updates) == updates + 4
    rig.leave()
    assert rig.widget.hover_phase == "leave" and rig.pending()[0][1] == 40
    rig.finish()
    assert rig.window.shown is rig.base and rig.widget.hover_phase == "idle"


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_nothing_runs_while_the_pointer_is_away(theme_id):
    rig = Rig("floating", theme_id)
    assert not rig.pending()
    rig.render()                                          # the same data again, then both blink phases
    rig.render(blink_on=False)
    rig.render(blink_on=True)
    assert not rig.pending() and rig.widget.hover_phase == "idle"
    assert all(picture.mode == "RGBA" for picture in rig.pictures), "the skin's own frames, nothing derived"
    rig.enter()
    rig.leave()
    rig.finish()
    assert not rig.pending() and rig.widget.hover_phase == "idle"


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_a_hover_renders_nothing_new_and_counts_no_frame_as_rendered(theme_id):
    rig = Rig("floating", theme_id)
    rendered = dict(rig.stats.rendered)
    rig.enter()
    rig.finish()
    rig.leave()
    rig.finish()
    assert dict(rig.stats.rendered) == rendered, "the response is derived from the frame, not drawn by the skin"
    assert rig.stats.failed == 0


def test_the_response_is_derived_once_per_frame_and_kept_for_the_next_visit():
    skin = CardSkin(plan=scripted(3, hold=True, leave=1))
    rig = Rig("floating", skin=skin)
    for _ in range(3):
        rig.enter()
        rig.finish()
        rig.leave()
        rig.finish()
    assert skin.calls["hover"] == 1
    rig.render(blink_on=False)                            # the other blink phase is another frame
    rig.enter()
    rig.finish()
    rig.leave()
    rig.finish()
    assert skin.calls["hover"] == 2
    rig.render(blink_on=True)
    rig.enter()
    rig.leave()
    rig.finish()
    assert skin.calls["hover"] == 2, "both phases are kept"


def test_new_data_while_a_response_is_held_holds_the_response_to_the_new_frame():
    skin = CardSkin(plan=scripted(2, hold=True, leave=1))
    rig = Rig("floating", skin=skin)
    rig.enter()
    rig.finish()
    assert same_picture(rig.window.shown, tinted(rig.base, 40))
    rig.render(rows=rig.ctx.model.rows[:1])                                 # other data, so another frame
    assert skin.calls["hover"] == 2 and skin.calls["floating"] == 2
    assert rig.widget.hover_phase == "hold" and not rig.pending()
    assert same_picture(rig.window.shown, tinted(skin.last, 40)), "the held frame is the new frame's response"
    rig.leave()
    rig.finish()
    assert rig.window.shown is skin.last


# ── The response follows the window ──────────────────────────────────────────

def test_the_alive_mark_keeps_blinking_under_a_held_response():
    rig = Rig("floating", "nostromo")
    lit = rig.base
    rig.render(blink_on=False)
    dark = rig.window.shown
    rig.enter()
    held_dark = rig.window.shown
    rig.render(blink_on=True)
    held_lit = rig.window.shown
    assert held_dark is not held_lit and same_picture(held_dark, rig.skin.hover_frames(dark, "floating", 1.0, rig.options).frames[0])
    assert same_picture(held_lit, rig.skin.hover_frames(lit, "floating", 1.0, rig.options).frames[0])
    rig.render(blink_on=False)
    assert rig.window.shown is held_dark, "back to the same ready frame: nothing was made again"
    rig.leave()
    assert rig.window.shown is dark


@pytest.mark.parametrize("kind", ["floating", "taskbar"])
@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_reduce_motion_means_no_response_at_all(theme_id, kind):
    rig = Rig(kind, theme_id, reduce_motion=True)
    count = len(rig.window.updates)
    rig.enter()
    assert not rig.pending() and rig.widget.hover_phase == "idle"
    assert len(rig.window.updates) == count
    rig.leave()
    assert len(rig.window.updates) == count


def test_reduce_motion_costs_not_even_the_derivation():
    skin = CardSkin(plan=scripted(2))
    rig = Rig("floating", skin=skin, reduce_motion=True)
    rig.enter()
    assert skin.calls["hover"] == 0


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_switching_reduce_motion_on_while_a_response_is_up_ends_it_at_once(theme_id):
    rig = Rig("floating", theme_id)
    rig.enter()
    rig.render(options=RenderOptions(reduce_motion=True))
    assert rig.widget.hover_phase == "idle" and not rig.pending()
    assert rig.window.shown is rig.window.updates[-1][0]
    assert rig.window.shown.mode == "RGBA", "the normal frame is back"
    rig.leave()
    assert not rig.pending()


def test_the_animations_switch_of_windows_stops_the_response_too():
    rig = Rig("floating", "night_city", animations=False)
    rig.enter()
    assert not rig.pending() and rig.widget.hover_phase == "idle"
    rig.animations = True
    rig.enter()
    assert rig.widget.hover_phase == "play"


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_nothing_plays_on_a_hidden_window_or_during_a_drag(theme_id):
    rig = Rig("floating", theme_id)
    rig.window.dragging = True
    rig.enter()
    assert rig.widget.hover_phase == "idle" and not rig.pending()
    rig.window.dragging = False
    rig.leave()
    rig.window.visible = False
    rig.enter()
    assert rig.widget.hover_phase == "idle" and not rig.pending()


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_hiding_the_widget_puts_the_normal_frame_back_and_stops_the_timers(theme_id):
    rig = Rig("floating", theme_id)
    rig.ctx = dataclasses.replace(rig.ctx, hide_floating_in_fullscreen=True)
    rig.enter()
    rig.plat.fullscreen = True
    rig.widget.render(rig.ctx)                            # a full screen app comes up
    assert not rig.window.visible
    assert rig.widget.hover_phase == "idle" and not rig.pending()
    assert rig.window.shown is rig.base


def test_the_strip_hides_with_a_full_screen_app_and_its_response_with_it():
    rig = Rig("taskbar", "night_city")
    rig.enter()
    assert rig.pending()
    rig.plat.fullscreen = True
    rig.widget.keep()
    assert not rig.window.visible and not rig.pending() and rig.widget.hover_phase == "idle"
    assert rig.window.shown is rig.base


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_destroying_the_widget_mid_response_leaves_no_timer(theme_id):
    rig = Rig("floating", theme_id)
    rig.enter()
    rig.widget.destroy()
    assert not rig.pending()
    assert rig.window.destroyed


def test_a_timer_that_fires_after_the_window_is_gone_does_nothing():
    rig = Rig("floating", "night_city")
    rig.enter()
    (timer_id, _ms), = rig.pending()
    _ms, fn, _repeat = rig.host.timers[timer_id]
    rig.widget.destroy()
    fn()                                                  # a tick that was already queued
    assert not rig.pending()


def test_leaving_in_the_middle_of_a_one_shot_ends_it_at_once():
    rig = Rig("floating", "night_city")
    rig.enter()
    rig.step()
    rig.leave()
    assert rig.window.shown is rig.base and not rig.pending() and rig.widget.hover_phase == "idle"


def test_leaving_in_the_middle_of_a_held_spring_goes_out_through_the_settle_frame():
    rig = Rig("floating", "gates")
    rig.enter()
    rig.step()
    rig.leave()
    assert rig.widget.hover_phase == "leave"
    rig.finish()
    assert rig.window.shown is rig.base


def test_coming_back_during_the_settle_starts_the_spring_again():
    rig = Rig("floating", "gates")
    rig.enter()
    rig.finish()
    rig.leave()
    assert rig.widget.hover_phase == "leave"
    rig.enter()
    assert rig.widget.hover_phase == "play" and len(rig.pending()) == 1
    rig.finish()
    assert rig.widget.hover_phase == "hold"


def test_an_enter_while_the_response_is_already_up_changes_nothing():
    rig = Rig("floating", "gates")
    rig.enter()
    count = len(rig.window.updates)
    rig.enter()
    assert len(rig.window.updates) == count and len(rig.pending()) == 1


def test_a_held_response_carries_over_to_the_next_skin_only_if_that_one_holds_too():
    rig = Rig("floating", "nostromo")
    rig.enter()
    assert rig.widget.hover_phase == "hold"
    rig.switch("gates")                                   # Gates holds its lift as well: the response goes on
    assert rig.widget.hover_phase == "hold" and rig.window.shown.mode == "RGBa"
    assert not rig.pending()
    rig.switch("night_city")                              # a glitch is a one-shot, it cannot be held: back to normal
    assert rig.widget.hover_phase == "idle" and not rig.pending()
    assert rig.window.shown.mode == "RGBA"


def test_a_failing_swap_ends_the_response_instead_of_leaving_it_half_played(caplog):
    rig = Rig("floating", "night_city")
    rig.enter()
    original = rig.window.update

    def broken(image, x=None, y=None):
        raise RuntimeError("Windows said no")
    rig.window.update = broken
    with caplog.at_level("WARNING", logger="usage_monitor"):
        rig.step()                                        # the tick must not raise into the host
    rig.window.update = original
    assert rig.widget.hover_phase == "idle" and not rig.pending()
    assert any("pointer response failed" in record.getMessage() for record in caplog.records)
    rig.render()                                          # the next redraw puts the normal frame back
    assert rig.window.shown is rig.base


def test_the_tooltip_still_follows_the_dwell_and_goes_with_the_pointer():
    rig = Rig("floating", "gates")
    rig.enter()
    rig.window.on_hover_dwell()
    assert rig.tooltip.visible
    rig.leave()
    assert not rig.tooltip.visible


# ── A skin that cannot answer, or answers badly ──────────────────────────────

def test_a_skin_without_hover_frames_has_no_response():
    class Plain(CardSkin):
        hover_frames = None
    rig = Rig("floating", skin=Plain())
    rig.enter()
    assert not rig.pending() and rig.widget.hover_phase == "idle"


@pytest.mark.parametrize("plan", [None, RuntimeError("the skin broke")])
def test_a_skin_that_declines_or_raises_costs_the_widget_nothing(plan):
    skin = CardSkin(plan=plan)
    rig = Rig("floating", skin=skin)
    count = len(rig.window.updates)
    rig.enter()
    rig.enter()
    rig.leave()
    rig.enter()
    assert skin.calls["hover"] == 1, "a failure is not tried again for the same frame"
    assert not rig.pending() and len(rig.window.updates) == count


def test_a_response_made_of_frames_of_another_size_is_refused():
    def wrong(frame, surface, scale, opts):
        return HoverPlay(frames=(Image.new("RGBA", (10, 10)),), delays_ms=(0,), hold=True)
    rig = Rig("floating", skin=CardSkin(plan=wrong))
    count = len(rig.window.updates)
    rig.enter()
    assert rig.widget.hover_phase == "idle" and len(rig.window.updates) == count


@pytest.mark.parametrize("kind", ["floating", "taskbar"])
@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_a_response_never_uncovers_a_pixel_the_normal_frame_covered(theme_id, kind):
    """Windows lets the pointer through pixels with no coverage at all: a frame that moved the card away
    from under a resting pointer would end the hover, and the next mouse move would begin it again."""
    rig = Rig(kind, theme_id, dpi=144)
    start = len(rig.window.updates)
    rig.enter()
    rig.finish()
    rig.leave()
    rig.finish()
    covered = alpha_of(rig.base).point(lambda v: 255 if v else 0)
    played = [p for p in rig.pictures[start:] if p is not rig.base]
    assert played
    for picture in played:
        now = alpha_of(picture).point(lambda v: 255 if v else 0)
        assert ImageChops.subtract(covered, now).getbbox() is None


def test_the_skins_own_frames_do_uncover_pixels_so_the_floor_is_needed():
    """The reason for the floor above: Gates' lifted card leaves pixels of its old place with no coverage."""
    base = frame_of("gates", SURFACE_FLOATING, 1.5, transparent_bg=True)
    play = get_skin("gates").hover_frames(base, SURFACE_FLOATING, 1.5, RenderOptions(transparent_bg=True))
    covered = alpha_of(base).point(lambda v: 255 if v else 0)
    gone = ImageChops.subtract(covered, alpha_of(play.frames[1]).point(lambda v: 255 if v else 0))
    assert gone.getbbox() is not None


def test_a_response_frame_is_what_windows_takes_without_converting_it_again():
    rig = Rig("floating", "nostromo")
    rig.enter()
    shown = rig.window.shown
    assert shown.mode == "RGBa", "premultiplied once, when the response was made"
    assert premultiplied_bgra(shown) == shown.tobytes("raw", "BGRa")      # LayeredWindow copies it as it is


def test_a_swap_does_not_move_the_window():
    rig = Rig("floating", "gates")
    rig.window.rect = Rect(500, 400, 500 + rig.window.rect.width, 400 + rig.window.rect.height)      # dragged elsewhere
    x, y = rig.window.rect.left, rig.window.rect.top
    rig.enter()
    rig.finish()
    assert (rig.window.rect.left, rig.window.rect.top) == (x, y)


# ── The animations switch ────────────────────────────────────────────────────

def test_the_switch_is_the_callers_then_the_platforms_then_windows_then_yes():
    class Opinion:
        def animations_enabled(self):
            return False
    assert widgets._animation_switch(Opinion(), None)() is False
    assert widgets._animation_switch(Opinion(), lambda: True)() is True
    assert widgets._animation_switch(object(), None)() is True                  # a fake platform has no opinion
    assert widgets._animation_switch(widgets.default_platform, None) is widgets.os_animations_enabled


def test_windows_animations_switch_is_read_and_a_failed_read_means_yes(monkeypatch):
    assert isinstance(widgets.os_animations_enabled(), bool)

    def says(value, ok=True):
        def fake(action, param, ref, ini):
            assert action == widgets.SPI_GETCLIENTAREAANIMATION
            ref._obj.value = value
            return 1 if ok else 0
        return fake

    monkeypatch.setattr(widgets.win32, "SystemParametersInfoW", says(0))
    assert widgets.os_animations_enabled() is False
    monkeypatch.setattr(widgets.win32, "SystemParametersInfoW", says(1))
    assert widgets.os_animations_enabled() is True
    monkeypatch.setattr(widgets.win32, "SystemParametersInfoW", says(0, ok=False))
    assert widgets.os_animations_enabled() is True
    monkeypatch.setattr(widgets.win32, "SystemParametersInfoW", lambda *a: (_ for _ in ()).throw(OSError("no")))
    assert widgets.os_animations_enabled() is True


# ── The cost of a swap, on a real layered window ─────────────────────────────

@UI
def test_a_swap_of_a_response_frame_costs_under_two_milliseconds_on_a_real_window(capsys):
    from usage_monitor.ui.host import UIHost
    from usage_monitor.ui.layered import LayeredWindow

    host = UIHost(controller_class=f"CUM.Test.Controller.{uuid.uuid4().hex[:8]}")
    window = None
    results: list[str] = []
    try:
        window = LayeredWindow(host, draggable=True)
        for theme_id in THEME_IDS:
            for scale in SCALES:
                base = frame_of(theme_id, SURFACE_FLOATING, scale)
                skin = get_skin(theme_id)
                started = time.perf_counter()
                play = skin.hover_frames(base, SURFACE_FLOATING, scale, RenderOptions())
                made = (time.perf_counter() - started) * 1000
                started = time.perf_counter()
                plan = widgets._prepare(play, base)
                prepared = (time.perf_counter() - started) * 1000
                window.update(base, -30000, -30000)
                window.show()
                timings: list[float] = []
                for _ in range(40):
                    for picture in (*plan.frames, *plan.leave):
                        started = time.perf_counter()
                        window.update(picture)
                        timings.append((time.perf_counter() - started) * 1000)
                median, worst = statistics.median(timings), max(timings)
                results.append(f"{theme_id} x{scale}: swap median {median:.2f} ms, worst {worst:.2f} ms; "
                               f"derive {made:.1f} ms + prepare {prepared:.1f} ms once ({base.size[0]}x{base.size[1]})")
                assert median < 2.0, results[-1]
    finally:
        if window is not None:
            window.destroy()
        host.close()
    with capsys.disabled():
        print("\n" + "\n".join(results))


class Parked:
    """The real platform module with every geometry answer moved far off-screen, so a real window is
    created, drawn and shown where nobody sees it (and the pointer is never over it)."""

    def __getattr__(self, name: str) -> Any:
        return getattr(plat, name)

    def work_areas(self):
        return [Rect(-30000, -30000, -27000, -28600)]

    def work_area_at(self, x, y):
        return self.work_areas()[0]

    def dpi_for_point(self, x, y):
        return 96

    def is_fullscreen_foreground(self):
        return False

    def cursor_pos(self):
        return None


def pump(until, timeout: float = 5.0) -> bool:
    """Dispatch this thread's window messages (timers among them) until ``until()`` is true."""
    from ctypes import wintypes

    from usage_monitor.ui import win32
    msg = wintypes.MSG()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if until():
            return True
        while win32.PeekMessageW(ctypes.byref(msg), None, 0, 0, win32.PM_REMOVE):
            win32.TranslateMessage(ctypes.byref(msg))
            win32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.002)
    return bool(until())


@UI
def test_the_response_plays_on_a_real_host_with_real_timers(capsys):
    """The widget on a real UIHost and a real layered window, the pointer reported the way LayeredWindow
    reports it: the response runs on WM_TIMER ticks, ends on its own, and each tick is cheap."""
    from usage_monitor.ui.host import UIHost

    host = UIHost(controller_class=f"CUM.Test.Controller.{uuid.uuid4().hex[:8]}")
    floating = None
    ticks: list[float] = []
    original = widgets._Hover._tick

    def timed(self):
        started = time.perf_counter()
        original(self)
        ticks.append((time.perf_counter() - started) * 1000)

    widgets._Hover._tick = timed
    report: list[str] = []
    try:
        stats = FrameStats()
        floating = FloatingWidget(host, get_position=lambda: None, set_position=lambda x, y: None,
                                  on_open=lambda: None, on_menu=lambda x, y: None, platform=Parked(), stats=stats)
        for theme_id, held in (("night_city", False), ("gates", True), ("nostromo", True)):
            theme = theme_of(theme_id)
            ctx = RenderContext(model_of(), theme, theme.default_variant, get_skin(theme_id))
            floating.render(ctx)
            assert floating.visible
            presented = stats.presented["floating"]
            began = time.perf_counter()
            floating.window.on_hover(True)                    # what LayeredWindow does on the first mouse move
            assert floating.hover_phase in ("play", "hold")
            assert pump(lambda: floating.hover_phase == ("hold" if held else "idle"))
            up = (time.perf_counter() - began) * 1000
            swaps = stats.presented["floating"] - presented
            if held:
                floating.window.on_hover(False)               # the pointer goes
                assert pump(lambda: floating.hover_phase == "idle")
            total = (time.perf_counter() - began) * 1000
            report.append(f"{theme_id}: {swaps} swaps in {up:.0f} ms, over after {total:.0f} ms")
            assert host._timers == {}, "an idle widget has no timer running"
            assert floating.hover_phase == "idle"
            assert stats.failed == 0
        assert ticks and statistics.median(ticks) < 2.0, ticks
        report.append(f"timer ticks: {len(ticks)}, median {statistics.median(ticks):.2f} ms, worst {max(ticks):.2f} ms")
    finally:
        widgets._Hover._tick = original
        if floating is not None:
            floating.destroy()
        host.close()
    with capsys.disabled():
        print("\n" + "\n".join(report))
