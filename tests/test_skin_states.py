"""Tests that every status looks different where a widget has no room for words: the taskbar strip and the
tray icon, in all three themes (docs/UI_ARCHITECTURE.md, model.LAMP_KINDS).

The states that need telling apart are live, paused, stale (the sign-in is lost, so the user must act),
offline (no connection, retried by itself) and rate limited (the usage API throttles the monitor, also retried
by itself). Before, the Nostromo strip drew all three of the last as the same hollow ring, the Gates strip
drew stale and rate limited the same, and Nostromo and Night City drew the paused tray icon like a live one.

A difference counts when pixels differ, at each size, by a margin that holds for every variant. The tray
icons are also checked where they are seen: the Nostromo icon is on its own opaque plate, so the lit arc and
its empty track keep their contrast on a light taskbar as on a dark one, and the Night City icon stands off
both taskbars.
"""
from __future__ import annotations

import dataclasses
import itertools
import math
from typing import Any

import pytest
from PIL import Image, ImageChops

from usage_monitor import themes
from usage_monitor.render import dump
from usage_monitor.render.skins import get_skin
from usage_monitor.render.skins import night_city, nostromo
from usage_monitor.themes import colour

THEME_IDS = ("nostromo", "night_city", "gates")
TRAY_SIZES = (16, 20, 24, 32)
SCALES = (1.0, 1.5, 2.0)
TASKBARS = {"dark": "#202020", "light": "#f3f3f3"}
LIVE, PAUSED, STALE, OFFLINE, LIMITED = "normal3", "paused", "stale", "offline", "rate_limited"
ALL_STATES = (LIVE, PAUSED, STALE, OFFLINE, LIMITED)
ACT_OR_WAIT = (STALE, OFFLINE, LIMITED)         # the three that a strip used to draw alike


def theme_of(theme_id: str) -> Any:
    return themes.get_theme(theme_id)


def variants_of(theme_id: str) -> list[Any]:
    return list(theme_of(theme_id).variants)


def differing_pixels(a: Image.Image, b: Image.Image) -> int:
    """How many pixels differ in colour or coverage between two images of one size."""
    assert a.size == b.size, (a.size, b.size)
    mask = None
    for band in ImageChops.difference(a.convert("RGBA"), b.convert("RGBA")).split():
        changed = band.point(lambda v: 255 if v else 0)
        mask = changed if mask is None else ImageChops.lighter(mask, changed)
    return sum(1 for v in mask.getdata() if v)


def hexof(rgb: tuple[int, ...]) -> str:
    return "#%02x%02x%02x" % tuple(rgb[:3])


def contrast(a: tuple[int, ...], b: tuple[int, ...] | str) -> float:
    return colour.contrast_ratio(hexof(a), b if isinstance(b, str) else hexof(b))


def tray(theme_id: str, variant: Any, state: str, size: int) -> Image.Image:
    return get_skin(theme_id).render_tray_icon(dump.sample_model(state), theme_of(theme_id), variant, size)


def strip(theme_id: str, variant: Any, state: str, scale: float) -> Image.Image:
    height = round(48 * scale)
    return get_skin(theme_id).render_taskbar(dump.sample_model(state), theme_of(theme_id), variant, height, scale, None)


def least_tray_difference(size: int) -> int:
    """The margin a tray icon pair must clear at ``size``: about one pixel in thirty-two, and never fewer than eight."""
    return max(8, size * size // 32)


# ── The taskbar strip ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("scale", SCALES)
@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_the_strip_tells_stale_offline_and_rate_limited_apart(theme_id, scale):
    for variant in variants_of(theme_id):
        frames = {state: strip(theme_id, variant, state, scale) for state in ACT_OR_WAIT}
        for a, b in itertools.combinations(ACT_OR_WAIT, 2):
            assert differing_pixels(frames[a], frames[b]) >= round(16 * scale * scale), (theme_id, variant.id, a, b)


@pytest.mark.parametrize("scale", SCALES)
@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_the_strip_tells_paused_from_live_and_from_the_rest(theme_id, scale):
    for variant in variants_of(theme_id):
        frames = {state: strip(theme_id, variant, state, scale) for state in ALL_STATES}
        for other in (LIVE, *ACT_OR_WAIT):
            assert differing_pixels(frames[PAUSED], frames[other]) >= round(16 * scale * scale), (theme_id, variant.id, other)


def difference_box(a: Image.Image, b: Image.Image) -> tuple[int, int, int, int] | None:
    """The box that holds every pixel where two images differ in colour or coverage, or None."""
    boxes = [band.getbbox() for band in ImageChops.difference(a.convert("RGBA"), b.convert("RGBA")).split()]
    boxes = [box for box in boxes if box is not None]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


@pytest.mark.parametrize("scale", SCALES)
def test_the_nostromo_strip_differs_between_those_three_only_in_the_mark(scale):
    """Stale, offline and rate limited share every other pixel: the bars, the numbers and the plate. What tells
    them apart is the mark beside the label (a ring, a cross, three dots), a few pixels wide."""
    variant = theme_of("nostromo").default_variant
    frames = {state: strip("nostromo", variant, state, scale) for state in ACT_OR_WAIT}
    for a, b in itertools.combinations(ACT_OR_WAIT, 2):
        box = difference_box(frames[a], frames[b])
        assert box is not None, (a, b)
        assert box[2] - box[0] <= 10 * scale and box[3] - box[1] <= 10 * scale, (a, b, box)


# ── The tray icon ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("size", TRAY_SIZES)
@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_the_tray_icon_tells_all_five_states_apart(theme_id, size):
    for variant in variants_of(theme_id):
        icons = {state: tray(theme_id, variant, state, size) for state in ALL_STATES}
        for a, b in itertools.combinations(ALL_STATES, 2):
            assert differing_pixels(icons[a], icons[b]) >= least_tray_difference(size), (theme_id, variant.id, a, b)


@pytest.mark.parametrize("theme_id", ("nostromo", "night_city"))
def test_a_paused_tray_icon_no_longer_reads_as_a_live_one(theme_id):
    for variant in variants_of(theme_id):
        for size in TRAY_SIZES:
            live, paused = tray(theme_id, variant, LIVE, size), tray(theme_id, variant, PAUSED, size)
            assert differing_pixels(live, paused) >= least_tray_difference(size) // 2, (variant.id, size)


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_a_state_keeps_its_look_whatever_the_numbers_are(theme_id):
    """The mark belongs to the status: a different percent changes the arc or the cells, not which mark it is."""
    variant = theme_of(theme_id).default_variant
    low, high = dump.sample_model(OFFLINE), dump.sample_model(OFFLINE)
    high = dataclasses.replace(high, rows=[dataclasses.replace(r, percent=88.0) for r in high.rows])
    skin = get_skin(theme_id)
    for size in (16, 24):
        a = skin.render_tray_icon(low, theme_of(theme_id), variant, size)
        b = skin.render_tray_icon(high, theme_of(theme_id), variant, size)
        assert differing_pixels(a, tray(theme_id, variant, LIVE, size)) >= least_tray_difference(size)
        assert differing_pixels(b, tray(theme_id, variant, LIVE, size)) >= least_tray_difference(size)


# ── Nostromo: the icon on its own plate (item: a light taskbar) ──────────────

def nostromo_models(severity: str) -> Any:
    """A model with the first row at 50 %, so the ring has a lit half and an empty half."""
    base = dump.sample_model(LIVE)
    row = dataclasses.replace(base.rows[0], percent=50.0, severity=severity)
    return dataclasses.replace(base, rows=[row, *base.rows[1:]])


def ring_colours(icon: Image.Image, size: int) -> tuple[set, set]:
    """The colours of the lit half of the ring (3 o'clock) and of the empty half (9 o'clock), sampled well inside the stroke."""
    radius, thickness, _chamfer = nostromo._tray_geometry(size)
    centre = size / 2
    lit, track = set(), set()
    for y in range(size):
        for x in range(size):
            dx, dy = x + 0.5 - centre, y + 0.5 - centre
            r = math.hypot(dx, dy)
            if not radius - thickness + 0.5 <= r <= radius - 0.5:
                continue
            angle = math.degrees(math.atan2(dx, -dy)) % 360
            if 40 <= angle <= 140:
                lit.add(icon.getpixel((x, y)))
            elif 220 <= angle <= 320:
                track.add(icon.getpixel((x, y)))
    return lit, track


@pytest.mark.parametrize("size", TRAY_SIZES)
@pytest.mark.parametrize("severity", ["normal", "warning", "critical"])
@pytest.mark.parametrize("variant", variants_of("nostromo"), ids=lambda v: v.id)
def test_the_nostromo_icon_keeps_its_arc_readable_on_both_taskbars(variant, severity, size):
    """The icon carries its own opaque plate, so whatever is behind it does not reach the ring: the lit arc
    reads against the empty track at 3:1 on a dark taskbar and on a light one alike."""
    theme, skin = theme_of("nostromo"), get_skin("nostromo")
    icon = skin.render_tray_icon(nostromo_models(severity), theme, variant, size)
    lit, track = ring_colours(icon, size)
    assert len(lit) == 1 and len(track) == 1, (lit, track)
    (lit_colour,), (track_colour,) = lit, track
    assert lit_colour[3] == 255 and track_colour[3] == 255, "the ring is opaque"
    for name, taskbar in TASKBARS.items():
        behind = Image.new("RGBA", icon.size, taskbar)
        behind.alpha_composite(icon)
        seen_lit = behind.getpixel(next(xy for xy in _xy(icon) if icon.getpixel(xy) == lit_colour))
        seen_track = behind.getpixel(next(xy for xy in _xy(icon) if icon.getpixel(xy) == track_colour))
        assert contrast(seen_lit, seen_track) >= 3.0, (name, variant.id, severity, size)


def _xy(image: Image.Image):
    return ((x, y) for y in range(image.height) for x in range(image.width))


@pytest.mark.parametrize("size", TRAY_SIZES)
@pytest.mark.parametrize("variant", variants_of("nostromo"), ids=lambda v: v.id)
def test_the_nostromo_icon_is_one_opaque_plate_whatever_is_behind_it(variant, size):
    icon = get_skin("nostromo").render_tray_icon(dump.sample_model(LIVE), theme_of("nostromo"), variant, size)
    alphas = {px[3] for px in icon.getdata()}
    assert alphas == {0, 255}, "every pixel is either clear (the cut corners) or fully opaque"
    dark = Image.new("RGBA", icon.size, TASKBARS["dark"])
    light = Image.new("RGBA", icon.size, TASKBARS["light"])
    dark.alpha_composite(icon)
    light.alpha_composite(icon)
    clear = icon.getchannel(3).point(lambda v: 255 if v == 0 else 0)
    moved = ImageChops.difference(dark.convert("RGB"), light.convert("RGB")).convert("L").point(lambda v: 255 if v else 0)
    assert ImageChops.difference(moved, clear).getbbox() is None, "only the cut corners let the taskbar through"
    assert icon.getpixel((size // 2, size // 2))[3] == 255


@pytest.mark.parametrize("variant", variants_of("nostromo"), ids=lambda v: v.id)
def test_the_nostromo_plate_is_dark_and_comes_from_the_variants_tokens(variant):
    theme = theme_of("nostromo")
    model = dump.sample_model(LIVE)
    pal = nostromo._palette(theme, variant, model)
    icon = get_skin("nostromo").render_tray_icon(model, theme, variant, 32)
    inside = icon.getpixel((29, 2))                            # the plate's top right, outside the ring
    assert inside[:3] == tuple(pal.crt[:3])
    assert colour.contrast_ratio(hexof(pal.crt), TASKBARS["light"]) >= 8.0, "the plate stands off a light taskbar"


# ── Night City: the icon on both taskbars ─────────────────────────────────────

@pytest.mark.parametrize("size", (16, 24))
@pytest.mark.parametrize("variant", variants_of("night_city"), ids=lambda v: v.id)
def test_the_night_city_icon_reads_on_both_taskbars(variant, size):
    theme = theme_of("night_city")
    pal = night_city.Palette.for_variant(theme, variant, plate=False)
    for name, taskbar in TASKBARS.items():
        assert max(contrast(pal.bg, taskbar), contrast(pal.line, taskbar)) >= 3.0, (variant.id, name)
    for lit in (pal.bar, pal.warn, pal.danger, pal.stale):
        assert contrast(lit, pal.bg) >= 4.5, (variant.id, hexof(lit))
    for state in ALL_STATES:
        icon = tray("night_city", variant, state, size)
        assert {px[3] for px in icon.getdata()} == {0, 255}, "opaque inside, clear in the corners"


@pytest.mark.parametrize("size", (16, 20, 24, 32))
@pytest.mark.parametrize("variant_id", ["relic", "mox"])
def test_the_relic_and_mox_marks_use_the_stale_colour_and_stand_off_the_plate(variant_id, size):
    theme = theme_of("night_city")
    variant = theme.variant(variant_id)
    pal = night_city.Palette.for_variant(theme, variant, plate=False)
    for state in (OFFLINE, LIMITED):
        icon = tray("night_city", variant, state, size)
        marks = [px for px in icon.getdata() if px[:3] == tuple(pal.stale[:3])]
        assert len(marks) >= size // 2, "the cross or hourglass is drawn in the stale colour"
        assert contrast(pal.stale, pal.bg) >= 4.5
