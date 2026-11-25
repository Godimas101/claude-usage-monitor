"""Writes usage_monitor/cldr.py, the CLDR data the formats (#91) read, from the Babel installed on this PC.

    python tests/cldr_gen.py            write usage_monitor/cldr.py
    python tests/cldr_gen.py --check    exit 1 when the committed file is not what this Babel gives

A dev tool, as node is for the dashboard checks: the app never imports Babel (standard library + Pillow + pywebview,
AGENTS.md), and pytest does not collect this file. tests/test_cldr.py regenerates the file and compares it only where
Babel 2.18.0 with CLDR 47 is installed; elsewhere (CI) that check is skipped. A newer Babel is a deliberate update: run
this, review the diff and rerun the tests.

The tables hold only the fields the formats read, for French, Spanish and Turkish: each language's own data, then each
CLDR region's differences from it. English is not here: its formats are today's own (#91, D2). Every string is written
with \\u escapes, so the no-break spaces (U+00A0, U+202F) show in review.

Three fields Babel 2.18 does not keep are rules here, each with its source:
- Minimum grouping digits (CLDR's minimumGroupingDigits): 2 for Spanish and its regions outside Latin America, 1 for
  es-419 and the regions under it, 1 for every other tag. So Spanish writes 1234 but 12.345. Sources: WebView2's es-ES
  (1234,5, #88's spike) and Windows' ICU 72.1 for es-ES, es-MX, es-US, es-419, es-AR, es-CO, fr-FR, fr-CA and tr-TR;
  the other Spanish regions follow the rule unchecked.
- The plain dollar: US dollars are "$" in every language (Chris, 2026-11-21, on #86). Another currency is written with
  its ISO code.
- Currency spacing (CLDR's currencySpacing): a currency code next to a digit takes a U+00A0, a sign such as "$" none.
  Source: ICU 72.1 writes es-MX "USD 5.96" against "$5.96".

And two gaps in Babel's copy of the data, filled as CLDR's own inheritance fills them:
- A short relative-time field Babel leaves empty (Turkish "day") takes the wide field, as CLDR's root aliases it.
- The unit-narrow list pattern Babel lacks (Turkish) comes from Babel's own fallback (unit-short, then unit).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import babel
from babel import Locale, localedata
from babel.core import get_cldr_version, get_global
from babel.lists import _resolve_list_style

REPO = Path(__file__).resolve().parent.parent
TARGET = REPO / "usage_monitor" / "cldr.py"
LANGUAGES = ("fr", "es", "tr")
PLURAL_LANGUAGES = ("en", "fr", "es", "tr")
UNITS = {"d": "day", "h": "hour", "m": "minute", "s": "second"}       # the view model's unit codes (render/model.py)
CATEGORIES = ("zero", "one", "two", "few", "many", "other")           # CLDR's order
PLURAL_SAMPLES = (0, 1, 2, 3, 5, 10, 11, 21, 100, 101, 1000, 1_000_000, 1_000_001, 2_000_000, 10_000_000,
                  0.5, 1.5, 2.5, 1_000_000.5)
CLOCK_PATTERNS = ("Hm", "hm", "EHm", "Ehm")
CLOCK_FIELDS = re.compile(r"'[^']*'|(E+|H+|h+|m+|a+)|([A-Za-z])")   # what formats.clock reads; any other letter fails
LATIN_AMERICA = "es_419"
MIN_GROUPING_SPANISH = 2
# Babel's latn number symbols: the ones the formats read, and the ones they have no use for (no exponent, per mille,
# plus sign or NaN is written; the clock patterns carry their own ":"). Any other key stops the run (Gap), so a symbol
# such as fr-CH's currencyDecimal is never dropped unseen.
SYMBOLS_READ = frozenset({"decimal", "group", "minusSign", "percentSign", "currencyDecimal", "currencyGroup"})
SYMBOLS_UNUSED = frozenset({"approximatelySign", "exponential", "infinity", "list", "nan", "perMille", "plusSign",
                            "superscriptingExponent", "timeSeparator"})


class Gap(Exception):
    """Babel's data lacks or reshapes a field the formats read: the generator stops rather than guess."""


def _tag(identifier: str) -> str:
    """Babel's identifier as a canonical BCP 47 tag: fr_CA is fr-CA, es_419 es-419."""
    tag = identifier.replace("_", "-")
    if not re.fullmatch(r"[a-z]{2}(-([A-Z]{2}|[0-9]{3}))?", tag):
        raise Gap(f"{identifier}: not a language-region tag")
    return tag


def _sides(pattern: Any, sign: str, symbol: str, minus: str) -> tuple[str, str, str, str]:
    """(prefix, suffix, negative prefix, negative suffix) of a number pattern, its sign written as ``symbol`` and the
    pattern's "-" (which stands for the minus sign) as ``minus``."""
    if pattern.grouping != (3, 3):
        raise Gap(f"{pattern.pattern!r}: grouping {pattern.grouping}, not groups of three")
    sides = (pattern.prefix[0], pattern.suffix[0], pattern.prefix[1], pattern.suffix[1])
    if any("'" in side for side in sides) or any("-" in side for side in sides[:2]):
        raise Gap(f"{pattern.pattern!r}: a quoted literal, or a minus in the positive pattern")
    return tuple(side.replace(sign, symbol).replace("-", minus) for side in sides)  # type: ignore[return-value]


def _min_grouping(identifier: str) -> int:
    language = identifier.split("_")[0]
    if language != "es":
        return 1
    under_latin_america = identifier == LATIN_AMERICA or get_global("parent_exceptions").get(identifier) == LATIN_AMERICA
    return 1 if under_latin_america else MIN_GROUPING_SPANISH


def _forms(patterns: Any, what: str) -> dict[str, str]:
    forms = {category: str(patterns[category]) for category in CATEGORIES if category in patterns}
    if "other" not in forms or any("{0}" not in form for form in forms.values()):
        raise Gap(f"{what}: {forms}")
    return forms


def _relative(loc: Locale, unit: str, when: str) -> dict[str, str]:
    fields = loc._data["date_fields"]
    short = fields[f"{unit}-short"]
    found = short.get(when) if when in short else None
    if not found:
        found = fields[unit].get(when)                 # CLDR root aliases the short field to the wide one
    return _forms(found or {}, f"{loc} {unit} {when}")


def _clock(loc: Locale, name: str) -> str:
    pattern = str(loc.datetime_skeletons[name])
    for match in CLOCK_FIELDS.finditer(pattern):
        if match.group(2):
            raise Gap(f"{loc} {name} {pattern!r}: field {match.group(2)!r} is not one formats.clock reads")
        if match.group(1) and match.group(1)[0] == "E" and not name.startswith("E"):
            raise Gap(f"{loc} {name} {pattern!r}: a weekday in a clock")
    return pattern


def number_symbols(identifier: str, symbols: Any) -> dict[str, str]:
    """The symbols the formats read. An amount with a currency takes CLDR's currencyDecimal and currencyGroup where
    the locale has them (fr-CH: a decimal point in money, a comma in a plain number), else the plain decimal and
    group, as ICU does."""
    unknown = sorted(set(symbols) - SYMBOLS_READ - SYMBOLS_UNUSED)
    if unknown:
        raise Gap(f"{identifier}: number symbols the generator does not know: {unknown}")
    if symbols["percentSign"] != "%" or any(not symbols[key] for key in ("decimal", "group", "minusSign")):
        raise Gap(f"{identifier}: number symbols {dict(symbols)}")
    return {
        "decimal": symbols["decimal"],
        "group": symbols["group"],
        "minus": symbols["minusSign"],
        "currency_decimal": symbols.get("currencyDecimal") or symbols["decimal"],
        "currency_group": symbols.get("currencyGroup") or symbols["group"],
    }


def table(identifier: str) -> dict[str, Any]:
    """Every field the formats read, for one Babel locale."""
    loc = Locale.parse(identifier)
    symbols = loc.number_symbols["latn"]
    if loc.decimal_formats[None].pattern != "#,##0.###":
        raise Gap(f"{identifier}: decimal pattern {loc.decimal_formats[None].pattern!r}")
    signs = number_symbols(identifier, symbols)
    days = loc.days["format"]["abbreviated"]
    months = loc.months["format"]["abbreviated"]
    periods = loc.day_periods["format"]["abbreviated"]
    unit_list = dict(_resolve_list_style(loc, "unit-narrow"))
    if set(unit_list) != {"2", "start", "middle", "end"}:
        raise Gap(f"{identifier}: unit-narrow list {unit_list}")
    units = {width: {code: _forms(loc._data["unit_patterns"][f"duration-{name}"][width], f"{identifier} {name} {width}")
                     for code, name in UNITS.items()}
             for width in ("narrow", "short")}
    return {
        **signs,
        "min_grouping": _min_grouping(identifier),
        "percent": _sides(loc.percent_formats[None], "%", symbols["percentSign"], symbols["minusSign"]),
        "currency": _sides(loc.currency_formats["standard"], "\u00a4", "\u00a4", symbols["minusSign"]),
        "weekdays": tuple(days[i] for i in range(7)),          # Babel numbers weekdays from Monday, as Python does
        "months": tuple(months[i] for i in range(1, 13)),
        "am_pm": (periods["am"], periods["pm"]),
        **{name: _clock(loc, name) for name in CLOCK_PATTERNS},
        "units": units,
        "unit_list": {key: unit_list[key] for key in ("2", "start", "middle", "end")},
        "past": {code: _relative(loc, name, "past") for code, name in UNITS.items()},
        "future": {code: _relative(loc, name, "future") for code, name in UNITS.items()},
    }


def plurals(language: str) -> dict[str, Any]:
    rule = Locale.parse(language).plural_form
    categories = tuple(category for category in CATEGORIES if category in rule.tags or category == "other")
    return {"categories": categories, "samples": tuple((count, rule(count)) for count in PLURAL_SAMPLES)}


def _leaf(value: Any) -> bool:
    """A dict of strings (plural forms, list parts) is one value: a region replaces it whole."""
    return not isinstance(value, dict) or all(isinstance(item, str) for item in value.values())


def _diff(own: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    """What ``own`` changes in ``base``, level by level down to the leaves (formats merges it back the same way)."""
    out: dict[str, Any] = {}
    for key, value in own.items():
        if value == base[key]:
            continue
        if _leaf(value):
            out[key] = value
        elif set(value) != set(base[key]):
            raise Gap(f"{key}: {sorted(value)} where the base has {sorted(base[key])}")
        else:
            out[key] = _diff(value, base[key])
    return out


def data() -> dict[str, Any]:
    identifiers = sorted(i for i in localedata.locale_identifiers() if i.split("_")[0] in LANGUAGES and "_" in i)
    bases = {language: table(language) for language in LANGUAGES}
    regions: dict[str, dict[str, Any]] = {}
    for identifier in identifiers:
        language = identifier.split("_")[0]
        if Locale.parse(identifier).plural_form.rules != Locale.parse(language).plural_form.rules:
            raise Gap(f"{identifier}: plural rules differ from {language}'s")
        regions[_tag(identifier)] = _diff(table(identifier), bases[language])
    return {"bases": bases, "regions": regions, "plurals": {language: plurals(language) for language in PLURAL_LANGUAGES}}


# ── Writing ───────────────────────────────────────────────────────────────────

WIDTH = 119


def _literal(value: Any, indent: int) -> str:
    """``value`` as Python source: one line when it fits, else one entry per line. Strings use \\u escapes."""
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=True)
    if isinstance(value, (bool, int, float)):
        return repr(value)
    pad = " " * (indent + 4)
    if isinstance(value, dict):
        items = [f"{_literal(key, 0)}: {_literal(item, indent + 4)}" for key, item in value.items()]
        flat = "{" + ", ".join(items) + "}"
        if "\n" not in flat and indent + len(flat) <= WIDTH - 2:
            return flat
        return "{\n" + "".join(f"{pad}{item},\n" for item in items) + " " * indent + "}"
    if isinstance(value, tuple):
        items = [_literal(item, indent + 4) for item in value]
        flat = "(" + ", ".join(items) + ("," if len(items) == 1 else "") + ")"
        if "\n" not in flat and indent + len(flat) <= WIDTH - 2:
            return flat
        return "(\n" + "".join(f"{pad}{item},\n" for item in items) + " " * indent + ")"
    raise TypeError(f"cannot write {type(value).__name__}")


HEADER = '''"""CLDR data for the formats (usage_monitor/formats.py, #91): numbers, percents, money, clocks, weekdays, months,
duration units and relative times for French, Spanish and Turkish, and the plural rules of the app's four languages.

Generated by tests/cldr_gen.py from {source} (CLDR {cldr}). Do not edit by hand: change the generator and run it. Plain
data, read by the formats only; English never reads it (its formats are today's own).

BASES holds each language's own CLDR data, which is also its base format tag's (fr-FR, es-ES, tr-TR). REGIONS holds every
CLDR region of the three languages, with only what differs from its language's base, level by level (a dict of strings,
such as one unit's plural forms, is replaced whole). A field:
- decimal, group, minus: the number symbols. currency_decimal, currency_group: the ones an amount with a currency
  takes (fr-CH's decimal point), the plain ones where CLDR has none. min_grouping: the digits that must come before the
  first group separator for it to be written (Spanish 2: 1234 but 12.345). percent and currency: (prefix, suffix,
  negative prefix, negative suffix) around the digits; U+00A4 in a currency side stands for the symbol.
- weekdays (Monday first) and months: abbreviated, as dates use them. am_pm: the abbreviated day periods.
- Hm, hm, EHm, Ehm: CLDR's patterns for a 24-hour and a 12-hour clock, alone and after a weekday.
- units: duration units by width ("narrow", "short"), the view model's unit code ("d", "h", "m", "s") and plural
  category. unit_list: the unit-narrow list pattern that joins them. past and future: short relative times by unit
  code and plural category.

Rules the generator adds (Babel does not keep these): {rules}
"""
from __future__ import annotations

from typing import Any

CLDR_VERSION = {cldr_literal}
SOURCE = {source_literal}
# US dollars in every language (Chris, 2026-11-21); another currency is written with its ISO code.
PLAIN_DOLLAR = "$"
# Between a currency code and a digit next to it; a sign such as "$" takes none.
CURRENCY_SPACING = "\\u00a0"

'''

RULES = ("minimum grouping digits (Spanish 2 outside Latin America, every other tag 1), the plain dollar, and the "
         "space between a currency code and a digit; a short relative time Babel leaves empty takes the wide one, and "
         "a missing unit-narrow list Babel's own fallback. Sources are in tests/cldr_gen.py.")


def _wrap(text: str, first: int) -> str:
    words, lines, line = text.split(), [], ""
    width = WIDTH - first
    for word in words:
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line, width = word, WIDTH
        else:
            line = f"{line} {word}" if line else word
    lines.append(line)
    return "\n".join(lines)


def render() -> str:
    """The text of usage_monitor/cldr.py as this Babel gives it."""
    found = data()
    source, cldr = f"Babel {babel.__version__}", str(get_cldr_version())
    rules_at = len("Rules the generator adds (Babel does not keep these): ")
    out = HEADER.format(source=source, cldr=cldr, rules=_wrap(RULES, rules_at), cldr_literal=json.dumps(cldr),
                        source_literal=json.dumps(source))
    out += "# Each language's own data.\n"
    out += f"BASES: dict[str, dict[str, Any]] = {_literal(found['bases'], 0)}\n\n"
    out += "# Every CLDR region of the three languages, by canonical tag: what differs from its language's base.\n"
    out += f"REGIONS: dict[str, dict[str, Any]] = {_literal(found['regions'], 0)}\n\n"
    out += ("# Each language's CLDR plural categories, in CLDR's order, and (count, category) samples worked out by "
            "Babel.\n")
    out += f"PLURALS: dict[str, dict[str, Any]] = {_literal(found['plurals'], 0)}\n"
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="exit 1 when usage_monitor/cldr.py differs")
    args = parser.parse_args(argv)
    text = render()
    if args.check:
        committed = TARGET.read_text(encoding="utf-8").replace("\r\n", "\n")
        same = committed == text
        print(f"{TARGET.name} {'is' if same else 'is NOT'} what {babel.__version__} gives")
        return 0 if same else 1
    TARGET.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {TARGET} ({len(found_tags(text))} region tags) from Babel {babel.__version__}, CLDR {get_cldr_version()}")
    return 0


def found_tags(text: str) -> list[str]:
    return re.findall(r'^    "([a-z]{2}-[A-Z0-9]+)": ', text, flags=re.M)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
