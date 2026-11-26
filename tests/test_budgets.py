"""The width budgets (#89; tests/budgets.py). Each box measures its text as its skin draws it, at every scale the skin
draws it at, and every run the frame dump draws stands in a box; every room is pinned, so a change to a skin that moves
one shows here; English runs past its room only where it already did; no offered language's text runs past a box that
has no fallback or only shrinks; and every other language, the pseudo-locale among them, gets a report."""
from __future__ import annotations

import ast
import itertools
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import budgets
from usage_monitor import cldr, formats, i18n
from usage_monitor.render import dump, engine
from usage_monitor.render.skins import RenderOptions
from usage_monitor.render.skins import gates as g
from usage_monitor.render.skins import night_city as nc
from usage_monitor.render.skins import nostromo as nos
from usage_monitor.ui import tray

REPO = Path(__file__).resolve().parent.parent
PINNED_TZ = "UTC0"                  # the frame dump's clocks, as tests/frame_digests.py pins them
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Every box's and line's limit in its unit, as budgets.py works it out from its skin's constants and the English beside
# it. After a change to a skin that moves a room, `python tests/budgets.py --limits` prints this table again.
LIMITS: dict[str, float] = {
    "nostromo.floating.status_pill": 129.6, "nostromo.floating.age": 37.2, "nostromo.floating.state_word": 105.2,
    "nostromo.floating.state_chip": 97.2, "nostromo.floating.hold_chip": 201.6, "nostromo.floating.hold_word": 201.6,
    "nostromo.floating.armed_guard": 50.4, "nostromo.floating.armed_guard_short": 21.6,
    "nostromo.floating.row_name": 202.0, "nostromo.floating.row_qualifier": 102.4, "nostromo.floating.row_value": 223.6,
    "nostromo.floating.cap_tag": 112.4, "nostromo.floating.message": 242, "nostromo.tooltip.row_name": 199.0,
    "nostromo.tooltip.row_qualifier": 99.4, "nostromo.tooltip.row_value": 229.6, "nostromo.tooltip.cap_tag": 109.4,
    "nostromo.tooltip.resets": 252, "nostromo.tooltip.pace": 120.6, "nostromo.tooltip.pace_short": 120.6,
    "nostromo.tooltip.guard_lead": 252, "nostromo.tooltip.guard_lines": 252, "nostromo.strip.window_tag": 20.0,
    "nostromo.strip.value_5h": 24.0, "nostromo.strip.value_5h_chip": 20.0, "nostromo.strip.value_7d": 32.0,
    "nostromo.strip.value_7d_chip": 28.0, "nostromo.tray.readout": 20.0, "night_city.floating.brand": 179.746,
    "night_city.floating.live_tag": 102.417, "night_city.floating.status_chip": 98.417,
    "night_city.floating.guard_chip": 39.056, "night_city.floating.hold_chip": 93.417,
    "night_city.floating.hold_word": 93.417, "night_city.floating.band_text": 220.0,
    "night_city.floating.band_text_no_age": 220, "night_city.floating.band_age": 62.359,
    "night_city.floating.band_hold_text": 168.921, "night_city.floating.band_hold_chip": 175.866,
    "night_city.floating.band_hold_word": 175.866, "night_city.floating.row_name": 137.372,
    "night_city.floating.row_chip": 153.065, "night_city.floating.row_tag": 91.192,
    "night_city.floating.row_reset": 128.331, "night_city.floating.row_value": 143.921,
    "night_city.tooltip.row_name": 129.271, "night_city.tooltip.row_chip": 144.965,
    "night_city.tooltip.row_tag": 83.092, "night_city.tooltip.row_value": 143.921,
    "night_city.tooltip.info_reset": 228.0, "night_city.tooltip.info_pace": 109.433,
    "night_city.tooltip.info_pace_short": 109.433, "night_city.tooltip.guard_lead": 236,
    "night_city.tooltip.guard_lines": 236, "night_city.strip.window_label": 16.0, "night_city.strip.percent": 21,
    "night_city.strip.status_live": 36, "night_city.strip.status_word": 45, "night_city.strip.hold_chip": 36,
    "gates.floating.title": 132.836, "gates.floating.status_word": 76.184, "gates.floating.status_word_shrunk": 76.184,
    "gates.floating.loading_word": 106.184, "gates.floating.loading_word_shrunk": 106.184,
    "gates.floating.status_pill": 76.184, "gates.tooltip.title": 153.836, "gates.tooltip.status_word": 97.184,
    "gates.tooltip.status_word_shrunk": 97.184, "gates.tooltip.loading_word": 127.184,
    "gates.tooltip.loading_word_shrunk": 127.184, "gates.tooltip.status_pill": 97.184,
    "gates.floating.row_label": 212.84, "gates.floating.row_tag": 100.124, "gates.floating.row_value": 228.02,
    "gates.floating.cap_badge": 52.972, "gates.tooltip.row_label": 204.05, "gates.tooltip.row_tag": 91.334,
    "gates.tooltip.row_value": 228.02, "gates.tooltip.cap_badge": 46.246, "gates.tooltip.resets": 258.0,
    "gates.tooltip.pace": 145.962, "gates.tooltip.pace_short": 145.962, "gates.tooltip.guard_lead": 220,
    "gates.tooltip.guard_text": 220, "gates.floating.stale_lead": 109.258, "gates.floating.stale_step": 155.194,
    "gates.floating.stale_step_alone": 258.0, "gates.floating.refused_lead": 124.867,
    "gates.floating.refused_step": 179.328, "gates.floating.refused_step_alone": 258.0,
    "gates.floating.no_login_lead": 111.546, "gates.floating.no_login_step": 170.077,
    "gates.floating.no_login_step_alone": 258.0, "gates.floating.rate_limited_lead": 236.66,
    "gates.floating.rate_limited_rest": 195.102, "gates.floating.pace_lead": 231.27,
    "gates.floating.pace_reset": 186.214, "gates.floating.hold_lead": 231.27, "gates.floating.hold_rest": 186.038,
    "gates.floating.footer_line": 258, "gates.strip.window_tag": 22.0, "gates.strip.value": 38,
    "gates.tray.number": 17.013, "dashboard.nostromo.cost_tile": 6, "dashboard.night_city.cost_tile": 6,
    "dashboard.gates.cost_tile": 6, "dashboard.nostromo.cost_hero": 6, "dashboard.night_city.cost_hero": 6,
    "dashboard.gates.cost_hero": 6, "app.tray.tooltip": 127, "app.toast.alert_threshold.title": 63,
    "app.toast.alert_resets.body": 255, "app.toast.alert_reset.title": 63, "app.toast.alert_reset_ended.body": 255,
    "app.toast.alert_credits.title": 63, "app.toast.alert_credits.body": 255, "app.toast.update.title": 63,
    "app.toast.update.body": 255, "app.toast.update_note.title": 63, "app.toast.update_note.body": 250,
    "nostromo.floating.header_line": 240.0, "nostromo.floating.row_name_line": 202.0,
    "nostromo.tooltip.row_name_line": 199.0, "nostromo.tooltip.info_line": 252, "night_city.floating.header_line": 223,
    "night_city.floating.band_line": 220, "night_city.floating.band_hold_line": 220,
    "night_city.tooltip.info_line": 236, "gates.floating.header_line": 216, "gates.tooltip.header_line": 237,
    "gates.floating.row_line": 258, "gates.tooltip.row_line": 258, "gates.tooltip.info_line": 258,
    "gates.floating.stale_footer": 258, "gates.floating.refused_footer": 258, "gates.floating.no_login_footer": 258,
    "gates.floating.rate_limited_footer": 258, "gates.floating.pace_footer": 258, "gates.floating.hold_footer": 258,
}

# Where a box's runs are drawn. The hover cards draw the floating widget's header, band, message and footer with the
# floating widget's own code and rooms, so a floating box stands on both.
STANDS_ON: dict[str, tuple[str, ...]] = {"floating": ("floating", "tooltip"), "tooltip": ("tooltip",),
                                          "strip": ("strip",), "tray": ("tray",)}

# The boxes the frame dump never draws: [G], since every sample state leaves room for [GUARD]; and the pace's short
# form, which English never needs (#94). Night City's (#95, critic 3): HOLD alone in the header and in the band (the
# dump's guard holds all have a resume time, and none holds over a stale reading), and the pace's short form, which
# English never needs either. Gates' (#96, critic 12, from this test's failure): the pace's points, and each sign-in
# step alone, which English never needs (its leads and steps fit together); and the refused footer's lead and step:
# the dump has no refused sign-in.
NOT_DRAWN_WHOLE = frozenset({"nostromo.floating.armed_guard_short", "nostromo.tooltip.pace_short",
                             "night_city.floating.hold_word", "night_city.floating.band_hold_word",
                             "night_city.tooltip.info_pace_short", "gates.tooltip.pace_short",
                             "gates.floating.stale_step_alone", "gates.floating.no_login_step_alone",
                             "gates.floating.refused_step_alone", "gates.floating.refused_lead",
                             "gates.floating.refused_step"})

# What still runs past before French or Spanish is offered (#107), each with the ticket that settles it: #91 the
# dashboard's money figures (#94, #95 and #96 settled the skins' boxes). The dollar is the plain "$" (Chris's decision
# on #91), and the boxes sized for English's figures (the strips' values, the cap badge on Gates' hover card, the
# dashboard's cost tiles and big cost figure) draw their percents and dollars without the space beside the sign
# (Box.tight, Chris's decision on #91): Turkish's "%100" fits, and so do the cents and whole dollars of every language.
# Gates' floating badge keeps the space; beside a row's own spaced share and value (critic 5 of #96) French's and
# Spanish's badge is left out there (ACCEPTED_CUTS). Recompute with budgets.over() after a change, never by hand.
_MONEY = tuple(f"dashboard.{skin}.cost_{part}" for part in ("tile", "hero")
               for skin in ("nostromo", "night_city", "gates"))
TIGHT_BOXES = frozenset({"nostromo.strip.value_5h", "nostromo.strip.value_5h_chip", "nostromo.strip.value_7d",
                         "nostromo.strip.value_7d_chip", "night_city.strip.percent", "gates.tooltip.cap_badge",
                         *_MONEY})
# #91: a compact figure keeps CLDR's space before its unit, even tight ("99,9 k$", 7 characters where English's "$99.9K"
# has 6); Chris's decision covered the space beside the sign only, so the compact tiles wait on his call.
_COMPACT = {language: {(box, budgets.tight(figure)) for box in _MONEY for figure in budgets.PAGE_MONEY[language]}
            for language in ("fr", "es", "tr")}
_FR_DAYS = ("LUN.", "MAR.", "MER.", "JEU.", "VEN.", "SAM.", "DIM.")
FOR_91: dict[str, set[tuple[str, str]]] = {
    "fr": _COMPACT["fr"],                                                 # #91: the compact tiles (above)
    "es": _COMPACT["es"],                                                 # #91: the compact tiles (above)
    "tr": _COMPACT["tr"],                                                 # #91: the compact tiles (above)
}
# What the budgets report and nothing will settle, kept apart from FOR_91 (critic 7 of #94's plan): a cut that comes
# from a CLDR format, not from a word, so no draft (#104 to #106) can shorten it. Each row says which format tags write
# it and why the cut is right. A cut a draft's word causes is no row here: it goes in that draft's reviewer questions.
# Recompute with budgets.over() after a change, never by hand.
# Nostromo's header leaves its age out before it cuts the pill (Q2, nostromo.HEADER_GIVE_WAY), and its rooms in a
# language are its own (budgets.header_room): beside the widest pill and a tag's own widest hold chip (CLDR's units:
# fr-FR's and es-ES's HOLD 23H 59MIN, es-CO's HOLD 23 H 59 MIN, against English's HOLD 23H 59M) the age has 22.8 px, or
# 8.4 in es-CO, where English's has 37.2; fr-CA's chip is English's width and keeps every age. The budgets pair each
# box's widest texts, so a row is the worst a tag can draw, at every age of that shape ("59MIN" stands for "41MIN").
_LEFT_OUT = "left out beside the tag's own HOLD 23H 59MIN"
_ES_CO = "es-CO's narrow unit, spaced: beside its own HOLD 23 H 59 MIN no age fits (8.4 px)"
# Night City's (#95): its rooms in a language are its own tags' too (budgets.nc_room), so a reset or an age a tag writes
# wider than English's is reported where its line gives way. Each row names the tags that write it and says whether a
# draft's word could settle it (critic 14 of #95's plan); the budgets measure the pace's points with both signs.
_FR_CA_ROW = ("fr-CA's 12-hour day clock (129.4 px), 1.1 px past the room beside SESSION: the floating row leaves its "
              "reset out (a weekday is shown only for a session that resets on another day) and the hover card shows "
              "it whole; a draft's shorter RST or SESSION (#104) could settle it")
_FR_CA_PACE = ("beside fr-CA's 12-hour day clock in the hover line's reset (RST LUN. 12 H 59 P.M., 129.4 px) the pace has "
               "78.6 px: it takes its short form, its signed points; English's UNDER PACE, which French shows until "
               "#104, needs 80.3 px at -18 and 86.8 at -100, so a draft's shorter pace word could settle it")
_ES_PACE = ("beside a 12-hour day clock with its spaced p. m. (RST MIÉ, 12:59 P. M., 123.2 px: es-ES's and most Latin "
            "American tags' on a 12-hour clock) the pace has 84.8 px: it takes its short form, its signed points; "
            "English's UNDER PACE -100, which Spanish shows until #105, needs 86.8 px, so a draft's shorter pace word "
            "could settle it")
# Gates' (#96): its rooms in a language are its own tags' too (budgets.g_room, g_pill_room, g_badge_room). Each row
# names the tags that write it and says whether a draft's word could settle it (critic 15 of #96's plan).
_HOLD = ("the guard footer's rest after Guard holding, beside a 12-hour day clock, is cut at its end (186 px), as "
         "English's own widest is (KNOWN_ENGLISH_OVERRUNS); the hover card's guard bar shows it whole; 24-hour tags fit; "
         "a draft's shorter Guard holding or Resumes")
_FR_HOLD = f"{_HOLD}, which French shows in English until #104, could settle it"
_ES_HOLD = f"{_HOLD}, which Spanish shows in English until #105, could settle it"
_BADGE = ("{tags}: the share's, the value's and the badge's spaced percent (CLDR's) beside a scoped model's row at a "
          "100 % share leave the {surface} badge {room} px where English's has {limit:g}: it is left out (drawn only "
          "where it fits; the value's alert colour and the footer carry the hold); a draft's shorter share or weekly, "
          "which {language} shows in English until {ticket}, could settle it")
_FR_BADGE = {surface: _BADGE.format(tags="every French tag but fr-CH", surface=surface, room=room, limit=limit,
                                    language="French", ticket="#104")
             for surface, room, limit in (("floating", 37.7, 52.972), ("hover card's", 31.0, 46.246))}
_ES_BADGE = {surface: _BADGE.format(tags="es-ES's (es-EA's, es-GQ's, es-IC's and es-PH's too)", surface=surface,
                                    room=room, limit=limit, language="Spanish", ticket="#105")
             for surface, room, limit in (("floating", 37.7, 52.972), ("hover card's", 31.0, 46.246))}
_ES_PILL = ("es-CO's narrow day, spaced and in full ( · 100 días): beside English's RATE LIMITED, which Spanish shows "
            "until #105, the pill leaves its age out without the guard (108.7 px against 106.2) and beside the armed "
            "shield on the hover card (against 107.2), where English keeps its widest age; a draft's shorter status word "
            "could settle it")
ACCEPTED_CUTS: dict[str, dict[tuple[str, str], str]] = {
    "fr": {
        ("gates.floating.cap_badge", "Cap 100\u00a0%"): _FR_BADGE["floating"],
        ("gates.tooltip.cap_badge", "Cap 100%"): _FR_BADGE["hover card's"],
        ("gates.floating.hold_rest", " · Resumes sam. 12 h 59\u202fp.m. (in 23h 59m)"): f"fr-CA's (201.5 px): {_FR_HOLD}",
        ("gates.floating.hold_rest", " · Resumes sam. 12:59\u202fPM (in 23h 59min)"): (
            f"fr-FR's and most French tags' on a 12-hour clock (195.9 px): {_FR_HOLD}"),
        ("gates.floating.hold_rest", " · Resumes sam. 12:59\u202fp.m. (in 23h 59min)"): f"fr-MA's (200.6 px): {_FR_HOLD}",
        **{("night_city.floating.row_reset", f"RST {day} 12 H 59{chr(0x202F)}P.M."): _FR_CA_ROW for day in _FR_DAYS},
        ("night_city.tooltip.info_pace", "UNDER PACE -100"): _FR_CA_PACE,
        ("night_city.tooltip.info_pace", "UNDER PACE +100"): _FR_CA_PACE,
        ("nostromo.floating.age", "59MIN"): f"the narrow minute of every French tag but fr-CA (59M): {_LEFT_OUT}",
        ("nostromo.floating.age", "100J"): f"every French tag's narrow day: {_LEFT_OUT} (every tag's but fr-CA's)",
    },
    "es": {
        ("gates.floating.status_pill", "RATE LIMITED · 100 días"): _ES_PILL,
        ("gates.tooltip.status_pill", "RATE LIMITED · 100 días"): _ES_PILL,
        ("gates.floating.cap_badge", "Cap 100\u00a0%"): _ES_BADGE["floating"],
        ("gates.tooltip.cap_badge", "Cap 100%"): _ES_BADGE["hover card's"],
        ("gates.floating.hold_rest", " · Resumes dom 12:59\u202fp.m. (in 23h 59min)"): (
            f"es-MX's and es-US's on a 12-hour clock (200.8 px): {_ES_HOLD}"),
        ("gates.floating.hold_rest", " · Resumes dom, 12:59\u202fp.m. (in 23h 59min)"): (
            f"es-419's (es-BR's, es-BZ's and es-CU's too) on a 12-hour clock (203.2 px): {_ES_HOLD}"),
        ("gates.floating.hold_rest", " · Resumes dom, 12:59\u202fp.\u202fm. (in 23 h 59 min)"): (
            f"es-CO's on a 12-hour clock (212.2 px): {_ES_HOLD}"),
        ("gates.floating.hold_rest", " · Resumes dom, 12:59\u202fp.\u202fm. (in 23h 59min)"): (
            f"es-ES's and most Latin American tags' on a 12-hour clock (206.2 px): {_ES_HOLD}"),
        ("nostromo.floating.age", "59MIN"): f"the narrow minute of every Spanish tag but es-CO: {_LEFT_OUT}",
        ("nostromo.floating.age", "100D"): f"es-ES's narrow day (es-MX's, es-US's, es-EA's, es-GQ's, es-IC's and "
                                           f"es-PH's too): {_LEFT_OUT}",
        ("nostromo.floating.age", "100DD."): f"es-419's narrow day (es-AR's and most Latin American tags'): {_LEFT_OUT}",
        ("nostromo.floating.age", "100D."): f"es-DO's narrow day: {_LEFT_OUT}",
        ("nostromo.floating.age", "59SEG."): f"es-AR's narrow second: {_LEFT_OUT}",
        ("nostromo.floating.age", "59 S"): _ES_CO,
        ("nostromo.floating.age", "59 MIN"): _ES_CO,
        ("nostromo.floating.age", "47 H"): _ES_CO,
        ("nostromo.floating.age", "100 DÍAS"): _ES_CO,
        # The state word is cut before the age goes: beside the widest pill and es-CO's 100 DÍAS (or es-419's 100DD.)
        # English's widest word, which Spanish shows until #105, has 78.8 px. NO LOGIN, the state that words it, has no
        # age, so no frame draws this pair: the budgets pair every box's widest.
        ("nostromo.floating.state_word", "AUTH REQUIRED"): "beside es-CO's and es-419's widest ages, cut first (Q2)",
        ("night_city.floating.band_age", "100 DÍAS OLD"): (
            "es-CO's narrow day, spaced and in full (73.9 px against the band's 62.4 beside the widest cause): the band "
            "leaves the age out and keeps the cause whole (BAND_GIVE_WAY; before #95 it cut the cause and dropped the age "
            "too); the day is CLDR's, and only an age word (OLD) two characters shorter, a draft's (#105), could settle "
            "it"),
        ("night_city.tooltip.info_pace", "UNDER PACE -100"): _ES_PACE,
        ("night_city.tooltip.info_pace", "UNDER PACE +100"): _ES_PACE,
    },
}

# Each language's longest tray tooltip in UTF-16 units (#93: "the longest tooltip per language is recorded as the
# budget"), as `python tests/budgets.py --tooltips` prints them, each with its text. fr, es and tr show English's words
# while their catalogs are empty; a draft that lengthens one records it here again (#104 to #106).
TOOLTIPS: dict[str, int] = {
    "en": 113,       # ... - rate limited - guard holding, resumes Fri 10:00 AM (in 10h 10m) (dev)
    "fr": 120,       # fr-CA, 12-hour: ... - guard holding, resumes dim. 10 h 00 a.m. (in 10h 10m) (dev)
    "es": 121,       # es-CO, 12-hour: ... - guard holding, resumes dom, 10:00 a. m. (in 10 h 10 min) (dev)
    "tr": 114,       # 12-hour, CLDR's one hour: ... - guard holding, resumes Cmt ÖS 10:00 (in 1 sa 10d) (dev)
    "pseudo": 127,   # live (no status word), a same-day resume with its countdown, at the shell's limit:
                     # ... [ğüárd höldıñğ, réşüméş 10:00 AM (ıñ 1m) ıİğşéñıİğşéñ] (dev)
}

# Boxes that draw a status word that is the skin's own, not the view model's, so they carry the skin's key, not the
# status word's (Night City's, #95): the header's LIVE tag and the strip's status words (night_city.STATE_KEYS).
OWN_STATUS_WORDS: dict[str, str] = {
    "night_city.floating.live_tag": "Night City's own LIVE tag (widget.night_city.state.live), #95",
    "night_city.strip.status_live": "Night City's own LIVE on the strip (widget.night_city.state.live), #95",
    "night_city.strip.status_word": "Night City's own strip words (widget.night_city.state.*), #95",
}

# Nostromo's boxes that draw its own words (#94): every box of its floating widget, hover card and strip but the
# figures', whose keys are widget.nostromo.* (and the pill's, the view model's status word). The guard's statement
# composes its texts instead.
NOSTROMO_WORDED = frozenset({
    *(f"nostromo.floating.{part}" for part in ("status_pill", "state_word", "state_chip", "hold_chip", "hold_word",
                                               "armed_guard", "armed_guard_short", "row_name", "row_qualifier",
                                               "cap_tag", "message")),
    *(f"nostromo.tooltip.{part}" for part in ("row_name", "row_qualifier", "cap_tag", "resets", "pace", "pace_short",
                                              "guard_lead")),
    "nostromo.strip.window_tag",
})
# Nostromo's boxes of figures alone, measured in the pseudo-locale as it writes figures (Box.words False, #94).
NOSTROMO_FIGURES = frozenset({"nostromo.floating.age", "nostromo.floating.row_value", "nostromo.tooltip.row_value",
                              "nostromo.strip.value_5h", "nostromo.strip.value_5h_chip", "nostromo.strip.value_7d",
                              "nostromo.strip.value_7d_chip", "nostromo.tray.readout"})
# Night City's boxes that draw its own words (#95): every box of its floating widget, hover card and strip but the
# figures' and the brand's. The guard's statement composes its texts instead.
NIGHT_CITY_WORDED = frozenset({
    *(f"night_city.floating.{part}" for part in ("live_tag", "status_chip", "guard_chip", "hold_chip", "hold_word",
                                                 "band_text", "band_text_no_age", "band_age", "band_hold_text",
                                                 "band_hold_chip", "band_hold_word", "row_name", "row_chip", "row_tag",
                                                 "row_reset")),
    *(f"night_city.tooltip.{part}" for part in ("row_name", "row_chip", "row_tag", "info_reset", "info_pace",
                                                "guard_lead")),
    *(f"night_city.strip.{part}" for part in ("window_label", "status_live", "status_word", "hold_chip")),
})
# Night City's boxes of figures alone (the values, the strip's percent, the pace's points), and its brand mark, drawn
# as written in every language (#86): measured in the pseudo-locale as it draws them (Box.words False).
NIGHT_CITY_FIGURES = frozenset({"night_city.floating.row_value", "night_city.tooltip.row_value",
                                "night_city.strip.percent", "night_city.tooltip.info_pace_short"})
NIGHT_CITY_AS_WRITTEN = frozenset({"night_city.floating.brand"})
# Gates' boxes that draw its own words (#96): every box of its floating widget, hover card and strip but the figures',
# the titles' and the composing boxes' (the pill, the footers' rests and steps after a lead, the guard bar's lines).
GATES_WORDED = frozenset({
    *(f"gates.{surface}.{part}" for surface in ("floating", "tooltip")
      for part in ("status_word", "status_word_shrunk", "loading_word", "loading_word_shrunk", "row_label", "row_tag",
                   "cap_badge")),
    *(f"gates.floating.{part}" for part in ("stale_lead", "stale_step_alone", "refused_lead", "refused_step_alone",
                                            "no_login_lead", "no_login_step_alone", "rate_limited_lead", "pace_lead",
                                            "hold_lead", "footer_line")),
    *(f"gates.tooltip.{part}" for part in ("resets", "pace", "pace_short", "guard_lead")),
    "gates.strip.window_tag",
})
# Gates' boxes of figures alone, and its title, the brand mark drawn as written in every language (#86): measured in
# the pseudo-locale as it draws them (Box.words False).
GATES_FIGURES = frozenset({"gates.floating.row_value", "gates.tooltip.row_value", "gates.strip.value",
                           "gates.tray.number"})
GATES_AS_WRITTEN = frozenset({"gates.floating.title", "gates.tooltip.title"})
# Gates' boxes that compose their texts as the skin words them in each language: the pill (the status word and its
# age), each footer's rest or step after its lead, and the guard bar's lines.
GATES_COMPOSED = frozenset({"gates.floating.status_pill", "gates.tooltip.status_pill", "gates.floating.stale_step",
                            "gates.floating.refused_step", "gates.floating.no_login_step",
                            "gates.floating.rate_limited_rest", "gates.floating.pace_reset", "gates.floating.hold_rest",
                            "gates.tooltip.guard_text"})


# ── As the skins draw ─────────────────────────────────────────────────────────────────────────────────────────────

_DRAW = ("import json, budgets; "
         "runs = set(budgets.drawn((1.0,))) | set(budgets.drawn(budgets.SCALES[1:], every_variant=False)); "
         "print(json.dumps(sorted(runs)))")


@pytest.fixture(scope="module")
def drawn() -> list[tuple[str, str, str, float, float]]:
    """budgets.drawn() in a fresh Python with the time zone pinned, as the frame dump's other tests run it: every
    theme and variant at 1.0, and each theme's first variant at the other display scales (variants differ in colour)."""
    done = subprocess.run([sys.executable, "-c", _DRAW], cwd=REPO,
                          env={**os.environ, "TZ": PINNED_TZ,
                               "PYTHONPATH": os.pathsep.join((str(REPO), str(REPO / "tests")))},
                          capture_output=True, encoding="utf-8", timeout=900, check=False, creationflags=NO_WINDOW)
    assert done.returncode == 0, done.stderr[-3000:]
    return [tuple(row) for row in json.loads(done.stdout)]


def _one_scale(box: budgets.Box) -> bool:
    """Night City's and Gates' ink lays out true advances, so their widths are the same at every scale."""
    return box.measure.how != "nostromo"


def _drawn_widths(seen: dict[tuple[str, str, str], dict[float, set[float]]], box: budgets.Box, text: str,
                  scale: float) -> set[float]:
    """The widths the skin worked out for ``text`` where ``box`` stands: at ``scale``, or at any scale for a box whose
    width is the same at every scale. Night City draws its value's figure and its percent sign as two runs."""
    skin, surface = box.id.split(".")[:2]

    def at(run: str) -> set[float]:
        found: set[float] = set()
        for where in STANDS_ON[surface]:
            for drawn_at, widths in seen.get((skin, where, run), {}).items():
                if _one_scale(box) or round(drawn_at, 6) == round(scale, 6):
                    found |= widths
        return found

    if box.measure.how != "night_city_value" or "%" not in text:
        return at(text)
    return {figure + nc.UNIT_GAP + sign for figure in at(text.replace("%", "")) for sign in at("%")}


def test_each_box_measures_its_text_as_its_skin_draws_it(drawn: list[tuple[str, str, str, float, float]]) -> None:
    """The frame dump draws every surface at every display scale, and Nostromo's strip also at the smaller scales a
    short taskbar draws it at. Each English text of a box that it draws where the box stands is measured to a width the
    skin worked out as it drew it there, within 0.01 px, and a box measured at several scales is compared at every one
    of them; so the box has the skin's font, size, tracking, canvas and shrinking. (A Night City or Gates text has its
    1.0 width at every scale.) The run check below holds every width the dump draws to a box."""
    seen: dict[tuple[str, str, str], dict[float, set[float]]] = defaultdict(lambda: defaultdict(set))
    for theme, surface, text, scale, width in drawn:
        seen[theme, surface, text][scale].add(width)
    wrong, measured = [], defaultdict(set)
    boxes = [box for box in budgets.BOXES if box.unit != "chars"]
    for box in boxes:
        for text in box.english:
            for scale in box.scales:
                widths = _drawn_widths(seen, box, text, scale)
                if not widths:
                    continue
                measured[box.id].add(scale)
                want = box.measure.width(text, scale)
                if not any(abs(want - width) <= 0.01 for width in widths):
                    wrong.append((box.id, text, scale, round(want, 3), sorted(widths)))
    assert wrong == []
    assert {box.id for box in boxes if not measured[box.id]} == NOT_DRAWN_WHOLE
    assert [(box.id, sorted(set(box.scales) - measured[box.id])) for box in boxes
            if box.id not in NOT_DRAWN_WHOLE and set(box.scales) - measured[box.id]] == []


def _shape(text: str) -> str:
    """A run with its digits made zeros: a box lists one value of each shape, not every value."""
    return re.sub(r"\d", "0", text)


def _run_width(box: budgets.Box, run: str, scale: float) -> float:
    """``run`` measured as ``box`` measures it. Night City draws its value's percent sign as a run of its own, UNIT_GAP
    after the figure, so the sign alone is measured without the gap."""
    if box.measure.how == "night_city_value" and run == "%":
        return box.measure.width(run, scale) - nc.UNIT_GAP
    return box.measure.width(run, scale)


def test_every_run_the_skins_draw_stands_in_a_box(drawn: list[tuple[str, str, str, float, float]]) -> None:
    """Each run the frame dump draws, at each scale it draws it, is one that a box standing on that surface lists, its
    digits aside, or a part of Night City's value (the figure and the sign are two runs); and one of those boxes
    measures the run, digits and all, to the width the skin drew it at, within that box's budget. So a run drawn in
    another face or size than its box's, a value wider than its box's budget, and a wrapped line wider than its room all
    fail. Gates' icons are glyphs, not text."""
    claims: dict[tuple[str, str, str], list[budgets.Box]] = defaultdict(list)
    for box in budgets.BOXES:
        if box.unit == "chars":
            continue
        skin, surface = box.id.split(".")[:2]
        shapes = {_shape(text) for text in box.english}
        if box.measure.how == "night_city_value":
            shapes |= {_shape(text.replace("%", "")) for text in box.english} | {"%"}
        for where in STANDS_ON[surface]:
            for shape in shapes:
                claims[skin, where, shape].append(box)
    stray, unmeasured = set(), []
    for theme, surface, text, scale, width in drawn:
        if all(0xE000 <= ord(ch) <= 0xF8FF for ch in text):
            continue
        boxes = claims.get((theme, surface, _shape(text)), [])
        if not boxes:
            stray.add((theme, surface, text))
        elif not any(abs(_run_width(box, text, scale) - width) <= 0.01 and width <= budgets.budget(box, scale) + 0.01
                     for box in boxes):
            unmeasured.append((theme, surface, text, scale, round(width, 3), sorted(box.id for box in boxes)))
    assert sorted(stray) == []
    assert unmeasured == []


def test_the_strip_is_measured_at_every_scale_a_taskbar_draws_it_at(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nostromo draws its strip smaller on a taskbar shorter than it (render_taskbar). Called as the frame dump calls
    it, at each display scale Windows offers up to 200 % (SCALES: the frame dump's four and 1.75) and each taskbar
    height the frame dump draws, the skin picks a scale in STRIP_SCALES, every one of them is picked, and the strip's
    boxes are measured at them. (The drawn fixture runs the frame dump itself at those scales.)"""
    used: set[float] = set()
    real = nos._render_strip

    def spy(model: Any, pal: Any, scale: float, *args: Any, **kwargs: Any) -> Any:
        used.add(round(scale, 6))
        return real(model, pal, scale, *args, **kwargs)

    monkeypatch.setattr(nos, "_render_strip", spy)
    theme = dump.load_theme_objects()["nostromo"]
    skin, model = dump.get_skin("nostromo"), dump.sample_model(dump.DUMP_STATE_NAMES[0])
    for scale in budgets.SCALES:
        for height in dump.TASKBAR_HEIGHTS_AT_1X:
            skin.render_taskbar(model, theme, theme.variants[0], round(height * scale), scale, None)
    assert used == {round(scale, 6) for scale in budgets.STRIP_SCALES}
    assert {box.scales for box in budgets.BOXES if box.id.startswith("nostromo.strip.")} == {budgets.STRIP_SCALES}


def test_every_room_is_pinned() -> None:
    """Each box's and line's limit is the one LIMITS holds: a change to a skin that moves a room fails here until the
    table is printed again (python tests/budgets.py --limits), so the change shows in review."""
    assert {item.id: item.limit for item in (*budgets.BOXES, *budgets.LINES)} == pytest.approx(LIMITS, abs=0.001)


def test_the_limits_print_as_this_file_pins_them(capsys: pytest.CaptureFixture[str]) -> None:
    assert budgets.main(["--limits"]) == 0
    assert ast.literal_eval("{" + capsys.readouterr().out + "}") == LIMITS


# ── The budgets ───────────────────────────────────────────────────────────────────────────────────────────────────

def test_english_runs_past_its_room_only_where_it_already_did() -> None:
    """A new English overrun (a longer word, a wider font, a narrower room) fails here; one that goes away is struck
    from KNOWN_ENGLISH_OVERRUNS, so the list only shrinks."""
    assert budgets.english_overruns() == budgets.KNOWN_ENGLISH_OVERRUNS


def test_no_offered_language_is_past_a_budget() -> None:
    """English, with its own longest figures set in, fits everywhere it did; no offered language's text runs past a
    box or line that has no fallback or only shrinks (#107 offers a language once this holds for it)."""
    assert budgets.over(i18n.DEFAULT) == []
    for language in i18n.OFFERED:
        assert [(r.box.id, r.text) for r in budgets.over(language) if r.box.enforced] == [], language


def test_what_91_has_to_settle_is_all_that_runs_past() -> None:
    """French's and Spanish's longest figures run past only where FOR_91 says, or where ACCEPTED_CUTS accepts a
    format's cut; #91 and the skins' tickets strike each FOR_91 row as they settle it. An accepted cut is a box that
    gives way (never a fault), and no FOR_91 row is a skin's: #94, #95 and #96 settled Nostromo's, Night City's and
    Gates'."""
    for language in sorted(set(FOR_91) | set(ACCEPTED_CUTS)):
        expected = FOR_91.get(language, set()) | set(ACCEPTED_CUTS.get(language, {}))
        assert {(r.box.id, r.text) for r in budgets.over(language)} == expected, language
    assert not any(budgets.BOX[box].enforced for cuts in ACCEPTED_CUTS.values() for box, _text in cuts)
    assert not any(box.startswith(("nostromo.", "night_city.", "gates.")) for rows in FOR_91.values()
                   for box, _text in rows)


def test_the_boxes_sized_for_englishs_figures_take_their_percents_and_dollars_tight() -> None:
    """Chris's decision on #91: French and Spanish drop the space before "%" and "$" only in the boxes sized for
    English's figures, and the sign keeps its side. budgets.tight writes each language's percent and dollar figure as
    formats.percent and formats.money do with tight=True for the language's base format, so the budgets measure what the
    skins draw (test_locale_surfaces.py holds the skins to it) and what the page draws in its money boxes
    (test_webui.py holds the page to it); a currency code keeps its space; every other box takes the figure with its
    space."""
    assert {box.id for box in budgets.BOXES if box.tight} == TIGHT_BOXES
    for language, figures in budgets.FIGURES["percent"].items():
        tag = i18n.BASE_FORMATS[language]
        locale = i18n.Locale(setting="system", language=language, system="en", format=tag, clock=24, offered=("en",))
        assert [budgets.tight(figure) for figure in figures] == [formats.percent("100", locale, tight=True)], language
        if language != i18n.DEFAULT:
            assert budgets.tight(formats.money(5.96, "USD", locale)) == formats.money(5.96, "USD", locale, tight=True)
            assert budgets.tight(formats.money(5.96, "EUR", locale)) == formats.money(5.96, "EUR", locale, tight=True)
    assert budgets.tight("5,96\u00a0$") == "5,96$" and budgets.tight("99,9\u00a0k\u00a0$") == "99,9\u00a0k$"
    assert budgets.tight("5,96\u00a0EUR") == "5,96\u00a0EUR" and budgets.tight("$ 5") == "$5"
    assert budgets.texts(budgets.BOX["gates.tooltip.cap_badge"], "fr") == ["Cap 100%"]
    assert budgets.texts(budgets.BOX["nostromo.strip.value_5h"], "es") == ["100%"]
    assert budgets.texts(budgets.BOX["night_city.strip.percent"], "tr") == ["%100"]
    assert budgets.texts(budgets.BOX["gates.floating.row_value"], "fr") == ["100\u00a0%"]
    assert budgets.texts(budgets.BOX["gates.floating.cap_badge"], "fr") == ["Cap 100\u00a0%"]


def test_a_box_that_shrinks_its_text_is_measured_at_the_size_it_shrinks_to(monkeypatch: pytest.MonkeyPatch) -> None:
    """Night City's strip words shrink a long word to fit, down to 9 px (_fit_type), and only then cut it (#95): the
    LIVE tag's, the status word's and the HOLD chip's are measured at the size the skin's _fit_type draws them at, so a
    word too wide at its own size fits shrunk; a word too wide even at 9 px is reported as a cut, its width the 9 px
    one."""
    ink = budgets._nc_ink()
    for box_id, spec, word in (("night_city.strip.status_live", "STATUS", "DIRECT"),
                               ("night_city.strip.status_word", "STATUS", "CONNEXION"),
                               ("night_city.strip.hold_chip", "CHIP", "ESPERA")):
        box = budgets.BOX[box_id]
        assert box.fit == "ellipsis" and not box.enforced, box_id
        drawn = nc._fit_type(ink, word, getattr(nc, spec), box.limit, nc.STRIP_FLOOR)
        assert budgets.W_nc(word, spec) > box.limit >= box.width(word) == pytest.approx(ink.width(word, drawn)), box_id
    box = budgets.BOX["night_city.strip.status_word"]
    monkeypatch.setattr(budgets, "texts", lambda _box, _language: ["CONNEXION", "DÉCONNEXION RÉSEAU"])
    [hit] = budgets.over("xx", [box])
    floor = nc.STATUS._replace(size=nc.STRIP_FLOOR, tracking=0.3)
    assert (hit.text, hit.width) == ("DÉCONNEXION RÉSEAU", pytest.approx(budgets._nc_ink().width(hit.text, floor)))


def test_a_character_limit_stays_the_budget_where_english_runs_past() -> None:
    """A pixel box where English already runs past its room budgets what English takes there (the strip's 100%). A
    character box's limit is a contract or a hard cut (the money figures' six characters, the tray's 127 units), so it
    stays the budget: Spanish's compact "99,9 mil$" fails a tile, and only the English in KNOWN_ENGLISH_OVERRUNS passes
    one."""
    strip = budgets.BOX["nostromo.strip.value_5h"]
    assert strip.width("100%") > strip.limit
    for scale in strip.scales:
        assert budgets.budget(strip, scale) == pytest.approx(max(strip.limit, strip.width("100%", scale)))
    tile = budgets.BOX["dashboard.nostromo.cost_tile"]
    assert (tile.unit, tile.limit, budgets.budget(tile)) == ("chars", 6, 6)
    assert (tile.id, "$100.00") in budgets.KNOWN_ENGLISH_OVERRUNS
    assert (tile.id, "99,9\u00a0mil$") in {(r.box.id, r.text) for r in budgets.over("es")}
    tip = budgets.BOX["app.tray.tooltip"]
    assert (tip.unit, tip.limit, budgets.budget(tip)) == ("chars", tray.TIP_LIMIT, tray.TIP_LIMIT)


def test_characters_are_counted_in_utf16_units_as_the_tray_clips_them() -> None:
    chars = budgets.Measure("chars")
    assert [chars.width(text) for text in ("Kullanım", "e\u0301", "😀")] == [8, 2, 2]
    fits, past = "😀" * 63 + "x", "😀" * 64                     # 127 and 128 units
    assert (chars.width(fits), tray.clip(fits, tray.TIP_LIMIT)) == (127, fits)
    assert chars.width(past) == 128 and tray.clip(past, tray.TIP_LIMIT) != past


def _grown(box: budgets.Box, text: str) -> str:
    """``text`` lengthened as far as ``box`` holds it at every scale."""
    def fits(candidate: str) -> bool:
        return all(box.width(candidate, scale) <= budgets.budget(box, scale) + 0.01 for scale in box.scales)

    assert fits(text)
    while fits(text + "X"):
        text += "X"
    return text


def test_a_line_is_measured_with_each_slots_widest_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """A row's name and qualifier that each fit their own box can still not fit Nostromo's row line together, so a line
    takes each slot's widest text in the language, as the language would draw them side by side. (A line that gives
    way does so first: test_a_line_gives_way_before_it_is_reported. Gates' and Night City's boxes have their rooms in a
    language from their line, beside its other slots' widest: budgets.g_room, nc_room.)"""
    line = next(item for item in budgets.LINES if item.id == "nostromo.floating.row_name_line")
    name, qualifier = budgets.BOX["nostromo.floating.row_name"], budgets.BOX["nostromo.floating.row_qualifier"]
    assert not line.gives_way
    name_text, qualifier_text = _grown(name, "WEEKLY SCOPED"), _grown(qualifier, "OF 100% SHARE")
    made = {name.id: [name_text, "SESSION"], qualifier.id: [qualifier_text, "5H"]}
    monkeypatch.setattr(budgets, "texts", lambda box, _language: made.get(box.id, []))
    assert budgets.over("xx", [name, qualifier]) == []
    [hit] = budgets.over("xx", [line])
    assert hit.text == f"{name_text} | {qualifier_text}" and hit.budget == line.limit
    assert hit.width == pytest.approx(name.width(name_text, hit.scale) + 6 + qualifier.width(qualifier_text, hit.scale))


def test_a_wrapping_box_is_measured_by_its_longest_run_without_a_space() -> None:
    """A box that wraps breaks at spaces, so what must fit is each run without one; U+00A0 holds a run together."""
    box = budgets.BOX["gates.tooltip.guard_text"]
    text = "Resumes 10:26 PM (in 2h 14m)."
    assert box.fit == "wrap" and not box.enforced
    assert box.width(text) == pytest.approx(max(box.measure.width(word) for word in text.split(" ")))
    glued = "16\u00a0h\u00a012"
    assert box.width(glued) == pytest.approx(box.measure.width(glued))


# ── What goes in a box ────────────────────────────────────────────────────────────────────────────────────────────

def test_each_languages_longest_figures_are_set_in_the_boxs_text() -> None:
    """Each figure in each of the box's templates, as the skin draws it: the language's whole day clock where English
    writes WED before the time, capitals in Nostromo and Night City, glued in the guard's statement; English as #89 set
    it. (A toast's box composes its texts since #97: the templates are tried here on its shape without that.)"""
    body = replace(budgets.BOX["app.toast.alert_resets.body"], compose=None, keys=())
    assert budgets.texts(body, "en") == ["Resets 12:59 PM", "Resets Wed 12:59 PM", "Resets 4:12 PM",
                                         "Resets Wed 4:12 PM"]
    spanish = budgets.texts(body, "es")
    assert spanish[:3] == ["Resets 16:12", "Resets 12:59\u202fp.\u202fm.", "Resets 12:59\u202fp.m."]
    assert {"Resets sáb, 16:12", "Resets dom, 12:59\u202fp.\u202fm.", "Resets sáb 12:59\u202fp.m."} <= set(spanish)
    assert len(spanish) == 3 + 7 * 5 and not any("Wed" in text for text in spanish)
    french = budgets.texts(body, "fr")
    assert french[:6] == ["Resets 16:12", "Resets 16 h 12", "Resets 12:59\u202fPM", "Resets 12 h 59\u202fp.m.",
                          "Resets 12:59", "Resets 12:59\u202fp.m."]                       # fr-MA's 12-hour clock (#111)
    assert "Resets sam. 12 h 59\u202fp.m." in french and len(french) == 6 + 7 * 6
    assert "RESETS MER. 12 H 59\u202fP.M." in budgets.texts(budgets.BOX["nostromo.tooltip.resets"], "fr")
    assert "RST CMT ÖS\u202f12:59" in budgets.texts(budgets.BOX["night_city.floating.row_reset"], "tr")
    assert budgets.texts(budgets.BOX["nostromo.floating.hold_chip"], "fr") == ["HOLD 23H 59MIN", "HOLD 23H 59M",
                                                                               "HOLD 6J 23H"]
    assert budgets.texts(budgets.BOX["night_city.floating.band_age"], "tr") == ["59SN OLD", "59D OLD", "47S OLD",
                                                                                "100G OLD"]
    assert "RATE LIMITED · 100 días" in budgets.texts(budgets.BOX["gates.floating.status_pill"], "es")
    guard = budgets.texts(budgets.BOX["gates.tooltip.guard_text"], "fr")       # whole statements, the figures glued
    assert all(any(piece in text for text in guard) for piece in ("dim.\u00a010\u00a0h\u00a000\u202fa.m.",
                                                               "(in\u00a023h\u00a059min).")), guard[:3]
    assert budgets.texts(budgets.BOX["nostromo.floating.cap_tag"], "tr") == ["CAP %100"]
    assert budgets.texts(budgets.BOX["nostromo.floating.message"], "fr") == list(                 # no figure; its keys
        budgets.BOX["nostromo.floating.message"].english)                         # are English's until #104 drafts them
    assert budgets.texts(body, i18n.PSEUDO) == [i18n.pseudo_text(text) for text in body.english]
    tile = budgets.BOX["dashboard.nostromo.cost_tile"]                      # the page's own figures, tight (#91)
    assert budgets.texts(tile, "fr") == ["99,9\u00a0k$"] and budgets.texts(tile, "tr") == ["99,9\u00a0Mn$"]
    assert budgets.texts(tile, "es") == ["99,9\u00a0mil$"] and budgets.texts(tile, "en") == list(tile.english)
    toast = replace(budgets.BOX["app.toast.alert_credits.body"], compose=None, keys=())  # the formats' money
    assert budgets.texts(toast, "es") == ["Used $5.96 of 10,000.00 USD", "Used 5,96\u00a0$ of 10,000.00 USD",
                                          "Used 1,234.5 of 10,000.00 USD"]


# What FIGURES samples (budgets.FIGURES says why): a time on each clock, the same after each weekday, the widest
# countdowns and the widest age of each unit.
AT_24, AT_12 = datetime(2026, 7, 13, 16, 12), datetime(2026, 7, 13, 12, 59)         # a Monday
COUNTDOWNS = (((23, "h"), (59, "m")), ((6, "d"), (23, "h")))
AGES = (((59, "s"),), ((59, "m"),), ((47, "h"),), ((100, "d"),))


def _locales(language: str) -> list[i18n.Locale]:
    """A dev instance's locale for each format tag of ``language`` (its CLDR regions and its base), on each clock."""
    tags = sorted({tag for tag in cldr.REGIONS if i18n.primary(tag) == language} | {i18n.BASE_FORMATS[language]})
    return [i18n.Locale(setting="system", language=language, system="en", format=tag, clock=clock, offered=("en",))
            for tag in tags for clock in (24, 12)]


def _written(kind: str, locale: i18n.Locale) -> set[str]:
    """What formats writes for ``kind`` in ``locale`` at FIGURES' samples."""
    at = AT_24 if locale.clock == 24 else AT_12
    if kind == "percent":
        return {formats.percent("100", locale)}
    if kind == "money":
        return {formats.money(5.96, "USD", locale), formats.number("1234.5", locale)}
    if kind == "clock":
        return {formats.clock(at, locale)}
    if kind == "day_clock":
        return {formats.day_clock(at + timedelta(days=day), locale) for day in range(7)}
    return {formats.duration(parts, locale) for parts in {"duration": COUNTDOWNS, "age": AGES}[kind]}


def test_every_figure_is_what_the_formats_write_for_one_of_the_languages_tags() -> None:
    """Each figure of French, Spanish and Turkish is what usage_monitor/formats.py writes for one of the language's
    format tags, on one of the clocks, so a figure and the formats cannot drift apart; and every clock, day clock,
    countdown and age the tags write at the samples is a figure, so a region with a longer one is measured. English
    keeps #89's figures, as the formats write them ("4:12 PM" at 16:12); its "$5.96" is the dashboard's own (app.js),
    which tests/fixtures/webui_formats_english.json holds, and the dashboard's money boxes take budgets.PAGE_MONEY,
    which test_webui.py holds to the page."""
    assert set(budgets.FIGURES) == {"percent", "clock", "money", "day_clock", "duration", "age"}
    english = {"percent": {formats.percent("100", i18n.ENGLISH)}, "clock": {formats.clock(AT_24, i18n.ENGLISH)}}
    for kind, by_language in budgets.FIGURES.items():
        for language, figures in by_language.items():
            if language == i18n.DEFAULT:
                assert kind == "money" or set(figures) == english[kind], kind
                continue
            written = set().union(*(_written(kind, locale) for locale in _locales(language)))
            assert set(figures) <= written, (kind, language, sorted(set(figures) - written))
            if kind in ("clock", "day_clock", "duration", "age"):
                assert written <= set(figures), (kind, language, sorted(written - set(figures)))
            assert len(set(figures)) == len(figures), (kind, language)
    languages = {language for by_language in budgets.FIGURES.values() for language in by_language}
    assert languages == {"en", "fr", "es", "tr"}


@pytest.mark.parametrize(("kind", "figure"), [("percent", r"%"), ("clock", r"\d:\d\d"), ("money", r"\$|USD")])
def test_a_box_names_the_figures_its_english_holds(kind: str, figure: str) -> None:
    """A box whose English holds a percent, a clock or a dollar names that kind, so each language's longest one is
    measured there, and a box that names one holds it."""
    holds = {box.id for box in budgets.BOXES if any(re.search(figure, text) for text in box.english)}
    assert holds == {box.id for box in budgets.BOXES if kind in box.kinds}


def test_every_template_is_a_shape_of_its_boxs_english() -> None:
    for box in budgets.BOXES:
        for template in box.templates:
            before, *after = template.split("{}")
            assert len(after) == 1, (box.id, template)
            shape = re.compile(re.escape(before) + "(.+)" + re.escape(after[0]))
            assert any(shape.fullmatch(text) for text in box.english), (box.id, template)


def test_no_value_of_a_listed_shape_is_wider_than_its_budget() -> None:
    """A box lists one value of each shape, and the run check above reads digits as zeros. Where a face's digits differ
    in width (Night City's chips, tags and status, Gates' body face), the shape with the face's widest digit in every
    place still fits the box's budget, so no value the skin can draw there is wider than the English it is worked out
    from. A text English already runs past in a box that cuts it is cut there whatever its digits."""
    wider = []
    for box in budgets.BOXES:
        if box.unit == "chars":
            continue
        digit = max("0123456789", key=box.measure.width)
        for text in box.english:
            if (box.id, text) in budgets.KNOWN_ENGLISH_OVERRUNS and not box.enforced:
                continue                    # already cut there (Gates' guard footer's rest): a wider value is cut too
            variant = re.sub(r"\d", digit, text)
            if any(box.width(variant, scale) > budgets.budget(box, scale) + 0.01 for scale in box.scales):
                wider.append((box.id, text, variant))
    assert wider == []


def test_every_box_and_line_is_well_formed() -> None:
    ids = [item.id for item in (*budgets.BOXES, *budgets.LINES)]
    assert len(ids) == len(set(ids)) and list(budgets.BOX) == [box.id for box in budgets.BOXES]
    for box in budgets.BOXES:
        assert re.fullmatch(r"(nostromo|night_city|gates|dashboard|app)(\.[a-z0-9_]+){2,3}", box.id), box.id
        assert box.fit in budgets._FITS and set(box.kinds) <= set(budgets._KINDS), box.id
        assert box.english and box.why and box.limit > 0, box.id
        assert box.unit == "chars" or box.width("Ag") > 0, box.id
    for line in budgets.LINES:
        assert line.fit in budgets._FITS and line.english and line.why, line.id
        slotted = [box_id for slot in line.slots for box_id in slot]
        assert all(budgets.BOX[box_id].unit == "px" for box_id in slotted), line.id
        assert {box_id for box_id, _ in line.extra} <= set(slotted), line.id
        for runs in line.english:
            assert len(runs) == len(line.slots), (line.id, runs)
            for slot, text in zip(line.slots, runs):
                assert not text or any(text in budgets.BOX[box_id].english for box_id in slot), (line.id, text)


def test_budget_notes_sit_beside_their_keys() -> None:
    """Each box key carries its budget just after it in en.json, so translators see the limit; a translator's note
    (``_note.<key>``) sits just before it (#93)."""
    raw = json.loads((i18n.CATALOG_DIR / "en.json").read_text(encoding="utf-8"))
    notes = {key: value for key, value in raw.items() if key.startswith("_budget.")}
    assert notes and notes == budgets.notes()
    order = list(raw)
    for note in notes:
        key = note[len("_budget."):]
        assert key in raw and order.index(note) == order.index(key) + 1, note


# ── The catalog keys in the boxes (#93) ───────────────────────────────────────────────────────────────────────────

def _english(key: str) -> Any:
    return json.loads((i18n.CATALOG_DIR / "en.json").read_text(encoding="utf-8"))[key]


def test_a_boxs_keys_are_the_words_it_draws() -> None:
    """A box's key, filled, is one of the texts it draws; test_a_worded_boxs_english_is_its_keys_filled holds the
    other way round (a composing box, Gates' pill among them, words its texts as the skin does). And a box
    whose text is the view model's status word carries that word's key, but for OWN_STATUS_WORDS, the skins' own words.
    The tooltip, a composing box, carries every tray.tip key and reads no other (the next test)."""
    from usage_monitor.render.model import STATUS_KEYS

    status = {_english(key): key for key in STATUS_KEYS.values()}
    wrong, missing = [], []
    for box in budgets.BOXES:
        if box.compose is not None:
            continue
        leads = set(box.english)
        for key in box.keys:
            if not any(budgets.key_pattern(_english(key)).fullmatch(lead) for lead in leads):
                wrong.append((box.id, key))
        for lead in sorted(leads & set(status)):
            if status[lead] not in box.keys and box.id not in OWN_STATUS_WORDS:
                missing.append((box.id, lead))
    assert wrong == [] and missing == []
    keyed = {box.id for box in budgets.BOXES if box.keys and box.compose is None}
    assert keyed == {*GATES_WORDED, *NOSTROMO_WORDED, *NIGHT_CITY_WORDED}
    for box_id in OWN_STATUS_WORDS:
        box = budgets.BOX[box_id]
        assert box.keys and all(key.startswith("widget.night_city.state.") for key in box.keys), box_id
        assert set(box.english) & set(status), box_id
    raw = json.loads((i18n.CATALOG_DIR / "en.json").read_text(encoding="utf-8"))
    assert set(budgets.BOX["app.tray.tooltip"].keys) == {key for key in raw if key.startswith("tray.tip.")}


def test_the_tooltip_box_composes_the_tooltips_the_code_words(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tooltip box's texts (English's listed ones aside) are app.tray_tooltip's, as a dev instance shows them, in
    each language, worded through keys the box lists: a key the tooltip reads that the box does not list fails here."""
    from usage_monitor import app

    read: set[str] = set()
    inside = [0]
    real_text, real_tip = i18n.Catalog.text, app.tray_tooltip

    def text(self: Any, key: str, *args: Any, **kwargs: Any) -> Any:
        if inside[0]:
            read.add(key)
        return real_text(self, key, *args, **kwargs)

    def tray_tooltip(*args: Any, **kwargs: Any) -> str:
        inside[0] += 1
        try:
            return real_tip(*args, **kwargs)
        finally:
            inside[0] -= 1

    monkeypatch.setattr(i18n.Catalog, "text", text)
    monkeypatch.setattr(app, "tray_tooltip", tray_tooltip)
    box = budgets.BOX["app.tray.tooltip"]
    for language in i18n.DEV_LANGUAGES:
        composed = [text for text in budgets.texts(box, language) if text not in box.english]
        assert composed and all(text.endswith(" (dev)") for text in composed), language
    assert read and read <= set(box.keys), sorted(read - set(box.keys))


def test_every_key_with_a_placeholder_is_filled_or_composed() -> None:
    """A key with a placeholder ("RESETS {clock}") is measured filled as the skin fills it (Box.fills, each placeholder
    with a FILLS kind), or its box composes its whole texts as the code words them: no box's text, in any language,
    keeps a placeholder as written."""
    found = []
    for box in budgets.BOXES:
        names = set().union(*(i18n._placeholders(_english(key)) for key in box.keys if isinstance(_english(key), str)))
        assert set(dict(box.fills)) <= names and set(dict(box.fills).values()) <= set(budgets.FILLS), box.id
        if box.compose is None:
            assert names <= set(dict(box.fills)), (box.id, sorted(names - set(dict(box.fills))))
        found += [(box.id, name) for name in names]
        for language in i18n.DEV_LANGUAGES:
            assert not any(re.search(r"\{[a-z_]+\}", text) for text in budgets.texts(box, language)), (box.id, language)
    assert {name for _box, name in found} >= {"percent", "clock", "countdown", "points", "name", "product", "command"}


def test_a_key_in_several_boxes_has_one_note_naming_them_all() -> None:
    """A status word sits in up to nine boxes; its one budget note names every one, by id, with its room and fit (or
    the box's own note)."""
    note = budgets.notes()["_budget.widget.status.rate_limited"]
    boxes = sorted(box.id for box in budgets.BOXES if "widget.status.rate_limited" in box.keys)
    assert len(boxes) == 9 and [piece.split("(")[1].split(",")[0] for piece in note.split("; ")] == boxes
    pill = budgets.BOX["nostromo.floating.status_pill"]
    assert f"{pill.limit:g} px ({pill.id}, ellipsis)" in note.split("; ")
    tip = budgets.notes()["_budget.tray.tip.lead"]
    assert tip == budgets.BOX["app.tray.tooltip"].note and tip.startswith("the whole tooltip: 127 UTF-16 units")


def test_the_longest_tooltips_are_the_recorded_ones(capsys: pytest.CaptureFixture[str]) -> None:
    """The longest tooltip per language, from the whole sweep, is TOOLTIPS: a catalog change that lengthens one fails
    here until it is recorded again (python tests/budgets.py --tooltips)."""
    assert budgets.main(["--tooltips"]) == 0
    assert ast.literal_eval("{" + capsys.readouterr().out + "}") == TOOLTIPS


# ── The toasts (#97) ──────────────────────────────────────────────────────────────────────────────────────────────

TOAST_BOXES = tuple(box_id for box_id in budgets.BOX if box_id.startswith("app.toast."))
# Each language's longest note after a silent update, whole, in UTF-16 units: one tool and 9999 waits (English "Setup
# closed the monitor's tool in a Claude Code session. ... 9999 sessions waiting on the usage guard were let go early.").
# The pseudo-locale sits exactly at app.py's cut (UPDATE_NOTE_MAX, 250): a longer English note, or a draft of fr, es or
# tr that runs past it, fails here (#104 to #106 record their own figure).
LONGEST_NOTES: dict[str, int] = {"en": 180, "fr": 180, "es": 180, "tr": 180, "pseudo": 250}


def test_the_toast_boxes_compose_the_toasts_the_code_words() -> None:
    """Each toast's box measures every toast the code words in a language (toast_words.toast_sweep), English's listed
    texts too in English, and carries the keys those texts are worded from."""
    import toast_words

    assert set(TOAST_BOXES) == set(toast_words.TOAST_BOXES) == set(budgets.TOAST_KEYS)
    raw = json.loads((i18n.CATALOG_DIR / "en.json").read_text(encoding="utf-8"))
    assert {key for box_id in TOAST_BOXES for key in budgets.BOX[box_id].keys} == {
        key for key in raw if key.startswith("toast.")}
    for language in i18n.DEV_LANGUAGES:
        sweep = toast_words.toast_sweep(language)
        for box_id in TOAST_BOXES:
            box = budgets.BOX[box_id]
            english = list(box.english) if language == i18n.DEFAULT else []
            assert sweep[box_id] and budgets.texts(box, language) == list(dict.fromkeys([*english, *sweep[box_id]]))


def test_english_toasts_keep_within_48_and_200() -> None:
    """Windows asks a toast's English for 48 characters of title and 200 of text at most, to leave room for languages
    (#97): every English toast keeps to it, with the longest version (3.10.12-rc.1), a 24-character name and 9999 sessions."""
    for box_id in TOAST_BOXES:
        box = budgets.BOX[box_id]
        room = budgets.TOAST_ENGLISH["title" if box_id.endswith(".title") else "text"]
        longest = max(budgets.texts(box, i18n.DEFAULT), key=box.width)
        assert box.width(longest) <= room, (box_id, longest)


def test_no_language_passes_the_trays_cuts() -> None:
    """No toast of any language, the pseudo-locale among them, runs past what the tray keeps (63 UTF-16 units of title,
    255 of text, ui/tray.py) or the note past app.py's own cut (250): every count pair of the note, every format tag."""
    assert {budgets.BOX[box_id].limit for box_id in TOAST_BOXES} == {tray.TITLE_LIMIT, tray.BODY_LIMIT, 250}
    longest_notes = {}
    for language in i18n.DEV_LANGUAGES:
        for box_id in TOAST_BOXES:
            box = budgets.BOX[box_id]
            over = [text for text in budgets.texts(box, language) if box.width(text) > box.limit]
            assert over == [], (language, box_id, over[:3])
        note = budgets.BOX["app.toast.update_note.body"]
        longest_notes[language] = int(max(note.width(text) for text in budgets.texts(note, language)))
    assert longest_notes == LONGEST_NOTES


def test_the_pseudo_tooltip_fits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Composed in the pseudo-locale, every tooltip fits through the drop rule alone: app.tray_tooltip never has to cut
    one (tray.clip). A cut text fits by construction, so that is what is checked. Made pseudo whole, its English would
    not fit (153 and more units)."""
    from usage_monitor import app

    def clip(text: str, limit: int) -> str:
        assert tray.clip(text, limit) == text, f"cut: {text}"
        return text

    monkeypatch.setattr(app, "clip", clip)
    box = budgets.BOX["app.tray.tooltip"]
    assert box.fit == "drop" and box.compose is not None
    assert budgets.texts(box, i18n.PSEUDO)
    assert max(box.width(i18n.pseudo_text(text)) for text in box.english) > tray.TIP_LIMIT


# ── Nostromo's words and fits (#94) ───────────────────────────────────────────────────────────────────────────────

def test_nostromo_passes_its_budgets_in_the_pseudo_locale() -> None:
    """In the pseudo-locale (about 40 % longer, bracketed, accented) no Nostromo box or line that has no fallback or
    only shrinks runs past its budget: its words come from its keys and fit, shrink or give way as the skin makes them.
    What gives way is reported as a cut. Every Nostromo box is worded by keys, holds figures alone, or composes."""
    rows = budgets.over(i18n.PSEUDO)
    assert [(r.box.id, r.text) for r in rows if r.box.id.startswith("nostromo.") and r.box.enforced] == []
    cut = {r.box.id for r in rows if r.box.id.startswith("nostromo.")}
    assert {"nostromo.floating.status_pill", "nostromo.floating.state_word", "nostromo.tooltip.pace",
            "nostromo.strip.window_tag"} <= cut
    nostromo = {box.id for box in budgets.BOXES if box.id.startswith("nostromo.")}
    assert {box.id for box in budgets.BOXES if box.id in nostromo and box.keys and box.compose is None} == \
        NOSTROMO_WORDED
    assert {box.id for box in budgets.BOXES if not box.words} == (NOSTROMO_FIGURES | NIGHT_CITY_FIGURES
                                                                   | NIGHT_CITY_AS_WRITTEN | GATES_FIGURES
                                                                   | GATES_AS_WRITTEN)
    assert nostromo == NOSTROMO_WORDED | NOSTROMO_FIGURES | {"nostromo.tooltip.guard_lines"}


def test_a_line_gives_way_before_it_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """While a line is over, it gives up its boxes in its order (Nostromo's header: the state word, the age, the pill,
    the hold chip's countdown) and is reported only when even its last step does not fit, with the runs it is left
    with."""
    line = next(item for item in budgets.LINES if item.id == "nostromo.floating.header_line")
    pill, age, word, hold, alone = (budgets.BOX[f"nostromo.floating.{part}"]
                                    for part in ("status_pill", "age", "state_word", "hold_chip", "hold_word"))
    steps, width, _after = line.give_way([(pill.id, "P" * 20), (age.id, "59 M"), (word.id, "W" * 30)], 1.0, {})
    assert steps == (word.id,) and width <= line.limit
    steps, width, after = line.give_way([(pill.id, "P" * 20), (age.id, "59 M"), (hold.id, "HOLD " + "9" * 30)], 1.0,
                                        {alone.id: "HOLD"})
    assert steps == (age.id, pill.id, hold.id) and width <= line.limit
    assert after == [(pill.id, "P" + nos.ELLIPSIS), (age.id, ""), (alone.id, "HOLD")]
    made = {pill.id: ["P" * 20], age.id: ["59 M"], hold.id: ["HOLD " + "9" * 30], alone.id: ["HOLD"]}
    monkeypatch.setattr(budgets, "texts", lambda box, _language: made.get(box.id, []))
    assert budgets.over("xx", [line]) == []
    made[alone.id] = ["H" * 40]
    [hit] = budgets.over("xx", [line])
    assert hit.text == f"P{nos.ELLIPSIS} | {'H' * 40}" and hit.width > hit.budget == line.limit


def test_a_worded_box_is_measured_by_its_keys_in_the_pseudo_locale() -> None:
    """A box of Nostromo's keys measures them in the pseudo-locale, filled with its figures (the reset's key made
    pseudo, with WED 12:59 PM in it), not its English made pseudo whole; what of its English is no key's is measured
    as the pseudo-locale draws it: a share alone as its percent, a name as written."""
    resets = budgets.texts(budgets.BOX["nostromo.tooltip.resets"], i18n.PSEUDO)
    template = str(i18n.app_catalog(i18n.PSEUDO).lookup("widget.nostromo.resets.at").value)
    assert i18n.fill(template, {"clock": "WED 12:59 PM"}) in resets
    assert i18n.pseudo_text("RESETS WED 12:59 PM") not in resets and len(resets) == 3
    qualifier = budgets.texts(budgets.BOX["nostromo.floating.row_qualifier"], i18n.PSEUDO)
    assert {"50\u00a0%", "100\u00a0%", i18n.pseudo_text("5H")} <= set(qualifier)
    assert "FABLE" in budgets.texts(budgets.BOX["nostromo.floating.row_name"], i18n.PSEUDO)


def test_a_figure_box_is_measured_with_the_pseudo_locales_figures() -> None:
    """A box of figures alone (Box.words False) measures them in the pseudo-locale as it writes them: a percent with
    its U+00A0 (tight where the box is), an age and a number as English writes them, never bracketed."""
    for box_id in NOSTROMO_FIGURES:
        found = budgets.texts(budgets.BOX[box_id], i18n.PSEUDO)
        assert found and not any("[" in text for text in found), box_id
    strip = budgets.BOX["nostromo.strip.value_5h"]
    assert budgets.texts(strip, i18n.PSEUDO) == list(strip.english)
    assert "13\u00a0%" in budgets.texts(budgets.BOX["nostromo.floating.row_value"], i18n.PSEUDO)
    age = budgets.BOX["nostromo.floating.age"]
    assert budgets.texts(age, i18n.PSEUDO) == list(age.english)


def test_a_worded_boxs_english_is_its_keys_filled() -> None:
    """Each English text of a box of Nostromo's, Night City's or Gates' keys is one of its keys' English filled (each
    placeholder standing for any text), or the box's data: a figure alone or a name alone; and each key's English,
    filled, is one of them."""
    english = i18n.load_catalog(i18n.DEFAULT)
    for box_id in sorted(NOSTROMO_WORDED | NIGHT_CITY_WORDED | GATES_WORDED):
        box = budgets.BOX[box_id]
        patterns = [budgets.key_pattern(str(english.lookup(key).value)) for key in box.keys]
        data = [text for text in box.english if not any(pattern.fullmatch(text) for pattern in patterns)]
        names = budgets.fill_values(box, "name", i18n.DEFAULT)
        assert all(re.fullmatch(r"[0-9.]+%", text) or text in names for text in data), (box_id, data)
        assert all(any(pattern.fullmatch(text) for text in box.english) for pattern in patterns), box_id


@pytest.mark.parametrize("skin", ["nostromo", "night_city", "gates"])
def test_the_guard_line_box_composes_the_statements_the_skin_words(skin: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard line's texts are the skin's _guard_statement's, as its _guard_lines (Gates' _guard_bar_lines) breaks
    them (Nostromo's lead glued on; Night City's and Gates' rest alone, the lead a run of its own), in every language
    and every locale of it, worded through exactly the keys the box lists."""
    module = {"nostromo": nos, "night_city": nc, "gates": g}[skin]
    read: set[str] = set()
    inside = [0]
    real_text, real_statement = i18n.Catalog.text, module._guard_statement

    def text(self: Any, key: str, *args: Any, **kwargs: Any) -> Any:
        if inside[0]:
            read.add(key)
        return real_text(self, key, *args, **kwargs)

    def statement(*args: Any, **kwargs: Any) -> Any:
        inside[0] += 1
        try:
            return real_statement(*args, **kwargs)
        finally:
            inside[0] -= 1

    monkeypatch.setattr(i18n.Catalog, "text", text)
    monkeypatch.setattr(module, "_guard_statement", statement)
    box = budgets.BOX[f"{skin}.tooltip.guard_text" if skin == "gates" else f"{skin}.tooltip.guard_lines"]
    assert box.compose is not None
    leads = {str(i18n.app_catalog(i18n.DEFAULT).text(key)) for key in box.keys if ".guard.armed" in key
             or ".guard.holding" in key}
    for language in i18n.DEV_LANGUAGES:
        composed = box.compose(language)
        assert composed, language
        if skin == "nostromo":
            assert all(engine.NO_BREAK in statement.split(" ")[0] for statement in composed), language
        else:
            assert not any(statement.startswith(tuple(leads)) for statement in composed), language
    assert read == set(box.keys), sorted(read ^ set(box.keys))
    glued = engine.glue("(in 6d 23h)." if skin == "gates" else "(IN 6D 23H).")
    assert any(glued in statement for statement in box.compose(i18n.DEFAULT))


def test_a_nostromo_box_that_shrinks_is_measured_at_its_step(monkeypatch: pytest.MonkeyPatch) -> None:
    """A sign-in line is measured at the first of nostromo.MESSAGE_STEPS where it fits (as _message_fit draws it), and
    a strip tag at the first of STRIP_TAG_SIZES; a line too long even at 9 px is a fault, a tag that long a cut."""
    message, tag = budgets.BOX["nostromo.floating.message"], budgets.BOX["nostromo.strip.window_tag"]
    cv = nos._canvas(nos.FLOAT_W, 100, 1.0, dump.load_theme_objects()["nostromo"])
    line = "X" * 38
    assert budgets.W_nos(line, nos.FONT_SEMI, 10, nos.MESSAGE_TRACKING) > message.limit
    assert message.width(line) == pytest.approx(cv.advance(line, cv.font(nos.FONT_SEMI, 10), 0.0))
    assert message.width(line) <= message.limit
    assert all(tag.width("7GN", scale) <= tag.limit for scale in tag.scales)
    assert tag.width("7GN", 1.0) < budgets.nos_run(nos.FONT_SEMI, 11, 1.1, "strip").width("7GN", 1.0)
    pseudo_tag = i18n.pseudo_text("5H")
    monkeypatch.setattr(budgets, "texts", lambda box, _language: {message.id: [line, "X" * 60],
                                                                  tag.id: ["7GN", pseudo_tag]}[box.id])
    assert [(r.box.id, r.text, r.box.enforced) for r in budgets.over("xx", [message, tag])] == [
        (message.id, "X" * 60, True), (tag.id, pseudo_tag, False)]


_STEP_BOXES = {"age": "nostromo.floating.age", "pill": "nostromo.floating.status_pill",
               "countdown": "nostromo.floating.hold_chip", "pace": "nostromo.tooltip.pace",
               "pace_short": "nostromo.tooltip.pace_short"}


@pytest.mark.parametrize("language", ["fr", "es", i18n.PSEUDO])
def test_the_budgets_give_way_as_the_skin_does(language: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Critic 18 of #94's plan: for the language's worst header (with a state word, the CRITICAL chip and a hold chip on
    its right) and its worst hover line, at every scale, the skin's plan (_header_plan, _info_plan) gives up the same
    boxes in the same order as the budgets' Line.gives_way, and both fit after."""
    import dataclasses

    theme = dump.load_theme_objects()["nostromo"]
    locale = i18n.dev_locale(language)
    box = budgets.BOX
    header = next(item for item in budgets.LINES if item.id == "nostromo.floating.header_line")
    info = next(item for item in budgets.LINES if item.id == "nostromo.tooltip.info_line")
    pill, age = box["nostromo.floating.status_pill"], box["nostromo.floating.age"]
    alone = box["nostromo.floating.hold_word"]
    resets, pace, short = (box[f"nostromo.tooltip.{part}"] for part in ("resets", "pace", "pace_short"))
    taken = []
    for scale in budgets.SCALES:
        cv = nos._canvas(nos.FLOAT_W, 100, scale, theme)
        for right in ("state_word", "state_chip", "hold_chip"):
            right_box = box[f"nostromo.floating.{right}"]
            texts = [budgets.widest_text(b, language, scale) for b in (pill, age, right_box)]
            steps, width, _after = header.give_way(list(zip((pill.id, age.id, right_box.id), texts)), scale,
                                                   {alone.id: budgets.widest_text(alone, language, scale)})
            holding, style = right == "hold_chip", "label" if right == "state_word" else "chip"
            monkeypatch.setattr(nos, "_age_text", lambda _model, text=texts[1]: text)
            monkeypatch.setattr(nos, "_state_word", lambda _model, text=texts[2], style=style: (text, style))
            monkeypatch.setattr(nos, "_hold_word", lambda _model, text=texts[2], holding=holding:
                                text if holding else None)
            model = dump.sample_model("guard_hold_session" if holding else "normal3", locale=locale)
            plan = nos._header_plan(cv, dataclasses.replace(model, status_text=texts[0]))
            mapped = tuple(right_box.id if step == "state_word" else _STEP_BOXES[step] for step in plan.gave_way)
            assert mapped == steps and width <= header.limit, (scale, right, plan, steps)
            taken.append(steps)
        texts = [budgets.widest_text(b, language, scale) for b in (resets, pace, short)]
        steps, width, _after = info.give_way(list(zip((resets.id, pace.id), texts)), scale, {short.id: texts[2]})
        monkeypatch.setattr(nos, "_reset_text", lambda _row, _locale, text=texts[0]: text)
        monkeypatch.setattr(nos, "_pace_word", lambda _row, _locale, text=texts[1]: text)
        monkeypatch.setattr(nos, "_pace_short", lambda _row, *, locale, text=texts[2]: text)
        plan = nos._info_plan(cv, dump.sample_model("normal3", locale=locale).rows[0], locale)
        assert tuple(_STEP_BOXES[step] for step in plan.gave_way) == steps and width <= info.limit, (scale, plan)
        taken.append(steps)
    assert any(taken)                                       # each language's worst lines give way somewhere


def _header_plan_of(monkeypatch: pytest.MonkeyPatch, pill: str, age: str, word: str, style: str,
                    holding: bool) -> Any:
    """nostromo._header_plan at 1.0 for a header that draws these texts (a holding guard's chip when ``holding``)."""
    import dataclasses

    monkeypatch.setattr(nos, "_age_text", lambda _model: age)
    monkeypatch.setattr(nos, "_state_word", lambda _model: (word, style))
    monkeypatch.setattr(nos, "_hold_word", lambda _model: word if holding else None)
    model = dump.sample_model("guard_hold_session" if holding else "normal3")
    return nos._header_plan(nos._canvas(nos.FLOAT_W, 100, 1.0, dump.load_theme_objects()["nostromo"]),
                            dataclasses.replace(model, status_text=pill))


# Each header box, the text of its shape it is tried with (n letters), its step, and how it is drawn.
_HEADER_TRIES: dict[str, tuple[Any, str, str, bool]] = {
    "nostromo.floating.status_pill": (lambda n: "P" * n, "pill", "label", False),
    "nostromo.floating.age": (lambda n: "9" * n, "age", "label", False),
    "nostromo.floating.state_word": (lambda n: "W" * n, "state_word", "label", False),
    "nostromo.floating.state_chip": (lambda n: "C" * n, "state_word", "chip", False),
    "nostromo.floating.hold_chip": (lambda n: "HOLD " + "9" * n, "countdown", "chip", True),
}


@pytest.mark.parametrize("order", list(itertools.permutations(nos.HEADER_GIVE_WAY)))
def test_the_budgets_take_q2s_order_from_the_skins_tuple(order: tuple[str, ...], monkeypatch: pytest.MonkeyPatch) -> None:
    """Q2 lives in nostromo.HEADER_GIVE_WAY alone: in any order of it, the header line's give-way
    (budgets.header_gives_way) is the tuple's steps as boxes, and each header box's room (budgets._header_room, which
    LIMITS pins for the order shipped) is where the skin's _header_plan stops keeping it whole beside English's widest
    other texts, each right-hand word or chip in turn: the longest text of the box's shape it keeps fits the room, and
    one letter more does not."""
    header = next(item for item in budgets.LINES if item.id == "nostromo.floating.header_line")
    assert header.gives_way == budgets.header_gives_way() and set(budgets.HEADER_STEPS) == set(nos.HEADER_GIVE_WAY)
    monkeypatch.setattr(nos, "HEADER_GIVE_WAY", order)
    assert budgets.header_gives_way() == tuple(item for step in order for item in budgets.HEADER_STEPS[step])
    widest = {box_id: budgets.widest_text(budgets.BOX[box_id], i18n.DEFAULT, 1.0) for box_id in budgets.HEADER_ADDED}
    rights = [(widest[box_id], style, holding) for box_id, style, holding in (
        ("nostromo.floating.state_word", "label", False), ("nostromo.floating.state_chip", "chip", False),
        ("nostromo.floating.hold_chip", "chip", True), ("nostromo.floating.hold_word", "chip", True))]
    for box_id, (text_of, step, style, holding) in _HEADER_TRIES.items():
        box = budgets.BOX[box_id]
        room = budgets._header_room(box_id, budgets._HEADER_WIDEST.__getitem__, budgets._NOS_NAME_FLOOR)
        kept = []
        for right, right_style, right_holding in (rights if box_id in (budgets._PILL, budgets._AGE)
                                                         else [(None, style, holding)]):
            def whole(n: int) -> bool:
                texts = {"pill": widest[budgets._PILL], "age": widest[budgets._AGE]}
                if right is None:
                    word, shown, hold = text_of(n), style, holding
                else:
                    word, shown, hold = right, right_style, right_holding
                texts["pill" if box_id == budgets._PILL else "age" if box_id == budgets._AGE else "word"] = text_of(n)
                plan = _header_plan_of(monkeypatch, texts["pill"], texts["age"], texts.get("word", word), shown, hold)
                return step not in plan.gave_way
            low, high = 0, 60                               # whole(low) holds (0 letters is no text), whole(high) not
            while high - low > 1:
                middle = (low + high) // 2
                low, high = (middle, high) if whole(middle) else (low, middle)
            kept.append(low)
        most = min(kept)
        assert (most == 0 or box.width(text_of(most)) <= room + 0.01) and box.width(text_of(most + 1)) > room + 0.01, (
            box_id, order, most, room)


def test_a_languages_header_rooms_are_its_own_tags() -> None:
    """In a language, Nostromo's header boxes have the rooms their limits are worked out by, beside the language's own
    widest texts and each format tag's own figures (budgets.header_room): English's at 1.0 are its limits; the age
    has 22.8 px beside fr-FR's HOLD 23H 59MIN, 8.4 beside es-CO's HOLD 23 H 59 MIN and English's 37.2 beside fr-CA's
    HOLD 23H 59M; and a figure is held to the rooms of the tags that write it (fr-CA's 59M keeps 37.2), a word to the
    least of any tag's."""
    for box_id in budgets.HEADER_ADDED:
        assert budgets.header_room(box_id, i18n.DEFAULT, 1.0) == pytest.approx(budgets.BOX[box_id].limit), box_id
    age, word = budgets.BOX["nostromo.floating.age"], budgets.BOX["nostromo.floating.state_word"]
    assert budgets.header_room(age.id, "fr", 1.0, "fr-FR") == pytest.approx(22.8)
    assert budgets.header_room(age.id, "fr", 1.0, "fr-CA") == pytest.approx(age.limit)
    assert budgets.header_room(age.id, "es", 1.0) == pytest.approx(8.4) == budgets.header_room(age.id, "es", 1.0,
                                                                                               "es-CO")
    assert "59M" in budgets.header_tags("fr")["fr-CA"][age.id] and "59M" not in budgets.header_tags("fr")["fr-FR"][age.id]
    assert budgets.allowed(age, "fr", "59M", 1.0) == pytest.approx(age.limit)
    assert budgets.allowed(age, "fr", "59MIN", 1.0) == pytest.approx(22.8)
    assert budgets.allowed(age, "es", "59S", 1.0) == pytest.approx(22.8)           # es-ES's, never beside es-CO's chip
    assert budgets.allowed(word, "es", "THROTTLED", 1.0) == pytest.approx(budgets.header_room(word.id, "es", 1.0))
    assert budgets.allowed(age, i18n.DEFAULT, "12 S", 1.0) == budgets.budget(age, 1.0)


# ── Night City's words and fits (#95) ─────────────────────────────────────────────────────────────────────────────

def test_night_city_passes_its_budgets_in_the_pseudo_locale() -> None:
    """In the pseudo-locale (about 40 % longer, bracketed, accented) no Night City box or line that has no fallback or
    only shrinks runs past its budget: its words come from its keys and fit, shrink or give way as the skin makes them.
    What gives way is reported as a cut. Every Night City box is worded by keys, holds figures alone, is the brand drawn
    as written, or composes."""
    rows = budgets.over(i18n.PSEUDO)
    assert [(r.box.id, r.text) for r in rows if r.box.id.startswith("night_city.") and r.box.enforced] == []
    cut = {r.box.id for r in rows if r.box.id.startswith("night_city.")}
    assert {"night_city.floating.band_age", "night_city.tooltip.info_pace", "night_city.strip.status_word",
            "night_city.strip.window_label"} <= cut
    city = {box.id for box in budgets.BOXES if box.id.startswith("night_city.")}
    assert {box.id for box in budgets.BOXES if box.id in city and box.keys and box.compose is None} == NIGHT_CITY_WORDED
    assert city == NIGHT_CITY_WORDED | NIGHT_CITY_FIGURES | NIGHT_CITY_AS_WRITTEN | {"night_city.tooltip.guard_lines"}
    assert budgets.texts(budgets.BOX["night_city.floating.brand"], i18n.PSEUDO) == [nc.BRAND_MARK]
    # Its keys made pseudo and filled (the reset's with WED 12:59 PM in it), not its English made pseudo whole.
    resets = budgets.texts(budgets.BOX["night_city.tooltip.info_reset"], i18n.PSEUDO)
    template = str(i18n.app_catalog(i18n.PSEUDO).lookup("widget.night_city.resets.at").value)
    assert i18n.fill(template, {"clock": "WED 12:59 PM"}) in resets
    assert i18n.pseudo_text("RST WED 12:59 PM") not in resets


def _nc_catalog(monkeypatch: pytest.MonkeyPatch, words: dict[str, str]) -> None:
    english = i18n.load_catalog("en").english()
    catalog = i18n.Catalog("en", {}, {**english, **words})
    monkeypatch.setattr(i18n, "app_catalog", lambda language: catalog)


def _nc_line(line_id: str) -> Any:
    return next(item for item in budgets.LINES if item.id == line_id)


@pytest.mark.parametrize("language", ["fr", "es", i18n.PSEUDO])
def test_the_budgets_give_way_as_night_city_does(language: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Critic 18 of #94's plan, for Night City: for the language's worst header (the LIVE tag, the status chip and the
    hold chip on its right), its worst band (the guard holding and not) and its worst hover line, the skin's plans
    (_header_plan, _band_plan, _info_plan) give up the same boxes in the same order as the budgets' Line.gives_way, and
    both fit after; and what the band and the hover line keep is the same (the age or none, the hold chip or HOLD alone,
    the pace whole, its points or none). Night City's widths are the same at every scale, so this is at 1.0."""
    import dataclasses

    theme = dump.load_theme_objects()["night_city"]
    blackwall = theme.variants[0]
    ink = nc._Ink(nc._canvas(nc.FLOAT_W, 126, 1.0, theme), nc.Palette.for_variant(theme, blackwall), RenderOptions())
    look = nc.look_for(blackwall)
    widest = {box_id: budgets.widest_text(budgets.BOX[box_id], language, 1.0) for box_id in budgets.NC_HOME}
    taken = []
    header = _nc_line("night_city.floating.header_line")
    for right, kind in (("live_tag", "live"), ("status_chip", "chip"), ("hold_chip", "hold")):
        right_id = f"night_city.floating.{right}"
        steps, width, _after = header.give_way([("night_city.floating.brand", nc.BRAND_MARK),
                                                (right_id, widest[right_id])], 1.0,
                                               {"night_city.floating.hold_word": widest["night_city.floating.hold_word"]})
        monkeypatch.setattr(nc, "_hold_word", lambda _model, kind=kind, text=widest[right_id]:
                            text if kind == "hold" else None)
        _nc_catalog(monkeypatch, {"widget.night_city.state.live": widest[right_id],
                                  "widget.night_city.guard.hold": widest["night_city.floating.hold_word"]})
        model = dump.sample_model("guard_hold_session" if kind == "hold" else "normal3" if kind == "live" else "paused")
        if kind == "chip":
            model = dataclasses.replace(model, status_text=widest[right_id])
        plan = nc._header_plan(ink, look, model)
        mapped = tuple("night_city.floating.hold_chip" if step == "countdown" else
                       "night_city.floating.hold_word" if "countdown" in plan.gave_way else right_id
                       for step in plan.gave_way)
        assert mapped == steps and width <= header.limit, (right, plan, steps)
        taken.append(steps)
    monkeypatch.undo()
    # A cut ends with the skin's own three dots (Q3, kept on #95), the budgets' floors too; Nostromo's with its "…".
    assert nc._cut(ink, "W" * 20, nc.CHIP, 40.0).endswith(budgets.cut_mark(header.id))
    assert budgets.cut_mark("nostromo.floating.age") == nos.ELLIPSIS != budgets.cut_mark(header.id)
    for holding in (False, True):
        line = _nc_line("night_city.floating.band_hold_line" if holding else "night_city.floating.band_line")
        cause_id = "night_city.floating.band_hold_text" if holding else "night_city.floating.band_text"
        runs = [(cause_id, widest[cause_id]), ("night_city.floating.band_age", widest["night_city.floating.band_age"])]
        if holding:
            runs.append(("night_city.floating.band_hold_chip", widest["night_city.floating.band_hold_chip"]))
        steps, width, after = line.give_way(runs, 1.0, {
            "night_city.floating.band_hold_word": widest["night_city.floating.band_hold_word"]})
        plan = nc._band_plan(ink, dump.sample_model("stale3"), widest[cause_id], widest["night_city.floating.band_age"],
                             widest["night_city.floating.band_hold_chip"] if holding else None,
                             widest["night_city.floating.band_hold_word"])
        names = {"countdown": "night_city.floating.band_hold_chip", "age": "night_city.floating.band_age",
                 "cause": cause_id}
        assert tuple(names[step] for step in plan.gave_way) == steps and width <= line.limit, (holding, plan, steps)
        kept = {box: text or None for box, text in after}
        chip = kept.get("night_city.floating.band_hold_chip", kept.get("night_city.floating.band_hold_word"))
        assert (kept["night_city.floating.band_age"], chip) == (plan.age, plan.chip), (holding, plan, after)
        taken.append(steps)
    info = _nc_line("night_city.tooltip.info_line")
    reset, pace, short = (widest[f"night_city.tooltip.{part}"] for part in ("info_reset", "info_pace", "info_pace_short"))
    steps, width, after = info.give_way([("night_city.tooltip.info_reset", reset), ("night_city.tooltip.info_pace", pace)],
                                        1.0, {"night_city.tooltip.info_pace_short": short})
    monkeypatch.setattr(nc, "_reset_text", lambda _row, _tooltip, *, locale: reset)
    monkeypatch.setattr(nc, "_pace_text", lambda _row, *, locale: (pace, "behind"))
    monkeypatch.setattr(nc, "_pace_short", lambda _row: short)
    model = dump.sample_model("normal3")
    plan = nc._info_plan(ink, look, model, model.rows[0])
    names = {"pace": "night_city.tooltip.info_pace", "pace_short": "night_city.tooltip.info_pace_short"}
    assert tuple(names[step] for step in plan.gave_way) == steps and width <= info.limit, (plan, steps)
    assert [text or None for _box, text in after] == [plan.reset, plan.pace], (plan, after)
    taken.append(steps)
    assert any(taken)                                       # each language's worst lines give way somewhere


@pytest.mark.parametrize("order", list(itertools.permutations(nc.HEADER_GIVE_WAY)))
def test_the_budgets_take_the_header_order_from_night_citys_tuple(order: tuple[str, ...],
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """Q2 lives in night_city.HEADER_GIVE_WAY alone (and the band's and the hover line's orders in theirs): every Night
    City line's give-way is its tuple's steps as boxes (budgets.nc_gives_way); in any order of Q2's, so is the header's,
    and each right-hand box's room (budgets._nc_room, which LIMITS pins for the order shipped) is where the skin's
    _header_plan stops keeping it whole: the longest text of the box's shape it keeps fits the room, and one letter more
    does not."""
    import dataclasses

    for line_id, plan in budgets.NC_PLANS.items():
        assert _nc_line(line_id).gives_way == budgets.nc_gives_way(plan), line_id
    monkeypatch.setattr(nc, "HEADER_GIVE_WAY", order)
    assert budgets.nc_gives_way(budgets.NC_HEADER) == tuple(item for step in order
                                                            for item in budgets.NC_HEADER.step(step))
    theme = dump.load_theme_objects()["night_city"]
    blackwall = theme.variants[0]
    ink = nc._Ink(nc._canvas(nc.FLOAT_W, 126, 1.0, theme), nc.Palette.for_variant(theme, blackwall), RenderOptions())
    look = nc.look_for(blackwall)
    real_hold = nc._hold_word
    for box_id, kind, text_of in (("night_city.floating.live_tag", "live", lambda n: "W" * n),
                                  ("night_city.floating.status_chip", "chip", lambda n: "W" * n),
                                  ("night_city.floating.hold_chip", "hold", lambda n: "HOLD " + "9" * n)):
        box = budgets.BOX[box_id]
        room = budgets._nc_room(budgets.NC_HEADER, box_id, budgets._nc_widest, budgets._nc_floor)

        def whole(n: int, kind: str = kind, text_of: Any = text_of) -> bool:
            text = text_of(n)
            monkeypatch.setattr(nc, "_hold_word", (lambda _model: text) if kind == "hold" else real_hold)
            _nc_catalog(monkeypatch, {"widget.night_city.state.live": text})
            model = dump.sample_model({"live": "normal3", "chip": "paused", "hold": "guard_hold_session"}[kind])
            if kind == "chip":
                model = dataclasses.replace(model, status_text=text)
            return nc._header_plan(ink, look, model).gave_way == ()

        low, high = 1, 80                                   # whole(low) holds, whole(high) not
        while high - low > 1:
            middle = (low + high) // 2
            low, high = (middle, high) if whole(middle) else (low, middle)
        assert box.width(text_of(low)) <= room + 0.01 < box.width(text_of(low + 1)), (box_id, order, low, room)


def test_a_night_city_boxs_room_in_a_language_is_its_own_tags() -> None:
    """In a language, Night City's line boxes have the rooms their limits are worked out by, beside the language's own
    widest texts and each format tag's own figures (budgets.nc_room; critic 1 and 2 of #95's plan): English's are its
    limits; the hover line's pace has less room beside fr-CA's 12-hour day clock (RST LUN. 12 H 59 P.M.) than beside
    English's reset; the band's age keeps its room beside the widest cause (a word) in every tag, so es-CO's 100 DÍAS OLD
    is held to it; and a figure is held to the rooms of the tags that write it, a word to the least of any tag's."""
    for box_id in budgets.NC_HOME:
        assert budgets.nc_room(box_id, i18n.DEFAULT) == pytest.approx(budgets.BOX[box_id].limit), box_id
    pace, age = budgets.BOX["night_city.tooltip.info_pace"], budgets.BOX["night_city.floating.band_age"]
    canada = budgets.nc_room(pace.id, "fr", 1.0, "fr-CA/12")
    assert canada < pace.limit - 20 and budgets.allowed(pace, "fr", "ON PACE", 1.0) == pytest.approx(
        budgets.nc_room(pace.id, "fr", 1.0))
    assert "RST LUN. 12 H 59" + chr(0x202F) + "P.M." in budgets.nc_tags("fr")["fr-CA/12"]["night_city.tooltip.info_reset"]
    assert "100 DÍAS OLD" in budgets.nc_tags("es")["es-CO/24"][age.id]
    assert budgets.allowed(age, "es", "100 DÍAS OLD", 1.0) == pytest.approx(age.limit)
    assert budgets.allowed(age, i18n.DEFAULT, "41 M OLD", 1.0) == budgets.budget(age, 1.0)
    # The brand is drawn as written in every language, so the header's words have English's room beside it, never the
    # whole line (a language with no brand text of its own once measured it as empty: 207 px for the LIVE tag).
    for language in i18n.DEV_LANGUAGES:
        for box_id in ("night_city.floating.live_tag", "night_city.floating.status_chip",
                       "night_city.floating.hold_chip", "night_city.floating.hold_word"):
            assert budgets.nc_room(box_id, language) == pytest.approx(budgets.BOX[box_id].limit), (language, box_id)


def test_night_citys_guard_chip_has_the_room_its_header_leaves_beside_the_languages_words(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """#95's review (look-1): the GUARD chip is chosen last, in the room the header's right-hand word leaves
    (night_city._header_plan), so its room in a language is beside that language's own LIVE tag and status words
    (budgets.nc_guard_room), the least of the headers English draws its chip in: English's is the box's limit; a short
    guard word beside a wide status word, which the header leaves out where English's GUARD stays, is reported as the
    box's cut (drop: a draft's word could settle it); and in each of those headers the room is the one the skin leaves
    beside the word as it draws it (a status word too wide for the header cut first), and the skin keeps a guard word as
    wide as that room and leaves out one a letter wider."""
    import dataclasses

    box = budgets.BOX["night_city.floating.guard_chip"]
    pairs = budgets.nc_guard_pairs()
    assert budgets.nc_guard_room(i18n.DEFAULT) == pytest.approx(box.limit) and box.fit == "drop"
    # Only LIVE's and PAUSED's headers are drawn with a reading: the band takes the header's place in its statuses.
    assert {status for status, _variant in pairs} == {"LIVE", "PAUSED"} and len(pairs) == 6
    assert not any(nc.ApiStatus[status] in nc.BAND_STATUSES for status, _variant in pairs)
    assert budgets.over("fr", [box]) == []                  # French has no words of its own here yet: English's room
    words = {"widget.status.paused": "EN PAUSE", "widget.night_city.guard.mark": "GARDE"}
    _nc_catalog(monkeypatch, words)
    rooms = budgets._nc_guard_rooms(i18n.app_catalog("fr"))
    assert rooms["PAUSED", "blackwall"] < budgets.W_nc("GARDE", "CHIP") < box.limit
    rows = budgets.over("fr", [box])
    assert [(row.box.id, row.text) for row in rows] == [(box.id, "GARDE")] and not rows[0].box.enforced
    assert rows[0].budget == pytest.approx(budgets.nc_guard_room("fr")) and budgets.nc_guard_room("fr") < box.limit
    words["widget.status.paused"] = "W" * 30                # wider than the header's right-hand room: cut there
    _nc_catalog(monkeypatch, words)
    rooms = budgets._nc_guard_rooms(i18n.app_catalog("fr"))
    theme = dump.load_theme_objects()["night_city"]
    armed = dump.sample_model("guard_armed")
    for status_name, variant_id in pairs:
        variant = theme.variant(variant_id)
        ink = nc._Ink(nc._canvas(nc.FLOAT_W, 126, 1.0, theme), nc.Palette.for_variant(theme, variant), RenderOptions())
        look, status = nc.look_for(variant), nc.ApiStatus[status_name]
        model = dataclasses.replace(armed, status=status, stale=status not in (nc.ApiStatus.LIVE, nc.ApiStatus.PAUSED),
                                    status_text=i18n.app_catalog("fr").text(budgets.STATUS_KEYS[status]))
        room = rooms[status_name, variant_id]
        plan = nc._header_plan(ink, look, model)
        spec = nc.STATUS if plan.kind == "live" else nc.CHIP
        left = nc.RIGHT - (ink.width(plan.word, spec) + nc._right_pad(ink, look, plan.kind))
        brand_end = nc._brand_x(ink.pal) + ink.width(nc.BRAND_MARK, nc.BRAND)
        assert room == pytest.approx(left - 2 * nc.GUARD_GAP - brand_end - 12), (status_name, variant_id, plan)

        def kept(n: int) -> bool:
            _nc_catalog(monkeypatch, {**words, "widget.night_city.guard.mark": "W" * n})
            return nc._header_plan(ink, look, model).mark is not None

        most = max((n for n in range(1, 12) if kept(n)), default=0)
        assert (most == 0 or budgets.W_nc("W" * most, "CHIP") <= room + 0.01) and room < budgets.W_nc(
            "W" * (most + 1), "CHIP"), (status_name, variant_id, most, room)
        _nc_catalog(monkeypatch, words)
    assert rooms["PAUSED", "blackwall"] < 0 and min(rooms[pair] for pair in pairs) == pytest.approx(
        budgets.nc_guard_room("fr"))


# ── Gates (#96) ───────────────────────────────────────────────────────────────────────────────────────────────────

def _g_line(line_id: str) -> Any:
    return next(item for item in budgets.LINES if item.id == line_id)


def _g_ink() -> Any:
    return g.Ink(g.CARD_W, g.CARD_H, 1.0, g._fonts(dump.load_theme_objects()["gates"]))


def _g_catalog(monkeypatch: pytest.MonkeyPatch, words: dict[str, str]) -> None:
    english = i18n.load_catalog("en").english()
    catalog = i18n.Catalog("en", {}, {**english, **words})
    monkeypatch.setattr(i18n, "app_catalog", lambda language: catalog)


def test_gates_passes_its_budgets_in_the_pseudo_locale() -> None:
    """In the pseudo-locale (about 40 % longer, bracketed, accented) no Gates box or line that has no fallback or only
    shrinks runs past its budget (before #96 its title, header, status word, hover line, strip and tray did): its words
    come from its keys and fit, shrink or give way as the skin makes them, and its title is the brand mark, drawn as
    written. What gives way is reported as a cut: the pill's age, the status word, the pace, a sign-in footer's lead,
    a strip tag. The pill word and the strip tag are measured at the size they shrink to. Every Gates box is worded by
    keys, holds figures alone, is the title, or composes."""
    rows = budgets.over(i18n.PSEUDO)
    assert [(r.box.id, r.text) for r in rows if r.box.id.startswith("gates.") and r.box.enforced] == []
    cut = {r.box.id for r in rows if r.box.id.startswith("gates.")}
    assert {"gates.floating.status_pill", "gates.floating.status_word_shrunk", "gates.tooltip.pace",
            "gates.floating.stale_lead", "gates.strip.window_tag"} <= cut
    assert not cut & {line.id for line in budgets.LINES if line.gives_way}      # each gives way and then fits
    gates = {box.id for box in budgets.BOXES if box.id.startswith("gates.")}
    assert {box.id for box in budgets.BOXES if box.id in gates and box.keys and box.compose is None} == GATES_WORDED
    assert {box.id for box in budgets.BOXES if box.id in gates and box.compose is not None} == GATES_COMPOSED
    assert gates == GATES_WORDED | GATES_FIGURES | GATES_AS_WRITTEN | GATES_COMPOSED
    for box_id in GATES_AS_WRITTEN:
        assert budgets.texts(budgets.BOX[box_id], i18n.PSEUDO) == [g.TITLE], box_id
    tag, word = budgets.BOX["gates.strip.window_tag"], budgets.BOX["gates.floating.status_word_shrunk"]
    assert budgets.W_g("WWI", "body", g.SIZE_STRIP_K, 600) > tag.limit >= tag.width("WWI")
    assert budgets.W_g("RATE LIMITED W", "body", g.SIZE_PILL, 600) > word.limit >= word.width("RATE LIMITED W")


@pytest.mark.parametrize("language", ["fr", "es", i18n.PSEUDO])
def test_the_budgets_give_way_as_gates_does(language: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Critic 18 of #94's plan, for Gates (#96): for the language's worst header on each surface (its widest status
    word beside the guard's holding capsule), its worst hover line and its worst sign-in footers, the skin's plans
    (_header_plan, _info_plan, _footer_plan) give up the same boxes in the same order as the budgets' Line.gives_way,
    and both fit after; what the hover line keeps is the same, and the footer keeps its lead exactly where the budgets
    do. English's words, which French and Spanish show until #104 and #105, take no step. Gates' widths are the same at
    every scale, so this is at 1.0."""
    import dataclasses

    ink = _g_ink()
    widest = {box_id: budgets.widest_text(budgets.BOX[box_id], language, 1.0) for box_id in budgets.G_HOME}
    taken = []
    for surface, more in (("floating", True), ("tooltip", False)):
        p = f"gates.{surface}."
        line, word = _g_line(p + "header_line"), widest[p + "status_word"]
        steps, width, _after = line.give_way([(p + "title", g.TITLE), (p + "status_word", word)], 1.0,
                                             {p + "status_word_shrunk": widest[p + "status_word_shrunk"]})
        model = dataclasses.replace(dump.sample_model("guard_hold_session"), status_text=word)
        plan = g._header_plan(ink, model, more=more)
        names = {"shrink": p + "status_word",
                 "word": p + ("status_word_shrunk" if "shrink" in plan.gave_way else "status_word")}
        assert tuple(names[step] for step in plan.gave_way if step != "age") == steps, (surface, plan, steps)
        assert width <= line.limit + 0.01, (surface, plan, width)
        taken.append(steps)
    line = _g_line("gates.tooltip.info_line")
    reset, pace, short = (widest[f"gates.tooltip.{part}"] for part in ("resets", "pace", "pace_short"))
    steps, width, after = line.give_way([("gates.tooltip.resets", reset), ("gates.tooltip.pace", pace)], 1.0,
                                        {"gates.tooltip.pace_short": short})
    _g_catalog(monkeypatch, {"widget.gates.resets.at": reset})
    monkeypatch.setattr(g, "_pace_text", lambda _row, *, locale: (pace, "ok"))
    monkeypatch.setattr(g, "_pace_short", lambda _row, *, locale: short)
    model = dump.sample_model("normal3")
    plan = g._info_plan(ink, model, model.rows[0])
    names = {"pace": "gates.tooltip.pace", "pace_short": "gates.tooltip.pace_short"}
    assert tuple(names[step] for step in plan.gave_way) == steps and width <= line.limit + 0.01, (plan, steps)
    assert [text or None for _box, text in after] == [plan.reset, plan.pace], (plan, after)
    taken.append(steps)
    for kind, status in budgets.G_STEP_KINDS.items():
        lead_id, step_id, alone_id = (f"gates.floating.{kind}_{part}" for part in ("lead", "step", "step_alone"))
        line = _g_line(f"gates.floating.{kind}_footer")
        steps, width, after = line.give_way([(lead_id, widest[lead_id]), (step_id, widest[step_id])], 1.0,
                                            {alone_id: widest[alone_id]})
        monkeypatch.setattr(g, "_footer_runs", lambda _model, kind=kind: [(widest[f"gates.floating.{kind}_lead"], 600,
                                                                           "warn"),
                                                                          (widest[f"gates.floating.{kind}_step"], 400,
                                                                           "text_3")])
        plan = g._footer_plan(ink, dataclasses.replace(dump.sample_model("stale"), status=status))
        names = {"lead": lead_id, "step": alone_id if "lead" in plan.gave_way else step_id}
        assert tuple(names[step] for step in plan.gave_way) == steps and width <= line.limit + 0.01, (kind, plan)
        assert (after[0][1] == "") == ("lead" in plan.gave_way) and len(plan.runs) == 2 - (after[0][1] == ""), kind
        taken.append(steps)
    assert any(taken) == (language == i18n.PSEUDO), taken


# Ages a pill can show (gates._age_label), so a test can give the skin English's widest at a size.
_G_AGE_SECONDS = (12, 59, 120, 360, 41 * 60, 44 * 60, 59 * 60, 3600, 47 * 3600, 2 * 86400, 100 * 86400)


def _g_longest(whole: Any) -> int:
    """The most W a header keeps whole (whole(n) holds for 1, not for 40, and turns false once)."""
    low, high = 1, 40
    assert whole(low) and not whole(high)
    while high - low > 1:
        middle = (low + high) // 2
        low, high = (middle, high) if whole(middle) else (low, middle)
    return low


@pytest.mark.parametrize("order", list(itertools.permutations(g.HEADER_GIVE_WAY)))
def test_the_budgets_take_the_header_order_from_gates_tuple(order: tuple[str, ...],
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """Q2 lives in gates.HEADER_GIVE_WAY alone (the hover line's and the sign-in footers' orders in theirs; #96's
    review F1): every Gates plan line's give-way is its tuple's steps as boxes (budgets.nc_gives_way), and in any order
    of Q2's so is the header's, its line built with the age beside the word too. Each header box's room is where the
    skin's _header_plan stops keeping its text whole beside the guard's holding capsule, with the pill's age where the
    order keeps it beside the word (its step after the word's: budgets.GHeaderPlan): the status word at 10 px and the
    shrunk word at the floor (where the order shrinks it before it cuts it), with English's widest age at that size (a
    word with no age keeps as much at least), and in a language beside each format tag's own ages (g_room); and the
    pill's text, its word and age, beside each guard mark at the size the order draws it whole at (_g_pill_size), past
    which its age is left out or its word cut (_g_pill_fit). Shipped (the age first) the word's rooms are the limits
    LIMITS pins. The longest text of its shape the skin keeps whole fits the room, and one letter more does not."""
    import dataclasses

    for line_id, plan in budgets.G_PLANS.items():
        assert _g_line(line_id).gives_way == budgets.nc_gives_way(plan), line_id
    monkeypatch.setattr(g, "HEADER_GIVE_WAY", order)
    for plan in (budgets.G_HEADER_F, budgets.G_HEADER_T):
        assert budgets.nc_gives_way(plan) == tuple(item for step in order for item in plan.step(step))
    ink = _g_ink()
    base = dump.sample_model("guard_hold_session")

    def age(seconds: int) -> str:
        return " · " + g._age_label(dataclasses.replace(base, age_seconds=seconds))

    spanish = budgets.g_tags("es")
    lines = {line.id: line for line in budgets._g_lines()}         # as an answer to Q2 would build them
    for surface, more, plan in (("floating", True, budgets.G_HEADER_F), ("tooltip", False, budgets.G_HEADER_T)):
        p = f"gates.{surface}."
        line = lines[p + "header_line"]
        assert line.gives_way == budgets.nc_gives_way(plan) and all(
            line.added(box) == pytest.approx(plan.add(box)) for slot in plan.slots for box in slot), (surface, order)
        for box_id, size, at in ((p + "status_word", g.SIZE_PILL, lambda drawn: drawn.size == g.SIZE_PILL),
                                 (p + "status_word_shrunk", g.PILL_SIZES[-1], lambda drawn: True)):
            if box_id.endswith("_shrunk") and order.index("word") < order.index("shrink"):
                continue                                    # the word is cut at 10 px: it never shrinks
            room = budgets._nc_room(plan, box_id, budgets._g_widest, budgets._g_floor, becoming=False)
            if order[0] == "age":
                assert room == pytest.approx(budgets.BOX[box_id].limit) and plan.age(box_id) == 0, box_id
            widest = max(_G_AGE_SECONDS, key=lambda seconds: budgets.W_g(age(seconds), "body", size, 600))
            assert budgets.W_g(age(widest), "body", size, 600) == pytest.approx(
                max(budgets.W_g(text, "body", size, 600) for text in budgets._G_AGES))
            fits = _g_longest(lambda n: budgets.W_g("W" * n, "body", size, 600) <= room + 0.01)
            for seconds in (widest, None):

                def whole(n: int, seconds: int | None = seconds) -> bool:
                    model = dataclasses.replace(base, status_text="W" * n, age_seconds=seconds)
                    drawn = g._header_plan(ink, model, more=more)
                    return drawn.word == "W" * n and at(drawn)

                kept = _g_longest(whole)
                assert kept == fits if seconds else kept >= fits, (box_id, order, seconds, kept, fits, room)
            for tag, figures in spanish.items():          # the tag's own ages beside the word (es-CO's 100 días)
                ages = max(budgets.W_g(text, "body", size, 600) for text in figures["ages"])
                assert budgets.g_room(box_id, "es", 1.0, tag) == pytest.approx(
                    room + plan.age(box_id) - (ages if plan.age(box_id) else 0.0)), (box_id, tag)
        size, rooms = budgets._g_pill_size(), budgets._g_pill_rooms(surface)
        for mark, state in (("none", "normal3"), ("armed", "guard_armed"), ("holding", "guard_hold_session")):
            model = dump.sample_model(state)

            def pill(n: int, model: Any = model) -> Any:
                return g._header_plan(ink, dataclasses.replace(model, status_text="W" * n, age_seconds=12), more=more)

            kept = _g_longest(lambda n: pill(n).word == "W" * n and pill(n).age is not None)
            room = rooms["LIVE", mark]
            assert budgets.W_g("W" * kept + " · 12 s", "body", size, 600) <= room + 0.01 < budgets.W_g(
                "W" * (kept + 1) + " · 12 s", "body", size, 600), (surface, mark, order, kept, room)
            past = pill(kept + 1)
            assert (past.age is None) == (budgets._g_pill_fit() == "drop"), (surface, mark, order, past)


def test_a_gates_box_room_in_a_language_is_its_own_tags() -> None:
    """In a language, Gates' line boxes have the rooms their limits are worked out by, beside the language's own
    widest texts and each format tag's own figures (budgets.g_room; #95's lesson): English's are its limits; the hover
    line's pace has less room beside fr-CA's 12-hour day clock than beside English's reset; the guard footer's rest is
    the widest each tag's clocks and countdowns make (one per tag), held to that tag's room; a word is held to the
    least of any tag's; and a row's tag has the room the language's own widest label and value leave (critic 5)."""
    for box_id in budgets.G_HOME:
        assert budgets.g_room(box_id, i18n.DEFAULT) == pytest.approx(budgets.BOX[box_id].limit), box_id
    pace, rest = budgets.BOX["gates.tooltip.pace"], budgets.BOX["gates.floating.hold_rest"]
    canada = budgets.g_tags("fr")["fr-CA/12"]
    assert "Resets sam. 12 h 59\u202fp.m." in canada["gates.tooltip.resets"]
    assert budgets.g_room(pace.id, "fr", 1.0, "fr-CA/12") == pytest.approx(
        g.TRACK_W - g.INFO_GAP - budgets.W_gs("Resets sam. 12 h 59\u202fp.m.", "gates.tooltip.resets"))
    assert budgets.g_room(pace.id, "fr", 1.0, "fr-CA/12") < pace.limit - 10
    assert budgets.allowed(pace, "fr", "On pace", 1.0) == pytest.approx(budgets.g_room(pace.id, "fr", 1.0))
    assert canada[rest.id] == (" · Resumes sam. 12 h 59\u202fp.m. (in 23h 59m)",)
    assert all(len(figures[rest.id]) == 1 for figures in budgets.g_tags("es").values())
    assert budgets.allowed(rest, "fr", canada[rest.id][0], 1.0) == pytest.approx(
        budgets.g_room(rest.id, "fr", 1.0, "fr-CA/12"))
    tag = budgets.BOX["gates.floating.row_tag"]
    value = max(budgets.W_gs(text, "gates.floating.row_value") for text in budgets.texts(
        budgets.BOX["gates.floating.row_value"], "fr"))
    assert budgets.g_room(tag.id, "fr") == pytest.approx(
        g.TRACK_W - 10 - value - 6 - budgets.W_gs("Claude Code weekly", "gates.floating.row_label"))
    assert budgets.g_room(tag.id, "fr") < tag.limit


def test_gates_pill_age_room_is_its_headers_beside_the_languages_words(monkeypatch: pytest.MonkeyPatch) -> None:
    """#95's lesson (a word budgeted beside another only where the skin draws them together), for Gates' pill (Q2's
    first step, the age): its room in a language is beside the language's own status word, for each status the least
    of the headers (the guard's mark beside it, each surface) English shows its widest age in (budgets.g_pill_pairs):
    RATE LIMITED's without the holding capsule, never beside it, where English leaves its age out (LOADING has no
    reading, so no mark); English's widest pill fits there; each such room is the one the skin's _header_plan keeps the
    age in, a word a letter wider leaving it out; and a status word the language makes wider is held to it."""
    import dataclasses

    ink = _g_ink()
    guards = {"none": "normal3", "armed": "guard_armed", "holding": "guard_hold_session"}
    for surface, more in (("floating", True), ("tooltip", False)):
        pairs, rooms = budgets.g_pill_pairs(surface), budgets._g_pill_rooms(surface)
        assert ("RATE LIMITED", "none") in pairs and ("RATE LIMITED", "holding") not in pairs, surface
        assert ("LOADING", "none") in pairs and not any(status == "LOADING" and mark != "none" for status, mark in rooms)
        box = budgets.BOX[f"gates.{surface}.status_pill"]
        for status, mark in pairs:
            room = rooms[status, mark]
            assert max(box.width(word + age) for word in (status,) for age in budgets._G_AGES) <= room + 0.01

            def kept(n: int, mark: str = mark) -> bool:
                model = dataclasses.replace(dump.sample_model(guards[mark]), status_text="W" * n, age_seconds=12)
                return g._header_plan(ink, model, more=more).age is not None

            low = max(n for n in range(1, 30) if kept(n))
            assert box.width("W" * low + " · 12 s") <= room + 0.01 < box.width("W" * (low + 1) + " · 12 s"), (
                surface, status, mark, low, room)
    rate_limited = min(budgets._g_pill_rooms("floating")[pair] for pair in budgets.g_pill_pairs("floating")
                       if pair[0] == "RATE LIMITED")
    pill = budgets.BOX["gates.floating.status_pill"]
    assert budgets.g_pill_room("floating", "es", "RATE LIMITED · 100 días") == pytest.approx(rate_limited)
    assert budgets.allowed(pill, "es", "RATE LIMITED · 100 días", 1.0) == pytest.approx(rate_limited)
    assert rate_limited > budgets.budget(pill)              # its own room, more than English's widest pill takes
    budgets._g_pills.cache_clear()
    try:
        _g_catalog(monkeypatch, {"widget.status.stale": "STALE WWW"})
        pills = budgets._g_pills("fr", "floating")
        assert any(text.startswith("STALE WWW · ") for text in pills) and not any(text.startswith("STALE · ")
                                                                                     for text in pills)
    finally:
        monkeypatch.undo()
        budgets._g_pills.cache_clear()


# Each status's pill room (budgets.g_pill_least), as probed for #96's review F5: the least of the headers English shows
# that status's widest age in, by the guard's mark beside it.
G_PILL_ROOMS = {
    "floating": {"LIVE": 76.184, "STALE": 76.184, "OFFLINE": 86.184, "PAUSED": 86.184, "RATE LIMITED": 106.184,
                 "NO LOGIN": 106.184, "LOADING": 106.184},
    "tooltip": {"LIVE": 97.184, "STALE": 97.184, "OFFLINE": 97.184, "PAUSED": 97.184, "RATE LIMITED": 107.184,
                "NO LOGIN": 97.184, "LOADING": 127.184},
}


def test_each_status_words_pill_note_is_its_own_room() -> None:
    """#96's review F5: a status word's budget note gives the room its own pill has on each surface (g_pill_least, the
    room allowed and g_pill_room hold that word's pill to), not the box's least: RATE LIMITED is never shown with its
    age beside the holding capsule, so its pill has 30 px more on the floating widget than LIVE's."""
    notes = budgets.notes()
    for surface, rooms in G_PILL_ROOMS.items():
        assert {status: round(room, 3) for status, room in budgets.g_pill_least(surface).items()} == rooms, surface
        box = budgets.BOX[f"gates.{surface}.status_pill"]
        assert box.limit == pytest.approx(min(rooms.values())), surface
        pills = budgets._g_pills(i18n.DEFAULT, surface)
        for status, room in rooms.items():
            note = notes[f"_budget.{budgets._STATUS_KEY[status]}"]
            assert f"{room:g} px for the word and its age where English shows its age (gates.{surface}.status_pill, " \
                   f"drop): past it the age is left out" in note, (surface, status, note)
            assert pills[f"{status} · 59 s"] == pytest.approx(room), (surface, status)


def test_a_sign_in_step_past_its_room_is_reported_as_its_lead_left_out(capsys: pytest.CaptureFixture[str],
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """#96's review F4: beside its lead a sign-in footer's step is never cut (Q3: past its room the lead is left out and
    the step drawn whole from LEFT), so the report says what gives way there, not a cut, and its note says so; the
    skin's _footer_plan draws each such step of the pseudo-locale whole beside the language's widest lead, with no
    lead."""
    import dataclasses

    rows = [row for row in budgets.over(i18n.PSEUDO) if row.box.id in {
        f"gates.floating.{kind}_step" for kind in budgets.G_STEP_KINDS}]
    assert {row.box.id for row in rows} == {f"gates.floating.{kind}_step" for kind in budgets.G_STEP_KINDS}
    notes = budgets.notes()
    ink = _g_ink()
    for row in rows:
        kind = row.box.id.split(".")[2][:-len("_step")]
        assert row.box.gives == "the lead left out; the step drawn whole", row.box.id
        assert notes[f"_budget.{row.box.keys[0]}"].startswith(
            f"{row.box.limit:g} px ({row.box.id}): beside its lead; past it the lead is left out"), row.box.id
        lead = budgets.widest_text(budgets.BOX[f"gates.floating.{kind}_lead"], i18n.PSEUDO, 1.0)
        monkeypatch.setattr(g, "_footer_runs", lambda _model, lead=lead, step=row.text: [(lead, 600, "warn"),
                                                                                         (step, 400, "text_3")])
        plan = g._footer_plan(ink, dataclasses.replace(dump.sample_model("stale"),
                                                       status=budgets.G_STEP_KINDS[kind]))
        assert plan.gave_way == ("lead",) and [run[0] for run in plan.runs] == [row.text.lstrip()], (kind, plan)
    monkeypatch.undo()
    budgets.main(["--lang", i18n.PSEUDO])
    out = capsys.readouterr().out
    for row in rows:
        assert f"\n  gives {row.box.id}: " in out and f"\n  cut   {row.box.id}: " not in out, row.box.id
        assert "(the lead left out; the step drawn whole)" in out


def test_gates_cap_badge_room_is_its_rows_beside_the_languages_words(monkeypatch: pytest.MonkeyPatch) -> None:
    """Critic 5: the cap badge, drawn last where it fits after its row's label and tag, has in a language the least
    room the rows English shows its widest badge in leave it, beside the language's own label, tag and value
    (budgets.g_badge_room; English's widest badge with its digits at the face's widest, as the budgets hold a shape):
    English's is the box's limit, and English leaves the badge out beside a scoped row named for nothing or for its
    surface at a 100 % share (a long scoped name keeps its room); French's spaced share and
    value leave less; and each row's room is the one the skin's _row hands _cap_badge, in English, in French and in
    the pseudo-locale, where a row too tight for its tag leaves it out."""
    import dataclasses

    from widget_words import sweep_locales

    for surface in ("floating", "tooltip"):
        box = budgets.BOX[f"gates.{surface}.cap_badge"]
        rows = budgets.g_badge_rows(surface)
        assert budgets.g_badge_room(surface, i18n.DEFAULT) == pytest.approx(box.limit), surface
        named = {(str(g._row_name(row)), row.share_pct) for row in rows}
        assert ("Fable weekly", 100.0) in named and ("Weekly scoped", 100.0) not in named, surface
        assert ("Claude_code weekly", 100.0) not in named and ("Claude code weekly", None) in named, surface
        assert ("Weekly scoped", None) in named and ("Haiku weekly", None) in named, surface
        assert budgets.g_badge_room(surface, "fr") < box.limit - 10 and budgets.g_badge_room(surface, "tr") == \
            pytest.approx(box.limit)
    seen: list[tuple[str, float]] = []
    monkeypatch.setattr(g, "_cap_badge", lambda _ink, _pal, _model, row, name_end, limit, _top, *, tight:
                        seen.append((row.key, limit - (name_end + g.BADGE_GAP) - 2 * g.BADGE_PAD)))
    theme = dump.load_theme_objects()["gates"]
    skin = dump.get_skin("gates")
    french = next(locale for locale in sweep_locales("fr") if locale.format == "fr-FR")
    for locale in (i18n.ENGLISH, french, i18n.dev_locale(i18n.PSEUDO)):     # the pseudo-locale's tags give way
        for row in budgets._g_badge_rows():
            for surface, percents in (("floating", (100.0,)), ("tooltip", (99.9, 100.0))):
                rooms = []
                for percent in percents:
                    model = dataclasses.replace(dump.sample_model("normal3", locale=locale),
                                                rows=(dataclasses.replace(row, percent=percent),))
                    seen.clear()
                    if surface == "floating":
                        skin.render_floating(model, theme, theme.variants[0], 1.0, None)
                    else:
                        skin.render_tooltip(model, theme, theme.variants[0], 1.0)
                    rooms += [room for key, room in seen if key == row.key]
                assert min(rooms) == pytest.approx(budgets._g_badge_room_in(surface, row, locale)), (
                    surface, row.key, locale.format)


# ── The report ────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_pseudo_locale_gets_a_report_not_a_failure(capsys: pytest.CaptureFixture[str]) -> None:
    rows = budgets.over(i18n.PSEUDO)
    faults = sum(r.box.enforced for r in rows)
    assert faults and any(isinstance(r.box, budgets.Line) for r in rows)      # what #93 to #101 clear
    assert budgets.main(["--lang", i18n.PSEUDO]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"pseudo: {len(rows)} texts past their budget, {faults} of them in boxes or lines")
    assert "\n  FAULT " in out and "\n  cut   " in out


def test_any_language_can_be_reported_and_only_an_offered_one_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    for language in i18n.DEV_LANGUAGES:
        assert budgets.main(["--lang", language]) == 0, language
    monkeypatch.setattr(i18n, "OFFERED", ("en", "es", "tr"))
    assert budgets.main(["--lang", "es"]) == 1                             # what #107 runs before it offers Spanish
    assert budgets.main(["--lang", "tr"]) == 1                             # offered: its compact tiles run past (#91)
    assert budgets.main(["--lang", "en"]) == 0                             # offered, and it fits
    assert budgets.main(["--lang", "fr"]) == 0                             # not offered: a report
