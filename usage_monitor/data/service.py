"""DataService: the one background poller of the data layer.

One daemon thread runs a cycle about every ingest interval. A cycle fetches the usage API when
due, ingests transcripts when due, recomputes the windows, burn rate and plan, and publishes a
MonitorState to every subscriber on the service thread. A subscriber that needs another thread
marshals the state itself.

Contract: docs/ARCHITECTURE.md, "data/service.py". This module imports nothing from the UI.
"""
from __future__ import annotations

import dataclasses
import math
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from usage_monitor import paths
from usage_monitor.data import windows as win
from usage_monitor.data.ingest import Ingestor
from usage_monitor.data.models import (
    ApiStatus,
    BurnRate,
    IngestProgress,
    MonitorState,
    PlanInfo,
    TokenTotals,
    UsageSnapshot,
    WindowStats,
)
from usage_monitor.data.pricing import BUNDLED_PATH, Pricing, load_best, refresh_remote
from usage_monitor.data.store import Store
from usage_monitor.data.usage_api import FetchResult, UsageClient, plan_from, read_credentials
from usage_monitor.log import get_logger
from usage_monitor.settings import DEFAULTS, Settings

log = get_logger(__name__)

THREAD_NAME = "usage-monitor-data"
PRICING_URL = ""                     # no pricing feed is published yet; an empty URL disables the refresh
PRICING_CACHE_NAME = "pricing.json"  # in app_data_dir(); load_best() uses it when its version is newer
PRICING_REFRESH_EVERY = timedelta(hours=24)
MIN_INGEST_S = 15.0
MIN_POLL_S = 60.0
PAUSED_RECHECK_S = 10.0
PROGRESS_PUBLISH_S = 0.5             # ingest progress is published at most this often
BURN_MINUTES = win.DEFAULT_BURN_MINUTES
SESSION_LOOKBACK_START = 2 * win.SESSION_LENGTH
SESSION_LOOKBACK_MAX = timedelta(days=14)
# Changing one of these settings changes the next cycle, so it wakes the loop. Other keys do not.
WAKE_KEYS = frozenset({"poll_interval_ms", "ingest_interval_s", "billing_day", "plan_price_override"})
PAUSED_DETAIL = "Paused"
LOADING_DETAIL = "Loading usage"


class DataService:
    """Publishes MonitorState from one daemon thread. Create it, subscribe, then start().

    Nothing runs before start(). The store, the pricing table and the remote refresh are used
    only on the service thread. refresh_now() runs a full cycle at once (ingest and a usage
    fetch); the usage client's own backoff still applies.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        store_path: Path | str | None = None,
        pricing: Pricing | None = None,
        usage_client: UsageClient | None = None,
        roots: list[Path] | None = None,
        pause_check: Callable[[], bool] = lambda: False,
        clock: Callable[[], datetime] | None = None,
        pricing_refresh: Callable[[Path], bool] | None = None,
    ) -> None:
        self._settings = settings
        self._store_path = store_path
        self._pricing: Pricing | None = pricing
        self._pricing_injected = pricing is not None
        self._client = usage_client if usage_client is not None else UsageClient(version=_app_version())
        self._roots = roots
        self._pause_check = pause_check
        self._clock = clock if clock is not None else _utc_now
        # Callable(cached_path) -> bool: True when a newer table was written. None uses PRICING_URL.
        self._pricing_refresh = pricing_refresh
        self.state: MonitorState | None = None

        self._lock = threading.Lock()  # guards the fields down to _unsubscribe_settings
        self._subscribers: list[tuple[object, Callable[[MonitorState], None]]] = []
        self._wake = threading.Event()
        self._force = False
        self._stopping = False
        self._thread: threading.Thread | None = None
        self._unsubscribe_settings: Callable[[], None] | None = None

        # Below here only the service thread touches the fields.
        self._store: Store | None = None
        self._pricing_synced = False
        self._plan: PlanInfo = plan_from(None, None)
        self._creds_seen = False
        self._creds_mtime: float | None = None
        self._last_fetch: FetchResult | None = None
        self._last_fetch_at: datetime | None = None
        self._last_ingest_at: datetime | None = None
        self._ingest = IngestProgress()
        self._ingest_error: str | None = None
        self._cycle_error: str | None = None
        self._windows: dict[str, WindowStats] = {}
        self._burn = BurnRate()
        self._unpriced: list[str] = []
        self._pricing_checked_at: datetime | None = None
        self._progress_mono = 0.0

    # ── Public API ──

    def start(self) -> None:
        """Start the service thread. The first cycle runs at once. A call while it runs does nothing."""
        with self._lock:
            if self._thread is not None:
                return
            self._stopping = False
            self._unsubscribe_settings = self._settings.on_change(self._on_setting)
            self._thread = threading.Thread(target=self._run, name=THREAD_NAME, daemon=True)
            self._thread.start()
        log.info("data service started")

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the loop, wait up to ``timeout`` seconds for the thread and close the store.

        A cycle that is running finishes first. Safe to call again, and from a subscriber.
        """
        with self._lock:
            self._stopping = True
            thread = self._thread
            unsubscribe, self._unsubscribe_settings = self._unsubscribe_settings, None
        if unsubscribe is not None:
            unsubscribe()
        self._wake.set()
        if thread is None or thread is threading.current_thread():
            return
        thread.join(timeout)
        if thread.is_alive():
            log.warning("data thread still busy %.1f s after stop(); it closes the store on exit", timeout)
            return
        with self._lock:
            if self._thread is thread:
                self._thread = None

    def subscribe(self, cb: Callable[[MonitorState], None]) -> Callable[[], None]:
        """Call ``cb(state)`` on the service thread after every cycle. Returns an unsubscribe function."""
        token = object()
        with self._lock:
            self._subscribers.append((token, cb))

        def unsubscribe() -> None:
            with self._lock:
                self._subscribers = [entry for entry in self._subscribers if entry[0] is not token]

        return unsubscribe

    def refresh_now(self) -> None:
        """Run a full cycle as soon as possible: ingest and a usage fetch, subject to the client's backoff."""
        with self._lock:
            self._force = True
        self._wake.set()

    # ── The loop ──

    def _run(self) -> None:
        try:
            while not self._stopping:
                self._wake.clear()
                delay = self._safe_cycle()
                if self._stopping:
                    break
                self._wake.wait(delay)
        finally:
            self._close_store()
        log.info("data service stopped")

    def _safe_cycle(self) -> float:
        """Run one cycle. Any exception is logged and recorded, and the loop carries on."""
        try:
            return self._cycle()
        except Exception as exc:  # thread boundary: one failed cycle must not end the loop
            self._cycle_error = f"{type(exc).__name__}: {exc}"[:200]
            log.exception("data cycle failed; the loop retries at the next interval")
            self._publish_best_effort()
            return self._ingest_interval().total_seconds()

    def _cycle(self) -> float:
        """One pass over the data. Returns the number of seconds to wait before the next one."""
        now = self._now()
        if self._is_paused():
            self._publish(self._build_state(now, status=ApiStatus.PAUSED, detail=PAUSED_DETAIL))
            return PAUSED_RECHECK_S
        forced = self._take_force()
        ingest_every = self._ingest_interval()
        creds_changed = self._check_credentials()
        if forced or creds_changed or _due(self._last_fetch_at, self._poll_interval(), now):
            self._fetch(now)
        if forced or _due(self._last_ingest_at, ingest_every, now):
            self._ingest_once(now)
        self._refresh_store_values(now)
        self._cycle_error = None
        self._publish(self._build_state(now))
        self._maybe_refresh_pricing(now)
        return ingest_every.total_seconds()

    def _is_paused(self) -> bool:
        try:
            return bool(self._pause_check())
        except Exception:  # a broken check must not stop the data; treat the desktop as active
            log.exception("pause check failed; treating the workstation as active")
            return False

    def _take_force(self) -> bool:
        with self._lock:
            forced, self._force = self._force, False
        return forced

    def _check_credentials(self) -> bool:
        """True when the sign-in file changed since the last check. Keeps the plan from it.

        The token is read by read_credentials and dropped at once; nothing here keeps or logs it.
        """
        path = paths.credentials_path()
        mtime = _mtime(path)
        if self._creds_seen and mtime == self._creds_mtime:
            return False
        info, _token = read_credentials(path)
        changed = self._creds_seen
        if info.present or mtime is None:
            self._creds_seen, self._creds_mtime = True, mtime
            self._plan = info.plan
        # Otherwise the file exists but could not be read, perhaps mid-write by Claude Code. The
        # old plan stays and the next cycle reads the file again.
        return changed

    def _fetch(self, now: datetime) -> None:
        result = self._client.fetch(now)
        self._last_fetch, self._last_fetch_at = result, now
        if result.fresh and result.snapshot is not None:
            store, _pricing = self._open()
            store.save_usage_snapshot(result.snapshot)

    def _ingest_once(self, now: datetime) -> None:
        store, pricing = self._open()
        self._ingest.running = True
        self._ingest.files_done = self._ingest.files_total = 0
        self._progress_mono = time.monotonic()
        self._publish(self._build_state(now))
        started = time.perf_counter()
        try:
            result = Ingestor(store, pricing, self._roots).run(progress=self._on_progress)
        finally:
            self._ingest.running = False
        self._ingest.files_done = self._ingest.files_total = result.files_scanned
        self._ingest.last_duration_s = time.perf_counter() - started
        self._ingest_error = "; ".join(result.errors[:3]) or None
        self._last_ingest_at = now

    def _on_progress(self, done: int, total: int) -> None:
        self._ingest.files_done, self._ingest.files_total = done, total
        mono = time.monotonic()
        if mono - self._progress_mono >= PROGRESS_PUBLISH_S:
            self._progress_mono = mono
            self._publish_best_effort()

    def _refresh_store_values(self, now: datetime) -> None:
        store, pricing = self._open()
        self._windows = _compute_windows(store, pricing, now, self._live_usage(now),
                                         self._settings.get("billing_day"))
        recent = store.recent_requests(now - timedelta(minutes=BURN_MINUTES))
        self._burn = win.burn_rate(recent, now, BURN_MINUTES)
        self._unpriced = store.unpriced_models()

    def _maybe_refresh_pricing(self, now: datetime) -> None:
        """Best-effort remote pricing check, at most once per PRICING_REFRESH_EVERY."""
        if not _due(self._pricing_checked_at, PRICING_REFRESH_EVERY, now):
            return
        self._pricing_checked_at = now
        refresh = self._pricing_refresh if self._pricing_refresh is not None else _default_refresh
        cached = _cache_path()
        try:
            changed = bool(refresh(cached))
            if changed and not self._pricing_injected:
                self._pricing = load_best(BUNDLED_PATH, cached)
                self._pricing_synced = False  # the next cycle reprices the store under the new table
        except Exception:  # best effort: a failed download never reaches the cycle
            log.exception("pricing refresh failed; the table in use stays")

    # ── Store and pricing ──

    def _open(self) -> tuple[Store, Pricing]:
        """The store and the pricing table, opened on first use. A failure raises; the next cycle retries."""
        if self._store is None:
            path = paths.db_path() if self._store_path is None else self._store_path
            self._store = Store(path)
            self._pricing_synced = False
        if self._pricing is None:
            self._pricing = load_best(BUNDLED_PATH, _cache_path())
        if not self._pricing_synced:
            _sync_pricing(self._store, self._pricing)
            self._pricing_synced = True
        return self._store, self._pricing

    def _close_store(self) -> None:
        store, self._store = self._store, None
        self._pricing_synced = False
        if store is None:
            return
        try:
            store.close()
        except Exception:  # closing is the last step of the thread; nothing else depends on it
            log.exception("closing the usage store failed")

    # ── Publishing ──

    def _publish(self, state: MonitorState) -> None:
        self.state = state
        with self._lock:
            subscribers = list(self._subscribers)
        for _token, cb in subscribers:
            try:
                cb(state)
            except Exception:  # a broken subscriber must not stop the others or the loop
                log.exception("a state subscriber raised; the others still receive the state")

    def _publish_best_effort(self) -> None:
        try:
            self._publish(self._build_state(self._now()))
        except Exception:  # the state could not be built; the next cycle tries again
            log.exception("could not publish the service state")

    def _build_state(self, now: datetime, *, status: ApiStatus | None = None,
                     detail: str | None = None) -> MonitorState:
        """A full state from the last fetch, the last store values and the current plan."""
        fetch = self._last_fetch
        snapshot = fetch.snapshot if fetch is not None else None
        if status is None:
            status = fetch.status if fetch is not None else ApiStatus.LOADING
        if detail is None:
            detail = fetch.detail if fetch is not None else LOADING_DETAIL
        return MonitorState(
            status=status,
            status_detail=detail,
            usage=snapshot,
            usage_fetched_at=snapshot.fetched_at if snapshot is not None else None,
            plan=self._effective_plan(),
            windows=dict(self._windows),
            burn=self._burn,
            unpriced_models=list(self._unpriced),
            ingest=dataclasses.replace(self._ingest, last_error=self._cycle_error or self._ingest_error),
            updated_at=now,
        )

    # ── Helpers ──

    def _live_usage(self, now: datetime) -> UsageSnapshot | None:
        """The last snapshot, keeping only the limits whose window has not yet ended.

        A limit whose resets_at has passed describes a window that is over. Dropping it lets the
        windows module fall back to the clock or to the request history.
        """
        snapshot = self._last_fetch.snapshot if self._last_fetch is not None else None
        if snapshot is None:
            return None
        live = [limit for limit in snapshot.limits if limit.resets_at is not None and limit.resets_at > now]
        return UsageSnapshot(fetched_at=snapshot.fetched_at, limits=live)

    def _effective_plan(self) -> PlanInfo:
        override = self._settings.get("plan_price_override")
        if override is None:
            return self._plan
        return dataclasses.replace(self._plan, monthly_price_usd=float(override))

    def _ingest_interval(self) -> timedelta:
        seconds = _positive(self._settings.get("ingest_interval_s"), DEFAULTS["ingest_interval_s"])
        return timedelta(seconds=max(MIN_INGEST_S, seconds))

    def _poll_interval(self) -> timedelta:
        ms = _positive(self._settings.get("poll_interval_ms"), DEFAULTS["poll_interval_ms"])
        return timedelta(seconds=max(MIN_POLL_S, ms / 1000.0))

    def _now(self) -> datetime:
        value = self._clock()
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

    def _on_setting(self, key: str, _value: Any) -> None:
        if key in WAKE_KEYS:
            self._wake.set()


# ── Module helpers ───────────────────────────────────────────────────────────────

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _app_version() -> str:
    """The version for the usage API's User-Agent. The root version module is optional here."""
    try:
        from version import APP_VERSION
    except ImportError:
        return "dev"
    return APP_VERSION


def _cache_path() -> Path:
    return paths.app_data_dir() / PRICING_CACHE_NAME


def _default_refresh(cached: Path) -> bool:
    """Download a newer pricing table. Does nothing while PRICING_URL is empty."""
    if not PRICING_URL:
        return False
    return refresh_remote(PRICING_URL, cached)


def _sync_pricing(store: Store, pricing: Pricing) -> None:
    """Reprice every stored request when the store was priced under another table version."""
    if store.pricing_version() == pricing.version:
        return
    count = store.reprice(pricing)
    log.info("store repriced under pricing %s (%d request(s))", pricing.version, count)


def _due(last: datetime | None, every: timedelta, now: datetime) -> bool:
    """True when ``every`` has passed since ``last``. A clock that moved backwards counts as due."""
    return last is None or now < last or now - last >= every


def _positive(value: Any, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return float(default)
    number = float(value)
    return number if math.isfinite(number) and number > 0 else float(default)


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _compute_windows(store: Store, pricing: Pricing, now: datetime, usage: UsageSnapshot | None,
                     billing_day: Any) -> dict[str, WindowStats]:
    found = {
        "session": _window(store, pricing, "session", _session_bounds(store, now, usage)),
        "today": _window(store, pricing, "today", win.today(now)),
        "week": _window(store, pricing, "week", win.week(now, usage)),
        "month": _window(store, pricing, "month", win.month(now)),
    }
    cycle = win.cycle(now, billing_day)
    if cycle is not None:
        found["cycle"] = _window(store, pricing, "cycle", cycle)
    found["all"] = _window(store, pricing, "all", (store.first_request_ts(), None))
    return found


def _window(store: Store, pricing: Pricing, name: str,
            bounds: tuple[datetime | None, datetime | None] | None) -> WindowStats:
    """Totals and per-model split over [start, end). No bounds means the window is not open now."""
    if bounds is None:
        return WindowStats(name=name, start=None, end=None)
    start, end = bounds
    return WindowStats(
        name=name,
        start=start,
        end=end,
        totals=store.totals(start, end),
        by_model=_by_model(store.totals_by_model(start, end), pricing),
    )


def _by_model(raw: dict[str, TokenTotals], pricing: Pricing) -> dict[str, TokenTotals]:
    """Per-model totals keyed by display name ("Opus 5.5"), largest cost first.

    Ids that share a display name are summed. The UI has no pricing table, so it cannot map ids.
    """
    merged: dict[str, TokenTotals] = {}
    for model_id, totals in raw.items():
        name = pricing.display_name(model_id) or model_id
        merged[name] = _add_totals(merged[name], totals) if name in merged else totals
    return dict(sorted(merged.items(), key=lambda item: item[1].cost_usd, reverse=True))


def _add_totals(a: TokenTotals, b: TokenTotals) -> TokenTotals:
    return TokenTotals(**{f.name: getattr(a, f.name) + getattr(b, f.name)
                          for f in dataclasses.fields(TokenTotals)})


def _session_bounds(store: Store, now: datetime,
                    usage: UsageSnapshot | None) -> tuple[datetime, datetime] | None:
    if usage is not None and usage.session is not None:  # only ever set while its window is open
        return win.session(now, usage, ())
    return win.session(now, usage, _block_stamps(store, now))


def _block_stamps(store: Store, now: datetime) -> list[datetime]:
    """Request times from the latest run of activity, as win.session needs for its block fallback.

    A session block never runs across a gap of five hours or more, so every block in use now starts
    after the latest such gap. The lookback doubles until that gap is in view, the store has nothing
    older, or SESSION_LOOKBACK_MAX is reached.
    """
    lookback = SESSION_LOOKBACK_START
    while True:
        since = now - lookback
        stamps = [ts for ts, _output, _cost in store.recent_requests(since) if ts <= now]
        start = _last_gap_end(stamps)
        if start is not None:
            return stamps[start:]
        earliest = store.first_request_ts()
        if earliest is None or earliest >= since or lookback >= SESSION_LOOKBACK_MAX:
            return stamps
        lookback = min(lookback * 2, SESSION_LOOKBACK_MAX)


def _last_gap_end(stamps: list[datetime]) -> int | None:
    """Index of the first stamp after the latest gap of five hours or more, or None if there is none."""
    for index in range(len(stamps) - 1, 0, -1):
        if stamps[index] - stamps[index - 1] >= win.SESSION_LENGTH:
            return index
    return None
