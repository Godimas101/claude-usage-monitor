"""Offline tests for usage_monitor.data.usage_api.

Network calls go through a fake opener, clock values are passed in explicitly, and every
credentials path is a temp file. Nothing here touches the network, ~/.claude or the wall clock.
"""
from __future__ import annotations

import dataclasses
import io
import json
import logging
import os
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from usage_monitor.data import usage_api as ua
from usage_monitor.data.models import (
    ApiStatus,
    BreakdownRow,
    CredentialsInfo,
    DollarWindow,
    Spend,
    UsageSnapshot,
)

UTC = timezone.utc
FIXTURES = Path(__file__).parent / "fixtures" / "usage_api"
T0 = datetime(2026, 10, 21, 12, 0, 0, tzinfo=UTC)
BASE_MTIME = 1_800_000_000.0  # fixed mtime for credentials files written by the tests


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def secs(n: float) -> timedelta:
    return timedelta(seconds=n)


def ms_at(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


CURRENT = load("current_2026_10_21.json")
CREDS = load("credentials_max_5x.json")
TOKEN = CREDS["claudeAiOauth"]["accessToken"]
EXPIRES_MS = CREDS["claudeAiOauth"]["expiresAt"]
EXPIRES_AT = datetime(2026, 10, 22, 12, 0, 0, tzinfo=UTC)


# ── Helpers: credentials files, fake opener, fake responses ──────────────────

def write_credentials(path: Path, *, expires_ms: int = EXPIRES_MS, token: str | None = TOKEN,
                      mtime: float = BASE_MTIME) -> Path:
    data = json.loads(json.dumps(CREDS))
    data["claudeAiOauth"]["expiresAt"] = expires_ms
    if token is None:
        del data["claudeAiOauth"]["accessToken"]
    else:
        data["claudeAiOauth"]["accessToken"] = token
    path.write_text(json.dumps(data), encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


class FakeResponse:
    """Stand-in for http.client.HTTPResponse: status, headers, read() and close()."""

    def __init__(self, body: bytes | str, status: int = 200, headers: dict | None = None) -> None:
        self._body = body.encode("utf-8") if isinstance(body, str) else body
        self.status = status
        self.headers = headers or {}
        self.closed = False

    def read(self, amt: int | None = None) -> bytes:
        return self._body if amt is None else self._body[:amt]

    def close(self) -> None:
        self.closed = True


class FakeOpener:
    """Scripted opener: each call returns or raises the next outcome and records the request."""

    def __init__(self, *outcomes: Any) -> None:
        self._outcomes = list(outcomes)
        self.requests: list[urllib.request.Request] = []
        self.timeouts: list[Any] = []

    def __call__(self, request: urllib.request.Request, timeout: Any = None) -> Any:
        self.requests.append(request)
        self.timeouts.append(timeout)
        if not self._outcomes:  # unscripted call: fail visibly rather than guess
            return FakeResponse(b"", status=500)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    @property
    def calls(self) -> int:
        return len(self.requests)


def ok(raw: dict | None = None) -> FakeResponse:
    return FakeResponse(json.dumps(CURRENT if raw is None else raw))


def http_error(code: int, headers: dict | None = None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(ua.USAGE_URL, code, f"HTTP {code}", headers or {}, io.BytesIO(b""))


def make_client(path: Path, *outcomes: Any, **kwargs: Any) -> tuple[ua.UsageClient, FakeOpener]:
    opener = FakeOpener(*outcomes)
    return ua.UsageClient(path, opener=opener, **kwargs), opener


def walk_backoff(client: ua.UsageClient, opener: FakeOpener, start: datetime,
                 waits: list[int], status: ApiStatus) -> None:
    """Each failure must wait exactly the expected seconds; one second early there is no request."""
    now = start
    for step, wait in enumerate(waits, start=1):
        result = client.fetch(now)
        assert result.status is status and result.fresh is False
        assert client.next_allowed_at - now == secs(wait)
        assert opener.calls == step
        early = client.fetch(now + secs(wait - 1))
        assert early.status is status and early.fresh is False
        assert "next try at" in early.detail
        assert opener.calls == step
        now = client.next_allowed_at


# ── Parser: current capture ───────────────────────────────────────────────────

def test_current_fixture_maps_limits_in_api_order():
    snap = ua.parse_usage(CURRENT, T0)
    assert [limit.label for limit in snap.limits] == ["SESSION", "WEEKLY", "FABLE WEEKLY"]
    session, weekly, fable = snap.limits
    assert session.kind == "session" and session.group == "session"
    assert session.percent == 13.0 and session.severity == "normal" and session.is_active is True
    assert session.resets_at == datetime(2026, 10, 22, 2, 40, tzinfo=UTC)  # 02:39:59.903338, rounded
    assert session.window_seconds == 18_000
    assert weekly.kind == "weekly_all" and weekly.percent == 2.0 and weekly.is_active is False
    assert weekly.window_seconds == 604_800
    assert fable.kind == "weekly_scoped" and fable.group == "weekly" and fable.percent == 0.0
    assert fable.scope_model == "Fable" and fable.scope_surface is None
    assert fable.resets_at == datetime(2026, 10, 28, 20, 0, 0, tzinfo=UTC)
    assert snap.session is session and snap.weekly is weekly
    assert snap.scoped == [fable]


def test_current_fixture_attaches_scoped_share_by_limit_index():
    snap = ua.parse_usage(CURRENT, T0)
    fable = snap.scoped[0]
    assert fable.allowance_pct == 50 and fable.used_pct_of_weekly == 0
    assert snap.session.allowance_pct is None and snap.weekly.used_pct_of_weekly is None


def test_current_fixture_ignores_all_null_codename_windows():
    snap = ua.parse_usage(CURRENT, T0)
    assert len(snap.limits) == 3
    assert {limit.kind for limit in snap.limits} == {"session", "weekly_all", "weekly_scoped"}


def test_current_fixture_breakdown_rows_and_window_start():
    snap = ua.parse_usage(CURRENT, T0)
    assert snap.breakdown == [
        BreakdownRow(key="claude_code", name="Claude Code", percent=100.0),
        BreakdownRow(key="chat", name="Chats", percent=0.0),
        BreakdownRow(key="cowork", name="Cowork", percent=0.0),
        BreakdownRow(key="other", name="Other", percent=0.0),
    ]
    assert snap.breakdown_window_start == datetime(2026, 10, 21, 19, 59, 59, 903359, tzinfo=UTC)


def test_current_fixture_spend_is_disabled_and_zero():
    snap = ua.parse_usage(CURRENT, T0)
    assert snap.spend == Spend(enabled=False, used=0.0, limit=None, percent=0.0, severity="normal",
                               currency="USD", balance=None, disabled_reason=None)


def test_snapshot_keeps_fetched_at_and_the_raw_response():
    snap = ua.parse_usage(CURRENT, T0)
    assert snap.fetched_at == T0 and snap.raw == CURRENT


def test_naive_fetched_at_is_taken_as_utc():
    snap = ua.parse_usage(CURRENT, datetime(2026, 10, 21, 12, 0))
    assert snap.fetched_at == T0 and snap.fetched_at.tzinfo is not None


# ── Parser: legacy shape, future shape, credits, garbage ─────────────────────

def test_legacy_shape_maps_to_session_weekly_and_opus_scoped():
    snap = ua.parse_usage(load("legacy_shape.json"), T0)
    assert [limit.label for limit in snap.limits] == ["SESSION", "WEEKLY", "OPUS WEEKLY"]
    session, weekly, opus = snap.limits
    assert (session.kind, session.group, session.percent, session.window_seconds) == (
        "session", "session", 41.0, 18_000)
    assert session.resets_at == datetime(2026, 10, 22, 2, 40, tzinfo=UTC)  # "...59.903Z", rounded
    assert (weekly.kind, weekly.percent, weekly.window_seconds) == ("weekly_all", 17.5, 604_800)
    assert (opus.kind, opus.group, opus.scope_model, opus.percent) == (
        "weekly_scoped", "weekly", "Opus", 9.0)
    assert snap.session is session and snap.weekly is weekly
    assert snap.spend is None and snap.breakdown == [] and snap.breakdown_window_start is None


def test_legacy_window_maps_locked_reason_and_dollars():
    raw = {"seven_day": {"utilization": 100, "resets_at": "2026-10-28T19:59:59Z",
                         "limit_dollars": 100, "used_dollars": 80, "remaining_dollars": 20,
                         "locked_reason": "weekly_cap_reached"}}
    weekly = ua.parse_usage(raw, T0).weekly
    assert weekly.locked_reason == "weekly_cap_reached"
    assert weekly.dollars == DollarWindow(limit=100.0, used=80.0, remaining=20.0)


def test_legacy_null_codenames_are_ignored_and_non_null_ones_kept():
    raw = {
        "five_hour": {"utilization": 5},
        "tangelo": None,
        "iguana_necktie": {"utilization": None},
        "amber_gauge": {"utilization": 7.5},
        "extra_usage": {"utilization": 42, "is_enabled": True},  # reserved: not a window
    }
    snap = ua.parse_usage(raw, T0)
    assert [limit.label for limit in snap.limits] == ["SESSION", "AMBER GAUGE"]
    assert snap.limits[1].kind == "amber_gauge" and snap.limits[1].percent == 7.5


def test_empty_limits_list_falls_back_to_legacy_windows():
    snap = ua.parse_usage({"limits": [], "five_hour": {"utilization": 5}}, T0)
    assert [limit.label for limit in snap.limits] == ["SESSION"]


def test_future_shape_keeps_extra_kind_warning_scope_and_locked_reason():
    snap = ua.parse_usage(load("future_shape.json"), T0)
    assert [limit.label for limit in snap.limits] == ["SESSION", "WEEKLY", "OPUS 9 WEEKLY", "MONTHLY ALL"]
    weekly, opus9, monthly = snap.limits[1], snap.limits[2], snap.limits[3]
    assert opus9.severity == "warning" and opus9.percent == 101.2 and opus9.scope_model == "Opus 9"
    assert (opus9.allowance_pct, opus9.used_pct_of_weekly) == (50, 60)
    assert weekly.locked_reason == "weekly_cap_reached" and weekly.severity == "warning"
    assert weekly.allowance_pct is None
    assert monthly.kind == "monthly_all" and monthly.group == "monthly" and monthly.window_seconds is None
    assert snap.scoped == [opus9, monthly]


def test_credits_enabled_converts_minor_units_and_keeps_percent():
    spend = ua.parse_usage(load("credits_enabled.json"), T0).spend
    assert spend.enabled is True
    assert spend.used == pytest.approx(12.34)
    assert spend.limit == pytest.approx(50.0)
    assert spend.balance == pytest.approx(37.66)
    assert spend.percent == 24.68
    assert spend.currency == "USD" and spend.severity == "normal" and spend.disabled_reason is None


def test_garbage_response_is_best_effort_and_never_raises():
    snap = ua.parse_usage(load("garbage.json"), T0)
    assert isinstance(snap, UsageSnapshot)
    assert [limit.label for limit in snap.limits] == ["OPUS WEEKLY"]  # string "12.5" accepted
    assert snap.limits[0].percent == 12.5
    assert snap.limits[0].resets_at == datetime(2026, 10, 21, 12, 0, tzinfo=UTC)  # epoch seconds
    assert snap.breakdown == [BreakdownRow(key="chat", name="Chats", percent=7.0)]
    assert snap.breakdown_window_start is None
    assert snap.spend is not None and snap.spend.enabled is False
    assert snap.spend.used is None and snap.spend.limit is None and snap.spend.percent is None
    assert snap.spend.currency == "USD"


@pytest.mark.parametrize("raw", [
    None,
    [],
    "text",
    3,
    {},
    {"limits": None},
    {"limits": [None, 5, "x", {"kind": 1, "percent": 2}, {"kind": "session", "percent": True}]},
    {"spend": []},
    {"seven_day_breakdown": "str"},
    {"five_hour": {"utilization": True}},
])
def test_parse_usage_never_raises_on_unusable_input(raw):
    snap = ua.parse_usage(raw, T0)
    assert isinstance(snap, UsageSnapshot)
    assert snap.limits == [] and snap.breakdown == [] and snap.spend is None


@pytest.mark.parametrize("value, expected", [
    ("2026-10-22T02:39:59Z", datetime(2026, 10, 22, 2, 39, 59, tzinfo=UTC)),
    ("2026-10-22T02:39:59.5+00:00", datetime(2026, 10, 22, 2, 39, 59, 500000, tzinfo=UTC)),
    ("2026-10-22T02:39:59.123456789Z", datetime(2026, 10, 22, 2, 39, 59, 123456, tzinfo=UTC)),
    ("2026-10-22T04:39:59+02:00", datetime(2026, 10, 22, 2, 39, 59, tzinfo=UTC)),
    ("2026-10-22T02:39:59", datetime(2026, 10, 22, 2, 39, 59, tzinfo=UTC)),
    (1792584000, datetime(2026, 10, 21, 12, 0, tzinfo=UTC)),
    ("not a date", None),
    (True, None),
    (None, None),
])
def test_timestamp_variants(value, expected):
    assert ua._parse_ts(value) == expected


# ── Credentials ───────────────────────────────────────────────────────────────

def test_read_credentials_returns_token_expiry_and_plan(tmp_path):
    path = write_credentials(tmp_path / ".credentials.json")
    info, token = ua.read_credentials(path)
    assert token == TOKEN
    assert info.present is True and info.expires_at == EXPIRES_AT and info.mtime == BASE_MTIME
    assert info.plan.name == "Max 5x" and info.plan.monthly_price_usd == 100.0
    assert info.is_expired(T0) is False and info.is_expired(EXPIRES_AT) is True


def test_read_credentials_missing_file_is_not_present(tmp_path):
    info, token = ua.read_credentials(tmp_path / "absent.json")
    assert token is None and info.present is False
    assert info.mtime is None and info.expires_at is None
    assert info.plan.name == "Unknown plan"


@pytest.mark.parametrize("text", ['{"claudeAiOauth": {"accessToken": ', "not json SECRET-IN-GARBAGE"])
def test_read_credentials_unreadable_file_is_absent_and_not_logged(tmp_path, caplog, text):
    caplog.set_level(logging.DEBUG)
    path = tmp_path / ".credentials.json"
    path.write_text(text, encoding="utf-8")
    info, token = ua.read_credentials(path)
    assert info.present is False and token is None and info.mtime is not None
    assert "SECRET" not in caplog.text


def test_read_credentials_without_sign_in_block_is_not_present(tmp_path):
    path = tmp_path / ".credentials.json"
    path.write_text(json.dumps({"mcpOAuth": {"x": {"accessToken": "other"}}}), encoding="utf-8")
    info, token = ua.read_credentials(path)
    assert info.present is False and token is None and info.mtime is not None


def test_read_credentials_block_without_token_is_present_but_tokenless(tmp_path):
    info, token = ua.read_credentials(write_credentials(tmp_path / ".credentials.json", token=None))
    assert info.present is True and token is None


@pytest.mark.parametrize("expires", ["soon", True, 1e30, None])
def test_read_credentials_bad_expiry_is_unknown_not_expired(tmp_path, expires):
    data = json.loads(json.dumps(CREDS))
    data["claudeAiOauth"]["expiresAt"] = expires
    path = tmp_path / ".credentials.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    info, token = ua.read_credentials(path)
    assert info.expires_at is None and info.is_expired(T0) is False and token == TOKEN


def test_credentials_info_never_carries_the_token(tmp_path):
    info, _ = ua.read_credentials(write_credentials(tmp_path / ".credentials.json"))
    assert {f.name for f in dataclasses.fields(CredentialsInfo)} == {"present", "expires_at", "mtime", "plan"}
    assert TOKEN not in repr(info) and TOKEN not in str(info)


@pytest.mark.parametrize("sub, tier, name, price", [
    ("max", "default_claude_max_5x", "Max 5x", 100.0),
    ("max", "default_claude_max_20x", "Max 20x", 200.0),
    ("max", "default_claude_max", "Max", None),
    ("max", None, "Max", None),
    ("Max", "DEFAULT_CLAUDE_MAX_5X", "Max 5x", 100.0),
    ("pro", None, "Pro", 20.0),
    ("pro", "default_claude_max_5x", "Pro", 20.0),
    ("team", "default_claude_team_standard", "Team", None),
    ("enterprise", None, "Enterprise", None),
    (None, None, "Unknown plan", None),
    (None, "default_claude_max_5x", "Unknown plan", None),
])
def test_plan_from_names_and_list_prices(sub, tier, name, price):
    plan = ua.plan_from(sub, tier)
    assert plan.name == name and plan.monthly_price_usd == price


# ── Client: request shape and the status machine ─────────────────────────────

def test_initial_status_is_loading(tmp_path):
    client, opener = make_client(tmp_path / "c.json")
    assert client.status is ApiStatus.LOADING and client.last_snapshot is None
    assert opener.calls == 0


def test_200_is_live_with_bearer_beta_and_user_agent_headers(tmp_path):
    path = write_credentials(tmp_path / ".credentials.json")
    client, opener = make_client(path, ok(), version="1.1.5")
    result = client.fetch(T0)
    assert opener.calls == 1
    request = opener.requests[0]
    assert request.full_url == ua.USAGE_URL == "https://api.anthropic.com/api/oauth/usage"
    assert request.get_header("Authorization") == f"Bearer {TOKEN}"
    assert request.get_header("Anthropic-beta") == "oauth-2025-04-20"
    assert request.get_header("User-agent") == "ClaudeUsageMonitor/1.1.5"
    assert opener.timeouts == [10]
    assert result.status is ApiStatus.LIVE and result.fresh is True
    assert result.snapshot is client.last_snapshot
    assert client.last_snapshot.session.percent == 13.0
    assert client.last_ok_at == T0 and client.next_allowed_at is None
    assert result.detail == f"Updated {T0.astimezone().strftime('%H:%M:%S')}"


def test_default_user_agent_is_dev_and_override_is_used_verbatim(tmp_path):
    path = write_credentials(tmp_path / ".credentials.json")
    client, opener = make_client(path, ok(), ok())
    client.fetch(T0)
    assert opener.requests[0].get_header("User-agent") == "ClaudeUsageMonitor/dev"
    other, other_opener = make_client(path, ok(), user_agent="Custom/9")
    other.fetch(T0)
    assert other_opener.requests[0].get_header("User-agent") == "Custom/9"


def test_naive_now_is_accepted(tmp_path):
    client, _ = make_client(write_credentials(tmp_path / ".credentials.json"), ok())
    assert client.fetch(datetime(2026, 10, 21, 12, 0)).status is ApiStatus.LIVE


def test_expired_token_is_stale_without_any_request(tmp_path):
    path = write_credentials(tmp_path / ".credentials.json", expires_ms=ms_at(T0 - secs(1)))
    client, opener = make_client(path, ok())
    first = client.fetch(T0)
    later = client.fetch(T0 + secs(3600))
    assert first.status is ApiStatus.STALE and later.status is ApiStatus.STALE
    assert first.fresh is False and opener.calls == 0
    assert "expired" in first.detail


def test_token_is_expired_at_its_expiry_instant_not_a_second_later(tmp_path):
    at_expiry, opener_a = make_client(write_credentials(tmp_path / "a.json", expires_ms=ms_at(T0)), ok())
    assert at_expiry.fetch(T0).status is ApiStatus.STALE and opener_a.calls == 0
    just_valid, opener_b = make_client(
        write_credentials(tmp_path / "b.json", expires_ms=ms_at(T0 + secs(1))), ok())
    assert just_valid.fetch(T0).status is ApiStatus.LIVE and opener_b.calls == 1


@pytest.mark.parametrize("code", [401, 403])
def test_401_and_403_are_stale_until_the_credentials_file_changes(tmp_path, code):
    path = write_credentials(tmp_path / ".credentials.json")
    client, opener = make_client(path, http_error(code), ok())
    assert client.fetch(T0).status is ApiStatus.STALE
    assert opener.calls == 1
    assert client.fetch(T0 + secs(3600)).status is ApiStatus.STALE  # no timer-based retry
    assert opener.calls == 1
    write_credentials(path, mtime=BASE_MTIME + 120)  # Claude Code signed in again
    result = client.fetch(T0 + secs(3601))
    assert opener.calls == 2 and result.status is ApiStatus.LIVE and result.fresh is True


def test_expired_token_recovers_once_the_file_is_refreshed(tmp_path):
    path = write_credentials(tmp_path / ".credentials.json", expires_ms=ms_at(T0 - secs(1)))
    client, opener = make_client(path, ok())
    assert client.fetch(T0).status is ApiStatus.STALE
    write_credentials(path, mtime=BASE_MTIME + 120)  # a fresh token with a new mtime
    result = client.fetch(T0 + secs(10))
    assert result.status is ApiStatus.LIVE and opener.calls == 1


def test_missing_credentials_are_no_credentials_and_recover_when_created(tmp_path):
    path = tmp_path / ".credentials.json"
    client, opener = make_client(path, ok())
    result = client.fetch(T0)
    assert result.status is ApiStatus.NO_CREDENTIALS and opener.calls == 0
    assert "No Claude sign-in" in result.detail
    write_credentials(path)
    assert client.fetch(T0 + secs(1)).status is ApiStatus.LIVE and opener.calls == 1


def test_tokenless_credentials_are_no_credentials_without_a_request(tmp_path):
    client, opener = make_client(write_credentials(tmp_path / ".credentials.json", token=None), ok())
    assert client.fetch(T0).status is ApiStatus.NO_CREDENTIALS and opener.calls == 0


def test_429_backoff_steps_double_from_60s_to_the_15_minute_cap(tmp_path):
    path = write_credentials(tmp_path / ".credentials.json")
    waits = [60, 120, 240, 480, 900, 900]
    client, opener = make_client(path, *[http_error(429) for _ in waits])
    walk_backoff(client, opener, T0, waits, ApiStatus.RATE_LIMITED)
    assert client.status is ApiStatus.RATE_LIMITED


@pytest.mark.parametrize("failure", [
    lambda: urllib.error.URLError("connection refused"),
    lambda: TimeoutError("timed out"),
    lambda: http_error(500),
    lambda: http_error(503),
    lambda: http_error(404),
    lambda: FakeResponse(b"<html>not json</html>"),
], ids=["url-error", "timeout", "http-500", "http-503", "http-404", "bad-json"])
def test_offline_backoff_steps_start_at_30s_and_cap_at_15_minutes(tmp_path, failure):
    path = write_credentials(tmp_path / ".credentials.json")
    waits = [30, 60, 120, 240, 480, 900, 900]
    client, opener = make_client(path, *[failure() for _ in waits])
    walk_backoff(client, opener, T0, waits, ApiStatus.OFFLINE)


def test_timeout_is_reported_as_timed_out(tmp_path):
    client, _ = make_client(write_credentials(tmp_path / ".credentials.json"), TimeoutError("slow"))
    result = client.fetch(T0)
    assert result.status is ApiStatus.OFFLINE and "timed out" in result.detail


def test_http_error_raised_by_the_opener_is_handled_like_a_response(tmp_path):
    client, _ = make_client(write_credentials(tmp_path / ".credentials.json"), http_error(429))
    assert client.fetch(T0).status is ApiStatus.RATE_LIMITED


def test_a_200_resets_the_backoff_steps(tmp_path):
    path = write_credentials(tmp_path / ".credentials.json")
    client, opener = make_client(path, http_error(429), http_error(429), ok(), http_error(429))
    client.fetch(T0)                                  # wait 60 s
    client.fetch(client.next_allowed_at)              # wait 120 s
    recovered_at = client.next_allowed_at
    assert client.fetch(recovered_at).status is ApiStatus.LIVE
    assert client.next_allowed_at is None
    client.fetch(recovered_at + secs(1))              # a new 429 starts again at 60 s
    assert client.next_allowed_at == recovered_at + secs(1) + secs(60)


def test_retry_after_seconds_sets_the_wait_and_the_backoff_keeps_counting(tmp_path):
    path = write_credentials(tmp_path / ".credentials.json")
    client, opener = make_client(path, http_error(429, {"Retry-After": "120"}), http_error(429))
    assert client.fetch(T0).status is ApiStatus.RATE_LIMITED
    assert client.next_allowed_at == T0 + secs(120)
    assert client.fetch(T0 + secs(119)).fresh is False and opener.calls == 1
    client.fetch(T0 + secs(120))
    assert opener.calls == 2
    assert client.next_allowed_at == T0 + secs(120) + secs(120)  # no header: 60 s doubled once


@pytest.mark.parametrize("header, seconds", [
    ("Wed, 21 Oct 2026 12:05:00 GMT", 300),  # five minutes after T0
    ("Wed, 21 Oct 2026 11:00:00 GMT", 0),    # already past: retry straight away
])
def test_retry_after_http_date_is_honoured(tmp_path, header, seconds):
    path = write_credentials(tmp_path / ".credentials.json")
    client, opener = make_client(path, http_error(429, {"Retry-After": header}), ok())
    client.fetch(T0)
    assert client.next_allowed_at == T0 + secs(seconds)
    assert client.fetch(T0 + secs(seconds)).status is ApiStatus.LIVE
    assert opener.calls == 2


@pytest.mark.parametrize("header", ["soon", "-5", "nan", "inf", ""])
def test_invalid_retry_after_falls_back_to_the_backoff(tmp_path, header):
    path = write_credentials(tmp_path / ".credentials.json")
    client, _ = make_client(path, http_error(429, {"Retry-After": header}))
    client.fetch(T0)
    assert client.next_allowed_at == T0 + secs(60)


def test_retry_after_is_capped_at_one_day(tmp_path):
    path = write_credentials(tmp_path / ".credentials.json")
    client, _ = make_client(path, http_error(429, {"Retry-After": "99999999999"}))
    client.fetch(T0)
    assert client.next_allowed_at == T0 + secs(86_400)


def test_fetch_before_next_allowed_returns_status_without_a_request(tmp_path):
    path = write_credentials(tmp_path / ".credentials.json")
    client, opener = make_client(path, http_error(429))
    client.fetch(T0)
    result = client.fetch(T0 + secs(10))
    assert result.status is ApiStatus.RATE_LIMITED and result.fresh is False
    assert result.snapshot is None and opener.calls == 1
    assert f"next try at {(T0 + secs(60)).astimezone().strftime('%H:%M:%S')}" in result.detail


def test_backoff_is_checked_before_credentials_are_read(tmp_path):
    path = write_credentials(tmp_path / ".credentials.json")
    client, opener = make_client(path, urllib.error.URLError("down"))
    client.fetch(T0)
    path.unlink()  # gone, but the client is still backing off: it must not look at the file
    result = client.fetch(T0 + secs(5))
    assert result.status is ApiStatus.OFFLINE and opener.calls == 1


def test_credentials_change_does_not_bypass_an_active_backoff(tmp_path):
    path = write_credentials(tmp_path / ".credentials.json")
    client, opener = make_client(path, http_error(429), ok())
    client.fetch(T0)
    write_credentials(path, mtime=BASE_MTIME + 120)
    assert client.fetch(T0 + secs(59)).status is ApiStatus.RATE_LIMITED and opener.calls == 1
    assert client.fetch(T0 + secs(60)).status is ApiStatus.LIVE and opener.calls == 2


def test_last_snapshot_survives_failures(tmp_path):
    path = write_credentials(tmp_path / ".credentials.json")
    client, _ = make_client(path, ok(), urllib.error.URLError("down"))
    good = client.fetch(T0)
    failed = client.fetch(T0 + secs(1))
    assert failed.status is ApiStatus.OFFLINE
    assert failed.snapshot is good.snapshot is client.last_snapshot
    assert client.last_ok_at == T0


@pytest.mark.parametrize("make_body", [
    lambda: b"<html>oops</html>",
    lambda: b"\xff\xfe\x00",
    lambda: b"{}",
    lambda: b"[1, 2, 3]",
    lambda: b"x" * (ua.MAX_BODY_BYTES + 10),
], ids=["html", "not-utf8", "empty-object", "array", "oversized"])
def test_unusable_200_bodies_are_offline_and_keep_the_last_snapshot(tmp_path, make_body):
    path = write_credentials(tmp_path / ".credentials.json")
    client, _ = make_client(path, ok(), FakeResponse(make_body()))
    client.fetch(T0)
    previous = client.last_snapshot
    result = client.fetch(T0 + secs(1))
    assert result.status is ApiStatus.OFFLINE and result.fresh is False
    assert client.last_snapshot is previous and result.snapshot is previous


def test_body_that_echoes_the_token_is_refused(tmp_path):
    path = write_credentials(tmp_path / ".credentials.json")
    echo = FakeResponse(json.dumps({"limits": [], "echo": TOKEN}))
    client, _ = make_client(path, echo)
    result = client.fetch(T0)
    assert result.status is ApiStatus.OFFLINE and client.last_snapshot is None
    assert TOKEN not in result.detail


def test_unexpected_exception_is_contained_and_backs_off(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    client, opener = make_client(write_credentials(tmp_path / ".credentials.json"), RuntimeError("boom"))
    result = client.fetch(T0)
    assert result.status is ApiStatus.OFFLINE and "unexpected error" in result.detail
    assert client.next_allowed_at == T0 + secs(30)
    assert "RuntimeError" in caplog.text and "boom" not in caplog.text


def test_token_never_appears_in_logs_or_results(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    path = write_credentials(tmp_path / ".credentials.json")
    leaky = urllib.error.URLError(f"proxy rejected {TOKEN}")
    client, _ = make_client(path, leaky, http_error(401, {"X-Echo": TOKEN}), ok())
    results = [client.fetch(T0), client.fetch(T0 + secs(30)), client.fetch(T0 + secs(31))]
    write_credentials(path, mtime=BASE_MTIME + 120)
    results.append(client.fetch(T0 + secs(32)))
    assert [r.status for r in results] == [
        ApiStatus.OFFLINE, ApiStatus.STALE, ApiStatus.STALE, ApiStatus.LIVE]
    assert caplog.records
    for record in caplog.records:
        assert TOKEN not in record.getMessage()
        assert TOKEN not in (record.exc_text or "")
    assert TOKEN not in caplog.text
    for result in results:
        assert TOKEN not in result.detail and TOKEN not in repr(result)


@pytest.mark.parametrize("status, expected", [
    (401, ApiStatus.STALE),
    (403, ApiStatus.STALE),
    (429, ApiStatus.RATE_LIMITED),
    (503, ApiStatus.OFFLINE),
])
def test_non_2xx_responses_returned_without_an_exception_are_classified(tmp_path, status, expected):
    path = write_credentials(tmp_path / ".credentials.json")
    client, opener = make_client(path, FakeResponse(b"", status=status))
    assert client.fetch(T0).status is expected
    assert opener.calls == 1


def test_a_failing_parser_section_does_not_sink_the_snapshot(monkeypatch):
    def boom(_section: Any) -> None:
        raise RuntimeError("bad section")

    monkeypatch.setattr(ua, "_parse_spend", boom)
    snap = ua.parse_usage(CURRENT, T0)
    assert [limit.label for limit in snap.limits] == ["SESSION", "WEEKLY", "FABLE WEEKLY"]
    assert snap.spend is None
    assert snap.breakdown[0].key == "claude_code"


def test_unreadable_credentials_path_is_absent_not_fatal(tmp_path):
    info, token = ua.read_credentials(tmp_path)  # a directory, not a file
    assert info.present is False and token is None


# ── Regression: deep JSON, overflowing times, half-written files, redirects, slow bodies ──

def test_deeply_nested_credentials_are_unreadable_not_fatal(tmp_path):
    path = tmp_path / ".credentials.json"
    path.write_text("[" * 100_000 + "]" * 100_000, encoding="utf-8")
    info, token = ua.read_credentials(path)
    assert info.present is False and token is None


def test_a_reset_time_that_overflows_on_conversion_is_unknown_not_an_error():
    assert ua._parse_ts("9999-12-31T23:30:00-05:00") is None


def test_an_out_of_range_reset_time_drops_only_that_limit():
    raw = {"limits": [
        {"kind": "session", "percent": 42, "resets_at": "2026-10-22T02:39:59Z"},
        {"kind": "weekly_all", "percent": 10, "resets_at": "9999-12-31T23:30:00-05:00"},
    ]}
    snapshot = ua.parse_usage(raw, T0)
    assert snapshot.session is not None and snapshot.session.percent == 42
    assert snapshot.weekly is not None and snapshot.weekly.resets_at is None


def test_inspect_credentials_tells_a_half_written_file_from_a_signed_out_one(tmp_path):
    half = tmp_path / "half.json"
    half.write_text('{"claudeAiOauth": {"accessToken": ', encoding="utf-8")
    assert ua.inspect_credentials(half)[2] is False
    signed_out = tmp_path / "out.json"
    signed_out.write_text('{"mcpOAuth": {}}', encoding="utf-8")
    assert ua.inspect_credentials(signed_out)[2] is True
    assert ua.inspect_credentials(tmp_path / "gone.json")[2] is True


def test_a_half_written_credentials_file_keeps_the_last_status(tmp_path):
    path = write_credentials(tmp_path / ".credentials.json")
    client, opener = make_client(path, ok())
    assert client.fetch(T0).status is ApiStatus.LIVE
    path.write_text('{"claudeAiOauth": {"accessT', encoding="utf-8")  # Claude Code is mid-write
    result = client.fetch(T0 + secs(60))
    assert result.status is ApiStatus.LIVE and result.fresh is False
    assert opener.calls == 1


def test_a_half_written_credentials_file_does_not_clear_an_active_backoff(tmp_path):
    path = write_credentials(tmp_path / ".credentials.json")
    client, opener = make_client(path, http_error(429))
    assert client.fetch(T0).status is ApiStatus.RATE_LIMITED
    assert client.next_allowed_at == T0 + secs(60)
    path.write_text('{"claudeAiOauth": {"accessT', encoding="utf-8")
    result = client.fetch(T0 + secs(61))
    assert result.status is ApiStatus.RATE_LIMITED
    assert client.next_allowed_at == T0 + secs(60)
    assert opener.calls == 1


def test_a_usage_body_that_trickles_in_is_offline_once_the_time_budget_is_spent(tmp_path, monkeypatch):
    monkeypatch.setattr(ua, "BODY_BUDGET_S", 0.0, raising=False)
    path = write_credentials(tmp_path / ".credentials.json")
    client, _opener = make_client(path, ok())
    result = client.fetch(T0)
    assert result.status is ApiStatus.OFFLINE
    assert "timed out" in result.detail


def _serve(status: int, location: str | None = None):
    """A loopback HTTP server that answers every GET with the given status.

    Returns (server, list of Authorization headers it received).
    """
    seen: list[str | None] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen.append(self.headers.get("Authorization"))
            self.send_response(status)
            if location is not None:
                self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, seen


def test_the_bearer_token_is_not_sent_to_a_redirect_target(tmp_path, monkeypatch):
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")  # keep the loopback servers off any proxy
    target, target_seen = _serve(200)
    origin, origin_seen = _serve(302, location=f"http://127.0.0.1:{target.server_address[1]}/collect")
    try:
        monkeypatch.setattr(ua, "USAGE_URL", f"http://127.0.0.1:{origin.server_address[1]}/api/oauth/usage")
        path = write_credentials(tmp_path / ".credentials.json")
        result = ua.UsageClient(path).fetch(T0)  # the client's own opener, not an injected one
        assert result.status is ApiStatus.OFFLINE
        assert len(origin_seen) == 1
        assert target_seen == []
    finally:
        for server in (origin, target):
            server.shutdown()
            server.server_close()


# ── resets_at rounding ────────────────────────────────────────────────────────

def _limits_payload(*resets):
    return {"limits": [{"kind": "session", "group": "session", "percent": 40, "resets_at": r}
                       for r in resets]}


def test_resets_at_jitter_rounds_to_the_same_minute():
    now = datetime(2026, 10, 22, 5, 0, tzinfo=timezone.utc)
    a = ua.parse_usage(_limits_payload("2026-10-22T07:39:59.890288+00:00"), now).limits[0].resets_at
    b = ua.parse_usage(_limits_payload("2026-10-22T07:40:00.392639+00:00"), now).limits[0].resets_at
    assert a == b == datetime(2026, 10, 22, 7, 40, tzinfo=timezone.utc)


def test_resets_at_rounds_half_minute_up_and_below_down():
    now = datetime(2026, 10, 22, 5, 0, tzinfo=timezone.utc)
    up = ua.parse_usage(_limits_payload("2026-10-22T07:39:30+00:00"), now).limits[0].resets_at
    down = ua.parse_usage(_limits_payload("2026-10-22T07:39:29.999+00:00"), now).limits[0].resets_at
    assert up == datetime(2026, 10, 22, 7, 40, tzinfo=timezone.utc)
    assert down == datetime(2026, 10, 22, 7, 39, tzinfo=timezone.utc)


def test_resets_at_rounding_survives_the_end_of_time():
    now = datetime(2026, 10, 22, 5, 0, tzinfo=timezone.utc)
    last = ua.parse_usage(_limits_payload("9999-12-31T23:59:45+00:00"), now).limits[0].resets_at
    assert last == datetime(9999, 12, 31, 23, 59, tzinfo=timezone.utc)


def test_legacy_window_resets_at_is_rounded_too():
    now = datetime(2026, 10, 22, 5, 0, tzinfo=timezone.utc)
    snap = ua.parse_usage({"five_hour": {"utilization": 12, "resets_at": "2026-10-22T07:40:00.48+00:00"}}, now)
    assert snap.session is not None
    assert snap.session.resets_at == datetime(2026, 10, 22, 7, 40, tzinfo=timezone.utc)
