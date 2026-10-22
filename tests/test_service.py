"""Tests for usage_monitor.data.service. Offline: synthetic transcripts, a fake clock and fake clients.

Each test runs a real DataService thread against tmp_path. States are read from a subscriber
queue, so no test waits for a poll interval. The app-data and Claude folders are redirected into
tmp_path by environment variables, and remote pricing is always a fake.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from usage_monitor.data import service as svc
from usage_monitor.data import windows as win
from usage_monitor.data.ingest import Ingestor
from usage_monitor.data.models import ApiStatus, Limit, MonitorState, UsageSnapshot
from usage_monitor.data.pricing import BUNDLED_PATH, Pricing
from usage_monitor.data.store import Store
from usage_monitor.data.usage_api import FetchResult, UsageClient
from usage_monitor.settings import Settings

UTC = timezone.utc
NOW = datetime(2026, 10, 21, 12, 0, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)
PRICING = Pricing.load()
OPUS = "claude-opus-5-5"      # input 4, output 20 USD per million tokens
HAIKU = "claude-haiku-4-5"    # input 1, output 5
MYSTERY = "claude-mystery-9"  # no price row
SESSION = "5e1f0c2a-7d3b-4c8e-9a10-000000000001"
TOKEN = "tok-SYNTHETIC-0123456789"  # placeholder sign-in token; must never reach a state or a log
NO_SIGN_IN = FetchResult(status=ApiStatus.NO_CREDENTIALS, detail="No Claude sign-in found",
                         snapshot=None, fresh=False)


# ── Synthetic data ───────────────────────────────────────────────────────────────

def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def request_line(message_id: str, moment: datetime, model: str = OPUS, *,
                 inp: int = 1000, out: int = 100) -> bytes:
    """One synthetic assistant record with usage: the kind of line that becomes a billable request."""
    rec = {
        "type": "assistant",
        "uuid": f"u-{message_id}",
        "sessionId": SESSION,
        "timestamp": iso(moment),
        "requestId": f"req_{message_id}",
        "cwd": "C:/work/demo",
        "gitBranch": "main",
        "entrypoint": "cli",
        "version": "2.1.0",
        "message": {
            "id": message_id,
            "model": model,
            "content": [{"type": "text", "text": "synthetic"}],
            "usage": {"input_tokens": inp, "output_tokens": out,
                      "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
        },
    }
    return (json.dumps(rec) + "\n").encode("utf-8")


def write_transcript(projects: Path, *lines: bytes) -> Path:
    folder = projects / "demo-app"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{SESSION}.jsonl"
    path.write_bytes(b"".join(lines))
    return path


def append_transcript(path: Path, *lines: bytes) -> None:
    with open(path, "ab") as handle:
        handle.write(b"".join(lines))


def write_sign_in(config_dir: Path, *, mtime: float | None = None) -> Path:
    """A synthetic sign-in file on a Max 5x plan. The token is a placeholder."""
    path = config_dir / ".credentials.json"
    expires_ms = int((NOW + DAY).timestamp() * 1000)
    data = {"claudeAiOauth": {"accessToken": TOKEN, "expiresAt": expires_ms,
                              "subscriptionType": "max", "rateLimitTier": "default_claude_max_5x"}}
    path.write_text(json.dumps(data), encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def live(moment: datetime, percent: float = 42.0) -> FetchResult:
    limit = Limit(kind="session", group="session", percent=percent, severity="normal",
                  resets_at=moment + timedelta(hours=3), is_active=True, label="SESSION",
                  window_seconds=18_000)
    snapshot = UsageSnapshot(fetched_at=moment, limits=[limit])
    return FetchResult(status=ApiStatus.LIVE, detail="Updated 12:00:00", snapshot=snapshot, fresh=True)


def doubled_doc(version: str) -> dict[str, Any]:
    """The bundled pricing document with every rate doubled and a newer version."""
    doc = json.loads(BUNDLED_PATH.read_text(encoding="utf-8-sig"))
    doc["version"] = version
    for row in doc["models"]:
        for key in ("input", "output", "cache_read"):
            row[key] = row[key] * 2
    return doc


# ── Fakes ────────────────────────────────────────────────────────────────────────

class Clock:
    """Fake clock: reads ``now`` until a test moves it."""

    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now = self.now + delta


class FakeClient:
    """Usage client stand-in. Gives the scripted outcomes in order (an exception is raised), then repeats the last."""

    def __init__(self, *outcomes: FetchResult | BaseException) -> None:
        self._outcomes = list(outcomes) or [NO_SIGN_IN]
        self.calls: list[datetime] = []
        self.next_allowed_at: datetime | None = None

    def fetch(self, now: datetime) -> FetchResult:
        self.calls.append(now)
        outcome = self._outcomes.pop(0) if len(self._outcomes) > 1 else self._outcomes[0]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeResponse:
    """Just enough of http.client.HTTPResponse for UsageClient: status, read() and close()."""

    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status
        self.headers: dict[str, str] = {}

    def read(self, amt: int | None = None) -> bytes:
        return self._body if amt is None else self._body[:amt]

    def close(self) -> None:
        return None


class FakeOpener:
    """Scripted urlopen stand-in: every request gets the same JSON body, and each request is recorded."""

    def __init__(self, body: dict[str, Any]) -> None:
        self._body = json.dumps(body).encode("utf-8")
        self.requests: list[Any] = []

    def __call__(self, request: Any, timeout: float | None = None) -> FakeResponse:
        self.requests.append(request)
        return FakeResponse(self._body)


class RecordingRefresh:
    """Stands in for the remote pricing download. Records each check; optionally writes the cache."""

    def __init__(self, changed: bool = False, write: Callable[[Path], None] | None = None) -> None:
        self.checks: list[Path] = []
        self._changed = changed
        self._write = write

    def __call__(self, cached: Path) -> bool:
        self.checks.append(cached)
        if self._write is not None:
            self._write(cached)
        return self._changed


def never_refresh(cached: Path) -> bool:
    """The default for every test: no remote check, so no network."""
    return False


# ── Fixtures and helpers ─────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_folders(tmp_path, monkeypatch) -> Path:
    """Keep app data and the Claude config folder inside tmp_path. Returns the config folder."""
    config = tmp_path / "claude"
    config.mkdir()
    monkeypatch.setenv("CUM_APPDATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    return config


@pytest.fixture
def projects(tmp_path) -> Path:
    root = tmp_path / "projects"
    root.mkdir()
    return root


@pytest.fixture
def settings(tmp_path):
    store = Settings(tmp_path / "settings.json", save_delay_s=60)
    # Long intervals: each test drives the cycles with refresh_now() or a settings change.
    store.update({"ingest_interval_s": 3600, "poll_interval_ms": 3_600_000})
    yield store
    store.close()


@pytest.fixture
def make(tmp_path, projects, settings):
    """Build a DataService wired into tmp_path. Services made here are stopped at teardown."""
    made: list[svc.DataService] = []

    def build(clock: Clock, **overrides: Any) -> svc.DataService:
        options: dict[str, Any] = {
            "store_path": tmp_path / "usage.db",
            "roots": [projects],
            "pricing": PRICING,
            "usage_client": FakeClient(),
            "pricing_refresh": never_refresh,
            "clock": clock,
        }
        options.update(overrides)
        service = svc.DataService(settings, **options)
        made.append(service)
        return service

    yield build
    for service in made:
        service.stop(timeout=5)


def watch(service: svc.DataService) -> queue.Queue:
    """Subscribe a queue to every state. Call before start() so the first state is not missed."""
    states: queue.Queue = queue.Queue()
    service.subscribe(states.put)
    return states


def next_state(states: queue.Queue, *, timeout: float = 5.0,
               want: Callable[[MonitorState], bool] | None = None) -> MonitorState:
    """The next published state that ``want`` accepts. By default a finished cycle, not a mid-ingest one."""
    accept = want if want is not None else (lambda state: not state.ingest.running)
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError("no matching MonitorState was published in time")
        try:
            state = states.get(timeout=remaining)
        except queue.Empty:
            raise AssertionError("no MonitorState was published in time") from None
        if accept(state):
            return state


def wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        time.sleep(0.01)


# ── Publishing and windows ───────────────────────────────────────────────────────

def test_first_publish_has_the_totals_windows_and_burn(make, projects):
    write_transcript(
        projects,
        request_line("m-opus-1", NOW - timedelta(minutes=10), OPUS, inp=1000, out=100),
        request_line("m-opus-2", NOW - 2 * DAY, OPUS, inp=2000, out=200),
        request_line("m-haiku-1", NOW - 3 * HOUR, HAIKU, inp=500, out=50),
        request_line("m-mystery-1", NOW - 40 * DAY, MYSTERY, inp=400, out=40),
    )
    service = make(Clock())
    states = watch(service)
    service.start()
    state = next_state(states)

    assert state.status is ApiStatus.NO_CREDENTIALS
    assert state.status_detail == NO_SIGN_IN.detail
    assert state.usage is None
    assert state.plan.name == "Unknown plan"
    assert state.ingest.files_total == 1
    assert state.ingest.running is False
    assert state.ingest.last_error is None

    everything = state.windows["all"]
    assert everything.totals.requests == 4
    assert everything.totals.input == 3900
    assert everything.totals.output == 390
    assert everything.totals.unpriced_requests == 1
    # Opus: 1000*4 + 100*20 + 2000*4 + 200*20 = 18,000; Haiku: 500*1 + 50*5 = 750; both per million.
    assert everything.totals.cost_usd == pytest.approx(0.01875)
    assert everything.start == NOW - 40 * DAY
    assert set(everything.by_model) == {"Opus 5.5", "Haiku 4.5", MYSTERY}
    assert everything.by_model["Opus 5.5"].requests == 2
    assert sum(model.requests for model in everything.by_model.values()) == everything.totals.requests

    week = state.windows["week"]
    assert week.totals.requests == 3          # the 40-day-old request is outside the last 7 days
    assert week.totals.input == 3500

    start, end = win.today(NOW)
    expected_today = [ts for ts in (NOW - timedelta(minutes=10), NOW - 3 * HOUR) if start <= ts < end]
    assert (state.windows["today"].start, state.windows["today"].end) == (start, end)
    assert state.windows["today"].totals.requests == len(expected_today)

    # No API session: the block comes from request history. The 2-day-old request sits behind a
    # gap of more than five hours, so the lookback has to reach it before the block is right.
    session = state.windows["session"]
    assert (session.start, session.end) == (NOW.replace(hour=9), NOW.replace(hour=14))
    assert session.totals.requests == 2

    assert state.burn.tokens_per_min == pytest.approx(100 / 30)
    assert state.burn.velocity == "SLOW"
    assert state.burn.usd_per_hour == pytest.approx(0.012)
    assert state.unpriced_models == [MYSTERY]
    assert set(state.windows) == {"session", "today", "week", "month", "all"}  # no billing day set


def test_billing_cycle_window_appears_only_when_a_billing_day_is_set(make, settings):
    service = make(Clock())
    states = watch(service)
    service.start()
    assert "cycle" not in next_state(states).windows
    settings.set("billing_day", 5)
    cycled = next_state(states, want=lambda s: "cycle" in s.windows)
    assert (cycled.windows["cycle"].start, cycled.windows["cycle"].end) == win.cycle(NOW, 5)


# ── Paused, sign-in and polling ──────────────────────────────────────────────────

def test_paused_publishes_paused_with_the_last_values_and_does_no_work(make, projects):
    path = write_transcript(projects, request_line("m1", NOW - timedelta(minutes=10)))
    paused = {"on": False}
    client = FakeClient(live(NOW))
    service = make(Clock(), usage_client=client, pause_check=lambda: paused["on"])
    states = watch(service)
    service.start()
    first = next_state(states)
    assert first.status is ApiStatus.LIVE
    assert first.windows["all"].totals.requests == 1
    assert len(client.calls) == 1

    paused["on"] = True
    append_transcript(path, request_line("m2", NOW - timedelta(minutes=5)))  # must not be read while paused
    service.refresh_now()
    held = next_state(states, want=lambda s: s.status is ApiStatus.PAUSED)
    assert held.status_detail == svc.PAUSED_DETAIL
    assert held.windows["all"].totals.requests == 1
    assert held.usage is first.usage
    assert len(client.calls) == 1

    paused["on"] = False
    service.refresh_now()
    resumed = next_state(states)
    assert resumed.status is ApiStatus.LIVE
    assert resumed.windows["all"].totals.requests == 2
    assert len(client.calls) == 2


def test_a_changed_sign_in_file_fetches_at_once_before_the_poll_interval(
        make, settings, isolated_folders, caplog):
    caplog.set_level(logging.DEBUG)
    sign_in = write_sign_in(isolated_folders, mtime=1_800_000_000.0)
    client = FakeClient(live(NOW))
    service = make(Clock(), usage_client=client)
    states = watch(service)
    service.start()
    first = next_state(states)
    assert len(client.calls) == 1
    assert (first.plan.name, first.plan.monthly_price_usd) == ("Max 5x", 100.0)
    assert TOKEN not in repr(first)
    assert TOKEN not in caplog.text

    settings.set("billing_day", 5)            # wakes the loop; the poll interval (1 h) has not passed
    next_state(states)
    assert len(client.calls) == 1             # sign-in file unchanged: no fetch

    os.utime(sign_in, (1_800_000_500.0, 1_800_000_500.0))   # Claude Code rewrote the file
    settings.set("billing_day", 6)
    next_state(states)
    assert len(client.calls) == 2


def test_the_usage_fetch_waits_for_the_poll_interval_on_the_injected_clock(make, settings):
    clock = Clock()
    client = FakeClient(live(NOW))
    service = make(clock, usage_client=client)
    states = watch(service)
    service.start()
    next_state(states)
    assert len(client.calls) == 1

    clock.advance(timedelta(minutes=59))
    settings.set("billing_day", 5)
    next_state(states)
    assert len(client.calls) == 1

    clock.advance(timedelta(minutes=1))       # one hour after the fetch
    settings.set("billing_day", 6)
    next_state(states)
    assert len(client.calls) == 2
    assert client.calls[-1] == NOW + HOUR


def test_ingest_waits_for_its_interval_then_picks_up_new_transcripts(make, settings, projects):
    settings.update({"ingest_interval_s": 60})
    clock = Clock()
    service = make(clock)
    states = watch(service)
    service.start()
    next_state(states)                        # first cycle: no transcripts yet
    write_transcript(projects, request_line("m1", NOW - timedelta(minutes=10)))

    settings.set("billing_day", 5)            # wakes the loop, but ingest is not due for another 60 s
    held = next_state(states)
    assert held.windows["all"].totals.requests == 0

    clock.advance(timedelta(seconds=60))
    settings.set("billing_day", 6)
    picked = next_state(states)
    assert picked.windows["all"].totals.requests == 1
    assert picked.ingest.files_total == 1


def test_usage_api_limits_set_the_session_and_week_windows_and_are_saved(
        make, isolated_folders, tmp_path):
    write_sign_in(isolated_folders)
    session_end = NOW + 3 * HOUR
    week_end = NOW + 2 * DAY
    opener = FakeOpener({"limits": [
        {"kind": "session", "group": "session", "percent": 42.0, "severity": "normal",
         "resets_at": iso(session_end), "is_active": True},
        {"kind": "weekly_all", "group": "weekly", "percent": 10.0, "severity": "normal",
         "resets_at": iso(week_end), "is_active": True},
    ]})
    service = make(Clock(), usage_client=UsageClient(opener=opener, version="test"))
    states = watch(service)
    service.start()
    state = next_state(states)

    assert state.status is ApiStatus.LIVE
    assert state.usage.session.percent == 42.0
    assert state.usage_fetched_at == NOW
    assert (state.windows["session"].start, state.windows["session"].end) == (session_end - 5 * HOUR, session_end)
    assert (state.windows["week"].start, state.windows["week"].end) == (week_end - 7 * DAY, week_end)
    assert state.plan.name == "Max 5x"
    assert len(opener.requests) == 1

    service.stop()
    reader = Store(tmp_path / "usage.db")
    try:
        assert len(reader.usage_snapshots(NOW - DAY)) == 1
    finally:
        reader.close()


def test_unsubscribe_removes_the_callback(make):
    service = make(Clock())
    kept = watch(service)
    dropped: list[MonitorState] = []
    off = service.subscribe(dropped.append)
    off()
    service.start()
    next_state(kept)
    assert dropped == []


# ── Loop behaviour: wake-ups, isolation, failures, shutdown ──────────────────────

def test_a_settings_change_wakes_the_loop_but_an_unrelated_one_does_not(make, settings):
    service = make(Clock())
    states = watch(service)
    service.start()
    next_state(states)
    settings.set("billing_day", 5)            # a wake key: a new cycle publishes the cycle window
    next_state(states, want=lambda s: "cycle" in s.windows)
    settings.set("theme", "Night City")       # not a wake key: nothing is published
    with pytest.raises(AssertionError):
        next_state(states, timeout=0.5)


def test_a_raising_subscriber_does_not_stop_the_others_or_the_loop(make, caplog):
    seen: list[MonitorState] = []

    def broken(state: MonitorState) -> None:
        seen.append(state)
        raise RuntimeError("subscriber bug")

    service = make(Clock())
    service.subscribe(broken)
    states = watch(service)
    service.start()
    next_state(states)
    service.refresh_now()
    second = next_state(states)
    assert len(seen) >= 2                     # the broken subscriber was called on every publish
    assert second.status is ApiStatus.NO_CREDENTIALS
    assert "raised" in caplog.text


def test_an_exception_inside_a_cycle_is_recorded_and_the_loop_carries_on(make):
    client = FakeClient(RuntimeError("usage client bug"), live(NOW))
    service = make(Clock(), usage_client=client)
    states = watch(service)
    service.start()
    failed = next_state(states)
    assert failed.ingest.last_error == "RuntimeError: usage client bug"
    assert failed.status is ApiStatus.LOADING     # no fetch has succeeded yet

    service.refresh_now()
    recovered = next_state(states)
    assert recovered.ingest.last_error is None
    assert recovered.status is ApiStatus.LIVE


def test_stop_joins_the_thread_closes_the_store_once_and_is_idempotent(make, monkeypatch):
    closed: list[str] = []
    real_close = Store.close

    def counting_close(self: Store) -> None:
        closed.append("closed")
        real_close(self)

    monkeypatch.setattr(Store, "close", counting_close)
    service = make(Clock())
    states = watch(service)
    service.start()
    next_state(states)
    service.stop(timeout=5)
    assert not any(thread.name == svc.THREAD_NAME for thread in threading.enumerate())
    assert closed == ["closed"]
    service.stop(timeout=5)
    assert closed == ["closed"]


def test_stop_returns_promptly_while_paused(make):
    service = make(Clock(), pause_check=lambda: True)
    states = watch(service)
    service.start()
    next_state(states, want=lambda s: s.status is ApiStatus.PAUSED)
    started = time.monotonic()
    service.stop(timeout=5)
    assert time.monotonic() - started < 2.0


# ── Store, pricing and plan ──────────────────────────────────────────────────────

def test_the_store_is_repriced_on_start_when_the_table_version_differs(make, projects, tmp_path):
    write_transcript(projects, request_line("m1", NOW - timedelta(minutes=10), OPUS, inp=1000, out=100))
    store_file = tmp_path / "usage.db"
    seed = Store(store_file)
    Ingestor(seed, PRICING, [projects]).run()   # priced under the bundled version
    seed.close()

    service = make(Clock(), pricing=Pricing.from_dict(doubled_doc("2099-01-01")), store_path=store_file)
    states = watch(service)
    service.start()
    state = next_state(states)
    assert state.windows["all"].totals.cost_usd == pytest.approx(0.012)   # 0.006 at the old rates
    service.stop()

    check = Store(store_file)
    try:
        assert check.pricing_version() == "2099-01-01"
        assert check.totals(None, None).cost_usd == pytest.approx(0.012)
    finally:
        check.close()


def test_remote_pricing_is_checked_after_the_first_cycle_and_at_most_once_a_day(make):
    clock = Clock()
    refresh = RecordingRefresh()
    service = make(clock, pricing_refresh=refresh)
    states = watch(service)
    service.start()
    next_state(states)
    service.refresh_now()
    next_state(states)
    assert len(refresh.checks) == 1           # checked once, after the first publish

    clock.advance(timedelta(hours=23))
    service.refresh_now()
    next_state(states)
    assert len(refresh.checks) == 1

    clock.advance(timedelta(hours=2))         # 25 h after the check
    service.refresh_now()
    next_state(states)
    wait_until(lambda: len(refresh.checks) == 2)


def test_a_newer_remote_table_is_loaded_and_the_store_repriced(make, projects, tmp_path):
    write_transcript(projects, request_line("m1", NOW - timedelta(minutes=10), OPUS, inp=1000, out=100))

    def publish_newer(cached: Path) -> None:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(doubled_doc("2099-01-01")), encoding="utf-8")

    refresh = RecordingRefresh(changed=True, write=publish_newer)
    service = make(Clock(), pricing=None, pricing_refresh=refresh)
    states = watch(service)
    service.start()
    first = next_state(states)
    assert first.windows["all"].totals.cost_usd == pytest.approx(0.006)   # bundled rates
    service.refresh_now()
    second = next_state(states)
    assert second.windows["all"].totals.cost_usd == pytest.approx(0.012)  # newer table, store repriced
    assert len(refresh.checks) == 1
    service.stop()

    check = Store(tmp_path / "usage.db")
    try:
        assert check.pricing_version() == "2099-01-01"
    finally:
        check.close()


def test_the_plan_comes_from_the_sign_in_and_the_price_override_wins(make, settings, isolated_folders):
    write_sign_in(isolated_folders)
    service = make(Clock())
    states = watch(service)
    service.start()
    first = next_state(states)
    assert (first.plan.name, first.plan.monthly_price_usd) == ("Max 5x", 100.0)

    settings.set("plan_price_override", 85)   # a wake key: the next cycle shows the override
    overridden = next_state(states)
    assert (overridden.plan.name, overridden.plan.monthly_price_usd) == ("Max 5x", 85.0)

    settings.set("plan_price_override", None)
    restored = next_state(states)
    assert restored.plan.monthly_price_usd == 100.0


# ── Regression: retries, sign-in changes, paused start, window isolation, restarts ──────

class BackingOffClient:
    """Fails its first fetch as a network error would, and sets its backoff gate the way UsageClient does."""

    def __init__(self, first: FetchResult, then: FetchResult, wait: timedelta) -> None:
        self._first = first
        self._then = then
        self._wait = wait
        self.next_allowed_at: datetime | None = None
        self.calls: list[datetime] = []

    def fetch(self, now: datetime) -> FetchResult:
        self.calls.append(now)
        if len(self.calls) == 1:
            self.next_allowed_at = now + self._wait
            return self._first
        self.next_allowed_at = None
        return self._then


class BlockingClient:
    """Holds its first fetch until the test releases it, so a stop can time out while a cycle runs."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.next_allowed_at: datetime | None = None

    def fetch(self, now: datetime) -> FetchResult:
        self.entered.set()
        self.release.wait(5)
        return NO_SIGN_IN


def test_every_published_state_has_the_documented_windows_including_the_first(make, projects):
    write_transcript(projects, request_line("m1", NOW - timedelta(minutes=10)))
    service = make(Clock())
    states = watch(service)
    service.start()
    published = []
    while True:
        state = next_state(states, want=lambda s: True)
        published.append(state)
        if not state.ingest.running:
            break
    assert any(state.ingest.running for state in published)
    for state in published:
        assert {"session", "today", "week", "month", "all"} <= set(state.windows)


def test_a_paused_start_reports_the_plan_from_the_sign_in(make, isolated_folders):
    write_sign_in(isolated_folders, mtime=1_800_000_000.0)
    service = make(Clock(), pause_check=lambda: True)
    states = watch(service)
    service.start()
    paused = next_state(states, want=lambda s: s.status is ApiStatus.PAUSED)
    assert (paused.plan.name, paused.plan.monthly_price_usd) == ("Max 5x", 100.0)


def test_a_failed_usage_fetch_is_retried_when_its_backoff_ends_not_at_the_next_poll(make):
    offline = FetchResult(status=ApiStatus.OFFLINE, detail="Usage API unreachable (network error)",
                          snapshot=None, fresh=False)
    client = BackingOffClient(offline, live(NOW), wait=timedelta(seconds=30))
    clock = Clock()
    service = make(clock, usage_client=client)  # the settings fixture polls once an hour
    service._cycle()
    assert service.state.status is ApiStatus.OFFLINE
    clock.advance(timedelta(seconds=30))
    service._cycle()
    assert len(client.calls) == 2
    assert service.state.status is ApiStatus.LIVE


def test_a_sign_in_written_after_a_start_without_one_fetches_at_once(make, isolated_folders):
    (isolated_folders / ".credentials.json").write_text(json.dumps({"mcpOAuth": {}}), encoding="utf-8")
    client = FakeClient(NO_SIGN_IN, live(NOW))
    service = make(Clock(), usage_client=client)
    service._cycle()
    assert len(client.calls) == 1
    write_sign_in(isolated_folders, mtime=1_800_000_500.0)
    service._cycle()
    assert len(client.calls) == 2
    assert service.state.status is ApiStatus.LIVE


def test_signing_out_with_the_file_kept_resets_the_plan_to_unknown(make, isolated_folders):
    path = write_sign_in(isolated_folders, mtime=1_800_000_000.0)
    service = make(Clock(), usage_client=FakeClient(NO_SIGN_IN))
    service._cycle()
    assert service.state.plan.name == "Max 5x"
    path.write_text(json.dumps({"mcpOAuth": {}}), encoding="utf-8")
    os.utime(path, (1_800_000_500.0, 1_800_000_500.0))
    service._cycle()
    assert service.state.plan.name == "Unknown plan"


def test_a_half_written_sign_in_keeps_the_plan_and_does_not_force_a_fetch(make, isolated_folders):
    path = write_sign_in(isolated_folders, mtime=1_800_000_000.0)
    client = FakeClient(live(NOW))
    service = make(Clock(), usage_client=client)
    service._cycle()
    assert len(client.calls) == 1
    path.write_text('{"claudeAiOauth": {"accessT', encoding="utf-8")  # Claude Code is mid-write
    os.utime(path, (1_800_000_500.0, 1_800_000_500.0))
    service._cycle()
    assert service.state.plan.name == "Max 5x"
    assert len(client.calls) == 1


def test_one_window_that_cannot_be_computed_does_not_stop_the_others(make, projects, monkeypatch):
    write_transcript(projects, request_line("m1", NOW - timedelta(minutes=10)))
    real_window = svc._window

    def failing_week(store, pricing, name, bounds):
        if name == "week":
            raise OSError("disk I/O error")
        return real_window(store, pricing, name, bounds)

    monkeypatch.setattr(svc, "_window", failing_week)
    service = make(Clock())
    service._cycle()
    assert service.state.windows["today"].totals.requests == 1
    assert service.state.windows["all"].totals.requests == 1


def test_the_wait_between_cycles_stays_within_a_day_whatever_the_setting(make, monkeypatch):
    service = make(Clock())
    absurd = {"ingest_interval_s": 1e308, "poll_interval_ms": 1e308}
    monkeypatch.setattr(service._settings, "get", lambda key, default=None: absurd.get(key, default))
    assert service._ingest_interval() <= timedelta(days=1)
    assert service._poll_interval() <= timedelta(days=1)


def test_start_after_a_stop_that_timed_out_while_busy_keeps_the_loop_running(make):
    client = BlockingClient()
    service = make(Clock(), usage_client=client)
    states = watch(service)
    service.start()
    assert client.entered.wait(5)
    service.stop(timeout=0.01)     # the cycle is still running, so the stop returns with the thread busy
    service.start()                # the stop has not taken effect yet; the loop must keep going
    client.release.set()
    next_state(states)             # the cycle that was running finishes and publishes
    service.refresh_now()
    next_state(states)             # and a later cycle publishes too: the loop kept going
    service.stop(timeout=5)


def test_start_after_a_stop_requested_by_a_subscriber_starts_a_new_loop(make):
    service = make(Clock())

    def stop_on_first(_state: MonitorState) -> None:
        service.stop(timeout=5)

    service.subscribe(stop_on_first)
    service.start()
    old = service._thread
    assert old is not None
    old.join(5)
    assert not old.is_alive()
    states = watch(service)
    service.start()
    service.refresh_now()
    next_state(states)
    service.stop(timeout=5)


def test_stop_closes_a_store_opened_by_a_cycle_without_a_thread(make):
    service = make(Clock())
    service._cycle()
    assert service._store is not None
    service.stop(timeout=1)
    assert service._store is None
