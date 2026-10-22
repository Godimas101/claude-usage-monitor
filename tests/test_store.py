"""Tests for usage_monitor.data.store. Synthetic records only; databases live in tmp_path."""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from usage_monitor.data import store as store_mod
from usage_monitor.data.models import (
    ApiErrorEvent,
    CodeChange,
    Compaction,
    DollarWindow,
    FileState,
    GitEvent,
    Limit,
    PullRequest,
    RequestRecord,
    SessionMeta,
    TokenTotals,
    ToolCall,
    UsageSnapshot,
)
from usage_monitor.data.pricing import BUNDLED_PATH, Pricing
from usage_monitor.data.store import Store, StoreError

UTC = timezone.utc
T0 = datetime(2026, 10, 20, 12, 0, 0, tzinfo=UTC)
OPUS = "claude-opus-5-5"
HAIKU = "claude-haiku-5-5"
PRICING = Pricing.load()


def make_rec(key: str = "msg_A|req_A", *, ts: datetime = T0, inp: int = 100, out: int = 10,
             cache_read: int = 0, cw5: int = 0, cw1: int = 0, model: str = OPUS,
             session: str = "sess-1", project: str = "demo-app", subagent: bool = False,
             agent: str | None = None, speed: str | None = None, **meta) -> RequestRecord:
    message_id, _, request_id = key.partition("|")
    return RequestRecord(
        key=key, message_id=message_id, request_id=request_id or None, ts=ts,
        session_id=session, project=project, cwd="C:/work/demo", git_branch="main",
        is_subagent=subagent, agent_id=agent, model=model,
        input_tokens=inp, output_tokens=out, cache_read_tokens=cache_read,
        cache_write_5m_tokens=cw5, cache_write_1h_tokens=cw1, speed=speed, **meta)


def raw(path: Path, sql: str, params: tuple = ()) -> list[tuple]:
    """Read the file with its own connection, the way another reader would."""
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def doubled_pricing() -> Pricing:
    data = json.loads(BUNDLED_PATH.read_text(encoding="utf-8-sig"))
    for row in data["models"]:
        for key in ("input", "output", "cache_read"):
            row[key] = row[key] * 2
        if row.get("long_context"):
            for key in ("input", "output", "cache_read"):
                row["long_context"][key] = row["long_context"][key] * 2
    data["version"] = "2099.1.1"
    return Pricing.from_dict(data)


def sample_snapshot(fetched: datetime, *, session_pct: float = 42.5, weekly_pct: float = 77.0,
                    raw_payload: dict | None = None) -> UsageSnapshot:
    session = Limit(kind="session", group="session", percent=session_pct, severity="normal",
                    resets_at=fetched + timedelta(hours=3), is_active=True, label="SESSION",
                    window_seconds=18000)
    weekly = Limit(kind="weekly_all", group="weekly", percent=weekly_pct, severity="warning",
                   resets_at=fetched + timedelta(days=5), is_active=True, label="WEEKLY",
                   window_seconds=604800)
    scoped = Limit(kind="weekly_scoped", group="weekly", percent=10.0, severity=None, resets_at=None,
                   is_active=False, label="FABLE WEEKLY", scope_model="Fable", allowance_pct=20.0,
                   used_pct_of_weekly=5.0, locked_reason="plan",
                   dollars=DollarWindow(limit=100.0, used=12.5, remaining=87.5))
    payload = raw_payload if raw_payload is not None else {"five_hour": {"utilization": session_pct}}
    return UsageSnapshot(fetched_at=fetched, limits=[session, weekly, scoped], raw=payload)


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


@pytest.fixture
def file_store(tmp_path):
    s = Store(tmp_path / "usage.db")
    yield s
    s.close()


# ── Open, schema and migrations ───────────────────────────────────────────────────

def test_new_database_is_schema_v2_wal_with_foreign_keys_off(tmp_path):
    path = tmp_path / "usage.db"
    with Store(path) as s:
        assert s.schema_version() == 2
        assert s.table_counts()["requests"] == 0
        assert s._rows("PRAGMA foreign_keys")[0][0] == 0
    assert raw(path, "PRAGMA journal_mode")[0][0] == "wal"
    names = {row[0] for row in raw(path, "SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"requests_ts", "requests_session", "requests_model", "requests_project"} <= names


def test_reopening_keeps_data_and_does_not_migrate_again(tmp_path):
    path = tmp_path / "usage.db"
    with Store(path) as s:
        s.add_items([make_rec()], PRICING)
    with Store(path) as s:
        assert s.schema_version() == 2
        assert s.totals(None, None).requests == 1


def test_newer_schema_is_refused(tmp_path):
    path = tmp_path / "usage.db"
    Store(path).close()
    conn = sqlite3.connect(path)
    conn.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    with pytest.raises(StoreError):
        Store(path)


def test_forward_migration_runs_once_per_new_version(tmp_path, monkeypatch):
    path = tmp_path / "usage.db"
    Store(path).close()

    def create_v3(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE demo_v3 (x INTEGER)")

    monkeypatch.setitem(store_mod._MIGRATIONS, 3, create_v3)
    monkeypatch.setattr(store_mod, "SCHEMA_VERSION", 3)
    with Store(path) as s:
        assert s.schema_version() == 3
        assert s.table_counts()["demo_v3"] == 0
    with Store(path) as s:  # already at 3: nothing runs again
        assert s.schema_version() == 3


def test_context_manager_closes_and_close_is_idempotent(tmp_path):
    with Store(tmp_path / "usage.db") as s:
        s.add_items([make_rec()], PRICING)
    s.close()  # a second close is harmless
    with pytest.raises(sqlite3.ProgrammingError):
        s.totals(None, None)


# ── Dedupe and merge ──────────────────────────────────────────────────────────────

def test_partial_line_then_full_line_keeps_the_max_across_batches(store):
    store.add_items([make_rec(out=5, inp=100)], PRICING)
    store.add_items([make_rec(out=50, inp=100, cache_read=200)], PRICING)
    totals = store.totals(None, None)
    assert totals.requests == 1
    assert totals.output == 50
    assert totals.cache_read == 200


def test_duplicates_inside_one_batch_merge_to_the_max(store):
    store.add_items([make_rec(out=5), make_rec(out=50), make_rec(out=20)], PRICING)
    totals = store.totals(None, None)
    assert totals.requests == 1
    assert totals.output == 50


def test_merge_keeps_first_non_null_metadata_and_treats_unknown_model_as_missing(file_store):
    first = replace(make_rec(), model="unknown", speed=None, cc_version=None, cwd="C:/first")
    second = replace(make_rec(ts=T0 + timedelta(seconds=5)), model=OPUS, speed="fast",
                     cc_version="2.1.0", cwd="C:/second")
    file_store.add_items([first], PRICING)
    file_store.add_items([second], PRICING)
    row = raw(file_store._path, "SELECT model, speed, cc_version, cwd, ts FROM requests")[0]
    assert row[0] == OPUS
    assert row[1] == "fast"
    assert row[2] == "2.1.0"
    assert row[3] == "C:/first"
    assert row[4] == T0.timestamp()  # the first sighting's time stays


def test_cost_is_recomputed_on_the_merged_tokens_not_summed(store):
    partial = make_rec("m|r", model=HAIKU, inp=100, out=5)
    full = make_rec("m|r", model=HAIKU, inp=100, out=50, cache_read=150_000)
    store.add_items([partial], PRICING)
    store.add_items([full], PRICING)
    merged = make_rec("m|r", model=HAIKU, inp=100, out=50, cache_read=150_000)
    assert PRICING.cost(merged).total != pytest.approx(
        PRICING.cost(partial).total + PRICING.cost(full).total)
    assert store.totals(None, None).cost_usd == pytest.approx(PRICING.cost(merged).total)


def test_stored_row_carries_every_field_cost_columns_and_pricing_version(file_store):
    rec = make_rec("m|r", inp=10, out=20, cache_read=30, cw5=40, cw1=50, speed="fast",
                   service_tier="standard", effort="xhigh", entrypoint="cli", cc_version="2.1.0",
                   thinking_tokens=7, web_search_requests=1, web_fetch_requests=2)
    file_store.add_items([rec], PRICING)
    cols = ("key, message_id, request_id, ts, session_id, project, cwd, git_branch, is_subagent, "
            "agent_id, model, input_tokens, output_tokens, cache_read_tokens, cache_write_5m_tokens, "
            "cache_write_1h_tokens, thinking_tokens, web_search_requests, web_fetch_requests, speed, "
            "service_tier, effort, entrypoint, cc_version, cost_input, cost_output, cost_cache_read, "
            "cost_cache_write_5m, cost_cache_write_1h, cost_total, nocache_cost, priced, "
            "pricing_version")
    row = raw(file_store._path, f"SELECT {cols} FROM requests")[0]
    cost = PRICING.cost(rec)
    assert row[0] == "m|r" and row[3] == T0.timestamp()
    assert row[10] == OPUS and row[11:13] == (10, 20)
    assert row[13:19] == (30, 40, 50, 7, 1, 2)
    assert row[24] == pytest.approx(cost.input)
    assert row[27] == pytest.approx(cost.cache_write_5m)
    assert row[29] == pytest.approx(cost.total)
    assert row[30] == pytest.approx(cost.nocache_total)
    assert row[31] == 1
    assert row[32] == PRICING.version


def test_chunked_lookup_merges_more_keys_than_one_in_clause(store, monkeypatch):
    monkeypatch.setattr(store_mod, "_IN_CHUNK", 3)
    store.add_items([make_rec(f"m{i}|r", out=1) for i in range(10)], PRICING)
    store.add_items([make_rec(f"m{i}|r", out=7) for i in range(10)], PRICING)
    totals = store.totals(None, None)
    assert totals.requests == 10
    assert totals.output == 70


def test_add_items_reports_submitted_counts_per_kind(store):
    items = [
        make_rec("a|1"), make_rec("a|1", out=3), make_rec("b|1"),
        ToolCall(tool_use_id="t1", name="Bash", request_key="a|1", session_id="s", ts=T0, is_subagent=False),
        SessionMeta(session_id="s", project="p", ai_title="Title"),
        "not an item",
    ]
    assert store.add_items(items, PRICING) == {"request": 3, "tool_call": 1, "session_meta": 1}
    assert store.add_items([], PRICING) == {}


def test_other_kinds_keep_the_first_write_per_key(file_store):
    file_store.add_items([
        ToolCall(tool_use_id="t1", name="Bash", request_key=None, session_id="s", ts=T0, is_subagent=False),
        CodeChange(tool_use_id="t1", session_id="s", ts=T0, lines_added=3, lines_removed=1, is_new_file=False),
        GitEvent(tool_use_id="g1", session_id="s", ts=T0, commit=True, push=False, pr=False),
        PullRequest(url="https://github.com/o/r/pull/1", number=1, repository="o/r", session_id="s", ts=T0),
        Compaction(uuid="c1", session_id="s", ts=T0, trigger="auto", pre_tokens=900, post_tokens=100),
        ApiErrorEvent(uuid="e1", session_id="s", ts=T0),
    ], PRICING)
    file_store.add_items([
        ToolCall(tool_use_id="t1", name="Edit", request_key=None, session_id="s", ts=T0, is_subagent=False),
        CodeChange(tool_use_id="t1", session_id="s", ts=T0, lines_added=99, lines_removed=0, is_new_file=True),
        GitEvent(tool_use_id="g1", session_id="s", ts=T0, commit=False, push=True, pr=True),
        PullRequest(url="https://github.com/o/r/pull/1", number=2, repository="x/y", session_id="s", ts=None),
        Compaction(uuid="c1", session_id="s", ts=T0, trigger="manual", pre_tokens=1, post_tokens=1),
        ApiErrorEvent(uuid="e1", session_id="s", ts=T0),
    ], PRICING)
    counts = file_store.table_counts()
    assert (counts["tool_calls"], counts["code_changes"], counts["git_events"],
            counts["pull_requests"], counts["compactions"], counts["api_errors"]) == (1, 1, 1, 1, 1, 1)
    assert raw(file_store._path, "SELECT name FROM tool_calls")[0][0] == "Bash"
    assert raw(file_store._path, "SELECT lines_added, is_new_file FROM code_changes")[0] == (3, 0)
    assert raw(file_store._path, "SELECT is_commit, is_push FROM git_events")[0] == (1, 0)
    assert raw(file_store._path, "SELECT number FROM pull_requests")[0][0] == 1
    assert raw(file_store._path, "SELECT trigger_kind FROM compactions")[0][0] == "auto"


def test_session_titles_latest_non_empty_value_wins(store):
    store.add_items([SessionMeta(session_id="s1", project="demo", ai_title="First")], PRICING)
    store.add_items([SessionMeta(session_id="s1", project="demo", ai_title="Second")], PRICING)
    store.add_items([SessionMeta(session_id="s1", project="demo", custom_title="Mine")], PRICING)
    store.add_items([SessionMeta(session_id="s1", project="demo", ai_title="")], PRICING)
    meta = store.session_meta("s1")
    assert (meta.ai_title, meta.custom_title, meta.project) == ("Second", "Mine", "demo")


def test_session_titles_latest_wins_within_one_batch(store):
    store.add_items([SessionMeta(session_id="s2", project="demo", ai_title="A"),
                     SessionMeta(session_id="s2", project="demo", ai_title="B")], PRICING)
    assert store.session_meta("s2").ai_title == "B"
    assert store.session_meta("missing") is None


# ── Reprice ───────────────────────────────────────────────────────────────────────

def test_reprice_recomputes_every_request_and_sets_the_version(file_store):
    file_store.add_items([make_rec(f"m{i}|r", out=100 + i) for i in range(3)], PRICING)
    before = file_store.totals(None, None).cost_usd
    assert file_store.pricing_version() == PRICING.version
    doubled = doubled_pricing()
    assert file_store.reprice(doubled) == 3
    after = file_store.totals(None, None)
    assert after.cost_usd == pytest.approx(2 * before)
    assert file_store.pricing_version() == "2099.1.1"
    assert {row[0] for row in raw(file_store._path, "SELECT pricing_version FROM requests")} == {"2099.1.1"}
    assert file_store.reprice(doubled) == 3  # a second pass is a no-op on the totals
    assert file_store.totals(None, None).cost_usd == pytest.approx(after.cost_usd)


def test_reprice_pages_through_all_rows(file_store, monkeypatch):
    monkeypatch.setattr(store_mod, "_REPRICE_CHUNK", 4)
    file_store.add_items([make_rec(f"m{i:02d}|r") for i in range(11)], PRICING)
    assert file_store.reprice(doubled_pricing()) == 11
    assert raw(file_store._path, "SELECT COUNT(*) FROM requests WHERE pricing_version = '2099.1.1'")[0][0] == 11


def test_reprice_on_an_empty_store_just_records_the_version(store):
    assert store.reprice(PRICING) == 0
    assert store.pricing_version() == PRICING.version


# ── Windows and reads ─────────────────────────────────────────────────────────────

def test_totals_window_is_half_open(store):
    store.add_items([
        make_rec("a|1", ts=T0 - timedelta(hours=1)),
        make_rec("b|1", ts=T0),
        make_rec("c|1", ts=T0 + timedelta(minutes=30)),
        make_rec("d|1", ts=T0 + timedelta(hours=1)),
    ], PRICING)
    assert store.totals(T0, T0 + timedelta(hours=1)).requests == 2
    assert store.totals(None, T0).requests == 1
    assert store.totals(T0 + timedelta(hours=1), None).requests == 1
    assert store.totals(None, None).requests == 4


def test_totals_by_model_is_keyed_by_raw_model_id(store):
    store.add_items([
        make_rec("a|1", model=OPUS),
        make_rec("b|1", model=OPUS),
        make_rec("c|1", model="us.anthropic.claude-opus-5-5"),
        make_rec("d|1", model=HAIKU),
    ], PRICING)
    by_model = store.totals_by_model(None, None)
    assert set(by_model) == {OPUS, "us.anthropic.claude-opus-5-5", HAIKU}
    assert by_model[OPUS].requests == 2
    assert isinstance(by_model[HAIKU], TokenTotals)


def test_subagent_filter_applies_to_totals(store):
    store.add_items([
        make_rec("a|1"),
        make_rec("b|1"),
        make_rec("c|1", subagent=True, agent="a1"),
    ], PRICING)
    assert store.totals(None, None, subagent=True).requests == 1
    assert store.totals(None, None, subagent=False).requests == 2
    assert store.totals(None, None).requests == 3
    assert store.totals_by_model(None, None, subagent=True)[OPUS].requests == 1


def test_unpriced_requests_are_counted_and_listed_without_synthetic(store):
    store.add_items([
        make_rec("a|1", model=OPUS, out=10),
        make_rec("b|1", model="claude-mystery-9", out=40),
        make_rec("c|1", model="<synthetic>"),
    ], PRICING)
    totals = store.totals(None, None)
    assert totals.unpriced_requests == 2
    assert totals.output == 60
    assert totals.cost_usd == pytest.approx(PRICING.cost(make_rec("a|1", out=10)).total)
    assert store.unpriced_models() == ["claude-mystery-9"]


def test_recent_requests_and_first_request_ts(store):
    assert store.first_request_ts() is None
    store.add_items([
        make_rec("a|1", ts=T0, out=10),
        make_rec("b|1", ts=T0 + timedelta(minutes=10), out=20),
        make_rec("c|1", ts=T0 + timedelta(minutes=20), out=30),
    ], PRICING)
    recent = store.recent_requests(T0 + timedelta(minutes=5))
    assert [(ts, out) for ts, out, _cost in recent] == [
        (T0 + timedelta(minutes=10), 20), (T0 + timedelta(minutes=20), 30)]
    assert all(cost > 0 for _ts, _out, cost in recent)
    assert store.first_request_ts() == T0


def test_empty_store_totals_are_zero(store):
    assert store.totals(None, None) == TokenTotals()
    assert store.totals_by_model(None, None) == {}


# ── Usage snapshots ───────────────────────────────────────────────────────────────

def test_snapshot_round_trip_and_compact_limits(store):
    store.save_usage_snapshot(sample_snapshot(T0))
    snaps = store.usage_snapshots(T0 - timedelta(hours=1))
    assert len(snaps) == 1
    snap = snaps[0]
    assert snap["ts"] == T0
    assert snap["session_pct"] == 42.5
    assert snap["session_resets_at"] == T0 + timedelta(hours=3)
    assert snap["weekly_pct"] == 77.0
    assert snap["weekly_resets_at"] == T0 + timedelta(days=5)
    assert [item["kind"] for item in snap["limits"]] == ["session", "weekly_all", "weekly_scoped"]
    assert snap["limits"][2]["dollars"] == {"limit": 100.0, "used": 12.5, "remaining": 87.5}
    assert "scope_model" not in snap["limits"][0]  # None values are left out
    assert store.latest_usage_raw() == (T0, {"five_hour": {"utilization": 42.5}})


def test_usage_snapshots_since_is_inclusive_and_ordered(store):
    for hours in (3, 1, 2):
        store.save_usage_snapshot(sample_snapshot(T0 + timedelta(hours=hours)))
    times = [snap["ts"] for snap in store.usage_snapshots(T0 + timedelta(hours=1))]
    assert times == [T0 + timedelta(hours=1), T0 + timedelta(hours=2), T0 + timedelta(hours=3)]


def test_latest_raw_only_moves_forward(store):
    store.save_usage_snapshot(sample_snapshot(T0, raw_payload={"v": 1}))
    store.save_usage_snapshot(sample_snapshot(T0 - timedelta(hours=1), raw_payload={"v": 0}))
    assert store.latest_usage_raw() == (T0, {"v": 1})
    store.save_usage_snapshot(sample_snapshot(T0 + timedelta(hours=1), raw_payload={"v": 2}))
    assert store.latest_usage_raw() == (T0 + timedelta(hours=1), {"v": 2})


def test_snapshots_older_than_180_days_are_pruned_on_save(store):
    old = T0 - timedelta(days=181)
    kept = T0 - timedelta(days=179)
    store.save_usage_snapshot(sample_snapshot(old))
    store.save_usage_snapshot(sample_snapshot(kept))
    assert [s["ts"] for s in store.usage_snapshots(old - timedelta(days=1))] == [old, kept]
    store.save_usage_snapshot(sample_snapshot(T0))
    assert [s["ts"] for s in store.usage_snapshots(old - timedelta(days=1))] == [kept, T0]


def test_corrupt_stored_raw_is_ignored(store, caplog):
    store.save_usage_snapshot(sample_snapshot(T0))
    store._set_meta("latest_usage_raw", "{not json")
    with caplog.at_level(logging.WARNING, logger="usage_monitor"):
        assert store.latest_usage_raw() is None
    assert "unreadable" in caplog.text


# ── File bookkeeping and transactions ─────────────────────────────────────────────

def test_file_state_round_trip_forget_and_known_files(store):
    store.add_items([make_rec()], PRICING)
    store.save_file_state(FileState("/p/a.jsonl", 120, 1.5, 100))
    store.save_file_state(FileState("/p/b.jsonl", 40, 2.5, 40))
    assert store.file_state("/p/a.jsonl") == FileState("/p/a.jsonl", 120, 1.5, 100)
    assert store.file_state("/p/none.jsonl") is None
    assert set(store.file_states()) == {"/p/a.jsonl", "/p/b.jsonl"}
    assert store.known_files() == ["/p/a.jsonl", "/p/b.jsonl"]
    store.forget_file("/p/a.jsonl")
    assert store.file_state("/p/a.jsonl") is None
    assert store.known_files() == ["/p/b.jsonl"]
    assert store.totals(None, None).requests == 1  # the items stay


def test_transaction_rolls_items_and_bookmark_back_together(file_store):
    with pytest.raises(RuntimeError):
        with file_store.transaction():
            file_store.add_items([make_rec()], PRICING)
            file_store.save_file_state(FileState("f.jsonl", 10, 1.0, 10))
            raise RuntimeError("boom")
    assert file_store.totals(None, None).requests == 0
    assert file_store.file_state("f.jsonl") is None


def test_nested_transaction_rolls_back_only_its_own_part(file_store):
    with file_store.transaction():
        file_store.add_items([make_rec("a|1")], PRICING)
        with pytest.raises(RuntimeError):
            with file_store.transaction():
                file_store.add_items([make_rec("b|1")], PRICING)
                raise RuntimeError("inner")
    assert file_store.totals(None, None).requests == 1


def test_store_lock_serialises_writers_and_other_connections_read(tmp_path):
    path = tmp_path / "usage.db"
    s = Store(path)
    failures: list[BaseException] = []

    def writer(n: int) -> None:
        try:
            for i in range(25):
                s.add_items([make_rec(f"t{n}-{i}|r", ts=T0 + timedelta(seconds=i))], PRICING)
        except BaseException as exc:  # reported by the assertion below
            failures.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert failures == []
    seen: list[int] = []

    def reader() -> None:
        with Store(path) as other:  # its own connection, as the UI's analytics readers use
            seen.append(other.totals(None, None).requests)

    t = threading.Thread(target=reader)
    t.start()
    t.join()
    assert seen == [100]
    s.close()


# ── Values SQLite cannot take, and sums past int64 (regression) ─────────────────────

def test_totals_add_up_past_the_int64_range(store):
    store.add_items([make_rec("m1|r1", out=2**62), make_rec("m2|r2", out=2**62)], PRICING)
    total = store.totals(None, None)
    assert total.output == 2**63
    assert total.requests == 2
    assert store.totals_by_model(None, None)[OPUS].output == 2**63


def test_a_row_sqlite_cannot_store_is_skipped_and_the_rest_of_the_batch_is_kept(store):
    batch = [make_rec("m1|r1"), make_rec("m2|r2", inp=2**63), make_rec("m3|r3")]
    assert store.add_items(batch, PRICING) == {"request": 2}
    assert store.totals(None, None).requests == 2


def test_a_lone_surrogate_row_is_skipped_and_the_rest_of_the_batch_is_kept(store):
    bad = replace(make_rec("m2|r2"), cwd="C:/bad\ud800")
    assert store.add_items([make_rec("m1|r1"), bad, make_rec("m3|r3")], PRICING) == {"request": 2}
    assert store.totals(None, None).requests == 2


def test_requests_dated_before_1970_are_stored_and_read_back(store):
    early = datetime(1900, 1, 1, tzinfo=UTC)
    store.add_items([make_rec("m0|r0", ts=early), make_rec("m1|r1")], PRICING)
    assert store.first_request_ts() == early
    assert store.totals(None, None).requests == 2


# ── The tail of a bookmark is stored with it (regression) ────────────────────────────

def test_a_bookmark_keeps_its_tail_fingerprint(file_store):
    file_store.save_file_state(FileState("/p/a.jsonl", 120, 1.5, 100, tail="abc123"))
    assert file_store.file_state("/p/a.jsonl") == FileState("/p/a.jsonl", 120, 1.5, 100, tail="abc123")
    assert file_store.file_states()["/p/a.jsonl"].tail == "abc123"


# ── Read-only query helper ────────────────────────────────────────────────────────

def test_query_returns_named_rows_for_select_and_with(store):
    store.add_items([make_rec("k1", inp=7)], PRICING)
    rows = store.query("SELECT key, input_tokens FROM requests WHERE key = ?", ("k1",))
    assert [(row["key"], row["input_tokens"]) for row in rows] == [("k1", 7)]
    assert store.query("WITH x AS (SELECT 2 AS v) SELECT v FROM x")[0][0] == 2
    assert store.query("SELECT 1;")[0][0] == 1


@pytest.mark.parametrize("sql", [
    "INSERT INTO meta (key, value) VALUES ('a', 'b')",
    "UPDATE requests SET model = 'x'",
    "DELETE FROM requests",
    "DROP TABLE requests",
    "PRAGMA user_version",
    "ATTACH DATABASE ':memory:' AS other",
    "SELECT 1; DROP TABLE requests",
    "SELECT 1; SELECT 2",
    "selectx 1",
    "",
    "   ",
])
def test_query_rejects_anything_that_is_not_one_select_or_with(store, sql):
    store.add_items([make_rec("k1")], PRICING)
    with pytest.raises(ValueError):
        store.query(sql)
    assert store.table_counts()["requests"] == 1


def test_query_cannot_write_even_inside_a_with_statement(file_store):
    file_store.add_items([make_rec("k1")], PRICING)
    with pytest.raises(sqlite3.Error):
        file_store.query("WITH doomed AS (SELECT 1) DELETE FROM requests")
    assert raw(file_store._path, "SELECT COUNT(*) FROM requests")[0][0] == 1
    # The connection is writable again once the read is over.
    file_store.add_items([make_rec("k2")], PRICING)
    assert raw(file_store._path, "SELECT COUNT(*) FROM requests")[0][0] == 2


def test_query_accepts_semicolons_in_literals_and_comments(store):
    for sql in ["SELECT ';'", "SELECT 1 WHERE 'a;b' LIKE 'a;b'", "/* c */ SELECT 1", "-- lead\nSELECT 1",
                "/* ; */ SELECT 1", "SELECT 1; -- end", "  \n SELECT 1 /* x */ ;"]:
        assert len(store.query(sql)) == 1, sql
    assert store.query("SELECT 1 WHERE 'a;b' LIKE ?", ("a;b",))[0][0] == 1


@pytest.mark.parametrize("sql", [
    "/* c */ DELETE FROM requests",
    "-- c\nPRAGMA query_only = 0",
    "(DELETE FROM requests)",
    "SELECT 1; /* x */ DELETE FROM requests",
    "SELECT 1; SELECT 2 -- tail",
])
def test_query_still_rejects_writes_hidden_behind_comments_and_second_statements(store, sql):
    store.add_items([make_rec("k1")], PRICING)
    with pytest.raises(ValueError):
        store.query(sql)
    assert store.table_counts()["requests"] == 1


# ── Pull requests keep the earliest ts per URL ────────────────────────────────────

PR_URL = "https://github.com/o/r/pull/9"


def pr_ts(file_store) -> float | None:
    return raw(file_store._path, "SELECT ts FROM pull_requests WHERE url = ?", (PR_URL,))[0][0]


def test_pull_request_keeps_the_earliest_ts_in_either_order(file_store):
    later = PullRequest(url=PR_URL, number=9, repository="o/r", session_id="s", ts=T0 + timedelta(hours=1))
    earlier = PullRequest(url=PR_URL, number=9, repository="o/r", session_id="s", ts=T0)
    file_store.add_items([later], PRICING)
    file_store.add_items([earlier], PRICING)
    assert pr_ts(file_store) == T0.timestamp()
    file_store.add_items([later], PRICING)
    assert pr_ts(file_store) == T0.timestamp()


def test_pull_request_null_ts_never_overrides_a_known_one_and_is_filled_by_one(file_store):
    file_store.add_items([PullRequest(url=PR_URL, number=9, repository="o/r", session_id="s", ts=None)], PRICING)
    assert pr_ts(file_store) is None
    file_store.add_items([PullRequest(url=PR_URL, number=9, repository="o/r", session_id="s", ts=T0)], PRICING)
    assert pr_ts(file_store) == T0.timestamp()
    file_store.add_items([PullRequest(url=PR_URL, number=9, repository="o/r", session_id="s", ts=None)], PRICING)
    assert pr_ts(file_store) == T0.timestamp()


def test_pull_request_fills_null_metadata_and_keeps_the_first_non_null(file_store):
    file_store.add_items([PullRequest(url=PR_URL, number=None, repository=None, session_id=None, ts=T0)], PRICING)
    file_store.add_items([PullRequest(url=PR_URL, number=9, repository="o/r", session_id="s", ts=T0)], PRICING)
    file_store.add_items([PullRequest(url=PR_URL, number=10, repository="x/y", session_id="t", ts=T0)], PRICING)
    row = raw(file_store._path, "SELECT number, repository, session_id FROM pull_requests WHERE url = ?", (PR_URL,))
    assert row == [(9, "o/r", "s")]


@pytest.mark.parametrize("a_first", [True, False])
def test_pull_request_session_follows_the_earliest_ts_in_either_ingest_order(file_store, a_first):
    a = PullRequest(url=PR_URL, number=9, repository="o/r", session_id="sess-A", ts=T0)
    b = PullRequest(url=PR_URL, number=9, repository="o/r", session_id="sess-B", ts=T0 + timedelta(days=5))
    for item in ([a, b] if a_first else [b, a]):
        file_store.add_items([item], PRICING)
    assert raw(file_store._path, "SELECT session_id, ts FROM pull_requests WHERE url = ?", (PR_URL,))         == [("sess-A", T0.timestamp())]


def test_pull_request_known_ts_keeps_its_session_when_a_later_null_ts_arrives(file_store):
    file_store.add_items([PullRequest(url=PR_URL, number=9, repository="o/r", session_id="sess-A", ts=T0)], PRICING)
    file_store.add_items([PullRequest(url=PR_URL, number=9, repository="o/r", session_id="sess-B", ts=None)], PRICING)
    assert raw(file_store._path, "SELECT session_id FROM pull_requests WHERE url = ?", (PR_URL,)) == [("sess-A",)]


def test_pull_requests_in_one_batch_merge_like_separate_batches(store):
    store.add_items([
        PullRequest(url=PR_URL, number=None, repository=None, session_id=None, ts=T0 + timedelta(hours=2)),
        PullRequest(url=PR_URL, number=9, repository="o/r", session_id="s", ts=T0 + timedelta(hours=1)),
    ], PRICING)
    rows = store.query("SELECT number, repository, session_id, ts FROM pull_requests")
    assert [tuple(row) for row in rows] == [(9, "o/r", "s", (T0 + timedelta(hours=1)).timestamp())]
