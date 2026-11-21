"""The width budgets (#89; tests/budgets.py): no offered language's text may run past a box that has no fallback, the
pseudo-locale and every other language get a report, and English runs past its room only where it already did."""
from __future__ import annotations

import json

import pytest

import budgets
from usage_monitor import i18n


def test_english_runs_past_its_room_only_where_it_already_did() -> None:
    """A new English overrun (a longer word, a wider font) fails here; one that goes away is struck from the list."""
    assert budgets.english_overruns() == budgets.KNOWN_ENGLISH_OVERRUNS


def test_no_offered_language_is_past_a_budget() -> None:
    for language in i18n.OFFERED:
        assert [(r.box.id, r.text) for r in budgets.over(language) if r.box.enforced] == [], language


def test_every_box_measures_in_a_font_its_skin_has() -> None:
    for box in budgets.BOXES:
        if box.unit == "px" and not box.composite:
            assert budgets.width(box, "Ag") > 0, box.id


def test_the_measures_are_the_skins_own() -> None:
    """The strip's "100%" in Nostromo's VT323 at 18 px, and Night City's status word at the size it shrinks to."""
    strip = next(box for box in budgets.BOXES if box.id == "nostromo.strip.value_5h")
    assert budgets.width(strip, "100%") == pytest.approx(28.0, abs=0.01)
    status = next(box for box in budgets.BOXES if box.id == "night_city.strip.status_word")
    assert status.fit == "shrink:9" and budgets.width(status, "OFFLINE") < budgets.width(
        status.__class__(**{**status.__dict__, "fit": "none"}), "OFFLINE")


def test_a_limit_the_app_enforces_by_cutting_stays_the_limit() -> None:
    tooltip = next(box for box in budgets.BOXES if box.id == "app.tray.tooltip")
    assert (tooltip.unit, tooltip.limit, budgets.budget(tooltip)) == ("chars", 127, 127)


def test_each_languages_longest_figures_are_budgeted() -> None:
    """What #91 has to settle: a percent with U+00A0 does not fit Nostromo's strip, and fr-CA's dollar does not fit the
    money tiles; Turkish's "%100" fits."""
    fr = {(r.box.id, r.text) for r in budgets.over("fr")}
    assert ("nostromo.strip.value_5h", "100 %") in fr and ("nostromo.dashboard.cost_tile", "5,96 $ US") in fr
    assert budgets.over("tr") == []


def test_the_pseudo_locale_gets_a_report_not_a_failure(capsys: pytest.CaptureFixture[str]) -> None:
    rows = budgets.over(i18n.PSEUDO)
    assert rows and any(r.box.enforced for r in rows)                       # the known overflows #93 to #101 clear
    assert budgets.main(["--lang", "pseudo"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("pseudo: ") and "FAULT" in out


def test_any_language_can_be_reported_and_only_an_offered_one_fails(monkeypatch: pytest.MonkeyPatch,
                                                                     capsys: pytest.CaptureFixture[str]) -> None:
    for language in i18n.DEV_LANGUAGES:
        assert budgets.main(["--lang", language]) == 0, language
    monkeypatch.setattr(i18n, "OFFERED", ("en", "fr"))
    assert budgets.main(["--lang", "fr"]) == 1                             # what #107 runs before it offers French


def test_budget_notes_sit_beside_their_keys() -> None:
    """Each box key carries its budget beside it in en.json, so translators see the limit (none has keys until #93)."""
    raw = json.loads((i18n.CATALOG_DIR / "en.json").read_text(encoding="utf-8"))
    notes = {key: value for key, value in raw.items() if key.startswith("_budget.")}
    assert notes == budgets.notes()
    order = list(raw)
    for note in notes:
        key = note[len("_budget."):]
        assert key in raw and abs(order.index(note) - order.index(key)) == 1, note
