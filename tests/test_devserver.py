"""Tests for usage_monitor/ui/devserver.py: the synthetic history, --db, --theme and the command line.

Offline: the dev server listens on loopback only, settings go to a temporary folder (a dev server never
touches the sign-in entry), and the synthetic year is built small (HISTORY_SCALE) so each test stays fast.
Starting the server as a child process is skipped unless CUM_UI_TESTS=1.
"""
from __future__ import annotations

import csv
import http.client
import io
import json
import os
import queue
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time
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
from usage_monitor.ui import devserver
from usage_monitor.ui.server import ApiServer

UI_TESTS = os.environ.get("CUM_UI_TESTS") == "1"
ui_only = pytest.mark.skipif(not UI_TESTS, reason="needs CUM_UI_TESTS=1 (starts a child process)")
# The child process needs the real profile (pywebview and Pillow live in the user site-packages).
REAL_PROFILE_ENV = {k: os.environ[k] for k in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME", "PYTHONUSERBASE")
                    if k in os.environ}
TOKEN_HEADER = "X-Monitor-Token"
PRICING = Pricing.load()
SMALL_YEAR = 0.1                    # the share of a session's requests the tests build; days and streaks stay
NOW = datetime(2026, 10, 21, 16, 12, tzinfo=timezone.utc)
REPORTS = ("cost", "efficiency", "pacing", "history", "sessions")


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUM_APPDATA_DIR", str(tmp_path / "app"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.delenv("CUM_DEV_INSTANCE", raising=False)
    monkeypatch.setattr(devserver, "HISTORY_SCALE", SMALL_YEAR)


def get(server: ApiServer, path: str, *, method: str = "GET") -> tuple[int, dict[str, str], bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=30)
    try:
        conn.request(method, path, headers={TOKEN_HEADER: server.token})
        response = conn.getresponse()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
    finally:
        conn.close()


def get_json(server: ApiServer, path: str) -> Any:
    status, _headers, body = get(server, path)
    assert status == 200, body[:200]
    return json.loads(body)


@pytest.fixture
def started(tmp_path: Path) -> Iterator[Callable[..., ApiServer]]:
    """make_server + start, stopped (with its settings closed) when the test ends."""
    running: list[tuple[ApiServer, Settings]] = []

    def build(scenario: str = "normal", **kwargs: Any) -> ApiServer:
        kwargs.setdefault("clock", lambda: NOW)     # the fake state, the invented year and the reports share one instant
        server, settings = devserver.make_server(scenario, 0, settings_dir=tmp_path / "dev", **kwargs)
        server.start()
        running.append((server, settings))
        return server

    yield build
    for server, settings in running:
        server.stop()
        settings.close()


def usage_db(path: Path, requests: int = 3) -> Path:
    """A real usage database with a few requests, standing in for a copy of the owner's usage.db."""
    with Store(path) as store:
        store.add_items([
            m.RequestRecord(
                key=f"k{n}", message_id=f"msg{n}", request_id=f"req{n}", ts=NOW - timedelta(days=n, hours=1),
                session_id=f"s{n}", project="alpha", cwd="C:/work/alpha", git_branch="main", is_subagent=False,
                agent_id=None, model="claude-opus-5-5", input_tokens=1_000_000)
            for n in range(requests)
        ], PRICING)
    return path


# ── The synthetic history (no --db) ───────────────────────────────────────────────

def assert_all_time_breakdowns(cost: dict[str, Any]) -> None:
    """What the breakdown cards need from the invented year. It reads the all-time window: the default one is
    the current billing cycle, which holds only a few sessions early in a month, so asserting on it failed on
    about a third of the days of the year (and in some time zones, on today's)."""
    breakdowns = cost["breakdowns"]
    assert {row["name"] for row in breakdowns["by_model"]} >= {"Opus 5.5", "Sonnet 5.5"}
    assert len(breakdowns["by_project"]) >= 5 and len(breakdowns["by_folder"]) >= 5
    assert breakdowns["fast"]["requests"] > 0 and breakdowns["by_thread"]["subagent"]["requests"] > 0


def test_every_statistics_route_has_data_without_a_database(started: Callable[..., ApiServer]) -> None:
    server = started()
    cost = get_json(server, "/api/analytics/cost")
    assert cost["windows"]["all"]["requests"] > 1000 and cost["windows"]["session"]["requests"] > 0
    assert len(cost["monthly"]) >= 12 and all(row["multiplier"] is not None for row in cost["monthly"])
    assert cost["plan"]["name"] == "Max 5x" and len(cost["top_sessions_week"]) >= 3
    assert_all_time_breakdowns(get_json(server, "/api/analytics/cost?window=all"))

    efficiency = get_json(server, "/api/analytics/efficiency?window=all")
    assert 0.5 < efficiency["cache"]["hit_rate"] < 1 and efficiency["thinking"]["share"] > 0
    assert efficiency["tools"]["errors"] > 0 and efficiency["tools"]["mcp"] and len(efficiency["tools"]["builtin"]) > 3
    assert efficiency["productivity"]["commits"] > 0 and efficiency["productivity"]["lines_added"] > 0
    assert efficiency["compactions"]["count"] > 0 and efficiency["api_errors"] >= 0 and len(efficiency["effort"]) >= 3

    history = get_json(server, "/api/analytics/history")
    assert len(history["days"]) == 371 and history["active_days"] > 100
    assert history["streaks"]["current_days"] >= 8 and history["streaks"]["longest_days"] >= 20
    assert history["busiest_day"]["cost_usd"] > 0 and history["first_use"]
    assert sum(sum(row) for row in history["hour_weekday"]["requests"]) > 0

    sessions = get_json(server, "/api/analytics/sessions?window=all&limit=50")["sessions"]
    assert len(sessions) == 50 and any(s["title"] for s in sessions) and any(s["title"] is None for s in sessions)
    assert all(s["project_name"] and s["models"] for s in sessions)


@pytest.mark.parametrize("day", [(2026, 1, 3), (2026, 2, 1), (2026, 11, 2), (2026, 12, 1), (2027, 3, 31)])
def test_the_invented_year_serves_the_breakdowns_on_any_day_of_the_year(
        started: Callable[..., ApiServer], day: tuple[int, int, int]) -> None:
    """These dates failed the old, unpinned test: a cycle that has only a day or two of data has no fast-mode
    request (2.5 % of the Opus ones) and fewer than five projects."""
    moment = datetime(*day, 9, 30, tzinfo=timezone.utc)
    server = started(clock=lambda: moment)
    assert_all_time_breakdowns(get_json(server, "/api/analytics/cost?window=all"))
    assert get_json(server, "/api/analytics/history")["days"][-1]["date"] == moment.astimezone().date().isoformat()


def test_the_clock_pins_the_state_the_history_and_the_reports_to_one_instant(started: Callable[..., ApiServer]) -> None:
    server = started()
    assert get_json(server, "/api/state")["state"]["updated_at"] == "2026-10-21T16:11:48.000Z"      # NOW - 12 s
    assert get_json(server, "/api/analytics/pacing")["now"] == "2026-10-21T16:12:00Z"
    history = get_json(server, "/api/analytics/history")
    assert history["days"][-1]["date"] == NOW.astimezone().date().isoformat()


def test_the_synthetic_pacing_has_points_and_a_verdict_for_each_limit(started: Callable[..., ApiServer]) -> None:
    pacing = get_json(started(), "/api/analytics/pacing")
    assert pacing["session"]["verdict"] == "SAFE" and len(pacing["session"]["points"]) >= 10
    assert pacing["session"]["slope_pct_per_hour"] > 0
    assert pacing["weekly"]["verdict"] in ("BEHIND", "ON_PACE", "AHEAD") and len(pacing["weekly"]["points"]) >= 50
    assert [item["label"] for item in pacing["scoped"]] == ["FABLE WEEKLY"] and len(pacing["scoped"][0]["points"]) >= 50


def test_the_critical_scenario_is_at_risk_of_hitting_the_limit(started: Callable[..., ApiServer]) -> None:
    pacing = get_json(started("critical"), "/api/analytics/pacing")
    assert pacing["session"]["verdict"] == "AT_RISK" and pacing["session"]["projected_limit_at"]
    assert pacing["session"]["percent"] == 93.0


def test_the_synthetic_year_is_the_same_every_time(monkeypatch: pytest.MonkeyPatch) -> None:
    counts = []
    for _ in range(2):
        history = devserver.SyntheticHistory("normal", now=lambda: NOW)
        store = history.open()
        counts.append(store.table_counts())
        store.close()
    assert counts[0] == counts[1] and counts[0]["requests"] > 1000


def test_the_synthetic_data_is_priced_and_uses_only_models_with_a_price(monkeypatch: pytest.MonkeyPatch) -> None:
    history = devserver.SyntheticHistory("normal", now=lambda: NOW)
    store = history.open()
    try:
        assert store.unpriced_models() == []
        assert store.first_request_ts() is not None and store.first_request_ts() < NOW - timedelta(days=300)
        assert store.query("SELECT MAX(ts) AS last FROM requests")[0]["last"] < NOW.timestamp()
    finally:
        store.close()


def test_usage_readings_follow_the_sliding_reset_times_without_piling_up(monkeypatch: pytest.MonkeyPatch) -> None:
    history = devserver.SyntheticHistory("normal", now=lambda: NOW)
    store = history.open()
    try:
        rows = store.query("SELECT COUNT(*) AS n FROM usage_snapshots")[0]["n"]
        assert rows > 100
        history.sync(devserver.fake_state("normal", NOW))                                   # nothing moved
        assert store.query("SELECT COUNT(*) AS n FROM usage_snapshots")[0]["n"] == rows
        later = NOW + timedelta(minutes=1)                                                  # the fake windows slide
        state = devserver.fake_state("normal", later)
        history.sync(state)
        grown = store.query("SELECT COUNT(*) AS n FROM usage_snapshots")[0]["n"]
        assert rows <= grown <= rows + 3, "the same time grid is written again"
        pacing = analytics.pacing_report(store, now=later, usage=state.usage)
        assert len(pacing["session"]["points"]) >= 10 and pacing["session"]["verdict"] == "SAFE"
        history.sync(state)                                                                 # and once is enough
        assert store.query("SELECT COUNT(*) AS n FROM usage_snapshots")[0]["n"] == grown
    finally:
        store.close()


def test_syncing_after_the_server_closed_the_store_is_harmless() -> None:
    history = devserver.SyntheticHistory("normal", now=lambda: NOW)
    history.open().close()
    history.sync(devserver.fake_state("normal", NOW + timedelta(minutes=3)))                # no exception
    history.sync(devserver.fake_state("normal", NOW + timedelta(minutes=4)))
    fresh = history.open()                                                                   # and it builds a new one
    try:
        assert fresh.query("SELECT COUNT(*) AS n FROM requests")[0]["n"] > 1000
    finally:
        fresh.close()


def test_warming_builds_the_history_once_and_the_first_request_reuses_it(started: Callable[..., ApiServer],
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    builds: list[int] = []
    real = devserver._History.build

    def counting(self: Any) -> Any:
        builds.append(1)
        return real(self)

    monkeypatch.setattr(devserver._History, "build", counting)
    server = started(warm=True)
    assert builds == [1]
    assert get_json(server, "/api/analytics/cost")["windows"]["all"]["requests"] > 1000
    assert builds == [1], "the warmed store was handed to the server, not built again"


def test_the_history_is_built_only_when_the_statistics_are_first_asked_for(started: Callable[..., ApiServer],
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    builds: list[int] = []
    monkeypatch.setattr(devserver._History, "build", lambda self: builds.append(1) or [])
    server = started()
    assert get(server, "/api/state")[0] == 200 and builds == []
    assert get(server, "/api/analytics/pacing")[0] == 200 and builds == [1]


def test_a_store_the_server_dropped_is_rebuilt_on_the_next_request(started: Callable[..., ApiServer],
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    server = started()
    assert get(server, "/api/analytics/cost")[0] == 200
    server._reader._discard_locked()                                      # what a failed query does
    assert get(server, "/api/analytics/history?days=7")[0] == 200


def test_the_export_comes_from_the_synthetic_history_too(started: Callable[..., ApiServer]) -> None:
    status, headers, body = get(started(), "/api/export?format=csv")
    assert status == 200 and headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(body.decode("utf-8"), newline="")))
    assert len(rows) > 100 and float(rows[-1]["cost_usd"]) > 0


def test_the_existing_scenarios_are_unchanged() -> None:
    assert devserver.SCENARIOS == ("normal", "warning", "critical", "stale")
    state = devserver.fake_state("normal", NOW)
    assert state.windows["month"].totals.cost_usd == pytest.approx(1659.14, abs=0.05)


# ── --db ──────────────────────────────────────────────────────────────────────────

def test_a_database_is_served_instead_of_the_synthetic_history(started: Callable[..., ApiServer], tmp_path: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("the synthetic history must not be built when a database is named")

    monkeypatch.setattr(devserver, "SyntheticHistory", forbidden)
    server = started(db=usage_db(tmp_path / "copy.db", requests=3))
    cost = get_json(server, "/api/analytics/cost")
    assert cost["windows"]["all"]["requests"] == 3
    assert cost["windows"]["all"]["tokens"]["input"] == 3_000_000
    rows = list(csv.DictReader(io.StringIO(get(server, "/api/export?format=csv")[2].decode("utf-8"), newline="")))
    assert sum(int(row["requests"]) for row in rows) == 3
    for name in REPORTS:
        assert get(server, f"/api/analytics/{name}")[0] == 200


def test_the_database_is_opened_on_the_first_request_and_closed_when_the_server_stops(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[Store] = []
    real_init = Store.__init__

    def tracking(self: Store, *args: Any, **kwargs: Any) -> None:
        real_init(self, *args, **kwargs)
        opened.append(self)

    path = usage_db(tmp_path / "copy.db")
    monkeypatch.setattr(Store, "__init__", tracking)
    server, settings = devserver.make_server("normal", 0, settings_dir=tmp_path / "dev", db=path)
    server.start()
    try:
        assert opened == []
        assert get(server, "/api/analytics/pacing")[0] == 200 and len(opened) == 1
    finally:
        server.stop()
        settings.close()
    with pytest.raises(Exception):
        opened[0].query("SELECT 1")                                        # closed, so the copy can be deleted
    path.unlink()


def test_reading_a_database_changes_nothing_in_it(started: Callable[..., ApiServer], tmp_path: Path) -> None:
    path = usage_db(tmp_path / "copy.db")
    with Store(path) as store:
        before = {table: store.query(f'SELECT COUNT(*) AS n FROM "{table}"')[0]["n"] for table in store.table_counts()}
        tables = store.table_counts()
    server = started(db=path)
    for name in REPORTS:
        assert get(server, f"/api/analytics/{name}")[0] == 200
    assert get(server, "/api/export?format=json")[0] == 200
    with Store(path) as store:
        assert store.table_counts() == tables == before


def set_schema_version(path: Path, version: int) -> None:
    connection = sqlite3.connect(path)
    connection.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(version),))
    connection.commit()
    connection.close()


def schema_version(path: Path) -> str:
    connection = sqlite3.connect(path)
    try:
        return connection.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0]
    finally:
        connection.close()


def test_a_database_from_an_older_schema_is_refused_not_upgraded(started: Callable[..., ApiServer],
                                                                 tmp_path: Path) -> None:
    """Opening it would migrate it from the API's thread; only the data service upgrades a usage database."""
    path = usage_db(tmp_path / "old.db")
    set_schema_version(path, 2)
    server = started(db=path)
    status, _headers, body = get(server, "/api/analytics/cost")
    assert status == 503 and json.loads(body) == {"error": "the statistics database could not be opened"}
    assert get(server, "/api/export?format=csv")[0] == 503
    assert schema_version(path) == "2"


def test_a_database_from_a_newer_build_is_refused(started: Callable[..., ApiServer], tmp_path: Path) -> None:
    path = usage_db(tmp_path / "new.db")
    set_schema_version(path, 99)
    assert get(started(db=path), "/api/analytics/history")[0] == 503
    assert schema_version(path) == "99"


def test_a_database_that_is_not_there_is_not_created_by_asking_for_it(started: Callable[..., ApiServer],
                                                                      tmp_path: Path) -> None:
    missing = tmp_path / "typo.db"
    assert get(started(db=missing), "/api/analytics/pacing")[0] == 503
    assert not missing.exists() and not list(tmp_path.glob("typo.db*"))


def test_a_database_that_appears_later_is_picked_up_by_the_next_request(started: Callable[..., ApiServer],
                                                                       tmp_path: Path) -> None:
    path = tmp_path / "later.db"
    server = started(db=path)
    assert get(server, "/api/analytics/cost")[0] == 503
    usage_db(path, requests=2)
    assert get_json(server, "/api/analytics/cost")["windows"]["all"]["requests"] == 2


def test_a_database_without_a_state_reading_still_shows_the_fake_states_limits(
        started: Callable[..., ApiServer], tmp_path: Path) -> None:
    pacing = get_json(started("warning", db=usage_db(tmp_path / "copy.db")), "/api/analytics/pacing")
    assert pacing["session"]["percent"] == 72.0 and pacing["weekly"]["percent"] == 21.0
    assert len(pacing["session"]["points"]) == 1                    # the copy's snapshots are old: only the live one


# ── --theme ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("spec, theme_id, variant", [
    ("nostromo", "nostromo", "amber"),
    ("nostromo/green", "nostromo", "green"),
    ("night_city", "night_city", "blackwall"),
    ("night_city/edgerun", "night_city", "edgerun"),
    ("gates", "gates", "system-dark"),
    ("gates/blue-light", "gates", "blue-light"),
    (" nostromo/white ", "nostromo", "white"),
])
def test_theme_settings_name_the_theme_and_a_variant(spec: str, theme_id: str, variant: str) -> None:
    assert devserver.theme_settings(spec) == {"theme_id": theme_id, "theme_variant": variant}


def test_an_unknown_theme_lists_the_known_ones() -> None:
    with pytest.raises(ValueError, match="unknown theme 'neon'") as caught:
        devserver.theme_settings("neon/pink")
    assert "nostromo" in str(caught.value) and "gates" in str(caught.value)


@pytest.mark.parametrize("spec, theme_id, variant, mode", [
    ("night_city/edgerun", "night_city", "edgerun", "dark"),
    ("gates/blue-light", "gates", "blue-light", "light"),
    ("gates/teal-dark", "gates", "teal-dark", "dark"),
    ("nostromo", "nostromo", "amber", "dark"),
])
def test_the_theme_is_what_the_page_starts_in(started: Callable[..., ApiServer], spec: str, theme_id: str,
                                              variant: str, mode: str) -> None:
    server = started(theme=spec)
    theme = get_json(server, "/api/state")["theme"]
    assert (theme["id"], theme["variant"], theme["mode"]) == (theme_id, variant, mode)
    assert theme["css_vars"]["--bg"]


def test_a_theme_without_a_variant_does_not_inherit_the_last_runs_choice(tmp_path: Path) -> None:
    folder = tmp_path / "dev"
    earlier = Settings(folder / "devserver-settings.json", save_delay_s=0.0)
    earlier.update({"theme_id": "nostromo", "theme_variant": "green"})
    earlier.close()
    server, settings = devserver.make_server("normal", 0, settings_dir=folder, theme="nostromo")
    try:
        assert settings.get("theme_variant") == "amber"
    finally:
        settings.close()
        server.stop()


@pytest.mark.parametrize("spec", ["neon", "nostromo/nonesuch", "gates/amber", "night_city/blue-dark"])
def test_a_theme_that_does_not_exist_is_refused_and_leaves_nothing_open(tmp_path: Path, spec: str) -> None:
    with pytest.raises(ValueError):
        devserver.make_server("normal", 0, settings_dir=tmp_path / "dev", theme=spec)


# ── The command line ──────────────────────────────────────────────────────────────

def interrupted() -> None:
    """The wait that ends at once, as Ctrl+C would end it."""
    raise KeyboardInterrupt


def test_main_serves_until_interrupted_and_says_where(capsys: pytest.CaptureFixture[str]) -> None:
    assert devserver.main(["--port", "0", "--scenario", "warning", "--theme", "night_city/mox"],
                          wait=interrupted) == 0
    line = capsys.readouterr().out.strip()
    assert line.startswith("devserver scenario=warning url=http://127.0.0.1:") and line.endswith("data=synthetic")


def test_main_names_the_database_it_serves(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = usage_db(tmp_path / "copy.db")
    assert devserver.main(["--port", "0", "--db", str(path)], wait=interrupted) == 0
    assert capsys.readouterr().out.strip().endswith(f"data={path}")


def test_main_refuses_a_database_that_is_not_there_without_creating_one(tmp_path: Path,
                                                                        capsys: pytest.CaptureFixture[str]) -> None:
    missing = tmp_path / "typo.db"
    with pytest.raises(SystemExit) as caught:
        devserver.main(["--db", str(missing)])
    assert caught.value.code == 2 and "--db" in capsys.readouterr().err
    assert not missing.exists() and not list(tmp_path.glob("typo.db*"))


def test_main_refuses_a_folder_as_a_database(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        devserver.main(["--db", str(tmp_path)])
    assert caught.value.code == 2 and "--db" in capsys.readouterr().err


def test_main_refuses_an_unknown_theme(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        devserver.main(["--theme", "neon"])
    assert caught.value.code == 2
    assert "--theme" in capsys.readouterr().err


def test_main_still_refuses_an_unknown_scenario(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        devserver.main(["--scenario", "sunny"])
    assert caught.value.code == 2


# ── The real thing, as a child process (CUM_UI_TESTS=1) ───────────────────────────

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
def test_the_devserver_process_serves_a_database_copy_with_a_theme(tmp_path: Path) -> None:
    path = usage_db(tmp_path / "copy.db")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = dict(os.environ, **REAL_PROFILE_ENV, CUM_APPDATA_DIR=str(tmp_path / "dev"))
    env.pop("CUM_DEV_INSTANCE", None)
    child = subprocess.Popen(
        [sys.executable, "-m", "usage_monitor.ui.devserver", "--port", str(port), "--db", str(path),
         "--theme", "gates/blue-light"],
        env=env, cwd=str(Path(__file__).resolve().parents[1]), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))        # python is a console program: no window
    try:
        page = read_devserver_page(child, port)
        assert b'name="monitor-token" content="' in page
        token = page.decode("utf-8").split('name="monitor-token" content="', 1)[1].split('"', 1)[0]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/api/analytics/cost", headers={TOKEN_HEADER: token})
        response = conn.getresponse()
        assert response.status == 200 and json.loads(response.read())["windows"]["all"]["requests"] == 3
        conn.request("GET", "/api/state", headers={TOKEN_HEADER: token})
        theme = json.loads(conn.getresponse().read())["theme"]
        assert (theme["id"], theme["variant"], theme["mode"]) == ("gates", "blue-light", "light")
        conn.close()
    finally:
        child.terminate()
        child.wait(timeout=10)
