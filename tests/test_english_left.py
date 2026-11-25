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


def test_names_are_taken_out_before_a_run_is_judged() -> None:
    dashboard = (PRODUCT, *DASHBOARD_MARKS)
    assert english(["Claude Usage Monitor", "CLAUDE USAGE MONITOR", "// ONBOARD COMPUTER"], dashboard) == set()
    assert english(["About Claude Usage Monitor", "Claude usage"], dashboard) == {"About Claude Usage Monitor",
                                                                                   "Claude usage"}   # a widget mark
    widget = (PRODUCT, *WIDGET_MARKS, *english_left.skin_names())
    assert {"Claude Code", "CLAUDE CODE", "Fable", "FABLE"} <= set(widget)    # the sample states' scoped model
    assert english(["FABLE", "CLAUDE//USAGE", "Send Claude Code a message.", "FABLE WEEKLY"], widget) == {
        "Send Claude Code a message.", "FABLE WEEKLY"}


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
