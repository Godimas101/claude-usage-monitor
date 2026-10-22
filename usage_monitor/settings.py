"""Persisted settings: defaults, v1 migration, debounced atomic save.

The file is the one v1 wrote (same path, same keys). A v1 file loads unchanged and is
rewritten once as settings_version 2 on first load. Keys starting with "_" are runtime-only:
they live in memory and are announced to listeners, but are never written.

Loading is forgiving. A corrupt file is moved aside to settings.json.bad, and a wrongly typed
value falls back to its default; both are logged. set() and update() are strict: a value that
breaks a rule raises ValueError and nothing in that batch is applied.
"""
from __future__ import annotations

import contextlib
import copy
import json
import os
import re
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from usage_monitor import paths
from usage_monitor.log import get_logger

log = get_logger(__name__)

SETTINGS_VERSION = 2

# Every key the app reads, with its default. The v1 keys keep their v1 defaults.
# settings_version, billing_day, plan_price_override, ingest_interval_s and the alert_* keys are v2 additions.
DEFAULTS: dict[str, Any] = {
    "settings_version": SETTINGS_VERSION,
    "poll_interval_ms": 15 * 60 * 1000,
    "position": None,
    "show_floating": True,
    "show_taskbar": True,
    "check_updates": True,
    "skip_version": "",
    "theme": "Default",
    "float_transparent_bg": False,
    "float_text_fx": True,
    "taskbar_transparent_bg": False,
    "taskbar_text_fx": True,
    "billing_day": None,
    "plan_price_override": None,
    "ingest_interval_s": 60,
    "alerts_enabled": True,
    "alert_thresholds": [80, 90, 100],
    "alert_on_threshold": True,
    "alert_on_reset": True,
    "alert_on_credits": True,
    "alert_quiet_hours": None,
}

_MISSING = object()
_REPLACE_TRIES = 5


# ── Value rules ───────────────────────────────────────────────────────────────

def _is_str(v: Any) -> bool:
    return isinstance(v, str)


def _is_bool(v: Any) -> bool:
    return isinstance(v, bool)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)  # bool is a subclass of int


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


MAX_INTERVAL_S = 86_400  # one day. Longer waits are useless, and a huge one overflows the timers


def _is_seconds(v: Any) -> bool:
    return _is_number(v) and 1 <= v <= MAX_INTERVAL_S


def _is_poll_ms(v: Any) -> bool:
    return _is_number(v) and 1_000 <= v <= MAX_INTERVAL_S * 1000


def _is_version(v: Any) -> bool:
    return _is_int(v) and v >= 1


def _is_position(v: Any) -> bool:
    if v is None:
        return True
    return isinstance(v, dict) and _is_number(v.get("x")) and _is_number(v.get("y"))


def _is_billing_day(v: Any) -> bool:
    return v is None or (_is_int(v) and 1 <= v <= 31)


def _is_price(v: Any) -> bool:
    return v is None or (_is_number(v) and v >= 0)


_CLOCK = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


def parse_clock(value: Any) -> int | None:
    """Minutes after local midnight for a strict 24-hour "HH:MM" string, else None."""
    if not isinstance(value, str) or _CLOCK.fullmatch(value) is None:
        return None
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def _is_thresholds(v: Any) -> bool:
    """1 to 5 distinct whole percentages in 1..100."""
    # Item types are checked before distinctness: set() raises TypeError on unhashable items.
    return (isinstance(v, list) and 1 <= len(v) <= 5
            and all(_is_int(t) and 1 <= t <= 100 for t in v) and len(set(v)) == len(v))


def _is_quiet_hours(v: Any) -> bool:
    if v is None:
        return True
    return (isinstance(v, dict) and set(v) == {"start", "end"}
            and parse_clock(v["start"]) is not None and parse_clock(v["end"]) is not None)


_RULES: dict[str, Callable[[Any], bool]] = {
    "settings_version": _is_version,
    "poll_interval_ms": _is_poll_ms,
    "position": _is_position,
    "show_floating": _is_bool,
    "show_taskbar": _is_bool,
    "check_updates": _is_bool,
    "skip_version": _is_str,
    "theme": _is_str,
    "float_transparent_bg": _is_bool,
    "float_text_fx": _is_bool,
    "taskbar_transparent_bg": _is_bool,
    "taskbar_text_fx": _is_bool,
    "billing_day": _is_billing_day,
    "plan_price_override": _is_price,
    "ingest_interval_s": _is_seconds,
    "alerts_enabled": _is_bool,
    "alert_thresholds": _is_thresholds,
    "alert_on_threshold": _is_bool,
    "alert_on_reset": _is_bool,
    "alert_on_credits": _is_bool,
    "alert_quiet_hours": _is_quiet_hours,
}


def _describe(value: Any) -> str:
    return f"{type(value).__name__} {value!r}"[:80]


def _check(key: str, value: Any) -> None:
    """Raise for a non-string key or a value that breaks its rule. Unknown keys accept anything."""
    if not isinstance(key, str):
        raise TypeError(f"setting keys must be str, not {type(key).__name__}")
    rule = _RULES.get(key)
    if rule is not None and not rule(value):
        raise ValueError(f"invalid value for setting {key!r}: {_describe(value)}")


def _copy(value: Any) -> Any:
    """Containers are copied so no caller can change stored state except through set()."""
    return copy.deepcopy(value) if isinstance(value, (dict, list)) else value


def _isolate(key: str, value: Any) -> Any:
    """Copy a persisted value on the way in and out. Runtime values (keys starting with "_")
    are held by reference, because they may be live objects that cannot be copied."""
    return value if key.startswith("_") else _copy(value)


def _same(a: Any, b: Any) -> bool:
    # Compare types too: True == 1 and 1 == 1.0 still count as a change to the stored value.
    return type(a) is type(b) and a == b


# ── File I/O ──────────────────────────────────────────────────────────────────

class _Unreadable(OSError):
    """The settings file exists but cannot be read right now, for example because another program holds it."""


def _read_file(path: Path) -> dict[str, Any] | None:
    """The stored object, or None when there is no usable file (missing or corrupt).

    Raises _Unreadable when the file exists but cannot be read. Such a file is left alone, since
    treating it as missing would overwrite it with defaults on the next save.
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _Unreadable(f"cannot read settings {path} ({exc})") from exc
    try:
        # utf-8-sig tolerates a byte-order mark left by a hand edit.
        data = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, RecursionError) as exc:  # bad JSON or UTF-8, or nesting too deep to parse
        log.warning("settings file %s is not valid JSON (%s)", path, exc)
        _quarantine(path)
        return None
    if not isinstance(data, dict):
        log.warning("settings file %s does not hold a JSON object", path)
        _quarantine(path)
        return None
    return data


def _quarantine(path: Path) -> None:
    """Move a corrupt settings file to settings.json.bad, replacing an older .bad."""
    bad = path.with_name(path.name + ".bad")
    try:
        os.replace(path, bad)
    except OSError as exc:
        log.warning("corrupt settings file %s could not be moved aside: %s", path, exc)
        return
    log.warning("corrupt settings file moved to %s; starting from defaults", bad)


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write JSON to a temp file beside ``path``, then move it into place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        _replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _replace(src: str, dst: Path) -> None:
    """os.replace, retried briefly: on Windows a reader or a virus scanner can hold the target."""
    for attempt in range(_REPLACE_TRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == _REPLACE_TRIES - 1:
                raise
            time.sleep(0.05 * (attempt + 1))


def _merge(stored: dict[str, Any] | None) -> tuple[dict[str, Any], bool]:
    """Defaults overlaid with the stored object. Returns (state, needs_upgrade)."""
    state = dict(DEFAULTS)
    if stored is None:
        return state, False
    for key, value in stored.items():
        if key.startswith("_"):
            log.debug("ignoring runtime key %r found in the settings file", key)
            continue
        rule = _RULES.get(key)
        if rule is not None and not rule(value):
            log.warning(
                "setting %r has an invalid value (%s); using the default",
                key, _describe(value),
            )
            continue
        state[key] = value
    version = stored.get("settings_version")
    upgraded = not (_is_int(version) and version >= SETTINGS_VERSION)
    if upgraded:
        state["settings_version"] = SETTINGS_VERSION
    return state, upgraded


# ── Settings ──────────────────────────────────────────────────────────────────

class Settings:
    """Thread-safe settings with change listeners and one debounced, atomic save.

    Listeners run on the thread that made the change, one change at a time, and every listener
    sees the changes in the order they were applied. A change that a listener makes is delivered
    once the listeners of the change in progress have run. A listener must not block waiting for
    another thread that calls set(), because that thread would wait on the dispatch lock. Pending
    saves run on daemon timers; call close() before exit to flush them.
    """

    def __init__(self, path: Path | None = None, save_delay_s: float = 0.75) -> None:
        self._path = Path(path) if path is not None else paths.settings_path()
        self._save_delay_s = max(0.0, float(save_delay_s))
        self._lock = threading.RLock()           # guards values, listeners and timer state
        self._dispatch_lock = threading.RLock()  # keeps each change and its notifications in order
        self._save_lock = threading.Lock()       # one disk write at a time
        self._listeners: list[tuple[object, Callable[[str, Any], None]]] = []
        self._dispatching = False  # True while listeners run; guarded by _dispatch_lock
        self._queued: list[tuple[str, Any]] = []  # changes made by listeners; guarded by _dispatch_lock
        self._timer: threading.Timer | None = None
        self._timer_token: object | None = None
        self._version = 0        # bumped by every change to a persisted key
        self._saved_version = 0  # the version written by the last successful save
        self._blocked = False    # the file exists but could not be read: saves wait until it can be
        self._held: set[str] = set()  # keys changed while blocked; they win over the file once it reads
        try:
            stored = _read_file(self._path)
        except _Unreadable as exc:
            log.warning("%s; using defaults and leaving the file alone until it can be read", exc)
            stored, self._blocked = None, True
        self._data, needs_upgrade = _merge(stored)
        if stored is not None and needs_upgrade:
            self._write_to_disk()

    # ── Reads ──

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            if key in self._data:
                return _isolate(key, self._data[key])
        return default

    def __getitem__(self, key: str) -> Any:
        with self._lock:
            return _isolate(key, self._data[key])

    def as_dict(self) -> dict[str, Any]:
        """A copy of everything that gets persisted (runtime keys are left out)."""
        with self._lock:
            return self._persistable_locked()

    # ── Writes ──

    def set(self, key: str, value: Any) -> None:
        self.update({key: value})

    def update(self, mapping: Mapping[str, Any]) -> None:
        """Apply several values as one change. Nothing is applied if any value is invalid."""
        items = list(mapping.items())
        for key, value in items:
            _check(key, value)
        with self._dispatch_lock:
            with self._lock:
                changes = self._apply_locked(items)
            if self._dispatching:  # a listener made this change: deliver it after the current one
                self._queued.extend(changes)
            else:
                self._dispatch(changes)
        if any(not key.startswith("_") for key, _ in changes):
            self._schedule_save()

    def on_change(self, callback: Callable[[str, Any], None]) -> Callable[[], None]:
        """Call ``callback(key, value)`` after each real change. Returns an unsubscribe function."""
        token = object()
        with self._lock:
            self._listeners.append((token, callback))

        def unsubscribe() -> None:
            with self._lock:
                self._listeners = [entry for entry in self._listeners if entry[0] is not token]

        return unsubscribe

    # ── Saving ──

    def save_now(self) -> None:
        """Write immediately, even when nothing changed, and cancel any pending save."""
        with self._lock:
            self._cancel_timer_locked()
        self._write_to_disk()

    def close(self) -> None:
        """Flush unsaved changes. Safe to call more than once; the object stays usable."""
        with self._lock:
            pending = self._version != self._saved_version
        if pending:
            self.save_now()
        else:
            with self._lock:
                self._cancel_timer_locked()

    # ── Internals ──

    def _apply_locked(self, items: list[tuple[str, Any]]) -> list[tuple[str, Any]]:
        changes: list[tuple[str, Any]] = []
        for key, value in items:
            new = _isolate(key, value)
            old = self._data.get(key, _MISSING)
            if old is not _MISSING and _same(old, new):
                continue
            self._data[key] = new
            changes.append((key, new))
            if not key.startswith("_"):
                self._version += 1
                if self._blocked:
                    self._held.add(key)
        return changes

    def _dispatch(self, changes: list[tuple[str, Any]]) -> None:
        """Notify the listeners of ``changes``, then of whatever they changed in turn. Caller holds _dispatch_lock."""
        self._dispatching = True
        try:
            pending = changes
            while pending:
                with self._lock:
                    listeners = list(self._listeners)
                for key, value in pending:
                    for _token, callback in listeners:
                        try:
                            callback(key, _isolate(key, value))
                        except Exception:  # a broken listener must not stop the others or the caller
                            log.exception("settings listener failed on key %r", key)
                pending, self._queued = self._queued, []
        finally:
            self._dispatching = False
            self._queued = []

    def _schedule_save(self) -> None:
        with self._lock:
            self._cancel_timer_locked()  # restarting the countdown is what coalesces a burst
            token = object()
            timer = threading.Timer(self._save_delay_s, self._on_timer, args=(token,))
            timer.daemon = True
            self._timer, self._timer_token = timer, token
            timer.start()

    def _on_timer(self, token: object) -> None:
        with self._lock:
            if token is not self._timer_token:  # superseded or cancelled after it started
                return
            self._timer = None
            self._timer_token = None
        self._write_to_disk()

    def _cancel_timer_locked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self._timer = None
        self._timer_token = None

    def _write_to_disk(self) -> None:
        with self._save_lock:  # serialised, so an older snapshot can never land after a newer one
            if self._blocked and not self._unblock():
                return  # the file still cannot be read, so it is left as it is; the next save retries
            with self._lock:
                payload = self._persistable_locked()
                version = self._version
            try:
                _atomic_write_json(self._path, payload)
            except Exception as exc:  # logged, not raised; the next change or close() retries
                log.warning("could not save settings to %s: %s", self._path, exc)
                return
            with self._lock:
                self._saved_version = version

    def _persistable_locked(self) -> dict[str, Any]:
        return {key: _copy(value) for key, value in self._data.items() if not key.startswith("_")}

    def _unblock(self) -> bool:
        """Read the file again. When it reads, its values are merged with the changes made meanwhile."""
        try:
            stored = _read_file(self._path)
        except _Unreadable:
            return False
        state, _needs_upgrade = _merge(stored)
        with self._lock:
            for key in self._held:
                state[key] = self._data[key]
            self._data = state
            self._held.clear()
            self._blocked = False
        log.info("settings file %s can be read again; saving over it", self._path)
        return True
