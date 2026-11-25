"""The app's locale on the widgets, the tray and the alerts (#91).

The locale rides on the view model (WidgetModel.locale, English by default), so every surface draws in the one the app
gives build_model, and the frame dump and the golden digests take it as an argument; the alerts read the app's locale
on every evaluation. English stays byte for byte what it was: tests/test_formats.py, the golden frames and the pins hold
that. Here: the other languages reach each surface, and only its numbers, percents and times change.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from usage_monitor import formats, i18n
from usage_monitor.data.alerts import AlertEngine
from usage_monitor.data.models import ApiStatus, Limit
from usage_monitor.render import dump
from usage_monitor.render import model as view
from usage_monitor.render.model import build_model
from usage_monitor.settings import Settings
from usage_monitor.ui.widgets import steady

EN = i18n.ENGLISH
PSEUDO = i18n.Locale(setting="system", language=i18n.PSEUDO, system="en", format="en-US", clock=12, offered=("en",))


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


# ── The model's times, ages, countdowns and shares (step 1.5) ──────────────────

WEDNESDAY_NOON = datetime(2026, 7, 15, 12, 0)       # wall times, made in this PC's zone as the model reads them
SATURDAY_1612 = datetime(2026, 7, 18, 16, 12)
LOCALES = [EN, PSEUDO, loc("fr-FR"), loc("fr-FR", 12), loc("fr-CA"), loc("fr-CA", 12), loc("es-ES"), loc("es-MX", 12),
           loc("es-419"), loc("tr-TR"), loc("tr-TR", 12)]


def ids(locale: i18n.Locale) -> str:
    return f"{locale.language}-{locale.format}-{locale.clock}"


def wall(moment: datetime) -> datetime:
    return moment.astimezone()


def limit(kind: str, percent: float, resets_at: datetime, **extra: Any) -> Limit:
    window = 18000 if kind == "session" else 604800
    return Limit(kind=kind, group="session" if kind == "session" else "weekly", percent=percent, severity=None,
                 resets_at=resets_at, is_active=True, label=extra.pop("label", kind.upper()), window_seconds=window,
                 **extra)


def held(resets_at: datetime, now: datetime, **scoped: Any) -> Any:
    """A live reading whose three limits reset at ``resets_at``, held by the guard on its session until then."""
    limits = [limit("session", 96.0, resets_at), limit("weekly_all", 40.0, resets_at),
              limit("weekly_scoped", 12.0, resets_at, **{"label": "FABLE WEEKLY", "scope_model": "Fable",
                                                         "allowance_pct": 50.0, **scoped})]
    block = {"state": "holding", "resume_at": resets_at.isoformat(), "policy": {"session_cap": 95},
             "over": [{"name": "session", "label": "session", "percent": 96.0, "cap": 95}]}
    return dataclasses.replace(dump._state(now, ApiStatus.LIVE, limits, 12), guard=block)


def test_fr_ca_reset_and_resume_times_are_written_its_way():
    now, at = wall(WEDNESDAY_NOON), wall(SATURDAY_1612)
    model = build_model(held(at, now), now, locale=FR_CA)
    assert [row.resets_text for row in model.rows] == ["sam. 16 h 12"] * 3
    assert model.guard is not None and model.guard.resume_clock == "sam. 16 h 12"
    same_day = build_model(held(at, wall(SATURDAY_1612.replace(hour=9))), wall(SATURDAY_1612.replace(hour=9)),
                           locale=FR_CA)
    assert same_day.rows[0].resets_text == "16 h 12" and same_day.guard.resume_clock == "16 h 12"


def test_a_turkish_twelve_hour_clock_writes_its_day_period_first():
    now, at = wall(WEDNESDAY_NOON), wall(SATURDAY_1612)
    model = build_model(held(at, now), now, locale=loc("tr-TR", 12))
    assert model.rows[0].resets_text == "Cmt ÖS\u202f4:12"
    assert build_model(held(at, now), now, locale=loc("tr-TR")).rows[0].resets_text == "Cmt 16:12"


@pytest.mark.parametrize("locale", LOCALES, ids=ids)
def test_a_reset_on_the_same_local_day_has_no_weekday_in_any_language(locale):
    now = wall(datetime(2026, 7, 18, 0, 5))                 # Saturday, just after midnight
    saturday = formats.weekday(5, locale)
    for hour in range(24):
        at = wall(datetime(2026, 7, 18, hour, 59))
        text = view.format_resets(at, now, locale)
        assert text == formats.clock(at, locale) and saturday not in text, (hour, text)
        later = view.format_resets(at + timedelta(days=1), now, locale)
        assert later == formats.day_clock(at + timedelta(days=1), locale), (hour, later)
        assert formats.weekday(6, locale) in later


def test_ages_countdowns_and_shares_follow_the_locale_in_capitals():
    now = datetime(2026, 10, 21, 20, 12, tzinfo=timezone.utc)
    fr, tr = loc("fr-FR"), loc("tr-TR")
    assert view.format_age(now - timedelta(seconds=12), now, fr) == "IL Y A 12\u00a0S"
    assert view.format_age(now - timedelta(hours=3), now, tr) == "3 SA. ÖNCE"
    assert view.format_countdown(2 * 86400 + 3 * 3600, fr) == "2J 3H"
    assert view.format_countdown(3 * 3600 + 27 * 60, tr) == "3S 27D"           # 3 hours 27 minutes
    assert view.format_countdown(3600, tr) == "1 SA"
    assert view.format_countdown(30, fr) == "<1MIN" and view.format_countdown(30, FR_CA) == "<1M"
    assert view.share_text(50.0, fr) == "50\u00a0% SHARE" and view.share_text(49.5, tr) == "%50 SHARE"
    for locale in (EN, PSEUDO):                                                   # English's own shapes
        assert view.format_age(now - timedelta(seconds=12), now, locale) == "12 S AGO"
        assert view.format_countdown(30, locale) == "<1M" and view.format_countdown(7500, locale) == "2H 5M"
    assert view.share_text(50.0, EN) == "50% SHARE"
    assert view.share_text(50.0, PSEUDO) == "50\u00a0% SHARE"                  # the pseudo-locale's own percent


def test_the_models_texts_follow_the_locale_and_its_values_do_not():
    english, french = dump.sample_model("guard_hold_weekly"), dump.sample_model("guard_hold_weekly", locale=FR_CA)
    assert (english.age_text, french.age_text) == ("12 S AGO", "IL Y A 12\u00a0S")
    assert [row.share_text for row in french.rows] == [None, None, "50\u00a0% SHARE"]
    assert english.guard is not None and french.guard is not None
    assert (english.guard.resume_in, french.guard.resume_in) == ("2D 3H", "2J 3H")
    resume = french.guard.resume_at.astimezone()
    assert french.guard.resume_clock == french.guard.resume_text == formats.day_clock(resume, FR_CA)
    assert [row.resets_text for row in french.rows] != [row.resets_text for row in english.rows]
    assert _data(french) == _data(english)


def test_a_rows_label_stays_english_cased_in_every_language():
    """Row.label is the English label: a scoped limit without one is named from its model by English rules."""
    now, at = wall(WEDNESDAY_NOON), wall(SATURDAY_1612)
    state = held(at, now, label="", scope_model="Haiku")
    assert build_model(state, now, locale=loc("tr-TR")).rows[2].label == "HAIKU"
