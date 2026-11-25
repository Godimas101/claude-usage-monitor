"""Letter case by the language's rules (usage_monitor/casing.py, #91): Turkish's dotted and dotless i, never a
combining dot, the default rules everywhere else, and names that keep their own case.

The Turkish strings are the spike's (#88): WebView2's toLocaleUpperCase("tr") and toLocaleLowerCase("tr") wrote them,
so the widgets and the dashboard case alike.
"""
from __future__ import annotations

import sys

import pytest

from usage_monitor import casing, i18n
from usage_monitor.i18n import Text

COMBINING_DOT = "\u0307"


def bmp_letters() -> str:
    return "".join(chr(code) for code in range(0x10000) if not 0xD800 <= code <= 0xDFFF and chr(code).isalpha())


def test_turkish_capitals_and_lower_case():
    assert casing.lower("HAFTALIK", "tr") == "haftalık"
    assert casing.capitalize("HAFTALIK", "tr") == "Haftalık"
    assert casing.upper("Nis", "tr") == "NİS" and casing.upper("Eki", "tr") == "EKİ"
    assert casing.upper("Haiku", "tr") == "HAİKU"
    assert casing.upper("Haiku", "tr-TR") == "HAİKU"                    # a format tag names its language
    assert casing.upper("Haiku", "az") == "HAİKU"


def test_turkish_matches_webview2_both_ways():
    """spike-88-results.md: toLocaleUpperCase("tr") gives İSTANBUL ILIK, toLocaleLowerCase("tr") istanbul ılık."""
    assert casing.upper("istanbul ılık", "tr") == "İSTANBUL ILIK"
    assert casing.lower("İSTANBUL ILIK", "tr") == "istanbul ılık"
    assert casing.lower("I\u0307STANBUL", "tr") == "istanbul"            # a capital I with its dot written apart


def strings(catalog: dict) -> list[str]:
    return [str(form) for value in catalog.values() for form in (value.values() if isinstance(value, dict) else [value])]


def turkish_cldr() -> list[str]:
    """Every Turkish word the formats can write: weekdays, months, day periods, units and relative times."""
    from usage_monitor import cldr

    base = cldr.BASES["tr"]
    found = [*base["weekdays"], *base["months"], *base["am_pm"]]
    found += [form for width in base["units"].values() for unit in width.values() for form in unit.values()]
    found += [form for when in ("past", "future") for unit in base[when].values() for form in unit.values()]
    return found


def test_no_turkish_case_holds_a_combining_dot():
    """str.lower() turns İ into i and U+0307. Nothing the Turkish frames can draw may hold one, in any case and back:
    the Turkish catalog (empty until it is drafted), the pseudo-locale's text and letters, and CLDR's Turkish words."""
    english = i18n._read_catalog(i18n.CATALOG_DIR / "en.json")
    letters = "".join(str(v) for v in i18n._PSEUDO_LETTERS.values()) + i18n._PSEUDO_PAD + "iIİı"
    texts = strings(i18n._read_catalog(i18n.CATALOG_DIR / "tr.json")) + strings(i18n.pseudo_catalog(english))
    texts += turkish_cldr() + list(letters) + [letters, "İı" * 3]
    assert "Nis" in texts and "Eki" in texts and len(texts) >= 80
    for text in texts:
        for out in (casing.upper(text, "tr"), casing.lower(text, "tr"), casing.capitalize(text, "tr"),
                    casing.lower(casing.upper(text, "tr"), "tr"), casing.upper(casing.lower(text, "tr"), "tr")):
            assert COMBINING_DOT not in out, (text, out)
    assert COMBINING_DOT in "İ".lower()                                  # what the rule is there for


@pytest.mark.parametrize("lang", [None, "en", "en-US", "fr", "fr-CA", "es", "es-MX", i18n.PSEUDO])
def test_other_languages_use_the_default_rules(lang):
    """For every letter of the Basic Multilingual Plane: what str.upper() and str.lower() give, so the Turkish rule
    cannot leak, and capitalize is the alerts' text[:1].upper() + text[1:].lower()."""
    letters = bmp_letters()
    assert casing.upper(letters, lang) == letters.upper()
    assert casing.lower(letters, lang) == letters.lower()
    for word in ("istanbul", "ILIK", "İzmir", "ǆungla", "ßtraße", "Haiku"):
        assert casing.capitalize(word, lang) == word[:1].upper() + word[1:].lower()


def test_text_carries_its_language():
    """A catalog Text is cased by the catalog it came from, and comes back as one."""
    turkish, english = Text("Haiku haftalık", "tr"), Text("Haiku weekly", "en")
    assert casing.upper(turkish) == "HAİKU HAFTALIK" and casing.upper(english) == "HAIKU WEEKLY"
    assert isinstance(casing.upper(turkish), Text) and casing.upper(turkish).language == "tr"
    assert casing.lower(Text("WEEKLY", "tr")) == "weekly"                 # no i: nothing Turkish to do
    assert casing.upper("weekly") == "WEEKLY" and casing.upper("ili") == "ILI"   # plain text is English
    assert casing.upper(english, "tr") == "HAİKU WEEKLY"                  # an explicit language wins


def test_a_name_is_cased_with_the_words_only_in_capitals():
    """Chris: in Turkish capitals "Haiku" is "HAİKU". In lower and sentence case the name goes in as written: a
    Turkish lower-casing of "Claude Instant" would give "claude ınstant"."""
    assert casing.cased("{name} weekly", "upper", "tr", name="Haiku") == "HAİKU WEEKLY"
    assert casing.cased("{name} haftalık", "upper", "tr", name="Haiku") == "HAİKU HAFTALIK"
    assert casing.cased("{name} weekly", "upper", "en", name="Haiku") == "HAIKU WEEKLY"
    assert casing.cased("{name} weekly", "sentence", "tr", name="Claude Instant") == "Claude Instant weekly"
    assert casing.cased("{name} WEEKLY", "lower", "tr", name="Claude Instant") == "Claude Instant weekly"
    assert casing.cased("WEEKLY {name}", "sentence", "tr", name="Claude Instant") == "Weekly Claude Instant"
    assert casing.cased("HAFTALIK {name}", "sentence", "tr", name="claude instant") == "Haftalık claude instant"


def test_a_name_that_starts_a_sentence_gets_its_first_capital_by_the_rule():
    assert casing.cased("{name} weekly", "sentence", "tr", name="iPhone") == "İPhone weekly"
    assert casing.cased("{name} weekly", "sentence", "en", name="iPhone") == "IPhone weekly"
    assert casing.cased("{name} weekly", "sentence", "en", name="claude design") == "Claude design weekly"
    assert casing.cased("  {name}", "sentence", "en", name="opus") == "  Opus"


def test_cased_keeps_what_it_does_not_fill():
    assert casing.cased("{other} WEEKLY", "lower", "en") == "{other} weekly"
    assert casing.cased("{other} weekly", "sentence", "en") == "{other} weekly"       # the slot starts the sentence
    assert casing.cased(Text("{name} WEEKLY", "tr"), "lower", None, name="Haiku") == "Haiku weekly"
    assert casing.cased(Text("{name} weekly", "tr"), "upper", name="Haiku").language == "tr"
    assert casing.cased("{lang} {case}", "upper", "en", lang="tr", case="x") == "TR X"  # names never clash
    with pytest.raises(ValueError):
        casing.cased("{name}", "title", "en", name="x")


def test_casing_reads_no_process_state():
    """The rule is the language's, not the process's: nothing here reads the locale module."""
    assert "locale" not in vars(casing)
    assert casing.upper("i", "tr") == "İ" and casing.upper("i", "en") == "I"
    assert sys.modules["usage_monitor.casing"] is casing
