"""Static checks on the dashboard's web files: usage_monitor/webui (index.html, app.js, charts.js, the
theme files of all three themes).

No browser, no window, no server and no network: the files are read as text and checked against the
Python side they depend on (the statistics routes, the settings whitelist, the window names) and
against the rules the page has to keep (no inline handlers, no literal colours, no external addresses,
every figure explained, motion honoured). The one test that starts a process (node --check) is skipped
unless CUM_UI_TESTS=1, like every test that starts a process.

The scripts themselves run under node, in tests/webui_checks.js: charts.js and app.js are loaded into a vm
context with a small fake DOM and window.__CUM_TEST__ set, so app.js hands back its helpers (the test hook at its
end) and the tests below assert on what they return: escaping, the percent formats, the pace band, the model mix,
the currency of real money, the plan multiple, the health rows, the plan price, the card layout of every page before
any number has arrived, the update banner, the forecast line. A syntax error, a lost x100 or an unescaped title fails
there. Those tests start node, so they need CUM_UI_TESTS=1 like every test that starts a
process; .github/workflows/tests.yml sets it for this file (and a test below checks that it does).

What these cannot show is how the page looks and behaves; that is checked by loading it in a browser
against the dev server (python -m usage_monitor.ui.devserver).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest

from usage_monitor import paths, themes
from usage_monitor.data import analytics, windows
from usage_monitor.render import model as widget_model
from usage_monitor.themes import colour
from usage_monitor.ui import server

WEBUI = paths.resource_dir() / "usage_monitor" / "webui"
CHECKS_JS = Path(__file__).resolve().parent / "webui_checks.js"
WORKFLOW = paths.resource_dir() / ".github" / "workflows" / "tests.yml"
UI_TESTS = os.environ.get("CUM_UI_TESTS") == "1"
ui_only = pytest.mark.skipif(not UI_TESTS, reason="needs CUM_UI_TESTS=1 (starts a child process)")
# A child process of a test must not open a console window on the desktop of whoever runs the tests.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

PAGES = ["overview", "limits", "cost", "efficiency", "history", "settings", "about"]
ALERT_KEYS = ["alerts_enabled", "alert_thresholds", "alert_on_threshold", "alert_on_reset",
              "alert_on_credits", "alert_quiet_hours"]


def read(name: str) -> str:
    return (WEBUI / name).read_text(encoding="utf-8")


APP = read("app.js")
CHARTS = read("charts.js")
INDEX = read("index.html")
CSS = read("themes/nostromo.css")
FX = read("themes/nostromo.js")
THEME_IDS = ("nostromo", "night_city", "gates")
THEME_CSS = {theme_id: read(f"themes/{theme_id}.css") for theme_id in THEME_IDS}
THEME_JS = {theme_id: read(f"themes/{theme_id}.js") for theme_id in THEME_IDS}
ALL_SCRIPTS = {"app.js": APP, "charts.js": CHARTS,
               **{f"themes/{theme_id}.js": THEME_JS[theme_id] for theme_id in THEME_IDS}}


def strip_comments(text: str) -> str:
    """The source without /* */ blocks and whole-line // comments (a // inside a string, as in a URL, stays)."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", text)


def function_body(source: str, name: str) -> str:
    """The text of ``function name(...) { ... }``, found by counting braces outside string literals and comments."""
    start = source.index(f"function {name}(")
    depth, i, quote = 0, source.index("{", start), ""
    begin = i
    while i < len(source):
        ch = source[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = ""
        elif source.startswith("//", i):                  # a comment may hold an apostrophe or a brace
            i = source.index("\n", i)
        elif source.startswith("/*", i):
            i = source.index("*/", i) + 1
        elif ch in "\"'":
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[begin:i + 1]
        i += 1
    raise AssertionError(f"no end found for function {name}")


APP_CODE = APP[APP.index("(function () {"):]          # without the header comment
APP_DOC = APP[:APP.index("(function () {")]           # the markup contract for theme authors


def why_table() -> dict[str, str]:
    block = APP[APP.index("  var WHY = {"):APP.index("  var S = {")]
    return dict(re.findall(r'^    "([a-z0-9-]+)": "([^"\n]*)",?$', block, re.M))


# ── The pages and the statistics they read ────────────────────────────────────────

def test_no_placeholder_pages_remain() -> None:
    assert "Phase 2" not in APP
    assert "futurePanel" not in APP
    assert "arrives with analytics" not in APP
    assert "nm-future" not in CSS and "nm-future" not in APP


def test_the_router_and_the_nav_name_the_same_pages() -> None:
    routed = re.findall(r'\{ key: "([a-z]+)", label:', APP)
    nav = re.findall(r'data-page="([a-z]+)"', INDEX)
    assert routed == PAGES
    assert nav == PAGES


def test_every_statistics_report_is_read_through_its_real_route() -> None:
    calls = re.findall(r'statEntry\("([a-z]+)"(?:, \{([^}]*)\})?\)', APP_CODE)
    names = {name for name, _ in calls}
    assert names <= set(server.ANALYTICS_NAMES)
    # sessions is not read: the cost report already carries the five costliest sessions of the week
    assert names == set(server.ANALYTICS_NAMES) - {"sessions"}
    assert "top_sessions_week" in APP_CODE
    for name, params in calls:
        asked = set(re.findall(r"(\w+):", params))
        assert asked <= set(server.REPORT_PARAMS[name]), f"{name} asks for {asked - set(server.REPORT_PARAMS[name])}"
    # the URL is built from the name, so a typo in a name cannot hide in a string
    assert '"/api/analytics/" + key' in APP_CODE


def test_the_windows_are_the_ones_the_server_knows() -> None:
    order = re.search(r"var WINDOW_ORDER = \[([^\]]*)\]", APP_CODE).group(1)
    assert tuple(re.findall(r'"(\w+)"', order)) == analytics.WINDOW_NAMES
    seg = re.search(r"var WINDOW_SEG = \[(.*?)\];", APP_CODE, re.S).group(1)
    assert tuple(re.findall(r'\["(\w+)", "', seg)) == analytics.WINDOW_NAMES
    for table in ("WINDOW_LABEL", "WINDOW_PHRASE"):
        body = re.search(rf"var {table} = \{{(.*?)\}};", APP_CODE, re.S).group(1)
        assert set(re.findall(r"(\w+): \"", body)) == set(analytics.WINDOW_NAMES), table


def test_export_goes_through_the_bridge_and_falls_back_to_the_route() -> None:
    assert "save_export" in APP_CODE
    assert "/api/export?format=" in APP_CODE
    assert "X-Monitor-Token" in APP_CODE
    assert set(re.findall(r'data-format="(\w+)"', APP_CODE)) == set(server.EXPORT_FORMATS)
    # the page names only the format: no path goes to the bridge
    assert re.findall(r"save_export\(([^)]*)\)", APP_CODE) == ["fmt"]


def test_the_history_page_has_both_heatmaps_the_odometer_and_the_streaks() -> None:
    body = function_body(APP_CODE, "historyPage")
    for needle in ("CHARTS.calendar", "CHARTS.matrix", "odometerHTML", "streak-current", "streak-longest",
                   "busiest-day", "first-use", 'data-action="export"'):
        assert needle in body, needle
    assert "WEEKDAYS_MON" in APP_CODE and APP_CODE.count('"MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"') >= 1


def test_money_is_labelled_apart_from_list_price_cost() -> None:
    cost = function_body(APP_CODE, "costPage")
    assert "is-real" in cost and "real money" in cost
    assert "BASIS" in cost                       # "API-equivalent at list prices"
    assert "Fast mode" in cost and "usage credits" in cost
    assert "fast.requests > 0" in cost           # the callout only when there is fast-mode use


# ── Settings ──────────────────────────────────────────────────────────────────────

def posted_setting_keys() -> set[str]:
    keys: set[str] = set()
    for pattern in (r'toggleRow\("([a-z_]+)"', r'selectRow\("([a-z_]+)"', r'capRow\("([a-z_]+)"', r'data-key="([a-z_]+)"',
                    r"saveSettings\(\{ ([a-z_]+):", r"patch = \{ ([a-z_]+):"):
        keys.update(re.findall(pattern, APP_CODE))
    return keys


def test_every_setting_the_page_posts_is_on_the_servers_whitelist() -> None:
    keys = posted_setting_keys()
    assert keys, "found no settings in the page"
    assert keys <= set(server.SETTING_SPECS), keys - set(server.SETTING_SPECS)
    assert set(ALERT_KEYS) <= keys
    assert {"plan_price_override", "billing_day"} <= keys


def test_the_retired_notifications_key_is_gone() -> None:
    assert "notifications_enabled" not in APP


def test_the_forms_apply_the_servers_own_limits() -> None:
    specs = server.SETTING_SPECS
    price = specs["plan_price_override"].check
    assert price(10_000) and not price(10_001) and not price(0) and price(0.01)
    assert re.search(r"value <= 0 \|\| value > 10000", APP_CODE)       # the form refuses what the server would
    levels = specs["alert_thresholds"].check
    assert levels([1, 2, 3, 4, 5]) and not levels([1, 2, 3, 4, 5, 6]) and not levels([0]) and not levels([101])
    assert "levels.length >= 5" in APP_CODE and re.search(r"\+raw < 1 \|\| \+raw > 100", APP_CODE)
    assert specs["billing_day"].check(31) and not specs["billing_day"].check(32)
    assert "{ length: 31 }" in APP_CODE
    quiet = specs["alert_quiet_hours"].check
    assert quiet({"start": "22:00", "end": "07:00"}) and not quiet({"start": "24:00", "end": "07:00"})
    assert r"/^\d\d:\d\d$/" in APP_CODE


def test_settings_errors_are_shown_inline_and_announced() -> None:
    assert 'role="alert"' in function_body(APP_CODE, "errLine")
    field_error = function_body(APP_CODE, "fieldError")
    assert "aria-invalid" in field_error
    for key in ("alert_thresholds", "alert_quiet_hours", "plan_price_override", "billing_day"):
        assert f'errLine("{key}")' in APP_CODE, key
    # a refusal from the server lands under the field, with the server's words
    assert "errorText(res)" in function_body(APP_CODE, "saveSettings")
    # a change that moves the numbers refetches them
    assert "invalidateStats" in function_body(APP_CODE, "saveSettings")


# ── Figures explain themselves ────────────────────────────────────────────────────

def test_every_figure_has_a_one_line_explanation() -> None:
    why = why_table()
    used = set(re.findall(r'(?:statTile|statBlock)\("([a-z0-9-]+)"', APP_CODE))
    assert used == set(why), (used - set(why), set(why) - used)
    assert len(used) >= 40
    for key, text in why.items():
        assert text[:1].isupper() and text.endswith("."), key
        assert 20 < len(text) <= 140, (key, len(text))
    assert len(set(why.values())) == len(why), "two figures share one explanation"


def test_a_stat_is_a_focusable_group_with_a_title_and_a_description() -> None:
    for name in ("statTile", "statBlock"):
        body = function_body(APP_CODE, name)
        for needle in ('role="group"', 'tabindex="0"', "title=", "aria-describedby=", "aria-labelledby=", "data-tip=", "nm-why nm-sr"):
            assert needle in body, (name, needle)


def test_the_one_tooltip_is_in_the_shell_and_wired() -> None:
    assert re.search(r'<div class="nm-tip" id="tip" role="tooltip" hidden></div>', INDEX)
    assert "CHARTS.bind(main, tipEl)" in APP_CODE
    bind = function_body(CHARTS, "bind")
    for needle in ("mouseover", "focusin", "focusout", "Escape", "data-tip", "aria-label", "keydown"):
        assert needle in bind, needle


# ── Hygiene the page depends on ───────────────────────────────────────────────────

@pytest.mark.parametrize("name", list(ALL_SCRIPTS))
def test_no_inline_handlers_or_dynamic_code(name: str) -> None:
    code = strip_comments(ALL_SCRIPTS[name])
    assert not re.search(r"""\bon[a-z]+\s*=\s*["']""", code), "an inline event handler (the CSP forbids it)"
    assert "eval(" not in code and "new Function" not in code and "javascript:" not in code
    assert "document.write" not in code
    assert not re.search(r"\b(console\.log|debugger)\b", code)
    assert not re.search(r"\b(TODO|FIXME|XXX)\b", ALL_SCRIPTS[name])


def test_index_html_is_the_shell_the_server_and_the_scripts_expect() -> None:
    assert INDEX.count('<meta name="monitor-token" content="">') == 1     # the server fills this in
    scripts = re.findall(r'<script src="([^"]+)"', INDEX)
    assert scripts[-2:] == ["/static/charts.js", "/static/app.js"]          # app.js last: it starts the page
    assert set(scripts[:-2]) <= {"/static/motion.js"}                       # and only the motion layer before them
    assert not re.search(r"<script(?![^>]*\bsrc=)", INDEX), "an inline script (the CSP forbids it)"
    for needle in ('id="theme-css"', 'id="main"', 'id="menu"', 'id="variants"', 'id="refresh"', 'id="boot"'):
        assert needle in INDEX, needle


def test_the_pages_make_no_requests_beyond_the_monitor_and_the_about_links() -> None:
    links = re.search(r"var LINKS = \{(.*?)\};", APP_CODE, re.S).group(1)
    allowed = set(re.findall(r'"(https://[^"]+)"', links))
    assert len(allowed) == 4          # the repository, the bug form, Patreon, and the guard's manual setup (docs/AGENT_HOOK.md)
    namespaces = {"http://www.w3.org/2000/svg"}          # the name of the SVG markup language: an identifier, never fetched
    for name, text in ALL_SCRIPTS.items():
        assert set(re.findall(r"https?://[^\s\"')]+", strip_comments(text))) <= allowed | namespaces, name
    for theme_id, css in THEME_CSS.items():
        assert not re.findall(r"https?://", strip_comments(css)), theme_id
        assert not re.search(r"@import|url\(\s*(?![\"']?/static/)", strip_comments(css)), theme_id
        for path in re.findall(r'url\("(/static/[^"]+)"\)', css):
            assert (WEBUI / path[len("/static/"):]).is_file(), (theme_id, path)


@pytest.mark.parametrize("name", list(ALL_SCRIPTS))
def test_scripts_carry_no_literal_colours(name: str) -> None:
    code = strip_comments(ALL_SCRIPTS[name])
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(", code), "colours come from CSS variables"


def test_chart_styles_only_use_variables() -> None:
    style = re.search(r"var STYLE = \[(.*?)\]\.join", CHARTS, re.S).group(1)
    painted = re.findall(r"\b(fill|stroke|background|color):([^;\"}]+)", style)
    assert len(painted) > 20
    for prop, value in painted:
        assert value.strip() in ("none", "transparent", "currentColor") or value.strip().startswith("var("), (prop, value)
    assert "--chart-mark" in style and "--chart-hot" in style and "--chart-text" in style


def test_storage_is_only_for_preferences_and_always_guarded() -> None:
    for source in (APP_CODE, *(strip_comments(js) for js in THEME_JS.values())):
        for m in re.finditer(r"(?:local|session)Storage\.", source):
            assert "try {" in source[max(0, m.start() - 260):m.start()], "storage used outside a try block"
    assert APP_CODE.count("localStorage.") == 2                  # one read, one write: the viewer's choices
    allowed = re.search(r"var allowed = \{(.*?)\};", APP_CODE, re.S).group(1)
    assert set(re.findall(r"(\w+):", allowed)) == {"costWindow", "efficiencyWindow", "folderMode", "calMetric", "matrixMetric"}


# ── Charts ────────────────────────────────────────────────────────────────────────

def test_charts_js_offers_every_chart_the_pages_need() -> None:
    exported = re.search(r"root\.CUMCharts = \{(.*?)\};", CHARTS, re.S).group(1)
    for name in ("sparkline", "line", "columns", "hbars", "stackedBar", "calendar", "matrix", "bind",
                 "niceStep", "levels", "refresh", "pips"):
        assert re.search(rf"\b{name}\b", exported), name
    for call in ("CHARTS.calendar(", "CHARTS.matrix(", "CHARTS.hbars(", "CHARTS.stackedBar(", "CHARTS.line(",
                 "CHARTS.columns(", "CHARTS.sparkline("):
        assert call in APP_CODE, call


def test_chart_items_are_keyboard_and_screen_reader_stops() -> None:
    item = function_body(CHARTS, "item")
    for needle in ("cum-item", "tabindex", 'role="img"', "aria-label", "data-i"):
        assert needle in item, needle
    navigate = function_body(CHARTS, "navigate")
    for key in ("ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"):
        assert key in navigate, key
    assert 'data-nav="' in CHARTS


def test_charts_are_drawn_at_the_width_of_their_host_and_redrawn_on_resize() -> None:
    assert "host.clientWidth" in function_body(APP_CODE, "hydrateCharts")
    assert "ResizeObserver" in APP_CODE
    assert "redrawAfterStyles" in APP_CODE            # a theme's stylesheet decides --chart-pips and sizes
    assert 'getPropertyValue("--chart-pips")' in CHARTS


# ── Every theme ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_every_class_the_markup_uses_is_styled_by_every_theme(theme_id: str) -> None:
    css = THEME_CSS[theme_id]
    used = set(re.findall(r"\bnm-[a-z0-9-]+", APP_CODE)) | set(re.findall(r"\bnm-[a-z0-9-]+", INDEX))
    defined = set(re.findall(r"\.(nm-[a-z0-9-]+)", css))
    assert not used - defined, sorted(used - defined)
    modifiers = set(re.findall(r"\b(?:is|span)-[a-z0-9]+\b", re.sub(r"'[^']*is-[^']*'", "", APP_CODE)))
    styled = set(re.findall(r"\.((?:is|span)-[a-z0-9]+)", css))
    assert not modifiers - styled, sorted(modifiers - styled)


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_every_theme_sets_the_pips_flag_and_the_chart_colours(theme_id: str) -> None:
    css = THEME_CSS[theme_id]
    flag = re.search(r"--chart-pips:\s*([01])\b", css)
    assert flag, "a theme decides whether charts draw pips (1) or solid bars (0)"
    assert flag.group(1) == ("1" if theme_id == "nostromo" else "0")
    for variable in ("--chart-mark", "--chart-hot", "--chart-line", "--chart-text", "--chart-track"):
        assert variable in css, (theme_id, variable)


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_every_theme_stops_its_motion_for_reduced_motion(theme_id: str) -> None:
    css = THEME_CSS[theme_id]
    assert 'html[data-reduce-motion="1"]' in css and "prefers-reduced-motion" in css


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_every_theme_ships_the_files_the_server_offers_for_it(theme_id: str) -> None:
    """/api/state lists a theme's css and js only when the file exists; the script registers its start hook."""
    assert (WEBUI / "themes" / f"{theme_id}.css").is_file() and (WEBUI / "themes" / f"{theme_id}.js").is_file()
    assert f"CUM_THEME_FX.{theme_id} = {{ start: start }}" in THEME_JS[theme_id]


# ── Nostromo ──────────────────────────────────────────────────────────────────────


def test_the_markup_contract_lists_exactly_the_classes_in_use() -> None:
    """The comment at the top of app.js is what the Night City and Gates stylists work from."""
    used = set(re.findall(r"\bnm-[a-z0-9-]+", APP_CODE)) | set(re.findall(r"\bnm-[a-z0-9-]+", INDEX))
    documented = set(re.findall(r"\bnm-[a-z0-9-]+", APP_DOC))
    assert documented - used == set(), f"documented but not used: {sorted(documented - used)}"
    assert used - documented == set(), f"used but not documented: {sorted(used - documented)}"
    for heading in ("Markup contract for theme authors", "Shell", "Page", "Card", "Stat", "Tables", "Controls", "Charts", "Motion"):
        assert heading in APP_DOC, heading


def test_nostromo_sets_the_pips_flag_in_its_root_block() -> None:
    root = re.search(r":root\s*\{(.*?)\n\}", CSS, re.S).group(1)
    assert "--chart-pips: 1" in root


def test_the_page_stops_its_own_animation_for_reduced_motion() -> None:
    for body in (function_body(APP_CODE, "syncCompareTimer"), function_body(APP_CODE, "playOdometer")):
        assert "reduceMotion()" in body
    assert 'getAttribute("data-reduce-motion") === "1"' in APP_CODE


# ── Theme-neutral wording and the cost figure's fit ───────────────────────────────

def test_notes_do_not_describe_colours_in_terms_a_theme_may_invert() -> None:
    """Nostromo lights the busy days, a light theme darkens them: the page says only "stronger"."""
    code = strip_comments(APP_CODE)
    assert not re.search(r"\b(darker|brighter|lighter|dimmer)\b", code, re.I)
    assert "A stronger colour is more" in code


def test_the_cost_figure_tells_the_theme_its_length_and_nostromo_fits_it() -> None:
    assert 'style="--n:\' + heroText.length' in function_body(APP_CODE, "costCard")
    assert '--n:<characters>' in APP_DOC                       # documented for the other stylists
    assert "container-type: inline-size" in CSS and "100cqw" in CSS and "var(--n" in CSS
    # the plan text beside the figure may wrap rather than run across the divider into the chart
    assert re.search(r"\.nm-plan \.nm-facts \{[^}]*white-space: normal", CSS)


def test_stat_figures_tell_the_theme_their_length_and_nostromo_fits_them_to_the_tile() -> None:
    """A seven-digit figure wrapped in a narrow tile ("+70,71" over "9") until the tile sized the figure."""
    assert "style=\"--n:' + String(value).length" in function_body(APP_CODE, "statTile")
    assert APP_DOC.count("--n:<characters>") == 2             # the cost figure and the stat figures
    assert re.search(r"\.nm-stat \{[^}]*container-type: inline-size", CSS)
    assert re.search(r"\.nm-stat-v\.is-big:not\(\.is-text\) \{ font-size: clamp\(24px, calc\(100cqw", CSS)
    assert "max(124px, calc((100% - 48px) / 4))" in CSS        # never more than four tiles across


def test_nostromo_fits_the_overview_in_the_default_window() -> None:
    """The default window is about 741 px tall inside its frame; the limit cards need 201 px normally and 187 px
    with the short-window rhythm, and the overview's floors must be what its content needs."""
    base = re.search(r"\.nm-grid \{ flex: 1 1 auto; min-height: (\d+)px;.*?grid-template-rows: minmax\((\d+)px,", CSS, re.S)
    assert base and (int(base.group(1)), int(base.group(2))) == (657, 201)      # (the last row's floor is 154: the burn card's trace)
    assert "minmax(154px, 146fr)" in CSS.split("short windows")[0]               # it ran over the card's figures at 140
    short = re.search(r"@media \(min-width: 1001px\) and \(max-height: (\d+)px\) \{(.*?)\n\}", CSS, re.S)
    assert short and int(short.group(1)) < 800 and "min-height: 634px" in short.group(2)
    assert "minmax(188px, 196fr)" in short.group(2) and "minmax(144px, 146fr)" in short.group(2)
    assert ".nm-sub { max-width: 680px; }" in short.group(2)


def test_nostromo_keeps_a_long_card_note_inside_the_card() -> None:
    assert re.search(r"\.nm-pgrid \.nm-card-t, \.nm-chart-t \{[^}]*flex-wrap: wrap", CSS)
    assert re.search(r"\.nm-grid \.nm-card-t:not\(\.nm-chart-t\) \.nm-meta-t \{[^}]*text-overflow: ellipsis", CSS)


# ── The overview in a tall window and the title bar in a narrow one ───────────────

@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_a_tall_window_does_not_stretch_the_overview_cards(theme_id: str) -> None:
    """The three rows shared the whole height of a maximised window, so at 1920 x 1040 every card was half empty. From
    1100 px wide (where the default layout holds) the grid stops growing a sixth past what the default window gives it
    and the rest of the window stays page."""
    match = re.search(r"@media \(min-width: 1100px\) \{\s*\.nm-grid \{ max-height: (\d+)px; \}\s*\}", THEME_CSS[theme_id])
    assert match, theme_id
    assert 640 <= int(match.group(1)) <= 760, theme_id


def test_nostromo_keeps_refresh_on_screen_in_a_720_px_window() -> None:
    """The title bar is one row that does not wrap, and at 720 px it was 55 px too long: REFRESH, the only manual
    control, was cut off. At 800 px and below the caption after the title and the data age leave it (the uplink pill
    still says whether the data is live). Measured in a browser: REFRESH ends at 708 of 720 px, and at 789 of 801 px
    with everything shown."""
    assert re.search(r"@media \(max-width: 800px\) \{\s*\.nm-brand em, #data-age \{ display: none; \}\s*\}", CSS)
    assert "<em>" in INDEX and 'id="data-age"' in INDEX and 'id="uplink"' in INDEX     # the rule names real elements


# ── Night City ────────────────────────────────────────────────────────────────────

DEFAULT_PAGE_HEIGHT = 741                 # the default 1200 x 780 window, less its frame
NAV_ROOM_AT_DEFAULT = 110                 # px in the navigation column between the menu and the health block there
RETICLE, MATRIX_REDUCED, MATRIX_FULL = 82, 104, 152           # px each needs, as measured in a browser


def nav_room(page_height: int) -> int:
    """The column gets every px of height the window has beyond the default."""
    return NAV_ROOM_AT_DEFAULT + page_height - DEFAULT_PAGE_HEIGHT


def test_night_city_shows_its_navigation_decoration_in_the_default_window() -> None:
    """The reticle (Blackwall) and the code matrix (Edgerun) were hidden below 760 and 800 px of page, so neither showed in
    the default window the approved mock draws them in. Each is now shown from the first height it fits at, and the
    matrix keeps two rows between 735 and 785 px."""
    css = THEME_CSS["night_city"]
    found = {
        "deco": re.search(r"@media \(max-height: (\d+)px\) \{ \.nm-nav > \.nc-deco \{ display: none !important; \} \}", css),
        "matrix": re.search(r"@media \(max-height: (\d+)px\) \{ \.nc-matrix \{ display: none !important; \} \}", css),
        "rows": re.search(r"@media \(max-height: (\d+)px\) \{\s*\.nc-matrix \{ margin-top: 12px; \}\s*"
                          r"\.nc-mx i:nth-child\(n\+11\) \{ display: none; \}", css),
    }
    assert all(found.values()), found
    at = {name: int(match.group(1)) for name, match in found.items() if match}
    for name, needs in (("deco", RETICLE), ("matrix", MATRIX_REDUCED), ("rows", MATRIX_FULL)):
        assert nav_room(at[name] + 1) >= needs, name        # shown only where it fits: the column would scroll otherwise
    assert at["deco"] < DEFAULT_PAGE_HEIGHT and at["matrix"] < DEFAULT_PAGE_HEIGHT <= at["rows"]
    assert "max-height: 759px" not in css and "max-height: 799px" not in css          # the old breakpoints are gone


def test_only_edgerun_draws_the_time_left_bar() -> None:
    css = THEME_CSS["night_city"]
    rules = re.findall(r"^([^{}\n]*\.nm-remain[^{}\n]*)\{", css, re.M)
    selectors = [part.strip() for rule in rules for part in rule.split(",")]
    assert selectors and all(part.startswith(':root[data-variant="edgerun"] ') for part in selectors), selectors
    for theme_id in ("nostromo", "gates"):                                      # these read the reset in the facts
        assert re.search(r"^\.nm-remain \{ display: none; \}", THEME_CSS[theme_id], re.M), theme_id
    assert '".nm-remain{display:none}"' in APP_CODE                            # and so does a theme that says nothing


def test_the_time_left_bar_keeps_both_texts_readable_over_the_fill_and_the_track() -> None:
    """The label and the time left each span the whole bar and are painted from one hard-stop gradient that flips where
    the fill ends (ink on the fill, the signal colour on the track), so both read from a fresh window to an empty one.
    The approved still only shows the middle: the time, in the signal colour, vanished into the fill above ~90 % left."""
    css = THEME_CSS["night_city"]
    text = re.search(r'\.nm-remain \.l, :root\[data-variant="edgerun"\] \.nm-remain \.v \{(.*?)\n\}', css, re.S)
    assert text
    body = text.group(1)
    assert "inset: 0" in body and "color: transparent" in body and "background-clip: text" in body
    assert "linear-gradient(90deg, var(--nc-ink) var(--r, 0%), var(--nc-signal) var(--r, 0%))" in body
    assert re.search(r"\.nm-remain::before \{[^}]*width: var\(--r, 0%\);[^}]*background: var\(--nc-signal\);", css)
    edgerun = themes.get_theme("night_city").variant("edgerun")
    ink, signal, track = (edgerun.extras[name] for name in ("ink", "signal", "track"))
    assert colour.contrast_ratio(ink, signal) >= 4.5, "the label and the time over the fill"
    assert colour.contrast_ratio(signal, track) >= 4.5, "the label and the time over the track"


# ── JavaScript (child processes: only with CUM_UI_TESTS=1) ────────────────────────

# Loads charts.js in node (no DOM needed to build a string) and looks for text or marks drawn outside the
# chart's own width, and for text drawn over text. Text is taken as 7.9 px a character, as charts.js itself does.
CHART_BOUNDS_JS = r"""
const fs = require('fs'), vm = require('vm');
const box = {}; box.window = box; vm.createContext(box);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), box);
const C = box.CUMCharts, CHAR = 7.9, problems = [];
function check(name, svg) {
  const W = +svg.match(/viewBox="0 0 ([\d.]+) /)[1], texts = [];
  for (const m of svg.matchAll(/<text class="([^"]*)" x="([-\d.]+)" y="([-\d.]+)">([^<]*)<\/text>/g)) {
    const w = m[4].length * CHAR, x = +m[2];
    const l = m[1].includes('is-end') ? x - w : m[1].includes('is-mid') ? x - w / 2 : x;
    texts.push({ label: m[4], l: l, r: l + w, y: +m[3] });
  }
  texts.forEach((t) => { if (t.l < -0.5 || t.r > W + 0.5) problems.push(name + ': text "' + t.label + '" leaves the chart'); });
  for (const m of svg.matchAll(/<rect[^>]* x="([-\d.]+)" y="[-\d.]+" width="([\d.]+)"/g)) {
    if (+m[1] < -0.5 || +m[1] + +m[2] > W + 0.5) problems.push(name + ': a rect leaves the chart');
  }
  for (let i = 0; i < texts.length; i++) for (let j = i + 1; j < texts.length; j++) {
    const a = texts[i], b = texts[j];
    if (Math.abs(a.y - b.y) < 9 && a.l < b.r - 0.5 && b.l < a.r - 0.5) problems.push(name + ': "' + a.label + '" overlaps "' + b.label + '"');
  }
}
for (const w of [180, 220, 260, 300, 340, 400, 500, 700, 1000]) {
  for (const label of ['AVG $26', 'AVG $9.87', 'AVG $1.2K', 'AVG $123']) {
    const costs = Array.from({ length: 30 }, (_, i) => (i * 7) % 23);
    check('columns ' + w + ' ' + label, C.columns(costs, { width: w, height: 132, gutterRight: 68, step: 5, pips: true, current: 29,
      tips: costs.map(String), avg: { value: 9.87, label: label },
      labels: [{ i: 29, text: 'TODAY' }, { i: 21, text: 'OCT 1' }, { i: 0, text: 'SEP 22' }] }));
  }
  check('line ' + w, C.line({ width: w, height: 190, xMin: 0, xMax: 100, yMin: 0, yMax: 100,
    yTicks: [0, 25, 50, 75, 100].map((v) => ({ v: v, label: v + '%', limit: v === 100 })),
    xTicks: [{ t: 0, label: 'MON' }, { t: 50, label: 'THU' }, { t: 100, label: 'SUN' }],
    marks: [{ t: 99, label: 'NOW' }, { t: 3, label: 'NOW' }],
    series: [{ kind: 'actual', points: [[0, 0], [50, 30], [99, 41]], tips: ['a', 'b', 'c'] }, { kind: 'forecast', points: [[99, 41], [100, 55]] }] }));
}
for (const w of [300, 340, 400, 500, 700, 1000]) {
  const rows = ['atlas-web', 'a-very-long-folder-name-for-a-project', 'x', 'home-automation'].map((l, i) => ({ label: l, value: 10 - i, text: '$12,345.67 \u00b7 100%' }));
  check('hbars ' + w, C.hbars(rows, { width: w }));
  check('hbars valueWidth ' + w, C.hbars(rows, { width: w, valueWidth: 70 }));
}
function yearOf(end) {      // 371 consecutive days up to and including `end`
  return Array.from({ length: 371 }, (_, i) => {
    const d = new Date(end.getFullYear(), end.getMonth(), end.getDate() - 370 + i);
    return { date: d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'), value: (i * 37) % 50, tip: 't' };
  });
}
const grid = Array.from({ length: 7 }, (_, r) => Array.from({ length: 24 }, (_, c) => (r * c) % 9));
const colLabels = [0, 3, 6, 9, 12, 15, 18, 21].map((h) => ({ c: h, text: String(h).padStart(2, '0') }));
for (const w of [260, 340, 500, 700, 1000]) {
  // the last column holds the first days of a month on some end dates: its name must be pulled back inside
  for (const end of [new Date(2026, 9, 1), new Date(2026, 9, 3), new Date(2026, 9, 7), new Date(2026, 0, 2), new Date(2026, 9, 22)]) {
    check('calendar ' + w + ' ' + end.toDateString(), C.calendar(yearOf(end), { width: w, maxCell: 22, legend: ['LESS', 'MORE'] }));
  }
  check('matrix ' + w, C.matrix(grid, { width: w, rowLabels: ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN'], colLabels: colLabels }));
}
console.log(JSON.stringify(Array.from(new Set(problems))));
"""


def node_binary() -> str:
    """The node executable. A machine without it skips these tests; CI (CI=true) fails instead, since a skipped
    script test is exactly how a broken script once shipped green."""
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI"):
            pytest.fail("node is not installed on this CI runner, so the dashboard scripts cannot be tested")
        pytest.skip("node is not installed")
    return node


@ui_only
@pytest.mark.parametrize("name", list(ALL_SCRIPTS))
def test_scripts_parse(name: str) -> None:
    run = subprocess.run([node_binary(), "--check", str(WEBUI / name)], capture_output=True, text=True, timeout=60,
                         check=False, creationflags=NO_WINDOW)
    assert run.returncode == 0, run.stderr[-400:]


@ui_only
def test_chart_text_and_marks_stay_inside_their_chart() -> None:
    run = subprocess.run([node_binary(), "-e", CHART_BOUNDS_JS, str(WEBUI / "charts.js")], capture_output=True,
                         text=True, timeout=60, check=False, creationflags=NO_WINDOW)
    assert run.returncode == 0, run.stderr[-400:]
    assert json.loads(run.stdout) == []


class Markup(HTMLParser):
    """The tags and text of a piece of the page's markup, as a browser would read them."""

    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.text: list[str] = []
        self.feed(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data: str) -> None:
        self.text.append(data)

    def find(self, tag: str) -> list[dict[str, str | None]]:
        return [attrs for name, attrs in self.tags if name == tag]


@pytest.fixture(scope="module")
def checks() -> dict[str, Any]:
    """What tests/webui_checks.js found out by running charts.js and app.js (one node run for the module)."""
    run = subprocess.run([node_binary(), str(CHECKS_JS), str(WEBUI)], capture_output=True, text=True, timeout=60,
                         check=False, encoding="utf-8", creationflags=NO_WINDOW)
    assert run.returncode == 0, run.stderr[-800:]
    return json.loads(run.stdout)


HOSTILE = "<img src=x onerror=alert(1)> \"quoted\" 'single' & done"


@ui_only
def test_esc_leaves_no_markup_in_session_titles_folder_names_and_chart_labels(checks: dict[str, Any]) -> None:
    assert checks["esc"] == "&lt;img src=x onerror=alert(1)&gt; &quot;quoted&quot; &#39;single&#39; &amp; done"
    assert checks["escNull"] == "||0"
    page = Markup(checks["sessionsTable"])
    assert page.find("img") == [] and page.find("b") == []             # nothing in a title or a folder became a tag
    cells = [attrs for attrs in page.find("td") if "lead" in (attrs.get("class") or "")]
    assert cells and cells[0]["title"].startswith(HOSTILE + " · <m> · 1 pull request")   # the attribute holds it whole
    assert HOSTILE in page.text                                                         # and the cell shows it as text
    assert "<b>name</b>" in page.text and any(a.get("title") == 'p"q<' for a in page.find("td"))
    svg = Markup(checks["chartLabelMarkup"])
    assert svg.find("img") == [] and svg.find("b") == []
    assert any(a.get("aria-label") == HOSTILE for a in svg.find("g"))                    # charts.js escapes its own labels
    assert "Untitled session" in Markup(checks["sessionsTableUntitled"]).text


@ui_only
def test_a_heat_map_says_how_many_columns_it_has_and_says_it_right(checks: dict[str, Any]) -> None:
    """The motion layer wipes a heat map on a column at a time and reads that count from one attribute (data-cols) on the
    svg, so it never walks the ~540 cells of a year. The count has to be the columns the cells really sit in."""
    found = checks["heatCols"]
    assert found["calendar"]["said"] == found["calendar"]["drawn"] == 54          # 371 days from a Friday: 54 week columns
    assert found["matrix"]["said"] == found["matrix"]["drawn"] == 24              # one column an hour
    assert found["empty"] is None                                                 # an empty calendar draws nothing to wipe
    assert found["other"] == [None, None]                                         # no other chart carries one


FOLDERS = ["se-infolcd-apex-advanced", "se-infolcd-apex-update", "se-infolcd-apex-server", "SpaceEngineersModSDK",
           "claude-usage-monitor", "x"]
ELLIPSIS = "…"


@ui_only
@pytest.mark.parametrize("per_char", ["6", "7.9", "9"])
def test_chart_labels_are_cut_to_the_measured_width_in_the_middle(checks: dict[str, Any], per_char: str) -> None:
    """The label column assumed 7.9 px a character and cut the end off, so with Gates' proportional font three
    different folders all read "se-infolcd-apex-..." while the column had room to spare."""
    shown = checks["labels"][per_char]
    room = 138 - 2                                          # the label column of a 406 px chart, less the margin
    for original, label in zip(FOLDERS, shown):
        assert len(label) * float(per_char) <= room + 1e-9 or label == original[:1] + ELLIPSIS, (original, label)
        if len(original) * float(per_char) <= room:
            assert label == original                        # what fits is never touched
        else:
            assert ELLIPSIS in label and label.startswith(original[:6]) and label.endswith(original[-4:]), (original, label)
    assert len(set(shown[:3])) == 3                         # the three told apart by their ends stay told apart


@ui_only
def test_a_narrow_chart_still_tells_the_long_names_apart(checks: dict[str, Any]) -> None:
    narrow = checks["labelsNarrow"]
    assert len(set(narrow[:3])) == 3 and all(ELLIPSIS in label for label in narrow[:5])
    assert narrow[5] == "x"


@ui_only
def test_the_value_text_is_measured_and_the_measure_is_asked_again_after_refresh(checks: dict[str, Any]) -> None:
    assert checks["barWidth"]["six"] == 406 - 138 - (72 + 2) - 20             # 12 characters of 6 px, plus 2
    assert checks["barWidthStale"] == checks["barWidth"]["six"]               # answers are kept between passes
    assert checks["barWidthWide"] == 406 - 138 - (120 + 2) - 20               # and asked again after refresh()


def test_no_chart_guesses_a_text_width_from_its_length_any_more() -> None:
    code = strip_comments(CHARTS)
    assert code.count(".length * CHAR_W") == 1                              # only textWidth()'s fallback, where there is no page
    assert code.count("textWidth(") >= 6 and "function fitLabel(" in code


@ui_only
def test_percent_formats_scale_a_ratio_to_a_percentage(checks: dict[str, Any]) -> None:
    assert checks["fmtRate"] == ["87.0%", "0.0%", "100.0%", "--", "87.65%"]     # a cache hit rate of 0.87 is 87 %, not 0.9
    assert checks["fmtPct"] == ["12%", "13%", "12.3%", "--"]


@ui_only
def test_the_model_mix_is_shown_in_percent_of_cost(checks: dict[str, Any]) -> None:
    assert checks["modelShares"] == [75, 25]                                    # the report's share is 0..1
    page = Markup(checks["modelMixCard"])
    values = [t for t in page.text if t.endswith("%")]
    assert values == ["75%", "25%"]
    assert len([a for a in page.find("i") if a.get("class") == "on"]) == 15 + 5         # 20 cells, 15 and 5 lit
    assert checks["cacheHit"] == pytest.approx(0.6) and checks["multiplier"] == pytest.approx(0.4)


@ui_only
def test_the_page_calls_a_pace_what_the_widgets_and_the_pacing_report_call_it(checks: dict[str, Any]) -> None:
    assert checks["paceBand"] == windows.PACE_BAND_POINTS == 10
    names = {"ahead": "ahead", "behind": "behind", "on_pace": "on_pace"}
    for delta, (kind, diff) in checks["pace"].items():
        assert diff == pytest.approx(float(delta))
        assert kind == names[widget_model.pace_for(50 + float(delta), 0.5)], delta
    assert checks["pace"]["7"][0] == "on_pace" and checks["pace"]["9"][0] == "on_pace"
    assert checks["pace"]["10"][0] == "on_pace" and checks["pace"]["11"][0] == "ahead"
    assert checks["pace"]["-10"][0] == "on_pace" and checks["pace"]["-11"][0] == "behind"


def test_the_pace_band_in_the_script_is_the_one_in_the_data_layer() -> None:
    match = re.search(r"var PACE_BAND = (\d+);", APP_CODE)
    assert match and float(match.group(1)) == windows.PACE_BAND_POINTS
    assert "diff > 10" not in APP_CODE and "diff < -10" not in APP_CODE          # no second copy of the number


@ui_only
def test_real_money_is_shown_in_the_currency_the_account_is_billed_in(checks: dict[str, Any]) -> None:
    def amounts(html: str) -> tuple[str, str]:
        page = Markup(html)
        big = next(text for text in page.text if re.search(r"\d", text) and "USED" not in text and not text.startswith("OF"))
        of = next(text for text in page.text if text.startswith("OF "))
        return big, of

    assert amounts(checks["spend"]["EUR"]) == ("\u20ac12.50", "OF \u20ac50.00")      # the toast says "Used 12.50 of 50.00 EUR"
    assert amounts(checks["spend"]["GBP"]) == ("\u00a312.50", "OF \u00a350.00")
    assert amounts(checks["spend"]["USD"]) == ("$12.50", "OF $50.00")
    assert amounts(checks["spend"]["none"]) == ("$12.50", "OF $50.00")            # no currency reported: read as USD
    assert amounts(checks["spend"]["lower"]) == ("\u20ac12.50", "OF \u20ac50.00")
    assert amounts(checks["spend"]["bad"]) == ("12.50 EURO", "OF 50.00 EURO")      # a code the browser cannot use
    assert amounts(checks["spend"]["jpy"]) == ("\u00a513", "OF \u00a550")          # whole units where the currency has none
    assert checks["fmtMoney"] == ["\u20ac1,234.50", "--", "$0.00"]
    assert checks["fmtUsdUnchanged"] == ["$12.50", "$1,235"]                       # API-equivalent cost stays dollars


@ui_only
@pytest.mark.parametrize(("ratio", "cells", "lit", "part", "unit"), [
    ("0", 1, 0, None, 1), ("0.01", 1, 0, None, 1), ("1", 1, 1, None, 1), ("2.3", 3, 2, "30", 1),
    ("23.5", 24, 23, "50", 1), ("24", 24, 24, None, 1),
    ("24.5", 13, 12, "25", 2), ("28.15", 15, 14, "7", 2), ("30.5", 16, 15, "25", 2),     # a $20 plan at $609
    ("48", 24, 24, None, 2), ("100", 20, 20, None, 5), ("1000", 24, 23, "81", 42),
])
def test_the_plan_multiple_keeps_growing_past_24_months(checks: dict[str, Any], ratio: str, cells: int, lit: int,
                                                        part: str | None, unit: int) -> None:
    """It was a solid row of 24 cells from 24 months on, whatever the multiple: 30.5x and 100x looked the same."""
    row = checks["ratio"][ratio]
    assert (row["count"], row["on"], row["part"], row["scale"]) == (cells, lit, part, unit)
    caption = f"1 cell = {unit} month{'' if unit == 1 else 's'} of Pro plan"
    assert row["note"] == caption
    assert row["label"] == ("r" if unit == 1 else f"r, 1 cell = {unit} months")


@ui_only
def test_the_link_row_is_ok_only_while_the_link_is_live(checks: dict[str, Any]) -> None:
    """Gates drew every failed state ("Sign-in needed", "Offline", ...) in the ok green beside a grey pill."""
    link = checks["link"]
    assert link["live"] == "ok"
    for status in ("loading", "stale", "offline", "rate_limited", "no_credentials", "paused", "nolink", "nostate"):
        assert link[status] == "is-down", status


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_every_theme_styles_the_down_link_value(theme_id: str) -> None:
    css = THEME_CSS[theme_id]
    assert re.search(r"\.nm-kv b\.ok, \.nm-kv b\.is-down \{", css), theme_id        # the lamp beside the word keeps its layout


def test_gates_greys_the_down_link_value_like_the_pill_beside_it() -> None:
    css = THEME_CSS["gates"]
    assert re.search(r"\.nm-kv b\.is-down \{ color: var\(--text-dim\); \}", css)
    assert re.search(r"\.nm-uplink\.is-down \{ color: var\(--text-dim\);", css)
    assert re.search(r"\.nm-kv b\.ok \{ color: var\(--ok\); \}", css)
    assert not re.search(r"\.nm-kv b\.is-down[^{]*\{[^}]*var\(--ok\)", css)


@ui_only
def test_the_price_override_wins_where_a_price_is_used_and_the_detected_price_stays_known(checks: dict[str, Any]) -> None:
    price = checks["price"]
    assert price["detected"] == [100, 100]
    assert price["override"] == [50, 100]            # the settings hint reads the detected one, the reports the override
    assert price["overrideFallback"] == 50
    assert "<span>PRICE</span><b>$50 / MO</b>" in price["pipeline"]
    assert price["cleared"] == [100, 100]


@ui_only
def test_the_time_left_in_a_window_is_a_bar_the_themes_may_draw(checks: dict[str, Any]) -> None:
    """Edgerun's approved limit card has a TIME REMAINING bar where the other themes have the RESETS and IN facts."""
    def bar(html: str) -> tuple[dict[str, str | None], list[str]]:
        page = Markup(html)
        (div,) = [attrs for attrs in page.find("div") if attrs.get("class") == "nm-remain"]
        return div, page.text

    remain = checks["remain"]
    div, text = bar(remain["mid"])
    assert div["style"] == "--r:75.0%" and div["role"] == "img"                      # the share of the window that is left
    assert div["aria-label"].startswith("Time remaining 3 H 45 M, resets ") and div["title"] == div["aria-label"]
    assert text == ["TIME REMAINING", "3 H 45 M"]
    assert bar(remain["fresh"])[0]["style"] == "--r:100.0%" and bar(remain["fresh"])[1][1] == "5 H 0 M"
    assert bar(remain["week"])[0]["style"] == "--r:50.0%" and bar(remain["week"])[1][1] == "3 D 12 H"
    done, text = bar(remain["done"])
    assert done["style"] == "--r:0.0%" and text[1] == "NOW"                           # a finished window is an empty bar
    assert remain["none"] == ""                                                       # no reset time, nothing to draw
    card = remain["card"]
    assert card.index('class="nm-row2"') < card.index('class="nm-remain"') < card.index('class="nm-pace"')
    assert "RESETS <b>" in card and "IN " in card                                     # the other themes read the facts


# ── The Efficiency page, under its old name too ───────────────────────────────────

def test_the_efficiency_page_is_called_efficiency_wherever_a_user_reads_it() -> None:
    assert 'key: "efficiency", label: "EFFICIENCY", title: "EFFICIENCY"' in APP_CODE
    assert '<a href="#efficiency" data-page="efficiency"><i>04</i>EFFICIENCY</a>' in INDEX
    visible = strip_comments(APP + INDEX + "".join(THEME_CSS.values()))
    assert "nerds only" not in visible.lower() and "NERDS ONLY" not in visible
    assert 'PAGE_ALIASES = { nerds: "efficiency" }' in APP_CODE            # an old link or bookmark still opens it
    assert 'data-page="efficiency"' in THEME_CSS["gates"] and 'data-page="nerds"' not in THEME_CSS["gates"]


@ui_only
def test_an_old_nerds_link_and_a_choice_saved_under_the_old_name_still_work(checks: dict[str, Any]) -> None:
    assert checks["pageKeys"] == PAGES
    assert checks["alias"] == "efficiency" and checks["aliasNew"] == "efficiency"
    assert checks["aliasUnknown"] == "overview"
    assert checks["legacyPrefs"] == ["week", "month"]               # the window saved as nerdsWindow, and the cost window
    assert checks["newPrefsWin"] == "today"                         # the new name wins when both are saved


@ui_only
def test_the_two_compaction_and_context_figures_say_they_are_different_counters(checks: dict[str, Any]) -> None:
    compactions, context = checks["counters"]
    assert "Size at compaction" in compactions and "Claude Code&#39;s own count" in compactions
    assert "Largest request" in context and "prompt tokens per request, API count" in context
    assert "so the two can differ" in context
    why = why_table()
    assert "own token count" in why["ctx-max"] and "compaction" in why["ctx-max"]
    assert "own count" in why["compact-pre"] and "API" in why["compact-pre"]


@ui_only
def test_the_plan_is_named_by_its_plan_name_never_by_the_internal_tier(checks: dict[str, Any]) -> None:
    pipeline = checks["pipeline"]
    assert "<span>PLAN</span><b>MAX 5X</b>" in pipeline
    assert "DEFAULT" not in pipeline.upper() and "TIER" not in pipeline.upper()


def test_the_supporters_label_is_thank_you_and_a_theme_that_wants_capitals_writes_them_in_css() -> None:
    assert '<span class="nm-meta-t" id="about-supporters-meta">Thank You</span>' in APP_CODE
    assert 'S.supporters.error ? "List unavailable" : "Thank You"' in APP_CODE
    assert "thank you" not in APP_CODE and "THANK YOU" not in APP_CODE


# ── A page is never empty ─────────────────────────────────────────────────────────

CARDS = {
    "overview": ["limit-session", "limit-weekly", "limit-scoped", "cost", "efficiency", "models", "surfaces", "burn"],
    "limits": ["limit-session", "limit-weekly", "limit-scoped-0", "credits-surfaces"],
    "cost": ["value", "monthly", "windows", "by-model", "by-folder", "threads", "haiku", "fast", "sessions", "credits"],
    "efficiency": ["cache", "thinking", "compactions", "context", "tools", "tool-mix", "mcp-mix", "productivity", "effort",
                   "tokens", "api-web", "pipeline", "not-priced"],
    "history": ["odometer", "calendar", "matrix", "streaks", "export"],
}


@ui_only
def test_a_page_opens_as_its_whole_card_layout_before_any_number_has_arrived(checks: dict[str, Any]) -> None:
    """The tab was empty (a line of text) until its reports came; now every card, title and track is there at once."""
    for page, keys in CARDS.items():
        model = checks["empty"][page]
        assert [c["key"] for c in model["cards"]] == keys, page
        assert model["top"][:3] == ["hazard", "head", "update"]            # the update banner sits under the heading
        for card in model["cards"]:
            html = card["html"]
            assert f'data-card="{card["key"]}"' in html and f'data-state="{card["state"]}"' in html, (page, card["key"])
            assert re.search(r'class="nm-(?:ttl|tag)"', html), (page, card["key"])           # its title is there
            if card["state"] == "pending":
                assert 'aria-busy="true"' in html, (page, card["key"])
        waiting = [c["state"] for c in model["cards"] if c["key"] != "export"]
        assert set(waiting) == {"pending"}, page                                              # export has nothing to wait for


@ui_only
def test_the_placeholder_layout_has_the_cards_of_the_real_one(checks: dict[str, Any]) -> None:
    """A card that appears or goes when the numbers land would move everything under it."""
    def keys(states: list[str]) -> list[str]:
        return [s.split(":")[0] for s in states]

    assert keys(checks["arrived"]["efficiency"]) == CARDS["efficiency"]
    assert keys(checks["arrived"]["history"]) == CARDS["history"]
    assert keys(checks["lastKnown"]) == CARDS["cost"]
    for page in ("overview", "limits"):
        assert keys(checks["stateOnly"][page]) == CARDS[page]


@ui_only
def test_every_card_is_marked_by_how_its_numbers_are_doing(checks: dict[str, Any]) -> None:
    only = checks["stateOnly"]
    assert only["overview"][:3] == ["limit-session:ready", "limit-weekly:ready", "limit-scoped:ready"]   # these come with the state
    assert "cost:pending" in only["overview"] and "efficiency:pending" in only["overview"]                # these need the cost report
    assert only["history"][-1] == "export:ready" and only["history"][0] == "odometer:pending"
    assert all(s.endswith(":ready") for s in checks["arrived"]["history"])
    assert [s for s in checks["arrived"]["efficiency"] if not s.endswith(":ready")] == ["pipeline:pending", "not-priced:pending"]


@ui_only
def test_a_window_that_has_not_come_yet_shows_the_last_windows_numbers_where_they_do_not_depend_on_it(checks: dict[str, Any]) -> None:
    states = dict(s.split(":") for s in checks["lastKnown"])
    assert {k for k, v in states.items() if v == "ready"} == {"value", "monthly", "windows", "sessions"}
    assert {k for k, v in states.items() if v == "pending"} == {"by-model", "by-folder", "threads", "haiku", "fast", "credits"}


@ui_only
def test_a_failed_report_replaces_the_placeholder_inside_its_own_cards_only(checks: dict[str, Any]) -> None:
    for page, failing in (("efficiency", CARDS["efficiency"][:-2]), ("cost", CARDS["cost"][:-1])):
        model = checks["failed"][page]
        assert [c["key"] for c in model["cards"]] == CARDS[page], page             # no card goes
        for card in model["cards"]:
            if card["key"] in failing:
                assert card["state"] == "error", (page, card["key"])
                assert 'class="nm-error"' in card["html"] and 'class="nm-ttl"' in card["html"]      # its title and one line
                assert "nm-stat-v" not in card["html"] and "nm-cells" not in card["html"]            # and no figures
            else:
                assert card["state"] != "error", (page, card["key"])               # a card that does not use the report stands
    assert "Not ready yet" in checks["failed"]["efficiency"]["cards"][0]["html"]       # a 503, the database is still being built
    assert "Could not be loaded" in checks["failed"]["cost"]["cards"][0]["html"]        # anything else


@ui_only
def test_the_toolbar_note_is_a_placeholder_only_while_its_report_is_on_its_way(checks: dict[str, Any]) -> None:
    """A placeholder outside a card has no error swap: left pending after a failed report it would shimmer or flicker for ever."""
    toolbar = checks["toolbar"]
    for name in ("costWaiting", "efficiencyWaiting"):
        assert 'class="nm-toolbar-n" data-pending="" aria-hidden="true"' in toolbar[name], name
    for name in ("costFailed", "efficiencyFailed"):
        assert 'class="nm-toolbar-n"' in toolbar[name] and "data-pending" not in toolbar[name], name
        assert "not loaded" in toolbar[name], name


def test_the_page_keeps_the_last_good_report_of_each_page_in_memory_and_says_when_it_refreshes() -> None:
    code = strip_comments(APP_CODE)
    assert "function lastGoodEntry(" in code and "function syncUpdating(" in code and "UPDATING_DELAY_MS" in code
    assert 'setAttribute("data-updating", "")' in code
    for name in ("data-pending", "data-final", "data-fill", "data-fresh", "data-state", "data-needs"):
        assert name in code, name
    assert "[data-pending],[data-pending] *{color:transparent!important" in code          # a placeholder's text only sizes the slot


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_every_theme_draws_its_placeholders_and_the_updating_cue(theme_id: str) -> None:
    css = strip_comments(THEME_CSS[theme_id])
    assert "[data-pending]" in css and "[data-updating]" in css, theme_id
    assert re.search(r"\.nm-stat-v[^{]*\{[^}]*background", css), theme_id             # a figure that is not in yet is a block


# ── The hook contract for the motion layer ────────────────────────────────────────

def test_the_header_documents_the_hooks_the_motion_layer_may_implement() -> None:
    for needle in ("enter(main, page)", "cancel(main)", "arrive(card, info)", "cum:card-data", "cum:page-rendered",
                   "fx-run", "fx-quiet", "data-pending", "data-final", "data-fill", "data-fresh", "data-state=pending | ready | error",
                   "data-updating", "window.CUM_THEME_FX[id]", "fx-hold, which kept the page hidden"):
        assert needle in APP_DOC, needle
    assert "Pending: a page is never empty" in APP_DOC


def test_the_page_calls_the_hooks_and_does_not_wait_for_the_reports() -> None:
    code = strip_comments(APP_CODE)
    for name in ("themeFx", "themeReady", "wantEnter", "playEnterIfDue", "cancelFx", "announce"):
        assert f"function {name}(" in code, name
    assert 'fx.enter(main, key)' in code and 'fx.arrive(u.el, info)' in code and 'fx.cancel(main)' in code
    assert '"cum:card-data"' in code and '"cum:page-rendered"' in code
    assert 'classList.add("fx-hold")' not in code and "pageReady" not in code          # the entrance is not held back by the reports
    assert 'classList.add("fx-quiet")' in code and 'classList.remove("fx-quiet")' in code
    body = function_body(APP_CODE, "playEnterIfDue")
    assert "themeReady()" in body and 'document.visibilityState === "hidden"' in body and "reduceMotion()" in body
    assert "reduceMotion()" in function_body(APP_CODE, "announce")                      # nothing is called under reduced motion


# ── The update banner ─────────────────────────────────────────────────────────────

@ui_only
def test_the_update_banner_is_there_only_while_an_unskipped_release_is_waiting(checks: dict[str, Any]) -> None:
    banner = checks["banner"]
    for hidden in ("none", "noBlock", "skipped", "skippedByVersion", "hostileVersion", "skippedHere"):
        value = banner[hidden]
        assert (value["html"] if isinstance(value, dict) else value) == "", hidden
    available = banner["available"]
    page = Markup(available["html"])
    assert "Claude Usage Monitor v2.1.0 is available" in "".join(page.text)
    assert [(a["data-action"], a.get("type")) for a in page.find("button")] == [
        ("update-now", "button"), ("update-notes", "button"), ("update-skip", "button")]
    assert [a for a in page.find("div") if a.get("class") == "nm-update"][0]["role"] == "status"
    assert available["found"] == {"version": "2.1.0", "url": "https://github.com/Godimas101/claude-usage-monitor/releases/tag/v2.1.0", "self": True}
    assert "Update now installs it" in "".join(page.text)
    assert "Update now opens the release page" in "".join(Markup(banner["browser"]["html"]).text)
    assert banner["newerThanSkipped"]["found"]["version"] == "2.2.0"                   # a newer release than the skipped one comes back
    assert banner["fromInfo"]["found"]["version"] == "3.0"                              # the block's info carries it too
    assert banner["foreignUrl"]["found"]["url"] == ""                                   # only a github.com link is ever opened
    assert [a["data-action"] for a in Markup(banner["foreignUrl"]["html"]).find("button")] == ["update-now", "update-skip"]


@ui_only
def test_the_update_banner_status_is_shown_only_for_the_release_it_is_about(checks: dict[str, Any]) -> None:
    assert "SKIPPED" not in checks["banner"]["staleStatus"]


def test_the_update_banner_uses_the_update_routes_and_sits_first_on_every_page() -> None:
    code = strip_comments(APP_CODE)
    for route in ("/api/update/apply", "/api/update/skip", "/api/update/check"):
        assert f'"{route}"' in code, route
    page_model = function_body(APP_CODE, "pageModel")
    assert page_model.index("hazard") < page_model.index("update") < page_model.index("problem")
    assert "updateBanner()" in page_model                  # every page the page model draws has it, unless a page passes its own
    for page in ("overviewPage", "limitsPage", "costPage", "efficiencyPage", "historyPage"):
        assert "pageModel(" in function_body(APP_CODE, page), page
    # the buttons work from any page that shows the banner
    assert 'closest(".nm-update button[data-action]")' in code


@ui_only
@pytest.mark.parametrize("page", ["overview", "limits", "cost", "efficiency", "history"])
def test_the_update_banner_is_on_every_page_not_only_the_overview(checks: dict[str, Any], page: str) -> None:
    """The toast says "open the tray menu or the dashboard to update" and a click on it only brings the dashboard forward, so
    the dashboard has to offer the update on whichever page it is on."""
    shown = checks["bannerPages"][page]
    banner = Markup(shown["shown"])
    assert "Claude Usage Monitor v2.1.0 is available" in "".join(banner.text)
    assert [a["data-action"] for a in banner.find("button")] == ["update-now", "update-notes", "update-skip"]
    assert shown["none"] == "" and shown["skipped"] == ""                  # and nothing while no release waits, or after a skip


@ui_only
def test_the_about_page_keeps_its_update_banner_in_step_by_hand(checks: dict[str, Any]) -> None:
    about = checks["bannerAbout"]
    assert about["shown"]["hasSlot"] and about["none"]["hasSlot"]
    assert "Claude Usage Monitor v2.1.0 is available" in "".join(Markup(about["shown"]["html"]).text)
    assert about["none"]["html"] == ""
    assert about["shown"]["again"] and about["none"]["again"]              # an unchanged banner is not rebuilt (it would drop focus)
    assert about["shown"]["sig"] == about["shown"]["html"]
    assert 'id="about-update" style="display:contents"' in function_body(APP_CODE, "aboutPage")


@ui_only
def test_the_overview_says_so_when_the_cost_report_failed_and_nothing_stands_in(checks: dict[str, Any]) -> None:
    """With a billing day set (the month is not the cycle) or no month in the state, the cost report is the only source of the
    Overview's cost, efficiency and model-mix cards. When it failed they stayed placeholders for ever, with nothing saying why,
    while the Cost and Efficiency pages showed the failure."""
    cost = checks["overviewCost"]
    for name in ("failedBilling", "failedNoMonth", "failed501"):
        shown = cost[name]
        assert shown["states"] == ["cost:error", "efficiency:error", "models:error"], name
        assert shown["errorLines"] == 3 and shown["stillPending"] is False, name      # one line each, and no placeholder left
        assert "STATISTICS UNAVAILABLE" in shown["problem"], name
    assert "not available in this build" in cost["failed501"]["problem"]              # the same sentence the other pages give
    assert "HTTP 500" in cost["failedBilling"]["problem"]
    # a report that is only on its way is still a placeholder, and the monitor's own month still stands in when there is one
    assert cost["waiting"]["states"] == ["cost:pending", "efficiency:pending", "models:pending"] and cost["waiting"]["problem"] == ""
    assert cost["fallback"]["states"] == ["cost:ready", "efficiency:ready", "models:ready"] and cost["fallback"]["problem"] == ""


@ui_only
def test_the_fast_mode_card_says_the_same_thing_before_its_report_and_after(checks: dict[str, Any]) -> None:
    """Only the tone changes with the data: the same sentence, in the same box, so the card is as tall in all three states
    (it grew from one line to a boxed note when the report landed, and the cards below it dropped by 21 to 59 px)."""
    fast = checks["fast"]
    assert [fast[k]["state"] for k in ("pending", "none", "some")] == ["pending", "ready", "ready"]

    def callout(card: dict[str, Any]) -> re.Match[str]:
        found = re.search(r'<div class="nm-callout( is-warn)?" role="note"( data-pending="")?>(.*?)</div>', card["html"], re.S)
        assert found, card["html"]
        return found

    pending, none, some = callout(fast["pending"]), callout(fast["none"]), callout(fast["some"])
    assert pending.group(3) == none.group(3) == some.group(3)                          # the very same words
    assert "billed to your usage credits" in pending.group(3)
    assert (pending.group(2), none.group(2), some.group(2)) == (' data-pending=""', None, None)      # a placeholder only before the report
    assert (pending.group(1), none.group(1), some.group(1)) == (None, None, " is-warn")              # the warning tone only with requests
    frame = [re.match(r'<section class="([^"]*)"', fast[k]["html"]).group(1) for k in ("pending", "none", "some")]
    assert ["is-warn" in classes.split() for classes in frame] == [False, False, True]               # and so is the card's own frame
    for key in ("pending", "none", "some"):
        assert '<div class="nm-stat is-wide"' in fast[key]["html"]                    # the tile has the card's width, so its sub line never wraps by itself
        assert "No fast-mode requests in this window" not in fast[key]["html"]        # that sentence changed the line count


@ui_only
def test_the_placeholder_threads_have_a_request_count_as_long_as_a_real_one(checks: dict[str, Any]) -> None:
    """"97% · 2,670 requests" wraps in a narrow tile and "0% · 0 requests" did not, so the Main and subagents card grew a line
    when its report landed. The placeholder text is never shown (it is transparent); it only sizes the tile."""
    html = checks["threadsPending"]["html"]
    sub = re.findall(r'<div class="nm-stat-s">([^<]*)</div>', html)
    assert len(sub) == 2 and all(len(text) >= len("97% · 2,670 requests") for text in sub), sub


@ui_only
def test_a_status_line_in_capitals_is_in_capitals_all_the_way(checks: dict[str, Any]) -> None:
    """The Gates theme sets a line in sentence case only when all of it is capitals (gates.js isCaps). The monitor's reason is lower
    case ("update apply failed"), so each failure path raises it, or Gates shows a shouting prefix over a lower-case tail."""
    lines = checks["failureLines"]
    assert lines["reason"] == {
        "refresh": "REFRESH FAILED: UPDATE APPLY FAILED", "check": "CHECK FAILED: UPDATE APPLY FAILED",
        "install": "UPDATE FAILED: UPDATE APPLY FAILED", "skip": "COULD NOT SKIP: UPDATE APPLY FAILED"}
    assert lines["status"]["install"] == "UPDATE FAILED: HTTP 502"                     # no reason from the monitor: its status
    assert set(lines) == {"reason", "status", "lost"}
    assert checks["failureLinesCaps"] is True
    for group in lines.values():
        for text in group.values():
            assert re.search(r"[A-Z]", text) and not re.search(r"[a-z]", text), text


@ui_only
def test_check_for_updates_does_not_say_up_to_date_when_it_could_not_look(checks: dict[str, Any]) -> None:
    said = checks["checkText"]
    assert said["notChecked"] == "COULD NOT REACH GITHUB; TRY AGAIN LATER."            # one sentence: Gates capitalises only the first letter
    assert said["upToDate"] == said["oldMonitor"] == said["noResult"] == "YOU ARE UP TO DATE."
    assert said["found"] == said["foundBefore"] == "AN UPDATE IS AVAILABLE."          # a release an earlier check found is still on offer
    for text in said.values():
        assert re.search(r"[A-Z]", text) and not re.search(r"[a-z]", text), text


def test_every_failure_status_line_goes_through_failed_text() -> None:
    """The static half of the check above: no line is built as an upper-case prefix plus the monitor's own reason."""
    code = strip_comments(APP_CODE)
    assert not re.search(r'"[A-Z][A-Z ]*: " \+ errorText\(', code)
    assert function_body(APP_CODE, "failedText").count("toUpperCase()") == 1
    for prefix in ("REFRESH FAILED", "CHECK FAILED", "UPDATE FAILED", "COULD NOT SKIP"):
        assert f'failedText("{prefix}", res)' in code, prefix


# ── The theme picker ──────────────────────────────────────────────────────────────

@ui_only
def test_the_theme_picker_shows_one_swatch_per_accent(checks: dict[str, Any]) -> None:
    swatches = checks["swatches"]
    for key in ("gatesDark", "gatesLight", "gatesOther"):
        assert len(swatches[key]) == 7 == len(set(swatches[key])), key               # Gates has seven accents, not fourteen swatches
    assert len(swatches["nostromo"]) == 8 and len(swatches["nightCity"]) == 4
    assert swatches["gatesDark"] != swatches["gatesLight"]                           # the mode of the page picks the colours shown


# ── Gates: the live accent ────────────────────────────────────────────────────────

def test_gates_fixed_accents_fill_usage_and_the_glow_follows_the_live_accent() -> None:
    css = THEME_CSS["gates"]
    # the literal defaults stay (tests/test_themes.py reads them); a variant on the page makes the fill the accent unless the
    # server sent a fill of its own, which it does for the system accent
    assert re.search(r"--accent-fill: #60cdff; --accent-soft: #60cdff24; --accent-tint: #60cdff14;", css)
    assert ":root[data-variant] { --accent-fill: var(--accent); }" in css
    assert "--g-wash: color-mix(in srgb, var(--accent) 8%, transparent);" in css
    screen = re.search(r"\.nm-screen \{[^}]*\}", css, re.S).group(0)
    assert "var(--g-wash)" in screen and "--accent-tint" not in screen and "--accent-fill" not in screen
    assert re.search(r"\.nm-theme\[aria-pressed=\"true\"\] \{[^}]*var\(--g-wash\)", css)
    assert re.search(r"\.nm-variants \.nm-btn\[aria-pressed=\"true\"\] \{[^}]*var\(--g-soft\)", css)


def test_the_gates_loading_screen_corner_glow_is_the_live_accent_like_the_rest_of_its_chrome() -> None:
    """The boot's top-left wash read --accent-tint, which comes from the fill colour. A system accent too near a status colour
    (a red Windows accent) keeps its glyph, centre glow and the dashboard's own wash but gets Gates' default blue as its fill, so
    the loading screen showed a blue corner under a red glyph, then the corner turned red as the dashboard faded in."""
    boot = re.search(r'html\[data-theme="gates"\] \.g-boot \{[^}]*\}', read("themes/gates.motion.css"), re.S).group(0)
    assert "var(--g-wash)" in boot
    assert "--accent-tint" not in boot and "--accent-fill" not in boot


# ── Switched-off settings and the time fields ─────────────────────────────────────

@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_a_switched_off_group_dims_its_controls_and_never_its_words(theme_id: str) -> None:
    css = strip_comments(THEME_CSS[theme_id])
    assert not re.search(r"\.nm-set\.is-off[^{]*\{[^}]*opacity", css), theme_id        # the rows kept 55 %: too faint to read
    assert re.search(r"\.nm-input:disabled[^{]*\{[^}]*opacity", css), theme_id           # the controls dim themselves
    assert re.search(r"\.nm-toggle input:disabled[^{]*\{[^}]*opacity", css), theme_id


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_the_quiet_hours_fields_are_as_wide_as_the_time_the_locale_writes_in_them(theme_id: str) -> None:
    """A fixed 112 px (Nostromo) cut the AM / PM off in a 12-hour locale; the field now takes the width its content needs."""
    rule = re.search(r"\.nm-input\.is-time \{([^}]*)\}", strip_comments(THEME_CSS[theme_id])).group(1)
    assert "width: auto" in rule, theme_id
    assert int(re.search(r"min-width: (\d+)px", rule).group(1)) >= 140, theme_id


# ── The usage guard card (#27) ─────────────────────────────────────────────────────

GUARD_KEYS = ["guard_enabled", "guard_session_cap", "guard_weekly_cap", "guard_scoped_cap", "guard_resume_stagger_s"]
GUARD_CAPS, GUARD_STAGGER = GUARD_KEYS[1:4], GUARD_KEYS[4]
GUARD_EXPLANATION = ("When plan usage reaches a cap, working Claude sessions are told to wrap up, and sessions that wait are "
                     "woken when every limit is under its cap again. Nothing is blocked.")
GUARD_COST = "Adds a quick check after each tool call (about a quarter of a second)."
GUARD_FUNCTIONS = ("capRow", "guardCard", "guardView", "guardLiveHtml", "guardSetupHtml", "guardResultHtml", "syncGuard",
                   "pollGuard", "guardTick", "guardResume", "guardSetup")


def js_string(name: str) -> str:
    """The text of ``var NAME = "...";`` in app.js."""
    return re.search(rf'var {name} = "([^"]*)";', APP_CODE).group(1)


def test_the_guard_card_sits_between_alerts_and_system_and_has_a_control_for_every_guard_setting() -> None:
    page = function_body(APP_CODE, "settingsPage")
    assert page.index("alertsCard() +") < page.index("guardCard() +") < page.index('aria-label="System"')
    card = function_body(APP_CODE, "guardCard")
    assert 'aria-label="Usage guard"' in card and 'toggleRow("guard_enabled", "Arm the guard", GUARD_EXPLAIN)' in card
    for key in GUARD_CAPS + [GUARD_STAGGER]:
        assert f'capRow("{key}"' in card, key
    assert 'id="guard-live"' in card and 'id="guard-setup"' in card
    assert set(GUARD_KEYS) <= posted_setting_keys() <= set(server.SETTING_SPECS)
    assert "errLine(key)" in function_body(APP_CODE, "capRow")                    # a refusal lands under its field, announced


def test_the_guard_says_what_it_does_in_the_words_it_was_given() -> None:
    assert js_string("GUARD_EXPLAIN") == GUARD_EXPLANATION and js_string("GUARD_COST") == GUARD_COST
    assert "GUARD_COST" in function_body(APP_CODE, "guardSetupHtml")


def test_the_guard_fields_apply_the_limits_the_server_and_settings_apply() -> None:
    specs = server.SETTING_SPECS
    for key in GUARD_CAPS:
        check = specs[key].check
        assert check(None) and check(1) and check(100) and not check(0) and not check(101) and not check(True)
    stagger = specs[GUARD_STAGGER].check
    assert stagger(0) and stagger(600) and not stagger(601) and not stagger(-1) and not stagger(None)
    change = function_body(APP_CODE, "settingsChange")
    assert 'key === "guard_session_cap" || key === "guard_weekly_cap" || key === "guard_scoped_cap"' in change
    assert re.search(r"\+cap < 1 \|\| \+cap > 100", change) and re.search(r"\+stagger > 600", change)
    assert 'value = null;' in change[change.index("guard_session_cap"):]               # a blank cap is "not watched"


def test_the_guard_talks_to_the_monitor_only_through_its_three_routes() -> None:
    routes = set(re.findall(r'api\("(?:GET|POST)", "(/api/guard[^"]*)"', APP_CODE))
    assert routes == {"/api/guard", "/api/guard/resume", "/api/guard/setup"}
    assert 'api("POST", "/api/guard/setup", { action: action })' in function_body(APP_CODE, "guardSetup")
    click = function_body(APP_CODE, "settingsClick")
    for action in ("guard-resume", "guard-install", "guard-remove"):
        assert f'"{action}"' in click, action
    assert 'failedText("RESUME FAILED", res)' in strip_comments(APP_CODE)             # in capitals all the way, like every failure line


def test_the_guard_asks_again_only_while_the_settings_page_is_on_screen_and_one_ask_at_a_time() -> None:
    tick = function_body(APP_CODE, "guardTick")
    for needle in ('S.page !== "settings"', 'S.formKey !== "settings"', 'visibilityState === "hidden"', "S.guardFlight", "GUARD_POLL_MS"):
        assert needle in tick, needle
    assert "guardTick();" in function_body(APP_CODE, "tick") and "var GUARD_POLL_MS = 3000;" in APP_CODE
    assert "if (S.guardFlight) {" in function_body(APP_CODE, "pollGuard")
    assert "pollGuard();" in function_body(APP_CODE, "saveSettings")                   # a change of a guard setting shows at once


def test_the_guard_has_no_motion_of_its_own_and_never_touches_a_token() -> None:
    """Whatever moves in the card is the theme's (the entrance, the pending shimmer, the hover), and so it obeys reduce_motion."""
    for name in GUARD_FUNCTIONS:
        body = strip_comments(function_body(APP_CODE, name))
        assert not re.search(r"requestAnimationFrame|setTimeout|setInterval|\.animate\(|animation|transition", body), name
        assert not re.search(r"TOKEN|token|credential|Authorization|localStorage|sessionStorage", body, re.I), name


def test_the_guard_blocks_are_built_from_the_existing_parts_and_open_as_placeholders() -> None:
    live, setup = function_body(APP_CODE, "guardLivePlaceholder"), function_body(APP_CODE, "guardSetupHtml")
    assert 'data-pending=""' in live and "disabled" in live                             # the shape of the common case, no button live yet
    assert 'kvGrid([["HOOKS", "NOT INSTALLED", true]' in setup                          # the rows exist from the first frame, pending
    for name in ("guardLiveHtml", "guardSetupHtml", "guardResultHtml"):
        assert "esc(" in function_body(APP_CODE, name), name                            # everything it was told is escaped
    assert 'function kvGrid(pairs, cls)' in APP_CODE and 'row[3] ? \' title="\'' in function_body(APP_CODE, "kvGrid")


def test_the_gates_theme_gives_the_guard_card_its_icon_and_the_other_themes_need_none() -> None:
    assert re.search(r'\.nm-card\[aria-label="Usage guard"\] \{ --g-ico: "\\[0-9A-F]{4}"; \}', THEME_CSS["gates"])
    for theme_id in ("nostromo", "night_city"):
        assert "--g-ico" not in THEME_CSS[theme_id]


def test_the_gates_theme_keeps_the_tone_of_the_guard_chips_its_script_calls_neutral() -> None:
    """gates.js tones a chip from its words and calls a word it does not know neutral, which would wash ARMED and HOLDING
    out: the two chips carry their tone as a class (is-ok, is-crit), and Gates lets that class win inside the guard block."""
    css = THEME_CSS["gates"]
    for tone, colour in (("is-ok", "--ok"), ("is-crit", "--alert")):
        assert f'#guard-live .nm-chip.{tone}[data-g-tone="neutral"] {{ color: var({colour});' in css, tone


def test_the_manual_setup_the_card_points_at_is_in_the_repository() -> None:
    links = re.search(r"var LINKS = \{(.*?)\};", APP_CODE, re.S).group(1)
    assert 'hooks: "https://github.com/Godimas101/claude-usage-monitor/blob/main/docs/AGENT_HOOK.md"' in links
    assert 'data-external="1">docs/AGENT_HOOK.md</a>' in function_body(APP_CODE, "guardSetupHtml")
    assert (paths.resource_dir() / "docs" / "AGENT_HOOK.md").is_file()


def kv(html: str) -> list[tuple[str, str]]:
    """The label and value of each row of a kvGrid, as text."""
    rows = re.findall(r'<div class="nm-kv"><span[^>]*>([^<]*)</span><b[^>]*>([^<]*)</b></div>', html)
    return [(unescape(label), unescape(value)) for label, value in rows]


def chip(html: str) -> tuple[str, str]:
    found = re.search(r'<span class="nm-chip([^"]*)"[^>]*>([^<]*)</span>', html)
    return found.group(1), found.group(2)


def story(html: str) -> str:
    return unescape(re.search(r'<p class="nm-story"[^>]*>([^<]*)</p>', html).group(1))


def resume_disabled(html: str) -> bool:
    return bool(re.search(r'<button[^>]*(?:data-action="guard-resume"|data-fid="guard-resume")[^>]* disabled', html))


@ui_only
def test_the_guard_card_has_its_toggle_four_fields_and_the_two_blocks_the_page_fills(checks: dict[str, Any]) -> None:
    card = Markup(checks["guard"]["card"])
    fields = {attrs["data-key"]: attrs for attrs in card.find("input") if attrs.get("data-key")}
    assert list(fields) == GUARD_KEYS and fields["guard_enabled"]["type"] == "checkbox"
    for key in GUARD_CAPS:
        assert (fields[key]["type"], fields[key]["min"], fields[key]["max"], fields[key]["placeholder"]) == ("number", "1", "100", "OFF")
        assert "err-" + key in fields[key]["aria-describedby"]                           # the field says where its refusal is announced
    assert (fields[GUARD_STAGGER]["min"], fields[GUARD_STAGGER]["max"], fields[GUARD_STAGGER]["placeholder"]) == ("0", "600", "120")
    ids = {attrs["id"] for _tag, attrs in card.tags if attrs.get("id")}
    assert {"guard-live", "guard-setup", *("err-" + key for key in GUARD_CAPS + [GUARD_STAGGER])} <= ids
    assert card.find("section")[0]["aria-label"] == "Usage guard"
    assert GUARD_EXPLANATION in " ".join(card.text)
    for title in ("Session cap", "Weekly cap", "Model weekly cap", "Stagger", "One-click setup"):
        assert title in card.text, title
    assert checks["guard"]["labels"] == ["USAGE GUARD", "SESSION CAP", "WEEKLY CAP", "MODEL WEEKLY CAP", "STAGGER", "POLL INTERVAL MS"]


@ui_only
def test_the_guard_status_says_off_armed_or_holding(checks: dict[str, Any]) -> None:
    live = checks["guard"]["live"]
    assert chip(live["off"]) == ("", "OFF") and "The guard is off" in story(live["off"])
    assert chip(live["armed"]) == (" is-ok", "ARMED")
    assert story(live["armed"]) == "Watching the session limit at 95% and the weekly limit at 90%. Every watched limit is under its cap."
    assert "each model’s weekly limit at 80%" in story(live["armedThree"]) and "the weekly limit at 90% and each" in story(live["armedThree"])
    assert story(live["armedNoCaps"]) == "No cap is set, so no limit is watched. Set at least one cap above."
    assert chip(live["holding"]) == (" solid is-crit", "HOLDING") and story(live["holding"]).startswith("A cap was reached.")
    for state in ("off", "armed", "armedThree", "armedNoCaps"):
        assert kv(live[state]) == [], state                                              # nothing but the sentence until something waits


@ui_only
def test_while_holding_it_names_what_is_over_when_it_ends_who_was_told_and_who_waits(checks: dict[str, Any]) -> None:
    rows, clock = kv(checks["guard"]["live"]["holding"]), checks["guard"]["clock"]
    assert rows[:2] == [("SESSION", "96% OF A 95% CAP"), ("FABLE WEEKLY", "91.5% OF A 90% CAP")]      # percent against cap, as the limit is named
    assert rows[2] == ("RESUMES", f"{clock['resumes']} · IN 3 H 27 M")                                 # local time and a countdown
    assert rows[3] == ("TOLD THIS EPISODE", "2 SESSIONS")
    assert rows[4] == ("3f9a1c2e", clock["told"]) and rows[5][0] == "3f9a1c2e" and rows[5][1] != rows[4][1]   # short ids, and when
    assert rows[6] == ("WAITING", "1 SESSION") and rows[7] == ("atlas-web · 77b0aa10", f"SINCE {clock['since']}")   # the folder, and since when
    assert 'title="C:\\Demo\\atlas-web"' in checks["guard"]["live"]["holding"]                          # the whole path is one hover away
    assert len(rows) == 8


@ui_only
def test_resume_now_is_enabled_only_while_something_waits(checks: dict[str, Any]) -> None:
    live = checks["guard"]["live"]
    for state in ("off", "armed", "armedThree", "armedNoCaps", "holdingNobody", "holdingNoResetTime", "holdingAMomentLeft", "pending",
                  "beforeTheFirstCycle", "holdingBusy"):
        assert resume_disabled(live[state]), state
    for state in ("holding", "holdingMany", "armedStillWaiting", "holdingHostile"):
        assert not resume_disabled(live[state]), state                                   # a session that is still waiting can be released
    assert 'data-action="guard-resume"' in live["holding"] and ">Resume now</button>" in live["holding"]
    assert ">Resuming…</button>" in live["holdingBusy"]                                  # and a click on its way is not sent twice


@ui_only
def test_the_hold_says_so_when_it_does_not_know_when_it_ends_and_when_it_is_about_to(checks: dict[str, Any]) -> None:
    live = checks["guard"]["live"]
    assert ("RESUMES", "NO RESET TIME KNOWN") in kv(live["holdingNoResetTime"])
    assert kv(live["holdingAMomentLeft"])[1] == ("RESUMES", f"{checks['guard']['clock']['resumes']} · NOW")
    assert kv(live["holdingNobody"])[2:] == [("TOLD THIS EPISODE", "NONE YET"), ("WAITING", "NONE")]
    assert kv(live["armedStillWaiting"])[0] == ("WAITING", "1 SESSION")                  # the hold just ended but a session has not woken yet


@ui_only
def test_a_long_list_of_sessions_is_cut_and_counted(checks: dict[str, Any]) -> None:
    rows = kv(checks["guard"]["live"]["holdingMany"])
    labels = [label for label, _value in rows]
    assert rows[labels.index("TOLD THIS EPISODE")][1] == "7 SESSIONS" and rows[labels.index("WAITING")][1] == "7 SESSIONS"
    # the limit, when it ends, the told header and its five and a "more" row, the waiting header and its five and a "more" row
    assert labels.count("AND 2 MORE") == 2 and len(rows) == 1 + 1 + 1 + 5 + 1 + 1 + 5 + 1
    assert [row for row in rows if row[0] == "AND 2 MORE"] == [("AND 2 MORE", "")] * 2


@ui_only
def test_the_guard_blocks_open_as_placeholders_and_say_so_when_the_monitor_does_not_answer(checks: dict[str, Any]) -> None:
    live, setup = checks["guard"]["live"], checks["guard"]["setup"]["pending"]
    for html in (live["pending"], live["beforeTheFirstCycle"], live["odd"]):
        assert 'class="nm-chip" data-pending=""' in html and '<p class="nm-story" data-pending=""' in html and resume_disabled(html)
        assert html.count('data-pending=""') == 2 == html.count('data-pending="" aria-hidden="true"')     # nothing to read yet, so nothing is read out
    assert setup.count('data-pending=""') == 3 == setup.count('data-pending="" aria-hidden="true"') and kv(setup)[0][0] == "HOOKS"
    assert 'class="nm-error"' in live["lost"] and "could not get the guard" in live["lost"] and "data-pending" not in live["lost"]
    assert live["pending"] != live["off"]


@ui_only
def test_everything_the_card_was_told_is_escaped(checks: dict[str, Any]) -> None:
    """Session ids, folders, limit labels and every message of setup arrive from outside the page: none may become markup."""
    for html in (checks["guard"]["live"]["holdingHostile"], checks["guard"]["setup"]["hostile"]):
        page = Markup(html)
        assert page.find("img") == [] and not any("onerror" in attrs for _tag, attrs in page.tags)
        assert "<img" not in html.lower()
    shown = " ".join(Markup(checks["guard"]["setup"]["hostile"]).text)
    assert shown.count(HOSTILE) == 7                      # the reason, what changed and what did not, the backup, the warning, the error, the command
    assert HOSTILE.upper() in " ".join(Markup(checks["guard"]["live"]["holdingHostile"]).text)   # a label is shown in capitals, as text


@ui_only
def test_the_setup_status_line_says_hooks_the_tool_and_the_claude_code_version(checks: dict[str, Any]) -> None:
    setup = checks["guard"]["setup"]
    assert kv(setup["missing"]) == [("HOOKS", "NOT INSTALLED"), ("MCP TOOL", "NOT REGISTERED"), ("CLAUDE CODE", "2.1.160")]
    assert kv(setup["installed"]) == [("HOOKS", "INSTALLED"), ("MCP TOOL", "REGISTERED"), ("CLAUDE CODE", "2.1.160")]
    assert kv(setup["partial"]) == [("HOOKS", "PARTLY INSTALLED (2 OF 3)"), ("MCP TOOL", "A DIFFERENT PROGRAM"), ("CLAUDE CODE", "2.1.160")]
    assert kv(setup["tooOld"])[2] == ("CLAUDE CODE", "2.1.94 · NEEDS 2.1.139 OR NEWER") and "older than 2.1.139" in setup["tooOld"]
    assert kv(setup["versionUnknown"])[2] == ("CLAUDE CODE", "VERSION UNKNOWN") and kv(setup["noClaude"])[2] == ("CLAUDE CODE", "NOT FOUND")
    assert kv(setup["fromSource"])[:2] == [("HOOKS", "UNKNOWN"), ("MCP TOOL", "UNKNOWN")]
    assert "The Claude Code settings file is not valid JSON" in setup["unreadable"]       # what setup would refuse, said before the click


def buttons(html: str) -> dict[str, bool]:
    """Install and Remove: whether each is disabled."""
    return {name: bool(re.search(rf'data-action="guard-{name}"[^>]* disabled', html)) for name in ("install", "remove")}


@ui_only
def test_install_and_remove_are_there_when_setup_can_run_and_the_cost_is_always_said(checks: dict[str, Any]) -> None:
    setup = checks["guard"]["setup"]
    for name in ("missing", "installed", "partial", "tooOld", "versionUnknown", "unreadable"):
        assert buttons(setup[name]) == {"install": False, "remove": False}, name
    for name in ("pending", "noClaude", "fromSource", "installing", "removing"):               # no answer yet, no setup here, or one click on its way
        assert buttons(setup[name]) == {"install": True, "remove": True}, name
    for name, html in setup.items():
        assert f'<p class="nm-lede">{GUARD_COST}</p>' in html, name                              # what it costs is said whatever the state
        install = "Installing…" if name == "installing" else "Install"                          # the label of a click on its way says so
        remove = "Removing…" if name == "removing" else "Remove"
        assert re.findall(r'data-action="guard-(?:install|remove)"[^>]*>([^<]*)</button>', html) == [install, remove], name


@ui_only
def test_when_setup_cannot_run_it_says_why_and_points_at_the_manual_setup(checks: dict[str, Any]) -> None:
    setup = checks["guard"]["setup"]
    for name, reason in (("fromSource", "One-click setup works in the installed app. This copy runs from source."),
                         ("noClaude", "Claude Code was not found on this PC")):
        page = Markup(setup[name])
        assert "Setup is not available here." in page.text and any(reason in text for text in page.text), name
        link = [attrs for attrs in page.find("a")][0]
        assert link["href"].endswith("/docs/AGENT_HOOK.md") and link["data-external"] == "1", name
        assert "docs/AGENT_HOOK.md" in page.text, name
        assert buttons(setup[name]) == {"install": True, "remove": True}
    assert "Setup is not available" not in setup["missing"]


@ui_only
def test_a_click_shows_what_changed_the_backup_the_warnings_and_the_errors_each_in_its_place(checks: dict[str, Any]) -> None:
    setup = checks["guard"]["setup"]
    done = setup["installed_ok"]
    assert '<div class="nm-callout" role="status"><b>Installed.</b> Added the SessionStart hook. Registered the claude-usage MCP tool.<br>' in done
    assert 'Backup: <span class="nm-path">C:\\Users\\t\\.claude\\settings.json.cum-backup-20261028-180509</span>' in done
    assert '<div class="nm-callout is-warn" role="note">The VS Code extension has its own Claude Code version; it also needs 2.1.139 or newer.</div>' in done
    assert "<b>Removed.</b> Removed the PostToolUse hook." in setup["removed_ok"]
    assert "<b>Nothing to change.</b> The three hooks were already in place." in setup["nothing"]
    refused = setup["refused"]
    assert '<p class="nm-error" role="alert">Claude Code 2.1.94 is older than 2.1.139' in refused and "nm-callout" not in refused   # no empty box above it
    by_hand = setup["byHand"]
    assert "To do it by hand, run: <span class=\"nm-path\">claude mcp add --transport stdio --scope user claude-usage" in by_hand
    assert "<b>Installed.</b> Added the SessionStart hook." in by_hand and '<div class="nm-callout is-warn" role="note">The MCP tool was not registered.' in by_hand


@ui_only
def test_each_block_is_rewritten_only_when_it_changed(checks: dict[str, Any]) -> None:
    sync = checks["guard"]["sync"]
    assert sync["first"] == 2 and sync["unchanged"] == 2 and sync["afterOneChange"] == 3 and sync["liveHasButtonDisabled"] is True


@ui_only
def test_the_card_asks_for_the_guard_once_the_page_is_up_and_visible_and_it_is_time(checks: dict[str, Any]) -> None:
    polling = checks["guard"]["polling"]
    assert polling["due"] == ["GET /api/guard"]
    for name in ("notYet", "inFlight", "elsewhere", "hidden", "pageNotBuilt"):
        assert polling[name] == [], name


@ui_only
def test_resume_now_posts_once_says_so_and_asks_for_the_new_state(checks: dict[str, Any]) -> None:
    actions = checks["guard"]["actions"]
    assert actions["resume"]["calls"] == ["POST /api/guard/resume", "GET /api/guard"] and actions["resume"]["learned"] is True
    assert actions["resume"]["status"] == {"text": "RESUME REQUESTED", "error": False} and actions["resume"]["busy"] is False
    assert actions["resumeTwice"] == []                                                  # a click while one is on its way is ignored
    for name, text in (("resumeRefused", "RESUME FAILED: COULD NOT RECORD THE RESUME REQUEST"),
                       ("resumeLost", "RESUME FAILED: THE DASHBOARD LOST ITS LINK")):
        assert actions[name]["status"] == {"text": text, "error": True} and actions[name]["busy"] is False, name
        assert re.search(r"[A-Z]", text) and not re.search(r"[a-z]", text)               # capitals all the way: Gates writes it in sentence case


@ui_only
def test_install_and_remove_post_their_action_show_the_result_and_look_again(checks: dict[str, Any]) -> None:
    actions = checks["guard"]["actions"]
    assert actions["install"]["calls"] == ['POST /api/guard/setup {"action":"install"}', "GET /api/guard"]
    assert actions["whileRunning"]["busy"] == "install" and "Installing…" not in actions["install"]["shown"]
    assert actions["install"]["busy"] == "" and actions["install"]["result"]["changed"] == ["Added the SessionStart hook."]
    assert "<b>Installed.</b> Added the SessionStart hook." in actions["install"]["shown"]                  # the block shows what it said
    assert actions["remove"]["calls"] == ['POST /api/guard/setup {"action":"remove"}', "GET /api/guard"]
    assert actions["remove"]["result"]["action"] == "remove"
    assert actions["refusedByTheMonitor"]["result"]["errors"] == ["not valid JSON"]
    assert actions["serverError"]["result"] == {"ok": False, "action": "install", "changed": [], "unchanged": [], "backup": None,
                                                "warnings": [], "errors": ["The monitor could not run setup (setup install failed)."],
                                                "manual": None}
    assert actions["setupLost"]["result"]["errors"] == ["The dashboard lost its link to the monitor."] and actions["setupLost"]["busy"] == ""
    assert actions["setupLost"]["calls"] == ['POST /api/guard/setup {"action":"remove"}']
    assert actions["setupTwice"] == []                                                   # one at a time


@ui_only
@pytest.mark.parametrize(("label", "key", "posted"), [
    ("capBlank", "guard_session_cap", None), ("capSpaces", "guard_weekly_cap", None), ("cap1", "guard_session_cap", 1),
    ("cap95", "guard_session_cap", 95), ("cap100", "guard_scoped_cap", 100), ("stagger0", "guard_resume_stagger_s", 0),
    ("stagger120", "guard_resume_stagger_s", 120), ("stagger600", "guard_resume_stagger_s", 600),
])
def test_a_field_the_server_would_take_is_posted_as_a_whole_number_or_null(checks: dict[str, Any], label: str, key: str,
                                                                          posted: int | None) -> None:
    result = checks["guard"]["actions"]["fields"][label]
    assert result["wrote"] == [{key: posted}] and result["error"] == "" and result["shown"] is False
    assert result["status"].startswith("SAVED ") and server.SETTING_SPECS[key].check(posted)


@ui_only
@pytest.mark.parametrize(("label", "key", "value", "line"), [
    ("cap0", "guard_session_cap", "0", "Enter a whole number from 1 to 100"), ("cap101", "guard_weekly_cap", "101", "Enter a whole number from 1 to 100"),
    ("capDecimal", "guard_session_cap", "9.5", "Enter a whole number from 1 to 100"), ("capNegative", "guard_scoped_cap", "-5", "Enter a whole number from 1 to 100"),
    ("capText", "guard_session_cap", "abc", "Enter a whole number from 1 to 100"), ("capExp", "guard_session_cap", "1e2", "Enter a whole number from 1 to 100"),
    ("capLong", "guard_session_cap", "0095", "Enter a whole number from 1 to 100"),
    ("stagger601", "guard_resume_stagger_s", "601", "Enter a whole number of seconds from 0 to 600"),
    ("staggerBlank", "guard_resume_stagger_s", "", "Enter a whole number of seconds from 0 to 600"),
    ("staggerNegative", "guard_resume_stagger_s", "-1", "Enter a whole number of seconds from 0 to 600"),
    ("staggerDecimal", "guard_resume_stagger_s", "1.5", "Enter a whole number of seconds from 0 to 600"),
])
def test_a_field_the_server_would_refuse_is_refused_in_the_page_under_the_field(checks: dict[str, Any], label: str, key: str,
                                                                               value: str, line: str) -> None:
    result = checks["guard"]["actions"]["fields"][label]
    assert result["wrote"] == [] and result["lines"] == 0 and result["shown"] is True and result["error"].startswith(line)


@ui_only
def test_a_guard_setting_asks_the_guard_for_its_new_status_and_no_other_setting_does(checks: dict[str, Any]) -> None:
    actions = checks["guard"]["actions"]
    assert actions["toggle"]["calls"] == ['POST /api/settings {"guard_enabled":true}', "GET /api/state", "GET /api/guard"]
    assert actions["toggle"]["status"] == "SAVED USAGE GUARD"
    assert actions["otherSettingDoesNotAskTheGuard"] == []


# ── Top sessions ──────────────────────────────────────────────────────────────────

@ui_only
def test_the_folder_column_is_as_wide_as_the_longest_folder_name_needs(checks: dict[str, Any]) -> None:
    column = {k: int(v) for k, v in checks["folderColumn"].items()}
    assert column["short"] == 14 and column["short"] < column["usual"] < column["long"] == 40      # a share of the table, 14 to 40 %
    assert "style=\"width:' + folderPct + '%\"" in function_body(APP_CODE, "sessionsTable")


# ── The forecast of a weekly or scoped limit ──────────────────────────────────────

@ui_only
def test_the_forecast_runs_from_now_at_the_average_rate_to_the_reset_or_to_100_percent(checks: dict[str, Any]) -> None:
    forecast = checks["forecast"]
    below = forecast["below"]
    assert below["reaches"] is None and below["end"] == 40
    assert below["points"][0][1] == 10 and below["points"][1] == [forecast["reset"], 40]       # to the reset, at the report's own figure
    crossing = forecast["crossing"]
    assert crossing["reaches"] == pytest.approx(forecast["expectCross"])
    assert crossing["points"][1] == [crossing["reaches"], 100] and crossing["points"][0][0] < crossing["reaches"] < forecast["reset"]
    assert forecast["exact"]["reaches"] == forecast["reset"]                                  # 100 % exactly at the reset
    assert forecast["reached"] is None and forecast["noProjection"] is None and forecast["over"] is None


@ui_only
def test_the_forecast_line_marks_when_it_crosses_100_percent_and_agrees_with_the_card(checks: dict[str, Any]) -> None:
    card = checks["forecastCard"]
    assert card["crossingForecastDrawn"] and card["stayingForecastDrawn"]
    assert card["crossingLabel"] and card["crossingLabel"] == card["crossingClock"]            # "100% ~ <the moment>" on the chart
    assert card["crossingTip"] == f"Reaches 100% about {card['crossingLabel']} at this rate"
    assert card["crossingFacts"] == ["120.4%", card["crossingLabel"]]                         # Projected end, Limit reached
    assert card["stayingLabel"] is False and card["stayingFacts"] == ["40.0%", False]         # no crossing, no marker, no fact


# ── CI ────────────────────────────────────────────────────────────────────────────

def test_ci_runs_the_script_tests_with_node() -> None:
    """Plain `python -m pytest` skips every test that starts a process, node included, so a syntax error in
    app.js passed CI. The workflow runs this file once more with CUM_UI_TESTS=1."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "CUM_UI_TESTS" in text and "tests/test_webui.py" in text and "setup-node" in text
    assert "tests/test_motion.py" in text                                          # motion.js is run under node there too


def test_the_test_hook_is_inert_unless_a_test_sets_it() -> None:
    code = strip_comments(APP_CODE)
    assert code.count("__CUM_TEST__") == 2                                         # the guard and the hand-over
    hook = code[code.index("if (window.__CUM_TEST__)"):]
    assert hook.index("return;") < hook.index("init();")                           # the page is not started under test
