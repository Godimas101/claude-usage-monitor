"""Each skin's own words come from the catalog (#94 to #96), and English stays what it was.

tests/fixtures/skin_words_english.json holds each skin's English as it was before its words moved: every helper that
words a text and every run each surface draws, in every sample, edge and guard state and the states its words hang on,
at 1.0 and 1.25. tests/skin_words.py works them out in a fresh Python with the time zone pinned, since reset times are
local.
"""
from __future__ import annotations

import dataclasses
import json
import re
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
