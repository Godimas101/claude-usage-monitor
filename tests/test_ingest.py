"""Tests for usage_monitor.data.ingest. Synthetic transcripts in tmp_path; nothing reads real data."""
from __future__ import annotations

import builtins
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from usage_monitor import paths
from usage_monitor.data import transcripts as tx
from usage_monitor.data.ingest import IngestResult, Ingestor
from usage_monitor.data.pricing import Pricing
from usage_monitor.data.store import Store

PRICING = Pricing.load()
MODEL = "claude-opus-5-5"
SESSION = "5e1f0c2a-7d3b-4c8e-9a10-000000000001"
SESSION_A = "aaaaaaaa-0000-4000-8000-00000000000a"
SESSION_B = "bbbbbbbb-0000-4000-8000-00000000000b"
SESSION_C = "cccccccc-0000-4000-8000-00000000000c"


def assistant(mid: str, *, out: int = 10, inp: int = 5) -> bytes:
    """One synthetic assistant line with usage: the record kind that becomes a request."""
    rec = {
        "type": "assistant",
        "uuid": f"u-{mid}",
        "sessionId": SESSION,
        "timestamp": "2026-10-20T10:00:00Z",
        "requestId": f"req_{mid}",
        "cwd": "C:/work/demo",
        "gitBranch": "main",
        "entrypoint": "cli",
        "version": "2.1.0",
        "message": {
            "id": mid,
            "model": MODEL,
            "content": [{"type": "text", "text": "synthetic"}],
            "usage": {"input_tokens": inp, "output_tokens": out,
                      "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
        },
    }
    return (json.dumps(rec) + "\n").encode("utf-8")


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "usage.db")
    yield s
    s.close()


@pytest.fixture
def projects(tmp_path) -> Path:
    root = tmp_path / "projects"
    root.mkdir()
    return root


def main_file(root: Path, session: str = SESSION, project: str = "demo-app") -> Path:
    folder = root / project
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{session}.jsonl"


def sub_file(root: Path, session: str = SESSION, project: str = "demo-app") -> Path:
    folder = root / project / session / "subagents"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "agent-a1b2c3d4e5f60718.jsonl"


def put(path: Path, *lines: bytes) -> None:
    path.write_bytes(b"".join(lines))


def add(path: Path, *lines: bytes) -> None:
    with open(path, "ab") as handle:
        handle.write(b"".join(lines))


def run(store: Store, roots: list, progress=None) -> IngestResult:
    return Ingestor(store, PRICING, roots).run(progress)


def requests(store: Store) -> int:
    return store.totals(None, None).requests


def same_path(a, b) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


# ── Each file state ───────────────────────────────────────────────────────────────

def test_new_file_is_read_from_the_start(store, projects):
    f = main_file(projects)
    put(f, assistant("m1"), assistant("m2"), assistant("m3"))
    result = run(store, [projects])
    assert isinstance(result, IngestResult)
    assert result.files_scanned == 1
    assert result.files_read == 1
    assert result.bytes_read == f.stat().st_size
    assert result.items == {"request": 3}
    assert result.errors == []
    assert result.duration_s >= 0
    assert requests(store) == 3


def test_grown_file_reads_only_the_appended_bytes(store, projects):
    f = main_file(projects)
    put(f, assistant("m1"), assistant("m2"))
    run(store, [projects])
    extra = assistant("m3", out=33)
    add(f, extra)
    result = run(store, [projects])
    assert result.files_read == 1
    assert result.bytes_read == len(extra)
    assert result.items == {"request": 1}
    assert requests(store) == 3


def test_unchanged_files_are_skipped_without_being_opened(store, projects, monkeypatch):
    files = [main_file(projects, SESSION_A), main_file(projects, SESSION_B)]
    put(files[0], assistant("a1"))
    put(files[1], assistant("b1"), assistant("b2"))
    run(store, [projects])

    reads: list[str] = []
    real_read_new = tx.read_new

    def spy_read_new(path, offset, ctx, **kwargs):
        reads.append(os.fspath(path))
        return real_read_new(path, offset, ctx, **kwargs)

    opened: list[str] = []
    real_open = builtins.open

    def spy_open(file, *args, **kwargs):
        if isinstance(file, (str, os.PathLike)):
            opened.append(os.fspath(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(tx, "read_new", spy_read_new)
    monkeypatch.setattr(builtins, "open", spy_open)
    result = run(store, [projects])

    assert result.files_scanned == 2
    assert result.files_read == 0
    assert result.items == {}
    assert reads == []
    assert not any(same_path(opened_path, f) for opened_path in opened for f in files)


def test_same_size_with_a_new_mtime_is_reread_without_double_counting(store, projects):
    f = main_file(projects)
    put(f, assistant("m1"), assistant("m2"))
    run(store, [projects])
    st = f.stat()
    os.utime(f, (st.st_atime + 100, st.st_mtime + 100))
    result = run(store, [projects])
    assert result.files_read == 1
    assert result.bytes_read == st.st_size
    assert requests(store) == 2


def test_truncated_file_is_reread_from_zero_and_its_bookmark_follows(store, projects):
    f = main_file(projects)
    put(f, assistant("m1"), assistant("m2"), assistant("m3"))
    run(store, [projects])
    shorter = assistant("m1") + assistant("m2")
    put(f, shorter)
    result = run(store, [projects])
    assert result.files_read == 1
    assert result.bytes_read == len(shorter)
    assert requests(store) == 3
    key = store.known_files()[0]
    assert store.file_state(key).offset == len(shorter)
    assert store.file_state(key).size == len(shorter)


def test_vanished_file_loses_its_bookmark_but_keeps_its_items(store, projects):
    f = main_file(projects)
    put(f, assistant("m1"), assistant("m2"))
    run(store, [projects])
    f.unlink()
    result = run(store, [projects])
    assert result.files_scanned == 0
    assert store.known_files() == []
    assert requests(store) == 2


def test_partial_trailing_line_waits_for_its_newline_across_runs(store, projects):
    f = main_file(projects)
    first, second = assistant("m1"), assistant("m2")
    put(f, first, second[:40])
    first_run = run(store, [projects])
    assert first_run.items == {"request": 1}
    assert first_run.bytes_read == len(first)
    assert store.file_state(store.known_files()[0]).offset == len(first)

    add(f, second[40:])
    second_run = run(store, [projects])
    assert second_run.items == {"request": 1}
    assert second_run.bytes_read == len(second)
    assert requests(store) == 2


def test_crlf_lines_are_read_and_the_bookmark_lands_on_the_file_end(store, projects):
    f = main_file(projects)
    put(f, assistant("m1").replace(b"\n", b"\r\n"), assistant("m2").replace(b"\n", b"\r\n"))
    result = run(store, [projects])
    assert result.items == {"request": 2}
    assert store.file_state(store.known_files()[0]).offset == f.stat().st_size


def test_malformed_and_non_item_lines_do_not_stop_ingest(store, projects):
    put(main_file(projects), assistant("m1"), b"not json at all\n", b"[1, 2]\n",
        b'{"type": "user", "message": {}}\n', b"\n", assistant("m2"))
    result = run(store, [projects])
    assert result.errors == []
    assert result.items == {"request": 2}
    assert requests(store) == 2


# ── Discovery and roots ───────────────────────────────────────────────────────────

def test_subagent_transcripts_are_found_and_flagged(store, projects):
    put(main_file(projects), assistant("m1"), assistant("m2"))
    put(sub_file(projects), assistant("s1"))
    result = run(store, [projects])
    assert result.files_scanned == 2
    assert store.totals(None, None, subagent=True).requests == 1
    assert store.totals(None, None, subagent=False).requests == 2
    assert [row[0] for row in store._rows("SELECT DISTINCT project FROM requests")] == ["demo-app"]


def test_only_jsonl_files_under_the_roots_are_read(store, projects, tmp_path):
    put(main_file(projects), assistant("m1"))
    (projects / "demo-app" / "notes.txt").write_bytes(assistant("txt1"))
    (projects / "demo-app" / "other.json").write_bytes(assistant("json1"))
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "x.jsonl").write_bytes(assistant("out1"))
    result = run(store, [projects])
    assert result.files_scanned == 1
    assert requests(store) == 1


def test_same_message_in_main_and_subagent_files_is_stored_once(store, projects):
    put(main_file(projects), assistant("shared"), assistant("m-main"))
    put(sub_file(projects), assistant("shared"), assistant("m-sub"))
    result = run(store, [projects])
    assert result.items == {"request": 4}   # parsed, before the store dedupes
    assert requests(store) == 3


def test_missing_root_is_not_scanned_and_forgets_nothing(store, projects, tmp_path):
    put(main_file(projects), assistant("m1"))
    run(store, [projects])
    result = run(store, [tmp_path / "gone"])
    assert result.files_scanned == 0
    assert result.errors == []
    assert len(store.known_files()) == 1
    assert requests(store) == 1


def test_partly_unlistable_root_forgets_nothing(store, projects, monkeypatch):
    put(main_file(projects), assistant("m1"))
    put(sub_file(projects), assistant("s1"))
    run(store, [projects])
    assert len(store.known_files()) == 2

    real_scandir = os.scandir

    def scandir_denying_subagents(path):
        if os.path.basename(os.fspath(path)) == "subagents":
            raise PermissionError(13, "denied")
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir_denying_subagents)
    result = run(store, [projects])
    assert result.errors == ["PermissionError listing subagents"]
    assert result.files_scanned == 1
    assert len(store.known_files()) == 2  # the unseen subagent file keeps its bookmark


def test_default_roots_come_from_paths_projects_dirs(store, projects, monkeypatch):
    put(main_file(projects), assistant("m1"))
    monkeypatch.setattr(paths, "projects_dirs", lambda: [projects])
    result = Ingestor(store, PRICING).run()
    assert result.files_scanned == 1
    assert requests(store) == 1


def test_overlapping_roots_are_scanned_once(store, projects):
    put(main_file(projects), assistant("m1"))
    assert run(store, [projects, projects]).files_scanned == 1


def test_an_empty_root_list_scans_nothing(store, projects):
    put(main_file(projects), assistant("m1"))
    result = run(store, [])
    assert result.files_scanned == 0
    assert requests(store) == 0


# ── Progress, failures and resume ─────────────────────────────────────────────────

def test_progress_reports_before_and_after_each_file(store, projects):
    for session in (SESSION_A, SESSION_B, SESSION_C):
        put(main_file(projects, session), assistant(session[:4]))
    seen: list[tuple[int, int]] = []
    run(store, [projects], progress=lambda done, total: seen.append((done, total)))
    assert seen == [(0, 3), (1, 3), (2, 3), (3, 3)]


def test_a_failed_file_rolls_back_and_the_next_run_completes_it(store, projects, monkeypatch):
    a = main_file(projects, SESSION_A)
    b = main_file(projects, SESSION_B)
    put(a, assistant("a1"), assistant("a2"))
    put(b, assistant("b1"), assistant("b2"), assistant("b3"))
    real_save = store.save_file_state
    calls = {"n": 0}

    def failing_save(state):
        calls["n"] += 1
        if calls["n"] == 2:  # b's bookmark, after b's items were written in the same transaction
            raise RuntimeError("disk full")
        return real_save(state)

    monkeypatch.setattr(store, "save_file_state", failing_save)
    first = run(store, [projects])
    assert first.files_read == 1
    assert first.errors == [f"RuntimeError on {b.name}"]
    assert requests(store) == 2  # b's items were rolled back with its bookmark
    assert len(store.known_files()) == 1

    monkeypatch.setattr(store, "save_file_state", real_save)
    second = run(store, [projects])
    assert second.files_read == 1
    assert second.errors == []
    assert requests(store) == 5


def test_interrupted_run_resumes_at_the_first_unfinished_file(store, projects, monkeypatch):
    put(main_file(projects, SESSION_A), assistant("a1"))
    put(main_file(projects, SESSION_B), assistant("b1"), assistant("b2"))
    real_add = store.add_items
    calls = {"n": 0}

    def interrupted_add(items, pricing):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        return real_add(items, pricing)

    monkeypatch.setattr(store, "add_items", interrupted_add)
    with pytest.raises(KeyboardInterrupt):
        run(store, [projects])
    assert requests(store) == 1

    monkeypatch.setattr(store, "add_items", real_add)
    resumed = run(store, [projects])
    assert resumed.files_read == 1  # file A is unchanged; only B is read
    assert resumed.errors == []
    assert requests(store) == 3


def test_unreadable_file_keeps_no_bookmark_so_the_next_run_retries_it(store, projects, monkeypatch):
    f = main_file(projects)
    put(f, assistant("m1"), assistant("m2"))
    blocked = {"on": True}
    real_open = builtins.open

    def guarded_open(file, *args, **kwargs):
        if blocked["on"] and isinstance(file, (str, os.PathLike)) and same_path(file, f):
            raise PermissionError(13, "denied")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    first = run(store, [projects])
    assert first.errors == [f"PermissionError on {f.name}"]
    assert first.files_read == 0
    assert store.known_files() == []
    assert requests(store) == 0

    blocked["on"] = False
    second = run(store, [projects])
    assert second.errors == []
    assert second.files_read == 1
    assert requests(store) == 2


# ── Reads that fail part way (a read that did not finish must not move the bookmark) ──

class FlakyFile:
    """Wraps a binary file and raises OSError when it reaches the second line of a read."""

    def __init__(self, real) -> None:
        self._real = real

    def __enter__(self) -> "FlakyFile":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._real.close()

    def fileno(self) -> int:
        return self._real.fileno()

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._real.seek(offset, whence)

    def __iter__(self):
        for number, raw in enumerate(self._real):
            if number == 1:
                raise OSError(5, "read broke off")
            yield raw


def test_an_open_that_fails_after_the_probe_keeps_the_bookmark_and_is_retried(store, projects, monkeypatch):
    f = main_file(projects)
    put(f, assistant("m1"), assistant("m2"))
    run(store, [projects])
    add(f, assistant("m3"))
    blocked = {"on": True}
    real_open = builtins.open

    def denying_open(file, *args, **kwargs):
        if blocked["on"] and same_path(file, f):
            raise PermissionError(13, "denied")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(tx, "open", denying_open, raising=False)  # the reader's own open, after the probe
    first = run(store, [projects])
    assert first.errors == [f"PermissionError on {f.name}"]
    assert first.files_read == 0
    assert requests(store) == 2

    blocked["on"] = False
    second = run(store, [projects])
    assert second.errors == []
    assert second.files_read == 1
    assert requests(store) == 3


def test_a_read_that_breaks_off_part_way_keeps_the_bookmark_and_is_retried(store, projects, monkeypatch):
    f = main_file(projects)
    put(f, assistant("m1"))
    run(store, [projects])
    add(f, assistant("m2"), assistant("m3"))
    real_open = builtins.open

    def breaking_open(file, *args, **kwargs):
        handle = real_open(file, *args, **kwargs)
        return FlakyFile(handle) if same_path(file, f) else handle

    monkeypatch.setattr(tx, "open", breaking_open, raising=False)
    first = run(store, [projects])
    assert first.errors == [f"OSError on {f.name}"]
    assert first.files_read == 0
    assert requests(store) == 1  # the line read before the break was not committed

    monkeypatch.setattr(tx, "open", real_open, raising=False)
    second = run(store, [projects])
    assert second.errors == []
    assert requests(store) == 3


def test_a_new_file_whose_read_breaks_off_is_read_in_full_on_the_next_run(store, projects, monkeypatch):
    f = main_file(projects)
    put(f, assistant("m1"), assistant("m2"))
    real_open = builtins.open

    def breaking_open(file, *args, **kwargs):
        handle = real_open(file, *args, **kwargs)
        return FlakyFile(handle) if same_path(file, f) else handle

    monkeypatch.setattr(tx, "open", breaking_open, raising=False)
    first = run(store, [projects])
    assert first.errors == [f"OSError on {f.name}"]
    assert store.known_files() == []
    assert requests(store) == 0

    monkeypatch.setattr(tx, "open", real_open, raising=False)
    second = run(store, [projects])
    assert second.errors == []
    assert requests(store) == 2


# ── Entries that cannot be examined are reported ──────────────────────────────────

def test_an_entry_that_cannot_be_examined_is_reported_in_the_result(store, projects, monkeypatch):
    class Denied:
        name = "hidden.jsonl"
        path = str(projects / "hidden.jsonl")

        def is_dir(self, follow_symlinks: bool = True) -> bool:
            raise PermissionError(13, "denied")

        def is_file(self, follow_symlinks: bool = True) -> bool:
            raise PermissionError(13, "denied")

    class Listing:
        def __enter__(self):
            return [Denied()]

        def __exit__(self, *exc_info: object) -> None:
            return None

    monkeypatch.setattr(os, "scandir", lambda folder: Listing())
    result = run(store, [projects])
    assert result.errors == ["PermissionError examining hidden.jsonl"]
    assert result.files_scanned == 0


# ── Regression: a file rewritten in place into a larger one is not read from the old bookmark ──

def test_a_file_rewritten_into_a_larger_one_is_read_from_the_start(store, projects):
    f = main_file(projects)
    put(f, assistant("a1"), assistant("a2"))
    run(store, [projects])
    put(f, assistant("c1"), assistant("c2"), assistant("c3"))  # rewritten in place, now larger
    result = run(store, [projects])
    assert result.files_read == 1
    assert result.errors == []
    assert requests(store) == 5  # a1, a2 kept; c1, c2 and c3 all read, not just the tail of the file


def test_records_appended_after_a_removal_that_regrows_the_file_are_stored(store, projects):
    f = main_file(projects)
    put(f, assistant("a1"), assistant("a2"))
    run(store, [projects])
    put(f, assistant("a2"), assistant("a3"), assistant("a4"))  # a1 removed in place, then two appended
    run(store, [projects])
    assert requests(store) == 4


def test_a_bookmark_without_a_recorded_tail_still_reads_only_the_appended_bytes(store, projects):
    f = main_file(projects)
    put(f, assistant("m1"), assistant("m2"))
    run(store, [projects])
    path = store.known_files()[0]
    store.save_file_state(replace(store.file_state(path), tail=None))  # as a bookmark from before this check
    extra = assistant("m3")
    add(f, extra)
    result = run(store, [projects])
    assert result.bytes_read == len(extra)
    assert requests(store) == 3


# ── A file removed between the walk and the read is skipped without an error ──

def test_a_file_removed_after_the_walk_is_skipped_without_an_error(store, projects, monkeypatch):
    f = main_file(projects)
    put(f, assistant("m1"))
    real_read_new = tx.read_new

    def vanished(path, offset, ctx, **kwargs):
        if same_path(path, f):
            raise FileNotFoundError(2, "gone")
        return real_read_new(path, offset, ctx, **kwargs)

    monkeypatch.setattr(tx, "read_new", vanished)
    result = run(store, [projects])
    assert result.errors == []
    assert result.files_read == 0
    assert requests(store) == 0


# ── A value SQLite could not hold does not block the rest of its file ─────────────────

def test_values_sqlite_could_not_hold_do_not_block_the_rest_of_their_file(store, projects):
    f = main_file(projects)
    huge = assistant("big", out=2 ** 63).replace(b"\"cwd\": \"C:/work/demo\"", b"\"cwd\": \"C:/bad\\ud800\"")
    put(f, assistant("g1"), assistant("bad2", inp=2 ** 63), huge, assistant("g2"))
    result = run(store, [projects])
    assert result.errors == []
    assert requests(store) == 4
