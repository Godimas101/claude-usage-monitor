"""Usage API client: sign-in credentials, plan, GET /api/oauth/usage and its parser.

Contract: docs/ARCHITECTURE.md (usage_api) and data/models.py. The only network call is the
usage GET; the monitor never runs `claude` and never sends a model request. The OAuth token
is read here, sent as a bearer header, and never logged, stored or put in exception text.
"""
from __future__ import annotations

import http.client
import json
import math
import re
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, TypeVar

from usage_monitor.data.models import (
    ApiStatus,
    BreakdownRow,
    CredentialsInfo,
    DollarWindow,
    Limit,
    PlanInfo,
    Spend,
    UsageSnapshot,
)
from usage_monitor.data.netread import read_capped
from usage_monitor.log import get_logger
from usage_monitor.paths import credentials_path as _credentials_path

log = get_logger(__name__)

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
API_BETA = "oauth-2025-04-20"

RATE_BACKOFF_BASE_S = 60.0      # first wait after a 429; doubles per consecutive failure
NETWORK_BACKOFF_BASE_S = 30.0   # first wait after a network error, non-2xx or unusable body
BACKOFF_CAP_S = 900.0           # 15 min
RETRY_AFTER_CAP_S = 86_400.0    # sanity bound on a server-supplied Retry-After
MAX_BODY_BYTES = 2_000_000
BODY_BUDGET_S = 10.0            # the whole body must arrive within this, not just each socket read

_KIND_WINDOW_S = {"session": 18_000, "weekly_all": 604_800, "weekly_scoped": 604_800}
_LEGACY_RESERVED = frozenset({
    "five_hour", "seven_day", "extra_usage", "spend", "seven_day_breakdown",
    "limits", "weekly_scoped_shares",
})
_BACKOFF_STATUSES = frozenset({ApiStatus.RATE_LIMITED, ApiStatus.OFFLINE})
_EXPIRED_DETAIL = "Sign-in token expired; open Claude Code to refresh it"
_NO_SIGN_IN_DETAIL = "No Claude sign-in found; open Claude Code to sign in"
_FRACTION = re.compile(r"\.(\d+)")

Opener = Callable[..., Any]
Shares = dict[int, tuple[float | None, float | None]]
T = TypeVar("T")


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Refuses every redirect, so a 3xx comes back as an HTTP error and the bearer token is never resent."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


_DEFAULT_OPENER: Opener = urllib.request.build_opener(_NoRedirects).open


# ── Credentials and plan ──────────────────────────────────────────────────────

def read_credentials(path: Path | None = None) -> tuple[CredentialsInfo, str | None]:
    """Read the Claude sign-in block: (info, access token or None). Never logs the token."""
    info, token, _readable = inspect_credentials(path)
    return info, token


def inspect_credentials(path: Path | None = None) -> tuple[CredentialsInfo, str | None, bool]:
    """As read_credentials, plus ``readable``: False when the file exists but cannot be read or parsed.

    Such a file is often one Claude Code is in the middle of writing. Its info then says "absent"
    only for lack of anything better, so a caller keeps its last state instead of acting on it.
    """
    target = Path(path) if path is not None else _credentials_path()
    try:
        mtime = target.stat().st_mtime
    except FileNotFoundError:
        return _absent(None), None, True
    except OSError as exc:
        log.warning("credentials file could not be read (%s)", type(exc).__name__)
        return _absent(None), None, False
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:  # nesting too deep to parse counts as bad JSON
        log.warning("credentials file could not be read (%s)", type(exc).__name__)
        return _absent(mtime), None, False
    block = data.get("claudeAiOauth") if isinstance(data, dict) else None
    if not isinstance(block, dict):
        return _absent(mtime), None, True
    info = CredentialsInfo(
        present=True,
        expires_at=_expires_at(block.get("expiresAt")),
        mtime=mtime,
        plan=plan_from(_text(block.get("subscriptionType")), _text(block.get("rateLimitTier"))),
    )
    return info, _text(block.get("accessToken")), True


def plan_from(subscription_type: str | None, rate_limit_tier: str | None) -> PlanInfo:
    """Plan name and list price from the credentials' subscriptionType and rateLimitTier."""
    sub = _lower(subscription_type)
    tier = _lower(rate_limit_tier)
    if sub is None:
        name, price = "Unknown plan", None
    elif sub == "max":
        name, price = _max_plan(tier)
    elif sub == "pro":
        name, price = "Pro", 20.0
    else:
        name, price = sub.replace("_", " ").title(), None
    return PlanInfo(subscription_type=sub, rate_limit_tier=tier, name=name, monthly_price_usd=price)


def _max_plan(tier: str | None) -> tuple[str, float | None]:
    if tier and "max_20x" in tier:
        return "Max 20x", 200.0
    if tier and "max_5x" in tier:
        return "Max 5x", 100.0
    return "Max", None


def _absent(mtime: float | None) -> CredentialsInfo:
    return CredentialsInfo(present=False, expires_at=None, mtime=mtime, plan=plan_from(None, None))


def _expires_at(value: Any) -> datetime | None:
    millis = _num(value)
    if millis is None:
        return None
    try:
        return datetime.fromtimestamp(millis / 1000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


# ── Parser ────────────────────────────────────────────────────────────────────

def parse_usage(raw: dict, fetched_at: datetime) -> UsageSnapshot:
    """Map one usage response to a snapshot. Never raises; a malformed section is skipped."""
    snapshot = UsageSnapshot(fetched_at=_as_utc(fetched_at), raw=raw if isinstance(raw, dict) else {})
    if not isinstance(raw, dict):
        return snapshot
    snapshot.limits = _guarded("limits", lambda: _parse_limits(raw), [])
    snapshot.breakdown, snapshot.breakdown_window_start = _guarded(
        "breakdown", lambda: _parse_breakdown(raw.get("seven_day_breakdown")), ([], None))
    snapshot.spend = _guarded("spend", lambda: _parse_spend(raw.get("spend")), None)
    return snapshot


def _guarded(section: str, parse: Callable[[], T], default: T) -> T:
    try:
        return parse()
    except Exception as exc:  # the parse boundary: one bad section must not sink the snapshot
        log.debug("usage %s section skipped (%s)", section, type(exc).__name__)
        return default


def _parse_limits(raw: dict) -> list[Limit]:
    items = raw.get("limits")
    if isinstance(items, list):
        limits = _limits_from_list(items, _shares(raw.get("weekly_scoped_shares")))
        if limits:
            return limits
    return _legacy_limits(raw)


def _limits_from_list(items: list, shares: Shares) -> list[Limit]:
    limits: list[Limit] = []
    for index, entry in enumerate(items):
        limit = _guarded(f"limits[{index}]", lambda entry=entry: _limit_from_entry(entry), None)
        if limit is None:
            log.debug("usage limits[%d] skipped (no kind or percent)", index)
            continue
        if index in shares:  # shares point at limits[] positions, so use the raw index
            limit.allowance_pct, limit.used_pct_of_weekly = shares[index]
        limits.append(limit)
    return limits


def _limit_from_entry(entry: Any) -> Limit | None:
    if not isinstance(entry, dict):
        return None
    kind, percent = _text(entry.get("kind")), _num(entry.get("percent"))
    if kind is None or percent is None:
        return None
    model, surface = _scope(entry.get("scope"))
    return Limit(
        kind=kind,
        group=_text(entry.get("group")),
        percent=percent,
        severity=_text(entry.get("severity")),
        resets_at=_parse_reset(entry.get("resets_at")),
        is_active=entry.get("is_active") is True,
        label=_label(kind, model, surface),
        scope_model=model,
        scope_surface=surface,
        window_seconds=_KIND_WINDOW_S.get(kind),
        locked_reason=_text(entry.get("locked_reason")),
        dollars=_dollars(entry),
    )


def _legacy_limits(raw: dict) -> list[Limit]:
    """five_hour / seven_day / seven_day_<model> windows, plus any other non-null codename window."""
    keys = ["five_hour", "seven_day"] + [key for key in raw if key not in _LEGACY_RESERVED]
    limits = [_legacy_window(key, raw.get(key)) for key in keys]
    return [limit for limit in limits if limit is not None]


def _legacy_window(key: Any, value: Any) -> Limit | None:
    if not isinstance(key, str) or not isinstance(value, dict):
        return None
    percent = _num(value.get("utilization"))
    if percent is None:  # null codename windows carry no utilization and are ignored
        return None
    if key == "five_hour":
        kind, group, scope = "session", "session", None
    elif key == "seven_day":
        kind, group, scope = "weekly_all", "weekly", None
    elif key.startswith("seven_day_"):
        kind, group = "weekly_scoped", "weekly"
        scope = key[len("seven_day_"):].replace("_", " ").title() or None
    else:
        kind, group, scope = key, None, None
    return Limit(
        kind=kind,
        group=group,
        percent=percent,
        severity=None,
        resets_at=_parse_reset(value.get("resets_at")),
        is_active=False,  # legacy windows carry no is_active; left False rather than guessed
        label=_label(kind, scope, None),
        scope_model=scope,
        window_seconds=_KIND_WINDOW_S.get(kind),
        locked_reason=_text(value.get("locked_reason")),
        dollars=_dollars(value),
    )


def _shares(section: Any) -> Shares:
    """weekly_scoped_shares keyed by limits[] index: (allowance % of weekly, used % of weekly)."""
    shares: Shares = {}
    for item in (section if isinstance(section, list) else []):
        index = _int(item.get("limit_index")) if isinstance(item, dict) else None
        if index is not None:
            shares[index] = (_num(item.get("allowance_percent_of_weekly")),
                             _num(item.get("used_percent_of_weekly")))
    return shares


def _label(kind: str, scope_model: str | None, scope_surface: str | None) -> str:
    if kind == "session":
        return "SESSION"
    if kind == "weekly_all":
        return "WEEKLY"
    if kind == "weekly_scoped":
        name = scope_model or scope_surface
        return f"{name.upper()} WEEKLY" if name else "WEEKLY SCOPED"
    return kind.replace("_", " ").upper()


def _scope(scope: Any) -> tuple[str | None, str | None]:
    if not isinstance(scope, dict):
        return None, None
    model = scope.get("model")
    if isinstance(model, dict):
        name = _text(model.get("display_name")) or _text(model.get("id"))
    else:
        name = _text(model)
    return name, _text(scope.get("surface"))


def _dollars(obj: dict) -> DollarWindow | None:
    limit = _num(obj.get("limit_dollars"))
    used = _num(obj.get("used_dollars"))
    remaining = _num(obj.get("remaining_dollars"))
    if limit is None and used is None and remaining is None:
        return None
    return DollarWindow(limit=limit, used=used, remaining=remaining)


def _parse_breakdown(section: Any) -> tuple[list[BreakdownRow], datetime | None]:
    if not isinstance(section, dict):
        return [], None
    rows: list[BreakdownRow] = []
    for item in (section.get("rows") if isinstance(section.get("rows"), list) else []):
        if not isinstance(item, dict):
            continue
        key, percent = _text(item.get("key")), _num(item.get("percent"))
        if key is None or percent is None:
            continue
        rows.append(BreakdownRow(key=key, name=_text(item.get("display_name")) or key, percent=percent))
    return rows, _parse_ts(section.get("window_started_at"))


def _parse_spend(section: Any) -> Spend | None:
    if not isinstance(section, dict):
        return None
    used, used_currency = _money(section.get("used"))
    limit, limit_currency = _money(section.get("limit"))
    balance, balance_currency = _money(section.get("balance"))
    return Spend(
        enabled=section.get("enabled") is True,
        used=used,
        limit=limit,
        balance=balance,
        percent=_num(section.get("percent")),
        severity=_text(section.get("severity")),
        currency=used_currency or limit_currency or balance_currency,
        disabled_reason=_text(section.get("disabled_reason")),
    )


def _money(value: Any) -> tuple[float | None, str | None]:
    """{amount_minor, currency, exponent} -> (amount in currency units, currency).

    Bare numbers are not money here: their unit is unknown, so they are not guessed at.
    """
    if not isinstance(value, dict):
        return None, None
    currency = _text(value.get("currency"))
    minor = _num(value.get("amount_minor"))
    if minor is None:
        return None, currency
    exponent = _int(value.get("exponent"))
    exponent = 2 if exponent is None else min(max(exponent, 0), 6)
    return minor / 10 ** exponent, currency


def _parse_ts(value: Any) -> datetime | None:
    """ISO 8601 text (any fraction length, Z or offset) or epoch seconds, as aware UTC."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text[-1] in "Zz":
        text = text[:-1] + "+00:00"
    # datetime.fromisoformat on 3.10 only takes 3 or 6 fraction digits; normalise to 6.
    text = _FRACTION.sub(lambda m: "." + (m.group(1) + "000000")[:6], text, count=1)
    try:
        return _as_utc(datetime.fromisoformat(text))
    except (ValueError, OverflowError, OSError):  # year 9999 with a west offset overflows when made UTC
        return None



def _parse_reset(value: Any) -> datetime | None:
    """A limit's resets_at, rounded to the nearest minute.

    The API reports the same window end with sub-second jitter between fetches
    (07:39:59.89 one time, 07:40:00.39 the next), which would otherwise look like
    a new window to anything that compares reset times.
    """
    ts = _parse_ts(value)
    if ts is None:
        return None
    floor = ts.replace(second=0, microsecond=0)
    if ts.second < 30:
        return floor
    try:
        return floor + timedelta(minutes=1)
    except OverflowError:  # the last minute of year 9999
        return floor

# ── Client ────────────────────────────────────────────────────────────────────

@dataclass(slots=True)
class FetchResult:
    """Outcome of one UsageClient.fetch(). ``snapshot`` is the last good one, even on failure."""
    status: ApiStatus
    detail: str             # short UI text: "Updated 12:00:03", "next try at 12:01:03", ...
    snapshot: UsageSnapshot | None
    fresh: bool             # True only when this call received a new HTTP 200


class UsageClient:
    """Polls GET /api/oauth/usage through a backoff state machine. Thread-safe; fetch never raises.

    Expired token, or 401/403: STALE, no request until the credentials file's mtime changes.
    429: RATE_LIMITED, Retry-After if sent, else 60 s doubling to 15 min.
    Network error, 5xx, other non-2xx, unusable body: OFFLINE, 30 s doubling to 15 min.
    200: LIVE, backoff reset. Missing sign-in: NO_CREDENTIALS, re-checked on every fetch.
    A sign-in file that is unreadable (mid-write, say) keeps the status and backoff as they were.
    """

    def __init__(self, credentials_path: Path | None = None, *, timeout: float = 10,
                 opener: Opener | None = None, version: str = "dev",
                 user_agent: str | None = None) -> None:
        self._path = credentials_path
        self._timeout = timeout
        self._opener: Opener = opener or _DEFAULT_OPENER
        self._user_agent = user_agent or f"ClaudeUsageMonitor/{version}"
        self._lock = threading.Lock()
        self._base_detail = "Loading usage"
        self._failures = 0                  # consecutive failed attempts; drives the backoff
        self._stale_mtime: float | None = None
        self.status = ApiStatus.LOADING
        self.last_snapshot: UsageSnapshot | None = None
        self.last_ok_at: datetime | None = None
        self.next_allowed_at: datetime | None = None

    def fetch(self, now: datetime) -> FetchResult:
        when = _as_utc(now)
        with self._lock:
            try:
                return self._fetch(when)
            except Exception as exc:  # thread boundary: the poller must survive any failure here
                log.warning("usage fetch failed unexpectedly (%s)", type(exc).__name__)
                return self._offline(when, "unexpected error")

    def _fetch(self, now: datetime) -> FetchResult:
        if self.next_allowed_at is not None and now < self.next_allowed_at:
            log.debug("usage API backing off until %s", _clock(self.next_allowed_at))
            return self._result(fresh=False)
        info, token, readable = inspect_credentials(self._path)
        if not readable:  # probably mid-write: keep the status and the backoff, try again next time
            return self._result(fresh=False)
        if not info.present or not token:
            self._stale_mtime = None
            self._failures = 0
            self.next_allowed_at = None
            self._set(ApiStatus.NO_CREDENTIALS, _NO_SIGN_IN_DETAIL)
            return self._result(fresh=False)
        if info.is_expired(now):
            return self._stale(info.mtime, _EXPIRED_DETAIL)
        if self.status is ApiStatus.STALE and info.mtime == self._stale_mtime:
            return self._result(fresh=False)  # rejected token: wait for the file to change
        return self._request(token, info.mtime, now)

    def _request(self, token: str, mtime: float | None, now: datetime) -> FetchResult:
        request = urllib.request.Request(USAGE_URL, headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": API_BETA,
            "User-Agent": self._user_agent,
        })
        try:
            response = self._opener(request, timeout=self._timeout)
        except urllib.error.HTTPError as exc:
            try:
                return self._on_status(exc.code, exc.headers, now, mtime)
            finally:
                _close_quietly(exc)
        except (OSError, http.client.HTTPException) as exc:
            return self._on_network_error(exc, token, now)
        try:
            code = _status_code(response)
            if not 200 <= code < 300:
                return self._on_status(code, getattr(response, "headers", None), now, mtime)
            body = read_capped(response, MAX_BODY_BYTES + 1, BODY_BUDGET_S)
        except (OSError, http.client.HTTPException) as exc:
            return self._on_network_error(exc, token, now)
        finally:
            _close_quietly(response)
        return self._on_body(body, token, now)

    def _on_status(self, code: int, headers: Any, now: datetime, mtime: float | None) -> FetchResult:
        if code in (401, 403):
            log.warning("usage API rejected the sign-in (HTTP %d); waiting for the credentials file "
                        "to change", code)
            return self._stale(mtime, f"Sign-in rejected (HTTP {code}); open Claude Code to sign in again")
        if code == 429:
            return self._rate_limited(now, _retry_after_seconds(headers, now))
        return self._offline(now, f"HTTP {code}")

    def _on_network_error(self, exc: BaseException, token: str, now: datetime) -> FetchResult:
        log.debug("usage API request error: %s", _scrub(str(exc), token))
        return self._offline(now, _network_reason(exc))

    def _on_body(self, body: object, token: str, now: datetime) -> FetchResult:
        raw = _decode_body(body, token)
        if raw is None:
            return self._offline(now, "bad response")
        if not _looks_like_usage(raw):
            return self._offline(now, "unexpected response")
        self._succeed(parse_usage(raw, now), now)
        return self._result(fresh=True)

    def _succeed(self, snapshot: UsageSnapshot, now: datetime) -> None:
        if self.status is not ApiStatus.LIVE:
            log.info("usage API live (was %s)", self.status.value)
        self.last_snapshot = snapshot
        self.last_ok_at = now
        self.next_allowed_at = None
        self._failures = 0
        self._stale_mtime = None
        self._set(ApiStatus.LIVE, f"Updated {_clock(now)}")

    def _rate_limited(self, now: datetime, retry_after: float | None) -> FetchResult:
        wait = self._back_off(now, RATE_BACKOFF_BASE_S, retry_after)
        log.info("usage API rate limited; retrying in %d s", round(wait))
        self._set(ApiStatus.RATE_LIMITED, "Usage API rate limited")
        return self._result(fresh=False)

    def _offline(self, now: datetime, reason: str) -> FetchResult:
        wait = self._back_off(now, NETWORK_BACKOFF_BASE_S)
        log.warning("usage API unavailable (%s); retrying in %d s", reason, round(wait))
        self._set(ApiStatus.OFFLINE, f"Usage API unreachable ({reason})")
        return self._result(fresh=False)

    def _stale(self, mtime: float | None, detail: str) -> FetchResult:
        self._stale_mtime = mtime
        self._failures = 0
        self.next_allowed_at = None
        self._set(ApiStatus.STALE, detail)
        return self._result(fresh=False)

    def _back_off(self, now: datetime, base: float, retry_after: float | None = None) -> float:
        if retry_after is not None:
            wait = retry_after
        else:
            wait = min(base * 2 ** min(self._failures, 16), BACKOFF_CAP_S)
        self._failures += 1
        self.next_allowed_at = now + timedelta(seconds=wait)
        return wait

    def _set(self, status: ApiStatus, detail: str) -> None:
        self.status = status
        self._base_detail = detail

    def _result(self, *, fresh: bool) -> FetchResult:
        return FetchResult(status=self.status, detail=self._describe(),
                           snapshot=self.last_snapshot, fresh=fresh)

    def _describe(self) -> str:
        if self.next_allowed_at is not None and self.status in _BACKOFF_STATUSES:
            return f"{self._base_detail}; next try at {_clock(self.next_allowed_at)}"
        return self._base_detail


# ── Helpers ───────────────────────────────────────────────────────────────────

def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _lower(value: Any) -> str | None:
    text = _text(value)
    return text.lower() if text else None


def _num(value: Any) -> float | None:
    """A finite float from a number or numeric string; None for anything else (bools included)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _as_utc(value: Any) -> datetime:
    """Aware UTC datetime. Naive values are taken as UTC; a non-datetime falls back to now."""
    if not isinstance(value, datetime):
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _clock(value: datetime) -> str:
    return _as_utc(value).astimezone().strftime("%H:%M:%S")


def _status_code(response: Any) -> int:
    code = getattr(response, "status", None)
    if code is None and callable(getattr(response, "getcode", None)):
        code = response.getcode()
    return code if isinstance(code, int) else 200  # urlopen only returns on 2xx


def _header(headers: Any, name: str) -> str | None:
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None
    value = getter(name)
    if value is None:
        value = getter(name.lower())
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _retry_after_seconds(headers: Any, now: datetime) -> float | None:
    """Retry-After as seconds from ``now``: delta-seconds or an HTTP date. None when absent or invalid."""
    value = _header(headers, "Retry-After")
    if value is None:
        return None
    try:
        seconds: float | None = float(value)
    except ValueError:
        seconds = None
    if seconds is not None:
        if not math.isfinite(seconds) or seconds < 0:
            return None
        return min(seconds, RETRY_AFTER_CAP_S)
    try:
        when = _as_utc(parsedate_to_datetime(value))
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    return min(max((when - now).total_seconds(), 0.0), RETRY_AFTER_CAP_S)


def _network_reason(exc: BaseException) -> str:
    reason = getattr(exc, "reason", None)
    if isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError):
        return "timed out"
    return "network error"


def _scrub(text: str, secret: str) -> str:
    return text.replace(secret, "[redacted]") if secret else text


def _close_quietly(obj: Any) -> None:
    close = getattr(obj, "close", None)
    if not callable(close):
        return
    try:
        close()
    except (OSError, ValueError, AttributeError) as exc:  # already closed, or never fully opened
        log.debug("response close skipped (%s)", type(exc).__name__)


def _decode_body(body: object, secret: str) -> dict | None:
    """The JSON object in a 200 body, or None when it is not one or it echoes the token."""
    if isinstance(body, bytes):
        if len(body) > MAX_BODY_BYTES:
            return None
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return None
    elif isinstance(body, str):
        text = body
    else:
        return None
    if secret and secret in text:  # a response that echoes the token is refused, never stored
        return None
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def _looks_like_usage(raw: dict) -> bool:
    return any(key in raw for key in ("limits", "five_hour", "seven_day", "spend"))
