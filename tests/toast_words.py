"""The English words of the tray's toasts and the usage alerts, for tests/test_toast_words.py (#97).

tests/fixtures/toast_words_english.json holds what they said before #97 moved their words into the catalog, and the
test holds the code to it byte for byte. Reset times are local time, so the words are worked out in a fresh Python with
the time zone pinned to UTC (``collect``), as tests/widget_words.py does.

What is recorded, all of it in English, through the code that shows it:
- the release toast, by MonitorApp._announce_update on a stand-in app (UPDATE_VERSIONS);
- the note toast after a silent update, by MonitorApp._announce_update_note on a stand-in app whose app data folder is
  a temporary one: for each count pair of NOTE_COUNTS, the note as installer.iss's EndedNote words it (its four
  sentence forms read from installer-messages/en.isl since #103), and a note with a line break, a long one, an empty
  one, one with a byte order mark and one in another code page, with whether the file was deleted after;
- the alerts, by AlertEngine.evaluate over scripted readings for each name shape (SHAPES): the threshold ladder, a
  reset by a change of window, a threshold in the new window and a reset when that window runs out by the clock; and
  the credits alert for each SPENDS shape; the session's ladder at the alert levels of THRESHOLDS;
- alerts._name for every shape, alerts._reset_text for the session and the other limits at RESET_HOURS and
  RESET_MINUTES on every weekday (local time), alerts._credits_text for every SPENDS shape.

Also here: ``toast_sweep``, every toast a language can show, by the budget box that measures it, for the width budgets
(tests/budgets.py) and the English scan (tests/english_left.py).

    python tests/toast_words.py --print       the words of the code this Python imports, as JSON
    python tests/toast_words.py --record      write the record, worked out in a fresh Python; refused when it exists
                                              (English must not change)
"""
from __future__ import annotations

import functools
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
RECORD = HERE / "fixtures" / "toast_words_english.json"
EN_ISL = REPO / "installer-messages" / "en.isl"
NOTE_MESSAGES = ("EndedToolsOne", "EndedToolsOther", "EndedWaitsOne", "EndedWaitsOther")   # EndedNote's, in order
PINNED_TZ = "UTC0"              # the C runtime reads TZ when Python starts, so it is set for a fresh process
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ABOUT = ("Today's English toasts and usage alerts, recorded before #97 moved their words into the catalog (step a1 of "
         "its plan, at 69d82e1 with TZ=UTC0, by tests/toast_words.py): the release toast, the note after a silent "
         "update for each count pair and for an older Setup's prose, every alert the engine raises for each name shape "
         "and credits shape, and the alerts' name, reset and credits texts. tests/test_toast_words.py holds the code "
         "to these bytes. Never re-recorded: English must not change.")

UPDATE_VERSIONS = ("2.1.0", "2.4.0", "3.0.0-rc.1", "3.10.12-rc.1", "10.0.0")
NOTE_COUNTS = (0, 1, 2, 12, 9999)
ALERT_NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)   # a Thursday
RESET_MONDAY = datetime(2026, 7, 13)                            # a Monday; its local times are the zone's (pinned)
RESET_HOURS = (0, 1, 9, 10, 11, 12, 13, 21, 22, 23)
RESET_MINUTES = (0, 59)

# Every way a limit can be named: (kind, label, scope model, scope surface).
SHAPES: dict[str, tuple[str, str, str | None, str | None]] = {
    "session": ("session", "SESSION", None, None),
    "session_unlabelled": ("session", "", None, None),
    "weekly": ("weekly_all", "WEEKLY", None, None),
    "weekly_unlabelled": ("weekly_all", "", None, None),
    "scoped_model": ("weekly_scoped", "FABLE WEEKLY", "Fable", None),
    "scoped_two_words": ("weekly_scoped", "CLAUDE INSTANT WEEKLY", "Claude Instant", None),
    "scoped_digits": ("weekly_scoped", "CLAUDE OPUS 5 WEEKLY", "Claude Opus 5", None),
    "scoped_long": ("weekly_scoped", "CLAUDE OPUS 4.5 THINKING WEEKLY", "Claude Opus 4.5 Thinking", None),
    "scoped_model_and_surface": ("weekly_scoped", "FABLE WEEKLY", "Fable", "chat"),
    "scoped_surface_only": ("weekly_scoped", "CLAUDE_CODE WEEKLY", None, "claude_code"),
    "scoped_surface_spaced": ("weekly_scoped", "CLAUDE CODE WEEKLY", None, "Claude Code"),
    "scoped_neither": ("weekly_scoped", "WEEKLY SCOPED", None, None),
    "scoped_dotted_i": ("weekly_scoped", "ISTANBUL WEEKLY", "istanbul", None),
    "scoped_capital_dotted_i": ("weekly_scoped", "İZMIR WEEKLY", "İzmir", None),
    "scoped_no_capital": ("weekly_scoped", "ĸRA WEEKLY", "ĸra", None),
    "scoped_model_unlabelled": ("weekly_scoped", "", "Fable", None),
    "scoped_spaced_unlabelled": ("weekly_scoped", "", " Spaced  Out ", None),
    "scoped_surface_unlabelled": ("weekly_scoped", "", None, "claude_code"),
    "scoped_neither_unlabelled": ("weekly_scoped", "", None, None),
    "scoped_blank_unlabelled": ("weekly_scoped", "", "  ", None),
    "no_kind": ("", "SCOPED", None, None),
    "unknown_kind": ("design_weekly", "DESIGN WEEKLY", None, None),
    "unknown_kind_unlabelled": ("design_weekly", "", None, None),
    "unknown_kind_unlabelled_model": ("design_weekly", "", "Design", None),
}
# Usage credits: (used, limit, currency).
SPENDS: dict[str, tuple[float, float | None, str | None]] = {
    "usd_of_limit": (12.0, 50.0, "USD"),
    "usd_large_of_limit": (1234.56, 10000.0, "USD"),
    "usd_no_limit": (1234.56, None, "USD"),
    "no_currency_of_limit": (1234.56, 10000.0, None),
    "no_currency_no_limit": (3.5, None, None),
    "euro_no_limit": (5.96, None, "EUR"),
    "euro_of_limit": (5.96, 100.0, "EUR"),
    "millions": (1234567.891, 9999999.0, "USD"),
    "lower_case_code": (2.5, 50.0, "usd"),
    "no_limit_figure": (3.0, float("nan"), "USD"),
    "zero": (0.0, 0.0, "USD"),
}
# Alert levels other than the default (80, 90, 100), for the threshold ladder of a session limit.
THRESHOLDS: tuple[tuple[int, ...], ...] = ((1, 33, 99), (50, 100))


# ── installer.iss's EndedNote ─────────────────────────────────────────────────

def ended_note_forms(text: str | None = None) -> tuple[str, str, str, str]:
    """EndedNote's four sentence forms, its messages in installer-messages/en.isl (``text``: that file's text) since
    #103: a tool, N tools, a wait, N waits, each count's %1 read as %d (EndedNote's Format wrote %d before #103, and the
    record's notes were made from those)."""
    from installer_words import custom_messages

    messages = custom_messages(EN_ISL.read_text(encoding="utf-8") if text is None else text)
    missing = [name for name in NOTE_MESSAGES if name not in messages]
    if missing:
        raise RuntimeError(f"en.isl lacks EndedNote's messages {missing}")
    return tuple(messages[name].replace("%1", "%d") for name in NOTE_MESSAGES)  # type: ignore[return-value]


def ended_note(tools: int, waits: int, forms: tuple[str, str, str, str] | None = None) -> str:
    """What EndedNote returns for these counts (its logic, with its own sentences): the tools' sentence, then the
    waits', a space between; "" when Setup ended neither."""
    tool_one, tool_many, wait_one, wait_many = forms or ended_note_forms()
    text = ""
    if tools == 1:
        text = tool_one
    elif tools > 1:
        text = tool_many.replace("%d", str(tools))
    if waits > 0:
        if text:
            text += " "
        text += wait_one if waits == 1 else wait_many.replace("%d", str(waits))
    return text


# ── Stand-ins for the app's parts the toasts use ──────────────────────────────

class _Tray:
    def __init__(self) -> None:
        self.notices: list[list[str]] = []

    def notify(self, title: str, body: str) -> bool:
        self.notices.append([title, body])
        return True


class _Tracker:
    """UpdateTracker as _announce_update uses it: the release to announce, until it is marked announced."""

    def __init__(self, version: str) -> None:
        self.info = {"version": version, "url": "https://github.com/x/y/releases/tag/v" + version}
        self.told: list[Any] = []

    def announcement(self) -> dict[str, Any] | None:
        return None if self.told else self.info

    def mark_announced(self, info: Any = None) -> None:
        self.told.append(info)


class _App:
    """What MonitorApp._announce_update and _announce_update_note read of the app: these attributes only (a method
    that reads or sets any other fails here)."""

    __slots__ = ("tray", "updates", "locale", "_torn_down", "_update_note_done")

    def __init__(self, locale: Any, updates: Any = None) -> None:
        self.tray = _Tray()
        self.updates = updates
        self.locale = lambda: locale
        self._torn_down = False
        self._update_note_done = False

    def _toast_held_back(self) -> bool:
        return False


def release_toasts(locale: Any, version: str) -> list[list[str]]:
    """The toasts _announce_update puts up for a release of ``version``, the app's locale being ``locale``."""
    from usage_monitor import app

    stand_in = _App(locale, _Tracker(version))
    app.MonitorApp._announce_update(stand_in)  # type: ignore[arg-type]
    return stand_in.tray.notices


def note_toasts(locale: Any, note: bytes | None) -> dict[str, Any]:
    """The toasts _announce_update_note puts up for a note file of these bytes (None: no file), the app's locale being
    ``locale``, and whether the file is still there after. The app data folder is a temporary one."""
    from usage_monitor import app, paths

    with tempfile.TemporaryDirectory(prefix="toast-words-") as folder:
        before = os.environ.get(paths.APP_DATA_OVERRIDE_ENV)
        os.environ[paths.APP_DATA_OVERRIDE_ENV] = folder
        try:
            assert paths.app_data_dir() == Path(folder)
            path = Path(folder) / app.UPDATE_NOTE_FILE
            if note is not None:
                path.write_bytes(note)
            stand_in = _App(locale)
            app.MonitorApp._announce_update_note(stand_in)  # type: ignore[arg-type]
            return {"toasts": stand_in.tray.notices, "kept": path.exists()}
        finally:
            if before is None:
                os.environ.pop(paths.APP_DATA_OVERRIDE_ENV, None)
            else:
                os.environ[paths.APP_DATA_OVERRIDE_ENV] = before


# Older Setups' prose notes besides EndedNote's own (a note that does not start with "{" is shown as it is).
PROSE_NOTES: dict[str, bytes] = {
    "line_break": ("Setup closed the monitor's tool in a Claude Code session.\r\nIf that session shows an error, type "
                   "/mcp there or restart it.").encode("ascii"),
    "long": (" ".join(["A session waiting on the usage guard was let go early."] * 6)).encode("ascii"),
    "empty": b"  \r\n",
    "byte_order_mark": b"\xef\xbb\xbfA session waiting on the usage guard was let go early.",
    "other_code_page": "Une session a été libérée.".encode("cp1252"),
}


# ── The alerts ────────────────────────────────────────────────────────────────

def limit(shape: str | tuple[str, str, str | None, str | None], percent: float, resets_at: datetime) -> Any:
    """A limit of ``shape``: a SHAPES name, or (kind, label, scope model, scope surface)."""
    from usage_monitor.data.models import Limit

    kind, label, model, surface = SHAPES[shape] if isinstance(shape, str) else shape
    group = "session" if kind == "session" else "weekly"
    return Limit(kind=kind, group=group, percent=percent, severity=None, resets_at=resets_at, is_active=True,
                 label=label, scope_model=model, scope_surface=surface)


def _said(alerts: list[Any]) -> list[list[str]]:
    return [[alert.kind, alert.severity, alert.key, alert.title, alert.message] for alert in alerts]


def alert_steps(shape: str | tuple[str, str, str | None, str | None], locale: Any,
                settings: dict[str, Any] | None = None) -> list[list[list[str]]]:
    """The alerts an engine reading ``locale`` raises for one limit of ``shape``, reading by reading (UTC): a baseline
    at 10 %, the ladder to 80, 90 and 100 %, a new window at 20 % (its reset), 85 % in it, and that window run out
    by the clock (its reset). ``settings`` are the alert settings (the defaults when None)."""
    from usage_monitor.data.alerts import AlertEngine
    from usage_monitor.data.models import ApiStatus
    from usage_monitor.render import dump

    engine = AlertEngine(dict(settings or {}), tz=timezone.utc, locale_provider=lambda: locale)
    first = ALERT_NOW + timedelta(hours=3)
    second = first + timedelta(hours=5)
    steps = [(0, 10.0, first), (1, 85.0, first), (2, 95.0, first), (3, 100.0, first), (4, 20.0, second),
             (5, 85.0, second), (5 * 60 + 3 * 60 + 2, 85.0, second)]
    said = []
    for minutes, percent, resets_at in steps:
        state = dump._state(ALERT_NOW + timedelta(minutes=minutes), ApiStatus.LIVE,
                            [limit(shape, percent, resets_at)], 0)
        said.append(_said(engine.evaluate(state)))
    return said


def spend(shape: str, extra: float = 0.0) -> Any:
    from usage_monitor.data.models import Spend

    used, limit_, currency = SPENDS[shape]
    return Spend(enabled=True, used=used + extra, limit=limit_, currency=currency)


def credits_steps(shape: str, locale: Any) -> list[list[list[str]]]:
    """The alerts an engine reading ``locale`` raises for usage credits of ``shape``: a baseline, then more used."""
    from usage_monitor.data.alerts import AlertEngine
    from usage_monitor.data.models import ApiStatus
    from usage_monitor.render import dump

    engine = AlertEngine({}, tz=timezone.utc, locale_provider=lambda: locale)
    said = []
    for minutes, extra in ((0, -1.0), (1, 0.0)):
        state = dump._state(ALERT_NOW + timedelta(minutes=minutes), ApiStatus.LIVE,
                            [limit("session", 10.0, ALERT_NOW + timedelta(hours=3))], 0)
        state = replace(state, usage=replace(state.usage, spend=spend(shape, extra)))
        said.append(_said(engine.evaluate(state)))
    return said


def reset_moments() -> list[datetime]:
    """The local wall times the reset texts are worked out at: every weekday at RESET_HOURS and RESET_MINUTES."""
    return [RESET_MONDAY + timedelta(days=day, hours=hour, minutes=minute)
            for day in range(7) for hour in RESET_HOURS for minute in RESET_MINUTES]


def words() -> dict[str, Any]:
    """The English words of the code this Python imports. Local times follow the process's time zone: see collect."""
    from usage_monitor import i18n
    from usage_monitor.data import alerts

    english = i18n.ENGLISH
    found: dict[str, Any] = {}
    for version in UPDATE_VERSIONS:
        found[f"update/{version}"] = release_toasts(english, version)
    forms = ended_note_forms()
    for tools in NOTE_COUNTS:
        for waits in NOTE_COUNTS:
            text = ended_note(tools, waits, forms)
            # Setup leaves no note when it ended neither (WriteUpdateNote exits on an empty EndedNote).
            found[f"note/{tools}/{waits}"] = note_toasts(english, text.encode("ascii") if text else None)
    for name, note in PROSE_NOTES.items():
        found[f"note/prose/{name}"] = note_toasts(english, note)
    for shape in SHAPES:
        found[f"alerts/{shape}"] = alert_steps(shape, english)
        found[f"name/{shape}"] = alerts._name(limit(shape, 10.0, ALERT_NOW), english)
    for levels in THRESHOLDS:
        found[f"ladder/{'-'.join(map(str, levels))}"] = alert_steps("session", english,
                                                                    {"alert_thresholds": list(levels)})
    for shape in SPENDS:
        found[f"credits/{shape}"] = credits_steps(shape, english)
        found[f"credits_text/{shape}"] = alerts._credits_text(spend(shape), english)
    for kind in ("session", "weekly"):
        for moment in reset_moments():
            local = moment.astimezone()                 # the wall time in the process's zone (pinned: UTC)
            found[f"reset_text/{kind}/{moment.isoformat()}"] = alerts._reset_text(limit(kind, 10.0, local), None,
                                                                                    english)
    return found


# ── The toast sweep (#97) ─────────────────────────────────────────────────────

SWEEP_VERSIONS = ("2.1.0", "3.0.0-rc.1", "3.10.12-rc.1")     # a plain release, a release candidate, the longest shape
# The budget boxes the toasts stand in (tests/budgets.py), each with what the sweep puts there.
TOAST_BOXES: dict[str, str] = {
    "app.toast.update.title": "the release toast's title, for each SWEEP_VERSIONS",
    "app.toast.update.body": "the release toast's text",
    "app.toast.update_note.title": "the note toast's title",
    "app.toast.update_note.body": "the note toast's text, whole, for every count pair of NOTE_COUNTS but 0 and 0",
    "app.toast.alert_threshold.title": "every threshold alert's title (the ladder of each name shape)",
    "app.toast.alert_resets.body": "every reset time: the alerts', and the session's and a weekly limit's at every "
                                   "RESET_HOURS and RESET_MINUTES on every weekday",
    "app.toast.alert_reset.title": "every reset alert's title, by a change of window and at a window's end",
    "app.toast.alert_reset_ended.body": "the text of a reset at a window's end",
    "app.toast.alert_credits.title": "the credits alert's title",
    "app.toast.alert_credits.body": "the credits alert's text for every SPENDS shape",
}


def sweep_shapes() -> dict[str, tuple[str, str, str | None, str | None]]:
    """The name shapes the sweep words: SHAPES, and a scoped limit named for each model of the dump's states."""
    from usage_monitor.data.identity import legacy_label
    from usage_monitor.render import dump

    shapes = dict(SHAPES)
    for name in dump.DUMP_STATE_NAMES:
        state = dump.sample_state(name)
        for item in (state.usage.limits if state is not None and state.usage is not None else ()):
            if item.scope_model:
                shapes.setdefault(f"dump/{item.scope_model}", ("weekly_scoped", legacy_label(
                    "weekly_scoped", item.scope_model, item.scope_surface), item.scope_model, item.scope_surface))
    return shapes


@functools.lru_cache(maxsize=None)
def toast_sweep(language: str) -> dict[str, tuple[str, ...]]:
    """Every toast ``language`` (a DEV_LANGUAGES id) can show, by the budget box that measures it (TOAST_BOXES), as
    the code words it: in each of the language's locales (widget_words.sweep_locales: every format tag, on each
    clock), the release toast for each version, the note for every count pair, whole (app.update_note_words: the
    budget is the text before app.py's cut), the alerts the engine raises for each sweep shape (its ladder, a reset by a
    change of window and at a window's end), every reset time of the grid, and the credits alert for each SPENDS shape.
    Pure: the reset times are UTC wall clocks, so the process's time zone does not matter."""
    from usage_monitor import app
    from usage_monitor.data import alerts
    from widget_words import sweep_locales

    found: dict[str, dict[str, None]] = {box: {} for box in TOAST_BOXES}

    def add(box: str, *texts: str) -> None:
        for text in texts:
            found[box][str(text)] = None

    shapes = list(sweep_shapes().values())
    moments = [moment.replace(tzinfo=timezone.utc) for moment in reset_moments()]
    for locale in sweep_locales(language):
        add("app.toast.update.title", *(app.update_toast_title(version, locale) for version in SWEEP_VERSIONS))
        add("app.toast.update.body", app.update_toast_body(locale))
        add("app.toast.update_note.title", app.update_note_title(locale))
        add("app.toast.update_note.body", *(app.update_note_words(tools, waits, locale) for tools in NOTE_COUNTS
                                            for waits in NOTE_COUNTS if tools or waits))
        for shape in shapes:
            steps = alert_steps(shape, locale)
            for index, step in enumerate(steps):
                for kind, _severity, _key, title, message in step:
                    if kind == "threshold":
                        add("app.toast.alert_threshold.title", title)
                        add("app.toast.alert_resets.body", message)
                    elif index == len(steps) - 1:            # the window ran out by the clock
                        add("app.toast.alert_reset.title", title)
                        add("app.toast.alert_reset_ended.body", message)
                    else:                                    # the next window showed
                        add("app.toast.alert_reset.title", title)
                        add("app.toast.alert_resets.body", message)
        for moment in moments:
            for kind in ("session", "weekly"):
                add("app.toast.alert_resets.body", alerts._reset_text(limit(kind, 10.0, moment), timezone.utc, locale))
        for shape in SPENDS:
            for step in credits_steps(shape, locale):
                for _kind, _severity, _key, title, message in step:
                    add("app.toast.alert_credits.title", title)
                    add("app.toast.alert_credits.body", message)
    return {box: tuple(texts) for box, texts in found.items()}


# ── Running ───────────────────────────────────────────────────────────────────

def collect(repo: str | Path = REPO) -> dict[str, Any]:
    """The words of the code in ``repo``, worked out in a fresh Python with the time zone pinned. Raises when that
    Python imported usage_monitor from anywhere else."""
    root = Path(repo).resolve()
    done = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--print"], cwd=root,
                          env={**os.environ, "TZ": PINNED_TZ, "PYTHONPATH": str(root)}, capture_output=True,
                          encoding="utf-8", timeout=600, check=False, creationflags=NO_WINDOW)
    if done.returncode:
        raise RuntimeError(f"the toast words could not be worked out:\n{done.stderr[-3000:]}")
    result = json.loads(done.stdout)
    source = Path(result["source"]).resolve()
    if root not in source.parents:
        raise RuntimeError(f"usage_monitor came from {source}, not from {root}")
    return result["words"]


def recorded() -> dict[str, Any]:
    return json.loads(RECORD.read_text(encoding="utf-8"))["words"]


def record() -> int:
    """Write the record from a fresh Python, one line per entry. Refused (exit 1) when it exists."""
    if RECORD.exists():
        print(f"{RECORD.name} exists: English must not change, so it is never recorded again")
        return 1
    found = collect()
    items = list(found.items())
    lines = ["{", f' "_about": {json.dumps(ABOUT, ensure_ascii=False)},', ' "words": {']
    for at, (key, value) in enumerate(items):
        comma = "," if at < len(items) - 1 else ""
        lines.append(f"  {json.dumps(key, ensure_ascii=False)}: {json.dumps(value, ensure_ascii=False)}{comma}")
    lines += [" }", "}"]
    RECORD.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    assert recorded() == found
    print(f"wrote {len(found)} entries to {RECORD}")
    return 0


def main(argv: list[str]) -> int:
    if argv == ["--print"]:
        import usage_monitor

        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        print(json.dumps({"source": usage_monitor.__file__, "words": words()}, ensure_ascii=False))
        return 0
    if argv == ["--record"]:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        return record()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
