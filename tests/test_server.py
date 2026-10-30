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
import queue
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
import urllib.parse
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from usage_monitor.agent import guard as agent_guard
from usage_monitor.agent import sessions as agent_sessions
from usage_monitor.agent import setup as agent_setup
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
# Every console program a test starts (python, tasklist, taskkill) gets this flag: pytest may have no console of its own
# (an editor, a task runner, an agent), and then each such process would open a terminal window on the desktop.
# test_update_check.py has the test that keeps every test file honest about it.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
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
ORIGINAL_READ_APPROVAL = startup._read_approval
ORIGINAL_CLEAR_APPROVAL = startup._clear_approval
ORIGINAL_RUNTIME_VERSIONS = dashboard._registered_runtime_versions


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
    # The Run value's companion record (StartupApproved) is Windows-owned: no test reads or clears the real one.
    monkeypatch.setattr(startup, "_read_approval", lambda: None)
    monkeypatch.setattr(startup, "_clear_approval", lambda: None)
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
        on_the_wire = f"/?k={server.token}" if token and path == "/" else path     # the page opens with the key
        conn.putrequest(method, on_the_wire, skip_host=host is not None, skip_accept_encoding=True)
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
    assert server.url == f"http://127.0.0.1:{server.port}/?k={server.token}"     # where to open the page
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


def test_the_url_the_server_hands_out_opens_the_page(make_server: Any) -> None:
    server, _ = make_server()
    target = server.url.split(f":{server.port}", 1)[1]                  # "/?k=<token>"
    assert target == f"/?k={server.token}"
    status, _headers, body = request(server, "GET", target)
    assert status == 200 and f'<meta name="monitor-token" content="{server.token}">' in body.decode("utf-8")
    quoted = urllib.parse.quote(server.token, safe="")
    assert request(server, "GET", f"/?k={quoted}")[0] == 200            # a percent-encoded key is the same key
    assert request(server, "GET", f"/?x=1&k={server.token}&y=2")[0] == 200


@pytest.mark.parametrize("query", [
    "", "?", "?k=", "?k=wrong", "?key={token}", "?K={token}", "?k={token}x", "?k=x{token}", "?k={cut}",
    "?k={token}&k={token}", "?k=&k={token}", "?k={token}&k=wrong", "?k=%00", "?k={token}%00", "?{token}", "#k={token}",
])
def test_the_page_is_not_served_without_the_key(make_server: Any, query: str) -> None:
    """GET / writes the per-run token into the page. Anyone on the machine can reach the port, another account or a
    terminal-server session included, so the page opens only for a caller that already knows the token."""
    server, _ = make_server()
    path = "/" + query.format(token=server.token, cut=server.token[:-1])
    status, headers, body = request(server, "GET", path, token=False)
    assert status == 401
    assert headers["content-type"].startswith("text/plain")
    assert server.token.encode("ascii") not in body and server.token not in "".join(headers.values())


def test_nothing_a_caller_without_the_token_can_fetch_contains_it(make_server: Any) -> None:
    server, _ = make_server()
    for path in ("/", "/?k=nope", "/static/app.js", "/static/index.html", "/static/themes/nostromo.css",
                 "/nothing-here", "/api/state", "/api/themes", "/api/analytics/cost", "/api/export?format=csv",
                 "/static/../index.html"):
        status, headers, body = request(server, "GET", path, token=False)
        assert status in (200, 401, 404), path
        assert server.token.encode("ascii") not in body, path
        assert server.token not in "".join(headers.values()), path


def test_the_key_check_comes_after_the_host_and_method_checks(make_server: Any) -> None:
    server, _ = make_server()
    assert request(server, "GET", "/", token=False, host="evil.example:80")[0] == 403
    assert request(server, "POST", "/", token=False, body=b"{}")[0] == 405
    assert request(server, "GET", f"/?k={server.token}", host="evil.example:80")[0] == 403


def test_the_dashboard_process_opens_the_page_with_the_key_from_its_environment(
        make_server: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end: the address dashboard_main hands the window is one the real server serves the page for."""
    server, _ = make_server()
    created = install_fake_webview(monkeypatch, FakeWindow())
    monkeypatch.setenv(dashboard.TOKEN_ENV, server.token)
    assert dashboard.dashboard_main(["--dashboard", "--port", str(server.port)]) == 0
    opened = created[0]["args"][1]
    assert opened == server.url
    status, _headers, body = request(server, "GET", opened.split(f":{server.port}", 1)[1])
    assert status == 200 and server.token in body.decode("utf-8")


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
    assert payload["update"] == {"available": True, "version": "2.1.0", "url": None, "can_self_update": False,
                                 "skipped": False, "info": {"version": "2.1.0"}, "skip_version": ""}
    assert payload["settings"]["launch_on_startup"] is False


def test_state_survives_a_broken_update_info_getter(make_server: Any, themes_module: types.ModuleType) -> None:
    def broken() -> None:
        raise RuntimeError("network down")

    server, _ = make_server(update_cb={"info": broken})
    status, _, body = request(server, "GET", "/api/state")
    assert status == 200
    block = json.loads(body)["update"]
    assert block == NO_UPDATE                 # a complete block, not a half one: the page reads every key


# ── The update block of /api/state: what the dashboard's banner and Settings page read ───────────────────

UPDATE_KEYS = {"available", "version", "url", "can_self_update", "skipped", "info", "skip_version"}
RELEASE_PAGE = "https://github.com/Godimas101/claude-usage-monitor/releases/tag/v2.1.0"
NO_UPDATE = {"available": False, "version": None, "url": None, "can_self_update": False, "skipped": False,
             "info": None, "skip_version": ""}


def update_block(make_server: Any, info: Any) -> dict[str, Any]:
    """The ``update`` block of /api/state when the app's info callback answers ``info``."""
    server, _ = make_server(update_cb={"info": lambda: info})
    return json.loads(request(server, "GET", "/api/state")[2])["update"]


def test_the_update_block_carries_what_the_banner_needs(make_server: Any) -> None:
    info = {"version": "2.1.0", "tag": "v2.1.0", "url": RELEASE_PAGE, "can_self_update": True, "notes": "Faster.",
            "asset_url": "https://github.com/Godimas101/claude-usage-monitor/releases/download/v2.1.0/Setup.exe",
            "asset_digest": "sha256:" + "a" * 64}
    block = update_block(make_server, info)
    assert set(block) == UPDATE_KEYS
    assert block == {"available": True, "version": "2.1.0", "url": RELEASE_PAGE, "can_self_update": True,
                     "skipped": False, "info": info, "skip_version": ""}


def test_no_known_release_is_a_complete_block_of_nothing(make_server: Any) -> None:
    assert update_block(make_server, None) == NO_UPDATE
    server, _ = make_server(update_cb={})                    # the dev server and the tests that give no info callback
    assert json.loads(request(server, "GET", "/api/state")[2])["update"] == NO_UPDATE


def test_a_skipped_version_is_marked_skipped_and_a_newer_one_is_not(make_server: Any, settings: Settings) -> None:
    settings.set("skip_version", "2.1.0")
    skipped = update_block(make_server, {"version": "2.1.0", "url": RELEASE_PAGE})
    assert skipped["available"] is True and skipped["skipped"] is True and skipped["skip_version"] == "2.1.0"
    newer = update_block(make_server, {"version": "2.2.0", "url": RELEASE_PAGE})
    assert newer["available"] is True and newer["skipped"] is False and newer["skip_version"] == "2.1.0"
    # The banner shows when `available and not skipped`; Settings keeps its Install and Skip buttons while `available`.


@pytest.mark.parametrize("version", ["2.1.0<script>", "2.1.0 ", " 2.1.0", "x" * 41, "", ".1.0", "2.1.0\n", "2,1", 5, None, ["2.1.0"]])
def test_a_version_that_is_not_plain_is_not_passed_to_the_page(make_server: Any, settings: Settings, version: Any) -> None:
    if isinstance(version, str):
        settings.set("skip_version", version)                # even a skipped odd version is not "skipped": it is not a version
    block = update_block(make_server, {"version": version, "url": RELEASE_PAGE})
    assert block["version"] is None and block["skipped"] is False
    assert block["url"] == RELEASE_PAGE                      # the address is judged on its own


@pytest.mark.parametrize("version", ["2.1.0", "10.0.12", "3.0.0-rc.1", "2.1.0+build.5", "v2.1.0", "x" * 40])
def test_plain_versions_pass(version: str) -> None:
    assert server_mod.plain_version(version) == version


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "http://github.com/Godimas101/claude-usage-monitor/releases/tag/v2.1.0",
    "https://evil.example/Godimas101/claude-usage-monitor", "https://github.com.evil.example/x",
    "https://github.com@evil.example/x", "https://github.com/", "https://github.com/a b", "https://github.com/a\nb",
    "https://github.com/x?next=https://evil.example", "https://github.com/x#frag", "https://github.com/" + "a" * 301,
    "https://GITHUB.com/x", "//github.com/x", "", 5, None, ["https://github.com/x"],
])
def test_a_release_address_that_is_not_a_plain_github_page_is_not_passed_to_the_page(make_server: Any, url: Any) -> None:
    block = update_block(make_server, {"version": "2.1.0", "url": url})
    assert block["url"] is None and block["version"] == "2.1.0"


@pytest.mark.parametrize(("flag", "expected"), [(True, True), (False, False), ("yes", False), (1, False), (None, False)])
def test_only_a_real_true_means_the_update_installs_in_place(make_server: Any, flag: Any, expected: bool) -> None:
    assert update_block(make_server, {"version": "2.1.0", "can_self_update": flag})["can_self_update"] is expected


@pytest.mark.parametrize("info", [{}, "2.1.0", ["2.1.0"], 5, {"version": {"x": 1}}])
def test_an_info_that_is_not_a_release_still_gives_a_complete_block(make_server: Any, info: Any) -> None:
    block = update_block(make_server, info)
    assert set(block) == UPDATE_KEYS and block["version"] is None and block["url"] is None
    assert block["can_self_update"] is False and block["skipped"] is False


def test_the_update_block_changes_the_etag_when_a_release_is_found(make_server: Any) -> None:
    """The dashboard polls with If-None-Match: finding a release has to reach it as a new answer, not a 304."""
    found: list[Any] = [None]
    server, _ = make_server(update_cb={"info": lambda: found[0]})
    first = request(server, "GET", "/api/state")[1]["etag"]
    found[0] = {"version": "2.1.0", "url": RELEASE_PAGE}
    status, headers, _ = request(server, "GET", "/api/state", headers={"If-None-Match": first})
    assert status == 200 and headers["etag"] != first


def test_the_notified_version_is_the_apps_own_business(make_server: Any, settings: Settings) -> None:
    """The toast bookkeeping lives in the settings file, not in the API: the page can neither read nor write it."""
    server, _ = make_server()
    settings.set("notified_version", "2.1.0")
    state = json.loads(request(server, "GET", "/api/state")[2])
    assert "notified_version" not in state["settings"] and "notified_version" not in server_mod.SETTING_SPECS
    status, body = post_json(server, "/api/settings", {"notified_version": "9.9.9"})
    assert status == 400 and "unknown setting" in body["error"]
    assert settings.get("notified_version") == "2.1.0"


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


def test_a_refused_request_with_a_body_still_reads_its_reply(make_server: Any) -> None:
    """A wrong host or token is refused before the body is read. Windows resets a connection that closes with unread
    bytes in it, and the reset could reach the client before the 403 did (the v2.2 release rehearsal saw it), so the
    server reads what is left of the body first."""
    server, _ = make_server()
    body = b'{"pad": "' + b"x" * 60_000 + b'"}'
    for _ in range(10):
        assert request(server, "POST", "/api/settings", body=body, host="evil.example:80")[0] == 403
        assert request(server, "POST", "/api/settings", body=body, token=False)[0] == 401


def test_a_refused_request_leaves_a_kept_alive_connection_clean(make_server: Any) -> None:
    server, _ = make_server()
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    try:
        conn.request("POST", "/api/refresh", body=b'{"pad": "' + b"z" * 1000 + b'"}',
                     headers={"Content-Type": "application/json"})    # no token: refused before the body is read
        first = conn.getresponse()
        first.read()
        assert first.status == 401 and (first.getheader("Connection") or "").lower() != "close"
        sock = conn.sock
        conn.request("GET", "/api/state", headers={TOKEN_HEADER: server.token})
        second = conn.getresponse()
        second.read()
        assert second.status == 200 and conn.sock is sock   # the same connection, not a body read as a request
    finally:
        conn.close()


def test_a_refused_request_whose_body_never_comes_is_answered_and_closed(make_server: Any,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server_mod, "DRAIN_TIMEOUT_S", 0.2)
    server, _ = make_server()
    with socket.create_connection(("127.0.0.1", server.port), timeout=5) as sock:
        sock.sendall(b"POST /api/refresh HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nContent-Length: 1000\r\n\r\n{}" % server.port)
        started = time.monotonic()
        reply = b""
        while b"\r\n\r\n" not in reply:
            chunk = sock.recv(4096)
            if not chunk:
                break
            reply += chunk
    assert reply.startswith(b"HTTP/1.1 401")
    assert b"connection: close" in reply.lower()   # the stream is in an unknown state, so it is not kept
    assert time.monotonic() - started < 3


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
    ("guard_enabled", [True, False, 1, 0, "true", None, []]),
    *[(key, [None, 1, 50, 95, 100, 0, 101, -1, 1.5, 95.0, True, False, "95", [95], {}])
      for key in ("guard_session_cap", "guard_weekly_cap", "guard_scoped_cap")],
    ("guard_resume_stagger_s", [0, 1, 120, 600, 601, -1, 1.5, 120.0, True, False, "120", None, [120]]),
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


# ── The usage guard (#27): its settings, GET /api/guard and the two POSTs ────────────

HOLDING = {
    "state": "holding",
    "over": [{"name": "session", "label": "session", "percent": 96.0, "cap": 95, "resets_at": "2026-10-21T19:39:00Z"}],
    "resume_at": "2026-10-21T19:39:00Z",
    "policy": {"enabled": True, "session_cap": 95, "weekly_cap": 90, "scoped_cap": None, "stagger_s": 120},
}
DEAD_PID = 99_999_999                    # no process has this id, so agent/sessions.py treats a file of it as left over
SETUP_STATUS: dict[str, Any] = {
    "available": True, "reason": None, "manual": "docs/AGENT_HOOK.md", "exe": "C:/x/ClaudeUsageMonitor.exe",
    "settings_path": "C:/Users/t/.claude/settings.json", "errors": [],
    "hooks": {"state": "missing", "installed": 0, "total": 3, "events": {}}, "mcp": {"state": "missing"},
    "claude": {"found": True, "path": "C:/x/claude.exe", "version": "2.1.160", "min_version": "2.1.139", "supported": True},
}


class FakeSetup:
    """agent/setup.py's status, install and remove, recording their calls. Nothing here reads a real ~/.claude or runs claude."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.answers: dict[str, Any] = {
            "install": {"ok": True, "action": "install", "changed": ["Added the SessionStart hook."], "unchanged": [],
                        "backup": "C:/Users/t/.claude/settings.json.cum-backup-20261028-180509", "warnings": [],
                        "errors": [], "manual": None, "settings_path": SETUP_STATUS["settings_path"]},
            "remove": {"ok": False, "action": "remove", "changed": [], "unchanged": [], "backup": None, "warnings": [],
                       "errors": ["The Claude Code settings file is not valid JSON, so setup left it alone."],
                       "manual": None, "settings_path": SETUP_STATUS["settings_path"]},
        }

    def status(self) -> Any:
        self.calls.append("status")
        return SETUP_STATUS

    def act(self, action: str) -> Any:
        self.calls.append(action)
        return self.answers[action]

    def callbacks(self) -> dict[str, Callable[[], Any]]:
        return {"status": self.status, "install": lambda: self.act("install"), "remove": lambda: self.act("remove")}


def seed_sessions(folder: Path, guard: dict[str, Any], told: list[tuple[str, str]],
                  waiting: list[dict[str, Any]] | None = None) -> None:
    """The files agent/sessions.py documents: hook-seen.json for the sessions told of this episode, waiting/<pid>.json."""
    folder.mkdir(parents=True, exist_ok=True)
    seen = {sid: {"at": at, "steps": {}, "guard": {"episode": agent_guard.episode(guard), "told_at": at}}
            for sid, at in told}
    seen["session-of-an-older-episode"] = {"at": "2026-10-20T10:00:00Z", "steps": {},
                                          "guard": {"episode": "session@2026-10-20T14:00:00Z", "told_at": "2026-10-20T10:00:00Z"}}
    (folder / agent_sessions.SEEN_NAME).write_text(json.dumps(seen), encoding="utf-8")
    (folder / agent_sessions.WAITING_DIR).mkdir(exist_ok=True)
    for entry in waiting or []:
        (folder / agent_sessions.WAITING_DIR / f"{entry['pid']}.json").write_text(json.dumps(entry), encoding="utf-8")


@pytest.fixture
def guard_rig(make_server: Any, tmp_path: Path) -> types.SimpleNamespace:
    folder, fake, clock = tmp_path / "guard", FakeSetup(), FakeClock()
    server, rec = make_server(guard_dir=folder, setup_cb=fake.callbacks(), clock=clock)
    rec.state = dataclasses.replace(sample_state(), guard=HOLDING)
    return types.SimpleNamespace(server=server, rec=rec, folder=folder, setup=fake, clock=clock)


def get_guard(server: ApiServer) -> dict[str, Any]:
    status, _headers, body = request(server, "GET", "/api/guard")
    assert status == 200
    return json.loads(body)


def test_the_guard_settings_are_stored_and_reported(make_server: Any, settings: Settings) -> None:
    server, rec = make_server()
    defaults = json.loads(request(server, "GET", "/api/state")[2])["settings"]
    assert (defaults["guard_enabled"], defaults["guard_session_cap"], defaults["guard_weekly_cap"],
            defaults["guard_scoped_cap"], defaults["guard_resume_stagger_s"]) == (False, 95, 90, None, 120)
    payload = {"guard_enabled": True, "guard_session_cap": 80, "guard_weekly_cap": None, "guard_scoped_cap": 70,
               "guard_resume_stagger_s": 30}
    status, body = post_json(server, "/api/settings", payload)
    assert status == 200 and body["applied"] == payload and rec.applied[-1] == payload
    for key, value in payload.items():
        assert settings.get(key) == value and body["settings"][key] == value, key
    assert post_json(server, "/api/settings", {"guard_scoped_cap": 100, "guard_resume_stagger_s": 0})[0] == 200
    assert (settings.get("guard_scoped_cap"), settings.get("guard_resume_stagger_s")) == (100, 0)


@pytest.mark.parametrize("payload", [
    {"guard_enabled": "yes"}, {"guard_enabled": 1}, {"guard_enabled": None},
    {"guard_session_cap": 0}, {"guard_weekly_cap": 101}, {"guard_scoped_cap": -1}, {"guard_session_cap": 95.5},
    {"guard_weekly_cap": True}, {"guard_scoped_cap": "95"}, {"guard_session_cap": [95]},
    {"guard_resume_stagger_s": 601}, {"guard_resume_stagger_s": -1}, {"guard_resume_stagger_s": 1.5},
    {"guard_resume_stagger_s": True}, {"guard_resume_stagger_s": None}, {"guard_resume_stagger_s": "120"},
])
def test_a_bad_guard_value_is_a_400_and_applies_nothing(make_server: Any, settings: Settings, payload: dict[str, Any]) -> None:
    server, rec = make_server()
    status, body = post_json(server, "/api/settings", {"reduce_motion": True, **payload})
    assert status == 400 and next(iter(payload)) in body["error"]
    assert rec.applied == [] and settings.get("reduce_motion", False) is False and settings.get(next(iter(payload))) in (
        agent_guard.DEFAULTS.get(next(iter(payload))), None)


def test_the_guard_block_of_the_state_reaches_the_page(make_server: Any) -> None:
    server, rec = make_server()
    rec.state = dataclasses.replace(sample_state(), guard=HOLDING)
    assert json.loads(request(server, "GET", "/api/state")[2])["state"]["guard"] == HOLDING


def test_the_guard_endpoint_reports_the_state_the_sessions_told_and_waiting_and_the_setup(guard_rig: Any) -> None:
    told = [("11111111-aaaa", "2026-10-21T16:00:00Z"), ("22222222-bbbb", "2026-10-21T15:30:00Z")]
    here = {"pid": os.getpid(), "session_id": "33333333-cccc", "since": "2026-10-21T16:05:00Z", "cwd": "C:/Demo/atlas-web"}
    gone = {"pid": DEAD_PID, "session_id": "44444444-dddd", "since": "2026-10-21T15:00:00Z", "cwd": "C:/Demo/gone"}
    seed_sessions(guard_rig.folder, HOLDING, told, [here, gone])
    answer = get_guard(guard_rig.server)
    assert set(answer) == {"guard", "told", "waiting", "setup"}
    assert answer["guard"] == HOLDING
    assert answer["told"] == [{"session_id": "22222222-bbbb", "told_at": "2026-10-21T15:30:00Z"},
                              {"session_id": "11111111-aaaa", "told_at": "2026-10-21T16:00:00Z"}]    # this episode, oldest first
    assert answer["waiting"] == [here]                                                                 # a dead --wait counts for nothing
    assert answer["setup"] == SETUP_STATUS


def test_before_the_first_cycle_the_guard_is_empty_but_the_waiting_sessions_are_still_listed(guard_rig: Any) -> None:
    here = {"pid": os.getpid(), "session_id": None, "since": "2026-10-21T16:05:00Z", "cwd": None}
    seed_sessions(guard_rig.folder, HOLDING, [("11111111-aaaa", "2026-10-21T16:00:00Z")], [here])
    for state in (None, dataclasses.replace(sample_state(), guard={})):
        guard_rig.rec.state = state
        answer = get_guard(guard_rig.server)
        assert answer["guard"] == {} and answer["told"] == [] and answer["waiting"] == [here]


def test_with_no_session_files_the_lists_are_empty_and_nothing_is_created(guard_rig: Any) -> None:
    answer = get_guard(guard_rig.server)
    assert answer["told"] == [] and answer["waiting"] == [] and not guard_rig.folder.exists()


def test_the_guard_files_are_read_from_the_app_data_folder_unless_told_otherwise(make_server: Any, tmp_path: Path) -> None:
    server, rec = make_server(setup_cb=FakeSetup().callbacks())                   # no guard_dir: CUM_APPDATA_DIR, as the app has it
    rec.state = dataclasses.replace(sample_state(), guard=HOLDING)
    here = {"pid": os.getpid(), "session_id": "s", "since": "2026-10-21T16:05:00Z", "cwd": "C:/Demo"}
    seed_sessions(tmp_path / "app", HOLDING, [("11111111-aaaa", "2026-10-21T16:00:00Z")], [here])
    answer = get_guard(server)
    assert [entry["session_id"] for entry in answer["told"]] == ["11111111-aaaa"] and answer["waiting"] == [here]
    assert request(server, "POST", "/api/guard/resume")[0] == 200
    assert (tmp_path / "app" / agent_sessions.RESUME_NAME).is_file()


# ── The setup status is slow to compute, so it is kept ────────────────────────────────

def test_the_setup_status_is_kept_for_a_while_and_looked_at_again_after(guard_rig: Any) -> None:
    get_guard(guard_rig.server)
    get_guard(guard_rig.server)
    assert guard_rig.setup.calls == ["status"]                                    # the second poll did not run claude again
    guard_rig.clock.now += server_mod.SETUP_STATUS_TTL_S - 1
    get_guard(guard_rig.server)
    assert guard_rig.setup.calls == ["status"]
    guard_rig.clock.now += 2
    get_guard(guard_rig.server)
    assert guard_rig.setup.calls == ["status", "status"]


def test_a_closed_dashboard_window_makes_the_next_look_a_fresh_one(make_server: Any, tmp_path: Path) -> None:
    fake = FakeSetup()
    server, _rec = make_server(guard_dir=tmp_path / "guard", setup_cb=fake.callbacks(), clock=FakeClock(),
                               dashboard_closed_cb=lambda pid: True)
    get_guard(server)
    assert post_json(server, "/api/dashboard/closed", {"pid": 4321})[0] == 200
    get_guard(server)
    assert fake.calls == ["status", "status"]


@pytest.mark.parametrize("action", ["install", "remove"])
def test_a_setup_click_makes_the_next_look_a_fresh_one(guard_rig: Any, action: str) -> None:
    get_guard(guard_rig.server)
    assert post_json(guard_rig.server, "/api/guard/setup", {"action": action})[0] == 200
    get_guard(guard_rig.server)
    assert guard_rig.setup.calls == ["status", action, "status"]


def test_two_polls_at_once_share_one_look_at_the_setup(make_server: Any, tmp_path: Path) -> None:
    gate, entered, calls = threading.Event(), threading.Event(), []

    def slow() -> Any:
        calls.append("status")
        entered.set()
        gate.wait(10)
        return SETUP_STATUS

    server, rec = make_server(guard_dir=tmp_path / "guard", setup_cb={"status": slow})
    answers: list[dict[str, Any]] = []
    threads = [threading.Thread(target=lambda: answers.append(get_guard(server))) for _ in range(3)]
    for thread in threads:
        thread.start()
    assert entered.wait(10)
    time.sleep(0.2)                                                              # the others are waiting for that one answer
    assert calls == ["status"]
    gate.set()
    for thread in threads:
        thread.join(10)
    assert calls == ["status"] and len(answers) == 3 and all(a["setup"] == SETUP_STATUS for a in answers)


def test_a_click_while_the_status_is_being_computed_is_not_undone_by_it(make_server: Any, tmp_path: Path) -> None:
    """A look that began before an install may end after it: it must not be kept as the answer for the next minute."""
    gate, entered, state = threading.Event(), threading.Event(), {"hooks": "missing"}

    def status() -> Any:
        seen = dict(state)
        entered.set()
        gate.wait(10)
        return {**SETUP_STATUS, "hooks": seen}

    def install() -> Any:
        state["hooks"] = "installed"
        return {"ok": True}

    server, _ = make_server(guard_dir=tmp_path / "guard", setup_cb={"status": status, "install": install})
    first: list[dict[str, Any]] = []
    thread = threading.Thread(target=lambda: first.append(get_guard(server)))
    thread.start()
    assert entered.wait(10)
    entered.clear()
    clicked = threading.Thread(target=lambda: post_json(server, "/api/guard/setup", {"action": "install"}))
    clicked.start()
    clicked.join(10)
    gate.set()
    thread.join(10)
    assert first[0]["setup"]["hooks"] == {"hooks": "missing"}                    # that poll saw the old state
    assert get_guard(server)["setup"]["hooks"] == {"hooks": "installed"}         # and the next one looked again


def test_a_setup_status_that_fails_is_an_unavailable_status_not_an_error(make_server: Any, tmp_path: Path) -> None:
    def broken() -> Any:
        raise RuntimeError("C:/private/place")

    server, _ = make_server(guard_dir=tmp_path / "guard", setup_cb={"status": broken})
    status, _headers, body = request(server, "GET", "/api/guard")
    answer = json.loads(body)
    assert status == 200 and answer["setup"]["available"] is False and b"private" not in body
    assert answer["setup"] == agent_setup.unavailable_status("The setup status could not be read right now.")


def test_without_a_status_callback_setup_is_unavailable(make_server: Any, tmp_path: Path) -> None:
    server, _ = make_server(guard_dir=tmp_path / "guard", setup_cb={})
    assert get_guard(server)["setup"]["available"] is False
    status, body = post_json(server, "/api/guard/setup", {"action": "install"})
    assert status == 501 and "not available" in body["error"]


# ── POST /api/guard/resume ────────────────────────────────────────────────────────────

def test_resume_now_writes_the_marker_the_waiting_sessions_look_for(guard_rig: Any) -> None:
    started = time.time() - 1
    assert not agent_sessions.resumed_since(guard_rig.folder, started)
    status, body = post_json(guard_rig.server, "/api/guard/resume", {})
    assert status == 200 and body == {"ok": True}
    assert (guard_rig.folder / agent_sessions.RESUME_NAME).is_file()
    assert agent_sessions.resumed_since(guard_rig.folder, started)
    assert request(guard_rig.server, "POST", "/api/guard/resume")[0] == 200       # no body at all is fine too, and so is again


def test_resume_now_drains_whatever_body_it_is_sent_and_refuses_an_oversized_one(guard_rig: Any) -> None:
    assert request(guard_rig.server, "POST", "/api/guard/resume", body=b'{"anything": [1, 2, 3]}')[0] == 200
    status, headers, _ = request(guard_rig.server, "POST", "/api/guard/resume", body=b"x" * (64 * 1024 + 1))
    assert status == 413 and headers.get("connection", "").lower() == "close"


def test_resume_now_failing_is_a_500_without_details(guard_rig: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(folder: Path) -> None:
        raise OSError(5, "C:/private/place is read-only")

    monkeypatch.setattr(agent_sessions, "resume_now", refuse)
    status, _headers, body = request(guard_rig.server, "POST", "/api/guard/resume")
    assert status == 500 and json.loads(body) == {"error": "could not record the resume request"}


# ── POST /api/guard/setup ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("action", ["install", "remove"])
def test_setup_returns_the_result_of_setup_as_it_is(guard_rig: Any, action: str) -> None:
    status, body = post_json(guard_rig.server, "/api/guard/setup", {"action": action})
    assert status == 200 and body == guard_rig.setup.answers[action]               # a refusal (ok false) is still a 200
    assert guard_rig.setup.calls == [action]


@pytest.mark.parametrize("payload", [{}, {"action": "gate"}, {"action": "Install"}, {"action": ["install"]}, {"action": None},
                                     {"action": 1}, {"act": "install"}, [], "install", None, 5])
def test_setup_takes_only_install_or_remove(guard_rig: Any, payload: Any) -> None:
    status, body = post_json(guard_rig.server, "/api/guard/setup", payload)
    assert status == 400 and body == {"error": 'action must be "install" or "remove"'}
    assert guard_rig.setup.calls == []


def test_setup_with_a_body_that_is_not_json_is_a_400_that_does_not_echo_it(guard_rig: Any) -> None:
    status, _headers, body = request(guard_rig.server, "POST", "/api/guard/setup", body=b'{"action": "install", "x": abc123')
    assert status == 400 and b"abc123" not in body and guard_rig.setup.calls == []
    assert request(guard_rig.server, "POST", "/api/guard/setup")[0] == 400         # no body is not an action either


def test_setup_failing_is_a_500_without_details_and_the_status_is_looked_at_again(make_server: Any, tmp_path: Path) -> None:
    calls: list[str] = []

    def boom() -> Any:
        raise RuntimeError("C:/private/place")

    def status() -> Any:
        calls.append("status")
        return SETUP_STATUS

    server, _ = make_server(guard_dir=tmp_path / "guard", setup_cb={"status": status, "install": boom})
    get_guard(server)
    code, _headers, body = request(server, "POST", "/api/guard/setup", body=b'{"action": "install"}')
    assert code == 500 and b"private" not in body and json.loads(body) == {"error": "setup install failed"}
    get_guard(server)
    assert calls == ["status", "status"]                                            # whatever it did half way, look again
    assert post_json(server, "/api/guard/setup", {"action": "remove"})[0] == 501    # no callback for that one


GUARD_ROUTES = [("GET", "/api/guard"), ("POST", "/api/guard/resume"), ("POST", "/api/guard/setup")]


@pytest.mark.parametrize(("method", "path"), GUARD_ROUTES)
def test_the_guard_routes_need_the_token_the_right_host_and_the_right_method(guard_rig: Any, method: str, path: str) -> None:
    body = b'{"action": "install"}' if method == "POST" else None
    assert request(guard_rig.server, method, path, body=body, token=False)[0] == 401
    assert request(guard_rig.server, method, path, body=body, host="evil.example:80")[0] == 403
    other = "GET" if method == "POST" else "POST"
    status, headers, _ = request(guard_rig.server, other, path, body=b"{}" if other == "POST" else None)
    assert status == 405 and headers["allow"] == method
    assert guard_rig.setup.calls == [] and not guard_rig.folder.exists()               # none of that reached the setup or the files


def test_the_guard_answers_carry_no_token_and_no_cors_header(guard_rig: Any) -> None:
    for status, headers, body in (request(guard_rig.server, "GET", "/api/guard"),
                                  request(guard_rig.server, "POST", "/api/guard/resume"),
                                  request(guard_rig.server, "POST", "/api/guard/setup", body=b'{"action": "install"}')):
        assert status == 200 and guard_rig.server.token.encode("ascii") not in body
        assert not [name for name in headers if name.startswith("access-control-")]


# ── Setup in a dev instance and from source ───────────────────────────────────────────

def test_a_dev_instance_never_sets_up_claude_code(make_server: Any, tmp_path: Path) -> None:
    """Like the Run entry, the user's Claude Code settings belong to the installed copy: a test build leaves them alone."""
    server, _ = make_server(dev=True, guard_dir=tmp_path / "guard")
    setup_status = get_guard(server)["setup"]
    assert setup_status["available"] is False and "dev instance" in setup_status["reason"]
    assert setup_status["manual"] == "docs/AGENT_HOOK.md"
    for action in ("install", "remove"):
        status, body = post_json(server, "/api/guard/setup", {"action": action})
        assert status == 200 and body["ok"] is False and body["action"] == action and "dev instance" in body["errors"][0]
        assert body["changed"] == [] and body["backup"] is None


def test_the_real_setup_is_the_default_and_from_source_it_says_so(make_server: Any, tmp_path: Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)                              # a source run: no exe to point hooks at
    server, _ = make_server(guard_dir=tmp_path / "guard")
    assert get_guard(server)["setup"]["reason"] == agent_setup.NOT_INSTALLED_REASON
    status, body = post_json(server, "/api/guard/setup", {"action": "install"})
    assert status == 200 and body["ok"] is False and body["errors"] == [agent_setup.NOT_INSTALLED_REASON]
    assert not (tmp_path / "home" / ".claude").exists()                            # and nothing was written anywhere


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


class _FakeStartupRegistry:
    """The Run value and its StartupApproved record in memory, under startup's registry primitives."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, run: str | None = None, approval: Any = None) -> None:
        self.run, self.approval, self.writes, self.deletes = run, approval, [], []
        monkeypatch.setattr(startup, "_read_value", lambda: self.run)
        monkeypatch.setattr(startup, "_write_value", self._write)
        monkeypatch.setattr(startup, "_delete_value", self._delete)
        monkeypatch.setattr(startup, "_read_approval", lambda: self.approval)
        monkeypatch.setattr(startup, "_clear_approval", self._clear)
        monkeypatch.setattr(startup, "is_enabled", REAL_IS_ENABLED)
        monkeypatch.setattr(startup, "set_enabled", REAL_SET_ENABLED)

    def _write(self, command: str) -> None:
        self.run = command
        self.writes.append(command)

    def _delete(self) -> None:
        self.run = None
        self.deletes.append("run")

    def _clear(self) -> None:
        self.approval = None
        self.deletes.append("approval")


def _record(first_byte: int) -> bytes:
    """What Windows stores under StartupApproved: a state DWORD, then a FILETIME (all zero when never turned off)."""
    return bytes([first_byte, 0, 0, 0]) + bytes(8)


ENTRY = '"C:/Users/me/AppData/Local/Programs/Claude Usage Monitor/ClaudeUsageMonitor.exe"'


@pytest.mark.parametrize("record", [None, _record(0x02), _record(0x06), _record(0x00), b"", 2, 6],
                         ids=["no record", "02 enabled", "06 enabled", "00", "empty", "dword 2", "dword 6"])
def test_the_entry_reads_as_on_unless_windows_turned_it_off(monkeypatch: pytest.MonkeyPatch, record: Any) -> None:
    _FakeStartupRegistry(monkeypatch, run=ENTRY, approval=record)
    assert startup.is_enabled() is True


@pytest.mark.parametrize("record", [_record(0x03), _record(0x07), _record(0x01), b"\x03", 3, 1],
                         ids=["03 (Task Manager)", "07", "01", "short 03", "dword 3", "dword 1"])
def test_an_entry_turned_off_in_task_manager_or_settings_reads_as_off(monkeypatch: pytest.MonkeyPatch,
                                                                       record: Any) -> None:
    """The Run value stays, the StartupApproved record says off, and Windows does not start the app at sign-in."""
    _FakeStartupRegistry(monkeypatch, run=ENTRY, approval=record)
    assert startup.is_enabled() is False


def test_no_run_entry_is_off_whatever_the_record_says(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeStartupRegistry(monkeypatch, run=None, approval=_record(0x02))
    assert startup.is_enabled() is False


def test_an_unreadable_record_does_not_switch_the_entry_off(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _FakeStartupRegistry(monkeypatch, run=ENTRY)

    def refuse() -> bytes:
        raise PermissionError("denied")

    monkeypatch.setattr(startup, "_read_approval", refuse)
    assert startup.is_enabled() is True
    startup.set_enabled(True)
    assert registry.run == startup.command_line()


def test_turning_the_switch_on_lifts_a_turned_off_record(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _FakeStartupRegistry(monkeypatch, run=ENTRY, approval=_record(0x03))
    assert startup.is_enabled() is False
    startup.set_enabled(True)
    assert registry.approval is None and registry.run == startup.command_line()
    assert startup.is_enabled() is True


def test_turning_the_switch_on_when_nothing_is_there_yet_clears_a_stale_off_record(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows keeps the record after the Run value is gone, so off, then on again in the app must still start."""
    registry = _FakeStartupRegistry(monkeypatch, run=None, approval=_record(0x03))
    startup.set_enabled(True)
    assert startup.is_enabled() is True and registry.approval is None


def test_turning_the_switch_on_leaves_an_enabled_record_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _FakeStartupRegistry(monkeypatch, run=None, approval=_record(0x02))
    startup.set_enabled(True)
    assert registry.approval == _record(0x02) and "approval" not in registry.deletes


def test_turning_the_switch_off_and_the_launch_repair_leave_the_record_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _FakeStartupRegistry(monkeypatch, run='"C:/old place/ClaudeUsageMonitor.exe"', approval=_record(0x03))
    assert startup.repair_if_enabled(frozen=True) is True               # a moved install is repointed ...
    assert registry.run == startup.command_line(frozen=True)
    assert registry.approval == _record(0x03)                           # ... but Windows' record is not ours to change
    assert startup.is_enabled() is False
    startup.set_enabled(False)
    assert registry.run is None and registry.approval == _record(0x03)
    assert "approval" not in registry.deletes


def test_a_record_that_cannot_be_cleared_fails_the_switch_instead_of_pretending(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _FakeStartupRegistry(monkeypatch, run=ENTRY, approval=_record(0x03))

    def refuse() -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(startup, "_clear_approval", refuse)
    with pytest.raises(OSError):
        startup.set_enabled(True)
    assert registry.writes == []                                        # nothing half done
    assert startup.is_enabled() is False


@ui_only
def test_registry_round_trip_under_a_throwaway_key(monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform != "win32":
        pytest.skip("Windows only")
    import winreg
    key_path = rf"Software\ClaudeUsageMonitorTests-{os.getpid()}"
    approved_path = rf"Software\ClaudeUsageMonitorTests-{os.getpid()}-approved"    # never the real StartupApproved key
    monkeypatch.setattr(startup, "RUN_KEY", key_path)
    monkeypatch.setattr(startup, "APPROVED_KEY", approved_path)
    monkeypatch.setattr(startup, "is_enabled", REAL_IS_ENABLED)
    monkeypatch.setattr(startup, "set_enabled", REAL_SET_ENABLED)
    monkeypatch.setattr(startup, "_read_approval", ORIGINAL_READ_APPROVAL)
    monkeypatch.setattr(startup, "_clear_approval", ORIGINAL_CLEAR_APPROVAL)
    try:
        startup.set_enabled(True)
        assert startup.is_enabled() is True
        assert startup._read_value() == startup.command_line()
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, approved_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, startup.VALUE_NAME, 0, winreg.REG_BINARY, _record(0x03))   # as Task Manager does
        assert startup._read_approval() == _record(0x03)
        assert startup.is_enabled() is False                            # the Run value is still there
        startup.set_enabled(True)
        assert startup._read_approval() is None and startup.is_enabled() is True
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, approved_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, startup.VALUE_NAME, 0, winreg.REG_BINARY, _record(0x06))
        assert startup.is_enabled() is True
        startup.set_enabled(False)
        assert startup.is_enabled() is False
    finally:
        for path in (key_path, approved_path):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
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


class FakeEvent(list):
    """Stands in for pywebview's Event: += adds a handler and returns the same object; set / is_set / wait work as its own."""

    def __init__(self) -> None:
        super().__init__()
        self._flag = threading.Event()

    def __iadd__(self, handler: Any) -> "FakeEvent":
        self.append(handler)
        return self

    def set(self) -> None:
        self._flag.set()

    def is_set(self) -> bool:
        return self._flag.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._flag.wait(timeout)


class FakeEvents:
    def __init__(self) -> None:
        self.moved = FakeEvent()
        self.resized = FakeEvent()
        self.closing = FakeEvent()
        self.closed = FakeEvent()
        self.before_show = FakeEvent()
        self.loaded = FakeEvent()


class FakeWindow:
    """A pywebview window as dashboard_main drives it."""

    def __init__(self, x: int = 1, y: int = 2, width: int = 1200, height: int = 780) -> None:
        self.events = FakeEvents()
        self.native = None
        self.x, self.y, self.width, self.height = x, y, width, height
        self.exposed: list[str] = []
        self.destroyed = False

    def expose(self, *functions: Any) -> None:
        self.exposed += [function.__name__ for function in functions]

    def destroy(self) -> None:
        self.destroyed = True
        self.events.closed.set()


def _no_loop(func: Any = None, *args: Any, **kwargs: Any) -> None:
    """A webview.start that returns at once, as if the user had closed the window."""


def install_fake_webview(monkeypatch: pytest.MonkeyPatch, window: Any, *, start: Any = _no_loop,
                         renderer: str | None = "edgechromium", state: Any = None) -> list[dict[str, Any]]:
    """Put a fake ``webview`` module in place and give the child a token. Returns the create_window keyword arguments."""
    created: list[dict[str, Any]] = []
    fake = types.ModuleType("webview")

    def create_window(*args: Any, **kwargs: Any) -> Any:
        created.append({"args": args, **kwargs})
        return window

    fake.create_window = create_window  # type: ignore[attr-defined]
    fake.start = start  # type: ignore[attr-defined]
    fake.settings = {}  # type: ignore[attr-defined]
    if renderer is not None:
        fake.renderer = renderer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setenv(dashboard.TOKEN_ENV, "tok")
    monkeypatch.setattr(dashboard, "_read_state", lambda base, token: state)
    monkeypatch.setattr(dashboard, "_notify_closed", lambda base, token: None)
    return created


@pytest.fixture(autouse=True)
def webview2_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whether the machine running the tests has the WebView2 runtime must not decide a test's outcome."""
    monkeypatch.setattr(dashboard, "_registered_runtime_versions", lambda: ["141.0.3537.85"])


def test_dashboard_main_checks_the_position_before_the_window_is_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    checked: list[Any] = []
    window = FakeWindow(x=9000, y=100)
    install_fake_webview(monkeypatch, window, state=_state_with_bounds({"x": 9000, "y": 100, "w": 1200, "h": 780}),
                         start=lambda *a, **k: [handler() for handler in window.events.before_show] and None)
    monkeypatch.setattr(dashboard, "_save_bounds", lambda *a: None)
    monkeypatch.setattr(dashboard, "keep_on_screen", lambda w: checked.append(w) or True)
    assert dashboard.dashboard_main(["--dashboard", "--port", "5127"]) == 0
    assert checked == [window]


def test_dashboard_main_copes_with_a_pywebview_that_has_no_before_show(monkeypatch: pytest.MonkeyPatch) -> None:
    window = FakeWindow()
    del window.events.before_show                          # an older pywebview: the event does not exist
    install_fake_webview(monkeypatch, window)
    assert dashboard.dashboard_main(["--dashboard", "--port", "5128"]) == 0


def test_geometry_uses_saved_bounds_or_the_default_size() -> None:
    assert dashboard._geometry(None) == {"x": None, "y": None, "width": 1200, "height": 780}
    saved = {"x": 10, "y": 20, "w": 1400, "h": 900}
    assert dashboard._geometry(saved) == {"x": 10, "y": 20, "width": 1400, "height": 900}
    assert dashboard._geometry({"x": 1}) == {"x": None, "y": None, "width": 1200, "height": 780}


def _state_with_bounds(bounds: Any, bg: Any = None) -> dict[str, Any]:
    """The part of /api/state the dashboard process reads before its window opens."""
    state: dict[str, Any] = {"settings": {"dashboard_bounds": bounds}}
    if bg is not None:
        state["theme"] = {"id": "gates", "css_vars": {"--bg": bg}}
    return state


def test_the_saved_bounds_are_read_from_the_state_when_there_are_some() -> None:
    bounds = {"x": 10, "y": 20, "w": 1400, "h": 900}
    assert dashboard._saved_bounds(_state_with_bounds(bounds)) == bounds
    for state in (None, {}, {"settings": None}, {"settings": {}}, {"settings": {"dashboard_bounds": None}},
                  {"settings": {"dashboard_bounds": [1, 2]}}, "not a state"):
        assert dashboard._saved_bounds(state) is None


@pytest.mark.parametrize(("bg", "expected"), [
    ("#f3f3f3", "#f3f3f3"),                 # Gates, light: the window must not open black and then jump to light
    ("#202020", "#202020"),                 # Gates, dark
    ("#0B0C22", "#0B0C22"),                 # Night City Mox
    ("#090909", "#090909"),
])
def test_the_window_opens_on_the_themes_own_page_colour(bg: str, expected: str) -> None:
    assert dashboard._window_background(_state_with_bounds(None, bg)) == expected


@pytest.mark.parametrize("state", [
    None, {}, "state", {"theme": None}, {"theme": {}}, {"theme": {"css_vars": None}}, {"theme": {"css_vars": {}}},
    {"theme": {"css_vars": {"--bg": None}}}, {"theme": {"css_vars": {"--bg": 0xf3f3f3}}},
    {"theme": {"css_vars": {"--bg": "#fff"}}},                        # pywebview's WebView2 path reads six digits
    {"theme": {"css_vars": {"--bg": "#f3f3f3ff"}}}, {"theme": {"css_vars": {"--bg": "f3f3f3"}}},
    {"theme": {"css_vars": {"--bg": "white"}}}, {"theme": {"css_vars": {"--bg": "rgb(1, 2, 3)"}}},
    {"theme": {"css_vars": {"--bg": "#f3f3f3; x"}}},
])
def test_without_a_usable_theme_colour_the_window_keeps_the_default_background(state: Any) -> None:
    assert dashboard._window_background(state) == dashboard.DEFAULT_BACKGROUND == "#090909"


def test_dashboard_main_gives_the_window_the_themes_page_colour(monkeypatch: pytest.MonkeyPatch) -> None:
    created = install_fake_webview(monkeypatch, FakeWindow(),
                                   state=_state_with_bounds({"x": 1, "y": 2, "w": 1300, "h": 800}, "#f3f3f3"))
    assert dashboard.dashboard_main(["--dashboard", "--port", "5129"]) == 0
    assert created[0]["background_color"] == "#f3f3f3"
    assert (created[0]["x"], created[0]["y"], created[0]["width"], created[0]["height"]) == (1, 2, 1300, 800)


def test_dashboard_main_keeps_the_default_colour_when_the_state_cannot_be_read(monkeypatch: pytest.MonkeyPatch) -> None:
    created = install_fake_webview(monkeypatch, FakeWindow(), state=None)
    assert dashboard.dashboard_main(["--dashboard", "--port", "5130"]) == 0
    assert created[0]["background_color"] == "#090909"
    assert (created[0]["width"], created[0]["height"]) == dashboard.DEFAULT_SIZE


def test_the_dashboard_process_reads_the_light_gates_colour_from_the_real_server(
    make_server: Any, settings: Settings, themes_module: types.ModuleType, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end with the real ApiServer: the key the window reads is the one /api/state sends, for a light variant."""
    light = dataclasses.replace(FAKE_THEMES["gates"].variants[2], colors={"bg": "#f3f3f3", "text": "#1b1b1b", "accent": "#0067c0"})
    monkeypatch.setitem(FAKE_THEMES, "gates", dataclasses.replace(FAKE_THEMES["gates"], variants=(light,)))
    settings.update({"theme_id": "gates", "theme_variant": "system-light"})
    server, _ = make_server()
    state = dashboard._read_state(f"http://127.0.0.1:{server.port}", server.token)
    assert state is not None and dashboard._window_background(state) == "#f3f3f3"
    assert dashboard._read_state(f"http://127.0.0.1:{server.port}", "wrong token") is None      # a 401 is "cannot be read"


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

    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], creationflags=NO_WINDOW)
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
                         capture_output=True, text=True, check=False, creationflags=NO_WINDOW).stdout
    return f'"{pid}"' in out


@ui_only
def test_the_dashboard_child_dies_when_the_monitor_is_killed(tmp_path: Path) -> None:
    """The real launcher, a stand-in child, and a hard kill of the parent (TerminateProcess is what
    End task and taskkill /F do). Nothing gets to tear down, so only the job can end the child."""
    repo = str(Path(__file__).resolve().parents[1])
    parent = subprocess.Popen([sys.executable, "-c", _PARENT.format(repo=repo)], stdout=subprocess.PIPE,
                              env=dict(os.environ, **REAL_PROFILE_ENV), cwd=repo, creationflags=NO_WINDOW)
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
            subprocess.run(["taskkill", "/F", "/PID", str(child_pid)], capture_output=True, check=False,
                           creationflags=NO_WINDOW)


class _Clock:
    """A clock the launcher tests move by hand."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def launcher_rig(make_server: Any, settings: Settings, monkeypatch: pytest.MonkeyPatch):
    """A launcher over fake child processes, a hand-moved clock and a recorded focus call. Nothing is a real window."""
    FakePopen.instances = []
    monkeypatch.setattr(dashboard.subprocess, "Popen", FakePopen)
    focus = {"window": False, "calls": []}
    monkeypatch.setattr(dashboard, "focus_window", lambda pid: focus["calls"].append(pid) or focus["window"])
    server, _ = make_server()
    clock = _Clock()
    return types.SimpleNamespace(launcher=dashboard.DashboardLauncher(server, settings, clock=clock), clock=clock,
                                 focus=focus, children=FakePopen.instances, server=server)


def test_a_click_right_after_the_window_closed_opens_a_new_dashboard_while_the_old_process_exits(launcher_rig) -> None:
    """The process outlives its window by several seconds (WebView2 shuts down); the tray item must not go dead for them."""
    rig = launcher_rig
    assert rig.launcher.open() is True
    first = rig.children[0]
    rig.clock.now = 5.0                                             # the user had it open for a while ...
    assert rig.launcher.window_closed(first.pid) is True            # ... closed it: the child says so and lingers
    assert first.poll() is None
    rig.clock.now = 5.2                                             # a click 0.2 s later
    assert rig.launcher.open() is True
    assert len(rig.children) == 2 and rig.launcher.pid == rig.children[1].pid
    assert first.poll() is None                                     # the old one is left to finish by itself
    assert rig.focus["calls"] == []                                 # and a window that is closing is not focused


def test_a_notice_from_an_old_child_does_not_condemn_the_new_one(launcher_rig) -> None:
    rig = launcher_rig
    rig.launcher.open()
    first = rig.children[0]
    rig.launcher.window_closed(first.pid)
    rig.launcher.open()
    second = rig.children[1]
    assert rig.launcher.window_closed(first.pid) is False           # late, from the process that was replaced
    rig.clock.now = 2.0
    assert rig.launcher.open() is True and len(rig.children) == 2   # the new child is still starting: left alone
    assert rig.launcher.pid == second.pid


def test_a_notice_from_a_stranger_or_before_any_child_changes_nothing(launcher_rig) -> None:
    rig = launcher_rig
    assert rig.launcher.window_closed(1234) is False                # nothing started yet
    rig.launcher.open()
    assert rig.launcher.window_closed(rig.children[0].pid + 1) is False
    rig.children[0]._returncode = 0
    assert rig.launcher.window_closed(rig.children[0].pid) is False  # already gone
    rig.clock.now = 1.0
    assert rig.launcher.open() is True and len(rig.children) == 2   # an exited child is replaced as it always was


def test_a_process_that_is_still_starting_is_left_alone_within_the_grace(launcher_rig) -> None:
    rig = launcher_rig
    rig.launcher.open()
    for seconds in (0.5, 3.0, dashboard.START_GRACE_S):
        rig.clock.now = seconds
        assert rig.launcher.open() is True
    assert len(rig.children) == 1 and rig.children[0].poll() is None


def test_a_process_that_shows_no_window_past_the_grace_is_replaced(launcher_rig, caplog: pytest.LogCaptureFixture) -> None:
    """Without this a child that stays alive and windowless (a stuck web view) would leave the tray item dead for good."""
    rig = launcher_rig
    rig.launcher.open()
    first = rig.children[0]
    rig.clock.now = dashboard.START_GRACE_S + 0.5
    with caplog.at_level("WARNING", logger="usage_monitor.ui.dashboard"):
        assert rig.launcher.open() is True
    assert len(rig.children) == 2 and rig.launcher.pid == rig.children[1].pid
    assert first.poll() is not None                                 # the leftover was asked to end
    assert any("shown no window" in record.getMessage() for record in caplog.records)
    rig.clock.now += 1.0                                            # the new child gets its own grace period
    assert rig.launcher.open() is True and len(rig.children) == 2


def test_an_open_window_is_focused_at_any_age(launcher_rig) -> None:
    rig = launcher_rig
    rig.focus["window"] = True
    rig.launcher.open()
    rig.clock.now = 10_000.0                                        # hours later, with the window still on screen
    assert rig.launcher.open() is True
    assert len(rig.children) == 1 and rig.focus["calls"] == [rig.children[0].pid]


def test_closing_the_launcher_ends_the_process_that_was_still_exiting_too(launcher_rig) -> None:
    rig = launcher_rig
    rig.launcher.open()
    rig.launcher.window_closed(rig.children[0].pid)
    rig.launcher.open()
    assert [child.poll() for child in rig.children] == [None, None]
    rig.launcher.close()
    assert all(child.poll() is not None for child in rig.children)
    assert rig.launcher.running is False


def test_a_retired_process_that_has_exited_is_not_kept(launcher_rig) -> None:
    rig = launcher_rig
    rig.launcher.open()
    rig.launcher.window_closed(rig.children[0].pid)
    rig.launcher.open()
    rig.children[0]._returncode = 0                                 # it finished shutting down
    rig.launcher.window_closed(rig.children[1].pid)
    rig.launcher.open()                                             # the second is retired in its turn
    assert len(rig.children) == 3
    assert rig.launcher._retired == [rig.children[1]]


def test_the_childs_notice_travels_through_the_real_server_to_the_launcher(
        make_server: Any, settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    FakePopen.instances = []
    monkeypatch.setattr(dashboard.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(dashboard, "focus_window", lambda pid: False)
    holder: dict[str, Any] = {}
    server, _ = make_server(dashboard_closed_cb=lambda pid: holder["launcher"].window_closed(pid))
    launcher = holder["launcher"] = dashboard.DashboardLauncher(server, settings)
    launcher.open()
    FakePopen.instances[0].pid = os.getpid()                         # the notice carries the pid of the process that posts it
    dashboard._notify_closed(f"http://127.0.0.1:{server.port}", server.token)
    assert launcher.open() is True
    assert len(FakePopen.instances) == 2


# ── POST /api/dashboard/closed ───────────────────────────────────────────────────────

def test_the_closed_notice_reaches_the_callback_with_the_process_id(make_server: Any) -> None:
    seen: list[int] = []
    server, _ = make_server(dashboard_closed_cb=lambda pid: seen.append(pid) or pid == 4321)
    assert post_json(server, "/api/dashboard/closed", {"pid": 4321}) == (200, {"ok": True, "known": True})
    assert post_json(server, "/api/dashboard/closed", {"pid": 99}) == (200, {"ok": True, "known": False})
    assert seen == [4321, 99]


@pytest.mark.parametrize("body", [{}, {"pid": "12"}, {"pid": True}, {"pid": 0}, {"pid": -5}, {"pid": 1.5}, [], None, "x"])
def test_the_closed_notice_needs_a_process_id(make_server: Any, body: Any) -> None:
    server, _ = make_server(dashboard_closed_cb=lambda pid: pytest.fail("a bad notice reached the launcher"))
    assert post_json(server, "/api/dashboard/closed", body)[0] == 400


def test_the_closed_notice_needs_the_token_and_the_right_verb(make_server: Any) -> None:
    server, _ = make_server(dashboard_closed_cb=lambda pid: pytest.fail("refused requests must not get through"))
    assert request(server, "POST", "/api/dashboard/closed", body=b'{"pid": 5}', token=False)[0] == 401
    status, headers, _ = request(server, "GET", "/api/dashboard/closed")
    assert status == 405 and headers["allow"] == "POST"


def test_the_closed_notice_says_so_when_nothing_manages_the_dashboard(make_server: Any) -> None:
    server, _ = make_server()
    assert post_json(server, "/api/dashboard/closed", {"pid": 5})[0] == 501


def test_a_failing_launcher_callback_is_a_500_not_a_dead_server(make_server: Any) -> None:
    def broken(pid: int) -> bool:
        raise RuntimeError("boom")

    server, _ = make_server(dashboard_closed_cb=broken)
    assert post_json(server, "/api/dashboard/closed", {"pid": 5})[0] == 500
    assert request(server, "GET", "/api/state")[0] == 200


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
    monkeypatch.setattr(dashboard, "_read_state", lambda base, token: None)
    opened: list[str] = []
    monkeypatch.setattr(dashboard.webbrowser, "open", lambda url: opened.append(url) or True)
    assert dashboard.dashboard_main(["--dashboard", "--port", "5123"]) == dashboard.EXIT_BROWSER_FALLBACK
    assert opened == ["http://127.0.0.1:5123/?k=tok"]          # the page is served to a caller that has the key


def test_dashboard_main_falls_back_when_the_webview_will_not_start(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_webview2(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("WebView2 runtime missing")

    install_fake_webview(monkeypatch, FakeWindow(), start=no_webview2,
                         state=_state_with_bounds({"x": 1, "y": 2, "w": 1200, "h": 780}))
    opened: list[str] = []
    monkeypatch.setattr(dashboard.webbrowser, "open", lambda url: opened.append(url) or True)
    assert dashboard.dashboard_main(["--dashboard", "--port", "5124"]) == dashboard.EXIT_BROWSER_FALLBACK
    assert opened == ["http://127.0.0.1:5124/?k=tok"]


def test_dashboard_main_reports_a_failing_window_call_as_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A TypeError from our own create_window call is a bug, not a missing WebView2: the page still
    opens in the browser, but the exit code must not look like the harmless fallback."""
    install_fake_webview(monkeypatch, FakeWindow(),
                         start=lambda *a, **k: pytest.fail("a window that was never created cannot be started"))

    def broken_call(*args: Any, **kwargs: Any) -> None:
        raise TypeError("create_window() got an unexpected keyword argument 'min_size'")

    sys.modules["webview"].create_window = broken_call  # type: ignore[attr-defined]
    opened: list[str] = []
    monkeypatch.setattr(dashboard.webbrowser, "open", lambda url: opened.append(url) or True)
    code = dashboard.dashboard_main(["--dashboard", "--port", "5126"])
    assert code == dashboard.EXIT_WINDOW_ERROR
    assert code not in (0, dashboard.EXIT_BROWSER_FALLBACK, dashboard.EXIT_NO_ARGS)
    assert opened == ["http://127.0.0.1:5126/?k=tok"]


def test_a_window_that_cannot_take_the_bridge_functions_is_an_error_too(monkeypatch: pytest.MonkeyPatch) -> None:
    window = FakeWindow()

    def refuse(*functions: Any) -> None:
        raise AttributeError("this pywebview has no expose()")

    window.expose = refuse  # type: ignore[method-assign]
    install_fake_webview(monkeypatch, window, start=lambda *a, **k: pytest.fail("no page without its bridge"))
    opened: list[str] = []
    monkeypatch.setattr(dashboard.webbrowser, "open", lambda url: opened.append(url) or True)
    assert dashboard.dashboard_main(["--dashboard", "--port", "5133"]) == dashboard.EXIT_WINDOW_ERROR
    assert opened == ["http://127.0.0.1:5133/?k=tok"]


def test_the_exit_codes_tell_the_cases_apart(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(dashboard.TOKEN_ENV, raising=False)
    codes = {dashboard.EXIT_WINDOW_ERROR, dashboard.EXIT_NO_ARGS, dashboard.EXIT_BROWSER_FALLBACK, 0}
    assert len(codes) == 4
    assert dashboard.dashboard_main(["--dashboard", "--port", "5000"]) == dashboard.EXIT_NO_ARGS


def test_dashboard_saves_bounds_once_on_close(monkeypatch: pytest.MonkeyPatch) -> None:
    saved: list[tuple[str, Any]] = []
    window = FakeWindow(x=100, y=50, width=1300, height=800)

    def run(*args: Any, **kwargs: Any) -> None:
        # The user resizes, then closes: each event handler gets the window's current geometry.
        window.width, window.height = 1400, 900
        for handler in window.events.resized:
            handler()
        for handler in window.events.closing:
            handler()
        for handler in window.events.closed:
            handler()

    install_fake_webview(monkeypatch, window, start=run)
    monkeypatch.setattr(dashboard, "_save_bounds", lambda base, token, bounds: saved.append((token, bounds)))
    assert dashboard.dashboard_main(["--dashboard", "--port", "5125"]) == 0
    assert saved == [("tok", {"x": 100, "y": 50, "w": 1400, "h": 900})]


def test_the_dashboard_tells_the_monitor_its_window_is_gone_after_saving_its_size(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    window = FakeWindow()

    def run(*args: Any, **kwargs: Any) -> None:
        for handler in window.events.resized:
            handler()
        for handler in window.events.closed:
            handler()

    install_fake_webview(monkeypatch, window, start=run)
    monkeypatch.setattr(dashboard, "_save_bounds", lambda base, token, bounds: order.append("bounds"))
    monkeypatch.setattr(dashboard, "_notify_closed", lambda base, token: order.append(f"closed {base} {token}"))
    assert dashboard.dashboard_main(["--dashboard", "--port", "5134"]) == 0
    assert order == ["bounds", "closed http://127.0.0.1:5134 tok"]


def test_the_notice_goes_out_even_when_no_bounds_were_tracked(monkeypatch: pytest.MonkeyPatch) -> None:
    """A window closed before it was ever moved or resized has no size to save, and still is gone."""
    sent: list[str] = []
    window = FakeWindow()

    def run(*args: Any, **kwargs: Any) -> None:
        for handler in window.events.closed:
            handler()

    install_fake_webview(monkeypatch, window, start=run)
    monkeypatch.setattr(dashboard, "_save_bounds", lambda *a: pytest.fail("there was no size to save"))
    monkeypatch.setattr(dashboard, "_notify_closed", lambda base, token: sent.append(token))
    assert dashboard.dashboard_main(["--dashboard", "--port", "5135"]) == 0
    assert sent == ["tok"]


def test_the_notice_posts_the_childs_process_id_and_never_raises(make_server: Any,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[int] = []
    server, _ = make_server(dashboard_closed_cb=lambda pid: received.append(pid) or True)
    dashboard._notify_closed(f"http://127.0.0.1:{server.port}", server.token)
    assert received == [os.getpid()]
    dashboard._notify_closed(f"http://127.0.0.1:{server.port}", "the wrong token")        # a refusal is not an error
    dashboard._notify_closed("http://127.0.0.1:1", server.token)                          # nobody listening: not either
    assert received == [os.getpid()]


# ── No usable WebView2: the browser, not a blank window (pywebview itself raises nothing) ──────────────────────

@pytest.mark.parametrize(("registered", "expected"), [
    ([], False),
    (["0.0.0.0"], False),                          # registered but not installed, Microsoft's own detection rule
    (["85.0.564.70"], False),                      # older than pywebview accepts
    (["86.0.622.0"], True),
    (["86.0.621.99"], False),
    (["141.0.3537.85"], True),
    (["0.0.0.0", "141.0.3537.85"], True),          # per-user and per-machine registrations are separate entries
    (["", "garbage", "x.y.z"], False),
    ([" 155.0.4283.62 "], True),
])
def test_the_runtime_check_follows_the_version_pywebview_requires(monkeypatch: pytest.MonkeyPatch,
                                                                  registered: list[str], expected: bool) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(dashboard, "_registered_runtime_versions", lambda: registered)
    assert dashboard.webview2_runtime_available(types.SimpleNamespace(settings={})) is expected


def test_a_fixed_runtime_named_in_pywebviews_settings_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(dashboard, "_registered_runtime_versions", lambda: [])
    fixed = types.SimpleNamespace(settings={"WEBVIEW2_RUNTIME_PATH": "C:/runtimes/fixed"})
    assert dashboard.webview2_runtime_available(fixed) is True
    assert dashboard.webview2_runtime_available(types.SimpleNamespace(settings={"WEBVIEW2_RUNTIME_PATH": None})) is False


def test_an_unreadable_registry_leaves_the_decision_to_pywebview_and_the_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> list[str]:
        raise PermissionError("denied")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(dashboard, "_registered_runtime_versions", broken)
    assert dashboard.webview2_runtime_available(types.SimpleNamespace(settings={})) is True


def test_the_runtime_check_is_not_ours_to_make_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(dashboard, "_registered_runtime_versions", lambda: pytest.fail("no registry off Windows"))
    assert dashboard.webview2_runtime_available(types.SimpleNamespace(settings={})) is True


class _FakeRegistryKey:
    def __init__(self, value: str) -> None:
        self.value = value

    def __enter__(self) -> "_FakeRegistryKey":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


def test_the_registered_versions_are_read_where_edgeupdate_keeps_them(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = dashboard.WEBVIEW2_CLIENTS[0]
    present = {
        ("HKCU", rf"Software\Microsoft\EdgeUpdate\Clients\{runtime}"): "141.0.1",
        ("HKLM", rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{runtime}"): "140.0.2",
        ("HKLM", rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{dashboard.WEBVIEW2_CLIENTS[3]}"): "145.0.3",
    }
    fake = types.ModuleType("winreg")
    fake.HKEY_CURRENT_USER, fake.HKEY_LOCAL_MACHINE = "HKCU", "HKLM"  # type: ignore[attr-defined]

    def open_key(hive: str, path: str) -> _FakeRegistryKey:
        if (hive, path) not in present:
            raise FileNotFoundError(path)
        return _FakeRegistryKey(present[(hive, path)])

    fake.OpenKey = open_key  # type: ignore[attr-defined]
    fake.QueryValueEx = lambda key, name: (key.value, 1) if name == "pv" else pytest.fail(name)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(dashboard, "_registered_runtime_versions", ORIGINAL_RUNTIME_VERSIONS)
    assert sorted(dashboard._registered_runtime_versions()) == ["140.0.2", "141.0.1", "145.0.3"]


@pytest.mark.skipif(sys.platform != "win32", reason="the registry is Windows only")
def test_the_real_registry_answers_with_a_list_of_version_strings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dashboard, "_registered_runtime_versions", ORIGINAL_RUNTIME_VERSIONS)
    versions = dashboard._registered_runtime_versions()          # read only; what is there depends on the machine
    assert isinstance(versions, list) and all(isinstance(v, str) for v in versions)


def test_dashboard_main_opens_the_browser_at_once_without_a_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    created = install_fake_webview(monkeypatch, FakeWindow(),
                                   start=lambda *a, **k: pytest.fail("pywebview would open a blank IE11 window here"))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(dashboard, "_registered_runtime_versions", lambda: [])
    opened: list[str] = []
    monkeypatch.setattr(dashboard.webbrowser, "open", lambda url: opened.append(url) or True)
    assert dashboard.dashboard_main(["--dashboard", "--port", "5136"]) == dashboard.EXIT_BROWSER_FALLBACK
    assert opened == ["http://127.0.0.1:5136/?k=tok"] and created == []         # no window was ever made


def _guarded_start(window: FakeWindow, before: Any = None) -> Any:
    """A webview.start that runs the guard function as pywebview does, in a thread, and returns when it has finished."""
    def start(func: Any = None, *args: Any, **kwargs: Any) -> None:
        if before is not None:
            before(window)
        thread = threading.Thread(target=func)
        thread.start()
        thread.join(10)
        assert not thread.is_alive(), "the guard did not finish"
    return start


def test_a_renderer_that_is_not_webview2_sends_the_page_to_the_browser(monkeypatch: pytest.MonkeyPatch,
                                                                        caplog: pytest.LogCaptureFixture) -> None:
    """pywebview picks the IE11 engine without a word when it finds no runtime: the window would stay blank."""
    window = FakeWindow()
    install_fake_webview(monkeypatch, window, renderer="mshtml", start=_guarded_start(window))
    opened: list[str] = []
    monkeypatch.setattr(dashboard.webbrowser, "open", lambda url: opened.append(url) or True)
    with caplog.at_level("WARNING", logger="usage_monitor.ui.dashboard"):
        code = dashboard.dashboard_main(["--dashboard", "--port", "5137"])
    assert code == dashboard.EXIT_BROWSER_FALLBACK
    assert opened == ["http://127.0.0.1:5137/?k=tok"] and window.destroyed is True
    assert any("mshtml" in record.getMessage() for record in caplog.records)           # and the log says why


def test_a_page_that_never_loads_is_sent_to_the_browser_after_the_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """A WebView2 that fails to start leaves a window and no error: the load event never comes."""
    monkeypatch.setattr(dashboard, "LOAD_TIMEOUT_S", 0.4)
    window = FakeWindow()
    install_fake_webview(monkeypatch, window, start=_guarded_start(window))
    opened: list[str] = []
    monkeypatch.setattr(dashboard.webbrowser, "open", lambda url: opened.append(url) or True)
    began = time.monotonic()
    assert dashboard.dashboard_main(["--dashboard", "--port", "5138"]) == dashboard.EXIT_BROWSER_FALLBACK
    assert 0.3 <= time.monotonic() - began < 5
    assert opened == ["http://127.0.0.1:5138/?k=tok"] and window.destroyed is True


def test_a_page_that_loads_leaves_the_window_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dashboard, "LOAD_TIMEOUT_S", 0.4)
    window = FakeWindow()
    install_fake_webview(monkeypatch, window, start=_guarded_start(window, before=lambda w: w.events.loaded.set()))
    monkeypatch.setattr(dashboard.webbrowser, "open", lambda url: pytest.fail("the page loaded; no browser"))
    assert dashboard.dashboard_main(["--dashboard", "--port", "5139"]) == 0
    assert window.destroyed is False


def test_a_window_the_user_closes_before_the_page_loads_does_not_open_the_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dashboard, "LOAD_TIMEOUT_S", 5.0)
    window = FakeWindow()
    install_fake_webview(monkeypatch, window, start=_guarded_start(window, before=lambda w: w.events.closed.set()))
    monkeypatch.setattr(dashboard.webbrowser, "open", lambda url: pytest.fail("the user closed the window"))
    assert dashboard.dashboard_main(["--dashboard", "--port", "5140"]) == 0


def test_the_guard_does_not_keep_the_process_alive_after_the_loop_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    """pywebview's start(func) runs the guard in a thread that is not a daemon; it must end with the GUI loop."""
    monkeypatch.setattr(dashboard, "LOAD_TIMEOUT_S", 30.0)
    window = FakeWindow()
    threads: list[threading.Thread] = []

    def start(func: Any = None, *args: Any, **kwargs: Any) -> None:
        thread = threading.Thread(target=func)
        thread.start()
        threads.append(thread)                 # and the loop ends at once, without the page ever loading

    install_fake_webview(monkeypatch, window, start=start)
    monkeypatch.setattr(dashboard.webbrowser, "open", lambda url: pytest.fail("the loop had ended"))
    assert dashboard.dashboard_main(["--dashboard", "--port", "5141"]) == 0
    threads[0].join(5)
    assert not threads[0].is_alive()


def test_a_window_that_will_not_close_does_not_turn_the_fallback_into_a_crash(monkeypatch: pytest.MonkeyPatch,
                                                                              caplog: pytest.LogCaptureFixture) -> None:
    window = FakeWindow()

    def refuse() -> None:
        raise RuntimeError("Main window failed to start")

    window.destroy = refuse  # type: ignore[method-assign]
    install_fake_webview(monkeypatch, window, renderer="mshtml", start=_guarded_start(window))
    monkeypatch.setattr(dashboard.webbrowser, "open", lambda url: True)
    with caplog.at_level("ERROR", logger="usage_monitor.ui.dashboard"):
        assert dashboard.dashboard_main(["--dashboard", "--port", "5142"]) == dashboard.EXIT_BROWSER_FALLBACK
    assert any("window check failed" in record.getMessage() for record in caplog.records)


def test_the_guards_timeout_is_shorter_than_the_launchers_patience() -> None:
    assert dashboard.LOAD_TIMEOUT_S < dashboard.START_GRACE_S


# ── The bridge: the page may call four functions and nothing it can reach through them ──────────────────────

def test_the_page_gets_its_functions_one_by_one_and_no_js_api_object(monkeypatch: pytest.MonkeyPatch) -> None:
    window = FakeWindow()
    created = install_fake_webview(monkeypatch, window)
    assert dashboard.dashboard_main(["--dashboard", "--port", "5143"]) == 0
    assert "js_api" not in created[0]                      # pywebview resolves a js_api call by getattr, private names included
    assert window.exposed == list(dashboard.BRIDGE_FUNCTIONS) == ["get_token", "open_external", "save_export", "set_chrome"]


class _BridgeOs:
    """What a page script would love to reach: the os module that pywebview's GUI module imports."""
    ran: list[str] = []

    @classmethod
    def system(cls, command: str) -> int:
        cls.ran.append(command)
        return 0


def _bridge_window(js_api: Any = None) -> Any:
    """The parts of a pywebview Window that expose() and js_bridge_call read, with pywebview's own expose()."""
    window_module = pytest.importorskip("webview.window")

    class Window:
        expose = window_module.Window.expose

        def __init__(self) -> None:
            self._expose_lock = threading.Lock()
            self._functions: dict[str, Any] = {}
            self._js_api = js_api
            self.events = types.SimpleNamespace(loaded=types.SimpleNamespace(is_set=lambda: False))
            self.gui = types.SimpleNamespace(os=_BridgeOs)           # the real window's gui is the winforms module
            self.answers: queue.Queue[str] = queue.Queue()

        def evaluate_js(self, script: str, *args: Any, **kwargs: Any) -> None:
            self.answers.put(script)

        def run_js(self, script: str) -> None:
            self.answers.put(script)

    return Window()


def test_a_page_script_cannot_walk_from_the_api_to_os_system_through_the_bridge() -> None:
    util = pytest.importorskip("webview.util")
    _BridgeOs.ran = []
    window = _bridge_window()
    api = dashboard.DashboardApi("tok")
    api._bind(window)
    dashboard.expose_api(window, api)

    for name in ("_window.gui.os.system", "_window.gui", "_bind", "_token", "_saving",
                 "get_token.__self__._window.gui.os.system", "no_such_call"):
        util.js_bridge_call(window, name, ["echo pwned"], "id-attack")
    time.sleep(0.3)                                         # js_bridge_call runs an allowed call in a thread
    assert _BridgeOs.ran == [] and window.answers.empty()

    util.js_bridge_call(window, "get_token", [], "id-control")                          # an exposed call still works
    assert "tok" in window.answers.get(timeout=5)
    util.js_bridge_call(window, "open_external", ["https://evil.example/"], "id-link")   # and still checks its own input
    assert "false" in window.answers.get(timeout=5)

    # The control: the same walk through a js_api object, which is how the window used to be wired.
    old = _bridge_window(js_api=api)
    util.js_bridge_call(old, "_window.gui.os.system", ["echo pwned"], "id-old")
    deadline = time.monotonic() + 5
    while not _BridgeOs.ran and time.monotonic() < deadline:
        time.sleep(0.05)
    if not _BridgeOs.ran:
        pytest.skip("this pywebview no longer resolves private names through js_api; the attack cannot be shown")
    assert _BridgeOs.ran == ["echo pwned"]


@ui_only
def test_dashboard_child_opens_a_window_and_stays_up(tmp_path: Path, make_server: Any) -> None:
    """Starts the real pywebview child against a live API and checks that its window stays open."""
    server, _ = make_server()
    env = dict(os.environ, **REAL_PROFILE_ENV, CUM_DASHBOARD_TOKEN=server.token, CUM_APPDATA_DIR=str(tmp_path / "app"))
    repo = str(Path(__file__).resolve().parents[1])
    child = subprocess.Popen([sys.executable, "-m", "usage_monitor.ui.dashboard", "--port", str(server.port)],
                             env=env, cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             creationflags=NO_WINDOW)
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
                             env=env, cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             creationflags=NO_WINDOW)
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
  const main = document.getElementById('main');
  // For the failure message only: what else could take the cards' height (a scrollbar, a banner, the scale, the fonts).
  const page = {main: main && [main.clientWidth, main.clientHeight, main.scrollWidth, main.scrollHeight],
                banners: [...document.querySelectorAll('.nm-msg, .nm-update')].map(el => el.className),
                dpr: devicePixelRatio, fonts: document.fonts.status, visible: document.visibilityState};
  return JSON.stringify({vw: innerWidth, vh: innerHeight, cards: cards, plan: plan, page: page});
})()'''
results = []

def probe(window):
    try:
        # The page draws every card at once, with placeholders (section.nm-card[data-state=pending], figures marked
        # data-pending). Measure once the cost card holds its numbers and its plan lines and nothing is pending.
        for _ in range(150):
            if window.evaluate_js("!!document.querySelector('#cost .nm-tile') && "
                                  "document.querySelectorAll('#cost .nm-plan .nm-facts span').length >= 2 && "
                                  "!document.querySelector('.nm-grid [data-pending], .nm-grid .nm-card[data-state=pending]')"):
                break
            time.sleep(0.2)
        for width, height in SIZES:
            window.resize(width, height)
            time.sleep(1.0)
            # A full CUM_UI_TESTS run on a busy machine once measured every Nostromo card 3 to 5 px over at 1200 x 700,
            # which never happened when the test ran alone: the page was still settling a second after the resize. Measure
            # until two reads agree, which still catches a card that stays clipped; a failure prints what else took room.
            measured = window.evaluate_js(MEASURE)
            for _ in range(20):
                time.sleep(0.3)
                again = window.evaluate_js(MEASURE)
                if again == measured:
                    break
                measured = again
            results.append(json.loads(measured))
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
                             timeout=120, check=False, creationflags=NO_WINDOW)
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
        assert not clipped, (f"{size} (page {result['vw']} x {result['vh']}): cards cut their content off: {clipped}; "
                             f"page {result['page']}")
        cost = next(card for card in result["cards"] if card["name"] == "cost")
        assert cost["tiles"] is not None and cost["tiles"] >= 4, f"{size}: the tile numerals touch the card edge"


# ── No card moves when its report lands (real web view) ───────────────────────────

# Runs in a child process like _LAYOUT_PROBE. For each size it loads the Cost page afresh, measures every card while the
# reports are still held back (each one a placeholder), waits until the numbers are in and nothing moves any more, and measures
# again. The reports are held by the test (see _hold_reports); the probe only checks that it did see placeholders.
_SHIFT_PROBE = r"""
import json, sys, time
import webview

URL, SIZES = sys.argv[1], json.loads(sys.argv[2])
CARDS = '''(() => {
  const cards = {};
  document.querySelectorAll('#main [data-card]').forEach(card => {
    cards[card.getAttribute('data-card')] = {state: card.getAttribute('data-state'), h: card.offsetHeight};
  });
  return JSON.stringify({vw: innerWidth, vh: innerHeight, cards: cards});
})()'''
FRESH = "!window.__shiftProbe && document.querySelectorAll('#main [data-card]').length > 0"
SETTLED = '''(() => {
  const cards = [...document.querySelectorAll('#main [data-card]')];
  return cards.length > 0 && cards.every(c => c.getAttribute('data-state') === 'ready') &&
    !document.querySelector('#main [data-pending]') && !document.getElementById('main').classList.contains('fx-run');
})()'''
results = []


def ask(window, script):
    try:
        return window.evaluate_js(script)
    except Exception:
        return None                              # the page is between two documents


def wait_for(window, script, seconds):
    end = time.time() + seconds
    while time.time() < end:
        if ask(window, script):
            return True
        time.sleep(0.05)
    return False


def probe(window):
    try:
        for index, (width, height) in enumerate(SIZES):
            window.resize(width, height)
            if index:
                ask(window, "window.__shiftProbe = 1")           # a mark only the old document has: the new one is the page to measure
                window.load_url(URL + ("&" if "?" in URL else "?") + "size=%d#cost" % index)    # the URL carries the run's key
            if not wait_for(window, FRESH, 30):
                raise RuntimeError("the Cost page did not draw its cards")
            pending = json.loads(ask(window, CARDS))
            settled = wait_for(window, SETTLED, 60)
            time.sleep(1.5)                                      # the entrance and the arrival effects are over
            loaded = json.loads(ask(window, CARDS))
            results.append({"size": [width, height], "pending": pending, "loaded": loaded, "settled": settled})
    finally:
        print(json.dumps(results), flush=True)
        window.destroy()


window = webview.create_window("shift probe", URL + "#cost", width=SIZES[0][0], height=SIZES[0][1], x=-30000, y=-30000)
try:
    webview.start(probe, window)
except Exception as exc:
    print("no web view here:", type(exc).__name__, file=sys.stderr)
    sys.exit(3)
"""


def _hold_reports(server: ApiServer, seconds: float) -> None:
    """Every statistics answer waits ``seconds`` first, so a page has placeholders for that long (the real reports take 1 to
    400 ms; a slow disk or a busy machine takes longer, which is the case the layout has to survive)."""
    real = server._reader.read

    def held(key: Any, compute: Any) -> Any:
        time.sleep(seconds)
        return real(key, compute)

    server._reader.read = held                                   # type: ignore[method-assign]


@ui_only
@pytest.mark.parametrize("theme", ["nostromo/amber", "night_city/edgerun", "gates/blue-light"])
def test_no_cost_card_changes_height_when_its_report_lands_in_the_real_web_view(tmp_path: Path, theme: str) -> None:
    """The Fast mode card was a one line note while the report was on its way and a boxed note of three lines when it landed,
    so it grew by 21 to 59 px and the cards below it dropped, at every window narrower than about 1260 px (a wider window
    hides it: the row is held open by taller cards beside it, which is why a check at 1280 x 800 never saw it). The Main and
    subagents card grew a line in a narrow window the same way, because the placeholder "0% · 0 requests" is shorter than a
    real "97% · 2,670 requests". Measured in WebView2, the engine the dashboard really uses, in the default window and a
    narrower one, with the reports held back for 5 s so the placeholders are what is measured first."""
    server, settings = devserver.make_server("normal", 0, settings_dir=tmp_path / "dev", theme=theme, warm=True)
    _hold_reports(server, 5.0)
    server.start()
    try:
        env = dict(os.environ, **REAL_PROFILE_ENV, CUM_APPDATA_DIR=str(tmp_path / "app"))
        sizes = [[1200, 780], [1000, 700]]
        run = subprocess.run([sys.executable, "-c", _SHIFT_PROBE, server.url, json.dumps(sizes)], env=env,
                             cwd=str(Path(__file__).resolve().parents[1]), capture_output=True, text=True,
                             timeout=240, check=False, creationflags=NO_WINDOW)
    finally:
        server.stop()
        settings.close()
    if run.returncode == 3:
        pytest.skip("pywebview or WebView2 is not available here")
    assert run.returncode == 0, run.stderr[-800:]
    measured = json.loads(run.stdout.strip().splitlines()[-1])
    assert [result["size"] for result in measured] == sizes
    for result in measured:
        before, after = result["pending"]["cards"], result["loaded"]["cards"]
        where = f"{result['size']} (page {result['pending']['vw']} x {result['pending']['vh']})"
        assert result["settled"], f"{where}: the page did not settle: {after}"
        assert set(before) == set(after), f"{where}: cards came or went: {sorted(set(before) ^ set(after))}"
        assert {"by-model", "threads", "fast"} <= set(before)
        assert all(before[key]["state"] == "pending" for key in ("by-model", "threads", "fast")), \
            f"{where}: the page was not measured while it waited for its reports: {before}"
        assert all(card["state"] == "ready" for card in after.values()), f"{where}: {after}"
        moved = {key: (before[key]["h"], after[key]["h"]) for key in before if abs(before[key]["h"] - after[key]["h"]) > 1}
        assert not moved, f"{where}: cards changed height when their report landed (placeholder, then real): {moved}"


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


def read_devserver_page(child: subprocess.Popen, port: int, timeout: float = 30.0) -> bytes:
    """The page a dev server child serves at the address it prints (``devserver ... url=http://127.0.0.1:PORT/?k=KEY``).

    Also checks that the same page is refused to a caller without the key: the token is written into it.
    """
    box: queue.Queue[bytes] = queue.Queue()
    threading.Thread(target=lambda: box.put(child.stdout.readline()), daemon=True).start()
    line = box.get(timeout=timeout).decode("utf-8", "replace")
    found = re.search(r"\burl=(\S+)", line)
    assert found, f"the dev server printed no address: {line!r}"
    address = found.group(1)
    assert f"127.0.0.1:{port}/?k=" in address
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("GET", "/")
        bare = conn.getresponse()
        bare.read()
        assert bare.status == 401
        conn.request("GET", address.split(f":{port}", 1)[1])
        response = conn.getresponse()
        assert response.status == 200
        return response.read()
    finally:
        conn.close()


@ui_only
def test_devserver_process_answers_on_loopback(tmp_path: Path) -> None:
    """Starts the real dev server as a child process, as a reviewer would, and calls it."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = dict(os.environ, **REAL_PROFILE_ENV, CUM_APPDATA_DIR=str(tmp_path / "dev"))
    child = subprocess.Popen([sys.executable, "-m", "usage_monitor.ui.devserver", "--port", str(port),
                              "--scenario", "warning"], env=env, cwd=str(Path(__file__).resolve().parents[1]),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
    try:
        page = read_devserver_page(child, port, timeout=20.0)
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
