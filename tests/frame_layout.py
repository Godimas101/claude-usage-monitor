"""The frame check (#94): every run of text a skin draws, read as the ink it puts down, against every other run and
every box, rule, bar and edge of its frame. For tests/test_frame_layout.py, which runs it in a fresh Python with the time zone
pinned (reset times are local), and for the fit tests.

A frame is recorded while a skin draws it, through engine.Canvas: each text layer the skin stamps (its ink: the
coverage mask at INK of 255 or more, in buffer pixels), each rectangle it fills, the strokes of each outline, the
rules and dashed lines (a fill 1 px thin), the cells of each bar, the hazard band, and the body: the polygon inside the
frame's 1 px edge, chamfered corners and all. A frame fails when:

- two runs share an ink pixel ("run on run");
- two runs on one line (their ink shares at least half the rows of the shorter) have less than MIN_GAP logical px of
  air between their ink ("runs too close"): the skins keep 6 px (QUALIFIER_GAP) and more between advances;
- a run's ink is on an outline's stroke, a rule, a dashed line or the hazard band ("run on an outline", "run on a
  rule", "run on the hazard band"), or within TOUCH buffer px of an outline's stroke, with no air between ("run
  touches an outline": Q1 keeps a mark's ink off the 1 px outline of the box it grows);
- a run's ink is partly inside a filled box and partly outside it ("run across a fill"): a chip's word stays in it;
- a run's ink is on a bar's cells ("run on a bar");
- a run's ink is outside the body ("run leaves the body"), in x or in y.

The skins whose frames are checked are THEMES: Nostromo (#94) and Night City (#95); #96 adds Gates when its words fit,
and with it what its own drawing code needs recorded (Gates' Ink.put). Night City draws through its own ink queue
(night_city._Ink), so the check hooks it too: each text layer _Ink.layer makes is a run (the hold chip's two shifted
copies, a refresh hit's and the transparent_bg backing are new layers, not runs); a value's numerals and its percent sign,
which _draw_value draws as two runs UNIT_GAP apart, are one run, the figure; an outlined chip (_chip_out) is the ring
between its two polygons (stroke_masks) and a filled chip (_chip_crit, _chip_hold) its polygon (fill_masks), checked as
outlines and fills are; the hazard strips are the tiles _Ink.hazard pastes; the bars are _draw_cells' cells and
_draw_slanted's bar; the pip is a fill; and the body is the polygon inside the frame's edge (_paint_frame).

Night City's looks place its runs differently (night_city.Look and the Edgerun plate), so its jobs are per look
(NIGHT_CITY_JOBS: every layout in every language), and two languages are a format tag of their own (TAGS: fr-CA's day
clock and es-CO's ages). It draws one state the skin-words record has not (night_city_states).

    python tests/frame_layout.py THEME LANGUAGE [VARIANT]    the findings, as JSON (an empty list when clean)
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
TOUCH = 1                       # buffer px round an outline's stroke that a run's ink must keep clear of
THEMES: tuple[str, ...] = ("nostromo", "night_city")
LANGUAGES: tuple[str, ...] = ("en", "pseudo", "fr", "es", "tr")
# A format tag drawn as a job of its own, as (language, format tag, clock): the tags whose figures the budgets' rows name
# (fr-CA's 12-hour day clock in the hover line and the row's reset, es-CO's spaced day in the band's age).
TAGS: dict[str, tuple[str, str, int]] = {"fr-CA": ("fr", "fr-CA", 12), "es-CO": ("es", "es-CO", 12)}
# Night City's jobs, as (language or tag, variant): every layout that draws its runs apart (layout_of), in the
# pseudo-locale, where the words give way the most, in English, French, Spanish and Turkish and in the two tags: boxed
# (Blackwall), plain (Relic), the plate (Edgerun), and Edgerun with transparent_bg ("edgerun/clear": the floating
# widget and the strip, the surfaces that take it), which drops the plate for a layout of its own (cells). The variant
# check runs in the pseudo-locale alone: there it holds Mox to Relic's runs and Blackwall and Relic drawn with
# transparent_bg to their own, so in the other languages those three are covered by that result, not drawn (#95
# critic 4's cost cut).
NIGHT_CITY_JOBS: tuple[tuple[str, str], ...] = tuple(
    (language, look) for language in ("pseudo", "en", "fr", "es", "tr", *TAGS)
    for look in ("blackwall", "relic", "edgerun", "edgerun/clear"))
CLEAR = "/clear"                # a job's variant drawn with transparent_bg
# What English already draws so, by design, and every language with it: (theme, surface, what, the run as a pattern),
# each with the budgets' record of it. Nostromo's 5H value at 100 % (tight: "100%", Turkish "%100") meets the 5H bar's
# last cells on a short taskbar's strip (budgets.KNOWN_ENGLISH_OVERRUNS, "nostromo.strip.value_5h"). A known overlap
# that is no longer found fails test_frame_layout.py, so this list only shrinks. Each is held to the place it names
# (KNOWN_WHERE): the 5H value only on the 5H bar's cells, never the 7D value on its own.
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
    # Shapes that are not rectangles (Night City's chips): (mask, x, y), the mask's 255 where the stroke or fill is, at
    # buffer px (x, y).
    stroke_masks: list[tuple[Image.Image, int, int]] = field(default_factory=list)
    fill_masks: list[tuple[Image.Image, int, int]] = field(default_factory=list)


@contextmanager
def recording() -> Iterator[list[Frame]]:
    """While open, every engine.Canvas that finishes a frame adds it to the list given."""
    from usage_monitor.render import engine

    Canvas = engine.Canvas
    frames: list[Frame] = []
    live: dict[int, tuple[Any, Frame]] = {}       # by id, holding the canvas so its id is not reused while recording
    texts: dict[int, tuple[Any, str]] = {}        # the same for the text layers
    figures: dict[int, int] = {}                  # a Night City value's layers, by layer id: the figure they make up
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
                run = Run(text, ink, layer.x, layer.y, (layer.x + box[0], layer.y + box[1], layer.x + box[2],
                                                        layer.y + box[3]))
                target = frame(self)
                figure = figures.get(id(layer))
                joined = target.__dict__.setdefault("_figures", {})
                if figure is not None and figure in joined:     # the figure's other run: one run, the two inks
                    target.runs[target.runs.index(joined[figure])] = joined[figure] = _joined(joined[figure], run)
                else:
                    if figure is not None:
                        joined[figure] = run
                    target.runs.append(run)
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
            done[1].__dict__.pop("_figures", None)
            frames.append(done[1])
        return real["finish"](self, *args, **kwargs)

    spies = {"text_layer": text_layer, "stamp": stamp, "fill_rect": fill_rect, "outline_rect": outline_rect,
             "dotted_hline": dotted_hline, "segment_layers": segment_layers, "hazard": hazard,
             "gradient_fill": gradient_fill, "fill_ring": fill_ring, "finish": finish}
    night = _night_city_spies(frame, texts, figures)
    for name, spy in spies.items():
        setattr(Canvas, name, spy)
    for (owner, name), (spy, _method) in night.items():
        setattr(owner, name, spy)
    try:
        yield frames
    finally:
        for name, method in real.items():
            setattr(Canvas, name, method)
        for (owner, name), (_spy, method) in night.items():
            setattr(owner, name, method)


def _joined(a: Run, b: Run) -> Run:
    """Two runs' ink as one run (Night City's value: its numerals and its percent sign), its text in reading order."""
    left, top = min(a.box[0], b.box[0]), min(a.box[1], b.box[1])
    right, bottom = max(a.box[2], b.box[2]), max(a.box[3], b.box[3])
    ink = Image.new("L", (right - left, bottom - top), 0)
    for run in (a, b):
        part = run.ink.crop((run.box[0] - run.x, run.box[1] - run.y, run.box[2] - run.x, run.box[3] - run.y))
        at = (run.box[0] - left, run.box[1] - top, run.box[2] - left, run.box[3] - top)
        ink.paste(ImageChops.lighter(ink.crop(at), part), at[:2])
    text = "".join(run.text for run in sorted((a, b), key=lambda run: run.box[0]))
    return Run(text, ink, left, top, (left, top, right, bottom))


def _night_city_spies(frame: Callable[[Any], Frame], texts: dict[int, tuple[Any, str]],
                      figures: dict[int, int]) -> dict[tuple[Any, str], tuple[Any, Any]]:
    """The hooks Night City's own drawing needs (the module's docstring says what each records), as {(owner, name):
    (spy, the real one)}."""
    from usage_monitor.render import engine
    from usage_monitor.render.skins import night_city as nc

    Ink = nc._Ink
    real = {name: getattr(Ink, name) for name in ("layer", "hazard", "polygon")}
    real_nc = {name: getattr(nc, name) for name in ("_chip_out", "_chip_crit", "_chip_hold", "_draw_cells",
                                                     "_draw_slanted", "_paint_frame", "_pip", "_draw_value")}
    polygons: list[list[Any]] = []                # the polygons each chip being drawn queues, innermost last
    figure: list[int | None] = [None]             # the value being drawn, if any
    count = [0]

    def layer(self: Any, text: str, *args: Any, **kwargs: Any) -> Any:
        out = real["layer"](self, text, *args, **kwargs)
        texts[id(out[0])] = (out[0], str(text))
        if figure[0] is not None:
            figures[id(out[0])] = figure[0]
        return out

    def hazard(self: Any, rect: Any, *args: Any, **kwargs: Any) -> None:
        frame(self.cv).hazard.append(self.cv._rect_px(*rect))
        return real["hazard"](self, rect, *args, **kwargs)

    def polygon(self: Any, points: Any, color: Any) -> None:
        if polygons:
            polygons[-1].append(points)
        return real["polygon"](self, points, color)

    def shapes(draw: Callable[[], Any]) -> tuple[Any, list[Any]]:
        polygons.append([])
        try:
            return draw(), polygons[-1]
        finally:
            polygons.pop()

    def cropped(mask: Image.Image) -> tuple[Image.Image, int, int] | None:
        box = mask.getbbox()
        return None if box is None else (mask.crop(box), box[0], box[1])

    def chip_out(ink: Any, *args: Any, **kwargs: Any) -> Any:
        width, made = shapes(lambda: real_nc["_chip_out"](ink, *args, **kwargs))
        ring = cropped(ImageChops.subtract(ink.cv._full_mask(made[0]), ink.cv._full_mask(made[1])))
        if ring is not None:
            frame(ink.cv).stroke_masks.append(ring)
        return width

    def filled(name: str) -> Callable[..., Any]:
        def chip(ink: Any, *args: Any, **kwargs: Any) -> Any:
            width, made = shapes(lambda: real_nc[name](ink, *args, **kwargs))
            fill = cropped(ink.cv._full_mask(made[0]))
            if fill is not None:
                frame(ink.cv).fill_masks.append(fill)
            return width
        return chip

    def draw_cells(ink: Any, x: float, y: float, step: float, width: float, height: float, cells: int,
                   *args: Any, **kwargs: Any) -> Any:
        frame(ink.cv).cells.extend(ink.cv._rect_px(x + step * i, y, x + step * i + width, y + height)
                                   for i in range(cells))
        return real_nc["_draw_cells"](ink, x, y, step, width, height, cells, *args, **kwargs)

    def draw_slanted(ink: Any, x: float, y: float, width: float, height: float, *args: Any, **kwargs: Any) -> Any:
        frame(ink.cv).cells.append(ink.cv._rect_px(x, y, x + width, y + height))
        return real_nc["_draw_slanted"](ink, x, y, width, height, *args, **kwargs)

    def paint_frame(cv: Any, pal: Any, width: float, height: float, chamfer: float, opts: Any) -> Any:
        frame(cv).body = cv._full_mask(engine.chamfer_polygon(1, 1, width - 1, height - 1, chamfer - 1, chamfer - 1))
        return real_nc["_paint_frame"](cv, pal, width, height, chamfer, opts)

    def pip(ink: Any, x: float, y: float, *args: Any, **kwargs: Any) -> Any:
        frame(ink.cv).fills.append(ink.cv._rect_px(x, y, x + nc.PIP, y + nc.PIP))
        return real_nc["_pip"](ink, x, y, *args, **kwargs)

    def draw_value(*args: Any, **kwargs: Any) -> Any:
        count[0] += 1
        figure[0] = count[0]
        try:
            return real_nc["_draw_value"](*args, **kwargs)
        finally:
            figure[0] = None

    spies: dict[tuple[Any, str], tuple[Any, Any]] = {
        (Ink, "layer"): (layer, real["layer"]), (Ink, "hazard"): (hazard, real["hazard"]),
        (Ink, "polygon"): (polygon, real["polygon"])}
    for name, spy in (("_chip_out", chip_out), ("_chip_crit", filled("_chip_crit")),
                      ("_chip_hold", filled("_chip_hold")), ("_draw_cells", draw_cells),
                      ("_draw_slanted", draw_slanted), ("_paint_frame", paint_frame), ("_pip", pip),
                      ("_draw_value", draw_value)):
        spies[nc, name] = (spy, real_nc[name])
    return spies


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


def _meets_mask(run: Run, mask: Image.Image, x: int, y: int, outside: bool = False) -> bool:
    """Whether the run's ink has a pixel where ``mask`` (at buffer px x, y) is set; with ``outside``, where it is not
    (beyond the mask's own box too)."""
    ink = run.ink.crop((run.box[0] - run.x, run.box[1] - run.y, run.box[2] - run.x, run.box[3] - run.y))
    part = mask.crop((run.box[0] - x, run.box[1] - y, run.box[2] - x, run.box[3] - y))
    return ImageChops.multiply(ink, ImageChops.invert(part) if outside else part).getbbox() is not None


def _grown(mask: Image.Image, by: int) -> Image.Image:
    """``mask`` with ``by`` buffer px added on every side, the shape grown into them."""
    from PIL import ImageFilter

    out = Image.new("L", (mask.width + 2 * by, mask.height + 2 * by), 0)
    out.paste(mask, (by, by))
    return out.filter(ImageFilter.MaxFilter(2 * by + 1))


def _mask_box(mask: Image.Image, x: int, y: int) -> tuple[int, int, int, int]:
    return x, y, x + mask.width, y + mask.height


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
        on: set[str] = set()
        for what, rects in (("run on an outline", frame.strokes), ("run on a rule", frame.rules),
                            ("run on a bar", frame.cells), ("run on the hazard band", frame.hazard)):
            for rect in rects:
                if _meets(run, rect):
                    found.append((what, run.text, logical(rect)))
                    on.add(what)
                    break
        if "run on an outline" not in on:
            for mask, x, y in frame.stroke_masks:
                if _meets_mask(run, mask, x, y):
                    found.append(("run on an outline", run.text, logical(_mask_box(mask, x, y))))
                    on.add("run on an outline")
                    break
        if "run on an outline" not in on:
            for rect in frame.strokes:
                if _meets(run, (rect[0] - TOUCH, rect[1] - TOUCH, rect[2] + TOUCH, rect[3] + TOUCH)):
                    found.append(("run touches an outline", run.text, logical(rect)))
                    on.add("run touches an outline")
                    break
        if "run on an outline" not in on and "run touches an outline" not in on:
            for mask, x, y in frame.stroke_masks:
                if _meets_mask(run, _grown(mask, TOUCH), x - TOUCH, y - TOUCH):
                    found.append(("run touches an outline", run.text, logical(_mask_box(mask, x, y))))
                    break
        for rect in frame.fills:
            if _meets(run, rect) and not (rect[0] <= run.box[0] and rect[1] <= run.box[1]
                                          and run.box[2] <= rect[2] and run.box[3] <= rect[3]):
                found.append(("run across a fill", run.text, logical(rect)))
        for mask, x, y in frame.fill_masks:
            if _meets_mask(run, mask, x, y) and _meets_mask(run, mask, x, y, outside=True):
                found.append(("run across a fill", run.text, logical(_mask_box(mask, x, y))))
        if frame.body is not None and _outside(run, frame.body):
            found.append(("run leaves the body", run.text, logical(run.box)))
    return found


# ── Every frame of a theme ────────────────────────────────────────────────────

def strip_draws(theme_id: str) -> list[tuple[str, int, float]]:
    """(name, taskbar height in device px, display scale) for each strip the check draws: Nostromo's at every
    budgets.STRIP_SCALES, a strip exactly as tall as itself; Night City's at each taskbar height the frame dump draws
    at each display scale (budgets.SCALES), one per scale the skin settles on (night_city.render_taskbar shrinks the
    strip to the bar less STRIP_MARGIN a side, and centres it in a taller one)."""
    import budgets
    from usage_monitor.render import dump

    if theme_id != "night_city":
        from usage_monitor.render.skins import nostromo

        return [(f"strip@{scale:.4g}", round(nostromo.STRIP_H * scale), scale) for scale in budgets.STRIP_SCALES]
    from usage_monitor.render.skins import night_city as nc

    seen: dict[float, tuple[str, int, float]] = {}
    for scale in budgets.SCALES:
        for height in dump.TASKBAR_HEIGHTS_AT_1X:
            px = round(height * scale)
            settled = round(max(0.25, min(scale, (px - 2 * nc.STRIP_MARGIN) / nc.STRIP_H)), 4)
            seen.setdefault(settled, (f"strip@{settled:.4g}", px, scale))
    return sorted(seen.values(), key=lambda item: item[2] * 1000 + item[1])


def surfaces(theme_id: str, model: Any, variant: Any = None, opts: Any = None) -> Iterator[tuple[str, Callable[[], Any]]]:
    """(name, draw) for each surface and scale the check draws ``model`` at: the floating widget and the hover card at
    every display scale Windows offers up to 200 % (budgets.SCALES, 1.75 too), and the strip at every scale a taskbar
    draws it at (strip_draws). With ``opts`` (RenderOptions) only the surfaces that take them, the floating widget and
    the strip."""
    import budgets
    from usage_monitor.render import dump
    from usage_monitor.render.skins import get_skin

    theme = dump.load_theme_objects()[theme_id]
    skin, variant = get_skin(theme_id), variant or theme.variants[0]
    for scale in budgets.SCALES:
        yield f"floating@{scale:g}", lambda s=scale: skin.render_floating(model, theme, variant, s, opts)
        if opts is None:
            yield f"tooltip@{scale:g}", lambda s=scale: skin.render_tooltip(model, theme, variant, s)
    for name, height, scale in strip_draws(theme_id):
        yield name, lambda h=height, s=scale: skin.render_taskbar(model, theme, variant, h, s, opts)


def locale_of(language: str) -> Any:
    """The locale a job draws in: a dev instance's for a DEV_LANGUAGES id, else a format tag's of TAGS (a 12-hour
    clock: the widest day clocks)."""
    from usage_monitor import i18n

    if language in TAGS:
        base, tag, clock = TAGS[language]
        return i18n.Locale(setting="system", language=base, system=i18n.DEFAULT, format=tag, clock=clock,
                           offered=(i18n.DEFAULT,))
    return i18n.dev_locale(language)


def night_city_states() -> dict[str, Any]:
    """The states tests/skin_words.py records for Night City (frozen with its English, so it takes no new one), and the
    ones the frame check draws besides: a missing sign-in that keeps its last reading while the guard holds, so the
    band's no-login cause sits beside the hold chip (#95's review)."""
    import dataclasses

    import skin_words
    from usage_monitor.data.models import ApiStatus
    from usage_monitor.render import dump

    return {**skin_words._night_city_states(), "band_hold/no_credentials": dataclasses.replace(
        dump.sample_state("guard_hold_session"), status=ApiStatus.NO_CREDENTIALS)}


def models(language: str, theme_id: str = "nostromo") -> dict[str, Any]:
    """Every state tests/skin_words.py records for ``theme_id`` (the dump's in every show_scoped mode, the edge and
    guard states and the theme's own; for Night City night_city_states), as view models in ``language``'s locale
    (locale_of)."""
    import skin_words

    states = night_city_states if theme_id == "night_city" else skin_words._nostromo_states
    return skin_words.models(states, locale_of(language))


def _strip_bar(group: int) -> tuple[float, float]:
    """Where Nostromo's strip draws a group's bar, in logical px: from its first cell's left to its last cell's right."""
    from usage_monitor.render.skins import nostromo

    bar_x = nostromo.STRIP_GROUPS[group][1]
    return bar_x, bar_x + nostromo.STRIP_CELL_STEP * (nostromo.STRIP_CELLS - 1) + nostromo.STRIP_CELL_W


# The x span (logical px) a KNOWN entry's box must lie in: the bar its box id names.
KNOWN_WHERE: dict[str, Callable[[], tuple[float, float]]] = {"nostromo.strip.value_5h": lambda: _strip_bar(0)}


def known(theme_id: str, finding: list[str]) -> tuple[str, str, str, str] | None:
    """The KNOWN entry a finding ([state, surface, what, run, other]) is, or None: its surface, kind and run, and the
    box it meets ("other", "(x0, y0, x1, y1)" in logical px) inside the span its entry names (KNOWN_WHERE)."""
    _state, surface, what, text, other = finding
    for entry, box_id in KNOWN.items():
        if entry[:3] == (theme_id, surface.split("@")[0], what) and re.fullmatch(entry[3], text):
            found = re.fullmatch(r"\(([-\d.]+), [-\d.]+, ([-\d.]+), [-\d.]+\)", other)
            low, high = KNOWN_WHERE[box_id]()
            if found and low - 1 <= float(found[1]) and float(found[2]) <= high + 1:      # a px of rounding
                return entry
    return None


def findings(theme_id: str, language: str, variant_id: str | None = None) -> list[list[str]]:
    """Every finding in every frame of ``theme_id`` in ``language`` (a variant's, the first when None; one ending in
    CLEAR drawn with transparent_bg): [state, surface, what, run, other], the KNOWN ones too."""
    from usage_monitor.render import dump
    from usage_monitor.render.skins import RenderOptions

    clear = bool(variant_id) and variant_id.endswith(CLEAR)
    variant_id = variant_id[:-len(CLEAR)] if clear and variant_id else variant_id
    variant = dump.load_theme_objects()[theme_id].variant(variant_id) if variant_id else None
    opts = RenderOptions(transparent_bg=True) if clear else None
    found: list[list[str]] = []
    for name, model in models(language, theme_id).items():
        for surface, draw in surfaces(theme_id, model, variant, opts):
            for what, text, other in check(drawn(draw)):
                found.append([name, surface, what, text, other])
    return found


def layout_of(theme_id: str, variant: Any, clear: bool = False) -> str:
    """Which variants draw the same runs: Nostromo's all (they differ in colour only); Night City's by its look, its
    bars and the plate: "boxed" Blackwall, "plate" the Edgerun plate, "plain" Relic and Mox (slanted bars), "cells"
    Edgerun without its plate (transparent_bg turns it off: night_city._palette), whose segmented bars end 1 px past
    the slanted one's and leave the strip's percent that much less room."""
    if theme_id != "night_city":
        return "all"
    from usage_monitor.render import dump
    from usage_monitor.render.skins import night_city as nc

    look = nc.look_for(variant)
    if look.boxed:
        return "boxed"
    if nc._plate_tokens(dump.load_theme_objects()[theme_id], variant) and not clear:
        return "plate"
    return "cells" if look.segmented else "plain"


def runs_by_variant(theme_id: str, language: str) -> dict[str, dict[str, list[list[Any]]]]:
    """For each state, each variant's runs at 1.0 by surface (floating widget, hover card, strip: text, buffer x and y
    of the layer, its ink box), keyed "<state>|<layout>|<variant>" (layout_of), so a test can hold every variant to the
    first of its layout. Night City's are also drawn with transparent_bg (the floating widget and the strip, the
    surfaces that take it), keyed "<state>|<layout>/clear|<variant>"."""
    from usage_monitor.render import dump
    from usage_monitor.render.skins import RenderOptions, get_skin

    theme = dump.load_theme_objects()[theme_id]
    skin = get_skin(theme_id)

    def runs(draw: Callable[[], Any]) -> list[list[Any]]:
        return [[run.text, run.x, run.y, list(run.box)] for run in drawn(draw).runs]

    out: dict[str, dict[str, list[list[Any]]]] = {}
    for name, model in models(language, theme_id).items():
        for variant in theme.variants:
            out[f"{name}|{layout_of(theme_id, variant)}|{variant.id}"] = {
                "floating": runs(lambda: skin.render_floating(model, theme, variant, 1.0, None)),
                "tooltip": runs(lambda: skin.render_tooltip(model, theme, variant, 1.0)),
                "strip": runs(lambda: skin.render_taskbar(model, theme, variant, 48, 1.0, None))}
            if theme_id == "night_city":
                clear = RenderOptions(transparent_bg=True)
                out[f"{name}|{layout_of(theme_id, variant, clear=True)}/clear|{variant.id}"] = {
                    "floating": runs(lambda: skin.render_floating(model, theme, variant, 1.0, clear)),
                    "strip": runs(lambda: skin.render_taskbar(model, theme, variant, 48, 1.0, clear))}
    return out


def jobs() -> list[tuple[str, ...]]:
    """What test_frame_layout.py runs, each in a fresh Python: ("findings", theme, language[, variant]) for Nostromo in
    every language on its first variant and for Night City's NIGHT_CITY_JOBS, and ("variants", theme, "pseudo")."""
    found: list[tuple[str, ...]] = []
    for theme in THEMES:
        if theme == "night_city":
            found += [("findings", theme, language, variant) for language, variant in NIGHT_CITY_JOBS]
        else:
            found += [("findings", theme, language) for language in LANGUAGES]
    return found + [("variants", theme, "pseudo") for theme in THEMES]


def collect(theme_id: str, language: str, what: str = "findings") -> Any:
    """``findings`` (or ``runs_by_variant``) worked out in a fresh Python with the time zone pinned."""
    done = subprocess.run([sys.executable, str(Path(__file__).resolve()), what, theme_id, language], cwd=REPO,
                          env={**os.environ, "TZ": PINNED_TZ, "PYTHONPATH": os.pathsep.join((str(REPO), str(HERE)))},
                          capture_output=True, encoding="utf-8", timeout=1800, check=False, creationflags=NO_WINDOW)
    if done.returncode:
        raise RuntimeError(f"the frame check could not run:\n{done.stderr[-3000:]}")
    return json.loads(done.stdout)


def main(argv: list[str]) -> int:
    languages = (*LANGUAGES, *TAGS)
    if len(argv) in (3, 4) and argv[0] in ("findings", "variants") and argv[1] in THEMES and argv[2] in languages:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        result = (findings(argv[1], argv[2], *argv[3:]) if argv[0] == "findings"
                  else runs_by_variant(argv[1], argv[2]))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if len(argv) in (2, 3) and argv[0] in THEMES and argv[1] in languages:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        print(json.dumps(findings(argv[0], argv[1], *argv[2:]), ensure_ascii=False, indent=1))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
