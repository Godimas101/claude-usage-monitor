"""Setup and the uninstaller show their texts in the Windows language (#103), and English stays what it was.

tests/fixtures/installer_words_english.json holds today's English of every text installer.iss shows, and the names that
stay, recorded before #103 moved a word (tests/installer_words.py renders each site of the script as Setup builds it).
"""
from __future__ import annotations

import json

import pytest

import installer_words


# ── English stays what it was (step a1) ───────────────────────────────────────

def test_installer_english_is_as_recorded():
    """Every box, status line, page text, task and run box, and every name, byte for byte, as it was before #103: each
    site of today's script rendered with the English messages, against the record."""
    now = installer_words.words()
    recorded = installer_words.recorded()
    assert sorted(now) == sorted(recorded)
    changed = {key: (recorded[key], now[key]) for key in recorded if now[key] != recorded[key]}
    assert not changed, json.dumps(changed, indent=1)


SAMPLE = """[Code]
function Box(const Who: String): Boolean;
var
  Text: String;
begin
  Text := 'It''s ' + #13#10 + Who + ' at http://x//y'; // a comment, with a ' in it
  if MsgBox(Text, mbError, MB_OK) = IDOK then
    Exit;
  Page := CreateDownloadPage(CustomMessage('Head'),
    FmtMessage(CustomMessage('Line'), [Path, IntToStr(Count)]) + Format(' %d of %s, 100%%', [Done, Name]), nil);
  Result := Result + ('(' + Path + ')')
  else
    Result := '';
end;
"""


def test_the_renderer_reads_pascal_as_setup_builds_it():
    """The record and the tests rest on this reading: literals with '' and #13#10 joined by +, a name as {Name} unless the
    site gives its value, a box's text read from its variable's one assignment, CustomMessage from the messages,
    FmtMessage's %1 to %9 and %% (its values not read again), Format's %d and %s, IntToStr(X) as {X}."""
    messages = {"Head": "Downloading", "Line": "%2 for %1 at 100%% (%3)"}
    site = ("box", "Box", r"(?<!\w)MsgBox\(", 0, 1, 0, {"Who": "Setup"}, "")
    assert installer_words.code_site(SAMPLE, site, messages) == "It's \r\nSetup at http://x//y"
    head = ("head", "Box", r"\bCreateDownloadPage\(", 0, 1, 0, {}, "")
    used: list[str] = []
    assert installer_words.code_site(SAMPLE, head, messages, used) == "Downloading" and used == ["Head"]
    line = ("line", "Box", r"\bCreateDownloadPage\(", 0, 1, 1, {}, "")
    assert installer_words.code_site(SAMPLE, line, messages) == "{Count} for {Path} at 100% (%3) {Done} of {Name}, 100%"
    tail = ("tail", "Box", r"\bResult := Result \+", 0, 1, 0, {}, "")
    assert installer_words.code_site(SAMPLE, tail, messages) == "({Path})"
    assert installer_words.fmt_message("%1 and %2", ["%2", "x"]) == "%2 and x"        # a value is not read again
    with pytest.raises(KeyError):
        installer_words.code_site(SAMPLE, head, {})                                   # Setup raises on a missing name
    with pytest.raises(ValueError):
        installer_words.code_site(SAMPLE, ("two", "Box", r"\bResult :=", 0, 1, 0, {}, ""), messages)  # 2 matches, not 1


def test_the_messages_files_are_read_as_iscc_reads_them():
    """name=value under [CustomMessages], the name trimmed and the value kept as written after the =, %n as CR LF (a %
    before anything else stays, for FmtMessage), blank lines and ; comments skipped; text before a section refused; the
    Apps-list name is UninstallDisplayName, else AppVerName, else the language's NameAndVersion."""
    isl = "; a note\n\n[LangOptions]\nLanguageName=x\n[CustomMessages]\n  Name = a%nb 100%% %1 =c\n; skipped\nTwo=%n\n"
    assert installer_words.custom_messages(isl) == {"Name": " a\r\nb 100%% %1 =c", "Two": "\r\n"}
    with pytest.raises(ValueError):
        installer_words.read_isl("Name=x\n[CustomMessages]\n")
    with pytest.raises(ValueError):
        installer_words.read_isl("[CustomMessages]\nName=x\nName=y\n")
    messages = {"NameAndVersion": "%1 version %2", "Pinned": "never"}
    setup = {"AppName": "A", "AppVersion": "{#AppVer}"}
    assert installer_words.apps_name(setup, messages) == "A version {AppVer}"
    assert installer_words.apps_name({**setup, "UninstallDisplayName": "A v{#AppVer}"}, messages) == "A v{AppVer}"
    assert installer_words.constants("{cm:Pinned} {app}", messages) == "never {app}"
