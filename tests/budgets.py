"""Width budgets (#89): the most room any language's text may take in each widget box sized for English, the lines
that hold several of them, and the character budgets of the dashboard's money figures, the tray tooltip and the toasts.

A box is one place where one run of text must stay within a width the skin fixes; a line is runs drawn side by side
that must fit it together (a header's word beside its status, a reset beside its pace). Each box is measured the way its
skin measures it, through the skin's own code: Nostromo on its own canvas (the floating widget's and the hover card's
280 px, the strip's 264 px, whose device pixels a glyph snaps to) at every display scale and at the smaller scales a
short taskbar draws the strip at; Night City through its ink (with its per-run advance correction) and its own fit
function where it shrinks text; Gates in its own faces; the tray icons' numbers in the icon's device pixels; character
boxes in UTF-16 units, as the tray counts them. ``drawn`` records the width each skin works out for every run the frame
dump draws, and test_budgets.py holds each box's measure to it and each drawn run to a box. Each limit is worked out
from the skin's constants and the widths of the English beside it (``why`` says how), and test_budgets.py pins them, so
a change to a skin that moves a room shows.

A text past its budget is a fault in a box that has no fallback or only shrinks its text (measured at the size it
shrinks to); a box that cuts its text with an ellipsis, clips it, leaves it out, or wraps it, and a line of such
boxes, gets a report instead. A pixel box's budget is its room, or what English already takes there where English
runs past it by design (the strip's 100%: KNOWN_ENGLISH_OVERRUNS). A character box's limit is a contract or a hard cut
(the money tiles' six characters, the tray's 127 units), so its budget is the limit, and only the English listed in
KNOWN_ENGLISH_OVERRUNS may pass it.

What goes in a box: English as the skins draw it today; each language's longest figures (FIGURES), set in the box's
text where the box holds one (``templates``), a percent without its space in a box sized for English's figures
(``Box.tight``: Chris's decision on #91); and, once the box's text moves to the catalog (#93 to #101), its keys in
that language. Until then the pseudo-locale measures that English through ``i18n.pseudo_text``. Durations and counts
get figures when #91 gives each language their format.

    python tests/budgets.py [--lang ID]     what a language's text would not fit (default: pseudo)
    python tests/budgets.py --limits        every box's and line's limit, as test_budgets.py pins them
"""
from __future__ import annotations

import argparse
import json
import math
import re
import string
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from usage_monitor import i18n  # noqa: E402
from usage_monitor.render import dump, engine  # noqa: E402
from usage_monitor.render.skins import RenderOptions  # noqa: E402
from usage_monitor.render.skins import gates as g  # noqa: E402
from usage_monitor.render.skins import night_city as nc  # noqa: E402
from usage_monitor.render.skins import nostromo as nos  # noqa: E402
from usage_monitor.ui import tray  # noqa: E402

# The display scales Windows offers up to 200 %, and the scales Nostromo's strip is drawn at on each taskbar height
# the frame dump draws (a bar shorter than the strip draws it smaller: nostromo.render_taskbar). Night City's and Gates'
# widths are the same at every scale (their ink lays out true advances), so they are measured once.
SCALES: tuple[float, ...] = (1.0, 1.25, 1.5, 1.75, 2.0)
STRIP_SCALES: tuple[float, ...] = tuple(sorted({
    s if round(nos.STRIP_H * s) <= round(h * s) else max(round(h * s) / nos.STRIP_H, nos.STRIP_MIN_SCALE)
    for s in SCALES for h in dump.TASKBAR_HEIGHTS_AT_1X}))
TRAY_SIZE = 32                      # the tray icon that carries a number (both skins draw it at 32 px)

# Each language's longest figures, as #89 lists them: percents with U+00A0 or with the sign first, clocks in both 12-
# and 24-hour forms (fr-CA's "16 h 12", es-MX's "4:12 p.m."), and the dollar as fr-CA and es-MX write it. #89 names
# no Turkish dollar: Turkish money gets a figure once L1 (#86) settles how the dollar is marked.
FIGURES: dict[str, dict[str, tuple[str, ...]]] = {
    "percent": {"en": ("100%",), "fr": ("100 %",), "es": ("100 %",), "tr": ("%100",)},
    "clock": {"en": ("4:12 PM",), "fr": ("16 h 12", "16:12"), "es": ("4:12 p.m.", "16:12"), "tr": ("16:12",)},
    "money": {"en": ("$5.96",), "fr": ("5,96 $ US",), "es": ("USD 5.96", "1,234.5")},
}
_KINDS = ("percent", "clock", "money", "duration", "count")
_FITS = ("none", "shrink", "ellipsis", "clip", "drop", "wrap")


# ── Measuring as each skin does ───────────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def _theme(skin: str) -> Any:
    return dump.load_theme_objects()[skin]


@lru_cache(maxsize=None)
def _nos_canvas(width: float, height: float, scale: float) -> Any:
    return nos._canvas(width, height, scale, _theme("nostromo"))


@lru_cache(maxsize=None)
def _nc_ink() -> Any:
    return nc._Ink(nc._canvas(8, 8, 1.0, _theme("night_city")), None, RenderOptions())


@lru_cache(maxsize=None)
def _gates_ink() -> Any:
    return g.Ink(8, 8, 1.0, g._fonts(_theme("gates")))


@dataclass(frozen=True)
class Measure:
    """How a skin measures a run of text in a box, and at which scales. ``how`` names the skin's way; ``args`` its
    settings: nostromo (font, size, tracking, canvas), night_city (spec), night_city_fit (spec, room, floor),
    night_city_value (), gates (role, size, weight), nostromo_tray (), gates_tray (), chars ()."""

    how: str
    args: tuple[Any, ...] = ()

    @property
    def unit(self) -> str:
        return {"chars": "chars", "nostromo_tray": "device px", "gates_tray": "device px"}.get(self.how, "px")

    @property
    def scales(self) -> tuple[float, ...]:
        if self.how == "nostromo":
            return STRIP_SCALES if self.args[3] == "strip" else SCALES
        return (1.0,)

    def width(self, text: str, scale: float = 1.0) -> float:
        return _width(self, text, scale)


@lru_cache(maxsize=None)
def _width(m: Measure, text: str, scale: float) -> float:
    if m.how == "chars":
        return float(len(text.encode("utf-16-le")) // 2)           # the tray counts UTF-16 units (ui/tray.clip)
    if m.how == "nostromo":
        font, size, tracking, canvas = m.args
        cv = _nos_canvas(*((nos.STRIP_W, nos.STRIP_H) if canvas == "strip" else (nos.FLOAT_W, 100)), scale)
        return cv.advance(text, cv.font(font, size), tracking)
    if m.how == "night_city":
        return _nc_ink().width(text, getattr(nc, m.args[0]))
    if m.how == "night_city_fit":
        spec, room, floor = m.args
        ink = _nc_ink()
        return ink.width(text, nc._fit_type(ink, text, getattr(nc, spec), room, floor))
    if m.how == "night_city_value":
        # _draw_value: the figure in VALUE, the percent sign in UNIT, UNIT_GAP between them.
        ink = _nc_ink()
        signs = text.count("%")
        rest = text.replace("%", "")
        return (ink.width(rest, nc.VALUE) if rest else 0.0) + signs * (nc.UNIT_GAP + ink.width("%", nc.UNIT))
    if m.how == "gates":
        role, size, weight = m.args
        ink = _gates_ink()
        return ink.advance(text, ink.face(getattr(ink.fonts, role), size, weight, size))
    if m.how == "nostromo_tray":
        # _tray_text: VT323 at 18 device px whatever the icon's scale.
        return engine.load_font(str(nos._faces(_theme("nostromo"))[nos.FONT_VT]), 18.0).getlength(text)
    if m.how == "gates_tray":
        # _tray_number: the body face at 10 px on a 32 px icon, 8 px for three characters or more.
        body = g._fonts(_theme("gates")).body
        px = g.TRAY_NUMBER_SIZE * TRAY_SIZE / 32 * (0.8 if len(text) > 2 else 1.0)
        face = g.Face(body.file_for(600), round(px, 3), 600 if body.variable else 0, px if body.variable else None,
                      body.variable)
        return g._text_px(face, text)
    raise ValueError(f"no such measure: {m.how}")


def nos_run(font: str, size: float, tracking: float, canvas: str = "frame") -> Measure:
    return Measure("nostromo", (font, size, tracking, canvas))


def W_nos(text: str, font: str, size: float, tracking: float) -> float:
    return nos_run(font, size, tracking).width(text, 1.0)


def W_nc(text: str, spec: str) -> float:
    return Measure("night_city", (spec,)).width(text)


def W_g(text: str, role: str, size: float, weight: int) -> float:
    return Measure("gates", (role, size, weight)).width(text)


# ── Boxes and lines ───────────────────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Box:
    id: str                         # <skin, dashboard or app>.<surface>.<part>
    measure: Measure
    limit: float                    # the room, in the measure's unit
    why: str                        # how the limit follows from the skin
    fit: str                        # none | shrink | ellipsis | clip | drop (left out) | wrap
    english: tuple[str, ...]        # every English text the skin can put there
    kinds: tuple[str, ...] = ()     # the figures it holds
    templates: tuple[str, ...] = ("{}",)    # how a figure sits in its text
    keys: tuple[str, ...] = ()      # catalog keys drawn in it, once its text is in the catalog (#93 to #101)
    tight: bool = False             # sized for English's figures: its percent has no space before the sign (#91)

    @property
    def unit(self) -> str:
        return self.measure.unit

    @property
    def enforced(self) -> bool:
        """Whether a text past the budget is a fault: no fallback, or only shrinking."""
        return self.fit in ("none", "shrink")

    @property
    def scales(self) -> tuple[float, ...]:
        return self.measure.scales

    def width(self, text: str, scale: float = 1.0) -> float:
        if self.fit == "wrap":
            # A wrapping box breaks at spaces; what cannot fit is a run with no space in it (U+00A0 glues).
            return max((self.measure.width(part, scale) for part in text.split(" ") if part), default=0.0)
        return self.measure.width(text, scale)


@dataclass(frozen=True)
class Line:
    """Runs drawn side by side, each from a box of its own, that must fit ``limit`` together. ``slots``: for each run,
    the boxes that can stand there (a header's word or chip); ``extra``: the pixels the skin adds beside a box's text
    there (a chip's padding, a gap). ``english``: the English lines as drawn, one text per slot ("" for none); a text
    is measured in the first box of its slot that lists it."""

    id: str
    limit: float
    why: str
    fit: str
    slots: tuple[tuple[str, ...], ...]
    extra: tuple[tuple[str, float], ...] = ()
    english: tuple[tuple[str, ...], ...] = ()

    @property
    def enforced(self) -> bool:
        return self.fit in ("none", "shrink")

    @property
    def unit(self) -> str:
        return "px"

    @property
    def scales(self) -> tuple[float, ...]:
        return tuple(sorted({s for slot in self.slots for b in slot for s in BOX[b].scales}))

    def added(self, box_id: str) -> float:
        return dict(self.extra).get(box_id, 0.0)

    def drawn(self, texts: Sequence[str], scale: float) -> float:
        """The width of one English line as drawn."""
        total = 0.0
        for slot, text in zip(self.slots, texts):
            if not text:
                continue
            box = BOX[next((b for b in slot if text in BOX[b].english), slot[0])]
            total += box.width(text, scale) + self.added(box.id)
        return total


SEMI, REG, VT = nos.FONT_SEMI, nos.FONT_REG, nos.FONT_VT
HEADER = nos.RIGHT - (nos.LEFT + 6.0 + 6)                        # the header line's room: past the dot, to RIGHT
_HOLD_WIDEST = W_nos("HOLD 23H 59M", SEMI, 10, 1.2)               # the widest state chip text (monospaced)
_PILL_WIDEST = W_nos("RATE LIMITED", SEMI, 10, 1.2)
_AGE_WIDEST = W_nos("59 M", REG, 10, 0.6)
_NOS_VALUE = W_nos("100%", VT, 22, 0)
_NOS_EXACT = W_nos("99.5%", VT, 22, 0)
# The name line gives way to the value down to one letter and the ellipsis (_name_line): the value's floor.
_NOS_NAME_FLOOR = max(W_nos(c + nos.ELLIPSIS, SEMI, 10, 1.2) for c in string.ascii_uppercase + string.digits)
_NOS_SESSION_5H = nos.LEFT + W_nos("SESSION", SEMI, 10, 1.2) + 6 + W_nos("5H", REG, 10, 0.6)   # where it ends
_NOS_PACE_WIDEST = W_nos("BEHIND -100 PTS", SEMI, 10, 1.2)
_NOS_RESET_WIDEST = W_nos("RESETS WED 12:59 PM", REG, 10, 0.6)
_NOS_STRIP_ROOM = [grp[2] - (grp[1] + nos.STRIP_CELL_STEP * (nos.STRIP_CELLS - 1) + nos.STRIP_CELL_W)
                   for grp in nos.STRIP_GROUPS]
_TRAY = nos._tray_geometry(TRAY_SIZE)

_NC_HEAD = nc.LEFT + nc.PIP + nc.BRAND_GAP                         # where the brand starts
_NC_BRAND = W_nc("CLAUDE//USAGE", "BRAND")
_NC_NAME_FLOOR = max(W_nc(c + "...", "NAME") for c in string.ascii_uppercase + string.digits)
_NC_CAUSE_FLOOR = max(W_nc(c + "...", "BAND_TYPE") for c in string.ascii_uppercase + string.digits)
_NC_CAP_CHIP = W_nc("CAP 100", "CHIP") + 17
_NC_VALUE_100 = W_nc("100", "VALUE") + nc.UNIT_GAP + W_nc("%", "UNIT")
_NC_VALUE_EXACT = W_nc("99.9", "VALUE") + nc.UNIT_GAP + W_nc("%", "UNIT")
_NC_PCT_ROOM = nc.STRIP_PCT_RIGHT - (nc.STRIP_BAR_X + nc.STRIP_CELL_STEP * (nc.STRIP_CELLS - 1) + nc.STRIP_CELL_W) - 2
_NC_STATUS_ROOM = (nc.STRIP_TAG[2] - nc.STRIP_TAG[0]) - 8

_G_TITLE = W_g("Claude usage", "body", 12, 600)
_G_WORD_WIDEST = max(W_g(w, "body", 10, 600) for w in ("LIVE", "STALE", "OFFLINE", "RATE LIMITED", "NO LOGIN", "PAUSED",
                                                       "LOADING"))
_G_GUARD = g.GUARD_CAPSULE_W + g.GUARD_PILL_GAP                    # the guard's capsule beside the pill, holding
_G_PILL_CHROME = g.PILL_PAD_LEFT + g.LAMP_SIZE + g.PILL_GAP + g.PILL_PAD_RIGHT
_G_VALUE_100 = W_g("100%", "mono", 15, 700)
_G_VALUE_EXACT = W_g("99.9%", "mono", 15, 700)
_G_LABEL_FLOOR = W_g("S…", "body", 12, 400)
_G_PACE_WIDEST = W_g("Ahead of pace by 444 pts", "body", 11, 600)
_G_RESET_WIDEST = W_g("Resets Mon 10:00 AM", "body", 11, 400)
_G_BAR_TEXT = g.RIGHT - g.BAR_PAD - (g.LEFT + g.BAR_PAD + g.BAR_ICON_W + g.BAR_TEXT_GAP)

_STATUS_WORDS = ("LOADING", "LIVE", "STALE", "OFFLINE", "RATE LIMITED", "NO LOGIN", "PAUSED")
_COUNTDOWNS = ("HOLD 20H 48M", "HOLD 23H 59M", "HOLD 6D 23H", "HOLD 2H 14M", "HOLD 2D 3H", "HOLD 9M", "HOLD <1M", "HOLD")
_NC_CAUSES = ("RELINK // USE CLAUDE CODE", "RELINK // RUN /login", "NO SIGNAL // NET DOWN", "RATE LIMITED")
_NC_RESETS = ("RST 11:39 PM", "RST 12:59 PM", "RST SAT 4:00 PM", "RST FRI 11:12 PM", "RST WED 12:59 PM")
_TILES = ("--", "$0.00", "$5.96", "$99.99", "$100", "$999", "$1000", "$100.00", "$9.9K", "$99.9K", "$100.0K", "$999.9K",
          "$1000.0K", "$9.9M", "$99.9M", "$999.9M", "$1000.0M", "-$9.99", "-$10.00", "-$99.99", "-$100.00", "$-999",
          "$-1000", "$-9.9K", "$-10.0K", "$-99.9K", "$-999.9K", "$-1000.0K", "$-9.9M", "$-99.9M", "$-999.9M", "$-1000.0M")
_HEROES = ("--", "$0.00", "$5.96", "$99.99", "$100", "$999", "$9,999", "$10,000", "$100.00", "$99.9K", "$100.0K",
           "$999.9K", "$1000.0K", "$9.9M", "$99.9M", "$999.9M", "$1000.0M", "-$9.99", "-$10.00", "-$99.99", "-$100.00",
           "-$999", "-$1,000", "-$9,999",
           "-$10,000", "$-10.0K", "$-99.9K", "$-999.9K", "$-1000.0K", "$-9.9M", "$-99.9M", "$-999.9M", "$-1000.0M")
_TOOL_1 = ("Setup closed the monitor's tool in a Claude Code session. If that session shows an error, type /mcp there "
           "or restart it.")
_TOOL_N = "Setup closed the monitor's tool in 12 Claude Code sessions. If one shows an error, type /mcp there or restart it."
_WAIT_1 = "A session waiting on the usage guard was let go early."
_WAIT_N = "12 sessions waiting on the usage guard were let go early."


def _nos(id_: str, font: str, size: float, tracking: float, limit: float, why: str, fit: str = "none", *,
         english: tuple[str, ...], kinds: tuple[str, ...] = (), templates: tuple[str, ...] = ("{}",),
         canvas: str = "frame", tight: bool = False) -> Box:
    return Box(id_, nos_run(font, size, tracking, canvas), limit, why, fit, english, kinds, templates, tight=tight)


def _nc(id_: str, spec: str, limit: float, why: str, fit: str = "none", *, english: tuple[str, ...],
        kinds: tuple[str, ...] = (), templates: tuple[str, ...] = ("{}",), measure: Measure | None = None,
        tight: bool = False) -> Box:
    return Box(id_, measure or Measure("night_city", (spec,)), limit, why, fit, english, kinds, templates,
               tight=tight)


def _g(id_: str, role: str, size: float, weight: int, limit: float, why: str, fit: str = "none", *,
       english: tuple[str, ...], kinds: tuple[str, ...] = (), templates: tuple[str, ...] = ("{}",),
       tight: bool = False) -> Box:
    return Box(id_, Measure("gates", (role, size, weight)), limit, why, fit, english, kinds, templates, tight=tight)


def _chars(id_: str, limit: float, why: str, fit: str, *, english: tuple[str, ...], kinds: tuple[str, ...] = (),
           templates: tuple[str, ...] = ("{}",)) -> Box:
    return Box(id_, Measure("chars"), limit, why, fit, english, kinds, templates)


BOXES: tuple[Box, ...] = (
    # ── Nostromo: the floating widget's header (nostromo._header, _pill, _state_word, _armed_mark) ──
    _nos("nostromo.floating.status_pill", SEMI, 10, 1.2,
         HEADER - 10 - (6 + _AGE_WIDEST) - (_HOLD_WIDEST + 8),
         "header room less the pill's frame, the widest age and the widest hold chip", english=_STATUS_WORDS),
    _nos("nostromo.floating.age", REG, 10, 0.6,
         nos.RIGHT - (_HOLD_WIDEST + 8) - (nos.LEFT + 12 + 10 + _PILL_WIDEST + 6),
         "from the widest pill's end to the widest hold chip", kinds=("duration",),
         english=("12 S", "41 M", "6 M", "59 M", "3 H", "47 H", "2 D")),
    _nos("nostromo.floating.state_word", SEMI, 10, 1.2,
         nos.RIGHT - (nos.LEFT + 12 + 10 + _PILL_WIDEST + 6 + _AGE_WIDEST),
         "right-aligned at RIGHT, back to the widest pill and age",
         english=("NOMINAL", "ADVISORY", "LAST KNOWN", "NO CARRIER", "HOLD", "ACQUIRING", "THROTTLED", "AUTH REQUIRED")),
    _nos("nostromo.floating.state_chip", SEMI, 10, 1.2,
         nos.RIGHT - (nos.LEFT + 12 + 10 + _PILL_WIDEST + 6 + _AGE_WIDEST) - 8,
         "the state word's room less the chip's 8 px", kinds=("duration",),
         english=("CRITICAL", "HOLD <1M", "HOLD 9M", "HOLD 2H", "HOLD 2H 14M", "HOLD 23H 59M", "HOLD 1D", "HOLD 2D",
                  "HOLD 2D 3H", "HOLD 6D 23H")),
    _nos("nostromo.floating.armed_guard", SEMI, 10, 1.2, W_nos("[GUARD]", SEMI, 10, 1.2),
         "drawn in the header's free stretch only where it fits (_armed_mark), else [G]", "drop", english=("[GUARD]",)),
    _nos("nostromo.floating.armed_guard_short", SEMI, 10, 1.2, W_nos("[G]", SEMI, 10, 1.2),
         "the armed mark's fallback, left out where it does not fit", "drop", english=("[G]",)),
    # ── Nostromo: the floating widget's rows (_row_widget, _name_line, _cap_text) ──
    _nos("nostromo.floating.row_name", SEMI, 10, 1.2,
         nos.RIGHT - nos.LEFT - nos.VALUE_CLEARANCE - nos.VALUE_CHIP_PAD - _NOS_VALUE,
         "from LEFT to the chipped value; the qualifier gives way first (_name_line), then the name is cut", "ellipsis",
         english=("SESSION", "WEEKLY", "FABLE WEEKLY", "WEEKLY SCOPED", "FABLE")),
    _nos("nostromo.floating.row_qualifier", REG, 10, 0.6,
         nos.RIGHT - nos.LEFT - nos.VALUE_CLEARANCE - nos.VALUE_CHIP_PAD - _NOS_VALUE - W_nos("WEEKLY SCOPED", SEMI, 10, 1.2) - 6,
         "beside the widest name: shortened to its share, then left out (_name_line)", "drop", kinds=("percent",),
         templates=("OF {} SHARE", "{}"), english=("5H", "7D", "OF 50% SHARE", "OF 100% SHARE", "50%", "100%")),
    _nos("nostromo.floating.row_value", VT, 22, 0,
         nos.RIGHT - nos.LEFT - nos.VALUE_CLEARANCE - nos.VALUE_CHIP_PAD - _NOS_NAME_FLOOR,
         "right-aligned; the name line gives way down to one letter and the ellipsis", kinds=("percent",),
         english=("--", "0%", "2%", "13%", "93%", "100%")),
    _nos("nostromo.floating.cap_tag", SEMI, 10, 1.2,
         (nos.RIGHT - _NOS_VALUE - nos.VALUE_CHIP_PAD) - nos.VALUE_CLEARANCE - (_NOS_SESSION_5H + nos.ARMED_GAP) - 10,
         "between SESSION 5H and the chipped value, left out whole where it does not fit", "drop", kinds=("percent",),
         templates=("CAP {}",), english=("CAP 95%", "CAP 90%", "CAP 100%")),
    _nos("nostromo.floating.message", SEMI, 10, 0.8, (nos.RIGHT - 1) - (nos.LEFT + 9),
         "each of the message box's two lines, inside the dashed border",
         english=("AUTH LINK LOST.", "USE CLAUDE CODE TO RESTORE.", "AUTH REFUSED.", "RUN /login IN CLAUDE CODE.",
                  "NO LOGIN FOUND", "OPEN THE DASHBOARD")),
    # ── Nostromo: the hover card (_row_tooltip, _pace_text, _guard_lines) ──
    _nos("nostromo.tooltip.row_name", SEMI, 10, 1.2, nos.RIGHT - _NOS_EXACT - nos.VALUE_CLEARANCE - nos.LEFT,
         "from LEFT to the exact value; the qualifier gives way first, then the name is cut", "ellipsis",
         english=("SESSION", "WEEKLY", "FABLE WEEKLY", "WEEKLY SCOPED", "FABLE")),
    _nos("nostromo.tooltip.row_qualifier", REG, 10, 0.6,
         nos.RIGHT - _NOS_EXACT - nos.VALUE_CLEARANCE - nos.LEFT - W_nos("WEEKLY SCOPED", SEMI, 10, 1.2) - 6,
         "beside the widest name, shortened then left out", "drop", kinds=("percent",), templates=("OF {} SHARE", "{}"),
         english=("5H", "7D", "OF 50% SHARE", "OF 100% SHARE", "50%", "100%")),
    _nos("nostromo.tooltip.row_value", VT, 22, 0, nos.RIGHT - nos.LEFT - nos.VALUE_CLEARANCE - _NOS_NAME_FLOOR,
         "right-aligned; the name line yields", kinds=("percent",), english=("--", "2%", "13%", "12.3%", "99.5%", "100%")),
    _nos("nostromo.tooltip.cap_tag", SEMI, 10, 1.2,
         (nos.RIGHT - _NOS_EXACT) - nos.VALUE_CLEARANCE - (_NOS_SESSION_5H + nos.ARMED_GAP) - 10,
         "between SESSION 5H and the exact value, left out whole where it does not fit", "drop", kinds=("percent",),
         templates=("CAP {}",), english=("CAP 95%", "CAP 90%", "CAP 100%")),
    _nos("nostromo.tooltip.resets", REG, 10, 0.6, nos.RIGHT - nos.LEFT - _NOS_PACE_WIDEST,
         "from LEFT to the widest pace, right-aligned at RIGHT", kinds=("clock",),
         templates=("RESETS {}", "RESETS WED {}"),
         english=("RESETS --", "RESETS 11:39 PM", "RESETS 10:26 PM", "RESETS SAT 8:12 PM", "RESETS FRI 11:12 PM",
                  "RESETS SAT 12:59 PM", "RESETS WED 12:59 PM")),
    _nos("nostromo.tooltip.pace", SEMI, 10, 1.2, nos.RIGHT - nos.LEFT - _NOS_RESET_WIDEST,
         "right-aligned at RIGHT, back to the widest reset", kinds=("count",),
         english=("PACE --", "ON PACE", "AHEAD +12 PTS", "AHEAD +21 PTS", "BEHIND -12 PTS", "BEHIND -18 PTS",
                  "BEHIND -55 PTS", "AHEAD +100 PTS", "BEHIND -100 PTS")),
    _nos("nostromo.tooltip.guard_lead", SEMI, 10, 0.6, nos.RIGHT - nos.LEFT,
         "the statement's lead, drawn semibold at the statement's tracking; it wraps with the statement", "wrap",
         english=("GUARD ARMED:", "GUARD HOLDING:", "GUARD ARMED", "GUARD HOLDING")),
    _nos("nostromo.tooltip.guard_lines", REG, 10, 0.6, nos.RIGHT - nos.LEFT,
         "each line of the guard's statement: the first lead and all, as _guard_lines measures it, and its rest after "
         "the lead, as _guard_block draws it; it wraps at spaces and the card grows", "wrap",
         kinds=("percent", "clock", "duration"),
         english=("GUARD ARMED: SESSION 95%, WEEKLY 90%", "GUARD HOLDING: SESSION 96% (CAP 95%).",
                  "GUARD HOLDING: WEEKLY 91% (CAP 90%).", " SESSION 95%, WEEKLY 90%", " SESSION 96% (CAP 95%).",
                  " WEEKLY 91% (CAP 90%).", "RESUMES 10:26 PM (IN 2H 14M).", "RESUMES FRI 11:12 PM (IN 2D 3H).")),
    # ── Nostromo: the taskbar strip (_render_strip) and the tray (_tray_text) ──
    _nos("nostromo.strip.window_tag", SEMI, 11, 1.1, nos.STRIP_GROUPS[0][1] - nos.STRIP_GROUPS[0][0],
         "from the group's start to its bar (STRIP_GROUPS)", canvas="strip", english=("5H", "7D")),
    _nos("nostromo.strip.value_5h", VT, 18, 0, _NOS_STRIP_ROOM[0], "from the 5H bar's last cell to the next group",
         kinds=("percent",), canvas="strip", english=("--", "13%", "38%", "41%", "78%", "100%"), tight=True),
    _nos("nostromo.strip.value_5h_chip", VT, 18, 0, _NOS_STRIP_ROOM[0] - 4,
         "the 5H value in its critical chip (4 px of padding): at 90 % and up, or at any cap the guard holds on "
         "(_row_emphasis)", kinds=("percent",), canvas="strip", english=("60%", "90%", "93%", "96%", "99%", "100%"),
         tight=True),
    _nos("nostromo.strip.value_7d", VT, 18, 0, _NOS_STRIP_ROOM[1], "from the 7D bar's last cell to the strip's end",
         kinds=("percent",), canvas="strip", english=("--", "2%", "21%", "52%", "58%", "100%"), tight=True),
    _nos("nostromo.strip.value_7d_chip", VT, 18, 0, _NOS_STRIP_ROOM[1] - 4,
         "the 7D value in its critical chip, as the 5H one", kinds=("percent",), canvas="strip",
         english=("60%", "90%", "91%", "100%"), tight=True),
    Box("nostromo.tray.readout", Measure("nostromo_tray"), 2 * (_TRAY[0] - _TRAY[1]),
        "the ring's hole on a 32 px icon, in device px (_tray_geometry)", "none", ("--", "0", "13", "100")),

    # ── Night City: the header (_draw_header) ──
    _nc("night_city.floating.brand", "BRAND", nc.RIGHT - _NC_HEAD - (W_nc("HOLD 20H 48M", "CHIP") + 17),
        "from the pip to the widest hold chip", english=("CLAUDE//USAGE",)),
    _nc("night_city.floating.live_tag", "STATUS", nc.RIGHT - (_NC_HEAD + _NC_BRAND) - 8,
        "right of the brand, its box 8 px wider than the word", english=("LIVE",)),
    _nc("night_city.floating.status_chip", "CHIP", nc.RIGHT - (_NC_HEAD + _NC_BRAND) - 12,
        "right of the brand, the chip 12 px wider than the word", english=("PAUSED", "LOADING")),
    _nc("night_city.floating.guard_chip", "CHIP",
        nc.RIGHT - (W_nc("PAUSED", "CHIP") + 12) - 2 * nc.GUARD_GAP - (_NC_HEAD + _NC_BRAND) - 12,
        "between the brand and a status chip, left out where it does not fit", "drop", english=("GUARD",)),
    _nc("night_city.floating.hold_chip", "CHIP", nc.RIGHT - (_NC_HEAD + _NC_BRAND) - 17,
        "right of the brand, the chip 17 px wider than its text", kinds=("duration",), english=_COUNTDOWNS),
    # ── Night City: the band (_draw_band, _draw_band_hold) ──
    _nc("night_city.floating.band_text", "BAND_TYPE", nc.BAND_AGE_RIGHT - 10 - nc.BAND_TEXT_X - W_nc("100 D OLD", "RESET"),
        "the cause, left of the widest age", "ellipsis", english=_NC_CAUSES),
    _nc("night_city.floating.band_text_no_age", "BAND_TYPE", nc.BAND_AGE_RIGHT - nc.BAND_TEXT_X,
        "the whole band", "ellipsis", english=("NO LOGIN // OPEN DASHBOARD",)),
    _nc("night_city.floating.band_age", "RESET",
        nc.BAND_AGE_RIGHT - 10 - nc.BAND_TEXT_X - W_nc("RELINK // USE CLAUDE CODE", "BAND_TYPE"),
        "right of the widest whole cause; left out when the cause is cut", "drop", kinds=("duration",),
        english=("41 M OLD", "59 S OLD", "47 H OLD", "2 D OLD", "100 D OLD", "2 M OLD", "6 M OLD")),
    _nc("night_city.floating.band_hold_text", "BAND_TYPE",
        nc.BAND_AGE_RIGHT - nc.GUARD_GAP - nc.BAND_TEXT_X - (W_nc("HOLD", "CHIP") + 17),
        "the cause beside the shortest hold chip (the countdown and the age give way first)", "ellipsis",
        english=_NC_CAUSES),
    _nc("night_city.floating.band_hold_chip", "CHIP", nc.BAND_AGE_RIGHT - nc.GUARD_GAP - nc.BAND_TEXT_X - 17 - _NC_CAUSE_FLOOR,
        "right-anchored at BAND_AGE_RIGHT, never cut; it may only leave the cause its shortest cut", kinds=("duration",),
        english=_COUNTDOWNS),
    # ── Night City: the rows (_layout_row, _draw_value) ──
    _nc("night_city.floating.row_name", "NAME",
        (nc.RIGHT - _NC_VALUE_100 - nc.MIN_GAP) - nc.LEFT - 6 - _NC_CAP_CHIP,
        "left of the widest chip and value; the reset and the tag give way first", "ellipsis",
        english=("SESSION", "WEEKLY", "FABLE", "WEEKLY SCOPED")),
    _nc("night_city.floating.row_chip", "CHIP",
        (nc.RIGHT - _NC_VALUE_100 - nc.MIN_GAP) - nc.LEFT - 6 - 17 - _NC_NAME_FLOOR,
        "beside the name's shortest cut, the chip 17 px wider than its text, never cut",
        english=("CRIT", "WARN", "HOLD", "CAP 90", "CAP 95", "CAP 100")),
    _nc("night_city.floating.row_tag", "TAG",
        (nc.RIGHT - _NC_VALUE_100 - nc.MIN_GAP) - nc.LEFT - W_nc("FABLE", "NAME") - 12 - _NC_CAP_CHIP,
        "beside FABLE and the widest chip, left out where it does not fit", "drop", kinds=("percent",),
        templates=("{} SHARE",), english=("50% SHARE", "100% SHARE")),
    _nc("night_city.floating.row_reset", "RESET",
        (nc.RIGHT - _NC_VALUE_100 - nc.VALUE_GAP) - nc.MIN_GAP - nc.LEFT - W_nc("SESSION", "NAME") - 8,
        "between SESSION and the value, left out whole where it does not fit", "drop", kinds=("clock",),
        templates=("RST {}", "RST WED {}"), english=_NC_RESETS),
    _nc("night_city.floating.row_value", "VALUE",
        nc.RIGHT - nc.MIN_GAP - (nc.LEFT + _NC_NAME_FLOOR + 6 + _NC_CAP_CHIP),
        "right-aligned; the name yields to its shortest cut beside the widest chip", kinds=("percent",),
        measure=Measure("night_city_value"), english=("100%", "13%", "2%", "78%", "93%", "--")),
    # ── Night City: the hover card ──
    _nc("night_city.tooltip.row_name", "NAME",
        (nc.RIGHT - _NC_VALUE_EXACT - nc.MIN_GAP) - nc.LEFT - 6 - _NC_CAP_CHIP,
        "left of the exact value and the widest chip; the tag gives way first", "ellipsis",
        english=("SESSION", "WEEKLY", "FABLE", "WEEKLY SCOPED")),
    _nc("night_city.tooltip.row_chip", "CHIP",
        (nc.RIGHT - _NC_VALUE_EXACT - nc.MIN_GAP) - nc.LEFT - 6 - 17 - _NC_NAME_FLOOR,
        "beside the name's shortest cut, never cut", english=("CRIT", "WARN", "HOLD", "CAP 90", "CAP 95", "CAP 100")),
    _nc("night_city.tooltip.row_tag", "TAG",
        (nc.RIGHT - _NC_VALUE_EXACT - nc.MIN_GAP) - nc.LEFT - W_nc("FABLE", "NAME") - 12 - _NC_CAP_CHIP,
        "beside FABLE and the widest chip, left out where it does not fit", "drop", kinds=("percent",), templates=("{} SHARE",),
        english=("50% SHARE", "100% SHARE")),
    _nc("night_city.tooltip.row_value", "VALUE",
        nc.RIGHT - nc.MIN_GAP - (nc.LEFT + _NC_NAME_FLOOR + 6 + _NC_CAP_CHIP),
        "right-aligned; the name yields", kinds=("percent",), measure=Measure("night_city_value"),
        english=("99.9%", "13.5%", "100%", "13%", "2%", "--")),
    _nc("night_city.tooltip.info_reset", "RESET", nc.CONTENT_W - (W_nc("UNDER PACE -100", "CHIP") + 12) - 8,
        "left of the widest pace chip", kinds=("clock",), templates=("RST {}", "RST WED {}"), english=_NC_RESETS),
    _nc("night_city.tooltip.info_pace", "CHIP", nc.CONTENT_W - W_nc("RST WED 12:59 PM", "RESET") - 8 - 12,
        "right of the widest reset, as a chip", kinds=("count",),
        english=("PACE --", "ON PACE", "AHEAD +41", "AHEAD +100", "UNDER PACE -18", "UNDER PACE -100")),
    _nc("night_city.tooltip.guard_lead", "CHIP", nc.CONTENT_W, "the card's content width",
        english=("GUARD // HOLDING:", "GUARD // ARMED:", "GUARD // HOLDING", "GUARD // ARMED")),
    _nc("night_city.tooltip.guard_lines", "RESET", nc.CONTENT_W,
        "each line the card breaks the statement into, after the lead on the first; it wraps and the card grows", "wrap",
        kinds=("percent", "clock", "duration"),
        english=("SESSION 95%,", "WEEKLY 90%", "SESSION 96%", "(CAP 95%). RESUMES 10:26 PM", "(IN 2H 14M).",
                 "WEEKLY 91% (CAP 90%).", "RESUMES FRI 11:12 PM (IN 2D 3H).")),
    # ── Night City: the strip (_draw_strip_row, _draw_strip_status) ──
    _nc("night_city.strip.window_label", "STATUS", nc.STRIP_BAR_X - nc.STRIP_LABEL_X, "from the label to the bar",
        english=("5H", "7D")),
    _nc("night_city.strip.percent", "STRIP_PCT", _NC_PCT_ROOM,
        "from the segmented bar's end (the slanted look has one px more); shrunk to fit, down to 8 px", "shrink",
        kinds=("percent",), measure=Measure("night_city_fit", ("STRIP_PCT", _NC_PCT_ROOM, 8.0)),
        english=("100%", "13%", "2%", "--"), tight=True),
    _nc("night_city.strip.status_live", "STATUS", nc.STRIP_TAG[2] - nc.STRIP_WORD_X, "the status tag's room",
        english=("LIVE",)),
    _nc("night_city.strip.status_word", "STATUS", _NC_STATUS_ROOM,
        "the status tag less its 8 px; shrunk to fit, down to 9 px", "shrink",
        measure=Measure("night_city_fit", ("STATUS", _NC_STATUS_ROOM, 9.0)),
        english=("STALE", "OFFLINE", "PAUSED", "LOADING", "LIMITED", "NO LOGIN")),
    _nc("night_city.strip.hold_chip", "CHIP", (nc.STRIP_TAG[2] - nc.STRIP_TAG[0]) - 17, "the status tag as a chip",
        english=("HOLD",)),

    # ── Gates: the header (_header) ──
    _g("gates.floating.title", "body", 12, 600,
       g.PILL_RIGHT - _G_PILL_CHROME - _G_GUARD - g.TITLE_GAP - g.TITLE_X - _G_WORD_WIDEST,
       "left of the guard's capsule and the widest status pill", english=("Claude usage",)),
    _g("gates.floating.status_word", "body", 10, 600,
       g.PILL_RIGHT - _G_PILL_CHROME - g.TITLE_X - g.TITLE_GAP - _G_TITLE - _G_GUARD,
       "the pill's word, right of the title and the guard's capsule", english=_STATUS_WORDS),
    _g("gates.floating.status_pill", "body", 10, 600,
       g.PILL_RIGHT - _G_PILL_CHROME - g.TITLE_X - g.TITLE_GAP - _G_TITLE - _G_GUARD,
       "the word and its age; the age is left out where it does not fit", "drop", kinds=("duration",),
       english=("LIVE · 12 s", "STALE · 41 min", "OFFLINE · 2 min", "PAUSED · 12 s", "RATE LIMITED · 6 min",
                "RATE LIMITED · 44 min", "RATE LIMITED · 59 min", "NO LOGIN · 2 d", "LIVE · 59 s", "STALE · 1 h")),
    _g("gates.tooltip.title", "body", 12, 600,
       g.RIGHT - _G_PILL_CHROME - _G_GUARD - g.TITLE_GAP - g.TITLE_X - _G_WORD_WIDEST,
       "the hover card's header, to RIGHT", english=("Claude usage",)),
    _g("gates.tooltip.status_word", "body", 10, 600,
       g.RIGHT - _G_PILL_CHROME - g.TITLE_X - g.TITLE_GAP - _G_TITLE - _G_GUARD,
       "the hover card's pill word", english=_STATUS_WORDS),
    _g("gates.tooltip.status_pill", "body", 10, 600,
       g.RIGHT - _G_PILL_CHROME - g.TITLE_X - g.TITLE_GAP - _G_TITLE - _G_GUARD,
       "the hover card's word and age; the age is left out where it does not fit", "drop", kinds=("duration",),
       english=("LIVE · 12 s", "STALE · 41 min", "OFFLINE · 2 min", "PAUSED · 12 s", "RATE LIMITED · 6 min",
                "RATE LIMITED · 44 min", "RATE LIMITED · 59 min", "NO LOGIN · 59 min", "LOADING · 59 s")),
    # ── Gates: the rows (_row, _cap_badge) ──
    _g("gates.floating.row_label", "body", 12, 400, g.RIGHT - g.LEFT - 10 - _G_VALUE_100,
       "left of the widest value; the tag gives way first, then the label is cut", "ellipsis",
       english=("Session", "Weekly", "Fable weekly")),
    _g("gates.floating.row_tag", "mono", 11, 400,
       (g.RIGHT - g.LEFT - 10 - _G_VALUE_100) - 6 - W_g("Fable weekly", "body", 12, 400),
       "beside the widest label, left out where it does not fit", "drop", kinds=("percent",), templates=("{} share",),
       english=("5H", "7D", "50% share", "100% share")),
    _g("gates.floating.row_value", "mono", 15, 700, g.RIGHT - g.LEFT - 10 - _G_LABEL_FLOOR,
       "right-aligned at RIGHT; the label yields to its shortest cut", kinds=("percent",),
       english=("--", "0%", "2%", "13%", "96%", "100%")),
    _g("gates.floating.cap_badge", "body", 10, 600,
       g.RIGHT - _G_VALUE_100 - 10 - g.LEFT - W_g("Fable weekly", "body", 12, 400) - (6 + W_g("100% share", "mono", 11, 400))
       - g.BADGE_GAP - 2 * g.BADGE_PAD,
       "after the widest label and tag, before the value; left out where it does not fit", "drop", kinds=("percent",),
       templates=("Cap {}",), english=("Cap 95%", "Cap 90%", "Cap 100%"), tight=True),
    _g("gates.tooltip.row_label", "body", 12, 400, g.RIGHT - g.LEFT - 10 - _G_VALUE_EXACT,
       "left of the exact value; the tag gives way first, then the label is cut", "ellipsis",
       english=("Session", "Weekly", "Fable weekly")),
    _g("gates.tooltip.row_tag", "mono", 11, 400,
       (g.RIGHT - g.LEFT - 10 - _G_VALUE_EXACT) - 6 - W_g("Fable weekly", "body", 12, 400),
       "beside the widest label, left out where it does not fit", "drop", kinds=("percent",), templates=("{} share",),
       english=("5H", "7D", "50% share", "100% share")),
    _g("gates.tooltip.row_value", "mono", 15, 700, g.RIGHT - g.LEFT - 10 - _G_LABEL_FLOOR,
       "right-aligned; the label yields", kinds=("percent",),
       english=("2%", "13%", "96%", "99.9%", "100%", "--", "13.5%")),
    _g("gates.tooltip.cap_badge", "body", 10, 600,
       g.RIGHT - _G_VALUE_EXACT - 10 - g.LEFT - W_g("Fable weekly", "body", 12, 400) - (6 + W_g("100% share", "mono", 11, 400))
       - g.BADGE_GAP - 2 * g.BADGE_PAD,
       "after the widest label and tag, before the exact value; left out where it does not fit", "drop",
       kinds=("percent",), templates=("Cap {}",), english=("Cap 95%", "Cap 90%", "Cap 100%"), tight=True),
    # ── Gates: the hover card's info line and guard bar (render_tooltip, _guard_bar_lines) ──
    _g("gates.tooltip.resets", "body", 11, 400, g.RIGHT - g.LEFT - _G_PACE_WIDEST,
       "from LEFT to the widest pace, right-aligned at RIGHT", kinds=("clock",), templates=("Resets {}", "Resets Wed {}"),
       english=("Resets 11:39 PM", "Resets 12:59 PM", "Resets Sat 8:12 PM", "Resets Fri 11:12 PM", "Resets Mon 10:00 AM",
                "Resets Wed 12:59 PM")),
    _g("gates.tooltip.pace", "body", 11, 600, g.RIGHT - g.LEFT - _G_RESET_WIDEST,
       "right-aligned at RIGHT, back to the widest reset", kinds=("count",),
       english=("On pace", "Under pace by 18 pts", "Ahead of pace by 12 pts", "Ahead of pace by 100 pts",
                "Ahead of pace by 444 pts")),
    _g("gates.tooltip.guard_lead", "body", 11, 600, _G_BAR_TEXT, "the guard bar's text room",
       english=("Guard armed:", "Guard holding:", "Guard armed", "Guard holding")),
    _g("gates.tooltip.guard_text", "body", 11, 400, _G_BAR_TEXT,
       "each line of the guard bar after its lead; it wraps and the bar grows", "wrap",
       kinds=("percent", "clock", "duration"),
       english=("session 95%, weekly 90%", "session 96% (cap 95%).", "Resumes 10:26 PM (in 2h 14m).", "weekly 91% (cap 90%).",
                "Resumes Fri 11:12 PM (in 2d 3h).")),
    # ── Gates: the footer (_footer, _footer_runs): a lead at 600, the rest at 400, each run cut to what is left ──
    _g("gates.floating.footer_lead", "body", 11, 600, g.RIGHT - g.LEFT, "the footer's first run", "ellipsis",
       english=("Hmm, we lost touch.", "Rate limited.", "No sign-in found.", "Sign-in refused.", "Guard holding",
                "Nearly out", "Ahead of pace", "Under pace", "On pace")),
    _g("gates.floating.footer_rest", "body", 11, 400, g.RIGHT - g.LEFT, "the footer's second run, after the lead",
       "ellipsis", kinds=("clock",), templates=(" · Resets {}", " · Resumes {} (in 23h 59m)"),
       english=(" Send Claude Code a message.", "No connection. Retrying every minute.", "Paused. Resume to keep usage live.",
                " Showing the last reading.", " Open the dashboard for help.", "Fetching your usage.",
                " Run /login in Claude Code.", " · Resets 11:39 PM", " · Resets Mon 10:00 AM", " · Resets Wed 12:59 PM",
                " · Resumes 10:26 PM (in 2h 14m)", " · Resumes Fri 11:12 PM (in 2d 3h)", " · Resumes Mon 10:00 AM (in 20h 20m)",
                " · Resumes Wed 12:59 PM (in 23h 59m)", "Resets 11:39 PM", "Waiting for your first reading.",
                "No reset time yet.")),
    # ── Gates: the strip and the tray ──
    _g("gates.strip.window_tag", "body", 11, 600, g.STRIP_RAIL_X0 - g.STRIP_K_X, "from the tag to the rail",
       english=("5H", "7D")),
    _g("gates.strip.value", "mono", 11, 700, g.STRIP_VALUE_RIGHT - g.STRIP_RAIL_X1,
       "right-aligned at STRIP_VALUE_RIGHT, clear of the rail's end", kinds=("percent",), english=("--", "13%", "2%", "100%")),
    Box("gates.tray.number", Measure("gates_tray"),
        2 * math.sqrt((g.TRAY_RING[TRAY_SIZE][0] - g.TRAY_RING[TRAY_SIZE][1] / 2) ** 2
                      - (round(TRAY_SIZE / 2 + 0.36 * g.TRAY_NUMBER_SIZE) - TRAY_SIZE / 2) ** 2),
        "the ring's hole at the number's baseline on a 32 px icon, in device px", "none",
        ("--", "0", "13", "99", "100", "999")),

    # ── The dashboard's money figures (fmtUsdTile, fmtUsdHero in app.js): "six characters at most" ──
    _chars("dashboard.nostromo.cost_tile", 6, "fmtUsdTile's six characters", "none", kinds=("money",), english=_TILES),
    _chars("dashboard.night_city.cost_tile", 6, "fmtUsdTile's six characters", "none", kinds=("money",), english=_TILES),
    _chars("dashboard.gates.cost_tile", 6, "fmtUsdTile's six characters", "none", kinds=("money",), english=_TILES),
    _chars("dashboard.nostromo.cost_hero", 6, "fmtUsdHero's six characters; the stylesheet shrinks the figure with its "
           "length down to 52 px", "shrink", kinds=("money",), english=_HEROES),
    _chars("dashboard.night_city.cost_hero", 6, "fmtUsdHero's six characters; the stylesheet does not shrink it", "none",
           kinds=("money",), english=_HEROES),
    _chars("dashboard.gates.cost_hero", 6, "fmtUsdHero's six characters; the stylesheet shrinks it to 30 px (40 px "
           "flat on short or 1001 to 1100 px windows)", "shrink", kinds=("money",), english=_HEROES),
    # ── The tray (ui/tray.py: TIP_LIMIT, TITLE_LIMIT, BODY_LIMIT) and its toasts ──
    _chars("app.tray.tooltip", tray.TIP_LIMIT, "TIP_LIMIT (szTip holds 128 UTF-16 units with the terminator)", "ellipsis",
           kinds=("percent", "clock", "duration"),
           english=("Claude usage - loading", "Claude usage - no login", "Claude usage - paused",
                    "Claude usage - session 13% - weekly 2%", "Claude usage - session 13% - weekly 2% - guard armed",
                    "Claude usage - session 13% - weekly 2% - rate limited",
                    "Claude usage - session 13% - weekly 2% - guard holding, resumes Wed 12:59 PM (in 6d 23h)",
                    "Claude usage - session 100% - weekly 100% - rate limited - guard holding",
                    "Claude usage - session 100% - guard holding, resumes 12:59 PM (in <1m)",
                    "Claude usage - session 100% - weekly 100% - rate limited - guard holding, resumes Sun 12:00 AM "
                    "(in 23h 59m)",
                    "Claude usage - session 100% - weekly 100% - rate limited - guard holding, resumes Sun 12:00 AM "
                    "(in 23h 59m) (dev)")),
    _chars("app.toast.alert_threshold.title", tray.TITLE_LIMIT, "TITLE_LIMIT", "ellipsis", kinds=("percent",),
           templates=("Weekly scoped limit at {}",),
           english=("Session limit at 100%", "Weekly limit at 100%", "Weekly scoped limit at 100%",
                    "Fable weekly limit at 100%")),
    _chars("app.toast.alert_resets.body", tray.BODY_LIMIT, "BODY_LIMIT", "ellipsis", kinds=("clock",),
           templates=("Resets {}", "Resets Wed {}"), english=("Resets 12:59 PM", "Resets Wed 12:59 PM")),
    _chars("app.toast.alert_reset.title", tray.TITLE_LIMIT, "TITLE_LIMIT", "ellipsis",
           english=("Session limit reset", "Weekly limit reset", "Weekly scoped limit reset", "Fable weekly limit reset")),
    _chars("app.toast.alert_reset_ended.body", tray.BODY_LIMIT, "BODY_LIMIT", "ellipsis", kinds=("percent",),
           templates=("Usage is back to {}",), english=("Usage is back to 0%.",)),
    _chars("app.toast.alert_credits.title", tray.TITLE_LIMIT, "TITLE_LIMIT", "ellipsis", english=("Usage credits in use",)),
    _chars("app.toast.alert_credits.body", tray.BODY_LIMIT, "BODY_LIMIT", "ellipsis", kinds=("money",),
           templates=("Used {} of 10,000.00 USD",),
           english=("Used 1,234.56 of 10,000.00 USD", "Used 1,234.56 of 10,000.00", "Used 1,234.56 USD", "Used 1,234.56")),
    _chars("app.toast.update.title", tray.TITLE_LIMIT, "TITLE_LIMIT", "ellipsis",
           english=("Claude Usage Monitor v2.3.0 is available", "Claude Usage Monitor v3.0.0-rc.1 is available")),
    _chars("app.toast.update.body", tray.BODY_LIMIT, "BODY_LIMIT", "ellipsis",
           english=("Open the tray menu or the dashboard to update",)),
    _chars("app.toast.update_note.title", tray.TITLE_LIMIT, "TITLE_LIMIT", "ellipsis",
           english=("Claude Usage Monitor updated",)),
    _chars("app.toast.update_note.body", 250, "app.py cuts the note at 250 characters (the balloon holds BODY_LIMIT)",
           "clip", english=(_TOOL_1, _TOOL_N, _WAIT_1, _WAIT_N, f"{_TOOL_1} {_WAIT_1}", f"{_TOOL_1} {_WAIT_N}",
                            f"{_TOOL_N} {_WAIT_1}", f"{_TOOL_N} {_WAIT_N}")),
)
BOX: dict[str, Box] = {box.id: box for box in BOXES}

LINES: tuple[Line, ...] = (
    Line("nostromo.floating.header_line", HEADER, "the pill (and its age) from the dot, the word or chip right-aligned "
         "at RIGHT", "none",
         (("nostromo.floating.status_pill",), ("nostromo.floating.age",),
          ("nostromo.floating.state_word", "nostromo.floating.state_chip")),
         (("nostromo.floating.status_pill", 10), ("nostromo.floating.age", 6), ("nostromo.floating.state_chip", 8)),
         (("LIVE", "12 S", "NOMINAL"), ("LIVE", "12 S", "ADVISORY"), ("LIVE", "12 S", "CRITICAL"),
          ("LIVE", "12 S", "HOLD 2H 14M"), ("LIVE", "12 S", "HOLD 2D 3H"), ("STALE", "41 M", "LAST KNOWN"),
          ("OFFLINE", "", "NO CARRIER"), ("PAUSED", "", "HOLD"), ("RATE LIMITED", "6 M", "THROTTLED"),
          ("NO LOGIN", "", "AUTH REQUIRED"), ("LOADING", "", "ACQUIRING"), ("RATE LIMITED", "59 M", "HOLD 23H 59M"))),
    Line("nostromo.floating.row_name_line", nos.RIGHT - nos.LEFT - nos.VALUE_CLEARANCE - nos.VALUE_CHIP_PAD - _NOS_VALUE,
         "the name and its qualifier, left of the chipped value", "ellipsis",
         (("nostromo.floating.row_name",), ("nostromo.floating.row_qualifier",)),
         (("nostromo.floating.row_qualifier", 6),),
         (("SESSION", "5H"), ("WEEKLY", "7D"), ("FABLE WEEKLY", "OF 50% SHARE"), ("WEEKLY SCOPED", "OF 100% SHARE"))),
    Line("nostromo.tooltip.row_name_line", nos.RIGHT - _NOS_EXACT - nos.VALUE_CLEARANCE - nos.LEFT,
         "the name and its qualifier, left of the exact value", "ellipsis",
         (("nostromo.tooltip.row_name",), ("nostromo.tooltip.row_qualifier",)), (("nostromo.tooltip.row_qualifier", 6),),
         (("SESSION", "5H"), ("WEEKLY", "7D"), ("FABLE WEEKLY", "OF 50% SHARE"), ("WEEKLY SCOPED", "OF 100% SHARE"))),
    Line("nostromo.tooltip.info_line", nos.RIGHT - nos.LEFT, "the reset from LEFT and the pace right-aligned at RIGHT, with "
         "no gap between", "none", (("nostromo.tooltip.resets",), ("nostromo.tooltip.pace",)), (),
         (("RESETS 11:39 PM", "BEHIND -18 PTS"), ("RESETS SAT 8:12 PM", "BEHIND -55 PTS"), ("RESETS FRI 11:12 PM", "AHEAD +21 PTS"),
          ("RESETS SAT 8:12 PM", "ON PACE"), ("RESETS --", "PACE --"), ("RESETS WED 12:59 PM", "BEHIND -100 PTS"))),
    Line("night_city.floating.header_line", nc.RIGHT - _NC_HEAD, "the brand from the pip, a tag or chip right-aligned at "
         "RIGHT", "none",
         (("night_city.floating.brand",),
          ("night_city.floating.live_tag", "night_city.floating.status_chip", "night_city.floating.hold_chip")),
         (("night_city.floating.live_tag", 8), ("night_city.floating.status_chip", 12), ("night_city.floating.hold_chip", 17)),
         (("CLAUDE//USAGE", "LIVE"), ("CLAUDE//USAGE", "PAUSED"), ("CLAUDE//USAGE", "LOADING"),
          ("CLAUDE//USAGE", "HOLD 20H 48M"))),
    Line("night_city.floating.band_line", nc.BAND_AGE_RIGHT - nc.BAND_TEXT_X, "the cause and its age (the age is left "
         "out when the cause is cut)", "ellipsis",
         (("night_city.floating.band_text",), ("night_city.floating.band_age",)), (("night_city.floating.band_age", 10),),
         (("RELINK // USE CLAUDE CODE", "100 D OLD"), ("NO SIGNAL // NET DOWN", "41 M OLD"), ("RATE LIMITED", "6 M OLD"))),
    Line("night_city.floating.band_hold_line", nc.BAND_AGE_RIGHT - nc.BAND_TEXT_X, "the cause and the hold chip (the "
         "countdown, then the age, give way first)", "ellipsis",
         (("night_city.floating.band_hold_text",), ("night_city.floating.band_hold_chip",)),
         (("night_city.floating.band_hold_chip", 17 + nc.GUARD_GAP),),
         (("RELINK // USE CLAUDE CODE", "HOLD"), ("RATE LIMITED", "HOLD 2H 14M"))),
    Line("night_city.tooltip.info_line", nc.CONTENT_W, "the reset (boxed: 8 px more) and the pace chip (12 px more) on "
         "one line", "none", (("night_city.tooltip.info_reset",), ("night_city.tooltip.info_pace",)),
         (("night_city.tooltip.info_reset", 8), ("night_city.tooltip.info_pace", 12)),
         (("RST 11:39 PM", "ON PACE"), ("RST WED 12:59 PM", "UNDER PACE -100"), ("RST 12:59 PM", "PACE --"))),
    Line("gates.floating.header_line", g.PILL_RIGHT - g.TITLE_X, "the title, the guard's capsule and the pill, to "
         "PILL_RIGHT", "none", (("gates.floating.title",), ("gates.floating.status_word",)),
         (("gates.floating.title", g.TITLE_GAP), ("gates.floating.status_word", _G_PILL_CHROME + _G_GUARD)),
         tuple(("Claude usage", word) for word in _STATUS_WORDS)),
    Line("gates.tooltip.header_line", g.RIGHT - g.TITLE_X, "the title, the guard's capsule and the pill, to RIGHT", "none",
         (("gates.tooltip.title",), ("gates.tooltip.status_word",)),
         (("gates.tooltip.title", g.TITLE_GAP), ("gates.tooltip.status_word", _G_PILL_CHROME + _G_GUARD)),
         tuple(("Claude usage", word) for word in _STATUS_WORDS)),
    Line("gates.floating.row_line", g.RIGHT - g.LEFT, "the label, its tag and the value, 10 px apart", "ellipsis",
         (("gates.floating.row_label",), ("gates.floating.row_tag",), ("gates.floating.row_value",)),
         (("gates.floating.row_tag", 6), ("gates.floating.row_value", 10)),
         (("Session", "5H", "100%"), ("Weekly", "7D", "13%"), ("Fable weekly", "100% share", "100%"))),
    Line("gates.floating.footer", g.RIGHT - g.LEFT, "the lead and the rest from LEFT, each cut to the room left (themes/"
         "gates.json's footer_widths.box_px says the same 258, but no code reads it)", "ellipsis",
         (("gates.floating.footer_lead",), ("gates.floating.footer_rest",)), (),
         (("Hmm, we lost touch.", " Send Claude Code a message."), ("", "No connection. Retrying every minute."),
          ("", "Paused. Resume to keep usage live."), ("Rate limited.", " Showing the last reading."),
          ("No sign-in found.", " Open the dashboard for help."), ("", "Fetching your usage."),
          ("Sign-in refused.", " Run /login in Claude Code."), ("Guard holding", ""),
          ("Guard holding", " · Resumes 10:26 PM (in 2h 14m)"), ("Guard holding", " · Resumes Fri 11:12 PM (in 2d 3h)"),
          ("Guard holding", " · Resumes Mon 10:00 AM (in 20h 20m)"), ("Guard holding", " · Resumes Wed 12:59 PM (in 23h 59m)"),
          ("Nearly out", " · Resets 11:39 PM"), ("Ahead of pace", " · Resets 11:39 PM"), ("Under pace", " · Resets 11:39 PM"),
          ("On pace", " · Resets 11:39 PM"), ("Ahead of pace", " · Resets Mon 10:00 AM"),
          ("Ahead of pace", " · Resets Wed 12:59 PM"), ("", "Resets 11:39 PM"), ("", "Waiting for your first reading."),
          ("", "No reset time yet."))),
    Line("gates.tooltip.info_line", g.RIGHT - g.LEFT, "the reset from LEFT and the pace right-aligned at RIGHT", "none",
         (("gates.tooltip.resets",), ("gates.tooltip.pace",)), (),
         (("Resets 11:39 PM", "On pace"), ("Resets Mon 10:00 AM", "Ahead of pace by 100 pts"),
          ("Resets Wed 12:59 PM", "Ahead of pace by 100 pts"), ("Resets Mon 10:00 AM", "Ahead of pace by 444 pts"))),
)

# Where English already runs past its room, as the skins draw it today (box or line id, text; a line's runs joined by
# " | "). A pixel box's budget there is what English takes; a character box and a line keep their limit, and only these
# texts pass it. A new English overrun fails test_budgets.py, so this list only shrinks.
KNOWN_ENGLISH_OVERRUNS: frozenset[tuple[str, str]] = frozenset({
    ("nostromo.strip.value_5h", "100%"),                    # the value's ink meets the 5H bar's last cell
    ("nostromo.strip.value_5h_chip", "100%"),               # a critical value's chip covers the bar's last cells:
                                                            # every value of two digits or more, the same in VT323
    ("nostromo.strip.value_5h_chip", "60%"),
    ("nostromo.strip.value_5h_chip", "90%"),
    ("nostromo.strip.value_5h_chip", "93%"),
    ("nostromo.strip.value_5h_chip", "96%"),
    ("nostromo.strip.value_5h_chip", "99%"),
    ("nostromo.strip.value_7d_chip", "100%"),
    ("nostromo.tray.readout", "100"),                       # one device px past the ring's hole
    ("gates.floating.status_pill", "RATE LIMITED · 6 min"),     # the age is left out; the word stays
    ("gates.floating.status_pill", "RATE LIMITED · 44 min"),
    ("gates.floating.status_pill", "RATE LIMITED · 59 min"),
    ("gates.tooltip.status_pill", "RATE LIMITED · 6 min"),
    ("gates.tooltip.status_pill", "RATE LIMITED · 44 min"),
    ("gates.tooltip.status_pill", "RATE LIMITED · 59 min"),
    ("gates.floating.footer", "Guard holding | · Resumes Mon 10:00 AM (in 20h 20m)"),     # the rest is cut
    ("gates.floating.footer", "Guard holding | · Resumes Wed 12:59 PM (in 23h 59m)"),
} | {
    # fmtUsdTile and fmtUsdHero write more than six characters for these amounts (what they write in brackets):
    # about $99.995 to $99.999 ($100.00), $99,950 to $999,999 ($100.0K to $1000.0K), from about $99.95M ($100.0M),
    # about -$9.995 to -$99.999 (-$10.00 to -$100.00), about -$9,950 to -$999,999 ($-10.0K to $-1000.0K) and from about
    # -$9.95M down ($-10.0M); the hero also for $9,999.50 to $9,999.99 ($10,000) and -$999.50 to -$9,999.99 (-$1,000
    # to -$10,000). #91's money format settles them.
    (box, text) for box, shapes in (("dashboard.nostromo.cost_tile", _TILES), ("dashboard.night_city.cost_tile", _TILES),
                                    ("dashboard.gates.cost_tile", _TILES), ("dashboard.nostromo.cost_hero", _HEROES),
                                    ("dashboard.night_city.cost_hero", _HEROES), ("dashboard.gates.cost_hero", _HEROES))
    for text in shapes if len(text) > 6
})


# ── Budgets ───────────────────────────────────────────────────────────────────────────────────────────────────────

def budget(box: Box, scale: float = 1.0) -> float:
    """A pixel box's room, or what English takes there where it already runs past; a character box's limit."""
    if box.unit == "chars":
        return box.limit
    return max(box.limit, max((box.width(text, scale) for text in box.english), default=0.0))


def tight(figure: str) -> str:
    """A percent figure as a box sized for English's figures takes it: no space between the number and the sign,
    which stays on its language's side ("100%" for French's "100 %", Turkish's "%100" as it is), as
    formats.percent(..., tight=True) writes it."""
    return re.sub(r"\s*%\s*", "%", figure)


def texts(box: Box, language: str) -> list[str]:
    """What ``language`` puts in ``box``: English as drawn today, for the pseudo-locale that English made pseudo,
    the language's longest figures set in the box's text, and the text of the box's keys once it has them."""
    found: list[str] = []
    if language == i18n.DEFAULT:
        found += box.english
    elif language == i18n.PSEUDO:
        found += [i18n.pseudo_text(text) for text in box.english]
    if language != i18n.PSEUDO:
        for kind in box.kinds:
            for figure in FIGURES.get(kind, {}).get(language, ()):
                figure = tight(figure) if box.tight and kind == "percent" else figure
                found += [template.format(figure) for template in box.templates]
    if box.keys:
        catalog = i18n.load_catalog(language)
        for key in box.keys:
            entry = catalog.lookup(key)
            if entry is not None and isinstance(entry.value, str):
                found.append(str(catalog.text(key)))
    return list(dict.fromkeys(found))


@dataclass(frozen=True)
class Over:
    box: Box | Line
    language: str
    text: str
    scale: float
    width: float
    budget: float


def _worst(box: Box | Line, language: str, text: str, measure: Any, allowed: Any) -> Over | None:
    worst = None
    for scale in box.scales:
        measured, room = measure(scale), allowed(scale)
        if measured > room + 0.01 and (worst is None or measured - room > worst.width - worst.budget):
            worst = Over(box, language, text, scale, measured, room)
    return worst


def _shown(runs: Sequence[str]) -> str:
    """A line's runs as one text: "Guard holding | · Resumes 10:26 PM (in 2h 14m)"."""
    return " | ".join(run.strip() for run in runs if run.strip())


def line_budget(line: Line, scale: float = 1.0) -> float:
    """A line keeps its limit: English that runs past it is listed in KNOWN_ENGLISH_OVERRUNS."""
    return line.limit


def over(language: str, boxes: Sequence[Box | Line] | None = None) -> list[Over]:
    """Every text of ``language`` past its box's or line's budget, at the scale where it is furthest past. A line takes
    the widest text of ``language`` in each of its slots, so it is the worst the language can draw there."""
    found: list[Over] = []
    for box in (*BOXES, *LINES) if boxes is None else boxes:
        if isinstance(box, Line):
            if language == i18n.DEFAULT:
                for runs in box.english:
                    text = _shown(runs)
                    if (box.id, text) in KNOWN_ENGLISH_OVERRUNS:
                        continue
                    hit = _worst(box, language, text, lambda s, runs=runs: box.drawn(runs, s),
                                 lambda s: line_budget(box, s))
                    if hit:
                        found.append(hit)
                continue
            choice: list[str] = []

            def widest(scale: float, line: Line = box) -> float:
                total = 0.0
                choice.clear()
                for slot in line.slots:
                    best, best_text = 0.0, ""
                    for box_id in slot:
                        for text in texts(BOX[box_id], language):
                            w = BOX[box_id].width(text, scale) + line.added(box_id)
                            if w > best:
                                best, best_text = w, text
                    total += best
                    choice.append(best_text)
                return total

            hit = _worst(box, language, "", widest, lambda s, line=box: line_budget(line, s))
            if hit:
                widest(hit.scale)
                found.append(Over(box, language, _shown(choice), hit.scale, hit.width, hit.budget))
            continue
        for text in texts(box, language):
            if (box.id, text) in KNOWN_ENGLISH_OVERRUNS:
                continue
            hit = _worst(box, language, text, lambda s, t=text, b=box: b.width(t, s), lambda s, b=box: budget(b, s))
            if hit:
                found.append(hit)
    return found


def english_overruns() -> set[tuple[str, str]]:
    """Where English already takes more than the room, as the skins are drawn today (boxes and lines)."""
    found = {(box.id, text) for box in BOXES for text in box.english
             if max(box.width(text, scale) for scale in box.scales) > box.limit + 0.01}
    for line in LINES:
        for runs in line.english:
            if max(line.drawn(runs, scale) for scale in line.scales) > line.limit + 0.01:
                found.add((line.id, _shown(runs)))
    return found


def notes() -> dict[str, str]:
    """The budget note each box key carries beside it in en.json, so translators see the limit."""
    return {f"_budget.{key}": f"{box.limit:g} {box.unit} ({box.id}, {box.fit})" for box in BOXES for key in box.keys}


def limits_table(width: int = 120) -> str:
    """Every box's and line's limit as test_budgets.py's LIMITS holds them, to paste there when a skin moves a room."""
    lines, line = [], "   "
    for item in (*BOXES, *LINES):
        piece = f' "{item.id}": {round(item.limit, 3)!r},'
        if len(line) + len(piece) > width:
            lines.append(line)
            line = "   "
        line += piece
    return "\n".join([*lines, line])


# ── What the skins draw ───────────────────────────────────────────────────────────────────────────────────────────

_SURFACES = {"render_floating": "floating", "render_taskbar": "strip", "render_tooltip": "tooltip",
             "render_tray_icon": "tray"}


def drawn(scales: Sequence[float] = (1.0,), every_variant: bool = True) -> list[tuple[str, str, str, float, float]]:
    """Every run of text the frame dump draws at ``scales``, with the width its skin worked out as it drew it: (theme,
    surface, text, scale, width). The scale is the canvas's, so a strip that a short taskbar draws smaller says so. A
    tray icon's number is kept on the TRAY_SIZE icon only, in device px at scale 1. Text reaches a canvas through
    engine.Canvas.text_layer (Nostromo and Night City) or Gates' Ink.put; a tray number through Pillow's
    ImageDraw.text (Nostromo) or Gates' _text_px, which measures the whole number before Gates draws it glyph by glyph
    with ImageDraw.text, so a tray icon keeps its longest run. Without ``every_variant`` each theme draws its first
    variant only: the variants differ in colour, not in text. test_budgets.py compares these with the boxes' measures."""
    from PIL import ImageDraw

    found: set[tuple[str, str, str, float, float]] = set()
    now: list[tuple[str, str, int]] = [("", "", 0)]                # the theme and surface being drawn, a tray's size
    tray: list[tuple[str, str, str, float, float]] = []             # the runs one tray icon measured
    real = (engine.Canvas.text_layer, g.Ink.put, g._text_px, ImageDraw.ImageDraw.text, dump._save, dump._contact_sheet,
            dump.get_skin)

    def keep(text: Any, scale: float, width: float, tray_only: bool = False) -> None:
        theme, surface, size = now[0]
        if not surface or (surface == "tray") != tray_only:
            return
        run = (theme, surface, str(text), round(float(scale), 6), round(float(width), 4))
        if surface != "tray":
            found.add(run)
        elif size == TRAY_SIZE:
            tray.append(run)

    def layer(self, text, *args, **kwargs):  # type: ignore[no-untyped-def]
        out = real[0](self, text, *args, **kwargs)
        keep(text, self.scale, out[1])
        return out

    def put(self, text, *args, **kwargs):  # type: ignore[no-untyped-def]
        out = real[1](self, text, *args, **kwargs)
        if text:
            keep(text, self.scale, out)
        return out

    def text_px(face, text):  # type: ignore[no-untyped-def]
        out = real[2](face, text)
        keep(text, 1.0, out, tray_only=True)
        return out

    def draw(self, xy, text, *args, **kwargs):  # type: ignore[no-untyped-def]
        font = kwargs.get("font", args[1] if len(args) > 1 else None)
        if hasattr(font, "getlength"):
            keep(text, 1.0, font.getlength(str(text)), tray_only=True)
        return real[3](self, xy, text, *args, **kwargs)

    class Told:
        """A skin whose renders say which theme and surface they draw."""

        def __init__(self, skin: Any) -> None:
            self._skin = skin

        def __getattr__(self, name: str) -> Any:
            attr = getattr(self._skin, name)
            if name not in _SURFACES:
                return attr

            def render(model, theme, variant, *args, **kwargs):  # type: ignore[no-untyped-def]
                before = now[0]
                now[0] = (theme.id, _SURFACES[name], int(args[0]) if name == "render_tray_icon" else 0)
                tray.clear()
                try:
                    return attr(model, theme, variant, *args, **kwargs)
                finally:
                    if tray:
                        found.add(max(tray, key=lambda run: len(run[2])))
                    now[0] = before
            return render

    engine.Canvas.text_layer, g.Ink.put, g._text_px, ImageDraw.ImageDraw.text = layer, put, text_px, draw
    dump._save = lambda image, path, report: None
    dump._contact_sheet = lambda theme, skin, path, variant_filter=None, **_kwargs: path
    dump.get_skin = lambda theme_id: Told(real[6](theme_id))
    try:
        with tempfile.TemporaryDirectory(prefix="budgets-drawn-") as folder:
            if every_variant:
                dump.dump_frames(folder, scales=scales)
            else:
                for theme_id, theme in sorted(dump.load_theme_objects().items()):
                    dump.dump_frames(folder, [theme_id], variants=[theme.variants[0].id], scales=scales)
    finally:
        (engine.Canvas.text_layer, g.Ink.put, g._text_px, ImageDraw.ImageDraw.text, dump._save, dump._contact_sheet,
         dump.get_skin) = real
    return sorted(found)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="What a language's text would not fit in the widgets (#89).")
    parser.add_argument("--lang", default=i18n.PSEUDO, choices=i18n.DEV_LANGUAGES)
    parser.add_argument("--limits", action="store_true", help="print every limit, as test_budgets.py pins them")
    args = parser.parse_args(argv)
    if args.limits:
        print(limits_table())
        return 0
    rows = over(args.lang)
    print(f"{args.lang}: {len(rows)} texts past their budget, "
          f"{sum(r.box.enforced for r in rows)} of them in boxes or lines with no fallback or only shrinking")
    for r in sorted(rows, key=lambda r: (not r.box.enforced, r.box.id)):
        mark = "FAULT" if r.box.enforced else "cut  "
        print(f"  {mark} {r.box.id}: {json.dumps(r.text, ensure_ascii=False)} {r.width:.1f} > {r.budget:.1f} "
              f"{r.box.unit} at {r.scale:g}x ({r.box.fit})")
    return 1 if args.lang in i18n.OFFERED and any(r.box.enforced for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
