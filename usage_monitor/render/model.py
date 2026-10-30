"""View model for the Pillow widgets: plain data built from MonitorState, settings and the clock.

No Win32 and no drawing code here. Skins draw a WidgetModel; the host builds one whenever the
state, the settings or the minute changes. Severity, pace, time windows, local reset times and
the status wording are decided once here, so every skin tells the same story.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from usage_monitor.data.models import ApiStatus, Limit, MonitorState, UsageSnapshot
from usage_monitor.data.windows import PACE_BAND_POINTS

ShowScoped = Literal["auto", "always", "never"]
SHOW_SCOPED_MODES: tuple[str, ...] = ("auto", "always", "never")

SESSION_WINDOW_S = 5 * 60 * 60          # the session limit is a 5 h window
WEEKLY_WINDOW_S = 7 * 24 * 60 * 60      # weekly and scoped limits are 7 d windows
WARNING_PERCENT = 75.0                  # severity thresholds when the API gives none
CRITICAL_PERCENT = 90.0
AUTO_SCOPED_MIN_PERCENT = 1.0           # "auto" shows a scoped row at 1 % or more, or when active
DAY_S = 24 * 60 * 60                    # a guard resume under a day away reads as a countdown, past it as a reset time

GUARD_ARMED = "armed"                   # the usage guard (#27): watching its caps ...
GUARD_HOLDING = "holding"               # ... or a watched limit is at its cap and Claude sessions were told to wind down
GUARD_STATES: tuple[str, ...] = (GUARD_ARMED, GUARD_HOLDING)

STALE_MESSAGE_EXPIRED = "LOGIN EXPIRED - OPEN CLAUDE CODE"
STALE_MESSAGE_NO_LOGIN = "NO LOGIN FOUND - OPEN THE DASHBOARD"   # it has the steps for a claude.ai or app user (#28)

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

# The lamp kinds: what a status looks like on a surface with no room for words (the taskbar strip, the tray icon).
# Each skin draws its own mark for a kind, but they all tell the same states apart: live, paused, last known
# (stale: the sign-in is lost or missing, so the user acts), offline (no connection) and limited (the usage API is
# throttling the monitor; both of those retry by themselves). A status still loading has a kind of its own, so a
# skin may draw it like "stale" (nothing to show yet) without the two sharing a name.
LAMP_LIVE = "live"
LAMP_PAUSED = "paused"
LAMP_STALE = "stale"
LAMP_OFFLINE = "offline"
LAMP_LIMITED = "limited"
LAMP_LOADING = "loading"
LAMP_KINDS: dict[ApiStatus, str] = {
    ApiStatus.LIVE: LAMP_LIVE,
    ApiStatus.PAUSED: LAMP_PAUSED,
    ApiStatus.STALE: LAMP_STALE,
    ApiStatus.NO_CREDENTIALS: LAMP_STALE,
    ApiStatus.OFFLINE: LAMP_OFFLINE,
    ApiStatus.RATE_LIMITED: LAMP_LIMITED,
    ApiStatus.LOADING: LAMP_LOADING,
}


def lamp_kind(status: ApiStatus) -> str:
    """The lamp kind of a status (see LAMP_KINDS). An unknown status reads as stale: nothing live to show."""
    return LAMP_KINDS.get(status, LAMP_STALE)

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
class GuardMark:
    """The usage guard (#27) as the widgets show it: armed (watching its caps) or holding (a watched
    limit is at its cap, so Claude sessions were told to wind down and will be woken when every watched
    limit is under its cap again).

    WidgetModel.guard is None while the guard is off, and a skin that draws nothing for None draws exactly
    what it drew before the guard existed.

    over_keys are the Row keys of the limits at or past their caps, in the guard's order. caps are the
    caps being watched, in percent, by Row key (a limit without a cap is not in it). labels is the name
    people read for each key ("session", "weekly", "Fable weekly"), and readings the percent of each limit
    over its cap, so a hover card can say it even for a scoped row the widget does not show.

    resume_at is the latest reset among the limits over their caps (None when one has no reset time or the
    guard is only armed). resume_text is that moment in one word for a surface with room for one: a countdown
    ("2H 14M", "9M", "<1M") under a day, past a day a reset time as the rows give it ("Wed 4:00 PM"); no skin
    draws it today (each picks resume_in or resume_clock itself), the dump and the tests read it. resume_in is always the
    countdown ("2D 3H" past a day) and resume_clock always the reset time, for a surface with room for both.
    """
    state: str
    over_keys: tuple[str, ...] = ()
    caps: dict[str, int | float] = field(default_factory=dict)
    resume_at: datetime | None = None
    resume_text: str | None = None
    resume_in: str | None = None
    resume_clock: str | None = None
    labels: dict[str, str] = field(default_factory=dict)
    readings: dict[str, float] = field(default_factory=dict)

    @property
    def holding(self) -> bool:
        """True while a watched limit is at its cap."""
        return self.state == GUARD_HOLDING

    def over_limits(self) -> list[tuple[str, float | None, int | float | None]]:
        """(label, reading, cap) of each limit over its cap, in the guard's order. A reading or a cap the guard did
        not give is None."""
        return [(self.labels.get(key, key), self.readings.get(key), self.caps.get(key)) for key in self.over_keys]

    def watched(self) -> list[tuple[str, int | float]]:
        """(label, cap) of each limit the guard has a cap for, in the order of ``caps``: session, weekly, then the
        scoped limits."""
        return [(self.labels.get(key, key), cap) for key, cap in self.caps.items()]


@dataclass(frozen=True, slots=True)
class WidgetModel:
    """Everything a skin needs to draw the floating widget, the strip, the tray and the tooltip.

    status_text is the pill wording (LIVE, STALE, OFFLINE, RATE LIMITED, PAUSED, LOADING,
    NO LOGIN). age_text is "12 S AGO" style, or None before the first fetch. stale is True when
    the values are not live (everything except LIVE and PAUSED). stale_message is set when the
    user must act (expired or missing login). blink_on drives the alive mark (animation only).
    guard is the usage guard when it is armed or holding, None while it is off.
    """
    status: ApiStatus
    status_text: str
    age_text: str | None
    rows: list[Row]
    stale: bool
    stale_message: str | None
    blink_on: bool
    updated_at: datetime
    guard: GuardMark | None = None

    @property
    def lamp(self) -> str:
        """The lamp kind of the status: live, paused, stale, offline, limited or loading (LAMP_KINDS)."""
        return lamp_kind(self.status)


# The surfaces a skin draws, as the optional skin methods content_insets() and hover_frames() name them.
SURFACE_FLOATING = "floating"
SURFACE_TASKBAR = "taskbar"
SURFACE_TOOLTIP = "tooltip"

# (left, top, right, bottom): the transparent pixels between the edge of a rendered frame and the visible surface in
# it, at the frame's own scale. A frame with a soft shadow around its card has them, and the widgets place the card,
# not the padded frame, so the tooltip, the screen edge and the default corner measure from what the user sees.
Insets = tuple[int, int, int, int]
NO_INSETS: Insets = (0, 0, 0, 0)


@dataclass(frozen=True, slots=True)
class HoverPlay:
    """A response to the pointer entering a widget, pre-rendered by a skin and played by ui/widgets.py.

    ``frames`` are PIL images of the size of the frame they were made from. frames[0] shows at once, and each
    frame stays up for the delay in ``delays_ms`` at its own index before the next one follows. After the last
    frame the surface returns to its normal frame once that frame's delay has passed, unless ``hold`` is set: the
    last frame then stays while the pointer is over the widget, and ``leave`` (each shown for ``leave_ms``) plays on
    the way out before the normal frame returns. Nothing here draws or touches a window: it is plain data.
    """
    frames: tuple[Any, ...]
    delays_ms: tuple[int, ...]
    hold: bool = False
    leave: tuple[Any, ...] = ()
    leave_ms: int = 40

    def __post_init__(self) -> None:
        if not self.frames or len(self.delays_ms) != len(self.frames):
            raise ValueError("a hover play needs at least one frame and one delay for each")


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
        guard=_guard_mark(getattr(state, "guard", None), usage, now),
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


def format_countdown(seconds: float) -> str:
    """Time left as "2D 3H", "2H 14M", "9M" or "<1M": whole minutes rounded down, and days once a day or
    more is left (minutes are noise by then). A whole hour or day has no zero tail: "2H", "1D"."""
    minutes = max(0, int(seconds)) // 60
    if minutes < 1:
        return "<1M"
    days, rest = divmod(minutes, 24 * 60)
    hours, minutes = divmod(rest, 60)
    if days:
        return f"{days}D {hours}H" if hours else f"{days}D"
    if hours:
        return f"{hours}H {minutes}M" if minutes else f"{hours}H"
    return f"{minutes}M"


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
        name = _scoped_name(limit, index)
        rows.append(_limit_row(
            f"scoped:{name}", limit.label or name.upper(), limit, now, WEEKLY_WINDOW_S, True,
        ))
    return rows


def _scoped_name(limit: Limit, index: int) -> str:
    """What a scoped row's key is made of: the limit's model, else its label, else its place in the list."""
    return (limit.scope_model or limit.label or "").strip().lower() or str(index)


# The usage guard. agent/guard.py judges a reading against the caps and says which limits are over, by name
# ("session", "weekly", or a scoped limit's own label) and by a label for people ("Fable weekly"). The widgets
# key a row by "session", "weekly" or "scoped:<model>", so the guard's answer is mapped onto the rows here, once,
# and every skin marks the rows it is told about.

def _guard_mark(block: Any, usage: UsageSnapshot | None, now: datetime) -> GuardMark | None:
    """The guard block of the state as a mark, or None when it is off, missing or empty, and while there is
    no reading (no sign-in, still loading): the guard cannot act on nothing, so no surface says it is armed."""
    if not isinstance(block, Mapping) or block.get("state") not in GUARD_STATES or usage is None:
        return None
    holding = block["state"] == GUARD_HOLDING
    policy = block.get("policy") if isinstance(block.get("policy"), Mapping) else {}
    scoped = []                         # (key, name, label) of every scoped limit: the guard watches the hidden ones too
    for index, limit in enumerate(usage.scoped if usage is not None else ()):
        model = limit.scope_model
        label = f"{model} weekly" if model else str(limit.label or "scoped").lower()   # as agent/guard.py names it
        scoped.append((f"scoped:{_scoped_name(limit, index)}", str(limit.label or label), label))

    labels = {"session": "session", "weekly": "weekly", **{key: label for key, _name, label in scoped}}
    caps: dict[str, int | float] = {}
    for key, setting in (("session", "session_cap"), ("weekly", "weekly_cap")):
        cap = _cap(policy.get(setting))
        if cap is not None:
            caps[key] = cap
    scoped_cap = _cap(policy.get("scoped_cap"))
    if scoped_cap is not None:
        caps.update({key: scoped_cap for key, _name, _label in scoped})

    over_keys: list[str] = []
    readings: dict[str, float] = {}
    over = block.get("over") if holding else None
    for item in over or ():
        if not isinstance(item, Mapping):
            continue
        key = _over_key(item, scoped)
        if key is None or key in over_keys:
            continue
        over_keys.append(key)
        labels.setdefault(key, str(item.get("label") or key))
        if _is_number(item.get("percent")):
            readings[key] = float(item["percent"])
        cap = _cap(item.get("cap"))
        if cap is not None:
            caps[key] = cap             # the cap the guard applied is the one that counts

    resume_at = _moment(block.get("resume_at")) if holding else None
    resume_in = resume_clock = resume_text = None
    if resume_at is not None:
        remaining = (resume_at - now).total_seconds()
        resume_in = format_countdown(remaining)
        resume_clock = format_resets(resume_at, now)
        resume_text = resume_in if remaining < DAY_S else resume_clock
    return GuardMark(
        state=block["state"], over_keys=tuple(over_keys), caps=caps, resume_at=resume_at, resume_text=resume_text,
        resume_in=resume_in, resume_clock=resume_clock, labels=labels, readings=readings,
    )


def _over_key(item: Mapping[str, Any], scoped: list[tuple[str, str, str]]) -> str | None:
    """The Row key of a limit the guard says is over its cap."""
    name, label = item.get("name"), item.get("label")
    if name in ("session", "weekly"):
        return name
    for key, scoped_name, scoped_label in scoped:
        if (name, label) == (scoped_name, scoped_label):
            return key
    text = str(label or name or "").strip().lower()      # a reading that is gone: key it as a model's row would be
    text = text.removesuffix(" weekly").strip() or text
    return f"scoped:{text}" if text else None


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _cap(value: Any) -> int | float | None:
    """A cap as a number above zero (settings keep whole percentages from 1 to 100), else None."""
    return value if _is_number(value) and value > 0 else None


def _moment(value: Any) -> datetime | None:
    """An aware datetime from the guard's ISO text ("2026-10-29T02:00:00Z"), a datetime, or None."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


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
