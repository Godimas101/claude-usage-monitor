"""Setup and the uninstaller show their texts in the Windows language (#103), and English stays what it was.

tests/fixtures/installer_words_english.json holds today's English of every text installer.iss shows, and the names that
stay, recorded before #103 moved a word (tests/installer_words.py renders each site of the script as Setup builds it).
The script's texts are in installer-messages/<lang>.isl, one file per language: en.isl the English, fr.isl, es.isl and
tr.isl the drafts (#104 to #106). The release build declares the languages the app offers; the review build
(ISCC /DAllLanguages) all of them.
"""
from __future__ import annotations

import json
import re

import pytest

import installer_words
from usage_monitor import i18n

REPO = installer_words.REPO
ISS = installer_words.ISS.read_text(encoding="ascii")


# ── English stays what it was (step a1) ───────────────────────────────────────

def test_installer_english_is_as_recorded():
    """Every box, status line, page text, task and run box, and every name, byte for byte, as it was before #103: each
    site of today's script rendered with the English messages, against the record."""
    now = installer_words.words()
    recorded = installer_words.recorded()
    assert sorted(now) == sorted(recorded)
    changed = {key: (recorded[key], now[key]) for key in recorded if now[key] != recorded[key]}
    assert not changed, json.dumps(changed, indent=1)


def test_the_installer_record_is_whole():
    """So the test above cannot pass on a thin record: it holds every site of the table, nothing else, and every message
    the script names (CustomMessage or {cm:}) is read by one of those sites."""
    expected = {site[0] for site in installer_words.CODE_SITES} | set(installer_words.SECTION_SITES)
    assert set(installer_words.recorded()) == expected
    used: list[str] = []
    installer_words.words(used=used)
    assert set(installer_words.used_names(ISS)) <= set(used), sorted(set(installer_words.used_names(ISS)) - set(used))


# ── The messages: one file per language (#103) ────────────────────────────────

def test_every_message_the_script_uses_is_in_english():
    """Every CustomMessage('X') and {cm:X} of the script is in en.isl, the one Inno Setup message it uses aside
    (AutoStartProgramGroupDescription, the task heading); every en.isl message is used (English put back into the code in
    place of one, in a box, a Text := or a Left :=, leaves it unused); none is named like one of Inno Setup's own."""
    used = set(installer_words.used_names(ISS))
    english = installer_words.ours("en")
    assert used - set(english) == {"AutoStartProgramGroupDescription"}
    assert set(english) - used == set(), "en.isl messages the script never shows"
    inno = {name.lower() for name in installer_words.INNO_DEFAULT_CUSTOM}
    assert not [name for name in english if name.lower() in inno]


# The literals a sink may hold: an empty string (a cleared label, PrepareToInstall's "go ahead") and line breaks.
ALLOWED_LITERALS = {"", "\r\n", "\r\n\r\n"}


def test_no_shown_text_is_left_in_the_script():
    """Text a person reads comes from the messages: the first argument of every MsgBox and SuppressibleMsgBox, every
    .Caption :=, both texts of CreateDownloadPage and PrepareToInstall's Result hold no literal but those of
    ALLOWED_LITERALS (WizardForm.FilenameLabel.Caption := '', PrepareToInstall's Result := '' and the Finish page's
    #13#10#13#10), a message name aside; [Tasks] and [Run] descriptions are a {cm:} alone. (English put into a Text :=
    or a Left := is caught by the test above, as an unused message.)"""
    found = installer_words.shown_literals(ISS)
    assert {text for _where, text in found} <= ALLOWED_LITERALS, [item for item in found if item[1] not in ALLOWED_LITERALS]
    assert ("InstallWebView2", "") in found and ("CurPageChanged", "\r\n\r\n") in found     # the scan reads those sinks
    for section in ("Tasks", "Run"):
        for line in installer_words.section(ISS, section):
            entry = installer_words.parameters(line)
            for key in ("Description", "GroupDescription"):
                if key in entry:
                    assert re.fullmatch(r"\{cm:\w+\}", entry[key]), (section, key, entry[key])


def test_each_language_file_holds_englishs_messages_in_the_same_shape():
    """fr.isl, es.isl and tr.isl hold only names en.isl has, each with English's %1 to %9, line breaks (%n) and access
    key (&), no other %, the names that stay as written, no space at an end, in UTF-8 without a byte order mark and
    NFC, and nothing but [CustomMessages]. en.isl itself: ASCII, and the same rules."""
    for language in ("en", "fr", "es", "tr"):
        assert installer_words.problems(language) == [], language


def test_an_offered_language_has_every_message():
    """A language the release build declares has every message of en.isl: a gap would show English there. The others
    (the review build's) are drafts (#104 to #106): what each lacks is listed, not failed."""
    release = [entry["Name"] for entry in installer_words.language_entries(ISS, review=False)]
    for language in release:
        assert installer_words.missing(language) == [], f"{language} is offered but lacks messages"
    for entry in installer_words.language_entries(ISS, review=True):
        if entry["Name"] not in release:
            todo = installer_words.missing(entry["Name"])
            print(f"{entry['Name']}.isl lacks {len(todo)} of en.isl's messages (English shows): {', '.join(todo)}")


def test_the_release_build_offers_the_apps_languages():
    """The release build declares exactly the languages the app offers (i18n.OFFERED, English first), so Setup never
    speaks a language the app does not; the review build declares every catalog language (i18n.LANGUAGES). A language
    moves above the #ifdef in the change that offers it (#107)."""
    release = [entry["Name"] for entry in installer_words.language_entries(ISS, review=False)]
    review = [entry["Name"] for entry in installer_words.language_entries(ISS, review=True)]
    assert release == list(i18n.OFFERED) and release[0] == "en"
    assert review == list(i18n.LANGUAGES)


def test_english_comes_first_and_falls_back():
    """English is the first entry of both builds: Setup takes it for a Windows language it does not offer, and ISCC takes
    a message a language's file lacks from the first language that defines it. Each entry is Inno Setup's own file for
    the language, then ours."""
    for review in (False, True):
        entries = installer_words.language_entries(ISS, review=review)
        assert entries[0] == {"Name": "en", "MessagesFile": "compiler:Default.isl,installer-messages\\en.isl"}
        for entry in entries:
            name = entry["Name"]
            assert entry == {"Name": name, "MessagesFile": f"{installer_words.INNO_FILES[name]},installer-messages\\{name}.isl"}


# Display-language tags with their LANGID, as Windows' LocaleNameToLCID gives them (#103's probe); 0x1000 is a tag
# without an LCID of its own.
TAG_LANGIDS = {
    "en-US": 0x0409, "en-GB": 0x0809, "en-CA": 0x1009, "fr-FR": 0x040C, "fr-CA": 0x0C0C, "fr-BE": 0x080C, "fr-CH": 0x100C,
    "fr-LU": 0x140C, "fr-MA": 0x380C, "fr-SN": 0x280C, "fr-CD": 0x240C, "fr-DZ": 0x1000, "es-ES": 0x0C0A, "es-MX": 0x080A,
    "es-US": 0x540A, "es-419": 0x580A, "es-AR": 0x2C0A, "es-CO": 0x240A, "es-GQ": 0x1000, "es-PH": 0x1000, "tr-TR": 0x041F,
    "tr-CY": 0x1000, "de-DE": 0x0407, "ca-ES": 0x0403, "eu-ES": 0x042D, "gl-ES": 0x0456, "pt-BR": 0x0416, "zh-CN": 0x0804,
    "zh-TW": 0x0404, "ar-SA": 0x0401, "qps-ploc": 0x0501,
}


def test_setup_picks_its_language_as_the_app_does():
    """Setup reads Windows' display language (GetUserDefaultUILanguage), never asks, and never takes the last install's
    language. For every display language, Inno Setup's rule (installer_words.inno_language) picks what the app's rule
    picks from the same language (i18n.resolve_system), in the release build and in the review build. The four tags
    without an LCID of their own are left out: as far as known no Windows display pack is one of them, so Setup's input
    never is (#108). Where the two can differ is their input: the app reads the first of Windows' preferred
    languages, which can be one without its display pack, or one not yet applied by signing out (#108 checks that on a
    real profile)."""
    setup = installer_words.setup_values(ISS)
    assert (setup["LanguageDetectionMethod"], setup["ShowLanguageDialog"], setup["UsePreviousLanguage"]) == ("uilanguage", "no", "no")
    for review in (False, True):
        entries = [entry["Name"] for entry in installer_words.language_entries(ISS, review=review)]
        for tag, langid in TAG_LANGIDS.items():
            if langid == 0x1000:
                continue
            assert installer_words.inno_language(langid, entries) == i18n.resolve_system([tag], entries), (review, tag)
        assert installer_words.inno_language(0, entries) == "en"                    # the call failed: English
        assert installer_words.inno_language(0x0407, entries, lang="FR") == ("fr" if review else "en")   # /LANG=
    assert {tag for tag, langid in TAG_LANGIDS.items() if langid == 0x1000} == {"fr-DZ", "es-GQ", "es-PH", "tr-CY"}


def test_the_finish_page_and_the_toast_agree_in_every_language():
    """The Finish page's sentences and the app's toast after a silent update say the same in each language: every form a
    language's file and its catalog both have, the file's %1 as {count}, the catalog's {product} and {command} as Claude
    Code and /mcp. (English: test_update_check.py's test_the_finish_page_and_the_toast_say_the_same_english.)"""
    names = {"product": "Claude Code", "command": "/mcp"}
    pairs = {"EndedToolsOne": ("tools", "one"), "EndedToolsOther": ("tools", "other"),
             "EndedWaitsOne": ("waits", "one"), "EndedWaitsOther": ("waits", "other")}
    for language in i18n.LANGUAGES:
        theirs = installer_words.ours(language)
        catalog = json.loads((REPO / "usage_monitor" / "webui" / "i18n" / f"{language}.json").read_text(encoding="utf-8"))
        for name, (kind, form) in pairs.items():
            said = catalog.get(f"toast.updated.{kind}", {}).get(form)
            if name in theirs and said is not None:
                assert theirs[name].replace("%1", "{count}") == i18n.fill(said, names), (language, name)


def test_the_finish_page_picks_the_form_by_count():
    """Inno Setup has no plural forms, so EndedNote picks one by count: ...One for 1, ...Other with the count (IntToStr)
    for more, nothing for 0, the tools' sentence then a space and the waits'. EndedNote is run (installer_words.run)
    for each count pair. CLDR's categories agree for every count Setup can end: one only at 1, other above it, in
    every language of the app, 1 to 999,999."""
    english = installer_words.english_messages(ISS)

    def form(kind: str, count: int) -> str:
        if count == 0:
            return ""
        if count == 1:
            return english[f"Ended{kind}One"]
        return installer_words.fmt_message(english[f"Ended{kind}Other"], [str(count)])

    for tools in (0, 1, 2, 3, 12, 9999):
        for waits in (0, 1, 2, 12):
            said = installer_words.run(ISS, "EndedNote", {"EndedTools": tools, "EndedWaits": waits}, english)["Result"]
            assert said == " ".join(part for part in (form("Tools", tools), form("Waits", waits)) if part), (tools, waits)
    for language in i18n.LANGUAGES:
        assert [count for count in range(1, 1_000_000) if (i18n.plural_category(language, count) == "one") != (count == 1)] == []


def test_the_still_running_box_is_two_whole_messages():
    """MonitorClosed takes no word to put into a sentence: the uninstaller's box (IsUninstaller) is StillRunningUninstall,
    Setup's StillRunningSetup, each a whole message."""
    closed = installer_words.routine(ISS, "MonitorClosed")
    assert closed.startswith("function MonitorClosed: Boolean;")
    assert re.search(r"if IsUninstaller then begin\s+if UninstallSilent or \(MsgBox\(CustomMessage\('StillRunningUninstall'\), "
                     r"mbError, MB_RETRYCANCEL\) <> IDRETRY\) then\s+Exit;\s+end else if SuppressibleMsgBox\("
                     r"CustomMessage\('StillRunningSetup'\), mbError, MB_RETRYCANCEL, IDCANCEL\) <> IDRETRY then\s+Exit;", closed)
    assert "+" not in closed and "'" not in re.sub(r"CustomMessage\('\w+'\)", "", closed)


def test_the_uninstaller_passes_its_language_to_remove_setup():
    """The uninstaller calls the exe with the report's path, then --lang and its language (ActiveLanguage: that of the
    last Setup run), for #102 to word the report in. The path stays the word after --remove-setup, so today's main.py,
    which ignores --lang, still writes the report; and a language it does not know never makes the call an error."""
    import ctypes

    import main

    call = re.search(r"Exec\(ExpandConstant\('\{app\}\\\{#ExeName\}'\), (.*?), '', SW_HIDE,",
                     installer_words.routine(ISS, "RemoveFromClaudeCode"), re.S)
    assert call is not None
    report = r"C:\Users\A B\AppData\Local\Temp\is-1A2B.tmp\claude-code-left.txt"
    for language in ("en", "fr", "zz"):
        context = installer_words.Context({}, {"Report": report, "ActiveLanguage": language})
        params, _end = installer_words.expression(call.group(1), 0, context)
        assert params == f'--remove-setup "{report}" --lang {language}'
        count = ctypes.c_int()
        shell32 = ctypes.WinDLL("shell32")
        shell32.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
        shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
        argv = shell32.CommandLineToArgvW(f'"C:\\app\\ClaudeUsageMonitor.exe" {params}', ctypes.byref(count))
        words = [argv[index] for index in range(1, count.value)]
        ctypes.WinDLL("kernel32").LocalFree(ctypes.cast(argv, ctypes.c_void_p))
        command = main.parse_args(words)
        assert (command.kind, command.path) == ("remove_setup", report), (language, words)


def test_the_names_stay_the_same_in_every_language():
    """The product, the publisher, the Start-menu folder and its shortcuts, and the name in Apps are names (#86): written
    in the script, never a message. The name in Apps is set (UninstallDisplayName): left out, Setup would build it from
    the language's NameAndVersion (its English is the same, so the record alone cannot tell)."""
    setup = installer_words.setup_values(ISS)
    assert setup.get("UninstallDisplayName") == "Claude Usage Monitor version {#AppVer}" and "AppVerName" not in setup
    for key in ("AppName", "AppPublisher", "DefaultGroupName"):
        assert "{cm:" not in setup[key], key
    for line in installer_words.section(ISS, "Icons"):
        assert "{cm:" not in installer_words.parameters(line)["Name"]


def test_inno_setups_own_english_is_the_one_recorded():
    """The record's task heading and name in Apps come from Inno Setup 6.7.1's Default.isl (INNO_DEFAULT_CUSTOM), and
    Setup's choice of language from its files' LanguageIDs (INNO_LANGUAGE_IDS): checked against the Inno Setup on this
    PC, where there is one (CI's runner, a dev PC)."""
    folder = installer_words.inno_folder()
    if folder is None:
        pytest.skip("Inno Setup is not installed")
    default = installer_words.read_isl((folder / "Default.isl").read_text(encoding="utf-8"))
    assert default["CustomMessages"] == installer_words.INNO_DEFAULT_CUSTOM
    for language, name in installer_words.INNO_FILES.items():
        options = installer_words.read_isl((folder / name.removeprefix("compiler:")).read_text(encoding="utf-8"))["LangOptions"]
        assert int(options["LanguageID"].lstrip("$"), 16) == installer_words.INNO_LANGUAGE_IDS[language], language


# ── The record's reader (step a1) ─────────────────────────────────────────────

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
