"""Tests for the local API server, startup entry, dashboard launcher and dev server.

Offline by design: the server listens on loopback only; the registry, the Win32 focus call, the
supporters fetch and the themes loader are replaced with fakes. Tests that create real windows,
tray icons or child processes are skipped unless CUM_UI_TESTS=1.
"""
from __future__ import annotations

import collections
import contextlib
import csv
import dataclasses
import html
import http.client
import http.server
import importlib
import io
import json
import logging
import math
import os
import re
import socket
import sqlite3
import struct
import subprocess
import sys
import threading
import time
import types
import urllib.error
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from usage_monitor.data import analytics
from usage_monitor.data import models as m
from usage_monitor.data.pricing import Pricing
from usage_monitor.data.store import Store
from usage_monitor.settings import Settings
from usage_monitor.ui import dashboard, devserver, server as server_mod, startup
from usage_monitor.ui.server import ApiServer, to_jsonable

UI_TESTS = os.environ.get("CUM_UI_TESTS") == "1"
# The autouse fixture points APPDATA and HOME at temp folders. Child processes started by the UI-gated
# tests need the real profile (pywebview lives in the user site-packages), so the values are kept here.
REAL_PROFILE_ENV = {k: os.environ[k] for k in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME", "PYTHONUSERBASE")
                    if k in os.environ}
ui_only = pytest.mark.skipif(not UI_TESTS, reason="needs CUM_UI_TESTS=1 (real windows or processes)")
NOW = datetime(2026, 10, 21, 16, 12, tzinfo=timezone.utc)


# ── Fakes ──────────────────────────────────────────────────────────────────────────

@dataclasses.dataclass
class FakeTheme:
    id: str
    name: str
    tagline: str
    variants: tuple["FakeVariant", ...]
    extras: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class FakeVariant:
    id: str
    name: str
    default: bool
    mode: str | None
    colors: dict[str, str]
    extras: dict[str, Any] = dataclasses.field(default_factory=dict)


def _variant(vid: str, name: str, mode: str = "dark", default: bool = False) -> FakeVariant:
    return FakeVariant(vid, name, default, mode, {"bg": "#090909", "text": "#e8a020", "accent": "#ffb300"})


FAKE_THEMES = {
    "nostromo": FakeTheme("nostromo", "Nostromo", "Amber on CRT black", (
        _variant("amber", "AMBER P3", default=True), _variant("green", "TERMINAL GREEN P1"))),
    "night_city": FakeTheme("night_city", "Night City", "Neon", (
        _variant("cyan", "CYAN", default=True),)),
    "gates": FakeTheme("gates", "Gates", "Fluent", (
        _variant("system-dark", "System accent - Dark", default=True),
        _variant("blue-dark", "Blue - Dark"),
        _variant("system-light", "System accent - Light", mode="light"),
        _variant("blue-light", "Blue - Light", mode="light"))),
}


LIVE_ACCENT = "#d0000c"


def _fake_themes_module() -> types.ModuleType:
    module = types.ModuleType("usage_monitor.themes")

    def load_themes(directory: Any = None) -> dict[str, FakeTheme]:
        return dict(FAKE_THEMES)

    def resolve(settings: Any) -> tuple[FakeTheme, FakeVariant]:
        theme = FAKE_THEMES.get(settings.get("theme_id", "nostromo"), FAKE_THEMES["nostromo"])
        wanted = settings.get("theme_variant", "amber")
        variant = next((v for v in theme.variants if v.id == wanted), None) or theme.variants[0]
        return theme, variant

    def css_variables(theme: FakeTheme, variant: FakeVariant) -> dict[str, str]:
        return {"--bg": variant.colors["bg"], "--text": variant.colors["text"], "--accent": variant.colors["accent"]}

    def live_variant(theme: FakeTheme, variant: FakeVariant, **_kw: Any) -> FakeVariant:
        """The Gates system variants take the Windows accent, as the real loader does."""
        if theme.id == "gates" and variant.id.startswith("system-"):
            return dataclasses.replace(variant, colors={**variant.colors, "accent": LIVE_ACCENT})
        return variant

    module.load_themes = load_themes  # type: ignore[attr-defined]
    module.resolve = resolve  # type: ignore[attr-defined]
    module.css_variables = css_variables  # type: ignore[attr-defined]
    module.live_variant = live_variant  # type: ignore[attr-defined]
    return module


def sample_state(*, status: m.ApiStatus = m.ApiStatus.LIVE, percent: float = 13.0) -> m.MonitorState:
    session = m.Limit(kind="session", group="session", percent=percent, severity="normal",
                      resets_at=NOW + timedelta(hours=3, minutes=27), is_active=True, label="SESSION",
                      window_seconds=18000)
    weekly = m.Limit(kind="weekly_all", group="weekly", percent=2.0, severity=None,
                     resets_at=NOW + timedelta(days=2, hours=23), is_active=True, label="WEEKLY",
                     window_seconds=604800)
    usage = m.UsageSnapshot(fetched_at=NOW - timedelta(seconds=12), limits=[session, weekly],
                            raw={"secret_looking_field": "keep me out of the JSON"})
    totals = m.TokenTotals(requests=10, input=2, output=8, cache_read=96, cache_write_5m=2,
                           cache_write_1h=0, thinking=3, cost_usd=84.2, nocache_cost_usd=100.0)
    month = m.WindowStats(name="month", start=NOW - timedelta(days=20), end=None, totals=totals,
                          by_model={"claude-opus-5-5": totals})
    return m.MonitorState(
        status=status, status_detail="Live usage from the Claude API.", usage=usage,
        usage_fetched_at=usage.fetched_at, plan=m.PlanInfo("max", "default_claude_max_5x", "Max 5x", 100.0),
        windows={"month": month}, burn=m.BurnRate(1240.0, 6.1, "NORMAL", 30),
        unpriced_models=["claude-opus-6-preview"],
        ingest=m.IngestProgress(running=False, files_done=3, files_total=3), updated_at=NOW)


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing in these tests may reach the real app data, registry or home folder."""
    monkeypatch.setenv("CUM_APPDATA_DIR", str(tmp_path / "app"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.delenv(server_mod.DEV_ENV, raising=False)    # a dev shell must not turn "dev=False" tests into dev ones


@pytest.fixture
def themes_module(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    module = _fake_themes_module()
    monkeypatch.setitem(sys.modules, "usage_monitor.themes", module)
    return module


REAL_IS_ENABLED = startup.is_enabled
REAL_SET_ENABLED = startup.set_enabled


@pytest.fixture(autouse=True)
def startup_fake(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """An in-memory stand-in for the HKCU Run entry. Every test gets it, so none touches the registry."""
    store: dict[str, Any] = {"on": False, "fail": False, "calls": []}

    def is_enabled() -> bool:
        return store["on"]

    def set_enabled(on: bool) -> None:
        store["calls"].append(on)
        if store["fail"]:
            raise OSError("registry refused")
        store["on"] = bool(on)

    monkeypatch.setattr(startup, "is_enabled", is_enabled)
    monkeypatch.setattr(startup, "set_enabled", set_enabled)
    return store


class Recorder:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.applied: list[dict[str, Any]] = []
        self.state: m.MonitorState | None = sample_state()


@pytest.fixture
def settings(tmp_path: Path) -> Iterator[Settings]:
    store = Settings(tmp_path / "settings.json", save_delay_s=0.0)
    yield store
    store.close()


@pytest.fixture
def webui(tmp_path: Path) -> Path:
    root = tmp_path / "webui"
    (root / "themes").mkdir(parents=True)
    (root / "fonts").mkdir()
    (root / "index.html").write_text(
        '<!doctype html><html><head><meta name="monitor-token" content=""></head><body>ok</body></html>',
        encoding="utf-8")
    (root / "app.js").write_text("console.log('app');", encoding="utf-8")
    (root / "themes" / "nostromo.css").write_text("body{}", encoding="utf-8")
    (root / "fonts" / "vt323.ttf").write_bytes(b"\x00\x01\x00\x00fake")
    (root / "fonts" / "OFL.txt").write_text("licence", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside the webui", encoding="utf-8")
    return root


@pytest.fixture
def make_server(webui: Path, settings: Settings) -> Iterator[Callable[..., tuple[ApiServer, Recorder]]]:
    started: list[ApiServer] = []

    def factory(**overrides: Any) -> tuple[ApiServer, Recorder]:
        rec = Recorder()

        def refresh() -> None:
            rec.calls.append("refresh")

        options: dict[str, Any] = dict(
            state_provider=lambda: rec.state,
            settings=settings,
            refresh_cb=refresh,
            update_cb={
                "check": lambda: (rec.calls.append("check"), {"available": False})[1],
                "apply": lambda: (rec.calls.append("apply"), {"applied": True})[1],
                "skip": lambda: (rec.calls.append("skip"), {"skipped": True})[1],
            },
            on_settings_applied=rec.applied.append,
            version="2.0.0-test",
            dev=False,
            webui_dir=webui,
            supporters_opener=lambda url, timeout: b'{"supporters": ["Ada", {"name": "Grace"}]}',
        )
        options.update(overrides)
        server = ApiServer(**options)
        server.start()
        started.append(server)
        return server, rec

    yield factory
    for server in started:
        server.stop()


def request(server: ApiServer, method: str, path: str, *, body: bytes | None = None,
            headers: dict[str, str] | None = None, token: bool = True,
            host: str | None = None) -> tuple[int, dict[str, str], bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    try:
        conn.putrequest(method, path, skip_host=host is not None, skip_accept_encoding=True)
        if host is not None:
            conn.putheader("Host", host)
        all_headers = dict(headers or {})
        if token and path.startswith("/api/") and TOKEN_HEADER not in all_headers:
            all_headers[TOKEN_HEADER] = server.token
        if body is not None:
            all_headers.setdefault("Content-Type", "application/json")
            all_headers["Content-Length"] = str(len(body))
        for name, value in all_headers.items():
            conn.putheader(name, value)
        conn.endheaders()
        if body is not None:
            conn.send(body)
        response = conn.getresponse()
        data = response.read()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, data
    finally:
        conn.close()


TOKEN_HEADER = "X-Monitor-Token"


def post_json(server: ApiServer, path: str, payload: Any) -> tuple[int, Any]:
    status, _h, data = request(server, "POST", path, body=json.dumps(payload).encode("utf-8"))
    return status, json.loads(data.decode("utf-8")) if data else None


# ── Lifecycle, host and token ─────────────────────────────────────────────────────

def test_start_binds_loopback_on_a_random_port(make_server: Any) -> None:
    server, _ = make_server()
    assert server.port > 0
    assert server.url == f"http://127.0.0.1:{server.port}/"
    assert len(server.token) >= 40
    assert server.start() == server.port  # idempotent


def test_stop_releases_the_port(webui: Path, settings: Settings) -> None:
    server = ApiServer(lambda: None, settings, refresh_cb=None, update_cb={}, version="t", dev=False,
                       webui_dir=webui)
    port = server.start()
    server.stop()
    with socket.socket() as probe:
        probe.settimeout(1)
        assert probe.connect_ex(("127.0.0.1", port)) != 0


@pytest.mark.parametrize("host", ["evil.example:PORT", "127.0.0.1", "127.0.0.1:1", "[::1]:PORT", "", "127.0.0.1.nip.io:PORT"])
def test_wrong_host_header_is_forbidden(make_server: Any, host: str) -> None:
    server, _ = make_server()
    value = host.replace("PORT", str(server.port))
    status, headers, _ = request(server, "GET", "/", host=value)
    assert status == 403
    assert "access-control-allow-origin" not in headers


@pytest.mark.parametrize("host", ["127.0.0.1:PORT", "localhost:PORT", "LOCALHOST:PORT"])
def test_loopback_hosts_with_the_right_port_are_allowed(make_server: Any, host: str) -> None:
    server, _ = make_server()
    status, _, _ = request(server, "GET", "/", host=host.replace("PORT", str(server.port)))
    assert status == 200


def test_api_requires_the_token(make_server: Any) -> None:
    server, _ = make_server()
    assert request(server, "GET", "/api/state", token=False)[0] == 401
    assert request(server, "GET", "/api/state", token=False, headers={TOKEN_HEADER: "nope"})[0] == 401
    assert request(server, "GET", "/api/state", token=False, headers={TOKEN_HEADER: "é"})[0] == 401
    assert request(server, "GET", "/api/state")[0] == 200


def test_unknown_api_paths_need_the_token_before_they_404(make_server: Any) -> None:
    server, _ = make_server()
    assert request(server, "GET", "/api/nope", token=False)[0] == 401
    assert request(server, "GET", "/api/nope")[0] == 404


def test_index_carries_the_token_in_the_meta_tag(make_server: Any) -> None:
    server, _ = make_server()
    status, headers, body = request(server, "GET", "/")
    assert status == 200
    assert headers["content-type"].startswith("text/html")
    assert f'<meta name="monitor-token" content="{server.token}">' in body.decode("utf-8")
    assert "default-src 'self'" in headers["content-security-policy"]
    assert headers["cache-control"] == "no-store"


# The shell the real index.html has the markers of: the html tag, the colour-scheme meta, the theme link.
SHELL = (
    '<!doctype html>\n<html lang="en" data-theme="nostromo">\n<head>\n'
    '<meta name="color-scheme" content="dark">\n<meta name="monitor-token" content="">\n'
    '<link id="theme-css" rel="stylesheet" href="/static/themes/nostromo.css">\n</head>\n<body>ok</body>\n</html>\n'
)


@pytest.fixture
def themed_webui(webui: Path) -> Path:
    (webui / "index.html").write_text(SHELL, encoding="utf-8")
    for name in ("gates", "night_city"):
        (webui / "themes" / f"{name}.css").write_text("body{}", encoding="utf-8")
    return webui


def shell_tokens(page: str) -> dict[str, str]:
    """The custom properties the shell's html tag carries, as the browser reads them."""
    style = html.unescape(re.search(r'<html[^>]* style="([^"]*)"', page).group(1))
    return dict(item.split(":", 1) for item in style.split(";") if item.startswith("--"))


def test_index_opens_in_the_active_theme_so_the_first_paint_is_not_the_nostromo_shell(
        themed_webui: Path, themes_module: Any, make_server: Any, settings: Settings) -> None:
    settings.update({"theme_id": "gates", "theme_variant": "blue-light"})
    server, _ = make_server(webui_dir=themed_webui)
    page = request(server, "GET", "/")[2].decode("utf-8")
    assert '<html lang="en" data-theme="gates" data-variant="blue-light" style="color-scheme:light;' in page
    assert '<meta name="color-scheme" content="light">' in page
    assert '<link id="theme-css" rel="stylesheet" href="/static/themes/gates.css" data-theme="gates">' in page
    assert "nostromo.css" not in page and 'data-theme="nostromo"' not in page
    assert f'<meta name="monitor-token" content="{server.token}">' in page          # the token is still in
    assert shell_tokens(page) == {"--bg": "#090909", "--text": "#e8a020", "--accent": "#ffb300"}


def test_index_keeps_the_nostromo_shell_for_nostromo(
        themed_webui: Path, themes_module: Any, make_server: Any) -> None:
    server, _ = make_server(webui_dir=themed_webui)
    page = request(server, "GET", "/")[2].decode("utf-8")
    assert 'data-theme="nostromo" data-variant="amber" style="color-scheme:dark;' in page
    assert '<meta name="color-scheme" content="dark">' in page
    assert 'href="/static/themes/nostromo.css" data-theme="nostromo"' in page


@pytest.mark.parametrize(("settings_change", "theme_id"), [
    ({"theme_id": "nostromo", "theme_variant": "green"}, "nostromo"),
    ({"theme_id": "night_city", "theme_variant": "edgerun"}, "night_city"),
    ({"theme_id": "gates", "theme_variant": "blue", "theme_mode": "light"}, "gates"),
])
def test_the_shell_tokens_are_the_ones_the_page_applies_when_its_state_arrives(
        webui: Path, make_server: Any, settings: Settings, settings_change: dict[str, str], theme_id: str) -> None:
    """The shipped themes, not fakes: what the first paint has equals what applyTheme sets a moment later
    (so the swap changes nothing), and no shipped value is dropped by the shell's safety filter."""
    for name in ("nostromo", "night_city", "gates"):
        (webui / "themes" / f"{name}.css").write_text("body{}", encoding="utf-8")
    (webui / "index.html").write_text(SHELL, encoding="utf-8")
    settings.update(settings_change)
    server, _ = make_server(webui_dir=webui)
    page = request(server, "GET", "/")[2].decode("utf-8")
    theme = json.loads(request(server, "GET", "/api/state")[2])["theme"]
    assert theme["id"] == theme_id
    assert f'data-theme="{theme_id}" data-variant="{theme["variant"]}"' in page
    assert f'href="/static/themes/{theme_id}.css" data-theme="{theme_id}"' in page
    assert len(theme["css_vars"]) > 20
    assert shell_tokens(page) == theme["css_vars"]


def test_index_leaves_the_link_alone_when_the_theme_ships_no_stylesheet(
        webui: Path, themes_module: Any, make_server: Any, settings: Settings) -> None:
    (webui / "index.html").write_text(SHELL, encoding="utf-8")       # no night_city.css next to it
    settings.update({"theme_id": "night_city", "theme_variant": "cyan"})
    server, _ = make_server(webui_dir=webui)
    page = request(server, "GET", "/")[2].decode("utf-8")
    assert 'data-theme="night_city" data-variant="cyan"' in page
    assert 'href="/static/themes/nostromo.css">' in page             # the page swaps in its fallback itself


def test_index_is_served_as_it_is_when_the_theme_cannot_be_resolved(
        themed_webui: Path, make_server: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server_mod, "_themes", lambda: (_ for _ in ()).throw(RuntimeError("no themes")))
    server, _ = make_server(webui_dir=themed_webui)
    status, _headers, body = request(server, "GET", "/")
    assert status == 200
    assert body.decode("utf-8") == SHELL.replace('content=""', f'content="{server.token}"')


def test_themed_shell_does_not_let_a_token_value_out_of_its_attribute() -> None:
    view = {"id": "gates", "variant": "blue-dark", "mode": "dark", "assets": {"css": True}, "css_vars": {
        "--ok": "#fff", "--quoted": '"Segoe UI", sans-serif', "--bad name": "1", "Bad": "1", "--semi": "red;background:url(x)",
        "--close": 'red"><script>alert(1)</script>', "--brace": "red}body{x:y", "--line": "red\nblue", "--slash": "a\\b"}}
    page = server_mod._themed_shell(SHELL, view)
    assert "<script>" not in page and "alert(1)" not in page
    style = re.search(r'<html[^>]* style="([^"]*)"', page).group(1)
    assert '"' not in style and "&quot;Segoe UI&quot;, sans-serif" in style
    assert shell_tokens(page) == {"--ok": "#fff", "--quoted": '"Segoe UI", sans-serif'}
    assert page.count("<html") == 1 and page.count("<link") == 1 and page.count("<meta") == 2


@pytest.mark.parametrize("view", [
    None, {}, {"id": "../etc", "variant": "blue-dark"}, {"id": "gates", "variant": "Blue Dark"}, {"id": "", "variant": ""},
])
def test_themed_shell_ignores_a_view_it_cannot_trust(view: Any) -> None:
    assert server_mod._themed_shell(SHELL, view) == SHELL


def test_themed_shell_replaces_the_attributes_it_sets_and_keeps_the_others() -> None:
    shell = '<html lang="en" class="x" data-theme="nostromo" data-variant="old" style="color:red"><head></head></html>'
    page = server_mod._themed_shell(shell, {"id": "gates", "variant": "teal-dark", "mode": "dark", "css_vars": {"--bg": "#000"}})
    assert page == ('<html lang="en" class="x" data-theme="gates" data-variant="teal-dark" '
                    'style="color-scheme:dark;--bg:#000"><head></head></html>')


def test_no_cors_headers_on_any_response(make_server: Any) -> None:
    server, _ = make_server()
    responses = [
        request(server, "GET", "/"),
        request(server, "GET", "/api/state"),
        request(server, "GET", "/api/state", token=False),
        request(server, "OPTIONS", "/api/state"),
        request(server, "GET", "/static/app.js"),
        request(server, "GET", "/nothing-here"),
    ]
    for _status, headers, _body in responses:
        assert not [h for h in headers if h.startswith("access-control-")]


def test_method_not_allowed_names_the_allowed_verb(make_server: Any) -> None:
    server, _ = make_server()
    status, headers, _ = request(server, "GET", "/api/settings")
    assert status == 405 and headers["allow"] == "POST"
    status, headers, _ = request(server, "POST", "/api/state", body=b"{}")
    assert status == 405 and headers["allow"] == "GET"
    status, headers, _ = request(server, "OPTIONS", "/")
    assert status == 405 and headers["allow"] == "GET"


# ── Static files ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path, ctype", [
    ("/static/app.js", "text/javascript"),
    ("/static/themes/nostromo.css", "text/css"),
    ("/static/fonts/vt323.ttf", "font/ttf"),
    ("/static/fonts/OFL.txt", "text/plain"),
])
def test_static_files_get_the_right_content_type(make_server: Any, path: str, ctype: str) -> None:
    server, _ = make_server()
    status, headers, body = request(server, "GET", path)
    assert status == 200
    assert headers["content-type"].startswith(ctype)
    assert body


def test_fonts_are_cached_but_webui_files_revalidate(make_server: Any) -> None:
    server, _ = make_server()
    assert request(server, "GET", "/static/fonts/vt323.ttf")[1]["cache-control"].startswith("public")
    assert request(server, "GET", "/static/app.js")[1]["cache-control"] == "no-cache"


def test_dev_mode_disables_static_caching(make_server: Any) -> None:
    server, _ = make_server(dev=True)
    assert request(server, "GET", "/static/app.js")[1]["cache-control"] == "no-store"


@pytest.mark.parametrize("path", [
    "/static/../secret.txt",
    "/static/..%2fsecret.txt",
    "/static/%2e%2e/secret.txt",
    "/static/%2e%2e%2fsecret.txt",
    "/static/themes/../../secret.txt",
    "/static/./app.js",
    "/static//app.js",
    "/static/app.js/",
    "/static/%2fetc%2fpasswd",
    "/static//etc/passwd",
    "/static/C:/Windows/win.ini",
    "/static/app.js:hidden",
    "/static/themes\\..\\..\\secret.txt",
    "/static/app.js%00.txt",
    "/static/%252e%252e/secret.txt",
    "/static/",
    "/static/themes",
])
def test_static_path_traversal_is_blocked(make_server: Any, path: str) -> None:
    server, _ = make_server()
    status, _headers, body = request(server, "GET", path, token=False)
    assert status == 404
    assert b"outside the webui" not in body


def test_static_post_is_not_allowed(make_server: Any) -> None:
    server, _ = make_server()
    assert request(server, "POST", "/static/app.js", body=b"{}")[0] == 405


# ── /api/state ─────────────────────────────────────────────────────────────────────

def test_state_payload_encodes_the_monitor_state(make_server: Any) -> None:
    server, _ = make_server()
    status, headers, body = request(server, "GET", "/api/state")
    assert status == 200
    payload = json.loads(body)
    state = payload["state"]
    assert state["status"] == "live"
    assert state["updated_at"] == "2026-10-21T16:12:00.000Z"
    assert state["usage"]["fetched_at"].endswith("Z")
    session = state["usage"]["limits"][0]
    assert session["kind"] == "session" and session["resets_at"].endswith("Z")
    assert session["fraction"] == pytest.approx(0.13)
    assert "raw" not in state["usage"]  # the raw API body never leaves the process
    month = state["windows"]["month"]
    assert month["totals"]["total_tokens"] == 2 + 8 + 96 + 2
    assert month["totals"]["cache_hit_rate"] == pytest.approx(0.96)
    assert payload["version"] == "2.0.0-test"
    assert payload["dev"] is False
    assert payload["model_names"]["claude-opus-5-5"] == "Opus 5.5"
    assert payload["settings"]["theme_id"] == "nostromo"
    assert headers["cache-control"] == "no-cache"


def test_state_etag_gives_304_until_the_state_changes(make_server: Any) -> None:
    server, rec = make_server()
    _, headers, _ = request(server, "GET", "/api/state")
    etag = headers["etag"]
    assert etag.startswith('"') and etag.endswith('"') and not etag.startswith('W/')
    status, headers304, body = request(server, "GET", "/api/state", headers={"If-None-Match": etag})
    assert status == 304 and body == b"" and headers304["etag"] == etag
    assert request(server, "GET", "/api/state", headers={"If-None-Match": '"other", ' + etag})[0] == 304
    assert request(server, "GET", "/api/state", headers={"If-None-Match": "*"})[0] == 304
    rec.state = sample_state(percent=40.0)
    status, headers_new, _ = request(server, "GET", "/api/state", headers={"If-None-Match": etag})
    assert status == 200 and headers_new["etag"] != etag


def test_state_is_null_before_the_first_fetch(make_server: Any) -> None:
    server, rec = make_server()
    rec.state = None
    payload = json.loads(request(server, "GET", "/api/state")[2])
    assert payload["state"] is None
    assert payload["model_names"] == {}


def test_state_reports_the_theme_and_update_info(make_server: Any, themes_module: types.ModuleType,
                                                 startup_fake: dict[str, Any]) -> None:
    server, _ = make_server(update_cb={"check": lambda: None, "info": lambda: {"version": "2.1.0"}})
    payload = json.loads(request(server, "GET", "/api/state")[2])
    assert payload["theme"]["id"] == "nostromo"
    assert payload["theme"]["variant"] == "amber"
    assert payload["theme"]["mode"] == "dark"
    assert payload["theme"]["css_vars"]["--bg"] == "#090909"
    assert payload["update"] == {"available": True, "info": {"version": "2.1.0"}, "skip_version": ""}
    assert payload["settings"]["launch_on_startup"] is False


def test_state_survives_a_broken_update_info_getter(make_server: Any, themes_module: types.ModuleType) -> None:
    def broken() -> None:
        raise RuntimeError("network down")

    server, _ = make_server(update_cb={"info": broken})
    status, _, body = request(server, "GET", "/api/state")
    assert status == 200
    assert json.loads(body)["update"]["available"] is False


def test_state_theme_is_null_when_the_loader_is_missing(make_server: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "usage_monitor.themes", None)  # import raises ImportError
    server, _ = make_server()
    status, _, body = request(server, "GET", "/api/state")
    assert status == 200 and json.loads(body)["theme"] is None


def test_theme_view_reports_which_optional_assets_ship(make_server: Any, themes_module: types.ModuleType) -> None:
    server, _ = make_server()
    theme = json.loads(request(server, "GET", "/api/state")[2])["theme"]
    assert theme["assets"] == {"css": True, "js": False}  # the fixture ships nostromo.css only
    themes = json.loads(request(server, "GET", "/api/themes")[2])["themes"]
    assert {t["id"]: t["assets"]["css"] for t in themes} == {"nostromo": True, "night_city": False, "gates": False}


def test_settings_view_has_defaults_for_unset_keys(make_server: Any, themes_module: types.ModuleType) -> None:
    server, _ = make_server()
    settings_view = json.loads(request(server, "GET", "/api/state")[2])["settings"]
    assert settings_view["poll_interval_ms"] == 900_000
    assert settings_view["show_scoped_rows"] == "auto"
    assert settings_view["billing_day"] is None
    assert settings_view["dashboard_bounds"] is None
    assert set(settings_view) == set(server_mod.SETTING_SPECS)


# ── /api/themes ─────────────────────────────────────────────────────────────────────

def test_themes_endpoint_lists_every_theme_with_variants_and_tokens(make_server: Any,
                                                                   themes_module: types.ModuleType) -> None:
    server, _ = make_server()
    status, _, body = request(server, "GET", "/api/themes")
    assert status == 200
    themes = json.loads(body)["themes"]
    assert [t["id"] for t in themes] == ["nostromo", "night_city", "gates"]
    nostromo, gates = themes[0], themes[2]
    assert nostromo["name"] == "Nostromo"
    assert nostromo["default_variant"] == "amber"
    assert nostromo["modes"] == []
    assert nostromo["variants"][0]["css_vars"]["--accent"] == "#ffb300"
    assert gates["modes"] == ["dark", "light", "system"]
    assert {v["accent"] for v in gates["variants"]} == {"system", "blue"}


def test_the_picker_shows_the_live_accent_of_the_system_variants(make_server: Any,
                                                              themes_module: types.ModuleType) -> None:
    server, _ = make_server()
    gates = json.loads(request(server, "GET", "/api/themes")[2])["themes"][2]
    by_id = {v["id"]: v for v in gates["variants"]}
    for variant_id in ("system-dark", "system-light"):
        assert by_id[variant_id]["colors"]["accent"] == LIVE_ACCENT
        assert by_id[variant_id]["css_vars"]["--accent"] == LIVE_ACCENT
    assert by_id["blue-dark"]["colors"]["accent"] == "#ffb300"             # a fixed accent stays as shipped
    nostromo = json.loads(request(server, "GET", "/api/themes")[2])["themes"][0]
    assert nostromo["variants"][0]["colors"]["accent"] == "#ffb300"


def test_themes_endpoint_is_503_without_the_loader(make_server: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "usage_monitor.themes", None)
    server, _ = make_server()
    assert request(server, "GET", "/api/themes")[0] == 503


# ── POST /api/settings: accepted and rejected values ───────────────────────────────

ACCEPTED: list[dict[str, Any]] = [
    {"show_floating": False},
    {"show_taskbar": True, "float_transparent_bg": True, "float_text_fx": False},
    {"taskbar_transparent_bg": True, "taskbar_text_fx": False},
    {"poll_interval_ms": 60_000},
    {"poll_interval_ms": 3_600_000},
    {"show_scoped_rows": "always"},
    {"show_scoped_rows": "never"},
    {"hide_floating_in_fullscreen": True},
    {"billing_day": 1},
    {"billing_day": 31},
    {"billing_day": None},
    {"plan_price_override": 0.01},
    {"plan_price_override": 10_000},
    {"plan_price_override": 99.5},
    {"plan_price_override": 100},
    {"plan_price_override": None},
    {"reduce_motion": True},
    {"check_updates": False},
    {"alerts_enabled": False},
    {"alert_thresholds": [50]},
    {"alert_thresholds": [80, 90, 100]},
    {"alert_thresholds": [1, 2, 3, 4, 5]},
    {"alert_thresholds": [100, 25]},
    {"alert_on_threshold": False, "alert_on_reset": False, "alert_on_credits": False},
    {"alert_quiet_hours": {"start": "22:00", "end": "07:00"}},
    {"alert_quiet_hours": {"start": "00:00", "end": "23:59"}},
    {"alert_quiet_hours": None},
    {"dashboard_bounds": {"x": -1200, "y": 40, "w": 1200, "h": 780}},
    {"dashboard_bounds": None},
    {"theme_id": "gates", "theme_variant": "blue-light", "theme_mode": "light"},
    {"theme_id": "gates", "theme_variant": "blue", "theme_mode": "system"},
    {"theme_id": "night_city", "theme_variant": "cyan"},
    {"theme_variant": "green"},
    {"launch_on_startup": True},
]

REJECTED: list[tuple[str, bytes]] = [
    ("unknown key", b'{"no_such_setting": 1}'),
    ("unknown key from the file", b'{"settings_version": 9}'),
    ("poll below minimum", b'{"poll_interval_ms": 59999}'),
    ("poll above maximum", b'{"poll_interval_ms": 3600001}'),
    ("poll as string", b'{"poll_interval_ms": "900000"}'),
    ("poll as bool", b'{"poll_interval_ms": true}'),
    ("poll as float", b'{"poll_interval_ms": 900000.5}'),
    ("bool as int", b'{"show_floating": 1}'),
    ("bool as string", b'{"reduce_motion": "yes"}'),
    ("scoped rows choice", b'{"show_scoped_rows": "sometimes"}'),
    ("billing day zero", b'{"billing_day": 0}'),
    ("billing day 32", b'{"billing_day": 32}'),
    ("billing day float", b'{"billing_day": 1.5}'),
    ("billing day bool", b'{"billing_day": true}'),
    ("billing day string", b'{"billing_day": "15"}'),
    ("price zero", b'{"plan_price_override": 0}'),
    ("price negative", b'{"plan_price_override": -1}'),
    ("price too high", b'{"plan_price_override": 10001}'),
    ("price NaN", b'{"plan_price_override": NaN}'),
    ("price infinity", b'{"plan_price_override": Infinity}'),
    ("price as string", b'{"plan_price_override": "100"}'),
    ("price bool", b'{"plan_price_override": true}'),
    ("the old notifications key is gone", b'{"notifications_enabled": false}'),
    ("alerts switch as int", b'{"alerts_enabled": 1}'),
    ("alerts switch as string", b'{"alerts_enabled": "yes"}'),
    ("alert kind as null", b'{"alert_on_reset": null}'),
    ("thresholds empty", b'{"alert_thresholds": []}'),
    ("thresholds six", b'{"alert_thresholds": [10, 20, 30, 40, 50, 60]}'),
    ("threshold zero", b'{"alert_thresholds": [0, 50]}'),
    ("threshold 101", b'{"alert_thresholds": [101]}'),
    ("threshold repeated", b'{"alert_thresholds": [80, 80]}'),
    ("threshold fractional", b'{"alert_thresholds": [80.5]}'),
    ("threshold as string", b'{"alert_thresholds": ["80"]}'),
    ("threshold as bool", b'{"alert_thresholds": [true]}'),
    ("threshold nested", b'{"alert_thresholds": [[80]]}'),
    ("thresholds not a list", b'{"alert_thresholds": 80}'),
    ("thresholds as text", b'{"alert_thresholds": "80,90"}'),
    ("thresholds null", b'{"alert_thresholds": null}'),
    ("quiet hours missing end", b'{"alert_quiet_hours": {"start": "22:00"}}'),
    ("quiet hours hour 25", b'{"alert_quiet_hours": {"start": "25:00", "end": "07:00"}}'),
    ("quiet hours short hour", b'{"alert_quiet_hours": {"start": "22:00", "end": "7:00"}}'),
    ("quiet hours seconds", b'{"alert_quiet_hours": {"start": "22:00:00", "end": "07:00"}}'),
    ("quiet hours extra key", b'{"alert_quiet_hours": {"start": "22:00", "end": "07:00", "tz": "x"}}'),
    ("quiet hours as text", b'{"alert_quiet_hours": "22:00-07:00"}'),
    ("quiet hours as list", b'{"alert_quiet_hours": ["22:00", "07:00"]}'),
    ("unknown theme id", b'{"theme_id": "unknown"}'),
    ("theme id with path", b'{"theme_id": "../evil"}'),
    ("mode not allowed", b'{"theme_mode": "neon"}'),
    ("variant from another theme", b'{"theme_id": "gates", "theme_variant": "amber"}'),
    ("variant unknown for nostromo", b'{"theme_variant": "red"}'),
    ("bounds too small", b'{"dashboard_bounds": {"x": 0, "y": 0, "w": 100, "h": 100}}'),
    ("bounds missing a key", b'{"dashboard_bounds": {"x": 0, "y": 0, "w": 1200}}'),
    ("bounds extra key", b'{"dashboard_bounds": {"x": 0, "y": 0, "w": 1200, "h": 780, "z": 1}}'),
    ("bounds as float", b'{"dashboard_bounds": {"x": 0.5, "y": 0, "w": 1200, "h": 780}}'),
    ("bounds off screen", b'{"dashboard_bounds": {"x": 90000, "y": 0, "w": 1200, "h": 780}}'),
    ("not an object", b'[1, 2]'),
    ("bare string", b'"hello"'),
    ("invalid json", b'{"show_floating": '),
    ("not utf-8", b'{"show_floating": \xff}'),
    ("empty body is null", b''),
]


@pytest.mark.parametrize("payload", ACCEPTED, ids=lambda p: ",".join(sorted(p)))
def test_settings_accepts_valid_values(make_server: Any, themes_module: types.ModuleType,
                                       startup_fake: dict[str, Any], payload: dict[str, Any]) -> None:
    server, rec = make_server()
    status, body = post_json(server, "/api/settings", payload)
    assert status == 200, body
    assert body["ok"] is True
    assert set(body["applied"]) == set(payload)
    assert rec.applied and set(rec.applied[-1]) == set(payload)


@pytest.mark.parametrize("label, raw", REJECTED, ids=[label for label, _ in REJECTED])
def test_settings_rejects_invalid_values_with_400(make_server: Any, themes_module: types.ModuleType,
                                                  startup_fake: dict[str, Any], label: str, raw: bytes) -> None:
    server, rec = make_server()
    status, _headers, body = request(server, "POST", "/api/settings", body=raw)
    assert status == 400, label
    assert "error" in json.loads(body)
    assert rec.applied == []


def test_a_bad_key_in_a_batch_applies_nothing(make_server: Any, settings: Settings,
                                              themes_module: types.ModuleType) -> None:
    server, rec = make_server()
    status, _ = post_json(server, "/api/settings", {"show_floating": False, "poll_interval_ms": 5})
    assert status == 400
    assert settings.get("show_floating") is True
    assert rec.applied == []


def test_settings_persist_to_the_settings_object(make_server: Any, settings: Settings,
                                                 themes_module: types.ModuleType) -> None:
    server, _ = make_server()
    post_json(server, "/api/settings", {"billing_day": 14, "dashboard_bounds": {"x": 5, "y": 6, "w": 1200, "h": 780}})
    assert settings.get("billing_day") == 14
    assert settings.get("dashboard_bounds") == {"x": 5, "y": 6, "w": 1200, "h": 780}


def test_empty_settings_object_is_a_no_op(make_server: Any) -> None:
    server, rec = make_server()
    status, body = post_json(server, "/api/settings", {})
    assert status == 200 and body["applied"] == {}
    assert rec.applied == []


def test_oversized_body_is_rejected_with_413(make_server: Any) -> None:
    server, _ = make_server()
    padding = "x" * (64 * 1024)
    status, headers, _ = request(server, "POST", "/api/settings",
                                 body=json.dumps({"skip_version": padding}).encode("utf-8"))
    assert status == 413
    assert headers.get("connection", "").lower() == "close"


def test_settings_listener_failure_does_not_fail_the_request(make_server: Any,
                                                             themes_module: types.ModuleType) -> None:
    def broken(applied: dict[str, Any]) -> None:
        raise RuntimeError("app hook broke")

    server, _ = make_server(on_settings_applied=broken)
    status, body = post_json(server, "/api/settings", {"reduce_motion": True})
    assert status == 200 and body["settings"]["reduce_motion"] is True


def test_launch_on_startup_delegates_to_the_run_entry(make_server: Any, startup_fake: dict[str, Any]) -> None:
    server, rec = make_server()
    status, body = post_json(server, "/api/settings", {"launch_on_startup": True})
    assert status == 200
    assert startup_fake["calls"] == [True]
    assert body["settings"]["launch_on_startup"] is True
    assert rec.applied[-1] == {"launch_on_startup": True}


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The real startup logic over a recording stand-in for the Run entry: any registry access shows up."""
    log: dict[str, Any] = {"value": '"C:/installed/ClaudeUsageMonitor.exe"', "reads": 0, "writes": [], "deletes": 0}

    def read() -> str | None:
        log["reads"] += 1
        return log["value"]

    def write(command: str) -> None:
        log["writes"].append(command)
        log["value"] = command

    def delete() -> None:
        log["deletes"] += 1
        log["value"] = None

    monkeypatch.setattr(startup, "is_enabled", REAL_IS_ENABLED)
    monkeypatch.setattr(startup, "set_enabled", REAL_SET_ENABLED)
    monkeypatch.setattr(startup, "_read_value", read)
    monkeypatch.setattr(startup, "_write_value", write)
    monkeypatch.setattr(startup, "_delete_value", delete)
    return log


def test_a_dev_server_never_touches_the_run_entry(make_server: Any, registry: dict[str, Any]) -> None:
    """A dev instance shares the entry's name with the installed copy: switching it off used to delete
    the installed copy's sign-in entry, and switching it on repointed the entry at the source tree."""
    server, rec = make_server(dev=True)
    state = json.loads(request(server, "GET", "/api/state")[2])
    assert state["settings"]["launch_on_startup"] is False            # not the installed copy's entry

    status, body = post_json(server, "/api/settings", {"launch_on_startup": True})
    assert status == 200 and body["settings"]["launch_on_startup"] is True
    assert json.loads(request(server, "GET", "/api/state")[2])["settings"]["launch_on_startup"] is True
    status, body = post_json(server, "/api/settings", {"launch_on_startup": False})
    assert status == 200 and body["settings"]["launch_on_startup"] is False
    assert rec.applied == [{"launch_on_startup": True}, {"launch_on_startup": False}]

    assert registry["reads"] == 0 and registry["writes"] == [] and registry["deletes"] == 0
    assert registry["value"] == '"C:/installed/ClaudeUsageMonitor.exe"'


def test_a_dev_server_still_applies_the_other_settings_beside_the_switch(
    make_server: Any, settings: Settings, registry: dict[str, Any],
) -> None:
    server, _ = make_server(dev=True)
    status, body = post_json(server, "/api/settings", {"launch_on_startup": True, "reduce_motion": True})
    assert status == 200 and set(body["applied"]) == {"launch_on_startup", "reduce_motion"}
    assert settings.get("reduce_motion") is True
    assert settings.get("launch_on_startup", None) is None            # the switch is never written to the file
    assert registry["writes"] == [] and registry["reads"] == 0


def test_a_normal_server_does_write_the_run_entry(make_server: Any, registry: dict[str, Any]) -> None:
    """The control for the dev test above: the same requests do reach the entry when dev is off."""
    server, _ = make_server(dev=False)
    assert json.loads(request(server, "GET", "/api/state")[2])["settings"]["launch_on_startup"] is True
    assert post_json(server, "/api/settings", {"launch_on_startup": True})[0] == 200
    assert registry["writes"] == [startup.command_line()]
    assert post_json(server, "/api/settings", {"launch_on_startup": False})[0] == 200
    assert registry["deletes"] == 1 and registry["value"] is None


def test_a_theme_switch_without_a_variant_stores_the_new_themes_default(
    make_server: Any, settings: Settings, themes_module: types.ModuleType,
) -> None:
    """The stored variant belongs to the old theme, so the id alone used to be a 400. The dashboard sends the
    new theme's default with the id; an API caller that sends only the id gets the same."""
    server, rec = make_server()
    status, body = post_json(server, "/api/settings", {"theme_id": "night_city"})
    assert status == 200, body
    assert body["applied"] == {"theme_id": "night_city", "theme_variant": "cyan"}
    assert (settings.get("theme_id"), settings.get("theme_variant")) == ("night_city", "cyan")
    assert rec.applied[-1] == {"theme_id": "night_city", "theme_variant": "cyan"}

    status, body = post_json(server, "/api/settings", {"theme_id": "gates"})
    assert status == 200 and body["applied"] == {"theme_id": "gates", "theme_variant": "system"}   # the bare accent

    status, body = post_json(server, "/api/settings", {"theme_id": "nostromo"})
    assert status == 200 and body["applied"] == {"theme_id": "nostromo", "theme_variant": "amber"}


def test_posting_the_current_theme_again_keeps_its_variant(make_server: Any, settings: Settings,
                                                          themes_module: types.ModuleType) -> None:
    settings.update({"theme_id": "nostromo", "theme_variant": "green"})
    server, _ = make_server()
    status, body = post_json(server, "/api/settings", {"theme_id": "nostromo"})
    assert status == 200 and body["applied"] == {"theme_id": "nostromo"}
    assert settings.get("theme_variant") == "green"


@pytest.mark.parametrize("sent, stored", [
    ({"theme_id": "gates", "theme_variant": "blue-dark"}, {"theme_variant": "blue", "theme_mode": "dark"}),
    ({"theme_id": "gates", "theme_variant": "blue-light"}, {"theme_variant": "blue", "theme_mode": "light"}),
    ({"theme_id": "gates", "theme_variant": "system-light"}, {"theme_variant": "system", "theme_mode": "light"}),
    ({"theme_id": "gates", "theme_variant": "blue-dark", "theme_mode": "light"},
     {"theme_variant": "blue", "theme_mode": "light"}),                       # a mode sent with it wins over the id's
    ({"theme_id": "gates", "theme_variant": "blue"}, {"theme_variant": "blue"}),   # a bare accent invents no mode
    ({"theme_id": "gates", "theme_variant": "system"}, {"theme_variant": "system"}),
])
def test_a_whole_gates_variant_id_is_stored_as_accent_and_mode(
    make_server: Any, settings: Settings, themes_module: types.ModuleType,
    sent: dict[str, Any], stored: dict[str, Any],
) -> None:
    """resolve() reads only bare accents and takes the mode from theme_mode, so "blue-light" was accepted and
    then silently ignored (the theme fell back to the system accent)."""
    server, _ = make_server()
    status, body = post_json(server, "/api/settings", sent)
    assert status == 200, body
    for key, value in stored.items():
        assert settings.get(key) == value, key
    assert body["applied"]["theme_variant"] == stored["theme_variant"]
    assert ("theme_mode" in body["applied"]) == ("theme_mode" in stored)


def test_a_whole_variant_id_alone_is_checked_against_the_stored_theme(
    make_server: Any, settings: Settings, themes_module: types.ModuleType,
) -> None:
    settings.update({"theme_id": "gates", "theme_variant": "system"})
    server, _ = make_server()
    assert post_json(server, "/api/settings", {"theme_variant": "teal-dark"})[0] == 400        # not in the fake's Gates
    status, body = post_json(server, "/api/settings", {"theme_variant": "blue-light"})
    assert status == 200 and body["applied"] == {"theme_variant": "blue", "theme_mode": "light"}


def test_launch_on_startup_failure_is_a_500_and_changes_nothing(make_server: Any, settings: Settings,
                                                                startup_fake: dict[str, Any]) -> None:
    startup_fake["fail"] = True
    server, rec = make_server()
    status, body = post_json(server, "/api/settings", {"launch_on_startup": True, "reduce_motion": True})
    assert status == 500
    assert settings.get("reduce_motion", False) is False
    assert rec.applied == []


def test_theme_check_needs_the_loader(make_server: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "usage_monitor.themes", None)
    server, _ = make_server()
    status, body = post_json(server, "/api/settings", {"theme_id": "gates"})
    assert status == 503 and body["error"]


# ── Other POST endpoints ────────────────────────────────────────────────────────────

def test_refresh_calls_the_callback(make_server: Any) -> None:
    server, rec = make_server()
    assert request(server, "POST", "/api/refresh")[0] == 200
    assert rec.calls == ["refresh"]
    assert request(server, "POST", "/api/refresh", token=False)[0] == 401


def test_refresh_without_a_callback_is_501(make_server: Any) -> None:
    server, _ = make_server(refresh_cb=None)
    assert request(server, "POST", "/api/refresh")[0] == 501


@pytest.mark.parametrize("operation", ["check", "apply", "skip"])
def test_update_operations_call_their_callbacks(make_server: Any, operation: str) -> None:
    server, rec = make_server()
    status, _, body = request(server, "POST", f"/api/update/{operation}")
    assert status == 200 and json.loads(body)["ok"] is True
    assert rec.calls == [operation]


def test_update_unknown_operation_is_404_and_wrong_verb_is_405(make_server: Any) -> None:
    server, _ = make_server()
    assert request(server, "POST", "/api/update/install")[0] == 404
    status, headers, _ = request(server, "GET", "/api/update/check")
    assert status == 405 and headers["allow"] == "POST"


def test_update_callback_failure_is_a_500_without_details(make_server: Any) -> None:
    def boom() -> None:
        raise RuntimeError("secret path C:/private")

    server, _ = make_server(update_cb={"check": boom})
    status, _, body = request(server, "POST", "/api/update/check")
    assert status == 500
    assert b"private" not in body


def test_bad_json_error_does_not_echo_the_body(make_server: Any) -> None:
    server, _ = make_server()
    status, _, body = request(server, "POST", "/api/settings", body=b'{"secret_token": "abc123"')
    assert status == 400
    assert b"abc123" not in body


def test_real_theme_loader_serves_the_shipped_themes(make_server: Any,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """Runs against the shipped loader and JSON files, not the fake."""
    monkeypatch.delitem(sys.modules, "usage_monitor.themes", raising=False)
    importlib.import_module("usage_monitor.themes")      # a broken loader must fail here, not skip
    server, _ = make_server()
    themes = json.loads(request(server, "GET", "/api/themes")[2])["themes"]
    assert [t["id"] for t in themes][:3] == ["nostromo", "night_city", "gates"]
    nostromo, gates = themes[0], themes[2]
    assert nostromo["variants"][0]["css_vars"]["--bg"]
    assert gates["modes"] == ["dark", "light", "system"]
    state = json.loads(request(server, "GET", "/api/state")[2])
    assert state["theme"]["id"] == "nostromo"
    assert state["theme"]["css_vars"]["--bg"] == "#090909"
    status, _ = post_json(server, "/api/settings", {"theme_id": "gates", "theme_variant": "blue", "theme_mode": "light"})
    assert status == 200
    state = json.loads(request(server, "GET", "/api/state")[2])
    assert state["theme"]["id"] == "gates"
    assert state["theme"]["variant"] == "blue-light"
    assert state["theme"]["mode"] == "light"


def _applied_theme(server: ApiServer) -> tuple[str, str]:
    theme = json.loads(request(server, "GET", "/api/state")[2])["theme"]
    return theme["id"], theme["variant"]


def test_partial_and_whole_variant_posts_resolve_to_what_was_asked_for(make_server: Any) -> None:
    """The same requests against the shipped loader and files, not the fake."""
    importlib.import_module("usage_monitor.themes")
    server, _ = make_server()
    assert post_json(server, "/api/settings", {"theme_id": "night_city"})[0] == 200
    assert _applied_theme(server) == ("night_city", "blackwall")
    assert post_json(server, "/api/settings", {"theme_id": "nostromo"})[0] == 200
    assert _applied_theme(server) == ("nostromo", "amber")
    assert post_json(server, "/api/settings", {"theme_id": "gates", "theme_variant": "blue-dark"})[0] == 200
    assert _applied_theme(server) == ("gates", "blue-dark")
    assert post_json(server, "/api/settings", {"theme_variant": "blue-light"})[0] == 200
    assert _applied_theme(server) == ("gates", "blue-light")
    assert post_json(server, "/api/settings", {"theme_id": "gates", "theme_variant": "teal-dark",
                                              "theme_mode": "system"})[0] == 200
    assert _applied_theme(server)[1] in ("teal-dark", "teal-light")        # the mode follows the machine


def test_the_picker_and_the_applied_theme_agree_on_the_system_accent(make_server: Any,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """The Gates "system" swatch showed the JSON default (#60cdff) while choosing it applied the user's
    Windows accent."""
    real = importlib.import_module("usage_monitor.themes")
    monkeypatch.setattr(real, "_platform_accent", lambda: "#d0000c")
    monkeypatch.setattr(real, "_platform_light", lambda: False)
    server, _ = make_server()
    gates = [t for t in json.loads(request(server, "GET", "/api/themes")[2])["themes"] if t["id"] == "gates"][0]
    picker = {v["id"]: v for v in gates["variants"]}
    # #d0000c is lifted to read on a dark window (#ff5145); it is close to the critical red, so normal fills
    # keep the Gates default accent (--accent-fill) while the chrome keeps the system colour (--accent)
    assert picker["system-dark"]["colors"]["accent"] == "#ff5145"
    assert picker["system-light"]["css_vars"]["--accent"] == "#d0000c"
    assert picker["system-dark"]["css_vars"]["--accent-fill"] == "#60cdff"
    assert picker["system-light"]["css_vars"]["--accent-fill"] == "#0067c0"
    assert picker["blue-dark"]["colors"]["accent"] not in ("#d0000c", "#ff5145")
    assert post_json(server, "/api/settings", {"theme_id": "gates", "theme_variant": "system", "theme_mode": "dark"})[0] == 200
    applied = json.loads(request(server, "GET", "/api/state")[2])["theme"]
    for token in ("--accent", "--accent-fill", "--accent-alt", "--accent-soft", "--glow"):
        assert applied["css_vars"][token] == picker["system-dark"]["css_vars"][token], token
    assert applied["css_vars"]["--accent"] == "#ff5145"


# ── Clients that hang up ─────────────────────────────────────────────────────────────

class HangUpRequest:
    """A request whose client is gone: the headers read fine, and every write fails like a closed socket."""

    def __init__(self, error: BaseException, headers: dict[str, str], path: str = "/api/state") -> None:
        self.path = path
        self.headers = headers
        self.error = error
        self.close_connection = False
        self.wfile = self

    def send_response(self, *_args: Any) -> None:
        pass

    def send_header(self, *_args: Any) -> None:
        pass

    def end_headers(self) -> None:
        self.write(b"")

    def write(self, _data: bytes) -> None:
        raise self.error

    def flush(self) -> None:
        pass


def _hang_up_headers(server: ApiServer, *, host_ok: bool = True) -> dict[str, str]:
    return {"Host": f"127.0.0.1:{server.port}" if host_ok else "evil.example:1", server_mod.TOKEN_HEADER: server.token}


@pytest.mark.parametrize("host_ok", [True, False], ids=["answer", "error reply"])
@pytest.mark.parametrize("error", [BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError])
def test_a_client_that_hangs_up_is_a_debug_line_and_not_an_error(
        make_server: Any, caplog: pytest.LogCaptureFixture, error: type[OSError], host_ok: bool) -> None:
    """On Windows a cancelled fetch is a ConnectionAbortedError (WinError 10053), a sibling of the reset and
    broken pipe the handler already knew. It was logged as an ERROR traceback and its 500 reply, written to the
    dead socket, raised out of the handler. The same goes for a 4xx reply written to a client that has gone."""
    server, _ = make_server()
    req = HangUpRequest(error("WinError 10053"), _hang_up_headers(server, host_ok=host_ok))
    with caplog.at_level(logging.DEBUG, logger="usage_monitor.ui.server"):
        server._handle(req, "GET")                         # type: ignore[arg-type]  # must not raise
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("closed the connection" in r.getMessage() for r in caplog.records)


def test_a_failing_handler_whose_client_is_also_gone_is_still_one_logged_error(
        make_server: Any, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    server, _ = make_server()

    def boom(_req: Any) -> None:
        raise RuntimeError("a real bug")

    monkeypatch.setattr(server, "_get_state", boom)
    req = HangUpRequest(ConnectionAbortedError("gone"), _hang_up_headers(server))
    with caplog.at_level(logging.DEBUG, logger="usage_monitor.ui.server"):
        server._handle(req, "GET")                         # type: ignore[arg-type]  # the 500 cannot be sent: no raise
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and "API request failed" in errors[0].getMessage()


def test_socketserver_errors_from_a_hung_up_client_are_quiet_and_real_ones_are_logged(
        make_server: Any, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]) -> None:
    """The reset of a connection that is read before ApiServer._handle runs reaches the server's handle_error,
    whose default prints a traceback on stderr every time."""
    server, _ = make_server()
    httpd = server._httpd
    with caplog.at_level(logging.DEBUG, logger="usage_monitor.ui.server"):
        for error in (ConnectionResetError("reset"), ConnectionAbortedError("aborted"), TimeoutError("slow")):
            try:
                raise error
            except OSError:
                httpd.handle_error(None, ("127.0.0.1", 1))
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
        try:
            raise ValueError("a bug in a handler")
        except ValueError:
            httpd.handle_error(None, ("127.0.0.1", 1))
    assert [r.levelno for r in caplog.records if r.levelno >= logging.ERROR] == [logging.ERROR]
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_clients_that_reset_the_connection_do_not_fill_the_log_or_stderr(
        make_server: Any, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]) -> None:
    server, _ = make_server()
    linger = struct.pack("ii", 1, 0)                       # close sends a RST, not a FIN
    with caplog.at_level(logging.DEBUG, logger="usage_monitor.ui.server"):
        for _ in range(25):
            conn = socket.create_connection(("127.0.0.1", server.port), timeout=5)
            conn.sendall(f"GET /api/state HTTP/1.1\r\nHost: 127.0.0.1:{server.port}\r\n"
                         f"{server_mod.TOKEN_HEADER}: {server.token}\r\n\r\n".encode("ascii"))
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, linger)
            conn.close()
        assert request(server, "GET", "/api/state")[0] == 200          # the server is still serving
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert capsys.readouterr().err == ""


# ── The statistics database on a network share ───────────────────────────────────────

def test_the_read_only_uri_of_a_local_path_is_the_file_uri_with_mode_ro(tmp_path: Path) -> None:
    uri = server_mod._read_only_uri(tmp_path / "a b" / "usage.db")
    assert uri.startswith("file:///") and uri.endswith("/a%20b/usage.db?mode=ro")


@pytest.mark.skipif(sys.platform != "win32", reason="UNC paths are Windows paths")
def test_the_read_only_uri_of_a_unc_path_has_an_empty_authority_and_four_slashes() -> None:
    """SQLite refuses ``file://server/share/...`` ("invalid uri authority") and wants ``file:////server/...``."""
    uri = server_mod._read_only_uri(Path(r"\\fileserver\home\Ada Lovelace\ClaudeUsageMonitor\usage.db"))
    assert uri == "file:////fileserver/home/Ada%20Lovelace/ClaudeUsageMonitor/usage.db?mode=ro"


def _admin_share_path(path: Path) -> Path | None:
    """The administrative-share form of a file on a local drive (the localhost share named for the drive letter
    with a dollar sign), or None when this account cannot open it."""
    drive, rest = os.path.splitdrive(str(path))
    if sys.platform != "win32" or len(drive) != 2:
        return None
    unc = Path(rf"\\localhost\{drive[0]}${rest}")
    try:
        return unc if unc.is_file() else None
    except OSError:
        return None


def test_the_schema_of_a_database_on_a_unc_path_can_be_read(tmp_path: Path) -> None:
    """The statistics were unavailable for good when the app data folder was on a share: the route answered
    503 on every request while the data service, which opens the file by its plain path, worked."""
    db = tmp_path / "share" / "usage.db"
    seed_usage_db(db)
    unc = _admin_share_path(db)
    if unc is None:
        pytest.skip("the administrative share of this drive cannot be opened by this account")
    assert server_mod._schema_version(unc) == server_mod.SCHEMA_VERSION
    with server_mod.open_statistics_store(unc) as store:
        assert store.query("SELECT COUNT(*) FROM requests")[0][0] == 4


def test_the_probe_never_creates_a_missing_database(tmp_path: Path) -> None:
    missing = tmp_path / "gone" / "usage.db"
    with pytest.raises(sqlite3.OperationalError):
        server_mod.open_statistics_store(missing)
    assert not missing.exists() and not missing.parent.exists()
    (tmp_path / "gone").mkdir()
    with pytest.raises(sqlite3.OperationalError):
        server_mod._schema_version(missing)                 # even a racing probe must not leave a file behind
    assert not missing.exists()


# ── Supporters proxy ─────────────────────────────────────────────────────────────────

class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class CountingOpener:
    def __init__(self, result: bytes | Exception) -> None:
        self.result = result
        self.calls = 0

    def __call__(self, url: str, timeout: float) -> bytes:
        self.calls += 1
        assert timeout == server_mod.SUPPORTERS_TIMEOUT_S
        assert url == server_mod.SUPPORTERS_URL
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_supporters_are_fetched_once_for_an_hour(make_server: Any) -> None:
    clock = FakeClock()
    opener = CountingOpener(b'{"supporters": ["Ada", {"name": "Grace"}, {"display_name": "Linus"}, 7, ""]}')
    server, _ = make_server(supporters_opener=opener, clock=clock)
    first = json.loads(request(server, "GET", "/api/supporters")[2])
    assert first == {"supporters": ["Ada", "Grace", "Linus"], "tiers": [{"tier": "", "names": ["Ada", "Grace", "Linus"]}], "error": None, "cached": False}
    second = json.loads(request(server, "GET", "/api/supporters")[2])
    assert second["cached"] is True and opener.calls == 1
    clock.now += 3601
    json.loads(request(server, "GET", "/api/supporters")[2])
    assert opener.calls == 2


def test_supporters_top_level_list_is_accepted(make_server: Any) -> None:
    server, _ = make_server(supporters_opener=CountingOpener(b'["Ada"]'), clock=FakeClock())
    assert json.loads(request(server, "GET", "/api/supporters")[2])["supporters"] == ["Ada"]


def test_supporters_file_grouped_by_tier_keeps_its_tier_names(make_server: Any) -> None:
    body = json.dumps({"tiers": [{"tier": "Mission Control", "members": ["MrShotgun"]},
                                 {"tier": "Launch Crew", "members": ["Beersatron", {"name": "Kid"}]},
                                 {"tier": "Empty", "members": []}, "not a tier"]}).encode("utf-8")
    server, _ = make_server(supporters_opener=CountingOpener(body), clock=FakeClock())
    payload = json.loads(request(server, "GET", "/api/supporters")[2])
    assert payload["supporters"] == ["MrShotgun", "Beersatron", "Kid"]
    assert payload["tiers"] == [{"tier": "Mission Control", "names": ["MrShotgun"]},
                                {"tier": "Launch Crew", "names": ["Beersatron", "Kid"]}]
    assert payload["error"] is None


@pytest.mark.parametrize("failure, expect", [
    (TimeoutError("timed out"), "TimeoutError"),
    (OSError("no route to host"), "OSError"),
    (b"not json", "JSONDecodeError"),
    (b'{"unexpected": {"shape": true}}', "ValueError"),
])
def test_supporters_failure_returns_an_empty_list_and_the_reason(make_server: Any, failure: Any,
                                                                 expect: str) -> None:
    server, _ = make_server(supporters_opener=CountingOpener(failure), clock=FakeClock())
    status, _, body = request(server, "GET", "/api/supporters")
    assert status == 200
    payload = json.loads(body)
    assert payload["supporters"] == []
    assert payload["error"].startswith(expect)


def test_supporters_failure_is_retried_after_five_minutes(make_server: Any) -> None:
    clock = FakeClock()
    opener = CountingOpener(TimeoutError("slow"))
    server, _ = make_server(supporters_opener=opener, clock=clock)
    request(server, "GET", "/api/supporters")
    request(server, "GET", "/api/supporters")
    assert opener.calls == 1
    clock.now += 301
    request(server, "GET", "/api/supporters")
    assert opener.calls == 2


# ── Statistics routes: /api/analytics/<name> and /api/export ─────────────────────────

PRICING = Pricing.load()
MILLION = 1_000_000
REPORT_NAMES = ["cost", "efficiency", "pacing", "history", "sessions"]
REPORT_KEYS: dict[str, set[str]] = {
    "cost": {"basis", "windows", "plan", "value", "monthly", "breakdowns", "top_sessions_week", "spend"},
    "efficiency": {"start", "end", "basis", "cache", "thinking", "context", "compactions", "active_days", "tools",
                   "productivity", "effort", "api_errors", "web"},
    "pacing": {"now", "session", "weekly", "scoped"},
    "history": {"tz", "days", "hour_weekday", "streaks", "busiest_day", "first_use", "active_days", "odometer"},
    "sessions": {"start", "end", "basis", "sessions"},
}


def seed_usage_db(path: Path) -> None:
    """A small history around NOW: an hour ago, yesterday, twelve days ago and forty days ago."""
    def request_row(key: str, when: datetime, *, model: str = "claude-opus-5-5", inp: int = 0, out: int = 0,
                    session: str = "s1", project: str = "alpha", cwd: str = "C:/work/alpha") -> m.RequestRecord:
        return m.RequestRecord(
            key=key, message_id="msg_" + key, request_id="req_" + key, ts=when, session_id=session,
            project=project, cwd=cwd, git_branch="main", is_subagent=False, agent_id=None, model=model,
            input_tokens=inp, output_tokens=out)

    with Store(path) as store:
        store.add_items([
            request_row("a", NOW - timedelta(hours=1), inp=MILLION),
            request_row("b", NOW - timedelta(days=1, hours=2), out=MILLION, model="claude-sonnet-5-5",
                        session="s2", project="beta", cwd="D:/src/beta"),
            request_row("c", NOW - timedelta(days=12), inp=2 * MILLION, session="s3"),
            request_row("d", NOW - timedelta(days=40), inp=MILLION, session="s4"),
            m.ToolCall("t1", "Bash", None, "s1", NOW - timedelta(hours=1), False, "commit"),
            m.ToolCall("t2", "mcp__github__create_issue", None, "s2", NOW - timedelta(days=1), False),
            m.SessionMeta("s1", "alpha", ai_title="First session", custom_title=None),
        ], PRICING)


class StatsRig(types.SimpleNamespace):
    """A server on a seeded usage database. ``opened`` lists every Store the server asked for."""

    server: ApiServer
    rec: Recorder
    clock: "FakeClock"
    opened: list[Store]
    db: Path

    def get(self, path: str) -> tuple[int, dict[str, str], bytes]:
        return request(self.server, "GET", path)

    def json(self, path: str) -> Any:
        status, _headers, body = self.get(path)
        assert status == 200, body
        return json.loads(body)


@pytest.fixture
def stats(make_server: Any, tmp_path: Path) -> StatsRig:
    db = tmp_path / "stats" / "usage.db"
    seed_usage_db(db)
    clock = FakeClock()
    opened: list[Store] = []

    def open_store() -> Store:
        opened.append(Store(db))
        return opened[-1]

    server, rec = make_server(analytics_store=open_store, wall_clock=lambda: NOW, clock=clock)
    return StatsRig(server=server, rec=rec, clock=clock, opened=opened, db=db)


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[dict[str, Any]]]:
    """Records the keyword arguments (and ``_args``) of every analytics call, then calls the real function."""
    calls: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for name in ("cost_report", "efficiency_report", "pacing_report", "history_report", "sessions_report",
                 "resolve_window", "daily_rollups"):
        real = getattr(analytics, name)

        def wrapper(*args: Any, _real: Any = real, _name: str = name, **kwargs: Any) -> Any:
            calls[_name].append(dict(kwargs, _args=args))
            return _real(*args, **kwargs)

        monkeypatch.setattr(analytics, name, wrapper)
    return calls


@pytest.mark.parametrize("name", REPORT_NAMES)
def test_each_report_is_served_as_json(stats: StatsRig, name: str) -> None:
    status, headers, body = stats.get(f"/api/analytics/{name}")
    assert status == 200
    assert headers["content-type"] == "application/json; charset=utf-8"
    assert headers["cache-control"] == "no-store"
    assert set(json.loads(body)) == REPORT_KEYS[name]


def test_the_reports_are_the_analytics_functions_run_on_the_state_and_the_settings(stats: StatsRig) -> None:
    state = stats.rec.state
    assert state is not None
    with Store(stats.db) as store:
        expected = {
            "cost": analytics.cost_report(store, PRICING, now=NOW, usage=state.usage, plan=state.plan,
                                          plan_price=None, billing_day=None, breakdown_window="cycle"),
            "pacing": analytics.pacing_report(store, now=NOW, usage=state.usage),
            "history": analytics.history_report(store, now=NOW, days=371),
        }
        start, end = analytics.resolve_window("month", store, now=NOW, usage=state.usage, billing_day=None)
        expected["efficiency"] = analytics.efficiency_report(store, start=start, end=end)
        start, end = analytics.resolve_window("week", store, now=NOW, usage=state.usage, billing_day=None)
        expected["sessions"] = analytics.sessions_report(store, PRICING, start=start, end=end, limit=25)
    for name, report in expected.items():
        assert stats.json(f"/api/analytics/{name}") == json.loads(json.dumps(report)), name


def test_the_seeded_history_shows_up_in_the_numbers(stats: StatsRig) -> None:
    cost = stats.json("/api/analytics/cost")
    assert cost["windows"]["all"]["requests"] == 4
    assert cost["windows"]["session"]["requests"] == 1                       # the API window ends 3 h 27 m from now
    assert cost["plan"] == {"name": "Max 5x", "subscription_type": "max", "rate_limit_tier": "default_claude_max_5x",
                            "price_usd": 100.0, "price_source": "detected"}
    assert {row["name"] for row in cost["breakdowns"]["by_model"]} == {"Opus 5.5", "Sonnet 5.5"}
    sessions = stats.json("/api/analytics/sessions?window=all")["sessions"]
    assert {s["session_id"]: s["title"] for s in sessions}["s1"] == "First session"
    assert stats.json("/api/analytics/efficiency?window=all")["tools"]["total"] == 2


def test_unknown_reports_are_404_and_only_get_is_allowed(stats: StatsRig) -> None:
    for path in ("/api/analytics/nope", "/api/analytics/", "/api/analytics/cost/", "/api/analytics/bad%20name",
                 "/api/analytics/Cost", "/api/analytics/cost%2f", "/api/analytics/../state"):
        status, _headers, body = stats.get(path)
        assert status == 404, path
        assert "error" in json.loads(body)
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        status, headers, _ = request(stats.server, method, "/api/analytics/cost", body=b"{}")
        assert status == 405 and headers["allow"] == "GET", method
    status, headers, _ = request(stats.server, "POST", "/api/export?format=csv", body=b"{}")
    assert status == 405 and headers["allow"] == "GET"
    assert stats.opened == []                                                # none of that touched the database


@pytest.mark.parametrize("path", [
    "/api/analytics/cost", "/api/analytics/efficiency?window=all", "/api/analytics/pacing",
    "/api/analytics/history?days=7", "/api/analytics/sessions?limit=3", "/api/export?format=csv",
    "/api/export?format=json",
])
def test_the_statistics_routes_keep_the_host_and_token_guards(stats: StatsRig, path: str) -> None:
    server = stats.server
    assert request(server, "GET", path, token=False)[0] == 401
    assert request(server, "GET", path, token=False, headers={TOKEN_HEADER: "wrong"})[0] == 401
    assert request(server, "GET", path, host="evil.example")[0] == 403
    assert request(server, "GET", path, host=f"127.0.0.1:{server.port + 1}")[0] == 403
    assert request(server, "GET", path, host=f"127.0.0.1.nip.io:{server.port}")[0] == 403
    assert stats.opened == [], "a refused request must not reach the database"
    assert request(server, "GET", path, host=f"localhost:{server.port}")[0] == 200
    assert len(stats.opened) == 1


@pytest.mark.parametrize("path, function, expected", [
    ("/api/analytics/cost", "cost_report", {"breakdown_window": "cycle"}),
    ("/api/analytics/cost?window=all", "cost_report", {"breakdown_window": "all"}),
    ("/api/analytics/cost?window=session", "cost_report", {"breakdown_window": "session"}),
    ("/api/analytics/history", "history_report", {"days": 371}),
    ("/api/analytics/history?days=7", "history_report", {"days": 7}),
    ("/api/analytics/history?days=800", "history_report", {"days": 800}),
    ("/api/analytics/sessions", "sessions_report", {"limit": 25}),
    ("/api/analytics/sessions?limit=1", "sessions_report", {"limit": 1}),
    ("/api/analytics/sessions?window=all&limit=200", "sessions_report", {"limit": 200}),
])
def test_query_parameters_reach_the_report(stats: StatsRig, spy: dict[str, list[dict[str, Any]]], path: str,
                                           function: str, expected: dict[str, Any]) -> None:
    assert stats.get(path)[0] == 200
    assert {key: spy[function][-1][key] for key in expected} == expected


@pytest.mark.parametrize("path, window", [
    ("/api/analytics/efficiency", "month"),
    ("/api/analytics/efficiency?window=all", "all"),
    ("/api/analytics/efficiency?window=session", "session"),
    ("/api/analytics/efficiency?window=cycle", "cycle"),
    ("/api/analytics/sessions", "week"),
    ("/api/analytics/sessions?window=today", "today"),
    ("/api/analytics/sessions?window=month", "month"),
])
def test_the_window_parameter_picks_the_span(stats: StatsRig, spy: dict[str, list[dict[str, Any]]], path: str,
                                             window: str) -> None:
    assert stats.get(path)[0] == 200
    assert spy["resolve_window"][-1]["_args"][0] == window


@pytest.mark.parametrize("path", [
    "/api/analytics/cost?window=year",
    "/api/analytics/cost?window=",
    "/api/analytics/cost?window",
    "/api/analytics/cost?window=WEEK",
    "/api/analytics/cost?window=week&window=month",
    "/api/analytics/cost?windw=week",
    "/api/analytics/cost?days=7",
    "/api/analytics/cost?window=%FF",
    "/api/analytics/efficiency?window=weekly",
    "/api/analytics/pacing?window=week",
    "/api/analytics/pacing?x=1",
    "/api/analytics/history?days=6",
    "/api/analytics/history?days=801",
    "/api/analytics/history?days=abc",
    "/api/analytics/history?days=",
    "/api/analytics/history?days=-7",
    "/api/analytics/history?days=7.5",
    "/api/analytics/history?days=+7",
    "/api/analytics/history?days=1e3",
    "/api/analytics/history?days=1_0",
    "/api/analytics/history?days=%D9%A7%D9%A7",
    "/api/analytics/history?days=0000007",
    "/api/analytics/history?window=week",
    "/api/analytics/sessions?limit=0",
    "/api/analytics/sessions?limit=201",
    "/api/analytics/sessions?limit=ten",
    "/api/analytics/sessions?window=x",
    "/api/analytics/sessions?limit=5&limit=6",
    "/api/analytics/sessions?" + "&".join(f"p{n}=1" for n in range(12)),
    "/api/export?format=xml",
    "/api/export?format=CSV",
    "/api/export?format=",
    "/api/export",
    "/api/export?format=csv&format=json",
    "/api/export?format=csv&window=week",
    "/api/export?window=week",
])
def test_bad_query_values_are_400_with_a_reason_and_never_reach_the_report(
        stats: StatsRig, spy: dict[str, list[dict[str, Any]]], path: str) -> None:
    status, headers, body = stats.get(path)
    assert status == 400, path
    assert json.loads(body)["error"]
    assert "x-cache" not in headers
    assert stats.opened == [] and not any(spy.values())


def test_every_error_names_the_parameter_that_is_wrong(stats: StatsRig) -> None:
    assert "window" in json.loads(stats.get("/api/analytics/cost?window=year")[2])["error"]
    assert "days" in json.loads(stats.get("/api/analytics/history?days=3")[2])["error"]
    assert "limit" in json.loads(stats.get("/api/analytics/sessions?limit=0")[2])["error"]
    assert "format" in json.loads(stats.get("/api/export")[2])["error"]
    assert "colour" in json.loads(stats.get("/api/analytics/cost?colour=red")[2])["error"]


def test_a_report_is_computed_once_and_served_from_the_cache_for_twenty_seconds(
        stats: StatsRig, spy: dict[str, list[dict[str, Any]]]) -> None:
    first = stats.get("/api/analytics/cost")
    assert first[1]["x-cache"] == "MISS"
    stats.clock.now += 19
    second = stats.get("/api/analytics/cost")
    assert second[1]["x-cache"] == "HIT" and second[2] == first[2]
    assert len(spy["cost_report"]) == 1 and len(stats.opened) == 1
    stats.clock.now += 2                                                       # 21 s after it was computed
    third = stats.get("/api/analytics/cost")
    assert third[1]["x-cache"] == "MISS" and len(spy["cost_report"]) == 2
    assert len(stats.opened) == 1, "the Store is kept open between reports"


def test_each_set_of_parameters_has_its_own_cache_entry(stats: StatsRig,
                                                        spy: dict[str, list[dict[str, Any]]]) -> None:
    for path in ("/api/analytics/cost", "/api/analytics/cost?window=week", "/api/analytics/cost?window=week",
                 "/api/analytics/cost?window=cycle"):                          # cycle is the default: the 1st again
        stats.get(path)
    assert len(spy["cost_report"]) == 2
    for path in ("/api/analytics/sessions?limit=5&window=all", "/api/analytics/sessions?window=all&limit=5"):
        stats.get(path)                                                        # the order of the parameters is not
    assert len(spy["sessions_report"]) == 1
    assert stats.get("/api/analytics/sessions?window=all&limit=6")[1]["x-cache"] == "MISS"


def test_a_changed_setting_does_not_wait_out_the_cache(stats: StatsRig, settings: Settings,
                                                       spy: dict[str, list[dict[str, Any]]]) -> None:
    first = stats.json("/api/analytics/cost")
    assert first["plan"]["price_source"] == "detected"
    settings.set("plan_price_override", 250.0)
    assert stats.get("/api/analytics/cost")[1]["x-cache"] == "MISS"
    override = stats.json("/api/analytics/cost")
    assert override["plan"]["price_usd"] == 250.0 and override["plan"]["price_source"] == "override"
    assert spy["cost_report"][-1]["plan_price"] == 250.0
    settings.set("billing_day", 15)
    shifted = stats.json("/api/analytics/cost")
    assert spy["cost_report"][-1]["billing_day"] == 15
    assert shifted["value"]["cycle_start"] != first["value"]["cycle_start"]
    settings.set("plan_price_override", None)
    settings.set("billing_day", None)
    assert stats.json("/api/analytics/cost")["value"]["cycle_start"] == first["value"]["cycle_start"]


def test_the_cache_keeps_a_bounded_number_of_entries(stats: StatsRig) -> None:
    limit = server_mod.ANALYTICS_CACHE_MAX
    for days in range(7, 7 + limit + 10):
        stats.get(f"/api/analytics/history?days={days}")
    assert stats.get("/api/analytics/history?days=7")[1]["x-cache"] == "MISS"             # the oldest was dropped
    assert stats.get(f"/api/analytics/history?days={7 + limit + 9}")[1]["x-cache"] == "HIT"


def test_expired_entries_are_dropped_when_a_new_one_is_stored(stats: StatsRig) -> None:
    for days in (7, 8, 9):
        stats.get(f"/api/analytics/history?days={days}")
    stats.clock.now += 25
    stats.get("/api/analytics/pacing")
    assert len(stats.server._reader._cache) == 1


def test_a_reading_from_before_the_current_windows_does_not_pin_the_session_or_week(
        stats: StatsRig, spy: dict[str, list[dict[str, Any]]]) -> None:
    """The data service drops a limit whose window is over before it computes the windows; so does the API."""
    state = sample_state()
    assert state.usage is not None
    state.usage.limits[0].resets_at = NOW - timedelta(hours=1)               # the session window ended an hour ago
    stats.rec.state = state
    stats.get("/api/analytics/cost")
    assert [limit.kind for limit in spy["cost_report"][-1]["usage"].limits] == ["weekly_all"]
    state.usage.limits[1].resets_at = NOW - timedelta(minutes=1)
    stats.get("/api/analytics/pacing")
    assert spy["pacing_report"][-1]["usage"].limits == []
    assert stats.json("/api/analytics/pacing")["session"]["verdict"] == "UNKNOWN"


def test_reports_work_before_the_first_state_arrives(stats: StatsRig) -> None:
    stats.rec.state = None
    cost = stats.json("/api/analytics/cost")
    assert cost["plan"]["name"] == "Unknown plan" and cost["plan"]["price_usd"] is None
    assert cost["value"]["multiplier"] is None
    pacing = stats.json("/api/analytics/pacing")
    assert pacing["session"]["verdict"] == "UNKNOWN" and pacing["weekly"]["verdict"] == "UNKNOWN"
    # With no API window the session is the 5-hour block that holds now: the request an hour ago opened one.
    assert [s["session_id"] for s in stats.json("/api/analytics/sessions?window=session")["sessions"]] == ["s1"]


def test_the_cost_report_carries_real_spend_apart_from_the_api_equivalent_cost(stats: StatsRig) -> None:
    state = sample_state()
    assert state.usage is not None
    state.usage.spend = m.Spend(enabled=True, used=12.5, limit=50.0, currency="USD")
    stats.rec.state = state
    cost = stats.json("/api/analytics/cost")
    assert cost["spend"]["used"] == 12.5 and cost["basis"] == "API-equivalent at list prices"


def test_a_missing_database_layer_is_501_not_a_crash(make_server: Any) -> None:
    server, _ = make_server()                                                  # no analytics_store given
    for path in ("/api/analytics/cost", "/api/export?format=json"):
        status, _headers, body = request(server, "GET", path)
        assert status == 501 and json.loads(body) == {"error": "statistics are not available"}
    assert request(server, "GET", "/api/analytics/cost?window=x")[0] == 400   # a bad request is still said to be bad


def test_a_database_that_cannot_be_opened_is_a_503_and_is_tried_again(make_server: Any, tmp_path: Path,
                                                                      caplog: pytest.LogCaptureFixture) -> None:
    db = tmp_path / "usage.db"
    seed_usage_db(db)
    attempts: list[int] = []

    def flaky() -> Store:
        attempts.append(1)
        if len(attempts) == 1:
            raise sqlite3.OperationalError("unable to open database file")
        return Store(db)

    server, _ = make_server(analytics_store=flaky, wall_clock=lambda: NOW)
    with caplog.at_level(logging.WARNING, logger="usage_monitor.ui.server"):
        status, _headers, body = request(server, "GET", "/api/analytics/pacing")
    assert status == 503 and "could not be opened" in json.loads(body)["error"]
    assert "unable to open" not in body.decode("utf-8")                       # no internals in the answer
    assert request(server, "GET", "/api/analytics/pacing")[0] == 200
    assert len(attempts) == 2


def test_a_store_that_fails_to_open_for_a_newer_schema_is_a_503(make_server: Any) -> None:
    from usage_monitor.data.store import StoreError

    def too_new() -> Store:
        raise StoreError("database schema 9 is newer than this build (3)")

    server, _ = make_server(analytics_store=too_new)
    assert request(server, "GET", "/api/analytics/cost")[0] == 503


def test_a_query_that_fails_reopens_the_store_and_runs_once_more(stats: StatsRig,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    real = analytics.history_report
    failures: list[int] = []

    def fail_once(store: Store, **kwargs: Any) -> Any:
        if not failures:
            failures.append(1)
            raise sqlite3.DatabaseError("database disk image is malformed")
        return real(store, **kwargs)

    monkeypatch.setattr(analytics, "history_report", fail_once)
    status, _headers, body = stats.get("/api/analytics/history")
    assert status == 200 and set(json.loads(body)) == REPORT_KEYS["history"]
    assert len(stats.opened) == 2
    with pytest.raises(sqlite3.ProgrammingError):                              # the first one was closed, not leaked
        stats.opened[0].query("SELECT 1")
    assert stats.opened[1].query("SELECT 1")[0][0] == 1


def test_a_query_that_keeps_failing_is_a_503_and_the_next_request_starts_clean(
        stats: StatsRig, monkeypatch: pytest.MonkeyPatch) -> None:
    real = analytics.pacing_report
    broken = [True]

    def maybe_fail(store: Store, **kwargs: Any) -> Any:
        if broken[0]:
            raise sqlite3.OperationalError("database is locked")
        return real(store, **kwargs)

    monkeypatch.setattr(analytics, "pacing_report", maybe_fail)
    status, _headers, body = stats.get("/api/analytics/pacing")
    assert status == 503 and json.loads(body) == {"error": "statistics are not available right now"}
    assert len(stats.opened) == 2
    for store in stats.opened:
        with pytest.raises(sqlite3.ProgrammingError):
            store.query("SELECT 1")
    broken[0] = False
    assert stats.get("/api/analytics/pacing")[0] == 200
    assert len(stats.opened) == 3, "a failed report is not cached, and the Store is opened afresh"


def test_a_bug_in_a_report_is_a_500_without_details_and_keeps_the_store(
        stats: StatsRig, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(store: Store, **kwargs: Any) -> Any:
        raise KeyError("private detail")

    monkeypatch.setattr(analytics, "history_report", boom)
    status, _headers, body = stats.get("/api/analytics/history")
    assert status == 500 and b"private detail" not in body
    assert len(stats.opened) == 1 and stats.opened[0].query("SELECT 1")[0][0] == 1


def test_stop_closes_the_statistics_store(stats: StatsRig) -> None:
    stats.get("/api/analytics/pacing")
    store = stats.opened[0]
    stats.server.stop()
    with pytest.raises(sqlite3.ProgrammingError):
        store.query("SELECT 1")
    stats.server.stop()                                                        # stopping twice is harmless


def test_the_data_services_own_connection_is_never_used(stats: StatsRig) -> None:
    """The server asks its factory for a Store of its own and never reaches for another one."""
    with Store(stats.db) as service_store:
        stats.get("/api/analytics/cost")
        assert stats.opened and all(store is not service_store for store in stats.opened)
        service_store.add_items([m.ApiErrorEvent("e1", "s1", NOW)], PRICING)   # the writer is not blocked by a reader
        assert stats.json("/api/analytics/efficiency?window=all")["api_errors"] == 1


def test_a_cached_report_and_the_state_answer_while_another_report_is_being_computed(
        stats: StatsRig, monkeypatch: pytest.MonkeyPatch) -> None:
    stats.get("/api/analytics/history")                                        # now cached
    gate, entered = threading.Event(), threading.Event()
    real = analytics.cost_report

    def slow(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        assert gate.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(analytics, "cost_report", slow)
    outcome: list[tuple[int, dict[str, str], bytes]] = []
    worker = threading.Thread(target=lambda: outcome.append(stats.get("/api/analytics/cost")))
    worker.start()
    try:
        assert entered.wait(5)
        assert stats.get("/api/analytics/history")[1]["x-cache"] == "HIT"       # the cache is not behind the query
        assert request(stats.server, "GET", "/api/state")[0] == 200
    finally:
        gate.set()
        worker.join(10)
    assert outcome and outcome[0][0] == 200


def test_two_requests_for_the_same_report_compute_it_once(stats: StatsRig,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    gate, entered = threading.Event(), threading.Event()
    real = analytics.cost_report
    calls: list[int] = []

    def slow(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        entered.set()
        assert gate.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(analytics, "cost_report", slow)
    outcome: list[tuple[int, dict[str, str], bytes]] = []
    workers = [threading.Thread(target=lambda: outcome.append(stats.get("/api/analytics/cost"))) for _ in range(3)]
    for worker in workers:
        worker.start()
    try:
        assert entered.wait(5)
        time.sleep(0.2)                                                       # the others are queued behind the first
    finally:
        gate.set()
        for worker in workers:
            worker.join(10)
    assert len(calls) == 1
    assert sorted(status for status, _h, _b in outcome) == [200, 200, 200]
    assert sorted(headers["x-cache"] for _s, headers, _b in outcome) == ["HIT", "HIT", "MISS"]


def test_a_result_is_cached_before_the_next_waiter_is_let_in(stats: StatsRig,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """A request queued behind the one that is computing must find the result when its turn comes, however
    long the first takes to store it (it used to be stored after the lock was let go)."""
    real_remember = server_mod._Reader._remember

    def slow_remember(self: Any, key: Any, body: bytes) -> None:
        time.sleep(0.3)                                                       # the window a waiter could slip through
        real_remember(self, key, body)

    gate, entered = threading.Event(), threading.Event()
    real = analytics.cost_report
    calls: list[int] = []

    def slow_report(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        entered.set()
        assert gate.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(server_mod._Reader, "_remember", slow_remember)
    monkeypatch.setattr(analytics, "cost_report", slow_report)
    outcome: list[tuple[int, dict[str, str], bytes]] = []
    first = threading.Thread(target=lambda: outcome.append(stats.get("/api/analytics/cost")))
    second = threading.Thread(target=lambda: outcome.append(stats.get("/api/analytics/cost")))
    first.start()
    try:
        assert entered.wait(5)
        second.start()
        time.sleep(0.2)                                                       # the second request is queued now
    finally:
        gate.set()
        first.join(10)
        second.join(10)
    assert len(calls) == 1
    assert sorted(headers["x-cache"] for _status, headers, _body in outcome) == ["HIT", "MISS"]


def test_the_export_is_a_csv_download_of_the_daily_rollups(stats: StatsRig) -> None:
    status, headers, body = stats.get("/api/export?format=csv")
    assert status == 200
    assert headers["content-type"] == "text/csv; charset=utf-8"
    local_day = NOW.astimezone().date().isoformat()
    assert headers["content-disposition"] == f'attachment; filename="claude-usage-daily-{local_day}.csv"'
    assert headers["cache-control"] == "no-store" and headers["x-content-type-options"] == "nosniff"
    with Store(stats.db) as store:
        expected = analytics.daily_rollups(store)
    rows = list(csv.DictReader(io.StringIO(body.decode("utf-8"), newline="")))
    assert len(rows) == len(expected) >= 3
    assert [row["date"] for row in rows] == [row["date"] for row in expected]
    assert set(rows[0]) == set(expected[0]) and "cost_usd" in rows[0]
    assert float(rows[-1]["cost_usd"]) == pytest.approx(expected[-1]["cost_usd"])
    assert int(rows[0]["requests"]) == expected[0]["requests"]
    assert body.endswith(b"\r\n")


def test_the_export_is_a_json_download_of_the_same_rows(stats: StatsRig) -> None:
    status, headers, body = stats.get("/api/export?format=json")
    assert status == 200
    assert headers["content-type"] == "application/json; charset=utf-8"
    local_day = NOW.astimezone().date().isoformat()
    assert headers["content-disposition"] == f'attachment; filename="claude-usage-daily-{local_day}.json"'
    with Store(stats.db) as store:
        expected = analytics.daily_rollups(store)
    payload = json.loads(body)
    assert payload["basis"] == analytics.BASIS and payload["rows"] == json.loads(json.dumps(expected))


def test_the_export_is_cached_but_the_filename_follows_the_clock(make_server: Any, tmp_path: Path,
                                                                 spy: dict[str, list[dict[str, Any]]]) -> None:
    db = tmp_path / "usage.db"
    seed_usage_db(db)
    today = [datetime(2026, 1, 2, 3, 30, tzinfo=timezone.utc)]
    server, _ = make_server(analytics_store=lambda: Store(db), wall_clock=lambda: today[0], clock=FakeClock())
    first = request(server, "GET", "/api/export?format=csv")
    today[0] = datetime(2026, 7, 30, 23, 59, tzinfo=timezone.utc)             # later, but within the 20 s
    second = request(server, "GET", "/api/export?format=csv")
    assert (first[1]["x-cache"], second[1]["x-cache"]) == ("MISS", "HIT") and first[2] == second[2]
    assert len(spy["daily_rollups"]) == 1
    for answer, moment in ((first, datetime(2026, 1, 2, 3, 30, tzinfo=timezone.utc)), (second, today[0])):
        assert answer[1]["content-disposition"] == (
            f'attachment; filename="claude-usage-daily-{moment.astimezone().date().isoformat()}.csv"')
    assert request(server, "GET", "/api/export?format=json")[1]["x-cache"] == "MISS"     # its own entry


def test_an_empty_database_exports_an_empty_csv_and_an_empty_json_rows_list(make_server: Any, tmp_path: Path) -> None:
    db = tmp_path / "empty.db"
    Store(db).close()
    server, _ = make_server(analytics_store=lambda: Store(db), wall_clock=lambda: NOW)
    status, _headers, body = request(server, "GET", "/api/export?format=csv")
    assert status == 200 and body == b""                                      # no rows, so no columns to name
    assert json.loads(request(server, "GET", "/api/export?format=json")[2]) == {"basis": analytics.BASIS, "rows": []}
    for name in REPORT_NAMES:                                                  # and every report copes with it
        assert request(server, "GET", f"/api/analytics/{name}")[0] == 200


# ── POST /api/settings: the alert, price and billing keys ──────────────────────────

def test_the_alert_settings_are_stored_and_reported(make_server: Any, settings: Settings) -> None:
    server, rec = make_server()
    defaults = json.loads(request(server, "GET", "/api/state")[2])["settings"]
    assert defaults["alerts_enabled"] is True and defaults["alert_thresholds"] == [80, 90, 100]
    assert defaults["alert_on_threshold"] is defaults["alert_on_reset"] is defaults["alert_on_credits"] is True
    assert defaults["alert_quiet_hours"] is None and "notifications_enabled" not in defaults

    payload = {"alerts_enabled": False, "alert_thresholds": [60, 95], "alert_on_reset": False,
               "alert_on_credits": False, "alert_quiet_hours": {"start": "23:00", "end": "06:30"},
               "plan_price_override": 120.5, "billing_day": 7}
    status, body = post_json(server, "/api/settings", payload)
    assert status == 200 and body["applied"] == payload
    for key, value in payload.items():
        assert settings.get(key) == value, key
        assert body["settings"][key] == value, key
    assert rec.applied[-1] == payload


@pytest.mark.parametrize("key, values", [
    ("alerts_enabled", [True, False, 1, 0, "true", None]),
    ("alert_on_threshold", [True, False, 1, "x", None]),
    ("alert_thresholds", [[50], [80, 90, 100], [1, 2, 3, 4, 5], [], [1, 2, 3, 4, 5, 6], [0], [101], [80, 80], [80.5],
                          ["80"], [True], 80, None, "80", [[1]], [{"a": 1}], [-5]]),
    ("alert_quiet_hours", [None, {"start": "22:00", "end": "07:00"}, {"start": "00:00", "end": "23:59"},
                           {"start": "22:00", "end": "22:00"}, {"start": "22:00"}, {"start": "24:00", "end": "07:00"},
                           {"start": "22:00", "end": "7:00"}, {"start": "22:00", "end": "07:00", "x": 1},
                           {"start": 2200, "end": "07:00"}, "22:00", [], 5, {}]),
    ("billing_day", [None, 1, 31, 15, 0, 32, -1, 1.5, True, "15"]),
])
def test_the_server_and_settings_agree_on_what_these_keys_accept(tmp_path: Path, key: str,
                                                                 values: list[Any]) -> None:
    """The specs repeat Settings' rules so a bad value is refused before anything is applied. This keeps the
    two from drifting apart."""
    scratch = Settings(tmp_path / "scratch.json", save_delay_s=0.0)
    try:
        for value in values:
            try:
                scratch.set(key, value)
                settings_says = True
            except ValueError:
                settings_says = False
            assert server_mod.SETTING_SPECS[key].check(value) is settings_says, (key, value)
    finally:
        scratch.close()


def test_settings_own_refusal_is_still_a_400_that_applies_nothing(make_server: Any, settings: Settings,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(server_mod.SETTING_SPECS, "alert_thresholds", server_mod._Spec(lambda value: True, [80]))
    server, rec = make_server()
    status, body = post_json(server, "/api/settings", {"reduce_motion": True, "alert_thresholds": [0]})
    assert status == 400 and "alert_thresholds" in body["error"]
    assert settings.get("reduce_motion", False) is False and rec.applied == []


def test_a_bad_alert_value_in_a_batch_applies_nothing_and_leaves_the_run_entry_alone(
        make_server: Any, settings: Settings, startup_fake: dict[str, Any]) -> None:
    server, rec = make_server()
    status, _ = post_json(server, "/api/settings", {"launch_on_startup": True, "alert_thresholds": [5, 5]})
    assert status == 400
    assert startup_fake["calls"] == [] and rec.applied == []
    assert settings.get("alert_thresholds") == [80, 90, 100]


def test_the_alert_settings_reach_the_alert_engine_live(make_server: Any, settings: Settings) -> None:
    from usage_monitor.data.alerts import AlertEngine

    engine = AlertEngine(settings)
    server, _ = make_server()
    quiet = {"start": "00:00", "end": "23:59"}
    assert post_json(server, "/api/settings", {"alert_thresholds": [20, 50], "alert_quiet_hours": quiet})[0] == 200
    assert engine.evaluate(sample_state(percent=10.0)) == []                    # the baseline
    assert engine.evaluate(sample_state(percent=30.0)) == []                    # 20 % was crossed, in quiet hours
    assert post_json(server, "/api/settings", {"alert_quiet_hours": None})[0] == 200
    assert [alert.title for alert in engine.evaluate(sample_state(percent=55.0))] == ["Session limit at 50%"]


# ── Dev isolation ──────────────────────────────────────────────────────────────────

def test_the_dev_environment_variable_makes_a_dev_server_whatever_the_flag_says(
        make_server: Any, registry: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:
    """The flag comes from the caller; the variable comes from the shell that started the process. Either one
    keeps launch_on_startup away from the sign-in entry that the installed copy owns."""
    monkeypatch.setenv(server_mod.DEV_ENV, "1")
    server, rec = make_server(dev=False)
    assert server.dev is True
    assert json.loads(request(server, "GET", "/api/state")[2])["dev"] is True
    with caplog.at_level(logging.INFO, logger="usage_monitor.ui.server"):
        assert post_json(server, "/api/settings", {"launch_on_startup": True})[0] == 200
    assert registry["reads"] == 0 and registry["writes"] == [] and registry["deletes"] == 0
    assert any("not touched" in record.getMessage() and "dev instance" in record.getMessage()
               for record in caplog.records)
    state = json.loads(request(server, "GET", "/api/state")[2])
    assert state["settings"]["launch_on_startup"] is True and registry["reads"] == 0
    assert rec.applied[-1] == {"launch_on_startup": True}


@pytest.mark.parametrize("value", ["0", "", "true", "yes", "2"])
def test_only_cum_dev_instance_1_counts_for_the_server(make_server: Any, monkeypatch: pytest.MonkeyPatch,
                                                       value: str) -> None:
    monkeypatch.setenv(server_mod.DEV_ENV, value)
    server, _ = make_server(dev=False)
    assert server.dev is False


def test_a_dev_server_logs_that_the_run_entry_was_skipped(make_server: Any, registry: dict[str, Any],
                                                          caplog: pytest.LogCaptureFixture) -> None:
    server, _ = make_server(dev=True)
    with caplog.at_level(logging.INFO, logger="usage_monitor.ui.server"):
        post_json(server, "/api/settings", {"launch_on_startup": False})
    assert any("start with Windows is off in memory only" in record.getMessage() for record in caplog.records)
    assert registry["deletes"] == 0 and registry["value"] == '"C:/installed/ClaudeUsageMonitor.exe"'


def test_apply_settings_is_the_post_without_the_http(make_server: Any, settings: Settings,
                                                     themes_module: types.ModuleType) -> None:
    server, rec = make_server()
    assert server.apply_settings({"theme_id": "night_city"}) == {"theme_id": "night_city", "theme_variant": "cyan"}
    assert settings.get("theme_id") == "night_city" and rec.applied[-1]["theme_id"] == "night_city"
    for bad in ({"theme_id": "nope"}, [1], {"unknown": 1}, {"billing_day": 99}):
        with pytest.raises(ValueError):
            server.apply_settings(bad)


# ── JSON encoding ───────────────────────────────────────────────────────────────────

def test_to_jsonable_encodes_enums_datetimes_and_nan() -> None:
    assert to_jsonable(m.ApiStatus.RATE_LIMITED) == "rate_limited"
    assert to_jsonable(datetime(2026, 1, 2, 3, 4, 5, 678000, tzinfo=timezone.utc)) == "2026-01-02T03:04:05.678Z"
    assert to_jsonable(datetime(2026, 1, 2, 3, 4)) == "2026-01-02T03:04:00.000Z"  # naive is UTC
    local = datetime(2026, 1, 2, 12, 0, tzinfo=timezone(timedelta(hours=9)))
    assert to_jsonable(local) == "2026-01-02T03:00:00.000Z"
    assert to_jsonable(float("nan")) is None
    assert to_jsonable(float("inf")) is None
    assert to_jsonable((1, [2, 3])) == [1, [2, 3]]


def test_to_jsonable_drops_raw_and_keeps_derived_numbers() -> None:
    snapshot = m.UsageSnapshot(fetched_at=NOW, limits=[m.Limit(kind="session", group=None, percent=150.0,
                                                               severity=None, resets_at=None, is_active=True,
                                                               label="SESSION")], raw={"x": 1})
    encoded = to_jsonable(snapshot)
    assert "raw" not in encoded
    assert encoded["limits"][0]["fraction"] == pytest.approx(1.5)
    assert encoded["limits"][0]["resets_at"] is None
    totals = to_jsonable(m.TokenTotals(input=1, cache_read=3, cache_write_5m=1, cost_usd=2.0, nocache_cost_usd=3.0))
    assert totals["cache_hit_rate"] == pytest.approx(0.6)
    assert totals["caching_saved_usd"] == pytest.approx(1.0)


def test_to_jsonable_full_monitor_state_is_valid_json() -> None:
    state = sample_state(status=m.ApiStatus.PAUSED)
    text = json.dumps(to_jsonable(state), allow_nan=False)
    decoded = json.loads(text)
    assert decoded["status"] == "paused"
    assert decoded["plan"]["monthly_price_usd"] == 100.0
    assert decoded["burn"]["velocity"] == "NORMAL"
    assert decoded["ingest"]["files_total"] == 3


def test_iso_utc_helper_matches_the_encoder() -> None:
    assert server_mod.iso_utc(NOW) == "2026-10-21T16:12:00.000Z"


# ── Startup entry ───────────────────────────────────────────────────────────────────

def test_command_line_for_the_frozen_exe_is_the_quoted_exe_only(tmp_path: Path) -> None:
    exe = tmp_path / "Program Files" / "Claude Usage Monitor" / "ClaudeUsageMonitor.exe"
    assert startup.command_line(frozen=True, executable=str(exe)) == f'"{exe}"'


def test_command_line_from_source_uses_pythonw_and_main(tmp_path: Path) -> None:
    python = tmp_path / "venv with space" / "Scripts" / "python.exe"
    pythonw = python.with_name("pythonw.exe")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    pythonw.write_bytes(b"")
    main = tmp_path / "repo" / "main.py"
    line = startup.command_line(frozen=False, executable=str(python), main_script=main)
    assert line == f'"{pythonw}" "{main}"'


def test_command_line_falls_back_to_python_when_pythonw_is_missing(tmp_path: Path) -> None:
    python = tmp_path / "python.exe"
    main = tmp_path / "main.py"
    assert startup.command_line(frozen=False, executable=str(python), main_script=main) == f'"{python}" "{main}"'


def test_pythonw_for_leaves_other_interpreters_alone(tmp_path: Path) -> None:
    other = tmp_path / "py3.exe"
    other.write_bytes(b"")
    (tmp_path / "pythonw.exe").write_bytes(b"")
    assert startup.pythonw_for(str(other)) == str(other)


def test_startup_enable_disable_and_repair(monkeypatch: pytest.MonkeyPatch) -> None:
    values: dict[str, str | None] = {"value": None}
    monkeypatch.setattr(startup, "_read_value", lambda: values["value"])
    monkeypatch.setattr(startup, "_write_value", lambda command: values.__setitem__("value", command))
    monkeypatch.setattr(startup, "_delete_value", lambda: values.__setitem__("value", None))
    monkeypatch.setattr(startup, "is_enabled", REAL_IS_ENABLED)  # real logic, fake storage
    monkeypatch.setattr(startup, "set_enabled", REAL_SET_ENABLED)
    installed = startup.command_line(frozen=True)

    assert startup.is_enabled() is False
    assert startup.repair_if_enabled(frozen=True) is False           # nothing to repair when absent
    startup.set_enabled(True)
    assert values["value"] == startup.command_line()
    assert startup.is_enabled() is True
    values["value"] = installed
    assert startup.repair_if_enabled(frozen=True) is False            # already correct
    values["value"] = '"C:/old place/ClaudeUsageMonitor.exe"'
    assert startup.repair_if_enabled(frozen=True) is True
    assert values["value"] == installed
    values["value"] = '"C:/old place/ClaudeUsageMonitor.exe"'
    startup.set_enabled(True)                               # overwrites an entry that points elsewhere, on request
    assert values["value"] == startup.command_line()
    startup.set_enabled(False)
    assert values["value"] is None
    startup.set_enabled(False)                              # removing twice is fine
    assert startup.is_enabled() is False


def test_only_the_frozen_build_repairs_the_startup_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    """From source, repair_if_enabled would point an installed copy's entry at the checkout."""
    operations: list[str] = []
    entry = '"C:/Users/me/AppData/Local/Programs/Claude Usage Monitor/ClaudeUsageMonitor.exe"'
    monkeypatch.setattr(startup, "_read_value", lambda: operations.append("read") or entry)
    monkeypatch.setattr(startup, "_write_value", lambda command: operations.append("write"))
    monkeypatch.setattr(startup, "_delete_value", lambda: operations.append("delete"))

    monkeypatch.delattr(sys, "frozen", raising=False)                  # running from source
    assert startup.repair_if_enabled() is False
    assert startup.repair_if_enabled(frozen=False) is False
    assert operations == []                                            # not even a read

    monkeypatch.setattr(sys, "frozen", True, raising=False)            # the installed build asks sys.frozen
    assert startup.repair_if_enabled() is True
    assert operations == ["read", "write"]


def test_is_enabled_never_raises_when_the_registry_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse() -> str:
        raise PermissionError("denied")

    monkeypatch.setattr(startup, "_read_value", refuse)
    monkeypatch.setattr(startup, "is_enabled", REAL_IS_ENABLED)
    assert startup.is_enabled() is False


@ui_only
def test_registry_round_trip_under_a_throwaway_key(monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform != "win32":
        pytest.skip("Windows only")
    import winreg
    key_path = rf"Software\ClaudeUsageMonitorTests-{os.getpid()}"
    monkeypatch.setattr(startup, "RUN_KEY", key_path)
    monkeypatch.setattr(startup, "is_enabled", REAL_IS_ENABLED)
    monkeypatch.setattr(startup, "set_enabled", REAL_SET_ENABLED)
    try:
        startup.set_enabled(True)
        assert startup.is_enabled() is True
        assert startup._read_value() == startup.command_line()
        startup.set_enabled(False)
        assert startup.is_enabled() is False
    finally:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
        except FileNotFoundError:
            pass


# ── Dashboard launcher and child ───────────────────────────────────────────────────

def test_build_command_frozen_and_source(tmp_path: Path) -> None:
    exe = tmp_path / "ClaudeUsageMonitor.exe"
    assert dashboard.build_command(4321, frozen=True, executable=str(exe)) == [
        str(exe), "--dashboard", "--port", "4321"]
    python = tmp_path / "python.exe"
    main = tmp_path / "main.py"
    assert dashboard.build_command(9, frozen=False, executable=str(python), main_script=main) == [
        str(python), str(main), "--dashboard", "--port", "9"]


def test_colorref_is_bgr_order() -> None:
    assert dashboard.colorref("#090909") == 0x090909
    assert dashboard.colorref("#ff0000") == 0x0000FF
    assert dashboard.colorref("#00ff00") == 0x00FF00
    assert dashboard.colorref("#0000ff") == 0xFF0000
    for bad in ("090909", "#0909", "#zzzzzz", None, 123):
        with pytest.raises(ValueError):
            dashboard.colorref(bad)  # type: ignore[arg-type]


@pytest.mark.parametrize("url, allowed", [
    ("https://github.com/Godimas101/claude-usage-monitor", True),
    ("https://github.com/Godimas101/claude-usage-monitor/issues/new?template=bug_report.md&labels=bug", True),
    ("https://patreon.com/Godimas101", True),
    ("http://github.com/Godimas101", False),
    ("https://evil.example/github.com", False),
    ("https://github.com.evil.example/", False),
    ("javascript:alert(1)", False),
    ("file:///C:/Windows/win.ini", False),
    (None, False),
    ("https://github.com/" + "a" * 3000, False),
])
def test_open_external_allows_only_listed_https_hosts(url: Any, allowed: bool) -> None:
    assert dashboard._external_allowed(url) is allowed


def test_dashboard_api_rejects_bad_colours_without_touching_windows() -> None:
    api = dashboard.DashboardApi("tok")
    assert api.get_token() == "tok"
    assert api.set_chrome("not-a-colour", "#ffffff", "#000000", True) is False
    assert api.open_external("http://github.com/x") is False


def test_dashboard_api_does_not_expose_private_methods() -> None:
    exposed = {name for name in dir(dashboard.DashboardApi) if not name.startswith("_")}
    assert exposed == {"get_token", "set_chrome", "open_external", "save_export"}


def test_the_window_finder_has_a_public_name_and_keeps_the_old_one() -> None:
    assert callable(dashboard.find_window_of) and dashboard._find_window_of is dashboard.find_window_of
    assert dashboard.find_window_of(2**31 - 5) is None                          # no such process, no window


# ── save_export: the export through the native save dialog ─────────────────────────

class SaveWindow:
    """A pywebview window double that answers the save dialog and remembers how it was asked."""

    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.calls: list[dict[str, Any]] = []

    def create_file_dialog(self, dialog_type: Any = None, directory: str = "", allow_multiple: bool = False,
                           save_filename: str = "", file_types: Any = ()) -> Any:
        self.calls.append({"dialog_type": dialog_type, "save_filename": save_filename,
                           "file_types": tuple(file_types)})
        return self.answer() if callable(self.answer) else self.answer


@pytest.fixture
def fake_webview(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    module = types.ModuleType("webview")
    module.FileDialog = types.SimpleNamespace(OPEN=10, FOLDER=20, SAVE=30)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "webview", module)
    return module


def saving_api(stats: StatsRig, answer: Any, token: str | None = None) -> tuple[dashboard.DashboardApi, SaveWindow]:
    api = dashboard.DashboardApi(token or stats.server.token, f"http://127.0.0.1:{stats.server.port}")
    window = SaveWindow(answer)
    api._bind(window)
    return api, window


def test_save_export_writes_the_download_where_the_dialog_says(stats: StatsRig, tmp_path: Path,
                                                               fake_webview: types.ModuleType) -> None:
    target = tmp_path / "out" / "mine.csv"
    target.parent.mkdir()
    api, window = saving_api(stats, lambda: (str(target),))
    result = api.save_export("csv")
    assert result == {"ok": True, "path": str(target), "error": None}
    assert target.read_bytes() == stats.get("/api/export?format=csv")[2]
    call = window.calls[0]
    assert call["dialog_type"] == 30
    assert call["save_filename"] == f"claude-usage-daily-{NOW.astimezone().date().isoformat()}.csv"
    assert call["file_types"] == ("CSV file (*.csv)", "All files (*.*)")
    assert [entry.name for entry in target.parent.iterdir()] == ["mine.csv"], "no temporary file is left behind"


def test_save_export_json_works_with_a_pywebview_that_only_has_the_old_constant(
        stats: StatsRig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old = types.ModuleType("webview")
    old.SAVE_DIALOG = 30  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "webview", old)
    target = tmp_path / "rollups.json"
    api, window = saving_api(stats, target)                                    # a bare path string is fine too
    assert api.save_export("json") == {"ok": True, "path": str(target), "error": None}
    assert json.loads(target.read_bytes())["basis"] == analytics.BASIS
    assert window.calls[0]["dialog_type"] == 30 and window.calls[0]["file_types"][0] == "JSON file (*.json)"
    assert window.calls[0]["save_filename"].endswith(".json")


@pytest.mark.parametrize("cancelled", [None, (), []])
def test_a_cancelled_dialog_is_not_an_error_and_writes_nothing(stats: StatsRig, tmp_path: Path,
                                                               fake_webview: types.ModuleType,
                                                               cancelled: Any) -> None:
    api, window = saving_api(stats, cancelled)
    assert api.save_export("csv") == {"ok": False, "path": None, "error": None}
    assert len(window.calls) == 1 and not list(tmp_path.glob("*.csv"))


def test_a_name_without_an_extension_gets_the_formats(stats: StatsRig, tmp_path: Path,
                                                      fake_webview: types.ModuleType) -> None:
    api, _ = saving_api(stats, (str(tmp_path / "report"),))
    result = api.save_export("json")
    assert result["ok"] is True and result["path"] == str(tmp_path / "report.json")
    assert (tmp_path / "report.json").is_file() and not (tmp_path / "report").exists()


@pytest.mark.parametrize("fmt", ["xml", "", "CSV", "csv ", None, 7, "../x"])
def test_save_export_only_knows_csv_and_json_and_does_not_ask_the_monitor_otherwise(
        stats: StatsRig, fake_webview: types.ModuleType, fmt: Any) -> None:
    api, window = saving_api(stats, lambda: pytest.fail("no dialog for a format that does not exist"))
    result = api.save_export(fmt)
    assert result == {"ok": False, "path": None, "error": "the format must be csv or json"}
    assert window.calls == [] and stats.opened == []


def test_the_dialog_is_shown_only_after_the_monitor_has_produced_the_export(stats: StatsRig, tmp_path: Path,
                                                                            fake_webview: types.ModuleType) -> None:
    """A refused or failed fetch must not first make the user pick a place to save nothing."""
    api, window = saving_api(stats, lambda: (str(tmp_path / "x.csv"),), token="not the monitor's token")
    result = api.save_export("csv")
    assert result == {"ok": False, "path": None, "error": "the monitor answered 401"}
    assert window.calls == [] and not (tmp_path / "x.csv").exists()


def test_a_monitor_that_cannot_be_reached_is_an_error_not_an_exception(
        tmp_path: Path, fake_webview: types.ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    class Unreachable:
        def open(self, request: Any, timeout: float = 0) -> Any:
            raise urllib.error.URLError(ConnectionRefusedError(10061, "nobody is listening"))

    monkeypatch.setattr(dashboard, "_local_opener", lambda: Unreachable())    # a refused connect takes 2 s on Windows
    api = dashboard.DashboardApi("tok", "http://127.0.0.1:9")
    window = SaveWindow(lambda: (str(tmp_path / "x.csv"),))
    api._bind(window)
    assert api.save_export("csv") == {"ok": False, "path": None, "error": "the monitor could not be reached"}
    assert window.calls == []


def test_a_file_that_cannot_be_written_is_reported_with_its_name(stats: StatsRig, tmp_path: Path,
                                                                 fake_webview: types.ModuleType) -> None:
    api, _ = saving_api(stats, (str(tmp_path / "no such folder" / "x.csv"),))
    result = api.save_export("csv")
    assert result["ok"] is False and result["path"] is None and result["error"].startswith("could not write x.csv")


def test_a_failed_write_leaves_the_file_the_user_chose_to_overwrite_as_it_was(
        stats: StatsRig, tmp_path: Path, fake_webview: types.ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "keep.csv"
    target.write_bytes(b"precious")

    def refuse(src: str, dst: Any) -> None:
        raise PermissionError(13, "the file is open in another program")

    monkeypatch.setattr(dashboard.os, "replace", refuse)
    api, _ = saving_api(stats, (str(target),))
    result = api.save_export("csv")
    assert result["ok"] is False and "open in another program" in result["error"]
    assert target.read_bytes() == b"precious"
    assert [entry.name for entry in tmp_path.glob("keep*")] == ["keep.csv"], "the temporary file is removed"


def test_an_export_that_is_too_big_or_a_base_that_is_not_the_monitors_is_said_so(
        stats: StatsRig, tmp_path: Path, fake_webview: types.ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    api, window = saving_api(stats, lambda: (str(tmp_path / "x.csv"),))
    monkeypatch.setattr(dashboard, "MAX_EXPORT_BYTES", 5)
    assert api.save_export("csv") == {"ok": False, "path": None, "error": "the export is larger than 16 MB"}
    elsewhere = dashboard.DashboardApi("tok", "http://example.com:80")
    elsewhere._bind(window)
    result = elsewhere.save_export("csv")
    assert result["ok"] is False and "only read from the monitor on this machine" in result["error"]
    assert window.calls == [] and not (tmp_path / "x.csv").exists()


def test_save_export_before_the_window_exists_is_an_error(stats: StatsRig) -> None:
    api = dashboard.DashboardApi(stats.server.token, f"http://127.0.0.1:{stats.server.port}")
    assert api.save_export("csv") == {"ok": False, "path": None, "error": "the dashboard window is not ready"}


def test_a_dialog_that_raises_becomes_an_error_for_the_page(stats: StatsRig, fake_webview: types.ModuleType,
                                                            caplog: pytest.LogCaptureFixture) -> None:
    def broken() -> Any:
        raise RuntimeError("the dialog blew up")

    api, _ = saving_api(stats, broken)
    with caplog.at_level(logging.ERROR, logger="usage_monitor.ui.dashboard"):
        assert api.save_export("csv") == {"ok": False, "path": None, "error": "could not save the export"}
    assert "save_export failed" in caplog.text
    assert api.save_export("xml")["error"] == "the format must be csv or json"   # and the next call still works


def test_only_one_save_dialog_is_open_at_a_time(stats: StatsRig, tmp_path: Path,
                                                fake_webview: types.ModuleType) -> None:
    release, shown = threading.Event(), threading.Event()

    def slow_dialog() -> Any:
        shown.set()
        assert release.wait(10)
        return (str(tmp_path / "late.csv"),)

    api, _ = saving_api(stats, slow_dialog)
    first: list[dict[str, Any]] = []
    worker = threading.Thread(target=lambda: first.append(api.save_export("csv")))
    worker.start()
    try:
        assert shown.wait(5)
        assert api.save_export("csv") == {"ok": False, "path": None, "error": "a save dialog is already open"}
    finally:
        release.set()
        worker.join(10)
    assert first and first[0]["ok"] is True
    assert api.save_export("csv")["error"] != "a save dialog is already open"   # the lock was let go


@pytest.mark.parametrize("base", [
    "http://example.com:80", "http://localhost:5000", "http://127.0.0.1", "http://127.0.0.1:5000/",
    "https://127.0.0.1:5000", "http://127.0.0.1:5000/api", "http://127.0.0.1.evil.example:5000",
    "http://user@127.0.0.1:5000", "", "file:///C:/x",
])
def test_the_export_is_only_ever_requested_from_the_monitor_on_this_machine(base: str) -> None:
    with pytest.raises(ValueError):
        dashboard.fetch_export(base, "tok", "csv")
    with pytest.raises(ValueError):
        dashboard.fetch_export("http://127.0.0.1:5000", "tok", "xml")


@contextlib.contextmanager
def tiny_server(respond: Callable[[http.server.BaseHTTPRequestHandler], None]) -> Iterator[tuple[int, list[dict[str, str]]]]:
    """A one-route HTTP server on loopback that records what it was asked and answers through ``respond``."""
    seen: list[dict[str, str]] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen.append({"path": self.path, "token": self.headers.get("X-Monitor-Token", ""),
                         "host": self.headers.get("Host", "")})
            respond(self)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1], seen
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(5)


def answer_with(body: bytes, disposition: str | None = None) -> Callable[[http.server.BaseHTTPRequestHandler], None]:
    def respond(handler: http.server.BaseHTTPRequestHandler) -> None:
        handler.send_response(200)
        handler.send_header("Content-Length", str(len(body)))
        if disposition is not None:
            handler.send_header("Content-Disposition", disposition)
        handler.end_headers()
        handler.wfile.write(body)

    return respond


def test_fetch_export_sends_the_token_and_takes_the_suggested_name() -> None:
    respond = answer_with(b"date,requests\r\n", 'attachment; filename="claude-usage-daily-2026-01-02.csv"')
    with tiny_server(respond) as (port, seen):
        body, name = dashboard.fetch_export(f"http://127.0.0.1:{port}", "tok-1", "csv")
    assert body == b"date,requests\r\n" and name == "claude-usage-daily-2026-01-02.csv"
    assert seen == [{"path": "/api/export?format=csv", "token": "tok-1", "host": f"127.0.0.1:{port}"}]


@pytest.mark.parametrize("header", [
    'attachment; filename="../../evil.csv"', 'attachment; filename="claude-usage-daily-2026-01-02.exe"',
    'attachment; filename="C:\\\\Windows\\\\x.csv"', 'attachment; filename="claude-usage-daily-2026-1-2.csv"',
    "attachment", None,
])
def test_a_suggested_name_that_is_not_ours_is_replaced_by_one_made_here(header: str | None) -> None:
    with tiny_server(answer_with(b"x", header)) as (port, _seen):
        _body, name = dashboard.fetch_export(f"http://127.0.0.1:{port}", "tok", "json")
    assert name == f"claude-usage-daily-{datetime.now().date().isoformat()}.json"


def test_an_export_over_the_size_limit_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dashboard, "MAX_EXPORT_BYTES", 10)
    with tiny_server(answer_with(b"x" * 11)) as (port, _seen):
        with pytest.raises(ValueError, match="larger than"):
            dashboard.fetch_export(f"http://127.0.0.1:{port}", "tok", "csv")
    with tiny_server(answer_with(b"x" * 10)) as (port, _seen):
        assert dashboard.fetch_export(f"http://127.0.0.1:{port}", "tok", "csv")[0] == b"x" * 10


def test_a_redirect_is_never_followed_so_the_token_stays_on_the_monitor() -> None:
    with tiny_server(answer_with(b"elsewhere")) as (other_port, other_seen):
        def redirect(handler: http.server.BaseHTTPRequestHandler) -> None:
            handler.send_response(302)
            handler.send_header("Location", f"http://127.0.0.1:{other_port}/steal")
            handler.send_header("Content-Length", "0")
            handler.end_headers()

        with tiny_server(redirect) as (port, seen):
            base = f"http://127.0.0.1:{port}"
            with pytest.raises(urllib.error.HTTPError) as caught:
                dashboard.fetch_export(base, "tok", "csv")
            assert caught.value.code == 302
            with pytest.raises(urllib.error.HTTPError):
                dashboard._api_call(base, "tok", "GET", "/api/state")           # the settings calls are no different
            assert len(seen) == 2
    assert other_seen == []


def _areas(*boxes: tuple[int, int, int, int]) -> list[Any]:
    from usage_monitor.ui.platform import Rect

    return [Rect(*box) for box in boxes]


ONE_SCREEN = (0, 0, 3440, 1392)
TWO_SCREENS = [(0, 0, 1920, 1032), (1920, 0, 3840, 1080)]


@pytest.mark.parametrize("left, top, expected", [
    (100, 100, None),                  # comfortably on the screen
    (2240, 612, None),                 # flush with the bottom right corner
    (-1000, 100, None),                # mostly off to the left, but 200 px of the title bar can be grabbed
    (-4, 100, None),
    (100, -4, None),                   # the top edge may hang over a little
    (9000, 100, (2240, 100)),          # the monitor it was on is gone: pulled back to the right edge
    (-5000, 100, (0, 100)),
    (100, 5000, (100, 612)),
    (100, -200, (100, 0)),
    (-1100, 100, (0, 100)),            # only 100 px of the title bar left on screen
    (3400, 100, (2240, 100)),
    (100, 1380, (100, 612)),           # the title bar is under the taskbar
])
def test_a_window_whose_title_bar_is_off_screen_is_moved_onto_one(left: int, top: int,
                                                                  expected: tuple[int, int] | None) -> None:
    assert dashboard.on_screen_position(left, top, 1200, 780, _areas(ONE_SCREEN)) == expected


def test_on_screen_placement_picks_the_screen_it_is_closest_to() -> None:
    areas = _areas(*TWO_SCREENS)
    assert dashboard.on_screen_position(2000, 100, 1200, 780, areas) is None           # on the second screen
    assert dashboard.on_screen_position(9000, 100, 1200, 780, areas) == (2640, 100)    # nearest is the second
    assert dashboard.on_screen_position(-9000, 100, 1200, 780, areas) == (0, 100)      # nearest is the first
    assert dashboard.on_screen_position(1900, 100, 1200, 780, areas) is None           # straddling both: reachable


def test_on_screen_placement_does_nothing_without_screen_information() -> None:
    assert dashboard.on_screen_position(9000, 100, 1200, 780, []) is None
    assert dashboard.reachable(9000, 100, 1200, 780, []) is False


@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5, 2.0])
def test_on_screen_placement_is_the_same_at_every_display_scale(scale: float) -> None:
    """Window and screens are both physical pixels of the live process, so the display scale cancels."""
    width, height = round(1200 * scale), round(780 * scale)
    areas = _areas((0, 0, round(3440 * scale), round(1392 * scale)))
    assert dashboard.on_screen_position(round(100 * scale), round(100 * scale), width, height, areas) is None
    moved = dashboard.on_screen_position(round(9000 * scale), round(100 * scale), width, height, areas)
    assert moved == (round(3440 * scale) - width, round(100 * scale))


class _NativeWindow:
    """A pywebview window double: just enough for _native_handle."""

    def __init__(self, handle: int | None = 4242) -> None:
        handle_object = types.SimpleNamespace(ToInt64=lambda: handle) if handle is not None else None
        self.native = types.SimpleNamespace(Handle=handle_object) if handle is not None else None


def test_keep_on_screen_moves_an_unreachable_window_and_leaves_a_reachable_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    moves: list[tuple[int, int, int]] = []
    rect = {"value": (9000, 100, 10200, 880)}
    monkeypatch.setattr(dashboard, "_window_rect", lambda hwnd: rect["value"])
    monkeypatch.setattr(dashboard, "_screen_areas", lambda: _areas(ONE_SCREEN))
    monkeypatch.setattr(dashboard, "_move_window", lambda hwnd, x, y: moves.append((hwnd, x, y)) or True)

    assert dashboard.keep_on_screen(_NativeWindow()) is True
    assert moves == [(4242, 2240, 100)]
    rect["value"] = (100, 100, 1300, 880)
    assert dashboard.keep_on_screen(_NativeWindow()) is False
    assert len(moves) == 1


def test_keep_on_screen_never_raises_into_the_window_code(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(hwnd: int) -> None:
        raise OSError("no such window")

    monkeypatch.setattr(dashboard, "_window_rect", broken)
    assert dashboard.keep_on_screen(_NativeWindow()) is False
    assert dashboard.keep_on_screen(_NativeWindow(handle=None)) is False        # the window does not exist yet


def test_dashboard_main_checks_the_position_before_the_window_is_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    checked: list[Any] = []

    class Window:
        def __init__(self) -> None:
            self.events = FakeEvents()
            self.native = None
            self.x, self.y, self.width, self.height = 9000, 100, 1200, 780

    window = Window()
    fake = types.ModuleType("webview")
    fake.create_window = lambda *a, **k: window  # type: ignore[attr-defined]
    fake.start = lambda: [handler() for handler in window.events.before_show] and None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setenv(dashboard.TOKEN_ENV, "tok")
    monkeypatch.setattr(dashboard, "_saved_bounds", lambda base, token: {"x": 9000, "y": 100, "w": 1200, "h": 780})
    monkeypatch.setattr(dashboard, "_save_bounds", lambda *a: None)
    monkeypatch.setattr(dashboard, "keep_on_screen", lambda w: checked.append(w) or True)
    assert dashboard.dashboard_main(["--dashboard", "--port", "5127"]) == 0
    assert checked == [window]


def test_dashboard_main_copes_with_a_pywebview_that_has_no_before_show(monkeypatch: pytest.MonkeyPatch) -> None:
    class Events:
        def __init__(self) -> None:
            self.moved, self.resized, self.closing, self.closed = (FakeEvent() for _ in range(4))

    class Window:
        events = Events()
        native = None
        x = y = 0
        width, height = 1200, 780

    fake = types.ModuleType("webview")
    fake.create_window = lambda *a, **k: Window()  # type: ignore[attr-defined]
    fake.start = lambda: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setenv(dashboard.TOKEN_ENV, "tok")
    monkeypatch.setattr(dashboard, "_saved_bounds", lambda base, token: None)
    assert dashboard.dashboard_main(["--dashboard", "--port", "5128"]) == 0


def test_geometry_uses_saved_bounds_or_the_default_size() -> None:
    assert dashboard._geometry(None) == {"x": None, "y": None, "width": 1200, "height": 780}
    saved = {"x": 10, "y": 20, "w": 1400, "h": 900}
    assert dashboard._geometry(saved) == {"x": 10, "y": 20, "width": 1400, "height": 900}
    assert dashboard._geometry({"x": 1}) == {"x": None, "y": None, "width": 1200, "height": 780}


class FakePopen:
    instances: list[FakePopen] = []

    def __init__(self, command: list[str], **kwargs: Any) -> None:
        self.command = command
        self.kwargs = kwargs
        self.pid = 4000 + len(FakePopen.instances)
        self._returncode: int | None = None
        FakePopen.instances.append(self)

    def poll(self) -> int | None:
        return self._returncode

    def terminate(self) -> None:
        self._returncode = 1

    def kill(self) -> None:
        self._returncode = 1

    def wait(self, timeout: float | None = None) -> int | None:
        return self._returncode


class HandledPopen(FakePopen):
    """A FakePopen that has a process handle, like a real Popen on Windows."""

    instances: list[FakePopen] = []

    def __init__(self, command: list[str], **kwargs: Any) -> None:
        super().__init__(command, **kwargs)
        self._handle = 0x1000 + len(HandledPopen.instances)
        HandledPopen.instances.append(self)


@pytest.fixture
def job_calls(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Replace the job object calls with recorders, so no test assigns a real process to a real job."""
    from usage_monitor.ui import win32

    calls: list[Any] = []
    monkeypatch.setattr(win32, "create_kill_on_close_job", lambda: calls.append("create") or 777)
    monkeypatch.setattr(win32, "assign_to_job", lambda job, process: calls.append(("assign", job, process)))
    monkeypatch.setattr(win32, "CloseHandle", lambda handle: calls.append(("close", handle)) or True)
    return calls


def test_launcher_starts_one_child_and_focuses_it_while_alive(make_server: Any, settings: Settings,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    FakePopen.instances = []
    monkeypatch.setattr(dashboard.subprocess, "Popen", FakePopen)
    focused: list[int] = []
    monkeypatch.setattr(dashboard, "focus_window", lambda pid: focused.append(pid) or True)
    server, _ = make_server()
    launcher = dashboard.DashboardLauncher(server, settings)

    assert launcher.open() is True
    assert len(FakePopen.instances) == 1
    child = FakePopen.instances[0]
    assert child.kwargs["env"][dashboard.TOKEN_ENV] == server.token
    assert child.command[-2:] == ["--port", str(server.port)]
    assert "--dashboard" in child.command
    assert launcher.running is True

    assert launcher.open() is True
    assert len(FakePopen.instances) == 1
    assert focused == [child.pid]

    child._returncode = 0  # the window was closed
    assert launcher.running is False
    assert launcher.open() is True
    assert len(FakePopen.instances) == 2


def test_the_dashboard_child_goes_into_a_kill_on_close_job_owned_by_the_monitor(
    make_server: Any, settings: Settings, monkeypatch: pytest.MonkeyPatch, job_calls: list[Any],
) -> None:
    """End task, taskkill /F or a crash of the monitor used to leave the dashboard window open on a dead API."""
    HandledPopen.instances = []
    FakePopen.instances = []
    monkeypatch.setattr(dashboard.subprocess, "Popen", HandledPopen)
    monkeypatch.setattr(dashboard, "focus_window", lambda pid: True)
    server, _ = make_server()
    launcher = dashboard.DashboardLauncher(server, settings)

    assert launcher.open() is True
    first = HandledPopen.instances[0]
    assert job_calls == ["create", ("assign", 777, first._handle)]

    first._returncode = 0                                  # the window was closed; a new one is started
    assert launcher.open() is True
    second = HandledPopen.instances[1]
    assert job_calls[2:] == [("assign", 777, second._handle)]   # the same job, no second job

    launcher.close()                                       # clean shutdown: the job goes too
    assert job_calls[-1] == ("close", 777)
    assert launcher.running is False
    launcher.close()                                       # closing twice is harmless
    assert job_calls.count(("close", 777)) == 1


def test_the_launcher_reports_the_childs_exit_code(make_server: Any, settings: Settings,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    FakePopen.instances = []
    monkeypatch.setattr(dashboard.subprocess, "Popen", FakePopen)
    server, _ = make_server()
    launcher = dashboard.DashboardLauncher(server, settings)
    assert launcher.exit_code is None                       # nothing was started
    assert launcher.open() is True
    assert launcher.exit_code is None                       # still running
    FakePopen.instances[0]._returncode = dashboard.EXIT_BROWSER_FALLBACK
    assert launcher.exit_code == dashboard.EXIT_BROWSER_FALLBACK and launcher.running is False


def test_a_dashboard_that_cannot_join_the_job_still_opens(
    make_server: Any, settings: Settings, monkeypatch: pytest.MonkeyPatch, job_calls: list[Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    from usage_monitor.ui import win32

    def refuse(job: int, process: int) -> None:
        raise OSError("access denied")

    HandledPopen.instances = []
    FakePopen.instances = []
    monkeypatch.setattr(dashboard.subprocess, "Popen", HandledPopen)
    monkeypatch.setattr(win32, "assign_to_job", refuse)
    server, _ = make_server()
    launcher = dashboard.DashboardLauncher(server, settings)
    with caplog.at_level("WARNING", logger="usage_monitor.ui.dashboard"):
        assert launcher.open() is True
    assert launcher.running is True and "may outlive a crash" in caplog.text


def test_a_process_double_without_a_handle_is_never_put_in_a_job(
    make_server: Any, settings: Settings, monkeypatch: pytest.MonkeyPatch, job_calls: list[Any],
) -> None:
    """A made-up pid must never reach AssignProcessToJobObject: the job would kill a stranger on close."""
    FakePopen.instances = []
    monkeypatch.setattr(dashboard.subprocess, "Popen", FakePopen)
    server, _ = make_server()
    assert dashboard.DashboardLauncher(server, settings).open() is True
    assert job_calls == []


def test_the_job_structures_match_the_windows_layout() -> None:
    import ctypes

    from usage_monitor.ui import win32

    pointer = ctypes.sizeof(ctypes.c_void_p)
    assert ctypes.sizeof(win32.IO_COUNTERS) == 48
    assert ctypes.sizeof(win32.JOBOBJECT_BASIC_LIMIT_INFORMATION) == (64 if pointer == 8 else 48)
    assert ctypes.sizeof(win32.JOBOBJECT_EXTENDED_LIMIT_INFORMATION) == (144 if pointer == 8 else 112)
    assert win32.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE == 0x2000 and win32.JobObjectExtendedLimitInformation == 9


@ui_only
def test_a_real_job_ends_its_process_when_the_last_handle_closes() -> None:
    from usage_monitor.ui import win32

    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        job = win32.create_kill_on_close_job()
        win32.assign_to_job(job, int(sleeper._handle))  # type: ignore[attr-defined]
        assert sleeper.poll() is None
        win32.CloseHandle(job)
        assert sleeper.wait(timeout=10) is not None
    finally:
        sleeper.kill()
        sleeper.wait(timeout=10)


_PARENT = """
import sys, time, types
sys.path.insert(0, {repo!r})
from usage_monitor.ui import dashboard
dashboard.build_command = lambda port, **_kw: [sys.executable, "-c", "import time; time.sleep(120)"]
launcher = dashboard.DashboardLauncher(types.SimpleNamespace(token="t", port=1), None)
assert launcher.open()
print(launcher.pid, flush=True)
time.sleep(120)
"""


def _pid_alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, check=False).stdout
    return f'"{pid}"' in out


@ui_only
def test_the_dashboard_child_dies_when_the_monitor_is_killed(tmp_path: Path) -> None:
    """The real launcher, a stand-in child, and a hard kill of the parent (TerminateProcess is what
    End task and taskkill /F do). Nothing gets to tear down, so only the job can end the child."""
    repo = str(Path(__file__).resolve().parents[1])
    parent = subprocess.Popen([sys.executable, "-c", _PARENT.format(repo=repo)], stdout=subprocess.PIPE,
                              env=dict(os.environ, **REAL_PROFILE_ENV), cwd=repo)
    child_pid = 0
    try:
        assert parent.stdout is not None
        child_pid = int(parent.stdout.readline().decode("ascii").strip())
        assert _pid_alive(child_pid)
        parent.kill()
        parent.wait(timeout=10)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and _pid_alive(child_pid):
            time.sleep(0.2)
        assert not _pid_alive(child_pid), "the dashboard child outlived a killed monitor"
    finally:
        parent.kill()
        if child_pid:
            subprocess.run(["taskkill", "/F", "/PID", str(child_pid)], capture_output=True, check=False)


def test_launcher_reports_failure_when_the_process_cannot_start(make_server: Any, settings: Settings,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise OSError("no such file")

    monkeypatch.setattr(dashboard.subprocess, "Popen", refuse)
    server, _ = make_server()
    assert dashboard.DashboardLauncher(server, settings).open() is False


def test_dashboard_main_without_token_or_port_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(dashboard.TOKEN_ENV, raising=False)
    assert dashboard.dashboard_main(["--dashboard", "--port", "5000"]) == 2
    monkeypatch.setenv(dashboard.TOKEN_ENV, "t")
    assert dashboard.dashboard_main(["--dashboard"]) == 2


def test_dashboard_main_falls_back_to_the_browser_without_pywebview(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(dashboard.TOKEN_ENV, "tok")
    monkeypatch.setitem(sys.modules, "webview", None)  # importing it raises ImportError
    monkeypatch.setattr(dashboard, "_saved_bounds", lambda base, token: None)
    opened: list[str] = []
    monkeypatch.setattr(dashboard.webbrowser, "open", lambda url: opened.append(url) or True)
    assert dashboard.dashboard_main(["--dashboard", "--port", "5123"]) == dashboard.EXIT_BROWSER_FALLBACK
    assert opened == ["http://127.0.0.1:5123/"]


class FakeEvent(list):
    """Stands in for pywebview's Event: += adds a handler and returns the same object."""

    def __iadd__(self, handler: Any) -> "FakeEvent":
        self.append(handler)
        return self


class FakeEvents:
    def __init__(self) -> None:
        self.moved = FakeEvent()
        self.resized = FakeEvent()
        self.closing = FakeEvent()
        self.closed = FakeEvent()
        self.before_show = FakeEvent()


def test_dashboard_main_falls_back_when_the_webview_will_not_start(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeWindow:
        def __init__(self) -> None:
            self.events = FakeEvents()
            self.native = None
            self.x, self.y, self.width, self.height = 1, 2, 1200, 780

    fake = types.ModuleType("webview")
    window = FakeWindow()
    fake.create_window = lambda *args, **kwargs: window  # type: ignore[attr-defined]

    def no_webview2() -> None:
        raise RuntimeError("WebView2 runtime missing")

    fake.start = no_webview2  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setenv(dashboard.TOKEN_ENV, "tok")
    monkeypatch.setattr(dashboard, "_saved_bounds", lambda base, token: {"x": 1, "y": 2, "w": 1200, "h": 780})
    opened: list[str] = []
    monkeypatch.setattr(dashboard.webbrowser, "open", lambda url: opened.append(url) or True)
    assert dashboard.dashboard_main(["--dashboard", "--port", "5124"]) == dashboard.EXIT_BROWSER_FALLBACK
    assert opened == ["http://127.0.0.1:5124/"]


def test_dashboard_main_reports_a_failing_window_call_as_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A TypeError from our own create_window call is a bug, not a missing WebView2: the page still
    opens in the browser, but the exit code must not look like the harmless fallback."""
    fake = types.ModuleType("webview")

    def broken_call(*args: Any, **kwargs: Any) -> None:
        raise TypeError("create_window() got an unexpected keyword argument 'min_size'")

    fake.create_window = broken_call  # type: ignore[attr-defined]
    fake.start = lambda: pytest.fail("a window that was never created cannot be started")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setenv(dashboard.TOKEN_ENV, "tok")
    monkeypatch.setattr(dashboard, "_saved_bounds", lambda base, token: None)
    opened: list[str] = []
    monkeypatch.setattr(dashboard.webbrowser, "open", lambda url: opened.append(url) or True)
    code = dashboard.dashboard_main(["--dashboard", "--port", "5126"])
    assert code == dashboard.EXIT_WINDOW_ERROR
    assert code not in (0, dashboard.EXIT_BROWSER_FALLBACK, dashboard.EXIT_NO_ARGS)
    assert opened == ["http://127.0.0.1:5126/"]


def test_the_exit_codes_tell_the_cases_apart(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(dashboard.TOKEN_ENV, raising=False)
    codes = {dashboard.EXIT_WINDOW_ERROR, dashboard.EXIT_NO_ARGS, dashboard.EXIT_BROWSER_FALLBACK, 0}
    assert len(codes) == 4
    assert dashboard.dashboard_main(["--dashboard", "--port", "5000"]) == dashboard.EXIT_NO_ARGS


def test_dashboard_saves_bounds_once_on_close(monkeypatch: pytest.MonkeyPatch) -> None:
    saved: list[tuple[str, Any]] = []

    class Window:
        def __init__(self) -> None:
            self.events = FakeEvents()
            self.native = None
            self.x, self.y, self.width, self.height = 100, 50, 1300, 800

    window = Window()
    fake = types.ModuleType("webview")
    fake.create_window = lambda *a, **k: window  # type: ignore[attr-defined]

    def run() -> None:
        # The user resizes, then closes: each event handler gets the window's current geometry.
        window.width, window.height = 1400, 900
        for handler in window.events.resized:
            handler()
        for handler in window.events.closing:
            handler()
        for handler in window.events.closed:
            handler()

    fake.start = run  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setenv(dashboard.TOKEN_ENV, "tok")
    monkeypatch.setattr(dashboard, "_saved_bounds", lambda base, token: None)
    monkeypatch.setattr(dashboard, "_save_bounds", lambda base, token, bounds: saved.append((token, bounds)))
    assert dashboard.dashboard_main(["--dashboard", "--port", "5125"]) == 0
    assert saved == [("tok", {"x": 100, "y": 50, "w": 1400, "h": 900})]


@ui_only
def test_dashboard_child_opens_a_window_and_stays_up(tmp_path: Path, make_server: Any) -> None:
    """Starts the real pywebview child against a live API and checks that its window stays open."""
    server, _ = make_server()
    env = dict(os.environ, **REAL_PROFILE_ENV, CUM_DASHBOARD_TOKEN=server.token, CUM_APPDATA_DIR=str(tmp_path / "app"))
    repo = str(Path(__file__).resolve().parents[1])
    child = subprocess.Popen([sys.executable, "-m", "usage_monitor.ui.dashboard", "--port", str(server.port)],
                             env=env, cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        time.sleep(6)
        code = child.poll()
        if code == dashboard.EXIT_BROWSER_FALLBACK:
            detail = child.stderr.read()[-400:].decode("utf-8", "replace") if child.stderr else ""
            pytest.skip("the child fell back to the browser: pywebview or WebView2 is not available here " + detail)
        assert code is None, f"the dashboard exited with code {code}: " + (child.stderr.read()[-800:] if child.stderr else b"").decode("utf-8", "replace")
    finally:
        child.terminate()
        child.wait(timeout=10)


@ui_only
def test_a_dashboard_saved_off_screen_opens_on_a_screen(tmp_path: Path, make_server: Any, settings: Settings) -> None:
    """The real pywebview child with a saved position on a monitor that no longer exists (x = 9000)."""
    from usage_monitor.ui import win32

    settings.update({"dashboard_bounds": {"x": 9000, "y": 100, "w": 1200, "h": 780}})
    server, _ = make_server()
    env = dict(os.environ, **REAL_PROFILE_ENV, CUM_DASHBOARD_TOKEN=server.token, CUM_APPDATA_DIR=str(tmp_path / "app"))
    repo = str(Path(__file__).resolve().parents[1])
    child = subprocess.Popen([sys.executable, "-m", "usage_monitor.ui.dashboard", "--port", str(server.port)],
                             env=env, cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        hwnd = None
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline and hwnd is None and child.poll() is None:
            time.sleep(0.5)
            hwnd = dashboard._find_window_of(child.pid)
        if child.poll() == dashboard.EXIT_BROWSER_FALLBACK:
            pytest.skip("the dashboard fell back to the browser: pywebview or WebView2 is not available here")
        assert hwnd is not None, f"no dashboard window (exit code {child.poll()})"
        time.sleep(1.0)                                    # let the placement settle
        # Asks Windows itself, so the answer does not depend on how this process scales its coordinates.
        assert win32.MonitorFromWindow(hwnd, win32.MONITOR_DEFAULTTONULL), "the window opened off every screen"
    finally:
        child.terminate()
        child.wait(timeout=10)


@ui_only
def test_launcher_focuses_the_open_dashboard_instead_of_starting_another(make_server: Any, settings: Settings,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    server, _ = make_server()
    for key, value in REAL_PROFILE_ENV.items():  # the launcher's child inherits this environment
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(dashboard, "build_command", lambda port, **_kw: [
        sys.executable, "-m", "usage_monitor.ui.dashboard", "--port", str(port)])
    launcher = dashboard.DashboardLauncher(server, settings)
    try:
        assert launcher.open() is True
        first = launcher.pid
        assert first is not None
        deadline = time.monotonic() + 20
        focused = False
        while time.monotonic() < deadline and launcher.running and not focused:
            time.sleep(0.5)
            focused = dashboard.focus_window(first)
        if not launcher.running:
            if launcher.exit_code == dashboard.EXIT_BROWSER_FALLBACK:
                pytest.skip("the dashboard fell back to the browser: pywebview or WebView2 is not available here")
            pytest.fail(f"the dashboard process exited with code {launcher.exit_code}")
        assert focused, "the dashboard window was not found by its process id"
        assert launcher.open() is True
        assert launcher.pid == first  # the second open focused the window; it did not start a second one
    finally:
        launcher.close()
    assert launcher.running is False


# ── Overview layout in the real web view ──────────────────────────────────────────

# Runs in a child process: pywebview must own its main thread. The window is parked far off screen, so
# nothing flashes up, and it is resized through the (outer) sizes in argv[2], like DEFAULT_SIZE.
_LAYOUT_PROBE = r"""
import json, sys, time
import webview

URL, SIZES = sys.argv[1], json.loads(sys.argv[2])
MEASURE = '''(() => {
  const cards = [...document.querySelectorAll('.nm-grid > .nm-card')].map(card => {
    const inner = card.querySelector('.nm-card-in');
    const tile = card.querySelector('.nm-tile .nm-big');
    return {name: card.id || card.getAttribute('data-limit') || card.getAttribute('data-card') || '?',
            over: inner.scrollHeight - inner.clientHeight,
            tiles: tile ? +(inner.getBoundingClientRect().bottom - tile.getBoundingClientRect().bottom).toFixed(2) : null};
  });
  const plan = [...document.querySelectorAll('#cost .nm-plan .nm-facts span')].map(span => span.textContent);
  return JSON.stringify({vw: innerWidth, vh: innerHeight, cards: cards, plan: plan});
})()'''
results = []

def probe(window):
    try:
        for _ in range(150):
            if window.evaluate_js("!!document.querySelector('#cost .nm-tile')"):
                break
            time.sleep(0.2)
        for width, height in SIZES:
            window.resize(width, height)
            time.sleep(1.0)
            results.append(json.loads(window.evaluate_js(MEASURE)))
    finally:
        print(json.dumps(results), flush=True)
        window.destroy()

window = webview.create_window("layout probe", URL, width=SIZES[0][0], height=SIZES[0][1], x=-30000, y=-30000)
try:
    webview.start(probe, window)
except Exception as exc:
    print("no web view here:", type(exc).__name__, file=sys.stderr)
    sys.exit(3)
"""


@ui_only
@pytest.mark.parametrize("theme", ["nostromo/amber", "night_city/edgerun", "gates/blue-light"])
def test_overview_cards_never_clip_their_content_in_the_real_web_view(tmp_path: Path, theme: str) -> None:
    """The cost card used to cut the numerals of its two tiles off in the default window (a 1200 x 780 outer
    size leaves a 1184 x 741 page): the plan block had a line more than the approved design, and the cost
    row had a fixed floor below its content. Measured in WebView2, the engine the dashboard really uses,
    for each theme (the page opens in the theme's own look, see _themed_shell)."""
    server, settings = devserver.make_server("normal", 0, settings_dir=tmp_path / "dev", theme=theme)
    server.start()
    try:
        env = dict(os.environ, **REAL_PROFILE_ENV, CUM_APPDATA_DIR=str(tmp_path / "app"))
        sizes = [[1200, 780], [1200, 700], [1100, 780], [1060, 640]]
        run = subprocess.run([sys.executable, "-c", _LAYOUT_PROBE, server.url, json.dumps(sizes)], env=env,
                             cwd=str(Path(__file__).resolve().parents[1]), capture_output=True, text=True,
                             timeout=120, check=False)
    finally:
        server.stop()
        settings.close()
    if run.returncode == 3:
        pytest.skip("pywebview or WebView2 is not available here")
    assert run.returncode == 0, run.stderr[-800:]
    measured = json.loads(run.stdout.strip().splitlines()[-1])
    assert len(measured) == len(sizes)
    assert [line.upper() for line in measured[0]["plan"]] == ["YOUR PLAN", "MAX 5X · $100 / MONTH"]   # two lines
    for size, result in zip(sizes, measured):
        assert result["vw"] > 1000, f"{size}: the page is in the one column layout"
        clipped = {card["name"]: card["over"] for card in result["cards"] if card["over"] > 0}
        assert not clipped, f"{size} (page {result['vw']} x {result['vh']}): cards cut their content off: {clipped}"
        cost = next(card for card in result["cards"] if card["name"] == "cost")
        assert cost["tiles"] is not None and cost["tiles"] >= 4, f"{size}: the tile numerals touch the card edge"


# ── Dev server and the fake state ──────────────────────────────────────────────────

@pytest.mark.parametrize("scenario, status, session_pct, severity", [
    ("normal", m.ApiStatus.LIVE, 13.0, "normal"),
    ("warning", m.ApiStatus.LIVE, 72.0, "warning"),
    ("critical", m.ApiStatus.LIVE, 93.0, "critical"),
    ("stale", m.ApiStatus.STALE, 13.0, "normal"),
])
def test_fake_state_scenarios(scenario: str, status: m.ApiStatus, session_pct: float, severity: str) -> None:
    state = devserver.fake_state(scenario, NOW)
    assert state.status is status
    session = state.usage.session  # type: ignore[union-attr]
    assert session.percent == session_pct and session.severity == severity
    assert state.plan.name == "Max 5x" and state.plan.monthly_price_usd == 100.0
    month = state.windows["month"].totals
    assert month.cost_usd == pytest.approx(1659.14, abs=0.05)
    assert month.cache_hit_rate == pytest.approx(0.964, abs=0.001)
    assert month.thinking / month.output == pytest.approx(0.38, abs=0.001)
    assert sum(t.cost_usd for t in state.windows["month"].by_model.values()) == pytest.approx(month.cost_usd)
    assert state.windows["session"].totals.cost_usd == pytest.approx(31.07)


def test_fake_state_rejects_unknown_scenarios() -> None:
    with pytest.raises(ValueError):
        devserver.fake_state("sunny", NOW)


def test_devserver_serves_the_real_api_with_a_throwaway_settings_file(tmp_path: Path,
                                                                    themes_module: types.ModuleType) -> None:
    server, settings = devserver.make_server("critical", 0, settings_dir=tmp_path / "dev")
    server.start()
    try:
        payload = json.loads(request(server, "GET", "/api/state")[2])
        assert payload["state"]["usage"]["limits"][0]["percent"] == 93.0
        assert payload["dev"] is True
        assert payload["state"]["unpriced_models"] == ["claude-opus-6-preview"]
        assert request(server, "POST", "/api/settings", body=b'{"reduce_motion": true}')[0] == 200
        settings.close()  # flush the debounced save before checking the file
        assert (tmp_path / "dev" / "devserver-settings.json").exists()
    finally:
        server.stop()
        settings.close()


def test_the_devserver_never_touches_the_run_entry(tmp_path: Path, registry: dict[str, Any]) -> None:
    """The dev server is a dev ApiServer, so its "Start with Windows" switch needs no patching of the
    startup module (devserver used to replace startup.set_enabled in main() only)."""
    server, settings = devserver.make_server("normal", 0, settings_dir=tmp_path / "dev")
    server.start()
    try:
        assert post_json(server, "/api/settings", {"launch_on_startup": False})[0] == 200
        assert post_json(server, "/api/settings", {"launch_on_startup": True})[0] == 200
        state = json.loads(request(server, "GET", "/api/state")[2])
        assert state["dev"] is True and state["settings"]["launch_on_startup"] is True
    finally:
        server.stop()
        settings.close()
    assert registry["reads"] == 0 and registry["writes"] == [] and registry["deletes"] == 0


@ui_only
def test_devserver_process_answers_on_loopback(tmp_path: Path) -> None:
    """Starts the real dev server as a child process, as a reviewer would, and calls it."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = dict(os.environ, **REAL_PROFILE_ENV, CUM_APPDATA_DIR=str(tmp_path / "dev"))
    child = subprocess.Popen([sys.executable, "-m", "usage_monitor.ui.devserver", "--port", str(port),
                              "--scenario", "warning"], env=env, cwd=str(Path(__file__).resolve().parents[1]),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 20
        page = b""
        while time.monotonic() < deadline:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                conn.request("GET", "/")
                page = conn.getresponse().read()
                conn.close()
                break
            except OSError:
                time.sleep(0.2)
        assert b'name="monitor-token" content="' in page
        token = page.decode("utf-8").split('name="monitor-token" content="', 1)[1].split('"', 1)[0]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/state", headers={TOKEN_HEADER: token})
        response = conn.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["state"]["status"] == "live"
    finally:
        child.terminate()
        child.wait(timeout=10)
