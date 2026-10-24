"""Tests for usage_monitor.data.alerts. Synthetic states only; the clock comes from state.updated_at."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from usage_monitor.data.alerts import AlertEngine
from usage_monitor.data.models import (
    ApiStatus, BurnRate, IngestProgress, Limit, MonitorState, PlanInfo, Spend, UsageSnapshot,
)
from usage_monitor.settings import Settings

# A fixed zone keeps the expected local times exact. Oct 22 2026 is a Thursday.
TZ = timezone(timedelta(hours=-5))
UTC = timezone.utc
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)               # 7:00 AM local
SESSION_RESET = datetime(2026, 10, 22, 21, 40, tzinfo=UTC)    # 4:40 PM local
NEXT_SESSION = SESSION_RESET + timedelta(hours=5)             # 9:40 PM local, same day
WEEK_RESET = datetime(2026, 10, 27, 21, 0, tzinfo=UTC)        # Tue 4:00 PM local
LATE_RESET = datetime(2026, 10, 30, 21, 0, tzinfo=UTC)        # a window still open at every clock the quiet-hours tests use


@pytest.fixture
def settings(tmp_path):
    s = Settings(tmp_path / "settings.json", save_delay_s=60)
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def engine(settings):
    return AlertEngine(settings, tz=TZ)


def session(pct: Any, resets: datetime | None = SESSION_RESET) -> Limit:
    return Limit(kind="session", group="session", percent=pct, severity=None,
                 resets_at=resets, is_active=True, label="SESSION")


def weekly(pct: Any, resets: datetime | None = WEEK_RESET) -> Limit:
    return Limit(kind="weekly_all", group="weekly", percent=pct, severity=None,
                 resets_at=resets, is_active=True, label="WEEKLY")


def fable(pct: Any, resets: datetime | None = WEEK_RESET, label: str = "FABLE WEEKLY") -> Limit:
    return Limit(kind="weekly_scoped", group="weekly", percent=pct, severity=None,
                 resets_at=resets, is_active=True, label=label, scope_model="Fable")


def state_at(*limits: Limit, now: datetime = NOW, status: ApiStatus = ApiStatus.LIVE,
             spend: Spend | None = None, usage: bool = True) -> MonitorState:
    snap = UsageSnapshot(fetched_at=now, limits=list(limits), spend=spend) if usage else None
    return MonitorState(
        status=status, status_detail="", usage=snap, usage_fetched_at=now,
        plan=PlanInfo(subscription_type="max", rate_limit_tier=None, name="Max 5x", monthly_price_usd=100.0),
        windows={}, burn=BurnRate(), unpriced_models=[], ingest=IngestProgress(), updated_at=now,
    )


def titles(alerts) -> list[str]:
    return [a.title for a in alerts]


def local(hour: int, minute: int = 0, day: int = 22) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)


# ── Baseline and the threshold ladder ─────────────────────────────────────────

def test_first_evaluation_records_a_baseline_and_returns_nothing(engine):
    st = state_at(session(95), weekly(85), fable(120))
    assert engine.evaluate(st) == []
    assert engine.evaluate(st) == []  # levels reached at startup never fire later


def test_each_threshold_alerts_once_on_the_way_up(engine):
    assert engine.evaluate(state_at(session(10))) == []
    at80 = engine.evaluate(state_at(session(80)))
    assert [(a.title, a.severity) for a in at80] == [("Session limit at 80%", "warning")]
    at90 = engine.evaluate(state_at(session(90)))
    assert [(a.title, a.severity) for a in at90] == [("Session limit at 90%", "warning")]
    at100 = engine.evaluate(state_at(session(100)))
    assert [(a.title, a.severity) for a in at100] == [("Session limit at 100%", "critical")]
    assert engine.evaluate(state_at(session(100))) == []


def test_a_jump_from_70_to_95_gives_one_alert_for_90(engine):
    engine.evaluate(state_at(session(70)))
    assert titles(engine.evaluate(state_at(session(95)))) == ["Session limit at 90%"]
    assert titles(engine.evaluate(state_at(session(100)))) == ["Session limit at 100%"]


def test_no_repeat_in_the_same_window_even_after_a_dip(engine):
    engine.evaluate(state_at(session(10)))
    assert titles(engine.evaluate(state_at(session(85)))) == ["Session limit at 80%"]
    assert engine.evaluate(state_at(session(60))) == []
    assert engine.evaluate(state_at(session(85))) == []
    assert titles(engine.evaluate(state_at(session(92)))) == ["Session limit at 90%"]


def test_the_alert_carries_its_kind_key_and_local_reset_time(engine):
    engine.evaluate(state_at(session(10)))
    (alert,) = engine.evaluate(state_at(session(85)))
    assert alert.kind == "threshold"
    assert alert.message == "Resets 4:40 PM"
    assert alert.key == "threshold:session:SESSION:2026-10-22T21:40:00+00:00:80"


def test_a_window_identity_is_the_reset_to_the_minute(engine):
    engine.evaluate(state_at(session(10)))
    assert titles(engine.evaluate(state_at(session(85)))) == ["Session limit at 80%"]
    same_minute = SESSION_RESET + timedelta(seconds=30)
    assert engine.evaluate(state_at(session(85, same_minute))) == []


def test_sub_second_jitter_across_a_minute_boundary_is_one_window(engine):
    edge = datetime(2026, 10, 22, 21, 40, 0, 200_000, tzinfo=UTC)
    before = edge - timedelta(milliseconds=300)  # 21:39:59.9
    engine.evaluate(state_at(session(95, edge)))
    for _ in range(3):
        assert engine.evaluate(state_at(session(95, before))) == []
        assert engine.evaluate(state_at(session(95, edge))) == []


def test_the_alert_key_stays_stable_while_the_reset_instant_jitters(engine):
    edge = datetime(2026, 10, 22, 21, 40, 0, 200_000, tzinfo=UTC)
    engine.evaluate(state_at(session(10, edge - timedelta(milliseconds=300))))
    (alert,) = engine.evaluate(state_at(session(85, edge)))
    assert alert.key == "threshold:session:SESSION:2026-10-22T21:39:00+00:00:80"


def test_a_real_new_window_still_resets_after_jitter(engine):
    engine.evaluate(state_at(session(10, SESSION_RESET - timedelta(milliseconds=100))))
    engine.evaluate(state_at(session(85, SESSION_RESET + timedelta(milliseconds=100))))
    assert titles(engine.evaluate(state_at(session(20, NEXT_SESSION)))) == ["Session limit reset"]


def test_a_threshold_added_mid_window_below_the_current_level_is_not_announced(engine, settings):
    engine.evaluate(state_at(session(10)))
    assert titles(engine.evaluate(state_at(session(95)))) == ["Session limit at 90%"]
    settings.set("alert_thresholds", [50, 80])
    assert engine.evaluate(state_at(session(95))) == []
    assert engine.evaluate(state_at(session(60))) == []  # a dip does not re-arm it either
    assert engine.evaluate(state_at(session(96))) == []


def test_a_threshold_added_above_the_high_water_mark_still_alerts(engine, settings):
    engine.evaluate(state_at(session(10)))
    engine.evaluate(state_at(session(95)))
    settings.set("alert_thresholds", [50, 98])
    assert titles(engine.evaluate(state_at(session(99)))) == ["Session limit at 98%"]


def test_a_threshold_added_below_the_baseline_level_is_not_announced(engine, settings):
    engine.evaluate(state_at(session(95)))
    settings.set("alert_thresholds", [50, 80])
    assert engine.evaluate(state_at(session(95))) == []


def test_thresholds_come_from_settings_on_every_evaluation(engine, settings):
    settings.set("alert_thresholds", [50, 75])
    engine.evaluate(state_at(session(10)))
    assert titles(engine.evaluate(state_at(session(60)))) == ["Session limit at 50%"]
    settings.set("alert_thresholds", [70])
    assert titles(engine.evaluate(state_at(session(75)))) == ["Session limit at 70%"]


# ── Windows and resets ────────────────────────────────────────────────────────

def test_a_new_window_rearms_the_thresholds(engine):
    engine.evaluate(state_at(session(10)))
    assert titles(engine.evaluate(state_at(session(85)))) == ["Session limit at 80%"]
    assert titles(engine.evaluate(state_at(session(20, NEXT_SESSION)))) == ["Session limit reset"]
    assert titles(engine.evaluate(state_at(session(85, NEXT_SESSION)))) == ["Session limit at 80%"]


def test_a_new_window_after_a_high_percent_gives_reset_then_the_threshold(engine):
    engine.evaluate(state_at(session(10)))
    engine.evaluate(state_at(session(85)))
    alerts = engine.evaluate(state_at(session(85, NEXT_SESSION)))
    assert titles(alerts) == ["Session limit reset", "Session limit at 80%"]


@pytest.mark.parametrize("previous, expect_reset", [(50, False), (79.9, False), (80, True), (100, True)])
def test_reset_alert_needs_a_previous_percent_at_the_lowest_threshold(engine, previous, expect_reset):
    engine.evaluate(state_at(session(10)))
    engine.evaluate(state_at(session(previous)))
    alerts = engine.evaluate(state_at(session(0, NEXT_SESSION)))
    assert ("Session limit reset" in titles(alerts)) is expect_reset


def test_reset_alert_shows_the_new_reset_time(engine):
    engine.evaluate(state_at(session(10)))
    engine.evaluate(state_at(session(85)))
    (alert,) = engine.evaluate(state_at(session(0, NEXT_SESSION)))
    assert alert.kind == "reset"
    assert alert.severity == "info"
    assert alert.message == "Resets 9:40 PM"
    assert alert.key == "reset:session:SESSION:2026-10-23T02:40:00+00:00"


def test_a_limit_first_seen_after_the_baseline_is_baseline_too(engine):
    engine.evaluate(state_at(session(10)))
    assert engine.evaluate(state_at(session(10), weekly(95))) == []
    assert engine.evaluate(state_at(session(10), weekly(96))) == []


# ── A window that ends: the reset is announced by the clock, not by the next window ──

def at(minutes_after_reset: float) -> datetime:
    return SESSION_RESET + timedelta(minutes=minutes_after_reset)


def climb_to_100(engine) -> None:
    """A session that ends at SESSION_RESET after reaching 100%: baseline, then 80/90/100 alerts on the way."""
    for pct, minutes in ((60, -180), (96, -90), (100, -30)):
        engine.evaluate(state_at(session(pct), now=at(minutes)))


@pytest.mark.parametrize("after_end", [
    pytest.param(lambda: session(0, None), id="the API keeps the session but without a reset time"),
    pytest.param(lambda: None, id="the API drops the session"),
])
def test_a_session_reset_is_announced_when_the_window_ends_even_if_the_api_shows_no_new_one(engine, after_end):
    climb_to_100(engine)
    limit = after_end()
    state = lambda minutes: state_at(*([limit] if limit is not None else []), now=at(minutes))
    assert engine.evaluate(state(0.5)) == []                      # inside the 60 s tolerance: still the same window
    (alert,) = engine.evaluate(state(1.5))
    assert (alert.kind, alert.title, alert.severity) == ("reset", "Session limit reset", "info")
    assert alert.message == "Usage is back to 0%."
    assert alert.key == "reset:session:SESSION:2026-10-22T21:40:00+00:00"
    for minutes in (5, 45, 120, 300):
        assert engine.evaluate(state(minutes)) == []              # once, not on every poll


def test_a_session_reset_is_announced_when_the_api_keeps_showing_the_window_that_ended(engine):
    climb_to_100(engine)
    assert engine.evaluate(state_at(session(100), now=at(0.5))) == []
    assert titles(engine.evaluate(state_at(session(100), now=at(1.5)))) == ["Session limit reset"]
    for minutes in (10, 60, 200):                                 # the stale 100% is neither a new alert nor a new reset
        assert engine.evaluate(state_at(session(100), now=at(minutes))) == []


def test_the_first_window_after_an_announced_reset_starts_a_quiet_baseline(engine):
    climb_to_100(engine)
    assert titles(engine.evaluate(state_at(session(100), now=at(2)))) == ["Session limit reset"]
    new_reset = at(300 + 120)
    assert engine.evaluate(state_at(session(2, new_reset), now=at(300))) == []     # no second reset alert
    assert titles(engine.evaluate(state_at(session(85, new_reset), now=at(330)))) == ["Session limit at 80%"]
    assert titles(engine.evaluate(state_at(session(5, new_reset + timedelta(hours=5)), now=at(500)))) == [
        "Session limit reset"]                                                      # and the ladder is armed again


def test_a_new_window_in_the_same_state_as_the_ended_one_still_announces_the_reset_once(engine):
    climb_to_100(engine)
    new_reset = at(300)
    alerts = engine.evaluate(state_at(session(3, new_reset), now=at(200)))          # the clock is past the old end
    assert [(a.title, a.message) for a in alerts] == [("Session limit reset", "Resets 9:40 PM")]  # the new window's end
    assert engine.evaluate(state_at(session(3, new_reset), now=at(210))) == []


def test_a_window_that_never_reached_the_lowest_threshold_ends_quietly(engine):
    engine.evaluate(state_at(session(10), now=at(-120)))
    engine.evaluate(state_at(session(50), now=at(-60)))
    assert engine.evaluate(state_at(session(50), now=at(5))) == []
    assert engine.evaluate(state_at(session(2, at(305)), now=at(300))) == []


def test_every_limit_ends_on_its_own_clock(engine):
    engine.evaluate(state_at(session(10), weekly(10), now=at(-300)))
    engine.evaluate(state_at(session(90), weekly(95), now=at(-200)))
    assert titles(engine.evaluate(state_at(session(90), weekly(95), now=at(2)))) == ["Session limit reset"]
    week_end = WEEK_RESET + timedelta(minutes=2)
    assert titles(engine.evaluate(state_at(session(5, at(300)), weekly(95), now=week_end))) == ["Weekly limit reset"]


def test_a_limit_first_seen_after_its_window_ended_is_baseline_not_a_reset(engine):
    """The app starts, or the API answers, after the window ran out: nothing happened that the user is waiting for."""
    assert engine.evaluate(state_at(session(100), now=at(30))) == []
    assert engine.evaluate(state_at(session(100), now=at(60))) == []
    assert engine.evaluate(state_at(session(1, at(330)), now=at(90))) == []
    assert titles(engine.evaluate(state_at(session(85, at(330)), now=at(100)))) == ["Session limit at 80%"]


def test_a_reset_that_comes_while_alerts_are_withheld_is_not_announced_later(engine, settings):
    climb_to_100(engine)
    assert engine.evaluate(state_at(session(100), now=at(2)), suppressed=True) == []
    assert engine.evaluate(state_at(session(100), now=at(3))) == []
    settings.set("alerts_enabled", False)
    engine.evaluate(state_at(session(10, at(300)), now=at(150)))
    engine.evaluate(state_at(session(97, at(300)), now=at(200)))
    assert engine.evaluate(state_at(session(97, at(300)), now=at(302))) == []
    settings.set("alerts_enabled", True)
    assert engine.evaluate(state_at(session(97, at(300)), now=at(303))) == []


def test_the_reset_alert_can_be_switched_off_on_its_own(engine, settings):
    settings.set("alert_on_reset", False)
    climb_to_100(engine)
    assert engine.evaluate(state_at(session(100), now=at(2))) == []


@pytest.mark.parametrize("status", [s for s in ApiStatus if s is not ApiStatus.LIVE])
def test_a_reset_that_comes_while_the_status_is_not_live_is_announced_by_the_next_live_state(engine, status):
    climb_to_100(engine)
    assert engine.evaluate(state_at(session(100), now=at(5), status=status)) == []
    assert titles(engine.evaluate(state_at(session(100), now=at(20)))) == ["Session limit reset"]


def test_the_ended_window_does_not_raise_threshold_alerts_for_a_late_reading(engine):
    engine.evaluate(state_at(session(10), now=at(-120)))
    assert engine.evaluate(state_at(session(85), now=at(5))) == []                  # 85% of a window that is over
    assert engine.evaluate(state_at(session(97), now=at(10))) == []


# ── Message formats ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("limit_fn, resets, expected", [
    (session, datetime(2026, 10, 22, 13, 5, tzinfo=UTC), "Resets 8:05 AM"),
    (session, datetime(2026, 10, 23, 5, 0, tzinfo=UTC), "Resets 12:00 AM"),
    (session, datetime(2026, 10, 22, 17, 30, tzinfo=UTC), "Resets 12:30 PM"),
    (session, SESSION_RESET, "Resets 4:40 PM"),
    (weekly, WEEK_RESET, "Resets Tue 4:00 PM"),
    (weekly, datetime(2026, 10, 26, 5, 0, tzinfo=UTC), "Resets Mon 12:00 AM"),
    (fable, datetime(2026, 10, 25, 23, 15, tzinfo=UTC), "Resets Sun 6:15 PM"),
])
def test_reset_times_are_local_without_a_leading_zero(engine, limit_fn, resets, expected):
    engine.evaluate(state_at(limit_fn(10, resets)))
    (alert,) = engine.evaluate(state_at(limit_fn(85, resets)))
    assert alert.message == expected


def test_weekly_and_scoped_limits_use_their_labels(engine):
    engine.evaluate(state_at(weekly(10), fable(10)))
    alerts = engine.evaluate(state_at(weekly(100), fable(85)))
    assert [(a.title, a.severity, a.message) for a in alerts] == [
        ("Weekly limit at 100%", "critical", "Resets Tue 4:00 PM"),
        ("Fable weekly limit at 80%", "warning", "Resets Tue 4:00 PM"),
    ]


def test_a_scoped_limit_without_a_label_falls_back_to_its_model(engine):
    engine.evaluate(state_at(fable(10, label="")))
    assert titles(engine.evaluate(state_at(fable(85, label="")))) == ["Fable limit at 80%"]


def test_scoped_and_session_thresholds_are_tracked_separately(engine):
    engine.evaluate(state_at(session(10), fable(10)))
    assert titles(engine.evaluate(state_at(session(85), fable(10)))) == ["Session limit at 80%"]
    assert titles(engine.evaluate(state_at(session(85), fable(85)))) == ["Fable weekly limit at 80%"]


# ── Usage credits ─────────────────────────────────────────────────────────────

def credits(used: float | None, *, enabled: bool = True, limit: float | None = 50.0,
            currency: str | None = "USD") -> Spend:
    return Spend(enabled=enabled, used=used, limit=limit, currency=currency)


def test_credits_alert_once_per_local_day(engine):
    assert engine.evaluate(state_at(session(10), spend=credits(10), now=local(12))) == []
    (alert,) = engine.evaluate(state_at(session(10), spend=credits(12), now=local(13)))
    assert (alert.kind, alert.severity, alert.title) == ("credits", "info", "Usage credits in use")
    assert alert.message == "Used 12.00 of 50.00 USD"
    assert engine.evaluate(state_at(session(10), spend=credits(15), now=local(14))) == []
    # 03:00 UTC on the 23rd is 10:00 PM local on the 22nd: still the same local day.
    late = datetime(2026, 10, 23, 3, 0, tzinfo=UTC)
    assert engine.evaluate(state_at(session(10), spend=credits(16), now=late)) == []
    alerts = engine.evaluate(state_at(session(10), spend=credits(17), now=local(9, day=23)))
    assert titles(alerts) == ["Usage credits in use"]


def test_credits_message_without_a_limit_or_currency(engine):
    engine.evaluate(state_at(session(10), spend=credits(1, limit=None, currency=None), now=local(9)))
    (alert,) = engine.evaluate(state_at(session(10), spend=credits(3.5, limit=None, currency=None), now=local(10)))
    assert alert.message == "Used 3.50"


def test_credits_only_alert_when_enabled_and_the_state_still_advances(engine):
    engine.evaluate(state_at(session(10), spend=credits(10), now=local(9)))
    assert engine.evaluate(state_at(session(10), spend=credits(20, enabled=False), now=local(10))) == []
    alerts = engine.evaluate(state_at(session(10), spend=credits(25), now=local(11)))
    assert titles(alerts) == ["Usage credits in use"]


def test_credits_without_spend_or_a_used_value_do_nothing(engine):
    assert engine.evaluate(state_at(session(10), spend=None)) == []
    assert engine.evaluate(state_at(session(10), spend=credits(None))) == []
    assert engine.evaluate(state_at(session(10), spend=credits(None), now=local(9))) == []


# ── Switches, quiet hours and suppression ─────────────────────────────────────

def test_suppressed_evaluations_advance_state_without_a_burst_later(engine):
    engine.evaluate(state_at(session(10)))
    assert engine.evaluate(state_at(session(85)), suppressed=True) == []
    assert engine.evaluate(state_at(session(85))) == []
    assert titles(engine.evaluate(state_at(session(95)))) == ["Session limit at 90%"]


def test_a_suppressed_reset_is_not_announced_later(engine):
    engine.evaluate(state_at(session(10)))
    engine.evaluate(state_at(session(85)))
    assert engine.evaluate(state_at(session(0, NEXT_SESSION)), suppressed=True) == []
    assert engine.evaluate(state_at(session(0, NEXT_SESSION))) == []


def test_suppressed_credits_use_the_days_allowance(engine):
    engine.evaluate(state_at(session(10), spend=credits(10), now=local(9)))
    assert engine.evaluate(state_at(session(10), spend=credits(12), now=local(10)), suppressed=True) == []
    assert engine.evaluate(state_at(session(10), spend=credits(14), now=local(11))) == []


def test_master_switch_off_advances_state_without_alerts(engine, settings):
    settings.set("alerts_enabled", False)
    engine.evaluate(state_at(session(10)))
    assert engine.evaluate(state_at(session(85))) == []
    settings.set("alerts_enabled", True)
    assert engine.evaluate(state_at(session(85))) == []
    assert titles(engine.evaluate(state_at(session(95)))) == ["Session limit at 90%"]


def test_threshold_switch_off_advances_state_without_alerts(engine, settings):
    settings.set("alert_on_threshold", False)
    engine.evaluate(state_at(session(10)))
    assert engine.evaluate(state_at(session(85))) == []
    settings.set("alert_on_threshold", True)
    assert titles(engine.evaluate(state_at(session(95)))) == ["Session limit at 90%"]


def test_reset_switch_off_suppresses_only_the_reset_alert(engine, settings):
    settings.set("alert_on_reset", False)
    engine.evaluate(state_at(session(10)))
    assert titles(engine.evaluate(state_at(session(85)))) == ["Session limit at 80%"]
    assert engine.evaluate(state_at(session(20, NEXT_SESSION))) == []
    assert titles(engine.evaluate(state_at(session(85, NEXT_SESSION)))) == ["Session limit at 80%"]


def test_credits_switch_off_suppresses_the_credits_alert(engine, settings):
    settings.set("alert_on_credits", False)
    engine.evaluate(state_at(session(10), spend=credits(10), now=local(9)))
    assert engine.evaluate(state_at(session(10), spend=credits(12), now=local(10))) == []


def test_quiet_hours_wrap_midnight_and_consume_the_crossing(engine, settings):
    settings.set("alert_quiet_hours", {"start": "22:00", "end": "07:00"})
    engine.evaluate(state_at(session(10, LATE_RESET), now=local(12)))
    assert engine.evaluate(state_at(session(85, LATE_RESET), now=local(23, 30))) == []
    assert engine.evaluate(state_at(session(95, LATE_RESET), now=local(6, 59, day=23))) == []
    assert engine.evaluate(state_at(session(95, LATE_RESET), now=local(7, 0, day=23))) == []
    assert titles(engine.evaluate(state_at(session(100, LATE_RESET), now=local(12, day=23)))) == [
        "Session limit at 100%"]


@pytest.mark.parametrize("quiet, hour, minute, expect_alert", [
    ({"start": "22:00", "end": "07:00"}, 21, 59, True),
    ({"start": "22:00", "end": "07:00"}, 22, 0, False),
    ({"start": "22:00", "end": "07:00"}, 6, 59, False),
    ({"start": "22:00", "end": "07:00"}, 7, 0, True),
    ({"start": "09:00", "end": "17:00"}, 8, 59, True),
    ({"start": "09:00", "end": "17:00"}, 9, 0, False),
    ({"start": "09:00", "end": "17:00"}, 16, 59, False),
    ({"start": "09:00", "end": "17:00"}, 17, 0, True),
    ({"start": "08:00", "end": "08:00"}, 8, 0, True),  # equal start and end: no quiet hours
])
def test_quiet_hours_edges(engine, settings, quiet, hour, minute, expect_alert):
    settings.set("alert_quiet_hours", quiet)
    engine.evaluate(state_at(session(10, LATE_RESET), now=local(4)))
    alerts = engine.evaluate(state_at(session(85, LATE_RESET), now=local(hour, minute)))
    assert (titles(alerts) == ["Session limit at 80%"]) is expect_alert


@pytest.mark.parametrize("status", [s for s in ApiStatus if s is not ApiStatus.LIVE])
def test_a_status_other_than_live_does_nothing_and_keeps_state(engine, status):
    engine.evaluate(state_at(session(10)))
    assert engine.evaluate(state_at(session(95), status=status)) == []
    assert titles(engine.evaluate(state_at(session(95)))) == ["Session limit at 90%"]


def test_live_without_usage_does_nothing(engine):
    engine.evaluate(state_at(session(10)))
    assert engine.evaluate(state_at(usage=False)) == []
    assert titles(engine.evaluate(state_at(session(85)))) == ["Session limit at 80%"]


# ── Odd values never raise ────────────────────────────────────────────────────

def test_none_nan_and_missing_reset_values_are_skipped(engine):
    engine.evaluate(state_at(session(10), weekly(10)))
    alerts = engine.evaluate(state_at(
        session(None), weekly(85), fable(float("nan"), resets=WEEK_RESET),
        Limit(kind="weekly_scoped", group=None, percent=95, severity=None, resets_at=None,
              is_active=True, label="ODD"),
    ))
    assert titles(alerts) == ["Weekly limit at 80%"]


def test_a_non_numeric_percent_is_skipped_and_never_raises(engine):
    engine.evaluate(state_at(session(10)))
    assert engine.evaluate(state_at(session("high"))) == []
    assert engine.evaluate(None) == []  # type: ignore[arg-type]
