"""The "no English left" scan (#89): the English the widgets draw and the dashboard shows under the pseudo-locale.
tests/test_english_left.py compares it with tests/fixtures/english_left.json, the English that the extraction
tickets (L8 #93 to L16 #101) have yet to move into the catalog. Each of them strikes its own lines from that file.

Under the pseudo-locale every catalog string carries letters English never has (``i18n.pseudo_text``), so a run of
text with two plain letters in a row and none of those letters is English. Numbers are not counted (no two letters
in a row), nor is anything marked as data: on the dashboard an element marked ``translate="no"`` or with a ``lang``
other than the page's is skipped with all inside it (``tName``, ``tPath`` and ``tCode`` in app.js mark names, paths
and commands so); a frame has no marks, so the widgets' scan takes the names the skins draw out of each run first
(SKIN_NAMES). The product's name and the brand marks #86 keeps (BRAND) are taken out on both. Digits are written as
"#" and spaces collapsed, so the same words with other figures are one entry.

The widgets: the frame dump (``render/dump.py``) at scale 1.0, every theme, variant, state, strip height and tray
size, with nothing saved, and the tray tooltip of each sample state. Other scales draw the same words. The widgets
take no language until #93, so for now this lists all their words.

The dashboard: tests/english_scan.js draws every page of app.js against the dev server under the pseudo-locale, for
each of its scenarios: the page as the server sends it (document.title too), each page's parts and charts, and what
app.js writes into the page's fixed elements. The theme scripts' boot texts and the words in the stylesheets are not
in it (#101 moves them).

Both run in a fresh Python with the time zone pinned (the dev server's clock and the page's are pinned too), so a
record made here matches one made on CI.

    python tests/english_left.py widgets      the widgets' English, as JSON (widgets() runs it with the zone pinned)
    python tests/english_left.py dashboard    the dashboard's English, as JSON (needs node; dashboard() pins the zone)
    python tests/english_left.py --record     rewrite tests/fixtures/english_left.json from both
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
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from usage_monitor import i18n  # noqa: E402

RECORD = Path(__file__).resolve().parent / "fixtures" / "english_left.json"
SCAN_JS = Path(__file__).resolve().parent / "english_scan.js"
WEBUI = REPO / "usage_monitor" / "webui"
PINNED_TZ = "UTC0"           # the C runtime's spelling, as tests/frame_digests.py pins it
NODE_TZ = "UTC"              # node's (ICU's) spelling of the same zone
SCAN_NOW = datetime(2026, 11, 18, 20, 0, tzinfo=timezone.utc)     # the dev server's clock and the page's, every run
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

PSEUDO_LETTERS = frozenset(i18n.pseudo_text(string.ascii_letters)) - frozenset(string.ascii_letters + "[] ")
# The product's name (the page title) and the brand marks #86 keeps as written. The dashboard header draws its mark,
# "CLAUDE USAGE MONITOR // ONBOARD COMPUTER", in two pieces. Night City's NC//USAGE and Usage // Monitor sit in its
# stylesheet and boot script, outside this scan.
BRAND: tuple[str, ...] = ("Claude Usage Monitor", "CLAUDE USAGE MONITOR", "// ONBOARD COMPUTER", "CLAUDE//USAGE",
                          "Claude usage")
ATTRIBUTES = ("aria-label", "title", "data-tip", "placeholder", "alt")


def english(texts: Iterable[str], names: Iterable[str] = BRAND) -> set[str]:
    """The texts that are English once ``names`` are taken out, as written (digits as "#", spaces collapsed)."""
    names = sorted(set(names), key=len, reverse=True)          # a longer name first: "Claude Code" before "Claude"
    found = set()
    for text in texts:
        if any(ch in PSEUDO_LETTERS for ch in text):
            continue
        rest = text
        for name in names:
            rest = rest.replace(name, " ")
        if re.search(r"[A-Za-z]{2}", rest):
            found.add(re.sub(r"\s+", " ", re.sub(r"\d", "#", text)).strip())
    return found


class _Texts(html.parser.HTMLParser):
    """The text nodes and the ATTRIBUTES of some HTML, leaving out every element marked translate="no" or with a lang
    other than the page's, and all inside it (and scripts and styles). ``data_ids``: the ids of the elements left out."""

    VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track",
                      "wbr"})

    def __init__(self, page_lang: str | None) -> None:
        super().__init__(convert_charrefs=True)
        self.page_lang = page_lang
        self.found: list[str] = []
        self.data_ids: set[str] = set()
        self._open: list[tuple[str, bool]] = []

    def _skipping(self) -> bool:
        return any(skip for _, skip in self._open)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        skip = (values.get("translate") == "no" or ("lang" in values and values["lang"] != self.page_lang)
                or tag in ("script", "style"))
        if skip or self._skipping():
            if values.get("id"):
                self.data_ids.add(str(values["id"]))
        else:
            self.found += [str(values[name]) for name in ATTRIBUTES if values.get(name)]
        if tag not in self.VOID:
            self._open.append((tag, skip))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self._open.pop()

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._open) - 1, -1, -1):
            if self._open[index][0] == tag:
                del self._open[index:]
                return

    def handle_data(self, data: str) -> None:
        if data.strip() and not self._skipping():
            self.found.append(data.strip())


def _parse(markup: str, page_lang: str | None) -> _Texts:
    parser = _Texts(page_lang)
    parser.feed(markup)
    parser.close()
    return parser


def html_texts(markup: str, page_lang: str | None = None) -> list[str]:
    """The texts of ``markup`` the scan reads, for a page in ``page_lang``."""
    return _parse(markup, page_lang).found


def page_texts(run: dict[str, Any]) -> list[str]:
    """The texts of one run of tests/english_scan.js: the page as the server sent it, each page's parts and charts,
    document.title, and what app.js wrote into the page's fixed elements, except into an element marked as data."""
    found = re.search(r'<html\b[^>]*\slang="([^"]*)"', run["shell"])
    page_lang = found.group(1) if found else None
    texts: list[str] = [run["title"]]
    data_ids: set[str] = set()
    for markup in [run["shell"], *(part for parts in run["pages"].values() for part in parts)]:
        parsed = _parse(markup, page_lang)
        texts += parsed.found
        data_ids |= parsed.data_ids
    for write in run["writes"]:
        if write["id"] in data_ids:
            continue
        if write["kind"] == "html":
            texts += html_texts(write["value"], page_lang)
        elif write["kind"] in ("text", "title") or write["name"] in ATTRIBUTES:
            texts.append(write["value"])
    return texts


# ── The widgets ──────────────────────────────────────────────────────────────────────────────────────────────────

def skin_names() -> tuple[str, ...]:
    """The names the skins draw: Claude Code (the sign-in hints name it) and each scoped model of the sample states,
    as given and in capitals."""
    from usage_monitor.render import dump

    models = {limit.scope_model for name in dump.DUMP_STATE_NAMES
              for state in [dump.sample_state(name)] if state is not None and state.usage is not None
              for limit in state.usage.limits if limit.scope_model}
    return ("Claude Code", "CLAUDE CODE", *sorted(models), *sorted(model.upper() for model in models))


def _drawn() -> set[str]:
    """Every run of text the frame dump draws at scale 1.0, and each sample state's tray tooltip (in this process)."""
    from usage_monitor import app
    from usage_monitor.render import dump, engine
    from usage_monitor.render.skins import gates

    seen: set[str] = set()
    real_layer, real_put, real_save, real_sheet = engine.Canvas.text_layer, gates.Ink.put, dump._save, dump._contact_sheet

    def layer(self, text, *args, **kwargs):  # type: ignore[no-untyped-def]
        seen.add(text)
        return real_layer(self, text, *args, **kwargs)

    def put(self, text, *args, **kwargs):  # type: ignore[no-untyped-def]
        seen.add(text)
        return real_put(self, text, *args, **kwargs)

    def save(image, path, report):  # type: ignore[no-untyped-def]
        report.files += 1

    engine.Canvas.text_layer, gates.Ink.put = layer, put
    dump._save, dump._contact_sheet = save, (lambda theme, skin, path, variant_filter=None: path)
    try:
        with tempfile.TemporaryDirectory(prefix="english-left-") as folder:
            dump.dump_frames(folder, scales=(1.0,))
    finally:
        engine.Canvas.text_layer, gates.Ink.put, dump._save, dump._contact_sheet = real_layer, real_put, real_save, real_sheet
    seen |= {app.tray_tooltip(dump.sample_model(name)) for name in dump.DUMP_STATE_NAMES}
    return seen


def _widgets_english() -> list[str]:
    return sorted(english(_drawn(), BRAND + skin_names()))


# ── The dashboard ────────────────────────────────────────────────────────────────────────────────────────────────

def _dashboard_english() -> list[str]:
    """Every scenario of the dev server under the pseudo-locale, drawn by tests/english_scan.js (in this process)."""
    from usage_monitor.ui import devserver

    node = shutil.which("node")
    if node is None:
        raise RuntimeError("node is not installed, so the dashboard cannot be scanned")
    found: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="english-left-") as folder:
        for scenario in devserver.SCENARIOS:
            server, settings = devserver.make_server(scenario, 0, settings_dir=Path(folder) / scenario,
                                                     lang=i18n.PSEUDO, clock=lambda: SCAN_NOW)
            server.start()
            try:
                run = subprocess.run([node, str(SCAN_JS), str(WEBUI), server.url, str(int(SCAN_NOW.timestamp() * 1000))],
                                     env={**os.environ, "TZ": NODE_TZ}, capture_output=True, encoding="utf-8",
                                     timeout=600, check=False, creationflags=NO_WINDOW)
            finally:
                server.stop()
                settings.close()
            if run.returncode:
                raise RuntimeError(f"tests/english_scan.js failed on the {scenario} scenario:\n{run.stderr[-2000:]}")
            result = json.loads(run.stdout)
            if result["errors"]:
                raise RuntimeError(f"pages failed to draw on the {scenario} scenario: {result['errors']}")
            found |= english(page_texts(result), BRAND)
    return sorted(found)


# ── Running ──────────────────────────────────────────────────────────────────────────────────────────────────────

def _pinned(command: str) -> list[str]:
    """``python tests/english_left.py COMMAND`` in a fresh Python with the time zone pinned, importing this repo."""
    done = subprocess.run([sys.executable, str(Path(__file__).resolve()), command], cwd=REPO,
                          env={**os.environ, "TZ": PINNED_TZ, "PYTHONPATH": str(REPO)}, capture_output=True,
                          encoding="utf-8", timeout=1800, check=False, creationflags=NO_WINDOW)
    if done.returncode:
        raise RuntimeError(f"the {command} scan failed:\n{done.stderr[-2000:]}")
    return json.loads(done.stdout)


def widgets() -> list[str]:
    return _pinned("widgets")


def dashboard() -> list[str]:
    return _pinned("dashboard")


def recorded() -> dict[str, list[str]]:
    return json.loads(RECORD.read_text(encoding="utf-8"))


def main(argv: list[str]) -> int:
    scans = {"widgets": _widgets_english, "dashboard": _dashboard_english}
    if len(argv) == 1 and argv[0] in scans:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        print(json.dumps(scans[argv[0]](), ensure_ascii=False))
        return 0
    if argv == ["--record"]:
        record = {"widgets": widgets(), "dashboard": dashboard()}
        RECORD.write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        print(f"wrote {len(record['widgets'])} widget and {len(record['dashboard'])} dashboard entries to {RECORD}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
