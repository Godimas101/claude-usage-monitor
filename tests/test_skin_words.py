"""Each skin's own words come from the catalog (#94 to #96), and English stays what it was.

tests/fixtures/skin_words_english.json holds each skin's English as it was before its words moved: every helper that
words a text and every run each surface draws, in every sample, edge and guard state and the states its words hang on,
at 1.0 and 1.25. tests/skin_words.py works them out in a fresh Python with the time zone pinned, since reset times are
local.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

import skin_words

RECORDED: dict[str, Any] = skin_words.recorded()


# ── English stays what it was (#94 step 0) ────────────────────────────────────

@pytest.mark.parametrize("skin", sorted(RECORDED))
def test_the_english_skin_words_are_the_recorded_ones(skin: str) -> None:
    """Every helper's text and every drawn run (text, place, face, size, tracking, advance), byte for byte, as they
    were before the skin's words moved into the catalog."""
    now, was = skin_words.collect(skin), RECORDED[skin]
    assert sorted(now) == sorted(was)
    changed = {state: {part: (was[state][part], now[state][part]) for part in was[state]
                       if was[state][part] != now[state][part]}
               for state in was if now[state] != was[state]}
    assert not changed, json.dumps(dict(list(changed.items())[:3]), ensure_ascii=False)[:6000]


def test_every_recorded_skin_is_worked_out_and_its_record_is_whole() -> None:
    """So the test above cannot pass on a thin record: every skin with a section can be worked out, and Nostromo's has
    every dump state, the edge and guard states, the states its own words hang on, and every surface at both scales."""
    from usage_monitor.render import dump

    assert set(RECORDED) <= set(skin_words.SKINS) and "nostromo" in RECORDED
    nostromo = RECORDED["nostromo"]
    assert {name.split("/")[0] for name in nostromo} >= set(dump.DUMP_STATE_NAMES)
    assert {"refused", "unknown_kind", "guard_over_a_limit_gone", "guard_hold_session/resume_in_59s",
            "rate_limited3/no_resume_time", "reason/no_login", "age/rate_limited/100d", "pace/none",
            "pace/behind_100", "guard_armed_no_caps"} <= set(nostromo)
    surfaces = {"floating@1", "floating@1.25", "tooltip@1", "tooltip@1.25", "tray/32",
                *(f"strip/h{round(height * scale)}@{scale:g}" for scale in skin_words.SCALES
                  for height in dump.TASKBAR_HEIGHTS_AT_1X)}
    assert all(set(words["runs"]) == surfaces for words in nostromo.values())
    drawn = {run[0] for words in nostromo.values() for runs in words["runs"].values() if not isinstance(runs, str)
             for run in runs}
    assert {"[GUARD]", "[G]", "GUARD ARMED", "GUARD HOLDING:", "PACE --", "ON PACE", "AHEAD +100 PTS",
            "BEHIND -100 PTS", "100 D", "47 H", "HOLD", "HOLD <1M", "RESETS --", "RUN /login IN CLAUDE CODE.",
            "NO LOGIN FOUND", "5H", "7D"} <= drawn


# ── The keys hold today's English (#94 step 1) ────────────────────────────────

# Nostromo's keys (widget.nostromo.<group>.<name>): the header's state words, the guard's chip, mark and line, the
# sign-in box, the rows' names, the window tags, the share, the cap tag, the reset and the pace.
NOSTROMO_KEYS = (
    *(f"state.{code}" for code in ("normal", "warning", "critical", "stale", "offline", "paused", "loading",
                                   "rate_limited", "no_credentials")),
    *(f"guard.{part}" for part in ("hold", "hold_for", "mark", "mark_short", "armed", "armed_list", "holding",
                                   "holding_list", "limit", "cap", "resumes", "time_left")),
    *(f"auth.{reason}.line{line}" for reason in ("expired", "refused", "no_login") for line in (1, 2)),
    "limit.session", "limit.weekly", "limit.scoped", "limit.scoped_unnamed", "window.session", "window.weekly",
    "share.of", "cap.tag", "resets.at", "resets.unknown", "pace.none", "pace.on", "pace.ahead", "pace.behind",
)


def _drawn(words: dict[str, Any], surface: str) -> set[str]:
    """The texts one recorded state drew on one surface (a surface may name the one it drew the same as)."""
    runs = words["runs"][surface]
    return {run[0] for run in (words["runs"][runs] if isinstance(runs, str) else runs)}


def test_the_english_catalog_is_nostromos_english() -> None:
    """Each widget.nostromo key of en.json, filled with the values English gives it, is the text Nostromo drew before
    #94 (the record above), so a retyped value fails. The fills: {countdown} 2H 14M, {name} SESSION, WEEKLY or FABLE,
    {percent} the percent, {clock} the reset or resume time, {time_left} the glued countdown, {product} CLAUDE CODE,
    {command} /login, {points} the signed points. One key has no English text of its own: guard.holding, the lead of
    a guard holding with nothing to list, is holding_list without its colon, as the code gave such a lead."""
    from usage_monitor import i18n
    from usage_monitor.render import engine

    en = i18n.load_catalog("en")
    assert {key for key in en.english() if key.startswith("widget.nostromo.")} == {
        f"widget.nostromo.{key}" for key in NOSTROMO_KEYS}

    def text(key: str, **values: str) -> str:
        return str(en.text(f"widget.nostromo.{key}", values))

    was = RECORDED["nostromo"]
    helpers = {state: words["helpers"] for state, words in was.items()}
    states = {"normal": "normal3", "warning": "warning", "critical": "critical", "stale": "stale",
              "offline": "offline", "paused": "paused", "loading": "loading", "rate_limited": "rate_limited",
              "no_credentials": "no_credentials"}
    for code, state in states.items():
        assert text(f"state.{code}") == helpers[f"{state}/auto"]["state_word"][0], code
    held = helpers["guard_hold_session/auto"]
    assert text("guard.hold_for", countdown="2H 14M") == held["hold"] == held["state_word"][0]
    assert text("guard.hold") == helpers["guard_hold_session/no_resume_time"]["hold"]
    assert text("guard.mark") in _drawn(was["guard_armed/auto"], "floating@1")
    assert text("guard.mark_short") in _drawn(was["guard_armed_rate_limited"], "floating@1")
    assert helpers["guard_armed_no_caps"]["statement"] == [text("guard.armed"), ""]
    caps = (("SESSION", "95%"), ("WEEKLY", "90%"))
    assert helpers["guard_armed/auto"]["statement"] == [text("guard.armed_list"), ", ".join(
        engine.glue(text("guard.limit", name=name, percent=cap)) for name, cap in caps)]
    assert text("guard.holding") + ":" == text("guard.holding_list") == held["statement"][0]
    time_left = engine.glue(text("guard.time_left", countdown="2H 14M"))
    assert held["statement"][1] == (engine.glue(text("guard.limit", name="SESSION", percent="96%")) + " "
                                    + engine.glue(text("guard.cap", percent="95%")) + ". "
                                    + text("guard.resumes", clock=engine.glue("10:26 PM"), time_left=time_left) + ".")
    for reason in ("expired", "refused", "no_login"):
        assert [text(f"auth.{reason}.line{line}", product="CLAUDE CODE", command="/login") for line in (1, 2)] == \
            helpers[f"reason/{reason}"]["message"], reason
    rows = {row["key"]: row for row in helpers["normal3/auto"]["rows"]}
    assert (text("limit.session"), text("limit.weekly")) == (rows["session"]["names"][0], rows["weekly"]["names"][0])
    assert text("limit.scoped", name="FABLE") == rows["scoped:fable"]["names"][0] == "FABLE WEEKLY"
    assert text("limit.scoped_unnamed") == helpers["scoped_neither"]["rows"][2]["names"][0]
    assert (text("window.session"), text("window.weekly")) == (rows["session"]["qualifier"],
                                                               rows["weekly"]["qualifier"])
    assert {text("window.session"), text("window.weekly")} <= _drawn(was["normal3/auto"], "strip/h48@1")
    assert text("share.of", percent="50%") == rows["scoped:fable"]["qualifier"]
    assert text("cap.tag", percent="95%") == held["rows"][0]["cap"]
    assert text("resets.at", clock="11:39 PM") in _drawn(was["normal3/auto"], "tooltip@1")
    assert text("resets.unknown") in _drawn(was["pace/none"], "tooltip@1")
    paces = [helpers[f"pace/{shape}"]["rows"][0]["pace"][0] for shape in ("none", "on", "ahead_100", "behind_100")]
    assert [text("pace.none"), text("pace.on"), text("pace.ahead", points="+100"),
            text("pace.behind", points="-100")] == paces
