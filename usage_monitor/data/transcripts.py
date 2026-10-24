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
from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from usage_monitor import paths
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
    ToolError,
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
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
# Counts above this are clamped. Each is still exact as a float, so sums of them stay exact too.
MAX_COUNT = 2 ** 53

# Every item-producing line contains at least one of these JSON tokens (quotes included),
# so a line without any of them is skipped before json.loads. A token must exist for every
# record kind _items_for_record turns into an item; the real-data proof checks equivalence.
PREFILTER_TOKENS: tuple[str, ...] = (
    '"assistant"',         # assistant records -> RequestRecord, ToolCall
    '"structuredPatch"',   # user toolUseResult with a diff -> CodeChange
    '"create"',            # user toolUseResult with type "create" -> CodeChange
    '"gitOperation"',      # user toolUseResult -> GitEvent
    '"is_error":true',     # user tool_result block -> ToolError (compact JSON, as Claude Code writes it)
    '"is_error": true',    # ... and the spaced spelling
    '"pr-link"',           # -> PullRequest
    '"ai-title"',          # -> SessionMeta
    '"custom-title"',      # -> SessionMeta
    '"compact_boundary"',  # system -> Compaction
    '"api_error"',         # system -> ApiErrorEvent
)
# One alternation: a single C-level scan per line (about 3x faster than a loop of `in` checks).
_PREFILTER_RX = re.compile("|".join(re.escape(tok) for tok in PREFILTER_TOKENS))
_PREFILTER_RX_BYTES = re.compile(b"|".join(re.escape(tok.encode("ascii")) for tok in PREFILTER_TOKENS))


# ── Git activity in shell commands ────────────────────────────────────────────
# Since Claude Code 2.1.284 a commit no longer leaves toolUseResult.gitOperation, so Bash and
# PowerShell commands are read for git verbs. Only the verbs are kept, never the command.

SHELL_TOOLS = ("Bash", "PowerShell")
# << WORD, <<- WORD, <<~ WORD, with WORD bare, 'quoted', "quoted" or \escaped (not <<<, a here-string).
_HEREDOC = re.compile(r"""<<[-~]?[ \t]*(?:\\([^\s'"<>|&;()]+)|'([^'\s]+)'|"([^"\s]+)"|([^\s'"<>|&;()\\]+))""")
_SCAN_NORMAL = re.compile(r"""['"#<\n]""")
_SCAN_NESTED = re.compile(r"""['"#<\n()]""")
_DQ_BASH = re.compile(r"""[\\"$]""")
_DQ_POWERSHELL = re.compile(r"""[`"$]""")
_MAX_NESTING = 16                      # $( ... ) inside "..." inside $( ... ): deeper is left as text
# A quoted path to git, kept as the word "git" so that `& "C:\Program Files\Git\cmd\git.exe" commit` counts.
_QUOTED_GIT = re.compile(r"(?:.*[\\/])?git\.exe|.*[\\/]git", re.IGNORECASE | re.DOTALL)
# The options between "git" and the verb. -c / -C take a value; no other branch can match them,
# so a failed match has one way to parse the options, not two to the power of their number.
_GIT_PREFIX = (r"""\bgit(?:\.exe)?\b(?:\s+(?:-[cC]\s+(?:"[^"]*"|'[^']*'|[^\s"']\S*)"""
               r"""|(?!-[cC]\b)--?[A-Za-z][\w-]*(?:=\S+)?))*\s+""")
_GIT_VERBS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("commit", re.compile(_GIT_PREFIX + r"commit(?![\w-])")),
    ("push", re.compile(_GIT_PREFIX + r"push(?![\w-])")),
    ("pr", re.compile(r"\bgh\s+pr\s+create\b")),
)


def git_ops(command: str, tool: str = "Bash") -> str | None:
    """The git operations a shell command runs: "commit", "push", "pr" joined with "+", else None.

    Heredoc bodies, PowerShell here-strings and the contents of quoted strings are blanked first,
    so a commit message that mentions "git push" does not count, and neither does
    "git log --grep commit". ``tool`` picks the quoting rules: PowerShell escapes with a backtick
    and doubles its quotes, Bash escapes with a backslash.
    """
    text = _Blanker(command, tool == "PowerShell").run()
    found = [name for name, rx in _GIT_VERBS if rx.search(text)]
    return "+".join(found) if found else None


class _Blanker:
    """One left-to-right pass that empties every '...' and "..." string (keeping its quotes), the
    bodies of heredocs and PowerShell here-strings, and # comments. A single pass is needed
    because each construct hides the others: a "<<" or an apostrophe inside a string or a
    heredoc body means nothing. Work is linear in the length of the command."""

    def __init__(self, text: str, powershell: bool) -> None:
        self.text = text
        self.powershell = powershell
        self.dq_dead = False        # a " found no closing quote, so no later " can close one either
        self.lines: tuple[dict[str, list[int]], dict[str, list[int]]] | None = None   # line text -> starts

    def run(self) -> str:
        out: list[str] = []
        self._scan(0, out, 0)
        return "".join(out)

    def _scan(self, pos: int, out: list[str] | None, depth: int) -> int:
        """Blank text[pos:] into ``out`` (None: discard). At depth > 0 this is the inside of a
        $( ... ) and the scan stops after its closing paren; -1 when there is none."""
        text = self.text
        size = len(text)
        special = _SCAN_NESTED if depth else _SCAN_NORMAL
        pending: list[tuple[str, bool]] = []     # heredocs whose bodies start after this line
        parens = 0
        while True:
            found = special.search(text, pos)
            end = size if found is None else found.start()
            if out is not None:
                out.append(text[pos:end])
            if found is None:
                return -1 if depth else size
            char = found.group()
            pos = end + 1
            if char == "\n":
                if out is not None:
                    out.append("\n")
                if pending:
                    pos = self._skip_bodies(pending, pos)
                    pending = []
            elif char == "(":
                parens += 1
                if out is not None:
                    out.append(char)
            elif char == ")":
                if not parens:
                    return pos
                parens -= 1
                if out is not None:
                    out.append(char)
            elif char == "#":
                if end == 0 or text[end - 1] in " \t\r\n;&|(":
                    stop = text.find("\n", pos)
                    pos = size if stop < 0 else stop      # the comment goes, its newline stays
                elif out is not None:
                    out.append(char)
            elif char == "<":
                marker = _HEREDOC.match(text, end) if self._starts_heredoc(end) else None
                if marker is not None:
                    pending.append((next(group for group in marker.groups() if group), text[end + 2] in "-~"))
                    pos = marker.end()
                if out is not None:
                    out.append("<<" if marker is not None else char)
            else:
                pos = self._string(char, end, out, depth)

    def _starts_heredoc(self, at: int) -> bool:
        text = self.text
        if not text.startswith("<<", at) or text[at - 1:at] == "<" or text[at + 2:at + 3] == "<":
            return False
        marker = _HEREDOC.match(text, at)
        if marker is None:
            return False
        eol = text.find("\n", marker.end())
        if eol < 0:
            return False
        word = next(group for group in marker.groups() if group)
        # A marker with no closing line is not a heredoc (`$((1 << n))`).
        return self._closing_line(word, text[at + 2] in "-~", eol + 1) is not None

    def _closing_line(self, word: str, strip: bool, start: int) -> int | None:
        """Index just past the first line at or after ``start`` that equals ``word``, else None."""
        if self.lines is None:      # indexed once, so many markers do not each rescan the command
            self.lines = ({}, {})
            offset = 0
            for line in self.text.split("\n"):
                plain = line.rstrip("\r")
                self.lines[0].setdefault(plain, []).append(offset)
                self.lines[1].setdefault(plain.strip(), []).append(offset)
                offset += len(line) + 1
        starts = self.lines[1 if strip else 0].get(word, ())
        at = bisect_left(starts, start)
        if at == len(starts):
            return None
        stop = self.text.find("\n", starts[at])
        return len(self.text) if stop < 0 else stop + 1

    def _skip_bodies(self, pending: list[tuple[str, bool]], pos: int) -> int:
        for word, strip in pending:
            after = self._closing_line(word, strip, pos)
            if after is None:
                return len(self.text)
            pos = after
        return pos

    def _string(self, quote: str, at: int, out: list[str] | None, depth: int) -> int:
        """Handle the quote character at ``at`` and return where scanning resumes."""
        text = self.text
        if self.powershell and text[at - 1:at] == "@":
            here = self._here_string(quote, at)
            if here is not None:
                if out is not None:
                    out.append(quote * 2)
                return here
        end = self._end_single(at) if quote == "'" else self._end_double(at, depth)
        if end < 0:
            if quote == '"':
                self.dq_dead = True
            if out is not None:
                out.append(quote)
            return at + 1
        if out is not None:
            content = text[at + 1:end]
            quoted_git = len(content) < 512 and _QUOTED_GIT.fullmatch(content)
            out.append("git" if quoted_git else quote * 2)
        return end + 1

    def _here_string(self, quote: str, at: int) -> int | None:
        """PowerShell @'...'@ / @"..."@: the opener ends its line and the closer starts one."""
        text = self.text
        eol = text.find("\n", at + 1)
        if eol < 0 or text[at + 1:eol].strip():
            return None
        close = text.find("\n" + quote + "@", eol)
        return None if close < 0 else close + 3

    def _end_single(self, at: int) -> int:
        text = self.text
        end = text.find("'", at + 1)
        while self.powershell and end >= 0 and text[end + 1:end + 2] == "'":     # '' is a quote in the string
            end = text.find("'", end + 2)
        return end

    def _end_double(self, at: int, depth: int) -> int:
        """Index of the quote closing the string opened at ``at``, else -1."""
        if self.dq_dead:
            return -1
        text = self.text
        special = _DQ_POWERSHELL if self.powershell else _DQ_BASH
        pos = at + 1
        while True:
            found = special.search(text, pos)
            if found is None:
                return -1
            pos = found.start() + 1
            char = found.group()
            if char == '"':
                if self.powershell and text[pos:pos + 1] == '"':
                    pos += 1
                else:
                    return pos - 1
            elif char == "$":
                if text[pos:pos + 1] == "(" and depth < _MAX_NESTING:
                    pos = self._scan(pos + 1, None, depth + 1)       # a command substitution has quotes of its own
                    if pos < 0:
                        return -1
            else:
                pos += 1                                              # the escaped character


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
    * A file that cannot be opened or read to its end raises OSError. Nothing is returned for it,
      so the caller keeps its bookmark and reads the file again on a later run.
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
    with open(paths.fs_path(path), "rb") as fh:  # OSError propagates: the caller keeps its bookmark
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
        name = _str(block.get("name")) or "unknown"
        items.append(ToolCall(
            tool_use_id=tool_id,
            name=name,
            request_key=key,
            session_id=session,
            ts=ts,
            is_subagent=is_sub,
            git_ops=_shell_git_ops(name, block),
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
    msg = _as_dict(rec.get("message"))
    failed = _failed_tool_ids(msg)
    change = _code_change(tur) if isinstance(tur, dict) else None
    git = _git_flags(tur) if isinstance(tur, dict) else None
    if change is None and git is None and not failed:
        return []
    ts = _record_ts(rec, st)
    if ts is None:
        return []
    session = _str(rec.get("sessionId")) or ctx.session_hint
    items: list[TranscriptItem] = [ToolError(tool_use_id=tool_id, session_id=session, ts=ts)
                                   for tool_id in failed]
    if change is None and git is None:
        return items
    ref = _tool_result_ref(rec, msg)
    if ref is None:
        st.no_key += 1
        return items
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


def _shell_git_ops(name: str, block: dict[str, Any]) -> str | None:
    if name not in SHELL_TOOLS:
        return None
    command = _as_dict(block.get("input")).get("command")
    return git_ops(command, name) if isinstance(command, str) else None


def _failed_tool_ids(msg: dict[str, Any]) -> list[str]:
    """tool_use_ids of the tool_result blocks marked is_error, in order and without repeats."""
    ids: list[str] = []
    for block in _blocks(msg):
        tool_id = _str(block.get("tool_use_id"))
        if block.get("type") == "tool_result" and block.get("is_error") is True                 and tool_id is not None and tool_id not in ids:
            ids.append(tool_id)
    return ids


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
        utc = parsed.astimezone(timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    # Older dates are never real transcript times, and the store cannot always turn them back.
    return utc if utc >= _EPOCH else None


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
    return _storable(value) if isinstance(value, str) and value else None


def _text(value: object) -> str | None:
    return _storable(value) if isinstance(value, str) and value.strip() else None


def _storable(text: str) -> str:
    """The text with lone surrogates replaced. A JSON escape such as \\ud800 decodes to one, and SQLite refuses it."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return text.encode("utf-8", "replace").decode("utf-8")
    return text


def _present(value: object) -> bool:
    return value is not None and value is not False


def _count_or_none(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return min(value, MAX_COUNT)


def _count(value: object) -> int:
    return _count_or_none(value) or 0
