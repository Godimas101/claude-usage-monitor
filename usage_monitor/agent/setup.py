"""One-click setup for the usage guard (#27): the Claude Code hooks and the MCP tool, written for the user.

Setup does what docs/AGENT_HOOK.md asks the user to do by hand. It adds three hooks to Claude Code's user settings
(SessionStart, UserPromptSubmit and PostToolUse, each starting this exe with ``--hook``) and registers the
``claude-usage`` MCP tool. It works only in the installed app: from source there is no exe to point the hooks at, and a
python.exe or a dev path never goes into a user's settings. ``--gate`` stays opt-in and manual: this module neither adds
nor removes it, and leaves a user's own ``--gate`` entry alone.

The settings file is edited the careful way. It is read first and refused when it is not a JSON object; an existing file
is copied to ``settings.json.cum-backup-<stamp>`` beside it before any change; only the handlers whose command is this
exe are touched (every other key and hook is kept); nothing is written when nothing changes, so running install twice
changes nothing; and the write goes through a temporary file and ``os.replace``. The MCP tool goes through the
``claude`` command, run with an argument list (never a shell), a timeout and no console window.

Pure functions (``hook_states``, ``merge_install``, ``merge_remove``, ``parse_mcp_get`` and the rest) do the thinking.
``Context`` holds everything that touches the machine (the exe, the environment, the home folder, ``which``, the runner
of ``claude`` and the clock), so a test hands in fakes and never reads a real ``~/.claude`` or runs a real ``claude``.

The monitor otherwise never runs ``claude``. This module does it only for the Settings page: ``claude --version`` and
``claude mcp get`` (both read-only) to show the status, and ``claude mcp add`` / ``remove`` when the user clicks Install
or Remove. Neither the credentials file nor any token is read, printed or logged, and output of ``claude`` reaches a
result only as a short, cleaned phrase.
"""
from __future__ import annotations

import contextlib
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

from usage_monitor.log import get_logger

log = get_logger(__name__)

MIN_CLAUDE = (2, 1, 139)           # the first Claude Code that reads "args" (the exec form) in a hook
MIN_CLAUDE_TEXT = "2.1.139"
MCP_NAME = "claude-usage"
MANUAL_DOC = "docs/AGENT_HOOK.md"  # the setup to do by hand, when this one is unavailable
SETTINGS_NAME = "settings.json"
BACKUP_INFIX = ".cum-backup-"      # settings.json.cum-backup-<YYYYMMDD-HHMMSS>
CLAUDE_COMMAND = "claude"
CLAUDE_TIMEOUT_S = 20.0
CONFIG_ENV = "CLAUDE_CONFIG_DIR"   # Claude Code's own override of its config folder
GATE_FLAG = "--gate"
VSCODE_WARNING = "The VS Code extension has its own Claude Code version; it also needs 2.1.139 or newer."
NOT_INSTALLED_REASON = "One-click setup works in the installed app. This copy runs from source."
NO_CLAUDE_REASON = ("Claude Code was not found on this PC: there is no claude command on the PATH. The copy inside "
                    "the VS Code extension is not on it; install Claude Code itself (see the README).")
KEEP_BACKUPS = 5                   # the newest backups of the settings file kept beside it; older ones go
_STAMP_UNKNOWN = ("unknown",)      # a settings file whose size and time cannot be read

# What setup writes: (event, matcher, args, timeout in seconds).
HOOKS = (
    ("SessionStart", None, ("--hook", "--always"), 30),
    ("UserPromptSubmit", None, ("--hook",), 10),
    ("PostToolUse", "*", ("--hook",), 10),
)
EVENTS = tuple(item[0] for item in HOOKS)

_CMD_UNSAFE = frozenset('%^&|<>"')  # what cmd.exe reads inside an argument, when claude is an npm .cmd shim
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_TOKENISH = re.compile(r"sk-[A-Za-z0-9_-]{8,}|\bBearer\s+\S+|\b[A-Za-z0-9_-]{32,}\b", re.IGNORECASE)
_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_LOCK = threading.Lock()           # one install or remove at a time: both rewrite the same file


class Ran(NamedTuple):
    """What a run of ``claude`` came to. ``returncode`` is None when it did not start or did not finish in time."""

    returncode: int | None
    stdout: str
    stderr: str


@dataclass(frozen=True)
class Context:
    """Everything that touches the machine, so a test hands in fakes."""

    frozen: bool                          # the installed build: only then is sys.executable this app's exe
    executable: str                       # sys.executable
    env: Mapping[str, str]
    home: Path
    which: Callable[[str], str | None]    # shutil.which
    run: Callable[[Sequence[str]], Ran]   # runs an argument list; run_claude
    now: Callable[[], datetime]           # names the backup


def default_context() -> Context:
    return Context(frozen=bool(getattr(sys, "frozen", False)), executable=sys.executable, env=os.environ,
                   home=Path.home(), which=find_command, run=run_claude, now=datetime.now)


def find_command(name: str) -> str | None:
    """shutil.which, and when that finds nothing, again on the PATH that Windows holds now. A process keeps the PATH
    it started with, so Claude Code installed while the monitor runs (the sign-in help sends people to do that) is
    not on the monitor's own."""
    found = shutil.which(name)
    if found or sys.platform != "win32":
        return found
    fresh = _registry_path()
    return shutil.which(name, path=fresh) if fresh else None


def _registry_path() -> str | None:
    """The machine PATH and then the user PATH, as new processes get them, with %VARIABLES% expanded."""
    import winreg

    parts = []
    for root, key in ((winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                      (winreg.HKEY_CURRENT_USER, "Environment")):
        try:
            with winreg.OpenKey(root, key) as handle:
                value, _kind = winreg.QueryValueEx(handle, "Path")
        except OSError:
            continue
        if isinstance(value, str) and value.strip():
            parts.append(os.path.expandvars(value))
    return os.pathsep.join(parts) or None


def run_claude(argv: Sequence[str]) -> Ran:
    """Run ``argv`` as an argument list (never through a shell) with a timeout, no console window and captured output.

    At the timeout the whole process tree goes: an npm ``claude.cmd`` runs node.exe under cmd.exe, and node holds the
    output pipes, so ending cmd.exe alone would leave this waiting for node."""
    try:
        process = subprocess.Popen(
            list(argv), stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True,
            encoding="utf-8", errors="replace", shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, ValueError) as exc:  # not found, not allowed, a bad argument
        return Ran(None, "", type(exc).__name__)
    try:
        stdout, stderr = process.communicate(timeout=CLAUDE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        _end_tree(process)
        with contextlib.suppress(subprocess.TimeoutExpired, OSError, ValueError):
            process.communicate(timeout=5)
        return Ran(None, "", "timed out")
    return Ran(process.returncode, stdout or "", stderr or "")


def _end_tree(process: subprocess.Popen) -> None:
    if sys.platform == "win32":
        taskkill = Path(os.environ.get("SystemRoot") or r"C:\Windows") / "System32" / "taskkill.exe"
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run([str(taskkill), "/T", "/F", "/PID", str(process.pid)], capture_output=True, timeout=10,
                           check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    with contextlib.suppress(OSError):
        process.kill()


# ── Pure helpers: paths, versions and the shape of what `claude` says ───────────────────

def settings_path(env: Mapping[str, str], home: Path) -> Path:
    """Claude Code's user settings file: $CLAUDE_CONFIG_DIR/settings.json when that is set, else
    %USERPROFILE%/.claude/settings.json (the folder rule of paths.claude_dir(), with the environment passed in)."""
    override = (env.get(CONFIG_ENV) or "").strip()
    if override:
        return Path(override).expanduser() / SETTINGS_NAME
    return Path(env.get("USERPROFILE") or home) / ".claude" / SETTINGS_NAME


def command_of(exe: str) -> str:
    """The exe as a hook's ``command``: the full path with forward slashes, which JSON needs and Windows accepts."""
    return exe.replace("\\", "/")


def _key(path: Any) -> str:
    """A path for comparing: case folded, slashes normalised, surrounding quotes and spaces dropped."""
    return str(path).strip().strip('"').replace("\\", "/").lower() if isinstance(path, str) else ""


def parse_version(text: str) -> tuple[int, int, int] | None:
    """The (major, minor, patch) in ``claude --version`` ("2.1.139 (Claude Code)"), or None."""
    found = _VERSION.search(text or "")
    return (int(found.group(1)), int(found.group(2)), int(found.group(3))) if found else None


def parse_mcp_get(text: str, returncode: int | None, exe: str) -> str:
    """What ``claude mcp get claude-usage`` says about the tool, for this exe: ``registered`` (starts this exe with
    --mcp), ``missing``, ``other`` (a claude-usage tool that starts something else) or ``unknown``."""
    text = _ANSI.sub("", text or "")
    if re.search(r"no mcp server found", text, re.IGNORECASE):
        return "missing"
    if returncode != 0:
        return "unknown"
    command = re.search(r"^[ \t]*Command:[ \t]*(.+?)[ \t]*$", text, re.MULTILINE)
    if command is None:
        # a tool that is not a program (a URL) is another tool; an answer in a shape we do not know is not an answer
        return "other" if re.search(r"^[ \t]*URL:", text, re.MULTILINE) else "unknown"
    args_line = re.search(r"^[ \t]*Args:[ \t]*(.*?)[ \t]*$", text, re.MULTILINE)
    target = _key(exe)
    if args_line is not None:
        same, args = _key(command.group(1)) == target, args_line.group(1).split()
    else:  # one line holding the program and its arguments
        line = _key(command.group(1))
        same, args = line == target or line.startswith(target + " "), line[len(target):].split()
    return "registered" if same and "--mcp" in args else "other"


def is_cmd_shim(claude: str) -> bool:
    """Whether the claude found is a batch file, which Windows starts through cmd.exe (an npm install does this)."""
    return Path(claude).suffix.lower() in (".cmd", ".bat")


def risky_for_cmd(path: str) -> bool:
    """Whether ``path`` holds a character that cmd.exe would read as a command rather than as part of an argument."""
    return any(char in _CMD_UNSAFE for char in path)


def manual_mcp_command(exe: str) -> str:
    """The PowerShell line of docs/AGENT_HOOK.md for this exe. The bare -- stays quoted: PowerShell swallows it when
    claude is an npm script."""
    quoted = "'" + exe.replace("'", "''") + "'"
    return f"claude mcp add --transport stdio --scope user {MCP_NAME} '--' {quoted} --mcp"


def _clean(text: str, limit: int = 160) -> str:
    """A short phrase from the output of a program, safe to show: one line, no escape codes, nothing token-like."""
    return _TOKENISH.sub("[hidden]", " ".join(_ANSI.sub("", text or "").split()))[:limit]


def _os_reason(exc: OSError) -> str:
    """What the operating system said, in its own words ("Access is denied"), else the kind of error."""
    return _clean(exc.strerror or "") or type(exc).__name__


def _ran_reason(ran: Ran) -> str:
    if ran.returncode is None:
        return "it did not finish in time" if ran.stderr == "timed out" else "it could not be started"
    detail = _clean(ran.stderr or ran.stdout)
    return f"exit code {ran.returncode}" + (f": {detail}" if detail else "")


# ── Pure helpers: the hooks in a settings document ──────────────────────────────────────

def _groups(doc: Mapping[str, Any], event: str) -> list:
    hooks = doc.get("hooks")
    groups = hooks.get(event) if isinstance(hooks, dict) else None
    return groups if isinstance(groups, list) else []


def _handlers(doc: Mapping[str, Any], event: str) -> Iterator[Any]:
    for group in _groups(doc, event):
        inner = group.get("hooks") if isinstance(group, dict) else None
        if isinstance(inner, list):
            yield from inner


def _is_ours(handler: Any, exe_key: str) -> bool:
    """A command hook that starts this app's exe with --hook. It is told by the exe's file name, not its folder: an app
    reinstalled elsewhere leaves hooks with the old path, which Install must replace and Remove must take out. A
    --gate entry is the user's own, and stays."""
    if not isinstance(handler, dict) or handler.get("type") != "command":
        return False
    program, args = _program_and_args(handler)
    return bool(program) and _name(program) == _name(exe_key) and "--hook" in args and GATE_FLAG not in args


def _program_and_args(handler: Mapping[str, Any]) -> tuple[str, list[str]]:
    """The program a command hook starts (as a _key) and its arguments: from the exec form (``command`` and
    ``args``), or from one command line ("C:/x/app.exe" --hook) written by hand."""
    command, args = handler.get("command"), handler.get("args")
    if not isinstance(command, str):
        return "", []
    if isinstance(args, list):
        return _key(command), [str(arg) for arg in args]
    found = re.match(r'\s*"([^"]+)"\s*(.*)$', command) or re.match(r"\s*(\S+)\s*(.*)$", command)
    return (_key(found.group(1)), found.group(2).split()) if found else ("", [])


def _name(key: str) -> str:
    return key.rsplit("/", 1)[-1]


def wanted_group(event: str, exe: str) -> dict:
    """The entry setup writes for ``event``: a matcher group holding one command hook in the exec form."""
    for name, matcher, args, timeout in HOOKS:
        if name == event:
            group: dict[str, Any] = {"matcher": matcher} if matcher else {}
            group["hooks"] = [{"type": "command", "command": command_of(exe), "args": list(args), "timeout": timeout}]
            return group
    raise KeyError(event)


def hook_states(doc: Mapping[str, Any], exe: str) -> dict[str, str]:
    """Per event of setup: ``installed`` (a hook with this exe and exactly the args setup writes), ``different``
    (this app's hook with other args, or with the path of an exe that moved) or ``missing``."""
    key = _key(exe)
    states = {}
    for event, _matcher, args, _timeout in HOOKS:
        mine = [handler for handler in _handlers(doc, event) if _is_ours(handler, key)]
        if any(handler.get("args") == list(args) and _key(handler.get("command")) == key for handler in mine):
            states[event] = "installed"
        else:
            states[event] = "different" if mine else "missing"
    return states


def hooks_summary(states: Mapping[str, str]) -> dict[str, Any]:
    installed = sum(1 for state in states.values() if state == "installed")
    if installed == len(states):
        state = "installed"
    else:
        state = "missing" if all(value == "missing" for value in states.values()) else "partial"
    return {"state": state, "installed": installed, "total": len(states)}


def hooks_problem(doc: Mapping[str, Any]) -> str | None:
    """Why setup must not edit the hooks of ``doc`` (a shape it would have to guess at), or None."""
    hooks = doc.get("hooks")
    if hooks is None:
        return None
    if not isinstance(hooks, dict):
        return 'The "hooks" setting in the Claude Code settings file is not an object, so setup left it alone.'
    for event in EVENTS:
        if event in hooks and not isinstance(hooks[event], list):
            return f'The "{event}" hooks in the Claude Code settings file are not a list, so setup left them alone.'
    return None


def _merge_event(groups: list, wanted: dict, exe_key: str) -> list:
    """``groups`` with this exe's hooks replaced by ``wanted``. A group that held only this exe's hook becomes
    ``wanted`` where it stands; a group shared with someone else's hook keeps that hook; any further copy goes."""
    merged: list = []
    placed = False
    for group in groups:
        inner = group.get("hooks") if isinstance(group, dict) else None
        if not isinstance(inner, list) or not any(_is_ours(handler, exe_key) for handler in inner):
            merged.append(group)
            continue
        rest = [handler for handler in inner if not _is_ours(handler, exe_key)]
        if rest:
            merged.append({**group, "hooks": rest})
        elif not placed:
            merged.append(wanted)
            placed = True
    if not placed:
        merged.append(wanted)
    return merged


def merge_install(doc: Mapping[str, Any], exe: str) -> tuple[dict, list[str]]:
    """``doc`` with the three hooks of this exe in place, and the events that changed. ``doc`` is not modified.
    Every other key and every other hook is kept as it is. The caller has checked hooks_problem(): the shapes it
    names are the ones this would have to guess at."""
    key = _key(exe)
    new = copy.deepcopy(dict(doc))
    hooks = new.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    changed = []
    for event in EVENTS:
        groups = hooks.get(event, [])
        merged = _merge_event(groups, wanted_group(event, exe), key)
        if merged != groups:
            hooks[event] = merged
            changed.append(event)
    if changed:
        new["hooks"] = hooks
    return new, changed


def merge_remove(doc: Mapping[str, Any], exe: str) -> tuple[dict, list[str]]:
    """``doc`` without the hooks of this exe in the three events of setup, and the events that lost one. An event
    list, or the ``hooks`` object, that this leaves empty goes too. A ``--gate`` entry and everything else stays."""
    key = _key(exe)
    new = copy.deepcopy(dict(doc))
    hooks = new.get("hooks")
    if not isinstance(hooks, dict):
        return new, []
    removed = []
    for event in EVENTS:
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        kept: list = []
        touched = False
        for group in groups:
            inner = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(inner, list) or not any(_is_ours(handler, key) for handler in inner):
                kept.append(group)
                continue
            touched = True
            rest = [handler for handler in inner if not _is_ours(handler, key)]
            if rest:
                kept.append({**group, "hooks": rest})
        if touched:
            removed.append(event)
            if kept:
                hooks[event] = kept
            else:
                del hooks[event]
    if removed and not hooks:
        del new["hooks"]
    return new, removed


# ── The settings file ───────────────────────────────────────────────────────────────────

def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not allowed in JSON")


def read_settings(path: Path) -> tuple[dict | None, str | None]:
    """(document, problem). The settings as a dict ({} when there is no file, or an empty one, which Claude Code also
    reads as no settings), or None and why setup must leave the file alone: it cannot be read, is not valid JSON or is
    not an object."""
    try:
        text = path.read_text(encoding="utf-8-sig")   # a byte order mark from an editor is not part of the JSON
    except FileNotFoundError:
        return {}, None
    except UnicodeDecodeError:
        return None, "The Claude Code settings file is not UTF-8 text, so setup left it alone."
    except OSError as exc:
        return None, f"The Claude Code settings file could not be read ({_os_reason(exc)}), so setup left it alone."
    if not text.strip():
        return {}, None
    try:
        doc = json.loads(text, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        return None, ("The Claude Code settings file is not valid JSON, so setup left it alone. "
                      "Fix it or move it aside, then try again.")
    if not isinstance(doc, dict):
        return None, "The Claude Code settings file does not hold a JSON object, so setup left it alone."
    return doc, None


def backup_settings(path: Path, now: datetime) -> Path:
    """Copy the settings file to settings.json.cum-backup-<YYYYMMDD-HHMMSS> beside it (numbered when that name is
    taken), and drop all but the newest KEEP_BACKUPS of those copies. Returns the copy."""
    stamp = now.strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.name}{BACKUP_INFIX}{stamp}")
    number = 1
    while target.exists():
        number += 1
        target = path.with_name(f"{path.name}{BACKUP_INFIX}{stamp}-{number}")
    shutil.copyfile(path, target)
    _prune_backups(path, keep=target)
    return target


def _prune_backups(path: Path, *, keep: Path) -> None:
    def age(copy: Path) -> float:
        try:
            return copy.stat().st_mtime
        except OSError:
            return 0.0

    copies = sorted((item for item in path.parent.glob(f"{path.name}{BACKUP_INFIX}*") if item != keep),
                    key=age, reverse=True)
    for old in copies[KEEP_BACKUPS - 1:]:
        with contextlib.suppress(OSError):
            old.unlink()


def settings_text(doc: Mapping[str, Any]) -> str:
    """``doc`` as setup writes it: JSON with a 2-space indent and a trailing newline. Raises ValueError for what cannot
    be written back as strict JSON in UTF-8, so setup refuses before it touches the file: a number too large for JSON
    (1e999 reads as infinity) or half of a broken character (a lone surrogate)."""
    text = json.dumps(doc, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    text.encode("utf-8")
    return text


def _stamp(path: Path) -> tuple:
    """The settings file's change time and size, to tell whether something wrote it while setup was working."""
    try:
        info = path.stat()
    except FileNotFoundError:
        return ()
    except OSError:
        return _STAMP_UNKNOWN
    return (info.st_mtime_ns, info.st_size)


def write_settings(path: Path, doc: Mapping[str, Any]) -> None:
    """Write ``doc`` as settings_text() lays it out, through a temp file in the same folder and os.replace, so a
    failure leaves the old file as it was. A settings file that is a link (a dotfiles folder) stays one: the file it
    points at is the one replaced."""
    if path.is_symlink():
        path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = settings_text(doc)
    handle, temp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp)
        raise


# ── Talking to claude ───────────────────────────────────────────────────────────────────

def claude_version(ctx: Context, claude: str) -> tuple[int, int, int] | None:
    ran = ctx.run([claude, "--version"])
    return parse_version(ran.stdout) if ran.returncode == 0 else None


def _mcp_state(ctx: Context, claude: str, exe: str) -> str:
    return _mcp_get(ctx, claude, exe)[0]


def _mcp_get(ctx: Context, claude: str, exe: str) -> tuple[str, str]:
    """(state, what the tool starts): parse_mcp_get's state, and for a tool that starts a program, that program and its
    arguments as a short phrase ("" when it says none)."""
    ran = ctx.run([claude, "mcp", "get", MCP_NAME])
    text = _ANSI.sub("", f"{ran.stdout}\n{ran.stderr}")
    command = re.search(r"^[ \t]*Command:[ \t]*(.+?)[ \t]*$", text, re.MULTILINE)
    args = re.search(r"^[ \t]*Args:[ \t]*(.*?)[ \t]*$", text, re.MULTILINE)
    starts = _clean(" ".join(part.group(1) for part in (command, args) if part)) if command else ""
    return parse_mcp_get(text, ran.returncode, exe), starts


def _availability(ctx: Context) -> tuple[str | None, str | None, str | None]:
    """(exe, claude, reason): this app's exe (None from source), the claude command (None when not found) and why
    setup is off (None when it is on)."""
    if not ctx.frozen:
        return None, None, NOT_INSTALLED_REASON
    claude = ctx.which(CLAUDE_COMMAND)
    return ctx.executable, claude, None if claude else NO_CLAUDE_REASON


def _version_text(version: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in version)


# ── The public three: status, install, remove ───────────────────────────────────────────

def unavailable_status(reason: str) -> dict:
    """The status of a setup that cannot run here, in the shape status() answers."""
    return {"available": False, "can_remove": False, "reason": reason, "manual": MANUAL_DOC, "exe": None,
            "settings_path": None, "errors": [],
            "hooks": {"state": "unknown", "installed": 0, "total": len(HOOKS), "events": {}},
            "mcp": {"state": "unknown"},
            "claude": {"found": False, "path": None, "version": None, "min_version": MIN_CLAUDE_TEXT, "supported": None}}


def refused(action: str, reason: str) -> dict:
    """The result of an install or a remove that did not run, in the shape those answer."""
    return _finish({**_new_result(action), "errors": [reason]})


def status(ctx: Context | None = None) -> dict:
    """Where setup stands. Reads the settings file and runs ``claude --version`` and ``claude mcp get`` (both read-only).

        available     setup can run here; ``reason`` says why not when it cannot, ``manual`` where to do it by hand
        can_remove    Remove can run: it needs the installed app, not Claude Code (a file it cannot edit is a refusal)
        hooks         ``state`` installed | partial | missing | unknown, ``installed`` of ``total``, and ``events``:
                      the state of each of the three, installed | different | missing
        mcp           ``state`` registered | missing | other | unknown
        claude        found, path, version ("2.1.160" or None), min_version and ``supported`` (None when unknown)
        errors        why the settings file cannot be edited, when it cannot
    """
    ctx = ctx or default_context()
    exe, claude, reason = _availability(ctx)
    out = unavailable_status(reason or "")
    out.update(available=reason is None, reason=reason, exe=command_of(exe) if exe else None)
    if exe is None:
        return out
    out["can_remove"] = True
    path = settings_path(ctx.env, ctx.home)
    out["settings_path"] = str(path)
    doc, problem = read_settings(path)
    problem = problem or (hooks_problem(doc) if doc is not None else None)
    if problem:
        out["errors"].append(problem)
    elif doc is not None:
        states = hook_states(doc, exe)
        out["hooks"] = {**hooks_summary(states), "events": states}
    if claude is not None:
        version = claude_version(ctx, claude)
        out["claude"] = {"found": True, "path": claude, "version": _version_text(version) if version else None,
                         "min_version": MIN_CLAUDE_TEXT, "supported": None if version is None else version >= MIN_CLAUDE}
        out["mcp"] = {"state": _mcp_state(ctx, claude, exe)}
    return out


def _new_result(action: str) -> dict:
    return {"ok": True, "action": action, "changed": [], "unchanged": [], "backup": None, "warnings": [],
            "errors": [], "manual": None, "settings_path": None}


def _finish(result: dict) -> dict:
    result["ok"] = not result["errors"]
    return result


def install(ctx: Context | None = None) -> dict:
    """Put the three hooks into Claude Code's settings and register the MCP tool.

    Returns ``{ok, action, changed, unchanged, backup, warnings, errors, manual, settings_path}``: what was done and what
    was already in place, the backup file made (None when nothing was written), notes, what went wrong in plain words,
    and the command to run by hand when the MCP tool could not be registered. It refuses, writing nothing, when setup is
    unavailable, the settings file is not a JSON object or Claude Code is older than 2.1.139. A failure of the MCP step
    leaves the hooks written and says so in ``errors``.
    """
    ctx = ctx or default_context()
    with _LOCK:
        return _install(ctx)


def _install(ctx: Context) -> dict:
    result = _new_result("install")
    exe, claude, reason = _availability(ctx)
    if reason or exe is None or claude is None:
        result["errors"].append(reason or NOT_INSTALLED_REASON)
        return _finish(result)
    path = settings_path(ctx.env, ctx.home)
    result["settings_path"] = str(path)
    version = claude_version(ctx, claude)
    if version is None:
        result["errors"].append("Could not tell which version of Claude Code is installed, so setup changed nothing. "
                                f"Hooks need {MIN_CLAUDE_TEXT} or newer; check with: claude --version")
    elif version < MIN_CLAUDE:
        result["errors"].append(f"Claude Code {_version_text(version)} (the claude at {claude}) is older than "
                                f"{MIN_CLAUDE_TEXT}, which hooks need, so setup changed nothing. Update it with: claude update")
    stamp = _stamp(path)
    doc, problem = read_settings(path)
    problem = problem or (hooks_problem(doc) if doc is not None else None)
    if problem:
        result["errors"].append(problem)
    if doc is None or result["errors"]:
        return _finish(result)

    states = hook_states(doc, exe)
    merged, changed = merge_install(doc, exe)
    if changed:
        problem = _write_problem(path, stamp, merged)
        if problem:
            result["errors"].append(problem + " The hooks were not added.")
            return _finish(result)
        try:
            if path.exists():
                result["backup"] = str(backup_settings(path, ctx.now()))
            write_settings(path, merged)
        except OSError as exc:
            result["errors"].append(f"Could not write the Claude Code settings file ({_os_reason(exc)}), "
                                    "so the hooks were not added.")
            return _finish(result)
        result["changed"] += [f"{'Added' if states[event] == 'missing' else 'Updated'} the {event} hook."
                              for event in changed]
        log.info("setup: hooks written (%s)", ", ".join(changed))
    else:
        result["unchanged"].append("The three hooks were already in place.")
    _install_mcp(ctx, claude, exe, result)
    result["warnings"].append(VSCODE_WARNING)
    return _finish(result)


def _write_problem(path: Path, stamp: tuple, doc: Mapping[str, Any]) -> str | None:
    """Why the new settings must not be written, or None: something else wrote the file since setup read it (Claude
    Code saves it too), or the document cannot be written back as strict JSON."""
    if _stamp(path) != stamp or stamp == _STAMP_UNKNOWN:
        return "The Claude Code settings file changed while setup was working, so setup left it alone. Try again."
    try:
        settings_text(doc)
    except ValueError:
        return ("The Claude Code settings file holds a value that cannot be written back as it is (a number too large "
                "for JSON, or a broken character), so setup left it alone.")
    return None


def _install_mcp(ctx: Context, claude: str, exe: str, result: dict) -> None:
    state, starts = _mcp_get(ctx, claude, exe)
    if state == "registered":
        result["unchanged"].append(f"The {MCP_NAME} MCP tool was already registered.")
        return
    if is_cmd_shim(claude) and risky_for_cmd(exe):
        result["warnings"].append("The MCP tool was not registered: claude here is an npm script that Windows runs "
                                  "through cmd.exe, and this app's path has characters cmd.exe would misread. "
                                  "Register it by hand with the command below.")
        result["manual"] = manual_mcp_command(exe)
        return
    if state == "other":
        ran = ctx.run([claude, "mcp", "remove", MCP_NAME, "-s", "user"])
        if ran.returncode != 0:
            result["errors"].append(f"A {MCP_NAME} MCP tool that starts another program is registered and could not be "
                                    f"removed ({_ran_reason(ran)}). Remove it with: claude mcp remove {MCP_NAME} -s user")
            result["manual"] = manual_mcp_command(exe)
            return
        result["changed"].append(f"Removed a {MCP_NAME} MCP tool that started another program"
                                 + (f" ({starts})." if starts else "."))
    ran = ctx.run([claude, "mcp", "add", "--transport", "stdio", "--scope", "user", MCP_NAME, "--", exe, "--mcp"])
    if ran.returncode != 0 and state == "unknown" and re.search(r"already exists", f"{ran.stdout}\n{ran.stderr}",
                                                                 re.IGNORECASE):
        result["warnings"].append(f"A {MCP_NAME} MCP tool is already registered, but Claude Code did not say what it "
                                  f"starts, so it was left as it is. If it is not this app, remove it with: claude mcp "
                                  f"remove {MCP_NAME} -s user, then press Install again.")
        return
    if ran.returncode != 0:
        result["errors"].append(f"Claude Code did not register the {MCP_NAME} MCP tool ({_ran_reason(ran)}). "
                                "The hooks are in place; register the tool by hand with the command below.")
        result["manual"] = manual_mcp_command(exe)
        return
    result["changed"].append(f"Registered the {MCP_NAME} MCP tool.")
    log.info("setup: MCP tool registered")


def remove(ctx: Context | None = None) -> dict:
    """Take this app's hooks out of Claude Code's settings and unregister the MCP tool when it starts this exe.

    Same result shape as install(). Only the handlers whose command is this exe in the three events of setup go (a
    ``--gate`` entry stays), with the event lists or the ``hooks`` object that this leaves empty; the file is backed up
    first and written atomically. A tool registered for another program is left alone.
    """
    ctx = ctx or default_context()
    with _LOCK:
        return _remove(ctx)


def _remove(ctx: Context) -> dict:
    result = _new_result("remove")
    exe, claude, _reason = _availability(ctx)
    if exe is None:
        result["errors"].append(NOT_INSTALLED_REASON)
        return _finish(result)
    path = settings_path(ctx.env, ctx.home)
    result["settings_path"] = str(path)
    stamp = _stamp(path)
    doc, problem = read_settings(path)
    problem = problem or (hooks_problem(doc) if doc is not None else None)
    if problem or doc is None:
        result["errors"].append(problem or NOT_INSTALLED_REASON)
        return _finish(result)

    merged, removed = merge_remove(doc, exe)
    if removed:
        problem = _write_problem(path, stamp, merged)
        if problem:
            result["errors"].append(problem + " The hooks were not removed.")
            return _finish(result)
        try:
            result["backup"] = str(backup_settings(path, ctx.now()))
            write_settings(path, merged)
        except OSError as exc:
            result["errors"].append(f"Could not write the Claude Code settings file ({_os_reason(exc)}), "
                                    "so the hooks were not removed.")
            return _finish(result)
        result["changed"] += [f"Removed the {event} hook." for event in removed]
        log.info("setup: hooks removed (%s)", ", ".join(removed))
    else:
        result["unchanged"].append("The settings file held no hooks of this app.")
    _remove_mcp(ctx, claude, exe, result)
    return _finish(result)


def _remove_mcp(ctx: Context, claude: str | None, exe: str, result: dict) -> None:
    by_hand = f"Remove it by hand with: claude mcp remove {MCP_NAME} -s user"
    if claude is None:
        result["warnings"].append(f"Claude Code was not found, so the {MCP_NAME} MCP tool was left alone. {by_hand}")
        return
    state = _mcp_state(ctx, claude, exe)
    if state == "registered":
        ran = ctx.run([claude, "mcp", "remove", MCP_NAME, "-s", "user"])
        if ran.returncode == 0:
            result["changed"].append(f"Removed the {MCP_NAME} MCP tool.")
            log.info("setup: MCP tool removed")
        else:
            result["errors"].append(f"Claude Code did not remove the {MCP_NAME} MCP tool ({_ran_reason(ran)}). {by_hand}")
    elif state == "other":
        result["unchanged"].append(f"A {MCP_NAME} MCP tool that starts another program was left alone.")
    elif state == "missing":
        result["unchanged"].append(f"No {MCP_NAME} MCP tool was registered.")
    else:
        result["warnings"].append(f"Could not tell whether the {MCP_NAME} MCP tool is registered, so it was left alone. "
                                  f"{by_hand}")
