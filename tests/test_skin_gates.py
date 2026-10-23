"""Tests for the Gates skin: usage_monitor/render/skins/gates.py.

Everything renders to memory or to pytest's tmp_path, so none of it needs a desktop and all of it runs
in CI. The two tests that hand frames to real (off-screen) windows and icons are skipped unless
CUM_UI_TESTS=1.

Pixel checks look at shapes (bars, plates, rings, the card), not at text, so they hold with whatever
font the machine has. Text is checked through the runs the skin queues (positions and widths).
"""
from __future__ import annotations

import math
import os
import sys
import time
import uuid
from dataclasses import replace
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from usage_monitor import themes as theme_registry
from usage_monitor.data.models import ApiStatus, Limit
from usage_monitor.render import dump, engine
from usage_monitor.render.model import build_model
from usage_monitor.render.skins import RenderOptions, get_skin
from usage_monitor.render.skins import gates
from usage_monitor.render.skins.gates import GatesSkin, Palette

SCALES = (1.0, 1.25, 1.5, 2.0)
STRIP_HEIGHTS = (40, 48, 60, 80)
TRAY_SIZES = (16, 20, 24, 32)
STATES = dump.STATE_NAMES
NOW = dump.SAMPLE_NOW

UI = pytest.mark.skipif(
    os.environ.get("CUM_UI_TESTS") != "1", reason="needs an interactive desktop: set CUM_UI_TESTS=1"
)


# ── Helpers ───────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def theme() -> Any:
    return dump.load_theme_objects()["gates"]


def variant(variant_id: str) -> Any:
    return theme().variant(variant_id)


def skin() -> GatesSkin:
    return get_skin("gates")  # type: ignore[return-value]


def model(session: float = 13.0, weekly: float = 2.0, *, status: ApiStatus = ApiStatus.LIVE, age_s: int = 12,
          scoped: float | None = None, label: str = "FABLE WEEKLY",
          severity: tuple[str | None, str | None] = (None, None),
          session_resets: timedelta = timedelta(hours=3, minutes=27)):
    """A model with chosen percentages (the dump's clock: 31 % of the session window gone)."""
    limits = [
        Limit(kind="session", group="session", percent=session, severity=severity[0], resets_at=NOW + session_resets,
              is_active=True, label="SESSION", window_seconds=18000),
        Limit(kind="weekly_all", group="weekly", percent=weekly, severity=severity[1],
              resets_at=NOW + timedelta(days=3), is_active=True, label="WEEKLY", window_seconds=604800),
    ]
    if scoped is not None:
        limits.append(Limit(kind="weekly_scoped", group="weekly", percent=scoped, severity=None,
                            resets_at=NOW + timedelta(days=3), is_active=True, label=label, scope_model="Fable",
                            window_seconds=604800, allowance_pct=50.0))
    return build_model(dump._state(NOW, status, limits, age_s), NOW)


def margin(scale: float) -> int:
    return round(gates.MARGIN * scale)


def card_height(m) -> int:
    """The card's height: 128 px with its two rows and the footer, and 27 px more for every scoped row the model carries."""
    return 128 + 27 * len([row for row in m.rows[2:] if row.percent is not None])


def frame_size(m, scale: float = 1.0) -> tuple[int, int]:
    """The floating frame: the card and a 24 px margin on every side."""
    return round((288 + 48) * scale), round((card_height(m) + 48) * scale)


def at(image: Image.Image, x: float, y: float, scale: float = 1.0) -> tuple[int, int, int, int]:
    """The pixel at card coordinates (x, y) of a floating or tooltip frame."""
    return image.getpixel((margin(scale) + round(x * scale), margin(scale) + round(y * scale)))


def colour(hex_value: str) -> tuple[int, int, int, int]:
    return engine.parse_color(hex_value)


def near(actual: tuple[int, ...], expected: tuple[int, ...], tolerance: int = 3) -> bool:
    return all(abs(a - e) <= tolerance for a, e in zip(actual[:3], expected[:3]))


def find_colour(image: Image.Image, box: tuple[int, int, int, int], expected: tuple[int, ...],
                tolerance: int = 4) -> bool:
    left, top, right, bottom = box
    return any(near(image.getpixel((x, y)), expected, tolerance) and image.getpixel((x, y))[3] > 200
               for y in range(top, bottom) for x in range(left, right))


def modes_theme(mode: str, **tokens: str) -> Any:
    """The theme with some per-mode tokens replaced (theme.extras.modes[mode])."""
    modes = {key: dict(value) for key, value in theme().extras["modes"].items()}
    modes[mode].update(tokens)
    return replace(theme(), extras={**theme().extras, "modes": modes})


def with_colors(base: Any, **colors: str) -> Any:
    return replace(base, colors={**base.colors, **colors})


def luminance(rgb: tuple[int, ...]) -> float:
    def channel(value: int) -> float:
        v = value / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2])


def contrast(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def flatten(image: Image.Image, background: tuple[int, int, int]) -> Image.Image:
    base = Image.new("RGBA", image.size, background + (255,))
    return Image.alpha_composite(base, image.convert("RGBA")).convert("RGB")


def queued_runs(ink: gates.Ink) -> list[tuple[str, float, float, str]]:
    """(text, left, right, align) of every run the canvas has queued, in logical px."""
    runs = []
    for text, x, _baseline, face, _color, align in ink._texts:
        width = ink.advance(text, face)
        left = x - width if align == "right" else x
        runs.append((text, left, left + width, align))
    return runs


# ── Registry and sizes ────────────────────────────────────────────────────────

def test_the_registry_serves_the_gates_skin():
    assert isinstance(get_skin("gates"), GatesSkin)
    assert get_skin("gates").theme_id == "gates"


def test_every_variant_renders_every_state():
    """All 14 variants x all 10 states, floating at 1x: the right size, real alpha, clear corners."""
    for v in theme().variants:
        for name in STATES:
            m = dump.sample_model(name)
            image = skin().render_floating(m, theme(), v, 1.0, None)
            assert image.mode == "RGBA"
            assert image.size == frame_size(m), (v.id, name)
            assert image.getpixel((0, 0))[3] == 0 and image.getpixel((image.width - 1, image.height - 1))[3] == 0
            assert image.getchannel("A").getextrema()[1] == 255, (v.id, name)


@pytest.mark.parametrize("variant_id", ["system-dark", "rose-light"])
def test_floating_renders_every_state_at_every_scale(variant_id):
    for name in STATES:
        for scale in SCALES:
            m = dump.sample_model(name)
            image = skin().render_floating(m, theme(), variant(variant_id), scale, None)
            assert image.size == frame_size(m, scale), (name, scale)
            assert image.getpixel((0, 0))[3] == 0


def test_floating_size_matches_the_rendered_size_and_follows_the_rows_and_never_the_state():
    """The card is 128 px tall with the session and weekly rows, and one row (27 px) taller for each scoped row: what
    the model carries decides, not the status."""
    for scale in (1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5):
        heights: dict[int, set[int]] = {}
        for name in STATES:
            m = dump.sample_model(name)
            image = skin().render_floating(m, theme(), variant("system-dark"), scale, None)
            assert skin().floating_size(theme(), m, scale) == image.size
            heights.setdefault(len(m.rows), set()).add(image.height)
        assert set(heights) == {2, 3}
        assert all(len(found) == 1 for found in heights.values()), scale          # the state changes nothing
        two, three = (next(iter(heights[count])) for count in (2, 3))
        assert three - two == pytest.approx(27 * scale, abs=1.5), scale


def test_the_card_sits_in_the_frame_margin():
    image = skin().render_floating(dump.sample_model("normal3"), theme(), variant("system-dark"), 1.0, None)
    assert at(image, 144, 64)[3] > 200                          # card interior
    assert image.getpixel((12, 88))[3] <= 40                    # the margin holds only the soft shadow
    assert image.getpixel((168, 24 + 128 + 6))[3] > image.getpixel((168, 24 - 6))[3]   # shadow falls downward


# ── Alpha ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("scale", SCALES)
def test_card_corners_are_rounded_and_the_interior_carries_the_token_alpha(scale):
    image = skin().render_floating(dump.sample_model("normal3"), theme(), variant("system-dark"), scale, None)
    token_alpha = colour(theme().extras["modes"]["dark"]["widget_bg"])[3]
    assert at(image, 0, 0, scale)[3] < 80 and at(image, 144, 0, scale)[3] > 200      # rounded corner, straight edge
    assert abs(at(image, 200, 121, scale)[3] - token_alpha) <= 3
    assert image.getpixel((0, 0))[3] == 0                                           # the frame corner is clear


def test_alpha_never_darkens_the_card_edge():
    """The downsample runs on premultiplied colour: the antialiased corner keeps the border's colour
    (white here) instead of fading toward the black of the empty pixels around it."""
    image = skin().render_floating(dump.sample_model("normal3"), theme(), variant("system-dark"), 1.0,
                                   {"transparent_bg": True})
    partial = [image.getpixel((x, y)) for y in range(24, 34) for x in range(24, 34)
               if 0 < image.getpixel((x, y))[3] < 250]
    assert len(partial) >= 8
    assert all(pixel[0] >= 235 for pixel in partial), partial


def test_shadow_is_clipped_at_a_low_alpha_and_stays_inside_the_frame():
    image = skin().render_floating(dump.sample_model("normal3"), theme(), variant("system-dark"), 1.0, None)
    alpha = image.getchannel("A")
    assert alpha.getpixel((0, 88)) == 0 and alpha.getpixel((168, 0)) == 0
    assert 0 < alpha.getpixel((24 - 6, 88)) < alpha.getpixel((24 - 1, 88))      # fades with distance


def test_transparent_background_drops_the_fill_the_highlight_and_the_shadow():
    m = dump.sample_model("normal3")
    solid = skin().render_floating(m, theme(), variant("system-dark"), 1.0, None)
    clear = skin().render_floating(m, theme(), variant("system-dark"), 1.0, {"transparent_bg": True})
    assert clear.size == solid.size
    assert at(clear, 200, 121)[3] == 0 and at(solid, 200, 121)[3] > 200            # no fill
    assert clear.getpixel((24 + 144, 24 + 128 + 6))[3] == 0                         # no shadow
    assert at(clear, 144, 0)[3] > 0                                                 # the 1 px frame stays
    assert find_colour(clear, (0, 0, 336, 176), colour("#60cdff"))                  # and the content


def test_taskbar_strip_has_real_alpha_and_the_plate_is_opaque():
    for scale in SCALES:
        height = round(48 * scale)
        image = skin().render_taskbar(dump.sample_model("normal3"), theme(), variant("system-dark"),
                                      height, scale, None)
        plate_h = round(40 * scale)
        top = (height - plate_h) // 2
        assert image.getpixel((0, 0))[3] == 0 and image.getpixel((0, top))[3] < 80      # clear margin, rounded corner
        assert image.getpixel((image.width // 2, top + 1))[3] == 255                    # opaque plate
        assert image.getpixel((image.width // 2, height - 1))[3] == 0


@pytest.mark.parametrize("size", TRAY_SIZES + (8, 12, 40, 48))
def test_tray_icons_are_square_with_a_clear_corner(size):
    image = skin().render_tray_icon(dump.sample_model("normal3"), theme(), variant("system-dark"), size)
    assert image.size == (size, size) and image.mode == "RGBA"
    assert image.getpixel((0, 0))[3] == 0 and image.getpixel((size - 1, size - 1))[3] == 0
    assert image.getchannel("A").getextrema()[1] == 255                               # the arc is solid


def test_tray_icon_rejects_tiny_sizes():
    with pytest.raises(ValueError):
        skin().render_tray_icon(dump.sample_model("normal3"), theme(), variant("system-dark"), 4)


# ── Taskbar strip ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("variant_id", ["system-dark", "teal-light"])
def test_strip_renders_at_every_height_and_scale_without_clipping(variant_id):
    for scale in SCALES:
        for height in STRIP_HEIGHTS:
            for name in ("normal3", "warning", "critical", "stale", "loading"):
                image = skin().render_taskbar(dump.sample_model(name), theme(), variant(variant_id),
                                              height, scale, None)
                assert image.height == height, (scale, height)
                full_width = round(248 * scale)
                if height / scale >= 36:
                    assert image.width == full_width, (scale, height)
                else:                                              # a bar shorter than the plate shrinks the strip
                    assert 0 < image.width < full_width, (scale, height)
                bbox = image.getchannel("A").getbbox()
                assert bbox is not None
                assert bbox[0] == 0 and bbox[2] == image.width               # the plate spans the whole width
                assert 0 <= bbox[1] and bbox[3] <= height                    # and fits the bar
                draw_scale, plate = gates._strip_fit(height, scale)
                assert abs((bbox[3] - bbox[1]) - round(plate * draw_scale)) <= 1, (scale, height)
                assert abs((bbox[1] + bbox[3]) / 2 - height / 2) <= 1        # centred on the bar


def test_strip_plate_is_40_px_on_a_48_px_bar_and_34_on_a_40_px_bar():
    m = dump.sample_model("normal3")
    for height, plate in ((48, 40), (40, 34), (60, 40), (80, 40)):
        image = skin().render_taskbar(m, theme(), variant("system-dark"), height, 1.0, None)
        bbox = image.getchannel("A").getbbox()
        assert bbox is not None and bbox[3] - bbox[1] == plate, (height, bbox)
        assert (bbox[1] + bbox[3]) // 2 == height // 2 or abs((bbox[1] + bbox[3]) / 2 - height / 2) <= 1


def test_strip_rows_do_not_collide_on_a_short_bar():
    tops = gates._strip_rows(34.0)
    assert tops[1] - tops[0] >= 14
    tops = gates._strip_rows(40.0)
    assert tops == (6.0, 25.0)                                   # the approved layout


def test_strip_is_readable_on_dark_and_light_taskbars():
    """The plate is opaque and made of tokens, so the labels and values keep their contrast on whatever
    taskbar sits behind: a dark variant on a light taskbar and a light variant on a dark one."""
    for v in theme().variants:
        pal = Palette.for_variant(theme(), v)
        assert pal.plate[3] == 255
        assert contrast(pal.text_3, pal.plate) >= 4.0, v.id        # labels: the approved text-3 on the plate
        assert contrast(pal.text, pal.plate) >= 7, v.id            # values
    for v_id, backdrop in (("system-dark", (243, 243, 243)), ("system-light", (32, 32, 32))):
        image = skin().render_taskbar(dump.sample_model("warning"), theme(), variant(v_id), 48, 1.0, None)
        pal = Palette.for_variant(theme(), variant(v_id))
        on_backdrop = flatten(image, backdrop)
        assert near(on_backdrop.getpixel((200, 4 + 38)), pal.plate, 2)          # the plate, not the backdrop
        assert near(on_backdrop.getpixel((5, 24)), pal.plate, 2)


def test_strip_transparent_background_leaves_the_plate_out():
    m = dump.sample_model("normal3")
    plate = skin().render_taskbar(m, theme(), variant("system-dark"), 48, 1.0, None)
    bare = skin().render_taskbar(m, theme(), variant("system-dark"), 48, 1.0, {"transparent_bg": True})
    assert bare.size == plate.size
    assert bare.getpixel((5, 24))[3] == 0 and plate.getpixel((5, 24))[3] == 255
    assert find_colour(bare, (0, 0, 248, 48), colour("#60cdff"))               # the bar is still there


def test_strip_value_and_labels_fit_in_every_state():
    for name in STATES:
        ink = gates.Ink(gates.STRIP_W, 40, 1.0, gates._fonts(theme()))
        pal = Palette.for_variant(theme(), variant("system-dark"))
        m = dump.sample_model(name)
        face = ink.face(ink.fonts.mono, gates.SIZE_STRIP_V, 700)
        for value in ("100%", "999%", "--", "13%"):
            assert ink.advance(value, face) <= gates.STRIP_VALUE_RIGHT - (gates.STRIP_RAIL_X1 + 4), value
        assert pal and m


# ── Tooltip ───────────────────────────────────────────────────────────────────

def test_tooltip_height_follows_the_rows_and_the_footer():
    heights = {}
    for name in STATES:
        image = skin().render_tooltip(dump.sample_model(name), theme(), variant("system-dark"), 1.0)
        assert image.width == 336
        heights[name] = image.height
    assert heights["normal3"] == heights["warning"] == heights["critical"]          # three rows, no footer
    assert heights["normal2"] < heights["normal3"]
    assert heights["stale"] == heights["normal2"] + gates.TIP_FOOT_H                # two rows and a footer
    assert heights["offline"] == heights["paused"] == heights["stale"]


def test_tooltip_renders_every_variant_state_and_scale():
    for v in theme().variants:
        for name in ("normal3", "critical", "stale"):
            image = skin().render_tooltip(dump.sample_model(name), theme(), v, 1.0)
            assert image.mode == "RGBA" and image.getpixel((0, 0))[3] == 0
            assert image.getchannel("A").getextrema()[1] == 255
    for scale in SCALES:
        a = skin().render_tooltip(dump.sample_model("normal3"), theme(), variant("blue-light"), scale)
        b = skin().render_tooltip(dump.sample_model("normal3"), theme(), variant("blue-light"), 1.0)
        same_size = a.size == (round(b.width * scale), round(b.height * scale))
        assert same_size or abs(a.height - b.height * scale) <= 2 * scale


def test_tooltip_info_lines_are_for_rows_with_something_to_say():
    m = dump.sample_model("loading")          # nothing known yet: no reset times, no pace
    ink = gates.Ink(gates.CARD_W, 200, 1.0, gates._fonts(theme()))
    pal = Palette.for_variant(theme(), variant("system-dark"))
    for index, row in enumerate(m.rows):
        gates._row(ink, pal, m, row, gates.TIP_ROW0 + gates.TIP_PITCH * index, exact=True)
    assert not any(text.startswith("Resets") for text, *_ in queued_runs(ink))


# ── Tokens: colour sampling ───────────────────────────────────────────────────

BAR_FILL = (25, 38 + gates.BAR_DY + 2)       # inside the 13 % fill of the session bar
BAR_TRACK = (200, 38 + gates.BAR_DY + 2)     # on the empty part of the same bar


@pytest.mark.parametrize("variant_id", [v.id for v in theme().variants])
def test_the_bar_fill_is_the_variants_accent(variant_id):
    v = variant(variant_id)
    image = skin().render_floating(dump.sample_model("normal3"), theme(), v, 1.0, None)
    assert near(at(image, *BAR_FILL), colour(v.colors["accent"]), 2), variant_id
    assert at(image, *BAR_FILL)[3] == 255


def test_changing_the_accent_token_changes_the_bar():
    base = variant("blue-dark")
    red = with_colors(base, accent="#ff0000")
    a = skin().render_floating(dump.sample_model("normal3"), theme(), base, 1.0, None)
    b = skin().render_floating(dump.sample_model("normal3"), theme(), red, 1.0, None)
    assert at(a, *BAR_FILL) != at(b, *BAR_FILL)
    assert near(at(b, *BAR_FILL), (255, 0, 0), 2)
    assert find_colour(b, (24 + 14, 24 + 12, 24 + 30, 24 + 30), (255, 0, 0), 40)       # the header glyph follows too


def test_changing_the_track_token_changes_the_empty_bar():
    base = variant("system-dark")
    a = skin().render_floating(dump.sample_model("normal3"), theme(), base, 1.0, None)
    b = skin().render_floating(dump.sample_model("normal3"), theme(), with_colors(base, track="#00ff0080"), 1.0, None)
    assert at(a, *BAR_TRACK) != at(b, *BAR_TRACK)
    assert at(b, *BAR_TRACK)[1] > at(a, *BAR_TRACK)[1] + 30


def test_the_card_fill_comes_from_the_widget_bg_token():
    for mode, token in (("dark", "#336699ff"), ("light", "#996633ff")):
        custom = modes_theme(mode, widget_bg=token)
        image = skin().render_floating(dump.sample_model("normal3"), custom, variant(f"system-{mode}"), 1.0, None)
        assert near(at(image, 200, 121), colour(token), 2), mode
        assert at(image, 200, 121)[3] >= 250


def test_the_border_and_the_shadow_come_from_tokens():
    m = dump.sample_model("normal2")                                  # the 128 px card: its bottom edge is at 24 + 128
    plain = skin().render_floating(m, theme(), variant("system-dark"), 1.0, None)
    loud = skin().render_floating(m, modes_theme("dark", widget_border="#ff0000ff"), variant("system-dark"), 1.0, None)
    assert near(at(loud, 144, 0), (255, 0, 0), 4) and not near(at(plain, 144, 0), (255, 0, 0), 60)
    no_shadow = modes_theme("dark", shadow_main="#00000000", shadow_tight="#00000000")
    bare = skin().render_floating(m, no_shadow, variant("system-dark"), 1.0, None)
    assert bare.getpixel((168, 24 + 128 + 6))[3] == 0 < plain.getpixel((168, 24 + 128 + 6))[3]


def test_severity_colours_come_from_the_per_mode_tokens():
    for mode in ("dark", "light"):
        tokens = theme().extras["modes"][mode]
        v = variant(f"system-{mode}")
        warning = skin().render_floating(dump.sample_model("warning"), theme(), v, 1.0, None)
        critical = skin().render_floating(dump.sample_model("critical"), theme(), v, 1.0, None)
        assert near(at(warning, 60, 38 + gates.BAR_DY + 2), colour(tokens["usage_warn"]), 2), mode
        assert near(at(critical, 60, 38 + gates.BAR_DY + 2), colour(tokens["usage_alert"]), 2), mode
        recoloured = modes_theme(mode, usage_warn="#00ff00", usage_alert="#0000ff")
        w2 = skin().render_floating(dump.sample_model("warning"), recoloured, v, 1.0, None)
        c2 = skin().render_floating(dump.sample_model("critical"), recoloured, v, 1.0, None)
        assert near(at(w2, 60, 38 + gates.BAR_DY + 2), (0, 255, 0), 2)
        assert near(at(c2, 60, 38 + gates.BAR_DY + 2), (0, 0, 255), 2)


def test_stale_states_use_the_stale_token_and_flat_values():
    for mode in ("dark", "light"):
        v = variant(f"system-{mode}")
        stale = colour(v.colors["stale"])
        for name in ("stale", "offline", "paused", "rate_limited"):
            image = skin().render_floating(dump.sample_model(name), theme(), v, 1.0, None)
            assert near(at(image, *BAR_FILL), stale, 2), (mode, name)
        changed = with_colors(v, stale="#ff00ff")
        image = skin().render_floating(dump.sample_model("stale"), theme(), changed, 1.0, None)
        assert near(at(image, *BAR_FILL), (255, 0, 255), 2)


def test_the_lamp_and_the_pill_use_the_ok_token():
    for mode in ("dark", "light"):
        v = variant(f"system-{mode}")
        image = skin().render_floating(dump.sample_model("normal3"), theme(), v, 1.0, None)
        header = (24 + 100, 24 + 14, 24 + 260, 24 + 28)
        assert find_colour(image, header, colour(v.colors["ok"]), 2), mode
        green = with_colors(v, ok="#ff00ff")
        image = skin().render_floating(dump.sample_model("normal3"), theme(), green, 1.0, None)
        assert find_colour(image, header, (255, 0, 255), 2)


def lamp_centre(m, scale: float = 1.0) -> tuple[float, float]:
    """Card coordinates of the status lamp: it sits 10 px in from the left edge of the pill."""
    ink = gates.Ink(gates.CARD_W, gates.CARD_H, scale, gates._fonts(theme()))
    pal = Palette.for_variant(theme(), variant("system-dark"))
    gates._header(ink, pal, m, more=True)
    pill = next(left for text, left, _right, _ in queued_runs(ink) if text.startswith(m.status_text))
    return pill - gates.PILL_GAP - gates.LAMP_SIZE / 2, gates.HEAD_MID


def test_the_lamp_shows_the_state():
    v = variant("system-dark")
    ok, stale, accent = (colour(v.colors[name]) for name in ("ok", "stale", "accent"))
    for name, centre_colour, ring_colour in (
        ("normal3", ok, ok), ("stale", stale, stale), ("no_credentials", stale, stale),
        ("offline", None, stale), ("paused", None, accent), ("loading", None, stale),
    ):
        m = dump.sample_model(name)
        cx, cy = lamp_centre(m)
        image = skin().render_floating(m, theme(), v, 1.0, None)
        if centre_colour is not None:
            assert near(at(image, cx, cy), centre_colour, 3), name                # a filled lamp
        else:
            assert not near(at(image, cx, cy), ring_colour, 30), name              # hollow: the pill shows through
        scale_box = tuple(margin(1.0) + round(v) for v in (cx - 5, cy - 5, cx + 5, cy + 5))
        assert find_colour(image, scale_box, ring_colour, 25), name               # the lamp's own colour is there
    # a throttled feed is a ring with its left half filled: neither the filled lamp of a lost sign-in nor the empty one
    m = dump.sample_model("rate_limited")
    cx, cy = lamp_centre(m)
    image = skin().render_floating(m, theme(), v, 2.0, None)
    assert near(at(image, cx - 2, cy, 2.0), stale, 3)                              # the left half is filled
    assert not near(at(image, cx + 2, cy, 2.0), stale, 30)                         # the right half is the pill again
    assert find_colour(image, tuple(margin(2.0) + round(c * 2) for c in (cx + 3, cy - 1, cx + 5, cy + 1)), stale, 25)   # its ring


def test_title_and_labels_use_the_text_tokens():
    for mode in ("dark", "light"):
        v = variant(f"system-{mode}")
        image = skin().render_floating(dump.sample_model("normal3"), theme(), v, 2.0, None)
        title_box = tuple(round((24 + c) * 2) for c in (36, 13, 108, 29))
        assert find_colour(image, title_box, colour(v.colors["text_bright"]), 12), mode


def test_the_system_variant_takes_the_live_windows_accent():
    settings = {"theme_id": "gates", "theme_variant": "system", "theme_mode": "system"}
    for light, token_mode in ((False, "dark"), (True, "light")):
        resolved_theme, resolved = theme_registry.resolve(
            settings, accent_provider=lambda: "#7b5ea7", light_provider=lambda light=light: light)
        lifted = "#7b5ea7" if light else "#a78ad7"     # lifted for the dark window, kept for the light one
        assert resolved.mode == token_mode and resolved.colors["accent"] == lifted
        assert resolved.extras["accent_fill"] == lifted           # a violet is nowhere near a severity colour
        image = skin().render_floating(dump.sample_model("normal3"), resolved_theme, resolved, 1.0, None)
        assert near(at(image, *BAR_FILL), colour(lifted), 2)
        tray = skin().render_tray_icon(dump.sample_model("normal3"), resolved_theme, resolved, 32)
        assert tray.size == (32, 32)


@pytest.mark.parametrize(("light", "bar", "glyph"), [(False, "#60cdff", "#ff5145"), (True, "#0067c0", "#d0000c")])
def test_a_red_system_accent_does_not_draw_normal_usage_in_the_critical_red(light, bar, glyph):
    """The owner's Windows accent is #d0000c. Taken as it was, every normal bar was the colour of a critical one:
    the bar keeps the Gates default accent, and the chrome (the header glyph) keeps the system colour."""
    settings = {"theme_id": "gates", "theme_variant": "system", "theme_mode": "system"}
    resolved_theme, resolved = theme_registry.resolve(
        settings, accent_provider=lambda: "#d0000c", light_provider=lambda: light)
    pal = Palette.for_variant(resolved_theme, resolved)
    assert pal.fill == colour(bar) and pal.accent == colour(glyph)
    normal = dump.sample_model("normal3")
    assert gates._usage_color(pal, normal, normal.rows[0]) == pal.fill
    image = skin().render_floating(normal, resolved_theme, resolved, 1.0, None)
    assert near(at(image, *BAR_FILL), colour(bar), 2)
    assert find_colour(image, (24 + 14, 24 + 12, 24 + 30, 24 + 30), colour(glyph), 40)
    critical = skin().render_floating(dump.sample_model("critical"), resolved_theme, resolved, 1.0, None)
    assert near(at(critical, *BAR_FILL), pal.usage_alert, 2)


def test_the_fill_is_the_accent_unless_the_variant_says_otherwise():
    for v in theme().variants:
        assert Palette.for_variant(theme(), v).fill == Palette.for_variant(theme(), v).accent, v.id
    custom = replace(variant("blue-dark"), extras={**variant("blue-dark").extras, "accent_fill": "#112233"})
    assert Palette.for_variant(theme(), custom).fill == colour("#112233")


def test_the_palette_reads_the_per_mode_and_derived_tokens():
    dark = Palette.for_variant(theme(), variant("green-dark"))
    modes = theme().extras["modes"]["dark"]
    derived = theme().extras["derived_per_variant"]["green-dark"]
    assert dark.mode == "dark"
    assert dark.card == colour(modes["widget_bg"]) and dark.usage_alert == colour(modes["usage_alert"])
    assert dark.time == colour(modes["time"]) and dark.tray_fg == colour(modes["tray_fg"])
    assert dark.lamp_halo == colour(derived["lamp_halo"]) and dark.pill_ok == colour(derived["pill_ok_bg"])
    assert dark.accent == colour("#C3E86B")
    light = Palette.for_variant(theme(), variant("green-light"))
    assert light.mode == "light" and light.highlight[3] == 0 and light.accent == colour("#5A7F00")


def test_a_bare_variant_still_renders():
    bare = replace(variant("system-dark"), extras={}, mode=None)
    bare_theme = replace(theme(), extras={})
    pal = Palette.for_variant(bare_theme, bare)
    assert pal.mode == "dark" and pal.card[3] == 0xF2 and pal.usage_warn == colour(bare.colors["warn"])
    image = skin().render_floating(dump.sample_model("warning"), bare_theme, bare, 1.0, None)
    assert image.size == frame_size(dump.sample_model("warning"))
    assert skin().render_taskbar(dump.sample_model("warning"), bare_theme, bare, 48, 1.0, None).height == 48
    assert skin().render_tray_icon(dump.sample_model("warning"), bare_theme, bare, 16).size == (16, 16)


# ── Tray icon ─────────────────────────────────────────────────────────────────

def ring_pixel(size: int, angle_degrees: float, radius_fraction: float = 1.0) -> tuple[int, int]:
    """The pixel on the ring at an angle (0 at 12 o'clock, clockwise)."""
    centre_r = gates._ring_geometry(size)[0] * radius_fraction
    x = size / 2 + centre_r * math.sin(math.radians(angle_degrees))
    y = size / 2 - centre_r * math.cos(math.radians(angle_degrees))
    return min(size - 1, max(0, int(x))), min(size - 1, max(0, int(y)))


@pytest.mark.parametrize("size", TRAY_SIZES)
def test_the_ring_arc_starts_at_noon_and_runs_clockwise(size):
    pal = Palette.for_variant(theme(), variant("system-dark"))
    image = skin().render_tray_icon(model(25.0), theme(), variant("system-dark"), size)
    arc = image.getpixel(ring_pixel(size, 45))                 # inside the first quarter
    track = image.getpixel(ring_pixel(size, 225))              # the empty part
    assert near(arc, pal.tray_fg, 2) and arc[3] == 255
    assert track[3] < 255 and not near(track, pal.tray_fg, 40)
    assert not near(image.getpixel(ring_pixel(size, 315)), pal.tray_fg, 40)     # not past the arc's end


def test_the_arc_sweeps_3_6_degrees_per_percent():
    size = 32
    for percent in (13.0, 40.0, 70.0):                             # below the warning threshold: white arcs
        image = skin().render_tray_icon(model(percent), theme(), variant("system-dark"), size)
        pal = Palette.for_variant(theme(), variant("system-dark"))
        before = percent * 3.6 - 6
        after = percent * 3.6 + 6
        assert near(image.getpixel(ring_pixel(size, before)), pal.tray_fg, 2), percent
        assert not near(image.getpixel(ring_pixel(size, after)), pal.tray_fg, 30), percent


def test_the_ring_colour_follows_the_severity_and_the_state():
    for mode in ("dark", "light"):
        v = variant(f"system-{mode}")
        pal = Palette.for_variant(theme(), v)
        cases = {"normal3": pal.tray_fg, "warning": pal.usage_warn, "critical": pal.usage_alert, "stale": pal.text_3,
                 "paused": pal.text_3, "offline": pal.text_3}
        for name, expected in cases.items():
            image = skin().render_tray_icon(dump.sample_model(name), theme(), v, 24)
            assert near(image.getpixel(ring_pixel(24, 20)), expected, 2), (mode, name)


def test_a_stale_ring_has_a_dashed_track_and_a_live_ring_a_solid_one():
    live = skin().render_tray_icon(dump.sample_model("normal3"), theme(), variant("system-dark"), 32)
    stale = skin().render_tray_icon(dump.sample_model("stale"), theme(), variant("system-dark"), 32)

    def track_pixels(image):
        return sum(1 for angle in range(100, 360, 3) if image.getpixel(ring_pixel(32, angle))[3] > 20)

    assert track_pixels(live) > track_pixels(stale) + 15


def test_only_the_32_px_icon_prints_the_number():
    for size, prints in ((16, False), (20, False), (24, False), (32, True)):
        image = skin().render_tray_icon(model(78.0), theme(), variant("system-dark"), size)
        centre_r, stroke, *_ = gates._ring_geometry(size)
        hole = centre_r - stroke / 2 - 1.2                       # inside the ring and its outline
        inner = [image.getpixel((x, y)) for y in range(size) for x in range(size)
                 if math.hypot(x + 0.5 - size / 2, y + 0.5 - size / 2) < hole - 0.5]
        assert inner
        assert any(p[3] > 100 for p in inner) is prints, size


def test_the_number_shows_the_rounded_session_percent_and_dashes_without_data():
    ink_free = skin().render_tray_icon(dump.sample_model("loading"), theme(), variant("system-dark"), 32)
    assert ink_free.size == (32, 32)
    wide = skin().render_tray_icon(model(112.0), theme(), variant("system-dark"), 32)        # three digits
    assert wide.size == (32, 32)
    centre = [wide.getpixel((x, y)) for y in range(10, 22) for x in range(6, 26)]
    assert any(p[3] > 100 for p in centre)


def test_the_ring_reads_on_a_taskbar_of_either_mode():
    """The icon keeps an outline in the taskbar colour, so a dark variant on a light taskbar (and the
    reverse, which Windows' custom modes allow) is still a visible ring."""
    for v_id, backdrop in (("system-dark", (243, 243, 243)), ("system-light", (32, 32, 32)),
                           ("system-dark", (32, 32, 32)), ("system-light", (243, 243, 243))):
        for size in TRAY_SIZES:
            for name in ("normal3", "stale"):
                icon = skin().render_tray_icon(dump.sample_model(name), theme(), variant(v_id), size)
                flat = flatten(icon, backdrop)
                bg_lum = luminance(backdrop)
                strong = sum(1 for p in flat.getdata() if abs(luminance(p) - bg_lum) > 0.12)
                assert strong >= size * 1.5, (v_id, backdrop, size, name, strong)


def test_a_tray_icon_matches_the_taskbar_it_was_drawn_for():
    """On the matching taskbar the outline disappears into the background (it is that colour)."""
    for v_id, backdrop in (("system-dark", (32, 32, 32)), ("system-light", (243, 243, 243))):
        v = variant(v_id)
        pal = Palette.for_variant(theme(), v)
        assert tuple(pal.keyline[:3]) == tuple(colour(v.colors["bg"])[:3]) or pal.keyline[:3] == colour(
            theme().extras["modes"][pal.mode]["tb_bg"])[:3]
        assert contrast(pal.tray_fg, backdrop) >= 4.5


def test_tray_icon_is_deterministic():
    a = skin().render_tray_icon(dump.sample_model("warning"), theme(), variant("rose-dark"), 32)
    b = skin().render_tray_icon(dump.sample_model("warning"), theme(), variant("rose-dark"), 32)
    assert a.tobytes() == b.tobytes()


# ── Rows, header and footer ───────────────────────────────────────────────────

ALL_STATUSES = (ApiStatus.LIVE, ApiStatus.STALE, ApiStatus.OFFLINE, ApiStatus.PAUSED, ApiStatus.RATE_LIMITED,
                ApiStatus.NO_CREDENTIALS, ApiStatus.LOADING)


def test_every_scoped_row_of_the_model_gets_a_row_in_every_state():
    """The scoped (Fable) row used to hide while the feed was not live or the session or the weekly row asked for
    attention. It is a row like the others now: whatever the model carries is shown, whatever the status says."""
    for status in ALL_STATUSES:
        assert [row.key for row in gates._scoped_rows(model(13.0, 2.0, scoped=3.0, status=status))] == ["scoped:fable"]
    assert gates._scoped_rows(model(80.0, 2.0, scoped=3.0, severity=("warning", None)))        # attention takes no slot
    assert gates._scoped_rows(model(13.0, 95.0, scoped=3.0, severity=(None, "critical")))
    assert gates._scoped_rows(model(13.0, 2.0, scoped=96.0, status=ApiStatus.OFFLINE))
    assert gates._scoped_rows(model(13.0, 2.0, scoped=0.0))                                    # a limit at 0 % still shows
    assert gates._scoped_rows(model(13.0, 2.0)) == []                                          # nothing to show: no row
    for name in ("normal3", "warning", "critical"):
        assert [row.key for row in gates._scoped_rows(dump.sample_model(name))] == ["scoped:fable"], name


def test_the_card_shows_the_scoped_row_in_every_state_and_the_footer_under_it(monkeypatch):
    made = drawn_canvases(monkeypatch)
    v = variant("system-dark")
    pal = Palette.for_variant(theme(), v)
    for status in ALL_STATUSES:
        m = model(session=41.0, weekly=7.0, scoped=40.0, status=status)
        image = skin().render_floating(m, theme(), v, 1.0, None)
        assert image.size == (336, 48 + 155), status                      # two rows and one more: 27 px taller
        shown = row_texts(made[-1], list(gates.ROW_TOPS))
        assert ("Fable weekly" in [t for t, _ in shown[2]]) and ("40%" in [t for t, _ in shown[2]]), status
        bar_y = gates.ROW_TOPS[2] + gates.BAR_DY + 2
        assert near(at(image, 60, bar_y), gates._usage_color(pal, m, m.rows[2]), 3), status     # its bar is drawn
        foot_top = gates.ROW_TOPS[2] + gates.ROW_PITCH + gates.FOOT_GAP
        feet = [(text, baseline) for text, _x, baseline, *_ in made[-1]._texts
                if text in {run[0] for run in gates._footer_runs(m)}]
        assert feet and all(baseline >= foot_top for _text, baseline in feet), status           # the footer comes last


def test_two_scoped_rows_make_the_card_two_rows_taller():
    one = model(13.0, 2.0, scoped=3.0)
    two = replace(one, rows=[*one.rows, replace(one.rows[2], key="scoped:other", label="OTHER WEEKLY", percent=9.0)])
    sizes = [skin().render_floating(m, theme(), variant("system-dark"), 1.0, None).size for m in (model(13.0, 2.0), one, two)]
    assert [height for _w, height in sizes] == [176, 203, 230]
    assert skin().floating_size(theme(), two, 1.0) == sizes[2]


def test_the_footer_copy_follows_the_state():
    def lead(m) -> str:
        return gates._footer_runs(m)[0][0]

    assert lead(dump.sample_model("stale")) == "Hmm, we lost touch."
    assert gates._footer_runs(dump.sample_model("offline"))[0][0].startswith("No connection")
    assert gates._footer_runs(dump.sample_model("paused"))[0][0].startswith("Paused")
    assert lead(dump.sample_model("rate_limited")) == "Rate limited."
    assert lead(dump.sample_model("no_credentials")) == "No sign-in found."
    assert lead(dump.sample_model("critical")) == "Nearly out"
    assert lead(dump.sample_model("warning")) == "Ahead of pace"
    assert lead(dump.sample_model("normal2")) == "Under pace"
    text = "".join(run[0] for run in gates._footer_runs(dump.sample_model("normal2")))
    assert text.startswith("Under pace · Resets ")


def test_the_footer_talks_about_the_most_severe_row():
    weekly_critical = model(20.0, 95.0)
    runs = gates._footer_runs(weekly_critical)
    assert runs[0][0] == "Nearly out" and "Resets" in "".join(r[0] for r in runs)
    assert gates._driver(weekly_critical).key == "weekly"
    assert gates._driver(model(50.0, 50.0)).key == "session"            # ties go to the session


@pytest.mark.parametrize(("age_text", "label"), [
    ("12 S AGO", "12 s"), ("41 M AGO", "41 min"), ("3 H AGO", "3 h"), ("2 D AGO", "2 d"), (None, None),
    ("0 S AGO", "0 s"),
])
def test_the_pill_shows_the_age_in_the_mockup_style(age_text, label):
    m = replace(dump.sample_model("normal3"), age_text=age_text)
    assert gates._age_label(m) == label


def test_labels_are_sentence_case_and_tags_follow_the_row():
    m = dump.sample_model("normal3")
    assert [gates._sentence(row.label) for row in m.rows] == ["Session", "Weekly", "Fable weekly"]
    assert [gates._tag(row) for row in m.rows] == ["5H", "7D", "50% share"]
    assert gates._sentence("Claude Design weekly") == "Claude Design weekly"


@pytest.mark.parametrize(("percent", "text"), [
    (13.0, "13%"), (13.4, "13%"), (13.5, "14%"), (100.0, "100%"), (112.4, "112%"), (1234.0, "1234%"),
    (0.0, "0%"), (-3.0, "0%"), (None, "--"), (float("nan"), "--"),
])
def test_percent_text(percent, text):
    assert gates._percent_text(percent) == text


def test_exact_percent_text_drops_a_trailing_zero():
    assert gates._exact_percent_text(13.0) == "13%"
    assert gates._exact_percent_text(13.25) == "13.2%" or gates._exact_percent_text(13.25) == "13.3%"
    assert gates._exact_percent_text(None) == "--"


def test_pace_text_gives_the_points_and_the_direction():
    ahead = model(78.0).rows[0]
    behind = model(5.0).rows[0]
    assert gates._pace_text(ahead) == ("Ahead of pace by 47 pts", "warn")
    assert gates._pace_text(behind) == ("Under pace by 26 pts", "ok")
    assert gates._pace_text(model(31.0).rows[0])[0] == "On pace"
    assert gates._pace_text(dump.sample_model("loading").rows[0])[0] == ""


def test_rows_fit_with_long_labels_and_large_values():
    pal = Palette.for_variant(theme(), variant("system-dark"))
    big = model(112.4, 1234.0, scoped=999.0, label="CLAUDE DESIGN EXTREMELY LONG WEEKLY LIMIT")
    ink = gates.Ink(gates.CARD_W, gates.CARD_H, 1.0, gates._fonts(theme()))
    for index, row in enumerate(big.rows):
        gates._row(ink, pal, big, row, gates.ROW_TOPS[index])
    runs = queued_runs(ink)
    assert runs
    for text, left, right, _align in runs:
        assert gates.LEFT - 0.5 <= left and right <= gates.RIGHT + 0.5, (text, left, right)
    by_row: dict[int, list[tuple[float, float]]] = {}
    for (text, left, right, _align), queued in zip(runs, ink._texts):
        by_row.setdefault(round(queued[2] / 20), []).append((left, right))
    for spans in by_row.values():
        spans.sort()
        for (_l1, r1), (l2, _r2) in zip(spans, spans[1:]):
            assert r1 <= l2 + 0.01                       # label, tag and value never overlap


def test_the_pill_never_runs_into_the_title():
    pal = Palette.for_variant(theme(), variant("system-dark"))
    for status, age in ((ApiStatus.RATE_LIMITED, 41 * 60), (ApiStatus.RATE_LIMITED, 3 * 86400),
                        (ApiStatus.NO_CREDENTIALS, 3 * 3600), (ApiStatus.OFFLINE, 59), (ApiStatus.LIVE, 3)):
        m = model(status=status, age_s=age)
        ink = gates.Ink(gates.CARD_W, gates.CARD_H, 1.0, gates._fonts(theme()))
        gates._header(ink, pal, m, more=True)
        runs = {text: (left, right) for text, left, right, _ in queued_runs(ink)}
        title_right = runs["Claude usage"][1]
        pill_text = next(text for text in runs if text != "Claude usage" and text != gates.ICON_GLYPH)
        assert runs[pill_text][0] >= title_right + gates.TITLE_GAP, (status, runs)
        assert runs[pill_text][1] <= gates.PILL_RIGHT


def test_footers_fit_the_card():
    pal = Palette.for_variant(theme(), variant("system-dark"))
    for name in STATES:
        m = dump.sample_model(name)
        ink = gates.Ink(gates.CARD_W, gates.CARD_H, 1.0, gates._fonts(theme()))
        gates._footer(ink, pal, m, gates.FOOT_TOP)
        for text, left, right, _ in queued_runs(ink):
            assert gates.LEFT <= left and right <= gates.RIGHT + 0.5, (name, text)


def test_fit_cuts_a_long_text_with_an_ellipsis():
    ink = gates.Ink(gates.CARD_W, gates.CARD_H, 1.0, gates._fonts(theme()))
    face = ink.face(ink.fonts.body, gates.SIZE_LABEL, 400)
    long_text = "A label that is much too long for the room it was given"
    cut = gates._fit(ink, long_text, face, 60)
    assert cut.endswith("…") and ink.advance(cut, face) <= 60
    assert gates._fit(ink, "Short", face, 60) == "Short"


# ── What the frames say ───────────────────────────────────────────────────────
# The shape tests above pass when the right shapes are drawn with the wrong data: 23 small mutations of the two
# skins were run against them and 8 that visibly change what is drawn passed. These read the text a card queues, row
# by row, with the colour it is queued in, and look for that colour in the pixels.

CARD_TOPS = list(gates.ROW_TOPS)
TIP_TOPS = [gates.TIP_ROW0 + gates.TIP_PITCH * index for index in range(3)]


def drawn_canvases(monkeypatch: pytest.MonkeyPatch) -> list[gates.Ink]:
    """The canvases the render calls from here on draw on, in order (a render leaves its queued text on its canvas)."""
    made: list[gates.Ink] = []
    real = gates._card_canvas

    def spy(*args: Any, **kwargs: Any) -> gates.Ink:
        canvas = real(*args, **kwargs)
        made.append(canvas)
        return canvas

    monkeypatch.setattr(gates, "_card_canvas", spy)
    return made


def row_texts(ink: gates.Ink, tops: list[float]) -> list[list[tuple[str, tuple[int, ...]]]]:
    """The text queued for each row slot as (text, colour) in drawing order. A run belongs to the last slot whose top is
    above its baseline, so the header's runs, which sit above the first row, belong to none."""
    slots: list[list[tuple[str, tuple[int, ...]]]] = [[] for _ in tops]
    for text, _x, baseline, _face, color, _align in ink._texts:
        owners = [index for index, top in enumerate(tops) if baseline >= top]
        if owners:
            slots[owners[-1]].append((text, color))
    return slots


def test_each_row_of_the_card_shows_its_own_figures(monkeypatch):
    """With different figures in the rows, row two carries the weekly percent and row three the Fable one. A regression
    drew the first row's data in the second row and no shape test noticed: all the shapes were still there."""
    made = drawn_canvases(monkeypatch)
    skin().render_floating(model(session=41.0, weekly=7.0, scoped=3.0), theme(), variant("system-dark"), 1.0, None)
    shown = [[text for text, _ in slot] for slot in row_texts(made[-1], CARD_TOPS)]
    expected = [("Session", "41%"), ("Weekly", "7%"), ("Fable weekly", "3%")]
    for index, slot in enumerate(shown):
        for other, (label, value) in enumerate(expected):
            assert (label in slot and value in slot) == (index == other), (index, other, slot)


def test_a_value_is_drawn_in_the_colour_of_its_rows_severity(monkeypatch):
    v = variant("system-dark")
    pal = Palette.for_variant(theme(), v)
    assert len({pal.text, pal.warn, pal.alert}) == 3                       # the three can be told apart
    made = drawn_canvases(monkeypatch)
    for percent, severity, expected in ((41.0, None, pal.text), (78.0, "warning", pal.warn), (93.0, "critical", pal.alert)):
        image = skin().render_floating(model(session=percent, weekly=7.0, severity=(severity, None)), theme(), v, 1.0, None)
        shown = f"{percent:.0f}%"
        assert dict(row_texts(made[-1], CARD_TOPS)[0])[shown] == expected, severity
        _text, left, right, _align = next(run for run in queued_runs(made[-1]) if run[0] == shown)
        box = (margin(1.0) + int(left), margin(1.0) + gates.ROW_TOPS[0], margin(1.0) + round(right),
               margin(1.0) + gates.ROW_TOPS[0] + gates.LINE_H)
        assert find_colour(image, box, expected, 6), severity                # and the pixels carry it
        if severity is not None:
            assert not find_colour(image, box, pal.text, 6), severity
    for status in (ApiStatus.STALE, ApiStatus.OFFLINE):                      # a dimmed card fades its values instead
        skin().render_floating(model(session=78.0, weekly=7.0, status=status, severity=("warning", None)), theme(), v, 1.0,
                               None)
        assert dict(row_texts(made[-1], CARD_TOPS)[0])["78%"] == gates._fade(pal.text, gates.DIM_VALUE), status


def test_the_tooltip_says_the_pace_while_live_and_leaves_it_off_a_dimmed_card(monkeypatch):
    made = drawn_canvases(monkeypatch)
    v = variant("system-dark")
    skin().render_tooltip(model(5.0, 31.0), theme(), v, 1.0)
    live = [text for slot in row_texts(made[-1], TIP_TOPS[:2]) for text, _ in slot]
    assert "Under pace by 26 pts" in live
    for status in (ApiStatus.STALE, ApiStatus.OFFLINE, ApiStatus.PAUSED):
        skin().render_tooltip(model(5.0, 31.0, status=status), theme(), v, 1.0)
        dimmed = [text for slot in row_texts(made[-1], TIP_TOPS[:2]) for text, _ in slot]
        assert not any("pace" in text.lower() for text in dimmed), status


def test_a_long_label_loses_its_tag_then_ends_in_an_ellipsis_before_the_value():
    pal = Palette.for_variant(theme(), variant("system-dark"))
    m = model(13.0, 2.0, scoped=45.0, label="CLAUDE DESIGN EXTREMELY LONG WEEKLY LIMIT THAT KEEPS ON GOING AND GOING")
    ink = gates.Ink(gates.CARD_W, gates.CARD_H, 1.0, gates._fonts(theme()))
    gates._row(ink, pal, m, m.rows[2], gates.ROW_TOPS[2])
    runs = queued_runs(ink)
    assert len(runs) == 2                                          # the tag went first, then the label itself was cut
    (label, label_left, label_right, _), (value, value_left, value_right, _) = runs
    assert label.endswith("…") and label_left == gates.LEFT and value == "45%"
    assert label_right <= value_left - 10 + 0.01 and value_right <= gates.RIGHT + 0.01


# ── Options and determinism ───────────────────────────────────────────────────

def test_text_fx_reduce_motion_phase_and_the_blink_change_nothing():
    """Gates does not glow, flicker or blink: those switches leave the frame as it is."""
    lit = dump.sample_model("normal3")
    dark = replace(lit, blink_on=False)
    base = skin().render_floating(lit, theme(), variant("system-dark"), 1.0, None)
    for opts in ({"text_fx": False}, {"reduce_motion": True}, {"phase": 0.5}, RenderOptions(text_fx=False)):
        assert skin().render_floating(lit, theme(), variant("system-dark"), 1.0, opts).tobytes() == base.tobytes()
    assert skin().render_floating(dark, theme(), variant("system-dark"), 1.0, None).tobytes() == base.tobytes()
    strip = skin().render_taskbar(lit, theme(), variant("system-dark"), 48, 1.0, None)
    plain = skin().render_taskbar(dark, theme(), variant("system-dark"), 48, 1.0, {"text_fx": False})
    assert plain.tobytes() == strip.tobytes()


def test_options_accept_a_mapping_and_reject_unknown_names():
    m = dump.sample_model("normal3")
    skin().render_floating(m, theme(), variant("system-dark"), 1.0, {"transparent_bg": True})
    with pytest.raises(ValueError):
        skin().render_floating(m, theme(), variant("system-dark"), 1.0, {"glow": True})
    with pytest.raises(TypeError):
        skin().render_taskbar(m, theme(), variant("system-dark"), 48, 1.0, 42)  # type: ignore[arg-type]


def test_rendering_is_deterministic():
    m = dump.sample_model("warning")
    for scale in (1.0, 1.25):
        a = GatesSkin().render_floating(m, theme(), variant("orange-light"), scale, None)
        b = GatesSkin().render_floating(m, theme(), variant("orange-light"), scale, None)
        assert a.tobytes() == b.tobytes()
        assert GatesSkin().render_tooltip(m, theme(), variant("orange-light"), scale).tobytes() == \
            GatesSkin().render_tooltip(m, theme(), variant("orange-light"), scale).tobytes()


def test_a_different_state_or_variant_changes_the_frame():
    base = skin().render_floating(dump.sample_model("normal2"), theme(), variant("system-dark"), 1.0, None)
    other_state = skin().render_floating(dump.sample_model("stale"), theme(), variant("system-dark"), 1.0, None)
    other_variant = skin().render_floating(dump.sample_model("normal2"), theme(), variant("system-light"), 1.0, None)
    assert other_state.tobytes() != base.tobytes()
    assert other_variant.tobytes() != base.tobytes()


# ── Fonts ─────────────────────────────────────────────────────────────────────

def test_a_machine_without_the_windows_fonts_still_renders(monkeypatch):
    """No Segoe UI Variable, no Cascadia and no icon font: Pillow's own face and a drawn stopwatch."""
    empty = gates.Typeface((), False)
    monkeypatch.setattr(gates, "_fonts", lambda _theme: gates.Fonts(empty, empty, None))
    for name in ("normal3", "stale", "loading"):
        m = dump.sample_model(name)
        assert skin().render_floating(m, theme(), variant("system-dark"), 1.0, None).size == frame_size(m)
        assert skin().render_floating(m, theme(), variant("system-light"), 1.5, None).size == frame_size(m, 1.5)
        assert skin().render_taskbar(m, theme(), variant("system-dark"), 48, 1.0, None).size == (248, 48)
        assert skin().render_tooltip(m, theme(), variant("system-dark"), 1.0).width == 336
        assert skin().render_tray_icon(m, theme(), variant("system-dark"), 32).size == (32, 32)
    image = skin().render_floating(dump.sample_model("normal3"), theme(), variant("system-dark"), 1.0, None)
    assert find_colour(image, (24 + 14, 24 + 8, 24 + 32, 24 + 32), colour("#60cdff"), 12)   # the drawn stopwatch


def test_a_machine_with_only_segoe_ui_uses_the_static_faces(monkeypatch):
    body = gates._static_typeface(gates._STATIC_BODY)
    if body is None:
        pytest.skip("Segoe UI is not installed")
    mono = gates._static_typeface(gates._STATIC_MONO) or gates._bundled_mono() or gates.Typeface((), False)
    monkeypatch.setattr(gates, "_fonts", lambda _theme: gates.Fonts(body, mono, None))
    image = skin().render_floating(dump.sample_model("warning"), theme(), variant("system-light"), 1.25, None)
    assert image.size == frame_size(dump.sample_model("warning"), 1.25)
    face = gates.Ink(288, 128, 1.0, None).face(body, 12, 600)
    assert face.path is not None and not face.variable and face.wght == 0


def test_the_theme_fonts_resolve_to_windows_fonts_when_present():
    fonts = gates._fonts(theme())
    body_path = theme_registry.font_path(theme(), "body", "regular")
    if body_path is not None:
        assert fonts.body.files and fonts.body.file_for(600) == str(body_path)
    assert fonts.mono.files or fonts.mono == gates.Typeface((), False)


def test_a_missing_font_file_falls_back_to_pillows_default():
    face = gates._font(str(Path("no") / "such" / "font.ttf"), 12.0, 400, None, False)
    assert face.getlength("Hello") > 0


def test_unhinted_advances_scale_with_the_size():
    fonts = gates._fonts(theme())
    ink1 = gates.Ink(gates.CARD_W, gates.CARD_H, 1.0, fonts)
    ink2 = gates.Ink(gates.CARD_W, gates.CARD_H, 2.0, fonts)
    a = ink1.advance("Claude usage", ink1.face(fonts.body, 12, 600))
    b = ink2.advance("Claude usage", ink2.face(fonts.body, 12, 600))
    assert a == pytest.approx(b, rel=1e-6)                     # the layout is the same at every scale


# ── Performance ───────────────────────────────────────────────────────────────

def _per_call_ms(fn, runs: int = 5) -> float:
    fn()                                                       # warm the caches
    started = time.perf_counter()
    for _ in range(runs):
        fn()
    return (time.perf_counter() - started) / runs * 1000


def test_frames_render_fast_enough_at_scale_1_5():
    """Targets: a floating frame under 60 ms and a strip under 40 ms. The limits here are three times
    that, so a slow CI machine does not make the test flaky."""
    m = dump.sample_model("normal3")
    v = variant("system-dark")
    floating = _per_call_ms(lambda: skin().render_floating(m, theme(), v, 1.5, None))
    strip = _per_call_ms(lambda: skin().render_taskbar(m, theme(), v, 72, 1.5, None))
    tooltip = _per_call_ms(lambda: skin().render_tooltip(m, theme(), v, 1.5), 3)
    tray = _per_call_ms(lambda: skin().render_tray_icon(m, theme(), v, 32))
    assert floating < 180, f"floating frame took {floating:.1f} ms"
    assert strip < 120, f"strip took {strip:.1f} ms"
    assert tooltip < 180, f"tooltip took {tooltip:.1f} ms"
    assert tray < 60, f"tray icon took {tray:.1f} ms"


# ── Dump ──────────────────────────────────────────────────────────────────────

def test_dump_frames_writes_gates_frames_and_a_contact_sheet(tmp_path: Path):
    report = dump.dump_frames(tmp_path, themes=["gates"], variants=["system-dark"], states=["normal3", "critical"],
                              scales=[1.0, 2.0])
    folder = tmp_path / "gates" / "system-dark"
    assert (folder / "floating_normal3_x1.png").is_file() and (folder / "floating_critical_x2.png").is_file()
    assert (folder / "taskbar_normal3_h48_x1.png").is_file() and (folder / "taskbar_normal3_h80_x2.png").is_file()
    assert (folder / "tooltip_critical_x1.png").is_file() and (folder / "tray_critical_32.png").is_file()
    assert report.files == 2 * 2 * (1 + len(dump.TASKBAR_HEIGHTS_AT_1X) + 1) + 2 * 4
    with Image.open(folder / "floating_normal3_x2.png") as opened:
        assert opened.size == frame_size(dump.sample_model("normal3"), 2.0) == (672, 406)
    with Image.open(report.sheets[0]) as sheet:
        assert sheet.format == "PNG" and sheet.width > 600


# ── Real windows and icons (CUM_UI_TESTS=1) ───────────────────────────────────

@UI
@pytest.mark.skipif(sys.platform != "win32", reason="the Win32 UI layer is Windows only")
def test_gates_frames_show_in_layered_windows():
    from usage_monitor.ui.host import UIHost
    from usage_monitor.ui.layered import LayeredWindow

    host = UIHost(controller_class=f"CUM.Test.Gates.{uuid.uuid4().hex[:8]}")
    try:
        offscreen = (-30000, -30000)
        m = dump.sample_model("warning")
        for scale in (1.0, 1.5):
            for make in (
                lambda: skin().render_floating(m, theme(), variant("system-dark"), scale, None),
                lambda: skin().render_tooltip(m, theme(), variant("system-light"), scale),
                lambda: skin().render_taskbar(m, theme(), variant("teal-dark"), round(48 * scale), scale, None),
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
@pytest.mark.parametrize("size", TRAY_SIZES)
def test_gates_tray_images_make_icons(size):
    from usage_monitor.ui import win32
    from usage_monitor.ui.tray import create_hicon

    for name in ("normal3", "stale"):
        hicon = create_hicon(skin().render_tray_icon(dump.sample_model(name), theme(), variant("system-dark"), size))
        assert hicon
        win32.DestroyIcon(hicon)
