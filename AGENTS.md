# AGENTS.md — claude-usage-monitor

> Parent: [`../../AGENTS.md`](../../AGENTS.md) — universal Golden Rules.

## What this is

Claude Usage Monitor v2: a Windows 11 tray utility (Python) that shows Claude Code plan usage. A
floating widget, a taskbar strip, a tray icon and a dashboard window, fed by the usage API
(`/api/oauth/usage`) and Claude Code's local transcripts. Standalone repo under Chris's personal
projects. **Chris uses it constantly** — do not break the installed copy, and test a change in the
real app before calling it done.

Privacy, everywhere: never print, store or log message text, prompts, file contents or OAuth tokens.

## Layout

| Path | What |
|---|---|
| `main.py` | Entry point: the monitor, `--dashboard`, `--print-version`, `--dump-frames`, `--smoke-test` |
| `usage_monitor/app.py` | `MonitorApp`: wires settings, data service, UI host, widgets, tray, local API |
| `usage_monitor/data/` | Data layer, no UI imports: transcripts, SQLite store, usage API, windows, `DataService` |
| `usage_monitor/ui/` | Win32 layer (ctypes, no Tk): host, layered windows, widgets, tray, menu, server, dashboard |
| `usage_monitor/render/` | Pillow renderers: view model, drawing engine, one skin per theme |
| `usage_monitor/themes/` | Theme tokens (`<id>.json`) and their loader |
| `usage_monitor/webui/` | Dashboard HTML/CSS/JS, the motion layer (`motion.js`, `motion.css`, `themes/<id>.motion.css`) and the bundled OFL fonts (the Pillow skins read them too) |
| `update_check.py`, `version.py` | GitHub release check and self-update; version read from `VERSION` |
| `tests/` | pytest, offline, synthetic fixtures only |

Read before changing anything: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (data layer rules and
contracts) and [`docs/UI_ARCHITECTURE.md`](docs/UI_ARCHITECTURE.md) (threads, windows, interfaces,
local API). Update them in the same change when behaviour moves.

## Stack decision (#15)

Hybrid, no Tk: **Pillow-drawn widgets on per-pixel-alpha layered windows** (floating widget, taskbar
strip, tooltip, tray icon) plus a **pywebview dashboard** (WebView2) served by a local API. Python
3.10+, standard library + Pillow + pywebview only.

- The UI thread (main) owns every window. Other threads call `UIHost.post()`; the data service
  publishes `MonitorState` on its own thread; the dashboard is a separate `--dashboard` process.
- All Win32 goes through ctypes with explicit `argtypes`/`restype` on every function (use
  `usage_monitor/ui/win32.py`). Keep callbacks referenced and catch every exception inside one.
- Log with `usage_monitor.log.get_logger(__name__)`. No hidden globals: paths, clock and HTTP are
  injectable. The monitor never runs `claude` and never sends a model request.

## Themes

Tokens live in `usage_monitor/themes/<id>.json` and feed both the Pillow skins and the dashboard CSS.
Settings: `theme_id`, `theme_variant`, `theme_mode`, `reduce_motion`. Theme changes apply live.

**Motion** is part of each theme: the dashboard's page entrances, placeholders and boots (`webui/motion.js` plus the
theme's `.motion.css` and script, see `docs/UI_ARCHITECTURE.md` "Motion") and the desktop widgets' hover response
(`hover_frames` in the skins). Everything that moves obeys `reduce_motion`, `prefers-reduced-motion` and, for the widgets,
Windows' own animation switch. A new effect that does not is a bug.

- **Nostromo** (default): amber phosphor on CRT black, the shipped identity — *preserve it*. Variants
  amber (default), green, white, red, blue, cyan, orange, purple. The only theme whose widgets blink
  (the alive mark). Every theme has a skin of its own in `usage_monitor/render/skins/`.
- **Night City**: Blackwall (default), Relic, Edgerun, Mox; chamfered frames, RAM-meter tray icon.
- **Gates**: Fluent surfaces; accents (system by default) in light, dark or system mode; Windows
  fonts, never bundled.

## Branches and releases

- `main` is what ships. v2 is built on the `v2` branch. Never push to `main` without Chris's say-so.
- **Releases are Chris's call.** `release.yml` runs on every push to `main`, but only builds and
  publishes a public GitHub Release (and a Discord post) when `VERSION` names a tag that does not
  exist yet. A `VERSION` bump that reaches `main` *is* a release.
- Add the change to `CHANGELOG.md` under `[Unreleased]`. When Chris releases, that section becomes
  `## [VERSION]`, and `release.yml` publishes it as the release notes.
- Never bump `VERSION` yourself, and never run `gh release create`. Ask first (see Publishing below).

## Running a dev instance

A test build must run beside the installed copy without touching it:

```
set CUM_DEV_INSTANCE=1
set CUM_APPDATA_DIR=C:\some\scratch\folder
python main.py            (console: Ctrl+C quits, errors print)     or    launch.bat   (pythonw)
```

- `CUM_DEV_INSTANCE=1`: mutex `Global\ClaudeUsageMonitor-dev`, its own controller window class, tray
  tooltip ends "(dev)". A dev instance never reads, writes or repairs the HKCU Run entry; its *Start
  with Windows* switch lives in memory. Only the frozen installed build repairs the entry at launch.
  `--smoke-test` always runs as a dev instance.
- `CUM_APPDATA_DIR`: settings, `usage.db` and logs go there instead of `%APPDATA%\ClaudeUsageMonitor`.
- Logs: `monitor.log` (dashboard child: `dashboard.log`, hard crashes: `faults.log`).
- `python main.py --smoke-test 45 report.json` runs the real app for 45 s (real server, dashboard,
  widgets; the data service never pauses) and exits 0 when clean. The first run backfills your
  transcripts into the dev database.
- `python main.py --dump-frames <dir>` renders every theme, variant, state and scale to PNGs plus a
  contact sheet per theme. Screen capture can be blocked, so check visuals through these.

## Tests

- `python -m pytest` — offline and fast; what CI (`tests.yml`, Windows, Python 3.12) runs.
- `CUM_UI_TESTS=1 python -m pytest` — adds the tests that create real windows, tray icons or
  processes. They need an interactive desktop, park widgets off-screen and use a fake data service.
  A new test that opens a window, a tray icon or a process must be gated the same way.
- The dashboard scripts (`app.js`, `charts.js`, `motion.js`) are tested by running them under node:
  `tests/webui_checks.js` loads `charts.js` and `app.js` with a fake DOM and the test hook at the end of `app.js`, and
  `tests/test_webui.py` asserts on what they return; `tests/test_motion.py` runs `motion.js` on its own fake DOM and
  checks the motion stylesheets, and runs each theme script with no `motion.js` (`tests/theme_scripts_checks.js`). They
  start node, so they are gated with `CUM_UI_TESTS=1`; `tests.yml` runs both files once more with it set, so CI covers them.
  A check that reads a script's source for a phrase proves the phrase is there, not that the script works: where the claim
  is behaviour, run the script.
- **No test opens a console window.** A console program started by a process that has no console (pytest run from an
  editor, a task runner or an agent) gets a visible terminal window of its own. A test that starts `git`, `node`,
  `python`, `cmd` or anything else passes `creationflags=subprocess.CREATE_NO_WINDOW`; `os.system` and `os.popen` are
  never used. Two layers keep it so. At run time, `tests/conftest.py` installs `tests/console_guard.py` for the whole
  session: every `subprocess.Popen` that is started without the flag is refused, and the test that was running fails even if
  the code under test swallowed the error (this covers the code a test calls, not only the test's own lines). In the source,
  `test_no_test_starts_a_console_program_without_the_flag` parses every `tests/*.py` and resolves aliases
  (`import subprocess as sp`, `from subprocess import run as go`, `asyncio.create_subprocess_exec`) to find a start that
  forgets it. To check a run by eye, sample the top-level windows during it: on Windows Terminal the class to count is
  `CASCADIA_HOSTING_WINDOW_CLASS` as well as `ConsoleWindowClass`.

## DELETE ME/ convention

Files that the rewrite replaces (the v1 Tk modules `widget.py`, `options.py`, `theme.py`, `tray.py`,
`usage_reader.py`, `win_utils.py`) are moved into `DELETE ME/` for Chris to review and delete. The
folder is gitignored and never committed. Do not delete anything yourself: list it, move it there.

## README + UI style

- **General README style:** [`../../personal-docs/style-guides/README_STYLE_GUIDE.md`](../../personal-docs/style-guides/README_STYLE_GUIDE.md)
- **App-specific style:** [`../../personal-docs/style-guides/CLAUDE_USAGE_STYLE_GUIDE.md`](../../personal-docs/style-guides/CLAUDE_USAGE_STYLE_GUIDE.md)

## Local-only docs

Keep these local (not committed):
- `CLAUDE.md` (delegating stub) + `AGENTS.md` (this file, if the parent
  gitignore hasn't caught it — check)
- `NOTES.md` / `*NOTES*.md`

## Release policy

Compiled binaries and installers ship through **GitHub Releases**, not tracked
in the repo root.

## Publishing + feedback flow

Wired into the [TCS Discord bot](https://github.com/The-Canadian-Space/tcs-forum-watcher) with tag `Claude Monitor` under the `tools` category. Full flow: [`discord/release-and-feedback-flow`](https://docs.thecanadian.space/discord/release-and-feedback-flow/) (wiki, Cloudflare-Access-gated).

- Issues labelled `bug` or `suggestion` → `🐛-tool-feedback` thread (auto). Other labels don't mirror.
- GitHub releases → persistent thread in `📦-tool-updates`. **Public — users watch this thread for new versions.**
- **Releases are user-driven.** Every release fires a public Discord post. Do NOT run `gh release create` autonomously. If the work looks releasable (new feature, notable fix, packaged binary re-cut), **ask** first with proposed release notes.

## Related

- Parent: [`../../AGENTS.md`](../../AGENTS.md)
- Style guides: [`../../personal-docs/style-guides/`](../../personal-docs/style-guides/)
