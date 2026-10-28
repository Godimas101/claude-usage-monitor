"""Tests for the usage guard (#27) on the widgets: the floating widget, the taskbar strip and the hover card of the three
skins (usage_monitor/render/skins/). Everything renders to memory, so none of it needs a desktop and all of it runs in CI.

What they hold the skins to:

* with the guard off a frame is what it was (the model gives None, and the skins' guard-free paths are the old ones);
* the guard changes no size and shifts nothing: a floating frame differs from the guard-off one only in the header, on
  the rows the guard holds on and, on the Gates card, in the footer line; armed leaves the strip alone; the hover card
  grows by exactly the guard's block and keeps its width;
* armed is quiet and holding is unmistakable: a hold chip with the countdown, the over-cap rows marked, the cap named;
* a stale frame is drawn flat: the header carries the guard and no row is marked;
* a mark never runs into what it sits beside, at any scale, in any status, and gives way in the order the code says;
* the colours come from the variant's tokens, and the marks do not blink.

Pixel checks look at where things are; text is checked through the runs the skin draws (positions and widths), so
they hold with whatever font the machine has.
"""
from __future__ import annotations

import dataclasses
import math
from dataclasses import replace
from datetime import timedelta
from functools import lru_cache
from typing import Any

import pytest
from PIL import Image, ImageChops

from usage_monitor.agent import guard
from usage_monitor.data.models import ApiStatus, Limit
from usage_monitor.render import dump, engine
from usage_monitor.render.model import WidgetModel, build_model
from usage_monitor.render.skins import RenderOptions, gates, get_skin, night_city, nostromo

THEME_IDS = ("nostromo", "night_city", "gates")
SCALES = (1.0, 1.25, 1.5, 2.0)
NOW = dump.SAMPLE_NOW
LIVE = ("normal3", "normal2", "warning", "critical")
NOT_LIVE = ("stale3", "offline3", "paused3", "rate_limited3", "stale", "offline", "paused", "rate_limited")
WITH_DATA = LIVE + NOT_LIVE                         # the sample states that carry a reading for the guard to judge
NO_FX = {"text_fx": False}                          # no glow, so nothing spills a few pixels into the next row


# ── Helpers ───────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def loaded() -> dict[str, Any]:
    return dump.load_theme_objects()


def theme_of(theme_id: str) -> Any:
    return loaded()[theme_id]


def variants_of(theme_id: str) -> list[Any]:
    return list(theme_of(theme_id).variants)


def picked(theme_id: str, which: str) -> Any:
    """The theme's default variant, or another one (the last: purple, mox, green-light)."""
    return theme_of(theme_id).variant(None) if which == "default" else variants_of(theme_id)[-1]


def guarded(name: str, kind: str, **policy: Any) -> WidgetModel:
    """The sample state ``name`` under the real guard (agent/guard.py): "armed" with the default caps, or "holding" with
    the session capped at 10 %, so its row is over the cap whatever the sample's figure and severity are."""
    state = dump.sample_state(name)
    caps = {**dump.GUARD_SAMPLE_POLICY, **({"session_cap": 10} if kind == "holding" else {}), **policy}
    verdict = guard.evaluate(guard.limits_doc(state.usage), caps, NOW)
    return build_model(dataclasses.replace(state, guard=verdict), NOW)


def custom(session: float = 38.0, weekly: float = 52.0, fable: float | None = 4.0, *, label: str = "FABLE WEEKLY",
           model: str | None = "Fable", session_in: timedelta = timedelta(hours=3, minutes=27),
           weekly_in: timedelta = timedelta(days=3), show_scoped: str = "auto", blink_on: bool = True,
           status: ApiStatus = ApiStatus.LIVE, **policy: Any) -> WidgetModel:
    """A live model with chosen readings under a guard with the default caps and ``policy`` on top."""
    limits = [
        Limit(kind="session", group="session", percent=session, severity=None, resets_at=NOW + session_in,
              is_active=True, label="SESSION", window_seconds=18000),
        Limit(kind="weekly_all", group="weekly", percent=weekly, severity=None, resets_at=NOW + weekly_in,
              is_active=True, label="WEEKLY", window_seconds=604800),
    ]
    if fable is not None:
        limits.append(Limit(kind="weekly_scoped", group="weekly", percent=fable, severity=None,
                            resets_at=NOW + weekly_in, is_active=True, label=label, scope_model=model,
                            window_seconds=604800, allowance_pct=50.0))
    state = dump._state(NOW, status, limits, 12)
    verdict = guard.evaluate(guard.limits_doc(state.usage), {**dump.GUARD_SAMPLE_POLICY, **policy}, NOW)
    return build_model(dataclasses.replace(state, guard=verdict), NOW, show_scoped=show_scoped, blink_on=blink_on)


def draw(theme_id: str, variant: Any, model: WidgetModel, scale: float, surface: str, opts: Any = None,
         bar: int = 48) -> Image.Image:
    skin, theme = get_skin(theme_id), theme_of(theme_id)
    if surface == "floating":
        return skin.render_floating(model, theme, variant, scale, opts)
    if surface == "taskbar":
        return skin.render_taskbar(model, theme, variant, round(bar * scale), scale, opts)
    return skin.render_tooltip(model, theme, variant, scale)


def changed(a: Image.Image, b: Image.Image) -> Image.Image:
    """A mask of the pixels where two frames of one size differ in colour or coverage."""
    assert a.size == b.size, (a.size, b.size)
    mask = None
    for band in ImageChops.difference(a.convert("RGBA"), b.convert("RGBA")).split():
        lit = band.point(lambda v: 255 if v else 0)
        mask = lit if mask is None else ImageChops.lighter(mask, lit)
    return mask


def outside(mask: Image.Image, boxes: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int] | None:
    """The box that holds the changed pixels left once ``boxes`` are set aside, or None when there are none."""
    rest = mask.copy()
    for box in boxes:
        rest.paste(0, box)
    return rest.getbbox()


def floating_boxes(theme_id: str, scale: float, model: WidgetModel, *, rows: tuple[int, ...] = (),
                   footer: bool = False) -> list[tuple[int, int, int, int]]:
    """Pixel boxes of a floating frame the guard may change: the header, the rows it holds on and, on the Gates card
    while the status is live, the footer line. A pixel of slack on every side."""
    dx = dy = 0
    if theme_id == "nostromo":
        w, found = nostromo.FLOAT_W, [(0, 0, nostromo.FLOAT_W, 22)]
        found += [(0, nostromo.ROW_TOP + nostromo.ROW_PITCH * i - 5, w, nostromo.ROW_TOP + nostromo.ROW_PITCH * (i + 1) - 3)
                  for i in rows]
    elif theme_id == "night_city":
        w, found = night_city.FLOAT_W, [(0, 0, night_city.FLOAT_W, 26)]
        found += [(0, night_city.ROW_TOP + night_city.ROW_PITCH * i - 2, w,
                   night_city.ROW_TOP + night_city.ROW_PITCH * (i + 1) - 1) for i in rows]
    else:
        dx = dy = round(gates.MARGIN * scale)
        limits = 2 + len(gates._scoped_rows(model))
        w, found = gates.CARD_W, [(0, 0, gates.CARD_W, 34)]
        found += [(0, gates._row_top(i) - 1, w, gates._row_top(i) + gates.ROW_PITCH) for i in rows]
        if footer:
            found.append((0, gates._row_top(limits) + gates.FOOT_GAP - 2, w, gates._card_height(limits)))
    return [(dx + math.floor(x0 * scale) - 1, dy + math.floor(y0 * scale) - 1,
             dx + math.ceil(x1 * scale) + 1, dy + math.ceil(y1 * scale) + 1) for x0, y0, x1, y1 in found]


def plain_text(text: str) -> str:
    """A guard sentence as it reads: the unbreakable spaces the skins glue its words with are plain spaces."""
    return text.replace(engine.NO_BREAK, " ")


def colour(hex_value: str) -> tuple[int, int, int, int]:
    return engine.parse_color(hex_value)


def near(actual: tuple[int, ...], expected: tuple[int, ...], tolerance: int = 3) -> bool:
    return all(abs(a - e) <= tolerance for a, e in zip(actual[:3], expected[:3]))


def find_colour(image: Image.Image, box: tuple[int, int, int, int], expected: tuple[int, ...], tolerance: int = 4) -> bool:
    left, top, right, bottom = box
    return any(near(image.getpixel((x, y)), expected, tolerance) and image.getpixel((x, y))[3] > 200
               for y in range(max(0, top), min(image.height, bottom)) for x in range(max(0, left), min(image.width, right)))


@pytest.fixture
def text_runs(monkeypatch):
    """Every text run the canvas is asked for, as dicts of text, x, baseline, advance and align (logical px). Nostromo
    and Night City draw through engine.Canvas."""
    runs: list[dict[str, Any]] = []
    original = engine.Canvas.text_layer

    def spy(self, text, x, baseline, font, tracking=0.0, align="left"):
        layer, advance = original(self, text, x, baseline, font, tracking, align)
        runs.append({"text": text, "x": x, "baseline": baseline, "advance": advance, "align": align})
        return layer, advance

    monkeypatch.setattr(engine.Canvas, "text_layer", spy)
    return runs


@pytest.fixture
def gates_runs(monkeypatch):
    """Every run Gates queues, as dicts of text, x, baseline, advance, colour and align (logical px)."""
    runs: list[dict[str, Any]] = []
    original = gates.Ink.put

    def spy(self, text, x, baseline, face, color, align="left"):
        advance = original(self, text, x, baseline, face, color, align)
        runs.append({"text": text, "x": x, "baseline": baseline, "advance": advance, "color": color, "align": align})
        return advance

    monkeypatch.setattr(gates.Ink, "put", spy)
    return runs


def texts(runs: list[dict[str, Any]]) -> list[str]:
    return [run["text"] for run in runs]


# ── Every skin draws every guard state ────────────────────────────────────────

@pytest.mark.parametrize("scale", SCALES)
@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_every_skin_draws_every_guard_state_at_every_scale_on_every_surface(theme_id, scale):
    """The three samples (armed, held by the session, held by the week) and a few that stress the layout: a normal
    reading over a low cap, two rows over, a held limit that is not live. Every variant at 1x and 1.5x, two of them
    at the other scales."""
    skin, theme = get_skin(theme_id), theme_of(theme_id)
    variants = variants_of(theme_id) if scale in (1.0, 1.5) else [picked(theme_id, "default"), picked(theme_id, "other")]
    cases = [dump.sample_model(name) for name in dump.GUARD_STATE_NAMES]
    cases += [guarded("normal3", "holding"), guarded("rate_limited3", "holding"), guarded("stale3", "armed"),
              guarded("normal2", "holding"), custom(97, 92, 12)]
    for variant in variants:
        for model in cases:
            frame = skin.render_floating(model, theme, variant, scale, None)
            assert frame.mode == "RGBA" and frame.size == skin.floating_size(theme, model, scale), (variant.id, model.guard)
            assert frame.getchannel("A").getextrema()[1] == 255
            strip = skin.render_taskbar(model, theme, variant, round(48 * scale), scale, None)
            assert strip.mode == "RGBA" and strip.getchannel("A").getextrema()[1] > 0
            tip = skin.render_tooltip(model, theme, variant, scale)
            assert tip.mode == "RGBA" and tip.getchannel("A").getextrema()[1] == 255


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_every_option_combination_draws_the_guard(theme_id):
    skin, theme, variant = get_skin(theme_id), theme_of(theme_id), picked(theme_id, "default")
    for model in (dump.sample_model("guard_armed"), dump.sample_model("guard_hold_session")):
        plain = dataclasses.replace(model, guard=None)
        for transparent in (False, True):
            for fx in (True, False):
                for reduce_motion in (False, True):
                    opts = RenderOptions(transparent_bg=transparent, text_fx=fx, reduce_motion=reduce_motion)
                    frame = skin.render_floating(model, theme, variant, 1.5, opts)
                    assert frame.size == skin.render_floating(plain, theme, variant, 1.5, opts).size
                    assert changed(frame, skin.render_floating(plain, theme, variant, 1.5, opts)).getbbox() is not None
                    strip = skin.render_taskbar(model, theme, variant, 72, 1.5, opts)
                    assert strip.size == skin.render_taskbar(plain, theme, variant, 72, 1.5, opts).size


# ── Nothing moves ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("scale", (1.0, 1.5))
@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_the_guard_changes_no_size_and_no_row(theme_id, scale):
    skin, theme, variant = get_skin(theme_id), theme_of(theme_id), picked(theme_id, "default")
    for name in WITH_DATA:
        plain = dump.sample_model(name)
        floating, strip = draw(theme_id, variant, plain, scale, "floating"), draw(theme_id, variant, plain, scale, "taskbar")
        for kind in ("armed", "holding"):
            model = guarded(name, kind)
            assert model.rows == plain.rows and model.stale == plain.stale and model.status == plain.status
            assert skin.floating_size(theme, model, scale) == skin.floating_size(theme, plain, scale)
            assert draw(theme_id, variant, model, scale, "floating").size == floating.size, (name, kind)
            assert draw(theme_id, variant, model, scale, "taskbar").size == strip.size, (name, kind)


@pytest.mark.parametrize("which", ("default", "other"))
@pytest.mark.parametrize("scale", (1.0, 1.5))
@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_armed_marks_the_header_and_nothing_else_of_the_floating_widget(theme_id, scale, which):
    variant = picked(theme_id, which)
    for name in LIVE:
        plain = dump.sample_model(name)
        armed = guarded(name, "armed")
        off = draw(theme_id, variant, plain, scale, "floating", NO_FX)
        on = draw(theme_id, variant, armed, scale, "floating", NO_FX)
        mask = changed(off, on)
        assert mask.getbbox() is not None, name                                   # the mark is there
        assert outside(mask, floating_boxes(theme_id, scale, armed)) is None, (name, outside(mask, floating_boxes(theme_id, scale, armed)))


@pytest.mark.parametrize("which", ("default", "other"))
@pytest.mark.parametrize("scale", (1.0, 1.5))
@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_holding_marks_the_header_and_the_rows_it_holds_on_and_leaves_the_others_alone(theme_id, scale, which):
    """The session row is over its cap and nothing else is: the weekly and the scoped rows are what they were, pixel for
    pixel (and the Gates card words its footer for the guard)."""
    variant = picked(theme_id, which)
    for name in LIVE:
        plain = dump.sample_model(name)
        held = guarded(name, "holding")
        assert held.guard.over_keys == ("session",)
        off = draw(theme_id, variant, plain, scale, "floating", NO_FX)
        on = draw(theme_id, variant, held, scale, "floating", NO_FX)
        mask = changed(off, on)
        boxes = floating_boxes(theme_id, scale, held, rows=(0,), footer=theme_id == "gates")
        assert outside(mask, boxes) is None, (name, outside(mask, boxes))
        assert mask.crop(boxes[1]).getbbox() is not None, name                     # the row it holds on is marked
        for index in range(1, len(held.rows)):                                      # and the other rows are what they were
            _, top, _, bottom = floating_boxes(theme_id, scale, held, rows=(index,))[1]
            inner = (0, top + math.ceil(6 * scale), on.width, bottom - math.ceil(2 * scale))
            assert mask.crop(inner).getbbox() is None, (name, index)


STALE = tuple(name for name in NOT_LIVE if dump.sample_model(name).stale)       # the frames every skin draws flat


@pytest.mark.parametrize("scale", (1.0, 1.5))
@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_a_stale_frame_is_drawn_flat_so_the_guard_marks_its_header_and_no_row(theme_id, scale):
    """The numbers are not live: no skin raises a row to critical or names a cap on it, and the Gates card keeps the
    words that say why they are old. The header carries the guard."""
    assert STALE
    variant = picked(theme_id, "default")
    for name in STALE:
        held = guarded(name, "holding")
        assert held.stale and held.guard.holding
        off = draw(theme_id, variant, dump.sample_model(name), scale, "floating", NO_FX)
        on = draw(theme_id, variant, held, scale, "floating", NO_FX)
        mask = changed(off, on)
        assert mask.getbbox() is not None, name                                     # the header says it
        assert outside(mask, floating_boxes(theme_id, scale, held)) is None, (name, outside(mask, floating_boxes(theme_id, scale, held)))


@pytest.mark.parametrize("scale", (1.0, 1.5, 2.0))
@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_armed_leaves_the_strip_alone_and_holding_marks_it(theme_id, scale):
    """The strip has no room for [GUARD] or a countdown: armed is nothing, in every skin, and holding is a compact mark."""
    for which in ("default", "other"):
        variant = picked(theme_id, which)
        for name in WITH_DATA:
            for bar in (40, 48, 60):
                plain = draw(theme_id, variant, dump.sample_model(name), scale, "taskbar", None, bar)
                armed = draw(theme_id, variant, guarded(name, "armed"), scale, "taskbar", None, bar)
                held = draw(theme_id, variant, guarded(name, "holding"), scale, "taskbar", None, bar)
                assert armed.tobytes() == plain.tobytes(), (theme_id, variant.id, name, bar)
                assert held.size == plain.size and changed(plain, held).getbbox() is not None, (variant.id, name, bar)


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_the_hover_card_gains_the_guards_block_and_keeps_its_width(theme_id):
    variant = picked(theme_id, "default")
    for name in LIVE + ("stale3", "rate_limited3"):
        plain = draw(theme_id, variant, dump.sample_model(name), 1.0, "tooltip")
        for kind in ("armed", "holding"):
            card = draw(theme_id, variant, guarded(name, kind), 1.0, "tooltip")
            assert card.width == plain.width and card.height > plain.height, (name, kind)
    off = dump.sample_model("normal3")
    assert draw(theme_id, variant, off, 1.0, "tooltip").size == draw(theme_id, variant, dataclasses.replace(off, guard=None),
                                                                      1.0, "tooltip").size


# ── Nostromo ──────────────────────────────────────────────────────────────────

def nostromo_frame(model: WidgetModel, opts: Any = None, scale: float = 1.0, which: str = "default") -> Image.Image:
    return draw("nostromo", picked("nostromo", which), model, scale, "floating", opts)


def test_nostromo_holding_is_a_hold_chip_with_the_countdown_and_the_over_cap_row_names_its_cap(text_runs):
    nostromo_frame(dump.sample_model("guard_hold_session"))
    shown = texts(text_runs)
    assert "HOLD 2H 14M" in shown and "CAP 95%" in shown
    assert "CRITICAL" not in shown and "[GUARD]" not in shown                   # the chip is the state word's place
    text_runs.clear()
    nostromo_frame(dump.sample_model("guard_hold_weekly"))
    assert "HOLD 2D 3H" in texts(text_runs) and "CAP 90%" in texts(text_runs)
    assert "CAP 95%" not in texts(text_runs)


def test_nostromo_armed_is_a_quiet_bracketed_guard_after_the_age_and_the_state_word_stays(text_runs):
    nostromo_frame(dump.sample_model("guard_armed"))
    shown = texts(text_runs)
    assert "[GUARD]" in shown and "NOMINAL" in shown
    assert not any(text.startswith(("HOLD", "CAP")) for text in shown)
    text_runs.clear()
    nostromo_frame(dump.sample_model("normal3"))
    assert not any("GUARD" in text or text.startswith(("HOLD", "CAP")) for text in texts(text_runs))


def test_nostromo_hold_without_a_reset_time_says_hold_alone(text_runs):
    model = dataclasses.replace(dump.sample_model("guard_hold_session"),
                                guard=dataclasses.replace(dump.sample_model("guard_hold_session").guard, resume_at=None,
                                                          resume_text=None, resume_in=None, resume_clock=None))
    nostromo_frame(model)
    assert "HOLD" in texts(text_runs) and not any(text.startswith("HOLD ") for text in texts(text_runs))


def header_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [run for run in runs if 5 < run["baseline"] < 19]


@pytest.mark.parametrize("scale", (1.0, 1.5))
@pytest.mark.parametrize("kind", ("armed", "holding"))
@pytest.mark.parametrize("name", WITH_DATA)
def test_nostromo_guard_mark_never_runs_into_the_header(name, kind, scale, text_runs):
    model = guarded(name, kind)
    nostromo_frame(model, scale=scale)
    header = header_runs(text_runs)
    pill = next(run for run in header if run["text"] == model.status_text)
    age = next((run for run in header if run["text"] == nostromo._age_text(model)), None)
    left_end = age["x"] + age["advance"] if age else pill["x"] + pill["advance"] + 5
    word = next(run for run in header if run["align"] == "right")
    chip = word["x"] == nostromo.RIGHT - 4
    word_left = word["x"] - word["advance"] - (4 if chip else 0)
    assert word["x"] <= nostromo.RIGHT
    if kind == "holding":
        assert chip and word["text"].startswith("HOLD")
        assert word_left - left_end >= 6, (name, word_left, left_end)
        return
    mark = next((run for run in header if run["text"] in ("[GUARD]", "[G]")), None)
    if mark is not None:
        assert mark["x"] >= left_end + nostromo.ARMED_GAP - 1e-6
        assert mark["x"] + mark["advance"] <= word_left - nostromo.ARMED_GAP + 1e-6


def test_nostromo_a_status_with_a_long_pill_leaves_room_for_the_short_guard_only(text_runs):
    nostromo_frame(guarded("rate_limited3", "armed"))
    assert "[G]" in texts(text_runs) and "[GUARD]" not in texts(text_runs)
    text_runs.clear()
    nostromo_frame(guarded("offline3", "armed"))
    assert "[GUARD]" in texts(text_runs)


@pytest.mark.parametrize(("label", "model_name"), [("CLAUDE DESIGN WEEKLY", "Claude Design"), ("CLAUDE OPUS 4.8 WEEKLY", "Claude Opus 4.8"),
                                                    ("CLAUDE SONNET 4.5 WEEKLY", "Claude Sonnet 4.5")])
def test_nostromo_a_long_scoped_name_keeps_its_room_and_the_cap_tag_is_left_off(label, model_name, text_runs):
    model = custom(20, 30, 40, label=label, model=model_name, scoped_cap=30)
    assert model.guard.over_keys == (model.rows[2].key,)
    nostromo_frame(model)
    shown = texts(text_runs)
    assert not any(text.startswith("CAP") for text in shown) and any(text.startswith("HOLD") for text in shown)
    row_top = nostromo.ROW_TOP + 2 * nostromo.ROW_PITCH
    line = [run for run in text_runs if run["align"] == "left" and row_top < run["baseline"] <= row_top + 12]
    value = next(run for run in text_runs if run["align"] == "right" and run["text"].endswith("%")
                 and row_top - 4 < run["baseline"] <= row_top + 18)
    chip_left = value["x"] - value["advance"] - nostromo.VALUE_CHIP_PAD / 2
    assert max(run["x"] + run["advance"] for run in line) <= chip_left - nostromo.VALUE_CLEARANCE + 1e-6


def test_nostromo_a_reading_over_a_low_cap_is_a_value_chip_whatever_its_severity():
    """13 % is normal to the API and over the cap to the user: its value sits in the chip a critical one does."""
    variant, theme = picked("nostromo", "default"), theme_of("nostromo")
    pal = nostromo.Palette.for_variant(theme, variant)
    held, plain = guarded("normal3", "holding"), dump.sample_model("normal3")
    assert held.rows[0].severity == "normal"
    on, off = nostromo_frame(held, NO_FX), nostromo_frame(plain, NO_FX)
    spot = (nostromo.RIGHT - 2, nostromo.ROW_TOP - 2)                           # inside the chip, above the digits
    assert near(on.getpixel(spot), pal.bright, 2) and not near(off.getpixel(spot), pal.bright, 30)
    other = (nostromo.RIGHT - 2, nostromo.ROW_TOP + nostromo.ROW_PITCH - 2)       # the weekly row is not
    assert not near(on.getpixel(other), pal.bright, 30)


def test_nostromo_strip_holds_with_an_hourglass_chip_in_the_dividers_place():
    variant, theme = picked("nostromo", "default"), theme_of("nostromo")
    pal = nostromo.Palette.for_variant(theme, variant)
    x0, y0, x1, y1 = nostromo.STRIP_HOLD_BOX
    inside, divider = (x0 + 1, y0 + 1), (nostromo.STRIP_DIVIDER_X, 9)          # the divider's top pixel, above the chip
    plain = draw("nostromo", variant, dump.sample_model("normal3"), 1.0, "taskbar", NO_FX, 34)
    held = draw("nostromo", variant, guarded("normal3", "holding"), 1.0, "taskbar", NO_FX, 34)
    armed = draw("nostromo", variant, guarded("normal3", "armed"), 1.0, "taskbar", NO_FX, 34)
    assert near(held.getpixel(inside), pal.bright, 2) and not near(plain.getpixel(inside), pal.bright, 30)
    assert held.getpixel(divider) != plain.getpixel(divider)                    # the divider is gone: only the chip is there
    assert armed.getpixel(divider) == plain.getpixel(divider) and armed.getpixel(inside) == plain.getpixel(inside)


def test_nostromo_the_colours_of_the_guard_are_the_variants_tokens():
    """The hold chips (header, over-cap value and strip) are the variant's accent; nothing in them is a colour of their own."""
    theme, variant = theme_of("nostromo"), picked("nostromo", "default")
    skin = get_skin("nostromo")
    recoloured = replace(variant, colors={**variant.colors, "accent": "#12ab34"})
    sentinel = colour("#12ab34")
    held = guarded("normal3", "holding")
    image = skin.render_floating(held, theme, recoloured, 1.0, NO_FX)
    assert near(image.getpixel((nostromo.RIGHT - 1, nostromo.HEADER_TOP + 1)), sentinel, 2)       # the header's hold chip
    assert near(image.getpixel((nostromo.RIGHT - 2, nostromo.ROW_TOP - 2)), sentinel, 2)          # the over-cap row's value chip
    x0, y0, _, _ = nostromo.STRIP_HOLD_BOX
    assert near(skin.render_taskbar(held, theme, recoloured, 34, 1.0, NO_FX).getpixel((x0 + 1, y0 + 1)), sentinel, 2)
    original = skin.render_floating(held, theme, variant, 1.0, NO_FX)
    assert not near(original.getpixel((nostromo.RIGHT - 1, nostromo.HEADER_TOP + 1)), sentinel, 30)


def test_nostromo_the_guard_line_of_the_hover_card_is_in_capitals():
    armed, held = dump.sample_model("guard_armed"), dump.sample_model("guard_hold_weekly")
    lead, rest = nostromo._guard_statement(armed)
    assert (lead, plain_text(rest)) == ("GUARD ARMED:", "SESSION 95%, WEEKLY 90%")
    lead, rest = nostromo._guard_statement(held)
    assert (lead, plain_text(rest)) == ("GUARD HOLDING:", f"WEEKLY 91% (CAP 90%). RESUMES {held.guard.resume_clock.upper()} (IN 2D 3H).")
    both = custom(97, 92, 12, session_in=timedelta(hours=1), weekly_in=timedelta(days=1, hours=9))
    assert plain_text(nostromo._guard_statement(both)[1]).startswith("SESSION 97% (CAP 95%), WEEKLY 92% (CAP 90%). RESUMES ")
    assert nostromo._guard_statement(dataclasses.replace(armed, guard=None)) is None


def test_nostromo_the_guard_line_fits_the_card_and_the_card_grows_by_exactly_its_lines():
    theme = theme_of("nostromo")
    cv = nostromo._canvas(1, 1, 1.0, theme)
    reg = cv.font(nostromo.FONT_REG, nostromo.LABEL_SIZE)
    variant = picked("nostromo", "default")
    for model in (guarded("normal3", "armed", scoped_cap=80), guarded("normal3", "holding"),
                  custom(97, 92, 12, scoped_cap=10, label="CLAUDE SONNET 4.5 WEEKLY", model="Claude Sonnet 4.5")):
        lead, lines = nostromo._guard_lines(cv, model)
        assert lines and lead > 0
        for line in lines:
            assert cv.advance(line, reg, nostromo.QUALIFIER_TRACKING) <= nostromo.RIGHT - nostromo.LEFT + 1e-6, line
        card = draw("nostromo", variant, model, 1.0, "tooltip")
        base = draw("nostromo", variant, dataclasses.replace(model, guard=None), 1.0, "tooltip")
        assert card.height - base.height == nostromo.GUARD_BLOCK_PAD + nostromo.GUARD_LINE_H * len(lines)


def test_nostromo_the_sign_in_box_moves_down_by_the_guard_block_and_stays_in_the_card():
    """A hover card with the sign-in message (stale3) and a guard: the block takes the room between the last row and the
    box, the box moves down by the block's height, and both stay inside the card."""
    model = guarded("stale3", "holding")
    assert model.stale_message
    variant = picked("nostromo", "default")
    card = draw("nostromo", variant, model, 1.0, "tooltip")
    base = draw("nostromo", variant, dataclasses.replace(model, guard=None), 1.0, "tooltip")
    lead, lines = nostromo._guard_lines(nostromo._canvas(1, 1, 1.0, theme_of("nostromo")), model)
    block = nostromo.GUARD_BLOCK_PAD + nostromo.GUARD_LINE_H * len(lines)
    assert card.height - base.height == block
    last_top = nostromo.ROW_TOP + nostromo.TOOLTIP_ROW_PITCH * (len(model.rows) - 1)
    block_end = last_top + nostromo.GUARD_TEXT_OFFSET + nostromo.GUARD_LINE_H * len(lines)
    box_top = last_top + nostromo.TOOLTIP_INFO_OFFSET + 20 + block
    assert block_end <= box_top and box_top + nostromo.MESSAGE_H < card.height


# ── Night City ────────────────────────────────────────────────────────────────

def city_frame(model: WidgetModel, opts: Any = None, scale: float = 1.0, which: str = "default", surface: str = "floating"):
    return draw("night_city", picked("night_city", which), model, scale, surface, opts)


def test_night_city_holding_is_a_hold_chip_with_the_countdown_and_the_over_cap_rows_chip_names_its_cap(text_runs):
    city_frame(dump.sample_model("guard_hold_weekly"))
    shown = texts(text_runs)
    assert "HOLD 2D 3H" in shown and "CAP 90" in shown
    assert "CRIT" not in shown and "LIVE" not in shown and "GUARD" not in shown   # the chip is the status tag's place
    text_runs.clear()
    city_frame(dump.sample_model("guard_hold_session"))
    assert "HOLD 2H 14M" in texts(text_runs) and "CAP 95" in texts(text_runs)


def test_night_city_armed_is_a_quiet_guard_chip_beside_the_live_tag(text_runs):
    city_frame(dump.sample_model("guard_armed"))
    assert "GUARD" in texts(text_runs) and "LIVE" in texts(text_runs)
    guard_run = next(run for run in text_runs if run["text"] == "GUARD")
    live_run = next(run for run in text_runs if run["text"] == "LIVE")
    assert guard_run["x"] + guard_run["advance"] < live_run["x"] - night_city.GUARD_GAP
    text_runs.clear()
    city_frame(dump.sample_model("normal3"))
    assert "GUARD" not in texts(text_runs)


@pytest.mark.parametrize("which", ("default", "other"))
@pytest.mark.parametrize("name", WITH_DATA)
def test_night_city_the_hold_chip_stays_inside_the_header_or_the_band(name, which, text_runs):
    model = guarded(name, "holding")
    city_frame(model, which=which)
    chip = next(run for run in text_runs if run["text"].startswith("HOLD"))
    assert chip["x"] + chip["advance"] <= night_city.RIGHT                         # a chip is its text and 17 px of box
    assert chip["x"] - 11 >= night_city.LEFT                                         # and never starts left of the card's content
    if model.status in night_city.BAND_STATUSES:
        theme = theme_of("night_city")
        assert night_city._band_text(theme, model) in texts(text_runs)             # the cause the user acts on stays whole
        cause = next(run for run in text_runs if run["text"] == night_city._band_text(theme, model))
        assert cause["x"] + cause["advance"] < chip["x"] - 11 + 1e-6                # and clear of the chip


def test_night_city_the_stale_band_gives_way_in_order_countdown_then_age(text_runs):
    """The cause stays whole. The chip gives up its countdown first, then the age gives up its place."""
    theme = theme_of("night_city")
    city_frame(guarded("stale3", "holding"))                                         # the longest cause: no room for the age
    shown = texts(text_runs)
    assert night_city._band_text(theme, guarded("stale3", "holding")) in shown and "HOLD" in shown
    assert not any(text.endswith(" OLD") for text in shown) and "HOLD 3H 27M" not in shown     # "HOLD" itself ends in OLD
    text_runs.clear()
    city_frame(guarded("rate_limited3", "holding"))                                  # a short cause: all three fit
    shown = texts(text_runs)
    assert "HOLD 3H 27M" in shown and any(text.endswith(" OLD") for text in shown)


def test_night_city_the_stale_band_drops_the_countdown_before_the_age(text_runs):
    """A cause with room for the age and the short chip but not for the countdown: the chip is HOLD alone, the age stays."""
    theme, variant = theme_of("night_city"), picked("night_city", "default")
    ink = night_city._Ink(night_city._canvas(night_city.FLOAT_W, 94, 1.0, theme),
                          night_city.Palette.for_variant(theme, variant), RenderOptions())
    model = guarded("rate_limited3", "holding")
    hold, age = night_city._hold_word(model), night_city._age_tag(model)
    assert hold != "HOLD" and age
    room = night_city.BAND_AGE_RIGHT - night_city.BAND_TEXT_X - night_city.GUARD_GAP - ink.width(age, night_city.RESET) - 10
    full, short = night_city._chip_hold_width(ink, hold), night_city._chip_hold_width(ink, "HOLD")
    cause = next("W" * n for n in range(1, 80) if ink.width("W" * n, night_city.BAND_TYPE) > room - full)
    assert ink.width(cause, night_city.BAND_TYPE) <= room - short
    night_city._draw_band_hold(ink, model, cause, age, hold)
    shown = texts(text_runs)
    assert cause in shown and age in shown and "HOLD" in shown and hold not in shown


def test_night_city_a_long_scoped_name_keeps_the_chip_and_the_value():
    pal = night_city.Palette.for_variant(theme_of("night_city"), picked("night_city", "default"))
    cv = night_city._canvas(night_city.FLOAT_W, 126, 1.0, theme_of("night_city"))
    ink = night_city._Ink(cv, pal, RenderOptions())
    look = night_city.look_for(picked("night_city", "default"))
    for label, model_name in (("CLAUDE DESIGN WEEKLY", "Claude Design"), ("CLAUDE SONNET 4.5 WEEKLY", "Claude Sonnet 4.5"),
                              ("A VERY LONG MODEL NAME THAT KEEPS GOING FOR A WHILE WEEKLY", "A Very Long Model Name")):
        model = custom(20, 30, 95, label=label, model=model_name, scoped_cap=90)
        row = model.rows[2]
        word = night_city._chip_word(model, row)
        assert word == "CAP 90" and night_city._row_emphasis(model, row) == "critical"
        boxes = night_city._layout_row(ink, look, row, "critical", "95", None, word)
        assert boxes.chip is not None and boxes.chip[1] <= boxes.value[0] - night_city.MIN_GAP + 1e-6, label


def with_tokens(variant: Any, **tokens: str) -> Any:
    """The variant with some of its own tokens swapped (the variant's extras are the last word on a Night City token)."""
    return replace(variant, extras={**variant.extras, **tokens})


def test_night_city_the_colours_of_the_chip_and_the_rows_are_the_variants_tokens():
    theme, variant = theme_of("night_city"), picked("night_city", "default")
    pal = night_city.Palette.for_variant(theme, variant)
    skin = get_skin("night_city")
    model = guarded("normal3", "holding")
    assert model.rows[0].severity == "normal"                                        # the cap alone makes the row critical
    image = skin.render_floating(model, theme, variant, 1.0, NO_FX)
    spot = (night_city.RIGHT - 9, 9)                                               # the chip's box, above its text
    assert near(image.getpixel(spot), pal.ink, 2)
    other = with_tokens(variant, ink="#12ab34")
    assert near(skin.render_floating(model, theme, other, 1.0, NO_FX).getpixel(spot), colour("#12ab34"), 2)
    bar = (night_city.LEFT, night_city.ROW_TOP + night_city.BAR_Y, night_city.RIGHT, night_city.ROW_TOP + night_city.BAR_Y + night_city.BAR_H)
    plain = skin.render_floating(dump.sample_model("normal3"), theme, variant, 1.0, NO_FX)
    assert find_colour(image, bar, pal.crit_fill[0], 2) and not find_colour(plain, bar, pal.crit_fill[0], 2)


def test_night_city_the_edgerun_plate_has_no_bright_tone_so_its_chip_word_is_the_crit_red(text_runs):
    theme, variant = theme_of("night_city"), theme_of("night_city").variant("edgerun")
    skin = get_skin("night_city")
    pal = night_city.Palette.for_variant(theme, variant)
    assert pal.plate
    image = skin.render_floating(dump.sample_model("guard_hold_session"), theme, variant, 1.0, NO_FX)
    chip = next(run for run in text_runs if run["text"].startswith("HOLD"))
    box = (round(chip["x"]), 8, round(chip["x"] + chip["advance"]), 21)
    assert find_colour(image, box, pal.hz_a, 8)                                    # red on the ink box
    assert not find_colour(image, box, pal.sel, 8)                                 # and no glitch fringe on the plate


def test_night_city_the_glitch_is_a_still_split_and_a_stale_frame_is_drawn_flat():
    theme, variant = theme_of("night_city"), picked("night_city", "default")
    pal = night_city.Palette.for_variant(theme, variant)
    live = city_frame(dump.sample_model("guard_hold_session"), NO_FX, scale=2.0)
    chip_box = (round((night_city.RIGHT - 80) * 2), 14, night_city.RIGHT * 2, 42)
    assert find_colour(live, chip_box, pal.sel, 6)                                   # the selection-colour copy of the word
    stale = city_frame(guarded("offline3", "holding"), NO_FX, scale=2.0)
    band_chip = (round((night_city.RIGHT - 50) * 2), 16, night_city.BAND_AGE_RIGHT * 2, 40)
    assert not find_colour(stale, band_chip, pal.sel, 6)                             # flat: no fringes over dimmed text


def test_night_city_the_guard_line_of_the_hover_card_has_the_themes_slashes():
    armed, held = dump.sample_model("guard_armed"), dump.sample_model("guard_hold_weekly")
    lead, rest = night_city._guard_statement(armed)
    assert (lead, plain_text(rest)) == ("GUARD // ARMED:", "SESSION 95%, WEEKLY 90%")
    lead, rest = night_city._guard_statement(held)
    assert (lead, plain_text(rest)) == ("GUARD // HOLDING:", f"WEEKLY 91% (CAP 90%). RESUMES {held.guard.resume_clock.upper()} (IN 2D 3H).")


def test_night_city_the_guard_line_fits_the_card_and_the_card_grows_by_exactly_its_lines():
    theme, variant = theme_of("night_city"), picked("night_city", "default")
    pal = night_city.Palette.for_variant(theme, variant)
    ink = night_city._Ink(night_city._canvas(1, 1, 1.0, theme), pal, RenderOptions())
    for model in (guarded("normal3", "armed", scoped_cap=80), guarded("normal3", "holding"),
                  custom(97, 92, 12, scoped_cap=10, label="CLAUDE SONNET 4.5 WEEKLY", model="Claude Sonnet 4.5")):
        lead, lines = night_city._guard_lines(ink, model)
        first_room = night_city.CONTENT_W - ink.width(lead, night_city.CHIP) - night_city.GUARD_GAP
        for index, line in enumerate(lines):
            room = first_room if index == 0 else night_city.CONTENT_W
            assert ink.width(line, night_city.RESET) <= room + 1e-6, line
        card = city_frame(model, surface="tooltip")
        base = city_frame(dataclasses.replace(model, guard=None), surface="tooltip")
        assert card.height - base.height == night_city.GUARD_BLOCK_PAD + night_city.GUARD_LINE_H * max(1, len(lines))


def test_night_city_a_guard_with_no_caps_to_list_is_its_lead_alone():
    model = guarded("normal3", "armed", session_cap=None, weekly_cap=None)
    ink = night_city._Ink(night_city._canvas(1, 1, 1.0, theme_of("night_city")),
                          night_city.Palette.for_variant(theme_of("night_city"), picked("night_city", "default")), RenderOptions())
    assert night_city._guard_lines(ink, model) == ("GUARD // ARMED", [])
    assert city_frame(model, surface="tooltip").height > city_frame(dataclasses.replace(model, guard=None), surface="tooltip").height


# ── Gates ─────────────────────────────────────────────────────────────────────

def gates_frame(model: WidgetModel, opts: Any = None, scale: float = 1.0, which: str = "default", surface: str = "floating"):
    return draw("gates", picked("gates", which), model, scale, surface, opts)


def test_gates_holding_is_a_capsule_beside_the_pill_a_cap_badge_and_a_footer_for_the_guard(gates_runs):
    gates_frame(dump.sample_model("guard_hold_session"))
    shown = texts(gates_runs)
    assert "Cap 95%" in shown and "Guard holding" in shown
    resumes = next(text for text in shown if text.startswith(" · Resumes"))
    assert resumes.endswith("(in 2h 14m)")
    assert not any("pace" in text.lower() for text in shown)                       # the pace footer gave way
    gates_runs.clear()
    gates_frame(dump.sample_model("guard_hold_weekly"))
    assert "Cap 90%" in texts(gates_runs) and "Cap 95%" not in texts(gates_runs)
    assert next(text for text in texts(gates_runs) if text.startswith(" · Resumes")).endswith("(in 2d 3h)")


def test_gates_armed_leaves_the_footer_and_the_rows_as_they_were(gates_runs):
    gates_frame(dump.sample_model("guard_armed"))
    shown = texts(gates_runs)
    assert "Guard holding" not in shown and not any(text.startswith("Cap") for text in shown)
    assert any("pace" in text.lower() for text in shown)


@pytest.mark.parametrize("name", NOT_LIVE)
def test_gates_a_state_that_needs_the_user_keeps_its_own_footer(name, gates_runs):
    model = guarded(name, "holding")
    gates_frame(model)
    shown = texts(gates_runs)
    assert "Guard holding" not in shown                                              # the card says why the numbers are old
    lead, rest, _ = gates._STATUS_COPY[model.status]
    assert (lead or rest) in shown
    assert not any(text.startswith("Cap") for text in shown)                       # and a dimmed card is drawn flat
    gates_runs.clear()
    gates_frame(model, surface="tooltip")                                            # the hover card carries the guard
    assert "Guard holding:" in texts(gates_runs) and (lead or rest) in texts(gates_runs)


@pytest.mark.parametrize("scale", (1.0, 1.5))
@pytest.mark.parametrize("surface", ("floating", "tooltip"))
@pytest.mark.parametrize("kind", ("armed", "holding"))
@pytest.mark.parametrize("name", WITH_DATA)
def test_gates_the_guards_mark_never_runs_into_the_title_or_the_pill(name, kind, surface, scale, gates_runs):
    model = guarded(name, kind)
    gates_frame(model, scale=scale, surface=surface)
    title = next(run for run in gates_runs if run["text"] == "Claude usage")
    pill = next(run for run in gates_runs if run["text"].startswith(model.status_text))
    pill_left = pill["x"] - (gates.PILL_PAD_LEFT + gates.LAMP_SIZE + gates.PILL_GAP)
    mark_width = gates._guard_mark_width(model)
    mark_left = pill_left - gates.GUARD_PILL_GAP - mark_width
    assert mark_left >= title["x"] + title["advance"] + gates.TITLE_GAP - 1e-6, (name, kind, mark_left)
    assert pill["x"] + pill["advance"] + gates.PILL_PAD_RIGHT <= (gates.PILL_RIGHT if surface == "floating" else gates.RIGHT) + 1e-6


def test_gates_a_long_status_gives_up_its_age_before_it_meets_the_guard(gates_runs):
    """RATE LIMITED with its age leaves the floating header about 8 px of air (a little more on a wider face): the
    guard's 20 px do not fit, so the pill drops its age. The hover card has the room of the dots the floating card
    spends on its menu mark, and keeps the age."""
    gates_frame(guarded("rate_limited3", "armed"))
    assert "RATE LIMITED" in texts(gates_runs) and "RATE LIMITED · 6 min" not in texts(gates_runs)
    gates_runs.clear()
    gates_frame(guarded("rate_limited3", "armed"), surface="tooltip")
    assert "RATE LIMITED · 6 min" in texts(gates_runs)


def test_gates_an_over_cap_row_is_drawn_in_the_alert_colour_whatever_its_severity(gates_runs):
    theme, variant = theme_of("gates"), picked("gates", "default")
    pal = gates.Palette.for_variant(theme, variant)
    held = guarded("normal3", "holding")
    assert held.rows[0].severity == "normal"
    image = gates_frame(held)
    values = {run["text"]: run["color"] for run in gates_runs if run["text"].endswith("%") and run["align"] == "right"}
    assert values["13%"] == pal.alert and values["2%"] == pal.text                    # the session row is over; the weekly is not
    margin = gates.MARGIN
    bar = (margin + gates.LEFT + 6, margin + gates.ROW_TOPS[0] + gates.BAR_DY, margin + gates.LEFT + 12,
           margin + gates.ROW_TOPS[0] + gates.BAR_DY + gates.BAR_H)
    assert find_colour(image, bar, pal.usage_alert, 4)
    plain = gates_frame(dump.sample_model("normal3"))
    assert not find_colour(plain, bar, pal.usage_alert, 4)


def test_gates_the_alert_tint_is_the_alert_colour_at_the_pills_alpha():
    for variant_id, alpha in (("system-dark", 0.16), ("rose-light", 0.12)):
        pal = gates.Palette.for_variant(theme_of("gates"), theme_of("gates").variant(variant_id))
        assert pal.pill_alert == engine.with_alpha(pal.alert, round(255 * alpha)), variant_id


def test_gates_the_colours_of_the_guard_are_the_variants_tokens(gates_runs):
    theme, variant = theme_of("gates"), picked("gates", "default")
    recoloured = replace(variant, colors={**variant.colors, "alert": "#12ab34"})
    gates_frame(dump.sample_model("guard_hold_session"))
    default = {run["text"]: run["color"] for run in gates_runs}
    gates_runs.clear()
    get_skin("gates").render_floating(dump.sample_model("guard_hold_session"), theme, recoloured, 1.0, None)
    swapped = {run["text"]: run["color"] for run in gates_runs}
    sentinel = colour("#12ab34")
    assert swapped["Cap 95%"] == sentinel and swapped["Guard holding"] == sentinel and swapped["96%"] == sentinel
    assert default["Cap 95%"] != sentinel and default["Guard holding"] != sentinel


def test_gates_the_strip_shows_a_shield_above_the_lamp_while_holding_and_nothing_while_armed():
    theme, variant = theme_of("gates"), picked("gates", "default")
    pal = gates.Palette.for_variant(theme, variant)
    for bar in (40, 48, 60, 80):
        held = draw("gates", variant, guarded("normal3", "holding"), 1.0, "taskbar", None, bar)
        plain = draw("gates", variant, dump.sample_model("normal3"), 1.0, "taskbar", None, bar)
        drawing, plate_h = gates._strip_fit(bar, 1.0)
        lamp_top = plate_h / 2 - (gates.LAMP_SIZE / 2 + gates.LAMP_HALO)
        offset = (held.height - round(plate_h * drawing)) // 2
        above = (round((gates.STRIP_LAMP_X - 7) * drawing), offset,
                 round((gates.STRIP_LAMP_X + 7) * drawing), offset + round(lamp_top * drawing))
        assert find_colour(held, above, pal.alert, 8), bar                         # the shield sits in the lamp's column
        assert not find_colour(plain, above, pal.alert, 8), bar


def test_gates_without_the_icon_font_the_shield_is_drawn(monkeypatch, gates_runs):
    """No Segoe Fluent Icons (Windows 10 has the older font, and a build machine may have none): a filled shield stands
    in, in the same place and the same colour, on the card and in the hover card's bar."""
    theme, variant = theme_of("gates"), picked("gates", "default")
    real = gates._fonts(theme)
    monkeypatch.setattr(gates, "_fonts", lambda _theme: gates.Fonts(real.body, real.mono, None))
    pal = gates.Palette.for_variant(theme, variant)
    skin = get_skin("gates")
    held, armed = dump.sample_model("guard_hold_session"), dump.sample_model("guard_armed")
    image = skin.render_floating(held, theme, variant, 1.0, None)
    assert image.size == skin.floating_size(theme, held, 1.0)
    pill = next(run for run in gates_runs if run["text"].startswith(held.status_text))
    left = pill["x"] - (gates.PILL_PAD_LEFT + gates.LAMP_SIZE + gates.PILL_GAP) - gates.GUARD_PILL_GAP - gates._guard_mark_width(held)
    margin = gates.MARGIN
    capsule = (margin + math.floor(left), margin + gates.HEAD_TOP,
               margin + math.ceil(left + gates.GUARD_CAPSULE_W), margin + gates.HEAD_TOP + gates.PILL_H)
    assert find_colour(image, capsule, pal.alert, 8)
    assert skin.render_floating(armed, theme, variant, 1.5, None).size == skin.floating_size(theme, armed, 1.5)
    rows_end = gates.TIP_ROW0 + gates.TIP_PITCH * len(gates._rows(held)) - (gates.TIP_PITCH - gates.TIP_ROW_H)
    icon_x, icon_y = margin + gates.LEFT + gates.BAR_PAD, margin + rows_end + gates.BAR_GAP + gates.BAR_PAD
    card = skin.render_tooltip(held, theme, variant, 1.0)
    assert find_colour(card, (icon_x, icon_y, icon_x + gates.BAR_ICON_W, icon_y + gates.FOOT_LINE), pal.alert, 8)


def test_gates_the_guard_line_of_the_hover_card_reads_as_the_brief_words_it():
    armed, held = dump.sample_model("guard_armed"), dump.sample_model("guard_hold_weekly")
    lead, rest = gates._guard_statement(armed)
    assert (lead, plain_text(rest)) == ("Guard armed:", "session 95%, weekly 90%")
    lead, rest = gates._guard_statement(held)
    assert (lead, plain_text(rest)) == ("Guard holding:", f"weekly 91% (cap 90%). Resumes {held.guard.resume_clock} (in 2d 3h).")
    both = custom(97, 92, 12, session_in=timedelta(hours=1), weekly_in=timedelta(days=1, hours=9))
    assert plain_text(gates._guard_statement(both)[1]).startswith("session 97% (cap 95%), weekly 92% (cap 90%). Resumes ")
    scoped = custom(10, 10, 5, scoped_cap=80)
    assert plain_text(gates._guard_statement(scoped)[1]) == "session 95%, weekly 90%, Fable weekly 80%"
    hidden = custom(10, 10, 95, scoped_cap=90, show_scoped="never")
    assert plain_text(gates._guard_statement(hidden)[1]).startswith("Fable weekly 95% (cap 90%). Resumes ")
    assert gates._guard_statement(dataclasses.replace(armed, guard=None)) is None


def test_gates_the_guard_line_fits_the_bar_and_the_card_grows_by_exactly_the_bar():
    theme = theme_of("gates")
    ink = gates._card_canvas(1, 1.0, theme)
    body = ink.face(ink.fonts.body, gates.SIZE_FOOT, 400)
    room = gates.RIGHT - gates.LEFT - 2 * gates.BAR_PAD - gates.BAR_ICON_W - gates.BAR_TEXT_GAP
    for model in (guarded("normal3", "armed", scoped_cap=80), guarded("normal3", "holding"),
                  custom(97, 92, 12, scoped_cap=10, label="CLAUDE SONNET 4.5 WEEKLY", model="Claude Sonnet 4.5")):
        lead, lines = gates._guard_bar_lines(ink, model)
        lead_w = ink.advance(lead + ":", ink.face(ink.fonts.body, gates.SIZE_FOOT, 600))
        for index, line in enumerate(lines):
            assert ink.advance(line, body) <= (room - lead_w - 4 if index == 0 else room) + 1e-6, line
        card = gates_frame(model, surface="tooltip")
        base = gates_frame(dataclasses.replace(model, guard=None), surface="tooltip")
        assert card.height - base.height == gates.BAR_GAP + 2 * gates.BAR_PAD + gates.FOOT_LINE * max(1, len(lines))


def test_gates_a_guard_with_no_caps_to_list_is_its_lead_alone():
    model = guarded("normal3", "armed", session_cap=None, weekly_cap=None)
    ink = gates._card_canvas(1, 1.0, theme_of("gates"))
    assert gates._guard_bar_lines(ink, model) == ("Guard armed", [])
    assert gates_frame(model, surface="tooltip").height > gates_frame(dataclasses.replace(model, guard=None), surface="tooltip").height


def test_gates_a_long_scoped_name_keeps_its_room_and_the_badge_is_left_off(gates_runs):
    model = custom(20, 30, 40, label="CLAUDE DESIGN EXTREMELY LONG WEEKLY LIMIT THAT KEEPS ON GOING", model="Claude Design Extremely Long",
                   scoped_cap=30)
    assert model.guard.over_keys == (model.rows[2].key,)
    gates_frame(model)
    assert not any(text.startswith("Cap") for text in texts(gates_runs)) and "Guard holding" in texts(gates_runs)


# ── The marks do not blink ────────────────────────────────────────────────────

@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_the_guard_marks_do_not_blink_the_alive_mark_does_what_it_did(theme_id):
    """The holding mark is a state: the blink phase and reduced motion touch the alive mark alone, as before the guard.
    (Night City's phase is its pulse and Gates has no animation at all.)"""
    variant = picked(theme_id, "default")
    for name in dump.GUARD_STATE_NAMES:
        lit = draw(theme_id, variant, replace_blink(dump.sample_model(name), True), 1.0, "floating")
        dark = draw(theme_id, variant, replace_blink(dump.sample_model(name), False), 1.0, "floating")
        box = changed(lit, dark).getbbox()
        if theme_id == "gates":
            assert box is None, name
        else:
            assert box is not None and box[0] < 30 and box[2] < 34 and box[3] < 26, (theme_id, name, box)
        for opts in ({"reduce_motion": True}, {"phase": 0.5}):
            frame = draw(theme_id, variant, replace_blink(dump.sample_model(name), True), 1.0, "floating", opts)
            moved = changed(lit, frame).getbbox()
            assert moved is None or (theme_id == "night_city" and moved[0] < 30 and moved[2] < 34 and moved[3] < 26), (name, opts)


def replace_blink(model: WidgetModel, on: bool) -> WidgetModel:
    return dataclasses.replace(model, blink_on=on)


# ── The pointer response still works on a frame that carries the guard ────────

@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_a_frame_with_the_guard_still_derives_its_pointer_response(theme_id):
    skin, variant = get_skin(theme_id), picked(theme_id, "default")
    for name in dump.GUARD_STATE_NAMES:
        for surface in ("floating", "taskbar"):
            frame = draw(theme_id, variant, dump.sample_model(name), 1.5, surface)
            play = skin.hover_frames(frame, surface, 1.5, None)
            assert play is not None and all(image.size == frame.size for image in play.frames), (name, surface)
    assert skin.content_insets("floating", theme_of(theme_id), dump.sample_model("guard_armed"), 1.5) == skin.content_insets(
        "floating", theme_of(theme_id), dump.sample_model("normal3"), 1.5)
