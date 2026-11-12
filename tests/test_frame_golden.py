"""Golden frames (#32): every frame the widgets draw for the sample states is what the last release drew, pixel for
pixel, except the frames this release changes on purpose (CHANGED).

The guard tests compare guarded frames with unguarded ones drawn by the same code, so on their own they cannot show
that a widget with the guard off is what the release before drew. This pins every theme, variant and sample state
(the guard's states too): the floating widget, the taskbar strip at each height, the hover card and the tray icon, at
every scale and size. tests/frame_digests.py works the digests out, and says how to record them after a release.
"""
from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any

import pytest

from frame_digests import PINNED_TZ, collect

RECORD = Path(__file__).parent / "fixtures" / "frame_digests.json"
REPO = Path(__file__).resolve().parent.parent

# The frames this release draws differently on purpose, and why: patterns over "theme/variant/state".
CHANGED: dict[str, str] = {
    "*/*/stale": "#52: an expired sign-in says to use Claude Code (LOGIN EXPIRED - USE CLAUDE CODE)",
    "*/*/stale3": "#52: the same, with three rows",
}


@pytest.fixture(scope="module")
def frames() -> tuple[dict[str, Any], dict[str, str]]:
    return json.loads(RECORD.read_text(encoding="utf-8")), collect(REPO)


def changed_why(key: str) -> str | None:
    return next((why for pattern, why in CHANGED.items() if fnmatch.fnmatchcase(key, pattern)), None)


def test_every_frame_the_last_release_drew_is_drawn_the_same(frames):
    record, now = frames
    old = record["frames"]
    gone = sorted(set(old) - set(now))
    assert not gone, f"frames {record['ref']} drew are no longer drawn: {gone}"
    new = sorted(key for key in set(now) - set(old) if changed_why(key) is None)
    assert not new, f"new frames, not in CHANGED: {new}"
    moved = sorted(key for key in old if key in now and now[key] != old[key] and changed_why(key) is None)
    assert not moved, (f"{len(moved)} frames are not what {record['ref']} drew. Compare python main.py --dump-frames "
                       f"DIR here and at the tag. Frames: {moved[:24]}")


def test_the_frames_this_release_changes_on_purpose_do_differ(frames):
    """So CHANGED stays true: a pattern that matches nothing, or a frame still drawn as before, is a stale entry."""
    record, now = frames
    for pattern, why in CHANGED.items():
        keys = [key for key in now if fnmatch.fnmatchcase(key, pattern)]
        assert keys, f"{pattern} ({why}) matches no frame"
        same = [key for key in keys if record["frames"].get(key) == now[key]]
        assert not same, f"{pattern} ({why}): still drawn as {record['ref']} drew them: {same}"


def test_the_record_is_of_a_release_with_the_time_zone_pinned(frames):
    record, now = frames
    assert record["ref"].startswith("v") and record["tz"] == PINNED_TZ
    assert len(now) == len(record["frames"]) > 300                     # every theme, variant and state, not a share
