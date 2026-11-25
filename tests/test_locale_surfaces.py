"""The app's locale on the widgets, the tray and the alerts (#91).

The locale rides on the view model (WidgetModel.locale, English by default), so every surface draws in the one the app
gives build_model, and the frame dump and the golden digests take it as an argument; the alerts read the app's locale
on every evaluation. English stays byte for byte what it was: tests/test_formats.py, the golden frames and the pins hold
that. Here: the other languages reach each surface, and only its numbers, percents and times change.
"""
from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from usage_monitor import i18n
from usage_monitor.data.alerts import AlertEngine
from usage_monitor.render import dump
from usage_monitor.render.model import build_model
from usage_monitor.settings import Settings
from usage_monitor.ui.widgets import steady

EN = i18n.ENGLISH


def loc(tag: str, clock: int = 24) -> i18n.Locale:
    """The locale a Windows with this regional format and clock gives a dev instance showing its language."""
    return i18n.Locale(setting="system", language=i18n.primary(tag), system="en", format=tag, clock=clock,
                       offered=("en",))


FR_CA = loc("fr-CA")


@pytest.fixture
def settings(tmp_path: Any) -> Any:
    store = Settings(tmp_path / "settings.json", save_delay_s=60)
    yield store
    store.close()


# ── The view model carries the locale (step 1.4) ──────────────────────────────

# The view model's texts, which follow the locale; every other field is data, the same in every language.
MODEL_TEXTS = ("age_text",)
ROW_TEXTS = ("resets_text", "share_text")
GUARD_TEXTS = ("resume_text", "resume_in", "resume_clock")


def _data(model: Any) -> dict[str, Any]:
    """The model's fields without its texts and its locale."""
    rows = [{f.name: getattr(row, f.name) for f in dataclasses.fields(row) if f.name not in ROW_TEXTS}
            for row in model.rows]
    guard = None if model.guard is None else {f.name: getattr(model.guard, f.name)
                                              for f in dataclasses.fields(model.guard) if f.name not in GUARD_TEXTS}
    rest = {f.name: getattr(model, f.name) for f in dataclasses.fields(model)
            if f.name not in (*MODEL_TEXTS, "rows", "guard", "locale")}
    return {"rows": rows, "guard": guard, **rest}


@pytest.mark.parametrize("name", dump.DUMP_STATE_NAMES)
def test_a_locale_changes_no_data_in_the_model(name):
    """fr-CA gives the same rows, keys, percents, severities, guard and status as English: only texts follow it."""
    english = build_model(dump.sample_state(name), dump.SAMPLE_NOW)
    french = build_model(dump.sample_state(name), dump.SAMPLE_NOW, locale=FR_CA)
    assert english.locale == EN and french.locale == FR_CA
    assert _data(french) == _data(english)


def test_the_model_is_english_unless_given_a_locale():
    assert build_model(None, dump.SAMPLE_NOW).locale is EN
    assert dump.sample_model("normal3").locale is EN


def test_a_locale_change_draws_the_frames_again():
    """The frame caches key on steady(model): two locales must not share a cached frame."""
    english = dump.sample_model("normal3")
    assert steady(dump.sample_model("normal3", locale=FR_CA)) != steady(english)
    assert steady(dump.sample_model("normal3", locale=EN)) == steady(english)


def test_the_frame_dump_and_the_digests_draw_in_the_locale_they_are_given(monkeypatch, tmp_path):
    import frame_digests

    real, seen = dump.sample_model, []

    def spy(name: str, *args: Any, **kwargs: Any) -> Any:
        model = real(name, *args, **kwargs)
        seen.append(model.locale)
        return model

    monkeypatch.setattr(dump, "sample_model", spy)
    monkeypatch.setattr(dump, "_save", lambda image, path, report: None)
    dump.dump_frames(tmp_path, themes=["gates"], variants=["system-light"], states=["normal3"], scales=(1.0,),
                     locale=FR_CA)
    assert seen and set(seen) == {FR_CA}                       # the frames and the contact sheet
    seen.clear()
    frame_digests.digests(part=0, parts=64, locale=FR_CA)
    assert seen and set(seen) == {FR_CA}
    seen.clear()
    frame_digests.digests(part=0, parts=64)
    assert seen and set(seen) == {EN}


# ── The alerts read the app's locale (step 1.4) ───────────────────────────────

def test_the_alerts_are_english_without_a_locale_provider(settings):
    assert AlertEngine(settings)._locale() is EN


def test_the_alerts_read_the_locale_each_time(settings):
    given = [FR_CA]
    engine = AlertEngine(settings, locale_provider=lambda: given[0])
    assert engine._locale() == FR_CA
    given[0] = loc("tr-TR", 12)
    assert engine._locale() == loc("tr-TR", 12)


def test_a_failing_locale_provider_leaves_the_alerts_english(settings):
    def broken() -> i18n.Locale:
        raise RuntimeError("no locale")

    assert AlertEngine(settings, locale_provider=broken)._locale() is EN
