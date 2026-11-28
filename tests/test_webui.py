"""Static checks on the dashboard's web files: usage_monitor/webui (index.html, app.js, charts.js, the
theme files of all three themes).

No browser, no window, no server and no network: the files are read as text and checked against the
Python side they depend on (the statistics routes, the settings whitelist, the window names) and
against the rules the page has to keep (no inline handlers, no literal colours, no external addresses,
every figure explained, motion honoured). The one test that starts a process (node --check) is skipped
unless USAGE_MONITOR_UI_TESTS=1, like every test that starts a process.

The scripts themselves run under node, in tests/webui_checks.js: charts.js and app.js are loaded into a vm
context with a small fake DOM and window.__USAGE_MONITOR_TEST__ set, so app.js hands back its helpers (the test hook at its
end) and the tests below assert on what they return: escaping, the percent formats, the pace band, the model mix,
the currency of real money, the plan multiple, the health rows, the plan price, the card layout of every page before
any number has arrived, the update banner, the forecast line, the sign-in help and the pages of a PC with no Claude Code.
A syntax error, a lost x100 or an unescaped title fails there. Those tests start node, so they need USAGE_MONITOR_UI_TESTS=1 like every test that starts a
process; .github/workflows/tests.yml sets it for this file (and a test below checks that it does).

What these cannot show is how the page looks and behaves; that is checked by loading it in a browser
against the dev server (python -m usage_monitor.ui.devserver).
"""
from __future__ import annotations

import hashlib
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

from usage_monitor import casing, cldr, formats, i18n, paths, themes
from usage_monitor.data import analytics, windows
from usage_monitor.render import model as widget_model
from usage_monitor.themes import colour
from usage_monitor.ui import dashboard, server

WEBUI = paths.resource_dir() / "usage_monitor" / "webui"
CHECKS_JS = Path(__file__).resolve().parent / "webui_checks.js"
WORKFLOW = paths.resource_dir() / ".github" / "workflows" / "tests.yml"
UI_TESTS = os.environ.get("USAGE_MONITOR_UI_TESTS") == "1"
ui_only = pytest.mark.skipif(not UI_TESTS, reason="needs USAGE_MONITOR_UI_TESTS=1 (starts a child process)")
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
ALL_SCRIPTS = {"app.js": APP, "charts.js": CHARTS, "formats.js": read("formats.js"),
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
    routed = re.findall(r'\{ key: "([a-z]+)" \}', APP)
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
    # Monday first: English's names are formats.js's (#91), which app.js's WEEKDAYS_MON takes in the page's language
    assert "WEEKDAYS_MON" in APP_CODE and read("formats.js").count('"MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"') == 1


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
    assert INDEX.count('<meta name="monitor-locale" content="">') == 1    # and this, the language (#87)
    assert INDEX.count('<html lang="en"') == 1                                # and the page's language
    scripts = re.findall(r'<script src="([^"]+)"', INDEX)
    # app.js last: it starts the page. The catalog before charts.js (the server names its language), so the first paint
    # has the text; only the formats (#91) and the motion layer before that, the formats first, so that any script after
    # them can use them.
    assert scripts[-3:] == ["/static/i18n/en.js", "/static/charts.js", "/static/app.js"]
    assert scripts[:-3] == ["/static/formats.js", "/static/motion.js"]
    assert not re.search(r"<script(?![^>]*\bsrc=)", INDEX), "an inline script (the CSP forbids it)"
    for needle in ('id="theme-css"', 'id="main"', 'id="menu"', 'id="variants"', 'id="refresh"', 'id="boot"'):
        assert needle in INDEX, needle


def test_the_pages_make_no_requests_beyond_the_monitor_and_the_about_links() -> None:
    links = re.search(r"var LINKS = \{(.*?)\};", APP_CODE, re.S).group(1)
    allowed = set(re.findall(r'"(https://[^"]+)"', links))
    # the repository, the bug form, Patreon, the guard's manual setup (docs/AGENT_HOOK.md), and the README section on using
    # Claude on the web or in the app, which the sign-in help links (#28)
    assert len(allowed) == 5
    # The install command the sign-in help shows for the user to run in PowerShell (#28). It is text in a code element: nothing
    # links, opens or fetches it (test_the_commands_are_shown_and_copied_and_never_linked_or_fetched), so the page still asks
    # claude.ai for nothing.
    shown = {"https://claude.ai/install.ps1"}
    namespaces = {"http://www.w3.org/2000/svg"}          # the name of the SVG markup language: an identifier, never fetched
    for name, text in ALL_SCRIPTS.items():
        assert set(re.findall(r"https?://[^\s\"')]+", strip_comments(text))) <= allowed | shown | namespaces, name
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


def test_storage_is_always_guarded_and_the_dashboards_choices_live_in_the_settings() -> None:
    for source in (APP_CODE, *(strip_comments(js) for js in THEME_JS.values())):
        for m in re.finditer(r"(?:local|session)Storage\.", source):
            assert "try {" in source[max(0, m.start() - 260):m.start()], "storage used outside a try block"
    # #54: browser storage belongs to the page's origin, whose port is new on every run, so the page keeps nothing there
    assert "localStorage." not in strip_comments(APP_CODE) and "sessionStorage." not in strip_comments(APP_CODE)
    assert "loadPrefs(SAVED_PREFS);" in APP_CODE                 # the saved choices are taken before the first paint
    init = APP_CODE[APP_CODE.index("  function init() {"):]
    init = init[:init.index("\n  }\n")]
    assert init.index("loadPrefs(SAVED_PREFS);") < init.index("renderPage(")   # ... before anything in init can paint
    choices = re.search(r"var PREF_CHOICES = \{(.*?)\};", APP_CODE, re.S).group(1)
    assert set(re.findall(r"(\w+):", choices)) == set(server.DASHBOARD_PREF_CHOICES)
    assert '<meta name="monitor-prefs" content="">' in INDEX


# ── Charts ────────────────────────────────────────────────────────────────────────

def test_charts_js_offers_every_chart_the_pages_need() -> None:
    exported = re.search(r"root\.MonitorCharts = \{(.*?)\};", CHARTS, re.S).group(1)
    for name in ("sparkline", "line", "columns", "hbars", "stackedBar", "calendar", "matrix", "bind",
                 "niceStep", "levels", "refresh", "pips"):
        assert re.search(rf"\b{name}\b", exported), name
    for call in ("CHARTS.calendar(", "CHARTS.matrix(", "CHARTS.hbars(", "CHARTS.stackedBar(", "CHARTS.line(",
                 "CHARTS.columns(", "CHARTS.sparkline("):
        assert call in APP_CODE, call


def test_chart_items_are_keyboard_and_screen_reader_stops() -> None:
    item = function_body(CHARTS, "item")
    for needle in ("mon-item", "tabindex", 'role="img"', "aria-label", "data-i"):
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
    assert f"USAGE_MONITOR_THEME_FX.{theme_id} = {{ start: start }}" in THEME_JS[theme_id]


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
    assert re.search(r"<em\b", INDEX) and 'id="data-age"' in INDEX and 'id="uplink"' in INDEX     # the rule names real elements


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
    """Night City draws the bar in Edgerun only. Outside Edgerun a rule may set only its label's capitals (#98: the label is
    the catalog's natural-case words, and the hidden bar keeps the capitals it always had), which draws nothing."""
    css = THEME_CSS["night_city"]
    rules = re.findall(r"^([^{}\n]*\.nm-remain[^{}\n]*)\{([^}]*)\}", css, re.M)
    selectors = [(part.strip(), body.strip()) for rule, body in rules for part in rule.split(",")]
    assert selectors and all(part.startswith(':root[data-variant="edgerun"] ') or (part == ".nm-remain .l" and body == "text-transform: uppercase;")
                             for part, body in selectors), selectors
    for theme_id in ("nostromo", "gates"):                                      # these read the reset in the facts
        assert re.search(r"^\.nm-remain \{ display: none; \}", THEME_CSS[theme_id], re.M), theme_id
    assert '".nm-remain{display:none}"' in APP_CODE                            # and so does a theme that says nothing


def test_the_longer_words_rules_hold_only_outside_english() -> None:
    """#98's owner question 1: outside English a line longer than its room wraps. Each theme sheet ends with its block of those
    rules, and every rule in it (in a media query too) is held to a page whose language is not English, so English's layout
    cannot change; no other rule of the sheet names a language."""

    def top_level(prelude: str) -> list[str]:                 # a selector list split at its own commas, not :is()'s
        parts, depth, start = [], 0, 0
        for i, ch in enumerate(prelude):
            depth += {"(": 1, ")": -1}.get(ch, 0)
            if ch == "," and depth == 0:
                parts.append(prelude[start:i].strip())
                start = i + 1
        return parts + [prelude[start:].strip()]

    for theme_id in ("nostromo", "night_city", "gates"):
        css = THEME_CSS[theme_id]
        mark = css.index("/* Outside English (#98, owner question 1)")
        assert ":lang(" not in re.sub(r"/\*.*?\*/", "", css[:mark], flags=re.S), theme_id
        block = re.sub(r"/\*.*?\*/", "", css[mark:], flags=re.S)
        selectors = [part for prelude in re.findall(r"([^{}]+)\{", block) if not prelude.strip().startswith("@media")
                     for part in top_level(prelude.strip())]
        assert selectors, theme_id
        stray = [part for part in selectors if not part.startswith(":root:not(:lang(en)) ")]
        assert not stray, (theme_id, stray)


def outside_english_rules(theme_id: str) -> list[tuple[str, str]]:
    """Each rule of a theme sheet's Outside English block (#98's owner question 1), in a media query too: (selector, body),
    with the comments taken out."""
    css = THEME_CSS[theme_id]
    block = re.sub(r"/\*.*?\*/", "", css[css.index("/* Outside English (#98, owner question 1"):], flags=re.S)
    return [(" ".join(sel.split()), body) for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", block)]


def test_outside_english_long_text_follows_the_owners_rules() -> None:
    """#98's owner question 1, as decided on #98, in each sheet's Outside English block: nothing shrinks (no font, no scale,
    no zoom, no hyphenation); the only ellipsis of the block is Nostromo's pace chip in a limit card's facts, a designed cut,
    and the Overview's card metas keep English's own ellipsis in Nostromo and Night City (one line beside the title, the
    title whole up to 70 % of the row, the meta alone giving way), where Gates moves them below the title as in English; nothing sets a nav tab, the link's words, a
    sentence line or a chip's own word on one line but the chip, which never wraps; a card's title row wraps, so a chip
    moves below its title; a nav tab that wraps grows where its tab has a fixed height (Night City, Gates); a word longer
    than a sentence's line breaks (overflow-wrap: break-word in Nostromo's pace column, anywhere in Night City's and Gates'
    wrapping pace rows); the link's words wrap; a limit's facts wrap within 60 % of the row with their figure on one line;
    and the meter's bar keeps tests/browser/page_checks.js's BAR_FLOOR."""
    checks_js = (Path(__file__).resolve().parent / "browser" / "page_checks.js").read_text(encoding="utf-8")
    floor = re.search(r"^var BAR_FLOOR = (\d+);$", checks_js, re.M).group(1)
    meta = ":root:not(:lang(en)) .nm-grid .nm-card-t:not(.nm-chart-t) > .nm-meta-t"
    for theme_id in ("nostromo", "night_city", "gates"):
        rules = outside_english_rules(theme_id)
        bodies = {sel: body for sel, body in rules}
        for sel, body in rules:
            assert not re.search(r"(^|[;\s])font(-size)?\s*:|hyphens|scale\(|zoom", body), (theme_id, sel)
            if "ellipsis" in body:
                assert (theme_id, sel) == ("nostromo", ":root:not(:lang(en)) .nm-grid .nm-row2 > .nm-facts > .nm-chip"), (theme_id, sel)
            if re.search(r"\.nm-menu|\.nm-uplink|\.nm-pace|\.fc\b|\.nm-story", sel):
                assert "nowrap" not in body and "ellipsis" not in body, (theme_id, sel)
            if re.search(r"(?<!:not\()\.nm-chip\b", sel):                # a chip itself, not an element that is not one
                assert "white-space: normal" not in body, (theme_id, sel)
            if ".nm-meta-t" in sel and ".nm-grid" in sel:
                assert "overflow: visible" not in body and "text-overflow" not in body, (theme_id, sel)
        uplink = [body for sel, body in rules if sel.endswith(" .nm-uplink")]
        assert uplink and all("white-space: normal" in body for body in uplink), theme_id
        assert re.search(rf"minmax\({floor}px, 1fr\)", bodies[":root:not(:lang(en)) .nm-meter"]), theme_id
        assert "max-width: 60%" in bodies[":root:not(:lang(en)) .nm-row2 > .nm-facts"], theme_id
        assert any(sel.endswith(" b") and ".nm-facts" in sel and "white-space: nowrap" in body for sel, body in rules), theme_id
        # a word longer than its line breaks: Nostromo's pace is a column, where break-word does it; Night City's and Gates' pace
        # rows wrap their items, and only anywhere lets one narrow below its longest word (#98's second review)
        wrap = "overflow-wrap: break-word" if theme_id == "nostromo" else "overflow-wrap: anywhere"
        assert any(".nm-pace" in sel and wrap in body for sel, body in rules), theme_id
        assert theme_id == "nostromo" or not any(".nm-pace" in sel and "overflow-wrap: break-word" in body for sel, body in rules), theme_id
        # a nav tab that wraps grows with its two lines where the look's tab has a fixed height (Nostromo's two lines fit its 34 px)
        assert theme_id == "nostromo" or any(sel == ":root:not(:lang(en)) .nm-menu a" and "height: auto" in body for sel, body in rules), theme_id
        if theme_id == "gates":
            assert re.search(r"^\.nm-card-t \{ display: flex; flex-wrap: wrap;", THEME_CSS["gates"], re.M)
            assert not any(".nm-meta-t" in sel for sel, _ in rules)
            assert any(sel == ":root:not(:lang(en)) .nm-menu a" and "white-space: normal" in body for sel, body in rules)
        else:
            assert "white-space: nowrap" in bodies[meta], theme_id
            title = bodies[":root:not(:lang(en)) .nm-grid .nm-card-t:not(.nm-chart-t):has(> .nm-meta-t) > :is(.nm-tag, .nm-ttl)"]
            assert "flex-shrink: 0" in title and "max-width: 70%" in title, theme_id
            assert "flex-wrap: nowrap" in bodies[":root:not(:lang(en)) .nm-grid .nm-card-t:not(.nm-chart-t):has(> .nm-meta-t)"], theme_id
            # a card's title row wraps, so a chip moves below its title (Gates' row wraps in every language)
            assert "flex-wrap: wrap" in bodies[":root:not(:lang(en)) .nm-card-t"], theme_id
            # English's own cut, which the designed cut keeps outside English
            english = THEME_CSS[theme_id][:THEME_CSS[theme_id].index("/* Outside English (#98, owner question 1")]
            assert ".nm-grid .nm-card-t:not(.nm-chart-t) .nm-meta-t { min-width: 0; overflow: hidden; text-overflow: ellipsis; }" in english
    assert "white-space: normal" in dict(outside_english_rules("night_city"))[":root:not(:lang(en)) .nm-menu a"]


def test_the_constrained_keys_notes_give_their_room() -> None:
    """#98's owner question 1, as decided: each constrained key's note in en.json gives translators its room, as the stand-in
    measured it (tests/browser/page_probe.js's rooms step, which fails a note more than 1 px or 1 capital off): every key
    its ROOM_KEYS names says "room about N px, about C capitals". The header's link words say they are never cut, and a
    sentence line's note asks for no shorthand."""
    probe = (Path(__file__).resolve().parent / "browser" / "page_probe.js").read_text(encoding="utf-8")
    listed = re.search(r"^const ROOM_KEYS = \{(.*?)^\};$", probe, re.S | re.M).group(1)
    keys = re.findall(r"'([a-z_]+(?:\.[a-z_]+)+)'", listed)
    assert len(keys) == len(set(keys)) == 33
    assert {f"shell.nav.{page}" for page in PAGES} <= set(keys)
    for key in keys:
        assert key in ENGLISH_CATALOG, key
        assert re.search(r"room about \d+ px, about \d+ capitals", ENGLISH_CATALOG["_note." + key]), key
    for code in ("live", "loading", "stale", "offline", "rate_limited", "no_credentials", "paused", "lost"):
        assert "never cut" in ENGLISH_CATALOG[f"_note.header.link.{code}"], code
        assert "never cut" in ENGLISH_CATALOG[f"_note.page.gates.link.{code}"], code
    for key in ("limit.pace.line", "limit.diff.ahead", "limit.forecast.weekly", "limit.forecast.at_risk"):
        assert "no shorthand" in ENGLISH_CATALOG["_note." + key], key


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


# ── JavaScript (child processes: only with USAGE_MONITOR_UI_TESTS=1) ────────────────────────

# Loads charts.js in node (no DOM needed to build a string) and looks for text or marks drawn outside the
# chart's own width, and for text drawn over text. Text is taken as 7.9 px a character, as charts.js itself does.
CHART_BOUNDS_JS = r"""
const fs = require('fs'), vm = require('vm');
const box = {}; box.window = box; vm.createContext(box);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), box);
const C = box.MonitorCharts, CHAR = 7.9, problems = [];
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


# Every format tag of French, Spanish and Turkish (#91): webui_checks.js writes the page's money boxes in each.
CLDR_TAGS = sorted(set(cldr.REGIONS) | {i18n.BASE_FORMATS[language] for language in cldr.BASES})


@pytest.fixture(scope="module")
def checks() -> dict[str, Any]:
    """What tests/webui_checks.js found out by running formats.js, charts.js and app.js (one node run for the module)."""
    run = subprocess.run([node_binary(), str(CHECKS_JS), str(WEBUI), json.dumps(CLDR_TAGS)], capture_output=True,
                         text=True, timeout=60, check=False, encoding="utf-8", creationflags=NO_WINDOW)
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
        big = next(text for text in page.text if re.search(r"\d", text) and "used" not in text and not text.startswith("Of"))
        of = next(text for text in page.text if text.startswith("Of "))       # limits.credits.of, in natural case (#98)
        return big, of

    assert amounts(checks["spend"]["EUR"]) == ("\u20ac12.50", "Of \u20ac50.00")      # the toast says "Used 12.50 of 50.00 EUR"
    assert amounts(checks["spend"]["GBP"]) == ("\u00a312.50", "Of \u00a350.00")
    assert amounts(checks["spend"]["USD"]) == ("$12.50", "Of $50.00")
    assert amounts(checks["spend"]["none"]) == ("$12.50", "Of $50.00")            # no currency reported: read as USD
    assert amounts(checks["spend"]["lower"]) == ("\u20ac12.50", "Of \u20ac50.00")
    assert amounts(checks["spend"]["bad"]) == ("12.50 EURO", "Of 50.00 EURO")      # a code the browser cannot use
    assert amounts(checks["spend"]["jpy"]) == ("\u00a513", "Of \u00a550")          # whole units where the currency has none
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
def test_the_plan_price_hint_says_what_the_latest_state_says(checks: dict[str, Any]) -> None:
    """The hint was built once, with the page. Loaded on #settings that is before the first state, and a first sign-in or a new
    plan changes the plan under a page that is open, so it kept the text of that moment until Settings was left (#46)."""
    hint = checks["planHint"]

    def said(detected: str, writes: int) -> dict[str, Any]:
        return {"text": f"USD a month. Blank uses the detected price ({detected}).", "writes": writes}

    assert hint["before"] == said("no price known", 0)
    assert hint["firstState"] == said("Max 5x, $100 a month", 1)
    assert hint["sameState"] == said("Max 5x, $100 a month", 1)         # a poll that changes nothing writes nothing
    assert hint["override"] == said("Max 5x, $100 a month", 1)          # the override is not the detected price
    assert hint["newPlan"] == said("Max 20x, $200 a month", 2)
    assert hint["noPrice"] == said("no price known", 3)
    assert hint["signedIn"] == said("Pro, $20 a month", 4)
    assert hint["noPlan"] == said("no price known", 5)
    hostile = hint["hostileName"]
    assert "<img" in hostile and hint["hostile"] == said(f"{hostile}, $20 a month", 6)    # a plan name is text, on update
    assert hint["drawnAgain"] == said(f"{hostile}, $20 a month", 6)     # the page draws the same words, so nothing is written over them
    assert hint["drawnEscaped"] is True and hint["asMarkup"] == 0       # and on first paint


@ui_only
def test_the_settings_update_line_is_about_the_release_on_offer(checks: dict[str, Any]) -> None:
    """Nothing cleared what the last click said, so a release found later stood beside "You are up to date." or the skip of an
    older one until the window closed (#47). The banner already kept its text to one release. The lines are the catalog's in
    natural case since #97 (Nostromo and Night City set them in capitals by CSS)."""
    line = checks["updateLine"]

    def offered(version: str) -> dict[str, Any]:
        return {"line": f"Version {version} is available.", "install": True}

    assert line["upToDate"] == {"line": "You are up to date.", "install": False}
    assert line["foundLater"] == offered("2.3.0")
    assert line["checking"] == {"line": "Checking\u2026", "install": True}            # a state on the way changes nothing
    assert line["checked"] == {"line": "An update is available.", "install": True}
    assert 'role="status"></p>' in line["banner"]                                      # a check's words stay out of the banner
    assert line["linkLost"] == {"line": "Check failed: the dashboard lost its link", "install": True}
    assert line["noGithub"] == {"line": "Could not reach GitHub; try again later.", "install": True}
    assert line["newerAfterThat"] == offered("2.4.0")
    assert line["skipped"] == {"line": "This version will be skipped.", "install": True}
    assert line["newerAfterSkip"] == offered("2.5.0")
    assert line["installing"] == {"line": "Opened the release page in your browser.", "install": True}
    assert line["newerAfterInstall"] == offered("2.6.0")
    assert line["installSkipped"] == {"line": "Opened the release page in your browser.", "install": True}   # a click on a skipped
    assert line["skipSkipped"] == {"line": "This version will be skipped.", "install": True}                 # release gets its answer


@ui_only
def test_the_start_with_windows_note_follows_the_state(checks: dict[str, Any]) -> None:
    """A dev instance loaded on #settings is drawn before the state says it is one, so the note said what a real install does (#47)."""
    hint = checks["startupHint"]
    normal, dev = "Run the monitor when you sign in.", "Dev instance: this is not saved to Windows."
    assert hint["before"] == {"text": normal, "writes": 0}
    assert hint["dev"] == {"text": dev, "writes": 1}
    assert hint["sameState"] == {"text": dev, "writes": 1}
    assert hint["notDev"] == {"text": normal, "writes": 2}
    assert hint["drawnAgain"] == {"text": dev, "writes": 3}                              # the page draws the same words itself


@ui_only
def test_a_supporters_list_that_failed_is_asked_for_at_the_next_visit(checks: dict[str, Any]) -> None:
    """One failed ask stayed "unavailable" until the window closed, though the monitor only holds a failure for five minutes (#47)."""
    list_ = checks["supporters"]
    assert list_["failed"] == {"asks": 1, "unavailable": True, "named": False}
    assert list_["sameVisit"] == {"asks": 1, "unavailable": True, "named": False}        # a new state asks nothing
    assert list_["nextVisit"] == {"asks": 2, "unavailable": False, "named": True}
    assert list_["visitAfterThat"] == {"asks": 2, "unavailable": False, "named": True}   # one that worked is not asked for again
    assert list_["linkLost"] == {"asks": 3, "unavailable": True, "named": False}
    assert list_["afterLinkLost"] == {"asks": 4, "unavailable": False, "named": True}


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
    assert text == [ENGLISH_CATALOG["limit.remain.title"], "3 H 45 M"]               # the label in natural case (#98), the time a badge
    assert bar(remain["fresh"])[0]["style"] == "--r:100.0%" and bar(remain["fresh"])[1][1] == "5 H 0 M"
    assert bar(remain["week"])[0]["style"] == "--r:50.0%" and bar(remain["week"])[1][1] == "3 D 12 H"
    done, text = bar(remain["done"])
    assert done["style"] == "--r:0.0%" and text[1] == "NOW"                           # a finished window is an empty bar
    assert remain["none"] == ""                                                       # no reset time, nothing to draw
    card = remain["card"]
    assert card.index('class="nm-row2"') < card.index('class="nm-remain"') < card.index('class="nm-pace"')
    assert "<span>Resets <b>" in card and re.search(r"<span>In \d+ h \d+ m</span>", card)   # the other themes read the facts


# ── The Efficiency page, under its old name too ───────────────────────────────────

def test_the_efficiency_page_is_called_efficiency_wherever_a_user_reads_it() -> None:
    assert '{ key: "efficiency" }' in APP_CODE and 'tLine("heading.efficiency.title")' in APP_CODE
    english = json.loads((WEBUI / "i18n" / "en.json").read_text(encoding="utf-8"))
    assert english["heading.efficiency.title"] == english["shell.nav.efficiency"] == "Efficiency"         # #98: the catalog's
    assert '<a href="#efficiency" data-page="efficiency"><i>04</i><span data-i18n="shell.nav.efficiency">Efficiency</span></a>' in INDEX
    visible = strip_comments(APP + INDEX + "".join(THEME_CSS.values()))
    assert "nerds only" not in visible.lower() and "NERDS ONLY" not in visible
    assert 'PAGE_ALIASES = { nerds: "efficiency" }' in APP_CODE            # an old link or bookmark still opens it
    assert 'data-page="efficiency"' in THEME_CSS["gates"] and 'data-page="nerds"' not in THEME_CSS["gates"]


@ui_only
def test_an_old_nerds_link_still_opens_the_efficiency_page(checks: dict[str, Any]) -> None:
    assert checks["pageKeys"] == PAGES
    assert checks["alias"] == "efficiency" and checks["aliasNew"] == "efficiency"
    assert checks["aliasUnknown"] == "overview"


@ui_only
def test_the_dashboards_choices_come_from_the_page_and_are_saved_in_the_monitors_settings(checks: dict[str, Any]) -> None:
    """#54: the page reads the choices the server put in it, and saves each change through POST /api/settings."""
    loaded = {"costWindow": "month", "efficiencyWindow": "week", "folderMode": "launch", "calMetric": "cost", "matrixMetric": "cost"}
    assert checks["prefsAfterOldNameAlone"] == {"costWindow": "cycle", "efficiencyWindow": "cycle", "folderMode": "folder",
                                                "calMetric": "cost", "matrixMetric": "cost"}   # nerdsWindow is not carried over
    assert checks["loadedPrefs"] == loaded                          # an unknown value ("bogus") and an old name are skipped
    assert checks["loadedPrefsAfterJunk"] == loaded                 # nothing that is not a set of choices changes them
    assert checks["prefChoices"] == {name: list(values) for name, values in server.DASHBOARD_PREF_CHOICES.items()}
    assert checks["savedPrefsWithoutMeta"] is None
    # Only the choice that changed is sent: the server merges it into the ones it holds, so windows never undo each other's.
    assert checks["savedPrefsPost"] == [{"url": "/api/settings", "method": "POST", "token": True,
                                         "body": {"dashboard_prefs": {"costWindow": "month"}}}]
    assert checks["savedPrefsUnknown"] == [] and checks["segmentUnknown"] == []
    assert checks["segmentPost"] == [{"url": "/api/settings", "method": "POST", "token": True,
                                      "body": {"dashboard_prefs": {"folderMode": "folder"}}}]
    assert checks["segmentShown"]["folderMode"] == "folder"
    assert checks["savedPrefsFromMeta"] == {"costWindow": "week", "calMetric": "tokens", "folderMode": "nope"}
    assert checks["prefsFromMeta"] == {"costWindow": "week", "efficiencyWindow": "cycle", "folderMode": "folder",
                                       "calMetric": "tokens", "matrixMetric": "cost"}
    assert checks["savedPrefsBadMeta"] is None and checks["savedPrefsEmptyMeta"] is None


def page_scripts() -> dict[str, str]:
    """Every script the dashboard runs: app.js, charts.js, the formats (#91), the motion layer and each theme's own."""
    return {"app.js": APP_CODE, "charts.js": CHARTS, "formats.js": read("formats.js"), "motion.js": read("motion.js"),
            **{f"themes/{theme_id}.js": source for theme_id, source in THEME_JS.items()}}


PAGE_SCRIPTS = page_scripts()


def test_the_page_never_reads_the_browsers_language() -> None:
    """The monitor decides the language (#87): navigator.language can disagree with the setting and with Windows."""
    for name, source in PAGE_SCRIPTS.items():
        assert "navigator.language" not in strip_comments(source), name          # navigator.languages too
    assert re.search(r"var LANGUAGE_NAMES = \{ en: \"English\", fr: \"Français\", es: \"Español\", tr: \"Türkçe\" \};", APP_CODE)


@ui_only
def test_the_page_takes_its_text_from_the_catalog_and_marks_english(checks: dict[str, Any]) -> None:
    """#87: the page's own strings first, English behind them; English on a French page is marked lang="en", so it is
    cased by English rules. Values are escaped unless they are markup the page built."""
    i = checks["i18n"]
    assert (i["own"], i["ownLang"]) == ("Clé x", "fr")
    assert (i["english"], i["englishLang"]) == ("Session <b> & 1", "en")
    assert i["missing"] == "no.such.key" and i["unfilled"] == "Clé {name}"
    assert i["html"] == '<span lang="en">Session &lt;b&gt; &amp; &lt;i&gt;</span>'
    assert i["htmlMarkup"] == "Clé <code>claude</code>"
    assert i["htmlForged"] == "Clé [object Object]"                   # only markup() passes; data never does
    assert i["code"] == '<code class="nm-path" lang="en" translate="no">claude --resume &lt;id&gt;</code>'
    assert i["path"] == '<span class="nm-path" lang="en" translate="no">C:\\Users\\t &amp; co</span>'
    assert i["name"] == '<span translate="no">Opus &lt;4&gt;</span>'            # a name: cased by the page's language (#91)
    assert (i["pluralZero"], i["pluralTwo"], i["pluralMillion"]) == ("0 session", "2 sessions", "1000000 de sessions")
    assert i["pluralFallbackZero"] == "0 sessions"                         # English text follows English rules
    assert i["locale"]["format"] == "fr-CA" and i["known"] is True and i["names"] == dict(i18n.NAMES)
    assert checks["i18nDefaultCatalog"] == "Language"                       # the real en.json, as the app serves it
    assert checks["i18nEnglishHtml"] == "Language"                         # an English page wraps nothing in lang="en"
    assert checks["i18nNoCatalog"] == "settings.language.label"            # no catalog loaded: the key, easy to see


@ui_only
def test_the_page_and_the_app_choose_the_same_plural_forms(checks: dict[str, Any]) -> None:
    """The page has its own copy of the CLDR rules, so a WebView with other ICU data cannot disagree with the app."""
    assert len(checks["i18nPlurals"]) == 56
    for language, count, category in checks["i18nPlurals"]:
        assert i18n.plural_category(language, float(count)) == category, (language, count)


def split_top(text: str) -> list[str]:
    """``text`` split at its top-level commas: a comma inside brackets or a string literal does not count."""
    parts, depth, quote, current, i = [], 0, "", "", 0
    while i < len(text):
        ch = text[i]
        current += ch
        if quote:
            if ch == "\\" and i + 1 < len(text):
                current += text[i + 1]
                i += 1
            elif ch == quote:
                quote = ""
        elif ch in "\"'`":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(current[:-1])
            current = ""
        i += 1
    parts.append(current)
    return [part.strip() for part in parts]


def call_tail(code: str, start: int) -> str:
    """The rest of a call's argument list, from ``start`` up to its own ")"."""
    depth, quote, i = 0, "", start
    while i < len(code):
        ch = code[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = ""
        elif ch in "\"'`":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                return code[start:i]
            depth -= 1
        i += 1
    raise AssertionError(f"a call with no end at {start}")


def literal_names(text: str) -> set[str] | None:
    """The property names of an object literal written in the call (``{ a: 1, b }``); None for anything else."""
    text = text.strip()
    if not (text.startswith("{") and text.endswith("}")):
        return None
    names = set()
    for part in split_top(text[1:-1]):
        if part:
            m = re.match(r"([A-Za-z_$][\w$]*)\s*(?::|$)", part)
            if m is None:
                return None                                   # a quoted, computed or spread name
            names.add(m.group(1))
    return names


def i18n_calls(code: str) -> list[tuple[str, str, set[str] | None]]:
    """Every t, tHtml, tHtmlLine, tLang, tPlural, tLine and tUpper call with a literal key: (function, key, the placeholders
    it fills). The placeholders are None when the scan cannot read them: a key built from parts, values not written in the
    call as an object literal, or arguments the function does not take."""
    calls = []
    for m in re.finditer(r'(?<![\w.$])(t|tHtml|tHtmlLine|tLang|tPlural|tLine|tUpper)\("([^"]+)"', code):
        name, key = m.group(1), m.group(2)
        first, *rest = split_top(call_tail(code, m.end()))
        given: set[str] | None
        if first:                                             # "a." + part: not one key
            given = None
        elif name == "tLang":
            given = set() if not rest else None
        elif name == "tPlural":
            names = literal_names(rest[1]) if len(rest) == 2 else set() if len(rest) == 1 else None
            given = None if names is None else names | {"count"}
        else:
            given = set() if not rest else literal_names(rest[0]) if len(rest) == 1 else None
        calls.append((name, key, given))
    return calls


def test_the_key_scan_reads_every_shape_of_call() -> None:
    """The scan below, on the call shapes the dashboard tickets (#98 to #101) will add."""
    samples = [
        ('t("a.b")', ("t", "a.b", set())),
        ('t("a.b", { x: 1, y: f(2, 3) })', ("t", "a.b", {"x", "y"})),
        ('tHtml("k", { cmd: tCode(c), path: tPath(p), })', ("tHtml", "k", {"cmd", "path"})),
        ('tHtml("k", { n })', ("tHtml", "k", {"n"})),
        ('tPlural("p", n, { a: [1, 2], b: "x, y" })', ("tPlural", "p", {"count", "a", "b"})),
        ('tPlural("p", count)', ("tPlural", "p", {"count"})),
        ('tLang("k")', ("tLang", "k", set())),
        ('tLine("k", { status: status })', ("tLine", "k", {"status"})),
        ('tLine("k")', ("tLine", "k", set())),
        ('tUpper("k", { amount: fmtUsd(a) })', ("tUpper", "k", {"amount"})),
        ('tHtmlLine("k", { time: markup(b) })', ("tHtmlLine", "k", {"time"})),
        ('t("x.fn", pick(a, b))', ("t", "x.fn", None)),
        ('t("x.v", values)', ("t", "x.v", None)),
        ('t("x." + part)', ("t", "x.", None)),
        ('tHtml("q", { "quoted": 1 })', ("tHtml", "q", None)),
    ]
    for code, expected in samples:
        assert i18n_calls(code) == [expected], code


def test_every_key_the_page_and_the_shell_use_is_in_english_with_its_placeholders() -> None:
    """en.json is the source of truth (#87): each key app.js or index.html asks for is there, and a call names exactly
    the placeholders its text has. Keys are literal strings and values are object literals written in the call, so
    this scan (and a translator's tools) can read them."""
    english = json.loads((WEBUI / "i18n" / "en.json").read_text(encoding="utf-8"))
    code = strip_comments(APP_CODE)
    assert not re.findall(r'(?<![\w.$])(?<!function )(?:t|tHtml|tHtmlLine|tLang|tPlural|tLine|tUpper)\((?!")', code), "a key that is not a literal"
    calls = i18n_calls(code)
    shell = re.findall(r'\sdata-i18n(?:-[a-z-]+)?="([^"]+)"', INDEX)
    assert calls and shell
    for key in [key for _, key, _ in calls] + shell:
        assert key in english, key
    for name, key, given in calls:
        assert given is not None, (name, key, "values the scan cannot read")
        if name == "tLang":
            continue                                          # it asks only which language the text comes from
        forms = list(english[key].values()) if isinstance(english[key], dict) else [english[key]]
        wanted = set().union(*(re.findall(r"\{([A-Za-z_]\w*)\}", form) for form in forms))
        assert given == wanted, (key, given, wanted)


def test_the_page_formats_in_the_monitors_locale_only() -> None:
    """#87: no page script may take the browser's own locale. A later change formats with LOCALE.format; none may leave
    the locale out (or pass [] or undefined), which would follow the WebView's language instead."""
    for name, source in PAGE_SCRIPTS.items():
        code = strip_comments(source)
        assert not re.search(r"\.toLocale(?:Upper|Lower)?(?:Case|String|DateString|TimeString)?\(\s*(?:\)|\[\]|undefined|null)", code), name
        assert not re.search(r"\bIntl\.\w+\(\s*(?:\)|\[\]|undefined|null)", code), name


# ── Formats (#91) ─────────────────────────────────────────────────────────────────────────────────────────────────

FORMATS_JS = Path(__file__).resolve().parent / "webui_formats.js"
FORMATS_ENGLISH = Path(__file__).resolve().parent / "fixtures" / "webui_formats_english.json"


def english_formats() -> dict[str, Any]:
    return json.loads(FORMATS_ENGLISH.read_text(encoding="utf-8"))


def test_the_pages_english_formats_record_is_whole() -> None:
    """So the check below cannot pass on a thin record: every formatter step 2.0 of #91's plan froze has its cases, and
    the record holds the shapes Chris kept for English ($5.96, CA$, 2 D 3 H, X, Y and Z, 7:39 PM with an ASCII space)."""
    english = english_formats()
    assert len(english) == 43 and sum(len(cases) for cases in english.values() if isinstance(cases, dict)) == 3454
    assert all(english[name] for name in english), [name for name in english if not english[name]]
    assert english["fmtUsd"]["(5.96)"] == "$5.96" and english["fmtUsd"]["(-1234.5)"] == "-$1,235"
    assert english["fmtUsd"]["(999.995)"] == "$1000.00" and english["fmtUsd"]["(-999.995)"] == "-$1000.00"  # no group
    assert english["fmtMoney"]['(5.96, "CAD")'] == "CA$5.96" and english["fmtMoney"]['(5.96, "EURO")'] == "5.96 EURO"
    assert english["fmtRemain"]["(183600000)"] == "2 D 3 H"
    assert english["listWords"]['(["A","B","C"])'] == "A, B and C"
    assert english["clockText"][f"({19 * 3600000 + 39 * 60000 + 1792540800000})"] == "7:39 PM"
    assert english["fmtPct"]["(33.5, 1)"] == "33.5%" and english["fmtUsdTile"]["(1234.5)"] == "$1.2K"
    assert english["odometerHTML"]["(null, true)"] == ["-- tokens, all time", "0,000,000,000"]


# The entries whose words moved into the catalog in natural case (#98), held to the record through the capitals a theme
# shows them in: the health box's and the limit cards' forecast lines and the daily chart's meta, which Nostromo and Night
# City set in capitals by CSS (Gates through gates.js before). Markup is compared as a browser shows it: the tags as they are,
# the text between them in English's capitals with its entities read ("&middot;" is the "·" the catalog writes).
SHOWN_IN_CAPITALS = {"health", "weeklyForecast", "dailyChart"}


def in_capitals(value: Any) -> Any:
    """A recorded value as Nostromo shows it: the text outside tags in English's capitals, entities read."""
    if isinstance(value, list):
        return [in_capitals(item) for item in value]
    if not isinstance(value, str):
        return value
    return "".join(part if part.startswith("<") else casing.upper(part, "en") for part in re.split(r"(<[^>]*>)", unescape(value)))


@ui_only
def test_the_pages_english_formats_are_the_recorded_ones() -> None:
    """Every formatter of the page, run under node on the record's grid (tests/webui_formats.js, TZ=UTC, the page's clock
    still), writes exactly what it wrote before #91 changed the page: English must not change by a byte. The entries in
    SHOWN_IN_CAPITALS are compared through English's capitals, as Nostromo shows them (#98: the words are the catalog's,
    in natural case, and the record held their capitals)."""
    run = subprocess.run([node_binary(), str(FORMATS_JS), str(WEBUI)], capture_output=True, text=True, timeout=60,
                         check=False, encoding="utf-8", env={**os.environ, "TZ": "UTC"}, creationflags=NO_WINDOW)
    assert run.returncode == 0, run.stderr[-800:]
    found, english = json.loads(run.stdout), english_formats()
    assert list(found) == list(english)
    shown = (lambda name, value: in_capitals(value) if name in SHOWN_IN_CAPITALS else value)
    for name, cases in english.items():
        if isinstance(cases, dict):
            changed = [(key, cases[key], found[name].get(key)) for key in cases
                       if shown(name, found[name].get(key)) != shown(name, cases[key])]
            assert not changed and list(found[name]) == list(cases), (name, changed[:5])
        else:
            assert found[name] == cases, name


NB, NNB = "\u00a0", "\u202f"            # what CLDR writes before "%" and "$", and French's group separator
SPACE_BESIDE_THE_DOLLAR = re.compile(r"\s\$|\$\s")


def as_the_cldr_writes(versions: dict[str, Any], forms: dict[str, Any], found: Any) -> None:
    """found is the form listed for the CLDR version of the node that wrote it (process.versions.cldr), where CLDR's
    versions write a figure otherwise (CLDR 48 moved Turkish's compact "$" before the number). A version not listed
    fails with what its page wrote, to be checked against CLDR and listed."""
    cldr_version = versions["cldr"]
    assert cldr_version in forms, f"CLDR {cldr_version} is not listed ({', '.join(sorted(forms))}); it wrote {found!r}"
    assert found == forms[cldr_version], (f"CLDR {cldr_version}", found, forms[cldr_version])


def sign_last(text: str) -> str:
    """A money box's figure with a "$" that comes first (before or after its minus) moved to the end: the boxes count
    characters, so the side CLDR puts the sign on does not change what they measure (Turkish's compact figures are
    "99,9 Mn$" on CLDR 47 and "$99,9 Mn" on CLDR 48)."""
    return re.sub(r"^(-?)\$(-?)(.*)$", r"\1\2\3$", text)


@ui_only
def test_the_formats_keep_englishs_shapes_and_the_pseudo_locales_two_changes(checks: dict[str, Any]) -> None:
    """formats.js on its own: English as the page writes it today (a minus after the "$" in a compact figure, as the
    tiles had it; a code Intl refuses after the number); the pseudo-locale the same with U+202F groups and a U+00A0
    before "%"; any language without formats of its own as English."""
    en, pseudo = checks["formats"]["en"], checks["formats"]["pseudo"]
    assert (en["tag"], en["english"], en["signs"]) == ("en-US", True, [",", "."])
    assert en["number"] == ["1,234.5", "1234.5", "1,234.5", "1,234.50", "-0", "-0.0", "x", "1,234", "12,345"]
    assert en["percent"] == ["34%", "33.5%", "-34%", "34%"]
    assert en["parts"] == [["", "34", "%"], ["", "-34", "%"], ["", "34", "%"]]
    assert en["usd"] == ["$5.96", "-$5.96", "$1,235", "$1,234.50", "-$1,235", "$1.2K", "$-1.2K", "$5.96", "$1.2K"]
    assert en["money"] == ["$5.96", "€5.96", "5.96 EURO", "$1,234.50"]
    assert en["compact"] == ["1.5K", "1.5M", "-1.5K"]
    assert (pseudo["tag"], pseudo["english"], pseudo["signs"]) == ("en-US", True, [NNB, "."])
    assert pseudo["number"] == [f"1{NNB}234.5", "1234.5", f"1{NNB}234.5", f"1{NNB}234.50", "-0", "-0.0", "x",
                                f"1{NNB}234", f"12{NNB}345"]
    assert pseudo["percent"] == [f"34{NB}%", f"33.5{NB}%", f"-34{NB}%", "34%"]
    assert pseudo["usd"][2:5] == [f"$1{NNB}235", f"$1{NNB}234.50", f"-$1{NNB}235"] and pseudo["money"][3] == f"$1{NNB}234.50"
    assert {key: pseudo[key] for key in ("compact", "parts")} == {"compact": en["compact"], "parts": [
        ["", "34", f"{NB}%"], ["", "-34", f"{NB}%"], ["", "34", "%"]]}
    tags = checks["formatsTags"]
    assert tags == ["fr-FR", "fr-FR", "fr-CA", "tr-TR", "es-419", "en-US", "en-US", "en-US", "en-US", "en-US"]


@ui_only
def test_the_formats_follow_the_format_tag_in_french_spanish_and_turkish(checks: dict[str, Any]) -> None:
    """#91's cases, as Intl writes them for the format tag: fr 34 % (U+00A0), tr %34, es-ES 1234 but 12.345 (CLDR's
    minimum grouping), fr-FR's U+202F and fr-CA's U+00A0 groups, US dollars as the plain "$" (5,96 $, never $US),
    fr-CH's currency decimal, Turkish's B for a thousand, and tight: no space before the "$" or the "%". Where CLDR's
    versions write a figure otherwise, each version's form is listed (as_the_cldr_writes)."""
    fr, ca, ch = checks["formats"]["frFR"], checks["formats"]["frCA"], checks["formats"]["frCH"]
    es, mx, tr = checks["formats"]["esES"], checks["formats"]["esMX"], checks["formats"]["trTR"]
    assert [f["english"] for f in (fr, ca, ch, es, mx, tr)] == [False] * 6
    assert (fr["signs"], ca["signs"], es["signs"], mx["signs"], tr["signs"]) == (
        [NNB, ","], [NB, ","], [".", ","], [",", "."], [".", ","])
    assert fr["number"][:4] == [f"1{NNB}234,5", "1234,5", f"1{NNB}234,5", f"1{NNB}234,50"] and fr["number"][5] == "-0,0"
    assert ca["number"][0] == f"1{NB}234,5" and tr["number"][0] == "1.234,5"
    assert es["number"][0] == "1234,5" and es["number"][-2:] == ["1234", "12.345"] and mx["number"][-2:] == ["1,234", "12,345"]
    assert fr["percent"] == [f"34{NB}%", f"33,5{NB}%", f"-34{NB}%", "34%"] and es["percent"][0] == f"34{NB}%"
    assert tr["percent"] == ["%34", "%33,5", "-%34", "%34"]
    assert fr["parts"] == [["", "34", f"{NB}%"], ["-", "34", f"{NB}%"], ["", "34", "%"]]
    assert tr["parts"] == [["%", "34", ""], ["-%", "34", ""], ["%", "34", ""]]
    assert fr["usd"] == [f"5,96{NB}$", f"-5,96{NB}$", f"1{NNB}235{NB}$", f"1{NNB}234,50{NB}$", f"-1{NNB}235{NB}$",
                         f"1,2{NB}k{NB}$", f"-1,2{NB}k{NB}$", "5,96$", f"1,2{NB}k$"]
    assert mx["usd"][:5] == ["$5.96", "-$5.96", "$1,235", "$1,234.50", "-$1,235"] and mx["usd"][7] == "$5.96"
    assert tr["usd"][:3] == ["$5,96", "-$5,96", "$1.235"] and tr["usd"][7] == "$5,96"
    # Turkish's compact dollar, tight: CLDR 47 writes its sign last ("1,2 B $"), CLDR 48 first ("$1,2 B", no space).
    as_the_cldr_writes(checks["versions"], {"47.0": f"1,2{NB}B$", "48.0": f"$1,2{NB}B"}, tr["usd"][8])
    assert es["usd"][2] == f"1235{NB}$" and ch["usd"][0] == f"5.96{NB}$" and ch["number"][0] == f"1{NNB}234,5"
    assert fr["money"] == [f"5,96{NB}$", f"5,96{NB}EUR", f"5,96{NB}EURO", f"1{NNB}234,50{NB}$"]
    assert ch["money"][2] == f"5.96{NB}EURO" and mx["money"][1:3] == [f"EUR{NB}5.96", f"EURO{NB}5.96"]
    assert tr["compact"][0] == f"1,5{NB}B" and fr["compact"] == [f"1,5{NB}k", f"1,5{NB}M", f"-1,5{NB}k"]
    assert es["compact"][0] == f"1,5{NB}mil"


@ui_only
def test_the_page_takes_its_formats_from_the_monitor_locale_meta(checks: dict[str, Any]) -> None:
    """MonitorFormats.active() reads the meta the server fills in, once; with no meta, one it cannot read, one with no
    language, or no page at all, the page formats in English (as app.js's LOCALE falls back)."""
    assert checks["formatsActive"] == [["tr-TR", "%34", True]] + [["en-US", "34%", True]] * 5


def locale_of(language: str, tag: str) -> i18n.Locale:
    return i18n.Locale(setting="system", language=language, system="en", format=tag, clock=24, offered=("en",))


@ui_only
def test_the_pages_figures_follow_the_format_tag(checks: dict[str, Any]) -> None:
    """#91 step 2.2: the page's numbers, percents, money and compact figures in the locale: fr 33,5 % (U+00A0), tr %34,
    fr 5,96 $ (the plain dollar), es-ES 1234 (CLDR's minimum grouping), tr 1,5 B (a thousand); the odometer's separators
    are the locale's group sign; the figures the page writes into sentences, cards and charts follow it too."""
    fr, ca, es, mx, tr = (checks["pageFormats"][name] for name in ("frFR", "frCA", "esES", "esMX", "trTR"))
    assert fr["percent"] == [f"33,5{NB}%", f"34{NB}%", f"33,5{NB}%", f"34,5{NB}%", f"0{NB}%"]
    assert tr["percent"] == ["%33,5", "%34", "%33,5", "%34,5", "%0"]
    assert fr["usd"] == [f"5,96{NB}$", f"-5,96{NB}$", f"1{NNB}235{NB}$", f"1{NNB}234,50{NB}$", f"1,2{NB}k{NB}$",
                         f"100{NB}$", f"17,50{NB}$"]                 # outside the money boxes, CLDR's spaces stay
    assert mx["usd"][:4] == ["$5.96", "-$5.96", "$1,235", "$1,234.50"] and tr["usd"][:3] == ["$5,96", "-$5,96", "$1.235"]
    assert es["count"][3:5] == ["1234", "12.345"] and es["usd"][2] == f"1235{NB}$" and mx["count"][3] == "1,234"
    assert tr["count"][:2] == [f"1,5{NB}B", f"1,5{NB}Mn"] and fr["count"][:3] == [f"1,5{NB}k", f"1,5{NB}M", "999"]
    assert fr["count"][3:] == [f"1{NNB}234", f"12{NNB}345", "6,0×", f"1{NNB}235", "12", "-3,3", f"1{NNB}234 requests"]
    assert ca["count"][3] == f"1{NB}234"
    assert fr["money"] == [f"5,96{NB}$", f"5,96{NB}EUR", f"5,96{NB}EURO"] and mx["money"][1] == f"EUR{NB}5.96"
    assert fr["prose"] == ["You are using this window 12,3 points faster than an even pace.",
                           f"<b>Ends ~33{NB}%</b> · budget 14{NB}%/day", f"33,5{NB}%", "1,2"]     # #98: the catalog's words
    assert tr["prose"][1] == "<b>Ends ~%33</b> · budget %14/day"
    assert tr["ticks"] == ["%0", "%25", "%50", "%75", "%100", "%140"] and fr["ticks"][4] == f"100{NB}%"
    assert fr["odometer"] == [[f"1{NNB}234{NNB}567 tokens, all time", f"1{NNB}234{NNB}567", 2],
                              ["-- tokens, all time", f"0{NNB}000{NNB}000{NNB}000", 3]]
    assert ca["odometer"][0] == [f"1{NB}234{NB}567 tokens, all time", f"1{NB}234{NB}567", 2]
    assert es["odometer"][1] == ["-- tokens, all time", "0.000.000.000", 3]


@ui_only
def test_a_count_in_the_billions_keeps_its_own_digits_in_every_language(checks: dict[str, Any]) -> None:
    """#91's review: Spanish has no unit for 10^9 to 10^10 and writes those counts in millions, so fmtCount's two decimals
    of a billion would read "2250,00 M" for 2,254,000,000 tokens; the page writes the unrounded count in millions
    instead ("2254 M"). From 10^10 Spanish has its unit ("12,50 mil M"); French, Turkish and English are as they were."""
    billions = checks["review"]["billions"]
    assert billions["es-ES"] == [f"2254{NB}M", f"9995{NB}M", f"1500{NB}M", f"12,50{NB}mil{NB}M", f"-2254{NB}M"]
    assert billions["es-MX"] == billions["es-US"] == [f"2254{NB}M", f"9995{NB}M", f"1500{NB}M", f"12.50{NB}mil{NB}M",
                                                      f"-2254{NB}M"]
    assert billions["fr-FR"] == [f"2,25{NB}Md", f"9,99{NB}Md", f"1,50{NB}Md", f"12,50{NB}Md", f"-2,25{NB}Md"]
    assert billions["tr-TR"][:2] == [f"2,25{NB}Mr", f"9,99{NB}Mr"] and billions["fr-CA"][0] == f"2,25{NB}G"
    assert billions["en-US"] == ["2.25B", "9.99B", "1.50B", "12.50B", "-2.25B"]


@ui_only
def test_money_in_another_currency_is_written_with_its_code(checks: dict[str, Any]) -> None:
    """#91's review: in French, Spanish and Turkish a currency other than US dollars is written with its code, as the
    widgets' and the alerts' formats.py writes it, never with a local symbol: fr-CA's own symbol for CAD and es-MX's for
    MXN are the plain "$" the page's US-dollar figures use. English keeps Intl's en-US symbols (CA$5.96)."""
    money = checks["review"]["money"]
    assert money["fr-CA"] == [f"5,96{NB}CAD", f"5,96{NB}MXN", f"-1{NB}234,50{NB}EUR", f"5,96{NB}$"]
    assert money["es-MX"] == [f"CAD{NB}5.96", f"MXN{NB}5.96", f"-EUR{NB}1,234.50", "$5.96"]
    assert money["fr-FR"][2] == f"-1{NNB}234,50{NB}EUR" and money["es-ES"][0] == f"5,96{NB}CAD"
    assert money["tr-TR"][:2] == [f"CAD{NB}5,96", f"MXN{NB}5,96"]
    assert all(row[0] != row[3] and row[1] != row[3] for row in money.values())       # never the US dollars' text
    assert money["en-US"] == ["CA$5.96", "MX$5.96", "-€1,234.50", "$5.96"]


@ui_only
def test_the_alert_levels_percents_follow_the_format_tag(checks: dict[str, Any]) -> None:
    """#91's review: Settings' alert levels and the error for a level set twice write their percent through the formats
    (Turkish %80, French 80 %); the remove buttons' names keep the plain number until their words' text ticket."""
    levels = checks["review"]["levels"]
    assert levels["en-US"] == {"chips": ["50%", "80%"], "error": "80% is already a level.",
                               "labels": ["Remove the 50 percent level", "Remove the 80 percent level"]}
    assert (levels["tr-TR"]["chips"], levels["tr-TR"]["error"]) == (["%50", "%80"], "%80 is already a level.")
    assert (levels["fr-FR"]["chips"], levels["fr-FR"]["error"]) == ([f"50{NB}%", f"80{NB}%"], f"80{NB}% is already a level.")
    assert levels["es-ES"]["chips"] == [f"50{NB}%", f"80{NB}%"] and levels["es-MX"]["chips"] == ["50%", "80%"]
    assert all(row["labels"] == levels["en-US"]["labels"] for row in levels.values())


@ui_only
def test_a_pending_money_note_takes_the_locales_shape(checks: dict[str, Any]) -> None:
    """#91's review: the daily chart's "TOP $00.00" and the cost toolbar's "$000.00", shown until their figure comes, are
    the locale's money with zeros for digits, so the note keeps its shape when the figure lands (English as it was)."""
    pending = checks["review"]["pending"]
    note = "the last 7 days, {} api-equivalent at list prices"
    assert pending["en-US"] == ["Top $00.00", note.format("$000.00"), note.format("$123.45")]   # overview.daily.top (#98)
    assert pending["fr-FR"] == [f"Top 00,00{NB}$", note.format(f"000,00{NB}$"), note.format(f"123,45{NB}$")]
    assert pending["tr-TR"] == ["Top $00,00", note.format("$000,00"), note.format("$123,45")]
    assert pending["es-MX"] == pending["en-US"]


@ui_only
def test_no_month_label_is_cut_and_englishs_column_stays(checks: dict[str, Any]) -> None:
    """#91's review: the month-by-month chart's label column grows to its widest label (fr-CA JUILL. 26), measured here
    with charts.js's own estimate, which is wider than the themes' fonts; English's column stays 64 px (its bars start at
    74, the column and the gap), as before."""
    months = checks["review"]["months"]
    for tag, row in months.items():
        assert row["labels"] == row["full"] and len(row["full"]) == 12, (tag, row["labels"])
        assert not any(ELLIPSIS in label for label in row["labels"]), tag
    assert months["en-US"]["bars"] == months["en-US"]["plain"] == "74"
    assert "JUILL. 26" in months["fr-CA"]["full"] and float(months["fr-CA"]["bars"]) > float(months["fr-CA"]["plain"])
    assert months["tr-TR"]["bars"] == months["es-ES"]["bars"] == "74"


@ui_only
def test_the_money_boxes_take_the_dollar_without_its_space(checks: dict[str, Any]) -> None:
    """Chris's decision on #91 (Q1 (a)): the cost tiles and the big cost figure are sized for English's six characters,
    so French and Spanish drop the space before "$" there, as usage_monitor/formats.py's money does with tight=True for
    the widgets' boxes; the sign keeps its side (es-MX $5.96, tr $5,96), and a compact figure keeps the space before its
    unit. Everywhere else the space stays. A compact figure's side is CLDR's, which CLDR 48 moved for Turkish ($1,2 B):
    the tile is the page's own compact figure without the space beside the "$", on either side."""
    for name, language, tag in (("frFR", "fr", "fr-FR"), ("frCA", "fr", "fr-CA"), ("esES", "es", "es-ES"),
                                ("esMX", "es", "es-MX"), ("trTR", "tr", "tr-TR")):
        page, locale = checks["pageFormats"][name], locale_of(language, tag)
        assert page["tile"][0] == page["hero"][0] == formats.money(5.96, "USD", locale, tight=True), name
        assert page["tile"][1] == formats.money(-5.96, "USD", locale, tight=True), name
        assert page["usd"][0] == formats.money(5.96, "USD", locale), name
        assert not [text for text in page["tile"] + page["hero"] if SPACE_BESIDE_THE_DOLLAR.search(text)], name
    fr, tr, mx = checks["pageFormats"]["frFR"], checks["pageFormats"]["trTR"], checks["pageFormats"]["esMX"]
    assert fr["tile"] == ["5,96$", "-5,96$", "123$", f"1,2{NB}k$", f"1,2{NB}M$"]
    assert fr["hero"] == ["5,96$", f"1{NNB}235$", f"12,3{NB}k$", f"1,2{NB}M$"]
    # Turkish's compact tiles: the digits and the unit with its space, one "$" at an end, on the side fmtUsdShort's
    # compact figure has it (1,2 B $ and 1,2 B$ on CLDR 47; $1,2 B on CLDR 48).
    assert [text.replace("$", "", 1) for text in tr["tile"][3:]] == [f"1,2{NB}B", f"1,2{NB}Mn"]
    assert all(text.count("$") == 1 and "$" in (text[0], text[-1]) for text in tr["tile"][3:]), tr["tile"]
    assert tr["tile"][3] == re.sub(r"\s*\$\s*", "$", tr["usd"][4]) and mx["hero"][:2] == ["$5.96", "$1,235"]
    assert tr["tile"][4].startswith("$") == tr["tile"][3].startswith("$"), tr["tile"]    # both on fmtUsdShort's side


@ui_only
def test_the_pseudo_pages_figures_are_englishs_with_two_changes(checks: dict[str, Any]) -> None:
    """The pseudo-locale's page writes English's figures with U+202F groups and a U+00A0 before "%", and nothing else
    changes: a tile, which has no group and no space, is English's; English is English."""
    pseudo, english = checks["pageFormats"]["pseudo"], checks["pageFormats"]["en"]
    assert pseudo["percent"] == [f"33.5{NB}%", f"34{NB}%", f"33.5{NB}%", f"34.5{NB}%", f"0{NB}%"]
    assert english["percent"] == ["33.5%", "34%", "33.5%", "34.5%", "0%"]
    assert pseudo["count"][3:5] == [f"1{NNB}234", f"12{NNB}345"] and english["count"][3:5] == ["1,234", "12,345"]
    assert pseudo["tile"] == english["tile"] == ["$5.96", "-$5.96", "$123", "$1.2K", "$1.2M"]
    assert pseudo["odometer"][1] == ["-- tokens, all time", f"0{NNB}000{NNB}000{NNB}000", 3]
    assert english["odometer"] == [["1,234,567 tokens, all time", "1,234,567", 2], ["-- tokens, all time", "0,000,000,000", 3]]
    folded = json.dumps(pseudo, ensure_ascii=False).replace(NNB, ",").replace(NB + "%", "%")
    assert json.loads(folded) == english


@ui_only
def test_the_budgets_measure_the_money_boxes_with_what_the_page_writes(checks: dict[str, Any]) -> None:
    """tests/budgets.py's PAGE_MONEY is what the cost tiles and the big cost figure write (#91): each figure, tight as
    those boxes take it, is what the page writes there for one of its language's format tags, at an amount where
    English's own figure has six characters at most (French's and Spanish's exactly; Turkish's with its "$" on either
    side, sign_last, since CLDR 48 puts Turkish's compact "$" first and the boxes count characters); nothing the page
    writes there in any of those tags is wider, so budgets.over() measures the widest (test_budgets.py's FOR_91 lists
    what runs past); and none of it has a space beside the "$"."""
    import budgets

    money = checks["pageMoney"]
    assert money["english"] and max(len(text) for text in money["english"]) <= 6
    assert sorted(money["tags"]) == CLDR_TAGS
    chars = budgets.Measure("chars")
    assert set(budgets.PAGE_MONEY) == {"fr", "es", "tr"}
    for language, figures in budgets.PAGE_MONEY.items():
        written = {text for tag, texts in money["tags"].items() if i18n.primary(tag) == language for text in texts}
        assert not [text for text in written if SPACE_BESIDE_THE_DOLLAR.search(text)], language
        tight = {budgets.tight(figure) for figure in figures}
        side = sign_last if language == "tr" else str
        missing = {side(text) for text in tight} - {side(text) for text in written}
        assert not missing, (language, missing)
        assert max(chars.width(text) for text in tight) == max(chars.width(text) for text in written), language


# ── Dates, times, durations, lists and case on the page (#91, steps 2.3 to 2.7) ──────────────────────────────────

def folded_spaces(texts: Any) -> Any:
    """A 12-hour clock with its U+202F as U+0020: Intl's DateTimeFormat writes the one for the other (node and the
    WebView alike; V2 lists it), so the clock assertions hold either way."""
    return json.loads(json.dumps(texts).replace("\\u202f", " "))


@ui_only
def test_the_pages_dates_and_times_follow_the_format_tag_and_the_clock(checks: dict[str, Any]) -> None:
    """#91 step 2.3: the page's clocks, dates, weekdays and months in the locale, on the clock the monitor follows
    (Windows' 12- or 24-hour setting), and in the page's capitals cased by the language's rule (Turkish NİS, EKİ): fr-CA
    16 H 12, es-ES SÁB, 16:12, tr 12-hour ÖS 4:12. The calendar's names follow the page; charts.js with no formats layer
    keeps English's. English and the pseudo-locale keep English's shapes (the record holds them byte for byte)."""
    d = checks["pageDates"]
    en, pseudo, fr, fr12, ca, es, mx12, tr, tr12 = (d[name] for name in (
        "en", "pseudo", "frFR", "frFR12", "frCA", "esES", "esMX12", "trTR", "trTR12"))
    assert tr["constants"]["MONTHS"] == ["OCA", "ŞUB", "MAR", "N\u0130S", "MAY", "HAZ", "TEM", "AĞU", "EYL", "EK\u0130", "KAS", "ARA"]
    assert tr["constants"]["WEEKDAYS_MON"] == ["PZT", "SAL", "ÇAR", "PER", "CUM", "CMT", "PAZ"]
    assert tr["constants"]["DAYS"][:2] == ["PAZ", "PZT"] and fr["constants"]["WEEKDAYS_MON"][0] == "LUN."
    assert es["constants"]["MONTHS"][8] == "SEPT" and mx12["constants"]["MONTHS"][8] == "SEP"
    assert fr["clock"] == ["16:12", "09:05", "SAM. 16:12"] and ca["clock"] == ["16 H 12", "09 H 05", "SAM. 16 H 12"]
    assert es["clock"] == ["16:12", "9:05", "SÁB, 16:12"] and tr["clock"] == ["16:12", "09:05", "CMT 16:12"]
    assert folded_spaces([fr12["clock"], mx12["clock"], tr12["clock"]]) == [
        ["4:12 PM", "9:05 AM", "SAM. 4:12 PM"], ["4:12 P.M.", "9:05 A.M.", "SÁB 4:12 P.M."], ["ÖS 4:12", "ÖÖ 9:05", "CMT ÖS 4:12"]]
    assert (fr["stamp"], fr["day"], fr["date"], fr["hour"]) == (
        "24 OCT., 16:12", ["SAM. 24 OCT.", "SAM. 24 OCT. 2026"], "24 OCT. 2026", ["16 H", "09 H"])
    assert (tr["stamp"], tr["date"], tr["range"], tr["labels"]) == (
        "24 EK\u0130 16:12", "24 EK\u0130 2026", "1 N\u0130S - 24 EK\u0130", ["9 N\u0130S", "EK\u0130 26", "OCA 27"])
    assert folded_spaces(tr12["hourRange"]) == "ÖS 4 to ÖS 5" and fr["hourRange"] == "16 H to 17 H"
    assert tr["calendar"] == ["OCA 26", "ŞUB", "MAR", "N\u0130S", "PZT", "ÇAR", "CUM"]
    assert fr["calendar"] == ["JANV. 26", "FÉVR.", "MARS", "AVR.", "LUN.", "MER.", "VEN."]
    assert en["calendar"] == ["JAN 26", "FEB", "MAR", "APR", "MON", "WED", "FRI"]
    assert checks["chartsAlone"] == ["DEC", "JAN 27", "FEB", "MON", "WED", "FRI", "LESS", "MORE"]
    assert en["constants"] == {"DAYS": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"],
                               "WEEKDAYS_MON": ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"],
                               "MONTHS": ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]}
    assert (en["clock"], en["stamp"], en["day"], en["hourRange"], en["labels"]) == (
        ["4:12 PM", "9:05 AM", "SAT 4:12 PM"], "OCT 24 4:12 PM", ["SAT, OCT 24", "SAT, OCT 24, 2026"], "4 PM to 5 PM",
        ["APR 9", "OCT 26", "JAN 27"])
    assert {key: value for key, value in pseudo.items() if key != "plural"} == {
        key: value for key, value in en.items() if key != "plural"}
    assert "\u0307" not in json.dumps([tr, tr12], ensure_ascii=False)


@ui_only
def test_the_pages_durations_are_the_widgets_countdowns_in_cldrs_narrow_units(checks: dict[str, Any]) -> None:
    """#91 step 2.4 (D14): fmtRemain and the ages in French, Spanish and Turkish are CLDR's narrow units, in capitals on a
    badge and as written inside a sentence, with the widgets' parts (no zero tail): the page's countdown is
    render.model.format_countdown's in every tag. Turkish 3S 27D is 3 hours 27 minutes and one hour is 1 SA. English keeps
    2 D 3 H, 2 D 0 H and 3 M; NOW stays English's word (its text ticket's)."""
    d = checks["pageDates"]
    assert d["trTR"]["remain"] == ["2G 3S", "2G", "3S 27D", "1 SA", "27D", "NOW"]
    assert d["frFR"]["remain"] == ["2J 3H", "2J", "3H 27MIN", "1H", "27MIN", "NOW"] and d["frCA"]["remain"][2] == "3H 27M"
    assert d["en"]["remain"] == d["pseudo"]["remain"] == ["2 D 3 H", "2 D 0 H", "3 H 27 M", "1 H 0 M", "27 M", "NOW"]
    for name, language, tag in (("frFR", "fr", "fr-FR"), ("frCA", "fr", "fr-CA"), ("esES", "es", "es-ES"),
                                ("esMX12", "es", "es-MX"), ("trTR", "tr", "tr-TR")):
        locale = locale_of(language, tag)
        assert d[name]["remain"][:5] == [widget_model.format_countdown(s, locale) for s in (183600, 172800, 12420, 3600, 1620)], name
    assert d["trTR"]["age"] == [["0SN", "0sn"], ["12SN", "12sn"], ["3D", "3d"], ["1 SA", "1 sa"], ["3S", "3s"], ["2G", "2g"]]
    assert d["frFR"]["age"][2] == ["3MIN", "3min"] and d["esES"]["age"][5] == ["2D", "2d"]
    assert d["en"]["age"] == [["0 S", "0 s"], ["12 S", "12 s"], ["3 M", "3 m"], ["1 H", "1 h"], ["3 H", "3 h"], ["2 D", "2 d"]]
    assert checks["formatsCase"]["englishDuration"].startswith("MonitorFormats.duration serves French, Spanish and Turkish")


@ui_only
def test_lists_keep_englishs_join_and_plurals_their_rules_with_the_locales_number(checks: dict[str, Any]) -> None:
    """#91 step 2.5 (D12, D13): English joins "X, Y and Z" (no Oxford comma, as the page always did); French, Spanish and
    Turkish join as Intl.ListFormat does. plural() keeps English's s rule for its English words, and its number is the
    locale's."""
    d = checks["pageDates"]
    assert d["en"]["list"] == d["pseudo"]["list"] == ["", "A", "A and B", "A, B and C"]
    assert (d["frFR"]["list"][3], d["esES"]["list"][3], d["trTR"]["list"][3]) == ("A, B et C", "A, B y C", "A, B ve C")
    assert d["frFR"]["list"][:3] == ["", "A", "A et B"]
    assert d["en"]["plural"] == ["1 session", "1,234 requests"] and d["esES"]["plural"] == ["1 session", "1234 requests"]
    assert d["frFR"]["plural"][1] == "1\u202f234 requests" and d["trTR"]["plural"][1] == "1.234 requests"


@ui_only
def test_names_in_capitals_follow_the_pages_language_and_english_words_englishs(checks: dict[str, Any]) -> None:
    """#91 step 2.7 (D7): a name (a model, a surface) in capitals is cased by the page's language, so Haiku is HAİKU on a
    Turkish page (Chris's decision); English words in capitals keep English's rule (SESSIONS, never SESSİONS) until their
    text ticket gives them their catalog's language. formats.js's upper and lower take a language or a tag and never
    leave a combining dot; tName is translate="no" and no lang of its own, so CSS capitals follow the page."""
    d = checks["pageDates"]
    assert d["trTR"]["named"] == ["HA\u0130KU 4.5", "CLAUDE \u0130N CHROME"]
    assert d["en"]["named"] == d["frFR"]["named"] == ["HAIKU 4.5", "CLAUDE IN CHROME"]
    english = ["2 AGENTS ONLY", "2 SESSIONS", "CHECK FAILED: SIGNING IS MISSING"]
    assert d["trTR"]["english"] == d["en"]["english"] == d["frFR"]["english"] == english
    case = checks["formatsCase"]
    assert case["tr"] == ["\u0130STANBUL ILIK", "istanbul \u0131l\u0131k", "i", "I", "i"]
    assert case["en"] == ["I", "\u0130", "\u0130", "\u0131", "i\u0307"]               # English is Unicode's own rule, as str.lower()
    assert case["hours"] == [24, 12, 12, 24, 12]                        # English and the pseudo-locale: always 12-hour
    assert checks["i18n"]["name"] == '<span translate="no">Opus &lt;4&gt;</span>'


# The bare case calls app.js may make: ids, never words (D8 of #91's plan); some reach the page as id-derived text (an
# export format, an accent's letters, an unknown currency code). Words go through the formats:
# FMT.upper(text) for a name (the page's language) and FMT.upper(text, "en") for English words (English's rule, until
# the text tickets #98 to #101 give each its catalog's language).
APP_BARE_CASE_CALLS = {
    'String(currency || "USD").trim().toUpperCase()': "fmtMoney: the currency's ISO code",
    'String(limit.severity || "").toLowerCase()': "severityOf: a severity id",
    "name.toLowerCase() !== accent.toLowerCase()": "accentTitled: a colour's name against its id",
    "var label = fmt.toUpperCase();": "exportData: the export format's id (CSV, JSON)",
    "accent.slice(0, 3).toUpperCase()": "accentShort: an accent id's first letters (a name's: Night City's)",
}
CASE_CALL = re.compile(r"\.to(?:Locale)?(?:Upper|Lower)Case\b")


def test_app_js_cases_text_through_the_formats_and_ids_alone_with_the_bare_calls() -> None:
    """D8 of #91's plan: every bare upper- or lower-case call in app.js is one of the ids listed, each once; text people
    read is cased through the formats (FMT.upper, FMT.lower), by the page's language for a name and by English's rule
    for English words."""
    code = strip_comments(APP_CODE)
    calls = [m.start() for m in CASE_CALL.finditer(code)]
    allowed: set[int] = set()
    for snippet in APP_BARE_CASE_CALLS:
        assert code.count(snippet) == 1, snippet
        at = code.index(snippet)
        allowed |= {m.start() + at for m in CASE_CALL.finditer(snippet)}
    stray = [code[max(0, i - 60):i + 14].strip().splitlines()[-1] for i in calls if i not in allowed]
    assert not stray, stray
    assert len(calls) == len(allowed) == 6


# ── V2 of #91's plan (D1): the page's formats are the widgets', for every format tag ──────────────────────────────

CLDR_JS = Path(__file__).resolve().parent / "webui_cldr.js"
# The grid tests/webui_cldr.js writes with formats.js and python_formats() with formats.py. Times are UTC wall times;
# day 0 is Monday 2026-10-19.
CLDR_GRID: dict[str, Any] = {
    "numbers": ["1234.5", "12345", "-1234567.25", "0.5", "1234"], "ungrouped": ["1234.5"],
    "percents": ["34", "-34", "33.5", "1234"], "tight": ["34", "100"],
    "amounts": [5.96, -5.96, 1234.56], "code": "EURO", "codes": ["EUR", "CAD"],
    "times": [[0, 5], [9, 39], [12, 0], [16, 12], [23, 59]],
    "dayTimes": [[day, 16, 12] for day in range(7)] + [[5, 9, 5]],
    "durations": [[[2, "d"], [3, "h"]], [[1, "d"]], [[21, "d"], [1, "h"]], [[1, "h"]], [[3, "h"], [27, "m"]],
                  [[2, "h"], [1, "m"]], [[27, "m"]], [[1, "m"]], [[12, "s"]], [[1, "s"]], [[0, "s"]]],
    "cases": ["Haiku", "istanbul \u0131l\u0131k", "\u0130STANBUL ILIK", "HAFTALIK", "Claude Instant", "N\u0130S EK\u0130", "ÇĞÖŞÜ çğöşü"],
}
CLOCKS_12 = ("clock12", "dayClock12")


def python_formats(tag: str) -> dict[str, list[str]]:
    """CLDR_GRID through usage_monitor/formats.py and casing.py, field by field as tests/webui_cldr.js names them."""
    from datetime import datetime

    from usage_monitor import casing

    language, g = i18n.primary(tag), CLDR_GRID
    row: dict[str, list[str]] = {}
    for clock in (24, 12):
        locale = i18n.Locale(setting="system", language=language, system="en", format=tag, clock=clock, offered=("en",))
        if clock == 24:
            row["number"] = [formats.number(d, locale) for d in g["numbers"]] + [
                formats.number(d, locale, group=False) for d in g["ungrouped"]]
            row["percent"] = [formats.percent(d, locale) for d in g["percents"]] + [
                formats.percent(d, locale, tight=True) for d in g["tight"]]
            row["usd"] = row["moneyUsd"] = [formats.money(a, "USD", locale) for a in g["amounts"]]
            row["usdTight"] = [formats.money(a, "USD", locale, tight=True) for a in g["amounts"]]
            row["moneyCode"] = [formats.money(a, g["code"], locale) for a in g["amounts"]]
            row["moneyIso"] = [formats.money(a, code, locale) for code in g["codes"] for a in g["amounts"]]
            row["weekday"] = [formats.weekday(i, locale) for i in range(7)]
            row["month"] = [formats.month(i, locale) for i in range(12)]
            row["duration"] = row["durationUnits"] = [
                formats.duration([(count, unit) for count, unit in parts], locale) for parts in g["durations"]]
            row["upper"] = [casing.upper(text, language) for text in g["cases"]]
            row["lower"] = [casing.lower(text, language) for text in g["cases"]]
        row[f"clock{clock}"] = [formats.clock(datetime(2026, 10, 19, h, m), locale) for h, m in g["times"]]
        row[f"dayClock{clock}"] = [formats.day_clock(datetime(2026, 10, 19 + day, h, m), locale) for day, h, m in g["dayTimes"]]
    return row


# The spaces a 12-hour clock writes inside or beside its day period, and the day periods of fr, es and tr's 12-hour
# clocks as cldr.py's am_pm and node's Intl write them (AM, a. m. with its space inside, a.m., ÖÖ), with a space on
# either side: Turkish writes the period before the time.
_CLOCK_SPACE = "[ \u00a0\u202f]"
_DAY_PERIOD = re.compile(rf"{_CLOCK_SPACE}?(?<!\w)(?:AM|PM|ÖÖ|ÖS|[ap]\.{_CLOCK_SPACE}?m\.)(?!\w){_CLOCK_SPACE}?")


def clock_spaces_folded(text: str) -> str:
    """A 12-hour clock with each space inside its day period or beside it as U+0020. Which of U+0020, U+00A0 and U+202F
    sits there is the one difference V2 lets pass in a 12-hour clock, on both sides (KNOWN_CLOCK_SPACES): it is safe,
    since the widgets draw U+202F as U+00A0 (engine.drawn, #92), as wide as a space, so the clock reads the same. A
    space missing or added, another period, another digit, or another space anywhere else still differs."""
    return _DAY_PERIOD.sub(lambda m: re.sub(_CLOCK_SPACE, " ", m.group(0)), text)


SEEN = "node v24.13.0, ICU 77.1, CLDR 47.0"
SEEN_48 = "node v20.20.2, ICU 78.2, CLDR 48.0"            # CI's run 45563782300 (windows-latest)
TABLE = "cldr.py: CLDR 47, Babel 2.18.0"
# The two spaces clock_spaces_folded lets differ in every 12-hour clock, each with what the page writes on each CLDR
# version it was seen on (its example's clock12 at 16:12, checked on that version) and what cldr.py writes.
KNOWN_CLOCK_SPACES = (
    {"tags": "every tag", "fields": CLOCKS_12, "page": {"47.0": "4:12 PM", "48.0": "4:12 PM"}, "python": "4:12\u202fPM",
     "example": "fr-FR", "page_versions": (SEEN, SEEN_48), "python_version": TABLE,
     "why": "Intl's DateTimeFormat writes U+0020 where CLDR's 12-hour patterns put U+202F beside the day period"},
    {"tags": "Spanish's", "fields": CLOCKS_12, "page": {"47.0": "4:12 p.\u00a0m.", "48.0": "4:12 p. m."},
     "python": "4:12\u202fp.\u202fm.", "example": "es-ES", "page_versions": (SEEN, SEEN_48), "python_version": TABLE,
     "why": "the space inside Spanish's day period: Intl writes U+00A0 from ICU 77.1 (CLDR 47.0) and U+0020 from "
            "ICU 78.2 (CLDR 48.0); Babel 2.18.0's day periods hold U+202F"},
)
# Where node's Intl and cldr.py differ beyond those two: (tag, field) -> what each writes for the field's grid row,
# with the versions it was seen on and why (known() adds one). The regions #91 listed (es-AR, es-CO, es-GQ, es-PY,
# fr-MA, tr-CY) follow the page since #111, through tests/cldr_gen.py's PAGE_RULES and formats.money's tight. What is
# left is one node build's own data, not the page's.
KNOWN_CLDR_DIFFERENCES: dict[tuple[str, str], dict[str, Any]] = {}


def known(tag: str, field: str, page: list[str], python: list[str], why: str, seen: str = SEEN) -> None:
    KNOWN_CLDR_DIFFERENCES[(tag, field)] = {"page": page, "python": python, "page_versions": seen, "python_version": TABLE,
                                           "why": why}


_MA_NODE_PERIODS = ("node 20's full ICU 78.2 data writes fr-MA's day periods as French's AM and PM. Chromium's ICU 78.2 "
                    "data (VS Code's Electron, the build WebView2 carries) and node 24's ICU 77.1 write a.m. and p.m., "
                    "which the widgets follow (#111)")
known("fr-MA", "clock12", ["12:05 AM", "9:39 AM", "12:00 PM", "4:12 PM", "11:59 PM"],
      [f"12:05{NNB}a.m.", f"9:39{NNB}a.m.", f"12:00{NNB}p.m.", f"4:12{NNB}p.m.", f"11:59{NNB}p.m."], _MA_NODE_PERIODS,
      seen=SEEN_48)
known("fr-MA", "dayClock12",
      [f"{day}. 4:12 PM" for day in ("lun", "mar", "mer", "jeu", "ven", "sam", "dim")] + ["sam. 9:05 AM"],
      [f"{day}. 4:12{NNB}p.m." for day in ("lun", "mar", "mer", "jeu", "ven", "sam", "dim")] + [f"sam. 9:05{NNB}a.m."],
      _MA_NODE_PERIODS, seen=SEEN_48)


@pytest.fixture(scope="module")
def cldr_page() -> dict[str, Any]:
    """What tests/webui_cldr.js wrote with formats.js for every format tag over CLDR_GRID (one node run, TZ=UTC)."""
    run = subprocess.run([node_binary(), str(CLDR_JS), str(WEBUI), json.dumps(CLDR_TAGS), json.dumps(CLDR_GRID)],
                         capture_output=True, text=True, timeout=120, check=False, encoding="utf-8",
                         env={**os.environ, "TZ": "UTC"}, creationflags=NO_WINDOW)
    assert run.returncode == 0, run.stderr[-800:]
    return json.loads(run.stdout)


def node_versions(page: dict[str, Any]) -> str:
    versions = page["versions"]
    return f"node {versions['node']}, ICU {versions['icu']}, CLDR {versions['cldr']}"


def page_differences(pages: dict[str, dict[str, list[str]]]) -> dict[tuple[str, str], dict[str, list[str]]]:
    """Each (tag, field) where the page's row is not what python_formats(tag) writes: a 12-hour clock compared after
    clock_spaces_folded on both sides, every other field as written. Each entry holds both rows as written."""
    found: dict[tuple[str, str], dict[str, list[str]]] = {}
    for tag, page in pages.items():
        for field, values in python_formats(tag).items():
            if field in CLOCKS_12:
                differs = list(map(clock_spaces_folded, page[field])) != list(map(clock_spaces_folded, values))
            else:
                differs = page[field] != values
            if differs:
                found[(tag, field)] = {"page": page[field], "python": values}
    return found


def not_on_the_list(found: dict[tuple[str, str], dict[str, list[str]]]) -> dict[str, dict[str, list[str]]]:
    """The differences KNOWN_CLDR_DIFFERENCES does not list with exactly these two rows."""
    listed = {key: {"page": entry["page"], "python": entry["python"]} for key, entry in KNOWN_CLDR_DIFFERENCES.items()}
    return {f"{tag} {field}": diff for (tag, field), diff in found.items() if listed.get((tag, field)) != diff}


@ui_only
def test_the_pages_formats_are_the_widgets_in_every_format_tag(cldr_page: dict[str, Any]) -> None:
    """V2 of #91's plan (D1): in every format tag of French, Spanish and Turkish, the page's numbers, percents, money,
    12- and 24-hour clocks, day clocks, weekdays, months, durations (both ways, D14) and letter case (formats.js through
    node's Intl) are what formats.py and casing.py write from cldr.py, except the differences listed above, each with
    both values and both versions. A difference not on the list fails with its entry: check which side is right first
    (a wrong table is fixed in tests/cldr_gen.py, a wrong page in formats.js), then list it with the versions it was
    seen with. On the versions the list was made with, a listed difference that is gone fails too; on each CLDR version
    a clock rule lists, its example is what that version writes."""
    versions = node_versions(cldr_page)
    print(f"V2: the page's Intl on {versions}; the widgets' {TABLE}")
    assert sorted(cldr_page["tags"]) == CLDR_TAGS
    for tag in CLDR_TAGS:
        assert cldr_page["tags"][tag]["tag"] == tag, (tag, cldr_page["tags"][tag]["tag"])
    found = page_differences(cldr_page["tags"])
    unknown = not_on_the_list(found)
    assert not unknown, f"{versions} against {TABLE}, not on the list: " + json.dumps(unknown)
    seen_here = {key for key, entry in KNOWN_CLDR_DIFFERENCES.items() if entry["page_versions"] == versions}
    assert sorted(seen_here - set(found)) == [], "listed, no longer different"
    cldr_version = cldr_page["versions"]["cldr"]
    for rule in KNOWN_CLOCK_SPACES:
        if cldr_version in rule["page"]:
            assert cldr_page["tags"][rule["example"]]["clock12"][3] == rule["page"][cldr_version], (cldr_version, rule)
        assert python_formats(rule["example"])["clock12"][3] == rule["python"], rule


def test_v2s_12_hour_fold_lets_only_the_space_beside_a_day_period_differ() -> None:
    """V2's one fold in a 12-hour clock (clock_spaces_folded): es-ES's page as CLDR 47 writes it (U+00A0 inside the day
    period) and as CLDR 48 writes it (U+0020) are the widgets' clocks (U+202F); a page that differs from them by
    anything else in the same field (the space inside or before the period gone, a space added, the other period, a
    digit, the space after the weekday) is a difference V2 does not list, so it fails V2. node is not needed: the pages
    are made from formats.py's own rows."""
    python = python_formats("es-ES")

    def page_with(change: Any) -> dict[str, dict[str, list[str]]]:
        rows: dict[str, Any] = {field: list(values) for field, values in python.items()}
        for field in CLOCKS_12:
            rows[field] = [change(text) for text in rows[field]]
        return {"es-ES": {"tag": "es-ES", **rows}}

    def on_47(text: str) -> str:
        return text.replace("\u202fa.\u202fm.", " a.\u00a0m.").replace("\u202fp.\u202fm.", " p.\u00a0m.")

    def on_48(text: str) -> str:
        return text.replace("\u202f", " ")

    assert "\u202fp.\u202fm." in python["clock12"][3] and "\u202fa.\u202fm." in python["dayClock12"][7]
    assert on_47(python["clock12"][3]) == "4:12 p.\u00a0m." and on_48(python["clock12"][3]) == "4:12 p. m."
    assert page_differences(page_with(on_47)) == page_differences(page_with(on_48)) == {}
    both = {("es-ES", "clock12"), ("es-ES", "dayClock12")}
    for change, fields in (
            (lambda t: on_48(t).replace("a. m.", "a.m.").replace("p. m.", "p.m."), both),      # inside the period: gone
            (lambda t: on_48(t).replace(" a. m.", "a. m.").replace(" p. m.", "p. m."), both),  # before it: gone
            (lambda t: on_48(t).replace("p. m.", "p.  m."), both),                              # a space added
            (lambda t: on_48(t).replace("p. m.", "a. m."), both),                               # the other period
            (lambda t: on_47(t).replace("4:12", "4:13"), both),                                 # a digit
            (lambda t: on_48(t).replace(", ", ",\u00a0"), {("es-ES", "dayClock12")})):          # after the weekday
        found = page_differences(page_with(change))
        assert set(found) == fields and set(not_on_the_list(found)) == {f"{tag} {field}" for tag, field in fields}
    for rule in KNOWN_CLOCK_SPACES:
        assert {clock_spaces_folded(text) for text in rule["page"].values()} == {clock_spaces_folded(rule["python"])}


@ui_only
def test_a_duration_is_the_same_with_intls_duration_format_and_without(cldr_page: dict[str, Any]) -> None:
    """D14 of #91's plan: formats.js writes a duration with Intl.DurationFormat where the WebView has it, else each unit
    through NumberFormat, joined as a unit list. The two must write the same text in every tag; if they ever differ,
    DURATION_FORMAT = false in formats.js writes them all the second way. A node without DurationFormat (node 20) skips
    this, and runs only the second way (which V2 holds to formats.py)."""
    assert cldr_page["versions"]["unitsWithDurationFormat"] is False          # the second way ran without it
    if not cldr_page["versions"]["durationFormat"]:
        pytest.skip(f"{node_versions(cldr_page)} has no Intl.DurationFormat: only the NumberFormat way runs here")
    differ = {tag: [row["duration"], row["durationUnits"]] for tag, row in cldr_page["tags"].items()
              if row["duration"] != row["durationUnits"]}
    assert not differ, differ
    assert cldr_page["tags"]["tr-TR"]["duration"][3:5] == ["1 sa", "3s 27d"]


def test_the_language_row_is_wired_into_the_settings_page() -> None:
    """#87: the row is on the Settings page, follows the stored choice, and its button saves it; a new state checks the
    locale before anything else."""
    assert "languageRow()" in function_body(APP_CODE, "settingsPage")
    assert "syncLanguage(main);" in function_body(APP_CODE, "syncSettings")
    assert re.search(r'action === "language-apply"\) \{\s*applyLanguage\(\);', function_body(APP_CODE, "settingsClick"))
    assert re.match(r"\{\s*if \(localeReload\(\)\) \{", function_body(APP_CODE, "onState"))


@ui_only
def test_the_language_row_shows_once_a_second_language_is_offered(checks: dict[str, Any]) -> None:
    row = checks["i18n"]["row"]
    assert ('<select class="nm-select" id="set-language" aria-labelledby="language-t" aria-describedby="language-d"'
            ' data-synced="fr">') in row                                              # no data-key: Apply saves it
    assert 'data-key="language"' not in row
    assert '<option value="system" lang="en">System default (Türkçe)</option>' in row  # names what system resolves to
    assert ('<option value="en" lang="en">English</option><option value="fr" lang="fr" selected>Français</option>'
            '<option value="tr" lang="tr">Türkçe</option>') in row                    # each named in itself
    assert '<span lang="en">Language</span>' in row                                   # English on a French page
    assert '<button class="nm-btn" type="button" data-action="language-apply"><span lang="en">Apply</span></button>' in row
    assert checks["i18nEnglishRow"] == ""                                              # English alone: nothing to choose
    assert '<option value="fr" lang="fr">Français</option>' in checks["i18nDevRow"]    # a dev instance's language


@ui_only
def test_the_language_list_keeps_a_pick_until_it_is_applied(checks: dict[str, Any]) -> None:
    assert checks["i18nSync"] == {"kept": "tr", "followed": "system", "synced": "system"}
    assert checks["i18nApply"] == [{"url": "/api/settings", "method": "POST", "body": {"language": "tr"}}]
    assert checks["i18nApplyClick"] == [{"url": "/api/settings", "method": "POST", "body": {"language": "es"}}]


@ui_only
def test_a_language_change_reloads_the_page_once(checks: dict[str, Any]) -> None:
    """The language, the format or the clock: a reload, once. Never on a page that did not know its own locale, and
    never twice in a row for the same locale in a window, so a server that kept serving another locale could not loop
    the page."""
    assert checks["i18nChanged"] == [False, False, False, True, True, True, False]
    assert checks["i18nLocaleReload"] == [[False, 0], [False, 0], [False, 0], [True, 1]]   # own locale, none, null, new
    assert checks["i18nReloads"] == 1
    mark = "usage-monitor-locale-reload:en|en-US|12"
    assert checks["i18nMarks"] == [{"answered": True, "count": 1, "name": mark},          # the first time: a reload
                                   {"answered": False, "count": 0, "name": mark},         # the same again: no loop
                                   {"answered": True, "count": 1, "name": mark}]          # after another locale: again
    assert checks["i18nUnknownPage"] == [{"answered": False, "count": 0, "name": ""}] * 3
    assert checks["i18nArrived"] == ""                                      # it came in the marked locale: done
    assert checks["i18nNotArrived"] == "usage-monitor-locale-reload:en|en-US|12"
    english = {**i18n.ENGLISH.as_json(), "offered": ["en"]}                 # the page's own fallback, whatever is offered
    assert checks["i18nDefaultLocale"] == english == checks["i18nBadMeta"] and checks["i18nBadMetaKnown"] is False


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


# ── Part 3 (#90): the scripts read ids, never the words on the page ──────────────────────────────────────────

TONE_IDS = {"ok", "warn", "alert", "acc", "neutral"}


def tone_in(html: str) -> str | None:
    found = re.search(r'<span class="nm-chip[^"]*"[^>]*data-tone="([^"]*)"', html)
    return found.group(1) if found else None


@ui_only
def test_every_card_names_itself_by_its_key_the_settings_and_about_cards_too(checks: dict[str, Any]) -> None:
    for page in ("overview", "limits", "cost", "efficiency", "history"):
        keys = [card["key"] for card in checks["empty"][page]["cards"]]
        assert checks["cards"][page] == keys, page
        assert all(checks["cards"][page]), page                                       # no card without a key
    assert checks["cards"]["settings"] == ["theme", "widgets", "data-plan", "alerts", "guard", "system"]
    assert checks["cards"]["about"] == ["about-app", "about-links", "about-credits", "about-fonts", "about-supporters"]
    assert checks["cards"]["limitsInner"] == ["credits", "surfaces"]                  # the Limits wrapper's two sections
    assert checks["cards"]["none"] == ["none"] and "unpriced" in checks["cards"]["unpriced"]


# Each card's Gates icon, as the theme drew it while the icons followed the card titles; Data and plan gained its icon in #90,
# when they began to follow the cards. A key that loses, swaps or gains a glyph fails here.
GATES_CARD_GLYPHS = {
    "cost": "E8C7", "value": "E8C7", "credits": "E8C7", "fast": "E8C7",
    "efficiency": "E945", "cache": "E945", "thinking": "E945", "productivity": "E945",
    "models": "E9F9", "by-model": "E9F9", "effort": "E9F9", "haiku": "E9F9", "tool-mix": "E9F9", "mcp-mix": "E9F9",
    "monthly": "E9F9",
    "surfaces": "E7C4", "by-folder": "E7C4", "threads": "E7C4", "windows": "E7C4",
    "burn": "E9D2", "compactions": "E9D2", "context": "E9D2", "tools": "E9D2", "api-web": "E9D2", "pipeline": "E9D2",
    "tokens": "E9D2",
    "sessions": "E81C", "streaks": "E81C",
    "odometer": "E787", "calendar": "E787", "matrix": "E787",
    "export": "E896", "theme": "E790", "widgets": "E7F4", "data-plan": "E8F1", "alerts": "EA8F", "guard": "EA18",
    "system": "E713", "about-app": "E946", "none": "E946", "about-links": "E71B", "about-supporters": "EB51",
    "unpriced": "E7BA", "not-priced": "E7BA",
}


def test_each_gates_card_keeps_its_icon() -> None:
    rules = re.findall(r'((?:\.nm-card\[data-card="[^"]+"\],?\s*)+)\{\s*--g-ico:\s*"[^0-9A-F"]([0-9A-F]{4})";\s*\}',
                       THEME_CSS["gates"])
    glyphs: dict[str, str] = {}
    for selectors, glyph in rules:
        for key in re.findall(r'data-card="([^"]+)"', selectors):
            assert key not in glyphs, f"{key} is named twice"
            glyphs[key] = glyph
    assert glyphs == GATES_CARD_GLYPHS


@ui_only
def test_gates_names_only_cards_the_pages_build(checks: dict[str, Any]) -> None:
    """Each data-card key gates.css names is carried by a card the page builds; Data and plan is the Settings card's key."""
    named = set(re.findall(r'\[data-card="([^"]+)"\]', THEME_CSS["gates"]))
    built = {key for keys in checks["cards"].values() for key in keys}
    assert named <= built, sorted(named - built)
    assert "data-plan" in named


@ui_only
def test_every_gates_chip_carries_a_tone_id_and_an_unknown_one_is_neutral(checks: dict[str, Any]) -> None:
    tags = list(checks["chipTags"])
    for html in checks["guard"]["live"].values():
        tags += re.findall(r'<span class="nm-chip[^"]*"[^>]*>', html)
    assert len(tags) >= 10
    for tag in tags:
        tone = re.search(r'data-tone="([^"]*)"', tag)
        assert tone and tone.group(1) in TONE_IDS, tag
    assert checks["tones"]["pace"]["noClock"] == "neutral"


@ui_only
def test_a_chip_takes_its_tone_from_its_status_id_not_from_its_words(checks: dict[str, Any]) -> None:
    assert checks["tones"]["pace"] == {"ahead": "warn", "behind": "ok", "onPace": "ok", "noClock": "neutral", "unused": "neutral", "noDraw": "neutral"}
    assert checks["tones"]["session"] == ["alert", "warn", "ok", "neutral", "neutral", "neutral"]     # LIMITED, AT_RISK, SAFE, IDLE, UNKNOWN, unknown
    assert checks["tones"]["weekly"] == ["warn", "ok", "ok", "neutral", "neutral"]                   # AHEAD, ON_PACE, BEHIND, UNKNOWN, unknown
    assert checks["burnTone"] == {"IDLE": "neutral", "SLOW": "neutral", "NORMAL": "ok", "FAST": "acc", "VERY FAST": "warn", "constructor": "neutral"}
    assert checks["paceWords"] == ["Ahead of pace", "Under pace", "No draw"]                         # the catalog's (#98), natural case
    guard = checks["guard"]["live"]
    assert (tone_in(guard["off"]), tone_in(guard["armed"]), tone_in(guard["holding"])) == ("neutral", "ok", "alert")


@ui_only
def test_the_uplink_line_and_the_age_are_written_from_codes_and_units(checks: dict[str, Any]) -> None:
    assert checks["uplinkWords"] == {                                                 # #98: the catalog's, in natural case
        "live": "Live", "loading": "Acquiring", "stale": "Auth lost", "offline": "No carrier", "rate_limited": "Rate limited",
        "no_credentials": "No login", "paused": "Paused", "unknown": "Acquiring", "nolink": "No link"}
    assert [(row["badge"], row["sentence"]) for row in checks["age"]] == [
        ("0 S", "0 s"), ("0 S", "0 s"), ("59 S", "59 s"), ("1 M", "1 m"), ("59 M", "59 m"),
        ("1 H", "1 h"), ("47 H", "47 h"), ("2 D", "2 d"), ("4 D", "4 d")]
    assert checks["ageNone"] == "--"
    assert "These figures are from 3 m ago." in checks["staleBanner"]                 # the sentence reads as it did
    assert ".toLowerCase()" not in function_body(APP_CODE, "staleBanner") and ".toLowerCase()" not in function_body(APP_CODE, "renderAge")
    assert 'replace("UPLINK "' not in APP_CODE


@ui_only
def test_the_scoped_limits_are_matched_by_kind_and_model_so_one_label_can_serve_two(checks: dict[str, Any]) -> None:
    assert checks["scoped"] == {"fable": 10, "opus": 90, "unnamed": 10, "none": None}      # the label is shared; the model is not
    assert checks["scopedSurface"] == {"code": 40, "cowork": 5, "fable": 70}               # no model: the surface tells them apart
    assert ".label" not in function_body(APP_CODE, "matchScoped")


@ui_only
def test_the_accent_tooltip_follows_the_theme_name_and_not_the_colour_word(checks: dict[str, Any]) -> None:
    assert checks["accentTitled"] == {"amber": True, "blue": True, "blackwall": False, "unnamed": False}


@ui_only
def test_the_total_row_of_the_token_table_is_marked_by_a_code(checks: dict[str, Any]) -> None:
    assert checks["tokensTotal"] == "is-today" and checks["tokensToday"] == 1
    assert 'row[0] === "Total"' not in APP_CODE


def test_gates_has_no_english_chip_words_and_its_script_reads_the_tone_attribute() -> None:
    script = read("themes/gates.js")
    assert "TONES" not in script and '"AT RISK"' not in script and '"UNDER PACE"' not in script
    assert 'getAttribute("data-tone")' in function_body(script, "toneChip")


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
    for needle in ("enter(main, page)", "cancel(main)", "arrive(card, info)", "mon:card-data", "mon:page-rendered",
                   "fx-run", "fx-quiet", "data-pending", "data-final", "data-fill", "data-fresh", "data-state=pending | ready | error",
                   "data-updating", "window.USAGE_MONITOR_THEME_FX[id]", "fx-hold, which kept the page hidden"):
        assert needle in APP_DOC, needle
    assert "Pending: a page is never empty" in APP_DOC


def test_the_page_calls_the_hooks_and_does_not_wait_for_the_reports() -> None:
    code = strip_comments(APP_CODE)
    for name in ("themeFx", "themeReady", "wantEnter", "playEnterIfDue", "cancelFx", "announce"):
        assert f"function {name}(" in code, name
    assert 'fx.enter(main, key)' in code and 'fx.arrive(u.el, info)' in code and 'fx.cancel(main)' in code
    assert '"mon:card-data"' in code and '"mon:page-rendered"' in code
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
    assert "skipped" not in checks["banner"]["staleStatus"]
    assert 'role="status"></p>' in checks["banner"]["staleStatus"]


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
        assert "<b>Statistics unavailable</b>" in shown["problem"], name                 # banner.stats.unavailable_title (#98)
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
    """The Gates theme sets a line in sentence case only when all of it is capitals (gates.js isCaps). The refresh line is still
    written in capitals (#100's) and raises the monitor's lower-case reason ("update apply failed") with it, or Gates would
    show a shouting prefix over a lower-case tail. The update buttons' lines follow #87's case rule since #97: the catalog's
    whole sentences in natural case (Nostromo and Night City set them in capitals by CSS, Gates shows them as written),
    worded by the answer's status, so a 503 that gives a reason is "HTTP 503" (test_each_answer_is_worded_by_its_status)."""
    lines = checks["failureLines"]
    assert lines["reason"] == {
        "refresh": "REFRESH FAILED: UPDATE APPLY FAILED", "check": "Check failed: HTTP 503",
        "install": "Update failed: HTTP 503", "skip": "Could not skip: HTTP 503"}
    assert lines["status"] == {
        "refresh": "REFRESH FAILED: HTTP 502", "check": "Check failed: HTTP 502",
        "install": "Update failed: HTTP 502", "skip": "Could not skip: HTTP 502"}                # no reason from the monitor: its status
    assert lines["lost"] == {"refresh": "REFRESH FAILED: THE DASHBOARD LOST ITS LINK", "check": "Check failed: the dashboard lost its link"}
    assert set(lines) == {"reason", "status", "lost"}
    assert checks["failureLinesCaps"] is True
    for group in lines.values():
        assert re.search(r"[A-Z]", group["refresh"]) and not re.search(r"[a-z]", group["refresh"]), group["refresh"]


@ui_only
def test_check_for_updates_does_not_say_up_to_date_when_it_could_not_look(checks: dict[str, Any]) -> None:
    said = checks["checkText"]
    assert said["notChecked"] == "Could not reach GitHub; try again later."
    assert said["upToDate"] == said["oldMonitor"] == said["noResult"] == "You are up to date."
    assert said["found"] == said["foundBefore"] == "An update is available."          # a release an earlier check found is still on offer


def test_every_failure_status_line_goes_through_failed_text() -> None:
    """The static half of the checks above: no line is built as an upper-case prefix plus the monitor's own reason. Refresh
    and resume go through failedText (#100's); the update buttons' lines go through updateRefusal since #97, which words
    the answer's status and reads its error only to tell a 500's rare "internal error" from the step's own failure."""
    code = strip_comments(APP_CODE)
    assert not re.search(r'"[A-Z][A-Z ]*: " \+ errorText\(', code)
    assert function_body(APP_CODE, "failedText").count('FMT.upper(errorText(res), "en")') == 1      # English's rule (#91)
    for prefix in ("REFRESH FAILED", "RESUME FAILED"):
        assert f'failedText("{prefix}", res)' in code, prefix
    for prefix in ("CHECK FAILED", "UPDATE FAILED", "COULD NOT SKIP"):
        assert prefix not in code, prefix
    for name in ("checkUpdateText", "installUpdate", "skipUpdate", "updateRefusal"):
        body = strip_comments(function_body(APP_CODE, name))
        assert "failedText(" not in body and "errorText(" not in body, name
    click = function_body(APP_CODE, "settingsClick")
    check = click[click.index('action === "check-update"'):click.index('action === "install-update"')]
    assert "failedText(" not in check and "errorText(" not in check
    refusal = strip_comments(function_body(APP_CODE, "updateRefusal"))
    assert refusal.count(".error") == 1 and 'res.data.error === "internal error"' in refusal


# ── The update lines (#97) ────────────────────────────────────────────────────────

# Every line under the update buttons (Settings' and the banner's), as app.js said it before #97 and as the Gates theme
# showed it, with the catalog key that holds it since: tests/fixtures/update_lines_english.json, made in step b1 of #97
# from the code before the move.
UPDATE_LINES: list[dict[str, Any]] = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "update_lines_english.json").read_text(encoding="utf-8"))["lines"]


def test_the_update_lines_record_is_whole() -> None:
    """Each answer of each update button once: the check's three results, the install's three modes, the skip, the two
    waits, the offered line, and for each button every refusal the monitor gives (500, its rare internal error, 501, 401,
    another status, a lost link). The lines were in capitals, the offered one in natural case."""
    said = [line["said"] for line in UPDATE_LINES]
    reasons = ("error", "internal", "unavailable", "token", "http", "lost")
    assert sorted(said) == sorted(["check:checking", "check:found", "check:up_to_date", "check:unreached", "install:installing",
                                   "install:browser", "install:installer", "install:not_started", "skip:skipping", "offered",
                                   *(f"{op}:{reason}" for op in ("check", "install", "skip") for reason in reasons)])
    assert len({line["key"] for line in UPDATE_LINES}) == len(UPDATE_LINES)
    for line in UPDATE_LINES:
        name = line["said"]
        assert line["today"] == (line["gates"] if name == "offered" else line["today"].upper()), name
        assert line["gates"] and line["gates"].upper() == line["today"].upper(), name


@ui_only
def test_the_update_lines_render_todays_english(checks: dict[str, Any]) -> None:
    """#87's case rule for the update lines (#97): each update button's answer, through the page's real click paths, is its
    catalog key's sentence in natural case, which is what the Gates theme showed before the move (gates.js leaves it as it
    is: test_motion.py), and in English's capitals, which Nostromo and Night City set by CSS
    (test_the_update_texts_take_their_capitals_from_css_in_every_look), it is the line they showed."""
    english = json.loads((WEBUI / "i18n" / "en.json").read_text(encoding="utf-8"))
    said = checks["updateSaid"]
    assert set(said) == {line["said"] for line in UPDATE_LINES}
    for line in UPDATE_LINES:
        name = line["said"]
        text = english[line["key"]]
        for placeholder, value in line["values"].items():
            text = text.replace("{" + placeholder + "}", value)
        assert said[name] == {"text": line["gates"], "lang": "en"}, name
        assert text == line["gates"], name
        assert casing.upper(text, "en") == line["today"].upper() and (name == "offered" or casing.upper(text, "en") == line["today"]), name


@ui_only
def test_each_answer_is_worded_by_its_status(checks: dict[str, Any]) -> None:
    """The failure lines word the HTTP status of the monitor's answer (#97; the server's codes are #102's): on the update routes
    501 is a step this monitor does not have, 500 a step that failed, or its rare "internal error" (that exact error only), 401
    the dashboard's token; any other status is named by its number, and no answer at all is a lost link."""
    english = json.loads((WEBUI / "i18n" / "en.json").read_text(encoding="utf-8"))
    for op, words in checks["updateRefusals"].items():
        key = f"settings.update.failed.{op}."
        en = {reason: {"text": english[key + reason], "lang": "en"} for reason in ("error", "internal", "unavailable", "token", "lost")}
        assert words["failed"] == words["bare500"] == words["internalOtherwise"] == en["error"], op
        assert words["internal"] == en["internal"] and words["unavailable"] == en["unavailable"], op
        assert words["token"] == en["token"] and words["lost"] == en["lost"], op
        assert words["forbidden"] == {"text": english[key + "http"].replace("{status}", "403"), "lang": "en"}, op
        assert words["teapot"] == {"text": english[key + "http"].replace("{status}", "418"), "lang": "en"}, op


@ui_only
def test_an_update_failure_never_shows_the_servers_words(checks: dict[str, Any]) -> None:
    """The monitor's `error` is English, and the page never puts it on an update line: through each button's click, every
    status the server can answer with carries an error of its own words here, and no line shows them."""
    shown = checks["updateServerWords"]
    assert len(shown) == 18 and checks["updateServerWordsSeen"] == []
    assert shown["install:403"] == "Update failed: HTTP 403" and shown["skip:500"] == "Could not skip: update skip failed"


@ui_only
def test_english_on_a_french_page_is_marked(checks: dict[str, Any]) -> None:
    """On another language's page an update line its catalog lacks is English's, and the element that shows it carries
    lang="en" (the line under Settings' buttons, set by syncUpdate; the banner's line, in its markup), so it is cased by
    English's rules; a line the catalog has is not marked. The buttons' and the banner's words go through tHtml's lang="en"
    span, the toggle row's too, and the product's name through tName (translate="no")."""
    french = checks["updateFrench97"]
    assert french["offered"] == {"text": "Version 2.4.0 is available.", "lang": "en"}
    assert french["found"] == {"text": "Une mise à jour est disponible.", "lang": None}
    assert french["failed"] == {"text": "Check failed: update check failed", "lang": "en"}
    installed = french["installed"]
    assert installed["said"] == {"text": "Opened the release page in your browser.", "lang": "en"}
    assert installed["line"] == {"text": "Opened the release page in your browser.", "lang": "en"}
    banner = installed["banner"]
    assert '<p class="nm-status" role="status" lang="en">Opened the release page in your browser.</p>' in banner
    assert '<b><span lang="en"><span translate="no">Claude Usage Monitor</span> v2.4.0 is available</span></b>' in banner
    for words in ("Update now", "Release notes", "Skip this version", "Update now installs it and restarts the monitor."):
        assert f'<span lang="en">{words}</span>' in banner, words
    assert 'aria-label="Update available"' in banner
    assert '<p class="nm-status" role="status"></p>' in french["bannerEmpty"]
    settings = french["settings"]
    for words in ("Check for updates", "Look for a newer release once in a while.", "Check for updates now", "Install update",
                  "Skip this version"):
        assert f'<span lang="en">{words}</span>' in settings, words


# What each look does to the update texts' case (text-transform): Nostromo sets them all in capitals, Night City (Blackwall
# and Edgerun) all but the banner's sentence and the toggle row's help, Gates none.
UPDATE_CASE = {
    "nostromo": {"settings.line": "uppercase", "settings.check": "uppercase", "settings.install": "uppercase",
                 "settings.skip": "uppercase", "settings.rowLabel": "uppercase", "settings.rowHelp": "none",
                 "banner.title": "uppercase", "banner.sentence": "uppercase", "banner.now": "uppercase",
                 "banner.notes": "uppercase", "banner.skip": "uppercase", "banner.line": "uppercase"},
}
UPDATE_CASE["night_city"] = UPDATE_CASE["edgerun"] = {**UPDATE_CASE["nostromo"], "banner.sentence": "none"}
UPDATE_CASE["gates-dark"] = UPDATE_CASE["gates-light"] = {name: "none" for name in UPDATE_CASE["nostromo"]}


@ui_only
def test_the_update_texts_take_their_capitals_from_css_in_every_look(checks: dict[str, Any]) -> None:
    """#97 (critic 11 of its plan): the case of each update text as the cascade of its look's stylesheets gives it, on the
    page's own markup (webui_checks.js's updateCase reads every rule that sets text-transform and matches the element or an
    ancestor, by importance, specificity and order), not one rule read alone, so a more specific rule that undid the capitals
    (".nm-update .nm-status { text-transform: none }") fails here. Each update line is stored in natural case, and where
    the look sets capitals, English's capitals of it are the line before #97; Gates shows it as stored. The page's own
    structural styles set no case."""
    assert "text-transform" not in strip_comments(APP_CODE) and "text-transform" not in strip_comments(CHARTS)
    found = checks["updateCase"]
    assert set(found) == set(UPDATE_CASE)
    for look, wanted in UPDATE_CASE.items():
        assert {name: (shown or {}).get("value") for name, shown in found[look].items()} == wanted, look
    for look in ("nostromo", "night_city", "edgerun"):
        assert found[look]["settings.line"]["value"] == found[look]["banner.line"]["value"] == "uppercase"
    lines = {line["gates"]: line["today"] for line in UPDATE_LINES}
    for line, today in lines.items():
        assert casing.upper(line, "en") == today.upper()


# ── A running update on Settings (#58) ────────────────────────────────────────────

# The status line of each failure code (update_check.REASONS), as en.json words it.
UPDATE_FAILURE_TEXTS = {
    "timeout": "The download stopped: nothing arrived for 60 seconds.",
    "network": "The download stopped: the connection to GitHub failed.",
    "http": "The download stopped: GitHub answered HTTP 404.",
    "redirect": "The download stopped: it was redirected to an address that is not HTTPS, which the monitor does not follow.",
    "too_large": "The download stopped: the installer is larger than 512 MB.",
    "cut_short": "The download stopped before the whole installer arrived.",
    "digest": "The download did not match the release's SHA-256 and was deleted; the release page was opened in your browser.",
    "rejected": "The installer link is not this project's release download; the release page was opened in your browser.",
    "helper": "The installer could not be started.",
    "local": "The update stopped: a file it needs could not be written or moved on this PC.",
    "error": "The update could not be installed.",
}
UPDATE_LOST = "The dashboard lost its link to the monitor during the update."
UPDATE_GONE = "The monitor is closing to install the update. Open the dashboard again from the tray icon once it is back."
UPDATE_STALLED = "The download has stalled; it carries on if data starts to arrive again."
STEP_TONE = {"done": "ok", "now": "acc", "next": "neutral", "stalled": "warn", "failed": "alert"}
STEP_CLASSES = {"done": "is-ok", "now": "", "next": "", "stalled": "solid is-warn", "failed": "solid is-crit"}
NOTHING = {"view": None, "html": ""}


def update_run(checks: dict[str, Any]) -> dict[str, Any]:
    return checks["updateProgress"]


def test_the_update_reasons_agree_with_the_updater() -> None:
    """The page words every code and every phase the updater's record can hold (update_check.REASONS, ACTIVE_PHASES), and
    builds each failure's words when it draws them: the table holds functions, not texts taken at load time."""
    import update_check

    table = APP[APP.index("  var UPDATE_FAILED = {"):]
    table = table[:table.index("\n  };")]
    assert re.findall(r"^    (\w+): ", table, re.M) == list(update_check.REASONS)
    assert re.findall(r"^    (\w+): function \(", table, re.M) == list(update_check.REASONS)
    steps = re.search(r"var UPDATE_STEP_OF = \{([^}]*)\};", APP).group(1)
    assert re.findall(r"(\w+): \d", steps) == list(update_check.ACTIVE_PHASES)
    assert set(update_check.PHASES) == {*update_check.ACTIVE_PHASES, "idle", "failed"}
    assert f'var UPDATE_STATUS_PATH = "{server.UPDATE_STATUS_PATH}";' in APP
    assert 'api("GET", UPDATE_STATUS_PATH)' in function_body(APP_CODE, "askUpdateStatus")


def test_the_update_asks_and_the_check_hold_are_one_number_each() -> None:
    """The cadence of #58's plan, and owner question 1's hold on the Check step: 0, so the Check chip shows done as soon as
    the record has passed it (the SHA-256 is worked out as the file arrives)."""
    for line in ("var UPDATE_POLL_MS = 500;", "var UPDATE_IDLE_POLL_MS = 3000;", "var UPDATE_LOST_AFTER = 3;",
                 "var UPDATE_CLICK_GRACE_MS = 3000;", "var UPDATE_CHECK_HOLD_MS = 0;"):
        assert APP_CODE.count(line) == 1, line
    assert "UPDATE_CHECK_HOLD_MS" in function_body(APP_CODE, "progressView")


@ui_only
def test_the_update_shows_its_bytes_and_a_bar_that_fills(checks: dict[str, Any]) -> None:
    """4404020 of 22020096 bytes is 4.2 of 21.0 MB (1 MB = 1,048,576 bytes, one decimal), six of the 32 cells lit and the next
    one partly, and 20 for a screen reader. The done is floored and the total rounded: 4466934 bytes (4.26 MB) reads 4.2, and
    22015000 of the same total reads 20.9, where rounding both would read 21.0 before the end."""
    b = update_run(checks)["bytes"]
    fifth = b["fifth"]
    assert fifth["size"] == fifth["text"] == "4.2 of 21.0 MB"
    assert (fifth["lit"], fifth["part"], fifth["partFill"], fifth["now"], fifth["pending"]) == (6, 1, "40.0", 20, False)
    for attribute in ('role="progressbar"', 'aria-label="Update download"', 'aria-valuemin="0"', 'aria-valuemax="100"',
                      'aria-valuenow="20"', 'aria-valuetext="4.2 of 21.0 MB"'):
        assert attribute in fifth["html"], attribute
    assert "data-pending" not in fifth["html"]
    assert b["nearlyRoundsUp"]["size"] == "20.9 of 21.0 MB" and b["nearlyRoundsUp"]["now"] == 99
    assert b["midTenth"]["size"] == "4.2 of 21.0 MB"                       # 4.26 MB: floored, never rounded up
    assert b["whole"]["size"] == "21.0 of 21.0 MB" and (b["whole"]["lit"], b["whole"]["now"]) == (32, 100)
    assert b["nothingYet"]["size"] == "0.0 of 21.0 MB" and b["nothingYet"]["lit"] == 0 and not b["nothingYet"]["pending"]


@ui_only
def test_the_done_never_reads_the_totals_figure_before_the_end(checks: dict[str, Any]) -> None:
    """A total of 22,060,000 bytes rounds to 21.0 and a done of 22,030,000 floors to 21.0 as well: the done is written a tenth
    lower until the last byte, and as the total at the end."""
    b = update_run(checks)["bytes"]
    assert b["sameTenth"]["size"] == "20.9 of 21.0 MB"
    assert b["sameTenthEnd"]["size"] == "21.0 of 21.0 MB"
    assert b["sizes"] == [{"done": "4.2", "total": "21.0"}, {"done": "20.9", "total": "21.0"}, {"done": "20.9", "total": "21.0"},
                          {"done": "0.0", "total": "1.0"}, {"done": "0.9", "total": "1.0"}, {"done": "0.0", "total": "100.0"}]


@ui_only
def test_an_unknown_size_is_a_pending_bar(checks: dict[str, Any]) -> None:
    """No Content-Length: the bar carries data-pending, so each theme draws its own placeholder bar, with no figure for a
    screen reader and no cell lit; the sizes line counts what has arrived. Before the first byte it says it is connecting."""
    u = update_run(checks)["unknown"]
    so_far = u["soFar"]
    assert so_far["pending"] and so_far["now"] is None and so_far["lit"] == 0
    assert 'data-pending=""' in so_far["html"] and "aria-valuenow" not in so_far["html"]
    assert so_far["size"] == so_far["text"] == "4.2 MB so far"
    assert so_far["line"] == "Downloading version 2.4.0; the server did not say how large it is."
    assert u["connecting"]["size"] == "Connecting to GitHub…" and u["connecting"]["pending"]
    assert u["connecting"]["line"] == "Downloading version 2.4.0."
    assert u["stalledUnknown"]["pending"] and u["stalledUnknown"]["tone"] == "is-warn"


@ui_only
def test_the_steps_follow_the_phase(checks: dict[str, Any]) -> None:
    """Download, Check and Restart: the steps before the current one done, the current one under way (or stalled, or
    failed), the rest still to come, each with the guard card's tone for it. The words are natural case: Nostromo and Night
    City set the chips in capitals in their CSS."""
    run = update_run(checks)
    expected = {"connecting": ["now", "next", "next"], "downloading": ["now", "next", "next"],
                "stalled": ["stalled", "next", "next"], "checking": ["done", "now", "next"],
                "starting": ["done", "done", "now"], "restarting": ["done", "done", "now"]}
    failed = {"downloading": ["failed", "next", "next"], "checking": ["done", "failed", "next"],
              "starting": ["done", "done", "failed"], "restarting": ["done", "done", "failed"], "none": ["failed", "next", "next"]}
    for views, wanted in ((run["steps"], expected), (run["failedIn"], failed)):
        for name, stands in wanted.items():
            view = views[name]
            assert view["steps"] == stands, name
            assert view["tones"] == [STEP_TONE[s] for s in stands], name
            assert view["classes"] == [STEP_CLASSES[s] for s in stands], name
    assert re.findall(r'data-step="\w+">([^<]+)<', run["steps"]["checking"]["html"]) == ["Download", "Check", "Restart"]


@ui_only
def test_a_stall_and_each_failure_say_so(checks: dict[str, Any]) -> None:
    """A stall: the warn look and its own line, the run still going. A failure: the crit look, the line in the error tone,
    the bar drawn hot where it stopped, and Install update free again. Every code the updater can record has words of its
    own; an unknown code, or an HTTP failure with no status, reads as the plain failure."""
    import update_check

    run = update_run(checks)
    stall = run["stall"]
    assert (stall["tone"], stall["err"], stall["busy"], stall["hot"]) == ("is-warn", False, True, False)
    assert stall["line"] == UPDATE_STALLED and stall["size"] == "7.3 of 21.0 MB" and stall["lit"] == 11
    failures = run["failures"]
    assert set(UPDATE_FAILURE_TEXTS) == set(update_check.REASONS)
    for code, text in UPDATE_FAILURE_TEXTS.items():
        view = failures[code]
        assert unescape(view["line"]) == text, code
        assert (view["kind"], view["tone"], view["err"], view["hot"], view["busy"]) == ("failed", "is-crit", True, True, False), code
        assert 'class="nm-status is-err"' in view["html"] and 'class="nm-cells hot"' in view["html"], code
    assert unescape(failures["unknownCode"]["line"]) == unescape(failures["httpWithoutStatus"]["line"]) == UPDATE_FAILURE_TEXTS["error"]
    assert failures["tooLargeAnnounced"]["size"] == "0.0 of 600.0 MB" and failures["tooLargeAnnounced"]["lit"] == 0
    assert failures["rejectedNoVersion"]["size"] == ""
    assert unescape(failures["rejectedNoVersion"]["line"]) == UPDATE_FAILURE_TEXTS["rejected"]


@ui_only
def test_the_failure_words_are_the_catalogs_when_they_are_drawn(checks: dict[str, Any]) -> None:
    """A text changed in the catalog after the page loaded shows at the next draw: nothing holds a copy taken at load."""
    assert update_run(checks)["drawnAtDrawTime"] == "Changed after the page loaded."


@ui_only
def test_the_sizes_are_written_in_the_locale(checks: dict[str, Any]) -> None:
    """A French page (fr-FR) whose catalog has no update text yet: the figures in French numbers inside English's words,
    which are marked lang="en" (so a theme's capitals follow English's rules)."""
    fr = checks["updateFrench"]
    assert fr["size"] == '<span lang="en">4,2 of 21,0 MB</span>'
    assert fr["text"] == "4,2 of 21,0 MB"
    assert fr["soFar"] == '<span lang="en">4,2 MB so far</span>'
    assert fr["step"].count('<span lang="en">') == 3
    assert fr["timeout"] == '<span lang="en">The download stopped: nothing arrived for 60 seconds.</span>'
    assert fr["tooLarge"] == '<span lang="en">The download stopped: the installer is larger than 512 MB.</span>'


@ui_only
def test_only_the_release_on_offer_shows_its_run(checks: dict[str, Any]) -> None:
    """A run, or a failure, of another version, no release on offer, no run, or a monitor with no status route: nothing,
    and the line under the buttons as it is today. A failure whose link named no version is the offer's."""
    offer = update_run(checks)["offer"]
    assert offer == {name: NOTHING for name in ("otherVersion", "otherVersionFailed", "nothingOnOffer", "idle", "none")}
    assert update_run(checks)["failures"]["rejectedNoVersion"]["kind"] == "failed"


@ui_only
def test_a_page_opened_mid_update_shows_it_at_the_first_ask(checks: dict[str, Any]) -> None:
    """Settings opened while a run goes (a tray click, another window): the first ask, made as the page is built, shows it,
    the line under the buttons gives way to it and Install update is busy."""
    opened = update_run(checks)["flow"]["opened"]
    assert opened["calls"] == ["GET /api/update/status"]
    assert opened["shown"] and opened["cls"] == "nm-uprog" and opened["steps"] == ["now", "next", "next"]
    assert opened["size"] == "4.2 of 21.0 MB" and opened["said"] == "Downloading version 2.4.0."
    assert opened["lineHidden"] and opened["line"] == "" and opened["busy"] == "true" and opened["quick"] == 1


@ui_only
def test_the_status_is_asked_only_while_it_matters(checks: dict[str, Any]) -> None:
    """Every half second while a run shows; every 3 s from the page's tick while Settings is open with a release on offer;
    none while the window is hidden, on another page or with nothing on offer; none after a 404 or a 501 (an older monitor,
    the dev server without its demo); and a page change stops the quick asks."""
    flow = update_run(checks)["flow"]
    asks = {name: len(flow[name]["calls"]) for name in flow if "calls" in flow[name]}
    assert asks["again"] == 2 and flow["again"]["quick"] == 1
    assert asks["idle"] == 3 and flow["idle"]["quick"] == 0
    assert not flow["idle"]["shown"] and flow["idle"]["line"] == "Version 2.4.0 is available." and not flow["idle"]["lineHidden"]
    assert flow["idle"]["busy"] is None                                   # no run: today's line, Install free
    assert asks["tickEarly"] == 3 and asks["tickDue"] == 4
    assert asks["hidden"] == asks["otherPage"] == asks["noOffer"] == 4
    assert asks["notFound"] == 5                                          # the 404 is asked once, then nothing more
    assert flow["notAvailable"]["calls"] == ["GET /api/update/status"] and flow["notAvailable"]["none"] is True
    assert flow["pageChange"] == {"quickBefore": 1, "calledOff": True, "timer": 0, "calls": ["GET /api/update/status"]}


@ui_only
def test_an_older_failure_does_not_show_after_a_click(checks: dict[str, Any]) -> None:
    """For 3 s after Install update started a run, a record with no run in it yet (none, idle, or a failure older than the
    click) shows the run's start; a failure younger than the click is the new run's; a running record is shown as it is,
    however old (a run the tray started, which the click's request joined)."""
    after = update_run(checks)["afterClick"]
    for name in ("nothingYet", "idle", "olderFailure"):
        view = after[name]
        assert (view["kind"], view["size"], view["pending"], view["busy"]) == ("start", "Connecting to GitHub…", True, True), name
        assert view["steps"] == ["now", "next", "next"] and view["line"] == "Downloading version 2.4.0.", name
    assert after["newFailure"]["kind"] == "failed"
    assert after["olderRun"]["kind"] == "run" and after["olderRun"]["size"] == "4.2 of 21.0 MB"
    assert after["graceOver"] == NOTHING
    click = update_run(checks)["click"]
    assert click["started"]["calls"] == ["POST /api/update/apply", "GET /api/update/status"]
    assert click["started"]["size"] == "Connecting to GitHub…" and click["started"]["busy"] == "true"
    assert click["started"]["lineHidden"] and click["started"]["quick"] == 1
    assert click["running"]["size"] == "4.2 of 21.0 MB"


@ui_only
def test_a_lost_link_and_a_restart_read_differently(checks: dict[str, Any]) -> None:
    """Three asks with no answer: while the update downloads or is checked, the dashboard lost its link (the crit look, the
    step it was on failed, asked again every 3 s); once the monitor has begun to close for the installer, it is closing (no
    version claimed: if the installer cannot run, the old version starts again) and nothing more is asked. Never with no run:
    a monitor that quits keeps today's line (the header says the link is lost)."""
    lost = update_run(checks)["lost"]
    down = lost["downloading"]
    assert (down["kind"], unescape(down["line"]), down["tone"], down["err"]) == ("lost", UPDATE_LOST, "is-crit", True)
    assert down["steps"] == ["failed", "next", "next"] and down["size"] == "4.2 of 21.0 MB" and down["hot"]
    assert lost["checking"]["kind"] == "lost" and lost["checking"]["steps"] == ["done", "failed", "next"]
    for name in ("restarting", "starting"):
        view = lost[name]
        assert (view["kind"], unescape(view["line"]), view["tone"], view["err"]) == ("gone", UPDATE_GONE, "", False), name
        assert view["steps"] == ["done", "done", "now"] and view["busy"] and view["lit"] == 32, name
    assert lost["twoMisses"]["kind"] == "run"
    assert lost["noRun"] == NOTHING
    assert lost["afterClickNoAnswer"]["kind"] == "lost" and lost["afterClickNoAnswer"]["size"] == ""
    paths = update_run(checks)["linkLost"]
    down, gone, idle = paths["downloading"], paths["restarting"], paths["idle"]
    assert [s["said"] for s in down["seen"]] == ["Downloading version 2.4.0."] * 2 + [UPDATE_LOST]
    assert down["seen"][2]["cls"] == "nm-uprog is-crit" and down["seen"][2]["busy"] is None and down["seen"][2]["quick"] == 0
    assert len(down["after"]["calls"]) == len(down["seen"][2]["calls"]) + 1          # still asked, every 3 s
    assert gone["seen"][2]["said"] == UPDATE_GONE and gone["seen"][2]["busy"] == "true"
    assert gone["after"]["calls"] == gone["seen"][2]["calls"]                        # nothing asked once it has gone
    assert not any(s["shown"] for s in idle["seen"]) and not idle["after"]["shown"]
    assert idle["after"]["line"] == "Version 2.4.0 is available."


@ui_only
def test_a_failure_lasts_until_the_next_click_on_an_update_button(checks: dict[str, Any]) -> None:
    """Owner question 4 on #58: a failure stays while its release is on offer, until a click on Check for updates now,
    Install update or Skip this version gives the line under the buttons a new answer, which then shows; or until a new run
    starts. An answer given while a run goes stays under the run's block."""
    run = update_run(checks)
    assert run["lasts"]["sameAnswer"]["kind"] == "failed"
    assert run["lasts"]["newAnswer"] == run["lasts"]["neverShown"] == NOTHING
    click = run["click"]
    old = click["oldFailure"]
    assert old["shown"] and old["cls"] == "nm-uprog is-crit" and old["lineHidden"] and old["busy"] is None
    assert unescape(old["said"]) == UPDATE_FAILURE_TEXTS["network"]
    checked = click["checked"]
    assert not checked["shown"] and checked["line"] == "An update is available." and not checked["lineHidden"]
    failed = click["failed"]
    assert failed["shown"] and failed["cls"] == "nm-uprog is-crit" and unescape(failed["said"]) == UPDATE_FAILURE_TEXTS["cut_short"]
    assert failed["line"] == "An update is available." and not failed["lineHidden"] and failed["busy"] is None
    assert not click["skipped"]["shown"] and click["skipped"]["line"] == "This version will be skipped."
    assert click["trayRun"]["shown"] and click["trayRun"]["steps"] == ["now", "next", "next"]
    assert click["trayFailed"]["shown"] and click["trayFailed"]["steps"] == ["done", "done", "failed"]
    assert unescape(click["trayFailed"]["said"]) == UPDATE_FAILURE_TEXTS["helper"] and click["trayFailed"]["lineHidden"]


@ui_only
def test_a_failure_a_click_ended_stays_ended_and_a_later_runs_failure_shows(checks: dict[str, Any]) -> None:
    """Question 4: the next answer of the run whose failure a click ended does not bring it back (its age_s has grown with
    the clock: the same run); a run that began later, which the tray started and which failed before an ask saw it going,
    shows its failure, over the line under the buttons as a first failure does, and a click ends that one in turn."""
    again = update_run(checks)["again"]
    assert again["shown"]["shown"] and unescape(again["shown"]["said"]) == UPDATE_FAILURE_TEXTS["network"]
    for name in ("checked", "sameRun", "laterRunEnded"):
        view = again[name]
        assert not view["shown"] and view["line"] == "An update is available." and not view["lineHidden"], name
    later = again["laterRun"]
    assert later["shown"] and later["cls"] == "nm-uprog is-crit" and later["lineHidden"]
    assert unescape(later["said"]) == UPDATE_FAILURE_TEXTS["http"].replace("{status}", "404")
    assert len(again["laterRunEnded"]["calls"]) == 6                  # each step above was asked, none was skipped


@ui_only
def test_a_failure_with_no_version_belongs_to_the_release_it_was_shown_under(checks: dict[str, Any]) -> None:
    """A link that was not this project's leaves a failure with no version: it is the release's that was on offer when it
    showed, so a newer release found later (the six-hourly check, no click) is not drawn under it; its line shows instead."""
    no_version = update_run(checks)["noVersion"]
    assert no_version["underItsOffer"]["kind"] == "failed" and no_version["underANewerOne"] == NOTHING
    assert no_version["named"]["kind"] == "failed"                    # a failure that names its version needs no such mark
    newer = update_run(checks)["newerOffer"]
    assert newer["shown"]["shown"] and unescape(newer["shown"]["said"]) == UPDATE_FAILURE_TEXTS["rejected"]
    assert not newer["after"]["shown"] and newer["after"]["line"] == "Version 2.5.0 is available." and not newer["after"]["lineHidden"]


@ui_only
def test_the_patches_keep_the_bar_and_the_line_in_step(checks: dict[str, Any]) -> None:
    """The block is drawn once at the start of a run and patched after: the bar's figure for a screen reader comes with the
    size, the pending mark goes (the theme's placeholder would stay over a filling bar), the bar turns hot and the line takes
    the error tone at a failure, and a new run clears both."""
    patched = update_run(checks)["patched"]
    assert patched["draws"] == 1
    assert patched["connecting"] == {"now": None, "text": "Connecting to GitHub…", "pending": True, "cells": "nm-cells", "line": "nm-status"}
    assert patched["sized"] == {"now": "20", "text": "4.2 of 21.0 MB", "pending": False, "cells": "nm-cells", "line": "nm-status"}
    assert patched["failed"] == {"now": "20", "text": "4.2 of 21.0 MB", "pending": False, "cells": "nm-cells hot", "line": "nm-status is-err"}
    assert patched["newRun"] == {"now": "4", "text": "1.0 of 21.0 MB", "pending": False, "cells": "nm-cells", "line": "nm-status"}


@ui_only
def test_the_link_back_shows_the_run_again_and_misses_count_only_in_a_row(checks: dict[str, Any]) -> None:
    """After three asks with no answer the run reads as lost; the first answer after shows it again, asked every half second.
    Asks that went unanswered one at a time, each followed by an answer, never add up to a lost link."""
    back = update_run(checks)["linkBack"]
    assert unescape(back["lost"]["said"]) == UPDATE_LOST
    for name in ("back", "spread"):
        view = back[name]
        assert view["shown"] and view["cls"] == "nm-uprog" and view["said"] == "Downloading version 2.4.0.", name
        assert view["steps"] == ["now", "next", "next"] and view["quick"] == 1 and view["busy"] == "true", name
    assert back["back"]["size"] == "5.1 of 21.0 MB"


@ui_only
def test_install_update_over_a_failure_keeps_the_block_while_the_click_is_answered(checks: dict[str, Any]) -> None:
    """Install update again over a failure that shows: the block becomes the new run's start at once and stays while the
    monitor answers (the card neither drops to the one line nor grows back), Install busy and its Installing… line hidden.
    Started: the start until the record has the run, and a failure of the new run shows at its first answer, even a second
    after the last one began. Any other answer shows in the block's place; an answer that never comes leaves the line once
    the click's grace is over."""
    rerun = update_run(checks)["rerun"]
    for name, case in rerun.items():
        assert case["shown"]["shown"] and unescape(case["shown"]["said"]) == UPDATE_FAILURE_TEXTS["network"], name
        asking = case["asking"]
        assert asking["calls"] == ["POST /api/update/apply"], name
        assert asking["shown"] and asking["steps"] == ["now", "next", "next"] and asking["size"] == "Connecting to GitHub…", name
        assert asking["said"] == "Downloading version 2.4.0." and asking["lineHidden"] and asking["busy"] == "true", name
    again = rerun["failedAgain"]["answered"]
    assert again["calls"] == ["POST /api/update/apply", "GET /api/update/status"]
    assert again["shown"] and unescape(again["said"]) == UPDATE_FAILURE_TEXTS["cut_short"] and again["lineHidden"]
    started = rerun["started"]["answered"]
    assert started["shown"] and started["size"] == "Connecting to GitHub…" and started["lineHidden"] and started["busy"] == "true"
    for name, line in (("browser", "Opened the release page in your browser."), ("linkLost", "Update failed: the dashboard lost its link")):
        answered = rerun[name]["answered"]
        assert not answered["shown"] and answered["line"] == line and not answered["lineHidden"] and answered["busy"] is None, name
    assert rerun["hung"]["answered"]["shown"] and rerun["hung"]["answered"]["lineHidden"]
    after = rerun["hung"]["afterGrace"]
    assert not after["shown"] and after["line"] == "Installing…" and not after["lineHidden"] and after["busy"] is None


@ui_only
def test_a_second_click_on_install_sends_nothing_while_a_run_goes(checks: dict[str, Any]) -> None:
    """Install update is aria-disabled while a run goes (it keeps the keyboard focus) and settingsClick sends nothing for it;
    the other buttons still work, and their answers show under the run."""
    click = update_run(checks)["click"]
    assert click["secondClick"]["calls"] == [] and click["secondClick"]["busy"] == "true"
    during = click["checkDuringRun"]
    assert during["calls"] == ["POST /api/update/check"]
    assert during["shown"] and during["line"] == "An update is available." and not during["lineHidden"]
    assert re.search(r'action === "install-update"\) \{\s*if \(!updateRunBusy\(\)\) \{[^\n]*\n\s*installUpdateFromSettings\(\);',
                     function_body(APP_CODE, "settingsClick"))
    assert "\n    installUpdate();\n" in function_body(APP_CODE, "installUpdateFromSettings")


@ui_only
def test_the_hold_on_the_check_step_is_off_and_works_when_set(checks: dict[str, Any]) -> None:
    """Owner question 1: no hold today. Set (here to 1 s, through progressView's own argument), the Check chip stays the current
    step for that long after the record passed it, and no longer."""
    hold = update_run(checks)["hold"]
    assert hold["constant"] == 0
    assert hold["byDefault"]["steps"] == ["done", "done", "now"]           # the Check chip shows done at once
    assert hold["holding"]["steps"] == ["done", "now", "next"]             # a hold of 1 s, 0.3 s after the check passed
    assert unescape(hold["holding"]["line"]) == "Checking the download against the release's SHA-256."
    assert hold["heldLongEnough"]["steps"] == ["done", "done", "now"]


@ui_only
def test_the_bar_is_redrawn_when_part_of_a_cell_fills(checks: dict[str, Any]) -> None:
    """The block is drawn once and then patched part by part: the same answer touches nothing, a block that moves only the
    part of a cell redraws the cells (a 32nd of 21 MB can take many seconds on a slow line), a new step redraws the chips."""
    patches = update_run(checks)["patches"]
    assert patches["first"] == patches["same"] == {"box": 1, "steps": 0, "cells": 0, "size": 0, "line": 0}
    assert patches["partMoved"] == {"box": 1, "steps": 0, "cells": 1, "size": 0, "line": 0}
    assert patches["cellsHtml"].count('class="on"') == 6 and "--f:53.9%" in patches["cellsHtml"]
    assert patches["nextStep"] == {"box": 1, "steps": 1, "cells": 2, "size": 1, "line": 1}
    assert patches["hiddenAfter"] and patches["emptyAfter"]


@ui_only
def test_the_banner_and_the_update_line_are_untouched_by_a_run(checks: dict[str, Any]) -> None:
    """The banner on the other pages and the line under Settings' buttons say what they said before #58, run or no run
    (showing the run in the banner is #114)."""
    untouched = update_run(checks)["untouched"]
    assert untouched["busy"] is True
    assert untouched["with"] == untouched["without"]
    assert "Downloading and checking the update" in untouched["with"]["banner"]


@ui_only
def test_the_block_holds_no_figure_night_city_glitches(checks: dict[str, Any]) -> None:
    """Night City glitches .nm-big, .nm-stat-v and .nm-cmp-n whenever their text changes, which would be twice a second
    here; the block is the guard card's parts only, and the page sets no motion mark of its own."""
    run = update_run(checks)
    views = [run["stall"]]
    for group in ("bytes", "unknown", "steps", "failedIn", "failures", "afterClick", "lost", "hold", "lasts"):
        views += [view for view in run[group].values() if isinstance(view, dict) and "html" in view]
    markup = [view["html"] for view in views if view["html"]]
    assert len(markup) > 40
    allowed = {"nm-verdict", "nm-chip", "nm-chip is-ok", "nm-chip solid is-warn", "nm-chip solid is-crit", "nm-cells",
               "nm-cells hot", "nm-story", "nm-status", "nm-status is-err", "on", "part"}
    for html in markup:
        assert not re.search(r"nm-big|nm-stat-v|nm-cmp-n|data-fx", html), html[:200]
        assert set(re.findall(r'class="([^"]+)"', html)) <= allowed, html[:200]


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_every_theme_draws_the_update_block_with_what_it_has(theme_id: str) -> None:
    """Each sheet lays the block out and lets its chips wrap (a long word in another language fits a narrow window), tells
    the three steps apart (Gates by the chips' own tones, which gates.js reads from data-tone), dims the next step as it dims
    a disabled control (at that control's opacity; in Night City with a button's ink on a button's frame, dimmed once), keeps
    the sizes line a line high when it is empty (a failure before the first byte would shrink the block), and colours a
    stalled and a failed bar its own way. No rule of the block moves anything."""
    css = THEME_CSS[theme_id]
    assert re.search(r"\.nm-uprog \{ display: flex; flex-direction: column;", css)
    assert re.search(r"\.nm-uprog \.nm-verdict \{ flex-wrap: wrap;", css)
    rules = re.findall(r"([^{}\n]*\.nm-uprog[^{}]*)\{([^}]*)\}", css)
    assert rules and not any(re.search(r"\b(?:animation|transition)\b", body) for _, body in rules)
    disabled = re.search(r'\.nm-btn\[aria-disabled="true"\][^{]*\{[^}]*opacity: (\.\d+);', css).group(1)
    upcoming = re.search(r'\.nm-uprog \.nm-chip\[data-step="next"\] \{([^}]*)\}', css).group(1)
    assert f"opacity: {disabled};" in upcoming                          # as a disabled control is
    assert ".nm-uprog .nm-story:empty { min-height: 1lh; }" in css
    if theme_id == "gates":
        assert ".nm-uprog.is-warn { --g-fill: var(--warn); }" in css and ".nm-uprog.is-crit { --g-fill: var(--alert); }" in css
        assert re.search(r'\.nm-chip\[data-g-tone="acc"\] \{[^}]*color: var\(--text\)', css)
        assert re.search(r'\.nm-chip\[data-g-tone="neutral"\] \{[^}]*color: var\(--text-dim\)', css)
        assert "color: var(--text);" in upcoming                         # a button's text, at a disabled button's opacity
    else:
        assert re.search(r'\.nm-uprog \.nm-chip\[data-step="done"\] \{[^}]+\}', css)
    if theme_id == "night_city":                                         # a button as a disabled one is drawn: dimmed once
        assert upcoming.strip() == "--oc: var(--nc-line-a); color: var(--nc-ink-2); opacity: .45;"
        assert re.search(r"\n\.nm-btn, [^{]*\{[^}]*color: var\(--nc-ink-2\);\s*--oc: var\(--nc-line-a\);", css)
    if theme_id == "nostromo":
        assert ".nm-cells.hot i.on { background: var(--hot); }" in css and ".nm-cells.hot i.part {" in css
        assert '.nm-err::before { content: "! ";' in css and '.nm-uprog .nm-chip.is-crit::before { content: "! "; }' in css
    if theme_id == "night_city":
        for tone in ("is-warn", "is-crit"):
            assert f".nm-uprog.{tone} .nm-cells i.on" in css and f".nm-uprog.{tone} .nm-cells i.part" in css


# ── The theme picker ──────────────────────────────────────────────────────────────

@ui_only
def test_the_theme_picker_shows_one_swatch_per_accent(checks: dict[str, Any]) -> None:
    swatches = checks["swatches"]
    for key in ("gatesDark", "gatesLight", "gatesOther"):
        assert len(swatches[key]) == 7 == len(set(swatches[key])), key               # Gates has seven accents, not fourteen swatches
    assert len(swatches["nostromo"]) == 6 and len(swatches["nightCity"]) == 4
    assert swatches["gatesDark"] != swatches["gatesLight"]                           # the mode of the page picks the colours shown


NOSTROMO_PHOSPHORS = ["amber", "green", "white", "red", "blue", "purple"]
NOSTROMO_NAMES = ["RAY TUBE", "TERMINAL", "STATIC", "ALERT", "CRYO", "PLASMA"]


@ui_only
def test_the_colour_buttons_say_each_colours_name(checks: dict[str, Any]) -> None:
    labels = checks["accentLabels"]
    assert labels["nostromo"] == NOSTROMO_NAMES
    assert labels["nightCity"] == ["BLACKWALL", "RELIC", "EDGERUN", "MOX"]                   # its names are its ids
    assert labels["gates"] == ["SYSTEM", "BLUE", "TEAL", "PURPLE", "ROSE", "ORANGE", "GREEN"]  # a dark and a light variant each
    assert labels["retired"] == "CYAN" and labels["noTheme"] == "AMBER"


@ui_only
def test_the_sidebar_names_nostromos_phosphors_and_keeps_the_short_labels_elsewhere(checks: dict[str, Any]) -> None:
    sidebar = checks["sidebar"]
    nostromo = Markup(sidebar["nostromo"]["html"])
    buttons = nostromo.find("button")
    assert sidebar["nostromo"]["label"] == "Phosphor"                                 # #98: Nostromo sets it in capitals
    assert nostromo.text == NOSTROMO_NAMES
    assert [b["data-accent"] for b in buttons] == NOSTROMO_PHOSPHORS == [b["title"] for b in buttons]   # the tooltip says the colour
    assert [b["aria-pressed"] for b in buttons] == ["false", "false", "false", "false", "true", "false"]
    assert sidebar["nightCity"]["label"] == "Accent" and Markup(sidebar["nightCity"]["html"]).text == ["BLA", "REL", "EDG", "MOX"]
    assert Markup(sidebar["gates"]["html"]).text == ["SYS", "BLU", "TEA", "PUR", "ROS", "ORG", "GRN"]
    assert all("lang" not in b for key in sidebar for b in Markup(sidebar[key]["html"]).find("button"))     # English: as before


@ui_only
def test_the_sidebar_colour_buttons_mark_englishs_words_on_another_languages_page(checks: dict[str, Any]) -> None:
    """#98's review (critic 8's words): on a French page a colour button whose tooltip, or in Gates whose three letters, are
    English's carries lang="en" on the button; one whose words are all French carries none. Nostromo's names are its own
    (English names, cased by English's rule), so its tooltip alone decides; Night City's ids are names, with no language."""
    french = checks["sidebarFrench"]
    nostromo = Markup(french["nostromo"]["html"]).find("button")
    assert [(b["data-accent"], b["title"], b.get("lang")) for b in nostromo] == [
        ("amber", "amber", "en"), ("green", "green", "en"), ("white", "white", "en"), ("red", "red", "en"), ("blue", "bleu", None),
        ("purple", "purple", "en")]
    gates = Markup(french["gates"]["html"])
    assert gates.text == ["SYS", "BLE", "TEA", "PUR", "ROS", "ORG", "GRN"]
    assert [(b["title"], b.get("lang")) for b in gates.find("button")] == [
        ("system", "en"), ("bleu", None), ("sarcelle", "en"), ("purple", "en"), ("rose", "en"), ("orange", "en"), ("green", "en")]
    assert all(b.get("lang") is None for b in Markup(french["nightCity"]["html"]).find("button"))


@ui_only
def test_the_settings_colour_buttons_use_the_names_and_keep_the_colour_as_a_tooltip(checks: dict[str, Any]) -> None:
    picker = checks["settingsAccents"]
    nostromo = Markup(picker["nostromo"]["accents"])
    assert nostromo.text == NOSTROMO_NAMES
    assert [b["title"] for b in nostromo.find("button")] == NOSTROMO_PHOSPHORS
    assert [b["data-accent"] for b in nostromo.find("button") if "solid" in (b["class"] or "")] == ["blue"]
    assert picker["nostromo"]["meta"] == "NOSTROMO · CRYO"
    for key, words in (("nightCity", ["BLACKWALL", "RELIC", "EDGERUN", "MOX"]),
                       ("gates", ["SYSTEM", "BLUE", "TEAL", "PURPLE", "ROSE", "ORANGE", "GREEN"])):
        page = Markup(picker[key]["accents"])
        assert page.text == words, key
        assert all("title" not in b for b in page.find("button")), key                  # the name is the colour: no tooltip


@ui_only
def test_saving_a_colour_names_it_and_sends_its_id(checks: dict[str, Any]) -> None:
    saves = checks["colourSaves"]
    assert saves["nostromo"] == {"saving": "SAVING CRYO…", "saved": "SAVED CRYO",
                                 "posts": ['/api/settings {"theme_variant":"blue"}']}
    assert saves["gates"] == {"saving": "SAVING TEAL…", "saved": "SAVED TEAL",
                              "posts": ['/api/settings {"theme_variant":"teal"}']}
    assert saves["sidebar"] == {"saving": "SAVING PLASMA…", "saved": "SAVED PLASMA",
                                "posts": ['/api/settings {"theme_variant":"purple"}']}


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
    assert re.search(r'\.nm-card\[data-card="guard"\] \{ --g-ico: "\\[0-9A-F]{4}"; \}', THEME_CSS["gates"])
    for theme_id in ("nostromo", "night_city"):
        assert "--g-ico" not in THEME_CSS[theme_id]


def test_gates_colours_a_chip_by_its_tone_attribute_and_the_guard_has_no_override() -> None:
    """gates.js copies a chip's data-tone to data-g-tone; the colours are keyed by that, for the ARMED and HOLDING chips too
    (they carry ok and alert), so the guard block needs no rule of its own to win over a neutral tone."""
    css = THEME_CSS["gates"]
    for tone, colour in (("ok", "--ok"), ("alert", "--alert")):
        assert re.search(rf'\.nm-chip\[data-g-tone="{tone}"\][^{{]*\{{ color: var\({colour}\)', css), tone
    assert "#guard-live .nm-chip" not in css


def test_gates_picks_its_card_icons_by_data_card_and_never_by_an_english_title() -> None:
    """The English title of a card is for people; the icon is picked by the card's key (data-card), so a translated title keeps it."""
    css = THEME_CSS["gates"]
    assert "[aria-label=" not in css
    keys = re.findall(r'\[data-card="([^"]+)"\]', css)
    assert keys and len(keys) == len(set(keys)), keys
    assert re.search(r'\.nm-card\[data-card="data-plan"\] \{ --g-ico: "\\[0-9A-F]{4}"; \}', css)      # the Data and plan card has its icon


def test_the_night_city_refresh_pairs_a_card_by_its_key_not_its_title() -> None:
    body = function_body(read("themes/night_city.js"), "ownerKey")
    assert 'getAttribute("data-card")' in body and "aria-label" not in body


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


def resume_busy(html: str) -> bool:
    """A Resume now on its way: aria-disabled, not disabled, so the keyboard focus stays on it."""
    return bool(re.search(r'<button[^>]*data-action="guard-resume"[^>]* aria-disabled="true"', html)) and not resume_disabled(html)


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
def test_the_card_lists_the_conversations_the_stop_hook_watches(checks: dict[str, Any]) -> None:
    """#41: a conversation that waits for its workflow is watched, and woken when the guard holds. The card says which
    are watched, since when and for what work, but not while the guard is off: the watches end with it."""
    live, clock = checks["guard"]["live"], checks["guard"]["clock"]
    assert kv(live["armedWatching"]) == [("WATCHING", "1 CONVERSATION"),
                                          ("3f9a1c2e", "SINCE " + clock["watched"] + " · 1 WORKFLOW, 2 AGENTS")]
    assert kv(live["offWatching"]) == []
    many = kv(live["holdingWatchingMany"])
    at = [label for label, _value in many].index("WATCHING")
    assert many[at] == ("WATCHING", "7 CONVERSATIONS") and many[at + 1] == ("5d2b7e40", "SINCE " + clock["watched"] + " · 1 AGENT")
    assert many[at + 6] == ("AND 2 MORE", "")
    odd = kv(live["watchingOdd"])                                            # what came back is not trusted
    assert odd[0] == ("WATCHING", "2 CONVERSATIONS") and odd[1][0] == "unknown session" and " · " not in odd[1][1]
    assert odd[2][0] == "abc" and odd[2][1].endswith(" · 1 TEAMMATE")


@ui_only
def test_the_card_counts_conversations_and_agents_apart_and_names_a_conversation_not_told(checks: dict[str, Any]) -> None:
    """#41: the card said two sessions were told when only a workflow's agents had heard, and its conversation had not."""
    rows = kv(checks["guard"]["live"]["holdingAgents"])
    assert rows[2] == ("TOLD THIS EPISODE", "1 CONVERSATION, 3 AGENTS")
    assert rows[3][0] == "3f9a1c2e" and rows[3][1].endswith(" · 3 AGENTS ONLY")
    assert rows[4][0] == "5d2b7e41" and rows[4][1].endswith(" · CONVERSATION")
    assert rows[5] == ("NOT TOLD", "1 CONVERSATION: ITS AGENTS WERE")


@ui_only
def test_while_holding_it_names_what_is_over_when_it_ends_who_was_told_and_who_waits(checks: dict[str, Any]) -> None:
    rows, clock = kv(checks["guard"]["live"]["holding"]), checks["guard"]["clock"]
    assert rows[:2] == [("SESSION", "96% OF A 95% CAP"), ("FABLE WEEKLY", "91.5% OF A 90% CAP")]      # percent against cap, as the limit is named
    assert rows[2] == ("RESUMES", f"{clock['resumes']} · IN 3 H 27 M")                                 # local time and a countdown
    assert rows[3] == ("TOLD THIS EPISODE", "2 CONVERSATIONS")                            # a list from before #41 counted conversations
    assert rows[4] == ("3f9a1c2e", clock["told"] + " · CONVERSATION") and rows[5][0] == "3f9a1c2e" and rows[5][1] != rows[4][1]
    assert rows[6] == ("WAITING", "1 SESSION") and rows[7] == ("atlas-web · 77b0aa10", f"SINCE {clock['since']}")   # the folder, and since when
    assert 'title="C:\\Demo\\atlas-web"' in checks["guard"]["live"]["holding"]                          # the whole path is one hover away
    assert len(rows) == 8


@ui_only
def test_resume_now_is_enabled_only_while_something_waits(checks: dict[str, Any]) -> None:
    live = checks["guard"]["live"]
    for state in ("off", "armed", "armedThree", "armedNoCaps", "holdingNobody", "holdingNoResetTime", "holdingAMomentLeft", "pending",
                  "beforeTheFirstCycle"):
        assert resume_disabled(live[state]), state
    for state in ("holding", "holdingMany", "armedStillWaiting", "holdingHostile"):
        assert not resume_disabled(live[state]), state                                   # a session that is still waiting can be released
    assert 'data-action="guard-resume"' in live["holding"] and ">Resume now</button>" in live["holding"]
    assert ">Resuming…</button>" in live["holdingBusy"] and resume_busy(live["holdingBusy"])  # a click on its way keeps the focus


@ui_only
def test_the_status_says_when_no_session_can_hear_the_guard(checks: dict[str, Any]) -> None:
    live = checks["guard"]["live"]
    said = "The hooks are not all installed, so sessions may not be told: press Install below."
    assert said in live["armedNoHooks"] and said in live["holdingPartHooked"]
    assert said not in live["armedHooked"] and said not in live["armed"]                # installed, or setup not known yet


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
    assert rows[labels.index("TOLD THIS EPISODE")][1] == "7 CONVERSATIONS" and rows[labels.index("WAITING")][1] == "7 SESSIONS"
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


def buttons(html: str) -> dict[str, str]:
    """Install and Remove: "disabled" (it cannot work here), "busy" (aria-disabled while a click is on its way, so the
    keyboard focus stays) or "" (it can be pressed)."""
    def state(name: str) -> str:
        tag = re.search(rf'<button[^>]*data-action="guard-{name}"[^>]*>', html)
        attrs = tag.group(0) if tag else ""
        return "disabled" if " disabled" in attrs else "busy" if 'aria-disabled="true"' in attrs else ""

    return {name: state(name) for name in ("install", "remove")}


@ui_only
def test_install_and_remove_are_there_when_setup_can_run_and_the_cost_is_always_said(checks: dict[str, Any]) -> None:
    setup = checks["guard"]["setup"]
    for name in ("missing", "installed", "partial", "tooOld", "versionUnknown", "unreadable"):
        assert buttons(setup[name]) == {"install": "", "remove": ""}, name
    for name in ("pending", "fromSource"):                                                    # no answer yet, or no setup here
        assert buttons(setup[name]) == {"install": "disabled", "remove": "disabled"}, name
    assert buttons(setup["noClaude"]) == {"install": "disabled", "remove": ""}                 # Remove needs no Claude Code
    for name in ("installing", "removing"):                                                   # one click on its way
        assert buttons(setup[name]) == {"install": "busy", "remove": "busy"}, name
    assert ('(<span class="nm-path" lang="en" translate="no">C:\\Users\\t\\AppData\\Roaming\\npm\\claude.CMD</span>) is older than 2.1.139'
            in setup["tooOldAt"])
    assert 'Update it with <span class="nm-path" lang="en" translate="no">claude update</span>' in setup["tooOld"]
    assert ('Update it with <span class="nm-path" lang="en" translate="no">&amp; &#39;C:\\Users\\t\\.local\\bin\\claude.exe&#39; update</span>'
            in setup["tooOldOnNoPath"])   # #43
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
        assert buttons(setup[name])["install"] == "disabled", name
    assert "Setup is not available" not in setup["missing"]


@ui_only
def test_a_click_shows_what_changed_the_backup_the_warnings_and_the_errors_each_in_its_place(checks: dict[str, Any]) -> None:
    setup = checks["guard"]["setup"]
    done = setup["installed_ok"]
    assert '<div class="nm-callout" role="status"><b>Installed.</b> Added the SessionStart hook. Registered the claude-usage MCP tool.<br>' in done
    assert 'Backup: <span class="nm-path" lang="en" translate="no">C:\\Users\\t\\.claude\\settings.json.usage-monitor-backup-20261028-180509</span>' in done
    assert '<div class="nm-callout is-warn" role="note">This card checks only the claude command. Claude Code in VS Code has its own version: in VS Code, check under Extensions that it is 2.1.139 or newer.</div>' in done
    assert "<b>Removed.</b> Removed the PostToolUse hook." in setup["removed_ok"]
    assert "<b>Nothing to change.</b> The three hooks were already in place." in setup["nothing"]
    refused = setup["refused"]
    assert '<p class="nm-error" role="alert">Claude Code 2.1.94 is older than 2.1.139' in refused and "nm-callout" not in refused   # no empty box above it
    by_hand = setup["byHand"]
    assert "To do it by hand, run: <span class=\"nm-path\" lang=\"en\" translate=\"no\">claude mcp add --transport stdio --scope user claude-usage" in by_hand
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
    ("capBadInput", "guard_session_cap", "", "Enter a whole number from 1 to 100"),  # "9e" in the box: not a blank cap
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


# ── The sign-in help and the pages of a PC with no Claude Code (#28) ────────────────

# Someone who uses Claude on the web or in the app has no Claude Code on this PC: no sign-in for the monitor to read the plan's
# limits with, and no transcript for the cost, efficiency and history pages to count. These are the words the page was given.
README_WEB_AND_APP = "https://github.com/Godimas101/claude-usage-monitor#-using-claude-on-the-web-or-in-the-app"
INSTALL_COMMAND = "irm https://claude.ai/install.ps1 | iex"
# Anthropic's installer leaves the PATH alone, so "claude" is not found after it (#42): this runs it from the installer's folder,
# for any user name
START_COMMAND = r'& "$env:USERPROFILE\.local\bin\claude.exe"'
SIGNIN_HEAD = "NO CLAUDE SIGN-IN YET"
SIGNIN_WHY = ("The monitor reads your plan's limits through the sign-in that Claude Code saves on this PC. Signing in to Claude Code "
              "once is enough, even if you use Claude on the web or in the Claude app. Claude Code needs a paid plan such as Pro or Max.")
SIGNIN_STEPS = ["Open PowerShell: press Start and type PowerShell.", "Install Claude Code:", "Start Claude Code:",
                "Come back here and press Check again."]
SIGNIN_PATH_NOTE = "If it warns that a folder is not in your PATH, carry on. The next step does not need it."      # under the second step's command
SIGNIN_SIGN_IN = "Then sign in with your Claude account in the browser that opens."      # the end of the third step, under its command
# "when you use it", not "each time it runs": an open but idle Claude Code lets the sign-in run out (#52)
SIGNIN_RENEWS = "Claude Code renews the sign-in when you use it. If the monitor later says the sign-in has expired, send Claude Code a message."
SIGNIN_EXPIRED = "Claude Code renews the sign-in when you use it. Send it a message, or run claude in PowerShell, then press Check again."
# an HTTP 403: using Claude Code does not fix it, only a new sign-in does (#55)
SIGNIN_REFUSED = "Only a new sign-in fixes this. Run claude in PowerShell, type /login and sign in, then press Check again."
# an older Claude app took the name "claude" on some PCs, so typing it opens the app (Anthropic's install troubleshooting page)
SIGNIN_NOT_FOUND = "If PowerShell says claude is not recognized, or the Claude app opens instead, run this:"
COPY_TITLES = {"install": "Copy the install command", "start": "Copy the command that starts Claude Code"}
CHECK_NONE, CHECK_STALE, CHECK_FAILED = "Still no sign-in found.", "Still no working sign-in.", "Could not check just now. Try again."
NO_ACTIVITY = "No Claude Code activity on this PC"
NO_ACTIVITY_WHY = ("These pages count the work Claude Code records on this PC. Claude on the web and in the Claude app keeps no records "
                   "here, so there is nothing to count. Your limits, pace and alerts work as usual.")
HELP_FUNCTIONS = ("needsSignIn", "codeText", "copyButton", "commandRow", "signInHelp", "checkAgain", "syncCheck", "selectCommand",
                  "copyCommand", "helpAction", "limitsOnly", "noActivityPage")
JARGON = r"\b(token|oauth|credentials?|api|json|http)\b"            # words the page's own copy keeps out of a reader's way


def plain(html: str) -> str:
    """The text of a piece of markup, as a reader sees it (blocks run together: the markup has no whitespace between them)."""
    return unescape(re.sub(r"<[^>]+>", "", html))


def paragraphs(html: str) -> list[str]:
    return [plain(p) for p in re.findall(r"<p>(.*?)</p>", html, re.S)]


def commands(html: str) -> list[str]:
    return [unescape(c) for c in re.findall(r'<code class="nm-path" lang="en" translate="no">(.*?)</code>', html)]


def test_the_help_and_the_no_activity_pages_say_what_they_were_given_to_say() -> None:
    assert js_string("INSTALL_COMMAND") == INSTALL_COMMAND and js_string("SIGNIN_HEAD") == SIGNIN_HEAD
    # the one command in single quotes (it holds double ones), each backslash written twice
    assert "var START_COMMAND = '" + START_COMMAND.replace("\\", "\\\\") + "';" in APP_CODE
    assert "var COMMANDS = { install: INSTALL_COMMAND, start: START_COMMAND };" in APP_CODE
    assert re.search(r"var COPY_TITLES = \{ install: \"([^\"]*)\", start: \"([^\"]*)\" \};", APP_CODE).groups() == tuple(COPY_TITLES.values())
    assert js_string("SIGNIN_WHY") == SIGNIN_WHY and js_string("SIGNIN_RENEWS") == SIGNIN_RENEWS
    assert (js_string("CHECK_NONE"), js_string("CHECK_STALE"), js_string("CHECK_FAILED")) == (CHECK_NONE, CHECK_STALE, CHECK_FAILED)
    assert js_string("NO_ACTIVITY") == NO_ACTIVITY and js_string("NO_ACTIVITY_WHY") == NO_ACTIVITY_WHY
    links = re.search(r"var LINKS = \{(.*?)\};", APP_CODE, re.S).group(1)
    assert f'readme: "{README_WEB_AND_APP}"' in links
    body = function_body(APP_CODE, "signInHelp")
    for step in (SIGNIN_STEPS[0], SIGNIN_STEPS[3], "Claude Code renews the sign-in when you use it. Send it a message, or run "):
        assert f'esc("{step}")' in body, step
    assert f'esc("{SIGNIN_STEPS[1]}") + commandRow("install") + esc("{SIGNIN_PATH_NOTE}")' in body  # the installer's note must not stop anyone
    assert f'esc("{SIGNIN_STEPS[2]}") + commandRow("start") + esc("{SIGNIN_SIGN_IN}")' in body      # no "new window": the PATH does not matter
    one_line = re.sub(r"\s+", " ", body)
    assert ('esc("If PowerShell says ") + codeText("claude") + esc(" is not recognized, or the Claude app opens instead, run this:") + "</p>" + '
            'commandRow("start")') in one_line
    # the heading is all capitals, which is what Gates sets in sentence case ("No Claude sign-in yet"; CLAUDE is one of its names)
    assert SIGNIN_HEAD.isupper() and 'CLAUDE: "Claude"' in THEME_JS["gates"]


def test_the_help_copy_keeps_jargon_out_of_a_readers_way() -> None:
    for text in (SIGNIN_HEAD, SIGNIN_WHY, *SIGNIN_STEPS, SIGNIN_PATH_NOTE, SIGNIN_SIGN_IN, SIGNIN_RENEWS, SIGNIN_EXPIRED, SIGNIN_REFUSED,
                 SIGNIN_NOT_FOUND,
                 *COPY_TITLES.values(), CHECK_NONE, CHECK_STALE, CHECK_FAILED, NO_ACTIVITY, NO_ACTIVITY_WHY, "Copy", "Copied", "Press Ctrl+C",
                 "More in the README"):
        assert not re.search(JARGON, text, re.I), text


def test_the_readme_link_is_one_the_dashboard_will_open_and_the_install_command_is_not_a_link() -> None:
    assert dashboard._external_allowed(README_WEB_AND_APP)                 # github.com: the bridge opens it in the browser
    assert not dashboard._external_allowed("https://claude.ai/install.ps1")
    assert "claude.ai" not in dashboard.EXTERNAL_HOSTS
    body = function_body(APP_CODE, "signInHelp")
    link = re.search(r'<a href="\' \+ esc\(LINKS\.readme\) \+ \'" data-external="1">More in the README</a>', body)
    assert link, "the README link opens the way the page opens its other external links"


def test_the_commands_are_shown_and_copied_and_never_linked_or_fetched() -> None:
    code = strip_comments(APP_CODE)
    assert code.count("claude.ai") == 1                                    # the one constant
    for name in ("INSTALL_COMMAND", "START_COMMAND"):                      # each is only defined and put in COMMANDS
        uses = [line.strip() for line in code.splitlines() if re.search(rf"\b{name}\b", line)]
        assert len(uses) == 2 and uses[0].startswith(f"var {name} = ") and uses[1].startswith("var COMMANDS = {"), (name, uses)
    uses = [line.strip() for line in code.splitlines() if re.search(r"\bCOMMANDS\b", line)]
    assert len(uses) == 4, uses                                            # defined, shown, the key checked, copied
    assert uses[0].startswith("var COMMANDS = {") and "codeText(COMMANDS[which])" in uses[1]
    assert "hasOwnProperty.call(COMMANDS, which)" in uses[2] and "clip.writeText(COMMANDS[which])" in uses[3]
    assert not re.search(r"COMMAND[^\n]*(href|fetch|open|api\()|(href|fetch|open|api\()[^\n]*COMMAND", code)


def test_the_help_buttons_are_wired_like_the_update_banners_and_nothing_is_inline() -> None:
    code = strip_comments(APP_CODE)
    assert 'closest(".nm-help button[data-action]")' in code
    assert 'helpAction(help.getAttribute("data-action"), help.getAttribute("data-command"))' in code
    action = function_body(APP_CODE, "helpAction")
    assert 'action === "check-again"' in action and "checkAgain()" in action
    assert 'action === "copy-command"' in action and "copyCommand(which)" in action
    html = function_body(APP_CODE, "signInHelp") + function_body(APP_CODE, "copyButton") + function_body(APP_CODE, "commandRow")
    assert 'data-action="check-again"' in html and 'data-action="copy-command" data-command="' in html
    assert not re.search(r"""\bon[a-z]+\s*=\s*["']""", html)


def test_check_again_asks_the_monitor_to_look_through_the_pages_own_client_and_one_at_a_time() -> None:
    check = function_body(APP_CODE, "checkAgain")
    assert 'api("POST", "/api/refresh")' in check and "fetch(" not in check
    assert check.index("if (S.check.busy) {") < check.index('api("POST", "/api/refresh")')        # a click while one is on its way sends nothing
    assert "poll(true)" in check                                                                # and the state of the cycle it started is asked for
    # busy, not disabled: a disabled button drops the keyboard focus of whoever pressed it, and the redraw cannot give it back
    html = function_body(APP_CODE, "signInHelp")
    assert "aria-disabled" in html and " disabled" not in html
    assert "syncCheck()" in function_body(APP_CODE, "onState") and "syncCheck()" in function_body(APP_CODE, "tick")


def test_the_help_has_no_motion_of_its_own_and_touches_no_storage() -> None:
    """What moves on these pages is the theme's (the entrance, the pending shimmer, the hover), so it obeys reduce_motion."""
    for name in HELP_FUNCTIONS:
        body = strip_comments(function_body(APP_CODE, name))
        assert not re.search(r"requestAnimationFrame|setInterval|\.animate\(|animation|transition|localStorage|sessionStorage|TOKEN", body), name
        if name != "copyCommand":
            assert "setTimeout" not in body, name
    assert strip_comments(function_body(APP_CODE, "copyCommand")).count("window.setTimeout(") == 1     # how long the button's answer stays


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_every_theme_draws_the_help_panel_its_command_row_and_a_busy_button(theme_id: str) -> None:
    css = strip_comments(THEME_CSS[theme_id])
    panel = re.search(r"\.nm-help \{([^}]*)\}", css)
    assert panel and "flex-direction: column" in panel.group(1), theme_id           # the status line, grown into a panel
    for selector in (r"\.nm-help > b", r"\.nm-help ol", r"\.nm-help li::marker", r"\.nm-help code", r"\.nm-cmd \{", r"\.nm-cmd code",
                     r"\.nm-help \.nm-status"):
        assert re.search(selector, css), (theme_id, selector)
    assert re.search(r'\.nm-btn\[aria-disabled="true"\][^{]*\{[^}]*opacity', css), theme_id      # a button that is checking looks it
    assert re.search(r"\.nm-help p, \.nm-help ol \{[^}]*max-width", css), theme_id      # one measure for the sentences and the steps


def test_nostromo_sets_the_help_in_natural_case_under_a_heading_in_capitals() -> None:
    """The dashed status line is all capitals in Nostromo; the steps are read, so the panel takes that off the text only."""
    css = strip_comments(THEME_CSS["nostromo"])
    assert "text-transform: none" in re.search(r"\.nm-help \{([^}]*)\}", css).group(1)
    assert "text-transform: uppercase" in re.search(r"\.nm-help > b \{([^}]*)\}", css).group(1)


def test_gates_gives_the_no_activity_card_an_icon_like_its_neighbours() -> None:
    assert re.search(r'\.nm-card\[data-card="none"\][^{]*\{ --g-ico: "\\[0-9A-F]{4}"; \}', THEME_CSS["gates"])


@ui_only
def test_the_help_is_the_status_banner_for_a_missing_or_an_expired_sign_in_only(checks: dict[str, Any]) -> None:
    banner = checks["signIn"]["banner"]
    assert banner["live"] == ""
    for status in ("stale", "no_credentials"):
        assert Markup(banner[status]).find("div")[0]["class"] == "nm-msg nm-help", status
    # every other state stays the plain line it was: the heading and the monitor's reason
    for status, heading in (("loading", "Acquiring"), ("offline", "No carrier"), ("rate_limited", "Rate limited"), ("paused", "Paused")):
        assert banner[status] == f'<div class="nm-msg" role="status"><b>{heading}</b>detail of {status}</div>', status


@ui_only
@pytest.mark.parametrize("page", ["overview", "limits", "cost", "efficiency", "history"])
def test_the_help_is_on_every_page_that_has_a_status_banner(checks: dict[str, Any], page: str) -> None:
    html = checks["signIn"]["onPages"][page]
    assert Markup(html).find("div")[0]["class"] == "nm-msg nm-help" and f"<b>{SIGNIN_HEAD}</b>" in html


@ui_only
def test_with_no_sign_in_the_help_says_what_to_do_in_the_words_it_was_given(checks: dict[str, Any]) -> None:
    html = checks["signIn"]["banner"]["no_credentials"]
    page = Markup(html)
    panel = page.find("div")[0]
    assert panel["role"] == "region" and panel["aria-label"] == "Claude sign-in"          # a landmark, not a live region that re-reads itself
    assert html.startswith(f'<div class="nm-msg nm-help" role="region" aria-label="Claude sign-in"><b>{SIGNIN_HEAD}</b><p>')
    assert paragraphs(html) == [SIGNIN_WHY, SIGNIN_RENEWS]
    steps = [plain(item) for item in re.findall(r"<li>(.*?)</li>", html, re.S)]
    assert steps == [SIGNIN_STEPS[0], SIGNIN_STEPS[1] + INSTALL_COMMAND + "Copy" + SIGNIN_PATH_NOTE,
                     SIGNIN_STEPS[2] + START_COMMAND + "Copy" + SIGNIN_SIGN_IN, SIGNIN_STEPS[3]]
    assert commands(html) == [INSTALL_COMMAND, START_COMMAND]                             # each command as code, the way it is typed
    assert '<div class="nm-cmd" data-command="install"><code class="nm-path" lang="en" translate="no">irm https://claude.ai/install.ps1 | iex</code><button class="nm-btn"' in html
    assert (r'<div class="nm-cmd" data-command="start"><code class="nm-path" lang="en" translate="no">&amp; &quot;$env:USERPROFILE\.local\bin\claude.exe&quot;</code>'
            '<button class="nm-btn"') in html
    buttons = page.find("button")
    assert [(attrs["data-action"], attrs.get("data-command")) for attrs in buttons] == [
        ("copy-command", "install"), ("copy-command", "start"), ("check-again", None)]
    assert all(attrs["type"] == "button" for attrs in buttons)
    copy_install, copy_start, check = buttons
    assert check["class"] == "nm-btn solid" and "aria-disabled" not in check
    assert (copy_install["aria-label"], copy_start["aria-label"]) == (COPY_TITLES["install"], COPY_TITLES["start"])
    assert (copy_install["data-fid"], copy_start["data-fid"]) == ("signin-copy-install", "signin-copy-start")    # each keeps its own focus
    assert page.text.count("Copy") == 2
    assert "Check again" in page.text and not [t for t in page.text if "Still no" in t or "Checking" in t]      # nothing found yet
    (link,) = page.find("a")
    assert link["href"] == README_WEB_AND_APP and link["data-external"] == "1" and "More in the README" in page.text
    assert not any(name.startswith("on") for _tag, attrs in page.tags for name in attrs)             # no inline handler
    assert not re.search(JARGON, plain(html), re.I)


@ui_only
def test_an_expired_sign_in_keeps_the_monitors_reason_and_adds_the_one_thing_to_do(checks: dict[str, Any]) -> None:
    html = checks["signIn"]["expired"]
    assert html.startswith('<div class="nm-msg nm-help" role="region" aria-label="Claude sign-in"><b>Auth lost</b>')       # the banner's own heading
    assert paragraphs(html) == ["The Claude sign-in has expired; send Claude Code a message to renew it", SIGNIN_EXPIRED, SIGNIN_NOT_FOUND]
    page = Markup(html)
    assert page.find("ol") == [] and page.find("a") == []                              # no steps to start from nothing, no manual to read
    # no install command to copy; the command that starts Claude Code where claude is not found (#42)
    assert [(attrs["data-action"], attrs.get("data-command")) for attrs in page.find("button")] == [("copy-command", "start"), ("check-again", None)]
    assert commands(html) == ["claude", "claude", START_COMMAND]
    assert html.index("opens instead, run this:</p>") < html.index('<div class="nm-cmd" data-command="start">') < html.index('class="nm-actions"')
    assert paragraphs(checks["signIn"]["noDetail"]) == [SIGNIN_EXPIRED, SIGNIN_NOT_FOUND]   # no reason sent: the heading and what to do


@ui_only
def test_a_sign_in_turned_down_with_403_asks_for_a_new_sign_in_not_a_message(checks: dict[str, Any]) -> None:
    """#55: a 403 is a sign-in that works for Claude Code but cannot read usage. Using Claude Code does not fix it."""
    html = checks["signIn"]["refused"]
    assert paragraphs(html) == ["Claude did not accept the sign-in (HTTP 403); sign in to Claude Code again with /login",
                                SIGNIN_REFUSED, SIGNIN_NOT_FOUND]
    assert "message" not in plain(html).lower()
    assert commands(html) == ["claude", "/login", "claude", START_COMMAND]
    assert paragraphs(checks["signIn"]["refused401"])[1] == SIGNIN_EXPIRED     # a 401 is renewed by use: the expired help


@ui_only
def test_everything_the_help_was_told_is_escaped(checks: dict[str, Any]) -> None:
    html = checks["signIn"]["hostile"]
    page = Markup(html)
    assert page.find("img") == [] and not any("onerror" in attrs for _tag, attrs in page.tags) and "<img" not in html.lower()
    assert HOSTILE in " ".join(page.text)                                              # the monitor's reason is shown as text


@ui_only
def test_check_again_posts_the_refresh_says_checking_and_then_what_it_found(checks: dict[str, Any]) -> None:
    actions = checks["signInActions"]
    still = actions["still"]
    assert still["calls"] == ["POST /api/refresh", "GET /api/state"]                   # one request to look, one to learn what it found
    busy = still["during"]
    assert busy["check"]["busy"] is True and ">Checking…</button>" in busy["html"] and 'aria-disabled="true"' in busy["html"]
    assert "Still no" not in busy["html"]
    assert (still["check"]["busy"], still["check"]["still"]) == (False, True)
    assert f'<p class="nm-status" role="status">{CHECK_NONE}</p>' in still["html"] and ">Check again</button>" in still["html"]
    assert "aria-disabled" not in still["html"]
    expired = actions["stillExpired"]
    assert (expired["check"]["busy"], expired["check"]["still"]) == (False, True)
    assert f'<p class="nm-status" role="status">{CHECK_STALE}</p>' in expired["html"]        # it says "working", for a sign-in that is there but expired


@ui_only
def test_check_again_sends_one_request_at_a_time(checks: dict[str, Any]) -> None:
    actions = checks["signInActions"]
    assert actions["twice"] == ["POST /api/refresh", "GET /api/state"]                 # three clicks, one check
    assert actions["busyIgnores"] == []                                                # a click while a check is on its way sends nothing


@ui_only
def test_a_check_ends_when_the_cycle_it_asked_for_is_published_or_after_a_while(checks: dict[str, Any]) -> None:
    actions = checks["signInActions"]
    found = actions["found"]
    assert found["check"]["busy"] is False and found["check"]["still"] is False and found["html"] == ""      # signed in: nothing left to say
    assert actions["waiting"]["ended"] is False and actions["waiting"]["check"]["busy"] is True        # updated_at has not moved: still looking
    late = actions["late"]
    assert late["ended"] is True and late["check"]["still"] is True and f">{CHECK_NONE}</p>" in late["html"]     # waited long enough
    assert actions["notSentYet"]["ended"] is False                                     # not before the monitor has answered the click
    assert actions["outlived"]["ended"] is True and actions["outlived"]["check"]["still"] is False       # what it said goes with the panel


@ui_only
def test_a_check_the_monitor_refuses_or_cannot_hear_says_so(checks: dict[str, Any]) -> None:
    for name in ("refused", "lost"):
        failed = checks["signInActions"][name]
        assert failed["calls"] == ["POST /api/refresh"], name                          # no state is asked for: nothing was started
        assert failed["check"]["busy"] is False and failed["check"]["failed"] is True, name
        assert f'<p class="nm-status" role="status">{CHECK_FAILED}</p>' in failed["html"], name


@ui_only
def test_copy_puts_the_command_on_the_clipboard_or_selects_it_and_says_which(checks: dict[str, Any]) -> None:
    copy = checks["signInActions"]["copy"]

    def label(html: str, which: str = "install") -> str:
        return re.search(rf'<button[^>]*data-command="{which}"[^>]*>([^<]*)</button>', html).group(1)

    def spoken(html: str, which: str) -> str:
        """What a screen reader says for the button."""
        return re.search(rf'<button[^>]*data-command="{which}"[^>]*aria-label="([^"]*)"', html).group(1)

    written = copy["written"]
    assert written["writes"] == [INSTALL_COMMAND] and (written["copy"], written["copyOf"]) == ("copied", "install")
    assert label(written["html"]) == "Copied" and label(written["html"], "start") == "Copy"      # only the button that was pressed answers
    assert (spoken(written["html"], "install"), spoken(written["html"], "start")) == ("Copied to the clipboard", COPY_TITLES["start"])
    assert written["timers"] == [3000] and (written["after"]["copy"], written["after"]["copyOf"]) == ("", "")      # for a moment
    assert label(written["after"]["html"]) == "Copy"
    assert written["after"]["painted"] == [["copied", "install"], ["", ""]]            # the page is drawn with the answer, then without it
    for name in ("refused", "throws", "missing", "noClipboard"):                       # nothing to write to, or it says no: select it instead
        shown = copy[name]
        assert (shown["copy"], shown["copyOf"]) == ("select", "install") and label(shown["html"]) == "Press Ctrl+C", name
        assert label(shown["html"], "start") == "Copy", name
        assert spoken(shown["html"], "install") == "Press Ctrl+C to copy the selected command", name
        assert spoken(shown["html"], "start") == COPY_TITLES["start"], name
        assert shown["timers"] == [8000] and label(shown["after"]["html"]) == "Copy", name       # long enough to read it and act
        assert shown["after"]["painted"] == [["select", "install"], ["", ""]], name
    assert copy["refused"]["writes"] == [INSTALL_COMMAND] and copy["throws"]["writes"] == copy["missing"]["writes"] == []
    # "Press Ctrl+C" must leave the command of the button pressed selected, or Ctrl+C takes the wrong one; a copy that worked selects nothing
    target = '.nm-help .nm-cmd[data-command="{}"] code'
    picked = checks["signInActions"]["selected"]
    assert picked["install"] == [target.format("install")]
    assert picked["start"] == picked["expiredStart"] == [target.format("start")]
    assert picked["copied"] == []
    # the second button pressed within the moment: the answer moves to it, and the first press's timer cannot end it early
    moved = checks["signInActions"]["handOver"]
    assert moved["writes"] == [INSTALL_COMMAND, START_COMMAND] and moved["copyOf"] == "start"
    assert label(moved["html"]) == "Copy" and label(moved["html"], "start") == "Copied"
    assert moved["cleared"][-1] == 1 and moved["timers"] == 2
    for name in ("start", "expiredStart"):                                             # the second command, with no sign-in or an expired one
        shown = copy[name]
        assert shown["writes"] == [START_COMMAND] and (shown["copy"], shown["copyOf"]) == ("copied", "start"), name
        assert label(shown["html"], "start") == "Copied" and label(shown["after"]["html"], "start") == "Copy", name
    assert label(copy["start"]["html"]) == "Copy"                                      # the install command's button says nothing
    assert "data-command=\"install\"" not in copy["expiredStart"]["html"]
    for name in ("unknownCommand", "noCommand"):                                       # a command the panel does not have copies nothing
        shown = copy[name]
        assert shown["writes"] == [] and (shown["copy"], shown["copyOf"]) == ("", "") and shown["timers"] == [], name
    unknown = checks["signInActions"]["unknownAction"]                                 # an action the panel does not have does nothing
    assert unknown["calls"] == [] and unknown["copy"] == "" and unknown["check"]["busy"] is False


@ui_only
def test_limits_only_is_decided_only_after_an_ingest_has_finished(checks: dict[str, Any]) -> None:
    decided = checks["limitsOnly"]
    assert decided["done"] is True                                                     # an ingest is done, and the all-time window holds no request
    for name in ("noState", "noIngest", "firstIngestRunning", "firstIngestFailed", "doneNoTotals", "noWindow", "withRequests"):
        assert decided[name] is False, name                                            # not before a state, nor while the first ingest runs
    assert decided["doneWithErrors"] is False                                          # nor when files could not be read: they may hold requests
    assert decided["laterIngestFromNothing"] is False                                  # an ingest that runs before anything was decided decides nothing
    assert decided["laterIngestHolds"] is True                                         # one that runs after (every minute) does not undo it
    assert decided["aRequestEndsIt"] is False                                          # and a counted request ends it


@ui_only
@pytest.mark.parametrize("page", ["cost", "efficiency", "history"])
def test_a_page_that_counts_claude_code_is_one_explanatory_card_where_there_is_none(checks: dict[str, Any], page: str) -> None:
    model = checks["noRecords"]["pages"][page]
    assert model["cls"] == "nm-pgrid" and model["top"] == ["hazard", "head", "update", "status", "problem", "stale"]
    assert [(card["key"], card["state"]) for card in model["cards"]] == [("none", "ready")]
    html = model["cards"][0]["html"]
    assert html.startswith(f'<section class="nm-card wide" aria-label="{NO_ACTIVITY}" data-card="none" data-state="ready">')
    parsed = Markup(html)
    assert parsed.find("h2")[0]["class"] == "nm-ttl" and NO_ACTIVITY in parsed.text and NO_ACTIVITY_WHY in parsed.text
    for left_out in ("data-needs", "nm-big", "nm-stat", "nm-cells", "nm-table", "nm-chart-host", "nm-toolbar", "nm-seg"):
        assert left_out not in html, left_out                                          # no figure, no chart, no window to pick
    assert checks["noRecords"]["top"][page] == {"problem": "", "stale": ""}            # and no banner about a report nobody asked for


@ui_only
def test_the_overviews_record_cards_say_so_and_the_others_stay(checks: dict[str, Any]) -> None:
    cards = {card["key"]: card for card in checks["noRecords"]["overview"]["cards"]}
    assert list(cards) == CARDS["overview"]                                            # the same eight cards, in the same places
    titles = {"cost": "API-equivalent cost, this cycle", "efficiency": "Cache &amp; thinking", "models": "Model mix"}
    for key, title in titles.items():
        html = cards[key]["html"]
        assert cards[key]["state"] == "ready" and "data-needs" not in html, key
        assert f'<h2 class="nm-ttl">{title}</h2>' in html, key                          # the frame and the title stay
        # plain text in English, as before #98 (nameInLine; #98's review): a name's span only off English
        assert html.endswith(f'<p class="nm-empty">{NO_ACTIVITY}.</p></div></section>'), key
        assert not re.search(r"nm-big|nm-mrow|nm-kpi|nm-tile|nm-chart|nm-cost|nm-cells", html), key
    for french in checks["noRecords"]["frenchOverview"]:       # off English the name keeps its span (tName)
        assert french["html"].endswith('<p class="nm-empty"><span lang="en">No <span translate="no">Claude Code</span> activity on '
                                       'this PC.</span></p></div></section>'), french["key"]
    assert [c["key"] for c in checks["noRecords"]["frenchOverview"]] == ["cost", "efficiency", "models"]
    for key in ("limit-session", "limit-weekly", "limit-scoped", "surfaces", "burn"):
        assert "No Claude Code activity" not in cards[key]["html"], key
        assert cards[key]["state"] == "ready", key
    assert [s.split(":")[0] for s in checks["noRecords"]["limits"]] == ["limit-session", "limit-weekly", "credits", "surfaces"]   # Limits is unchanged


@ui_only
def test_nothing_is_asked_for_that_there_is_nothing_to_count(checks: dict[str, Any]) -> None:
    assert checks["noRecords"]["stats"] == {"overview": ["pacing"], "limits": ["pacing"], "cost": [], "efficiency": [], "history": []}
    assert checks["records"]["stats"] == ["cost?window=cycle"]                         # with records the Cost page asks for its report as before
    failed = checks["noRecords"]["failedReport"]
    assert failed["problem"] == "" and failed["stale"] == ""                           # a report that was never wanted has not failed
    assert [s.split(":")[1] for s in failed["states"]] == ["ready"] * 8


@ui_only
def test_a_pc_with_records_or_a_first_ingest_still_running_keeps_the_pages_it_had(checks: dict[str, Any]) -> None:
    for name in ("records", "firstIngest"):
        found = checks[name]
        for page in ("overview", "cost", "efficiency", "history"):
            assert [s.split(":")[0] for s in found[page]] == CARDS[page], (name, page)
        assert {"cost:pending", "efficiency:pending", "models:pending"} <= set(found["overview"]), name       # waiting for the cost report, as before
        assert "none:ready" not in found["cost"] + found["efficiency"] + found["history"], name


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
    app.js passed CI. The workflow runs this file once more with USAGE_MONITOR_UI_TESTS=1."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "USAGE_MONITOR_UI_TESTS" in text and "tests/test_webui.py" in text and "setup-node" in text
    assert "tests/test_motion.py" in text                                          # motion.js is run under node there too
    # The node step itself (not a comment) runs the three files, the dashboard's "no English left" scan among them, with
    # the switch set in that step.
    step = text[text.index("- name: Run the dashboard script tests"):]
    run_line = next(line.strip() for line in step.splitlines() if line.strip().startswith("run:"))
    assert run_line == "run: python -m pytest tests/test_webui.py tests/test_motion.py tests/test_english_left.py"
    assert "USAGE_MONITOR_UI_TESTS: '1'" in step[:step.index(run_line)]


def test_the_test_hook_is_inert_unless_a_test_sets_it() -> None:
    code = strip_comments(APP_CODE)
    assert code.count("__USAGE_MONITOR_TEST__") == 2                                         # the guard and the hand-over
    hook = code[code.index("if (window.__USAGE_MONITOR_TEST__)"):]
    assert hook.index("return;") < hook.index("init();")                           # the page is not started under test

# ── Gates' own words (#98) ────────────────────────────────────────────────────────────────────────────────────────

LINK_CODES = ["live", "loading", "stale", "offline", "rate_limited", "no_credentials", "paused", "lost"]
ENGLISH_CATALOG: dict[str, Any] = json.loads((WEBUI / "i18n" / "en.json").read_text(encoding="utf-8"))


@ui_only
def test_gates_takes_its_own_words_and_the_others_never_do(checks: dict[str, Any]) -> None:
    """#98 (G2): where gates.js turned the capitals into other words (the link's states, DATA AGE, TOK / MIN), Gates now
    gets them from page.gates.*: in the header, the health box's link row, the status line under the heading and the burn
    rate's unit. Nostromo and Night City get the header's words and never one of Gates'. A French page with no
    translation shows the English of its look, marked lang="en"."""
    en = ENGLISH_CATALOG
    words = checks["gatesWords"]
    gates_only = {en[k] for k in en if k.startswith("page.gates.") and en[k] not in
                  {en[j] for j in en if j.startswith(("header.", "nav.", "overview.burn."))}}
    assert gates_only >= {"Connecting", "Sign-in needed", "Offline", "Not signed in", "No connection", "Tokens / min"}
    for look in ("gates", "frenchGates", "nostromo", "nightCity", "frenchNostromo"):
        gates = look.endswith(("gates", "Gates"))
        mark = "en" if look.startswith("french") else None
        for code in LINK_CODES:
            shown = words[look][code]
            header = en["page.gates.link." + code] if gates else en["header.link." + code]
            health = header if gates or code != "live" else en["nav.health.live"]
            assert (shown["uplink"], shown["uplinkLang"], shown["link"], shown["linkLang"]) == (header, mark, health, mark), (look, code)
            template = en["page.gates.age"] if gates else en["header.age"]
            if mark:                                                     # French writes the unit its own way
                lead, tail = template.split("{age}")
                assert shown["age"].startswith(lead) and shown["age"].endswith(tail), (look, code, shown["age"])
            else:
                assert shown["age"] == template.replace("{age}", "12 s"), (look, code, shown["age"])
            assert shown["ageLang"] == mark and shown["unit"] == [' lang="en"' if mark else None,
                                                                   en["page.gates.burn.unit" if gates else "overview.burn.unit"]]
            if code in ("loading", "offline", "rate_limited", "paused", "stale"):
                heading = re.search(r'<b( lang="en")?>([^<]*)</b>', shown["banner"])
                assert heading and heading.group(2) == header and (heading.group(1) or None) == (' lang="en"' if mark else None), (look, code)
            if not gates:
                assert not any(text in json.dumps(shown, ensure_ascii=False) for text in gates_only), (look, code)
        before = words[look]["none"]
        assert (before["uplink"], before["age"], before["link"]) == (
            en["page.gates.link.loading" if gates else "header.link.loading"], en["page.gates.age_none" if gates else "header.age_none"], "--")


@ui_only
def test_gates_says_the_burn_rates_cost_an_hour_its_own_way_before_there_is_a_rate(checks: dict[str, Any]) -> None:
    """#98: with no burn rate the card's cost an hour is "-- / hour"; gates.js made "-- / Hour" of today's capitals (its
    sentence case raises the first word after the dashes), so Gates has its own words for it. With a rate it is the same
    line in every look."""
    en, words = ENGLISH_CATALOG, checks["gatesWords"]
    for look in ("gates", "frenchGates", "nostromo", "nightCity", "frenchNostromo"):
        mark = ' lang="en"' if look.startswith("french") else None
        none = en["page.gates.burn.per_hour_none"] if look.lower().endswith("gates") else en["overview.burn.per_hour"].replace("{amount}", "--")
        assert words[look]["none"]["hour"] == [mark, none], look
        assert words[look]["live"]["hour"][0] == mark and words[look]["live"]["hour"][1].endswith(" / hour"), look
    assert en["page.gates.burn.per_hour_none"] == "-- / Hour"


# ── The banners', Overview's and Limits' words (#98) ─────────────────────────────────────────────────────────────

# What ui/server.py answers the statistics routes with: each text the page compares to tell two reasons of one status apart
SERVER_CODE = (Path(__file__).resolve().parent.parent / "usage_monitor" / "ui" / "server.py").read_text(encoding="utf-8")


def test_the_reason_texts_the_page_compares_are_the_servers() -> None:
    """#98: statReason tells a report's reasons apart by the answer's status and, within a status, by the monitor's text,
    compared whole. Each text in app.js's table, with its status, is one ui/server.py answers: a change on either side fails
    here, not silently on the page (where the reason would be the monitor's English text as it came, never the catalog's)."""
    table = re.search(r"var STAT_ERRORS = \{(.*?)\};", APP_CODE, re.S).group(1)
    texts = dict(re.findall(r'(\w+): "([^"]+)"', table))
    statuses = {code: int(status) for code, status in re.findall(r"(\w+): (\d{3})", re.search(
        r"var STAT_ERROR_STATUS = \{(.*?)\};", APP_CODE, re.S).group(1))}
    assert set(texts) == set(statuses) == {"unavailable", "busy", "closed", "internal", "token"}
    for code, text in texts.items():
        status = statuses[code]
        raised = f'_HttpError({status}, "{text}")' in SERVER_CODE
        sent = f'self._send_json(req, {status}, {{"error": "{text}"}})' in SERVER_CODE
        assert raised or sent, (code, status, text)
    # and they are the catalog's English for those reasons (#97's rule: every answer the monitor gives reads as before)
    assert all(ENGLISH_CATALOG["stats.reason." + code] == text for code, text in texts.items())


@ui_only
def test_each_stats_answer_is_worded_by_its_status(checks: dict[str, Any]) -> None:
    """#98: a failed report's reason in the banners and the problem lines is the catalog's when the page knows it: chosen by
    the answer's status (and the monitor's text where a status has two reasons). Any other text the monitor sends (a query it
    refused, an unknown report, a 500 or 503 with another text) is shown as it came, escaped, as English always showed it;
    "HTTP {status}" names only an answer with no text. So English reads as it did before #98 for every answer. On a French
    page the reason is French where the catalog has it, else English's, and the monitor's own text, marked lang="en", inside
    the French sentence."""
    reasons = checks["stageB"]["reasons"]
    codes = {name: found["code"] for name, found in reasons.items()}
    assert codes == {"lost": "lost", "token": "token", "token_blank": "http", "unavailable": "unavailable", "internal": "internal",
                     "internal_blank": "http", "internal_other": "sent", "busy": "busy", "closed": "closed", "busy_other": "sent",
                     "bad_gateway": "http", "query": "sent", "unknown_report": "sent", "wrong_status": "sent", "markup": "sent"}
    said = {"lost": "the dashboard lost its link", "token": "missing or wrong token", "token_blank": "HTTP 401",
            "unavailable": "statistics are not available", "internal": "internal error", "internal_blank": "HTTP 500",
            "internal_other": "database is locked", "busy": "statistics are not available right now",
            "closed": "the statistics database could not be opened", "busy_other": "the disk is on fire", "bad_gateway": "HTTP 502",
            "query": "invalid days: expected a whole number from 7 to 800", "unknown_report": "unknown report",
            "wrong_status": "internal error", "markup": "unknown parameter: &lt;b&gt;&amp;x"}
    sent = {name for name, code in codes.items() if code == "sent"}
    for name, found in reasons.items():
        assert found["stale"] == ('<div class="nm-msg" role="status"><b>Statistics old</b>The latest refresh failed ('
                                  f"{said[name]}). These figures are from 3 m ago.</div>"), name
        problem = re.sub(r"^.*</b>", "", found["problem"])[:-len("</div>")]
        status = {"unavailable": 501, "busy": 503, "closed": 503, "busy_other": 503, "wrong_status": 503}.get(name)
        assert problem == {501: ENGLISH_CATALOG["stats.problem.unavailable"], 503: ENGLISH_CATALOG["stats.problem.not_ready"]}.get(
            status, f"Statistics could not be loaded ({said[name]}). Trying again shortly."), name
        french = found["frenchStale"]
        assert french.startswith('<div class="nm-msg" role="status"><b lang="en">Statistics old</b>OLD-FR ('), name
        assert ("(BUSY-FR)" in french) == (name == "busy") and (f'(<span lang="en">{said[name]}</span>)' in french) == (name != "busy"), name
        if name in sent:          # the monitor's text, marked on its own: the sentence around it is the French catalog's
            assert f'FAILED-FR (<span lang="en">{said[name]}</span>)' in found["frenchProblem"] or status in (501, 503), name
    assert reasons["internal"]["frenchProblem"].endswith('FAILED-FR (<span lang="en">internal error</span>)</div>')
    assert "<b>&x" not in reasons["markup"]["problem"] + reasons["markup"]["stale"] + reasons["markup"]["frenchStale"]


@ui_only
def test_a_time_left_inside_a_sentence_is_in_lower_case(checks: dict[str, Any]) -> None:
    """#98: inside a sentence ("In {time}") the time left is written as a sentence writes it (English "3 h 27 m", CLDR's
    units as they are elsewhere), so Nostromo and Night City set it in capitals with the words around it and Gates shows it
    as written; alone (a badge, the facts' value) it keeps today's form, which gates.js still re-cases there. Under a minute
    it is format.now: "now" and "NOW"."""
    remain = checks["stageB"]["remain"]
    assert remain["en"] == {"sentence": ["3 h 27 m", "2 d 3 h", "59 m", "now", "now"], "badge": ["3 H 27 M", "2 D 3 H", "59 M", "NOW", "NOW"]}
    fr = remain["fr"]
    assert fr["sentence"][1] == "maintenant" and fr["badge"][1] == "MAINTENANT"
    assert fr["badge"][0] == casing.upper(fr["sentence"][0], "fr") != fr["sentence"][0]
    assert "<span>In 3 h 27 m</span>" in remain["card"]                                # the card's fact takes the sentence's form


@ui_only
def test_the_daily_tips_count_requests_with_the_languages_plural(checks: dict[str, Any]) -> None:
    """#98: a column's tip counts its requests with the plural form of the language the tip's words came from (the page's
    CLDR rules): French counts 0 and 1 as one, Spanish only 1, English only 1."""
    tips = checks["stageB"]["tips"]                                   # the last three days, whatever today is
    assert all(re.match(r"[A-Z]{3}, [A-Z]{3} \d{1,2}: ", tip) for tip in tips["en"]), tips["en"]
    assert [tip.split(": ", 1)[1] for tip in tips["en"]] == ["$1.50 API-equivalent, 0 requests", "$1.50 API-equivalent, 1 request",
                                                            "$1.50 API-equivalent, 2 requests"]
    assert [tip.split()[-1] for tip in tips["fr"]] == ["R-ONE", "R-ONE", "R-OTHER"]
    assert [tip.split()[-1] for tip in tips["es"]] == ["R-OTHER", "R-ONE", "R-OTHER"]


@ui_only
def test_english_limit_tags_are_the_limits_own_and_other_languages_name_the_kind(checks: dict[str, Any]) -> None:
    """#98: in English a limit's tag is its own label, the identity string the data layer gives (#90), as a name, and the
    pacing card's aria-label keeps it, capitals and all. Another language names the limit from its kind and scope (the
    widgets' rule, #93): "{name} weekly" with the scope as the usage API names it, the scoped placeholder "Scoped", and a
    kind it has no words for keeps the label. A name's element is marked translate="no" there; English keeps its markup.
    Outside English the Overview card's meta carries its whole text in a title and the card a name (#98's owner question 1);
    in English the meta's title alone (Chris on #98, 2026-11-27)."""
    tags = checks["stageB"]["tags"]
    en = {name: (tag["text"], tag["lang"], tag.get("name", False)) for name, tag in tags["en"].items()}
    assert en == {"scoped": ("FABLE WEEKLY", "", True), "surface": ("CLAUDE_CODE WEEKLY", "", True), "unnamed": ("WEEKLY SCOPED", "", True),
                  "pending": ("SCOPED", "", True), "session": ("SESSION", "", True), "unlabelled": ("Session", "en", False),
                  "weekly": ("WEEKLY", "", True), "other": ("MONTHLY THING", "", True), "otherBare": ("MONTHLY THING", "", True)}
    fr = {name: (tag["text"], tag["lang"], tag.get("name", False)) for name, tag in tags["fr"].items()}
    assert fr == {"scoped": ("Fable HEBDO-FR", "fr", False), "surface": ("claude_code HEBDO-FR", "fr", False),
                  "unnamed": ("Weekly scoped", "en", False), "pending": ("Scoped", "en", False), "session": ("SESSION-FR", "fr", False),
                  "unlabelled": ("SESSION-FR", "fr", False), "weekly": ("Weekly", "en", False), "other": ("MONTHLY THING", "", True),
                  "otherBare": ("MONTHLY THING", "", True)}
    markup = checks["stageB"]["tagMarkup"]
    assert '<span class="nm-tag">FABLE WEEKLY</span><span class="nm-meta-t" title="7-day window">7-day window</span>' in markup["en"]
    assert '<span class="nm-tag">Fable HEBDO-FR</span><span class="nm-meta-t" lang="en" title="7-day window">7-day window</span>' in markup["fr"]
    assert markup["fr"].startswith('<section class="nm-card" data-limit="weekly_scoped" aria-label="Fable HEBDO-FR, 7-day window, ')
    assert '<span class="nm-tag" lang="en">Weekly</span>' in markup["frWeekly"]
    assert 'aria-label="SESSION"' in markup["pacingEn"] and '<span class="nm-tag">SESSION</span>' in markup["pacingEn"]
    assert 'aria-label="SESSION-FR"' in markup["pacingFr"]
    assert "translate=" not in markup["en"] + markup["pacingEn"]


@ui_only
def test_an_unknown_plan_is_worded_by_the_page(checks: dict[str, Any]) -> None:
    """#90's hand-off, done here: a plan whose sign-in names no subscription keeps "Unknown plan" in the data layer's name;
    the page shows plan.unknown in its place (English as before, another language its own words). A plan with a
    subscription keeps its name, and a plan with no name the cost card's fallback. plan.unknown is the catalog's word, not
    a name: never translate="no", and marked with its language where it is not the plan line's (#98's review). In English
    the cost card's plan line is plain text, as before #98: a plan's own name too."""
    plan = checks["stageB"]["plan"]
    assert plan["words"] == ["Unknown plan", "Forfait inconnu", "Max 5x", None, "", "Team"]
    assert plan["unknown"] == [True, False, False, False]
    assert "<span>UNKNOWN PLAN · $50 / month</span>" in plan["en"]
    assert "<span>MAX 5X · $50 / month</span>" in plan["named"]["en"]
    assert "translate=" not in plan["en"] + plan["named"]["en"]
    assert '<span lang="en"><span lang="fr">FORFAIT INCONNU</span> · 50' in plan["fr"] and "Unknown plan" not in plan["fr"]
    assert '<span><span lang="en">UNKNOWN PLAN</span> - 50' in plan["partial"]          # an English word inside a French line
    assert '<span translate="no">MAX 5X</span>' in plan["named"]["fr"]          # a name is marked as one off English
    assert 'translate="no">FORFAIT' not in plan["fr"] and 'translate="no">UNKNOWN' not in plan["partial"]
    assert "1 cell = 1 month of Forfait inconnu plan" in plan["fr"]          # ratio.note, plain text (the Cost page escapes it)


@ui_only
def test_the_overviews_cut_lines_keep_their_whole_text_outside_english(checks: dict[str, Any]) -> None:
    """#98's owner question 1, as decided: outside English the Overview may cut a card's meta, and a limit card's pace chip,
    with an ellipsis (the designed cuts). Each keeps its whole text in the element, in its title, and in the card's name
    (aria-label: overview.card.name, overview.card.name_chip for a limit card). Every Overview card subtitle carries its
    whole text in a title in English too (Chris on #98, 2026-11-27: a cut subtitle can be read), the cost card's daily
    chart's too; otherwise English keeps its markup: no title on a chip, no name on a limit card, each other card named by
    its title as before."""
    def opening(markup: str) -> str:
        return re.match(r"<section[^>]*>", markup).group(0)

    def name(markup: str) -> str:
        return unescape(re.search(r' aria-label="([^"]*)"', opening(markup)).group(1))

    english = checks["q1"]["en"]
    for card, markup in english.items():
        metas = [(attrs, text) for attrs, text in re.findall(r'<span class="nm-meta-t"([^>]*)>([^<]*)</span>', markup) if text != "&nbsp;"]
        assert metas, card
        for attrs, text in metas:
            title = re.search(r' title="([^"]*)"', attrs)
            assert title and unescape(title.group(1)) == unescape(text), (card, attrs, text)
        assert not re.search(r'class="nm-chip[^"]*"[^>]*title=', markup), card
    assert "aria-label" not in opening(english["limit"]) and "aria-label" not in opening(english["empty"])
    assert [name(english[card]) for card in ("cost", "efficiency", "models", "surfaces")] == [
        ENGLISH_CATALOG["overview.cost.title"], ENGLISH_CATALOG["overview.efficiency.label"], ENGLISH_CATALOG["overview.models.title"],
        ENGLISH_CATALOG["overview.surfaces.title"]]
    french = checks["q1"]["fr"]
    for card, markup in french.items():
        attrs, text = re.search(r'<span class="nm-meta-t"([^>]*)>([^<]*)</span>', markup).groups()
        title = re.search(r' title="([^"]*)"', attrs)
        assert title and unescape(title.group(1)) == unescape(text), card
        assert unescape(text) in name(markup), card
    attrs, word = re.search(r'<span class="nm-chip[^"]*"([^>]*)>([^<]*)</span>', french["limit"]).groups()
    assert word == "sous le rythme" and f' title="{word}"' in attrs
    assert name(french["limit"]) == "Session, fen\u00eatre de 5 heures, sous le rythme"     # name_chip, English's form behind it
    assert name(french["surfaces"]) == "Where the week went : part, 7 jours"           # the page's own overview.card.name
    assert name(french["efficiency"]) == "Cache and thinking : this cycle"


@ui_only
def test_the_pacing_cards_hidden_texts_are_in_the_pages_words_and_formats(checks: dict[str, Any]) -> None:
    """#98's critic 14: until the pacing report answers, a card holds a hidden sentence as long as the one it waits for. It is
    the catalog's sentence in the page's formats: the clock of noon (English 12:00 PM, French 12:00) and the language's 00.0."""
    pending = checks["stageB"]["pending"]
    en = ENGLISH_CATALOG
    assert [s["text"] for s in pending["en"]] == [en["limits.story.session.pending"].replace("{time}", "12:00 PM"),
                                                  en["limits.story.weekly.behind"]["other"].replace("{count}", "00.0")]
    assert [s["text"] for s in pending["fr"]] == [en["limits.story.session.pending"].replace("{time}", "12:00"),
                                                  en["limits.story.weekly.behind"]["other"].replace("{count}", "00,0")]
    assert {s["chip"] for s in pending["en"]} == {"Unknown"} and all(s["toneId"] == "neutral" for s in pending["en"])


@ui_only
def test_a_pacing_charts_label_is_one_sentence_with_both_parts(checks: dict[str, Any]) -> None:
    """#98's critic 25: what a screen reader reads for a limit's chart is one key, limits.chart.label, with the readings and
    the forecast as its two parts, so a translator sees the whole and may change their order."""
    label = checks["stageB"]["chartLabel"]
    assert label["en"].startswith("Weekly usage readings, 2 points. Reaches 100% about ") and label["en"].endswith(" at this rate")
    assert label["fr"].startswith(f"Reaches 100{NB}% about ") and label["fr"].endswith(" at this rate // Weekly usage readings, 2 points")


@ui_only
def test_a_forecasts_end_label_has_the_languages_percent(checks: dict[str, Any]) -> None:
    """#98's review: the label above a forecast's end is limits.chart.end_label, filled with the language's percent (French
    "100 %" with a no-break space, Turkish "%100"), as its tooltip has it; English reads "100% ~ <clock>" as before. The
    pseudo scan cannot see it, since it holds no words."""
    end = checks["stageB"]["endLabel"]
    assert ENGLISH_CATALOG["limits.chart.end_label"] == "{percent} ~ {time}"
    for lang, percent in (("en", "100%"), ("fr", f"100{NB}%"), ("tr", "%100")):
        label, tip = end[lang]["label"], end[lang]["tip"]
        clock = label.split(" ~ ", 1)[1]
        assert label == f"{percent} ~ {clock}" and tip.startswith(f"Reaches {percent} about {clock} "), (lang, end[lang])
    assert re.fullmatch(r"100% ~ [A-Z]{3} \d{1,2}:\d{2} [AP]M", end["en"]["label"])


@ui_only
def test_a_limits_severity_is_worded_from_its_code(checks: dict[str, Any]) -> None:
    severity = checks["stageB"]["severity"]
    assert "Severity</span><b>nominal</b>" in severity["en"] and "<b>normal-fr</b>" in severity["fr"]


@ui_only
def test_a_chart_label_is_in_capitals_but_its_values_keep_their_case(checks: dict[str, Any]) -> None:
    """tUpper (#98): an SVG label's words in the capitals of the language they come from (Turkish's dotted İ, English's own
    rule for an English fallback), its values filled in afterwards as they are ("1,2 k$" keeps its k)."""
    assert checks["tUpper"] == {"own": "ORT. 1,2 k$ GÜN", "plain": "BUGÜN", "english": "TODAY i", "missing": "no.such.key",
                                "unfilled": "ORT. {amount} GÜN"}


# ── The page's English in every look (#98) ────────────────────────────────────────

# The dashboard's English before #98 moved its words into the catalog, as a person sees it in each of the five looks:
# tests/page_words.js on the code at c05f45d (tests/fixtures/page_words_english.json, never re-recorded), from the frozen
# answers in tests/fixtures/page_words_inputs.json.
PAGE_WORDS_JS = Path(__file__).resolve().parent / "page_words.js"
PAGE_WORDS_RECORD: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "page_words_english.json").read_text(encoding="utf-8"))
PAGE_LOOKS = [{"name": "nostromo", "theme": "nostromo", "variant": "amber"},
              {"name": "night_city", "theme": "night_city", "variant": "blackwall"},
              {"name": "edgerun", "theme": "night_city", "variant": "edgerun"},
              {"name": "gates-dark", "theme": "gates", "variant": "blue-dark"},
              {"name": "gates-light", "theme": "gates", "variant": "blue-light"}]
CHROME_REGIONS = ["chrome:brand", "chrome:uplink", "chrome:age", "chrome:refresh", "chrome:nav"]
OVERVIEW_CARDS = ["card:limit-session", "card:limit-weekly", "card:limit-scoped", "card:cost", "card:efficiency",
                  "card:models", "card:surfaces", "card:burn"]
BEFORE_STATE = {"before-state", "before-state-tick", "first-poll-failed"}     # Gates' theme script comes with the state


def page_views(record: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    """case|look|page -> region -> {text, attrs, and gates where the record has it}."""
    strings = record["strings"]
    views: dict[str, dict[str, dict[str, Any]]] = {}
    for key, regions in record["views"].items():
        views[key] = {}
        for name, refs in regions.items():
            region: dict[str, Any] = {"text": strings[refs[0]], "attrs": json.loads(strings[refs[1]])}
            if len(refs) > 2:
                region["gates"] = None if refs[2] is None else strings[refs[2]]
            views[key][name] = region
    return views


THEN = page_views(PAGE_WORDS_RECORD)
# The answers the first record lacked (#98's review: the statistics routes' own texts the page does not word), recorded later
# from the same code at c05f45d by page_words.js's --record-more, so the first record stays as it was made.
PAGE_WORDS_MORE: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "page_words_english_more.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def page_words() -> dict[str, Any]:
    """What tests/page_words.js reads off the page now (one node run for the module, with the record's TZ)."""
    run = subprocess.run([node_binary(), str(PAGE_WORDS_JS), str(WEBUI)], capture_output=True, text=True, timeout=600,
                         check=False, encoding="utf-8", env={**os.environ, "TZ": "UTC"}, creationflags=NO_WINDOW)
    assert run.returncode == 0, run.stderr[-2000:]
    return json.loads(run.stdout)


def words_in(page: str, region: str, look: str = "nostromo") -> str:
    """Every text the record holds for a region of a page in one look, joined."""
    return "\n".join(r["text"] for key, view in THEN.items() if key.endswith("|" + look + "|" + page)
                     for name, r in view.items() if name == region or (region.endswith("*") and name.startswith(region[:-1])))


def test_the_page_words_record_is_whole() -> None:
    """The record covers what #98 moves (its critic 3): the shell, the header and the sidebar; Overview and Limits; the
    heading and banners of every page; the sign-in help's heading; Cost's value and credits cards and Efficiency's
    pipeline card; in all five looks. Its cases reach every status and a lost link, the page before its first state, at
    its first tick and after a failed first poll, every answer a report can get (each failure, pending, stale), every
    verdict, pace chip and velocity, credits on and off, no, one and two scoped limits, the plan's price known, unknown and
    overridden for an unknown plan, an ingest, unpriced models, no Claude Code activity and an update on offer."""
    record = PAGE_WORDS_RECORD
    assert record["looks"] == PAGE_LOOKS
    assert len(record["strings"]) == len(set(record["strings"]))
    keys = [c["name"] + "|" + look["name"] + "|" + page for c in record["cases"] for look in PAGE_LOOKS for page in c["pages"]]
    assert list(THEN) == keys
    for c in record["cases"]:
        assert c["pages"] and set(c["pages"]) <= set(PAGES), c
        for look in PAGE_LOOKS:
            for i, page in enumerate(c["pages"]):
                view = THEN[c["name"] + "|" + look["name"] + "|" + page]
                assert ([r for r in view if r.startswith("chrome:")] == CHROME_REGIONS) == (i == 0), (c, page)
                assert "top:head" in view, (c, page)
                if page == "overview":
                    assert all(card in view for card in OVERVIEW_CARDS), (c, look)
                if page == "limits":
                    assert any(r.startswith("card:limit-") for r in view) and any(r.startswith("card:credits") for r in view)
                if page == "cost" and c["name"] != "no-activity":
                    assert "card:value" in view and "card:credits" in view, (c, look)
                if page == "efficiency" and c["name"] != "no-activity":
                    assert "card:pipeline" in view, (c, look)
                gated = look["theme"] == "gates" and c["name"] in BEFORE_STATE
                assert all(("gates" in r) == gated for r in view.values()), (c, look, page)
    pages_of = {c["name"]: c["pages"] for c in record["cases"]}
    assert set(pages_of["live"]) == set(PAGES) and set(pages_of["before-state"]) == set(PAGES)
    uplink = words_in("overview", "chrome:uplink")
    for word in ("UPLINK LIVE", "ACQUIRING", "AUTH LOST", "NO CARRIER", "RATE LIMITED", "NO LOGIN", "PAUSED", "NO LINK"):
        assert word in uplink, word
    assert "DATA AGE --" in words_in("overview", "chrome:age") and "DATA AGE 3 H" in words_in("overview", "chrome:age")
    assert "INGEST 28%" in words_in("overview", "chrome:nav") and "UNPRICED2" in words_in("overview", "chrome:nav")
    status = words_in("overview", "top:status")
    for heading in ("ACQUIRING", "AUTH LOST", "NO CARRIER", "RATE LIMITED", "NO CLAUDE SIGN-IN YET", "PAUSED"):
        assert heading in status, heading
    for page in ("overview", "limits", "cost", "efficiency", "history"):
        assert "STATISTICS OLD" in words_in(page, "top:stale"), page
        assert ("STATISTICS UNAVAILABLE" in words_in(page, "top:problem")) == (page != "limits"), page   # Limits says it in its cards
    assert "Statistics could not be loaded (HTTP 502)." in words_in("limits", "card:limit-session")
    problem = words_in("cost", "top:problem")
    for reason in ("THE DASHBOARD LOST ITS LINK", "INTERNAL ERROR", "IS NOT READY YET", "NOT AVAILABLE IN THIS BUILD"):
        assert reason in problem, reason
    limits_cards = words_in("limits", "card:limit-*") + words_in("overview", "card:limit-*")
    for word in ("LIMITED", "AT RISK", "SAFE", "IDLE", "UNKNOWN", "AHEAD", "ON PACE", "BEHIND", "UNUSED", "NO DRAW",
                 "AHEAD OF PACE", "UNDER PACE", "NO CLOCK", "NO RESET TIME", "LIMIT REACHED", "LIMIT ~", "SAFE UNTIL RESET",
                 "IDLE NO RECENT DRAW", "FORECAST NEEDS MORE READINGS", "PROJECTION NEEDS MORE TIME", "BUDGET"):
        assert word in limits_cards, word
    burn = words_in("overview", "card:burn")
    for velocity in ("IDLE", "SLOW", "NORMAL", "FAST", "VERY FAST", "WARP"):
        assert "BURN RATE" + velocity in burn, velocity
    credits = words_in("limits", "card:credits*") + words_in("cost", "card:credits")
    assert "USAGE CREDITS ARE OFF" in credits and "25% USED" in credits
    assert "CLAUDE CODE WEEKLY" in words_in("limits", "card:limit-scoped-1") and "card:limit-scoped" in THEN["scoped-none|nostromo|overview"]
    cost = words_in("overview", "card:cost")
    assert "PLAN PRICE UNKNOWN" in cost and "PLAN · $50 / MONTH" in cost and "1 CELL = 1 MONTH OF PLAN PLAN" in cost
    assert "NO CLAUDE CODE ACTIVITY ON THIS PC." in cost
    assert "IS AVAILABLE" in words_in("about", "top:about-update")


def gates_first_frame(key: str) -> bool:
    """A Gates view before the first state: gates.js has not come yet (it comes with the state's theme)."""
    case, look, _ = key.split("|")
    return case in BEFORE_STATE and look.startswith("gates")


@ui_only
def test_the_page_shows_todays_english_in_every_look(page_words: dict[str, Any]) -> None:
    """#98: the shell, the header, the sidebar, the page headings and banners, Overview and Limits (and the cards elsewhere
    that share their code) read as they did before their words moved into the catalog, byte for byte, in every look: each
    region's text as drawn (the look's CSS capitals, and in Gates what gates.js makes of it) and every attribute people read.
    The page met no error on the way.

    One difference is recorded (#98's critic 11): before its first state, Gates showed the shell's own capitals until
    gates.js came with the theme ("ACQUIRING", "REFRESH", "OVERVIEW"); now its words are Gates' from app.js's first render
    (init, before any state; the served shell's own words show until app.js has run). What
    it shows once gates.js runs is still the record's, byte for byte, and the header's and the sidebar's words are already
    the ones gates.js would make of them."""
    assert page_words["problems"] == []
    now = page_views(page_words)
    assert list(now) == list(THEN)
    differences = []
    for key, regions in THEN.items():
        for name in sorted(set(regions) | set(now[key])):
            then, shown = regions.get(name), now[key].get(name)
            if gates_first_frame(key) and then and shown:
                same = then["attrs"] == shown["attrs"] and then["gates"] == shown["gates"]
                if name.startswith("chrome:") and name != "chrome:brand":       # the product's name is kept as written
                    same = same and shown["text"] == shown["gates"]
            else:
                same = then == shown
            if not same:
                differences.append(f"{key} {name}:\n  then {json.dumps(then, ensure_ascii=False)[:600]}\n"
                                   f"  now  {json.dumps(shown, ensure_ascii=False)[:600]}")
    assert not differences, f"{len(differences)} regions differ:\n" + "\n".join(differences[:12])


def test_the_later_record_holds_the_answers_the_first_lacked() -> None:
    """The second record (#98's review) is the first's kind, made from the same code: a refused query (400), an unknown
    report (404) and a refresh refused with a query's text (the stale banner), each as English showed the monitor's own text
    before #98, and a plan the data layer names "Unknown plan" (a sign-in with no subscription, a price the user set), from
    the cost report and from the state, in all five looks."""
    more = PAGE_WORDS_MORE
    assert more["looks"] == PAGE_LOOKS and more["_about"][0].startswith("Cases tests/fixtures/page_words_english.json lacked")
    assert [c["name"] for c in more["cases"]] == ["reports-400", "reports-404", "reports-stale-400", "plan-unknown-named",
                                                  "plan-unknown-named-pending"]
    assert not set(page_views(more)) & set(THEN)
    texts = "\n".join(more["strings"])
    assert "STATISTICS COULD NOT BE LOADED (INVALID DAYS: EXPECTED A WHOLE NUMBER FROM 7 TO 800). TRYING AGAIN SHORTLY." in texts
    assert "Statistics could not be loaded (unknown report). Trying again shortly." in texts
    assert "THE LATEST REFRESH FAILED (UNKNOWN PARAMETER: WINDW). THESE FIGURES ARE FROM 2 M AGO." in texts
    assert "HTTP 4" not in texts
    assert "YOUR PLANUNKNOWN PLAN · $50 / MONTH" in texts and "Your planUnknown plan · $50 / month" in texts    # Nostromo, Gates


# Each record as tests/page_words.js first made it (--record, --record-more, on the code at c05f45d), by its sha256. Since then
# both differ from that by one deliberate change alone (Chris on #98, 2026-11-27): every Overview card subtitle (.nm-meta-t on
# the Overview's cards) carries its full text in a title, in English too, derived from the record (nothing on screen moved).
FIRST_MAKING = {"page_words_english.json": "1a05b790667797d3007db97201bb3fbc9c7b48fb715c8cdb8bb058dab5c160ec",
                "page_words_english_more.json": "7bcbfe92adafa0c47feef5352668445b7ceea64534dad6395f5661aa15a06653"}
# The subtitles each Overview card can show (en.json's keys), by region: the limit cards', the cost card's and its daily
# chart's, efficiency's, model mix's and the surfaces card's. The burn card has none.
SUBTITLE_KEYS = {"limit": ["limit.meta.session", "limit.meta.weekly", "overview.limit.no_scoped"],
                 "cost": ["overview.cost.meta", "overview.cost.meta_range"],
                 "daily": ["overview.daily.top", "overview.daily.no_use", "overview.daily.pip"],
                 "efficiency": ["overview.efficiency.cycle", "overview.efficiency.month"],
                 "models": ["overview.models.cycle", "overview.models.month"], "surfaces": ["overview.surfaces.meta"]}


def record_text(record: dict[str, Any]) -> str:
    """A record as tests/page_words.js's format() writes it: one case, string or view a line."""
    def dumps(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    def lines(items: list[str]) -> str:
        return ",\n".join("  " + item for item in items)
    views = [dumps(key) + ": " + dumps(view) for key, view in record["views"].items()]
    return "\n".join(["{", ' "_about": ' + dumps(record["_about"]) + ",", ' "looks": ' + dumps(record["looks"]) + ",",
                      ' "cases": [', lines([dumps(c) for c in record["cases"]]), " ],",
                      ' "strings": [', lines([dumps(t) for t in record["strings"]]), " ],",
                      ' "views": {', lines(views), " }", "}", ""])


def record_pack(views: dict[str, dict[str, dict[str, Any]]]) -> tuple[list[str], dict[str, dict[str, list[Any]]]]:
    """page_views' views as tests/page_words.js's pack() keeps them: every distinct text once, in the order first met."""
    strings: list[str] = []
    index: dict[str, int] = {}

    def ref(value: Any) -> int:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if text not in index:
            index[text] = len(strings)
            strings.append(text)
        return index[text]
    packed = {key: {name: [ref(r["text"]), ref(r["attrs"])] + ([None if r["gates"] is None else ref(r["gates"])] if "gates" in r else [])
                    for name, r in regions.items()} for key, regions in views.items()}
    return strings, packed


def test_the_records_differ_from_their_first_making_by_the_subtitle_titles_alone() -> None:
    """The one deliberate change to English's records (Chris on #98, 2026-11-27, attributes only): each Overview card's
    subtitle carries its full text in a title. Take every such title out of both records and each is, byte for byte, the
    file tests/page_words.js first made (FIRST_MAKING), its _about without the sentence that says so. Each title taken out
    sits on an Overview card, is a subtitle the card's text shows (en.json's English for one of the meta keys), and every
    card has its subtitle's: one on each card but the burn card (none) and the cost card (two where its daily chart shows
    a subtitle beside its title). In Gates, which draws no capitals of its own, each is the subtitle as the card shows it
    (gates.js re-cases the title with the words: the cost card's "Oct 1 - Nov 1 · at list prices", not app.js's "OCT 1")."""
    english = ENGLISH_CATALOG

    def pattern(key: str) -> re.Pattern[str]:
        return re.compile(".+".join(re.escape(part) for part in re.split(r"\{\w+\}", english[key])), re.S)

    shapes = {group: [pattern(key) for key in keys] for group, keys in SUBTITLE_KEYS.items()}
    daily = re.compile(r"daily · last \d+ days?(.)", re.I | re.S)
    for name, sha in FIRST_MAKING.items():
        path = Path(__file__).resolve().parent / "fixtures" / name
        record = json.loads(path.read_text(encoding="utf-8"))
        # the recorder's own form, whatever line ends the checkout gave the file (Git for Windows' autocrlf writes CRLF)
        assert path.read_bytes().replace(b"\r\n", b"\n") == record_text(record).encode("utf-8"), name
        assert len(record["_about"]) == 2 and "Chris on #98, 2026-11-27" in record["_about"][1], name
        views = page_views(record)
        taken = 0
        for key, regions in views.items():
            for region, r in regions.items():
                if not (key.endswith("|overview") and region.startswith("card:")):
                    continue                    # every other region as first made: the sha256 below holds it
                shown = (r["gates"] if r.get("gates") else r["text"]).casefold()
                added = [attr == "title" and bool(value) and value.casefold() in shown for attr, value in r["attrs"]]
                titles = [value for (_, value), new in zip(r["attrs"], added) if new]
                r["attrs"] = [attr for attr, new in zip(r["attrs"], added) if not new]
                taken += len(titles)
                group = "limit" if region.startswith("card:limit-") else region[5:]
                if group == "burn":
                    assert not titles, (name, key)
                    continue
                with_daily = group == "cost" and (found := daily.search(shown)) is not None and found.group(1) != "\u00a0"
                assert len(titles) == 1 + with_daily, (name, key, region, titles)
                assert any(shape.fullmatch(titles[0]) for shape in shapes[group]), (name, key, region, titles)
                assert not with_daily or any(shape.fullmatch(titles[1]) for shape in shapes["daily"]), (name, key, titles)
                # Gates draws no capitals of its own (gates.js re-cases the words, and the subtitle's title with them, #98's
                # third review): each title is the subtitle exactly as the card shows it, once the first state has come
                if key.split("|")[1].startswith("gates") and "gates" not in r:
                    assert all(title in r["text"] for title in titles), (name, key, region, titles)
        assert taken > 0, name
        strings, packed = record_pack(views)
        first = dict(record, _about=record["_about"][:1], strings=strings, views=packed)
        assert hashlib.sha256(record_text(first).encode("utf-8")).hexdigest() == sha, name


@ui_only
def test_the_page_shows_todays_english_for_the_answers_the_first_record_lacked(page_words: dict[str, Any]) -> None:
    """#98's review: a statistics answer whose text the page does not word (a query the monitor refused, a report it does not
    know) reads in English as the monitor sent it, as before #98, byte for byte in every look: the banners and the Limits
    cards' problem lines, against the second record; and so does the cost card of a plan the data layer names "Unknown
    plan"."""
    shown = page_views(page_words["more"])
    then = page_views(PAGE_WORDS_MORE)
    assert list(shown) == list(then)
    differences = [f"{key} {name}:\n  then {json.dumps(then[key].get(name), ensure_ascii=False)[:400]}\n"
                   f"  now  {json.dumps(shown[key].get(name), ensure_ascii=False)[:400]}"
                   for key in then for name in sorted(set(then[key]) | set(shown[key])) if then[key].get(name) != shown[key].get(name)]
    assert not differences, f"{len(differences)} regions differ:\n" + "\n".join(differences[:12])


@ui_only
def test_englishs_cards_carry_no_translate_mark(page_words: dict[str, Any]) -> None:
    """#98's review: in English a name inside a card is plain text, as before #98 (a span would split its text run and draw it
    a sub-pixel apart): no card of the record's pages holds an element marked translate. Another language marks a name
    translate="no" (tName, lineAttrs)."""
    assert page_words["marked"] == []


@ui_only
def test_the_shell_and_app_js_agree_before_the_first_state(page_words: dict[str, Any]) -> None:
    """#98's critic 10, 11 and 17: before the monitor has answered, the header shows index.html's own text (which the server
    fills from the same keys) in Nostromo and Night City, as the page opens and at its first 1 s tick, and the health box
    its words once that tick has drawn it; a first poll that fails says No link. Gates shows its own words from app.js's first
    render, as the page opens (the served shell's words, the header's keys in every look, show until app.js has run). Only
    the words: the link's mark stays as the shell has it until a state or a failed poll says how the link is."""
    en = ENGLISH_CATALOG
    first = page_words["first"]
    shell = first["shell"]
    assert (shell["uplink"], shell["age"], shell["health"]) == (en["header.link.loading"], en["header.age_none"], "")
    health = (en["nav.health.title"] + en["nav.health.link"] + "{link}" + en["nav.health.updated"] + "--" + en["nav.health.sources"]
              + "--" + en["nav.health.unpriced"] + "0")
    assert set(first["looks"]) == {look["name"] for look in PAGE_LOOKS}
    for look in PAGE_LOOKS:
        gates = look["theme"] == "gates"
        moments = first["looks"][look["name"]]
        loading = en["page.gates.link.loading"] if gates else shell["uplink"]
        none = en["page.gates.age_none"] if gates else shell["age"]
        lost = en["page.gates.link.lost" if gates else "header.link.lost"]
        for moment in ("opened", "tick"):
            shown = moments[moment]
            assert (shown["uplink"], shown["age"], shown["uplinkLang"], shown["ageLang"]) == (loading, none, None, None), (look, moment)
            assert (shown["uplinkClass"], shown["dotClass"]) == (shell["uplinkClass"], shell["dotClass"]), (look, moment)
        assert moments["opened"]["health"] == "" and moments["tick"]["health"] == health.replace("{link}", "--"), look
        failed = moments["failed"]
        assert (failed["uplink"], failed["age"], failed["health"]) == (lost, none, health.replace("{link}", lost)), look
        assert (failed["uplinkClass"], failed["dotClass"]) == ("nm-uplink is-down", "nm-alive is-off"), look


@ui_only
def test_each_themes_entrance_moves_every_word_of_what_it_animates(page_words: dict[str, Any]) -> None:
    """#98's critic 2: Nostromo's entrance types an element's first text node (FX.text.firstText) and Night City's decodes
    its own text nodes (FX.text.directText), so a word in a span of its own inside an animated element (a name, or English
    marked on another language's page) would show at once while the rest moves. Every element the two themes' directors
    name (read from their scripts) holds all its words in the text they animate: on Overview and Limits, live and before the
    first state, and on every page's heading, in English and on a French page showing English's words."""
    moving = page_words["entrance"]
    assert moving["untyped"] == []
    checked = moving["checked"]
    assert len(checked) == 2 * 2 * (len(PAGES) + 2)
    for key, counts in checked.items():
        look, _, case, page = key.split("|")
        assert counts[".nm-h1"] == 1 and (look != "nostromo" or counts[".nm-sub"] == 1), key
        if page in ("overview", "limits"):
            assert counts[".nm-tag, .nm-ttl"] >= 5, key
    for lang in ("en", "fr"):
        overview, limits = checked[f"nostromo|{lang}|live|overview"], checked[f"nostromo|{lang}|live|limits"]
        assert overview[".nm-mrow .k"] >= 7 and overview[".nm-meta-t"] >= 8 and limits[".nm-kv-grid .nm-kv b"] >= 20
        assert checked[f"night_city|{lang}|live|limits"][".nm-mrow .v, .nm-kv-grid .nm-kv b"] >= 30


@ui_only
def test_english_on_a_french_page_is_marked_on_overview_and_limits(page_words: dict[str, Any]) -> None:
    """#98: on a French page whose catalog has none of these words yet, every English word of Overview and Limits sits in
    an element marked lang="en" (a name in one marked translate="no"), live and before the first state, in Nostromo and
    Night City; what has no mark is the page's own French figures, dates and durations. Charts' SVG labels are cased by
    their own language (tUpper) and are left out."""
    unmarked = page_words["entrance"]["unmarked"]
    assert set(unmarked) == {f"{look}|fr|{case}|{page}" for look in ("nostromo", "night_city") for case in ("live", "before-state")
                             for page in ("overview", "limits")}
    texts = [value for value in ENGLISH_CATALOG.values() for value in (value.values() if isinstance(value, dict) else [value])]
    english = {word.lower() for text in texts for word in re.findall(r"[A-Za-z]{4,}", text)}
    for key, found in unmarked.items():
        assert found, key                                                   # the figures are there to read
        words = [text for text in found if {word.lower() for word in re.findall(r"[A-Za-z]{4,}", text)} & english]
        assert not words, (key, words)


MINI_DOM_JS = Path(__file__).resolve().parent / "mini_dom.js"
MINI_DOM_OBSERVED = r"""
const D = require(process.argv[1]);
const doc = D.createDocument('<html><body><div id="screen"><p id="p">One <b>two</b></p><i id="i">x</i></div><div id="off">z</div></body></html>');
const screen = doc.getElementById("screen");
const heard = [];
const name = (n) => (n.nodeType === 3 ? "#" + n.data : n.localName + (n.id ? "#" + n.id : ""));
const mo = new D.MutationObserver((records) => heard.push(records.map((r) => r.type + ":" + name(r.target) + ":" +
  r.addedNodes.map(name).join(",") + "/" + r.removedNodes.map(name).join(",") + (r.attributeName ? ":" + r.attributeName : ""))));
mo.observe(screen, { childList: true, subtree: true, characterData: true });
const attrs = [];
new D.MutationObserver((records) => attrs.push(records.map((r) => r.attributeName + "=" + r.oldValue))).observe(doc.documentElement,
  { attributes: true, attributeFilter: ["data-theme"] });
doc.getElementById("p").firstChild.nodeValue = "Uno ";
doc.getElementById("i").textContent = "y";
screen.querySelector("b").innerHTML = "<u>three</u>";
doc.getElementById("off").textContent = "unheard";
doc.documentElement.setAttribute("data-theme", "gates");
doc.documentElement.setAttribute("data-variant", "x");
const sync = heard.length;
const walker = doc.createTreeWalker(screen, D.NodeFilter.SHOW_TEXT, null);
const texts = [];
for (let n = walker.nextNode(); n; n = walker.nextNode()) { texts.push(n.data); }
const loose = doc.createElement("span");
Promise.resolve().then(() => {
  mo.disconnect();
  doc.getElementById("i").textContent = "after";
  return Promise.resolve();
}).then(() => {
  console.log(JSON.stringify({ sync, heard, attrs, texts, connected: [screen.isConnected, loose.isConnected, screen.firstChild.firstChild.isConnected] }));
});
"""


@ui_only
def test_the_small_dom_hears_changes_and_walks_text_as_a_browser_does() -> None:
    """tests/mini_dom.js (for tests/page_words.js, #98): a MutationObserver hears a text change, children added and removed
    and an attribute named in its filter, only under what it observes, in one batch a microtask later, and nothing once it
    is disconnected; createTreeWalker gives the text nodes in document order; isConnected says whether a node is in the page.
    gates.js rewords the page with exactly these."""
    run = subprocess.run([node_binary(), "-e", MINI_DOM_OBSERVED, str(MINI_DOM_JS)], capture_output=True, encoding="utf-8",
                         timeout=60, check=False, creationflags=NO_WINDOW)
    assert run.returncode == 0, run.stderr[-2000:]
    out = json.loads(run.stdout)
    assert out["sync"] == 0                                                             # nothing is heard synchronously
    # one batch, #off's change unheard; markup parsed into the page is heard level by level (a browser sends the one record
    # of <u>), which an observer that walks what was added reads the same
    assert out["heard"] == [["characterData:#Uno :/", "childList:i#i:/#x", "childList:i#i:#y/", "childList:b:/#two",
                             "childList:b:u/", "childList:u:#three/"]]
    assert out["attrs"] == [["data-theme=null"]]                                        # data-variant is not in the filter
    assert out["texts"] == ["Uno ", "three", "y"]
    assert out["connected"] == [True, False, True]


MINI_DOM_RAW_AFTER_DOTTED_I = r"""
const D = require(process.argv[1]);
const doc = D.createDocument('<html><body><p>' + "İ".repeat(12) + '</p><script src="a.js"></script><SCRIPT src="b.js">x<\/b' +
  '</Script><style>p{}</style><p id="after">z</p></body></html>');
console.log(JSON.stringify({ scripts: doc.querySelectorAll("script").map((s) => [s.getAttribute("src"), s.textContent]),
  style: doc.querySelector("style").textContent, after: doc.getElementById("after").textContent }));
"""


@ui_only
def test_the_small_dom_ends_a_script_where_its_closing_tag_is_after_a_dotted_capital_i() -> None:
    """tests/mini_dom.js (#98): a script's or a style's text ends at its own closing tag in any case, also after text whose
    lowercase is longer than it (Turkish's İ lowercases to two code units). The pseudo-locale's shell holds many, and the
    parser once found the end in a lowercased copy and lost the next script (charts.js, for the English-left scan)."""
    run = subprocess.run([node_binary(), "-e", MINI_DOM_RAW_AFTER_DOTTED_I, str(MINI_DOM_JS)], capture_output=True,
                         encoding="utf-8", timeout=60, check=False, creationflags=NO_WINDOW)
    assert run.returncode == 0, run.stderr[-2000:]
    assert json.loads(run.stdout) == {"scripts": [["a.js", ""], ["b.js", "x</b"]], "style": "p{}", "after": "z"}


GATES_SUBTITLE_TITLES = r"""
const fs = require("fs");
const vm = require("vm");
const D = require(process.argv[1]);
const doc = D.createDocument('<html lang="en" data-theme="gates"><head></head><body><div id="screen"><main id="main">' +
  '<section data-card="cost"><div class="nm-card-t"><h2 class="nm-ttl">Cost</h2><span class="nm-meta-t" id="range" ' +
  'title="OCT 1 - NOV 1 · at list prices">OCT 1 - NOV 1 · at list prices</span></div></section>' +
  '<section data-card="limit-session"><div class="nm-card-t"><span class="nm-tag" id="tag" title="SESSION">SESSION</span>' +
  '<span class="nm-meta-t" id="window" title="5-hour window">5-hour window</span></div></section>' +
  '<section data-card="surfaces"><div class="nm-card-t"><span class="nm-meta-t" id="bare">SHARE, 7 DAYS</span></div></section>' +
  '</main></div></body></html>');
const box = { document: doc, MutationObserver: D.MutationObserver, NodeFilter: D.NodeFilter, WeakRef, console,
  fetch: () => Promise.reject(new Error("no server here")) };
Object.setPrototypeOf(box, D.Target.prototype);
box.window = box;
box.self = box;
doc.defaultView = box;
vm.createContext(box);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), box, { filename: "gates.js" });
const read = () => Object.fromEntries(["range", "tag", "window", "bare"].map((id) => {
  const el = doc.getElementById(id);
  return [id, [el.textContent, el.getAttribute("title")]];
}));
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const out = {};
(async () => {
  box.USAGE_MONITOR_THEME_FX.gates.start();
  out.started = read();
  // app.js draws the card again: its new subtitle comes in capitals, its title with it
  doc.querySelector('[data-card="cost"] .nm-card-t').innerHTML = '<h2 class="nm-ttl">Cost</h2><span class="nm-meta-t" id="range" ' +
    'title="SEP 1 - OCT 1 · at list prices">SEP 1 - OCT 1 · at list prices</span>';
  await tick();
  out.redrawn = read();
  // the subtitle's words change in place, the title this script gave it still on it
  doc.getElementById("range").firstChild.nodeValue = "AUG 1 - SEP 1 · at list prices";
  await tick();
  out.changed = read();
  doc.documentElement.setAttribute("data-theme", "nostromo");     // another look: every word and title as app.js wrote it
  await tick();
  out.left = read();
  doc.documentElement.setAttribute("data-theme", "gates");
  await tick();
  out.back = read();
  doc.documentElement.setAttribute("data-theme", "nostromo");
  await tick();
  out.leftAgain = read();
  console.log(JSON.stringify(out));
})();
"""


@ui_only
def test_gates_gives_an_overview_subtitles_title_the_words_it_shows_and_puts_both_back() -> None:
    """#98's third review: every Overview card subtitle carries its whole text in a title (Chris on #98, 2026-11-27), and in
    Gates the title says what the card shows. gates.js re-cases a subtitle's words ("OCT 1 - NOV 1" -> "Oct 1 - Nov 1"), so it
    gives the title the same words, again whenever app.js draws the subtitle anew or changes its words; when another look
    takes over, the words and the title app.js wrote come back (a title gates.js set is never what it puts back). A subtitle
    whose words it leaves alone keeps its title; it adds no title, and re-cases no title but a subtitle's."""
    run = subprocess.run([node_binary(), "-e", GATES_SUBTITLE_TITLES, str(MINI_DOM_JS), str(WEBUI / "themes" / "gates.js")],
                         capture_output=True, encoding="utf-8", timeout=60, check=False, creationflags=NO_WINDOW)
    assert run.returncode == 0, run.stderr[-2000:]
    out = json.loads(run.stdout)
    rest = {"tag": ["Session", "SESSION"], "window": ["5-hour window", "5-hour window"], "bare": ["Share, 7 days", None]}
    app = {"tag": ["SESSION", "SESSION"], "window": ["5-hour window", "5-hour window"], "bare": ["SHARE, 7 DAYS", None]}
    shown = "Oct 1 - Nov 1 · at list prices"
    assert out["started"] == dict(rest, range=[shown, shown])
    shown = "Sep 1 - Oct 1 · at list prices"
    assert out["redrawn"] == dict(rest, range=[shown, shown])
    shown = "Aug 1 - Sep 1 · at list prices"
    assert out["changed"] == dict(rest, range=[shown, shown])
    assert out["left"] == dict(app, range=["AUG 1 - SEP 1 · at list prices", "SEP 1 - OCT 1 · at list prices"])
    assert out["back"] == out["changed"]
    assert out["leftAgain"] == out["left"]


CASE_RESET_JS = r"""
const C = require(process.argv[1]);
const D = require(process.argv[2]);
const rules = C.caseRules(process.argv[3], "t");
const doc = D.createDocument('<html><body><div class="bar"><span id="s">a</span><button id="b">b<span id="bs">c</span></button>' +
  '<button class="up" id="u">d</button><input id="i"><select id="sel"></select><textarea id="ta"></textarea></div></body></html>');
const out = {};
for (const id of ["s", "b", "bs", "u", "i", "sel", "ta"]) { out[id] = C.caseOf(doc.getElementById(id), rules, new Map()).value; }
out.shown = C.shownIn("Refresh", out.u);
console.log(JSON.stringify(out));
"""


@ui_only
def test_the_case_check_knows_the_browsers_reset_of_buttons_and_fields(tmp_path: Path) -> None:
    """tests/css_case.js (#98's critic 21): a button, an input, a select and a textarea take the browser's own
    text-transform: none, not their parent's, unless a rule names them; what is inside a button takes the button's. So a
    capitals rule on the header's right side does not pass for capitals on its Refresh button."""
    (tmp_path / "themes").mkdir()
    (tmp_path / "themes" / "t.css").write_text(".bar { text-transform: uppercase; }\n.up { text-transform: uppercase; }\n",
                                               encoding="utf-8")
    for sheet in ("motion.css", "themes/nostromo.motion.css", "themes/night_city.motion.css", "themes/gates.motion.css"):
        (tmp_path / sheet).write_text("", encoding="utf-8")
    run = subprocess.run([node_binary(), "-e", CASE_RESET_JS, str(Path(__file__).resolve().parent / "css_case.js"),
                          str(MINI_DOM_JS), str(tmp_path)], capture_output=True, encoding="utf-8", timeout=60, check=False,
                         creationflags=NO_WINDOW)
    assert run.returncode == 0, run.stderr[-2000:]
    assert json.loads(run.stdout) == {"s": "uppercase", "b": "none", "bs": "none", "u": "uppercase", "i": "none", "sel": "none",
                                      "ta": "none", "shown": "REFRESH"}
