"""Nostromo's fits (#94): what gives way where a language's words are longer than English's, read from the pure helpers
the skin draws by (_header_plan, _info_plan, _message_fit, _strip_tag, _mark_growth). English never meets any of them,
so its frames stay as they were; the owner defaults (Q1, the marks' room; Q2, the header's order) are each one constant
or tuple in nostromo.py."""
from __future__ import annotations

import dataclasses
import string
from typing import Any

import pytest

import budgets
import frame_layout
import skin_words
from usage_monitor import casing, i18n
from usage_monitor.render import dump
from usage_monitor.render.skins import get_skin
from usage_monitor.render.skins import nostromo as nos

THEME = dump.load_theme_objects()["nostromo"]
SKIN, VARIANT = get_skin("nostromo"), THEME.variants[0]


def _cv(scale: float = 1.0, strip: bool = False) -> Any:
    return nos._canvas(*((nos.STRIP_W, nos.STRIP_H) if strip else (nos.FLOAT_W, 100)), scale, THEME)


@pytest.fixture(scope="module")
def english() -> dict[str, Any]:
    """Every state the English record covers (tests/skin_words.py), as English view models."""
    return skin_words.models(skin_words._nostromo_states)


# ── The header (Q2) ───────────────────────────────────────────────────────────

def _plan(monkeypatch: pytest.MonkeyPatch, pill: str, age: str | None, word: str, style: str = "label", *,
          holding: bool = False, scale: float = 1.0) -> nos.HeaderPlan:
    """_header_plan for a header that draws these texts (a holding guard's chip when ``holding``)."""
    model = dump.sample_model("guard_hold_session" if holding else "normal3")
    monkeypatch.setattr(nos, "_age_text", lambda _model: age)
    monkeypatch.setattr(nos, "_state_word", lambda _model: (word, style))
    monkeypatch.setattr(nos, "_hold_word", lambda _model: word if holding else None)
    return nos._header_plan(_cv(scale), dataclasses.replace(model, status_text=pill))


def _width(plan: nos.HeaderPlan, scale: float = 1.0) -> float:
    cv = _cv(scale)
    semi, reg = cv.font(nos.FONT_SEMI, nos.LABEL_SIZE), cv.font(nos.FONT_REG, nos.LABEL_SIZE)
    age = 6 + cv.advance(plan.age, reg, nos.QUALIFIER_TRACKING) if plan.age else 0.0
    return (10 + cv.advance(plan.pill, semi, nos.LABEL_TRACKING) + age + nos.QUALIFIER_GAP
            + cv.advance(plan.word, semi, nos.LABEL_TRACKING) + (8 if plan.style == "chip" else 0))


ROOM = nos.RIGHT - nos.PILL_X


def test_the_header_gives_way_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Q2: the state word (or the CRITICAL chip) is cut first, then the age goes, then the pill's word is cut, then the
    hold chip drops its countdown; each step only while the line is still over, and the line then fits."""
    long_word = "W" * 30
    plan = _plan(monkeypatch, "LIVE", "12 S", long_word)
    assert (plan.gave_way, plan.pill, plan.age) == (("state_word",), "LIVE", "12 S")
    assert plan.word.endswith(nos.ELLIPSIS) and long_word.startswith(plan.word[:-1]) and _width(plan) <= ROOM
    chip = _plan(monkeypatch, "LIVE", "12 S", "C" * 30, "chip")
    assert chip.gave_way == ("state_word",) and chip.word.endswith(nos.ELLIPSIS) and _width(chip) <= ROOM
    # the word at its floor beside the pill and the age still does not fit: the age goes, and the word takes its room
    plan = _plan(monkeypatch, "P" * 26, "12 S", long_word)
    assert (plan.gave_way, plan.pill, plan.age) == (("state_word", "age"), "P" * 26, None)
    assert len(plan.word) > 2 and _width(plan) <= ROOM
    plan = _plan(monkeypatch, "P" * 40, "12 S", long_word)
    assert plan.gave_way == ("state_word", "age", "pill") and plan.age is None
    assert plan.word == "W" + nos.ELLIPSIS and plan.pill.endswith(nos.ELLIPSIS) and _width(plan) <= ROOM
    # holding: the chip is never cut; the age, then the pill, then the countdown give way
    hold = "HOLD 23H 59M"
    plan = _plan(monkeypatch, "P" * 20, "59 M", hold, "chip", holding=True)
    assert (plan.gave_way, plan.word) == (("age", "pill"), hold) and plan.pill.endswith(nos.ELLIPSIS)
    assert _width(plan) <= ROOM
    plan = _plan(monkeypatch, "P" * 20, "59 M", "HOLD " + "9" * 30, "chip", holding=True)
    assert (plan.gave_way, plan.word) == (("age", "pill", "countdown"), "HOLD") and _width(plan) <= ROOM
    # every step taken comes after every earlier step that could be taken, at every length
    for pill in range(1, 45, 3):
        for word in range(1, 45, 3):
            plan = _plan(monkeypatch, "P" * pill, "12 S", "W" * word)
            taken = plan.gave_way
            assert list(taken) == [step for step in nos.HEADER_GIVE_WAY[:len(taken)]], (pill, word, taken)
            assert _width(plan) <= ROOM or taken == ("state_word", "age", "pill"), (pill, word)


def test_the_header_takes_no_step_while_it_fits(english: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Every English header, in every recorded state at every scale, draws its texts whole; and a header is left whole
    up to its room, and gives way 1 px past it."""
    widest = (0.0, None)
    for name, model in english.items():
        for scale in budgets.SCALES:
            plan = nos._header_plan(_cv(scale), model)
            assert plan.gave_way == () and (plan.pill, plan.age, plan.word) == (
                model.status_text, nos._age_text(model), nos._state_word(model)[0]), (name, scale)
            widest = max(widest, (_width(plan, scale), name), key=lambda item: item[0])
    width, name = widest
    assert width < ROOM, widest
    model = english[name]
    assert nos._header_plan(_cv(), model, right=nos.PILL_X + _width(nos._header_plan(_cv(), model)) + 1).gave_way == ()
    assert nos._header_plan(_cv(), model, right=nos.PILL_X + _width(nos._header_plan(_cv(), model)) - 1).gave_way


# ── The hover line ────────────────────────────────────────────────────────────

def test_the_hover_line_shortens_its_pace_then_leaves_it_out(english: dict[str, Any]) -> None:
    """The reset stays whole: as it grows, the pace takes its short form (the signed points), then is left out; a pace
    with no points (ON PACE) is left out at once. English's lines never give way."""
    for name, model in english.items():
        for row in model.rows:
            for scale in budgets.SCALES:
                info = nos._info_plan(_cv(scale), row, model.locale)
                assert info.gave_way == () and info.pace == nos._pace_text(nos._palette(THEME, VARIANT, model), row,
                                                                           locale=model.locale)[0], (name, scale)
    ahead = english["pace/ahead_100"].rows[0]
    on = english["pace/on"].rows[0]
    for row, forms in ((ahead, [("AHEAD +100 PTS", ()), ("+100 PTS", ("pace",)), (None, ("pace", "pace_short"))]),
                       (on, [("ON PACE", ()), (None, ("pace",))])):
        seen: list[tuple[str | None, tuple[str, ...]]] = []
        for extra in range(0, 40):
            info = nos._info_plan(_cv(), dataclasses.replace(row, resets_text="WED 12:59 PM" + " X" * extra),
                                  i18n.ENGLISH)
            assert info.reset == "RESETS WED 12:59 PM" + " X" * extra
            if not seen or seen[-1] != (info.pace, info.gave_way):
                seen.append((info.pace, info.gave_way))
        assert seen == forms


# ── The sign-in box ───────────────────────────────────────────────────────────

ROOM_MESSAGE = (nos.RIGHT - 1) - (nos.LEFT + 9)


@pytest.mark.parametrize("scale", budgets.SCALES)
def test_a_long_sign_in_line_shrinks_before_it_is_cut(scale: float) -> None:
    """Tracking to 0, then 9 px, and only then the ellipsis; both lines take one step; English's lines take none."""
    cv = _cv(scale)
    for reason in nos.AUTH_KEYS:
        lines = nos._message_lines(dataclasses.replace(dump.sample_model("stale3"), stale_reason=reason))
        assert nos._message_fit(cv, lines, ROOM_MESSAGE) == nos.MessageFit(nos.LABEL_SIZE, nos.MESSAGE_TRACKING, lines)
    steps = []
    for length in range(30, 50):
        fit = nos._message_fit(cv, ("SHORT", "X" * length), ROOM_MESSAGE)
        semi = cv.font(nos.FONT_SEMI, fit.size)
        assert all(cv.advance(line, semi, fit.tracking) <= ROOM_MESSAGE for line in fit.lines), length
        step = nos.MESSAGE_STEPS.index((fit.size, fit.tracking))
        cut = fit.lines[1].endswith(nos.ELLIPSIS)
        assert fit.lines[0] == "SHORT" and (cut or fit.lines[1] == "X" * length) and (not cut or step == 2), length
        steps.append((step, cut))
    assert steps == sorted(steps) and {step for step, _cut in steps} == {0, 1, 2} and steps[-1] == (2, True)


# ── The strip's window tags ───────────────────────────────────────────────────

@pytest.mark.parametrize("scale", budgets.STRIP_SCALES)
def test_a_strip_tag_past_its_room_shrinks_then_is_left_out(scale: float, monkeypatch: pytest.MonkeyPatch) -> None:
    """A tag is drawn whole at 11 px where it fits its 22 px, smaller (down to 9 px, its tracking 0.1 em) where it does
    not, and left out where even 9 px does not fit: "5H" stays, a three-letter tag shrinks where it must (at 1.0 it
    must: 23.1 px at 11 px), "[5H ı]" goes."""
    cv = _cv(scale, strip=True)
    room = nos.STRIP_GROUPS[0][1] - nos.STRIP_GROUPS[0][0]
    assert nos._strip_tag(cv, "5H", room) == (nos.STRIP_LABEL_SIZE, nos.STRIP_LABEL_TRACKING)
    fit = nos._strip_tag(cv, "7GN", room)
    assert fit is not None
    size, tracking = fit
    whole =cv.advance("7GN", cv.font(nos.FONT_SEMI, nos.STRIP_LABEL_SIZE), nos.STRIP_LABEL_TRACKING)
    assert (size < nos.STRIP_LABEL_SIZE) == (whole > room) and tracking == pytest.approx(size / 10)
    assert cv.advance("7GN", cv.font(nos.FONT_SEMI, size), tracking) <= room
    assert scale != 1.0 or size < nos.STRIP_LABEL_SIZE
    assert nos._strip_tag(cv, "[5H ı]", room) is None
    english = i18n.load_catalog("en").english()
    catalog = i18n.Catalog("en", {}, {**english, "widget.nostromo.window.session": "7GN",
                                      "widget.nostromo.window.weekly": "[5H ı]"})
    monkeypatch.setattr(i18n, "app_catalog", lambda language: catalog)
    model = dump.sample_model("normal3")
    frame = frame_layout.drawn(lambda: SKIN.render_taskbar(model, THEME, VARIANT, round(nos.STRIP_H * scale), scale))
    texts = [run.text for run in frame.runs]
    assert "7GN" in texts and "[5H ı]" not in texts and frame_layout.check(frame) == []


# ── Marks (Q1) ────────────────────────────────────────────────────────────────

def _letters() -> list[str]:
    """Printable ASCII, the French, Spanish and Turkish letters (test_font_coverage's floor) in both cases by each
    language's rule, the pseudo-locale's letters and the ellipsis."""
    from test_font_coverage import FLOOR

    found = set(string.printable.strip()) | set(i18n.pseudo_text(string.ascii_letters)) | {nos.ELLIPSIS}
    for language, text in FLOOR.items():
        found |= set(text) | set(casing.upper(text, language))
    return sorted(ch for ch in found if len(ch) == 1)


def _ink_reach(ch: str) -> tuple[float, float]:
    """(least gap above, least gap below) of ``ch``'s ink to the header's 12 px box, over every scale, in logical px."""
    above = below = 99.0
    for scale in budgets.SCALES:
        cv = _cv(scale)
        semi = cv.font(nos.FONT_SEMI, nos.LABEL_SIZE)
        layer, _ = cv.text_layer(ch, 30, cv.baseline_in_box(nos.HEADER_TOP + 1, 10, semi), semi, nos.LABEL_TRACKING)
        box = layer.mask.point(lambda value: 255 if value >= frame_layout.INK else 0).getbbox()
        if box is not None:
            above = min(above, (layer.y + box[1]) / cv.ky - nos.HEADER_TOP)
            below = min(below, nos.HEADER_TOP + nos.HEADER_H - (layer.y + box[3]) / cv.ky)
    return above, below


def test_the_marks_that_grow_a_box_are_the_ones_that_reach_its_edge() -> None:
    """Q1 decides by characters: a capital with a mark above grows a box up, a mark below or one of DESCENDERS grows it
    down. Held to the face: of every letter the four languages and the pseudo-locale draw, exactly those whose ink comes
    within 1 px (an outline's width) of the 12 px box's edge at some scale grow it."""
    wrong = []
    for ch in _letters():
        above, below = _ink_reach(ch)
        up, down = nos._mark_growth(ch)
        if (up > 0) != (above < 1) or (down > 0) != (below < 1):
            wrong.append((ch, round(above, 2), round(below, 2), up, down))
    assert wrong == []


def _english_box_words(models: dict[str, Any]) -> set[str]:
    """Every English text drawn in a pill, the header's chip or a cap tag (a state word not in a chip has no box)."""
    words: set[str] = set()
    for model in models.values():
        words.add(str(model.status_text))
        word, style = nos._state_word(model)
        if style == "chip":
            words.add(word)
        words.update(text for row in model.rows if (text := nos._cap_text(model, row)))
    return words


MARK_CHECKS = ("run on an outline", "run across a fill", "run on the hazard band", "run on a rule")


def test_a_pill_grows_round_marks_and_never_round_english(english: dict[str, Any],
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """No English pill, chip or cap tag grows (LIVE's pill keeps its 6 to 18 px), and a word with marks grows its
    boxes so that no frame at any scale has a mark on an outline or out of a chip: real capitals (OBSOLÈTE, ÇEVRİMDIŞI,
    CRÍTICO) and the deepest marks (¡, Ș)."""
    assert {word: nos._mark_growth(word) for word in _english_box_words(english)
            if nos._mark_growth(word) != (0, 0)} == {}
    live = frame_layout.drawn(lambda: SKIN.render_floating(english["normal3/auto"], THEME, VARIANT, 1.0, None))
    pill_top = min(rect[1] for rect in live.strokes)
    assert (pill_top, max(rect[3] for rect in live.strokes)) == (nos.HEADER_TOP * 2, (nos.HEADER_TOP + 12) * 2)
    plain = i18n.load_catalog("en").english()
    for word in ("OBSOLÈTE", "ÇEVRİMDIŞI", "CRÍTICO", "¡ȘQ"):
        catalog = i18n.Catalog("en", {}, {**plain, "widget.nostromo.state.critical": word,
                                          "widget.nostromo.cap.tag": word + " {percent}"})
        monkeypatch.setattr(i18n, "app_catalog", lambda language, catalog=catalog: catalog)
        for state in ("critical", "guard_hold_session"):
            model = dataclasses.replace(dump.sample_model(state), status_text=word)
            for scale in budgets.SCALES:
                for draw in (lambda: SKIN.render_floating(model, THEME, VARIANT, scale, None),
                             lambda: SKIN.render_tooltip(model, THEME, VARIANT, scale)):
                    frame = frame_layout.drawn(draw)
                    assert [found for found in frame_layout.check(frame) if found[0] in MARK_CHECKS] == [], (
                        word, state, scale)
                    texts = {run.text for run in frame.runs}
                    assert word in texts and (state == "critical" or any(text.startswith(word + " ")
                                                                         for text in texts)), (word, state, scale)
    up, down = nos._mark_growth("OBSOLÈTE")
    assert (up, down) == (nos.MARK_GROWTH_UP, 0) and nos._mark_growth("OBSOLÈTE", filled=True) == (
        nos.CHIP_GROWTH_UP, 0)


@pytest.mark.parametrize("scale", budgets.SCALES)
def test_the_grown_boxes_keep_off_the_hazard_band_and_the_rules(scale: float) -> None:
    """At every scale, in the buffer pixels the canvas draws: the grown chip keeps air under the hazard band and the
    grown pill, chip and cap tags keep air over the rule and round the rows' bars, rails and ticks. The grown pill's top
    meets the band's bottom edge and never overlaps it: the conflict MARK_GROWTH_UP's comment records."""
    for height in (nos.FLOAT_BASE_H, nos.FLOAT_BASE_H + 2 * nos.ROW_PITCH, 134, 190):
        cv = nos._canvas(nos.FLOAT_W, height, scale, THEME)

        def rows(y0: float, y1: float) -> tuple[int, int]:
            return cv._rect_px(0, y0, 1, y1)[1::2]

        band = rows(0, nos.HAZARD_H)
        rule = rows(nos.RULE_Y, nos.RULE_Y + 1)
        pill = rows(nos.HEADER_TOP - nos.MARK_GROWTH_UP, nos.HEADER_TOP + nos.HEADER_H + nos.MARK_GROWTH_DOWN)
        chip = rows(nos.HEADER_TOP - nos.CHIP_GROWTH_UP, nos.HEADER_TOP + nos.HEADER_H + nos.MARK_GROWTH_DOWN)
        assert pill[0] == band[1] and chip[0] > band[1] and pill[1] < rule[0] and chip[1] < rule[0], (height, scale)
        for pitch, above in ((nos.ROW_PITCH, nos.PIP_OFFSET + 8),
                             (nos.TOOLTIP_ROW_PITCH, nos.TOOLTIP_INFO_OFFSET + 12)):
            for index in range(3):
                top = nos.ROW_TOP + pitch * index
                tag = rows(top - nos.MARK_GROWTH_UP, top + nos.HEADER_H + nos.MARK_GROWTH_DOWN)
                over = rule[1] if index == 0 else rows(0, top - pitch + above)[1]
                tick = rows(top + 15, top + 20)
                assert over < tag[0] and tag[1] < tick[0], (height, scale, pitch, index)
