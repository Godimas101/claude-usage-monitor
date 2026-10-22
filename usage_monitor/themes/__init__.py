"""Theme tokens: load and validate themes/<id>.json, resolve variants, derive CSS and fonts.

Each theme is one JSON file. It holds the token schema that the Pillow skins and the dashboard
CSS share (docs/UI_ARCHITECTURE.md, "Themes"), and this module is the only reader of those
files. Callers get frozen dataclasses. Colour values are lowercase #rrggbb or #rrggbbaa.

Selection (resolve)
  theme_id       "nostromo" | "night_city" | "gates". An unknown id gives Nostromo.
  theme_variant  Nostromo: amber, green, white, red, blue, cyan, orange, purple.
                 Night City: blackwall, relic, edgerun, mox.
                 Gates: an accent id (system, blue, teal, purple, rose, orange, green).
  theme_mode     Gates only: "system" (the default), "light" or "dark".
  v1 "theme"     read only while theme_id is unset. migrate_v1() copies it into theme_id and
                 theme_variant once, and after that the old name is never read again.

Gates accents. A Gates variant id is "<accent>-<dark|light>". For the "system" accent the live
Windows accent replaces the accent tokens, and the other tokens are derived from that colour:
  accent        the live accent
  accent_alt    the accent moved 30 % of the way toward the variant's surface colour. This
                reproduces 11 of the 12 shipped fixed-accent values exactly. green-dark is one
                level out in one channel.
  glow          the accent with the variant's own glow alpha (5c on dark, 47 on light)
  accent_soft, accent_tint (extras)   the accent with each token's own alpha byte
The mode "system" asks AppsUseLightTheme. Both readers are keyword arguments of resolve(), so
tests can inject them. By default they come from usage_monitor.ui.platform. When that module
is missing or a read fails, the JSON values are used.

Fonts. font_path() maps a theme role to a TTF file: bundled faces under
usage_monitor/webui/fonts/<family>/ first, then Windows fonts in the Fonts folder of %WINDIR%.
Weights follow CSS font-weight matching. A family with a single face (a variable font) gives
that face for every weight. Both folders come from paths.resource_dir(), so frozen builds find
them too.
"""
from __future__ import annotations

import functools
import importlib
import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol

from usage_monitor.log import get_logger
from usage_monitor.paths import resource_dir

log = get_logger(__name__)

DEFAULT_THEME_ID = "nostromo"
GATES_ID = "gates"
_THEME_ORDER = (DEFAULT_THEME_ID, "night_city", GATES_ID)
# The sixteen colour roles every variant must define (docs/UI_ARCHITECTURE.md, "Themes").
ROLES: tuple[str, ...] = (
    "bg", "surface", "surface_alt", "border", "border_dim", "text", "text_bright", "text_dim",
    "accent", "accent_alt", "ok", "warn", "alert", "track", "glow", "stale",
)
FONT_ROLES: tuple[str, ...] = ("display", "body", "mono")
GATES_ACCENTS: tuple[str, ...] = ("system", "blue", "teal", "purple", "rose", "orange", "green")
# v1 "theme" names (theme.py THEMES) -> the Nostromo variant of the same colour.
V1_NAME_TO_VARIANT: dict[str, str] = {
    "Default": "amber", "Red": "red", "Blue": "blue", "Green": "green",
    "Purple": "purple", "Cyan": "cyan", "Orange": "orange", "White": "white",
}
# Gates system accent: accent_alt sits this fraction of the way from the accent to the surface.
SYSTEM_ACCENT_ALT_MIX = 0.30

_TOP_KEYS = (
    "id", "name", "tagline", "fonts", "shape", "effects", "bar", "widget", "variants", "extras",
)
_OBJECT_KEYS = ("fonts", "shape", "effects", "bar", "widget", "extras")
_VARIANT_KEYS = ("id", "name", "default", "mode", "colors")
# Theme-level extras keyed by variant id. Each variant's entries become Variant.extras.
_VARIANT_TABLES = ("variant_tokens", "token_extras", "derived_per_variant")
_FALLBACK_FONT_STACK = {
    "display": "system-ui, sans-serif",
    "body": "system-ui, sans-serif",
    "mono": "Consolas, monospace",
}
_HEX = re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})")
_ACCENT = re.compile(r"#?([0-9a-fA-F]{6})")
_WEIGHTS = {
    "regular": 400, "normal": 400, "medium": 500, "semibold": 600, "demibold": 600, "bold": 700,
}
# Windows fonts by normalised family name: (weight, file in the Fonts folder). A single entry
# is a variable font that covers every weight.
_SYSTEM_FACES: dict[str, tuple[tuple[int, str], ...]] = {
    "segoeuivariable": ((400, "SegUIVar.ttf"),),
    "segoeuivariabledisplay": ((400, "SegUIVar.ttf"),),
    "segoeuivariabletext": ((400, "SegUIVar.ttf"),),
    "segoefluenticons": ((400, "SegoeIcons.ttf"),),
    "cascadiamono": ((400, "CascadiaMono.ttf"),),
    "consolas": ((400, "consola.ttf"), (700, "consolab.ttf")),
}
# Problems already logged, so a reader that keeps failing does not flood the log on every resolve.
_WARNED: set[str] = set()


class ThemeError(ValueError):
    """A theme file or theme definition breaks the schema. The message lists every problem."""


class SettingsReader(Protocol):
    """Anything with Settings.get: the Settings class, or a plain dict in tests."""

    def get(self, key: str, default: Any = None) -> Any: ...


class SettingsStore(SettingsReader, Protocol):
    """A reader that can also write, as Settings.update does."""

    def update(self, mapping: Mapping[str, Any]) -> None: ...


@dataclass(frozen=True, slots=True)
class Variant:
    """One colour set of a theme.

    colors holds every role in ROLES, plus any other colour tokens the file defines. extras holds
    the variant's entries from the theme's keyed tables, for example Nostromo's ghost or Gates'
    accent_soft. Treat both dicts as read-only: theme objects are cached and shared.
    """

    id: str
    name: str
    default: bool
    mode: str | None
    colors: dict[str, str]
    extras: dict[str, str]


@dataclass(frozen=True, slots=True)
class Theme:
    """A validated theme file. Theme objects are cached and shared, so treat them as read-only."""

    id: str
    name: str
    tagline: str
    fonts: dict[str, dict[str, Any]]
    shape: dict[str, Any]
    effects: dict[str, Any]
    bar: dict[str, Any]
    widget: dict[str, Any]
    variants: tuple[Variant, ...]
    extras: dict[str, Any]

    @property
    def default_variant(self) -> Variant:
        return next(variant for variant in self.variants if variant.default)

    def variant(self, variant_id: str | None) -> Variant:
        """The variant with this id. An unknown or None id gives the default variant."""
        for variant in self.variants:
            if variant.id == variant_id:
                return variant
        return self.default_variant


# ── Loading and validation ────────────────────────────────────────────────────

def load_themes(directory: Path | None = None) -> dict[str, Theme]:
    """Read and validate every themes/*.json in ``directory`` (default: the shipped folder).

    Returns the themes keyed by id, default theme first. Raises ThemeError that lists the
    problems of every broken file, so one error never hides another.
    """
    folder = Path(directory) if directory is not None else _shipped_dir()
    if not folder.is_dir():
        raise ThemeError(f"theme folder not found: {folder}")
    themes: dict[str, Theme] = {}
    problems: list[str] = []
    for path in sorted(folder.glob("*.json"), key=_theme_order):
        try:
            theme = _read_theme(path)
        except ThemeError as exc:
            problems.append(f"{path.name}: {exc}")
        else:
            themes[theme.id] = theme
    if problems:
        raise ThemeError("; ".join(problems))
    return themes


def _shipped_dir() -> Path:
    return resource_dir() / "usage_monitor" / "themes"


def _theme_order(path: Path) -> tuple[int, str]:
    """Design order (Nostromo is the default), then any other file by name."""
    if path.stem in _THEME_ORDER:
        return (_THEME_ORDER.index(path.stem), path.stem)
    return (len(_THEME_ORDER), path.stem)


def _read_theme(path: Path) -> Theme:
    try:
        raw = path.read_text(encoding="utf-8-sig")  # tolerates a byte-order mark from a hand edit
    except (OSError, UnicodeDecodeError) as exc:
        raise ThemeError(f"cannot read the file: {exc}") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ThemeError(f"not valid JSON: {exc}") from exc
    return _parse_theme(data, path.stem)


def _parse_theme(data: object, stem: str) -> Theme:
    if not isinstance(data, dict):
        raise ThemeError("the top level must be a JSON object")
    problems: list[str] = []
    missing = [key for key in _TOP_KEYS if key not in data]
    if missing:
        problems.append("missing keys: " + ", ".join(missing))
    if data.get("id") != stem:
        problems.append(f"id {data.get('id')!r} does not match the file name {stem!r}")
    for key in ("name", "tagline"):
        if not _is_text(data.get(key)):
            problems.append(f"{key} must be a non-empty string")
    for key in _OBJECT_KEYS:
        if key in data and not isinstance(data[key], dict):
            problems.append(f"{key} must be an object")
    _check_fonts(data.get("fonts"), problems)
    extras = data.get("extras") if isinstance(data.get("extras"), dict) else {}
    variants = _parse_variants(data.get("variants"), extras, problems)
    if problems:
        raise ThemeError("; ".join(problems))
    return Theme(
        id=stem,
        name=data["name"],
        tagline=data["tagline"],
        fonts=data["fonts"],
        shape=data["shape"],
        effects=data["effects"],
        bar=data["bar"],
        widget=data["widget"],
        variants=variants,
        extras=extras,
    )


def _check_fonts(fonts: object, problems: list[str]) -> None:
    if not isinstance(fonts, dict):
        return  # reported with the other object keys
    for role in FONT_ROLES:
        spec = fonts.get(role)
        if not isinstance(spec, dict) or not _is_text(spec.get("family")):
            problems.append(f"fonts.{role} needs a family name")


def _parse_variants(
    raw: object, extras: dict[str, Any], problems: list[str]
) -> tuple[Variant, ...]:
    if not isinstance(raw, list) or not raw:
        problems.append("variants must be a non-empty list")
        return ()
    defaults = sum(1 for item in raw if isinstance(item, dict) and item.get("default") is True)
    if defaults != 1:
        problems.append(f"exactly one variant must be default (found {defaults})")
    variants: list[Variant] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        label = f"variants[{index}]"
        if not isinstance(item, dict):
            problems.append(f"{label} must be an object")
            continue
        variant_id = item.get("id")
        if not _is_text(variant_id):
            problems.append(f"{label} needs an id")
            continue
        label = f"variant {variant_id!r}"
        missing = [key for key in _VARIANT_KEYS if key not in item]
        if missing:
            problems.append(f"{label}: missing keys {', '.join(missing)}")
            continue
        if variant_id in seen:
            problems.append(f"{label}: duplicate id")
            continue
        seen.add(variant_id)
        if not _is_text(item["name"]):
            problems.append(f"{label}: name must be a non-empty string")
        if not isinstance(item["default"], bool):
            problems.append(f"{label}: default must be true or false")
        if item["mode"] not in (None, "dark", "light"):
            problems.append(f"{label}: mode must be dark, light or null")
        colors = _check_colors(item["colors"], label, problems)
        if colors is not None:
            variants.append(Variant(
                id=variant_id,
                name=item["name"],
                default=item["default"],
                mode=item["mode"],
                colors=colors,
                extras=_variant_extras(extras, variant_id),
            ))
    return tuple(variants)


def _check_colors(raw: object, label: str, problems: list[str]) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        problems.append(f"{label}: colors must be an object")
        return None
    missing = [role for role in ROLES if role not in raw]
    bad = [f"{key}={value!r}" for key, value in raw.items() if not _is_hex(value)]
    if missing:
        problems.append(f"{label}: missing colour roles {', '.join(missing)}")
    if bad:
        problems.append(f"{label}: not #rrggbb or #rrggbbaa: {', '.join(bad)}")
    if missing or bad:
        return None
    return {key: value.lower() for key, value in raw.items()}


def _variant_extras(extras: dict[str, Any], variant_id: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for table in _VARIANT_TABLES:
        entry = extras.get(table)
        tokens = entry.get(variant_id) if isinstance(entry, dict) else None
        if isinstance(tokens, dict):
            found.update({key: value for key, value in tokens.items() if isinstance(value, str)})
    return found


@functools.lru_cache(maxsize=None)
def _shipped_themes() -> dict[str, Theme]:
    themes = load_themes()
    if DEFAULT_THEME_ID not in themes:
        raise ThemeError(f"the default theme {DEFAULT_THEME_ID!r} is missing from {_shipped_dir()}")
    return themes


def get_theme(theme_id: str | None) -> Theme:
    """The shipped theme with this id. An unknown or None id gives Nostromo. Cached."""
    themes = _shipped_themes()
    if isinstance(theme_id, str) and theme_id in themes:
        return themes[theme_id]
    if theme_id is not None:
        log.debug("unknown theme %r; using %s", theme_id, DEFAULT_THEME_ID)
    return themes[DEFAULT_THEME_ID]


def all_themes() -> dict[str, Theme]:
    """Every shipped theme keyed by id, default first. Returns a new dict on each call."""
    return dict(_shipped_themes())


# ── Selection ─────────────────────────────────────────────────────────────────

def resolve(
    settings: SettingsReader,
    *,
    accent_provider: Callable[[], str | None] | None = None,
    light_provider: Callable[[], bool | None] | None = None,
) -> tuple[Theme, Variant]:
    """The theme and variant the settings select. Reads the settings and never writes them.

    accent_provider returns the Windows accent as "#rrggbb", and light_provider reports
    AppsUseLightTheme. Both are used only for the Gates system accent and system mode. They
    default to usage_monitor.ui.platform, and when a reader is missing or fails, the JSON
    values are used.
    """
    choice = _choice(settings)
    theme = get_theme(choice.theme_id)
    if theme.id == GATES_ID:
        return theme, _gates_variant(theme, choice, accent_provider, light_provider)
    return theme, theme.variant(choice.variant_id)


def live_variant(
    theme: Theme,
    variant: Variant,
    *,
    accent_provider: Callable[[], str | None] | None = None,
) -> Variant:
    """``variant`` as resolve() would apply it now.

    A Gates "system" variant takes the live Windows accent in place of the JSON default, and every
    other variant is returned unchanged. For lists that show the variants side by side, such as the
    dashboard's theme picker, so a swatch matches what choosing it gives.
    """
    if theme.id == GATES_ID and variant.id.startswith("system-"):
        return _with_system_accent(variant, _system_accent(accent_provider))
    return variant


def migrate_v1(settings: SettingsStore) -> bool:
    """Copy the v1 "theme" name into theme_id and theme_variant, only while theme_id is unset.

    Returns True when it wrote the two keys. Call it once at startup, before resolve().
    """
    if _is_text(settings.get("theme_id")):
        return False
    name = settings.get("theme")
    variant_id = _v1_variant(name)
    settings.update({"theme_id": DEFAULT_THEME_ID, "theme_variant": variant_id})
    log.info("migrated the v1 theme %r to %s/%s", name, DEFAULT_THEME_ID, variant_id)
    return True


@dataclass(frozen=True, slots=True)
class _Choice:
    theme_id: str
    variant_id: str | None
    mode: str | None


def _choice(settings: SettingsReader) -> _Choice:
    stored = settings.get("theme_id")
    if _is_text(stored):
        return _Choice(
            stored,
            _text_or_none(settings.get("theme_variant")),
            _text_or_none(settings.get("theme_mode")),
        )
    # Not migrated yet, or a fresh install: the v1 name still picks the Nostromo variant.
    return _Choice(DEFAULT_THEME_ID, _v1_variant(settings.get("theme")), None)


def _v1_variant(name: object) -> str:
    """The Nostromo variant for a v1 theme name. A missing or unknown name gives the default."""
    if isinstance(name, str):
        wanted = name.strip().lower()
        for known, variant_id in V1_NAME_TO_VARIANT.items():
            if known.lower() == wanted:
                return variant_id
    log.debug("v1 theme name %r is not known; using the default variant", name)
    return V1_NAME_TO_VARIANT["Default"]


def _gates_variant(
    theme: Theme,
    choice: _Choice,
    accent_provider: Callable[[], str | None] | None,
    light_provider: Callable[[], bool | None] | None,
) -> Variant:
    accent = choice.variant_id if choice.variant_id in GATES_ACCENTS else "system"
    mode = choice.mode if choice.mode in ("dark", "light") else None
    if mode is None:
        mode = "light" if _system_is_light(light_provider) else "dark"
    variant = theme.variant(f"{accent}-{mode}")
    if accent == "system":
        variant = _with_system_accent(variant, _system_accent(accent_provider))
    return variant


def _system_is_light(provider: Callable[[], bool | None] | None) -> bool:
    read = provider if provider is not None else _platform_light
    try:
        return bool(read())
    except Exception as exc:  # the registry read is best effort; the JSON default is dark
        _warn_once("light", "cannot read the Windows app mode (%s); using the dark variant", exc)
        return False


def _system_accent(provider: Callable[[], str | None] | None) -> str | None:
    read = provider if provider is not None else _platform_accent
    try:
        raw = read()
    except Exception as exc:  # the registry read is best effort; the JSON accent stays
        _warn_once("accent", "cannot read the Windows accent (%s); using the JSON accent", exc)
        return None
    accent = _normal_accent(raw)
    if accent is None:
        log.debug("ignoring Windows accent value %r", raw)
    return accent


def _normal_accent(raw: object) -> str | None:
    if isinstance(raw, str):
        match = _ACCENT.fullmatch(raw.strip())
        if match:
            return "#" + match.group(1).lower()
    return None


def _with_system_accent(variant: Variant, accent: str | None) -> Variant:
    """The variant with the live accent in place of its accent tokens (see the module docstring)."""
    if accent is None:
        return variant
    colors = dict(variant.colors)
    colors["accent"] = accent
    colors["accent_alt"] = _mix(accent, colors["surface"], SYSTEM_ACCENT_ALT_MIX)
    colors["glow"] = accent + _alpha_suffix(colors["glow"])
    extras = dict(variant.extras)
    for key in ("accent_soft", "accent_tint"):
        if key in extras:
            extras[key] = accent + _alpha_suffix(extras[key])
    return replace(variant, colors=colors, extras=extras)


def _alpha_suffix(value: str) -> str:
    """The two alpha hex digits of #rrggbbaa, or "" for an opaque #rrggbb."""
    return value[7:] if len(value) == 9 else ""


def _mix(start: str, end: str, weight: float) -> str:
    """``start`` moved ``weight`` of the way toward ``end``, per channel, rounded half up."""
    channels = [
        int(a + (b - a) * weight + 0.5)
        for a, b in zip(_channels(start), _channels(end))
    ]
    return "#" + "".join(f"{value:02x}" for value in channels)


def _channels(hex_colour: str) -> tuple[int, int, int]:
    return (int(hex_colour[1:3], 16), int(hex_colour[3:5], 16), int(hex_colour[5:7], 16))


@functools.lru_cache(maxsize=1)
def _platform() -> ModuleType | None:
    """usage_monitor.ui.platform, or None while the Win32 layer is missing. Imported lazily.

    importlib on purpose: `from usage_monitor.ui import platform` returns the package attribute that
    an earlier import left behind, even when sys.modules says the module is unavailable (None).
    """
    try:
        platform = importlib.import_module("usage_monitor.ui.platform")
    except Exception as exc:  # no UI layer yet, or not Windows: the JSON values stand
        log.debug("platform colour and mode readers are unavailable: %s", exc)
        return None
    return platform


def _platform_accent() -> str | None:
    platform = _platform()
    return None if platform is None else platform.windows_accent_hex()


def _platform_light() -> bool | None:
    platform = _platform()
    return None if platform is None else platform.apps_use_light_theme()


def _warn_once(key: str, message: str, *args: object) -> None:
    if key not in _WARNED:
        _WARNED.add(key)
        log.warning(message, *args)


# ── Outputs ───────────────────────────────────────────────────────────────────

def css_variables(theme: Theme, variant: Variant) -> dict[str, str]:
    """CSS custom properties for the webui.

    One per colour role, then the variant's extra tokens (a colour role of the same name wins),
    then the font stacks and shape sizes. Underscores become hyphens: surface_alt -> --surface-alt.
    """
    values: dict[str, str] = {}
    for name, value in variant.colors.items():
        values[_css_name(name)] = value
    for name, value in variant.extras.items():
        values.setdefault(_css_name(name), value)
    for role in FONT_ROLES:
        values[f"--font-{role}"] = _font_stack(theme, role)
    shape = theme.shape
    for css_name, key in (
        ("--radius", "radius"),
        ("--radius-control", "control_radius"),
        ("--chamfer", "chamfer"),
        ("--stroke", "stroke"),
    ):
        value = shape.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            values[css_name] = f"{value}px"
    return values


def _css_name(name: str) -> str:
    return "--" + name.replace("_", "-")


def _font_stack(theme: Theme, role: str) -> str:
    return f'"{theme.fonts[role]["family"]}", {_FALLBACK_FONT_STACK[role]}'


def font_path(theme: Theme, role: str, weight: str = "regular") -> Path | None:
    """The TTF for a theme role ("display", "body" or "mono") at a weight, or None."""
    spec = theme.fonts.get(role)
    family = spec.get("family") if isinstance(spec, dict) else None
    if not _is_text(family):
        log.debug("theme %s has no %r font", theme.id, role)
        return None
    return family_font_path(family, weight)


def family_font_path(family: str, weight: str | int = "regular") -> Path | None:
    """The TTF for any font family and weight: bundled under webui/fonts first, then Windows fonts.

    Use it for families outside the theme roles, such as Segoe Fluent Icons. Returns None when
    neither location has the family.
    """
    key = _family_key(family)
    if not key:
        return None
    target = _weight_number(weight)
    bundled = _bundled_faces(str(_fonts_dir() / key))
    if bundled:
        return _pick_face(bundled, target)
    windows = _windows_fonts_dir()
    system = tuple(
        (weight_value, windows / file_name)
        for weight_value, file_name in _SYSTEM_FACES.get(key, ())
    )
    system = tuple(item for item in system if item[1].is_file())
    return _pick_face(system, target) if system else None


def _fonts_dir() -> Path:
    return resource_dir() / "usage_monitor" / "webui" / "fonts"


def _windows_fonts_dir() -> Path:
    return Path(os.environ.get("WINDIR") or "C:/Windows") / "Fonts"


def _family_key(family: str) -> str:
    """The folder name a bundled family uses: lowercase letters and digits only."""
    return re.sub(r"[^0-9a-z]", "", family.lower())


@functools.lru_cache(maxsize=None)
def _bundled_faces(folder: str) -> tuple[tuple[int, Path], ...]:
    root = Path(folder)
    if not root.is_dir():
        return ()
    faces = [(_face_weight(path.stem), path) for path in root.glob("*.ttf") if path.is_file()]
    return tuple(sorted(faces, key=lambda item: (item[0], item[1].name)))


def _face_weight(stem: str) -> int:
    """The weight named by the file's suffix: ChakraPetch-SemiBold is 600. Unknown means 400."""
    return _WEIGHTS.get(stem.rsplit("-", 1)[-1].lower(), 400)


def _weight_number(weight: str | int) -> int:
    if isinstance(weight, int) and not isinstance(weight, bool):
        return weight
    text = str(weight).strip().lower()
    if text.isdigit():
        return int(text)
    if text in _WEIGHTS:
        return _WEIGHTS[text]
    log.debug("unknown font weight %r; using regular", weight)
    return 400


def _pick_face(faces: tuple[tuple[int, Path], ...], target: int) -> Path:
    by_weight: dict[int, Path] = {}
    for weight_value, path in faces:
        by_weight.setdefault(weight_value, path)
    return by_weight[_css_weight_match(sorted(by_weight), target)]


def _css_weight_match(available: list[int], target: int) -> int:
    """The weight CSS font matching picks for ``target`` (CSS Fonts 4, font-weight matching)."""
    if target in available:
        return target
    if target < 400:
        order = sorted((w for w in available if w < target), reverse=True)
        order += sorted(w for w in available if w > target)
    elif target > 500:
        order = sorted(w for w in available if w > target)
        order += sorted((w for w in available if w < target), reverse=True)
    else:
        order = sorted(w for w in available if target < w <= 500)
        order += sorted((w for w in available if w < target), reverse=True)
        order += sorted(w for w in available if w > 500)
    return order[0]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _text_or_none(value: object) -> str | None:
    return value if _is_text(value) else None


def _is_hex(value: object) -> bool:
    return isinstance(value, str) and _HEX.fullmatch(value) is not None
