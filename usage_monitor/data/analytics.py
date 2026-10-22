"""Read-only reports for the stats dashboard and exports: cost, efficiency, pacing, history.

Each report reads through Store.query on the caller's Store and never writes. Money is
API-equivalent at list prices; the only real money is the usage API's spend. Local days and
hours come from 900-second UTC buckets converted to the caller's zone, so DST days keep their
real length. Every report returns JSON-ready values: no NaN or infinity, datetimes as ISO-8601
UTC strings ending in "Z", local calendar dates as "YYYY-MM-DD".

Queries select only the aggregates a report needs. Bucket rows are kept as plain tuples, since
an all-time pass can have tens of thousands of them.
"""
from __future__ import annotations

import csv
import dataclasses
import io
import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any

from usage_monitor.data import windows
from usage_monitor.data.models import PlanInfo, TokenTotals, UsageSnapshot
from usage_monitor.data.pricing import Pricing
from usage_monitor.data.store import Store

WINDOW_NAMES = ("session", "today", "week", "month", "cycle", "all")
BASIS = "API-equivalent at list prices"
BUCKET_S = 900                  # aggregation bucket in seconds; zone offsets are multiples of it
TOP_PROJECTS = 15
TOP_SESSIONS_WEEK = 5
_CHUNK = 500                    # ids per IN (...) list, well under SQLite's variable limit
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_DAY = timedelta(days=1)
_WEEK = windows.WEEK_LENGTH
_SESSION = windows.SESSION_LENGTH
_RESET_MATCH = timedelta(seconds=60)    # snapshots whose reset time is this close share a window
_SLOPE_SPAN = timedelta(minutes=90)     # the session slope uses points from this long before now
_SLOPE_MIN_SPAN_S = 600                 # ... and needs points spanning at least this many seconds
_IDLE_SLOPE = 0.5                       # %/h below which a session is IDLE
_PACE_BAND = 5.0                        # weekly pace delta (points) beyond which AHEAD / BEHIND
_MIN_ELAPSED_PCT = 5.0                  # a weekly projection needs this much of the window
_MIN_CYCLE_ELAPSED_S = 3600             # a cycle projection needs an hour of the cycle
_EVEN_DAILY_PCT = 100 / 7
_PROMPT_SQL = "(input_tokens + cache_read_tokens + cache_write_5m_tokens + cache_write_1h_tokens)"
_TOKENS_SQL = ("CAST(input_tokens AS REAL) + CAST(output_tokens AS REAL) + "
               "CAST(cache_read_tokens AS REAL) + CAST(cache_write_5m_tokens AS REAL) + "
               "CAST(cache_write_1h_tokens AS REAL)")

# Core sums, in TokenTotals order after the request count. Token sums are REAL so they cannot
# overflow int64. Positions: n, in, out, read, w5, w1, thinking, cost, nocache.
_CORE = (
    "COUNT(*) AS n_req, "
    "COALESCE(SUM(CAST(input_tokens AS REAL)), 0) AS tok_in, "
    "COALESCE(SUM(CAST(output_tokens AS REAL)), 0) AS tok_out, "
    "COALESCE(SUM(CAST(cache_read_tokens AS REAL)), 0) AS tok_read, "
    "COALESCE(SUM(CAST(cache_write_5m_tokens AS REAL)), 0) AS tok_w5, "
    "COALESCE(SUM(CAST(cache_write_1h_tokens AS REAL)), 0) AS tok_w1, "
    "COALESCE(SUM(CAST(thinking_tokens AS REAL)), 0) AS tok_think, "
    "COALESCE(SUM(cost_total), 0.0) AS cost, "
    "COALESCE(SUM(nocache_cost), 0.0) AS nocache"
)
# Full sums: the core plus unpriced, searches, fetches (positions 9, 10, 11).
_SUMS = (
    _CORE + ", "
    "COALESCE(SUM(CASE WHEN priced = 0 THEN 1 ELSE 0 END), 0) AS unpriced, "
    "COALESCE(SUM(CAST(web_search_requests AS REAL)), 0) AS searches, "
    "COALESCE(SUM(CAST(web_fetch_requests AS REAL)), 0) AS fetches"
)
# Light sums (positions: n, cost, tokens) for breakdowns and monthly rows.
_LIGHT = (f"COUNT(*) AS n_req, COALESCE(SUM(cost_total), 0.0) AS cost, "
          f"COALESCE(SUM({_TOKENS_SQL}), 0.0) AS tokens")
# Daily sums (positions: n, cost, tokens, output) for the history report.
_DAILY = _LIGHT + ", COALESCE(SUM(CAST(output_tokens AS REAL)), 0.0) AS output"


# ── Windows ───────────────────────────────────────────────────────────────────

def resolve_window(name: str, store: Store, *, now: datetime, usage: UsageSnapshot | None,
                   billing_day: int | None, tz: tzinfo | None = None,
                   ) -> tuple[datetime | None, datetime | None]:
    """UTC [start, end) of a named window; (None, None) means all time.

    "session" and "week" follow the usage API's windows when they are known. "session" with no
    API window is the 5-hour block that holds ``now``; (now, now) when no block does.
    "cycle" is the billing cycle, or the calendar month when ``billing_day`` is None.
    """
    if name == "session":
        return _session_window(store, now, usage)
    if name == "today":
        return windows.today(now, tz=tz)
    if name == "week":
        return windows.week(now, usage)
    if name == "month":
        return windows.month(now, tz=tz)
    if name == "cycle":
        cycle = windows.cycle(now, billing_day, tz=tz)
        return cycle if cycle is not None else windows.month(now, tz=tz)
    if name == "all":
        return (None, None)
    raise ValueError(f"unknown window {name!r}; expected one of {', '.join(WINDOW_NAMES)}")


def _session_window(store: Store, now: datetime,
                    usage: UsageSnapshot | None) -> tuple[datetime | None, datetime | None]:
    limit = usage.session if usage is not None else None
    if limit is not None and limit.resets_at is not None:
        return windows.session(now, usage, ())
    found = windows.session(now, None, _block_stamps(store, now))
    return found if found is not None else (now, now)


def _block_stamps(store: Store, now: datetime) -> list[datetime]:
    """Request times from the last gap of 5 h or more before ``now``.

    A request after such a gap always opens a new session block, so the block chain can start
    there. The search looks back a day at a time, doubling, until it finds the gap or runs out of
    requests.
    """
    gap = _SESSION.total_seconds()
    hi = _epoch(now)
    span = _DAY
    while True:
        lo = _epoch(now - span)
        prev = store.query("SELECT MAX(ts) AS ts FROM requests WHERE ts < ?", (lo,))[0]["ts"]
        recent = store.query("SELECT ts FROM requests WHERE ts >= ? AND ts <= ? ORDER BY ts",
                             (lo, hi))
        stamps = ([prev] if prev is not None else []) + [row["ts"] for row in recent]
        cut = next((i for i in range(len(stamps) - 1, 0, -1)
                    if stamps[i] - stamps[i - 1] >= gap), None)
        if cut is not None:
            stamps = stamps[cut:]
            break
        if prev is None:
            break
        span *= 2
    return [_from_epoch(stamp) for stamp in stamps]


# ── Shared helpers ────────────────────────────────────────────────────────────

def project_name(project: str, cwd: str | None) -> str:
    """The last path segment of the working directory, else the project slug."""
    if cwd:
        parts = [part for part in re.split(r"[\\/]+", cwd) if part]
        if parts:
            return parts[-1]
    return project


def _project_label(project: str, cwds: Counter) -> str:
    """Name a project from its most common working directory (ties: smallest path)."""
    if not cwds:
        return project_name(project, None)
    best = min(cwds.items(), key=lambda item: (-item[1], item[0]))[0]
    return project_name(project, best)


def _epoch(value: datetime) -> float:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return (value - _EPOCH).total_seconds()


def _from_epoch(seconds: float) -> datetime:
    return _EPOCH + timedelta(seconds=seconds)


def _local(epoch: float, tz: tzinfo | None) -> datetime:
    """A UTC epoch as a local datetime (tz None = the machine's zone)."""
    return datetime.fromtimestamp(epoch, timezone.utc).astimezone(tz)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _ratio(part: float, whole: float | None) -> float | None:
    return part / whole if whole else None


def _share(part: float, total: float) -> float:
    return part / total if total else 0.0


def _span(start: datetime | None, end: datetime | None,
          column: str = "ts") -> tuple[list[str], list[Any]]:
    """WHERE clauses for [start, end); a None side is open."""
    clauses: list[str] = []
    params: list[Any] = []
    if start is not None:
        clauses.append(f"{column} >= ?")
        params.append(_epoch(start))
    if end is not None:
        clauses.append(f"{column} < ?")
        params.append(_epoch(end))
    return clauses, params


def _where(clauses: Sequence[str]) -> str:
    return " WHERE " + " AND ".join(clauses) if clauses else ""


def _totals(values: Sequence[Any]) -> TokenTotals:
    """TokenTotals from a row of _SUMS values (see the position note on _SUMS)."""
    return TokenTotals(
        requests=int(values[0]), input=int(values[1]), output=int(values[2]),
        cache_read=int(values[3]), cache_write_5m=int(values[4]),
        cache_write_1h=int(values[5]), thinking=int(values[6]),
        cost_usd=float(values[7]), nocache_cost_usd=float(values[8]),
        unpriced_requests=int(values[9]),
    )


def _tally(acc: dict, key: Any, values: Sequence[float]) -> None:
    """Add a tuple of numbers into acc[key], element by element."""
    current = acc.get(key)
    if current is None:
        acc[key] = list(values)
        return
    for index, value in enumerate(values):
        current[index] += value


def _sums_values(store: Store, start: datetime | None, end: datetime | None,
                 extra: Sequence[str] = (), extra_params: Sequence[Any] = ()) -> tuple:
    clauses, params = _span(start, end)
    rows = store.query(f"SELECT {_SUMS} FROM requests{_where([*clauses, *extra])}",
                       [*params, *extra_params])
    return tuple(rows[0])


def _sums(store: Store, start: datetime | None, end: datetime | None,
          extra: Sequence[str] = (), extra_params: Sequence[Any] = ()) -> TokenTotals:
    return _totals(_sums_values(store, start, end, extra, extra_params))


def _buckets(store: Store, select: str, start: datetime | None = None,
             end: datetime | None = None) -> list[tuple[float, tuple]]:
    """(bucket start epoch, select values) for each 900-second UTC bucket that holds requests."""
    clauses, params = _span(start, end)
    rows = store.query(
        f"SELECT CAST(ts / {BUCKET_S} AS INTEGER) AS bucket, {select} FROM requests"
        f"{_where(clauses)} GROUP BY bucket", params)
    return [(row[0] * BUCKET_S, tuple(row)[1:]) for row in rows]


def _plan_price(plan: PlanInfo, override: float | None) -> tuple[float | None, str | None]:
    if override is not None:
        return float(override), "override"
    if plan.monthly_price_usd is not None:
        return float(plan.monthly_price_usd), "detected"
    return None, None


def _window_json(start: datetime | None, end: datetime | None, totals: TokenTotals) -> dict:
    return {
        "start": _iso(start), "end": _iso(end), "requests": totals.requests,
        "tokens": {"input": totals.input, "output": totals.output,
                   "cache_read": totals.cache_read, "cache_write": totals.cache_write,
                   "thinking": totals.thinking, "total": totals.total_tokens},
        "cost_usd": totals.cost_usd, "nocache_cost_usd": totals.nocache_cost_usd,
        "unpriced_requests": totals.unpriced_requests,
    }


def _spend(usage: UsageSnapshot | None) -> dict | None:
    if usage is None or usage.spend is None:
        return None
    return dataclasses.asdict(usage.spend)


def _clean(value: Any) -> Any:
    """Copy with every non-finite float replaced by None, so json.dumps(allow_nan=False) works."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    return value


def _mcp_parts(name: str) -> tuple[str | None, str]:
    """(server, tool) for mcp__<server>__<tool>; (None, name) for anything else."""
    if name.startswith("mcp__"):
        server, sep, tool = name[len("mcp__"):].partition("__")
        if sep and server and tool:
            return server, tool
    return None, name


# ── Cost ──────────────────────────────────────────────────────────────────────

def cost_report(store: Store, pricing: Pricing, *, now: datetime, usage: UsageSnapshot | None,
                plan: PlanInfo, plan_price: float | None, billing_day: int | None,
                breakdown_window: str = "cycle", tz: tzinfo | None = None) -> dict:
    """Totals per window, the plan's value multiplier, monthly rows and breakdowns."""
    if breakdown_window not in WINDOW_NAMES:
        raise ValueError(f"unknown window {breakdown_window!r}")
    # One pass over the table: all-time buckets with the full sums. Windows whose edges fall on
    # bucket edges (local midnights) and the monthly rows are folded from them; only windows
    # with arbitrary edges (the API's session and week) get their own indexed range query.
    buckets = _buckets(store, _SUMS)
    spans: dict[str, tuple[datetime | None, datetime | None]] = {}
    totals: dict[str, TokenTotals] = {}
    for name in WINDOW_NAMES:
        spans[name] = resolve_window(name, store, now=now, usage=usage,
                                     billing_day=billing_day, tz=tz)
        # Equal spans (the cycle is the calendar month without a billing day) share one result.
        twin = next((other for other in totals if spans[other] == spans[name]), None)
        if twin is not None:
            totals[name] = totals[twin]
        elif all(_on_bucket_edge(edge) for edge in spans[name]):
            totals[name] = _totals(_fold(buckets, *spans[name]))
        else:
            totals[name] = _sums(store, *spans[name])

    price, source = _plan_price(plan, plan_price)
    cycle_start, cycle_end = spans["cycle"]
    cycle_cost = totals["cycle"].cost_usd
    elapsed = (now - cycle_start).total_seconds()
    span = (cycle_end - cycle_start).total_seconds()
    projected = None
    if elapsed >= _MIN_CYCLE_ELAPSED_S and span > 0:
        projected = cycle_cost * span / elapsed

    monthly_acc: dict[str, list[float]] = {}
    for epoch, values in buckets:
        local = _local(epoch, tz)
        # _SUMS positions: 0 requests, 1-5 input/output/read/write 5m/write 1h, 7 cost.
        _tally(monthly_acc, f"{local.year:04d}-{local.month:02d}",
               (values[0], values[7], sum(values[1:6])))
    monthly = [{"month": key, "requests": int(item[0]), "tokens": int(item[2]),
                "cost_usd": item[1], "multiplier": _ratio(item[1], price)}
               for key, item in sorted(monthly_acc.items())]

    b_start, b_end = spans[breakdown_window]
    groups = _breakdown_groups(store, b_start, b_end)
    week_start, week_end = spans["week"]
    return _clean({
        "basis": BASIS,
        "windows": {name: _window_json(*spans[name], totals[name]) for name in WINDOW_NAMES},
        "plan": {"name": plan.name, "subscription_type": plan.subscription_type,
                 "rate_limit_tier": plan.rate_limit_tier, "price_usd": price,
                 "price_source": source},
        "value": {"cycle_start": _iso(cycle_start), "cycle_end": _iso(cycle_end),
                  "cycle_cost_usd": cycle_cost, "multiplier": _ratio(cycle_cost, price),
                  "projected_cycle_cost_usd": projected},
        "monthly": monthly,
        "breakdowns": {
            "window": breakdown_window,
            "by_model": _by_model(groups, pricing),
            "by_project": _by_project(groups),
            "by_thread": {"main": _brief_light(_pick(groups, subagent=False)),
                          "subagent": _brief_light(_pick(groups, subagent=True))},
            "haiku": {"main": _brief_light(_pick(groups, subagent=False, haiku=True)),
                      "subagent": _brief_light(_pick(groups, subagent=True, haiku=True))},
            "fast": _fast(_pick(groups, fast=True)),
        },
        "top_sessions_week": _sessions(store, pricing, week_start, week_end,
                                       TOP_SESSIONS_WEEK),
        "spend": _spend(usage),
    })


def _on_bucket_edge(moment: datetime | None) -> bool:
    """True for an open side or an instant on a bucket boundary."""
    return moment is None or _epoch(moment) % BUCKET_S == 0


def _fold(buckets: list[tuple[float, tuple]], start: datetime | None,
          end: datetime | None) -> list[float]:
    """Sum the _SUMS values of the buckets inside [start, end); both edges are on bucket edges."""
    lo = -math.inf if start is None else _epoch(start)
    hi = math.inf if end is None else _epoch(end)
    folded = [0.0] * 12
    for epoch, values in buckets:
        if lo <= epoch < hi:
            for index, value in enumerate(values):
                folded[index] += value
    return folded


def _breakdown_groups(store: Store, start: datetime | None,
                      end: datetime | None) -> list[tuple]:
    """One scan, grouped finely enough for every breakdown.

    Rows are (model, is_subagent, fast, project, cwd, requests, cost, tokens); by model, thread,
    haiku, fast and project are all sums over these.
    """
    clauses, params = _span(start, end)
    rows = store.query(
        "SELECT model, is_subagent, COALESCE(speed = 'fast', 0) AS fast, project, cwd, "
        f"{_LIGHT} FROM requests{_where(clauses)} GROUP BY model, is_subagent, fast, project, cwd",
        params)
    return [(row["model"], bool(row["is_subagent"]), bool(row["fast"]), row["project"],
             row["cwd"], int(row["n_req"]), float(row["cost"]), float(row["tokens"]))
            for row in rows]


def _pick(groups: Sequence[tuple], *, subagent: bool | None = None, haiku: bool = False,
          fast: bool = False) -> tuple[int, float, float]:
    """(requests, cost, tokens) summed over the groups that match the given filters."""
    requests, cost, tokens = 0, 0.0, 0.0
    for model, is_sub, is_fast, _, _, n_req, group_cost, group_tokens in groups:
        if subagent is not None and is_sub != subagent:
            continue
        if haiku and "haiku" not in model.lower():
            continue
        if fast and not is_fast:
            continue
        requests += n_req
        cost += group_cost
        tokens += group_tokens
    return requests, cost, tokens


def _fast(values: tuple[int, float, float]) -> dict[str, Any]:
    return {"requests": values[0], "cost_usd": values[1]}


def _brief_light(values: tuple[int, float, float]) -> dict[str, Any]:
    requests, cost_usd, tokens = values
    return {"requests": requests, "tokens": int(tokens), "cost_usd": cost_usd}


def _row(extra: dict[str, Any], requests: int, tokens: float, cost_usd: float,
         grand: float) -> dict[str, Any]:
    return {**extra, "requests": requests, "tokens": int(tokens), "cost_usd": cost_usd,
            "share": _share(cost_usd, grand)}


def _by_model(groups: Sequence[tuple], pricing: Pricing) -> list[dict]:
    per_model: dict[str, list[float]] = {}
    for model, _, _, _, _, n_req, cost, tokens in groups:
        _tally(per_model, model, (n_req, tokens, cost))
    items = list(per_model.items())
    grand = sum(cost_usd for _, (_, _, cost_usd) in items)
    items.sort(key=lambda item: (-item[1][2], item[0]))
    return [_row({"model": model, "name": pricing.display_name(model)}, requests, tokens,
                 cost_usd, grand)
            for model, (requests, tokens, cost_usd) in items]


def _by_project(groups: Sequence[tuple]) -> list[dict]:
    totals: dict[str, list[float]] = {}
    cwds: dict[str, Counter] = defaultdict(Counter)
    for _, _, _, project, cwd, n_req, cost, tokens in groups:
        _tally(totals, project, (n_req, cost, tokens))
        if cwd:
            cwds[project][cwd] += n_req
    return _project_rows(totals, cwds)


def _project_rows(totals: dict[str, list[float]], cwds: dict[str, Counter]) -> list[dict]:
    grand = sum(item[1] for item in totals.values())
    ranked = sorted(totals.items(), key=lambda item: (-item[1][1], item[0]))
    rows = [_row({"project": project, "name": _project_label(project, cwds[project])},
                 int(item[0]), item[2], item[1], grand)
            for project, item in ranked[:TOP_PROJECTS]]
    rest = [item for _, item in ranked[TOP_PROJECTS:]]
    if rest:
        folded = [sum(values) for values in zip(*rest)]
        rows.append(_row({"project": "other", "name": "Other"}, int(folded[0]), folded[2],
                         folded[1], grand))
    return rows


# ── Efficiency ────────────────────────────────────────────────────────────────

def efficiency_report(store: Store, *, start: datetime | None, end: datetime | None,
                      tz: tzinfo | None = None) -> dict:
    """Cache, thinking, context size, compactions, tools, productivity, effort and web use.

    ``tz`` only sets how local days are counted for active_days (None = machine local).
    """
    values = _sums_values(store, start, end)
    totals = _totals(values)
    denominator = totals.input + totals.cache_read + totals.cache_write
    cache = {
        "hit_rate": _ratio(totals.cache_read, denominator),
        "fresh_input_tokens": totals.input, "read_tokens": totals.cache_read,
        "write_tokens": totals.cache_write, "write_5m_tokens": totals.cache_write_5m,
        "write_1h_tokens": totals.cache_write_1h, "cost_usd": totals.cost_usd,
        "nocache_cost_usd": totals.nocache_cost_usd,
        "saved_usd": max(0.0, totals.nocache_cost_usd - totals.cost_usd),
    }
    thinking = {"thinking_tokens": totals.thinking, "output_tokens": totals.output,
                "share": _ratio(totals.thinking, totals.output)}

    clauses, params = _span(start, end)
    where = _where(clauses)
    context = _context(store, where, params)

    active = _active_dates(store, start, end, tz)
    comp = store.query(
        "SELECT COUNT(*) AS n, "
        "COALESCE(SUM(CASE WHEN trigger_kind = 'auto' THEN 1 ELSE 0 END), 0) AS n_auto, "
        "COALESCE(SUM(CASE WHEN trigger_kind = 'manual' THEN 1 ELSE 0 END), 0) AS n_manual, "
        f"AVG(pre_tokens) AS avg_pre FROM compactions{where}", params)[0]
    compactions = {
        "count": comp["n"], "auto": comp["n_auto"], "manual": comp["n_manual"],
        "per_active_day": _ratio(comp["n"], len(active)),
        "avg_pre_tokens": None if comp["avg_pre"] is None else float(comp["avg_pre"]),
    }

    code = store.query(
        "SELECT COALESCE(SUM(lines_added), 0) AS added, "
        "COALESCE(SUM(lines_removed), 0) AS removed, "
        f"COALESCE(SUM(is_new_file), 0) AS created FROM code_changes{where}", params)[0]
    git = store.query(
        "SELECT COALESCE(SUM(is_commit), 0) AS commits, COALESCE(SUM(is_push), 0) AS pushes "
        f"FROM git_events{where}", params)[0]
    prs = store.query(f"SELECT COUNT(*) AS n FROM pull_requests{where}", params)[0]["n"]
    lines = code["added"] + code["removed"]
    productivity = {
        "lines_added": code["added"], "lines_removed": code["removed"],
        "files_created": code["created"], "commits": git["commits"], "pushes": git["pushes"],
        "prs": prs,
        "cost_per_pr_usd": _ratio(totals.cost_usd, prs),
        "cost_per_100_lines_usd": _ratio(totals.cost_usd * 100, lines),
    }

    effort_rows = store.query(
        "SELECT COALESCE(NULLIF(effort, ''), 'default') AS eff, COUNT(*) AS n_req, "
        f"COALESCE(SUM(cost_total), 0.0) AS cost FROM requests{where} GROUP BY eff", params)
    effort = sorted(({"effort": row["eff"], "requests": row["n_req"],
                      "cost_usd": float(row["cost"])} for row in effort_rows),
                    key=lambda item: (-item["cost_usd"], item["effort"]))
    api_errors = store.query(f"SELECT COUNT(*) AS n FROM api_errors{where}", params)[0]["n"]

    return _clean({
        "start": _iso(start), "end": _iso(end), "basis": BASIS,
        "cache": cache, "thinking": thinking, "context": context,
        "compactions": compactions, "active_days": len(active),
        "tools": _tools(store, where, params),
        "productivity": productivity, "effort": effort, "api_errors": api_errors,
        "web": {"searches": int(values[10]), "fetches": int(values[11])},
    })


def _context(store: Store, where: str, params: Sequence[Any]) -> dict:
    """Prompt tokens per request: the mean, the nearest-rank p50 and p95, and the maximum."""
    values = sorted(row[0] for row in store.query(f"SELECT {_PROMPT_SQL} FROM requests{where}",
                                                  params))
    count = len(values)
    if not count:
        return {"requests": 0, "avg": None, "p50": None, "p95": None, "max": None}
    return {
        "requests": count,
        "avg": sum(values) / count,
        "p50": values[_nearest_rank(50, count) - 1],
        "p95": values[_nearest_rank(95, count) - 1],
        "max": values[-1],
    }


def _nearest_rank(pct: int, count: int) -> int:
    """1-based nearest-rank index of percentile ``pct`` among ``count`` sorted values."""
    return (pct * count + 99) // 100          # ceil(pct * count / 100) in integers


def _active_dates(store: Store, start: datetime | None, end: datetime | None,
                  tz: tzinfo | None) -> set[date]:
    return {_local(epoch, tz).date() for epoch, _ in _buckets(store, "COUNT(*)", start, end)}


def _tools(store: Store, where: str, params: Sequence[Any]) -> dict:
    rows = store.query(f"SELECT name, COUNT(*) AS n FROM tool_calls{where} GROUP BY name", params)
    builtin: list[dict] = []
    servers: dict[str, dict] = {}
    for row in rows:
        server, tool = _mcp_parts(row["name"])
        if server is None:
            builtin.append({"name": row["name"], "count": row["n"]})
            continue
        entry = servers.setdefault(server, {"server": server, "count": 0, "tools": set()})
        entry["count"] += row["n"]
        entry["tools"].add(tool)
    builtin.sort(key=lambda item: (-item["count"], item["name"]))
    mcp = sorted(({"server": name, "count": entry["count"], "tools": len(entry["tools"])}
                  for name, entry in servers.items()),
                 key=lambda item: (-item["count"], item["server"]))
    return {"total": sum(row["n"] for row in rows), "builtin": builtin, "mcp": mcp}


# ── Pacing ────────────────────────────────────────────────────────────────────

def pacing_report(store: Store, *, now: datetime, usage: UsageSnapshot | None) -> dict:
    """Where the session and weekly limits stand, and whether the current pace is safe.

    Missing usage gives percent None and verdict UNKNOWN for every limit.
    """
    return _clean({
        "now": _iso(now),
        "session": _session_pace(store, now, usage),
        "weekly": _weekly_pace(store, now, usage),
        "scoped": _scoped_pace(store, now, usage),
    })


def _session_pace(store: Store, now: datetime, usage: UsageSnapshot | None) -> dict:
    limit = usage.session if usage is not None else None
    if limit is None:
        return {"percent": None, "resets_at": None, "window_start": None,
                "slope_pct_per_hour": None, "projected_limit_at": None,
                "verdict": "UNKNOWN", "points": []}
    resets = limit.resets_at
    start = resets - _SESSION if resets is not None else None
    points: list[tuple[datetime, float]] = []
    if start is not None:
        stored = [(snap["ts"], snap["session_pct"]) for snap in store.usage_snapshots(start)
                  if snap["session_pct"] is not None
                  and _same_reset(snap["session_resets_at"], resets)]
        points = _with_live(stored, usage.fetched_at, limit.percent)
    slope = _slope(points, now)
    verdict, projected = _session_verdict(limit.percent, slope, resets, now)
    return {"percent": limit.percent, "resets_at": _iso(resets), "window_start": _iso(start),
            "slope_pct_per_hour": slope, "projected_limit_at": _iso(projected),
            "verdict": verdict, "points": _points_json(points)}


def _session_verdict(percent: float | None, slope: float | None, resets: datetime | None,
                     now: datetime) -> tuple[str, datetime | None]:
    if percent is None:
        return "UNKNOWN", None
    if percent >= 100:
        return "LIMITED", None
    if slope is None:
        return "UNKNOWN", None
    if slope < _IDLE_SLOPE:
        return "IDLE", None
    if resets is None:
        return "UNKNOWN", None
    limit_at = now + timedelta(hours=(100 - percent) / slope)
    if limit_at >= resets:
        return "SAFE", None
    return "AT_RISK", limit_at


def _slope(points: Sequence[tuple[datetime, float]], now: datetime) -> float | None:
    """Least-squares slope in percent per hour over the points from the last 90 minutes."""
    cutoff = now - _SLOPE_SPAN
    recent = [(when, pct) for when, pct in points if when >= cutoff]
    if len(recent) < 2:
        return None
    times = [when for when, _ in recent]
    if (max(times) - min(times)).total_seconds() < _SLOPE_MIN_SPAN_S:
        return None
    xs = [(when - now).total_seconds() / 3600 for when in times]
    ys = [pct for _, pct in recent]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx == 0:
        return None
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    return sxy / sxx


def _weekly_pace(store: Store, now: datetime, usage: UsageSnapshot | None) -> dict:
    limit = usage.weekly if usage is not None else None
    if limit is None:
        return _pace(now, None, None, _WEEK, [])
    resets = limit.resets_at
    start = resets - _WEEK if resets is not None else None
    points: list[tuple[datetime, float]] = []
    if start is not None:
        stored = [(snap["ts"], snap["weekly_pct"]) for snap in store.usage_snapshots(start)
                  if snap["weekly_pct"] is not None
                  and _same_reset(snap["weekly_resets_at"], resets)]
        points = _hourly(_with_live(stored, usage.fetched_at, limit.percent))
    return _pace(now, limit.percent, resets, _WEEK, points)


def _scoped_pace(store: Store, now: datetime, usage: UsageSnapshot | None) -> list[dict]:
    entries: list[dict] = []
    for limit in usage.scoped if usage is not None else []:
        length = timedelta(seconds=limit.window_seconds or _WEEK.total_seconds())
        resets = limit.resets_at
        start = resets - length if resets is not None else None
        points: list[tuple[datetime, float]] = []
        if start is not None:
            stored: list[tuple[datetime, float]] = []
            for snap in store.usage_snapshots(start):
                for item in snap["limits"]:
                    if (item.get("kind") == limit.kind and item.get("label") == limit.label
                            and _same_reset(item.get("resets_at"), resets)
                            and _is_number(item.get("percent"))):
                        stored.append((snap["ts"], float(item["percent"])))
            points = _hourly(_with_live(stored, usage.fetched_at, limit.percent))
        entries.append({"label": limit.label, "model": limit.scope_model,
                        "allowance_pct": limit.allowance_pct,
                        "used_pct_of_weekly": limit.used_pct_of_weekly,
                        **_pace(now, limit.percent, resets, length, points)})
    return entries


def _pace(now: datetime, percent: float | None, resets: datetime | None, length: timedelta,
          points: Sequence[tuple[datetime, float]]) -> dict:
    """Pace of one limit window: elapsed share, verdict, daily budget and projected end."""
    start = resets - length if resets is not None else None
    elapsed = None
    if start is not None:
        elapsed = min(max((now - start).total_seconds() / length.total_seconds(), 0.0), 1.0)
    elapsed_pct = None if elapsed is None else elapsed * 100
    delta = None if percent is None or elapsed_pct is None else percent - elapsed_pct
    if delta is None:
        verdict = "UNKNOWN"
    elif delta > _PACE_BAND:
        verdict = "AHEAD"
    elif delta < -_PACE_BAND:
        verdict = "BEHIND"
    else:
        verdict = "ON_PACE"
    days_left = None if resets is None else (resets - now).total_seconds() / 86400
    budget = None
    if percent is not None and days_left is not None:
        budget = max(0.0, 100.0 - percent) / max(days_left, 1.0)
    projected = None
    if percent is not None and elapsed is not None and elapsed * 100 >= _MIN_ELAPSED_PCT:
        projected = percent / elapsed
    return {"percent": percent, "resets_at": _iso(resets), "window_start": _iso(start),
            "elapsed_pct": elapsed_pct, "pace_delta_pp": delta, "verdict": verdict,
            "daily_budget_pct": budget, "even_daily_pct": _EVEN_DAILY_PCT,
            "projected_end_pct": projected, "points": _points_json(points)}


def _same_reset(stamp: datetime | None, resets: datetime | None) -> bool:
    return stamp is not None and resets is not None and abs(stamp - resets) <= _RESET_MATCH


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _with_live(stored: Sequence[tuple[datetime, float]], fetched_at: datetime | None,
               percent: float | None) -> list[tuple[datetime, float]]:
    """Stored points plus the live usage reading when it is newer than all of them."""
    points = list(stored)
    if percent is not None and fetched_at is not None and (
            not points or fetched_at > points[-1][0]):
        points.append((fetched_at, float(percent)))
    return points


def _hourly(points: Sequence[tuple[datetime, float]]) -> list[tuple[datetime, float]]:
    """One point per UTC hour: the last one in each hour."""
    last: dict[int, tuple[datetime, float]] = {}
    for when, pct in points:
        last[int(_epoch(when) // 3600)] = (when, pct)
    return [last[hour] for hour in sorted(last)]


def _points_json(points: Sequence[tuple[datetime, float]]) -> list[list[Any]]:
    return [[_iso(when), pct] for when, pct in points]


# ── History ───────────────────────────────────────────────────────────────────

def history_report(store: Store, *, now: datetime, tz: tzinfo | None = None,
                   days: int = 371) -> dict:
    """Daily series for the last ``days`` local days, weekday-by-hour grid, streaks and totals."""
    if days < 1:
        raise ValueError("days must be at least 1")
    today = now.astimezone(tz).date()
    first_day = today - timedelta(days=days - 1)
    all_days: dict[date, list[float]] = {}
    grid_requests = [[0] * 24 for _ in range(7)]
    grid_cost = [[0.0] * 24 for _ in range(7)]
    for epoch, (requests, cost_usd, tokens, output) in _buckets(store, _DAILY):
        local = _local(epoch, tz)
        _tally(all_days, local.date(), (requests, cost_usd, tokens, output))
        if first_day <= local.date() <= today:
            grid_requests[local.weekday()][local.hour] += requests
            grid_cost[local.weekday()][local.hour] += cost_usd

    series = []
    for offset in range(days):
        day = first_day + timedelta(days=offset)
        requests, cost_usd, tokens, output = all_days.get(day, (0, 0.0, 0.0, 0.0))
        series.append({"date": day.isoformat(), "requests": int(requests),
                       "tokens": int(tokens), "output_tokens": int(output), "cost_usd": cost_usd})

    busiest = None
    if all_days:
        day, values = min(all_days.items(), key=lambda item: (-item[1][1], item[0]))
        busiest = {"date": day.isoformat(), "cost_usd": values[1], "tokens": int(values[2])}
    total = _totals(_sums_values(store, None, None))
    odometer = {
        "total_tokens": total.total_tokens, "output_tokens": total.output,
        "requests": total.requests, "cost_usd": total.cost_usd,
        "comparisons": [
            {"key": "war_and_peace", "label": "War and Peace (copies)",
             "value": total.output / 783_000},
            {"key": "britannica", "label": "Encyclopaedia Britannica (sets)",
             "value": total.total_tokens / 58_700_000},
            {"key": "page_stack_m", "label": "Stack of printed pages (m)",
             "value": total.output * 0.75 / 500 * 0.0001},
        ],
    }
    return _clean({
        "tz": _tz_label(now, tz),
        "days": series,
        "hour_weekday": {"requests": grid_requests, "cost_usd": grid_cost},
        "streaks": _streaks(list(all_days), today),
        "busiest_day": busiest,
        "first_use": _iso(store.first_request_ts()),
        "active_days": len(all_days),
        "odometer": odometer,
    })


def _streaks(active_days: Sequence[date], today: date) -> dict:
    active = set(active_days)
    longest = (0, None, None)
    run_start: date | None = None
    run = 0
    previous: date | None = None
    for day in sorted(active):
        run = run + 1 if previous is not None and day - previous == _DAY else 1
        if run == 1:
            run_start = day
        if run > longest[0]:
            longest = (run, run_start, day)
        previous = day
    anchor = today if today in active else (today - _DAY if today - _DAY in active else None)
    current = 0
    while anchor is not None and anchor in active:
        current += 1
        anchor -= _DAY
    return {"current_days": current, "longest_days": longest[0],
            "longest_start": None if longest[1] is None else longest[1].isoformat(),
            "longest_end": None if longest[2] is None else longest[2].isoformat()}


def _tz_label(now: datetime, tz: tzinfo | None) -> str:
    local = now.astimezone(tz)
    if local.tzname():
        return local.tzname()
    minutes = int((local.utcoffset() or timedelta(0)).total_seconds() // 60)
    hours, mins = divmod(abs(minutes), 60)
    return f"UTC{'+' if minutes >= 0 else '-'}{hours:02d}:{mins:02d}"


# ── Sessions and daily rollups ────────────────────────────────────────────────

def sessions_report(store: Store, pricing: Pricing, *, start: datetime | None,
                    end: datetime | None, limit: int = 25) -> dict:
    """Sessions with requests in [start, end), most expensive first, at most ``limit`` rows."""
    return _clean({"start": _iso(start), "end": _iso(end), "basis": BASIS,
                   "sessions": _sessions(store, pricing, start, end, limit)})


def _sessions(store: Store, pricing: Pricing, start: datetime | None, end: datetime | None,
              limit: int) -> list[dict]:
    clauses, params = _span(start, end)
    window = _where([*clauses, "session_id IS NOT NULL"])
    # One scan: per session / model / project / cwd. Sessions, their top models and the project
    # labels (most common cwd in the window) are all folded from these rows.
    rows = store.query(
        "SELECT session_id, model, project, cwd, COUNT(*) AS n_req, MIN(ts) AS first_ts, "
        "MAX(ts) AS last_ts, COALESCE(SUM(" + _TOKENS_SQL + "), 0) AS tokens, "
        "COALESCE(SUM(cost_total), 0.0) AS cost, "
        "COALESCE(SUM(CASE WHEN is_subagent = 1 THEN cost_total ELSE 0.0 END), 0.0) AS sub_cost "
        f"FROM requests{window} GROUP BY session_id, model, project, cwd", params)

    cwds: dict[str, Counter] = defaultdict(Counter)
    per_session: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row["cwd"]:
            cwds[row["project"]][row["cwd"]] += row["n_req"]
        item = per_session.get(row["session_id"])
        if item is None:
            item = per_session[row["session_id"]] = {
                "project": row["project"], "n_req": 0, "first_ts": row["first_ts"],
                "last_ts": row["last_ts"], "tokens": 0.0, "cost": 0.0, "sub_cost": 0.0,
                "models": Counter()}
        item["project"] = min(item["project"], row["project"])
        item["n_req"] += row["n_req"]
        item["first_ts"] = min(item["first_ts"], row["first_ts"])
        item["last_ts"] = max(item["last_ts"], row["last_ts"])
        item["tokens"] += row["tokens"]
        item["cost"] += row["cost"]
        item["sub_cost"] += row["sub_cost"]
        item["models"][pricing.display_name(row["model"])] += row["cost"]

    top = sorted(per_session.items(), key=lambda pair: (-pair[1]["cost"], pair[0]))[:max(0, limit)]
    pr_counts = _session_pr_counts(store, [sid for sid, _ in top], start, end)
    labels = {project: _project_label(project, cwds[project])
              for project in {item["project"] for _, item in top}}
    sessions = []
    for sid, item in top:
        ranked = sorted(item["models"].items(), key=lambda pair: (-pair[1], pair[0]))
        meta = store.session_meta(sid)
        title = (meta.custom_title or meta.ai_title or None) if meta is not None else None
        sessions.append({
            "session_id": sid, "title": title, "project": item["project"],
            "project_name": labels[item["project"]],
            "first_ts": _iso(_from_epoch(item["first_ts"])),
            "last_ts": _iso(_from_epoch(item["last_ts"])),
            "requests": item["n_req"], "tokens": int(item["tokens"]),
            "cost_usd": float(item["cost"]), "subagent_cost_usd": float(item["sub_cost"]),
            "models": [name for name, _ in ranked[:3]], "prs": pr_counts.get(sid, 0),
        })
    return sessions


def _session_pr_counts(store: Store, ids: Sequence[str], start: datetime | None,
                       end: datetime | None) -> dict[str, int]:
    """Pull requests per session whose first sighting is in the window."""
    clauses, params = _span(start, end)
    counts: dict[str, int] = {}
    for index in range(0, len(ids), _CHUNK):
        chunk = ids[index:index + _CHUNK]
        marks = ", ".join("?" * len(chunk))
        rows = store.query(
            "SELECT session_id, COUNT(*) AS n FROM pull_requests"
            + _where([f"session_id IN ({marks})", *clauses]) + " GROUP BY session_id",
            [*chunk, *params])
        counts.update({row["session_id"]: row["n"] for row in rows})
    return counts


def daily_rollups(store: Store, *, tz: tzinfo | None = None, start: datetime | None = None,
                  end: datetime | None = None) -> list[dict]:
    """One row per local day with requests, oldest first."""
    per_day: dict[date, list[float]] = {}
    for epoch, values in _buckets(store, _CORE, start, end):
        # Positions in _CORE: 0 n, 1 input, 2 output, 3 cache read, 4 w5, 5 w1, 6 thinking,
        # 7 cost, 8 nocache.
        _tally(per_day, _local(epoch, tz).date(), values)
    return _clean([{
        "date": day.isoformat(), "requests": int(v[0]),
        "input_tokens": int(v[1]), "output_tokens": int(v[2]),
        "cache_read_tokens": int(v[3]), "cache_write_5m_tokens": int(v[4]),
        "cache_write_1h_tokens": int(v[5]), "thinking_tokens": int(v[6]),
        "cost_usd": v[7], "nocache_cost_usd": v[8],
    } for day, v in sorted(per_day.items())])


# ── Export ────────────────────────────────────────────────────────────────────

def export_csv(rows: list[dict]) -> str:
    """CSV with a header row, one line per dict, columns in first-seen order (CRLF line ends).

    No rows gives an empty string, since there are no columns to name.
    """
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    if not columns:
        return ""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    for row in _clean(rows):
        writer.writerow([_cell(row.get(column)) for column in columns])
    return buffer.getvalue()


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, allow_nan=False)
    return value


def export_json(rows: list[dict]) -> str:
    return json.dumps({"basis": BASIS, "rows": _clean(rows)}, allow_nan=False)
