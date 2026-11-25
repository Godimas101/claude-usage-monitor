"""The formats (usage_monitor/formats.py, #91), and today's English, which they must not change.

tests/fixtures/formats_english.json holds what the model, the tray, the skins and the alerts said before #91 changed
any of them. Today's callers are held to it, and so is the formats module's English branch, with each caller's own
rounding: English must not change by a byte. Then French, Spanish and Turkish, by format tag and clock, against what
CLDR 47 and Windows' ICU write; the pseudo-locale; and a process whose own locale is Turkish.

The fixture's moments are local wall times, made in the test's own time zone here, so the texts are the same on any PC.
HELPERS names each recorded helper and how it is called; a step that reshapes one changes its entry, not the fixture.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import pytest

from usage_monitor import app, formats, i18n
from usage_monitor.data import alerts
from usage_monitor.data.identity import legacy_label
from usage_monitor.data.models import Limit, Spend
from usage_monitor.render import model
from usage_monitor.render.model import Row
from usage_monitor.render.skins import gates, night_city, nostromo

FIXTURE = Path(__file__).parent / "fixtures" / "formats_english.json"
MONDAY = datetime(2026, 7, 13)          # a week with no daylight-saving change anywhere


@pytest.fixture(scope="module")
def english() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def local(text: str) -> datetime:
    """A wall time in this PC's zone, as the model reads a reset time."""
    return datetime.fromisoformat(text).astimezone()


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def limit(kind: str, *, resets_at: datetime | None = None, model: str | None = None, surface: str | None = None,
          label: str | None = None) -> Limit:
    return Limit(kind=kind, group="session" if kind == "session" else "weekly", percent=40.0, severity=None,
                 resets_at=resets_at, is_active=True,
                 label=legacy_label(kind, model, surface) if label is None else label, scope_model=model,
                 scope_surface=surface)


def scoped_row(share: float | None) -> Row:
    return Row("scoped:fable", "FABLE WEEKLY", 4.0, 0.5, "normal", None, None, None, None, kind="weekly_scoped",
               scope_model="Fable", scope_name="Fable", share_pct=share)


# Each recorded helper as today's callers call it.
HELPERS: dict[str, dict[str, Callable[[Any], str]]] = {
    "percent": {
        "app._percent_text": lambda p: app._percent_text(p, i18n.ENGLISH),
        "nostromo._percent_text": lambda p: nostromo._percent_text(p, i18n.ENGLISH),
        "nostromo._exact_percent_text": lambda p: nostromo._exact_percent_text(p, i18n.ENGLISH),
        "gates._percent_text": lambda p: gates._percent_text(p, i18n.ENGLISH),
        "gates._exact_percent_text": lambda p: gates._exact_percent_text(p, i18n.ENGLISH),
        "night_city._digits": night_city._digits,
        "night_city._exact_digits": night_city._exact_digits,
    },
    "cap": {
        "nostromo._number": nostromo._number,
        "gates._number": gates._number,
        "night_city._number": night_city._number,
    },
    "share": {
        "nostromo._qualifier": lambda share: nostromo._qualifier(scoped_row(share), i18n.ENGLISH),
        "nostromo._short_qualifier": lambda share: nostromo._short_qualifier(scoped_row(share), i18n.ENGLISH),
        "gates._tag": lambda share: gates._tag(scoped_row(share), i18n.ENGLISH),
        "night_city._share_tag": lambda share: night_city._share_tag(scoped_row(share), i18n.ENGLISH),
    },
}


def test_the_fixture_is_whole(english):
    """So a test below cannot pass by reading an empty list: every helper of HELPERS has its outputs, one per input."""
    assert english["weekdays"] == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    assert len(english["clock"]) == 24 * 60 and english["clock"][16 * 60 + 12] == "4:12 PM"
    for group, helpers in HELPERS.items():
        assert set(english[group]) == {"inputs", *helpers}
        assert all(len(english[group][name]) == len(english[group]["inputs"]) for name in helpers)
    for key in ("format_resets", "format_age", "format_countdown", "share_text", "alerts._name", "alerts._reset_text",
                "alerts._credits_text"):
        assert english[key], key


def test_a_reset_today_reads_as_the_recorded_clock_for_every_minute(english):
    now = local(f"{MONDAY:%Y-%m-%d}T00:00")
    for minute, clock in enumerate(english["clock"]):
        assert model.format_resets(now + timedelta(minutes=minute), now) == clock


def test_a_reset_on_another_day_reads_as_the_recorded_weekday_and_clock(english):
    now = local(f"{MONDAY:%Y-%m-%d}T12:00")
    for day in range(1, 8):
        for minute in range(0, 24 * 60, 7):
            at = local(f"{MONDAY + timedelta(days=day, minutes=minute):%Y-%m-%dT%H:%M}")
            assert model.format_resets(at, now) == f"{english['weekdays'][day % 7]} {english['clock'][minute]}"


def test_reset_texts_are_the_recorded_ones(english):
    for at, now, text in english["format_resets"]:
        assert model.format_resets(local(at), local(now)) == text, (at, now)


def test_ages_countdowns_and_shares_are_the_recorded_ones(english):
    now = datetime(2026, 10, 21, 20, 12, tzinfo=timezone.utc)
    for seconds, text in english["format_age"]:
        assert model.format_age(now - timedelta(seconds=seconds), now) == text, seconds
    for seconds, text in english["format_countdown"]:
        assert model.format_countdown(seconds) == text, seconds
    for share, text in english["share_text"]:
        assert model.share_text(share) == text, share


@pytest.mark.parametrize("group,name", [(group, name) for group, helpers in HELPERS.items() for name in helpers])
def test_the_widgets_numbers_are_the_recorded_ones(english, group, name):
    helper = HELPERS[group][name]
    for value, text in zip(english[group]["inputs"], english[group][name]):
        if isinstance(text, dict):                          # an input the helper refuses; no caller passes it
            with pytest.raises(Exception) as raised:
                helper(value)
            assert type(raised.value).__name__ == text["raises"], (name, value)
        else:
            assert helper(value) == text, (name, value)


def test_the_alerts_names_reset_texts_and_credits_are_the_recorded_ones(english):
    for kind, scope_model, surface, label, text in english["alerts._name"]:
        assert alerts._name(limit(kind, model=scope_model, surface=surface, label=label), i18n.ENGLISH) == text
    for kind, at, text in english["alerts._reset_text"]:
        assert alerts._reset_text(limit(kind, resets_at=utc(at)), timezone.utc, i18n.ENGLISH) == text, (kind, at)
    for used, limit_value, currency, text in english["alerts._credits_text"]:
        spend = Spend(enabled=True, used=used, limit=limit_value, currency=currency)
        assert alerts._credits_text(spend, i18n.ENGLISH) == text


def test_the_alerts_clock_is_the_recorded_clock_for_every_minute(english):
    """The alerts word their reset clock apart from the model; both say the same, for every minute and weekday."""
    for day in range(7):
        for minute, clock in enumerate(english["clock"]):
            at = utc(f"{MONDAY + timedelta(days=day, minutes=minute):%Y-%m-%dT%H:%M}")
            assert alerts._reset_text(limit("session", resets_at=at), timezone.utc, i18n.ENGLISH) == f"Resets {clock}"
            weekly = alerts._reset_text(limit("weekly_all", resets_at=at), timezone.utc, i18n.ENGLISH)
            assert weekly == f"Resets {english['weekdays'][day]} {clock}"


# ── The formats' English branch gives the recorded English ────────────────────

EN = i18n.ENGLISH
EN_24 = i18n.Locale(setting="system", language="en", system="en", format="en-US", clock=24, offered=("en",))
PSEUDO = i18n.Locale(setting="system", language=i18n.PSEUDO, system="en", format="en-US", clock=12, offered=("en",))


def loc(tag: str, clock: int = 24) -> i18n.Locale:
    """The locale a French, Spanish or Turkish Windows with this regional format and clock gives."""
    return i18n.Locale(setting="system", language=i18n.primary(tag), system="en", format=tag, clock=clock,
                       offered=("en",))


def half_up(percent: float) -> str:
    """The widgets' and the tray's rounding."""
    return str(int(percent + 0.5) if percent >= 0 else 0)


def exact(percent: float) -> str:
    """The tooltips' exact value: one decimal, ".0" dropped."""
    text = f"{percent:.1f}"
    return text[:-2] if text.endswith(".0") else text


def cap(value: float) -> str:
    """A cap as people write it: 95, not 95.0."""
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


# Each recorded helper as today's caller rounds, then the formats' English branch.
ENGLISH_BY_FORMATS: dict[str, dict[str, Callable[[Any], str | None]]] = {
    "percent": {
        "app._percent_text": lambda p: formats.percent(half_up(p), EN),
        "nostromo._percent_text": lambda p: "--" if p is None else formats.percent(half_up(p), EN),
        "nostromo._exact_percent_text": lambda p: "--" if p is None else formats.percent(exact(p), EN),
        "gates._percent_text": lambda p: "--" if p is None else formats.percent(half_up(p), EN),
        "gates._exact_percent_text": lambda p: "--" if p is None else formats.percent(exact(p), EN),
        "night_city._digits": lambda p: "--" if p is None else formats.number(str(max(0, int(p + 0.5))), EN, group=False),
        "night_city._exact_digits": lambda p: "--" if p is None else formats.number(exact(max(0.0, p)), EN, group=False),
    },
    "cap": {name: (lambda v: formats.number(cap(v), EN, group=False))
            for name in ("nostromo._number", "gates._number", "night_city._number")},
    "share": {
        "nostromo._qualifier": lambda s: None if s is None else f"OF {formats.percent(str(round(s)), EN)} SHARE",
        "nostromo._short_qualifier": lambda s: None if s is None else formats.percent(str(round(s)), EN),
        "gates._tag": lambda s: "7D" if s is None else f"{formats.percent(str(round(s)), EN)} share",
        "night_city._share_tag": lambda s: None if s is None else f"{formats.percent(str(round(s)), EN)} SHARE",
    },
}


def test_the_english_clock_is_the_recorded_one_whatever_the_clock_setting(english):
    """English keeps today's 12-hour clock with an ASCII space (CLDR's en-US has U+202F before PM), and so does the
    pseudo-locale."""
    for minute, text in enumerate(english["clock"]):
        moment = MONDAY + timedelta(minutes=minute)
        assert formats.clock(moment, EN) == text
        assert formats.clock(moment, EN_24) == text
        assert formats.clock(moment, PSEUDO) == text
    assert formats.clock(MONDAY.replace(hour=19, minute=39), PSEUDO) == "7:39 PM"


def test_the_english_weekdays_and_day_clocks_are_the_recorded_ones(english):
    assert [formats.weekday(i, EN) for i in range(7)] == english["weekdays"]
    for day in range(7):
        for minute in range(0, 24 * 60, 11):
            moment = MONDAY + timedelta(days=day, minutes=minute)
            assert formats.day_clock(moment, EN) == f"{english['weekdays'][day]} {english['clock'][minute]}"
    for at, now, text in english["format_resets"]:
        moment = datetime.fromisoformat(at)
        same_day = at[:10] == now[:10]
        assert (formats.clock(moment, EN) if same_day else formats.day_clock(moment, EN)) == text, (at, now)


@pytest.mark.parametrize("group,name", [(group, name) for group, helpers in ENGLISH_BY_FORMATS.items()
                                        for name in helpers])
def test_the_english_numbers_are_the_recorded_ones(english, group, name):
    """Each caller's own rounding, then formats.number or formats.percent in English: today's text, byte for byte."""
    build = ENGLISH_BY_FORMATS[group][name]
    for value, text in zip(english[group]["inputs"], english[group][name]):
        if not isinstance(text, dict):                       # an input today's helper refuses has nothing to match
            assert build(value) == text, (name, value)


def test_the_english_shares_credits_and_alert_clocks_are_the_recorded_ones(english):
    for share, text in english["share_text"]:
        assert (None if share is None else f"{formats.percent(str(round(share)), EN)} SHARE") == text
    for used, limit_value, currency, text in english["alerts._credits_text"]:
        built = f"Used {formats.number(f'{used:.2f}', EN)}"
        if isinstance(limit_value, (int, float)) and math.isfinite(limit_value):
            built += f" of {formats.number(f'{limit_value:.2f}', EN)}"
        assert built + (f" {currency}" if currency else "") == text, (used, limit_value, currency)
    for kind, at, text in english["alerts._reset_text"]:
        moment = datetime.fromisoformat(at)
        built = formats.clock(moment, EN) if kind == "session" else formats.day_clock(moment, EN)
        assert f"Resets {built}" == text


def test_english_groups_only_when_asked_and_keeps_its_digits():
    assert formats.number("1234567.891", EN) == "1,234,567.891"
    assert formats.number("1234567.891", EN, group=False) == "1234567.891"
    assert formats.number(95, EN) == "95" and formats.number("-1234", EN) == "-1,234"
    assert formats.percent("1234", EN) == "1234%"                      # today's percents are never grouped
    assert formats.percent("--", EN) == "--%" and formats.number("1e+20", EN) == "1e+20"


def test_english_has_no_cldr_shapes():
    """English and the pseudo-locale write today's shapes; money, durations, ages and months serve fr, es and tr."""
    for locale in (EN, EN_24, PSEUDO):
        assert formats.english_shapes(locale)
        with pytest.raises(ValueError):
            formats.duration(((2, "h"), (14, "m")), locale)
        with pytest.raises(ValueError):
            formats.ago(12, "s", locale)
        with pytest.raises(ValueError):
            formats.money(5.96, "USD", locale)
        with pytest.raises(ValueError):
            formats.month(0, locale)
    for tag in ("fr-FR", "fr-CA", "es-ES", "es-MX", "tr-TR"):
        assert not formats.english_shapes(loc(tag))


# ── French, Spanish and Turkish ───────────────────────────────────────────────

def test_numbers_follow_the_format_tag():
    assert formats.number("1234.5", loc("fr-FR")) == "1\u202f234,5"
    assert formats.number("1234.5", loc("fr-CA")) == "1\u00a0234,5"
    assert formats.number("1234", loc("es-ES")) == "1234"              # two digits before a group (CLDR's minimum)
    assert formats.number("12345", loc("es-ES")) == "12.345"
    assert formats.number("1234", loc("es-MX")) == "1,234"
    assert formats.number("1234.5", loc("tr-TR")) == "1.234,5"
    assert formats.number("1234567.891", loc("fr-FR")) == "1\u202f234\u202f567,891"   # WebView2's (#88)
    assert formats.number("33.5", loc("fr-FR"), group=False) == "33,5"
    assert formats.number("-12345", loc("es-ES")) == "-12.345"
    assert formats.number("--", loc("fr-FR")) == "--"


def test_percents_put_the_sign_on_the_languages_side():
    assert formats.percent("34", loc("fr-FR")) == "34\u00a0%"
    assert formats.percent("34", loc("es-ES")) == "34\u00a0%"
    assert formats.percent("34", loc("es-MX")) == "34%"
    assert formats.percent("34", loc("tr-TR")) == "%34"
    assert formats.percent("33.5", loc("fr-CA")) == "33,5\u00a0%"
    assert formats.percent("12345", loc("fr-FR")) == "12\u202f345\u00a0%"
    assert formats.percent_parts("34", loc("tr-TR")) == ("%", "34", "")
    assert formats.percent_parts("34", loc("fr-FR")) == ("", "34", "\u00a0%")
    assert formats.percent_parts("-5", loc("fr-FR")) == ("-", "5", "\u00a0%")
    assert formats.percent_parts("-5", loc("tr-TR")) == ("-%", "5", "")
    assert formats.percent_parts("34", EN) == ("", "34", "%")


def test_tight_drops_the_space_and_keeps_the_side():
    """Chris's decision on #91 (Q1 a): in the boxes sized for English's figures, French and Spanish write 100% and
    99,99$; Turkish keeps its sign first, which already fits."""
    assert formats.percent("100", loc("fr-FR"), tight=True) == "100%"
    assert formats.percent("100", loc("es-ES"), tight=True) == "100%"
    assert formats.percent("100", loc("tr-TR"), tight=True) == "%100"
    assert formats.percent("100", EN, tight=True) == "100%"
    assert formats.percent("100", PSEUDO, tight=True) == "100%"
    assert formats.percent_parts("99.5", loc("fr-FR"), tight=True) == ("", "99,5", "%")
    assert formats.money(99.99, "USD", loc("fr-FR"), tight=True) == "99,99$"
    assert formats.money(99.99, "USD", loc("tr-TR"), tight=True) == "$99,99"


def test_clocks_follow_the_tag_and_the_windows_clock():
    """CLDR's 24-hour (Hm) or 12-hour (hm) pattern of the tag, by Locale.clock: never one hour cycle for all."""
    at_1612 = MONDAY.replace(hour=16, minute=12)
    cases = {
        ("fr-FR", 24): "16:12", ("fr-FR", 12): "4:12\u202fPM",
        ("fr-CA", 24): "16 h 12", ("fr-CA", 12): "4 h 12\u202fp.m.",
        ("es-ES", 24): "16:12", ("es-ES", 12): "4:12\u202fp.\u202fm.",
        ("es-MX", 24): "16:12", ("es-MX", 12): "4:12\u202fp.m.",
        ("tr-TR", 24): "16:12", ("tr-TR", 12): "ÖS\u202f4:12",
    }
    for (tag, hours), text in cases.items():
        assert formats.clock(at_1612, loc(tag, hours)) == text, (tag, hours)
    assert formats.clock(MONDAY.replace(hour=0, minute=5), loc("es-ES")) == "0:05"
    assert formats.clock(MONDAY.replace(hour=0, minute=5), loc("es-MX")) == "00:05"
    assert formats.clock(MONDAY.replace(hour=0, minute=5), loc("fr-FR", 12)) == "12:05\u202fAM"
    assert formats.clock(MONDAY.replace(hour=12, minute=0), loc("tr-TR", 12)) == "ÖS\u202f12:00"


def test_day_clocks_follow_the_tags_pattern():
    saturday = MONDAY.replace(day=18, hour=16, minute=12)
    assert formats.day_clock(saturday, loc("es-ES")) == "sáb, 16:12"
    assert formats.day_clock(saturday, loc("es-MX")) == "sáb 16:12"
    assert formats.day_clock(saturday, loc("tr-TR")) == "Cmt 16:12"
    assert formats.day_clock(saturday, loc("fr-FR")) == "sam. 16:12"
    assert formats.day_clock(saturday, loc("fr-CA")) == "sam. 16 h 12"
    assert formats.day_clock(saturday, loc("tr-TR", 12)) == "Cmt ÖS\u202f4:12"


def test_weekday_zero_is_monday_in_every_language():
    """datetime.weekday() counts from Monday; CLDR's lists start on Sunday."""
    for tag in ("fr-FR", "fr-CA", "fr-BE", "es-ES", "es-MX", "es-419", "tr-TR", "tr-CY"):
        locale = loc(tag)
        assert formats.weekday(0, locale) == formats.day_clock(MONDAY, locale).split(" ")[0].rstrip(","), tag
    assert [formats.weekday(0, loc(tag)) for tag in ("fr-FR", "es-ES", "tr-TR")] == ["lun.", "lun", "Pzt"]
    assert [formats.weekday(6, loc(tag)) for tag in ("fr-FR", "es-ES", "tr-TR")] == ["dim.", "dom", "Paz"]
    assert [formats.month(i, loc("tr-TR")) for i in (0, 3, 9)] == ["Oca", "Nis", "Eki"]
    assert formats.month(6, loc("fr-CA")) == "juill." and formats.month(6, loc("fr-FR")) == "juil."
    assert formats.month(8, loc("es-MX")) == "sep" and formats.month(8, loc("es-ES")) == "sept"


def test_durations_use_cldrs_units():
    """Turkish narrow units are the trap: "3s 27d" is 3 hours 27 minutes, and one hour is "1 sa"."""
    assert formats.duration(((3, "h"), (27, "m")), loc("tr-TR")) == "3s 27d"
    assert formats.duration(((1, "h"),), loc("tr-TR")) == "1 sa"
    assert formats.duration(((2, "d"), (3, "h")), loc("fr-FR")) == "2j 3h"
    assert formats.duration(((14, "m"),), loc("fr-CA")) == "14m"
    assert formats.duration(((14, "m"),), loc("fr-FR")) == "14min"
    assert formats.duration(((2, "d"),), loc("es-419")) == "2dd."
    assert formats.duration(((1, "d"),), loc("es-419")) == "1d."
    assert formats.duration(((2, "h"), (14, "m")), loc("es-ES"), "short") == "2 h 14 min"
    assert formats.duration((), loc("fr-FR")) == ""
    with pytest.raises(ValueError):
        formats.duration(((1, "h"),), loc("fr-FR"), "wide")


def test_money_writes_the_plain_dollar():
    """Chris: US dollars are the plain $. Each as Windows' ICU 72.1 writes the tag's pattern."""
    assert formats.money(5.96, "USD", loc("fr-FR")) == "5,96\u00a0$"
    assert formats.money(5.96, "USD", loc("fr-CA")) == "5,96\u00a0$"
    assert formats.money(5.96, "USD", loc("es-MX")) == "$5.96"
    assert formats.money(5.96, "USD", loc("tr-TR")) == "$5,96"
    assert formats.money(1234.5, "USD", loc("fr-FR")) == "1\u202f234,50\u00a0$"      # WebView2's, with the plain $
    assert formats.money(5.96, "EUR", loc("es-MX")) == "EUR\u00a05.96"               # a code next to a digit
    assert formats.money(5.96, "eur", loc("fr-FR")) == "5,96\u00a0EUR"
    assert formats.money(-5.96, "USD", loc("fr-FR")) == "-5,96\u00a0$"
    assert formats.money(1234.56, None, loc("fr-FR")) == "1\u202f234,56"


def test_ago_uses_cldrs_relative_time():
    assert formats.ago(12, "s", loc("fr-FR")) == "il y a 12\u00a0s"
    assert formats.ago(12, "s", loc("es-ES")) == "hace 12 s"
    assert formats.ago(12, "s", loc("tr-TR")) == "12 sn. önce"
    assert formats.ago(2, "d", loc("tr-TR")) == "2 gün önce"                # Babel's short day is empty: the wide one
    assert formats.ago(1, "d", loc("es-MX")) == "hace 1 día" and formats.ago(3, "d", loc("es-MX")) == "hace 3 días"


def test_a_tag_finds_its_region_else_its_languages_base():
    assert formats.number("1234.5", loc("fr-ca")) == "1\u00a0234,5"               # Windows' case is not CLDR's
    assert formats.number("1234.5", loc("es-ES-tradnl")) == "1234,5"              # es-ES_tradnl (traditional sort)
    assert formats.number("1234.5", loc("fr-XK")) == "1\u202f234,5"               # no CLDR region: the base
    other = i18n.Locale(setting="system", language="fr", system="en", format="de-DE", clock=24, offered=("en",))
    assert formats.number("1234.5", other) == "1\u202f234,5"                     # another language's tag: the base
    unknown = i18n.Locale(setting="system", language="de", system="en", format="de-DE", clock=24, offered=("en",))
    assert formats.english_shapes(unknown)                                     # a language with no table
    assert formats.canonical_tag("es_419") == "es-419" and formats.canonical_tag("sr_latn_rs") == "sr-Latn-RS"


def test_the_pseudo_locale_is_english_with_two_changes():
    """#89's pseudo format: numbers grouped with U+202F, a U+00A0 before %; English's clock and weekdays."""
    assert formats.number("1234.5", PSEUDO) == "1\u202f234.5"
    assert formats.percent("34", PSEUDO) == "34\u00a0%"
    assert formats.clock(MONDAY.replace(hour=19, minute=39), PSEUDO) == "7:39 PM"
    assert formats.day_clock(MONDAY.replace(day=18, hour=16), PSEUDO) == "Sat 4:00 PM"


# ── Nothing reads the process locale ──────────────────────────────────────────

SAMPLES = """
import json
from datetime import datetime
from usage_monitor import formats, i18n

def loc(tag, clock):
    return i18n.Locale(setting="system", language=i18n.primary(tag), system="en", format=tag, clock=clock,
                       offered=("en",))

locales = [i18n.ENGLISH] + [loc(tag, clock) for tag in ("fr-FR", "es-MX", "tr-TR") for clock in (12, 24)]
moment = datetime(2026, 7, 18, 16, 12)
out = []
for locale in locales:
    out.append([formats.number("1234567.5", locale), formats.percent("34", locale), formats.clock(moment, locale),
                formats.day_clock(moment, locale), [formats.weekday(i, locale) for i in range(7)]])
    if not formats.english_shapes(locale):
        out.append([formats.money(1234.5, "USD", locale), formats.duration(((3, "h"), (27, "m")), locale),
                    formats.ago(12, "s", locale), formats.month(3, locale)])
print(json.dumps(out))
"""


def test_no_format_reads_the_process_locale():
    """In a process whose own locale is Turkish (setlocale, LANG and LC_ALL), every format gives what it gives here. The
    child proves its locale took: strftime there names Monday Pzt."""
    script = ("import locale, time\n"
              "locale.setlocale(locale.LC_ALL, 'tr-TR')\n"
              "assert time.strftime('%a', (2026, 7, 13, 16, 0, 0, 0, 194, 0)) == 'Pzt'\n" + SAMPLES)
    here: dict[str, Any] = {}
    exec(compile(SAMPLES.replace("print(json.dumps(out))", "result = out"), "<samples>", "exec"), here)
    env = {**os.environ, "LANG": "tr_TR.UTF-8", "LC_ALL": "tr_TR.UTF-8", "PYTHONUTF8": "1",
           "PYTHONPATH": str(Path(__file__).resolve().parent.parent)}
    run = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, encoding="utf-8", env=env,
                         cwd=Path(__file__).resolve().parent.parent, timeout=120,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert run.returncode == 0, run.stderr[-2000:]
    assert json.loads(run.stdout) == json.loads(json.dumps(here["result"]))
