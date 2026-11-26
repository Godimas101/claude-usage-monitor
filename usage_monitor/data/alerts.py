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
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any

from usage_monitor import casing, formats, i18n
from usage_monitor.data.identity import scope_name
from usage_monitor.data.models import ApiStatus, Limit, MonitorState, Spend
from usage_monitor.i18n import DEFAULT, ENGLISH, Locale
from usage_monitor.log import get_logger
from usage_monitor.settings import DEFAULTS, Settings, parse_clock

log = get_logger(__name__)

_SAME_WINDOW = timedelta(seconds=60)  # resets_at jitter below this is the same window (as in analytics)
KEY = "toast.alert."                    # the alerts' words in the catalog (#97)
# The kinds with a name of their own, by key; any other is named by its scope, its code or its model (_name).
_FALLBACK_NAMES = {"session": KEY + "name.session", "weekly_all": KEY + "name.weekly"}


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
    closed: bool = False                         # the window ended and its reset was dealt with; waits for the next one
    # What names the limit (_name), kept from its last reading so a reset can be announced without a Limit in hand,
    # worded then in the locale of that moment.
    kind: str = ""
    label: str = ""
    scope_model: str | None = None
    scope_surface: str | None = None


@dataclass(frozen=True, slots=True)
class _Config:
    enabled: bool
    thresholds: tuple[int, ...]                  # ascending, distinct
    on_threshold: bool
    on_reset: bool
    on_credits: bool
    quiet: tuple[int, int] | None                # (start, end) minutes after midnight; None = off


class AlertEngine:
    """Turns successive MonitorStates into the alerts that should be shown.

    ``locale_provider`` gives the app's locale (i18n.LocaleSource, #91); it is read on every evaluation, as the
    settings are. Without one the alerts are English. The words come from the catalog in the locale's language
    (toast.alert.*, #97); the numbers, the reset times (as the widgets write them) and the case of the names follow the
    locale."""

    def __init__(self, settings: Settings, *, tz: tzinfo | None = None,
                 locale_provider: Callable[[], Locale] | None = None) -> None:
        self._settings = settings
        self._tz = tz
        self._locale_provider = locale_provider
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
        locale = self._locale()
        now = state.updated_at
        candidates: list[Alert] = []
        with self._lock:
            for limit in state.usage.limits:
                try:
                    candidates.extend(self._limit(limit, cfg, now, locale))
                except Exception:
                    log.exception("alert check failed for limit %r", getattr(limit, "label", None))
            try:
                candidates.extend(self._expire(cfg, now, locale))
            except Exception:
                log.exception("alert check failed for ended windows")
            try:
                candidates.extend(self._credits(state.usage.spend, now, locale))
            except Exception:
                log.exception("alert check failed for usage credits")
        if suppressed or not cfg.enabled or _in_quiet_hours(cfg.quiet, now, self._tz):
            return []
        allowed = {"threshold": cfg.on_threshold, "reset": cfg.on_reset, "credits": cfg.on_credits}
        return [alert for alert in candidates if allowed.get(alert.kind, False)]

    def _limit(self, limit: Limit, cfg: _Config, now: datetime, locale: Locale) -> list[Alert]:
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
            self._tracks[lid] = _keep_name(_Track(_window_key(resets_at), resets_at, float(percent), float(percent),
                                                  {t for t in cfg.thresholds if t <= percent}, closed=ended), limit)
            return []
        _keep_name(track, limit)
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
                    title=_title(KEY + "reset", limit, locale),
                    message=_reset_text(limit, self._tz, locale),
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
                title=_title(KEY + "threshold", limit, locale, percent=formats.percent(str(top), locale)),
                message=_reset_text(limit, self._tz, locale),
            ))
        return out

    def _expire(self, cfg: _Config, now: datetime, locale: Locale) -> list[Alert]:
        """Reset alerts for the windows whose time has run out, whatever the usage API still shows.

        A window that ended is closed here, once: its level and thresholds are cleared and the next window that appears
        starts from a quiet baseline (see _limit). The alert needs the window to have reached the lowest threshold, as
        a reset announced through a change of window does.
        """
        out: list[Alert] = []
        moment = now.astimezone(timezone.utc)
        words = _words(locale)
        for (kind, label), track in self._tracks.items():
            if track.closed or moment < track.resets_at + _SAME_WINDOW:
                continue
            reached = track.percent
            track.closed, track.percent, track.high, track.done = True, 0.0, 0.0, set()
            if reached >= cfg.thresholds[0]:
                # Named now, in this evaluation's locale, from what named it at its last reading (a language changed
                # since then is the one the reset is told in). A limit with no name is "Usage".
                named = bool(_name(track, locale))
                out.append(Alert(
                    key=f"reset:{kind}:{label}:{track.key}",
                    kind="reset",
                    severity="info",
                    title=_title(KEY + "reset", track, locale) if named else str(words.text(KEY + "reset_unnamed")),
                    message=str(words.text(KEY + "back_to_zero", {"percent": formats.percent("0", locale)})),
                ))
        return out

    def _credits(self, spend: Spend | None, now: datetime, locale: Locale) -> list[Alert]:
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
            title=str(_words(locale).text(KEY + "credits.title")),
            message=_credits_text(spend, locale),
        )]

    # ── Settings ──

    def _locale(self) -> Locale:
        """The locale for this evaluation: the provider's, else English. A provider that fails gives English."""
        if self._locale_provider is None:
            return ENGLISH
        try:
            return self._locale_provider()
        except Exception:
            log.exception("the locale could not be read; the alerts are English")
            return ENGLISH

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


def _words(locale: Locale) -> i18n.Catalog:
    """The catalog of ``locale``'s language (English behind it), read once per language and process."""
    return i18n.app_catalog(locale.language)


def _starts(template: str) -> bool:
    """Whether what fills ``template``'s {name} starts it: no letter comes before the placeholder."""
    return not any(ch.isalpha() for ch in template.split("{name}", 1)[0])


def _title(key: str, limit: Limit | _Track, locale: Locale, **values: str) -> str:
    """An alert's title, ``key``'s template with the limit's name put in (and ``values``): "Session limit at 80%". The
    name is in sentence case where it starts the title (_name)."""
    words = _words(locale)
    name = _name(limit, locale, starts=_starts(words.text(key)))
    return str(words.text(key, {"name": name, **values}))


def _name(limit: Limit | _Track, locale: Locale, *, starts: bool = True) -> str:
    """The limit's name for a title, from the catalog: "Fable weekly", "Weekly scoped", "Session" (#90, #97). It is
    worded from the kind and the scope, not from the label. A limit with no label (none comes from the API) is named by
    its kind, else by its model. English cases the name with the words, as it always has ("Claude opus 5 weekly"). Any
    other language, the pseudo-locale too, takes the catalog's words as they are written and puts the name in as
    written, its first letter in capitals by the locale's rule where it starts the title (``starts``): "Claude Opus 5
    weekly", and "İstanbul weekly" for "istanbul" in Turkish. Only the name is cased, never the template's words."""
    words = _words(locale)
    if locale.language != DEFAULT:
        return " ".join(_other_name(limit, words, locale.language, starts).split())
    if (limit.label or "").strip() and limit.kind:
        text = _kind_words(limit, words)
    else:
        key = _FALLBACK_NAMES.get(limit.kind)
        text = words.text(key) if key else (limit.scope_model or words.text(KEY + "name.unlabelled"))
    text = " ".join(text.split())
    return text[:1].upper() + text[1:].lower()


def _other_name(limit: Limit | _Track, words: i18n.Catalog, language: str, starts: bool) -> str:
    """_name in a language other than English: the catalog's words as written, a name in as written, its first letter in
    capitals by ``language``'s rule where it starts the title. A kind no catalog has words for is named from its code
    (identity text, as the gates skin names it, #93 D4)."""
    if limit.kind in _FALLBACK_NAMES:
        return words.text(_FALLBACK_NAMES[limit.kind])
    labelled = bool((limit.label or "").strip()) and bool(limit.kind)
    if labelled and limit.kind == "weekly_scoped":
        scope = scope_name(limit.scope_model, limit.scope_surface)
        if not scope:
            return words.text(KEY + "name.scoped_unnamed")
        template = words.text(KEY + "name.scoped")
        first = starts and _starts(template)
        return words.text(KEY + "name.scoped", {"name": _first_up(scope, language) if first else scope})
    if labelled:
        name = limit.kind.replace("_", " ")
    elif limit.scope_model:
        name = limit.scope_model
    else:
        return words.text(KEY + "name.unlabelled")
    return _first_up(name, language) if starts else name


def _first_up(name: str, language: str) -> str:
    """``name`` as written, its first letter in capitals by ``language``'s rule (casing.cased)."""
    return casing.cased("{name}", "sentence", language, name=name)


def _kind_words(limit: Limit | _Track, words: i18n.Catalog) -> str:
    """English's name for a labelled limit, before _name cases it: the catalog's words, the scope in capitals."""
    if limit.kind in _FALLBACK_NAMES:
        return words.text(_FALLBACK_NAMES[limit.kind])
    if limit.kind == "weekly_scoped":
        scope = scope_name(limit.scope_model, limit.scope_surface)
        # Upper case first, as the label always was, so a name that upper-cases to two letters titles the same as before.
        return words.text(KEY + "name.scoped", {"name": scope.upper()}) if scope else words.text(
            KEY + "name.scoped_unnamed")
    return limit.kind.replace("_", " ").upper()


def _keep_name(track: _Track, limit: Limit) -> _Track:
    """Keep on ``track`` what names ``limit`` (_name reads these four), for a reset announced when its window ends."""
    track.kind, track.label = limit.kind, limit.label
    track.scope_model, track.scope_surface = limit.scope_model, limit.scope_surface
    return track


def _reset_text(limit: Limit, tz: tzinfo | None, locale: Locale) -> str:
    """"Resets 4:40 PM" for the session, "Resets Tue 4:00 PM" for the others: the reset time in ``locale``, as the
    widgets write it (formats.clock and formats.day_clock), in the catalog's words."""
    local = limit.resets_at.astimezone(tz)
    time = formats.clock(local, locale) if limit.kind == "session" else formats.day_clock(local, locale)
    return str(_words(locale).text(KEY + "resets", {"time": time}))


def _credits_text(spend: Spend, locale: Locale) -> str:
    """"Used 1,234.56 of 10,000.00 USD" in English (and the pseudo-locale's shapes), as always: the code after the
    last amount, as part of how English writes the figure. Another language writes each amount in its own way, US
    dollars as the plain "$" and another currency by its code, and no code after them: "Used 1 234,56 $ of
    10 000,00 $" in French (#91). The words are the catalog's (#97)."""
    of = _finite(spend.limit)
    if formats.english_shapes(locale):
        code = f" {spend.currency}" if spend.currency else ""
        used = formats.number(f"{spend.used:.2f}", locale)
        limit = formats.number(f"{spend.limit:.2f}", locale) + code if of else ""
        used += "" if of else code
    else:
        used = formats.money(spend.used, spend.currency, locale)
        limit = formats.money(spend.limit, spend.currency, locale) if of else ""
    key = KEY + ("credits.used_of" if of else "credits.used")
    return str(_words(locale).text(key, {"used": used, "limit": limit}))
