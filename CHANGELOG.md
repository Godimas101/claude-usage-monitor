# Changelog

All notable changes to Claude Usage Monitor. Format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

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
