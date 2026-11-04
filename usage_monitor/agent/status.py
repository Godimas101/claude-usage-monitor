"""The plan-usage status file that Claude sessions read (#24).

The running app writes state.json in its app data folder after every data cycle (see DataService).
--status, --hook and --mcp read it. When the app is not running, --status and --mcp ask the usage
API once themselves and keep the answer in the same file, so the callers within FRESH_S share one
request. The file holds plan percentages, reset times, pace and burn rate. It never holds the
sign-in token, and nothing from a transcript.

Every reader also touches agent-demand beside it. While that file is younger than DEMAND_S, the
app keeps polling when the screen is locked, which is when long unattended runs happen.

The command-line modes import this module, so it imports nothing from the UI and loads the store
and the usage client only when it has to fetch.
"""
from __future__ import annotations

import contextlib
import ctypes
import dataclasses
import json
import os
import sys
import tempfile
import time
from collections.abc import Iterator, Mapping
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from usage_monitor.data.models import BurnRate, PlanInfo, Spend, UsageSnapshot
from usage_monitor.log import get_logger

log = get_logger(__name__)

SCHEMA = 1
STATUS_NAME = "state.json"
DEMAND_NAME = "agent-demand"
LOCK_NAME = "state.lock"      # one reader at a time asks the API; the OS frees it when its holder dies
SOURCE_MONITOR = "monitor"    # written by the running app after a data cycle
SOURCE_CLI = "cli"            # written by --status or --mcp after a request of their own
FRESH_S = 120.0               # with the app closed, a reading this young is reused, not fetched again
DEMAND_S = 60 * 60            # a session asked within this: the app keeps polling while the screen is locked
DEMAND_TOUCH_S = 5 * 60       # readers renew the demand at most this often: a stat is a read, a touch a write
REFRESH_S = 15 * 60           # the app rewrites an unchanged reading at most this often
STALE_FLOOR_S = 30 * 60       # a reading is stale past this, or past two poll intervals when longer
WAIT_S = 15.0                 # how long a reader waits for a monitor that a locked screen paused
FETCH_LOCK_S = 25.0           # how long a reader waits for another reader's request (a request can take 20 s)
PAUSED = "paused"             # ApiStatus.PAUSED.value
_HEALTHY = frozenset({"live", PAUSED})  # the app's statuses that mean it is not failing to fetch
MUTEX_NAME = "Global\\ClaudeUsageMonitor"   # app.MUTEX_NAME (app.py is too heavy to import here)
DEV_ENV = "CUM_DEV_INSTANCE"                 # app.DEV_ENV
_SYNCHRONIZE = 0x00100000
_ERROR_ACCESS_DENIED = 5
_TRIES = 5
_SESSION_KEYS = ("percent", "resets_at", "window_start", "verdict", "slope_pct_per_hour",
                 "projected_limit_at")
_WINDOW_KEYS = ("percent", "resets_at", "window_start", "elapsed_pct", "pace_delta_pp", "verdict",
                "daily_budget_pct", "projected_end_pct")
_SCOPED_KEYS = ("label", "model", "allowance_pct", "used_pct_of_weekly")
_CLOCK_KEYS = ("elapsed_pct", "pace_delta_pp", "daily_budget_pct", "projected_end_pct", "projected_limit_at")

if sys.platform == "win32":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _OpenMutexW = _kernel32.OpenMutexW
    _OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    _OpenMutexW.restype = wintypes.HANDLE
    _CloseHandle = _kernel32.CloseHandle
    _CloseHandle.argtypes = [wintypes.HANDLE]
    _CloseHandle.restype = wintypes.BOOL


# ── Building and writing (the app's side) ──────────────────────────────────────

def build(*, status: str, status_detail: str, usage: UsageSnapshot | None, plan: PlanInfo,
          burn: BurnRate | None, pacing: Mapping[str, Any], now: datetime,
          poll_interval_s: float | None, source: str = SOURCE_MONITOR, app_version: str = "",
          guard: Mapping[str, Any] | None = None) -> dict:
    """The status document. ``pacing`` is analytics.pacing_report() for the same moment, ``guard``
    the usage guard's evaluation (agent/guard.py), with the policy readers re-evaluate against."""
    scoped = [_window_entry(item, _SCOPED_KEYS) for item in pacing.get("scoped") or []]
    return {
        "schema": SCHEMA,
        "source": source,
        "app_version": app_version,
        "written_at": _iso(now),
        "status": status,
        "status_detail": status_detail,
        "usage_fetched_at": _iso(usage.fetched_at) if usage is not None else None,
        "poll_interval_s": None if poll_interval_s is None else round(poll_interval_s),
        "plan": {"name": plan.name, "tier": plan.rate_limit_tier},
        "session": _pick(pacing.get("session"), _SESSION_KEYS),
        "weekly": _window_entry(pacing.get("weekly")),
        "scoped": [entry for entry in scoped if entry is not None],
        "burn": _burn_entry(burn),
        "extra_usage": _extra_entry(usage.spend if usage is not None else None),
        "guard": dict(guard) if guard else None,
    }


def write(doc: Mapping[str, Any], path: Path) -> None:
    """Replace the file in one step, so a reader never sees half a document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, allow_nan=False)
        for attempt in range(_TRIES):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:  # a reader or a virus scanner holds the file for a moment
                if attempt == _TRIES - 1:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def fingerprint(doc: Mapping[str, Any]) -> str:
    """The reading without what moves with the clock alone, and the burn rate reduced to its velocity.

    The app rewrites the file when this changes, and otherwise every REFRESH_S at most, so a quiet
    machine writes a few small files an hour rather than one a minute.
    """
    def still(entry: Any) -> Any:
        return {k: v for k, v in entry.items() if k not in _CLOCK_KEYS} if isinstance(entry, Mapping) else entry

    burn = doc.get("burn")
    view = {key: value for key, value in doc.items() if key not in ("written_at", "burn")}
    view.update(session=still(doc.get("session")), weekly=still(doc.get("weekly")),
                scoped=[still(entry) for entry in doc.get("scoped") or []],
                burn=burn.get("velocity") if isinstance(burn, Mapping) else None)
    return json.dumps(view, sort_keys=True, default=str)


def demanded(folder: Path, now_epoch: float | None = None) -> bool:
    """Whether a Claude session asked for the usage within DEMAND_S."""
    try:
        mtime = (folder / DEMAND_NAME).stat().st_mtime
    except OSError:
        return False
    return (time.time() if now_epoch is None else now_epoch) - mtime < DEMAND_S


def _pick(entry: Any, keys: tuple[str, ...]) -> dict | None:
    if not isinstance(entry, Mapping) or entry.get("percent") is None:
        return None
    return {key: _round(entry.get(key)) for key in keys}


def _window_entry(entry: Any, extra: tuple[str, ...] = ()) -> dict | None:
    picked = _pick(entry, extra + _WINDOW_KEYS)
    if picked is not None:
        picked["projected_limit_at"] = _limit_at(entry)  # from the unrounded figures
    return picked


def _limit_at(entry: Mapping[str, Any]) -> str | None:
    """When the window reaches 100% if its average pace so far holds; None when that is after the reset.

    The pacing report's projected_end_pct is percent / elapsed share, so 100% falls at the share
    elapsed_pct / percent of the window.
    """
    percent, elapsed, end_pct = entry.get("percent"), entry.get("elapsed_pct"), entry.get("projected_end_pct")
    start, resets = parse_time(entry.get("window_start")), parse_time(entry.get("resets_at"))
    if None in (percent, elapsed, end_pct, start, resets) or not 0 < percent < 100 or end_pct <= 100:
        return None
    return _iso(start + (resets - start) * (elapsed / percent))


def _burn_entry(burn: BurnRate | None) -> dict | None:
    if burn is None:
        return None
    return {"output_tokens_per_min": round(burn.tokens_per_min, 1), "usd_per_hour": round(burn.usd_per_hour, 2),
            "velocity": burn.velocity, "window_minutes": burn.window_minutes}


def _extra_entry(spend: Spend | None) -> dict | None:
    if spend is None:
        return None
    return {"enabled": spend.enabled, "used": spend.used, "limit": spend.limit,
            "percent": spend.percent, "currency": spend.currency}


# ── Reading (the command-line side) ────────────────────────────────────────────

def read(path: Path) -> dict | None:
    """The document, or None when the file is missing, unreadable or of another schema."""
    for attempt in range(_TRIES):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):  # held by the writer's replace, or a scanner: try again shortly
            time.sleep(0.05 * (attempt + 1))
            continue
        return doc if isinstance(doc, dict) and doc.get("schema") == SCHEMA else None
    return None


def monitor_running(env: Mapping[str, str] | None = None) -> bool:
    """True while the app holds its single-instance mutex (a dev instance its -dev one)."""
    if sys.platform != "win32":
        return False
    dev = (os.environ if env is None else env).get(DEV_ENV) == "1"
    handle = _OpenMutexW(_SYNCHRONIZE, False, MUTEX_NAME + ("-dev" if dev else ""))
    if handle:
        _CloseHandle(handle)
        return True
    return ctypes.get_last_error() == _ERROR_ACCESS_DENIED  # it exists, but another account made it


def touch_demand(folder: Path) -> None:
    """Renew the demand, at most every DEMAND_TOUCH_S (it lasts DEMAND_S)."""
    path = folder / DEMAND_NAME
    with contextlib.suppress(OSError):
        if time.time() - path.stat().st_mtime < DEMAND_TOUCH_S:
            return
    with contextlib.suppress(OSError):
        folder.mkdir(parents=True, exist_ok=True)
        path.touch()


def current(folder: Path, *, now: datetime, fetch: bool, wait_s: float = 0.0, app_version: str = "",
            running: bool | None = None) -> tuple[dict | None, bool]:
    """(document or None, whether the app is running).

    The running app is the poller, so a current reading of its own is used as it is. One that says
    "paused" (the screen is locked) is first waited on for up to ``wait_s``: the demand touched here
    wakes the app within its pause recheck. With ``fetch`` set, no reading, or a stale one (the app
    is closed, or is an older version that writes no file), is replaced by one request of our own,
    which the callers within FRESH_S share. A stale reading from a running app that reports a
    failure (offline, rate-limited, signed out) is left to the app's own retries: a request of ours
    would fail the same way, and would add to a 429.
    """
    running = monitor_running() if running is None else running
    touch_demand(folder)
    path = folder / STATUS_NAME
    doc = read(path)
    if running and wait_s > 0 and _worth_waiting(doc, now):
        doc = _await_cycle(path, doc, wait_s)
    if running and doc is not None and (not is_stale(doc, now) or _app_is_retrying(doc)):
        return doc, True
    if not fetch or _reusable(doc, now):
        return doc, running
    with _fetch_lock(folder) as held:
        latest = read(path)  # another reader may have asked while this one waited for the lock
        if not held or _reusable(latest, now):
            return latest or doc, running
        doc = fetch_once(now, previous=latest or doc, app_version=app_version)
        newer = read(path)
        if _newer(newer, doc):  # the app wrote a later reading during the request: never write over it
            return newer, running
        try:
            write(doc, path)
        except OSError as exc:  # the answer is still good for this caller
            log.warning("could not keep the usage reading in %s (%s)", path, type(exc).__name__)
    return doc, running


@contextlib.contextmanager
def _fetch_lock(folder: Path) -> Iterator[bool]:
    """Yields True once this process holds the fetch lock, or False after FETCH_LOCK_S without it."""
    with file_lock(folder, LOCK_NAME, FETCH_LOCK_S) as held:
        yield held


@contextlib.contextmanager
def file_lock(folder: Path, name: str, wait_s: float) -> Iterator[bool]:
    """Yields True once this process holds the lock ``name`` in ``folder``, or False after ``wait_s``
    without it.

    A byte-range lock that Windows releases when its holder exits or is killed, as Claude Code does
    to a hook at its timeout, so no stale lock file can block the others. Elsewhere it is a no-op.
    """
    if sys.platform != "win32":
        yield True
        return
    import msvcrt

    try:
        folder.mkdir(parents=True, exist_ok=True)
        fd = os.open(folder / name, os.O_RDWR | os.O_CREAT)
    except OSError:  # an unwritable folder: go ahead, as before the lock existed
        yield True
        return
    try:
        deadline = time.monotonic() + wait_s
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    yield False
                    return
                time.sleep(0.05)
        try:
            yield True
        finally:
            os.lseek(fd, 0, os.SEEK_SET)
            with contextlib.suppress(OSError):
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    finally:
        os.close(fd)


def fetch_once(now: datetime, *, previous: Mapping[str, Any] | None, app_version: str = "") -> dict:
    """A document from one request to the usage API, made while the app is not running.

    The pace comes from the history the app stored while it ran. A failed request keeps the
    previous reading and says why, and its written_at stops the next caller asking again within FRESH_S.
    """
    from usage_monitor.data.usage_api import UsageClient, inspect_credentials

    plan = inspect_credentials()[0].plan  # the token in the same answer is dropped here
    result = UsageClient(version=app_version or "dev").fetch(now)
    if result.fresh and result.snapshot is not None:
        return build(status=result.status.value, status_detail=result.detail, usage=result.snapshot,
                     plan=plan, burn=None, pacing=_pacing(result.snapshot, now), now=now,
                     poll_interval_s=None, source=SOURCE_CLI, app_version=app_version)
    if previous:
        doc = dict(previous)
    else:
        doc = build(status=result.status.value, status_detail=result.detail, usage=None, plan=plan,
                    burn=None, pacing={}, now=now, poll_interval_s=None, source=SOURCE_CLI,
                    app_version=app_version)
    doc.update(source=SOURCE_CLI, written_at=_iso(now), status=result.status.value,
               status_detail=result.detail)
    return doc


def _pacing(usage: UsageSnapshot, now: datetime) -> dict:
    """The pacing report for a reading of our own, from the app's stored history when it can be opened."""
    from usage_monitor import paths
    from usage_monitor.data import analytics
    from usage_monitor.data.store import Store

    live = dataclasses.replace(usage, limits=[limit for limit in usage.limits
                                              if limit.resets_at is not None and limit.resets_at > now])
    for target in (paths.db_path(), ":memory:"):  # no history only costs the session slope
        try:
            store = Store(target)
            try:
                return analytics.pacing_report(store, now=now, usage=live)
            finally:
                store.close()
        except Exception:  # a locked or damaged store: fall back to no history
            log.warning("the usage history could not be read for the pace", exc_info=True)
    return {}


def _reusable(doc: Mapping[str, Any] | None, now: datetime) -> bool:
    """With the app closed: whether ``doc`` is young enough to answer without asking the API.

    A document written after ``now`` counts as young: a reader that waited for the fetch lock
    holds a ``now`` from before the wait.
    """
    if not doc:
        return False
    stamp = doc.get("written_at") if doc.get("source") == SOURCE_CLI else doc.get("usage_fetched_at")
    age = _age_s(stamp, now)
    return age is not None and -FRESH_S <= age < FRESH_S


def _newer(candidate: Mapping[str, Any] | None, doc: Mapping[str, Any]) -> bool:
    """Whether ``candidate`` holds a later reading than ``doc``."""
    if not candidate:
        return False
    theirs, ours = parse_time(candidate.get("usage_fetched_at")), parse_time(doc.get("usage_fetched_at"))
    return theirs is not None and (ours is None or theirs > ours)


def _worth_waiting(doc: Mapping[str, Any] | None, now: datetime) -> bool:
    """A paused reading older than a poll interval: the app, woken by the demand, will fetch a newer one."""
    if not doc or doc.get("status") != PAUSED:
        return False
    age = _age_s(doc.get("usage_fetched_at"), now)
    poll = doc.get("poll_interval_s")
    return age is None or age >= (poll if isinstance(poll, (int, float)) and poll > 0 else REFRESH_S)


def _app_is_retrying(doc: Mapping[str, Any]) -> bool:
    return doc.get("source") == SOURCE_MONITOR and doc.get("status") not in _HEALTHY


def _await_cycle(path: Path, doc: dict, wait_s: float) -> dict:
    seen = doc.get("written_at")
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        time.sleep(0.5)
        newer = read(path)
        if newer is not None and newer.get("written_at") != seen:
            return newer
    return doc


# ── The one-line summary ───────────────────────────────────────────────────────

def report(doc: Mapping[str, Any] | None, *, now: datetime, running: bool) -> dict:
    """``doc`` with what a reader needs to judge it: whether the app runs, the reading's age, a summary."""
    out = dict(doc) if doc else {"schema": SCHEMA}
    age = _age_s(out.get("usage_fetched_at"), now)
    out.update(monitor_running=running, usage_age_s=None if age is None else round(age),
               stale=is_stale(doc, now), summary=summary(doc, now=now, running=running))
    return out


def is_stale(doc: Mapping[str, Any] | None, now: datetime) -> bool:
    if not doc:
        return True
    age = _age_s(doc.get("usage_fetched_at"), now)
    poll = doc.get("poll_interval_s")
    limit = max(STALE_FLOOR_S, 2 * poll + 60) if isinstance(poll, (int, float)) else STALE_FLOOR_S
    return age is None or age > limit


def summary(doc: Mapping[str, Any] | None, *, now: datetime, running: bool) -> str:
    """One line for a person or a model: each limit with its reset and pace, then the burn rate."""
    if not doc or not doc.get("usage_fetched_at"):
        why = "the monitor has not fetched one yet" if running else "Claude Usage Monitor is not running"
        detail = (doc or {}).get("status_detail")
        return f"Claude plan usage: no reading ({why}{'; ' + detail if detail else ''})."
    parts = [_session_text(doc.get("session"), now), _window_text("weekly", doc.get("weekly"), now)]
    parts += [_window_text(_scoped_name(item), item, now) for item in doc.get("scoped") or []]
    parts.append(_burn_text(doc.get("burn")))
    age = _age_s(doc.get("usage_fetched_at"), now) or 0.0
    plan = (doc.get("plan") or {}).get("name") or "unknown plan"
    when = f"STALE, reading from {_span(age)} ago" if is_stale(doc, now) else f"reading from {_span(age)} ago"
    status = doc.get("status")
    note = ""
    if status not in ("live", None):
        note = f" [{status}: {doc.get('status_detail') or ''}]".replace(": ]", "]")
    return f"Claude plan usage ({plan}, {when}){note}: " + " | ".join(part for part in parts if part) + "."


def _session_text(entry: Any, now: datetime) -> str:
    if not isinstance(entry, Mapping) or entry.get("percent") is None:
        return ""
    text = f"session {_pct(entry['percent'])}{_reset_text(entry, now)}"
    if _ended(entry, now):  # what the reading said about a window that is over no longer holds
        return text
    verdict = entry.get("verdict")
    limit_at = parse_time(entry.get("projected_limit_at"))
    if verdict == "LIMITED":
        text += ", LIMIT REACHED"
    elif verdict == "AT_RISK" and limit_at is not None and limit_at > now:
        text += f", at this rate the limit is hit {when(limit_at, now)}"
    elif verdict == "SAFE":
        text += ", safe at this rate"
    return text


def _window_text(name: str, entry: Any, now: datetime) -> str:
    if not isinstance(entry, Mapping) or entry.get("percent") is None:
        return ""
    text = f"{name} {_pct(entry['percent'])}{_reset_text(entry, now)}"
    if _ended(entry, now):
        return text
    delta = entry.get("pace_delta_pp")
    if entry.get("percent", 0) >= 100:
        text += ", LIMIT REACHED"
    elif entry.get("verdict") == "AHEAD" and delta is not None:
        text += f", {delta:.0f} pts ahead of an even pace"
    elif entry.get("verdict") == "BEHIND" and delta is not None:
        text += f", {-delta:.0f} pts under an even pace"
    elif entry.get("verdict") == "ON_PACE":
        text += ", on an even pace"
    limit_at = parse_time(entry.get("projected_limit_at"))
    if limit_at is not None and limit_at > now:
        text += f", at its average pace so far 100% is reached {when(limit_at, now)}"
    return text


def _ended(entry: Mapping[str, Any], now: datetime) -> bool:
    """Whether the window of ``entry`` has reset since the reading."""
    resets = parse_time(entry.get("resets_at"))
    return resets is not None and resets <= now


def _reset_text(entry: Mapping[str, Any], now: datetime) -> str:
    resets = parse_time(entry.get("resets_at"))
    if resets is None:
        return ""
    if resets <= now:
        return f" (window reset {when(resets, now)}; no newer reading)"
    return f", resets {when(resets, now)}"


def _scoped_name(entry: Any) -> str:
    if not isinstance(entry, Mapping):
        return ""
    model = entry.get("model")
    return f"{model} weekly" if model else str(entry.get("label") or "scoped").lower()


def _burn_text(burn: Any) -> str:
    if not isinstance(burn, Mapping) or burn.get("velocity") in (None, "IDLE"):
        return ""
    rate = burn.get("output_tokens_per_min") or 0.0
    shown = f"{rate / 1000:.1f}k" if rate >= 1000 else f"{rate:.0f}"
    return f"burn {shown} output tokens/min ({str(burn['velocity']).lower()})"


def when(moment: datetime, now: datetime) -> str:
    """'in 1h 25m (14:30)' or '2h ago (Mon 09:00)', the clock in local time."""
    seconds = (moment - now).total_seconds()
    local, today = moment.astimezone(), now.astimezone().date()
    clock = local.strftime("%H:%M") if local.date() == today else local.strftime("%a %H:%M")
    return f"in {_span(seconds)} ({clock})" if seconds >= 0 else f"{_span(-seconds)} ago ({clock})"


def _span(seconds: float) -> str:
    minutes = int(max(0.0, seconds) // 60)
    if minutes < 1:
        return "under a minute"
    days, rest = divmod(minutes, 24 * 60)
    hours, mins = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h"
    return f"{hours}h {mins}m" if hours else f"{mins}m"


def _pct(value: float) -> str:
    return f"{value:.0f}%" if abs(value - round(value)) < 0.05 else f"{value:.1f}%"


# ── Small helpers ─────────────────────────────────────────────────────────────

def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_time(value: Any) -> datetime | None:
    """An ISO time as the readers write it ("...Z"; fromisoformat takes a Z only from Python 3.11)."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _age_s(stamp: Any, now: datetime) -> float | None:
    moment = parse_time(stamp)
    return None if moment is None else (now - moment).total_seconds()


def _round(value: Any) -> Any:
    return round(value, 2) if isinstance(value, float) else value

