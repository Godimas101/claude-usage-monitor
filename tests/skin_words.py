"""Each skin's own English words, for tests/test_skin_words.py (#94 to #96).

tests/fixtures/skin_words_english.json holds what a skin drew before its own words moved into the catalog, one section
per skin: "nostromo" (#94), "night_city" (#95) and "gates" (#96), each recorded before its skin was edited. A section has
the output of every helper of the skin that words a text, and every run of text each surface draws (the text, where it
is set, its face, size, tracking and alignment, the pixel it lands on and its advance) at 1.0 and 1.25. The test holds
the skin to it byte for byte. Reset times and resume clocks are local time, so the words are worked out in a fresh Python with the time
zone pinned to UTC (``collect``), as tests/widget_words.py does.

The states: every dump state in every show_scoped mode; tests/widget_words.py's edge states (the refused sign-in, the
scoped limits named every way, a kind with no words, a limit gone from the reading) and guard states (each countdown
shape, on a live and on a rate-limited reading, with no resume time); and the states a skin's own words hang on. For
Nostromo: each sign-in reason on three rows, ages of 59 s, 59 min, 47 h and 100 d on a live and on a rate-limited
reading, a pace that is unknown, on pace, 100 points ahead and 100 points behind, and a guard armed with no caps. For
Night City: Nostromo's, and the same ages on a stale and on an offline reading (its band shows an age on the stale,
offline and rate-limited ones), the guard holding over a stale and an offline reading with a resume time and without
(the band's hold chip), and the guard armed while paused and while loading (the header's status chip beside the GUARD
chip). Night City's variants place their runs by look (night_city.Look), so its section has Blackwall's runs at 1.0 and
1.25, Relic's and Edgerun's at 1.0 (Mox is Relic's look), and Blackwall's and Edgerun's floating widget and strip with
transparent_bg at 1.0 (Edgerun drops its plate there). For Gates: Night City's, a missing sign-in that keeps its reading
while the guard holds, and the two guard holds whose resume English's footer cuts, each at a clock of its own. Gates
queues its text and draws it on the finished frame through a glyph cache, so its runs are read where it places them
(gates_runs); its variants place their runs alike, so its section has system-dark's.

    python tests/skin_words.py --print SKIN      a skin's words, from the code this Python imports, as JSON
    python tests/skin_words.py --record SKIN     write a skin's section, worked out in a fresh Python; refused when the
                                                 record has it already (English must not change)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import widget_words  # noqa: E402

RECORD = HERE / "fixtures" / "skin_words_english.json"
PINNED_TZ = widget_words.PINNED_TZ
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SCALES = (1.0, 1.25)
TRAY_SIZE = 32                  # the one tray icon that draws text (its number)
ABOUT = ("Each skin's own English words, recorded before its words moved into the catalog (#94 to #96), by "
         "tests/skin_words.py in a fresh Python with TZ=UTC0: every helper that words a text, and every run each "
         "surface draws at 1.0 and 1.25. tests/test_skin_words.py holds the code to these bytes. A section is written "
         "once, before its skin is edited, and never again: English must not change.")


# ── States ────────────────────────────────────────────────────────────────────

def _limit(kind: str, percent: float, at: Any, label: str, **extra: Any) -> Any:
    return widget_words._limit(kind, percent, at, label, **extra)


# An age in each unit at its widest (model.age_unit), by its shape's name.
AGES: dict[str, int] = {"59s": 59, "59m": 59 * 60 + 59, "47h": 47 * 3600 + 59 * 60, "100d": 100 * 86400}


def _nostromo_states() -> dict[str, Any]:
    """The states Nostromo's own words hang on, beyond the dump's and widget_words': each sign-in reason, the age in
    each unit at its widest, each pace, and a guard armed with no caps (its lead alone)."""
    from usage_monitor.data.models import ApiStatus
    from usage_monitor.render import dump

    now = dump.SAMPLE_NOW
    states: dict[str, Any] = {}
    for reason, refused in (("expired", None), ("refused", 403), ("expired_401", 401)):
        states[f"reason/{reason}"] = replace(dump.sample_state("stale3"), signin_refused=refused)
    states["reason/no_login"] = dump.sample_state("no_credentials")
    base = [_limit("session", 40.0, now + timedelta(hours=3, minutes=27), "SESSION"),
            _limit("weekly_all", 20.0, now + timedelta(days=3), "WEEKLY")]
    for status in (ApiStatus.LIVE, ApiStatus.RATE_LIMITED):
        for shape, seconds in AGES.items():
            states[f"age/{status.name.lower()}/{shape}"] = dump._state(now, status, base, seconds)
    session = 18000
    paces = {
        "none": _limit("session", 40.0, None, "SESSION"),                                    # no reset time: no pace
        "on": _limit("session", 50.0, now + timedelta(seconds=session // 2), "SESSION"),
        "ahead_100": _limit("session", 100.0, now + timedelta(seconds=session), "SESSION"),  # nothing elapsed
        "behind_100": _limit("session", 0.0, now, "SESSION"),                                # all of it elapsed
    }
    for shape, limit in paces.items():
        states[f"pace/{shape}"] = dump._state(now, ApiStatus.LIVE, [limit, base[1]], 12)
    armed = dump._state(now, ApiStatus.LIVE, base, 12)
    states["guard_armed_no_caps"] = replace(armed, guard={"state": "armed", "policy": {"enabled": True}})
    return states


def _night_city_states() -> dict[str, Any]:
    """The states Night City's own words hang on, beyond the dump's and widget_words': Nostromo's (each sign-in reason,
    each age shape on a live and a rate-limited reading, each pace, a guard armed with no caps), each age shape on a
    stale and an offline reading too (the band's age), the guard holding over a stale and an offline reading with a
    resume time and without (the band's hold chip), and the guard armed while paused and while loading (the header's
    status chip beside the GUARD chip, which a short room leaves out)."""
    from usage_monitor.data.models import ApiStatus
    from usage_monitor.render import dump

    now = dump.SAMPLE_NOW
    states = _nostromo_states()
    base = [_limit("session", 40.0, now + timedelta(hours=3, minutes=27), "SESSION"),
            _limit("weekly_all", 20.0, now + timedelta(days=3), "WEEKLY")]
    for status in (ApiStatus.STALE, ApiStatus.OFFLINE):
        for shape, seconds in AGES.items():
            states[f"age/{status.name.lower()}/{shape}"] = dump._state(now, status, base, seconds)
    held = dump.sample_state("guard_hold_session").guard
    for name in ("stale3", "offline3"):
        source = dump.sample_state(name)
        states[f"band_hold/{name}"] = replace(source, guard=held)
        states[f"band_hold/{name}/no_resume_time"] = replace(source, guard={**held, "resume_at": None})
    armed = dump.sample_state("guard_armed").guard
    states["guard_armed_paused"] = replace(dump.sample_state("paused3"), guard=armed)
    states["guard_armed_loading"] = replace(dump._state(now, ApiStatus.LOADING, None, 0), guard=armed)
    return states


# The two guard holds whose resume English's footer cuts ("Guard holding · Resumes Mon 10:00 AM (in 20h 20m)" is 260.3 px
# of 258, budgets.KNOWN_ENGLISH_OVERRUNS): the state's name, its clock and the resume time, in UTC.
GATES_WIDE_RESUMES: tuple[tuple[str, datetime, datetime], ...] = (
    ("guard_hold/resume_mon_10am", datetime(2026, 10, 25, 13, 40, tzinfo=timezone.utc),
     datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)),
    ("guard_hold/resume_wed_1259pm", datetime(2026, 10, 20, 13, 0, tzinfo=timezone.utc),
     datetime(2026, 10, 21, 12, 59, tzinfo=timezone.utc)),
)


def _gates_states() -> dict[str, Any]:
    """The states Gates' own words hang on, beyond the dump's and widget_words': Night City's (each sign-in reason, each
    age shape, each pace, the guard armed with no caps, holding over a stale and an offline reading, armed while paused
    and while loading), a missing sign-in that keeps its reading while the guard holds (NO LOGIN beside the holding
    capsule, the no-login footer), and the two guard holds whose resume English's footer cuts (GATES_WIDE_RESUMES), each
    a (state, now) pair: the sample clock never gives them."""
    from usage_monitor.data.models import ApiStatus
    from usage_monitor.render import dump

    states: dict[str, Any] = dict(_night_city_states())
    states["guard_hold/no_credentials"] = replace(dump.sample_state("guard_hold_session"), status=ApiStatus.NO_CREDENTIALS)
    for name, now, resume in GATES_WIDE_RESUMES:
        state = dump.sample_state("guard_hold_session", now)
        at = resume.isoformat().replace("+00:00", "Z")
        states[name] = (replace(state, guard={**state.guard, "resume_at": at}), now)
    return states


def models(extra: Callable[[], dict[str, Any]], locale: Any = None) -> dict[str, Any]:
    """Every state the record covers, by name, as its view model in ``locale`` (English when None): the dump's in
    every show_scoped mode that gives it other rows than the modes before it, then widget_words' edge and guard states
    and ``extra``'s, with every scoped row shown. A state given as a (state, now) pair is built at that clock, any other
    at the dump's."""
    from usage_monitor.i18n import ENGLISH
    from usage_monitor.render import dump
    from usage_monitor.render.model import SHOW_SCOPED_MODES, build_model

    locale = ENGLISH if locale is None else locale
    found: dict[str, Any] = {}
    for name in dump.DUMP_STATE_NAMES:
        shown: list[Any] = []
        for mode in SHOW_SCOPED_MODES:
            model = dump.sample_model(name, show_scoped=mode, locale=locale)
            if model not in shown:              # a mode that shows the same rows draws the same frames
                shown.append(model)
                found[f"{name}/{mode}"] = model
    for group in (widget_words._edge_states(), widget_words._guard_states(), extra()):
        for name, given in group.items():
            state, now = given if isinstance(given, tuple) else (given, dump.SAMPLE_NOW)
            found[name] = build_model(state, now, show_scoped="always", locale=locale)
    return found


# ── What a skin draws ─────────────────────────────────────────────────────────

def runs(skin: Any, model: Any, theme: Any, variant: Any, heights: tuple[int, ...],
         scales: tuple[float, ...] = SCALES, *, opts: Any = None) -> dict[str, list[list[Any]] | str]:
    """Every run of text each surface draws, in drawing order, at each of ``scales`` (the strip at each taskbar height
    of ``heights``, the tray icon at TRAY_SIZE): (text, x, baseline, face file without its .ttf, size, tracking, align,
    the layer's pixel x and y, advance) for a canvas run, (text, x, y, size, anchor) for a tray number. A surface that
    draws exactly what an earlier one of the state drew names it instead (the strip on a taskbar tall enough for
    it). With ``opts`` (RenderOptions) only the surfaces that take them are drawn, the floating widget and the strip."""
    from PIL import ImageDraw

    from usage_monitor.render import engine

    found: list[list[Any]] = []
    real_layer, real_draw = engine.Canvas.text_layer, ImageDraw.ImageDraw.text

    def layer(self: Any, text: str, x: float, baseline: float, font: Any, tracking: float = 0.0,
              align: str = "left") -> Any:
        out = real_layer(self, text, x, baseline, font, tracking, align)
        found.append([str(text), round(x, 2), round(baseline, 2), Path(str(getattr(font, "path", ""))).stem,
                      getattr(font, "size", None), tracking, align, out[0].x, out[0].y, round(out[1], 2)])
        return out

    def draw(self: Any, xy: Any, text: Any, *args: Any, **kwargs: Any) -> Any:
        if tray[0]:                 # elsewhere Pillow draws a canvas run's glyphs, once per glyph the cache lacks
            font = kwargs.get("font")
            found.append([str(text), *(round(float(v), 2) for v in xy), getattr(font, "size", None),
                          kwargs.get("anchor")])
        return real_draw(self, xy, text, *args, **kwargs)

    def take(surface: str, draw_it: Callable[[], Any]) -> None:
        found.clear()
        draw_it()
        same = next((name for name, earlier in drawn.items() if earlier == found), None)
        drawn[surface] = same if same is not None else list(found)

    drawn: dict[str, list[list[Any]] | str] = {}
    tray = [False]
    engine.Canvas.text_layer, ImageDraw.ImageDraw.text = layer, draw
    try:
        for scale in scales:
            tag = f"{scale:g}"
            take(f"floating@{tag}", lambda: skin.render_floating(model, theme, variant, scale, opts))
            if opts is None:
                take(f"tooltip@{tag}", lambda: skin.render_tooltip(model, theme, variant, scale))
            for height in heights:
                take(f"strip/h{round(height * scale)}@{tag}",
                     lambda: skin.render_taskbar(model, theme, variant, round(height * scale), scale, opts))
        if opts is None:
            tray[0] = True
            take(f"tray/{TRAY_SIZE}", lambda: skin.render_tray_icon(model, theme, variant, TRAY_SIZE))
    finally:
        engine.Canvas.text_layer, ImageDraw.ImageDraw.text = real_layer, real_draw
    return drawn


def gates_runs(skin: Any, model: Any, theme: Any, variant: Any, heights: tuple[int, ...],
               scales: tuple[float, ...] = SCALES, *, opts: Any = None) -> dict[str, list[list[Any]] | str]:
    """runs() for Gates, which queues its text (Ink.put) and draws it on the finished frame at output size
    (Ink._draw_text, through the cached _run and _blend): every run in drawing order as (text, x, baseline, face file
    without its .ttf, size in output px, weight, optical size, align, coverage gamma, the fraction of a pixel it starts
    at, the pixel x and y of its mask, advance); the tray number as one entry per run (text, face file, size, weight,
    optical size, gamma, fraction, and the pixel each of its masks lands on: the keyline's, then the ink's). Each run is
    read where Ink._draw_text and _tray_number place it, never from Pillow's draw calls, which _run makes only when its
    cache misses. The surfaces, their names and ``opts`` as runs()."""
    from usage_monitor.render.skins import gates

    found: list[list[Any]] = []
    calls: list[tuple[Any, ...]] = []
    real_draw, real_tray, real_run, real_blend = gates.Ink._draw_text, gates._tray_number, gates._run, gates._blend

    def run(face: Any, text: str, gamma: float, frac: float) -> Any:
        calls.append(("run", face, text, gamma, frac))
        return real_run(face, text, gamma, frac)

    def blend(image: Any, mask: Any, x: int, y: int, color: Any) -> None:
        calls.append(("blend", x, y))
        real_blend(image, mask, x, y, color)

    def heard(draw_it: Callable[[], Any]) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
        calls.clear()
        gates._run, gates._blend = run, blend
        try:
            draw_it()
        finally:
            gates._run, gates._blend = real_run, real_blend
        return [call for call in calls if call[0] == "run"], [call for call in calls if call[0] == "blend"]

    def face_of(face: Any) -> list[Any]:
        return [Path(str(face.path or "")).stem, face.size, face.wght, face.opsz]

    def draw_text(self: Any, out: Any) -> None:
        queued = list(self._texts)
        made, placed = heard(lambda: real_draw(self, out))
        assert len(made) == len(placed) == len(queued), (len(made), len(placed), len(queued))
        for (_text, x, baseline, _face, _color, align), (_, face, text, gamma, frac), (_, px, py) in zip(
                queued, made, placed):
            found.append([str(text), round(x, 2), round(baseline, 2), *face_of(face), align, round(gamma, 6), frac,
                          px, py, round(self.advance(text, face), 2)])

    def tray(icon: Any, size: int, percent: Any, pal: Any, theme_: Any) -> None:
        made, placed = heard(lambda: real_tray(icon, size, percent, pal, theme_))
        for _, face, text, gamma, frac in made:
            found.append([str(text), *face_of(face), round(gamma, 6), frac, [[px, py] for _, px, py in placed]])

    def take(surface: str, draw_it: Callable[[], Any]) -> None:
        found.clear()
        draw_it()
        same = next((name for name, earlier in drawn.items() if earlier == found), None)
        drawn[surface] = same if same is not None else list(found)

    drawn: dict[str, list[list[Any]] | str] = {}
    gates.Ink._draw_text, gates._tray_number = draw_text, tray
    try:
        for scale in scales:
            tag = f"{scale:g}"
            take(f"floating@{tag}", lambda: skin.render_floating(model, theme, variant, scale, opts))
            if opts is None:
                take(f"tooltip@{tag}", lambda: skin.render_tooltip(model, theme, variant, scale))
            for height in heights:
                take(f"strip/h{round(height * scale)}@{tag}",
                     lambda: skin.render_taskbar(model, theme, variant, round(height * scale), scale, opts))
        if opts is None:
            take(f"tray/{TRAY_SIZE}", lambda: skin.render_tray_icon(model, theme, variant, TRAY_SIZE))
    finally:
        gates.Ink._draw_text, gates._tray_number = real_draw, real_tray
    return drawn


def _nostromo_helpers(model: Any, theme: Any, variant: Any) -> dict[str, Any]:
    """What each of Nostromo's wording helpers gives for ``model``. The reset text and the strip's window tags have no
    helper of their own: they are in the runs."""
    from usage_monitor.render.skins import nostromo as nos

    pal = nos._palette(theme, variant, model)
    rows = []
    for row in model.rows:
        text, colour = nos._pace_text(pal, row)
        rows.append({"key": row.key, "names": list(nos._row_names(row)),
                     "qualifier": nos._qualifier(row, model.locale),
                     "short_qualifier": nos._short_qualifier(row, model.locale),
                     "cap": nos._cap_text(model, row), "pace": [text, list(colour)]})
    statement = nos._guard_statement(model)
    lines: dict[str, Any] = {}
    for scale in SCALES:
        lead, wrapped = nos._guard_lines(nos._canvas(nos.FLOAT_W, 1, scale, theme), model)
        lines[f"{scale:g}"] = {"lead": lead, "lines": wrapped,
                               "split": list(nos._split_lead(wrapped[0], lead)) if wrapped else None}
    message = nos._message_lines(model)
    return {"message": list(message) if message is not None else None, "state_word": list(nos._state_word(model)),
            "age": nos._age_text(model), "hold": nos._hold_word(model),
            "statement": list(statement) if statement is not None else None, "guard_lines": lines, "rows": rows}


def _nostromo() -> dict[str, Any]:
    from usage_monitor.render import dump
    from usage_monitor.render.skins import get_skin

    theme = dump.load_theme_objects()["nostromo"]
    skin, variant = get_skin("nostromo"), theme.variants[0]
    found: dict[str, Any] = {}
    for name, model in models(_nostromo_states).items():
        found[name] = {"helpers": _nostromo_helpers(model, theme, variant),
                       "runs": runs(skin, model, theme, variant, dump.TASKBAR_HEIGHTS_AT_1X)}
    return found


def _night_city_helpers(model: Any, theme: Any, variant: Any) -> dict[str, Any]:
    """What each of Night City's wording helpers gives for ``model``: per row its name, share tag, critical chip's word,
    reset text on the widget and on the hover card, and pace with its kind; the band's cause and age, the hold chip's
    word, the guard's statement and its lines at 1.0 and 1.25 (measured as render_tooltip measures them). The header's
    LIVE tag, the GUARD chip, the WARN chip and the strip's words have no helper of their own: they are in the runs."""
    from usage_monitor.render.skins import RenderOptions
    from usage_monitor.render.skins import night_city as nc

    rows = [{"key": row.key, "name": nc._row_name(row), "share": nc._share_tag(row, model.locale),
             "chip": nc._chip_word(model, row), "reset": nc._reset_text(row, False),
             "reset_tooltip": nc._reset_text(row, True), "pace": list(nc._pace_text(row))} for row in model.rows]
    opts = RenderOptions()
    pal = nc._palette(theme, variant, model, opts)
    lines: dict[str, Any] = {}
    for scale in SCALES:
        lead, wrapped = nc._guard_lines(nc._Ink(nc._canvas(1, 1, scale, theme), pal, opts), model)
        lines[f"{scale:g}"] = {"lead": lead, "lines": wrapped}
    statement = nc._guard_statement(model)
    return {"band": nc._band_text(theme, model), "age": nc._age_tag(model), "hold": nc._hold_word(model),
            "statement": list(statement) if statement is not None else None, "guard_lines": lines, "rows": rows}


# Night City's looks besides Blackwall's (its first variant): their runs at 1.0. Mox is Relic's look (night_city.Look).
NIGHT_CITY_LOOKS = ("relic", "edgerun")
# The variants drawn with transparent_bg at 1.0: Blackwall's boxes stay, Edgerun's plate goes (night_city._palette).
NIGHT_CITY_CLEAR = ("blackwall", "edgerun")


def _night_city() -> dict[str, Any]:
    from usage_monitor.render import dump
    from usage_monitor.render.skins import RenderOptions, get_skin

    theme = dump.load_theme_objects()["night_city"]
    skin, first = get_skin("night_city"), theme.variants[0]
    heights = dump.TASKBAR_HEIGHTS_AT_1X
    clear = RenderOptions(transparent_bg=True)
    found: dict[str, Any] = {}
    for name, model in models(_night_city_states).items():
        found[name] = {
            "helpers": _night_city_helpers(model, theme, first),
            "runs": runs(skin, model, theme, first, heights),
            "looks": {look: runs(skin, model, theme, theme.variant(look), heights, scales=(1.0,))
                      for look in NIGHT_CITY_LOOKS},
            "clear": {variant: runs(skin, model, theme, theme.variant(variant), heights, scales=(1.0,), opts=clear)
                      for variant in NIGHT_CITY_CLEAR},
        }
    return found


def _gates_helpers(model: Any, theme: Any) -> dict[str, Any]:
    """What each of Gates' wording helpers gives for ``model``: the pill's age; per row its name, tag, and the hover
    card's pace with its colour role; the footer's runs and the guard's hold runs (text, weight, colour role); the
    guard's countdown, the name it gives each limit it watches or holds on, its statement and the hover card's guard bar
    at 1.0 and 1.25 (measured as render_tooltip measures it). The title, the cap badge, the hover card's reset and the
    strip's tags have no helper of their own: they are in the runs."""
    from usage_monitor.render.skins import gates

    guard = model.guard
    rows = [{"key": row.key, "name": gates._row_name(row), "tag": gates._tag(row, model.locale),
             "pace": list(gates._pace_text(row))} for row in model.rows]
    names = (None if guard is None else
             {key: gates._guard_name(guard, key) for key in dict.fromkeys([*guard.caps, *guard.over_keys])})
    countdown = (gates._countdown(guard.resume_seconds)
                 if guard is not None and guard.resume_seconds is not None else None)
    lines: dict[str, Any] = {}
    for scale in SCALES:
        lead, wrapped = gates._guard_bar_lines(gates._card_canvas(1, scale, theme), model)
        lines[f"{scale:g}"] = {"lead": lead, "lines": wrapped}
    statement = gates._guard_statement(model)
    return {"age": gates._age_label(model), "footer": [list(run) for run in gates._footer_runs(model)],
            "hold": [list(run) for run in gates._hold_runs(model)], "countdown": countdown, "names": names,
            "statement": list(statement) if statement is not None else None, "guard_lines": lines, "rows": rows}


def _gates() -> dict[str, Any]:
    from usage_monitor.render import dump
    from usage_monitor.render.skins import get_skin

    theme = dump.load_theme_objects()["gates"]
    skin, variant = get_skin("gates"), theme.variants[0]            # system-dark: the variants place their runs alike
    found: dict[str, Any] = {}
    for name, model in models(_gates_states).items():
        found[name] = {"helpers": _gates_helpers(model, theme),
                       "runs": gates_runs(skin, model, theme, variant, dump.TASKBAR_HEIGHTS_AT_1X)}
    return found


# The skins with a section, and what works their words out.
SKINS: dict[str, Callable[[], dict[str, Any]]] = {"nostromo": _nostromo, "night_city": _night_city, "gates": _gates}


# ── Running ───────────────────────────────────────────────────────────────────

def collect(skin: str, repo: str | Path = REPO) -> dict[str, Any]:
    """``skin``'s words in the code in ``repo``, worked out in a fresh Python with the time zone pinned. Raises when
    that Python imported usage_monitor from anywhere else."""
    root = Path(repo).resolve()
    done = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--print", skin], cwd=root,
                          env={**os.environ, "TZ": PINNED_TZ, "PYTHONPATH": str(root)}, capture_output=True,
                          encoding="utf-8", timeout=900, check=False, creationflags=NO_WINDOW)
    if done.returncode:
        raise RuntimeError(f"{skin}'s words could not be worked out:\n{done.stderr[-3000:]}")
    result = json.loads(done.stdout)
    source = Path(result["source"]).resolve()
    if root not in source.parents:
        raise RuntimeError(f"usage_monitor came from {source}, not from {root}")
    return result["words"]


def recorded() -> dict[str, Any]:
    return json.loads(RECORD.read_text(encoding="utf-8"))["skins"] if RECORD.exists() else {}


def record(skin: str) -> int:
    """Write ``skin``'s section from a fresh Python, one line per state. Refused (exit 1) when the record has it."""
    skins = recorded()
    if skin in skins:
        print(f"{RECORD.name} has {skin} already: English must not change, so it is never recorded again")
        return 1
    skins[skin] = collect(skin)
    lines = ["{", f' "_about": {json.dumps(ABOUT, ensure_ascii=False)},', ' "skins": {']
    for index, (name, states) in enumerate(sorted(skins.items())):
        lines.append(f"  {json.dumps(name)}: {{")
        items = list(states.items())
        for at, (state, words) in enumerate(items):
            comma = "," if at < len(items) - 1 else ""
            lines.append(f"   {json.dumps(state, ensure_ascii=False)}: "
                         f"{json.dumps(words, ensure_ascii=False, separators=(',', ':'))}{comma}")
        lines.append("  }" + ("," if index < len(skins) - 1 else ""))
    lines += [" }", "}"]
    RECORD.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    assert recorded() == skins
    print(f"wrote {skin}: {len(skins[skin])} states to {RECORD}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "--print" and argv[1] in SKINS:
        import usage_monitor

        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        print(json.dumps({"source": usage_monitor.__file__, "words": SKINS[argv[1]]()}, ensure_ascii=False))
        return 0
    if len(argv) == 2 and argv[0] == "--record" and argv[1] in SKINS:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        return record(argv[1])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
