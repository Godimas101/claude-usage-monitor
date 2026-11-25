"""The English words of the view model, the tray tooltip and the tray menu, for tests/test_widget_words.py (#93).

tests/fixtures/widget_words_english.json holds what these said before #93 moved their words into the catalog, and the
test holds the code to it byte for byte. Reset times and resume clocks are local time, so the words are worked out in a
fresh Python with the time zone pinned to UTC (``collect``), as tests/frame_digests.py does for the frames.

What is recorded: every model text (status, stale message, age, each row's label, share and reset time, the guard's
labels and resume texts) for every dump state in every show_scoped mode, and for the edge states below; the tray
tooltip of each, without and with the dev mark; the guard's tooltip at each countdown shape, with and without a resume
time; and every tray menu the toggles, the waiting sessions and an update can give.

    python tests/widget_words.py --print        the words of the code Python imports, as JSON
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
PINNED_TZ = "UTC0"              # the C runtime reads TZ when Python starts, so it is set for a fresh process
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
COUNTDOWNS = {"59s": timedelta(seconds=59), "9m": timedelta(minutes=9), "2h14m": timedelta(hours=2, minutes=14),
              "2d3h": timedelta(days=2, hours=3), "6d23h": timedelta(days=6, hours=23)}
WAITING = (0, 1, 2, 12, 1000)
UPDATES = (None, "2.1.0", "3.0.0-rc.1")


def _model_words(model: Any) -> dict[str, Any]:
    guard = model.guard
    return {
        "status_text": model.status_text, "stale_message": model.stale_message, "age_text": model.age_text,
        "rows": [[row.key, row.label, row.share_text, row.resets_text] for row in model.rows],
        "guard": None if guard is None else {
            "labels": dict(guard.labels), "over_limits": [list(item) for item in guard.over_limits()],
            "watched": [list(item) for item in guard.watched()], "resume_text": guard.resume_text,
            "resume_in": guard.resume_in, "resume_clock": guard.resume_clock,
        },
    }


def _tooltips(model: Any) -> list[str]:
    from usage_monitor import app

    return [app.tray_tooltip(model), app.tray_tooltip(model, dev=True)]


def _limit(kind: str, percent: float, at: Any, label: str, **extra: Any) -> Any:
    from usage_monitor.data.models import Limit

    group = "session" if kind == "session" else "weekly"
    window = 18000 if kind == "session" else 604800
    return Limit(kind=kind, group=group, percent=percent, severity=None, resets_at=at, is_active=True, label=label,
                 window_seconds=window, **extra)


def _edge_states() -> dict[str, Any]:
    """The states the dump has no sample of: a refused sign-in, scoped limits named every way a reading can name them,
    a kind with no words, and the guard holding over each of those and over a limit gone from the reading."""
    from usage_monitor.agent import guard
    from usage_monitor.data.identity import legacy_label
    from usage_monitor.data.models import ApiStatus
    from usage_monitor.render import dump

    now = dump.SAMPLE_NOW
    week = now + timedelta(days=3)
    base = [_limit("session", 40.0, now + timedelta(hours=3, minutes=27), "SESSION"),
            _limit("weekly_all", 20.0, week, "WEEKLY")]
    scoped = {
        "scoped_model": _limit("weekly_scoped", 60.0, week, legacy_label("weekly_scoped", "Fable", None),
                               scope_model="Fable", allowance_pct=50.0),
        "scoped_model_and_surface": _limit("weekly_scoped", 60.0, week, legacy_label("weekly_scoped", "Fable", "chat"),
                                           scope_model="Fable", scope_surface="chat", allowance_pct=33.4),
        "scoped_empty_label": _limit("weekly_scoped", 60.0, week, "", scope_model="Haiku", allowance_pct=25.0),
        "scoped_surface_only": _limit("weekly_scoped", 60.0, week, legacy_label("weekly_scoped", None, "claude_code"),
                                      scope_surface="claude_code", allowance_pct=25.0),
        "scoped_surface_only_unlabelled": _limit("weekly_scoped", 60.0, week, "", scope_surface="Claude Code"),
        "scoped_neither": _limit("weekly_scoped", 60.0, week, legacy_label("weekly_scoped", None, None)),
        "scoped_neither_unlabelled": _limit("weekly_scoped", 60.0, week, ""),
        "unknown_kind": _limit("design_weekly", 60.0, week, legacy_label("design_weekly", None, None)),
    }
    states: dict[str, Any] = {"refused": replace(dump.sample_state("stale3"), signin_refused=403),
                              "refused_401": replace(dump.sample_state("stale3"), signin_refused=401)}
    for status in ApiStatus:                    # no reading: the tooltip names every status, live and paused too
        states[f"no_reading/{status.name.lower()}"] = dump._state(now, status, None, 0)
    policy = {**dump.GUARD_SAMPLE_POLICY, "scoped_cap": 50}
    for name, limit in scoped.items():
        state = dump._state(now, ApiStatus.LIVE, [*base, limit], 12)
        states[name] = state
        verdict = guard.evaluate(guard.limits_doc(state.usage), policy, now)
        states[f"{name}/guard_holding"] = replace(state, guard=verdict)
    gone = dump._state(now, ApiStatus.LIVE, [*base, scoped["scoped_model"]], 12)
    block = guard.evaluate(guard.limits_doc(gone.usage), policy, now)
    block = {**block, "over": [*block["over"], {"name": "OPUS WEEKLY", "label": "Opus weekly", "percent": 99.0,
                                                "cap": 50, "resets_at": block["resume_at"]},
                               {"name": "", "label": "", "percent": 99.0}]}
    states["guard_over_a_limit_gone"] = replace(gone, guard=block)
    return states


def _guard_states() -> dict[str, Any]:
    """The guard holding the session limit with each countdown shape, on a live and on a rate-limited reading, and with
    no resume time."""
    from usage_monitor.render import dump

    now = dump.SAMPLE_NOW
    states: dict[str, Any] = {}
    for status_name in ("guard_hold_session", "rate_limited3"):
        held = dump.sample_state("guard_hold_session")
        source = dump.sample_state(status_name)
        for shape, delta in COUNTDOWNS.items():
            at = (now + delta).isoformat().replace("+00:00", "Z")
            block = {**held.guard, "resume_at": at}
            states[f"{status_name}/resume_in_{shape}"] = replace(source, guard=block)
        states[f"{status_name}/no_resume_time"] = replace(source, guard={**held.guard, "resume_at": None})
    armed = dump.sample_state("guard_armed")
    states["guard_armed_rate_limited"] = replace(dump.sample_state("rate_limited3"), guard=armed.guard)
    return states


def _menus() -> dict[str, Any]:
    from usage_monitor import app

    actions = ("open_dashboard", "toggle_floating", "toggle_taskbar", "toggle_guard", "resume_waiting", "refresh",
               "report_bug", "update", "quit_app")
    calls = {name: (lambda: None) for name in actions}
    named = {id(call): name for name, call in calls.items()}
    found: dict[str, Any] = {}
    for floating_on in (False, True):
        for taskbar_on in (False, True):
            for guard_on in (False, True):
                for waiting in WAITING:
                    for update in UPDATES:
                        items = app.menu_items(floating_on=floating_on, taskbar_on=taskbar_on, guard_on=guard_on,
                                               waiting=waiting, update_version=update, **calls)
                        key = f"floating={floating_on} taskbar={taskbar_on} guard={guard_on} waiting={waiting} " \
                              f"update={update}"
                        found[key] = [["-"] if item.separator else
                                      [item.label, item.checked, item.enabled, named.get(id(item.action), "?")]
                                      for item in items]
    return found


def words() -> dict[str, Any]:
    """The English words of the code this Python imports. Local times follow the process's time zone: see collect."""
    from usage_monitor.render import dump
    from usage_monitor.render.model import SHOW_SCOPED_MODES, build_model

    found: dict[str, Any] = {}
    for name in dump.DUMP_STATE_NAMES:
        for mode in SHOW_SCOPED_MODES:
            model = dump.sample_model(name, show_scoped=mode)
            found[f"model/{name}/{mode}"] = _model_words(model)
            found[f"tooltip/{name}/{mode}"] = _tooltips(model)
    for group in (_edge_states(), _guard_states()):
        for name, state in group.items():
            model = build_model(state, dump.SAMPLE_NOW, show_scoped="always")
            found[f"model/{name}"] = _model_words(model)
            found[f"tooltip/{name}"] = _tooltips(model)
    for key, menu in _menus().items():
        found[f"menu/{key}"] = menu
    return found


def collect(repo: str | Path = REPO) -> dict[str, Any]:
    """The words of the code in ``repo``, worked out in a fresh Python with the time zone pinned. Raises when that
    Python imported usage_monitor from anywhere else."""
    root = Path(repo).resolve()
    done = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--print"], cwd=root,
                          env={**os.environ, "TZ": PINNED_TZ, "PYTHONPATH": str(root)}, capture_output=True,
                          encoding="utf-8", timeout=600, check=False, creationflags=NO_WINDOW)
    if done.returncode:
        raise RuntimeError(f"the widget words could not be worked out:\n{done.stderr[-3000:]}")
    result = json.loads(done.stdout)
    source = Path(result["source"]).resolve()
    if root not in source.parents:
        raise RuntimeError(f"usage_monitor came from {source}, not from {root}")
    return result["words"]


def main(argv: list[str]) -> int:
    if argv == ["--print"]:
        import usage_monitor

        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        print(json.dumps({"source": usage_monitor.__file__, "words": words()}, ensure_ascii=False))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
