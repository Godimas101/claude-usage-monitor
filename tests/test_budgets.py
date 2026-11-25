"""The width budgets (#89; tests/budgets.py). Each box measures its text as its skin draws it, at every scale the skin
draws it at, and every run the frame dump draws stands in a box; every room is pinned, so a change to a skin that moves
one shows here; English runs past its room only where it already did; no offered language's text runs past a box that
has no fallback or only shrinks; and every other language, the pseudo-locale among them, gets a report."""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest

import budgets
from usage_monitor import formats, i18n
from usage_monitor.render import dump
from usage_monitor.render.skins import night_city as nc
from usage_monitor.render.skins import nostromo as nos
from usage_monitor.ui import tray

REPO = Path(__file__).resolve().parent.parent
PINNED_TZ = "UTC0"                  # the frame dump's clocks, as tests/frame_digests.py pins them
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Every box's and line's limit in its unit, as budgets.py works it out from its skin's constants and the English beside
# it. After a change to a skin that moves a room, `python tests/budgets.py --limits` prints this table again.
LIMITS: dict[str, float] = {
    "nostromo.floating.status_pill": 103.2, "nostromo.floating.age": 43.2, "nostromo.floating.state_word": 111.2,
    "nostromo.floating.state_chip": 103.2, "nostromo.floating.armed_guard": 50.4,
    "nostromo.floating.armed_guard_short": 21.6, "nostromo.floating.row_name": 202.0,
    "nostromo.floating.row_qualifier": 102.4, "nostromo.floating.row_value": 223.6, "nostromo.floating.cap_tag": 112.4,
    "nostromo.floating.message": 242, "nostromo.tooltip.row_name": 199.0, "nostromo.tooltip.row_qualifier": 99.4,
    "nostromo.tooltip.row_value": 229.6, "nostromo.tooltip.cap_tag": 109.4, "nostromo.tooltip.resets": 144.0,
    "nostromo.tooltip.pace": 126.6, "nostromo.tooltip.guard_lead": 252, "nostromo.tooltip.guard_lines": 252,
    "nostromo.strip.window_tag": 22.0, "nostromo.strip.value_5h": 24.0, "nostromo.strip.value_5h_chip": 20.0,
    "nostromo.strip.value_7d": 32.0, "nostromo.strip.value_7d_chip": 28.0, "nostromo.tray.readout": 20.0,
    "night_city.floating.brand": 132.236, "night_city.floating.live_tag": 110.417,
    "night_city.floating.status_chip": 106.417, "night_city.floating.guard_chip": 39.056,
    "night_city.floating.hold_chip": 101.417, "night_city.floating.band_text": 154.556,
    "night_city.floating.band_text_no_age": 220, "night_city.floating.band_age": 62.359,
    "night_city.floating.band_hold_text": 168.921, "night_city.floating.band_hold_chip": 175.866,
    "night_city.floating.row_name": 137.372, "night_city.floating.row_chip": 153.065,
    "night_city.floating.row_tag": 91.192, "night_city.floating.row_reset": 128.331,
    "night_city.floating.row_value": 143.921, "night_city.tooltip.row_name": 129.271,
    "night_city.tooltip.row_chip": 144.965, "night_city.tooltip.row_tag": 83.092,
    "night_city.tooltip.row_value": 143.921, "night_city.tooltip.info_reset": 129.245,
    "night_city.tooltip.info_pace": 117.433, "night_city.tooltip.guard_lead": 236,
    "night_city.tooltip.guard_lines": 236, "night_city.strip.window_label": 18, "night_city.strip.percent": 21,
    "night_city.strip.status_live": 40, "night_city.strip.status_word": 45, "night_city.strip.hold_chip": 36,
    "gates.floating.title": 84.54, "gates.floating.status_word": 76.184, "gates.floating.status_pill": 76.184,
    "gates.tooltip.title": 105.54, "gates.tooltip.status_word": 97.184, "gates.tooltip.status_pill": 97.184,
    "gates.floating.row_label": 212.84, "gates.floating.row_tag": 139.496, "gates.floating.row_value": 232.82,
    "gates.floating.cap_badge": 55.036, "gates.tooltip.row_label": 204.05, "gates.tooltip.row_tag": 130.706,
    "gates.tooltip.row_value": 232.82, "gates.tooltip.cap_badge": 46.246, "gates.tooltip.resets": 130.565,
    "gates.tooltip.pace": 153.962, "gates.tooltip.guard_lead": 220, "gates.tooltip.guard_text": 220,
    "gates.floating.footer_lead": 258, "gates.floating.footer_rest": 258, "gates.strip.window_tag": 24,
    "gates.strip.value": 38, "gates.tray.number": 17.013, "dashboard.nostromo.cost_tile": 6,
    "dashboard.night_city.cost_tile": 6, "dashboard.gates.cost_tile": 6, "dashboard.nostromo.cost_hero": 6,
    "dashboard.night_city.cost_hero": 6, "dashboard.gates.cost_hero": 6, "app.tray.tooltip": 127,
    "app.toast.alert_threshold.title": 63, "app.toast.alert_resets.body": 255, "app.toast.alert_reset.title": 63,
    "app.toast.alert_reset_ended.body": 255, "app.toast.alert_credits.title": 63, "app.toast.alert_credits.body": 255,
    "app.toast.update.title": 63, "app.toast.update.body": 255, "app.toast.update_note.title": 63,
    "app.toast.update_note.body": 250, "nostromo.floating.header_line": 240.0, "nostromo.floating.row_name_line": 202.0,
    "nostromo.tooltip.row_name_line": 199.0, "nostromo.tooltip.info_line": 252, "night_city.floating.header_line": 223,
    "night_city.floating.band_line": 220, "night_city.floating.band_hold_line": 220,
    "night_city.tooltip.info_line": 236, "gates.floating.header_line": 216, "gates.tooltip.header_line": 237,
    "gates.floating.row_line": 258, "gates.floating.footer": 258, "gates.tooltip.info_line": 258,
}

# Where a box's runs are drawn. The hover cards draw the floating widget's header, band, message and footer with the
# floating widget's own code and rooms, so a floating box stands on both.
STANDS_ON: dict[str, tuple[str, ...]] = {"floating": ("floating", "tooltip"), "tooltip": ("tooltip",),
                                          "strip": ("strip",), "tray": ("tray",)}

# The box the frame dump never draws: [G], since every sample state leaves room for [GUARD].
NOT_DRAWN_WHOLE = frozenset({"nostromo.floating.armed_guard_short"})

# What #91's number formats have to settle before French or Spanish is offered (#107): fr-CA's and es-MX's dollars do
# not fit the money figures, and Turkish money has no figure yet (budgets.FIGURES says why). The percents are settled:
# the boxes sized for English's figures (the strips' values, Gates' cap badge) draw them without the space (Box.tight,
# Chris's decision on #91), and Turkish's "%100" fits.
TIGHT_BOXES = frozenset({"nostromo.strip.value_5h", "nostromo.strip.value_5h_chip", "nostromo.strip.value_7d",
                         "nostromo.strip.value_7d_chip", "night_city.strip.percent", "gates.floating.cap_badge",
                         "gates.tooltip.cap_badge"})
_MONEY = tuple(f"dashboard.{skin}.cost_{part}" for part in ("tile", "hero")
               for skin in ("nostromo", "night_city", "gates"))
FOR_91: dict[str, set[tuple[str, str]]] = {
    "fr": {(box, "5,96 $ US") for box in _MONEY},
    "es": {(box, text) for box in _MONEY for text in ("USD 5.96", "1,234.5")},
    "tr": set(),
}


# ── As the skins draw ─────────────────────────────────────────────────────────────────────────────────────────────

_DRAW = ("import json, budgets; "
         "runs = set(budgets.drawn((1.0,))) | set(budgets.drawn(budgets.SCALES[1:], every_variant=False)); "
         "print(json.dumps(sorted(runs)))")


@pytest.fixture(scope="module")
def drawn() -> list[tuple[str, str, str, float, float]]:
    """budgets.drawn() in a fresh Python with the time zone pinned, as the frame dump's other tests run it: every
    theme and variant at 1.0, and each theme's first variant at the other display scales (variants differ in colour)."""
    done = subprocess.run([sys.executable, "-c", _DRAW], cwd=REPO,
                          env={**os.environ, "TZ": PINNED_TZ,
                               "PYTHONPATH": os.pathsep.join((str(REPO), str(REPO / "tests")))},
                          capture_output=True, encoding="utf-8", timeout=900, check=False, creationflags=NO_WINDOW)
    assert done.returncode == 0, done.stderr[-3000:]
    return [tuple(row) for row in json.loads(done.stdout)]


def _one_scale(box: budgets.Box) -> bool:
    """Night City's and Gates' ink lays out true advances, so their widths are the same at every scale."""
    return box.measure.how != "nostromo"


def _drawn_widths(seen: dict[tuple[str, str, str], dict[float, set[float]]], box: budgets.Box, text: str,
                  scale: float) -> set[float]:
    """The widths the skin worked out for ``text`` where ``box`` stands: at ``scale``, or at any scale for a box whose
    width is the same at every scale. Night City draws its value's figure and its percent sign as two runs."""
    skin, surface = box.id.split(".")[:2]

    def at(run: str) -> set[float]:
        found: set[float] = set()
        for where in STANDS_ON[surface]:
            for drawn_at, widths in seen.get((skin, where, run), {}).items():
                if _one_scale(box) or round(drawn_at, 6) == round(scale, 6):
                    found |= widths
        return found

    if box.measure.how != "night_city_value" or "%" not in text:
        return at(text)
    return {figure + nc.UNIT_GAP + sign for figure in at(text.replace("%", "")) for sign in at("%")}


def test_each_box_measures_its_text_as_its_skin_draws_it(drawn: list[tuple[str, str, str, float, float]]) -> None:
    """The frame dump draws every surface at every display scale, and Nostromo's strip also at the smaller scales a
    short taskbar draws it at. Each English text of a box that it draws where the box stands is measured to a width the
    skin worked out as it drew it there, within 0.01 px, and a box measured at several scales is compared at every one
    of them; so the box has the skin's font, size, tracking, canvas and shrinking. (A Night City or Gates text has its
    1.0 width at every scale.) The run check below holds every width the dump draws to a box."""
    seen: dict[tuple[str, str, str], dict[float, set[float]]] = defaultdict(lambda: defaultdict(set))
    for theme, surface, text, scale, width in drawn:
        seen[theme, surface, text][scale].add(width)
    wrong, measured = [], defaultdict(set)
    boxes = [box for box in budgets.BOXES if box.unit != "chars"]
    for box in boxes:
        for text in box.english:
            for scale in box.scales:
                widths = _drawn_widths(seen, box, text, scale)
                if not widths:
                    continue
                measured[box.id].add(scale)
                want = box.measure.width(text, scale)
                if not any(abs(want - width) <= 0.01 for width in widths):
                    wrong.append((box.id, text, scale, round(want, 3), sorted(widths)))
    assert wrong == []
    assert {box.id for box in boxes if not measured[box.id]} == NOT_DRAWN_WHOLE
    assert [(box.id, sorted(set(box.scales) - measured[box.id])) for box in boxes
            if box.id not in NOT_DRAWN_WHOLE and set(box.scales) - measured[box.id]] == []


def _shape(text: str) -> str:
    """A run with its digits made zeros: a box lists one value of each shape, not every value."""
    return re.sub(r"\d", "0", text)


def _run_width(box: budgets.Box, run: str, scale: float) -> float:
    """``run`` measured as ``box`` measures it. Night City draws its value's percent sign as a run of its own, UNIT_GAP
    after the figure, so the sign alone is measured without the gap."""
    if box.measure.how == "night_city_value" and run == "%":
        return box.measure.width(run, scale) - nc.UNIT_GAP
    return box.measure.width(run, scale)


def test_every_run_the_skins_draw_stands_in_a_box(drawn: list[tuple[str, str, str, float, float]]) -> None:
    """Each run the frame dump draws, at each scale it draws it, is one that a box standing on that surface lists, its
    digits aside, or a part of Night City's value (the figure and the sign are two runs); and one of those boxes
    measures the run, digits and all, to the width the skin drew it at, within that box's budget. So a run drawn in
    another face or size than its box's, a value wider than its box's budget, and a wrapped line wider than its room all
    fail. Gates' icons are glyphs, not text."""
    claims: dict[tuple[str, str, str], list[budgets.Box]] = defaultdict(list)
    for box in budgets.BOXES:
        if box.unit == "chars":
            continue
        skin, surface = box.id.split(".")[:2]
        shapes = {_shape(text) for text in box.english}
        if box.measure.how == "night_city_value":
            shapes |= {_shape(text.replace("%", "")) for text in box.english} | {"%"}
        for where in STANDS_ON[surface]:
            for shape in shapes:
                claims[skin, where, shape].append(box)
    stray, unmeasured = set(), []
    for theme, surface, text, scale, width in drawn:
        if all(0xE000 <= ord(ch) <= 0xF8FF for ch in text):
            continue
        boxes = claims.get((theme, surface, _shape(text)), [])
        if not boxes:
            stray.add((theme, surface, text))
        elif not any(abs(_run_width(box, text, scale) - width) <= 0.01 and width <= budgets.budget(box, scale) + 0.01
                     for box in boxes):
            unmeasured.append((theme, surface, text, scale, round(width, 3), sorted(box.id for box in boxes)))
    assert sorted(stray) == []
    assert unmeasured == []


def test_the_strip_is_measured_at_every_scale_a_taskbar_draws_it_at(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nostromo draws its strip smaller on a taskbar shorter than it (render_taskbar). Called as the frame dump calls
    it, at each display scale Windows offers up to 200 % (SCALES: the frame dump's four and 1.75) and each taskbar
    height the frame dump draws, the skin picks a scale in STRIP_SCALES, every one of them is picked, and the strip's
    boxes are measured at them. (The drawn fixture runs the frame dump itself at those scales.)"""
    used: set[float] = set()
    real = nos._render_strip

    def spy(model: Any, pal: Any, scale: float, *args: Any, **kwargs: Any) -> Any:
        used.add(round(scale, 6))
        return real(model, pal, scale, *args, **kwargs)

    monkeypatch.setattr(nos, "_render_strip", spy)
    theme = dump.load_theme_objects()["nostromo"]
    skin, model = dump.get_skin("nostromo"), dump.sample_model(dump.DUMP_STATE_NAMES[0])
    for scale in budgets.SCALES:
        for height in dump.TASKBAR_HEIGHTS_AT_1X:
            skin.render_taskbar(model, theme, theme.variants[0], round(height * scale), scale, None)
    assert used == {round(scale, 6) for scale in budgets.STRIP_SCALES}
    assert {box.scales for box in budgets.BOXES if box.id.startswith("nostromo.strip.")} == {budgets.STRIP_SCALES}


def test_every_room_is_pinned() -> None:
    """Each box's and line's limit is the one LIMITS holds: a change to a skin that moves a room fails here until the
    table is printed again (python tests/budgets.py --limits), so the change shows in review."""
    assert {item.id: item.limit for item in (*budgets.BOXES, *budgets.LINES)} == pytest.approx(LIMITS, abs=0.001)


def test_the_limits_print_as_this_file_pins_them(capsys: pytest.CaptureFixture[str]) -> None:
    assert budgets.main(["--limits"]) == 0
    assert ast.literal_eval("{" + capsys.readouterr().out + "}") == LIMITS


# ── The budgets ───────────────────────────────────────────────────────────────────────────────────────────────────

def test_english_runs_past_its_room_only_where_it_already_did() -> None:
    """A new English overrun (a longer word, a wider font, a narrower room) fails here; one that goes away is struck
    from KNOWN_ENGLISH_OVERRUNS, so the list only shrinks."""
    assert budgets.english_overruns() == budgets.KNOWN_ENGLISH_OVERRUNS


def test_no_offered_language_is_past_a_budget() -> None:
    """English, with its own longest figures set in, fits everywhere it did; no offered language's text runs past a
    box or line that has no fallback or only shrinks (#107 offers a language once this holds for it)."""
    assert budgets.over(i18n.DEFAULT) == []
    for language in i18n.OFFERED:
        assert [(r.box.id, r.text) for r in budgets.over(language) if r.box.enforced] == [], language


def test_what_91_has_to_settle_is_all_that_runs_past() -> None:
    """French's and Spanish's longest figures run past only where FOR_91 says; #91 strikes each as it settles it."""
    for language, expected in FOR_91.items():
        assert {(r.box.id, r.text) for r in budgets.over(language)} == expected, language


def test_the_boxes_sized_for_englishs_figures_take_their_percents_tight() -> None:
    """Chris's decision on #91: French and Spanish drop the space before "%" only in the boxes sized for English's
    figures, and the sign keeps its side. budgets.tight writes each language's percent figure as formats.percent does
    with tight=True for the language's base format, so the budgets measure what the skins draw (test_locale_surfaces.py
    holds the skins to it); every other box takes the figure with its space."""
    assert {box.id for box in budgets.BOXES if box.tight} == TIGHT_BOXES
    for language, figures in budgets.FIGURES["percent"].items():
        tag = i18n.BASE_FORMATS[language]
        locale = i18n.Locale(setting="system", language=language, system="en", format=tag, clock=24, offered=("en",))
        assert [budgets.tight(figure) for figure in figures] == [formats.percent("100", locale, tight=True)], language
    assert budgets.texts(budgets.BOX["gates.tooltip.cap_badge"], "fr") == ["Cap 100%"]
    assert budgets.texts(budgets.BOX["nostromo.strip.value_5h"], "es") == ["100%"]
    assert budgets.texts(budgets.BOX["night_city.strip.percent"], "tr") == ["%100"]
    assert budgets.texts(budgets.BOX["gates.floating.row_value"], "fr") == ["100\u00a0%"]


def test_a_box_that_shrinks_its_text_is_measured_at_the_size_it_shrinks_to(monkeypatch: pytest.MonkeyPatch) -> None:
    """Night City's strip status shrinks a long word to fit, down to 9 px (_fit_type): "CONNEXION" is too wide at the
    status size but fits shrunk; a word too wide even at 9 px is still a fault."""
    box = budgets.BOX["night_city.strip.status_word"]
    assert box.fit == "shrink" and box.enforced
    assert budgets.W_nc("CONNEXION", "STATUS") > box.limit >= box.width("CONNEXION")
    monkeypatch.setattr(budgets, "texts", lambda _box, _language: ["CONNEXION", "DÉCONNEXION RÉSEAU"])
    assert [(r.box.id, r.text) for r in budgets.over("xx", [box])] == [(box.id, "DÉCONNEXION RÉSEAU")]


def test_a_character_limit_stays_the_budget_where_english_runs_past() -> None:
    """A pixel box where English already runs past its room budgets what English takes there (the strip's 100%). A
    character box's limit is a contract or a hard cut (the money figures' six characters, the tray's 127 units), so it
    stays the budget: es-MX's "USD 5.96" fails a tile, and only the English in KNOWN_ENGLISH_OVERRUNS passes one."""
    strip = budgets.BOX["nostromo.strip.value_5h"]
    assert strip.width("100%") > strip.limit
    for scale in strip.scales:
        assert budgets.budget(strip, scale) == pytest.approx(max(strip.limit, strip.width("100%", scale)))
    tile = budgets.BOX["dashboard.nostromo.cost_tile"]
    assert (tile.unit, tile.limit, budgets.budget(tile)) == ("chars", 6, 6)
    assert (tile.id, "$100.00") in budgets.KNOWN_ENGLISH_OVERRUNS
    assert (tile.id, "USD 5.96") in {(r.box.id, r.text) for r in budgets.over("es")}
    tip = budgets.BOX["app.tray.tooltip"]
    assert (tip.unit, tip.limit, budgets.budget(tip)) == ("chars", tray.TIP_LIMIT, tray.TIP_LIMIT)


def test_characters_are_counted_in_utf16_units_as_the_tray_clips_them() -> None:
    chars = budgets.Measure("chars")
    assert [chars.width(text) for text in ("Kullanım", "e\u0301", "😀")] == [8, 2, 2]
    fits, past = "😀" * 63 + "x", "😀" * 64                     # 127 and 128 units
    assert (chars.width(fits), tray.clip(fits, tray.TIP_LIMIT)) == (127, fits)
    assert chars.width(past) == 128 and tray.clip(past, tray.TIP_LIMIT) != past


def _grown(box: budgets.Box, text: str) -> str:
    """``text`` lengthened as far as ``box`` holds it at every scale."""
    def fits(candidate: str) -> bool:
        return all(box.width(candidate, scale) <= budgets.budget(box, scale) + 0.01 for scale in box.scales)

    assert fits(text)
    while fits(text + "X"):
        text += "X"
    return text


def test_a_line_is_measured_with_each_slots_widest_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """A reset and a pace that each fit their own box can still not fit Nostromo's hover-card line together, so a line
    takes each slot's widest text in the language, as the language would draw them side by side."""
    line = next(item for item in budgets.LINES if item.id == "nostromo.tooltip.info_line")
    resets, pace = budgets.BOX["nostromo.tooltip.resets"], budgets.BOX["nostromo.tooltip.pace"]
    reset_text, pace_text = _grown(resets, "RESETS WED 12:59 PM"), _grown(pace, "BEHIND -100 PTS")
    made = {resets.id: [reset_text, "RESETS --"], pace.id: [pace_text, "ON PACE"]}
    monkeypatch.setattr(budgets, "texts", lambda box, _language: made.get(box.id, []))
    assert budgets.over("xx", [resets, pace]) == []
    [hit] = budgets.over("xx", [line])
    assert hit.text == f"{reset_text} | {pace_text}" and hit.budget == line.limit
    assert hit.width == pytest.approx(resets.width(reset_text, hit.scale) + pace.width(pace_text, hit.scale))


def test_a_wrapping_box_is_measured_by_its_longest_run_without_a_space() -> None:
    """A box that wraps breaks at spaces, so what must fit is each run without one; U+00A0 holds a run together."""
    box = budgets.BOX["gates.tooltip.guard_text"]
    text = "Resumes 10:26 PM (in 2h 14m)."
    assert box.fit == "wrap" and not box.enforced
    assert box.width(text) == pytest.approx(max(box.measure.width(word) for word in text.split(" ")))
    glued = "16\u00a0h\u00a012"
    assert box.width(glued) == pytest.approx(box.measure.width(glued))


# ── What goes in a box ────────────────────────────────────────────────────────────────────────────────────────────

def test_each_languages_longest_figures_are_set_in_the_boxs_text() -> None:
    body = budgets.BOX["app.toast.alert_resets.body"]
    assert budgets.texts(body, "es") == ["Resets 4:12 p.m.", "Resets Wed 4:12 p.m.", "Resets 16:12", "Resets Wed 16:12"]
    assert budgets.texts(body, "fr") == ["Resets 16 h 12", "Resets Wed 16 h 12", "Resets 16:12", "Resets Wed 16:12"]
    assert budgets.texts(budgets.BOX["nostromo.floating.cap_tag"], "tr") == ["CAP %100"]
    assert budgets.texts(budgets.BOX["nostromo.floating.message"], "fr") == []      # no figure, and no keys until #93
    assert budgets.texts(body, i18n.PSEUDO) == [i18n.pseudo_text(text) for text in body.english]


@pytest.mark.parametrize(("kind", "figure"), [("percent", r"%"), ("clock", r"\d:\d\d"), ("money", r"\$|USD")])
def test_a_box_names_the_figures_its_english_holds(kind: str, figure: str) -> None:
    """A box whose English holds a percent, a clock or a dollar names that kind, so each language's longest one is
    measured there, and a box that names one holds it."""
    holds = {box.id for box in budgets.BOXES if any(re.search(figure, text) for text in box.english)}
    assert holds == {box.id for box in budgets.BOXES if kind in box.kinds}


def test_every_template_is_a_shape_of_its_boxs_english() -> None:
    for box in budgets.BOXES:
        for template in box.templates:
            before, *after = template.split("{}")
            assert len(after) == 1, (box.id, template)
            shape = re.compile(re.escape(before) + "(.+)" + re.escape(after[0]))
            assert any(shape.fullmatch(text) for text in box.english), (box.id, template)


def test_no_value_of_a_listed_shape_is_wider_than_its_budget() -> None:
    """A box lists one value of each shape, and the run check above reads digits as zeros. Where a face's digits differ
    in width (Night City's chips, tags and status, Gates' body face), the shape with the face's widest digit in every
    place still fits the box's budget, so no value the skin can draw there is wider than the English it is worked out
    from."""
    wider = []
    for box in budgets.BOXES:
        if box.unit == "chars":
            continue
        digit = max("0123456789", key=box.measure.width)
        for text in box.english:
            variant = re.sub(r"\d", digit, text)
            if any(box.width(variant, scale) > budgets.budget(box, scale) + 0.01 for scale in box.scales):
                wider.append((box.id, text, variant))
    assert wider == []


def test_every_box_and_line_is_well_formed() -> None:
    ids = [item.id for item in (*budgets.BOXES, *budgets.LINES)]
    assert len(ids) == len(set(ids)) and list(budgets.BOX) == [box.id for box in budgets.BOXES]
    for box in budgets.BOXES:
        assert re.fullmatch(r"(nostromo|night_city|gates|dashboard|app)(\.[a-z0-9_]+){2,3}", box.id), box.id
        assert box.fit in budgets._FITS and set(box.kinds) <= set(budgets._KINDS), box.id
        assert box.english and box.why and box.limit > 0, box.id
        assert box.unit == "chars" or box.width("Ag") > 0, box.id
    for line in budgets.LINES:
        assert line.fit in budgets._FITS and line.english and line.why, line.id
        slotted = [box_id for slot in line.slots for box_id in slot]
        assert all(budgets.BOX[box_id].unit == "px" for box_id in slotted), line.id
        assert {box_id for box_id, _ in line.extra} <= set(slotted), line.id
        for runs in line.english:
            assert len(runs) == len(line.slots), (line.id, runs)
            for slot, text in zip(line.slots, runs):
                assert not text or any(text in budgets.BOX[box_id].english for box_id in slot), (line.id, text)


def test_budget_notes_sit_beside_their_keys() -> None:
    """Each box key carries its budget beside it in en.json, so translators see the limit (none has keys until #93)."""
    raw = json.loads((i18n.CATALOG_DIR / "en.json").read_text(encoding="utf-8"))
    notes = {key: value for key, value in raw.items() if key.startswith("_budget.")}
    assert notes == budgets.notes()
    order = list(raw)
    for note in notes:
        key = note[len("_budget."):]
        assert key in raw and abs(order.index(note) - order.index(key)) == 1, note


# ── The report ────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_pseudo_locale_gets_a_report_not_a_failure(capsys: pytest.CaptureFixture[str]) -> None:
    rows = budgets.over(i18n.PSEUDO)
    faults = sum(r.box.enforced for r in rows)
    assert faults and any(isinstance(r.box, budgets.Line) for r in rows)      # what #93 to #101 clear
    assert budgets.main(["--lang", i18n.PSEUDO]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"pseudo: {len(rows)} texts past their budget, {faults} of them in boxes or lines")
    assert "\n  FAULT " in out and "\n  cut   " in out


def test_any_language_can_be_reported_and_only_an_offered_one_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    for language in i18n.DEV_LANGUAGES:
        assert budgets.main(["--lang", language]) == 0, language
    monkeypatch.setattr(i18n, "OFFERED", ("en", "fr", "tr"))
    assert budgets.main(["--lang", "fr"]) == 1                             # what #107 runs before it offers French
    assert budgets.main(["--lang", "tr"]) == 0                             # offered, and it fits
    assert budgets.main(["--lang", "es"]) == 0                             # not offered: a report
