"""Tests for usage_monitor.data.transcripts. Synthetic fixtures only (tests/fixtures/transcripts)."""
from __future__ import annotations

import json
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


def test_missing_file_returns_nothing_and_keeps_the_offset(tmp_path, main_ctx):
    assert tx.read_new(tmp_path / "gone.jsonl", 42, main_ctx) == ([], 42)


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
    assert st.items == len(items) == 23
    assert st.synthetic == 1
    assert st.no_timestamp == 0 and st.no_key == 0 and st.malformed == 0
    assert st.prefiltered > 0
