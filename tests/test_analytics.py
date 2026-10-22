"""Tests for usage_monitor/data/analytics.py. Synthetic store data only.

Every expected number is worked out by hand from the rates in pricing.json (USD per million
tokens): Opus 5.5 $4 in / $20 out (fast x2), Sonnet 5 $2 in / $10 out, Haiku 5.5 $0.10 in /
$0.50 out with a $0.50 / $0.05 long-context tier above 100K prompt tokens. M is one million.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta, timezone, tzinfo

import pytest

from usage_monitor.data import analytics as an
from usage_monitor.data.models import (
    ApiErrorEvent,
    CodeChange,
    Compaction,
    GitEvent,
    Limit,
    PlanInfo,
    PullRequest,
    RequestRecord,
    SessionMeta,
    ToolCall,
    UsageSnapshot,
)
from usage_monitor.data.pricing import Pricing
from usage_monitor.data.store import Store

UTC = timezone.utc
M = 1_000_000
OPUS = "claude-opus-5-5"
HAIKU = "claude-haiku-5-5"
SONNET = "claude-sonnet-5"
PRICING = Pricing.load()
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)          # a Tuesday
PLAN = PlanInfo(subscription_type="max", rate_limit_tier="default_claude_max_5x",
                name="Max 5x", monthly_price_usd=100.0)
NO_PLAN = PlanInfo(subscription_type=None, rate_limit_tier=None, name="Unknown plan",
                   monthly_price_usd=None)
PR_A = "https://github.com/o/r/pull/1"
PR_B = "https://github.com/o/r/pull/2"
PR_C = "https://github.com/o/r/pull/3"


def dt(month: int, day: int, hour: int = 0, minute: int = 0, year: int = 2026) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def rec(key: str, ts: datetime, *, model: str = OPUS, inp: int = 0, out: int = 0,
        cache_read: int = 0, session: str = "s1", project: str = "alpha",
        cwd: str | None = "C:/work/alpha", subagent: bool = False, agent: str | None = None,
        speed: str | None = None, **meta) -> RequestRecord:
    return RequestRecord(
        key=key, message_id="msg_" + key, request_id="req_" + key, ts=ts, session_id=session,
        project=project, cwd=cwd, git_branch="main", is_subagent=subagent, agent_id=agent,
        model=model, input_tokens=inp, output_tokens=out, cache_read_tokens=cache_read,
        speed=speed, **meta)


def build_store() -> Store:
    """The hand-built dataset behind most expectations below.

    Requests (window membership noted):
      R1 Oct 1 10:00  Opus   1M in            alpha   s1  cycle
      R2 Sep 30 23:30 Opus   1M in            alpha   s0  before the cycle
      R3 Oct 10 09:00 Haiku  1M out           alpha   s1  subagent, cycle
      R4 Oct 15 12:00 Sonnet 1M out           beta    s2  cycle, week
      R5 Oct 20 11:00 Opus   1M in, fast      beta    s2  cycle, week, today, session
      R6 Oct 20 11:30 Haiku  1M cache read    gamma   s3  cycle, week, today, session
      R7 Oct 20 11:45 unknown model 1M in     alpha   s3  cycle, week, today, session
    """
    store = Store(":memory:")
    store.add_items([
        rec("R1", dt(10, 1, 10), inp=M, project="alpha", cwd="C:/work/alpha"),
        rec("R2", dt(9, 30, 23, 30), inp=M, session="s0", project="alpha", cwd="C:/work/alpha"),
        rec("R3", dt(10, 10, 9), model=HAIKU, out=M, session="s1", project="alpha",
            cwd="C:/work/alpha", subagent=True, agent="a1"),
        rec("R4", dt(10, 15, 12), model=SONNET, out=M, session="s2", project="beta",
            cwd="D:/src/beta", web_search_requests=2, effort="high"),
        rec("R5", dt(10, 20, 11), inp=M, session="s2", project="beta", cwd="D:/src/beta",
            speed="fast", web_fetch_requests=1, effort="xhigh"),
        rec("R6", dt(10, 20, 11, 30), model=HAIKU, cache_read=M, session="s3", project="gamma",
            cwd="C:/work/gamma"),
        rec("R7", dt(10, 20, 11, 45), model="mystery-1", inp=M, session="s3", project="alpha",
            cwd="C:\\work\\alpha-old"),
    ], PRICING)
    store.add_items([
        ToolCall("t-old", "Bash", None, "s0", dt(9, 30, 23), False),
        ToolCall("t1", "Bash", None, "s1", dt(10, 1, 10), False),
        ToolCall("t2", "Bash", None, "s2", dt(10, 15, 12), False),
        ToolCall("t3", "Read", None, "s1", dt(10, 10, 9), True),
        ToolCall("t4", "mcp__n8n-mcp__n8n_get_workflow", None, "s2", dt(10, 15, 12, 30), False),
        ToolCall("t5", "mcp__n8n-mcp__n8n_get_workflow", None, "s2", dt(10, 20, 11, 10), False),
        ToolCall("t6", "mcp__n8n-mcp__search_nodes", None, "s2", dt(10, 20, 11, 20), False),
        ToolCall("t7", "mcp__github__create_issue", None, "s3", dt(10, 20, 11, 50), False),
        CodeChange("c1", "s1", dt(10, 10, 9), 10, 2, False),
        CodeChange("c2", "s3", dt(10, 20, 11, 40), 5, 0, True),
        CodeChange("c-old", "s0", dt(9, 30, 23), 100, 0, True),
        GitEvent("g1", "s2", dt(10, 15, 12), True, True, False),
        GitEvent("g2", "s2", dt(10, 20, 11), True, False, False),
        GitEvent("g-old", "s0", dt(9, 30, 23), True, False, False),
        PullRequest(url=PR_A, number=1, repository="o/r", session_id="s2", ts=dt(10, 15, 12)),
        PullRequest(url=PR_B, number=2, repository="o/r", session_id="s1", ts=None),
        PullRequest(url=PR_C, number=3, repository="o/r", session_id="s1", ts=dt(9, 20, 8)),
        Compaction("k1", "s1", dt(10, 5, 10), "auto", 800, 100),
        Compaction("k2", "s1", dt(10, 12, 10), "manual", 400, 50),
        ApiErrorEvent("e1", "s1", dt(10, 5, 10)),
        ApiErrorEvent("e-old", "s0", dt(9, 1, 10)),
        SessionMeta("s1", "alpha", ai_title="AI one", custom_title=None),
        SessionMeta("s2", "beta", ai_title="AI two", custom_title="Custom two"),
    ], PRICING)
    return store


@pytest.fixture
def data():
    store = build_store()
    yield store
    store.close()


def cost(store: Store, *, usage=None, plan=PLAN, plan_price=None, billing_day=None,
         breakdown="cycle", now=NOW, tz=UTC) -> dict:
    return an.cost_report(store, PRICING, now=now, usage=usage, plan=plan, plan_price=plan_price,
                          billing_day=billing_day, breakdown_window=breakdown, tz=tz)


def jsonable(report) -> None:
    """Fails on NaN, infinity or any value json cannot encode."""
    json.dumps(report, allow_nan=False)


# ── Empty store and None usage ────────────────────────────────────────────────────

def test_every_report_on_an_empty_store_is_json_safe_and_zero():
    store = Store(":memory:")
    report = cost(store, plan=NO_PLAN)
    jsonable(report)
    assert set(report["windows"]) == set(an.WINDOW_NAMES)
    for window in report["windows"].values():
        assert window["requests"] == 0 and window["cost_usd"] == 0.0
    assert report["plan"] == {"name": "Unknown plan", "subscription_type": None,
                              "rate_limit_tier": None, "price_usd": None, "price_source": None}
    assert report["value"]["multiplier"] is None
    assert report["value"]["projected_cycle_cost_usd"] == 0.0
    assert report["monthly"] == [] and report["top_sessions_week"] == []
    assert report["breakdowns"]["by_model"] == [] and report["breakdowns"]["by_project"] == []
    assert report["spend"] is None and report["basis"] == an.BASIS

    jsonable(an.efficiency_report(store, start=None, end=None))
    jsonable(an.pacing_report(store, now=NOW, usage=None))
    jsonable(an.history_report(store, now=NOW, tz=UTC, days=3))
    jsonable(an.sessions_report(store, PRICING, start=None, end=None))
    assert an.daily_rollups(store, tz=UTC) == []
    assert an.export_csv(an.daily_rollups(store, tz=UTC)) == ""
    store.close()


def test_reports_with_usage_on_an_empty_store_are_json_safe():
    store = Store(":memory:")
    usage = UsageSnapshot(fetched_at=NOW, raw={}, limits=[
        Limit(kind="session", group="session", percent=12.5, severity=None,
              resets_at=NOW + timedelta(hours=2), is_active=True, label="SESSION"),
        Limit(kind="weekly_all", group="weekly", percent=40.0, severity=None,
              resets_at=NOW + timedelta(days=2), is_active=True, label="WEEKLY"),
    ])
    jsonable(cost(store, usage=usage, billing_day=15))
    pacing = an.pacing_report(store, now=NOW, usage=usage)
    jsonable(pacing)
    assert pacing["session"]["verdict"] == "UNKNOWN"
    # Resets in 2 days leaves 5 of 7 days elapsed, so 40 % is behind pace.
    assert pacing["weekly"]["verdict"] == "BEHIND"
    store.close()


def test_empty_window_efficiency_gives_zeros_and_none_ratios(data):
    report = an.efficiency_report(data, start=datetime(2020, 1, 1, tzinfo=UTC),
                                  end=datetime(2020, 1, 2, tzinfo=UTC), tz=UTC)
    jsonable(report)
    assert report["cache"]["hit_rate"] is None and report["cache"]["cost_usd"] == 0
    assert report["thinking"]["share"] is None
    assert report["context"] == {"requests": 0, "avg": None, "p50": None, "p95": None, "max": None}
    assert report["compactions"]["per_active_day"] is None
    assert report["active_days"] == 0
    assert report["tools"] == {"total": 0, "builtin": [], "mcp": []}
    assert report["productivity"]["cost_per_pr_usd"] is None
    assert report["productivity"]["cost_per_100_lines_usd"] is None
    assert report["effort"] == [] and report["api_errors"] == 0
    assert report["web"] == {"searches": 0, "fetches": 0}


# ── Windows ───────────────────────────────────────────────────────────────────────

def test_resolve_window_handles_every_name(data):
    def resolve(name, **kwargs):
        options = {"usage": None, "billing_day": None, "tz": UTC, **kwargs}
        return an.resolve_window(name, data, now=NOW, **options)

    assert resolve("today") == (dt(10, 20), dt(10, 21))
    assert resolve("week") == (dt(10, 13, 12), dt(10, 20, 12))
    assert resolve("month") == (dt(10, 1), dt(11, 1))
    assert resolve("cycle") == (dt(10, 1), dt(11, 1))           # calendar month without billing day
    assert resolve("cycle", billing_day=15) == (dt(10, 15), dt(11, 15))
    assert resolve("all") == (None, None)
    # The 5-hour block that holds now: R5 opened it at 11:00, the earlier gap was 5 days long.
    assert resolve("session") == (dt(10, 20, 11), dt(10, 20, 16))
    limit = Limit(kind="session", group="session", percent=10.0, severity=None,
                  resets_at=dt(10, 20, 15), is_active=True, label="SESSION")
    api = UsageSnapshot(fetched_at=NOW, limits=[limit])
    assert resolve("session", usage=api) == (dt(10, 20, 10), dt(10, 20, 15))


def test_session_window_is_empty_when_no_block_holds_now():
    store = Store(":memory:")
    assert an.resolve_window("session", store, now=NOW, usage=None, billing_day=None) == (NOW, NOW)
    store.add_items([rec("old", dt(10, 1, 9))], PRICING)           # block 09:00-14:00 on Oct 1
    assert an.resolve_window("session", store, now=NOW, usage=None, billing_day=None) == (NOW, NOW)
    store.close()


def test_resolve_window_rejects_an_unknown_name(data):
    with pytest.raises(ValueError):
        an.resolve_window("year", data, now=NOW, usage=None, billing_day=None)
    with pytest.raises(ValueError):
        an.cost_report(data, PRICING, now=NOW, usage=None, plan=PLAN, plan_price=None,
                       billing_day=None, breakdown_window="year", tz=UTC)


# ── Cost report ───────────────────────────────────────────────────────────────────

def test_window_totals_match_the_hand_sums(data):
    windows = cost(data)["windows"]
    assert windows["today"]["start"] == "2026-10-20T00:00:00Z"
    assert windows["today"]["end"] == "2026-10-21T00:00:00Z"
    # Today: R5 + R6 + R7. Cost 8 + 0.05 + 0; R7's unknown model is unpriced.
    assert windows["today"]["requests"] == 3
    assert windows["today"]["cost_usd"] == pytest.approx(8.05)
    assert windows["today"]["unpriced_requests"] == 1
    assert windows["today"]["tokens"]["total"] == 3 * M
    # Week (last 7 days, no usage): R4, R5, R6, R7.
    assert windows["week"]["start"] == "2026-10-13T12:00:00Z"
    assert windows["week"]["requests"] == 4
    assert windows["week"]["cost_usd"] == pytest.approx(18.05)
    # Cycle (October): R1, R3, R4, R5, R6, R7. Input 3M, output 2M, cache read 1M.
    cycle = windows["cycle"]
    assert cycle["start"] == "2026-10-01T00:00:00Z" and cycle["end"] == "2026-11-01T00:00:00Z"
    assert cycle["requests"] == 6
    assert cycle["cost_usd"] == pytest.approx(4 + 0.5 + 10 + 8 + 0.05)
    assert cycle["nocache_cost_usd"] == pytest.approx(4 + 0.5 + 10 + 8 + 0.5)
    assert cycle["tokens"] == {"input": 3 * M, "output": 2 * M, "cache_read": M,
                               "cache_write": 0, "thinking": 0, "total": 6 * M}
    assert windows["month"] == cycle
    # Session: the block 11:00-16:00 holds R5, R6 and R7.
    assert windows["session"]["start"] == "2026-10-20T11:00:00Z"
    assert windows["session"]["requests"] == 3
    # All time adds R2 (Opus, $4).
    assert windows["all"]["requests"] == 7
    assert windows["all"]["cost_usd"] == pytest.approx(26.55)
    assert windows["all"]["tokens"]["total"] == 7 * M


def test_breakdowns_sum_to_the_window_totals(data):
    report = cost(data)
    cycle = report["windows"]["cycle"]
    models = report["breakdowns"]["by_model"]
    assert [(m["model"], m["name"], m["requests"]) for m in models] == [
        (OPUS, "Opus 5.5", 2), (SONNET, "Sonnet 5", 1), (HAIKU, "Haiku 5.5", 2),
        ("mystery-1", "mystery-1", 1)]
    assert models[0]["cost_usd"] == pytest.approx(12.0)
    assert models[2]["cost_usd"] == pytest.approx(0.55)
    assert sum(m["requests"] for m in models) == cycle["requests"]
    assert sum(m["cost_usd"] for m in models) == pytest.approx(cycle["cost_usd"])
    assert sum(m["share"] for m in models) == pytest.approx(1.0)

    projects = report["breakdowns"]["by_project"]
    assert [(p["project"], p["name"]) for p in projects] == [
        ("beta", "beta"), ("alpha", "alpha"), ("gamma", "gamma")]
    assert projects[1]["requests"] == 3 and projects[1]["cost_usd"] == pytest.approx(4.5)
    assert projects[1]["tokens"] == 3 * M
    assert sum(p["cost_usd"] for p in projects) == pytest.approx(cycle["cost_usd"])
    assert sum(p["requests"] for p in projects) == cycle["requests"]

    threads = report["breakdowns"]["by_thread"]
    assert threads["main"] == {"requests": 5, "tokens": 5 * M, "cost_usd": pytest.approx(22.05)}
    assert threads["subagent"] == {"requests": 1, "tokens": M, "cost_usd": pytest.approx(0.5)}
    haiku = report["breakdowns"]["haiku"]
    assert haiku["main"] == {"requests": 1, "tokens": M, "cost_usd": pytest.approx(0.05)}
    assert haiku["subagent"] == {"requests": 1, "tokens": M, "cost_usd": pytest.approx(0.5)}
    assert report["breakdowns"]["fast"] == {"requests": 1, "cost_usd": pytest.approx(8.0)}


def test_breakdown_window_can_be_any_named_window(data):
    report = cost(data, breakdown="today")
    assert report["breakdowns"]["window"] == "today"
    assert [m["model"] for m in report["breakdowns"]["by_model"]] == [OPUS, HAIKU, "mystery-1"]
    assert sum(m["cost_usd"] for m in report["breakdowns"]["by_model"]) == pytest.approx(8.05)


def test_by_project_folds_everything_past_fifteen_into_other():
    store = Store(":memory:")
    store.add_items([
        rec(f"P{i:02d}", dt(10, 1, 10), inp=(i + 1) * M, session=f"s{i:02d}",
            project=f"p{i:02d}", cwd=f"C:/work/p{i:02d}")
        for i in range(17)], PRICING)
    rows = cost(store, breakdown="all")["breakdowns"]["by_project"]
    store.close()
    # Project i costs 4 * (i + 1) dollars, so the total is 4 * 153 = 612.
    assert len(rows) == 16
    assert rows[0]["project"] == "p16" and rows[0]["cost_usd"] == pytest.approx(68.0)
    assert rows[14]["project"] == "p02" and rows[14]["cost_usd"] == pytest.approx(12.0)
    other = rows[15]
    assert other["project"] == "other" and other["name"] == "Other"
    assert other["requests"] == 2 and other["tokens"] == 3 * M
    assert other["cost_usd"] == pytest.approx(12.0)
    assert other["share"] == pytest.approx(12.0 / 612.0)


def test_value_multiplier_uses_the_override_before_the_detected_price(data):
    detected = cost(data, plan=PLAN)
    assert detected["plan"]["price_usd"] == 100.0
    assert detected["plan"]["price_source"] == "detected"
    assert detected["value"]["multiplier"] == pytest.approx(22.55 / 100)

    override = cost(data, plan=PLAN, plan_price=50.0)
    assert override["plan"]["price_usd"] == 50.0
    assert override["plan"]["price_source"] == "override"
    assert override["value"]["multiplier"] == pytest.approx(22.55 / 50)

    unpriced = cost(data, plan=NO_PLAN)
    assert unpriced["value"]["multiplier"] is None
    assert all(row["multiplier"] is None for row in unpriced["monthly"])


def test_projection_extrapolates_the_cycle_cost_and_waits_an_hour():
    store = build_store()
    value = cost(store)["value"]
    # Oct 1 00:00 to Oct 20 12:00 is 468 h of a 744 h cycle.
    assert value["projected_cycle_cost_usd"] == pytest.approx(22.55 * 744 / 468)
    assert value["cycle_start"] == "2026-10-01T00:00:00Z"
    early = cost(store, now=datetime(2026, 10, 1, 0, 30, tzinfo=UTC))["value"]
    assert early["projected_cycle_cost_usd"] is None
    store.close()


def test_monthly_rows_cover_active_months_oldest_first(data):
    monthly = cost(data)["monthly"]
    assert [row["month"] for row in monthly] == ["2026-09", "2026-10"]
    assert monthly[0]["requests"] == 1 and monthly[0]["tokens"] == M
    assert monthly[0]["cost_usd"] == pytest.approx(4.0)
    assert monthly[0]["multiplier"] == pytest.approx(4.0 / 100)
    assert monthly[1]["requests"] == 6 and monthly[1]["tokens"] == 6 * M
    assert monthly[1]["cost_usd"] == pytest.approx(22.55)
    assert sum(row["cost_usd"] for row in monthly) == pytest.approx(
        cost(data)["windows"]["all"]["cost_usd"])


def test_top_sessions_week_carries_titles_and_models(data):
    rows = cost(data)["top_sessions_week"]
    assert [row["session_id"] for row in rows] == ["s2", "s3"]
    beta = rows[0]
    assert beta["title"] == "Custom two"                 # custom title wins over the AI title
    assert beta["project"] == "beta" and beta["project_name"] == "beta"
    assert beta["requests"] == 2 and beta["tokens"] == 2 * M
    assert beta["cost_usd"] == pytest.approx(18.0)
    assert beta["subagent_cost_usd"] == pytest.approx(0.0)
    assert beta["models"] == ["Sonnet 5", "Opus 5.5"]
    assert beta["prs"] == 1                              # PR A, opened Oct 15
    untitled = rows[1]
    assert untitled["title"] is None and untitled["cost_usd"] == pytest.approx(0.05)
    assert untitled["models"] == ["Haiku 5.5", "mystery-1"]


def test_spend_is_passed_through_only_from_the_usage_api(data):
    from usage_monitor.data.models import Spend
    usage = UsageSnapshot(fetched_at=NOW, raw={}, spend=Spend(enabled=True, used=3.5, limit=20.0,
                                                               currency="USD"))
    spend = cost(data, usage=usage)["spend"]
    assert spend["used"] == 3.5 and spend["currency"] == "USD"
    assert cost(data, usage=UsageSnapshot(fetched_at=NOW, raw={}))["spend"] is None


def test_custom_billing_day_moves_the_cycle(data):
    report = cost(data, billing_day=15)
    assert report["windows"]["cycle"]["start"] == "2026-10-15T00:00:00Z"
    assert report["windows"]["cycle"]["requests"] == 4        # R4, R5, R6, R7
    assert report["value"]["cycle_end"] == "2026-11-15T00:00:00Z"


# ── Efficiency ────────────────────────────────────────────────────────────────────

def test_efficiency_cache_thinking_and_context_for_the_cycle(data):
    report = an.efficiency_report(data, start=dt(10, 1), end=dt(11, 1), tz=UTC)
    jsonable(report)
    cache = report["cache"]
    # Fresh input 3M (R1, R5, R7), reads 1M (R6), no writes: hit rate 1 / 4.
    assert cache["hit_rate"] == pytest.approx(0.25)
    assert cache["fresh_input_tokens"] == 3 * M and cache["read_tokens"] == M
    assert cache["write_tokens"] == 0
    assert cache["cost_usd"] == pytest.approx(22.55)
    # Uncached the same tokens cost 4 + 0.5 + 10 + 8 + 0.5 = 23.0, so caching saved 0.45.
    assert cache["nocache_cost_usd"] == pytest.approx(23.0)
    assert cache["saved_usd"] == pytest.approx(0.45)
    # No thinking tokens were recorded; output is R3 + R4 = 2M.
    assert report["thinking"] == {"thinking_tokens": 0, "output_tokens": 2 * M, "share": 0.0}
    # Prompt sizes: 1M, 0, 0, 1M, 1M, 1M. Sorted: 0, 0, 1M, 1M, 1M, 1M.
    context = report["context"]
    assert context["requests"] == 6
    assert context["avg"] == pytest.approx(4 * M / 6)
    assert context["p50"] == M and context["p95"] == M and context["max"] == M
    assert report["web"] == {"searches": 2, "fetches": 1}
    assert report["api_errors"] == 1


def test_context_percentiles_use_nearest_rank(data):
    store = Store(":memory:")
    store.add_items([rec(f"K{k}", dt(10, k), inp=k * 1000, session=f"k{k}") for k in range(1, 21)],
                    PRICING)
    context = an.efficiency_report(store, start=None, end=None, tz=UTC)["context"]
    store.close()
    # Values 1000..20000. Nearest rank: p50 is the 10th, p95 the 19th.
    assert context == {"requests": 20, "avg": pytest.approx(10500.0), "p50": 10000,
                       "p95": 19000, "max": 20000}


def test_efficiency_compactions_active_days_and_tools(data):
    report = an.efficiency_report(data, start=dt(10, 1), end=dt(11, 1), tz=UTC)
    compactions = report["compactions"]
    assert compactions == {"count": 2, "auto": 1, "manual": 1, "per_active_day": 0.5,
                           "avg_pre_tokens": 600.0}
    # Active UTC days in the cycle: Oct 1, 10, 15 and 20.
    assert report["active_days"] == 4
    tools = report["tools"]
    assert tools["total"] == 7                            # the Sep 30 Bash call is outside
    assert tools["builtin"] == [{"name": "Bash", "count": 2}, {"name": "Read", "count": 1}]
    assert tools["mcp"] == [
        {"server": "n8n-mcp", "count": 3, "tools": 2},    # get_workflow twice, search_nodes once
        {"server": "github", "count": 1, "tools": 1},
    ]


def test_productivity_ratios_and_their_divisors(data):
    report = an.efficiency_report(data, start=dt(10, 1), end=dt(11, 1), tz=UTC)
    productivity = report["productivity"]
    assert productivity == {
        "lines_added": 15, "lines_removed": 2, "files_created": 1, "commits": 2, "pushes": 1,
        "prs": 1,                                         # PR B has no ts; PR C is in September
        "cost_per_pr_usd": pytest.approx(22.55),
        "cost_per_100_lines_usd": pytest.approx(22.55 * 100 / 17),
    }
    # Today has lines (5) but no PR, so only the per-PR ratio is None.
    today = an.efficiency_report(data, start=dt(10, 20), end=dt(10, 21), tz=UTC)["productivity"]
    assert today["prs"] == 0 and today["cost_per_pr_usd"] is None
    assert today["cost_per_100_lines_usd"] == pytest.approx(8.05 * 100 / 5)


def test_effort_groups_a_missing_effort_as_default(data):
    effort = an.efficiency_report(data, start=dt(10, 1), end=dt(11, 1), tz=UTC)["effort"]
    assert effort == [
        {"effort": "high", "requests": 1, "cost_usd": pytest.approx(10.0)},
        {"effort": "xhigh", "requests": 1, "cost_usd": pytest.approx(8.0)},
        {"effort": "default", "requests": 4, "cost_usd": pytest.approx(4.55)},
    ]


# ── Pacing ────────────────────────────────────────────────────────────────────────

def session_limit(percent: float, resets: datetime) -> Limit:
    return Limit(kind="session", group="session", percent=percent, severity=None,
                 resets_at=resets, is_active=True, label="SESSION", window_seconds=18000)


def weekly_limit(percent: float, resets: datetime) -> Limit:
    return Limit(kind="weekly_all", group="weekly", percent=percent, severity=None,
                 resets_at=resets, is_active=True, label="WEEKLY", window_seconds=604800)


def usage_at(when: datetime, *limits: Limit) -> UsageSnapshot:
    return UsageSnapshot(fetched_at=when, limits=list(limits), raw={})


def store_with(*snapshots: UsageSnapshot) -> Store:
    store = Store(":memory:")
    for snap in snapshots:
        store.save_usage_snapshot(snap)
    return store


RESETS_15 = dt(10, 20, 15)     # the session window that holds NOW is 10:00-15:00


def test_session_verdict_safe_when_the_slope_lands_after_reset():
    store = store_with(usage_at(dt(10, 20, 11), session_limit(40, RESETS_15)),
                       usage_at(dt(10, 20, 11, 30), session_limit(45, RESETS_15)),
                       # A snapshot of another window must not count.
                       usage_at(dt(10, 20, 11, 10), session_limit(99, dt(10, 20, 16))))
    live = usage_at(NOW, session_limit(50, RESETS_15))
    session = an.pacing_report(store, now=NOW, usage=live)["session"]
    store.close()
    # Points 40, 45, 50 at -1 h, -0.5 h, 0 h: least-squares slope 10 %/h.
    # 50 % more at 10 %/h runs out at 17:00, after the 15:00 reset.
    assert session["slope_pct_per_hour"] == pytest.approx(10.0)
    assert session["verdict"] == "SAFE"
    assert session["projected_limit_at"] is None
    assert session["window_start"] == "2026-10-20T10:00:00Z"
    assert session["resets_at"] == "2026-10-20T15:00:00Z"
    assert session["points"] == [["2026-10-20T11:00:00Z", 40], ["2026-10-20T11:30:00Z", 45],
                                 ["2026-10-20T12:00:00Z", 50]]


def test_session_verdict_at_risk_reports_the_projected_limit_time():
    store = store_with(usage_at(dt(10, 20, 11), session_limit(20, RESETS_15)),
                       usage_at(dt(10, 20, 11, 30), session_limit(35, RESETS_15)))
    session = an.pacing_report(store, now=NOW,
                               usage=usage_at(NOW, session_limit(50, RESETS_15)))["session"]
    store.close()
    # Slope 30 %/h: 50 % more takes 1 h 40 min, so the limit falls at 13:40, before the reset.
    assert session["slope_pct_per_hour"] == pytest.approx(30.0)
    assert session["verdict"] == "AT_RISK"
    assert session["projected_limit_at"] == "2026-10-20T13:40:00Z"


def test_session_verdict_idle_when_usage_is_flat():
    store = store_with(usage_at(dt(10, 20, 11), session_limit(50, RESETS_15)),
                       usage_at(dt(10, 20, 11, 30), session_limit(50, RESETS_15)))
    session = an.pacing_report(store, now=NOW,
                               usage=usage_at(NOW, session_limit(50, RESETS_15)))["session"]
    store.close()
    assert session["slope_pct_per_hour"] == pytest.approx(0.0)
    assert session["verdict"] == "IDLE"


def test_session_verdict_unknown_without_two_points_spanning_ten_minutes():
    lone = an.pacing_report(Store(":memory:"), now=NOW,
                            usage=usage_at(NOW, session_limit(50, RESETS_15)))["session"]
    assert lone["verdict"] == "UNKNOWN" and lone["slope_pct_per_hour"] is None
    assert len(lone["points"]) == 1
    store = store_with(usage_at(dt(10, 20, 11, 55), session_limit(40, RESETS_15)))
    short = an.pacing_report(store, now=NOW,
                             usage=usage_at(NOW, session_limit(41, RESETS_15)))["session"]
    store.close()
    assert short["verdict"] == "UNKNOWN" and short["slope_pct_per_hour"] is None


def test_session_verdict_limited_at_one_hundred_percent_and_none_usage_is_unknown():
    store = store_with(usage_at(dt(10, 20, 11), session_limit(90, RESETS_15)))
    limited = an.pacing_report(store, now=NOW,
                               usage=usage_at(NOW, session_limit(100, RESETS_15)))["session"]
    store.close()
    assert limited["verdict"] == "LIMITED"
    none = an.pacing_report(Store(":memory:"), now=NOW, usage=None)
    assert none["session"] == {"percent": None, "resets_at": None, "window_start": None,
                               "slope_pct_per_hour": None, "projected_limit_at": None,
                               "verdict": "UNKNOWN", "points": []}
    assert none["weekly"]["verdict"] == "UNKNOWN" and none["scoped"] == []


@pytest.mark.parametrize(("percent", "verdict", "delta"), [
    (70, "AHEAD", 70 - 4 / 7 * 100),
    (60, "ON_PACE", 60 - 4 / 7 * 100),
    (40, "BEHIND", 40 - 4 / 7 * 100),
])
def test_weekly_pace_verdicts(percent, verdict, delta):
    resets = dt(10, 23, 12)                        # 3 days after NOW, so 4 of 7 days have passed
    report = an.pacing_report(Store(":memory:"), now=NOW,
                              usage=usage_at(NOW, weekly_limit(percent, resets)))["weekly"]
    assert report["verdict"] == verdict
    assert report["pace_delta_pp"] == pytest.approx(delta)
    assert report["elapsed_pct"] == pytest.approx(4 / 7 * 100)


def test_weekly_pace_budget_projection_and_downsampled_points():
    resets = dt(10, 23, 12)
    store = store_with(usage_at(dt(10, 17, 12), weekly_limit(30, resets)),
                       usage_at(dt(10, 18, 10), weekly_limit(45, resets)),
                       usage_at(dt(10, 18, 10, 40), weekly_limit(47, resets)))
    weekly = an.pacing_report(store, now=NOW,
                              usage=usage_at(NOW, weekly_limit(60, resets)))["weekly"]
    store.close()
    assert weekly["window_start"] == "2026-10-16T12:00:00Z"
    assert weekly["verdict"] == "ON_PACE"
    # 40 points left over 3 days is 13.33 points a day; an even week is 100/7 a day.
    assert weekly["daily_budget_pct"] == pytest.approx(40 / 3)
    assert weekly["even_daily_pct"] == pytest.approx(100 / 7)
    # 60 % used after 4/7 of the week projects to 105 % by the end.
    assert weekly["projected_end_pct"] == pytest.approx(105.0)
    assert weekly["points"] == [["2026-10-17T12:00:00Z", 30], ["2026-10-18T10:40:00Z", 47],
                                ["2026-10-20T12:00:00Z", 60]]


def test_projection_is_withheld_in_the_first_five_percent_of_the_week():
    resets = NOW + timedelta(days=7) - timedelta(hours=6)      # 6 h of 168 h elapsed (3.6 %)
    weekly = an.pacing_report(Store(":memory:"), now=NOW,
                              usage=usage_at(NOW, weekly_limit(10, resets)))["weekly"]
    assert weekly["projected_end_pct"] is None


def test_scoped_limit_entry_has_its_own_window_and_history():
    resets = dt(10, 23, 12)
    scoped_old = Limit(kind="weekly_scoped", group="weekly", percent=8.0, severity=None,
                       resets_at=resets, is_active=False, label="FABLE WEEKLY",
                       scope_model="Fable", allowance_pct=20.0, used_pct_of_weekly=5.0)
    store = store_with(usage_at(dt(10, 19, 12), weekly_limit(45, resets), scoped_old))
    scoped_now = Limit(kind="weekly_scoped", group="weekly", percent=10.0, severity=None,
                       resets_at=resets, is_active=False, label="FABLE WEEKLY",
                       scope_model="Fable", allowance_pct=20.0, used_pct_of_weekly=5.0)
    report = an.pacing_report(store, now=NOW,
                              usage=usage_at(NOW, weekly_limit(60, resets), scoped_now))
    store.close()
    (entry,) = report["scoped"]
    assert entry["label"] == "FABLE WEEKLY" and entry["model"] == "Fable"
    assert entry["allowance_pct"] == 20.0 and entry["used_pct_of_weekly"] == 5.0
    assert entry["percent"] == 10.0 and entry["verdict"] == "BEHIND"
    assert entry["daily_budget_pct"] == pytest.approx(30.0)
    assert entry["projected_end_pct"] == pytest.approx(17.5)
    assert entry["points"] == [["2026-10-19T12:00:00Z", 8], ["2026-10-20T12:00:00Z", 10]]


# ── History ───────────────────────────────────────────────────────────────────────

def test_history_days_hour_grid_streaks_and_totals(data):
    report = an.history_report(data, now=NOW, tz=UTC, days=5)
    jsonable(report)
    assert report["tz"] == "UTC"
    days = report["days"]
    assert [d["date"] for d in days] == ["2026-10-16", "2026-10-17", "2026-10-18",
                                          "2026-10-19", "2026-10-20"]
    assert all(d["requests"] == 0 for d in days[:4])
    assert days[4] == {"date": "2026-10-20", "requests": 3, "tokens": 3 * M, "output_tokens": 0,
                       "cost_usd": pytest.approx(8.05)}
    grid = report["hour_weekday"]
    assert len(grid["requests"]) == 7 and all(len(row) == 24 for row in grid["requests"])
    # Row 0 is Monday. R5, R6 and R7 all fall on Tuesday (row 1) in the 11:00 hour.
    assert grid["requests"][1][11] == 3
    assert grid["cost_usd"][1][11] == pytest.approx(8.05)
    assert sum(sum(row) for row in grid["requests"]) == 3
    # Active days: Sep 30, Oct 1, Oct 10, Oct 15, Oct 20.
    assert report["streaks"] == {"current_days": 1, "longest_days": 2,
                                 "longest_start": "2026-09-30", "longest_end": "2026-10-01"}
    assert report["active_days"] == 5
    assert report["busiest_day"] == {"date": "2026-10-15", "cost_usd": pytest.approx(10.0),
                                     "tokens": M}
    assert report["first_use"] == "2026-09-30T23:30:00Z"
    odometer = report["odometer"]
    assert odometer["requests"] == 7 and odometer["total_tokens"] == 7 * M
    assert odometer["output_tokens"] == 2 * M
    assert odometer["cost_usd"] == pytest.approx(26.55)
    comparisons = {c["key"]: c["value"] for c in odometer["comparisons"]}
    assert comparisons["war_and_peace"] == pytest.approx(2 * M / 783_000)
    assert comparisons["britannica"] == pytest.approx(7 * M / 58_700_000)
    assert comparisons["page_stack_m"] == pytest.approx(0.3)


@pytest.mark.parametrize(("days_with_requests", "today", "current", "longest", "start", "end"), [
    # Today and yesterday are quiet, so the current streak is 0.
    ((14, 15, 17, 18), 20, 0, 2, "2026-10-14", "2026-10-15"),     # first of two equal runs wins
    # Yesterday is active but today is not: the streak still counts from yesterday.
    ((17, 18, 19), 20, 3, 3, "2026-10-17", "2026-10-19"),
    # Today is active: the streak ends today.
    ((18, 19, 20), 20, 3, 3, "2026-10-18", "2026-10-20"),
])
def test_streaks_with_gaps(days_with_requests, today, current, longest, start, end):
    store = Store(":memory:")
    store.add_items([rec(f"d{day}", dt(10, day, 12), session=f"s{day}")
                     for day in days_with_requests], PRICING)
    streaks = an.history_report(store, now=dt(10, today, 12), tz=UTC, days=3)["streaks"]
    store.close()
    assert streaks == {"current_days": current, "longest_days": longest,
                       "longest_start": start, "longest_end": end}


def test_history_rejects_a_non_positive_day_count(data):
    with pytest.raises(ValueError):
        an.history_report(data, now=NOW, tz=UTC, days=0)


# ── Sessions and rollups ──────────────────────────────────────────────────────────

def test_sessions_report_orders_by_cost_and_counts_prs(data):
    report = an.sessions_report(data, PRICING, start=None, end=None, limit=2)
    jsonable(report)
    first, second = report["sessions"]
    assert (first["session_id"], second["session_id"]) == ("s2", "s1")
    assert first["title"] == "Custom two" and first["project_name"] == "beta"
    assert first["first_ts"] == "2026-10-15T12:00:00Z"
    assert first["last_ts"] == "2026-10-20T11:00:00Z"
    assert first["prs"] == 1
    assert second["title"] == "AI one"                    # no custom title, so the AI title
    assert second["requests"] == 2 and second["tokens"] == 2 * M
    assert second["cost_usd"] == pytest.approx(4.5)
    assert second["subagent_cost_usd"] == pytest.approx(0.5)
    assert second["models"] == ["Opus 5.5", "Haiku 5.5"]
    assert second["prs"] == 2                            # PR B has no ts, which counts all time


def test_daily_rollups_split_by_local_day_and_respect_the_range(data):
    rows = an.daily_rollups(data, tz=UTC, start=dt(10, 15), end=dt(10, 21))
    assert [row["date"] for row in rows] == ["2026-10-15", "2026-10-20"]
    assert rows[0]["requests"] == 1 and rows[0]["output_tokens"] == M
    assert rows[1]["requests"] == 3 and rows[1]["cache_read_tokens"] == M
    assert rows[1]["cost_usd"] == pytest.approx(8.05)
    assert set(rows[0]) == {"date", "requests", "input_tokens", "output_tokens",
                            "cache_read_tokens", "cache_write_5m_tokens", "cache_write_1h_tokens",
                            "thinking_tokens", "cost_usd", "nocache_cost_usd"}


# ── Local days across DST ─────────────────────────────────────────────────────────

class FlipTZ(tzinfo):
    """A zone whose offset changes at one UTC instant: ``before`` until then, ``after`` from then.

    Written out here so the tests need no tz database. A wall-clock time at or past the new
    offset's transition uses ``after``; a skipped wall time resolves to the old offset.
    """

    def __init__(self, before: timedelta, after: timedelta, flip_utc: datetime) -> None:
        super().__init__()
        self._before = before
        self._after = after
        self._flip = flip_utc.replace(tzinfo=None)

    def utcoffset(self, value):
        if value is None:
            return self._before
        wall = value.replace(tzinfo=None)
        return self._after if wall >= self._flip + self._after else self._before

    def dst(self, value):
        return timedelta(0)

    def tzname(self, value):
        return "FLIP"

    def fromutc(self, value):
        instant = value.replace(tzinfo=None)
        offset = self._after if instant >= self._flip else self._before
        return (instant + offset).replace(tzinfo=self)


# Spring forward: UTC+1 until 01:00 UTC on Mar 29, UTC+2 after. Local Mar 29 has 23 hours.
SPRING = FlipTZ(timedelta(hours=1), timedelta(hours=2), datetime(2026, 3, 29, 1, tzinfo=UTC))
# Fall back: UTC+2 until 01:00 UTC on Oct 25, UTC+1 after. Local Oct 25 has 25 hours.
FALL = FlipTZ(timedelta(hours=2), timedelta(hours=1), datetime(2026, 10, 25, 1, tzinfo=UTC))


def test_today_window_across_spring_forward_is_23_hours_long():
    store = Store(":memory:")
    start, end = an.resolve_window("today", store, now=datetime(2026, 3, 29, 12, tzinfo=UTC),
                                   usage=None, billing_day=None, tz=SPRING)
    store.close()
    assert start == datetime(2026, 3, 28, 23, tzinfo=UTC)
    assert end == datetime(2026, 3, 29, 22, tzinfo=UTC)
    assert end - start == timedelta(hours=23)


def test_history_local_days_and_weekday_hours_across_spring_forward():
    store = Store(":memory:")
    store.add_items([
        rec("A", datetime(2026, 3, 28, 23, 30, tzinfo=UTC), inp=M, session="a"),  # local Sun 00:30
        rec("D", datetime(2026, 3, 29, 0, 30, tzinfo=UTC), inp=M, session="d"),    # local Sun 01:30
        rec("B", datetime(2026, 3, 29, 21, 30, tzinfo=UTC), inp=M, session="b"),   # local Sun 23:30
        rec("C", datetime(2026, 3, 29, 22, 30, tzinfo=UTC), inp=M, session="c"),   # local Mon 00:30
    ], PRICING)
    report = an.history_report(store, now=datetime(2026, 3, 30, 10, tzinfo=UTC), tz=SPRING, days=3)
    days = {d["date"]: d for d in report["days"]}
    assert days["2026-03-28"]["requests"] == 0
    assert days["2026-03-29"]["requests"] == 3 and days["2026-03-29"]["cost_usd"] == pytest.approx(12.0)
    assert days["2026-03-30"]["requests"] == 1
    grid = report["hour_weekday"]["requests"]
    assert grid[6][0] == 1 and grid[6][1] == 1 and grid[6][23] == 1   # Sunday hours
    assert grid[0][0] == 1                                            # Monday 00:00
    assert sum(sum(row) for row in grid) == 4
    rollups = an.daily_rollups(store, tz=SPRING)
    store.close()
    assert [(row["date"], row["requests"]) for row in rollups] == [("2026-03-29", 3),
                                                                   ("2026-03-30", 1)]


def test_daily_rollups_across_fall_back_give_a_25_hour_day():
    store = Store(":memory:")
    store.add_items([
        rec("W", datetime(2026, 10, 24, 21, 30, tzinfo=UTC), inp=M, session="w"),  # Oct 24 23:30
        rec("X", datetime(2026, 10, 24, 22, 30, tzinfo=UTC), inp=M, session="x"),  # Oct 25 00:30
        rec("Y", datetime(2026, 10, 25, 22, 30, tzinfo=UTC), inp=M, session="y"),  # Oct 25 23:30
        rec("Z", datetime(2026, 10, 25, 23, 30, tzinfo=UTC), inp=M, session="z"),  # Oct 26 00:30
    ], PRICING)
    rows = an.daily_rollups(store, tz=FALL)
    store.close()
    assert [(row["date"], row["requests"]) for row in rows] == [
        ("2026-10-24", 1), ("2026-10-25", 2), ("2026-10-26", 1)]


# ── Names and export ──────────────────────────────────────────────────────────────

def test_project_name_is_the_last_path_segment_else_the_slug():
    assert an.project_name("slug", "C:\\Users\\me\\repo\\") == "repo"
    assert an.project_name("slug", "/home/me/app") == "app"
    assert an.project_name("slug", None) == "slug"
    assert an.project_name("slug", "") == "slug"


def test_export_csv_parses_back_with_the_csv_module():
    rows = [
        {"date": "2026-10-20", "requests": 3, "cost_usd": 8.05, "note": None, "flag": True,
         "codes": [1, 2], "meta": {"k": "v"}},
        {"date": "2026-10-21", "requests": 0, "cost_usd": 0.0, "note": "a,b", "flag": False,
         "codes": [], "meta": {}},
    ]
    text = an.export_csv(rows)
    assert text.endswith("\r\n") and text.count("\r\n") == 3
    parsed = list(csv.DictReader(io.StringIO(text)))
    assert list(csv.reader(io.StringIO(text)))[0] == ["date", "requests", "cost_usd", "note",
                                                       "flag", "codes", "meta"]
    assert parsed[0]["requests"] == "3" and parsed[0]["cost_usd"] == "8.05"
    assert parsed[0]["note"] == "" and parsed[0]["flag"] == "true"
    assert json.loads(parsed[0]["codes"]) == [1, 2] and json.loads(parsed[0]["meta"]) == {"k": "v"}
    assert parsed[1]["note"] == "a,b" and parsed[1]["flag"] == "false"


def test_export_json_and_non_finite_values_never_break_an_export():
    body = json.loads(an.export_json([{"x": 1.5}]))
    assert body == {"basis": an.BASIS, "rows": [{"x": 1.5}]}
    odd = an.export_json([{"x": float("inf"), "y": float("nan")}])
    assert json.loads(odd)["rows"] == [{"x": None, "y": None}]
    assert list(csv.DictReader(io.StringIO(an.export_csv([{"x": float("nan")}]))))[0]["x"] == ""


# ── Query shape: a report's work must not grow with its sessions or projects ───────

def counted(store: Store):
    """Wrap store.query and return the list that collects every statement it runs."""
    seen: list[str] = []
    original = store.query

    def query(sql, params=()):
        seen.append(sql)
        return original(sql, params)

    store.query = query
    return seen


def many_sessions(count: int) -> Store:
    """``count`` sessions in ``count`` projects, all inside the week before NOW."""
    store = Store(":memory:")
    store.add_items([
        rec(f"Q{index}", NOW - timedelta(hours=1 + index), model=(OPUS, HAIKU, SONNET)[index % 3],
            inp=M, session=f"q{index}", project=f"proj{index}", cwd=f"C:/work/proj{index}",
            subagent=index % 2 == 1)
        for index in range(count)
    ], PRICING)
    return store


def test_cost_report_runs_the_same_queries_however_many_sessions_there_are():
    small, large = many_sessions(3), many_sessions(9)
    try:
        small_seen, large_seen = counted(small), counted(large)
        cost(small)
        cost(large)
        assert len(small_seen) == len(large_seen)
        assert len(large_seen) <= 12
    finally:
        small.close()
        large.close()


def test_sessions_report_runs_the_same_queries_however_many_sessions_there_are():
    small, large = many_sessions(3), many_sessions(9)
    try:
        small_seen, large_seen = counted(small), counted(large)
        an.sessions_report(small, PRICING, start=None, end=None)
        an.sessions_report(large, PRICING, start=None, end=None)
        assert len(small_seen) == len(large_seen)
    finally:
        small.close()
        large.close()


def test_many_sessions_keep_their_totals_models_and_order():
    store = many_sessions(9)
    try:
        rows = an.sessions_report(store, PRICING, start=None, end=None, limit=4)["sessions"]
        assert len(rows) == 4
        costs = [row["cost_usd"] for row in rows]
        assert costs == sorted(costs, reverse=True)
        top = rows[0]                                    # an Opus session: 1M in at $4
        assert top["cost_usd"] == pytest.approx(4.0) and top["models"] == ["Opus 5.5"]
        assert top["project_name"] == top["project"]
        assert all(row["requests"] == 1 and row["tokens"] == M for row in rows)
    finally:
        store.close()


def test_project_label_counts_every_request_of_the_project_in_the_window():
    store = Store(":memory:")
    try:
        store.add_items([
            rec("P1", dt(10, 1, 10), session="x", project="p", cwd="C:/work/one"),
            rec("P2", dt(10, 2, 10), session="y", project="p", cwd="C:/work/two"),
            rec("P3", dt(10, 3, 10), session="y", project="p", cwd="C:/work/two"),
            rec("P4", dt(10, 4, 10), session="y", project="p", cwd="C:/work/two"),
        ], PRICING)
        rows = an.sessions_report(store, PRICING, start=None, end=None)["sessions"]
        assert {row["session_id"]: row["project_name"] for row in rows} == {"x": "two", "y": "two"}
        narrow = an.sessions_report(store, PRICING, start=dt(10, 1), end=dt(10, 2))["sessions"]
        assert [row["project_name"] for row in narrow] == ["one"]
    finally:
        store.close()


@pytest.mark.parametrize("offset_minutes", [0, 7, -330])
def test_window_totals_agree_with_direct_sums_for_any_zone_offset(data, offset_minutes):
    # An offset that is not a multiple of 15 minutes puts the day edges off the bucket grid.
    tz = timezone(timedelta(minutes=offset_minutes))
    windows = cost(data, tz=tz)["windows"]
    for name in an.WINDOW_NAMES:
        start, end = an.resolve_window(name, data, now=NOW, usage=None, billing_day=None, tz=tz)
        direct = an._sums(data, start, end)
        assert windows[name]["requests"] == direct.requests, name
        assert windows[name]["cost_usd"] == pytest.approx(direct.cost_usd), name
        assert windows[name]["nocache_cost_usd"] == pytest.approx(direct.nocache_cost_usd), name
        assert windows[name]["unpriced_requests"] == direct.unpriced_requests, name
        assert windows[name]["tokens"]["total"] == direct.total_tokens, name


def test_window_edges_belong_to_the_window_that_starts_there():
    store = Store(":memory:")
    try:
        store.add_items([
            rec("E1", dt(10, 19, 23, 59) + timedelta(seconds=59), inp=M),   # last second of Oct 19
            rec("E2", dt(10, 20, 0, 0), inp=M),                             # first second of Oct 20
        ], PRICING)
        windows = cost(store)["windows"]
        assert windows["today"]["requests"] == 1
        assert windows["all"]["requests"] == 2
    finally:
        store.close()
