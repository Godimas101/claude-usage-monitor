# UI Architecture — Claude Usage Monitor v2 (Phase 3)

Companion to [`ARCHITECTURE.md`](ARCHITECTURE.md) (data layer). Stack decision: #15 —
Pillow-drawn widgets on per-pixel-alpha layered windows + a pywebview dashboard.
Spike results: #15 comments.

## Processes and threads

```
ClaudeUsageMonitor (main process)
├─ UI thread (main thread)  Win32 message loop: layered widgets, tray icon, controller window,
│                           menus, timers, session-lock + settings-change notifications
├─ data thread              DataService (Phase 1) → MonitorState
├─ API threads              ThreadingHTTPServer on 127.0.0.1:<random>: webui + JSON API
└─ dashboard child process  "--dashboard": pywebview (WebView2) window on the local API URL
```

- **No Tk in v2.** The UI thread owns every window. Other threads never touch a window; they
  `PostMessageW(controller_hwnd, WM_APP_*)` and the UI thread pulls the latest value from a
  lock-protected slot. (v1 called Tk from worker and tray threads.)
- The dashboard is a separate process because pywebview must own its process's main thread.
  It costs nothing while closed and a normal browser can load the same URL (Playwright tests).
- Single instance: named mutex `Global\ClaudeUsageMonitor` (unchanged from v1) for the main
  process only. A second launch asks the running instance to open the dashboard
  (`WM_APP_OPEN_DASHBOARD` posted to the controller window found by class name).

## Modules

```
usage_monitor/
  app.py                  MonitorApp: wires settings, DataService, UI host, server; run()/quit()
  ui/
    win32.py              typed ctypes bindings (every function has argtypes/restype) + constants
    host.py               UIHost: window classes, controller window, message loop, timers,
                          cross-thread posting, WTS session notifications, TaskbarCreated
    layered.py            LayeredWindow: show(image, x, y), move, hide, destroy, hit-testing,
                          native drag (WM_NCHITTEST → HTCAPTION), click/double-click/right-click,
                          hover (TrackMouseEvent), click-through toggle
    widgets.py            FloatingWidget, TaskbarWidget, Tooltip: layout/position + renderer calls
    tray.py               TrayIcon via Shell_NotifyIconW: icon from renderer, tooltip, left click →
                          dashboard, right click → menu, balloon/toast notifications
    menu.py               native popup menu (TrackPopupMenuEx), shared by tray + widgets
    platform.py           taskbar geometry (primary + secondary), monitors, DPI, fullscreen
                          detection, lock/screensaver, Windows accent colour + light/dark mode
    server.py             local API server (see "Local API")
    dashboard.py          DashboardLauncher (spawn/focus the child) + dashboard_main() (child side)
  render/
    model.py              WidgetModel: view model built from MonitorState + settings + now
    engine.py             Pillow toolkit: supersampled canvas, font cache, glow, bars, shapes,
                          scanlines, vignette, premultiplied BGRA conversion, HICON conversion
    skins/<theme>.py      render_floating / render_taskbar / render_tooltip / render_tray_icon
  themes/
    __init__.py           load + validate theme JSON, variants, v1 migration, system accent
    colour.py             OKLCH, WCAG contrast and CIE76 distance in pure Python (the accent's lift)
    nostromo.json  night_city.json  gates.json
  webui/                  index.html, app.js, charts.js, themes/<theme>.css (+ optional .js),
                          fonts/<family>/*.ttf + OFL.txt — bundled fonts, used by BOTH the
                          dashboard (@font-face via /static/fonts) and the Pillow skins
```

## UI thread behaviour

- **DPI:** `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)` before any window. Widgets
  render at the DPI of their monitor (`scale = dpi / 96`) and re-render on `WM_DPICHANGED`.
- **Floating widget:** `WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_NOACTIVATE`.
  `WM_NCHITTEST` → `HTCAPTION` for native drag; `WM_EXITSIZEMOVE` saves `position` (clamped to a
  visible monitor work area); `WM_NCLBUTTONDBLCLK` → dashboard; `WM_NCRBUTTONUP` → menu.
  Hidden while a fullscreen app runs, when `hide_floating_in_fullscreen` is on (default off).
- **Taskbar strip:** sized to the taskbar height, placed left of `TrayNotifyWnd` (bottom
  taskbar), re-placed on `WM_DISPLAYCHANGE`, `WM_SETTINGCHANGE` (work area), `TaskbarCreated`
  and a 3 s fallback timer. Topmost re-asserted every 500 ms while visible (the shell re-raises
  the taskbar). Hidden while a fullscreen app runs. Click → dashboard; right click → menu.
- **Tooltip:** hover over either widget for ~400 ms shows a themed layered tooltip with exact
  percentages, reset times and pace; hides on `WM_MOUSELEAVE`.
- **Lock / screensaver:** `WTSRegisterSessionNotification` (lock/unlock events) plus the v1
  `should_pause()` check; while locked the data service is paused and animations stop.
- **Animation:** one timer, only while something animates (blink, flicker, glitch). Idle
  redraws happen only on new state, settings change, DPI change or hover. Target: ~0% CPU idle.
- **Settings changes** (from any thread) post `WM_APP_SETTINGS`; the UI thread re-reads
  settings and re-renders. Theme changes apply live.

## Interfaces (binding for builders)

### `themes/__init__.py`
```python
@dataclass(frozen=True) class Variant: id, name, default: bool, mode: str | None, colors: dict[str, str], extras: dict
@dataclass(frozen=True) class Theme:   id, name, tagline, fonts, shape, effects, bar, widget, variants: tuple[Variant, ...], extras
    def variant(self, variant_id: str | None) -> Variant          # unknown/None -> the default variant
load_themes(directory: Path | None = None) -> dict[str, Theme]     # validates; shipped files must pass
get_theme(theme_id: str | None) -> Theme                          # cached; unknown -> nostromo
resolve(settings) -> tuple[Theme, Variant]                        # theme_id / theme_variant / theme_mode (+ v1 "theme")
migrate_v1(settings) -> bool                                       # v1 "theme" name -> theme_id + theme_variant, once
css_variables(theme, variant) -> dict[str, str]                    # {"--bg": "#090909", ...} for the webui
font_path(theme, role: "display" | "body" | "mono", weight: str = "regular") -> Path | None   # bundled or system TTF
```
Gates "system" variants take the live Windows accent and light/dark mode through injected
callables (defaults read the registry via `ui/platform.py`). v1 map: Default→amber, Red, Blue,
Green, Purple, Cyan, Orange, White → the Nostromo variants of the same colour.

The system accent is made usable once, in `themes/colour.py` (`lift_accent`), and every surface reads the
result: Pillow, `/api/state`, `/api/themes` and the CSS variables, so the dashboard and the widgets show the
same colour. In OKLCH its lightness is raised to at least 0.69 in dark mode (lowered to at most 0.60 in light),
hue and chroma kept, gamut-mapped by chroma reduction as CSS Color 4 does, then stepped further until it has 3:1
against the card and widget surfaces. An accent already inside the bound is left alone. `extras.accent_fill`
(`--accent-fill`, `Palette.fill` in the Gates skin) is the colour normal usage is drawn in: the lifted accent,
unless that is within CIE76 ΔE 25 of one of the mode's severity colours (`ok`, `warn`, `alert`, `stale`,
`usage_warn`, `usage_alert`), in which case the variant's own JSON accent. A red Windows accent keeps its
icons, links and selection and never draws "normal" bars in the critical red.

### `render/`
```python
# model.py — plain data, no Win32
@dataclass class Row: key ("session" | "weekly" | "scoped:<name>"), label, percent (0..100+),
                      time_fraction (0..1 | None), severity ("normal" | "warning" | "critical"),
                      resets_at, resets_text, pace ("ahead" | "on_pace" | "behind" | None), share_text
@dataclass class WidgetModel: status: ApiStatus, status_text, age_text, rows: list[Row],
                              stale: bool, stale_message: str | None, blink_on: bool, updated_at
build_model(state: MonitorState | None, now, *, show_scoped="auto", blink_on=True) -> WidgetModel
# skins/__init__.py
get_skin(theme_id) -> Skin       # Skin has the five functions below
skin.floating_size(theme, model, scale) -> tuple[int, int]
skin.render_floating(model, theme, variant, scale, opts) -> PIL.Image (RGBA)   # opts: transparent_bg, text_fx, reduce_motion, phase
skin.render_taskbar(model, theme, variant, height_px, scale, opts) -> PIL.Image
skin.render_tooltip(model, theme, variant, scale) -> PIL.Image
skin.render_tray_icon(model, theme, variant, size_px) -> PIL.Image          # 16 / 20 / 24 / 32
```
Skins draw at 2× and downsample (LANCZOS); colours come only from variant tokens; fonts from
`usage_monitor/webui/fonts/` (bundled OFL) or Windows system fonts. Severity: the API's
`severity` when present, else 75 % warning / 90 % critical (approved 2026-10-21, #16). Pace:
ahead when usage − time > 10 points, behind when < −10, else on pace. The 10 is one number,
`windows.PACE_BAND_POINTS`: the pacing report, every widget and the dashboard (`PACE_BAND` in `app.js`,
which a test keeps equal) read it.

Sign-in copy. `WidgetModel.stale_message` is the neutral wording ("LOGIN EXPIRED - OPEN CLAUDE CODE");
a skin words the state as its approved mockup does and falls back to the neutral text where it has
none. Nostromo: the dashed auth box reads "AUTH LINK LOST." / "OPEN CLAUDE CODE TO RESTORE."
(`STALE_MESSAGES`, by status). Night City: the hazard band reads "RELINK // OPEN CLAUDE CODE"
(`theme.extras.copy.stale_auth`). Gates: the footer reads "Hmm, we lost touch. Sign in to Claude Code."

### `ui/`
```python
class UIHost:                                   # host.py
    def run(self) -> int; def quit(self)
    def post(self, fn) -> None                   # thread-safe: run fn on the UI thread
    def set_timer(self, ms: int, fn, *, repeat=True) -> int; def kill_timer(self, timer_id)
    def on_session_change(self, cb) / on_settings_change(cb) / on_display_change(cb) / on_taskbar_created(cb)
class LayeredWindow:                            # layered.py
    def __init__(self, host, *, draggable: bool, topmost=True): ...
    def update(self, image, x: int, y: int) -> None    # premultiplied BGRA via UpdateLayeredWindow
    def move(self, x, y); show(); hide(); destroy(); bring_to_top(); set_click_through(on)
    on_click / on_double_click / on_right_click(x, y) / on_drag_end(x, y) / on_hover(bool): callbacks
    hwnd; dpi; rect; visible
platform.py: taskbar_info(), work_areas(), dpi_for_point(x, y), is_fullscreen_foreground(),
             is_locked(), screensaver_running(), windows_accent_hex(), apps_use_light_theme(),
             clamp_to_work_area(x, y, w, h)
menu.py:     show_menu(host, items: list[MenuItem], x, y)        # MenuItem(label, action, checked, enabled, separator)
tray.py:     TrayIcon(host, tooltip, on_left_click, on_menu): set_images({16: img, 24: img, 32: img}),
             set_tooltip(text), notify(title, body), remove()    # re-adds itself on TaskbarCreated
```

### Dev isolation
`CUM_APPDATA_DIR=<dir>` puts settings, DB and log in `<dir>`. `CUM_DEV_INSTANCE=1` uses the
mutex `Global\ClaudeUsageMonitor-dev`, its own controller window class, and tags the tray
tooltip "(dev)", so a test build runs alongside an installed copy. `main.py --dump-frames <dir>`
renders every theme × variant × state × scale to PNGs without opening windows;
`--smoke-test <seconds> <report.json>` runs the real app, exercises the API server (state,
themes, live settings, the dashboard child, every statistics route and both exports), then exits
with a report. The report holds statuses, sizes and timings, never usage content.

- **The sign-in entry belongs to the installed copy.** A dev instance never reads, writes or
  repairs the HKCU Run entry (`ClaudeUsageMonitor`): its *Start with Windows* switch lives in
  memory (shown in `/api/state`, gone when the process ends, logged as skipped). Only the frozen
  installed build repairs the entry at launch, so a source checkout cannot repoint it.
  `--smoke-test` always runs as a dev instance, whatever the environment says. The API server is
  a dev server when its `dev` flag is set **or** `CUM_DEV_INSTANCE=1` is in the environment.
- **Dev server.** `python -m usage_monitor.ui.devserver [--port N] [--scenario
  normal|warning|critical|stale] [--db PATH] [--theme ID[/VARIANT]]` serves the real webui and API
  from a fake `MonitorState`, with settings in `CUM_APPDATA_DIR` (or a temporary folder).
  `--db PATH` serves `/api/analytics/*` and `/api/export` from that usage database through the same
  read-only opener the app uses (`server.open_statistics_store`: the file is never created and never
  upgraded, so a copy from an older schema answers `503` until the current app has opened it once);
  **pass a copy** anyway, since opening a WAL database leaves `-wal` and `-shm` files beside it.
  Its stored usage readings are old, so the pacing page shows only the fake state's current
  readings. Without `--db` a synthetic history
  (about 40 000 requests over a year, with sessions, tools, errors, pull requests and usage
  readings that lead up to the fake limits) is built in memory, so every page has data.
  `--theme nostromo`, `night_city/edgerun` or `gates/blue-light` sets the theme the page starts
  in (a theme without a variant gets its default one). `make_server(..., clock=None)` takes a callable
  that returns the current time; the synthetic history, the fake state and the API's report clock all read
  it, so a test pins the date (the command line uses the time quantised to the minute).

## Themes

`themes/<id>.json` follows the token schema in `design/mockups/` (id, name, tagline, fonts,
shape, effects, bar, widget sizes, variants[].colors with roles bg, surface, surface_alt,
border, border_dim, text, text_bright, text_dim, accent, accent_alt, ok, warn, alert, track,
glow, stale; plus extras). The same tokens feed the Pillow skins and the dashboard (CSS custom
properties `--bg`, `--text`, …). v1 theme names migrate to Nostromo variants.
Settings: `theme_id`, `theme_variant`, `theme_mode` (Gates: light / dark / system),
`reduce_motion`.

## Local API

`ThreadingHTTPServer` on `127.0.0.1:0`, started at app start (idle cost is negligible).

- Every request: `Host` must be `127.0.0.1:<port>` or `localhost:<port>` (DNS-rebinding guard),
  else `403`.
- `/api/*` requires header `X-Monitor-Token` (random per run, embedded in `index.html` as
  `<meta name="monitor-token">`), else `401`. The guards run first, so a refused request never
  reaches the database. No CORS headers are ever sent.
- **The shell.** `GET /` serves `index.html` with the per-run token and the active theme already in
  it: `data-theme` and `data-variant` on `<html>`, the colour tokens as inline custom properties
  (the `css_vars` of `/api/state`) with `color-scheme`, the `color-scheme` meta and the theme's own
  stylesheet link. The page sets the same values when its first state arrives, so the first frame is
  never another theme's. A theme that cannot be resolved, or a shell without the markers, is served
  as it is.
- Errors are JSON, `{"error": "<reason>"}`: `400` a bad query or body, `404` an unknown route or
  report, `405` the wrong method (with `Allow`), `413` a body over 64 KB, `501` a route that is not
  configured, `503` the statistics database cannot be used right now (the next request tries
  again), `500` a bug (logged, no details sent).
- **A hang-up is not an error.** A client that closes the connection mid-request or mid-reply (a closed
  dashboard window, an aborted fetch: `ConnectionError`, `TimeoutError`) is logged at debug and nothing more
  is sent. A bug still answers `500` and is logged with its traceback. The server's `handle_error` treats a
  failure outside a request the same way, so the console never prints a traceback for a closed tab.
- Statistics use a `Store` of their own (WAL), never the data service's, so they cannot block
  ingest. The server opens it on the first statistics request through its `analytics_store`
  callable, runs one report at a time, closes it and reopens it once when SQLite errors, and closes
  it when the server stops. The app's callable (`app.analytics_store`) opens `paths.db_path()` but
  never creates or upgrades it: a file that is not there yet, or not at this build's schema yet, is
  the data service's to make in its first cycle, and until then the routes answer `503`. The schema
  check opens the file read-only through a SQLite URI (`server._read_only_uri`): a drive path is
  `file:///C:/...?mode=ro` and a UNC path, for an app data folder redirected to a share, is
  `file:////server/share/...?mode=ro` (SQLite refuses a `file://server/` authority).

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | `index.html` with the token and the active theme injected (below) |
| GET | `/static/<path>` | webui assets (fonts cached; HTML/JS/CSS `no-store` in dev) |
| GET | `/api/state` | MonitorState + settings subset + theme + update info + version (ETag) |
| GET | `/api/themes` | themes with variants and tokens; the Gates `system-*` variants show the live Windows accent, as applied |
| GET | `/api/analytics/<name>` | statistics report: `cost`, `efficiency`, `pacing`, `history`, `sessions` (below) |
| GET | `/api/export?format=csv\|json` | daily rollups download (below) |
| GET | `/api/supporters` | supporters list (cached for an hour; failures come back as an empty list plus the reason) |
| POST | `/api/settings` | validated partial settings update (below) |
| POST | `/api/refresh` | `DataService.refresh_now()` |
| POST | `/api/update/check` · `/api/update/apply` · `/api/update/skip` | updater |

### Statistics: `GET /api/analytics/<name>`

The body is the report exactly as ARCHITECTURE.md "Phase 2 — stats" defines it. Query parameters
(a parameter that is not listed, a repeated one and a bad value are each a `400` naming the
parameter; numbers are plain ASCII digits):

| `<name>` | Parameters | Default | Report |
|---|---|---|---|
| `cost` | `window` | `cycle` | `cost_report`; `window` picks the breakdown window |
| `efficiency` | `window` | `month` | `efficiency_report` over that window (`resolve_window`) |
| `pacing` | none | | `pacing_report` |
| `history` | `days` 7..800 | `371` | `history_report` |
| `sessions` | `window`, `limit` 1..200 | `week`, `25` | `sessions_report` over that window |

`window` is `session`, `today`, `week`, `month`, `cycle` or `all`. An unknown `<name>` is a `404`.

- **Inputs.** The report's clock, the latest `MonitorState` (`usage`, `plan`; before the first
  state: no usage and "Unknown plan") and two settings, `plan_price_override` and `billing_day`.
  Limits whose window is over (`resets_at` not in the future) are dropped from `usage` first, the
  rule the data service applies to its own windows, so a reading from before an outage cannot pin
  the session or week to a period that has ended. Display names come from the data service's
  pricing table (the bundled one, loaded once, until the service has its own).
- **Caching.** A result is kept for 20 s, keyed by name, parameters, `billing_day` and
  `plan_price_override` (so changing either shows at once), at most 64 entries. The answer carries
  `X-Cache: HIT|MISS`. Reports are computed one at a time; a request for a cached report, and every
  other route, answers while another report is being computed.
- **Cost.** Measured on a database of 68 000 requests and 87 000 tool calls: a report that is not
  cached takes 1 to about 400 ms (the slowest are `efficiency?window=all` at 340 to 400 ms, by machine load, and
  `cost?window=all` at 310 to 330 ms; the usual ones 150 to 250 ms), a cached one 1 to 25 ms. The time is
  SQLite scanning the `requests` table (about 220 ms of the 340 in `efficiency?window=all` is `execute`,
  80 ms `fetchall`), not Python or this module; a cache or memory-map setting on the connection gains
  only 10 to 15 %, so the lever is in the analytics queries.

### Export: `GET /api/export?format=csv|json`

`format` is required; any other value is a `400`. The body is the daily rollups
(`analytics.daily_rollups`, one row per local day with requests, oldest first) as `text/csv;
charset=utf-8` (header row, CRLF line ends; an empty body when there are no rows) or
`application/json; charset=utf-8` (`{"basis", "rows"}`). Both carry `Content-Disposition:
attachment; filename="claude-usage-daily-YYYY-MM-DD.<ext>"`, the date being today's local date. The
rows are cached like a report (`X-Cache`); the file name is not.

### Settings: `POST /api/settings`

A JSON object of keys. An unknown key, a value that breaks its rule or a body that is not an
object is a `400` and nothing in the request is applied. The answer is `{"ok": true, "applied":
{…}, "settings": {…}}`; `/api/state` carries the same `settings` view.

| Keys | Rule |
|---|---|
| `theme_id`, `theme_variant`, `theme_mode` | `theme_id` and `theme_variant` are slugs of a shipped theme and its variants; `theme_mode` is `dark`, `light` or `system` |
| `show_floating`, `show_taskbar`, `float_transparent_bg`, `float_text_fx`, `taskbar_transparent_bg`, `taskbar_text_fx`, `hide_floating_in_fullscreen`, `reduce_motion`, `check_updates`, `launch_on_startup` | booleans |
| `show_scoped_rows` | `auto`, `always` or `never` |
| `poll_interval_ms` | whole number, 60 000 to 3 600 000 |
| `billing_day` | `null` (the calendar month), or a whole number 1 to 31 |
| `plan_price_override` | `null` (the detected price), or a number above 0 and at most 10 000 |
| `alerts_enabled`, `alert_on_threshold`, `alert_on_reset`, `alert_on_credits` | booleans |
| `alert_thresholds` | 1 to 5 different whole numbers from 1 to 100 |
| `alert_quiet_hours` | `null`, or `{"start": "HH:MM", "end": "HH:MM"}` in local 24-hour time (may wrap midnight) |
| `dashboard_bounds` | `null`, or `{"x", "y", "w", "h"}` whole numbers (width 320 to 8000, height 240 to 8000) |

The alert rules repeat `Settings`' own (a test keeps the two identical), so a bad value is refused
before anything is applied; a value `Settings` still refuses is a `400` too. There is no
`notifications_enabled` key: `alerts_enabled` and the three `alert_on_*` kinds replace it.

- **Theme completion.** `theme_id` alone stores the new theme's default variant with it (the stored
  variant belonged to the old theme); posting the current theme again keeps its variant. A whole
  Gates id such as `blue-light` is split into the accent (`blue`) and the mode (`light`) that
  `themes.resolve` reads; a `theme_mode` in the same request wins over the one in the id.
- **`launch_on_startup`** writes the HKCU Run entry. A dev server (see Dev isolation) keeps the
  choice in memory instead and logs that the entry was left alone.
- `ApiServer.apply_settings(body)` does the same in-process (the dev server's `--theme`); it
  raises `ValueError` where the endpoint would answer `400`.

## Dashboard process

`ClaudeUsageMonitor.exe --dashboard --port <n>` (source: `pythonw main.py --dashboard …`).
pywebview window titled "Claude Usage Monitor", native frame. After it's shown, the caption,
caption text and border colours are set from the theme via `DwmSetWindowAttribute`
(attributes 35/36/34, dark mode 20) on `window.native.Handle`, and again whenever the page
calls `pywebview.api.set_chrome(...)` after a theme change. Bounds persist in settings
(`dashboard_bounds`). If WebView2 is missing, the launcher opens the same URL in the default
browser instead.

- **Job object.** The launcher puts the child in a kill-on-close job object that only the monitor
  holds, so the dashboard ends with the monitor however the monitor ends (End task, `taskkill /F`,
  a crash) instead of leaving a window that polls a dead API. `DashboardLauncher.close()` closes
  the job too, which also ends WebView2 helpers the child left behind.
- **Exit codes** of `dashboard_main`: `0` a window that was shown and closed, `1` our own window call
  failed (a bug; the page is opened in the browser), `2` no token or port, `3` pywebview or WebView2
  is missing or would not start (the page is opened in the browser).
- **Window placement.** `keep_on_screen` runs on the `before_show` event: a saved position whose
  title bar is off every screen (an unplugged monitor) is moved fully onto the work area it overlaps
  most, or the nearest one, before the window appears. The child measures in its own coordinates, so `platform.monitors()` and
  `platform.work_areas()` are called with `set_awareness=False` there (pywebview sets the process's
  DPI awareness itself).
- **`DashboardApi` (the page's `pywebview.api`).** `get_token()`, `set_chrome(caption, text,
  border, dark)`, `open_external(url)` (https links to an allow-list of hosts) and
  `save_export(fmt)`. Names that start with `_` are not exposed.
  - `save_export("csv" | "json")` fetches `/api/export?format=<fmt>` from the monitor (127.0.0.1
    only, `X-Monitor-Token` header, redirects refused, 16 MB cap), then shows the native save
    dialog (`FileDialog.SAVE`, or the older `SAVE_DIALOG` where pywebview has no `FileDialog`)
    suggesting the name the monitor sent in `Content-Disposition`, and writes the chosen file
    through a temporary file, so a file
    that is overwritten survives a failed write. A name without an extension gets the format's. It
    returns `{"ok": bool, "path": str | null, "error": str | null}`; a cancelled dialog is `ok` false
    with `error` null, and a failure is `ok` false with a short reason (never a stack trace). The
    page names only the format: the path written to is the user's choice in the dialog. One dialog is
    open at a time. Fetching comes first, so a failed fetch never asks where to save nothing.
- **`find_window_of(pid)`** (formerly the private `_find_window_of`, kept as an alias) finds the
  visible dashboard window a process owns; the monitor closes the dashboard through it, by owner and
  not by title, because an installed copy beside a dev build can have a window of the same title.

## webui

- `index.html` shell + `app.js` (API client with token, polling `/api/state` every 2 s while
  visible with ETag, hash router: `#overview #limits #cost #nerds #history #settings #about`) +
  `charts.js` (dependency-free SVG: sparkline, bars, stacked bar, ring, heatmap).
- Markup is shared; each theme's CSS (`themes/<id>.css`) owns layout, shapes and effects and
  reads colours from CSS variables set from the theme tokens. Optional `themes/<id>.js` adds
  theme flavour (Nostromo boot sequence, Night City refresh glitch) and honours
  `reduce_motion` / `prefers-reduced-motion`.
- Fonts are bundled under `webui/fonts/` (OFL) — no network requests at runtime.
- **Text is measured, not guessed.** `charts.js` asks the page how wide a run of text is (a probe `<text>`
  element; answers are cached and `CUMCharts.refresh()` clears them when the font or size changes), so a bar
  chart's label column and the value text beside its bar fit the real font of any theme. A label that does not
  fit is cut in the middle with an ellipsis (`fitLabel`), so folders that differ at the end still differ.
- **Test hook.** With `window.__CUM_TEST__` set before the script runs, `app.js` hands its helpers back
  (`__CUM_TEST__.api`: `esc`, `fmtPct`, `paceOf`, `ratioCells`, `remainHTML`, `sessionsTable`, ...) and returns
  without starting the page. Nothing else sets it. `tests/webui_checks.js` loads `charts.js` and `app.js` in a
  node `vm` context with a fake DOM and prints what they do as JSON; `tests/test_webui.py` asserts on it. Those
  tests start node, so they need `CUM_UI_TESTS=1`, and `tests.yml` runs the file once more with it set.
- **Money.** Real money (the extra-usage Spend card) is shown in the account's own currency (`fmtMoney`, an
  `Intl.NumberFormat` with the ISO code the usage API reports; USD where it reports none). API-equivalent cost
  stays dollars. `/api/state.plan` carries the detected plan and price only: `plan_price_override` is a setting,
  applied where a price is used (the reports, and `effectivePlanPrice()` in the page), so setting or clearing it
  never rewrites state.
- **The plan multiple** is a row of cells of one plan-month each. Past 24 months a cell stands for N months
  (`ratioScale`) and its caption and aria-label say so ("1 cell = 5 months of Pro plan").
- **Time remaining.** Every limit card carries `.nm-remain` (`--r` the share of the window left, `.l` the label,
  `.v` the time), hidden by the page's own stylesheet. Night City's Edgerun draws it in place of the two reset
  facts, its two texts painted from one hard-stop gradient so they read over the fill and the track alike.
- **Window sizes.** From 1100 px wide each theme caps the overview grid (`.nm-grid { max-height }`, about a
  sixth more than the default window gives it), so a maximised window's extra height stays page and the cards
  keep their height. Night City shows its navigation decoration only where it fits the column (the reticle from
  720 px of window height; the code matrix from 735 px with two rows, in full from 786 px). Nostromo drops the
  caption after the title and the data age at 800 px and below, so REFRESH stays on screen.
