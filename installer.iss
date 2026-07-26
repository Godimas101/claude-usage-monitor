; Version is supplied by the build (CI + build.bat pass it from the VERSION
; file):  ISCC /DAppVer=1.2.3 installer.iss  — falls back to the default below.
#ifndef AppVer
  #define AppVer "1.1.0"
#endif

[Setup]
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
UninstallDisplayIcon={app}\ClaudeUsageMonitor.exe
Compression=lzma
SolidCompression=yes
PrivilegesRequired=lowest
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "dist\ClaudeUsageMonitor.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Claude Usage Monitor"; Filename: "{app}\ClaudeUsageMonitor.exe"
Name: "{group}\Uninstall Claude Usage Monitor"; Filename: "{uninstallexe}"

[UninstallDelete]
; Clean up settings on uninstall only if the user confirms — leave them by default
; (settings live in %APPDATA%\ClaudeUsageMonitor\ and are managed by the app itself)

[Run]
Filename: "{app}\ClaudeUsageMonitor.exe"; Description: "Launch Claude Usage Monitor now"; Flags: nowait postinstall skipifsilent
