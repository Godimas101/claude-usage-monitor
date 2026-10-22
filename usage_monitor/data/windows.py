"""Time windows and burn rate for the usage monitor.

Day, month and billing-cycle boundaries are local midnights. They come from the OS local-time
rules, so DST days are 23 h or 25 h long. Callers may pass ``tz`` to use another zone (the
tests do). Week and session windows come from the usage snapshot's resets_at when it has one.
Without it they fall back to the clock or to the request timestamps. Every returned boundary is
an aware UTC datetime.
"""
from __future__ import annotations

import calendar
import logging
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta, timezone, tzinfo

from usage_monitor.data.models import BurnRate, UsageSnapshot

_log = logging.getLogger("usage_monitor." + __name__.split(".")[-1])

SESSION_LENGTH = timedelta(hours=5)
WEEK_LENGTH = timedelta(days=7)
DEFAULT_BURN_MINUTES = 30
# Pace: usage within this many points of the elapsed share of its window is "on pace", more is "ahead" and less
# is "behind". One number for every surface (the widgets, the dashboard's overview card, the pacing report and
# the Limits page), so a limit never reads ON PACE in one place and AHEAD in another. app.js keeps a copy
# (PACE_BAND), which tests/test_webui.py compares with this one.
PACE_BAND_POINTS = 10.0
# Output tokens per minute: below the first bound is SLOW, zero is IDLE, the last band is open.
_VELOCITY_BANDS: tuple[tuple[float, str], ...] = ((500, "SLOW"), (1500, "NORMAL"), (3000, "FAST"))


# ── Local calendar windows ───────────────────────────────────────────────────

def today(now: datetime, *, tz: tzinfo | None = None) -> tuple[datetime, datetime]:
    """Local calendar day that contains ``now``, as UTC [start, end)."""
    day = _local_date(now, tz)
    return _local_midnight(day, tz), _local_midnight(day + timedelta(days=1), tz)


def month(now: datetime, *, tz: tzinfo | None = None) -> tuple[datetime, datetime]:
    """Local calendar month that contains ``now``, as UTC [start, end)."""
    day = _local_date(now, tz)
    next_year, next_mon = _shift_month(day.year, day.month, 1)
    return (_local_midnight(day.replace(day=1), tz),
            _local_midnight(date(next_year, next_mon, 1), tz))


def cycle(now: datetime, billing_day: int | None, *,
          tz: tzinfo | None = None) -> tuple[datetime, datetime] | None:
    """Billing cycle that contains ``now``: local midnight of the latest renewal to the next one.

    Renewal days past the end of a short month clamp to its last day, so day 31 renews on
    Feb 28 (Feb 29 in leap years). Returns None when ``billing_day`` is missing or not 1..31.
    """
    renew_day = _valid_billing_day(billing_day)
    if renew_day is None:
        return None
    local = _local_date(now, tz)
    this_renewal = _renewal_date(local.year, local.month, renew_day)
    if local >= this_renewal:
        next_year, next_mon = _shift_month(local.year, local.month, 1)
        start, end = this_renewal, _renewal_date(next_year, next_mon, renew_day)
    else:
        prev_year, prev_mon = _shift_month(local.year, local.month, -1)
        start, end = _renewal_date(prev_year, prev_mon, renew_day), this_renewal
    return _local_midnight(start, tz), _local_midnight(end, tz)


# ── Limit windows: the usage snapshot first, the clock or requests as fallback ──

def week(now: datetime, usage: UsageSnapshot | None) -> tuple[datetime, datetime]:
    """Weekly limit window [resets_at - 7 d, resets_at), else the last 7 days up to ``now``."""
    limit = usage.weekly if usage is not None else None
    if limit is not None and limit.resets_at is not None:
        return _window_ending(limit.resets_at, WEEK_LENGTH)
    end = _utc(now)
    return end - WEEK_LENGTH, end


def session(now: datetime, usage: UsageSnapshot | None,
            recent_ts: Iterable[datetime]) -> tuple[datetime, datetime] | None:
    """Session limit window [resets_at - 5 h, resets_at), else the block containing ``now``.

    The API window is returned even when ``now`` is past it, so pass ``usage=None`` for a stale
    snapshot. Otherwise the block comes from ``recent_ts`` (see ``_block_containing``), and None
    means ``now`` is outside every block.
    """
    limit = usage.session if usage is not None else None
    if limit is not None and limit.resets_at is not None:
        return _window_ending(limit.resets_at, SESSION_LENGTH)
    return _block_containing(_utc(now), recent_ts)


# ── Burn rate ────────────────────────────────────────────────────────────────

def burn_rate(recent: Iterable[tuple[datetime, int, float]], now: datetime,
              window_minutes: int = DEFAULT_BURN_MINUTES) -> BurnRate:
    """Output tokens per minute and API-equivalent USD per hour over the recent window.

    ``recent`` holds (ts, output_tokens, cost_usd). Entries inside [now - window, now] count.
    """
    if window_minutes <= 0:
        raise ValueError(f"window_minutes must be positive, got {window_minutes}")
    end = _utc(now)
    start = end - timedelta(minutes=window_minutes)
    tokens = 0
    cost = 0.0
    for ts, output_tokens, cost_usd in recent:
        if start <= _utc(ts) <= end:
            tokens += output_tokens
            cost += cost_usd
    tokens_per_min = tokens / window_minutes
    return BurnRate(
        tokens_per_min=tokens_per_min,
        usd_per_hour=cost * 60 / window_minutes,
        velocity=_velocity(tokens_per_min),
        window_minutes=window_minutes,
    )


# ── Helpers ──────────────────────────────────────────────────────────────────

def _utc(ts: datetime) -> datetime:
    return ts.astimezone(timezone.utc)


def _local_date(now: datetime, tz: tzinfo | None) -> date:
    """Calendar date of ``now`` in ``tz``. ``tz=None`` means the OS local zone."""
    return now.astimezone(tz).date()


def _local_midnight(day: date, tz: tzinfo | None) -> datetime:
    """UTC instant of local midnight at the start of ``day``.

    With ``tz=None`` the naive midnight is read as OS local time. A midnight that DST skips
    resolves to the transition instant, because the pre-transition offset applies to it.
    """
    return datetime.combine(day, time.min).replace(tzinfo=tz).astimezone(timezone.utc)


def _shift_month(year: int, mon: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (mon - 1) + delta
    return index // 12, index % 12 + 1


def _renewal_date(year: int, mon: int, billing_day: int) -> date:
    return date(year, mon, min(billing_day, calendar.monthrange(year, mon)[1]))


def _valid_billing_day(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 31:
        _log.debug("ignoring billing_day %r: expected an int from 1 to 31", value)
        return None
    return value


def _window_ending(resets_at: datetime, length: timedelta) -> tuple[datetime, datetime]:
    end = _utc(resets_at)
    return end - length, end


def _block_containing(now: datetime,
                      timestamps: Iterable[datetime]) -> tuple[datetime, datetime] | None:
    """Session block that contains ``now`` (UTC), detected from request timestamps.

    A block opens at the first request and again at any request at or past the open block's
    end. A gap of 5 h or more always lands past that end, so it opens a block too. A block
    starts at its first request floored to the UTC hour and lasts five hours.
    """
    blocks: list[tuple[datetime, datetime]] = []
    for ts in sorted(_utc(t) for t in timestamps):
        if not blocks or ts >= blocks[-1][1]:
            start = ts.replace(minute=0, second=0, microsecond=0)
            blocks.append((start, start + SESSION_LENGTH))
    return next(((s, e) for s, e in blocks if s <= now < e), None)


def _velocity(tokens_per_min: float) -> str:
    if tokens_per_min <= 0:
        return "IDLE"
    for upper, label in _VELOCITY_BANDS:
        if tokens_per_min < upper:
            return label
    return "VERY FAST"
