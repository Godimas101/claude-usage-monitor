"""Tests for usage_monitor.render: the view model, the Pillow toolkit, the Nostromo skin and the dump.

Everything renders to memory or to pytest's tmp_path. No window, tray icon or process is created,
so none of these tests needs CUM_UI_TESTS=1 and all of them run in CI.
"""
from __future__ import annotations

import dataclasses
import time
from dataclasses import replace
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from usage_monitor.agent import guard as guard_mod
from usage_monitor.data.models import (
    ApiStatus,
    BurnRate,
    IngestProgress,
    Limit,
    MonitorState,
    PlanInfo,
    UsageSnapshot,
)
from usage_monitor.render import dump, engine
from usage_monitor.render.model import (
    SEVERITY_CRITICAL,
    SEVERITY_NORMAL,
    SEVERITY_WARNING,
    STALE_MESSAGE_EXPIRED,
    STALE_MESSAGE_NO_LOGIN,
    GuardMark,
    WidgetModel,
    build_model,
    elapsed_fraction,
    format_age,
    format_countdown,
    format_resets,
    pace_for,
    severity_for,
    share_text,
    worst_severity,
)
from usage_monitor.render.skins import RenderOptions, coerce_options, get_skin
from usage_monitor.render.skins import nostromo as nostromo_skin
from usage_monitor.render.skins.nostromo import NostromoSkin, Palette

MOCKUPS = Path(__file__).resolve().parents[1] / "design" / "mockups"

# A fixed local clock: Wednesday 2026-10-21 16:12, the sample clock used across the mockups.
NOW = datetime(2026, 10, 21, 16, 12).astimezone()
PLAN = PlanInfo(subscription_type="max", rate_limit_tier="default_claude_max_5x", name="Max 5x",
                monthly_price_usd=100.0)
ALL_STATES = dump.STATE_NAMES
SCALES = (1.0, 1.25, 1.5, 2.0)
FLOAT_HEIGHTS = {
    "normal3": 126, "normal2": 94, "warning": 126, "critical": 126, "stale": 126,
    "offline": 94, "paused": 94, "rate_limited": 94, "no_credentials": 126, "loading": 94,
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def limit(kind: str, percent: float, *, resets_in: timedelta | None = timedelta(hours=2), label: str | None = None,
          severity: str | None = None, active: bool = True, window: int | None = None,
          scope: str | None = None, allowance: float | None = None) -> Limit:
    resets_at = None if resets_in is None else NOW + resets_in
    default_label = {"session": "SESSION", "weekly_all": "WEEKLY"}.get(kind, "FABLE WEEKLY")
    return Limit(
        kind=kind,
        group="session" if kind == "session" else "weekly",
        percent=percent,
        severity=severity,
        resets_at=resets_at,
        is_active=active,
        label=label or default_label,
        scope_model=scope,
        window_seconds=window,
        allowance_pct=allowance,
    )


def state(*limits: Limit, status: ApiStatus = ApiStatus.LIVE, age: timedelta = timedelta(seconds=12),
          with_usage: bool = True) -> MonitorState:
    fetched = NOW - age
    usage = UsageSnapshot(fetched_at=fetched, limits=list(limits)) if with_usage else None
    return MonitorState(
        status=status,
        status_detail="",
        usage=usage,
        usage_fetched_at=fetched if usage is not None else None,
        plan=PLAN,
        windows={},
        burn=BurnRate(),
        unpriced_models=[],
        ingest=IngestProgress(),
        updated_at=NOW,
    )


@lru_cache(maxsize=1)
def themes() -> dict[str, Any]:
    return dump.load_theme_objects()


def nostromo():
    return themes()["nostromo"]


def alpha_extrema(image: Image.Image) -> tuple[int, int]:
    return image.getchannel("A").getextrema()


# ── View model: status, staleness and messages ───────────────────────────────

def test_missing_state_renders_as_loading_with_dashes():
    model = build_model(None, NOW)
    assert model.status is ApiStatus.LOADING
    assert model.status_text == "LOADING"
    assert model.stale is True
    assert model.stale_message is None
    assert model.age_text is None
    assert model.updated_at == NOW
    assert [row.key for row in model.rows] == ["session", "weekly"]
    assert all(row.percent is None and row.time_fraction is None for row in model.rows)


@pytest.mark.parametrize(("status", "text"), [
    (ApiStatus.LOADING, "LOADING"),
    (ApiStatus.LIVE, "LIVE"),
    (ApiStatus.STALE, "STALE"),
    (ApiStatus.OFFLINE, "OFFLINE"),
    (ApiStatus.RATE_LIMITED, "RATE LIMITED"),
    (ApiStatus.NO_CREDENTIALS, "NO LOGIN"),
    (ApiStatus.PAUSED, "PAUSED"),
])
def test_status_text_for_every_status(status, text):
    model = build_model(state(limit("session", 10), limit("weekly_all", 5), status=status), NOW)
    assert model.status_text == text


@pytest.mark.parametrize(("status", "stale", "message"), [
    (ApiStatus.LIVE, False, None),
    (ApiStatus.PAUSED, False, None),
    (ApiStatus.STALE, True, STALE_MESSAGE_EXPIRED),
    (ApiStatus.NO_CREDENTIALS, True, STALE_MESSAGE_NO_LOGIN),
    (ApiStatus.OFFLINE, True, None),
    (ApiStatus.RATE_LIMITED, True, None),
])
def test_stale_flag_and_message_per_status(status, stale, message):
    model = build_model(state(limit("session", 10), limit("weekly_all", 5), status=status), NOW)
    assert model.stale is stale
    assert model.stale_message == message


def test_expired_login_message_text_is_the_agreed_wording():
    assert STALE_MESSAGE_EXPIRED == "LOGIN EXPIRED - OPEN CLAUDE CODE"


# ── Sign-in copy: the view model is neutral, each skin words it as its approved mockup ──────────

NOSTROMO_AUTH_LINES = ["AUTH LINK LOST.", "OPEN CLAUDE CODE TO RESTORE."]


def mockup_html(theme_id: str) -> str:
    path = MOCKUPS / theme_id / "final.html"
    if not path.is_file():
        pytest.skip(f"{path} is not part of this checkout")
    return path.read_text(encoding="utf-8")


def test_nostromo_words_the_expired_sign_in_as_its_approved_mockup():
    model = dump.sample_model("stale")
    assert model.stale_message == STALE_MESSAGE_EXPIRED          # the neutral default stays in the model
    text = nostromo_skin._message_text(model)
    assert text == "\n".join(NOSTROMO_AUTH_LINES)
    assert nostromo_skin._message_lines(text) == NOSTROMO_AUTH_LINES
    assert "<br>".join(NOSTROMO_AUTH_LINES) in mockup_html("nostromo")


def test_nostromo_keeps_the_neutral_text_where_it_has_no_wording_of_its_own():
    no_login = dump.sample_model("no_credentials")
    assert nostromo_skin._message_text(no_login) == STALE_MESSAGE_NO_LOGIN
    assert nostromo_skin._message_lines(STALE_MESSAGE_NO_LOGIN) == ["NO LOGIN FOUND", "OPEN THE DASHBOARD"]
    for name in ("normal3", "offline", "paused", "rate_limited", "loading"):
        assert nostromo_skin._message_text(dump.sample_model(name)) is None, name


def test_a_skin_wording_never_leaks_into_the_view_model():
    for name in ("stale", "no_credentials"):
        assert "AUTH LINK" not in (dump.sample_model(name).stale_message or "")


def test_night_city_and_gates_word_the_expired_sign_in_as_their_approved_mockups():
    from usage_monitor.render.skins import gates, night_city

    stale = dump.sample_model("stale")
    band = night_city._band_text(themes()["night_city"], stale)
    assert band == "RELINK // OPEN CLAUDE CODE"
    assert band.lower() in mockup_html("night_city").lower()
    footer = "".join(run[0] for run in gates._footer_runs(stale))
    assert footer == "Hmm, we lost touch. Sign in to Claude Code."
    assert "Hmm, we lost touch.</b> Sign in to Claude Code." in mockup_html("gates")


def test_no_usage_keeps_status_but_dashes_the_rows():
    model = build_model(state(status=ApiStatus.OFFLINE, with_usage=False), NOW)
    assert model.status_text == "OFFLINE"
    assert model.age_text is None
    assert [row.percent for row in model.rows] == [None, None]


def test_blink_flag_passes_through():
    assert build_model(state(limit("session", 1)), NOW, blink_on=False).blink_on is False
    assert build_model(state(limit("session", 1)), NOW).blink_on is True


# ── View model: severity ──────────────────────────────────────────────────────

@pytest.mark.parametrize(("api", "percent", "expected"), [
    ("normal", 99.0, SEVERITY_NORMAL),
    ("NORMAL", 99.0, SEVERITY_NORMAL),
    ("warning", 3.0, SEVERITY_WARNING),
    ("critical", 3.0, SEVERITY_CRITICAL),
    ("exceeded", 3.0, SEVERITY_CRITICAL),       # anything worse than warning is critical
])
def test_api_severity_wins_over_percent(api, percent, expected):
    assert severity_for(api, percent) == expected


@pytest.mark.parametrize(("percent", "expected"), [
    (0.0, SEVERITY_NORMAL),
    (74.9, SEVERITY_NORMAL),
    (75.0, SEVERITY_WARNING),
    (89.9, SEVERITY_WARNING),
    (90.0, SEVERITY_CRITICAL),
    (112.0, SEVERITY_CRITICAL),
])
def test_threshold_severity_when_api_is_silent(percent, expected):
    assert severity_for(None, percent) == expected
    assert severity_for("  ", percent) == expected


def test_severity_of_missing_percent_is_normal():
    assert severity_for(None, None) == SEVERITY_NORMAL


def test_worst_severity_ignores_rows_without_data():
    model = build_model(state(limit("session", 95), limit("weekly_all", 10)), NOW)
    assert worst_severity(model.rows) == SEVERITY_CRITICAL
    empty = build_model(state(status=ApiStatus.OFFLINE, with_usage=False), NOW)
    assert worst_severity(empty.rows) == SEVERITY_NORMAL


# ── View model: time windows and pace ─────────────────────────────────────────

def test_time_fraction_uses_default_windows():
    session = limit("session", 10, resets_in=timedelta(hours=3, minutes=27))   # 31 % elapsed
    weekly = limit("weekly_all", 10, resets_in=timedelta(days=3))              # 4/7 elapsed
    model = build_model(state(session, weekly), NOW)
    assert model.rows[0].time_fraction == pytest.approx(0.31, abs=0.001)
    assert model.rows[1].time_fraction == pytest.approx(4 / 7, abs=0.001)


def test_time_fraction_prefers_the_window_seconds_the_api_gives():
    one_hour = limit("session", 10, resets_in=timedelta(minutes=45), window=3600)
    model = build_model(state(one_hour), NOW)
    assert model.rows[0].time_fraction == pytest.approx(0.25, abs=0.001)


def test_time_fraction_is_clamped_and_none_without_reset():
    overdue = limit("session", 10, resets_in=timedelta(hours=-1))
    far = limit("session", 10, resets_in=timedelta(days=30))
    unknown = limit("session", 10, resets_in=None)
    assert build_model(state(overdue), NOW).rows[0].time_fraction == 1.0
    assert build_model(state(far), NOW).rows[0].time_fraction == 0.0
    assert build_model(state(unknown), NOW).rows[0].time_fraction is None


def test_elapsed_fraction_rejects_non_positive_windows():
    assert elapsed_fraction(NOW, 0, NOW) is None
    assert elapsed_fraction(None, 18000, NOW) is None


@pytest.mark.parametrize(("percent", "fraction", "expected"), [
    (70.0, 0.5, "ahead"),        # +20 points
    (30.0, 0.5, "behind"),       # -20 points
    (50.0, 0.5, "on_pace"),
    (60.0, 0.5, "on_pace"),      # exactly +10 is still on pace
    (60.1, 0.5, "ahead"),
    (40.0, 0.5, "on_pace"),     # exactly -10 is still on pace
    (39.9, 0.5, "behind"),      # -10.1 points
    (50.0, None, None),
    (None, 0.5, None),
])
def test_pace_bands(percent, fraction, expected):
    assert pace_for(percent, fraction) == expected


def test_pace_in_the_built_rows():
    ahead = build_model(state(limit("session", 72, resets_in=timedelta(hours=3, minutes=27))), NOW)
    assert ahead.rows[0].pace == "ahead"
    behind = build_model(state(limit("session", 5, resets_in=timedelta(hours=3, minutes=27)),
                               limit("weekly_all", 5, resets_in=timedelta(days=3))), NOW)
    assert behind.rows[0].pace == "behind"
    assert behind.rows[1].pace == "behind"


# ── View model: local reset text ──────────────────────────────────────────────

def test_resets_text_same_local_day_is_clock_only():
    reset = datetime(2026, 10, 21, 19, 39).astimezone()
    assert format_resets(reset, NOW) == "7:39 PM"


def test_resets_text_other_day_adds_weekday():
    reset = datetime(2026, 10, 24, 16, 0).astimezone()
    assert format_resets(reset, NOW) == "Sat 4:00 PM"


def test_resets_text_midnight_and_noon_use_twelve_hour_clock():
    assert format_resets(datetime(2026, 10, 21, 0, 5).astimezone(), NOW) == "12:05 AM"
    assert format_resets(datetime(2026, 10, 21, 12, 30).astimezone(), NOW) == "12:30 PM"


def test_resets_text_in_the_built_row_is_local():
    reset = datetime(2026, 10, 21, 19, 39).astimezone()
    model = build_model(state(limit("session", 10, resets_in=reset - NOW)), NOW)
    assert model.rows[0].resets_text == "7:39 PM"
    assert model.rows[0].resets_at == reset


# ── View model: scoped rows ───────────────────────────────────────────────────

def scoped(percent: float, *, active: bool, allowance: float | None = 50.0) -> Limit:
    return limit("weekly_scoped", percent, label="FABLE WEEKLY", active=active, scope="Fable",
                 allowance=allowance, resets_in=timedelta(days=3))


@pytest.mark.parametrize(("percent", "active", "mode", "shown"), [
    (0.0, False, "auto", False),     # idle scoped row hidden in auto
    (0.5, False, "auto", False),     # below 1 % and not active
    (0.5, True, "auto", True),       # active shows in auto
    (1.0, False, "auto", True),      # 1 % shows in auto
    (0.0, False, "always", True),
    (50.0, True, "never", False),
])
def test_scoped_row_visibility(percent, active, mode, shown):
    model = build_model(state(limit("session", 10), limit("weekly_all", 10), scoped(percent, active=active)),
                        NOW, show_scoped=mode)
    keys = [row.key for row in model.rows]
    assert ("scoped:fable" in keys) is shown


def test_scoped_row_carries_share_text_and_label():
    model = build_model(state(limit("session", 10), limit("weekly_all", 10), scoped(4.0, active=True)), NOW)
    row = next(r for r in model.rows if r.key == "scoped:fable")
    assert row.label == "FABLE WEEKLY"
    assert row.share_text == "50% SHARE"
    assert share_text(None) is None
    assert share_text(33.4) == "33% SHARE"


def test_invalid_show_scoped_mode_is_rejected():
    with pytest.raises(ValueError):
        build_model(state(limit("session", 10)), NOW, show_scoped="sometimes")  # type: ignore[arg-type]


# ── View model: age text ──────────────────────────────────────────────────────

@pytest.mark.parametrize(("seconds", "text"), [
    (0, "0 S AGO"),
    (12, "12 S AGO"),
    (59, "59 S AGO"),
    (60, "1 M AGO"),
    (41 * 60, "41 M AGO"),
    (3 * 3600, "3 H AGO"),
    (47 * 3600, "47 H AGO"),
    (50 * 3600, "2 D AGO"),
    (-30, "0 S AGO"),
])
def test_age_text(seconds, text):
    assert format_age(NOW - timedelta(seconds=seconds), NOW) == text


def test_age_text_in_the_built_model():
    model = build_model(state(limit("session", 10), age=timedelta(seconds=12)), NOW)
    assert model.age_text == "12 S AGO"


def test_row_keys_and_labels_for_the_basic_pair():
    model = build_model(state(limit("session", 10), limit("weekly_all", 20)), NOW)
    assert [(r.key, r.label) for r in model.rows] == [("session", "SESSION"), ("weekly", "WEEKLY")]
    assert [r.percent for r in model.rows] == [10.0, 20.0]


def test_elapsed_time_is_never_negative_for_future_fetch():
    model = build_model(state(limit("session", 10), age=timedelta(seconds=-20)), NOW)
    assert model.age_text == "0 S AGO"


# ── View model: the usage guard (#27) ─────────────────────────────────────────

GUARD_POLICY = {"enabled": True, "session_cap": 95, "weekly_cap": 90, "scoped_cap": None, "stagger_s": 120}


def guarded(*limits: Limit, policy: dict[str, Any] | None = None, **kwargs: Any) -> MonitorState:
    """A state carrying the verdict the real guard (agent/guard.py) gives its limits, so the model is tested on the
    shape the app publishes."""
    plain = state(*limits, **kwargs)
    verdict = guard_mod.evaluate(guard_mod.limits_doc(plain.usage), {**GUARD_POLICY, **(policy or {})}, NOW)
    return replace(plain, guard=verdict)


FABLE = dict(label="FABLE WEEKLY", scope="Fable", allowance=50.0, resets_in=timedelta(days=3))


@pytest.mark.parametrize("block", [
    {},
    None,
    {"state": "off", "over": [], "resume_at": None, "policy": GUARD_POLICY},
    {"state": "sideways"},
    {"state": ""},
    "holding",
    ["armed"],
])
def test_a_guard_that_is_off_missing_or_empty_gives_no_mark(block):
    assert build_model(replace(state(limit("session", 99), limit("weekly_all", 99)), guard=block), NOW).guard is None


def test_no_state_a_state_without_the_field_and_a_directly_built_model_have_no_guard():
    assert build_model(None, NOW).guard is None
    plain = state(limit("session", 10))
    old = SimpleNamespace(status=plain.status, usage=plain.usage, usage_fetched_at=plain.usage_fetched_at,
                          updated_at=plain.updated_at)            # a state from before the guard: no such field
    assert build_model(old, NOW).guard is None                    # type: ignore[arg-type]
    by_hand = WidgetModel(status=ApiStatus.LIVE, status_text="LIVE", age_text=None, rows=[], stale=False,
                          stale_message=None, blink_on=True, updated_at=NOW)
    assert by_hand.guard is None                                  # callers and tests that predate it keep working


def test_a_guard_under_every_cap_is_armed_and_names_the_caps_it_watches():
    model = build_model(guarded(limit("session", 40), limit("weekly_all", 50)), NOW)
    mark = model.guard
    assert isinstance(mark, GuardMark) and mark.state == "armed" and not mark.holding
    assert mark.over_keys == () and mark.over_limits() == []
    assert mark.caps == {"session": 95, "weekly": 90}
    assert mark.watched() == [("session", 95), ("weekly", 90)]
    assert (mark.resume_at, mark.resume_text, mark.resume_in, mark.resume_clock) == (None, None, None, None)


def test_a_limit_without_a_cap_is_not_watched():
    model = build_model(guarded(limit("session", 40), limit("weekly_all", 50), policy={"session_cap": None}), NOW)
    assert model.guard.caps == {"weekly": 90}


def test_a_limit_at_its_cap_is_held_and_its_row_is_named_by_key():
    model = build_model(guarded(limit("session", 96, resets_in=timedelta(hours=2, minutes=14)),
                                limit("weekly_all", 50)), NOW)
    mark = model.guard
    assert mark.state == "holding" and mark.holding
    assert mark.over_keys == ("session",) == (model.rows[0].key,)
    assert mark.readings == {"session": 96.0} and mark.labels["session"] == "session"
    assert mark.over_limits() == [("session", 96.0, 95)]
    weekly = build_model(guarded(limit("session", 10), limit("weekly_all", 91)), NOW)
    assert weekly.guard.over_keys == ("weekly",) == (weekly.rows[1].key,)


def test_with_two_limits_over_their_caps_the_guard_resumes_when_the_last_of_them_resets():
    model = build_model(guarded(limit("session", 97, resets_in=timedelta(hours=1)),
                                limit("weekly_all", 92, resets_in=timedelta(days=1, hours=9))), NOW)
    mark = model.guard
    assert mark.over_keys == ("session", "weekly")
    assert mark.over_limits() == [("session", 97.0, 95), ("weekly", 92.0, 90)]
    assert mark.resume_at == NOW + timedelta(days=1, hours=9) and mark.resume_in == "1D 9H"


def test_a_scoped_limit_over_its_cap_is_keyed_as_its_row_is():
    model = build_model(guarded(limit("session", 10), limit("weekly_all", 10), limit("weekly_scoped", 92, **FABLE),
                                policy={"scoped_cap": 90}), NOW)
    mark = model.guard
    assert model.rows[2].key == "scoped:fable"
    assert mark.over_keys == ("scoped:fable",) == (model.rows[2].key,)
    assert mark.caps == {"session": 95, "weekly": 90, "scoped:fable": 90}
    assert mark.labels["scoped:fable"] == "Fable weekly" and mark.readings == {"scoped:fable": 92.0}


def test_a_scoped_limit_with_no_model_is_keyed_by_its_label_as_its_row_is():
    odd = limit("weekly_scoped", 95, label="OPUS WEEKLY", scope=None, allowance=50.0)
    model = build_model(guarded(limit("session", 10), limit("weekly_all", 10), odd, policy={"scoped_cap": 90}), NOW)
    assert model.rows[2].key == "scoped:opus weekly"
    assert model.guard.over_keys == (model.rows[2].key,)
    assert model.guard.labels[model.rows[2].key] == "opus weekly"


def test_a_scoped_limit_the_widget_hides_is_still_watched_and_named():
    held = guarded(limit("session", 10), limit("weekly_all", 10), limit("weekly_scoped", 92, **FABLE),
                   policy={"scoped_cap": 90})
    model = build_model(held, NOW, show_scoped="never")
    assert [row.key for row in model.rows] == ["session", "weekly"]
    assert model.guard.over_keys == ("scoped:fable",)
    assert model.guard.over_limits() == [("Fable weekly", 92.0, 90)]
    assert model.guard.watched()[-1] == ("Fable weekly", 90)


def test_the_guard_keys_line_up_with_the_row_keys_whatever_the_scoped_labels_are():
    """The guard names a scoped limit by its label and for people by "<model> weekly"; the widgets key a row by its
    model, else its label. Both ways of naming the same limit have to end on the same key."""
    for scope, label in (("Fable", "FABLE WEEKLY"), ("Sonnet 4.5", "CLAUDE SONNET 4.5 WEEKLY"), (None, "DESIGN WEEKLY")):
        scoped = limit("weekly_scoped", 99, label=label, scope=scope, allowance=25.0)
        model = build_model(guarded(limit("session", 1), limit("weekly_all", 1), scoped, policy={"scoped_cap": 90}), NOW)
        assert model.guard.over_keys == (model.rows[2].key,), (scope, label)


@pytest.mark.parametrize(("resets_in", "text"), [
    (timedelta(hours=23, minutes=59), "23H 59M"),
    (timedelta(hours=2, minutes=14), "2H 14M"),
    (timedelta(minutes=9), "9M"),
    (timedelta(seconds=30), "<1M"),
    (timedelta(days=1) - timedelta(seconds=1), "23H 59M"),
])
def test_the_resume_text_is_a_countdown_under_a_day(resets_in, text):
    mark = build_model(guarded(limit("session", 96, resets_in=resets_in), limit("weekly_all", 50)), NOW).guard
    assert mark.resume_text == text == mark.resume_in
    assert mark.resume_at == NOW + resets_in
    assert mark.resume_clock == format_resets(NOW + resets_in, NOW)


def test_the_resume_text_a_day_or_more_away_is_the_reset_time_the_rows_give():
    for resets_in, countdown in ((timedelta(days=2, hours=3), "2D 3H"), (timedelta(days=1), "1D")):
        mark = build_model(guarded(limit("session", 10), limit("weekly_all", 91, resets_in=resets_in)), NOW).guard
        assert mark.resume_text == format_resets(NOW + resets_in, NOW) == mark.resume_clock
        assert mark.resume_in == countdown


def test_a_limit_with_no_reset_time_holds_with_no_resume_time():
    mark = build_model(guarded(limit("session", 99, resets_in=None), limit("weekly_all", 10)), NOW).guard
    assert mark.holding and mark.over_keys == ("session",)
    assert (mark.resume_at, mark.resume_text, mark.resume_in, mark.resume_clock) == (None, None, None, None)


def test_a_resume_time_that_has_passed_since_the_reading_reads_as_under_a_minute():
    """The guard judges a reading once a cycle and the model is built every minute: in between, the countdown runs out."""
    held = guarded(limit("session", 96, resets_in=timedelta(minutes=5)), limit("weekly_all", 10))
    assert build_model(held, NOW + timedelta(minutes=5, seconds=40)).guard.resume_text == "<1M"
    assert build_model(held, NOW + timedelta(minutes=2)).guard.resume_text == "3M"


@pytest.mark.parametrize(("seconds", "text"), [
    (-5, "<1M"), (0, "<1M"), (30, "<1M"), (59, "<1M"), (60, "1M"), (540, "9M"), (3599, "59M"), (3600, "1H"),
    (3660, "1H 1M"), (8040, "2H 14M"), (86340, "23H 59M"), (86399, "23H 59M"), (86400, "1D"), (90000, "1D 1H"),
    (183600, "2D 3H"), (172800, "2D"),
])
def test_countdown_text(seconds, text):
    assert format_countdown(seconds) == text


def test_the_countdown_counts_down_and_the_frame_cache_sees_it():
    """The widgets keep a frame while the model is unchanged (ui/widgets.steady): a countdown that moved is a model that
    differs, so the frame is drawn again."""
    held = guarded(limit("session", 96, resets_in=timedelta(hours=2, minutes=14)), limit("weekly_all", 10))
    now = build_model(held, NOW)
    later = build_model(held, NOW + timedelta(minutes=1))
    assert (now.guard.resume_text, later.guard.resume_text) == ("2H 14M", "2H 13M")
    assert now != later and now == build_model(held, NOW)
    assert now != build_model(replace(held, guard={}), NOW)


def test_a_malformed_guard_block_is_read_as_far_as_it_makes_sense():
    block = {"state": "holding", "resume_at": "not a time", "policy": {"session_cap": "95", "weekly_cap": True},
             "over": [None, "session", 7, {"name": "session", "label": "session", "percent": "96", "cap": 95,
                                           "resets_at": "no"}, {"name": "session"}, {}]}
    mark = build_model(replace(state(limit("session", 96), limit("weekly_all", 10)), guard=block), NOW).guard
    assert mark.holding and mark.over_keys == ("session",)
    assert mark.caps == {"session": 95} and mark.readings == {}
    assert mark.resume_at is None and mark.resume_text is None


def test_a_guard_mark_is_plain_frozen_data():
    mark = build_model(guarded(limit("session", 96), limit("weekly_all", 10)), NOW).guard
    with pytest.raises(dataclasses.FrozenInstanceError):
        mark.state = "armed"                                      # type: ignore[misc]
    assert GuardMark("armed") == GuardMark("armed", (), {}, None, None)


# ── Engine ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("text", "rgba"), [
    ("#abc", (0xAA, 0xBB, 0xCC, 255)),
    ("#112233", (0x11, 0x22, 0x33, 255)),
    ("#11223344", (0x11, 0x22, 0x33, 0x44)),
    ("#e8a02040", (0xE8, 0xA0, 0x20, 0x40)),
])
def test_parse_color_forms(text, rgba):
    assert engine.parse_color(text) == rgba


@pytest.mark.parametrize("text", ["abc", "#12345", "#zzzzzz", "#1234567", ""])
def test_parse_color_rejects_bad_input(text):
    with pytest.raises(ValueError):
        engine.parse_color(text)


def test_canvas_output_size_follows_scale():
    assert engine.Canvas(280, 126, scale=1.0).finish().size == (280, 126)
    assert engine.Canvas(280, 126, scale=2.0).finish().size == (560, 252)
    assert engine.Canvas(10, 4, scale=1.25).finish().size == (12, 5)


def test_canvas_rectangles_are_exact_before_downsampling():
    # Checked on the 2x buffer: LANCZOS rings at edges, so the exact geometry is read before it.
    canvas = engine.Canvas(10, 4, scale=1.0)
    canvas.fill_rect(2, 1, 5, 3, (255, 0, 0, 255))
    buffer = canvas.image
    assert buffer.getpixel((4, 2)) == (255, 0, 0, 255)
    assert buffer.getpixel((9, 5)) == (255, 0, 0, 255)
    assert buffer.getpixel((10, 5)) == (0, 0, 0, 0)
    assert buffer.getpixel((3, 2)) == (0, 0, 0, 0)


def test_polygon_far_edges_are_exclusive():
    canvas = engine.Canvas(10, 10, scale=1.0)
    canvas.fill_polygon([(2, 2), (6, 2), (6, 6), (2, 6)], (0, 255, 0, 255))
    buffer = canvas.image
    assert buffer.getpixel((4, 4)) == (0, 255, 0, 255)
    assert buffer.getpixel((11, 11)) == (0, 255, 0, 255)
    assert buffer.getpixel((12, 11))[3] == 0
    assert buffer.getpixel((11, 12))[3] == 0


def test_translucent_fill_keeps_straight_alpha():
    canvas = engine.Canvas(4, 4, scale=1.0)
    canvas.fill_rect(0, 0, 4, 4, (255, 255, 255, 128))
    pixel = canvas.finish().getpixel((1, 1))
    assert pixel[:3] == (255, 255, 255)           # colour not darkened by the transparent surround
    assert pixel[3] == 128


def test_dotted_rail_pattern():
    canvas = engine.Canvas(16, 2, scale=1.0)
    canvas.dotted_hline(0, 16, 0, 2, 2, (255, 255, 255, 255))
    row = [canvas.image.getpixel((x, 0))[3] for x in range(32)]
    # 2 px on and 2 px off in logical pixels: 4 buffer px lit, 4 dark, repeating.
    assert row[0:4] == [255] * 4
    assert row[4:8] == [0] * 4
    assert row[8:12] == [255] * 4


def test_rounded_rect_zero_radius_is_plain_rectangle():
    canvas = engine.Canvas(8, 8, scale=1.0)
    layer = canvas.rounded_rect_layer(0, 0, 8, 8, 0)
    assert layer.mask.getextrema() == (255, 255)


def test_split_segments_fills_cells_from_the_left():
    assert engine.split_segments(None, 16) == (0, 0.0)
    assert engine.split_segments(0.0, 16) == (0, 0.0)
    assert engine.split_segments(100.0, 16) == (16, 0.0)
    full, fraction = engine.split_segments(13.0, 16)
    assert full == 2
    assert fraction == pytest.approx(0.08, abs=0.001)
    assert engine.split_segments(150.0, 16) == (16, 0.0)


def test_chamfer_inset_matches_the_css_value():
    body = engine.inset_chamfer(0, 0, 280, 126, 10, 10, 1)
    assert body[0][0] == pytest.approx(10.414, abs=0.01)
    assert body[5][1] == pytest.approx(10.414, abs=0.01)


def test_bgra_byte_helpers_order_and_premultiplication():
    pixel = Image.new("RGBA", (1, 1), (255, 0, 0, 128))
    assert engine.to_bgra_straight(pixel) == bytes((0, 0, 255, 128))
    assert engine.to_bgra_premultiplied(pixel) == bytes((0, 0, 128, 128))
    frame = Image.new("RGBA", (5, 3), (10, 20, 30, 40))
    assert len(engine.to_bgra_straight(frame)) == 5 * 3 * 4
    assert len(engine.to_bgra_premultiplied(frame)) == 5 * 3 * 4


def test_font_cache_returns_the_same_face_for_the_same_key():
    path = str(engine.font_file("vt323/VT323-Regular.ttf"))
    assert engine.load_font(path, 22.0) is engine.load_font(path, 22.0)


def test_bundled_fonts_are_present():
    for relative in ("vt323/VT323-Regular.ttf", "ibmplexmono/IBMPlexMono-SemiBold.ttf",
                     "ibmplexmono/IBMPlexMono-Regular.ttf"):
        assert engine.font_file(relative).is_file(), relative


def test_vignette_darkens_corners_not_centre():
    canvas = engine.Canvas(40, 40, scale=1.0)
    canvas.fill_rect(0, 0, 40, 40, (200, 200, 200, 255))
    canvas.vignette(1.0)
    image = canvas.finish()
    assert image.getpixel((20, 20))[0] > image.getpixel((1, 1))[0]


def test_hazard_stripes_stay_inside_the_polygon():
    canvas = engine.Canvas(40, 10, scale=1.0)
    outline = engine.chamfer_polygon(0, 0, 40, 10, 4, 4)
    canvas.hazard(outline, 0, 40, 0, 4, 4, (255, 179, 0, 255), (9, 9, 9, 255))
    buffer = canvas.image
    assert buffer.getpixel((0, 0))[3] == 0       # the chamfer corner stays clear
    assert buffer.getpixel((60, 2))[3] == 255    # the band is opaque inside the outline
    assert buffer.getpixel((60, 12))[3] == 0     # and stops at its 4 px height


def test_glue_makes_every_space_unbreakable():
    glued = engine.glue("FRI 1:12 AM")
    assert " " not in glued and glued.count(engine.NO_BREAK) == 2


def test_wrap_words_breaks_at_plain_spaces_up_to_the_limit():
    assert engine.wrap_words("aaa bbb ccc ddd", len, 7) == ["aaa bbb", "ccc ddd"]
    assert engine.wrap_words("aaa bbb", len, 99) == ["aaa bbb"]
    assert engine.wrap_words("", len, 7) == []
    assert engine.wrap_words("  aaa   bbb ", len, 7) == ["aaa bbb"]


def test_wrap_words_keeps_a_glued_group_together_and_gives_plain_spaces_back():
    text = f"aaa {engine.glue('bbb ccc')} ddd"
    assert engine.wrap_words(text, len, 8) == ["aaa", "bbb ccc", "ddd"]
    assert engine.wrap_words(text, len, 99) == ["aaa bbb ccc ddd"]


def test_wrap_words_gives_the_first_line_its_own_limit_and_never_splits_a_word():
    assert engine.wrap_words("aaa bbb ccc", len, 7, first_limit=3) == ["aaa", "bbb ccc"]
    assert engine.wrap_words("aaaaaaaaa bbb", len, 4) == ["aaaaaaaaa", "bbb"]


def test_wrap_words_starts_a_first_word_too_wide_for_the_room_beside_the_lead_on_the_next_line():
    assert engine.wrap_words("aaaaa bbb", len, 10, first_limit=3) == ["", "aaaaa bbb"]   # "FABLE WEEKLY" after a long lead
    assert engine.wrap_words("aaa bbb", len, 10, first_limit=3) == ["aaa", "bbb"]


# ── Options ───────────────────────────────────────────────────────────────────

def test_coerce_options_accepts_none_mapping_and_object():
    assert coerce_options(None) == RenderOptions()
    assert coerce_options({"text_fx": False}).text_fx is False
    custom = RenderOptions(transparent_bg=True)
    assert coerce_options(custom) is custom


def test_coerce_options_rejects_unknown_names():
    with pytest.raises(ValueError):
        coerce_options({"glow": True})
    with pytest.raises(TypeError):
        coerce_options(42)  # type: ignore[arg-type]


# ── Skin registry ─────────────────────────────────────────────────────────────

def test_get_skin_returns_each_theme_skin_and_falls_back_to_nostromo():
    import importlib.util

    from usage_monitor.render import skins

    assert isinstance(get_skin("nostromo"), NostromoSkin)
    for theme_id, (module_name, class_name) in skins.SKIN_CLASSES.items():
        skin = get_skin(theme_id)
        if importlib.util.find_spec(f"usage_monitor.render.skins.{module_name}") is None:
            assert isinstance(skin, NostromoSkin)   # not built yet: renders through Nostromo
        else:
            assert type(skin).__name__ == class_name
    assert isinstance(get_skin(None), NostromoSkin)
    assert isinstance(get_skin("no-such-theme"), NostromoSkin)


# ── Palette and tokens ────────────────────────────────────────────────────────

@pytest.mark.parametrize("variant_id", ["amber", "green", "white", "red", "blue", "cyan", "orange", "purple"])
def test_palette_tokens_come_from_the_variant(variant_id):
    theme = nostromo()
    variant = theme.variant(variant_id)
    palette = Palette.for_variant(theme, variant)
    colors = variant.colors
    assert palette.crt == engine.parse_color(colors["bg"])
    assert palette.hot == engine.parse_color(colors["text_bright"])
    assert palette.main == engine.parse_color(colors["text"])
    assert palette.bright == engine.parse_color(colors["accent"])
    assert palette.label == engine.parse_color(colors["text_dim"])
    assert palette.ghost == engine.parse_color(colors["track"])
    assert palette.rule == engine.parse_color(colors["border_dim"])
    assert palette.glow == engine.parse_color(colors["glow"])
    assert palette.glow_soft == engine.parse_color(theme.extras["variant_tokens"][variant_id]["glow_soft"])


def test_stale_palette_drops_tones_and_halos():
    palette = Palette.for_variant(nostromo(), nostromo().variant("amber"))
    stale = palette.stale_tones()
    assert stale.main == stale.bright == stale.hot == palette.stale
    assert stale.glow[3] == 0 and stale.glow_soft[3] == 0
    assert stale.message == palette.message        # the auth box keeps its label tone


def test_variant_tokens_are_read_when_variant_has_no_extras():
    theme = nostromo()
    bare = replace(theme.variant("amber"), extras={})
    palette = Palette.for_variant(theme, bare)
    assert palette.ink == engine.parse_color(theme.extras["variant_tokens"]["amber"]["ink"])


# ── Floating widget: every variant x state x scale ───────────────────────────

@pytest.mark.parametrize("variant_id", ["amber", "green", "white", "red", "blue", "cyan", "orange", "purple"])
def test_every_variant_renders_every_state_at_every_scale(variant_id):
    theme = nostromo()
    variant = theme.variant(variant_id)
    skin = get_skin("nostromo")
    for name in ALL_STATES:
        model = dump.sample_model(name)
        for scale in SCALES:
            image = skin.render_floating(model, theme, variant, scale, None)
            height = FLOAT_HEIGHTS[name]
            assert image.mode == "RGBA"
            assert image.size == (round(280 * scale), round(height * scale)), (name, scale)
            low, high = alpha_extrema(image)
            assert low == 0 and high == 255, (name, scale)     # real alpha: clear corners, solid body


def test_floating_size_matches_rendered_size():
    skin = get_skin("nostromo")
    for name in ALL_STATES:
        model = dump.sample_model(name)
        for scale in SCALES:
            image = skin.render_floating(model, nostromo(), nostromo().variant("amber"), scale, None)
            assert skin.floating_size(nostromo(), model, scale) == image.size


def test_frame_height_is_94_for_two_rows_and_126_for_three():
    model_two = build_model(state(limit("session", 10), limit("weekly_all", 5)), NOW)
    model_three = build_model(state(limit("session", 10), limit("weekly_all", 5), scoped(3.0, active=True)), NOW)
    skin = get_skin("nostromo")
    assert skin.floating_size(nostromo(), model_two, 1.0) == (280, 94)
    assert skin.floating_size(nostromo(), model_three, 1.0) == (280, 126)


def test_transparent_background_removes_the_plate():
    theme = nostromo()
    variant = theme.variant("amber")
    skin = get_skin("nostromo")
    model = dump.sample_model("normal3")
    solid = skin.render_floating(model, theme, variant, 1.0, None)
    clear = skin.render_floating(model, theme, variant, 1.0, {"transparent_bg": True})
    solid_coverage = sum(1 for a in solid.getchannel("A").getdata() if a > 128)
    clear_coverage = sum(1 for a in clear.getchannel("A").getdata() if a > 128)
    assert clear_coverage < solid_coverage * 0.5
    assert clear.getpixel((0, 0))[3] == 0            # corners stay clear either way


def test_text_fx_off_removes_scanlines_from_the_plate():
    theme = nostromo()
    variant = theme.variant("amber")
    skin = get_skin("nostromo")
    model = dump.sample_model("stale")      # stale has no halos, so only the scanlines differ
    on = skin.render_floating(model, theme, variant, 1.0, {"text_fx": True})
    off = skin.render_floating(model, theme, variant, 1.0, {"text_fx": False})
    # The box x 150..230, y 62..74 holds no content. One row in three is a scanline row, which
    # darkens to 70 %, so the box averages about 0.9 of its unscanned brightness.
    box = (150, 62, 230, 74)
    mean_on = sum(on.convert("L").crop(box).getdata()) / ((box[2] - box[0]) * (box[3] - box[1]))
    mean_off = sum(off.convert("L").crop(box).getdata()) / ((box[2] - box[0]) * (box[3] - box[1]))
    assert mean_on < mean_off
    assert 0.85 <= mean_on / mean_off <= 0.95


def test_critical_state_draws_the_hazard_band_and_others_do_not():
    theme = nostromo()
    variant = theme.variant("amber")
    skin = get_skin("nostromo")

    def bright_in_band(image: Image.Image) -> int:
        count = 0
        for y in range(4):
            for x in range(image.width):
                r, g, b, a = image.getpixel((x, y))
                if a > 200 and r > 200 and g > 140 and b < 60:
                    count += 1
        return count

    assert bright_in_band(skin.render_floating(dump.sample_model("critical"), theme, variant, 1.0, None)) > 20
    assert bright_in_band(skin.render_floating(dump.sample_model("normal3"), theme, variant, 1.0, None)) == 0


def _rgb_at(image: Image.Image, x: float, y: float, scale: float) -> tuple[int, int, int]:
    """The colour at a logical coordinate of a frame rendered at ``scale`` (a cell interior is flat)."""
    return image.getpixel((round(x * scale), round(y * scale)))[:3]


def _near(actual: tuple[int, int, int], expected: tuple[int, ...]) -> bool:
    return all(abs(a - e) <= 2 for a, e in zip(actual, expected[:3]))


@pytest.mark.parametrize(("severity", "percent", "emphasis"), [
    (SEVERITY_NORMAL, 52.0, "main"),
    (SEVERITY_WARNING, 80.0, "bright"),
    (SEVERITY_CRITICAL, 90.0, "hot"),
])
def test_a_part_lit_pip_stays_main_while_the_full_pips_take_the_severity_colour(severity, percent, emphasis):
    """In the approved CSS only a full cell (.on) turns --bright for a warning and --hot for a critical
    limit; a part-lit cell (.part) is always --main. The skin used to light both in the severity colour,
    so the boundary cell looked one cell too bright."""
    theme = nostromo()
    palette = Palette.for_variant(theme, theme.variant("amber"))
    skin = get_skin("nostromo")
    model = build_model(state(limit("session", percent, severity=severity), limit("weekly_all", 10.0)), NOW)
    scale = 2.0
    image = skin.render_floating(model, theme, theme.variant("amber"), scale, {"text_fx": False})

    full, fraction = engine.split_segments(percent, 16)
    partial_w = round(fraction * 12)
    assert full < 16 and partial_w >= 4                      # the sample row really has a part-lit cell
    y = 28 + 21 + 4                                           # row 1, vertical middle of the 8 px pips
    full_cell = _rgb_at(image, 14 + 16 * (full - 1) + 6, y, scale)
    partial_cell = _rgb_at(image, 14 + 16 * full + partial_w / 2, y, scale)
    ghost_cell = _rgb_at(image, 14 + 16 * 15 + 6, y, scale)
    assert _near(full_cell, getattr(palette, emphasis)), (full_cell, getattr(palette, emphasis))
    assert _near(partial_cell, palette.main), (partial_cell, palette.main)
    assert _near(ghost_cell, palette.ghost)


@pytest.mark.parametrize(("severity", "emphasis"), [(SEVERITY_WARNING, "bright"), (SEVERITY_CRITICAL, "hot")])
def test_a_part_lit_strip_cell_stays_main_too(severity, emphasis):
    theme = nostromo()
    palette = Palette.for_variant(theme, theme.variant("amber"))
    model = build_model(state(limit("session", 95.0, severity=severity), limit("weekly_all", 10.0)), NOW)
    scale = 2.0
    image = get_skin("nostromo").render_taskbar(model, theme, theme.variant("amber"), round(34 * scale), scale,
                                                {"text_fx": False})
    full, fraction = engine.split_segments(95.0, 10)         # 9 full cells and half of the tenth
    partial_w = round(fraction * 4)
    assert (full, partial_w) == (9, 2)
    y = 13 + 4
    assert _near(_rgb_at(image, 44 + 6 * 8 + 2, y, scale), getattr(palette, emphasis))
    assert _near(_rgb_at(image, 44 + 6 * 9 + 1, y, scale), palette.main)


def test_stale_and_live_render_differently():
    theme = nostromo()
    variant = theme.variant("amber")
    skin = get_skin("nostromo")
    live = skin.render_floating(dump.sample_model("normal2"), theme, variant, 1.0, None)
    stale = skin.render_floating(dump.sample_model("stale"), theme, variant, 1.0, None)
    assert live.tobytes() != stale.tobytes()


def _auth_box_variants() -> tuple[Any, Any, Any]:
    """The expired-sign-in model, the same box text arriving through a status the skin does not word
    (so the model's text is drawn as given), and that status with the neutral expired text."""
    stale = dump.sample_model("stale")
    spelled = replace(stale, status=ApiStatus.NO_CREDENTIALS, stale_message="\n".join(NOSTROMO_AUTH_LINES))
    neutral = replace(stale, status=ApiStatus.NO_CREDENTIALS, stale_message=STALE_MESSAGE_EXPIRED)
    return stale, spelled, neutral


def _box(image: Image.Image, top: float, scale: float) -> bytes:
    n = nostromo_skin
    return image.crop((round(n.LEFT * scale), round(top * scale), round(n.RIGHT * scale),
                       round((top + n.MESSAGE_H) * scale))).tobytes()


def test_the_floating_auth_box_draws_the_mockup_wording_not_the_neutral_one():
    theme = nostromo()
    variant = theme.variant("amber")
    skin = get_skin("nostromo")
    stale, spelled, neutral = _auth_box_variants()
    box = {name: _box(skin.render_floating(m, theme, variant, 2.0, None), nostromo_skin.MESSAGE_TOP, 2.0)
           for name, m in (("stale", stale), ("spelled", spelled), ("neutral", neutral))}
    assert box["stale"] == box["spelled"]
    assert box["stale"] != box["neutral"]


def test_the_tooltip_auth_box_draws_the_mockup_wording_not_the_neutral_one():
    theme = nostromo()
    variant = theme.variant("amber")
    skin = get_skin("nostromo")
    stale, spelled, neutral = _auth_box_variants()
    n = nostromo_skin
    top = n.ROW_TOP + n.TOOLTIP_ROW_PITCH * (len(stale.rows) - 1) + n.TOOLTIP_INFO_OFFSET + 20
    box = {name: _box(skin.render_tooltip(m, theme, variant, 2.0), top, 2.0)
           for name, m in (("stale", stale), ("spelled", spelled), ("neutral", neutral))}
    assert box["stale"] == box["spelled"]
    assert box["stale"] != box["neutral"]


def test_the_auth_box_fits_its_two_lines_at_every_scale():
    """The mockup's second line is the longer one; it has to stay inside the dashed box."""
    theme = nostromo()
    variant = theme.variant("amber")
    skin = get_skin("nostromo")
    n = nostromo_skin
    for scale in SCALES:
        image = skin.render_floating(dump.sample_model("stale"), theme, variant, scale, {"text_fx": False})
        pal = Palette.for_variant(theme, variant).stale_tones()
        right_inside = round((n.RIGHT - 3) * scale)
        for y in range(round((n.MESSAGE_TOP + 3) * scale), round((n.MESSAGE_TOP + n.MESSAGE_H - 3) * scale)):
            r, g, b, a = image.getpixel((right_inside, y))
            assert _near((r, g, b), pal.crt), (scale, y, (r, g, b))      # no text reaches the right edge


def test_reduce_motion_keeps_the_alive_mark_lit():
    theme = nostromo()
    variant = theme.variant("amber")
    skin = get_skin("nostromo")
    dim = build_model(dump.sample_state("normal2"), NOW, blink_on=False)
    lit_still = skin.render_floating(dim, theme, variant, 1.0, {"reduce_motion": True})
    blink_off = skin.render_floating(dim, theme, variant, 1.0, {"reduce_motion": False})
    assert lit_still.tobytes() != blink_off.tobytes()


def test_rendering_is_deterministic():
    theme = nostromo()
    variant = theme.variant("red")
    model = dump.sample_model("warning")
    first = NostromoSkin().render_floating(model, theme, variant, 1.25, None)
    second = NostromoSkin().render_floating(model, theme, variant, 1.25, None)
    assert first.tobytes() == second.tobytes()


def test_floating_render_is_fast_enough():
    theme = nostromo()
    variant = theme.variant("amber")
    skin = get_skin("nostromo")
    model = dump.sample_model("normal3")
    skin.render_floating(model, theme, variant, 1.0, None)        # warm the font cache
    started = time.perf_counter()
    for _ in range(5):
        skin.render_floating(model, theme, variant, 1.0, None)
    per_frame_ms = (time.perf_counter() - started) / 5 * 1000
    assert per_frame_ms < 150, f"floating frame took {per_frame_ms:.1f} ms"


# ── Scoped row labels: the name line stays clear of the value ─────────────────

# A scoped limit is named for its model and WEEKLY, and qualified by its share. These realistic names used to run
# under the percentage ("CLAUDE DESIGN WEEKLY OF 25% SHARE" put SHARE under the 33 %).
STRESS_LABELS = [("CLAUDE DESIGN WEEKLY", 25.0), ("CLAUDE OPUS 4.8 WEEKLY", 100.0), ("CLAUDE SONNET 4.5 WEEKLY", 25.0)]


def scoped_model(label: str, allowance: float, percent: float = 33.0, session: float = 13.0):
    return build_model(state(
        limit("session", session), limit("weekly_all", 2.0),
        limit("weekly_scoped", percent, label=label, active=True, scope=label.split()[1].lower(),
              allowance=allowance, resets_in=timedelta(days=3)),
    ), NOW)


@pytest.fixture
def text_runs(monkeypatch):
    """Every text run the canvas is asked for, as dicts of text, x, baseline, advance and align (logical px)."""
    runs: list[dict[str, Any]] = []
    original = engine.Canvas.text_layer

    def spy(self, text, x, baseline, font, tracking=0.0, align="left"):
        layer, advance = original(self, text, x, baseline, font, tracking, align)
        runs.append({"text": text, "x": x, "baseline": baseline, "advance": advance, "align": align})
        return layer, advance

    monkeypatch.setattr(engine.Canvas, "text_layer", spy)
    return runs


def scoped_row_runs(runs: list[dict[str, Any]], pitch: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """(the name line's runs, the value run) of the third row."""
    top = nostromo_skin.ROW_TOP + pitch * 2
    line = [r for r in runs if r["align"] == "left" and top < r["baseline"] <= top + 12]
    values = [r for r in runs if r["align"] == "right" and top - 4 < r["baseline"] <= top + 18
              and r["text"].endswith("%") and r["text"][0].isdigit()]
    assert len(values) == 1, values
    return line, values[0]


@pytest.mark.parametrize(("label", "allowance"), STRESS_LABELS)
@pytest.mark.parametrize("surface", ["floating", "tooltip"])
def test_a_long_scoped_label_ends_before_its_value(label, allowance, surface, text_runs):
    theme, skin = nostromo(), get_skin("nostromo")
    model = scoped_model(label, allowance)
    if surface == "floating":
        skin.render_floating(model, theme, theme.variant("amber"), 1.0, None)
        pitch = nostromo_skin.ROW_PITCH
    else:
        skin.render_tooltip(model, theme, theme.variant("amber"), 1.0)
        pitch = nostromo_skin.TOOLTIP_ROW_PITCH
    line, value = scoped_row_runs(text_runs, pitch)
    assert line and line[0]["text"].startswith("CLAUDE"), line
    value_left = value["x"] - value["advance"]
    end = max(r["x"] + r["advance"] for r in line)
    assert end <= value_left - nostromo_skin.VALUE_CLEARANCE + 1e-6, (label, surface, end, value_left)
    assert line[0]["x"] == nostromo_skin.LEFT


@pytest.mark.parametrize(("label", "allowance"), STRESS_LABELS)
def test_a_critical_value_chip_keeps_the_name_line_clear_too(label, allowance, text_runs):
    theme = nostromo()
    get_skin("nostromo").render_floating(scoped_model(label, allowance, percent=96.0), theme,
                                         theme.variant("amber"), 1.0, None)
    line, value = scoped_row_runs(text_runs, nostromo_skin.ROW_PITCH)
    chip_left = value["x"] - value["advance"] - nostromo_skin.VALUE_CHIP_PAD / 2
    assert max(r["x"] + r["advance"] for r in line) <= chip_left - nostromo_skin.VALUE_CLEARANCE + 1e-6


def test_a_scoped_label_that_fits_is_drawn_whole(text_runs):
    theme = nostromo()
    get_skin("nostromo").render_floating(scoped_model("FABLE WEEKLY", 50.0, percent=4.0), theme,
                                         theme.variant("amber"), 1.0, None)
    line, _ = scoped_row_runs(text_runs, nostromo_skin.ROW_PITCH)
    assert [r["text"] for r in line] == ["FABLE WEEKLY", "OF 50% SHARE"]
    assert line[1]["x"] == pytest.approx(nostromo_skin.LEFT + line[0]["advance"] + nostromo_skin.QUALIFIER_GAP)


@pytest.mark.parametrize(("label", "allowance", "expected"), [
    ("FABLE WEEKLY", 50.0, ("FABLE WEEKLY", "OF 50% SHARE")),                         # whole
    ("CLAUDE DESIGN WEEKLY", 25.0, ("CLAUDE DESIGN WEEKLY", "25%")),                   # the qualifier shrinks to the share
    ("CLAUDE SONNET 4.5 HAIKU WEEKLY", 25.0, ("CLAUDE SONNET 4.5 HAIKU", "25%")),      # then the name drops WEEKLY
])
def test_the_name_line_gives_way_in_the_order_night_city_does(label, allowance, expected):
    theme = nostromo()
    cv = nostromo_skin._canvas(nostromo_skin.FLOAT_W, 126, 1.0, theme)
    fonts = (cv.font(nostromo_skin.FONT_SEMI, nostromo_skin.LABEL_SIZE), cv.font(nostromo_skin.FONT_REG, nostromo_skin.LABEL_SIZE),
             cv.font(nostromo_skin.FONT_VT, nostromo_skin.VALUE_SIZE))
    row = scoped_model(label, allowance).rows[2]
    assert nostromo_skin._name_line(cv, row, "33%", *fonts) == expected


def test_a_name_too_long_for_any_form_is_cut_with_an_ellipsis():
    theme = nostromo()
    cv = nostromo_skin._canvas(nostromo_skin.FLOAT_W, 126, 1.0, theme)
    semi, reg, vt = (cv.font(nostromo_skin.FONT_SEMI, nostromo_skin.LABEL_SIZE), cv.font(nostromo_skin.FONT_REG, nostromo_skin.LABEL_SIZE),
                     cv.font(nostromo_skin.FONT_VT, nostromo_skin.VALUE_SIZE))
    row = scoped_model("A VERY LONG MODEL NAME THAT KEEPS GOING FOR A WHILE WEEKLY", 25.0).rows[2]
    name, qualifier = nostromo_skin._name_line(cv, row, "33%", semi, reg, vt)
    assert name.endswith("\u2026") and name.startswith("A VERY LONG") and qualifier == "25%"
    room = nostromo_skin.RIGHT - cv.advance("33%", vt) - nostromo_skin.VALUE_CLEARANCE - nostromo_skin.LEFT
    used = cv.advance(name, semi, nostromo_skin.LABEL_TRACKING) + nostromo_skin.QUALIFIER_GAP + cv.advance(
        qualifier, reg, nostromo_skin.QUALIFIER_TRACKING)
    assert used <= room


def test_the_session_and_weekly_rows_keep_their_fixed_qualifiers():
    theme = nostromo()
    cv = nostromo_skin._canvas(nostromo_skin.FLOAT_W, 126, 1.0, theme)
    semi, reg, vt = (cv.font(nostromo_skin.FONT_SEMI, nostromo_skin.LABEL_SIZE), cv.font(nostromo_skin.FONT_REG, nostromo_skin.LABEL_SIZE),
                     cv.font(nostromo_skin.FONT_VT, nostromo_skin.VALUE_SIZE))
    rows = scoped_model("FABLE WEEKLY", 50.0).rows
    assert nostromo_skin._name_line(cv, rows[0], "13%", semi, reg, vt) == ("SESSION", "5H")
    assert nostromo_skin._name_line(cv, rows[1], "2%", semi, reg, vt) == ("WEEKLY", "7D")


# ── Taskbar strip ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("height", "scale", "size"), [
    (48, 1.0, (264, 48)),
    (40, 1.0, (264, 40)),
    (72, 1.5, (396, 72)),
    (96, 2.0, (528, 96)),
])
def test_taskbar_strip_size_and_placement(height, scale, size):
    theme = nostromo()
    image = get_skin("nostromo").render_taskbar(dump.sample_model("normal3"), theme, theme.variant("amber"),
                                                height, scale, None)
    assert image.size == size
    assert alpha_extrema(image) == (0, 255)
    assert image.getchannel("A").crop((0, 0, size[0], 1)).getextrema() == (0, 0)   # strip is centred


@pytest.mark.parametrize(("height", "scale"), [
    (28, 1.0), (30, 1.0), (32, 1.0), (33, 1.0), (30, 1.25), (34, 1.25), (38, 1.25), (50, 1.5), (60, 2.0), (20, 1.0),
])
def test_the_strip_is_drawn_smaller_for_a_taskbar_shorter_than_it(height, scale):
    """The 34 px strip (42 px at 125 %) used to be returned as it was, taller than the bar: the shell centres it,
    so it overhung the bar or was cut by the screen edge. The frame is now exactly as tall as the bar."""
    theme = nostromo()
    image = get_skin("nostromo").render_taskbar(dump.sample_model("normal3"), theme, theme.variant("amber"),
                                                height, scale, None)
    assert image.height == height
    assert image.width <= round(264 * scale)
    assert alpha_extrema(image)[1] == 255
    # the whole strip is there, not a crop of a bigger one: the top left and bottom right corners are chamfered
    # away and the other two are square and solid
    last_x, last_y = image.width - 1, height - 1
    assert [image.getpixel(p)[3] for p in ((0, 0), (last_x, last_y), (0, last_y), (last_x, 0))] == [0, 0, 255, 255]


@pytest.mark.parametrize(("height", "scale"), [(34, 1.0), (40, 1.0), (48, 1.0), (42, 1.25), (51, 1.5), (68, 2.0), (80, 2.0)])
def test_the_strip_keeps_its_size_on_a_taskbar_as_tall_as_it_or_taller(height, scale):
    theme = nostromo()
    image = get_skin("nostromo").render_taskbar(dump.sample_model("normal3"), theme, theme.variant("amber"),
                                                height, scale, None)
    assert image.size == (round(264 * scale), height)


@pytest.mark.parametrize("name", ALL_STATES)
def test_taskbar_strip_renders_every_state(name):
    theme = nostromo()
    image = get_skin("nostromo").render_taskbar(dump.sample_model(name), theme, theme.variant("cyan"), 34, 1.0,
                                                None)
    assert image.size == (264, 34)
    assert alpha_extrema(image)[1] == 255


# ── Tooltip ───────────────────────────────────────────────────────────────────

def test_tooltip_sizes_follow_row_count_and_message():
    theme = nostromo()
    skin = get_skin("nostromo")
    variant = theme.variant("amber")
    assert skin.render_tooltip(dump.sample_model("normal3"), theme, variant, 1.0).size == (280, 184)
    assert skin.render_tooltip(dump.sample_model("normal2"), theme, variant, 1.0).size == (280, 134)
    assert skin.render_tooltip(dump.sample_model("stale"), theme, variant, 1.0).size == (280, 170)
    assert skin.render_tooltip(dump.sample_model("loading"), theme, variant, 2.0).size == (560, 268)


def test_tooltip_renders_every_state_and_variant():
    theme = nostromo()
    skin = get_skin("nostromo")
    for variant in theme.variants:
        for name in ALL_STATES:
            image = skin.render_tooltip(dump.sample_model(name), theme, variant, 1.0)
            assert image.mode == "RGBA" and alpha_extrema(image) == (0, 255)


# ── Tray icon ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("size", [16, 20, 24, 32])
def test_tray_icon_size_and_plate(size):
    theme = nostromo()
    image = get_skin("nostromo").render_tray_icon(dump.sample_model("normal3"), theme, theme.variant("amber"), size)
    assert image.size == (size, size)
    assert image.getpixel((0, 0))[3] == 0          # chamfered corner is clear
    assert image.getpixel((size // 2, 1))[3] == 255  # plate edge is opaque


@pytest.mark.parametrize("size", [16, 20, 24])
def test_small_tray_icons_have_no_anti_aliasing(size):
    theme = nostromo()
    for name in ("normal3", "warning", "critical", "stale"):
        image = get_skin("nostromo").render_tray_icon(dump.sample_model(name), theme, theme.variant("amber"), size)
        colours = {pixel for pixel in image.getdata()}
        assert len(colours) <= 6, (name, size, len(colours))


def test_tray_icon_centre_marks_match_the_approved_geometry():
    theme = nostromo()
    skin = get_skin("nostromo")
    palette = Palette.for_variant(theme, theme.variant("amber"))
    image = skin.render_tray_icon(dump.sample_model("normal3"), theme, theme.variant("amber"), 16)
    for x in (7, 8):
        for y in (7, 8):
            assert image.getpixel((x, y)) == palette.main
    assert image.getpixel((6, 6)) != palette.main or image.getpixel((9, 9)) != palette.main  # arc, not solid


def test_tray_stale_icon_has_no_centre_and_no_solid_arc():
    theme = nostromo()
    palette = Palette.for_variant(theme, theme.variant("amber"))
    image = get_skin("nostromo").render_tray_icon(dump.sample_model("stale"), theme, theme.variant("amber"), 24)
    assert palette.main not in set(image.getdata())
    assert palette.stale in set(image.getdata())


def test_tray_colour_follows_severity():
    theme = nostromo()
    palette = Palette.for_variant(theme, theme.variant("amber"))
    skin = get_skin("nostromo")
    warning = skin.render_tray_icon(dump.sample_model("warning"), theme, theme.variant("amber"), 24)
    critical = skin.render_tray_icon(dump.sample_model("critical"), theme, theme.variant("amber"), 24)
    assert palette.bright in set(warning.getdata())
    assert palette.hot in set(critical.getdata())


def test_tray_icon_rejects_tiny_sizes():
    theme = nostromo()
    with pytest.raises(ValueError):
        get_skin("nostromo").render_tray_icon(dump.sample_model("normal3"), theme, theme.variant("amber"), 4)


def test_tray_icon_is_deterministic():
    theme = nostromo()
    skin = NostromoSkin()
    first = skin.render_tray_icon(dump.sample_model("warning"), theme, theme.variant("amber"), 32)
    second = skin.render_tray_icon(dump.sample_model("warning"), theme, theme.variant("amber"), 32)
    assert first.tobytes() == second.tobytes()


# ── Dump helper ───────────────────────────────────────────────────────────────

def test_shipped_nostromo_theme_has_eight_variants_and_the_expected_fonts():
    theme = nostromo()
    assert theme.name == "Nostromo"
    assert len(theme.variants) == 8
    assert theme.variant(None).id == "amber"
    assert theme.variant("amber").colors["bg"] == "#090909"
    assert theme.fonts["body"]["family"] == "IBM Plex Mono"
    assert theme.fonts["mono"]["family"] == "VT323"


def test_theme_objects_include_the_shipped_themes():
    assert {"nostromo", "night_city", "gates"} <= set(themes())


def test_dump_frames_writes_the_expected_files(tmp_path: Path):
    report = dump.dump_frames(tmp_path, themes=["nostromo"], variants=["amber"], states=["normal3", "critical"],
                              scales=[1.0, 2.0])
    variant_dir = tmp_path / "nostromo" / "amber"
    strips = len(dump.TASKBAR_HEIGHTS_AT_1X)
    assert report.files == 2 * 2 * (1 + strips + 1) + 2 * 4      # floating + the strips + tooltip per state/scale, tray x4
    assert (variant_dir / "floating_normal3_x1.png").is_file()
    assert (variant_dir / "floating_critical_x2.png").is_file()
    assert (variant_dir / "taskbar_normal3_h48_x1.png").is_file()
    assert (variant_dir / "taskbar_normal3_h96_x2.png").is_file()
    assert (variant_dir / "tooltip_critical_x1.png").is_file()
    assert (variant_dir / "tray_critical_32.png").is_file()
    sheet = report.sheets[0]
    assert sheet.is_file() and sheet.name == "contact_sheet.png"
    with Image.open(sheet) as opened:
        assert opened.format == "PNG" and opened.width > 300


def test_dump_frames_rejects_unknown_states(tmp_path: Path):
    with pytest.raises(KeyError):
        dump.dump_frames(tmp_path, themes=["nostromo"], states=["sideways"])


def test_the_scoped_sample_states_are_the_not_live_states_with_a_third_row():
    """What a widget shows on an account that has the Fable row while the sign-in is lost or the network is down."""
    assert dump.SCOPED_STATE_NAMES == ("stale3", "offline3", "paused3", "rate_limited3")
    assert dump.DUMP_STATE_NAMES == dump.STATE_NAMES + dump.SCOPED_STATE_NAMES + dump.GUARD_STATE_NAMES
    assert len(dump.STATE_NAMES) == 10, "the contact sheet and the existing sweeps keep their ten states"
    for name in dump.SCOPED_STATE_NAMES:
        plain, scoped = dump.sample_model(name[:-1]), dump.sample_model(name)
        assert scoped.status == plain.status and scoped.stale == plain.stale
        assert [row.key for row in scoped.rows[:2]] == [row.key for row in plain.rows] == ["session", "weekly"]
        assert len(scoped.rows) == 3 and scoped.rows[2].key.startswith("scoped:")
        assert scoped.rows[2].percent == 4.0


def test_dump_frames_renders_the_scoped_states_too(tmp_path: Path):
    report = dump.dump_frames(tmp_path, themes=["nostromo"], variants=["amber"], states=["offline3", "rate_limited3"],
                              scales=[1.0])
    variant_dir = tmp_path / "nostromo" / "amber"
    assert report.files == 2 * (1 + len(dump.TASKBAR_HEIGHTS_AT_1X) + 1) + 2 * 4
    assert (variant_dir / "floating_offline3_x1.png").is_file() and (variant_dir / "tray_rate_limited3_16.png").is_file()


def test_the_guard_sample_states_are_armed_and_held_by_each_window_in_turn():
    """The usage guard on a live account with the Fable row: armed with the default caps, held by the session with the
    reset hours away, held by the week with it days away. The verdict is the real guard's."""
    assert dump.GUARD_STATE_NAMES == ("guard_armed", "guard_hold_session", "guard_hold_weekly")
    armed, by_session, by_week = (dump.sample_model(name) for name in dump.GUARD_STATE_NAMES)
    for model in (armed, by_session, by_week):
        assert model.status is ApiStatus.LIVE and not model.stale and len(model.rows) == 3
    assert armed.guard.state == "armed" and armed.guard.caps == {"session": 95, "weekly": 90}
    assert by_session.guard.over_keys == ("session",) and by_session.rows[0].severity == SEVERITY_CRITICAL
    assert by_session.guard.resume_text == by_session.guard.resume_in == "2H 14M"
    assert by_week.guard.over_keys == ("weekly",) and by_week.guard.resume_in == "2D 3H"
    assert by_week.guard.resume_text == by_week.guard.resume_clock != by_week.guard.resume_in
    state_ = dump.sample_state("guard_hold_weekly")
    assert state_.guard == guard_mod.evaluate(guard_mod.limits_doc(state_.usage), dump.GUARD_SAMPLE_POLICY, dump.SAMPLE_NOW)


def test_the_other_sample_states_carry_no_guard():
    for name in dump.STATE_NAMES + dump.SCOPED_STATE_NAMES:
        state_ = dump.sample_state(name)
        assert state_ is None or state_.guard == {}, name
        assert dump.sample_model(name).guard is None, name


def test_dump_frames_renders_the_guard_states_too(tmp_path: Path):
    report = dump.dump_frames(tmp_path, themes=["nostromo"], variants=["amber"],
                              states=["guard_armed", "guard_hold_weekly"], scales=[1.0])
    variant_dir = tmp_path / "nostromo" / "amber"
    assert report.files == 2 * (1 + len(dump.TASKBAR_HEIGHTS_AT_1X) + 1) + 2 * 4
    for name in ("floating_guard_armed_x1.png", "taskbar_guard_hold_weekly_h48_x1.png",
                 "tooltip_guard_hold_weekly_x1.png", "tray_guard_armed_16.png"):
        assert (variant_dir / name).is_file(), name


def test_unknown_theme_is_reported(tmp_path: Path):
    with pytest.raises(KeyError):
        dump.dump_frames(tmp_path, themes=["no-such-theme"])
