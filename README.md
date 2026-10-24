<p align="center">
  <img src="https://capsule-render.vercel.app/api?type=slice&height=180&color=0:140B03,45:F59E0B,100:F97316&text=Claude%20Usage%20Monitor&fontColor=ffffff&fontSize=34&fontAlign=69&fontAlignY=26&rotate=11&desc=Your%20Claude%20Code%20limits%2C%20always%20in%20view&descAlign=60&descAlignY=45&descSize=18" alt="Claude Usage Monitor" />
</p>

<p align="center">
  <a href="https://github.com/Godimas101/claude-usage-monitor/releases/latest"><img src="https://img.shields.io/github/v/release/Godimas101/claude-usage-monitor?style=flat-square&color=F59E0B&label=release" alt="Latest release" /></a>
  <img src="https://img.shields.io/badge/platform-Windows%2011-8b949e?style=flat-square" alt="Windows 11" />
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-F97316?style=flat-square" alt="MIT license" /></a>
  <a href="https://github.com/Godimas101/claude-usage-monitor/issues/new?template=bug_report.md&labels=bug"><img src="https://img.shields.io/badge/report-a%20bug-b96f22?style=flat-square&logo=github&logoColor=white" alt="Report a bug" /></a>
</p>

> **"Never be surprised by a rate limit again — unless you deserve it."**

A free Windows tray utility that keeps your **Claude Code plan usage** where you can see it: a floating widget, a strip beside the clock, a tray icon and a full dashboard. It asks Anthropic's usage API for your limits (with the sign-in Claude Code already has), reads Claude Code's own transcripts to show what your work would cost at API list prices, and never sends a single prompt to Claude.

<p align="center">
  <img src="docs/images/hero.png" width="880" alt="A mock-up Windows desktop: the Nostromo dashboard, the floating widget in all three themes and the taskbar strip next to the tray icon" />
</p>

## 🚀 Quick Start

1. **Download** the latest installer from the [**Releases page**](https://github.com/Godimas101/claude-usage-monitor/releases/latest) (`ClaudeUsageMonitorSetup-v….exe`). No Python needed.
2. **Run it.** It installs for your user only, so there is no admin prompt, and it has a **Start with Windows** box if you want the monitor at every sign-in.
3. **Have Claude Code installed and signed in.** That sign-in is where your limits come from. The widgets appear next to the tray as soon as the app starts.
4. **Click the tray icon** (or double-click the floating widget) to open the dashboard.

## ✨ Features

| | What you get |
|---|---|
| 🪟 | **Three ways to glance:** a floating widget you can drag anywhere, a strip that sits beside the clock, and a tray icon that shows your session usage. Hover for the exact numbers. |
| 📈 | **Every limit that applies to you:** the 5-hour session, the 7-day week, and per-model weekly limits such as Fable, each with its reset time and a pace line (usage against time). |
| 🔮 | **Forecasts:** when you would hit the session limit at this rate, where the week is heading, and what a day's budget is. |
| 💸 | **API-equivalent cost at list prices**, and how many times over your plan has paid for itself. Never confused with a bill. |
| 🧠 | **Efficiency:** cache hit rate, thinking share, context size, tool use, commits, pushes and pull requests, straight from your own transcripts. |
| 🗓️ | **History that lasts:** a year of use as a heatmap, streaks, an all-time token odometer and CSV or JSON export. It outlives Claude Code's transcript cleanup. |
| 🔔 | **Alerts** as Windows notifications at the levels you choose (80, 90 and 100% by default), when a limit resets, and when usage credits start being used. Quiet hours included. |
| 🎨 | **Three themes**, switched live: Nostromo, Night City and Gates, each with motion of its own. |
| 🤖 | **Claude can check it too:** an MCP tool, `get_plan_usage`, and optional Claude Code hooks let a long run see how much of the plan is left, and stop launching agents past a cap you set. [Set it up](docs/AGENT_HOOK.md). |
| 🔒 | **All local.** No account, no telemetry, no server of ours. The only network calls are listed [below](#-privacy). |
| 😴 | **Polite:** it pauses while your screen is locked, hides the strip while a fullscreen app runs, and only redraws when something changes. **Reduce motion** stops every animation, and the dashboard and the widgets' pointer response also follow Windows' own animation setting. |

## 🎨 Themes

Pick one in **Settings → Theme**. It changes everywhere at once (widgets, tooltip, tray icon and dashboard), with no restart. Each theme has its own look and its own voice; the numbers are the same.

### Nostromo (the default)

Amber P3 phosphor on CRT black: segmented pip bars, chamfered panels, scanlines and glow, and a short typed boot log when the dashboard opens (a click skips it). Pages print themselves in: the numbers are typed out behind a block cursor and the pips light cell by cell. It is the only theme whose widgets blink (the little "alive" mark). Eight phosphors: **Amber** (default), **Green**, **White**, **Red**, **Blue**, **Cyan**, **Orange** and **Purple**.

<p align="center">
  <img src="docs/images/theme-nostromo.png" width="880" alt="Nostromo: the floating widget, its hover tooltip, the tray icon in four states and the taskbar strip" />
</p>
<p align="center">
  <img src="docs/images/dashboard-nostromo-overview.png" width="880" alt="The Nostromo dashboard overview page" />
</p>

<details>
<summary>More Nostromo: widget states, all eight phosphors and every dashboard page</summary>

<p align="center"><img src="docs/images/states-nostromo.png" width="640" alt="Nostromo floating widget when normal, in warning, critical and signed out" /></p>
<p align="center"><img src="docs/images/variants-nostromo.png" width="880" alt="The eight Nostromo phosphors" /></p>
<p align="center"><img src="docs/images/dashboard-nostromo-limits.png" width="880" alt="Nostromo, Limits page" /></p>
<p align="center"><img src="docs/images/dashboard-nostromo-cost.png" width="880" alt="Nostromo, Cost and value page" /></p>
<p align="center"><img src="docs/images/dashboard-nostromo-efficiency.png" width="880" alt="Nostromo, Efficiency page" /></p>
<p align="center"><img src="docs/images/dashboard-nostromo-history.png" width="880" alt="Nostromo, History page" /></p>
<p align="center"><img src="docs/images/dashboard-nostromo-settings.png" width="880" alt="Nostromo, Settings page" /></p>

</details>

### Night City

Chamfered frames, red line art and cyan RAM cells, numbers that decode and lock in as a page opens, a jack-in boot sequence (once per session, a click skips it) and a one-shot RGB glitch on the dashboard's numbers when fresh data changes them. The tray icon is a RAM meter. Dark only. Four colourways: **Blackwall** (default), **Relic**, **Edgerun** (yellow plates) and **Mox** (pink).

<p align="center">
  <img src="docs/images/theme-night-city.png" width="880" alt="Night City: the floating widget, its hover tooltip, the RAM-meter tray icon in four states and the taskbar strip" />
</p>
<p align="center">
  <img src="docs/images/dashboard-night-city-overview.png" width="880" alt="The Night City dashboard overview page" />
</p>

<details>
<summary>More Night City: widget states, all four colourways and every dashboard page</summary>

<p align="center"><img src="docs/images/states-night-city.png" width="640" alt="Night City floating widget when normal, in warning, critical and signed out" /></p>
<p align="center"><img src="docs/images/variants-night-city.png" width="640" alt="The four Night City colourways" /></p>
<p align="center"><img src="docs/images/dashboard-night-city-limits.png" width="880" alt="Night City, Limits page" /></p>
<p align="center"><img src="docs/images/dashboard-night-city-cost.png" width="880" alt="Night City, Cost and value page" /></p>
<p align="center"><img src="docs/images/dashboard-night-city-efficiency.png" width="880" alt="Night City, Efficiency page" /></p>
<p align="center"><img src="docs/images/dashboard-night-city-history.png" width="880" alt="Night City, History page" /></p>
<p align="center"><img src="docs/images/dashboard-night-city-settings.png" width="880" alt="Night City, Settings page" /></p>

</details>

### Gates

Native Windows 11 Fluent surfaces: rounded cards, a thin time rail above every usage bar and one lamp for the link. It follows your **Windows accent colour** by default (or pick Blue, Teal, Purple, Rose, Orange or Green) and your **light or dark** mode, or you can fix either in Settings. It uses the fonts Windows already has. Its widgets never blink or glow. The dashboard opens on a Windows 11-style loading screen (a click skips it), and its pages fade and rise, its numbers roll up and its bars fill on Fluent's easing curve. A Windows accent too close to a status colour (red, yellow, green or grey) is kept for the window's chrome, and normal usage is drawn in Windows' default blue instead.

<p align="center">
  <img src="docs/images/theme-gates.png" width="880" alt="Gates in dark mode: the floating widget, its hover tooltip, the ring tray icon in four states and the taskbar strip" />
</p>
<p align="center">
  <img src="docs/images/dashboard-gates-overview.png" width="880" alt="The Gates dashboard overview page in dark mode" />
</p>

<details>
<summary>More Gates: light mode, accents, widget states and every dashboard page</summary>

<p align="center"><img src="docs/images/theme-gates-light.png" width="880" alt="Gates in light mode: the floating widget, its hover tooltip, the tray icon and the taskbar strip on a light taskbar" /></p>
<p align="center"><img src="docs/images/dashboard-gates-light-overview.png" width="880" alt="The Gates dashboard overview page in light mode" /></p>
<p align="center"><img src="docs/images/states-gates.png" width="640" alt="Gates floating widget when normal, in warning, critical and signed out" /></p>
<p align="center"><img src="docs/images/variants-gates.png" width="880" alt="Four of the seven Gates accents (system, teal, rose and green) in dark and light mode" /></p>
<p align="center"><img src="docs/images/dashboard-gates-limits.png" width="880" alt="Gates, Limits page" /></p>
<p align="center"><img src="docs/images/dashboard-gates-cost.png" width="880" alt="Gates, Cost and value page" /></p>
<p align="center"><img src="docs/images/dashboard-gates-efficiency.png" width="880" alt="Gates, Efficiency page" /></p>
<p align="center"><img src="docs/images/dashboard-gates-history.png" width="880" alt="Gates, History page" /></p>
<p align="center"><img src="docs/images/dashboard-gates-settings.png" width="880" alt="Gates, Settings page" /></p>

</details>

> The images are composites: invented demo data, with the widgets and dashboard rendered by the real app, placed on a mock-up desktop. The pictures of single dashboard pages are plain captures of the dashboard.

## 🪟 Widgets

| Widget | What it does |
|---|---|
| **Floating widget** | Always on top, never steals focus and has no taskbar button. Drag it anywhere; it remembers its spot (the first time, it sits in the bottom-right corner above the tray). **Double-click** opens the dashboard, **right-click** opens the menu. Optional: transparent background, text effects, and **hide in fullscreen**. |
| **Taskbar strip** | A compact `5H` and `7D` readout as tall as your taskbar, just left of the notification area. Click opens the dashboard, right-click opens the menu. Windows 11's taskbar cannot host real deskbands without extra tools, so the strip is a small topmost window kept in place and re-raised every half second, which looks like the real thing and needs nothing else installed. It needs a horizontal taskbar that does not auto-hide, and it steps aside while a fullscreen app is in front. |
| **Tray icon** | Draws your session usage. **Click** opens the dashboard; **right-click** gives the menu: open the dashboard, show or hide each widget, refresh now, report a bug, update, exit. Launching the app a second time just opens the dashboard too. |
| **Hover tooltip** | Rest the pointer on a widget for about 0.4 seconds to see the exact percentages, reset times and pace for every limit. The widget answers the pointer too, in its theme's own way: Nostromo brightens, Night City glitches once, Gates lifts a little. **Reduce motion** and Windows' animation setting turn that off. |

Each usage bar comes with a thin **time line**: how much of that window has already passed. A bar ahead of its line means you are using the window faster than an even pace. The pace verdict is **on pace** while usage is within 10 points of the clock, **ahead** when it leads by more than that, and **behind** (some themes say *under pace*) when it trails by more. Bars turn to *warning* at 75% and *critical* at 90%, unless the usage API reports its own severity. A scoped limit (such as Fable) gets its own row once it is in use, and **Scoped rows** in Settings can force it on or off. (On Gates the floating widget grows by a row for each scoped limit it shows.) The strip and the tray icon mark every state apart: sign-in lost, offline, rate limited and paused each get a mark of their own in every theme.

**Screen lock.** While your workstation is locked or a screensaver runs, the monitor stops asking the API (the status reads **PAUSED**), and a locked screen stops the animations too. When you come back it refreshes at once.

## 🖥️ Dashboard

Open it from the tray icon, a widget click, or by starting the app again. It is a separate window that only runs while it is open. Every setting applies the moment you change it.

| Page | What you will find |
|---|---|
| **Overview** | The session, weekly and first scoped limit with reset times, pace and forecast; this cycle's API-equivalent cost and the plan multiple with a 30-day chart; cache and thinking; model mix; where the week went; burn rate in tokens per minute. |
| **Limits** | Every limit window on its own card: where it is, a chart of the readings with the even-pace line and a forecast, time left, budget per day and the projected end. Also your usage credits (real money) and the weekly split by surface. |
| **Cost & value** | How many times over your plan has paid for itself, month by month; cost for the session, today, 7 days, the month, your billing cycle and all time; breakdowns by model, by folder (the repository the work happened in) and by launch folder; main threads against subagents; Haiku share; fast mode; your priciest sessions this week. |
| **Efficiency** | Cache hit rate and what caching saved, thinking share, context size (average, median, p95), compactions, built-in and MCP tool use and error rate, lines changed, files created, commits, pushes, pull requests, cost per PR, effort mix, exact token counts and data health. Hover a figure to see what it means. |
| **History** | The all-time token odometer with some fun comparisons, a calendar of every day for a year, an hour-by-weekday heatmap, streaks and records, and **Export** of your daily totals as CSV or JSON. |
| **Settings** | Theme, accent and mode; widgets and display; how often to fetch (1 to 60 minutes); billing day; plan price override; alerts; start with Windows; updates. |

There is also an **About** page with the version, credits and links. An older `#nerds` link or bookmark (the page's old name) still opens Efficiency. Every page draws all its cards at once, with placeholders in the theme's own style, and the numbers arrive into them; a page you have already seen shows its last numbers straight away while it refreshes.

## 🤖 For Claude sessions

Long runs, such as multi-agent workflows, can burn through a session or a week before anyone
notices. The monitor shares what it knows with Claude Code, read-only and at no model-token cost:

- **`get_plan_usage`**, an MCP tool Claude can call between steps. It returns each limit's
  percent, reset time and pace, the projected time to the limit and the burn rate.
- **`--hook`** adds a one-line summary to Claude's context at session start, and when a limit
  passes a threshold.
- **`--gate`** refuses new agents and workflows once a limit reaches a cap you choose.
- **`--status --json`** prints the same reading for your own scripts.

The setup, the settings snippet and the fields are in [docs/AGENT_HOOK.md](docs/AGENT_HOOK.md).

## 📡 Data Sources

| Data | Where it comes from |
|---|---|
| **Limits and usage credits** | Anthropic's usage API, `https://api.anthropic.com/api/oauth/usage`: the session (5 h) and weekly (7 d) limits, scoped weekly limits such as **Fable** (with their share of the weekly allowance), the weekly split by surface and your usage credits. It is called with the sign-in Claude Code already saved in `~/.claude/.credentials.json`. The monitor only reads that file and never writes it. |
| **Plan name and price** | The same sign-in file. Pro is $20, Max 5x is $100 and Max 20x is $200 a month. Another plan? Type its price in Settings. |
| **Requests, tokens, tools and sessions** | Claude Code's local transcripts, `~/.claude/projects/**/*.jsonl` (and `~/.config/claude/projects`, or wherever `CLAUDE_CONFIG_DIR` points). **Each request is counted once** (per message ID and request ID), because Claude Code repeats a response's usage on every content-block line. Code changes and git activity come from the same files. |
| **History** | A local SQLite database, `usage.db`, that remembers every request the transcripts showed, so it survives Claude Code cleaning up old transcripts. |
| **Prices** | A price table bundled with the app (`pricing.json`, list prices in US dollars per million tokens). |

The monitor never runs `claude`, never signs in for you and never sends a model request.

## 💵 What "API-equivalent at list prices" means

On a Pro or Max plan you pay a flat monthly price, and what you run out of is *limits*. The cost figures in the dashboard are something else: for every request in your transcripts, the monitor multiplies the tokens (fresh input, output, cache reads, and 5-minute and 1-hour cache writes) by that model's **list price per million tokens**. That is what the same work would have cost on the pay-as-you-go API.

It is a yardstick, good for "how many times over has my plan paid for itself?" and for comparing models, folders and days. It is **not a bill**, not what you owe Anthropic, and it can be many times your plan price.

Real money is only two things: your plan's monthly price, and **usage credits** if you have switched them on. The usage API reports the credits, and the dashboard shows them on their own "real money" card, never added to the API-equivalent figures. A model that is missing from the price table is listed as *not priced* and left out of the cost, instead of being priced as something else.

## 🔒 Privacy

Everything stays on your PC. There is no account, no telemetry and no server of ours.

- **What it reads:** your Claude Code transcripts and sign-in file. To group the Cost page by repository, it also looks for a `.git` entry in each session's working folder and the folders above it (when that entry is a file, as in a linked worktree or a submodule, it reads only its first line, the `gitdir:` pointer). It never prints, logs or stores message text, prompts, tool inputs or outputs, or file contents, and never stores your token.
- **What it keeps** in `usage.db`, on your PC only: IDs, model names, token counts, timestamps, each session's folder and git branch, the session titles Claude Code gives them, tool names, lines added and removed, pull-request links, and whether a shell command ran `git commit`, `git push` or `gh pr create` (never the command itself). It also keeps the full path of each transcript file it has read (your Windows user name is in those paths), 180 days of your limit readings, and the newest usage-API response, including your credit balance when usage credits are on. **Do not attach `usage.db` to a bug report:** it holds your folder names, session titles and those paths. If you need to share numbers, use **Export** on the History page (CSV or JSON, daily totals only). Beside it, `state.json` holds the latest limits, pace and burn rate for Claude sessions (never the token), and `agent-demand`, `hook-seen.json` and `agent.log` support the [Claude session tools](docs/AGENT_HOOK.md#privacy).
- **What leaves your PC:** only these requests, and they carry no prompts, no code and no usage numbers.

| When | To | What |
|---|---|---|
| Every fetch (15 minutes by default, or when you press Refresh) | `api.anthropic.com` | The usage request, with your Claude Code sign-in token. The token goes nowhere else, and a redirect never gets to carry it. |
| At start-up, every six hours after that, and on **Check for updates now** (switch it off in Settings → System) | `api.github.com` | An anonymous request for this repository's latest release. |
| Only when you click **Install update** | `github.com` and GitHub's download servers | The download of the installer. |
| Only when you open the **About** page | `raw.githubusercontent.com` | An anonymous request for the supporters list, cached for an hour. |
| Only when the app is closed and a Claude session asks for the usage (the MCP tool, `--status`, the gate) | `api.anthropic.com` | The same usage request, at most once every two minutes. While the app runs, sessions read its last reading instead. |

The **installer** (not the app) has one more optional download: Microsoft's small WebView2 runtime installer, from `go.microsoft.com`, and only if you accept its offer. It is run only after Windows confirms that Microsoft Corporation signed it. Prices ship inside the app, so there is no price download. The dashboard is served by a tiny local web server on `127.0.0.1` (a random port and a per-run token), so nothing listens on your network, and the page loads no outside fonts or scripts.

## 📦 Installation

**Install:** download the installer from the [latest release](https://github.com/Godimas101/claude-usage-monitor/releases/latest) and run it. The app goes to `%LOCALAPPDATA%\ClaudeUsageMonitor` and a Start menu entry is added. The **Start with Windows** box is ticked on a first install, and you can change it any time in Settings → System. If the monitor is running, the installer asks you to close it first. If the WebView2 runtime is missing, it offers to download Microsoft's small installer for it; say no and the dashboard opens in your browser instead.

**Update:** the monitor looks for a newer release when it starts and again every six hours while it runs (switch that off in Settings → System). A release it finds is announced once with a Windows notification (click it to open the dashboard) and then stays on offer in three places: **Update to vX** in the tray menu, a banner at the top of every dashboard page but Settings with **Update now**, **Release notes** and **Skip this version**, and **Install update** in Settings → System. A version you skip is never announced, nothing is announced while the check is off, and the notification waits for a moment you can see it: not while the PC is locked, a fullscreen app is in front or your quiet hours are on. Installing downloads the installer from this project's GitHub release, checks it against the SHA-256 checksum GitHub lists for that file, runs it silently and relaunches the app. If the link or the checksum is not right, or the release has none, nothing is run and the release page opens in your browser instead. The check shows that the file is the one GitHub lists for the release; it does not prove who published it, and the installer is not code-signed. You can also just run the newer installer over the old one. Your settings, history and Start with Windows choice are kept, and v1 users keep their settings too (the colour you picked in v1 becomes the matching Nostromo phosphor).

**Uninstall:** Settings → Apps → Installed apps → Claude Usage Monitor → Uninstall. It asks whether to delete your settings, usage history and logs in `%APPDATA%\ClaudeUsageMonitor`. They stay unless you say yes (a silent uninstall never deletes them).

**From source:**

```
pip install -r requirements.txt
pythonw main.py
```

Use `python main.py` instead if you want a console that shows errors (Ctrl+C quits). To try a build next to your installed copy without touching it, set `CUM_DEV_INSTANCE=1` and `CUM_APPDATA_DIR` to a scratch folder first. Contributors: see [`CONTRIBUTING.md`](CONTRIBUTING.md), [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and [`docs/UI_ARCHITECTURE.md`](docs/UI_ARCHITECTURE.md); `pip install -r requirements-dev.txt` then `python -m pytest` runs the tests.

## 🔩 Requirements

- Windows 11, 64-bit. Version 2 is built and tested there; Windows 10 has not been tested.
- **Claude Code**, installed and signed in with a Claude account (that is where your limits and your transcripts come from)
- The **Microsoft Edge WebView2 runtime** for the dashboard window. It comes with Windows 11, and the installer offers to download it when it is missing. Without it the dashboard opens in your default browser instead.
- Python 3.10 or newer, only if you run from source

## 🩺 Troubleshooting

| You see | What is going on | What to do |
|---|---|---|
| **STALE**, with a message to open Claude Code | The sign-in Claude Code saved has expired or was rejected. The monitor never signs in for you. | Open Claude Code. It refreshes the sign-in file, and the monitor picks the change up by itself. |
| **NO LOGIN** | There is no Claude sign-in on this PC. | Sign in to Claude Code with your Claude account. An API-key-only setup has no plan limits to show. |
| **OFFLINE** | The usage API could not be reached. Your last numbers stay on screen. | Check your network. It retries after 30 seconds, then waits longer each time, up to 15 minutes. |
| **RATE LIMITED** | The usage API asked for a pause. | It waits as long as the API says (or 1 minute, growing to 15). A longer fetch interval in Settings helps. |
| **PAUSED** | Your screen is locked or a screensaver is running. | Nothing: it refreshes the moment you are back. |
| No taskbar strip | The strip needs a horizontal taskbar that does not auto-hide, and it hides while a fullscreen app is in front. | Use the floating widget, or change the taskbar setting. |
| The floating widget is gone | It was switched off, or hidden by **Hide in fullscreen**. | Tray menu → **Floating widget**. |
| The dashboard opens in my browser | The WebView2 runtime is missing. | Install Microsoft's Evergreen WebView2 runtime, or run the installer again and accept its offer. The dashboard works in your browser meanwhile. |
| "Reading your usage history…" for a while | The first launch reads all your existing transcripts into the database. | Wait. The Data health box in the sidebar shows the progress. A big history takes a minute or so. |
| A model is listed as **not priced** | It is not in the bundled price table yet, so its requests are left out of the cost. | [Report it](https://github.com/Godimas101/claude-usage-monitor/issues/new?template=bug_report.md&labels=bug) so it can be added. |
| The plan multiple is missing | Your plan is not Pro, Max 5x or Max 20x, so its price is unknown. | Settings → Data & plan → **Plan price override**. |
| Cost looks huge next to my plan | It is API-equivalent, not a bill. | See [above](#-what-api-equivalent-at-list-prices-means). |
| Windows SmartScreen warns about the installer | The installer is not code-signed, so Windows may show a SmartScreen warning. | **More info → Run anyway**, if you downloaded it from the Releases page. |

Logs are in `%APPDATA%\ClaudeUsageMonitor` (`monitor.log`, plus `dashboard.log` and `faults.log`). They never contain prompts or tokens. They can contain file paths, so give them a glance before you share them.

<details>
<summary>Where everything lives</summary>

| Path | What |
|---|---|
| `%LOCALAPPDATA%\ClaudeUsageMonitor` | The installed app |
| `%APPDATA%\ClaudeUsageMonitor\settings.json` | Your settings. A damaged file is set aside as `settings.json.bad` and the app starts from defaults. |
| `%APPDATA%\ClaudeUsageMonitor\usage.db` | Your history (SQLite). Close the app and delete it to rebuild it from the transcripts Claude Code still has. It holds folder names, session titles and file paths, so keep it out of bug reports (see [Privacy](#-privacy)). |
| `%APPDATA%\ClaudeUsageMonitor\monitor.log` | The log (it rotates), plus `dashboard.log` and `faults.log` |
| `~\.claude\projects` and `~\.claude\.credentials.json` | Claude Code's transcripts and sign-in. Read only. |

</details>

## 🐛 Found a bug?

> Found a bug? [File a report on GitHub →](https://github.com/Godimas101/claude-usage-monitor/issues/new?template=bug_report.md&labels=bug) You can also use **Report a bug** in the tray menu, or the **Issues** link on the About page.

Have an idea? [Request a feature →](https://github.com/Godimas101/claude-usage-monitor/issues/new?template=feature_request.md&labels=enhancement)

## 🙏 Credits

Built by **Chris Carpenter** with **Claude Code**: Claude Opus 5.5, with Sonnet 5.5 and Haiku 5.5 subagents. (Version 1 was built with Claude Sonnet 4.6.)

Fonts, all under the SIL Open Font License 1.1: Michroma, IBM Plex Mono and VT323 (Nostromo); Chakra Petch, Rajdhani and Share Tech Mono (Night City). Gates uses fonts from your own Windows install (Segoe UI Variable, Segoe Fluent Icons and Cascadia Mono, with Segoe UI and Consolas as fallbacks) and bundles none. Each licence text sits beside its font in `usage_monitor/webui/fonts`.

Claude and Claude Code belong to Anthropic. This is an independent, unofficial tool.

Standing on the shoulders of these projects:

| Repo | Notes |
|------|-------|
| [CodeZeno/Claude-Code-Usage-Monitor](https://github.com/CodeZeno/Claude-Code-Usage-Monitor) | Rust, Win32 native, true taskbar embedding |
| [Bortlesboat/claude-usage-monitor](https://github.com/Bortlesboat/claude-usage-monitor) | Python, OAuth API + JSONL, advanced stats |
| [Maciek-roboblog/Claude-Code-Usage-Monitor](https://github.com/Maciek-roboblog/Claude-Code-Usage-Monitor) | Python, Rich terminal UI, JSONL only |
| [amnweb/yasb](https://github.com/amnweb/yasb) | Python status bar, same topmost-window approach for Win11 |

## 🧡 Support

This tool is free and always will be. If it saves you from a surprise rate limit, consider supporting on Patreon — it helps keep the mods and tools coming.

[![Support on Patreon](https://raw.githubusercontent.com/Godimas101/personal-projects/main/patreon/images/buttons/patreon-medium.png)](https://patreon.com/Godimas101)

---

*Please poll responsibly.*
