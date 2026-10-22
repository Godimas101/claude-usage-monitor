# Changelog

All notable changes to Claude Usage Monitor. Format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Fixed
- **Gates accent:** a Windows accent close to the warning or critical colours (red, yellow) no longer draws normal usage in a severity colour. The accent is lifted once, in OKLCH, to be readable on the card in dark mode, and the widgets and the dashboard use that same colour; normal bars keep the theme's own accent where the lifted one would be mistaken for a severity colour.
- **Nostromo:** stale, offline and sign-in text is readable (at least 4.5:1) in every phosphor; the name of a scoped limit gives way to its value on the widget and the tooltip instead of running into it; the taskbar strip scales to a short taskbar; the dashboard title bar keeps REFRESH on screen in a 720 px window.
- **Night City:** the targeting reticle and the code matrix show in the default dashboard window, and Edgerun's limit cards have their TIME REMAINING bar.
- **Dashboard:** the data-link row says OK only while the link is live; the plan multiple keeps growing past 24 months (one cell is then several months, and the caption says so); real money is shown in the account's own currency; chart labels fit the font in use and are cut in the middle, so similar folder names stay apart; the overview cards no longer stretch in a very tall window.
- **Settings and data:** the plan price override is applied where a price is used instead of being written into the published state, and the pace verdict is the same 10 points on the widgets, in the reports and on the dashboard.
- **Local API:** a client that hangs up mid-request is no longer logged as an error, and the statistics database can sit on a network share.

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
