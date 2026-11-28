; Claude Usage Monitor: the installer's and the uninstaller's own messages, in English (#103).
;
; installer.iss shows them through {cm:Name} and CustomMessage('Name'); Inno Setup's own pages and buttons come from
; its own messages files. English is the source of truth: fr.isl, es.isl and tr.isl beside this file hold the same
; names in their language (#104 to #106). A name a language lacks shows this English (ISCC warns, and uses the first
; language that defines it: English, which comes first in [Languages]). tests/test_installer_words.py holds this
; English to what Setup said before #103 and every language's file to its shape.
;
; Rules for every language: %1 and %2 are filled in by the code; %n is a line break, kept where English has one; & marks
; the access key (one per message that has it). Names stay as written: Claude Usage Monitor, Claude Code, Microsoft Edge
; WebView2, microsoft.com, /mcp and the web address. "Setup" is this installer: use the word the language's own Inno
; Setup messages use for it. A box's buttons (Yes, No, Retry, Cancel) are not in this file: where a message names one,
; use the word the box shows on it in that language (to be checked on the review build).
; Plain UTF-8 without a byte order mark; NFC. Each message has its note on the lines above it.

[CustomMessages]

; ---- The wizard ----------------------------------------------------------------------------------------------------

; The Select Additional Tasks page: the box of the one task, under the group heading Startup: (Inno Setup's own
; AutoStartProgramGroupDescription). Checked on a first install. One line beside a check box.
StartupTask=&Start with Windows (start the monitor when I sign in)

; The last page (Finish): the box that starts the monitor when the page closes. One line beside a check box.
LaunchNow=Launch Claude Usage Monitor now

; ---- The WebView2 runtime (Windows 10 only; Windows 11 has it) ------------------------------------------------------

; A question on the Ready page, Yes or No, when the runtime the dashboard window uses is missing. Three paragraphs.
WebView2Offer=The dashboard window needs the Microsoft Edge WebView2 Runtime, and it is not installed on this PC.%n%nWithout it the dashboard opens in your web browser instead. Everything else works the same.%n%nDownload Microsoft's installer for it (about 2 MB) and run it after the installation?

; The download page's heading while the runtime's installer downloads. One line.
WebView2Downloading=Downloading the Microsoft Edge WebView2 Runtime

; The download page's line under its heading. One line.
WebView2DownloadingWhy=Setup is downloading the installer for the runtime that the dashboard window needs.

; A box (OK) when that download failed. Two paragraphs.
WebView2NotDownloaded=The WebView2 installer could not be downloaded, so the installation continues without it.%n%nThe dashboard will open in your web browser. You can install the WebView2 Runtime later from microsoft.com and it will be used automatically.

; The Installing page's status line while Windows checks the download's signature. One line; English ends with
; three dots.
WebView2Checking=Checking the Microsoft Edge WebView2 installer...

; A box (OK) when the download is not signed by Microsoft, so it was not run. Two paragraphs.
WebView2NotVerified=The WebView2 installer that was downloaded could not be verified as Microsoft's, so it was not run.%n%nClaude Usage Monitor is installed, and its dashboard will open in your web browser. You can install the WebView2 Runtime later from microsoft.com and it will be used automatically.

; The Installing page's status line while the runtime installs. One line; English ends with three dots.
WebView2Installing=Installing the Microsoft Edge WebView2 Runtime...

; A box (OK) when the runtime's installer ran but the runtime is still missing. One paragraph.
WebView2NotInstalled=The Microsoft Edge WebView2 Runtime could not be installed. Claude Usage Monitor is installed, and its dashboard will open in your web browser.

; ---- A monitor that will not close ----------------------------------------------------------------------------------

; A box (Retry or Cancel) in Setup: it asked the running monitor to quit and it did not. Two paragraphs. Exit is the
; monitor's own tray menu item (the app's tray.menu.exit in the same language); Retry is the box's button.
StillRunningSetup=Claude Usage Monitor is still running. It did not close when Setup asked it to.%n%nClose it from its icon near the clock (right-click it, then Exit), and click Retry.

; The same box in the uninstaller. Two paragraphs.
StillRunningUninstall=Claude Usage Monitor is still running. It did not close when Uninstall asked it to.%n%nClose it from its icon near the clock (right-click it, then Exit), and click Retry.

; The Preparing to Install page, when Cancel was chosen in that box: Setup stops. One paragraph.
StillRunningStopped=Claude Usage Monitor is still running, so Setup stopped without changing anything. Close the monitor, then run Setup again.

; ---- The Finish page: what Setup closed of Claude Code's -----------------------------------------------------------
; Shown after the page's own text when Setup had to end the monitor's tool (its MCP server) in open Claude Code
; sessions, or sessions waiting on the usage guard. The tools' sentence first, then a space and the waits' sentence. The
; same sentences as the app's toast.updated.tools and toast.updated.waits in the same language (a test holds them
; together). One form is for one, the other for 2 or more: %1 is the count, plain digits. Inno Setup has no plurals.

EndedToolsOne=Setup closed the monitor's tool in a Claude Code session. If that session shows an error, type /mcp there or restart it.

EndedToolsOther=Setup closed the monitor's tool in %1 Claude Code sessions. If one shows an error, type /mcp there or restart it.

EndedWaitsOne=A session waiting on the usage guard was let go early.

EndedWaitsOther=%1 sessions waiting on the usage guard were let go early.

; ---- An update that cannot go ahead ---------------------------------------------------------------------------------

; A box (OK): a file of the old install is held open, so Setup stops with nothing changed. %1 is the file's or folder's
; full path. Two paragraphs.
InUse=Claude Usage Monitor cannot be updated because %1 is in use. Nothing was changed.%n%nClose the program and run Setup again.

; A box (OK), rare: Setup renamed the old program and could not rename it back. %1 is the program's path, %2 the name
; it has now. Two paragraphs.
NotMovedBack=Setup moved %1 to %2, and could not move it back.%n%nPlease rename it back by hand and run Setup again.

; A box (OK), rare: Setup stopped half way and could not restore the version that was there. Two paragraphs.
NotPutBack=Setup did not finish, and could not put the old version back.%n%nRun Setup again to install Claude Usage Monitor.

; ---- The uninstaller ------------------------------------------------------------------------------------------------

; A question (Yes or No, No by default) at the end of the uninstall. %1 is the folder's full path, on a line of its own.
; No is the box's button. Three paragraphs.
DeleteUserData=Do you also want to delete your Claude Usage Monitor settings and usage history?%n%nThis removes the settings, the usage database and the logs in:%n%1%n%nChoose No to keep them, for example to reinstall later.

; The uninstall window's status line while the program takes its hooks and MCP tool out of Claude Code (it can take up
; to a minute and a half). One line; English ends with three dots.
TakingOutOfClaudeCode=Taking Claude Usage Monitor out of Claude Code...

; A box (OK): what the program reported it could not take out of Claude Code. %1 is that report, one line each, from
; the program in the same language; the uninstall goes on. The report's own words are not here.
LeftInClaudeCode=Some of this app is still in Claude Code:%n%n%1%nThe uninstall carries on.

; Stands in for that report when the program gave none (it never answered). Keep the web address as written.
MaybeLeftInClaudeCode=If you set up the usage guard, its hooks and MCP tool may still be there. https://github.com/Godimas101/claude-usage-monitor/blob/main/docs/AGENT_HOOK.md says how to remove them.
