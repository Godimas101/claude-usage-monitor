"""The language core (#87, epic #49): detection, the format tag, the clock, plural forms, catalogs and the locale source.

Chris's rule (2026-11-21): English by default, plus French, Spanish and Turkish; the app follows the Windows display
language when it is one the app offers, and falls back to English. Everything here runs on injected values; one smoke
test reads the real Windows calls and only checks that they answer in the right shape.
"""
from __future__ import annotations

import json
import logging
import math
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

import pytest

from usage_monitor import i18n

REPO = Path(__file__).resolve().parent.parent
ALL = ("en", "fr", "es", "tr")
REAL_WINDOWS_READER = i18n.windows_reader      # taken at import, before conftest.py's fixture replaces it in each test


class FakeWindows:
    """What Windows says, and how often it was asked."""

    def __init__(self, preferred: list[str] | None = None, regional: str | None = None,
                 short_time: str | None = None) -> None:
        self.preferred = list(preferred or [])
        self.regional = regional
        self.time = short_time
        self.calls: list[str] = []

    def display_languages(self) -> list[str]:
        self.calls.append("display_languages")
        return list(self.preferred)

    def regional_format(self) -> str | None:
        self.calls.append("regional_format")
        return self.regional

    def short_time(self) -> str | None:
        self.calls.append("short_time")
        return self.time


# ── Detection ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("preferred", "expected"), [
    (["fr-CA"], "fr"),
    (["es-419"], "es"),
    (["tr-TR"], "tr"),
    (["en-GB"], "en"),
    (["de-DE", "fr-FR"], "en"),        # only the first display language counts, as for the installer
    (["ca-ES", "es-ES"], "en"),
    (["ku-Arab-TR"], "en"),
    ([], "en"),
    (["not a tag"], "en"),
])
def test_the_first_display_language_picks_when_it_is_offered(preferred: list[str], expected: str) -> None:
    assert i18n.resolve_system(preferred, ALL) == expected


def test_today_only_english_is_offered_so_windows_can_never_pick_a_half_done_catalog() -> None:
    assert i18n.OFFERED == ("en",)
    assert i18n.resolve_system(["fr-CA"]) == "en"
    assert i18n.resolve("fr", ["fr-CA"]) == "en"            # an explicit choice that is not offered reads as system


def test_walking_the_list_is_one_flag_away(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other choice on #86: the first offered language anywhere in Windows' list."""
    assert i18n.resolve_system(["de-DE", "fr-FR"], ALL, walk=True) == "fr"
    assert i18n.resolve_system(["de-DE", "nl-NL"], ALL, walk=True) == "en"
    monkeypatch.setattr(i18n, "WALK_DISPLAY_LANGUAGES", True)          # the flag, read at call time
    assert i18n.resolve_system(["de-DE", "fr-FR"], ALL) == "fr"


def test_the_offered_list_is_read_when_called(monkeypatch: pytest.MonkeyPatch) -> None:
    """The sign-off change (#107) edits OFFERED; nothing keeps the list from before."""
    monkeypatch.setattr(i18n, "OFFERED", ("en", "fr"))
    assert i18n.resolve_system(["fr-CA"]) == "fr" and i18n.resolve("system", ["fr-CA"]) == "fr"
    locale = i18n.LocaleSource(lambda: "system", windows=FakeWindows(["fr-CA"], "fr-CA", "HH:mm"), env={})()
    assert (locale.language, locale.format, locale.clock, locale.offered) == ("fr", "fr-CA", 24, ("en", "fr"))


def test_a_language_that_is_not_offered_is_reported_as_system() -> None:
    """settings.json keeps the choice, so the release that offers it picks it up; until then the page's list (which
    has no such entry) shows System default."""
    locale = i18n.build_locale("fr", FakeWindows(["es-ES"]), offered=("en", "es"))
    assert (locale.setting, locale.language) == ("system", "es")
    assert i18n.resolve("fr", ["es-ES"], ("en", "es")) == "es"
    assert i18n.build_locale("es", FakeWindows(["fr-FR"]), offered=("en", "es")).setting == "es"


@pytest.mark.parametrize(("setting", "preferred", "expected"), [
    ("system", ["fr-FR"], "fr"),
    ("es", ["fr-FR"], "es"),             # an offered explicit choice wins over Windows
    ("de", ["fr-FR"], "fr"),             # an unknown value follows Windows
    (None, ["tr-TR"], "tr"),
    (42, [], "en"),
])
def test_an_explicit_setting_wins_when_offered(setting: Any, preferred: list[str], expected: str) -> None:
    assert i18n.resolve(setting, preferred, ALL) == expected


def test_a_dev_instance_can_show_a_language_that_is_not_offered() -> None:
    dev = {"USAGE_MONITOR_DEV_INSTANCE": "1", "USAGE_MONITOR_LANG": "TR"}
    assert i18n.dev_override(dev) == "tr"
    assert i18n.dev_override({"USAGE_MONITOR_LANG": "tr"}) is None          # not a dev instance: ignored
    assert i18n.resolve("system", ["en-US"], dev_override="tr") == "tr"
    assert i18n.resolve("system", ["en-US"], dev_override="xx") == "en"      # not a catalog language
    locale = i18n.build_locale("system", FakeWindows(["en-US"]), env=dev)
    assert locale.language == "tr" and locale.system == "en" and locale.offered == ("en",)


@pytest.mark.parametrize(("tag", "expected"), [
    ("fr-CA", "fr"), ("FR", "fr"), ("es_MX", "es"), ("zh-Hant-TW", "zh"), ("", ""), ("-", ""), (None, ""), ("e", ""),
])
def test_primary_subtag(tag: Any, expected: str) -> None:
    assert i18n.primary(tag) == expected


# ── Formats and the clock ─────────────────────────────────────────────────────

@pytest.mark.parametrize(("language", "regional", "expected"), [
    ("fr", "fr-CA", "fr-CA"),
    ("fr", "en-CA", "fr-FR"),            # a regional format of another language: the language's base
    ("en", "en-GB", "en-US"),            # English keeps today's formats (#86's default)
    ("en", "en-CA", "en-US"),
    ("es", "es-MX", "es-MX"),
    ("tr", None, "tr-TR"),
    ("es", "", "es-ES"),
])
def test_the_format_tag(language: str, regional: str | None, expected: str) -> None:
    assert i18n.format_tag(language, regional) == expected


@pytest.mark.parametrize(("language", "pattern", "hours"), [
    ("en", "HH:mm", 12),                 # English keeps today's 12-hour clock
    ("fr", "HH:mm", 24),
    ("fr", "h:mm tt", 12),               # Windows' own setting, so the strip matches the taskbar clock
    ("es", "hh 'h' mm", 12),             # quoted text is not a field
    ("es", "H'h'mm", 24),
    ("tr", None, 24),                    # unknown: the language's own clock
    ("tr", "mm", 24),
    ("fr", "mm 'h'", 24),                # an h inside quotes is text, not an hour
    ("fr", "h 'H' mm tt", 12),           # and so is an H
])
def test_the_clock(language: str, pattern: str | None, hours: int) -> None:
    assert i18n.clock_hours(language, pattern) == hours


# ── Plural forms (CLDR 49) ────────────────────────────────────────────────────

@pytest.mark.parametrize(("language", "count", "category"), [
    ("en", 1, "one"), ("en", 0, "other"), ("en", 2, "other"),
    ("tr", 1, "one"), ("tr", 5, "other"),
    ("fr", 0, "one"), ("fr", 1, "one"), ("fr", 1.5, "one"), ("fr", 2, "other"), ("fr", 1_000_000, "many"),
    ("es", 1, "one"), ("es", 0, "other"), ("es", 2_000_000, "many"), ("es", 1_000_001, "other"),
    ("tr", 0, "other"), ("tr", 2, "other"), ("tr", 1.5, "other"), ("tr", 1_000_000, "other"),
    ("en", -1, "one"), ("fr", -2, "other"),                                  # the sign does not count
    ("fr", math.inf, "other"), ("en", math.nan, "other"), ("es", -math.inf, "other"),   # as Intl.PluralRules has it
])
def test_plural_categories(language: str, count: float, category: str) -> None:
    assert i18n.plural_category(language, count) == category


def test_fill_puts_named_values_in_and_leaves_unknown_placeholders() -> None:
    assert i18n.fill("Resume {n} of {total}", {"n": 2}) == "Resume 2 of {total}"
    assert i18n.fill("{a}{a}", {"a": "x"}) == "xx"
    assert i18n.fill("plain") == "plain"


# ── Catalogs ──────────────────────────────────────────────────────────────────

def write_catalogs(root: Path, **catalogs: dict[str, Any]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name, data in catalogs.items():
        (root / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return root


def test_a_missing_key_shows_english_and_says_so(tmp_path: Path) -> None:
    root = write_catalogs(tmp_path / "i18n", en={"a.b": "Session", "c": "Weekly"}, tr={"c": "Haftalık"})
    catalog = i18n.load_catalog("tr", root)
    assert catalog.lookup("c") == i18n.Found("Haftalık", "tr")
    assert catalog.lookup("a.b") == i18n.Found("Session", "en")        # the caller marks it lang="en" and cases it so
    assert catalog.lookup("nope") is None
    assert catalog.text("nope") == "nope"                               # a bug, easy to see
    assert (catalog.text("c").language, catalog.text("a.b").language, catalog.text("nope").language) == ("tr", "en", "en")


def test_plural_entries_follow_the_rules_of_the_language_they_came_from(tmp_path: Path) -> None:
    root = write_catalogs(tmp_path / "i18n",
                          en={"waiting": {"one": "{count} session", "other": "{count} sessions"}},
                          fr={"waiting": {"one": "{count} session", "many": "{count} de sessions", "other": "{count} sessions"}})
    assert i18n.load_catalog("fr", root).plural("waiting", 0) == "0 session"       # French: 0 is one
    assert i18n.load_catalog("en", root).plural("waiting", 0) == "0 sessions"
    assert i18n.load_catalog("es", root).plural("waiting", 1) == "1 session"        # English behind Spanish
    english_only = write_catalogs(tmp_path / "en-only", en={"waiting": {"one": "{count} session", "other": "{count} sessions"}})
    behind = i18n.load_catalog("fr", english_only).plural("waiting", 0)
    assert (behind, behind.language) == ("0 sessions", "en")                         # English rules for English text
    assert i18n.load_catalog("fr", root).plural("waiting", 0).language == "fr"


def test_a_broken_catalog_shows_english(tmp_path: Path) -> None:
    root = write_catalogs(tmp_path / "i18n", en={"k": "English"})
    (root / "fr.json").write_text("{not json", encoding="utf-8")
    assert i18n.load_catalog("fr", root).text("k") == "English"


@pytest.fixture
def fresh_app_catalog() -> Any:
    """app_catalog with nothing cached, before and after the test."""
    i18n.app_catalog.cache_clear()
    yield i18n.app_catalog
    i18n.app_catalog.cache_clear()


def test_the_app_catalog_reads_each_file_once(fresh_app_catalog: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """#93: the tray tooltip is worded on every blink, so the widgets' catalog is read once per language and process."""
    reads: list[str] = []
    real = i18n._read_catalog

    def counted(path: Path) -> dict[str, Any]:
        reads.append(path.name)
        return real(path)

    monkeypatch.setattr(i18n, "_read_catalog", counted)
    for _ in range(100):
        assert fresh_app_catalog("fr").text("tray.tip.lead") == "Claude usage"
        assert fresh_app_catalog("en").text("tray.tip.status.live") == "live"
    assert sorted(reads) == ["en.json", "en.json", "fr.json"]
    assert fresh_app_catalog("fr") is fresh_app_catalog("fr") and fresh_app_catalog("fr").language == "fr"


def test_a_missing_key_is_logged_once(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A key no catalog has shows as itself and is logged once per catalog, not once a second from the tooltip."""
    catalog = i18n.load_catalog("fr", write_catalogs(tmp_path / "i18n", en={"k.a": "A"}))
    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            assert catalog.text("k.gone") == "k.gone" and catalog.plural("k.none", 2) == "k.none"
        catalog.text("k.other")
    messages = [record.getMessage() for record in caplog.records]
    assert messages == ["catalog key 'k.gone' missing", "catalog plural 'k.none' missing", "catalog key 'k.other' missing"]


def test_a_dev_locale_is_the_dev_servers(tmp_path: Path) -> None:
    """dev_locale(x) is what the dev server's --lang x shows, a dev instance's USAGE_MONITOR_LANG on a Windows that says
    nothing: the language's base format and its own clock."""
    from usage_monitor.ui import devserver

    for language in i18n.DEV_LANGUAGES:
        server, settings = devserver.make_server("normal", 0, settings_dir=tmp_path / language, lang=language)
        try:
            assert i18n.dev_locale(language) == server._locale(), language
        finally:
            settings.close()
    assert [(i18n.dev_locale(x).language, i18n.dev_locale(x).format, i18n.dev_locale(x).clock)
            for x in i18n.DEV_LANGUAGES] == [("en", "en-US", 12), ("fr", "fr-FR", 24), ("es", "es-ES", 24),
                                             ("tr", "tr-TR", 24), ("pseudo", "en-US", 12)]
    with pytest.raises(ValueError, match="no such language"):
        i18n.dev_locale("de")


def test_the_shipped_catalogs_are_plain_text_and_english_holds_every_key() -> None:
    """en.json is the source of truth: no other catalog has a key it lacks. Strings are plain text (no HTML, no
    entities), NFC, with named placeholders only."""
    root = i18n.CATALOG_DIR
    english = json.loads((root / "en.json").read_text(encoding="utf-8"))
    assert sorted(p.stem for p in root.glob("*.json")) == sorted(i18n.LANGUAGES)
    for language in i18n.LANGUAGES:
        data = json.loads((root / f"{language}.json").read_text(encoding="utf-8"))
        keys = [k for k in data if not k.startswith("_")]
        assert set(keys) <= set(english), (language, set(keys) - set(english))
        for key in keys:
            assert re.fullmatch(r"[a-z0-9]+(?:[._][a-z0-9]+)+", key), key
            texts = data[key].values() if isinstance(data[key], dict) else [data[key]]
            for text in texts:
                assert isinstance(text, str), (language, key)
                # Plain text: no tag and no entity, though "<" and "&" as themselves are fine ("A & B", "< 1 %").
                assert not re.search(r"<[A-Za-z/!?]|&(?:[A-Za-z][A-Za-z0-9]*|#[0-9]+|#[xX][0-9A-Fa-f]+);", text), (language, key)
                assert unicodedata.normalize("NFC", text) == text, (language, key)
                assert all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) for name in re.findall(r"\{([^}]*)\}", text))


def test_each_catalog_key_is_written_once_and_none_starts_another() -> None:
    """#98's critic 6: a key is one text. No catalog names a key twice (json keeps the last silently), and no key of en.json
    is the start of another (``a.b`` and ``a.b.c`` never both), so a key cannot be at once a text and a group of texts, a
    label and its value (limits.fact.label.rate, limits.fact.value.rate)."""
    def pairs_once(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        keys = [key for key, _value in pairs]
        twice = sorted({key for key in keys if keys.count(key) > 1})
        assert not twice, twice
        return dict(pairs)

    for language in i18n.LANGUAGES:
        json.loads((i18n.CATALOG_DIR / f"{language}.json").read_text(encoding="utf-8"), object_pairs_hook=pairs_once)
    english = [key for key in json.loads((i18n.CATALOG_DIR / "en.json").read_text(encoding="utf-8")) if not key.startswith("_")]
    starts = {key[:i] for key in english for i, ch in enumerate(key) if ch == "."}
    assert not starts & set(english), sorted(starts & set(english))


# ── The pseudo-locale (#89) ───────────────────────────────────────────────────

def test_the_pseudo_locale_shows_left_over_english_missing_glyphs_and_cut_ends() -> None:
    assert i18n.pseudo_text("Resume {n} of {total} sessions") == "[Réşümé {n} öf {total} şéşşıöñş ıİğşéñıİ]"
    text = i18n.pseudo_text("The dashboard needs JavaScript.")
    assert text.startswith("[") and text.endswith("]")                     # a cut end shows
    assert len(text) >= 1.4 * len("The dashboard needs JavaScript.")      # about 40% longer
    assert set("İığşéñ") <= set(text)                                     # the letters some faces lack
    assert unicodedata.normalize("NFC", text) == text
    assert i18n.pseudo_text("") == "[]"


def test_the_pseudo_catalog_is_made_from_english_with_its_plural_forms(tmp_path: Path) -> None:
    root = write_catalogs(tmp_path / "i18n", en={"k": "Key {x}", "p": {"one": "{count} item", "other": "{count} items"}})
    catalog = i18n.load_catalog(i18n.PSEUDO, root)
    assert catalog.language == "pseudo" and catalog.lookup("k") == i18n.Found(i18n.pseudo_text("Key {x}"), "pseudo")
    assert catalog.plural("p", 2) == "[2 ıtémş ıİğ]"                     # the placeholder filled after the change
    assert catalog.plural("p", 1).language == "pseudo"
    payload = json.loads(i18n.page_script(i18n.PSEUDO, root)[len("window.MonitorI18n = "):-2].replace("<\\/", "</"))
    assert payload["language"] == "pseudo" and payload["strings"] == i18n.pseudo_catalog(payload["fallback"])


def test_only_a_dev_instance_can_show_the_pseudo_locale() -> None:
    assert i18n.PSEUDO not in i18n.SETTING_VALUES and i18n.PSEUDO not in i18n.OFFERED
    assert i18n.resolve("pseudo", ["en-US"], ALL) == "en"                    # never a setting
    dev = {i18n.DEV_INSTANCE_ENV: "1", i18n.DEV_LANG_ENV: "pseudo"}
    locale = i18n.build_locale("system", FakeWindows(["fr-FR"], "fr-FR", "HH:mm"), env=dev, offered=ALL)
    assert (locale.language, locale.format, locale.clock, locale.setting) == ("pseudo", "en-US", 12, "system")
    assert i18n.build_locale("system", FakeWindows(["en-US"]), env={i18n.DEV_LANG_ENV: "pseudo"}).language == "en"


# ── The catalog check (#89) ───────────────────────────────────────────────────

def test_the_shipped_catalogs_pass_the_check() -> None:
    english = set(json.loads((i18n.CATALOG_DIR / "en.json").read_text(encoding="utf-8"))) - {"_comment"}
    for language in i18n.LANGUAGES:
        report = i18n.check_catalog(language)
        assert report.errors == () and not report.failed(), (language, report.errors)
        assert set(report.missing) <= english
    assert i18n.check_catalog("en").missing == ()


def test_the_check_finds_each_kind_of_fault(tmp_path: Path) -> None:
    root = write_catalogs(
        tmp_path / "i18n",
        en={"a.text": "Hello {name}", "a.count": {"one": "{count} item", "other": "{count} items"},
            "a.gone": "Gone", "export.csv.filter": "CSV file (*.csv)"},
        fr={"a.text": "Bonjour {nom}", "a.count": {"one": "{count} élément", "other": "{count} éléments"},
            "b.extra": "x", "export.csv.filter": "Fichier d’export CSV (*.csv)", "a.gone": "Parti <b>"},
        es={"a.text": "Hola {name}", "a.count": {"one": "{count} elemento", "many": "{count} de elementos",
                                                  "other": "{count} elementos"},
            "a.gone": "Ejecute claude update", "export.csv.filter": "Archivo CSV (*.csv)"},
        tr={"a.text": "Merhaba {name}", "a.gone": "Kafé", "export.csv.filter": "CSV dosyası (*.csv)"},
    )
    fr = i18n.check_catalog("fr", root)
    assert "b.extra: not in en.json" in fr.errors
    assert any(e.startswith("a.text: placeholders ['nom']") for e in fr.errors)
    assert any(e.startswith("a.count: plural forms ['one', 'other'], not ['many', 'one', 'other']") for e in fr.errors)
    assert any(e.startswith("export.csv.filter: not a Save-dialog filter") for e in fr.errors)
    assert any(e.startswith("a.gone: holds a tag") for e in fr.errors)
    es = i18n.check_catalog("es", root)
    assert es.errors == ("a.gone: holds a command (commands are placeholders)",)
    tr = i18n.check_catalog("tr", root)
    assert tr.errors == ("a.gone: not NFC (the skins draw one character at a time)",) and tr.missing == ("a.count",)
    assert tr.failed()                                                    # an error fails it, offered or not
    clean = i18n.CatalogReport("tr", errors=(), missing=tr.missing)
    assert not clean.failed() and clean.failed(offered=("en", "tr"))      # a missing key fails only an offered one


def test_a_catalog_the_check_cannot_read_fails_it(tmp_path: Path) -> None:
    """The app shows English for a catalog it cannot read; the check must not pass it. A hand edit that breaks
    en.json fails every language, since none has anything to be held to."""
    root = write_catalogs(tmp_path / "i18n", fr={"a.b": "Clé"}, es=[1, 2])
    (root / "en.json").write_text('{"a.b": "Key",, "c.d": "x"}', encoding="utf-8")
    for language in ("en", "fr"):
        report = i18n.check_catalog(language, root)
        assert report.failed() and report.errors[0].startswith("en.json: unreadable (JSONDecodeError"), language
    assert "es.json: not a JSON object" in i18n.check_catalog("es", root).errors
    assert "tr.json: missing" in i18n.check_catalog("tr", root).errors
    (root / "en.json").write_text('{"a.b": "Key", "c.d": "One", "c.d": "Two"}', encoding="utf-8")
    assert i18n.check_catalog("en", root).errors == ("c.d: given twice in en.json",)


def test_braces_commands_and_keys_are_held_to_their_rules(tmp_path: Path) -> None:
    held = {"stray.open": "Total {", "stray.close": "Total {n} }", "stray.both": "{{n}}",
            "cmd.capital": "Run Claude update now", "cmd.end": "Then type claude.", "cmd.slash": "Run /mcp to see it",
            "settings_language": "Language"}
    free = {"ok.name": "Claude Usage Monitor and Claude Code", "ok.host": "See claude.ai for help",
            "ok.folder": "The claude-usage-monitor folder", "ok.path": r"Saved to C:\claude\usage.db",
            "ok.server": "The x/mcp-server tool", "ok.count": "{n} of {total}", "settings.reduce_motion.label": "Motion"}
    root = write_catalogs(tmp_path / "i18n", en={**held, **free})
    errors = i18n.check_catalog("en", root).errors
    assert sorted({e.split(":")[0] for e in errors}) == sorted(held), errors
    assert "settings_language: not a dotted name" in errors
    assert "stray.open: a brace that opens or closes no placeholder (placeholders are {name})" in errors
    assert "cmd.capital: holds a command (commands are placeholders)" in errors


def test_the_save_dialog_labels_the_app_passes_pass_pywebviews_rule() -> None:
    """The labels are literals in dashboard.py today; the catalog rule covers them once they move to keys ending
    in ".filter"."""
    source = (Path(i18n.__file__).parent / "ui" / "dashboard.py").read_text(encoding="utf-8")
    labels = re.findall(r'"([^"]*\(\*\.[^"]*\))"', source)
    assert {"CSV file (*.csv)", "JSON file (*.json)", "All files (*.*)"} <= set(labels)
    assert [label for label in labels if not i18n.FILE_FILTER.match(label)] == []


def test_the_filter_rule_is_pywebviews_own() -> None:
    """The check holds Save-dialog labels to pywebview's rule; this holds the rule to pywebview."""
    util = pytest.importorskip("webview.util")
    for label in ("Fichier CSV (*.csv)", "Fichier d’export CSV (*.csv)", "All files (*.*)", "Données JSON (*.json)",
                  "CSV (*.csv;*.txt)", "CSV file (csv)", "Archivo CSV (*.csv) ", "Türkçe dosya (*.json)"):
        try:
            util.parse_file_type(label)
            takes = True
        except ValueError:
            takes = False
        assert bool(i18n.FILE_FILTER.match(label)) is takes, label


def test_the_check_runs_from_the_command_line(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    assert i18n.main([]) == 0
    out = capsys.readouterr().out
    assert "en (offered): 0 errors, 0 keys missing" in out and "fr (not offered): 0 errors" in out
    root = write_catalogs(tmp_path / "i18n", en={"a.b": "Key"}, fr={"a.b": "Clé <b>"}, es={}, tr={})
    assert i18n.main(["--dir", str(root)]) == 1                           # an error in a language not offered fails
    assert "  error: a.b: holds a tag or an entity (strings are plain text)" in capsys.readouterr().out
    with pytest.raises(SystemExit) as stopped:
        i18n.main(["--help"])
    assert stopped.value.code == 0 and "--dir" in capsys.readouterr().out


def test_the_page_script_holds_the_language_and_english_behind_it(tmp_path: Path) -> None:
    root = write_catalogs(tmp_path / "i18n", en={"k": "Key", "x": "</script><b>"}, fr={"k": "Clé"})
    script = i18n.page_script("fr", root)
    assert script.startswith("window.MonitorI18n = ") and script.endswith(";\n")
    payload = json.loads(script[len("window.MonitorI18n = "):-2].replace("<\\/", "</"))
    assert payload == {"language": "fr", "strings": {"k": "Clé"}, "fallback": {"k": "Key", "x": "</script><b>"}}
    assert "</script>" not in script                                      # it can never close the script early
    english = json.loads(i18n.page_script("en", root)[len("window.MonitorI18n = "):-2].replace("<\\/", "</"))
    assert english["strings"] == {} and english["fallback"]["k"] == "Key"


# ── The locale source ─────────────────────────────────────────────────────────

def test_the_locale_reads_the_display_languages_once_and_the_regional_format_on_refresh() -> None:
    windows = FakeWindows(["fr-CA", "en-US"], "fr-CA", "HH:mm")
    source = i18n.LocaleSource(lambda: "system", windows=windows, env={}, offered=ALL)
    assert source() == i18n.Locale(setting="system", language="fr", system="fr", format="fr-CA", clock=24, offered=ALL)
    windows.regional, windows.time = "en-CA", "h:mm tt"
    assert source().format == "fr-CA"                                     # not until Windows says "intl"
    source.refresh()
    assert source().format == "fr-FR" and source().clock == 12
    assert windows.calls.count("display_languages") == 1                  # a new one signs the user out


def test_the_setting_is_read_each_time_and_system_is_still_named() -> None:
    chosen = {"value": "system"}
    source = i18n.LocaleSource(lambda: chosen["value"], windows=FakeWindows(["tr-TR"]), env={}, offered=ALL)
    assert source().language == "tr"
    chosen["value"] = "es"
    locale = source()
    assert locale.language == "es" and locale.system == "tr" and locale.setting == "es"
    assert locale.as_json() == {"setting": "es", "language": "es", "system": "tr", "format": "es-ES", "clock": 24,
                                "offered": list(ALL)}
    chosen["value"] = "gibberish"
    assert source().setting == "system"


def test_english_is_the_locale_with_no_windows_at_all() -> None:
    assert i18n.LocaleSource(lambda: "system", windows=i18n.NoWindows(), env={})() == i18n.ENGLISH


def test_only_the_first_display_language_counts_for_the_source_too() -> None:
    assert i18n.LocaleSource(lambda: "system", windows=FakeWindows(["de-DE", "fr-FR"]), env={}, offered=ALL)().language == "en"


def test_windows_is_read_on_first_use_not_when_the_source_is_made() -> None:
    windows = FakeWindows(["fr-CA"], "fr-CA", "HH:mm")
    source = i18n.LocaleSource(lambda: "system", windows=windows, env={}, offered=ALL)
    assert windows.calls == []                                            # the app's log is not set up yet then
    assert source().language == "fr" and windows.calls.count("display_languages") == 1
    source.refresh()
    source()
    assert windows.calls.count("display_languages") == 1


class BrokenWindows:
    """A reader that raises what kernel32 never should."""

    def display_languages(self) -> list[str]:
        raise RuntimeError("display languages")

    def regional_format(self) -> str | None:
        raise KeyError("regional format")

    def short_time(self) -> str | None:
        raise TypeError("short time")


def test_a_reader_that_raises_reads_as_english(caplog: pytest.LogCaptureFixture) -> None:
    source = i18n.LocaleSource(lambda: "system", windows=BrokenWindows(), env={}, offered=ALL)
    with caplog.at_level(logging.WARNING):
        assert source() == i18n.Locale(setting="system", language="en", system="en", format="en-US", clock=12,
                                       offered=ALL)
        source.refresh()
    assert sum("language reader failed" in r.getMessage() for r in caplog.records) == 5


@pytest.mark.skipif(sys.platform != "win32", reason="Windows calls")
def test_a_failing_windows_call_reads_as_nothing_and_warns_once(caplog: pytest.LogCaptureFixture) -> None:
    reader = i18n.Win32Language()

    def refuse(*_args: Any) -> Any:
        raise OSError("refused")

    reader._preferred = refuse
    reader._locale_name = refuse
    reader._locale_info = refuse
    with caplog.at_level(logging.WARNING):
        for _ in range(2):
            assert reader.display_languages() == []
            assert reader.regional_format() is None
            assert reader.short_time() is None
    assert [r.getMessage().split(" unreadable")[0] for r in caplog.records] == [
        "Windows display languages", "Windows regional format", "Windows short time"]     # once each


@pytest.mark.skipif(sys.platform != "win32", reason="Windows calls")
def test_a_windows_call_that_says_no_reads_as_nothing_and_warns_once(caplog: pytest.LogCaptureFixture) -> None:
    """Win32 reports most failures by returning FALSE or 0, not by raising: those read as nothing too, and are logged."""
    reader = i18n.Win32Language()
    reader._preferred = lambda *_args: 0
    reader._default_ui = lambda: 0
    reader._locale_name = lambda *_args: 0
    reader._locale_info = lambda *_args: 0
    with caplog.at_level(logging.WARNING):
        for _ in range(2):
            assert reader.display_languages() == []
            assert reader.regional_format() is None
            assert reader.short_time() is None
    assert [r.getMessage().split(" unreadable")[0] for r in caplog.records] == [
        "Windows display languages", "Windows regional format", "Windows short time"]     # once each


@pytest.mark.skipif(sys.platform != "win32", reason="Windows calls")
def test_without_a_preferred_list_the_default_ui_language_counts() -> None:
    """GetUserPreferredUILanguages saying no falls back to GetUserDefaultUILanguage, named by LCIDToLocaleName."""
    reader = i18n.Win32Language()
    reader._preferred = lambda *_args: 0
    reader._default_ui = lambda: 0x040C                                       # fr-FR

    def name(lcid: int, buffer: Any, size: int, flags: int) -> int:
        assert (lcid, size, flags) == (0x040C, i18n.Win32Language.LOCALE_NAME_MAX_LENGTH, 0)
        buffer.value = "fr-FR"
        return len("fr-FR") + 1

    reader._lcid_name = name
    assert reader.display_languages() == ["fr-FR"]
    reader._lcid_name = lambda *_args: 0                                      # no name for it: nothing known
    assert reader.display_languages() == []


def test_off_windows_the_reader_knows_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(i18n.sys, "platform", "linux")
    assert isinstance(REAL_WINDOWS_READER(), i18n.NoWindows)


def test_a_reader_that_cannot_bind_kernel32_knows_nothing_and_says_so(monkeypatch: pytest.MonkeyPatch,
                                                                      caplog: pytest.LogCaptureFixture) -> None:
    def unbound() -> Any:
        raise OSError("no kernel32")

    monkeypatch.setattr(i18n.sys, "platform", "win32")
    monkeypatch.setattr(i18n, "Win32Language", unbound)
    with caplog.at_level(logging.WARNING):
        assert isinstance(REAL_WINDOWS_READER(), i18n.NoWindows)
    assert any("Windows language calls unavailable (OSError)" in r.getMessage() for r in caplog.records)


def test_the_apps_reader_is_made_on_first_use(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not when the app is built, before run() sets up the log, so a reader that cannot bind kernel32 is logged."""
    made: list[str] = []

    def reader() -> Any:
        made.append("reader")
        return FakeWindows(["fr-CA"], "fr-CA", "HH:mm")

    monkeypatch.setattr(i18n, "windows_reader", reader)
    lazy = i18n.LazyWindows()
    assert made == []
    assert (lazy.display_languages(), lazy.regional_format(), lazy.short_time()) == (["fr-CA"], "fr-CA", "HH:mm")
    assert made == ["reader"]                                                 # once


@pytest.mark.skipif(sys.platform != "win32", reason="Windows calls")
def test_the_real_windows_calls_answer_in_shape() -> None:
    """Read-only calls of the user's own settings; only their shape is checked. Windows always has a display
    language, a regional format and a short-time pattern, so each call answers. The reader is built here: the test
    run's own (conftest.py) knows nothing."""
    reader = i18n.Win32Language()
    languages = reader.display_languages()
    assert languages and all(isinstance(tag, str) and i18n.primary(tag) for tag in languages)
    regional = reader.regional_format()
    assert regional is not None and i18n.primary(regional)
    short_time = reader.short_time()
    assert isinstance(short_time, str) and short_time


# ── Rendering never reads Windows ─────────────────────────────────────────────

def test_rendering_never_reads_the_windows_language(monkeypatch: pytest.MonkeyPatch) -> None:
    """The widgets, the golden frames and the frame dump draw in the language they are given, never in the one this PC
    is set to: a French Windows must not change a single English frame. The model and the frames are built under the
    trap in English and in two other locales (Turkish on a 12-hour clock, fr-CA on a 24-hour one), so the formats'
    CLDR branch (clocks, day clocks, ages, countdowns, percents) runs under it too; and the formats, casing and CLDR
    modules the render path calls are read with the render folder."""
    asked: list[str] = []

    def told(name: str) -> Any:
        def call(*_args: Any, **_kwargs: Any) -> Any:
            asked.append(name)
            raise AssertionError(f"rendering asked Windows: {name}")
        return call

    monkeypatch.setattr(i18n, "windows_reader", told("windows_reader"))
    for name in ("display_languages", "regional_format", "short_time"):
        monkeypatch.setattr(i18n.Win32Language, name, told(name))
    from usage_monitor import formats
    from usage_monitor.render import dump
    from usage_monitor.render.skins import get_skin
    tables: list[tuple[str, str]] = []
    real_table_for = formats._table_for

    def table_for(language: str, tag: str) -> Any:
        tables.append((language, tag))
        return real_table_for(language, tag)

    monkeypatch.setattr(formats, "_table_for", table_for)
    turkish = i18n.Locale(setting="tr", language="tr", system="en", format="tr-TR", clock=12, offered=("en", "tr"))
    french = i18n.Locale(setting="fr", language="fr", system="en", format="fr-CA", clock=24, offered=("en", "fr"))
    themes = dump.load_theme_objects()
    draws = [("normal2", None)] + [(state, locale) for locale in (turkish, french)
                                   for state in ("normal2", "guard_hold_session", "guard_hold_weekly")]
    for theme_id, theme in themes.items():
        skin = get_skin(theme_id)
        for state, locale in draws:
            model = dump.sample_model(state) if locale is None else dump.sample_model(state, locale=locale)
            variant = theme.variant(None)
            skin.render_floating(model, theme, variant, 1.0)
            skin.render_taskbar(model, theme, variant, 40, 1.0)
            skin.render_tooltip(model, theme, variant, 1.0)
            skin.render_tray_icon(model, theme, variant, 32)
    assert asked == []
    assert {tag for _language, tag in tables} == {"tr-TR", "fr-CA"}           # the CLDR branch ran under the trap
    sources = [path for path in (REPO / "usage_monitor" / "render").rglob("*.py")] + [REPO / "tests" / "frame_digests.py"]
    sources += [REPO / "usage_monitor" / name for name in ("formats.py", "casing.py", "cldr.py")]
    names = ("windows_reader", "Win32Language", "LocaleSource", "locale_provider", "GetUserPreferredUILanguages",
             "GetUserDefaultUILanguage", "GetUserDefaultLocaleName", "GetLocaleInfoEx", "getlocale", "getdefaultlocale",
             "setlocale", "DEV_LANG_ENV", "USAGE_MONITOR_LANG", "LCIDToLocaleName", "build_locale", "LazyWindows")
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for name in names:
            assert name not in text, (path.name, name)


def test_a_french_windows_changes_no_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same share of the golden frames, drawn plain and then on a French Windows (an fr-CA regional format and a
    24-hour clock) with French offered and a dev instance asking for French: every digest is the same. The widgets'
    language comes in as an argument (#91: the locale of build_model, dump.sample_model and frame_digests.digests,
    English by default), never from Windows."""
    import frame_digests

    plain = frame_digests.digests(part=0, parts=8)
    monkeypatch.setattr(i18n, "OFFERED", ALL)
    monkeypatch.setattr(i18n, "windows_reader", lambda: FakeWindows(["fr-FR"], "fr-CA", "HH:mm"))
    monkeypatch.setenv(i18n.DEV_INSTANCE_ENV, "1")
    monkeypatch.setenv(i18n.DEV_LANG_ENV, "fr")
    assert plain and frame_digests.digests(part=0, parts=8) == plain
