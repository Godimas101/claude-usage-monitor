"""SQLite store: transcript items, usage snapshots and ingest bookmarks in one WAL database.

A Store owns one connection, guarded by a lock. Other threads open their own Store on the
same file to read; WAL keeps those readers from blocking the writer.

Requests dedupe by key. A duplicate merges into the stored row: the max of every token count,
the first non-null metadata, and a cost recomputed from the merged tokens. Other item kinds
dedupe on their own keys (the first write wins) except session titles (the latest non-empty
title wins).

Times are stored as UTC epoch seconds (REAL). Callers pass and receive aware datetimes.
"""
from __future__ import annotations

import contextlib
import json
import re
import sqlite3
import threading
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from usage_monitor.data.models import (
    ApiErrorEvent,
    CodeChange,
    Compaction,
    CostBreakdown,
    FileState,
    GitEvent,
    Limit,
    PullRequest,
    RequestRecord,
    SessionMeta,
    TokenTotals,
    ToolCall,
    ToolError,
    TranscriptItem,
    UsageSnapshot,
)
from usage_monitor.data.pricing import Pricing
from usage_monitor.data.transcripts import SYNTHETIC_MODEL, UNKNOWN_MODEL
from usage_monitor.log import get_logger

log = get_logger(__name__)

SCHEMA_VERSION = 3
SNAPSHOT_RETENTION = timedelta(days=180)
_IN_CHUNK = 500             # bound parameters per IN (...) query
_REPRICE_CHUNK = 5000       # requests repriced per transaction
_BUSY_TIMEOUT_S = 30.0


class StoreError(RuntimeError):
    """The database cannot be used by this build (for example, it was written by a newer one)."""


# ── Schema ────────────────────────────────────────────────────────────────────────

# RequestRecord fields in column order. Every column is named after its field.
_RECORD_FIELDS: tuple[str, ...] = (
    "key", "message_id", "request_id", "ts", "session_id", "project", "cwd", "git_branch",
    "is_subagent", "agent_id", "model", "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_write_5m_tokens", "cache_write_1h_tokens", "thinking_tokens", "web_search_requests",
    "web_fetch_requests", "speed", "service_tier", "effort", "entrypoint", "cc_version",
)
_TOKEN_FIELDS: tuple[str, ...] = (
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_5m_tokens",
    "cache_write_1h_tokens", "thinking_tokens", "web_search_requests", "web_fetch_requests",
)
_METADATA_FIELDS: tuple[str, ...] = tuple(
    name for name in _RECORD_FIELDS if name not in _TOKEN_FIELDS and name != "key")
_COST_COLUMNS: tuple[str, ...] = (
    "cost_input", "cost_output", "cost_cache_read", "cost_cache_write_5m", "cost_cache_write_1h",
    "cost_total", "nocache_cost", "priced",
)

_SCHEMA_META = "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"

_SCHEMA_V1: tuple[str, ...] = (
    "CREATE TABLE IF NOT EXISTS file_state ("
    " path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime REAL NOT NULL, offset INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS requests ("
    " key TEXT PRIMARY KEY,"
    " message_id TEXT, request_id TEXT, ts REAL NOT NULL, session_id TEXT,"
    " project TEXT NOT NULL, cwd TEXT, git_branch TEXT,"
    " is_subagent INTEGER NOT NULL DEFAULT 0, agent_id TEXT, model TEXT NOT NULL,"
    " input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,"
    " cache_read_tokens INTEGER NOT NULL DEFAULT 0,"
    " cache_write_5m_tokens INTEGER NOT NULL DEFAULT 0,"
    " cache_write_1h_tokens INTEGER NOT NULL DEFAULT 0,"
    " thinking_tokens INTEGER NOT NULL DEFAULT 0,"
    " web_search_requests INTEGER NOT NULL DEFAULT 0,"
    " web_fetch_requests INTEGER NOT NULL DEFAULT 0,"
    " speed TEXT, service_tier TEXT, effort TEXT, entrypoint TEXT, cc_version TEXT,"
    " cost_input REAL NOT NULL DEFAULT 0, cost_output REAL NOT NULL DEFAULT 0,"
    " cost_cache_read REAL NOT NULL DEFAULT 0, cost_cache_write_5m REAL NOT NULL DEFAULT 0,"
    " cost_cache_write_1h REAL NOT NULL DEFAULT 0, cost_total REAL NOT NULL DEFAULT 0,"
    " nocache_cost REAL NOT NULL DEFAULT 0, priced INTEGER NOT NULL DEFAULT 0,"
    " pricing_version TEXT)",
    "CREATE INDEX IF NOT EXISTS requests_ts ON requests (ts)",
    "CREATE INDEX IF NOT EXISTS requests_session ON requests (session_id)",
    "CREATE INDEX IF NOT EXISTS requests_model ON requests (model)",
    "CREATE INDEX IF NOT EXISTS requests_project ON requests (project)",
    "CREATE TABLE IF NOT EXISTS tool_calls ("
    " tool_use_id TEXT PRIMARY KEY, name TEXT NOT NULL, request_key TEXT, session_id TEXT,"
    " ts REAL NOT NULL, is_subagent INTEGER NOT NULL DEFAULT 0)",
    "CREATE INDEX IF NOT EXISTS tool_calls_ts ON tool_calls (ts)",
    "CREATE INDEX IF NOT EXISTS tool_calls_session ON tool_calls (session_id)",
    "CREATE TABLE IF NOT EXISTS code_changes ("
    " tool_use_id TEXT PRIMARY KEY, session_id TEXT, ts REAL NOT NULL,"
    " lines_added INTEGER NOT NULL, lines_removed INTEGER NOT NULL, is_new_file INTEGER NOT NULL)",
    "CREATE INDEX IF NOT EXISTS code_changes_ts ON code_changes (ts)",
    "CREATE INDEX IF NOT EXISTS code_changes_session ON code_changes (session_id)",
    "CREATE TABLE IF NOT EXISTS git_events ("
    " tool_use_id TEXT PRIMARY KEY, session_id TEXT, ts REAL NOT NULL,"
    " is_commit INTEGER NOT NULL, is_push INTEGER NOT NULL, is_pr INTEGER NOT NULL)",
    "CREATE INDEX IF NOT EXISTS git_events_ts ON git_events (ts)",
    "CREATE INDEX IF NOT EXISTS git_events_session ON git_events (session_id)",
    "CREATE TABLE IF NOT EXISTS pull_requests ("
    " url TEXT PRIMARY KEY, number INTEGER, repository TEXT, session_id TEXT, ts REAL)",
    "CREATE INDEX IF NOT EXISTS pull_requests_session ON pull_requests (session_id)",
    "CREATE TABLE IF NOT EXISTS session_titles ("
    " session_id TEXT PRIMARY KEY, project TEXT NOT NULL, ai_title TEXT, custom_title TEXT)",
    "CREATE TABLE IF NOT EXISTS compactions ("
    " uuid TEXT PRIMARY KEY, session_id TEXT, ts REAL NOT NULL, trigger_kind TEXT,"
    " pre_tokens INTEGER, post_tokens INTEGER)",
    "CREATE INDEX IF NOT EXISTS compactions_ts ON compactions (ts)",
    "CREATE INDEX IF NOT EXISTS compactions_session ON compactions (session_id)",
    "CREATE TABLE IF NOT EXISTS api_errors ("
    " uuid TEXT PRIMARY KEY, session_id TEXT, ts REAL NOT NULL)",
    "CREATE INDEX IF NOT EXISTS api_errors_ts ON api_errors (ts)",
    "CREATE INDEX IF NOT EXISTS api_errors_session ON api_errors (session_id)",
    "CREATE TABLE IF NOT EXISTS usage_snapshots ("
    " ts REAL PRIMARY KEY, session_pct REAL, session_resets_at REAL,"
    " weekly_pct REAL, weekly_resets_at REAL, limits_json TEXT)",
)


def _create_v1(conn: sqlite3.Connection) -> None:
    for statement in _SCHEMA_V1:
        conn.execute(statement)


def _add_tail_to_file_state(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE file_state ADD COLUMN tail TEXT")


def _add_git_ops_and_tool_errors(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE tool_calls ADD COLUMN git_ops TEXT")
    conn.execute("CREATE TABLE IF NOT EXISTS tool_errors ("
                 " tool_use_id TEXT PRIMARY KEY, session_id TEXT, ts REAL NOT NULL)")
    conn.execute("CREATE INDEX IF NOT EXISTS tool_errors_ts ON tool_errors (ts)")
    # Existing rows lack git_ops and tool_errors. With no bookmarks the next ingest re-reads
    # every transcript, and dedupe keeps that safe while the upsert fills git_ops.
    conn.execute("DELETE FROM file_state")


# Forward migrations: _MIGRATIONS[n] upgrades a version n-1 database to version n.
_MIGRATIONS: dict[int, Any] = {1: _create_v1, 2: _add_tail_to_file_state,
                               3: _add_git_ops_and_tool_errors}

_RECORD_SELECT = ", ".join(_RECORD_FIELDS)
_REQUEST_UPSERT_SQL = (
    "INSERT OR REPLACE INTO requests (" + ", ".join(_RECORD_FIELDS + _COST_COLUMNS + ("pricing_version",))
    + ") VALUES (" + ", ".join("?" * (len(_RECORD_FIELDS) + len(_COST_COLUMNS) + 1)) + ")"
)
_REPRICE_SQL = (
    "UPDATE requests SET " + ", ".join(f"{col} = ?" for col in _COST_COLUMNS + ("pricing_version",))
    + " WHERE key = ?"
)
# A repeat sighting changes nothing except to fill git_ops where the stored row has none.
_TOOL_CALL_SQL = ("INSERT INTO tool_calls (tool_use_id, name, request_key, session_id, ts, "
                  "is_subagent, git_ops) VALUES (?, ?, ?, ?, ?, ?, ?) "
                  "ON CONFLICT (tool_use_id) DO UPDATE SET git_ops = excluded.git_ops "
                  "WHERE tool_calls.git_ops IS NULL AND excluded.git_ops IS NOT NULL")
_CODE_CHANGE_SQL = ("INSERT OR IGNORE INTO code_changes (tool_use_id, session_id, ts, lines_added, "
                    "lines_removed, is_new_file) VALUES (?, ?, ?, ?, ?, ?)")
_GIT_EVENT_SQL = ("INSERT OR IGNORE INTO git_events (tool_use_id, session_id, ts, is_commit, is_push, "
                  "is_pr) VALUES (?, ?, ?, ?, ?, ?)")
_TOOL_ERROR_SQL = "INSERT OR IGNORE INTO tool_errors (tool_use_id, session_id, ts) VALUES (?, ?, ?)"
# Keeps the earliest ts for a URL (a PR counts where it first appeared) and the session that
# earliest sighting belongs to; number and repository only fill in while they are still null.
# All right-hand sides read the row as it was before the update, so the ts test is the same
# for every column.
_PULL_REQUEST_SQL = (
    "INSERT INTO pull_requests (url, number, repository, session_id, ts) VALUES (?, ?, ?, ?, ?) "
    "ON CONFLICT (url) DO UPDATE SET "
    "ts = CASE WHEN pull_requests.ts IS NULL THEN excluded.ts "
    "WHEN excluded.ts IS NULL THEN pull_requests.ts "
    "ELSE MIN(pull_requests.ts, excluded.ts) END, "
    "number = COALESCE(pull_requests.number, excluded.number), "
    "repository = COALESCE(pull_requests.repository, excluded.repository), "
    "session_id = CASE WHEN excluded.session_id IS NOT NULL AND excluded.ts IS NOT NULL "
    "AND (pull_requests.ts IS NULL OR excluded.ts < pull_requests.ts) THEN excluded.session_id "
    "ELSE COALESCE(pull_requests.session_id, excluded.session_id) END"
)
# Latest non-empty title wins; a later record without a title keeps the stored one.
_TITLE_SQL = (
    "INSERT INTO session_titles (session_id, project, ai_title, custom_title) VALUES (?, ?, ?, ?) "
    "ON CONFLICT (session_id) DO UPDATE SET project = excluded.project, "
    "ai_title = COALESCE(NULLIF(excluded.ai_title, ''), session_titles.ai_title), "
    "custom_title = COALESCE(NULLIF(excluded.custom_title, ''), session_titles.custom_title)"
)
_COMPACTION_SQL = ("INSERT OR IGNORE INTO compactions (uuid, session_id, ts, trigger_kind, pre_tokens, "
                   "post_tokens) VALUES (?, ?, ?, ?, ?, ?)")
_API_ERROR_SQL = "INSERT OR IGNORE INTO api_errors (uuid, session_id, ts) VALUES (?, ?, ?)"


# Token sums are REAL: SQLite's integer SUM fails with "integer overflow" once the total passes 2**63.
_AGGREGATES = (
    "COUNT(*), COALESCE(SUM(CAST(input_tokens AS REAL)), 0), "
    "COALESCE(SUM(CAST(output_tokens AS REAL)), 0), "
    "COALESCE(SUM(CAST(cache_read_tokens AS REAL)), 0), "
    "COALESCE(SUM(CAST(cache_write_5m_tokens AS REAL)), 0), "
    "COALESCE(SUM(CAST(cache_write_1h_tokens AS REAL)), 0), "
    "COALESCE(SUM(CAST(thinking_tokens AS REAL)), 0), "
    "COALESCE(SUM(cost_total), 0.0), COALESCE(SUM(nocache_cost), 0.0), "
    "COALESCE(SUM(CASE WHEN priced = 0 THEN 1 ELSE 0 END), 0)"
)

_LATEST_TS = "latest_usage_ts"
_LATEST_RAW = "latest_usage_raw"
_PRICING_VERSION = "pricing_version"
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
# SELECT or WITH, after any whitespace and comments.
_READ_ONLY_START = re.compile(r"(?is)(?:\s|--[^\n]*(?:\n|$)|/\*.*?\*/)*(?:select|with)\b")


# ── Store ─────────────────────────────────────────────────────────────────────────

class Store:
    """One SQLite database. Pass ":memory:" for an unshared database in tests."""

    def __init__(self, path: Path | str) -> None:
        self._lock = threading.RLock()
        self._depth = 0
        self._path = ":memory:" if str(path) == ":memory:" else str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            self._path, timeout=_BUSY_TIMEOUT_S, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        try:
            self._configure()
            self._migrate()
        except BaseException:
            self._conn.close()
            raise

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the connection. Later calls raise sqlite3.ProgrammingError."""
        with self._lock:
            self._conn.close()

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        """Commit the enclosed writes together. Nested use runs as a savepoint."""
        with self._lock:
            outer = self._depth == 0
            name = f"store_sp{self._depth}"
            self._conn.execute("BEGIN IMMEDIATE" if outer else f"SAVEPOINT {name}")
            self._depth += 1
            try:
                yield
                self._conn.execute("COMMIT" if outer else f"RELEASE SAVEPOINT {name}")
            except BaseException:
                self._roll_back(outer, name)
                raise
            finally:
                self._depth -= 1

    # ── Schema and metadata ──────────────────────────────────────────────────────

    def schema_version(self) -> int:
        value = self._meta("schema_version")
        try:
            return int(value) if value is not None else 0
        except ValueError:
            return 0

    def pricing_version(self) -> str | None:
        """The pricing version every stored request is priced under (set by reprice)."""
        return self._meta(_PRICING_VERSION)

    def table_counts(self) -> dict[str, int]:
        """Row count of every table, for diagnostics."""
        names = [row[0] for row in self._rows(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name")]
        return {name: self._rows(f'SELECT COUNT(*) FROM "{name}"')[0][0] for name in names}

    # ── Ingest bookkeeping ───────────────────────────────────────────────────────

    def file_state(self, path: str) -> FileState | None:
        rows = self._rows("SELECT path, size, mtime, offset, tail FROM file_state WHERE path = ?", (path,))
        return _file_state(rows[0]) if rows else None

    def file_states(self) -> dict[str, FileState]:
        return {row["path"]: _file_state(row) for row in self._rows(
            "SELECT path, size, mtime, offset, tail FROM file_state")}

    def save_file_state(self, st: FileState) -> None:
        with self.transaction():
            self._conn.execute(
                "INSERT OR REPLACE INTO file_state (path, size, mtime, offset, tail) VALUES (?, ?, ?, ?, ?)",
                (st.path, st.size, st.mtime, st.offset, st.tail))

    def known_files(self) -> list[str]:
        return [row[0] for row in self._rows("SELECT path FROM file_state ORDER BY path")]

    def forget_file(self, path: str) -> None:
        """Drop a file's bookmark. Its stored items stay."""
        with self.transaction():
            self._conn.execute("DELETE FROM file_state WHERE path = ?", (path,))

    # ── Writes ───────────────────────────────────────────────────────────────────

    def add_items(self, items: Iterable[TranscriptItem], pricing: Pricing) -> dict[str, int]:
        """Store one batch in a single transaction. Returns the stored count per item kind.

        A row that SQLite refuses is skipped and logged, so it cannot block the rest of the batch.
        """
        groups: dict[str, list[Any]] = {}
        for item in items:
            kind = _KIND_BY_TYPE.get(type(item))
            if kind is None:
                log.debug("store: ignored an item of type %s", type(item).__name__)
                continue
            groups.setdefault(kind, []).append(item)
        if not groups:
            return {}
        stored: dict[str, int] = {}
        with self.transaction():
            self._ensure_pricing_version(pricing.version)
            for kind, group in groups.items():
                if kind == "request":
                    refused = self._upsert_requests(group, pricing)
                else:
                    sql, row_of = _ROW_WRITES[kind]
                    refused = self._insert_rows(sql, [row_of(item) for item in group])
                stored[kind] = len(group) - refused
        skipped = sum(len(groups[kind]) - count for kind, count in stored.items())
        if skipped:
            log.warning("store: %d item(s) that SQLite cannot store were skipped", skipped)
        return stored

    def reprice(self, pricing: Pricing) -> int:
        """Recompute the cost columns of every request under ``pricing``. Returns the row count."""
        done = 0
        after: str | None = None
        while True:
            if after is None:
                rows = self._rows(f"SELECT {_RECORD_SELECT} FROM requests ORDER BY key LIMIT ?",
                                  (_REPRICE_CHUNK,))
            else:
                rows = self._rows(f"SELECT {_RECORD_SELECT} FROM requests WHERE key > ? "
                                  "ORDER BY key LIMIT ?", (after, _REPRICE_CHUNK))
            if not rows:
                break
            updates = []
            for row in rows:
                rec = _record_from_row(row)
                updates.append((*_cost_params(pricing.cost(rec)), pricing.version, rec.key))
            with self.transaction():
                self._conn.executemany(_REPRICE_SQL, updates)
            done += len(rows)
            after = rows[-1]["key"]
        with self.transaction():
            self._set_meta(_PRICING_VERSION, pricing.version)
        return done

    def save_usage_snapshot(self, snap: UsageSnapshot) -> None:
        """Store one usage fetch. Keeps the newest full response and prunes snapshots over 180 days."""
        ts = _to_epoch(snap.fetched_at)
        session, weekly = snap.session, snap.weekly
        row = (
            ts,
            session.percent if session else None,
            _to_epoch_or_none(session.resets_at) if session else None,
            weekly.percent if weekly else None,
            _to_epoch_or_none(weekly.resets_at) if weekly else None,
            json.dumps([_limit_json(item) for item in snap.limits], separators=(",", ":")),
        )
        with self.transaction():
            self._conn.execute(
                "INSERT OR REPLACE INTO usage_snapshots (ts, session_pct, session_resets_at, "
                "weekly_pct, weekly_resets_at, limits_json) VALUES (?, ?, ?, ?, ?, ?)", row)
            self._conn.execute("DELETE FROM usage_snapshots WHERE ts < ?",
                               (ts - SNAPSHOT_RETENTION.total_seconds(),))
            stored = self._meta(_LATEST_TS)
            if stored is None or ts >= float(stored):
                self._set_meta(_LATEST_TS, repr(ts))
                self._set_meta(_LATEST_RAW, json.dumps(snap.raw, default=str, separators=(",", ":")))

    # ── Reads ────────────────────────────────────────────────────────────────────

    def totals(self, start: datetime | None, end: datetime | None, *,
               subagent: bool | None = None) -> TokenTotals:
        """Totals over [start, end). None leaves that side open. subagent: None = both kinds."""
        where, params = _window(start, end, subagent)
        rows = self._rows(f"SELECT {_AGGREGATES} FROM requests{where}", params)
        return _totals(tuple(rows[0]))

    def totals_by_model(self, start: datetime | None, end: datetime | None, *,
                        subagent: bool | None = None) -> dict[str, TokenTotals]:
        """Per-model totals over [start, end), keyed by the raw model id."""
        where, params = _window(start, end, subagent)
        rows = self._rows(f"SELECT model, {_AGGREGATES} FROM requests{where} GROUP BY model", params)
        return {row[0]: _totals(tuple(row)[1:]) for row in rows}

    def first_request_ts(self) -> datetime | None:
        value = self._rows("SELECT MIN(ts) FROM requests")[0][0]
        return None if value is None else _from_epoch(value)

    def unpriced_models(self) -> list[str]:
        """Model ids with at least one request that has no price row (synthetic excluded)."""
        rows = self._rows("SELECT DISTINCT model FROM requests WHERE priced = 0 AND model <> ? "
                          "ORDER BY model", (SYNTHETIC_MODEL,))
        return [row[0] for row in rows]

    def recent_requests(self, since: datetime) -> list[tuple[datetime, int, float]]:
        """(ts, output tokens, API-equivalent cost) for requests at or after ``since``, oldest first."""
        rows = self._rows("SELECT ts, output_tokens, cost_total FROM requests WHERE ts >= ? ORDER BY ts",
                          (_to_epoch(since),))
        return [(_from_epoch(row[0]), int(row[1]), float(row[2])) for row in rows]

    def session_meta(self, session_id: str) -> SessionMeta | None:
        rows = self._rows("SELECT session_id, project, ai_title, custom_title FROM session_titles "
                          "WHERE session_id = ?", (session_id,))
        if not rows:
            return None
        row = rows[0]
        return SessionMeta(session_id=row[0], project=row[1], ai_title=row[2], custom_title=row[3])

    def usage_snapshots(self, since: datetime) -> list[dict[str, Any]]:
        """Snapshots at or after ``since``, oldest first, as dicts with datetimes for every time."""
        rows = self._rows(
            "SELECT ts, session_pct, session_resets_at, weekly_pct, weekly_resets_at, limits_json "
            "FROM usage_snapshots WHERE ts >= ? ORDER BY ts", (_to_epoch(since),))
        return [{
            "ts": _from_epoch(row[0]),
            "session_pct": row[1],
            "session_resets_at": _from_epoch_or_none(row[2]),
            "weekly_pct": row[3],
            "weekly_resets_at": _from_epoch_or_none(row[4]),
            "limits": _limits_from_json(row[5]),
        } for row in rows]

    def latest_usage_raw(self) -> tuple[datetime, dict[str, Any]] | None:
        """The newest full usage response and when it was fetched, or None."""
        stamp, text = self._meta(_LATEST_TS), self._meta(_LATEST_RAW)
        if stamp is None or text is None:
            return None
        try:
            when = _from_epoch(float(stamp))
            raw = json.loads(text)
        except (ValueError, TypeError, OverflowError, OSError):
            log.warning("stored usage response is unreadable; ignored")
            return None
        return (when, raw) if isinstance(raw, dict) else None

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        """Run one read-only SELECT or WITH statement and return all its rows.

        Anything else, or more than one statement, raises ValueError (sqlite3 itself refuses a
        second statement, so a ';' inside a string literal or comment is fine). The connection is
        in query_only mode for the call, so a WITH ... DELETE fails rather than writing.
        """
        text = sql.strip()
        if not _READ_ONLY_START.match(text):
            raise ValueError("query() accepts one SELECT or WITH statement")
        with self._lock:
            previous = self._conn.execute("PRAGMA query_only").fetchone()[0]
            self._conn.execute("PRAGMA query_only = 1")
            try:
                return self._conn.execute(text, params).fetchall()
            except (sqlite3.ProgrammingError, sqlite3.Warning) as exc:
                if "one statement" in str(exc):  # ProgrammingError since 3.12, Warning before
                    raise ValueError("query() accepts one SELECT or WITH statement") from exc
                raise
            finally:
                self._conn.execute(f"PRAGMA query_only = {int(previous)}")

    # ── Internals ────────────────────────────────────────────────────────────────

    def _configure(self) -> None:
        self._conn.execute("PRAGMA foreign_keys = OFF").fetchall()
        self._conn.execute("PRAGMA journal_mode = WAL").fetchall()
        self._conn.execute("PRAGMA synchronous = NORMAL").fetchall()
        self._conn.execute("PRAGMA temp_store = MEMORY").fetchall()
        self._conn.execute(_SCHEMA_META)

    def _migrate(self) -> None:
        current = self.schema_version()
        if current > SCHEMA_VERSION:
            raise StoreError(f"database schema {current} is newer than this build ({SCHEMA_VERSION})")
        for version in range(current + 1, SCHEMA_VERSION + 1):
            with self.transaction():
                _MIGRATIONS[version](self._conn)
                self._set_meta("schema_version", str(version))
        if current < SCHEMA_VERSION:
            log.info("store schema upgraded from %d to %d", current, SCHEMA_VERSION)

    def _roll_back(self, outer: bool, name: str) -> None:
        try:
            if outer:
                self._conn.execute("ROLLBACK")
            else:
                self._conn.execute(f"ROLLBACK TO SAVEPOINT {name}")
                self._conn.execute(f"RELEASE SAVEPOINT {name}")
        except sqlite3.Error as exc:
            log.warning("store rollback failed: %s", type(exc).__name__)

    def _rows(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _meta(self, key: str) -> str | None:
        rows = self._rows("SELECT value FROM meta WHERE key = ?", (key,))
        return rows[0][0] if rows else None

    def _set_meta(self, key: str, value: str) -> None:
        self._conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))

    def _ensure_pricing_version(self, version: str) -> None:
        # Only a fresh database takes a version here; a changed one waits for reprice().
        self._conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)", (_PRICING_VERSION, version))

    def _upsert_requests(self, records: list[RequestRecord], pricing: Pricing) -> int:
        """Upsert one batch of requests. Returns how many rows SQLite refused."""
        batch: dict[str, RequestRecord] = {}
        for rec in records:
            earlier = batch.get(rec.key)
            batch[rec.key] = rec if earlier is None else _merge(earlier, rec)
        stored = self._stored_requests(list(batch))
        rows = []
        for key, rec in batch.items():
            old = stored.get(key)
            rows.append(_request_row(rec if old is None else _merge(old, rec), pricing))
        return self._insert_rows(_REQUEST_UPSERT_SQL, rows)

    def _insert_rows(self, sql: str, rows: list[tuple[Any, ...]]) -> int:
        """Run ``sql`` once per row and return how many rows SQLite refused to bind.

        The batch runs first, which is the normal path. Only when it fails is each row run alone,
        so one value that cannot be bound (an integer past 64 bits, a lone surrogate) skips just its row.
        """
        if not rows:
            return 0
        self._conn.execute("SAVEPOINT store_rows")
        try:
            self._conn.executemany(sql, rows)
        except (OverflowError, UnicodeEncodeError):
            self._conn.execute("ROLLBACK TO store_rows")
            self._conn.execute("RELEASE store_rows")
        else:
            self._conn.execute("RELEASE store_rows")
            return 0
        refused = 0
        for row in rows:
            try:
                self._conn.execute(sql, row)
            except (OverflowError, UnicodeEncodeError):  # raised while binding, before anything runs
                refused += 1
        return refused

    def _stored_requests(self, keys: list[str]) -> dict[str, RequestRecord]:
        found: dict[str, RequestRecord] = {}
        for start in range(0, len(keys), _IN_CHUNK):
            chunk = keys[start:start + _IN_CHUNK]
            marks = ", ".join("?" * len(chunk))
            for row in self._rows(f"SELECT {_RECORD_SELECT} FROM requests WHERE key IN ({marks})", chunk):
                rec = _record_from_row(row)
                found[rec.key] = rec
        return found


# ── Row builders and converters ───────────────────────────────────────────────────

_KIND_BY_TYPE: dict[type, str] = {
    RequestRecord: "request",
    ToolCall: "tool_call",
    CodeChange: "code_change",
    GitEvent: "git_event",
    ToolError: "tool_error",
    PullRequest: "pull_request",
    SessionMeta: "session_meta",
    Compaction: "compaction",
    ApiErrorEvent: "api_error",
}


def _merge(first: RequestRecord, second: RequestRecord) -> RequestRecord:
    """One record from two sightings of a request: max of each token count, first non-null metadata."""
    changes: dict[str, Any] = {name: max(getattr(first, name), getattr(second, name)) for name in _TOKEN_FIELDS}
    for name in _METADATA_FIELDS:
        value = getattr(first, name)
        changes[name] = getattr(second, name) if _is_missing(name, value) else value
    return replace(first, **changes)


def _is_missing(name: str, value: Any) -> bool:
    # The transcript parser's placeholder model id counts as missing, so a real id can replace it.
    return value is None or (name == "model" and value == UNKNOWN_MODEL)


def _request_row(rec: RequestRecord, pricing: Pricing) -> tuple[Any, ...]:
    return (*_record_params(rec), *_cost_params(pricing.cost(rec)), pricing.version)


def _record_params(rec: RequestRecord) -> tuple[Any, ...]:
    return (
        rec.key, rec.message_id, rec.request_id, _to_epoch(rec.ts), rec.session_id, rec.project,
        rec.cwd, rec.git_branch, int(rec.is_subagent), rec.agent_id, rec.model,
        rec.input_tokens, rec.output_tokens, rec.cache_read_tokens,
        rec.cache_write_5m_tokens, rec.cache_write_1h_tokens, rec.thinking_tokens,
        rec.web_search_requests, rec.web_fetch_requests,
        rec.speed, rec.service_tier, rec.effort, rec.entrypoint, rec.cc_version,
    )


def _cost_params(cost: CostBreakdown) -> tuple[Any, ...]:
    return (cost.input, cost.output, cost.cache_read, cost.cache_write_5m, cost.cache_write_1h,
            cost.total, cost.nocache_total, int(cost.priced))


def _record_from_row(row: sqlite3.Row) -> RequestRecord:
    return RequestRecord(
        key=row["key"], message_id=row["message_id"], request_id=row["request_id"],
        ts=_from_epoch(row["ts"]), session_id=row["session_id"], project=row["project"],
        cwd=row["cwd"], git_branch=row["git_branch"], is_subagent=bool(row["is_subagent"]),
        agent_id=row["agent_id"], model=row["model"],
        input_tokens=row["input_tokens"], output_tokens=row["output_tokens"],
        cache_read_tokens=row["cache_read_tokens"],
        cache_write_5m_tokens=row["cache_write_5m_tokens"],
        cache_write_1h_tokens=row["cache_write_1h_tokens"],
        thinking_tokens=row["thinking_tokens"], web_search_requests=row["web_search_requests"],
        web_fetch_requests=row["web_fetch_requests"], speed=row["speed"],
        service_tier=row["service_tier"], effort=row["effort"], entrypoint=row["entrypoint"],
        cc_version=row["cc_version"],
    )


def _tool_call_row(item: ToolCall) -> tuple[Any, ...]:
    return (item.tool_use_id, item.name, item.request_key, item.session_id,
            _to_epoch(item.ts), int(item.is_subagent), item.git_ops)


def _tool_error_row(item: ToolError) -> tuple[Any, ...]:
    return (item.tool_use_id, item.session_id, _to_epoch(item.ts))


def _code_change_row(item: CodeChange) -> tuple[Any, ...]:
    return (item.tool_use_id, item.session_id, _to_epoch(item.ts), item.lines_added,
            item.lines_removed, int(item.is_new_file))


def _git_event_row(item: GitEvent) -> tuple[Any, ...]:
    return (item.tool_use_id, item.session_id, _to_epoch(item.ts),
            int(item.commit), int(item.push), int(item.pr))


def _pull_request_row(item: PullRequest) -> tuple[Any, ...]:
    return (item.url, item.number, item.repository, item.session_id, _to_epoch_or_none(item.ts))


def _title_row(item: SessionMeta) -> tuple[Any, ...]:
    return (item.session_id, item.project, item.ai_title, item.custom_title)


def _compaction_row(item: Compaction) -> tuple[Any, ...]:
    return (item.uuid, item.session_id, _to_epoch(item.ts), item.trigger,
            item.pre_tokens, item.post_tokens)


def _api_error_row(item: ApiErrorEvent) -> tuple[Any, ...]:
    return (item.uuid, item.session_id, _to_epoch(item.ts))


# Statement and row builder for each item kind that is stored one row per item.
_ROW_WRITES: dict[str, tuple[str, Any]] = {
    "tool_call": (_TOOL_CALL_SQL, _tool_call_row),
    "code_change": (_CODE_CHANGE_SQL, _code_change_row),
    "git_event": (_GIT_EVENT_SQL, _git_event_row),
    "tool_error": (_TOOL_ERROR_SQL, _tool_error_row),
    "pull_request": (_PULL_REQUEST_SQL, _pull_request_row),
    "session_meta": (_TITLE_SQL, _title_row),
    "compaction": (_COMPACTION_SQL, _compaction_row),
    "api_error": (_API_ERROR_SQL, _api_error_row),
}


def _window(start: datetime | None, end: datetime | None,
            subagent: bool | None) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if start is not None:
        clauses.append("ts >= ?")
        params.append(_to_epoch(start))
    if end is not None:
        clauses.append("ts < ?")
        params.append(_to_epoch(end))
    if subagent is not None:
        clauses.append("is_subagent = ?")
        params.append(int(subagent))
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), params


def _totals(values: tuple[Any, ...]) -> TokenTotals:
    return TokenTotals(
        requests=int(values[0]), input=int(values[1]), output=int(values[2]),
        cache_read=int(values[3]), cache_write_5m=int(values[4]), cache_write_1h=int(values[5]),
        thinking=int(values[6]), cost_usd=float(values[7]), nocache_cost_usd=float(values[8]),
        unpriced_requests=int(values[9]),
    )


def _file_state(row: sqlite3.Row) -> FileState:
    return FileState(path=row["path"], size=row["size"], mtime=row["mtime"], offset=row["offset"],
                     tail=row["tail"])


def _limit_json(item: Limit) -> dict[str, Any]:
    dollars = item.dollars
    compact = {
        "kind": item.kind, "group": item.group, "label": item.label, "percent": item.percent,
        "severity": item.severity, "resets_at": _to_epoch_or_none(item.resets_at),
        "is_active": item.is_active, "scope_model": item.scope_model,
        "scope_surface": item.scope_surface, "window_seconds": item.window_seconds,
        "allowance_pct": item.allowance_pct, "used_pct_of_weekly": item.used_pct_of_weekly,
        "locked_reason": item.locked_reason,
        "dollars": None if dollars is None else {
            "limit": dollars.limit, "used": dollars.used, "remaining": dollars.remaining},
    }
    return {key: value for key, value in compact.items() if value is not None}


def _limits_from_json(text: str | None) -> list[dict[str, Any]]:
    try:
        items = json.loads(text) if text else []
    except ValueError:
        return []
    if not isinstance(items, list):
        return []
    limits: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            item = dict(item)
            item["resets_at"] = _json_time(item.get("resets_at"))
            limits.append(item)
    return limits


def _json_time(value: Any) -> datetime | None:
    """A stored epoch from the JSON column; anything that is not a usable number reads as None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return _from_epoch(float(value))
    except (OverflowError, OSError, ValueError):
        return None


def _to_epoch(value: datetime) -> float:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def _to_epoch_or_none(value: datetime | None) -> float | None:
    return None if value is None else _to_epoch(value)


def _from_epoch(value: float) -> datetime:
    # Arithmetic, not datetime.fromtimestamp: that fails on Windows for dates before 1970 and after 3000.
    return _EPOCH + timedelta(seconds=value)


def _from_epoch_or_none(value: float | None) -> datetime | None:
    return None if value is None else _from_epoch(value)
