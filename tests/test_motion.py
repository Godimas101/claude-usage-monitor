"""The dashboard's motion layer: motion.js, motion.css and the three themes' motion sheets and scripts.

Two kinds of test. The static ones read the files: what may loop, what may animate, that every rule is scoped to its theme
and its hover only where hover exists, that no hover rule moves, clips or animates its own target on :hover alone (it hangs
on the marks motion.js sets once per entry of the pointer), that motion stops for reduced motion, the Fluent numbers of the
Gates loading screen, the budgets, and the hygiene the rest of the dashboard's scripts keep. The node ones run motion.js on a
minimal fake DOM (no browser) and check the engine itself: the easing, the figure formats, the roller, the typer, the decoder,
the job loop, reduced motion (the setting and the OS preference, also switched on while something plays), the once-per-session
boot flags, the stagger clamp and the hover marks (an entry, a card that moves off a resting pointer and back, the cool-down).
They start node, so they need CUM_UI_TESTS=1 like every test that starts a process.

What the motion looks like in a real browser (frames, boots, performance) is checked by hand with Playwright; see
docs/UI_ARCHITECTURE.md, "Motion". The hover rule has a probe of its own that drives a real Chromium against the dev server:
tests/browser/hover_probe.js (not part of pytest).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from usage_monitor import paths

WEBUI = paths.resource_dir() / "usage_monitor" / "webui"
UI_TESTS = os.environ.get("CUM_UI_TESTS") == "1"
ui_only = pytest.mark.skipif(not UI_TESTS, reason="needs CUM_UI_TESTS=1 (starts a child process)")
# A child process of a test must not open a console window on the desktop of whoever runs the tests.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

THEME_IDS = ("nostromo", "night_city", "gates")
MOTION_CSS = ["motion.css"] + [f"themes/{theme_id}.motion.css" for theme_id in THEME_IDS]
SCRIPTS = ["motion.js"] + [f"themes/{theme_id}.js" for theme_id in THEME_IDS]


def read(name: str) -> str:
    return (WEBUI / name).read_text(encoding="utf-8")


def strip_js_comments(text: str) -> str:
    """The source without /* */ blocks and whole-line // comments (a // inside a string, as in a URL, stays)."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", text)


def strip_css_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def split_selectors(selector: str) -> list[str]:
    """A selector list split at its top-level commas (the commas inside :is(), :where(), :not() stay)."""
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(selector):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(selector[start:i].strip())
            start = i + 1
    parts.append(selector[start:].strip())
    return [p for p in parts if p]


class Rule:
    """One CSS block: its header (a selector list or an at-rule), the at-rules around it and its text."""

    def __init__(self, header: str, ancestors: tuple[str, ...], body: str) -> None:
        self.header = " ".join(header.split())
        self.ancestors = ancestors
        self.body = body

    @property
    def is_style_rule(self) -> bool:
        return not self.header.startswith("@")


def css_rules(css: str) -> list[Rule]:
    """Every block of a stylesheet, depth first. @media and @supports are entered (their rules carry them as ancestors);
    @keyframes, @property and the like are listed but their content is not read as rules."""
    css = strip_css_comments(css)
    rules: list[Rule] = []

    def walk(text: str, stack: tuple[str, ...]) -> None:
        i, n, start = 0, len(text), 0
        while i < n:
            ch = text[i]
            if ch == "{":
                header = text[start:i].strip()
                depth, j = 1, i + 1
                while j < n and depth:
                    depth += {"{": 1, "}": -1}.get(text[j], 0)
                    j += 1
                body = text[i + 1:j - 1]
                rules.append(Rule(header, stack, body))
                if header.startswith(("@media", "@supports")):
                    walk(body, stack + (" ".join(header.split()),))
                i, start = j, j
                continue
            if ch == ";":
                start = i + 1
            i += 1

    walk(css, ())
    return rules


CSS = {name: css_rules(read(name)) for name in MOTION_CSS}


# ── The files are there ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", MOTION_CSS + SCRIPTS)
def test_the_motion_files_exist(name: str) -> None:
    assert (WEBUI / name).is_file() and (WEBUI / name).stat().st_size > 500


@pytest.mark.parametrize("name", ["themes/nostromo.js", "themes/night_city.js", "themes/gates.js"])
def test_a_theme_script_takes_the_engine_as_optional(name: str) -> None:
    """The source half: every theme script reads the engine as `window.CUM_FX || null` and makes its director only when there is
    one. That a script then really works without it is test_a_theme_script_runs_without_the_engine, which runs it."""
    code = read(name)
    assert re.search(r"var FX = window\.CUM_FX \|\| null;", code)
    assert "FX ? FX.director(" in code


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_a_theme_script_gives_the_page_its_three_hooks(theme_id: str) -> None:
    code = read(f"themes/{theme_id}.js")
    assert f"CUM_THEME_FX.{theme_id} = {{ start: start }}" in code
    for hook, source in (("enter", "dir.enter"), ("cancel", "dir.cancel"), ("arrive", "FX.noticeCard")):
        assert re.search(rf"CUM_THEME_FX\.{theme_id}\.{hook} = {re.escape(source)};", code), (theme_id, hook)


def test_the_engine_exports_what_the_theme_scripts_use() -> None:
    code = read("motion.js")
    for name in ("FX.director", "FX.boot", "FX.watch", "FX.arrive", "FX.arriveIn", "FX.onReduced", "FX.noticeCard", "FX.jobs",
                 "FX.roller", "FX.typer", "FX.decoder", "FX.prepare", "FX.text", "FX.ghost", "FX.ease", "FX.easeInv"):
        assert re.search(rf"{re.escape(name)}\s*=", code), name
    used = set(re.findall(r"\bFX\.([A-Za-z]+)", "".join(read(f"themes/{t}.js") for t in THEME_IDS)))
    assert used <= set(re.findall(r"\bFX\.([A-Za-z]+)\s*=", code)) | {"boot", "text"}, sorted(used)


# ── Hygiene the scripts keep ──────────────────────────────────────────────────────

@pytest.mark.parametrize("name", SCRIPTS)
def test_the_motion_scripts_carry_no_inline_handlers_dynamic_code_or_literal_colours(name: str) -> None:
    code = strip_js_comments(read(name))
    assert not re.search(r"""\bon[a-z]+\s*=\s*["']""", code), "an inline event handler (the CSP forbids it)"
    assert "eval(" not in code and "new Function" not in code and "javascript:" not in code and "document.write" not in code
    assert not re.search(r"\b(console\.log|debugger)\b", code)
    assert not re.search(r"\b(TODO|FIXME|XXX)\b", read(name))
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(", code), "colours come from CSS variables"
    assert not re.findall(r"https?://", code), "the layer makes no requests"


@pytest.mark.parametrize("name", SCRIPTS)
def test_the_motion_scripts_use_storage_only_for_the_boot_flags_and_guard_it(name: str) -> None:
    code = strip_js_comments(read(name))
    assert "localStorage" not in code
    for m in re.finditer(r"sessionStorage\.", code):
        assert "try {" in code[max(0, m.start() - 260):m.start()], "storage used outside a try block"
    keys = set(re.findall(r'"([a-z]+-boot-session)"', code))
    assert keys <= {"nm-boot-session", "nc-boot-session", "g-boot-session"}


@pytest.mark.parametrize("name", MOTION_CSS)
def test_the_motion_sheets_hold_no_literal_colours_requests_or_imports(name: str) -> None:
    css = strip_css_comments(read(name))
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(", css), "colours come from the theme's variables"
    assert not re.search(r"@import|url\(|https?://", css)


# ── Reduced motion ────────────────────────────────────────────────────────────────

def test_the_shared_sheet_stops_every_animation_for_reduced_motion() -> None:
    rules = CSS["motion.css"]
    setting = [r for r in rules if r.is_style_rule and r.header.startswith('html[data-reduce-motion="1"] *')]
    assert setting and all("animation: none !important" in r.body and "transition: none !important" in r.body for r in setting)
    media = [r for r in rules if r.header.startswith("@media (prefers-reduced-motion: reduce)")]
    assert media and "animation: none !important" in media[0].body and "transition: none !important" in media[0].body


def test_nothing_in_the_layer_plays_without_the_run_class_a_placeholder_or_an_overlay() -> None:
    """Entrance motion hangs on main.fx-run (or a landing class); the rest is a placeholder, an overlay or a hover (the one-shot
    ones hang on [data-fx-play], which motion.js sets once per entry of the pointer, never on :hover)."""
    allowed = re.compile(r"\.fx-run|\.fx-land|data-fx-pend|data-fx\]|data-fx-play\]|\.fx-t\b|\.nc-boot|\.nc-b-|\.g-boot|\.g-b-|"
                         r"\.g-ring|\.nm-boot|:hover|:active|\.is-typing|\.fx-u|\.cum-|\.nm-od-|\.nm-cmp|\.is-")
    for theme_id in THEME_IDS:
        for rule in CSS[f"themes/{theme_id}.motion.css"]:
            if rule.is_style_rule and re.search(r"(?<![-\w])animation(-name)?\s*:", rule.body):
                assert allowed.search(rule.header), (theme_id, rule.header)


# ── What may loop, fill, animate ──────────────────────────────────────────────────

def test_only_placeholders_and_the_loading_orbit_loop() -> None:
    for theme_id in THEME_IDS:
        for rule in CSS[f"themes/{theme_id}.motion.css"]:
            if rule.is_style_rule and re.search(r"\binfinite\b", rule.body):
                assert re.search(r"data-fx-pend|\.g-ring", rule.header), (theme_id, rule.header)
    assert not [r for r in CSS["motion.css"] if re.search(r"\binfinite\b", r.body)]


def test_a_finished_animation_never_holds_a_value_on_page_content() -> None:
    """fill-mode forwards/both would keep an animated value after the entrance; only overlays and the one sweep line may."""
    allowed = re.compile(r"\.nc-boot|\.nc-b-|\.g-boot|\.g-b-|\.nm-card-in::after")
    for theme_id in THEME_IDS:
        for rule in CSS[f"themes/{theme_id}.motion.css"]:
            if rule.is_style_rule and re.search(r"\b(forwards|both)\b", rule.body):
                assert allowed.search(rule.header), (theme_id, rule.header)


CHEAP = {"transform", "opacity", "clip-path", "--t"}
SMALL_THINGS = {"filter", "text-shadow", "box-shadow", "background-color", "background", "background-position",
                "animation-timing-function"}
LAYOUT = {"width", "height", "top", "left", "right", "bottom", "margin", "padding", "font-size", "line-height", "border-width",
          "letter-spacing", "gap", "flex", "grid-template-columns", "min-width", "max-width"}


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_keyframes_animate_cheap_properties_or_small_things_and_never_layout(theme_id: str) -> None:
    seen: set[str] = set()
    for rule in CSS[f"themes/{theme_id}.motion.css"]:
        if rule.header.startswith("@keyframes"):
            seen |= set(re.findall(r"(--[a-z-]+|[a-z-]+)\s*:", rule.body))
    assert seen, "no keyframes found"
    assert not seen & LAYOUT, sorted(seen & LAYOUT)
    assert seen <= CHEAP | SMALL_THINGS, sorted(seen - CHEAP - SMALL_THINGS)
    assert seen & CHEAP >= {"transform", "opacity", "clip-path"}


def test_the_registered_property_used_for_counting_is_declared_once() -> None:
    assert len([r for r in CSS["motion.css"] if r.header.startswith("@property --t")]) == 1


# ── Scope and hover ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_every_rule_of_a_theme_sheet_is_scoped_to_its_theme(theme_id: str) -> None:
    scope = f'html[data-theme="{theme_id}"]'
    rules = [r for r in CSS[f"themes/{theme_id}.motion.css"] if r.is_style_rule]
    assert len(rules) > 20
    for rule in rules:
        if rule.header[:1].isdigit() or rule.header in ("from", "to"):
            continue                                              # a keyframe step
        for selector in split_selectors(rule.header):
            assert selector.startswith(scope), (theme_id, selector)


HOVER_HOOK = re.compile(r":hover|\[data-fx-hot\]|\[data-fx-play\]")
# what a rule does to the thing the pointer is on (or cuts out of it): the things a hover must not hang on :hover alone
MOVES_ITS_TARGET = re.compile(r"(?<![-\w])(animation|animation-name|transform|translate|scale|rotate|clip-path)\s*:")


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_hover_effects_only_exist_where_a_pointer_can_hover(theme_id: str) -> None:
    hovered = [r for r in CSS[f"themes/{theme_id}.motion.css"] if r.is_style_rule and HOVER_HOOK.search(r.header)]
    assert hovered
    for rule in hovered:
        assert any("hover: hover" in a for a in rule.ancestors), (theme_id, rule.header)


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_no_hover_rule_moves_cuts_or_animates_its_target_on_the_pseudo_class_alone(theme_id: str) -> None:
    """A rule that moves what the pointer is on (a lift, a nudge, a settle), cuts it (Night City's tear clips slits out of the
    card) or animates it takes its own ground away: the pointer rests at an edge or on a slit, the target moves off it, :hover
    goes, the target comes back, :hover returns, and the effect starts again, as long as the pointer rests there (Night City's
    glitch played about 30 times a second on a fill bar by the edge of the "Cache and thinking" card). So such a rule hangs on
    what motion.js sets from the pointer's entries: [data-fx-play] (a one-shot, once per entry) or [data-fx-hot] (a state, kept
    while the target moves under a pointer that has not)."""
    for rule in CSS[f"themes/{theme_id}.motion.css"]:
        if rule.is_style_rule and ":hover" in rule.header and MOVES_ITS_TARGET.search(rule.body):
            assert re.search(r"\[data-fx-(hot|play)\]", rule.header), (theme_id, rule.header)


def test_no_sheet_of_the_dashboard_moves_or_animates_a_target_on_hover_alone() -> None:
    """The same rule for every other sheet: the base themes' :hover rules colour things, light a border or draw a thumb larger
    inside a control that does not move; nothing there moves, clips or animates the element that the pointer is on."""
    for sheet in sorted(WEBUI.rglob("*.css")):
        if sheet.name.endswith(".motion.css"):
            continue
        for rule in css_rules(sheet.read_text(encoding="utf-8")):
            if rule.is_style_rule and ":hover" in rule.header and MOVES_ITS_TARGET.search(rule.body):
                assert False, (sheet.name, rule.header)


@pytest.mark.parametrize("theme_id", ["night_city", "nostromo"])
def test_a_one_shot_hover_animation_waits_for_a_quiet_page_and_for_the_entrance(theme_id: str) -> None:
    """The jitter, the tear, the fringe and the settle start only when the page is neither entering (fx-run) nor just
    redrawn under a resting pointer (fx-quiet); the menu and the buttons are not page content and are not gated."""
    gated = 0
    for rule in CSS[f"themes/{theme_id}.motion.css"]:
        if rule.is_style_rule and "[data-fx-play]" in rule.header and re.search(r"\.nm-card|\.nm-stat", rule.header) \
                and re.search(r"(?<![-\w])animation\s*:", rule.body):
            gated += 1
            assert ":not(.fx-run)" in rule.header and ":not(.fx-quiet)" in rule.header, (theme_id, rule.header)
    assert gated


def test_a_card_that_the_pointer_rests_on_keeps_its_look_when_the_page_is_redrawn() -> None:
    """fx-quiet takes the motion away, not the look: Nostromo's and Gates' hover is a state with a transition, so the
    transition is what the quiet page switches off (a refresh of the data must not un-light the card under the pointer).
    The state still answers to :hover itself, so that a card the redraw just made, under a pointer that has not moved, is lit
    at once, before motion.js has heard of it."""
    for theme_id in ("nostromo", "gates"):
        rules = CSS[f"themes/{theme_id}.motion.css"]
        hover = [r for r in rules if r.is_style_rule and re.search(r"\.nm-card:is\(:hover, \[data-fx-hot\]\)$", r.header)
                 and not re.search(r"(?<![-\w])animation\s*:", r.body)]
        assert hover and all(".fx-quiet" not in r.header for r in hover), theme_id
        quiet = [r for r in rules if r.is_style_rule and ".nm-main.fx-quiet" in r.header and "transition" in r.body]
        assert quiet, theme_id


def hover_targets() -> list[str]:
    """The selector list motion.js sets the hover marks on."""
    match = re.search(r'var HOVER_TARGETS = "([^"]+)";', read("motion.js"))
    assert match, "motion.js has no HOVER_TARGETS"
    return [part.strip() for part in match.group(1).split(",")]


def test_the_sheets_hang_hover_effects_on_marks_that_motion_js_sets_on_exactly_the_things_they_style() -> None:
    script = strip_js_comments(read("motion.js"))
    assert 'setAttribute("data-fx-hot"' in script and 'setAttribute("data-fx-play"' in script
    assert 'removeAttribute("data-fx-hot")' in script and 'removeAttribute("data-fx-play")' in script
    targets = hover_targets()
    # the one thing in the markup that is a card, a stat tile, a menu link or a button is a target; nothing else is
    assert targets == [".nm-card", ".nm-stat", ".nm-menu a", ".nm-refresh", ".nm-sw button", ".nm-btn"]
    for theme_id in THEME_IDS:
        for rule in CSS[f"themes/{theme_id}.motion.css"]:
            if rule.is_style_rule and re.search(r"\[data-fx-(hot|play)\]", rule.header):
                # a theme that hangs a hover on a new kind of element has to add it to HOVER_TARGETS (and to the list above)
                assert any(target in rule.header for target in targets), (theme_id, rule.header)


def test_every_one_shot_is_finite_and_ends_before_motion_js_takes_its_mark_off() -> None:
    play_ms = int(re.search(r"var PLAY_MS = (\d+);", read("motion.js")).group(1))
    seen = 0
    for theme_id in THEME_IDS:
        for rule in CSS[f"themes/{theme_id}.motion.css"]:
            if not (rule.is_style_rule and "[data-fx-play]" in rule.header):
                continue
            value = re.search(r"(?<![-\w])animation\s*:\s*([^;]+)", rule.body)
            assert value, (theme_id, rule.header)
            for one in split_selectors(value.group(1)):              # the comma-separated animations of the shorthand
                seen += 1
                assert "infinite" not in one and one.split()[-1] == "1", (theme_id, one)
                assert int(re.search(r"(\d+)ms", one).group(1)) <= play_ms - 100, (theme_id, one, play_ms)
    assert seen >= 7


def test_the_hover_marks_come_from_pointerenter_of_the_target_and_never_from_a_bubbling_mouseover() -> None:
    """pointerenter does not bubble, which is the point: a bar coming under the pointer is not a new entry of the card. The
    capturing listener on the document is how a non-bubbling event of every element is heard once."""
    script = strip_js_comments(read("motion.js"))
    assert 'document.addEventListener("pointerenter", onPointerEnter, true)' in script
    assert 'document.addEventListener("pointerleave", onPointerLeave, true)' in script
    assert "mouseover" not in script and "mouseenter" not in script and "pointerover" not in script
    for theme_id in THEME_IDS:
        assert "mouseover" not in strip_js_comments(read(f"themes/{theme_id}.js"))


# ── The Gates loading screen: Windows 11 style, Fluent numbers ────────────────────

def test_the_gates_sheet_uses_the_fluent_curves_and_durations() -> None:
    css = strip_css_comments(read("themes/gates.motion.css"))
    assert "cubic-bezier(0.1, 0.9, 0.2, 1)" in css                  # decelerate
    assert "cubic-bezier(0.7, 0, 1, 0.5)" in css                    # accelerate
    for ms in ("167ms", "250ms", "333ms"):
        assert ms in css, ms
    assert re.search(r"--gx-out:\s*cubic-bezier\(0\.1, 0\.9, 0\.2, 1\)", css)
    assert re.search(r"--gx-in:\s*cubic-bezier\(0\.7, 0, 1, 0\.5\)", css)


def test_the_gates_loading_screen_is_the_glyph_the_name_five_dots_and_a_status_line() -> None:
    code = read("themes/gates.js")
    assert "Claude Usage Monitor" in code and "Getting your usage ready" in code
    assert code.count("<i></i>") == 5
    assert 'querySelector(".nm-brand svg")' in code                  # the dashboard's own glyph, so the two never differ
    hold = int(re.search(r"BOOT_HOLD_MS = (\d+)", code).group(1))
    leave = int(re.search(r"BOOT_LEAVE_MS = (\d+)", code).group(1))
    assert leave == 333                                             # Fluent's long duration
    assert 1200 <= hold + leave <= 1800                             # 1.2 to 1.8 s in all
    for key in ('"click"', '"Escape"', '"Enter"', '" "'):
        assert key in code, key                                     # skippable by click and keys
    css = strip_css_comments(read("themes/gates.motion.css"))
    assert ".g-boot" in css and ".g-ring" in css and ".g-b-status" in css and ".g-boot.is-out" in css


def test_the_night_city_jack_in_ends_in_a_collapse_and_can_be_skipped() -> None:
    code = read("themes/night_city.js")
    assert "JACKING IN" in code and "ACCESS GRANTED" in code
    assert re.search(r"go: 2040", code) and re.search(r"hide: 240", code)
    for key in ('"click"', '"Escape"', '"Enter"', '" "'):
        assert key in code, key
    assert ".nc-boot" in read("themes/night_city.motion.css")


# ── Budgets and the stagger ───────────────────────────────────────────────────────

def test_the_entrances_end_inside_their_budgets() -> None:
    assert re.search(r"total: 1240", read("themes/nostromo.js"))            # Nostromo: 1.2 s and the cursor's last beat
    assert re.search(r"total: 940", read("themes/night_city.js"))
    assert re.search(r"total: 940", read("themes/gates.js"))
    assert re.search(r"var STAGGER_MAX = 8;", read("motion.js"))


def test_the_loading_states_are_styled_by_every_theme_for_every_kind_of_slot() -> None:
    for theme_id in THEME_IDS:
        css = strip_css_comments(read(f"themes/{theme_id}.motion.css"))
        for kind in ("num", "bar", "chart"):
            assert f'[data-fx-pend="{kind}"]' in css, (theme_id, kind)


HEAT_WIPE = {"nostromo": "nmx-draw", "night_city": "ncx-reveal", "gates": "gx-reveal"}


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_a_heat_map_comes_in_as_one_wipe_four_ms_a_column_at_most_400_ms(theme_id: str) -> None:
    """One animation for the whole map, not one for each cell: a year of days is about 540 cells, and a timeline and a style
    write apiece left History at 46 to 59 of 90 frames in its first 1.5 s at a 4x CPU slowdown (one wipe keeps 77 or more).
    The wipe goes left to right in a step a column (Gates: on its decelerate curve, fading up), 4 ms a column and 400 ms at most."""
    rules = CSS[f"themes/{theme_id}.motion.css"]
    cells = [r for r in rules if r.is_style_rule and ".cum-cell" in r.header and re.search(r"(?<![-\w])animation(-name)?\s*:", r.body)]
    assert not cells, [r.header for r in cells]                              # no cell has a timeline of its own
    wipes = [r for r in rules if r.is_style_rule and ".cum-calendar" in r.header and ".cum-matrix" in r.header]
    assert len(wipes) == 1, theme_id
    assert wipes[0].header.endswith(":is(.cum-calendar, .cum-matrix)")      # the svg itself, and what is in it comes with it
    body, name = wipes[0].body, HEAT_WIPE[theme_id]
    assert f"animation: {name} " in body and "min(var(--fx-cn, 1) * 4ms, 400ms)" in body
    if theme_id != "gates":
        assert "steps(var(--fx-cn, 1), end)" in body                         # a step a column, the way the theme draws everything
    else:
        assert "var(--gx-out)" in body and "+ 220ms" in body                 # the decelerate curve, and 220 ms to settle after the wipe
    keyframes = [r for r in rules if r.header == f"@keyframes {name}"]
    assert len(keyframes) == 1 and "clip-path: inset(0 100% 0 0)" in keyframes[0].body and "clip-path: inset(0 0 0 0)" in keyframes[0].body
    assert ("opacity" in keyframes[0].body) is (theme_id == "gates")


def test_the_heat_maps_say_how_many_columns_they_have_and_the_engine_passes_it_on() -> None:
    """charts.js puts data-cols on each heat map's svg; motion.js turns it into --fx-cn on that svg (once a map, not once a cell).
    Nothing writes or reads the old per-cell --fx-cc any more."""
    charts = read("charts.js")
    assert 'data-cols="' in charts
    assert charts.count('{ role: "group", nav: "grid", cols: cols }') == 2            # the calendar and the matrix
    assert '.setProperty("--fx-cn"' in read("motion.js")
    for name in ["charts.js", "app.js", *MOTION_CSS, *SCRIPTS]:
        assert "--fx-cc" not in read(name), name


def test_the_pending_marks_the_engine_reads_are_the_apps() -> None:
    """motion.js reads the marks app.js writes (data-pending, or .is-pending) and writes data-fx-pend for the sheets."""
    code = read("motion.js")
    assert 'var PENDING = "[data-pending], .is-pending";' in code
    assert "data-fx-pend" in code


# ── The engine on a minimal fake DOM (node) ───────────────────────────────────────

MOTION_CHECKS_JS = r"""
"use strict";
const vm = require("vm");
const fs = require("fs");
const path = require("path");

const webui = process.argv[2];

class TextNode {
  constructor(v) { this.nodeType = 3; this.nodeValue = v; this.parentNode = null; }
  get nextSibling() { return sibling(this); }
}

function sibling(n) {
  const p = n.parentNode;
  if (!p) { return null; }
  const i = p.childNodes.indexOf(n);
  return p.childNodes[i + 1] || null;
}

function detach(n) {
  if (n.parentNode) { n.parentNode.removeChild(n); }
}

class El {
  constructor(tag) {
    this.nodeType = 1;
    this.tagName = String(tag).toUpperCase();
    this.childNodes = [];
    this.parentNode = null;
    this._a = {};
    this._cls = new Set();
    this._lists = {};
    this._ls = {};
    this.rect = null;                          // what getBoundingClientRect answers; a hover scenario moves it
    this.gone = false;                         // a redraw took the element out of the page
    const vars = {};
    this.style = {
      setProperty(k, v) { vars[k] = String(v); },
      removeProperty(k) { delete vars[k]; },
      getPropertyValue(k) { return k in vars ? vars[k] : ""; },
      vars
    };
  }
  get firstChild() { return this.childNodes[0] || null; }
  get nextSibling() { return sibling(this); }
  get children() { return this.childNodes.filter((n) => n.nodeType === 1); }
  get className() { return Array.from(this._cls).join(" "); }
  set className(v) { this._cls = new Set(String(v).split(/\s+/).filter(Boolean)); }
  get classList() {
    const s = this;
    return {
      add(...c) { c.forEach((x) => s._cls.add(x)); },
      remove(...c) { c.forEach((x) => s._cls.delete(x)); },
      contains(c) { return s._cls.has(c); }
    };
  }
  setAttribute(k, v) { this._a[k] = String(v); if (k === "class") { this.className = v; } }
  getAttribute(k) { return k in this._a ? this._a[k] : null; }
  hasAttribute(k) { return k in this._a; }
  removeAttribute(k) { delete this._a[k]; }
  appendChild(n) { detach(n); this.childNodes.push(n); n.parentNode = this; return n; }
  insertBefore(n, ref) { detach(n); const i = ref ? this.childNodes.indexOf(ref) : this.childNodes.length; this.childNodes.splice(i, 0, n); n.parentNode = this; return n; }
  removeChild(n) { const i = this.childNodes.indexOf(n); if (i >= 0) { this.childNodes.splice(i, 1); n.parentNode = null; } return n; }
  replaceChild(n, old) { detach(n); const i = this.childNodes.indexOf(old); this.childNodes.splice(i, 1, n); n.parentNode = this; old.parentNode = null; return old; }
  get textContent() { return this.childNodes.map((c) => (c.nodeType === 3 ? c.nodeValue : c.textContent)).join(""); }
  // a page is a list of lists: main.querySelectorAll(selector) answers from what a scenario registered, nothing else
  querySelector(sel) { return (this._lists[sel] || [])[0] || null; }
  querySelectorAll(sel) { return (this._lists[sel] || []).slice(); }
  addEventListener(type, fn) { (this._ls[type] = this._ls[type] || []).push(fn); }
  // enough of a selector list for the hover targets: ".a", "tag" and ".a tag", separated by commas
  matches(sel) {
    return String(sel).split(",").some((part) => {
      const bits = part.trim().split(/\s+/);
      const last = bits[bits.length - 1];
      const own = last.startsWith(".") ? this._cls.has(last.slice(1)) : this.tagName === last.toUpperCase();
      if (!own) { return false; }
      if (bits.length === 1) { return true; }
      for (let n = this.parentNode; n; n = n.parentNode) { if (n._cls && n._cls.has(bits[0].slice(1))) { return true; } }
      return false;
    });
  }
  closest() { return null; }
  getBoundingClientRect() { return this.rect || { left: 0, top: 0, right: 0, bottom: 0 }; }
  get isConnected() { return !this.gone; }
  get offsetWidth() { return 100; }
}

/* One page-like world per scenario, with a manual clock, a manual requestAnimationFrame and manual timers. */
function world(opts) {
  opts = opts || {};
  const w = {
    now: 0, frames: [], timers: [], timerId: 0, observers: [], mediaListeners: [], store: {}, reducedPref: !!opts.reducedPref,
    listeners: {}, windowListeners: {}, hovering: []
  };
  const root = new El("html");
  root.setAttribute("data-theme", opts.theme || "gates");
  const main = new El("main");
  main.setAttribute("id", "main");
  const byId = { main };
  const sandbox = {
    console, Math, JSON, Object, Array, String, Number, RegExp, Set, WeakMap, Promise, Date, parseFloat, parseInt, isFinite, isNaN,
    queueMicrotask,
    document: {
      documentElement: root,
      visibilityState: "visible",
      createElement: (t) => new El(t),
      createTextNode: (t) => new TextNode(t),
      getElementById: (id) => byId[id] || null,
      addEventListener(type, fn) { (w.listeners[type] = w.listeners[type] || []).push(fn); },
      // the only query the engine makes of the whole document: what is still marked as under the pointer
      querySelectorAll: (sel) => (sel === "[data-fx-hot]" ? w.hovering.filter((e) => e.hasAttribute("data-fx-hot")) : [])
    },
    location: { hash: "#overview" }
  };
  sandbox.window = sandbox;
  sandbox.addEventListener = (type, fn) => { (w.windowListeners[type] = w.windowListeners[type] || []).push(fn); };
  sandbox.performance = { now: () => w.now };
  sandbox.requestAnimationFrame = (cb) => { w.frames.push(cb); return w.frames.length; };
  sandbox.setTimeout = (fn, ms) => { const id = ++w.timerId; w.timers.push({ id, at: w.now + (ms || 0), fn }); return id; };
  sandbox.clearTimeout = (id) => { w.timers = w.timers.filter((t) => t.id !== id); };
  sandbox.getComputedStyle = () => ({ color: "rest-colour", textShadow: "rest-shadow" });
  sandbox.matchMedia = () => ({
    get matches() { return w.reducedPref; },
    addEventListener(type, fn) { w.mediaListeners.push(fn); }
  });
  sandbox.MutationObserver = class { constructor(cb) { this.cb = cb; w.observers.push(this); } observe() {} disconnect() {} };
  sandbox.sessionStorage = {
    getItem(k) { if (opts.blockedStorage) { throw new Error("blocked"); } return k in w.store ? w.store[k] : null; },
    setItem(k, v) { if (opts.blockedStorage) { throw new Error("blocked"); } w.store[k] = String(v); }
  };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(path.join(webui, "motion.js"), "utf8"), sandbox, { filename: "motion.js" });
  w.FX = sandbox.CUM_FX;
  w.root = root;
  w.main = main;
  w.sandbox = sandbox;
  // runs the frames and the timers up to time t (ms), one frame per 16 ms
  w.advance = (t) => {
    while (w.now < t) {
      w.now = Math.min(t, w.now + 16);
      w.timers.filter((x) => x.at <= w.now).forEach((x) => { w.timers = w.timers.filter((y) => y !== x); x.fn(); });
      const run = w.frames; w.frames = [];
      run.forEach((cb) => cb(w.now));
    }
  };
  // a pointer event as the browser delivers it to the document's listeners (capturing ones see it first, which is all there are)
  w.fire = (type, target, x, y, extra) => {
    const ev = Object.assign({ type, target, clientX: x, clientY: y, pointerType: "mouse" }, extra || {});
    (w.listeners[type] || []).slice().forEach((fn) => fn(ev));
    return ev;
  };
  w.fireWindow = (type) => { (w.windowListeners[type] || []).slice().forEach((fn) => fn({ type })); };
  return w;
}

function figure(text, tag) {
  const el = new El(tag || "div");
  el.appendChild(new TextNode(text));
  return el;
}

const out = {};

// easing
{
  const { FX } = world();
  let mono = true;
  let prev = -1;
  for (let i = 0; i <= 200; i++) { const y = FX.ease(i / 200); if (y < prev - 1e-9) { mono = false; } prev = y; }
  let roundTrip = 0;
  for (let i = 1; i < 20; i++) { roundTrip = Math.max(roundTrip, Math.abs(FX.easeInv(FX.ease(i / 20)) - i / 20)); }
  const accelerate = FX.bezier(0.7, 0, 1, 0.5);
  out.easing = {
    ends: [FX.ease(0), FX.ease(1)], mid: FX.ease(0.5), early: FX.ease(0.1), monotonic: mono, roundTrip,
    accelerateMid: accelerate(0.5), accelerateEnds: [accelerate(0), accelerate(1)]
  };
}

// figure formats
{
  const { FX } = world();
  const t = FX.text;
  const a = t.parseNum("$1,107.50 x");
  const b = t.parseNum("13%");
  const c = t.parseNum("6.0×");
  out.formats = {
    money: a, moneyZero: t.fmtNum(a, 0), moneyFull: t.fmtNum(a, 1107.5), moneyHalf: t.fmtNum(a, 553.75),
    percent: b, percentMid: t.fmtNum(b, 6.4), times: c.val, timesDec: c.dec, timesMid: t.fmtNum(c, 3.04).length,
    none: t.parseNum("Mon")
  };
}

// roller
{
  const { FX } = world();
  const el = figure("$1,107.50");
  const style = { c: "colour", s: "shadow" };
  const job = FX.roller("o", el, 100, 500, style);
  const first = el._a["data-fx"];
  job.update(50);
  const before = el._a["data-fx"];
  job.update(350);
  const mid = el._a["data-fx"];
  const textKept = el.firstChild.nodeValue;
  const vars = { gc: el.style.vars["--fx-gc"], gs: el.style.vars["--fx-gs"] };
  const done = job.update(700);
  const after = { done, hasFx: el.hasAttribute("data-fx"), gc: el.style.vars["--fx-gc"] };
  const el2 = figure("13%");
  const j2 = FX.roller("o", el2, 0, 400, style);
  j2.cancel();
  const guards = ["0%", "Mon", "Oct 25 9:16", "0.00", "12 / 30"].map((s) => FX.roller("o", figure(s), 0, 400, style) === null);
  out.roller = { first, before, mid, textKept, vars, after, cancelled: { hasFx: el2.hasAttribute("data-fx") }, guards };
}

// typer
{
  const { FX } = world();
  const el = figure("ABCDEFGH");
  const node = el.firstChild;
  const job = FX.typer("o", node, 100, 44, 330, 90);
  const shape = el.childNodes.map((n) => n.className || "");
  const per = Math.min(44, 330 / 8);
  const texts = (t) => { job.update(t); return el.childNodes.map((n) => n.textContent); };
  const start = texts(99);
  const quarter = texts(100 + per * 3 + 1);
  const full = texts(100 + per * 8 - 1);
  const alwaysWhole = [99, 150, 200, 300, 420, 430].every((t) => texts(t).join("") === "ABCDEFGH");
  const done = job.update(100 + per * 8 + 90 + 1);
  out.typer = {
    shape, start, quarter, full, alwaysWhole, done, restored: el.childNodes.length === 1 && el.firstChild === node && node.nodeValue === "ABCDEFGH",
    empty: FX.typer("o", figure("").firstChild, 0, 10, 10, 0) === null
  };
  const el2 = figure("12345");
  const n2 = el2.firstChild;
  const j2 = FX.typer("o", n2, 0, 40, 200, 50);
  j2.update(60);
  j2.cancel();
  out.typer.cancelled = el2.childNodes.length === 1 && el2.firstChild === n2;
  const capped = figure("A".repeat(40));
  const jc = FX.typer("o", capped.firstChild, 0, 44, 330, 0);
  out.typer.capDoneAt = (() => { for (let t = 0; t < 1000; t += 5) { if (jc.update(t) === true) { return t; } } return -1; })();
}

// decoder
{
  const w = world();
  const { FX } = w;
  w.sandbox.Math = Object.create(Math);
  w.sandbox.Math.random = () => 0.99999;     // every scramble is the last hex digit or the last letter
  const el = figure("A1-b2");
  const job = FX.decoder("o", el, 100, 400, { c: "colour", s: "shadow" }, 38, false);
  const at = (t) => { job.update(t); return el._a["data-fx"]; };
  const early = job.update(50);
  const first = at(150);
  const lock0 = 100 + 400 * (0.22 + 0.78 * 1 / 5);
  const afterFirstLock = at(lock0 + 40);
  const throttled = (() => { const b = el._a["data-fx"]; job.update(lock0 + 41); return b === el._a["data-fx"]; })();
  const lock3 = 100 + 400 * (0.22 + 0.78 * 4 / 5);
  const nearEnd = at(lock3 + 40);
  const flagged = el.hasAttribute("data-fx-d");
  const done = job.update(500);
  out.decoder = {
    early, first, afterFirstLock, throttled, nearEnd, flagged, done,
    cleaned: !el.hasAttribute("data-fx") && !el.hasAttribute("data-fx-d") && !("--fx-gc" in el.style.vars),
    textKept: el.firstChild.nodeValue === "A1-b2", empty: FX.decoder("o", figure(""), 0, 100, null, 38, false) === null
  };
  const pre = figure("123");
  FX.decoder("o", pre, 1000, 400, null, 38, true);
  out.decoder.preroll = pre._a["data-fx"];
}

// the job loop
{
  const w = world();
  const { FX } = w;
  const log = [];
  const mk = (owner, until) => ({ owner, update: (t) => { log.push(owner); return t >= until; }, cancel: () => log.push("cancel-" + owner) });
  FX.jobs.add(mk("a", 100));
  FX.jobs.add(mk("b", 1000));
  const counts = [FX.jobs.count()];
  FX.jobs.cancel("b");
  counts.push(FX.jobs.count());
  w.advance(200);
  counts.push(FX.jobs.count());
  out.jobs = { counts, cancelled: log.filter((x) => x.startsWith("cancel")), idle: w.frames.length === 0 };
}

// reduced motion, the director
function director(w) {
  const made = [];
  const dir = w.FX.director({
    id: "gates", c0: 30, step: 30, total: 940, landMs: 700, barMs: 520, ghost: false, defer: false,
    card: [{ sel: ".nm-big", at: 70, dur: 500 }], fallback: { at: 70, dur: 500 },
    make: () => { made.push(1); return null; }
  });
  return { dir, made };
}

{
  const w = world();
  const { dir, made } = director(w);
  w.root.setAttribute("data-reduce-motion", "1");
  dir.enter(w.main, "overview");
  const bySetting = { run: w.main.classList.contains("fx-run"), jobs: w.FX.jobs.count(), reduced: w.FX.reduced() };
  const w2 = world({ reducedPref: true });
  const d2 = director(w2);
  d2.dir.enter(w2.main, "overview");
  const byPreference = { run: w2.main.classList.contains("fx-run"), reduced: w2.FX.reduced() };
  const w3 = world();
  const d3 = director(w3);
  d3.dir.enter(w3.main, "overview");
  const playing = w3.main.classList.contains("fx-run");
  w3.advance(500);
  const midway = w3.main.classList.contains("fx-run");
  d3.dir.cancel(w3.main);
  const cancelled = !w3.main.classList.contains("fx-run");
  d3.dir.enter(w3.main, "overview");
  w3.advance(w3.now + 940 + 41);
  const endedAlone = !w3.main.classList.contains("fx-run");
  out.reduced = { bySetting, byPreference, playing, midway, cancelled, endedAlone, made: made.length };
}

// reduced motion switched on while an entrance plays, and who is told
{
  const w = world();
  const { dir } = director(w);
  const told = [];
  w.FX.onReduced(() => { throw new Error("a subscriber that fails"); });
  w.FX.onReduced(() => told.push("second"));
  dir.enter(w.main, "overview");
  const before = w.main.classList.contains("fx-run");
  w.root.setAttribute("data-reduce-motion", "1");
  w.observers.forEach((o) => o.cb());
  const w2 = world();
  const d2 = director(w2);
  d2.dir.enter(w2.main, "overview");
  const told2 = [];
  w2.FX.onReduced(() => told2.push("x"));
  w2.observers.forEach((o) => o.cb());                      // nothing changed: not reduced, nothing is told, nothing ends
  const w3 = world();
  const d3 = director(w3);
  d3.dir.enter(w3.main, "overview");
  w3.reducedPref = true;
  w3.mediaListeners.forEach((fn) => fn());                  // the OS preference changed
  out.switched = {
    before, after: w.main.classList.contains("fx-run"), told, notReduced: { told: told2.length, run: w2.main.classList.contains("fx-run") },
    byPreference: !w3.main.classList.contains("fx-run")
  };
}

// the stagger clamp and the bars
{
  const w = world();
  const cards = [];
  for (let i = 0; i < 12; i++) { cards.push(new El("section")); }
  w.main._lists[".nm-card"] = cards;
  w.FX.prepare(w.main, {});
  const bar = new El("div");
  const cells = [];
  for (let i = 0; i < 10; i++) {
    const c = new El("i");
    if (i < 6) { c.classList.add("on"); } else if (i === 6) { c.classList.add("part"); c.style.setProperty("--f", "50"); }
    cells.push(c);
    bar.appendChild(c);
  }
  w.main._lists[".nm-cells, .nm-ratio"] = [bar];
  w.FX.prepare(w.main, { barMs: 520 });
  const lit = cells.slice(0, 7);
  const starts = lit.map((c) => Number(c.style.vars["--gk-d"]));
  const lens = lit.map((c) => Number(c.style.vars["--gk-l"]));
  const heat = new El("svg");
  heat.setAttribute("data-cols", "54");
  const heatCells = [];
  for (let i = 0; i < 6; i++) { const c = new El("rect"); c.setAttribute("data-c", String(Math.floor(i / 2))); heatCells.push(c); }
  heat._lists[".cum-cell"] = heatCells;
  const unsaid = new El("svg");                              // a heat map that does not say (an old chart): a one-step wipe
  const nonsense = new El("svg");
  nonsense.setAttribute("data-cols", "many");
  w.main._lists[".cum-calendar, .cum-matrix"] = [heat, unsaid, nonsense];
  w.FX.prepare(w.main, {});
  out.heat = {
    columns: [heat, unsaid, nonsense].map((svg) => svg.style.vars["--fx-cn"]),
    cellWrites: heatCells.map((c) => Object.keys(c.style.vars).length),
  };
  out.stagger = {
    order: cards.map((c) => Number(c.style.vars["--fx-i"])),
    barOrder: lit.map((c) => Number(c.style.vars["--fx-k"])),
    starts, lens, lastEnd: starts[6] + lens[6], unlit: cells.slice(7).every((c) => !("--gk-d" in c.style.vars))
  };
}

// boots: once per session, per theme
{
  const w = world();
  const b = w.FX.boot;
  const first = { gates: b.due("gates"), night_city: b.due("night_city"), nostromo: b.due("nostromo"), unknown: b.due("windows") };
  b.mark("gates");
  const after = { gates: b.due("gates"), night_city: b.due("night_city"), keys: Object.keys(w.store).sort() };
  const w2 = world({ blockedStorage: true });
  let threw = false;
  try { w2.FX.boot.mark("gates"); } catch (e) { threw = true; }
  out.boots = {
    first, after, blocked: { due: w2.FX.boot.due("gates"), threw }, reduced: world({ reducedPref: true }).FX.boot.due("gates"),
    curtainWhenDue: world({ theme: "night_city" }).root.getAttribute("data-fx-boot"),
    curtainWhenReduced: world({ theme: "night_city", reducedPref: true }).root.getAttribute("data-fx-boot")
  };
}

// hover: once per entry of the pointer (data-fx-hot while it is on the target, data-fx-play for the one-shots)
function hoverWorld(opts) {
  const w = world(opts);
  const card = new El("section");
  card.className = "nm-card";
  card.rect = { left: 100, top: 100, right: 300, bottom: 200 };
  const bar = new El("div");
  bar.className = "nm-cells";
  card.appendChild(bar);
  w.main.appendChild(card);
  w.hovering.push(card);
  w.card = card;
  w.bar = bar;
  w.plays = [];                                // when data-fx-play was put on the card
  const set = card.setAttribute.bind(card);
  card.setAttribute = (k, v) => { if (k === "data-fx-play") { w.plays.push(w.now); } set(k, v); };
  w.FX.watch(w.main);
  return w;
}
const marks = (el) => ({ hot: el.hasAttribute("data-fx-hot"), play: el.hasAttribute("data-fx-play") });
const HIGH = { left: 100, top: 98, right: 300, bottom: 198 };      // the card moved up 2 px (a lift, a jitter, a settle)
const HOME = { left: 100, top: 100, right: 300, bottom: 200 };

// one listener of each kind, however often the page is watched; an entry sets both marks, a child's entry sets none, the
// one-shot ends by itself and the card stays hot
{
  const w = hoverWorld();
  const listening = {
    enter: (w.listeners.pointerenter || []).length, leave: (w.listeners.pointerleave || []).length,
    move: (w.listeners.pointermove || []).length, blur: (w.windowListeners.blur || []).length
  };
  w.FX.watch(w.main);
  const again = { enter: (w.listeners.pointerenter || []).length, move: (w.listeners.pointermove || []).length };
  w.fire("pointerenter", w.card, 150, 150);
  const entered = marks(w.card);
  w.fire("pointerenter", w.bar, 150, 150);                  // a child coming under the pointer is not an entry
  const child = marks(w.bar);
  w.fire("pointerenter", w.card, 150, 150);                 // the card entered again while it is hot and playing: nothing new
  w.advance(330);
  out.hoverEntry = { listening, again, entered, child, settled: marks(w.card), plays: w.plays.length };
}

// the loop: the card moves off a pointer that rests at its edge and comes back, over and over; the pointer never moved
{
  const w = hoverWorld();
  w.fire("pointerenter", w.card, 150, 199);                 // 1 px inside the bottom edge
  const hot = [];
  for (let i = 0; i < 30; i++) {
    w.advance(w.now + 16);
    w.card.rect = HIGH;                                     // it moved off the pointer: the browser says the pointer left
    w.fire("pointerleave", w.card, 150, 199);
    hot.push(marks(w.card).hot);
    w.advance(w.now + 16);
    w.card.rect = HOME;                                     // and it came back: the browser says the pointer entered
    w.fire("pointerenter", w.card, 150, 199);
    hot.push(marks(w.card).hot);
  }
  w.advance(w.now + 1000);
  out.hoverLoop = { plays: w.plays.length, alwaysHot: hot.every((x) => x === true), after: marks(w.card) };
}

// the pointer really leaves: the mark goes at once, a quick return does not replay, a return after the cool-down does
{
  const w = hoverWorld();
  w.fire("pointerenter", w.card, 150, 150);
  w.advance(330);
  w.fire("pointerleave", w.card, 450, 150);                 // 150 px from the card: gone
  const left = marks(w.card);
  w.advance(w.now + 100);
  w.fire("pointerenter", w.card, 150, 150);                 // 100 ms later: inside the cool-down
  const quick = marks(w.card);
  const playsAfterQuick = w.plays.length;
  w.fire("pointerleave", w.card, 450, 150);
  w.advance(w.now + 450);
  w.fire("pointerenter", w.card, 150, 150);                 // 450 ms after leaving: a new entry
  const real = marks(w.card);
  const playsAfterReal = w.plays.length;
  w.advance(w.now + 330);
  w.fire("pointerleave", w.card, 150, 230);                 // 30 px under the card
  w.advance(w.now + 450);
  w.fire("pointerenter", w.card, 150, 150);
  out.hoverRearm = { left, quick, playsAfterQuick, real, playsAfterReal, playsAfterThird: w.plays.length, third: marks(w.card) };
}

// a card that moved off a resting pointer stays hot until the pointer is more than 4 px from where the card is now
{
  const w = hoverWorld();
  w.fire("pointerenter", w.card, 150, 199);
  w.advance(330);
  w.card.rect = HIGH;
  w.fire("pointerleave", w.card, 150, 199);                 // 1 px under the moved card
  const resting = marks(w.card).hot;
  w.fire("pointermove", w.main, 150, 201);                  // 3 px under it: still the same rest
  const nudged = marks(w.card).hot;
  w.fire("pointermove", w.main, 150, 205);                  // 7 px under it: the pointer is away
  const away = marks(w.card).hot;
  w.advance(w.now + 100);
  w.card.rect = HOME;
  w.fire("pointerenter", w.card, 150, 150);                 // inside the cool-down
  const quick = marks(w.card);
  out.hoverHold = { resting, nudged, away, quick, plays: w.plays.length };
}

// something else took the mark off a card the pointer never left: the browser's next pointerenter is not a new entry
{
  const w = hoverWorld();
  w.fire("pointerenter", w.card, 150, 150);
  w.advance(330);
  w.card.removeAttribute("data-fx-hot");
  w.fire("pointerenter", w.card, 150, 150);
  out.hoverStray = { plays: w.plays.length, marks: marks(w.card) };
}

// a held card that a redraw removed from the page is let go of at the next move
{
  const w = hoverWorld();
  w.fire("pointerenter", w.card, 150, 199);
  w.card.rect = HIGH;
  w.fire("pointerleave", w.card, 150, 199);
  const held = marks(w.card).hot;
  w.card.gone = true;
  w.fire("pointermove", w.main, 150, 199);
  out.hoverGone = { held, after: marks(w.card).hot };
}

// who plays and who does not: the entrance, a redraw under a resting pointer, reduced motion, a hidden page, a touch, a menu link
{
  const found = {};
  const entry = (name, setup, who) => {
    const w = hoverWorld();
    const nav = new El("nav");
    nav.className = "nm-menu";
    const link = new El("a");
    nav.appendChild(link);
    w.root.appendChild(nav);
    w.hovering.push(link);
    const other = new El("p");
    w.main.appendChild(other);
    setup(w);
    const target = who === "link" ? link : who === "other" ? other : w.card;
    w.fire("pointerenter", target, 150, 150, who === "touch" ? { pointerType: "touch" } : {});
    found[name] = marks(target);
    return w;
  };
  entry("plain", () => {});
  entry("entering", (w) => w.main.classList.add("fx-run"));
  entry("quiet", (w) => w.main.classList.add("fx-quiet"));
  entry("reduced", (w) => w.root.setAttribute("data-reduce-motion", "1"));
  entry("hidden", (w) => { w.sandbox.document.visibilityState = "hidden"; });
  entry("touch", () => {}, "touch");
  entry("menuWhileEntering", (w) => w.main.classList.add("fx-run"), "link");
  entry("notATarget", () => {}, "other");
  const quiet = entry("quietThenMove", (w) => w.main.classList.add("fx-quiet"));
  quiet.main.classList.remove("fx-quiet");                  // the pointer moved: the quiet page wakes
  quiet.fire("pointermove", quiet.card, 151, 150);
  found.quietThenMove = marks(quiet.card);
  out.hoverWho = found;
}

// the pointer leaves the window, the window loses focus, the page is hidden: nothing stays lit
{
  const w = hoverWorld();
  const stat = new El("div");
  stat.className = "nm-stat";
  stat.rect = { left: 400, top: 100, right: 500, bottom: 160 };
  w.main.appendChild(stat);
  w.hovering.push(stat);
  w.fire("pointerenter", w.card, 150, 150);
  w.fire("pointerenter", stat, 450, 150);
  const both = [marks(w.card).hot, marks(stat).hot];
  w.fire("pointerleave", w.root, 0, 0);
  const afterWindowLeave = [marks(w.card).hot, marks(stat).hot];
  w.advance(w.now + 450);
  w.fire("pointerenter", w.card, 150, 150);
  w.fireWindow("blur");
  const afterBlur = marks(w.card).hot;
  w.advance(w.now + 450);
  w.fire("pointerenter", stat, 450, 150);
  w.sandbox.document.visibilityState = "hidden";
  w.fire("visibilitychange", w.sandbox.document);
  out.hoverWindow = { both, afterWindowLeave, afterBlur, afterHidden: marks(stat).hot };
}

console.log(JSON.stringify(out));
"""


def node_binary() -> str:
    """The node executable. A machine without it skips these tests; CI (CI=true) fails instead, since a skipped
    script test is exactly how a broken script once shipped green."""
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI"):
            pytest.fail("node is not installed on this CI runner, so the motion engine cannot be tested")
        pytest.skip("node is not installed")
    return node


@pytest.fixture(scope="module")
def engine(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """What running motion.js on the fake DOM found out (one node run for the module)."""
    script = Path(tmp_path_factory.mktemp("motion")) / "motion_checks.js"
    script.write_text(MOTION_CHECKS_JS, encoding="utf-8")
    run = subprocess.run([node_binary(), str(script), str(WEBUI)], capture_output=True, text=True, timeout=60,
                         check=False, encoding="utf-8", creationflags=NO_WINDOW)
    assert run.returncode == 0, run.stderr[-800:]
    return json.loads(run.stdout)


THEME_SCRIPTS_JS = Path(__file__).resolve().parent / "theme_scripts_checks.js"


@pytest.fixture(scope="module")
def theme_scripts() -> dict[str, Any]:
    """What tests/theme_scripts_checks.js found out by running each theme script with no motion.js (one node run)."""
    run = subprocess.run([node_binary(), str(THEME_SCRIPTS_JS), str(WEBUI)], capture_output=True, text=True, timeout=60,
                         check=False, encoding="utf-8", creationflags=NO_WINDOW)
    assert run.returncode == 0, run.stderr[-800:]
    return json.loads(run.stdout)


@ui_only
@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_a_theme_script_runs_without_the_engine(theme_scripts: dict[str, Any], theme_id: str) -> None:
    """The page is complete and still without motion.js, so a theme script may use the engine only behind `if (FX)`. Run with
    no window.CUM_FX it has to load, register its start hook and nothing else (the motion hooks exist only with an engine), and
    start() (twice, as app.js calls it), the frames and timers it leaves behind, being switched off and on again and a
    reduced-motion preference must not throw. A later unguarded `FX.x()` took the whole theme script down and a test that read
    the source for `var FX = window.CUM_FX || null;` stayed green."""
    found = theme_scripts[theme_id]
    assert found["engine"] == "undefined", "the scenario must run with no engine"
    assert found["loaded"] is True and found["error"] is None, found["error"]
    assert found["startIsFunction"] is True and found["hooks"] == ["start"]
    for scenario in (found, found["reduced"]):
        assert scenario["start"] is None and scenario["restart"] is None, scenario
        assert scenario["settled"] is True and scenario["thrown"] == [], scenario
    assert found["themeSwitch"]["thrown"] == [], found["themeSwitch"]


@ui_only
def test_the_nostromo_boot_finishes_and_flags_the_session_with_no_engine_to_tell_it_the_page_is_ready(theme_scripts: dict[str, Any]) -> None:
    boot = theme_scripts["nostromoBoot"]
    assert boot["thrown"] == [] and boot["settled"] is True
    assert boot["flagged"] is True and boot["overlayHidden"] is True        # the log typed itself, the overlay left and the flag is set
    seen = theme_scripts["nostromoSeen"]
    assert seen["thrown"] == [] and seen["overlayHidden"] is True           # a second visit in the session shows no boot


@ui_only
def test_the_night_city_nav_decoration_is_added_once_and_goes_with_the_theme_with_no_engine(theme_scripts: dict[str, Any]) -> None:
    deco = theme_scripts["nightCityDecoration"]
    assert deco["thrown"] == []
    assert (deco["added"], deco["afterOff"], deco["afterOn"]) == (1, 0, 1)    # start() ran twice and made one; the theme left; it came back
    assert deco["hiddenAtFirst"] == "none"                                    # a theme that does not know it never shows it


@ui_only
@pytest.mark.parametrize("name", SCRIPTS)
def test_the_motion_scripts_parse(name: str) -> None:
    run = subprocess.run([node_binary(), "--check", str(WEBUI / name)], capture_output=True, text=True, timeout=60,
                         check=False, creationflags=NO_WINDOW)
    assert run.returncode == 0, run.stderr[-400:]


@ui_only
def test_the_fluent_decelerate_curve_is_the_one_every_roll_and_bar_uses(engine: dict[str, Any]) -> None:
    e = engine["easing"]
    assert e["ends"] == [0, 1] and e["monotonic"] is True
    assert e["early"] > 0.5 and 0.95 < e["mid"] < 0.98          # cubic-bezier(0.1, 0.9, 0.2, 1): fast out, long settle
    assert e["roundTrip"] < 1e-6                                # the bar timing inverts it
    assert e["accelerateEnds"] == [0, 1] and 0.05 < e["accelerateMid"] < 0.2      # cubic-bezier(0.7, 0, 1, 0.5)


@ui_only
def test_figures_are_parsed_and_formatted_with_their_prefix_decimals_commas_and_suffix(engine: dict[str, Any]) -> None:
    f = engine["formats"]
    assert f["money"] == {"pre": "$", "val": 1107.5, "dec": 2, "comma": True, "post": " x"}
    assert (f["moneyZero"], f["moneyFull"], f["moneyHalf"]) == ("$0.00 x", "$1,107.50 x", "$553.75 x")
    assert f["percent"]["val"] == 13 and f["percent"]["post"] == "%" and f["percentMid"] == "6%"
    assert f["times"] == 6 and f["timesDec"] == 1 and f["timesMid"] == 4
    assert f["none"] is None


@ui_only
def test_the_roller_counts_up_a_ghost_and_never_touches_the_real_text(engine: dict[str, Any]) -> None:
    r = engine["roller"]
    assert r["first"] == "$0.00" and r["before"] == "$0.00"
    assert r["mid"].startswith("$1,0") and r["mid"] != "$1,107.50"      # ease(0.5) of 1,107.50, with its comma and two decimals
    assert r["textKept"] == "$1,107.50"
    assert r["vars"] == {"gc": "colour", "gs": "shadow"}
    assert r["after"] == {"done": True, "hasFx": False} and r["cancelled"] == {"hasFx": False}
    assert r["guards"] == [True] * 5                                    # zero, words, a date, 0.00 and a ratio are left alone


@ui_only
def test_the_typer_types_a_copy_and_puts_the_original_text_back(engine: dict[str, Any]) -> None:
    t = engine["typer"]
    assert t["shape"] == ["fx-t", "fx-u"]
    assert t["start"] == ["", "ABCDEFGH"] and t["quarter"] == ["ABCD", "EFGH"] and t["full"] == ["ABCDEFGH", ""]
    assert t["alwaysWhole"] is True                                     # typed + the rest is always the whole text: nothing moves
    assert t["done"] is True and t["restored"] is True and t["cancelled"] is True and t["empty"] is True
    assert t["capDoneAt"] == 330                                        # a long text still ends in its cap


@ui_only
def test_the_decoder_locks_left_to_right_keeps_punctuation_and_leaves_no_trace(engine: dict[str, Any]) -> None:
    d = engine["decoder"]
    assert d["early"] is False
    assert d["first"] == "ZF-zF"                                        # digits scramble to hex digits, letters keep their case, "-" stays
    assert d["afterFirstLock"] == "AF-zF" and d["nearEnd"] == "A1-bF"   # character i locks at start + dur * (0.22 + 0.78 * (i + 1) / n)
    assert d["throttled"] is True and d["flagged"] is True
    assert d["done"] is True and d["cleaned"] is True and d["textKept"] is True and d["empty"] is True
    assert d["preroll"] == "FFF"


@ui_only
def test_one_frame_loop_runs_every_job_and_stops_when_none_is_left(engine: dict[str, Any]) -> None:
    j = engine["jobs"]
    assert j["counts"] == [2, 1, 0] and j["cancelled"] == ["cancel-b"] and j["idle"] is True


@ui_only
def test_reduced_motion_schedules_nothing_by_setting_or_by_preference(engine: dict[str, Any]) -> None:
    r = engine["reduced"]
    assert r["bySetting"] == {"run": False, "jobs": 0, "reduced": True}
    assert r["byPreference"] == {"run": False, "reduced": True}
    assert r["made"] == 0


@ui_only
def test_an_entrance_ends_when_cancelled_and_on_its_own_inside_its_budget(engine: dict[str, Any]) -> None:
    r = engine["reduced"]
    assert r["playing"] is True and r["midway"] is True and r["cancelled"] is True and r["endedAlone"] is True


@ui_only
def test_reduced_motion_switched_on_mid_entrance_ends_it_and_tells_every_subscriber(engine: dict[str, Any]) -> None:
    s = engine["switched"]
    assert s["before"] is True and s["after"] is False
    assert s["told"] == ["second"]                                      # the first subscriber threw; the second was still told
    assert s["notReduced"] == {"told": 0, "run": True}                  # a change that is not "reduced" stops nothing
    assert s["byPreference"] is True                                    # the OS preference counts like the setting


@ui_only
def test_the_stagger_is_clamped_and_a_bar_opens_its_cells_on_the_fluent_curve(engine: dict[str, Any]) -> None:
    s = engine["stagger"]
    assert s["order"] == [0, 1, 2, 3, 4, 5, 6, 7, 8, 8, 8, 8]           # the ninth card and every later one share the last slot
    assert s["barOrder"] == list(range(7))
    assert s["starts"][0] == 0 and s["starts"] == sorted(s["starts"])
    assert all(length >= 16 for length in s["lens"])
    assert 519 <= s["lastEnd"] <= 521                                   # the last cell ends with the bar: barMs = 520
    assert s["unlit"] is True                                           # an empty cell has no timing


@ui_only
def test_a_heat_map_is_told_its_column_count_once_and_its_cells_are_left_alone(engine: dict[str, Any]) -> None:
    heat = engine["heat"]
    assert heat["columns"] == ["54", "1", "1"]            # the count it says; one step for a map that says nothing sensible
    assert heat["cellWrites"] == [0] * 6                  # not one style write for a cell: that cost 540 of them a visit


@ui_only
def test_a_boot_plays_once_per_session_per_theme_and_never_under_reduced_motion(engine: dict[str, Any]) -> None:
    b = engine["boots"]
    assert b["first"] == {"gates": True, "night_city": True, "nostromo": True, "unknown": False}
    assert b["after"] == {"gates": False, "night_city": True, "keys": ["g-boot-session"]}
    assert b["blocked"] == {"due": True, "threw": False}                # blocked storage: the boot plays again, nothing throws
    assert b["reduced"] is False
    assert b["curtainWhenDue"] == "night_city" and b["curtainWhenReduced"] is None


# ── Hover: once per entry of the pointer ─────────────────────────────────────────

@ui_only
def test_an_entry_of_the_pointer_marks_the_target_hot_and_due_once_and_a_child_is_not_an_entry(engine: dict[str, Any]) -> None:
    h = engine["hoverEntry"]
    assert h["listening"] == {"enter": 1, "leave": 1, "move": 1, "blur": 1}     # capturing listeners on the document, once
    assert h["again"] == {"enter": 1, "move": 1}                                 # watching the page again does not listen twice
    assert h["entered"] == {"hot": True, "play": True}
    assert h["child"] == {"hot": False, "play": False}                           # pointerenter of a child (a bar) is not the card's
    assert h["settled"] == {"hot": True, "play": False}                          # the one-shot ended by itself; the card is still hot
    assert h["plays"] == 1


@ui_only
def test_a_card_that_moves_off_a_resting_pointer_and_back_never_plays_again(engine: dict[str, Any]) -> None:
    """The reported loop: the glitch moves the card, the pointer at its edge is off it, the card comes back, the pointer is on it
    again, and the glitch starts again. Thirty such leave-and-enter pairs without the pointer moving play the glitch once."""
    loop = engine["hoverLoop"]
    assert loop["plays"] == 1
    assert loop["alwaysHot"] is True                                              # and the lifted look never blinked off
    assert loop["after"] == {"hot": True, "play": False}


@ui_only
def test_a_pointer_that_really_left_clears_the_mark_and_a_return_plays_again_only_after_the_cool_down(engine: dict[str, Any]) -> None:
    r = engine["hoverRearm"]
    assert r["left"] == {"hot": False, "play": False}
    assert r["quick"] == {"hot": True, "play": False} and r["playsAfterQuick"] == 1      # 100 ms after leaving: lit, no replay
    assert r["real"] == {"hot": True, "play": True} and r["playsAfterReal"] == 2         # 450 ms after leaving: a new entry
    assert r["third"] == {"hot": True, "play": True} and r["playsAfterThird"] == 3


@ui_only
def test_a_card_that_moved_stays_hot_until_the_pointer_is_more_than_four_pixels_from_where_it_is_now(engine: dict[str, Any]) -> None:
    h = engine["hoverHold"]
    assert h["resting"] is True and h["nudged"] is True and h["away"] is False
    assert h["quick"] == {"hot": True, "play": False} and h["plays"] == 1


@ui_only
def test_an_entry_the_pointer_did_not_make_after_the_mark_was_lost_does_not_replay(engine: dict[str, Any]) -> None:
    """A target is armed again only when the pointer really leaves it, not when its mark goes missing."""
    stray = engine["hoverStray"]
    assert stray["plays"] == 1 and stray["marks"] == {"hot": True, "play": False}


@ui_only
def test_a_held_card_that_a_redraw_removed_from_the_page_is_let_go(engine: dict[str, Any]) -> None:
    assert engine["hoverGone"] == {"held": True, "after": False}


@ui_only
def test_the_one_shot_waits_for_the_entrance_a_quiet_page_and_motion_and_never_comes_from_a_touch(engine: dict[str, Any]) -> None:
    who = engine["hoverWho"]
    lit_and_playing = {"hot": True, "play": True}
    lit_not_playing = {"hot": True, "play": False}
    assert who["plain"] == lit_and_playing
    assert who["entering"] == lit_not_playing               # main.fx-run: the cards are still coming in
    assert who["quiet"] == lit_not_playing                  # main.fx-quiet: the page was redrawn under a resting pointer
    assert who["quietThenMove"] == lit_not_playing          # and the pointer moving inside the card afterwards is not an entry
    assert who["reduced"] == lit_not_playing                # reduced motion keeps the look and takes the motion
    assert who["hidden"] == lit_not_playing
    assert who["touch"] == {"hot": False, "play": False}    # a finger is not a hover
    assert who["notATarget"] == {"hot": False, "play": False}
    assert who["menuWhileEntering"] == lit_and_playing      # the menu is not page content: the entrance does not hold it back


@ui_only
def test_nothing_stays_lit_when_the_pointer_leaves_the_window_the_window_blurs_or_the_page_is_hidden(engine: dict[str, Any]) -> None:
    w = engine["hoverWindow"]
    assert w["both"] == [True, True]
    assert w["afterWindowLeave"] == [False, False]
    assert w["afterBlur"] is False and w["afterHidden"] is False
