"""Each skin's own English words, for tests/test_skin_words.py (#94 to #96).

tests/fixtures/skin_words_english.json holds what a skin drew before its own words moved into the catalog, one section
per skin: "nostromo" (#94), and Night City's and Gates' once #95 and #96 record theirs, before they edit. A section has
the output of every helper of the skin that words a text, and every run of text each surface draws (the text, where it
is set, its face, size, tracking and alignment, the pixel it lands on and its advance) at 1.0 and 1.25. The test holds
the skin to it byte for byte. Reset times and resume clocks are local time, so the words are worked out in a fresh
Python with the time zone pinned to UTC (``collect``), as tests/widget_words.py does.

The states: every dump state in every show_scoped mode; tests/widget_words.py's edge states (the refused sign-in, the
scoped limits named every way, a kind with no words, a limit gone from the reading) and guard states (each countdown
shape, on a live and on a rate-limited reading, with no resume time); and the states a skin's own words hang on. For
Nostromo: each sign-in reason on three rows, ages of 59 s, 59 min, 47 h and 100 d on a live and on a rate-limited
reading, a pace that is unknown, on pace, 100 points ahead and 100 points behind, and a guard armed with no caps.

    python tests/skin_words.py --print SKIN      a skin's words, from the code this Python imports, as JSON
    python tests/skin_words.py --record SKIN     write a skin's section, worked out in a fresh Python; refused when the
                                                 record has it already (English must not change)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import widget_words  # noqa: E402

RECORD = HERE / "fixtures" / "skin_words_english.json"
PINNED_TZ = widget_words.PINNED_TZ
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SCALES = (1.0, 1.25)
TRAY_SIZE = 32                  # the one tray icon that draws text (its number)
ABOUT = ("Each skin's own English words, recorded before its words moved into the catalog (#94 to #96), by "
         "tests/skin_words.py in a fresh Python with TZ=UTC0: every helper that words a text, and every run each "
         "surface draws at 1.0 and 1.25. tests/test_skin_words.py holds the code to these bytes. A section is written "
         "once, before its skin is edited, and never again: English must not change.")


# ── States ────────────────────────────────────────────────────────────────────

def _limit(kind: str, percent: float, at: Any, label: str, **extra: Any) -> Any:
    return widget_words._limit(kind, percent, at, label, **extra)


def _nostromo_states() -> dict[str, Any]:
    """The states Nostromo's own words hang on, beyond the dump's and widget_words': each sign-in reason, the age in
    each unit at its widest, each pace, and a guard armed with no caps (its lead alone)."""
    from usage_monitor.data.models import ApiStatus
    from usage_monitor.render import dump

    now = dump.SAMPLE_NOW
    states: dict[str, Any] = {}
    for reason, refused in (("expired", None), ("refused", 403), ("expired_401", 401)):
        states[f"reason/{reason}"] = replace(dump.sample_state("stale3"), signin_refused=refused)
    states["reason/no_login"] = dump.sample_state("no_credentials")
    base = [_limit("session", 40.0, now + timedelta(hours=3, minutes=27), "SESSION"),
            _limit("weekly_all", 20.0, now + timedelta(days=3), "WEEKLY")]
    ages = {"59s": 59, "59m": 59 * 60 + 59, "47h": 47 * 3600 + 59 * 60, "100d": 100 * 86400}
    for status in (ApiStatus.LIVE, ApiStatus.RATE_LIMITED):
        for shape, seconds in ages.items():
            states[f"age/{status.name.lower()}/{shape}"] = dump._state(now, status, base, seconds)
    session = 18000
    paces = {
        "none": _limit("session", 40.0, None, "SESSION"),                                    # no reset time: no pace
        "on": _limit("session", 50.0, now + timedelta(seconds=session // 2), "SESSION"),
        "ahead_100": _limit("session", 100.0, now + timedelta(seconds=session), "SESSION"),  # nothing elapsed
        "behind_100": _limit("session", 0.0, now, "SESSION"),                                # all of it elapsed
    }
    for shape, limit in paces.items():
        states[f"pace/{shape}"] = dump._state(now, ApiStatus.LIVE, [limit, base[1]], 12)
    armed = dump._state(now, ApiStatus.LIVE, base, 12)
    states["guard_armed_no_caps"] = replace(armed, guard={"state": "armed", "policy": {"enabled": True}})
    return states


def models(extra: Callable[[], dict[str, Any]], locale: Any = None) -> dict[str, Any]:
    """Every state the record covers, by name, as its view model in ``locale`` (English when None): the dump's in
    every show_scoped mode that gives it other rows than the modes before it, then widget_words' edge and guard states
    and ``extra``'s, with every scoped row shown."""
    from usage_monitor.i18n import ENGLISH
    from usage_monitor.render import dump
    from usage_monitor.render.model import SHOW_SCOPED_MODES, build_model

    locale = ENGLISH if locale is None else locale
    found: dict[str, Any] = {}
    for name in dump.DUMP_STATE_NAMES:
        shown: list[Any] = []
        for mode in SHOW_SCOPED_MODES:
            model = dump.sample_model(name, show_scoped=mode, locale=locale)
            if model not in shown:              # a mode that shows the same rows draws the same frames
                shown.append(model)
                found[f"{name}/{mode}"] = model
    for group in (widget_words._edge_states(), widget_words._guard_states(), extra()):
        for name, state in group.items():
            found[name] = build_model(state, dump.SAMPLE_NOW, show_scoped="always", locale=locale)
    return found


# ── What a skin draws ─────────────────────────────────────────────────────────

def runs(skin: Any, model: Any, theme: Any, variant: Any, heights: tuple[int, ...],
         scales: tuple[float, ...] = SCALES) -> dict[str, list[list[Any]] | str]:
    """Every run of text each surface draws, in drawing order, at each of ``scales`` (the strip at each taskbar height
    of ``heights``, the tray icon at TRAY_SIZE): (text, x, baseline, face file without its .ttf, size, tracking, align,
    the layer's pixel x and y, advance) for a canvas run, (text, x, y, size, anchor) for a tray number. A surface that
    draws exactly what an earlier one of the state drew names it instead (the strip on a taskbar tall enough for
    it)."""
    from PIL import ImageDraw

    from usage_monitor.render import engine

    found: list[list[Any]] = []
    real_layer, real_draw = engine.Canvas.text_layer, ImageDraw.ImageDraw.text

    def layer(self: Any, text: str, x: float, baseline: float, font: Any, tracking: float = 0.0,
              align: str = "left") -> Any:
        out = real_layer(self, text, x, baseline, font, tracking, align)
        found.append([str(text), round(x, 2), round(baseline, 2), Path(str(getattr(font, "path", ""))).stem,
                      getattr(font, "size", None), tracking, align, out[0].x, out[0].y, round(out[1], 2)])
        return out

    def draw(self: Any, xy: Any, text: Any, *args: Any, **kwargs: Any) -> Any:
        if tray[0]:                 # elsewhere Pillow draws a canvas run's glyphs, once per glyph the cache lacks
            font = kwargs.get("font")
            found.append([str(text), *(round(float(v), 2) for v in xy), getattr(font, "size", None),
                          kwargs.get("anchor")])
        return real_draw(self, xy, text, *args, **kwargs)

    def take(surface: str, draw_it: Callable[[], Any]) -> None:
        found.clear()
        draw_it()
        same = next((name for name, earlier in drawn.items() if earlier == found), None)
        drawn[surface] = same if same is not None else list(found)

    drawn: dict[str, list[list[Any]] | str] = {}
    tray = [False]
    engine.Canvas.text_layer, ImageDraw.ImageDraw.text = layer, draw
    try:
        for scale in scales:
            tag = f"{scale:g}"
            take(f"floating@{tag}", lambda: skin.render_floating(model, theme, variant, scale, None))
            take(f"tooltip@{tag}", lambda: skin.render_tooltip(model, theme, variant, scale))
            for height in heights:
                take(f"strip/h{round(height * scale)}@{tag}",
                     lambda: skin.render_taskbar(model, theme, variant, round(height * scale), scale, None))
        tray[0] = True
        take(f"tray/{TRAY_SIZE}", lambda: skin.render_tray_icon(model, theme, variant, TRAY_SIZE))
    finally:
        engine.Canvas.text_layer, ImageDraw.ImageDraw.text = real_layer, real_draw
    return drawn


def _nostromo_helpers(model: Any, theme: Any, variant: Any) -> dict[str, Any]:
    """What each of Nostromo's wording helpers gives for ``model``. The reset text and the strip's window tags have no
    helper of their own: they are in the runs."""
    from usage_monitor.render.skins import nostromo as nos

    pal = nos._palette(theme, variant, model)
    rows = []
    for row in model.rows:
        text, colour = nos._pace_text(pal, row)
        rows.append({"key": row.key, "names": list(nos._row_names(row)),
                     "qualifier": nos._qualifier(row, model.locale),
                     "short_qualifier": nos._short_qualifier(row, model.locale),
                     "cap": nos._cap_text(model, row), "pace": [text, list(colour)]})
    statement = nos._guard_statement(model)
    lines: dict[str, Any] = {}
    for scale in SCALES:
        lead, wrapped = nos._guard_lines(nos._canvas(nos.FLOAT_W, 1, scale, theme), model)
        lines[f"{scale:g}"] = {"lead": lead, "lines": wrapped,
                               "split": list(nos._split_lead(wrapped[0], lead)) if wrapped else None}
    message = nos._message_lines(model)
    return {"message": list(message) if message is not None else None, "state_word": list(nos._state_word(model)),
            "age": nos._age_text(model), "hold": nos._hold_word(model),
            "statement": list(statement) if statement is not None else None, "guard_lines": lines, "rows": rows}


def _nostromo() -> dict[str, Any]:
    from usage_monitor.render import dump
    from usage_monitor.render.skins import get_skin

    theme = dump.load_theme_objects()["nostromo"]
    skin, variant = get_skin("nostromo"), theme.variants[0]
    found: dict[str, Any] = {}
    for name, model in models(_nostromo_states).items():
        found[name] = {"helpers": _nostromo_helpers(model, theme, variant),
                       "runs": runs(skin, model, theme, variant, dump.TASKBAR_HEIGHTS_AT_1X)}
    return found


# The skins with a section, and what works their words out. #95 and #96 add theirs here, and record before they edit.
SKINS: dict[str, Callable[[], dict[str, Any]]] = {"nostromo": _nostromo}


# ── Running ───────────────────────────────────────────────────────────────────

def collect(skin: str, repo: str | Path = REPO) -> dict[str, Any]:
    """``skin``'s words in the code in ``repo``, worked out in a fresh Python with the time zone pinned. Raises when
    that Python imported usage_monitor from anywhere else."""
    root = Path(repo).resolve()
    done = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--print", skin], cwd=root,
                          env={**os.environ, "TZ": PINNED_TZ, "PYTHONPATH": str(root)}, capture_output=True,
                          encoding="utf-8", timeout=900, check=False, creationflags=NO_WINDOW)
    if done.returncode:
        raise RuntimeError(f"{skin}'s words could not be worked out:\n{done.stderr[-3000:]}")
    result = json.loads(done.stdout)
    source = Path(result["source"]).resolve()
    if root not in source.parents:
        raise RuntimeError(f"usage_monitor came from {source}, not from {root}")
    return result["words"]


def recorded() -> dict[str, Any]:
    return json.loads(RECORD.read_text(encoding="utf-8"))["skins"] if RECORD.exists() else {}


def record(skin: str) -> int:
    """Write ``skin``'s section from a fresh Python, one line per state. Refused (exit 1) when the record has it."""
    skins = recorded()
    if skin in skins:
        print(f"{RECORD.name} has {skin} already: English must not change, so it is never recorded again")
        return 1
    skins[skin] = collect(skin)
    lines = ["{", f' "_about": {json.dumps(ABOUT, ensure_ascii=False)},', ' "skins": {']
    for index, (name, states) in enumerate(sorted(skins.items())):
        lines.append(f"  {json.dumps(name)}: {{")
        items = list(states.items())
        for at, (state, words) in enumerate(items):
            comma = "," if at < len(items) - 1 else ""
            lines.append(f"   {json.dumps(state, ensure_ascii=False)}: "
                         f"{json.dumps(words, ensure_ascii=False, separators=(',', ':'))}{comma}")
        lines.append("  }" + ("," if index < len(skins) - 1 else ""))
    lines += [" }", "}"]
    RECORD.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    assert recorded() == skins
    print(f"wrote {skin}: {len(skins[skin])} states to {RECORD}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "--print" and argv[1] in SKINS:
        import usage_monitor

        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        print(json.dumps({"source": usage_monitor.__file__, "words": SKINS[argv[1]]()}, ensure_ascii=False))
        return 0
    if len(argv) == 2 and argv[0] == "--record" and argv[1] in SKINS:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        return record(argv[1])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
