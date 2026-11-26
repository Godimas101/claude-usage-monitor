"""Gates' fits (#96): what gives way where a language's words are longer than English's, read from the pure helpers the
skin draws by (_header_plan, _info_plan, _footer_plan, _strip_tag, _strip_tag_raise). English never meets a step it did
not take before #96 (the header's age), so its frames stay as they were; the owner defaults (Q1, the strip tag's raise;
Q2, the header's order; Q3, the sign-in footers' order) are each a few named constants or one tuple in gates.py, and
each order is followed whatever it is."""
from __future__ import annotations

import dataclasses
import itertools
import math
import string
import unicodedata
from functools import lru_cache
from typing import Any

import pytest

import budgets
import frame_layout
import skin_words
from usage_monitor import casing, i18n
from usage_monitor.render import dump
from usage_monitor.render.skins import get_skin
from usage_monitor.render.skins import gates

THEME = dump.load_theme_objects()["gates"]
SKIN = get_skin("gates")
VARIANT = THEME.variants[0]
CUT = "…"


def _ink(scale: float = 1.0, height: float = gates.CARD_H) -> gates.Ink:
    return gates.Ink(gates.CARD_W, height, scale, gates._fonts(THEME))


def _catalog(monkeypatch: pytest.MonkeyPatch, **words: str) -> None:
    """The skin's words for these keys (widget.gates.<key with . for __>), English for the rest."""
    english = i18n.load_catalog("en").english()
    catalog = i18n.Catalog("en", {}, {**english, **{f"widget.gates.{key.replace('__', '.')}": text
                                                   for key, text in words.items()}})
    monkeypatch.setattr(i18n, "app_catalog", lambda language: catalog)


@pytest.fixture(scope="module")
def english() -> dict[str, Any]:
    """Every state Gates' English record covers (tests/skin_words.py), as English view models."""
    return skin_words.models(skin_words._gates_states)


# ── The header (Q2) ───────────────────────────────────────────────────────────

GUARDS = {"none": "normal3", "armed": "guard_armed", "holding": "guard_hold_session"}


def _model(word: str, guard: str = "none", age: int | None = 41 * 60) -> Any:
    return dataclasses.replace(dump.sample_model(GUARDS[guard]), status_text=word, age_seconds=age)


def _room(ink: gates.Ink, model: Any, more: bool) -> float:
    """The pill's text room: from TITLE_GAP after the title (and the guard's mark with its gap) to the pill's right edge,
    less the pill's lamp and padding."""
    title = ink.advance(gates.TITLE, ink.face(ink.fonts.body, gates.SIZE_TITLE, 600))
    mark = gates._guard_mark_width(model)
    mark = mark + gates.GUARD_PILL_GAP if mark else 0.0
    chrome = gates.PILL_PAD_LEFT + gates.LAMP_SIZE + gates.PILL_GAP + gates.PILL_PAD_RIGHT
    return (gates.PILL_RIGHT if more else gates.RIGHT) - chrome - (gates.TITLE_X + title + gates.TITLE_GAP + mark)


def _width(ink: gates.Ink, plan: gates.HeaderPlan, word: str | None = None) -> float:
    text = plan.text if word is None else (f"{word} · {plan.age}" if plan.age else word)
    return ink.advance(text, ink.face(ink.fonts.body, plan.size, 600))


def test_the_header_gives_way_in_order() -> None:
    """Q2: the age is left out first (as before #96), then the word shrinks to 9.5 and 9 px (PILL_SIZES), then it is
    cut with "…" at the floor, one letter at least; each step only while the pill is still over, and it then fits. On
    both surfaces, with the guard's capsule beside it."""
    for more in (True, False):
        ink = _ink()
        room = _room(ink, _model("W", "holding"), more)
        sizes = [(size, ink.advance("W", ink.face(ink.fonts.body, size, 600))) for size in gates.PILL_SIZES]
        whole = max(n for n in range(1, 60) if n * sizes[0][1] <= room)          # W's at 10 px that fit
        shrunk = next(n for n in range(whole + 1, 60) if n * sizes[0][1] > room and n * sizes[-1][1] <= room)
        cut = next(n for n in range(60) if n * sizes[-1][1] > room)
        plan = gates._header_plan(ink, _model("W" * whole, "holding"), more=more)
        assert (plan.gave_way, plan.word, plan.size, plan.age) == (("age",), "W" * whole, gates.SIZE_PILL, None), more
        plan = gates._header_plan(ink, _model("W" * whole, "holding", None), more=more)
        assert (plan.gave_way, plan.word) == ((), "W" * whole), more
        plan = gates._header_plan(ink, _model("W" * shrunk, "holding"), more=more)
        assert plan.gave_way == ("age", "shrink") and plan.word == "W" * shrunk and plan.size < gates.SIZE_PILL, more
        assert _width(ink, plan) <= room + gates.FIT_SLACK
        plan = gates._header_plan(ink, _model("W" * cut, "holding"), more=more)
        assert plan.gave_way == ("age", "shrink", "word") and plan.size == gates.PILL_SIZES[-1], (more, plan)
        assert plan.word.endswith(CUT) and len(plan.word) > 1 and _width(ink, plan) <= room + gates.FIT_SLACK
        plan = gates._header_plan(ink, _model("W" * 60, "none", None), more=more)
        assert plan.gave_way == ("shrink", "word") and plan.age is None, plan     # no age: its step is skipped


LENGTHS = (1, 6, 10, 13, 16, 19, 24, 40)


@pytest.mark.parametrize("order", list(itertools.permutations(gates.HEADER_GIVE_WAY)))
def test_the_header_plan_follows_any_order_of_its_steps(order: tuple[str, ...], monkeypatch: pytest.MonkeyPatch) -> None:
    """Q2 is HEADER_GIVE_WAY alone: in any order of its steps, on both surfaces, at every scale, with the guard off,
    armed or holding, with an age or none and at every length, _header_plan takes the steps in that order and only
    while the pill is over (a pill with no age skips the age's step); it then fits; a word it cut could not take one
    more letter and still fit; and the size it shrank to is the first of PILL_SIZES that fits when the word was not
    cut yet."""
    monkeypatch.setattr(gates, "HEADER_GIVE_WAY", order)
    for scale in budgets.SCALES:
        ink = _ink(scale)
        for more, guard, age, length in itertools.product((True, False), GUARDS, (41 * 60, None), LENGTHS):
            word = "W" * length
            model = _model(word, guard, age)
            plan = gates._header_plan(ink, model, more=more)
            room = _room(ink, model, more)
            can = [step for step in order if step != "age" or age is not None]
            where = (order, scale, more, guard, age, length, plan)
            assert list(plan.gave_way) == can[:len(plan.gave_way)], where
            assert _width(ink, plan) <= room + gates.FIT_SLACK or plan.word == word[:1] + CUT, where
            assert (plan.age is None) == (age is None or "age" in plan.gave_way), where
            if plan.word != word:
                assert "word" in plan.gave_way and plan.word.endswith(CUT), where
                kept = len(plan.word) - len(CUT)
                more_text = word[:kept + 1] + CUT if kept + 1 < len(word) else word
                assert _width(ink, plan, more_text) > room, where
            if plan.gave_way[-1:] == ("shrink",):       # shrunk last: to the first of PILL_SIZES that fits
                bigger = [size for size in gates.PILL_SIZES if size > plan.size]
                assert all(_width(ink, dataclasses.replace(plan, size=size)) > room for size in bigger), where
            if plan.gave_way:                           # the last step was taken while over
                monkeypatch.setattr(gates, "HEADER_GIVE_WAY", order[:order.index(plan.gave_way[-1])])
                before = gates._header_plan(ink, model, more=more)
                monkeypatch.setattr(gates, "HEADER_GIVE_WAY", order)
                assert _width(ink, before) > room + gates.FIT_SLACK, (where, before)


def test_the_header_takes_no_step_in_english(english: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Every English header, in every recorded state, on both surfaces and at every scale, draws its word whole at 10
    px and leaves its age out only where it did before #96 (RATE LIMITED beside the guard's capsule): its one step is
    the age's. Its tightest pill has room to spare, and a TITLE_GAP that took that room and a px more would make it
    take a step more."""
    tightest = (999.0, "", True, 1.0)
    for name, model in english.items():
        for more in (True, False):
            for scale in budgets.SCALES:
                ink = _ink(scale)
                plan = gates._header_plan(ink, model, more=more)
                assert (plan.word, plan.size) == (model.status_text, gates.SIZE_PILL), (name, more, scale)
                age = gates._age_label(model)
                room = _room(ink, model, more)
                was = age is not None and ink.advance(f"{model.status_text} · {age}", ink.face(
                    ink.fonts.body, gates.SIZE_PILL, 600)) > room
                assert plan.gave_way == (("age",) if was else ()), (name, more, scale, plan)
                tightest = min(tightest, (room - _width(ink, plan), name, more, scale))
    spare, name, more, scale = tightest
    assert spare > 0, tightest
    model = english[name]
    before = gates._header_plan(_ink(scale), model, more=more).gave_way
    monkeypatch.setattr(gates, "TITLE_GAP", gates.TITLE_GAP + spare + 1)
    after = gates._header_plan(_ink(scale), model, more=more).gave_way
    assert len(after) > len(before) and after[:len(before)] == before, (tightest, before, after)


def test_the_header_draws_its_plan() -> None:
    """The header draws the plan's text at the plan's size, its lamp and pill round it, clear of the title and the
    guard's capsule: a pseudo-locale RATE LIMITED beside the capsule is shrunk on the floating widget and keeps its
    place."""
    pseudo = i18n.dev_locale(i18n.PSEUDO)
    model = skin_words.models(skin_words._gates_states, pseudo)["rate_limited3/resume_in_2h14m"]
    plan = gates._header_plan(_ink(), model, more=True)
    assert plan.gave_way[:2] == ("age", "shrink") and plan.age is None
    frame = frame_layout.drawn(lambda: SKIN.render_floating(model, THEME, VARIANT, 1.0, None))
    assert plan.text in [run.text for run in frame.runs] and _found(frame) == []


def _found(frame: frame_layout.Frame, theme: str = "gates") -> list[tuple[str, str, str]]:
    """The frame check's findings in ``frame`` but the known touches (frame_layout.KNOWN_TOUCHES)."""
    return [found for found in frame_layout.check(frame)
            if frame_layout.known_touch(theme, ["", "floating@1", *found]) is None]


# ── The hover line ────────────────────────────────────────────────────────────

def test_the_hover_line_never_gives_way_in_english(english: dict[str, Any]) -> None:
    """Every English hover line keeps its reset and its pace whole, at least INFO_GAP apart."""
    ink = _ink()
    for name, model in english.items():
        for row in model.rows:
            info = gates._info_plan(ink, model, row)
            text = gates._pace_text(row, locale=model.locale)[0]
            assert info.gave_way == () and info.pace == (text if text and not gates._dim(model) else None), (name, row)


@pytest.mark.parametrize("order", list(itertools.permutations(gates.INFO_GIVE_WAY)))
def test_the_hover_line_shortens_its_pace_then_leaves_it_out(order: tuple[str, ...], english: dict[str, Any],
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """The reset stays whole: as it grows, a pace with points (ahead or under) takes its short form, its signed points in
    its own colour ("+100 pts"), then is left out; On pace has none and is left out at once. In any order of
    INFO_GIVE_WAY, each form comes as its step does; a dimmed card draws no pace."""
    monkeypatch.setattr(gates, "INFO_GIVE_WAY", order)
    ink = _ink()
    for state, short, role in (("pace/ahead_100", "+100 pts", "warn"), ("pace/behind_100", "-100 pts", "ok"),
                               ("pace/on", None, "ok")):
        model = english[state]
        row = model.rows[0]
        assert gates._pace_short(row) == short
        want = [(gates._pace_text(row)[0], ())]
        for index, step in enumerate(order):
            form = short if step == "pace" else None
            want.append((form, order[:index + 1]))
            if form is None:
                break
        seen: list[tuple[str | None, tuple[str, ...]]] = []
        for extra in range(0, 80):
            longer = dataclasses.replace(row, resets_text="Wed 12:59 PM" + " X" * extra)
            info = gates._info_plan(ink, model, longer)
            assert info.reset == "Resets Wed 12:59 PM" + " X" * extra and info.role == role
            if info.pace is not None:
                lead = ink.face(ink.fonts.body, gates.SIZE_FOOT, 600)
                body = ink.face(ink.fonts.body, gates.SIZE_FOOT, 400)
                assert ink.advance(info.reset, body) + gates.INFO_GAP + ink.advance(info.pace, lead) <= \
                    gates.TRACK_W + gates.FIT_SLACK
            if not seen or seen[-1] != (info.pace, info.gave_way):
                seen.append((info.pace, info.gave_way))
        assert seen == want, (order, state)
    dimmed = dataclasses.replace(english["pace/ahead_100"], status=gates.ApiStatus.STALE)
    assert gates._info_plan(ink, dimmed, dimmed.rows[0]).pace is None


# ── The sign-in footers (Q3) ──────────────────────────────────────────────────

STEPS = {"stale/auto": ("stale", "stale_step"), "refused": ("refused", "refused_step"),
         "no_credentials/auto": ("no_login", "no_login_step")}


def _footer_runs(monkeypatch: pytest.MonkeyPatch, model: Any, state: str, lead: str | None = None,
                 step: str | None = None, scale: float = 1.0) -> gates.FooterPlan:
    """_footer_plan for ``model`` (the recorded state ``state``) with these words for its lead and its step."""
    keys = STEPS[state]
    words = {}
    if lead is not None:
        words[f"footer__{keys[0]}"] = lead
    if step is not None:
        words[f"footer__{keys[1]}"] = step
    _catalog(monkeypatch, **words)
    return gates._footer_plan(_ink(scale), model)


def _footer_fits(ink: gates.Ink, runs: tuple[tuple[str, int, str], ...]) -> bool:
    return sum(ink.advance(text, ink.face(ink.fonts.body, gates.SIZE_FOOT, weight)) for text, weight, _role in runs) \
        <= gates.TRACK_W + gates.FIT_SLACK


def test_a_sign_in_footer_keeps_its_step_whole(english: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Q3: a sign-in footer whose lead and step do not fit together leaves its lead out and draws the step whole, from
    LEFT, without the space that follows a lead: its ink starts where a lead's would (critic 4); only a step too long
    alone is cut. English's footers are drawn as before #96, whole, and its wide guard footers (not sign-in footers)
    take no step: _footer cuts their rest where it is drawn, as before."""
    for state in STEPS:
        model = english[state]
        plan = gates._footer_plan(_ink(), model)
        assert plan.gave_way == () and list(plan.runs) == gates._footer_runs(model), state
        for scale in budgets.SCALES:
            plan = _footer_runs(monkeypatch, model, state, lead="W" * 30, scale=scale)
            step = gates._footer_runs(model)[1][0]
            assert step.startswith(" ") and plan.gave_way == ("lead",), (state, plan)
            assert plan.runs == ((step.lstrip(), 400, "text_3"),) and _footer_fits(_ink(scale), plan.runs)
            frame = frame_layout.drawn(lambda: SKIN.render_floating(model, THEME, VARIANT, scale, None))
            run = next(run for run in frame.runs if run.text == step.lstrip())
            assert "W" * 30 not in [each.text for each in frame.runs]
            assert run.box[0] == _ink_left(step.lstrip(), scale) < _ink_left(step, scale), (state, scale)
            plan = _footer_runs(monkeypatch, model, state, lead="W" * 30, step="Run it " * 12, scale=scale)
            assert plan.gave_way == ("lead", "step") and len(plan.runs) == 1, (state, plan)
            assert plan.runs[0][0].endswith(CUT) and not plan.runs[0][0].startswith(" ")
            assert _footer_fits(_ink(scale), plan.runs)
    for wide in ("guard_hold/resume_mon_10am", "guard_hold/resume_wed_1259pm"):
        plan = gates._footer_plan(_ink(), english[wide])
        assert plan.gave_way == () and list(plan.runs) == gates._footer_runs(english[wide]), wide


@lru_cache(maxsize=None)
def _ink_left(text: str, scale: float) -> int:
    """Where the ink of ``text``, put at LEFT on the footer's line in the footer's regular face, starts (output px)."""
    def draw() -> None:
        ink = gates._card_canvas(gates.CARD_H, scale, THEME)
        gates._paint_card(ink, gates.Palette.for_variant(THEME, VARIANT), False)
        face = ink.face(ink.fonts.body, gates.SIZE_FOOT, 400)
        ink.put(text, gates.LEFT, ink.baseline(gates.FOOT_TOP, gates.FOOT_LINE, face), face, (255, 255, 255, 255))
        ink.finish()
    return frame_layout.drawn(draw).runs[0].box[0]


@pytest.mark.parametrize("order", list(itertools.permutations(gates.FOOTER_GIVE_WAY)))
def test_the_footer_plan_follows_any_order_of_its_steps(order: tuple[str, ...], english: dict[str, Any],
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """Q3 is FOOTER_GIVE_WAY alone: in either order, at every length of lead and step, _footer_plan takes the steps in
    that order and only while the footer is over, and the footer then fits: the alternative order cuts the step beside
    a whole lead."""
    monkeypatch.setattr(gates, "FOOTER_GIVE_WAY", order)
    ink = _ink()
    model = english["stale/auto"]
    for lead, step in itertools.product((4, 12, 20, 30), (4, 20, 40, 60)):
        plan = _footer_runs(monkeypatch, model, "stale/auto", lead="W" * lead, step="w" * step)
        where = (order, lead, step, plan)
        assert list(plan.gave_way) == list(order[:len(plan.gave_way)]) and _footer_fits(ink, plan.runs), where
        assert ("lead" in plan.gave_way) == (plan.runs[0][1] == 400), where
        if plan.gave_way:
            monkeypatch.setattr(gates, "FOOTER_GIVE_WAY", order[:order.index(plan.gave_way[-1])])
            before = gates._footer_plan(ink, model)
            monkeypatch.setattr(gates, "FOOTER_GIVE_WAY", order)
            assert not _footer_fits(ink, before.runs), (where, before)


def test_a_footer_that_is_not_a_sign_in_keeps_its_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other footers are not STEP_FOOTERS: their lead is the news, so a long rest is cut to the room the lead
    leaves, as English's widest guard footer is (the rate-limited footer here)."""
    assert gates.STEP_FOOTERS == {gates.ApiStatus.STALE, gates.ApiStatus.NO_CREDENTIALS}
    _catalog(monkeypatch, footer__rate_limited_rest="Showing the last reading " * 6)
    model = dump.sample_model("rate_limited")
    plan = gates._footer_plan(_ink(), model)
    assert plan.gave_way == () and list(plan.runs) == gates._footer_runs(model)
    frame = frame_layout.drawn(lambda: SKIN.render_floating(model, THEME, VARIANT, 1.0, None))
    texts = [run.text for run in frame.runs]
    assert "Rate limited." in texts and any(text.startswith(" Showing") and text.endswith(CUT) for text in texts)


# ── The strip's tags ──────────────────────────────────────────────────────────

def _layouts() -> list[tuple[str, int, float, float, float]]:
    """Each strip layout the frame check draws: (name, taskbar px, display scale, draw scale, plate)."""
    found = []
    for name, px, scale in frame_layout.strip_draws("gates"):
        draw_scale, plate = gates._strip_fit(max(gates.STRIP_MIN_HEIGHT, px), scale)
        found.append((name, px, scale, draw_scale, plate))
    return found


def test_a_strip_tag_past_its_room_shrinks_then_is_left_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """A window tag is drawn at 11 px where it fits its 22 px (to the rail, less STRIP_TAG_AIR), smaller (down to 9 px,
    STRIP_TAG_SIZES) where it does not, and left out where even 9 px does not fit: 5H and 7D stay, any two capitals fit
    (WW), three wide letters (WWI) shrink, the pseudo-locale's [5H ı] goes. At every strip layout a drawn tag's ink
    keeps the frame check's MIN_GAP off the rail and the frame is clean."""
    ink = gates.Ink(gates.STRIP_W, gates.STRIP_H, 1.0, gates._fonts(THEME))
    size = {text: (face.size if (face := gates._strip_tag(ink, text)) else None)
            for text in ("5H", "7D", "WW", "WWI", i18n.pseudo_text("5H"))}
    assert size["5H"] == size["7D"] == size["WW"] == gates.SIZE_STRIP_K and size[i18n.pseudo_text("5H")] is None
    assert gates.STRIP_TAG_SIZES[-1] <= size["WWI"] < gates.SIZE_STRIP_K
    assert ink.advance("WWI", ink.face(ink.fonts.body, size["WWI"], 600)) <= gates.STRIP_TAG_ROOM
    assert gates.STRIP_TAG_ROOM == gates.STRIP_RAIL_X0 - gates.STRIP_K_X - gates.STRIP_TAG_AIR == 22
    _catalog(monkeypatch, window__session="WWI", window__weekly=i18n.pseudo_text("7D"))
    model = dump.sample_model("normal3")
    for name, px, scale, _draw_scale, _plate in _layouts():
        frame = frame_layout.drawn(lambda: SKIN.render_taskbar(model, THEME, VARIANT, px, scale, None))
        texts = [run.text for run in frame.runs]
        assert "WWI" in texts and i18n.pseudo_text("7D") not in texts and frame_layout.check(frame) == [], name
        tag = next(run for run in frame.runs if run.text == "WWI")
        assert tag.box[2] <= (gates.STRIP_RAIL_X0 - frame_layout.MIN_GAP) * frame.kx, (name, tag.box)


# ── Marks (Q1) ────────────────────────────────────────────────────────────────

def _letters() -> list[str]:
    """Printable ASCII, the French, Spanish and Turkish letters (test_font_coverage's floor) in both cases by each
    language's rule, the pseudo-locale's letters, the letters with a comma below (Ș ș Ț ț), and the other marks below a
    language may bring: an ogonek (Ą ą Ę ę Į į Ų ų) and a dot below (Ạ ạ Ọ ọ Ṣ ṣ). The letters a tag may draw; a new
    language's draft adds its own here."""
    from test_font_coverage import FLOOR

    found = (set(string.printable.strip()) | set(i18n.pseudo_text(string.ascii_letters)) | {"…"} | set("ȘșȚț")
             | set("ĄąĘęĮįŲų") | set("ẠạỌọṢṣ"))
    for language, text in FLOOR.items():
        found |= set(text) | set(casing.upper(text, language))
    return sorted(ch for ch in found if len(ch) == 1)


# The face's Latin blocks past ASCII (Latin-1 Supplement, Latin Extended-A and -B, Latin Extended Additional): the
# letters DESCENDERS is measured over, with _letters() (#96's review F3: the face's descenders, not only the ones the
# shipped languages draw). A language in another script adds its block here.
LATIN_BLOCKS = ((0x00A0, 0x0100), (0x0100, 0x0180), (0x0180, 0x0250), (0x1E00, 0x1F00))


@lru_cache(maxsize=None)
def _face_letters() -> tuple[str, ...]:
    """_letters() and every letter of LATIN_BLOCKS the strip tag's face maps (its character map)."""
    from usage_monitor.render import glyphs

    cmap = glyphs.cmap(gates._fonts(THEME).body.file_for(600))
    assert cmap, "the body face's character map"
    latin = {chr(code) for low, high in LATIN_BLOCKS for code in range(low, high)
             if code in cmap and unicodedata.category(chr(code)).startswith("L")}
    return tuple(sorted(set(_letters()) | latin))


# Glyphs of the face that leave a plate whose second line box fits it (32 and 33 px, the rows 14 px apart) by 1 px at
# one size, unraised: Q1 raises a tag only where its line box runs past the plate (an owner default). No language the
# app offers or plans writes them; a draft that does brings it to the owner. Asserted exactly, so it only shrinks.
DEEP_ON_A_FULL_LINE: dict[str, str] = {
    "ȴ": "l with curl (Latin Extended-B, a phonetic letter): its curl, at 10 px",
    "Ḁ": "A with ring below (Latin Extended Additional): its ring, at 11 px",
    "ḁ": "a with ring below: its ring, at 11 px",
}


@lru_cache(maxsize=None)
def _strip_ink(draw_scale: float, plate: float) -> gates.Ink:
    return gates.Ink(gates.STRIP_W, plate, draw_scale, gates._fonts(THEME))


@lru_cache(maxsize=None)
def _glyph(ch: str, size: float, draw_scale: float) -> tuple[int, int] | None:
    """(top, bottom) output rows of ``ch``'s ink from its baseline's row, its bottom exclusive, for a tag of ``size``
    px at ``draw_scale`` (Ink._draw_text's placement), at the most any variant inks it."""
    ink = _strip_ink(draw_scale, float(gates.STRIP_MIN_PLATE))
    face = ink.face(ink.fonts.body, size, 600)
    mask, pad, ascent = gates._run(face, ch, gates.GAMMA_LIGHT_MIN, 0.0)
    box = mask.point([255 if value >= frame_layout.INK else 0 for value in range(256)]).getbbox()
    return None if box is None else (box[1] - ascent - pad, box[3] - ascent - pad)


@lru_cache(maxsize=None)
def _baseline(size: float, draw_scale: float, plate: float, row: int, rise: float) -> int:
    """The output row of a tag's baseline in row ``row``, risen ``rise`` px (Ink.baseline)."""
    ink = _strip_ink(draw_scale, plate)
    face = ink.face(ink.fonts.body, size, 600)
    return round(ink.baseline(gates._strip_rows(plate)[row] - rise, gates.STRIP_ROW_LINE, face) * ink.scale)


@lru_cache(maxsize=None)
def _rows(ch: str, size: float, draw_scale: float, plate: float, row: int, rise: float) -> tuple[int, int] | None:
    """(top, bottom) output rows of ``ch``'s ink, its bottom exclusive, as the strip draws a tag of ``size`` px in row
    ``row`` risen ``rise`` px (Ink.baseline and Ink._draw_text's placement), at the most any variant inks it."""
    glyph = _glyph(ch, size, draw_scale)
    if glyph is None:
        return None
    y = _baseline(size, draw_scale, plate, row, rise)
    return y + glyph[0], y + glyph[1]


def _plate_bottom(draw_scale: float, plate: float) -> int:
    return _strip_ink(draw_scale, plate).out_h


def _below_by_rule(ch: str) -> bool:
    return any(unicodedata.combining(part) in gates._BELOW for part in unicodedata.normalize("NFD", ch))


# Taskbars the frame check does not draw whose plates sit at the edges of Q1's rule (#96's review F2): 31 px (37 px at
# 100 %, 74 px at 200 %) and 30.8 to 31.6 px at the other scales, where the second row still rises; 32 and 33 px (38
# and 39 px at 100 %), where it no longer does but the rows are still 14 px apart; and 35 and 36 px.
EDGE_TASKBARS = ((37, 1.0), (38, 1.0), (39, 1.0), (41, 1.0), (42, 1.0), (46, 1.25), (47, 1.25), (56, 1.5), (65, 1.75),
                 (74, 2.0), (75, 2.0))


@lru_cache(maxsize=None)
def _q1_layouts() -> tuple[tuple[str, float, float], ...]:
    """Every strip layout the frame check draws and the edge taskbars': (name, draw scale, plate)."""
    found = {(round(draw_scale, 4), round(plate, 3)): (name, draw_scale, plate)
             for name, _px, _scale, draw_scale, plate in _layouts()}
    for px, scale in EDGE_TASKBARS:
        draw_scale, plate = gates._strip_fit(px, scale)
        found.setdefault((round(draw_scale, 4), round(plate, 3)),
                         (f"strip@{draw_scale:.4g}/{plate:.4g}", draw_scale, plate))
    return tuple(found.values())


def _short_layouts() -> list[tuple[str, float, float]]:
    """The strip layouts whose second row's 14 px line box runs past the plate (a plate under 32 px): (name, draw
    scale, plate)."""
    return [(name, draw_scale, plate) for name, draw_scale, plate in _q1_layouts()
            if gates._strip_rows(plate)[1] + gates.STRIP_ROW_LINE > plate]


def test_the_glyphs_that_raise_a_strip_tag_are_the_ones_that_leave_the_plate() -> None:
    """Q1 decides by characters, held to the face (#96's review F3): of the face's Latin letters (_face_letters), those
    whose ink leaves the plate in the strip's second row at some short layout (every plate under 32 px) and size are
    exactly the rule's (a mark below, combining class 202 or 220) and DESCENDERS (bases with no mark below: ( ) [ ] _
    g j p q y { | } ¡ ¿ µ þ ŋ ǥ ȷ and the others); the first row's never leave, nor do the second row's on a plate of
    32 px or more but DEEP_ON_A_FULL_LINE's. Raised STRIP_TAG_RAISE, every one stays on the plate, and that is the least
    raise that does: a px less leaves one off. Every mark below the shipped languages may bring leaves it."""
    short = _short_layouts()
    assert all(plate < 32 for _name, _scale, plate in short)
    assert {round(plate, 3) for _name, _scale, plate in short} >= {30.0, 31.0, 31.6}
    assert all(layout[2] >= 32 for layout in _q1_layouts() if layout not in short)
    leave: set[str] = set()
    full: set[str] = set()
    low: set[str] = set()
    deep: set[str] = set()
    for ch in _face_letters():
        for name, draw_scale, plate in _q1_layouts():
            bottom = _plate_bottom(draw_scale, plate)
            for size in gates.STRIP_TAG_SIZES:
                for row in (0, 1):
                    rows = _rows(ch, size, draw_scale, plate, row, 0.0)
                    if rows is not None and rows[1] > bottom:
                        assert row == 1, (ch, name)
                        (leave if (name, draw_scale, plate) in short else full).add(ch)
    assert full == set(DEEP_ON_A_FULL_LINE), "".join(sorted(full))
    for ch in _face_letters():
        if not gates._reaches_below(ch):
            continue
        for _name, draw_scale, plate in short:
            for size in gates.STRIP_TAG_SIZES:
                for rise, into in ((gates.STRIP_TAG_RAISE, deep), (gates.STRIP_TAG_RAISE - 1, low)):
                    rows = _rows(ch, size, draw_scale, plate, 1, rise)
                    if rows is not None and rows[1] > _plate_bottom(draw_scale, plate):
                        into.add(ch)
    bases = {unicodedata.normalize("NFD", ch)[0] for ch in leave if not _below_by_rule(ch)}
    assert bases == set(gates.DESCENDERS), "".join(sorted(bases ^ set(gates.DESCENDERS)))
    assert all(_below_by_rule(ch) or unicodedata.normalize("NFD", ch)[0] in gates.DESCENDERS for ch in leave)
    assert {ch for ch in _letters() if _below_by_rule(ch)} <= leave          # every mark below leaves it
    assert set("þŋµǥȷƴɉǉǌȴ") <= set(gates.DESCENDERS)
    assert deep == set() and low, (sorted(deep), sorted(low))


def test_a_strip_tag_rises_round_marks_below_and_never_in_english(english: dict[str, Any]) -> None:
    """A tag rises STRIP_TAG_RAISE where its row's line box runs past the plate (under 32 px) and it reaches below the
    line (a mark below or a descender), and only there: 7Ç, 7g and 7ŋ rise in the second row of the 30 to 31.6 px
    plates, not on the 32 px plates and up, and never in the first row; English's tags (every recorded state's) never
    rise."""
    for _name, _draw_scale, plate in _q1_layouts():
        tops = gates._strip_rows(plate)
        short = tops[1] + gates.STRIP_ROW_LINE > plate
        assert short == (plate < 32), plate
        for text in ("7Ç", "7g", "5Ş", "ŞĞ", "7ŋ"):
            assert gates._strip_tag_raise(text, tops[0], plate) == 0, (text, plate)
            assert gates._strip_tag_raise(text, tops[1], plate) == (gates.STRIP_TAG_RAISE if short else 0), (text, plate)
        for text in ("7D", "5H", "7É", "7Q"):
            assert gates._strip_tag_raise(text, tops[1], plate) == 0, (text, plate)
    tags = {str(i18n.load_catalog("en").text(key)) for key in gates.WINDOW_KEYS}
    assert tags == {"5H", "7D"} and not any(gates._reaches_below(tag) for tag in tags)
    for name, model in english.items():
        assert {str(gates._words(model.locale).text(key)) for key in gates.WINDOW_KEYS} == tags, name


@lru_cache(maxsize=None)
def _reach(ch: str, draw_scale: float, plate: float, row: int, rise: float) -> int | None:
    """The first row's tag character's lowest ink row (exclusive), or the second's highest, over every size."""
    found = [rows for size in gates.STRIP_TAG_SIZES if (rows := _rows(ch, size, draw_scale, plate, row, rise))]
    if not found:
        return None
    return max(rows[1] for rows in found) if row == 0 else min(rows[0] for rows in found)


def _air(first: str, second: str, draw_scale: float, plate: float, rise: float) -> int:
    """Rows of no ink between the first row's tag character's ink and the second's (raised ``rise``), the least over
    every pair of sizes; negative where they share rows."""
    bottom, top = _reach(first, draw_scale, plate, 0, 0.0), _reach(second, draw_scale, plate, 1, rise)
    return 99 if bottom is None or top is None else top - bottom


def _capital_marked_above(ch: str) -> bool:
    parts = unicodedata.normalize("NFD", ch)
    return parts[0].isupper() and any(unicodedata.combining(part) == 230 for part in parts[1:])


def test_a_raised_tag_stays_on_the_plate_and_off_the_tag_above() -> None:
    """Critic 9 of #96's plan, at every strip layout (#96's review F2 and look F1), for the tags the drafts' rule allows
    (capitals or figures) of the letters a language may draw (_letters). Raised, on every short layout (a plate under
    32 px), a second tag keeps its ink on the plate (the test above) and a px of air under every capital or figure the
    first tag may have, where its own highest letter is a capital or figure with no mark above (7Ç under 5Q). The pairs
    that meet are STRIP_TAG_MEETS's, and only those: raised, a capital with a mark above under a first tag that reaches
    the foot of its line (Q, a capital with a mark below), by up to 3 px; and, with no raise, on any layout whose rows
    are 14 px apart (a plate under 36 px: the 34 px plates the frame check draws too), a capital with a mark above under
    a capital with a mark below, by up to 1 px, as before #96. The 36 and 40 px plates keep air."""
    letters = _letters()
    capitals = [ch for ch in letters if unicodedata.normalize("NFD", ch)[0].isupper() or ch.isdigit()]
    plain = [ch for ch in capitals if len(unicodedata.normalize("NFD", ch)) == 1]
    marked = [ch for ch in capitals if _capital_marked_above(ch)]
    meets: dict[str, set[str]] = {"raised": set(), "unraised": set()}
    least = {"raised": 99, "unraised": 99}
    where: dict[str, set[float]] = {"raised": set(), "unraised": set()}
    for _name, draw_scale, plate in _short_layouts():
        for first in capitals:
            for second in plain:
                assert _air(first, second, draw_scale, plate, gates.STRIP_TAG_RAISE) >= 1, (first, second, plate)
            air = min(_air(first, second, draw_scale, plate, gates.STRIP_TAG_RAISE) for second in marked)
            least["raised"] = min(least["raised"], air)
            if air < 1:
                meets["raised"].add(first)
                where["raised"].add(plate)
    for _name, draw_scale, plate in _q1_layouts():
        tops = gates._strip_rows(plate)
        for first in capitals:
            air = min(_air(first, second, draw_scale, plate, 0.0) for second in marked)
            least["unraised"] = min(least["unraised"], air)
            if air < 1:
                assert tops[1] - tops[0] == 14 and plate < 36, (first, plate)
                meets["unraised"].add(first)
                where["unraised"].add(plate)
    reach = {ch for ch in capitals if _below_by_rule(ch)}
    assert meets["raised"] == reach | {"Q"} and meets["unraised"] <= reach and meets["unraised"], meets
    assert least == {"raised": -3, "unraised": -1}, least
    assert 34.0 in where["unraised"] and max(where["raised"]) < 32, where
    assert gates.STRIP_TAG_MEETS == (
        "on a plate under 32 px, a raised second tag that has a capital with a mark above (É, as well as the mark below "
        "or descender that raises it) meets the first tag's ink at the same place where that tag reaches the foot of its "
        "line (Q, or a capital with a mark below), by up to 3 px",
        "on a plate under 36 px, where the rows are 14 px apart (the 34 px plate too), a second tag with a capital with a "
        "mark above meets a first tag with a capital with a mark below at the same place by up to 1 px with no raise, as "
        "before #96",
    )
    assert math.isclose(gates.STRIP_TAG_RAISE, 2.0)
