# Releasing

A release is a `VERSION` bump that reaches `main`. Nothing else prepares one, except running the release workflow by
hand (see below). Releases are the owner's call: each one posts to the public Discord thread, so agents ask first and
never run `gh release create` (see [`../AGENTS.md`](../AGENTS.md)).

The workflow never makes a release live by itself. It leaves a **draft**, which is not the "latest release" the in-app
updater reads (`releases/latest` skips drafts), and which the Discord bot does not mirror until you publish it. The
first release is the one that matters most: v1.1.5's updater installs it silently on every v1 install and has no
second chance, so the pipeline is rehearsed first and the draft is checked before it goes live.

## What happens when `VERSION` reaches `main`

`.github/workflows/release.yml` runs on every push to `main`, and by hand (`workflow_dispatch`, with a **publish**
checkbox). Only one run goes at a time (a `concurrency` group), so two pushes cannot both decide that a version is new.
Every step has a timeout.

- **Push to `main`, or Run workflow with publish ticked:** the pipeline below, ending in a draft release.
- **Run workflow with publish left off (the default): a rehearsal.** The same pipeline from whichever branch you pick,
  for whatever `VERSION` that branch holds, even one that is already released. It skips the "already exists" check,
  creates no release and no tag, and keeps the installer as a downloadable artifact of the run
  (`ClaudeUsageMonitorSetup-v<VERSION>`). Rehearse from a branch that has the release's `VERSION` and `CHANGELOG.md`
  section before the bump goes to `main`.

1. It reads `VERSION`. If a GitHub **release** `v<VERSION>` already exists it stops (a draft counts), so an ordinary push
   does nothing. It looks for the release, not the tag: a leftover tag without a release does not stop it, and
   `gh release create` then attaches the new build to that tag's old commit. Delete such a tag first
   (`git push --delete origin vX.Y.Z`). A rehearsal skips this check.
2. On a Windows runner it installs Python 3.14, `requirements.txt` and PyInstaller, held to the versions in
   `constraints-build.txt`, and Inno Setup 6.7.1 exactly (`choco install innosetup --version=6.7.1 --allow-downgrade`):
   its own pages and messages are part of what the installer shows, and `installer.iss` refuses any other version
   (#103; moving to another is #119). It runs the test suite (`python -m pytest`, then the node-based dashboard script
   tests and the installer script's compile tests, the release build and the review build, as `tests.yml` does); a
   failing test stops the release.
3. It runs `pyinstaller --noconfirm ClaudeUsageMonitor.spec`: a windowed onedir bundle, `dist\ClaudeUsageMonitor\`. It
   then starts the built exe with `--print-version` and stops unless it reports `VERSION`, and runs a 45 second
   `--smoke-test` of the exe, which **fails the release** when it reports a problem (the dashboard's page must start,
   not only be fetched). The report and the logs are kept as the `smoke-report` artifact whatever happens.
4. It runs `ISCC /DAppVer=<VERSION> installer.iss`, which writes `installer\ClaudeUsageMonitorSetup-v<VERSION>.exe`,
   and stops unless ISCC's `Compiler engine version:` line names Inno Setup 6.7.1. The run summary shows its
   SHA-256.
5. It checks that file exists, then runs `gh release create v<VERSION> --draft --target <the built commit>` with the
   installer as the only asset. The notes are the body of the `## [<VERSION>]` section of `CHANGELOG.md`, up to the next
   `## [` heading. If that section is missing or empty, GitHub's generated notes are used instead. The tag is created
   when you publish the draft, on the commit that was built, not on whatever `main` holds by then.

The Discord post is not part of the workflow: the TCS Discord bot mirrors every GitHub release to the tool-updates
thread, which users watch.

What the workflow does **not** do:

- It does not publish. Publishing the draft is a deliberate click or `gh release edit vX.Y.Z --draft=false`, after the
  checks under "Before you publish the draft".
- It does not run the checks that need a person or another machine: the installer on a spare user, the upgrade from
  v1.1.5, Windows 10 (the checklists below).
- It pins Pillow, pywebview, PyInstaller and pythonnet to `constraints-build.txt` and uses Python 3.14, which is what the
  checklist's smoke run uses; their own dependencies still float. When you change the pins, run `build.bat` and the smoke
  run below first. `build.bat` prints the toolchain it used; the workflow's log shows CI's.

## The asset digest the updater verifies

When an asset is uploaded, GitHub computes its SHA-256 and lists it in the releases API as
`"digest": "sha256:<hex>"`; the release page shows it beside the file. The in-app updater
(`update_check.py`) depends on it:

- It takes an installer only from `https://github.com/Godimas101/claude-usage-monitor/releases/download/<tag>/<name>.exe`.
- It hashes the download and compares it with the digest the API lists for that asset. A mismatch deletes the
  file and opens the release page. A release whose asset lists no digest is never run silently: the release
  page opens too.
- Then it waits for the monitor to exit and runs the installer
  `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /MERGETASKS="!startup"` (an update leaves the Start with Windows
  entry alone) and `/LANG=<the app's language>` (Setup and the uninstaller it leaves speak the language chosen in
  the app, #103; a build that lacks that language passes over it), and relaunches the app. If the helper that does
  this dies while the app is closing, the app opens the release page and starts itself again instead of quitting
  for nothing.

So attach the one `ClaudeUsageMonitorSetup-v<VERSION>.exe` that the workflow built, under that name, and do
not swap it by hand later. The updater reads `releases/latest`, which skips drafts and pre-releases, so a
pre-release is offered to nobody.

What the digest proves is that the file is the one GitHub lists for that release, not who published it. GitHub's
**immutable releases** are on for this repository (since v2.3.0), so once a release is published nobody can replace,
add or delete its assets, or move its tag. Its title and notes can still be edited. A draft is not protected, which is
why the asset goes on the draft and is checked there (see below). The installer is not code-signed. Signing it, and
having `update_check.py` verify the signature as well as the digest, would also prove who built it.

## Before you bump `VERSION`

- [ ] `python -m pytest -q` passes, and so does `USAGE_MONITOR_UI_TESTS=1 python -m pytest -q` on your desktop (it adds the
      window and process tests, including the update helpers under the real `cmd.exe`, the installer script
      compiled by Inno Setup 6.7.1 as the release build and as the review build, and the signature check of the
      WebView2 download). `tests.yml` is green on the commit that will reach `main`.
- [ ] The sandboxed installer runs pass: `USAGE_MONITOR_UI_TESTS=1 USAGE_MONITOR_INSTALLER_E2E=1 python -m pytest -q tests/test_update_check.py -k sandboxed`.
      They install and uninstall a **renamed** copy of the installer (own AppId, folder, Run value and mutex; the test
      refuses to run if a real name is left in it) and check the Start with Windows entry for each updater and each
      starting state, and that an update with a file held open stops with the old install whole. Your own install is
      read before and after and must be unchanged. They need Inno Setup 6.7.1; the stand-in tests also need the
      .NET Framework's `csc.exe`. The language runs (#103) build the review build (ISCC `/DAllLanguages`) and install it
      with `/LANG=en`, `fr`, `es` and `tr`: the language recorded, the name in Apps, Setup's box and Preparing page for
      a monitor that stays, the uninstaller's `--lang` and its status line, each in that language (English where its
      draft lacks a message); an update with and without `/LANG` (never the last install's language); and the release
      build passing over a language it lacks.
- [ ] Every frame `CHANGED` in `tests/test_frame_golden.py` lists is a change this release means to make. That test
      checks every widget frame against the last release's, pixel for pixel, and `CHANGED` names the ones that differ
      on purpose (#32). Compare any you doubt with `python main.py --dump-frames DIR` here and at the last tag.
- [ ] `CHANGELOG.md` has the user-facing notes under `## [X.Y.Z] — YYYY-MM-DD` (the heading must start with
      `## [X.Y.Z]` or the notes are not found), and `[Unreleased]` is empty above it. `README.md` agrees.
- [ ] `build.bat` succeeds. It builds the exe and the installer from `VERSION`, checks that the frozen exe
      reports that version (`--print-version`) and prints the installer's SHA-256. It needs Inno Setup 6.7.1
      exactly (`installer.iss` refuses any other; https://github.com/jrsoftware/issrc/releases/tag/is-6_7_1) and
      `python -m pip install -r requirements.txt pyinstaller`. Set `USAGE_MONITOR_DISTPATH`, `USAGE_MONITOR_WORKPATH` and
      `USAGE_MONITOR_OUTDIR` to short folders if the checkout is deep (both tools fail past 260 characters). Its toolchain line
      (Python, PyInstaller, Pillow, pywebview) should match what the release workflow's last log shows; if it does
      not, build again with CI's versions before the next two items.
- [ ] **The pipeline has run on a runner.** Run the workflow by hand with publish left off (a rehearsal), from a
      branch that holds the release's `VERSION` and `CHANGELOG.md` section. It is green, and the `smoke-report` artifact
      says `"ok": true` with `page_booted` true in the dashboard step. The first run of the smoke step on a hosted runner
      is the one that can fail for a reason of that machine: read its report before blaming the build. The workflow
      reads the WebView2 runtime's files before the smoke test. On a fresh runner they are cold, and a cold runtime
      started the dashboard in up to 32 s (image win25-vs2026/20261030), past the smoke test's 15 s.
- [ ] The frozen smoke run is clean on your own machine too: with `USAGE_MONITOR_DEV_INSTANCE=1`, `USAGE_MONITOR_APPDATA_DIR=<scratch folder>` and
      `CLAUDE_CONFIG_DIR=<empty folder>` (so your own transcripts stay out of it) run
      `dist\ClaudeUsageMonitor\ClaudeUsageMonitor.exe --smoke-test 45 report.json`. Exit code 0 and `"ok": true`.
      Close any other dev instance first (`python main.py` with `USAGE_MONITOR_DEV_INSTANCE=1`): there is one dev mutex,
      and while another holds it the report says `the app never started its loop`. The run fails when the strip floats
      over a taskbar that could hold it, so its windows step says `"strip_in_taskbar": true` on a bottom taskbar.
- [ ] **The strip in the taskbar, by eye.** With the new build running and the strip on, open Start, then Search, then
      the tray's hidden icons (^). The strip stays in sight and does not blink. Hover a taskbar button until its preview
      shows: the strip stays too (#29). The smoke run cannot see that Windows lifts the taskbar over it.
- [ ] On a spare Windows 11 user or a VM, **not over your own install**, run the new `ClaudeUsageMonitorSetup`:
  - the tray icon, widgets and dashboard window appear, and **Start with Windows** is ticked;
  - `reg query HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v ClaudeUsageMonitor` shows the quoted path
    of the exe and nothing else, and the monitor starts at the next sign-in;
  - running the installer again while the monitor runs closes the monitor without asking (#53), the dashboard
    window closes with it, and the last page starts the monitor again;
  - uninstalling removes that Run value and Windows' startup record for it, takes the hooks and the MCP tool out of
    Claude Code, and asks before deleting `%APPDATA%\ClaudeUsageMonitor` (No keeps it).
- [ ] **The installer in each language (#103), on the same spare user or VM, never on your own profile.** After
      `build.bat`, build the review build from the same bundle (another folder if you set `USAGE_MONITOR_DISTPATH`;
      ISCC is `C:\Program Files (x86)\Inno Setup 6\ISCC.exe`). It is never published, and
      nothing else builds it:

      ISCC /DAllLanguages /DAppVer=<VERSION> "/DDistDir=dist\ClaudeUsageMonitor" "/O<scratch folder>" installer.iss

      ISCC warns once for each message a language's file lacks, and English stands in for it
      (`python tests/installer_words.py --todo` lists them). Then, with that Setup:
  - set the user's Windows display language to French (Settings → Time & language → Language & region), sign out
    and in, and run it with no `/LANG`: no language dialog, Inno Setup's own pages in French, the tasks page's heading
    `Démarrage :` above the Start with Windows box. Run it once more over that install while a Claude Code session
    uses the monitor's tool, so the last page says what Setup closed. Uninstall: its confirmation and Inno Setup's
    own words in French. Our own texts (the boxes, the status lines, the last page's sentences, the delete question)
    stay English until French's draft (#104) lands; nothing may be cut off. Apps lists `Claude Usage Monitor version
    <VERSION>`, as in English;
  - the same with a display language Setup does not have (German): everything in English;
  - without a French display pack: the same Setup with `/LANG=fr`, then `/LANG=es` and `/LANG=tr`. Note the words on
    a box's buttons: a message that names a button must use the word the box shows (`installer-messages/en.isl`'s
    header);
  - screenshots on #103 for the first round, and on #104 to #106 as each draft lands.
- [ ] **With Claude Code open.** In a Claude Code session that started after the monitor's MCP tool was registered,
      `/mcp` shows `claude-usage` as connected. With the monitor running, run the new installer by hand over that install
      (your own install is fine for this one). It closes the monitor without asking, goes through without an "in use"
      stop, and its last page says it closed the monitor's tool in a Claude Code session. In that session the tool
      answers again on its next use, or after `/mcp`. The sandboxed runs (`USAGE_MONITOR_UI_TESTS=1` and `USAGE_MONITOR_INSTALLER_E2E=1`;
      the stand-in tests also need the .NET Framework's `csc.exe`) cover the same with stand-in readers, hooks that
      keep starting the exe all through an update, and the uninstall.
- [ ] **Upgrade from v1, the way v1.1.5's updater does it.** That updater cannot be changed and is the one every v1
      user will run, so this is the check that matters most. On the same spare user or VM, install v1.1.5 and make the
      state you want to test: **on** is what v1 wrote (the exe followed by the path of a script, see
      `reg query HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v ClaudeUsageMonitor`); **off** is
      `reg delete HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v ClaudeUsageMonitor`. Then run the new installer
      from a `.cmd` that holds v1's helper lines (no `/MERGETASKS`, no log):

      ping 127.0.0.1 -n 4 >nul
      "<folder>\ClaudeUsageMonitorSetup-vX.Y.Z.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
      ping 127.0.0.1 -n 8 >nul
      start "" "%LOCALAPPDATA%\ClaudeUsageMonitor\ClaudeUsageMonitor.exe"

      Do it once with the entry **off** and once with it **on**. Pass: nothing prompts; the app is back within about
      15 seconds; `DisplayVersion` is the new version under `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\Claude Usage Monitor_is1`;
      and the Run value is still **missing** (it was off), or is the quoted exe and nothing else (it was on). The theme
      becomes its Nostromo phosphor (v1's Cyan and Orange, retired in 2.2, become Cryo and Ray Tube), and the
      position and widget toggles stay. Then run the installer once
      interactively over a v1 install with the entry off: the **Start with Windows** box on the tasks page is unticked.
      Last, with a file in `<install folder>\_internal` held open (in PowerShell,
      `$f = [IO.File]::Open('<install folder>\_internal\base_library.zip', 'Open', 'Read', 'ReadWrite')`), the same run
      stops with an error and the old install still starts; after `$f.Close()` it succeeds.
- [ ] A machine without WebView2 (Windows 11 always has it, so this needs a Windows 10 machine or VM): the Ready page
      offers the runtime; Yes downloads it, checks Microsoft's signature and installs it, No leaves the dashboard
      opening in the browser; a silent install offers nothing. This is also the only check of version 2 on Windows 10:
      until it has been done there, the README, CHANGELOG and installer comment must keep saying that version 2 is
      tested on Windows 11 only.
- [ ] Then bump `VERSION` in the change that goes to `main`. That change starts the release: the workflow builds it and
      leaves a draft.

## Before you publish the draft

The draft holds the installer CI built, which is the file every v1 install will be handed. The checks above ran on your
own build; these run on that one.

- [ ] The draft has one asset, with a `sha256:` digest, and the notes are the CHANGELOG section.
- [ ] `certutil -hashfile ClaudeUsageMonitorSetup-v<VERSION>.exe SHA256` of the downloaded asset equals that digest, and
      the sha256 in the run summary.
- [ ] The v1.1.5 upgrade check above, once with the Start with Windows entry off and once on, with **this downloaded
      installer** and not your own build. This is the only check on the file that will actually be offered.
- [ ] Then publish it: `gh release edit v<VERSION> --draft=false` (or the Publish button). The tag is created on the
      built commit and the Discord bot posts. Delete a draft that fails a check instead (`gh release delete v<VERSION>`);
      nothing was offered to anyone.

## After you publish

- [ ] `releases/latest` is the new release (`gh release view --json tagName`), with its one asset and `sha256:` digest.
- [ ] The golden frames are this release's. On the next release's branch, record the new tag's frames (the three
      commands at the top of `tests/frame_digests.py`: a worktree of the tag, the record, then remove the worktree),
      empty `CHANGED` in `tests/test_frame_golden.py`, and commit both. Until then that branch is checked against this
      release's predecessor.

On a machine with the previous version, update through the app. What you look for depends on which version that is.

**The first release, where the previous version is v1.1.5:** its tray menu shows **⬆ Update to vX** and its options
panel has an **UPDATES** section with a **⬆ UPDATE TO vX & RESTART** button. That updater checks no digest, writes no
log and passes no `/LOG` to Setup, so the evidence is the result: the new version is back within about half a
minute, `DisplayVersion` is the new version (see above), the Run value is what it was, and nothing is left in
`%TEMP%` (v1 deletes its installer and helper). There is no second chance for this hop: a v1 user whose update fails
has to download the installer by hand.

**Later releases, where the previous version is 2.x:** Settings → System → **Install update**. The monitor exits and the
new version is back within about half a minute. `monitor.log` has `update verified: ... sha256 ...`,
`%TEMP%\ClaudeUsageMonitor-update.log` ends with `installer exit code 0`, and Setup's own log is
`%TEMP%\ClaudeUsageMonitor-setup.log`. From an updater that passes the language (a release after 2.3.0), that
log's `Setup command line:` line holds `/LANG=` and the language the app showed; 2.3.0's and older updaters pass
none.

- [ ] The Discord post is up (the bot makes it).

## A bad release

Delete it (`gh release delete vX.Y.Z --cleanup-tag`), fix the cause and release the next version. A published release
is immutable, so its installer cannot be swapped for a fixed one, and its tag name can never be used again, even after
the delete. The updater only moves forward, so installs that already updated stay on the bad one until the next release.
