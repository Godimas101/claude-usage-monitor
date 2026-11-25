"""The app's locale on the widgets, the tray and the alerts (#91).

The locale rides on the view model (WidgetModel.locale, English by default), so every surface draws in the one the app
gives build_model, and the frame dump and the golden digests take it as an argument; the alerts read the app's locale
on every evaluation. English stays byte for byte what it was: tests/test_formats.py, the golden frames and the pins hold
that. Here: the other languages reach each surface, and only its numbers, percents and times change.
"""
from __future__ import annotations

import dataclasses
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from usage_monitor import formats, i18n
from usage_monitor.data.alerts import AlertEngine
from usage_monitor.data.models import ApiStatus, Limit
from usage_monitor.render import dump, engine
from usage_monitor.render import model as view
from usage_monitor.render.model import build_model
from usage_monitor.render.skins import gates, get_skin
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
    """Chris's decision on #91: Nostromo's strip values, Night City's strip percent and Gates' cap badge draw "100%"
    (Turkish "%100"); everywhere else the language keeps its space ("100 %" in fr-FR and es-ES)."""
    locale = loc(tag)
    model = build_model(reading(100.0, 100.0, capped=True), NOW, locale=locale)
    tight = formats.percent("100", locale, tight=True)
    spaced = formats.percent("100", locale)
    assert tight == ("%100" if tag == "tr-TR" else "100%")
    for theme_id in ("nostromo", "night_city"):
        strip = texts(drawn(runs, theme_id, "taskbar", model))
        assert strip.count(tight) == 2, (theme_id, strip)
        assert spaced == tight or spaced not in strip, (theme_id, strip)
    for surface in ("floating", "tooltip"):
        badges = [text for text in texts(drawn(runs, "gates", surface, model)) if text.startswith("Cap ")]
        assert badges == [f"Cap {tight}"] * 2, (surface, badges)
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
                    tight_box = surface == "taskbar" and theme_id != "gates" or run["text"].startswith("Cap ")
                    assert english is None or tight_box, (name, theme_id, surface, run["text"])
    held = dump.sample_model("guard_hold_session", locale=fr)
    statement = " ".join(texts(drawn(runs, "nostromo", "tooltip", held)))
    assert "96 % (CAP 95 %)" in statement.replace("\u00a0", " ")
