"""The CLDR data the formats read (usage_monitor/cldr.py, #91), checked against sources other than the table itself.

- What WebView2 wrote for the same tags (#88's spike, spike-88-results.md), with the plain dollar in place of "$US".
- Spanish's minimum grouping (1234 but 12.345), which Babel alone does not give.
- The fields the tables take from the page's data where Babel's differs (#111), and the header that names them.
- The plural categories and rules usage_monitor.i18n uses, against the samples Babel worked out.
- The shape of the tables: canonical tags, a base for every region, seven weekdays from Monday, twelve months.
- That tests/cldr_gen.py still writes the committed file and keeps the currency symbols, where Babel 2.18.0 with
  CLDR 47 is installed (skipped elsewhere: CI has no Babel, and the app never imports it).
"""
from __future__ import annotations

import ast
import dataclasses
import re
from pathlib import Path
from typing import Any

import pytest

from usage_monitor import cldr, i18n

REPO = Path(__file__).resolve().parent.parent
TARGET = REPO / "usage_monitor" / "cldr.py"


def table(tag: str) -> dict[str, Any]:
    """A tag's fields: its language's base with the region's differences over it, level by level."""
    def merge(base: dict[str, Any], region: dict[str, Any]) -> dict[str, Any]:
        out = dict(base)
        for key, value in region.items():
            leaf = not isinstance(base[key], dict) or all(isinstance(v, str) for v in base[key].values())
            out[key] = value if leaf else merge(base[key], value)
        return out
    return merge(cldr.BASES[tag.split("-")[0]], cldr.REGIONS[tag])


# ── What WebView2 wrote (#88's spike) ─────────────────────────────────────────

def test_the_tables_give_what_webview2_wrote():
    """spike-88-results.md: fr-FR 1 234 567,891 (U+202F), 42 % (U+00A0) and 1 234,50 $US; fr-CA 1 234,50 $ US (U+00A0
    only); tr %42; es-ES 1234,5 and 12.345,5; fr-FR 14:05; tr 15 Oca 2026. Each is rebuilt here from the fields it
    shows, the dollar written plain."""
    fr, fr_ca, es, tr = table("fr-FR"), table("fr-CA"), table("es-ES"), table("tr-TR")
    assert f"1{fr['group']}234{fr['group']}567{fr['decimal']}891" == "1\u202f234\u202f567,891"
    assert fr["percent"][:2] == ("", "\u00a0%")
    assert "1" + fr["group"] + "234" + fr["decimal"] + "50" + fr["currency"][1].replace("\u00a4", "$") == \
        "1\u202f234,50\u00a0$"
    assert "1" + fr_ca["group"] + "234" + fr_ca["decimal"] + "50" + fr_ca["currency"][1].replace("\u00a4", "$") == \
        "1\u00a0234,50\u00a0$"
    assert tr["percent"][0] + "42" + tr["percent"][1] == "%42"
    assert es["min_grouping"] == 2 and es["decimal"] == ","            # 1234,5: no group under five digits
    assert f"12{es['group']}345{es['decimal']}5" == "12.345,5"
    assert fr["Hm"] == "HH:mm"                                         # 14:05
    assert tr["months"][0] == "Oca"                                    # 15 Oca 2026


def test_spanish_groups_five_digits_but_not_four():
    """CLDR's minimumGroupingDigits, which Babel 2.18 does not keep: es-ES (and Spanish outside Latin America) writes
    1234 and 12.345; es-419 and the regions under it group from four digits, as WebView2 and Windows' ICU do."""
    for tag in ("es-ES", "es-EA", "es-IC", "es-GQ", "es-PH"):
        assert table(tag)["min_grouping"] == 2, tag
    for tag in ("es-419", "es-MX", "es-US", "es-AR", "es-CO"):
        assert table(tag)["min_grouping"] == 1, tag
    for tag in ("fr-FR", "fr-CA", "tr-TR"):
        assert table(tag)["min_grouping"] == 1, tag


# ── The page's data (#111) ────────────────────────────────────────────────────

# Each field tests/cldr_gen.py's PAGE_RULES takes from the page's data: (tag, field) -> (the value as ICU has it, read
# from what node's Intl writes on ICU 77.1 and 78.2; Babel 2.18.0's, which the table held before). fr-MA's
# currency_group follows its group, as ICU's does: fr-MA has no currency group of its own.
PAGE_VALUES: dict[tuple[str, str], tuple[Any, Any]] = {
    ("es-AR", "currency"): (("\u00a4\u00a0", "", "-\u00a4\u00a0", ""), ("\u00a4", "", "-\u00a4", "")),
    ("es-CO", "currency"): (("\u00a4\u00a0", "", "-\u00a4\u00a0", ""), ("\u00a4", "", "-\u00a4", "")),
    ("es-GQ", "currency"): (("\u00a4", "", "-\u00a4", ""), ("", "\u00a0\u00a4", "-", "\u00a0\u00a4")),
    ("fr-MA", "group"): (".", "\u202f"),
    ("fr-MA", "currency_group"): (".", "\u202f"),
    ("fr-MA", "am_pm"): (("a.m.", "p.m."), ("AM", "PM")),
    ("tr-CY", "hm"): ("h:mm\u202fa", "a\u202fh:mm"),
}


@pytest.mark.parametrize(("tag", "field"), sorted(PAGE_VALUES))
def test_a_field_the_page_rules_cover_is_the_pages(tag: str, field: str) -> None:
    """Where Babel's data for es-AR, es-CO, es-GQ, fr-MA and tr-CY differs from what the dashboard writes, the table
    holds the page's (the widgets follow the page, #111), not Babel's."""
    page, babel = PAGE_VALUES[(tag, field)]
    assert table(tag)[field] == page != babel


def test_the_page_rules_change_only_their_fields():
    """The rest of the six tags is Babel's: fr-MA keeps French's decimal comma, tr-CY's day clock keeps the day period
    first (E a h:mm, in ICU as in Babel), and es-PY's pattern, the same in ICU and Babel, is Babel's."""
    assert table("fr-MA")["decimal"] == table("fr-MA")["currency_decimal"] == ","
    assert table("tr-CY")["Ehm"] == table("tr-TR")["Ehm"] == "E a\u202fh:mm"
    assert table("tr-CY")["Hm"] == table("tr-TR")["Hm"]
    assert table("es-PY")["currency"] == ("\u00a4\u00a0", "", "\u00a4\u00a0-", "")
    assert table("es-419")["currency"] == ("\u00a4", "", "-\u00a4", "")
    assert table("es-ES")["currency"] == ("", "\u00a0\u00a4", "-", "\u00a0\u00a4")


def test_the_header_names_each_rule_from_the_page_with_its_source():
    """usage_monitor/cldr.py says which fields come from the page's data, and the source: an item for each rule, with
    its tags and field."""
    doc = cldr.__doc__ or ""
    _, source, listed = doc.partition("the widgets write what the dashboard writes (#111). Source:\nICU 77.1 and 78.2 "
                                      "(node), the page's data.\n")
    rules = [rule.removeprefix("- ") for rule in listed.split("\n\n")[0].split("\n- ")]
    assert source and all("(Babel " in rule for rule in rules)
    assert len(rules) == 5
    for tag, field in PAGE_VALUES:
        if field != "currency_group":
            assert [rule for rule in rules if tag in rule.split(":")[1] and f" {field} " in rule], (tag, field)


# ── Plural rules ──────────────────────────────────────────────────────────────

def test_each_language_has_the_plural_categories_its_catalog_uses():
    assert set(cldr.PLURALS) == set(i18n.LANGUAGES)
    for language in i18n.LANGUAGES:
        assert cldr.PLURALS[language]["categories"] == i18n.PLURAL_CATEGORIES[language], language


def test_the_apps_plural_rules_agree_with_cldr():
    """i18n.plural_category is the app's own copy of the rules (the dashboard has another, held to it by the webui
    tests). Babel's rules for each sample count must give the same category."""
    for language in i18n.LANGUAGES:
        samples = cldr.PLURALS[language]["samples"]
        assert len(samples) >= 15
        for count, category in samples:
            assert i18n.plural_category(language, count) == category, (language, count)
    assert {category for _count, category in cldr.PLURALS["fr"]["samples"]} == {"one", "many", "other"}


# ── The shape of the tables ───────────────────────────────────────────────────

def test_every_tag_is_canonical_and_has_a_base():
    assert set(cldr.BASES) == {"fr", "es", "tr"}
    assert {"fr-FR", "fr-CA", "fr-BE", "fr-CH", "es-ES", "es-419", "es-MX", "es-US", "es-AR", "tr-TR"} <= set(cldr.REGIONS)
    for tag in cldr.REGIONS:
        assert re.fullmatch(r"[a-z]{2}-([A-Z]{2}|[0-9]{3})", tag), tag             # fr-CA, es-419; never fr_CA
        assert tag.split("-")[0] in cldr.BASES, tag
        assert set(table(tag)) == set(cldr.BASES[tag.split("-")[0]]), tag
    for language in i18n.LANGUAGES:
        if language != i18n.DEFAULT:
            assert i18n.BASE_FORMATS[language] in cldr.REGIONS


def test_weekdays_start_on_monday_and_months_are_twelve():
    """datetime.weekday() counts from Monday; CLDR's own lists start on Sunday."""
    firsts = {"fr": "lun.", "es": "lun", "tr": "Pzt"}
    for tag in cldr.REGIONS:
        fields = table(tag)
        assert len(fields["weekdays"]) == 7 and len(set(fields["weekdays"])) == 7, tag
        assert len(fields["months"]) == 12 and len(set(fields["months"])) == 12, tag
        assert len(fields["am_pm"]) == 2, tag
    for language, first in firsts.items():
        assert cldr.BASES[language]["weekdays"][0] == first
    assert cldr.BASES["tr"]["weekdays"][5] == "Cmt" and cldr.BASES["fr"]["weekdays"][6] == "dim."


def test_every_tag_has_every_unit_and_relative_time():
    for tag in cldr.REGIONS:
        fields = table(tag)
        for width in ("narrow", "short"):
            assert set(fields["units"][width]) == {"d", "h", "m", "s"}, (tag, width)
        for when in ("past", "future"):
            assert set(fields[when]) == {"d", "h", "m", "s"}, (tag, when)
        forms = [form for width in fields["units"].values() for unit in width.values() for form in unit.items()]
        forms += [form for when in ("past", "future") for unit in fields[when].values() for form in unit.items()]
        for category, pattern in forms:
            assert category in cldr.PLURALS[tag.split("-")[0]]["categories"] and "{0}" in pattern, (tag, category)
        assert set(fields["unit_list"]) == {"2", "start", "middle", "end"}, tag


def test_the_data_is_plain_and_written_with_escapes():
    """No code to run, and no character beyond ASCII in the source: U+00A0 and U+202F show as escapes in review."""
    source = TARGET.read_text(encoding="utf-8")
    assert source.isascii()
    tree = ast.parse(source)
    assert not [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Call))]
    assert {node.names[0].name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} == {"annotations", "Any"}
    assert cldr.CLDR_VERSION == "47" and cldr.SOURCE.startswith("Babel ")


# ── The generator ─────────────────────────────────────────────────────────────

def _generator() -> Any:
    """tests/cldr_gen.py, where Babel 2.18.0 with CLDR 47 is installed; the test skips elsewhere."""
    babel = pytest.importorskip("babel")
    from babel.core import get_cldr_version

    if babel.__version__ != "2.18.0" or str(get_cldr_version()) != "47":
        pytest.skip(f"Babel {babel.__version__} (CLDR {get_cldr_version()}), not 2.18.0 (CLDR 47)")
    import cldr_gen

    return cldr_gen


def test_the_generator_writes_the_committed_file():
    """Where Babel 2.18.0 with CLDR 47 is installed (a dev PC), regenerating gives usage_monitor/cldr.py byte for byte.
    CI has no Babel and skips this; a newer Babel is a deliberate update (run tests/cldr_gen.py, review the diff)."""
    cldr_gen = _generator()
    assert cldr_gen.render() == TARGET.read_text(encoding="utf-8").replace("\r\n", "\n")


def test_the_generator_keeps_the_currency_symbols_and_stops_on_one_it_does_not_know():
    """Money takes CLDR's currencyDecimal and currencyGroup where a tag has them (fr-CH, the only one of the three
    languages in CLDR 47), else the plain ones; a number symbol the generator neither reads nor knows it can leave out
    stops the run (Gap) rather than being dropped unseen."""
    cldr_gen = _generator()
    plain = {"decimal": ",", "group": "\u202f", "minusSign": "-", "percentSign": "%", "nan": "NaN", "plusSign": "+"}
    assert cldr_gen.number_symbols("fr", plain) == {"decimal": ",", "group": "\u202f", "minus": "-",
                                                    "currency_decimal": ",", "currency_group": "\u202f"}
    swiss = cldr_gen.number_symbols("fr_CH", {**plain, "currencyDecimal": ".", "currencyGroup": "'"})
    assert (swiss["decimal"], swiss["currency_decimal"], swiss["currency_group"]) == (",", ".", "'")
    with pytest.raises(cldr_gen.Gap):
        cldr_gen.number_symbols("xx", {**plain, "currencyMinusSign": "-"})
    assert table("fr-CH")["currency_decimal"] == "." and table("fr-CH")["decimal"] == ","
    assert table("fr-FR")["currency_decimal"] == ","


def test_the_page_rules_are_the_fields_the_tables_hold():
    """Each rule of tests/cldr_gen.py's PAGE_RULES gives the value PAGE_VALUES above holds the table to, and what
    Babel 2.18.0 gives for it (a currency rule as the pattern's sides): the two lists cannot drift apart."""
    cldr_gen = _generator()
    from babel.numbers import parse_pattern

    def sides(rule: Any, value: Any) -> Any:
        return cldr_gen._sides(parse_pattern(value), "\u00a4", "\u00a4", "-") if rule.field == "currency" else value

    covered = {(cldr_gen._tag(identifier), rule.field): (sides(rule, rule.page), sides(rule, rule.babel))
               for rule in cldr_gen.PAGE_RULES for identifier in rule.tags}
    assert covered == {key: value for key, value in PAGE_VALUES.items() if key[1] != "currency_group"}
    assert cldr_gen.PAGE_SOURCE == "ICU 77.1 and 78.2 (node), the page's data"


def test_a_page_rule_stops_the_run_when_babel_gives_something_else(monkeypatch):
    """A rule was made for what Babel 2.18.0 gives: a Babel that gives anything else for the field stops the run (Gap),
    so the rule is looked at again. So does a rule for a tag Babel does not have, a field the generator never asks
    about, or a second rule for the same field of a tag. A field no rule covers is Babel's."""
    cldr_gen = _generator()
    assert cldr_gen.page_value("es_AR", "currency", "\u00a4#,##0.00") == "\u00a4\u00a0#,##0.00"
    assert cldr_gen.page_value("es_AR", "hm", "h:mm\u202fa") == "h:mm\u202fa"
    assert cldr_gen.page_value("es_MX", "currency", "\u00a4#,##0.00") == "\u00a4#,##0.00"
    with pytest.raises(cldr_gen.Gap, match="the dollar spaced"):
        cldr_gen.page_value("es_AR", "currency", "\u00a4\u00a0#,##0.00")
    with pytest.raises(cldr_gen.Gap):
        cldr_gen.page_value("tr_CY", "hm", "h:mm a")
    identifiers = ["es_AR", "es_CO", "es_GQ", "fr_MA", "tr_CY"]
    cldr_gen.check_page_rules(identifiers)
    rule = cldr_gen.PAGE_RULES[0]
    for broken in (dataclasses.replace(rule, tags=("es_XX",)), dataclasses.replace(rule, field="decimal")):
        monkeypatch.setattr(cldr_gen, "PAGE_RULES", (broken,))
        with pytest.raises(cldr_gen.Gap):
            cldr_gen.check_page_rules(identifiers)
    monkeypatch.setattr(cldr_gen, "PAGE_RULES", (rule, rule))
    with pytest.raises(cldr_gen.Gap):
        cldr_gen.check_page_rules(identifiers)
