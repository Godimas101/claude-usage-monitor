"""The frame check (#94): every run of text a skin draws, read as the ink it puts down, against every other run and every
box, rule, bar and edge of its frame. For tests/test_frame_layout.py, which runs it in a fresh Python with the time zone
pinned (reset times are local), and for the fit tests.

A frame is recorded while a skin draws it, through engine.Canvas: each text layer the skin stamps (its ink: the
coverage mask at INK of 255 or more, in buffer pixels), each rectangle it fills, the strokes of each outline, the
rules and dashed lines (a fill 1 px thin), the cells of each bar, the hazard band, and the body: the polygon inside the
frame's 1 px edge, chamfered corners and all. A frame fails when:

- two runs share an ink pixel ("run on run");
- two runs on one line (their ink shares at least half the rows of the shorter) have less than MIN_GAP logical px of
  air between their ink ("runs too close"): the skins keep 6 px (QUALIFIER_GAP) and more between advances;
- a run's ink is on an outline's stroke, a rule, a dashed line or the hazard band ("run on an outline", "run on a
  rule", "run on the hazard band");
- a run's ink is partly inside a filled box and partly outside it ("run across a fill"): a chip's word stays in it;
- a run's ink is on a bar's cells ("run on a bar");
- a run's ink is outside the body ("run leaves the body"), in x or in y.

The skins whose frames are checked are THEMES: Nostromo (#94). #95 and #96 add Night City and Gates when their words
fit, and with them what their own drawing code needs recorded (Night City's ink and Gates' Ink.put).

    python tests/frame_layout.py THEME LANGUAGE     the findings, as JSON (an empty list when the frames are clean)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from PIL import Image, ImageChops  # noqa: E402

PINNED_TZ = "UTC0"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
INK = 64                        # the coverage (of 255) that counts as ink, as the probes for #94's plan count it
MIN_GAP = 2.0                   # logical px of air between the ink of two runs on one line
THEMES: tuple[str, ...] = ("nostromo",)
LANGUAGES: tuple[str, ...] = ("en", "pseudo", "fr", "es", "tr")
# What English already draws so, by design, and every language with it: (theme, surface, what, the run as a pattern),
# each with the budgets' record of it. Nostromo's 5H value at 100 % (tight: "100%", Turkish "%100") meets the 5H bar's
# last cells on a short taskbar's strip (budgets.KNOWN_ENGLISH_OVERRUNS, "nostromo.strip.value_5h"). A known overlap
# that is no longer found fails test_frame_layout.py, so this list only shrinks.
KNOWN: dict[tuple[str, str, str, str], str] = {
    ("nostromo", "strip", "run on a bar", r"%?100%?"): "nostromo.strip.value_5h",
}
_INK_TABLE = [255 if value >= INK else 0 for value in range(256)]


@dataclass
class Run:
    text: str
    ink: Image.Image            # mode "1"-like L mask: 255 where the run puts ink down
    x: int                      # buffer position of the mask
    y: int
    box: tuple[int, int, int, int]      # the ink's bounding box, buffer px (left, top, right, bottom), exclusive


@dataclass
class Frame:
    """One canvas as a skin drew it: its runs and boxes, in buffer px, and its scale (kx, ky buffer px per logical
    px)."""
    width: int
    height: int
    kx: float
    ky: float
    runs: list[Run] = field(default_factory=list)
    fills: list[tuple[int, int, int, int]] = field(default_factory=list)
    strokes: list[tuple[int, int, int, int]] = field(default_factory=list)
    rules: list[tuple[int, int, int, int]] = field(default_factory=list)
    cells: list[tuple[int, int, int, int]] = field(default_factory=list)
    hazard: list[tuple[int, int, int, int]] = field(default_factory=list)
    body: Image.Image | None = None


@contextmanager
def recording() -> Iterator[list[Frame]]:
    """While open, every engine.Canvas that finishes a frame adds it to the list given."""
    from usage_monitor.render import engine

    Canvas = engine.Canvas
    frames: list[Frame] = []
    live: dict[int, tuple[Any, Frame]] = {}       # by id, holding the canvas so its id is not reused while recording
    texts: dict[int, tuple[Any, str]] = {}        # the same for the text layers
    inside: list[str] = []
    real = {name: getattr(Canvas, name) for name in ("text_layer", "stamp", "fill_rect", "outline_rect",
                                                      "dotted_hline", "segment_layers", "hazard", "gradient_fill",
                                                      "fill_ring", "finish")}

    def frame(cv: Any) -> Frame:
        key = id(cv)
        if key not in live:
            live[key] = (cv, Frame(cv.buf_w, cv.buf_h, cv.kx, cv.ky))
        return live[key][1]

    def text_layer(self: Any, text: str, *args: Any, **kwargs: Any) -> Any:
        layer, advance = real["text_layer"](self, text, *args, **kwargs)
        texts[id(layer)] = (layer, str(text))
        return layer, advance

    def stamp(self: Any, layer: Any, color: Any) -> None:
        held = texts.get(id(layer))
        text = held[1] if held is not None and held[0] is layer else None
        if text is not None and color[3] > 0:
            ink = layer.mask.point(_INK_TABLE)
            box = ink.getbbox()
            if box is not None:
                frame(self).runs.append(Run(text, ink, layer.x, layer.y, (layer.x + box[0], layer.y + box[1],
                                                                         layer.x + box[2], layer.y + box[3])))
        return real["stamp"](self, layer, color)

    def fill_rect(self: Any, x0: float, y0: float, x1: float, y1: float, color: Any) -> None:
        rect = self._rect_px(x0, y0, x1, y1)
        if color[3] > 0 and rect[2] > rect[0] and rect[3] > rect[1]:
            target = frame(self)
            if inside and inside[-1] == "outline":
                target.strokes.append(rect)
            elif inside and inside[-1] == "dotted":
                target.rules.append(rect)
            elif min(x1 - x0, y1 - y0) <= 1:
                target.rules.append(rect)
            else:
                target.fills.append(rect)
        return real["fill_rect"](self, x0, y0, x1, y1, color)

    def outline_rect(self: Any, *args: Any, **kwargs: Any) -> None:
        inside.append("outline")
        try:
            return real["outline_rect"](self, *args, **kwargs)
        finally:
            inside.pop()

    def dotted_hline(self: Any, x0: float, x1: float, y: float, on: float, off: float, color: Any) -> None:
        if color[3] > 0:
            x = x0
            while x < x1:
                frame(self).rules.append(self._rect_px(x, y, min(x + on, x1), y + 1))
                x += on + off
        return real["dotted_hline"](self, x0, x1, y, on, off, color)

    def segment_layers(self: Any, x: float, y: float, step: float, cell_w: float, cell_h: float, cells: int,
                       *args: Any, **kwargs: Any) -> Any:
        frame(self).cells.extend(self._rect_px(x + step * i, y, x + step * i + cell_w, y + cell_h)
                                 for i in range(cells))
        return real["segment_layers"](self, x, y, step, cell_w, cell_h, cells, *args, **kwargs)

    def hazard(self: Any, points: Any, x0: float, x1: float, y0: float, y1: float, *args: Any, **kwargs: Any) -> None:
        frame(self).hazard.append(self._rect_px(x0, y0, x1, y1))
        return real["hazard"](self, points, x0, x1, y0, y1, *args, **kwargs)

    def gradient_fill(self: Any, points: Any, *args: Any, **kwargs: Any) -> None:
        frame(self).body = self._full_mask(points)
        return real["gradient_fill"](self, points, *args, **kwargs)

    def fill_ring(self: Any, outer: Any, inner: Any, color: Any) -> None:
        frame(self).body = self._full_mask(inner)
        return real["fill_ring"](self, outer, inner, color)

    def finish(self: Any, *args: Any, **kwargs: Any) -> Any:
        done = live.pop(id(self), None)
        if done is not None:
            frames.append(done[1])
        return real["finish"](self, *args, **kwargs)

    spies = {"text_layer": text_layer, "stamp": stamp, "fill_rect": fill_rect, "outline_rect": outline_rect,
             "dotted_hline": dotted_hline, "segment_layers": segment_layers, "hazard": hazard,
             "gradient_fill": gradient_fill, "fill_ring": fill_ring, "finish": finish}
    for name, spy in spies.items():
        setattr(Canvas, name, spy)
    try:
        yield frames
    finally:
        for name, method in real.items():
            setattr(Canvas, name, method)


def drawn(draw: Callable[[], Any]) -> Frame:
    """The one frame ``draw`` finishes."""
    with recording() as frames:
        draw()
    assert len(frames) == 1, f"{len(frames)} frames finished"
    return frames[0]


# ── Checks ────────────────────────────────────────────────────────────────────

def _meets(run: Run, rect: tuple[int, int, int, int]) -> bool:
    """Whether the run's ink has a pixel inside ``rect`` (buffer px, exclusive)."""
    x0, y0 = max(rect[0], run.box[0]), max(rect[1], run.box[1])
    x1, y1 = min(rect[2], run.box[2]), min(rect[3], run.box[3])
    if x1 <= x0 or y1 <= y0:
        return False
    return run.ink.crop((x0 - run.x, y0 - run.y, x1 - run.x, y1 - run.y)).getbbox() is not None


def _shared(a: Run, b: Run) -> bool:
    x0, y0 = max(a.box[0], b.box[0]), max(a.box[1], b.box[1])
    x1, y1 = min(a.box[2], b.box[2]), min(a.box[3], b.box[3])
    if x1 <= x0 or y1 <= y0:
        return False
    one = a.ink.crop((x0 - a.x, y0 - a.y, x1 - a.x, y1 - a.y))
    two = b.ink.crop((x0 - b.x, y0 - b.y, x1 - b.x, y1 - b.y))
    return ImageChops.multiply(one, two).getbbox() is not None


def _outside(run: Run, body: Image.Image) -> bool:
    """Whether any of the run's ink lies outside the body mask (or outside the canvas)."""
    left, top, right, bottom = run.box
    if left < 0 or top < 0 or right > body.width or bottom > body.height:
        return True
    outside = ImageChops.invert(body.crop(run.box))
    ink = run.ink.crop((left - run.x, top - run.y, right - run.x, bottom - run.y))
    return ImageChops.multiply(ink, outside).getbbox() is not None


def check(frame: Frame) -> list[tuple[str, str, str]]:
    """The findings of one frame: (what, the run's text, the other run's text or the box, in logical px)."""
    found: list[tuple[str, str, str]] = []

    def logical(rect: tuple[int, int, int, int]) -> str:
        return "({:.1f}, {:.1f}, {:.1f}, {:.1f})".format(rect[0] / frame.kx, rect[1] / frame.ky, rect[2] / frame.kx,
                                                         rect[3] / frame.ky)

    runs = frame.runs
    for i, a in enumerate(runs):
        for b in runs[i + 1:]:
            if _shared(a, b):
                found.append(("run on run", a.text, b.text))
                continue
            rows = min(a.box[3], b.box[3]) - max(a.box[1], b.box[1])
            if rows <= 0 or rows < 0.5 * min(a.box[3] - a.box[1], b.box[3] - b.box[1]):
                continue                                  # not on one line
            gap = max(a.box[0], b.box[0]) - min(a.box[2], b.box[2])
            if gap < MIN_GAP * frame.kx:
                found.append(("runs too close", a.text, f"{b.text} ({gap / frame.kx:.2f} px)"))
    for run in runs:
        for what, rects in (("run on an outline", frame.strokes), ("run on a rule", frame.rules),
                            ("run on a bar", frame.cells), ("run on the hazard band", frame.hazard)):
            for rect in rects:
                if _meets(run, rect):
                    found.append((what, run.text, logical(rect)))
                    break
        for rect in frame.fills:
            if _meets(run, rect) and not (rect[0] <= run.box[0] and rect[1] <= run.box[1]
                                          and run.box[2] <= rect[2] and run.box[3] <= rect[3]):
                found.append(("run across a fill", run.text, logical(rect)))
        if frame.body is not None and _outside(run, frame.body):
            found.append(("run leaves the body", run.text, logical(run.box)))
    return found


# ── Every frame of a theme ────────────────────────────────────────────────────

def surfaces(theme_id: str, model: Any, variant: Any = None) -> Iterator[tuple[str, Callable[[], Any]]]:
    """(name, draw) for each surface and scale the check draws ``model`` at: the floating widget and the hover card at
    every display scale Windows offers up to 200 % (budgets.SCALES, 1.75 too), and the strip at every scale a taskbar
    draws it at (budgets.STRIP_SCALES)."""
    import budgets
    from usage_monitor.render import dump
    from usage_monitor.render.skins import get_skin

    theme = dump.load_theme_objects()[theme_id]
    skin, variant = get_skin(theme_id), variant or theme.variants[0]
    for scale in budgets.SCALES:
        yield f"floating@{scale:g}", lambda s=scale: skin.render_floating(model, theme, variant, s, None)
        yield f"tooltip@{scale:g}", lambda s=scale: skin.render_tooltip(model, theme, variant, s)
    strip_h = getattr(sys.modules[type(skin).__module__], "STRIP_H", 34)
    for scale in budgets.STRIP_SCALES:
        yield f"strip@{scale:.4g}", lambda s=scale: skin.render_taskbar(model, theme, variant, round(strip_h * s), s,
                                                                        None)


def models(language: str) -> dict[str, Any]:
    """Every state tests/skin_words.py records (the dump's in every show_scoped mode, the edge and guard states and
    Nostromo's own), as view models in ``language``'s dev locale."""
    import skin_words
    from usage_monitor import i18n

    return skin_words.models(skin_words._nostromo_states, i18n.dev_locale(language))


def known(theme_id: str, finding: list[str]) -> tuple[str, str, str, str] | None:
    """The KNOWN entry a finding ([state, surface, what, run, other]) is, or None."""
    _state, surface, what, text, _other = finding
    for entry in KNOWN:
        if entry[:3] == (theme_id, surface.split("@")[0], what) and re.fullmatch(entry[3], text):
            return entry
    return None


def findings(theme_id: str, language: str) -> list[list[str]]:
    """Every finding in every frame of ``theme_id`` in ``language``: [state, surface, what, run, other], the KNOWN
    ones too."""
    found: list[list[str]] = []
    for name, model in models(language).items():
        for surface, draw in surfaces(theme_id, model):
            for what, text, other in check(drawn(draw)):
                found.append([name, surface, what, text, other])
    return found


def runs_by_variant(theme_id: str, language: str) -> dict[str, list[list[Any]]]:
    """For each state, each variant's runs at 1.0 on every surface (text, buffer x and y of the layer, its ink box),
    keyed "<state>|<variant>", so a test can hold every variant to the first (they differ in colour only)."""
    from usage_monitor.render import dump
    from usage_monitor.render.skins import get_skin

    theme = dump.load_theme_objects()[theme_id]
    skin = get_skin(theme_id)
    out: dict[str, list[list[Any]]] = {}
    for name, model in models(language).items():
        for variant in theme.variants:
            with recording() as frames:
                skin.render_floating(model, theme, variant, 1.0, None)
                skin.render_tooltip(model, theme, variant, 1.0)
                skin.render_taskbar(model, theme, variant, 48, 1.0, None)
            out[f"{name}|{variant.id}"] = [[run.text, run.x, run.y, list(run.box)] for frame in frames
                                           for run in frame.runs]
    return out


def collect(theme_id: str, language: str, what: str = "findings") -> Any:
    """``findings`` (or ``runs_by_variant``) worked out in a fresh Python with the time zone pinned."""
    done = subprocess.run([sys.executable, str(Path(__file__).resolve()), what, theme_id, language], cwd=REPO,
                          env={**os.environ, "TZ": PINNED_TZ, "PYTHONPATH": os.pathsep.join((str(REPO), str(HERE)))},
                          capture_output=True, encoding="utf-8", timeout=1800, check=False, creationflags=NO_WINDOW)
    if done.returncode:
        raise RuntimeError(f"the frame check could not run:\n{done.stderr[-3000:]}")
    return json.loads(done.stdout)


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[0] in ("findings", "variants") and argv[1] in THEMES and argv[2] in LANGUAGES:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        result = findings(argv[1], argv[2]) if argv[0] == "findings" else runs_by_variant(argv[1], argv[2])
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if len(argv) == 2 and argv[0] in THEMES and argv[1] in LANGUAGES:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        print(json.dumps(findings(argv[0], argv[1]), ensure_ascii=False, indent=1))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
