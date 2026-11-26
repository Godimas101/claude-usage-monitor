"""No frame overlaps (#94 to #96; tests/frame_layout.py): every run of text a checked skin draws keeps off every other
run and every box, rule, bar and edge of its frame, in English, the pseudo-locale and the French, Spanish and Turkish
dev locales (Night City and Gates also in fr-CA's and es-CO's formats), at every display scale and every scale a taskbar
draws the strip at. The skins whose frames are checked are frame_layout.THEMES (Nostromo, Night City, Gates); Night
City's jobs are per look (frame_layout.NIGHT_CITY_JOBS), Gates' on its first variant (frame_layout.GATES_JOBS). No
window opens: the frames are drawn in memory, in a fresh Python with the time zone pinned, one per job, all started
together (frame_layout.jobs)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import Any

import pytest

from PIL import Image, ImageChops

import budgets
import frame_layout
import skin_words
from usage_monitor.render import dump, engine

CASES = [job[1:] + ("",) * (4 - len(job)) for job in frame_layout.jobs() if job[0] == "findings"]
VARIANTS_IN = "pseudo"          # the language whose words give way the most


@pytest.fixture(scope="module")
def found() -> dict[tuple[str, str, str], Any]:
    """Each case's findings, and each theme's runs per variant, each worked out by frame_layout.py in a fresh Python
    with the time zone pinned; the processes run side by side."""
    jobs = frame_layout.jobs()
    env = {**os.environ, "TZ": frame_layout.PINNED_TZ,
           "PYTHONPATH": os.pathsep.join((str(frame_layout.REPO), str(frame_layout.HERE)))}
    running = {job: subprocess.Popen([sys.executable, str(frame_layout.HERE / "frame_layout.py"), *job],
                                     cwd=frame_layout.REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     encoding="utf-8", creationflags=frame_layout.NO_WINDOW)
               for job in jobs}
    results = {}
    for job, process in running.items():
        out, err = process.communicate(timeout=1800)
        assert process.returncode == 0, (job, err[-3000:])
        results[job] = json.loads(out)
    return results


@pytest.mark.parametrize(("theme", "language", "variant"), CASES)
def test_no_frame_overlaps(found: dict[tuple[str, ...], Any], theme: str, language: str, variant: str) -> None:
    """Every state tests/skin_words.py records for the theme, on the floating widget and the hover card at every
    display scale and on the strip at every taskbar scale, in the job's variant (the first when none), and Gates' strip
    tags in a test catalog's words (frame_layout.GATES_WORDED): no finding but the known ones (frame_layout.KNOWN,
    KNOWN_TOUCHES)."""
    findings = found[("findings", theme, language, variant) if variant else ("findings", theme, language)]
    left = [finding for finding in findings if frame_layout.known(theme, finding) is None
            and frame_layout.known_touch(theme, finding) is None]
    assert left == [], json.dumps(left[:20], ensure_ascii=False)


def test_the_known_overlaps_are_englishs_and_on_the_budgets_record(found: dict[tuple[str, str, str], Any]) -> None:
    """Each known overlap is drawn in English, by design: the budgets list it as English's own overrun, and the check
    still finds it, so the list only shrinks."""
    for entry, box in frame_layout.KNOWN.items():
        theme = entry[0]
        assert any(frame_layout.known(theme, finding) == entry for finding in found["findings", theme, "en"]), entry
        assert any(box_id == box for box_id, _text in budgets.KNOWN_ENGLISH_OVERRUNS), entry


def test_the_known_touches_are_found_and_never_in_english(found: dict[tuple[str, ...], Any]) -> None:
    """Each known touch (frame_layout.KNOWN_TOUCHES: what a language's words draw so by design, never English) is still
    found, in the pseudo-locale, so the list only shrinks; and no English frame of its theme has a touch at all."""
    for entry in frame_layout.KNOWN_TOUCHES:
        theme = entry[0]
        assert any(frame_layout.known_touch(theme, finding) == entry for finding in found["findings", theme, "pseudo"]), \
            entry
        assert not any(finding[2].startswith("run touches") for finding in found["findings", theme, "en"]), entry


@pytest.mark.parametrize("theme", frame_layout.THEMES)
def test_every_variant_draws_the_first_variants_runs(found: dict[tuple[str, ...], Any], theme: str) -> None:
    """Variants that share a layout (frame_layout.layout_of) differ in colour only, so the check draws one of each: in
    every recorded state, every variant's runs at 1.0 (floating widget, hover card, strip) are the first variant's of
    its layout, text, place and ink alike. Nostromo's variants share one; Night City's are its looks (Blackwall boxed,
    the Edgerun plate, Relic and Mox plain), and with transparent_bg (floating widget and strip) each draws the runs it
    draws without it, but Edgerun, which drops its plate there for a layout of its own (segmented bars, no box). This
    runs in the pseudo-locale alone, so each layout it leaves apart is drawn by a findings job in every language and
    tag the check draws (the others are held to those by this result). Gates' variants share one layout, and
    transparent_bg moves none of its runs either; its runs' ink is recorded at the most any variant draws
    (test_gates_ink_is_recorded_at_the_most_any_variant_draws), so its first variant's jobs hold every variant."""
    runs = found["variants", theme, VARIANTS_IN]
    first: dict[str, Any] = {}
    members: dict[str, set[str]] = {}
    differ = []
    for key, drawn in runs.items():
        group, variant = key.rsplit("|", 1)
        members.setdefault(group.split("|", 1)[1], set()).add(variant)
        if first.setdefault(group, drawn) != drawn:
            differ.append(key)
    assert len(first) > 50 and len(runs) > len(first) and differ == []
    if theme == "gates":
        theme_object = dump.load_theme_objects()[theme]
        assert members == {"all": {variant.id for variant in theme_object.variants},
                           "all/clear": {variant.id for variant in theme_object.variants}}
        for key, drawn in runs.items():             # transparent_bg moves no run
            state, layout, variant = key.split("|")
            if layout == "all/clear":
                opaque = runs[f"{state}|all|{variant}"]
                assert drawn == {surface: opaque[surface] for surface in drawn}, key
        assert set(frame_layout.GATES_JOBS) == {*frame_layout.LANGUAGES, *frame_layout.TAGS}
        return
    if theme != "night_city":
        assert set(members) == {"all"}
        return
    assert members == {"boxed": {"blackwall"}, "plate": {"edgerun"}, "plain": {"relic", "mox"},
                       "boxed/clear": {"blackwall"}, "plain/clear": {"relic", "mox"}, "cells/clear": {"edgerun"}}
    for key, drawn in runs.items():                 # transparent_bg moves no run of a look it keeps
        state, layout, variant = key.split("|")
        if layout in ("boxed/clear", "plain/clear"):
            opaque = runs[f"{state}|{layout.split('/')[0]}|{variant}"]
            assert drawn == {surface: opaque[surface] for surface in drawn}, key
    theme_object = dump.load_theme_objects()[theme]
    drawn_in = {language for language, _variant in frame_layout.NIGHT_CITY_JOBS}
    assert drawn_in == {*frame_layout.LANGUAGES, *frame_layout.TAGS}
    for each in drawn_in:
        looks = {frame_layout.layout_of(theme, theme_object.variant(variant.split("/")[0]), variant.endswith("/clear"))
                 + ("/clear" if variant.endswith("/clear") else "")
                 for language, variant in frame_layout.NIGHT_CITY_JOBS if language == each}
        assert looks == {"boxed", "plate", "plain", "cells/clear"}, each


def _value_parts(text: str) -> list[str]:
    """A joined value run ("13%", "%13") as the two runs Night City draws it with, its numerals and its sign."""
    found = re.fullmatch(r"(%?)([0-9.,]+)(%?)", text)
    if found is None or not (found[1] or found[3]):
        return [text]
    return [found[2], "%"]


def test_the_check_sees_night_citys_runs() -> None:
    """Night City draws through its own ink queue (night_city._Ink), whose text layers are new ones, so the recorder
    hooks it: every run tests/skin_words.py sees the skin draw (skin_words.runs, through engine.Canvas.text_layer, in
    this process, so the clocks are this time zone's) is a run of the frame (a value's numerals and its sign joined
    into one), each state's floating widget, hover card and strip at 1.0, Blackwall's, the state the record has not
    among them (frame_layout.night_city_states); and the frame records the chips' outlines and fills, the hazard strips,
    the bars and the body."""
    from usage_monitor.render.skins import get_skin

    theme = dump.load_theme_objects()["night_city"]
    skin, variant = get_skin("night_city"), theme.variants[0]
    models = frame_layout.models("en", "night_city")
    seen = {"stroke_masks": 0, "fill_masks": 0, "hazard": 0, "cells": 0}
    for name in ("normal3/auto", "warning/auto", "critical/auto", "stale3/auto", "paused/auto", "guard_hold_session/auto",
                 "guard_armed/auto", "band_hold/stale3", "guard_armed_paused", "band_hold/no_credentials"):
        model = models[name]
        seen_runs = skin_words.runs(skin, model, theme, variant, (48,), (1.0,))
        for surface, draw in (("floating@1", lambda: skin.render_floating(model, theme, variant, 1.0, None)),
                              ("tooltip@1", lambda: skin.render_tooltip(model, theme, variant, 1.0)),
                              ("strip/h48@1", lambda: skin.render_taskbar(model, theme, variant, 48, 1.0, None))):
            frame = frame_layout.drawn(draw)
            record = seen_runs[surface]
            while isinstance(record, str):                       # a surface that drew what an earlier one did
                record = seen_runs[record]
            record = [run[0] for run in record]
            texts = [part for run in frame.runs                   # the strip's percent is one run already
                     for part in ([run.text] if surface.startswith("strip") else _value_parts(run.text))]
            assert sorted(texts) == sorted(record), (name, surface, texts, record)
            assert frame.body is not None, (name, surface)
            for kind in seen:
                seen[kind] += len(getattr(frame, kind))
    assert all(seen.values()), seen


def test_the_check_sees_gates_runs() -> None:
    """Gates queues its text and draws it on the finished frame through its glyph cache, so the recorder hooks
    Ink._draw_text: every run tests/skin_words.py sees the skin draw (skin_words.gates_runs, in this process, so the
    clocks are this time zone's) is a run of the frame, each state's floating widget, hover card and strip at 1.0 and
    on a 30 px plate, the record's wide resumes among them; the frame records the fills, the rules, the bars and the
    body; and the test catalog's strip tags (frame_layout.GATES_WORDED) are drawn, shrunk to fit and raised round their
    mark below."""
    from usage_monitor.render.skins import gates, get_skin

    theme = dump.load_theme_objects()["gates"]
    skin, variant = get_skin("gates"), theme.variants[0]
    models = frame_layout.models("en", "gates")
    seen = {"fills": 0, "rules": 0, "cells": 0}
    for name in ("normal3/auto", "warning/auto", "critical/auto", "stale3/auto", "paused/auto", "guard_hold_session/auto",
                 "guard_armed/auto", "rate_limited3/auto", "refused", "guard_hold/resume_mon_10am",
                 "guard_hold/no_credentials", "scoped_model/guard_holding"):
        model = models[name]
        seen_runs = skin_words.gates_runs(skin, model, theme, variant, (48, 30), (1.0,))
        for surface, draw in (("floating@1", lambda: skin.render_floating(model, theme, variant, 1.0, None)),
                              ("tooltip@1", lambda: skin.render_tooltip(model, theme, variant, 1.0)),
                              ("strip/h48@1", lambda: skin.render_taskbar(model, theme, variant, 48, 1.0, None)),
                              ("strip/h30@1", lambda: skin.render_taskbar(model, theme, variant, 30, 1.0, None))):
            frame = frame_layout.drawn(draw)
            record = seen_runs[surface]
            while isinstance(record, str):                       # a surface that drew what an earlier one did
                record = seen_runs[record]
            assert [run.text for run in frame.runs] == [run[0] for run in record], (name, surface)
            assert frame.body is not None and frame.bar_touch == frame_layout.TOUCH, (name, surface)
            for kind in seen:
                seen[kind] += len(getattr(frame, kind))
    assert all(seen.values()), seen
    ink = gates.Ink(gates.STRIP_W, gates.STRIP_H, 1.0, gates._fonts(theme))
    states = list(frame_layout.gates_worded("en"))
    assert sorted(name for name, _model, _worded in states) == ["strip_tags/mark_below", "strip_tags/shrunk"]
    for name, model, worded in states:
        for height in (48, 36):                     # plates of 40 and 30 px, drawn at 1.0
            plain = frame_layout.drawn(lambda: skin.render_taskbar(model, theme, variant, height, 1.0, None))
            with frame_layout.worded(worded):
                frame = frame_layout.drawn(lambda: skin.render_taskbar(model, theme, variant, height, 1.0, None))
            for key, text in worded.items():
                was = next(run for run in plain.runs if run.text == ("5H" if key.endswith(".session") else "7D"))
                now = next(run for run in frame.runs if run.text == text)
                if name == "strip_tags/shrunk":         # drawn smaller, in its room
                    assert ink.advance(text, ink.face(ink.fonts.body, gates.SIZE_STRIP_K, 600)) > gates.STRIP_TAG_ROOM
                    assert now.box[2] <= gates.STRIP_RAIL_X0 - gates.STRIP_TAG_AIR + 1, (name, now.box)
                else:                                   # 2 px higher than 7D on the short plate, in place on the tall
                    assert was.y - now.y == (gates.STRIP_TAG_RAISE if height == 36 else 0), (name, height)


# ── The check itself ──────────────────────────────────────────────────────────

def _frame(draw: Any, scale: float = 1.0) -> frame_layout.Frame:
    """A 100 x 40 chamfered frame drawn by ``draw(cv)``, its body inside a 1 px edge, as Nostromo paints one."""
    def make() -> None:
        cv = engine.Canvas(100, 40, scale)
        cv.faces = None
        body = engine.inset_chamfer(0, 0, 100, 40, 8, 8, 1)
        cv.gradient_fill(body, (0, 0, 0, 255), (0, 0, 0, 255))
        draw(cv)
        cv.finish()
    return frame_layout.drawn(make)


def _text(cv: engine.Canvas, text: str, x: float, y: float, size: float = 10.0) -> float:
    font = cv.font("ibmplexmono/IBMPlexMono-SemiBold.ttf", size)
    return cv.text(text, x, y, font, (255, 255, 255, 255))


@pytest.mark.parametrize("scale", budgets.SCALES)
def test_the_check_finds_each_kind_of_overlap(scale: float) -> None:
    """Each check fails its own case and passes the case beside it, at every display scale."""
    def kinds(draw: Any) -> set[str]:
        return {what for what, _text, _other in frame_layout.check(_frame(draw, scale))}

    assert kinds(lambda cv: (_text(cv, "AB", 20, 20), _text(cv, "CD", 20 + 15, 20))) == set()
    assert kinds(lambda cv: (_text(cv, "AB", 20, 20), _text(cv, "CD", 20 + 8, 20))) == {"run on run"}
    assert kinds(lambda cv: (_text(cv, "AB", 20, 20), _text(cv, "CD", 20 + 11.5, 20))) == {"runs too close"}
    assert kinds(lambda cv: (_text(cv, "AB", 20, 20), _text(cv, "CD", 20, 32))) == set()      # two lines
    assert kinds(lambda cv: (cv.outline_rect(16, 9, 40, 23, (9, 9, 9, 255)), _text(cv, "AB", 20, 20))) == set()
    assert kinds(lambda cv: (cv.outline_rect(16, 13, 40, 23, (9, 9, 9, 255)), _text(cv, "AB", 20, 20))) == {
        "run on an outline"}
    assert kinds(lambda cv: (cv.fill_rect(16, 9, 40, 23, (9, 9, 9, 255)), _text(cv, "AB", 20, 20))) == set()
    assert kinds(lambda cv: (cv.fill_rect(16, 9, 28, 23, (9, 9, 9, 255)), _text(cv, "AB", 20, 20))) == {
        "run across a fill"}
    assert kinds(lambda cv: (cv.fill_rect(16, 21, 60, 22, (9, 9, 9, 255)), _text(cv, "AB", 20, 20))) == set()
    assert kinds(lambda cv: (cv.fill_rect(16, 16, 60, 17, (9, 9, 9, 255)), _text(cv, "AB", 20, 20))) == {
        "run on a rule"}
    assert kinds(lambda cv: (cv.dotted_hline(16, 60, 16, 3, 3, (9, 9, 9, 255)), _text(cv, "AB", 20, 20))) == {
        "run on a rule"}
    assert kinds(lambda cv: (cv.segment_layers(40, 12, 6, 4, 8, 5, 2), _text(cv, "AB", 20, 20))) == set()
    assert kinds(lambda cv: (cv.segment_layers(28, 12, 6, 4, 8, 5, 2), _text(cv, "AB", 20, 20))) == {"run on a bar"}
    band = engine.chamfer_polygon(0, 0, 100, 40, 8, 8)
    assert kinds(lambda cv: (cv.hazard(band, 0, 100, 0, 4, 8, (9, 9, 9, 255), (0, 0, 0, 255)),
                             _text(cv, "AB", 20, 20))) == set()
    assert kinds(lambda cv: (cv.hazard(band, 0, 100, 0, 14, 8, (9, 9, 9, 255), (0, 0, 0, 255)),
                             _text(cv, "AB", 20, 20))) == {"run on the hazard band"}
    assert kinds(lambda cv: _text(cv, "AB", 84, 20)) == set()
    assert kinds(lambda cv: _text(cv, "ABC", 84, 20)) == {"run leaves the body"}          # past the right edge
    assert kinds(lambda cv: _text(cv, "AB", 20, 41)) == {"run leaves the body"}           # below the bottom edge
    assert kinds(lambda cv: _text(cv, "AB", 2, 10)) == {"run leaves the body"}            # in the chamfered corner
    assert kinds(lambda cv: _text(cv, "AB", 10, 20)) == set()


def test_a_known_overlap_is_matched_by_its_surface_and_run() -> None:
    """A finding is a KNOWN entry by its surface, its kind, its run and the place it names: the 5H value at 100 % on
    the 5H bar's cells, never the 7D value on its own bar."""
    entry = next(iter(frame_layout.KNOWN))
    on_5h, on_7d = "(98.0, 13.0, 102.0, 21.0)", "(220.0, 13.0, 224.0, 21.0)"
    assert frame_layout.known("nostromo", ["pace/ahead_100", "strip@1.176", "run on a bar", "100%", on_5h]) == entry
    assert frame_layout.known("nostromo", ["x", "strip@1", "run on a bar", "%100", on_5h]) == entry
    assert frame_layout.known("nostromo", ["x", "strip@1", "run on a bar", "100%", on_7d]) is None
    assert frame_layout.known("nostromo", ["x", "strip@1", "run on a bar", "%100", on_7d]) is None
    assert frame_layout.known("nostromo", ["x", "strip@1", "run on a bar", "100%", "(...)"]) is None
    assert frame_layout.known("nostromo", ["x", "strip@1", "run on a bar", "99%", on_5h]) is None
    assert frame_layout.known("nostromo", ["x", "floating@1", "run on a bar", "100%", on_5h]) is None
    assert frame_layout.known("nostromo", ["x", "strip@1", "run on run", "100%", on_5h]) is None


def test_ink_touching_an_outline_is_found() -> None:
    """Q1 keeps a mark's ink off the 1 px outline round it: ink in the buffer row (or column) next to an outline's
    stroke, with no air between, is "run touches an outline"; one buffer px of air passes; ink on the stroke is "run
    on an outline" alone."""
    def found(stroke: tuple[int, int, int, int]) -> list[str]:
        frame = frame_layout.Frame(100, 60, 2.0, 2.0)
        ink = Image.new("L", (10, 6), 255)
        frame.runs.append(frame_layout.Run("AB", ink, 20, 20, (20, 20, 30, 26)))
        frame.strokes.append(stroke)
        return [what for what, _text, _other in frame_layout.check(frame)]

    assert found((16, 26, 34, 28)) == ["run touches an outline"]          # the row under the ink
    assert found((16, 12, 34, 20)) == ["run touches an outline"]          # the row over it
    assert found((30, 18, 32, 30)) == ["run touches an outline"]          # the column after it
    assert found((16, 27, 34, 29)) == []                                  # one row of air
    assert found((16, 25, 34, 27)) == ["run on an outline"]


def test_a_shapes_outline_and_fill_are_checked_as_a_boxs() -> None:
    """Night City's chips are polygons: their ring (stroke_masks) and their fill (fill_masks) are checked as an
    outline's strokes and a fill are: ink on a ring is "run on an outline", ink within TOUCH of it "run touches an
    outline", one row of air passes; ink partly in a fill and partly out is "run across a fill", ink wholly inside
    passes."""
    def found(stroke: Image.Image | None = None, fill: Image.Image | None = None, at: tuple[int, int] = (10, 10),
              ink_at: tuple[int, int] = (20, 20)) -> list[str]:
        frame = frame_layout.Frame(100, 60, 2.0, 2.0)
        frame.runs.append(frame_layout.Run("AB", Image.new("L", (10, 6), 255), *ink_at,
                                           (ink_at[0], ink_at[1], ink_at[0] + 10, ink_at[1] + 6)))
        if stroke is not None:
            frame.stroke_masks.append((stroke, *at))
        if fill is not None:
            frame.fill_masks.append((fill, *at))
        return [what for what, _text, _other in frame_layout.check(frame)]

    def ring(width: int, height: int) -> Image.Image:
        mask = Image.new("L", (width, height), 255)
        mask.paste(0, (2, 2, width - 2, height - 2))
        return mask

    assert found(ring(30, 20)) == []                                       # the ink inside the ring, 6 rows of air
    assert found(ring(30, 13)) == ["run on an outline"]                    # the ring's bottom row across the ink
    assert found(ring(30, 18)) == ["run touches an outline"]               # the ink right over the ring's bottom
    assert found(ring(30, 19)) == []                                       # one row of air
    assert found(fill=Image.new("L", (30, 20), 255)) == []                 # wholly inside the fill
    assert found(fill=Image.new("L", (30, 14), 255)) == ["run across a fill"]
    assert found(fill=Image.new("L", (30, 20), 255), at=(50, 10)) == []    # a fill elsewhere


@pytest.mark.parametrize("scale", budgets.SCALES)
def test_the_check_finds_each_kind_of_overlap_in_a_gates_frame(scale: float) -> None:
    """Gates' frames are recorded through its own hooks, in output px (frame_layout._gates_spies): on a Gates card
    each check fails its own case and passes the case beside it, at every display scale: a fill round the run, a run
    across a fill, on a rule, on a bar, touching a bar with no air (Frame.bar_touch) and one px off it, run on run, and
    a run past the card's content box."""
    from usage_monitor.render.skins import gates

    theme = dump.load_theme_objects()["gates"]
    pal = gates.Palette.for_variant(theme, theme.variants[0])
    white = (255, 255, 255, 255)

    def frame(draw: Any) -> frame_layout.Frame:
        def make() -> None:
            ink = gates._card_canvas(60, scale, theme)
            gates._paint_card(ink, pal, False)
            draw(ink, ink.face(ink.fonts.body, 11, 600))
            ink.finish()
        return frame_layout.drawn(make)

    def kinds(draw: Any) -> set[str]:
        return {what for what, _text, _other in frame_layout.check(frame(draw))}

    def ab(ink: Any, face: Any, x: float = 40.0) -> None:
        ink.put("AB", x, 30.0, face, white)

    probe = frame(ab)
    [run] = probe.runs
    left, top, right, bottom = (run.box[0] / probe.kx, run.box[1] / probe.ky, run.box[2] / probe.kx,
                                run.box[3] / probe.ky)
    middle = (top + bottom) / 2
    assert kinds(ab) == set()
    assert kinds(lambda ink, face: (ink.rrect(left - 4, top - 4, right + 4, bottom + 4, 2, white), ab(ink, face))) \
        == set()
    assert kinds(lambda ink, face: (ink.rrect(left - 4, top - 4, (left + right) / 2, bottom + 4, 2, white),
                                    ab(ink, face))) == {"run across a fill"}
    assert kinds(lambda ink, face: (ink.rect(left - 4, middle, right + 4, middle + 1, white), ab(ink, face))) == {
        "run on a rule"}
    assert kinds(lambda ink, face: (ink.rrect(left - 4, middle - 2, right + 4, middle + 2, 2, white), ab(ink, face))) \
        == {"run on a bar"}
    assert kinds(lambda ink, face: (ink.rrect(left - 4, bottom, right + 4, bottom + gates.BAR_H, 2, white),
                                    ab(ink, face))) == {"run touches a bar"}
    off = bottom + 1 / probe.ky                                         # one output px of air
    assert kinds(lambda ink, face: (ink.rrect(left - 4, off, right + 4, off + gates.BAR_H, 2, white), ab(ink, face))) \
        == set()
    assert kinds(lambda ink, face: (ab(ink, face), ab(ink, face, 41.0))) == {"run on run"}
    assert kinds(lambda ink, face: ab(ink, face, gates.LEFT - 4.0)) == {"run leaves the body"}


def test_gates_ink_is_recorded_at_the_most_any_variant_draws() -> None:
    """The containment rule the Gates jobs stand on (#96's critic 7): a run's ink is coverage at INK and up after the
    text colour's gamma (gates._text_gamma), which is GAMMA_LIGHT_MIN or more for every colour of every variant, and a
    higher gamma only takes ink away; so the recorder, which takes each run's mask at GAMMA_LIGHT_MIN, holds every
    variant's ink. Each light and dark variant's frame records the same ink, and it holds the ink each draws."""
    from dataclasses import fields

    from usage_monitor.render.skins import gates, get_skin

    theme = dump.load_theme_objects()["gates"]
    for variant in theme.variants:
        pal = gates.Palette.for_variant(theme, variant)
        for item in fields(pal):
            color = getattr(pal, item.name)
            if isinstance(color, tuple):
                assert gates._text_gamma(color) >= gates.GAMMA_LIGHT_MIN, (variant.id, item.name)
    for low, high in ((gates.GAMMA_LIGHT_MIN, 0.6), (0.6, gates.GAMMA_DARK_INK), (gates.GAMMA_LIGHT_MIN, 1.0)):
        assert all(a >= b for a, b in zip(gates._coverage_table(low), gates._coverage_table(high))), (low, high)
    skin, model = get_skin("gates"), dump.sample_model("guard_hold_session")
    seen = {}
    for variant_id in ("system-dark", "system-light", "green-dark", "rose-light"):
        variant = theme.variant(variant_id)
        frame = frame_layout.drawn(lambda: skin.render_floating(model, theme, variant, 1.25, None))
        pal = gates.Palette.for_variant(theme, variant)
        seen[variant_id] = [(run.text, run.x, run.y, run.ink.tobytes()) for run in frame.runs]
        for run in frame.runs:
            if run.text == gates.TITLE:                  # its own ink, at its colour's gamma, lies in the recorded
                face = gates.Ink(gates.CARD_W, 10, 1.25, gates._fonts(theme)).face(
                    gates._fonts(theme).body, gates.SIZE_TITLE, 600)
                own = gates._run(face, run.text, gates._text_gamma(pal.text_bright), 0.0)[0].point(
                    [255 if v >= frame_layout.INK else 0 for v in range(256)])
                assert own.size == run.ink.size and ImageChops.subtract(own, run.ink).getbbox() is None, variant_id
    assert len({tuple(found) for found in map(tuple, seen.values())}) == 1, sorted(seen)
