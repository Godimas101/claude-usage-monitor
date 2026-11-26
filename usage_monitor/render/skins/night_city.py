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

Words. They are catalog keys (widget.night_city.*, #95), read in the model's language with English behind it and drawn
as written: their text is in capitals in every language. A name or a clock put into one is in the language's capitals
first (casing), and an age or a countdown is written by the view model (format_age_short, format_countdown). The brand
mark is drawn as written (#86), and a cut ends with the theme's three dots ("...").
"""
from __future__ import annotations

import functools
import math
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, NamedTuple

from PIL import Image, ImageFilter

from usage_monitor import casing, formats, i18n
from usage_monitor import themes as theme_registry
from usage_monitor.data.models import ApiStatus
from usage_monitor.i18n import ENGLISH, Locale
from usage_monitor.log import get_logger
from usage_monitor.render import engine
from usage_monitor.render.engine import Canvas, Color, Layer, Point, TRANSPARENT, parse_color, with_alpha
from usage_monitor.render.model import (
    LAMP_LIMITED,
    LAMP_LIVE,
    LAMP_OFFLINE,
    LAMP_PAUSED,
    LOGIN_COMMAND,
    NO_INSETS,
    PRODUCT,
    SEVERITY_CRITICAL,
    SEVERITY_NORMAL,
    SEVERITY_WARNING,
    STALE_REFUSED,
    SURFACE_FLOATING,
    SURFACE_TASKBAR,
    GuardMark,
    HoverPlay,
    Insets,
    Row,
    WidgetModel,
    format_age_short,
    format_countdown,
    pace_delta,
    unknown_kind_names,
)
from usage_monitor.render.skins import RenderOptions, coerce_options, face_donors

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
HOLD_SPLIT = 0.75               # the hold chip's standing glitch: logical px each copy of its word sits off the word
GUARD_GAP = 8                   # air round the armed guard's chip in the header, and above the hover card's guard block
GUARD_LINE_H = 13               # a line of the hover card's guard block
GUARD_BLOCK_PAD = 12            # what the block adds to the card besides its lines: the gap round its rule
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
_DONORS: dict[tuple[str, tuple[str, ...]], dict[str, tuple[engine.Donor, ...]]] = {}


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

BAND_STATUSES = frozenset({ApiStatus.STALE, ApiStatus.NO_CREDENTIALS, ApiStatus.OFFLINE, ApiStatus.RATE_LIMITED})
AGE_STATUSES = frozenset({ApiStatus.STALE, ApiStatus.OFFLINE, ApiStatus.RATE_LIMITED})

# The skin's words (#95): catalog keys, in capitals in every language (_words). en.json's English for each is what the
# skin drew before #95 (tests/fixtures/skin_words_english.json). The status words: the strip's status tag, for every
# status, and the header's LIVE tag (STATE_KEYS[ApiStatus.LIVE]).
STATE_KEYS: dict[ApiStatus, str] = {
    ApiStatus.LIVE: "widget.night_city.state.live",                         # LIVE
    ApiStatus.STALE: "widget.night_city.state.stale",                       # STALE
    ApiStatus.OFFLINE: "widget.night_city.state.offline",                   # OFFLINE
    ApiStatus.PAUSED: "widget.night_city.state.paused",                     # PAUSED
    ApiStatus.LOADING: "widget.night_city.state.loading",                   # LOADING
    ApiStatus.RATE_LIMITED: "widget.night_city.state.rate_limited",         # LIMITED
    ApiStatus.NO_CREDENTIALS: "widget.night_city.state.no_credentials",     # NO LOGIN
}
# A row's severity chip: the outlined WARN, the filled CRIT.
CHIP_KEYS: dict[str, str] = {SEVERITY_WARNING: "widget.night_city.state.warning",
                             SEVERITY_CRITICAL: "widget.night_city.state.critical"}
# The stale band's cause (_band_text): no connection; the sign-in ran out (using Claude Code renews it); the usage API
# turned it down (HTTP 403: only a new sign-in fixes it, #55); there is none (the dashboard has the steps, #28).
# {product} is Claude Code's name in the language's capitals, {command} the command as typed.
BAND_KEYS: dict[str, str] = {
    "offline": "widget.night_city.band.offline",
    "expired": "widget.night_city.band.expired",
    "refused": "widget.night_city.band.refused",
    "no_login": "widget.night_city.band.no_login",
}
# A row's or a guarded limit's name by kind. A scoped row is named for its model or surface alone, the name in the
# language's capitals; the hover card's guard line adds its WEEKLY (SCOPED_KEY).
LIMIT_KEYS: dict[str, str] = {"session": "widget.night_city.limit.session",
                              "weekly_all": "widget.night_city.limit.weekly"}
SCOPED_KEY = "widget.night_city.limit.scoped"
UNNAMED_SCOPED_KEY = "widget.night_city.limit.scoped_unnamed"
# The strip's labels of its two bars, by row: the 5-hour and the 7-day window ("5H", "7D").
WINDOW_KEYS: tuple[str, str] = ("widget.night_city.window.session", "widget.night_city.window.weekly")
HOLD_KEY = "widget.night_city.guard.hold"           # the guard's chip without its countdown ("HOLD")

# English records, never drawn: en.json's English for the band's causes and the strip's status words, kept for the
# older tests that read them (tests/test_skin_night_city.py); tests/test_skin_words.py holds them to en.json. The theme
# file's extras.copy holds the same four causes, as the mockup's record; the skin no longer reads it.
DEFAULT_STALE_AUTH = "RELINK // USE CLAUDE CODE"
DEFAULT_STALE_REFUSED = "RELINK // RUN /login"   # HTTP 403: only a new sign-in fixes it; the command keeps its case (#55)
DEFAULT_NO_LOGIN = "NO LOGIN // OPEN DASHBOARD"   # no sign-in at all: the dashboard has the steps (#28)
DEFAULT_OFFLINE = "NO SIGNAL // NET DOWN"
STRIP_WORDS: dict[ApiStatus, str] = {
    ApiStatus.LIVE: "LIVE",
    ApiStatus.STALE: "STALE",
    ApiStatus.OFFLINE: "OFFLINE",
    ApiStatus.PAUSED: "PAUSED",
    ApiStatus.LOADING: "LOADING",
    ApiStatus.RATE_LIMITED: "LIMITED",
    ApiStatus.NO_CREDENTIALS: "NO LOGIN",
}
BRAND_MARK = "CLAUDE//USAGE"        # the brand mark, drawn as written in every language (#86); it never gives way

# ── Fits (#95): what gives way where a language's words are longer than English's ──
# English never meets any of them (tests/test_skin_night_city_fit.py), so its frames are as before #95.

# Q2, an owner default on #95 that Chris may overturn: what the header's right-hand side gives up, in this order, while it
# does not fit GUARD_GAP off the brand. The hold chip drops its countdown first (HOLD alone), the order the stale band
# already takes (BAND_GIVE_WAY); then the right-hand word (the LIVE tag's, the status chip's, or the hold chip's) is cut
# with the theme's dots, one letter at least. The brand never gives way, and the armed guard's GUARD chip is drawn only
# in the room left, as before #95. Nostromo's default on #94 drops the countdown last. _header_plan takes the steps in
# this tuple's order, whatever it is, and tests/budgets.py derives the header line's give-way and its boxes' rooms from
# it: an answer changes this tuple alone (then test_budgets.py's LIMITS and the _budget notes are printed again).
HEADER_GIVE_WAY = ("countdown", "word")
# The hover card's reset-and-pace line: the pace shortens to its signed points ("-18", in its chip and colour; a stale
# card's plain pace the same), then is left out; a pace with no points (ON PACE, PACE --) is left out at once. The reset
# never gives way: it is what people look for there. _info_plan follows this tuple.
INFO_GIVE_WAY = ("pace", "pace_short")
# The stale band, while the guard holds or not: what gives way, in this order, while the cause, its age and the hold
# chip do not fit the band together. The chip drops its countdown (HOLD alone), the age is left out, and last the cause
# is cut: it is what the user acts on. _band_plan follows this tuple on both paths; a band without the hold chip skips
# its step.
BAND_GIVE_WAY = ("countdown", "age", "cause")
# The strip's window labels shrink from 11 px to 9 px, their tracking with them (0.12 em), then are left out: the bars'
# order still tells the two windows apart (_strip_label). A label's advance keeps STRIP_LABEL_AIR off its bar, so a
# shrunk label never reads as part of the bar (English's 5H takes 14.5 px of the 16 left).
STRIP_LABEL_SIZES = (11.0, 10.5, 10.0, 9.5, 9.0)
STRIP_LABEL_AIR = 2.0
# The strip's LIVE, status words and HOLD chip shrink to this size (_fit_type) before they are cut.
STRIP_FLOOR = 9.0
FIT_SLACK = 1e-6                    # float error in a sum of advances: a line exactly as wide as its room fits

# Q1, an owner default on #95 that Chris may overturn (as Nostromo's on #94): the boxes Night City outlines round a word
# grow round marks whose ink would touch their 1 px stroke, and the stale band round marks that would reach its top
# hazard strip. It is decided by the word's characters, per face, so a box is the same at every scale (_mark_growth):
# the least growth that keeps ink off the stroke, measured on the faces at every scale and card height (Rajdhani for the
# chips, the tags and the band's cause; Share Tech Mono, with Consolas for ğ Ğ ş Ş İ, for the reset box and the band's
# age). The boxes:
# - up, round a capital with a mark above (É, Ñ, İ, Ğ) or one of the face's RISERS: the outlined chip (_chip_out: the
#   header's status and GUARD chips, a row's WARN, the hover card's pace chip), Blackwall's outlined LIVE tag and strip
#   tag MARK_GROWTH_UP["body"] px; Blackwall's outlined reset box MARK_GROWTH_UP["mono"]; the band as far as its
#   cause's, its age's or its hold chip's word asks, its top hazard strip, outline and hold chip with it (the chip keeps
#   its word on the band's baseline);
# - down, round a cedilla or a comma below (Ç Ş Ș) or one of DESCENDERS: Blackwall's outlined LIVE tag and strip tag
#   MARK_GROWTH_DOWN px; the outlined chip CHIP_GROWTH_DOWN px round a comma below alone (Ș ș: a cedilla keeps 1 px of air
#   over its 4 px deeper edge).
# The filled chips (CRIT, CAP, HOLD) and the Edgerun plate's tags never grow: every mark stays inside the fill. English
# never moves: none of its boxed words holds such a character. A word with no mark but a DESCENDERS glyph (QUOTA, a
# bracket) grows the tags down too: without it, its ink lands on the stroke. Where a grown box meets a neighbouring
# stroke with no air (never overlapping), GROWN_MEETS says so (tests/test_skin_night_city_fit.py holds every other box to
# keep air round its neighbours).
MARK_GROWTH_UP: dict[str, int] = {"body": 1, "mono": 2}
MARK_GROWTH_DOWN = 2
CHIP_GROWTH_DOWN = 1
# Per face, the characters with no mark whose ink reaches a box's top stroke at some scale; and Rajdhani's that reach
# the outlined tags' bottom stroke. Held to the faces by tests/test_skin_night_city_fit.py.
RISERS: dict[str, frozenset[str]] = {"body": frozenset(), "mono": frozenset("$()/[\\]^{|}")}
DESCENDERS = frozenset("();Q[]gjpqy{}")
REACHING_MARKS = frozenset({unicodedata.lookup("COMBINING CEDILLA"), unicodedata.lookup("COMBINING COMMA BELOW")})
CHIP_REACHING_MARKS = frozenset({unicodedata.lookup("COMBINING COMMA BELOW")})
_ABOVE = 230                        # the canonical combining class of a mark above
# Which face each growing box's word is in, by the name _mark_growth takes.
MARK_FACES: dict[str, str] = {"chip": "body", "tag": "body", "band": "body", "reset": "mono", "band_age": "mono"}
# The neighbouring strokes a grown box meets with no air, never overlapping (Q1's recorded conflicts).
GROWN_MEETS: tuple[str, ...] = (
    "a row's WARN chip grown CHIP_GROWTH_DOWN px down round a comma below (Ș ș) meets its row's rail (top + 15)",
)


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


def _donors(theme: Any, faces: Mapping[str, Path]) -> dict[str, tuple[engine.Donor, ...]]:
    """The fallback faces of each face file (face_donors), resolved once per theme and set of files."""
    key = (str(getattr(theme, "id", "night_city")), tuple(str(path) for path in faces.values()))
    if key not in _DONORS:
        _DONORS[key] = face_donors(theme, _FACE_ROLES, faces)
    return _DONORS[key]


def _canvas(width: float, height: float, scale: float, theme: Any) -> Canvas:
    cv = Canvas(width, height, scale)
    cv.faces = _faces(theme)
    cv.donors = _donors(theme, cv.faces)
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
        font = self.font(spec)
        path = getattr(font, "path", None)
        if not text or not isinstance(path, str):
            return spec.tracking
        return spec.tracking + _advance_gap(path, self.cv.font_donors(font), spec.size, self.cv.k, text)

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
def _advance_gap(path: str, donors: tuple[engine.Donor, ...], size: float, k: float, text: str) -> float:
    """Per-glyph letter-spacing (logical px) that brings ``text`` to its true width at ``size``. Each character is
    measured in the face the engine draws it from (engine.face_index), and as it draws it (engine.drawn)."""
    text = engine.drawn(text)
    files = [path, *(donor.path for donor in donors)]
    picked = [files[engine.face_index(path, donors, char)] for char in text]
    true = sum(_em_advance(face, char) for face, char in zip(picked, text)) * size
    hinted = sum(engine.load_font(face, round(size * k, 3)).getlength(char) for face, char in zip(picked, text)) / k
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

def _words(locale: Locale) -> i18n.Catalog:
    """The catalog the skin's words come from: the locale's language's, English behind it (#95). It comes with the
    model, so the skin never works out a language itself."""
    return i18n.app_catalog(locale.language)


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


def _over_cap(model: WidgetModel, row: Row) -> bool:
    """Whether the usage guard (#27) holds because of this row: it is at or past the cap the guard watches for it."""
    guard = model.guard
    return guard is not None and guard.holding and row.key in guard.over_keys and row.percent is not None


def _row_emphasis(model: WidgetModel, row: Row) -> str:
    """_emphasis, raised to critical for a row the guard holds on, whatever its severity: a cap set at 60 % is as
    final to the user as the API's critical, so its numerals, bar and chip read as the state they are."""
    if not model.stale and _over_cap(model, row):
        return SEVERITY_CRITICAL
    return _emphasis(model, row)


def _chip_word(model: WidgetModel, row: Row) -> str:
    """The word of a row's critical chip: CRIT, or the cap ("CAP 95", a number without its sign) for a row the guard
    holds on, HOLD for one held with no cap."""
    guard, words = model.guard, _words(model.locale)
    if model.stale or not _over_cap(model, row) or guard is None:
        return words.text(CHIP_KEYS[SEVERITY_CRITICAL])
    cap = guard.caps.get(row.key)
    if cap is None:
        return words.text(HOLD_KEY)
    return words.text("widget.night_city.cap.tag",
                      {"number": formats.number(_number(cap), model.locale, group=False)})


def _number(value: float) -> str:
    """A cap as people write it: 95, not 95.0. ASCII digits, for formats.number or formats.percent to write."""
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _hold_word(model: WidgetModel) -> str | None:
    """The hold chip's text while the guard holds: "HOLD 2H 14M", or "HOLD" when no reset time is known. None while
    the guard is off or only armed. The countdown is always the time left ("HOLD 2D 3H"), as the view model writes it
    (format_countdown); the hover card has the clock."""
    guard = model.guard
    if guard is None or not guard.holding:
        return None
    words = _words(model.locale)
    if guard.resume_seconds is None:
        return words.text(HOLD_KEY)
    return words.text("widget.night_city.guard.hold_for",
                      {"countdown": format_countdown(guard.resume_seconds, model.locale)})


def _digits(percent: float | None) -> str:
    """A value rounded half up, never below 0, as ASCII digits ("--" for none): formats.number writes them."""
    return "--" if percent is None else str(max(0, int(percent + 0.5)))


def _exact_digits(percent: float | None) -> str:
    """The hover card's value to a tenth, ".0" dropped, as ASCII digits: formats.number writes them."""
    if percent is None:
        return "--"
    text = f"{max(0.0, percent):.1f}"
    return text[:-2] if text.endswith(".0") else text


def _is_scoped(row: Row) -> bool:
    return row.key.startswith("scoped:")


def _row_name(row: Row, *, locale: Locale = ENGLISH) -> str:
    """The row's name as the design shows it, in capitals: "SESSION", "WEEKLY", and a scoped limit named for its model,
    else its surface ("FABLE"; in the language's capitals, "HAİKU" in Turkish), or "WEEKLY SCOPED" when it has
    neither. A kind this skin has no words for is named from its code, its short name when it is scoped ("DESIGN" for
    design_weekly, unknown_kind_names); a row built without a kind keeps its label, in its own language's capitals."""
    words = _words(locale)
    if row.kind in LIMIT_KEYS:
        return words.text(LIMIT_KEYS[row.kind])
    if row.kind == "weekly_scoped":
        if not row.scope_name:
            return words.text(UNNAMED_SCOPED_KEY)
        name = casing.upper(row.scope_name.strip(), locale.language)
        return name or words.text(LIMIT_KEYS["weekly_all"])     # a name of spaces leaves its WEEKLY
    if row.kind:
        name, short = unknown_kind_names(row.kind)
        return short if _is_scoped(row) else name
    return casing.upper(row.label).strip()


def _share_tag(row: Row, locale: Locale) -> str | None:
    """A scoped row's share of the weekly limit, "50% SHARE" ("50 % SHARE" in French); None for the other rows and
    a scoped one without it."""
    if not _is_scoped(row) or row.share_pct is None:
        return None
    return _words(locale).text("widget.night_city.share.of",
                               {"percent": formats.percent(str(round(row.share_pct)), locale)})


def _sign_first(locale: Locale) -> bool:
    """Whether ``locale`` puts the percent sign before the number ("%34" in Turkish)."""
    return bool(formats.percent_parts("0", locale, tight=True)[0])


def _band_text(theme: Any, model: WidgetModel) -> str:
    """The stale band's cause (BAND_KEYS); while the usage API throttles the monitor, the view model's status word,
    which is in capitals already. ``theme`` stays for the callers: its extras.copy is the mockup's record of the causes,
    and the catalog's words are drawn."""
    words = _words(model.locale)
    if model.status is ApiStatus.OFFLINE:
        return words.text(BAND_KEYS["offline"])
    if model.status is ApiStatus.RATE_LIMITED:
        return model.status_text
    if model.status is ApiStatus.NO_CREDENTIALS:
        return words.text(BAND_KEYS["no_login"])
    names = {"product": casing.upper(PRODUCT, model.locale.language), "command": LOGIN_COMMAND}
    if model.stale_reason == STALE_REFUSED:
        return words.text(BAND_KEYS["refused"], names)
    return words.text(BAND_KEYS["expired"], names)


def _age_tag(model: WidgetModel) -> str | None:
    """"41 M OLD" for the band: the model's age in its one unit, as the view model writes it (format_age_short)."""
    if model.age_seconds is None or model.status not in AGE_STATUSES:
        return None
    return _words(model.locale).text("widget.night_city.age.old",
                                     {"age": format_age_short(model.age_seconds, model.locale)})


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


# ── Fits: the decisions, as pure helpers the tests read ───────────────────────

def _cut(ink: _Ink, text: str, spec: Type, limit: float) -> str:
    """``text`` whole when it fits ``limit``, else cut with the theme's dots (_fit), keeping one letter at least: a
    one-letter text is its own floor."""
    cut = _fit(ink, text, spec, limit + FIT_SLACK)
    return text if cut != text and ink.width(cut, spec) >= ink.width(text, spec) else cut


@dataclass(frozen=True, slots=True)
class HeaderPlan:
    """What the header's right-hand side draws (_header_plan): its word as drawn; what draws it ("hold" the HOLD chip,
    "live" the LIVE tag, "chip" the status chip); the armed guard's GUARD chip, or None when there is none or no room
    for it; and the HEADER_GIVE_WAY steps taken, in order (none while the line fits)."""
    word: str
    kind: str
    mark: str | None
    gave_way: tuple[str, ...] = ()


def _brand_x(pal: Palette) -> float:
    """Where the brand starts: after the pip, or at LEFT on the Edgerun plate, which has no pip."""
    return LEFT if pal.plate else LEFT + PIP + BRAND_GAP


def _right_pad(ink: _Ink, look: Look, kind: str) -> float:
    """What a header's right-hand box adds to its word: the hold chip 17 px, the status chip 12, the LIVE tag 20 on the
    plate (its dot), 8 outlined (Blackwall), none plain."""
    if kind == "hold":
        return 17.0
    if kind == "chip":
        return 12.0
    return 20.0 if ink.pal.plate else 8.0 if look.boxed else 0.0


def _header_plan(ink: _Ink, look: Look, model: WidgetModel) -> HeaderPlan:
    """The header's right-hand word as it fits from GUARD_GAP after the brand to RIGHT, measured on ``ink`` (its palette
    says whether the plate is on): the hold chip's word while the guard holds, else the LIVE tag's while live, else the
    view model's status word in its chip. While it does not fit, the HEADER_GIVE_WAY steps are taken in order until it
    does. The armed guard's GUARD chip is chosen last, in the room left, GUARD_GAP off each side."""
    words = _words(model.locale)
    brand_end = _brand_x(ink.pal) + ink.width(BRAND_MARK, BRAND)
    room = RIGHT - brand_end - GUARD_GAP
    hold = _hold_word(model)
    live = model.status is ApiStatus.LIVE and not model.stale
    kind = "hold" if hold is not None else "live" if live else "chip"
    spec = STATUS if kind == "live" else CHIP
    word = hold if hold is not None else words.text(STATE_KEYS[ApiStatus.LIVE]) if live else model.status_text
    short = words.text(HOLD_KEY)
    pad = _right_pad(ink, look, kind)

    def fits(text: str) -> bool:
        return ink.width(text, spec) + pad <= room + FIT_SLACK

    # A step a header cannot take is skipped: only a hold chip with a countdown has one to drop.
    can = {"countdown": hold is not None and hold != short, "word": True}
    taken: list[str] = []

    def refit() -> str:
        """The word as the steps taken leave it, whatever their order: the countdown step leaves HOLD alone, the word
        step lets the word be cut."""
        whole = short if "countdown" in taken else word
        return _cut(ink, whole, spec, room - pad) if "word" in taken else whole

    drawn = word
    if not fits(word):
        for step in HEADER_GIVE_WAY:
            if not can[step]:
                continue
            taken.append(step)
            drawn = refit()
            if fits(drawn):
                break
    mark = None
    if model.guard is not None and hold is None:
        text = words.text("widget.night_city.guard.mark")
        tag_left = RIGHT - (ink.width(drawn, spec) + pad)
        if tag_left - GUARD_GAP - _chip_out_width(ink, text) >= brand_end + GUARD_GAP:
            mark = text
    return HeaderPlan(drawn, kind, mark, tuple(taken))


@dataclass(frozen=True, slots=True)
class InfoPlan:
    """What the hover card's reset-and-pace line draws (_info_plan): the reset (None when the row has none); the pace as
    drawn (whole, its short form or None); whether it is a chip (its words are news: ahead or under pace, on a live
    card) or plain text; and the INFO_GIVE_WAY steps taken."""
    reset: str | None
    pace: str | None
    chip: bool
    kind: str
    gave_way: tuple[str, ...] = ()


def _pace_short(row: Row) -> str | None:
    """The pace's short form for a tight hover line: its signed points alone ("+41", "-18"), a figure with no words, in
    the full form's chip or tone. None for a pace that has no points (ON PACE, PACE --). English never draws it: its
    lines fit whole."""
    delta = pace_delta(row.percent, row.time_fraction)
    if delta is None or row.pace is None or row.pace == "on_pace":
        return None
    points = round(abs(delta))
    return f"+{points}" if row.pace == "ahead" else f"-{points}"


def _info_plan(ink: _Ink, look: Look, model: WidgetModel, row: Row) -> InfoPlan:
    """The hover line's reset (from LEFT, in its box when ``look`` is boxed) and pace (right-aligned at RIGHT; a chip on
    a live card when ahead or under pace, else plain text: a stale card's pace is plain), VALUE_GAP apart at least,
    measured on ``ink``: while they do not fit, the pace takes its short form (_pace_short), then is left out
    (INFO_GIVE_WAY)."""
    reset = _reset_text(row, True, locale=model.locale)
    text, kind = _pace_text(row, locale=model.locale)
    chip = kind != "plain" and not model.stale
    room = float(CONTENT_W)
    if reset is not None:
        room -= ink.width(reset, RESET) + (8 if look.boxed else 0) + VALUE_GAP
    pace: str | None = text
    forms = {"pace": _pace_short(row), "pace_short": None}
    taken: list[str] = []
    for step in INFO_GIVE_WAY:
        if pace is None or ink.width(pace, CHIP) + (12 if chip else 0) <= room + FIT_SLACK:
            break
        pace = forms[step]
        taken.append(step)
    return InfoPlan(reset, pace, chip, kind, tuple(taken))


@dataclass(frozen=True, slots=True)
class BandPlan:
    """What the stale band draws (_band_plan): the cause as drawn (whole or cut), the age (None when there is none or it
    gave way), the hold chip's word (None while the guard does not hold; HOLD alone once its countdown gave way), and
    the BAND_GIVE_WAY steps taken."""
    cause: str
    age: str | None
    chip: str | None
    gave_way: tuple[str, ...] = ()


def _band_plan(ink: _Ink, model: WidgetModel, text: str, age: str | None, hold: str | None,
               short: str | None = None) -> BandPlan:
    """The band's cause from BAND_TEXT_X, its age 10 px after it and, while the guard holds, the hold chip (``hold``,
    its word; ``short`` HOLD alone, the language's when None) GUARD_GAP after that, all to BAND_AGE_RIGHT. While they do
    not fit, the BAND_GIVE_WAY steps are taken in order until they do; the cause is cut last, to the room left."""
    short = _words(model.locale).text(HOLD_KEY) if short is None else short
    room = BAND_AGE_RIGHT - BAND_TEXT_X

    def others(age_on: bool, chip: str | None) -> float:
        return ((ink.width(age, RESET) + 10 if age_on and age else 0.0)
                + (GUARD_GAP + _chip_hold_width(ink, chip) if chip is not None else 0.0))

    def fits(cause: str, age_on: bool, chip: str | None) -> bool:
        return ink.width(cause, BAND_TYPE) + others(age_on, chip) <= room + FIT_SLACK

    can = {"countdown": hold is not None and hold != short, "age": bool(age), "cause": True}
    taken: list[str] = []

    def refit() -> tuple[str, bool, str | None]:
        """The texts as the steps taken leave them, whatever their order."""
        chip = None if hold is None else short if "countdown" in taken else hold
        age_on = bool(age) and "age" not in taken
        cause = _cut(ink, text, BAND_TYPE, room - others(age_on, chip)) if "cause" in taken else text
        return cause, age_on, chip

    cause, age_on, chip = text, bool(age), hold
    if not fits(cause, age_on, chip):
        for step in BAND_GIVE_WAY:
            if not can[step]:
                continue
            taken.append(step)
            cause, age_on, chip = refit()
            if fits(cause, age_on, chip):
                break
    return BandPlan(cause, age if age_on else None, chip, tuple(taken))


def _strip_label(ink: _Ink, text: str, room: float) -> Type | None:
    """The style the strip draws a window label in: the first of STRIP_LABEL_SIZES where it fits ``room``, its tracking
    0.12 em (STATUS's at 11 px). None when it does not fit even at the last: the label is left out."""
    for size in STRIP_LABEL_SIZES:
        spec = STATUS if size == STATUS.size else STATUS._replace(size=size, tracking=STATUS.tracking * size / STATUS.size)
        if ink.width(text, spec) <= room + FIT_SLACK:
            return spec
    return None


def _mark_growth(text: str | None, box: str) -> tuple[int, int]:
    """(px up, px down) the box named ``box`` (MARK_FACES: "chip" the outlined chip, "tag" Blackwall's outlined LIVE and
    strip tags, "reset" its reset box, "band" and "band_age" the stale band round its cause or hold chip and round its
    age) grows round ``text`` (Q1, see MARK_GROWTH_UP): up for a capital with a mark above or one of its face's RISERS;
    the tags down for a cedilla or a comma below (REACHING_MARKS) or one of DESCENDERS, the chip down for a comma below.
    (0, 0) for English's boxed words."""
    face = MARK_FACES[box]
    up = below = comma = False
    base = ""
    for ch in unicodedata.normalize("NFD", text or ""):
        mark = unicodedata.combining(ch)
        if not mark:
            base = ch
            up = up or ch in RISERS[face]
            below = below or ch in DESCENDERS
        elif mark == _ABOVE:
            up = up or base.isupper()
        elif ch in REACHING_MARKS:
            below = True
            comma = comma or ch in CHIP_REACHING_MARKS
    down = (MARK_GROWTH_DOWN if below else 0) if box == "tag" else (CHIP_GROWTH_DOWN if comma else 0) \
        if box == "chip" else 0
    return (MARK_GROWTH_UP[face] if up else 0), down


def _band_growth(plan: BandPlan) -> int:
    """How far the band grows up (Q1): as far as its cause, its age or its hold chip's word asks, else 0."""
    return max(_mark_growth(plan.cause, "band")[0], _mark_growth(plan.age, "band_age")[0],
               _mark_growth(plan.chip, "band")[0])


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
    """Outline chip, 14 px tall, 5 px top-left chamfer, filled with the panel; grown round marks (Q1, _mark_growth), its
    word where it was. Returns its width."""
    width = _chip_out_width(ink, text)
    up, down = _mark_growth(text, "chip")
    top, bottom = y - up, y + CHIP_H + down
    x0, x1 = ink.snap(x), ink.snap(x + width)
    outer = [(x0 + CHIP_CUT, top), (x1, top), (x1, bottom), (x0, bottom), (x0, top + CHIP_CUT)]
    inner = [(x0 + CHIP_CUT, top + 1), (x1 - 1, top + 1), (x1 - 1, bottom - 1),
             (x0 + 1, bottom - 1), (x0 + 1, top + CHIP_CUT)]
    ink.polygon(outer, outline)
    ink.polygon(inner, ink.pal.panel)
    ink.text(text, CHIP, x + 6, y + 10, color)
    return width


def _chip_crit(ink: _Ink, x: float, y: float, word: str | None = None) -> float:
    """CRIT: an ink box with a 7 px hazard cap and a 5 px top-right chamfer. Returns its width. The usage guard
    puts its own word in it: the cap a row is held on ("CAP 95"). With no ``word``, English's CRIT."""
    pal = ink.pal
    word = _words(ENGLISH).text(CHIP_KEYS[SEVERITY_CRITICAL]) if word is None else word
    width = ink.width(word, CHIP) + 17
    x0, x1 = ink.snap(x), ink.snap(x + width)
    ink.polygon([(x0, y), (x1 - CHIP_CUT, y), (x1, y + CHIP_CUT), (x1, y + CHIP_H), (x0, y + CHIP_H)], pal.ink)
    ink.hazard((x0, y, x0 + 7, y + CHIP_H), *pal.crit_fill)
    ink.text(word, CHIP, x + 11, y + 10, pal.hz_a)
    return width


def _chip_hold_width(ink: _Ink, word: str, spec: Type = CHIP) -> float:
    return ink.width(word, spec) + 17


def _chip_hold(ink: _Ink, x: float, y: float, word: str, height: float = CHIP_H, glitch: bool = True, *,
               spec: Type = CHIP, baseline: float | None = None) -> float:
    """The guard's HOLD chip: the CRIT chip's shape with the word in the bright tone and a glitch under it, the
    text drawn again a pixel either side in the selection and the danger colours, as a refresh hit splits the
    numerals. The split stays still: the chip is a state, not an event. The Edgerun plate has no bright tone (its
    text is ink, for the yellow) and no glow, so there the word is the CRIT chip's red and the split is left out,
    and so it is for a stale frame (``glitch`` False), which is drawn flat: the fringes would outshine the dimmed
    word. ``height`` is the chip's, 14 px in the header and 12 inside the stale band, between its hazard strips (14 in a
    band grown round marks, Q1). The word sits on ``baseline`` (the band's), else centred as in a 14 px chip; ``spec`` is
    its style (the strip's shrinks, _fit_type). Returns its width."""
    pal = ink.pal
    width = _chip_hold_width(ink, word, spec)
    x0, x1 = ink.snap(x), ink.snap(x + width)
    ink.polygon([(x0, y), (x1 - CHIP_CUT, y), (x1, y + CHIP_CUT), (x1, y + height), (x0, y + height)], pal.ink)
    ink.hazard((x0, y, x0 + 7, y + height), *pal.crit_fill)
    layer, _ = ink.layer(word, spec, x + 11, y + 10 - (CHIP_H - height) / 2 if baseline is None else baseline)
    if pal.plate:
        ink.glyphs(layer, pal.hz_a)
        return width
    if not glitch:
        ink.glyphs(layer, pal.fg_hi)
        return width
    shift = round(HOLD_SPLIT * ink.cv.kx)
    ink.stamp(Layer(layer.mask, layer.x - shift, layer.y), pal.sel)
    ink.stamp(Layer(layer.mask, layer.x + shift, layer.y), pal.hz_a)
    ink.glyphs(layer, pal.fg_hi)
    return width


def _live_tag(ink: _Ink, look: Look, motion: Motion, text: str) -> float:
    """The header's right-hand word: plain, in a 1 px outline (Blackwall), or on a black chip with the live dot
    (the Edgerun plate). Returns the tag's left edge."""
    pal = ink.pal
    word_w = ink.width(text, STATUS)
    if pal.plate:
        left = RIGHT - (word_w + 20)
        ink.rect(ink.snap(left), TAG_TOP, RIGHT, TAG_BOTTOM, pal.ink)
        _pip(ink, left + 5, TAG_TOP + 3, pal.ok, motion.pulse, glow=False)
        ink.text(text, STATUS, left + 15, BOXED_BASE, pal.ok)
    elif look.boxed:
        left = RIGHT - (word_w + 8)
        up, down = _mark_growth(text, "tag")
        ink.outline(ink.snap(left), TAG_TOP - up, RIGHT, TAG_BOTTOM + down, pal.line)
        ink.text(text, STATUS, left + 4, BOXED_BASE, pal.ok)
    else:
        left = RIGHT - word_w
        ink.text(text, STATUS, RIGHT, HEAD_BASE, pal.ok, align="right")
    return left


def _draw_header(ink: _Ink, look: Look, model: WidgetModel, motion: Motion) -> None:
    """Alive mark and brand on the left, the status word on the right, as _header_plan fits it. While the usage guard
    holds, the right-hand place is the HOLD chip (the status is in the pulse and the dim of the rows); while it is only
    armed, a quiet GUARD chip sits before the status word, if the brand leaves room."""
    pal = ink.pal
    live = model.status is ApiStatus.LIVE and not model.stale
    head = _header_plan(ink, look, model)
    if not pal.plate:
        _pip(ink, LEFT, PIP_Y, pal.ok if live else pal.stale, motion.pulse if live else 1.0, glow=live)
    brand_x = _brand_x(pal)
    ink.text(BRAND_MARK, BRAND, brand_x, HEAD_BASE, pal.fg_hi)
    if head.kind == "hold":
        _chip_hold(ink, RIGHT - _chip_hold_width(ink, head.word), HEADER_TOP - 1, head.word, glitch=not model.stale)
        return
    if head.kind == "live":
        tag_left = _live_tag(ink, look, motion, head.word)
    else:
        tag_left = RIGHT - _chip_out_width(ink, head.word)    # the view model's word, in capitals already
        _chip_out(ink, tag_left, HEADER_TOP - 1, head.word, pal.ink3, pal.ink2)
    if head.mark is not None:
        _chip_out(ink, tag_left - GUARD_GAP - _chip_out_width(ink, head.mark), HEADER_TOP - 1, head.mark, pal.ink3,
                  pal.ink2)


def _draw_band(ink: _Ink, theme: Any, model: WidgetModel) -> None:
    """The stale band: an ink box with a warn outline, hazard strips top and bottom, the cause, its age and, while the
    guard holds, the hold chip, as _band_plan fits them (BAND_GIVE_WAY). It grows up round marks (Q1, _band_growth)."""
    pal = ink.pal
    plan = _band_plan(ink, model, _band_text(theme, model), _age_tag(model), _hold_word(model))
    x0, y0, x1, y1 = BAND
    y0 -= _band_growth(plan)
    ink.rect(x0, y0, x1, y1, pal.ink)
    ink.outline(x0, y0, x1, y1, pal.warn)
    fill_a, fill_b = pal.crit_fill
    ink.hazard((x0, y0, x1, y0 + HAZARD_STRIP), fill_a, fill_b)
    ink.hazard((x0, y1 - HAZARD_STRIP, x1, y1), fill_a, fill_b)
    _draw_band_words(ink, model, plan, y0)


def _draw_band_hold(ink: _Ink, model: WidgetModel, text: str, age: str | None, hold: str,
                    short: str | None = None) -> None:
    """The stale band's words while the guard holds. The cause stays whole (it is what the user acts on), the HOLD chip
    sits at the right end between the hazard strips, and the age keeps its place before it when the band has room for
    all three. What gives way, in BAND_GIVE_WAY's order: the countdown (the chip is then ``short``, HOLD alone, the
    language's when None), the age, and last the end of the cause. ``hold`` is the chip's full word; with no countdown
    it is ``short`` itself, and there is no countdown to drop."""
    plan = _band_plan(ink, model, text, age, hold, short)
    _draw_band_words(ink, model, plan, BAND[1] - _band_growth(plan))


def _draw_band_words(ink: _Ink, model: WidgetModel, plan: BandPlan, top: float) -> None:
    """The band's words as ``plan`` has them: the cause, the age right-aligned at BAND_AGE_RIGHT (GUARD_GAP before the
    hold chip while there is one) and the chip between the hazard strips of a band whose top is ``top``, its word on
    the band's baseline."""
    pal = ink.pal
    ink.text(plan.cause, BAND_TYPE, BAND_TEXT_X, BAND_BASE, pal.warn)
    left = right = BAND_AGE_RIGHT
    if plan.chip is not None:
        left = BAND_AGE_RIGHT - _chip_hold_width(ink, plan.chip)
        right = left - GUARD_GAP
    if plan.age:
        ink.text(plan.age, RESET, right, BAND_BASE, pal.signal if pal.plate else pal.ink2, align="right")
    if plan.chip is not None:
        _chip_hold(ink, left, top + HAZARD_STRIP, plan.chip, BAND[3] - top - 2 * HAZARD_STRIP, glitch=not model.stale,
                   baseline=float(BAND_BASE))


# ── Rows ──────────────────────────────────────────────────────────────────────

class RowBoxes(NamedTuple):
    """Where a row's parts sit on the x axis, as (left, right). tag, chip and reset are None when absent or
    dropped for room."""
    name: tuple[float, float]
    tag: tuple[float, float] | None
    chip: tuple[float, float] | None
    reset: tuple[float, float] | None
    value: tuple[float, float]


def _chip_width(ink: _Ink, emphasis: str, word: str | None = None, *, locale: Locale = ENGLISH) -> float:
    """The width of a row's severity chip: the filled one round ``word`` (the language's CRIT when None), the outlined
    one round the language's WARN (as _draw_row draws them), none for a normal row."""
    if emphasis == SEVERITY_CRITICAL:
        return ink.width(_words(locale).text(CHIP_KEYS[SEVERITY_CRITICAL]) if word is None else word, CHIP) + 17
    if emphasis == SEVERITY_WARNING:
        return _chip_out_width(ink, _words(locale).text(CHIP_KEYS[SEVERITY_WARNING]))
    return 0.0


def _layout_row(ink: _Ink, look: Look, row: Row, emphasis: str, digits: str, reset: str | None,
                chip_word: str | None = None, *, locale: Locale) -> RowBoxes:
    """Fit a row's name side against its value side. A row too wide for both gives up its reset text first,
    then its share tag, and then the end of its name; the value and the severity chip always stay. The value is
    as wide on either side of its sign (_draw_value)."""
    unit_w = ink.width("%", UNIT) if row.percent is not None else 0.0
    value_w = ink.width(digits, VALUE) + (UNIT_GAP + unit_w if unit_w else 0.0)
    value = (RIGHT - value_w, float(RIGHT))
    name = _row_name(row, locale=locale)
    name_w = ink.width(name, NAME)
    tag = _share_tag(row, locale)
    tag_w = ink.width(tag, TAG) if tag else 0.0
    chip_w = _chip_width(ink, emphasis, chip_word, locale=locale)

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


def _draw_value(ink: _Ink, row: Row, emphasis: str, motion: Motion, top: float, digits: str,
                locale: Locale) -> None:
    """The numerals in Share Tech Mono 15 with a small dim percent sign, glowing; a refresh hit splits them. The sign
    goes on its language's side (after the numerals in English, French and Spanish, before them in Turkish), UNIT_GAP
    from them (it stands for the space French and Spanish put there)."""
    pal = ink.pal
    has_unit = row.percent is not None
    unit_w = ink.width("%", UNIT) if has_unit else 0.0
    first = has_unit and _sign_first(locale)
    digits_right = RIGHT - (unit_w + UNIT_GAP if has_unit and not first else 0.0)
    if has_unit:
        sign_right = RIGHT - ink.width(digits, VALUE) - UNIT_GAP if first else RIGHT
        ink.text("%", UNIT, sign_right, top + UNIT_BASE, pal.ink2, align="right")
    color, glow = pal.fg_hi, pal.glow
    if emphasis == SEVERITY_WARNING:
        color = pal.value_warn
    elif emphasis == SEVERITY_CRITICAL:
        color, glow = pal.value_crit, pal.glow_danger
    layer, _ = ink.layer(digits, VALUE, digits_right, top + VALUE_BASE, align="right")
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


def _reset_text(row: Row, tooltip: bool, *, locale: Locale = ENGLISH) -> str | None:
    """The reset text of a row: "RST 11:39 PM", the clock in the language's capitals ("RST CMT 20:12" in Turkish).
    The widget leaves it off scoped rows, which reset with the weekly limit."""
    if not row.resets_text or (_is_scoped(row) and not tooltip):
        return None
    return _words(locale).text("widget.night_city.resets.at", {"clock": casing.upper(row.resets_text, locale.language)})


def _draw_reset(ink: _Ink, look: Look, text: str, box: tuple[float, float], top: float) -> None:
    """The reset text, in Blackwall's outlined box (grown up round marks, Q1) or plain, right-aligned in the box."""
    pal = ink.pal
    if look.boxed:
        ink.outline(ink.snap(box[0]), top - _mark_growth(text, "reset")[0], ink.snap(box[1]), top + CHIP_H, pal.line)
        ink.text(text, RESET, box[0] + 4, top + RESET_BASE, pal.ink2)
    else:
        ink.text(text, RESET, box[1], top + RESET_BASE, pal.ink2, align="right")


def _draw_row(ink: _Ink, look: Look, model: WidgetModel, row: Row, top: float, motion: Motion,
              tooltip: bool = False) -> None:
    """One limit: name (tag, chip) and reset text above, value on the right, hairline and bar below. The
    tooltip moves the reset text to an info line under the bar and shows the exact percentage."""
    pal = ink.pal
    emphasis = _row_emphasis(model, row)
    word = _chip_word(model, row)
    digits = formats.number(_exact_digits(row.percent) if tooltip else _digits(row.percent), model.locale, group=False)
    reset = _reset_text(row, tooltip, locale=model.locale)
    boxes = _layout_row(ink, look, row, emphasis, digits, None if tooltip else reset, word, locale=model.locale)
    name = _row_name(row, locale=model.locale)
    if ink.width(name, NAME) > boxes.name[1] - boxes.name[0] + 0.01:
        name = _fit(ink, name, NAME, boxes.name[1] - boxes.name[0])
    ink.text(name, NAME, LEFT, top + NAME_BASE, pal.fg)
    tag = _share_tag(row, model.locale)
    if boxes.tag is not None and tag:
        ink.text(tag, TAG, boxes.tag[0], top + TAG_BASE, pal.ink3)
    if boxes.chip is not None:
        if emphasis == SEVERITY_CRITICAL:
            _chip_crit(ink, boxes.chip[0], top, word)
        else:
            colour = pal.ink if pal.plate else pal.warn
            warn = _words(model.locale).text(CHIP_KEYS[SEVERITY_WARNING])
            _chip_out(ink, boxes.chip[0], top, warn, colour, colour)
    if boxes.reset is not None and reset is not None:
        _draw_reset(ink, look, reset, boxes.reset, top)
    _draw_value(ink, row, emphasis, motion, top, digits, model.locale)
    _draw_rail(ink, row, top)
    _draw_bar(ink, look, row, emphasis, top)
    if tooltip:
        _draw_info(ink, look, model, row, reset, top + TOOLTIP_INFO)


# ── Tooltip info line ─────────────────────────────────────────────────────────

def _pace_text(row: Row, *, locale: Locale = ENGLISH) -> tuple[str, str]:
    """(text, kind) for the pace of a row. kind is "ahead" (a warn chip), "behind" (an accent chip) or "plain". The
    points are signed ASCII digits, as before #95: a whole number under 1000 and its sign are written alike in every
    language the app has (no group sign; CLDR's minus is "-" in each)."""
    words = _words(locale)
    delta = pace_delta(row.percent, row.time_fraction)
    if delta is None or row.pace is None:
        return words.text("widget.night_city.pace.none"), "plain"
    if row.pace == "on_pace":
        return words.text("widget.night_city.pace.on"), "plain"
    points = round(abs(delta))
    if row.pace == "ahead":
        return words.text("widget.night_city.pace.ahead", {"points": f"+{points}"}), "ahead"
    return words.text("widget.night_city.pace.behind", {"points": f"-{points}"}), "behind"


def _draw_info(ink: _Ink, look: Look, model: WidgetModel, row: Row, reset: str | None, top: float) -> None:
    """Reset time on the left, pace on the right: a chip when it is news (ahead, under), plain text otherwise; the pace
    shortens, then goes, where the line has no room for it (_info_plan). ``reset`` is the row's reset text."""
    pal = ink.pal
    info = _info_plan(ink, look, model, row)
    if reset is not None:
        width = ink.width(reset, RESET) + (8 if look.boxed else 0)
        _draw_reset(ink, look, reset, (LEFT, LEFT + width), top)
    if info.pace is None:
        return
    if not info.chip:
        ink.text(info.pace, CHIP, RIGHT, top + TAG_BASE, pal.ink2 if row.pace else pal.ink3, align="right")
        return
    colour = (pal.ink if pal.plate else pal.warn) if info.kind == "ahead" else pal.accent
    _chip_out(ink, RIGHT - _chip_out_width(ink, info.pace), top, info.pace, colour, colour)


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


# ── Tooltip: the usage guard's line ───────────────────────────────────────────

def _guard_name(guard: GuardMark, key: str, locale: Locale) -> str:
    """A limit the guard watches, in capitals, worded from its kind as the rows are: "SESSION", "WEEKLY",
    "FABLE WEEKLY" (the name in the language's capitals), "WEEKLY SCOPED". A kind this skin has no words for, and a
    limit the reading no longer carries, keep the guard's own label for it, in its own language's capitals (English's
    for a label that is data)."""
    words = _words(locale)
    kind = guard.kinds.get(key)
    if kind in LIMIT_KEYS:
        return words.text(LIMIT_KEYS[kind])
    if kind == "weekly_scoped":
        scope = guard.scope_names.get(key)
        if scope:
            return words.text(SCOPED_KEY, {"name": casing.upper(scope, locale.language)})
        return words.text(UNNAMED_SCOPED_KEY)
    return casing.upper(guard.labels.get(key, key))


def _lead(lead: str, lead_to_list: str, rest: str) -> tuple[str, str]:
    """(lead, rest): the lead that introduces a list ("GUARD // ARMED:", its colon the language's) only when something
    follows it."""
    return (lead_to_list if rest else lead), rest


def _guard_statement(model: WidgetModel) -> tuple[str, str] | None:
    """(lead, rest) of the hover card's guard line, in capitals and the theme's slashes, or None while the guard is
    off: ("GUARD // ARMED:", "SESSION 95%, WEEKLY 90%") and ("GUARD // HOLDING:",
    "WEEKLY 91% (CAP 90%). RESUMES FRI 7:12 PM (IN 2D 3H)."). A guard with nothing to list (no caps) is its lead
    alone, without the colon: ("GUARD // ARMED", ""). Each limit, its cap, the resume time and the time left are glued,
    so a wrap keeps each on one line. The ", " between limits, the space before a cap and the closing periods are the
    code's: the four languages write them alike."""
    guard, locale = model.guard, model.locale
    if guard is None:
        return None
    words = _words(locale)
    if not guard.holding:
        caps = ", ".join(engine.glue(words.text("widget.night_city.guard.limit", {
            "name": _guard_name(guard, key, locale), "percent": formats.percent(_number(cap), locale)}))
            for key, cap in guard.caps.items())
        return _lead(words.text("widget.night_city.guard.armed"), words.text("widget.night_city.guard.armed_list"),
                     caps)
    limits = []
    for key in guard.over_keys:
        name, reading, cap = _guard_name(guard, key, locale), guard.readings.get(key), guard.caps.get(key)
        text = engine.glue(name if reading is None else words.text("widget.night_city.guard.limit", {
            "name": name, "percent": formats.percent(_digits(reading), locale)}))
        if cap is not None:
            text += " " + engine.glue(words.text("widget.night_city.guard.cap",
                                                 {"percent": formats.percent(_number(cap), locale)}))
        limits.append(text)
    rest = ", ".join(limits)
    rest = f"{rest}." if rest else ""
    if guard.resume_seconds is not None and guard.resume_clock:
        clock = engine.glue(casing.upper(guard.resume_clock, locale.language))
        time_left = engine.glue(words.text("widget.night_city.guard.time_left",
                                           {"countdown": format_countdown(guard.resume_seconds, locale)}))
        rest = f"{rest} {words.text('widget.night_city.guard.resumes', {'clock': clock, 'time_left': time_left})}."
    return _lead(words.text("widget.night_city.guard.holding"), words.text("widget.night_city.guard.holding_list"),
                 rest.strip())


def _guard_lines(ink: _Ink, model: WidgetModel) -> tuple[str, list[str]]:
    """(the lead, the rest of the guard's line broken to the card's width), measured on ``ink`` (any canvas of the
    frame's scale). The first line leaves room for the lead. No lines while the guard is off; a guard with nothing to
    list is its lead alone. The lead is a run of its own, never wrapped and never glued, so it comes back as the
    catalog writes it and its colon stays with it, whatever space the language puts before it."""
    statement = _guard_statement(model)
    if statement is None:
        return "", []
    lead, rest = statement
    if not rest:
        return lead, []
    room = CONTENT_W - ink.width(lead, CHIP) - GUARD_GAP
    return lead, engine.wrap_words(rest, lambda line: ink.width(line, RESET), CONTENT_W, room)


def _draw_guard_block(ink: _Ink, model: WidgetModel, lead: str, lines: list[str], last_top: float) -> None:
    """A hairline under the last row and the guard's line under it: the lead in bold, in the danger colour while the
    guard holds, then the rest in the body tone, running on from it and wrapping to the left edge."""
    pal = ink.pal
    holding = model.guard is not None and model.guard.holding
    rule = last_top + TOOLTIP_INFO + CHIP_H + GUARD_GAP - 2
    ink.rect(LEFT, rule, RIGHT, rule + 1, pal.track)
    top = rule + 6
    lead_w = ink.text(lead, CHIP, LEFT, top + TAG_BASE, pal.value_crit if holding and not model.stale else pal.fg)
    for index, line in enumerate(lines):
        x = LEFT + lead_w + GUARD_GAP if index == 0 else LEFT
        ink.text(line, RESET, x, top + TAG_BASE + GUARD_LINE_H * index, pal.fg if holding else pal.ink2)


# ── Taskbar strip ─────────────────────────────────────────────────────────────

def _draw_strip_row(ink: _Ink, look: Look, model: WidgetModel, row: Row | None, top: float, label: str) -> None:
    pal = ink.pal
    percent = row.percent if row is not None else None
    emphasis = _row_emphasis(model, row) if row is not None else SEVERITY_NORMAL
    spec = _strip_label(ink, label, STRIP_BAR_X - STRIP_LABEL_X - STRIP_LABEL_AIR)
    if spec is not None:                # a label too long even at 9 px is left out: the bars' order tells them apart
        ink.text(label, spec, STRIP_LABEL_X, top + STRIP_TEXT_BASE, pal.fg_hi)

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

    # A box sized for English's "100%": no space before the sign in any language (Chris's decision on #91).
    text = "--" if percent is None else formats.percent(_digits(percent), model.locale, tight=True)
    spec = _fit_type(ink, text, STRIP_PCT, STRIP_PCT_RIGHT - bar_end - 2, 8.0)
    color = pal.fg_hi
    if emphasis == SEVERITY_WARNING:
        color = pal.value_warn
    elif emphasis == SEVERITY_CRITICAL:
        color = pal.value_crit
    ink.text(text, spec, STRIP_PCT_RIGHT, top + STRIP_TEXT_BASE, color, align="right")


def _draw_strip_status(ink: _Ink, look: Look, model: WidgetModel, motion: Motion) -> None:
    """The status word. While live it sits after the pulsing dot, as the design shows. Any other state drops
    the dot and centres its word, dim, in the tag. While the usage guard holds, the tag is the HOLD chip instead:
    the strip has no room for the countdown, and the status is in the dim of the rows. Each word shrinks to STRIP_FLOOR
    (_fit_type) and only then is cut; Blackwall's outlined tag grows down round marks below (Q1)."""
    pal = ink.pal
    words = _words(model.locale)
    x0, y0, x1, y1 = STRIP_TAG
    if _hold_word(model) is not None:
        word = words.text(HOLD_KEY)
        room = x1 - x0 - 17
        spec = _fit_type(ink, word, CHIP, room, STRIP_FLOOR)
        word = _cut(ink, word, spec, room)
        _chip_hold(ink, (x0 + x1 - _chip_hold_width(ink, word, spec)) / 2, y0, word, y1 - y0, glitch=not model.stale,
                   spec=spec)
        return
    live = model.status is ApiStatus.LIVE and not model.stale
    word = words.text(STATE_KEYS[model.status])
    room = x1 - 4 - STRIP_WORD_X if live else x1 - x0 - 8
    spec = _fit_type(ink, word, STATUS, room, STRIP_FLOOR)
    word = _cut(ink, word, spec, room)
    if pal.plate:
        ink.rect(x0, y0, x1, y1, pal.ink)
    elif look.boxed:
        up, down = _mark_growth(word, "tag")
        ink.outline(x0, y0 - up, x1, y1 + down, pal.line)
    if live:
        _pip(ink, STRIP_DOT[0], STRIP_DOT[1], pal.ok, motion.pulse, glow=False)
        ink.text(word, spec, STRIP_WORD_X, STRIP_WORD_BASE, pal.ok)
        return
    centre = (x0 + x1) / 2 + spec.tracking / 2
    ink.text(word, spec, centre, STRIP_WORD_BASE, pal.stale, align="centre")


def _render_strip(model: WidgetModel, theme: Any, variant: Any, scale: float, opts: RenderOptions) -> Image.Image:
    look = look_for(variant)
    pal = _palette(theme, variant, model, opts)
    cv = _canvas(STRIP_W, STRIP_H, scale, theme)
    _paint_frame(cv, pal, STRIP_W, STRIP_H, STRIP_CHAMFER, opts)
    ink = _Ink(cv, pal, opts)
    rows = _rows(model)[:2]
    labels = [_words(model.locale).text(key) for key in WINDOW_KEYS]
    for index, top in enumerate(STRIP_ROWS):
        _draw_strip_row(ink, look, model, rows[index] if index < len(rows) else None, top, labels[index])
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
        pal = _palette(theme, variant, model, o)
        lead, guard_lines = _guard_lines(_Ink(_canvas(1, 1, scale, theme), pal, o), model)   # measured at this scale
        height = _tooltip_height(len(rows))
        if model.guard is not None:
            height += GUARD_BLOCK_PAD + GUARD_LINE_H * max(1, len(guard_lines))
        cv = _canvas(FLOAT_W, height, scale, theme)
        _paint_frame(cv, pal, FLOAT_W, height, CHAMFER, o)
        ink = _Ink(cv, pal, o)
        _draw_surface(ink, theme, look_for(variant), model, rows, TOOLTIP_PITCH, motion_for(model, o), height, True)
        if model.guard is not None:
            _draw_guard_block(ink, model, lead, guard_lines, ROW_TOP + TOOLTIP_PITCH * (max(2, len(rows)) - 1))
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
