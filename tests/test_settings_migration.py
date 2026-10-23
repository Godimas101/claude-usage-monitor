"""A v1.1.5 settings file, opened by v2: nothing the user chose is lost, the theme maps, the new keys appear.

tests/fixtures/settings_v1_1_5.json has the keys and value shapes of a settings.json that v1.1.5 wrote
(widget toggles, position, theme name, poll interval, update-check state), with generic values: the
numbers in it say nothing about anyone's screen. The v1 installs update to v2 in place and keep the file,
so this is the path every existing user takes.

The pieces are covered one by one in test_settings.py and test_themes.py. This file runs them together,
on a file as v1 wrote it, through the same two calls MonitorApp makes at start-up: Settings(), then
themes.migrate_v1().
"""
from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from usage_monitor import themes
from usage_monitor.settings import DEFAULTS, Settings

FIXTURE = Path(__file__).parent / "fixtures" / "settings_v1_1_5.json"

# Every key v1.1.5 could have in its file (main.py DEFAULT_SETTINGS plus what the options panel adds).
V1_KEYS = {
    "poll_interval_ms", "position", "show_floating", "show_taskbar", "check_updates", "skip_version",
    "theme", "float_transparent_bg", "float_text_fx", "taskbar_transparent_bg", "taskbar_text_fx",
}
# What a v1 file has once the user touched the options panel: the keys of the fixture.
FIXTURE_KEYS = {
    "poll_interval_ms", "position", "show_floating", "show_taskbar", "check_updates", "skip_version",
    "theme", "taskbar_transparent_bg",
}
# v1 theme.py THEMES -> the Nostromo variant of the same colour.
V1_THEMES = {
    "Default": "amber", "Red": "red", "Blue": "blue", "Green": "green",
    "Purple": "purple", "Cyan": "cyan", "Orange": "orange", "White": "white",
}


@pytest.fixture
def v1_file(tmp_path: Path) -> Path:
    path = tmp_path / "settings.json"
    shutil.copyfile(FIXTURE, path)
    return path


@pytest.fixture
def opened() -> Iterator[Callable[[Path], Settings]]:
    """Settings objects opened by a test, flushed and closed afterwards."""
    made: list[Settings] = []

    def open_settings(path: Path) -> Settings:
        settings = Settings(path, save_delay_s=0)
        made.append(settings)
        return settings

    yield open_settings
    for settings in made:
        settings.close()


def fixture_data() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def stored(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def with_values(path: Path, **values: Any) -> None:
    """Rewrite the file with some keys changed or added."""
    data = stored(path)
    data.update(values)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ── The fixture itself ────────────────────────────────────────────────────────

def test_the_fixture_is_a_v1_file_and_nothing_more() -> None:
    data = fixture_data()
    assert set(data) == FIXTURE_KEYS
    assert set(data) <= V1_KEYS
    assert set(data["position"]) == {"x", "y"} and all(isinstance(v, int) for v in data["position"].values())
    assert data["theme"] in V1_THEMES
    # None of what v2 adds is in it yet.
    assert not {"settings_version", "theme_id", "theme_variant", "theme_mode", "billing_day",
                "plan_price_override", "ingest_interval_s", "alerts_enabled"} & set(data)


def test_the_v1_theme_names_are_the_ones_themes_knows() -> None:
    assert V1_THEMES == themes.V1_NAME_TO_VARIANT


# ── Opening it ────────────────────────────────────────────────────────────────

def test_every_value_the_user_set_is_kept(v1_file: Path, opened: Callable[[Path], Settings]) -> None:
    settings = opened(v1_file)
    for key, value in fixture_data().items():
        assert settings.get(key) == value, key
        assert type(settings.get(key)) is type(value), key


def test_the_position_the_widget_was_dragged_to_is_kept(v1_file: Path, opened: Callable[[Path], Settings]) -> None:
    settings = opened(v1_file)
    assert settings.get("position") == fixture_data()["position"]
    assert themes.migrate_v1(settings) is True
    settings.save_now()
    assert opened(v1_file).get("position") == fixture_data()["position"]


def test_the_widget_toggles_are_kept(v1_file: Path, opened: Callable[[Path], Settings]) -> None:
    """A user who hid the floating widget and kept the taskbar strip still has exactly that."""
    settings = opened(v1_file)
    assert (settings.get("show_floating"), settings.get("show_taskbar")) == (False, True)
    assert settings.get("taskbar_transparent_bg") is False
    assert DEFAULTS["show_floating"] is True  # the kept value is the user's, not the default coming back


def test_all_four_look_toggles_of_the_options_panel_are_kept(v1_file: Path, opened: Callable[[Path], Settings]) -> None:
    looks = {"float_transparent_bg": True, "float_text_fx": False, "taskbar_transparent_bg": True, "taskbar_text_fx": False}
    with_values(v1_file, **looks)
    settings = opened(v1_file)
    for key, value in looks.items():
        assert settings.get(key) is value, key


def test_the_file_becomes_settings_version_2_once(v1_file: Path, opened: Callable[[Path], Settings]) -> None:
    settings = opened(v1_file)
    assert settings.get("settings_version") == 2
    first = stored(v1_file)
    assert first["settings_version"] == 2
    # v1 can read the file back: its own keys are still there with the values the user set.
    for key, value in fixture_data().items():
        assert first[key] == value, key
    settings.close()
    again = v1_file.read_bytes()
    opened(v1_file).close()
    assert v1_file.read_bytes() == again  # a file at version 2 is left alone


def test_the_new_v2_settings_appear_with_their_defaults(v1_file: Path, opened: Callable[[Path], Settings]) -> None:
    settings = opened(v1_file)
    assert settings.get("billing_day") is None
    assert settings.get("plan_price_override") is None
    assert settings.get("ingest_interval_s") == 60
    assert settings.get("alerts_enabled") is True
    assert settings.get("alert_thresholds") == [80, 90, 100]
    assert (settings.get("alert_on_threshold"), settings.get("alert_on_reset"), settings.get("alert_on_credits")) == (True, True, True)
    assert settings.get("alert_quiet_hours") is None
    # The look toggles v1 only wrote once touched come back at their v1 defaults.
    assert (settings.get("float_transparent_bg"), settings.get("float_text_fx"), settings.get("taskbar_text_fx")) == (False, True, True)
    for key, default in DEFAULTS.items():
        if key not in fixture_data():
            assert settings.get(key) == default, key
            assert stored(v1_file)[key] == default, key


def test_unknown_keys_are_preserved(v1_file: Path, opened: Callable[[Path], Settings]) -> None:
    """A key this build does not know (a later version's, a hand edit) must survive every save."""
    extra = {"future_flag": {"nested": [1, 2]}, "note": "kept"}
    with_values(v1_file, **extra)
    settings = opened(v1_file)
    for key, value in extra.items():
        assert settings.get(key) == value, key
    settings.set("show_floating", True)
    settings.close()
    on_disk = stored(v1_file)
    for key, value in extra.items():
        assert on_disk[key] == value, key
    assert on_disk["show_floating"] is True


def test_runtime_only_keys_in_a_file_are_not_loaded_or_written_back(v1_file: Path, opened: Callable[[Path], Settings]) -> None:
    """v1 kept callbacks and update info in its settings dict under keys starting with an underscore and never saved them."""
    with_values(v1_file, _update_info={"version": "9.9.9"}, _save_cb=1)
    settings = opened(v1_file)
    assert settings.get("_update_info") is None
    assert "_update_info" not in stored(v1_file) and "_save_cb" not in stored(v1_file)


# ── The theme ─────────────────────────────────────────────────────────────────

def test_the_fixture_theme_becomes_its_nostromo_variant(v1_file: Path, opened: Callable[[Path], Settings]) -> None:
    settings = opened(v1_file)
    assert themes.migrate_v1(settings) is True
    assert (settings.get("theme_id"), settings.get("theme_variant")) == ("nostromo", V1_THEMES[fixture_data()["theme"]])


@pytest.mark.parametrize(("v1_name", "variant_id"), list(V1_THEMES.items()))
def test_every_v1_theme_name_maps_to_the_nostromo_variant_of_the_same_colour(
    v1_name: str, variant_id: str, v1_file: Path, opened: Callable[[Path], Settings],
) -> None:
    with_values(v1_file, theme=v1_name)
    settings = opened(v1_file)
    assert themes.migrate_v1(settings) is True
    assert settings.get("theme_id") == "nostromo"
    assert settings.get("theme_variant") == variant_id
    assert settings.get("theme") == v1_name  # the v1 key stays, so a rollback to v1 still finds its theme
    theme, variant = themes.resolve(settings)
    assert (theme.id, variant.id) == ("nostromo", variant_id)

    settings.save_now()
    reopened = opened(v1_file)
    assert themes.migrate_v1(reopened) is False  # saved, so it does not run again
    assert (themes.resolve(reopened)[0].id, themes.resolve(reopened)[1].id) == ("nostromo", variant_id)


def test_a_theme_chosen_after_the_migration_is_not_overwritten(v1_file: Path, opened: Callable[[Path], Settings]) -> None:
    settings = opened(v1_file)
    assert themes.migrate_v1(settings) is True
    settings.update({"theme_id": "night_city", "theme_variant": "relic"})
    settings.save_now()
    reopened = opened(v1_file)
    assert themes.migrate_v1(reopened) is False
    theme, variant = themes.resolve(reopened)
    assert (theme.id, variant.id) == ("night_city", "relic")
    assert reopened.get("theme") == fixture_data()["theme"]


def test_a_v1_file_without_a_theme_gets_the_default_amber(v1_file: Path, opened: Callable[[Path], Settings]) -> None:
    data = stored(v1_file)
    del data["theme"]
    v1_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    settings = opened(v1_file)
    assert settings.get("theme") == DEFAULTS["theme"] == "Default"
    assert themes.migrate_v1(settings) is True
    assert (settings.get("theme_id"), settings.get("theme_variant")) == ("nostromo", "amber")
