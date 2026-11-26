"""Night City's fits (#95): what gives way where a language's words are longer than English's, read from the pure helpers
the skin draws by (_header_plan, _info_plan, _band_plan, _strip_label, _mark_growth). English never meets any of them,
so its frames stay as they were; the owner defaults (Q1, the marks' room; Q2, the header's order) are each a few named
constants or one tuple in night_city.py, and each of the three orders is followed whatever it is."""
from __future__ import annotations

import dataclasses
import itertools
import string
from functools import lru_cache
from typing import Any

import pytest

import budgets
import frame_layout
import skin_words
from usage_monitor import casing, i18n
from usage_monitor.render import dump
from usage_monitor.render.skins import RenderOptions, get_skin
from usage_monitor.render.skins import night_city as nc

THEME = dump.load_theme_objects()["night_city"]
SKIN = get_skin("night_city")
LOOKS = ("blackwall", "relic", "edgerun")          # boxed, plain, the plate (Mox is Relic's look)
DOTS = "..."
HOLD_WORD = nc._hold_word


def _ink(scale: float = 1.0, variant: str = "blackwall", *, strip: bool = False, height: float = 126) -> Any:
    canvas = nc._canvas(*((nc.STRIP_W, nc.STRIP_H) if strip else (nc.FLOAT_W, height)), scale, THEME)
    return nc._Ink(canvas, nc.Palette.for_variant(THEME, THEME.variant(variant)), RenderOptions())


def _look(variant: str) -> Any:
    return nc.look_for(THEME.variant(variant))


def _catalog(monkeypatch: pytest.MonkeyPatch, **words: str) -> None:
    """The skin's words for these keys (widget.night_city.<key with . for __>), English for the rest."""
    english = i18n.load_catalog("en").english()
    catalog = i18n.Catalog("en", {}, {**english, **{f"widget.night_city.{key.replace('__', '.')}": text
                                                   for key, text in words.items()}})
    monkeypatch.setattr(i18n, "app_catalog", lambda language: catalog)


@pytest.fixture(scope="module")
def english() -> dict[str, Any]:
    """Every state Night City's English record covers (tests/skin_words.py), as English view models."""
    return skin_words.models(skin_words._night_city_states)


# ── The header (Q2) ───────────────────────────────────────────────────────────

def _header(monkeypatch: pytest.MonkeyPatch, word: str, kind: str, *, short: str = "HOLD", variant: str = "blackwall",
            scale: float = 1.0) -> nc.HeaderPlan:
    """_header_plan for a header whose right-hand side draws ``word``: the hold chip's (``short`` HOLD alone), the LIVE
    tag's or the status chip's."""
    if kind == "hold":
        model = dump.sample_model("guard_hold_session")
        monkeypatch.setattr(nc, "_hold_word", lambda _model: word)
        _catalog(monkeypatch, guard__hold=short)
    elif kind == "live":
        model = dump.sample_model("normal3")
        monkeypatch.setattr(nc, "_hold_word", HOLD_WORD)
        _catalog(monkeypatch, state__live=word)
    else:
        model = dataclasses.replace(dump.sample_model("paused"), status_text=word)
        monkeypatch.setattr(nc, "_hold_word", HOLD_WORD)
    return nc._header_plan(_ink(scale, variant), _look(variant), model)


def _right(plan: nc.HeaderPlan, variant: str = "blackwall", scale: float = 1.0) -> float:
    ink = _ink(scale, variant)
    return ink.width(plan.word, nc.STATUS if plan.kind == "live" else nc.CHIP) + nc._right_pad(ink, _look(variant),
                                                                                             plan.kind)


def _room(variant: str = "blackwall", scale: float = 1.0) -> float:
    ink = _ink(scale, variant)
    return nc.RIGHT - (nc._brand_x(ink.pal) + ink.width(nc.BRAND_MARK, nc.BRAND)) - nc.GUARD_GAP


def test_the_header_gives_way_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Q2: the hold chip drops its countdown first (HOLD alone), then the right-hand word is cut with the dots, one
    letter at least; a LIVE tag or a status chip has no countdown, so it is cut at once; each step only while the side
    is still over, and it then fits GUARD_GAP off the brand. The brand never gives way."""
    plan = _header(monkeypatch, "HOLD " + "9" * 30, "hold")
    assert (plan.gave_way, plan.word) == (("countdown",), "HOLD") and _right(plan) <= _room()
    plan = _header(monkeypatch, "H" * 30 + " 9", "hold", short="H" * 30)
    assert plan.gave_way == ("countdown", "word") and plan.word.endswith(DOTS) and _right(plan) <= _room()
    for kind in ("live", "chip"):
        plan = _header(monkeypatch, "W" * 30, kind)
        assert plan.gave_way == ("word",) and plan.word.endswith(DOTS) and _right(plan) <= _room(), kind
        assert ("W" * 30).startswith(plan.word[:-len(DOTS)]) and len(plan.word) > len(DOTS) + 1
    plan = _header(monkeypatch, "HOLD 2H 14M", "hold")
    assert (plan.gave_way, plan.word) == ((), "HOLD 2H 14M")


LENGTHS = (1, 4, 8, 12, 16, 20, 26, 34)


@pytest.mark.parametrize("order", list(itertools.permutations(nc.HEADER_GIVE_WAY)))
def test_the_header_plan_follows_any_order_of_its_steps(order: tuple[str, ...], monkeypatch: pytest.MonkeyPatch) -> None:
    """Q2 is HEADER_GIVE_WAY alone: in any order of its steps, in every look, at every scale, holding or not and at
    every length, _header_plan takes the steps in that order and only while the side is over; it then fits; and a word it
    cut could not take one more letter and still fit."""
    monkeypatch.setattr(nc, "HEADER_GIVE_WAY", order)
    for variant in LOOKS:
        for scale in budgets.SCALES:
            room = _room(variant, scale)
            for kind in ("hold", "live", "chip"):
                for length in LENGTHS:
                    word = ("HOLD " + "9" * length) if kind == "hold" else "W" * length
                    for short in (("HOLD", "H" * length) if kind == "hold" else ("HOLD",)):
                        plan = _header(monkeypatch, word, kind, short=short, variant=variant, scale=scale)
                        can = [step for step in order if kind == "hold" or step != "countdown"]
                        where = (order, variant, scale, kind, length, short, plan)
                        assert list(plan.gave_way) == can[:len(plan.gave_way)], where
                        assert _right(plan, variant, scale) <= room + nc.FIT_SLACK, where
                        if plan.word.endswith(DOTS) and "word" in plan.gave_way:
                            whole = short if "countdown" in plan.gave_way else word
                            kept = len(plan.word) - len(DOTS)
                            more = whole if kept + 1 >= len(whole) else whole[:kept + 1] + DOTS
                            assert _right(dataclasses.replace(plan, word=more), variant, scale) > room, where
                        if plan.gave_way:                   # the last step was taken while over
                            monkeypatch.setattr(nc, "HEADER_GIVE_WAY", order[:order.index(plan.gave_way[-1])])
                            before = _header(monkeypatch, word, kind, short=short, variant=variant, scale=scale)
                            monkeypatch.setattr(nc, "HEADER_GIVE_WAY", order)
                            assert _right(before, variant, scale) > room + nc.FIT_SLACK, (where, before)


def test_the_header_takes_no_step_while_it_fits(english: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Every English header, in every recorded state, every variant and every scale, draws its word whole; and a header
    is left whole up to its room and gives way 1 px past it."""
    widest = (-999.0, "", "", 1.0)
    for name, model in english.items():
        if model.status in nc.BAND_STATUSES:
            continue
        for variant in THEME.variants:
            for scale in budgets.SCALES:
                plan = nc._header_plan(_ink(scale, variant.id), nc.look_for(variant), model)
                live = model.status is nc.ApiStatus.LIVE and not model.stale
                whole = nc._hold_word(model) or ("LIVE" if live else model.status_text)
                assert (plan.gave_way, plan.word) == ((), whole), (name, variant.id, scale)
                widest = max(widest, (_right(plan, variant.id, scale) - _room(variant.id, scale), name, variant.id,
                                      scale))
    over, name, variant, scale = widest
    assert over < 0, widest
    model = english[name]
    for gap, taken in ((nc.GUARD_GAP - over - 1, False), (nc.GUARD_GAP - over + 1, True)):
        monkeypatch.setattr(nc, "GUARD_GAP", gap)
        assert bool(nc._header_plan(_ink(scale, variant), _look(variant), model).gave_way) == taken, (gap, widest)


# ── The hover line ────────────────────────────────────────────────────────────

def test_the_hover_line_never_gives_way_in_english(english: dict[str, Any]) -> None:
    for name, model in english.items():
        for row in model.rows:
            for variant in LOOKS:
                info = nc._info_plan(_ink(1.0, variant), _look(variant), model, row)
                assert info.gave_way == () and info.pace == nc._pace_text(row, locale=model.locale)[0], (name, variant)


@pytest.mark.parametrize("order", list(itertools.permutations(nc.INFO_GIVE_WAY)))
def test_the_hover_line_shortens_its_pace_then_leaves_it_out(order: tuple[str, ...], english: dict[str, Any],
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """The reset stays whole: as it grows, a pace with points (ahead or under, a chip on a live card and plain text on a
    stale one) takes its short form, its signed points, then is left out; a pace with no points (ON PACE) is left out at
    once. In any order of INFO_GIVE_WAY, each form comes as its step does."""
    monkeypatch.setattr(nc, "INFO_GIVE_WAY", order)
    forms = {"pace": "short", "pace_short": None}
    ahead, behind, on = (english[name].rows[0] for name in ("pace/ahead_100", "pace/behind_100", "pace/on"))
    stale = dataclasses.replace(english["pace/ahead_100"], stale=True)
    for model, row, short, chip in ((english["pace/ahead_100"], ahead, "+100", True),
                                    (english["pace/behind_100"], behind, "-100", True), (stale, ahead, "+100", False),
                                    (english["pace/on"], on, None, False)):
        want = [(nc._pace_text(row)[0], ())]
        for index, step in enumerate(order):
            form = short if forms[step] == "short" else None
            want.append((form, order[:index + 1]))
            if form is None:
                break
        seen: list[tuple[str | None, tuple[str, ...]]] = []
        for extra in range(0, 60):
            longer = dataclasses.replace(row, resets_text="WED 12:59 PM" + " X" * extra)
            info = nc._info_plan(_ink(), _look("blackwall"), model, longer)
            assert info.reset == "RST WED 12:59 PM" + " X" * extra and info.chip == chip
            if not seen or seen[-1] != (info.pace, info.gave_way):
                seen.append((info.pace, info.gave_way))
        assert seen == want, (order, row.pace, model.stale)


# ── The stale band ────────────────────────────────────────────────────────────

def _band_width(ink: Any, plan: nc.BandPlan) -> float:
    return (ink.width(plan.cause, nc.BAND_TYPE) + (ink.width(plan.age, nc.RESET) + 10 if plan.age else 0.0)
            + (nc.GUARD_GAP + nc._chip_hold_width(ink, plan.chip) if plan.chip is not None else 0.0))


BAND_ROOM = nc.BAND_AGE_RIGHT - nc.BAND_TEXT_X


@pytest.mark.parametrize("order", list(itertools.permutations(nc.BAND_GIVE_WAY)))
def test_the_band_gives_way_in_one_order_on_both_paths(order: tuple[str, ...], monkeypatch: pytest.MonkeyPatch) -> None:
    """One tuple holds the band's order on both of its paths (critic 1 of #95's plan): in any order of BAND_GIVE_WAY, at
    every length of cause, with an age or none and with the guard holding (its chip with a countdown or HOLD alone) or
    not, _band_plan takes the steps in that order, only while the band is over; the band then fits; and a cause it cut
    could not take one more letter and still fit."""
    monkeypatch.setattr(nc, "BAND_GIVE_WAY", order)
    model = dump.sample_model("stale3")
    for scale in (1.0, 1.25):
        ink = _ink(scale)
        for length in (4, 12, 20, 26, 32, 40):
            cause = "W" * length
            for age in (None, "59 S OLD", "100 DÍAS OLD"):
                for hold in (None, "HOLD", "HOLD 23H 59M"):
                    plan = nc._band_plan(ink, model, cause, age, hold, "HOLD")
                    can = [step for step in order if (step != "countdown" or hold == "HOLD 23H 59M")
                           and (step != "age" or age)]
                    where = (order, scale, length, age, hold, plan)
                    assert list(plan.gave_way) == can[:len(plan.gave_way)], where
                    assert _band_width(ink, plan) <= BAND_ROOM + nc.FIT_SLACK, where
                    if plan.cause != cause:
                        assert "cause" in plan.gave_way and plan.cause.endswith(DOTS), where
                        kept = len(plan.cause) - len(DOTS)
                        more = cause[:kept + 1] + DOTS if kept + 1 < len(cause) else cause
                        assert _band_width(ink, dataclasses.replace(plan, cause=more)) > BAND_ROOM, where
                    if plan.gave_way:
                        monkeypatch.setattr(nc, "BAND_GIVE_WAY", order[:order.index(plan.gave_way[-1])])
                        before = nc._band_plan(ink, model, cause, age, hold, "HOLD")
                        monkeypatch.setattr(nc, "BAND_GIVE_WAY", order)
                        assert _band_width(ink, before) > BAND_ROOM + nc.FIT_SLACK, (where, before)


def test_the_band_leaves_an_age_out_before_it_cuts_its_cause(english: dict[str, Any]) -> None:
    """es-CO writes 100 days spaced and in full ("100 DÍAS OLD", 73.9 px): beside the widest cause the band leaves the
    age out and keeps the cause whole (it was cut, and the age dropped too, before #95). English never meets a step
    without the guard, and with it only the countdown's and the age's, never the cause's."""
    locale = i18n.Locale(setting="system", language="es", system="en", format="es-CO", clock=12, offered=("en",))
    model = skin_words.models(skin_words._night_city_states, locale)["age/stale/100d"]
    ink = _ink()
    cause, age = nc._band_text(THEME, model), nc._age_tag(model)
    assert (cause, age) == ("RELINK // USE CLAUDE CODE", "100 DÍAS OLD")
    assert nc._band_plan(ink, model, cause, age, None) == nc.BandPlan(cause, None, None, ("age",))
    frame = frame_layout.drawn(lambda: SKIN.render_floating(model, THEME, THEME.variants[0], 1.0, None))
    texts = [run.text for run in frame.runs]
    assert cause in texts and age not in texts and frame_layout.check(frame) == []
    for name, english_model in english.items():
        if english_model.status not in nc.BAND_STATUSES:
            continue
        hold = nc._hold_word(english_model)
        plan = nc._band_plan(ink, english_model, nc._band_text(THEME, english_model), nc._age_tag(english_model), hold)
        assert "cause" not in plan.gave_way and (hold is not None or plan.gave_way == ()), (name, plan)


def test_a_missing_sign_in_keeps_its_cause_beside_a_holding_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing sign-in keeps the last reading (usage_api's NO_CREDENTIALS result), so the guard can hold over the
    no-login band (#95's review): English's cause stays whole beside HOLD alone, its countdown given way, as before
    #95; the budgets hold band.no_login to that room as well as to the whole band (band_hold_text, band_text_no_age):
    the longest cause the skin keeps whole beside the hold chip fits band_hold_text's limit, and one letter more is cut.
    The frame check draws the state (frame_layout.night_city_states)."""
    from usage_monitor.render.model import build_model

    state = frame_layout.night_city_states()["band_hold/no_credentials"]
    model = build_model(state, dump.SAMPLE_NOW, show_scoped="always")
    assert model.status is nc.ApiStatus.NO_CREDENTIALS and model.guard is not None and model.guard.holding
    ink = _ink()
    plan = nc._band_plan(ink, model, nc._band_text(THEME, model), nc._age_tag(model), HOLD_WORD(model))
    assert plan == nc.BandPlan("NO LOGIN // OPEN DASHBOARD", None, "HOLD", ("countdown",))
    hold_box, whole_box = (budgets.BOX[f"night_city.floating.{part}"] for part in ("band_hold_text", "band_text_no_age"))
    assert all("widget.night_city.band.no_login" in box.keys for box in (hold_box, whole_box))
    assert plan.cause in hold_box.english

    def whole(n: int) -> bool:
        _catalog(monkeypatch, band__no_login="W" * n)
        drawn = build_model(state, dump.SAMPLE_NOW, show_scoped="always")
        cause = nc._band_text(THEME, drawn)
        assert cause == "W" * n
        return nc._band_plan(ink, drawn, cause, nc._age_tag(drawn), HOLD_WORD(drawn)).cause == cause

    low, high = 1, 60                                   # whole(low) holds, whole(high) not
    while high - low > 1:
        middle = (low + high) // 2
        low, high = (middle, high) if whole(middle) else (low, middle)
    assert hold_box.width("W" * low) <= hold_box.limit + 0.01 < hold_box.width("W" * (low + 1)), (low, hold_box.limit)
    assert hold_box.limit < whole_box.limit


# ── The strip ─────────────────────────────────────────────────────────────────

LABEL_ROOM = nc.STRIP_BAR_X - nc.STRIP_LABEL_X - nc.STRIP_LABEL_AIR


@pytest.mark.parametrize("scale", budgets.STRIP_SCALES)
def test_a_strip_label_past_its_room_shrinks_then_is_left_out(scale: float, monkeypatch: pytest.MonkeyPatch) -> None:
    """A window label is drawn whole at 11 px where it fits its 16 px (to its bar, less STRIP_LABEL_AIR), smaller (down
    to 9 px, its tracking 0.12 em) where it does not, and left out where even 9 px does not fit: "5H" stays, a wide
    pair ("WK", 17.7 px at 11 px) shrinks, three letters ("7GN") and "[5H ı]" go: two characters is what fits. A shrunk
    label's ink keeps the frame check's MIN_GAP off its bar."""
    ink = _ink(scale, strip=True)
    assert nc._strip_label(ink, "5H", LABEL_ROOM) is nc.STATUS and nc._strip_label(ink, "7D", LABEL_ROOM) is nc.STATUS
    spec = nc._strip_label(ink, "WK", LABEL_ROOM)
    assert spec is not None and spec.size < nc.STATUS.size and ink.width("WK", spec) <= LABEL_ROOM
    assert spec.tracking == pytest.approx(spec.size * 0.12)
    assert nc._strip_label(ink, "7GN", LABEL_ROOM) is None and nc._strip_label(ink, "[5H ı]", LABEL_ROOM) is None
    _catalog(monkeypatch, window__session="WK", window__weekly="[5H ı]")
    model = dump.sample_model("normal3")
    frame = frame_layout.drawn(lambda: SKIN.render_taskbar(model, THEME, THEME.variants[0], round(nc.STRIP_H * scale),
                                                           scale))
    texts = [run.text for run in frame.runs]
    assert "WK" in texts and "[5H ı]" not in texts and frame_layout.check(frame) == []
    label = next(run for run in frame.runs if run.text == "WK")
    assert label.box[2] <= (nc.STRIP_BAR_X - frame_layout.MIN_GAP) * frame.kx, (label.box, frame.kx)


@pytest.mark.parametrize("kind", ["live", "status", "hold"])
def test_a_strip_word_shrinks_before_it_is_cut(kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The strip's LIVE, a status word and the HOLD chip's word shrink (_fit_type, down to STRIP_FLOOR) before they are
    cut, and are cut only at the floor; each is drawn as the fit says, inside its tag, at every strip scale."""
    key, state, spec, room = {
        "live": ("state__live", "normal3", nc.STATUS, nc.STRIP_TAG[2] - 4 - nc.STRIP_WORD_X),
        "status": ("state__stale", "stale3", nc.STATUS, nc.STRIP_TAG[2] - nc.STRIP_TAG[0] - 8),
        "hold": ("guard__hold", "guard_hold_session", nc.CHIP, nc.STRIP_TAG[2] - nc.STRIP_TAG[0] - 17)}[kind]
    ink = _ink(strip=True)
    steps = []
    for length in range(2, 14):
        word = "W" * length
        fit = nc._fit_type(ink, word, spec, room, nc.STRIP_FLOOR)
        cut = nc._cut(ink, word, fit, room)
        assert cut == word or (fit.size == nc.STRIP_FLOOR and cut.endswith(DOTS)), (word, fit, cut)
        steps.append((fit.size, cut != word))
        _catalog(monkeypatch, **{key: word})
        model = dump.sample_model(state)
        for scale in (1.0, *budgets.STRIP_SCALES[::4]):
            frame = frame_layout.drawn(lambda: SKIN.render_taskbar(model, THEME, THEME.variants[0],
                                                                   round(nc.STRIP_H * scale), scale))
            assert cut in [run.text for run in frame.runs] and frame_layout.check(frame) == [], (word, scale)
    assert steps[0] == (spec.size, False) and steps[-1] == (nc.STRIP_FLOOR, True) and steps == sorted(
        steps, key=lambda step: (-step[0], step[1]))


# ── Marks (Q1) ────────────────────────────────────────────────────────────────

def _letters() -> list[str]:
    """Printable ASCII, the French, Spanish and Turkish letters (test_font_coverage's floor) in both cases by each
    language's rule, the pseudo-locale's letters, the letters with a comma below (Ș ș Ț ț), the deepest marks the faces
    have, and with the other marks below a language may bring: an ogonek (Ą ą Ę ę Į į Ų ų) and a dot below (Ạ ạ Ọ ọ
    Ṣ ṣ)."""
    from test_font_coverage import FLOOR

    found = (set(string.printable.strip()) | set(i18n.pseudo_text(string.ascii_letters)) | {"…"} | set("ȘșȚț")
             | set("ĄąĘęĮįŲų") | set("ẠạỌọṢṣ"))
    for language, text in FLOOR.items():
        found |= set(text) | set(casing.upper(text, language))
    return sorted(ch for ch in found if len(ch) == 1)


# Where each growing box stands: (its face's styles, canvases (width, heights, scales), places (top edge, baseline,
# bottom edge), what its edges are: "stroke" an outline's 1 px inside the edge, "edge" a hazard strip or a fill). The
# heights are every frame's: the floating widget at 2 to 4 rows, the hover card at 2 to 4 with no guard block or one of
# 1 to 3 lines.
_TALL = (96, 126, 156, 132, 182, 232, 157, 170, 183, 207, 220, 233)
_ROWS = (28, 58, 88)
_CARD_ROWS = (28, 78, 128)
_INFO = tuple(top + nc.TOOLTIP_INFO for top in _CARD_ROWS)
_STRIP_SCALES = tuple(sorted({round(max(0.25, min(s, (round(h * s) - 2) / nc.STRIP_H)), 6)
                              for s in budgets.SCALES for h in dump.TASKBAR_HEIGHTS_AT_1X}))
_FLOOR9 = nc.STATUS._replace(size=nc.STRIP_FLOOR, tracking=0.3)
BOXES: dict[str, tuple[tuple[Any, ...], tuple[Any, ...], tuple[tuple[float, float, float], ...], str]] = {
    "chip": ((nc.CHIP,), (nc.FLOAT_W, _TALL, budgets.SCALES),
             ((nc.HEADER_TOP - 1, nc.HEAD_BASE, nc.HEADER_TOP - 1 + nc.CHIP_H),
              *((top, top + 10, top + nc.CHIP_H) for top in _ROWS + _CARD_ROWS + _INFO)), "stroke"),
    "live_tag": ((nc.STATUS,), (nc.FLOAT_W, _TALL, budgets.SCALES), ((nc.TAG_TOP, nc.BOXED_BASE, nc.TAG_BOTTOM),),
                 "stroke"),
    "strip_tag": ((nc.STATUS, _FLOOR9), (nc.STRIP_W, (nc.STRIP_H,), _STRIP_SCALES),
                  ((nc.STRIP_TAG[1], nc.STRIP_WORD_BASE, nc.STRIP_TAG[3]),), "stroke"),
    "reset": ((nc.RESET,), (nc.FLOAT_W, _TALL, budgets.SCALES),
              tuple((top, top + nc.RESET_BASE, top + nc.CHIP_H) for top in _ROWS + _INFO), "stroke"),
    "band": ((nc.BAND_TYPE, nc.CHIP), (nc.FLOAT_W, _TALL, budgets.SCALES),
             ((nc.BAND[1] + nc.HAZARD_STRIP, nc.BAND_BASE, nc.BAND[3] - nc.HAZARD_STRIP),), "edge"),
    "band_age": ((nc.RESET,), (nc.FLOAT_W, _TALL, budgets.SCALES),
                 ((nc.BAND[1] + nc.HAZARD_STRIP, nc.BAND_BASE, nc.BAND[3] - nc.HAZARD_STRIP),), "edge"),
}
# Which boxes grow down, and the name _mark_growth knows each by.
GROWS = {"chip": "chip", "live_tag": "tag", "strip_tag": "tag", "reset": "reset", "band": "band", "band_age": "band_age"}


@lru_cache(maxsize=None)
def _ink_rows(spec: Any, ch: str, width: float, scale: float) -> tuple[int, int] | None:
    """(top, bottom) of ``ch``'s ink in buffer px from its baseline's buffer row, on a canvas of ``width`` at ``scale``
    (the face's size follows the canvas's horizontal scale, which its height never changes)."""
    ink = nc._Ink(nc._canvas(width, 100, scale, THEME), nc.Palette.for_variant(THEME, THEME.variants[0]),
                  RenderOptions())
    layer, _ = ink.layer(ch, spec, 40, 50)
    box = layer.mask.point(lambda value: 255 if value >= frame_layout.INK else 0).getbbox()
    if box is None:
        return None
    base = round(50 * ink.cv.ky)
    return layer.y + box[1] - base, layer.y + box[3] - base


@lru_cache(maxsize=None)
def _canvas(width: float, height: float, scale: float) -> Any:
    return nc._canvas(width, height, scale, THEME)


def _air(ch: str, box: str, up: int, down: int) -> tuple[int, int]:
    """(least air above, least air below), buffer px, between ``ch``'s ink and the edges of ``box`` grown ``up`` and
    ``down`` px, at every place, canvas height and scale it stands at: an outline's stroke (the 1 px inside its edge),
    or a hazard strip's or a fill's edge."""
    specs, (width, heights, scales), places, kind = BOXES[box]
    above = below = 99
    for spec in specs:
        for scale in scales:
            for height in heights:
                cv = _canvas(width, height, scale)
                rows = _ink_rows(spec, ch, width, scale)
                if rows is None:
                    continue
                for top, base, bottom in places:
                    ink_top, ink_bottom = (round(base * cv.ky) + row for row in rows)
                    edge_top = cv._rect_px(0, top - up, 1, top - up + 1)[3 if kind == "stroke" else 1]
                    edge_bottom = cv._rect_px(0, bottom + down - 1, 1, bottom + down)[1 if kind == "stroke" else 3]
                    above, below = min(above, ink_top - edge_top), min(below, edge_bottom - ink_bottom)
    return above, below


def _by_rule(ch: str, side: str) -> bool:
    """Whether ``ch`` is in the rule's own class for a side: a capital with a mark above, or a letter with a mark below
    (combining class 202 or 220, as Nostromo's rule: it grows the box whether its mark reaches the edge or not: Ț's
    comma sits higher than Ș's, a dot below higher than a cedilla)."""
    import unicodedata

    parts = unicodedata.normalize("NFD", ch)
    if side == "up":
        return parts[0].isupper() and any(unicodedata.combining(part) == nc._ABOVE for part in parts[1:])
    return any(unicodedata.combining(part) in nc._BELOW for part in parts[1:])


def test_the_marks_that_grow_a_box_are_the_ones_that_reach_its_edge() -> None:
    """Q1 decides by characters, per face, held to the faces: of every letter the four languages and the pseudo-locale
    draw (and the comma-below letters), each whose ink keeps less than frame_layout.TOUCH of air over the top stroke of
    any box of a face (or puts ink on the band's hazard strip or out of a fill) at some scale and card height grows
    every box of that face up, and a letter that grows them and reaches none is in the rule's own class (a capital with
    a mark above); the same below for the outlined tags (a mark below of class 202 or 220, DESCENDERS: an ogonek reaches
    the strip tag's stroke, a dot below grows it by the rule alone) and the outlined chip (a comma below alone). Each
    grown box keeps that air round every such letter, and its growth is the least that does: one px less leaves a
    letter on the stroke (Share Tech Mono's 2 px up, the tags' 2 px down)."""
    wrong = []
    reach: dict[tuple[str, str], set[str]] = {}
    grows: dict[tuple[str, str], set[str]] = {}
    for box, name in GROWS.items():
        need = frame_layout.TOUCH if BOXES[box][3] == "stroke" else 0
        for ch in _letters():
            up, down = nc._mark_growth(ch, name)
            above, below = _air(ch, box, 0, 0)
            face = ("up", nc.MARK_FACES[name])
            low = ("down", "tag" if name == "tag" else name)
            if above < need:
                reach.setdefault(face, set()).add(ch)
            if below < need:
                reach.setdefault(low, set()).add(ch)
            if up:
                grows.setdefault(face, set()).add(ch)
            if down:
                grows.setdefault(low, set()).add(ch)
            if up or down:
                grown = _air(ch, box, up, down)
                if (up and grown[0] < need) or (down and grown[1] < need):
                    wrong.append((box, ch, "grown", grown, (up, down)))
    for key in set(reach) | set(grows):
        reached, grown = reach.get(key, set()), grows.get(key, set())
        if not reached <= grown or not all(_by_rule(ch, key[0]) for ch in grown - reached):
            wrong.append((key, "".join(sorted(reached - grown)), "".join(sorted(ch for ch in grown - reached
                                                                                  if not _by_rule(ch, key[0])))))
    assert wrong == []
    assert set(reach) == {("up", "body"), ("up", "mono"), ("down", "tag"), ("down", "chip")}
    assert grows[("down", "chip")] == set("ȘșȚț") and reach[("down", "chip")] == set("Șș")
    assert set("ĄąĘęĮįŲų") <= reach[("down", "tag")] and set("ẠạỌọṢṣ") <= grows[("down", "tag")] - reach[("down", "tag")]
    least = {("reset", "up"): (nc.MARK_GROWTH_UP["mono"] - 1, 0), ("band_age", "up"): (nc.MARK_GROWTH_UP["mono"] - 1, 0),
             ("live_tag", "down"): (0, nc.MARK_GROWTH_DOWN - 1), ("strip_tag", "down"): (0, nc.MARK_GROWTH_DOWN - 1)}
    for (box, side), growth in least.items():
        index = 0 if side == "up" else 1
        assert min(_air(ch, box, *growth)[index] for ch in _letters()
                   if nc._mark_growth(ch, GROWS[box])[index]) < frame_layout.TOUCH, (box, side)
    assert nc.MARK_GROWTH_UP["body"] == 1 and nc.CHIP_GROWTH_DOWN == 1      # 0 px leaves the marks on the stroke


# Boxed words with marks for every growing box, in the catalog's keys (a few real French, Spanish and Turkish words, a
# comma below, a riser and descenders): each must be drawn with no mark on an outline, the hazard strip or out of a fill.
MARKED = {"state__live": "ÇEVRİMİÇİ", "state__stale": "ÉCHU", "state__offline": "ÇEVRİMDIŞI QUOTA",
          "state__warning": "ȘTIRE", "guard__mark": "GÜVENLİK", "pace__ahead": "ÖNDE {points}",
          "pace__behind": "DETRÁS {points}", "resets__at": "RÉINIT ({clock})", "band__offline": "SIN SEÑAL // RED CAÍDA",
          "age__old": "[{age}] ÖNCE", "guard__hold": "ÉN PAUSE"}
MARK_CHECKS = ("run on an outline", "run touches an outline", "run across a fill", "run on the hazard band",
               "run on a rule")


def _english_box_words(models: dict[str, Any]) -> dict[str, set[str]]:
    """Every English text drawn in a growing box, by the name _mark_growth knows the box by."""
    words: dict[str, set[str]] = {name: set() for name in set(GROWS.values())}
    for model in models.values():
        live = model.status is nc.ApiStatus.LIVE and not model.stale
        words["tag"] |= {"LIVE", str(nc.STRIP_WORDS[model.status])}
        words["chip"] |= {str(model.status_text), "GUARD", "WARN"}
        words["chip"] |= {nc._pace_text(row)[0] for row in model.rows}
        words["reset"] |= {text for row in model.rows for tooltip in (False, True)
                           if (text := nc._reset_text(row, tooltip))}
        words["band"] |= {nc._band_text(THEME, model), nc._hold_word(model) or "HOLD"}
        words["band_age"] |= {nc._age_tag(model) or ""}
        assert live or model.status_text
    return words


def test_a_box_grows_round_marks_and_never_round_english(english: dict[str, Any],
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """No English word grows its box (Blackwall's LIVE tag keeps 8 to 21 px), and words with marks grow theirs so that no
    frame of a boxed, a plain or the plate look, at any scale, has a mark on an outline, touching one with no air, on
    the hazard strip or out of a fill; Spanish's RST SÁB grows the reset box today's English leaves as it was."""
    assert {(name, word): nc._mark_growth(word, name) for name, words in _english_box_words(english).items()
            for word in words if nc._mark_growth(word, name) != (0, 0)} == {}
    live = frame_layout.drawn(lambda: SKIN.render_floating(english["normal3/auto"], THEME, THEME.variants[0], 1.0, None))
    tag = [rect for rect in live.strokes if rect[2] == nc.RIGHT * 2]
    assert (min(rect[1] for rect in tag), max(rect[3] for rect in tag)) == (nc.TAG_TOP * 2, nc.TAG_BOTTOM * 2)
    assert nc._mark_growth("RST SÁB, 20:12", "reset") == (nc.MARK_GROWTH_UP["mono"], 0)
    _catalog(monkeypatch, **MARKED)
    for state in ("normal3", "warning", "paused", "offline", "guard_armed", "pace/ahead_100", "pace/behind_100",
                  "band_hold/offline3", "age/offline/100d"):
        model = (skin_words.models(skin_words._night_city_states)[state] if "/" in state
                 else dump.sample_model(state))
        if state == "paused":
            model = dataclasses.replace(model, status_text="EN PAUSE ȘI Ç")
        for variant in LOOKS:
            for surface, draw in frame_layout.surfaces("night_city", model, THEME.variant(variant)):
                frame = frame_layout.drawn(draw)
                assert [found for found in frame_layout.check(frame) if found[0] in MARK_CHECKS] == [], (
                    state, variant, surface)


@pytest.mark.parametrize("scale", budgets.SCALES)
def test_the_grown_boxes_keep_off_their_neighbours(scale: float) -> None:
    """At every scale and frame height, in the buffer pixels the canvas draws: a row's grown chip and reset box keep air
    under the row above (its bar, or the header and the band), the hover card's grown info line keeps air under its
    row's bar, the grown header boxes and the band keep air under the frame's top and over the first row's grown
    boxes, and the grown strip tag stays inside the strip with air. The two conflicts GROWN_MEETS records: a row's chip
    grown CHIP_GROWTH_DOWN round a comma below meets its row's rail with no air, never overlapping; and the row's pace
    tick crosses the bottom stroke of its WARN chip and of Blackwall's reset box by design (English's too), starting on
    the ungrown box's stroke and no higher than the grown chip's last row inside, and ending on the bar's top."""
    up_body, up_mono = nc.MARK_GROWTH_UP["body"], nc.MARK_GROWTH_UP["mono"]
    up = max(up_body, up_mono)
    for height in _TALL:
        cv = nc._canvas(nc.FLOAT_W, height, scale, THEME)

        def rows(y0: float, y1: float) -> tuple[int, int]:
            return cv._rect_px(0, y0, 1, y1)[1::2]

        header_bottom = max(rows(nc.TAG_TOP, nc.TAG_BOTTOM + nc.MARK_GROWTH_DOWN)[1],
                            rows(nc.HEADER_TOP - 1, nc.HEADER_TOP - 1 + nc.CHIP_H + nc.CHIP_GROWTH_DOWN)[1],
                            rows(nc.BAND[1], nc.BAND[3])[1])
        assert rows(nc.BAND[1] - up, nc.BAND[3])[0] > rows(0, 1)[1], (height, scale)
        assert rows(nc.HEADER_TOP - 1 - up_body, 0)[0] > rows(0, 1)[1]
        for pitch, info in ((nc.ROW_PITCH, False), (nc.TOOLTIP_PITCH, True)):
            for index in range(3):
                top = nc.ROW_TOP + pitch * index
                if top + nc.BAR_Y + nc.BAR_H > height:
                    continue
                if index == 0:
                    over = header_bottom
                elif info:
                    over = rows(0, top - pitch + nc.TOOLTIP_INFO + nc.CHIP_H + nc.CHIP_GROWTH_DOWN)[1]
                else:
                    over = rows(0, top - pitch + nc.BAR_Y + nc.BAR_H)[1]
                assert over < rows(top - up_mono, top)[0] and over < rows(top - up_body, top)[0], (height, pitch, index)
                rail = rows(top + nc.RAIL_Y, top + nc.RAIL_Y + nc.RAIL_H)
                chip = rows(top, top + nc.CHIP_H + nc.CHIP_GROWTH_DOWN)
                assert chip[1] == rail[0] < rail[1], (height, scale, pitch, index)       # meets, never overlaps
                tick = rows(top + nc.TICK_TOP, top + nc.TICK_TOP + nc.TICK_H)
                stroke = rows(top + nc.CHIP_H - 1, top + nc.CHIP_H)          # the ungrown chip's and reset box's
                inside = rows(top + nc.CHIP_H + nc.CHIP_GROWTH_DOWN - 2, top + nc.CHIP_H + nc.CHIP_GROWTH_DOWN - 1)
                bar = rows(top + nc.BAR_Y, top + nc.BAR_Y + nc.BAR_H)
                assert stroke[0] <= tick[0] < stroke[1] and inside[0] <= tick[0] and chip[1] < tick[1] <= bar[0], (
                    height, scale, pitch, index, tick)                                   # crosses, by design
                if info:
                    bar = rows(top + nc.BAR_Y, top + nc.BAR_Y + nc.BAR_H)[1]
                    line = top + nc.TOOLTIP_INFO
                    assert bar < rows(line - up_mono, line)[0] and bar < rows(line - up_body, line)[0], (height, index)
    strip = nc._canvas(nc.STRIP_W, nc.STRIP_H, scale, THEME)
    body = strip._rect_px(0, 1, 1, nc.STRIP_H - 1)[1::2]
    tag = strip._rect_px(0, nc.STRIP_TAG[1] - up_body, 1, nc.STRIP_TAG[3] + nc.MARK_GROWTH_DOWN)[1::2]
    assert body[0] < tag[0] and tag[1] < body[1], scale
    assert nc.GROWN_MEETS == (
        "a row's WARN chip grown CHIP_GROWTH_DOWN px down round a comma below (Ș ș) meets its row's rail (top + 15)",
        "a row's pace tick (top + 13 to top + 19, drawn after the chip and the reset box) crosses the bottom stroke of "
        "the row's WARN chip and of Blackwall's reset box by design, in English too; round a comma below it crosses the "
        "grown WARN chip's stroke and its last row inside")
    assert (nc.TICK_TOP, nc.TICK_TOP + nc.TICK_H) == (13, 19)          # as GROWN_MEETS words it
