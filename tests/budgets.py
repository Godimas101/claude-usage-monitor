"""Width budgets (#89): the most room any language's text may take in each widget box sized for English, and the
character budgets of the dashboard's money tiles, the tray tooltip and the toasts.

Each box is one place a run of text must stay within a fixed width. The map of them (where each is drawn, and how its
room follows from the skin's constants) was worked out once from the skins and is kept here as data. A box's budget
is its room, or what English already takes there where English runs past the room by design (KNOWN_ENGLISH_OVERRUNS).
A text past the budget is a fault in a box with no fallback, or one that only shrinks its text (measured at the
smallest size it shrinks to). Boxes that cut with an ellipsis, clip or drop their text are reported instead, and so
are whole lines made of several runs (their parts have boxes of their own).

What goes in a box: English as the skins draw it today, each language's longest figures (FIGURES) and, once the
box's text moves to the catalog (#93 to #101), its keys in that language. Until then the pseudo-locale measures that
English through ``i18n.pseudo_text``. Widths are logical px in the skin's own fonts at every scale the frames are drawn at.

    python tests/budgets.py [--lang ID]     what a language's text would not fit (default: pseudo)
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from usage_monitor import i18n  # noqa: E402
from usage_monitor.render import dump  # noqa: E402
from usage_monitor.render.skins import gates, night_city, nostromo  # noqa: E402

SCALES: tuple[float, ...] = dump.SCALES
# Where English already runs past a box's room, as the skins draw it today: the budget there is what English takes.
# A new English overrun is a test failure (test_budgets.py), so this list only shrinks.
KNOWN_ENGLISH_OVERRUNS: frozenset[tuple[str, str]] = frozenset({
    ("night_city.floating.band_hold_chip", "HOLD 23H 59M"),     # the countdown drops out when it does not fit
    ("night_city.floating.band_hold_chip", "HOLD 2D 3H"),
    ("night_city.floating.band_hold_chip", "HOLD 2H 14M"),
    ("nostromo.strip.value_5h", "100%"),                         # the value's ink meets the bar's last cell
    ("nostromo.strip.value_5h_chip", "100%"),
    ("nostromo.strip.value_5h_chip", "90%"),
    ("nostromo.strip.value_5h_chip", "93%"),
    ("nostromo.strip.value_5h_chip", "96%"),
    ("nostromo.strip.value_5h_chip", "99%"),
    ("nostromo.strip.value_7d_chip", "100%"),
    ("nostromo.tray.readout", "100"),
})


@dataclass(frozen=True)
class Box:
    id: str                         # <skin or app>.<surface>.<part>
    unit: str                       # "px" (logical px at every scale) or "chars"
    font: str                       # nostromo, night_city: a font key; gates: "body" or "mono"; chars: ""
    size: float = 0.0
    tracking: float = 0.0
    weight: int = 0                 # gates' variable fonts
    opsz: float | None = None
    limit: float = 0.0              # the room
    fit: str = "none"               # none | shrink:<smallest px> | ellipsis | clip | other (the text is dropped)
    figures: bool = False
    composite: bool = False         # a whole line of several runs, listed for the record
    english: tuple[str, ...] = ()
    keys: tuple[str, ...] = ()      # catalog keys drawn in it, once its text is in the catalog (#93 to #101)

    @property
    def enforced(self) -> bool:
        """Whether a text past the budget is a fault: no fallback, or only shrinking."""
        return not self.composite and (self.fit == "none" or self.fit.startswith("shrink:"))

    @property
    def kind(self) -> str | None:
        """The figures it holds, for FIGURES."""
        if not self.figures:
            return None
        part = self.id.rsplit(".", 1)[-1]
        if "cost" in part:
            return "money"
        if "reset" in part:
            return "clock"
        if any("%" in text for text in self.english):
            return "percent"
        return None


# Each language's longest figures, as #89 lists them: percents with U+00A0 or with the sign first, clocks in both 12-
# and 24-hour forms (fr-CA's "16 h 12", es-MX's "4:12 p.m."), and the dollar as fr-CA and es-MX write it.
FIGURES: dict[str, dict[str, tuple[str, ...]]] = {
    "percent": {"en": ("100%",), "fr": ("100\u00a0%",), "es": ("100\u00a0%",), "tr": ("%100",)},
    "clock": {"en": ("4:12 PM",), "fr": ("16 h 12", "16:12"), "es": ("4:12 p.m.", "16:12"), "tr": ("16:12",)},
    "money": {"en": ("$5.96",), "fr": ("5,96 $ US",), "es": ("USD 5.96", "1,234.5")},
}

BOXES: tuple[Box, ...] = (
    Box("nostromo.floating.header_line", "px", "ibmplexmono/IBMPlexMono-SemiBold.ttf", size=10, tracking=1.2, limit=240, figures=True, composite=True, english=("LIVE | 12 S | NOMINAL", "LIVE | 12 S | ADVISORY", "LIVE | 12 S | CRITICAL (chip)", "LIVE | 12 S | [GUARD] | NOMINAL", "LIVE | 12 S | HOLD 2H 14M (chip)", "LIVE | 12 S | HOLD 2D 3H (chip)", "STALE | 41 M | LAST KNOWN", "OFFLINE | NO CARRIER", "PAUSED | HOLD", "RATE LIMITED | 6 M | [G] | THROTTLED", "NO LOGIN | AUTH REQUIRED", "LOADING | ACQUIRING", "RATE LIMITED | 59 M | HOLD 23H 59M (chip)", )),
    Box("nostromo.floating.status_pill", "px", "ibmplexmono/IBMPlexMono-SemiBold.ttf", size=10, tracking=1.2, limit=103.2, english=("LOADING", "LIVE", "STALE", "OFFLINE", "RATE LIMITED", "NO LOGIN", "PAUSED", )),
    Box("nostromo.floating.age", "px", "ibmplexmono/IBMPlexMono-Regular.ttf", size=10, tracking=0.6, limit=43.2, figures=True, english=("12 S", "41 M", "6 M", "59 M", "3 H", "47 H", "2 D", )),
    Box("nostromo.floating.state_word", "px", "ibmplexmono/IBMPlexMono-SemiBold.ttf", size=10, tracking=1.2, limit=111.2, english=("NOMINAL", "ADVISORY", "LAST KNOWN", "NO CARRIER", "HOLD", "ACQUIRING", "THROTTLED", "AUTH REQUIRED", )),
    Box("nostromo.floating.state_chip", "px", "ibmplexmono/IBMPlexMono-SemiBold.ttf", size=10, tracking=1.2, limit=103.2, figures=True, english=("CRITICAL", "HOLD", "HOLD 9M", "HOLD <1M", "HOLD 2H 14M", "HOLD 23H 59M", "HOLD 2D 3H", "HOLD 6D 23H", )),
    Box("nostromo.floating.armed_guard", "px", "ibmplexmono/IBMPlexMono-SemiBold.ttf", size=10, tracking=1.2, limit=58.8, fit="other", english=("[GUARD]", )),
    Box("nostromo.floating.armed_guard_short", "px", "ibmplexmono/IBMPlexMono-SemiBold.ttf", size=10, tracking=1.2, limit=26.4, fit="other", english=("[G]", )),
    Box("nostromo.floating.row_name_line", "px", "ibmplexmono/IBMPlexMono-SemiBold.ttf", size=10, tracking=1.2, limit=202, fit="ellipsis", figures=True, composite=True, english=("SESSION 5H", "WEEKLY 7D", "FABLE WEEKLY OF 50% SHARE", "FABLE WEEKLY 50%", "FABLE 50%", "WEEKLY SCOPED OF 100% SHARE", )),
    Box("nostromo.floating.row_value", "px", "vt323/VT323-Regular.ttf", size=22, limit=168.4, figures=True, english=("--", "0%", "2%", "13%", "93%", "100%", )),
    Box("nostromo.floating.cap_tag", "px", "ibmplexmono/IBMPlexMono-SemiBold.ttf", size=10, tracking=1.2, limit=112.4, fit="other", figures=True, english=("CAP 95%", "CAP 90%", "CAP 100%", )),
    Box("nostromo.floating.message", "px", "ibmplexmono/IBMPlexMono-SemiBold.ttf", size=10, tracking=0.8, limit=242, english=("AUTH LINK LOST.", "USE CLAUDE CODE TO RESTORE.", "AUTH REFUSED.", "RUN /login IN CLAUDE CODE.", "NO LOGIN FOUND", "OPEN THE DASHBOARD", )),
    Box("nostromo.hover.row_name_line", "px", "ibmplexmono/IBMPlexMono-SemiBold.ttf", size=10, tracking=1.2, limit=199, fit="ellipsis", figures=True, composite=True, english=("SESSION 5H", "WEEKLY 7D", "FABLE WEEKLY OF 50% SHARE", "FABLE WEEKLY 50%", "FABLE 50%", "WEEKLY SCOPED OF 100% SHARE", )),
    Box("nostromo.hover.row_value", "px", "vt323/VT323-Regular.ttf", size=22, limit=174.4, figures=True, english=("--", "2%", "13%", "12.3%", "99.5%", "100%", )),
    Box("nostromo.hover.cap_tag", "px", "ibmplexmono/IBMPlexMono-SemiBold.ttf", size=10, tracking=1.2, limit=109.4, fit="other", figures=True, english=("CAP 95%", "CAP 90%", "CAP 100%", )),
    Box("nostromo.hover.info_line", "px", "ibmplexmono/IBMPlexMono-Regular.ttf", size=10, tracking=0.6, limit=252, figures=True, composite=True, english=("RESETS 7:39 PM | BEHIND -18 PTS", "RESETS SAT 4:12 PM | BEHIND -55 PTS", "RESETS FRI 7:12 PM | AHEAD +21 PTS", "RESETS 6:26 PM | AHEAD +41 PTS", "RESETS SAT 4:12 PM | ON PACE", "RESETS -- | PACE --", "RESETS SAT 12:59 PM | BEHIND -100 PTS", )),
    Box("nostromo.hover.resets", "px", "ibmplexmono/IBMPlexMono-Regular.ttf", size=10, tracking=0.6, limit=144, figures=True, english=("RESETS --", "RESETS 7:39 PM", "RESETS 6:26 PM", "RESETS SAT 4:12 PM", "RESETS FRI 7:12 PM", "RESETS SAT 12:59 PM", )),
    Box("nostromo.hover.pace", "px", "ibmplexmono/IBMPlexMono-SemiBold.ttf", size=10, tracking=1.2, limit=126.6, figures=True, english=("PACE --", "ON PACE", "AHEAD +12 PTS", "BEHIND -12 PTS", "AHEAD +100 PTS", "BEHIND -100 PTS", )),
    Box("nostromo.hover.guard_lines", "px", "ibmplexmono/IBMPlexMono-Regular.ttf", size=10, tracking=0.6, limit=252, fit="other", figures=True, composite=True, english=("GUARD ARMED: SESSION 95%, WEEKLY 90%", "GUARD HOLDING: SESSION 96% (CAP 95%).", "RESUMES 6:26 PM (IN 2H 14M).", "GUARD HOLDING: WEEKLY 91% (CAP 90%).", "RESUMES FRI 7:12 PM (IN 2D 3H).", "GUARD ARMED", "GUARD HOLDING", )),
    Box("nostromo.strip.window_tag", "px", "ibmplexmono/IBMPlexMono-SemiBold.ttf", size=11, tracking=1.1, limit=22, figures=True, english=("5H", "7D", )),
    Box("nostromo.strip.value_5h", "px", "vt323/VT323-Regular.ttf", size=18, limit=24, figures=True, english=("--", "13%", "38%", "78%", "100%", )),
    Box("nostromo.strip.value_5h_chip", "px", "vt323/VT323-Regular.ttf", size=18, limit=20, figures=True, english=("90%", "93%", "96%", "99%", "100%", )),
    Box("nostromo.strip.value_7d", "px", "vt323/VT323-Regular.ttf", size=18, limit=32, figures=True, english=("--", "2%", "21%", "52%", "58%", "100%", )),
    Box("nostromo.strip.value_7d_chip", "px", "vt323/VT323-Regular.ttf", size=18, limit=28, figures=True, english=("90%", "91%", "100%", )),
    Box("nostromo.tray.readout", "px", "vt323/VT323-Regular.ttf", size=18, limit=20, figures=True, english=("--", "0", "13", "100", )),
    Box("night_city.floating.brand", "px", "rajdhani/Rajdhani-Bold.ttf", size=12, tracking=1.92, limit=133.111, english=("CLAUDE//USAGE", )),
    Box("night_city.floating.live_tag", "px", "rajdhani/Rajdhani-Bold.ttf", size=11, tracking=1.32, limit=110.417, english=("LIVE", )),
    Box("night_city.floating.status_chip", "px", "rajdhani/Rajdhani-Bold.ttf", size=11, tracking=0.66, limit=106.417, english=("PAUSED", "LOADING", )),
    Box("night_city.floating.guard_chip", "px", "rajdhani/Rajdhani-Bold.ttf", size=11, tracking=0.66, limit=39.056, fit="other", english=("GUARD", )),
    Box("night_city.floating.hold_chip", "px", "rajdhani/Rajdhani-Bold.ttf", size=11, tracking=0.66, limit=101.417, figures=True, english=("HOLD 23H 59M", "HOLD 6D 23H", "HOLD 2H 14M", "HOLD 2D 3H", "HOLD <1M", "HOLD", )),
    Box("night_city.floating.band_text", "px", "rajdhani/Rajdhani-Bold.ttf", size=11, tracking=0.88, limit=160.717, fit="ellipsis", english=("RELINK // USE CLAUDE CODE", "RELINK // RUN /login", "NO SIGNAL // NET DOWN", "RATE LIMITED", )),
    Box("night_city.floating.band_text_no_age", "px", "rajdhani/Rajdhani-Bold.ttf", size=11, tracking=0.88, limit=220, fit="ellipsis", english=("NO LOGIN // OPEN DASHBOARD", )),
    Box("night_city.floating.band_age", "px", "sharetechmono/ShareTechMono-Regular.ttf", size=11, tracking=0.22, limit=62.359, fit="other", figures=True, english=("41 M OLD", "59 S OLD", "47 H OLD", "2 D OLD", )),
    Box("night_city.floating.band_hold_text", "px", "rajdhani/Rajdhani-Bold.ttf", size=11, tracking=0.88, limit=168.921, fit="ellipsis", english=("RELINK // USE CLAUDE CODE", "RELINK // RUN /login", "NO SIGNAL // NET DOWN", "RATE LIMITED", )),
    Box("night_city.floating.band_hold_chip", "px", "rajdhani/Rajdhani-Bold.ttf", size=11, tracking=0.66, limit=47.359, fit="other", figures=True, english=("HOLD 23H 59M", "HOLD 2H 14M", "HOLD 2D 3H", "HOLD", )),
    Box("night_city.floating.row_name", "px", "rajdhani/Rajdhani-Bold.ttf", size=13, tracking=1.56, limit=137.372, fit="ellipsis", english=("SESSION", "WEEKLY", "WEEKLY SCOPED", )),
    Box("night_city.floating.row_chip", "px", "rajdhani/Rajdhani-Bold.ttf", size=11, tracking=0.66, limit=121.331, figures=True, english=("CRIT", "WARN", "HOLD", "CAP 95", "CAP 100", )),
    Box("night_city.floating.row_tag", "px", "rajdhani/Rajdhani-SemiBold.ttf", size=11, tracking=0.88, limit=91.192, fit="other", figures=True, english=("50% SHARE", "100% SHARE", )),
    Box("night_city.floating.row_reset", "px", "sharetechmono/ShareTechMono-Regular.ttf", size=11, tracking=0.22, limit=136.432, fit="other", figures=True, english=("RST 7:39 PM", "RST 12:59 PM", "RST SAT 4:00 PM", "RST WED 12:59 PM", )),
    Box("night_city.floating.row_value", "px", "sharetechmono/ShareTechMono-Regular.ttf", size=15, limit=112.188, figures=True, english=("100%", "13%", "--", )),
    Box("night_city.tooltip.row_name", "px", "rajdhani/Rajdhani-Bold.ttf", size=13, tracking=1.56, limit=133.476, fit="ellipsis", english=("SESSION", "WEEKLY", "WEEKLY SCOPED", )),
    Box("night_city.tooltip.row_chip", "px", "rajdhani/Rajdhani-Bold.ttf", size=11, tracking=0.66, limit=113.231, figures=True, english=("CRIT", "WARN", "HOLD", "CAP 95", "CAP 100", )),
    Box("night_city.tooltip.row_tag", "px", "rajdhani/Rajdhani-SemiBold.ttf", size=11, tracking=0.88, limit=87.297, fit="other", figures=True, english=("50% SHARE", "100% SHARE", )),
    Box("night_city.tooltip.row_value", "px", "sharetechmono/ShareTechMono-Regular.ttf", size=15, limit=112.188, figures=True, english=("99.9%", "13.5%", "100%", "--", )),
    Box("night_city.tooltip.info_reset", "px", "sharetechmono/ShareTechMono-Regular.ttf", size=11, tracking=0.22, limit=129.245, figures=True, english=("RST 7:39 PM", "RST 12:59 PM", "RST SAT 4:00 PM", "RST WED 12:59 PM", )),
    Box("night_city.tooltip.info_pace", "px", "rajdhani/Rajdhani-Bold.ttf", size=11, tracking=0.66, limit=117.433, figures=True, english=("PACE --", "ON PACE", "AHEAD +41", "AHEAD +100", "UNDER PACE -18", "UNDER PACE -100", )),
    Box("night_city.tooltip.guard_lead", "px", "rajdhani/Rajdhani-Bold.ttf", size=11, tracking=0.66, limit=236, english=("GUARD // HOLDING:", "GUARD // ARMED:", "GUARD // HOLDING", "GUARD // ARMED", )),
    Box("night_city.tooltip.guard_lines", "px", "sharetechmono/ShareTechMono-Regular.ttf", size=11, tracking=0.22, limit=236, fit="other", figures=True, composite=True, english=("SESSION 95%, WEEKLY 90%", "SESSION 96% (CAP 95%). RESUMES 6:26 PM (IN 2H 14M).", "WEEKLY 91% (CAP 90%). RESUMES FRI 7:12 PM (IN 2D 3H).", )),
    Box("night_city.strip.window_label", "px", "rajdhani/Rajdhani-Bold.ttf", size=11, tracking=1.32, limit=18, figures=True, english=("5H", "7D", )),
    Box("night_city.strip.percent", "px", "sharetechmono/ShareTechMono-Regular.ttf", size=11, limit=21, fit="shrink:8", figures=True, english=("100%", "13%", "--", )),
    Box("night_city.strip.status_live", "px", "rajdhani/Rajdhani-Bold.ttf", size=11, tracking=1.32, limit=40, english=("LIVE", )),
    Box("night_city.strip.status_word", "px", "rajdhani/Rajdhani-Bold.ttf", size=11, tracking=1.32, limit=45, fit="shrink:9", english=("STALE", "OFFLINE", "PAUSED", "LOADING", "LIMITED", "NO LOGIN", )),
    Box("night_city.strip.hold_chip", "px", "rajdhani/Rajdhani-Bold.ttf", size=11, tracking=0.66, limit=36, english=("HOLD", )),
    Box("gates.floating.title", "px", "body", size=12, weight=600, opsz=12, limit=84.54, english=("Claude usage", )),
    Box("gates.floating.status_word", "px", "body", size=10, weight=600, limit=76.184, english=("LIVE", "STALE", "OFFLINE", "RATE LIMITED", "NO LOGIN", "PAUSED", "LOADING", )),
    Box("gates.floating.status_pill", "px", "body", size=10, weight=600, limit=106.184, fit="other", figures=True, english=("LIVE · 12 s", "STALE · 41 min", "OFFLINE · 2 min", "PAUSED · 12 s", "RATE LIMITED · 6 min", "RATE LIMITED · 59 min", )),
    Box("gates.floating.row_label", "px", "body", size=12, weight=400, limit=212.84, fit="ellipsis", english=("Session", "Weekly", "Fable weekly", )),
    Box("gates.floating.row_tag", "px", "mono", size=11, weight=400, limit=139.496, fit="other", figures=True, english=("5H", "7D", "50% share", "100% share", )),
    Box("gates.floating.row_value", "px", "mono", size=15, weight=700, limit=110.196, fit="other", figures=True, english=("13%", "2%", "100%", "--", )),
    Box("gates.floating.cap_badge", "px", "body", size=10, weight=600, limit=134.336, fit="other", figures=True, english=("Cap 95%", "Cap 90%", "Cap 100%", )),
    Box("gates.floating.footer", "px", "body", size=11, weight=600, limit=258, fit="ellipsis", figures=True, composite=True, english=("Under pace · Resets 7:39 PM", "Ahead of pace · Resets 7:39 PM", "On pace · Resets 7:39 PM", "Nearly out · Resets 7:39 PM", "Ahead of pace · Resets Mon 10:00 AM", "No reset time yet.", "Waiting for your first reading.", "Guard holding · Resumes 6:26 PM (in 2h 14m)", "Guard holding · Resumes Fri 7:12 PM (in 2d 3h)", "Guard holding · Resumes Mon 10:00 AM (in 20h 20m)", "Hmm, we lost touch. Send Claude Code a message.", "Sign-in refused. Run /login in Claude Code.", "No connection. Retrying every minute.", "Paused. Resume to keep usage live.", "Rate limited. Showing the last reading.", "No sign-in found. Open the dashboard for help.", "Fetching your usage.", )),
    Box("gates.tooltip.title", "px", "body", size=12, weight=600, opsz=12, limit=105.54, english=("Claude usage", )),
    Box("gates.tooltip.status_word", "px", "body", size=10, weight=600, limit=97.184, english=("LIVE", "STALE", "OFFLINE", "RATE LIMITED", "NO LOGIN", "PAUSED", "LOADING", )),
    Box("gates.tooltip.status_pill", "px", "body", size=10, weight=600, limit=127.184, fit="other", figures=True, english=("LIVE · 12 s", "STALE · 41 min", "OFFLINE · 2 min", "PAUSED · 12 s", "RATE LIMITED · 6 min", "RATE LIMITED · 59 min", )),
    Box("gates.tooltip.row_label", "px", "body", size=12, weight=400, limit=204.05, fit="ellipsis", english=("Session", "Weekly", "Fable weekly", )),
    Box("gates.tooltip.row_tag", "px", "mono", size=11, weight=400, limit=130.706, fit="other", figures=True, english=("5H", "7D", "50% share", "100% share", )),
    Box("gates.tooltip.row_value", "px", "mono", size=15, weight=700, limit=110.196, fit="other", figures=True, english=("13%", "96%", "99.9%", "100%", "--", )),
    Box("gates.tooltip.cap_badge", "px", "body", size=10, weight=600, limit=125.546, fit="other", figures=True, english=("Cap 95%", "Cap 90%", "Cap 100%", )),
    Box("gates.tooltip.resets", "px", "body", size=11, weight=400, limit=132.92, figures=True, english=("Resets 7:39 PM", "Resets Sat 4:12 PM", "Resets Fri 7:12 PM", "Resets Mon 10:00 AM", )),
    Box("gates.tooltip.pace", "px", "body", size=11, weight=600, limit=153.96, figures=True, english=("On pace", "Under pace by 18 pts", "Ahead of pace by 12 pts", "Ahead of pace by 100 pts", )),
    Box("gates.tooltip.guard_lead", "px", "body", size=11, weight=600, limit=220, english=("Guard armed:", "Guard holding:", "Guard armed", "Guard holding", )),
    Box("gates.tooltip.guard_text", "px", "body", size=11, weight=400, limit=220, fit="other", figures=True, english=("session 95%, weekly 90%", "session 96% (cap 95%).", "Resumes 6:26 PM (in 2h 14m).", "weekly 91% (cap 90%).", "Resumes Fri 7:12 PM (in 2d 3h).", "Fable weekly 100%,", )),
    Box("gates.tooltip.footer", "px", "body", size=11, weight=600, limit=258, fit="ellipsis", composite=True, english=("Hmm, we lost touch. Send Claude Code a message.", "Sign-in refused. Run /login in Claude Code.", "No connection. Retrying every minute.", "Paused. Resume to keep usage live.", "Rate limited. Showing the last reading.", "No sign-in found. Open the dashboard for help.", "Fetching your usage.", )),
    Box("gates.strip.window_tag", "px", "body", size=11, weight=600, limit=24, figures=True, english=("5H", "7D", )),
    Box("gates.strip.value", "px", "mono", size=11, weight=700, limit=34, figures=True, english=("13%", "2%", "100%", "--", )),
    Box("gates.tray.number", "px", "body", size=10, weight=600, limit=18.8, fit="shrink:8", figures=True, english=("--", "13", "99", "100", )),
    Box("nostromo.dashboard.cost_tile", "chars", "", size=44, limit=6, figures=True, english=("--", "$0.00", "$99.99", "$999", "$9.9K", "$99.9K", "$100.00", "$100.0K", "$1000.0K", )),
    Box("night_city.dashboard.cost_tile", "chars", "", size=28, limit=6, figures=True, english=("--", "$0.00", "$99.99", "$999", "$9.9K", "$99.9K", "$100.00", "$100.0K", "$1000.0K", )),
    Box("gates.dashboard.cost_tile", "chars", "", size=20, limit=6, figures=True, english=("--", "$0.00", "$99.99", "$999", "$9.9K", "$99.9K", "$100.00", "$100.0K", "$1000.0K", )),
    Box("nostromo.dashboard.cost_hero", "chars", "", size=88, limit=6, fit="shrink:52", figures=True, english=("--", "$0.00", "$99.99", "$9,999", "$99.9K", "$100.00", "$10,000", "$100.0K", "$1000.0K", )),
    Box("night_city.dashboard.cost_hero", "chars", "", size=52, limit=6, figures=True, english=("--", "$0.00", "$99.99", "$9,999", "$99.9K", "$100.00", "$10,000", "$100.0K", "$1000.0K", )),
    Box("gates.dashboard.cost_hero", "chars", "", size=44, limit=6, fit="shrink:30", figures=True, english=("--", "$0.00", "$99.99", "$9,999", "$99.9K", "$100.00", "$10,000", "$100.0K", "$1000.0K", )),
    Box("app.tray.tooltip", "chars", "", size=0, limit=127, fit="ellipsis", figures=True, english=("Claude usage - loading", "Claude usage - no login", "Claude usage - session 13% - weekly 2%", "Claude usage - session 13% - weekly 2% - rate limited", "Claude usage - session 38% - weekly 52% - guard armed", "Claude usage - session 100% - weekly 100% - rate limited - guard holding, resumes Thu 10:59 PM (in 23h 59m)", "Claude usage - session 100% - weekly 100% - rate limited - guard holding, resumes Thu 10:59 PM (in 23h 59m) (dev)", )),
    Box("app.toast.alert_threshold.title", "chars", "", size=0, limit=63, fit="ellipsis", figures=True, english=("Session limit at 100%", "Weekly limit at 100%", "Weekly scoped limit at 100%", "Fable weekly limit at 100%", )),
    Box("app.toast.alert_resets.body", "chars", "", size=0, limit=255, fit="ellipsis", figures=True, english=("Resets 12:59 PM", "Resets Wed 12:59 PM", )),
    Box("app.toast.alert_reset.title", "chars", "", size=0, limit=63, fit="ellipsis", english=("Session limit reset", "Weekly limit reset", "Weekly scoped limit reset", "Fable weekly limit reset", "Usage limit reset", )),
    Box("app.toast.alert_reset_ended.body", "chars", "", size=0, limit=255, fit="ellipsis", figures=True, english=("Usage is back to 0%.", )),
    Box("app.toast.alert_credits.title", "chars", "", size=0, limit=63, fit="ellipsis", english=("Usage credits in use", )),
    Box("app.toast.alert_credits.body", "chars", "", size=0, limit=255, fit="ellipsis", figures=True, english=("Used 1,234.56 of 10,000.00 USD", "Used 1,234.56 USD", "Used 1,234.56", )),
    Box("app.toast.update.title", "chars", "", size=0, limit=63, fit="ellipsis", figures=True, english=("Claude Usage Monitor v2.3.0 is available", "Claude Usage Monitor v3.0.0-rc.1 is available", )),
    Box("app.toast.update.body", "chars", "", size=0, limit=255, fit="ellipsis", english=("Open the tray menu or the dashboard to update", )),
    Box("app.toast.update_note.title", "chars", "", size=0, limit=63, fit="ellipsis", english=("Claude Usage Monitor updated", )),
    Box("app.toast.update_note.body", "chars", "", size=0, limit=250, fit="clip", figures=True, english=("Setup closed the monitor's tool in a Claude Code session. If that session shows an error, type /mcp there or restart it.", "Setup closed the monitor's tool in 12 Claude Code sessions. If one shows an error, type /mcp there or restart it.", "A session waiting on the usage guard was let go early.", "12 sessions waiting on the usage guard were let go early.", "Setup closed the monitor's tool in a Claude Code session. If that session shows an error, type /mcp there or restart it. 12 sessions waiting on the usage guard were let go early.", )),
)


@lru_cache(maxsize=None)
def _theme(skin: str) -> Any:
    return dump.load_theme_objects()[skin]


def width(box: Box, text: str, scale: float = 1.0) -> float:
    """``text`` in ``box``: logical px in the skin's own font (at its smallest size, for a box that shrinks), or
    characters."""
    if box.unit == "chars":
        return float(len(text))
    skin = box.id.split(".", 1)[0]
    size, tracking = box.size, box.tracking
    if box.fit.startswith("shrink:"):
        smallest = float(box.fit.split(":", 1)[1])
        size, tracking = smallest, (tracking * smallest / box.size if box.size else tracking)
    if skin == "gates":
        ink = gates.Ink(8, 8, scale, gates._fonts(_theme(skin)))
        typeface = ink.fonts.mono if box.font == "mono" else ink.fonts.body
        return ink.advance(text, ink.face(typeface, size, box.weight or 400, box.opsz))
    module = nostromo if skin == "nostromo" else night_city
    cv = module._canvas(8, 8, scale, _theme(skin))
    return cv.advance(text, cv.font(box.font, size), tracking)


def budget(box: Box, scale: float = 1.0) -> float:
    """The room, or what English takes there where it already runs past. A character limit the app enforces by
    cutting (the tooltip, the toasts) is the limit itself."""
    if box.unit == "chars" and box.fit in ("ellipsis", "clip"):
        return box.limit
    return max(box.limit, max((width(box, text, scale) for text in box.english), default=0.0))


def texts(box: Box, language: str) -> list[str]:
    """What ``language`` puts in ``box``: English as drawn today, for the pseudo-locale that English made pseudo,
    the language's longest figures, and the text of the box's keys once it has them."""
    found: list[str] = []
    if language == i18n.DEFAULT:
        found += box.english
    elif language == i18n.PSEUDO:
        found += [i18n.pseudo_text(text) for text in box.english]
    if box.kind and language != i18n.PSEUDO:
        found += FIGURES[box.kind].get(language, ())
    if box.keys:
        catalog = i18n.load_catalog(language)
        for key in box.keys:
            entry = catalog.lookup(key)
            if entry is not None and isinstance(entry.value, str):
                found.append(str(catalog.text(key)))
    return list(dict.fromkeys(found))


@dataclass(frozen=True)
class Over:
    box: Box
    language: str
    text: str
    scale: float
    width: float
    budget: float


def over(language: str, boxes: Sequence[Box] | None = None) -> list[Over]:
    """Every text of ``language`` past its box's budget, at the scale where it is furthest past."""
    found = []
    for box in BOXES if boxes is None else boxes:
        if box.composite:
            continue
        for text in texts(box, language):
            worst = None
            for scale in (SCALES if box.unit == "px" else (1.0,)):
                measured, allowed = width(box, text, scale), budget(box, scale)
                if measured > allowed + 0.01 and (worst is None or measured - allowed > worst.width - worst.budget):
                    worst = Over(box, language, text, scale, measured, allowed)
            if worst is not None:
                found.append(worst)
    return found


def english_overruns() -> set[tuple[str, str]]:
    """Where English already takes more than the room, as the skins are drawn today."""
    return {(box.id, text) for box in BOXES if box.unit == "px" and not box.composite for text in box.english
            if max(width(box, text, scale) for scale in SCALES) > box.limit + 0.01}


def notes() -> dict[str, str]:
    """The budget note each box key carries beside it in en.json, so translators see the limit."""
    return {f"_budget.{key}": f"{box.limit:g} {box.unit} ({box.id}, {box.fit})" for box in BOXES for key in box.keys}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="What a language's text would not fit in the widgets (#89).")
    parser.add_argument("--lang", default=i18n.PSEUDO, choices=i18n.DEV_LANGUAGES)
    args = parser.parse_args(argv)
    rows = over(args.lang)
    print(f"{args.lang}: {len(rows)} texts past their budget, "
          f"{sum(r.box.enforced for r in rows)} of them in boxes with no fallback or only shrinking")
    for r in sorted(rows, key=lambda r: (not r.box.enforced, r.box.id)):
        mark = "FAULT" if r.box.enforced else "cut  "
        print(f"  {mark} {r.box.id}: {json.dumps(r.text)} {r.width:.1f} > {r.budget:.1f} {r.box.unit}"
              f" at {r.scale:g}x ({r.box.fit})")
    return 1 if args.lang in i18n.OFFERED and any(r.box.enforced for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
