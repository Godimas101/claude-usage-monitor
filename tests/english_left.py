"""The "no English left" scan (#89): the English the widgets draw and the dashboard shows under the pseudo-locale.
tests/test_english_left.py compares it with tests/fixtures/english_left.json, the English that the extraction
tickets (L8 #93 to L16 #101) have yet to move into the catalog. The record only shrinks: ``--record`` strikes what is
no longer English and refuses to add anything unless told to (``--allow-new``).

How English is told from the pseudo-locale. ``i18n.pseudo_text`` changes every a, c, e, g, i, n, o, s, u and y (and
their capitals) into a look-alike, pads the string and brackets it. So a word that still holds one of those letters as
plain ASCII was never made pseudo: it is English ("usage", "Café", "OFF"). A word with a look-alike letter and none of
those plain ones is pseudo; so is one whose only plain one is a capital I, the dotless ı in capitals (#98). A word
with neither ("PM", "MB") could be either: it counts as English only outside a bracketed pseudo string, which the scan
follows across the runs of text one block of the page holds (markup can split a string). Numbers are no words (a word has two ASCII letters in a row). Names are taken out of a run before it is judged:
the product name and #86's brand marks on both halves, and on the widgets the names the skins draw (SKIN_NAMES), since
a frame has no markup to mark them. On the dashboard, an element marked ``translate="no"``, or with a ``lang`` other
than the page's, is skipped with all inside it (``tName``, ``tPath`` and ``tCode`` in app.js mark names, paths and
commands so); an unmarked name, id or path is reported, for the ticket that words its card to mark. An entry is a run
of text as the page or the frame holds it, digits as "#" and spaces collapsed, so the same words with other figures are
one entry; the pseudo-locale's number shapes (#91: groups set off by U+202F, a U+00A0 before "%") are English's there,
and a digit, a plain space and "%" is a digit and "%" (a widget's wrap gives a glued U+00A0 back as a plain space).

The widgets: the frame dump (``render/dump.py``) at scale 1.0 in the pseudo-locale (a dev instance's, i18n.dev_locale),
every theme, variant, state, strip height and tray size, with nothing saved; the tray tooltip of each sample state, as
an installed monitor and as a dev instance show it; and the tray menu with every row shown (#93). Other scales draw the
same words. Text drawn on the dump's contact sheet (a developer's overview, not the widgets) is left out. Besides the
names, the widget half takes out the dev instance's "(dev)" mark and, as whole words, the pseudo-locale's format words
(its weekday names and AM/PM, English's since #91): they are formats, as numbers are. The widget half also reads every
toast and usage alert the tray can show in the pseudo-locale (#97: tests/toast_words.toast_sweep), each judged with the
data a toast holds taken out too (toast_names, toast_format_words): the names of the limits it words, the commands, the
currency codes and the release versions.

The dashboard: tests/english_scan.js runs the page (index.html, formats.js, the catalog, charts.js and app.js, from the
dev server) on a small DOM, for every scenario of the dev server under the pseudo-locale, in the passes PASSES lists:
every page and segment as an installed monitor and as a dev instance shows them, and in some scenarios every action
and control, the update banner, Settings during an update in each state of its run (#58), what each update button says
for every answer the monitor gives (#97), the reports' loading and failed states, and every change failing; and since
#98 the statuses no scenario shows (with the data layer's detail, STATUS_DETAILS), the link lost, the figures old after a
failed refresh and the Gates theme, on Overview and Limits. The theme scripts'
boot texts and the words in the stylesheets are not in it (#101 moves them). Besides the names, the dashboard half takes out
the page's format words, as whole words (its weekday and month names and AM/PM, English's in the pseudo-locale since #91),
and the dev server's data names that the catalog's sentences hold (#98: a plan's name, a limit's scope), which a sentence
holds as plain text (a theme's entrance moves an element's own text).

Both halves run in a fresh Python with the time zone pinned (the dev server's clock and the page's are pinned too), so
a record made here matches one made on CI.

    python tests/english_left.py widgets       the widgets' English, as JSON: each entry and where it was first seen
    python tests/english_left.py dashboard     the dashboard's English, the same way (needs node)
    python tests/english_left.py --record [widgets|dashboard] [--allow-new]
                                               strike what is no longer English from tests/fixtures/english_left.json;
                                               --allow-new also adds what is (English reworded, or the dev server's data
                                               changed: new English text goes through the catalog instead)
"""
from __future__ import annotations

import html.parser
import json
import os
import re
import shutil
import string
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import update_check  # noqa: E402
from usage_monitor import i18n  # noqa: E402
from usage_monitor.data import details  # noqa: E402

RECORD = Path(__file__).resolve().parent / "fixtures" / "english_left.json"
SCAN_JS = Path(__file__).resolve().parent / "english_scan.js"
WEBUI = REPO / "usage_monitor" / "webui"
PINNED_TZ = "UTC0"           # the C runtime's spelling, as tests/frame_digests.py pins it
NODE_TZ = "UTC"              # node's (ICU's) spelling of the same zone
SCAN_NOW = datetime(2026, 11, 18, 20, 0, tzinfo=timezone.utc)     # the dev server's clock and the page's, every run
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
HALVES = ("widgets", "dashboard")

# The look-alikes pseudo_text writes, and the plain letters it never leaves plain.
PSEUDO_LETTERS = frozenset(i18n.pseudo_text(string.ascii_letters)) - frozenset(string.ascii_letters + "[] ")
MAPPED_LETTERS = frozenset(ch for ch in string.ascii_letters if i18n.pseudo_text(ch)[1] != ch)
# The marks a cut ends with: the ellipsis, and Night City's three dots (its own mark, kept by #95).
CUT_MARKS: tuple[str, ...] = ("…", "...")
PRODUCT = "Claude Usage Monitor"
# #86's brand marks, kept as written. The dashboard header draws its mark, "CLAUDE USAGE MONITOR // ONBOARD COMPUTER",
# in two pieces. Night City's NC//USAGE and Usage // Monitor sit in its stylesheet and boot script, outside this scan.
DASHBOARD_MARKS: tuple[str, ...] = ("CLAUDE USAGE MONITOR", "// ONBOARD COMPUTER")
WIDGET_MARKS: tuple[str, ...] = ("CLAUDE//USAGE", "Claude usage")          # Night City's widget, Gates' widget
DEV_MARK = "(dev)"          # a dev instance's tray tooltip ends with it (app.DEV_MARK): a developer's mark, not a word
ATTRIBUTES = ("aria-label", "title", "data-tip", "placeholder", "alt")
# The dev server's scenarios each pass runs in (english_scan.js says what a pass does). Every scenario is seen as an
# installed monitor and as a dev instance; the actions in the scenarios that have their own (the sign-in help, the
# guard); the rest in "normal".
EVERY_SCENARIO = ("installed", "dev")
# The states of a running update that Settings shows (#58): one update-run pass each, in "normal".
UPDATE_RUNS: tuple[str, ...] = ("downloading", "unknown", "connecting", "checking", "starting", "restarting", "stalled",
                                *(f"failed-{code}" for code in update_check.REASONS))
# The monitor's answers to the update buttons (#97): one update-said pass each, in "normal".
UPDATE_SAID: tuple[str, ...] = ("found", "none", "unchecked", "error", "internal", "unavailable", "token", "http", "lost", "hang")
# The statuses the dev server's scenarios never show (#98): one status pass each, in "normal", with the detail the data
# layer words for it (details.english; the next try five minutes after the scan's moment, as the usage API's clock writes
# it). The detail is the data layer's English, which #102 words: these four are the only English the passes add.
_NEXT_TRY = (SCAN_NOW + timedelta(minutes=5)).strftime("%H:%M:%S")
STATUS_DETAILS: dict[str, str] = {
    "offline": details.english(details.offline("timed_out").with_next_try(_NEXT_TRY)),
    "rate_limited": details.english(details.Detail(details.RATE_LIMITED).with_next_try(_NEXT_TRY)),
    "paused": details.english(details.Detail(details.PAUSED)),
    "loading": details.english(details.Detail(details.LOADING)),
}
# #98's passes besides the statuses: the link lost, the figures old, and the Gates theme (english_scan.js says what each does).
LOOK_PASSES: tuple[str, ...] = ("link-lost", "reports-stale", "gates")
PASSES: dict[str, tuple[str, ...]] = {
    "normal": (*EVERY_SCENARIO, "actions", "update", "update-self", "pending", "reports-503", "reports-501",
               "reports-failed", "lookups-failed", "posts-failed", *(f"update-run:{state}" for state in UPDATE_RUNS),
               *(f"update-said:{answer}" for answer in UPDATE_SAID), *(f"status:{code}" for code in STATUS_DETAILS),
               *LOOK_PASSES),
    "no_login": (*EVERY_SCENARIO, "actions", "posts-failed"),
    "refused": (*EVERY_SCENARIO, "actions"),
    "stale": (*EVERY_SCENARIO, "actions"),
    "guard": (*EVERY_SCENARIO, "actions", "posts-failed"),
}


# ── Telling English from the pseudo-locale ───────────────────────────────────────────────────────────────────────

def _kind(word: str) -> str | None:
    """"pseudo", "english", "either" (no letter pseudo_text changes, so it reads the same both ways) or None (no word:
    fewer than two ASCII letters in a row). A plain capital I in a word with a look-alike is pseudo_text's dotless ı set
    in capitals (the page's FMT.upper and tUpper, #98: "[ÜPLIÑK LIVÉ …]"), which has no capital of its own outside
    Turkish: such a word is pseudo when I is its only plain letter of those pseudo_text changes."""
    plain = {ch for ch in word if ch in MAPPED_LETTERS}
    looks_alike = any(ch in PSEUDO_LETTERS for ch in word)
    if plain and not (looks_alike and plain == {"I"}):
        return "english" if re.search(r"[A-Za-z]{2}", word) else None
    if looks_alike:
        return "pseudo"
    return "either" if re.search(r"[A-Za-z]{2}", word) else None


def _english_runs(runs: Sequence[str], names: Iterable[str], format_words: Iterable[str] = ()) -> list[str]:
    """The runs of one block that hold English, as given. A word that reads the same both ways is English only outside
    a bracketed string with a pseudo word in it; the brackets are followed from run to run. A bracketed string that a
    run ends inside with an ellipsis, or Night City's three dots, was cut (#94's and #95's fits, CUT_MARKS): it is
    pseudo_text's, whose bracket opened it, even when the letters left ("[THR…", "[THR...") read the same both ways;
    and a name the cut ends inside is a name ("CLAUDE CO..." of "CLAUDE CODE", _cut_name). ``names`` are taken out wherever they stand, ``format_words`` only
    as whole words ("Mon" is no part of "Monthly")."""
    names = sorted(set(names), key=len, reverse=True)          # a longer name first: "Claude Code" before "Claude"
    words_out = sorted(set(format_words), key=len, reverse=True)
    as_words = re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, words_out)) + r")(?!\w)") if words_out else None
    spans: list[bool] = []                                     # for each bracketed string: has it a pseudo word?
    open_: list[int] = []
    words: list[list[tuple[str, tuple[int, ...]]]] = []
    for run in runs:
        rest = run
        for name in names:
            rest = rest.replace(name, " ")
        rest = _cut_name(rest, names)
        if as_words is not None:
            rest = as_words.sub(" ", rest)
        found = []
        for token in re.finditer(r"\[|\]|[^\s\[\]]+", rest):
            text = token.group(0)
            if text == "[":
                spans.append(False)
                open_.append(len(spans) - 1)
            elif text == "]":
                if open_:
                    open_.pop()
            elif re.fullmatch(r"\{[A-Za-z_][A-Za-z0-9_]*\}", text):
                continue                                       # a placeholder's name, not a word of the page
            else:
                kind = _kind(text)
                if kind == "pseudo":
                    for span in open_:
                        spans[span] = True
                if kind in ("english", "either"):
                    found.append((kind, tuple(open_)))
        if rest.rstrip().endswith(CUT_MARKS):
            for span in open_:                                 # cut inside its brackets
                spans[span] = True
        words.append(found)
    return [run for run, found in zip(runs, words)
            if any(kind == "english" or not any(spans[s] for s in inside) for kind, inside in found)]


def _cut_name(rest: str, names: Sequence[str]) -> str:
    """``rest`` with the start of a name taken out where a cut ends inside it: a run cut with a CUT_MARK whose last
    letters before the mark begin one of ``names`` at a word's start ("USE CLAUDE CO..." of CLAUDE CODE, which a
    Night City band cuts, #95). The longest such start goes; a run with no cut mark comes back as it was."""
    stripped = rest.rstrip()
    mark = next((mark for mark in CUT_MARKS if stripped.endswith(mark)), None)
    if mark is None:
        return rest
    body = stripped[:-len(mark)]
    starts = [name[:size] for name in names for size in range(1, len(name))
              if body.endswith(name[:size]) and (size == len(body) or not body[-size - 1].isalnum())]
    return body[:-len(max(starts, key=len))] + " " + mark if starts else rest


def entry(text: str) -> str:
    """A run of text as the record holds it: digits as "#", spaces collapsed. The pseudo-locale's number shapes fold to
    English's first (#91, D15 of its plan): a U+202F between digits is a ",", a U+00A0 before "%" is nothing, so a figure
    the page's formats write the pseudo way is the entry it always was. A plain space between a digit and "%" is
    nothing too: it is a U+00A0 that a widget's wrap gave back as a space (engine.wrap_words); English writes no space
    there (#93)."""
    text = re.sub(r"(?<=\d)\u202f(?=\d)", ",", text).replace("\u00a0%", "%")
    text = re.sub(r"(?<=\d) %", "%", text)
    return re.sub(r"\s+", " ", re.sub(r"\d", "#", text)).strip()


def english(texts: Iterable[str], names: Iterable[str] = (PRODUCT,), format_words: Iterable[str] = ()) -> set[str]:
    """The entries of the texts that hold English, each text judged on its own."""
    names, format_words = tuple(names), tuple(format_words)
    return {entry(text) for text in texts if _english_runs([text], names, format_words)}


# ── Reading the dashboard's markup ───────────────────────────────────────────────────────────────────────────────

_VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"})
# Elements that sit inside a line of text, so a string the page split with them is still one block.
_INLINE = frozenset({"a", "abbr", "b", "bdi", "bdo", "br", "cite", "code", "data", "del", "dfn", "em", "font", "i", "img",
                     "input", "ins", "kbd", "label", "mark", "output", "q", "s", "samp", "small", "span", "strong", "sub",
                     "sup", "time", "tspan", "u", "var", "wbr"})
# The end tags HTML leaves out (the same rules as tests/mini_dom.js): a start tag closes an open element in its first
# set, unless an element in the second lies between.
_IMPLIED: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "li": (frozenset({"li"}), frozenset({"ul", "ol", "menu"})),
    "option": (frozenset({"option"}), frozenset({"select", "datalist", "optgroup"})),
    "optgroup": (frozenset({"optgroup", "option"}), frozenset({"select"})),
    "td": (frozenset({"td", "th"}), frozenset({"tr", "table"})),
    "th": (frozenset({"td", "th"}), frozenset({"tr", "table"})),
    "tr": (frozenset({"tr", "td", "th"}), frozenset({"table", "thead", "tbody", "tfoot"})),
    "thead": (frozenset({"thead", "tbody", "tfoot", "tr", "td", "th"}), frozenset({"table"})),
    "tbody": (frozenset({"thead", "tbody", "tfoot", "tr", "td", "th"}), frozenset({"table"})),
    "tfoot": (frozenset({"thead", "tbody", "tfoot", "tr", "td", "th"}), frozenset({"table"})),
    "dt": (frozenset({"dt", "dd"}), frozenset({"dl"})),
    "dd": (frozenset({"dt", "dd"}), frozenset({"dl"})),
}
_CLOSES_P = frozenset({"address", "article", "aside", "blockquote", "details", "div", "dl", "fieldset", "figcaption",
                       "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "main", "menu",
                       "nav", "ol", "p", "pre", "section", "table", "ul"})


def _primary(tag: str | None) -> str:
    return (tag or "").strip().split("-")[0].lower()


class _Blocks(html.parser.HTMLParser):
    """The runs of text and the ATTRIBUTES of some HTML, grouped in blocks (a run of inline content; each attribute is a
    block of its own), leaving out every element marked translate="no" or with a lang other than the page's, all inside
    it, scripts and styles. An empty lang is no language."""

    def __init__(self, page_lang: str | None) -> None:
        super().__init__(convert_charrefs=True)
        self.page_lang = _primary(page_lang) or None
        self.blocks: list[list[str]] = []
        self._runs: list[str] = []
        self._open: list[tuple[str, bool]] = []

    def _skipping(self) -> bool:
        return any(skip for _, skip in self._open)

    def _flush(self) -> None:
        if self._runs:
            self.blocks.append(self._runs)
            self._runs = []

    def _close_to(self, index: int) -> None:
        del self._open[index:]

    def _find(self, names: frozenset[str], stops: frozenset[str]) -> int:
        for index in range(len(self._open) - 1, -1, -1):
            tag = self._open[index][0]
            if tag in names:
                return index
            if tag in stops:
                return -1
        return -1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        rule = _IMPLIED.get(tag)
        if rule:
            found = self._find(*rule)
            if found >= 0:
                self._close_to(found)
        if tag in _CLOSES_P:
            found = self._find(frozenset({"p"}), frozenset({"button", "table", "ul", "ol", "li", "td", "th"}))
            if found >= 0:
                self._close_to(found)
        values = dict(attrs)
        lang = values.get("lang")
        own_lang = bool(_primary(lang)) and _primary(lang) != self.page_lang
        skip = values.get("translate") == "no" or own_lang or tag in ("script", "style")
        if tag not in _INLINE:
            self._flush()
        if not (skip or self._skipping()):
            for name in ATTRIBUTES:
                if values.get(name):
                    self.blocks.append([str(values[name])])
        if tag not in _VOID:
            self._open.append((tag, skip))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID and self._open and self._open[-1][0] == tag:
            self._open.pop()

    def handle_endtag(self, tag: str) -> None:
        if tag not in _INLINE:
            self._flush()
        for index in range(len(self._open) - 1, -1, -1):
            if self._open[index][0] == tag:
                self._close_to(index)
                return

    def handle_data(self, data: str) -> None:
        if data.strip() and not self._skipping():
            self._runs.append(data.strip())

    def close(self) -> None:
        super().close()
        self._flush()


def html_blocks(markup: str, page_lang: str | None = None) -> list[list[str]]:
    """The blocks of runs of ``markup`` the scan reads, for a page in ``page_lang``."""
    parser = _Blocks(page_lang)
    parser.feed(markup)
    parser.close()
    return parser.blocks


def html_texts(markup: str, page_lang: str | None = None) -> list[str]:
    """Every run of text and attribute of ``markup`` the scan reads, in order."""
    return [run for block in html_blocks(markup, page_lang) for run in block]


def html_english(markup: str, page_lang: str | None, names: Iterable[str], format_words: Iterable[str] = ()) -> set[str]:
    names, format_words = tuple(names), tuple(format_words)
    return {entry(run) for block in html_blocks(markup, page_lang) for run in _english_runs(block, names, format_words)}


def page_language(shell: str) -> str | None:
    found = re.search(r'<html\b[^>]*\slang="([^"]*)"', shell)
    return found.group(1) if found else None


def scan_english(run: dict[str, Any], names: Iterable[str] | None = None,
                 format_words: Iterable[str] | None = None) -> dict[str, str]:
    """The English of one run of tests/english_scan.js (the page as the server sent it, then each snapshot's title and
    body), each entry with the pass and step it was first seen at. By default the product, #86's marks and the dev
    server's data names are taken out (dashboard_names), and the page's format words as whole words
    (dashboard_format_words)."""
    names = tuple((PRODUCT, *DASHBOARD_MARKS, *dashboard_names()) if names is None else names)
    format_words = tuple(dashboard_format_words() if format_words is None else format_words)
    lang = page_language(run["shell"])
    found: dict[str, str] = {}
    for text in html_english(run["shell"], lang, names, format_words):
        found.setdefault(text, "as served")
    for shot in run["snapshots"]:
        where = f"{shot['pass']}/{shot['step']}"
        for text in english([shot["title"]], names, format_words) | html_english(shot["body"], lang, names, format_words):
            found.setdefault(text, where)
    return found


def dashboard_format_words() -> tuple[str, ...]:
    """The page's format words in the pseudo-locale: the weekday and month names webui/formats.js writes for English (#91
    gives the pseudo-locale English's formats), in capitals as the page shows them and as written, and AM and PM."""
    source = (WEBUI / "formats.js").read_text(encoding="utf-8")
    names: list[str] = []
    for table, count in (("WEEKDAYS", 7), ("MONTHS", 12)):
        found = re.search(rf"var {table} = \[([^\]]*)\];", source)
        words = re.findall(r'"([A-Z]{3})"', found.group(1)) if found else []
        if len(words) != count:
            raise RuntimeError(f"webui/formats.js's {table} is not the {count} names the scan expects: {words}")
        names += words
    return tuple(dict.fromkeys([*names, *(name.title() for name in names), "AM", "PM"]))


def dashboard_names() -> tuple[str, ...]:
    """The dev server's data names that the page puts beside the catalog's words (#98), as given and in capitals: each
    scenario's plan name (ratio.note), where the sign-in names a subscription ("Unknown plan" is English the page words
    itself), each scoped limit's model or surface (limit.name.scoped, limits.meta.scoped), and the surfaces of the week's
    split (the Limits page's bar names them in its tips, "Chats: 12%")."""
    from usage_monitor.data.identity import scope_name
    from usage_monitor.ui import devserver

    found: list[str] = []
    for scenario in devserver.SCENARIOS:
        state = devserver.fake_state(scenario, SCAN_NOW)
        if state.plan is not None and state.plan.subscription_type is not None and state.plan.name:
            found.append(state.plan.name)
        for limit in state.usage.limits if state.usage is not None else []:
            name = scope_name(limit.scope_model, limit.scope_surface)
            if limit.kind == "weekly_scoped" and name:
                found.append(name)
        found += [row.name for row in state.usage.breakdown if row.name] if state.usage is not None else []
    return tuple(dict.fromkeys([*found, *(name.upper() for name in found)]))


# ── The widgets ──────────────────────────────────────────────────────────────────────────────────────────────────

def skin_names() -> tuple[str, ...]:
    """The names the skins draw: Claude Code (the sign-in hints name it) and each scoped model of the sample states,
    as given and in capitals."""
    from usage_monitor.render import dump

    models = {limit.scope_model for name in dump.DUMP_STATE_NAMES
              for state in [dump.sample_state(name)] if state is not None and state.usage is not None
              for limit in state.usage.limits if limit.scope_model}
    return ("Claude Code", "CLAUDE CODE", *sorted(models), *sorted(model.upper() for model in models))


def widget_names() -> tuple[str, ...]:
    """What the widget half takes out of a run before judging it: the product, #86's widget marks, the dev mark and the
    names the skins draw."""
    return (PRODUCT, *WIDGET_MARKS, DEV_MARK, *skin_names())


def pseudo_format_words() -> tuple[str, ...]:
    """The pseudo-locale's format words: its weekday names and its AM and PM, as written and in capitals (the skins draw
    both), and Gates' unit words of two letters or more as Gates writes them (its age's "min", #96). They are
    English's, since #91 gives the pseudo-locale English's formats. The widget half treats them as formats, as it treats
    numbers (#93); the dashboard half's record still lists the date names the page shows."""
    from usage_monitor import casing, formats
    from usage_monitor.render.skins import gates

    locale = i18n.dev_locale(i18n.PSEUDO)
    words = [formats.weekday(day, locale) for day in range(7)]
    words += [formats.clock(datetime(2026, 1, 1, hour), locale).split()[-1] for hour in (0, 12)]
    units = [unit for unit in (*gates.AGE_UNITS.values(), *gates.COUNTDOWN_UNITS.values()) if len(unit) >= 2]
    return tuple(dict.fromkeys([*words, *(casing.upper(word, locale.language) for word in words), *units]))


def toast_names() -> tuple[str, ...]:
    """What the widget half takes out of a toast before judging it, wherever it stands: the widget names, and the names
    the sweep puts in an alert's title, each as written and with its first letter in capitals (as the pseudo-locale
    writes one that starts a title): every model's and surface's name of tests/toast_words.sweep_shapes(), and the name
    of a kind no catalog has words for, from its code (identity text, #93 D4)."""
    from usage_monitor import casing
    from usage_monitor.data.identity import scope_name

    import toast_words

    found: list[str] = []
    for kind, label, model, surface in toast_words.sweep_shapes().values():
        for name in (model, scope_name(model, surface), kind.replace("_", " ") if label.strip() and kind not in (
                "session", "weekly_all", "weekly_scoped") else None):
            name = " ".join(str(name or "").split())
            if name:
                found += [name, casing.cased("{name}", "sentence", i18n.PSEUDO, name=name)]
    return tuple(dict.fromkeys([*widget_names(), *found]))


def toast_format_words() -> tuple[str, ...]:
    """What the widget half takes out of a toast as whole words: the pseudo-locale's format words, every command the
    app shows (i18n.COMMANDS: the note's /mcp), the currency codes the sweep's credits carry (written as the code is
    given, English's shapes put it after the amount), and the sweep's release versions, bare and after a "v"."""
    import toast_words

    codes = {currency for _used, _limit, currency in toast_words.SPENDS.values() if currency}
    versions = [text for version in toast_words.SWEEP_VERSIONS for text in (version, "v" + version)]
    return tuple(dict.fromkeys([*pseudo_format_words(), *i18n.COMMANDS, *sorted(codes), *versions]))


def _toasts() -> dict[str, str]:
    """Every toast the tray can show in the pseudo-locale (tests/toast_words.toast_sweep), each with its box."""
    import toast_words

    seen: dict[str, str] = {}
    for box, texts in toast_words.toast_sweep(i18n.PSEUDO).items():
        for text in texts:
            seen.setdefault(text, f"toast {box}")
    return seen


def _menu_labels(locale: i18n.Locale) -> list[str]:
    """The tray menu's labels in ``locale`` with every row shown: sessions waiting (one, then two) and an update on
    offer."""
    from usage_monitor import app

    def nothing() -> None:
        return None

    labels: list[str] = []
    for waiting in (1, 2):
        items = app.menu_items(floating_on=True, taskbar_on=True, guard_on=True, waiting=waiting,
                               update_version="2.1.0", open_dashboard=nothing, toggle_floating=nothing,
                               toggle_taskbar=nothing,
                               toggle_guard=nothing, resume_waiting=nothing, refresh=nothing, report_bug=nothing,
                               update=nothing, quit_app=nothing, locale=locale)
        labels += [item.label for item in items if not item.separator]
    return labels


def _drawn() -> dict[str, str]:
    """Every run of text the frame dump draws at scale 1.0 in the pseudo-locale, each sample state's tray tooltip there
    (plain and with the dev mark) and the tray menu's labels, each with where it was first drawn (in this process).
    Text reaches the frames through engine.Canvas.text_layer, gates' Ink.put, or Pillow's ImageDraw.text (a skin may
    draw with it directly); all three are listened to."""
    from PIL import ImageDraw

    from usage_monitor import app
    from usage_monitor.render import dump, engine
    from usage_monitor.render.skins import gates

    pseudo = i18n.dev_locale(i18n.PSEUDO)
    seen: dict[str, str] = {}
    where = [""]
    state = [""]
    real = (engine.Canvas.text_layer, gates.Ink.put, ImageDraw.ImageDraw.text, dump._save, dump._contact_sheet,
            dump.sample_model, dump.get_skin)

    def layer(self, text, *args, **kwargs):  # type: ignore[no-untyped-def]
        seen.setdefault(str(text), where[0])
        return real[0](self, text, *args, **kwargs)

    def put(self, text, *args, **kwargs):  # type: ignore[no-untyped-def]
        seen.setdefault(str(text), where[0])
        return real[1](self, text, *args, **kwargs)

    def draw(self, xy, text, *args, **kwargs):  # type: ignore[no-untyped-def]
        seen.setdefault(str(text), where[0])
        return real[2](self, xy, text, *args, **kwargs)

    def save(image, path, report):  # type: ignore[no-untyped-def]
        report.files += 1

    def model(name, *args, **kwargs):  # type: ignore[no-untyped-def]
        state[0] = name
        return real[5](name, *args, **kwargs)

    class Told:
        """A skin whose renders say what they draw: theme, variant, state and the frame's kind."""

        def __init__(self, skin: Any) -> None:
            self._skin = skin

        def __getattr__(self, name: str) -> Any:
            attr = getattr(self._skin, name)
            if not name.startswith("render_"):
                return attr

            def render(model, theme, variant, *args, **kwargs):  # type: ignore[no-untyped-def]
                where[0] = f"{theme.id}/{variant.id}/{state[0]} {name[len('render_'):]}"
                return attr(model, theme, variant, *args, **kwargs)
            return render

    engine.Canvas.text_layer, gates.Ink.put, ImageDraw.ImageDraw.text = layer, put, draw
    dump._save, dump._contact_sheet = save, (lambda theme, skin, path, variant_filter=None, **_kwargs: path)
    dump.sample_model, dump.get_skin = model, (lambda theme_id: Told(real[6](theme_id)))
    try:
        with tempfile.TemporaryDirectory(prefix="english-left-") as folder:
            dump.dump_frames(folder, scales=(1.0,), locale=pseudo)
    finally:
        (engine.Canvas.text_layer, gates.Ink.put, ImageDraw.ImageDraw.text, dump._save, dump._contact_sheet,
         dump.sample_model, dump.get_skin) = real
    for name in dump.DUMP_STATE_NAMES:
        model = dump.sample_model(name, locale=pseudo)
        for dev in (False, True):
            seen.setdefault(app.tray_tooltip(model, dev=dev), f"tray tooltip, state {name}" + (", dev" if dev else ""))
    for label in _menu_labels(pseudo):
        seen.setdefault(label, "tray menu")
    return seen


def _widgets_english() -> dict[str, str]:
    names, format_words = widget_names(), pseudo_format_words()
    found: dict[str, str] = {}
    for text, where in _drawn().items():
        for hit in english([text], names, format_words):
            found.setdefault(hit, where)
    names, format_words = toast_names(), toast_format_words()
    for text, where in _toasts().items():
        for hit in english([text], names, format_words):
            found.setdefault(hit, where)
    return dict(sorted(found.items()))


# ── The dashboard ────────────────────────────────────────────────────────────────────────────────────────────────

def scan_dashboard(scenario: str, passes: Sequence[str], folder: Path) -> dict[str, Any]:
    """One run of tests/english_scan.js: the dev server's ``scenario`` under the pseudo-locale (its settings under
    ``folder``), in ``passes``. Raises when the scan failed, or the page met an error or wrote anything to the console."""
    from usage_monitor.ui import devserver

    node = shutil.which("node")
    if node is None:
        raise RuntimeError("node is not installed, so the dashboard cannot be scanned")
    server, settings = devserver.make_server(scenario, 0, settings_dir=folder / scenario, lang=i18n.PSEUDO,
                                             clock=lambda: SCAN_NOW)
    try:
        server.start()
        run = subprocess.run([node, str(SCAN_JS), str(WEBUI), server.url, str(int(SCAN_NOW.timestamp() * 1000)),
                              json.dumps(list(passes)), json.dumps(STATUS_DETAILS)], env={**os.environ, "TZ": NODE_TZ},
                             capture_output=True,
                             encoding="utf-8", timeout=1200, check=False, creationflags=NO_WINDOW)
    finally:
        server.stop()
        settings.close()
    if run.returncode or run.stderr.strip():
        raise RuntimeError(f"tests/english_scan.js failed on the {scenario} scenario (exit {run.returncode}):\n"
                           f"{run.stderr[-3000:]}")
    result: dict[str, Any] = json.loads(run.stdout)
    if result["problems"]:
        raise RuntimeError(f"the page met errors on the {scenario} scenario:\n" + "\n".join(result["problems"][:40]))
    return result


def _dashboard_english() -> dict[str, str]:
    """Every scenario of the dev server under the pseudo-locale, in its passes, through tests/english_scan.js (in this
    process). Raises when the page met an error or wrote anything to the console."""
    from usage_monitor.ui import devserver

    found: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="english-left-") as folder:
        for scenario in devserver.SCENARIOS:
            result = scan_dashboard(scenario, PASSES.get(scenario, EVERY_SCENARIO), Path(folder))
            for text, where in scan_english(result).items():
                found.setdefault(text, f"{scenario}/{where}")
    return dict(sorted(found.items()))


# ── Running ──────────────────────────────────────────────────────────────────────────────────────────────────────

def _pinned(half: str) -> dict[str, str]:
    """``python tests/english_left.py --here HALF`` in a fresh Python with the time zone pinned, importing this repo."""
    done = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--here", half], cwd=REPO,
                          env={**os.environ, "TZ": PINNED_TZ, "PYTHONPATH": str(REPO)}, capture_output=True,
                          encoding="utf-8", timeout=2400, check=False, creationflags=NO_WINDOW)
    if done.returncode:
        raise RuntimeError(f"the {half} scan failed:\n{done.stderr[-3000:]}")
    return json.loads(done.stdout)


def widgets() -> dict[str, str]:
    """The widgets' English, each entry with where it was first drawn."""
    return _pinned("widgets")


def dashboard() -> dict[str, str]:
    """The dashboard's English, each entry with the scenario, pass and step it was first seen at."""
    return _pinned("dashboard")


def recorded() -> dict[str, list[str]]:
    return json.loads(RECORD.read_text(encoding="utf-8"))


def record(halves: Sequence[str] = HALVES, allow_new: bool = False) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Rewrite the record for ``halves`` from a fresh scan: what is no longer English is struck. English the record
    lacks is added only with ``allow_new``; otherwise nothing is written. Returns (added, struck) for each half."""
    was = recorded() if RECORD.exists() else {half: [] for half in HALVES}
    now = {half: (widgets() if half == "widgets" else dashboard()) for half in halves}
    added = {half: sorted(set(now[half]) - set(was.get(half, []))) for half in halves}
    struck = {half: sorted(set(was.get(half, [])) - set(now[half])) for half in halves}
    if any(added.values()) and not allow_new:
        return added, struck
    fresh = {half: sorted(now[half]) if half in now else was[half] for half in HALVES}
    RECORD.write_text(json.dumps(fresh, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return added, struck


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "--here" and argv[1] in HALVES:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        print(json.dumps(_widgets_english() if argv[1] == "widgets" else _dashboard_english(), ensure_ascii=False))
        return 0
    if len(argv) == 1 and argv[0] in HALVES:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        print(json.dumps(widgets() if argv[0] == "widgets" else dashboard(), ensure_ascii=False, indent=1))
        return 0
    if argv[:1] == ["--record"]:
        rest = argv[1:]
        allow_new = "--allow-new" in rest
        halves = [h for h in rest if h in HALVES] or list(HALVES)
        if set(rest) - set(HALVES) - {"--allow-new"}:
            print(__doc__)
            return 2
        added, struck = record(halves, allow_new)
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        for half in halves:
            print(f"{half}: {len(struck[half])} struck, {len(added[half])} new")
            for text in added[half]:
                print(f"  new: {text}")
        if any(added.values()) and not allow_new:
            print("Nothing written: the scan found English the record lacks. Put new text through the catalog; if the "
                  "English was only reworded, or the dev server's data changed, record with --allow-new.")
            return 1
        print(f"wrote {RECORD}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
