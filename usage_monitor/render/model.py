"""View model for the Pillow widgets: plain data built from MonitorState, settings and the clock.

No Win32 and no drawing code here. Skins draw a WidgetModel; the host builds one whenever the
state, the settings or the minute changes. Severity, pace, time windows, local reset times and
the status wording are decided once here, so every skin tells the same story.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from usage_monitor.data.models import ApiStatus, Limit, MonitorState, UsageSnapshot
from usage_monitor.data.windows import PACE_BAND_POINTS

ShowScoped = Literal["auto", "always", "never"]
SHOW_SCOPED_MODES: tuple[str, ...] = ("auto", "always", "never")

SESSION_WINDOW_S = 5 * 60 * 60          # the session limit is a 5 h window
WEEKLY_WINDOW_S = 7 * 24 * 60 * 60      # weekly and scoped limits are 7 d windows
WARNING_PERCENT = 75.0                  # severity thresholds when the API gives none
CRITICAL_PERCENT = 90.0
AUTO_SCOPED_MIN_PERCENT = 1.0           # "auto" shows a scoped row at 1 % or more, or when active

STALE_MESSAGE_EXPIRED = "LOGIN EXPIRED - OPEN CLAUDE CODE"
STALE_MESSAGE_NO_LOGIN = "NO LOGIN FOUND - OPEN CLAUDE CODE"

STATUS_TEXT: dict[ApiStatus, str] = {
    ApiStatus.LOADING: "LOADING",
    ApiStatus.LIVE: "LIVE",
    ApiStatus.STALE: "STALE",
    ApiStatus.OFFLINE: "OFFLINE",
    ApiStatus.RATE_LIMITED: "RATE LIMITED",
    ApiStatus.NO_CREDENTIALS: "NO LOGIN",
    ApiStatus.PAUSED: "PAUSED",
}

SEVERITY_NORMAL = "normal"
SEVERITY_WARNING = "warning"
SEVERITY_CRITICAL = "critical"
_SEVERITY_RANK = {SEVERITY_NORMAL: 0, SEVERITY_WARNING: 1, SEVERITY_CRITICAL: 2}

_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_NORMAL_NAMES = frozenset({"normal", "ok", "none"})
_WARNING_NAMES = frozenset({"warning", "warn"})


@dataclass(frozen=True, slots=True)
class Row:
    """One limit as the widget shows it.

    key is "session", "weekly" or "scoped:<name>". percent and time_fraction are None when there
    is no data yet. time_fraction is the elapsed share of the limit's window (0..1). pace is
    "ahead", "on_pace" or "behind" (usage against the clock, +/-10 points), or None.
    """
    key: str
    label: str
    percent: float | None
    time_fraction: float | None
    severity: str                       # "normal" | "warning" | "critical"
    resets_at: datetime | None
    resets_text: str | None             # local time: "7:39 PM" today, else "Sat 4:00 PM"
    pace: str | None
    share_text: str | None              # scoped limits only: "50% SHARE"


@dataclass(frozen=True, slots=True)
class WidgetModel:
    """Everything a skin needs to draw the floating widget, the strip, the tray and the tooltip.

    status_text is the pill wording (LIVE, STALE, OFFLINE, RATE LIMITED, PAUSED, LOADING,
    NO LOGIN). age_text is "12 S AGO" style, or None before the first fetch. stale is True when
    the values are not live (everything except LIVE and PAUSED). stale_message is set when the
    user must act (expired or missing login). blink_on drives the alive mark (animation only).
    """
    status: ApiStatus
    status_text: str
    age_text: str | None
    rows: list[Row]
    stale: bool
    stale_message: str | None
    blink_on: bool
    updated_at: datetime


# ── Public entry point ────────────────────────────────────────────────────────

def build_model(
    state: MonitorState | None,
    now: datetime,
    *,
    show_scoped: ShowScoped = "auto",
    blink_on: bool = True,
) -> WidgetModel:
    """Build the view model. A None state (nothing published yet) renders as LOADING with dashes."""
    if show_scoped not in SHOW_SCOPED_MODES:
        raise ValueError(f"show_scoped must be one of {SHOW_SCOPED_MODES}, not {show_scoped!r}")
    now = _aware(now)
    usage = state.usage if state is not None else None
    status = state.status if state is not None else ApiStatus.LOADING
    fetched_at = _first_not_none(
        state.usage_fetched_at if state is not None else None,
        usage.fetched_at if usage is not None else None,
    )
    return WidgetModel(
        status=status,
        status_text=STATUS_TEXT[status],
        age_text=format_age(fetched_at, now) if fetched_at is not None else None,
        rows=_rows(usage, now, show_scoped),
        stale=status not in (ApiStatus.LIVE, ApiStatus.PAUSED),
        stale_message=_stale_message(status),
        blink_on=bool(blink_on),
        updated_at=state.updated_at if state is not None else now,
    )


def worst_severity(rows: list[Row]) -> str:
    """The highest severity among rows that have a value; normal when none does."""
    worst = SEVERITY_NORMAL
    for row in rows:
        if row.percent is not None and _SEVERITY_RANK[row.severity] > _SEVERITY_RANK[worst]:
            worst = row.severity
    return worst


# ── Formatting helpers (public so the tooltip and tests share them) ──────────

def severity_for(api_severity: str | None, percent: float | None) -> str:
    """The API's severity when it gives one (anything but normal or warning is critical), else
    75 % warning and 90 % critical."""
    if api_severity is not None and str(api_severity).strip():
        name = str(api_severity).strip().lower()
        if name in _NORMAL_NAMES:
            return SEVERITY_NORMAL
        if name in _WARNING_NAMES:
            return SEVERITY_WARNING
        return SEVERITY_CRITICAL
    if percent is None:
        return SEVERITY_NORMAL
    if percent >= CRITICAL_PERCENT:
        return SEVERITY_CRITICAL
    if percent >= WARNING_PERCENT:
        return SEVERITY_WARNING
    return SEVERITY_NORMAL


def elapsed_fraction(resets_at: datetime | None, window_s: int, now: datetime) -> float | None:
    """Share of the window that has passed (0..1), or None when the reset time is unknown."""
    if resets_at is None or window_s <= 0:
        return None
    elapsed = window_s - (resets_at - _aware(now)).total_seconds()
    return min(1.0, max(0.0, elapsed / window_s))


def pace_for(percent: float | None, time_fraction: float | None) -> str | None:
    """ahead when usage leads the clock by more than 10 points, behind when it trails by more."""
    delta = pace_delta(percent, time_fraction)
    if delta is None:
        return None
    if delta > PACE_BAND_POINTS:
        return "ahead"
    if delta < -PACE_BAND_POINTS:
        return "behind"
    return "on_pace"


def pace_delta(percent: float | None, time_fraction: float | None) -> float | None:
    """Usage minus elapsed time, in percentage points."""
    if percent is None or time_fraction is None:
        return None
    return percent - time_fraction * 100.0


def format_resets(resets_at: datetime, now: datetime) -> str:
    """Local reset time: "7:39 PM" on the same local day, otherwise "Sat 4:00 PM"."""
    local = resets_at.astimezone()
    clock = _clock_text(local)
    if local.date() == _aware(now).astimezone().date():
        return clock
    return f"{_WEEKDAYS[local.weekday()]} {clock}"


def format_age(fetched_at: datetime, now: datetime) -> str:
    """"12 S AGO", "41 M AGO", "3 H AGO" or "2 D AGO". Never negative."""
    seconds = max(0, int((_aware(now) - fetched_at).total_seconds()))
    if seconds < 60:
        return f"{seconds} S AGO"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} M AGO"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} H AGO"
    return f"{hours // 24} D AGO"


def share_text(allowance_pct: float | None) -> str | None:
    if allowance_pct is None:
        return None
    return f"{round(allowance_pct)}% SHARE"


# ── Internals ─────────────────────────────────────────────────────────────────

def _rows(usage: UsageSnapshot | None, now: datetime, show_scoped: str) -> list[Row]:
    rows = [
        _limit_row("session", "SESSION", usage.session if usage else None, now, SESSION_WINDOW_S, False),
        _limit_row("weekly", "WEEKLY", usage.weekly if usage else None, now, WEEKLY_WINDOW_S, False),
    ]
    if usage is None or show_scoped == "never":
        return rows
    for index, limit in enumerate(usage.scoped):
        if show_scoped == "auto" and not _auto_shows(limit):
            continue
        name = (limit.scope_model or limit.label or "").strip().lower() or str(index)
        rows.append(_limit_row(
            f"scoped:{name}", limit.label or name.upper(), limit, now, WEEKLY_WINDOW_S, True,
        ))
    return rows


def _auto_shows(limit: Limit) -> bool:
    return limit.percent >= AUTO_SCOPED_MIN_PERCENT or limit.is_active


def _limit_row(
    key: str,
    label: str,
    limit: Limit | None,
    now: datetime,
    default_window_s: int,
    scoped: bool,
) -> Row:
    if limit is None:
        return Row(key, label, None, None, SEVERITY_NORMAL, None, None, None, None)
    percent = float(limit.percent)
    window = limit.window_seconds if limit.window_seconds and limit.window_seconds > 0 else default_window_s
    fraction = elapsed_fraction(limit.resets_at, window, now)
    return Row(
        key=key,
        label=label,
        percent=percent,
        time_fraction=fraction,
        severity=severity_for(limit.severity, percent),
        resets_at=limit.resets_at,
        resets_text=format_resets(limit.resets_at, now) if limit.resets_at is not None else None,
        pace=pace_for(percent, fraction),
        share_text=share_text(limit.allowance_pct) if scoped else None,
    )


def _stale_message(status: ApiStatus) -> str | None:
    if status is ApiStatus.STALE:
        return STALE_MESSAGE_EXPIRED
    if status is ApiStatus.NO_CREDENTIALS:
        return STALE_MESSAGE_NO_LOGIN
    return None


def _clock_text(local: datetime) -> str:
    hour = local.hour % 12 or 12
    suffix = "AM" if local.hour < 12 else "PM"
    return f"{hour}:{local.minute:02d} {suffix}"


def _aware(moment: datetime) -> datetime:
    """Naive datetimes are read as local time; the data layer always passes aware ones."""
    return moment if moment.tzinfo is not None else moment.astimezone()


def _first_not_none(*values: datetime | None) -> datetime | None:
    for value in values:
        if value is not None:
            return value
    return None
