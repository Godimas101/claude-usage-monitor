# Releasing

A release is a `VERSION` bump that reaches `main`. Nothing else publishes anything, except running the release
workflow by hand (see below). Releases are the owner's call: each one posts to the public Discord thread, so agents
ask first and never run `gh release create` (see [`../AGENTS.md`](../AGENTS.md)).

## What happens when `VERSION` reaches `main`

`.github/workflows/release.yml` runs on every push to `main`, and by hand (`workflow_dispatch`, which builds whichever
branch you pick, while a new release tag goes on the default branch's latest commit: run it on `main` only):

1. It reads `VERSION`. If a GitHub **release** `v<VERSION>` already exists it stops, so an ordinary push does nothing.
   It looks for the release, not the tag: a leftover tag without a release does not stop it, and `gh release create`
   then attaches the new build to that tag's old commit. Delete such a tag first (`git push --delete origin vX.Y.Z`).
2. On a Windows runner it installs Python 3.14, `requirements.txt` and PyInstaller, held to the versions in
   `constraints-build.txt`, and runs `pyinstaller --noconfirm ClaudeUsageMonitor.spec`: a windowed onedir bundle,
   `dist\ClaudeUsageMonitor\`. It then starts the built exe with `--print-version` and stops unless it reports `VERSION`;
   a 30 second `--smoke-test` follows, which only warns while `continue-on-error` is on (it has not yet been run on a runner).
3. It installs Inno Setup (Chocolatey, any 6.3 or newer) and runs `ISCC /DAppVer=<VERSION> installer.iss`,
   which writes `installer\ClaudeUsageMonitorSetup-v<VERSION>.exe`.
4. It checks that file exists, then runs `gh release create v<VERSION>` with the installer as the only
   asset. The notes are the body of the `## [<VERSION>]` section of `CHANGELOG.md`, up to the next `## [`
   heading. If that section is missing or empty, GitHub's generated notes are used instead.

The Discord post is not part of the workflow: the TCS Discord bot mirrors every GitHub release to the tool-updates
thread, which users watch.

What the workflow does **not** do:

- It runs no tests. `tests.yml` runs them on every push, and the checklist below covers the frozen build beyond what the
  workflow checks (that the exe starts and reports its version, and the smoke test while it only warns).
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
  entry alone), and relaunches the app. If the helper that does this dies while the app is closing, the app
  opens the release page and starts itself again instead of quitting for nothing.

So attach the one `ClaudeUsageMonitorSetup-v<VERSION>.exe` that the workflow built, under that name, and do
not swap it by hand later. The updater reads `releases/latest`, which skips drafts and pre-releases, so a
pre-release is offered to nobody.

What the digest proves is that the file is the one GitHub lists for that release, not who published it. Anyone who can
edit the release can upload another installer, GitHub lists the new digest, and every installed copy runs it. The
installer is not code-signed. Two things would close the gap: turn on GitHub's **immutable releases** for the
repository, so a published asset cannot be replaced; and sign the installer and have `update_check.py` verify the
signature as well as the digest.

## Before you bump `VERSION`

- [ ] `python -m pytest -q` passes, and so does `CUM_UI_TESTS=1 python -m pytest -q` on your desktop (it adds the
      window and process tests, including the update helpers under the real `cmd.exe`, the installer script
      compiled by Inno Setup, and the signature check of the WebView2 download). `tests.yml` is green on the
      commit that will reach `main`.
- [ ] The sandboxed installer runs pass: `CUM_UI_TESTS=1 CUM_INSTALLER_E2E=1 python -m pytest -q tests/test_update_check.py -k sandboxed`.
      They install and uninstall a **renamed** copy of the installer (own AppId, folder, Run value and mutex; the test
      refuses to run if a real name is left in it) and check the Start with Windows entry for each updater and each
      starting state, and that an update with a file held open stops with the old install whole. Your own install is
      read before and after and must be unchanged. They need Inno Setup 6.
- [ ] `CHANGELOG.md` has the user-facing notes under `## [X.Y.Z] — YYYY-MM-DD` (the heading must start with
      `## [X.Y.Z]` or the notes are not found), and `[Unreleased]` is empty above it. `README.md` agrees.
- [ ] `build.bat` succeeds. It builds the exe and the installer from `VERSION`, checks that the frozen exe
      reports that version (`--print-version`) and prints the installer's SHA-256. It needs Inno Setup 6.3+
      and `python -m pip install -r requirements.txt pyinstaller`. Set `CUM_DISTPATH`, `CUM_WORKPATH` and
      `CUM_OUTDIR` to short folders if the checkout is deep (both tools fail past 260 characters). Its toolchain line
      (Python, PyInstaller, Pillow, pywebview) should match what the release workflow's last log shows; if it does
      not, build again with CI's versions before the next two items.
- [ ] The frozen smoke run is clean: with `CUM_DEV_INSTANCE=1`, `CUM_APPDATA_DIR=<scratch folder>` and
      `CLAUDE_CONFIG_DIR=<empty folder>` (so your own transcripts stay out of it) run
      `dist\ClaudeUsageMonitor\ClaudeUsageMonitor.exe --smoke-test 30 report.json`. Exit code 0 and `"ok": true`.
      Close any other dev instance first (`python main.py` with `CUM_DEV_INSTANCE=1`): there is one dev mutex,
      and while another holds it the report says `the app never started its loop`.
- [ ] On a spare Windows 11 user or a VM, **not over your own install**, run the new `ClaudeUsageMonitorSetup`:
  - the tray icon, widgets and dashboard window appear, and **Start with Windows** is ticked;
  - `reg query HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v ClaudeUsageMonitor` shows the quoted path
    of the exe and nothing else, and the monitor starts at the next sign-in;
  - running the installer again while the monitor runs makes Setup ask you to close it, and the dashboard
    window closes with it;
  - uninstalling removes that Run value and asks before deleting `%APPDATA%\ClaudeUsageMonitor` (No keeps it).
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
      becomes its Nostromo phosphor, and the position and widget toggles stay. Then run the installer once
      interactively over a v1 install with the entry off: the **Start with Windows** box on the tasks page is unticked.
      Last, with a file in `<install folder>\_internal` held open (in PowerShell,
      `$f = [IO.File]::Open('<install folder>\_internal\base_library.zip', 'Open', 'Read', 'ReadWrite')`), the same run
      stops with an error and the old install still starts; after `$f.Close()` it succeeds.
- [ ] A machine without WebView2 (Windows 11 always has it, so this needs a Windows 10 machine or VM): the Ready page
      offers the runtime; Yes downloads it, checks Microsoft's signature and installs it, No leaves the dashboard
      opening in the browser; a silent install offers nothing. This is also the only check of version 2 on Windows 10:
      until it has been done there, the README, CHANGELOG and installer comment must keep saying that version 2 is
      tested on Windows 11 only.
- [ ] Then bump `VERSION` in the change that goes to `main`. That change is the release.

## After the workflow finishes

- [ ] The release has one asset, with a `sha256:` digest, and the notes are the CHANGELOG section.
- [ ] `certutil -hashfile ClaudeUsageMonitorSetup-v<VERSION>.exe SHA256` of the downloaded asset equals that digest.

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
`%TEMP%\ClaudeUsageMonitor-setup.log`.

- [ ] The Discord post is up (the bot makes it).

## A bad release

Delete it (`gh release delete vX.Y.Z --cleanup-tag`), fix the cause and release the next version. The updater
only moves forward, so installs that already updated stay on the bad one until the next release.
