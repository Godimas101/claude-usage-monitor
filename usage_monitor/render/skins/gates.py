"""Gates skin, "Readouts on native Fluent surfaces": the approved Windows 11 design (design/mockups/gates).

Geometry is the approved 1x layout in logical pixels (see design/mockups/gates/DESIGN_NOTES.md and the
CSS in final.html). Shapes are drawn on a Canvas at four times the output scale (twice from 200 %) with
anti-aliased masks and downsampled with premultiplied alpha, so rounded corners and 1 px rules are
crisp at 1, 1.25, 1.5 and 2. Text is drawn afterwards at output size, where FreeType hints it to the
pixel grid as the browser did in the mockup. Every colour comes from the variant's tokens (the sixteen
roles in variant.colors, the per-mode roles in theme.extras.modes and the per-variant extras), so all
fourteen variants render with the same code, and the "system" variant carries the live Windows accent
in colors["accent"]: lifted to read on the card, and kept out of the severity colours for normal fills
(Palette.fill; usage_monitor/themes/__init__.py, "Gates accents").

Surfaces
  floating   a 288 px wide card in a frame 24 px bigger on every side, which holds the soft shadow. The card shows
             the session and weekly rows, then one row for every scoped limit (Fable) the model carries, in every
             state, and the footer last: 128 px tall with two rows, 27 px more for each further row. content_insets()
             says how much of the frame is margin, so the widgets place the card and not the padded frame.
  tooltip    the same card, taller: one block per row with the exact percentage, reset time and pace.
  taskbar    a 248 x 40 strip (a Fluent "hover" plate) that adapts to the taskbar height.
  tray       a ring of the session percent; the 32 px size prints the number.

Real Mica and Acrylic need a DWM backdrop that a per-pixel-alpha layered window cannot have, so the
card is a plain Fluent surface: widget_bg (the approved 95 % fill), a 1 px border, an 8 px corner
radius and a two-layer shadow. The strip has its own plate and the tray ring a thin outline in the
taskbar colour, so both read on a dark and on a light taskbar whatever the Windows modes are. Windows
fonts are read from the Fonts folder, never bundled.

Gates has no ambient animation and no glow: the lamp is steady (blink_on and reduce_motion change nothing
about a frame) and text_fx has nothing to switch. Its one motion is the pointer response, hover_frames(): a
small spring lift that the widgets play (never under reduce_motion). transparent_bg drops the card fill, the
highlight and the shadow and keeps the 1 px frame and the content (on the strip, it drops the plate).
"""
from __future__ import annotations

import functools
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from usage_monitor import themes as theme_registry
from usage_monitor.data.models import ApiStatus
from usage_monitor.log import get_logger
from usage_monitor.render import engine
from usage_monitor.render.engine import Canvas, Color, Layer, TRANSPARENT, parse_color, with_alpha
from usage_monitor.render.model import (
    LAMP_LIMITED,
    LAMP_LIVE,
    LAMP_OFFLINE,
    LAMP_PAUSED,
    LAMP_STALE,
    NO_INSETS,
    SEVERITY_CRITICAL,
    SEVERITY_NORMAL,
    SEVERITY_WARNING,
    SURFACE_FLOATING,
    SURFACE_TASKBAR,
    SURFACE_TOOLTIP,
    HoverPlay,
    Insets,
    Row,
    WidgetModel,
    pace_delta,
)
from usage_monitor.render.skins import RenderOptions, coerce_options

log = get_logger(__name__)

# ── Geometry (logical px, card origin top-left) ───────────────────────────────

CARD_W = 288
CARD_H = 128                    # the card with its two limit rows and the footer; each row after them adds ROW_PITCH
MARGIN = 24                     # transparent frame margin per side: holds the shadow
RADIUS = 8.0                    # theme.shape.radius
BORDER = 1.0
LEFT = 15                       # content box: 1 px border + 14 px padding
RIGHT = 273
TRACK_W = RIGHT - LEFT          # 258

HEAD_TOP = 12
HEAD_H = 18
HEAD_MID = HEAD_TOP + HEAD_H / 2
ICON_X = 15
ICON_W = 14
TITLE_X = 36
PILL_RIGHT = 252
PILL_H = 18
PILL_PAD_LEFT = 6
PILL_PAD_RIGHT = 9
PILL_GAP = 6
LAMP_SIZE = 8
LAMP_HALO = 2
MORE_X = (261.5, 266.5, 271.5)  # three 3 px menu dots
MORE_DOT = 1.5                  # their radius
TITLE_GAP = 8                   # least gap between the title and the pill

ROW_TOP = 38
ROW_PITCH = 27
ROW_TOPS = tuple(ROW_TOP + ROW_PITCH * index for index in range(3))     # 38, 65, 92
LINE_H = 16
RAIL_DY = 19
RAIL_H = 2
TICK_DY = 17
TICK_H = 6
BAR_DY = 23
BAR_H = 4
BAR_RADIUS = 2.0
FOOT_TOP = 98                   # the footer's line box on a two-row card ...
FOOT_LINE = 16
FOOT_GAP = FOOT_TOP - (ROW_TOP + 2 * ROW_PITCH)      # ... starts this far under the last row's slot
FOOT_PAD_BOTTOM = CARD_H - FOOT_TOP - FOOT_LINE      # and ends this far above the card's bottom edge

TIP_ROW0 = ROW_TOP
TIP_PITCH = 50
TIP_INFO_DY = 30
TIP_ROW_H = 46                  # a row block: label line, rail, bar and the info line
TIP_FOOT_H = 22
TIP_BOTTOM = 9

STRIP_W = 248
STRIP_H = 40
STRIP_RADIUS = 4.0              # theme.shape.control_radius
STRIP_MIN_PLATE = 30            # least plate height before the strip shrinks to fit
STRIP_PAD_MIN = 6               # least taskbar height around a plate (3 px a side)
STRIP_MIN_HEIGHT = 8            # px: the least height asked for
STRIP_K_X = 10
STRIP_RAIL_X0 = 34
STRIP_RAIL_X1 = 184
STRIP_VALUE_RIGHT = 222
STRIP_LAMP_X = 234              # lamp centre
STRIP_ROW_LINE = 14

# Soft shadow: CSS 0 8px 16px (sigma 8) plus 0 0 2px (sigma 1)
SHADOW_MAIN_DY = 8.0
SHADOW_MAIN_SIGMA = 8.0
SHADOW_TIGHT_SIGMA = 1.0
SHADOW_CUTOFF = 3               # alpha at or under this is dropped

DIM_VALUE = 0.7                 # last-known values: opacity .7
DIM_RAIL = 0.6                  # ... and the time rail: opacity .6

# Fonts: size in logical px, CSS weight
SIZE_TITLE = 12.0
SIZE_LABEL = 12.0
SIZE_TAG = 11.0
SIZE_VALUE = 15.0
SIZE_PILL = 10.0
SIZE_FOOT = 11.0
SIZE_STRIP_K = 11.0
SIZE_STRIP_V = 11.0
SIZE_ICON = 14.0
ICON_GLYPH = "\ue916"           # Segoe Fluent Icons: timer
SHIELD_GLYPH = "\uea18"         # Segoe Fluent Icons: shield, the usage guard's mark
SIZE_GUARD_ICON = 13.0
GUARD_CAPSULE_W = 24            # the guard's mark in the header: the holding capsule (shield in a tinted capsule) ...
GUARD_ARMED_W = 14              # ... and the armed one (the bare shield), left of the status pill
GUARD_PILL_GAP = 6
BADGE_H = 15                    # the "Cap 95%" badge after the name of a row the guard holds on
BADGE_PAD = 6
BADGE_GAP = 8
BAR_PAD = 8                     # the hover card's guard bar: padding, icon and text
BAR_ICON_W = 14
BAR_TEXT_GAP = 8
BAR_GAP = 8

# Text coverage gamma (see _text_gamma)
GAMMA_LIGHT_MIN = 0.43
GAMMA_LIGHT_SPAN = 0.82
GAMMA_DARK_INK = 0.95

# Tray ring: size -> (centre radius, stroke, dash on, dash off); dashes are pathLength units (3.6 deg)
TRAY_RING: dict[int, tuple[float, float, float, float]] = {
    16: (5.25, 2.5, 6.0, 4.5),
    24: (8.0, 3.4, 4.0, 3.0),
    32: (11.5, 4.2, 3.2, 2.4),
}
TRAY_NUMBER_MIN = 28            # the number needs room: 32 px icons only
TRAY_SS = 8
TRAY_MIN = 8
TRAY_NUMBER_SIZE = 10.0
KEYLINE = 0.6                   # px: the outline that keeps the ring readable on a taskbar of the other mode
KEYLINE_ALPHA = 0xC0

_AGE = re.compile(r"^\s*(\d+)\s*([SMHD])\b(?:\s*AGO)?\s*$", re.IGNORECASE)
_AGE_UNITS = {"S": "s", "M": "min", "H": "h", "D": "d"}


# ── Typefaces ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class Typeface:
    """A font role resolved to files: one variable file, or one static file per weight."""
    files: tuple[tuple[int, str], ...]
    variable: bool

    def file_for(self, weight: int) -> str | None:
        if not self.files:
            return None
        return min(self.files, key=lambda item: (abs(item[0] - weight), item[0]))[1]


@dataclass(frozen=True, slots=True)
class Fonts:
    body: Typeface
    mono: Typeface
    icons: str | None


@dataclass(frozen=True, slots=True)
class Face:
    """One font at one size and weight. size is in buffer px; opsz (a variable font's optical size)
    is in logical px, so a face looks the same at every scale."""
    path: str | None
    size: float
    wght: int
    opsz: float | None
    variable: bool


_WEIGHTS = (("regular", 400), ("semibold", 600), ("bold", 700))
_STATIC_BODY = ((400, "segoeui.ttf"), (600, "seguisb.ttf"), (700, "segoeuib.ttf"))
_STATIC_MONO = ((400, "consola.ttf"), (700, "consolab.ttf"))
_FONTS: dict[str, Fonts] = {}
_WARNED: set[str] = set()


def _windows_font(name: str) -> str | None:
    path = Path(os.environ.get("WINDIR") or "C:/Windows") / "Fonts" / name
    return str(path) if path.is_file() else None


@functools.lru_cache(maxsize=32)
def _is_variable(path: str) -> bool:
    try:
        ImageFont.truetype(path, 12).get_variation_axes()
    except (OSError, ValueError):
        return False
    return True


def _theme_typeface(theme: Any, role: str) -> Typeface | None:
    """The theme's own font for a role (Windows fonts are found by themes.font_path), or None."""
    found: dict[int, str] = {}
    for name, weight in _WEIGHTS:
        try:
            path = theme_registry.font_path(theme, role, name)
        except (AttributeError, KeyError, TypeError):   # an object that is not a theme
            path = None
        if path is not None and Path(path).is_file():
            found[weight] = str(path)
    if not found:
        return None
    distinct = set(found.values())
    if len(distinct) == 1 and _is_variable(next(iter(distinct))):
        return Typeface(((400, next(iter(distinct))),), True)
    return Typeface(tuple(sorted(found.items())), False)


def _static_typeface(table: tuple[tuple[int, str], ...]) -> Typeface | None:
    files = tuple((weight, path) for weight, name in table if (path := _windows_font(name)) is not None)
    return Typeface(files, False) if files else None


def _bundled_mono() -> Typeface | None:
    regular = engine.font_file("ibmplexmono/IBMPlexMono-Regular.ttf")
    semibold = engine.font_file("ibmplexmono/IBMPlexMono-SemiBold.ttf")
    files = tuple((weight, str(path)) for weight, path in ((400, regular), (700, semibold)) if path.is_file())
    return Typeface(files, False) if files else None


def _icon_font() -> str | None:
    path = theme_registry.family_font_path("Segoe Fluent Icons")
    if path is not None and Path(path).is_file():
        return str(path)
    return _windows_font("segmdl2.ttf")             # Windows 10: the same codepoints


def _fonts(theme: Any) -> Fonts:
    """Typefaces for a theme, resolved once. A missing Windows font falls back to Segoe UI, Consolas
    or the bundled Plex Mono, and in the end to Pillow's own face, so a frame always renders."""
    key = str(getattr(theme, "id", "gates"))
    if key not in _FONTS:
        empty = Typeface((), False)
        _FONTS[key] = Fonts(
            body=_theme_typeface(theme, "body") or _static_typeface(_STATIC_BODY) or empty,
            mono=_theme_typeface(theme, "mono") or _static_typeface(_STATIC_MONO) or _bundled_mono() or empty,
            icons=_icon_font(),
        )
    return _FONTS[key]


@functools.lru_cache(maxsize=128)
def _font(path: str | None, size: float, wght: int, opsz: float | None, variable: bool):
    """A FreeType face at ``size`` px, its variable axes set. A missing file gives Pillow's default."""
    if path is None:
        return ImageFont.load_default(size=size)
    try:
        font = ImageFont.truetype(path, size)
    except OSError:
        if path not in _WARNED:
            _WARNED.add(path)
            log.warning("font file missing or unreadable: %s (using Pillow's default)", path)
        return ImageFont.load_default(size=size)
    if variable:
        try:
            values = []
            for axis in font.get_variation_axes():
                name = axis["name"].decode("utf-8", "replace").lower() if isinstance(axis["name"], bytes) \
                    else str(axis["name"]).lower()
                if "weight" in name:
                    value = wght
                elif "optical" in name and opsz is not None:
                    value = opsz
                else:
                    value = axis["default"]
                values.append(min(max(value, axis["minimum"]), axis["maximum"]))
            font.set_variation_by_axes(values)
        except (OSError, ValueError) as exc:
            log.debug("cannot set the variation axes of %s: %s", path, exc)
    return font


@functools.lru_cache(maxsize=8192)
def _em_advance(path: str | None, wght: int, opsz: float | None, variable: bool, ch: str) -> float:
    """The advance of one glyph in em, measured on a large face so hinting does not round it."""
    reference = _font(path, 1000.0, wght, opsz, variable)
    return float(reference.getlength(ch)) / 1000.0


@functools.lru_cache(maxsize=128)
def _em_metrics(path: str | None, wght: int, opsz: float | None, variable: bool) -> tuple[float, float]:
    """(ascent, descent) of a face in em, measured on a large face."""
    ascent, descent = _font(path, 1000.0, wght, opsz, variable).getmetrics()
    return ascent / 1000.0, descent / 1000.0


@functools.lru_cache(maxsize=16)
def _coverage_table(gamma: float) -> list[int]:
    return [round(255 * (v / 255) ** gamma) for v in range(256)]


@functools.lru_cache(maxsize=1024)
def _run(face: Face, text: str, gamma: float, frac: float) -> tuple[Image.Image, int, int]:
    """One run of glyphs with its baseline at a fixed row: (mask, pad, ascent).

    The face is at output size, so FreeType hints the glyphs to the pixel grid like the browser the
    design was drawn in. Glyphs sit at their unhinted advances (a run is as wide, in logical px, at
    every scale) and the run starts ``frac`` of a pixel in. Kerning is not applied: Pillow's basic
    layout has no GPOS. The mask is cached and reused at any position.
    """
    font = _font(face.path, face.size, face.wght, face.opsz, face.variable)
    ascent, descent = font.getmetrics()
    width = _text_px(face, text)
    pad = int(face.size) // 2 + 2
    mask = Image.new("L", (math.ceil(width + frac) + 2 * pad, ascent + descent + 2 * pad), 0)
    draw = ImageDraw.Draw(mask)
    pen = pad + frac
    for ch in text:
        if not ch.isspace():
            draw.text((pen, pad + ascent), ch, font=font, fill=255, anchor="ls")
        pen += _em_advance(face.path, face.wght, face.opsz, face.variable, ch) * face.size
    return mask.point(_coverage_table(gamma)), pad, ascent


def _text_px(face: Face, text: str) -> float:
    """Width of ``text`` in output px."""
    return sum(_em_advance(face.path, face.wght, face.opsz, face.variable, ch) for ch in text) * face.size


def _is_light(color: Color) -> bool:
    return (0.299 * color[0] + 0.587 * color[1] + 0.114 * color[2]) > 140


def _text_gamma(color: Color) -> float:
    """Coverage gamma for text of this colour. Browsers draw light text on a dark surface heavier than
    FreeType's linear coverage and dark text a little lighter; the curve is fitted to the approved
    screenshots, where white text wants about 0.45 and the dark text of the light modes about 0.95."""
    luminance = (0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]) / 255
    if luminance <= 0.5:
        return GAMMA_DARK_INK
    return GAMMA_LIGHT_MIN + GAMMA_LIGHT_SPAN * (1 - luminance) ** 1.3


# ── Masks ─────────────────────────────────────────────────────────────────────

def _factor(w: int, h: int) -> int:
    """Supersampling of a shape mask: finer for small shapes, none for a huge one."""
    area = w * h
    return 4 if area <= 400_000 else 2 if area <= 1_800_000 else 1


@functools.lru_cache(maxsize=256)
def _rounded_mask(w: int, h: int, radius: float) -> Image.Image:
    """Coverage of a w x h rectangle with circular corners (radius in px), anti-aliased."""
    r = max(0.0, min(radius, w / 2, h / 2))
    if r < 0.75:
        return Image.new("L", (w, h), 255)
    f = _factor(w, h)
    big = Image.new("L", (w * f, h * f), 0)
    ImageDraw.Draw(big).rounded_rectangle([0, 0, w * f - 1, h * f - 1], radius=r * f, fill=255)
    return big.reduce(f) if f > 1 else big


@functools.lru_cache(maxsize=64)
def _ellipse_mask(w: int, h: int) -> Image.Image:
    f = _factor(w, h)
    big = Image.new("L", (w * f, h * f), 0)
    ImageDraw.Draw(big).ellipse([0, 0, w * f - 1, h * f - 1], fill=255)
    return big.reduce(f) if f > 1 else big


def _inset(mask: Image.Image, size: tuple[int, int], offset: int) -> Image.Image:
    """``mask`` placed ``offset`` px in from the top-left corner of a black mask of ``size``."""
    placed = Image.new("L", size, 0)
    placed.paste(mask, (offset, offset))
    return placed


@functools.lru_cache(maxsize=32)
def _ring_mask(w: int, h: int, radius: float, thickness: int) -> Image.Image:
    """The border of a rounded rectangle: the shape minus the same shape shrunk by ``thickness``."""
    outer = _rounded_mask(w, h, radius)
    iw, ih = w - 2 * thickness, h - 2 * thickness
    if iw <= 0 or ih <= 0:
        return outer
    inner = _inset(_rounded_mask(iw, ih, max(0.0, radius - thickness)), (w, h), thickness)
    return ImageChops.subtract(outer, inner)


@functools.lru_cache(maxsize=64)
def _disc_ring_mask(size: int, thickness: int) -> Image.Image:
    outer = _ellipse_mask(size, size)
    inner = size - 2 * thickness
    if inner <= 0:
        return outer
    return ImageChops.subtract(outer, _inset(_ellipse_mask(inner, inner), (size, size), thickness))


@functools.lru_cache(maxsize=16)
def _ramp_mask(w: int, h: int) -> Image.Image:
    """A triangular alpha ramp, 0 at both ends and 255 in the middle (the card's top highlight)."""
    row = Image.new("L", (w, 1))
    row.putdata([round(255 * (1 - abs(2 * (i + 0.5) / w - 1))) for i in range(w)])
    return row.resize((w, h), Image.Resampling.NEAREST)


def _supersample(scale: float) -> int:
    return 4 if scale < 1.9 else 2


def _fade(color: Color, factor: float) -> Color:
    return with_alpha(color, round(color[3] * factor))


# ── Ink: the canvas the skin draws on ─────────────────────────────────────────

class Ink(Canvas):
    """engine.Canvas plus anti-aliased shapes, variable-font text and a premultiplied downsample."""

    def __init__(self, width: float, height: float, scale: float, fonts: Fonts | None) -> None:
        super().__init__(width, height, scale, ss=_supersample(scale))
        self.fonts = fonts
        self._texts: list[tuple[str, float, float, Face, Color, str]] = []

    # Shapes

    def _snap(self, value: float, k: float) -> int:
        """``value`` (logical px) as a buffer coordinate on a whole device pixel, as a browser snaps a box
        (a half goes down)."""
        return math.ceil(value * k / self.ss - 0.5) * self.ss

    def _px(self, x0: float, y0: float, x1: float, y1: float) -> tuple[int, int, int, int]:
        return (self._snap(x0, self.kx), self._snap(y0, self.ky), self._snap(x1, self.kx), self._snap(y1, self.ky))

    def stroke_px(self, width: float) -> int:
        return max(1, round(width * self.k))

    def rrect(self, x0: float, y0: float, x1: float, y1: float, radius: float, color: Color) -> None:
        """A rounded rectangle; the radius is in logical px and shrinks to fit the shape."""
        if color[3] == 0:
            return
        left, top, right, bottom = self._px(x0, y0, x1, y1)
        if right <= left or bottom <= top:
            return
        mask = _rounded_mask(right - left, bottom - top, radius * self.k)
        self.stamp(Layer(mask, left, top), color)

    def border(self, radius: float, width: float, color: Color) -> None:
        """A border drawn inside the whole canvas, like CSS border-box."""
        if color[3] == 0:
            return
        mask = _ring_mask(self.buf_w, self.buf_h, radius * self.k, self.stroke_px(width))
        self.stamp(Layer(mask, 0, 0), color)

    def rect(self, x0: float, y0: float, x1: float, y1: float, color: Color) -> None:
        if color[3] == 0:
            return
        left, top, right, bottom = self._px(x0, y0, x1, y1)
        if right <= left or bottom <= top:
            return
        self.stamp(Layer(Image.new("L", (right - left, bottom - top), 255), left, top), color)

    def disc(self, cx: float, cy: float, radius: float, color: Color, ring: float = 0.0) -> None:
        """A filled circle, or with ``ring`` a ring of that width drawn inside the circle."""
        if color[3] == 0:
            return
        size = max(self.ss, round(2 * radius * self.k / self.ss) * self.ss)
        left = self._snap(cx - radius, self.kx)
        top = self._snap(cy - radius, self.ky)
        mask = _disc_ring_mask(size, self.stroke_px(ring)) if ring > 0 else _ellipse_mask(size, size)
        self.stamp(Layer(mask, left, top), color)

    def half_disc(self, cx: float, cy: float, radius: float, color: Color) -> None:
        """The left half of a filled circle (the same circle ``disc`` draws), for the lamp of a throttled feed."""
        if color[3] == 0:
            return
        size = max(self.ss, round(2 * radius * self.k / self.ss) * self.ss)
        left = self._snap(cx - radius, self.kx)
        top = self._snap(cy - radius, self.ky)
        mask = _ellipse_mask(size, size).copy()        # the cached mask is shared: cut a copy
        mask.paste(0, (size // 2, 0, size, size))
        self.stamp(Layer(mask, left, top), color)

    def ramp(self, x0: float, y0: float, x1: float, y1: float, color: Color) -> None:
        if color[3] == 0:
            return
        left, top, right, bottom = self._px(x0, y0, x1, y1)
        self.stamp(Layer(_ramp_mask(right - left, bottom - top), left, top), color)

    # Text. Shapes are drawn supersampled; text is queued and drawn on the finished frame at output
    # size, so FreeType can hint it to the pixel grid as a browser does.

    def face(self, typeface: Typeface, size: float, weight: int, opsz: float | None = None) -> Face:
        path = typeface.file_for(weight)
        px = round(size * self.scale, 3)
        if typeface.variable:
            return Face(path, px, weight, size if opsz is None else opsz, True)
        return Face(path, px, 0, None, False)

    def advance(self, text: str, face: Face) -> float:
        """Logical width of ``text``."""
        return _text_px(face, text) / self.scale

    def baseline(self, top: float, line_h: float, face: Face) -> float:
        """Baseline of text in a CSS line box of ``line_h`` whose top is at ``top``, as a browser puts it.

        Chromium rounds the ascent and descent to whole CSS px, floors the half-leading and puts the
        baseline on the device pixel grid (a .5 goes down). Pillow's own metrics round the descent up,
        which would put some lines a pixel off the design."""
        size = face.size / self.scale
        ascent_em, descent_em = _em_metrics(face.path, face.wght, face.opsz, face.variable)
        ascent = math.floor(ascent_em * size + 0.5)
        descent = math.floor(descent_em * size + 0.5)
        half = math.floor((line_h - (ascent + descent)) / 2)
        return math.ceil((top + half + ascent) * self.scale - 0.5) / self.scale

    def put(self, text: str, x: float, baseline: float, face: Face, color: Color, align: str = "left") -> float:
        """Queue a run of text; returns its logical advance. align is "left" or "right" (x is the edge)."""
        if not text:
            return 0.0
        self._texts.append((text, x, baseline, face, color, align))
        return self.advance(text, face)

    def _draw_text(self, out: Image.Image) -> None:
        for text, x, baseline, face, color, align in self._texts:
            start = x * self.scale - (_text_px(face, text) if align == "right" else 0.0)
            whole = math.floor(start)
            frac = round((start - whole) * 4) / 4
            if frac >= 1.0:
                whole, frac = whole + 1, 0.0
            mask, pad, ascent = _run(face, text, _text_gamma(color), frac)
            _blend(out, mask, whole - pad, round(baseline * self.scale) - ascent - pad, color)

    # Output

    def finish(self, method: str = "box") -> Image.Image:
        """The frame at output size, with the queued text drawn on it. The reduction runs on
        premultiplied colour, so a shape's edge against transparent pixels keeps its own colour
        instead of darkening toward black."""
        if self.image.size == (self.out_w, self.out_h):
            out = self.image
        else:
            out = self.image.convert("RGBa").reduce(self.ss).convert("RGBA")
        self._draw_text(out)
        return out


def _blend(image: Image.Image, mask: Image.Image, x: int, y: int, color: Color) -> None:
    """Composite ``color`` through ``mask`` placed at (x, y) on ``image``, clipped to its bounds."""
    if color[3] == 0:
        return
    left, top = max(0, x), max(0, y)
    right, bottom = min(image.width, x + mask.width), min(image.height, y + mask.height)
    if right <= left or bottom <= top:
        return
    if (left, top, right, bottom) != (x, y, x + mask.width, y + mask.height):
        mask = mask.crop((left - x, top - y, right - x, bottom - y))
    if color[3] < 255:
        mask = mask.point(_alpha_table(color[3]))
    layer = Image.new("RGBA", mask.size, (color[0], color[1], color[2], 0))
    layer.putalpha(mask)
    image.alpha_composite(layer, (left, top))


# ── Palette ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class Palette:
    """The tokens one frame needs, resolved from a variant.

    Role colours come from variant.colors; the per-mode roles (card fill, time rail, status bars,
    taskbar and tray colours, shadows) from theme.extras.modes[mode]; the lamp halo and the pill tints
    from the variant's derived extras. A missing token falls back to a value built from the roles that
    are present, so a bare variant still renders.
    """
    mode: str
    card: Color                 # widget_bg
    card_border: Color          # widget_border
    highlight: Color            # hl: the card's 1 px top highlight
    text: Color
    text_bright: Color
    text_dim: Color
    text_3: Color
    accent: Color               # chrome: the title glyph (the live system accent, lifted to read on the card)
    fill: Color                 # normal-severity fills and the paused lamp: accent unless it collides with a severity colour
    ok: Color
    warn: Color
    alert: Color
    usage_warn: Color
    usage_alert: Color
    stale: Color
    track: Color
    time: Color
    time_track: Color
    lamp_halo: Color
    pill_ok: Color
    pill_alert: Color           # the usage guard's holding capsule and cap badge: the alert colour at the pill's alpha
    pill_track: Color
    shadow_main: Color
    shadow_tight: Color
    plate: Color                # the strip's plate: tb_bg under the tb_hover wash
    keyline: Color              # tb_bg, translucent: the tray icon's outline against a taskbar of the other mode
    tray_fg: Color
    tray_track: Color

    @classmethod
    def for_variant(cls, theme: Any, variant: Any) -> Palette:
        colors = _mapping(getattr(variant, "colors", None))
        derived = _mapping(getattr(variant, "extras", None))
        extras = _mapping(getattr(theme, "extras", None))
        mode = getattr(variant, "mode", None)
        if mode not in ("dark", "light"):
            mode = "light" if _is_light(parse_color(str(colors.get("bg", "#202020")))) else "dark"
        modes = _mapping(_mapping(extras.get("modes")).get(mode))

        def pick(table: Mapping[str, Any], key: str, default: Color | None = None) -> Color:
            value = table.get(key)
            if value is None:
                if default is None:
                    raise KeyError(f"variant {getattr(variant, 'id', '?')!r} has no colour {key!r}")
                return default
            return parse_color(value) if isinstance(value, str) else tuple(value)   # type: ignore[return-value]

        dark = mode == "dark"
        accent = pick(colors, "accent")
        text = pick(colors, "text")
        text_dim = pick(colors, "text_dim", text)
        ok = pick(colors, "ok")
        warn = pick(colors, "warn")
        alert = pick(colors, "alert")
        track = pick(colors, "track")
        surface = pick(colors, "surface", pick(colors, "bg"))
        pill_alpha = _mapping(extras.get("pill")).get("dark_alpha" if dark else "light_alpha", 0.16 if dark else 0.12)
        hover = pick(modes, "tb_hover", with_alpha(text, 0x12 if dark else 0x0D))
        tb_bg = pick(modes, "tb_bg", pick(colors, "bg"))
        return cls(
            mode=mode,
            card=pick(modes, "widget_bg", with_alpha(surface, 0xF2)),
            card_border=pick(modes, "widget_border", pick(colors, "border")),
            highlight=pick(modes, "hl", with_alpha((255, 255, 255, 255), 0x29 if dark else 0)),
            text=text,
            text_bright=pick(colors, "text_bright", text),
            text_dim=text_dim,
            text_3=pick(modes, "text_3", text_dim),
            accent=accent,
            fill=pick(derived, "accent_fill", accent),
            ok=ok,
            warn=warn,
            alert=alert,
            usage_warn=pick(modes, "usage_warn", warn),
            usage_alert=pick(modes, "usage_alert", alert),
            stale=pick(colors, "stale"),
            track=track,
            time=pick(modes, "time", with_alpha(text, 0x8F if dark else 0x80)),
            time_track=pick(modes, "time_track", with_alpha(text, 0x14 if dark else 0x12)),
            lamp_halo=pick(derived, "lamp_halo", with_alpha(ok, 0x3D)),
            pill_ok=pick(derived, "pill_ok_bg", with_alpha(ok, round(255 * float(pill_alpha)))),
            pill_alert=pick(derived, "pill_alert_bg", with_alpha(alert, round(255 * float(pill_alpha)))),
            pill_track=pick(derived, "pill_track_bg", track),
            shadow_main=pick(modes, "shadow_main", (0, 0, 0, 0x57 if dark else 0x24)),
            shadow_tight=pick(modes, "shadow_tight", (0, 0, 0, 0x66 if dark else 0x1F)),
            plate=_over(tb_bg, hover),
            keyline=with_alpha(tb_bg, KEYLINE_ALPHA),
            tray_fg=pick(modes, "tray_fg", text),
            tray_track=pick(modes, "tray_track", with_alpha(text, 0x6B if dark else 0x5C)),
        )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _over(base: Color, wash: Color) -> Color:
    """``wash`` composited over the opaque ``base``: an opaque colour."""
    a = wash[3] / 255
    return (round(base[0] + (wash[0] - base[0]) * a), round(base[1] + (wash[1] - base[1]) * a),
            round(base[2] + (wash[2] - base[2]) * a), 255)


# ── Reading the model ─────────────────────────────────────────────────────────

# The lamp of each status: a filled grey disc when the sign-in is lost or missing (last known), a hollow ring when
# there is no connection or nothing is loaded yet, a half-filled disc while the API throttles the monitor, a ring in
# the accent while paused, and the lit green disc with its halo when live. Every kind has a shape of its own, so the
# strip (which has no word) tells them apart without colour.
_LAMP_KIND = {
    ApiStatus.LIVE: LAMP_LIVE,
    ApiStatus.STALE: LAMP_STALE,
    ApiStatus.OFFLINE: LAMP_OFFLINE,
    ApiStatus.PAUSED: LAMP_PAUSED,
    ApiStatus.RATE_LIMITED: LAMP_LIMITED,
    ApiStatus.NO_CREDENTIALS: LAMP_STALE,
    ApiStatus.LOADING: LAMP_OFFLINE,
}

# status -> (lead, rest, lead colour role). The three the mockup shows are STALE, OFFLINE and PAUSED.
_STATUS_COPY: dict[ApiStatus, tuple[str, str, str]] = {
    ApiStatus.STALE: ("Hmm, we lost touch.", " Sign in to Claude Code.", "warn"),
    ApiStatus.OFFLINE: ("", "No connection. Retrying every minute.", "text_3"),
    ApiStatus.PAUSED: ("", "Paused. Resume to keep usage live.", "text_3"),
    ApiStatus.RATE_LIMITED: ("Rate limited.", " Showing the last reading.", "warn"),
    ApiStatus.NO_CREDENTIALS: ("No sign-in found.", " Open the dashboard for help.", "warn"),
    ApiStatus.LOADING: ("", "Fetching your usage.", "text_3"),
}


def _dim(model: WidgetModel) -> bool:
    """Everything but LIVE is drawn dimmed: grey bars and values at 70 % (PAUSED included)."""
    return model.status is not ApiStatus.LIVE


def _known(percent: float | None) -> bool:
    return percent is not None and math.isfinite(percent)


def _percent_text(percent: float | None) -> str:
    return f"{int(percent + 0.5) if percent >= 0 else 0}%" if _known(percent) else "--"


def _exact_percent_text(percent: float | None) -> str:
    if not _known(percent):
        return "--"
    text = f"{percent:.1f}"
    return f"{text[:-2] if text.endswith('.0') else text}%"


def _age_label(model: WidgetModel) -> str | None:
    """The model's "12 S AGO" as the pill shows it: "12 s", "41 min", "3 h", "2 d"."""
    text = model.age_text
    if not text:
        return None
    match = _AGE.match(text)
    if match:
        return f"{match.group(1)} {_AGE_UNITS[match.group(2).upper()]}"
    return text.lower().removesuffix(" ago").strip() or None


def _sentence(label: str) -> str:
    """"FABLE WEEKLY" as "Fable weekly"; a label that already has lower case is kept."""
    return label.capitalize() if label.isupper() else label


def _tag(row: Row) -> str:
    if row.key == "session":
        return "5H"
    if row.key == "weekly":
        return "7D"
    return row.share_text.lower() if row.share_text else "7D"


def _blank_row(key: str, label: str) -> Row:
    return Row(key, label, None, None, SEVERITY_NORMAL, None, None, None, None)


def _rows(model: WidgetModel) -> list[Row]:
    """The model's rows, with at least the session and the weekly row (a model with fewer is padded)."""
    rows = list(model.rows)
    for key, label in (("session", "SESSION"), ("weekly", "WEEKLY"))[len(rows):]:
        rows.append(_blank_row(key, label))
    return rows


def _rows_with_data(model: WidgetModel) -> list[Row]:
    return [row for row in model.rows if row.percent is not None]


def _scoped_rows(model: WidgetModel) -> list[Row]:
    """The rows after the session and the weekly one: every scoped limit (Fable) the model carries, in every state.
    The card grows by one row for each, and the footer always comes last."""
    return [row for row in model.rows[2:] if row.percent is not None]


def _row_top(index: int) -> int:
    """Top of the card's row ``index`` (0 is the session row)."""
    return ROW_TOP + ROW_PITCH * index


def _card_height(rows: int) -> int:
    """The card's height for ``rows`` limit rows: 128 for two, and one more row's pitch for each after them. The
    footer sits under the last row, FOOT_GAP below where the next row would start."""
    return _row_top(rows) + FOOT_GAP + FOOT_LINE + FOOT_PAD_BOTTOM


_RANK = {SEVERITY_NORMAL: 0, SEVERITY_WARNING: 1, SEVERITY_CRITICAL: 2}


def _driver(model: WidgetModel) -> Row | None:
    """The row the footer talks about: the most severe one, the session on a tie."""
    rows = _rows_with_data(model)
    if not rows:
        return None
    return max(rows, key=lambda row: (_RANK.get(row.severity, 0), -rows.index(row)))


def _hold_runs(model: WidgetModel) -> list[tuple[str, int, str]]:
    """The footer while the usage guard holds: "Guard holding · Resumes 6:26 PM (in 2h 14m)", the lead in the alert
    colour. Empty while the guard is off or only armed."""
    guard = model.guard
    if guard is None or not guard.holding:
        return []
    runs = [("Guard holding", 600, "alert")]
    if guard.resume_clock:
        countdown = (guard.resume_in or "").lower()
        runs.append((f" · Resumes {guard.resume_clock} (in {countdown})", 400, "text_3"))
    return runs


def _footer_runs(model: WidgetModel) -> list[tuple[str, int, str]]:
    """The footer as (text, weight, colour role) runs. LIVE reads the pace of the driving row, or the guard while it
    holds: the pace of a limit that is at its cap is not news. A state that needs the user (the sign-in, the
    connection) keeps its own words."""
    if model.status is not ApiStatus.LIVE:
        lead, rest, role = _STATUS_COPY[model.status]
        return ([(lead, 600, role)] if lead else []) + [(rest, 400, "text_3")]
    holding = _hold_runs(model)
    if holding:
        return holding
    row = _driver(model)
    if row is None:
        return [("Waiting for your first reading.", 400, "text_3")]
    if row.severity == SEVERITY_CRITICAL:
        lead, role = "Nearly out", "alert"
    elif row.pace == "ahead":
        lead, role = "Ahead of pace", "warn"
    elif row.pace == "behind":
        lead, role = "Under pace", "ok"
    elif row.pace == "on_pace":
        lead, role = "On pace", "ok"
    else:
        lead, role = "", "text_3"
    reset = f"Resets {row.resets_text}" if row.resets_text else ""
    runs: list[tuple[str, int, str]] = []
    if lead:
        runs.append((lead, 600, role))
    if reset:
        runs.append((f" · {reset}" if lead else reset, 400, "text_3"))
    return runs or [("No reset time yet.", 400, "text_3")]


def _pace_text(row: Row) -> tuple[str, str]:
    """(text, colour role) of the tooltip's pace readout."""
    delta = pace_delta(row.percent, row.time_fraction)
    if delta is None or row.pace is None:
        return "", "text_3"
    points = round(abs(delta))
    if row.pace == "ahead":
        return f"Ahead of pace by {points} pts", "warn"
    if row.pace == "behind":
        return f"Under pace by {points} pts", "ok"
    return "On pace", "ok"


# ── Shared drawing pieces ─────────────────────────────────────────────────────

def _role(pal: Palette, name: str) -> Color:
    return getattr(pal, name)


def _over_cap(model: WidgetModel, row: Row) -> bool:
    """Whether the usage guard (#27) holds because of this row, and the card is live to say so (a dimmed card is
    drawn flat): the row is at or past the cap the guard watches for it."""
    guard = model.guard
    return (guard is not None and guard.holding and not _dim(model) and row.key in guard.over_keys
            and row.percent is not None)


def _usage_color(pal: Palette, model: WidgetModel, row: Row) -> Color:
    if _dim(model):
        return pal.stale
    if row.severity == SEVERITY_CRITICAL or _over_cap(model, row):
        return pal.usage_alert
    if row.severity == SEVERITY_WARNING:
        return pal.usage_warn
    return pal.fill


def _value_color(pal: Palette, model: WidgetModel, row: Row) -> Color:
    if _dim(model):
        return _fade(pal.text, DIM_VALUE)
    if row.severity == SEVERITY_CRITICAL or _over_cap(model, row):
        return pal.alert
    if row.severity == SEVERITY_WARNING:
        return pal.warn
    return pal.text


def _number(value: float) -> str:
    """A cap as people write it: 95, not 95.0."""
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _holding(model: WidgetModel) -> bool:
    return model.guard is not None and model.guard.holding


def _lamp(ink: Ink, pal: Palette, kind: str, cx: float, cy: float) -> None:
    """The status lamp: filled with a halo when live, filled grey when stale, half filled when limited, a ring in the
    accent when paused and a grey ring otherwise (offline, loading)."""
    radius = LAMP_SIZE / 2
    if kind == LAMP_LIVE:
        ink.disc(cx, cy, radius + LAMP_HALO, pal.lamp_halo)
        ink.disc(cx, cy, radius, pal.ok)
    elif kind == LAMP_STALE:
        ink.disc(cx, cy, radius, pal.stale)
    elif kind == LAMP_LIMITED:
        ink.disc(cx, cy, radius, pal.stale, ring=1.5)
        ink.half_disc(cx, cy, radius, pal.stale)
    elif kind == LAMP_PAUSED:
        ink.disc(cx, cy, radius, pal.fill, ring=1.5)
    else:
        ink.disc(cx, cy, radius, pal.stale, ring=1.5)


def _timer_glyph(ink: Ink, color: Color, cx: float, cy: float) -> None:
    """A drawn stopwatch for machines without the Fluent icon font."""
    ink.disc(cx, cy + 1.2, 5.6, color, ring=1.3)
    ink.rect(cx - 2.0, cy - 6.2, cx + 2.0, cy - 4.9, color)
    ink.rect(cx - 0.65, cy - 2.2, cx + 0.65, cy + 1.6, color)


def _shield_shape(ink: Ink, cx: float, cy: float, height: float, color: Color) -> None:
    """A filled shield ``height`` px tall (and 0.86 of that wide) centred on (cx, cy). The strip draws it, where the
    icon font's thin outline does not read at 7 to 11 px, and the card uses it on a machine without the font."""
    width = height * 0.86
    left, top = cx - width / 2, cy - height / 2
    shoulder, waist = top + 0.16 * height, top + 0.55 * height
    ink.stamp(ink.polygon_layer([(left, shoulder), (cx, top), (left + width, shoulder), (left + width, waist),
                                 (cx, top + height), (left, waist)]), color)


def _guard_mark_width(model: WidgetModel) -> float:
    """The room the usage guard's mark takes in the header: 0 while the guard is off, the bare shield while it is
    armed and the shield in its capsule while it holds."""
    if model.guard is None:
        return 0.0
    return GUARD_CAPSULE_W if model.guard.holding else GUARD_ARMED_W


def _guard_mark(ink: Ink, pal: Palette, model: WidgetModel, left: float) -> None:
    """The guard's mark in the header, its left edge at ``left``: while it holds, the shield in the alert colour on a
    capsule tinted with it (the pill's own look); while it is armed, the bare shield in the dim tone."""
    holding = _holding(model)
    width = GUARD_CAPSULE_W if holding else GUARD_ARMED_W
    if holding:
        ink.rrect(left, HEAD_TOP, left + width, HEAD_TOP + PILL_H, PILL_H / 2, pal.pill_alert)
    color = pal.alert if holding else pal.text_3
    if ink.fonts.icons is not None:
        icon = ink.face(Typeface(((400, ink.fonts.icons),), False), SIZE_GUARD_ICON, 400)
        glyph_w = ink.advance(SHIELD_GLYPH, icon)
        ink.put(SHIELD_GLYPH, left + width / 2 - glyph_w / 2, ink.baseline(HEAD_TOP, HEAD_H, icon), icon, color)
    else:
        _shield_shape(ink, left + width / 2, HEAD_MID, SIZE_GUARD_ICON - 1, color)


def _strip_shield(ink: Ink, pal: Palette, plate_h: float) -> None:
    """The guard holds: a small filled shield in the alert colour above the lamp, in the strip's right-hand column.
    The strip has no room for the countdown or for an armed mark, so the hover card says both."""
    lamp_top = plate_h / 2 - (LAMP_SIZE / 2 + LAMP_HALO)
    height = max(7.0, min(11.0, lamp_top - 4))
    _shield_shape(ink, STRIP_LAMP_X, lamp_top / 2, height, pal.alert)


@functools.lru_cache(maxsize=12)
def _card_base(card_h: float, scale: float, card: Color, border: Color, highlight: Color,
               transparent: bool) -> Image.Image:
    """The card's own surface at buffer size: fill, 1 px border and top highlight (the fill and the
    highlight go with transparent_bg). It is the same for every frame, so it is drawn once."""
    ink = Ink(CARD_W, card_h, scale, None)
    if not transparent:
        mask = _rounded_mask(ink.buf_w, ink.buf_h, RADIUS * ink.k)
        ink.stamp(Layer(mask, 0, 0), card)
    ink.border(RADIUS, BORDER, border)
    if not transparent:
        ink.ramp(1 + 0.06 * (CARD_W - 2), 1, 1 + 0.94 * (CARD_W - 2), 2, highlight)
    return ink.image


def _paint_card(ink: Ink, pal: Palette, transparent: bool) -> None:
    """Start the canvas from the card surface."""
    ink.image = _card_base(ink.height, ink.scale, pal.card, pal.card_border, pal.highlight, transparent).copy()


def _pill_colors(pal: Palette, model: WidgetModel) -> tuple[Color, Color]:
    """(text, background) of the status pill."""
    if model.status is ApiStatus.LIVE:
        return pal.ok, pal.pill_ok
    return (pal.text_dim if model.status is ApiStatus.PAUSED else pal.text_3), pal.pill_track


def _header(ink: Ink, pal: Palette, model: WidgetModel, *, more: bool) -> None:
    """Fluent glyph and title on the left; the status pill (lamp, word, age) on the right."""
    fonts = ink.fonts
    if fonts.icons is not None:
        icon = ink.face(Typeface(((400, fonts.icons),), False), SIZE_ICON, 400)
        width = ink.advance(ICON_GLYPH, icon)
        ink.put(ICON_GLYPH, ICON_X + ICON_W / 2 - width / 2, ink.baseline(HEAD_TOP, HEAD_H, icon), icon, pal.accent)
    else:
        _timer_glyph(ink, pal.accent, ICON_X + ICON_W / 2, HEAD_MID)
    title = ink.face(fonts.body, SIZE_TITLE, 600)
    title_w = ink.put("Claude usage", TITLE_X, ink.baseline(HEAD_TOP, HEAD_H, title), title, pal.text_bright)

    pill = ink.face(fonts.body, SIZE_PILL, 600)
    right = PILL_RIGHT if more else RIGHT
    age = _age_label(model)
    text = f"{model.status_text} · {age}" if age else model.status_text
    chrome = PILL_PAD_LEFT + LAMP_SIZE + PILL_GAP + PILL_PAD_RIGHT
    guard_room = _guard_mark_width(model)
    guard_room = guard_room + GUARD_PILL_GAP if guard_room else 0.0     # the usage guard's mark sits left of the pill
    if right - (chrome + ink.advance(text, pill)) < TITLE_X + title_w + TITLE_GAP + guard_room:
        text = model.status_text                     # a long word leaves the age out before it collides
    width = chrome + ink.advance(text, pill)
    left = right - width
    ink_color, tint = _pill_colors(pal, model)
    ink.rrect(left, HEAD_TOP, right, HEAD_TOP + PILL_H, PILL_H / 2, tint)
    _lamp(ink, pal, _LAMP_KIND[model.status], left + PILL_PAD_LEFT + LAMP_SIZE / 2, HEAD_MID)
    ink.put(text, left + PILL_PAD_LEFT + LAMP_SIZE + PILL_GAP, ink.baseline(HEAD_TOP, HEAD_H, pill), pill, ink_color)
    if guard_room:
        _guard_mark(ink, pal, model, left - guard_room)
    if more:
        for x in MORE_X:
            ink.disc(x, HEAD_MID, MORE_DOT, pal.text_dim)


def _fit(ink: Ink, text: str, face: Face, limit: float) -> str:
    """``text`` cut with an ellipsis until it fits ``limit`` logical px."""
    if ink.advance(text, face) <= limit:
        return text
    while len(text) > 1 and ink.advance(text + "…", face) > limit:
        text = text[:-1]
    return text.rstrip() + "…"


def _cap_badge(ink: Ink, pal: Palette, model: WidgetModel, row: Row, name_end: float, limit: float, top: float) -> None:
    """"Cap 95%" in a capsule tinted with the alert colour, after the name of a row the usage guard holds on. It is
    drawn only where it fits between the name and the value (``limit``): a long scoped name keeps its room, and the
    value in the alert colour and the footer carry the state."""
    guard = model.guard
    cap = guard.caps.get(row.key) if guard is not None and _over_cap(model, row) else None
    if cap is None:
        return
    face = ink.face(ink.fonts.body, SIZE_PILL, 600)
    text = f"Cap {_number(cap)}%"
    left = name_end + BADGE_GAP
    width = BADGE_PAD + ink.advance(text, face) + BADGE_PAD
    if left + width > limit:
        return
    top += (LINE_H - BADGE_H) / 2
    ink.rrect(left, top, left + width, top + BADGE_H, BADGE_H / 2, pal.pill_alert)
    ink.put(text, left + BADGE_PAD, ink.baseline(top, BADGE_H, face), face, pal.alert)


def _row(ink: Ink, pal: Palette, model: WidgetModel, row: Row, top: float, *, exact: bool = False) -> None:
    """One limit: label, tag and value on the first line, then the time rail over the usage bar."""
    fonts = ink.fonts
    dim = _dim(model)
    label_face = ink.face(fonts.body, SIZE_LABEL, 400)
    tag_face = ink.face(fonts.mono, SIZE_TAG, 400)
    value_face = ink.face(fonts.mono, SIZE_VALUE, 700)

    value = _exact_percent_text(row.percent) if exact else _percent_text(row.percent)
    value_w = ink.advance(value, value_face)
    room = RIGHT - LEFT - value_w - 10
    label = _sentence(row.label)
    tag = _tag(row)
    label_w = ink.advance(label, label_face)
    tag_w = ink.advance(tag, tag_face)
    if label_w + 6 + tag_w > room:
        tag = ""                                    # the tag goes first when the line is tight
    if label_w > room:
        label = _fit(ink, label, label_face, room)
    label_w = ink.put(label, LEFT, ink.baseline(top, LINE_H, label_face), label_face,
                      pal.text_3 if dim else pal.text_dim)
    name_end = LEFT + label_w
    if tag:
        ink.put(tag, LEFT + label_w + 6, ink.baseline(top, LINE_H, tag_face), tag_face, pal.text_3)
        name_end += 6 + tag_w
    ink.put(value, RIGHT, ink.baseline(top, LINE_H, value_face), value_face, _value_color(pal, model, row), "right")
    _cap_badge(ink, pal, model, row, name_end, RIGHT - value_w - 10, top)

    # time rail: the share of the window already gone, with an index tick at its end
    rail_top = top + RAIL_DY
    ink.rrect(LEFT, rail_top, RIGHT, rail_top + RAIL_H, RAIL_H / 2, pal.time_track)
    if row.time_fraction is not None:
        end = LEFT + TRACK_W * max(0.0, min(1.0, row.time_fraction))
        fill = _fade(pal.time, DIM_RAIL) if dim else pal.time
        if end - LEFT >= 0.5:
            ink.rrect(LEFT, rail_top, end, rail_top + RAIL_H, RAIL_H / 2, fill)
        tick = max(LEFT, min(RIGHT - 1, end - 1))
        ink.rect(tick, top + TICK_DY, tick + 1, top + TICK_DY + TICK_H, fill)

    # usage bar
    bar_top = top + BAR_DY
    ink.rrect(LEFT, bar_top, RIGHT, bar_top + BAR_H, BAR_RADIUS, pal.track)
    if row.percent is not None and row.percent > 0:
        fill_w = TRACK_W * min(1.0, row.percent / 100)
        ink.rrect(LEFT, bar_top, LEFT + fill_w, bar_top + BAR_H, BAR_RADIUS, _usage_color(pal, model, row))


def _footer(ink: Ink, pal: Palette, model: WidgetModel, top: float) -> None:
    """The state line: a semibold lead in the status colour, then regular text."""
    fonts = ink.fonts
    x = float(LEFT)
    runs = _footer_runs(model)
    faces = {400: ink.face(fonts.body, SIZE_FOOT, 400), 600: ink.face(fonts.body, SIZE_FOOT, 600)}
    baseline = ink.baseline(top, FOOT_LINE, faces[400])
    limit = RIGHT - LEFT
    for text, weight, role in runs:
        face = faces[weight]
        text = _fit(ink, text, face, limit - (x - LEFT))
        x += ink.put(text, x, baseline, face, _role(pal, role))


# ── Tooltip: the usage guard's bar ────────────────────────────────────────────

def _guard_statement(model: WidgetModel) -> tuple[str, str] | None:
    """(lead, rest) of the hover card's guard line, or None while the guard is off:
    ("Guard armed:", "session 95%, weekly 90%") and
    ("Guard holding:", "weekly 91% (cap 90%). Resumes Fri 7:12 PM (in 2d 3h).")."""
    guard = model.guard
    if guard is None:
        return None
    if not guard.holding:
        caps = ", ".join(engine.glue(f"{label} {_number(cap)}%") for label, cap in guard.watched())
        return "Guard armed:", caps
    limits = []
    for label, reading, cap in guard.over_limits():
        text = engine.glue(label if reading is None else f"{label} {_percent_text(reading)}")
        if cap is not None:
            text += " " + engine.glue(f"(cap {_number(cap)}%)")
        limits.append(text)
    rest = ", ".join(limits)
    rest = f"{rest}." if rest else ""
    if guard.resume_clock:
        clock = engine.glue(guard.resume_clock)
        countdown = engine.glue("(in " + (guard.resume_in or "").lower() + ")")
        rest = f"{rest} Resumes {clock} {countdown}."
    return "Guard holding:", rest.strip()


def _guard_bar_lines(ink: Ink, model: WidgetModel) -> tuple[str, list[str]]:
    """(the lead, the rest of the guard's line broken to the bar's text width), measured on ``ink`` (any canvas of the
    frame's scale). The first line leaves room for the lead. No lines while the guard is off; a guard with nothing to
    list (no caps) is its lead alone, without the colon."""
    statement = _guard_statement(model)
    if statement is None:
        return "", []
    lead, rest = statement
    if not rest:
        return lead.removesuffix(":"), []
    lead_w = ink.advance(lead, ink.face(ink.fonts.body, SIZE_FOOT, 600))
    body = ink.face(ink.fonts.body, SIZE_FOOT, 400)
    room = RIGHT - LEFT - 2 * BAR_PAD - BAR_ICON_W - BAR_TEXT_GAP
    return lead, engine.wrap_words(rest, lambda line: ink.advance(line, body), room, room - lead_w - 4)


def _guard_bar(ink: Ink, pal: Palette, model: WidgetModel, lead: str, lines: list[str], top: float,
               height: float) -> None:
    """An InfoBar under the rows: a rounded bar tinted with the alert colour while the guard holds (a neutral tint
    while it is armed), the shield at its left, the lead in semibold and the rest of the sentence after it,
    wrapping under it."""
    holding = _holding(model)
    ink.rrect(LEFT, top, RIGHT, top + height, STRIP_RADIUS, pal.pill_alert if holding else pal.time_track)
    color = pal.alert if holding else pal.text_3
    if ink.fonts.icons is not None:
        icon = ink.face(Typeface(((400, ink.fonts.icons),), False), SIZE_ICON, 400)
        glyph_w = ink.advance(SHIELD_GLYPH, icon)
        ink.put(SHIELD_GLYPH, LEFT + BAR_PAD + BAR_ICON_W / 2 - glyph_w / 2,
                ink.baseline(top + BAR_PAD, FOOT_LINE, icon), icon, color)
    else:
        _shield_shape(ink, LEFT + BAR_PAD + BAR_ICON_W / 2, top + BAR_PAD + FOOT_LINE / 2, SIZE_ICON - 2, color)
    x = LEFT + BAR_PAD + BAR_ICON_W + BAR_TEXT_GAP
    lead_face = ink.face(ink.fonts.body, SIZE_FOOT, 600)
    body = ink.face(ink.fonts.body, SIZE_FOOT, 400)
    lead_w = ink.put(lead, x, ink.baseline(top + BAR_PAD, FOOT_LINE, lead_face), lead_face,
                     pal.alert if holding else pal.text)
    for index, line in enumerate(lines):
        line_top = top + BAR_PAD + FOOT_LINE * index
        ink.put(line, x + lead_w + 4 if index == 0 else x, ink.baseline(line_top, FOOT_LINE, body), body, pal.text_dim)


# ── Frames ────────────────────────────────────────────────────────────────────

def _frame_size(card_h: float, scale: float) -> tuple[int, int]:
    margin = round(MARGIN * scale)
    return (max(1, round(CARD_W * scale)) + 2 * margin, max(1, round(card_h * scale)) + 2 * margin)


@functools.lru_cache(maxsize=16)
def _shadow(card_w: int, card_h: int, margin: int, scale: float, main: Color, tight: Color) -> Image.Image:
    """The card's two soft shadows on a transparent frame, at output size. Cached: the same for every
    frame of a size. The blur runs at output resolution, which is smooth enough for a sigma of 8."""
    size = (card_w + 2 * margin, card_h + 2 * margin)
    shape = _rounded_mask(card_w, card_h, RADIUS * scale)
    frame = Image.new("RGBA", size, TRANSPARENT)
    outside = ImageChops.invert(_inset(shape, size, margin))     # a box-shadow is not drawn under its box
    for dy, sigma, color in ((SHADOW_MAIN_DY, SHADOW_MAIN_SIGMA, main), (0.0, SHADOW_TIGHT_SIGMA, tight)):
        if color[3] == 0:
            continue
        mask = Image.new("L", size, 0)
        mask.paste(shape, (margin, margin + round(dy * scale)))
        mask = ImageChops.multiply(mask.filter(ImageFilter.GaussianBlur(sigma * scale)), outside)
        mask = mask.point(_shadow_table(color[3]))
        layer = Image.new("RGBA", size, (color[0], color[1], color[2], 0))
        layer.putalpha(mask)
        frame = Image.alpha_composite(frame, layer)
    return frame


@functools.lru_cache(maxsize=256)
def _alpha_table(alpha: int) -> list[int]:
    return [(value * alpha + 127) // 255 for value in range(256)]


@functools.lru_cache(maxsize=16)
def _shadow_table(alpha: int) -> list[int]:
    """Coverage scaled to the shadow's alpha, with the faintest tail cut off: under about 1.5 % black is
    invisible, and the window only reacts to the mouse where it is not fully transparent."""
    return [0 if (value * alpha + 127) // 255 <= SHADOW_CUTOFF else (value * alpha + 127) // 255
            for value in range(256)]


def _compose(card: Image.Image, scale: float, pal: Palette, transparent: bool) -> Image.Image:
    """The card inside its frame: the shadow underneath, unless the widget is transparent."""
    margin = round(MARGIN * scale)
    if transparent:
        frame = Image.new("RGBA", (card.width + 2 * margin, card.height + 2 * margin), TRANSPARENT)
    else:
        frame = _shadow(card.width, card.height, margin, scale, pal.shadow_main, pal.shadow_tight).copy()
    frame.alpha_composite(card, (margin, margin))
    return frame


def _card_canvas(card_h: float, scale: float, theme: Any) -> Ink:
    return Ink(CARD_W, card_h, scale, _fonts(theme))


# ── Skin ──────────────────────────────────────────────────────────────────────

class GatesSkin:
    """The Gates renderer for all four surfaces."""

    theme_id = "gates"

    def floating_size(self, theme: Any, model: WidgetModel, scale: float) -> tuple[int, int]:
        return _frame_size(_card_height(2 + len(_scoped_rows(model))), scale)

    def render_floating(self, model: WidgetModel, theme: Any, variant: Any, scale: float = 1.0,
                        opts: RenderOptions | Mapping[str, Any] | None = None) -> Image.Image:
        o = coerce_options(opts)
        pal = Palette.for_variant(theme, variant)
        limits = [*_rows(model)[:2], *_scoped_rows(model)]
        ink = _card_canvas(_card_height(len(limits)), scale, theme)
        _paint_card(ink, pal, o.transparent_bg)
        _header(ink, pal, model, more=True)
        for index, row in enumerate(limits):
            _row(ink, pal, model, row, _row_top(index))
        _footer(ink, pal, model, _row_top(len(limits)) + FOOT_GAP)
        return _compose(ink.finish(), scale, pal, o.transparent_bg)

    def render_tooltip(self, model: WidgetModel, theme: Any, variant: Any, scale: float = 1.0) -> Image.Image:
        pal = Palette.for_variant(theme, variant)
        rows = _rows(model)
        footer = model.status is not ApiStatus.LIVE
        rows_end = TIP_ROW0 + TIP_PITCH * len(rows) - (TIP_PITCH - TIP_ROW_H)
        lead_w, bar_lines = _guard_bar_lines(_card_canvas(1, scale, theme), model)      # measured at this scale
        bar_h = 2 * BAR_PAD + FOOT_LINE * max(1, len(bar_lines)) if model.guard is not None else 0
        bar_room = BAR_GAP + bar_h if bar_h else 0                  # the guard's bar and the gap above it
        height = rows_end + TIP_BOTTOM + (TIP_FOOT_H if footer else 0) + bar_room
        ink = _card_canvas(height, scale, theme)
        _paint_card(ink, pal, False)
        _header(ink, pal, model, more=False)
        body = ink.face(ink.fonts.body, SIZE_FOOT, 400)
        lead = ink.face(ink.fonts.body, SIZE_FOOT, 600)
        for index, row in enumerate(rows):
            top = TIP_ROW0 + TIP_PITCH * index
            _row(ink, pal, model, row, top, exact=True)
            base = ink.baseline(top + TIP_INFO_DY, FOOT_LINE, body)
            if row.resets_text:
                ink.put(f"Resets {row.resets_text}", LEFT, base, body, pal.text_3)
            text, role = _pace_text(row)
            if text and not _dim(model):
                ink.put(text, RIGHT, base, lead, _role(pal, role), "right")
        if bar_h:
            _guard_bar(ink, pal, model, lead_w, bar_lines, rows_end + BAR_GAP, bar_h)
        if footer:
            _footer(ink, pal, model, rows_end + 6 + bar_room)
        return _compose(ink.finish(), scale, pal, False)

    def render_taskbar(self, model: WidgetModel, theme: Any, variant: Any, height_px: int, scale: float = 1.0,
                       opts: RenderOptions | Mapping[str, Any] | None = None) -> Image.Image:
        o = coerce_options(opts)
        pal = Palette.for_variant(theme, variant)
        return _render_strip(model, pal, theme, max(STRIP_MIN_HEIGHT, int(height_px)), float(scale), o)

    def render_tray_icon(self, model: WidgetModel, theme: Any, variant: Any, size_px: int) -> Image.Image:
        size = int(size_px)
        if size < TRAY_MIN:
            raise ValueError(f"tray icon size must be at least {TRAY_MIN} px, not {size}")
        pal = Palette.for_variant(theme, variant)
        return _render_tray(model, pal, theme, size)

    def content_insets(self, surface: str, theme: Any, model: WidgetModel, scale: float) -> Insets:
        """The transparent margin round the visible surface. The floating widget and the tooltip are a card inside a
        frame MARGIN px bigger on every side (the room of its soft shadow). The strip is the taskbar band itself, and
        the tooltip stands off the band, not off the plate: no margin to subtract."""
        if surface in (SURFACE_FLOATING, SURFACE_TOOLTIP):
            margin = round(MARGIN * scale)
            return (margin, margin, margin, margin)
        return NO_INSETS

    def hover_frames(self, frame: Image.Image, surface: str, scale: float,
                     opts: RenderOptions | Mapping[str, Any] | None = None) -> HoverPlay | None:
        """The pointer response of the floating widget and the strip: the card lifts on a gentle spring (1, 3, 2 and 2
        px, 40 ms apart) over a soft shadow that stays on the ground, holds while the pointer is over it, and settles
        back (1 px, then 0) on the way out."""
        if surface not in (SURFACE_FLOATING, SURFACE_TASKBAR):
            return None
        return _lift(frame, scale, shadow=not coerce_options(opts).transparent_bg)


# ── Hover ─────────────────────────────────────────────────────────────────────

HOVER_LIFTS = (1, 3, 2, 2)      # logical px the card rises on each frame of the spring
HOVER_STEP_MS = 40
HOVER_LEAVE_LIFT = 1            # on the way out the card is 1 px up for one step, then down
HOVER_SHADOW_DY = 2.0           # the shadow the lifted card leaves on the ground: offset, blur (sigma) and strength
HOVER_SHADOW_SIGMA = 3.3
HOVER_SHADOW_ALPHA = 0.30
_HOVER_SHADOW_LUT = [int(v * HOVER_SHADOW_ALPHA + 0.5) for v in range(256)]


def _lift(frame: Image.Image, scale: float, shadow: bool) -> HoverPlay:
    """The spring of a card rising in its own frame. The frame's transparent margin above the card (the shadow's room
    on the floating widget, the strip's gap above its plate) is what it rises into, so nothing is resized and a lift
    never goes past that margin. The shadow is made once, from the card's shape, and every frame sits on it."""
    rgba = frame.convert("RGBA")
    card = rgba.getchannel("A").point(lambda v: 255 if v >= 128 else 0)
    box = card.getbbox()
    room = box[1] if box else 0
    ground = _hover_shadow(card, scale) if shadow and box else None

    def lifted(logical: float) -> Image.Image:
        rise = min(int(logical * scale + 0.5), room)
        out = Image.new("RGBA", rgba.size, TRANSPARENT)
        out.paste(rgba, (0, -rise))
        return Image.alpha_composite(ground, out) if ground is not None else out

    frames = tuple(lifted(logical) for logical in HOVER_LIFTS)
    delays = (HOVER_STEP_MS,) * (len(frames) - 1) + (0,)
    return HoverPlay(frames=frames, delays_ms=delays, hold=True, leave=(lifted(HOVER_LEAVE_LIFT),),
                     leave_ms=HOVER_STEP_MS)


def _hover_shadow(card: Image.Image, scale: float) -> Image.Image:
    """A soft black shadow under ``card`` (a 0 / 255 mask), offset down and blurred, at HOVER_SHADOW_ALPHA. The blur
    runs at half size, which is the same picture for a quarter of the work."""
    moved = Image.new("L", card.size, 0)
    moved.paste(card, (0, int(HOVER_SHADOW_DY * scale + 0.5)))
    blurred = moved.reduce(2).filter(ImageFilter.GaussianBlur(max(0.5, HOVER_SHADOW_SIGMA * scale / 2)))
    mask = blurred.resize(card.size, Image.Resampling.BILINEAR).point(_HOVER_SHADOW_LUT)
    shade = Image.new("RGBA", card.size, TRANSPARENT)
    shade.putalpha(mask)
    return shade


# ── Taskbar strip ─────────────────────────────────────────────────────────────

def _strip_fit(height_px: int, scale: float) -> tuple[float, float]:
    """(drawing scale, plate height in logical px) for a taskbar ``height_px`` tall.

    The plate is the strip's 40 px with 4 px above and below on a 48 px taskbar, 34 px on a 40 px one.
    A taskbar shorter than 36 logical px (a small bar at a high scale) shrinks the whole strip instead of
    clipping it.
    """
    available = height_px / scale
    need = STRIP_MIN_PLATE + STRIP_PAD_MIN
    draw_scale = scale * min(1.0, available / need)
    plate = max(float(STRIP_MIN_PLATE), min(float(STRIP_H), height_px / draw_scale - STRIP_PAD_MIN))
    return draw_scale, plate


def _strip_rows(plate_h: float) -> tuple[float, float]:
    """Tops of the two rows. The ink (rail tick to value baseline) is 12 px below a row's top plus
    its 1 px above; the rows are 19 px apart on the 40 px plate and as close as 14 px on a short one."""
    pitch = max(14.0, min(19.0, plate_h - 21.0))
    first = math.floor((plate_h - (pitch + 12.0)) / 2 + 0.5) + 1
    return float(first), float(first) + pitch


def _render_strip(model: WidgetModel, pal: Palette, theme: Any, height_px: int, scale: float,
                  opts: RenderOptions) -> Image.Image:
    draw_scale, plate_h = _strip_fit(height_px, scale)
    ink = Ink(STRIP_W, plate_h, draw_scale, _fonts(theme))
    dim = _dim(model)
    if not opts.transparent_bg:
        ink.rrect(0, 0, STRIP_W, plate_h, STRIP_RADIUS, pal.plate)
    label_face = ink.face(ink.fonts.body, SIZE_STRIP_K, 600)
    value_face = ink.face(ink.fonts.mono, SIZE_STRIP_V, 700)
    rows = list(model.rows[:2])
    for index, (label, top) in enumerate(zip(("5H", "7D"), _strip_rows(plate_h))):
        row = rows[index] if index < len(rows) else None
        ink.put(label, STRIP_K_X, ink.baseline(top, STRIP_ROW_LINE, label_face), label_face, pal.text_3)
        width = STRIP_RAIL_X1 - STRIP_RAIL_X0
        ink.rrect(STRIP_RAIL_X0, top + 1, STRIP_RAIL_X1, top + 3, 1.0, pal.time_track)
        if row is not None and row.time_fraction is not None:
            end = STRIP_RAIL_X0 + width * max(0.0, min(1.0, row.time_fraction))
            fill = _fade(pal.time, DIM_RAIL) if dim else pal.time
            if end - STRIP_RAIL_X0 >= 0.5:
                ink.rrect(STRIP_RAIL_X0, top + 1, end, top + 3, 1.0, fill)
            tick = max(STRIP_RAIL_X0, min(STRIP_RAIL_X1 - 1, end - 1))
            ink.rect(tick, top - 1, tick + 1, top + 5, fill)
        ink.rrect(STRIP_RAIL_X0, top + 5, STRIP_RAIL_X1, top + 9, BAR_RADIUS, pal.track)
        if row is not None and row.percent is not None and row.percent > 0:
            fill_w = width * min(1.0, row.percent / 100)
            ink.rrect(STRIP_RAIL_X0, top + 5, STRIP_RAIL_X0 + fill_w, top + 9, BAR_RADIUS,
                      _usage_color(pal, model, row))
        value = _percent_text(row.percent if row is not None else None)
        color = _value_color(pal, model, row) if row is not None else _fade(pal.text, DIM_VALUE)
        ink.put(value, STRIP_VALUE_RIGHT, ink.baseline(top, STRIP_ROW_LINE, value_face), value_face, color, "right")
    _lamp(ink, pal, _LAMP_KIND[model.status], STRIP_LAMP_X, plate_h / 2)
    if _holding(model):
        _strip_shield(ink, pal, plate_h)
    plate = ink.finish()
    out = Image.new("RGBA", (plate.width, max(int(height_px), plate.height)), TRANSPARENT)
    out.alpha_composite(plate, (0, (out.height - plate.height) // 2))
    return out


# ── Tray icon ─────────────────────────────────────────────────────────────────

def _ring_geometry(size: int) -> tuple[float, float, float, float]:
    """(centre radius, stroke, dash on, dash off) at ``size`` px. 16, 24 and 32 are the approved sizes;
    20 and the others come from the nearest two, and sizes beyond the table scale its ends."""
    keys = sorted(TRAY_RING)
    if size <= keys[0]:
        radius, stroke, on, off = TRAY_RING[keys[0]]
        factor = size / keys[0]
        return radius * factor, stroke * factor, on, off
    if size >= keys[-1]:
        radius, stroke, on, off = TRAY_RING[keys[-1]]
        factor = size / keys[-1]
        return radius * factor, stroke * factor, on, off
    low = max(k for k in keys if k <= size)
    high = min(k for k in keys if k >= size)
    if low == high:
        return TRAY_RING[low]
    t = (size - low) / (high - low)
    return tuple(a + (b - a) * t for a, b in zip(TRAY_RING[low], TRAY_RING[high]))   # type: ignore[return-value]


def _ring_masks(size: int, percent: float | None, dashed: bool, grow: float = 0.0
                ) -> tuple[Image.Image, Image.Image]:
    """(track, arc) coverage at ``size`` px: drawn at 8x, reduced with a box filter. ``grow`` widens
    the stroke by that many px on each side."""
    centre_r, stroke, on, off = _ring_geometry(size)
    big = size * TRAY_SS
    middle = big / 2
    outer = (centre_r + stroke / 2 + grow) * TRAY_SS     # Pillow draws the stroke inward from the box
    box = [middle - outer, middle - outer, middle + outer, middle + outer]
    width = max(1, round((stroke + 2 * grow) * TRAY_SS))
    track = Image.new("L", (big, big), 0)
    arc = Image.new("L", (big, big), 0)
    if dashed:
        period = on + off
        unit = 3.6
        k = 0
        draw = ImageDraw.Draw(track)
        while k * period < 100:
            start = k * period * unit
            end = min(start + on * unit, 360.0)
            if end > start:
                draw.arc(box, start, end, fill=255, width=width)
            k += 1
    else:
        ImageDraw.Draw(track).ellipse(box, outline=255, width=width)
    if percent is not None and percent > 0:
        if percent >= 100:
            ImageDraw.Draw(arc).ellipse(box, outline=255, width=width)
        else:
            ImageDraw.Draw(arc).arc(box, 270, (270 + 3.6 * percent) % 360, fill=255, width=width)
    return track.reduce(TRAY_SS), arc.reduce(TRAY_SS)


def _tinted(mask: Image.Image, color: Color) -> Image.Image:
    layer = Image.new("RGBA", mask.size, (color[0], color[1], color[2], 0))
    layer.putalpha(mask.point(_alpha_table(color[3])))
    return layer


# The tray ring's centre mark for the states that need one: a cross when there is no connection and three dots while
# the API throttles the monitor. A stale ring stays empty (the number at 32 px), so the three differ from each other
# down to 16 px. size -> (the cross's half extent, its stroke, a dot's radius), in px; sizes between two keys
# interpolate and the ends scale.
TRAY_GLYPH: dict[int, tuple[float, float, float]] = {
    16: (1.9, 1.3, 1.0),
    24: (2.9, 1.8, 1.4),
    32: (4.6, 2.4, 1.9),
}
TRAY_GLYPH_KINDS = (LAMP_OFFLINE, LAMP_LIMITED)


def _glyph_geometry(size: int) -> tuple[float, float, float]:
    keys = sorted(TRAY_GLYPH)
    if size <= keys[0]:
        return tuple(value * size / keys[0] for value in TRAY_GLYPH[keys[0]])      # type: ignore[return-value]
    if size >= keys[-1]:
        return tuple(value * size / keys[-1] for value in TRAY_GLYPH[keys[-1]])     # type: ignore[return-value]
    low = max(k for k in keys if k <= size)
    high = min(k for k in keys if k >= size)
    if low == high:
        return TRAY_GLYPH[low]
    t = (size - low) / (high - low)
    return tuple(a + (b - a) * t for a, b in zip(TRAY_GLYPH[low], TRAY_GLYPH[high]))   # type: ignore[return-value]


def _glyph_mask(kind: str, size: int, grow: float = 0.0) -> Image.Image | None:
    """Coverage of the centre mark of a lamp kind at ``size`` px (drawn at 8x and reduced), or None. ``grow`` widens
    every stroke and dot by that many px on each side, for the keyline."""
    if kind not in TRAY_GLYPH_KINDS:
        return None
    half, stroke, dot = _glyph_geometry(size)
    big = size * TRAY_SS
    mask = Image.new("L", (big, big), 0)
    draw = ImageDraw.Draw(mask)
    middle = big / 2
    if kind == LAMP_OFFLINE:
        reach = half * TRAY_SS
        width = max(1, round((stroke + 2 * grow) * TRAY_SS))
        for start, end in (((-reach, -reach), (reach, reach)), ((-reach, reach), (reach, -reach))):
            draw.line([(middle + start[0], middle + start[1]), (middle + end[0], middle + end[1])], fill=255, width=width)
            for x, y in (start, end):                          # round ends
                draw.ellipse([middle + x - width / 2, middle + y - width / 2, middle + x + width / 2,
                              middle + y + width / 2], fill=255)
    else:
        radius = (dot + grow) * TRAY_SS
        for step in (-1, 0, 1):
            x = middle + step * 2.7 * dot * TRAY_SS
            draw.ellipse([x - radius, middle - radius, x + radius, middle + radius], fill=255)
    return mask.reduce(TRAY_SS)


def _render_tray(model: WidgetModel, pal: Palette, theme: Any, size: int) -> Image.Image:
    row = model.rows[0] if model.rows else None
    percent = row.percent if row is not None else None
    stale = model.stale or percent is None
    paused = model.status is ApiStatus.PAUSED
    if stale or paused:
        arc_color = pal.text_3
    elif row is not None and row.severity == SEVERITY_CRITICAL:
        arc_color = pal.usage_alert
    elif row is not None and row.severity == SEVERITY_WARNING:
        arc_color = pal.usage_warn
    else:
        arc_color = pal.tray_fg
    track, arc = _ring_masks(size, percent, dashed=stale)
    outline_track, outline_arc = _ring_masks(size, percent, dashed=stale, grow=min(1.0, KEYLINE * size / 16))
    icon = _tinted(ImageChops.lighter(outline_track, outline_arc), pal.keyline)
    icon.alpha_composite(_tinted(track, pal.tray_track))
    icon.alpha_composite(_tinted(arc, arc_color))
    glyph = _glyph_mask(model.lamp, size)
    if glyph is not None:
        keyline = _glyph_mask(model.lamp, size, grow=min(1.0, KEYLINE * size / 16))
        icon.alpha_composite(_tinted(keyline, pal.keyline))
        icon.alpha_composite(_tinted(glyph, pal.tray_fg))
    elif size >= TRAY_NUMBER_MIN:
        _tray_number(icon, size, percent, pal, theme)
    return icon


def _tray_number(icon: Image.Image, size: int, percent: float | None, pal: Palette, theme: Any) -> None:
    """The session percent in the ring's centre: 10 px semibold on a 32 px icon, set like the other
    text (hinted at output size), a little smaller for three digits."""
    text = "--" if percent is None else str(min(999, max(0, int(percent + 0.5))))
    px = TRAY_NUMBER_SIZE * size / 32 * (0.8 if len(text) > 2 else 1.0)
    body = _fonts(theme).body
    face = Face(body.file_for(600), round(px, 3), 600 if body.variable else 0, px if body.variable else None,
                body.variable)
    start = size / 2 - _text_px(face, text) / 2
    whole = math.floor(start)
    frac = round((start - whole) * 4) / 4
    mask, pad, ascent = _run(face, text, _text_gamma(pal.tray_fg), frac if frac < 1 else 0.0)
    baseline = round(size / 2 + 0.36 * px)
    _blend(icon, mask.filter(ImageFilter.MaxFilter(3)), whole - pad, baseline - ascent - pad, pal.keyline)
    _blend(icon, mask, whole - pad, baseline - ascent - pad, pal.tray_fg)


__all__ = ["GatesSkin", "Palette", "RenderOptions"]
