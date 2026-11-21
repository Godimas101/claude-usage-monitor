"""The "no English left" scan (#89; tests/english_left.py): under the pseudo-locale, the widgets draw and the dashboard
shows the English tests/fixtures/english_left.json lists, no more and no less. More means English text that did not go
through the catalog. Less means an extraction ticket moved some into it, and strikes those lines by recording again
(``python tests/english_left.py --record``), so the list only shrinks, to nothing after L16 (#101).

The widgets' scan runs the frame dump in a fresh Python, like the golden frames. The dashboard's starts node and the dev
server, so it needs USAGE_MONITOR_UI_TESTS=1 like every test that starts node."""
from __future__ import annotations

import os
import shutil

import pytest

import english_left
from english_left import BRAND, english, html_texts, page_texts

UI_TESTS = os.environ.get("USAGE_MONITOR_UI_TESTS") == "1"
ui_only = pytest.mark.skipif(not UI_TESTS, reason="needs USAGE_MONITOR_UI_TESTS=1 (starts node and the dev server)")
RECORD_AGAIN = "python tests/english_left.py --record"


def same_as_record(kind: str, now: list[str]) -> None:
    was = english_left.recorded()[kind]
    new = sorted(set(now) - set(was))
    assert not new, (f"{len(new)} English {kind} texts the record does not have. Put new text through the catalog; if "
                     f"the English was only reworded, or the dev server's data changed, record again ({RECORD_AGAIN}): "
                     f"{new[:40]}")
    gone = sorted(set(was) - set(now))
    assert not gone, f"{len(gone)} recorded {kind} texts are no longer English: strike them ({RECORD_AGAIN}): {gone[:40]}"


def test_the_widgets_draw_only_the_english_on_record() -> None:
    same_as_record("widgets", english_left.widgets())


@ui_only
def test_the_dashboard_shows_only_the_english_on_record() -> None:
    if shutil.which("node") is None:
        if os.environ.get("CI"):
            pytest.fail("node is not installed on this CI runner, so the dashboard cannot be scanned")
        pytest.skip("node is not installed")
    same_as_record("dashboard", english_left.dashboard())


def test_the_record_is_sorted_english_without_pseudo_text() -> None:
    record = english_left.recorded()
    assert set(record) == {"widgets", "dashboard"}
    for kind, texts in record.items():
        assert texts == sorted(set(texts)), kind
        assert not [t for t in texts if set(t) & english_left.PSEUDO_LETTERS or "  " in t or t != t.strip()], kind
        assert english(texts, ()) == set(texts), kind                            # each entry is itself English


def test_pseudo_text_and_numbers_are_not_english() -> None:
    from usage_monitor import i18n

    assert english([i18n.pseudo_text("Resets {time}"), "42%", "1,234.5", "—", "4:12", "x", "RESETS 4:12 PM"]) == {
        "RESETS #:## PM"}
    assert set("ıİğşéñ") <= english_left.PSEUDO_LETTERS and not english_left.PSEUDO_LETTERS & set("[] abcXYZ")


def test_digits_fold_and_spaces_collapse() -> None:
    assert english(["Resets 4:12 PM", "Resets  4:59 PM", " Resets 11:59 PM "]) == {"Resets #:## PM",
                                                                                         "Resets ##:## PM"}


def test_names_are_taken_out_before_a_run_is_judged() -> None:
    assert english(["Claude Usage Monitor", "CLAUDE USAGE MONITOR", "// ONBOARD COMPUTER", "CLAUDE//USAGE"]) == set()
    assert english(["About Claude Usage Monitor"]) == {"About Claude Usage Monitor"}     # what is left is English
    names = BRAND + english_left.skin_names()
    assert {"Claude Code", "CLAUDE CODE", "Fable", "FABLE"} <= set(names)                # the sample states' scoped model
    assert english(["FABLE", "Send Claude Code a message.", "FABLE WEEKLY"], names) == {"Send Claude Code a message.",
                                                                                       "FABLE WEEKLY"}


def test_markup_marked_as_data_is_skipped_with_all_inside_it() -> None:
    markup = ('<html lang="pseudo"><head><title>Claude Usage Monitor</title><style>p{}</style></head><body>'
              '<p title="Copy">Calls <span lang="en" translate="no">Bash <b>Read</b></span> today<br/>'
              '<code class="nm-path" lang="en" translate="no">claude update</code><input placeholder="Search">'
              '<img alt="Logo"><span translate="no">v2.4.0</span><span lang="pseudo">[Şéşşıöñ]</span>'
              '<script>var x = "never";</script></p></body></html>')
    assert html_texts(markup, "pseudo") == ["Claude Usage Monitor", "Copy", "Calls", "today", "Search", "Logo",
                                            "[Şéşşıöñ]"]


def test_what_app_js_writes_is_read_unless_its_element_is_data() -> None:
    run = {"shell": '<html lang="pseudo"><span id="version" translate="no"></span><span id="uplink"></span></html>',
           "title": "Claude Usage Monitor", "pages": {"about": ['<p id="age">x</p>']},
           "writes": [{"id": "version", "kind": "text", "name": "", "value": "2.4.0 dev build"},
                      {"id": "uplink", "kind": "text", "name": "", "value": "NO LINK"},
                      {"id": "uplink", "kind": "attr", "name": "href", "value": "/static/themes/gates.css"},
                      {"id": "uplink", "kind": "attr", "name": "aria-label", "value": "Link status"},
                      {"id": "age", "kind": "html", "name": "", "value": 'DATA AGE <b translate="no">12 s</b>'}]}
    assert english(page_texts(run)) == {"NO LINK", "Link status", "DATA AGE"}
