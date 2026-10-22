"""Tolerant JSONL parser for Claude Code transcripts (~/.claude/projects/**/*.jsonl).

Public API (contract in docs/ARCHITECTURE.md, "data/transcripts.py"):

    file_context(path, projects_root) -> FileContext
    parse_line(line, ctx)             -> list[TranscriptItem]   never raises
    read_new(path, offset, ctx)       -> (items, new_offset)    stops at a line boundary

The transcript format is internal to Claude Code and changes between versions, so every
accessor type-checks its input: bad lines are skipped and counted, never fatal. Message
text, tool inputs and file contents are never copied into an item or a log line.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from usage_monitor.data.models import (
    ApiErrorEvent,
    CodeChange,
    Compaction,
    FileContext,
    GitEvent,
    PullRequest,
    RequestRecord,
    SessionMeta,
    ToolCall,
    TranscriptItem,
)

try:  # usage_monitor/log.py is written in parallel; fall back to stdlib logging
    from usage_monitor.log import get_logger

    log = get_logger(__name__)
except ImportError:
    log = logging.getLogger("usage_monitor." + __name__.split(".")[-1])

SYNTHETIC_MODEL = "<synthetic>"
UNKNOWN_MODEL = "unknown"
_CHUNK = 64 * 1024
_FRACTION = re.compile(r"\.(\d+)")

# Every item-producing line contains at least one of these JSON tokens (quotes included),
# so a line without any of them is skipped before json.loads. A token must exist for every
# record kind _items_for_record turns into an item; the real-data proof checks equivalence.
PREFILTER_TOKENS: tuple[str, ...] = (
    '"assistant"',         # assistant records -> RequestRecord, ToolCall
    '"structuredPatch"',   # user toolUseResult with a diff -> CodeChange
    '"create"',            # user toolUseResult with type "create" -> CodeChange
    '"gitOperation"',      # user toolUseResult -> GitEvent
    '"pr-link"',           # -> PullRequest
    '"ai-title"',          # -> SessionMeta
    '"custom-title"',      # -> SessionMeta
    '"compact_boundary"',  # system -> Compaction
    '"api_error"',         # system -> ApiErrorEvent
)
# One alternation: a single C-level scan per line (about 3x faster than a loop of `in` checks).
_PREFILTER_RX = re.compile("|".join(re.escape(tok) for tok in PREFILTER_TOKENS))
_PREFILTER_RX_BYTES = re.compile(b"|".join(re.escape(tok.encode("ascii")) for tok in PREFILTER_TOKENS))


@dataclass(slots=True)
class ScanStats:
    """Counters for parse_line / read_new. Pass an instance in to collect them."""
    lines: int = 0          # non-blank lines examined
    prefiltered: int = 0    # skipped by the substring prefilter (never decoded)
    malformed: int = 0      # not JSON, or JSON that is not an object
    no_timestamp: int = 0   # item-producing records dropped: missing or unparseable timestamp
    no_key: int = 0         # records with no message.id / uuid / tool_use_id to dedupe on
    synthetic: int = 0      # assistant records with model "<synthetic>" (skipped)
    items: int = 0          # items returned

    def add(self, other: ScanStats) -> None:
        for name in ScanStats.__slots__:
            setattr(self, name, getattr(self, name) + getattr(other, name))


# ── Files ─────────────────────────────────────────────────────────────────────

def file_context(path: Path, projects_root: Path) -> FileContext:
    """Locate a transcript: <root>/<project>/<session>.jsonl, or ".../<session>/subagents/agent-<id>.jsonl"."""
    full = Path(path).absolute()
    try:
        parts = full.relative_to(Path(projects_root).absolute()).parts
    except ValueError:
        parts = ()
    project = parts[0] if len(parts) >= 2 else ""
    is_subagent = full.parent.name == "subagents"
    stem = full.stem
    agent_id: str | None = None
    if stem.startswith("agent-"):
        agent_id = stem[len("agent-"):] or None
    elif is_subagent:
        agent_id = stem or None
    # Subagent files: the parent of "subagents" is the session folder. Main files: the stem.
    session_hint = (full.parent.parent.name if is_subagent else stem) or None
    return FileContext(
        path=str(full),
        project=project,
        is_subagent=is_subagent,
        agent_id=agent_id,
        session_hint=session_hint,
    )


def read_new(
    path: Path,
    offset: int,
    ctx: FileContext,
    *,
    stats: ScanStats | None = None,
) -> tuple[list[TranscriptItem], int]:
    """Items from the complete lines at or after ``offset``, and the new offset.

    * The returned offset is always a line boundary: a trailing line without its newline is
      left unread and picked up by the next call once it is complete.
    * CRLF and LF both work; the offset counts the raw bytes, including the line ending.
    * ``offset`` beyond EOF means the file was truncated or replaced: nothing is read and the
      returned offset is the last line boundary within the current size. The ingestor sees
      size < bookmark and re-reads from 0 (the store dedupes). A negative offset reads from 0.
    * A file that cannot be opened yields ([], offset) and a debug log line.
    """
    return _scan(Path(path), offset, ctx, prefilter=True, stats=stats)


def _scan(
    path: Path,
    offset: int,
    ctx: FileContext,
    *,
    prefilter: bool,
    stats: ScanStats | None,
) -> tuple[list[TranscriptItem], int]:
    # prefilter=False is the reference path for the equivalence proof and tests only.
    local = ScanStats()
    items: list[TranscriptItem] = []
    start = max(offset, 0)
    consumed = 0
    try:
        fh = open(path, "rb")
    except OSError as exc:
        log.debug("transcript %s not readable (%s); offset kept", path.name, type(exc).__name__)
        return items, start
    with fh:
        try:
            size = os.fstat(fh.fileno()).st_size
            if offset > size:
                end = _last_boundary(fh, size)
                log.debug("transcript %s shrank below offset; offset reset to %d", path.name, end)
                return items, end
            fh.seek(start)
            for raw in fh:
                if not raw.endswith(b"\n"):
                    break  # partial line still being written; it is read on a later call
                consumed += len(raw)
                body = raw.rstrip(b"\r\n")
                if not body.strip(b" \t"):
                    continue
                local.lines += 1
                if prefilter and _PREFILTER_RX_BYTES.search(body) is None:
                    local.prefiltered += 1
                    continue
                items.extend(_decode_items(body.decode("utf-8", errors="replace"), ctx, local))
        except OSError as exc:
            log.debug("transcript %s: read stopped early (%s)", path.name, type(exc).__name__)
    local.items = len(items)
    if local.malformed:
        log.debug("transcript %s: %d malformed line(s) skipped", path.name, local.malformed)
    if stats is not None:
        stats.add(local)
    return items, start + consumed


def _last_boundary(fh: Any, size: int) -> int:
    """Offset just past the last newline at or before ``size`` (0 when there is none)."""
    end = size
    while end > 0:
        begin = max(0, end - _CHUNK)
        fh.seek(begin)
        idx = fh.read(end - begin).rfind(b"\n")
        if idx >= 0:
            return begin + idx + 1
        end = begin
    return 0


# ── Lines ─────────────────────────────────────────────────────────────────────

def parse_line(line: str, ctx: FileContext, *, stats: ScanStats | None = None) -> list[TranscriptItem]:
    """Items from one JSONL line (newline optional). Never raises; [] for non-items."""
    local = ScanStats()
    items: list[TranscriptItem] = []
    if not isinstance(line, str):
        local.malformed += 1
    else:
        text = line.strip(" \t\r\n")
        if text:
            local.lines += 1
            if _PREFILTER_RX.search(text) is None:
                local.prefiltered += 1
            else:
                items = _decode_items(text, ctx, local)
    local.items = len(items)
    if stats is not None:
        stats.add(local)
    return items


def _decode_items(text: str, ctx: FileContext, st: ScanStats) -> list[TranscriptItem]:
    if text.startswith("\ufeff"):
        text = text[1:]
    try:
        obj = json.loads(text)
    except (ValueError, RecursionError):  # JSONDecodeError is a ValueError
        st.malformed += 1
        return []
    if not isinstance(obj, dict):
        st.malformed += 1
        return []
    return _items_for_record(obj, ctx, st)


def _items_for_record(rec: dict[str, Any], ctx: FileContext, st: ScanStats) -> list[TranscriptItem]:
    kind = rec.get("type")
    if kind == "assistant":
        return _assistant_items(rec, ctx, st)
    if kind == "user":
        return _tool_result_items(rec, ctx, st)
    if kind == "pr-link":
        return _pr_link_items(rec, ctx)
    if kind in ("ai-title", "custom-title"):
        return _title_items(rec, ctx)
    if kind == "system":
        return _system_items(rec, ctx, st)
    return []


# ── Record mappers ────────────────────────────────────────────────────────────

def _assistant_items(rec: dict[str, Any], ctx: FileContext, st: ScanStats) -> list[TranscriptItem]:
    msg = _as_dict(rec.get("message"))
    usage = msg.get("usage")
    tool_blocks = [b for b in _blocks(msg) if b.get("type") == "tool_use"]
    if not isinstance(usage, dict) and not tool_blocks:
        return []
    if msg.get("model") == SYNTHETIC_MODEL:
        st.synthetic += 1
        return []
    ts = _record_ts(rec, st)
    if ts is None:
        return []
    key = _request_key(rec, msg)
    session = _str(rec.get("sessionId")) or ctx.session_hint
    # Sidechain (subagent) lines also sit inside main-session files in real data, so the
    # record flag counts as well as the file location.
    is_sub = ctx.is_subagent or rec.get("isSidechain") is True
    agent = _str(rec.get("agentId")) or ctx.agent_id
    items: list[TranscriptItem] = []
    if isinstance(usage, dict):
        if key is None:
            st.no_key += 1
        else:
            items.append(_request_record(rec, msg, usage, key, ctx, ts, session, is_sub, agent))
    for block in tool_blocks:
        tool_id = _str(block.get("id"))
        if tool_id is None:
            st.no_key += 1
            continue
        items.append(ToolCall(
            tool_use_id=tool_id,
            name=_str(block.get("name")) or "unknown",
            request_key=key,
            session_id=session,
            ts=ts,
            is_subagent=is_sub,
        ))
    return items


def _request_record(
    rec: dict[str, Any],
    msg: dict[str, Any],
    usage: dict[str, Any],
    key: str,
    ctx: FileContext,
    ts: datetime,
    session: str | None,
    is_sub: bool,
    agent: str | None,
) -> RequestRecord:
    # usage.iterations[] is per-iteration detail. In real data it holds one entry equal to
    # the top-level numbers, so the top-level usage is the request total and is never summed.
    five_m, one_h = _cache_write_split(usage)
    details = _as_dict(usage.get("output_tokens_details"))
    tools = _as_dict(usage.get("server_tool_use"))
    return RequestRecord(
        key=key,
        message_id=_str(msg.get("id")),
        request_id=_str(rec.get("requestId")),
        ts=ts,
        session_id=session,
        project=ctx.project,
        cwd=_str(rec.get("cwd")),
        git_branch=_str(rec.get("gitBranch")),
        is_subagent=is_sub,
        agent_id=agent,
        model=_str(msg.get("model")) or UNKNOWN_MODEL,
        input_tokens=_count(usage.get("input_tokens")),
        output_tokens=_count(usage.get("output_tokens")),
        cache_read_tokens=_count(usage.get("cache_read_input_tokens")),
        cache_write_5m_tokens=five_m,
        cache_write_1h_tokens=one_h,
        thinking_tokens=_count(details.get("thinking_tokens")),
        web_search_requests=_count(tools.get("web_search_requests")),
        web_fetch_requests=_count(tools.get("web_fetch_requests")),
        speed=_str(usage.get("speed")),
        service_tier=_str(usage.get("service_tier")),
        effort=_str(rec.get("effort")),
        entrypoint=_str(rec.get("entrypoint")),
        cc_version=_str(rec.get("version")),
    )


def _tool_result_items(rec: dict[str, Any], ctx: FileContext, st: ScanStats) -> list[TranscriptItem]:
    tur = rec.get("toolUseResult")
    if not isinstance(tur, dict):
        return []
    change = _code_change(tur)
    git = _git_flags(tur)
    if change is None and git is None:
        return []
    ref = _tool_result_ref(rec, _as_dict(rec.get("message")))
    if ref is None:
        st.no_key += 1
        return []
    ts = _record_ts(rec, st)
    if ts is None:
        return []
    session = _str(rec.get("sessionId")) or ctx.session_hint
    items: list[TranscriptItem] = []
    if change is not None:
        added, removed, is_new = change
        items.append(CodeChange(
            tool_use_id=ref,
            session_id=session,
            ts=ts,
            lines_added=added,
            lines_removed=removed,
            is_new_file=is_new,
        ))
    if git is not None:
        commit, push, pr = git
        items.append(GitEvent(tool_use_id=ref, session_id=session, ts=ts, commit=commit, push=push, pr=pr))
    return items


def _pr_link_items(rec: dict[str, Any], ctx: FileContext) -> list[TranscriptItem]:
    url = _str(rec.get("prUrl"))
    if url is None:
        return []
    return [PullRequest(
        url=url,
        number=_count_or_none(rec.get("prNumber")),
        repository=_str(rec.get("prRepository")),
        session_id=_str(rec.get("sessionId")) or ctx.session_hint,
        ts=_parse_ts(rec.get("timestamp")),
    )]


def _title_items(rec: dict[str, Any], ctx: FileContext) -> list[TranscriptItem]:
    session = _str(rec.get("sessionId")) or ctx.session_hint
    if session is None:
        return []
    if rec.get("type") == "ai-title":
        ai_title, custom_title = _text(rec.get("aiTitle")), None
    else:
        ai_title, custom_title = None, _text(rec.get("customTitle"))
    if ai_title is None and custom_title is None:
        return []
    return [SessionMeta(session_id=session, project=ctx.project, ai_title=ai_title, custom_title=custom_title)]


def _system_items(rec: dict[str, Any], ctx: FileContext, st: ScanStats) -> list[TranscriptItem]:
    subtype = rec.get("subtype")
    if subtype not in ("compact_boundary", "api_error"):
        return []
    uuid = _str(rec.get("uuid"))
    if uuid is None:
        st.no_key += 1
        return []
    ts = _record_ts(rec, st)
    if ts is None:
        return []
    session = _str(rec.get("sessionId")) or ctx.session_hint
    if subtype == "api_error":
        return [ApiErrorEvent(uuid=uuid, session_id=session, ts=ts)]
    meta = _as_dict(rec.get("compactMetadata"))
    return [Compaction(
        uuid=uuid,
        session_id=session,
        ts=ts,
        trigger=_str(meta.get("trigger")),
        pre_tokens=_count_or_none(meta.get("preTokens")),
        post_tokens=_count_or_none(meta.get("postTokens")),
    )]


# ── Field helpers ─────────────────────────────────────────────────────────────

def _request_key(rec: dict[str, Any], msg: dict[str, Any]) -> str | None:
    # Claude Code writes one line per content block and repeats the usage on each. Lines of
    # one API response share message.id and requestId, so they dedupe to one key.
    mid = _str(msg.get("id"))
    if mid is not None:
        return f"{mid}|{_str(rec.get('requestId')) or ''}"
    uuid = _str(rec.get("uuid"))
    return f"uuid|{uuid}" if uuid is not None else None


def _tool_result_ref(rec: dict[str, Any], msg: dict[str, Any]) -> str | None:
    for block in _blocks(msg):
        tool_id = _str(block.get("tool_use_id")) if block.get("type") == "tool_result" else None
        if tool_id is not None:
            return tool_id
    uuid = _str(rec.get("uuid"))
    return f"uuid|{uuid}" if uuid is not None else None


def _code_change(tur: dict[str, Any]) -> tuple[int, int, bool] | None:
    """(lines_added, lines_removed, is_new_file) for an Edit/Write result, else None."""
    hunks = tur.get("structuredPatch")
    diff = [h for h in hunks if isinstance(h, dict)] if isinstance(hunks, list) else []
    is_new = tur.get("type") == "create"
    if diff:
        added = removed = 0
        for hunk in diff:
            lines = hunk.get("lines")
            if not isinstance(lines, list):
                continue
            for line in lines:
                if isinstance(line, str) and line.startswith("+"):
                    added += 1
                elif isinstance(line, str) and line.startswith("-"):
                    removed += 1
        return added, removed, is_new
    content = tur.get("content")
    if is_new and isinstance(content, str):
        return _line_count(content), 0, True
    return None


def _line_count(text: str) -> int:
    lines = text.count("\n")
    return lines + 1 if text and not text.endswith("\n") else lines


def _git_flags(tur: dict[str, Any]) -> tuple[bool, bool, bool] | None:
    op = tur.get("gitOperation")
    if not isinstance(op, dict):
        return None
    flags = (_present(op.get("commit")), _present(op.get("push")), _present(op.get("pr")))
    return flags if any(flags) else None


def _cache_write_split(usage: dict[str, Any]) -> tuple[int, int]:
    """(5-minute, 1-hour) cache-write tokens. Tokens the split does not cover count as 5m."""
    total = _count_or_none(usage.get("cache_creation_input_tokens"))
    cc = usage.get("cache_creation")
    if isinstance(cc, dict):
        five = _count_or_none(cc.get("ephemeral_5m_input_tokens"))
        one = _count_or_none(cc.get("ephemeral_1h_input_tokens"))
        if five is not None or one is not None:
            five, one = five or 0, one or 0
            if total is None:
                return five, one
            return five + max(0, total - five - one), one
    return total or 0, 0


def _record_ts(rec: dict[str, Any], st: ScanStats) -> datetime | None:
    ts = _parse_ts(rec.get("timestamp"))
    if ts is None:
        st.no_timestamp += 1
    return ts


def _parse_ts(value: object) -> datetime | None:
    """ISO-8601 to aware UTC. A trailing Z and a missing offset (naive) are read as UTC."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = _fromiso(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        return parsed.astimezone(timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _fromiso(text: str) -> datetime:
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        # Python 3.10 accepts only 3- or 6-digit fractions; pad or cut the fraction to 6.
        return datetime.fromisoformat(_FRACTION.sub(_pad_fraction, text, count=1))


def _pad_fraction(match: re.Match[str]) -> str:
    return "." + (match.group(1) + "000000")[:6]


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _blocks(msg: dict[str, Any]) -> list[dict[str, Any]]:
    content = msg.get("content")
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict)]


def _str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _present(value: object) -> bool:
    return value is not None and value is not False


def _count_or_none(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _count(value: object) -> int:
    return _count_or_none(value) or 0
