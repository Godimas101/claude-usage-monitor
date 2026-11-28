"""The texts installer.iss shows, for tests/test_installer_words.py (#103).

tests/fixtures/installer_words_english.json holds what Setup and the uninstaller showed in English before #103 moved
the script's own texts out of it: recorded from the untouched script at c05f45d (step a1 of #103's plan) and never
recorded again. The test renders every site of today's script and compares, byte for byte.

A site is a place that shows text: a box, a status line, a page's heading or line, the Finish page's sentences, a
[Tasks] or [Run] description, the Start-menu names, the group, the publisher, the product name and the name in Apps.
Each is found by where it is (the routine, the call, which of them), never by its words, so one table reads the script
before and after #103. A site's Pascal string expression is rendered as Setup builds it:
- literals, with '' as one quote, #13#10 and +;
- CustomMessage('X') and {cm:X}: X in the English messages, the custom messages of the first [Languages] entry's
  files read in order, a later file's over an earlier one's ("compiler:Default.isl" is INNO_DEFAULT_CUSTOM, Inno Setup
  6.7.1's), %n as CR LF (ISCC does that when it compiles, Compiler.SetupCompiler.pas EnumCustomMessagesProc);
- FmtMessage(text, [a, b]): %1 to %9 filled, %% as %, the values not read again (SetupLdrAndSetup.Messages.pas);
- Format('..%d..', [X]) and IntToStr(X): the value as {X};
- any other name: {Name}, a value the code fills in. A box whose whole text is a variable (MonitorClosed's Text before
  #103) is read from that variable's one assignment, with the routine's parameters as the site gives them.

Also here (#103): the [Languages] entries of each build, every language's messages and their shape against English's,
Setup's choice of language (inno_language), and a run of EndedNote (run).

    python tests/installer_words.py --print     the texts of today's script, as JSON
    python tests/installer_words.py --record    write the record; refused once it exists (English must not change)
    python tests/installer_words.py --todo      what each language's file lacks of en.isl (English shows there)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
ISS = REPO / "installer.iss"
RECORD = HERE / "fixtures" / "installer_words_english.json"
CR_LF = chr(13) + chr(10)
ABOUT = ("Today's English of Setup and the uninstaller, recorded before #103 moved the script's texts into "
         "installer-messages (step a1 of its plan, at c05f45d, by tests/installer_words.py): every box, status line, "
         "page heading and line, Finish page sentence, task and run box of installer.iss, with {Name} for a value the "
         "code fills in, and the names that stay: the product, the Apps list's name, the publisher, the Start-menu "
         "folder and its two shortcuts. tests/test_installer_words.py holds the script to these bytes. Never "
         "re-recorded: English must not change.")

# Inno Setup 6.7.1's Default.isl [CustomMessages], as written there (%n not yet expanded). The record's task heading is
# AutoStartProgramGroupDescription and its Apps-list name is built from NameAndVersion. A test compares this with the
# Default.isl of the Inno Setup on the PC, where there is one.
INNO_DEFAULT_CUSTOM: dict[str, str] = {
    "NameAndVersion": "%1 version %2",
    "AdditionalIcons": "Additional shortcuts:",
    "CreateDesktopIcon": "Create a &desktop shortcut",
    "CreateQuickLaunchIcon": "Create a &Quick Launch shortcut",
    "ProgramOnTheWeb": "%1 on the Web",
    "UninstallProgram": "Uninstall %1",
    "LaunchProgram": "Launch %1",
    "AssocFileExtension": "&Associate %1 with the %2 file extension",
    "AssocingFileExtension": "Associating %1 with the %2 file extension...",
    "AutoStartProgramGroupDescription": "Startup:",
    "AutoStartProgram": "Automatically start %1",
    "AddonHostProgramNotFound": "%1 could not be located in the folder you selected.%n%nDo you want to continue anyway?",
}


# ── The sites ────────────────────────────────────────────────────────────────

# [Code] sites: (id, routine, pattern, which match, how many matches, argument, the routine's parameters, where shown).
# The pattern ends just before the call's arguments or the assignment's value; the routine is read without // comments.
# A routine must hold exactly that many matches, so a site that moves or doubles is noticed.
CODE_SITES: list[tuple[str, str, str, int, int, int, dict[str, str], str]] = [
    ("webview2.offer", "OfferWebView2", r"\bSuppressibleMsgBox\(", 0, 2, 0, {},
     "a box, Yes or No, on the Ready page: the WebView2 Runtime is missing"),
    ("download.caption", "InitializeWizard", r"\bCreateDownloadPage\(", 0, 1, 0, {}, "the download page's heading"),
    ("download.description", "InitializeWizard", r"\bCreateDownloadPage\(", 0, 1, 1, {}, "the download page's line"),
    ("webview2.download_failed", "OfferWebView2", r"\bSuppressibleMsgBox\(", 1, 2, 0, {},
     "a box, OK: the runtime's installer could not be downloaded"),
    ("still_running/Setup", "MonitorClosed", r"\bSuppressibleMsgBox\(", 0, 1, 0, {"Who": "Setup"},
     "a box in Setup, Retry or Cancel: the monitor did not close"),
    ("prepare.still_running", "PrepareToInstall", r"\bResult :=", 1, 2, 0, {},
     "the Preparing to Install page, when Cancel was chosen in that box"),
    ("in_use", "StopInUse", r"\bSuppressibleMsgBox\(", 0, 1, 0, {}, "a box, OK: a file of the old install is in use"),
    ("moved_back_failed", "MoveOldInstallAside", r"\bSuppressibleMsgBox\(", 0, 1, 0, {},
     "a box, OK: the old exe could not be moved back"),
    ("webview2.checking", "InstallWebView2", r"\bWizardForm\.StatusLabel\.Caption :=", 0, 2, 0, {},
     "the Installing page's status line"),
    ("webview2.not_verified", "InstallWebView2", r"\bSuppressibleMsgBox\(", 0, 2, 0, {},
     "a box, OK: the download is not Microsoft's"),
    ("webview2.installing", "InstallWebView2", r"\bWizardForm\.StatusLabel\.Caption :=", 1, 2, 0, {},
     "the Installing page's status line"),
    ("webview2.not_installed", "InstallWebView2", r"\bSuppressibleMsgBox\(", 1, 2, 0, {},
     "a box, OK: the runtime is still missing"),
    ("not_put_back", "PutOldInstallBack", r"\bSuppressibleMsgBox\(", 0, 1, 0, {},
     "a box, OK: the old version could not be put back"),
    ("ended.tools_one", "EndedNote", r"\bif EndedTools = 1 then\s+Result :=", 0, 1, 0, {},
     "the Finish page, after Inno Setup's text: one MCP tool ended"),
    ("ended.tools_other", "EndedNote", r"\belse if EndedTools > 1 then\s+Result :=", 0, 1, 0, {},
     "the Finish page: several MCP tools ended"),
    ("ended.waits_one", "EndedNote", r"\bif EndedWaits = 1 then\s+Result := Result \+", 0, 1, 0, {},
     "the Finish page: one wait let go"),
    ("ended.waits_other", "EndedNote", r"\belse\s+Result := Result \+", 0, 1, 0, {},
     "the Finish page: several waits let go"),
    ("still_running/Uninstall", "MonitorClosed", r"(?<!\w)MsgBox\(", 0, 1, 0, {"Who": "Uninstall"},
     "a box in the uninstaller, Retry or Cancel: the monitor did not close"),
    ("uninstall.taking_out", "RemoveFromClaudeCode", r"\bUninstallProgressForm\.StatusLabel\.Caption :=", 0, 2, 0, {},
     "the uninstall window's status line"),
    ("uninstall.left_default", "RemoveFromClaudeCode", r"\bLeft :=", 0, 3, 0, {},
     "the uninstaller's next box, in place of the program's report when it gave none; also logged"),
    ("uninstall.still_in_claude_code", "RemoveFromClaudeCode", r"(?<!\w)MsgBox\(", 0, 1, 0, {},
     "a box in the uninstaller, OK: what is still in Claude Code"),
    ("uninstall.delete_data", "AskToDeleteUserData", r"(?<!\w)MsgBox\(", 0, 1, 0, {},
     "a box at the end of the uninstall, Yes or No (No by default): delete the settings and history"),
]

# The sites outside [Code] (rendered by section_words), with where they show.
SECTION_SITES: dict[str, str] = {
    "setup.name": "[Setup] AppName: the product's name on Inno Setup's own pages",
    "apps.name": "the name in Apps (the uninstall key's DisplayName)",
    "setup.publisher": "[Setup] AppPublisher: the publisher in Apps",
    "setup.group": "[Setup] DefaultGroupName: the Start-menu folder",
    "icon.app": "[Icons] the program's Start-menu name",
    "icon.uninstall": "[Icons] the uninstaller's Start-menu name",
    "task.group": "[Tasks] the heading above the task on the Select Additional Tasks page",
    "task.startup": "[Tasks] the task's box on the Select Additional Tasks page",
    "run.launch": "[Run] the box on the Finish page that starts the monitor",
}


# ── Reading the script ───────────────────────────────────────────────────────

def without_comments(text: str) -> str:
    """Pascal without // comments; a // inside a string literal (a web address) stays. Lines are kept."""
    lines = []
    for line in text.split("\n"):
        quoted = False
        for at, char in enumerate(line):
            if char == "'":
                quoted = not quoted            # a doubled quote inside a string toggles twice
            elif char == "/" and line.startswith("//", at) and not quoted:
                line = line[:at]
                break
        lines.append(line)
    return "\n".join(lines)


def section(script: str, name: str) -> list[str]:
    """The lines of [name], stripped, without blank lines, ; comments and preprocessor lines."""
    found = re.search(rf"^\[{re.escape(name)}\][ \t]*$(.*?)(?=^\[|\Z)", script, re.M | re.S)
    if found is None:
        raise ValueError(f"installer.iss has no [{name}]")
    lines = [line.strip() for line in found.group(1).splitlines()]
    return [line for line in lines if line and not line.startswith((";", "#"))]


def has_section(script: str, name: str) -> bool:
    return re.search(rf"^\[{re.escape(name)}\][ \t]*$", script, re.M) is not None


def setup_values(script: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in section(script, "Setup"))


def parameters(line: str) -> dict[str, str]:
    """One entry line -> its parameters; a quoted value loses its quotes, a doubled quote in it is one quote."""
    out: dict[str, str] = {}
    for key, raw in re.findall(r'(\w+):\s*("(?:[^"]|"")*"|[^;]*)', line):
        raw = raw.strip()
        if raw.startswith('"') and raw.endswith('"'):
            raw = raw[1:-1].replace('""', '"')
        out[key] = raw
    return out


def routine(script: str, name: str) -> str:
    """One [Code] procedure or function, from its header to the next routine or section comment, without comments."""
    code = without_comments(script[script.index("[Code]"):])
    found = re.search(rf"^(?:procedure|function) {name}\b.*?(?=^(?:procedure|function) |^// ----|\Z)", code, re.M | re.S)
    if found is None:
        raise ValueError(f"installer.iss has no routine {name}")
    return found.group(0)


# ── The messages ─────────────────────────────────────────────────────────────

def expand_newlines(value: str) -> str:
    """%n as CR LF, as ISCC expands a custom message (a % before any other character keeps that character)."""
    out, at = [], 0
    while at < len(value):
        if value[at] == "%" and at + 1 < len(value):
            out.append(CR_LF if value[at + 1] == "n" else value[at:at + 2])
            at += 2
        else:
            out.append(value[at])
            at += 1
    return "".join(out)


def read_isl(text: str) -> dict[str, dict[str, str]]:
    """A messages file as ISCC reads it (Compiler.SetupCompiler.pas, EnumIniSection): [Section] lines, name=value lines
    (the name trimmed, the value as written after the =), blank lines and ; comments skipped. Stricter than ISCC on one
    point: a name given twice in a section (ISCC keeps the last) is refused. Raises on a line outside a section or a
    line without = too, as ISCC does."""
    out: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith(";"):
            continue
        if stripped.startswith("["):
            if not stripped.endswith("]") or len(stripped) < 3:
                raise ValueError(f"line {number}: a bad section tag")
            current = out.setdefault(stripped[1:-1], {})
            continue
        if current is None:
            raise ValueError(f"line {number}: text before any section")
        if "=" not in line:
            raise ValueError(f"line {number}: no =")
        name, value = line.split("=", 1)
        name = name.strip()
        if name in current:
            raise ValueError(f"line {number}: {name} is given twice")
        current[name] = value
    return out


def custom_messages(text: str) -> dict[str, str]:
    """The [CustomMessages] of a messages file, %n expanded."""
    return {name: expand_newlines(value) for name, value in read_isl(text).get("CustomMessages", {}).items()}


def language_lines(script: str) -> list[dict[str, str]]:
    """Every [Languages] entry (Name, MessagesFile), in order, whatever preprocessor switch is around it."""
    return [parameters(line) for line in section(script, "Languages")]


def messages_of(files: str, repo: Path = REPO) -> dict[str, str]:
    """The custom messages a [Languages] entry's MessagesFile list gives, each file over the ones before it.
    "compiler:Default.isl" is INNO_DEFAULT_CUSTOM; another compiler: file is Inno Setup's and not read here."""
    found: dict[str, str] = {}
    for name in files.split(","):
        if name == "compiler:Default.isl":
            found.update({key: expand_newlines(value) for key, value in INNO_DEFAULT_CUSTOM.items()})
        elif name.startswith("compiler:"):
            raise ValueError(f"{name} is Inno Setup's own file, not read here")
        else:
            found.update(custom_messages((repo / name.replace("\\", "/")).read_text(encoding="utf-8")))
    return found


def english_messages(script: str, repo: Path = REPO) -> dict[str, str]:
    """The custom messages of the first [Languages] entry: English, for a Windows language Setup does not offer."""
    for name in ("CustomMessages", "Messages"):
        if has_section(script, name):
            raise ValueError(f"installer.iss has a [{name}] section of its own, which this does not read")
    return messages_of(language_lines(script)[0]["MessagesFile"], repo)


def fmt_message(template: str, args: list[str]) -> str:
    """Inno Setup's FmtMessage: %1 to %9 become the arguments, %% one %, any other % stays; values are not read again."""
    out, at = [], 0
    while at < len(template):
        char = template[at]
        following = template[at + 1] if at + 1 < len(template) else ""
        if char == "%" and following in "123456789" and following and int(following) <= len(args):
            out.append(args[int(following) - 1])
            at += 2
        elif char == "%":
            out.append("%")
            at += 2 if following == "%" else 1
        else:
            out.append(char)
            at += 1
    return "".join(out)


def pascal_format(template: str, args: list[str]) -> str:
    """Delphi's Format for the shapes the script's texts use: %d and %s take the next argument, %% is one %."""
    pieces = iter(args)
    out, at = [], 0
    while at < len(template):
        if template[at] != "%":
            out.append(template[at])
            at += 1
            continue
        kind = template[at + 1:at + 2]
        if kind == "%":
            out.append("%")
        elif kind in ("d", "s"):
            out.append(next(pieces))
        else:
            raise ValueError(f"Format with %{kind} is not read here")
        at += 2
    return "".join(out)


# ── Rendering an expression ──────────────────────────────────────────────────

class Context:
    """What an expression is rendered with: the messages CustomMessage reads, and the values of named parameters."""

    def __init__(self, messages: dict[str, str], env: dict[str, str] | None = None) -> None:
        self.messages = messages
        self.env = env or {}
        self.used: list[str] = []          # the message names read, in order

    def message(self, name: str) -> str:
        if name not in self.messages:
            raise KeyError(f"no custom message {name!r} (Setup raises at run time; {{cm:{name}}} fails the compile)")
        self.used.append(name)
        return self.messages[name]


_NAME = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")
_CHARS = re.compile(r"(?:#\d+)+")


def _skip(text: str, at: int) -> int:
    while at < len(text) and text[at] in " \t\r\n":
        at += 1
    return at


def _expect(text: str, at: int, char: str) -> int:
    at = _skip(text, at)
    if not text.startswith(char, at):
        raise ValueError(f"expected {char!r} at {text[at:at + 40]!r}")
    return at + 1


def _literal(text: str, at: int) -> tuple[str, int]:
    out, at = [], at + 1
    while True:
        if at >= len(text):
            raise ValueError("a string literal does not end")
        if text[at] == "'":
            if text.startswith("''", at):
                out.append("'")
                at += 2
                continue
            return "".join(out), at + 1
        out.append(text[at])
        at += 1


def _array(text: str, at: int, context: Context) -> tuple[list[str], int]:
    at = _expect(text, at, "[")
    items: list[str] = []
    at = _skip(text, at)
    if text.startswith("]", at):
        return items, at + 1
    while True:
        item, at = expression(text, at, context)
        items.append(item)
        at = _skip(text, at)
        if text.startswith(",", at):
            at += 1
            continue
        return items, _expect(text, at, "]")


def _call(name: str, text: str, at: int, context: Context) -> tuple[str, int]:
    """A call at ``at`` (just past its opening parenthesis)."""
    if name == "CustomMessage":
        key, at = expression(text, at, Context({}))
        return context.message(key), _expect(text, at, ")")
    if name == "IntToStr":
        found = _NAME.match(text, _skip(text, at))
        if found is None:
            raise ValueError(f"IntToStr of something other than a name at {text[at:at + 40]!r}")
        return "{" + found.group(0) + "}", _expect(text, found.end(), ")")
    if name in ("FmtMessage", "Format"):
        template, at = expression(text, at, context)
        at = _expect(text, at, ",")
        args, at = _array(text, at, context)
        filled = fmt_message(template, args) if name == "FmtMessage" else pascal_format(template, args)
        return filled, _expect(text, at, ")")
    raise ValueError(f"a call to {name} is not read here")


def _term(text: str, at: int, context: Context) -> tuple[str, int]:
    at = _skip(text, at)
    if text.startswith("'", at):
        return _literal(text, at)
    if text.startswith("#", at):
        found = _CHARS.match(text, at)
        assert found is not None
        return "".join(chr(int(code)) for code in found.group(0)[1:].split("#")), found.end()
    if text.startswith("(", at):
        value, at = expression(text, at + 1, context)
        return value, _expect(text, at, ")")
    found = _NAME.match(text, at)
    if found is None:
        raise ValueError(f"cannot read the expression at {text[at:at + 40]!r}")
    name, at = found.group(0), _skip(text, found.end())
    if text.startswith("(", at):
        return _call(name, text, at + 1, context)
    return context.env.get(name, "{" + name + "}"), at


def expression(text: str, at: int, context: Context) -> tuple[str, int]:
    """The string expression at ``at``, rendered, and where it ended (before the , ; ) or ] that follows it)."""
    parts = []
    while True:
        part, at = _term(text, at, context)
        parts.append(part)
        at = _skip(text, at)
        if text.startswith("+", at):
            at += 1
            continue
        return "".join(parts), at


def constants(value: str, messages: dict[str, str], used: list[str] | None = None) -> str:
    """A section value as Setup shows it: {cm:X} is the message through FmtMessage with no arguments (%% one %, as
    Setup.MainFunc.pas's ExpandCustomMessageConst gives it), {#Name} (the preprocessor's) is {Name}; other constants
    stay as written. ``used`` collects the message names it read."""
    def one(found: re.Match[str]) -> str:
        inner = found.group(1)
        if inner.startswith("cm:"):
            if "," in inner:
                raise ValueError(f"{{{inner}}}: a message with arguments is not read here")
            if used is not None:
                used.append(inner[3:])
            return fmt_message(Context(messages).message(inner[3:]), [])
        if inner.startswith("#"):
            return "{" + inner[1:] + "}"
        return found.group(0)
    return re.sub(r"\{([^{}]*)\}", one, value)


# ── The texts ────────────────────────────────────────────────────────────────

def code_site(script: str, site: tuple[str, str, str, int, int, int, dict[str, str], str],
              messages: dict[str, str], used: list[str] | None = None) -> str:
    """One [Code] site, rendered. ``used`` collects the message names it read."""
    site_id, name, pattern, which, count, argument, env, _where = site
    body = routine(script, name)
    hits = list(re.finditer(pattern, body))
    if len(hits) != count:
        raise ValueError(f"{site_id}: {name} has {len(hits)} matches of {pattern!r}, not {count}")
    at = hits[which].end()
    for _ in range(argument):                 # the arguments before the one shown
        _skipped, at = expression(body, at, Context(messages, env))
        at = _expect(body, at, ",")
    context = Context(messages, env)
    whole = re.compile(r"\s*([A-Za-z_]\w*)\s*[,;)]").match(body, at)
    if whole is not None and whole.group(1) not in env:
        assigned = list(re.finditer(rf"\b{whole.group(1)} :=", body))
        if len(assigned) > 1:
            raise ValueError(f"{site_id}: {whole.group(1)} is assigned {len(assigned)} times in {name}")
        if assigned:                          # the box's text is in a variable: read its one assignment
            at = assigned[0].end()
    text, _end = expression(body, at, context)
    if used is not None:
        used.extend(context.used)
    return text


def apps_name(setup: dict[str, str], messages: dict[str, str]) -> str:
    """The uninstall key's DisplayName: UninstallDisplayName, else AppVerName, else the language's NameAndVersion
    filled with AppName and AppVersion (Inno Setup 6.7.1, Setup.Install.pas 296-298, Setup.MainFunc.pas 3767-3774)."""
    for key in ("UninstallDisplayName", "AppVerName"):
        if setup.get(key):
            return constants(setup[key], messages)
    template = messages.get("NameAndVersion", "%1 %2")
    return fmt_message(template, [constants(setup["AppName"], messages), constants(setup["AppVersion"], messages)])


def section_words(script: str, messages: dict[str, str], used: list[str] | None = None) -> dict[str, str]:
    setup = setup_values(script)
    (task,) = [parameters(line) for line in section(script, "Tasks")]
    (run,) = [parameters(line) for line in section(script, "Run")]
    icons = {entry["Filename"]: entry["Name"] for entry in (parameters(line) for line in section(script, "Icons"))}

    def shortcut(filename: str) -> str:
        name = icons[filename]
        if not name.startswith("{group}\\"):
            raise ValueError(f"the shortcut {name!r} is not in the Start-menu folder")
        return constants(name[len("{group}\\"):], messages, used)

    return {
        "setup.name": constants(setup["AppName"], messages, used),
        "apps.name": apps_name(setup, messages),
        "setup.publisher": constants(setup["AppPublisher"], messages, used),
        "setup.group": constants(setup["DefaultGroupName"], messages, used),
        "icon.app": shortcut("{app}\\{#ExeName}"),
        "icon.uninstall": shortcut("{uninstallexe}"),
        "task.group": constants(task["GroupDescription"], messages, used),
        "task.startup": constants(task["Description"], messages, used),
        "run.launch": constants(run["Description"], messages, used),
    }


def words(script: str | None = None, repo: Path = REPO, used: list[str] | None = None) -> dict[str, str]:
    """Every text the script shows, in English, by site id: the record's shape. ``used`` collects the message names
    the sites read."""
    text = ISS.read_text(encoding="ascii") if script is None else script
    messages = english_messages(text, repo)
    found = section_words(text, messages, used)
    for site in CODE_SITES:
        found[site[0]] = code_site(text, site, messages, used)
    return found


def used_names(script: str) -> list[str]:
    """Every message name the script shows: CustomMessage('X') in [Code] and {cm:X} above it, comments left out."""
    head, code = script[:script.index("[Code]")], without_comments(script[script.index("[Code]"):])
    lines = "\n".join(line for line in head.splitlines() if not line.lstrip().startswith(";"))
    return re.findall(r"\bCustomMessage\('(\w+)'\)", code) + re.findall(r"\{cm:(\w+)", lines)


class _AnyMessage(Context):
    """A context in which every message name reads as empty: to find where an expression ends."""

    def message(self, name: str) -> str:
        return ""


# Where text is shown in [Code]: a box's first argument, a label's caption, the download page's two texts.
SINKS = ((r"(?<!\w)(?:Suppressible)?MsgBox\(", 1), (r"\.Caption :=", 1), (r"\bCreateDownloadPage\(", 2))
_LITERAL = re.compile(r"'((?:[^']|'')*)'|((?:#\d+)+)")


def shown_literals(script: str) -> list[tuple[str, str]]:
    """(routine, literal) for every literal in a place [Code] shows text (SINKS, and PrepareToInstall's Result, the
    Preparing page's text), a CustomMessage's name aside; a run of #13#10 codes is one literal."""
    code = without_comments(script[script.index("[Code]"):])
    found: list[tuple[str, str]] = []
    for name in re.findall(r"^(?:procedure|function) (\w+)", code, re.M):
        body = routine(script, name)
        starts = [(hit.end(), count) for pattern, count in SINKS for hit in re.finditer(pattern, body)]
        if name == "PrepareToInstall":
            starts += [(hit.end(), 1) for hit in re.finditer(r"\bResult :=", body)]
        for at, count in starts:
            for index in range(count):
                _text, end = expression(body, at, _AnyMessage({}))
                for quoted, chars in _LITERAL.findall(re.sub(r"\bCustomMessage\('\w*'\)", "", body[at:end])):
                    found.append((name, quoted.replace("''", "'") if not chars else
                                  "".join(chr(int(code)) for code in chars[1:].split("#"))))
                if index + 1 < count:
                    at = _expect(body, end, ",")
    return found


# ── The languages (#103) ─────────────────────────────────────────────────────

MESSAGES_DIR = REPO / "installer-messages"
# Inno Setup's own messages file for each language of the app, as [Languages] names it, and its [LangOptions]
# LanguageID (Inno Setup 6.7.1's Default.isl and Languages\*.isl; a test compares them with the PC's Inno Setup).
INNO_FILES: dict[str, str] = {"en": "compiler:Default.isl", "fr": "compiler:Languages\\French.isl",
                              "es": "compiler:Languages\\Spanish.isl", "tr": "compiler:Languages\\Turkish.isl"}
INNO_LANGUAGE_IDS: dict[str, int] = {"en": 0x0409, "fr": 0x040C, "es": 0x0C0A, "tr": 0x041F}
# Names that stay as written in every language (en.isl's rules): where English has one, a translation has it too.
KEPT_NAMES = ("Claude Usage Monitor", "Claude Code", "Microsoft Edge WebView2", "WebView2", "microsoft.com", "/mcp",
              "https://github.com/Godimas101/claude-usage-monitor/blob/main/docs/AGENT_HOOK.md")


def language_entries(script: str, review: bool) -> list[dict[str, str]]:
    """The [Languages] entries of the release build (``review`` False) or of the review build (ISCC /DAllLanguages):
    the entries between "#ifdef AllLanguages" and "#endif" are the review build's only. Any other directive there is
    refused, so a switch this does not know is noticed."""
    found = re.search(r"^\[Languages\][ \t]*$(.*?)(?=^\[|\Z)", script, re.M | re.S)
    if found is None:
        raise ValueError("installer.iss has no [Languages]")
    entries, inside = [], False
    for line in (raw.strip() for raw in found.group(1).splitlines()):
        if not line or line.startswith(";"):
            continue
        if line == "#ifdef AllLanguages" and not inside:
            inside = True
        elif line == "#endif" and inside:
            inside = False
        elif line.startswith("#"):
            raise ValueError(f"[Languages]: {line!r} is not read here")
        elif review or not inside:
            entries.append(parameters(line))
    if inside:
        raise ValueError("[Languages]: #ifdef AllLanguages has no #endif")
    return entries


def ours(language: str, folder: Path = MESSAGES_DIR) -> dict[str, str]:
    """installer-messages/<language>.isl's custom messages, %n expanded."""
    return custom_messages((folder / f"{language}.isl").read_text(encoding="utf-8"))


def missing(language: str, folder: Path = MESSAGES_DIR) -> list[str]:
    """The names en.isl has and <language>.isl lacks: that language's to do. Setup shows English for each."""
    theirs = ours(language, folder)
    return [name for name in ours("en", folder) if name not in theirs]


def message(language: str, name: str, *args: str, folder: Path = MESSAGES_DIR) -> str:
    """The text Setup shows for ``name`` in ``language``, filled as FmtMessage fills it: the language's own entry, else
    English's (ISCC's fallback to the first language that defines it)."""
    theirs, english = ours(language, folder), ours("en", folder)
    return fmt_message(theirs.get(name, english[name]), list(args))


def _access_keys(value: str) -> int:
    return len(re.findall(r"(?<!&)&(?!&)", value))


def problems(language: str, folder: Path = MESSAGES_DIR) -> list[str]:
    """What is wrong with <language>.isl's shape against en.isl: an encoding other than UTF-8 without a byte order mark
    in NFC (en.isl: ASCII), a section other than [CustomMessages], a name English lacks or one of Inno Setup's own,
    another set of %1 to %9, another count of %n or of & (the access key), any other % (a literal one, %% too: English
    has none, and a box that shows CustomMessage() without FmtMessage shows %% as two signs), a name that stays as
    written (KEPT_NAMES) dropped, or a space at either end."""
    import unicodedata

    path = folder / f"{language}.isl"
    raw = path.read_bytes()
    found: list[str] = []
    if raw.startswith(b"\xef\xbb\xbf"):
        found.append("starts with a byte order mark")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return found + ["is not UTF-8"]
    if language == "en" and not text.isascii():
        found.append("is not ASCII")
    if unicodedata.normalize("NFC", text) != text:
        found.append("is not NFC")
    sections = read_isl(text)
    found += [f"has a [{name}] section" for name in sections if name != "CustomMessages"]
    english = custom_messages((folder / "en.isl").read_text(encoding="utf-8"))
    inno = {name.lower() for name in INNO_DEFAULT_CUSTOM}
    for name, written in sections.get("CustomMessages", {}).items():
        if name.lower() in inno:
            found.append(f"{name}: is one of Inno Setup's own messages")
        if name not in english:
            found.append(f"{name}: English has no such message")
            continue
        source = written
        model = read_isl((folder / "en.isl").read_text(encoding="utf-8"))["CustomMessages"][name]
        if sorted(set(re.findall(r"%([1-9])", source))) != sorted(set(re.findall(r"%([1-9])", model))):
            found.append(f"{name}: other arguments than English's %1 to %9")
        if source.count("%n") != model.count("%n"):
            found.append(f"{name}: {source.count('%n')} line breaks, English has {model.count('%n')}")
        if _access_keys(source) != _access_keys(model):
            found.append(f"{name}: another count of & than English's")
        if re.search(r"%(?![1-9n])", source):
            found.append(f"{name}: a literal % (English has none: Setup shows %% as two signs where no "
                         "FmtMessage runs)")
        found += [f"{name}: drops {kept!r}" for kept in KEPT_NAMES if kept in model and kept not in source]
        if source != source.strip():
            found.append(f"{name}: a space at an end")
    return found


def inno_language(langid: int, entries: list[str], lang: str = "") -> str:
    """The language Setup picks (Inno Setup 6.7.1, SetupLdrAndSetup.InstFunc.pas DetermineDefaultLanguage): a /LANG=
    naming an entry (any case), else the entry whose LanguageID is Windows' display language exactly, else the first
    whose primary language (the low 10 bits) is the same, else the first entry; a LANGID of 0 (the call failed) is
    the first entry. ``entries`` are language names in [Languages] order, with INNO_LANGUAGE_IDS. (Inno Setup's code
    page check for Chinese is left out: no entry is Chinese.)"""
    for name in entries:
        if lang and lang.lower() == name.lower():
            return name
    if langid:
        for name in entries:
            if INNO_LANGUAGE_IDS[name] == langid:
                return name
        for name in entries:
            if INNO_LANGUAGE_IDS[name] & 0x3FF == langid & 0x3FF:
                return name
    return entries[0]


def inno_folder() -> Path | None:
    """The folder of the Inno Setup on this PC (its ISCC.exe), as tests/test_update_check.py's find_iscc finds it."""
    import os
    import shutil

    candidates = [os.environ.get("ISCC"), r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
                  r"C:\Program Files\Inno Setup 6\ISCC.exe", shutil.which("ISCC")]
    found = next((Path(candidate) for candidate in candidates if candidate and Path(candidate).is_file()), None)
    return found.parent if found is not None else None


# ── Running a routine (EndedNote) ────────────────────────────────────────────

_TOKEN = re.compile(r"(?P<string>'(?:[^']|'')*')|(?P<chars>(?:#\d+)+)|(?P<number>\d+)|(?P<name>[A-Za-z_]\w*)"
                    r"|(?P<op>:=|<>|<=|>=|[=<>+()\[\],;])")
_COMPARE = {"=": lambda a, b: a == b, "<>": lambda a, b: a != b, "<": lambda a, b: a < b, ">": lambda a, b: a > b,
            "<=": lambda a, b: a <= b, ">=": lambda a, b: a >= b}


class _Exit(Exception):
    pass


class _Routine:
    """Enough Pascal to run EndedNote: begin/end, if/then/else, := and Exit; strings and integers, = <> < > <= >=, +,
    and, or (short-circuit, as Pascal Script evaluates them), not; CustomMessage, FmtMessage, Format (%d, %s) and
    IntToStr. Anything else is refused, so a routine that grows past this is noticed."""

    def __init__(self, text: str) -> None:
        self.tokens: list[tuple[str, Any]] = []
        at = 0
        while (at := _skip(text, at)) < len(text):
            found = _TOKEN.match(text, at)
            if found is None:
                raise ValueError(f"cannot read {text[at:at + 40]!r}")
            kind, value = found.lastgroup or "", found.group(found.lastgroup or 0)
            if kind == "string":
                value = value[1:-1].replace("''", "'")
            elif kind == "chars":
                value, kind = "".join(chr(int(code)) for code in value[1:].split("#")), "string"
            elif kind == "number":
                value = int(value)
            self.tokens.append((kind, value))
            at = found.end()
        self.at = 0
        self.body = self._block()

    # parsing
    def _peek(self) -> tuple[str, Any]:
        return self.tokens[self.at] if self.at < len(self.tokens) else ("", None)

    def _is(self, word: str) -> bool:
        kind, value = self._peek()
        return (kind == "name" and value.lower() == word) or (kind == "op" and value == word)

    def _take(self, word: str) -> None:
        if not self._is(word):
            raise ValueError(f"expected {word!r}, found {self._peek()!r}")
        self.at += 1

    def _block(self) -> tuple[Any, ...]:
        self._take("begin")
        statements = []
        while not self._is("end"):
            statements.append(self._statement())
            if self._is(";"):
                self.at += 1
            elif not self._is("end"):
                raise ValueError(f"expected ; or end, found {self._peek()!r}")
        self._take("end")
        return ("block", statements)

    def _statement(self) -> tuple[Any, ...]:
        if self._is("begin"):
            return self._block()
        if self._is("if"):
            self.at += 1
            condition = self._expression()
            self._take("then")
            then = self._statement()
            otherwise = None
            if self._is("else"):
                self.at += 1
                otherwise = self._statement()
            return ("if", condition, then, otherwise)
        if self._is("exit"):
            self.at += 1
            return ("exit",)
        kind, name = self._peek()
        if kind != "name":
            raise ValueError(f"cannot run the statement at {self._peek()!r}")
        self.at += 1
        self._take(":=")
        return ("set", name, self._expression())

    def _expression(self) -> tuple[Any, ...]:
        left = self._simple()
        kind, value = self._peek()
        if kind == "op" and value in _COMPARE:
            self.at += 1
            return ("compare", value, left, self._simple())
        return left

    def _simple(self) -> tuple[Any, ...]:
        left = self._term()
        while self._is("+") or self._is("or"):
            operator = self._peek()[1].lower()
            self.at += 1
            left = (operator, left, self._term())
        return left

    def _term(self) -> tuple[Any, ...]:
        left = self._factor()
        while self._is("and"):
            self.at += 1
            left = ("and", left, self._factor())
        return left

    def _factor(self) -> tuple[Any, ...]:
        kind, value = self._peek()
        if self._is("not"):
            self.at += 1
            return ("not", self._factor())
        self.at += 1
        if kind in ("string", "number"):
            return ("value", value)
        if kind == "op" and value == "(":
            inner = self._expression()
            self._take(")")
            return inner
        if kind == "op" and value == "[":
            items = []
            while not self._is("]"):
                items.append(self._expression())
                if self._is(","):
                    self.at += 1
            self._take("]")
            return ("array", items)
        if kind != "name":
            raise ValueError(f"cannot read {value!r}")
        if self._is("("):
            self.at += 1
            args = []
            while not self._is(")"):
                args.append(self._expression())
                if self._is(","):
                    self.at += 1
            self._take(")")
            return ("call", value, args)
        return ("name", value)

    # running
    def run(self, values: dict[str, Any], messages: dict[str, str]) -> dict[str, Any]:
        state = dict(values)
        try:
            self._do(self.body, state, messages)
        except _Exit:
            pass
        return state

    def _do(self, node: tuple[Any, ...], state: dict[str, Any], messages: dict[str, str]) -> None:
        kind = node[0]
        if kind == "block":
            for statement in node[1]:
                self._do(statement, state, messages)
        elif kind == "if":
            branch = node[2] if self._value(node[1], state, messages) else node[3]
            if branch is not None:
                self._do(branch, state, messages)
        elif kind == "set":
            state[node[1]] = self._value(node[2], state, messages)
        elif kind == "exit":
            raise _Exit

    def _value(self, node: tuple[Any, ...], state: dict[str, Any], messages: dict[str, str]) -> Any:
        kind = node[0]
        if kind == "value":
            return node[1]
        if kind == "name":
            if node[1] not in state:
                raise KeyError(f"{node[1]} has no value")
            return state[node[1]]
        if kind == "compare":
            return _COMPARE[node[1]](self._value(node[2], state, messages), self._value(node[3], state, messages))
        if kind == "+":
            return self._value(node[1], state, messages) + self._value(node[2], state, messages)
        if kind == "and":
            return bool(self._value(node[1], state, messages)) and bool(self._value(node[2], state, messages))
        if kind == "or":
            return bool(self._value(node[1], state, messages)) or bool(self._value(node[2], state, messages))
        if kind == "not":
            return not self._value(node[1], state, messages)
        if kind == "array":
            return [self._value(item, state, messages) for item in node[1]]
        args = [self._value(arg, state, messages) for arg in node[2]]
        if node[1] == "CustomMessage":
            return Context(messages).message(args[0])
        if node[1] == "FmtMessage":
            return fmt_message(args[0], args[1])
        if node[1] == "Format":
            return pascal_format(args[0], [str(arg) for arg in args[1]])
        if node[1] == "IntToStr":
            return str(int(args[0]))
        raise ValueError(f"a call to {node[1]} is not run here")


def run(script: str, name: str, values: dict[str, Any], messages: dict[str, str]) -> dict[str, Any]:
    """Run the [Code] routine ``name`` (no var section, only what _Routine reads) with ``values`` for its names; returns
    every name's value after it, Result among them."""
    text = routine(script, name)
    start = re.search(r"^begin\b", text, re.M)
    if start is None:
        raise ValueError(f"{name} has no begin")
    return _Routine(text[start.start():].rstrip().rstrip(";")).run(values, messages)


def recorded() -> dict[str, str]:
    return json.loads(RECORD.read_text(encoding="utf-8"))["words"]


def record() -> int:
    """Write the record, one line per entry. Refused (exit 1) when it exists."""
    if RECORD.exists():
        print(f"{RECORD.name} exists: English must not change, so it is never recorded again")
        return 1
    found = words()
    items = list(found.items())
    lines = ["{", f' "_about": {json.dumps(ABOUT)},', ' "words": {']
    for at, (key, value) in enumerate(items):
        comma = "," if at < len(items) - 1 else ""
        lines.append(f"  {json.dumps(key)}: {json.dumps(value)}{comma}")
    lines += [" }", "}"]
    RECORD.write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")
    assert recorded() == found
    print(f"wrote {len(found)} entries to {RECORD}")
    return 0


def main(argv: list[str]) -> int:
    if argv == ["--print"]:
        print(json.dumps(words(), indent=1))
        return 0
    if argv == ["--record"]:
        return record()
    if argv == ["--todo"]:
        for language in ("fr", "es", "tr"):
            todo = missing(language)
            print(f"{language}.isl lacks {len(todo)} of en.isl's {len(ours('en'))} messages: {', '.join(todo) or '-'}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
