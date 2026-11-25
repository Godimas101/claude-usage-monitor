"""Numbers, percents, money, clocks, weekdays, months, durations and relative times in the app's locale (#91).

English keeps today's US formats byte for byte: each English branch is the code its callers have today, unchanged
(tests/fixtures/formats_english.json holds both to it), and English never reads the CLDR tables. French, Spanish and
Turkish follow their format tag
(``Locale.format``: Windows' regional format when it is of the same language, else the language's base) through
usage_monitor/cldr.py, and their clock follows ``Locale.clock`` (12 or 24). The pseudo-locale (#89) takes English's
shapes with two changes: numbers grouped with U+202F, and a U+00A0 before "%".

Rounding stays with the caller: ``number`` and ``percent`` take the digits the caller rounded to ("34", "33.5") and
change only the decimal sign, the grouping and the side the sign goes on, so every language rounds as English does.
``duration``, ``ago`` and ``money`` have no one English shape today and the widgets show no month, so these four serve
French, Spanish and Turkish only (ValueError otherwise); their English callers keep their own text (``english_shapes``
tells a caller which it has).

``tight=True`` (``percent``, ``percent_parts``, ``money``) drops the space between a number and its sign ("%", "$"; a
currency code keeps its U+00A0), for the boxes sized for English's figures (Chris's decision on #91: Nostromo's strip
values, Night City's strip percent, the "Cap 100 %" badge on Gates' hover card, the dashboard's money tiles); the sign
stays on its language's side.

Standard library only, and pure: nothing here reads the process locale or Windows.
"""
from __future__ import annotations

import functools
import re
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from usage_monitor import cldr
from usage_monitor.i18n import DEFAULT, PSEUDO, Locale, plural_category

_ENGLISH_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")   # Monday first, as datetime.weekday() counts
_PSEUDO_GROUP = "\u202f"
_PSEUDO_PERCENT = "\u00a0%"
_SPACES = " \u00a0\u202f"
_SYMBOL_SLOT = "\u00a4"
_DIGITS = re.compile(r"(-?)([0-9]+)(?:\.([0-9]+))?")
_FIELD = re.compile(r"'(?:[^']|'')*'|E+|H+|h+|m+|a+|.", re.S)
WIDTHS: tuple[str, ...] = ("narrow", "short")


# ── The table for a locale ────────────────────────────────────────────────────

def english_shapes(locale: Locale) -> bool:
    """True when ``locale`` writes today's English formats: English and the pseudo-locale, or a language with no
    CLDR table."""
    return _table(locale) is None


def _table(locale: Locale) -> Mapping[str, Any] | None:
    if locale.language in (DEFAULT, PSEUDO):
        return None
    return _table_for(locale.language, locale.format)


def canonical_tag(tag: str) -> str:
    """A BCP 47 tag in its canonical case: ``fr_ca`` is ``fr-CA``, ``es-419`` stays, a script is title-cased."""
    parts = [part for part in str(tag).strip().replace("_", "-").split("-") if part]
    out = parts[:1] and [parts[0].lower()]
    for part in parts[1:]:
        if len(part) == 2 and part.isalpha():
            out.append(part.upper())
        elif len(part) == 4 and part.isalpha():
            out.append(part.title())
        else:
            out.append(part.lower())
    return "-".join(out)


@functools.lru_cache(maxsize=None)
def _table_for(language: str, tag: str) -> Mapping[str, Any] | None:
    """The language's base with the region of ``tag`` over it (its first two subtags when the whole tag has no
    table, "es-ES-tradnl" as "es-ES"); the base alone for a tag CLDR has no region for, or of another language."""
    base = cldr.BASES.get(language)
    if base is None:
        return None
    parts = canonical_tag(tag).split("-")
    if parts[0] == language:
        for size in (len(parts), 2):
            region = cldr.REGIONS.get("-".join(parts[:size]))
            if region is not None:
                return _merge(base, region)
    return base


def _leaf(value: Any) -> bool:
    return not isinstance(value, dict) or all(isinstance(item, str) for item in value.values())


def _merge(base: Mapping[str, Any], region: Mapping[str, Any]) -> dict[str, Any]:
    """A region's differences over its base, level by level; a dict of strings is replaced whole (cldr_gen's rule)."""
    out = dict(base)
    for key, value in region.items():
        out[key] = value if _leaf(base[key]) else _merge(base[key], value)
    return out


# ── Numbers and percents ──────────────────────────────────────────────────────

def _grouped(whole: str, separator: str, minimum: int) -> str:
    """``whole`` in groups of three, when it has at least ``minimum`` digits before the first group."""
    if len(whole) < 3 + minimum:
        return whole
    head = len(whole) % 3 or 3
    return separator.join([whole[:head], *(whole[i:i + 3] for i in range(head, len(whole), 3))])


def _localized(text: str, decimal: str, separator: str, minimum: int, minus: str, group: bool) -> str:
    """ASCII digits ("-1234.5") in a language's signs; anything that is not plain digits is returned as given."""
    found = _DIGITS.fullmatch(text)
    if found is None:
        return text
    sign, whole, fraction = found.groups()
    body = _grouped(whole, separator, minimum) if group else whole
    if fraction is not None:
        body += decimal + fraction
    return (minus if sign else "") + body


def _table_number(text: str, table: Mapping[str, Any], group: bool = True) -> str:
    return _localized(text, table["decimal"], table["group"], table["min_grouping"], table["minus"], group)


def number(digits: str | int, locale: Locale, *, group: bool = True) -> str:
    """The caller's digits ("1234.5", 95) in the locale's signs: "1,234.5" in English, "1 234,5" (U+202F) in fr-FR,
    "1234,5" in es-ES (two digits before a group, CLDR's minimum), "1.234,5" in tr. ``group=False`` leaves out the
    group separator (today's widget numbers have none). Text that is not plain digits, such as "--" or "1e+20", is
    returned as given."""
    text = str(digits)
    table = _table(locale)
    if table is not None:
        return _table_number(text, table, group)
    separator = _PSEUDO_GROUP if locale.language == PSEUDO else ","
    return _localized(text, ".", separator, 1, "-", group)


def _tight(before: str, after: str) -> tuple[str, str]:
    return before.rstrip(_SPACES), after.lstrip(_SPACES)


def percent_parts(digits: str | int, locale: Locale, *, tight: bool = False) -> tuple[str, str, str]:
    """(before, number, after) of a percent, so a skin can draw the sign on its own side: ("", "34", "%") in English,
    ("", "34", "\\u00a0%") in French and Spanish, ("%", "34", "") in Turkish. A minus is part of ``before``. The number
    is grouped in fr, es and tr ("1 234"); English writes the digits as given, as today."""
    text = str(digits)
    table = _table(locale)
    if table is None:
        parts = ("", text, _PSEUDO_PERCENT if locale.language == PSEUDO else "%")
    else:
        found = _DIGITS.fullmatch(text)
        negative = found is not None and found.group(1) == "-"
        prefix, suffix, negative_prefix, negative_suffix = table["percent"]
        body = _table_number(text[1:] if negative else text, table)
        parts = (negative_prefix, body, negative_suffix) if negative else (prefix, body, suffix)
    if tight:
        before, after = _tight(parts[0], parts[2])
        parts = (before, parts[1], after)
    return parts


def percent(digits: str | int, locale: Locale, *, tight: bool = False) -> str:
    """The caller's digits as a percent: "34%" in English (as today), "34 %" (U+00A0) in French and Spanish, "%34" in
    Turkish; "34%" in French with ``tight``. See percent_parts."""
    return "".join(percent_parts(digits, locale, tight=tight))


def money(amount: float, currency: str | None, locale: Locale, *, tight: bool = False) -> str:
    """An amount to the cent in French, Spanish or Turkish: US dollars as the plain "$" ("5,96 $" in fr, "$5.96" in
    es-MX, "$5,96" in tr), another currency by its code where the language's pattern puts the sign, a U+00A0 between a
    code and a digit ("EUR 5.96" in es-MX). An amount with a currency takes the tag's currency decimal and group, which
    CLDR sets apart for a few tags ("5.96 $" in fr-CH); no currency gives the plain number alone. ``tight`` drops only
    the pattern's own space: a code keeps its U+00A0 ("5,96 EUR"). English keeps its callers' own text (ValueError)."""
    table = _require(locale, "money")
    text = f"{float(amount):.2f}"
    negative = text.startswith("-")
    digits = text[1:] if negative else text
    code = (currency or "").strip().upper()
    if not code:
        return (table["minus"] if negative else "") + _table_number(digits, table)
    body = _localized(digits, table["currency_decimal"], table["currency_group"], table["min_grouping"], table["minus"],
                      True)
    symbol = cldr.PLAIN_DOLLAR if code == "USD" else code
    prefix, suffix, negative_prefix, negative_suffix = table["currency"]
    before, after = (negative_prefix, negative_suffix) if negative else (prefix, suffix)
    before, after = before.replace(_SYMBOL_SLOT, symbol), after.replace(_SYMBOL_SLOT, symbol)
    if tight:
        before, after = _tight(before, after)
    if before.endswith(symbol) and _spaced(symbol[-1]) and body[:1].isdigit():
        before += cldr.CURRENCY_SPACING
    if after.startswith(symbol) and _spaced(symbol[0]) and body[-1:].isdigit():
        after = cldr.CURRENCY_SPACING + after
    return before + body + after


def _spaced(ch: str) -> bool:
    """CLDR's currencySpacing: a currency character that is not a symbol or a space takes a space beside a digit."""
    return not unicodedata.category(ch).startswith(("S", "Z"))


# ── Clocks and names ──────────────────────────────────────────────────────────

def clock(local: datetime, locale: Locale) -> str:
    """The time of ``local`` (already in local time): "4:12 PM" in English, always 12-hour, with an ASCII space (as
    today; CLDR's en-US has U+202F); CLDR's 24-hour or 12-hour pattern of the format tag otherwise, by Locale.clock:
    "16:12" or "4:12 PM" in fr-FR, "16 h 12" in fr-CA, "4:12 p.m." in es-MX, "ÖS 4:12" in Turkish (each 12-hour form
    with a U+202F)."""
    table = _table(locale)
    if table is None:
        hour = local.hour % 12 or 12
        suffix = "AM" if local.hour < 12 else "PM"
        return f"{hour}:{local.minute:02d} {suffix}"
    return _pattern(table["hm" if locale.clock == 12 else "Hm"], local, table)


def day_clock(local: datetime, locale: Locale) -> str:
    """The weekday and time of ``local``: "Sat 4:00 PM" in English (as today), CLDR's pattern otherwise: "sam. 16:00"
    in fr-FR, "sáb, 16:00" in es-ES, "Cmt 16:00" in Turkish."""
    table = _table(locale)
    if table is None:
        return f"{_ENGLISH_WEEKDAYS[local.weekday()]} {clock(local, locale)}"
    return _pattern(table["Ehm" if locale.clock == 12 else "EHm"], local, table)


def _pattern(pattern: str, local: datetime, table: Mapping[str, Any]) -> str:
    """A CLDR date pattern with the fields the clocks use: E (weekday), H and h (hour), m (minute), a (AM or PM) and
    quoted text."""
    out = []
    for field in _FIELD.findall(pattern):
        letter = field[0]
        if letter == "'":
            out.append("'" if field == "''" else field[1:-1].replace("''", "'"))
        elif letter == "E":
            out.append(table["weekdays"][local.weekday()])
        elif letter == "H":
            out.append(f"{local.hour:0{len(field)}d}")
        elif letter == "h":
            out.append(f"{local.hour % 12 or 12:0{len(field)}d}")
        elif letter == "m":
            out.append(f"{local.minute:0{len(field)}d}")
        elif letter == "a":
            out.append(table["am_pm"][0 if local.hour < 12 else 1])
        else:
            out.append(field)
    return "".join(out)


def weekday(index: int, locale: Locale) -> str:
    """The abbreviated name of a weekday, 0 for Monday (as datetime.weekday() counts): "Mon" in English (as today),
    "lun." in French, "lun" in Spanish, "Pzt" in Turkish."""
    table = _table(locale)
    return _ENGLISH_WEEKDAYS[index] if table is None else table["weekdays"][index]


def month(index: int, locale: Locale) -> str:
    """The abbreviated name of a month, 0 for January: "janv." in French, "ene" in Spanish, "Oca" in Turkish. English
    has no month in the widgets today (ValueError)."""
    return _require(locale, "month")["months"][index]


# ── Durations and ages ────────────────────────────────────────────────────────

def duration(parts: Sequence[tuple[int, str]], locale: Locale, width: str = "narrow") -> str:
    """Time as countdown_units and age_unit give it, ((2, "d"), (3, "h")), in CLDR's duration units: "2j 3h" in French,
    "3s 27d" in Turkish (3 hours 27 minutes; one hour is "1 sa"), "2dd." in es-419. ``width`` is "narrow" or "short"
    ("3 h" in Spanish); both join units with CLDR's unit-narrow list (Babel 2.18 has no whole unit-short list for
    French and Spanish). No parts give "". English keeps its callers' own text (ValueError)."""
    table = _require(locale, "duration")
    if width not in WIDTHS:
        raise ValueError(f"width must be one of {WIDTHS}, not {width!r}")
    units = table["units"][width]
    words = [_form(units[unit], count, locale).replace("{0}", number(count, locale)) for count, unit in parts]
    return _joined(words, table["unit_list"])


def ago(count: int, unit: str, locale: Locale) -> str:
    """How long ago, by age_unit's (count, unit code), in CLDR's short relative time: "il y a 12 s" (U+00A0) in French,
    "hace 12 s" in Spanish, "12 sn. önce" in Turkish. English keeps its callers' own text (ValueError)."""
    table = _require(locale, "ago")
    return _form(table["past"][unit], count, locale).replace("{0}", number(count, locale))


def _form(forms: Mapping[str, str], count: int, locale: Locale) -> str:
    return forms.get(plural_category(locale.language, count)) or forms["other"]


def _joined(words: Sequence[str], patterns: Mapping[str, str]) -> str:
    """CLDR's list patterns: "2" for two items, else "start", "middle" and "end"."""
    if len(words) < 2:
        return "".join(words)
    if len(words) == 2:
        return patterns["2"].replace("{0}", words[0]).replace("{1}", words[1])
    text = patterns["start"].replace("{0}", words[0]).replace("{1}", words[1])
    for word in words[2:-1]:
        text = patterns["middle"].replace("{0}", text).replace("{1}", word)
    return patterns["end"].replace("{0}", text).replace("{1}", words[-1])


def _require(locale: Locale, what: str) -> Mapping[str, Any]:
    table = _table(locale)
    if table is None:
        raise ValueError(f"formats.{what} serves French, Spanish and Turkish; English keeps its callers' own text")
    return table
