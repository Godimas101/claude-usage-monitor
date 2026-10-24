"""Tests for the Night City skin (usage_monitor/render/skins/night_city.py).

Everything renders to memory or to pytest's tmp_path, so nearly all of it needs no desktop and runs in CI. The
two tests that hand frames to real (off-screen) windows and icons at the end are skipped unless CUM_UI_TESTS=1.

Pixel checks sample flat shapes (cells, plates, rings, the frame) and compare them with the variant's tokens, so
they hold whatever the text looks like. Where a test needs text it compares two renders instead.
"""
from __future__ import annotations

import logging
import os
import re
import sys
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageChops

from usage_monitor.data.models import ApiStatus
from usage_monitor.render import dump, engine
from usage_monitor.render.model import Row, WidgetModel
from usage_monitor.render.skins import RenderOptions, get_skin
from usage_monitor.render.skins import night_city as nc
from usage_monitor.render.skins.night_city import NightCitySkin, Palette

UI = pytest.mark.skipif(
    os.environ.get("CUM_UI_TESTS") != "1", reason="needs an interactive desktop: set CUM_UI_TESTS=1"
)
VARIANTS = ("blackwall", "relic", "edgerun", "mox")
SCALES = (1.0, 1.25, 1.5, 2.0)
ALL_STATES = dump.STATE_NAMES
FLOAT_HEIGHTS = {
    "normal3": 126, "normal2": 96, "warning": 126, "critical": 126, "stale": 96,
    "offline": 96, "paused": 96, "rate_limited": 96, "no_credentials": 96, "loading": 96,
}
TOOLTIP_HEIGHTS = {2: 132, 3: 182}
NOW = datetime(2026, 10, 21, 16, 12, tzinfo=timezone.utc)


# ── Helpers ───────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def theme() -> Any:
    return dump.load_theme_objects()["night_city"]


def variant(variant_id: str) -> Any:
    return theme().variant(variant_id)


def skin() -> NightCitySkin:
    result = get_skin("night_city")
    assert isinstance(result, NightCitySkin)
    return result


def palette(variant_id: str, *, plate: bool = True) -> Palette:
    return Palette.for_variant(theme(), variant(variant_id), plate=plate)


def row(key: str, label: str, percent: float | None, *, fraction: float | None = 0.31, severity: str = "normal",
        reset: str | None = "7:39 PM", pace: str | None = "behind", share: str | None = None) -> Row:
    return Row(key, label, percent, fraction, severity, None, reset, pace, share)


def model(rows: list[Row], status: ApiStatus = ApiStatus.LIVE, *, age: str | None = "12 S AGO",
          stale: bool | None = None, blink_on: bool = True) -> WidgetModel:
    texts = {ApiStatus.LIVE: "LIVE", ApiStatus.STALE: "STALE", ApiStatus.OFFLINE: "OFFLINE",
             ApiStatus.PAUSED: "PAUSED", ApiStatus.LOADING: "LOADING", ApiStatus.RATE_LIMITED: "RATE LIMITED",
             ApiStatus.NO_CREDENTIALS: "NO LOGIN"}
    return WidgetModel(
        status=status, status_text=texts[status], age_text=age, rows=rows,
        stale=(status not in (ApiStatus.LIVE, ApiStatus.PAUSED)) if stale is None else stale,
        stale_message=None, blink_on=blink_on, updated_at=NOW,
    )


def sample_rows(session: float = 13.0, weekly: float = 2.0, fable: float | None = 0.0,
                severities: tuple[str, str, str] = ("normal", "normal", "normal")) -> list[Row]:
    rows = [row("session", "SESSION", session, severity=severities[0]),
            row("weekly", "WEEKLY", weekly, fraction=0.33, severity=severities[1], reset="Mon 9:00 AM")]
    if fable is not None:
        rows.append(row("scoped:fable", "FABLE", fable, fraction=0.33, severity=severities[2], reset=None,
                        share="50% SHARE"))
    return rows


def rgb_at(image: Image.Image, x: float, y: float, scale: float) -> tuple[int, int, int]:
    """The colour at a logical coordinate of a frame rendered at ``scale``."""
    return image.getpixel((int(x * scale), int(y * scale)))[:3]


def rgb(color: tuple[int, ...]) -> tuple[int, int, int]:
    return color[0], color[1], color[2]


def near(actual: tuple[int, ...], expected: tuple[int, ...], tolerance: int = 2) -> bool:
    return all(abs(a - e) <= tolerance for a, e in zip(actual[:3], expected[:3]))


def changed_box(a: Image.Image, b: Image.Image) -> tuple[int, int, int, int] | None:
    """The box around every pixel that differs in any channel (getbbox() on RGBA would look at alpha only)."""
    red, green, blue, alpha = ImageChops.difference(a.convert("RGBA"), b.convert("RGBA")).split()
    return ImageChops.lighter(ImageChops.lighter(red, green), ImageChops.lighter(blue, alpha)).getbbox()


def strip_rgb(image: Image.Image, x: float, y: float, scale: float) -> tuple[int, int, int]:
    """The colour at a strip coordinate (logical px) of a taskbar image that centres the strip vertically."""
    offset = (image.height - round(nc.STRIP_H * scale)) // 2
    return image.getpixel((int(x * scale), int(y * scale) + offset))[:3]


def render(model_: WidgetModel, variant_id: str = "blackwall", scale: float = 2.0, **opts: Any) -> Image.Image:
    return skin().render_floating(model_, theme(), variant(variant_id), scale, RenderOptions(**opts))


def themed(**colors: str) -> Any:
    """The blackwall variant with some colour roles replaced."""
    base = variant("blackwall")
    return replace(base, colors={**base.colors, **colors})


def with_extras(**tokens: str) -> Any:
    base = variant("blackwall")
    return replace(base, extras={**base.extras, **tokens})


def ink_for(scale: float = 1.0, variant_id: str = "blackwall") -> nc._Ink:
    cv = nc._canvas(nc.FLOAT_W, 126, scale, theme())
    return nc._Ink(cv, palette(variant_id), RenderOptions())


# ── Registry and palette ──────────────────────────────────────────────────────

def test_the_registry_builds_the_night_city_skin():
    assert isinstance(get_skin("night_city"), NightCitySkin)
    assert NightCitySkin.theme_id == "night_city"


def test_shipped_variants_are_the_four_night_city_variants():
    assert [v.id for v in theme().variants] == list(VARIANTS)
    assert theme().default_variant.id == "blackwall"


@pytest.mark.parametrize("variant_id", VARIANTS)
def test_palette_tokens_come_from_the_variant(variant_id):
    pal = palette(variant_id, plate=False)
    colors = variant(variant_id).colors
    tokens = variant(variant_id).extras
    assert pal.bg == engine.parse_color(colors["bg"])
    assert pal.panel == engine.parse_color(colors["surface"])
    assert pal.fg == engine.parse_color(colors["text"])
    assert pal.fg_hi == engine.parse_color(colors["text_bright"])
    assert pal.ink2 == engine.parse_color(colors["text_dim"])
    assert pal.line_a == engine.parse_color(colors["border"])
    assert pal.accent == engine.parse_color(colors["accent"])
    assert pal.ok == engine.parse_color(colors["ok"])
    assert pal.warn == engine.parse_color(colors["warn"])
    assert pal.danger == engine.parse_color(colors["alert"])
    assert pal.track == engine.parse_color(colors["track"])
    assert pal.glow == engine.parse_color(colors["glow"])
    assert pal.stale == engine.parse_color(colors["stale"])
    assert pal.ink3 == engine.parse_color(tokens.get("ink-3", colors["stale"]))      # the tertiary text, not the stale tone
    assert pal.bar == engine.parse_color(tokens["bar"])
    assert pal.line == engine.parse_color(tokens["line"])
    assert pal.ink == engine.parse_color(tokens["ink"])
    assert pal.hz_a == engine.parse_color(tokens["hz-a"])
    assert pal.hz_b == engine.parse_color(tokens["hz-b"])
    assert pal.ring == engine.parse_color(tokens["ring"])
    assert pal.scan == engine.parse_color(tokens["scan"])
    assert not pal.plate


def test_only_edgerun_has_a_plate_and_it_overrides_the_panel_and_text():
    assert [palette(v).plate for v in VARIANTS] == [False, False, True, False]
    plate = palette("edgerun")
    tokens = theme().extras["edgerun_plate"]["tokens"]
    assert plate.panel == engine.parse_color(tokens["panel"])
    assert plate.fg == plate.fg_hi == plate.accent == plate.bar == engine.parse_color(tokens["fg"])
    assert plate.track == engine.parse_color(tokens["track"])
    assert plate.warn == engine.parse_color(tokens["warn"])
    assert plate.glow[3] == 0 and plate.glow_accent[3] == 0 and plate.glow_danger[3] == 0
    assert plate.crit_fill == (plate.danger, None)             # solid red, no stripes
    assert plate.value_warn == plate.value_crit == plate.ink    # red text on the yellow is not used
    dark = palette("edgerun", plate=False)
    assert dark.panel == engine.parse_color(variant("edgerun").colors["surface"]) and not dark.plate


def test_stale_tones_dim_the_text_and_keep_the_plate_ink():
    pal = palette("blackwall")
    stale = pal.stale_tones()
    assert stale.fg == stale.fg_hi == stale.ink2 == stale.accent == stale.bar == stale.ok == pal.stale
    assert stale.glow[3] == 0 and stale.glow_accent[3] == 0 and stale.glow_danger[3] == 0
    assert stale.line == pal.line and stale.warn == pal.warn            # chrome and the band keep their colours
    plate = palette("edgerun")
    flat = plate.stale_tones()
    assert flat.fg == plate.fg and flat.bar == plate.bar                # the approved plate stays ink
    assert flat.ok == plate.stale and flat.glow[3] == 0


def test_a_variant_missing_a_colour_role_is_reported():
    broken = replace(variant("relic"), colors={k: v for k, v in variant("relic").colors.items() if k != "track"})
    with pytest.raises(KeyError):
        Palette.for_variant(theme(), broken)


def test_variant_tokens_are_read_when_the_variant_has_no_extras():
    bare = replace(variant("blackwall"), extras={})
    pal = Palette.for_variant(theme(), bare)
    assert pal.bar == engine.parse_color(theme().extras["token_extras"]["blackwall"]["bar"])
    assert pal.ink == engine.parse_color(theme().extras["variant_tokens"]["blackwall"]["ink"])


def test_each_variant_has_its_look():
    assert nc.look_for(variant("blackwall")) == nc.Look(segmented=True, boxed=True)
    assert nc.look_for(variant("edgerun")) == nc.Look(segmented=True, boxed=False)
    assert nc.look_for(variant("relic")) == nc.look_for(variant("mox")) == nc.Look(segmented=False, boxed=False)


def test_the_skin_source_holds_no_colour_literals():
    source = Path(nc.__file__).read_text(encoding="utf-8")
    assert re.findall(r"#[0-9a-fA-F]{3,8}\b", source) == []              # every colour is a theme token


def luminance(color: tuple[int, ...]) -> float:
    def channel(value: int) -> float:
        v = value / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * channel(color[0]) + 0.7152 * channel(color[1]) + 0.0722 * channel(color[2])


def contrast(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


@pytest.mark.parametrize("variant_id", VARIANTS)
def test_every_text_token_keeps_wcag_aa_contrast_on_what_it_is_drawn_on(variant_id):
    """Text sits on the panel, on the ink band (stale states) and, on the Edgerun plate, on the black LIVE chip."""
    pal = palette(variant_id)
    on_panel = {"fg": pal.fg, "fg_hi": pal.fg_hi, "ink2": pal.ink2, "stale": pal.ink3,
                "warn value": pal.value_warn, "critical value": pal.value_crit}
    on_ink = {"band cause": pal.warn, "band age": pal.signal if pal.plate else pal.ink2, "stale word": pal.stale}
    if pal.plate:
        on_ink["live word"] = pal.ok
    else:
        on_panel["live word"] = pal.ok
    for name, color in on_panel.items():
        assert contrast(color, pal.panel) >= 4.5, (variant_id, name)
    for name, color in on_ink.items():
        assert contrast(color, pal.ink) >= 4.5, (variant_id, name)


# ── Floating widget: every variant x state x scale ───────────────────────────

@pytest.mark.parametrize("variant_id", VARIANTS)
def test_every_state_renders_at_every_scale_with_real_alpha(variant_id):
    for name in ALL_STATES:
        sample = dump.sample_model(name)
        for scale in SCALES:
            image = skin().render_floating(sample, theme(), variant(variant_id), scale, None)
            width, height = round(264 * scale), round(FLOAT_HEIGHTS[name] * scale)
            assert image.mode == "RGBA"
            assert image.size == (width, height), (name, scale)
            assert skin().floating_size(theme(), sample, scale) == image.size
            assert image.getchannel("A").getextrema() == (0, 255), (name, scale)
            assert image.getpixel((0, 0))[3] == 0                          # chamfered top-left
            assert image.getpixel((width - 1, height - 1))[3] == 0         # chamfered bottom-right
            assert image.getpixel((width // 2, 0))[3] > 0                  # the 1 px frame
            assert image.getpixel((width // 2, height // 2))[3] == 255     # the panel


@pytest.mark.parametrize("scale", [0.75, 1.1, 3.0])
def test_unusual_scales_still_render(scale):
    sample = dump.sample_model("critical")
    for variant_id in VARIANTS:
        assert skin().render_floating(sample, theme(), variant(variant_id), scale, None).size == (
            round(264 * scale), round(126 * scale))
        assert skin().render_tooltip(sample, theme(), variant(variant_id), scale).width == round(264 * scale)


def test_frame_height_is_96_for_two_rows_and_126_for_three_and_grows_by_30():
    for count, height in ((2, 96), (3, 126), (4, 156), (5, 186)):
        rows = sample_rows(fable=None) + [row(f"scoped:m{i}", f"M{i}", 5.0, share="25% SHARE", reset=None)
                                          for i in range(count - 2)]
        assert skin().render_floating(model(rows), theme(), variant("relic"), 1.0, None).size == (264, height)


def test_the_frame_is_the_line_colour_at_its_alpha_and_the_corners_are_clear():
    image = render(model(sample_rows()), scale=1.0)
    pal = palette("blackwall")
    for point in ((132, 0), (0, 60), (263, 5), (132, 125)):
        pixel = image.getpixel(point)
        assert pixel[3] == pal.line_a[3] and near(pixel, pal.line_a), point         # straight alpha, +-1 for rounding
    assert image.getpixel((3, 3))[3] == 0 and image.getpixel((6, 6))[3] > 0         # the 8 px chamfer, top-left
    assert image.getpixel((260, 122))[3] == 0 and image.getpixel((257, 119))[3] > 0   # and bottom-right
    assert image.getpixel((263, 0))[3] > 0 and image.getpixel((0, 125))[3] > 0      # the other corners are square


@pytest.mark.parametrize("variant_id", VARIANTS)
def test_rendering_is_deterministic(variant_id):
    sample = model(sample_rows(72, 41, 3, ("warning", "normal", "normal")))
    first = render(sample, variant_id, 1.25)
    second = skin().render_floating(sample, theme(), variant(variant_id), 1.25, None)
    assert first.tobytes() == second.tobytes()


def test_blackwall_and_edgerun_draw_cells_and_relic_and_mox_draw_a_slanted_bar():
    sample = model(sample_rows(50, 50, 50))
    track = {v: palette(v).track for v in VARIANTS}
    for variant_id in VARIANTS:
        image = render(sample, variant_id, 1.0)
        # The gap between two cells shows the panel: only a cell bar has one, at x 21 and 22 of row 1.
        gap = image.getpixel((22, 28 + 22))[:3]
        panel = rgb(palette(variant_id).panel)
        if variant_id in ("blackwall", "edgerun"):
            assert near(gap, panel, 12), variant_id
            assert near(rgb_at(image, 240, 28 + 22, 1.0), rgb(track[variant_id]), 4)
        else:
            assert near(gap, rgb(palette(variant_id).bar), 4), variant_id    # inside the 50 % fill


# ── Colour sampling: tokens are what gets drawn ──────────────────────────────

def test_the_panel_is_the_surface_token_and_scanlines_darken_every_third_row():
    sample = model(sample_rows())
    image = render(sample, scale=2.0)
    pal = palette("blackwall")
    assert rgb_at(image, 150, 24, 2.0) == rgb(pal.panel)                           # empty panel, no scan row
    scan_row = rgb_at(image, 150, 25, 2.0)                                         # y = 1 mod 3 darkens
    assert near(scan_row, tuple(round(c * (1 - pal.scan[3] / 255)) for c in pal.panel[:3]), 2)
    other = skin().render_floating(sample, theme(), themed(surface="#123456"), 2.0, None)
    assert rgb_at(other, 150, 24, 2.0) == (0x12, 0x34, 0x56)


def test_text_fx_off_is_exactly_no_scanlines_and_no_glow():
    sample = model(sample_rows(93, 58, 0, ("critical", "normal", "normal")))
    flat = render(sample, text_fx=False)
    assert rgb_at(flat, 150, 25, 2.0) == rgb(palette("blackwall").panel)          # the scan row stays the panel
    fx = render(sample, text_fx=True)
    assert fx.tobytes() != flat.tobytes()
    # Effects on with every effect token clear is the same picture as effects off: nothing else changes.
    base = variant("blackwall")
    clear = "#00000000"
    quiet = replace(base, colors={**base.colors, "glow": clear},
                    extras={**base.extras, "scan": clear, "glow-accent": clear, "glow-danger": clear})
    assert skin().render_floating(sample, theme(), quiet, 2.0, RenderOptions(text_fx=True)).tobytes() == flat.tobytes()


def test_lit_cells_unlit_cells_and_the_warn_fill_are_the_variant_tokens():
    pal = palette("blackwall")
    calm = render(model(sample_rows()))
    assert rgb_at(calm, 17.5, 51, 2.0) == rgb(pal.bar)                            # first lit cell of the session bar
    assert rgb_at(calm, 242.5, 51, 2.0) == rgb(pal.track)                         # last, unlit cell
    warning = render(model(sample_rows(72, 41, 0, ("warning", "normal", "normal"))))
    assert rgb_at(warning, 17.5, 51, 2.0) == rgb(pal.warn)
    assert rgb_at(warning, 17.5, 81, 2.0) == rgb(pal.bar)                         # weekly stays calm
    recolored = skin().render_floating(model(sample_rows(72, 41, 0, ("warning", "normal", "normal"))), theme(),
                                       themed(warn="#00ff00"), 2.0, None)
    assert rgb_at(recolored, 17.5, 51, 2.0) == (0, 255, 0)


def test_changing_the_bar_and_track_tokens_changes_those_pixels():
    sample = model(sample_rows())
    base = render(sample)
    swapped = skin().render_floating(sample, theme(), with_extras(bar="#00ff00"), 2.0, None)
    assert rgb_at(swapped, 17.5, 51, 2.0) == (0, 255, 0)
    assert rgb_at(base, 17.5, 51, 2.0) != (0, 255, 0)
    track = skin().render_floating(sample, theme(), themed(track="#101010"), 2.0, None)
    assert rgb_at(track, 242.5, 51, 2.0) == (16, 16, 16)


def test_the_slanted_bar_of_relic_and_mox_uses_the_bar_token():
    sample = model(sample_rows(50, 50, 50))
    for variant_id in ("relic", "mox"):
        image = render(sample, variant_id)
        assert rgb_at(image, 30, 51, 2.0) == rgb(palette(variant_id).bar)
        assert rgb_at(image, 200, 51, 2.0) == rgb(palette(variant_id).track)


def test_a_critical_fill_is_hazard_stripes_in_the_two_hazard_tokens():
    sample = model(sample_rows(93, 58, 0, ("critical", "normal", "normal")))
    for variant_id in ("blackwall", "relic", "mox"):
        pal = palette(variant_id)
        cell = render(sample, variant_id).crop((28, 94, 42, 110))                  # the first cell of the session bar
        colours = {rgb(c) for _, c in cell.getcolors(maxcolors=4096)}
        assert rgb(pal.hz_a) in colours and rgb(pal.hz_b) in colours, variant_id
    plate = render(sample, "edgerun").crop((28, 94, 42, 110))
    assert {rgb(c) for _, c in plate.getcolors(maxcolors=4096)} == {rgb(palette("edgerun").danger)}


def test_the_critical_value_is_the_alert_colour_and_warn_is_the_warn_colour():
    pal = palette("blackwall")
    crit = render(model(sample_rows(93, 58, 0, ("critical", "normal", "normal"))))
    warn = render(model(sample_rows(72, 41, 0, ("warning", "normal", "normal"))))

    def has_colour(image: Image.Image, colour: tuple[int, ...]) -> bool:
        value = image.crop((440, 54, 500, 86))                                     # the session numerals
        return any(near(c, colour, 6) for _, c in value.getcolors(maxcolors=100000))

    assert has_colour(crit, pal.danger) and not has_colour(warn, pal.danger)
    assert has_colour(warn, pal.warn) and not has_colour(crit, pal.warn)


def test_stale_rows_use_the_stale_tone_for_cells_and_the_band_carries_the_state():
    pal = palette("blackwall")
    live = render(dump.sample_model("normal3"))
    stale = render(dump.sample_model("stale"))
    assert rgb_at(live, 17.5, 51, 2.0) == rgb(pal.bar)
    assert rgb_at(stale, 17.5, 51, 2.0) == rgb(pal.stale)                          # no bright cyan in a stale widget
    band = stale.crop((24, 12, 496, 44))
    colours = {rgb(c) for _, c in band.getcolors(maxcolors=100000)}
    assert rgb(pal.hz_a) in colours and rgb(pal.hz_b) in colours                   # the hazard strips
    assert rgb(pal.warn) in colours                                                # outline and cause text
    assert rgb_at(stale, 12.5, 14, 2.0) == rgb(pal.warn)                           # left edge of the outline


@pytest.mark.parametrize("state", ["stale", "offline", "rate_limited", "no_credentials"])
def test_band_states_replace_the_header(state):
    image = render(dump.sample_model(state))
    pal = palette("blackwall")
    band = image.crop((24, 12, 496, 44))
    assert rgb(pal.warn) in {rgb(c) for _, c in band.getcolors(maxcolors=100000)}
    # The band is an ink box over the header, so there is no live pip where it would sit.
    assert near(rgb_at(image, 16, 14, 2.0), rgb(pal.ink), 2)
    assert not near(rgb_at(image, 16, 14, 2.0), rgb(pal.ok), 40)


def test_the_edgerun_plate_is_yellow_with_ink_cells_and_follows_its_tokens():
    sample = model(sample_rows())
    image = render(sample, "edgerun")
    tokens = theme().extras["edgerun_plate"]["tokens"]
    assert rgb_at(image, 150, 24, 2.0) == tuple(int(tokens["panel"][i:i + 2], 16) for i in (1, 3, 5))
    assert rgb_at(image, 17.5, 51, 2.0) == tuple(int(tokens["bar"][i:i + 2], 16) for i in (1, 3, 5))
    repainted = replace(theme(), extras={**theme().extras, "edgerun_plate": {
        **theme().extras["edgerun_plate"], "tokens": {**tokens, "panel": "#102030", "bar": "#aa0000"}}})
    other = skin().render_floating(sample, repainted, variant("edgerun"), 2.0, None)
    assert rgb_at(other, 150, 24, 2.0) == (0x10, 0x20, 0x30)
    assert rgb_at(other, 17.5, 51, 2.0) == (0xAA, 0, 0)


def test_the_plate_header_is_a_black_chip_with_the_live_dot_in_the_ok_colour():
    image = render(model(sample_rows()), "edgerun")
    pal = palette("edgerun")
    assert rgb_at(image, 208, 16, 2.0) == rgb(pal.ink)                                 # chip, left of the dot
    assert rgb_at(image, 213.5, 14, 2.0) == rgb(pal.ok)                                # the dot
    assert near(rgb_at(image, 120, 14, 2.0), rgb(pal.panel), 3)                        # no pip on the plate


def test_edgerun_without_its_plate_keeps_its_dark_theme_text():
    sample = model(sample_rows())
    image = render(sample, "edgerun", transparent_bg=True)
    dark = palette("edgerun", plate=False)
    assert image.getpixel((264, 120))[3] == 0                                           # no plate
    assert rgb_at(image, 17.5, 51, 2.0) == rgb(dark.bar)                                # yellow cells, not ink ones


def test_the_numeral_glow_follows_the_glow_token_and_stays_beside_the_numerals():
    sample = model(sample_rows())
    base = render(sample)
    recoloured = skin().render_floating(sample, theme(), themed(glow="#00ff0073"), 2.0, None)
    box = changed_box(base, recoloured)
    assert box is not None
    # Only the halo behind the three numerals uses the token: the right-hand side, rows 18..110.
    assert box[0] >= 2 * 210 and box[2] <= 2 * 262 and box[1] >= 2 * 18 and box[3] <= 2 * 110


def test_the_alive_pip_glows_in_the_accent_glow_token():
    sample = model(sample_rows())
    base = render(sample)
    green = skin().render_floating(sample, theme(), with_extras(**{"glow-accent": "#00ff00ff"}), 2.0, None)
    box = changed_box(base, green)
    assert box is not None and box[2] <= 2 * 32 and box[3] <= 2 * 28                     # around the pip, nothing else
    assert green.getpixel((25, 28))[1] > base.getpixel((25, 28))[1]                      # beside it the halo is greener


# ── Options ───────────────────────────────────────────────────────────────────

def test_transparent_background_removes_the_plate_and_keeps_the_frame():
    sample = model(sample_rows())
    solid = render(sample, scale=1.0)
    clear = render(sample, scale=1.0, transparent_bg=True)
    assert clear.getpixel((150, 24))[3] == 0                                           # nothing behind the empty panel
    assert clear.getpixel((132, 0)) == solid.getpixel((132, 0))                        # the frame is still drawn
    assert clear.getpixel((0, 0))[3] == 0
    def covered(image: Image.Image) -> int:
        return sum(image.getchannel("A").histogram()[129:])                            # pixels over 50 % opaque

    assert covered(clear) < covered(solid) * 0.5


def test_transparent_text_gets_a_dark_backing_so_it_reads_on_a_light_desktop():
    sample = model(sample_rows())
    image = render(sample, scale=2.0, transparent_bg=True)
    backing = [p for p in image.crop((54, 54, 160, 80)).getdata() if p[3] >= 150 and sum(p[:3]) < 120]
    assert len(backing) > 40                                                           # around the "SESSION" glyphs


def test_reduce_motion_pins_the_pulse_and_the_refresh_hit():
    sample = model(sample_rows())
    steady = render(sample)
    pulsed = render(sample, phase=nc.animation_phase(0.5))
    hit = render(sample, phase=nc.animation_phase(0.0, 1))
    settled = render(sample, phase=nc.animation_phase(0.0, 3))
    assert pulsed.tobytes() != steady.tobytes()
    assert hit.tobytes() != steady.tobytes()
    assert settled.tobytes() == steady.tobytes()                                        # frame 3 is the settled frame
    for phase in (nc.animation_phase(0.5), nc.animation_phase(0.0, 1), nc.animation_phase(0.7, 2)):
        pinned = render(sample, reduce_motion=True, phase=phase)
        assert pinned.tobytes() == steady.tobytes()


def test_the_pulse_dims_the_pip_and_the_hit_splits_the_numerals_by_two_then_one_pixel():
    sample = model(sample_rows())
    steady = render(sample)
    dim = changed_box(steady, render(sample, phase=nc.animation_phase(0.5)))
    assert dim is not None and dim[2] <= 2 * 32 and dim[3] <= 2 * 28                    # the pip and its halo only
    first = changed_box(steady, render(sample, phase=nc.animation_phase(0.0, 1)))
    second = changed_box(steady, render(sample, phase=nc.animation_phase(0.0, 2)))
    for box in (first, second):
        assert box is not None and box[0] >= 2 * 218 and box[2] <= 2 * 250 + 8          # only around the numerals
    assert first[2] - first[0] > second[2] - second[0]                                  # 2 px apart, then 1 px


def test_a_dark_blink_phase_drops_to_the_pulse_minimum_unless_motion_is_reduced():
    lit = render(model(sample_rows(), blink_on=True))
    dark = render(model(sample_rows(), blink_on=False))
    pinned = render(model(sample_rows(), blink_on=False), reduce_motion=True)
    box = changed_box(lit, dark)
    assert box is not None and box[2] <= 2 * 32 and box[3] <= 2 * 28                    # only the pip dims
    assert pinned.tobytes() == lit.tobytes()


def test_the_pulse_is_eased_between_full_and_a_third():
    assert nc.pulse_alpha(0.0) == pytest.approx(1.0)
    assert nc.pulse_alpha(0.5) == pytest.approx(0.3)
    assert nc.pulse_alpha(0.25) == pytest.approx(0.65)
    assert nc.pulse_alpha(1.25) == pytest.approx(nc.pulse_alpha(0.25))
    assert nc.animation_phase(0.25, 2) == pytest.approx(2.25)
    assert nc.animation_phase(1.25) == pytest.approx(0.25)


def test_option_mappings_are_accepted_and_unknown_names_are_rejected():
    sample = model(sample_rows())
    first = skin().render_floating(sample, theme(), variant("relic"), 1.0, {"text_fx": False})
    assert first.size == (264, 126)
    with pytest.raises(ValueError):
        skin().render_floating(sample, theme(), variant("relic"), 1.0, {"glitch": True})


@pytest.mark.parametrize("variant_id", VARIANTS)
def test_every_option_combination_renders_floating_and_strip(variant_id):
    for name in ("normal3", "critical", "stale", "rate_limited"):
        sample = dump.sample_model(name)
        for transparent in (False, True):
            for fx in (False, True):
                for reduce in (False, True):
                    opts = RenderOptions(transparent_bg=transparent, text_fx=fx, reduce_motion=reduce, phase=1.4)
                    floating = skin().render_floating(sample, theme(), variant(variant_id), 1.5, opts)
                    assert floating.size == (396, round(FLOAT_HEIGHTS[name] * 1.5))
                    assert skin().render_taskbar(sample, theme(), variant(variant_id), 60, 1.5, opts).size == (360, 60)


# ── Layout: nothing overlaps or leaves the frame ──────────────────────────────

LAYOUT_ROWS = [
    row("session", "SESSION", 13.0, severity="normal"),
    row("session", "SESSION", 100.0, severity="critical"),
    row("session", "SESSION", 112.4, severity="critical", reset="Wed 12:30 PM"),
    row("weekly", "WEEKLY", 100.0, severity="critical", reset="Wed 12:30 PM"),
    row("weekly", "WEEKLY", 80.0, severity="warning", reset="Mon 9:00 AM"),
    row("weekly", "WEEKLY", 1000.0, severity="critical", reset="Wed 12:30 PM"),
    row("scoped:fable", "FABLE WEEKLY", 50.0, reset=None, share="50% SHARE"),
    row("scoped:long", "CLAUDE DESIGN WEEKLY", 100.0, severity="critical", reset=None, share="100% SHARE"),
    row("scoped:longer", "A VERY LONG MODEL NAME WEEKLY", 99.0, severity="critical", reset=None, share="100% SHARE"),
    row("weekly", "WEEKLY", None, fraction=None, reset=None),
]


@pytest.mark.parametrize("variant_id", VARIANTS)
@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5, 2.0])
@pytest.mark.parametrize("index", range(len(LAYOUT_ROWS)))
def test_row_parts_never_overlap_and_stay_inside_the_frame(variant_id, scale, index):
    target = LAYOUT_ROWS[index]
    ink = ink_for(scale, variant_id)
    look = nc.look_for(variant(variant_id))
    emphasis = target.severity
    digits = nc._digits(target.percent)
    boxes = nc._layout_row(ink, look, target, emphasis, digits, nc._reset_text(target, False))
    parts = [boxes.name]
    for part in (boxes.tag, boxes.chip, boxes.reset):
        if part is not None:
            parts.append(part)
    parts.append(boxes.value)
    parts.sort()
    for left, right in zip(parts, parts[1:]):
        assert left[1] + 4 <= right[0] + 0.01, (variant_id, scale, index, boxes)    # at least 4 px of air
    assert boxes.name[0] >= nc.LEFT and boxes.value[1] <= nc.RIGHT + 0.01
    assert boxes.value[0] >= boxes.name[1] + 4 - 0.01


def test_a_long_name_is_cut_with_dots_and_the_value_and_chip_stay():
    ink = ink_for()
    target = LAYOUT_ROWS[8]
    boxes = nc._layout_row(ink, nc.look_for(variant("blackwall")), target, "critical", "99", None)
    assert boxes.tag is None and boxes.chip is not None                               # the tag went, the chip stayed
    cut = nc._fit(ink, nc._row_name(target), nc.NAME, boxes.name[1] - boxes.name[0])
    assert cut.endswith("...") and ink.width(cut, nc.NAME) <= boxes.name[1] - boxes.name[0] + 0.01


def test_the_reset_text_is_dropped_before_the_name_is_touched():
    ink = ink_for()
    look = nc.look_for(variant("blackwall"))
    tight = LAYOUT_ROWS[3]                                                           # WEEKLY 100 % with a CRIT chip
    roomy = LAYOUT_ROWS[0]
    squeezed = nc._layout_row(ink, look, tight, "critical", "100", "RST WED 12:30 PM")
    assert squeezed.reset is None
    assert squeezed.name == (nc.LEFT, nc.LEFT + ink.width("WEEKLY", nc.NAME))          # the name is whole
    assert nc._layout_row(ink, look, roomy, "normal", "13", "RST 7:39 PM").reset is not None


@pytest.mark.parametrize("variant_id", VARIANTS)
def test_extreme_rows_stay_inside_the_content_box(variant_id):
    extreme = model([row("session", "SESSION", 1000.0, severity="critical", reset="Wed 12:30 PM"),
                     row("weekly", "WEEKLY", -5.0),
                     row("scoped:x", "A VERY LONG MODEL NAME WEEKLY", 100.0, severity="critical", reset=None,
                         share="100% SHARE")])
    pal = palette(variant_id)
    for scale in SCALES:
        image = render(extreme, variant_id, scale, text_fx=False)
        assert image.size == (round(264 * scale), round(126 * scale))
        # Nothing is drawn right of x = 250 below the header, where only the bracket and the frame live.
        margin = [image.getpixel((x, y)) for x in range(round(252 * scale), round(262 * scale))
                  for y in range(round(20 * scale), round(108 * scale))]
        assert all(is_panel(p, pal) for p in margin), (variant_id, scale)


def test_non_finite_numbers_are_drawn_as_missing_instead_of_failing():
    base = dump.sample_model("normal3")
    bad = replace(base, rows=[replace(base.rows[0], percent=float("nan"), time_fraction=float("inf")),
                              replace(base.rows[1], percent=float("inf"))] + list(base.rows[2:]))
    assert skin().render_floating(bad, theme(), variant("blackwall"), 1.5, None).size == (396, 189)
    assert skin().render_tooltip(bad, theme(), variant("relic"), 1.0).size == (264, 182)
    assert skin().render_taskbar(bad, theme(), variant("edgerun"), 48, 1.0, None).size == (240, 48)
    assert skin().render_tray_icon(bad, theme(), variant("mox"), 16).size == (16, 16)


@pytest.mark.parametrize("status", list(ApiStatus))
def test_every_status_renders_on_every_surface_and_variant(status):
    rows = sample_rows(55.0, 20.0, 1.0)
    state = model(rows, status)
    for variant_id in VARIANTS:
        for scale in (1.0, 1.5):
            assert skin().render_floating(state, theme(), variant(variant_id), scale, None).width == round(264 * scale)
            assert skin().render_tooltip(state, theme(), variant(variant_id), scale).width == round(264 * scale)
            assert skin().render_taskbar(state, theme(), variant(variant_id), 48, scale, None).height >= 48


def test_a_model_without_rows_still_renders():
    empty = model([], ApiStatus.LOADING, age=None)
    assert skin().render_floating(empty, theme(), variant("blackwall"), 1.0, None).size == (264, 96)
    assert skin().render_tooltip(empty, theme(), variant("blackwall"), 1.0).size == (264, 132)
    assert skin().render_tray_icon(empty, theme(), variant("blackwall"), 16).size == (16, 16)


# ── Band wording ──────────────────────────────────────────────────────────────

def test_band_wording_follows_the_theme_copy_and_the_status():
    expired = replace(model(sample_rows(), ApiStatus.STALE), stale_message="LOGIN EXPIRED - OPEN CLAUDE CODE")
    assert nc._band_text(theme(), expired) == theme().extras["copy"]["stale_auth"] == "RELINK // OPEN CLAUDE CODE"
    assert nc._band_text(theme(), model(sample_rows(), ApiStatus.NO_CREDENTIALS)) == "RELINK // OPEN CLAUDE CODE"
    assert nc._band_text(theme(), model(sample_rows(), ApiStatus.OFFLINE)) == theme().extras["copy"]["offline"]
    assert nc._band_text(theme(), model(sample_rows(), ApiStatus.RATE_LIMITED)) == "RATE LIMITED"
    assert nc._age_tag(model(sample_rows(), ApiStatus.STALE, age="41 M AGO")) == "41 M OLD"
    assert nc._age_tag(model(sample_rows(), ApiStatus.LIVE, age="12 S AGO")) is None
    assert nc._age_tag(model(sample_rows(), ApiStatus.NO_CREDENTIALS, age=None)) is None


# ── Tooltip ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("variant_id", VARIANTS)
def test_tooltip_sizes_follow_the_row_count_at_every_scale(variant_id):
    for name in ALL_STATES:
        sample = dump.sample_model(name)
        for scale in SCALES:
            image = skin().render_tooltip(sample, theme(), variant(variant_id), scale)
            assert image.mode == "RGBA"
            assert image.size == (round(264 * scale), round(TOOLTIP_HEIGHTS[len(sample.rows)] * scale)), (name, scale)
            assert image.getchannel("A").getextrema() == (0, 255)
            assert image.getpixel((0, 0))[3] == 0


def test_tooltip_info_line_shows_reset_time_and_pace():
    sample = model(sample_rows(72.0, 12.0, 0.0), ApiStatus.LIVE)
    ahead = model([row("session", "SESSION", 72.0, fraction=0.31, pace="ahead", severity="warning"),
                   row("weekly", "WEEKLY", 12.0, fraction=0.33, pace="on_pace")])
    assert nc._pace_text(ahead.rows[0]) == ("AHEAD +41", "ahead")
    assert nc._pace_text(ahead.rows[1]) == ("ON PACE", "plain")
    assert nc._pace_text(sample.rows[1]) == ("UNDER PACE -21", "behind")
    assert nc._pace_text(row("session", "SESSION", None, fraction=None, pace=None)) == ("PACE --", "plain")
    assert nc._exact_digits(12.0) == "12" and nc._exact_digits(12.34) == "12.3" and nc._exact_digits(None) == "--"
    plain = skin().render_tooltip(sample, theme(), variant("blackwall"), 2.0)
    other = skin().render_tooltip(ahead, theme(), variant("blackwall"), 2.0)
    assert plain.tobytes() != other.tobytes()


# ── Taskbar strip ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("scale", SCALES)
@pytest.mark.parametrize("height", [40, 48, 60, 80, 96])
def test_the_strip_fits_every_bar_height_at_every_scale(scale, height):
    sample = dump.sample_model("critical")
    for variant_id in VARIANTS:
        image = skin().render_taskbar(sample, theme(), variant(variant_id), height, scale, None)
        fit = min(scale, (height - 2) / 38)
        assert image.size == (round(240 * fit), height)
        bbox = image.getchannel("A").getbbox()
        assert bbox is not None
        assert bbox[0] == 0 and bbox[2] == image.width                              # the strip spans the image
        assert bbox[1] >= 1 and bbox[3] <= image.height - 1                         # clear of the bar's edges
        assert (bbox[1] + bbox[3]) // 2 in range(height // 2 - 1, height // 2 + 2)  # centred


@pytest.mark.parametrize("scale", SCALES)
def test_the_strip_has_its_natural_size_when_the_bar_is_tall_enough(scale):
    natural = round(38 * scale)
    image = skin().render_taskbar(dump.sample_model("normal3"), theme(), variant("blackwall"), natural + 2, scale,
                                  None)
    assert image.size == (round(240 * scale), natural + 2)


def test_the_strip_corners_are_chamfered_and_the_frame_is_translucent():
    image = skin().render_taskbar(dump.sample_model("normal3"), theme(), variant("blackwall"), 40, 1.0, None)
    pal = palette("blackwall")
    assert image.getpixel((0, 1))[3] == 0                                           # top-left chamfer
    assert image.getpixel((239, 38))[3] == 0                                        # bottom-right chamfer
    frame = image.getpixel((120, 1))
    assert frame[3] == pal.line_a[3] and near(frame, pal.line_a)                    # the 1 px frame
    assert image.getpixel((120, 0))[3] == 0                                         # the margin above


def on_taskbar(strip: Image.Image, color: tuple[int, int, int]) -> Image.Image:
    canvas = Image.new("RGBA", strip.size, (*color, 255))
    canvas.alpha_composite(strip)
    return canvas


@pytest.mark.parametrize("variant_id", VARIANTS)
def test_the_strip_reads_the_same_on_a_dark_and_a_light_taskbar(variant_id):
    """The plate is opaque, so what sits inside the frame cannot depend on the taskbar behind it."""
    strip = skin().render_taskbar(dump.sample_model("normal3"), theme(), variant(variant_id), 48, 2.0, None)
    dark, light = on_taskbar(strip, (28, 28, 33)), on_taskbar(strip, (243, 243, 243))
    inside = (round(10 * 1.21), 3, strip.width - round(10 * 1.21), strip.height - 3)   # clear of the frame and chamfers
    assert dark.crop(inside).tobytes() == light.crop(inside).tobytes()
    low, high = dark.crop(inside).convert("L").getextrema()
    assert high - low > 100                                                            # and it has contrast of its own


@pytest.mark.parametrize("variant_id", VARIANTS)
def test_a_transparent_strip_backs_its_text_so_it_reads_on_a_light_taskbar(variant_id):
    strip = skin().render_taskbar(dump.sample_model("normal3"), theme(), variant(variant_id), 80, 2.0,
                                  RenderOptions(transparent_bg=True))
    assert strip.getpixel((strip.width // 2, 6))[3] < 255                              # no plate behind the bars
    light = on_taskbar(strip, (243, 243, 243))
    labels = light.crop((12, 12, 64, 44)).convert("L")                                 # the 5H and 7D labels
    assert labels.getextrema()[0] < 100                                                # dark backing around the glyphs


def test_the_strip_marks_the_five_hour_and_seven_day_rows_with_the_variant_tokens():
    pal = palette("blackwall")
    image = skin().render_taskbar(dump.sample_model("normal3"), theme(), variant("blackwall"), 38 * 2 + 2, 2.0, None)
    assert strip_rgb(image, 30, 13.5, 2.0) == rgb(pal.bar)                          # first lit cell, row 5H
    assert strip_rgb(image, 150, 13.5, 2.0) == rgb(pal.track)                       # last cell, unlit
    assert strip_rgb(image, 45, 9.5, 2.0) == rgb(pal.accent)                        # pace hairline, before "now" (31 %)
    assert strip_rgb(image, 100, 9.5, 2.0) == rgb(pal.track)                        # and the rest of the window
    relic = skin().render_taskbar(dump.sample_model("normal3"), theme(), variant("relic"), 38 * 2 + 2, 2.0, None)
    assert strip_rgb(relic, 30, 13.5, 2.0) == rgb(palette("relic").bar)
    assert strip_rgb(relic, 100, 13.5, 2.0) == rgb(palette("relic").track)          # a slanted bar: no cell gaps


@pytest.mark.parametrize("status", list(ApiStatus))
def test_every_status_word_fits_inside_the_strip_tag(status):
    ink = ink_for(1.0)
    x0, _, x1, _ = nc.STRIP_TAG
    word = nc.STRIP_WORDS[status]
    spec = nc._fit_type(ink, word, nc.STATUS, x1 - x0 - 8, 9.0)
    assert ink.width(word, spec) <= x1 - x0 - 8 + 0.01
    assert spec.size >= 9.0


def is_panel(pixel: tuple[int, ...], pal: Palette) -> bool:
    """True for the empty panel, with or without a scan row over it."""
    darkest = 1 - pal.scan[3] / 255
    return all(round(c * darkest) - 2 <= p <= c + 2 for p, c in zip(pixel[:3], pal.panel[:3])) and pixel[3] == 255


@pytest.mark.parametrize("variant_id", VARIANTS)
@pytest.mark.parametrize("scale", [1.0, 2.0])
def test_three_digit_percents_stay_between_the_bar_and_the_status_tag(variant_id, scale):
    """The numbers are text, so look at the strips of panel they must leave clear on either side."""
    pal = palette(variant_id)
    segmented = nc.look_for(variant(variant_id)).segmented
    bar_end = nc.STRIP_BAR_X + (nc.STRIP_CELL_STEP * (nc.STRIP_CELLS - 1) + nc.STRIP_CELL_W if segmented
                                else nc.STRIP_BAR_W)
    for percent in (100.0, 112.0, 999.0):
        sample = model([row("session", "SESSION", percent, severity="critical"),
                        row("weekly", "WEEKLY", percent, severity="critical")])
        image = skin().render_taskbar(sample, theme(), variant(variant_id), round(38 * scale) + 2, scale, None)
        assert image.size == (round(240 * scale), round(38 * scale) + 2)
        for x_logical in (bar_end + 0.5, nc.STRIP_PCT_RIGHT + 1.5, nc.STRIP_PCT_RIGHT + 4):
            x = int(x_logical * scale)
            column = [image.getpixel((x, y)) for y in range(int(4 * scale), int(34 * scale))]
            assert all(is_panel(p, pal) for p in column), (percent, x_logical)   # no digit reaches the gaps


def test_the_strip_percent_text_shrinks_to_fit_but_not_below_eight_pixels():
    ink = ink_for(1.0)
    room = nc.STRIP_PCT_RIGHT - (nc.STRIP_BAR_X + nc.STRIP_CELL_STEP * (nc.STRIP_CELLS - 1) + nc.STRIP_CELL_W) - 2
    plain = nc._fit_type(ink, "13%", nc.STRIP_PCT, room, 8.0)
    wide = nc._fit_type(ink, "1000%", nc.STRIP_PCT, room, 8.0)
    assert plain == nc.STRIP_PCT and 8.0 <= wide.size < nc.STRIP_PCT.size
    assert ink.width("100%", nc._fit_type(ink, "100%", nc.STRIP_PCT, room, 8.0)) <= room + 0.01


# ── Tray icon: the RAM meter ──────────────────────────────────────────────────

LEGEND = {"L": "line", "B": "bg", "R": "ring", "C": "bar", "W": "warn", "D": "danger", "S": "stale"}

GOLDEN_16_AT_13_PERCENT = [
    "...LLLLLLLLLLL..",
    "..LBRRRRRRRRBBL.",
    ".LBBRRRRRRRRBBBL",
    "LBBBBBBBBBBBBBBL",
    "LBBBRRRRRRRRBBBL",
    "LBBBRRRRRRRRBBBL",
    "LBBBBBBBBBBBBBBL",
    "LBBBRRRRRRRRBBBL",
    "LBBBRRRRRRRRBBBL",
    "LBBBBBBBBBBBBBBL",
    "LBBBRRRRRRRRBBBL",
    "LBBBRRRRRRRRBBBL",
    "LBBBBBBBBBBBBBBL",
    ".LBBCCCCCCCCBBBL",
    "..LBCCCCCCCCBBL.",
    "...LLLLLLLLLLL..",
]
GOLDEN_24_AT_93_PERCENT = [
    "....LLLLLLLLLLLLLLLLL...",
    "...LBBBBBBBBBBBBBBBBBL..",
    "..LBBBBDDDDDDDDDDBBBBBL.",
    ".LBBBBBDDDDDDDDDDBBBBBBL",
    "LBBBBBBDDDDDDDDDDBBBBBBL",
    "LBBBBBBBBBBBBBBBBBBBBBBL",
    "LBBBBBBDDDDDDDDDDBBBBBBL",
    "LBBBBBBDDDDDDDDDDBBBBBBL",
    "LBBBBBBDDDDDDDDDDBBBBBBL",
    "LBBBBBBBBBBBBBBBBBBBBBBL",
    "LBBBBBBDDDDDDDDDDBBBBBBL",
    "LBBBBBBDDDDDDDDDDBBBBBBL",
    "LBBBBBBDDDDDDDDDDBBBBBBL",
    "LBBBBBBBBBBBBBBBBBBBBBBL",
    "LBBBBBBDDDDDDDDDDBBBBBBL",
    "LBBBBBBDDDDDDDDDDBBBBBBL",
    "LBBBBBBDDDDDDDDDDBBBBBBL",
    "LBBBBBBBBBBBBBBBBBBBBBBL",
    "LBBBBBBDDDDDDDDDDBBBBBBL",
    "LBBBBBBDDDDDDDDDDBBBBBBL",
    ".LBBBBBDDDDDDDDDDBBBBBBL",
    "..LBBBBBBBBBBBBBBBBBBBL.",
    "...LBBBBBBBBBBBBBBBBBL..",
    "....LLLLLLLLLLLLLLLLL...",
]


def tray_rows(image: Image.Image, pal: Palette) -> list[str]:
    names = {getattr(pal, name): letter for letter, name in LEGEND.items()}
    rows = []
    for y in range(image.height):
        row_ = ""
        for x in range(image.width):
            pixel = image.getpixel((x, y))
            row_ += "." if pixel[3] == 0 else names[pixel]
        rows.append(row_)
    return rows


def tray_model(percent: float | None, severity: str = "normal", status: ApiStatus = ApiStatus.LIVE) -> WidgetModel:
    return model([row("session", "SESSION", percent, severity=severity)], status)


@pytest.mark.parametrize("variant_id", VARIANTS)
@pytest.mark.parametrize("size", [16, 20, 24, 32])
def test_tray_icons_are_square_octagons_with_clear_corners(variant_id, size):
    image = skin().render_tray_icon(dump.sample_model("normal3"), theme(), variant(variant_id), size)
    assert image.size == (size, size) and image.mode == "RGBA"
    for corner in ((0, 0), (size - 1, 0), (0, size - 1), (size - 1, size - 1)):
        assert image.getpixel(corner)[3] == 0                                       # all four corners are cut
    assert image.getpixel((size // 2, 0))[3] == 255                                 # the frame is opaque
    assert image.getpixel((0, size // 2))[3] == 255
    colours = set(image.getdata())
    assert len(colours) <= 5                                                         # transparent, line, bg, ring, lit
    assert all(p[3] in (0, 255) for p in colours)                                    # crisp: no anti-aliasing


def test_tray_icons_match_the_approved_mock_pixels():
    pal = palette("blackwall", plate=False)
    first = skin().render_tray_icon(dump.sample_model("normal3"), theme(), variant("blackwall"), 16)
    assert tray_rows(first, pal) == GOLDEN_16_AT_13_PERCENT
    second = skin().render_tray_icon(dump.sample_model("critical"), theme(), variant("blackwall"), 24)
    assert tray_rows(second, pal) == GOLDEN_24_AT_93_PERCENT


@pytest.mark.parametrize(("percent", "lit"), [
    (None, 0), (0.0, 0), (9.9, 0), (10.0, 1), (13.0, 1), (29.9, 1), (30.0, 2), (50.0, 3), (72.0, 4), (89.9, 4),
    (90.0, 5), (93.0, 5), (100.0, 5), (150.0, 5),
])
def test_tray_cells_fill_from_the_bottom_in_twenty_percent_steps(percent, lit):
    pal = palette("blackwall", plate=False)
    for size in (16, 20, 24, 32):
        image = skin().render_tray_icon(tray_model(percent), theme(), variant("blackwall"), size)
        cells = []                                                                  # one entry per run of cell colour
        for y in range(size):
            pixel = image.getpixel((size // 2, y))
            if pixel in (pal.ring, pal.bar) and (not cells or cells[-1][0] != pixel or cells[-1][1] != y - 1):
                cells.append([pixel, y])
            elif pixel in (pal.ring, pal.bar):
                cells[-1][1] = y
        assert len(cells) == 5, (percent, size)
        lit_cells = [i for i, (pixel, _) in enumerate(cells) if pixel == pal.bar]
        assert lit_cells == list(range(5 - lit, 5)), (percent, size)                 # the bottom `lit` cells


def test_tray_colour_follows_severity_and_staleness():
    pal = palette("blackwall", plate=False)

    def lit_colours(m: WidgetModel) -> set:
        image = skin().render_tray_icon(m, theme(), variant("blackwall"), 24)
        return set(image.getdata()) & {pal.bar, pal.warn, pal.danger, pal.stale}

    assert lit_colours(tray_model(13.0)) == {pal.bar}
    assert lit_colours(tray_model(72.0, "warning")) == {pal.warn}
    assert lit_colours(tray_model(93.0, "critical")) == {pal.danger}
    assert lit_colours(tray_model(13.0, status=ApiStatus.STALE)) == {pal.stale}
    assert lit_colours(tray_model(None, status=ApiStatus.LOADING)) == set()


@pytest.mark.parametrize("variant_id", VARIANTS)
def test_tray_icon_uses_the_line_bg_and_ring_tokens(variant_id):
    pal = palette(variant_id, plate=False)
    image = skin().render_tray_icon(tray_model(40.0), theme(), variant(variant_id), 32)
    assert image.getpixel((16, 0)) == pal.line
    assert image.getpixel((2, 16)) == pal.bg
    assert pal.ring in set(image.getdata()) and pal.bar in set(image.getdata())
    recoloured = skin().render_tray_icon(tray_model(40.0), theme(), with_extras(line="#00ff00", ring="#0000ff"), 32)
    assert recoloured.getpixel((16, 0))[:3] == (0, 255, 0)
    assert (0, 0, 255, 255) in set(recoloured.getdata())


def test_tray_icon_ignores_the_edgerun_plate():
    image = skin().render_tray_icon(tray_model(40.0), theme(), variant("edgerun"), 32)
    assert image.getpixel((16, 0)) == palette("edgerun", plate=False).line          # a yellow frame on a dark plate


def test_tray_icon_rejects_tiny_sizes_and_is_deterministic():
    with pytest.raises(ValueError):
        skin().render_tray_icon(tray_model(10.0), theme(), variant("blackwall"), 4)
    first = skin().render_tray_icon(tray_model(55.0), theme(), variant("mox"), 20)
    second = NightCitySkin().render_tray_icon(tray_model(55.0), theme(), variant("mox"), 20)
    assert first.tobytes() == second.tobytes()


@pytest.mark.parametrize("size", [8, 12, 18, 28, 48, 64])
def test_off_table_tray_sizes_still_fit_five_cells(size):
    pal = palette("relic", plate=False)
    cell_h = nc._tray_geometry(size)[1]
    full = skin().render_tray_icon(tray_model(100.0), theme(), variant("relic"), size)
    empty = skin().render_tray_icon(tray_model(0.0), theme(), variant("relic"), size)
    assert full.size == empty.size == (size, size)
    assert full.getpixel((size // 2, size // 2))[3] == 255

    def rows_of(image: Image.Image, colour: tuple[int, ...]) -> int:                 # on the centre line
        return sum(1 for y in range(size) if image.getpixel((size // 2, y)) == colour)

    assert rows_of(full, pal.bar) == 5 * cell_h and rows_of(empty, pal.ring) == 5 * cell_h


# ── What the frames say ───────────────────────────────────────────────────────
# Shapes and sizes pass when the right shapes carry the wrong words or numbers: of 23 small mutations of the two
# skins, 8 that visibly change what is drawn passed the tests. These read the text and the numerals a frame draws
# and compare renders where the difference is a colour.

def drawn_text(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every run of text the render calls from here on draw, in order (the numerals are drawn another way)."""
    texts: list[str] = []
    real = nc._Ink.text

    def spy(self: nc._Ink, text: str, *args: Any, **kwargs: Any) -> float:
        texts.append(text)
        return real(self, text, *args, **kwargs)

    monkeypatch.setattr(nc._Ink, "text", spy)
    return texts


def drawn_digits(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """(row key, numerals) of every value the render calls from here on draw."""
    seen: list[tuple[str, str]] = []
    real = nc._draw_value

    def spy(ink: nc._Ink, row_: Row, emphasis: str, motion: Any, top: float, digits: str) -> None:
        seen.append((row_.key, digits))
        real(ink, row_, emphasis, motion, top, digits)

    monkeypatch.setattr(nc, "_draw_value", spy)
    return seen


@pytest.mark.parametrize(("percent", "digits"), [
    (79.6, "80"), (79.5, "80"), (79.4, "79"), (0.4, "0"), (99.5, "100"), (112.4, "112"), (-3.0, "0"), (None, "--"),
])
def test_a_value_is_rounded_to_the_nearest_percent(percent, digits):
    assert nc._digits(percent) == digits


def test_the_widget_draws_the_rounded_value_and_the_tooltip_the_exact_one(monkeypatch):
    """A session at 79.6 % was drawn as 79 here while the dashboard and Gates show 80."""
    seen = drawn_digits(monkeypatch)
    m = model(sample_rows(session=79.6))
    render(m, "blackwall", 1.0)
    assert ("session", "80") in seen and ("session", "79") not in seen
    seen.clear()
    skin().render_tooltip(m, theme(), variant("blackwall"), 1.0)
    assert ("session", "79.6") in seen


def test_a_scoped_row_leaves_its_reset_time_to_the_tooltip(monkeypatch):
    """The Fable limit resets with the weekly one: the widget does not repeat the time, the tooltip does."""
    fable = row("scoped:fable", "FABLE", 40.0, fraction=0.33, reset="Tue 1:11 AM", share="50% SHARE")
    assert nc._reset_text(fable, False) is None and nc._reset_text(fable, True) == "RST TUE 1:11 AM"
    plain = row("weekly", "WEEKLY", 2.0, reset="Mon 9:00 AM")
    assert nc._reset_text(plain, False) == nc._reset_text(plain, True) == "RST MON 9:00 AM"
    m = model(sample_rows(fable=None) + [fable])
    texts = drawn_text(monkeypatch)
    render(m, "blackwall", 1.0)
    assert "RST 7:39 PM" in texts and "RST MON 9:00 AM" in texts       # the session and the weekly row say when they reset
    assert not any("1:11" in text for text in texts)
    texts.clear()
    skin().render_tooltip(m, theme(), variant("blackwall"), 1.0)
    assert "RST TUE 1:11 AM" in texts


def test_a_stale_frame_draws_no_severity_colour():
    """Stale rows are flat, the band carries the state: severities change nothing on a stale widget or tooltip."""
    loud = sample_rows(session=93.0, weekly=80.0, severities=("critical", "warning", "normal"))
    flat = sample_rows(session=93.0, weekly=80.0)
    for variant_id in ("blackwall", "edgerun"):
        def frames(rows: list[Row], status: ApiStatus) -> tuple[bytes, bytes]:
            m = model(rows, status)
            return (render(m, variant_id, 1.0).tobytes(),
                    skin().render_tooltip(m, theme(), variant(variant_id), 1.0).tobytes())

        for status in (ApiStatus.STALE, ApiStatus.OFFLINE, ApiStatus.RATE_LIMITED):
            assert frames(loud, status) == frames(flat, status), (variant_id, status)
        assert frames(loud, ApiStatus.LIVE)[0] != frames(flat, ApiStatus.LIVE)[0], variant_id      # live ones do differ
    assert nc._emphasis(model(loud, ApiStatus.STALE), loud[0]) == "normal"
    assert nc._emphasis(model(loud), loud[0]) == "critical" and nc._emphasis(model(loud), loud[1]) == "warning"


def test_the_layout_reserves_exactly_the_width_the_chips_draw():
    ink = ink_for()
    outline = ink.pal.warn
    assert nc._chip_width(ink, "warning") == nc._chip_out(ink, 0, 0, "WARN", outline, outline)
    assert nc._chip_width(ink, "critical") == nc._chip_crit(ink, 0, 0)
    assert nc._chip_width(ink, "normal") == 0


@pytest.mark.parametrize("variant_id", VARIANTS)
def test_the_warn_chip_has_the_same_padding_either_side_of_its_text(variant_id):
    """The chip is its text plus the same padding either side. Four px less on the right pushed the text against its
    frame, which no test saw (the layout and the drawing shrank together)."""
    scale = 4.0
    rows = sample_rows(session=78.0, severities=("warning", "normal", "normal"))
    image = render(model(rows), variant_id, scale)
    boxes = nc._layout_row(ink_for(1.0, variant_id), nc.look_for(variant(variant_id)), rows[0], "warning",
                           nc._digits(78.0), nc._reset_text(rows[0], False))
    assert boxes.chip is not None
    panel = palette(variant_id).panel[:3]
    left, right = int(boxes.chip[0] * scale), round(boxes.chip[1] * scale)
    top, bottom = int(nc.ROW_TOP * scale), int((nc.ROW_TOP + nc.CHIP_H) * scale)
    glyph_columns = [x for y in range(top + int((nc.CHIP_CUT + 1) * scale), bottom - int(2 * scale))      # below the chamfer
                     for x in range(left + int(2 * scale), right - int(2 * scale))                         # inside the frame
                     if sum(abs(a - b) for a, b in zip(image.getpixel((x, y))[:3], panel)) > 90]
    assert glyph_columns
    before, after = (min(glyph_columns) - left) / scale, (right - 1 - max(glyph_columns)) / scale
    assert before > 4 and after > 4 and abs(before - after) <= 1.0, (before, after)


# ── Fonts ─────────────────────────────────────────────────────────────────────

def test_a_theme_without_font_roles_falls_back_to_the_bundled_faces(monkeypatch):
    monkeypatch.setattr(nc, "_FACES", {})
    monkeypatch.setattr(nc.theme_registry, "font_path", lambda *args, **kwargs: None)
    faces = nc._faces(theme())
    assert set(faces) == {nc.FONT_BOLD, nc.FONT_SEMI, nc.FONT_MONO}
    assert all(path.is_file() for path in faces.values())
    assert skin().render_floating(dump.sample_model("normal3"), theme(), variant("mox"), 1.0, None).size == (264, 126)


def test_the_font_fallback_is_logged_once_per_face(monkeypatch, caplog):
    monkeypatch.setattr(nc, "_FACES", {})
    monkeypatch.setattr(nc.theme_registry, "font_path", lambda *args, **kwargs: None)
    with caplog.at_level(logging.DEBUG, logger="usage_monitor.render.skins.night_city"):
        nc._faces(theme())
        nc._faces(theme())                                                          # cached: no second round of logs
    notes = [record.getMessage() for record in caplog.records if "bundled" in record.getMessage()]
    assert len(notes) == 3 and all("night_city" in note for note in notes)


def test_missing_font_files_degrade_to_a_default_face_instead_of_failing(monkeypatch):
    missing = {key: Path("no-such-folder") / key for key in (nc.FONT_BOLD, nc.FONT_SEMI, nc.FONT_MONO)}
    monkeypatch.setattr(nc, "_FACES", {"night_city": missing})
    sample = dump.sample_model("critical")
    assert skin().render_floating(sample, theme(), variant("blackwall"), 1.0, None).size == (264, 126)
    assert skin().render_taskbar(sample, theme(), variant("blackwall"), 48, 1.0, None).size == (240, 48)
    assert skin().render_tooltip(sample, theme(), variant("blackwall"), 1.0).size == (264, 182)


# ── Speed (loose, three times the budget) ─────────────────────────────────────

def mean_ms(call, runs: int = 5) -> float:
    call()                                                                           # warm the font and mask caches
    started = time.perf_counter()
    for _ in range(runs):
        call()
    return (time.perf_counter() - started) / runs * 1000


@pytest.mark.parametrize("variant_id", VARIANTS)
def test_floating_and_strip_frames_are_fast_enough_at_scale_one_and_a_half(variant_id):
    for name in ("normal3", "critical", "stale"):
        sample = dump.sample_model(name)
        floating = mean_ms(lambda: skin().render_floating(sample, theme(), variant(variant_id), 1.5, None))
        strip = mean_ms(lambda: skin().render_taskbar(sample, theme(), variant(variant_id), 72, 1.5, None))
        assert floating < 180, f"floating {name} took {floating:.1f} ms (budget 60 ms)"
        assert strip < 120, f"strip {name} took {strip:.1f} ms (budget 40 ms)"


def test_tooltip_frames_are_fast_enough():
    sample = dump.sample_model("normal3")
    took = mean_ms(lambda: skin().render_tooltip(sample, theme(), variant("blackwall"), 1.5))
    assert took < 180, f"tooltip took {took:.1f} ms"


# ── Dump ──────────────────────────────────────────────────────────────────────

def test_dump_frames_writes_night_city_files(tmp_path: Path):
    report = dump.dump_frames(tmp_path, themes=["night_city"], variants=["edgerun"], states=["normal3", "stale"],
                              scales=[1.0, 1.5])
    variant_dir = tmp_path / "night_city" / "edgerun"
    assert report.files == 2 * 2 * (1 + len(dump.TASKBAR_HEIGHTS_AT_1X) + 1) + 2 * 4
    for name in ("floating_normal3_x1.png", "floating_stale_x1.5.png", "taskbar_normal3_h48_x1.png",
                 "taskbar_stale_h72_x1.5.png", "tooltip_stale_x1.png", "tray_normal3_16.png", "tray_stale_32.png"):
        assert (variant_dir / name).is_file(), name
    with Image.open(variant_dir / "floating_normal3_x1.png") as opened:
        assert opened.size == (264, 126)


# ── Real windows and icons (CUM_UI_TESTS=1) ───────────────────────────────────

@UI
@pytest.mark.skipif(sys.platform != "win32", reason="the Win32 UI layer is Windows only")
def test_night_city_frames_show_in_layered_windows():
    from usage_monitor.ui.host import UIHost
    from usage_monitor.ui.layered import LayeredWindow

    host = UIHost(controller_class=f"CUM.Test.NightCity.{uuid.uuid4().hex[:8]}")
    try:
        offscreen = (-30000, -30000)
        sample = dump.sample_model("critical")
        clear = RenderOptions(transparent_bg=True)
        for scale in (1.0, 1.5):
            for make in (
                lambda: skin().render_floating(sample, theme(), variant("blackwall"), scale, None),
                lambda: skin().render_floating(sample, theme(), variant("edgerun"), scale, clear),
                lambda: skin().render_tooltip(sample, theme(), variant("relic"), scale),
                lambda: skin().render_taskbar(sample, theme(), variant("mox"), round(48 * scale), scale, None),
            ):
                frame = make()
                window = LayeredWindow(host, draggable=False)
                assert window.update(frame, *offscreen) is True
                rect = window.rect
                assert (rect.width, rect.height) == frame.size
                window.destroy()
    finally:
        host.close()


@UI
@pytest.mark.skipif(sys.platform != "win32", reason="the Win32 UI layer is Windows only")
@pytest.mark.parametrize("size", [16, 20, 24, 32])
def test_night_city_tray_images_make_icons(size):
    from usage_monitor.ui import win32
    from usage_monitor.ui.tray import create_hicon

    for name in ("normal3", "critical", "stale"):
        hicon = create_hicon(skin().render_tray_icon(dump.sample_model(name), theme(), variant("blackwall"), size))
        assert hicon
        win32.DestroyIcon(hicon)
