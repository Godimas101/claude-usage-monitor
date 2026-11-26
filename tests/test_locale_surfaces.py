"""The app's locale on the widgets, the tray and the alerts (#91).

The locale rides on the view model (WidgetModel.locale, English by default), so every surface draws in the one the app
gives build_model, and the frame dump and the golden digests take it as an argument; the alerts read the app's locale
on every evaluation. English stays byte for byte what it was: tests/test_formats.py, the golden frames and the pins hold
that. Here: the other languages reach each surface, and only its numbers, percents and times change.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

import widget_words
from usage_monitor import app, casing, cldr, formats, i18n
from usage_monitor.data import alerts
from usage_monitor.data.alerts import AlertEngine
from usage_monitor.data.models import ApiStatus, Limit, Spend
from usage_monitor.render import dump, engine
from usage_monitor.render import model as view
from usage_monitor.render.model import build_model
from usage_monitor.render.skins import gates, get_skin
from usage_monitor.settings import Settings
from usage_monitor.ui import tray
from usage_monitor.ui.widgets import FrameCache, steady

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

# The view model's texts, which follow the locale (their words follow its language too, #93); every other field is
# data, the same in every language.
MODEL_TEXTS = ("age_text", "status_text", "stale_message")
ROW_TEXTS = ("resets_text", "share_text", "label")
GUARD_TEXTS = ("resume_text", "resume_in", "resume_clock", "labels")


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
    """The frame caches key on steady(model), so steady() keeps the locale: two locales never share a cached frame,
    even where their models' texts agree. fr-FR and fr-CH write the same resets and age on normal2, but Gates' strip
    draws "13 %" in one and "13%" in the other."""
    english, french = dump.sample_model("normal3"), dump.sample_model("normal3", locale=FR_CA)
    assert steady(french).locale == FR_CA and steady(english).locale == EN
    assert steady(french) != steady(english)
    assert steady(dump.sample_model("normal3", locale=EN)) == steady(english)
    france, swiss = (dump.sample_model("normal2", locale=loc(tag)) for tag in ("fr-FR", "fr-CH"))
    assert dataclasses.replace(france, locale=EN) == dataclasses.replace(swiss, locale=EN)
    assert steady(france) != steady(swiss)
    theme = THEMES["gates"]
    skin, variant = get_skin("gates"), theme.variant(None)
    cache, frames = FrameCache(), []

    def strip(model: Any) -> Any:
        frames.append(skin.render_taskbar(model, theme, variant, 40, 1.0, None).tobytes())
        return frames[-1]

    for model in (france, swiss, swiss):
        cache.get((theme.id, None, 1.0, steady(model)), True, lambda: strip(model))
    assert len(frames) == 2 and frames[0] != frames[1]          # drawn again for fr-CH, once


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
    # The pseudo-locale's own percent, and its words from the catalog (#93).
    assert view.share_text(50.0, PSEUDO) == "[50\u00a0% \u015eH\u00c1R\u00c9 \u0131\u0130\u011f]"


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


def test_a_scoped_rows_label_is_worded_in_the_models_language():
    """#93: English keeps a scoped row's label as the limit gives it, and names an unlabelled one from its model by
    English rules. Another language words it from the catalog (English's words while Turkish has none of its own),
    the model's name in its own capitals: Turkish's dotted İ."""
    now, at = wall(WEDNESDAY_NOON), wall(SATURDAY_1612)
    state = held(at, now, label="", scope_model="Haiku")
    assert build_model(state, now).rows[2].label == "HAIKU"
    turkish = build_model(state, now, locale=loc("tr-TR")).rows[2].label
    assert (turkish, turkish.language) == ("HAİKU WEEKLY", "en")


# ── The skins' numbers (step 1.6) ─────────────────────────────────────────────

THEMES = dump.load_theme_objects()
NOW = dump.SAMPLE_NOW


@pytest.fixture
def runs(monkeypatch):
    """Every run of text the skins hand over while drawing, as dicts of text, x, baseline, advance and align (logical
    px; x is the right edge of a right-aligned run). Nostromo and Night City draw through engine.Canvas.text_layer,
    Gates through its Ink.put."""
    seen: list[dict[str, Any]] = []
    real_layer, real_put = engine.Canvas.text_layer, gates.Ink.put

    def layer(self, text, x, baseline, font, tracking=0.0, align="left"):  # type: ignore[no-untyped-def]
        made, advance = real_layer(self, text, x, baseline, font, tracking, align)
        seen.append({"text": text, "x": x, "baseline": baseline, "advance": advance, "align": align})
        return made, advance

    def put(self, text, x, baseline, face, color, align="left"):  # type: ignore[no-untyped-def]
        advance = real_put(self, text, x, baseline, face, color, align)
        seen.append({"text": text, "x": x, "baseline": baseline, "advance": advance, "align": align})
        return advance

    monkeypatch.setattr(engine.Canvas, "text_layer", layer)
    monkeypatch.setattr(gates.Ink, "put", put)
    return seen


def reading(session: float, weekly: float, *, capped: bool = False) -> Any:
    """A live reading of the session and the week (and Fable's share), held by the guard at 100 % caps when
    ``capped``."""
    limits = [limit("session", session, NOW + timedelta(hours=3, minutes=27)),
              limit("weekly_all", weekly, NOW + timedelta(days=3)),
              limit("weekly_scoped", 4.0, NOW + timedelta(days=3), label="FABLE WEEKLY", scope_model="Fable",
                    allowance_pct=50.0)]
    state = dump._state(NOW, ApiStatus.LIVE, limits, 12)
    if capped:
        block = {"state": "holding", "resume_at": (NOW + timedelta(hours=3)).isoformat(),
                 "policy": {"session_cap": 100, "weekly_cap": 100},
                 "over": [{"name": "session", "label": "session", "percent": session, "cap": 100},
                          {"name": "weekly", "label": "weekly", "percent": weekly, "cap": 100}]}
        state = dataclasses.replace(state, guard=block)
    return state


def drawn(runs: list[dict[str, Any]], theme_id: str, surface: str, model: Any) -> list[dict[str, Any]]:
    """The runs one surface of a skin draws for ``model``, at 1x in the theme's default variant."""
    theme = THEMES[theme_id]
    skin, variant = get_skin(theme_id), theme.variant(None)
    runs.clear()
    if surface == "floating":
        skin.render_floating(model, theme, variant, 1.0, None)
    elif surface == "tooltip":
        skin.render_tooltip(model, theme, variant, 1.0)
    else:
        skin.render_taskbar(model, theme, variant, 40, 1.0, None)
    return list(runs)


def texts(found: list[dict[str, Any]]) -> list[str]:
    return [run["text"] for run in found]


def test_a_turkish_night_city_value_draws_its_percent_sign_before_the_numerals(runs):
    """Night City draws a row's numerals and its small percent sign as two runs; Turkish writes "%57", so the sign is
    left of the numerals, and English, French and Spanish keep it right of them."""
    for locale, first in ((loc("tr-TR"), True), (EN, False), (loc("fr-FR"), False), (loc("es-ES"), False)):
        model = build_model(reading(57.0, 34.0), NOW, locale=locale)
        for surface in ("floating", "tooltip"):
            found = drawn(runs, "night_city", surface, model)
            numerals = next(run for run in found if run["text"] == "57" and run["align"] == "right")
            sign = min((run for run in found if run["text"] == "%"),
                       key=lambda run: abs(run["baseline"] - numerals["baseline"]))
            left = numerals["x"] - numerals["advance"]
            if first:
                assert sign["x"] <= left + 0.01, (locale.format, surface, sign, numerals)
            else:
                assert numerals["x"] <= sign["x"] - sign["advance"] + 0.01, (locale.format, surface, sign, numerals)
    strip = texts(drawn(runs, "night_city", "taskbar", build_model(reading(57.0, 34.0), NOW, locale=loc("tr-TR"))))
    assert "%57" in strip and "%34" in strip


def test_a_french_exact_value_has_its_decimal_comma_in_every_hover_card(runs):
    model = build_model(reading(33.5, 2.25), NOW, locale=loc("fr-FR"))
    expected = {"nostromo": "33,5\u00a0%", "gates": "33,5\u00a0%", "night_city": "33,5"}    # Night City: sign apart
    for theme_id, value in expected.items():
        shown = texts(drawn(runs, theme_id, "tooltip", model))
        assert value in shown, (theme_id, shown)
        assert not any("33.5" in text or "2.2" in text for text in shown), (theme_id, shown)
    english = build_model(reading(33.5, 2.25), NOW)
    assert "33.5%" in texts(drawn(runs, "nostromo", "tooltip", english))


@pytest.mark.parametrize("tag", ["fr-FR", "fr-CA", "es-ES", "es-MX", "tr-TR"])
def test_the_boxes_sized_for_englishs_figures_draw_their_percents_tight(runs, tag):
    """Chris's decision on #91: Nostromo's strip values, Night City's strip percent and the cap badge on Gates' hover
    card draw "100%" (Turkish "%100"); everywhere else the language keeps its space ("100 %" in fr-FR and es-ES),
    Gates' floating badge too, which has the room."""
    locale = loc(tag)
    model = build_model(reading(100.0, 100.0, capped=True), NOW, locale=locale)
    tight = formats.percent("100", locale, tight=True)
    spaced = formats.percent("100", locale)
    assert tight == ("%100" if tag == "tr-TR" else "100%")
    for theme_id in ("nostromo", "night_city"):
        strip = texts(drawn(runs, theme_id, "taskbar", model))
        assert strip.count(tight) == 2, (theme_id, strip)
        assert spaced == tight or spaced not in strip, (theme_id, strip)
    for surface, value in (("floating", spaced), ("tooltip", tight)):
        badges = [text for text in texts(drawn(runs, "gates", surface, model)) if text.startswith("Cap ")]
        assert badges == [f"Cap {value}"] * 2, (surface, badges)
    elsewhere = {
        ("nostromo", "floating"): spaced, ("gates", "floating"): spaced, ("gates", "taskbar"): spaced,
        ("nostromo", "tooltip"): spaced, ("gates", "tooltip"): spaced,
    }
    for (theme_id, surface), value in elsewhere.items():
        shown = texts(drawn(runs, theme_id, surface, model))
        assert shown.count(value) >= 2, (theme_id, surface, shown)
    if tag in ("fr-FR", "es-ES"):
        assert spaced == "100\u00a0%"


def test_the_skins_numbers_follow_the_locale_where_they_are_drawn(runs):
    """Caps, guard readings and shares in French: no run of a French frame writes a percent the English way."""
    fr = loc("fr-FR")
    for name in dump.DUMP_STATE_NAMES:
        model = dump.sample_model(name, locale=fr)
        for theme_id in ("nostromo", "night_city", "gates"):
            for surface in ("floating", "tooltip", "taskbar"):
                for run in drawn(runs, theme_id, surface, model):
                    english = re.search(r"\d%", run["text"])
                    tight_box = (surface == "taskbar" and theme_id != "gates"
                                 or surface == "tooltip" and theme_id == "gates" and run["text"].startswith("Cap "))
                    assert english is None or tight_box, (name, theme_id, surface, run["text"])
    held = dump.sample_model("guard_hold_session", locale=fr)
    statement = " ".join(texts(drawn(runs, "nostromo", "tooltip", held)))
    assert "96 % (CAP 95 %)" in statement.replace("\u00a0", " ")


def test_nostromo_writes_names_and_units_in_the_language(runs):
    """#94: Nostromo's words come from the catalog (English's while a language has none of its own), a scope's name in
    the language's capitals on its row and in the guard's line (Turkish's dotted İ, Chris's answer on #86), and its
    age and hold chip as the view model writes them: CLDR's narrow units in capitals outside English."""
    at = NOW + timedelta(days=2, hours=3)
    state = held(at, NOW, label="", scope_model="Haiku")
    over = [*state.guard["over"], {"name": "Haiku weekly", "label": "Haiku weekly", "percent": 12.0, "cap": 10}]
    state = dataclasses.replace(state, usage_fetched_at=NOW - timedelta(minutes=41), guard={**state.guard, "over": over})
    for locale, name, hold, age in ((loc("tr-TR"), "HAİKU WEEKLY", "HOLD 2G 3S", "41D"),
                                    (loc("fr-FR"), "HAIKU WEEKLY", "HOLD 2J 3H", "41MIN"),
                                    (EN, "HAIKU WEEKLY", "HOLD 2D 3H", "41 M")):
        model = build_model(state, NOW, show_scoped="always", locale=locale)
        floating, tooltip = (texts(drawn(runs, "nostromo", surface, model)) for surface in ("floating", "tooltip"))
        assert {name, hold, age} <= set(floating) and name in tooltip, (locale.format, floating)
        assert f"{name} " in " ".join(tooltip[tooltip.index("GUARD HOLDING:"):]), (locale.format, tooltip)
        assert not [text for text in floating + tooltip if COMBINING_DOT in text], locale.format


def test_night_city_writes_names_and_units_in_the_language(runs):
    """#95: Night City's words come from the catalog (English's while a language has none of its own), a scope's name
    in the language's capitals on its row and in the guard's line (Turkish's dotted İ, Chris's answer on #86), and its
    hold chip and its band's age as the view model writes them: CLDR's narrow units in capitals outside English."""
    at = NOW + timedelta(days=2, hours=3)
    state = held(at, NOW, label="", scope_model="Haiku")
    over = [*state.guard["over"], {"name": "Haiku weekly", "label": "Haiku weekly", "percent": 12.0, "cap": 10}]
    state = dataclasses.replace(state, guard={**state.guard, "over": over})
    stale = dataclasses.replace(state, status=ApiStatus.STALE, guard=None, usage_fetched_at=NOW - timedelta(minutes=41))
    for locale, name, hold, age in ((loc("tr-TR"), "HAİKU", "HOLD 2G 3S", "41D OLD"),
                                    (loc("fr-FR"), "HAIKU", "HOLD 2J 3H", "41MIN OLD"),
                                    (EN, "HAIKU", "HOLD 2D 3H", "41 M OLD")):
        model = build_model(state, NOW, show_scoped="always", locale=locale)
        floating, tooltip = (texts(drawn(runs, "night_city", surface, model)) for surface in ("floating", "tooltip"))
        assert {name, hold} <= set(floating) and name in tooltip, (locale.format, floating)
        guard_line = " ".join(tooltip[tooltip.index("GUARD // HOLDING:"):]).replace(engine.NO_BREAK, " ")
        assert f"{name} WEEKLY " in guard_line, (locale.format, tooltip)
        band = texts(drawn(runs, "night_city", "floating", build_model(stale, NOW, show_scoped="always", locale=locale)))
        assert age in band and "RELINK // USE CLAUDE CODE" in band, (locale.format, band)
        shown = floating + tooltip + band
        assert not [text for text in shown if COMBINING_DOT in text], locale.format
        assert locale.language != "tr" or not [text for text in shown if "HAIKU" in text], shown


# ── The tray's percent (step 1.7) and words (#93) ─────────────────────────────

def test_the_tray_tooltip_writes_its_percents_in_the_locale():
    assert app.tray_tooltip(dump.sample_model("normal3")) == "Claude usage - session 13% - weekly 2%"
    assert (app.tray_tooltip(dump.sample_model("normal3", locale=loc("fr-FR")))
            == "Claude usage - session 13\u00a0% - weekly 2\u00a0%")
    turkish = app.tray_tooltip(dump.sample_model("normal3", locale=loc("tr-TR")))
    assert turkish == "Claude usage - session %13 - weekly %2"
    pseudo, fill = i18n.pseudo_text, i18n.fill                               # the pseudo-locale's words too (#93)
    assert app.tray_tooltip(dump.sample_model("stale", locale=PSEUDO)) == " - ".join([
        pseudo("Claude usage"), fill(pseudo("session {percent}"), {"percent": "13\u00a0%"}),
        fill(pseudo("weekly {percent}"), {"percent": "2\u00a0%"}), pseudo("stale")])


def test_the_tray_countdown_is_cldrs_outside_english():
    """#93: the guard's countdown in the tooltip is CLDR's narrow units in French, Spanish and Turkish, in CLDR's own
    case; English and the pseudo-locale keep their own letters."""
    def countdown(locale: i18n.Locale, seconds: int) -> str:
        model = dump.sample_model("guard_hold_session", locale=locale)
        model = dataclasses.replace(model, guard=dataclasses.replace(model.guard, resume_seconds=seconds))
        before = " (ıñ " if locale.language == i18n.PSEUDO else " (in "       # the pseudo-locale's "in"
        return app.tray_tooltip(model, limit=1000).rsplit(before, 1)[1].split(")")[0]

    two_hours = 2 * 3600 + 14 * 60
    assert [countdown(locale, two_hours) for locale in (EN, PSEUDO, loc("fr-FR"), loc("tr-TR"), loc("es-ES"))] == [
        "2h 14m", "2h 14m", "2h 14min", "2s 14d", "2h 14min"]
    assert [countdown(locale, 30) for locale in (EN, PSEUDO, loc("fr-FR"), loc("fr-CA"), loc("tr-TR"))] == [
        "<1m", "<1m", "<1min", "<1m", "<1d"]
    assert countdown(loc("es-CO"), (6 * 24 + 23) * 3600) == "6 días 23 h"


def _units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def test_the_tooltip_sweep_tries_a_clock_of_every_length():
    """A tooltip's length, and so whether it fits, drops or is cut, hangs on its resume clock's length, so the sweep
    tries one clock of each length a locale writes (#93), in both of the guard's forms (model.format_resets: the clock
    alone on the same day, the weekday and the clock on another): every length is tried, once."""
    several = 0
    for language in i18n.DEV_LANGUAGES:
        for locale in widget_words.sweep_locales(language):
            moments = [widget_words.SWEEP_MONDAY + timedelta(days=day, hours=hour, minutes=minute)
                       for day in range(7) for hour in widget_words.SWEEP_HOURS for minute in widget_words.SWEEP_MINUTES]
            written = {write(moment, locale) for moment in moments for write in (formats.clock, formats.day_clock)}
            tried = widget_words.sweep_clocks(locale)
            assert set(tried) <= written and sorted(map(_units, tried)) == sorted(set(map(_units, written))), locale
            several += len(tried) > 1
    assert several, "no locale writes clocks of two lengths, so the check above proves nothing"


def test_the_tooltip_sweep_tries_a_countdown_of_every_length():
    """The same for the guard's countdown: of every countdown the guard can show (each minute up to a week, and under a
    minute), the sweep tries one of each length the tooltip writes, once. Turkish's one hour ("1 sa 10d") is longer
    than any of its other hours ("10s 10d")."""
    shapes: dict[Any, int] = {}                     # each countdown_units shape, at the first second that has it
    for seconds in (30, *range(60, 7 * 86400 + 1, 60)):
        shapes.setdefault(view.countdown_units(seconds), seconds)
    tags = set()
    for language in i18n.DEV_LANGUAGES:
        for locale in widget_words.sweep_locales(language):
            if (language, locale.format) in tags:
                continue                            # the clock does not change a countdown
            tags.add((language, locale.format))
            written = {app._tray_countdown(seconds, locale) for seconds in shapes.values()}
            tried = [app._tray_countdown(seconds, locale) for seconds in widget_words.sweep_countdowns(locale)]
            assert sorted(map(_units, tried)) == sorted(set(map(_units, written))), locale
    turkish = [app._tray_countdown(seconds, loc("tr-TR")) for seconds in widget_words.sweep_countdowns(loc("tr-TR"))]
    assert max(turkish, key=_units) == "1 sa 10d"


def test_every_language_fits_the_tooltip_in_every_tag(monkeypatch):
    """The worst tooltips each language can show (two limits at 100 % in each status, the guard holding with a resume
    clock and a countdown of every length, a dev instance: widget_words.tooltip_sweep), in every format tag and clock,
    fit the shell's 127 UTF-16 units through the drop rule alone: app.tray_tooltip never has to cut one (tray.clip).
    English, French, Spanish and Turkish (English's words while their catalogs are empty) keep the whole guard phrase;
    the pseudo-locale, longer, needs the drop rule."""
    def clip(text: str, limit: int) -> str:
        assert tray.clip(text, limit) == text, f"cut: {text}"
        return text

    monkeypatch.setattr(app, "clip", clip)
    longest: dict[str, int] = {}
    for language in i18n.DEV_LANGUAGES:
        texts = list(widget_words.tooltip_sweep(language))
        assert texts and all(text.endswith(" (dev)") for text in texts), language
        if language != i18n.PSEUDO:
            assert all(" (in " in text for text in texts), language
        longest[language] = max(map(_units, texts))
    # Today's worst: es-CO and fr-CA on a 12-hour clock with English's words, Turkish with CLDR's one hour ("1 sa"),
    # and the pseudo-locale live (no status word) with a same-day resume, its countdown kept at the shell's limit. A
    # draft of fr, es or tr changes its own figure here and in test_budgets.TOOLTIPS (#104 to #106).
    assert longest == {"en": 113, "fr": 120, "es": 121, "tr": 114, "pseudo": 127}


# ── The alerts (step 1.8) ─────────────────────────────────────────────────────

ALERT_NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
ALERT_TZ = timezone(timedelta(hours=-5))                 # 7:00 AM local on Thursday 22 October 2026
SESSION_AT = datetime(2026, 10, 22, 21, 40, tzinfo=timezone.utc)    # 4:40 PM local
WEEK_AT = datetime(2026, 10, 24, 21, 0, tzinfo=timezone.utc)        # Saturday 4:00 PM local


def scoped(model: str | None = "Fable", surface: str | None = None, percent: float = 10.0, **extra: Any) -> Limit:
    return limit("weekly_scoped", percent, WEEK_AT, label=extra.pop("label", "X WEEKLY"), scope_model=model,
                 scope_surface=surface, **extra)


def alerts_in(settings: Any, locale: i18n.Locale, *steps: list[Limit]) -> list[list[Any]]:
    """The alerts an engine reading ``locale`` gives for each step's limits, a minute apart (the first is the
    baseline)."""
    engine = AlertEngine(settings, tz=ALERT_TZ, locale_provider=lambda: locale)
    return [engine.evaluate(dump._state(ALERT_NOW + timedelta(minutes=index), ApiStatus.LIVE, limits, 0))
            for index, limits in enumerate(steps)]


def test_the_alerts_write_their_percents_and_reset_times_in_the_locale(settings):
    fr = loc("fr-FR")
    found = alerts_in(settings, fr, [limit("session", 10.0, SESSION_AT), limit("weekly_all", 10.0, WEEK_AT)],
                      [limit("session", 91.0, SESSION_AT), limit("weekly_all", 81.0, WEEK_AT)])[-1]
    assert [(alert.title, alert.message) for alert in found] == [
        ("Session limit at 90\u00a0%", "Resets 16:40"), ("Weekly limit at 80\u00a0%", "Resets sam. 16:00")]
    turkish = alerts_in(settings, loc("tr-TR", 12), [limit("session", 10.0, SESSION_AT)],
                        [limit("session", 91.0, SESSION_AT)])[-1]
    assert [(alert.title, alert.message) for alert in turkish] == [("Session limit at %90", "Resets ÖS\u202f4:40")]
    english = alerts_in(settings, EN, [limit("session", 10.0, SESSION_AT)], [limit("session", 91.0, SESSION_AT)])[-1]
    assert [(alert.title, alert.message) for alert in english] == [("Session limit at 90%", "Resets 4:40 PM")]


def test_a_reset_announced_when_its_window_ends_writes_zero_and_the_name_in_the_locale(settings):
    """The reset of a window that ran out is worded from the name kept since its last reading."""
    ended = SESSION_AT + timedelta(minutes=2) - ALERT_NOW
    engine = AlertEngine(settings, tz=ALERT_TZ, locale_provider=lambda: loc("fr-FR"))
    for minutes, percent in ((0, 10.0), (1, 91.0), (ended.total_seconds() / 60, 91.0)):
        now = ALERT_NOW + timedelta(minutes=minutes)
        limits = [limit("session", percent, SESSION_AT),
                  limit("weekly_scoped", percent, SESSION_AT, label="X WEEKLY", scope_model="Claude Instant")]
        found = engine.evaluate(dump._state(now, ApiStatus.LIVE, limits, 0))
    zero = "Usage is back to 0\u00a0%."
    assert [(alert.title, alert.message) for alert in found] == [("Session limit reset", zero),
                                                                 ("Claude Instant weekly limit reset", zero)]


@pytest.mark.parametrize("locale", LOCALES, ids=ids)
def test_the_alerts_reset_time_is_the_widgets_for_every_hour(locale):
    """The alerts and the view model write a reset time with the same formats, so the two never drift apart. Both read
    this PC's zone here (the alerts with no zone given), so the moments are its wall times."""
    for hour in range(24):
        at = wall(SATURDAY_1612.replace(hour=hour, minute=0))
        earlier, same_day = at - timedelta(days=2), wall(SATURDAY_1612.replace(hour=0, minute=0))
        weekly = alerts._reset_text(limit("weekly_all", 40.0, at), None, locale)
        session = alerts._reset_text(limit("session", 40.0, at), None, locale)
        assert weekly == f"Resets {view.format_resets(at, earlier, locale)}", hour
        assert session == f"Resets {view.format_resets(at, same_day, locale)}", hour
    assert alerts._reset_text(limit("weekly_all", 40.0, WEEK_AT), ALERT_TZ, loc("fr-FR")) == "Resets sam. 16:00"


def test_french_credits_are_in_plain_dollars_with_no_code():
    spend = Spend(enabled=True, used=1234.56, limit=10000.0, currency="USD")
    fr, es_mx = loc("fr-FR"), loc("es-MX")
    assert alerts._credits_text(spend, fr) == "Used 1\u202f234,56\u00a0$ of 10\u202f000,00\u00a0$"
    assert "USD" not in alerts._credits_text(spend, fr)
    assert alerts._credits_text(spend, es_mx) == "Used $1,234.56 of $10,000.00"
    euros = Spend(enabled=True, used=5.96, limit=None, currency="EUR")
    assert alerts._credits_text(euros, fr) == "Used 5,96\u00a0EUR"
    assert alerts._credits_text(euros, es_mx) == "Used EUR\u00a05.96"
    assert alerts._credits_text(Spend(enabled=True, used=5.96, currency=None), loc("tr-TR")) == "Used 5,96"
    assert alerts._credits_text(spend, EN) == "Used 1,234.56 of 10,000.00 USD"
    assert alerts._credits_text(spend, PSEUDO) == "Used 1\u202f234.56 of 10\u202f000.00 USD"


def test_a_turkish_title_keeps_a_name_as_written_and_never_writes_a_combining_dot(settings):
    """Turkish capitals of "Haiku" are "HAİKU" (Chris); a title is in sentence case, so the name stays "Haiku", and
    lowering what Turkish capitals made never leaves an i with a dot above (U+0307)."""
    tr = loc("tr-TR")
    for model, expected in (("Haiku", "Haiku weekly"), ("Claude Instant", "Claude Instant weekly"),
                            ("istanbul", "İstanbul weekly"), ("İzmir", "İzmir weekly")):
        title = alerts._name(scoped(model), tr)
        assert title == expected and "\u0307" not in title, (model, title)
        assert "\u0307" not in casing.lower(casing.upper(title, "tr"), "tr")
    assert casing.upper(alerts._name(scoped("Haiku"), tr), "tr") == "HAİKU WEEKLY"
    names = ("Haiku", "Claude Instant")
    found = alerts_in(settings, tr, [scoped(name, percent=10.0, label=name.upper()) for name in names],
                      [scoped(name, percent=91.0, label=name.upper()) for name in names])[-1]
    assert [alert.title for alert in found] == ["Haiku weekly limit at %90", "Claude Instant weekly limit at %90"]
    assert alerts._name(scoped("Claude Instant"), EN) == "Claude instant weekly"         # English keeps its quirk


def test_other_languages_name_every_kind_of_limit_from_its_scope():
    fr = loc("fr-FR")
    assert alerts._name(limit("session", 1.0, WEEK_AT), fr) == "Session"
    assert alerts._name(limit("weekly_all", 1.0, WEEK_AT), fr) == "Weekly"
    assert alerts._name(scoped(None, "claude_code"), fr) == "Claude_code weekly"
    assert alerts._name(scoped(None, None), fr) == "Weekly scoped"
    assert alerts._name(limit("design_weekly", 1.0, WEEK_AT, label="DESIGN WEEKLY"), fr) == "Design weekly"
    assert alerts._name(scoped("Fable", label=""), fr) == "Fable"                # no label: named by its model
    assert alerts._name(limit("session", 1.0, WEEK_AT, label=""), fr) == "Session"
    assert alerts._name(scoped("", label=""), fr) == "Scoped"
    assert alerts._name(scoped(" Spaced  Out ", label=""), fr) == "Spaced Out"


# ── Turkish frames (step 1.12) ────────────────────────────────────────────────

REPO = Path(__file__).resolve().parent.parent
COMBINING_DOT = "\u0307"
# The frame dump in a fresh Python, every run of text it draws listened to (as tests/english_left.py listens), under
# tr-TR on each clock; each time with the dump's Fable row and again with that row scoped to Haiku.
_TURKISH_FRAMES = """
import dataclasses
import json
import tempfile

from PIL import ImageDraw

from usage_monitor import i18n
from usage_monitor.render import dump, engine
from usage_monitor.render.skins import gates

runs = set()
real = (engine.Canvas.text_layer, gates.Ink.put, ImageDraw.ImageDraw.text, dump.sample_state)


def layer(self, text, *args, **kwargs):
    runs.add(str(text))
    return real[0](self, text, *args, **kwargs)


def put(self, text, *args, **kwargs):
    runs.add(str(text))
    return real[1](self, text, *args, **kwargs)


def draw(self, xy, text, *args, **kwargs):
    runs.add(str(text))
    return real[2](self, xy, text, *args, **kwargs)


def haiku(name, now=dump.SAMPLE_NOW):
    state = real[3](name, now)
    if state is None or state.usage is None:
        return state
    limits = [dataclasses.replace(limit, scope_model="Haiku", label="HAIKU WEEKLY") if limit.scope_model == "Fable"
              else limit for limit in state.usage.limits]
    guard = state.guard and json.loads(json.dumps(state.guard).replace("Fable", "Haiku").replace("FABLE", "HAIKU"))
    return dataclasses.replace(state, usage=dataclasses.replace(state.usage, limits=limits), guard=guard)


engine.Canvas.text_layer, gates.Ink.put, ImageDraw.ImageDraw.text = layer, put, draw
dump._save = lambda image, path, report: None
dump._contact_sheet = lambda theme, skin, path, variant_filter=None, **_kwargs: path
out = {}
for clock in (24, 12):
    locale = i18n.Locale(setting="system", language="tr", system="tr", format="tr-TR", clock=clock, offered=("en",))
    for scope, states in (("Fable", real[3]), ("Haiku", haiku)):
        runs.clear()
        dump.sample_state = states
        with tempfile.TemporaryDirectory(prefix="turkish-frames-") as folder:
            for theme_id, theme in sorted(dump.load_theme_objects().items()):
                dump.dump_frames(folder, [theme_id], variants=[theme.variants[0].id], scales=(1.0,), locale=locale)
        out[f"{clock} {scope}"] = sorted(runs)
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def turkish_frames() -> dict[str, list[str]]:
    """Every run of text the frame dump draws under tr-TR, on a 24-hour and on a 12-hour clock: every theme (in its
    first variant; the variants differ in colour), state and surface at 1x, with the time zone pinned as the frame
    dump's other tests pin it ("24 Fable", "24 Haiku", "12 Fable", "12 Haiku")."""
    done = subprocess.run([sys.executable, "-c", _TURKISH_FRAMES], cwd=REPO, capture_output=True, encoding="utf-8",
                          env={**os.environ, "TZ": "UTC0", "PYTHONPATH": str(REPO)}, timeout=600, check=False,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert done.returncode == 0, done.stderr[-3000:]
    return json.loads(done.stdout)


def test_no_turkish_frame_draws_a_combining_dot(turkish_frames):
    """A U+0307 comes from lower-casing Turkish's İ by the default rule (str.lower, str.capitalize); casing never
    writes one. Nostromo's own case calls moved to casing in #94 and Night City's in #95; Gates' do in #96: this holds
    every frame meanwhile."""
    assert set(turkish_frames) == {"24 Fable", "24 Haiku", "12 Fable", "12 Haiku"}
    for frames, runs in turkish_frames.items():
        assert runs and [run for run in runs if COMBINING_DOT in run] == [], frames


def test_turkish_frames_draw_turkish_weekdays_percents_and_clocks(turkish_frames):
    """The locale reaches every skin's frames: the Saturday reset is "Cmt" in all three (Nostromo and Night City in
    capitals), every percent has its sign first, the day period ("ÖS") comes first on a 12-hour clock, and an
    afternoon hour is written past 12 on a 24-hour clock only. Nothing is left in English's shapes."""
    for frames, runs in turkish_frames.items():
        shown = "\n".join(runs)
        assert [lead for lead in ("RESETS CMT ", "RST CMT ", "Resets Cmt ") if lead not in shown] == [], frames
        assert re.search(r"%\d", shown) and not re.search(r"\d%", shown), frames
        assert ("ÖS" in shown) == frames.startswith("12"), frames
        assert bool(re.search(r"\b(?:1[3-9]|2[0-3]):[0-5]\d", shown)) == frames.startswith("24"), frames
        assert not re.search(r"\b(?:SAT|Sat|AM|PM)\b", shown), frames


def test_a_name_with_an_i_reaches_the_turkish_frames(turkish_frames):
    """Haiku, scoped in place of Fable, is drawn in every skin's own case, so the combining-dot check above has an i to
    see: in Turkish capitals in Nostromo since #94 ("HAİKU WEEKLY") and in Night City since #95 ("HAİKU", a scoped row
    shows the name alone), and as written in Gates ("Haiku weekly"). No skin writes it by English's rule ("HAIKU")."""
    for clock in (24, 12):
        runs = turkish_frames[f"{clock} Haiku"]
        assert {"HAİKU", "Haiku weekly", "HAİKU WEEKLY"} <= set(runs), clock
        assert not [run for run in runs if "HAIKU" in run], clock
        assert not [run for run in runs if "Fable" in run or "FABLE" in run], clock
    assert any(re.search("Fable|FABLE", run) for run in turkish_frames["24 Fable"])
