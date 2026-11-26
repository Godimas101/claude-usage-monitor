"""The tray's toasts and the usage alerts take their words from the catalog (#97), and English stays what it was.

tests/fixtures/toast_words_english.json holds today's English of every toast and alert, captured before #97 moved a
word: the release toast, the note after a silent update for each count pair and for an older Setup's prose, the alerts
the engine raises for each name shape and credits shape, and the alerts' name, reset and credits texts.
tests/toast_words.py works them out in a fresh Python with the time zone pinned, since reset times are local.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

import english_left
import toast_words
from usage_monitor import app, i18n
from usage_monitor.ui import dashboard


@pytest.fixture(scope="module")
def recorded() -> dict[str, Any]:
    return toast_words.recorded()


# ── English stays what it was (step a1) ───────────────────────────────────────

def test_english_toasts_are_as_recorded(recorded):
    """Every toast and alert, byte for byte, as it was before #97."""
    now = toast_words.collect()
    assert sorted(now) == sorted(recorded)
    changed = {key: (recorded[key], now[key]) for key in recorded if now[key] != recorded[key]}
    assert not changed, json.dumps(dict(list(changed.items())[:5]), ensure_ascii=False, indent=1)


def test_the_toast_record_is_whole(recorded):
    """So the test above cannot pass on a thin record: every release, count pair, prose note, name shape, credits shape
    and reset time is in it, and every kind of alert was raised."""
    expected = {f"update/{version}" for version in toast_words.UPDATE_VERSIONS}
    expected |= {f"note/{tools}/{waits}" for tools in toast_words.NOTE_COUNTS for waits in toast_words.NOTE_COUNTS}
    expected |= {f"note/prose/{name}" for name in toast_words.PROSE_NOTES}
    expected |= {f"{part}/{shape}" for part in ("alerts", "name") for shape in toast_words.SHAPES}
    expected |= {f"{part}/{shape}" for part in ("credits", "credits_text") for shape in toast_words.SPENDS}
    expected |= {f"ladder/{'-'.join(map(str, levels))}" for levels in toast_words.THRESHOLDS}
    expected |= {f"reset_text/{kind}/{moment.isoformat()}" for kind in ("session", "weekly")
                 for moment in toast_words.reset_moments()}
    assert set(recorded) == expected
    for version in toast_words.UPDATE_VERSIONS:
        assert recorded[f"update/{version}"] == [[f"Claude Usage Monitor v{version} is available",
                                                  "Open the tray menu or the dashboard to update"]]
    for tools in toast_words.NOTE_COUNTS:
        for waits in toast_words.NOTE_COUNTS:
            note = recorded[f"note/{tools}/{waits}"]
            assert not note["kept"] and len(note["toasts"]) == (1 if tools or waits else 0), (tools, waits)
    assert len(recorded["note/prose/long"]["toasts"][0][1]) == 250                  # app.py cuts the note there
    assert recorded["note/prose/empty"] == {"toasts": [], "kept": False}
    for shape in toast_words.SHAPES:
        steps = recorded[f"alerts/{shape}"]
        kinds = [[alert[0] for alert in step] for step in steps]
        assert kinds == [[], ["threshold"], ["threshold"], ["threshold"], ["reset"], ["threshold"], ["reset"]], shape
        assert steps[4][0][4].startswith("Resets ") and steps[6][0][4] == "Usage is back to 0%.", shape
        assert [alert[1] for step in steps[1:4] for alert in step] == ["warning", "warning", "critical"], shape
    for shape in toast_words.SPENDS:
        steps = recorded[f"credits/{shape}"]
        assert steps[0] == [] and [alert[3] for alert in steps[1]] == ["Usage credits in use"], shape
    names = {recorded[f"name/{shape}"] for shape in toast_words.SHAPES}
    assert {"Session", "Weekly", "Fable weekly", "Claude instant weekly", "Weekly scoped", "Design weekly", "Fable",
            "Scoped", "Spaced out", "Claude_code weekly", ""} <= names
    blank = recorded["alerts/scoped_blank_unlabelled"]                     # a name of spaces alone: no name at all
    assert (blank[1][0][3], blank[4][0][3], blank[6][0][3]) == (" limit at 80%", " limit reset", "Usage limit reset")
    assert [alert[3] for step in recorded["ladder/1-33-99"] for alert in step] == [
        "Session limit at 33%", "Session limit at 99%", "Session limit reset", "Session limit at 1%",
        "Session limit at 33%", "Session limit reset"]           # 1 % was passed at the baseline, not in the new window


def test_the_recorder_words_the_note_as_endednote_does():
    """The prose the record's notes start from is installer.iss's own EndedNote: its four sentence forms, read from
    the script, joined by a space as the routine joins them."""
    forms = toast_words.ended_note_forms()
    assert forms[0].startswith("Setup closed the monitor's tool in a Claude Code session.")
    assert "%d" in forms[1] and "%d" not in forms[0] and "%d" in forms[3] and "%d" not in forms[2]
    assert toast_words.ended_note(0, 0) == ""
    assert toast_words.ended_note(2, 1) == forms[1].replace("%d", "2") + " " + forms[2]
    assert toast_words.ended_note(0, 12) == forms[3].replace("%d", "12")
    pascal = ("function EndedNote: String;\nbegin\n  Result := '';\n  A := 'It''s one';\n  B := 'x %d';\n"
              "  C := ' ';\n  D := 'a wait';\n  E := 'ws %d';\nend;\n\nprocedure Other;\nbegin\n  F := 'not it';\nend;\n")
    assert toast_words.ended_note_forms(pascal) == ("It's one", "x %d", "a wait", "ws %d")


# ── The note Setup leaves after a silent update (steps a4 to a6) ──────────────

def test_every_count_pair_says_what_setup_said(recorded):
    """Setup now leaves the counts (installer.iss WriteUpdateNote), and the app words them: for every count pair the
    toast is the one today's app showed for the sentences Setup used to write (the record's), and the note goes."""
    for tools in toast_words.NOTE_COUNTS:
        for waits in toast_words.NOTE_COUNTS:
            counts = json.dumps({"tools": tools, "waits": waits}).encode("ascii")
            said = toast_words.note_toasts(i18n.ENGLISH, counts)
            was = recorded[f"note/{tools}/{waits}"]
            assert said == was, (tools, waits)
    assert app.update_note_counts('{"waits": 3}') == (0, 3) and app.update_note_counts("{}") == (0, 0)
    assert app.update_note_text('  {"tools": 1, "waits": 0}\r\n') == recorded["note/1/0"]["toasts"][0][1]


def test_the_note_is_worded_in_the_apps_language():
    """The counts are worded in the app's language of the moment, title and text: the pseudo-locale's own sentences,
    Claude Code and /mcp as written, the counts in its digits; no English is left."""
    pseudo = i18n.dev_locale(i18n.PSEUDO)
    said = toast_words.note_toasts(pseudo, b'{"tools": 2, "waits": 1}')
    words = i18n.app_catalog(i18n.PSEUDO)
    names = {"product": "Claude Code", "command": "/mcp"}
    text = (words.plural("toast.updated.tools", 2, {"count": "2", **names}) + " "
            + words.plural("toast.updated.waits", 1, {"count": "1"}))
    assert said == {"toasts": [[words.text("toast.updated.title", {"app": "Claude Usage Monitor"}), text]],
                    "kept": False}
    assert "Claude Code" in text and "/mcp" in text and text.startswith("[")
    title, body = said["toasts"][0]
    assert english_left.english([title, body], english_left.widget_names(), ["/mcp"]) == set()
    assert english_left.english([body], english_left.widget_names()) == {english_left.entry(body)}  # /mcp is data


def test_the_toasts_fixed_words_are_the_catalogs():
    """The app's English records of its two fixed toast texts are en.json's, the monitor's name in them is the
    dashboard window's, and the note's command is one the catalog check knows as a command."""
    english = i18n.load_catalog("en")
    assert app.UPDATE_TOAST_BODY == english.text("toast.update.body") == app.update_toast_body()
    assert app.UPDATE_NOTE_TITLE == english.text("toast.updated.title", {"app": app.APP_NAME}) == app.update_note_title()
    assert app.APP_NAME == dashboard.WINDOW_TITLE and app.MCP_COMMAND in i18n.COMMANDS
    assert app.update_toast_title("2.1.0") == "Claude Usage Monitor v2.1.0 is available"
