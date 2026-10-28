"""Tests for agent/setup.py (#27): the hooks and the MCP tool that one click writes into Claude Code's settings.

Nothing here reads a real ~/.claude or runs a real claude. Every test passes a Context whose home and config folder are
in tmp_path and whose runner is FakeClaude, the claude command kept in memory; an autouse fixture makes a test that
forgets the Context (the real machine) or reaches for subprocess fail loudly.
"""
from __future__ import annotations

import builtins
import dataclasses
import io
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from usage_monitor.agent import setup
from usage_monitor.agent.setup import Ran

EXE = r"C:\Users\Test User\AppData\Local\ClaudeUsageMonitor\ClaudeUsageMonitor.exe"
CMD = "C:/Users/Test User/AppData/Local/ClaudeUsageMonitor/ClaudeUsageMonitor.exe"   # EXE as a hook's command
CLAUDE_CMD = r"C:\Users\Test User\AppData\Roaming\npm\claude.CMD"
CLAUDE_EXE = r"C:\Users\Test User\.local\bin\claude.exe"
NOW = datetime(2026, 10, 28, 18, 5, 9)
BACKUP_NAME = "settings.json.cum-backup-20261028-180509"
GET_TEXT = (
    "claude-usage:\n  Scope: User config (available in all your projects)\n  Status: ✓ Connected\n  Type: stdio\n"
    "  Command: {command}\n  Args: {args}\n  Environment:\n\n"
    'To remove this server, run: claude mcp remove "claude-usage" -s user\n'
)


@pytest.fixture(autouse=True)
def nothing_real(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a setup test must pass a Context and a fake claude: no real ~/.claude, no real process")

    monkeypatch.setattr(setup, "default_context", refuse)
    monkeypatch.setattr(setup.subprocess, "run", refuse)


class FakeClaude:
    """The claude command, in memory: it answers --version and mcp get / add / remove the way Claude Code does, keeps
    the claude-usage tool, and records every call. ``forced`` makes one kind of call answer something else."""

    def __init__(self, version: str = "2.1.160 (Claude Code)", tool: tuple[str, list[str]] | None = None) -> None:
        self.version = version
        self.tool = tool                         # (command, args) of the claude-usage tool, or None
        self.calls: list[list[str]] = []
        self.forced: dict[str, Ran] = {}         # "version" | "get" | "add" | "remove"

    def __call__(self, argv: Sequence[str]) -> Ran:
        words = list(argv)
        self.calls.append(words)
        rest = words[1:]
        if rest == ["--version"]:
            return self.forced.get("version") or Ran(0, f"{self.version}\n", "")
        if rest == ["mcp", "get", setup.MCP_NAME]:
            if "get" in self.forced:
                return self.forced["get"]
            if self.tool is None:
                return Ran(1, "", 'No MCP server found with name: "claude-usage".\n')
            return Ran(0, GET_TEXT.format(command=self.tool[0], args=" ".join(self.tool[1])), "")
        if rest[:2] == ["mcp", "add"]:
            if "add" in self.forced:
                return self.forced["add"]
            if self.tool is not None:
                return Ran(1, "", "MCP server claude-usage already exists in user config\n")
            tail = rest[rest.index("--") + 1:]
            self.tool = (tail[0], tail[1:])
            return Ran(0, "Added stdio MCP server claude-usage to user config\n", "")
        if rest == ["mcp", "remove", setup.MCP_NAME, "-s", "user"]:
            if "remove" in self.forced:
                return self.forced["remove"]
            if self.tool is None:
                return Ran(1, "", 'No MCP server found with name: "claude-usage".\n')
            self.tool = None
            return Ran(0, "Removed MCP server claude-usage from user config\n", "")
        raise AssertionError(f"an unexpected claude call: {words}")

    def kinds(self) -> list[str]:
        """The calls in order: version, get, add, remove."""
        return ["version" if call[1:] == ["--version"] else call[2] for call in self.calls]


@dataclasses.dataclass
class Rig:
    ctx: setup.Context
    claude: FakeClaude
    home: Path
    settings: Path

    @property
    def folder(self) -> Path:
        return self.settings.parent

    def write(self, doc: Any) -> bytes:
        """Put ``doc`` (a JSON value, or text or bytes to write as they are) in the settings file. Returns its bytes."""
        self.folder.mkdir(parents=True, exist_ok=True)
        if isinstance(doc, bytes):
            raw = doc
        elif isinstance(doc, str):
            raw = doc.encode("utf-8")
        else:
            raw = json.dumps(doc, indent=2).encode("utf-8")
        self.settings.write_bytes(raw)
        return raw

    def load(self) -> Any:
        return json.loads(self.settings.read_text(encoding="utf-8"))

    def files(self) -> list[str]:
        return sorted(path.name for path in self.folder.iterdir()) if self.folder.exists() else []


def make_rig(tmp_path: Path, *, frozen: bool = True, version: str = "2.1.160 (Claude Code)", tool: Any = None,
             claude_path: str | None = CLAUDE_EXE, executable: str = EXE, config_dir: bool = False,
             env: dict[str, str] | None = None) -> Rig:
    home = tmp_path / "home"
    environment = {"USERPROFILE": str(home)}
    folder = home / ".claude"
    if config_dir:
        folder = tmp_path / "elsewhere"
        environment["CLAUDE_CONFIG_DIR"] = str(folder)
    environment.update(env or {})
    fake = FakeClaude(version, tool)
    ctx = setup.Context(frozen=frozen, executable=executable, env=environment, home=home,
                        which=lambda name: claude_path if name == "claude" else None, run=fake, now=lambda: NOW)
    return Rig(ctx, fake, home, folder / "settings.json")


def ours(command: str = CMD) -> dict:
    """The three events as setup writes them."""
    return {
        "SessionStart": [{"hooks": [{"type": "command", "command": command, "args": ["--hook", "--always"],
                                     "timeout": 30}]}],
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": command, "args": ["--hook"], "timeout": 10}]}],
        "PostToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": command, "args": ["--hook"],
                                                    "timeout": 10}]}],
    }


GATE_ENTRY = {"matcher": "Agent|Workflow", "hooks": [{"type": "command", "command": CMD,
                                                      "args": ["--gate", "--session", "95", "--weekly", "85"],
                                                      "timeout": 45}]}
THEIR_FORMATTER = {"matcher": "Bash", "hooks": [{"type": "command", "command": "node format.js", "timeout": 5}]}
THEIR_STOP = {"hooks": [{"type": "command", "command": "echo done"}]}


def rich_settings() -> dict:
    """What a user's file may hold: other keys, a gate entry of their own, hooks of other programs in an event of ours."""
    return {
        "model": "opus",
        "permissions": {"allow": ["Bash(git status)", "mcp__claude-usage__get_plan_usage"], "deny": []},
        "env": {"NOTE": "naïve ✓"},
        "hooks": {"PreToolUse": [GATE_ENTRY], "PostToolUse": [THEIR_FORMATTER], "Stop": [THEIR_STOP]},
    }


# ── A fresh file ────────────────────────────────────────────────────────────────────

def test_a_fresh_install_writes_the_three_hooks_and_registers_the_tool(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    result = setup.install(rig.ctx)
    assert result["ok"] is True and result["errors"] == [] and result["action"] == "install"
    assert rig.load() == {"hooks": ours()}
    assert rig.files() == ["settings.json"]                                  # no backup: there was nothing to keep
    assert result["backup"] is None and result["settings_path"] == str(rig.settings)
    assert result["changed"] == ["Added the SessionStart hook.", "Added the UserPromptSubmit hook.",
                                 "Added the PostToolUse hook.", "Registered the claude-usage MCP tool."]
    assert setup.VSCODE_WARNING in result["warnings"] and result["manual"] is None
    assert rig.claude.calls == [
        [CLAUDE_EXE, "--version"],
        [CLAUDE_EXE, "mcp", "get", "claude-usage"],
        [CLAUDE_EXE, "mcp", "add", "--transport", "stdio", "--scope", "user", "claude-usage", "--", EXE, "--mcp"],
    ]
    assert rig.claude.tool == (EXE, ["--mcp"])


def test_the_file_is_utf8_json_with_two_space_indent_and_a_trailing_newline(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.write({"env": {"NOTE": "naïve ✓"}})
    setup.install(rig.ctx)
    text = rig.settings.read_bytes().decode("utf-8")
    assert text.endswith("}\n") and not text.endswith("\n\n") and "\r" not in text
    assert text.startswith('{\n  "env": {\n    "NOTE": "naïve ✓"\n  },\n  "hooks": {\n    "SessionStart": [')
    assert "\\u" not in text                                                  # non-ASCII text stays readable


def test_the_command_is_the_exe_with_forward_slashes_and_the_tool_gets_the_native_path(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    setup.install(rig.ctx)
    command = rig.load()["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert command == CMD and "\\" not in command
    assert rig.claude.tool is not None and rig.claude.tool[0] == EXE


# ── Existing hooks and keys ──────────────────────────────────────────────────────────

def test_install_keeps_every_other_key_and_hook_and_backs_the_file_up_first(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    original = rig.write(rich_settings())
    result = setup.install(rig.ctx)
    after = rig.load()
    expected = rich_settings()
    expected["hooks"]["SessionStart"] = ours()["SessionStart"]
    expected["hooks"]["UserPromptSubmit"] = ours()["UserPromptSubmit"]
    expected["hooks"]["PostToolUse"] = [THEIR_FORMATTER, *ours()["PostToolUse"]]
    assert after == expected
    assert after["hooks"]["PreToolUse"] == [GATE_ENTRY]                      # the user's own --gate entry is not ours to touch
    assert list(after) == ["model", "permissions", "env", "hooks"]           # order kept, hooks last where it was
    assert Path(result["backup"]).name == BACKUP_NAME and Path(result["backup"]).parent == rig.folder
    assert Path(result["backup"]).read_bytes() == original                   # the file as it was
    assert rig.files() == ["settings.json", BACKUP_NAME]


def test_install_replaces_our_old_entry_where_it_stands_and_leaves_a_shared_group_to_its_other_hook(
        tmp_path: Path) -> None:
    old = {"hooks": [{"type": "command", "command": CMD, "args": ["--hook", "--threshold", "80"], "timeout": 10}]}
    shared = {"matcher": "startup", "hooks": [THEIR_FORMATTER["hooks"][0],
                                              {"type": "command", "command": CMD, "args": ["--hook"], "timeout": 5}]}
    rig = make_rig(tmp_path)
    rig.write({"hooks": {"UserPromptSubmit": [old, THEIR_STOP], "SessionStart": [shared]}})
    result = setup.install(rig.ctx)
    hooks = rig.load()["hooks"]
    assert hooks["UserPromptSubmit"] == [ours()["UserPromptSubmit"][0], THEIR_STOP]          # replaced in place
    assert hooks["SessionStart"] == [{"matcher": "startup", "hooks": [THEIR_FORMATTER["hooks"][0]]},
                                     ours()["SessionStart"][0]]
    assert "Updated the UserPromptSubmit hook." in result["changed"]
    assert "Added the PostToolUse hook." in result["changed"] and "Updated the SessionStart hook." in result["changed"]


def test_a_second_copy_of_our_hook_in_an_event_is_not_kept(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    twice = ours()
    twice["SessionStart"] = [twice["SessionStart"][0], THEIR_STOP, twice["SessionStart"][0]]
    rig.write({"hooks": twice})
    setup.install(rig.ctx)
    assert rig.load()["hooks"]["SessionStart"] == [ours()["SessionStart"][0], THEIR_STOP]


@pytest.mark.parametrize("spelling", [
    r"c:\users\test user\appdata\local\claudeusagemonitor\CLAUDEUSAGEMONITOR.EXE",
    "C:/USERS/TEST USER/APPDATA/LOCAL/CLAUDEUSAGEMONITOR/ClaudeUsageMonitor.exe",
    '"C:/Users/Test User/AppData/Local/ClaudeUsageMonitor/ClaudeUsageMonitor.exe"',
])
def test_our_command_is_recognised_whatever_the_case_and_the_slashes(tmp_path: Path, spelling: str) -> None:
    rig = make_rig(tmp_path)
    rig.write({"hooks": ours(spelling)})
    assert set(setup.status(rig.ctx)["hooks"]["events"].values()) == {"installed"}
    setup.install(rig.ctx)
    for event, groups in rig.load()["hooks"].items():
        assert len(groups) == 1 and len(groups[0]["hooks"]) == 1, event      # replaced, never added beside
        assert groups[0]["hooks"][0]["command"] == CMD


def test_a_hook_of_another_program_is_never_taken_for_ours(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    other = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": CMD.replace("Monitor", "Other"),
                                                    "args": ["--hook", "--always"]}]}]}}
    rig.write(other)
    setup.install(rig.ctx)
    assert rig.load()["hooks"]["SessionStart"][0] == other["hooks"]["SessionStart"][0]
    assert len(rig.load()["hooks"]["SessionStart"]) == 2


# ── Idempotence ──────────────────────────────────────────────────────────────────────

def test_running_install_twice_changes_nothing(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.write(rich_settings())
    setup.install(rig.ctx)
    before = rig.settings.read_bytes()
    stamp = rig.settings.stat().st_mtime_ns
    files = rig.files()
    rig.claude.calls.clear()
    again = setup.install(rig.ctx)
    assert again["ok"] is True and again["changed"] == [] and again["backup"] is None
    assert again["unchanged"] == ["The three hooks were already in place.",
                                  "The claude-usage MCP tool was already registered."]
    assert rig.settings.read_bytes() == before and rig.settings.stat().st_mtime_ns == stamp   # not even rewritten
    assert rig.files() == files                                              # and no second backup
    assert rig.claude.kinds() == ["version", "get"]                          # the tool is not added again


def test_a_file_that_already_has_the_hooks_is_left_as_the_user_formatted_it(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    raw = rig.write(json.dumps({"hooks": ours()}, indent=4) + "\r\n")
    setup.install(rig.ctx)
    assert rig.settings.read_bytes() == raw and rig.files() == ["settings.json"]


# ── Remove ───────────────────────────────────────────────────────────────────────────

def test_remove_takes_out_what_install_put_in_and_leaves_everything_else(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.write(rich_settings())
    setup.install(rig.ctx)
    rig.claude.calls.clear()
    result = setup.remove(rig.ctx)
    assert result["ok"] is True and result["action"] == "remove"
    assert rig.load() == rich_settings()                                    # the file as the user had it, hooks of theirs and all
    assert result["changed"] == ["Removed the SessionStart hook.", "Removed the UserPromptSubmit hook.",
                                 "Removed the PostToolUse hook.", "Removed the claude-usage MCP tool."]
    assert rig.claude.tool is None and rig.claude.kinds() == ["get", "remove"]
    assert Path(result["backup"]).exists() and Path(result["backup"]).name.startswith("settings.json.cum-backup-")


def test_remove_drops_an_event_list_and_the_hooks_object_that_it_empties(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.write({"model": "opus", "hooks": ours()})
    setup.remove(rig.ctx)
    assert rig.load() == {"model": "opus"}
    rig.write({"hooks": {**ours(), "Stop": [THEIR_STOP]}})
    setup.remove(rig.ctx)
    assert rig.load() == {"hooks": {"Stop": [THEIR_STOP]}}


def test_remove_leaves_a_user_gate_entry_alone_even_in_one_of_our_events(tmp_path: Path) -> None:
    gate_in_ours = {"hooks": [{"type": "command", "command": CMD, "args": ["--gate"], "timeout": 45}]}
    rig = make_rig(tmp_path)
    rig.write({"hooks": {**ours(), "UserPromptSubmit": [gate_in_ours, *ours()["UserPromptSubmit"]],
                         "PreToolUse": [GATE_ENTRY]}})
    setup.install(rig.ctx)                                                   # install does not touch it either
    assert rig.load()["hooks"]["UserPromptSubmit"] == [gate_in_ours, ours()["UserPromptSubmit"][0]]
    setup.remove(rig.ctx)
    assert rig.load() == {"hooks": {"UserPromptSubmit": [gate_in_ours], "PreToolUse": [GATE_ENTRY]}}


def test_remove_takes_only_our_hook_out_of_a_group_it_shares_with_another(tmp_path: Path) -> None:
    mine = {"type": "command", "command": CMD, "args": ["--hook", "--always"], "timeout": 30}
    rig = make_rig(tmp_path)
    rig.write({"hooks": {"SessionStart": [{"matcher": "startup", "hooks": [THEIR_FORMATTER["hooks"][0], mine]}]}})
    setup.remove(rig.ctx)
    assert rig.load() == {"hooks": {"SessionStart": [{"matcher": "startup", "hooks": [THEIR_FORMATTER["hooks"][0]]}]}}


def test_a_failed_write_while_removing_leaves_the_old_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rig = make_rig(tmp_path)
    original = rig.write({"hooks": ours()})

    def refuse(src: Any, dst: Any) -> None:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(setup.os, "replace", refuse)
    result = setup.remove(rig.ctx)
    assert result["ok"] is False and result["errors"] == [
        "Could not write the Claude Code settings file (Access is denied), so the hooks were not removed."]
    assert rig.settings.read_bytes() == original and not [n for n in rig.files() if n.endswith(".tmp")]


def test_remove_with_nothing_of_ours_in_the_file_writes_and_backs_up_nothing(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    original = rig.write(rich_settings())
    result = setup.remove(rig.ctx)
    assert result["ok"] is True and result["changed"] == [] and result["backup"] is None
    assert result["unchanged"] == ["The settings file held no hooks of this app.", "No claude-usage MCP tool was registered."]
    assert rig.settings.read_bytes() == original and rig.files() == ["settings.json"]


def test_remove_works_without_a_settings_file(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    result = setup.remove(rig.ctx)
    assert result["ok"] is True and rig.files() == []


def test_remove_leaves_a_tool_that_starts_another_program(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, tool=(r"C:\Elsewhere\Other.exe", ["--mcp"]))
    rig.write({"hooks": ours()})
    result = setup.remove(rig.ctx)
    assert rig.claude.tool == (r"C:\Elsewhere\Other.exe", ["--mcp"]) and "remove" not in rig.claude.kinds()
    assert "A claude-usage MCP tool that starts another program was left alone." in result["unchanged"]


def test_remove_says_so_when_claude_will_not_remove_the_tool(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, tool=(EXE, ["--mcp"]))
    rig.claude.forced["remove"] = Ran(1, "", "boom\n")
    result = setup.remove(rig.ctx)
    assert result["ok"] is False and "exit code 1: boom" in result["errors"][0]
    assert "claude mcp remove claude-usage -s user" in result["errors"][0]


def test_remove_still_takes_the_hooks_out_when_claude_is_not_found(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, claude_path=None)
    rig.write({"hooks": ours()})
    result = setup.remove(rig.ctx)
    assert result["ok"] is True and rig.load() == {} and rig.claude.calls == []
    assert "claude mcp remove claude-usage -s user" in result["warnings"][0]


def test_remove_does_not_guess_when_it_cannot_tell_whether_the_tool_is_ours(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.claude.forced["get"] = Ran(None, "", "timed out")
    result = setup.remove(rig.ctx)
    assert "remove" not in rig.claude.kinds() and "Could not tell whether the claude-usage MCP tool" in result["warnings"][0]


# ── What it refuses ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("content", [
    '{"hooks": ', "{not json}", "", "   \n", "[]", '"text"', "null", "42", '{"a": NaN}', '{"a": 1}{"b": 2}',
    b"\xff\xfe\x00{", '{"hooks": []}', '{"hooks": "yes"}', '{"hooks": {"PostToolUse": {}}}',
    '{"hooks": {"SessionStart": "x"}}',
])
def test_a_settings_file_it_cannot_trust_is_refused_with_nothing_written(tmp_path: Path, content: Any) -> None:
    rig = make_rig(tmp_path)
    original = rig.write(content)
    for action in (setup.install, setup.remove):
        result = action(rig.ctx)
        assert result["ok"] is False and len(result["errors"]) == 1 and "left" in result["errors"][0]
        assert result["changed"] == [] and result["backup"] is None
        assert rig.settings.read_bytes() == original and rig.files() == ["settings.json"]    # no backup, no temp file
    assert "add" not in rig.claude.kinds() and "remove" not in rig.claude.kinds()


def test_a_settings_file_that_cannot_be_read_is_refused_and_the_status_says_so(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.settings.mkdir(parents=True)                       # a folder where the file should be: reading it fails
    for action in (setup.install, setup.remove):
        result = action(rig.ctx)
        assert result["ok"] is False and "could not be read" in result["errors"][0] and "left it alone" in result["errors"][0]
    assert "could not be read" in setup.status(rig.ctx)["errors"][0]
    assert rig.settings.is_dir() and "add" not in rig.claude.kinds()


def test_an_event_setup_does_not_write_has_no_entry() -> None:
    with pytest.raises(KeyError):
        setup.wanted_group("Stop", EXE)


def test_a_byte_order_mark_from_an_editor_is_not_a_problem(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.write(b"\xef\xbb\xbf" + json.dumps({"model": "opus"}).encode("utf-8"))
    assert setup.install(rig.ctx)["ok"] is True
    assert rig.load()["model"] == "opus" and not rig.settings.read_bytes().startswith(b"\xef\xbb\xbf")


@pytest.mark.parametrize(("version", "accepted"), [
    ("2.1.94 (Claude Code)", False), ("2.1.138 (Claude Code)", False), ("1.9.999", False), ("2.0.200", False),
    ("2.1.139 (Claude Code)", True), ("2.1.140", True), ("2.2.0 (Claude Code)", True), ("3.0.0", True),
])
def test_an_old_claude_code_is_refused_with_nothing_written(tmp_path: Path, version: str, accepted: bool) -> None:
    rig = make_rig(tmp_path, version=version)
    original = rig.write({"model": "opus"})
    result = setup.install(rig.ctx)
    assert result["ok"] is accepted
    if accepted:
        return
    assert "is older than 2.1.139" in result["errors"][0] and version.split()[0] in result["errors"][0]
    assert "claude update" in result["errors"][0]
    assert rig.settings.read_bytes() == original and rig.files() == ["settings.json"]
    assert rig.claude.kinds() == ["version"]                                 # nothing beyond the version was asked or done


@pytest.mark.parametrize("answer", [Ran(0, "no number here\n", ""), Ran(1, "", "bad\n"), Ran(None, "", "timed out")])
def test_a_version_it_cannot_read_is_refused_too(tmp_path: Path, answer: Ran) -> None:
    rig = make_rig(tmp_path)
    rig.claude.forced["version"] = answer
    result = setup.install(rig.ctx)
    assert result["ok"] is False and "Could not tell which version of Claude Code" in result["errors"][0]
    assert not rig.settings.exists() and rig.claude.kinds() == ["version"]


def test_both_problems_are_reported_together(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, version="2.0.1")
    rig.write("{")
    result = setup.install(rig.ctx)
    assert len(result["errors"]) == 2 and rig.files() == ["settings.json"]


# ── Where it works ───────────────────────────────────────────────────────────────────

def test_from_source_setup_is_unavailable_and_never_writes_a_dev_path_into_settings(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, frozen=False, executable=r"C:\Python312\python.exe")
    snapshot = setup.status(rig.ctx)
    assert snapshot["available"] is False and snapshot["reason"] == setup.NOT_INSTALLED_REASON
    assert snapshot["exe"] is None and snapshot["manual"] == "docs/AGENT_HOOK.md"
    for action in (setup.install, setup.remove):
        result = action(rig.ctx)
        assert result["ok"] is False and result["errors"] == [setup.NOT_INSTALLED_REASON]
    assert not rig.home.exists() and rig.claude.calls == []                  # nothing read, written or run


def test_without_a_claude_command_install_is_unavailable(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, claude_path=None)
    snapshot = setup.status(rig.ctx)
    assert snapshot["available"] is False and snapshot["reason"] == setup.NO_CLAUDE_REASON
    assert snapshot["exe"] == CMD and snapshot["claude"]["found"] is False
    result = setup.install(rig.ctx)
    assert result["ok"] is False and result["errors"] == [setup.NO_CLAUDE_REASON]
    assert not rig.settings.exists() and rig.claude.calls == []


def test_the_settings_file_follows_claude_config_dir(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, config_dir=True)
    assert rig.settings == tmp_path / "elsewhere" / "settings.json"
    setup.install(rig.ctx)
    assert rig.settings.is_file() and not (rig.home / ".claude").exists()    # never the default folder
    assert setup.status(rig.ctx)["settings_path"] == str(rig.settings)


def test_without_claude_config_dir_it_is_the_dot_claude_folder_of_the_user_profile(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    assert rig.settings == rig.home / ".claude" / "settings.json"
    setup.install(rig.ctx)
    assert rig.settings.is_file()
    assert setup.settings_path({"CLAUDE_CONFIG_DIR": "  "}, tmp_path) == tmp_path / ".claude" / "settings.json"
    assert setup.settings_path({}, tmp_path / "h") == tmp_path / "h" / ".claude" / "settings.json"
    assert setup.settings_path({"USERPROFILE": str(tmp_path / "u")}, tmp_path / "h") == tmp_path / "u" / ".claude" / "settings.json"


# ── cmd.exe and the npm shim ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("char", list('%^&|<>"'))
def test_each_character_cmd_reads_as_a_command_is_risky_in_a_path(char: str) -> None:
    assert setup.risky_for_cmd(rf"C:\Users\A{char}B\ClaudeUsageMonitor.exe") is True


def test_an_ordinary_path_is_not_risky_even_with_spaces_and_brackets() -> None:
    assert setup.risky_for_cmd(EXE) is False and setup.risky_for_cmd(r"C:\Program Files (x86)\X\a.exe") is False


@pytest.mark.parametrize(("path", "shim"), [(CLAUDE_CMD, True), (r"C:\x\claude.cmd", True), (r"C:\x\claude.bat", True),
                                            (CLAUDE_EXE, False), ("/usr/bin/claude", False)])
def test_a_batch_file_is_a_shim(path: str, shim: bool) -> None:
    assert setup.is_cmd_shim(path) is shim


def test_a_cmd_shim_with_a_risky_path_gets_the_hooks_but_not_the_tool(tmp_path: Path) -> None:
    risky = r"C:\Users\Ann&Bob\AppData\Local\ClaudeUsageMonitor\ClaudeUsageMonitor.exe"
    rig = make_rig(tmp_path, claude_path=CLAUDE_CMD, executable=risky)
    result = setup.install(rig.ctx)
    assert result["ok"] is True and result["errors"] == []
    assert rig.load()["hooks"]["SessionStart"][0]["hooks"][0]["command"] == risky.replace("\\", "/")
    assert "add" not in rig.claude.kinds() and rig.claude.tool is None        # no claude mcp add through cmd.exe
    assert result["manual"] == ("claude mcp add --transport stdio --scope user claude-usage '--' "
                                f"'{risky}' --mcp")
    assert any("cmd.exe" in warning for warning in result["warnings"])
    assert result["changed"] == ["Added the SessionStart hook.", "Added the UserPromptSubmit hook.",
                                 "Added the PostToolUse hook."]


def test_a_cmd_shim_with_an_ordinary_path_still_gets_the_tool(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, claude_path=CLAUDE_CMD)
    assert setup.install(rig.ctx)["changed"][-1] == "Registered the claude-usage MCP tool."
    assert rig.claude.calls[-1][0] == CLAUDE_CMD and rig.claude.calls[-1][-2:] == [EXE, "--mcp"]


def test_a_real_claude_exe_gets_the_tool_even_when_the_path_has_an_ampersand(tmp_path: Path) -> None:
    risky = r"C:\Users\Ann&Bob\AppData\Local\ClaudeUsageMonitor\ClaudeUsageMonitor.exe"
    rig = make_rig(tmp_path, claude_path=CLAUDE_EXE, executable=risky)     # no cmd.exe is involved: nothing to misread
    assert setup.install(rig.ctx)["manual"] is None and rig.claude.tool == (risky, ["--mcp"])


def test_the_manual_command_quotes_a_single_quote_for_powershell() -> None:
    assert setup.manual_mcp_command(r"C:\Users\O'Neil\x.exe").endswith(r"'C:\Users\O''Neil\x.exe' --mcp")


# ── The MCP tool ─────────────────────────────────────────────────────────────────────

def test_a_tool_already_registered_for_this_exe_is_left_alone(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, tool=(EXE.lower(), ["--mcp"]))                  # another spelling of the same path
    result = setup.install(rig.ctx)
    assert "add" not in rig.claude.kinds() and "remove" not in rig.claude.kinds()
    assert "The claude-usage MCP tool was already registered." in result["unchanged"]


def test_a_tool_that_starts_something_else_is_removed_and_registered_again(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, tool=(r"C:\Old\Elsewhere.exe", ["--mcp"]))
    result = setup.install(rig.ctx)
    assert rig.claude.kinds() == ["version", "get", "remove", "add"]
    assert rig.claude.calls[2] == [CLAUDE_EXE, "mcp", "remove", "claude-usage", "-s", "user"]
    assert rig.claude.tool == (EXE, ["--mcp"])
    assert result["changed"][-2:] == ["Removed a claude-usage MCP tool that started another program.",
                                      "Registered the claude-usage MCP tool."]


def test_a_tool_without_the_mcp_argument_is_not_ours(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, tool=(EXE, []))                                 # "added wrongly": empty Args
    setup.install(rig.ctx)
    assert rig.claude.kinds() == ["version", "get", "remove", "add"] and rig.claude.tool == (EXE, ["--mcp"])


def test_when_claude_will_not_register_the_tool_the_hooks_stay_and_the_command_is_offered(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.claude.forced["add"] = Ran(1, "", "something went wrong\n")
    result = setup.install(rig.ctx)
    assert result["ok"] is False and "exit code 1: something went wrong" in result["errors"][0]
    assert "The hooks are in place" in result["errors"][0] and result["manual"].startswith("claude mcp add ")
    assert len(result["changed"]) == 3 and rig.load() == {"hooks": ours()}


def test_when_claude_will_not_remove_a_foreign_tool_nothing_is_added(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, tool=(r"C:\Old\Elsewhere.exe", ["--mcp"]))
    rig.claude.forced["remove"] = Ran(1, "", "nope\n")
    result = setup.install(rig.ctx)
    assert result["ok"] is False and "add" not in rig.claude.kinds()
    assert "claude mcp remove claude-usage -s user" in result["errors"][0]


def test_a_get_that_gave_no_answer_still_tries_to_register_the_tool(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.claude.forced["get"] = Ran(None, "", "timed out")
    result = setup.install(rig.ctx)
    assert result["ok"] is True and rig.claude.tool == (EXE, ["--mcp"])


def test_a_claude_that_does_not_finish_is_reported_in_words(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.claude.forced["add"] = Ran(None, "", "timed out")
    assert "it did not finish in time" in setup.install(rig.ctx)["errors"][0]
    rig = make_rig(tmp_path / "again")
    rig.claude.forced["add"] = Ran(None, "", "FileNotFoundError")
    assert "it could not be started" in setup.install(rig.ctx)["errors"][0]


# ── Reading what claude says ─────────────────────────────────────────────────────────

@pytest.mark.parametrize(("text", "code", "expected"), [
    (GET_TEXT.format(command=EXE, args="--mcp"), 0, "registered"),
    (GET_TEXT.format(command=CMD.upper(), args="--mcp"), 0, "registered"),
    (GET_TEXT.format(command=f'"{EXE}"', args="--mcp"), 0, "registered"),
    ("\x1b[1mclaude-usage:\x1b[0m\n  Command: " + EXE + "\n  Args: --mcp\n", 0, "registered"),
    (GET_TEXT.format(command=EXE, args="--mcp --verbose"), 0, "registered"),
    (f"claude-usage:\n  Type: stdio\n  Command: {EXE} --mcp\n", 0, "registered"),     # one line, no Args
    (GET_TEXT.format(command=EXE, args=""), 0, "other"),
    (GET_TEXT.format(command=EXE, args="--status"), 0, "other"),
    (GET_TEXT.format(command=r"C:\Elsewhere\Other.exe", args="--mcp"), 0, "other"),
    (f"claude-usage:\n  Type: stdio\n  Command: {EXE}x\n  Args: --mcp\n", 0, "other"),    # a longer name is not our exe
    ("claude-usage:\n  Type: http\n  URL: https://example.com/mcp\n", 0, "other"),
    ('No MCP server found with name: "claude-usage".', 1, "missing"),
    ("no mcp server found with name claude-usage", 0, "missing"),
    ("something we have never seen", 0, "unknown"),
    ("", 0, "unknown"),
    (GET_TEXT.format(command=EXE, args="--mcp"), 1, "unknown"),
    (GET_TEXT.format(command=EXE, args="--mcp"), None, "unknown"),
])
def test_what_claude_mcp_get_says_about_the_tool(text: str, code: int | None, expected: str) -> None:
    assert setup.parse_mcp_get(text, code, EXE) == expected


@pytest.mark.parametrize(("text", "expected"), [
    ("2.1.139 (Claude Code)", (2, 1, 139)), ("2.1.94 (Claude Code)\n", (2, 1, 94)), ("claude 10.20.30-beta", (10, 20, 30)),
    ("2.1", None), ("", None), ("no digits", None),
])
def test_the_claude_version(text: str, expected: tuple[int, int, int] | None) -> None:
    assert setup.parse_version(text) == expected


# ── Status ───────────────────────────────────────────────────────────────────────────

def test_the_status_before_and_after_a_click(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    before = setup.status(rig.ctx)
    assert before == {
        "available": True, "reason": None, "manual": "docs/AGENT_HOOK.md", "exe": CMD, "settings_path": str(rig.settings),
        "errors": [],
        "hooks": {"state": "missing", "installed": 0, "total": 3,
                  "events": {"SessionStart": "missing", "UserPromptSubmit": "missing", "PostToolUse": "missing"}},
        "mcp": {"state": "missing"},
        "claude": {"found": True, "path": CLAUDE_EXE, "version": "2.1.160", "min_version": "2.1.139", "supported": True},
    }
    assert rig.files() == [] and rig.claude.kinds() == ["version", "get"]       # looking changes nothing and runs nothing else
    setup.install(rig.ctx)
    after = setup.status(rig.ctx)
    assert after["hooks"]["state"] == "installed" and after["hooks"]["installed"] == 3
    assert set(after["hooks"]["events"].values()) == {"installed"} and after["mcp"] == {"state": "registered"}


def test_the_status_tells_partly_installed_and_different_apart(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    some = ours()
    del some["PostToolUse"]
    some["UserPromptSubmit"][0]["hooks"][0]["args"] = ["--hook", "--threshold", "80"]
    rig.write({"hooks": some})
    hooks = setup.status(rig.ctx)["hooks"]
    assert hooks["state"] == "partial" and hooks["installed"] == 1
    assert hooks["events"] == {"SessionStart": "installed", "UserPromptSubmit": "different", "PostToolUse": "missing"}
    assert setup.hooks_summary({"a": "different", "b": "different", "c": "different"})["state"] == "partial"


def test_the_status_reports_a_claude_that_is_too_old_or_unreadable(tmp_path: Path) -> None:
    old = setup.status(make_rig(tmp_path, version="2.1.94 (Claude Code)").ctx)["claude"]
    assert old["version"] == "2.1.94" and old["supported"] is False
    rig = make_rig(tmp_path / "x")
    rig.claude.forced["version"] = Ran(0, "garbage", "")
    unknown = setup.status(rig.ctx)["claude"]
    assert unknown["found"] is True and unknown["version"] is None and unknown["supported"] is None


def test_the_status_says_when_the_settings_file_cannot_be_edited(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.write("{broken")
    snapshot = setup.status(rig.ctx)
    assert snapshot["hooks"]["state"] == "unknown" and "not valid JSON" in snapshot["errors"][0]
    assert snapshot["available"] is True                                      # the buttons stay; install explains the refusal


def test_the_status_is_the_same_shape_whether_or_not_setup_can_run() -> None:
    assert set(setup.unavailable_status("why")) == {"available", "reason", "manual", "exe", "settings_path", "errors",
                                                    "hooks", "mcp", "claude"}
    answer = setup.refused("install", "not here")
    assert answer["ok"] is False and answer["errors"] == ["not here"] and answer["action"] == "install"
    assert set(answer) == {"ok", "action", "changed", "unchanged", "backup", "warnings", "errors", "manual", "settings_path"}


# ── Writing safely ───────────────────────────────────────────────────────────────────

def test_a_failed_write_leaves_the_old_file_and_no_temp_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rig = make_rig(tmp_path)
    original = rig.write(rich_settings())

    def refuse(src: Any, dst: Any) -> None:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(setup.os, "replace", refuse)
    result = setup.install(rig.ctx)
    assert result["ok"] is False and result["errors"] == [
        "Could not write the Claude Code settings file (Access is denied), so the hooks were not added."]
    assert rig.settings.read_bytes() == original
    assert not [name for name in rig.files() if name.endswith(".tmp")] and "add" not in rig.claude.kinds()
    result = setup.remove(rig.ctx)                                              # nothing of ours is in it: no write to fail
    assert result["ok"] is True


def test_a_failed_backup_stops_before_the_file_is_touched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rig = make_rig(tmp_path)
    original = rig.write(rich_settings())

    def refuse(src: Any, dst: Any) -> None:
        raise OSError(28, "There is not enough space on the disk")

    monkeypatch.setattr(setup.shutil, "copyfile", refuse)
    result = setup.install(rig.ctx)
    assert result["ok"] is False and "There is not enough space on the disk" in result["errors"][0]
    assert rig.settings.read_bytes() == original and rig.files() == ["settings.json"]


def test_a_backup_name_that_is_taken_gets_a_number(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    target.write_text("{}", encoding="utf-8")
    first = setup.backup_settings(target, NOW)
    second = setup.backup_settings(target, NOW)
    third = setup.backup_settings(target, NOW)
    assert [path.name for path in (first, second, third)] == [BACKUP_NAME, BACKUP_NAME + "-2", BACKUP_NAME + "-3"]


def test_the_folder_of_the_settings_file_is_made_when_claude_never_made_it(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, config_dir=True)
    assert not rig.folder.exists()
    setup.install(rig.ctx)
    assert rig.settings.is_file()


def test_the_hooks_are_the_three_the_guard_needs() -> None:
    assert setup.HOOKS == (
        ("SessionStart", None, ("--hook", "--always"), 30),
        ("UserPromptSubmit", None, ("--hook",), 10),
        ("PostToolUse", "*", ("--hook",), 10),
    )
    assert all("--gate" not in args for _event, _matcher, args, _timeout in setup.HOOKS)    # --gate stays opt-in and manual


def test_the_pure_merges_do_not_modify_the_document_they_are_given() -> None:
    doc = rich_settings()
    snapshot = json.dumps(doc)
    merged, changed = setup.merge_install(doc, EXE)
    assert json.dumps(doc) == snapshot and changed == ["SessionStart", "UserPromptSubmit", "PostToolUse"]
    again, none = setup.merge_install(merged, EXE)
    assert none == [] and again == merged
    cleaned, removed = setup.merge_remove(merged, EXE)
    assert removed == changed and cleaned == doc


# ── The real runner ──────────────────────────────────────────────────────────────────

def test_the_real_runner_uses_an_argument_list_a_timeout_and_no_console_window(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_run(argv: Any, **options: Any) -> Any:
        seen.update(argv=argv, **options)
        return subprocess.CompletedProcess(argv, 0, stdout="2.1.160 (Claude Code)\n", stderr="")

    monkeypatch.setattr(setup.subprocess, "run", fake_run)
    assert setup.run_claude([CLAUDE_EXE, "--version"]) == Ran(0, "2.1.160 (Claude Code)\n", "")
    assert seen["argv"] == [CLAUDE_EXE, "--version"] and isinstance(seen["argv"], list)
    assert not seen.get("shell")                                                # never a shell
    assert seen["timeout"] == setup.CLAUDE_TIMEOUT_S and 0 < seen["timeout"] <= 20
    assert seen["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
    assert seen["capture_output"] is True and seen["stdin"] == subprocess.DEVNULL


@pytest.mark.parametrize(("error", "stderr"), [
    (subprocess.TimeoutExpired("claude", 20), "timed out"), (FileNotFoundError(2, "no such file"), "FileNotFoundError"),
    (PermissionError(13, "denied"), "PermissionError"), (ValueError("embedded null"), "ValueError"),
])
def test_the_real_runner_turns_a_failure_to_run_into_an_answer(monkeypatch: pytest.MonkeyPatch, error: Exception,
                                                                stderr: str) -> None:
    def fail(argv: Any, **options: Any) -> Any:
        raise error

    monkeypatch.setattr(setup.subprocess, "run", fail)
    assert setup.run_claude(["claude", "--version"]) == Ran(None, "", stderr)


def test_the_default_context_describes_the_real_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()                              # the autouse fixture hides default_context; look at the real one
    ctx = setup.default_context()
    assert ctx.frozen is bool(getattr(sys, "frozen", False)) and ctx.run is setup.run_claude
    assert ctx.executable and ctx.env is os.environ and ctx.home == Path.home()


# ── Privacy ──────────────────────────────────────────────────────────────────────────

def test_setup_never_opens_the_credentials_file_or_repeats_a_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    canary = "sk-ant-oat01-" + "Zq9" * 20
    rig = make_rig(tmp_path)
    rig.folder.mkdir(parents=True)
    (rig.folder / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": canary}}), encoding="utf-8")
    rig.write(rich_settings())
    rig.claude.forced["add"] = Ran(1, f"token {canary}\n", f"Authorization: Bearer {canary}\nfailed")
    opened: list[str] = []
    real_open = builtins.open

    def spy(file: Any, *args: Any, **kwargs: Any) -> Any:
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy)
    monkeypatch.setattr(io, "open", spy)             # pathlib reads and writes through io.open
    answers = [setup.status(rig.ctx), setup.install(rig.ctx), setup.remove(rig.ctx)]
    assert opened and not [name for name in opened if "credentials" in name.lower()]
    assert canary not in json.dumps(answers) and "Zq9Zq9" not in json.dumps(answers)
    assert ".credentials" not in Path(setup.__file__).read_text(encoding="utf-8")


@pytest.mark.parametrize(("raw", "shown"), [
    ("plain words", "plain words"), ("a\n\nb\t c", "a b c"), ("\x1b[31mred\x1b[0m", "red"),
    ("Bearer abc.def.ghi and more", "[hidden] and more"), ("key sk-ant-api03-AbCdEf123456 end", "key [hidden] end"),
    ("hash " + "a1" * 20 + " end", "hash [hidden] end"),
])
def test_what_claude_says_is_cleaned_before_it_is_shown(raw: str, shown: str) -> None:
    assert setup._clean(raw) == shown


def test_a_long_answer_is_cut(tmp_path: Path) -> None:
    assert len(setup._clean("word " * 200)) == 160
    rig = make_rig(tmp_path)
    rig.claude.forced["add"] = Ran(2, "", "bad thing\n" * 100)
    reason = setup.install(rig.ctx)["errors"][0]
    assert "exit code 2: bad thing bad thing" in reason and len(reason) < 400


# ── Two clicks at once ───────────────────────────────────────────────────────────────

def test_an_install_waits_for_another_one_that_is_still_writing(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    finished = threading.Event()

    def click() -> None:
        setup.install(rig.ctx)
        finished.set()

    with setup._LOCK:                                      # another install or remove holds the file
        threading.Thread(target=click, daemon=True).start()
        time.sleep(0.3)
        assert not finished.is_set() and rig.claude.calls == [] and not rig.settings.exists()
    assert finished.wait(10)
    assert rig.load() == {"hooks": ours()}
