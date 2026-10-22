"""Tests for usage_monitor.data.transcripts. Synthetic fixtures only (tests/fixtures/transcripts)."""
from __future__ import annotations

import json
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from usage_monitor.data import transcripts as tx
from usage_monitor.data.models import (
    ApiErrorEvent,
    CodeChange,
    Compaction,
    GitEvent,
    PullRequest,
    RequestRecord,
    SessionMeta,
    ToolCall,
    ToolError,
)

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"
PROJECTS = FIXTURES / "projects"
SESSION = "5e1f0c2a-7d3b-4c8e-9a10-000000000001"
MAIN = PROJECTS / "demo-app" / f"{SESSION}.jsonl"
SUB = PROJECTS / "demo-app" / SESSION / "subagents" / "agent-a1b2c3d4e5f60718.jsonl"
WEIRD = FIXTURES / "weird_lines.jsonl"
UTC = timezone.utc


def assistant_line(*, ts="2026-07-14T10:00:00Z", mid="msg_T", rid="req_T", out=5, **extra) -> str:
    rec = {
        "type": "assistant",
        "uuid": f"u-{mid}",
        "sessionId": SESSION,
        "timestamp": ts,
        "requestId": rid,
        "message": {
            "id": mid,
            "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": "synthetic"}],
            "usage": {"input_tokens": 1, "output_tokens": out},
        },
    }
    rec.update(extra)
    return json.dumps(rec)


def of_type(items, cls):
    return [i for i in items if isinstance(i, cls)]


def first_request(items, key):
    return next(r for r in of_type(items, RequestRecord) if r.key == key)


@pytest.fixture(scope="module")
def main_ctx():
    return tx.file_context(MAIN, PROJECTS)


@pytest.fixture(scope="module")
def main_items(main_ctx):
    return tx.read_new(MAIN, 0, main_ctx)[0]


# ── file_context ──────────────────────────────────────────────────────────────

def test_file_context_for_main_session(main_ctx):
    assert main_ctx.project == "demo-app"
    assert main_ctx.is_subagent is False
    assert main_ctx.agent_id is None
    assert main_ctx.session_hint == SESSION
    assert Path(main_ctx.path).is_absolute()


def test_file_context_for_subagent_file():
    ctx = tx.file_context(SUB, PROJECTS)
    assert ctx.project == "demo-app"
    assert ctx.is_subagent is True
    assert ctx.agent_id == "a1b2c3d4e5f60718"
    assert ctx.session_hint == SESSION  # the parent session folder, not the file stem


def test_file_context_outside_projects_root_has_no_project():
    ctx = tx.file_context(WEIRD, PROJECTS)
    assert ctx.project == ""
    assert ctx.is_subagent is False
    assert ctx.session_hint == "weird_lines"


# ── record -> item mapping on the main fixture ────────────────────────────────

def test_main_fixture_item_counts(main_items):
    counts = Counter(type(i).__name__ for i in main_items)
    assert counts == {
        "RequestRecord": 9,
        "ToolCall": 3,
        "CodeChange": 3,
        "GitEvent": 2,
        "ToolError": 1,
        "PullRequest": 2,
        "SessionMeta": 2,
        "Compaction": 1,
        "ApiErrorEvent": 1,
    }


def test_split_block_lines_share_one_request_key(main_items):
    rows = [r for r in of_type(main_items, RequestRecord) if r.key == "msg_R1|req_R1"]
    assert len(rows) == 3  # thinking, text and tool_use lines of one API response
    assert {r.output_tokens for r in rows} == {120}
    assert {r.model for r in rows} == {"claude-opus-5-5"}
    edit = next(c for c in of_type(main_items, ToolCall) if c.tool_use_id == "toolu_EDIT1")
    assert edit.request_key == "msg_R1|req_R1"


def test_partial_stream_lines_keep_every_output_value(main_items):
    rows = [r for r in of_type(main_items, RequestRecord) if r.key == "msg_R2|req_R2"]
    assert sorted(r.output_tokens for r in rows) == [15, 80]


def test_synthetic_model_records_are_skipped(main_items):
    assert all(r.model != "<synthetic>" for r in of_type(main_items, RequestRecord))
    assert all(r.key != "msg_SYN|req_SYN" for r in of_type(main_items, RequestRecord))


def test_request_metadata_fields(main_items):
    r1 = first_request(main_items, "msg_R1|req_R1")
    assert r1.message_id == "msg_R1" and r1.request_id == "req_R1"
    assert r1.session_id == SESSION
    assert r1.project == "demo-app"
    assert r1.cwd == "C:/demo/app"
    assert r1.git_branch == "main"
    assert r1.effort == "high"
    assert r1.entrypoint == "cli"
    assert r1.cc_version == "2.1.0"
    assert r1.is_subagent is False and r1.agent_id is None
    assert r1.ts == datetime(2026, 7, 14, 10, 0, 2, 100000, tzinfo=UTC)


def test_cache_write_split_into_5m_and_1h(main_items):
    r1 = first_request(main_items, "msg_R1|req_R1")
    assert (r1.cache_write_5m_tokens, r1.cache_write_1h_tokens) == (500, 1500)
    assert r1.cache_write_tokens == 2000


def test_cache_write_fallbacks(main_items):
    # No cache_creation dict: the whole total is 5-minute.
    r2 = first_request(main_items, "msg_R2|req_R2")
    assert (r2.cache_write_5m_tokens, r2.cache_write_1h_tokens) == (300, 0)
    # Split (200 + 300) below the total (1000): the uncovered 500 is 5-minute.
    r4 = first_request(main_items, "msg_R4|req_R4")
    assert (r4.cache_write_5m_tokens, r4.cache_write_1h_tokens) == (700, 300)


def test_thinking_tokens_server_tools_and_speed(main_items):
    r1 = first_request(main_items, "msg_R1|req_R1")
    assert r1.thinking_tokens == 100
    assert (r1.speed, r1.service_tier) == ("standard", "standard")
    r3 = first_request(main_items, "msg_R3|req_R3")
    assert (r3.web_search_requests, r3.web_fetch_requests) == (2, 1)
    assert r3.speed == "fast"
    assert r3.thinking_tokens == 0


def test_iterations_are_not_added_on_top_of_top_level_usage(main_items):
    # The fixture's iterations[] repeats the top-level numbers; the totals must not double.
    r1 = first_request(main_items, "msg_R1|req_R1")
    assert (r1.input_tokens, r1.output_tokens, r1.cache_read_tokens) == (10, 120, 5000)


def test_tool_calls_carry_ids_names_and_request_key(main_items):
    calls = {c.tool_use_id: c for c in of_type(main_items, ToolCall)}
    assert set(calls) == {"toolu_EDIT1", "toolu_READ1", "toolu_MCP1"}
    assert calls["toolu_EDIT1"].name == "Edit"
    assert calls["toolu_READ1"].name == "Read"
    assert calls["toolu_MCP1"].name == "mcp__n8n-mcp__n8n_get_workflow"
    assert calls["toolu_MCP1"].request_key == "msg_R3|req_R3"
    assert all(not c.is_subagent for c in calls.values())


def test_code_change_from_structured_patch(main_items):
    change = next(c for c in of_type(main_items, CodeChange) if c.tool_use_id == "toolu_EDIT1")
    assert (change.lines_added, change.lines_removed, change.is_new_file) == (2, 1, False)
    assert change.session_id == SESSION
    assert change.ts == datetime(2026, 7, 14, 10, 0, 3, tzinfo=UTC)


def test_code_change_from_create_counts_content_lines(main_items):
    changes = {c.tool_use_id: c for c in of_type(main_items, CodeChange)}
    # A trailing newline does not start an extra line.
    assert (changes["toolu_WRITE1"].lines_added, changes["toolu_WRITE1"].is_new_file) == (3, True)
    # No trailing newline and no structuredPatch key at all.
    assert (changes["toolu_WRITE2"].lines_added, changes["toolu_WRITE2"].is_new_file) == (1, True)
    assert changes["toolu_WRITE1"].lines_removed == 0


def test_git_events_follow_flag_combinations(main_items):
    events = {e.tool_use_id: e for e in of_type(main_items, GitEvent)}
    assert set(events) == {"toolu_BASH1", "toolu_BASH2"}  # a branch-only result yields nothing
    full = events["toolu_BASH1"]
    assert (full.commit, full.push, full.pr) == (True, True, True)
    push_only = events["toolu_BASH2"]
    assert (push_only.commit, push_only.push, push_only.pr) == (False, True, False)


def test_pull_request_from_pr_link(main_items):
    prs = of_type(main_items, PullRequest)
    assert len(prs) == 2  # the store dedupes on url
    assert {p.url for p in prs} == {"https://github.com/example/demo/pull/42"}
    assert prs[0].number == 42
    assert prs[0].repository == "example/demo"
    assert prs[0].session_id == SESSION
    assert prs[0].ts == datetime(2026, 7, 14, 10, 5, tzinfo=UTC)


def test_session_meta_from_ai_and_custom_titles(main_items):
    metas = of_type(main_items, SessionMeta)
    assert [m.ai_title for m in metas] == ["Demo parser fix", None]
    assert [m.custom_title for m in metas] == [None, "Parser sprint"]
    assert all(m.session_id == SESSION and m.project == "demo-app" for m in metas)


def test_compaction_fields(main_items):
    (comp,) = of_type(main_items, Compaction)
    assert (comp.uuid, comp.trigger, comp.pre_tokens, comp.post_tokens) == ("sys-001", "auto", 180000, 12000)
    assert comp.session_id == SESSION
    assert comp.ts == datetime(2026, 7, 14, 10, 10, tzinfo=UTC)


def test_api_error_event(main_items):
    (err,) = of_type(main_items, ApiErrorEvent)
    assert err.uuid == "sys-002"
    assert err.session_id == SESSION
    assert err.ts == datetime(2026, 7, 14, 10, 11, tzinfo=UTC)


def test_every_timestamp_is_aware_utc(main_items):
    for item in main_items:
        ts = getattr(item, "ts", None)
        if ts is not None:
            assert ts.tzinfo is not None and ts.utcoffset() == timedelta(0)


def test_other_record_types_produce_nothing(main_ctx):
    lines = [
        '{"type":"attachment","attachment":{"type":"x"}}',
        '{"type":"last-prompt","lastPrompt":"x"}',
        '{"type":"mode","mode":"plan"}',
        '{"type":"queue-operation","operation":"enqueue"}',
        '{"type":"file-history-snapshot","snapshot":{}}',
        '{"type":"system","subtype":"stop_hook_summary","timestamp":"2026-07-14T10:00:00Z"}',
    ]
    for line in lines:
        assert tx.parse_line(line, main_ctx) == []


# ── subagents and sidechains ──────────────────────────────────────────────────

def test_subagent_file_items_are_marked_as_subagent():
    ctx = tx.file_context(SUB, PROJECTS)
    items, _ = tx.read_new(SUB, 0, ctx)
    requests = of_type(items, RequestRecord)
    assert {r.key for r in requests} == {"msg_SA1|req_SA1", "msg_SA2|req_SA2"}
    assert all(r.is_subagent and r.agent_id == "a1b2c3d4e5f60718" for r in requests)
    assert all(r.session_id == SESSION and r.project == "demo-app" for r in requests)
    (call,) = of_type(items, ToolCall)
    assert call.tool_use_id == "toolu_SA_BASH1" and call.is_subagent is True
    (change,) = of_type(items, CodeChange)
    assert (change.lines_added, change.lines_removed, change.is_new_file) == (2, 1, False)


def test_sidechain_record_inside_main_file_is_marked_subagent(main_items):
    side = first_request(main_items, "msg_S1|req_S1")
    assert side.is_subagent is True
    assert side.agent_id == "c0ffee00deadbeef"
    normal = first_request(main_items, "msg_R1|req_R1")
    assert normal.is_subagent is False and normal.agent_id is None


# ── timestamps and missing fields ─────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2026-07-14T10:00:00Z", datetime(2026, 7, 14, 10, tzinfo=UTC)),
        ("2026-07-14T10:00:00", datetime(2026, 7, 14, 10, tzinfo=UTC)),  # naive: read as UTC
        ("2026-07-14T15:30:00+05:30", datetime(2026, 7, 14, 10, tzinfo=UTC)),
        ("2026-07-14T10:00:00.5Z", datetime(2026, 7, 14, 10, 0, 0, 500000, tzinfo=UTC)),
        ("2026-07-14T10:00:00.1234567890+05:30", datetime(2026, 7, 14, 4, 30, 0, 123456, tzinfo=UTC)),
    ],
)
def test_timestamp_forms_normalise_to_aware_utc(raw, expected, main_ctx):
    (req,) = tx.parse_line(assistant_line(ts=raw), main_ctx)
    assert req.ts == expected
    assert req.ts.tzinfo is not None


@pytest.mark.parametrize("ts", ["yesterday", 12345, None])
def test_request_without_usable_timestamp_is_skipped_and_counted(ts, main_ctx):
    st = tx.ScanStats()
    assert tx.parse_line(assistant_line(ts=ts), main_ctx, stats=st) == []
    assert st.no_timestamp == 1


def test_request_without_any_dedupe_key_is_skipped_and_counted(main_ctx):
    line = json.dumps({
        "type": "assistant",
        "timestamp": "2026-07-14T10:00:00Z",
        "message": {"model": "claude-opus-5-5", "usage": {"input_tokens": 1, "output_tokens": 1}},
    })
    st = tx.ScanStats()
    assert tx.parse_line(line, main_ctx, stats=st) == []
    assert st.no_key == 1


def test_request_key_uses_uuid_when_message_id_is_missing(main_ctx):
    line = json.dumps({
        "type": "assistant",
        "uuid": "solo-1",
        "timestamp": "2026-07-14T10:00:00Z",
        "message": {"model": "claude-opus-5-5", "usage": {"input_tokens": 1, "output_tokens": 1}},
    })
    (req,) = tx.parse_line(line, main_ctx)
    assert req.key == "uuid|solo-1"
    assert req.message_id is None


def test_request_without_request_id_keeps_message_key(main_ctx):
    (req,) = tx.parse_line(assistant_line(mid="msg_NR", rid=None), main_ctx)
    assert req.key == "msg_NR|"
    assert req.request_id is None


def test_synthetic_line_is_counted_and_skipped(main_ctx):
    st = tx.ScanStats()
    line = assistant_line(mid="msg_SX", rid="req_SX")
    line = line.replace("claude-opus-5-5", "<synthetic>")
    assert tx.parse_line(line, main_ctx, stats=st) == []
    assert st.synthetic == 1


# ── weird but well-formed input ───────────────────────────────────────────────

def test_weird_fixture_yields_expected_items_and_counts():
    ctx = tx.file_context(WEIRD, PROJECTS)
    st = tx.ScanStats()
    items, offset = tx.read_new(WEIRD, 0, ctx, stats=st)
    assert offset == WEIRD.stat().st_size
    requests = {r.key: r for r in of_type(items, RequestRecord)}
    assert set(requests) == {"m9|r9", "uuid|w10", "m11|r11", "m12|", "m23|r23"}
    assert requests["m9|r9"].ts == datetime(2026, 7, 14, 4, 30, 0, 123456, tzinfo=UTC)
    zeroed = requests["m23|r23"]
    assert (zeroed.input_tokens, zeroed.output_tokens, zeroed.cache_write_tokens) == (0, 0, 0)
    changes = {c.tool_use_id: c for c in of_type(items, CodeChange)}
    assert set(changes) == {"uuid|w13", "uuid|w25"}
    assert (changes["uuid|w13"].lines_added, changes["uuid|w13"].lines_removed) == (2, 1)
    (pr,) = of_type(items, PullRequest)
    assert pr.number is None and pr.ts is None and pr.session_id == "sess-w"
    (comp,) = of_type(items, Compaction)
    assert (comp.uuid, comp.trigger, comp.pre_tokens, comp.post_tokens) == ("c20", None, None, None)
    assert len(items) == 9
    assert st.items == 9
    assert st.synthetic == 1
    assert st.no_timestamp == 2
    assert st.no_key == 2
    assert st.malformed == 0  # junk that carries no item token is prefiltered, not decoded
    assert st.prefiltered >= 4


def test_wrong_types_and_bad_values_never_raise(main_ctx):
    bad = [
        "{not json",
        "[1, 2, 3]",
        '"text"',
        "42",
        "null",
        '{"type":"assistant","message":[1,2]}',
        '{"type":"assistant","message":{"usage":"x","content":{"type":"tool_use"}}}',
        '{"type":"user","toolUseResult":{"structuredPatch":"no","content":42}}',
        '{"type":"user","toolUseResult":{"gitOperation":["commit"]}}',
        '{"type":"pr-link","prUrl":42}',
        '{"type":"system","subtype":"compact_boundary"}',
        '{"type":"system","subtype":"api_error","uuid":["x"],"timestamp":"2026-07-14T10:00:00Z"}',
        '{"type":"assistant","timestamp":null,"message":{"usage":{"input_tokens":{}}}}',
        '{"type":"assistant","timestamp":"2026-07-14T10:00:00Z","requestId":"r","message":{"id":"m",'
        '"usage":{"input_tokens":1e400,"output_tokens":-1}}}',
        '{"type":"assistant","timestamp":"2026-99-99T99:99:99Z","requestId":"r","message":{"id":"m",'
        '"usage":{"input_tokens":1}}}',
        '{"type":"assistant","timestamp":"0001-01-01T00:00:00+05:00","requestId":"r","message":{"id":"m",'
        '"usage":{"input_tokens":1}}}',
        '{"type":"assistant","timestamp":"2026-07-14T10:00:00Z","requestId":"r","message":{"id":"m",'
        '"usage":{"input_tokens":1},"content":[{"type":"tool_use","id":"t","name":{"x":1}}]}}',
        "\ufeff" + assistant_line(mid="msg_BOM"),
        "   ",
        "",
    ]
    for line in bad:
        assert isinstance(tx.parse_line(line, main_ctx), list)


def test_deeply_nested_json_is_malformed_not_fatal(main_ctx):
    line = '{"type":"assistant","x":' + "[" * 100_000 + "]" * 100_000 + "}"
    st = tx.ScanStats()
    assert tx.parse_line(line, main_ctx, stats=st) == []
    assert st.malformed == 1


def test_non_string_line_is_counted_not_raised(main_ctx):
    st = tx.ScanStats()
    assert tx.parse_line(None, main_ctx, stats=st) == []  # type: ignore[arg-type]
    assert st.malformed == 1


def test_non_utf8_bytes_decode_with_replacement(tmp_path, main_ctx):
    raw = (
        b'{"type":"assistant","uuid":"x1","sessionId":"' + SESSION.encode() + b'",'
        b'"timestamp":"2026-07-14T10:00:00Z","requestId":"req_X1",'
        b'"message":{"id":"msg_X1","model":"claude-opus-5-5",'
        b'"content":[{"type":"text","text":"bad \xff\xfe bytes"}],'
        b'"usage":{"input_tokens":1,"output_tokens":7}}}\n'
    )
    path = tmp_path / "bytes.jsonl"
    path.write_bytes(raw)
    items, offset = tx.read_new(path, 0, main_ctx)
    assert [r.key for r in items] == ["msg_X1|req_X1"]
    assert items[0].output_tokens == 7
    assert offset == len(raw)


# ── read_new: offsets, partial lines, line endings ────────────────────────────

def test_read_new_offset_is_end_of_file_for_a_complete_file(main_ctx):
    items, offset = tx.read_new(MAIN, 0, main_ctx)
    assert offset == MAIN.stat().st_size
    assert items


def test_partial_last_line_is_not_consumed(tmp_path, main_ctx):
    line1 = assistant_line(mid="msg_P1", rid="req_P1") + "\n"
    line2 = assistant_line(mid="msg_P2", rid="req_P2")
    path = tmp_path / "grow.jsonl"
    path.write_bytes((line1 + line2[:40]).encode("utf-8"))
    items, offset = tx.read_new(path, 0, main_ctx)
    assert [r.key for r in items] == ["msg_P1|req_P1"]
    assert offset == len(line1.encode("utf-8"))
    path.write_bytes((line1 + line2 + "\n").encode("utf-8"))
    items, offset = tx.read_new(path, offset, main_ctx)
    assert [r.key for r in items] == ["msg_P2|req_P2"]
    assert offset == path.stat().st_size


def test_complete_json_without_newline_waits_for_its_newline(tmp_path, main_ctx):
    path = tmp_path / "nonl.jsonl"
    path.write_bytes(assistant_line(mid="msg_N1", rid="req_N1").encode("utf-8"))
    assert tx.read_new(path, 0, main_ctx) == ([], 0)
    with path.open("ab") as fh:
        fh.write(b"\n")
    items, offset = tx.read_new(path, 0, main_ctx)
    assert [r.key for r in items] == ["msg_N1|req_N1"]
    assert offset == path.stat().st_size


def test_crlf_lines_parse_and_offsets_count_raw_bytes(tmp_path, main_ctx):
    data = MAIN.read_bytes().replace(b"\n", b"\r\n")
    path = tmp_path / "crlf.jsonl"
    path.write_bytes(data)
    expected, _ = tx.read_new(MAIN, 0, main_ctx)
    items, offset = tx.read_new(path, 0, main_ctx)
    assert items == expected
    assert offset == len(data)


def test_blank_lines_are_skipped_without_counting(tmp_path, main_ctx):
    path = tmp_path / "blank.jsonl"
    path.write_bytes(b"\r\n\n" + assistant_line(mid="msg_B", rid="req_B").encode("utf-8") + b"\r\n\r\n")
    st = tx.ScanStats()
    items, offset = tx.read_new(path, 0, main_ctx, stats=st)
    assert [r.key for r in items] == ["msg_B|req_B"]
    assert st.malformed == 0
    assert offset == path.stat().st_size


def test_resume_in_steps_matches_a_single_read(tmp_path, main_ctx):
    data = MAIN.read_bytes()
    expected, end = tx.read_new(MAIN, 0, main_ctx)
    assert end == len(data)
    path = tmp_path / "grow.jsonl"
    seen: list = []
    offset = 0
    for cut in range(0, len(data) + 1, 311):  # most cuts land mid-line on purpose
        path.write_bytes(data[:cut])
        items, offset = tx.read_new(path, offset, main_ctx)
        seen.extend(items)
    path.write_bytes(data)
    items, offset = tx.read_new(path, offset, main_ctx)
    seen.extend(items)
    assert seen == expected
    assert offset == len(data)


def test_offset_beyond_eof_reads_nothing_and_returns_a_line_boundary(tmp_path, main_ctx):
    path = tmp_path / "cut.jsonl"
    line1 = assistant_line(mid="msg_B1", rid="req_B1") + "\n"
    path.write_bytes((line1 + '{"type":"assis').encode("utf-8"))
    assert tx.read_new(path, 10_000, main_ctx) == ([], len(line1.encode("utf-8")))
    size = MAIN.stat().st_size
    assert tx.read_new(MAIN, size + 500, main_ctx) == ([], size)


def test_negative_offset_reads_from_the_start(main_ctx):
    assert tx.read_new(MAIN, -5, main_ctx) == tx.read_new(MAIN, 0, main_ctx)


def test_missing_file_raises_so_the_caller_keeps_its_bookmark(tmp_path, main_ctx):
    with pytest.raises(FileNotFoundError):
        tx.read_new(tmp_path / "gone.jsonl", 42, main_ctx)


# ── prefilter equivalence ─────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [MAIN, SUB, WEIRD])
def test_prefilter_gives_the_same_items_as_a_full_parse(path):
    ctx = tx.file_context(path, PROJECTS)
    fast, fast_offset = tx._scan(path, 0, ctx, prefilter=True, stats=None)
    full, full_offset = tx._scan(path, 0, ctx, prefilter=False, stats=None)
    assert fast == full
    assert fast_offset == full_offset == path.stat().st_size


@pytest.mark.parametrize("path", [MAIN, SUB, WEIRD])
def test_every_item_bearing_line_contains_a_prefilter_token(path):
    ctx = tx.file_context(path, PROJECTS)
    for line in path.read_text(encoding="utf-8").splitlines():
        items = tx._decode_items(line, ctx, tx.ScanStats())  # no prefilter here on purpose
        assert not items or any(tok in line for tok in tx.PREFILTER_TOKENS)


def test_parse_line_agrees_with_read_new(main_ctx):
    lines = MAIN.read_text(encoding="utf-8").splitlines()
    per_line = [item for line in lines for item in tx.parse_line(line, main_ctx)]
    assert per_line == tx.read_new(MAIN, 0, main_ctx)[0]


def test_scan_stats_for_main_fixture(main_ctx):
    st = tx.ScanStats()
    items, _ = tx.read_new(MAIN, 0, main_ctx, stats=st)
    assert st.items == len(items) == 24
    assert st.synthetic == 1
    assert st.no_timestamp == 0 and st.no_key == 0 and st.malformed == 0
    assert st.prefiltered > 0


# ── Values the store can hold, and timestamps it can convert back (regression) ──────

def usage_record(*, usage=None, **top) -> str:
    """An assistant record with the given usage block. Top-level fields come from ``top``."""
    rec = {
        "type": "assistant",
        "uuid": "u-edge",
        "sessionId": SESSION,
        "timestamp": "2026-07-14T10:00:00Z",
        "requestId": "req_E",
        "cwd": "C:/work/demo",
        "message": {
            "id": "msg_E",
            "model": "claude-opus-5-5",
            "content": [],
            "usage": usage if usage is not None else {"input_tokens": 1, "output_tokens": 5},
        },
    }
    rec.update(top)
    return json.dumps(rec)


@pytest.mark.parametrize("stamp", ["1900-01-01T00:00:00Z", "0001-01-01T00:00:00Z", "1969-12-31T23:59:59Z"])
def test_timestamps_before_1970_are_not_read(stamp):
    assert tx._parse_ts(stamp) is None


def test_the_epoch_itself_is_read():
    assert tx._parse_ts("1970-01-01T00:00:00Z") == datetime(1970, 1, 1, tzinfo=UTC)


def test_a_count_past_2_to_the_53_is_clamped_so_the_record_is_kept(main_ctx):
    items = tx.parse_line(usage_record(usage={"input_tokens": 2**63, "output_tokens": 5}), main_ctx)
    request = of_type(items, RequestRecord)[0]
    assert request.input_tokens == 2**53
    assert request.output_tokens == 5


def test_a_lone_surrogate_from_a_json_escape_is_replaced_in_text_fields(main_ctx):
    items = tx.parse_line(usage_record(cwd="C:/bad\ud800dir", gitBranch="main\udc00"), main_ctx)
    request = of_type(items, RequestRecord)[0]
    assert request.cwd == "C:/bad?dir"
    assert request.git_branch == "main?"
    request.cwd.encode("utf-8")
    request.git_branch.encode("utf-8")


# ── Git activity in shell commands, and tool errors ───────────────────────────

GIT_CASES = [
    # (command, git_ops)
    ("git commit -m x", "commit"),
    ("git add -A && git commit -am 'fix it' && git push origin main", "commit+push"),
    ("git push", "push"),
    ("git push --force-with-lease origin feat/x", "push"),
    ("gh pr create --title t --body b", "pr"),
    ("git commit -m x && git push && gh pr create --fill", "commit+push+pr"),
    ("gh pr create && git push && git commit -m x", "commit+push+pr"),     # always in fixed order
    ("git commit --amend --no-edit", "commit"),
    ("git --no-pager commit -m x", "commit"),
    ("git -c user.name=a -c user.email=b commit -m x", "commit"),
    ('git -C "C:/repos/my app" commit -m x', "commit"),
    ("git -C 'D:/repos/my app' push", "push"),
    ("git -C ../other commit -m x", "commit"),
    ("git -C C:\\repos\\app commit -m x", "commit"),                        # Windows path, unquoted
    ('git -C "C:\\Users\\Chris Carpenter\\repo" commit -m x', "commit"),    # Windows path, quoted
    ('& git -C "D:\\Work Dir" push', "push"),                               # PowerShell call operator
    ('git commit -m "x"; if ($?) { git push }', "commit+push"),             # PowerShell
    ("git commit -m @'\nfix: mentions git push and gh pr create\n'@", "commit"),   # here-string
    ('git commit -m "docs: mention git push in the guide"', "commit"),
    ('git commit -m "say \\"git push\\" and \\"gh pr create\\""', "commit"),
    ("git commit -m 'it''s fine'", "commit"),
    ("git commit -F - <<'EOF'\nfeat: x\n\nthen git push and gh pr create\nEOF\n", "commit"),
    ('git commit -m "$(cat <<\'EOF\'\nsubject\n\nbody says git push\nEOF\n)"', "commit"),
    ("git commit -F - <<-EOF\n\tgit push\n\tEOF\n", "commit"),
    ("git commit -F - <<~EOF\n  gh pr create\n  EOF\n", "commit"),
    ("cat <<EOF\ngit push\nEOF\ngit commit -m x", "commit"),                # code after the heredoc counts
    ("git commit -F - <<EOF && git push\nmsg\nEOF", "commit+push"),        # rest of the marker line counts
    ("# push it later, it's not ready\ngit commit -m x\ngit push origin main", "commit+push"),   # a comment's apostrophe
    ("git commit -m x # and then git push", "commit"),
    ("echo $#; git push", "push"),                                          # $# is not a comment
    ("git commit -m x#1 && git push origin issue#2", "commit+push"),
    ("# git commit -m x", None),
    ("git log --grep commit", None),
    ("git log --oneline -5 --grep=push", None),
    ("git commit-tree HEAD^{tree}", None),
    ("git push-to-checkout", None),
    ("git status && git diff", None),
    ("echo 'git commit' > note.txt", None),
    ('echo "git push; gh pr create"', None),
    ("cat <<'EOF'\ngit commit -m x\nEOF\n", None),
    ("cat <<EOF\ngit push\n", "push"),                                      # no closing line: not a heredoc
    ("digit commit && github push", None),                                  # not the git word
    ("gh pr list && gh pr view 3", None),
    ("grep -r 'git commit' docs/", None),
    # A "<<" inside a quoted string or arithmetic is not a heredoc.
    ('python -c "print(1 << n)"\ngit commit -m x', "commit"),
    ('git commit -m "use <<EOF syntax"\ngit push', "commit+push"),
    ("echo 'x << y'\ngit push", "push"),
    ("n=$((1 << n))\ngit commit -m x", "commit"),
    # Delimiters beyond [A-Za-z_]\w*.
    ("cat > f.sh <<\\EOF\ndon't\ngit push\nEOF\n", None),
    ("cat <<'END-OF-FILE'\ngit commit -m x\nEND-OF-FILE\n", None),
    ("cat <<END-OF-FILE\ngit commit -m x\nEND-OF-FILE\ngit push", "push"),
    ('cat <<"EOF.1"\ngit push\nEOF.1\n', None),
    ("cat <<A <<B\ngit push\nA\ngit commit\nB\ngit push", "push"),             # two heredocs on one line
    ("git commit -m x <<< 'git push'", "commit"),                             # a here-string, not a heredoc
    # Quotes inside a heredoc body or a $( ) cannot unbalance the pass.
    ("git commit -m \"$(cat <<'EOF'\nit's \"quoted\n\ngit push\nEOF\n)\" && git push", "commit+push"),
    # git by name with an extension.
    ("git.exe commit -m x", "commit"),
    ("git.exe -C D:\\repo push", "push"),
    ("", None),
]


@pytest.mark.parametrize("command,expected", GIT_CASES)
def test_git_ops_detection(command, expected):
    assert tx.git_ops(command) == expected


POWERSHELL_CASES = [
    # (command, git_ops)
    ('git -C "D:\\repo\\" commit -m "x"', "commit"),                           # backslash is not an escape
    ('git -C "D:\\repo\\" push', "push"),
    ('& "C:\\Program Files\\Git\\cmd\\git.exe" commit -m x', "commit"),        # a quoted path to git
    ("& 'C:\\Program Files\\Git\\cmd\\git.exe' -C D:\\r push", "push"),
    ('echo "git" push', None),                                                # a bare quoted word is not a path
    ("git commit -m @'\nfix don't run\nmention git push twice\n'@", "commit"),
    ("git commit -m @\"\nsay \"git push\" and it's fine\n\"@\ngit push", "commit+push"),
    ('git commit -m "say `"git push`" now"', "commit"),                       # backtick escapes a quote
    ('git commit -m "a ""git push"" b"', "commit"),                           # a doubled quote
    ("git commit -m 'it''s git push'", "commit"),
    ('git commit -m "x"; if ($?) { git push }', "commit+push"),
    ("git commit -m @'\nonly a message\n'@ ; git push", "commit+push"),
    ("git log --grep @'\ncommit\n'@", None),
]


@pytest.mark.parametrize("command,expected", POWERSHELL_CASES)
def test_git_ops_detection_in_powershell(command, expected):
    assert tx.git_ops(command, "PowerShell") == expected


def test_a_trailing_backslash_in_a_quoted_path_is_an_escape_only_in_bash():
    command = 'git -C "D:\\repo\\" commit -m "x"'
    assert tx.git_ops(command, "PowerShell") == "commit"
    assert tx.git_ops(command, "Bash") is None
    assert tx.git_ops(command) is None


def test_the_tool_name_picks_the_quoting_rules(main_ctx):
    command = 'git -C "D:\\repo\\" commit -m "x"'
    ops = {name: tx.parse_line(tool_use_line(name, {"command": command}), main_ctx)[0].git_ops
           for name in ("Bash", "PowerShell")}
    assert ops == {"Bash": None, "PowerShell": "commit"}


def test_pathological_commands_are_scanned_in_linear_time():
    commands = [
        "git " + "-C -x " * 40 + "status",                      # used to be 2 ** n parses
        "git " + "-c a=b -c  " * 2000 + "status",
        'echo "' + '\\"' * 100_000,                             # an unterminated quote, many escapes
        'echo "' + "$(" * 5000,                                 # unclosed command substitutions
        'echo "a" "' * 20_000,
        "echo '" + "<<EOF\n" * 20_000,
        "git commit -m x\n" + "cat <<EOF\n" * 20_000,
        "(" * 100_000,
        "".join(f"cat <<W{i}\n" for i in range(20_000)) + "git push",   # many markers, none closed
    ]
    begin = time.perf_counter()
    results = [tx.git_ops(command) for command in commands]
    assert time.perf_counter() - begin < 5.0
    assert results[0] is None and results[1] is None and results[6] == "commit"
    assert tx.git_ops(commands[0], "PowerShell") is None


def tool_use_line(name: str, tool_input, *, tool_id="toolu_G1", mid="msg_G1") -> str:
    return json.dumps({
        "type": "assistant", "uuid": f"u-{tool_id}", "sessionId": SESSION,
        "timestamp": "2026-10-14T10:00:00Z", "requestId": "req_G1",
        "message": {"id": mid, "model": "claude-opus-5-5",
                    "content": [{"type": "tool_use", "id": tool_id, "name": name,
                                 "input": tool_input}]},
    })


def test_bash_and_powershell_tool_calls_carry_git_ops(main_ctx):
    for name in ("Bash", "PowerShell"):
        items = tx.parse_line(tool_use_line(name, {"command": "git commit -m x && git push"}), main_ctx)
        assert len(items) == 1 and isinstance(items[0], ToolCall)
        assert items[0].name == name and items[0].git_ops == "commit+push"


def test_tool_calls_without_git_have_no_git_ops(main_ctx):
    def ops(name, tool_input):
        return tx.parse_line(tool_use_line(name, tool_input), main_ctx)[0].git_ops

    assert ops("Bash", {"command": "ls -la"}) is None
    assert ops("Edit", {"command": "git commit -m x"}) is None           # not a shell tool
    assert ops("Bash", {"command": 5}) is None                           # not a string
    assert ops("Bash", {"command": None}) is None
    assert ops("Bash", {}) is None
    assert ops("Bash", "git commit") is None                             # input is not an object
    assert ops("mcp__x__run", {"command": "git push"}) is None


def test_the_command_itself_is_never_kept(main_ctx):
    item = tx.parse_line(tool_use_line("Bash", {"command": "git commit -m secret-message"}), main_ctx)[0]
    assert "secret" not in repr(item)


def test_git_ops_in_the_main_fixture_are_none():
    items = tx.read_new(MAIN, 0, tx.file_context(MAIN, PROJECTS))[0]
    assert all(c.git_ops is None for c in of_type(items, ToolCall))


def result_line(blocks, *, ts="2026-10-14T10:01:00Z", **extra) -> str:
    rec = {"type": "user", "uuid": "u-res", "sessionId": SESSION, "timestamp": ts,
           "message": {"role": "user", "content": blocks}}
    rec.update(extra)
    return json.dumps(rec)


def test_error_tool_results_become_tool_errors(main_ctx):
    line = result_line([
        {"type": "tool_result", "tool_use_id": "toolu_BAD1", "is_error": True, "content": "boom"},
        {"type": "tool_result", "tool_use_id": "toolu_OK1", "is_error": False, "content": "fine"},
        {"type": "tool_result", "tool_use_id": "toolu_OK2", "content": "no flag"},
        {"type": "tool_result", "tool_use_id": "toolu_BAD2", "is_error": True, "content": "bang"},
        {"type": "tool_result", "tool_use_id": "toolu_BAD1", "is_error": True, "content": "again"},
        {"type": "text", "text": "hello", "is_error": True},                # not a tool_result
        {"type": "tool_result", "is_error": True, "content": "no id"},      # nothing to dedupe on
        {"type": "tool_result", "tool_use_id": "toolu_STR", "is_error": "true", "content": "x"},
    ])
    items = tx.parse_line(line, main_ctx)
    assert [(type(i).__name__, i.tool_use_id) for i in items] == [
        ("ToolError", "toolu_BAD1"), ("ToolError", "toolu_BAD2")]
    assert items[0] == ToolError("toolu_BAD1", SESSION, datetime(2026, 10, 14, 10, 1, tzinfo=UTC))


def test_a_tool_error_does_not_need_a_tool_use_result(main_ctx):
    line = result_line([{"type": "tool_result", "tool_use_id": "toolu_X", "is_error": True,
                         "content": "Error: file not found"}], toolUseResult="Error: file not found")
    assert [type(i).__name__ for i in tx.parse_line(line, main_ctx)] == ["ToolError"]


def test_a_tool_error_next_to_a_code_change_yields_both(main_ctx):
    line = result_line(
        [{"type": "tool_result", "tool_use_id": "toolu_E2", "is_error": True, "content": "x"}],
        toolUseResult={"type": "create", "content": "a\nb\n"})
    items = tx.parse_line(line, main_ctx)
    assert sorted(type(i).__name__ for i in items) == ["CodeChange", "ToolError"]


def test_tool_error_lines_need_a_timestamp_and_are_otherwise_tolerant(main_ctx):
    st = tx.ScanStats()
    no_ts = result_line([{"type": "tool_result", "tool_use_id": "t", "is_error": True}], ts=None)
    assert tx.parse_line(no_ts, main_ctx, stats=st) == [] and st.no_timestamp == 1
    for content in ("text", None, 7, [None, "x", 3]):
        line = json.dumps({"type": "user", "timestamp": "2026-10-14T10:00:00Z",
                           "message": {"content": content}, "x": '"is_error":true'})
        assert tx.parse_line(line, main_ctx) == []


def test_successful_tool_results_skip_the_prefilter(main_ctx):
    st = tx.ScanStats()
    line = result_line([{"type": "tool_result", "tool_use_id": "t", "is_error": False,
                         "content": "ok"}])
    assert tx.parse_line(line, main_ctx, stats=st) == [] and st.prefiltered == 1
