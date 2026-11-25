"""The words of the view model, the tray tooltip and the tray menu come from the catalog (#93), and English stays what
it was.

tests/fixtures/widget_words_english.json holds today's English of all three, captured before #93 moved a word: the
model's texts in every sample and edge state, the tooltip of each (with and without the dev mark, the guard at every
countdown shape) and every menu. tests/widget_words.py works them out in a fresh Python with the time zone pinned, since
reset times are local.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import widget_words

FIXTURE = Path(__file__).parent / "fixtures" / "widget_words_english.json"


@pytest.fixture(scope="module")
def recorded() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["words"]


# ── English stays what it was (step 0) ────────────────────────────────────────

def test_the_english_words_are_the_recorded_ones(recorded):
    """Every model text, tooltip and menu row, byte for byte, as they were before #93."""
    now = widget_words.collect()
    assert sorted(now) == sorted(recorded)
    changed = {key: (recorded[key], now[key]) for key in recorded if now[key] != recorded[key]}
    assert not changed, json.dumps(dict(list(changed.items())[:5]), ensure_ascii=False, indent=1)


def test_the_record_is_whole(recorded):
    """So the test above cannot pass on a thin record: every state, mode, countdown and menu is in it."""
    assert sum(key.startswith("menu/") for key in recorded) == 2 * 2 * 2 * len(widget_words.WAITING) * len(
        widget_words.UPDATES)
    assert any("(1000)" in row[0] for key, menu in recorded.items() if key.startswith("menu/") for row in menu)
    tips = [text for key, pair in recorded.items() if key.startswith("tooltip/") for text in pair]
    for shape in ("(in <1m)", "(in 9m)", "(in 2h 14m)", "(in 2d 3h)", "(in 6d 23h)"):
        assert any(shape in text for text in tips), shape
    assert any(text.endswith(" - guard holding") for text in tips)
    assert any(text.endswith(" (dev)") for text in tips)
    models = [value for key, value in recorded.items() if key.startswith("model/")]
    assert {model["status_text"] for model in models} == {"LOADING", "LIVE", "STALE", "OFFLINE", "RATE LIMITED",
                                                          "NO LOGIN", "PAUSED"}
    assert {model["stale_message"] for model in models} >= {"LOGIN EXPIRED - USE CLAUDE CODE",
                                                            "NO LOGIN FOUND - OPEN THE DASHBOARD",
                                                            "LOGIN REFUSED - RUN /login IN CLAUDE CODE"}


# ── The keys hold today's English (step 1) ────────────────────────────────────

# Each status code's key part, and a recorded state with that status.
STATUS_STATES = {"loading": "loading", "live": "normal3", "stale": "stale", "offline": "offline",
                 "rate_limited": "rate_limited", "no_login": "no_credentials", "paused": "paused"}
NO_READING = {"loading": "loading", "live": "live", "stale": "stale", "offline": "offline",
              "rate_limited": "rate_limited", "no_login": "no_credentials", "paused": "paused"}


def test_the_english_catalog_is_todays_english(recorded):
    """Each key of en.json, filled with the values English gives it, is the text English showed before #93, so a retyped
    value fails. The templates and their fills: the stale messages ({product} CLAUDE CODE, {command} /login), the scoped
    row ({name} FABLE) and guard label ({name} Fable), the share ({percent} 50%), the tooltip's limits ({percent}) and
    guard phrase ({clock}, {countdown}), the menu's plural ({count}) and update ({version}). Three keys have no one
    English text of their own: the scoped row and guard label with no name are the shape of identity.legacy_label's
    "WEEKLY SCOPED", which English shows as the label, and tray.tip.guard_until is guard_until_in without its countdown
    (English always has the countdown)."""
    from usage_monitor import i18n
    from usage_monitor.data.identity import legacy_label
    from usage_monitor.render import model

    en = i18n.load_catalog("en")
    text = en.text
    words = {key[len("model/"):]: value for key, value in recorded.items() if key.startswith("model/")}
    tips = {key[len("tooltip/"):]: value[0] for key, value in recorded.items() if key.startswith("tooltip/")}
    for code, state in STATUS_STATES.items():
        assert text(f"widget.status.{code}") == words[f"{state}/auto"]["status_text"], code
        assert tips[f"no_reading/{NO_READING[code]}"] == f"{text('tray.tip.lead')} - {text(f'tray.tip.status.{code}')}"
    assert text("widget.stale.expired", {"product": "CLAUDE CODE"}) == model.STALE_MESSAGE_EXPIRED == words[
        "stale/auto"]["stale_message"]
    assert text("widget.stale.no_login") == model.STALE_MESSAGE_NO_LOGIN == words["no_credentials/auto"]["stale_message"]
    assert text("widget.stale.refused", {"command": "/login", "product": "CLAUDE CODE"}) == model.STALE_MESSAGE_REFUSED \
        == words["refused"]["stale_message"]
    rows = {row[0]: row for row in words["normal3/auto"]["rows"]}
    assert (text("widget.limit.session"), text("widget.limit.weekly")) == (rows["session"][1], rows["weekly"][1])
    assert text("widget.limit.scoped", {"name": "FABLE"}) == rows["scoped:fable"][1] == "FABLE WEEKLY"
    assert text("widget.limit.scoped_unnamed") == legacy_label("weekly_scoped", None, None) == words[
        "scoped_neither"]["rows"][2][1]
    assert text("widget.share", {"percent": "50%"}) == rows["scoped:fable"][2] == "50% SHARE"
    labels = words["guard_over_a_limit_gone"]["guard"]["labels"]
    assert (text("widget.guard.session"), text("widget.guard.weekly")) == (labels["session"], labels["weekly"])
    assert text("widget.guard.scoped", {"name": "Fable"}) == labels["scoped:fable"] == "Fable weekly"
    assert text("widget.guard.scoped_unnamed") == words["scoped_neither/guard_holding"]["guard"]["labels"][
        "scoped:weekly scoped"] == "weekly scoped"
    lead, session, weekly = tips["normal3/auto"].split(" - ")
    assert (text("tray.tip.lead"), text("tray.tip.session", {"percent": "13%"}),
            text("tray.tip.weekly", {"percent": "2%"})) == (lead, session, weekly)
    assert tips["guard_armed/auto"].endswith(" - " + text("tray.tip.guard_armed"))
    assert tips["guard_hold_session/no_resume_time"].endswith(" - " + text("tray.tip.guard_holding"))
    held = words["guard_hold_session/auto"]["guard"]
    assert tips["guard_hold_session/auto"].endswith(" - " + text("tray.tip.guard_until_in", {
        "clock": held["resume_clock"], "countdown": "2h 14m"}))
    assert text("tray.tip.guard_until_in") == text("tray.tip.guard_until") + " (in {countdown})"
    menu = [row[0] for row in recorded["menu/floating=True taskbar=True guard=True waiting=2 update=2.1.0"]
            if row != ["-"]]
    keys = ("open_dashboard", "floating", "taskbar", "guard", None, "refresh", "report_bug", None, "exit")
    shown = [text(f"tray.menu.{key}") if key else None for key in keys]
    assert [label if key else None for key, label in zip(keys, menu)] == shown
    for count in (1, 2, 12, 1000):
        row = next(row[0] for row in recorded[f"menu/floating=True taskbar=True guard=True waiting={count} update=None"]
                   if row[0].startswith("Resume"))
        assert en.plural("tray.menu.resume_waiting", count) == row
    assert en.lookup("tray.menu.resume_waiting").value == {"one": "Resume waiting sessions ({count})",
                                                          "other": "Resume waiting sessions ({count})"}
    assert text("tray.menu.update", {"version": "2.1.0"}) == menu[7] == "Update to v2.1.0"
    english = en.english()
    assert {key for key in english if key.startswith(("widget.", "tray."))} == {
        *(f"widget.status.{code}" for code in STATUS_STATES), "widget.stale.expired", "widget.stale.no_login",
        "widget.stale.refused", "widget.limit.session", "widget.limit.weekly", "widget.limit.scoped",
        "widget.limit.scoped_unnamed", "widget.share", "widget.guard.session", "widget.guard.weekly",
        "widget.guard.scoped", "widget.guard.scoped_unnamed", "tray.tip.lead", "tray.tip.session", "tray.tip.weekly",
        *(f"tray.tip.status.{code}" for code in STATUS_STATES), "tray.tip.guard_armed", "tray.tip.guard_holding",
        "tray.tip.guard_until", "tray.tip.guard_until_in", *(f"tray.menu.{key}" for key in keys if key),
        "tray.menu.resume_waiting", "tray.menu.update"}


def test_each_note_sits_before_its_key():
    """A translator's note (``_note.<key>``, #93) names a key en.json has and sits just before it, so the budget note
    (``_budget.<key>``) can sit just after it."""
    from usage_monitor import i18n

    raw = json.loads((i18n.CATALOG_DIR / "en.json").read_text(encoding="utf-8"))
    order = list(raw)
    notes = [key for key in order if key.startswith("_note.")]
    assert notes, "no notes, so nothing is checked"
    for note in notes:
        key = note[len("_note."):]
        assert key in raw and order.index(key) == order.index(note) + 1, note
        assert isinstance(raw[note], str) and raw[note].strip(), note
