"""Dev helper: the real webui on the local API, fed by a fake MonitorState (no Win32, no tray).

    python -m usage_monitor.ui.devserver --port 8765 --scenario normal|warning|critical|stale

Used for Playwright checks and design review. Settings go to CUM_APPDATA_DIR (or a temporary
folder), never to the owner's real app data, and the server is a dev one, so the "Start with
Windows" switch never reaches the sign-in (Run) entry. The numbers are realistic: the session
window is 31 % elapsed, the weekly window 57 % elapsed, and the month sits at about $1,659
API-equivalent.
"""
from __future__ import annotations

import argparse
import math
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from usage_monitor import paths
from usage_monitor.data.models import (
    ApiStatus,
    BreakdownRow,
    BurnRate,
    IngestProgress,
    Limit,
    MonitorState,
    PlanInfo,
    Spend,
    TokenTotals,
    UsageSnapshot,
    WindowStats,
)
from usage_monitor.log import get_logger
from usage_monitor.settings import Settings
from usage_monitor.ui.server import ApiServer

log = get_logger(__name__)

SCENARIOS = ("normal", "warning", "critical", "stale")
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
    severity = {"critical": ("critical", "warning"), "warning": ("warning", "normal")}.get(scenario, ("normal", "normal"))
    fetched = now - timedelta(minutes=41) if scenario == "stale" else now - timedelta(seconds=12)
    status = ApiStatus.STALE if scenario == "stale" else ApiStatus.LIVE
    detail = ("Token expired. Sign in to Claude Code to restore live usage." if status is ApiStatus.STALE
              else "Live usage from the Claude API.")

    limits = [
        Limit(kind="session", group="session", percent=float(session_pct), severity=severity[0],
              resets_at=session_reset, is_active=True, label="SESSION", window_seconds=_SESSION_S),
        Limit(kind="weekly_all", group="weekly", percent=float(weekly_pct), severity=severity[1],
              resets_at=weekly_reset, is_active=True, label="WEEKLY", window_seconds=_WEEK_S),
        Limit(kind="weekly_scoped", group="weekly", percent=float(fable_pct), severity="normal",
              resets_at=weekly_reset, is_active=True, label="FABLE WEEKLY", scope_model="Fable",
              window_seconds=_WEEK_S, allowance_pct=50.0, used_pct_of_weekly=float(fable_pct)),
    ]
    usage = UsageSnapshot(
        fetched_at=fetched,
        limits=limits,
        breakdown=[BreakdownRow("claude_code", "Claude Code", 100.0),
                   BreakdownRow("chat", "Chats", 0.0),
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
    burn_by_scenario = {"normal": (1240.0, 6.10, "NORMAL"), "warning": (2140.0, 10.40, "FAST"),
                        "critical": (3400.0, 17.20, "VERY FAST"), "stale": (0.0, 0.0, "IDLE")}
    tokens_per_min, usd_per_hour, velocity = burn_by_scenario[scenario]
    # A gentle minute-to-minute drift, so the burn trace has a shape to draw.
    drift = 1.0 + 0.12 * math.sin(now.timestamp() / 150.0)
    tokens_per_min, usd_per_hour = round(tokens_per_min * drift, 1), round(usd_per_hour * drift, 2)
    ingest_running = scenario == "warning"
    return MonitorState(
        status=status,
        status_detail=detail,
        usage=usage,
        usage_fetched_at=fetched,
        plan=PlanInfo(subscription_type="max", rate_limit_tier="default_claude_max_5x",
                      name="Max 5x", monthly_price_usd=100.0),
        windows=windows,
        burn=BurnRate(tokens_per_min=tokens_per_min, usd_per_hour=usd_per_hour, velocity=velocity,
                      window_minutes=30),
        unpriced_models=["claude-opus-6-preview"] if scenario == "critical" else [],
        ingest=IngestProgress(running=ingest_running, files_done=3120 if ingest_running else 4210,
                              files_total=4210, last_duration_s=0.42, last_error=None),
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


def make_server(scenario: str, port: int, settings_dir: Path | None = None) -> tuple[ApiServer, Settings]:
    """A configured ApiServer for ``scenario``. Not started; the caller runs start()/stop()."""
    folder = settings_dir or Path(os.environ.get("CUM_APPDATA_DIR") or tempfile.mkdtemp(prefix="cum-dev-"))
    settings = Settings(folder / "devserver-settings.json", save_delay_s=0.2)

    def update_check() -> dict[str, Any]:
        return {"available": False, "current": _app_version()}

    server = ApiServer(
        lambda: fake_state(scenario, _quantised_now()),
        settings,
        refresh_cb=lambda: log.info("devserver: refresh requested"),
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
    )
    return server, settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the webui with a fake MonitorState.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--scenario", choices=SCENARIOS, default="normal")
    args = parser.parse_args(argv)
    server, settings = make_server(args.scenario, args.port)
    try:
        server.start()
        print(f"devserver scenario={args.scenario} url={server.url}", flush=True)
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        settings.close()
    return 0


if __name__ == "__main__":  # pragma: no cover - manual dev entry point
    raise SystemExit(main())
