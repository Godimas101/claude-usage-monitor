"""Tests for usage_monitor.agent: the status file, --status, --hook and the MCP server (#24).

Offline: the usage API is never called (fetch_once is replaced), the app data folder is tmp_path,
and whether the app runs is passed in rather than read from the real mutex.
"""
from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

import main
from usage_monitor import app
from usage_monitor.agent import cli, mcp, status
from usage_monitor.data import service as svc
from usage_monitor.data.models import (
    ApiStatus,
    BurnRate,
    Limit,
    PlanInfo,
    Spend,
    UsageSnapshot,
)
from usage_monitor.data.pricing import Pricing
from usage_monitor.data.usage_api import FetchResult

UTC = timezone.utc
NOW = datetime(2026, 10, 24, 16, 0, 0, tzinfo=UTC)
PLAN = PlanInfo(subscription_type="max", rate_limit_tier="default_claude_max_5x", name="Max 5x",
                monthly_price_usd=100.0)
TOKEN = "tok-SYNTHETIC-0123456789"
REAL_FETCH_ONCE = status.fetch_once  # the autouse fixture replaces it


def snapshot(fetched_at: datetime = NOW, session: float = 30.0, weekly: float = 80.0) -> UsageSnapshot:
    return UsageSnapshot(fetched_at=fetched_at, limits=[
        Limit(kind="session", group="session", percent=session, severity="normal",
              resets_at=NOW + timedelta(hours=2), is_active=True, label="SESSION", window_seconds=18_000),
        Limit(kind="weekly_all", group="weekly", percent=weekly, severity="warning",
              resets_at=NOW + timedelta(days=4), is_active=True, label="WEEKLY", window_seconds=604_800),
    ], spend=Spend(enabled=True, used=12.5, limit=50.0, percent=25.0, currency="USD"))


def pacing(session: float = 30.0, weekly: float = 80.0, *, session_verdict: str = "SAFE",
           limit_at: str | None = None) -> dict:
    """A pacing_report() result: the week started 3 days ago, so 3/7 of it has gone."""
    week_start = NOW - timedelta(days=3)
    elapsed = 3 / 7 * 100
    return {
        "now": status._iso(NOW),
        "session": {"percent": session, "resets_at": status._iso(NOW + timedelta(hours=2)),
                    "window_start": status._iso(NOW - timedelta(hours=3)), "slope_pct_per_hour": 4.0,
                    "projected_limit_at": limit_at, "verdict": session_verdict, "points": [[1, 2]]},
        "weekly": {"percent": weekly, "resets_at": status._iso(week_start + timedelta(days=7)),
                   "window_start": status._iso(week_start), "elapsed_pct": elapsed,
                   "pace_delta_pp": weekly - elapsed, "verdict": "AHEAD", "daily_budget_pct": 5.0,
                   "even_daily_pct": 14.3, "projected_end_pct": weekly / (elapsed / 100), "points": []},
        "scoped": [{"label": "FABLE WEEKLY", "model": "Fable", "allowance_pct": None,
                    "used_pct_of_weekly": None, "percent": 0.0,
                    "resets_at": status._iso(week_start + timedelta(days=7)),
                    "window_start": status._iso(week_start), "elapsed_pct": elapsed,
                    "pace_delta_pp": -elapsed, "verdict": "BEHIND", "daily_budget_pct": 25.0,
                    "even_daily_pct": 14.3, "projected_end_pct": 0.0, "points": []}],
    }


def document(*, at: datetime = NOW, fetched_at: datetime | None = None, state: str = "live",
             source: str = status.SOURCE_MONITOR, session: float = 30.0, weekly: float = 80.0,
             **pace: Any) -> dict:
    return status.build(status=state, status_detail="Updated", usage=snapshot(fetched_at or at, session, weekly),
                        plan=PLAN, burn=BurnRate(tokens_per_min=1234.0, usd_per_hour=4.5, velocity="FAST"),
                        pacing=pacing(session, weekly, **pace), now=at, poll_interval_s=900.0,
                        source=source, app_version="2.1.0")


@pytest.fixture(autouse=True)
def offline(tmp_path, monkeypatch) -> list:
    """No test reads the real mutex, sign-in or history, or reaches the network: the app is closed,
    and a fetch fails as if offline unless a test asks for ``fetches``."""
    monkeypatch.setenv("CUM_APPDATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setattr(status, "monitor_running", lambda env=None: False)
    calls: list = []

    def failed(now, *, previous, app_version=""):
        calls.append(now)
        doc = dict(previous) if previous else {"schema": status.SCHEMA}
        doc.update(source=status.SOURCE_CLI, written_at=status._iso(now), status="offline", status_detail="offline")
        return doc

    monkeypatch.setattr(status, "fetch_once", failed)
    return calls


@pytest.fixture
def folder(tmp_path) -> Path:
    return tmp_path / "appdata"


@pytest.fixture
def fetches(monkeypatch) -> list:
    """Replace the one-shot fetch: it records the call and answers with a fresh CLI document."""
    calls: list = []

    def fake(now, *, previous, app_version=""):
        calls.append((now, previous))
        return document(at=now, source=status.SOURCE_CLI, session=55.0)

    monkeypatch.setattr(status, "fetch_once", fake)
    return calls


def save(folder: Path, doc: dict) -> None:
    status.write(doc, folder / status.STATUS_NAME)


# ── The document ─────────────────────────────────────────────────────────────────

def test_the_document_carries_each_limit_with_its_pace_and_no_history_points():
    doc = document()
    assert doc["schema"] == status.SCHEMA and doc["source"] == "monitor"
    assert doc["plan"] == {"name": "Max 5x", "tier": "default_claude_max_5x"}
    assert doc["session"]["percent"] == 30.0 and doc["session"]["verdict"] == "SAFE"
    assert "points" not in doc["session"] and "points" not in doc["weekly"]
    assert doc["weekly"]["verdict"] == "AHEAD"
    assert [entry["model"] for entry in doc["scoped"]] == ["Fable"]
    assert doc["burn"]["output_tokens_per_min"] == 1234.0
    assert doc["extra_usage"] == {"enabled": True, "used": 12.5, "limit": 50.0, "percent": 25.0, "currency": "USD"}
    assert doc["usage_fetched_at"] == "2026-10-24T16:00:00Z"


def test_the_weekly_limit_is_projected_from_its_average_pace_so_far():
    # 80% after 3 of 7 days: at that rate 100% falls at 3/7 * 100/80 of the window, 3.75 days in.
    doc = document()
    week_start = NOW - timedelta(days=3)
    assert doc["weekly"]["projected_limit_at"] == status._iso(week_start + timedelta(days=3.75))
    assert doc["scoped"][0]["projected_limit_at"] is None  # 0% never gets there


def test_no_projection_when_the_pace_lands_under_100_or_the_limit_is_already_reached():
    assert document(weekly=30.0)["weekly"]["projected_limit_at"] is None
    assert document(weekly=100.0)["weekly"]["projected_limit_at"] is None


def test_the_document_never_holds_the_token(tmp_path):
    doc = document()
    save(tmp_path, doc)
    assert TOKEN not in (tmp_path / status.STATUS_NAME).read_text(encoding="utf-8")
    assert "token" not in json.dumps(doc).lower().replace("tokens_per_min", "")


def test_write_then_read_round_trips_and_another_schema_reads_as_none(folder):
    save(folder, document())
    assert status.read(folder / status.STATUS_NAME) == document()
    (folder / status.STATUS_NAME).write_text(json.dumps({"schema": 99}), encoding="utf-8")
    assert status.read(folder / status.STATUS_NAME) is None
    assert status.read(folder / "missing.json") is None


def test_the_mutex_name_matches_the_app():
    assert status.MUTEX_NAME == app.MUTEX_NAME
    assert status.DEV_ENV == app.DEV_ENV


# ── Choosing a reading ───────────────────────────────────────────────────────────

def test_a_running_app_with_a_current_reading_is_read_as_it_is(folder, fetches):
    save(folder, document(at=NOW - timedelta(minutes=5)))
    doc, running = status.current(folder, now=NOW, fetch=True, running=True)
    assert running and doc["session"]["percent"] == 30.0
    assert fetches == []
    assert (folder / status.DEMAND_NAME).exists()  # every reader leaves its demand


def test_with_the_app_closed_an_old_reading_is_fetched_again_and_kept(folder, fetches):
    save(folder, document(at=NOW - timedelta(minutes=10)))
    doc, running = status.current(folder, now=NOW, fetch=True, running=False)
    assert not running and doc["session"]["percent"] == 55.0
    assert len(fetches) == 1
    assert status.read(folder / status.STATUS_NAME)["source"] == status.SOURCE_CLI


def test_a_young_reading_of_our_own_is_shared_instead_of_fetched_again(folder, fetches):
    save(folder, document(at=NOW - timedelta(seconds=30), source=status.SOURCE_CLI))
    doc, _running = status.current(folder, now=NOW, fetch=True, running=False)
    assert fetches == [] and doc["source"] == status.SOURCE_CLI


def test_a_running_app_that_writes_no_file_is_covered_by_a_fetch(folder, fetches):
    # An older app version runs (it holds the mutex) but writes no state.json.
    doc, running = status.current(folder, now=NOW, fetch=True, running=True)
    assert running and doc["session"]["percent"] == 55.0 and len(fetches) == 1


def test_a_running_app_that_reports_a_failure_is_left_to_its_own_retries(folder, fetches):
    save(folder, document(at=NOW - timedelta(hours=2), state="rate_limited"))
    doc, _running = status.current(folder, now=NOW, fetch=True, running=True)
    assert doc["status"] == "rate_limited" and fetches == []  # a request of ours would add to the 429
    save(folder, document(at=NOW - timedelta(hours=2), state="live"))  # live but not writing: it hung
    status.current(folder, now=NOW, fetch=True, running=True)
    assert len(fetches) == 1


def test_without_fetch_a_missing_or_old_reading_is_returned_as_it_is(folder, fetches):
    assert status.current(folder, now=NOW, fetch=False, running=False) == (None, False)
    save(folder, document(at=NOW - timedelta(hours=3)))
    doc, _running = status.current(folder, now=NOW, fetch=False, running=False)
    assert doc["session"]["percent"] == 30.0 and fetches == []


def test_a_paused_reading_is_waited_on_until_the_app_writes_again(folder, fetches, monkeypatch):
    save(folder, document(at=NOW - timedelta(minutes=2), state="paused"))
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:  # the app's next cycle lands during the wait
        sleeps.append(seconds)
        save(folder, document(at=NOW, session=31.0))

    monkeypatch.setattr(status.time, "sleep", sleep)
    doc, _running = status.current(folder, now=NOW, fetch=True, wait_s=5.0, running=True)
    assert doc["status"] == "live" and doc["session"]["percent"] == 31.0
    assert sleeps and fetches == []


def test_a_failed_fetch_keeps_the_old_reading_and_says_why(folder, monkeypatch):
    from usage_monitor.data import usage_api

    class Offline:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def fetch(self, now: datetime) -> FetchResult:
            return FetchResult(status=ApiStatus.OFFLINE, detail="next try at 16:01", snapshot=None, fresh=False)

    monkeypatch.setattr(usage_api, "UsageClient", Offline)
    old = document(at=NOW - timedelta(hours=1))
    doc = REAL_FETCH_ONCE(NOW, previous=old)
    assert doc["status"] == "offline" and doc["status_detail"] == "next try at 16:01"
    assert doc["session"] == old["session"] and doc["source"] == status.SOURCE_CLI
    assert doc["written_at"] == status._iso(NOW)  # so the next caller within FRESH_S does not retry


def test_demand_lasts_for_demand_s(folder):
    assert not status.demanded(folder)
    status.touch_demand(folder)
    mtime = (folder / status.DEMAND_NAME).stat().st_mtime
    assert status.demanded(folder, mtime + status.DEMAND_S - 1)
    assert not status.demanded(folder, mtime + status.DEMAND_S + 1)


# ── The summary line ─────────────────────────────────────────────────────────────

def test_the_summary_names_each_limit_its_reset_pace_and_the_burn():
    line = status.summary(document(), now=NOW + timedelta(minutes=3), running=True)
    assert line.startswith("Claude plan usage (Max 5x, reading from 3m ago): session 30%, resets in 1h 57m")
    assert "safe at this rate" in line
    assert "weekly 80%, resets in 3d 23h" in line and "37 pts ahead of an even pace" in line
    assert "at its average pace so far 100% is reached in" in line
    assert "Fable weekly 0%" in line and "43 pts under an even pace" in line
    assert "burn 1.2k output tokens/min (fast)" in line
    assert "STALE" not in line


def test_the_summary_warns_when_the_session_is_at_risk_or_limited():
    at_risk = document(session=90.0, session_verdict="AT_RISK", limit_at=status._iso(NOW + timedelta(minutes=40)))
    assert "at this rate the limit is hit in 40m" in status.summary(at_risk, now=NOW, running=True)
    limited = document(session=100.0, session_verdict="LIMITED")
    assert "session 100%, resets in 2h 0m" in status.summary(limited, now=NOW, running=True)
    assert "LIMIT REACHED" in status.summary(limited, now=NOW, running=True)


def test_an_old_reading_is_marked_stale_with_its_status():
    doc = document(at=NOW - timedelta(hours=2), state="offline")
    line = status.summary(doc, now=NOW, running=True)
    assert "STALE, reading from 2h 0m ago" in line and "[offline: Updated]" in line
    assert status.is_stale(doc, NOW)
    assert not status.is_stale(document(at=NOW - timedelta(minutes=20)), NOW)


def test_a_reset_that_has_passed_is_said_plainly():
    line = status.summary(document(), now=NOW + timedelta(hours=3), running=True)  # the session reset at +2h
    assert "(window reset 1h 0m ago" in line and "no newer reading" in line


def test_no_reading_says_why():
    assert "Claude Usage Monitor is not running" in status.summary(None, now=NOW, running=False)
    assert "has not fetched one yet" in status.summary(None, now=NOW, running=True)


def test_the_report_adds_age_staleness_and_the_summary():
    out = status.report(document(at=NOW - timedelta(minutes=4)), now=NOW, running=True)
    assert out["monitor_running"] is True and out["usage_age_s"] == 240 and out["stale"] is False
    assert out["summary"].startswith("Claude plan usage")
    assert status.report(None, now=NOW, running=False)["stale"] is True


# ── The data service writes it ───────────────────────────────────────────────────

class OneClient:
    next_allowed_at = None

    def fetch(self, now: datetime) -> FetchResult:
        return FetchResult(status=ApiStatus.LIVE, detail="Updated", snapshot=snapshot(now), fresh=True)


def run_cycle(tmp_path: Path, folder: Path | None, *, paused: bool = False) -> svc.DataService:
    from usage_monitor.settings import Settings

    settings = Settings(tmp_path / "settings.json", save_delay_s=60)
    service = svc.DataService(settings, store_path=tmp_path / "usage.db", pricing=Pricing.load(),
                              usage_client=OneClient(), roots=[tmp_path], clock=lambda: NOW,
                              pause_check=lambda: paused, pricing_refresh=lambda _cached: False,
                              status_dir=folder)
    try:
        service._safe_cycle()
    finally:
        service._close_store()
        settings.close()
    return service


def test_every_cycle_writes_the_status_file_when_given_a_folder(tmp_path, folder):
    run_cycle(tmp_path, folder)
    doc = status.read(folder / status.STATUS_NAME)
    assert doc["status"] == "live" and doc["session"]["percent"] == 30.0
    assert doc["poll_interval_s"] == 900 and doc["weekly"]["verdict"] in ("AHEAD", "ON_PACE", "BEHIND")


def test_without_a_folder_nothing_is_written(tmp_path, monkeypatch):
    monkeypatch.setenv("CUM_APPDATA_DIR", str(tmp_path / "elsewhere"))
    run_cycle(tmp_path, None)
    assert not (tmp_path / "elsewhere" / status.STATUS_NAME).exists()


def test_a_locked_screen_pauses_unless_a_session_asked_lately(tmp_path, folder):
    run_cycle(tmp_path, folder, paused=True)
    assert status.read(folder / status.STATUS_NAME)["status"] == "paused"
    status.touch_demand(folder)
    run_cycle(tmp_path, folder, paused=True)
    assert status.read(folder / status.STATUS_NAME)["status"] == "live"


# ── --status and --hook ──────────────────────────────────────────────────────────

def test_status_json_prints_the_report(folder, fetches):
    save(folder, document(at=NOW))
    out = io.BytesIO()
    code = cli.run_status(as_json=True, folder=folder, clock=lambda: NOW, app_version="2.1.0", out=out)
    report = json.loads(out.getvalue())
    assert code == 0 and report["session"]["percent"] in (30.0, 55.0) and "summary" in report


def hook(folder: Path, event: dict | None, *, threshold: float = 80.0, always: bool = False,
         now: datetime = NOW) -> dict | None:
    out = io.BytesIO()
    stdin = io.BytesIO(json.dumps(event).encode()) if event is not None else None
    assert cli.run_hook(threshold=threshold, always=always, folder=folder, clock=lambda: now,
                        app_version="2.1.0", stdin=stdin, out=out) == 0
    return json.loads(out.getvalue()) if out.getvalue() else None


PROMPT = {"session_id": "s1", "hook_event_name": "UserPromptSubmit", "prompt": "hi"}


def test_the_hook_is_silent_below_the_threshold(folder):
    save(folder, document(weekly=60.0))
    assert hook(folder, PROMPT) is None


def test_the_hook_speaks_once_per_step_and_again_in_a_new_session(folder):
    save(folder, document(weekly=81.0))
    said = hook(folder, PROMPT)
    assert said["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert said["hookSpecificOutput"]["additionalContext"].startswith("Claude plan usage (Max 5x")
    assert hook(folder, PROMPT) is None                      # same step: quiet
    save(folder, document(weekly=86.0))
    assert hook(folder, PROMPT) is not None                  # climbed a step
    assert hook(folder, {**PROMPT, "session_id": "s2"}) is not None


def test_the_hook_speaks_every_time_with_always_and_tolerates_bad_input(folder):
    save(folder, document(weekly=10.0))
    start = {"session_id": "s1", "hook_event_name": "SessionStart", "source": "startup"}
    assert hook(folder, start, always=True)["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert hook(folder, None, always=True)["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    out = io.BytesIO()
    assert cli.run_hook(threshold=80.0, always=True, folder=folder, clock=lambda: NOW, app_version="",
                        stdin=io.BytesIO(b"not json"), out=out) == 0


def test_the_hook_stays_silent_with_no_reading(folder):
    assert hook(folder, PROMPT, always=True) is None


# ── The MCP server ───────────────────────────────────────────────────────────────

def rpc(folder: Path, *messages: Any) -> list:
    stdin = io.BytesIO(b"".join((m if isinstance(m, bytes) else json.dumps(m).encode()) + b"\n" for m in messages))
    stdout = io.BytesIO()
    assert mcp.serve(stdin, stdout, folder=folder, clock=lambda: NOW, app_version="2.1.0") == 0
    return [json.loads(line) for line in stdout.getvalue().splitlines()]


def test_initialize_echoes_a_known_version_and_offers_its_own_otherwise(folder):
    first, second = rpc(folder,
                        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
                        {"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}})
    assert first["result"]["protocolVersion"] == "2025-03-26"
    assert first["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert first["result"]["serverInfo"]["name"] == mcp.SERVER_NAME
    assert second["result"]["protocolVersion"] == mcp.DEFAULT_VERSION


def test_notifications_get_no_reply_and_unknown_methods_an_error(folder):
    replies = rpc(folder, {"jsonrpc": "2.0", "method": "notifications/initialized"},
                  {"jsonrpc": "2.0", "id": 7, "method": "resources/list"}, b"{broken",
                  {"jsonrpc": "2.0", "id": 8, "method": "ping"})
    assert [reply.get("id") for reply in replies] == [7, None, 8]
    assert replies[0]["error"]["code"] == -32601 and replies[1]["error"]["code"] == -32700
    assert replies[2]["result"] == {}


def test_tools_list_and_call_return_the_reading(folder, fetches):
    save(folder, document(at=NOW))
    listed, called, wrong = rpc(folder, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                                {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                 "params": {"name": "get_plan_usage", "arguments": {}}},
                                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "nope"}})
    assert [tool["name"] for tool in listed["result"]["tools"]] == ["get_plan_usage"]
    result = called["result"]
    assert result["isError"] is False
    assert result["content"][0]["text"].startswith("Claude plan usage")
    assert result["structuredContent"]["schema"] == status.SCHEMA
    assert wrong["error"]["code"] == -32602


# ── The command line ─────────────────────────────────────────────────────────────

def test_parse_args_reads_the_agent_modes():
    assert main.parse_args(["--status", "--json"]) == main.Command("status", as_json=True)
    assert main.parse_args(["--hook", "--threshold", "70", "--always"]) == main.Command("hook", threshold=70.0, always=True)
    assert main.parse_args(["--hook", "--threshold", "lots"]).threshold is None  # never exit 2: it blocks the prompt
    assert main.parse_args(["--mcp"]).kind == "mcp"


# ── --gate ───────────────────────────────────────────────────────────────────────

def gate(folder: Path, *, session_cap: float = 95.0, weekly_cap: float = 85.0, now: datetime = NOW) -> dict | None:
    out = io.BytesIO()
    event = {"session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": "Agent", "tool_input": {}}
    assert cli.run_gate(session_cap=session_cap, weekly_cap=weekly_cap, folder=folder, clock=lambda: now,
                        app_version="2.1.0", stdin=io.BytesIO(json.dumps(event).encode()), out=out) == 0
    return json.loads(out.getvalue()) if out.getvalue() else None


def test_the_gate_lets_tools_run_under_the_caps(folder):
    save(folder, document(weekly=84.0))
    assert gate(folder) is None


def test_the_gate_refuses_new_agents_at_a_cap_and_says_when_it_resets(folder):
    save(folder, document(weekly=86.0))
    decision = gate(folder)["hookSpecificOutput"]
    assert decision["hookEventName"] == "PreToolUse" and decision["permissionDecision"] == "deny"
    assert "weekly usage is 86%, at or past the 85% cap; it resets in 4d 0h" in decision["permissionDecisionReason"]


def test_the_gate_ignores_a_limit_whose_window_has_reset_and_fails_open(folder, monkeypatch):
    save(folder, document(session=99.0))
    assert gate(folder, now=NOW + timedelta(hours=2, minutes=1), weekly_cap=200.0) is None  # the session reset
    monkeypatch.setattr(status, "current", lambda *_a, **_k: 1 / 0)
    assert gate(folder) is None


def test_parse_args_reads_the_gate_caps():
    assert main.parse_args(["--gate", "--weekly", "85"]) == main.Command("gate", weekly_cap=85.0)
