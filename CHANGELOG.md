# Changelog

All notable changes to Claude Usage Monitor. Format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

## [2.0.0] — 2026-10-22

Claude Usage Monitor 2 is a rewrite: new widgets, a real dashboard, three themes and a data layer that counts honestly. Install it over v1 and your settings carry over. There was no separate v1.2 release, so the fixes planned for it (correct counting, current prices, local-time windows, a sturdier data layer) ship here, under **Fixed**.

### Added
- **Three themes**, switched live in Settings. **Nostromo** is the amber phosphor look you know, and your v1 colours live on as its eight phosphors. **Night City** has Blackwall, Relic, Edgerun and Mox. **Gates** is a Windows 11 Fluent look that follows your Windows accent colour (or Blue, Teal, Purple, Rose, Orange or Green) in light, dark or system mode; an accent too close to a status colour (red, yellow, green or grey) is kept for the window chrome, and normal usage is drawn in Windows' default blue instead. **Reduce motion** stops the blinking, the flicker, the boot log, the RGB glitch and the rotating comparison.
- **New widgets**, drawn natively with no Tk: the floating widget, the taskbar strip beside the notification area, a hover tooltip with exact percentages, reset times and pace, and a tray icon that shows your session usage. They are drawn at your display scale, so they stay sharp at 100, 125, 150 and 200%.
- **A dashboard window** (Edge WebView2) with seven pages: Overview, Limits, Cost & value, Nerds only, History, Settings and About. Open it from the tray icon, a widget click, or by launching the app again.
- **Pace and forecasts.** Every limit shows usage against the share of its window that has passed. The session forecasts when you would hit the limit, and the week shows where it is heading and what a day's budget is.
- **Scoped weekly limits**, such as the Fable weekly limit with its share of your weekly allowance, next to the session and weekly limits. **Usage credits** appear as the one figure of real money, in the currency your account is billed in.
- **Cost and value.** API-equivalent cost at list prices for the session, today, 7 days, the month, your billing cycle and all time. How many times over your plan has paid for itself (set your billing day or plan price in Settings). Breakdowns by model, by folder, main thread against subagents, Haiku share, fast mode, and your most expensive sessions.
- **Nerds only.** Cache hit rate and what caching saved, thinking share, context size, compactions, tool use (built-in tools and MCP servers), tool error rate, lines changed, commits, pushes, pull requests and effort mix.
- **History.** A year of use as a calendar and an hour-by-weekday heatmap, streaks, the all-time token odometer, and CSV or JSON export of your daily totals.
- **A local history database** (`usage.db` in `%APPDATA%\ClaudeUsageMonitor`), filled from your transcripts, so your history outlasts Claude Code's own transcript cleanup.
- **Alerts.** Windows notifications when a limit reaches 80, 90 and 100% (your own levels, up to five), when a limit resets, and when usage credits start being used. Quiet hours, and nothing while a fullscreen app is in front.
- **Hide in fullscreen** for the floating widget, and **scoped rows** (auto, always or never) for the limits that only apply to one model.

### Changed
- Every cost figure is labelled **API-equivalent at list prices**. It is what the same tokens would cost at API prices, never a bill. Only usage credits are real money.
- Days, months and billing cycles follow your **local time**. The session and the week are the real windows the usage API reports (5 hours and 7 days).
- Your v1 settings file loads as it is. The colour you picked in v1 becomes the matching Nostromo phosphor (Default becomes Amber).
- The Tk options panel is replaced by the dashboard. The old stats tab lives on as the **Nerds only** page.
- The app needs Pillow and pywebview only. The dashboard window uses the Edge WebView2 runtime that ships with Windows 11; without it, the dashboard opens in your default browser.
- **A new installer.** Still per-user, with no admin prompt, and it installs over v1 and keeps your settings. It has a **Start with Windows** box (ticked on a first install; an update keeps your choice), offers to download the WebView2 runtime when it is missing, closes a running dashboard, starts the program folder clean so nothing of v1's Tk build is left behind (and stops, leaving your install as it was, when a file in that folder is in use), and asks on uninstall whether to delete your settings, usage history and logs. Version 2 is built and tested on 64-bit Windows 11; Windows 10 has not been tested.

### Fixed
- **Totals were several times too high.** Claude Code repeats a response's usage on every content-block line of a transcript, and v1 added every line up. Each request is now counted once. How far off v1 was depends on how much your sessions use tools: on a tool-heavy history, v1's request and token totals came out about 5 times too high.
- **Prices were out of date.** v1 priced from a short table of old models and used Sonnet rates for any model it did not know. The bundled table now covers the Fable, Mythos, Opus, Sonnet and Haiku generations back to Claude 3, with 5-minute and 1-hour cache writes, per-model cache reads, long-context rates and fast mode. A model with no price is listed as unpriced instead of being guessed.
- **No more `claude -p .`.** v1 ran that command to refresh an expired sign-in, which is a real prompt sent to Claude. The monitor now never runs `claude` and never sends a model request. When the sign-in has expired it says so ("login expired, open Claude Code") and picks up the new one as soon as Claude Code writes it.
- **Windows follow your clock.** v1 cut "today" and its 5-hour "session" on the UTC clock, so numbers rolled over at odd hours. Days and months now start at local midnight, a billing cycle starts on your billing day, and the session and the week are the usage API's own windows.
- **A sturdier data layer.** A damaged `settings.json` is set aside as `settings.json.bad` instead of resetting the app, settings are saved atomically, and a settings file that is merely locked is never overwritten. Transcript lines the parser cannot read are skipped, and a sign-in file that is half written is ignored until it settles. The usage API is retried with a growing delay when it is busy or unreachable, and a rate limit's Retry-After is honoured.
- **Windows details.** The screensaver check no longer writes past its buffer. A locked screen is detected on newer Windows 11 builds, where the old check missed it. The tray, the widgets and the dashboard each run on the one thread that owns them, instead of v1's Tk calls from the tray and worker threads.

### Removed
- The Tk widgets and options panel, and the `pystray` tray icon, replaced by the native widgets, the native tray icon and the dashboard.
- The call to the `claude` command (see Fixed).

### Security
- The sign-in token is read from Claude Code's own file and sent only to the usage API. It is never logged, stored or written anywhere, and a redirect never gets to carry it.
- The installer's one optional download, Microsoft's WebView2 runtime installer, is run only after Windows confirms that Microsoft Corporation signed it.
- The dashboard talks to the monitor over a local-only server (127.0.0.1, a random port and a per-run token), so nothing is exposed to your network.
- **Checked updates.** The silent update only downloads an installer from this project's own GitHub release, over https, and checks it against the SHA-256 checksum GitHub lists for that file before running it. That shows the file is the one GitHub lists for the release, not who published it (the installer is not code-signed). If the link or the checksum is not right, or the release lists none, nothing is run and the release page opens in your browser instead.

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
