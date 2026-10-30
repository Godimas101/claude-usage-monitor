"""Dev helper: the real webui on the local API, fed by a fake MonitorState (no Win32, no tray).

    python -m usage_monitor.ui.devserver --port 8765
        --scenario normal|warning|critical|stale|guard|no_login|limits_only [--db PATH] [--theme ID[/VARIANT]]

Used for Playwright checks and design review. Settings go to CUM_APPDATA_DIR (or a temporary
folder), never to the owner's real app data, and the server is a dev one, so the "Start with
Windows" switch never reaches the sign-in (Run) entry. The numbers are realistic: the session
window is 31 % elapsed, the weekly window 57 % elapsed, and the month sits at about $1,659
API-equivalent.

The usage guard (#27) works in every scenario: its verdict is the real agent/guard.py evaluated on the fake limits
and the dev settings, so arming it and changing a cap on the Settings page shows at once. The ``guard`` scenario is
the one that holds: the guard is armed and the session is past its cap, two sessions were told of it and one waits
(Resume now releases it). Their files live in a folder of the dev server's own, and "Install" and "Remove" run the
real agent/setup.py on a sandbox (a settings file in the dev folder and a claude that lives in memory), so nothing of
the owner's ~/.claude, the app data folder or Claude Code is ever read or touched.

Two scenarios are for someone who uses Claude on the web or in the app and has no Claude Code on this PC (#28). ``no_login``
has no sign-in: the status is no_credentials, with no limits and no plan, and the ingest has finished with no file to read.
``limits_only`` is signed in with the limits of ``normal`` (its week went to chats), and its statistics store holds no transcript (every window reads
zero, the ingest has finished) but the usage readings the monitor stores at each poll, so the pace and the forecast work. In
both, nothing is counted, so the pages that count Claude Code's records say so. A refresh (the Refresh buttons, "Check again")
moves the fake state's ``updated_at`` as a real monitor's does, so the page sees the cycle it asked for arrive.

The statistics routes (/api/analytics/*, /api/export) read a usage database. With ``--db PATH``
that is the file you name, read the way the app reads its own (it is never created and never
upgraded, so a copy from an older schema is refused with a 503). Pass a COPY, never the live
usage.db: opening a WAL database leaves -wal and -shm files beside it. Its usage snapshots are old,
so the pacing page shows only the fake state's current readings. Without ``--db`` a synthetic
history is built in memory (a year of invented sessions, tool calls, pull requests and usage
readings that lead up to the fake state's limits), so every page has data.

``--theme`` sets the theme the page starts in: nostromo, night_city/edgerun, gates/blue-light.
It is stored in the dev settings like a choice made on the page.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import random
import re
import sqlite3
import tempfile
import threading
import time as clock_time
from collections.abc import Callable, Sequence
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from usage_monitor import paths, themes
from usage_monitor.agent import guard as agent_guard
from usage_monitor.agent import sessions as agent_sessions
from usage_monitor.agent import setup as agent_setup
from usage_monitor.data.models import (
    ApiErrorEvent,
    ApiStatus,
    BreakdownRow,
    BurnRate,
    CodeChange,
    Compaction,
    IngestProgress,
    Limit,
    MonitorState,
    PlanInfo,
    PullRequest,
    RequestRecord,
    SessionMeta,
    Spend,
    TokenTotals,
    ToolCall,
    ToolError,
    UsageSnapshot,
    WindowStats,
)
from usage_monitor.data.pricing import Pricing
from usage_monitor.data.store import Store
from usage_monitor.data.usage_api import EXPIRED_DETAIL, NO_SIGN_IN_DETAIL
from usage_monitor.log import get_logger
from usage_monitor.settings import Settings
from usage_monitor.ui.server import ApiServer, open_statistics_store

log = get_logger(__name__)

SCENARIOS = ("normal", "warning", "critical", "stale", "guard", "no_login", "limits_only")
# Scenarios of someone who uses Claude on the web or in the app (#28): no Claude Code transcript on this PC, so nothing is counted.
NO_TRANSCRIPTS = ("no_login", "limits_only")
_SESSION_S = 5 * 3600
_WEEK_S = 7 * 86400

# Per-model month-to-date totals for the normal scenario: (requests, output, cost_usd, savings_usd).
_MONTH_MODELS = {
    "claude-opus-5-5": (2410, 15_900_000, 1426.90, 12_120.0),
    "claude-sonnet-5-5": (880, 1_310_000, 116.10, 1_180.0),
    "claude-haiku-4-5": (1950, 1_020_000, 99.50, 650.0),
    "claude-haiku-5-5": (410, 180_000, 16.64, 150.0),
}
_SCENARIO_PCT = {
    "normal": (13, 2, 0),
    "warning": (72, 21, 4),
    "critical": (93, 58, 12),
    "stale": (13, 2, 0),
    "guard": (96, 58, 12),          # the session is past the guard's 95 % cap, the week and Fable are not
    "no_login": (0, 0, 0),          # no limits at all: there is no sign-in to read them with
    "limits_only": (13, 2, 0),      # the limits of the normal scenario
}


def _totals(requests: int, output: int, cost: float, savings: float, scale: float = 1.0) -> TokenTotals:
    """Plausible token counts: the 96.4 % cache hit rate and 38 % thinking share hold at any scale."""
    cache_read = int(964_000_000 * scale)
    input_tokens = int(2_000_000 * scale)
    writes_5m = int(30_000_000 * scale)
    writes_1h = int(4_000_000 * scale)
    return TokenTotals(
        requests=int(requests * scale),
        input=input_tokens,
        output=int(output * scale),
        cache_read=cache_read,
        cache_write_5m=writes_5m,
        cache_write_1h=writes_1h,
        thinking=int(output * scale * 0.38),
        cost_usd=round(cost * scale, 2),
        nocache_cost_usd=round((cost + savings) * scale, 2),
    )


def _sum(parts: list[TokenTotals]) -> TokenTotals:
    total = TokenTotals()
    for part in parts:
        total.requests += part.requests
        total.input += part.input
        total.output += part.output
        total.cache_read += part.cache_read
        total.cache_write_5m += part.cache_write_5m
        total.cache_write_1h += part.cache_write_1h
        total.thinking += part.thinking
        total.cost_usd = round(total.cost_usd + part.cost_usd, 2)
        total.nocache_cost_usd = round(total.nocache_cost_usd + part.nocache_cost_usd, 2)
    return total


def fake_state(scenario: str, now: datetime) -> MonitorState:
    """A MonitorState for ``scenario`` with every window, limit and health field filled in."""
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}")
    now = now.astimezone(timezone.utc)
    session_pct, weekly_pct, fable_pct = _SCENARIO_PCT[scenario]
    session_reset = now + timedelta(hours=3, minutes=27)   # 31 % of the 5-hour window elapsed
    weekly_reset = now + timedelta(days=2, hours=23)       # 57 % of the 7-day window elapsed
    severity = {"critical": ("critical", "warning"), "guard": ("critical", "warning"),
                "warning": ("warning", "normal")}.get(scenario, ("normal", "normal"))
    fetched = now - timedelta(minutes=41) if scenario == "stale" else now - timedelta(seconds=12)
    status = {"stale": ApiStatus.STALE, "no_login": ApiStatus.NO_CREDENTIALS}.get(scenario, ApiStatus.LIVE)
    # A missing or an expired sign-in in UsageClient's own words, as the dashboard's sign-in help shows them.
    detail = {ApiStatus.STALE: EXPIRED_DETAIL,
              ApiStatus.NO_CREDENTIALS: NO_SIGN_IN_DETAIL}.get(status, "Live usage from the Claude API.")

    limits = [
        Limit(kind="session", group="session", percent=float(session_pct), severity=severity[0],
              resets_at=session_reset, is_active=True, label="SESSION", window_seconds=_SESSION_S),
        Limit(kind="weekly_all", group="weekly", percent=float(weekly_pct), severity=severity[1],
              resets_at=weekly_reset, is_active=True, label="WEEKLY", window_seconds=_WEEK_S),
        Limit(kind="weekly_scoped", group="weekly", percent=float(fable_pct), severity="normal",
              resets_at=weekly_reset, is_active=True, label="FABLE WEEKLY", scope_model="Fable",
              window_seconds=_WEEK_S, allowance_pct=50.0, used_pct_of_weekly=float(fable_pct)),
    ]
    usage = None if scenario == "no_login" else UsageSnapshot(
        fetched_at=fetched,
        limits=limits,
        # Someone with no Claude Code here spends the week in chats (limits_only); the rest is Claude Code's.
        breakdown=[BreakdownRow("claude_code", "Claude Code", 0.0 if scenario == "limits_only" else 100.0),
                   BreakdownRow("chat", "Chats", 100.0 if scenario == "limits_only" else 0.0),
                   BreakdownRow("cowork", "Cowork", 0.0)],
        breakdown_window_start=weekly_reset - timedelta(days=7),
        spend=Spend(enabled=False),
    )

    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_models = {name: _totals(*row[:2], row[2], row[3]) for name, row in _MONTH_MODELS.items()}
    month = _sum(list(month_models.values()))
    today = _totals(410, 2_350_000, 84.20, 210.0)
    week = _totals(2650, 4_100_000, 412.55, 1_400.0)
    session = _totals(212, 620_000, 31.07, 96.0)
    everything = _totals(9800, 66_000_000, 4812.66, 16_600.0)

    def window(name: str, start: datetime, totals: TokenTotals,
               models: dict[str, TokenTotals] | None = None) -> WindowStats:
        return WindowStats(name=name, start=start, end=None, totals=totals, by_model=models or {})

    windows = {
        "session": window("session", session_reset - timedelta(seconds=_SESSION_S), session),
        "today": window("today", today_start, today),
        "week": window("week", weekly_reset - timedelta(seconds=_WEEK_S), week),
        "month": window("month", month_start, month, month_models),
        "all": window("all", now - timedelta(days=180), everything),
    }
    if scenario in NO_TRANSCRIPTS:      # the same windows with nothing in them; all-time starts at the first request, and there is none
        windows = {name: WindowStats(name=name, start=w.start, end=w.end) for name, w in windows.items()}
        windows["all"] = WindowStats(name="all", start=None, end=None)
    burn_by_scenario = {"normal": (1240.0, 6.10, "NORMAL"), "warning": (2140.0, 10.40, "FAST"),
                        "critical": (3400.0, 17.20, "VERY FAST"), "guard": (3400.0, 17.20, "VERY FAST"),
                        "stale": (0.0, 0.0, "IDLE"), "no_login": (0.0, 0.0, "IDLE"), "limits_only": (0.0, 0.0, "IDLE")}
    tokens_per_min, usd_per_hour, velocity = burn_by_scenario[scenario]
    # A gentle minute-to-minute drift, so the burn trace has a shape to draw.
    drift = 1.0 + 0.12 * math.sin(now.timestamp() / 150.0)
    tokens_per_min, usd_per_hour = round(tokens_per_min * drift, 1), round(usd_per_hour * drift, 2)
    ingest_running = scenario == "warning"
    if scenario in NO_TRANSCRIPTS:      # the first read is done and found no transcript
        ingest = IngestProgress(running=False, files_done=0, files_total=0, last_duration_s=0.01, last_error=None)
    else:
        ingest = IngestProgress(running=ingest_running, files_done=3120 if ingest_running else 4210,
                                files_total=4210, last_duration_s=0.42, last_error=None)
    plan = (PlanInfo(subscription_type=None, rate_limit_tier=None, name="Unknown plan", monthly_price_usd=None)
            if scenario == "no_login" else
            PlanInfo(subscription_type="max", rate_limit_tier="default_claude_max_5x", name="Max 5x", monthly_price_usd=100.0))
    return MonitorState(
        status=status,
        status_detail=detail,
        usage=usage,
        usage_fetched_at=None if usage is None else fetched,
        plan=plan,
        windows=windows,
        burn=BurnRate(tokens_per_min=tokens_per_min, usd_per_hour=usd_per_hour, velocity=velocity,
                      window_minutes=30),
        unpriced_models=["claude-opus-6-preview"] if scenario == "critical" else [],
        ingest=ingest,
        updated_at=now - timedelta(seconds=12),
    )


def _quantised_now() -> datetime:
    """Now, rounded down to the minute, so the fake state (and its ETag) changes once a minute."""
    return datetime.now(timezone.utc).replace(second=0, microsecond=0)


def _app_version() -> str:
    try:
        return (paths.resource_dir() / "VERSION").read_text(encoding="utf-8").strip() or "dev"
    except OSError:
        return "dev"


# ── Synthetic history for the statistics pages (used without --db) ────────────────

HISTORY_DAYS = 380              # how far back the invented history starts
HISTORY_SEED = 20261021         # the same invented year on every run
HISTORY_SCALE = 1.0             # 1 = about 40 000 requests; the tests lower it for speed
LIVE_SESSION_MINUTES = 85       # today's last session runs up to the present, so the session window has data
CHUNK = 4000                    # items per Store.add_items call

_FOLDERS = (                    # (working directory, weight)
    (r"C:\Demo\atlas-web", 30), (r"C:\Demo\atlas-web\packages\design-system", 6), (r"C:\Demo\atlas-api", 20),
    (r"C:\Demo\ledger-tools", 14), (r"C:\Demo\home-automation", 10), (r"D:\Games\prototype", 9),
    (r"C:\Demo\dotfiles", 6), (r"C:\Users\demo\notes", 5),
)
_TITLES = (
    "Fix the login redirect loop", "Add CSV export to the reports page", "Refactor the settings store",
    "Investigate the flaky integration tests", "Write the migration for the new schema",
    "Tidy the build pipeline", "Draft the release notes", "Debug the websocket reconnect",
    "Speed up the dashboard query", "Port the old scripts to the new CLI",
    "Review the pull request feedback", "Set up the staging environment",
)
_TOOLS = (
    ("Bash", 30), ("Read", 22), ("Edit", 16), ("Grep", 8), ("Glob", 5), ("Write", 4), ("TodoWrite", 4),
    ("Task", 3), ("WebSearch", 1), ("mcp__github__create_pull_request", 2), ("mcp__github__get_file_contents", 2),
    ("mcp__n8n-mcp__search_nodes", 2), ("mcp__n8n-mcp__n8n_get_workflow", 1),
)
_GIT_OPS = ((None, 88), ("commit", 6), ("commit+push", 3), ("push", 1.5), ("pr", 1.5))
_HOURS = ((9, 3), (10, 5), (11, 4), (12, 1), (13, 3), (14, 5), (15, 5), (16, 4), (17, 2), (20, 2), (21, 1))
_EFFORTS = ((None, 10), ("low", 8), ("medium", 30), ("high", 35), ("xhigh", 17))
_RECENT_MODELS = (("claude-opus-5-5", 50), ("claude-sonnet-5-5", 25), ("claude-haiku-5-5", 10), ("claude-haiku-4-5", 15))
_OLDER_MODELS = (("claude-opus-4-8", 45), ("claude-sonnet-4-6", 30), ("claude-haiku-4-5", 25))
_SUBAGENT_MODELS = ("claude-haiku-4-5", "claude-haiku-5-5", "claude-sonnet-5-5")
_FORCED_ACTIVE = ((0, 8), (60, 85), (150, 158))     # day offsets from today: a current streak and two long ones
_FORCED_IDLE = ((9, 9), (59, 59), (86, 88), (200, 214), (330, 345))
_WEEKDAY_ODDS = (0.82, 0.82, 0.82, 0.82, 0.8, 0.45, 0.3)


def _local_moment(day: date, hour: int, minute: int = 0) -> datetime:
    """A wall-clock time on a local day as an aware UTC datetime (the machine's zone, which the reports use)."""
    return datetime.combine(day, time(hour, minute)).astimezone(timezone.utc)


class _History:
    """Invents a year of Claude Code use as transcript items: sessions with their requests, tool calls,
    errors, code changes, pull requests, compactions and titles. Deterministic for a given ``now``."""

    def __init__(self, now: datetime, seed: int = HISTORY_SEED, scale: float = 1.0) -> None:
        self.now = now
        self.scale = scale                  # shrinks every session's request count (the tests build a small year)
        self.rng = random.Random(seed)
        self.items: list[Any] = []
        self._n = 0                     # one counter for every id, so keys never collide

    def build(self) -> list[Any]:
        today = self.now.astimezone().date()
        for offset in range(HISTORY_DAYS, -1, -1):
            day = today - timedelta(days=offset)
            if self._active(offset, day):
                for _ in range(self._pick(((2, 35), (3, 40), (4, 25)))):
                    start = _local_moment(day, self._pick(_HOURS), self.rng.randint(0, 50))
                    if start < self.now - timedelta(minutes=LIVE_SESSION_MINUTES + 20):
                        self._session(start, offset)
        self._session(self.now - timedelta(minutes=LIVE_SESSION_MINUTES), 0,
                      requests=max(10, round(48 * self.scale)), model="claude-opus-5-5")
        return self.items

    def _active(self, offset: int, day: date) -> bool:
        if any(low <= offset <= high for low, high in _FORCED_ACTIVE):
            return True
        if any(low <= offset <= high for low, high in _FORCED_IDLE):
            return False
        odds = _WEEKDAY_ODDS[day.weekday()] * (0.6 if offset > 300 else 1.0)    # the first months were lighter
        return self.rng.random() < odds

    def _pick(self, choices: tuple[tuple[Any, float], ...]) -> Any:
        return self.rng.choices([item for item, _ in choices], [weight for _, weight in choices])[0]

    def _id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n:07d}"

    def _session(self, start: datetime, offset: int, requests: int | None = None, model: str | None = None) -> None:
        rng = self.rng
        folder = self._pick(_FOLDERS)
        slug = re.sub(r"[^A-Za-z0-9]", "-", folder)
        sid = self._id("synthetic-session-")
        model = model or self._pick(_RECENT_MODELS if offset <= 150 else _OLDER_MODELS)
        effort = self._pick(_EFFORTS)
        count = requests or max(3, round(rng.randint(20, 80) * self.scale))
        if rng.random() < 0.6:
            title = rng.choice(_TITLES)
            custom = rng.random() < 0.15
            self.items.append(SessionMeta(sid, slug, ai_title=None if custom else title,
                                          custom_title=title if custom else None))
        moment = start
        for index in range(count):
            moment += timedelta(seconds=rng.uniform(40, 200))
            if moment >= self.now - timedelta(minutes=1):
                break
            self._request(sid, slug, folder, moment, index, model, effort)
        if count >= 3 and rng.random() < 0.3 * min(1.0, count / 40):
            trigger = "auto" if rng.random() < 0.85 else "manual"
            self.items.append(Compaction(self._id("compaction-"), sid, min(moment, self.now), trigger,
                                         rng.randint(140_000, 195_000), rng.randint(9_000, 30_000)))

    def _request(self, sid: str, slug: str, folder: str, moment: datetime, index: int, model: str,
                 effort: str | None) -> None:
        rng = self.rng
        number = self._id("")
        output = min(30_000, max(60, int(rng.lognormvariate(math.log(4500), 0.8))))
        fast = model == "claude-opus-5-5" and rng.random() < 0.025
        self.items.append(RequestRecord(
            key=f"msg_{number}|req_{number}", message_id=f"msg_{number}", request_id=f"req_{number}",
            ts=moment, session_id=sid, project=slug, cwd=folder, git_branch="main", is_subagent=False,
            agent_id=None, model=model, input_tokens=rng.randint(80, 3500), output_tokens=output,
            cache_read_tokens=min(600_000, int(60_000 + index * rng.uniform(8_000, 20_000))),
            cache_write_5m_tokens=rng.randint(4_000, 30_000) if index else rng.randint(30_000, 90_000),
            cache_write_1h_tokens=rng.randint(0, 6_000) if rng.random() < 0.15 else 0,
            thinking_tokens=int(output * rng.uniform(0.2, 0.5)) if rng.random() < 0.6 else 0,
            web_search_requests=1 if rng.random() < 0.01 else 0, speed="fast" if fast else "standard",
            service_tier="standard", effort=effort, entrypoint="cli", cc_version="2.1.290"))
        for _ in range(self._pick(((0, 20), (1, 35), (2, 30), (3, 15)))):
            self._tool_call(sid, number, folder, moment)
        if rng.random() < 0.25:                                     # a subagent reads on the side
            sub = self._id("")
            self.items.append(RequestRecord(
                key=f"msg_{sub}|req_{sub}", message_id=f"msg_{sub}", request_id=f"req_{sub}",
                ts=moment + timedelta(seconds=2), session_id=sid, project=slug, cwd=folder, git_branch="main",
                is_subagent=True, agent_id="a" + sub, model=rng.choice(_SUBAGENT_MODELS),
                input_tokens=rng.randint(200, 2000), output_tokens=rng.randint(300, 2500),
                cache_read_tokens=rng.randint(20_000, 120_000), cache_write_5m_tokens=rng.randint(2_000, 12_000),
                entrypoint="cli", cc_version="2.1.290"))
        if rng.random() < 0.004:
            self.items.append(ApiErrorEvent(self._id("api-error-"), sid, moment))

    def _tool_call(self, sid: str, number: str, folder: str, moment: datetime) -> None:
        rng = self.rng
        name = self._pick(_TOOLS)
        use_id = self._id("toolu_")
        ops = self._pick(_GIT_OPS) if name == "Bash" else None
        self.items.append(ToolCall(use_id, name, f"msg_{number}|req_{number}", sid, moment, False, ops))
        if rng.random() < (0.02 if ops and "commit" in ops else 0.05):
            self.items.append(ToolError(use_id, sid, moment + timedelta(seconds=1)))
        if name in ("Edit", "Write"):
            self.items.append(CodeChange(use_id, sid, moment + timedelta(seconds=1), rng.randint(1, 120),
                                         rng.randint(0, 60) if name == "Edit" else 0,
                                         name == "Write" and rng.random() < 0.6))
        if ops and "pr" in ops.split("+"):
            repo = re.split(r"[\\/]", folder)[-1]
            pull = rng.randint(1, 400)
            self.items.append(PullRequest(f"https://github.com/demo/{repo}/pull/{pull}-{self._n}", pull,
                                          f"demo/{repo}", sid, moment + timedelta(seconds=3)))


def _sync_usage_history(store: Store, state: MonitorState) -> None:
    """Store usage readings that lead up to the limits in ``state``, so the pacing page has a slope to show.

    Readings sit on a fixed grid (every 5 minutes over the last 95, every hour over the last week) and
    each rises with the elapsed share of its window to the limit's current percent. Writing the same
    grid again replaces its rows, so repeating this for the next minute's state does not pile rows up.
    """
    usage = state.usage
    if usage is None:
        return
    end = usage.fetched_at
    epoch = end.timestamp()
    stamps = {int((epoch - k * 300) // 300 * 300) for k in range(1, 20)}
    stamps |= {int((epoch - k * 3600) // 3600 * 3600) for k in range(1, 170)}
    for stamp in sorted(stamps):
        at = datetime.fromtimestamp(stamp, timezone.utc)
        limits = [reading for limit in usage.limits if (reading := _reading(limit, at, end)) is not None]
        if limits:
            store.save_usage_snapshot(UsageSnapshot(fetched_at=at, limits=limits))


def _reading(limit: Limit, at: datetime, end: datetime) -> Limit | None:
    """``limit`` as it read at ``at``, or None before its window opened."""
    if limit.resets_at is None:
        return None
    start = limit.resets_at - timedelta(seconds=limit.window_seconds or _WEEK_S)
    span = (end - start).total_seconds()
    if at < start or span <= 0:
        return None
    share = ((at - start).total_seconds() / span) ** 0.85
    return dataclasses.replace(limit, percent=round(limit.percent * share, 1))


class SyntheticHistory:
    """The statistics source for a dev server without --db: an in-memory Store built on first use.

    open() hands the Store over (the server closes it); sync() keeps the usage readings in step with
    the fake state, whose reset times slide forward a minute at a time.
    """

    def __init__(self, scenario: str, now: Any = None, scale: float | None = None) -> None:
        self._scenario = scenario
        self._now = now or _quantised_now
        self._scale = HISTORY_SCALE if scale is None else scale
        self._lock = threading.Lock()
        self._ready: Store | None = None
        self._live: Store | None = None
        self._synced: tuple[Any, ...] | None = None

    def warm(self) -> None:
        """Build the Store now instead of on the first statistics request."""
        with self._lock:
            if self._ready is None:
                self._ready = self._build()

    def open(self) -> Store:
        with self._lock:
            store = self._ready or self._build()
            self._ready, self._live = None, store
            return store

    def sync(self, state: MonitorState) -> None:
        usage = state.usage
        key = tuple(limit.resets_at for limit in usage.limits) if usage is not None else ()
        with self._lock:
            store = self._live
            if store is None or key == self._synced:
                return
            try:
                _sync_usage_history(store, state)
            except sqlite3.Error:       # the server closed this Store; the next open() starts afresh
                self._live = None
                return
            self._synced = key

    def _build(self) -> Store:
        now = self._now()
        began = datetime.now()
        store = Store(":memory:")
        # No transcript at all for someone who has no Claude Code here; the usage readings below are still stored, as a poll does.
        items = [] if self._scenario in NO_TRANSCRIPTS else _History(now, scale=self._scale).build()
        pricing = Pricing.load()
        for start in range(0, len(items), CHUNK):
            store.add_items(items[start:start + CHUNK], pricing)
        state = fake_state(self._scenario, now)
        _sync_usage_history(store, state)
        self._synced = tuple(limit.resets_at for limit in state.usage.limits) if state.usage else ()
        log.info("devserver: built %d synthetic items in %.1f s", len(items), (datetime.now() - began).total_seconds())
        return store


# ── The usage guard (#27) ─────────────────────────────────────────────────────────

# What the "guard" scenario arms: the defaults, with the guard on.
GUARD_SETTINGS = {"guard_enabled": True, "guard_session_cap": 95, "guard_weekly_cap": 90, "guard_scoped_cap": None,
                  "guard_resume_stagger_s": 120}
_TOLD = (("3f9a1c2e-5b7d-4e0a-9c11-7d2f6a8b0e41", 24), ("c81d04b7-62aa-4f3e-8e57-b09a1d3c5f72", 21))   # id, minutes ago
_WAITER = ("77b0aa10-9d3e-4c52-a6f8-12e4c0b7d9a3", r"C:\Demo\atlas-web", 18)                           # id, folder, minutes ago
_FAKE_EXE = r"C:\Dev\ClaudeUsageMonitor\ClaudeUsageMonitor.exe"       # the sandbox's idea of the installed exe: never run
_FAKE_CLAUDE = r"C:\Dev\claude\claude.exe"


def fake_guard(state: MonitorState, settings: Settings, now: datetime) -> dict[str, Any]:
    """The guard's verdict on the fake limits, from the real agent/guard.py and the dev settings."""
    return agent_guard.evaluate(agent_guard.limits_doc(state.usage), agent_guard.policy_from(settings.get), now)


class GuardSessions:
    """The session files of the ``guard`` scenario, in a folder of the dev server's own. The app data folder is never
    used: Resume now there would release the owner's real waiting sessions.

    Two sessions were told of the holding episode and one session waits, as agent/sessions.py keeps them; the server
    reads them through that module. This only writes them. The told sessions are written again whenever the episode
    changes (the fake reset times slide a minute at a time), and the wait ends when Resume now was pressed, as a real
    ``--wait`` process would end it.
    """

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self._started = clock_time.time()
        self._origin: datetime | None = None    # the first moment seen: the minutes "ago" count from it, so they stay put
        self._episode = ""
        self._released = False
        self._lock = threading.Lock()            # the server's threads all ask for the state

    def sync(self, guard: dict[str, Any], now: datetime) -> None:
        with self._lock:
            self._sync(guard, now)

    def _sync(self, guard: dict[str, Any], now: datetime) -> None:
        self._origin = self._origin or now
        episode = agent_guard.episode(guard)
        waiter = self.folder / agent_sessions.WAITING_DIR / f"{os.getpid()}.json"     # this process is alive: its wait counts
        if agent_sessions.resumed_since(self.folder, self._started):
            self._released = True
        if not episode or self._released:
            waiter.unlink(missing_ok=True)
        elif not waiter.exists():
            session_id, folder, minutes = _WAITER
            waiter.parent.mkdir(parents=True, exist_ok=True)
            waiter.write_text(json.dumps({"pid": os.getpid(), "session_id": session_id, "cwd": folder,
                                          "since": _iso(self._origin - timedelta(minutes=minutes))}), encoding="utf-8")
        if episode and episode != self._episode:
            seen = {}
            for session_id, minutes in _TOLD:
                told_at = _iso(self._origin - timedelta(minutes=minutes))
                seen[session_id] = {"at": told_at, "steps": {}, "guard": {"episode": episode, "told_at": told_at}}
            self.folder.mkdir(parents=True, exist_ok=True)
            (self.folder / agent_sessions.SEEN_NAME).write_text(json.dumps(seen), encoding="utf-8")
        self._episode = episode


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class _DevClaude:
    """The claude command, in memory, for the sandbox: ``--version`` and ``mcp get`` / ``add`` / ``remove`` as Claude
    Code answers them. It runs nothing."""

    def __init__(self) -> None:
        self.tool: tuple[str, list[str]] | None = None

    def __call__(self, argv: Sequence[str]) -> agent_setup.Ran:
        words = list(argv)[1:]
        if words == ["--version"]:
            return agent_setup.Ran(0, "2.1.160 (Claude Code)\n", "")
        if words[:3] == ["mcp", "get", agent_setup.MCP_NAME]:
            if self.tool is None:
                return agent_setup.Ran(1, "", f'No MCP server found with name: "{agent_setup.MCP_NAME}".\n')
            command, args = self.tool
            return agent_setup.Ran(0, f"{agent_setup.MCP_NAME}:\n  Scope: User config (available in all your projects)\n"
                                      f"  Status: ✓ Connected\n  Type: stdio\n  Command: {command}\n"
                                      f"  Args: {' '.join(args)}\n", "")
        if words[:2] == ["mcp", "add"] and "--" in words:
            command, *args = words[words.index("--") + 1:]
            self.tool = (command, args)
            return agent_setup.Ran(0, f"Added stdio MCP server {agent_setup.MCP_NAME} to user config\n", "")
        if words[:2] == ["mcp", "remove"]:
            self.tool = None
            return agent_setup.Ran(0, f"Removed MCP server {agent_setup.MCP_NAME} from user config\n", "")
        return agent_setup.Ran(1, "", "the dev server's claude does not know that command")


def dev_setup(folder: Path) -> dict[str, Callable[[], Any]]:
    """The setup callbacks of the dev server: the real agent/setup.py, working on a sandbox. Its "installed exe" and
    "claude" are made-up paths that nothing runs; its Claude Code settings file is ``claude-config/settings.json`` in
    ``folder``; its claude keeps the MCP tool in memory. The page can be clicked through, and the owner's real
    ~/.claude is never read."""
    ctx = agent_setup.Context(
        frozen=True, executable=_FAKE_EXE, env={agent_setup.CONFIG_ENV: str(folder / "claude-config")}, home=folder,
        which=lambda name: _FAKE_CLAUDE if name == agent_setup.CLAUDE_COMMAND else None, run=_DevClaude(),
        now=datetime.now)
    return {"status": lambda: agent_setup.status(ctx), "install": lambda: agent_setup.install(ctx),
            "remove": lambda: agent_setup.remove(ctx)}


def theme_settings(spec: str) -> dict[str, str]:
    """The settings for ``--theme ID[/VARIANT]``. Without a variant the theme's default one is named, so
    the result does not depend on what an earlier run left in the dev settings. ValueError for an unknown theme."""
    theme_id, _, variant = spec.strip().partition("/")
    known = themes.all_themes()
    theme = known.get(theme_id)
    if theme is None:
        raise ValueError(f"unknown theme {theme_id!r} (known: {', '.join(known)})")
    return {"theme_id": theme.id, "theme_variant": variant or theme.default_variant.id}


def make_server(scenario: str, port: int, settings_dir: Path | None = None, *, db: Path | str | None = None,
                theme: str | None = None, warm: bool = False,
                clock: Callable[[], datetime] | None = None) -> tuple[ApiServer, Settings]:
    """A configured ApiServer for ``scenario``. Not started; the caller runs start()/stop().

    ``db`` is the usage database the statistics read (a copy; see the module docstring); without it a
    synthetic history serves them (``warm`` builds it now instead of on the first request). ``theme`` is
    "ID" or "ID/VARIANT". ValueError for a theme that does not exist. ``clock`` returns the current time
    as an aware datetime; it dates the fake state, the invented year and every report, so a test can pin
    them all to one instant (the default is the real clock, rounded down to the minute for the fake state).
    """
    folder = settings_dir or Path(os.environ.get("CUM_APPDATA_DIR") or tempfile.mkdtemp(prefix="cum-dev-"))
    settings = Settings(folder / "devserver-settings.json", save_delay_s=0.2)
    now = clock or _quantised_now
    history = None if db is not None else SyntheticHistory(scenario, now=now)

    def update_check() -> dict[str, Any]:
        return {"available": False, "version": None, "checked": True, "current": _app_version()}

    guard_folder = folder / "guard-dev"
    guard_sessions = GuardSessions(guard_folder) if scenario == "guard" else None
    refreshes = [0]

    def refresh() -> None:
        # A real monitor publishes a new state (a new updated_at) after a refresh, and the page tells that its cycle has run by
        # it ("Check again" on the sign-in help). The fake one moves updated_at by a millisecond a refresh, the least JSON keeps.
        refreshes[0] += 1
        log.info("devserver: refresh requested")

    def state() -> MonitorState:
        moment = now()
        current = fake_state(scenario, moment)
        if refreshes[0]:
            current = dataclasses.replace(current, updated_at=current.updated_at + timedelta(milliseconds=refreshes[0]))
        if history is not None:
            history.sync(current)
        guard = fake_guard(current, settings, moment)
        if guard_sessions is not None:
            guard_sessions.sync(guard, moment)
        return dataclasses.replace(current, guard=guard)

    def open_store() -> Store:
        return history.open() if history is not None else open_statistics_store(Path(str(db)))

    server = ApiServer(
        state,
        settings,
        refresh_cb=refresh,
        update_cb={
            "check": update_check,
            "apply": lambda: {"started": False, "reason": "devserver never updates"},
            "skip": lambda: {"skipped": True},
            "info": lambda: None,
        },
        on_settings_applied=lambda applied: log.info("devserver: applied %s", sorted(applied)),
        version=_app_version(),
        dev=True,
        port=port,
        analytics_store=open_store,
        wall_clock=clock,
        guard_dir=guard_folder,
        setup_cb=dev_setup(folder),
    )
    try:
        if theme:
            server.apply_settings(theme_settings(theme))
        if scenario == "guard":
            server.apply_settings(GUARD_SETTINGS)
    except ValueError:
        settings.close()
        raise
    if warm and history is not None:
        history.warm()
    return server, settings


def main(argv: list[str] | None = None, *, wait: Callable[[], Any] | None = None) -> int:
    """Run the dev server until Ctrl+C. ``wait`` replaces the wait for it (tests)."""
    parser = argparse.ArgumentParser(description="Serve the webui with a fake MonitorState.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--scenario", choices=SCENARIOS, default="normal")
    parser.add_argument("--db", type=Path, metavar="PATH",
                        help="usage database for the statistics pages; pass a COPY of usage.db "
                             "(default: a synthetic history in memory)")
    parser.add_argument("--theme", metavar="ID[/VARIANT]",
                        help="theme to start in, e.g. night_city/edgerun or gates/blue-light")
    args = parser.parse_args(argv)
    if args.db is not None and not args.db.is_file():
        parser.error(f"--db: no such file: {args.db}")
    try:
        server, settings = make_server(args.scenario, args.port, db=args.db, theme=args.theme, warm=True)
    except ValueError as exc:
        parser.error(f"--theme: {exc}")
    try:
        server.start()
        print(f"devserver scenario={args.scenario} url={server.url} "
              f"data={args.db if args.db is not None else 'synthetic'}", flush=True)
        (wait or threading.Event().wait)()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        settings.close()
    return 0


if __name__ == "__main__":  # pragma: no cover - manual dev entry point
    raise SystemExit(main())
