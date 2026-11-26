"""The "no English left" scan (#89; tests/english_left.py): under the pseudo-locale, the widgets draw and the dashboard
shows the English tests/fixtures/english_left.json lists, no more and no less. More means English text that did not go
through the catalog. Less means an extraction ticket moved some into it, and strikes those lines with
``python tests/english_left.py --record``, which adds nothing unless told to, so the list only shrinks, to nothing
after L16 (#101).

The widgets' scan runs the frame dump in a fresh Python, as the golden frames do. The dashboard's runs node and the
dev server, so it needs USAGE_MONITOR_UI_TESTS=1, like every test that starts node."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

import english_left
from english_left import (DASHBOARD_MARKS, PRODUCT, WIDGET_MARKS, english, entry, html_blocks, html_english, html_texts,
                          scan_english)
from usage_monitor import i18n

UI_TESTS = os.environ.get("USAGE_MONITOR_UI_TESTS") == "1"
ui_only = pytest.mark.skipif(not UI_TESTS, reason="needs USAGE_MONITOR_UI_TESTS=1 (starts node and the dev server)")
STRIKE = "python tests/english_left.py --record"
P = i18n.pseudo_text


def node() -> str:
    found = shutil.which("node")
    if found is None:
        if os.environ.get("CI"):
            pytest.fail("node is not installed on this CI runner, so the dashboard cannot be scanned")
        pytest.skip("node is not installed")
    return found


def same_as_record(half: str, now: dict[str, str]) -> None:
    was = english_left.recorded()[half]
    new = sorted(set(now) - set(was))
    assert not new, (f"{len(new)} English {half} texts the record does not have. Put new text through the catalog. If "
                     f"the English was only reworded, or the dev server's data changed, record with {STRIKE} {half} "
                     f"--allow-new. Each with where it was first seen: " + "; ".join(f"{t!r} at {now[t]}" for t in new[:30]))
    gone = sorted(set(was) - set(now))
    assert not gone, f"{len(gone)} recorded {half} texts are no longer English: strike them ({STRIKE} {half}): {gone[:40]}"


# ── The scans against the record ─────────────────────────────────────────────────────────────────────────────────

def test_the_widgets_draw_only_the_english_on_record() -> None:
    same_as_record("widgets", english_left.widgets())


@ui_only
def test_the_dashboard_shows_only_the_english_on_record() -> None:
    node()
    same_as_record("dashboard", english_left.dashboard())


@ui_only
def test_the_update_run_passes_reach_every_phase(tmp_path: Path) -> None:
    """#58: each update-run pass offers a release the monitor installs itself (with the check's info, so Settings has one on
    offer) and answers GET /api/update/status with one state of a run; Settings, opened, shows that state at its first ask.
    So the scan above sees every text of the update's block, in the pseudo-locale. Lost and gone need three timed asks, and
    the scan's timers never fire: the page tests hold those."""
    node()
    import update_check

    runs = [name for name in english_left.PASSES["normal"] if name.startswith("update-run:")]
    assert runs == [f"update-run:{state}" for state in english_left.UPDATE_RUNS]
    assert {state[len("failed-"):] for state in english_left.UPDATE_RUNS if state.startswith("failed-")} == set(update_check.REASONS)
    shown = {s["pass"]: s["body"] for s in english_left.scan_dashboard("normal", runs, tmp_path)["snapshots"]
             if s["step"] == "settings"}
    steps = {"downloading": ["now", "next", "next"], "unknown": ["now", "next", "next"], "connecting": ["now", "next", "next"],
             "checking": ["done", "now", "next"], "starting": ["done", "done", "now"], "restarting": ["done", "done", "now"],
             "stalled": ["stalled", "next", "next"]}
    failed_in = {"digest": ["done", "failed", "next"], "helper": ["done", "done", "failed"]}
    for state in english_left.UPDATE_RUNS:
        body = shown[f"update-run:{state}"]
        block = re.search(r'<div class="(nm-uprog[^"]*)" id="set-update-progress"([^>]*)>', body)
        assert block and "hidden" not in block.group(2), state
        code = state[len("failed-"):] if state.startswith("failed-") else ""
        wanted = steps.get(state) or failed_in.get(code, ["failed", "next", "next"])
        assert re.findall(r'data-step="(\w+)"', body) == wanted, state
        assert block.group(1) == ("nm-uprog is-crit" if code else "nm-uprog is-warn" if state == "stalled" else "nm-uprog"), state
        line = re.search(r'data-part="line"[^>]*>(.*?)</p>', body).group(1)
        assert line.startswith("[") and line.endswith("]"), (state, line)              # the pseudo-locale's, not English
    assert "data-pending" in shown["update-run:unknown"] and "data-pending" in shown["update-run:connecting"]
    assert 'class="nm-cells hot"' in shown["update-run:failed-network"]


def test_the_record_holds_sorted_entries_each_of_them_english() -> None:
    record = english_left.recorded()
    assert set(record) == set(english_left.HALVES)
    for half, texts in record.items():
        assert texts == sorted(set(texts)), half
        assert [t for t in texts if entry(t) != t] == [], half                 # written as the scan writes them
        assert english(texts, ()) == set(texts), half                          # each is English on its own


# ── Telling English from the pseudo-locale ───────────────────────────────────────────────────────────────────────

def test_a_word_the_pseudo_locale_made_is_not_english_and_a_plain_one_is() -> None:
    assert english([P("Resets {time}"), P("Weekly"), "42%", "1,234.5", "—", "4:12", "x", "RESETS 4:12 PM"]) == {
        "RESETS #:## PM"}
    assert english_left.MAPPED_LETTERS == frozenset("aceginosuyACEGINOSUY")
    assert set("ıİğşéñ") <= english_left.PSEUDO_LETTERS and not english_left.PSEUDO_LETTERS & set("[] abcXYZ")


def test_english_with_accents_and_english_beside_a_pseudo_string_are_found() -> None:
    mixed = P("Resets") + " weekly 2%"
    assert english(["Café usage", "Señor", "Über", P("Cafe"), mixed, "Session " + P("Resets")]) == {
        "Café usage", "Señor", "Über", entry(mixed), entry("Session " + P("Resets"))}


def test_a_word_that_reads_the_same_both_ways_is_english_only_outside_a_pseudo_string() -> None:
    """"MB" and "PM" have no letter pseudo_text changes. Inside a pseudo string, even one markup split, they are its own
    words; anywhere else they are English."""
    split = "<p>" + P("{n} MB").replace("{n}", "<b>12</b>") + "</p>"
    runs = html_texts(split, "pseudo")
    assert runs[:2] == ["[", "12"] and runs[2].startswith("MB ") and html_english(split, "pseudo", ()) == set()
    assert html_english("<p>Total <b>12</b> MB</p>", "pseudo", ()) == {"Total", "MB"}
    assert english(["MB", "PM", "[MB]"]) == {"MB", "PM", "[MB]"}              # brackets with no pseudo word in them


def test_a_bracketed_string_cut_with_an_ellipsis_is_the_pseudo_locales() -> None:
    """A widget cuts a long word with an ellipsis (#94's fits), and the letters a cut leaves of a pseudo word can read
    the same both ways ("[THRÖTTLÉD" cut to "[THR…"). A run that ends inside its brackets with the ellipsis was cut
    from pseudo_text's string, so its words are the pseudo-locale's; English, and a word outside brackets or in closed
    ones, is still found."""
    cut = P("THROTTLED")[:4] + "…"
    assert cut == "[THR…" and english([cut]) == set()
    assert english(["THR…", "[THR…]", "[GUA…"]) == {"THR…", "[THR…]", "[GUA…"}


def test_a_bracketed_string_cut_with_dots_is_the_pseudo_locales() -> None:
    """Night City cuts with three dots, its own mark (#95), where the other skins cut with an ellipsis: a run that
    ends inside its brackets with them was cut too. English, and a word outside brackets or in closed ones, is still
    found."""
    cut = P("THROTTLED")[:4] + "..."
    assert cut == "[THR..." and english([cut]) == set()
    assert english(["THR...", "[THR...]", "[GUA..."]) == {"THR...", "[THR...]", "[GUA..."}


def test_a_name_a_cut_ends_inside_is_a_name() -> None:
    """A Night City band cuts its cause where the room ends, which can be inside the product's name (#95): "CLAUDE
    CO..." is the start of a name, not English. Only the name's start at a word's start goes, and only before a cut
    mark: English before it, a word that only looks like a name's start, and a name cut with no mark are found."""
    names = ("CLAUDE CODE",)
    cause = P("RELINK // USE {product}").replace("{product}", "CLAUDE CODE")
    for size in range(1, len("CLAUDE CODE")):
        cut = cause[:cause.index("CLAUDE") + size] + "..."
        assert english([cut], names) == set(), cut
    assert english(["USE CLAUDE CO...", "[ÜŞÉ XCLAUDE...", "[ÜŞÉ CLAUDE CO", "[ÜŞÉ CLAUDE CO…]"], names) == {
        "USE CLAUDE CO...", "[ÜŞÉ XCLAUDE...", "[ÜŞÉ CLAUDE CO", "[ÜŞÉ CLAUDE CO…]"}


def test_digits_fold_and_spaces_collapse() -> None:
    assert english(["Resets 4:12\u202fPM", "Resets  4:59 PM", " Resets 11:59 PM "]) == {"Resets #:## PM",
                                                                                         "Resets ##:## PM"}


def test_the_pseudo_locales_number_shapes_are_englishs_entries() -> None:
    """#91 (D15 of its plan): the page writes the pseudo-locale's numbers with U+202F groups and a U+00A0 before "%",
    which fold to English's entries, so the record never grows for them; a U+202F that is not between digits, and a
    U+00A0 that is not before "%", stay spaces."""
    assert english(["Session 34\u00a0% used", "1\u202f234 requests", "1\u202f234\u202f567 tokens"]) == {
        "Session ##% used", "#,### requests", "#,###,### tokens"}
    assert english(["Session 34% used", "1,234 requests"]) == {"Session ##% used", "#,### requests"}
    assert english(["Resets 4:12\u202fPM", "Used 34\u00a0of 50"]) == {"Resets #:## PM", "Used ## of ##"}


def test_a_wrapped_percent_folds_as_a_glued_one() -> None:
    """A widget's wrap gives the pseudo-locale's U+00A0 before "%" back as a plain space (engine.wrap_words), so a
    digit, a space and "%" are the entry a digit and "%" are (#93). A space before "%" with no digit before it stays."""
    assert entry("SESSION 13 %") == entry("SESSION 13\u00a0%") == entry("SESSION 13%") == "SESSION ##%"
    assert entry("(CAP 90 %). RESUMES") == "(CAP ##%). RESUMES"
    assert entry("CAP %") == "CAP %" and entry("13  %") == "## %"


def test_names_are_taken_out_before_a_run_is_judged() -> None:
    dashboard = (PRODUCT, *DASHBOARD_MARKS)
    assert english(["Claude Usage Monitor", "CLAUDE USAGE MONITOR", "// ONBOARD COMPUTER"], dashboard) == set()
    assert english(["About Claude Usage Monitor", "Claude usage"], dashboard) == {"About Claude Usage Monitor",
                                                                                   "Claude usage"}   # a widget mark
    widget = (PRODUCT, *WIDGET_MARKS, *english_left.skin_names())
    assert {"Claude Code", "CLAUDE CODE", "Fable", "FABLE"} <= set(widget)    # the sample states' scoped model
    assert english(["FABLE", "CLAUDE//USAGE", "Send Claude Code a message.", "FABLE WEEKLY"], widget) == {
        "Send Claude Code a message.", "FABLE WEEKLY"}


def test_the_pseudo_locales_format_words_and_the_dev_mark_are_no_english_on_the_widgets() -> None:
    """#91 gives the pseudo-locale English's formats, so its weekday names and AM/PM are English's, and Gates' age
    unit "min" (#96): the widget half treats them as formats, as it treats numbers, and takes them out as whole words
    only. The dev instance's "(dev)" is a developer's mark (#93). The dashboard half's record keeps the date names the
    page shows."""
    from usage_monitor import app

    days = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    words = english_left.pseudo_format_words()
    assert set(words) == {*days, *(day.upper() for day in days), "AM", "PM", "min"}
    names = english_left.widget_names()
    assert english_left.DEV_MARK == app.DEV_MARK.strip() and english_left.DEV_MARK in names
    held = P("guard holding, resumes {clock} (in {countdown})").replace("{clock}", "Fri 9:59 PM").replace(
        "{countdown}", "2d 3h")
    assert english([held, P("RESETS {clock}").replace("{clock}", "SAT 4:00 AM")], names, words) == set()
    assert english([held], names) == {entry(held)}                         # without the format words it is English
    assert english([P("live") + " (dev)"], names, words) == set()
    assert english([P("stale") + " · 41 min"], names, words) == set()                 # Gates' pill beside its age
    assert english([P("stale") + " · 41 min"], names, [w for w in words if w != "min"]) == {
        entry(P("stale") + " · 41 min")}
    assert english(["minute", "admin"], names, words) == {"minute", "admin"}
    assert english([P("live") + " (dev)"], (PRODUCT, *WIDGET_MARKS), words) == {entry(P("live") + " (dev)")}
    assert english(["AMP", "Monk", "SUNK", "Friday", P("weekly") + " Monday"], names, words) == {
        "AMP", "Monk", "SUNK", "Friday", entry(P("weekly") + " Monday")}    # whole words only
    page = {"shell": '<html lang="pseudo"><body></body></html>',
            "snapshots": [{"pass": "dev", "step": "cost", "title": "", "body": "<body><p>FRI</p></body>"}]}
    assert scan_english(page) == {"FRI": "dev/cost"}                        # the dashboard half is as it was


def test_the_widget_half_draws_in_the_pseudo_locale_and_reads_the_menu(monkeypatch: pytest.MonkeyPatch) -> None:
    """The frames, every sample state's tooltip (plain and as a dev instance) and the tray menu with every row are read
    in the pseudo-locale a dev instance shows (#93)."""
    from usage_monitor import app
    from usage_monitor.render import dump

    pseudo = i18n.dev_locale(i18n.PSEUDO)
    dumped: list[Any] = []
    tips: list[tuple[str, bool]] = []
    menus: list[tuple[Any, int]] = []
    real_tip, real_menu = app.tray_tooltip, app.menu_items

    def dump_frames(folder: Any, **options: Any) -> None:
        dumped.append(options.get("locale"))

    def tray_tooltip(model: Any, **options: Any) -> str:
        tips.append((model.locale, options.get("dev", False)))
        return real_tip(model, **options)

    def menu_items(**options: Any) -> Any:
        menus.append((options.get("locale"), options["waiting"]))
        return real_menu(**options)

    monkeypatch.setattr(dump, "dump_frames", dump_frames)
    monkeypatch.setattr(app, "tray_tooltip", tray_tooltip)
    monkeypatch.setattr(app, "menu_items", menu_items)
    seen = english_left._drawn()
    assert dumped == [pseudo]
    assert sorted(tips, key=repr) == sorted([(pseudo, dev) for _ in dump.DUMP_STATE_NAMES for dev in (False, True)],
                                            key=repr)
    assert menus and all(locale == pseudo for locale, _waiting in menus) and {w for _l, w in menus} == {1, 2}
    rows = {item.id: item.label for waiting in (1, 2) for item in real_menu(
        floating_on=True, taskbar_on=True, guard_on=True, waiting=waiting, update_version="2.1.0",
        **{name: (lambda: None) for name in ("open_dashboard", "toggle_floating", "toggle_taskbar", "toggle_guard",
                                             "resume_waiting", "refresh", "report_bug", "update", "quit_app")},
        locale=pseudo) if not item.separator}
    assert {"resume_waiting", "update", "exit"} <= set(rows)
    assert all(seen.get(label) == "tray menu" for label in rows.values())
    assert any(where.endswith(", dev") for where in seen.values())


# ── Reading the dashboard's markup ───────────────────────────────────────────────────────────────────────────────

def test_markup_marked_as_data_is_skipped_with_all_inside_it() -> None:
    markup = ('<html lang="pseudo"><head><title>Claude Usage Monitor</title><style>p{}</style></head><body>'
              '<p title="Copy">Calls <span lang="en" translate="no">Bash <b>Read</b></span> today<br/>'
              '<code class="nm-path" lang="en" translate="no">claude update</code><input placeholder="Search">'
              '<img alt="Logo"><span translate="no">v2.4.0</span><span lang="PSEUDO">[Şéşşıöñ]</span>'
              '<span lang>Weekly</span><span lang="">Session</span><script>var x = "never";</script></p></body></html>')
    assert html_texts(markup, "pseudo") == ["Claude Usage Monitor", "Copy", "Search", "Logo", "Calls", "today",
                                            "[Şéşşıöñ]", "Weekly", "Session"]


def test_end_tags_html_leaves_out_do_not_hide_what_follows() -> None:
    for markup in ('<ul><li translate="no">a<li>Visible English</ul>',
                   '<select><option translate="no">A<option>Visible English</select>',
                   '<table><tr><td translate="no">A<td>Visible English</table>',
                   '<dl><dt translate="no">A<dd>Visible English</dl>',
                   '<p translate="no">A<div>Visible English</div>'):
        assert html_texts(markup, "pseudo") == ["Visible English"], markup


def test_a_block_holds_its_inline_runs_and_an_attribute_is_a_block_of_its_own() -> None:
    assert html_blocks('<p>Hi <b>there</b>, <a title="Go">you</a></p><div>Next<svg><text>A</text></svg></div>') == [
        ["Go"], ["Hi", "there", ",", "you"], ["Next"], ["A"]]


def test_a_run_of_the_page_is_read_with_its_title_and_each_snapshot() -> None:
    run = {"shell": '<html lang="pseudo"><head><title>Claude Usage Monitor</title></head><body><p>ACQUIRING</p>'
                    '</body></html>',
           "snapshots": [{"pass": "installed", "step": "cost", "title": "Claude Usage Monitor",
                          "body": '<body><p>LIVE</p><p translate="no">C:\\Dev\\x</p><p>Claude usage</p></body>'},
                         {"pass": "dev", "step": "about", "title": "Claude Usage Monitor", "body": "<body><p>LIVE</p>"}]}
    assert scan_english(run) == {"ACQUIRING": "as served", "LIVE": "installed/cost", "Claude usage": "installed/cost"}


# ── The record ───────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def scratch_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, str]]:
    """The record in tmp_path, with scans that answer what the test puts in ``now``."""
    path = tmp_path / "english_left.json"
    path.write_text(json.dumps({"widgets": ["OLD", "SESSION"], "dashboard": ["About"]}), encoding="utf-8")
    now: dict[str, dict[str, str]] = {"widgets": {"SESSION": "here"}, "dashboard": {"About": "here"}}
    monkeypatch.setattr(english_left, "RECORD", path)
    monkeypatch.setattr(english_left, "widgets", lambda: now["widgets"])
    monkeypatch.setattr(english_left, "dashboard", lambda: now["dashboard"])
    return now


def test_recording_strikes_what_is_gone_and_adds_nothing_unless_told(scratch_record: dict[str, dict[str, str]],
                                                                    capsys: pytest.CaptureFixture[str]) -> None:
    assert english_left.main(["--record", "widgets"]) == 0
    assert english_left.recorded() == {"widgets": ["SESSION"], "dashboard": ["About"]}       # OLD struck, the rest kept
    scratch_record["widgets"] = {"SESSION": "here", "NEW WORDS": "nostromo/amber/normal3 floating"}
    assert english_left.main(["--record"]) == 1
    out = capsys.readouterr().out
    assert "  new: NEW WORDS" in out and "Nothing written" in out
    assert english_left.recorded()["widgets"] == ["SESSION"]
    assert english_left.main(["--record", "widgets", "--allow-new"]) == 0
    assert english_left.recorded() == {"widgets": ["NEW WORDS", "SESSION"], "dashboard": ["About"]}
    assert english_left.main(["--record", "--sideways"]) == 2


def test_the_command_line_scans_only_through_the_pinned_runner(monkeypatch: pytest.MonkeyPatch,
                                                              capsys: pytest.CaptureFixture[str]) -> None:
    seen: list[str] = []
    monkeypatch.setattr(english_left, "_pinned", lambda half: seen.append(half) or {"WORD": "there"})
    assert english_left.main(["widgets"]) == 0 and seen == ["widgets"]
    assert json.loads(capsys.readouterr().out) == {"WORD": "there"}


def test_the_pinned_runner_runs_this_file_with_the_time_zone_pinned(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], str, int]] = []

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs["env"]["TZ"], kwargs["creationflags"]))
        return subprocess.CompletedProcess(args, 0, stdout='{"A": "b"}', stderr="")

    monkeypatch.setattr(english_left.subprocess, "run", run)
    assert english_left._pinned("dashboard") == {"A": "b"}
    assert calls[0][0][-2:] == ["--here", "dashboard"] and calls[0][1:] == ("UTC0", english_left.NO_WINDOW)


# ── The small DOM the dashboard's scan runs on ───────────────────────────────────────────────────────────────────

DOM_CHECKS = r"""
const D = require(process.argv[1]);
const doc = D.createDocument('<!doctype html><html lang="x"><head><title>T &amp; U</title></head><body>' +
  '<div id="main" class="a b"><p title="x &quot;y&quot;">Hi <b>there</b><ul><li translate="no">a<li data-k="v w">b</ul>' +
  '<select id="s"><option value="1">One<option value="2" selected>Two</select><input type="checkbox" data-key="k">' +
  '<span data-lbl-for="k">OFF</span><svg viewBox="0 0 1 1"><linearGradient id="g"/><text x="1">LBL</text></svg>' +
  '<template id="t"></template></div><script>var a = "<b>";</script></body></html>', { width: 640 });
const main = doc.getElementById("main");
const out = { title: doc.title, items: main.querySelectorAll("li").length, quoted: main.querySelector('[data-k="v w"]').textContent,
  descendant: doc.querySelector("#main .a"), child: !!doc.querySelector("div.a.b > p"), list: doc.querySelectorAll("#main li, #s option").length };
const s = doc.getElementById("s"); out.select = [s.value, s.selectedIndex]; s.value = "1"; out.select.push(s.value);
const t = doc.getElementById("t"); t.innerHTML = '<section data-state="ready"><h2>Card</h2></section>';
out.template = t.content.firstElementChild.tagName;
const box = main.querySelector('input[data-key="k"]'); box.click(); out.checked = box.checked;
const heard = []; main.addEventListener("click", (e) => heard.push(e.target.tagName + ">" + e.currentTarget.id));
main.querySelector("b").click(); out.heard = heard;
out.svg = main.querySelector("svg").innerHTML; out.hover = main.matches(":hover");
out.not = main.querySelector("li:not([translate])").textContent;
main.querySelector('[data-lbl-for="k"]').textContent = "ON <b>"; out.written = main.querySelector('[data-lbl-for="k"]').outerHTML;
out.script = doc.querySelector("script").textContent;
try { doc.querySelector("a!b"); out.bad = "read"; } catch (e) { out.bad = e.name; }
console.log(JSON.stringify(out));
"""


@ui_only
def test_the_small_dom_reads_writes_finds_and_dispatches_as_a_browser_does() -> None:
    run = subprocess.run([node(), "-e", DOM_CHECKS, str(Path(__file__).parent / "mini_dom.js")], capture_output=True,
                         encoding="utf-8", timeout=60, check=False, creationflags=english_left.NO_WINDOW)
    assert run.returncode == 0, run.stderr[-2000:]
    out: dict[str, Any] = json.loads(run.stdout)
    assert out == {"title": "T & U", "items": 2, "quoted": "b", "descendant": None, "child": True, "list": 4,
                   "select": ["2", 1, "1"], "template": "SECTION", "checked": True, "heard": ["B>main"],
                   "svg": '<linearGradient id="g"></linearGradient><text x="1">LBL</text>', "hover": False, "not": "b",
                   "written": '<span data-lbl-for="k">ON &lt;b&gt;</span>', "script": 'var a = "<b>";',
                   "bad": "SyntaxError"}
