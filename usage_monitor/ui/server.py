"""Local API server: the webui and its JSON API on 127.0.0.1 (docs/UI_ARCHITECTURE.md, "Local API").

A ThreadingHTTPServer on loopback and a random port. Every request must send a Host header of
127.0.0.1 or localhost with that port (a DNS-rebinding guard). Every /api/ route also needs the
per-run X-Monitor-Token header. No CORS header is ever sent. A failing handler becomes a JSON
error and a log line; nothing is raised into the server thread.
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
import re
import secrets
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from usage_monitor import paths
from usage_monitor.data.models import MonitorState
from usage_monitor.log import get_logger
from usage_monitor.settings import DEFAULTS as SETTINGS_DEFAULTS
from usage_monitor.settings import Settings
from usage_monitor.ui import startup

log = get_logger(__name__)

TOKEN_HEADER = "X-Monitor-Token"
MAX_BODY_BYTES = 64 * 1024
SUPPORTERS_URL = (
    "https://raw.githubusercontent.com/Godimas101/personal-projects/main/patreon/supporters.json"
)
SUPPORTERS_TIMEOUT_S = 10.0
SUPPORTERS_TTL_S = 3600.0
SUPPORTERS_RETRY_S = 300.0      # a failed fetch is retried after 5 min, not after the full hour
MAX_SUPPORTERS_BYTES = 512 * 1024
THEME_ORDER = ("nostromo", "night_city", "gates")
UPDATE_OPERATIONS = ("check", "apply", "skip")

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


def _is_slug(value: Any) -> bool:
    return isinstance(value, str) and _SLUG.fullmatch(value) is not None


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
    "billing_day": _Spec(_nullable(_is_int(1, 31)), None),
    "plan_price_override": _Spec(_nullable(_is_real(0, 10_000)), None),
    "reduce_motion": _Spec(_is_bool, False),
    "check_updates": _Spec(_is_bool, True),
    "launch_on_startup": _Spec(_is_bool, False),          # lives in the Run entry, not the file
    "notifications_enabled": _Spec(_is_bool, True),
    "dashboard_bounds": _Spec(_nullable(_is_bounds), None),
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


# ── HTTP plumbing ─────────────────────────────────────────────────────────────────

class _Httpd(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False
    api: ApiServer


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
    """Serves the webui and the local JSON API. start() returns the bound port; stop() shuts it down."""

    def __init__(
        self,
        state_provider: Callable[[], MonitorState | None],
        settings: Settings,
        *,
        refresh_cb: Callable[[], Any] | None,
        update_cb: Mapping[str, Callable[[], Any]],
        on_settings_applied: Callable[[dict[str, Any]], None] | None = None,
        version: str,
        dev: bool,
        port: int = 0,
        webui_dir: Path | None = None,
        supporters_url: str = SUPPORTERS_URL,
        supporters_opener: Callable[[str, float], bytes] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._state_provider = state_provider
        self._settings = settings
        self._refresh_cb = refresh_cb
        self._update_cb = dict(update_cb)
        self._on_settings_applied = on_settings_applied
        self._version = str(version)
        self._dev = bool(dev)
        self._requested_port = int(port)
        self._webui_root = (webui_dir or default_webui_dir()).resolve()
        self._token = secrets.token_urlsafe(32)
        self._supporters = _Supporters(supporters_url, supporters_opener or _default_opener,
                                       clock or time.monotonic)
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
        return f"http://127.0.0.1:{self.port}/" if self.port else ""

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

    # ── Dispatch ──

    def _handle(self, req: _Handler, method: str) -> None:
        try:
            self._route(req, method)
        except _HttpError as exc:
            headers = {"Allow": exc.allow} if exc.allow else None
            if exc.plain:
                self._send(req, exc.status, exc.message.encode("utf-8"), "text/plain; charset=utf-8",
                           headers, close=exc.close)
            else:
                self._send_json(req, exc.status, {"error": exc.message}, headers, close=exc.close)
        except (BrokenPipeError, ConnectionResetError):
            log.debug("client closed the connection early")
        except Exception:  # a failing handler is a 500 and a log line, never a raised exception
            log.exception("API request failed: %s %s", method, req.path.split("?", 1)[0])
            self._send_json(req, 500, {"error": "internal error"})

    def _route(self, req: _Handler, method: str) -> None:
        if not self._host_allowed(req):
            raise _HttpError(403, "forbidden host", close=True)
        path = urllib.parse.urlsplit(req.path).path
        if path == "/":
            self._only(method, "GET")
            self._send_index(req)
        elif path.startswith("/static/"):
            self._only(method, "GET")
            self._send_static(req, path[len("/static/"):])
        elif path.startswith("/api/"):
            if not self._token_ok(req):
                raise _HttpError(401, "missing or wrong token")
            self._api(req, method, path)
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

    @staticmethod
    def _only(method: str, allowed: str) -> None:
        if method != allowed:
            raise _HttpError(405, "method not allowed", allow=allowed)

    def _api(self, req: _Handler, method: str, path: str) -> None:
        if re.fullmatch(r"/api/analytics/[A-Za-z0-9_-]{1,40}", path):
            self._only(method, "GET")
            raise _HttpError(404, "not yet available")
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
            "/api/settings": ("POST", self._post_settings),
            "/api/refresh": ("POST", self._post_refresh),
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
            "model_names": _model_names(state),
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

    # ── Write endpoints ──

    def _post_settings(self, req: _Handler) -> None:
        body = self._json_body(req)
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
                self._settings.update(values)
            except ValueError as exc:
                raise _HttpError(400, str(exc)[:200]) from None
            applied.update(values)
        self._notify_applied(applied)
        self._send_json(req, 200, {"ok": True, "applied": to_jsonable(applied),
                                   "settings": self._settings_view()})

    def _post_refresh(self, req: _Handler) -> None:
        self._read_body(req)  # no body is expected; drain whatever was sent
        if self._refresh_cb is None:
            raise _HttpError(501, "refresh is not available")
        self._refresh_cb()
        self._send_json(req, 200, {"ok": True})

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
            return
        try:
            startup.set_enabled(want)
        except OSError as exc:
            log.warning("could not change the startup entry: %s", exc)
            raise _HttpError(500, "could not change the startup entry") from None

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
        info = None
        getter = self._update_cb.get("info")
        if getter is not None:
            try:
                info = to_jsonable(getter())
            except Exception:  # an update check that fails must not break the dashboard
                log.exception("update info unavailable")
        return {"available": info is not None, "info": info,
                "skip_version": str(self._settings.get("skip_version", "") or "")}


# ── Helpers shared with the dashboard and the dev server ─────────────────────────

def default_webui_dir() -> Path:
    """The webui folder beside this package; the frozen build keeps the same layout under _MEIPASS."""
    here = Path(__file__).resolve().parent.parent / "webui"
    if here.is_dir():
        return here
    return paths.resource_dir() / "usage_monitor" / "webui"


_pricing_cache: Any = None


def _model_names(state: MonitorState | None) -> dict[str, str]:
    """Display names ("Opus 5.5") for every model id the state mentions. Unknown ids keep their own name."""
    global _pricing_cache
    if state is None:
        return {}
    ids = set(state.unpriced_models)
    for window in state.windows.values():
        ids.update(window.by_model)
    if not ids:
        return {}
    try:
        if _pricing_cache is None:
            from usage_monitor.data.pricing import Pricing  # noqa: PLC0415
            _pricing_cache = Pricing.load()
        return {model: _pricing_cache.display_name(model) for model in sorted(ids)}
    except Exception:  # pricing is cosmetic here; the raw id is still correct
        log.debug("pricing unavailable for model names", exc_info=True)
        return {model: model for model in sorted(ids)}
