"""Nostromo skin, "Phosphor Console": the approved amber-on-CRT design (design/mockups/nostromo).

Geometry is the approved 1x layout in logical pixels (see design/mockups/nostromo/DESIGN_NOTES.md
and the CSS in final.html). Canvas draws it at twice the scale and downsamples, so every surface
is crisp at 1, 1.25, 1.5 and 2. Every colour comes from the variant tokens through Palette, so
all eight variants render with the same code and no per-variant rules.

Draw order per frame: background, glow halos, crisp marks, then scanlines and the hazard band.
That is the CSS painting order, with halos kept under the marks they belong to.
"""
from __future__ import annotations

import functools
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from usage_monitor import themes as theme_registry
from usage_monitor.themes import colour as colour_math
from usage_monitor.data.models import ApiStatus
from usage_monitor.render import engine
from usage_monitor.render.engine import Canvas, Color, Layer, TRANSPARENT, parse_color, with_alpha
from usage_monitor.render.model import (
    LAMP_LIMITED,
    LAMP_OFFLINE,
    LAMP_PAUSED,
    NO_INSETS,
    SEVERITY_CRITICAL,
    SEVERITY_WARNING,
    SURFACE_FLOATING,
    SURFACE_TASKBAR,
    HoverPlay,
    Insets,
    Row,
    WidgetModel,
    pace_delta,
    worst_severity,
)
from usage_monitor.render.skins import RenderOptions, coerce_options

# ── Geometry (logical px, origin top-left) ────────────────────────────────────

FLOAT_W = 280
FLOAT_BASE_H = 62               # frame height is 62 + 32 per row after the first (94, 126, 158 ...)
ROW_TOP = 28
ROW_PITCH = 32
LEFT = 14
RIGHT = 266
CHAMFER = 10.0
RULE_Y = 21
HEADER_TOP = 6
HEADER_H = 12
RAIL_OFFSET = 17
PIP_OFFSET = 21
PIP_CELLS = 16
PIP_W = 12
PIP_STEP = 16                   # 12 px cell plus 4 px gap
MESSAGE_TOP = 92
MESSAGE_H = 28
HAZARD_H = 4
HAZARD_PERIOD = 8
SCAN_STRENGTH = 0.3

TOOLTIP_ROW_PITCH = 50
TOOLTIP_INFO_OFFSET = 34
QUALIFIER_GAP = 6               # between a row's name and its qualifier ("5H", "OF 25% SHARE")
ARMED_GAP = 10                  # air round the armed guard's "[GUARD]" in the header, and before a row's "CAP 95%" tag
GUARD_RULE_OFFSET = 52          # the hover card's guard block: its rule and first line, from the last row's top
GUARD_TEXT_OFFSET = 58
GUARD_LINE_H = 12
GUARD_BLOCK_PAD = 12            # what the block adds to the card besides its lines: the gap round its rule
VALUE_CLEARANCE = 8             # least space between the name line and the value
VALUE_CHIP_PAD = 6.0            # a critical value sits in a chip 3 px wider than the digits on each side
ELLIPSIS = "\u2026"

STRIP_W = 264
STRIP_H = 34
STRIP_MIN_SCALE = 0.5           # the strip is drawn no smaller than this when the taskbar is shorter than it
STRIP_CHAMFER = 8.0
STRIP_DOT_X = 10
STRIP_DOT_Y = 14
STRIP_LABEL_TOP = 10
STRIP_BAR_Y = 13
STRIP_BAR_H = 8
STRIP_CELLS = 10
STRIP_CELL_W = 4
STRIP_CELL_STEP = 6
STRIP_VALUE_TOP = 7
STRIP_VALUE_H = 20
STRIP_GROUPS = (
    # label x, bar x, value right edge, window label
    (22.0, 44.0, 126.0, "5H"),
    (144.0, 166.0, 256.0, "7D"),
)
STRIP_DIVIDER_X = 134
STRIP_HOLD_BOX = (129, 10, 140, 25)         # the hold chip that takes the divider's place while the usage guard holds
STRIP_HOLD_GLYPH = (                        # an hourglass: waiting for the reset
    "#######",
    ".#####.",
    "..###..",
    "...#...",
    "..###..",
    ".#####.",
    "#######",
)

# The strip has no room for a status word, so the mark beside "5H" tells the states apart: the blinking dot while
# live, the pause bars, a hollow ring for last known (stale, no login, loading), and a pixel glyph each for no
# connection and a throttled API. One logical px per "#", centred on the 6 px mark (the glyphs may be a little
# wider than the dot: there are 8 px between the plate's edge and the "5H").
STRIP_GLYPHS: dict[str, tuple[str, ...]] = {
    LAMP_OFFLINE: (                 # a cross: NO CARRIER
        "#......#",
        ".#....#.",
        "..#..#..",
        "...##...",
        "...##...",
        "..#..#..",
        ".#....#.",
        "#......#",
    ),
    LAMP_LIMITED: (                 # three dots: THROTTLED, waiting to try again
        "##.##.##",
        "##.##.##",
    ),
}

# Font keys used by the drawing code. Each maps to a theme role and weight, and to the bundled
# Nostromo file that is used when the theme cannot supply one.
FONT_SEMI = "ibmplexmono/IBMPlexMono-SemiBold.ttf"    # theme body, weight 600
FONT_REG = "ibmplexmono/IBMPlexMono-Regular.ttf"      # theme body, weight 400
FONT_VT = "vt323/VT323-Regular.ttf"                   # theme mono (the readouts)
_FACE_ROLES = {
    FONT_SEMI: ("body", "semibold"),
    FONT_REG: ("body", "regular"),
    FONT_VT: ("mono", "regular"),
}
_FACES: dict[str, dict[str, Path]] = {}
LABEL_SIZE = 10.0
VALUE_SIZE = 22.0
STRIP_LABEL_SIZE = 11.0
STRIP_VALUE_SIZE = 18.0
LABEL_TRACKING = 1.2            # 0.12 em
QUALIFIER_TRACKING = 0.6        # 0.06 em
MESSAGE_TRACKING = 0.8          # 0.08 em
STRIP_LABEL_TRACKING = 1.1      # 0.10 em

# Glow radii are CSS blur radii; Canvas.glow takes the Gaussian sigma (blur / 2).
GLOW_PIP_SIGMA = 1.5            # box-shadow 3 px, glow-soft
GLOW_PIP_CRIT_SIGMA = 2.0       # box-shadow 4 px, glow (critical cells)
GLOW_VALUE_SMALL_SIGMA = 1.0    # text-shadow 2 px, glow
GLOW_VALUE_LARGE_SIGMA = 3.5    # text-shadow 7 px, glow-soft
GLOW_DOT_SIGMA = 2.0            # box-shadow 4 px, glow
GLOW_STRIP_CELL_SIGMA = 1.0     # box-shadow 2 px, glow-soft
GLOW_STRIP_VALUE_SMALL_SIGMA = 1.0
GLOW_STRIP_VALUE_LARGE_SIGMA = 2.5

DASH_ON = 3
DASH_OFF = 3

STATE_WORDS: dict[str, str] = {
    "normal": "NOMINAL",
    "warning": "ADVISORY",
    "critical": "CRITICAL",
    "stale": "LAST KNOWN",
    "offline": "NO CARRIER",
    "paused": "HOLD",
    "loading": "ACQUIRING",
    "rate_limited": "THROTTLED",
    "no_credentials": "AUTH REQUIRED",
}

# The auth box wording the approved mockup gives (final.html, "stale3": two lines). The view model's
# stale_message stays the neutral default for every status not listed here (a missing login has no
# mockup wording yet, so it keeps the neutral text).
STALE_MESSAGES: dict[ApiStatus, str] = {
    ApiStatus.STALE: "AUTH LINK LOST.\nOPEN CLAUDE CODE TO RESTORE.",
}


# ── Palette ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class Palette:
    """The tokens one frame needs, resolved from a variant.

    Mapping from the theme JSON: crt = bg, plate = surface, hot = text_bright, bright = accent,
    main = text, label = text_dim, stale = stale, rule = border_dim, edge = border, glow = glow,
    ghost = track. The rest come from the variant's extras (extras.variant_tokens).
    """
    crt: Color
    plate: Color
    hot: Color
    bright: Color
    main: Color
    label: Color
    stale: Color
    ghost: Color
    ink: Color
    edge: Color
    edge_hi: Color
    rule: Color
    rule_strong: Color
    glow: Color
    glow_soft: Color
    tray_track: Color
    message: Color                      # the auth box text: label tone, also in stale mode

    @classmethod
    def for_variant(cls, theme: Any, variant: Any) -> Palette:
        colors = _mapping(getattr(variant, "colors", None))
        tokens = {**_mapping(_theme_token_table(theme, variant)), **_mapping(getattr(variant, "extras", None))}

        def pick(source: Mapping[str, Any], key: str, default: Color | None = None) -> Color:
            value = source.get(key)
            if value is None:
                if default is None:
                    raise KeyError(f"variant {getattr(variant, 'id', '?')!r} has no colour {key!r}")
                return default
            return parse_color(value) if isinstance(value, str) else tuple(value)  # type: ignore[return-value]

        crt = pick(colors, "bg")
        hot = pick(colors, "text_bright")
        bright = pick(colors, "accent")
        main = pick(colors, "text")
        glow = pick(colors, "glow")
        rule = pick(colors, "border_dim", with_alpha(main, 0x40))
        return cls(
            crt=crt,
            plate=pick(colors, "surface", crt),
            hot=hot,
            bright=bright,
            main=main,
            label=pick(colors, "text_dim", main),
            stale=pick(colors, "stale", main),
            ghost=pick(tokens, "ghost", pick(colors, "track", crt)),
            ink=pick(tokens, "ink", crt),
            edge=pick(colors, "border", rule),
            edge_hi=pick(tokens, "edge_hi", with_alpha(hot, 0x33)),
            rule=rule,
            rule_strong=pick(tokens, "rule_strong", with_alpha(main, 0x85)),
            glow=glow,
            glow_soft=pick(tokens, "glow_soft", with_alpha(glow, 0x38)),
            tray_track=pick(tokens, "tray_track", with_alpha(main, 0x85)),
            message=pick(colors, "text_dim", main),
        )

    def stale_tones(self) -> Palette:
        """Every tone drops to the stale colour and the halos go, as in the approved stale state."""
        clear = TRANSPARENT
        return replace(self, main=self.stale, bright=self.stale, hot=self.stale, label=self.stale,
                       glow=clear, glow_soft=clear)  # message keeps its label tone


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _theme_token_table(theme: Any, variant: Any) -> Mapping[str, Any]:
    extras = _mapping(getattr(theme, "extras", None))
    table = _mapping(extras.get("variant_tokens"))
    return _mapping(table.get(getattr(variant, "id", None)))


def _faces(theme: Any) -> dict[str, Path]:
    """The face file for each font key: the theme's own role, else the bundled Nostromo file."""
    key = str(getattr(theme, "id", "nostromo"))
    if key not in _FACES:
        resolved: dict[str, Path] = {}
        for font_key, (role, weight) in _FACE_ROLES.items():
            path = None
            try:
                path = theme_registry.font_path(theme, role, weight)
            except (AttributeError, KeyError, TypeError):   # an object that is not a theme
                path = None
            resolved[font_key] = path if path is not None else engine.font_file(font_key)
        _FACES[key] = resolved
    return _FACES[key]


def _canvas(width: float, height: float, scale: float, theme: Any) -> Canvas:
    cv = Canvas(width, height, scale)
    cv.faces = _faces(theme)
    return cv


def _palette(theme: Any, variant: Any, model: WidgetModel) -> Palette:
    palette = Palette.for_variant(theme, variant)
    return palette.stale_tones() if model.stale else palette


# ── Shared model reading ──────────────────────────────────────────────────────

def _visible_rows(model: WidgetModel) -> list[Row]:
    """The widget's rows. A message box takes the third slot, so only the first two rows show."""
    rows = list(model.rows)
    return rows[:2] if model.stale_message else rows


def _frame_height(model: WidgetModel) -> int:
    """94 px for two rows, 126 for three (the stale message box takes the third slot)."""
    rows = max(2, len(_visible_rows(model)))
    if model.stale_message:
        rows = max(rows, 3)
    return FLOAT_BASE_H + ROW_PITCH * (rows - 1)


def _emphasis(model: WidgetModel, row: Row) -> str:
    """The severity a row is drawn with. Stale values are drawn flat, so no emphasis there."""
    return "normal" if model.stale else row.severity


def _row_emphasis(model: WidgetModel, row: Row) -> str:
    """_emphasis, raised to critical for a row the usage guard holds on, whatever its severity: a cap set at 60 %
    is as final to the user as the API's critical, and the value then reads as the state it is."""
    if _over_cap(model, row):
        return SEVERITY_CRITICAL
    return _emphasis(model, row)


def _percent_text(percent: float | None) -> str:
    return "--" if percent is None else f"{int(percent + 0.5) if percent >= 0 else 0}%"


def _exact_percent_text(percent: float | None) -> str:
    if percent is None:
        return "--"
    text = f"{percent:.1f}"
    if text.endswith(".0"):
        text = text[:-2]
    return f"{text}%"


def _qualifier(row: Row) -> str | None:
    if row.key == "session":
        return "5H"
    if row.key == "weekly":
        return "7D"
    return f"OF {row.share_text}" if row.share_text else None


def _short_qualifier(row: Row) -> str | None:
    """The qualifier when the full one does not fit: a scoped limit's share on its own ("25%")."""
    if row.key in ("session", "weekly") or not row.share_text:
        return _qualifier(row)
    return row.share_text.split()[0]


def _name_line(cv: Canvas, row: Row, value: str, semi: Any, reg: Any, vt: Any,
               chip: float = 0.0) -> tuple[str, str | None]:
    """The row's name and qualifier, cut so the line ends VALUE_CLEARANCE px before the value (and before
    its chip, which is ``chip`` px wider than the digits, when a critical row draws one).

    A scoped limit is named for its model ("CLAUDE SONNET 4.5 WEEKLY") and qualified "OF 25% SHARE", which
    together can be wider than the room the value leaves. They give way in the order Night City's rows do:
    the qualifier shrinks to the share ("25%"), then the name loses its WEEKLY, then the name is cut with an
    ellipsis, and only when even that cannot fit does the qualifier go. The value never moves.
    """
    room = RIGHT - cv.advance(value, vt) - chip - VALUE_CLEARANCE - LEFT

    def fits(name: str, qualifier: str | None) -> bool:
        used = cv.advance(name, semi, LABEL_TRACKING)
        if qualifier:
            used += QUALIFIER_GAP + cv.advance(qualifier, reg, QUALIFIER_TRACKING)
        return used <= room

    qualifier, short = _qualifier(row), _short_qualifier(row)
    shorter = row.label.removesuffix(" WEEKLY").strip() or row.label
    for name, kept in ((row.label, qualifier), (row.label, short), (shorter, short)):
        if fits(name, kept):
            return name, kept
    cut = shorter
    while len(cut) > 1 and not fits(cut.rstrip() + ELLIPSIS, short):
        cut = cut[:-1]
    cut = cut.rstrip() + ELLIPSIS
    return (cut, short) if fits(cut, short) else (cut, None)


def _age_text(model: WidgetModel) -> str | None:
    """The age after the status pill, shown while the values are live or last-known."""
    if model.age_text is None or model.status not in (ApiStatus.LIVE, ApiStatus.STALE, ApiStatus.RATE_LIMITED):
        return None
    return model.age_text.removesuffix(" AGO")


def _hold_word(model: WidgetModel) -> str | None:
    """The header's chip while the usage guard (#27) holds: "HOLD 2H 14M", or "HOLD" when no reset time is known.
    None when the guard is off or only armed. The countdown is always the time left ("HOLD 2D 3H"): the clock time of
    a resume that far away is the tooltip's."""
    guard = model.guard
    if guard is None or not guard.holding:
        return None
    return f"HOLD {guard.resume_in}" if guard.resume_in else "HOLD"


def _over_cap(model: WidgetModel, row: Row) -> bool:
    """Whether the guard holds because of this row: it is at or past the cap the guard watches for it, and the numbers
    are live to say so (a stale frame is drawn flat, with no severity marks, as the other skins' are)."""
    guard = model.guard
    return (guard is not None and guard.holding and not model.stale and row.key in guard.over_keys
            and row.percent is not None)


def _cap_text(model: WidgetModel, row: Row) -> str | None:
    """"CAP 95%" for a row that is over its cap, else None."""
    guard = model.guard
    cap = guard.caps.get(row.key) if guard is not None and _over_cap(model, row) else None
    return None if cap is None else f"CAP {_number(cap)}%"


def _number(value: float) -> str:
    """A cap as people write it: 95, not 95.0."""
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _state_word(model: WidgetModel) -> tuple[str, str]:
    """(word, style) for the header's right-hand word. style is "label", "bright" or "chip". While the guard holds
    the word is the hold chip: a held account is the state that matters, and the status pill beside it says the rest."""
    hold = _hold_word(model)
    if hold is not None:
        return hold, "chip"
    status = model.status
    if status is ApiStatus.LIVE:
        worst = worst_severity(model.rows) if not model.stale else "normal"
        if worst == SEVERITY_CRITICAL:
            return STATE_WORDS["critical"], "chip"
        if worst == SEVERITY_WARNING:
            return STATE_WORDS["warning"], "bright"
        return STATE_WORDS["normal"], "label"
    keys = {
        ApiStatus.STALE: "stale",
        ApiStatus.OFFLINE: "offline",
        ApiStatus.PAUSED: "paused",
        ApiStatus.LOADING: "loading",
        ApiStatus.RATE_LIMITED: "rate_limited",
        ApiStatus.NO_CREDENTIALS: "no_credentials",
    }
    return STATE_WORDS[keys[status]], "label"


# ── Painting plan: halos first, then marks ────────────────────────────────────

class _Plan:
    """Collects the halos and crisp marks of one frame. run() draws all halos, then all marks."""

    def __init__(self, text_fx: bool) -> None:
        self.text_fx = text_fx
        self._halos: list[tuple[Layer, float, Color, float]] = []
        self._marks: list[Callable[[Canvas], None]] = []

    def halo(self, layer: Layer | None, sigma: float, color: Color, strength: float = 1.0) -> None:
        if self.text_fx and layer is not None and color[3] > 0 and strength > 0:
            self._halos.append((layer, sigma, color, strength))

    def mark(self, draw: Callable[[Canvas], None]) -> None:
        self._marks.append(draw)

    def stamp(self, layer: Layer | None, color: Color) -> None:
        if layer is not None and color[3] > 0:
            self._marks.append(lambda cv: cv.stamp(layer, color))

    def run(self, cv: Canvas) -> None:
        for layer, sigma, color, strength in self._halos:
            cv.glow(layer, sigma, color, strength)
        for draw in self._marks:
            draw(cv)


# ── Shared drawing pieces ─────────────────────────────────────────────────────

def _paint_frame(cv: Canvas, pal: Palette, outer: list, body: list, height: float,
                 opts: RenderOptions) -> None:
    """The 1 px edge, and the plate gradient inside it unless transparent_bg leaves the plate out."""
    if opts.transparent_bg:
        cv.fill_ring(outer, body, pal.edge)
        return
    cv.fill_polygon(outer, pal.edge)
    cv.gradient_fill(body, pal.plate, pal.crt, stop=0.4, y_top=1, y_bottom=height - 1)


def _pixel_glyph(plan: _Plan, cv: Canvas, rows: tuple[str, ...], cx: float, cy: float, colour: Color) -> None:
    """A bitmap glyph, one logical px per "#", centred on (cx, cy) and snapped to whole logical px: one layer."""
    width = max(len(row) for row in rows)
    left, top = round(cx - width / 2), round(cy - len(rows) / 2)
    rects = [(left + i, top + j, left + i + 1, top + j + 1)
             for j, row in enumerate(rows) for i, mark in enumerate(row) if mark == "#"]
    plan.stamp(cv.rects_layer(rects), colour)


def _alive_mark(plan: _Plan, cv: Canvas, pal: Palette, model: WidgetModel, opts: RenderOptions,
                x: float, y: float, size: float = 6.0, glyphs: bool = False) -> None:
    """The status mark: a blinking dot when live, a hollow ring when stale, a pause glyph when paused. With
    ``glyphs`` (the strip, which has no room for a word) no connection and a throttled API get a glyph of their own."""
    if model.lamp == LAMP_PAUSED:
        bar = size / 3
        plan.stamp(cv.rect_layer(x, y, x + bar, y + size), pal.label)
        plan.stamp(cv.rect_layer(x + 2 * bar, y, x + 3 * bar, y + size), pal.label)
        return
    glyph = STRIP_GLYPHS.get(model.lamp) if glyphs else None
    if glyph is not None:
        _pixel_glyph(plan, cv, glyph, x + size / 2, y + size / 2, pal.stale)
        return
    if model.stale:
        plan.stamp(cv.ellipse_layer(x, y, x + size, y + size, ring=1.0), pal.stale)
        return
    lit = opts.reduce_motion or model.blink_on
    alpha = 255 if lit else 46                    # the approved blink drops to 18 % opacity
    layer = cv.ellipse_layer(x, y, x + size, y + size)
    plan.halo(layer, GLOW_DOT_SIGMA, pal.glow, alpha / 255)
    plan.stamp(layer, with_alpha(pal.bright, alpha))


def _pill(plan: _Plan, cv: Canvas, pal: Palette, model: WidgetModel, x: float, y: float) -> float:
    """Status pill (1 px outline, 12 px tall) and the age after it. Returns the age's right edge."""
    semi = cv.font(FONT_SEMI, LABEL_SIZE)
    reg = cv.font(FONT_REG, LABEL_SIZE)
    text = model.status_text
    width = 2 + 4 + cv.advance(text, semi, LABEL_TRACKING) + 4
    plan.mark(lambda c: c.outline_rect(x, y, x + width, y + HEADER_H, pal.rule_strong, 1))
    text_layer, _ = cv.text_layer(text, x + 5, cv.baseline_in_box(y + 1, 10, semi), semi, LABEL_TRACKING)
    plan.stamp(text_layer, pal.bright)
    right = x + width
    age = _age_text(model)
    if age:
        age_layer, advance = cv.text_layer(age, right + 6, cv.baseline_in_box(y + 1, 10, reg), reg,
                                           QUALIFIER_TRACKING)
        plan.stamp(age_layer, pal.label)
        right = right + 6 + advance
    return right


def _header(plan: _Plan, cv: Canvas, pal: Palette, model: WidgetModel, opts: RenderOptions,
            dot_x: float, dot_y: float, top: float, right: float, dot_size: float = 6.0) -> None:
    """Status mark, pill and age on the left, severity word on the right. The usage guard adds nothing to the
    left while it holds (its chip takes the right-hand word, see _state_word) and a quiet [GUARD] after the age
    while it is armed."""
    _alive_mark(plan, cv, pal, model, opts, dot_x, dot_y, dot_size)
    left_end = _pill(plan, cv, pal, model, dot_x + dot_size + 6, top)
    semi = cv.font(FONT_SEMI, LABEL_SIZE)
    word, style = _state_word(model)
    baseline = cv.baseline_in_box(top, HEADER_H, semi)
    word_w = cv.advance(word, semi, LABEL_TRACKING)
    if style == "chip":
        chip_w = word_w + 8
        plan.mark(lambda c: c.fill_rect(right - chip_w, top, right, top + HEADER_H, pal.bright))
        layer, _ = cv.text_layer(word, right - 4, baseline, semi, LABEL_TRACKING, align="right")
        plan.stamp(layer, pal.ink)
    else:
        layer, _ = cv.text_layer(word, right, baseline, semi, LABEL_TRACKING, align="right")
        plan.stamp(layer, pal.bright if style == "bright" else pal.label)
    if model.guard is not None and not model.guard.holding:
        _armed_mark(plan, cv, pal, left_end, right - word_w - (8 if style == "chip" else 0), top)


def _armed_mark(plan: _Plan, cv: Canvas, pal: Palette, left_end: float, word_left: float, top: float) -> None:
    """The armed guard: "[GUARD]" in the label tone after the age, in the free stretch before the state word. A
    long status ("RATE LIMITED" and its age) leaves room for the short "[G]" only, and a header with no room at
    all draws nothing: the mark is quiet by design."""
    semi = cv.font(FONT_SEMI, LABEL_SIZE)
    for text in ("[GUARD]", "[G]"):
        x = left_end + ARMED_GAP
        if x + cv.advance(text, semi, LABEL_TRACKING) <= word_left - ARMED_GAP:
            layer, _ = cv.text_layer(text, x, cv.baseline_in_box(top + 1, 10, semi), semi, LABEL_TRACKING)
            plan.stamp(layer, pal.label)
            return


def _dashed_box(plan: _Plan, cv: Canvas, pal: Palette, x0: float, y0: float, x1: float, y1: float) -> None:
    """Crt-filled box with a 1 px dashed border in the label colour."""
    def draw(c: Canvas) -> None:
        c.fill_rect(x0, y0, x1, y1, pal.crt)
        c.dotted_hline(x0, x1, y0, DASH_ON, DASH_OFF, pal.message)
        c.dotted_hline(x0, x1, y1 - 1, DASH_ON, DASH_OFF, pal.message)
        y = y0
        while y < y1:
            c.fill_rect(x0, y, x0 + 1, min(y + DASH_ON, y1), pal.message)
            c.fill_rect(x1 - 1, y, x1, min(y + DASH_ON, y1), pal.message)
            y += DASH_ON + DASH_OFF
    plan.mark(draw)


def _message_text(model: WidgetModel) -> str | None:
    """The wording of the dashed auth box: this skin's own for the statuses it words (the approved
    mockup's), else the neutral text the view model carries. None when the model asks for no box."""
    if not model.stale_message:
        return None
    return STALE_MESSAGES.get(model.status, model.stale_message)


def _message_lines(text: str) -> list[str]:
    """One or two lines: a line feed splits them, and so does the view model's "A - B" spelling."""
    if "\n" in text:
        return [part.strip() for part in text.split("\n")]
    return [part.strip() for part in text.split(" - ")] if " - " in text else [text]


def _message(plan: _Plan, cv: Canvas, pal: Palette, text: str, top: float, x0: float, x1: float, y1: float) -> None:
    """Dashed auth box with one or two lines of 10 px SemiBold text (see _message_lines)."""
    _dashed_box(plan, cv, pal, x0, top, x1, y1)
    lines = _message_lines(text)
    semi = cv.font(FONT_SEMI, LABEL_SIZE)
    for index, line in enumerate(lines[:2]):
        line_top = top + 2 + 12 * index
        layer, _ = cv.text_layer(line, x0 + 9, cv.baseline_in_box(line_top, 12, semi), semi, MESSAGE_TRACKING)
        plan.stamp(layer, pal.message)


def _cell_bar(plan: _Plan, cv: Canvas, pal: Palette, x: float, y: float, step: float, cell_w: float,
              cell_h: float, cells: int, full: int, partial_w: float, lit: Color) -> None:
    """A segmented bar: unlit ghosts, then the full cells in ``lit`` (the emphasis colour), then the
    partly lit cell in --main. The approved CSS recolours only the full cells (.on) for a warning or
    a critical limit; a partial cell (.part) keeps --main. Two composites per bar, three when a
    cell is part lit."""
    ghosts, lit_layer = cv.segment_layers(x, y, step, cell_w, cell_h, cells, full)
    plan.stamp(ghosts, pal.ghost)
    plan.stamp(lit_layer, lit)
    if full < cells and partial_w > 0:
        start = x + step * full
        plan.stamp(cv.rects_layer([(start, y, start + partial_w, y + cell_h)]), pal.main)


def _lit_color(pal: Palette, emphasis: str) -> Color:
    if emphasis == SEVERITY_CRITICAL:
        return pal.hot
    if emphasis == SEVERITY_WARNING:
        return pal.bright
    return pal.main


# ── Floating widget ───────────────────────────────────────────────────────────

def _cap_tag(plan: _Plan, cv: Canvas, pal: Palette, model: WidgetModel, row: Row, name_end: float, value_left: float,
             top: float) -> None:
    """"CAP 95%" in a pill, after the name line of a row the guard holds on. It is drawn only where it fits between
    the name line and the value (VALUE_CLEARANCE off it): a long scoped name keeps its room, and the value chip and
    the header's hold chip carry the state."""
    text = _cap_text(model, row)
    if text is None:
        return
    semi = cv.font(FONT_SEMI, LABEL_SIZE)
    x = name_end + ARMED_GAP
    width = 2 + 4 + cv.advance(text, semi, LABEL_TRACKING) + 4
    if x + width > value_left - VALUE_CLEARANCE:
        return
    plan.mark(lambda c: c.outline_rect(x, top, x + width, top + HEADER_H, pal.bright, 1))
    layer, _ = cv.text_layer(text, x + 5, cv.baseline_in_box(top + 1, 10, semi), semi, LABEL_TRACKING)
    plan.stamp(layer, pal.bright)


def _row_widget(plan: _Plan, cv: Canvas, pal: Palette, model: WidgetModel, row: Row, index: int) -> None:
    top = ROW_TOP + ROW_PITCH * index
    emphasis = _row_emphasis(model, row)
    semi = cv.font(FONT_SEMI, LABEL_SIZE)
    reg = cv.font(FONT_REG, LABEL_SIZE)
    vt = cv.font(FONT_VT, VALUE_SIZE)

    # Name and qualifier share one 12 px line box, and end before the value.
    chipped = emphasis == SEVERITY_CRITICAL and row.percent is not None
    label, qualifier = _name_line(cv, row, _percent_text(row.percent), semi, reg, vt, VALUE_CHIP_PAD if chipped else 0.0)
    name_layer, name_w = cv.text_layer(label, LEFT, cv.baseline_in_box(top, 12, semi), semi, LABEL_TRACKING)
    plan.stamp(name_layer, pal.main)
    name_end = LEFT + name_w
    if qualifier:
        qual_layer, qual_w = cv.text_layer(qualifier, LEFT + name_w + QUALIFIER_GAP, cv.baseline_in_box(top, 12, reg),
                                           reg, QUALIFIER_TRACKING)
        plan.stamp(qual_layer, pal.label)
        name_end += QUALIFIER_GAP + qual_w
    value_left = RIGHT - cv.advance(_percent_text(row.percent), vt) - (VALUE_CHIP_PAD if chipped else 0.0)
    _cap_tag(plan, cv, pal, model, row, name_end, value_left, top)

    # Value: 22 px VT323, right edge at 266, line box from top - 4.
    value = _percent_text(row.percent)
    value_baseline = cv.baseline_in_box(top - 4, 22, vt)
    if chipped:
        chip_w = cv.advance(value, vt) + VALUE_CHIP_PAD
        plan.mark(lambda c: c.fill_rect(RIGHT - chip_w, top - 4, RIGHT, top + 18, pal.bright))
        layer, _ = cv.text_layer(value, RIGHT - VALUE_CHIP_PAD / 2, value_baseline, vt, align="right")
        plan.stamp(layer, pal.ink)
    else:
        layer, _ = cv.text_layer(value, RIGHT, value_baseline, vt, align="right")
        value_color = pal.hot if emphasis == SEVERITY_WARNING else pal.bright
        plan.halo(layer, GLOW_VALUE_SMALL_SIGMA, pal.glow)
        plan.halo(layer, GLOW_VALUE_LARGE_SIGMA, pal.glow_soft)
        plan.stamp(layer, value_color)

    # Time rail: dotted track for the window, solid to the elapsed share, tick at "now".
    rail_y = top + RAIL_OFFSET
    track = pal.ghost
    plan.mark(lambda c: c.dotted_hline(LEFT, RIGHT, rail_y, 2, 2, track))
    if row.time_fraction is not None:
        end = LEFT + round(row.time_fraction * (RIGHT - LEFT))
        plan.mark(lambda c: c.fill_rect(LEFT, rail_y, end, rail_y + 1, pal.label))
        plan.mark(lambda c: c.fill_rect(end - 1, top + 15, end + 1, top + 20, pal.hot))

    # Pip bar: 16 cells of 12 px with 4 px gaps, lit by usage.
    full, fraction = engine.split_segments(row.percent, PIP_CELLS)
    partial_w = round(fraction * PIP_W)
    lit = _lit_color(pal, emphasis)
    lit_rects = [(LEFT + PIP_STEP * i, top + PIP_OFFSET, LEFT + PIP_STEP * i + PIP_W, top + PIP_OFFSET + 8)
                 for i in range(full)]
    if lit_rects and emphasis != SEVERITY_CRITICAL:
        plan.halo(cv.rects_layer(lit_rects), GLOW_PIP_SIGMA, pal.glow_soft)
    elif lit_rects:
        plan.halo(cv.rects_layer(lit_rects), GLOW_PIP_CRIT_SIGMA, pal.glow)
    _cell_bar(plan, cv, pal, LEFT, top + PIP_OFFSET, PIP_STEP, PIP_W, 8, PIP_CELLS, full, partial_w, lit)


class NostromoSkin:
    """The Nostromo renderer for all four surfaces."""

    theme_id = "nostromo"

    def floating_size(self, theme: Any, model: WidgetModel, scale: float) -> tuple[int, int]:
        return round(FLOAT_W * scale), round(_frame_height(model) * scale)

    def render_floating(self, model: WidgetModel, theme: Any, variant: Any, scale: float = 1.0,
                        opts: RenderOptions | Mapping[str, Any] | None = None) -> Image.Image:
        o = coerce_options(opts)
        pal = _palette(theme, variant, model)
        height = _frame_height(model)
        cv = _canvas(FLOAT_W, height, scale, theme)
        outer = engine.chamfer_polygon(0, 0, FLOAT_W, height, CHAMFER, CHAMFER)
        body = engine.inset_chamfer(0, 0, FLOAT_W, height, CHAMFER, CHAMFER, 1)
        _paint_frame(cv, pal, outer, body, height, o)
        cv.fill_rect(10, 1, FLOAT_W - 10, 2, pal.edge_hi)               # top hairline

        plan = _Plan(o.text_fx)
        _header(plan, cv, pal, model, o, dot_x=LEFT, dot_y=9, top=HEADER_TOP, right=RIGHT)
        plan.mark(lambda c: c.fill_rect(LEFT, RULE_Y, RIGHT, RULE_Y + 1, pal.rule))
        rows = _visible_rows(model)
        for index, row in enumerate(rows):
            _row_widget(plan, cv, pal, model, row, index)
        message = _message_text(model)
        if message:
            _message(plan, cv, pal, message, MESSAGE_TOP, LEFT, RIGHT, MESSAGE_TOP + MESSAGE_H)
        plan.run(cv)

        if o.text_fx:
            cv.scanlines(1, height - 1, SCAN_STRENGTH, clip=body)
        if not model.stale and worst_severity(rows) == SEVERITY_CRITICAL:
            cv.hazard(outer, 0, FLOAT_W, 0, HAZARD_H, HAZARD_PERIOD, pal.bright, pal.crt)
        return cv.finish()

    def render_tooltip(self, model: WidgetModel, theme: Any, variant: Any, scale: float = 1.0) -> Image.Image:
        pal = _palette(theme, variant, model)
        opts = RenderOptions()
        rows = list(model.rows)
        last_top = ROW_TOP + TOOLTIP_ROW_PITCH * (len(rows) - 1)
        lead, guard_lines = _guard_lines(_canvas(1, 1, scale, theme), model)    # measured at this scale, drawn below
        guard_h = GUARD_BLOCK_PAD + GUARD_LINE_H * len(guard_lines) if guard_lines else 0
        height = int(last_top + 56 + guard_h) if not model.stale_message else int(last_top + 92 + guard_h)
        cv = _canvas(FLOAT_W, height, scale, theme)
        outer = engine.chamfer_polygon(0, 0, FLOAT_W, height, CHAMFER, CHAMFER)
        body = engine.inset_chamfer(0, 0, FLOAT_W, height, CHAMFER, CHAMFER, 1)
        _paint_frame(cv, pal, outer, body, height, opts)
        cv.fill_rect(10, 1, FLOAT_W - 10, 2, pal.edge_hi)

        plan = _Plan(True)
        _header(plan, cv, pal, model, opts, dot_x=LEFT, dot_y=9, top=HEADER_TOP, right=RIGHT)
        plan.mark(lambda c: c.fill_rect(LEFT, RULE_Y, RIGHT, RULE_Y + 1, pal.rule))
        for index, row in enumerate(rows):
            _row_tooltip(plan, cv, pal, model, row, index)
        if guard_lines:
            _guard_block(plan, cv, pal, lead, guard_lines, last_top)
        message = _message_text(model)
        if message:
            top = last_top + TOOLTIP_INFO_OFFSET + 20 + guard_h
            _message(plan, cv, pal, message, top, LEFT, RIGHT, top + MESSAGE_H)
        plan.run(cv)
        cv.scanlines(1, height - 1, SCAN_STRENGTH, clip=body)
        if not model.stale and worst_severity(rows) == SEVERITY_CRITICAL:
            cv.hazard(outer, 0, FLOAT_W, 0, HAZARD_H, HAZARD_PERIOD, pal.bright, pal.crt)
        return cv.finish()

    def render_taskbar(self, model: WidgetModel, theme: Any, variant: Any, height_px: int, scale: float = 1.0,
                       opts: RenderOptions | Mapping[str, Any] | None = None) -> Image.Image:
        o = coerce_options(opts)
        pal = _palette(theme, variant, model)
        if round(STRIP_H * scale) > height_px:      # a bar shorter than the strip: draw it smaller, not overhanging
            scale = max(height_px / STRIP_H, STRIP_MIN_SCALE)
        strip = _render_strip(model, pal, scale, o, theme)
        height = max(int(height_px), strip.height)
        out = Image.new("RGBA", (strip.width, height), TRANSPARENT)
        out.alpha_composite(strip, (0, (height - strip.height) // 2))
        return out

    def render_tray_icon(self, model: WidgetModel, theme: Any, variant: Any, size_px: int) -> Image.Image:
        size = int(size_px)
        if size < 8:
            raise ValueError(f"tray icon size must be at least 8 px, not {size}")
        pal = _palette(theme, variant, model)
        row = model.rows[0] if model.rows else None
        percent = row.percent if row is not None else None
        stale = model.stale or percent is None
        if stale:
            lit = pal.stale
        else:
            lit = _lit_color(pal, row.severity if row is not None else "normal")
        return _render_tray(size, percent, stale, lit, pal, theme, model.lamp)

    def content_insets(self, surface: str, theme: Any, model: WidgetModel, scale: float) -> Insets:
        """No transparent margin: every Nostromo frame is its visible surface (the chamfers cut corners, not the box)."""
        return NO_INSETS

    def hover_frames(self, frame: Image.Image, surface: str, scale: float,
                     opts: RenderOptions | Mapping[str, Any] | None = None) -> HoverPlay | None:
        """The pointer response of the floating widget and the strip: the phosphor is driven harder while the pointer
        is over it, one frame that holds until it leaves (see the motion spec, desktop widget hover)."""
        if surface not in (SURFACE_FLOATING, SURFACE_TASKBAR):
            return None
        return HoverPlay(frames=(_brighten(frame, scale),), delays_ms=(0,), hold=True)


# ── Hover ─────────────────────────────────────────────────────────────────────

HOVER_GAIN = 1.22               # the phosphor is 22 % brighter ...
HOVER_BLOOM = 0.30              # ... and a blurred copy of it adds a faint bloom at 30 %
HOVER_BLOOM_SIGMA = 2.0         # logical px
_HOVER_LUT = [min(255, int(v * HOVER_GAIN + 0.5)) for v in range(256)]
_BLOOM_LUT = [int(v * HOVER_BLOOM + 0.5) for v in range(256)]


def _brighten(frame: Image.Image, scale: float) -> Image.Image:
    """``frame`` with its colour channels gained and a faint bloom added. Alpha is untouched, so the chamfered corners
    and the window's shape stay as they were. The bloom is blurred at half size: the same picture for a quarter of
    the work, which keeps the one-off cost of the first hover to a few milliseconds."""
    red, green, blue, alpha = frame.convert("RGBA").split()
    lit = Image.merge("RGB", (red.point(_HOVER_LUT), green.point(_HOVER_LUT), blue.point(_HOVER_LUT)))
    sigma = max(0.5, HOVER_BLOOM_SIGMA * scale / 2)
    bloom = lit.reduce(2).filter(ImageFilter.GaussianBlur(sigma)).resize(frame.size, Image.Resampling.BILINEAR)
    bloom = bloom.point(_BLOOM_LUT * 3)
    r, g, b = ImageChops.add(lit, bloom).split()
    return Image.merge("RGBA", (r, g, b, alpha))


# ── Tooltip rows ──────────────────────────────────────────────────────────────

def _row_tooltip(plan: _Plan, cv: Canvas, pal: Palette, model: WidgetModel, row: Row, index: int) -> None:
    """A widget row plus a reset-and-pace line: exact percentage, local reset time, pace in points."""
    top = ROW_TOP + TOOLTIP_ROW_PITCH * index
    emphasis = _row_emphasis(model, row)
    semi = cv.font(FONT_SEMI, LABEL_SIZE)
    reg = cv.font(FONT_REG, LABEL_SIZE)
    vt = cv.font(FONT_VT, VALUE_SIZE)

    label, qualifier = _name_line(cv, row, _exact_percent_text(row.percent), semi, reg, vt)
    name_layer, name_w = cv.text_layer(label, LEFT, cv.baseline_in_box(top, 12, semi), semi, LABEL_TRACKING)
    plan.stamp(name_layer, pal.main)
    name_end = LEFT + name_w
    if qualifier:
        qual_layer, qual_w = cv.text_layer(qualifier, LEFT + name_w + QUALIFIER_GAP, cv.baseline_in_box(top, 12, reg),
                                           reg, QUALIFIER_TRACKING)
        plan.stamp(qual_layer, pal.label)
        name_end += QUALIFIER_GAP + qual_w
    _cap_tag(plan, cv, pal, model, row, name_end, RIGHT - cv.advance(_exact_percent_text(row.percent), vt), top)

    value_layer, _ = cv.text_layer(_exact_percent_text(row.percent), RIGHT, cv.baseline_in_box(top - 4, 22, vt),
                                   vt, align="right")
    plan.halo(value_layer, GLOW_VALUE_SMALL_SIGMA, pal.glow)
    plan.halo(value_layer, GLOW_VALUE_LARGE_SIGMA, pal.glow_soft)
    plan.stamp(value_layer, pal.hot if emphasis in (SEVERITY_WARNING, SEVERITY_CRITICAL) else pal.bright)

    rail_y = top + RAIL_OFFSET
    plan.mark(lambda c: c.dotted_hline(LEFT, RIGHT, rail_y, 2, 2, pal.ghost))
    if row.time_fraction is not None:
        end = LEFT + round(row.time_fraction * (RIGHT - LEFT))
        plan.mark(lambda c: c.fill_rect(LEFT, rail_y, end, rail_y + 1, pal.label))
        plan.mark(lambda c: c.fill_rect(end - 1, top + 15, end + 1, top + 20, pal.hot))
    full, fraction = engine.split_segments(row.percent, PIP_CELLS)
    partial_w = round(fraction * PIP_W)
    lit = _lit_color(pal, emphasis)
    _cell_bar(plan, cv, pal, LEFT, top + PIP_OFFSET, PIP_STEP, PIP_W, 8, PIP_CELLS, full, partial_w, lit)

    info_top = top + TOOLTIP_INFO_OFFSET
    reset = f"RESETS {row.resets_text.upper()}" if row.resets_text else "RESETS --"
    info_layer, _ = cv.text_layer(reset, LEFT, cv.baseline_in_box(info_top, 12, reg), reg, QUALIFIER_TRACKING)
    plan.stamp(info_layer, pal.label)
    pace_text, pace_color = _pace_text(pal, row)
    pace_layer, _ = cv.text_layer(pace_text, RIGHT, cv.baseline_in_box(info_top, 12, semi), semi,
                                  LABEL_TRACKING, align="right")
    plan.stamp(pace_layer, pace_color)


# ── Tooltip: the usage guard's line ───────────────────────────────────────────

def _guard_statement(model: WidgetModel) -> tuple[str, str] | None:
    """(lead, rest) of the hover card's guard line, in capitals, or None while the guard is off:
    ("GUARD ARMED:", "SESSION 95%, WEEKLY 90%") and
    ("GUARD HOLDING:", "WEEKLY 91% (CAP 90%). RESUMES FRI 7:12 PM (IN 2D 3H).")."""
    guard = model.guard
    if guard is None:
        return None
    if not guard.holding:
        caps = ", ".join(engine.glue(f"{label.upper()} {_number(cap)}%") for label, cap in guard.watched())
        return "GUARD ARMED:", caps
    limits = []
    for label, reading, cap in guard.over_limits():
        text = engine.glue(label.upper() if reading is None else f"{label.upper()} {_percent_text(reading)}")
        if cap is not None:
            text += " " + engine.glue(f"(CAP {_number(cap)}%)")
        limits.append(text)
    rest = ", ".join(limits)
    rest = f"{rest}." if rest else ""
    if guard.resume_clock:
        clock = engine.glue(guard.resume_clock.upper())
        countdown = engine.glue(f"(IN {guard.resume_in})")
        rest = f"{rest} RESUMES {clock} {countdown}."
    return "GUARD HOLDING:", rest.strip()


def _guard_lines(cv: Canvas, model: WidgetModel) -> tuple[int, list[str]]:
    """(characters of the lead, the guard's line broken to the card's width), measured on ``cv`` (any canvas of the
    frame's scale). No lines while the guard is off. A guard with nothing to list (no caps) is its lead alone,
    without the colon."""
    statement = _guard_statement(model)
    if statement is None:
        return 0, []
    lead, rest = statement
    if not rest:
        lead = lead.removesuffix(":")
    reg = cv.font(FONT_REG, LABEL_SIZE)
    text = f"{lead} {rest}" if rest else lead
    return len(lead), engine.wrap_words(text, lambda line: cv.advance(line, reg, QUALIFIER_TRACKING), RIGHT - LEFT)


def _guard_block(plan: _Plan, cv: Canvas, pal: Palette, lead: int, lines: list[str], last_top: float) -> None:
    """A rule under the last row and the guard's lines under it. The lead (the line's first ``lead`` characters) is
    semibold and bright, the rest of it plain."""
    plan.mark(lambda c: c.fill_rect(LEFT, last_top + GUARD_RULE_OFFSET, RIGHT, last_top + GUARD_RULE_OFFSET + 1, pal.rule))
    semi = cv.font(FONT_SEMI, LABEL_SIZE)
    reg = cv.font(FONT_REG, LABEL_SIZE)
    for index, line in enumerate(lines):
        top = last_top + GUARD_TEXT_OFFSET + GUARD_LINE_H * index
        head, tail = (line[:lead], line[lead:]) if index == 0 else ("", line)
        x = LEFT
        if head:
            layer, advance = cv.text_layer(head, x, cv.baseline_in_box(top, 12, semi), semi, QUALIFIER_TRACKING)
            plan.stamp(layer, pal.hot)
            x += advance
        if tail:
            layer, _ = cv.text_layer(tail, x, cv.baseline_in_box(top, 12, reg), reg, QUALIFIER_TRACKING)
            plan.stamp(layer, pal.main)


def _pace_text(pal: Palette, row: Row) -> tuple[str, Color]:
    delta = pace_delta(row.percent, row.time_fraction)
    if delta is None or row.pace is None:
        return "PACE --", pal.label
    if row.pace == "on_pace":
        return "ON PACE", pal.label
    sign = "+" if delta >= 0 else "-"
    word = "AHEAD" if row.pace == "ahead" else "BEHIND"
    return f"{word} {sign}{abs(delta):.0f} PTS", pal.bright if row.pace == "ahead" else pal.label


# ── Taskbar strip ─────────────────────────────────────────────────────────────

def _render_strip(model: WidgetModel, pal: Palette, scale: float, opts: RenderOptions, theme: Any) -> Image.Image:
    cv = _canvas(STRIP_W, STRIP_H, scale, theme)
    outer = engine.chamfer_polygon(0, 0, STRIP_W, STRIP_H, STRIP_CHAMFER, STRIP_CHAMFER)
    body = engine.inset_chamfer(0, 0, STRIP_W, STRIP_H, STRIP_CHAMFER, STRIP_CHAMFER, 1)
    _paint_frame(cv, pal, outer, body, STRIP_H, opts)
    # In the approved CSS the strip's scanlines sit under its content, so only the plate carries them.
    if opts.text_fx:
        cv.scanlines(1, STRIP_H - 1, SCAN_STRENGTH, clip=body)

    plan = _Plan(opts.text_fx)
    _alive_mark(plan, cv, pal, model, opts, STRIP_DOT_X, STRIP_DOT_Y, 6.0, glyphs=True)
    semi = cv.font(FONT_SEMI, STRIP_LABEL_SIZE)
    vt = cv.font(FONT_VT, STRIP_VALUE_SIZE)
    label_baseline = cv.baseline_in_box(STRIP_LABEL_TOP, 12, semi)
    value_baseline = cv.baseline_in_box(STRIP_VALUE_TOP, STRIP_VALUE_H, vt)
    rows = list(model.rows[:2])
    for index, (label_x, bar_x, value_right, window) in enumerate(STRIP_GROUPS):
        row = rows[index] if index < len(rows) else None
        percent = row.percent if row is not None else None
        emphasis = _row_emphasis(model, row) if row is not None else "normal"
        label_layer, _ = cv.text_layer(window, label_x, label_baseline, semi, STRIP_LABEL_TRACKING)
        plan.stamp(label_layer, pal.label)

        lit = _lit_color(pal, emphasis)
        full, fraction = engine.split_segments(percent, STRIP_CELLS)
        partial_w = round(fraction * STRIP_CELL_W)
        lit_rects = [(bar_x + STRIP_CELL_STEP * i, STRIP_BAR_Y, bar_x + STRIP_CELL_STEP * i + STRIP_CELL_W,
                      STRIP_BAR_Y + STRIP_BAR_H) for i in range(full)]
        plan.halo(cv.rects_layer(lit_rects), GLOW_STRIP_CELL_SIGMA, pal.glow_soft)
        _cell_bar(plan, cv, pal, bar_x, STRIP_BAR_Y, STRIP_CELL_STEP, STRIP_CELL_W, STRIP_BAR_H,
                  STRIP_CELLS, full, partial_w, lit)

        value = _percent_text(percent)
        if emphasis == SEVERITY_CRITICAL and percent is not None:
            chip_w = cv.advance(value, vt) + 4
            plan.mark(lambda c, r=value_right, w=chip_w: c.fill_rect(r - w, STRIP_VALUE_TOP, r,
                                                                    STRIP_VALUE_TOP + STRIP_VALUE_H, pal.bright))
            layer, _ = cv.text_layer(value, value_right - 2, value_baseline, vt, align="right")
            plan.stamp(layer, pal.ink)
        else:
            layer, _ = cv.text_layer(value, value_right, value_baseline, vt, align="right")
            plan.halo(layer, GLOW_STRIP_VALUE_SMALL_SIGMA, pal.glow)
            plan.halo(layer, GLOW_STRIP_VALUE_LARGE_SIGMA, pal.glow_soft)
            plan.stamp(layer, pal.hot)

    if model.guard is not None and model.guard.holding:
        # The guard holds: the divider's place, between the groups, is a chip with an hourglass in it. The strip has
        # no room for the countdown or for [GUARD] while armed, so the hover card says both.
        x0, y0, x1, y1 = STRIP_HOLD_BOX
        plan.mark(lambda c: c.fill_rect(x0, y0, x1, y1, pal.bright))
        _pixel_glyph(plan, cv, STRIP_HOLD_GLYPH, (x0 + x1) / 2, (y0 + y1) / 2, pal.ink)
    else:
        plan.mark(lambda c: c.fill_rect(STRIP_DIVIDER_X, 9, STRIP_DIVIDER_X + 1, 25, pal.rule_strong))
    plan.run(cv)
    return cv.finish()


# ── Tray icon ─────────────────────────────────────────────────────────────────

def _tray_geometry(size: int) -> tuple[float, float, float]:
    """(outer radius, thickness, chamfer) in px. 16, 24 and 32 are the approved sizes, and 20 is
    interpolated between 16 and 24."""
    s = max(16.0, min(32.0, float(size)))
    if s <= 24:
        t = (s - 16) / 8
        return 6.0 + 3.5 * t, 3.0 + 0.5 * t, 3.0 + t
    t = (s - 24) / 8
    return 9.5 + 4.5 * t, 3.5 + 0.5 * t, 4.0 + t


def _bars(width: int, gap: int, height: int) -> tuple[str, ...]:
    """Two bars ``width`` px wide, ``gap`` px apart and ``height`` px tall: the pause glyph."""
    return (("#" * width + "." * gap + "#" * width),) * height


def _cross(size: int, thickness: int = 1) -> tuple[str, ...]:
    """A ``size`` x ``size`` X whose diagonals are ``thickness`` px thick (an even size keeps it symmetric)."""
    return tuple(
        "".join("#" if abs(x - y) < thickness or abs(x - (size - 1 - y)) < thickness else "." for x in range(size))
        for y in range(size)
    )


def _dots(width: int, height: int, gap: int) -> tuple[str, ...]:
    """Three dots, ``width`` x ``height`` px each and ``gap`` px apart: an ellipsis."""
    row = ("#" * width + "." * gap) * 2 + "#" * width
    return (row,) * height


# The tray icon's centre glyphs, one px per "#", centred in the ring's hole. They replace the centre block (the number
# at 32 px) in the states that need a mark of their own: the pause bars while the numbers are live but not updating,
# a cross for no connection and three dots for a throttled API. A stale icon stays an empty dashed ring, so each of
# the three differs from it, and from each other, down to 16 px. Keyed by the icon size they are drawn at; a size
# between two keys uses the smaller one.
TRAY_GLYPHS: dict[int, dict[str, tuple[str, ...]]] = {
    16: {LAMP_PAUSED: _bars(1, 2, 4), LAMP_OFFLINE: _cross(4), LAMP_LIMITED: _dots(1, 2, 1)},
    20: {LAMP_PAUSED: _bars(2, 2, 6), LAMP_OFFLINE: _cross(6), LAMP_LIMITED: _dots(2, 2, 1)},
    24: {LAMP_PAUSED: _bars(3, 2, 8), LAMP_OFFLINE: _cross(8, 2), LAMP_LIMITED: _dots(2, 2, 2)},
    32: {LAMP_PAUSED: _bars(4, 4, 12), LAMP_OFFLINE: _cross(12, 2), LAMP_LIMITED: _dots(3, 3, 3)},
}
TRAY_ARC_CONTRAST = 3.2         # least contrast of a lit arc against the empty track (WCAG asks 3:1 of a graphic)


def _tray_glyph(kind: str, size: int) -> tuple[str, ...] | None:
    key = max((k for k in TRAY_GLYPHS if k <= size), default=min(TRAY_GLYPHS))
    return TRAY_GLYPHS[key].get(kind)


def _over(base: Color, wash: Color) -> Color:
    """``wash`` laid over the opaque ``base``: an opaque colour."""
    a = wash[3] / 255
    return (round(base[0] + (wash[0] - base[0]) * a), round(base[1] + (wash[1] - base[1]) * a),
            round(base[2] + (wash[2] - base[2]) * a), 255)


@functools.lru_cache(maxsize=64)
def _tray_track(pal: Palette) -> Color:
    """The empty part of the ring as an opaque colour: the variant's translucent tray_track laid over the plate, so
    the ring looks the same on a dark and on a light taskbar (a translucent pixel lets the taskbar through, which
    washed the track out to pale amber on a light one), and dimmed, never brightened, until the weakest lit colour
    reads against it at TRAY_ARC_CONTRAST."""
    lit = [pal.main, pal.bright, pal.hot]
    alpha = pal.tray_track[3]
    while True:
        track = _over(pal.crt, with_alpha(pal.tray_track, alpha))
        if alpha <= 0 or min(_contrast(colour, track) for colour in lit) >= TRAY_ARC_CONTRAST:
            return track
        alpha -= 2


def _contrast(a: Color, b: Color) -> float:
    return colour_math.contrast_ratio(colour_math.to_hex(a[:3]), colour_math.to_hex(b[:3]))


def _render_tray(size: int, percent: float | None, stale: bool, lit: Color, pal: Palette, theme: Any,
                 kind: str = "live") -> Image.Image:
    """One pixel per cell, no anti-aliasing: pixel centres decide ink. Each icon has its own opaque plate, and
    everything on it is opaque too, so the icon reads the same on any taskbar. ``kind`` is the lamp kind (see
    model.LAMP_KINDS): paused, offline and limited draw a glyph in the centre."""
    radius, thickness, chamfer = _tray_geometry(size)
    img = Image.new("RGBA", (size, size), TRANSPARENT)
    px = img.load()
    centre = size / 2
    fraction = 0.0 if percent is None else max(0.0, min(100.0, percent)) / 100.0
    centre_n = 2 if size <= 16 else 4
    centre_lo = size // 2 - centre_n // 2
    centre_hi = centre_lo + centre_n - 1
    glyph = _tray_glyph(kind, size)
    small = size < 28                                       # a small icon has a centre block, a large one the number
    has_centre = small and glyph is None
    track = _tray_track(pal)
    dash = track if glyph is not None else lit              # behind a glyph the dashes drop to the track, so it stands out
    inner_r = radius - thickness / 2
    for y in range(size):
        cy = y + 0.5
        for x in range(size):
            cx = x + 0.5
            if cx + cy < chamfer or (size - cx) + (size - cy) < chamfer:
                continue                                    # outside the chamfered plate
            colour = pal.crt
            if (x == 0 or y == 0 or x == size - 1 or y == size - 1
                    or cx + cy - chamfer < 1.42 or (size - cx) + (size - cy) - chamfer < 1.42):
                colour = pal.edge
            dx = cx - centre
            dy = cy - centre
            r = math.hypot(dx, dy)
            angle = math.atan2(dx, -dy) % (2 * math.pi)     # 0 at 12 o'clock, clockwise
            if has_centre and not stale and centre_lo <= x <= centre_hi and centre_lo <= y <= centre_hi:
                colour = lit
            elif radius - thickness <= r <= radius:
                if stale:
                    arc = angle * inner_r
                    colour = dash if math.floor(arc / 2) % 2 == 0 else pal.crt
                else:
                    colour = lit if fraction > 0 and angle <= fraction * 2 * math.pi else track
            px[x, y] = colour
    if glyph is not None:
        left, top = (size - max(len(row) for row in glyph)) // 2, (size - len(glyph)) // 2
        for j, row in enumerate(glyph):
            for i, mark in enumerate(row):
                if mark == "#":
                    px[left + i, top + j] = lit
    elif not small:
        _tray_text(img, size, "--" if stale or percent is None else str(round(percent)),
                   lit if stale else pal.hot, _faces(theme)[FONT_VT])
    return img


def _tray_text(img: Image.Image, size: int, text: str, colour: Color, face: Path) -> None:
    """The 32 px centre readout: VT323 18 px, thresholded to one bit so it stays pixel-sharp.

    The threshold is low (64 of 255) so the strokes come out as heavy as the browser-drawn
    readout in the approved tray sheet, which a 128 threshold would thin out.
    """
    font = engine.load_font(str(face), 18.0)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).text((size / 2, size / 2 + 1), text, font=font, fill=255, anchor="mm")
    mask = mask.point(lambda v: 255 if v >= 64 else 0)
    img.paste(colour, (0, 0, size, size), mask)


__all__ = ["NostromoSkin", "Palette", "RenderOptions"]
