"""The coverage test (#92): every character the widgets can draw has a glyph with an outline in the face that draws it.

Offline, and standard library only: the character maps come from usage_monitor/render/glyphs.py and the outlines
from tests/font_tables.py. Pillow is used once, as an independent check of the character-map reader (FreeType).

The corpus is every string of every catalog and of the pseudo-locale, in the cases the skins draw them; the CLDR
formats the languages use (format_samples: what usage_monitor/formats.py writes for every format tag, on both clocks);
a floor of the French, Spanish and Turkish letters (FLOOR, until #93's drafts fill the catalogs); printable ASCII; and
the skins' own non-ASCII text.
The chains are the skins' own tables, for every shipped theme, read from the skin that draws it as the widgets pick
it (skins.get_skin): each face the skin draws with, and its fallback faces (fonts.<role>.fallback in the theme's
JSON), chosen per character as the engine chooses them. A Windows face this machine lacks is skipped.

A new face (S3, O3) joins through its theme JSON and its skin's _FACE_ROLES, and must pass here before it ships. A new
theme or skin is found without an edit here; one whose faces this test cannot read fails it.
"""
from __future__ import annotations

import re
import sys
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from PIL import Image, ImageDraw, ImageFont

import font_tables
from usage_monitor import casing, cldr, formats, i18n, themes
from usage_monitor.render import engine, glyphs, skins
from usage_monitor.render.skins import gates, night_city, nostromo

FONTS = engine.bundled_font_root()
BUNDLED = tuple(sorted(FONTS.glob("*/*.ttf")))
WEBUI = FONTS.parent
ICON_FILES = ("SegoeIcons.ttf", "segmdl2.ttf")              # Gates' icon face, and the same codepoints on Windows 10
ICONS = gates.ICON_GLYPH + gates.SHIELD_GLYPH
NOT_A_CHARACTER = "\U0010fffd"                            # mapped by no font: what FreeType draws for a missing one

ASCII = "".join(chr(code) for code in range(0x20, 0x7F))
SKIN_TEXT = "·…"                                # the skins' own: Gates' middle dot, the ellipsis
# The letters each language needs, until its catalog holds them (#93 to #96). French and Spanish as plan-92 1.8
# lists them, Turkish as its alphabet has them beyond ASCII. Capitals are added by the language's rule.
FLOOR: dict[str, str] = {
    "fr": "àâæçéèêëîïôœùûüÿ«»",
    "es": "áéíñóúü¿¡",
    "tr": "çğıİöşü",
}


# ── Seams: what the skins and the engine do with a character ──────────────────

def drawn(text: str) -> str:
    """``text`` as the run builders draw it (U+202F becomes U+00A0)."""
    return engine.drawn(text)


def chosen(primary: str, donors: tuple[Any, ...], ch: str) -> int:
    """The face the engine draws ``ch`` from: 0 the primary, i + 1 donors[i]."""
    return engine.face_index(primary, donors, ch)


def skin_donors(theme: Any, module: Any, faces: dict[str, Path]) -> dict[str, tuple[Any, ...]]:
    """A Canvas skin's fallback faces, resolved fresh (not from the skin's cache)."""
    return skins.face_donors(theme, module._FACE_ROLES, faces)


def typeface_donors(typeface: Any) -> tuple[Any, ...]:
    return typeface.donors


# ── The corpus ────────────────────────────────────────────────────────────────

def upper(text: str, language: str) -> str:
    """Capitals by the language's rule (casing): Turkish i to İ."""
    return casing.upper(text, language)


def lower(text: str, language: str) -> str:
    """Lower case by the language's rule (casing): Turkish I to ı, and İ to i (never i with a combining dot)."""
    return casing.lower(text, language)


def _locales() -> list[i18n.Locale]:
    """English, the pseudo-locale, and every format tag of French, Spanish and Turkish (each CLDR region and the
    language's base) on a 12-hour and a 24-hour clock, as a dev instance on such a Windows gets them."""
    english = i18n.ENGLISH
    pseudo = i18n.Locale(setting="system", language=i18n.PSEUDO, system="en", format="en-US", clock=12,
                         offered=english.offered)
    tags = sorted({*cldr.REGIONS, *(i18n.BASE_FORMATS[language] for language in cldr.BASES)})
    return [english, pseudo] + [i18n.Locale(setting="system", language=i18n.primary(tag), system="en", format=tag,
                                            clock=clock, offered=english.offered)
                                for tag in tags for clock in (12, 24)]


def format_samples() -> Iterable[tuple[str, str, str]]:
    """(language, source, text): what usage_monitor/formats.py writes in each of _locales(): numbers, percents,
    weekdays, and clocks and day clocks on every weekday either side of midnight and noon; and, outside English and
    the pseudo-locale, months, money, durations in both widths and ages, at counts of every plural category."""
    monday = datetime(2026, 7, 13)
    counts = (0, 1, 2, 5, 21, 100, 1000000)
    for locale in _locales():
        source = f"formats, {locale.format} {locale.clock}-hour"
        texts = [formats.number("-1234567.5", locale), formats.percent("-12.5", locale),
                 formats.percent("100", locale, tight=True)]
        texts += [formats.weekday(day, locale) for day in range(7)]
        for day in range(7):
            for hour in (0, 4, 11, 12, 16, 23):
                moment = monday + timedelta(days=day, hours=hour, minutes=59)
                texts += [formats.clock(moment, locale), formats.day_clock(moment, locale)]
        if not formats.english_shapes(locale):
            texts += [formats.month(month, locale) for month in range(12)]
            texts += [formats.money(amount, currency, locale) for amount in (-1234.5, 5.96)
                      for currency in ("USD", "EUR", None)]
            for unit in ("s", "m", "h", "d"):
                for count in counts:
                    texts += [formats.duration(((count, unit),), locale, width) for width in formats.WIDTHS]
                    texts.append(formats.ago(count, unit, locale))
        for text in texts:
            yield locale.language, source, text


def _strings(catalog: dict[str, Any]) -> Iterable[tuple[str, str]]:
    """(key, text) for every string of a catalog, each plural form included, placeholders removed."""
    for key, value in catalog.items():
        forms = value.values() if isinstance(value, dict) else (value,)
        for form in forms:
            yield key, i18n._PLACEHOLDER.sub("", str(form))


def corpus() -> dict[str, str]:
    """Every character the widgets may draw -> the first source that holds it."""
    found: dict[str, str] = {}

    def add(text: str, source: str) -> None:
        for ch in text:
            found.setdefault(ch, source)

    add(ASCII, "printable ASCII")
    add(SKIN_TEXT, "the skins' own text")
    for language in i18n.LANGUAGES:
        for key, text in _strings(i18n._read_catalog(i18n.CATALOG_DIR / f"{language}.json")):
            for form in (text, upper(text, language), lower(text, language)):
                add(form, f"{language}.json {key}")
    english = i18n._read_catalog(i18n.CATALOG_DIR / f"{i18n.DEFAULT}.json")
    for key, text in _strings(i18n.pseudo_catalog(english)):
        add(text + text.upper(), f"pseudo {key}")
    letters = "".join(str(value) for value in i18n._PSEUDO_LETTERS.values()) + i18n._PSEUDO_PAD + "[]"
    add(letters + letters.upper(), "the pseudo-locale's letters")
    for language, source, text in format_samples():
        add(text + upper(text, language) + lower(text, language), source)
    for language, text in FLOOR.items():
        add(text + upper(text, language) + lower(text, language), f"the {language} floor")
    return found


CORPUS = corpus()


def _label(ch: str) -> str:
    return f"{ch!r} U+{ord(ch):04X} ({CORPUS.get(ch, 'extra')})"


# ── The faces and the chains ──────────────────────────────────────────────────

def _windows_names() -> tuple[str, ...]:
    """Every Windows font file the skins may draw text with: the theme families' files and Gates' static fallbacks."""
    names = {name for table in themes._SYSTEM_FACES.values() for _weight, name in table}
    names |= {name for _weight, name in (*gates._STATIC_BODY, *gates._STATIC_MONO)}
    return tuple(sorted(names - set(ICON_FILES)))


def windows_faces() -> tuple[Path, ...]:
    folder = themes._windows_fonts_dir()
    return tuple(path for name in _windows_names() if (path := folder / name).is_file())


def icon_faces() -> tuple[Path, ...]:
    folder = themes._windows_fonts_dir()
    return tuple(path for name in ICON_FILES if (path := folder / name).is_file())


@dataclass(frozen=True)
class Chain:
    """One face a skin draws text with, and the fallback faces it takes missing characters from."""
    theme: str
    face: str
    primary: str
    donors: tuple[Any, ...]

    def files(self) -> tuple[str, ...]:
        return (self.primary, *(donor.path for donor in self.donors))

    def name(self) -> str:
        chain = " > ".join(Path(path).name for path in self.files())
        return f"{self.theme} {self.face} [{chain}]"


def skin_module(theme_id: str) -> ModuleType:
    """The skin module that draws ``theme_id``, as the widgets find it: get_skin gives the Nostromo skin to a theme
    with no skin of its own and to one whose skin module does not exist yet."""
    return sys.modules[type(skins.get_skin(theme_id)).__module__]


def theme_skins() -> dict[str, tuple[str, ModuleType]]:
    """Every shipped theme's id -> (how its faces are read, the skin module that draws it): "gates" through
    gates._fonts, "canvas" through the skin's _FACE_ROLES, _faces and the donors. A skin with neither fails here."""
    found: dict[str, tuple[str, ModuleType]] = {}
    for theme_id in themes.all_themes():
        module = skin_module(theme_id)
        if module is gates:
            found[theme_id] = ("gates", module)
        elif hasattr(module, "_FACE_ROLES") and hasattr(module, "_faces"):
            found[theme_id] = ("canvas", module)
        else:
            raise AssertionError(f"theme {theme_id!r} is drawn by {module.__name__}, which has neither _FACE_ROLES "
                                 "(with _faces) nor Gates' _fonts: the coverage test cannot read its faces")
    return found


def canvas_skins() -> tuple[ModuleType, ...]:
    """The distinct Canvas skin modules that draw a shipped theme."""
    return tuple(dict.fromkeys(module for route, module in theme_skins().values() if route == "canvas"))


def canvas_chains() -> list[Chain]:
    """The faces of every theme a Canvas skin draws, from the skin's own tables (_FACE_ROLES through _faces and the
    donors)."""
    found: list[Chain] = []
    for theme_id, (route, module) in theme_skins().items():
        if route != "canvas":
            continue
        theme = themes.get_theme(theme_id)
        faces = module._faces(theme)
        donors = skin_donors(theme, module, faces)
        for key in module._FACE_ROLES:
            path = str(faces[key])
            found.append(Chain(theme_id, key, path, donors.get(path, ())))
    return found


def gates_chains() -> list[Chain]:
    """For every theme Gates draws: its body and mono at the weights Gates draws, and each whole-face fallback this
    machine has as its own chain."""
    found: list[Chain] = []
    for theme_id, (route, _module) in theme_skins().items():
        if route != "gates":
            continue
        fonts = gates._fonts(themes.get_theme(theme_id))
        for role in ("body", "mono"):
            typeface = getattr(fonts, role)
            for weight in (400, 600, 700):
                path = typeface.file_for(weight)
                if path is not None:
                    found.append(Chain(theme_id, f"{role} {weight}", path, typeface_donors(typeface)))
        spares = (("Segoe UI", gates._static_typeface(gates._STATIC_BODY)),
                  ("Consolas", gates._static_typeface(gates._STATIC_MONO)),
                  ("bundled mono", gates._bundled_mono()))
        for name, typeface in spares:
            for weight, path in typeface.files if typeface is not None else ():
                found.append(Chain(theme_id, f"{name} {weight}", path, typeface_donors(typeface)))
    return list(dict.fromkeys(found))


def chains() -> list[Chain]:
    return canvas_chains() + gates_chains()


def text_faces() -> tuple[str, ...]:
    """Every font file the widgets may draw text with: the chains' faces, the bundled files, the Windows faces."""
    found = [path for chain in chains() for path in chain.files()]
    found += [str(path) for path in (*BUNDLED, *windows_faces())]
    return tuple(dict.fromkeys(found))


def maps(path: str, ch: str) -> bool:
    found = glyphs.codepoints(path)
    return found is not None and ord(ch) in found


def uncovered(chain: Chain, chars: Iterable[str]) -> list[str]:
    """The characters whose drawn form the face the engine picks for it does not map."""
    files = chain.files()
    return [ch for ch in chars if not maps(files[chosen(chain.primary, chain.donors, drawn(ch))], drawn(ch))]


# ── c1: the reader agrees with FreeType ───────────────────────────────────────

def _freetype_draws(font: Any, ch: str) -> tuple[float, bytes]:
    size = int(font.size)
    image = Image.new("L", (4 * size, 3 * size), 0)
    ImageDraw.Draw(image).text((size, 2 * size), ch, font=font, fill=255, anchor="ls")
    return float(font.getlength(ch)), image.tobytes()


def test_the_character_map_reader_agrees_with_freetype():
    """A character counts as mapped for FreeType when Pillow does not draw it as the .notdef glyph that U+10FFFD,
    which no font maps, gets. Spaces are left out: they draw nothing, and so does VT323's .notdef."""
    disagree: list[str] = []
    faces = [Path(path) for path in text_faces()] + list(icon_faces())
    for path in faces:
        assert not maps(str(path), NOT_A_CHARACTER), path.name
        font = ImageFont.truetype(str(path), 40)
        notdef = _freetype_draws(font, NOT_A_CHARACTER)
        chars = [ch for ch in CORPUS if not ch.isspace()] + list(ICONS)
        for ch in chars:
            if maps(str(path), ch) != (_freetype_draws(font, ch) != notdef):
                disagree.append(f"{path.name}: {_label(ch)} reader says {maps(str(path), ch)}")
    assert not disagree, "\n".join(disagree)


# ── c2: known facts ───────────────────────────────────────────────────────────

TURKISH_GAPS = "\u011e\u011f\u0130\u015e\u015f\u202f"     # Ğ ğ İ Ş ş and the narrow no-break space


def test_known_facts_about_the_bundled_faces():
    """From the spike (#88) and plan-92 1.8: Share Tech Mono lacks five Turkish letters and U+202F but has ı and Ç;
    VT323 lacks U+202F, and its .notdef is blank where its A is not."""
    stm = str(FONTS / "sharetechmono" / "ShareTechMono-Regular.ttf")
    assert [ch for ch in TURKISH_GAPS if maps(stm, ch)] == []
    assert maps(stm, "ı") and maps(stm, "Ç")
    vt = str(FONTS / "vt323" / "VT323-Regular.ttf")
    assert not maps(vt, "\u202f")
    assert not font_tables.has_outline(vt, 0)
    a = glyphs.cmap(vt)[ord("A")]
    assert font_tables.has_outline(vt, a)


def test_consolas_has_what_share_tech_mono_lacks():
    consolas = themes._windows_fonts_dir() / "consola.ttf"
    if not consolas.is_file():
        pytest.skip("Consolas is not installed on this machine")
    assert [ch for ch in TURKISH_GAPS if not maps(str(consolas), ch)] == []


# ── c3: every chain covers the corpus ─────────────────────────────────────────

def _coverage_failures(found: list[Chain]) -> list[str]:
    failures: list[str] = []
    for chain in found:
        failures += [f"{chain.name()}: {_label(ch)}" for ch in uncovered(chain, CORPUS)]
    return failures


def test_the_chains_take_in_every_shipped_theme():
    """A theme the routing above passed over would go unchecked: every shipped theme has chains of its own."""
    assert {chain.theme for chain in chains()} == set(themes.all_themes())


def test_every_widget_face_covers_every_character_through_its_chain():
    failures = _coverage_failures(chains())
    assert not failures, "no face in the chain draws:\n" + "\n".join(failures)


def test_the_chains_cover_the_corpus_without_the_windows_fonts_too(tmp_path, monkeypatch):
    """Where Consolas is missing, Night City's letters come from the bundled IBM Plex Mono: checked on any machine."""
    monkeypatch.setattr(themes, "_windows_fonts_dir", lambda: tmp_path)
    monkeypatch.setattr(themes, "_WARNED", set())
    found = canvas_chains()
    mono = [chain for chain in found if chain.theme == "night_city" and chain.face == night_city.FONT_MONO]
    assert [Path(path).name for path in mono[0].files()][1:] == ["IBMPlexMono-Regular.ttf"]
    failures = _coverage_failures(found)
    assert not failures, "no face in the chain draws:\n" + "\n".join(failures)


def test_gates_icon_face_has_its_two_icons():
    icons = gates._fonts(themes.get_theme("gates")).icons
    if icons is None:
        pytest.skip("Segoe Fluent Icons is not installed on this machine")
    for ch in ICONS:
        assert maps(icons, ch), _label(ch)
        assert font_tables.has_outline(icons, glyphs.cmap(icons)[ord(ch)]), _label(ch)


# ── c4: no blank glyphs ───────────────────────────────────────────────────────

def test_no_face_maps_a_character_to_a_blank_glyph():
    """A blank draws nothing and looks like a space, so no screenshot shows it (VT323's missing characters do this)."""
    failures: list[str] = []
    for path in text_faces():
        if font_tables.outline_format(path) != "glyf":
            failures.append(f"{Path(path).name}: outline format not read ({font_tables.outline_format(path)})")
            continue
        table = glyphs.cmap(path) or {}
        for ch in CORPUS:
            if not ch.isspace() and ord(ch) in table and not font_tables.has_outline(path, table[ord(ch)]):
                failures.append(f"{Path(path).name}: {_label(ch)} is mapped to a glyph with no outline")
    assert not failures, "\n".join(failures)


# ── c5: the faces only the dashboard draws ────────────────────────────────────

_FONT_URL = re.compile(r'url\("/static/fonts/([^"]+\.ttf)"\)')


def dashboard_faces() -> tuple[Path, ...]:
    """The bundled files the dashboard's @font-face rules load and no widget draws text with."""
    widget = {Path(path).resolve() for chain in chains() for path in chain.files()}
    urls = {match for css in sorted(WEBUI.glob("**/*.css")) for match in _FONT_URL.findall(css.read_text("utf-8"))}
    return tuple(sorted({(FONTS / url).resolve() for url in urls} - widget))


def test_the_dashboard_only_faces_lack_nothing_but_the_narrow_space():
    """WebView2 falls back per character for U+202F (the spike), so that one may be missing; nothing else may."""
    found = dashboard_faces()
    assert {path.name for path in found} >= {"Michroma-Regular.ttf", "ChakraPetch-Bold.ttf", "Rajdhani-Medium.ttf"}
    failures = [f"{path.name}: {_label(ch)}" for path in found for ch in CORPUS
                if ch != "\u202f" and not maps(str(path), ch)]
    assert not failures, "\n".join(failures)


# ── c6 and c7: the corpus and the skins' tables ───────────────────────────────

def test_the_corpus_holds_no_combining_mark():
    """A stray U+0307 comes from lower-casing İ by the English rule. The frames are checked for one in Turkish
    (#91: tests/test_locale_surfaces.py)."""
    marks = [_label(ch) for ch in CORPUS if unicodedata.category(ch).startswith("M")]
    assert not marks, marks


def test_the_corpus_holds_what_the_languages_need():
    for ch in "ğĞşŞİıÇçÖöÜü" + "àâæçéèêëîïôœùûüÿ«»" + "áéíñóúü¿¡" + "\u00a0\u202f·…[]":
        assert ch in CORPUS, _label(ch)


def test_every_face_key_a_skin_draws_with_is_in_its_face_table():
    """A key outside _FACE_ROLES would draw with no theme role and no fallback, and this test would not see it.
    Night City's text styles (Type) are its own; the FONT_* keys are checked in every Canvas skin a theme uses."""
    styles = [value for value in vars(night_city).values() if isinstance(value, night_city.Type)]
    assert styles and {style.face for style in styles} <= set(night_city._FACE_ROLES)
    modules = canvas_skins()
    assert {night_city, nostromo} <= set(modules)
    for module in modules:
        keys = {value for name, value in vars(module).items() if name.startswith("FONT_")}
        assert keys and keys <= set(module._FACE_ROLES), module.__name__
