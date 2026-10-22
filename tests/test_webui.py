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
the currency of real money, the plan multiple, the health rows, the plan price. A syntax error, a lost x100 or an
unescaped title fails there. Those tests start node, so they need CUM_UI_TESTS=1 like every test that starts a
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

PAGES = ["overview", "limits", "cost", "nerds", "history", "settings", "about"]
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
    """The text of ``function name(...) { ... }``, found by counting braces outside string literals."""
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
    for pattern in (r'toggleRow\("([a-z_]+)"', r'selectRow\("([a-z_]+)"', r'data-key="([a-z_]+)"',
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
    assert re.findall(r'<script src="([^"]+)"', INDEX) == ["/static/charts.js", "/static/app.js"]
    assert not re.search(r"<script(?![^>]*\bsrc=)", INDEX), "an inline script (the CSP forbids it)"
    for needle in ('id="theme-css"', 'id="main"', 'id="menu"', 'id="variants"', 'id="refresh"', 'id="boot"'):
        assert needle in INDEX, needle


def test_the_pages_make_no_requests_beyond_the_monitor_and_the_about_links() -> None:
    links = re.search(r"var LINKS = \{(.*?)\};", APP_CODE, re.S).group(1)
    allowed = set(re.findall(r'"(https://[^"]+)"', links))
    assert len(allowed) == 3
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
    assert set(re.findall(r"(\w+):", allowed)) == {"costWindow", "nerdsWindow", "folderMode", "calMetric", "matrixMetric"}


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
    assert base and (int(base.group(1)), int(base.group(2))) == (643, 201)
    short = re.search(r"@media \(min-width: 1001px\) and \(max-height: (\d+)px\) \{(.*?)\n\}", CSS, re.S)
    assert short and int(short.group(1)) < 800 and "min-height: 630px" in short.group(2)
    assert "minmax(188px, 196fr)" in short.group(2) and ".nm-sub { max-width: 680px; }" in short.group(2)


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
                         check=False)
    assert run.returncode == 0, run.stderr[-400:]


@ui_only
def test_chart_text_and_marks_stay_inside_their_chart() -> None:
    run = subprocess.run([node_binary(), "-e", CHART_BOUNDS_JS, str(WEBUI / "charts.js")], capture_output=True,
                         text=True, timeout=60, check=False)
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
                         check=False, encoding="utf-8")
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


# ── CI ────────────────────────────────────────────────────────────────────────────

def test_ci_runs_the_script_tests_with_node() -> None:
    """Plain `python -m pytest` skips every test that starts a process, node included, so a syntax error in
    app.js passed CI. The workflow runs this file once more with CUM_UI_TESTS=1."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "CUM_UI_TESTS" in text and "tests/test_webui.py" in text and "setup-node" in text


def test_the_test_hook_is_inert_unless_a_test_sets_it() -> None:
    code = strip_comments(APP_CODE)
    assert code.count("__CUM_TEST__") == 2                                         # the guard and the hand-over
    hook = code[code.index("if (window.__CUM_TEST__)"):]
    assert hook.index("return;") < hook.index("init();")                           # the page is not started under test
