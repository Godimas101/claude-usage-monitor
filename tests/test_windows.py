"""Tests for usage_monitor/data/windows.py. Synthetic and offline.

The DST cases use test-only zones written out here (America/Toronto's US rules and a zone whose
clock skips midnight), so no tz database is needed.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone, tzinfo

import pytest

from usage_monitor.data.models import BurnRate, Limit, UsageSnapshot
from usage_monitor.data.windows import burn_rate, cycle, month, session, today, week

UTC = timezone.utc
HOUR = timedelta(hours=1)


def utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=UTC)


def at(hour: int, minute: int = 0, second: int = 0, microsecond: int = 0) -> datetime:
    """Synthetic UTC instant on 2026-10-21."""
    return datetime(2026, 10, 21, hour, minute, second, microsecond, tzinfo=UTC)


# ── Test-only zones ──────────────────────────────────────────────────────────

def _nth_sunday(year: int, mon: int, n: int) -> date:
    first = date(year, mon, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def _toronto_dst(dt: datetime) -> bool:
    """America/Toronto (US rules since 2007), DST from 02:00 EST on the 2nd Sunday of March to
    02:00 EDT on the 1st Sunday of November. Wall times in the spring gap and the autumn overlap
    follow PEP 495: fold=0 keeps the pre-transition offset in the gap, and fold=0 picks the
    first (EDT) occurrence in the overlap."""
    wall = dt.replace(tzinfo=None)
    spring_gap = datetime.combine(_nth_sunday(wall.year, 3, 2), time(2))
    fall_overlap = datetime.combine(_nth_sunday(wall.year, 11, 1), time(1))
    if wall < spring_gap:
        return False
    if wall < spring_gap + HOUR:
        return dt.fold == 1
    if wall < fall_overlap:
        return True
    if wall < fall_overlap + HOUR:
        return dt.fold == 0
    return False


class TorontoZone(tzinfo):
    def utcoffset(self, dt: datetime | None) -> timedelta:
        return timedelta(hours=-4) if dt is not None and _toronto_dst(dt) else timedelta(hours=-5)

    def dst(self, dt: datetime | None) -> timedelta:
        return HOUR if dt is not None and _toronto_dst(dt) else timedelta(0)

    def tzname(self, dt: datetime | None) -> str:
        return "EDT" if dt is not None and _toronto_dst(dt) else "EST"

    def fromutc(self, dt: datetime) -> datetime:
        wall = dt.replace(tzinfo=None)
        spring_utc = datetime.combine(_nth_sunday(wall.year, 3, 2), time(7))  # 02:00 EST
        fall_utc = datetime.combine(_nth_sunday(wall.year, 11, 1), time(6))   # 02:00 EDT
        if spring_utc <= wall < fall_utc:
            return (wall - timedelta(hours=4)).replace(tzinfo=self)
        fold = 1 if fall_utc <= wall < fall_utc + HOUR else 0
        return (wall - timedelta(hours=5)).replace(tzinfo=self, fold=fold)


class MidnightSkipZone(tzinfo):
    """The clock jumps 00:00 -> 01:00 at local midnight on 2026-09-06 (UTC-5 to UTC-4)."""
    _JUMP_WALL = datetime(2026, 9, 6, 0)
    _JUMP_UTC = datetime(2026, 9, 6, 5)

    def utcoffset(self, dt: datetime | None) -> timedelta:
        wall = dt.replace(tzinfo=None)
        if wall < self._JUMP_WALL:
            return timedelta(hours=-5)
        if wall < self._JUMP_WALL + HOUR:
            return timedelta(hours=-4) if dt.fold else timedelta(hours=-5)
        return timedelta(hours=-4)

    def dst(self, dt: datetime | None) -> timedelta:
        return HOUR if self.utcoffset(dt) == timedelta(hours=-4) else timedelta(0)

    def tzname(self, dt: datetime | None) -> str:
        return "TST"

    def fromutc(self, dt: datetime) -> datetime:
        wall = dt.replace(tzinfo=None)
        offset = timedelta(hours=-5) if wall < self._JUMP_UTC else timedelta(hours=-4)
        return (wall + offset).replace(tzinfo=self)


TORONTO = TorontoZone()


def local(*parts: int, fold: int = 0) -> datetime:
    """Instant at this wall-clock time in America/Toronto, as UTC."""
    return datetime(*parts, tzinfo=TORONTO, fold=fold).astimezone(UTC)


# ── The test zones themselves ────────────────────────────────────────────────

def test_toronto_zone_offsets_follow_us_rules():
    assert datetime(2026, 3, 8, 1, 59, tzinfo=TORONTO).utcoffset() == timedelta(hours=-5)
    assert datetime(2026, 3, 8, 2, 30, tzinfo=TORONTO, fold=0).utcoffset() == timedelta(hours=-5)
    assert datetime(2026, 3, 8, 2, 30, tzinfo=TORONTO, fold=1).utcoffset() == timedelta(hours=-4)
    assert datetime(2026, 3, 8, 3, 0, tzinfo=TORONTO).utcoffset() == timedelta(hours=-4)
    assert datetime(2026, 11, 1, 1, 30, tzinfo=TORONTO, fold=0).utcoffset() == timedelta(hours=-4)
    assert datetime(2026, 11, 1, 1, 30, tzinfo=TORONTO, fold=1).utcoffset() == timedelta(hours=-5)
    assert datetime(2026, 11, 1, 2, 0, tzinfo=TORONTO).utcoffset() == timedelta(hours=-5)


@pytest.mark.parametrize("instant", [
    utc(2026, 3, 8, 6, 59), utc(2026, 3, 8, 7), utc(2026, 11, 1, 5, 59),
    utc(2026, 11, 1, 6), utc(2026, 11, 1, 6, 30), utc(2026, 11, 1, 7),
])
def test_toronto_zone_round_trips_utc(instant):
    assert instant.astimezone(TORONTO).astimezone(UTC) == instant


def test_repeated_one_am_is_the_second_occurrence_with_fold_one():
    wall = utc(2026, 11, 1, 6, 30).astimezone(TORONTO)
    assert (wall.hour, wall.minute, wall.fold) == (1, 30, 1)


# ── today() ──────────────────────────────────────────────────────────────────

def test_today_uses_the_local_day_not_the_utc_day():
    # v1 bucketed "today" at UTC midnight. 20:30 EDT on Oct 21 is already Oct 22 in UTC.
    assert today(utc(2026, 10, 22, 0, 30), tz=TORONTO) == (
        utc(2026, 10, 21, 4), utc(2026, 10, 22, 4))


def test_today_midnight_edges():
    assert today(local(2026, 10, 21, 23, 59, 59, 999_999), tz=TORONTO) == (
        local(2026, 10, 21), local(2026, 10, 22))
    # Exactly local midnight belongs to the new day.
    assert today(local(2026, 10, 22), tz=TORONTO) == (local(2026, 10, 22), local(2026, 10, 23))


def test_today_on_spring_forward_day_is_23_hours():
    start, end = today(local(2026, 3, 8, 12), tz=TORONTO)
    assert start == utc(2026, 3, 8, 5)  # 00:00 EST
    assert end == utc(2026, 3, 9, 4)    # 00:00 EDT
    assert end - start == timedelta(hours=23)


def test_today_on_fall_back_day_is_25_hours():
    start, end = today(local(2026, 11, 1, 12), tz=TORONTO)
    assert start == utc(2026, 11, 1, 4)  # 00:00 EDT
    assert end == utc(2026, 11, 2, 5)    # 00:00 EST
    assert end - start == timedelta(hours=25)


@pytest.mark.parametrize("fold", [0, 1])
def test_both_repeated_one_am_instants_belong_to_the_same_day(fold):
    assert today(local(2026, 11, 1, 1, 30, fold=fold), tz=TORONTO) == (
        local(2026, 11, 1), local(2026, 11, 2))


@pytest.mark.parametrize("first_day", [date(2026, 3, 6), date(2026, 10, 30)])
def test_consecutive_days_tile_without_gaps_across_dst(first_day):
    previous_end = None
    for offset in range(5):
        day = first_day + timedelta(days=offset)
        start, end = today(local(day.year, day.month, day.day, 12), tz=TORONTO)
        if previous_end is not None:
            assert start == previous_end
        previous_end = end


def test_day_that_starts_at_a_skipped_midnight_begins_at_the_transition():
    zone = MidnightSkipZone()
    noon = datetime(2026, 9, 6, 12, tzinfo=zone).astimezone(UTC)  # 16:00 UTC
    start, end = today(noon, tz=zone)
    assert start == utc(2026, 9, 6, 5)  # 00:00 never exists; the day opens at the jump
    assert end == utc(2026, 9, 7, 4)    # 00:00 at UTC-4
    assert end - start == timedelta(hours=23)


# ── month() ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("now, expected", [
    (local(2026, 2, 1), (local(2026, 2, 1), local(2026, 3, 1))),
    (local(2026, 2, 28, 23, 59, 59), (local(2026, 2, 1), local(2026, 3, 1))),
    (local(2026, 3, 1), (local(2026, 3, 1), local(2026, 4, 1))),
    (local(2026, 3, 31, 23, 59), (local(2026, 3, 1), local(2026, 4, 1))),
    (local(2026, 4, 1), (local(2026, 4, 1), local(2026, 5, 1))),
    (local(2026, 12, 31, 23, 59), (local(2026, 12, 1), local(2027, 1, 1))),
    (local(2028, 2, 29, 12), (local(2028, 2, 1), local(2028, 3, 1))),
])
def test_month_boundaries(now, expected):
    assert month(now, tz=TORONTO) == expected


@pytest.mark.parametrize("now, hours", [
    (local(2026, 2, 15), 28 * 24),    # no DST change
    (local(2026, 3, 15), 31 * 24 - 1),  # spring forward
    (local(2026, 11, 15), 30 * 24 + 1),  # fall back
    (local(2028, 2, 15), 29 * 24),    # leap February
])
def test_month_length_in_hours_reflects_dst(now, hours):
    start, end = month(now, tz=TORONTO)
    assert end - start == timedelta(hours=hours)


# ── cycle() ──────────────────────────────────────────────────────────────────

def test_billing_day_31_clamps_to_feb_28_in_a_common_year():
    # Renewals: Jan 31, Feb 28 (clamped), Mar 31.
    assert cycle(local(2026, 2, 27, 12), 31, tz=TORONTO) == (local(2026, 1, 31), local(2026, 2, 28))
    assert cycle(local(2026, 2, 28, 0), 31, tz=TORONTO) == (local(2026, 2, 28), local(2026, 3, 31))


def test_billing_day_31_clamps_to_feb_29_in_a_leap_year():
    assert cycle(local(2028, 2, 28, 12), 31, tz=TORONTO) == (local(2028, 1, 31), local(2028, 2, 29))
    assert cycle(local(2028, 2, 29, 12), 31, tz=TORONTO) == (local(2028, 2, 29), local(2028, 3, 31))


def test_billing_day_29_renews_on_feb_28_in_2027_and_feb_29_in_2028():
    assert cycle(local(2027, 2, 27, 12), 29, tz=TORONTO) == (local(2027, 1, 29), local(2027, 2, 28))
    assert cycle(local(2028, 2, 28, 12), 29, tz=TORONTO) == (local(2028, 1, 29), local(2028, 2, 29))


def test_billing_day_31_in_a_thirty_day_month_renews_on_the_last_day():
    assert cycle(local(2026, 4, 29, 12), 31, tz=TORONTO) == (local(2026, 3, 31), local(2026, 4, 30))
    assert cycle(local(2026, 4, 30, 12), 31, tz=TORONTO) == (local(2026, 4, 30), local(2026, 5, 31))


def test_cycle_starts_at_renewal_midnight_and_ends_at_the_next_one():
    assert cycle(local(2026, 3, 15, 0), 15, tz=TORONTO) == (local(2026, 3, 15), local(2026, 4, 15))
    assert cycle(local(2026, 3, 14, 23, 59), 15, tz=TORONTO) == (
        local(2026, 2, 15), local(2026, 3, 15))


def test_cycle_across_spring_forward_is_local_midnight_on_both_ends():
    start, end = cycle(local(2026, 3, 8, 12), 8, tz=TORONTO)
    assert (start, end) == (local(2026, 3, 8), local(2026, 4, 8))
    assert start == utc(2026, 3, 8, 5)
    assert end - start == timedelta(hours=31 * 24 - 1)


@pytest.mark.parametrize("day", [1, 31])
def test_cycle_accepts_the_boundary_days(day):
    assert cycle(local(2026, 3, 15, 12), day, tz=TORONTO) is not None


@pytest.mark.parametrize("bad", [None, 0, 32, -1, True, "15", 15.0])
def test_cycle_is_none_for_missing_or_invalid_billing_day(bad):
    assert cycle(local(2026, 3, 15, 12), bad, tz=TORONTO) is None


# ── session(): API path ──────────────────────────────────────────────────────

def _limit(kind: str, resets_at: datetime | None) -> Limit:
    group = "session" if kind == "session" else "weekly"
    return Limit(kind=kind, group=group, percent=10.0, severity="normal",
                 resets_at=resets_at, is_active=True, label=kind.upper())


def _usage(*limits: Limit) -> UsageSnapshot:
    return UsageSnapshot(fetched_at=at(12), limits=list(limits))


def test_session_uses_the_limit_window_when_resets_at_is_known():
    usage = _usage(_limit("session", at(14)))
    recent = [at(12, 30)]  # the fallback would open a block at 12:00 instead
    assert session(at(12, 45), usage, recent) == (at(9), at(14))


def test_session_api_window_is_reported_in_utc():
    plus_two = timezone(timedelta(hours=2))
    usage = _usage(_limit("session", datetime(2026, 10, 21, 16, tzinfo=plus_two)))
    start, end = session(at(12), usage, [])
    assert (start, end) == (at(9), at(14))
    assert start.utcoffset() == timedelta(0) and end.utcoffset() == timedelta(0)


def test_session_limit_without_resets_at_falls_back_to_blocks():
    usage = _usage(_limit("session", None))
    assert session(at(12), usage, [at(10, 10)]) == (at(10), at(15))


def test_session_ignores_the_weekly_limit():
    usage = _usage(_limit("weekly_all", utc(2026, 10, 23, 12)))
    assert session(at(12), usage, [at(10, 10)]) == (at(10), at(15))


# ── session(): block fallback ────────────────────────────────────────────────

def test_block_floors_the_first_request_to_the_hour():
    assert session(at(11), None, [at(10, 37)]) == (at(10), at(15))
    assert session(at(11), None, [at(10)]) == (at(10), at(15))
    assert session(at(11), None, [at(10, 59, 59, 999_999)]) == (at(10), at(15))


def test_block_lasts_five_hours_and_its_end_is_exclusive():
    recent = [at(10, 10)]
    assert session(at(14, 59, 59, 999_999), None, recent) == (at(10), at(15))
    assert session(at(15), None, recent) is None


def test_now_before_the_block_is_outside_every_block():
    assert session(at(9, 59), None, [at(10, 10)]) is None


def test_gap_of_five_hours_opens_a_new_block():
    recent = [at(10, 10), at(11), at(16, 10)]  # 5 h 10 min after the last request before it
    assert session(at(12), None, recent) == (at(10), at(15))
    assert session(at(15, 30), None, recent) is None  # between the two blocks
    assert session(at(16, 30), None, recent) == (at(16), at(21))


def test_gap_of_exactly_five_hours_opens_a_new_block():
    assert session(at(15, 30), None, [at(10, 10), at(15, 10)]) == (at(15), at(20))


def test_request_past_block_end_opens_a_block_even_without_a_five_hour_gap():
    # The block from 10:59 ends at 15:00. The request at 15:30 is only 4 h 31 min later, yet it
    # lands past the end, so it opens a new block. Otherwise it would sit in no block at all.
    assert session(at(15, 45), None, [at(10, 59), at(15, 30)]) == (at(15), at(20))


def test_continuous_usage_chains_blocks():
    recent = [at(10), at(12), at(14), at(16), at(18)]
    assert session(at(14, 59), None, recent) == (at(10), at(15))
    assert session(at(17), None, recent) == (at(16), at(21))


def test_unsorted_timestamps_give_the_same_blocks():
    recent = [at(18), at(10), at(16), at(12), at(14)]
    assert session(at(17), None, recent) == (at(16), at(21))


def test_no_requests_and_no_limit_means_no_block():
    assert session(at(12), None, []) is None
    assert session(at(12), _usage(), []) is None


def test_usage_without_a_session_limit_uses_blocks():
    assert session(at(12), _usage(), [at(10, 10)]) == (at(10), at(15))


# ── week() ───────────────────────────────────────────────────────────────────

def test_week_uses_the_weekly_limit_window_when_resets_at_is_known():
    usage = _usage(_limit("weekly_all", utc(2026, 10, 23, 12)))
    assert week(utc(2026, 10, 21, 12), usage) == (utc(2026, 10, 16, 12), utc(2026, 10, 23, 12))


def test_week_falls_back_to_the_last_seven_days():
    now = utc(2026, 10, 21, 12)
    expected = (utc(2026, 10, 14, 12), now)
    assert week(now, None) == expected
    assert week(now, _usage(_limit("weekly_all", None))) == expected
    assert week(now, _usage(_limit("session", utc(2026, 10, 21, 14)))) == expected


# ── burn_rate() ──────────────────────────────────────────────────────────────

NOW = at(12)


def test_burn_rate_counts_only_entries_inside_the_window():
    recent = [
        (at(11, 29, 59), 1_000, 1.0),  # before the 30-minute window
        (at(11, 30), 3_000, 0.5),      # the window start counts
        (at(11, 45), 6_000, 2.0),
        (at(12), 0, 0.0),              # now counts
        (at(12, 0, 1), 999_999, 99.0),  # after now is excluded
    ]
    rate = burn_rate(recent, NOW)
    assert rate.tokens_per_min == pytest.approx(9_000 / 30)
    assert rate.usd_per_hour == pytest.approx(2.5 * 60 / 30)
    assert rate.window_minutes == 30
    assert rate.velocity == "SLOW"


def test_burn_rate_scales_with_the_window_length():
    rate = burn_rate([(at(11, 55), 5_000, 0.2)], NOW, window_minutes=10)
    assert rate.tokens_per_min == pytest.approx(500)
    assert rate.usd_per_hour == pytest.approx(1.2)
    assert rate.velocity == "NORMAL"
    assert rate.window_minutes == 10


def test_burn_rate_with_nothing_recent_is_idle():
    assert burn_rate([], NOW) == BurnRate(tokens_per_min=0.0, usd_per_hour=0.0,
                                          velocity="IDLE", window_minutes=30)


@pytest.mark.parametrize("tokens, velocity", [
    (0, "IDLE"),
    (1, "SLOW"),
    (14_997, "SLOW"),       # 499.9 tokens/min
    (15_000, "NORMAL"),     # 500.0
    (44_997, "NORMAL"),     # 1499.9
    (45_000, "FAST"),       # 1500.0
    (89_997, "FAST"),       # 2999.9
    (90_000, "VERY FAST"),  # 3000.0
    (5_000_000, "VERY FAST"),
])
def test_velocity_bands_on_output_tokens_per_minute(tokens, velocity):
    rate = burn_rate([(at(11, 59), tokens, 0.0)], NOW, window_minutes=30)
    assert rate.velocity == velocity


def test_zero_output_entries_are_idle_even_with_cost():
    rate = burn_rate([(at(11, 59), 0, 0.3)], NOW)
    assert rate.velocity == "IDLE"
    assert rate.usd_per_hour == pytest.approx(0.6)


@pytest.mark.parametrize("window_minutes", [0, -5])
def test_burn_rate_rejects_a_non_positive_window(window_minutes):
    with pytest.raises(ValueError):
        burn_rate([], NOW, window_minutes=window_minutes)


# ── Default zone (OS local rules) ────────────────────────────────────────────
# These hold on any machine, whatever its zone, so they do not depend on the CI runner.

def _is_local_midnight(instant: datetime) -> bool:
    wall = instant.astimezone()
    return (wall.hour, wall.minute, wall.second, wall.microsecond) == (0, 0, 0, 0)


def test_default_zone_day_and_month_start_at_os_local_midnight():
    now = datetime(2026, 10, 21, 15, tzinfo=UTC)
    day_start, day_end = today(now)
    assert today(now, tz=None) == (day_start, day_end)
    assert _is_local_midnight(day_start) and _is_local_midnight(day_end)
    assert day_start <= now < day_end
    assert timedelta(hours=23) <= day_end - day_start <= timedelta(hours=25)
    assert day_start.astimezone().date() == now.astimezone().date()

    month_start, month_end = month(now)
    assert _is_local_midnight(month_start) and month_start.astimezone().day == 1
    assert _is_local_midnight(month_end) and month_end.astimezone().day == 1


def test_default_zone_cycle_renews_at_local_midnight():
    now = datetime(2026, 10, 21, 15, tzinfo=UTC)
    result = cycle(now, 15)
    assert result is not None
    start, end = result
    assert _is_local_midnight(start) and _is_local_midnight(end)
    assert start.astimezone().day == 15 and end.astimezone().day == 15
    assert start <= now < end
