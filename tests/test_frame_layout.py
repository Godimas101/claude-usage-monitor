"""No frame overlaps (#94; tests/frame_layout.py): every run of text a checked skin draws keeps off every other run and
every box, rule, bar and edge of its frame, in English, the pseudo-locale and the French, Spanish and Turkish dev
locales, at every display scale and every scale a taskbar draws the strip at. The skins whose frames are checked
are frame_layout.THEMES (#95 and #96 add theirs). No window opens: the frames are drawn in memory, in a fresh Python
with the time zone pinned, one per language, all started together."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

import pytest

import budgets
import frame_layout
from usage_monitor.render import engine

CASES = [(theme, language) for theme in frame_layout.THEMES for language in frame_layout.LANGUAGES]
VARIANTS_IN = "pseudo"          # the language whose words give way the most


@pytest.fixture(scope="module")
def found() -> dict[tuple[str, str, str], Any]:
    """Each case's findings, and each theme's runs per variant, each worked out by frame_layout.py in a fresh Python
    with the time zone pinned; the processes run side by side."""
    jobs = [("findings", theme, language) for theme, language in CASES]
    jobs += [("variants", theme, VARIANTS_IN) for theme in frame_layout.THEMES]
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


@pytest.mark.parametrize(("theme", "language"), CASES)
def test_no_frame_overlaps(found: dict[tuple[str, str, str], Any], theme: str, language: str) -> None:
    """Every state tests/skin_words.py records, on the floating widget and the hover card at every display scale and on
    the strip at every taskbar scale, first variant: no finding but the known ones (frame_layout.KNOWN)."""
    findings = found["findings", theme, language]
    left = [finding for finding in findings if frame_layout.known(theme, finding) is None]
    assert left == [], json.dumps(left[:20], ensure_ascii=False)


def test_the_known_overlaps_are_englishs_and_on_the_budgets_record(found: dict[tuple[str, str, str], Any]) -> None:
    """Each known overlap is drawn in English, by design: the budgets list it as English's own overrun, and the check
    still finds it, so the list only shrinks."""
    for entry, box in frame_layout.KNOWN.items():
        theme = entry[0]
        assert any(frame_layout.known(theme, finding) == entry for finding in found["findings", theme, "en"]), entry
        assert any(box_id == box for box_id, _text in budgets.KNOWN_ENGLISH_OVERRUNS), entry


@pytest.mark.parametrize("theme", frame_layout.THEMES)
def test_every_variant_draws_the_first_variants_runs(found: dict[tuple[str, str, str], Any], theme: str) -> None:
    """The variants differ in colour only, so the check draws the first: in every recorded state, every variant's runs
    at 1.0 (floating widget, hover card, strip) are the first variant's, text, place and ink alike."""
    runs = found["variants", theme, VARIANTS_IN]
    first: dict[str, Any] = {}
    differ = []
    for key, drawn in runs.items():
        state, _variant = key.rsplit("|", 1)
        if first.setdefault(state, drawn) != drawn:
            differ.append(key)
    assert len(first) > 50 and len(runs) > len(first) and differ == []


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
    entry = next(iter(frame_layout.KNOWN))
    assert frame_layout.known("nostromo", ["pace/ahead_100", "strip@1.176", "run on a bar", "100%", "(...)"]) == entry
    assert frame_layout.known("nostromo", ["x", "strip@1", "run on a bar", "%100", "(...)"]) == entry
    assert frame_layout.known("nostromo", ["x", "strip@1", "run on a bar", "99%", "(...)"]) is None
    assert frame_layout.known("nostromo", ["x", "floating@1", "run on a bar", "100%", "(...)"]) is None
    assert frame_layout.known("nostromo", ["x", "strip@1", "run on run", "100%", "(...)"]) is None
