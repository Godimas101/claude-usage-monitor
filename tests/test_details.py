"""The status of a usage reading as codes and values, and its one English wording (#90).

english() must give the text state.json and the agent modes have always carried: those strings are the contract.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from usage_monitor.data import details
from usage_monitor.data.details import Detail, english, offline, refused
from usage_monitor.data.usage_api import UsageClient


@pytest.mark.parametrize(("detail", "text"), [
    (Detail("loading"), "Loading usage"),
    (Detail("paused"), "Paused"),
    (Detail("updated", {"clock": "11:59:00"}), "Updated 11:59:00"),
    (Detail("expired"), "The Claude sign-in has expired; send Claude Code a message to renew it"),
    (refused(401), "Claude did not accept the sign-in (HTTP 401); send Claude Code a message, and sign in again if it asks"),
    (refused(403), "Claude did not accept the sign-in (HTTP 403); sign in to Claude Code again with /login"),
    (Detail("no_sign_in"), "No Claude sign-in on this PC; sign in to Claude Code once (the dashboard shows how)"),
    (Detail("rate_limited", {"next_try": "12:01:03"}), "Usage API rate limited; next try at 12:01:03"),
    (offline("timed_out", None).with_next_try("12:01:30"),
     "Usage API unreachable (timed out); next try at 12:01:30"),
    (offline("http", 503), "Usage API unreachable (HTTP 503)"),
    (offline("network_error"), "Usage API unreachable (network error)"),
    (offline("bad_response"), "Usage API unreachable (bad response)"),
    (offline("unexpected_response"), "Usage API unreachable (unexpected response)"),
    (offline("unexpected_error"), "Usage API unreachable (unexpected error)"),
])
def test_english_words_each_code_as_the_status_text_has_always_read(detail, text):
    assert english(detail) == text


def test_a_next_try_is_added_only_to_a_status_that_backs_off():
    assert Detail("expired").with_next_try("12:00:00") == Detail("expired")
    assert offline("timed_out").with_next_try("12:00:00").values["next_try"] == "12:00:00"


def write_sign_in(path: Path) -> None:
    expires = int((datetime.now(timezone.utc) + timedelta(hours=8)).timestamp() * 1000)
    path.write_text(json.dumps({"claudeAiOauth": {"accessToken": "tok-SYNTHETIC-0123456789", "expiresAt": expires,
                                                   "subscriptionType": "max"}}), encoding="utf-8")


def test_a_failed_request_carries_its_code_and_its_words_are_the_same(tmp_path):
    def timed_out(request, timeout):
        raise TimeoutError("slow")

    path = tmp_path / ".credentials.json"
    write_sign_in(path)
    now = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
    result = UsageClient(path, opener=timed_out).fetch(now)
    assert result.info is not None and result.info.code == details.OFFLINE
    assert result.info.values["reason"] == "timed_out"
    assert result.info.values["next_try"] == result.detail.rsplit("next try at ", 1)[1]
    assert english(result.info) == result.detail
    assert "timed out" in result.detail


def test_a_live_reading_carries_its_clock_as_a_value(tmp_path):
    class Response:
        status = 200
        headers: dict = {}

        def read(self, limit=None):
            return b'{"limits": []}'

        def close(self):
            pass

    path = tmp_path / ".credentials.json"
    write_sign_in(path)
    now = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
    result = UsageClient(path, opener=lambda request, timeout: Response()).fetch(now)
    assert result.info == Detail("updated", {"clock": result.info.values["clock"]})
    assert result.detail == f"Updated {result.info.values['clock']}"
