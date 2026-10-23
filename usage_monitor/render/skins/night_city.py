"""Night City skin, "Quickhack HUD": the approved chamfered-plate design (design/mockups/night_city).

Geometry is the approved 1x layout in logical pixels (final.html and night_city.json, extras.widget_layout and
extras.strip_layout). Canvas draws it at twice the output scale and downsamples, so every surface is crisp at 1,
1.25, 1.5 and 2. Every colour comes from the variant tokens through Palette. The four variants share one
geometry. What differs is a Look (segmented RAM cells or a slanted bar, boxed tags) and, for Edgerun, the yellow
plate whose tokens the theme lists under extras.edgerun_plate.

Surfaces
  floating  264 wide, 96 high for two rows and 126 for three. Header (pip, brand, LIVE), rows (name, reset,
            value, pace hairline, bar), corner brackets. A stale, offline or throttled state swaps the header
            for the hazard band; rows stay where they are and drop to the dim tokens.
  taskbar   240 x 38 strip with the 5H and 7D rows and a status word. It shrinks to fit a short taskbar.
  tooltip   the floating frame with the exact percentage, reset time and pace of every row.
  tray      the RAM meter, tray option B: five stacked cells in an octagonal plate, drawn pixel by pixel.

Motion. RenderOptions.phase carries both animations of this theme. Its fraction is the position in the 2.4 s
eased pulse of the alive mark (0 is fully lit). Its integer part is the frame of the one-shot refresh hit, an RGB
split of the value numerals: 1 splits by 2 px, 2 by 1 px, 3 and up is settled. animation_phase() builds the
value. reduce_motion pins both, so the mark stays lit and no split is ever drawn. The theme never steps or
flickers; a mark that is dark because model.blink_on is False drops to the pulse minimum instead.
"""
from __future__ import annotations

import functools
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, NamedTuple

from PIL import Image, ImageFilter

from usage_monitor import themes as theme_registry
from usage_monitor.data.models import ApiStatus
from usage_monitor.log import get_logger
from usage_monitor.render import engine
from usage_monitor.render.engine import Canvas, Color, Layer, Point, TRANSPARENT, parse_color, with_alpha
from usage_monitor.render.model import (
    LAMP_LIMITED,
    LAMP_LIVE,
    LAMP_OFFLINE,
    LAMP_PAUSED,
    NO_INSETS,
    SEVERITY_CRITICAL,
    SEVERITY_NORMAL,
    SEVERITY_WARNING,
    SURFACE_FLOATING,
    SURFACE_TASKBAR,
    HoverPlay,
    Insets,
    Row,
    WidgetModel,
    pace_delta,
)
from usage_monitor.render.skins import RenderOptions, coerce_options

log = get_logger(__name__)

# ── Geometry (logical px, origin top-left) ────────────────────────────────────

FLOAT_W = 264
ROW_TOP = 28
ROW_PITCH = 30
BOTTOM_PAD = 8                  # frame height is 36 + 30 per row: 96 for two rows, 126 for three
CHAMFER = 8.0                   # top-left and bottom-right; the plate inside the 1 px line has 7
LEFT = 14
RIGHT = 250
CONTENT_W = RIGHT - LEFT        # 236
HEADER_TOP = 8
PIP = 6
PIP_Y = 11
BRAND_GAP = 7                   # pip to brand
HEAD_BASE = 17                  # baseline of the brand and of a plain LIVE word
BOXED_BASE = 18                 # baseline inside a boxed or plate LIVE tag
TAG_TOP = 8                     # the LIVE tag, snapped to whole pixels
TAG_BOTTOM = 21
CHIP_H = 14
CHIP_CUT = 5.0

NAME_BASE = 11                  # baselines are offsets from the row top
TAG_BASE = 10
RESET_BASE = 10
VALUE_BASE = 11
UNIT_BASE = 11
UNIT_GAP = 1
VALUE_GAP = 8                   # reset text to value
MIN_GAP = 4                     # least air between the name side and the value side
RAIL_Y = 15
RAIL_H = 2
TICK_TOP = 13
TICK_H = 6
BAR_Y = 19
BAR_H = 8
CELLS = 26
CELL_W = 7
CELL_STEP = 9
SLANT = 4.0
END_TICK_TOP = 17
END_TICK_H = 12

BAND = (12, 6, 248, 22)         # the stale band: x0, y0, x1, y1
BAND_BASE = 17
BAND_TEXT_X = 20
BAND_AGE_RIGHT = 240
HAZARD_STRIP = 2

TOOLTIP_PITCH = 50
TOOLTIP_INFO = 32

STRIP_W = 240
STRIP_H = 38
STRIP_CHAMFER = 7.0
STRIP_ROWS = (6, 22)
STRIP_LABELS = ("5H", "7D")
STRIP_LABEL_X = 10
STRIP_BAR_X = 28
STRIP_BAR_W = 124
STRIP_RAIL_Y = 2
STRIP_BAR_Y = 5
STRIP_BAR_H = 6
STRIP_CELLS = 18
STRIP_CELL_W = 6
STRIP_CELL_STEP = 7
STRIP_SLANT = 3.0
STRIP_TEXT_BASE = 9
STRIP_PCT_RIGHT = 176
STRIP_TAG = (183, 12, 236, 26)
STRIP_DOT = (186, 16)
STRIP_WORD_X = 196
STRIP_WORD_BASE = 23
STRIP_MARGIN = 1                # least air above and below the strip in a short taskbar

PULSE_MIN = 0.3
HIT_SPLITS = (0.0, 2.0, 1.0)    # refresh hit: x offset of the two copies per frame (index 0 is no hit)
GLOW_SIGMA = 3.0                # the 6 px CSS blur of the numerals and the live pip; Canvas.glow takes blur / 2

# ── Fonts and text styles ─────────────────────────────────────────────────────

FONT_BOLD = "rajdhani/Rajdhani-Bold.ttf"                # theme body, weight 700
FONT_SEMI = "rajdhani/Rajdhani-SemiBold.ttf"            # theme body, weight 600
FONT_MONO = "sharetechmono/ShareTechMono-Regular.ttf"   # theme mono
_FACE_ROLES = {
    FONT_BOLD: ("body", "bold"),
    FONT_SEMI: ("body", "semibold"),
    FONT_MONO: ("mono", "regular"),
}
_FACES: dict[str, dict[str, Path]] = {}


class Type(NamedTuple):
    """A text style: font key, size in logical px, letter-spacing in logical px (CSS style), and the exponent
    applied to glyph coverage on top of the engine's own curve (below 1 is bolder). The browser hints these
    faces to the pixel grid, which draws them heavier than a supersampled render; the exponents close that."""
    face: str
    size: float
    tracking: float = 0.0
    gamma: float = 1.0


BRAND = Type(FONT_BOLD, 12.0, 1.92, 0.7)    # 0.16 em
STATUS = Type(FONT_BOLD, 11.0, 1.32, 0.7)   # 0.12 em: LIVE, the strip labels and its status word
NAME = Type(FONT_BOLD, 13.0, 1.56, 0.7)     # 0.12 em
TAG = Type(FONT_SEMI, 11.0, 0.88, 0.8)      # 0.08 em
CHIP = Type(FONT_BOLD, 11.0, 0.66, 0.7)     # 0.06 em
BAND_TYPE = Type(FONT_BOLD, 11.0, 0.88, 0.7)    # 0.08 em
RESET = Type(FONT_MONO, 11.0, 0.22, 0.85)   # 0.02 em, also the band's age tag
VALUE = Type(FONT_MONO, 15.0, 0.0, 0.7)
UNIT = Type(FONT_MONO, 11.0, 0.0, 0.85)
STRIP_PCT = Type(FONT_MONO, 11.0, 0.0, 0.85)

DEFAULT_STALE_AUTH = "RELINK // OPEN CLAUDE CODE"
DEFAULT_OFFLINE = "NO SIGNAL // NET DOWN"
BAND_STATUSES = frozenset({ApiStatus.STALE, ApiStatus.NO_CREDENTIALS, ApiStatus.OFFLINE, ApiStatus.RATE_LIMITED})
AGE_STATUSES = frozenset({ApiStatus.STALE, ApiStatus.OFFLINE, ApiStatus.RATE_LIMITED})
STRIP_WORDS: dict[ApiStatus, str] = {
    ApiStatus.LIVE: "LIVE",
    ApiStatus.STALE: "STALE",
    ApiStatus.OFFLINE: "OFFLINE",
    ApiStatus.PAUSED: "PAUSED",
    ApiStatus.LOADING: "LOADING",
    ApiStatus.RATE_LIMITED: "LIMITED",
    ApiStatus.NO_CREDENTIALS: "NO LOGIN",
}


# ── Looks and palette ─────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class Look:
    """How a variant draws, beyond its colours: RAM cells or a slanted bar, outlined LIVE and reset tags."""
    segmented: bool = False
    boxed: bool = False


_LOOKS = {
    "blackwall": Look(segmented=True, boxed=True),
    "edgerun": Look(segmented=True),
}


def look_for(variant: Any) -> Look:
    return _LOOKS.get(getattr(variant, "id", None), Look())


@dataclass(frozen=True, slots=True)
class Palette:
    """The tokens one frame needs, resolved from a variant.

    Names follow the mock's CSS tokens. From the variant colours: bg, panel (surface), fg (text), fg_hi
    (text_bright), ink2 (text_dim), line_a (border), accent, ok, warn, danger (alert), track, glow and stale.
    From the variant's extras (variant_tokens and token_extras): line, signal, ink, hz_a, hz_b, glow_accent,
    glow_danger, scan, ring, bar, sel, and ink3 (the ink-3 token, the tertiary text; a variant without one gets
    its stale colour). stale is the tone every live colour drops to while the numbers are not live: a variant
    picks it to stay clear of its live colours, and ink3 follows it only in that state. plate is True once the
    Edgerun plate overrides are applied: a yellow panel with ink text.
    """
    bg: Color
    panel: Color
    track: Color
    fg: Color
    fg_hi: Color
    ink2: Color
    ink3: Color
    line: Color
    line_a: Color
    accent: Color
    ok: Color
    warn: Color
    danger: Color
    signal: Color
    ink: Color
    hz_a: Color
    hz_b: Color
    glow: Color
    glow_accent: Color
    glow_danger: Color
    scan: Color
    ring: Color
    stale: Color
    bar: Color
    sel: Color
    plate: bool = False
    crit_fill: tuple[Color, Color | None] = (TRANSPARENT, None)    # hazard pair; a lone colour is solid
    value_warn: Color = TRANSPARENT
    value_crit: Color = TRANSPARENT

    @classmethod
    def for_variant(cls, theme: Any, variant: Any, *, plate: bool = True) -> Palette:
        colors = _mapping(getattr(variant, "colors", None))
        tokens = _tokens(theme, variant)
        name = getattr(variant, "id", "?")

        def role(key: str) -> Color:
            value = colors.get(key)
            if value is None:
                raise KeyError(f"variant {name!r} has no colour {key!r}")
            return _color(value)

        def token(key: str, default: Color) -> Color:
            value = tokens.get(key)
            return default if value is None else _color(value)

        accent, danger, line_a = role("accent"), role("alert"), role("border")
        ink = token("ink", role("bg"))
        hz_a, hz_b = token("hz-a", danger), token("hz-b", ink)
        palette = cls(
            bg=role("bg"), panel=role("surface"), track=role("track"), fg=role("text"),
            fg_hi=role("text_bright"), ink2=role("text_dim"), ink3=token("ink-3", role("stale")),
            line=token("line", with_alpha(line_a, 255)), line_a=line_a, accent=accent, ok=role("ok"),
            warn=role("warn"), danger=danger, signal=token("signal", accent), ink=ink, hz_a=hz_a, hz_b=hz_b,
            glow=role("glow"), glow_accent=token("glow-accent", with_alpha(accent, 0x80)),
            glow_danger=token("glow-danger", with_alpha(danger, 0x59)), scan=token("scan", (0, 0, 0, 0x26)),
            ring=token("ring", role("track")), stale=role("stale"), bar=token("bar", role("text")),
            sel=token("sel", accent), crit_fill=(hz_a, hz_b), value_warn=role("warn"), value_crit=danger,
        )
        plate_tokens = _plate_tokens(theme, variant)
        return palette.on_plate(plate_tokens) if plate and plate_tokens else palette

    def on_plate(self, tokens: Mapping[str, Any]) -> Palette:
        """The Edgerun plate: a yellow panel, ink text and ink cells. Glows go, a critical fill is solid red,
        and warn and critical values are ink (red text on the yellow is 3.0:1 and is not used)."""
        def get(key: str, current: Color) -> Color:
            value = _plate_color(tokens.get(key))
            return current if value is None else value

        return replace(
            self,
            panel=get("panel", self.panel), line=get("line", self.line), line_a=get("line_a", self.line_a),
            fg=get("fg", self.fg), fg_hi=get("fg_hi", self.fg_hi), ink2=get("ink_2", self.ink2),
            ink3=get("ink_3", self.ink3), accent=get("accent", self.accent), track=get("track", self.track),
            bar=get("bar", self.bar), scan=get("scan", self.scan), glow=get("glow", self.glow),
            glow_accent=get("glow_accent", self.glow_accent), warn=get("warn", self.warn),
            glow_danger=TRANSPARENT, crit_fill=(self.danger, None), value_warn=self.ink, value_crit=self.ink,
            plate=True,
        )

    def stale_tones(self) -> Palette:
        """Labels, values, time lines, fills and marks drop to the stale tone and the halos go. The plate keeps
        its ink text, as the approved plate does, and its marks take the variant's stale tone for the black tag."""
        clear = TRANSPARENT
        if self.plate:
            return replace(self, ok=self.stale, glow=clear, glow_accent=clear, glow_danger=clear)
        tone = self.stale
        return replace(self, fg=tone, fg_hi=tone, ink2=tone, ink3=tone, accent=tone, bar=tone, ok=tone,
                       glow=clear, glow_accent=clear, glow_danger=clear)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _color(value: Any) -> Color:
    return parse_color(value) if isinstance(value, str) else tuple(value)  # type: ignore[return-value]


def _tokens(theme: Any, variant: Any) -> dict[str, Any]:
    """The variant's extra tokens: the theme's keyed tables first, then the variant's own extras on top."""
    extras = _mapping(getattr(theme, "extras", None))
    name = getattr(variant, "id", None)
    merged: dict[str, Any] = {}
    for table in ("variant_tokens", "token_extras"):
        merged.update(_mapping(_mapping(extras.get(table)).get(name)))
    merged.update(_mapping(getattr(variant, "extras", None)))
    return merged


def _plate_tokens(theme: Any, variant: Any) -> Mapping[str, Any]:
    extras = _mapping(getattr(theme, "extras", None))
    return _mapping(_mapping(extras.get(f"{getattr(variant, 'id', None)}_plate")).get("tokens"))


def _plate_color(value: Any) -> Color | None:
    if value == "transparent":
        return TRANSPARENT
    if isinstance(value, str) and value.startswith("#"):
        try:
            return parse_color(value)
        except ValueError:
            return None
    return None


def _faces(theme: Any) -> dict[str, Path]:
    """The face file for each font key: the theme's own role, else the bundled file."""
    key = str(getattr(theme, "id", "night_city"))
    if key not in _FACES:
        resolved: dict[str, Path] = {}
        for font_key, (role, weight) in _FACE_ROLES.items():
            path = None
            try:
                path = theme_registry.font_path(theme, role, weight)
            except (AttributeError, KeyError, TypeError):   # an object that is not a theme
                path = None
            if path is None:
                log.debug("theme %s has no %s/%s font file: using the bundled %s", key, role, weight, font_key)
            resolved[font_key] = path if path is not None else engine.font_file(font_key)
        _FACES[key] = resolved
    return _FACES[key]


def _canvas(width: float, height: float, scale: float, theme: Any) -> Canvas:
    cv = Canvas(width, height, scale)
    cv.faces = _faces(theme)
    return cv


def _palette(theme: Any, variant: Any, model: WidgetModel, opts: RenderOptions) -> Palette:
    """Without its plate (transparent_bg) the Edgerun ink text could not be read on the desktop, so Edgerun
    keeps its dark-theme tokens there."""
    palette = Palette.for_variant(theme, variant, plate=not opts.transparent_bg)
    return palette.stale_tones() if model.stale else palette


# ── Motion ────────────────────────────────────────────────────────────────────

def animation_phase(pulse: float = 0.0, hit_frame: int = 0) -> float:
    """The RenderOptions.phase for a position in the pulse (0..1 of 2.4 s) and a refresh-hit frame (0 none)."""
    return max(0, int(hit_frame)) + (float(pulse) % 1.0)


def pulse_alpha(phase: float) -> float:
    """Opacity of the alive mark: 0.65 + 0.35 cos(2 pi t), eased between 1.0 and 0.3 (no stepped blink)."""
    return 0.65 + 0.35 * math.cos(2 * math.pi * (phase % 1.0))


class Motion(NamedTuple):
    pulse: float        # opacity of the alive mark
    hit: int            # refresh-hit frame: 0 none, 1 and 2 split the numerals


def motion_for(model: WidgetModel, opts: RenderOptions) -> Motion:
    if opts.reduce_motion:
        return Motion(1.0, 0)
    phase = opts.phase if math.isfinite(opts.phase) and opts.phase > 0 else 0.0
    pulse = pulse_alpha(phase) if model.blink_on else PULSE_MIN
    frame = int(phase)
    return Motion(pulse, frame if frame < len(HIT_SPLITS) else 0)


# ── Painting plan: halos first, then marks ────────────────────────────────────

class _Ink:
    """Collects the halos and the crisp marks of one frame; run() draws every halo, then every mark, so a
    glow sits under all content like a CSS text-shadow. Text goes through text() and glyphs(), which also lay
    a dark backing under it when the plate is off, so light text stays readable over a light taskbar."""

    def __init__(self, cv: Canvas, pal: Palette, opts: RenderOptions) -> None:
        self.cv = cv
        self.pal = pal
        self.fx = opts.text_fx
        self.backing = opts.transparent_bg
        self._halos: list[tuple[Layer, float, Color, float]] = []
        self._marks: list[Callable[[Canvas], None]] = []

    # Measuring

    def font(self, spec: Type):
        return self.cv.font(spec.face, spec.size)

    def tracking(self, spec: Type, text: str) -> float:
        """Letter-spacing for a run of ``text``: the CSS value plus an even share of the gap between the run's
        true width and the one FreeType hints at the buffer size. Hinting rounds every advance to a whole
        buffer pixel, so a run can come out a pixel off the browser's (which lays out true advances)."""
        path = getattr(self.font(spec), "path", None)
        if not text or not isinstance(path, str):
            return spec.tracking
        return spec.tracking + _advance_gap(path, spec.size, self.cv.k, text)

    def width(self, text: str, spec: Type) -> float:
        return self.cv.advance(text, self.font(spec), self.tracking(spec, text))

    def snap(self, value: float) -> float:
        """``value`` moved to the nearest device pixel, as the browser snaps the edges of a box."""
        return math.floor(value * self.cv.scale + 0.5) / self.cv.scale

    # Queueing

    def halo(self, layer: Layer | None, sigma: float, color: Color, strength: float = 1.0) -> None:
        if self.fx and layer is not None and color[3] > 0 and strength > 0:
            self._halos.append((layer, sigma, color, strength))

    def mark(self, draw: Callable[[Canvas], None]) -> None:
        self._marks.append(draw)

    def stamp(self, layer: Layer | None, color: Color) -> None:
        if layer is not None and color[3] > 0:
            self._marks.append(lambda cv: cv.stamp(layer, color))

    def rect(self, x0: float, y0: float, x1: float, y1: float, color: Color) -> None:
        self._marks.append(lambda cv: cv.fill_rect(x0, y0, x1, y1, color))

    def outline(self, x0: float, y0: float, x1: float, y1: float, color: Color) -> None:
        self._marks.append(lambda cv: cv.outline_rect(x0, y0, x1, y1, color, 1))

    def polygon(self, points: Sequence[Point], color: Color) -> None:
        self._marks.append(lambda cv: cv.fill_polygon(points, color))

    def hazard(self, rect: tuple[float, float, float, float], a: Color, b: Color | None,
               clip: Sequence[Point] | None = None) -> None:
        self._marks.append(lambda cv: _hazard(cv, rect, a, b, clip))

    def layer(self, text: str, spec: Type, x: float, baseline: float, align: str = "left") -> tuple[Layer, float]:
        layer, advance = self.cv.text_layer(text, x, baseline, self.font(spec), self.tracking(spec, text), align)
        if spec.gamma != 1.0:
            layer = Layer(layer.mask.point(_coverage_curve(spec.gamma)), layer.x, layer.y)
        return layer, advance

    def text(self, text: str, spec: Type, x: float, baseline: float, color: Color, align: str = "left") -> float:
        """Queue a run of text and return its advance (trailing letter-spacing included, as CSS does)."""
        layer, advance = self.layer(text, spec, x, baseline, align)
        self.glyphs(layer, color)
        return advance

    def glyphs(self, layer: Layer, color: Color) -> None:
        if self.backing:
            outline = Layer(layer.mask.filter(ImageFilter.MaxFilter(5)), layer.x, layer.y)
            self.stamp(outline, with_alpha(self.pal.bg, 0xC8))
        self.stamp(layer, color)

    def run(self) -> None:
        for layer, sigma, color, strength in self._halos:
            self.cv.glow(layer, sigma, color, strength)
        for draw in self._marks:
            draw(self.cv)


@functools.lru_cache(maxsize=16)
def _coverage_curve(gamma: float) -> list[int]:
    return [round(255 * (v / 255) ** gamma) for v in range(256)]


@functools.lru_cache(maxsize=4096)
def _em_advance(path: str, char: str) -> float:
    """Advance of ``char`` in em, measured at a size where hinting rounds away."""
    return engine.load_font(path, 2048.0).getlength(char) / 2048.0


@functools.lru_cache(maxsize=1024)
def _advance_gap(path: str, size: float, k: float, text: str) -> float:
    """Per-glyph letter-spacing (logical px) that brings ``text`` to its true width at ``size``."""
    font = engine.load_font(path, round(size * k, 3))
    true = sum(_em_advance(path, char) for char in text) * size
    hinted = sum(font.getlength(char) for char in text) / k
    return (true - hinted) / len(text)


@functools.lru_cache(maxsize=128)
def _hazard_tile(width: int, height: int, stripe_px: float, a: Color, b: Color) -> Image.Image:
    """Diagonal "/" stripes filling a width x height tile, stripe_px wide along x + y (the mock's
    repeating-linear-gradient at -45 degrees). The pattern counts from the bottom-right pixel, which is
    stripe a, as the browser draws it."""
    length = width + height
    ramp = Image.new("RGBA", (length, 1))
    ramp.putdata([a if int(((length - 2) - i) // stripe_px) % 2 == 0 else b for i in range(length)])
    # Each output pixel reads ramp[x + y]; -0.75 puts the sample inside that texel for Pillow's pixel origin.
    return ramp.transform((width, height), Image.Transform.AFFINE, (1, 1, -0.75, 0, 0, 0),
                          Image.Resampling.NEAREST)


def _hazard(cv: Canvas, rect: tuple[float, float, float, float], a: Color, b: Color | None,
            clip: Sequence[Point] | None = None) -> None:
    """Hazard stripes over ``rect``, or colour ``a`` alone when ``b`` is None, optionally cut to a polygon."""
    if b is None:
        if clip is None:
            cv.fill_rect(*rect, a)
        else:
            cv.fill_polygon(clip, a)
        return
    box = cv.rect_layer(*rect)
    tile = _hazard_tile(box.mask.width, box.mask.height, 4.0 * cv.k, a, b)
    if clip is None:
        cv.image.paste(tile, (box.x, box.y))
        return
    shape = cv.polygon_layer(clip)
    mask = Image.new("L", tile.size, 0)
    mask.paste(shape.mask, (shape.x - box.x, shape.y - box.y))
    cv.image.paste(tile, (box.x, box.y), mask)


# ── Shared model reading ──────────────────────────────────────────────────────

def _frame_height(rows: int) -> int:
    return ROW_TOP + ROW_PITCH * max(2, rows) + BOTTOM_PAD


def _finite(value: float | None) -> bool:
    return value is not None and math.isfinite(value)


def _rows(model: WidgetModel) -> list[Row]:
    """The model's rows, with a percent or a time share that is not a finite number treated as missing."""
    rows = []
    for row in model.rows:
        percent = row.percent if _finite(row.percent) else None
        share = row.time_fraction if _finite(row.time_fraction) else None
        if percent != row.percent or share != row.time_fraction:     # NaN never equals itself
            row = replace(row, percent=percent, time_fraction=share)
        rows.append(row)
    return rows


def _emphasis(model: WidgetModel, row: Row) -> str:
    """The severity a row is drawn with. A stale row is flat: the band carries the state."""
    return SEVERITY_NORMAL if model.stale else row.severity


def _digits(percent: float | None) -> str:
    return "--" if percent is None else str(max(0, int(percent + 0.5)))


def _exact_digits(percent: float | None) -> str:
    if percent is None:
        return "--"
    text = f"{max(0.0, percent):.1f}"
    return text[:-2] if text.endswith(".0") else text


def _is_scoped(row: Row) -> bool:
    return row.key.startswith("scoped:")


def _row_name(row: Row) -> str:
    """The row's name as the design shows it: upper case, and a scoped limit is named for its model."""
    name = row.label.strip().upper()
    if _is_scoped(row):
        name = name.removesuffix(" WEEKLY").strip() or name
    return name


def _copy(theme: Any, key: str, default: str) -> str:
    value = _mapping(_mapping(getattr(theme, "extras", None)).get("copy")).get(key)
    return value if isinstance(value, str) and value.strip() else default


def _band_text(theme: Any, model: WidgetModel) -> str:
    if model.status is ApiStatus.OFFLINE:
        return _copy(theme, "offline", DEFAULT_OFFLINE)
    if model.status is ApiStatus.RATE_LIMITED:
        return model.status_text.upper()
    return _copy(theme, "stale_auth", DEFAULT_STALE_AUTH)


def _age_tag(model: WidgetModel) -> str | None:
    """"41 M OLD" for the band, from the model's "41 M AGO"."""
    if model.age_text is None or model.status not in AGE_STATUSES:
        return None
    return model.age_text.removesuffix(" AGO") + " OLD"


def _fit(ink: _Ink, text: str, spec: Type, limit: float) -> str:
    """``text`` cut with trailing dots until it is at most ``limit`` wide."""
    if ink.width(text, spec) <= limit:
        return text
    while len(text) > 1 and ink.width(text.rstrip() + "...", spec) > limit:
        text = text[:-1]
    return text.rstrip() + "..."


def _fit_type(ink: _Ink, text: str, spec: Type, limit: float, floor: float) -> Type:
    """The style that makes ``text`` fit ``limit``: tighter letter-spacing first, then a smaller size."""
    if ink.width(text, spec) <= limit:
        return spec
    tight = spec._replace(tracking=min(spec.tracking, 0.5))
    if ink.width(text, tight) <= limit:
        return tight
    size = spec.size
    while size > floor:
        size = max(floor, size - 0.5)
        trial = spec._replace(size=size, tracking=min(spec.tracking, 0.4))
        if ink.width(text, trial) <= limit:
            return trial
    return spec._replace(size=floor, tracking=min(spec.tracking, 0.3))


# ── Frame, brackets and the header ────────────────────────────────────────────

def _paint_frame(cv: Canvas, pal: Palette, width: float, height: float, chamfer: float, opts: RenderOptions) -> None:
    """The 1 px frame, and inside it the flat panel with its scanlines. transparent_bg leaves the panel out."""
    outer = engine.chamfer_polygon(0, 0, width, height, chamfer, chamfer)
    body = engine.chamfer_polygon(1, 1, width - 1, height - 1, chamfer - 1, chamfer - 1)
    if opts.transparent_bg:
        cv.fill_ring(outer, body, pal.line_a)
        return
    cv.fill_polygon(outer, pal.line_a)
    cv.fill_polygon(body, pal.panel)
    if opts.text_fx and pal.scan[3] > 0:
        # The scan rows count up from the panel's last row, which puts the first one at 1 + height % 3.
        cv.scanlines(1, height - 1, pal.scan[3] / 255, phase=int(height) % 3, clip=body)


def _brackets(ink: _Ink, width: float, height: float) -> None:
    """Accent corner brackets: 9 px, 2 px stroke, top-right and bottom-left."""
    color = ink.pal.accent
    ink.rect(width - 13, 4, width - 4, 6, color)
    ink.rect(width - 6, 4, width - 4, 13, color)
    ink.rect(4, height - 6, 13, height - 4, color)
    ink.rect(4, height - 13, 6, height - 4, color)


def _pip(ink: _Ink, x: float, y: float, color: Color, alpha: float, glow: bool) -> None:
    layer = ink.cv.rect_layer(x, y, x + PIP, y + PIP)
    if glow:
        ink.halo(layer, GLOW_SIGMA, ink.pal.glow_accent, alpha)
    ink.stamp(layer, with_alpha(color, round(255 * alpha)))


def _chip_out_width(ink: _Ink, text: str) -> float:
    return ink.width(text, CHIP) + 12


def _chip_out(ink: _Ink, x: float, y: float, text: str, outline: Color, color: Color) -> float:
    """Outline chip, 14 px tall, 5 px top-left chamfer, filled with the panel. Returns its width."""
    width = _chip_out_width(ink, text)
    x0, x1 = ink.snap(x), ink.snap(x + width)
    outer = [(x0 + CHIP_CUT, y), (x1, y), (x1, y + CHIP_H), (x0, y + CHIP_H), (x0, y + CHIP_CUT)]
    inner = [(x0 + CHIP_CUT, y + 1), (x1 - 1, y + 1), (x1 - 1, y + CHIP_H - 1),
             (x0 + 1, y + CHIP_H - 1), (x0 + 1, y + CHIP_CUT)]
    ink.polygon(outer, outline)
    ink.polygon(inner, ink.pal.panel)
    ink.text(text, CHIP, x + 6, y + 10, color)
    return width


def _chip_crit(ink: _Ink, x: float, y: float) -> float:
    """CRIT: an ink box with a 7 px hazard cap and a 5 px top-right chamfer. Returns its width."""
    pal = ink.pal
    width = ink.width("CRIT", CHIP) + 17
    x0, x1 = ink.snap(x), ink.snap(x + width)
    ink.polygon([(x0, y), (x1 - CHIP_CUT, y), (x1, y + CHIP_CUT), (x1, y + CHIP_H), (x0, y + CHIP_H)], pal.ink)
    ink.hazard((x0, y, x0 + 7, y + CHIP_H), *pal.crit_fill)
    ink.text("CRIT", CHIP, x + 11, y + 10, pal.hz_a)
    return width


def _live_tag(ink: _Ink, look: Look, motion: Motion, text: str) -> None:
    """The header's right-hand word: plain, in a 1 px outline (Blackwall), or on a black chip with the live dot
    (the Edgerun plate)."""
    pal = ink.pal
    word_w = ink.width(text, STATUS)
    if pal.plate:
        left = RIGHT - (word_w + 20)
        ink.rect(ink.snap(left), TAG_TOP, RIGHT, TAG_BOTTOM, pal.ink)
        _pip(ink, left + 5, TAG_TOP + 3, pal.ok, motion.pulse, glow=False)
        ink.text(text, STATUS, left + 15, BOXED_BASE, pal.ok)
    elif look.boxed:
        left = RIGHT - (word_w + 8)
        ink.outline(ink.snap(left), TAG_TOP, RIGHT, TAG_BOTTOM, pal.line)
        ink.text(text, STATUS, left + 4, BOXED_BASE, pal.ok)
    else:
        ink.text(text, STATUS, RIGHT, HEAD_BASE, pal.ok, align="right")


def _draw_header(ink: _Ink, look: Look, model: WidgetModel, motion: Motion) -> None:
    """Alive mark and brand on the left, the status word on the right."""
    pal = ink.pal
    live = model.status is ApiStatus.LIVE and not model.stale
    brand_x = LEFT
    if not pal.plate:
        _pip(ink, LEFT, PIP_Y, pal.ok if live else pal.stale, motion.pulse if live else 1.0, glow=live)
        brand_x = LEFT + PIP + BRAND_GAP
    ink.text("CLAUDE//USAGE", BRAND, brand_x, HEAD_BASE, pal.fg_hi)
    if live:
        _live_tag(ink, look, motion, "LIVE")
        return
    text = model.status_text.upper()
    _chip_out(ink, RIGHT - _chip_out_width(ink, text), HEADER_TOP - 1, text, pal.ink3, pal.ink2)


def _draw_band(ink: _Ink, theme: Any, model: WidgetModel) -> None:
    """The stale band: an ink box with a warn outline, hazard strips top and bottom, the cause and its age."""
    pal = ink.pal
    x0, y0, x1, y1 = BAND
    ink.rect(x0, y0, x1, y1, pal.ink)
    ink.outline(x0, y0, x1, y1, pal.warn)
    fill_a, fill_b = pal.crit_fill
    ink.hazard((x0, y0, x1, y0 + HAZARD_STRIP), fill_a, fill_b)
    ink.hazard((x0, y1 - HAZARD_STRIP, x1, y1), fill_a, fill_b)
    text = _band_text(theme, model)
    age = _age_tag(model)
    age_w = ink.width(age, RESET) if age else 0.0
    room = (BAND_AGE_RIGHT - age_w - 10 if age else BAND_AGE_RIGHT) - BAND_TEXT_X
    fitted = _fit(ink, text, BAND_TYPE, room)
    ink.text(fitted, BAND_TYPE, BAND_TEXT_X, BAND_BASE, pal.warn)
    if age and fitted == text:
        ink.text(age, RESET, BAND_AGE_RIGHT, BAND_BASE, pal.signal if pal.plate else pal.ink2, align="right")


# ── Rows ──────────────────────────────────────────────────────────────────────

class RowBoxes(NamedTuple):
    """Where a row's parts sit on the x axis, as (left, right). tag, chip and reset are None when absent or
    dropped for room."""
    name: tuple[float, float]
    tag: tuple[float, float] | None
    chip: tuple[float, float] | None
    reset: tuple[float, float] | None
    value: tuple[float, float]


def _chip_width(ink: _Ink, emphasis: str) -> float:
    if emphasis == SEVERITY_CRITICAL:
        return ink.width("CRIT", CHIP) + 17
    if emphasis == SEVERITY_WARNING:
        return _chip_out_width(ink, "WARN")
    return 0.0


def _layout_row(ink: _Ink, look: Look, row: Row, emphasis: str, digits: str, reset: str | None) -> RowBoxes:
    """Fit a row's name side against its value side. A row too wide for both gives up its reset text first,
    then its share tag, and then the end of its name; the value and the severity chip always stay."""
    unit_w = ink.width("%", UNIT) if row.percent is not None else 0.0
    value_w = ink.width(digits, VALUE) + (UNIT_GAP + unit_w if unit_w else 0.0)
    value = (RIGHT - value_w, float(RIGHT))
    name = _row_name(row)
    name_w = ink.width(name, NAME)
    tag = row.share_text.upper() if _is_scoped(row) and row.share_text else None
    tag_w = ink.width(tag, TAG) if tag else 0.0
    chip_w = _chip_width(ink, emphasis)

    def end_of(name_width: float, with_tag: bool) -> float:
        end = LEFT + name_width + ((6 + tag_w) if with_tag and tag else 0.0)
        return end + ((6 + chip_w) if chip_w else 0.0)

    limit = value[0] - MIN_GAP
    reset_box = None
    if reset is not None:
        reset_w = ink.width(reset, RESET) + (8 if look.boxed else 0)
        right = value[0] - VALUE_GAP
        if end_of(name_w, True) + MIN_GAP <= right - reset_w:
            reset_box = (right - reset_w, right)
            limit = reset_box[0] - MIN_GAP
    with_tag = bool(tag) and end_of(name_w, True) <= limit
    if end_of(name_w, with_tag) > limit:
        name_w = ink.width(_fit(ink, name, NAME, limit - end_of(0.0, with_tag)), NAME)
    x = LEFT + name_w
    tag_box = None
    if with_tag:
        tag_box = (x + 6, x + 6 + tag_w)
        x = tag_box[1]
    chip_box = (x + 6, x + 6 + chip_w) if chip_w else None
    return RowBoxes((LEFT, LEFT + name_w), tag_box, chip_box, reset_box, value)


def _draw_value(ink: _Ink, row: Row, emphasis: str, motion: Motion, top: float, digits: str) -> None:
    """The numerals in Share Tech Mono 15 with a small dim percent sign, glowing; a refresh hit splits them."""
    pal = ink.pal
    has_unit = row.percent is not None
    unit_w = ink.width("%", UNIT) if has_unit else 0.0
    if has_unit:
        ink.text("%", UNIT, RIGHT, top + UNIT_BASE, pal.ink2, align="right")
    color, glow = pal.fg_hi, pal.glow
    if emphasis == SEVERITY_WARNING:
        color = pal.value_warn
    elif emphasis == SEVERITY_CRITICAL:
        color, glow = pal.value_crit, pal.glow_danger
    layer, _ = ink.layer(digits, VALUE, RIGHT - (unit_w + UNIT_GAP if has_unit else 0.0), top + VALUE_BASE,
                         align="right")
    ink.halo(layer, GLOW_SIGMA, glow)
    if motion.hit:
        shift = round(HIT_SPLITS[motion.hit] * ink.cv.kx)
        ink.stamp(Layer(layer.mask, layer.x - shift, layer.y), pal.sel)
        ink.stamp(Layer(layer.mask, layer.x + shift, layer.y), pal.danger)
    ink.glyphs(layer, color)


def _draw_rail(ink: _Ink, row: Row, top: float) -> None:
    """The pace hairline: the window in the track colour, the elapsed share in accent, a tick at "now"."""
    pal = ink.pal
    y = top + RAIL_Y
    ink.rect(LEFT, y, RIGHT, y + RAIL_H, pal.track)
    if row.time_fraction is None:
        return
    x = LEFT + CONTENT_W * min(1.0, max(0.0, row.time_fraction))
    ink.rect(LEFT, y, x, y + RAIL_H, pal.accent)
    ink.rect(x - 1, top + TICK_TOP, x + 1, top + TICK_TOP + TICK_H, pal.accent)


def _fill_for(pal: Palette, emphasis: str) -> tuple[Color, Color | None]:
    """The bar fill: the usage colour, amber for a warning, the hazard pair (or solid red) when critical."""
    if emphasis == SEVERITY_CRITICAL:
        return pal.crit_fill
    if emphasis == SEVERITY_WARNING:
        return pal.warn, None
    return pal.bar, None


def _draw_cells(ink: _Ink, x: float, y: float, step: float, width: float, height: float, cells: int,
                percent: float | None, fill: tuple[Color, Color | None]) -> None:
    """A RAM bar: every cell in the track colour, the lit share of each over it. Cell i holds
    clamp(p / 100 * cells - i, 0, 1) of its width."""
    cv = ink.cv
    ink.stamp(cv.rects_layer([(x + step * i, y, x + step * i + width, y + height) for i in range(cells)]),
              ink.pal.track)
    full, fraction = engine.split_segments(percent, cells)
    lit = [(x + step * i, y, x + step * i + width, y + height) for i in range(full)]
    part = round(width * fraction * cv.scale) / cv.scale      # the part-lit cell ends on a whole device pixel
    if full < cells and part > 0:
        start = x + step * full
        lit.append((start, y, start + part, y + height))
    colour, second = fill
    if second is None:
        ink.stamp(cv.rects_layer(lit), colour)
        return
    for rect in lit:
        ink.hazard(rect, colour, second)


def _draw_slanted(ink: _Ink, x: float, y: float, width: float, height: float, slant: float,
                  percent: float | None, fill: tuple[Color, Color | None]) -> None:
    """A solid bar whose fill has a 45 degree leading edge, cut to the track."""
    ink.rect(x, y, x + width, y + height, ink.pal.track)
    if percent is None or percent <= 0:
        return
    reach = width * min(100.0, percent) / 100.0
    if reach >= slant:
        shape = [(x, y), (x + reach, y), (x + reach - slant, y + height), (x, y + height)]
    else:
        shape = [(x, y), (x + reach, y), (x, y + height * reach / slant)]
    colour, second = fill
    if second is None:
        ink.polygon(shape, colour)
    else:
        ink.hazard((x, y, x + reach, y + height), colour, second, clip=shape)


def _draw_bar(ink: _Ink, look: Look, row: Row, emphasis: str, top: float) -> None:
    fill = _fill_for(ink.pal, emphasis)
    y = top + BAR_Y
    if look.segmented:
        _draw_cells(ink, LEFT, y, CELL_STEP, CELL_W, BAR_H, CELLS, row.percent, fill)
        return
    _draw_slanted(ink, LEFT, y, CONTENT_W, BAR_H, SLANT, row.percent, fill)
    ink.rect(RIGHT - 1, top + END_TICK_TOP, RIGHT, top + END_TICK_TOP + END_TICK_H, ink.pal.ink3)   # the 100 % mark


def _reset_text(row: Row, tooltip: bool) -> str | None:
    """The reset text of a row. The widget leaves it off scoped rows, which reset with the weekly limit."""
    if not row.resets_text or (_is_scoped(row) and not tooltip):
        return None
    return f"RST {row.resets_text.upper()}"


def _draw_reset(ink: _Ink, look: Look, text: str, box: tuple[float, float], top: float) -> None:
    pal = ink.pal
    if look.boxed:
        ink.outline(ink.snap(box[0]), top, ink.snap(box[1]), top + CHIP_H, pal.line)
        ink.text(text, RESET, box[0] + 4, top + RESET_BASE, pal.ink2)
    else:
        ink.text(text, RESET, box[1], top + RESET_BASE, pal.ink2, align="right")


def _draw_row(ink: _Ink, look: Look, model: WidgetModel, row: Row, top: float, motion: Motion,
              tooltip: bool = False) -> None:
    """One limit: name (tag, chip) and reset text above, value on the right, hairline and bar below. The
    tooltip moves the reset text to an info line under the bar and shows the exact percentage."""
    pal = ink.pal
    emphasis = _emphasis(model, row)
    digits = _exact_digits(row.percent) if tooltip else _digits(row.percent)
    reset = _reset_text(row, tooltip)
    boxes = _layout_row(ink, look, row, emphasis, digits, None if tooltip else reset)
    name = _row_name(row)
    if ink.width(name, NAME) > boxes.name[1] - boxes.name[0] + 0.01:
        name = _fit(ink, name, NAME, boxes.name[1] - boxes.name[0])
    ink.text(name, NAME, LEFT, top + NAME_BASE, pal.fg)
    if boxes.tag is not None and row.share_text:
        ink.text(row.share_text.upper(), TAG, boxes.tag[0], top + TAG_BASE, pal.ink3)
    if boxes.chip is not None:
        if emphasis == SEVERITY_CRITICAL:
            _chip_crit(ink, boxes.chip[0], top)
        else:
            colour = pal.ink if pal.plate else pal.warn
            _chip_out(ink, boxes.chip[0], top, "WARN", colour, colour)
    if boxes.reset is not None and reset is not None:
        _draw_reset(ink, look, reset, boxes.reset, top)
    _draw_value(ink, row, emphasis, motion, top, digits)
    _draw_rail(ink, row, top)
    _draw_bar(ink, look, row, emphasis, top)
    if tooltip:
        _draw_info(ink, look, model, row, reset, top + TOOLTIP_INFO)


# ── Tooltip info line ─────────────────────────────────────────────────────────

def _pace_text(row: Row) -> tuple[str, str]:
    """(text, kind) for the pace of a row. kind is "ahead" (a warn chip), "behind" (an accent chip) or "plain"."""
    delta = pace_delta(row.percent, row.time_fraction)
    if delta is None or row.pace is None:
        return "PACE --", "plain"
    if row.pace == "on_pace":
        return "ON PACE", "plain"
    points = round(abs(delta))
    if row.pace == "ahead":
        return f"AHEAD +{points}", "ahead"
    return f"UNDER PACE -{points}", "behind"


def _draw_info(ink: _Ink, look: Look, model: WidgetModel, row: Row, reset: str | None, top: float) -> None:
    """Reset time on the left, pace on the right: a chip when it is news (ahead, under), plain text otherwise."""
    pal = ink.pal
    if reset is not None:
        width = ink.width(reset, RESET) + (8 if look.boxed else 0)
        _draw_reset(ink, look, reset, (LEFT, LEFT + width), top)
    text, kind = _pace_text(row)
    if kind == "plain" or model.stale:
        ink.text(text, CHIP, RIGHT, top + TAG_BASE, pal.ink2 if row.pace else pal.ink3, align="right")
        return
    colour = (pal.ink if pal.plate else pal.warn) if kind == "ahead" else pal.accent
    _chip_out(ink, RIGHT - _chip_out_width(ink, text), top, text, colour, colour)


# ── Floating widget and tooltip ───────────────────────────────────────────────

def _draw_surface(ink: _Ink, theme: Any, look: Look, model: WidgetModel, rows: list[Row], pitch: int,
                  motion: Motion, height: int, tooltip: bool) -> None:
    _brackets(ink, FLOAT_W, height)
    if model.status in BAND_STATUSES:
        _draw_band(ink, theme, model)
    else:
        _draw_header(ink, look, model, motion)
    for index, row in enumerate(rows):
        _draw_row(ink, look, model, row, ROW_TOP + pitch * index, motion, tooltip)


def _tooltip_height(rows: int) -> int:
    return ROW_TOP + TOOLTIP_PITCH * (max(2, rows) - 1) + TOOLTIP_INFO + CHIP_H + BOTTOM_PAD


# ── Taskbar strip ─────────────────────────────────────────────────────────────

def _draw_strip_row(ink: _Ink, look: Look, model: WidgetModel, row: Row | None, top: float, label: str) -> None:
    pal = ink.pal
    percent = row.percent if row is not None else None
    emphasis = _emphasis(model, row) if row is not None else SEVERITY_NORMAL
    ink.text(label, STATUS, STRIP_LABEL_X, top + STRIP_TEXT_BASE, pal.fg_hi)

    rail = top + STRIP_RAIL_Y
    ink.rect(STRIP_BAR_X, rail, STRIP_BAR_X + STRIP_BAR_W, rail + RAIL_H, pal.track)
    if row is not None and row.time_fraction is not None:
        x = STRIP_BAR_X + STRIP_BAR_W * min(1.0, max(0.0, row.time_fraction))
        ink.rect(STRIP_BAR_X, rail, x, rail + RAIL_H, pal.accent)
        ink.rect(x - 1, top, x + 1, top + TICK_H, pal.accent)

    fill = _fill_for(pal, emphasis)
    bar_y = top + STRIP_BAR_Y
    if look.segmented:
        _draw_cells(ink, STRIP_BAR_X, bar_y, STRIP_CELL_STEP, STRIP_CELL_W, STRIP_BAR_H, STRIP_CELLS, percent, fill)
        bar_end = STRIP_BAR_X + STRIP_CELL_STEP * (STRIP_CELLS - 1) + STRIP_CELL_W
    else:
        _draw_slanted(ink, STRIP_BAR_X, bar_y, STRIP_BAR_W, STRIP_BAR_H, STRIP_SLANT, percent, fill)
        bar_end = STRIP_BAR_X + STRIP_BAR_W

    text = "--" if percent is None else f"{_digits(percent)}%"
    spec = _fit_type(ink, text, STRIP_PCT, STRIP_PCT_RIGHT - bar_end - 2, 8.0)
    color = pal.fg_hi
    if emphasis == SEVERITY_WARNING:
        color = pal.value_warn
    elif emphasis == SEVERITY_CRITICAL:
        color = pal.value_crit
    ink.text(text, spec, STRIP_PCT_RIGHT, top + STRIP_TEXT_BASE, color, align="right")


def _draw_strip_status(ink: _Ink, look: Look, model: WidgetModel, motion: Motion) -> None:
    """The status word. While live it sits after the pulsing dot, as the design shows. Any other state drops
    the dot and centres its word, dim, in the tag."""
    pal = ink.pal
    x0, y0, x1, y1 = STRIP_TAG
    if pal.plate:
        ink.rect(x0, y0, x1, y1, pal.ink)
    elif look.boxed:
        ink.outline(x0, y0, x1, y1, pal.line)
    live = model.status is ApiStatus.LIVE and not model.stale
    word = STRIP_WORDS.get(model.status, model.status_text.upper())
    if live:
        _pip(ink, STRIP_DOT[0], STRIP_DOT[1], pal.ok, motion.pulse, glow=False)
        ink.text(word, STATUS, STRIP_WORD_X, STRIP_WORD_BASE, pal.ok)
        return
    spec = _fit_type(ink, word, STATUS, x1 - x0 - 8, 9.0)
    centre = (x0 + x1) / 2 + spec.tracking / 2
    ink.text(word, spec, centre, STRIP_WORD_BASE, pal.stale, align="centre")


def _render_strip(model: WidgetModel, theme: Any, variant: Any, scale: float, opts: RenderOptions) -> Image.Image:
    look = look_for(variant)
    pal = _palette(theme, variant, model, opts)
    cv = _canvas(STRIP_W, STRIP_H, scale, theme)
    _paint_frame(cv, pal, STRIP_W, STRIP_H, STRIP_CHAMFER, opts)
    ink = _Ink(cv, pal, opts)
    rows = _rows(model)[:2]
    for index, top in enumerate(STRIP_ROWS):
        _draw_strip_row(ink, look, model, rows[index] if index < len(rows) else None, top, STRIP_LABELS[index])
    _draw_strip_status(ink, look, model, motion_for(model, opts))
    ink.run()
    return cv.finish()


# ── Tray icon: the RAM meter ──────────────────────────────────────────────────

TRAY_CELLS = 5
# size -> (cell width, cell height, gap, plate chamfer): the approved 16, 24 and 32 px masters, and a 20 px
# one derived from them (the mock does not draw it).
_TRAY_TABLE = {16: (8, 2, 1, 3), 20: (10, 2, 2, 3), 24: (10, 3, 1, 4), 32: (14, 4, 2, 4)}


def _tray_geometry(size: int) -> tuple[int, int, int, int]:
    """(cell width, cell height, gap, chamfer) for an icon size. Sizes off the table scale the nearest one."""
    if size in _TRAY_TABLE:
        return _TRAY_TABLE[size]
    inner = size - 2
    gap = 1 if size < 28 else 2
    height = max(1, (inner - gap * (TRAY_CELLS - 1)) // TRAY_CELLS)
    if height * TRAY_CELLS + gap * (TRAY_CELLS - 1) > inner:
        gap = 0
    width = max(2, (size // 2) // 2 * 2)
    return width, height, gap, 3 if size < 24 else 4


def _octagon_span(y: int, size: int, chamfer: int, inset: int) -> tuple[int, int] | None:
    """The pixel run [left, right) of one scanline of the chamfered square the mock calls octo(): a square
    inset by ``inset`` with 45 degree corners. Edges round at the pixel centre, as the browser's crisp-edge
    fill does, so the run matches the approved pixels."""
    a, b = inset, size - inset
    k = int(math.floor(chamfer + inset * 0.414 + 0.5))
    yc = y + 0.5
    if not a <= yc < b:
        return None
    left = max(a, a + k - yc, a + (yc - (size - k)))
    right = min(b, (size - k) + (yc - a), b - (yc - (size - k)))
    first, last = int(math.floor(left + 0.5)), int(math.floor(right + 0.5))
    return (first, last) if last > first else None


# What the cells cannot say. The plain meter shows the level and the colour of the status; two states need a mark of
# their own (a stale icon keeps the plain meter in the stale colour, so each of them differs from it and from each other):
#   paused   the cells are cut in two columns that keep their level, which reads as the pause sign
#   offline  a cross in the stale colour on the plate: NO SIGNAL
#   limited  an hourglass in the stale colour on the plate: the API makes the monitor wait
# The marks are bitmaps, one px per "#", centred on the plate: bold enough to hold together at 16 px.
_PAUSE_GAP = 2


def _tray_pause_row(cell_w: int) -> str | None:
    """One cell cut in two bars with a gap between them, or None when the cell is too narrow to cut."""
    bar = (cell_w - _PAUSE_GAP) // 2
    if bar < 1:
        return None
    return "#" * bar + "." * (cell_w - 2 * bar) + "#" * bar


def _tray_cross(size: int) -> tuple[str, ...]:
    """An X filling a ``size`` square, its diagonals thick enough for the size (a quarter of it, at least 2 px)."""
    thickness = max(2, size // 4)
    return tuple(
        "".join("#" if abs(x - y) < thickness or abs(x - (size - 1 - y)) < thickness else "." for x in range(size))
        for y in range(size)
    )


def _tray_hourglass(size: int) -> tuple[str, ...]:
    """An hourglass filling a ``size`` square: a bar at the top and bottom, narrowing by a pixel a side per row."""
    rows = []
    for y in range(size):
        width = size - 2 * min(y, size - 1 - y)
        rows.append("." * ((size - width) // 2) + "#" * width + "." * (size - width - (size - width) // 2))
    return tuple(rows)


def _tray_mark(kind: str, size: int) -> tuple[str, ...] | None:
    """The bitmap that stands in for the cells in the offline and limited states, or None."""
    side = size // 2
    if kind == LAMP_OFFLINE:
        return _tray_cross(side)
    if kind == LAMP_LIMITED:
        return _tray_hourglass(side)
    return None


def _render_tray(size: int, percent: float | None, lit: Color, pal: Palette, kind: str = LAMP_LIVE) -> Image.Image:
    """The line-coloured octagon, the bg octagon inside it, then five cells filling from the bottom. Lit cells
    = round(pct / 20). One pixel per cell edge and no anti-aliasing, so it stays sharp at 16 px. ``kind`` is the lamp
    kind of the status (model.LAMP_KINDS): paused cuts the cells in two columns, and offline and limited draw a cross
    and an hourglass in place of the cells (see _tray_mark)."""
    cell_w, cell_h, gap, chamfer = _tray_geometry(size)
    img = Image.new("RGBA", (size, size), TRANSPARENT)
    px = img.load()
    for inset, colour in ((0, pal.line), (1, pal.bg)):
        for y in range(size):
            span = _octagon_span(y, size, chamfer, inset)
            if span is not None:
                for x in range(span[0], span[1]):
                    px[x, y] = colour
    mark = _tray_mark(kind, size)
    if mark is not None:
        left, top = (size - len(mark[0])) // 2, (size - len(mark)) // 2
        for j, row in enumerate(mark):
            for i, ink in enumerate(row):
                if ink == "#":
                    px[left + i, top + j] = lit
        return img
    fraction = 0.0 if percent is None else max(0.0, min(100.0, percent)) / 100.0
    count = int(math.floor(fraction * TRAY_CELLS + 0.5))
    total = TRAY_CELLS * cell_h + (TRAY_CELLS - 1) * gap
    x0 = (size - cell_w) // 2
    y0 = (size - total) // 2
    split = _tray_pause_row(cell_w) if kind == LAMP_PAUSED else None
    for index in range(TRAY_CELLS):
        top = y0 + (TRAY_CELLS - 1 - index) * (cell_h + gap)
        colour = lit if index < count else pal.ring
        for y in range(top, top + cell_h):
            for x in range(x0, x0 + cell_w):
                if split is None or split[x - x0] == "#":
                    px[x, y] = colour
    return img


# ── The skin ──────────────────────────────────────────────────────────────────

class NightCitySkin:
    """The Night City renderer for all four surfaces."""

    theme_id = "night_city"

    def floating_size(self, theme: Any, model: WidgetModel, scale: float) -> tuple[int, int]:
        return round(FLOAT_W * scale), round(_frame_height(len(model.rows)) * scale)

    def render_floating(self, model: WidgetModel, theme: Any, variant: Any, scale: float = 1.0,
                        opts: RenderOptions | Mapping[str, Any] | None = None) -> Image.Image:
        o = coerce_options(opts)
        rows = _rows(model)
        height = _frame_height(len(rows))
        pal = _palette(theme, variant, model, o)
        cv = _canvas(FLOAT_W, height, scale, theme)
        _paint_frame(cv, pal, FLOAT_W, height, CHAMFER, o)
        ink = _Ink(cv, pal, o)
        _draw_surface(ink, theme, look_for(variant), model, rows, ROW_PITCH, motion_for(model, o), height, False)
        ink.run()
        return cv.finish()

    def render_taskbar(self, model: WidgetModel, theme: Any, variant: Any, height_px: int, scale: float = 1.0,
                       opts: RenderOptions | Mapping[str, Any] | None = None) -> Image.Image:
        """The strip centred in a ``height_px`` tall image. A bar too short for the strip at ``scale`` gets a
        strip drawn smaller, so nothing is ever cut off."""
        o = coerce_options(opts)
        height = int(height_px)
        fit = scale if height <= 0 else min(scale, (height - 2 * STRIP_MARGIN) / STRIP_H)
        strip = _render_strip(model, theme, variant, max(0.25, fit), o)
        if height <= strip.height:
            return strip
        out = Image.new("RGBA", (strip.width, height), TRANSPARENT)
        out.alpha_composite(strip, (0, (height - strip.height) // 2))
        return out

    def render_tooltip(self, model: WidgetModel, theme: Any, variant: Any, scale: float = 1.0) -> Image.Image:
        o = RenderOptions()
        rows = _rows(model)
        height = _tooltip_height(len(rows))
        pal = _palette(theme, variant, model, o)
        cv = _canvas(FLOAT_W, height, scale, theme)
        _paint_frame(cv, pal, FLOAT_W, height, CHAMFER, o)
        ink = _Ink(cv, pal, o)
        _draw_surface(ink, theme, look_for(variant), model, rows, TOOLTIP_PITCH, motion_for(model, o), height, True)
        ink.run()
        return cv.finish()

    def render_tray_icon(self, model: WidgetModel, theme: Any, variant: Any, size_px: int) -> Image.Image:
        size = int(size_px)
        if size < 8:
            raise ValueError(f"tray icon size must be at least 8 px, not {size}")
        pal = Palette.for_variant(theme, variant, plate=False)
        rows = _rows(model)
        row = rows[0] if rows else None
        percent = row.percent if row is not None else None
        if model.stale or percent is None:
            lit = pal.stale
        elif row.severity == SEVERITY_CRITICAL:
            lit = pal.danger
        elif row.severity == SEVERITY_WARNING:
            lit = pal.warn
        else:
            lit = pal.bar
        return _render_tray(size, percent, lit, pal, model.lamp)

    def content_insets(self, surface: str, theme: Any, model: WidgetModel, scale: float) -> Insets:
        """No transparent margin: every Night City frame is its visible surface (the chamfers cut corners, not the box)."""
        return NO_INSETS

    def hover_frames(self, frame: Image.Image, surface: str, scale: float,
                     opts: RenderOptions | Mapping[str, Any] | None = None) -> HoverPlay | None:
        """The pointer response of the floating widget and the strip: a one-shot glitch of three 60 ms frames (an RGB
        split with a torn band, then less of both, then a hairline split), after which the normal frame returns."""
        if surface not in (SURFACE_FLOATING, SURFACE_TASKBAR):
            return None
        frames = tuple(_glitch(frame, scale, split, band, shift) for split, band, shift in HOVER_GLITCH)
        return HoverPlay(frames=frames, delays_ms=(HOVER_FRAME_MS,) * len(frames), hold=False)


# ── Hover ─────────────────────────────────────────────────────────────────────

HOVER_FRAME_MS = 60
# Per frame: the red channel moves by +split logical px and green and blue by -split; a band between the two heights
# (shares of the frame's height) moves by ``shift`` px. Values are logical px at 100 %, the motion spec's 5, 4 and 2 px
# and 7 and 5 px at 150 %.
HOVER_GLITCH: tuple[tuple[float, tuple[float, float] | None, float], ...] = (
    (3.3, (0.38, 0.46), 4.7),
    (-2.7, (0.62, 0.69), -3.3),
    (1.3, None, 0.0),
)


def _shifted(channel: Image.Image, dx: int) -> Image.Image:
    """``channel`` moved ``dx`` px sideways, with black where it left a gap (nothing wraps round)."""
    if dx == 0:
        return channel
    moved = Image.new("L", channel.size, 0)
    moved.paste(channel, (dx, 0))
    return moved


def _glitch(frame: Image.Image, scale: float, split: float, band: tuple[float, float] | None, shift: float) -> Image.Image:
    """One glitch frame: the red channel and the green and blue ones pulled apart, and a band torn sideways. The
    alpha channel is not split, so the plate and its frame keep their shape while their colours fringe."""
    red, green, blue, alpha = frame.convert("RGBA").split()
    dx = round(split * scale)
    out = Image.merge("RGBA", (_shifted(red, dx), _shifted(green, -dx), _shifted(blue, -dx), alpha))
    if band is not None and round(shift * scale) != 0:
        top, bottom = round(frame.height * band[0]), round(frame.height * band[1])
        torn = Image.new("RGBA", (frame.width, bottom - top), TRANSPARENT)
        torn.paste(out.crop((0, top, frame.width, bottom)), (round(shift * scale), 0))
        out.paste(torn, (0, top))
    return out


__all__ = ["NightCitySkin", "Palette", "Look", "RenderOptions", "animation_phase", "pulse_alpha"]
