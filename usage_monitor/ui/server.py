"""Local API server: the webui and its JSON API on 127.0.0.1 (docs/UI_ARCHITECTURE.md, "Local API").

A ThreadingHTTPServer on loopback and a random port. Every request must send a Host header of
127.0.0.1 or localhost with that port (a DNS-rebinding guard). Every /api/ route also needs the
per-run X-Monitor-Token header, and the page itself (GET /) only opens with that same secret as ?k=...: the
token is written into the page, so a page anyone on the machine could fetch would hand it to every local user and
session. The address to open is ApiServer.url, key included. No CORS header is ever sent. A failing handler becomes
a JSON error and a log line; nothing is raised into the server thread.

The statistics routes (/api/analytics/<name>, /api/export) read through one extra, lazily opened
Store of their own, so a report never touches the data service's connection. Reports are cached
for a short while and computed one at a time; a request for a cached report never waits for one
that is being computed.
"""
from __future__ import annotations

import dataclasses
import enum
import hashlib
import hmac
import html
import importlib
import json
import math
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Hashable, Mapping
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from usage_monitor import paths
from usage_monitor.agent import guard as agent_guard
from usage_monitor.agent import sessions as agent_sessions
from usage_monitor.agent import setup as agent_setup
from usage_monitor.data import analytics
from usage_monitor.data.models import MonitorState, PlanInfo, UsageSnapshot
from usage_monitor.data.pricing import Pricing
from usage_monitor.data.store import SCHEMA_VERSION, Store, StoreError
from usage_monitor.data.usage_api import plan_from
from usage_monitor.log import get_logger
from usage_monitor.settings import DEFAULTS as SETTINGS_DEFAULTS
from usage_monitor.settings import Settings, parse_clock
from usage_monitor.ui import startup

log = get_logger(__name__)

DEV_ENV = "CUM_DEV_INSTANCE"
TOKEN_HEADER = "X-Monitor-Token"
KEY_PARAM = "k"                 # GET /?k=<the per-run token> opens the page; without it the answer is 401
MAX_BODY_BYTES = 64 * 1024
ANALYTICS_NAMES = ("cost", "efficiency", "pacing", "history", "sessions")
ANALYTICS_TTL_S = 20.0          # how long a computed report is served again without a new query
ANALYTICS_CACHE_MAX = 64        # entries kept; the oldest go first
EXPORT_FORMATS = {
    "csv": "text/csv; charset=utf-8",
    "json": "application/json; charset=utf-8",
}
EXPORT_FILE_PREFIX = "claude-usage-daily-"
SUPPORTERS_URL = (
    "https://raw.githubusercontent.com/Godimas101/personal-projects/main/patreon/supporters.json"
)
SUPPORTERS_TIMEOUT_S = 10.0
SUPPORTERS_TTL_S = 3600.0
SUPPORTERS_RETRY_S = 300.0      # a failed fetch is retried after 5 min, not after the full hour
MAX_SUPPORTERS_BYTES = 512 * 1024
THEME_ORDER = ("nostromo", "night_city", "gates")
UPDATE_OPERATIONS = ("check", "apply", "skip")
GUARD_SETUP_ACTIONS = ("install", "remove")
SETUP_STATUS_TTL_S = 60.0       # the setup status runs `claude` twice, so /api/guard keeps it this long (a click clears it)

_MISSING = object()
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
}
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}
_FONT_SUFFIXES = {".ttf", ".otf", ".woff", ".woff2"}
_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
_JSON = "application/json; charset=utf-8"


# ── Settings whitelist ───────────────────────────────────────────────────────────

def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def _is_int(low: int, high: int) -> Callable[[Any], bool]:
    def check(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high
    return check


def _is_real(low: float, high: float) -> Callable[[Any], bool]:
    def check(value: Any) -> bool:
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value) and low <= value <= high)
    return check


def _one_of(*choices: str) -> Callable[[Any], bool]:
    return lambda value: isinstance(value, str) and value in choices


def _nullable(check: Callable[[Any], bool]) -> Callable[[Any], bool]:
    return lambda value: value is None or check(value)


_SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
# The update block of /api/state (see _update_view). Both values come off the network via the release check, so
# only a plain version and a plain github.com address are passed on to the page.
_SAFE_VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,39}")
_RELEASE_URL = re.compile(r"https://github\.com/[0-9A-Za-z._~%+/-]{1,300}")


def plain_version(value: Any) -> str | None:
    """``value`` when it is a plain version ("2.1.0", "3.0.0-rc.1") fit for a page, a menu or a toast, else None."""
    return value if isinstance(value, str) and _SAFE_VERSION.fullmatch(value) else None


def _is_slug(value: Any) -> bool:
    return isinstance(value, str) and _SLUG.fullmatch(value) is not None


_is_billing_day = _is_int(1, 31)         # the windows module takes 1..31 and clamps to short months
_is_percent = _is_int(1, 100)
_is_real_price = _is_real(0, 10_000)


def _is_price(value: Any) -> bool:
    """A plan price in USD: more than zero, at most 10 000. (null clears the override.)"""
    return _is_real_price(value) and value > 0


def _is_thresholds(value: Any) -> bool:
    """1 to 5 different whole percentages from 1 to 100, the rule Settings applies to alert_thresholds."""
    return (isinstance(value, list) and 1 <= len(value) <= 5
            and all(_is_percent(item) for item in value) and len(set(value)) == len(value))


def _is_quiet_hours(value: Any) -> bool:
    """{"start": "HH:MM", "end": "HH:MM"} in 24-hour local time, nothing else."""
    return (isinstance(value, dict) and set(value) == {"start", "end"}
            and parse_clock(value["start"]) is not None and parse_clock(value["end"]) is not None)


_is_bounds_x = _is_int(-32000, 32000)
_is_bounds_w = _is_int(320, 8000)
_is_bounds_h = _is_int(240, 8000)


def _is_bounds(value: Any) -> bool:
    """Window bounds as the dashboard saves them: {x, y, w, h} integers, nothing else."""
    if not isinstance(value, dict) or set(value) != {"x", "y", "w", "h"}:
        return False
    return (_is_bounds_x(value["x"]) and _is_bounds_x(value["y"])
            and _is_bounds_w(value["w"]) and _is_bounds_h(value["h"]))


@dataclasses.dataclass(frozen=True)
class _Spec:
    check: Callable[[Any], bool]
    default: Any


# Every key POST /api/settings accepts. Anything else is a 400.
SETTING_SPECS: dict[str, _Spec] = {
    "theme_id": _Spec(_is_slug, "nostromo"),
    "theme_variant": _Spec(_is_slug, "amber"),
    "theme_mode": _Spec(_one_of("dark", "light", "system"), "system"),
    "poll_interval_ms": _Spec(_is_int(60_000, 3_600_000), 900_000),
    "show_floating": _Spec(_is_bool, True),
    "show_taskbar": _Spec(_is_bool, True),
    "float_transparent_bg": _Spec(_is_bool, False),
    "float_text_fx": _Spec(_is_bool, True),
    "taskbar_transparent_bg": _Spec(_is_bool, False),
    "taskbar_text_fx": _Spec(_is_bool, True),
    "show_scoped_rows": _Spec(_one_of("auto", "always", "never"), "auto"),
    "hide_floating_in_fullscreen": _Spec(_is_bool, False),
    "billing_day": _Spec(_nullable(_is_billing_day), None),
    "plan_price_override": _Spec(_nullable(_is_price), None),
    "reduce_motion": _Spec(_is_bool, False),
    "check_updates": _Spec(_is_bool, True),
    "launch_on_startup": _Spec(_is_bool, False),          # lives in the Run entry, not the file
    "alerts_enabled": _Spec(_is_bool, True),
    "alert_thresholds": _Spec(_is_thresholds, [80, 90, 100]),
    "alert_on_threshold": _Spec(_is_bool, True),
    "alert_on_reset": _Spec(_is_bool, True),
    "alert_on_credits": _Spec(_is_bool, True),
    "alert_quiet_hours": _Spec(_nullable(_is_quiet_hours), None),
    "dashboard_bounds": _Spec(_nullable(_is_bounds), None),
    # The usage guard (#27). A cap of null leaves that limit unwatched; the rules are Settings' own.
    "guard_enabled": _Spec(_is_bool, False),
    "guard_session_cap": _Spec(_nullable(_is_percent), 95),
    "guard_weekly_cap": _Spec(_nullable(_is_percent), 90),
    "guard_scoped_cap": _Spec(_nullable(_is_percent), None),
    "guard_resume_stagger_s": _Spec(_is_int(0, 600), 120),
}


# ── JSON encoding ────────────────────────────────────────────────────────────────

# Fields that are never sent: UsageSnapshot.raw is the whole API response, which the UI does not need.
_OMITTED_FIELDS = frozenset({"raw"})
# Properties worth sending because the UI cannot easily recompute them.
_DERIVED = {
    "TokenTotals": ("cache_write", "total_tokens", "cache_hit_rate", "caching_saved_usd"),
    "Limit": ("fraction",),
    "CostBreakdown": ("total",),
}


def iso_utc(value: datetime) -> str:
    """ISO 8601 in UTC with a trailing Z. A naive datetime is taken to be UTC."""
    aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return aware.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def to_jsonable(value: Any) -> Any:
    """Plain JSON types for the data classes: enums by value, datetimes as ISO UTC, no raw API body."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, enum.Enum):
        return to_jsonable(value.value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, datetime):
        return iso_utc(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        out = {f.name: to_jsonable(getattr(value, f.name))
               for f in dataclasses.fields(value) if f.name not in _OMITTED_FIELDS}
        for name in _DERIVED.get(type(value).__name__, ()):
            out[name] = to_jsonable(getattr(value, name))
        return out
    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_jsonable(item) for item in value]
    return str(value)


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not allowed in JSON")


# ── Themes (imported late: the loader is a sibling module that may change independently) ──

def _themes() -> Any:
    """The themes package, or 503 while it is unavailable. Imported on first use, not at import time.

    importlib is used on purpose: `from usage_monitor import themes` keeps returning a package
    attribute that an earlier import left behind, even when sys.modules no longer has the module.
    """
    try:
        return importlib.import_module("usage_monitor.themes")
    except ImportError:
        log.exception("themes package is not available")
        raise _HttpError(503, "themes are not available") from None


def _accent_base(variant_id: str) -> str:
    """'blue-dark' -> 'blue' (Gates accents); 'amber' stays 'amber'."""
    return re.sub(r"-(dark|light)$", "", variant_id)


def _default_variant(theme: Any) -> Any:
    """The theme's default variant (the first one when none is marked)."""
    return next((v for v in theme.variants if v.default), theme.variants[0])


def _css_vars(themes: Any, theme: Any, variant: Any) -> dict[str, str]:
    """Colour tokens as CSS custom properties: the loader's set, plus the variant's extra tokens."""
    css = {str(name): str(value) for name, value in themes.css_variables(theme, variant).items()}
    extras = theme.extras if isinstance(theme.extras, dict) else {}
    tokens: dict[str, Any] = {}
    per_variant = extras.get("variant_tokens")
    if isinstance(per_variant, dict) and isinstance(per_variant.get(variant.id), dict):
        tokens.update(per_variant[variant.id])
    if isinstance(variant.extras, dict):
        tokens.update(variant.extras)
    for name, value in tokens.items():
        if isinstance(value, str) and re.fullmatch(r"[a-z0-9_-]+", name):
            css.setdefault("--" + name.replace("_", "-"), value)
    return css


_SHELL_HTML_TAG = re.compile(r"<html\b[^>]*>")
_SHELL_SCHEME_META = re.compile(r'(<meta name="color-scheme" content=")[^"]*(")')
_SHELL_THEME_LINK = re.compile(r'<link id="theme-css"[^>]*>')
_CSS_VAR_NAME = re.compile(r"--[a-z0-9-]+")
_CSS_VAR_VALUE = re.compile(r"[^;{}<>\\\r\n]*")     # cannot end its declaration or leave the attribute


def _themed_shell(text: str, view: Mapping[str, Any] | None) -> str:
    """index.html with the active theme in it, so the very first paint is already that theme's.

    The page applies the same things once its first state arrives (applyTheme in app.js). Without
    this a Gates or Night City user sees the Nostromo shell for a frame or two first. Anything the
    shell does not have a marker for is left alone, and so is a theme that cannot be resolved.
    """
    if not view:
        return text
    theme_id, variant = str(view.get("id", "")), str(view.get("variant", ""))
    if not (_SLUG.fullmatch(theme_id) and _SLUG.fullmatch(variant)):
        return text
    scheme = "light" if view.get("mode") == "light" else "dark"
    declarations = [f"color-scheme:{scheme}"]
    declarations += [f"{name}:{value}" for name, value in (view.get("css_vars") or {}).items()
                     if _CSS_VAR_NAME.fullmatch(str(name)) and _CSS_VAR_VALUE.fullmatch(str(value))]

    def html_tag(match: re.Match[str]) -> str:
        tag = match.group(0)
        for attribute in ("data-theme", "data-variant", "style"):
            tag = re.sub(rf'\s{attribute}="[^"]*"', "", tag)
        style = html.escape(";".join(declarations), quote=True)
        return f'{tag[:-1]} data-theme="{theme_id}" data-variant="{variant}" style="{style}">'

    text = _SHELL_HTML_TAG.sub(html_tag, text, count=1)
    text = _SHELL_SCHEME_META.sub(lambda m: m.group(1) + scheme + m.group(2), text, count=1)
    if (view.get("assets") or {}).get("css"):
        link = f'<link id="theme-css" rel="stylesheet" href="/static/themes/{theme_id}.css" data-theme="{theme_id}">'
        text = _SHELL_THEME_LINK.sub(lambda m: link, text, count=1)
    return text


# ── Errors and small helpers ──────────────────────────────────────────────────────

class _HttpError(Exception):
    def __init__(self, status: int, message: str, *, allow: str | None = None, close: bool = False,
                 plain: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.allow = allow
        self.close = close
        self.plain = plain  # a plain-text body: used for static files, which are not JSON


def _reject_bad_body(exc: Exception) -> _HttpError:
    return _HttpError(400, f"request body must be JSON ({type(exc).__name__})")


def _etag_matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    if header.strip() == "*":
        return True
    return any(tag.strip() == etag for tag in header.split(","))


def _names(items: Any) -> list[str]:
    names: list[str] = []
    for item in items[:1000] if isinstance(items, list) else []:
        if isinstance(item, dict):
            item = item.get("name") or item.get("display_name") or item.get("username")
        if isinstance(item, str) and item.strip():
            names.append(item.strip()[:80])
    return names


def _supporter_groups(data: Any) -> list[dict[str, Any]]:
    """Supporters as [{tier, names}]. Reads {"tiers": [{tier, members}]} and a flat list (one unnamed group)."""
    if isinstance(data, dict) and isinstance(data.get("tiers"), list):
        groups: list[dict[str, Any]] = []
        for tier in data["tiers"][:50]:
            names = _names(tier.get("members")) if isinstance(tier, dict) else []
            if names:
                groups.append({"tier": str(tier.get("tier") or "")[:60], "names": names})
        return groups
    items = data.get("supporters") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("supporters.json has an unexpected shape")
    names = _names(items)
    return [{"tier": "", "names": names}] if names else []


def _default_opener(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": "ClaudeUsageMonitor", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (fixed https URL)
        data = response.read(MAX_SUPPORTERS_BYTES + 1)
    if len(data) > MAX_SUPPORTERS_BYTES:
        raise ValueError("supporters.json is larger than 512 KB")
    return data


class _Supporters:
    """One cached fetch of the supporters list. Failures are cached briefly and reported as text."""

    def __init__(self, url: str, opener: Callable[[str, float], bytes],
                 clock: Callable[[], float]) -> None:
        self._url = url
        self._opener = opener
        self._clock = clock
        self._lock = threading.Lock()
        self._entry: tuple[float, dict[str, Any]] | None = None

    def get(self) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            if self._entry is not None and now < self._entry[0]:
                return dict(self._entry[1], cached=True)
            try:
                groups = _supporter_groups(json.loads(self._opener(self._url, SUPPORTERS_TIMEOUT_S)))
                flat = [name for group in groups for name in group["names"]]
                payload: dict[str, Any] = {"supporters": flat, "tiers": groups, "error": None}
                ttl = SUPPORTERS_TTL_S
            except Exception as exc:  # any failure: an empty list plus the reason, never a 500
                log.warning("supporters list unavailable: %s", type(exc).__name__)
                payload = {"supporters": [], "tiers": [], "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
                ttl = SUPPORTERS_RETRY_S
            self._entry = (now + ttl, payload)
            return dict(payload, cached=False)


class _TimedValue:
    """One value that is slow to compute, kept for ``ttl`` seconds and computed by one caller at a time (the others
    wait for that answer). ``clear()`` drops it, and an answer that was still being computed then is not kept."""

    def __init__(self, compute: Callable[[], Any], ttl: float, clock: Callable[[], float]) -> None:
        self._compute = compute
        self._ttl = ttl
        self._clock = clock
        self._compute_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._entry: tuple[float, Any] | None = None
        self._generation = 0

    def get(self) -> Any:
        with self._compute_lock:
            with self._state_lock:
                if self._entry is not None and self._clock() < self._entry[0]:
                    return self._entry[1]
                generation = self._generation
            value = self._compute()
            with self._state_lock:
                if generation == self._generation:
                    self._entry = (self._clock() + self._ttl, value)
            return value

    def clear(self) -> None:
        with self._state_lock:
            self._generation += 1
            self._entry = None


# ── Statistics: query parameters, report inputs and the cached reader ────────────────

MAX_QUERY_FIELDS = 8
CLOSE_WAIT_S = 2.0              # how long stop() waits for a report that is running
_REQUIRED = object()


@dataclasses.dataclass(frozen=True)
class _Param:
    """One query parameter of a statistics route: its default (or _REQUIRED) and how to read its text."""

    default: Any
    parse: Callable[[str], Any]     # raises ValueError for text that is not a valid value


def _window_param(default: str) -> _Param:
    def parse(text: str) -> str:
        if text not in analytics.WINDOW_NAMES:
            raise ValueError("expected one of " + ", ".join(analytics.WINDOW_NAMES))
        return text

    return _Param(default, parse)


def _int_param(default: int, low: int, high: int) -> _Param:
    def parse(text: str) -> int:
        # ASCII digits only: int() would also take " 7", "+7", "7_0" and other scripts' digits.
        if re.fullmatch(r"[0-9]{1,6}", text) is None or not low <= int(text) <= high:
            raise ValueError(f"expected a whole number from {low} to {high}")
        return int(text)

    return _Param(default, parse)


def _format_param(text: str) -> str:
    if text not in EXPORT_FORMATS:
        raise ValueError("expected " + " or ".join(EXPORT_FORMATS))
    return text


# Every report and the query parameters it accepts. Anything else in the query string is a 400.
REPORT_PARAMS: dict[str, dict[str, _Param]] = {
    "cost": {"window": _window_param("cycle")},
    "efficiency": {"window": _window_param("month")},
    "pacing": {},
    "history": {"days": _int_param(371, 7, 800)},
    "sessions": {"window": _window_param("week"), "limit": _int_param(25, 1, 200)},
}
EXPORT_PARAMS: dict[str, _Param] = {"format": _Param(_REQUIRED, _format_param)}


def _read_params(query: str, spec: Mapping[str, _Param]) -> dict[str, Any]:
    """The query string as {name: value} for every parameter in ``spec``, defaults filled in.

    An unknown, repeated or unreadable parameter is a 400, so a typo never quietly answers with the
    default (and never makes another cache entry).
    """
    try:
        pairs = urllib.parse.parse_qsl(query, keep_blank_values=True, max_num_fields=MAX_QUERY_FIELDS,
                                       errors="strict")
    except ValueError:
        raise _HttpError(400, "bad query string") from None
    given: dict[str, str] = {}
    for name, text in pairs:
        if name not in spec:
            raise _HttpError(400, f"unknown parameter: {name[:40]}")
        if name in given:
            raise _HttpError(400, f"repeated parameter: {name}")
        given[name] = text
    values: dict[str, Any] = {}
    for name, param in spec.items():
        if name in given:
            try:
                values[name] = param.parse(given[name])
            except ValueError as exc:
                raise _HttpError(400, f"invalid {name}: {exc}") from None
        elif param.default is _REQUIRED:
            raise _HttpError(400, f"missing parameter: {name}")
        else:
            values[name] = param.default
    return values


@dataclasses.dataclass(frozen=True)
class _Inputs:
    """What a report needs besides the database: the clock, the live usage reading, the plan and two settings."""

    now: datetime
    usage: UsageSnapshot | None
    plan: PlanInfo
    plan_price: float | None
    billing_day: int | None


def _live_usage(usage: UsageSnapshot | None, now: datetime) -> UsageSnapshot | None:
    """``usage`` without the limits whose window is over, the rule the data service applies.

    A reading from before an outage must not pin the session or the week to a period that has
    ended: without the limit the windows module falls back to the clock and to the request history.
    """
    if usage is None:
        return None
    live = [limit for limit in usage.limits if limit.resets_at is not None and limit.resets_at > now]
    return dataclasses.replace(usage, limits=live)


def _window_of(store: Store, inputs: _Inputs, name: str) -> tuple[datetime | None, datetime | None]:
    return analytics.resolve_window(name, store, now=inputs.now, usage=inputs.usage,
                                    billing_day=inputs.billing_day)


def _cost_report(store: Store, pricing: Pricing, inputs: _Inputs, params: Mapping[str, Any]) -> dict:
    return analytics.cost_report(
        store, pricing, now=inputs.now, usage=inputs.usage, plan=inputs.plan,
        plan_price=inputs.plan_price, billing_day=inputs.billing_day, breakdown_window=params["window"])


def _efficiency_report(store: Store, pricing: Pricing, inputs: _Inputs, params: Mapping[str, Any]) -> dict:
    start, end = _window_of(store, inputs, params["window"])
    return analytics.efficiency_report(store, start=start, end=end)


def _pacing_report(store: Store, pricing: Pricing, inputs: _Inputs, params: Mapping[str, Any]) -> dict:
    return analytics.pacing_report(store, now=inputs.now, usage=inputs.usage)


def _history_report(store: Store, pricing: Pricing, inputs: _Inputs, params: Mapping[str, Any]) -> dict:
    return analytics.history_report(store, now=inputs.now, days=params["days"])


def _sessions_report(store: Store, pricing: Pricing, inputs: _Inputs, params: Mapping[str, Any]) -> dict:
    start, end = _window_of(store, inputs, params["window"])
    return analytics.sessions_report(store, pricing, start=start, end=end, limit=params["limit"])


_REPORTS: dict[str, Callable[[Store, Pricing, _Inputs, Mapping[str, Any]], dict]] = {
    "cost": _cost_report,
    "efficiency": _efficiency_report,
    "pacing": _pacing_report,
    "history": _history_report,
    "sessions": _sessions_report,
}


class _Reader:
    """Reads the statistics database for the API: one Store of its own, one report at a time, a short cache.

    The Store is opened on the first report, not at server start, through ``open_store`` (so the
    data service's connection is never shared). A report that fails on SQLite closes the Store and
    tries once more on a fresh one; a Store that cannot be opened is a 503 and is tried again on the
    next request. Reports run under ``_store_lock``, so two dashboards cannot make the same
    query twice; the cache has a lock of its own, so a request for a cached report never waits for
    one that is being computed.
    """

    def __init__(self, open_store: Callable[[], Store] | None, pricing: Callable[[], Pricing],
                 clock: Callable[[], float], ttl_s: float = ANALYTICS_TTL_S) -> None:
        self._open_store = open_store
        self._pricing = pricing
        self._clock = clock
        self._ttl_s = ttl_s
        self._store_lock = threading.Lock()
        self._store: Store | None = None
        self._cache_lock = threading.Lock()
        self._cache: dict[Hashable, tuple[float, bytes]] = {}

    def read(self, key: Hashable, compute: Callable[[Store, Pricing], bytes]) -> tuple[bytes, bool]:
        """The answer for ``key`` (``compute`` runs on the Store when it is not cached) and whether it was cached."""
        if self._open_store is None:
            raise _HttpError(501, "statistics are not available")
        body = self._cached(key)
        if body is not None:
            return body, True
        with self._store_lock:
            body = self._cached(key)        # another request may have computed it while this one waited
            if body is not None:
                return body, True
            began = time.perf_counter()
            body = self._run_locked(compute)
            self._remember(key, body)       # before the lock is let go, or the next waiter would compute it again
            log.debug("statistics report %s computed in %.0f ms", key[0] if isinstance(key, tuple) else key,
                      (time.perf_counter() - began) * 1000)
        return body, False

    def close(self) -> None:
        """Close the Store, after a report that is running has finished (waits up to CLOSE_WAIT_S for it)."""
        if not self._store_lock.acquire(timeout=CLOSE_WAIT_S):
            log.warning("a statistics report is still running; its database closes when the process ends")
            return
        try:
            self._discard_locked()
        finally:
            self._store_lock.release()

    # Store handling (callers hold _store_lock)

    def _run_locked(self, compute: Callable[[Store, Pricing], bytes]) -> bytes:
        for _attempt in range(2):
            store = self._store_locked()
            try:
                return compute(store, self._pricing())
            except (sqlite3.Error, StoreError) as exc:
                log.warning("a statistics query failed (%s); reopening the database", type(exc).__name__)
                self._discard_locked()
        raise _HttpError(503, "statistics are not available right now")

    def _store_locked(self) -> Store:
        if self._store is None:
            assert self._open_store is not None
            try:
                self._store = self._open_store()
            except (sqlite3.Error, StoreError, OSError) as exc:
                log.warning("could not open the statistics database (%s: %s)", type(exc).__name__, str(exc)[:120])
                raise _HttpError(503, "the statistics database could not be opened") from None
        return self._store

    def _discard_locked(self) -> None:
        store, self._store = self._store, None
        if store is None:
            return
        try:
            store.close()
        except Exception:  # a store that is being thrown away may well be broken
            log.debug("closing the statistics store failed", exc_info=True)

    # Cache

    def _cached(self, key: Hashable) -> bytes | None:
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if self._clock() >= entry[0]:
                del self._cache[key]
                return None
            return entry[1]

    def _remember(self, key: Hashable, body: bytes) -> None:
        now = self._clock()
        with self._cache_lock:
            for stale in [other for other, (expires, _body) in self._cache.items() if expires <= now]:
                del self._cache[stale]
            self._cache[key] = (now + self._ttl_s, body)
            while len(self._cache) > ANALYTICS_CACHE_MAX:   # the oldest entry has the earliest expiry
                del self._cache[min(self._cache, key=lambda other: self._cache[other][0])]


# ── HTTP plumbing ─────────────────────────────────────────────────────────────────

class _Httpd(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False
    api: ApiServer

    def handle_error(self, request: Any, client_address: Any) -> None:
        """Called by socketserver for an exception that left a handler (including one raised while the
        request line was read, before ApiServer._handle runs). A browser that cancels a request is not an
        error: on Windows that is a ConnectionResetError or ConnectionAbortedError (a socket timeout is the
        same kind of event), and the default would print a traceback to stderr every time. Anything else is a
        bug, logged with its traceback."""
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError)):
            log.debug("client closed the connection early: %s", type(exc).__name__)
            return
        log.exception("unhandled error while serving a request")


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ClaudeUsageMonitor"
    sys_version = ""

    def do_GET(self) -> None:
        self.server.api._handle(self, "GET")  # type: ignore[attr-defined]

    def do_POST(self) -> None:
        self.server.api._handle(self, "POST")  # type: ignore[attr-defined]

    def do_PUT(self) -> None:
        self.server.api._handle(self, "PUT")  # type: ignore[attr-defined]

    def do_DELETE(self) -> None:
        self.server.api._handle(self, "DELETE")  # type: ignore[attr-defined]

    def do_PATCH(self) -> None:
        self.server.api._handle(self, "PATCH")  # type: ignore[attr-defined]

    def do_OPTIONS(self) -> None:
        self.server.api._handle(self, "OPTIONS")  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 (base-class signature)
        # Method and path only: headers carry the token and are never logged.
        log.debug("api %s %s", self.command, self.path.split("?", 1)[0])


# ── The server ────────────────────────────────────────────────────────────────────

class ApiServer:
    """Serves the webui and the local JSON API. start() returns the bound port; stop() shuts it down.

    The statistics routes need ``analytics_store``, a callable that opens a Store on the usage
    database (called on the first statistics request, and again after a failure); the server closes
    what it returns. ``pricing_provider`` returns the data service's current pricing table (it is
    only used for display names), ``clock`` is a monotonic clock in seconds (the caches' time) and
    ``wall_clock`` the current time as an aware datetime.

    ``dashboard_closed_cb(pid)`` answers POST /api/dashboard/closed, with which the dashboard process says its window
    is gone while the process itself is still exiting (the launcher then starts a new one at the next request to
    open it). ``update_cb`` maps ``info`` (the newest release the update check found, as a dict, or None), ``check``,
    ``apply`` and ``skip`` to callables. The last three answer POST /api/update/<name> (a missing one is a 501);
    ``info`` feeds the ``update`` block of /api/state (see ``_update_view``).

    The usage guard (#27) reads and writes the session files in ``guard_dir`` (default: the app data folder; agent/
    sessions.py owns them) and sets up Claude Code through ``setup_cb``, a mapping of ``status``, ``install`` and
    ``remove`` to callables (default: agent/setup.py, except in a dev instance, which never edits the user's Claude
    Code settings and answers that setup is off). GET /api/guard keeps the setup status for SETUP_STATUS_TTL_S.
    """

    def __init__(
        self,
        state_provider: Callable[[], MonitorState | None],
        settings: Settings,
        *,
        refresh_cb: Callable[[], Any] | None,
        update_cb: Mapping[str, Callable[[], Any]],
        on_settings_applied: Callable[[dict[str, Any]], None] | None = None,
        dashboard_closed_cb: Callable[[int], Any] | None = None,
        version: str,
        dev: bool,
        port: int = 0,
        webui_dir: Path | None = None,
        supporters_url: str = SUPPORTERS_URL,
        supporters_opener: Callable[[str, float], bytes] | None = None,
        clock: Callable[[], float] | None = None,
        analytics_store: Callable[[], Store] | None = None,
        pricing_provider: Callable[[], Pricing | None] | None = None,
        wall_clock: Callable[[], datetime] | None = None,
        guard_dir: Path | None = None,
        setup_cb: Mapping[str, Callable[[], Any]] | None = None,
    ) -> None:
        self._state_provider = state_provider
        self._settings = settings
        self._refresh_cb = refresh_cb
        self._update_cb = dict(update_cb)
        self._on_settings_applied = on_settings_applied
        self._dashboard_closed_cb = dashboard_closed_cb
        self._version = str(version)
        # CUM_DEV_INSTANCE=1 makes a dev server whatever the caller says: the environment is how every
        # process of a dev run (and a test shell) is told not to touch the installed copy's state.
        self._dev = bool(dev) or os.environ.get(DEV_ENV) == "1"
        self._requested_port = int(port)
        self._webui_root = (webui_dir or default_webui_dir()).resolve()
        self._token = secrets.token_urlsafe(32)
        monotonic = clock or time.monotonic
        self._wall_clock = wall_clock or (lambda: datetime.now(timezone.utc))
        self._supporters = _Supporters(supporters_url, supporters_opener or _default_opener, monotonic)
        self._pricing_provider = pricing_provider
        self._fallback_pricing: Pricing | None = None
        self._reader = _Reader(analytics_store, self._current_pricing, monotonic)
        self._guard_dir = guard_dir
        self._setup_cb = dict(setup_cb) if setup_cb is not None else self._default_setup_cb()
        self._setup_status = _TimedValue(self._setup_status_now, SETUP_STATUS_TTL_S, monotonic)
        self._lock = threading.Lock()
        self._httpd: _Httpd | None = None
        self._thread: threading.Thread | None = None
        # A dev instance never touches the HKCU Run entry, which it shares with the installed copy.
        # Its "Start with Windows" switch lives here instead and is gone when the process ends.
        self._dev_launch_on_startup = False
        self.port = 0

    # ── Lifecycle ──

    @property
    def token(self) -> str:
        return self._token

    @property
    def dev(self) -> bool:
        return self._dev

    @property
    def url(self) -> str:
        """The address that opens the dashboard page: loopback, the port, and the key the page is served for."""
        return f"http://127.0.0.1:{self.port}/?{KEY_PARAM}={self._token}" if self.port else ""

    def start(self) -> int:
        with self._lock:
            if self._httpd is not None:
                return self.port
            httpd = _Httpd(("127.0.0.1", self._requested_port), _Handler)
            httpd.api = self
            self._httpd = httpd
            self.port = int(httpd.server_address[1])
            self._thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05},
                                            name="monitor-api", daemon=True)
            self._thread.start()
            log.info("local API listening on 127.0.0.1:%d", self.port)
            return self.port

    def stop(self) -> None:
        with self._lock:
            httpd, thread = self._httpd, self._thread
            self._httpd = None
            self._thread = None
        if httpd is None:
            return
        httpd.shutdown()
        httpd.server_close()
        if thread is not None:
            thread.join(timeout=5)
        self._reader.close()

    # ── Dispatch ──

    def _handle(self, req: _Handler, method: str) -> None:
        try:
            try:
                self._route(req, method)
            except _HttpError as exc:
                headers = {"Allow": exc.allow} if exc.allow else None
                if exc.plain:
                    self._send(req, exc.status, exc.message.encode("utf-8"), "text/plain; charset=utf-8",
                               headers, close=exc.close)
                else:
                    self._send_json(req, exc.status, {"error": exc.message}, headers, close=exc.close)
        except (ConnectionError, TimeoutError):   # BrokenPipe, Reset and Aborted are all ConnectionErrors
            log.debug("client closed the connection early")
        except Exception:  # a failing handler is a 500 and a log line, never a raised exception
            log.exception("API request failed: %s %s", method, req.path.split("?", 1)[0])
            try:
                self._send_json(req, 500, {"error": "internal error"})
            except OSError:   # the client is gone as well: nothing to tell it
                log.debug("client closed the connection before the error reply")

    def _route(self, req: _Handler, method: str) -> None:
        if not self._host_allowed(req):
            raise _HttpError(403, "forbidden host", close=True)
        target = urllib.parse.urlsplit(req.path)
        path = target.path
        if path == "/":
            self._only(method, "GET")
            if not self._key_ok(target.query):
                raise _HttpError(401, "This address needs the key the Claude Usage Monitor adds to it. "
                                      "Open the dashboard from the monitor's tray icon.", plain=True)
            self._send_index(req)
        elif path.startswith("/static/"):
            self._only(method, "GET")
            self._send_static(req, path[len("/static/"):])
        elif path.startswith("/api/"):
            if not self._token_ok(req):
                raise _HttpError(401, "missing or wrong token")
            self._api(req, method, path, target.query)
        else:
            raise _HttpError(404, "not found")

    def _host_allowed(self, req: _Handler) -> bool:
        host = (req.headers.get("Host") or "").strip().lower()
        return host in {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

    def _token_ok(self, req: _Handler) -> bool:
        given = req.headers.get(TOKEN_HEADER)
        if not given:
            return False
        return hmac.compare_digest(given.encode("utf-8", "surrogatepass"), self._token.encode("ascii"))

    def _key_ok(self, query: str) -> bool:
        """Whether the query of GET / holds the per-run token as its one ``k``. Compared in constant time."""
        try:
            given = urllib.parse.parse_qs(query, keep_blank_values=True, max_num_fields=16).get(KEY_PARAM, [])
        except ValueError:  # more fields than any real address has
            return False
        if len(given) != 1 or not given[0]:
            return False
        return hmac.compare_digest(given[0].encode("utf-8", "surrogatepass"), self._token.encode("ascii"))

    @staticmethod
    def _only(method: str, allowed: str) -> None:
        if method != allowed:
            raise _HttpError(405, "method not allowed", allow=allowed)

    def _api(self, req: _Handler, method: str, path: str, query: str = "") -> None:
        if path.startswith("/api/analytics/"):
            name = path[len("/api/analytics/"):]
            if name not in ANALYTICS_NAMES:
                raise _HttpError(404, "unknown report")
            self._only(method, "GET")
            self._get_analytics(req, name, query)
            return
        if path == "/api/export":
            self._only(method, "GET")
            self._get_export(req, query)
            return
        if path.startswith("/api/update/"):
            operation = path[len("/api/update/"):]
            if operation not in UPDATE_OPERATIONS:
                raise _HttpError(404, "not found")
            self._only(method, "POST")
            self._post_update(req, operation)
            return
        routes: dict[str, tuple[str, Callable[[_Handler], None]]] = {
            "/api/state": ("GET", self._get_state),
            "/api/themes": ("GET", self._get_themes),
            "/api/supporters": ("GET", self._get_supporters),
            "/api/guard": ("GET", self._get_guard),
            "/api/settings": ("POST", self._post_settings),
            "/api/refresh": ("POST", self._post_refresh),
            "/api/guard/resume": ("POST", self._post_guard_resume),
            "/api/guard/setup": ("POST", self._post_guard_setup),
            "/api/dashboard/closed": ("POST", self._post_dashboard_closed),
        }
        route = routes.get(path)
        if route is None:
            raise _HttpError(404, "not found")
        self._only(method, route[0])
        route[1](req)

    # ── Responses ──

    def _send(self, req: _Handler, status: int, body: bytes = b"", content_type: str = _JSON,
              headers: Mapping[str, str] | None = None, *, close: bool = False) -> None:
        req.send_response(status)
        if status != 304:  # a 304 has no body and no content headers
            req.send_header("Content-Type", content_type)
            req.send_header("Content-Length", str(len(body)))
        for name, value in _SECURITY_HEADERS.items():
            req.send_header(name, value)
        if "Cache-Control" not in (headers or {}):
            req.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            req.send_header(name, value)
        if close:
            req.send_header("Connection", "close")
            req.close_connection = True
        req.end_headers()
        if body and req.command != "HEAD":
            req.wfile.write(body)

    def _send_json(self, req: _Handler, status: int, payload: Any,
                   headers: Mapping[str, str] | None = None, *, close: bool = False) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send(req, status, body, _JSON, headers, close=close)

    def _send_index(self, req: _Handler) -> None:
        try:
            text = (self._webui_root / "index.html").read_text(encoding="utf-8")
        except OSError:
            raise _HttpError(500, "webui is missing index.html") from None
        text, count = re.subn(r'(<meta name="monitor-token" content=")[^"]*(")',
                              lambda m: m.group(1) + html.escape(self._token) + m.group(2), text, count=1)
        if count != 1:
            log.warning("index.html has no monitor-token meta tag")
        text = _themed_shell(text, self._theme_view())
        self._send(req, 200, text.encode("utf-8"), "text/html; charset=utf-8",
                   {"Content-Security-Policy": _CSP})

    def _send_static(self, req: _Handler, relative: str) -> None:
        target = self._static_file(relative)
        suffix = target.suffix.lower()
        if self._dev:
            cache = "no-store"
        elif suffix in _FONT_SUFFIXES:
            cache = "public, max-age=86400"
        else:
            cache = "no-cache"
        self._send(req, 200, target.read_bytes(), _CONTENT_TYPES.get(suffix, "application/octet-stream"),
                   {"Cache-Control": cache})

    def _static_file(self, relative: str) -> Path:
        """Resolve a /static/ path inside the webui folder, or 404. Rejects every traversal form."""
        decoded = urllib.parse.unquote(relative)
        parts = decoded.split("/")
        unsafe = (not decoded or "\x00" in decoded or "\\" in decoded or decoded.startswith("/")
                  or any(part in ("", ".", "..") or ":" in part for part in parts))
        if unsafe:
            raise _HttpError(404, "not found", plain=True)
        candidate = (self._webui_root / Path(*parts)).resolve()
        if not candidate.is_relative_to(self._webui_root) or not candidate.is_file():
            raise _HttpError(404, "not found", plain=True)
        return candidate

    # ── Read endpoints ──

    def _get_state(self, req: _Handler) -> None:
        state = self._state_provider()
        payload = {
            "state": to_jsonable(state) if state is not None else None,
            "model_names": self._model_names(state),
            "settings": self._settings_view(),
            "theme": self._theme_view(),
            "version": self._version,
            "update": self._update_view(),
            "dev": self._dev,
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        etag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
        headers = {"ETag": etag, "Cache-Control": "no-cache"}
        if _etag_matches(req.headers.get("If-None-Match"), etag):
            self._send(req, 304, b"", _JSON, headers)
            return
        self._send(req, 200, body, _JSON, headers)

    def _get_themes(self, req: _Handler) -> None:
        self._send_json(req, 200, {"themes": self._theme_entries()})

    def _get_supporters(self, req: _Handler) -> None:
        self._send_json(req, 200, self._supporters.get())

    def _get_guard(self, req: _Handler) -> None:
        """The usage guard as the Settings card shows it: the guard block of the state (``{}`` before the first
        cycle), the sessions told of this holding episode, the sessions waiting, and the setup status."""
        state = self._state_provider()
        guard = to_jsonable(state.guard) if state is not None and state.guard else {}
        folder = self._guard_folder()
        self._send_json(req, 200, {
            "guard": guard,
            "told": agent_sessions.told(folder, agent_guard.episode(guard)),
            "waiting": agent_sessions.waiting(folder),
            "setup": self._setup_status.get(),
        })

    def _get_analytics(self, req: _Handler, name: str, query: str) -> None:
        """One statistics report as JSON (shapes: docs/ARCHITECTURE.md, "Phase 2 - stats")."""
        params = _read_params(query, REPORT_PARAMS[name])
        inputs = self._report_inputs()
        # The settings are in the key: a changed billing day or plan price must not wait out the cache.
        key = (name, tuple(sorted(params.items())), inputs.billing_day, inputs.plan_price)
        report = _REPORTS[name]
        body, cached = self._reader.read(
            key, lambda store, pricing: _encode(report(store, pricing, inputs, params)))
        self._send(req, 200, body, _JSON, {"X-Cache": "HIT" if cached else "MISS"})

    def _get_export(self, req: _Handler, query: str) -> None:
        """The daily rollups as a CSV or JSON download."""
        fmt = _read_params(query, EXPORT_PARAMS)["format"]
        body, cached = self._reader.read(("export", fmt), lambda store, _pricing: _export_body(store, fmt))
        local_day = self._wall_clock().astimezone().date().isoformat()
        self._send(req, 200, body, EXPORT_FORMATS[fmt], {
            "Content-Disposition": f'attachment; filename="{EXPORT_FILE_PREFIX}{local_day}.{fmt}"',
            "X-Cache": "HIT" if cached else "MISS",
        })

    def _report_inputs(self) -> _Inputs:
        now = self._wall_clock()
        state = self._state_provider()
        return _Inputs(
            now=now,
            usage=_live_usage(state.usage, now) if state is not None else None,
            plan=state.plan if state is not None else plan_from(None, None),
            plan_price=self._settings.get("plan_price_override"),
            billing_day=self._settings.get("billing_day"),
        )

    def _current_pricing(self) -> Pricing:
        """The data service's pricing table; until it has one, the bundled table, loaded once."""
        if self._pricing_provider is not None:
            try:
                found = self._pricing_provider()
            except Exception:  # a provider that fails is a provider with nothing yet
                log.debug("the pricing provider failed", exc_info=True)
                found = None
            if found is not None:
                return found
        if self._fallback_pricing is None:
            self._fallback_pricing = Pricing.load()
        return self._fallback_pricing

    def _model_names(self, state: MonitorState | None) -> dict[str, str]:
        """Display names ("Opus 5.5") for every model id the state mentions. Unknown ids keep their own name."""
        if state is None:
            return {}
        ids = set(state.unpriced_models)
        for window in state.windows.values():
            ids.update(window.by_model)
        if not ids:
            return {}
        try:
            pricing = self._current_pricing()
            return {model: pricing.display_name(model) for model in sorted(ids)}
        except Exception:  # pricing is cosmetic here; the raw id is still correct
            log.debug("pricing unavailable for model names", exc_info=True)
            return {model: model for model in sorted(ids)}

    # ── Write endpoints ──

    def _post_settings(self, req: _Handler) -> None:
        applied = self._apply_settings(self._json_body(req))
        self._send_json(req, 200, {"ok": True, "applied": to_jsonable(applied),
                                   "settings": self._settings_view()})

    def apply_settings(self, body: Any) -> dict[str, Any]:
        """What POST /api/settings does, for code in this process (the dev server's --theme).

        Returns what was applied. A request the endpoint would refuse raises ValueError with its reason.
        """
        try:
            return self._apply_settings(body)
        except _HttpError as exc:
            raise ValueError(exc.message) from None

    def _apply_settings(self, body: Any) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise _HttpError(400, "expected a JSON object of settings")
        values: dict[str, Any] = {}
        for key, value in body.items():
            spec = SETTING_SPECS.get(key)
            if spec is None:
                raise _HttpError(400, f"unknown setting: {str(key)[:40]}")
            if not spec.check(value):
                raise _HttpError(400, f"invalid value for {key}")
            values[key] = value
        self._check_theme(values)

        applied: dict[str, Any] = {}
        if "launch_on_startup" in values:
            want = bool(values.pop("launch_on_startup"))
            self._set_launch_on_startup(want)
            applied["launch_on_startup"] = want
        if values:
            try:
                self._settings.update(values)    # Settings' own rules, as a last line behind the specs
            except ValueError as exc:
                raise _HttpError(400, str(exc)[:200]) from None
            applied.update(values)
        self._notify_applied(applied)
        return applied

    def _post_refresh(self, req: _Handler) -> None:
        self._read_body(req)  # no body is expected; drain whatever was sent
        if self._refresh_cb is None:
            raise _HttpError(501, "refresh is not available")
        self._refresh_cb()
        self._send_json(req, 200, {"ok": True})

    def _post_dashboard_closed(self, req: _Handler) -> None:
        """The dashboard process reports that its window is gone: ``{"pid": <its process id>}``."""
        body = self._json_body(req)
        pid = body.get("pid") if isinstance(body, dict) else None
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise _HttpError(400, "pid must be a process id")
        if self._dashboard_closed_cb is None:
            raise _HttpError(501, "the dashboard is not managed here")
        try:
            known = bool(self._dashboard_closed_cb(pid))
        except Exception:
            log.exception("dashboard-closed notice failed")
            raise _HttpError(500, "could not record the notice") from None
        self._send_json(req, 200, {"ok": True, "known": known})

    def _post_update(self, req: _Handler, operation: str) -> None:
        self._read_body(req)
        callback = self._update_cb.get(operation)
        if callback is None:
            raise _HttpError(501, f"update {operation} is not available")
        try:
            result = callback()
        except Exception:
            log.exception("update %s failed", operation)
            raise _HttpError(500, f"update {operation} failed") from None
        self._send_json(req, 200, {"ok": True, "result": to_jsonable(result)})

    def _post_guard_resume(self, req: _Handler) -> None:
        """"Resume now": every waiting ``--wait`` ends at once, whatever the limits say (agent/sessions.py)."""
        self._read_body(req)  # no body is expected; drain whatever was sent
        try:
            agent_sessions.resume_now(self._guard_folder())
        except OSError:
            log.exception("could not record the resume request")
            raise _HttpError(500, "could not record the resume request") from None
        self._send_json(req, 200, {"ok": True})

    def _post_guard_setup(self, req: _Handler) -> None:
        """``{"action": "install" | "remove"}``: set Claude Code up for the guard, or take it out again. The answer is
        the result of agent/setup.py as it is: ``ok`` false with ``errors`` is a refusal in plain words, not an HTTP
        error, so the page can show it beside the buttons."""
        body = self._json_body(req)
        action = body.get("action") if isinstance(body, dict) else None
        if action not in GUARD_SETUP_ACTIONS:
            raise _HttpError(400, 'action must be "install" or "remove"')
        callback = self._setup_cb.get(action)
        if callback is None:
            raise _HttpError(501, f"setup {action} is not available")
        try:
            result = callback()
        except Exception:
            log.exception("guard setup %s failed", action)
            raise _HttpError(500, f"setup {action} failed") from None
        finally:
            self._setup_status.clear()     # the next /api/guard looks again
        self._send_json(req, 200, to_jsonable(result))

    # ── Body handling ──

    def _read_body(self, req: _Handler) -> bytes:
        if req.headers.get("Transfer-Encoding"):
            raise _HttpError(411, "Content-Length is required", close=True)
        raw_length = req.headers.get("Content-Length")
        try:
            length = int(raw_length) if raw_length else 0
        except ValueError:
            raise _HttpError(400, "bad Content-Length", close=True) from None
        if length < 0:
            raise _HttpError(400, "bad Content-Length", close=True)
        if length > MAX_BODY_BYTES:
            # Drain (up to 1 MB) so the client reads the 413 instead of a connection reset.
            remaining = min(length, 1024 * 1024)
            while remaining > 0:
                chunk = req.rfile.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
            raise _HttpError(413, "request body is larger than 64 KB", close=True)
        return req.rfile.read(length) if length else b""

    def _json_body(self, req: _Handler) -> Any:
        raw = self._read_body(req)
        try:
            return json.loads(raw.decode("utf-8") or "null", parse_constant=_reject_constant)
        except (UnicodeDecodeError, ValueError) as exc:
            raise _reject_bad_body(exc) from None

    # ── Settings helpers ──

    def _set_launch_on_startup(self, want: bool) -> None:
        """Write the Run entry, or only remember the choice in a dev instance (see __init__)."""
        if self._dev:
            self._dev_launch_on_startup = want
            log.info("dev instance: start with Windows is %s in memory only; the sign-in entry was not touched",
                     "on" if want else "off")
            return
        try:
            startup.set_enabled(want)
        except OSError as exc:
            log.warning("could not change the startup entry: %s", exc)
            raise _HttpError(500, "could not change the startup entry") from None

    def _guard_folder(self) -> Path:
        """Where the guard's session files live: the folder given to the constructor, else the app data folder."""
        return self._guard_dir if self._guard_dir is not None else paths.app_data_dir()

    def _default_setup_cb(self) -> dict[str, Callable[[], Any]]:
        """The real setup, except in a dev instance: a test build never edits the user's Claude Code settings or
        registers itself with Claude Code, as it never touches the Run entry either (see __init__)."""
        if self._dev:
            reason = ("This is a dev instance, so one-click setup is off: a test build never edits your "
                      "Claude Code settings.")
            return {"status": lambda: agent_setup.unavailable_status(reason),
                    "install": lambda: agent_setup.refused("install", reason),
                    "remove": lambda: agent_setup.refused("remove", reason)}
        return {"status": agent_setup.status, "install": agent_setup.install, "remove": agent_setup.remove}

    def _setup_status_now(self) -> Any:
        getter = self._setup_cb.get("status")
        if getter is None:
            return agent_setup.unavailable_status("Setup is not available here.")
        try:
            return to_jsonable(getter())
        except Exception:  # a status that fails must not break the page that asks for it
            log.exception("guard setup status unavailable")
            return agent_setup.unavailable_status("The setup status could not be read right now.")

    def _current(self, key: str) -> Any:
        if key == "launch_on_startup":
            return self._dev_launch_on_startup if self._dev else startup.is_enabled()
        default = SETTINGS_DEFAULTS.get(key, SETTING_SPECS[key].default)
        return self._settings.get(key, default)

    def _settings_view(self) -> dict[str, Any]:
        return {key: to_jsonable(self._current(key)) for key in SETTING_SPECS}

    def _check_theme(self, values: dict[str, Any]) -> None:
        """Validate the theme keys of a POST and complete them in place, so what is stored always
        resolves to what was asked for.

        A theme switch without a variant stores the new theme's default variant with it (as the
        dashboard sends it); the old variant would belong to another theme. A whole Gates variant id
        such as "blue-dark" is stored as the accent ("blue") plus the mode ("dark") that resolve()
        reads; a theme_mode in the same request wins over the one in the id.
        """
        if "theme_id" not in values and "theme_variant" not in values:
            return
        themes = _themes()
        known = themes.load_themes()
        stored_id = self._settings.get("theme_id", "nostromo")
        theme = known.get(values.get("theme_id", stored_id))
        if theme is None:
            raise _HttpError(400, "unknown theme_id")
        if "theme_variant" not in values:
            if theme.id != stored_id:
                values["theme_variant"] = _accent_base(_default_variant(theme).id)
            return
        variant = values["theme_variant"]
        allowed = {v.id for v in theme.variants} | {_accent_base(v.id) for v in theme.variants}
        if variant not in allowed:
            raise _HttpError(400, "theme_variant does not belong to this theme")
        whole = next((v for v in theme.variants if v.id == variant and _accent_base(v.id) != v.id), None)
        if whole is not None:
            values["theme_variant"] = _accent_base(whole.id)
            if whole.mode in ("dark", "light"):
                values.setdefault("theme_mode", whole.mode)

    def _notify_applied(self, applied: dict[str, Any]) -> None:
        if self._on_settings_applied is None or not applied:
            return
        try:
            self._on_settings_applied(dict(applied))
        except Exception:  # the app's own hook must not turn a saved change into an error
            log.exception("on_settings_applied raised")

    def _theme_assets(self, theme_id: str) -> dict[str, bool]:
        """Which optional files ship for a theme, so the page never asks for one that is missing."""
        if not _SLUG.fullmatch(theme_id):
            return {"css": False, "js": False}
        folder = self._webui_root / "themes"
        return {"css": (folder / f"{theme_id}.css").is_file(), "js": (folder / f"{theme_id}.js").is_file()}

    def _theme_view(self) -> dict[str, Any] | None:
        try:
            themes = _themes()
            theme, variant = themes.resolve(self._settings)
            return {
                "id": theme.id,
                "name": theme.name,
                "variant": variant.id,
                "variant_name": variant.name,
                "mode": variant.mode,
                "css_vars": _css_vars(themes, theme, variant),
                "assets": self._theme_assets(theme.id),
            }
        except Exception:  # the page falls back to the Nostromo default when there is no theme
            log.exception("theme could not be resolved")
            return None

    def _theme_entries(self) -> list[dict[str, Any]]:
        themes = _themes()
        loaded = list(themes.load_themes().values())
        loaded.sort(key=lambda t: (THEME_ORDER.index(t.id) if t.id in THEME_ORDER else len(THEME_ORDER), t.id))
        entries: list[dict[str, Any]] = []
        for theme in loaded:
            default = _default_variant(theme).id
            shown = [themes.live_variant(theme, variant) for variant in theme.variants]   # as applied, not as shipped
            entries.append({
                "id": theme.id,
                "name": theme.name,
                "tagline": theme.tagline,
                "default_variant": default,
                "assets": self._theme_assets(theme.id),
                "modes": ["dark", "light", "system"] if any(v.mode == "light" for v in theme.variants) else [],
                "variants": [{
                    "id": variant.id,
                    "name": variant.name,
                    "accent": _accent_base(variant.id),
                    "default": variant.id == default,
                    "mode": variant.mode,
                    "colors": to_jsonable(variant.colors),
                    "css_vars": _css_vars(themes, theme, variant),
                } for variant in shown],
            })
        return entries

    def _update_view(self) -> dict[str, Any]:
        """The ``update`` block of /api/state: everything the dashboard's update banner and the Settings
        page need, built from what the app's ``info`` callback returns (None while no newer release is known).

            available        a newer release was found (also when the user skipped it)
            version          its version, "2.1.0" (None when the info carries no usable one)
            url              its release page, always a github.com link (None when the info has none)
            can_self_update  True when "Update now" installs it in place (the installed build, this project's own
                             installer with a SHA-256 to check); False when it opens the release page instead
            skipped          the user chose "Skip this version" for exactly this version
            info             the whole info dict, as before (version, tag, url, notes, asset_*, can_self_update)
            skip_version     the version that was skipped ("" for none)

        The banner shows when ``available and not skipped``. ``available``, ``info`` and ``skip_version`` are the
        original three keys; the rest are derived here from the info and the setting, so a callback that returns
        only ``{"version": ...}`` still gives a complete block."""
        info = None
        getter = self._update_cb.get("info")
        if getter is not None:
            try:
                info = to_jsonable(getter())
            except Exception:  # an update check that fails must not break the dashboard
                log.exception("update info unavailable")
        found = info if isinstance(info, dict) else {}
        skip_version = str(self._settings.get("skip_version", "") or "")
        version = plain_version(found.get("version"))
        url = found.get("url")
        url = url if isinstance(url, str) and _RELEASE_URL.fullmatch(url) else None
        return {
            "available": info is not None,
            "version": version,
            "url": url,
            "can_self_update": found.get("can_self_update") is True,
            "skipped": version is not None and version == skip_version,
            "info": info,
            "skip_version": skip_version,
        }


# ── Helpers shared with the dashboard and the dev server ─────────────────────────

def default_webui_dir() -> Path:
    """The webui folder beside this package; the frozen build keeps the same layout under _MEIPASS."""
    here = Path(__file__).resolve().parent.parent / "webui"
    if here.is_dir():
        return here
    return paths.resource_dir() / "usage_monitor" / "webui"


def open_statistics_store(path: Path) -> Store:
    """A Store on the usage database at ``path`` for the statistics routes, which only ever read.

    Read-only in spirit: this never creates the file and never upgrades it, because a second Store
    migrating a file at the same moment as its owner can make the loser fail. A file that is not there
    yet, or not at this build's schema yet, belongs to the data service (it makes and upgrades it in
    its first cycle). Until then this raises sqlite3.OperationalError, the routes answer 503 and the
    next request tries again. A file from a newer build raises StoreError.
    """
    if not path.is_file():
        raise sqlite3.OperationalError("the usage database has not been created yet")
    found = _schema_version(path)
    if found < SCHEMA_VERSION:
        raise sqlite3.OperationalError(f"the usage database is at schema {found}, not yet upgraded")
    return Store(path)


def _read_only_uri(path: Path) -> str:
    """A SQLite URI that opens ``path`` read-only and never creates it.

    A path on a network share (a UNC path, ``//server/share/...`` in posix form) has no ``file://server/...``
    form: SQLite refuses the authority. It wants the path itself, which starts with two slashes, after an
    empty authority: ``file:////server/share/...``. That is what keeps the statistics available when the app
    data folder is redirected to a share.
    """
    absolute = Path(os.path.abspath(path))
    posix = absolute.as_posix()
    if posix.startswith("//"):
        return "file://" + urllib.parse.quote(posix) + "?mode=ro"
    return absolute.as_uri() + "?mode=ro"


def _schema_version(path: Path) -> int:
    """The schema version a database file records, read through a connection that cannot write (0 if none)."""
    connection = sqlite3.connect(_read_only_uri(path), uri=True, timeout=5.0)
    try:
        row = connection.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    finally:
        connection.close()
    try:
        return int(row[0]) if row else 0
    except (TypeError, ValueError):
        return 0


def _encode(payload: Any) -> bytes:
    """A report as compact UTF-8 JSON. NaN and infinity never reach the page: to_jsonable makes them null."""
    return json.dumps(to_jsonable(payload), ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _export_body(store: Store, fmt: str) -> bytes:
    """The daily rollups, one row per local day with requests, as CSV or JSON text."""
    rows = analytics.daily_rollups(store)
    return (analytics.export_csv(rows) if fmt == "csv" else analytics.export_json(rows)).encode("utf-8")
