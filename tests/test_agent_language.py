"""The agent modes are English in every app language, and never load a catalog (#90).

--status, --hook and --gate run on every Claude tool call, so they must not import or read the translated text, and their
output (state.json's summary, the hook's notes, the gate's reason) is the same English whatever the app's language is.
"""
from __future__ import annotations

import builtins
import io
import json
import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from usage_monitor import i18n
from usage_monitor.agent import cli, status

UTC = timezone.utc
NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
ALL_MODES = ("status", "hook", "gate")


def reading(folder: Path) -> None:
    """A live reading that holds the guard: the session is past its cap, so the hook and the gate both have words."""
    def iso(moment: datetime) -> str:
        return moment.isoformat(timespec="seconds").replace("+00:00", "Z")

    session_reset = NOW.replace(hour=13, minute=57)
    weekly_reset = NOW.replace(day=24, hour=11, minute=0)
    doc = {"schema": 1, "source": "monitor", "status": "live", "status_detail": "Updated 11:59:00",
           "usage_fetched_at": iso(NOW.replace(minute=59)), "poll_interval_s": 300, "written_at": iso(NOW),
           "plan": {"name": "Max 5x", "tier": "default_claude_max_5x"},
           "session": {"percent": 96.0, "resets_at": iso(session_reset), "window_start": None, "verdict": "AT_RISK",
                       "projected_limit_at": None},
           "weekly": {"percent": 80.0, "resets_at": iso(weekly_reset), "window_start": None, "verdict": "ON_PACE",
                      "pace_delta_pp": 0.0, "projected_limit_at": None},
           "scoped": [{"label": "FABLE WEEKLY", "model": "Fable", "percent": 96.0, "resets_at": iso(weekly_reset)}],
           "burn": {"output_tokens_per_min": 1240.0, "usd_per_hour": 6.1, "velocity": "NORMAL", "window_minutes": 30},
           "guard": {"policy": {"enabled": True, "session_cap": 95, "weekly_cap": 90, "scoped_cap": 90,
                                "stagger_s": 120, "enabled_at": "2026-11-20T09:00:00Z"}}}
    status.write(doc, folder / status.STATUS_NAME)


def run_modes(folder: Path) -> dict[str, bytes]:
    """The three modes' output, in-process, from the reading in ``folder``."""
    out: dict[str, bytes] = {}
    buf = io.BytesIO()
    cli.run_status(as_json=False, folder=folder, clock=lambda: NOW, app_version="2.4.0", out=buf)
    out["status"] = buf.getvalue()
    buf = io.BytesIO()
    cli.run_hook(threshold=80.0, always=False, folder=folder, clock=lambda: NOW, app_version="2.4.0",
                 stdin=io.BytesIO(json.dumps({"hook_event_name": "SessionStart", "session_id": "s1"}).encode()),
                 out=buf)
    out["hook"] = buf.getvalue()
    buf = io.BytesIO()
    cli.run_gate(session_cap=95.0, weekly_cap=90.0, folder=folder, clock=lambda: NOW, app_version="2.4.0",
                 stdin=io.BytesIO(b"{}"), out=buf)
    out["gate"] = buf.getvalue()
    return out


@pytest.fixture
def no_fetch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The reading is current and the app is running, so no request may be made; one would fail the test."""
    monkeypatch.setenv("USAGE_MONITOR_APPDATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setattr(status, "monitor_running", lambda env=None: True)

    def no_request(now: datetime, **kwargs: Any) -> dict:
        raise AssertionError("an agent mode made a request for a reading")

    monkeypatch.setattr(status, "fetch_once", no_request)


def test_the_modes_say_the_same_in_french_as_in_english(tmp_path, monkeypatch, no_fetch):
    english_folder, french_folder = tmp_path / "en", tmp_path / "fr"
    reading(english_folder)
    reading(french_folder)
    english_out = run_modes(english_folder)

    # The app's language is French: the setting says so, and the locale the app would build from it is French.
    monkeypatch.setenv("USAGE_MONITOR_DEV_INSTANCE", "1")
    monkeypatch.setenv("USAGE_MONITOR_LANG", "fr")
    assert i18n.build_locale("fr", i18n.NoWindows(), offered=("en", "fr"), env={}).language == "fr"
    (tmp_path / "appdata").mkdir(exist_ok=True)
    (tmp_path / "appdata" / "settings.json").write_text('{"language": "fr"}', encoding="utf-8")

    french_out = run_modes(french_folder)
    assert french_out == english_out
    for mode in ALL_MODES:
        assert french_out[mode], f"--{mode} said nothing, so the comparison proves nothing"
    assert b"Claude plan usage" in english_out["status"]
    assert b"Claude Usage Monitor" in english_out["hook"]
    assert b"permissionDecisionReason" in english_out["gate"]


def test_the_agent_modes_never_read_a_catalog(tmp_path, monkeypatch, no_fetch):
    """Every catalog load fails, and so does any read of a file under the catalog folder. The modes still work, and
    none of those reads was made."""
    reads: list[str] = []

    def refuse(*args: Any, **kwargs: Any) -> Any:
        reads.append(f"call {getattr(args[0], '__name__', args[0])!s}" if args else "call")
        raise AssertionError("a catalog was loaded")

    monkeypatch.setattr(i18n, "load_catalog", refuse)
    monkeypatch.setattr(i18n, "_read_catalog", refuse)
    monkeypatch.setattr(i18n.Catalog, "__init__", refuse)

    catalogs = i18n.CATALOG_DIR.resolve()

    def under_catalogs(file: Any) -> bool:
        try:
            return Path(os.fspath(file)).resolve().is_relative_to(catalogs)
        except (TypeError, ValueError):
            return False

    def guarded(opener: Callable[..., Any]) -> Callable[..., Any]:
        def open_file(file: Any, *args: Any, **kwargs: Any) -> Any:
            if under_catalogs(file):
                reads.append(f"open {file}")
                raise OSError("catalog reads are refused in the agent modes")
            return opener(file, *args, **kwargs)
        return open_file

    monkeypatch.setattr(builtins, "open", guarded(builtins.open))
    monkeypatch.setattr(io, "open", guarded(io.open))

    folder = tmp_path / "folder"
    reading(folder)
    out = run_modes(folder)
    assert out["status"].startswith(b"Claude plan usage") and out["hook"] and out["gate"]
    assert reads == []
