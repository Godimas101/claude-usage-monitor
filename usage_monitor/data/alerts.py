"""Usage alerts: threshold crossings, limit resets and usage-credit use.

The engine is fed every published MonitorState. It keeps its memory in process only, so a
restart starts a fresh baseline: the first sight of a limit records its current level and
returns nothing, which keeps startup quiet. Settings are read on every evaluation, so changes
apply at once. Alerts are withheld while the master switch is off, while a kind is toggled off,
during quiet hours and when the caller reports suppression (fullscreen). The memory still
advances in those cases, so nothing fires in a burst later.

A limit's reset is announced when its window ends by the clock, not when the API next shows a new window: the
usage API says nothing at the end of a 5-hour session, which either drops out of its answer or keeps showing the
window that ended until the next message starts another, so waiting for a new window would announce the reset
hours late, while the user is already working again.
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any

from usage_monitor.data.models import ApiStatus, Limit, MonitorState, Spend
from usage_monitor.log import get_logger
from usage_monitor.settings import DEFAULTS, Settings, parse_clock

log = get_logger(__name__)

_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_SAME_WINDOW = timedelta(seconds=60)  # resets_at jitter below this is the same window (as in analytics)
_FALLBACK_NAMES = {"session": "Session", "weekly_all": "Weekly"}


@dataclass(slots=True)
class Alert:
    key: str          # dedupe key
    kind: str         # "threshold" | "reset" | "credits"
    severity: str     # "info" | "warning" | "critical"
    title: str        # "Session limit at 90%"
    message: str      # "Resets 4:40 PM"


@dataclass(slots=True)
class _Track:
    """Memory for one limit (kind + label): the window it is in and the thresholds it has reached."""
    key: str                                     # window id for alert keys: first resets_at, to the minute, UTC
    resets_at: datetime                          # latest resets_at seen (UTC); window identity is tolerance-based
    percent: float                               # last percent seen in that window
    high: float                                  # highest percent seen in that window
    done: set[int] = field(default_factory=set)  # thresholds already handled in that window
    name: str = ""                               # display name, kept so a reset can be announced without a Limit in hand
    closed: bool = False                         # the window ended and its reset was dealt with; waits for the next one


@dataclass(frozen=True, slots=True)
class _Config:
    enabled: bool
    thresholds: tuple[int, ...]                  # ascending, distinct
    on_threshold: bool
    on_reset: bool
    on_credits: bool
    quiet: tuple[int, int] | None                # (start, end) minutes after midnight; None = off


class AlertEngine:
    """Turns successive MonitorStates into the alerts that should be shown."""

    def __init__(self, settings: Settings, *, tz: tzinfo | None = None) -> None:
        self._settings = settings
        self._tz = tz
        self._lock = threading.Lock()
        self._tracks: dict[tuple[str, str], _Track] = {}
        self._credits_seen: float | None = None   # last "used" value; None until the baseline
        self._credits_day: date | None = None     # local day of the last credits alert

    def evaluate(self, state: MonitorState, *, suppressed: bool = False) -> list[Alert]:
        """Alerts due for ``state``. Never raises; a failure is logged and gives an empty list."""
        try:
            return self._evaluate(state, suppressed)
        except Exception:
            log.exception("alert evaluation failed")
            return []

    # ── Evaluation ──

    def _evaluate(self, state: MonitorState, suppressed: bool) -> list[Alert]:
        if state.status is not ApiStatus.LIVE or state.usage is None:
            return []
        cfg = self._config()
        now = state.updated_at
        candidates: list[Alert] = []
        with self._lock:
            for limit in state.usage.limits:
                try:
                    candidates.extend(self._limit(limit, cfg, now))
                except Exception:
                    log.exception("alert check failed for limit %r", getattr(limit, "label", None))
            try:
                candidates.extend(self._expire(cfg, now))
            except Exception:
                log.exception("alert check failed for ended windows")
            try:
                candidates.extend(self._credits(state.usage.spend, now))
            except Exception:
                log.exception("alert check failed for usage credits")
        if suppressed or not cfg.enabled or _in_quiet_hours(cfg.quiet, now, self._tz):
            return []
        allowed = {"threshold": cfg.on_threshold, "reset": cfg.on_reset, "credits": cfg.on_credits}
        return [alert for alert in candidates if allowed.get(alert.kind, False)]

    def _limit(self, limit: Limit, cfg: _Config, now: datetime) -> list[Alert]:
        percent = limit.percent
        if not _finite(percent) or not isinstance(limit.resets_at, datetime):
            return []  # nothing to judge without a percent and a reset time
        lid = (str(limit.kind or ""), str(limit.label or ""))
        resets_at = limit.resets_at.astimezone(timezone.utc)
        ended = now.astimezone(timezone.utc) >= resets_at + _SAME_WINDOW  # over by the clock, whatever the API shows
        track = self._tracks.get(lid)
        if track is None or track.closed:
            if track is not None and ended:
                return []  # still the window that ran out (or another that is over too): nothing to judge
            # First sight of this limit, or its first window since a reset that was announced when the old one ran
            # out: whatever level it is at now is baseline, not news.
            self._tracks[lid] = _Track(_window_key(resets_at), resets_at, float(percent), float(percent),
                                       {t for t in cfg.thresholds if t <= percent}, _name(limit), closed=ended)
            return []
        track.name = _name(limit)
        if ended:
            return []  # a window that is over has no thresholds left; _expire() announces its reset

        out: list[Alert] = []
        if abs(resets_at - track.resets_at) > _SAME_WINDOW:
            resets = _window_key(resets_at)
            if track.percent >= cfg.thresholds[0]:
                out.append(Alert(
                    key=f"reset:{lid[0]}:{lid[1]}:{resets}",
                    kind="reset",
                    severity="info",
                    title=f"{_name(limit)} limit reset",
                    message=_reset_text(limit, self._tz),
                ))
            track.key = resets
            track.high = 0.0
            track.done = set()
        track.resets_at = resets_at

        # Levels at or below the window's high-water mark were passed before: a threshold added by a
        # settings change mid-window is marked done, not announced.
        track.done.update(t for t in cfg.thresholds if t <= track.high)
        track.percent = float(percent)
        track.high = max(track.high, track.percent)
        crossed = [t for t in cfg.thresholds if t <= percent and t not in track.done]
        track.done.update(crossed)
        if crossed:
            top = max(crossed)  # one alert for the highest level reached; the lower ones are done
            out.append(Alert(
                key=f"threshold:{lid[0]}:{lid[1]}:{track.key}:{top}",
                kind="threshold",
                severity="critical" if top >= 100 else "warning",
                title=f"{_name(limit)} limit at {top}%",
                message=_reset_text(limit, self._tz),
            ))
        return out

    def _expire(self, cfg: _Config, now: datetime) -> list[Alert]:
        """Reset alerts for the windows whose time has run out, whatever the usage API still shows.

        A window that ended is closed here, once: its level and thresholds are cleared and the next window that appears
        starts from a quiet baseline (see _limit). The alert needs the window to have reached the lowest threshold, as
        a reset announced through a change of window does.
        """
        out: list[Alert] = []
        moment = now.astimezone(timezone.utc)
        for (kind, label), track in self._tracks.items():
            if track.closed or moment < track.resets_at + _SAME_WINDOW:
                continue
            reached = track.percent
            track.closed, track.percent, track.high, track.done = True, 0.0, 0.0, set()
            if reached >= cfg.thresholds[0]:
                out.append(Alert(
                    key=f"reset:{kind}:{label}:{track.key}",
                    kind="reset",
                    severity="info",
                    title=f"{track.name or 'Usage'} limit reset",
                    message="Usage is back to 0%.",
                ))
        return out

    def _credits(self, spend: Spend | None, now: datetime) -> list[Alert]:
        if spend is None or not _finite(spend.used):
            return []
        used = float(spend.used)
        seen, self._credits_seen = self._credits_seen, used
        if seen is None or used <= seen or not spend.enabled:
            return []
        day = now.astimezone(self._tz).date()
        if self._credits_day == day:
            return []  # at most one credits alert per local day
        self._credits_day = day
        return [Alert(
            key=f"credits:{day.isoformat()}",
            kind="credits",
            severity="info",
            title="Usage credits in use",
            message=_credits_text(spend),
        )]

    # ── Settings ──

    def _config(self) -> _Config:
        s = self._settings
        return _Config(
            enabled=bool(s.get("alerts_enabled", DEFAULTS["alerts_enabled"])),
            thresholds=_thresholds(s.get("alert_thresholds", DEFAULTS["alert_thresholds"])),
            on_threshold=bool(s.get("alert_on_threshold", DEFAULTS["alert_on_threshold"])),
            on_reset=bool(s.get("alert_on_reset", DEFAULTS["alert_on_reset"])),
            on_credits=bool(s.get("alert_on_credits", DEFAULTS["alert_on_credits"])),
            quiet=_quiet_span(s.get("alert_quiet_hours", None)),
        )


# ── Helpers ──────────────────────────────────────────────────────────────────

def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _thresholds(value: Any) -> tuple[int, ...]:
    if isinstance(value, list):
        levels = sorted({t for t in value if isinstance(t, int) and not isinstance(t, bool) and 1 <= t <= 100})
        if levels:
            return tuple(levels)
    return tuple(sorted(DEFAULTS["alert_thresholds"]))


def _quiet_span(value: Any) -> tuple[int, int] | None:
    """(start, end) in minutes. Equal start and end mean no quiet hours; start > end wraps midnight."""
    if not isinstance(value, dict):
        return None
    start, end = parse_clock(value.get("start")), parse_clock(value.get("end"))
    if start is None or end is None or start == end:
        return None
    return start, end


def _in_quiet_hours(span: tuple[int, int] | None, now: datetime, tz: tzinfo | None) -> bool:
    if span is None:
        return False
    local = now.astimezone(tz)
    minute = local.hour * 60 + local.minute
    start, end = span
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def _window_key(resets_at: datetime) -> str:
    """Window identity: the reset instant truncated to the minute, in UTC."""
    return resets_at.astimezone(timezone.utc).replace(second=0, microsecond=0).isoformat()


def _name(limit: Limit) -> str:
    """Sentence-case label: "FABLE WEEKLY" becomes "Fable weekly"."""
    text = " ".join((limit.label or "").split()) or _FALLBACK_NAMES.get(limit.kind) or (limit.scope_model or "Scoped")
    return text[:1].upper() + text[1:].lower()


def _reset_text(limit: Limit, tz: tzinfo | None) -> str:
    local = limit.resets_at.astimezone(tz)
    clock = f"{local.hour % 12 or 12}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"
    if limit.kind == "session":
        return f"Resets {clock}"
    return f"Resets {_DAYS[local.weekday()]} {clock}"


def _credits_text(spend: Spend) -> str:
    text = f"Used {spend.used:,.2f}"
    if _finite(spend.limit):
        text += f" of {spend.limit:,.2f}"
    if spend.currency:
        text += f" {spend.currency}"
    return text
