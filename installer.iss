; Claude Usage Monitor installer (Inno Setup 6.3 or newer).
;
; Per-user install, no UAC. Build:  ISCC /DAppVer=2.0.0 installer.iss
;   AppVer   the version. build.bat and release.yml pass it from the VERSION file; without it the
;            VERSION file is read here.
;   DistDir  the PyInstaller onedir folder to package (default: dist\ClaudeUsageMonitor).
; The installer is written to installer\ClaudeUsageMonitorSetup-v<version>.exe (ISCC /O<dir> moves it).
;
; The in-app updater (update_check.py) runs this installer unattended:
;   Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /MERGETASKS="!startup"
; after the running app has exited. The /MERGETASKS part keeps an update from touching the "Start with
; Windows" entry (the app repairs a stale path itself at launch). v1.1.5's updater, which cannot be changed,
; runs the same line WITHOUT /MERGETASKS, so the entry is also decided in code (WriteStartupEntry), not only
; by the task. tests/test_update_check.py keeps this file and the updater in agreement.

#ifndef AppVer
  #define VersionFile FileOpen(SourcePath + "VERSION")
  #define AppVer FileRead(VersionFile)
  #expr FileClose(VersionFile)
#endif
#ifndef DistDir
  #define DistDir "dist\ClaudeUsageMonitor"
#endif

; The AppId is what Inno derives from AppName, and the v1 installs already carry it: their uninstall key
; is "Claude Usage Monitor_is1". Pinned here so a later change of the display name cannot orphan them.
#define AppIdValue "Claude Usage Monitor"
#define ExeName "ClaudeUsageMonitor.exe"

[Setup]
AppId={#AppIdValue}
AppName=Claude Usage Monitor
AppVersion={#AppVer}
AppPublisher=Godimas
AppPublisherURL=https://patreon.com/Godimas101
AppSupportURL=https://github.com/Godimas101/claude-usage-monitor
DefaultDirName={localappdata}\ClaudeUsageMonitor
DefaultGroupName=Claude Usage Monitor
DisableProgramGroupPage=yes
OutputDir=installer
OutputBaseFilename=ClaudeUsageMonitorSetup-v{#AppVer}
SetupIconFile=usage_monitor.ico
UninstallDisplayIcon={app}\{#ExeName}
Compression=lzma
SolidCompression=yes
PrivilegesRequired=lowest
WizardStyle=modern
; Setup runs on 64-bit Windows 10 and 11 (also ARM64 through emulation): the PyInstaller bundle is x64.
; Version 2 is built and tested on Windows 11 only (README, Requirements); this is the floor Setup
; enforces, not a promise of support.
MinVersion=10.0
ArchitecturesAllowed=x64compatible
; The monitor holds this mutex for as long as it runs (usage_monitor/app.py MUTEX_NAME). Setup and Uninstall
; stop with "please close it" while it exists. The dashboard window is a second process of the same exe that
; holds no mutex: Restart Manager (CloseApplications) finds it through the files it has open and closes it,
; and PrepareToInstall below asks it to close first. "force" also ends the windowless readers Claude Code
; starts from the same exe (--mcp lives as long as a Claude session; --hook and --gate briefly; --wait until
; the usage guard lets its session go, which can be days): they hold no state, and while one held the files a
; silent update stopped with exit code 5 and changed nothing. A --wait that is ended wakes its session without
; the "wait is over" line (docs/AGENT_HOOK.md says so). Nothing is restarted by Setup: a closed dashboard cannot
; come back without the port and token of a monitor, and the monitor is relaunched by the Finish page (or
; by the update helper after a silent update).
AppMutex=Global\ClaudeUsageMonitor
CloseApplications=force
CloseApplicationsFilter=*.exe,*.dll,*.pyd
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; Checked by default. On an update the user's current choice wins: CurPageChanged unchecks the box when the
; entry is off, and in a silent run the [Registry] entry's Check function decides (see "Start with Windows").
Name: "startup"; Description: "&Start with Windows (start the monitor when I sign in)"; GroupDescription: "Startup:"

[InstallDelete]
; A PyInstaller onedir bundle: the files under _internal change with every Python, Pillow or pywebview
; version, so an update starts from an empty folder. Stale DLLs of the previous build (v1 shipped Tcl/Tk)
; would otherwise stay behind for good. This wipe runs before any new file is copied, so CurStepChanged
; first proves that the folder can be replaced (RequireBundleFolderFree) and stops, untouched, when it cannot.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
; Onedir build: package the whole PyInstaller output folder (exe + _internal).
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Claude Usage Monitor"; Filename: "{app}\{#ExeName}"
Name: "{group}\Uninstall Claude Usage Monitor"; Filename: "{uninstallexe}"

[Registry]
; The same entry the app's own switch writes (usage_monitor/ui/startup.py): the quoted path of the frozen
; exe, nothing else. Removed on uninstall; CurUninstallStepChanged also removes the one the app wrote.
; Check: WriteStartupEntry keeps a silent update from switching the entry on for a user who had it off.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "ClaudeUsageMonitor"; ValueData: """{app}\{#ExeName}"""; Flags: uninsdeletevalue; Tasks: startup; Check: WriteStartupEntry

[UninstallDelete]
; Settings, the usage database and the logs live in %APPDATA%\ClaudeUsageMonitor, outside the install
; folder, and stay by default. CurUninstallStepChanged asks whether to delete them.

[Run]
Filename: "{app}\{#ExeName}"; Description: "Launch Claude Usage Monitor now"; Flags: nowait postinstall skipifsilent

[Code]
const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';
  RunValue = 'ClaudeUsageMonitor';
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#AppIdValue}_is1';
  DashboardTitle = 'Claude Usage Monitor';     // title of the dashboard window; the controller window's differs
  WmClose = $0010;
  // The Microsoft Edge WebView2 Runtime (Evergreen) registers its version, "pv", under this client id.
  WebView2Client = 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  // Microsoft's small Evergreen bootstrapper; it fetches the runtime itself.
  WebView2Url = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703';
  WebView2File = 'MicrosoftEdgeWebview2Setup.exe';
  // Run through Windows PowerShell before the bootstrapper is started: exit code 0 only for a valid Authenticode
  // signature made by Microsoft Corporation. %s is the file's path, put between single quotes (a quote inside the
  // path is doubled by the caller). Nothing here goes through ExpandConstant, so the braces stay as they are.
  WebView2SignatureCheck = '$s = Get-AuthenticodeSignature -LiteralPath ''%s''; if ($s.Status -eq ''Valid'' -and $s.SignerCertificate.Subject -like ''CN=Microsoft Corporation,*'') { exit 0 } else { exit 1 }';

var
  DownloadPage: TDownloadWizardPage;
  WebView2Asked: Boolean;
  WebView2Downloaded: Boolean;
  WebView2Cancelled: Boolean;
  StartupBoxSet: Boolean;

// ---- Start with Windows -------------------------------------------------------------------------

function StartupEntryExists: Boolean;
begin
  Result := RegValueExists(HKCU, RunKey, RunValue);
end;

function InstalledBefore: Boolean;
begin
  Result := RegKeyExists(HKCU, UninstallKey);
end;

// Check function of the [Registry] entry: is the "Start with Windows" entry written? A silent Setup over an
// existing install never asks, and the task box cannot carry the user's choice into it: Setup rebuilds the task
// list with its defaults after InitializeWizard, and v1.1.5's updater passes no /MERGETASKS. So the decision is
// made here. A silent update keeps what the user has now: an entry that exists is rewritten to this exe (v1 wrote
// the exe followed by a script path), one that does not exist stays missing. A first install, and an
// interactive Setup (where the box, preset by CurPageChanged, speaks for the user), write it when the task is on.
function WriteStartupEntry: Boolean;
begin
  if WizardSilent and InstalledBefore then
    Result := StartupEntryExists
  else
    Result := True;
  Log('"Start with Windows" entry: ' + IntToStr(Ord(Result)) + ' (1 = written, 0 = left as it is).');
end;

// ---- WebView2 ------------------------------------------------------------------------------------

function RealVersion(const Version: String): Boolean;
begin
  Result := (Version <> '') and (Version <> '0.0.0.0');
end;

function WebView2VersionIn(const RootKey: Integer): String;
begin
  if not RegQueryStringValue(RootKey, WebView2Client, 'pv', Result) then
    Result := '';
end;

// True when the WebView2 Runtime is installed for this PC or this user. A missing, empty or 0.0.0.0 "pv"
// means it is not (Microsoft's own detection rule). HKLM32 is HKLM\SOFTWARE\WOW6432Node on 64-bit Windows,
// where the runtime registers itself.
function WebView2Installed: Boolean;
begin
  Result := RealVersion(WebView2VersionIn(HKLM32)) or RealVersion(WebView2VersionIn(HKCU));
  if (not Result) and IsWin64 then
    Result := RealVersion(WebView2VersionIn(HKLM64));
end;

function DownloadWebView2: Boolean;
begin
  Result := False;
  DownloadPage.Clear;
  DownloadPage.Add(WebView2Url, WebView2File, '');   // Evergreen: the file changes with each Microsoft release, so no fixed hash
  DownloadPage.Show;
  try
    try
      DownloadPage.Download;
      Result := True;
    except
      WebView2Cancelled := DownloadPage.AbortedByUser;
      if WebView2Cancelled then
        Log('The WebView2 download was cancelled.')
      else
        Log('The WebView2 download failed: ' + GetExceptionMessage);
    end;
  finally
    DownloadPage.Hide;
  end;
end;

// Offered once, on the Ready page, never in a silent install, and never able to stop the installation:
// whatever happens here the monitor is installed, and without the runtime its dashboard opens in the browser.
procedure OfferWebView2;
begin
  if WebView2Asked or WizardSilent then
    Exit;
  WebView2Asked := True;
  if WebView2Installed then begin
    Log('The WebView2 Runtime is installed.');
    Exit;
  end;
  if SuppressibleMsgBox(
       'The dashboard window needs the Microsoft Edge WebView2 Runtime, and it is not installed on this PC.' + #13#10 + #13#10 +
       'Without it the dashboard opens in your web browser instead. Everything else works the same.' + #13#10 + #13#10 +
       'Download Microsoft''s installer for it (about 2 MB) and run it after the installation?',
       mbConfirmation, MB_YESNO or MB_DEFBUTTON1, IDNO) <> IDYES then begin
    Log('The user declined the WebView2 Runtime.');
    Exit;
  end;
  WebView2Downloaded := DownloadWebView2;
  if (not WebView2Downloaded) and (not WebView2Cancelled) then   // a download the user cancelled needs no apology
    SuppressibleMsgBox(
      'The WebView2 installer could not be downloaded, so the installation continues without it.' + #13#10 + #13#10 +
      'The dashboard will open in your web browser. You can install the WebView2 Runtime later from microsoft.com and it will be used automatically.',
      mbInformation, MB_OK, IDOK);
end;

// True when the downloaded bootstrapper has a valid Authenticode signature made by Microsoft Corporation. The
// download has no fixed hash (it is the Evergreen file), so this is what stands between a TLS-intercepting proxy
// or a bad mirror and a program that would otherwise run as the user. Windows PowerShell is Windows' own
// verifier; when it is missing, blocked or fails, the answer is no and the bootstrapper is not run.
function WebView2InstallerIsMicrosofts(const FileName: String): Boolean;
var
  Quoted, Params: String;
  Code: Integer;
begin
  Result := False;
  Quoted := FileName;
  StringChangeEx(Quoted, '''', '''''', True);        // a single quote inside the path, doubled for PowerShell
  Params := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "' + Format(WebView2SignatureCheck, [Quoted]) + '"';
  if Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Params, '', SW_HIDE, ewWaitUntilTerminated, Code) then begin
    Result := (Code = 0);
    Log(Format('The WebView2 installer signature check ended with exit code %d.', [Code]));
  end else
    Log('The WebView2 installer signature check could not be started: ' + SysErrorMessage(Code));
end;

// Runs Microsoft's bootstrapper once the files are in place, and only after its signature checks out. ShellExec
// lets Windows ask for administrator rights if the bootstrapper wants them; without them it installs for this
// user only.
procedure InstallWebView2;
var
  ResultCode: Integer;
  Bootstrapper: String;
begin
  Bootstrapper := ExpandConstant('{tmp}\' + WebView2File);
  WizardForm.StatusLabel.Caption := 'Checking the Microsoft Edge WebView2 installer...';
  WizardForm.FilenameLabel.Caption := '';
  if not WebView2InstallerIsMicrosofts(Bootstrapper) then begin
    Log('The WebView2 installer is not signed by Microsoft Corporation; it was not run.');
    SuppressibleMsgBox(
      'The WebView2 installer that was downloaded could not be verified as Microsoft''s, so it was not run.' + #13#10 + #13#10 +
      'Claude Usage Monitor is installed, and its dashboard will open in your web browser. You can install the WebView2 Runtime later from microsoft.com and it will be used automatically.',
      mbInformation, MB_OK, IDOK);
    Exit;
  end;
  WizardForm.StatusLabel.Caption := 'Installing the Microsoft Edge WebView2 Runtime...';
  if ShellExec('open', Bootstrapper, '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    Log(Format('The WebView2 installer finished with exit code %d.', [ResultCode]))
  else
    Log('The WebView2 installer could not be started: ' + SysErrorMessage(ResultCode));
  if not WebView2Installed then
    SuppressibleMsgBox(
      'The Microsoft Edge WebView2 Runtime could not be installed. Claude Usage Monitor is installed, and its dashboard will open in your web browser.',
      mbInformation, MB_OK, IDOK);
end;

// ---- The running dashboard ----------------------------------------------------------------------

// The dashboard is a separate process of the monitor's exe and holds no mutex. If one is open, ask it to close
// and give it a few seconds; Restart Manager (CloseApplications) deals with a window that stays.
procedure CloseDashboardWindow;
var
  Wnd: HWND;
  Tries: Integer;
begin
  Wnd := FindWindowByWindowName(DashboardTitle);
  if Wnd = 0 then
    Exit;
  Log('Asking the dashboard window to close.');
  PostMessage(Wnd, WmClose, 0, 0);
  for Tries := 1 to 40 do begin
    Sleep(100);
    if FindWindowByWindowName(DashboardTitle) = 0 then begin
      Log('The dashboard window closed.');
      Exit;
    end;
  end;
  Log('The dashboard window is still open.');
end;

// ---- Setup events -------------------------------------------------------------------------------

procedure InitializeWizard;
begin
  DownloadPage := CreateDownloadPage(
    'Downloading the Microsoft Edge WebView2 Runtime',
    'Setup is downloading the installer for the runtime that the dashboard window needs.', nil);
  DownloadPage.ShowBaseNameInsteadOfUrl := True;
end;

// First install: the "Start with Windows" box is checked. Update: it follows what the user has now, so an update
// does not switch the entry back on after it was turned off in the app. This cannot be done in InitializeWizard,
// because Setup rebuilds the task list with its defaults afterwards; it is done once, as the tasks page appears.
// It only ever unchecks the box: this event also fires in a silent run, where checking it would override
// /MERGETASKS="!startup". (WriteStartupEntry is what decides a silent run.)
procedure CurPageChanged(CurPageID: Integer);
begin
  if (CurPageID = wpSelectTasks) and (not StartupBoxSet) then begin
    StartupBoxSet := True;
    if InstalledBefore and (not StartupEntryExists) then begin
      Log('Updating: "Start with Windows" is off, so its box is unchecked.');
      WizardSelectTasks('!startup');
    end;
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  if CurPageID = wpReady then
    OfferWebView2;
  Result := True;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  CloseDashboardWindow;
  Result := '';
end;

// [InstallDelete] empties {app}\_internal before one new file is copied. If a file in it is still held open by a
// program Setup could not close (a virus scanner, a backup agent, an indexer), the delete stops half way and the
// copy never happens: no program is left, and v1.1.5's updater, which cannot be changed, then starts an exe that
// is gone. A folder with nothing open below it can be renamed, so prove that first (the rename changes nothing:
// it is undone at once) and stop with the old install untouched when it cannot be done. This is stricter than
// the delete itself, which can step round a file opened with delete sharing; the price is a clean, retryable stop.
procedure RequireBundleFolderFree;
var
  Folder, Probe: String;
  Tries: Integer;
  Moved, Back: Boolean;
begin
  Folder := ExpandConstant('{app}\_internal');
  Probe := Folder + '.probe';
  if not DirExists(Folder) then
    Exit;                                   // a first install: nothing to replace
  Moved := False;
  for Tries := 1 to 25 do begin             // about 5 seconds: a scanner lets go of a file after a moment
    if RenameFile(Folder, Probe) then begin
      Moved := True;
      Break;
    end;
    Sleep(200);
  end;
  if not Moved then begin
    Log('The bundle folder is in use; stopping before anything is changed.');
    SuppressibleMsgBox(
      'Claude Usage Monitor cannot be updated because a file in ' + Folder + ' is in use. Nothing was changed.' + #13#10 + #13#10 +
      'Close the program and run Setup again.',
      mbError, MB_OK, IDOK);
    Abort;
  end;
  Back := False;
  for Tries := 1 to 25 do begin
    if RenameFile(Probe, Folder) then begin
      Back := True;
      Break;
    end;
    Sleep(200);
  end;
  if not Back then begin
    Log('The bundle folder could not be renamed back from ' + Probe + '.');
    SuppressibleMsgBox(
      'Setup tested whether ' + Folder + ' can be replaced by renaming it to ' + Probe + ', and could not rename it back.' + #13#10 + #13#10 +
      'Please rename it back by hand and run Setup again.',
      mbError, MB_OK, IDOK);
    Abort;
  end;
  Log('The bundle folder is free to be replaced.');
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
    RequireBundleFolderFree;                // after Restart Manager has had its turn, before [InstallDelete]
  if (CurStep = ssPostInstall) and WebView2Downloaded then
    InstallWebView2;
end;

// ---- Uninstall events ---------------------------------------------------------------------------

// The entry may have been written by the app's own switch rather than by Setup, so Setup's uninsdeletevalue
// would miss it. It is removed when it starts with the quoted path of this install's exe: that is the entry
// v2 writes, and also the one v1 wrote (the exe followed by the path of a script), which v2 only rewrites
// the first time it runs. An entry that points somewhere else is not ours and is left alone.
procedure RemoveStartupEntry;
var
  Current, Ours: String;
begin
  if RegQueryStringValue(HKCU, RunKey, RunValue, Current) then begin
    Ours := '"' + ExpandConstant('{app}\{#ExeName}') + '"';
    if Pos(Lowercase(Ours), Lowercase(Current)) = 1 then begin
      RegDeleteValue(HKCU, RunKey, RunValue);
      Log('Removed the "Start with Windows" entry.');
    end else
      Log('The "Start with Windows" entry points somewhere else; left alone.');
  end;
end;

// Settings, the usage database and the logs are kept unless the user says otherwise. A silent
// uninstall never deletes them.
procedure AskToDeleteUserData;
var
  Folder: String;
begin
  if UninstallSilent then
    Exit;
  Folder := ExpandConstant('{userappdata}\ClaudeUsageMonitor');
  if not DirExists(Folder) then
    Exit;
  if not SameText(ExtractFileName(RemoveBackslash(Folder)), 'ClaudeUsageMonitor') then
    Exit;   // never delete a folder that is not ours, whatever the constant expanded to
  if MsgBox('Do you also want to delete your Claude Usage Monitor settings and usage history?' + #13#10 + #13#10 +
            'This removes the settings, the usage database and the logs in:' + #13#10 + Folder + #13#10 + #13#10 +
            'Choose No to keep them, for example to reinstall later.',
            mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then begin
    DelTree(Folder, True, True, True);
    Log('Deleted ' + Folder);
  end else
    Log('Kept ' + Folder);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    CloseDashboardWindow;      // the monitor itself is already gone: AppMutex made Uninstall wait for it
  if CurUninstallStep = usPostUninstall then begin
    RemoveStartupEntry;
    AskToDeleteUserData;
  end;
end;
