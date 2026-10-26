# Changelog

All notable changes to Claude Usage Monitor. Format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]


## [2.1.0] — 2026-10-24

Claude Usage Monitor 2.1 lets your Claude Code sessions see how much of your plan is left, so long runs can pace themselves. It is also the first update that v2 installs on its own.

### Highlights
- **Claude can see your plan:** a new MCP tool, `get_plan_usage`, gives Claude Code the session, weekly and Fable limits with their reset times, pace and projected time to the limit, so long runs can pace themselves.
- **Hooks for Claude Code:** `--hook` adds a one-line usage summary to Claude's context at session start and when a limit passes a threshold. `--gate` refuses new agents and workflows once a limit reaches a cap you set.
- **Awake when it matters:** while a Claude session keeps asking, the monitor keeps polling on a locked screen, which is when unattended runs happen.

### Added
- `state.json` in the app data folder holds the latest reading: limits, pace, burn rate and plan, never the token. The app rewrites it only when the reading changes ([#24](https://github.com/Godimas101/claude-usage-monitor/issues/24)).
- `ClaudeUsageMonitor.exe --status [--json]`, `--hook`, `--gate` and `--mcp` run beside the app. With the app closed, `--status`, the MCP tool and the gate ask for the usage once and share the answer for two minutes. Setup and fields: [docs/AGENT_HOOK.md](https://github.com/Godimas101/claude-usage-monitor/blob/main/docs/AGENT_HOOK.md) ([#24](https://github.com/Godimas101/claude-usage-monitor/issues/24)).

### Changed
- The installer now also closes the monitor's windowless helpers, such as the MCP server a Claude session keeps open, before it replaces the files. A silent update no longer stops while one runs ([#24](https://github.com/Godimas101/claude-usage-monitor/issues/24)).
- While a Claude session has asked for the usage in the last hour, the monitor keeps polling on a locked screen ([#24](https://github.com/Godimas101/claude-usage-monitor/issues/24)).

## [2.0.0] — 2026-10-24

Claude Usage Monitor 2 is a rewrite: new widgets, a real dashboard, three themes and a data layer that counts honestly. Install it over v1 and your settings carry over. There was no separate v1.2 release, so the fixes planned for it (correct counting, current prices, local-time windows, a sturdier data layer) ship here, under **Fixed**.

### Highlights
- **Three themes:** Nostromo (the amber look you know), Night City and Gates, each with colour variants and motion of its own.
- **A real dashboard:** Overview, Limits, Cost & value, Efficiency, History, Settings and About.
- **Honest numbers:** each request counted once, current prices for every Claude model back to Claude 3, and days and months on your local clock.
- **What your plan is worth:** API-equivalent cost by model, folder and session, and how many times over your plan has paid for itself.
- **Efficiency and history:** cache savings, thinking share and tool use, a year of use as a calendar, streaks, and CSV or JSON export.
- **Alerts** at 80, 90 and 100% (or your own levels), when a limit resets, and quiet hours.
- **No hidden prompts:** the monitor never runs `claude` and never sends a model request.
- **Update notices,** and updates that are checked against GitHub's SHA-256 checksum before they install.

### Added
- **Three themes**, switched live in Settings. **Nostromo** is the amber phosphor look you know, and your v1 colours live on as its eight phosphors. **Night City** has Blackwall, Relic, Edgerun and Mox. **Gates** is a Windows 11 Fluent look that follows your Windows accent colour (or Blue, Teal, Purple, Rose, Orange or Green) in light, dark or system mode; an accent too close to a status colour (red, yellow, green or grey) is kept for the window chrome, and normal usage is drawn in Windows' default blue instead. **Reduce motion** stops all of it: the blinking, the flicker, the boot screens, the page and hover animations, the RGB glitch and the rotating comparison. The dashboard also follows Windows' own animation setting.
- **New widgets**, drawn natively with no Tk: the floating widget, the taskbar strip beside the notification area, a hover tooltip with exact percentages, reset times and pace, and a tray icon that shows your session usage. They are drawn at your display scale, so they stay sharp at 100, 125, 150 and 200%.
- **A dashboard window** (Edge WebView2) with seven pages: Overview, Limits, Cost & value, Efficiency, History, Settings and About. Open it from the tray icon, a widget click, or by launching the app again; a click straight after you closed the window opens it again at once.
- **Pace and forecasts.** Every limit shows usage against the share of its window that has passed. The session forecasts when you would hit the limit, and the week shows where it is heading and what a day's budget is.
- **Scoped weekly limits**, such as the Fable weekly limit with its share of your weekly allowance, next to the session and weekly limits. **Usage credits** appear as the one figure of real money, in the currency your account is billed in.
- **Cost and value.** API-equivalent cost at list prices for the session, today, 7 days, the month, your billing cycle and all time. How many times over your plan has paid for itself (set your billing day or plan price in Settings). Breakdowns by model, by folder, main thread against subagents, Haiku share, fast mode, and your most expensive sessions.
- **Efficiency.** Cache hit rate and what caching saved, thinking share, context size, compactions, tool use (built-in tools and MCP servers), tool error rate, lines changed, commits, pushes, pull requests and effort mix.
- **History.** A year of use as a calendar and an hour-by-weekday heatmap, streaks, the all-time token odometer, and CSV or JSON export of your daily totals.
- **A local history database** (`usage.db` in `%APPDATA%\ClaudeUsageMonitor`), filled from your transcripts, so your history outlasts Claude Code's own transcript cleanup.
- **Alerts.** Windows notifications when a limit reaches 80, 90 and 100% (your own levels, up to five), when a limit resets (you hear it when the window ends by the clock, not hours later when your next message starts a new one), and when usage credits start being used. Quiet hours, and nothing while a fullscreen app is in front.
- **Hide in fullscreen** for the floating widget, and **scoped rows** (auto, always or never) for the limits that only apply to one model.
- **Update notices.** When a newer release is out you hear about it. The monitor looks for one when it starts and again every six hours while it runs, quietly: a check that fails is simply tried again later. A new version is announced three ways: a Windows notification the first time it is found (click it to open the dashboard), **Update to vX** in the tray menu, and a banner at the top of every dashboard page but Settings (which has the same buttons in its System card) with **Update now**, **Release notes** and **Skip this version**. A version you skipped is never announced, nothing is announced while the update check is off (Settings → System), and the notification waits for a moment you can see it: not while the PC is locked, a fullscreen app is in front, or your quiet hours are on. A notification that Windows drops is tried again, and **Check for updates now** says so when it could not reach GitHub (offline, rate limited or too slow) instead of telling you that you are up to date.
- **Motion that fits each theme.** Pages, numbers and bars animate in when you open a tab, and the cards and desktop widgets answer the pointer with a short response of their own. Nostromo types its numbers out behind a block cursor and fills its pips cell by cell. Night City decodes its numbers and locks them, flashes a colour split along its bars, and has a new jack-in boot sequence. Gates fades and rises, rolls its numbers up and fills its bars on Fluent's easing curve. Hovering brightens a Nostromo widget, glitches a Night City one once and lifts a Gates one. On the dashboard, a hover effect plays once each time the pointer enters a card, tile, button or menu link, and again only after the pointer has left and come back; it never restarts itself while the pointer rests, not even at the edge of a card or on a fill bar. With Reduce motion on, every value simply appears.
- **A Windows 11 loading screen for Gates.** The Gates dashboard opens on a Windows 11-style loading screen, the app glyph and the spinning dots with "Getting your usage ready" on a soft Fluent surface, that fades into the page in under two seconds. Click or press Esc to skip it. It does not play with Reduce motion on. The window itself opens in the theme's own page colour, so a light Gates window never flashes black first.
- **No empty pages.** Every page draws all its cards at once, with placeholders in the theme's own style (Nostromo's dim cursors, Night City's scrambling glyphs, Gates' shimmering skeletons), and the numbers and charts animate in as the data arrives. A page you have already seen shows its last numbers straight away while it refreshes.

### Changed
- Every cost figure is labelled **API-equivalent at list prices**. It is what the same tokens would cost at API prices, never a bill. Only usage credits are real money.
- Days, months and billing cycles follow your **local time**. The session and the week are the real windows the usage API reports (5 hours and 7 days).
- Your v1 settings file loads as it is. The colour you picked in v1 becomes the matching Nostromo phosphor (Default becomes Amber).
- The Tk options panel is replaced by the dashboard. The old stats tab (Nerds Only) lives on as the **Efficiency** page, and an old `#nerds` link or bookmark still opens it.
- The app needs Pillow and pywebview only. The dashboard window uses the Edge WebView2 runtime that ships with Windows 11; without it (or with a runtime that is too old to start), the dashboard opens in your default browser instead of an empty window.
- **A new installer.** Still per-user, with no admin prompt, and it installs over v1 and keeps your settings. It has a **Start with Windows** box (ticked on a first install; an update keeps your choice; the switch in Settings follows Windows' own Startup apps list, so an app turned off there shows as off, and turning it on here turns it on there), offers to download the WebView2 runtime when it is missing, closes a running dashboard, starts the program folder clean so nothing of v1's Tk build is left behind (and stops, leaving your install as it was, when a file in that folder is in use), and asks on uninstall whether to delete your settings, usage history and logs. Version 2 is built and tested on 64-bit Windows 11; Windows 10 has not been tested.
- **Night City's Relic and Mox have new colours.** Relic is cool cyan on near-black with a blue accent, Mox hot pink with cyan accents on deep indigo. Both were re-checked for legibility: body text at least 4.5:1 against its panel, the status colours clearly apart from each other, and the colour for a lost sign-in clearly apart from the text, accent and status colours.
- **Every state looks like itself.** Sign-in lost, offline, rate limited and paused each get their own mark on the taskbar strip and the tray icon in every theme, so a glance tells them apart. The Nostromo tray ring now sits on a dark plate, so it stays readable on a light taskbar.
- **Gates widgets follow the other themes.** The floating widget carries a row for each scoped limit (the Fable row) in every state, so its card grows by a row instead of swapping that row for a one-line summary. The tooltip sits as far above the strip as in the other themes, and the widget's default corner and its screen-edge limit follow the visible card instead of its soft shadow.
- **Gates follows your accent properly.** The glow behind the page uses your accent, and a fixed accent (Teal, Rose and the rest) now colours the usage bars, chart marks, chips and dots, not only the Windows accent. The theme picker shows exactly the seven accents, the plan line reads "Plan: Max 5x", and Top sessions gives folder names the width they need.
- **The Limits forecast uses your real rate.** For the week and for each scoped limit, the forecast line runs on your average rate so far, marks the time it crosses 100% (or runs to the reset if it stays below), and agrees with "Projected end" on the card.
- **Smaller dashboard fixes.** The two context figures on Efficiency say which counter each comes from, so an average size before compacting no longer looks larger than the largest context without explanation. The supporters on About are headed "Thank You" in every theme. Quiet hours show the whole time, AM or PM included, in 12-hour locales. A settings card that is switched off (Alerts, for example) keeps its descriptions readable; only its controls look disabled.

### Fixed
- **Totals were several times too high.** Claude Code repeats a response's usage on every content-block line of a transcript, and v1 added every line up. Each request is now counted once. How far off v1 was depends on how much your sessions use tools: on a tool-heavy history, v1's request and token totals came out about 5 times too high.
- **Prices were out of date.** v1 priced from a short table of old models and used Sonnet rates for any model it did not know. The bundled table now covers the Fable, Mythos, Opus, Sonnet and Haiku generations back to Claude 3, with 5-minute and 1-hour cache writes, per-model cache reads (Fable 5.1 and Mythos 5.1 at 0.025 times the input price, Opus 5.5 and Sonnet 5.5 at 0.05), long-context rates and fast mode. A model with no price is listed as unpriced instead of being guessed.
- **No more `claude -p .`.** v1 ran that command to refresh an expired sign-in, which is a real prompt sent to Claude. The monitor now never runs `claude` and never sends a model request. When the sign-in has expired it says so ("login expired, open Claude Code") and picks up the new one as soon as Claude Code writes it.
- **Windows follow your clock.** v1 cut "today" and its 5-hour "session" on the UTC clock, so numbers rolled over at odd hours. Days and months now start at local midnight, a billing cycle starts on your billing day, and the session and the week are the usage API's own windows.
- **A sturdier data layer.** A damaged `settings.json` is set aside as `settings.json.bad` instead of resetting the app, settings are saved atomically, and a settings file that is merely locked is never overwritten. Transcript lines the parser cannot read are skipped, transcripts under very long folder paths (260 characters and more, such as subagent files in deep project folders) are read like any other, a file that is listed but cannot be opened is reported in the log instead of being skipped without a word, and a sign-in file that is half written is ignored until it settles. The usage API is retried with a growing delay when it is busy or unreachable, and a rate limit's Retry-After is honoured.
- **Windows details.** The screensaver check no longer writes past its buffer. A locked screen is detected on newer Windows 11 builds, where the old check missed it. The tray, the widgets and the dashboard each run on the one thread that owns them, instead of v1's Tk calls from the tray and worker threads.

### Removed
- The Tk widgets and options panel, and the `pystray` tray icon, replaced by the native widgets, the native tray icon and the dashboard.
- The call to the `claude` command (see Fixed).

### Security
- The sign-in token is read from Claude Code's own file and sent only to the usage API. It is never logged, stored or written anywhere, and a redirect never gets to carry it.
- The installer's one optional download, Microsoft's WebView2 runtime installer, is run only after Windows confirms that Microsoft Corporation signed it.
- The dashboard talks to the monitor over a local-only server (127.0.0.1, a random port and a per-run token), so nothing is exposed to your network. The page itself is served only to the window that was opened with that run's key (a bare request to the server's address gets a 401), and the window's script bridge offers the dashboard's named functions and nothing else, not the window's own internals.
- **Checked updates.** The silent update only downloads an installer from this project's own GitHub release, over https, and checks it against the SHA-256 checksum GitHub lists for that file before running it. That shows the file is the one GitHub lists for the release, not who published it (the installer is not code-signed). If the link or the checksum is not right, or the release lists none, nothing is run and the release page opens in your browser instead. What the update check reports is shown (in the notification, the menu and the banner) only when the version is a plain version number and the release page a github.com link.

## [1.1.5] — 2026-07-26

### Fixed
- **"Failed to load Python DLL" on launch:** the app is now built as a onedir bundle instead of onefile. Onefile unpacked its bundled DLLs to a temp folder on every launch — a step that raced Windows Defender and intermittently failed right after an install. Onedir installs the files once (no per-launch extraction), which removes the race and starts a little faster. This is the real fix for the launch error that v1.1.3–1.1.4 were chasing.

## [1.1.4] — 2026-07-26

### Fixed
- **"Failed to load Python DLL" on launch:** disabled UPX compression in the build. The release runner was UPX-packing the executable, which tripped Windows Defender into intermittently blocking the packed Python DLL from loading right after install. The download is a few MB larger now, but the app launches reliably.

## [1.1.3] — 2026-07-26

### Fixed
- **Self-update relaunch:** after a silent update the app now waits for the new install to fully settle before relaunching, fixing an intermittent *"Failed to load Python DLL"* error when it restarted itself too quickly. The update itself always applied correctly — only the automatic restart was affected.

## [1.1.2] — 2026-07-26

### Added
- **Bug-report template:** the issue tracker now has a pre-filled **bug** form (and a feature-request form), and the in-app **Report a Bug** links open it directly with the `bug` label already applied — no more blank issue.

## [1.1.1] — 2026-07-26

### Added
- **Update on startup:** the monitor checks GitHub for a newer release when it launches and, when one exists, shows a subtle **⬆ Update** item in the tray menu and an **Update & restart** button in **Settings → Updates**. One click downloads the new installer, applies it silently (no UAC — it's a per-user install), and relaunches. The check runs on a background thread, fails silent when offline, honours a per-version **skip**, and can be turned off (**Settings → Updates → Auto-check**).

### Changed
- The in-app version now reads from the bundled `VERSION` file, so it can't drift from the release that CI ships.

## [1.1.0] — 2026-07-26

### Added
- A **"Report a bug"** link — in the tray right-click menu and in **Settings → About** — that opens the GitHub issue tracker.
- **Automated releases:** bumping `VERSION` now builds the installer and publishes it here automatically.

## [1.0.0] — 2026-04-03

- Initial release: system-tray usage monitor with SESSION / WEEKLY bars, floating + taskbar widgets, colour themes, and a stats ("Nerds Only") panel.
