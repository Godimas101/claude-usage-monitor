"""Tests for usage_monitor.settings. Every file lives under pytest's tmp_path."""
from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from usage_monitor import settings as settings_mod
from usage_monitor.settings import DEFAULTS, Settings

V1_DEFAULTS: dict[str, Any] = {
    "poll_interval_ms": 900000,
    "position": None,
    "show_floating": True,
    "show_taskbar": True,
    "check_updates": True,
    "skip_version": "",
    "theme": "Default",
    "float_transparent_bg": False,
    "float_text_fx": True,
    "taskbar_transparent_bg": False,
    "taskbar_text_fx": True,
}

V1_FILE: dict[str, Any] = {
    "poll_interval_ms": 300000,
    "position": {"x": 120, "y": 40},
    "show_floating": False,
    "show_taskbar": True,
    "check_updates": True,
    "skip_version": "1.2.0",
    "theme": "Night City",
    "taskbar_transparent_bg": True,
}


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A Settings() without an explicit path must never reach the real %APPDATA%."""
    monkeypatch.setenv("CUM_APPDATA_DIR", str(tmp_path / "app"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))


@pytest.fixture
def writes(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Records the path of every disk write the settings object makes."""
    calls: list[Path] = []
    real_write = settings_mod._atomic_write_json

    def counting(target: Path, payload: dict[str, Any]) -> None:
        calls.append(target)
        real_write(target, payload)

    monkeypatch.setattr(settings_mod, "_atomic_write_json", counting)
    return calls


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def wait_for(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


# ── Defaults and v1 files ─────────────────────────────────────────────────────

def test_missing_file_gives_defaults_and_writes_nothing(tmp_path: Path, writes: list[Path]) -> None:
    path = tmp_path / "settings.json"
    s = Settings(path)
    try:
        assert s.as_dict() == DEFAULTS
        assert s["poll_interval_ms"] == 900000
        assert s["ingest_interval_s"] == 60
        assert s["settings_version"] == 2
        assert not path.exists()
    finally:
        s.close()
    assert writes == []


def test_v1_keys_keep_their_v1_defaults() -> None:
    for key, value in V1_DEFAULTS.items():
        assert DEFAULTS[key] == value
        assert type(DEFAULTS[key]) is type(value)


def test_v1_file_loads_unchanged_then_is_upgraded_once(tmp_path: Path, writes: list[Path]) -> None:
    path = tmp_path / "settings.json"
    write_json(path, V1_FILE)
    s = Settings(path)
    try:
        for key, value in V1_FILE.items():
            assert s[key] == value
        assert s["settings_version"] == 2
        assert s["billing_day"] is None
        assert s["plan_price_override"] is None
        assert s["ingest_interval_s"] == 60
    finally:
        s.close()
    stored = read_json(path)
    assert stored["settings_version"] == 2
    assert stored["theme"] == "Night City"
    assert len(writes) == 1


def test_current_version_file_is_left_alone(tmp_path: Path, writes: list[Path]) -> None:
    path = tmp_path / "settings.json"
    write_json(path, {**V1_FILE, "settings_version": 2})
    before = path.read_bytes()
    Settings(path).close()
    assert writes == []
    assert path.read_bytes() == before


def test_byte_order_mark_and_version_one_are_accepted(tmp_path: Path, writes: list[Path]) -> None:
    path = tmp_path / "settings.json"
    body = json.dumps({**V1_FILE, "settings_version": 1}).encode("utf-8")
    path.write_bytes(b"\xef\xbb\xbf" + body)
    s = Settings(path)
    s.close()
    assert s["theme"] == "Night City"
    assert s["settings_version"] == 2
    assert read_json(path)["settings_version"] == 2
    assert len(writes) == 1


def test_unknown_keys_survive_a_save(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    write_json(path, {**V1_FILE, "future_flag": {"nested": [1, 2]}})
    s = Settings(path)
    assert s["future_flag"] == {"nested": [1, 2]}
    s.set("theme", "Gates")
    s.close()
    stored = read_json(path)
    assert stored["future_flag"] == {"nested": [1, 2]}
    assert stored["theme"] == "Gates"


def test_v2_keys_keep_valid_values_from_disk(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    write_json(path, {
        "settings_version": 2,
        "billing_day": 15,
        "plan_price_override": 200.0,
        "ingest_interval_s": 30,
    })
    s = Settings(path)
    try:
        assert (s["billing_day"], s["plan_price_override"], s["ingest_interval_s"]) == (15, 200.0, 30)
    finally:
        s.close()


def test_absurdly_deep_json_is_treated_as_corrupt(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("[" * 200_000 + "]" * 200_000, encoding="utf-8")
    s = Settings(path)  # must not raise RecursionError
    try:
        assert s["poll_interval_ms"] == 900000
    finally:
        s.close()
    assert not path.exists()


def test_runtime_values_are_held_by_reference_even_when_not_copyable(tmp_path: Path) -> None:
    s = Settings(tmp_path / "settings.json", save_delay_s=60)
    lock = threading.Lock()
    runtime = {"lock": lock}  # deepcopy would fail on this
    seen: list[object] = []
    s.on_change(lambda key, value: seen.append(value))
    try:
        s.set("_live", runtime)
        assert s.get("_live") is runtime
        assert seen == [runtime]
        assert seen[0] is runtime
    finally:
        s.close()


# ── Corrupt and wrongly typed files ───────────────────────────────────────────

def test_corrupt_json_is_moved_aside_and_defaults_are_used(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "settings.json"
    older_bad = tmp_path / "settings.json.bad"
    older_bad.write_text("an older corrupt copy", encoding="utf-8")
    path.write_text("{ not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="usage_monitor"):
        s = Settings(path)
    try:
        assert s["poll_interval_ms"] == 900000
    finally:
        s.close()
    assert not path.exists()
    assert older_bad.read_text(encoding="utf-8") == "{ not json"
    assert any(
        r.levelno == logging.WARNING and r.name == "usage_monitor.settings" for r in caplog.records
    )


@pytest.mark.parametrize("text", ["[1, 2, 3]", '"just a string"', ""])
def test_non_object_or_empty_file_is_treated_as_corrupt(tmp_path: Path, text: str) -> None:
    path = tmp_path / "settings.json"
    path.write_text(text, encoding="utf-8")
    s = Settings(path)
    try:
        assert s.as_dict() == DEFAULTS
    finally:
        s.close()
    assert not path.exists()
    assert (tmp_path / "settings.json.bad").exists()


def test_wrong_typed_values_fall_back_to_defaults_with_a_log_line(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    bad = {
        "poll_interval_ms": "fast",
        "show_floating": "yes",
        "show_taskbar": 1,
        "position": "left",
        "theme": 7,
        "billing_day": 45,
        "plan_price_override": -5,
        "ingest_interval_s": 0,
        "settings_version": "2",
    }
    path = tmp_path / "settings.json"
    write_json(path, bad)
    with caplog.at_level(logging.WARNING, logger="usage_monitor"):
        s = Settings(path)
    try:
        for key in ("poll_interval_ms", "show_floating", "show_taskbar", "position", "theme",
                    "billing_day", "plan_price_override", "ingest_interval_s", "settings_version"):
            assert s[key] == DEFAULTS[key]
            assert type(s[key]) is type(DEFAULTS[key])
    finally:
        s.close()
    logged = " ".join(r.getMessage() for r in caplog.records)
    for key in bad:
        assert repr(key) in logged


# ── Runtime keys, listeners and validation ───────────────────────────────────

def test_runtime_keys_notify_listeners_but_are_never_written(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    write_json(path, {**V1_FILE, "settings_version": 2, "_stale_from_disk": 1})
    s = Settings(path)
    seen: list[tuple[str, object]] = []
    s.on_change(lambda key, value: seen.append((key, value)))
    try:
        assert s.get("_stale_from_disk") is None
        s.set("_status", "live")
        assert seen == [("_status", "live")]
        assert s.get("_status") == "live"
        assert "_status" not in s.as_dict()
        s.save_now()
        assert "_status" not in read_json(path)
    finally:
        s.close()


def test_listeners_fire_only_when_a_value_really_changes(tmp_path: Path) -> None:
    s = Settings(tmp_path / "settings.json", save_delay_s=60)
    seen: list[tuple[str, object]] = []
    s.on_change(lambda key, value: seen.append((key, value)))
    try:
        s.set("theme", "Gates")
        s.set("theme", "Gates")
        s.set("show_floating", False)
        s.update({"theme": "Gates", "skip_version": "9.9.9"})
    finally:
        s.close()
    assert seen == [("theme", "Gates"), ("show_floating", False), ("skip_version", "9.9.9")]


def test_unsubscribe_stops_delivery_and_is_idempotent(tmp_path: Path) -> None:
    s = Settings(tmp_path / "settings.json", save_delay_s=60)
    seen: list[str] = []
    unsubscribe = s.on_change(lambda key, value: seen.append(key))
    try:
        s.set("theme", "A")
        unsubscribe()
        unsubscribe()
        s.set("theme", "B")
    finally:
        s.close()
    assert seen == ["theme"]


def test_a_failing_listener_is_logged_and_does_not_stop_the_others(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    s = Settings(tmp_path / "settings.json", save_delay_s=60)
    delivered: list[object] = []

    def broken(key: str, value: object) -> None:
        raise RuntimeError("listener bug")

    s.on_change(broken)
    s.on_change(lambda key, value: delivered.append(value))
    try:
        with caplog.at_level(logging.ERROR, logger="usage_monitor"):
            s.set("theme", "Gates")
        assert delivered == ["Gates"]
        assert s["theme"] == "Gates"
    finally:
        s.close()
    assert any(r.levelno == logging.ERROR and "theme" in r.getMessage() for r in caplog.records)


def test_listeners_and_callers_cannot_alter_stored_values(tmp_path: Path) -> None:
    s = Settings(tmp_path / "settings.json", save_delay_s=60)

    def tamper(key: str, value: dict[str, int]) -> None:
        value["x"] = -1

    s.on_change(tamper)
    position = {"x": 5, "y": 6}
    try:
        s.set("position", position)
        position["y"] = 99
        assert s["position"] == {"x": 5, "y": 6}
        fetched = s.get("position")
        fetched["x"] = 0
        assert s.get("position") == {"x": 5, "y": 6}
        assert s.as_dict()["position"] == {"x": 5, "y": 6}
    finally:
        s.close()


def test_set_rejects_bad_values_and_applies_nothing_from_that_batch(tmp_path: Path) -> None:
    s = Settings(tmp_path / "settings.json")
    try:
        with pytest.raises(ValueError):
            s.set("poll_interval_ms", "soon")
        with pytest.raises(ValueError):
            s.update({"theme": "Gates", "billing_day": 40})
        assert s["theme"] == "Default"
        with pytest.raises(TypeError):
            s.set(5, "x")  # type: ignore[arg-type]
    finally:
        s.close()


def test_v2_setters_accept_valid_values(tmp_path: Path) -> None:
    s = Settings(tmp_path / "settings.json", save_delay_s=60)
    try:
        s.set("billing_day", 31)
        s.set("billing_day", None)
        s.set("plan_price_override", 0)
        s.set("plan_price_override", 199.99)
        s.set("ingest_interval_s", 30)
        assert s["billing_day"] is None
        assert s["plan_price_override"] == 199.99
        assert s["ingest_interval_s"] == 30
    finally:
        s.close()


def test_every_default_passes_its_own_rule(tmp_path: Path) -> None:
    s = Settings(tmp_path / "settings.json")
    try:
        s.update(DEFAULTS)
        assert s.as_dict() == DEFAULTS
    finally:
        s.close()


# ── Saving ────────────────────────────────────────────────────────────────────

def test_set_defers_the_write_until_close(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    s = Settings(path, save_delay_s=60)
    s.set("theme", "Gates")
    assert not path.exists()
    s.close()
    assert read_json(path)["theme"] == "Gates"


def test_a_burst_of_changes_is_written_once(tmp_path: Path, writes: list[Path]) -> None:
    path = tmp_path / "settings.json"
    s = Settings(path, save_delay_s=0.3)
    try:
        for value in range(1, 51):
            s.set("ingest_interval_s", value)
        assert wait_for(path.exists)
        time.sleep(0.7)  # more than twice the debounce, so any extra write would show up
        assert len(writes) == 1
        assert read_json(path)["ingest_interval_s"] == 50
    finally:
        s.close()
    assert len(writes) == 1


def test_save_now_writes_at_once_and_cancels_the_pending_save(tmp_path: Path, writes: list[Path]) -> None:
    path = tmp_path / "settings.json"
    s = Settings(path, save_delay_s=60)
    s.set("theme", "Gates")
    s.save_now()
    assert read_json(path)["theme"] == "Gates"
    assert len(writes) == 1
    s.close()
    assert len(writes) == 1


def test_pending_save_runs_on_a_daemon_timer(tmp_path: Path) -> None:
    s = Settings(tmp_path / "settings.json", save_delay_s=60)
    try:
        s.set("theme", "Gates")
        timers = [t for t in threading.enumerate() if isinstance(t, threading.Timer)]
        assert timers
        assert all(t.daemon for t in timers)
    finally:
        s.close()


def test_default_path_is_the_settings_file_under_the_app_folder(tmp_path: Path) -> None:
    s = Settings()
    try:
        s.set("theme", "Gates")
    finally:
        s.close()
    assert read_json(tmp_path / "app" / "settings.json")["theme"] == "Gates"


def test_failed_save_is_logged_and_leaves_the_old_file_and_no_temp_files(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "settings.json"
    s = Settings(path, save_delay_s=60)
    try:
        s.set("theme", "Gates")
        s.save_now()
        before = path.read_bytes()
        s.set("unserialisable", object())  # json cannot write this, so the save must fail
        with caplog.at_level(logging.WARNING, logger="usage_monitor"):
            s.save_now()
        assert path.read_bytes() == before
        assert list(tmp_path.glob("*.tmp")) == []
        assert any("could not save settings" in r.getMessage() for r in caplog.records)
        s.set("unserialisable", None)
    finally:
        s.close()
    assert read_json(path)["theme"] == "Gates"


def test_failed_upgrade_write_is_logged_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "settings.json"
    write_json(path, V1_FILE)

    def broken(target: Path, payload: dict[str, Any]) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(settings_mod, "_atomic_write_json", broken)
    with caplog.at_level(logging.WARNING, logger="usage_monitor"):
        s = Settings(path)
    try:
        assert s["theme"] == "Night City"
    finally:
        s.close()
    assert any("could not save settings" in r.getMessage() for r in caplog.records)


def test_concurrent_writers_all_land_and_each_change_is_announced_once(tmp_path: Path) -> None:
    s = Settings(tmp_path / "settings.json", save_delay_s=60)
    events: list[tuple[str, object]] = []
    guard = threading.Lock()

    def record(key: str, value: object) -> None:
        with guard:
            events.append((key, value))

    s.on_change(record)

    def worker(n: int) -> None:
        for i in range(200):
            s.set(f"counter_{n}", i)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert not any(thread.is_alive() for thread in threads)
        assert len(events) == 8 * 200
        for n in range(8):
            assert s[f"counter_{n}"] == 199
    finally:
        s.close()


# ── Regression: absurd intervals, unreadable files, re-entrant listeners ─────────────

@pytest.mark.parametrize("key", ["ingest_interval_s", "poll_interval_ms"])
def test_an_absurd_interval_is_refused_by_set_and_by_load(tmp_path, key):
    s = Settings(tmp_path / "settings.json", save_delay_s=60)
    with pytest.raises(ValueError):
        s.set(key, 1e308)
    s.close()
    path = tmp_path / "loaded.json"
    path.write_text(json.dumps({key: 1e308}), encoding="utf-8")
    assert Settings(path)[key] == DEFAULTS[key]


def test_an_unreadable_settings_file_is_neither_replaced_nor_treated_as_missing(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    original = b'{"poll_interval_ms": 1234, "skip_version": "2.0.1", "theme": "Night City"}'
    path.write_bytes(original)
    real_read_bytes = Path.read_bytes
    blocked = {"on": True}

    def denied(self: Path) -> bytes:
        if blocked["on"] and self == path:
            raise PermissionError(13, "denied")
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", denied)
    s = Settings(path, save_delay_s=60)
    s.set("show_taskbar", False)
    s.close()  # the save is refused while the file cannot be read
    assert real_read_bytes(path) == original

    blocked["on"] = False
    s.set("skip_version", "2.0.2")
    s.close()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["poll_interval_ms"] == 1234 and data["theme"] == "Night City"  # the file's values survive
    assert data["show_taskbar"] is False and data["skip_version"] == "2.0.2"  # the changes made meanwhile too


def test_a_listener_that_sets_the_same_key_does_not_reorder_what_the_others_see(tmp_path):
    s = Settings(tmp_path / "settings.json", save_delay_s=60)
    seen: dict[str, list[Any]] = {"first": [], "second": []}

    def first(key: str, value: Any) -> None:
        if key == "theme":
            seen["first"].append(value)
        if key == "theme" and value == "A":
            s.set("theme", "B")  # a change made while the change to A is still being delivered

    def second(key: str, value: Any) -> None:
        if key == "theme":
            seen["second"].append(value)

    s.on_change(first)
    s.on_change(second)
    s.set("theme", "A")
    assert seen["second"] == ["A", "B"]
    assert seen["first"] == ["A", "B"]
    assert s.get("theme") == "B"
    s.close()
