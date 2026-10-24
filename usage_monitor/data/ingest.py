"""Incremental ingest: brings the store up to date with the Claude Code transcript files.

Each run walks the transcript roots with os.scandir and compares every *.jsonl file with its
stored bookmark. Only what changed is read:

* new file                       read from byte 0
* same size and mtime            skipped without opening the file
* grown                          read from the bookmark (the last complete line), when the bytes
                                 before the bookmark are unchanged
* shrunk or rewritten in place   read from byte 0; the store dedupes, so a re-read is safe
* gone from a root that was scanned completely
                                 bookmark forgotten; the stored items stay

A file's items and its new bookmark commit in one store transaction, so an interrupted run
resumes at the first file it did not finish. A file that cannot be read keeps its old bookmark
and is retried on the next run.

Paths are kept as plain strings (they are the bookmarks' keys); every listing and read goes through
``paths.fs_path``, so a transcript 260 or more characters down (a subagent file under a long project
folder) is read like any other instead of being skipped without a trace.
"""
from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from usage_monitor import paths
from usage_monitor.data import transcripts as tx
from usage_monitor.data.models import FileState, TranscriptItem
from usage_monitor.data.pricing import Pricing
from usage_monitor.data.store import Store
from usage_monitor.log import get_logger

log = get_logger(__name__)

TRANSCRIPT_SUFFIX = ".jsonl"
TAIL_BYTES = 4096  # the bytes before a bookmark that identify it: an append never changes them

# path -> (the root it was found under, its stat taken during the walk)
_Found = dict[str, tuple[str, os.stat_result]]


@dataclass(slots=True)
class IngestResult:
    """Outcome of one run. ``items`` counts what was parsed and committed, before store dedupe."""
    files_scanned: int = 0                                  # transcript files found under the roots
    files_read: int = 0                                     # files opened: new, grown or changed
    bytes_read: int = 0
    items: dict[str, int] = field(default_factory=dict)    # item kind -> count
    duration_s: float = 0.0
    errors: list[str] = field(default_factory=list)         # short messages, never content


class Ingestor:
    """Brings the store up to date with the transcripts. Run it from one thread at a time.

    ``roots`` defaults to paths.projects_dirs(), resolved at each run. An empty list scans nothing.
    """

    def __init__(self, store: Store, pricing: Pricing, roots: list[Path] | None = None) -> None:
        self._store = store
        self._pricing = pricing
        self._roots = roots

    def run(self, progress: Callable[[int, int], None] | None = None) -> IngestResult:
        """Bring the store up to date. ``progress(done, total)`` is called before and after each file.

        A file that fails is rolled back, recorded in ``errors`` and retried on the next run. A
        failure outside any one file (the store itself) propagates to the caller.
        """
        started = time.perf_counter()
        result = IngestResult()
        roots = _unique_roots(paths.projects_dirs() if self._roots is None else self._roots)
        found, scanned = _discover(roots, result.errors)
        result.files_scanned = len(found)
        states = self._store.file_states()
        self._forget_vanished(states, found, scanned)
        total = len(found)
        _report(progress, 0, total)
        for done, (path, (root, stat)) in enumerate(sorted(found.items()), start=1):
            self._sync_file(path, root, stat, states.get(path), result)
            _report(progress, done, total)
        result.duration_s = time.perf_counter() - started
        log.debug("ingest: %d scanned, %d read, %d bytes, %.2f s, %d errors",
                  result.files_scanned, result.files_read, result.bytes_read,
                  result.duration_s, len(result.errors))
        return result

    def _sync_file(self, path: str, root: str, stat: os.stat_result, state: FileState | None,
                   result: IngestResult) -> None:
        try:  # a read that raises keeps the old bookmark, so the next run retries the file
            start = _start_offset(path, state, stat.st_size, stat.st_mtime)
            if start is None:
                return
            ctx = tx.file_context(Path(path), Path(root))
            items, end = tx.read_new(Path(path), start, ctx)
            bookmark = FileState(path=path, size=stat.st_size, mtime=stat.st_mtime, offset=end,
                                 tail=_tail_digest(path, end))
            counts = self._commit(items, bookmark)
        except FileNotFoundError as exc:
            if not os.path.lexists(paths.fs_path(path)):
                # removed after the walk: nothing to read, and the next walk forgets it
                log.debug("ingest: %s vanished during the run", os.path.basename(path))
                return
            _record_failure(result, path, exc)  # listed and still there, yet it would not open: say so
            return
        except Exception as exc:  # one bad file must not stop the run; it is retried next time
            _record_failure(result, path, exc)
            return
        result.files_read += 1
        result.bytes_read += max(0, end - start)
        for kind, count in counts.items():
            result.items[kind] = result.items.get(kind, 0) + count

    def _commit(self, items: Iterable[TranscriptItem], bookmark: FileState) -> dict[str, int]:
        with self._store.transaction():
            counts = self._store.add_items(items, self._pricing)
            self._store.save_file_state(bookmark)
        return counts

    def _forget_vanished(self, states: dict[str, FileState], found: _Found, scanned: list[str]) -> None:
        gone = [path for path in states if path not in found and _under_any(path, scanned)]
        if not gone:
            return
        with self._store.transaction():
            for path in gone:
                self._store.forget_file(path)
        log.debug("ingest: forgot %d vanished transcript bookmark(s)", len(gone))


def _record_failure(result: IngestResult, path: str, exc: Exception) -> None:
    """Report a file that could not be read: a short message with no content, and a warning in the log."""
    message = f"{type(exc).__name__} on {os.path.basename(path)}"
    result.errors.append(message)
    log.warning("ingest failed: %s", message)
    log.debug("ingest traceback", exc_info=True)


# ── Discovery ─────────────────────────────────────────────────────────────────────

def _unique_roots(roots: Iterable[Path | str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for root in roots:
        absolute = os.path.abspath(root)
        key = os.path.normcase(absolute)
        if key not in seen:
            seen.add(key)
            unique.append(absolute)
    return unique


def _discover(roots: list[str], errors: list[str]) -> tuple[_Found, list[str]]:
    """Every transcript under the roots, and the roots that were listed completely."""
    found: _Found = {}
    scanned: list[str] = []
    for root in roots:
        if _walk(root, found, errors):
            scanned.append(root)
    return found, scanned


def _walk(root: str, found: _Found, errors: list[str]) -> bool:
    """Collect the transcripts under ``root``. False when any part of it could not be listed."""
    complete = True
    pending = [root]
    while pending:
        folder = pending.pop()
        try:
            with os.scandir(paths.fs_path(folder)) as listing:
                for entry in listing:
                    if not _take(entry, folder, root, found, pending, errors):
                        complete = False
        except OSError as exc:
            if folder == root and isinstance(exc, FileNotFoundError):
                return False  # a missing root is simply not scanned
            complete = False
            errors.append(f"{type(exc).__name__} listing {os.path.basename(folder)}")
    return complete


def _take(entry: os.DirEntry[str], folder: str, root: str, found: _Found, pending: list[str],
          errors: list[str]) -> bool:
    """Sort one directory entry of ``folder`` into the walk. False when the entry could not be examined.

    The path is rebuilt from the plain folder: ``entry.path`` carries the extended-length prefix the listing used.
    """
    try:
        if entry.is_dir(follow_symlinks=False):
            pending.append(os.path.join(folder, entry.name))
        elif entry.name.lower().endswith(TRANSCRIPT_SUFFIX) and entry.is_file():
            found[os.path.join(folder, entry.name)] = (root, entry.stat())
    except OSError as exc:
        errors.append(f"{type(exc).__name__} examining {entry.name}")
        log.debug("ingest: cannot examine %s (%s)", entry.name, type(exc).__name__)
        return False
    return True


def _under_any(path: str, roots: list[str]) -> bool:
    key = os.path.normcase(path)
    for root in roots:
        prefix = os.path.normcase(root)
        if not prefix.endswith(os.sep):
            prefix += os.sep
        if key.startswith(prefix):
            return True
    return False


# ── Per-file decisions ────────────────────────────────────────────────────────────

def _start_offset(path: str, state: FileState | None, size: int, mtime: float) -> int | None:
    """Byte offset to read from, or None when the file is unchanged since its last read."""
    if state is None:
        return 0
    if size == state.size and mtime == state.mtime:
        return None
    if size > state.size and state.offset <= size and _prefix_intact(path, state):
        return state.offset
    return 0


def _prefix_intact(path: str, state: FileState) -> bool:
    """False when the bytes before the bookmark changed: the file was rewritten, not appended to.

    A bookmark with no recorded tail, from before this check, is trusted as it always was.
    """
    if state.tail is None or state.offset == 0:
        return True
    return _tail_digest(path, state.offset) == state.tail


def _tail_digest(path: str, end: int) -> str | None:
    """SHA-256 of the up to TAIL_BYTES bytes that end at ``end``. None at the start of the file."""
    if end <= 0:
        return None
    begin = max(0, end - TAIL_BYTES)
    with open(paths.fs_path(path), "rb") as handle:
        handle.seek(begin)
        return hashlib.sha256(handle.read(end - begin)).hexdigest()


def _report(progress: Callable[[int, int], None] | None, done: int, total: int) -> None:
    if progress is not None:
        progress(done, total)
