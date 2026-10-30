"""Tests for usage_monitor.agent.guard: the usage guard's verdict (#27). Offline and pure."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from usage_monitor import settings as settings_mod
from usage_monitor.agent import guard
from usage_monitor.data.models import Limit, UsageSnapshot

UTC = timezone.utc
NOW = datetime(2026, 10, 28, 21, 0, 0, tzinfo=UTC)
ARMED_POLICY = {"enabled": True, "session_cap": 95, "weekly_cap": 85, "scoped_cap": None, "stagger_s": 120}


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def reading(session: float | None = 40.0, weekly: float | None = 50.0, fable: float | None = None, *,
            session_resets: datetime = NOW + timedelta(hours=2),
            weekly_resets: datetime = NOW + timedelta(days=3)) -> dict:
    doc: dict = {"session": None, "weekly": None, "scoped": []}
    if session is not None:
        doc["session"] = {"percent": session, "resets_at": iso(session_resets)}
    if weekly is not None:
        doc["weekly"] = {"percent": weekly, "resets_at": iso(weekly_resets)}
    if fable is not None:
        doc["scoped"].append({"label": "FABLE WEEKLY", "model": "Fable", "percent": fable,
                              "resets_at": iso(weekly_resets)})
    return doc


def test_a_disarmed_guard_is_off_whatever_the_usage():
    verdict = guard.evaluate(reading(99.0, 99.0), {**ARMED_POLICY, "enabled": False}, NOW)
    assert verdict["state"] == guard.OFF and verdict["over"] == [] and verdict["resume_at"] is None


def test_under_every_cap_the_guard_is_armed():
    verdict = guard.evaluate(reading(94.0, 84.0), ARMED_POLICY, NOW)
    assert verdict["state"] == guard.ARMED and verdict["over"] == []


def test_at_a_cap_it_holds_until_that_window_resets():
    verdict = guard.evaluate(reading(95.0, 50.0), ARMED_POLICY, NOW)
    assert verdict["state"] == guard.HOLDING
    assert [(item["name"], item["percent"], item["cap"]) for item in verdict["over"]] == [("session", 95.0, 95)]
    assert verdict["resume_at"] == iso(NOW + timedelta(hours=2))


def test_a_fresh_5_hour_window_does_not_release_a_week_that_is_over_its_cap():
    # Both over: the guard holds until the LATER reset, the week's.
    both = guard.evaluate(reading(97.0, 99.9), ARMED_POLICY, NOW)
    assert both["resume_at"] == iso(NOW + timedelta(days=3))
    # The 5-hour window has reset (a new reading at 3%): still holding for the week.
    later = NOW + timedelta(hours=3)
    after_reset = guard.evaluate(reading(3.0, 99.9, session_resets=later + timedelta(hours=5)), ARMED_POLICY, later)
    assert after_reset["state"] == guard.HOLDING and [item["name"] for item in after_reset["over"]] == ["weekly"]


def test_a_window_that_reset_since_the_reading_no_longer_counts():
    old = reading(99.0, 50.0)                                           # the session resets at NOW + 2 h
    assert guard.evaluate(old, ARMED_POLICY, NOW + timedelta(hours=2, seconds=1))["state"] == guard.ARMED


def test_a_cap_of_none_leaves_that_limit_unwatched_and_the_scoped_cap_watches_fable():
    assert guard.evaluate(reading(99.0, 50.0), {**ARMED_POLICY, "session_cap": None}, NOW)["state"] == guard.ARMED
    fable = guard.evaluate(reading(10.0, 10.0, fable=92.0), {**ARMED_POLICY, "scoped_cap": 90}, NOW)
    assert fable["state"] == guard.HOLDING and fable["over"][0]["label"] == "Fable weekly"
    assert guard.evaluate(reading(10.0, 10.0, fable=92.0), ARMED_POLICY, NOW)["state"] == guard.ARMED


def test_a_limit_with_no_reset_time_holds_with_no_resume_time():
    doc = {"session": {"percent": 99.0, "resets_at": None}, "weekly": None, "scoped": []}
    verdict = guard.evaluate(doc, ARMED_POLICY, NOW)
    assert verdict["state"] == guard.HOLDING and verdict["resume_at"] is None


def test_an_episode_is_the_limits_over_their_caps_and_their_windows():
    first = guard.episode(guard.evaluate(reading(96.0, 90.0), ARMED_POLICY, NOW))
    same = guard.episode(guard.evaluate(reading(97.0, 91.0), ARMED_POLICY, NOW))
    new_window = guard.episode(guard.evaluate(
        reading(96.0, 90.0, session_resets=NOW + timedelta(hours=7)), ARMED_POLICY, NOW))
    assert first == same and first != new_window
    assert guard.episode(guard.evaluate(reading(), ARMED_POLICY, NOW)) == ""


def test_the_policy_defaults_come_from_settings():
    assert guard.DEFAULTS == {key: settings_mod.DEFAULTS[key] for key in guard.SETTING_KEYS}
    assert guard.policy_from(settings_mod.DEFAULTS.get) == {
        "enabled": False, "session_cap": 95, "weekly_cap": 90, "scoped_cap": None, "stagger_s": 120}


def test_settings_accept_only_sensible_guard_values():
    for key, good, bad in (("guard_session_cap", None, 0), ("guard_weekly_cap", 85, 101),
                           ("guard_scoped_cap", 50, 12.5), ("guard_resume_stagger_s", 0, 601),
                           ("guard_enabled", True, "yes")):
        settings_mod._check(key, good)
        try:
            settings_mod._check(key, bad)
        except ValueError:
            continue
        raise AssertionError(f"{key} accepted {bad!r}")


def test_a_reader_uses_the_policy_written_with_the_reading_else_the_settings_file(tmp_path):
    doc = {**reading(96.0, 50.0), "guard": {"policy": ARMED_POLICY}}
    assert guard.for_reader(doc, NOW, tmp_path)["state"] == guard.HOLDING
    own = reading(96.0, 50.0)                                           # a reading a reader fetched itself
    assert guard.for_reader(own, NOW, tmp_path)["state"] == guard.OFF   # no settings file: disarmed
    (tmp_path / guard.SETTINGS_NAME).write_text(json.dumps({"guard_enabled": True, "guard_session_cap": 90}),
                                                 encoding="utf-8")
    verdict = guard.for_reader(own, NOW, tmp_path)
    assert verdict["state"] == guard.HOLDING and verdict["policy"]["weekly_cap"] == 90  # the default


def test_a_reader_reads_the_settings_file_as_the_app_does(tmp_path):
    path = tmp_path / guard.SETTINGS_NAME
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"guard_enabled": True, "guard_weekly_cap": 70}).encode())
    policy = guard.policy_from_file(path)
    assert policy["enabled"] is True and policy["weekly_cap"] == 70           # a byte-order mark is fine
    path.write_text(json.dumps({"guard_enabled": "yes", "guard_weekly_cap": "70", "guard_session_cap": None}),
                    encoding="utf-8")
    policy = guard.policy_from_file(path)                                     # refused values: the defaults
    assert policy["enabled"] is False and policy["weekly_cap"] == 90 and policy["session_cap"] is None
    held = guard.evaluate(reading(96.0, 96.0), {"enabled": True, "session_cap": "95", "weekly_cap": 90}, NOW)
    assert [item["name"] for item in held["over"]] == ["weekly"]             # a cap that is not a number watches nothing


def test_limits_doc_reads_a_snapshot():
    snap = UsageSnapshot(fetched_at=NOW, limits=[
        Limit(kind="session", group="session", percent=12.0, severity=None, resets_at=NOW + timedelta(hours=1),
              is_active=True, label="SESSION"),
        Limit(kind="weekly_scoped", group="weekly", percent=3.0, severity=None, resets_at=NOW + timedelta(days=2),
              is_active=True, label="FABLE WEEKLY", scope_model="Fable"),
    ])
    doc = guard.limits_doc(snap)
    assert doc["session"]["percent"] == 12.0 and doc["weekly"] is None
    assert doc["scoped"] == [{"label": "FABLE WEEKLY", "model": "Fable", "percent": 3.0,
                              "resets_at": iso(NOW + timedelta(days=2))}]
    assert guard.limits_doc(None) == {"session": None, "weekly": None, "scoped": []}


# ── The session files (agent/sessions.py) ────────────────────────────────────────

def test_told_lists_the_sessions_told_of_this_episode(tmp_path):
    from usage_monitor.agent import sessions

    (tmp_path / sessions.SEEN_NAME).write_text(json.dumps({
        "s1": {"at": "x", "guard": {"episode": "weekly@W", "told_at": "2026-10-28T21:05:00Z"}},
        "s2": {"at": "x", "guard": {"episode": "session@S", "told_at": "2026-10-28T20:00:00Z"}},
        "s3": {"at": "x", "steps": {}},
    }), encoding="utf-8")
    assert sessions.told(tmp_path, "weekly@W") == [{"session_id": "s1", "told_at": "2026-10-28T21:05:00Z"}]
    assert sessions.told(tmp_path, "") == []


def test_a_session_told_of_two_limits_has_heard_of_either_one_alone(tmp_path):
    from usage_monitor.agent import sessions

    assert sessions.same_episode("session@S|weekly@W", "weekly@W")       # the session window reset: same episode
    assert sessions.same_episode("weekly@W", "weekly@W")
    assert not sessions.same_episode("weekly@W", "session@S|weekly@W")   # a limit newly over its cap: a new one
    assert not sessions.same_episode("weekly@W", "weekly@W2")            # a new week over its cap: a new one
    assert not sessions.same_episode(None, "weekly@W") and not sessions.same_episode("weekly@W", "")
    (tmp_path / sessions.SEEN_NAME).write_text(json.dumps({
        "s1": {"at": "x", "guard": {"episode": "session@S|weekly@W", "told_at": "2026-10-28T21:05:00Z"}},
    }), encoding="utf-8")
    assert [entry["session_id"] for entry in sessions.told(tmp_path, "weekly@W")] == ["s1"]


def test_waiting_lists_live_waits_and_removes_leftovers(tmp_path):
    import os

    from usage_monitor.agent import sessions

    mine = sessions.register_wait(tmp_path, "s1", NOW)
    folder = tmp_path / sessions.WAITING_DIR
    gone = folder / "999999.json"
    gone.write_text(json.dumps({"pid": 999999, "session_id": "gone", "since": "2026-10-28T20:00:00Z"}),
                    encoding="utf-8")
    reused = folder / "reused.json"   # a wait that was killed, whose id Windows gave to a live process (this one)
    reused.write_text(json.dumps({"pid": os.getpid(), "started": 1, "session_id": "old",
                                  "since": "2026-10-28T19:00:00Z"}), encoding="utf-8")
    torn = folder / "torn.json"       # a file being written: left alone
    torn.write_text('{"pid": ', encoding="utf-8")
    assert [(entry["pid"], entry["session_id"]) for entry in sessions.waiting(tmp_path)] == [(os.getpid(), "s1")]
    assert not gone.exists() and not reused.exists() and torn.exists()
    sessions.unregister_wait(mine)
    assert sessions.waiting(tmp_path) == []


def test_the_last_release_is_remembered(tmp_path):
    from usage_monitor.agent import sessions

    assert sessions.released_at(tmp_path) is None
    sessions.mark_released(tmp_path, NOW)
    assert sessions.released_at(tmp_path) == NOW


def test_resume_now_releases_waits_that_started_before_it(tmp_path):
    import time

    from usage_monitor.agent import sessions

    started = time.time() - 5
    assert not sessions.resumed_since(tmp_path, started)
    sessions.resume_now(tmp_path)
    assert sessions.resumed_since(tmp_path, started)
    assert not sessions.resumed_since(tmp_path, time.time() + 60)
