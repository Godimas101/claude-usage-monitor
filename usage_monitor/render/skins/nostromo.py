"""Nostromo skin, "Phosphor Console": the approved amber-on-CRT design (design/mockups/nostromo).

Geometry is the approved 1x layout in logical pixels (see design/mockups/nostromo/DESIGN_NOTES.md
and the CSS in final.html). Canvas draws it at twice the scale and downsamples, so every surface
is crisp at 1, 1.25, 1.5 and 2. Every colour comes from the variant tokens through Palette, so
all six variants render with the same code and no per-variant rules.

Draw order per frame: background, glow halos, crisp marks, then scanlines and the hazard band.
That is the CSS painting order, with halos kept under the marks they belong to.

Its words are catalog keys (widget.nostromo.*, #94), read in the model's language with English behind it and drawn as
written: their text is in capitals in every language. A name or a clock put into one is in the language's capitals
first (casing), and an age or a countdown is written by the view model (format_age_short, format_countdown).
"""
from __future__ import annotations

import functools
import math
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from usage_monitor import casing, formats, i18n
from usage_monitor import themes as theme_registry
from usage_monitor.themes import colour as colour_math
from usage_monitor.data.models import ApiStatus
from usage_monitor.i18n import ENGLISH, Locale
from usage_monitor.render import engine
from usage_monitor.render.engine import Canvas, Color, Layer, TRANSPARENT, parse_color, with_alpha
from usage_monitor.render.model import (
    LAMP_LIMITED,
    LAMP_OFFLINE,
    LAMP_PAUSED,
    LOGIN_COMMAND,
    NO_INSETS,
    PRODUCT,
    SEVERITY_CRITICAL,
    SEVERITY_WARNING,
    STALE_EXPIRED,
    STALE_NO_LOGIN,
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
    worst_severity,
)
from usage_monitor.render.skins import RenderOptions, coerce_options, face_donors

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
    # label x, bar x, value right edge, the window tag's key (WINDOW_KEYS: "5H" and "7D" in English)
    (22.0, 44.0, 126.0, "widget.nostromo.window.session"),
    (144.0, 166.0, 256.0, "widget.nostromo.window.weekly"),
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
_DONORS: dict[tuple[str, tuple[str, ...]], dict[str, tuple[engine.Donor, ...]]] = {}
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

# The skin's words (#94): catalog keys, in capitals in every language. en.json's English for each is what the skin drew
# before #94 (tests/fixtures/skin_words_english.json). The header's right-hand word, by _state_word's state:
STATE_KEYS: dict[str, str] = {
    "normal": "widget.nostromo.state.normal",                   # NOMINAL
    "warning": "widget.nostromo.state.warning",                 # ADVISORY
    "critical": "widget.nostromo.state.critical",               # CRITICAL, in a chip
    "stale": "widget.nostromo.state.stale",                     # LAST KNOWN
    "offline": "widget.nostromo.state.offline",                 # NO CARRIER
    "paused": "widget.nostromo.state.paused",                   # HOLD
    "loading": "widget.nostromo.state.loading",                 # ACQUIRING
    "rate_limited": "widget.nostromo.state.rate_limited",       # THROTTLED
    "no_credentials": "widget.nostromo.state.no_credentials",   # AUTH REQUIRED
}

# The dashed auth box's two lines for each reason the user must act on the sign-in (WidgetModel.stale_reason). A lost
# sign-in has the approved mockup's wording (final.html, "stale3"); a sign-in the usage API turned down (HTTP 403) needs
# a new one, and the command keeps its case (#55); a missing one has no mockup wording, so it reads as the view model's
# neutral text does. {product} is Claude Code's name in the language's capitals, {command} the command as typed.
AUTH_KEYS: dict[str, tuple[str, str]] = {
    STALE_EXPIRED: ("widget.nostromo.auth.expired.line1", "widget.nostromo.auth.expired.line2"),
    STALE_REFUSED: ("widget.nostromo.auth.refused.line1", "widget.nostromo.auth.refused.line2"),
    STALE_NO_LOGIN: ("widget.nostromo.auth.no_login.line1", "widget.nostromo.auth.no_login.line2"),
}

# A row's or a guarded limit's name by kind. A scoped one is named for its model or surface ("FABLE WEEKLY"), the name
# in the language's capitals.
LIMIT_KEYS: dict[str, str] = {"session": "widget.nostromo.limit.session", "weekly_all": "widget.nostromo.limit.weekly"}
SCOPED_KEY = "widget.nostromo.limit.scoped"
UNNAMED_SCOPED_KEY = "widget.nostromo.limit.scoped_unnamed"
# The session and weekly rows' tags ("5H", "7D"), which the strip's labels are too (STRIP_GROUPS).
WINDOW_KEYS: dict[str, str] = {"session": "widget.nostromo.window.session", "weekly": "widget.nostromo.window.weekly"}

# ── Fits (#94): what gives way where a language's words are longer than English's ──
# English never meets any of them (tests/test_skin_nostromo_fit.py), so its frames are as before #94.

# Q2, an owner default on #94 that Chris may overturn: what the header gives up, in this order, while its left side
# (the pill and the age) and its right side (the state word or chip) do not fit QUALIFIER_GAP apart. The armed mark
# goes before any of them, as before #94: it is drawn only in the room the rest leaves. The state word, or the CRITICAL
# chip, is cut first (with an ellipsis): it mostly repeats the pill, and in LIVE the colours and the hazard band carry
# the severity. Then the age goes, then the pill's word is cut (one letter at least), and last the hold chip drops its
# countdown and reads HOLD alone: the age and the countdown are facts the floating widget shows nowhere else. A word cut
# at one step takes back the room a later step frees, as far as its whole text. _header_plan follows this tuple.
HEADER_GIVE_WAY = ("state_word", "age", "pill", "countdown")
# The hover card's reset-and-pace line: the pace shortens to its signed points ("+21 PTS"), then is left out. The reset
# never gives way: it is what people look for there. _info_plan follows this tuple.
INFO_GIVE_WAY = ("pace", "pace_short")
# The sign-in box's lines shrink before anything is cut: the tracking to 0, then 9 px. Only a line still too long at
# 9 px is cut, with an ellipsis. Both lines take the same step, so the box reads as one (_message_fit).
MESSAGE_STEPS = ((LABEL_SIZE, MESSAGE_TRACKING), (LABEL_SIZE, 0.0), (9.0, 0.0))
# The strip's window tags shrink from 11 px to 9 px, their tracking with them (0.1 em), then are left out: the bars'
# order still tells the two windows apart (_strip_tag).
STRIP_TAG_SIZES = (11.0, 10.5, 10.0, 9.5, 9.0)

# Q1, an owner default on #94 that Chris may overturn: the status pill, the header's chip (CRITICAL, HOLD) and a cap tag
# grow round a word whose marks would touch their edge. It is decided by the word's characters, so a box is the same at
# every scale (_mark_growth). A capital with a mark above (É, Ñ, İ, Ğ) reaches the 12 px box's top edge, so an outlined
# box (the pill, a cap tag) grows MARK_GROWTH_UP px up to keep its 1 px stroke off the mark, and the filled chip
# CHIP_GROWTH_UP px to keep the mark inside it. A mark below (Ç, Ş, Ą, Ș), or a glyph that drops below the line
# (DESCENDERS), grows any of them MARK_GROWTH_DOWN px down: the header's box then ends 1 px over the rule (RULE_Y).
# English and every word without such characters never move. The conflict: the grown pill's top is then y = 4, the
# hazard band's bottom edge (HAZARD_H), so in a LIVE frame with a critical limit (the only one with the band) the two
# meet with no air between, though they never overlap; 1 px up would keep that air but leaves the stroke on the mark
# in the hover card at 1.25x (that canvas's height rounds its vertical scale up to 0.5 % off its horizontal one, and a
# 1 px growth has no slack). The chip, a fill, keeps 1 px of air under the band.
MARK_GROWTH_UP = 2
CHIP_GROWTH_UP = 1
MARK_GROWTH_DOWN = 2
# The characters with no mark below whose ink reaches the 12 px box's bottom edge in IBM Plex Mono SemiBold at 10 px at
# some display scale (tests/test_skin_nostromo_fit.py holds the list to the face).
DESCENDERS = frozenset("$(),/;@Q[\\]_gjpqy{|}¡¿")
_ABOVE, _BELOW = 230, (202, 220)        # the canonical combining classes of a mark above, and of one below


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


def _donors(theme: Any, faces: Mapping[str, Path]) -> dict[str, tuple[engine.Donor, ...]]:
    """The fallback faces of each face file (face_donors), resolved once per theme and set of files. The Nostromo
    theme declares none."""
    key = (str(getattr(theme, "id", "nostromo")), tuple(str(path) for path in faces.values()))
    if key not in _DONORS:
        _DONORS[key] = face_donors(theme, _FACE_ROLES, faces)
    return _DONORS[key]


def _canvas(width: float, height: float, scale: float, theme: Any) -> Canvas:
    cv = Canvas(width, height, scale)
    cv.faces = _faces(theme)
    cv.donors = _donors(theme, cv.faces)
    return cv


def _palette(theme: Any, variant: Any, model: WidgetModel) -> Palette:
    palette = Palette.for_variant(theme, variant)
    return palette.stale_tones() if model.stale else palette


# ── Shared model reading ──────────────────────────────────────────────────────

def _words(locale: Locale) -> i18n.Catalog:
    """The catalog the skin's words come from: the locale's language's, English behind it (#94). It comes with the
    model, so the skin never works out a language itself."""
    return i18n.app_catalog(locale.language)


def _visible_rows(model: WidgetModel) -> list[Row]:
    """The widget's rows. A message box takes the third slot, so only the first two rows show."""
    rows = list(model.rows)
    return rows[:2] if _message_lines(model) is not None else rows


def _frame_height(model: WidgetModel) -> int:
    """94 px for two rows, 126 for three (the stale message box takes the third slot)."""
    rows = max(2, len(_visible_rows(model)))
    if _message_lines(model) is not None:
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


def _percent_text(percent: float | None, locale: Locale, *, tight: bool = False) -> str:
    """A value rounded half up, in ``locale``: "13%", "13 %" in French, "%13" in Turkish. ``tight`` drops the space
    (the strip's values are sized for English's "100%"; Chris's decision on #91)."""
    if percent is None:
        return "--"
    return formats.percent(str(int(percent + 0.5) if percent >= 0 else 0), locale, tight=tight)


def _exact_percent_text(percent: float | None, locale: Locale) -> str:
    """The hover card's value to a tenth, ".0" dropped, in ``locale``: "33.5%", "33,5 %" in French."""
    if percent is None:
        return "--"
    text = f"{percent:.1f}"
    if text.endswith(".0"):
        text = text[:-2]
    return formats.percent(text, locale)


def _qualifier(row: Row, locale: Locale) -> str | None:
    """The tag after a row's name: its window ("5H", "7D"), or a scoped limit's share ("OF 25% SHARE")."""
    if row.key in WINDOW_KEYS:
        return _words(locale).text(WINDOW_KEYS[row.key])
    if row.share_pct is None:
        return None
    return _words(locale).text("widget.nostromo.share.of",
                               {"percent": formats.percent(str(round(row.share_pct)), locale)})


def _short_qualifier(row: Row, locale: Locale) -> str | None:
    """The qualifier when the full one does not fit: a scoped limit's share on its own ("25%")."""
    if row.key in ("session", "weekly") or row.share_pct is None:
        return _qualifier(row, locale)
    return formats.percent(str(round(row.share_pct)), locale)


def _row_names(row: Row, *, locale: Locale = ENGLISH) -> tuple[str, str]:
    """(the row's name, its shorter form for a tight line). A scoped limit is named for its model, else its surface:
    ("FABLE WEEKLY", "FABLE"), the name in the language's capitals ("HAİKU WEEKLY" in Turkish), and ("WEEKLY SCOPED",
    "WEEKLY SCOPED") when it has neither. A kind this skin has no words for is named from its code
    (unknown_kind_names), and a row built without a kind keeps its label whole."""
    words = _words(locale)
    if row.kind in LIMIT_KEYS:
        name = words.text(LIMIT_KEYS[row.kind])
        return name, name
    if row.kind == "weekly_scoped":
        if not row.scope_name:
            name = words.text(UNNAMED_SCOPED_KEY)
            return name, name
        scope = casing.upper(row.scope_name, locale.language)
        name = words.text(SCOPED_KEY, {"name": scope})
        return name, scope.strip() or name
    if row.kind:
        return unknown_kind_names(row.kind)
    return row.label, row.label


def _name_line(cv: Canvas, row: Row, value: str, semi: Any, reg: Any, vt: Any,
               chip: float = 0.0, *, locale: Locale) -> tuple[str, str | None]:
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

    qualifier, short = _qualifier(row, locale), _short_qualifier(row, locale)
    full, shorter = _row_names(row, locale=locale)
    for name, kept in ((full, qualifier), (full, short), (shorter, short)):
        if fits(name, kept):
            return name, kept
    cut = shorter
    while len(cut) > 1 and not fits(cut.rstrip() + ELLIPSIS, short):
        cut = cut[:-1]
    cut = cut.rstrip() + ELLIPSIS
    return (cut, short) if fits(cut, short) else (cut, None)


def _age_text(model: WidgetModel) -> str | None:
    """The age after the status pill ("12 S", "41 M"; "41MIN" in French), shown while the values are live or
    last-known."""
    if model.age_seconds is None or model.status not in (ApiStatus.LIVE, ApiStatus.STALE, ApiStatus.RATE_LIMITED):
        return None
    return format_age_short(model.age_seconds, model.locale)


def _hold_word(model: WidgetModel) -> str | None:
    """The header's chip while the usage guard (#27) holds: "HOLD 2H 14M", or "HOLD" when no reset time is known.
    None when the guard is off or only armed. The countdown is always the time left ("HOLD 2D 3H"): the clock time of
    a resume that far away is the tooltip's."""
    guard = model.guard
    if guard is None or not guard.holding:
        return None
    words = _words(model.locale)
    if guard.resume_seconds is None:
        return words.text("widget.nostromo.guard.hold")
    return words.text("widget.nostromo.guard.hold_for",
                      {"countdown": format_countdown(guard.resume_seconds, model.locale)})


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
    if cap is None:
        return None
    return _words(model.locale).text("widget.nostromo.cap.tag",
                                     {"percent": formats.percent(_number(cap), model.locale)})


def _number(value: float) -> str:
    """A cap as people write it: 95, not 95.0. ASCII digits, for formats.number or formats.percent to write."""
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _state_word(model: WidgetModel) -> tuple[str, str]:
    """(word, style) for the header's right-hand word. style is "label", "bright" or "chip". While the guard holds
    the word is the hold chip: a held account is the state that matters, and the status pill beside it says the rest."""
    hold = _hold_word(model)
    if hold is not None:
        return hold, "chip"
    words = _words(model.locale)
    status = model.status
    if status is ApiStatus.LIVE:
        worst = worst_severity(model.rows) if not model.stale else "normal"
        if worst == SEVERITY_CRITICAL:
            return words.text(STATE_KEYS["critical"]), "chip"
        if worst == SEVERITY_WARNING:
            return words.text(STATE_KEYS["warning"]), "bright"
        return words.text(STATE_KEYS["normal"]), "label"
    keys = {
        ApiStatus.STALE: "stale",
        ApiStatus.OFFLINE: "offline",
        ApiStatus.PAUSED: "paused",
        ApiStatus.LOADING: "loading",
        ApiStatus.RATE_LIMITED: "rate_limited",
        ApiStatus.NO_CREDENTIALS: "no_credentials",
    }
    return words.text(STATE_KEYS[keys[status]]), "label"


# ── Fits: the decisions, as pure helpers the tests read ───────────────────────

PILL_X = LEFT + 6 + 6           # the status pill's left edge: past the 6 px status mark and a 6 px gap


@dataclass(frozen=True, slots=True)
class HeaderPlan:
    """What the header draws (_header_plan): each text as drawn, the armed guard's mark (None when there is none or no
    room for it), and the HEADER_GIVE_WAY steps taken, in order (none while the line fits)."""
    pill: str
    age: str | None
    word: str
    style: str
    mark: str | None
    gave_way: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class InfoPlan:
    """What the hover card's reset-and-pace line draws (_info_plan): the reset, the pace as drawn (whole, its short form
    or None) and the INFO_GIVE_WAY steps taken."""
    reset: str
    pace: str | None
    gave_way: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MessageFit:
    """The sign-in box's lines as drawn (_message_fit): their size and tracking (MESSAGE_STEPS) and the lines, each cut
    only when it does not fit at the last step."""
    size: float
    tracking: float
    lines: tuple[str, ...]


def _cut(cv: Canvas, text: str, room: float, font: Any, tracking: float) -> str:
    """``text`` whole when it fits ``room``, else cut with an ellipsis to fit, keeping one letter at least."""
    if cv.advance(text, font, tracking) <= room:
        return text
    cut = text
    while len(cut) > 1 and cv.advance(cut.rstrip() + ELLIPSIS, font, tracking) > room:
        cut = cut[:-1]
    return cut.rstrip() + ELLIPSIS


def _header_plan(cv: Canvas, model: WidgetModel, left: float = PILL_X, right: float = RIGHT) -> HeaderPlan:
    """The header's texts as they fit between ``left`` (the pill's edge) and ``right``, measured on ``cv``: the pill
    (10 px wider than its word) and the age 6 px after it on the left, the state word or chip (8 px wider) on the right,
    QUALIFIER_GAP apart. While they do not fit, the HEADER_GIVE_WAY steps are taken in order until they do. The armed
    mark ([GUARD], else [G]) is chosen last, in the room left, ARMED_GAP off each side."""
    semi = cv.font(FONT_SEMI, LABEL_SIZE)
    reg = cv.font(FONT_REG, LABEL_SIZE)
    words = _words(model.locale)
    pill, age = str(model.status_text), _age_text(model)
    word, style = _state_word(model)
    holding = _hold_word(model) is not None
    pad = 8 if style == "chip" else 0
    room = right - left

    def pill_w(text: str) -> float:
        return 10 + cv.advance(text, semi, LABEL_TRACKING)

    def age_w(text: str | None) -> float:
        return 6 + cv.advance(text, reg, QUALIFIER_TRACKING) if text else 0.0

    def word_w(text: str) -> float:
        return cv.advance(text, semi, LABEL_TRACKING) + pad

    def fits(p: str, a: str | None, w: str) -> bool:
        return pill_w(p) + age_w(a) + QUALIFIER_GAP + word_w(w) <= room

    drawn_pill, drawn_age, drawn_word = pill, age, word
    taken: list[str] = []
    if not fits(pill, age, word):
        for step in HEADER_GIVE_WAY:
            if step == "state_word" and not holding:
                drawn_word = _cut(cv, word, room - pill_w(pill) - age_w(age) - QUALIFIER_GAP - pad, semi,
                                  LABEL_TRACKING)
            elif step == "age" and drawn_age is not None:
                drawn_age = None
                if "state_word" in taken:       # the word takes back what the age leaves
                    drawn_word = _cut(cv, word, room - pill_w(pill) - QUALIFIER_GAP - pad, semi, LABEL_TRACKING)
            elif step == "pill":
                if "state_word" in taken:       # the word gave way first: it keeps its one letter
                    drawn_word = _cut(cv, word, 0.0, semi, LABEL_TRACKING)
                drawn_pill = _cut(cv, pill, room - 10 - age_w(drawn_age) - QUALIFIER_GAP - word_w(drawn_word), semi,
                                  LABEL_TRACKING)
            elif step == "countdown" and holding and model.guard is not None \
                    and model.guard.resume_seconds is not None:
                drawn_word = str(words.text("widget.nostromo.guard.hold"))
                drawn_pill = _cut(cv, pill, room - 10 - age_w(drawn_age) - QUALIFIER_GAP - word_w(drawn_word), semi,
                                  LABEL_TRACKING)
            else:
                continue
            taken.append(step)
            if fits(drawn_pill, drawn_age, drawn_word):
                break
    mark = None
    if model.guard is not None and not model.guard.holding:
        left_end = left + pill_w(drawn_pill) + age_w(drawn_age)
        word_left = right - word_w(drawn_word)
        for key in ("widget.nostromo.guard.mark", "widget.nostromo.guard.mark_short"):
            text = str(words.text(key))
            if left_end + ARMED_GAP + cv.advance(text, semi, LABEL_TRACKING) <= word_left - ARMED_GAP:
                mark = text
                break
    return HeaderPlan(drawn_pill, drawn_age, drawn_word, style, mark, tuple(taken))


def _pace_short(row: Row, *, locale: Locale = ENGLISH) -> str | None:
    """The pace's short form for a tight hover line: the signed points alone ("+21 PTS"), in the full form's tone.
    None for a pace that has no points ("ON PACE", "PACE --"). English never draws it: its lines fit whole."""
    delta = pace_delta(row.percent, row.time_fraction)
    if delta is None or row.pace not in ("ahead", "behind"):
        return None
    points = f"{'+' if delta >= 0 else '-'}{abs(delta):.0f}"
    return _words(locale).text("widget.nostromo.pace.points", {"points": points})


def _info_plan(cv: Canvas, row: Row, locale: Locale) -> InfoPlan:
    """The hover line's reset (from LEFT) and pace (right-aligned at RIGHT), QUALIFIER_GAP apart at least, measured on
    ``cv``: while they do not fit, the pace takes its short form (_pace_short), then is left out (INFO_GIVE_WAY)."""
    semi = cv.font(FONT_SEMI, LABEL_SIZE)
    reg = cv.font(FONT_REG, LABEL_SIZE)
    reset = _reset_text(row, locale)
    room = RIGHT - LEFT - cv.advance(reset, reg, QUALIFIER_TRACKING) - QUALIFIER_GAP
    pace: str | None = _pace_word(row, locale)
    forms = {"pace": _pace_short(row, locale=locale), "pace_short": None}
    taken: list[str] = []
    for step in INFO_GIVE_WAY:
        if pace is None or cv.advance(pace, semi, LABEL_TRACKING) <= room:
            break
        pace = forms[step]
        taken.append(step)
    return InfoPlan(reset, pace, tuple(taken))


def _message_fit(cv: Canvas, lines: tuple[str, ...], room: float) -> MessageFit:
    """The sign-in box's lines at the first of MESSAGE_STEPS where every one fits ``room``; at the last step a line that
    still does not fit is cut with an ellipsis."""
    for size, tracking in MESSAGE_STEPS:
        semi = cv.font(FONT_SEMI, size)
        if all(cv.advance(line, semi, tracking) <= room for line in lines):
            return MessageFit(size, tracking, lines)
    return MessageFit(size, tracking, tuple(_cut(cv, line, room, semi, tracking) for line in lines))


def _strip_tag(cv: Canvas, text: str, room: float) -> tuple[float, float] | None:
    """(size, tracking) the strip draws a window tag at: the first of STRIP_TAG_SIZES where it fits ``room``, its
    tracking 0.1 em. None when it does not fit even at the last: the tag is left out."""
    for size in STRIP_TAG_SIZES:
        tracking = STRIP_LABEL_TRACKING * (size / STRIP_LABEL_SIZE)     # exactly STRIP_LABEL_TRACKING at 11 px
        if cv.advance(text, cv.font(FONT_SEMI, size), tracking) <= room:
            return size, tracking
    return None


def _mark_growth(text: str, *, filled: bool = False) -> tuple[int, int]:
    """(px up, px down) an outlined pill or cap tag, or with ``filled`` the header's chip, grows round ``text`` (Q1, see
    MARK_GROWTH_UP): up for a capital with a mark above, down for a mark below or one of DESCENDERS; (0, 0) for
    English's words."""
    up = down = False
    base = ""
    for ch in unicodedata.normalize("NFD", text):
        mark = unicodedata.combining(ch)
        if not mark:
            base = ch
            down = down or ch in DESCENDERS
        elif mark == _ABOVE:
            up = up or base.isupper()
        elif mark in _BELOW:
            down = True
    return ((CHIP_GROWTH_UP if filled else MARK_GROWTH_UP) if up else 0), (MARK_GROWTH_DOWN if down else 0)


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


def _pill(plan: _Plan, cv: Canvas, pal: Palette, text: str, age: str | None, x: float, y: float) -> float:
    """Status pill (1 px outline, 12 px tall, grown round marks: _mark_growth) and the age after it. Returns the age's
    right edge."""
    semi = cv.font(FONT_SEMI, LABEL_SIZE)
    reg = cv.font(FONT_REG, LABEL_SIZE)
    width = 2 + 4 + cv.advance(text, semi, LABEL_TRACKING) + 4
    up, down = _mark_growth(text)
    plan.mark(lambda c: c.outline_rect(x, y - up, x + width, y + HEADER_H + down, pal.rule_strong, 1))
    text_layer, _ = cv.text_layer(text, x + 5, cv.baseline_in_box(y + 1, 10, semi), semi, LABEL_TRACKING)
    plan.stamp(text_layer, pal.bright)
    right = x + width
    if age:
        age_layer, advance = cv.text_layer(age, right + 6, cv.baseline_in_box(y + 1, 10, reg), reg,
                                           QUALIFIER_TRACKING)
        plan.stamp(age_layer, pal.label)
        right = right + 6 + advance
    return right


def _header(plan: _Plan, cv: Canvas, pal: Palette, model: WidgetModel, opts: RenderOptions,
            dot_x: float, dot_y: float, top: float, right: float, dot_size: float = 6.0) -> None:
    """Status mark, pill and age on the left, severity word on the right, as _header_plan fits them. The usage guard
    adds nothing to the left while it holds (its chip takes the right-hand word, see _state_word) and a quiet [GUARD]
    after the age while it is armed."""
    _alive_mark(plan, cv, pal, model, opts, dot_x, dot_y, dot_size)
    head = _header_plan(cv, model, dot_x + dot_size + 6, right)
    left_end = _pill(plan, cv, pal, head.pill, head.age, dot_x + dot_size + 6, top)
    semi = cv.font(FONT_SEMI, LABEL_SIZE)
    word = head.word
    baseline = cv.baseline_in_box(top, HEADER_H, semi)
    if head.style == "chip":
        chip_w = cv.advance(word, semi, LABEL_TRACKING) + 8
        up, down = _mark_growth(word, filled=True)
        plan.mark(lambda c: c.fill_rect(right - chip_w, top - up, right, top + HEADER_H + down, pal.bright))
        layer, _ = cv.text_layer(word, right - 4, baseline, semi, LABEL_TRACKING, align="right")
        plan.stamp(layer, pal.ink)
    else:
        layer, _ = cv.text_layer(word, right, baseline, semi, LABEL_TRACKING, align="right")
        plan.stamp(layer, pal.bright if head.style == "bright" else pal.label)
    if head.mark is not None:
        _armed_mark(plan, cv, pal, head.mark, left_end, top)


def _armed_mark(plan: _Plan, cv: Canvas, pal: Palette, text: str, left_end: float, top: float) -> None:
    """The armed guard: "[GUARD]" in the label tone after the age, in the free stretch before the state word. A
    long status ("RATE LIMITED" and its age) leaves room for the short "[G]" only, and a header with no room at
    all draws nothing (_header_plan chooses): the mark is quiet by design."""
    semi = cv.font(FONT_SEMI, LABEL_SIZE)
    layer, _ = cv.text_layer(text, left_end + ARMED_GAP, cv.baseline_in_box(top + 1, 10, semi), semi, LABEL_TRACKING)
    plan.stamp(layer, pal.label)


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


def _message_lines(model: WidgetModel) -> tuple[str, ...] | None:
    """The lines of the dashed auth box, worded for the reason the user must act (AUTH_KEYS). None when the model
    asks for no box."""
    keys = AUTH_KEYS.get(model.stale_reason) if model.stale_reason is not None else None
    if keys is None:
        return None
    words = _words(model.locale)
    names = {"product": casing.upper(PRODUCT, model.locale.language), "command": LOGIN_COMMAND}
    return tuple(words.text(key, names) for key in keys)


def _message(plan: _Plan, cv: Canvas, pal: Palette, lines: tuple[str, ...], top: float, x0: float, x1: float,
             y1: float) -> None:
    """Dashed auth box with one or two lines of 10 px SemiBold text (see _message_lines), from 9 px in from its left
    edge to its right border; a line too long shrinks (_message_fit)."""
    _dashed_box(plan, cv, pal, x0, top, x1, y1)
    fit = _message_fit(cv, tuple(lines[:2]), (x1 - 1) - (x0 + 9))
    semi = cv.font(FONT_SEMI, fit.size)
    for index, line in enumerate(fit.lines):
        line_top = top + 2 + 12 * index
        layer, _ = cv.text_layer(line, x0 + 9, cv.baseline_in_box(line_top, 12, semi), semi, fit.tracking)
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
    up, down = _mark_growth(text)
    plan.mark(lambda c: c.outline_rect(x, top - up, x + width, top + HEADER_H + down, pal.bright, 1))
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
    value = _percent_text(row.percent, model.locale)
    label, qualifier = _name_line(cv, row, value, semi, reg, vt, VALUE_CHIP_PAD if chipped else 0.0,
                                  locale=model.locale)
    name_layer, name_w = cv.text_layer(label, LEFT, cv.baseline_in_box(top, 12, semi), semi, LABEL_TRACKING)
    plan.stamp(name_layer, pal.main)
    name_end = LEFT + name_w
    if qualifier:
        qual_layer, qual_w = cv.text_layer(qualifier, LEFT + name_w + QUALIFIER_GAP, cv.baseline_in_box(top, 12, reg),
                                           reg, QUALIFIER_TRACKING)
        plan.stamp(qual_layer, pal.label)
        name_end += QUALIFIER_GAP + qual_w
    value_left = RIGHT - cv.advance(value, vt) - (VALUE_CHIP_PAD if chipped else 0.0)
    _cap_tag(plan, cv, pal, model, row, name_end, value_left, top)

    # Value: 22 px VT323, right edge at 266, line box from top - 4.
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
        message = _message_lines(model)
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
        # measured on a canvas as wide as the card: a canvas's buffer factor follows its width at a fractional scale
        lead, guard_lines = _guard_lines(_canvas(FLOAT_W, 1, scale, theme), model)
        guard_h = GUARD_BLOCK_PAD + GUARD_LINE_H * len(guard_lines) if guard_lines else 0
        message = _message_lines(model)
        height = int(last_top + 56 + guard_h) if message is None else int(last_top + 92 + guard_h)
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

    value = _exact_percent_text(row.percent, model.locale)
    label, qualifier = _name_line(cv, row, value, semi, reg, vt, locale=model.locale)
    name_layer, name_w = cv.text_layer(label, LEFT, cv.baseline_in_box(top, 12, semi), semi, LABEL_TRACKING)
    plan.stamp(name_layer, pal.main)
    name_end = LEFT + name_w
    if qualifier:
        qual_layer, qual_w = cv.text_layer(qualifier, LEFT + name_w + QUALIFIER_GAP, cv.baseline_in_box(top, 12, reg),
                                           reg, QUALIFIER_TRACKING)
        plan.stamp(qual_layer, pal.label)
        name_end += QUALIFIER_GAP + qual_w
    _cap_tag(plan, cv, pal, model, row, name_end, RIGHT - cv.advance(value, vt), top)

    value_layer, _ = cv.text_layer(value, RIGHT, cv.baseline_in_box(top - 4, 22, vt), vt, align="right")
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
    info = _info_plan(cv, row, model.locale)
    info_layer, _ = cv.text_layer(info.reset, LEFT, cv.baseline_in_box(info_top, 12, reg), reg, QUALIFIER_TRACKING)
    plan.stamp(info_layer, pal.label)
    if info.pace is not None:
        _, pace_color = _pace_text(pal, row, locale=model.locale)
        pace_layer, _ = cv.text_layer(info.pace, RIGHT, cv.baseline_in_box(info_top, 12, semi), semi,
                                      LABEL_TRACKING, align="right")
        plan.stamp(pace_layer, pace_color)


def _reset_text(row: Row, locale: Locale) -> str:
    """The hover card's reset: "RESETS 11:39 PM", "RESETS SAT 8:12 PM" (the clock in the language's capitals:
    "RESETS CMT 20:12" in Turkish), "RESETS --" while the time is not known."""
    words = _words(locale)
    if not row.resets_text:
        return words.text("widget.nostromo.resets.unknown")
    return words.text("widget.nostromo.resets.at", {"clock": casing.upper(row.resets_text, locale.language)})


# ── Tooltip: the usage guard's line ───────────────────────────────────────────

def _guard_name(guard: GuardMark, key: str, locale: Locale) -> str:
    """A limit the guard watches, in capitals, worded from its kind as the rows are: "SESSION", "WEEKLY",
    "FABLE WEEKLY", "WEEKLY SCOPED". A kind this skin has no words for, and a limit the reading no longer carries,
    keep the guard's own label for it, in its own language's capitals (English's for a label that is data)."""
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
    """(lead, rest): the lead that introduces a list ("GUARD ARMED:") only when something follows it."""
    return (lead_to_list if rest else lead), rest


def _guard_statement(model: WidgetModel) -> tuple[str, str] | None:
    """(lead, rest) of the hover card's guard line, in capitals, or None while the guard is off:
    ("GUARD ARMED:", "SESSION 95%, WEEKLY 90%") and
    ("GUARD HOLDING:", "WEEKLY 91% (CAP 90%). RESUMES FRI 7:12 PM (IN 2D 3H)."). A guard with nothing to list (no
    caps) is its lead alone, without the colon: ("GUARD ARMED", ""). Each limit, its cap, the resume time and the time
    left are glued, so a wrap keeps each on one line. The ", " between limits, the space before a cap and the closing
    periods are the code's: the four languages write them alike."""
    guard, locale = model.guard, model.locale
    if guard is None:
        return None
    words = _words(locale)
    if not guard.holding:
        caps = ", ".join(engine.glue(words.text("widget.nostromo.guard.limit", {
            "name": _guard_name(guard, key, locale), "percent": formats.percent(_number(cap), locale)}))
            for key, cap in guard.caps.items())
        return _lead(words.text("widget.nostromo.guard.armed"), words.text("widget.nostromo.guard.armed_list"), caps)
    limits = []
    for key in guard.over_keys:
        name, reading, cap = _guard_name(guard, key, locale), guard.readings.get(key), guard.caps.get(key)
        text = engine.glue(name if reading is None else words.text("widget.nostromo.guard.limit", {
            "name": name, "percent": _percent_text(reading, locale)}))
        if cap is not None:
            text += " " + engine.glue(words.text("widget.nostromo.guard.cap",
                                                 {"percent": formats.percent(_number(cap), locale)}))
        limits.append(text)
    rest = ", ".join(limits)
    rest = f"{rest}." if rest else ""
    if guard.resume_seconds is not None and guard.resume_clock:
        clock = engine.glue(casing.upper(guard.resume_clock, locale.language))
        time_left = engine.glue(words.text("widget.nostromo.guard.time_left",
                                           {"countdown": format_countdown(guard.resume_seconds, locale)}))
        rest = f"{rest} {words.text('widget.nostromo.guard.resumes', {'clock': clock, 'time_left': time_left})}."
    return _lead(words.text("widget.nostromo.guard.holding"), words.text("widget.nostromo.guard.holding_list"),
                 rest.strip())


def _guard_lines(cv: Canvas, model: WidgetModel) -> tuple[str, list[str]]:
    """(the lead, the guard's line broken to the card's width), measured on ``cv`` (any canvas of the frame's scale).
    No lines while the guard is off; a guard with nothing to list is its lead alone. The lead is glued, so its
    colon stays with it (a French " :" never starts a line)."""
    statement = _guard_statement(model)
    if statement is None:
        return "", []
    lead, rest = statement
    reg = cv.font(FONT_REG, LABEL_SIZE)
    text = engine.glue(lead) + (f" {rest}" if rest else "")
    return lead, engine.wrap_words(text, lambda line: cv.advance(line, reg, QUALIFIER_TRACKING), RIGHT - LEFT)


def _split_lead(line: str, lead: str) -> tuple[str, str]:
    """(the lead, the rest) of the guard's first line. A first line the wrap ended inside the lead is all lead."""
    return (lead, line[len(lead):]) if line.startswith(lead) else (line, "")


def _guard_block(plan: _Plan, cv: Canvas, pal: Palette, lead: str, lines: list[str], last_top: float) -> None:
    """A rule under the last row and the guard's lines under it. The lead, at the start of the first line, is
    semibold and bright, the rest of it plain."""
    plan.mark(lambda c: c.fill_rect(LEFT, last_top + GUARD_RULE_OFFSET, RIGHT, last_top + GUARD_RULE_OFFSET + 1, pal.rule))
    semi = cv.font(FONT_SEMI, LABEL_SIZE)
    reg = cv.font(FONT_REG, LABEL_SIZE)
    for index, line in enumerate(lines):
        top = last_top + GUARD_TEXT_OFFSET + GUARD_LINE_H * index
        head, tail = _split_lead(line, lead) if index == 0 else ("", line)
        x = LEFT
        if head:
            layer, advance = cv.text_layer(head, x, cv.baseline_in_box(top, 12, semi), semi, QUALIFIER_TRACKING)
            plan.stamp(layer, pal.hot)
            x += advance
        if tail:
            layer, _ = cv.text_layer(tail, x, cv.baseline_in_box(top, 12, reg), reg, QUALIFIER_TRACKING)
            plan.stamp(layer, pal.main)


def _pace_text(pal: Palette, row: Row, *, locale: Locale = ENGLISH) -> tuple[str, Color]:
    """The hover card's pace and its tone: "AHEAD +21 PTS" (bright), "BEHIND -18 PTS", "ON PACE", or "PACE --" with
    no reading or no reset time. The points are signed ASCII digits, as before #94: a whole number under 1000 and its
    sign are written alike in every language the app has (no group sign; CLDR's minus is "-" in each)."""
    text = _pace_word(row, locale)
    return text, pal.bright if row.pace == "ahead" and pace_delta(row.percent, row.time_fraction) is not None \
        else pal.label


def _pace_word(row: Row, locale: Locale) -> str:
    """_pace_text's words, without the tone."""
    words = _words(locale)
    delta = pace_delta(row.percent, row.time_fraction)
    if delta is None or row.pace is None:
        return words.text("widget.nostromo.pace.none")
    if row.pace == "on_pace":
        return words.text("widget.nostromo.pace.on")
    points = f"{'+' if delta >= 0 else '-'}{abs(delta):.0f}"
    if row.pace == "ahead":
        return words.text("widget.nostromo.pace.ahead", {"points": points})
    return words.text("widget.nostromo.pace.behind", {"points": points})


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
    words = _words(model.locale)
    for index, (label_x, bar_x, value_right, window) in enumerate(STRIP_GROUPS):
        row = rows[index] if index < len(rows) else None
        percent = row.percent if row is not None else None
        emphasis = _row_emphasis(model, row) if row is not None else "normal"
        tag = words.text(window)
        fit = _strip_tag(cv, tag, bar_x - label_x)
        if fit is not None:
            size, tracking = fit
            font = semi if size == STRIP_LABEL_SIZE else cv.font(FONT_SEMI, size)
            baseline = label_baseline if size == STRIP_LABEL_SIZE else cv.baseline_in_box(STRIP_LABEL_TOP, 12, font)
            label_layer, _ = cv.text_layer(tag, label_x, baseline, font, tracking)
            plan.stamp(label_layer, pal.label)

        lit = _lit_color(pal, emphasis)
        full, fraction = engine.split_segments(percent, STRIP_CELLS)
        partial_w = round(fraction * STRIP_CELL_W)
        lit_rects = [(bar_x + STRIP_CELL_STEP * i, STRIP_BAR_Y, bar_x + STRIP_CELL_STEP * i + STRIP_CELL_W,
                      STRIP_BAR_Y + STRIP_BAR_H) for i in range(full)]
        plan.halo(cv.rects_layer(lit_rects), GLOW_STRIP_CELL_SIGMA, pal.glow_soft)
        _cell_bar(plan, cv, pal, bar_x, STRIP_BAR_Y, STRIP_CELL_STEP, STRIP_CELL_W, STRIP_BAR_H,
                  STRIP_CELLS, full, partial_w, lit)

        value = _percent_text(percent, model.locale, tight=True)       # a box sized for English's "100%"
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
