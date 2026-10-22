"""Tests for usage_monitor.themes: shipped files, validation, resolve, v1 migration, CSS and fonts.

Everything runs offline and opens no window. The Windows readers are blocked unless a test
injects its own, so no test reads the registry. The font checks use this machine's files and
skip only a Windows font that is not installed here. Settings files are written under tmp_path.
"""
from __future__ import annotations

import dataclasses
import importlib
import json
import logging
import re
import shutil
import sys
import types
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from PIL import ImageFont

from usage_monitor import paths, themes
from usage_monitor.settings import Settings
from usage_monitor.themes import colour

SHIPPED = paths.resource_dir() / "usage_monitor" / "themes"
FONTS = paths.resource_dir() / "usage_monitor" / "webui" / "fonts"
ROLES = (
    "bg", "surface", "surface_alt", "border", "border_dim", "text", "text_bright", "text_dim",
    "accent", "accent_alt", "ok", "warn", "alert", "track", "glow", "stale",
)
FONT_ROLES = ("display", "body", "mono")
ACCENTS = ("system", "blue", "teal", "purple", "rose", "orange", "green")
NOSTROMO_IDS = ("amber", "green", "white", "red", "blue", "cyan", "orange", "purple")
NIGHT_CITY_IDS = ("blackwall", "relic", "edgerun", "mox")
V1_NAMES = {
    "Default": "amber", "Red": "red", "Blue": "blue", "Green": "green",
    "Purple": "purple", "Cyan": "cyan", "Orange": "orange", "White": "white",
}
HEX = re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})")


@pytest.fixture(autouse=True)
def windows_readers_blocked(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Make usage_monitor.ui.platform unimportable, so resolve() uses the JSON values unless a
    test injects readers. The module's caches are cleared before and after each test."""
    monkeypatch.setitem(sys.modules, "usage_monitor.ui.platform", None)
    themes._platform.cache_clear()
    themes._WARNED.clear()
    yield
    themes._platform.cache_clear()
    themes._WARNED.clear()


@pytest.fixture(scope="module")
def shipped() -> dict[str, themes.Theme]:
    return themes.load_themes(SHIPPED)


def _raw(theme_id: str) -> dict[str, Any]:
    return json.loads((SHIPPED / f"{theme_id}.json").read_text(encoding="utf-8"))


def _rgb(hex_colour: str) -> tuple[int, int, int]:
    return (int(hex_colour[1:3], 16), int(hex_colour[3:5], 16), int(hex_colour[5:7], 16))


def _gates_system(
    mode: str, accent: str | None = None, light: bool | None = None,
) -> tuple[dict[str, str], dict[str, Any]]:
    """resolve() arguments for the Gates system accent, with optional injected readers."""
    settings = {"theme_id": "gates", "theme_variant": "system", "theme_mode": mode}
    kwargs: dict[str, Any] = {}
    if accent is not None:
        kwargs["accent_provider"] = lambda: accent
    if light is not None:
        kwargs["light_provider"] = lambda: light
    return settings, kwargs


# ── Shipped files ─────────────────────────────────────────────────────────────

def test_the_three_shipped_files_load_in_design_order(shipped: dict[str, themes.Theme]) -> None:
    assert list(shipped) == ["nostromo", "night_city", "gates"]
    assert tuple(v.id for v in shipped["nostromo"].variants) == NOSTROMO_IDS
    assert tuple(v.id for v in shipped["night_city"].variants) == NIGHT_CITY_IDS
    gate_ids = {v.id for v in shipped["gates"].variants}
    assert gate_ids == {f"{a}-{m}" for a in ACCENTS for m in ("dark", "light")}


def test_default_variants_are_amber_blackwall_and_system_dark(
    shipped: dict[str, themes.Theme],
) -> None:
    assert shipped["nostromo"].default_variant.id == "amber"
    assert shipped["night_city"].default_variant.id == "blackwall"
    assert shipped["gates"].default_variant.id == "system-dark"


def test_all_themes_matches_the_shipped_folder(shipped: dict[str, themes.Theme]) -> None:
    assert list(themes.all_themes()) == list(shipped)


@pytest.mark.parametrize("theme_id", ["nostromo", "night_city", "gates"])
def test_every_shipped_variant_has_every_role_as_lowercase_hex(
    theme_id: str, shipped: dict[str, themes.Theme]
) -> None:
    for variant in shipped[theme_id].variants:
        assert set(ROLES) <= set(variant.colors), variant.id
        for role in ROLES:
            value = variant.colors[role]
            assert HEX.fullmatch(value), f"{variant.id}.{role}={value}"
            assert value == value.lower(), f"{variant.id}.{role}"


def test_gates_modes_follow_the_variant_id(shipped: dict[str, themes.Theme]) -> None:
    for variant in shipped["gates"].variants:
        accent, mode = variant.id.rsplit("-", 1)
        assert accent in ACCENTS
        assert variant.mode == mode


def test_shipped_variant_extras_come_from_the_keyed_tables(
    shipped: dict[str, themes.Theme],
) -> None:
    assert shipped["nostromo"].variant("amber").extras["ghost"] == "#2a1c08"
    assert shipped["night_city"].variant("blackwall").extras["fg-hi"] == "#ffd9cf"
    assert shipped["night_city"].variant("mox").extras["bar"] == "#ff7ac3"
    assert shipped["gates"].variant("teal-dark").extras["accent_soft"] == "#4dd6c224"


def test_gates_accent_rule_reproduces_every_shipped_accent_alt(
    shipped: dict[str, themes.Theme],
) -> None:
    """accent_alt = accent + 30 % of (surface - accent) per channel, for each fixed accent."""
    for variant in shipped["gates"].variants:
        if variant.id.startswith("system-"):
            continue
        accent = _rgb(variant.colors["accent"])
        surface = _rgb(variant.colors["surface"])
        predicted = [a + 0.30 * (s - a) for a, s in zip(accent, surface)]
        shipped_alt = _rgb(variant.colors["accent_alt"])
        assert all(abs(p - q) <= 1 for p, q in zip(predicted, shipped_alt)), variant.id


def test_gates_glow_is_the_accent_with_the_mode_alpha(shipped: dict[str, themes.Theme]) -> None:
    for variant in shipped["gates"].variants:
        glow = variant.colors["glow"]
        assert glow[:7] == variant.colors["accent"], variant.id
        assert glow[7:] == ("5c" if variant.mode == "dark" else "47"), variant.id


def test_variant_lookup_falls_back_to_the_default(shipped: dict[str, themes.Theme]) -> None:
    theme = shipped["night_city"]
    assert theme.variant("mox").id == "mox"
    assert theme.variant("no-such-variant").id == "blackwall"
    assert theme.variant(None).id == "blackwall"


def test_colour_roles_match_the_documented_sixteen() -> None:
    assert themes.ROLES == ROLES


# ── Validation ────────────────────────────────────────────────────────────────

def _break(mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    data = _raw("nostromo")
    mutate(data)
    return data


BREAKAGES: list[tuple[str, Callable[[dict[str, Any]], None], str]] = [
    ("missing top-level key", lambda d: d.pop("tagline"), "missing keys: tagline"),
    ("bad hex colour", lambda d: d["variants"][0]["colors"].update(bg="#12345"),
     "not #rrggbb or #rrggbbaa: bg='#12345'"),
    ("hex without the hash", lambda d: d["variants"][0]["colors"].update(bg="090909"),
     "bg='090909'"),
    ("missing colour role", lambda d: d["variants"][0]["colors"].pop("glow"),
     "missing colour roles glow"),
    ("no default variant", lambda d: d["variants"][0].update(default=False),
     "exactly one variant must be default (found 0)"),
    ("two default variants", lambda d: d["variants"][1].update(default=True),
     "exactly one variant must be default (found 2)"),
    ("id not matching the file", lambda d: d.update(id="other"),
     "does not match the file name 'nostromo'"),
    ("font family missing", lambda d: d["fonts"]["mono"].pop("family"),
     "fonts.mono needs a family name"),
    ("no variants", lambda d: d.update(variants=[]), "variants must be a non-empty list"),
    ("duplicate variant id", lambda d: d["variants"][1].update(id="amber"), "duplicate id"),
    ("variant without colours", lambda d: d["variants"][2].pop("colors"),
     "missing keys colors"),
    ("unknown variant mode", lambda d: d["variants"][0].update(mode="grey"),
     "mode must be dark, light or null"),
]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [(mutate, message) for _name, mutate, message in BREAKAGES],
    ids=[name for name, _mutate, _message in BREAKAGES],
)
def test_broken_theme_definitions_are_rejected(
    tmp_path: Path, mutate: Callable[[dict[str, Any]], None], message: str
) -> None:
    (tmp_path / "nostromo.json").write_text(json.dumps(_break(mutate)), encoding="utf-8")
    with pytest.raises(themes.ThemeError, match=re.escape(message)):
        themes.load_themes(tmp_path)


def test_a_file_that_is_not_json_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "nostromo.json").write_text("{", encoding="utf-8")
    with pytest.raises(themes.ThemeError, match="not valid JSON"):
        themes.load_themes(tmp_path)


def test_the_top_level_must_be_an_object(tmp_path: Path) -> None:
    (tmp_path / "nostromo.json").write_text("[]", encoding="utf-8")
    with pytest.raises(themes.ThemeError, match="the top level must be a JSON object"):
        themes.load_themes(tmp_path)


def test_a_missing_folder_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(themes.ThemeError, match="theme folder not found"):
        themes.load_themes(tmp_path / "absent")


def test_every_broken_file_is_reported_in_one_error(tmp_path: Path) -> None:
    broken = _break(lambda d: d.pop("tagline"))
    (tmp_path / "nostromo.json").write_text(json.dumps(broken), encoding="utf-8")
    gates = json.loads((SHIPPED / "gates.json").read_text(encoding="utf-8"))
    gates["variants"][0]["colors"]["bg"] = "not-a-colour"
    (tmp_path / "gates.json").write_text(json.dumps(gates), encoding="utf-8")
    with pytest.raises(themes.ThemeError) as excinfo:
        themes.load_themes(tmp_path)
    message = str(excinfo.value)
    assert "nostromo.json: missing keys: tagline" in message
    assert "gates.json: variant 'system-dark'" in message


def test_colour_values_are_lowercased_on_load(shipped: dict[str, themes.Theme]) -> None:
    assert shipped["gates"].variant("system-dark").colors["accent"] == "#60cdff"


# ── Theme lookup ──────────────────────────────────────────────────────────────

def test_get_theme_falls_back_to_nostromo() -> None:
    assert themes.get_theme(None).id == "nostromo"
    assert themes.get_theme("no-such-theme").id == "nostromo"
    assert themes.get_theme("gates").id == "gates"


def test_get_theme_returns_the_cached_object() -> None:
    assert themes.get_theme("gates") is themes.get_theme("gates")


def test_all_themes_returns_a_copy_in_design_order() -> None:
    everything = themes.all_themes()
    assert list(everything) == ["nostromo", "night_city", "gates"]
    everything.clear()
    assert list(themes.all_themes()) == ["nostromo", "night_city", "gates"]


# ── resolve ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("mode", ["dark", "light", "system", None])
@pytest.mark.parametrize("variant_id", NOSTROMO_IDS)
def test_resolve_every_nostromo_variant_ignores_the_mode(
    variant_id: str, mode: str | None, shipped: dict[str, themes.Theme]
) -> None:
    settings = {"theme_id": "nostromo", "theme_variant": variant_id, "theme_mode": mode}
    theme, variant = themes.resolve(settings)
    assert theme.id == "nostromo"
    assert variant == shipped["nostromo"].variant(variant_id)


@pytest.mark.parametrize("mode", ["dark", "light", "system", None])
@pytest.mark.parametrize("variant_id", NIGHT_CITY_IDS)
def test_resolve_every_night_city_variant_ignores_the_mode(
    variant_id: str, mode: str | None, shipped: dict[str, themes.Theme]
) -> None:
    settings = {"theme_id": "night_city", "theme_variant": variant_id, "theme_mode": mode}
    theme, variant = themes.resolve(settings)
    assert theme.id == "night_city"
    assert variant == shipped["night_city"].variant(variant_id)


def test_unknown_or_missing_variants_use_the_theme_default() -> None:
    assert themes.resolve({"theme_id": "nostromo"})[1].id == "amber"
    assert themes.resolve({"theme_id": "nostromo", "theme_variant": "pink"})[1].id == "amber"
    assert themes.resolve({"theme_id": "night_city", "theme_variant": "nope"})[1].id == "blackwall"
    theme, variant = themes.resolve({"theme_id": "no-such-theme", "theme_variant": "red"})
    assert (theme.id, variant.id) == ("nostromo", "red")


@pytest.mark.parametrize("light", [False, True])
@pytest.mark.parametrize("mode", ["dark", "light", "system"])
@pytest.mark.parametrize("accent", ACCENTS)
def test_resolve_every_gates_accent_mode_and_injected_reader(
    accent: str, mode: str, light: bool, shipped: dict[str, themes.Theme]
) -> None:
    settings = {"theme_id": "gates", "theme_variant": accent, "theme_mode": mode}
    theme, variant = themes.resolve(
        settings, accent_provider=lambda: "#123456", light_provider=lambda: light,
    )
    assert theme.id == "gates"
    chosen = ("light" if light else "dark") if mode == "system" else mode
    assert variant.id == f"{accent}-{chosen}"
    shipped_variant = shipped["gates"].variant(variant.id)
    if accent != "system":
        assert variant == shipped_variant
        return
    # #123456 is too dark for the dark window, so it is lifted there; the light window keeps it
    lifted = "#7a9fc7" if chosen == "dark" else "#123456"
    assert variant.colors["accent"] == lifted
    assert variant.colors["accent_alt"] == ("#647d99" if chosen == "dark" else "#597189")
    assert variant.colors["glow"] == lifted + shipped_variant.colors["glow"][7:]
    assert variant.extras["accent_fill"] == lifted
    for role in ROLES:
        if role not in ("accent", "accent_alt", "glow"):
            assert variant.colors[role] == shipped_variant.colors[role], role


def test_system_accent_values_for_dark_and_light(shipped: dict[str, themes.Theme]) -> None:
    _, dark = themes.resolve(
        {"theme_id": "gates", "theme_variant": "system", "theme_mode": "dark"},
        accent_provider=lambda: "#123456",
    )
    assert dark.id == "system-dark"
    assert dark.colors["accent"] == "#7a9fc7"            # lifted to OKLCH lightness 0.69, hue and chroma kept
    assert dark.colors["accent_alt"] == "#647d99"
    assert dark.colors["glow"] == "#7a9fc75c"
    assert dark.extras["accent_soft"] == "#7a9fc724"
    assert dark.extras["accent_tint"] == "#7a9fc714"
    assert dark.extras["accent_fill"] == "#7a9fc7"

    _, light = themes.resolve(
        {"theme_id": "gates", "theme_variant": "system", "theme_mode": "system"},
        accent_provider=lambda: "#123456", light_provider=lambda: True,
    )
    assert light.id == "system-light"
    assert light.colors["accent"] == "#123456"           # already below lightness 0.60: unchanged
    assert light.colors["accent_alt"] == "#597189"
    assert light.colors["glow"] == "#12345647"
    assert light.extras["accent_soft"] == "#12345624"


@pytest.mark.parametrize("reported", ["#ABCDEF", "abcdef", "  #abcdef  "])
def test_windows_accent_strings_are_normalised(reported: str) -> None:
    _, variant = themes.resolve(
        {"theme_id": "gates", "theme_variant": "system", "theme_mode": "dark"},
        accent_provider=lambda: reported,
    )
    assert variant.colors["accent"] == "#abcdef"       # already light enough for the dark window


def test_resolving_the_system_accent_leaves_the_cached_theme_unchanged() -> None:
    themes.resolve(
        {"theme_id": "gates", "theme_variant": "system", "theme_mode": "dark"},
        accent_provider=lambda: "#123456",
    )
    assert themes.get_theme("gates").variant("system-dark").colors["accent"] == "#60cdff"


def test_without_readers_the_system_variant_keeps_its_json_values(
    shipped: dict[str, themes.Theme],
) -> None:
    settings, _ = _gates_system("system")
    _, variant = themes.resolve(settings)
    assert variant == shipped["gates"].variant("system-dark")


@pytest.mark.parametrize("reported", [None, "", "not-a-colour", "#12345", 12345])
def test_unusable_accent_values_fall_back_to_the_json_accent(
    reported: Any, shipped: dict[str, themes.Theme]
) -> None:
    _, variant = themes.resolve(
        {"theme_id": "gates", "theme_variant": "system", "theme_mode": "dark"},
        accent_provider=lambda: reported,
    )
    assert variant == shipped["gates"].variant("system-dark")


def test_unknown_gates_accent_or_mode_mean_system(shipped: dict[str, themes.Theme]) -> None:
    settings = {"theme_id": "gates", "theme_variant": "pink", "theme_mode": "auto"}
    _, variant = themes.resolve(settings)
    assert variant == shipped["gates"].variant("system-dark")


def test_failing_readers_fall_back_to_the_json_and_warn_once(
    shipped: dict[str, themes.Theme], caplog: pytest.LogCaptureFixture
) -> None:
    def broken() -> str:
        raise RuntimeError("registry unavailable")

    settings = {"theme_id": "gates", "theme_variant": "system", "theme_mode": "system"}
    with caplog.at_level(logging.WARNING, logger="usage_monitor.themes"):
        for _ in range(3):
            _, variant = themes.resolve(settings, accent_provider=broken, light_provider=broken)
    assert variant == shipped["gates"].variant("system-dark")
    warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and r.name == "usage_monitor.themes"
    ]
    assert len(warnings) == 2  # one for the accent, one for the mode, however often resolve runs


def test_default_readers_come_from_the_platform_module_when_it_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = types.ModuleType("usage_monitor.ui.platform")
    fake.windows_accent_hex = lambda: "#ABCDEF"  # type: ignore[attr-defined]
    fake.apps_use_light_theme = lambda: True  # type: ignore[attr-defined]
    package = types.ModuleType("usage_monitor.ui")
    package.platform = fake  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "usage_monitor.ui", package)
    monkeypatch.setitem(sys.modules, "usage_monitor.ui.platform", fake)
    themes._platform.cache_clear()
    settings, _ = _gates_system("system")
    _, variant = themes.resolve(settings)
    assert variant.id == "system-light"
    assert variant.colors["accent"] == "#6484a3"         # a pale accent is darkened for the light window


def test_live_variant_is_what_choosing_the_variant_applies(shipped: dict[str, themes.Theme]) -> None:
    """The theme picker lists the variants. A system swatch showed the JSON accent while resolve() applied
    the live Windows accent."""
    gates, nostromo = shipped["gates"], shipped["nostromo"]
    for mode in ("dark", "light"):
        listed = themes.live_variant(gates, gates.variant(f"system-{mode}"), accent_provider=lambda: "#D0000C")
        applied = themes.resolve(
            {"theme_id": "gates", "theme_variant": "system", "theme_mode": mode},
            accent_provider=lambda: "#D0000C",
        )[1]
        assert listed == applied
        assert listed.colors["accent"] == ("#ff5145" if mode == "dark" else "#d0000c")
    fixed = gates.variant("blue-dark")
    assert themes.live_variant(gates, fixed, accent_provider=lambda: "#d0000c") is fixed
    amber = nostromo.variant("amber")
    assert themes.live_variant(nostromo, amber, accent_provider=lambda: "#d0000c") is amber
    system = gates.variant("system-dark")
    assert themes.live_variant(gates, system, accent_provider=lambda: None) == system     # no reading: the JSON values


def test_a_blocked_platform_module_stays_blocked_after_an_earlier_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once any module has imported usage_monitor.ui.platform, the package keeps it as an attribute.
    A `from usage_monitor.ui import platform` would hand that module back and ignore the block, so
    the live Windows accent leaked into the "no readers" expectations of a full run."""
    monkeypatch.delitem(sys.modules, "usage_monitor.ui.platform")      # lift the fixture's block ...
    importlib.import_module("usage_monitor.ui.platform")                # ... so the attribute gets set
    monkeypatch.setitem(sys.modules, "usage_monitor.ui.platform", None)  # ... then block it again
    themes._platform.cache_clear()
    assert themes._platform() is None
    settings, _ = _gates_system("system")
    assert themes.resolve(settings)[1].colors["accent"] == themes.get_theme("gates").variant("system-dark").colors["accent"]


def test_resolve_without_theme_settings_is_the_fresh_install_default() -> None:
    theme, variant = themes.resolve({})
    assert (theme.id, variant.id) == ("nostromo", "amber")


# ── v1 migration ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("v1_name", "variant_id"), list(V1_NAMES.items()))
def test_migrate_v1_maps_every_v1_name(v1_name: str, variant_id: str) -> None:
    settings: dict[str, Any] = {"theme": v1_name}
    assert themes.migrate_v1(settings) is True
    assert settings == {"theme": v1_name, "theme_id": "nostromo", "theme_variant": variant_id}
    assert themes.resolve(settings)[1].id == variant_id


def test_migrate_v1_runs_once() -> None:
    settings: dict[str, Any] = {"theme": "Red"}
    assert themes.migrate_v1(settings) is True
    settings["theme_variant"] = "cyan"  # a later choice must survive a second call
    assert themes.migrate_v1(settings) is False
    assert settings["theme_variant"] == "cyan"


def test_migrate_v1_leaves_settings_that_already_have_a_theme_id_alone() -> None:
    settings: dict[str, Any] = {"theme": "Red", "theme_id": "gates", "theme_variant": "teal"}
    before = dict(settings)
    assert themes.migrate_v1(settings) is False
    assert settings == before


def test_migrate_v1_uses_amber_for_missing_and_unknown_names() -> None:
    fresh: dict[str, Any] = {}
    assert themes.migrate_v1(fresh) is True
    assert fresh == {"theme_id": "nostromo", "theme_variant": "amber"}
    unknown: dict[str, Any] = {"theme": "Pink"}
    assert themes.migrate_v1(unknown) is True
    assert unknown["theme_variant"] == "amber"


def test_resolve_reads_the_v1_name_without_writing_it() -> None:
    settings: dict[str, Any] = {"theme": "Cyan"}
    theme, variant = themes.resolve(settings)
    assert (theme.id, variant.id) == ("nostromo", "cyan")
    assert settings == {"theme": "Cyan"}


def test_v1_map_agrees_with_the_nostromo_accent_map(shipped: dict[str, themes.Theme]) -> None:
    accent_map = shipped["nostromo"].extras["accent_map"]
    assert {v1: variant for variant, v1 in accent_map.items()} == themes.V1_NAME_TO_VARIANT


def test_a_v1_settings_file_is_migrated_and_saved(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"theme": "Purple", "poll_interval_ms": 900000}), encoding="utf-8")
    settings = Settings(path, save_delay_s=0)
    assert themes.migrate_v1(settings) is True
    assert themes.resolve(settings)[1].id == "purple"
    settings.save_now()  # synchronous, so the read below cannot race the zero-delay save timer
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["theme_id"] == "nostromo"
    assert saved["theme_variant"] == "purple"
    assert saved["theme"] == "Purple"
    assert saved["poll_interval_ms"] == 900000


# ── CSS variables ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("theme_id", ["nostromo", "night_city", "gates"])
def test_css_variables_mirror_every_colour_role(
    theme_id: str, shipped: dict[str, themes.Theme]
) -> None:
    theme = shipped[theme_id]
    for variant in theme.variants:
        css = themes.css_variables(theme, variant)
        for role in ROLES:
            css_name = "--" + role.replace("_", "-")
            assert css[css_name] == variant.colors[role], f"{variant.id} {role}"
        assert all(name.startswith("--") and value for name, value in css.items()), variant.id


def test_css_variables_carry_fonts_shape_and_extras(shipped: dict[str, themes.Theme]) -> None:
    nostromo = shipped["nostromo"]
    css = themes.css_variables(nostromo, nostromo.variant("amber"))
    assert css["--bg"] == "#090909"
    assert css["--text"] == "#e8a020"
    assert css["--surface-alt"] == "#100e09"
    assert css["--glow"] == "#ffb30099"
    assert css["--glow-soft"] == "#ffb30038"
    assert css["--ghost"] == "#2a1c08"
    assert css["--font-display"] == '"Michroma", system-ui, sans-serif'
    assert css["--font-mono"] == '"VT323", Consolas, monospace'
    assert css["--radius"] == "0px"
    assert css["--chamfer"] == "10px"
    assert css["--stroke"] == "1px"

    gates = shipped["gates"]
    css_gates = themes.css_variables(gates, gates.variant("system-dark"))
    assert css_gates["--radius"] == "8px"
    assert css_gates["--radius-control"] == "4px"
    assert css_gates["--font-body"] == '"Segoe UI Variable Text", system-ui, sans-serif'
    assert css_gates["--font-mono"] == '"Cascadia Mono", Consolas, monospace'

    night = shipped["night_city"]
    assert themes.css_variables(night, night.variant("blackwall"))["--fg-hi"] == "#ffd9cf"


def test_css_variables_follow_the_system_accent() -> None:
    settings, kwargs = _gates_system("dark", accent="#123456")
    theme, variant = themes.resolve(settings, **kwargs)
    css = themes.css_variables(theme, variant)
    assert css["--accent"] == "#7a9fc7"
    assert css["--accent-alt"] == "#647d99"
    assert css["--accent-soft"] == "#7a9fc724"
    assert css["--accent-fill"] == "#7a9fc7"


# ── Stale ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("theme_id", ["nostromo", "night_city"])
def test_the_stale_colour_is_readable_on_every_surface_it_is_drawn_on(
    theme_id: str, shipped: dict[str, themes.Theme]
) -> None:
    """Stale, offline, rate-limited, no-credentials and loading draw every tone, the big numeral and the sign-in
    banner in this one colour, so it has to pass the 4.5:1 text floor on its own variant's window and cards.
    Nostromo's red was 2.4:1 and its amber 3.6:1. (Gates draws stale text in text_3 and uses the stale colour
    for bars and the lamp only, which need 3:1.)"""
    for variant in shipped[theme_id].variants:
        stale = variant.colors["stale"][:7]
        for role in ("bg", "surface", "surface_alt"):
            backdrop = variant.colors[role][:7]
            assert colour.contrast_ratio(stale, backdrop) >= 4.5, (theme_id, variant.id, role)


def test_stale_stays_dimmer_than_live_text_in_every_nostromo_variant(shipped: dict[str, themes.Theme]) -> None:
    for variant in shipped["nostromo"].variants:
        live, stale = variant.colors["text_dim"], variant.colors["stale"]
        assert colour.relative_luminance(stale) < colour.relative_luminance(live), variant.id
        assert colour.relative_luminance(stale) < colour.relative_luminance(variant.colors["text"]), variant.id


def test_nostromo_css_defaults_to_the_amber_stale_token(shipped: dict[str, themes.Theme]) -> None:
    css = (SHIPPED.parent / "webui" / "themes" / "nostromo.css").read_text(encoding="utf-8")
    assert f"--stale: {shipped['nostromo'].variant('amber').colors['stale']};" in css


# ── The usable system accent ──────────────────────────────────────────────────

# The 48 swatches of Windows Settings > Personalization > Colors.
WINDOWS_SWATCHES = (
    "#ffb900 #ff8c00 #f7630c #ca5010 #da3b01 #ef6950 #d13438 #ff4343 "
    "#e74856 #e81123 #ea005e #c30052 #e3008c #bf0077 #c239b3 #9a0089 "
    "#0078d4 #0063b1 #8e8cd8 #6b69d6 #8764b8 #744da9 #b146c2 #881798 "
    "#0099bc #2d7d9a #00b7c3 #038387 #00b294 #018574 #00cc6a #10893e "
    "#7a7574 #5d5a58 #68768a #515c6b #567c73 #486860 #498205 #107c10 "
    "#767676 #4c4a48 #69797e #4a5459 #647c64 #525e54 #847545 #7e735f"
).split()


def _system(accent: str, mode: str) -> tuple[themes.Theme, themes.Variant]:
    settings = {"theme_id": "gates", "theme_variant": "system", "theme_mode": mode}
    return themes.resolve(settings, accent_provider=lambda: accent)


def _severity(theme: themes.Theme, variant: themes.Variant) -> dict[str, str]:
    """Every colour that means ok, warn, alert or stale in this variant's mode."""
    tokens = theme.extras["modes"][variant.mode]
    found = {role: variant.colors[role] for role in ("ok", "warn", "alert", "stale")}
    found.update({name: tokens[name] for name in ("usage_warn", "usage_alert")})
    return {name: value[:7] for name, value in found.items()}


def _cards(theme: themes.Theme, variant: themes.Variant) -> dict[str, str]:
    """What the usage bars and marks are drawn on: the widget card and the dashboard card."""
    return {"widget": theme.extras["modes"][variant.mode]["widget_bg"][:7], "surface": variant.colors["surface"]}


def test_there_are_48_stock_windows_swatches() -> None:
    assert len(WINDOWS_SWATCHES) == 48 and len(set(WINDOWS_SWATCHES)) == 48


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_no_stock_windows_accent_draws_normal_usage_in_a_severity_colour(mode: str) -> None:
    """The accent colours normal-severity fills. With the owner's red (#d0000c) a 12 % bar was the colour of a
    93 % one. Whatever Windows hands over, the fill stays 25 dE76 away from ok, warn, alert, the two usage
    bar colours and stale, and the chrome accent and the fill both reach 3:1 on the cards."""
    json_accent = themes.get_theme("gates").variant(f"system-{mode}").colors["accent"]
    kept = 0
    for swatch in WINDOWS_SWATCHES:
        theme, variant = _system(swatch, mode)
        fill, chrome = variant.extras["accent_fill"], variant.colors["accent"]
        assert fill in (chrome, json_accent), (swatch, mode)
        kept += fill == chrome
        for name, severity in _severity(theme, variant).items():
            assert colour.delta_e76(fill, severity) >= 25, (swatch, mode, name)
        for name, card in _cards(theme, variant).items():
            assert colour.contrast_ratio(fill, card) >= 3.0, (swatch, mode, name)
            assert colour.contrast_ratio(chrome, card) >= 3.0, (swatch, mode, name)
        for key in ("accent_soft", "accent_tint"):
            assert variant.extras[key].startswith(fill), (swatch, mode, key)
        assert variant.colors["glow"].startswith(chrome), (swatch, mode)
    assert 10 < kept < 48          # most reds, oranges, greens and greys fall back; the blues and purples stay


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_the_json_system_accent_is_itself_clear_of_every_severity_colour(mode: str) -> None:
    theme = themes.get_theme("gates")
    variant = theme.variant(f"system-{mode}")
    for name, severity in _severity(theme, variant).items():
        assert colour.delta_e76(variant.colors["accent"], severity) >= 25, name


def test_a_separated_accent_is_its_own_fill_and_a_colliding_one_falls_back_to_the_json_accent() -> None:
    _, blue = _system("#0078d4", "light")
    assert blue.extras["accent_fill"] == blue.colors["accent"] == "#0078d4"
    _, red_dark = _system("#d0000c", "dark")
    assert (red_dark.colors["accent"], red_dark.extras["accent_fill"]) == ("#ff5145", "#60cdff")
    _, red_light = _system("#d0000c", "light")
    assert (red_light.colors["accent"], red_light.extras["accent_fill"]) == ("#d0000c", "#0067c0")
    # the tints behind a fill follow the fill, the glow of the selection follows the chrome accent
    assert red_dark.extras["accent_soft"] == "#60cdff24" and red_dark.extras["accent_tint"] == "#60cdff14"
    assert red_dark.colors["glow"] == "#ff51455c"
    assert red_dark.colors["accent_alt"] == "#519ec1"       # the second series is drawn from the fill, like the first


@pytest.mark.parametrize("variant_id", [f"{a}-{m}" for a in ACCENTS if a != "system" for m in ("dark", "light")])
def test_the_designed_accents_are_inside_the_bounds_and_the_lift_leaves_them_alone(
    variant_id: str, shipped: dict[str, themes.Theme]
) -> None:
    variant = shipped["gates"].variant(variant_id)
    cards = [variant.colors["surface"], shipped["gates"].extras["modes"][variant.mode]["widget_bg"]]
    assert colour.lift_accent(variant.colors["accent"], variant.mode, cards) == variant.colors["accent"]


def test_gates_css_defaults_to_the_json_accents() -> None:
    """Before /api/state answers, the stylesheet shows the system variants' own values."""
    css = (SHIPPED.parent / "webui" / "themes" / "gates.css").read_text(encoding="utf-8")
    for mode in ("dark", "light"):
        accent = themes.get_theme("gates").variant(f"system-{mode}").colors["accent"]
        assert f"--accent: {accent};" in css and f"--accent-fill: {accent};" in css, mode


# ── Fonts ─────────────────────────────────────────────────────────────────────

def _is_bundled(family: str) -> bool:
    return (FONTS / re.sub(r"[^0-9a-z]", "", family.lower())).is_dir()


@pytest.mark.parametrize("theme_id", ["nostromo", "night_city", "gates"])
@pytest.mark.parametrize("role", FONT_ROLES)
def test_font_path_finds_a_file_for_every_role_of_every_theme(
    theme_id: str, role: str, shipped: dict[str, themes.Theme]
) -> None:
    theme = shipped[theme_id]
    path = themes.font_path(theme, role)
    if path is None and not _is_bundled(theme.fonts[role]["family"]):
        family = theme.fonts[role]["family"]
        pytest.skip(f"Windows font {family!r} is not installed on this machine")
    assert path is not None and path.is_file(), f"{theme_id}/{role}"


@pytest.mark.parametrize("theme_id", ["nostromo", "night_city", "gates"])
def test_resolved_font_files_load_as_truetype(
    theme_id: str, shipped: dict[str, themes.Theme]
) -> None:
    for role in FONT_ROLES:
        path = themes.font_path(shipped[theme_id], role)
        if path is not None:
            assert ImageFont.truetype(str(path), 12) is not None


@pytest.mark.parametrize(
    ("theme_id", "role", "weight", "file_name"),
    [
        ("nostromo", "body", "regular", "IBMPlexMono-Regular.ttf"),
        ("nostromo", "body", "semibold", "IBMPlexMono-SemiBold.ttf"),
        ("nostromo", "body", "bold", "IBMPlexMono-SemiBold.ttf"),  # no bold face: nearest heavier
        ("nostromo", "body", "medium", "IBMPlexMono-Regular.ttf"),  # CSS looks at 400 before 600
        ("nostromo", "display", "bold", "Michroma-Regular.ttf"),  # one face serves every weight
        ("nostromo", "mono", "semibold", "VT323-Regular.ttf"),
        ("night_city", "display", "regular", "ChakraPetch-Medium.ttf"),
        ("night_city", "display", "semibold", "ChakraPetch-SemiBold.ttf"),
        ("night_city", "display", "bold", "ChakraPetch-Bold.ttf"),
        ("night_city", "body", "regular", "Rajdhani-Medium.ttf"),
        ("night_city", "body", "bold", "Rajdhani-Bold.ttf"),
        ("night_city", "mono", "bold", "ShareTechMono-Regular.ttf"),
    ],
)
def test_font_weights_pick_the_css_matching_bundled_face(
    theme_id: str, role: str, weight: str, file_name: str, shipped: dict[str, themes.Theme]
) -> None:
    path = themes.font_path(shipped[theme_id], role, weight)
    assert path is not None and path.name == file_name
    assert path.parent.parent == FONTS


@pytest.mark.parametrize(
    ("family", "weight", "file_name"),
    [
        ("Segoe UI Variable Display", "semibold", "SegUIVar.ttf"),
        ("Segoe UI Variable Text", "regular", "SegUIVar.ttf"),
        ("Cascadia Mono", "bold", "CascadiaMono.ttf"),
        ("Segoe Fluent Icons", "regular", "SegoeIcons.ttf"),
        ("Consolas", "regular", "consola.ttf"),
        ("Consolas", "bold", "consolab.ttf"),
    ],
)
def test_windows_fonts_resolve_by_family_name(family: str, weight: str, file_name: str) -> None:
    path = themes.family_font_path(family, weight)
    if path is None:
        pytest.skip(f"Windows font {family!r} is not installed on this machine")
    assert path.name == file_name
    assert path.parent.name == "Fonts"
    assert path.is_file()


def test_unknown_families_and_roles_give_none(shipped: dict[str, themes.Theme]) -> None:
    assert themes.family_font_path("No Such Typeface") is None
    assert themes.family_font_path("") is None
    assert themes.font_path(shipped["gates"], "italic") is None
    odd = dataclasses.replace(
        shipped["nostromo"],
        fonts={**shipped["nostromo"].fonts, "display": {"family": "No Such Typeface"}},
    )
    assert themes.font_path(odd, "display") is None


def test_bundled_fonts_and_themes_come_from_resource_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A frozen build has no repo: the same relative layout under resource_dir() must work."""
    bundle = tmp_path / "bundle"
    font_dir = bundle / "usage_monitor" / "webui" / "fonts" / "michroma"
    theme_dir = bundle / "usage_monitor" / "themes"
    font_dir.mkdir(parents=True)
    theme_dir.mkdir(parents=True)
    shutil.copy(FONTS / "michroma" / "Michroma-Regular.ttf", font_dir)
    shutil.copy(SHIPPED / "nostromo.json", theme_dir)
    monkeypatch.setattr(themes, "resource_dir", lambda: bundle)

    loaded = themes.load_themes()
    assert list(loaded) == ["nostromo"]
    path = themes.font_path(loaded["nostromo"], "display")
    assert path == font_dir / "Michroma-Regular.ttf"
    assert path.is_file()
