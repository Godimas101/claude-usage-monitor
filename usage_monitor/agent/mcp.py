"""A small MCP server on stdio with one tool, get_plan_usage (#24).

Claude Code starts it as ``ClaudeUsageMonitor.exe --mcp``. Messages are JSON-RPC 2.0, one per
line, as the MCP stdio transport defines. Only replies go to stdout; anything worth logging goes
to agent.log. The tool reads the status file (agent/status.py), so a call costs no model tokens
and, while the app runs, no request of its own.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

from usage_monitor.agent import status
from usage_monitor.log import get_logger

log = get_logger(__name__)

SERVER_NAME = "claude-usage-monitor"
TOOL_NAME = "get_plan_usage"
SUPPORTED_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_VERSION = "2025-06-18"   # answered when the client asks for a version not listed above
INSTRUCTIONS = (
    "Call get_plan_usage before and between long or parallel work (multi-agent workflows, large "
    "fan-outs) to see how much of the Claude plan's 5-hour session and weekly limits is left, when "
    "each resets and whether the current pace is safe."
)
TOOL = {
    "name": TOOL_NAME,
    "title": "Claude plan usage",
    "description": (
        "Live usage of the Claude plan from Claude Usage Monitor: the 5-hour session, the weekly "
        "limit and any model-scoped weekly limit (such as Fable), each with percent used, reset "
        "time, pace and projected time to the limit, plus the recent burn rate. Read-only. Returns "
        "a one-line summary followed by the full reading as JSON."
    ),
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    "annotations": {"readOnlyHint": True, "openWorldHint": False},
}
_PARSE_ERROR, _INVALID_REQUEST, _METHOD_NOT_FOUND, _INVALID_PARAMS = -32700, -32600, -32601, -32602


def serve(stdin: BinaryIO, stdout: BinaryIO, *, folder: Path, clock: Callable[[], datetime],
          app_version: str) -> int:
    """Answer requests until stdin closes."""
    for raw in stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            _send(stdout, _error(None, _PARSE_ERROR, "Parse error"))
            continue
        batch = message if isinstance(message, list) else [message]
        replies = [reply for reply in (handle(item, folder=folder, clock=clock, app_version=app_version)
                                       for item in batch) if reply is not None]
        if replies:
            _send(stdout, replies if isinstance(message, list) else replies[0])
    return 0


def handle(message: Any, *, folder: Path, clock: Callable[[], datetime], app_version: str) -> dict | None:
    """The reply to one message, or None for a notification (or a response we never asked for)."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(message.get("id") if isinstance(message, dict) else None, _INVALID_REQUEST,
                      "Invalid Request")
    method = message.get("method")
    if "id" not in message or not isinstance(method, str):
        return None
    msg_id = message["id"]
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    if method == "initialize":
        asked = params.get("protocolVersion")
        return _result(msg_id, {
            "protocolVersion": asked if asked in SUPPORTED_VERSIONS else DEFAULT_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "title": "Claude Usage Monitor", "version": app_version},
            "instructions": INSTRUCTIONS,
        })
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        return _result(msg_id, {"tools": [TOOL]})
    if method == "tools/call":
        if params.get("name") != TOOL_NAME:
            return _error(msg_id, _INVALID_PARAMS, f"Unknown tool: {params.get('name')}")
        return _result(msg_id, call_tool(folder=folder, clock=clock, app_version=app_version))
    return _error(msg_id, _METHOD_NOT_FOUND, f"Method not found: {method}")


def call_tool(*, folder: Path, clock: Callable[[], datetime], app_version: str) -> dict:
    """The get_plan_usage result: the summary line, then the reading as JSON (and as structured content)."""
    try:
        doc, running = status.current(folder, now=clock(), fetch=True, wait_s=status.WAIT_S, app_version=app_version)
        out = status.report(doc, now=clock(), running=running)
    except Exception as exc:  # the server must answer; the session sees why there is no reading
        log.exception("get_plan_usage failed")
        return {"content": [{"type": "text", "text": f"Could not read the plan usage ({type(exc).__name__})."}],
                "isError": True}
    body = {key: value for key, value in out.items() if key != "summary"}
    text = f"{out['summary']}\n\n{json.dumps(body, indent=1)}"
    return {"content": [{"type": "text", "text": text}], "structuredContent": out, "isError": False}


def _result(msg_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _send(stdout: BinaryIO, payload: Any) -> None:
    stdout.write(json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n")
    stdout.flush()
