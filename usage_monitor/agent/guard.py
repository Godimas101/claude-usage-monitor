"""The usage guard (#27): from a reading and the caps set in Settings, whether Claude sessions should
wind down, and when they may resume.

The guard only informs. It never blocks a tool or stops a session: a session that is working hears
once that a cap was reached (``--hook`` on PostToolUse), and ``--wait`` tells a session that wound
down when every watched limit is under its cap again. Resuming needs every limit: a fresh 5-hour
window while the week is still over its cap releases nobody.

A limit only drops at its reset, so the guard holds until the latest reset among the limits that
are over their caps. A window that has reset since the reading no longer counts.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from usage_monitor.data.models import UsageSnapshot
from usage_monitor.settings import DEFAULTS as _SETTINGS
from usage_monitor.settings import is_valid as _valid

OFF, ARMED, HOLDING = "off", "armed", "holding"
SETTING_KEYS = {  # setting -> policy field
    "guard_enabled": "enabled",
    "guard_session_cap": "session_cap",
    "guard_weekly_cap": "weekly_cap",
    "guard_scoped_cap": "scoped_cap",
    "guard_resume_stagger_s": "stagger_s",
}
DEFAULTS = {key: _SETTINGS[key] for key in SETTING_KEYS}
SETTINGS_NAME = "settings.json"  # paths.settings_path() is app_data_dir() / this


def policy_from(get: Callable[[str], Any]) -> dict:
    """The policy from a settings lookup (Settings.get, or dict.get on a settings file)."""
    policy = {}
    for key, field in SETTING_KEYS.items():
        value = get(key)
        policy[field] = DEFAULTS[key] if value is None and key in ("guard_enabled", "guard_resume_stagger_s") else value
    policy["enabled"] = bool(policy["enabled"])
    return policy


def policy_from_file(path: Path) -> dict:
    """The policy in a settings file, for a reader running while the app is closed, read as the app
    reads it: a byte-order mark is fine, and a value its rule refuses gives the default. Defaults
    when the file is missing or unreadable."""
    try:
        stored = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    return policy_from(lambda key: stored[key] if key in stored and _valid(key, stored[key]) else DEFAULTS[key])


def for_reader(doc: Mapping[str, Any] | None, now: datetime, folder: Path) -> dict:
    """The guard's verdict for a reader process, evaluated again now (a window may have reset since
    the reading). The policy is the one the app wrote with the reading, or, with the app closed and
    the reading one of our own, the one in the settings file."""
    block = (doc or {}).get("guard")
    policy = block.get("policy") if isinstance(block, Mapping) else None
    if not isinstance(policy, Mapping):
        policy = policy_from_file(folder / SETTINGS_NAME)
    return evaluate(doc or {}, policy, now)


def limits_doc(usage: UsageSnapshot | None) -> dict:
    """The parts of a reading the guard looks at, in the shape of the status document."""
    if usage is None:
        return {"session": None, "weekly": None, "scoped": []}

    def entry(limit: Any) -> dict:
        return {"label": limit.label, "model": limit.scope_model, "percent": limit.percent,
                "resets_at": _iso(limit.resets_at)}

    return {"session": entry(usage.session) if usage.session else None,
            "weekly": entry(usage.weekly) if usage.weekly else None,
            "scoped": [entry(limit) for limit in usage.scoped]}


def evaluate(doc: Mapping[str, Any], policy: Mapping[str, Any], now: datetime) -> dict:
    """{"state": off | armed | holding, "over": [...], "resume_at": ISO or None, "policy": {...}}.

    ``over`` lists each watched limit at or past its cap whose window has not reset: name
    ("session", "weekly" or the scoped label), label for people, percent, cap and resets_at.
    ``resume_at`` is the latest of their resets (None when one of them has no reset time).
    """
    result = {"state": OFF, "over": [], "resume_at": None, "policy": dict(policy)}
    if not policy.get("enabled"):
        return result
    over = []
    for name, label, entry, cap in _watched(doc, policy):
        percent = entry.get("percent") if isinstance(entry, Mapping) else None
        if not _is_number(cap) or not _is_number(percent) or percent < cap:
            continue
        resets = _parse(entry.get("resets_at"))
        if resets is not None and resets <= now:
            continue  # that window has reset since the reading: its percent no longer holds
        over.append({"name": name, "label": label, "percent": percent, "cap": cap, "resets_at": _iso(resets)})
    resets_all = [_parse(item["resets_at"]) for item in over]
    resume = max(resets_all) if over and all(resets_all) else None
    result.update(state=HOLDING if over else ARMED, over=over, resume_at=_iso(resume))
    return result


def episode(evaluation: Mapping[str, Any]) -> str:
    """A key for one holding episode: the limits over their caps and their windows. A session is
    told once per episode; a new window over a cap is a new episode."""
    return "|".join(sorted(f"{item['name']}@{item['resets_at']}" for item in evaluation.get("over") or []))


def _watched(doc: Mapping[str, Any], policy: Mapping[str, Any]) -> Iterator[tuple[str, str, Any, Any]]:
    yield "session", "session", doc.get("session"), policy.get("session_cap")
    yield "weekly", "weekly", doc.get("weekly"), policy.get("weekly_cap")
    for entry in doc.get("scoped") or []:
        if isinstance(entry, Mapping):
            model = entry.get("model")
            label = f"{model} weekly" if model else str(entry.get("label") or "scoped").lower()
            yield str(entry.get("label") or label), label, entry, policy.get("scoped_cap")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:  # fromisoformat takes the "Z" that _iso writes only from Python 3.11
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
