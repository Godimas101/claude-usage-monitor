"""Tests for usage_monitor.agent: the status file, --status, --hook and the MCP server (#24), and the usage
guard's notice and --wait (#27).

Offline: the usage API is never called (fetch_once is replaced), the app data folder is tmp_path,
and whether the app runs is passed in rather than read from the real mutex.
"""
from __future__ import annotations

import io
import json
import os
import sys
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

import main
from usage_monitor import app
from usage_monitor.agent import cli, guard, mcp, sessions, status
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
REAL_FETCH_ONCE = status.fetch_once  # the autouse fixture replaces these two
REAL_MONITOR_RUNNING = status.monitor_running


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
    monkeypatch.setenv("USAGE_MONITOR_APPDATA_DIR", str(tmp_path / "appdata"))
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
    save(folder, document(at=NOW - timedelta(minutes=20), state="paused"))  # older than a poll interval
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


def run_cycle(tmp_path: Path, folder: Path | None, *, paused: bool = False,
              options: dict | None = None) -> svc.DataService:
    from usage_monitor.settings import Settings

    settings = Settings(tmp_path / "settings.json", save_delay_s=60)
    if options:
        settings.update(options)
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
    monkeypatch.setenv("USAGE_MONITOR_APPDATA_DIR", str(tmp_path / "elsewhere"))
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


# ── The usage guard: --hook's notice (#27) ───────────────────────────────────────

ARMED = {"enabled": True, "session_cap": 95, "weekly_cap": 75, "scoped_cap": None, "stagger_s": 120}
TOOL = {"session_id": "s1", "hook_event_name": "PostToolUse", "tool_name": "Bash"}


def guarded(doc: dict, **policy: Any) -> dict:
    """``doc`` as the app writes it with the guard armed: by default the weekly cap is 75%."""
    return {**doc, "guard": {"policy": {**ARMED, **policy}}}


def context(said: dict | None) -> str:
    return said["hookSpecificOutput"]["additionalContext"] if said else ""


def told(folder: Path) -> list[str]:
    verdict = guard.for_reader(status.read(folder / status.STATUS_NAME), NOW, folder)
    return [entry["session_id"] for entry in sessions.told(folder, guard.episode(verdict))]


def test_a_holding_guard_tells_each_agent_of_a_session_once(folder):
    save(folder, guarded(document(weekly=78.0)))           # under the 80% threshold: only the guard speaks
    said = context(hook(folder, TOOL))
    assert said.startswith("Claude Usage Monitor: plan usage has reached the wind-down point")
    assert "weekly 78% (cap 75%)" in said and "Nothing is blocked" in said and "Wrap up at a safe point" in said
    assert "--wait --session s1`" in said and "Resume now" in said
    assert "In PowerShell run `& \"" in said and "--wait --session s1 | Out-String`" in said  # or it returns at once
    assert hook(folder, TOOL) is None                       # told already
    assert hook(folder, PROMPT) is None                     # the main conversation heard it after a tool call
    sub = context(hook(folder, {**TOOL, "agent_id": "a1", "agent_type": "Explore"}))
    assert "wind-down point" in sub and "--wait" not in sub  # a subagent is not offered the wait
    assert hook(folder, {**TOOL, "agent_id": "a1"}) is None
    assert told(folder) == ["s1"]
    assert hook(folder, {**TOOL, "session_id": "s2"}) is not None


def test_an_agents_last_call_is_told_nothing_and_uses_nothing_up(folder):
    """An agent's StructuredOutput call hands back its answer, and nothing reads what follows it. The notice it got there
    was wasted, and still counted the agent as told (#41). Now it hears at its next ordinary call instead."""
    save(folder, guarded(document(weekly=78.0)))
    answer = {**TOOL, "agent_id": "a1", "tool_name": "StructuredOutput"}
    assert hook(folder, answer) is None and told(folder) == []
    assert "wind-down point" in context(hook(folder, {**TOOL, "agent_id": "a1"}))
    assert hook(folder, answer) is None


def test_a_main_conversations_answer_call_does_not_use_up_the_threshold_line(folder):
    save(folder, document(weekly=81.0))
    assert hook(folder, {**TOOL, "tool_name": "StructuredOutput"}) is None
    assert context(hook(folder, PROMPT)).startswith("Claude plan usage")


def test_an_agent_is_told_that_only_the_main_conversation_can_wait(folder):
    """Agents are not offered the wait, and were not told why. One wrapped up early on its own (#41)."""
    save(folder, guarded(document(weekly=78.0)))
    main = context(hook(folder, TOOL))
    sub = context(hook(folder, {**TOOL, "agent_id": "a1"}))
    assert cli.AGENT_CANNOT_WAIT in sub and cli.AGENT_CANNOT_WAIT not in main


def test_get_plan_usage_says_when_the_guard_holds(folder, fetches):
    """Sessions call the tool before long work, and read its summary line first. That line now says the guard holds,
    and when work can resume (#41)."""
    save(folder, guarded(document(weekly=78.0)))
    (called,) = rpc(folder, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_plan_usage", "arguments": {}}})
    first = called["result"]["content"][0]["text"].split("\n")[0]
    assert "usage guard holding: weekly 78% (cap 75%)" in first and "Wrap up at a safe point" in first
    assert first == called["result"]["structuredContent"]["summary"]
    save(folder, guarded(document(weekly=60.0)))
    (called,) = rpc(folder, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_plan_usage", "arguments": {}}})
    assert "usage guard" not in called["result"]["content"][0]["text"].split("\n")[0]


def test_switching_the_guard_off_and_on_starts_a_new_episode(folder):
    """#41, left over from #40: a hold that ended because the guard was switched off, and that starts again when it is
    switched back on in the same window, is told again. The app records when the guard was switched on."""
    save(folder, guarded(document(weekly=78.0)))
    assert hook(folder, TOOL) is not None and hook(folder, TOOL) is None
    save(folder, guarded(document(weekly=78.0), enabled_at="2026-10-21T12:30:00+00:00"))
    assert "wind-down point" in context(hook(folder, TOOL))
    assert hook(folder, TOOL) is None and told(folder) == ["s1"]
    save(folder, guarded(document(weekly=78.0), enabled_at="2026-10-21T12:45:00+00:00"))
    assert hook(folder, TOOL) is not None


def test_the_policy_carries_when_the_guard_was_switched_on(tmp_path):
    on = "2026-10-21T12:30:00+00:00"
    assert guard.policy_from({"guard_enabled": True, guard.ON_KEY: on}.get)["enabled_at"] == on
    assert guard.policy_from({"guard_enabled": True, guard.ON_KEY: 5}.get)["enabled_at"] is None
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"guard_enabled": True, guard.ON_KEY: on}), encoding="utf-8")
    assert guard.policy_from_file(path)["enabled_at"] == on
    path.write_text(json.dumps({"guard_enabled": True}), encoding="utf-8")        # a file from before: no time yet
    assert guard.policy_from_file(path)["enabled_at"] is None


def test_the_told_list_counts_the_conversation_and_its_agents_apart(folder):
    """#41: the card said two sessions were told when only a workflow's agents were. The list says, per session, whether
    its main conversation heard and how many of its agents did."""
    save(folder, guarded(document(weekly=78.0)))
    hook(folder, {**TOOL, "agent_id": "a1"})                 # the agents of s1 hear it first
    hook(folder, {**TOOL, "agent_id": "a2"})
    hook(folder, {**TOOL, "session_id": "s2"})               # s2's main conversation

    def who() -> dict:
        verdict = guard.for_reader(status.read(folder / status.STATUS_NAME), NOW, folder)
        return {entry["session_id"]: (entry["main"], entry["agents"]) for entry in sessions.told(folder, guard.episode(verdict))}

    assert who() == {"s1": (False, 2), "s2": (True, 0)}
    hook(folder, TOOL)                                       # s1's conversation hears it at its next tool call
    assert who()["s1"] == (True, 2)


def test_a_new_holding_episode_is_told_again(folder):
    save(folder, guarded(document(weekly=78.0)))
    assert hook(folder, TOOL) is not None
    save(folder, guarded(document(session=96.0, weekly=78.0)))  # the session is over its cap too
    said = context(hook(folder, TOOL))
    assert "session 96% (cap 95%), weekly 78% (cap 75%)" in said
    assert hook(folder, TOOL) is None


def test_a_window_that_resets_while_another_limit_holds_is_not_a_new_episode(folder):
    save(folder, guarded(document(session=96.0, weekly=78.0)))
    assert hook(folder, TOOL) is not None
    save(folder, guarded(document(session=4.0, weekly=78.0)))  # the session reset; the week is still over its cap
    assert hook(folder, TOOL) is None
    assert told(folder) == ["s1"]                            # the card still counts s1 as told


def test_a_hold_at_a_changed_cap_in_the_same_window_is_told_again(folder):
    """#40: the guard held at a low cap, the cap went back up, and the same window then reached the new cap. The
    sessions and agents told of the first hold kept working after it, so they are told of the second one."""
    save(folder, guarded(document(session=45.0, weekly=20.0), session_cap=40))
    assert "session 45% (cap 40%)" in context(hook(folder, TOOL))
    assert "wind-down point" in context(hook(folder, {**TOOL, "agent_id": "a1"}))
    assert hook(folder, {**TOOL, "session_id": "s2"}) is not None
    save(folder, guarded(document(session=45.0, weekly=20.0), session_cap=95))   # the cap went back up: no hold
    assert hook(folder, TOOL) is None and told(folder) == []
    save(folder, guarded(document(session=96.0, weekly=20.0), session_cap=95))   # the same window reaches it
    assert "session 96% (cap 95%)" in context(hook(folder, TOOL))
    assert "wind-down point" in context(hook(folder, {**TOOL, "agent_id": "a1"}))
    assert hook(folder, TOOL) is None                        # once per episode, as before
    assert told(folder) == ["s1"]                            # the card lists who heard this hold, not the first one


def test_a_subagent_does_not_use_up_the_threshold_line(folder):
    save(folder, document(weekly=81.0))
    assert hook(folder, {**TOOL, "agent_id": "a1"}) is None  # a subagent hears no summary line...
    assert context(hook(folder, PROMPT)).startswith("Claude plan usage")  # ...so the main conversation still does


def test_a_memory_that_cannot_be_read_is_not_overwritten(folder, monkeypatch):
    save(folder, guarded(document(weekly=78.0)))
    (folder / cli.HOOK_MEMORY).write_text(json.dumps({"s9": {"at": NOW.isoformat(), "steps": {"x": 1}}}),
                                          encoding="utf-8")
    monkeypatch.setattr(cli, "_load_memory", lambda *_a: None)  # another process holds it for a moment
    assert "wind-down point" in context(hook(folder, TOOL))  # it still speaks...
    assert "s9" in json.loads((folder / cli.HOOK_MEMORY).read_text(encoding="utf-8"))  # ...and saves nothing


def test_session_start_repeats_the_notice_after_a_compaction(folder):
    save(folder, guarded(document(weekly=78.0)))
    start = {"session_id": "s1", "hook_event_name": "SessionStart", "source": "compact"}
    for _ in range(2):
        said = context(hook(folder, start, always=True))
        assert said.startswith("Claude plan usage (Max 5x") and "wind-down point" in said


def test_the_threshold_line_and_the_notice_share_one_context_and_one_memory(folder):
    save(folder, guarded(document(weekly=81.0)))
    said = context(hook(folder, TOOL))
    summary, notice = said.split("\n\n")
    assert summary.startswith("Claude plan usage") and notice.startswith("Claude Usage Monitor: plan usage")
    save(folder, guarded(document(weekly=86.0)))            # a step up: the line again, the notice not
    said = context(hook(folder, PROMPT))
    assert said.startswith("Claude plan usage") and "wind-down point" not in said
    assert told(folder) == ["s1"]                            # the step did not wipe who was told


def test_the_guard_is_quiet_when_off_or_under_its_caps(folder):
    save(folder, guarded(document(weekly=78.0), enabled=False))
    assert hook(folder, TOOL) is None
    save(folder, guarded(document(weekly=78.0), weekly_cap=79))
    assert hook(folder, TOOL) is None
    save(folder, document(weekly=78.0))                     # no policy in the reading: the settings file decides
    (folder / guard.SETTINGS_NAME).write_text(json.dumps({"guard_enabled": True, "guard_weekly_cap": 70}),
                                              encoding="utf-8")
    assert "weekly 78% (cap 70%)" in context(hook(folder, TOOL))


# ── The usage guard: --wait (#27) ────────────────────────────────────────────────

class Clock:
    """A clock that sleeps by moving on, with something to do during the naps."""

    def __init__(self, during: Callable[[int], None] | None = None) -> None:
        self.now, self.naps, self.during = NOW, [], during

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.naps.append(seconds)
        self.now += timedelta(seconds=seconds)
        if self.during is not None:
            self.during(len(self.naps))
        assert len(self.naps) < 2_000, "the wait never ended"


def wait(folder: Path, clock: Clock) -> tuple[int, str]:
    out = io.BytesIO()
    code = cli.run_wait(session_id="s1", folder=folder, clock=clock, app_version="2.1.0", out=out,
                        sleep=clock.sleep)
    return code, out.getvalue().decode()


def test_a_wait_ends_at_once_when_nothing_is_over_its_cap(folder):
    save(folder, guarded(document(weekly=50.0)))
    clock = Clock()
    code, said = wait(folder, clock)
    assert code == 0 and clock.naps == []
    assert said.startswith("Claude Usage Monitor: the wait is over: every watched limit is under its cap again. "
                           "Work can resume. Claude plan usage (Max 5x")


def test_a_wait_holds_until_the_window_over_its_cap_resets(folder, offline):
    save(folder, guarded(document(session=30.0), session_cap=25, weekly_cap=None))  # the session resets at +2 h
    seen: list = []
    clock = Clock(during=lambda nap: seen.append(sessions.waiting(folder)) if nap == 1 else None)
    code, said = wait(folder, clock)
    assert code == 0 and "every watched limit is under its cap again" in said
    assert NOW + timedelta(hours=2) <= clock.now < NOW + timedelta(hours=2, minutes=1)
    assert [(entry["pid"], entry["session_id"]) for entry in seen[0]] == [(os.getpid(), "s1")]
    assert sessions.waiting(folder) == []                   # it unregistered
    assert 1 <= len(offline) <= 8                           # the app is closed: it asked now and then, not every nap


def test_resume_now_ends_a_wait_whatever_the_limits(folder):
    save(folder, guarded(document(weekly=78.0)))
    clock = Clock(during=lambda nap: sessions.resume_now(folder) if nap == 3 else None)
    code, said = wait(folder, clock)
    assert code == 0 and len(clock.naps) == 3
    assert said.startswith("Claude Usage Monitor: the wait is over: Resume now was pressed (the limits were not "
                           "checked)")


def test_waits_are_released_one_stagger_apart_oldest_first(folder, monkeypatch):
    save(folder, guarded(document(weekly=50.0), stagger_s=120))
    me = {"pid": os.getpid(), "session_id": "s1", "since": "2026-10-24T16:00:00Z", "cwd": None}
    older = {**me, "pid": 1, "session_id": "s0", "since": "2026-10-24T15:00:00Z"}
    alive = [older, me]
    monkeypatch.setattr(sessions, "waiting", lambda _folder: list(alive))

    def older_goes(nap: int) -> None:
        if nap == 2:  # the older wait is let go now, and is gone by the next look
            sessions.mark_released(folder, clock.now)
            alive.remove(older)

    clock = Clock(during=older_goes)
    code, _said = wait(folder, clock)
    went = NOW + timedelta(seconds=2 * cli.WAIT_POLL_S)
    assert code == 0 and clock.now == went + timedelta(seconds=120) and max(clock.naps) <= cli.WAIT_POLL_S
    assert sessions.released_at(folder) == clock.now        # the next one goes a stagger after this one


def test_a_lone_wait_still_keeps_the_stagger_after_the_last_release(folder):
    save(folder, guarded(document(weekly=50.0), stagger_s=120))
    sessions.mark_released(folder, NOW - timedelta(seconds=45))  # another wait went 45 s ago, and is gone
    clock = Clock()
    code, _said = wait(folder, clock)
    assert code == 0 and clock.now == NOW + timedelta(seconds=75)


def test_a_wait_that_cannot_register_still_waits(folder, monkeypatch):
    def refused(*_a: Any, **_k: Any) -> Any:
        raise PermissionError("waiting/ is not writable")

    monkeypatch.setattr(sessions, "register_wait", refused)
    save(folder, guarded(document(weekly=78.0)))
    clock = Clock(during=lambda nap: save(folder, guarded(document(weekly=50.0))) if nap == 2 else None)
    code, said = wait(folder, clock)
    assert code == 0 and len(clock.naps) == 2 and "every watched limit is under its cap again" in said


def test_a_wait_turned_off_with_the_guard_says_so(folder):
    save(folder, guarded(document(weekly=78.0), enabled=False))
    code, said = wait(folder, Clock())
    assert code == 0 and "the wait is over: the usage guard was turned off" in said


def test_a_wait_without_a_reading_keeps_waiting(folder):
    clock = Clock(during=lambda nap: sessions.resume_now(folder) if nap == 5 else None)
    code, said = wait(folder, clock)
    assert code == 0 and len(clock.naps) == 5 and "Resume now was pressed" in said


def test_a_wait_that_fails_says_so_and_exits_1(folder, monkeypatch):
    def broken(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(status, "current", broken)
    code, said = wait(folder, Clock())
    assert code == 1 and said.startswith("Claude Usage Monitor: the wait stopped because of an error")
    assert sessions.waiting(folder) == []


def test_the_wait_command_line_and_its_flags(monkeypatch):
    assert main.parse_args(["--wait", "--session", "ab c;d-1"]) == main.Command("wait", session_id="abcd-1")
    assert main.parse_args(["--wait"]) == main.Command("wait")
    assert main.parse_args(["--wait", "--session", "--json"]) == main.Command("wait")
    monkeypatch.setattr(cli.sys, "frozen", True, raising=False)
    monkeypatch.setattr(cli.sys, "executable", r"C:\Users\A B\AppData\Local\ClaudeUsageMonitor\ClaudeUsageMonitor.exe")
    assert cli.wait_command("s1") == ('"C:/Users/A B/AppData/Local/ClaudeUsageMonitor/ClaudeUsageMonitor.exe" '
                                      '--wait --session s1')
    assert cli.wait_command(None).endswith('ClaudeUsageMonitor.exe" --wait')
    monkeypatch.setattr(cli.sys, "executable", r"C:\Apps\O'Neil\ClaudeUsageMonitor.exe")  # no space: still quoted
    assert cli.wait_command('s"1; rm x') == '"C:/Apps/O\'Neil/ClaudeUsageMonitor.exe" --wait --session s1rmx'


def test_a_wait_runs_even_with_no_stdout(monkeypatch):
    import usage_monitor.log

    monkeypatch.setattr(usage_monitor.log, "setup_logging", lambda *_a, **_k: None)
    monkeypatch.setattr(main.sys, "stdout", None)
    calls: list = []
    monkeypatch.setattr(cli, "run_wait", lambda **kwargs: calls.append(kwargs) or 0)
    assert main._agent(main.Command("wait", session_id="s1")) == 0
    assert calls[0]["out"] is None and calls[0]["session_id"] == "s1"


# ── The MCP server ───────────────────────────────────────────────────────────────

# ── The usage guard: an agent's start, and a conversation that waits for its background work (#41) ──────────

AGENT_START = {"session_id": "s1", "hook_event_name": "SubagentStart", "agent_id": "a1", "agent_type": "workflow-subagent"}
WORKFLOW = {"id": "w1", "type": "workflow", "status": "running", "description": "audit", "name": "audit"}
STOP = {"session_id": "s1", "hook_event_name": "Stop", "stop_hook_active": False, "last_assistant_message": "LAUNCHED",
        "background_tasks": [WORKFLOW], "session_crons": []}


def who_heard(folder: Path) -> list[tuple[str, bool, int]]:
    verdict = guard.for_reader(status.read(folder / status.STATUS_NAME), NOW, folder)
    return [(entry["session_id"], entry["main"], entry["agents"]) for entry in sessions.told(folder, guard.episode(verdict))]


def test_an_agent_that_starts_during_a_hold_hears_it_before_its_first_prompt(folder):
    """#41: a workflow's agents heard the notice at their first tool call, after a model call that could run for
    minutes. SubagentStart hands it over before the agent's first prompt."""
    save(folder, guarded(document(weekly=60.0)))
    assert hook(folder, AGENT_START) is None                                  # armed: nothing to say
    save(folder, guarded(document(weekly=78.0)))
    said = hook(folder, AGENT_START)
    assert said["hookSpecificOutput"]["hookEventName"] == "SubagentStart"
    text = context(said)
    assert "wind-down point" in text and cli.AGENT_CANNOT_WAIT in text and "--wait" not in text
    assert hook(folder, {**TOOL, "agent_id": "a1"}) is None                   # told already: its tool calls say nothing
    assert who_heard(folder) == [("s1", False, 1)]


def test_claude_codes_own_agents_hear_nothing_at_their_start(folder):
    """Claude Code starts agents of its own with no agent type (prompt suggestions and the like). Nothing of the
    session reads what they are told, and they must not count as told."""
    save(folder, guarded(document(weekly=78.0)))
    assert hook(folder, {**AGENT_START, "agent_type": ""}) is None and told(folder) == []


def watch(folder: Path, clock: Clock, event: dict | None = STOP, *,
          parent_alive: Callable[[], bool] | None = None) -> tuple[int, str]:
    err = io.BytesIO()
    stdin = io.BytesIO(json.dumps(event).encode()) if event is not None else None
    code = cli.run_watch(folder=folder, clock=clock, app_version="2.1.0", stdin=stdin, err=err, sleep=clock.sleep,
                         epoch=lambda: (clock.now - NOW).total_seconds(), parent_alive=parent_alive)
    return code, err.getvalue().decode()


def test_a_conversation_that_waits_for_its_workflow_is_woken_when_the_guard_holds(folder):
    """#41: the conversation that started a workflow was idle the whole hold, waiting for it, and nothing reached it.
    The Stop hook watches while that work runs, and its exit wakes the conversation with the notice."""
    save(folder, guarded(document(weekly=60.0)))
    seen: list = []

    def hold_begins(nap: int) -> None:
        if nap == 1:
            seen.append(sessions.watching(folder))
        if nap == 3:
            save(folder, guarded(document(weekly=78.0)))

    clock = Clock(during=hold_begins)
    agents = [{"id": f"a{n}", "type": "subagent", "status": "running", "agent_type": "Explore"} for n in (1, 2)]
    shell = {"id": "b1", "type": "shell", "status": "running", "command": "npm test"}
    code, said = watch(folder, clock, {**STOP, "background_tasks": [*agents, WORKFLOW, shell]})
    assert code == cli.WAKE_EXIT and clock.naps == [cli.WATCH_POLL_S] * 3
    assert said.startswith("Claude Usage Monitor: plan usage has reached the wind-down point")
    assert "weekly 78% (cap 75%)" in said and "--wait --session s1`" in said
    assert "as it waits for its background work (1 workflow, 2 agents)" in said            # a shell spends nothing
    assert [(entry["session_id"], entry["work"]) for entry in seen[0]] == [("s1", {"workflow": 1, "agent": 2})]
    assert sessions.watching(folder) == [] and who_heard(folder) == [("s1", True, 0)]
    assert hook(folder, TOOL) is None                                         # its next tool call does not tell it again


def test_a_conversation_with_nothing_running_that_spends_the_plan_is_left_alone(folder):
    """An idle conversation is not disturbed. A Stop with no background work, or only shells, or from a Claude Code
    that sends no list, ends at once, as does one with no session id or nowhere to write."""
    save(folder, guarded(document(weekly=78.0)))
    shell = {"id": "b1", "type": "shell", "status": "running", "command": "npm run dev"}
    no_list = {key: value for key, value in STOP.items() if key != "background_tasks"}
    for event in ({**STOP, "background_tasks": []}, {**STOP, "background_tasks": [shell]}, no_list,
                  {**STOP, "session_id": ""}, None):
        clock = Clock()
        assert watch(folder, clock, event) == (0, "") and clock.naps == [], event
    nowhere = cli.run_watch(folder=folder, clock=Clock(), app_version="2.1.0",
                            stdin=io.BytesIO(json.dumps(STOP).encode()), err=None)
    assert nowhere == 0 and told(folder) == [] and sessions.watching(folder) == []


def test_a_watch_ends_when_the_guard_is_off(folder):
    save(folder, guarded(document(weekly=78.0), enabled=False))
    clock = Clock()
    assert watch(folder, clock) == (0, "") and clock.naps == []
    save(folder, guarded(document(weekly=60.0)))
    clock = Clock(during=lambda nap: save(folder, guarded(document(weekly=60.0), enabled=False)) if nap == 2 else None)
    assert watch(folder, clock) == (0, "") and len(clock.naps) == 2 and sessions.watching(folder) == []


def test_a_conversation_told_already_is_not_woken_again(folder):
    """It heard at a tool call before it went idle. The watch goes on, for a new hold, until a newer Stop with nothing
    to watch ends it."""
    save(folder, guarded(document(weekly=78.0)))
    assert hook(folder, TOOL) is not None
    clock = Clock(during=lambda nap: watch(folder, Clock(), {**STOP, "background_tasks": []}) if nap == 4 else None)
    assert watch(folder, clock) == (0, "") and len(clock.naps) == 4


def test_a_newer_stop_of_the_same_conversation_takes_the_watch_over(folder):
    save(folder, guarded(document(weekly=60.0)))
    newer = folder / sessions.WATCHING_DIR / "s1.json"

    def taken_over(nap: int) -> None:
        if nap == 2:                                   # another process watches s1 now, and the hold is its to tell
            status.write({"pid": 1, "started": None, "session_id": "s1", "since": "x", "work": {"workflow": 1}}, newer)
            save(folder, guarded(document(weekly=78.0)))

    clock = Clock(during=taken_over)
    assert watch(folder, clock) == (0, "") and len(clock.naps) == 2
    assert told(folder) == [] and newer.exists()                              # the newer watch's file stays


def test_a_watch_ends_with_the_claude_code_that_started_it(folder):
    save(folder, guarded(document(weekly=60.0)))
    clock = Clock()
    assert watch(folder, clock, parent_alive=lambda: len(clock.naps) < 2) == (0, "") and len(clock.naps) == 2
    assert sessions.watching(folder) == []
    assert sessions.parent_check()() is True                                 # the process that started this one runs


def test_a_watch_ends_itself_before_claude_codes_timeout_would(folder, monkeypatch):
    monkeypatch.setattr(cli, "WATCH_MAX_S", 300.0)
    save(folder, guarded(document(weekly=60.0)))
    clock = Clock()
    assert watch(folder, clock) == (0, "") and clock.now == NOW + timedelta(seconds=300)


def test_a_watch_that_cannot_record_who_was_told_tells_it_at_a_later_look(folder, monkeypatch):
    """A conversation woken twice is worse than one told a poll later: the watch speaks only once it is recorded."""
    save(folder, guarded(document(weekly=78.0)))
    real, calls = cli._load_memory, []
    monkeypatch.setattr(cli, "_load_memory", lambda *a: calls.append(1) or (None if len(calls) <= 2 else real(*a)))
    clock = Clock()
    code, said = watch(folder, clock)
    assert code == cli.WAKE_EXIT and len(clock.naps) == 2 and "wind-down point" in said and told(folder) == ["s1"]


def test_a_watch_that_fails_wakes_nobody(folder, monkeypatch):
    def broken(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("boom")

    save(folder, guarded(document(weekly=60.0)))
    clock = Clock(during=lambda nap: monkeypatch.setattr(status, "current", broken) if nap == 1 else None)
    assert watch(folder, clock) == (0, "") and sessions.watching(folder) == []


def test_the_watch_command_line_and_where_it_writes(monkeypatch):
    """Claude Code shows the woken conversation what an asyncRewake hook wrote on stderr, or on stdout when stderr is
    empty. A windowed build has either only when Claude Code hands it a pipe."""
    import usage_monitor.log

    assert main.parse_args(["--watch"]) == main.Command("watch")
    monkeypatch.setattr(usage_monitor.log, "setup_logging", lambda *_a, **_k: None)
    calls: list = []
    monkeypatch.setattr(cli, "run_watch", lambda **kwargs: calls.append(kwargs) or 0)
    err, out = io.TextIOWrapper(io.BytesIO()), io.TextIOWrapper(io.BytesIO())
    monkeypatch.setattr(main.sys, "stdin", None)
    monkeypatch.setattr(main.sys, "stderr", err)
    monkeypatch.setattr(main.sys, "stdout", out)
    assert main._agent(main.Command("watch")) == 0 and calls[-1]["err"] is err.buffer
    monkeypatch.setattr(main.sys, "stderr", None)
    assert main._agent(main.Command("watch")) == 0 and calls[-1]["err"] is out.buffer
    monkeypatch.setattr(main.sys, "stdout", None)
    assert main._agent(main.Command("watch")) == 0 and calls[-1]["err"] is None and calls[-1]["stdin"] is None
    assert callable(calls[-1]["parent_alive"])


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


# ── Sparing the disk ─────────────────────────────────────────────────────────────

def test_the_fingerprint_ignores_what_moves_with_the_clock_but_not_the_reading():
    doc = document()
    moved = json.loads(json.dumps(doc))
    moved["written_at"] = "2026-10-24T16:05:00Z"
    moved["weekly"]["elapsed_pct"] += 0.5
    moved["weekly"]["projected_limit_at"] = "2026-10-25T11:00:00Z"
    moved["burn"]["output_tokens_per_min"] = 999.0
    assert status.fingerprint(moved) == status.fingerprint(doc)
    for key, value in (("status", "offline"), ("usage_fetched_at", "2026-10-24T16:15:00Z")):
        assert status.fingerprint({**doc, key: value}) != status.fingerprint(doc)
    assert status.fingerprint({**doc, "burn": {**doc["burn"], "velocity": "SLOW"}}) != status.fingerprint(doc)
    assert status.fingerprint({**doc, "weekly": {**doc["weekly"], "percent": 81.0}}) != status.fingerprint(doc)


def test_readers_renew_the_demand_at_most_every_few_minutes(folder):
    import os

    status.touch_demand(folder)
    path = folder / status.DEMAND_NAME
    recent = path.stat().st_mtime - 60
    os.utime(path, (recent, recent))
    status.touch_demand(folder)
    assert path.stat().st_mtime == recent                  # a minute old: left alone
    old = recent - status.DEMAND_TOUCH_S
    os.utime(path, (old, old))
    status.touch_demand(folder)
    assert path.stat().st_mtime > old                      # renewed


def test_an_unchanged_reading_is_not_rewritten_until_it_is_due(tmp_path, folder, monkeypatch):
    from usage_monitor.settings import Settings

    writes: list[str] = []
    real_write = status.write
    monkeypatch.setattr(status, "write", lambda doc, path: (writes.append(doc["status"]), real_write(doc, path)))
    settings = Settings(tmp_path / "settings.json", save_delay_s=60)
    paused = {"on": False}
    service = svc.DataService(settings, store_path=tmp_path / "usage.db", pricing=Pricing.load(),
                              usage_client=OneClient(), roots=[tmp_path], clock=lambda: NOW,
                              pause_check=lambda: paused["on"], pricing_refresh=lambda _cached: False,
                              status_dir=folder)
    try:
        service._safe_cycle()
        service._safe_cycle()
        assert writes == ["live"]                          # the same reading: one write
        service._status_mono -= status.REFRESH_S + 1
        service._safe_cycle()
        assert writes == ["live", "live"]                  # due again
        paused["on"] = True
        service._safe_cycle()
        service._safe_cycle()
        assert writes == ["live", "live", "paused"]        # a change of status, once per pause
    finally:
        service._close_store()
        settings.close()


# ── Review fixes (PR #26) ────────────────────────────────────────────────────────

def test_a_fresh_paused_reading_is_used_without_waiting(folder, fetches, monkeypatch):
    save(folder, document(at=NOW - timedelta(minutes=2), state="paused"))
    monkeypatch.setattr(status.time, "sleep", lambda _s: pytest.fail("waited on a reading that is current"))
    doc, _running = status.current(folder, now=NOW, fetch=True, wait_s=5.0, running=True)
    assert doc["status"] == "paused" and fetches == []


def test_status_exits_1_only_when_there_is_no_reading(folder):
    out = io.BytesIO()
    assert cli.run_status(as_json=False, folder=folder, clock=lambda: NOW, app_version="", out=out) == 1
    assert b"no reading" in out.getvalue()
    save(folder, document(at=NOW))
    assert cli.run_status(as_json=False, folder=folder, clock=lambda: NOW, app_version="", out=io.BytesIO()) == 0


def test_a_window_that_has_reset_gets_only_its_reset_note():
    later = NOW + timedelta(hours=3)  # the session reset at +2h
    limited = status.summary(document(session=100.0, session_verdict="LIMITED"), now=later, running=True)
    assert "session 100% (window reset 1h 0m ago" in limited
    assert "LIMIT REACHED" not in limited and "safe at this rate" not in limited
    at_risk = document(session=92.0, session_verdict="AT_RISK", limit_at=status._iso(NOW + timedelta(minutes=30)))
    assert "the limit is hit" not in status.summary(at_risk, now=later, running=True)


def test_the_hook_does_not_speak_for_a_window_that_has_reset(folder):
    save(folder, document(session=95.0, weekly=10.0))
    assert hook(folder, PROMPT, now=NOW + timedelta(hours=3)) is None
    assert hook(folder, PROMPT) is not None


def test_the_gate_refuses_at_the_session_cap(folder):
    save(folder, document(session=95.0, weekly=10.0))
    reason = gate(folder, session_cap=95.0)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "session usage is 95%, at or past the 95% cap" in reason
    save(folder, document(session=94.9, weekly=10.0))
    assert gate(folder, session_cap=95.0) is None


def test_a_successful_fetch_keeps_the_limits_and_never_the_token(tmp_path, monkeypatch):
    from usage_monitor.data import usage_api

    config = tmp_path / "claude"
    config.mkdir(exist_ok=True)
    (config / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {
        "accessToken": TOKEN, "subscriptionType": "max", "rateLimitTier": "default_claude_max_5x",
        "expiresAt": int((NOW + timedelta(hours=8)).timestamp() * 1000)}}), encoding="utf-8")

    class Live:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def fetch(self, now: datetime) -> FetchResult:
            return FetchResult(status=ApiStatus.LIVE, detail="Updated", snapshot=snapshot(now), fresh=True)

    monkeypatch.setattr(usage_api, "UsageClient", Live)
    doc = REAL_FETCH_ONCE(NOW, previous=None, app_version="2.1.0")
    assert doc["source"] == status.SOURCE_CLI and doc["plan"]["name"] == "Max 5x"
    assert doc["session"]["percent"] == 30.0 and doc["weekly"]["percent"] == 80.0
    assert doc["usage_fetched_at"] == status._iso(NOW) and doc["burn"] is None
    assert TOKEN not in json.dumps(doc)


def test_a_later_reading_written_during_a_fetch_is_kept(folder, monkeypatch):
    save(folder, document(at=NOW - timedelta(hours=1)))

    def slow_failure(now, *, previous, app_version=""):
        save(folder, document(at=NOW, session=97.0))  # the app wrote while the request was out
        doc = dict(previous)
        doc.update(source=status.SOURCE_CLI, written_at=status._iso(now), status="offline")
        return doc

    monkeypatch.setattr(status, "fetch_once", slow_failure)
    doc, _running = status.current(folder, now=NOW, fetch=True, running=False)
    assert doc["session"]["percent"] == 97.0
    assert status.read(folder / status.STATUS_NAME)["session"]["percent"] == 97.0


@pytest.mark.skipif(sys.platform != "win32", reason="the fetch lock is a Windows byte-range lock")
def test_one_reader_at_a_time_holds_the_fetch_lock(folder, monkeypatch):
    import msvcrt
    import os

    monkeypatch.setattr(status, "FETCH_LOCK_S", 0.2)
    folder.mkdir(parents=True, exist_ok=True)
    fd = os.open(folder / status.LOCK_NAME, os.O_RDWR | os.O_CREAT)
    try:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # another reader holds it
        with status._fetch_lock(folder) as held:
            assert held is False
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        with status._fetch_lock(folder) as held:
            assert held is True
    finally:
        os.close(fd)


@pytest.mark.skipif(sys.platform != "win32", reason="the single-instance mutex is a Windows object")
def test_monitor_running_sees_the_mutex(monkeypatch):
    import ctypes
    import os
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    name = f"Local\\ClaudeUsageMonitorTest-{os.getpid()}"
    monkeypatch.setattr(status, "MUTEX_NAME", name)
    assert REAL_MONITOR_RUNNING(env={}) is False
    handle = kernel32.CreateMutexW(None, False, name)
    try:
        assert REAL_MONITOR_RUNNING(env={}) is True
    finally:
        kernel32.CloseHandle(handle)
    assert REAL_MONITOR_RUNNING(env={}) is False


def test_mcp_answers_a_request_without_a_method_an_empty_batch_and_deep_nesting(folder):
    replies = rpc(folder, {"jsonrpc": "2.0", "id": 4}, b"[]", b"[" * 100_000,
                  {"jsonrpc": "2.0", "id": 5, "result": {}}, {"jsonrpc": "2.0", "id": 6, "method": "ping"})
    assert [(reply.get("id"), reply.get("error", {}).get("code")) for reply in replies] == [
        (4, -32600), (None, -32600), (None, -32700), (6, None)]


def test_hooks_exit_0_even_with_no_stdout_or_a_crash(monkeypatch):
    import usage_monitor.log

    monkeypatch.setattr(usage_monitor.log, "setup_logging", lambda *_a, **_k: None)  # keep the test's own logging
    monkeypatch.setattr(main.sys, "stdout", None)
    assert main._agent(main.Command("hook")) == 0
    assert main._agent(main.Command("gate")) == 0
    assert main._agent(main.Command("status")) == 1
    monkeypatch.setattr(main, "run_command", lambda *_a: 1 / 0)
    assert main.main(["--gate"]) == 0
    assert main.main(["--status"]) == 1


def test_an_overwritten_status_file_is_put_back_on_the_next_cycle(tmp_path, folder):
    from usage_monitor.settings import Settings

    settings = Settings(tmp_path / "settings.json", save_delay_s=60)
    service = svc.DataService(settings, store_path=tmp_path / "usage.db", pricing=Pricing.load(),
                              usage_client=OneClient(), roots=[tmp_path], clock=lambda: NOW,
                              pause_check=lambda: False, pricing_refresh=lambda _cached: False,
                              status_dir=folder)
    try:
        service._safe_cycle()
        save(folder, document(at=NOW, source=status.SOURCE_CLI, session=12.0))  # a reader wrote over it
        service._safe_cycle()
        doc = status.read(folder / status.STATUS_NAME)
        assert doc["source"] == status.SOURCE_MONITOR and doc["session"]["percent"] == 30.0
    finally:
        service._close_store()
        settings.close()


# ── The usage guard in the service (#27) ─────────────────────────────────────────

def test_the_service_publishes_the_guard_and_writes_it_with_the_reading(tmp_path, folder):
    service = run_cycle(tmp_path, folder, options={"guard_enabled": True, "guard_weekly_cap": 70})
    assert service.state.guard["state"] == "holding"
    assert [item["name"] for item in service.state.guard["over"]] == ["weekly"]   # 80% against a 70% cap
    doc = status.read(folder / status.STATUS_NAME)
    assert doc["guard"]["state"] == "holding" and doc["guard"]["policy"]["weekly_cap"] == 70


def test_a_disarmed_guard_is_off_in_the_state(tmp_path, folder):
    service = run_cycle(tmp_path, folder)
    assert service.state.guard["state"] == "off"


def test_a_window_that_reset_since_the_last_reading_is_fetched_again_soon(tmp_path):
    from usage_monitor.settings import Settings

    settings = Settings(tmp_path / "settings.json", save_delay_s=60)
    settings.update({"poll_interval_ms": 86_400_000})                 # a day: only the reset can make a fetch due
    service = svc.DataService(settings, store_path=tmp_path / "usage.db", pricing=Pricing.load(),
                              usage_client=OneClient(), roots=[tmp_path], clock=lambda: NOW,
                              pricing_refresh=lambda _cached: False)
    try:
        resets = NOW + timedelta(hours=2)                               # the session in snapshot()
        service._last_fetch = OneClient().fetch(NOW)
        service._last_fetch_at = NOW
        assert not service._fetch_due(resets + timedelta(seconds=10))  # too soon after the reset
        assert service._fetch_due(resets + svc.RESET_FETCH_DELAY + timedelta(seconds=1))
        service._last_fetch_at = resets + timedelta(minutes=1)          # read again after the reset
        assert not service._fetch_due(resets + timedelta(minutes=2))
    finally:
        settings.close()
