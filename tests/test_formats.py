"""Today's English formats are frozen (#91): tests/fixtures/formats_english.json holds what the model, the tray, the
skins and the alerts said before #91 changed any of them, and English must not change by a byte.

The fixture's moments are local wall times, made in the test's own time zone here, so the texts are the same on any PC.
HELPERS names each recorded helper and how it is called; a step that reshapes one changes its entry, not the fixture.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import pytest

from usage_monitor import app
from usage_monitor.data import alerts
from usage_monitor.data.identity import legacy_label
from usage_monitor.data.models import Limit, Spend
from usage_monitor.render import model
from usage_monitor.render.model import Row
from usage_monitor.render.skins import gates, night_city, nostromo

FIXTURE = Path(__file__).parent / "fixtures" / "formats_english.json"
MONDAY = datetime(2026, 7, 13)          # a week with no daylight-saving change anywhere


@pytest.fixture(scope="module")
def english() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def local(text: str) -> datetime:
    """A wall time in this PC's zone, as the model reads a reset time."""
    return datetime.fromisoformat(text).astimezone()


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def limit(kind: str, *, resets_at: datetime | None = None, model: str | None = None, surface: str | None = None,
          label: str | None = None) -> Limit:
    return Limit(kind=kind, group="session" if kind == "session" else "weekly", percent=40.0, severity=None,
                 resets_at=resets_at, is_active=True,
                 label=legacy_label(kind, model, surface) if label is None else label, scope_model=model,
                 scope_surface=surface)


def scoped_row(share: float | None) -> Row:
    return Row("scoped:fable", "FABLE WEEKLY", 4.0, 0.5, "normal", None, None, None, None, kind="weekly_scoped",
               scope_model="Fable", scope_name="Fable", share_pct=share)


# Each recorded helper as today's callers call it.
HELPERS: dict[str, dict[str, Callable[[Any], str]]] = {
    "percent": {
        "app._percent_text": app._percent_text,
        "nostromo._percent_text": nostromo._percent_text,
        "nostromo._exact_percent_text": nostromo._exact_percent_text,
        "gates._percent_text": gates._percent_text,
        "gates._exact_percent_text": gates._exact_percent_text,
        "night_city._digits": night_city._digits,
        "night_city._exact_digits": night_city._exact_digits,
    },
    "cap": {
        "nostromo._number": nostromo._number,
        "gates._number": gates._number,
        "night_city._number": night_city._number,
    },
    "share": {
        "nostromo._qualifier": lambda share: nostromo._qualifier(scoped_row(share)),
        "nostromo._short_qualifier": lambda share: nostromo._short_qualifier(scoped_row(share)),
        "gates._tag": lambda share: gates._tag(scoped_row(share)),
        "night_city._share_tag": lambda share: night_city._share_tag(scoped_row(share)),
    },
}


def test_the_fixture_is_whole(english):
    """So a test below cannot pass by reading an empty list: every helper of HELPERS has its outputs, one per input."""
    assert english["weekdays"] == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    assert len(english["clock"]) == 24 * 60 and english["clock"][16 * 60 + 12] == "4:12 PM"
    for group, helpers in HELPERS.items():
        assert set(english[group]) == {"inputs", *helpers}
        assert all(len(english[group][name]) == len(english[group]["inputs"]) for name in helpers)
    for key in ("format_resets", "format_age", "format_countdown", "share_text", "alerts._name", "alerts._reset_text",
                "alerts._credits_text"):
        assert english[key], key


def test_a_reset_today_reads_as_the_recorded_clock_for_every_minute(english):
    now = local(f"{MONDAY:%Y-%m-%d}T00:00")
    for minute, clock in enumerate(english["clock"]):
        assert model.format_resets(now + timedelta(minutes=minute), now) == clock


def test_a_reset_on_another_day_reads_as_the_recorded_weekday_and_clock(english):
    now = local(f"{MONDAY:%Y-%m-%d}T12:00")
    for day in range(1, 8):
        for minute in range(0, 24 * 60, 7):
            at = local(f"{MONDAY + timedelta(days=day, minutes=minute):%Y-%m-%dT%H:%M}")
            assert model.format_resets(at, now) == f"{english['weekdays'][day % 7]} {english['clock'][minute]}"


def test_reset_texts_are_the_recorded_ones(english):
    for at, now, text in english["format_resets"]:
        assert model.format_resets(local(at), local(now)) == text, (at, now)


def test_ages_countdowns_and_shares_are_the_recorded_ones(english):
    now = datetime(2026, 10, 21, 20, 12, tzinfo=timezone.utc)
    for seconds, text in english["format_age"]:
        assert model.format_age(now - timedelta(seconds=seconds), now) == text, seconds
    for seconds, text in english["format_countdown"]:
        assert model.format_countdown(seconds) == text, seconds
    for share, text in english["share_text"]:
        assert model.share_text(share) == text, share


@pytest.mark.parametrize("group,name", [(group, name) for group, helpers in HELPERS.items() for name in helpers])
def test_the_widgets_numbers_are_the_recorded_ones(english, group, name):
    helper = HELPERS[group][name]
    for value, text in zip(english[group]["inputs"], english[group][name]):
        if isinstance(text, dict):                          # an input the helper refuses; no caller passes it
            with pytest.raises(Exception) as raised:
                helper(value)
            assert type(raised.value).__name__ == text["raises"], (name, value)
        else:
            assert helper(value) == text, (name, value)


def test_the_alerts_names_reset_texts_and_credits_are_the_recorded_ones(english):
    for kind, scope_model, surface, label, text in english["alerts._name"]:
        assert alerts._name(limit(kind, model=scope_model, surface=surface, label=label)) == text
    for kind, at, text in english["alerts._reset_text"]:
        assert alerts._reset_text(limit(kind, resets_at=utc(at)), timezone.utc) == text, (kind, at)
    for used, limit_value, currency, text in english["alerts._credits_text"]:
        assert alerts._credits_text(Spend(enabled=True, used=used, limit=limit_value, currency=currency)) == text


def test_the_alerts_clock_is_the_recorded_clock_for_every_minute(english):
    """The alerts word their reset clock apart from the model; both say the same, for every minute and weekday."""
    for day in range(7):
        for minute, clock in enumerate(english["clock"]):
            at = utc(f"{MONDAY + timedelta(days=day, minutes=minute):%Y-%m-%dT%H:%M}")
            assert alerts._reset_text(limit("session", resets_at=at), timezone.utc) == f"Resets {clock}"
            weekly = alerts._reset_text(limit("weekly_all", resets_at=at), timezone.utc)
            assert weekly == f"Resets {english['weekdays'][day]} {clock}"
