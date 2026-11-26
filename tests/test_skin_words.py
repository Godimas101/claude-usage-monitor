"""Each skin's own words come from the catalog (#94 to #96), and English stays what it was.

tests/fixtures/skin_words_english.json holds each skin's English as it was before its words moved: every helper that
words a text and every run each surface draws, in every sample, edge and guard state and the states its words hang on,
at 1.0 and 1.25. tests/skin_words.py works them out in a fresh Python with the time zone pinned, since reset times are
local.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import subprocess
import sys
from typing import Any

import pytest

import english_left
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


def _night_city_runs(words: dict[str, Any]) -> list[dict[str, Any]]:
    """Every set of runs one recorded Night City state has: Blackwall's, each look's and each transparent_bg one."""
    return [words["runs"], *words["looks"].values(), *words["clear"].values()]


def test_night_citys_record_is_whole() -> None:
    """Night City's section has every dump state, the edge and guard states and the states its own words hang on (the
    refused band, HOLD alone, ages on the stale and offline bands, the band's hold chip, the GUARD chip beside a status
    chip, the paces); Blackwall's runs on every surface at both scales, Relic's and Edgerun's at 1.0, and Blackwall's
    and Edgerun's floating widget and strip with transparent_bg; and every word the skin draws today."""
    from usage_monitor.render import dump

    night_city = RECORDED["night_city"]
    assert {name.split("/")[0] for name in night_city} >= set(dump.DUMP_STATE_NAMES)
    assert {"refused", "reason/refused", "unknown_kind", "scoped_neither", "guard_over_a_limit_gone",
            "guard_hold_session/no_resume_time", "rate_limited3/resume_in_59s", "age/stale/100d", "age/offline/59s",
            "age/rate_limited/47h", "band_hold/stale3", "band_hold/offline3/no_resume_time", "guard_armed_paused",
            "guard_armed_loading", "guard_armed_no_caps", "pace/none", "pace/behind_100"} <= set(night_city)
    heights = dump.TASKBAR_HEIGHTS_AT_1X
    surfaces = {"floating@1", "floating@1.25", "tooltip@1", "tooltip@1.25", "tray/32",
                *(f"strip/h{round(height * scale)}@{scale:g}" for scale in skin_words.SCALES for height in heights)}
    looks = {"floating@1", "tooltip@1", "tray/32", *(f"strip/h{height}@1" for height in heights)}
    clear = {"floating@1", *(f"strip/h{height}@1" for height in heights)}
    for words in night_city.values():
        assert set(words) == {"helpers", "runs", "looks", "clear"}
        assert set(words["runs"]) == surfaces
        assert {look: set(found) for look, found in words["looks"].items()} == {
            look: looks for look in skin_words.NIGHT_CITY_LOOKS}
        assert {variant: set(found) for variant, found in words["clear"].items()} == {
            variant: clear for variant in skin_words.NIGHT_CITY_CLEAR}
    assert set(skin_words.NIGHT_CITY_LOOKS) == {"relic", "edgerun"} and "blackwall" in skin_words.NIGHT_CITY_CLEAR
    drawn = {run[0] for words in night_city.values() for part in _night_city_runs(words) for found in part.values()
             if not isinstance(found, str) for run in found}
    assert {"CLAUDE//USAGE", "LIVE", "PAUSED", "LOADING", "GUARD", "HOLD", "HOLD 2H 14M", "HOLD <1M",
            "RELINK // USE CLAUDE CODE", "RELINK // RUN /login", "NO LOGIN // OPEN DASHBOARD", "NO SIGNAL // NET DOWN",
            "RATE LIMITED", "59 S OLD", "100 D OLD", "CRIT", "WARN", "CAP 95", "SESSION", "WEEKLY", "WEEKLY SCOPED",
            "50% SHARE", "RST 11:39 PM", "RST SAT 8:12 PM", "PACE --", "ON PACE", "AHEAD +100", "UNDER PACE -100",
            "GUARD // ARMED", "GUARD // ARMED:", "GUARD // HOLDING:", "5H", "7D", "STALE", "OFFLINE", "LIMITED",
            "NO LOGIN"} <= drawn
    paused = _drawn(night_city["guard_armed_paused"], "floating@1")
    assert {"PAUSED", "GUARD"} <= paused and "GUARD" not in _drawn(night_city["guard_armed_loading"], "floating@1")
    normal = night_city["normal3/auto"]
    assert len({json.dumps(part["floating@1"]) for part in _night_city_runs(normal)}) == 3   # boxed, plain, plate
    assert normal["clear"]["edgerun"]["floating@1"] == normal["looks"]["relic"]["floating@1"]  # no plate: plain


# ── Gates' English stays what it was (#96 step 0) ─────────────────────────────

def _gates_surfaces() -> set[str]:
    from usage_monitor.render import dump

    return {"floating@1", "floating@1.25", "tooltip@1", "tooltip@1.25", "tray/32",
            *(f"strip/h{round(height * scale)}@{scale:g}" for scale in skin_words.SCALES
              for height in dump.TASKBAR_HEIGHTS_AT_1X)}


def test_gates_record_is_whole() -> None:
    """Gates' section has every dump state, the edge and guard states and the states its own words hang on (Night
    City's, the guard holding beside a missing sign-in, and the two guard holds whose resume English's footer cuts,
    each at a clock of its own); every surface at both scales and the tray once, its number one entry per run (never
    per glyph) with the keyline's mask and the ink's; and the words the frame dump never draws: the refused sign-in's
    footer, the first reading awaited, no reset time yet, the guard armed with nothing to list, and English's own
    cut."""
    from usage_monitor.render import dump

    gates = RECORDED["gates"]
    assert {name.split("/")[0] for name in gates} >= set(dump.DUMP_STATE_NAMES)
    assert set(RECORDED["night_city"]) <= set(gates)                 # Night City's states, every one
    assert {"refused", "unknown_kind", "scoped_neither", "scoped_surface_only/guard_holding", "guard_over_a_limit_gone",
            "no_reading/live", "pace/none", "pace/ahead_100", "guard_armed_no_caps", "age/stale/100d",
            "guard_hold/no_credentials", "guard_hold/resume_mon_10am", "guard_hold/resume_wed_1259pm"} <= set(gates)
    surfaces = _gates_surfaces()
    numbers = 0
    for words in gates.values():
        assert set(words) == {"helpers", "runs"} and set(words["runs"]) == surfaces
        tray = words["runs"]["tray/32"]               # the number, or nothing beside a lamp's mark (offline, limited)
        assert isinstance(tray, list) and len(tray) <= 1 and all(len(run[-1]) == 2 for run in tray), tray
        numbers += len(tray)
    assert numbers > 50
    drawn = {run[0] for words in gates.values() for runs in words["runs"].values() if not isinstance(runs, str)
             for run in runs}
    assert {"Claude usage", "Session", "Weekly", "Fable weekly", "Weekly scoped", "5H", "7D", "50% share", "Cap 95%",
            "Nearly out", "On pace", "Ahead of pace", "Under pace", "Ahead of pace by 100 pts",
            "Under pace by 100 pts", "Resets 11:39 PM", "Guard armed", "Guard armed:", "Guard holding",
            "Guard holding:", "Hmm, we lost touch.", " Send Claude Code a message.", "Sign-in refused.",
            " Run /login in Claude Code.", "No sign-in found.", " Open the dashboard for help.", "Rate limited.",
            " Showing the last reading.", "No connection. Retrying every minute.",
            "Paused. Resume to keep usage live.", "Fetching your usage.", "Waiting for your first reading.",
            "No reset time yet."} <= drawn
    held = gates["guard_hold/no_credentials"]
    assert {"No sign-in found.", " Open the dashboard for help."} <= _drawn(held, "floating@1")
    for name in ("guard_hold/resume_mon_10am", "guard_hold/resume_wed_1259pm"):
        cut = [text for text in _drawn(gates[name], "floating@1") if text.endswith("…")]
        assert cut and cut[0].startswith(" · Resumes "), (name, cut)                 # English's own cut


def test_the_gates_record_does_not_hang_on_the_glyph_cache() -> None:
    """Gates draws each run through _run, whose cache hands back a mask it drew before, and Pillow draws a glyph only
    when the cache misses: a record read from those draw calls would hang on what was drawn first. Gates' runs are read
    where the skin places them (skin_words.gates_runs), so the record worked out in one process with the cache cleared
    and again with it warm is the same."""
    from usage_monitor.render.skins import gates

    gates._run.cache_clear()
    cold = skin_words.SKINS["gates"]()
    warm = skin_words.SKINS["gates"]()
    assert gates._run.cache_info().hits > 0
    assert cold == warm


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
    "pace.points",
)


def _drawn(words: dict[str, Any], surface: str) -> set[str]:
    """The texts one recorded state drew on one surface (a surface may name the one it drew the same as)."""
    runs = words["runs"][surface]
    return {run[0] for run in (words["runs"][runs] if isinstance(runs, str) else runs)}


def test_the_english_catalog_is_nostromos_english() -> None:
    """Each widget.nostromo key of en.json, filled with the values English gives it, is the text Nostromo drew before
    #94 (the record above), so a retyped value fails. The fills: {countdown} 2H 14M, {name} SESSION, WEEKLY or FABLE,
    {percent} the percent, {clock} the reset or resume time, {time_left} the glued countdown, {product} CLAUDE CODE,
    {command} /login, {points} the signed points. Two keys have no English text of their own: guard.holding, the lead
    of a guard holding with nothing to list, is holding_list without its colon, as the code gave such a lead; and
    pace.points, the pace's short form for a tight line (#94's fits), which English never draws, is pace.ahead's and
    pace.behind's tail."""
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
    assert paces[2].endswith(" " + text("pace.points", points="+100")) and \
        paces[3].endswith(" " + text("pace.points", points="-100"))


# Night City's keys (widget.night_city.<group>.<name>, #95): the status words (the strip's tag, the header's LIVE tag)
# and the rows' chips, the stale band's four causes, the guard's chip, mark and line, the rows' names, the strip's
# labels, the share, the cap chip, the reset, the pace and the band's age.
NIGHT_CITY_KEYS = (
    *(f"state.{code}" for code in ("live", "stale", "offline", "paused", "loading", "rate_limited", "no_credentials",
                                   "warning", "critical")),
    *(f"band.{cause}" for cause in ("offline", "expired", "refused", "no_login")),
    *(f"guard.{part}" for part in ("hold", "hold_for", "mark", "armed", "armed_list", "holding", "holding_list",
                                   "limit", "cap", "resumes", "time_left")),
    "limit.session", "limit.weekly", "limit.scoped", "limit.scoped_unnamed", "window.session", "window.weekly",
    "share.of", "cap.tag", "resets.at", "pace.none", "pace.on", "pace.ahead", "pace.behind", "age.old",
)


def _night_city_drawn(words: dict[str, Any], surface: str = "floating@1") -> set[str]:
    """The texts one recorded Night City state drew on one surface in every look (Blackwall's runs, each look's and
    each transparent_bg one that has the surface)."""
    found: set[str] = set()
    for part in _night_city_runs(words):
        runs = part.get(surface, [])
        found |= {run[0] for run in (part[runs] if isinstance(runs, str) else runs)}
    return found


def test_the_english_catalog_is_night_citys_english() -> None:
    """Each widget.night_city key of en.json, filled with the values English gives it, is the text Night City drew
    before #95 (the record above), so a retyped value fails. The fills: {product} CLAUDE CODE, {command} /login,
    {countdown} 2H 14M, {name} SESSION, WEEKLY or FABLE, {percent} the percent, {clock} the reset or resume time,
    {time_left} the glued countdown, {number} the cap, {points} the signed points, {age} the age. guard.holding, the
    lead of a guard holding with nothing to list, has no English text of its own: it is holding_list without its colon,
    as the code gave such a lead."""
    from usage_monitor import i18n
    from usage_monitor.render import engine

    en = i18n.load_catalog("en")
    assert {key for key in en.english() if key.startswith("widget.night_city.")} == {
        f"widget.night_city.{key}" for key in NIGHT_CITY_KEYS}

    def text(key: str, **values: str) -> str:
        return str(en.text(f"widget.night_city.{key}", values))

    was = RECORDED["night_city"]
    helpers = {state: words["helpers"] for state, words in was.items()}
    strip = {"live": "normal3/auto", "stale": "stale/auto", "offline": "offline/auto", "paused": "paused/auto",
             "loading": "loading/auto", "rate_limited": "rate_limited/auto", "no_credentials": "no_credentials/auto"}
    for code, state in strip.items():
        assert text(f"state.{code}") in _night_city_drawn(was[state], "strip/h48@1"), code
    assert text("state.live") in _night_city_drawn(was["normal3/auto"])
    assert text("state.warning") in _night_city_drawn(was["warning/auto"])
    assert text("state.critical") == helpers["critical/auto"]["rows"][0]["chip"]
    names = {"product": "CLAUDE CODE", "command": "/login"}
    for cause, state in (("offline", "offline/auto"), ("expired", "stale/auto"), ("refused", "refused"),
                         ("no_login", "no_credentials/auto")):
        assert text(f"band.{cause}", **names) == helpers[state]["band"], cause
        assert helpers[state]["band"] in _night_city_drawn(was[state]), cause
    held = helpers["guard_hold_session/auto"]
    assert text("guard.hold_for", countdown="2H 14M") == held["hold"]
    assert text("guard.hold") == helpers["guard_hold_session/no_resume_time"]["hold"]
    assert text("guard.mark") in _night_city_drawn(was["guard_armed/auto"])
    assert helpers["guard_armed_no_caps"]["statement"] == [text("guard.armed"), ""]
    caps = (("SESSION", "95%"), ("WEEKLY", "90%"))
    assert helpers["guard_armed/auto"]["statement"] == [text("guard.armed_list"), ", ".join(
        engine.glue(text("guard.limit", name=name, percent=cap)) for name, cap in caps)]
    assert text("guard.holding") + ":" == text("guard.holding_list") == held["statement"][0]
    time_left = engine.glue(text("guard.time_left", countdown="2H 14M"))
    assert held["statement"][1] == (engine.glue(text("guard.limit", name="SESSION", percent="96%")) + " "
                                    + engine.glue(text("guard.cap", percent="95%")) + ". "
                                    + text("guard.resumes", clock=engine.glue("10:26 PM"), time_left=time_left) + ".")
    rows = {row["key"]: row for row in helpers["normal3/auto"]["rows"]}
    assert (text("limit.session"), text("limit.weekly")) == (rows["session"]["name"], rows["weekly"]["name"])
    assert helpers["scoped_model/guard_holding"]["statement"][1].replace(engine.NO_BREAK, " ").startswith(
        text("limit.scoped", name="FABLE") + " ")
    assert text("limit.scoped_unnamed") == helpers["scoped_neither"]["rows"][2]["name"]
    labels = [run[0] for run in was["normal3/auto"]["runs"]["strip/h48@1"] if run[1] == 10]    # x: STRIP_LABEL_X
    assert [text("window.session"), text("window.weekly")] == labels
    assert text("share.of", percent="50%") == rows["scoped:fable"]["share"]
    assert text("cap.tag", number="95") == held["rows"][0]["chip"]
    assert text("resets.at", clock="11:39 PM") == rows["session"]["reset"] == rows["session"]["reset_tooltip"]
    paces = [helpers[f"pace/{shape}"]["rows"][0]["pace"][0] for shape in ("none", "on", "ahead_100", "behind_100")]
    assert [text("pace.none"), text("pace.on"), text("pace.ahead", points="+100"),
            text("pace.behind", points="-100")] == paces
    for state, age in (("stale/auto", "41 M"), ("age/stale/100d", "100 D"), ("age/offline/59s", "59 S"),
                       ("age/rate_limited/47h", "47 H")):
        assert text("age.old", age=age) == helpers[state]["age"], state


def test_night_citys_english_records_are_the_catalogs() -> None:
    """Night City keeps its English words as records the older tests read, never drawn: STRIP_WORDS (the strip's
    status words), the four DEFAULT_ band causes and the theme file's copy of them (night_city.json's extras.copy, the
    mockup's record). Each is en.json's English, so the two cannot drift apart."""
    from usage_monitor import i18n
    from usage_monitor.data.models import ApiStatus
    from usage_monitor.render import dump
    from usage_monitor.render.skins import night_city as nc

    en = i18n.load_catalog("en")
    names = {"product": "CLAUDE CODE", "command": "/login"}

    def text(key: str) -> str:
        return str(en.text(f"widget.night_city.{key}", names))

    codes = {ApiStatus.LIVE: "live", ApiStatus.STALE: "stale", ApiStatus.OFFLINE: "offline",
             ApiStatus.PAUSED: "paused", ApiStatus.LOADING: "loading", ApiStatus.RATE_LIMITED: "rate_limited",
             ApiStatus.NO_CREDENTIALS: "no_credentials"}
    assert set(nc.STRIP_WORDS) == set(ApiStatus) == set(codes)
    assert nc.STATE_KEYS == {status: f"widget.night_city.state.{code}" for status, code in codes.items()}
    assert {status: word for status, word in nc.STRIP_WORDS.items()} == {
        status: text(f"state.{code}") for status, code in codes.items()}
    copy = dump.load_theme_objects()["night_city"].extras["copy"]
    for default, copied, cause in ((nc.DEFAULT_OFFLINE, "offline", "offline"),
                                   (nc.DEFAULT_STALE_AUTH, "stale_auth", "expired"),
                                   (nc.DEFAULT_STALE_REFUSED, "stale_refused", "refused"),
                                   (nc.DEFAULT_NO_LOGIN, "no_login", "no_login")):
        assert default == copy[copied] == text(f"band.{cause}"), cause


# Gates' keys (widget.gates.<group>.<name>, #96), in sentence case but the 5H and 7D tags: the rows' names, the window
# tags (the strip's labels too), the share, the cap badge, the reset, the paces (the footer's leads and the hover card's
# readouts, and their short form for a tight line), the critical lead, the guard's line and its names, and the
# footer's sentences (a lead and its step, or its one run).
GATES_KEYS = (
    "limit.session", "limit.weekly", "limit.scoped", "limit.scoped_unnamed", "window.session", "window.weekly",
    "share.of", "cap.tag", "resets.at", "pace.on", "pace.ahead", "pace.behind", "pace.ahead_by", "pace.behind_by",
    "pace.points", "state.critical",
    *(f"guard.{part}" for part in ("armed", "armed_list", "holding", "holding_list", "limit", "cap", "resumes",
                                   "time_left", "session", "weekly", "scoped", "scoped_unnamed")),
    *(f"footer.{part}" for part in ("stale", "stale_step", "refused", "refused_step", "no_login", "no_login_step",
                                    "rate_limited", "rate_limited_rest", "offline", "paused", "loading", "waiting",
                                    "no_reset")),
)


def test_the_english_catalog_is_gates_english() -> None:
    """Each widget.gates key of en.json, filled with the values English gives it, is the text Gates drew before #96
    (the record above), so a retyped value fails. The fills: {name} Fable or claude_code, {percent} the percent,
    {clock} the reset or resume time, {time_left} the time left in its brackets, {countdown} 2h 14m, {points} the
    points, {product} Claude Code, {command} /login. A footer's rest follows its lead after a space, which the code
    writes; the hover card's guard line glues its pieces (engine.glue). pace.points, the hover pace's short form for a
    tight line (#96's fits), which English never draws, is the end of pace.ahead_by's and pace.behind_by's."""
    from usage_monitor import i18n
    from usage_monitor.render import engine

    en = i18n.load_catalog("en")
    assert {key for key in en.english() if key.startswith("widget.gates.")} == {
        f"widget.gates.{key}" for key in GATES_KEYS}

    def text(key: str, **values: str) -> str:
        return str(en.text(f"widget.gates.{key}", values))

    was = RECORDED["gates"]
    helpers = {state: words["helpers"] for state, words in was.items()}
    rows = {row["key"]: row for row in helpers["normal3/auto"]["rows"]}
    assert (text("limit.session"), text("limit.weekly")) == (rows["session"]["name"], rows["weekly"]["name"])
    assert text("limit.scoped", name="Fable") == rows["scoped:fable"]["name"] == "Fable weekly"
    assert text("limit.scoped_unnamed") == helpers["scoped_neither"]["rows"][2]["name"]
    assert (text("window.session"), text("window.weekly")) == (rows["session"]["tag"], rows["weekly"]["tag"])
    labels = [run[0] for run in was["normal3/auto"]["runs"]["strip/h48@1"] if run[1] == 10]    # x: STRIP_K_X
    assert labels == [text("window.session"), text("window.weekly")]
    assert text("share.of", percent="50%") == rows["scoped:fable"]["tag"]
    held = helpers["guard_hold_session/auto"]
    assert text("cap.tag", percent="95%") in _drawn(was["guard_hold_session/auto"], "floating@1")
    assert text("resets.at", clock="11:39 PM") in _drawn(was["normal3/auto"], "tooltip@1")
    paces = {shape: helpers[f"pace/{shape}"]["rows"][0]["pace"] for shape in ("on", "ahead_100", "behind_100")}
    assert paces == {"on": [text("pace.on"), "ok"], "ahead_100": [text("pace.ahead_by", points="100"), "warn"],
                     "behind_100": [text("pace.behind_by", points="100"), "ok"]}
    assert paces["ahead_100"][0].endswith(" " + text("pace.points", points="100")) and         paces["behind_100"][0].endswith(" " + text("pace.points", points="100"))
    assert helpers["critical/auto"]["footer"][0] == [text("state.critical"), 600, "alert"]
    leads = {tuple(words["footer"][0]) for words in helpers.values()}
    assert {(text("pace.ahead"), 600, "warn"), (text("pace.behind"), 600, "ok"), (text("pace.on"), 600, "ok")} <= leads
    assert helpers["normal3/auto"]["footer"][1][0] == " · " + text("resets.at", clock="11:39 PM")
    assert helpers["guard_armed_no_caps"]["statement"] == [text("guard.armed"), ""]
    caps = (("session", "95%"), ("weekly", "90%"))
    assert helpers["guard_armed/auto"]["statement"] == [text("guard.armed_list"), ", ".join(
        engine.glue(text("guard.limit", name=name, percent=cap)) for name, cap in caps)]
    assert text("guard.holding") + ":" == text("guard.holding_list") == held["statement"][0]
    time_left = text("guard.time_left", countdown="2h 14m")
    assert held["statement"][1] == (engine.glue(text("guard.limit", name="session", percent="96%")) + " "
                                    + engine.glue(text("guard.cap", percent="95%")) + ". "
                                    + text("guard.resumes", clock=engine.glue("10:26 PM"),
                                           time_left=engine.glue(time_left)) + ".")
    assert held["hold"] == held["footer"] == [[text("guard.holding"), 600, "alert"], [
        " · " + text("guard.resumes", clock="10:26 PM", time_left=time_left), 400, "text_3"]]
    assert (text("guard.session"), text("guard.weekly")) == (held["names"]["session"],
                                                             helpers["guard_hold_weekly/auto"]["names"]["weekly"])
    named = {**helpers["scoped_model/guard_holding"]["names"], **helpers["scoped_surface_only/guard_holding"]["names"],
             **helpers["scoped_neither/guard_holding"]["names"]}
    assert {text("guard.scoped", name="Fable"), text("guard.scoped", name="claude_code"),
            text("guard.scoped_unnamed")} <= set(named.values()), named
    names = {"product": "Claude Code", "command": "/login"}
    for state, lead, rest in (("stale/auto", "stale", "stale_step"), ("refused", "refused", "refused_step"),
                              ("no_credentials/auto", "no_login", "no_login_step"),
                              ("rate_limited/auto", "rate_limited", "rate_limited_rest")):
        assert helpers[state]["footer"] == [[text(f"footer.{lead}"), 600, "warn"],
                                            [" " + text(f"footer.{rest}", **names), 400, "text_3"]], state
    for state, key in (("offline/auto", "offline"), ("paused/auto", "paused"), ("loading/auto", "loading"),
                       ("no_reading/live", "waiting"), ("pace/none", "no_reset")):
        assert helpers[state]["footer"] == [[text(f"footer.{key}"), 400, "text_3"]], state


# ── The skin's words come from the catalog (#94 steps 2 and 3) ────────────────

def _marked(english: str) -> str:
    """``english`` with the first letter of each word in lower case, its placeholders kept: "aUTH lINK lOST.",
    "hOLD {countdown}", "[gUARD]"."""
    parts = re.split(r"(\{[A-Za-z_][A-Za-z0-9_]*\})", english)
    return "".join(part if index % 2 else re.sub(r"(?<![A-Za-z])[A-Z]", lambda m: m.group(0).lower(), part)
                   for index, part in enumerate(parts))


def _tokens(text: str) -> list[str]:
    return [token.strip("[](),.:;") for token in text.split()]


def test_nostromo_takes_every_word_from_the_catalog_as_written(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every word Nostromo draws comes from a Catalog.text call on one of its keys, drawn as the catalog writes it.
    The catalog is patched so each widget.nostromo text has each word's first letter in lower case ("nOMINAL",
    "hOLD {countdown}"), Catalog.text is spied on, and every recorded state is drawn on every surface at 1.0. The
    texts the skin draws that are not its words are taken out of each run first: the view model's status word (the
    pill) and the data it read (a kind's name from its code, a guard label). After that no word of a run may be one of
    the marked words in another case: a word drawn as a literal, or a catalog text cased again, fails (a word of one
    letter, "5H" or "[G]", is marked all in lower case, so only capitals are seen there). The skin reads every one of
    its keys and no other. Its faces are monospaced, so the marked frames lay out as English's, drawn first (checked):
    each key is drawn where English draws it."""
    from usage_monitor import casing, i18n
    from usage_monitor.render import dump
    from usage_monitor.render.skins import get_skin
    from usage_monitor.render.skins import nostromo

    theme = dump.load_theme_objects()["nostromo"]
    skin, variant = get_skin("nostromo"), theme.variants[0]
    models = skin_words.models(skin_words._nostromo_states)
    english_runs = {name: skin_words.runs(skin, model, theme, variant, dump.TASKBAR_HEIGHTS_AT_1X, scales=(1.0,))
                    for name, model in models.items()}
    english = i18n.load_catalog("en").english()
    own = {key: _marked(value) for key, value in english.items() if key.startswith("widget.nostromo.")}
    catalog = i18n.Catalog("en", {}, {**english, **own})
    monkeypatch.setattr(i18n, "app_catalog", lambda language: catalog)
    data = {str(model.status_text) for model in models.values()}
    read: set[str] = set()
    real_text, real_names = i18n.Catalog.text, nostromo.unknown_kind_names

    def text(self: Any, key: str, values: Any = None) -> Any:
        read.add(key)
        return real_text(self, key, values)

    def unknown_kind_names(kind: str) -> tuple[str, str]:
        names = real_names(kind)
        data.update(names)
        return names

    class Labels(dict):  # type: ignore[type-arg]
        """A guard's labels, saying which the skin read."""

        def get(self, key: str, default: Any = None) -> Any:
            label = super().get(key, default)
            data.add(str(casing.upper(label)))
            return label

    monkeypatch.setattr(i18n.Catalog, "text", text)
    monkeypatch.setattr(nostromo, "unknown_kind_names", unknown_kind_names)
    drawn = {}
    for name, model in models.items():
        if model.guard is not None:
            guard = dataclasses.replace(model.guard, labels=Labels(model.guard.labels))
            model = dataclasses.replace(model, guard=guard)
        drawn[name] = skin_words.runs(skin, model, theme, variant, dump.TASKBAR_HEIGHTS_AT_1X, scales=(1.0,))
    marked = {token.upper(): token for value in own.values() for token in _tokens(value)
              if re.search("[A-Za-z]", token) and not token.startswith("{")}
    wrong, moved, words = [], [], 0
    for name, surfaces in drawn.items():
        if [[run[1:] for run in found] if not isinstance(found, str) else found for found in surfaces.values()] != [
                [run[1:] for run in found] if not isinstance(found, str) else found
                for found in english_runs[name].values()]:
            moved.append(name)
        for surface, found in surfaces.items():
            if isinstance(found, str):
                continue
            for run in found:
                rest = run[0]
                for piece in sorted(data, key=len, reverse=True):
                    rest = rest.replace(piece, " ")
                for token in _tokens(rest):
                    if token.upper() in marked:
                        words += token == marked[token.upper()]
                        if token != marked[token.upper()]:
                            wrong.append((name, surface, run[0], token))
    assert wrong == []
    assert read == set(own), sorted(read ^ set(own))
    assert moved == []
    assert words > 1000


# What Nostromo draws in English in every language, by design: data, not words.
DATA_LEFTOVERS: dict[str, str] = {
    "DESIGN WEEKLY": "a kind the usage API names that no catalog has words for, named from its code "
                     "(model.unknown_kind_names, #93 D4)",
    "OPUS WEEKLY": "a limit gone from the reading keeps the guard's own label in every language (#93 D4, owner "
                   "default)",
}


def test_no_nostromo_run_is_english_in_the_pseudo_locale() -> None:
    """Every run Nostromo draws in the pseudo-locale is the pseudo-locale's, as tests/english_left.py judges it: in
    every recorded state (the refused sign-in, the edge and guard states and the states Nostromo's words hang on, which
    the frame dump lacks), on every surface, at 1.0. Names are taken out (the scan's widget names, the states' scope
    names as given and in capitals, the commands), and so are the pseudo-locale's format words (its weekdays, AM and
    PM). What stays English is data: DATA_LEFTOVERS, each drawn."""
    from usage_monitor import casing, i18n
    from usage_monitor.render import dump
    from usage_monitor.render.skins import get_skin

    pseudo = i18n.dev_locale(i18n.PSEUDO)
    models = skin_words.models(skin_words._nostromo_states, pseudo)
    scopes = {scope for model in models.values()
              for scope in [*(row.scope_name for row in model.rows),
                            *(model.guard.scope_names.values() if model.guard is not None else ())] if scope}
    names = (*english_left.widget_names(), *i18n.COMMANDS, *sorted(scopes),
             *sorted(casing.upper(scope, pseudo.language) for scope in scopes))
    theme = dump.load_theme_objects()["nostromo"]
    skin, variant = get_skin("nostromo"), theme.variants[0]
    texts: dict[str, str] = {}
    for name, model in models.items():
        for surface, found in skin_words.runs(skin, model, theme, variant, dump.TASKBAR_HEIGHTS_AT_1X,
                                              scales=(1.0,)).items():
            for run in ([] if isinstance(found, str) else found):
                texts.setdefault(run[0], f"{name} {surface}")
    format_words = english_left.pseudo_format_words()
    left = english_left.english(texts, (*names, *DATA_LEFTOVERS), format_words)
    assert left == set(), {text: where for text, where in texts.items() if english_left.entry(text) in left}
    assert all(any(leftover in text for text in texts) for leftover in DATA_LEFTOVERS)
    assert {"Claude Code", "claude_code", "Haiku"} <= scopes
    refused = RECORDED["nostromo"]["refused"]["runs"]["floating@1"]
    assert english_left.english([run[0] for run in refused], names, format_words) >= {
        "AUTH REFUSED.", "RUN /login IN CLAUDE CODE."}                     # English is caught


def test_the_guard_lead_keeps_its_colon_on_its_line(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard line's lead is the language's, its colon too, and French writes a space before it ("GARDE EN
    ATTENTE :"): a plain one, a U+00A0 or a U+202F. The code keeps the lead on one line, so at no length of the lead,
    up to past the card's width, does a line of the statement start with the colon: the first line starts with the
    whole lead, the card draws exactly the lead in its face (_split_lead) and the reading after it in its own."""
    from usage_monitor import i18n
    from usage_monitor.render import dump, engine
    from usage_monitor.render.skins import nostromo

    english = i18n.load_catalog("en").english()
    cv = nostromo._canvas(nostromo.FLOAT_W, 1, 1.0, dump.load_theme_objects()["nostromo"])
    for state, key in (("guard_hold_session", "widget.nostromo.guard.holding_list"),
                       ("guard_armed", "widget.nostromo.guard.armed_list")):
        model = dump.sample_model(state, locale=i18n.dev_locale("fr"))
        for space in (" ", chr(0xA0), chr(0x202F)):
            for length in range(1, 60):
                lead = ("GARDE EN ATTENTE " * 4)[:length].rstrip() + space + ":"
                catalog = i18n.Catalog("fr", {key: lead}, english)
                monkeypatch.setattr(i18n, "app_catalog", lambda language, catalog=catalog: catalog)
                drawn, lines = nostromo._guard_lines(cv, model)
                shown = lead.replace(engine.NO_BREAK, " ")
                assert drawn == shown and lines[0].startswith(shown), (state, length, lines)
                assert not [line for line in lines if line.startswith(":")], (state, length, lines)
                head, tail = nostromo._split_lead(lines[0], drawn)
                statement = nostromo._guard_statement(model)
                assert statement is not None
                reading = statement[1].replace(engine.NO_BREAK, " ")
                assert head == shown and " ".join([tail.strip(), *lines[1:]]).strip() == reading, (state, space,
                                                                                                    length, lines)


# ── Night City's words come from the catalog (#95 step 2) ─────────────────────

def _data_marked(english: str) -> str:
    """``english`` with the last letter of each word in lower case ("PAUSEd", "RATe LIMITEd"): the view model's status
    words as the marker test below draws them, so they pass neither for Night City's own words (which are the same
    English words on the strip) nor for those words marked."""
    return re.sub(r"[A-Z](?![A-Za-z])", lambda m: m.group(0).lower(), english)


def _resolved(surfaces: dict[str, Any], surface: str) -> list[Any]:
    found = surfaces[surface]
    return surfaces[found] if isinstance(found, str) else found


def test_night_city_takes_every_word_from_the_catalog_as_written(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every word Night City draws comes from a Catalog.text call on one of its keys, drawn as the catalog writes it,
    as Nostromo's test above holds Nostromo: each widget.night_city text has each word's first letter in lower case
    ("lIVE", "hOLD {countdown}"), Catalog.text is spied on, and every recorded state is drawn on every surface at 1.0
    in Blackwall's, Relic's and Edgerun's looks. Night City also draws the view model's status word (the header's
    chip, the throttled band), whose English is the same as its own strip words, so the view model's words are marked
    otherwise (_data_marked) and taken out of each run, with the data the skin read (a kind's name from its code, a
    guard label). After that no word of a run may be one of the marked words in another case: a word drawn as a
    literal, or a catalog text cased again, fails (a word of one letter, "5H", is marked all in lower case, so only
    capitals are seen there). The skin reads every one of its keys and no other. Its body face is not monospaced, so
    the marked frames do not lay out as English's; a marked word is narrower, and every run English draws is drawn in
    the marked frames too, its case aside (checked), so each key is drawn where English draws it."""
    from usage_monitor import casing, i18n
    from usage_monitor.render import dump
    from usage_monitor.render.skins import get_skin
    from usage_monitor.render.skins import night_city

    theme = dump.load_theme_objects()["night_city"]
    skin = get_skin("night_city")
    variants = (theme.variants[0], *(theme.variant(look) for look in skin_words.NIGHT_CITY_LOOKS))

    def draw(models: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
        return {(name, variant.id): skin_words.runs(skin, model, theme, variant, dump.TASKBAR_HEIGHTS_AT_1X,
                                                    scales=(1.0,))
                for name, model in models.items() for variant in variants}

    english_runs = draw(skin_words.models(skin_words._night_city_states))
    english = i18n.load_catalog("en").english()
    own = {key: _marked(value) for key, value in english.items() if key.startswith("widget.night_city.")}
    status = {key: _data_marked(value) for key, value in english.items() if key.startswith("widget.status.")}
    catalog = i18n.Catalog("en", {}, {**english, **status, **own})
    monkeypatch.setattr(i18n, "app_catalog", lambda language: catalog)
    models = skin_words.models(skin_words._night_city_states)
    data = {str(model.status_text) for model in models.values()}
    assert data == set(status.values())                          # every status word, marked as the view model's
    read: set[str] = set()
    real_text, real_names = i18n.Catalog.text, night_city.unknown_kind_names

    def text(self: Any, key: str, values: Any = None) -> Any:
        read.add(key)
        return real_text(self, key, values)

    def unknown_kind_names(kind: str) -> tuple[str, str]:
        names = real_names(kind)
        data.update(names)
        return names

    class Labels(dict):  # type: ignore[type-arg]
        """A guard's labels, saying which the skin read."""

        def get(self, key: str, default: Any = None) -> Any:
            label = super().get(key, default)
            data.add(str(casing.upper(label)))
            return label

    monkeypatch.setattr(i18n.Catalog, "text", text)
    monkeypatch.setattr(night_city, "unknown_kind_names", unknown_kind_names)
    for name, model in models.items():
        if model.guard is not None:
            models[name] = dataclasses.replace(model, guard=dataclasses.replace(model.guard,
                                                                               labels=Labels(model.guard.labels)))
    drawn = draw(models)
    marked = {token.upper(): token for value in own.values() for token in _tokens(value)
              if re.search("[A-Za-z]", token) and not token.startswith("{")}
    wrong, missing, words = [], [], 0
    for where, surfaces in drawn.items():
        for surface in surfaces:
            if not {run[0].upper() for run in _resolved(english_runs[where], surface)} <= {
                    run[0].upper() for run in _resolved(surfaces, surface)}:
                missing.append((where, surface))
            if isinstance(surfaces[surface], str):
                continue
            for run in surfaces[surface]:
                rest = run[0]
                for piece in sorted(filter(None, data), key=len, reverse=True):
                    rest = rest.replace(piece, " ")
                for token in _tokens(rest):
                    if token.upper() in marked:
                        words += token == marked[token.upper()]
                        if token != marked[token.upper()]:
                            wrong.append((where, surface, run[0], token))
    assert wrong == []
    assert read == set(own), sorted(read ^ set(own))
    assert missing == []
    assert words > 3000


def test_night_citys_row_chips_are_measured_round_the_words_they_draw(monkeypatch: pytest.MonkeyPatch) -> None:
    """A row's layout makes room for its chip (_chip_width), and _draw_row draws the chip round the catalog's word: the
    two are the same word in every language, so a longer WARN or CRIT moves the name, not the chip onto it (#95's
    plan critic 6). English keeps its widths."""
    from usage_monitor import i18n
    from usage_monitor.render import dump
    from usage_monitor.render.skins import RenderOptions
    from usage_monitor.render.skins import night_city as nc

    theme = dump.load_theme_objects()["night_city"]
    ink = nc._Ink(nc._canvas(nc.FLOAT_W, 96, 1.0, theme), nc.Palette.for_variant(theme, theme.variants[0]),
                  RenderOptions())
    colour = ink.pal.warn
    english = (nc._chip_width(ink, "warning"), nc._chip_width(ink, "critical"))
    assert english == (nc._chip_out(ink, 0, 0, "WARN", colour, colour), nc._chip_crit(ink, 0, 0, "CRIT"))
    words = {"widget.night_city.state.warning": "AVERTISSEMENT", "widget.night_city.state.critical": "CRITIQUE"}
    catalog, real = i18n.Catalog("fr", words, i18n.load_catalog("en").english()), i18n.app_catalog
    monkeypatch.setattr(i18n, "app_catalog", lambda language: catalog if language == "fr" else real(language))
    french = i18n.dev_locale("fr")
    warn = nc._chip_out(ink, 0, 0, "AVERTISSEMENT", colour, colour)
    assert nc._chip_width(ink, "warning", locale=french) == warn
    assert nc._chip_width(ink, "critical", locale=french) == nc._chip_crit(ink, 0, 0, "CRITIQUE")
    assert nc._chip_width(ink, "critical", "CAP 95", locale=french) == nc._chip_crit(ink, 0, 0, "CAP 95")
    assert warn > english[0] and nc._chip_width(ink, "warning") == english[0] and nc._chip_width(ink, "normal") == 0
    row = dump.sample_model("warning", locale=french).rows[0]
    boxes = nc._layout_row(ink, nc.look_for(theme.variants[0]), row, "warning", "78", None, locale=french)
    assert boxes.chip is not None and boxes.chip[1] - boxes.chip[0] == warn             # the row's room for the chip


# What Night City draws in English in every language, by design: data, not words. Nostromo's DATA_LEFTOVERS, and a
# scoped row's short name, which Night City shows alone.
NIGHT_CITY_DATA_LEFTOVERS: dict[str, str] = {
    **DATA_LEFTOVERS,
    "DESIGN": "a scoped row of a kind no catalog has words for: the row shows its short name, from its code "
              "(model.unknown_kind_names, #93 D4)",
}


def test_no_night_city_run_is_english_in_the_pseudo_locale() -> None:
    """Every run Night City draws in the pseudo-locale is the pseudo-locale's, as tests/english_left.py judges it: in
    every recorded state (the refused band, the edge and guard states and the states Night City's words hang on, which
    the frame dump lacks), on every surface, at 1.0, in Blackwall's, Relic's and Edgerun's looks. Names are taken out
    (the scan's widget names, the states' scope names as given and in capitals, the commands), and so are the
    pseudo-locale's format words (its weekdays, AM and PM). What stays English is data: NIGHT_CITY_DATA_LEFTOVERS,
    each drawn."""
    from usage_monitor import casing, i18n
    from usage_monitor.render import dump
    from usage_monitor.render.skins import get_skin

    pseudo = i18n.dev_locale(i18n.PSEUDO)
    models = skin_words.models(skin_words._night_city_states, pseudo)
    scopes = {scope for model in models.values()
              for scope in [*(row.scope_name for row in model.rows),
                            *(model.guard.scope_names.values() if model.guard is not None else ())] if scope}
    names = (*english_left.widget_names(), *i18n.COMMANDS, *sorted(scopes),
             *sorted(casing.upper(scope, pseudo.language) for scope in scopes))
    theme = dump.load_theme_objects()["night_city"]
    skin = get_skin("night_city")
    texts: dict[str, str] = {}
    for variant in (theme.variants[0], *(theme.variant(look) for look in skin_words.NIGHT_CITY_LOOKS)):
        for name, model in models.items():
            for surface, found in skin_words.runs(skin, model, theme, variant, dump.TASKBAR_HEIGHTS_AT_1X,
                                                  scales=(1.0,)).items():
                for run in ([] if isinstance(found, str) else found):
                    texts.setdefault(run[0], f"{variant.id} {name} {surface}")
    format_words = english_left.pseudo_format_words()
    left = english_left.english(texts, (*names, *NIGHT_CITY_DATA_LEFTOVERS), format_words)
    assert left == set(), {text: where for text, where in texts.items() if english_left.entry(text) in left}
    assert all(any(leftover in text for text in texts) for leftover in NIGHT_CITY_DATA_LEFTOVERS)
    assert {"Claude Code", "claude_code", "Haiku"} <= scopes
    refused = RECORDED["night_city"]["refused"]["runs"]["floating@1"]
    assert english_left.english([run[0] for run in refused], names, format_words) >= {
        "RELINK // RUN /login", "SESSION"}                                 # English is caught


# ── Gates' words come from the catalog (#96 step 2) ───────────────────────────

_PLACEHOLDERS = re.compile(r"(\{[A-Za-z_][A-Za-z0-9_]*\})")


def _swapped(english: str) -> str:
    """``english`` with the case of its first letter swapped, the first after a leading placeholder: "session",
    "{name} Weekly", "5h", "(Cap {percent})". A text with no letter is kept ("{name} {percent}")."""
    parts = _PLACEHOLDERS.split(english)
    for index in range(0, len(parts), 2):
        at = next((i for i, ch in enumerate(parts[index]) if ch.isalpha()), None)
        if at is not None:
            parts[index] = parts[index][:at] + parts[index][at].swapcase() + parts[index][at + 1:]
            break
    return "".join(parts)


def _gates_draw(models: dict[str, Any], scales: tuple[float, ...] = (1.0,)) -> dict[str, dict[str, Any]]:
    from usage_monitor.render import dump
    from usage_monitor.render.skins import get_skin

    theme = dump.load_theme_objects()["gates"]
    skin, variant = get_skin("gates"), theme.variants[0]
    return {name: skin_words.gates_runs(skin, model, theme, variant, dump.TASKBAR_HEIGHTS_AT_1X, scales=scales)
            for name, model in models.items()}


# The widget.gates keys English never draws in a recorded state: the hover pace's short form, for a line too tight for
# the whole pace (#96's fits), whose marked piece ("Pts") is also pace.ahead_by's and pace.behind_by's unmarked tail.
# The skin still reads it (it words the short form before it knows the line is tight), so the read check holds it.
GATES_NOT_DRAWN_IN_ENGLISH = ("widget.gates.pace.points",)


def _gates_pieces(english: dict[str, str]) -> dict[str, str]:
    """Each widget.gates text's pieces between its placeholders, English's to the marked one's, where the mark is,
    without the spaces round them (the guard bar may break its line there); a key English never draws
    (GATES_NOT_DRAWN_IN_ENGLISH) has no piece in a run to mark."""
    found: dict[str, str] = {}
    for key, value in english.items():
        if key.startswith("widget.gates.") and key not in GATES_NOT_DRAWN_IN_ENGLISH:
            for was, now in zip(_PLACEHOLDERS.split(value)[::2], _PLACEHOLDERS.split(_swapped(value))[::2]):
                was, now = was.strip(), now.strip()
                if was != now:
                    assert found.get(was, now) == now, (key, was)
                    found[was] = now
    return found


def _gates_mark(text: str, pieces: dict[str, str], data: set[str]) -> str:
    """``text`` (a run English drew) with each of Gates' pieces in it marked, the data in it left as it is."""
    kept: list[str] = []

    def keep(match: re.Match[str]) -> str:
        kept.append(match.group(0))
        return f"\x00{len(kept) - 1}\x00"

    if data:
        text = re.sub("|".join(map(re.escape, sorted(data, key=len, reverse=True))), keep, text)
    text = re.sub("|".join(map(re.escape, sorted(pieces, key=len, reverse=True))), lambda m: pieces[m.group(0)], text)
    return re.sub("\x00(\\d+)\x00", lambda m: kept[int(m.group(1))], text)


def _gates_same(english: str, marked: str, pieces: dict[str, str], data: set[str]) -> bool:
    """Whether ``marked`` is ``english`` with its pieces marked; where either is cut ("…"), whether the one is the
    other's start: a marked word is wider or narrower, so a cut falls elsewhere."""
    want = _gates_mark(english, pieces, data)
    if want == marked:
        return True
    if not (english.endswith("…") or marked.endswith("…")):
        return False
    a, b = want.removesuffix("…").rstrip(), marked.removesuffix("…").rstrip()
    return a.startswith(b) or b.startswith(a)


# Gates' marker test draws in a fresh Python with the time zone pinned, as the record was (TZ=UTC0): the wide resumes
# English's footer cuts are wide only at the record's clock. The script reads the marked widget.gates texts on stdin,
# draws every recorded state on every surface at 1.0 and 1.25 in English, then again with the catalog patched to the
# marked texts, Catalog.text spied on, and the data the skin read (a kind's label in sentence case, a guard's label)
# noted; it prints both sets of runs, the keys read and the data.
_GATES_MARKED = """
import dataclasses
import json
import sys

sys.path.insert(0, "tests")
import skin_words
from usage_monitor import i18n
from usage_monitor.render import dump
from usage_monitor.render.skins import gates, get_skin

own = json.loads(sys.stdin.read())
theme = dump.load_theme_objects()["gates"]
skin, variant = get_skin("gates"), theme.variants[0]
models = skin_words.models(skin_words._gates_states)


def draw():
    return {name: skin_words.gates_runs(skin, model, theme, variant, dump.TASKBAR_HEIGHTS_AT_1X)
            for name, model in models.items()}


english = draw()
catalog = i18n.Catalog("en", {}, {**i18n.load_catalog("en").english(), **own})
i18n.app_catalog = lambda language: catalog
data = {str(model.status_text) for model in models.values()}
read = set()
real_text, real_sentence = i18n.Catalog.text, gates._sentence


def text(self, key, values=None):
    read.add(key)
    return real_text(self, key, values)


def sentence(label):
    found = real_sentence(label)
    data.add(found)
    return found


class Labels(dict):
    def get(self, key, default=None):
        label = super().get(key, default)
        data.add(str(label))
        return label


i18n.Catalog.text, gates._sentence = text, sentence
for name, model in models.items():
    if model.guard is not None:
        models[name] = dataclasses.replace(model, guard=dataclasses.replace(model.guard,
                                                                           labels=Labels(model.guard.labels)))
marked = draw()
sys.stdout.reconfigure(encoding="utf-8")
print(json.dumps({"english": english, "marked": marked, "read": sorted(read), "data": sorted(data)},
                 ensure_ascii=False))
"""


def test_gates_takes_every_word_from_the_catalog_as_written() -> None:
    """Every word Gates draws comes from a Catalog.text call on one of its keys, drawn as the catalog writes it. The
    catalog is patched so each widget.gates text has the case of its first letter swapped ("session", "{name} Weekly",
    "5h", "hmm, we lost touch."), Catalog.text is spied on, and every recorded state is drawn on every surface at 1.0
    and 1.25 (_GATES_MARKED, at the record's clock). Each run is held to the run English draws in its place: English's
    with each of Gates' pieces marked and its data as it is (the view model's status word, a kind's label in sentence
    case, a guard's label), so a literal, a key read for another or a catalog text cased again fails. Gates' faces are
    proportional, so a marked word is wider or narrower: where a run is cut ("…", English's two wide resumes) the one
    need only start the other, and where the hover card's guard bar breaks its lines elsewhere its words, in order,
    are held instead. The skin reads every one of its keys and no other."""
    from usage_monitor import i18n

    english = i18n.load_catalog("en").english()
    own = {key: _swapped(value) for key, value in english.items() if key.startswith("widget.gates.")}
    pieces = _gates_pieces(english)
    done = subprocess.run([sys.executable, "-c", _GATES_MARKED], cwd=skin_words.REPO, input=json.dumps(own),
                          capture_output=True, encoding="utf-8", timeout=900, check=False,
                          env={**os.environ, "TZ": skin_words.PINNED_TZ, "PYTHONPATH": str(skin_words.REPO)},
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert done.returncode == 0, done.stderr[-3000:]
    found = json.loads(done.stdout)
    assert found["english"] == {name: words["runs"] for name, words in RECORDED["gates"].items()}    # the record's
    data = set(found["data"]) - {""}
    wrong, rewrapped, cut, marks = [], [], [], 0
    for name, surfaces in found["marked"].items():
        for surface in surfaces:
            was = [run[0] for run in _resolved(found["english"][name], surface)]
            now = [run[0] for run in _resolved(surfaces, surface)]
            if len(was) == len(now) and all(_gates_same(a, b, pieces, data) for a, b in zip(was, now)):
                for a, b in zip(was, now):
                    marks += a != b
                    cut += [(name, surface, a, b)] if _gates_mark(a, pieces, data) != b else []
                continue
            rewrapped.append((name, surface))                  # the guard bar's lines break elsewhere: its words
            if " ".join(_gates_mark(text, pieces, data) for text in was).split() != " ".join(now).split():
                wrong.append((name, surface, was, now))
    assert wrong == [], wrong[:4]
    assert set(found["read"]) == set(own), sorted(set(found["read"]) ^ set(own))
    assert marks > 3000
    assert {surface.split("@")[0] for _name, surface in rewrapped} == {"tooltip"}
    assert {(name, surface) for name, surface, _a, _b in cut} >= {
        (f"guard_hold/{wide}", f"floating@{scale}") for wide in ("resume_mon_10am", "resume_wed_1259pm")
        for scale in ("1", "1.25")}, cut
    assert all(a.endswith("…") or b.endswith("…") for _name, _surface, a, b in cut), cut


def test_gates_english_footers_are_the_catalogs() -> None:
    """gates._STATUS_COPY, which an older test reads, is no copy of the words: each status's footer in English, read
    from the catalog when asked, as the skin draws it for an English model of that status (#96's plan critic 1)."""
    from usage_monitor import i18n
    from usage_monitor.data.models import ApiStatus
    from usage_monitor.render import dump
    from usage_monitor.render.skins import gates

    en = i18n.load_catalog("en")
    assert set(gates._STATUS_COPY) == set(ApiStatus) - {ApiStatus.LIVE}
    for status, (lead_key, rest_key, role) in gates.FOOTER_KEYS.items():
        rest = str(en.text(rest_key, {"product": "Claude Code", "command": "/login"}))
        assert gates._STATUS_COPY[status] == ((str(en.text(lead_key)), " " + rest, role) if lead_key else
                                              ("", rest, role)), status
    for name in ("stale", "offline", "paused", "rate_limited", "no_credentials", "loading"):
        model = dump.sample_model(name)
        lead, rest, role = gates._STATUS_COPY[model.status]
        assert gates._footer_runs(model) == ([(lead, 600, role)] if lead else []) + [(rest, 400, "text_3")], name


# What Gates draws in English in every language, by design: data, not words.
GATES_DATA_LEFTOVERS: dict[str, str] = {
    "Design weekly": "a scoped row of a kind no catalog has words for, named from its code in sentence case "
                     "(gates._sentence, model.unknown_kind_names, #93 D4)",
    "design weekly": "the guard's own label for a limit of a kind no catalog has words for (#93 D4)",
    "Opus weekly": "a limit gone from the reading keeps the guard's own label in every language (#93 D4, owner "
                   "default)",
}


def test_no_gates_run_is_english_in_the_pseudo_locale() -> None:
    """Every run Gates draws in the pseudo-locale is the pseudo-locale's, as tests/english_left.py judges it: in every
    recorded state (the refused sign-in, the edge and guard states and the states Gates' words hang on, which the
    frame dump lacks), on every surface, at 1.0. Names are taken out (the scan's widget names, the commands, the
    states' scope names as given and as a row's name starts with them), and so are the pseudo-locale's format words
    (its weekdays, AM, PM and Gates' "min"). What stays English is data: GATES_DATA_LEFTOVERS, each drawn. The skin
    never cases a catalog text again: a pseudo-locale İ lowered by the default rule would draw a combining dot."""
    from usage_monitor import casing, i18n

    pseudo = i18n.dev_locale(i18n.PSEUDO)
    models = skin_words.models(skin_words._gates_states, pseudo)
    scopes = {scope for model in models.values()
              for scope in [*(row.scope_name for row in model.rows),
                            *(model.guard.scope_names.values() if model.guard is not None else ())] if scope}
    names = (*english_left.widget_names(), *i18n.COMMANDS, *sorted(scopes),
             *sorted(casing.cased("{name}", "sentence", pseudo.language, name=scope) for scope in scopes))
    texts: dict[str, str] = {}
    for name, surfaces in _gates_draw(models).items():
        for surface, found in surfaces.items():
            for run in ([] if isinstance(found, str) else found):
                texts.setdefault(run[0], f"{name} {surface}")
    format_words = english_left.pseudo_format_words()
    left = english_left.english(texts, (*names, *GATES_DATA_LEFTOVERS), format_words)
    assert left == set(), {text: where for text, where in texts.items() if english_left.entry(text) in left}
    assert all(any(leftover in text for text in texts) for leftover in GATES_DATA_LEFTOVERS)
    assert {"Claude Code", "claude_code", "Haiku"} <= scopes
    assert [text for text in texts if chr(0x307) in text] == []
    assert any(text.startswith("[Claude_code ") for text in texts)            # a name starting a row, in capitals
    refused = RECORDED["gates"]["refused"]["runs"]["floating@1"]
    assert english_left.english([run[0] for run in refused], names, format_words) >= {
        "Sign-in refused.", "Run /login in Claude Code."}                     # English is caught
