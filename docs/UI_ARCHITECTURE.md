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
    widgets.py            FloatingWidget, TaskbarWidget, Tooltip: layout/position, renderer calls and the
                          pointer response (see "Pointer response")
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
    skins/<theme>.py      render_floating / render_taskbar / render_tooltip / render_tray_icon, and the optional
                          content_insets / hover_frames
  themes/
    __init__.py           load + validate theme JSON, variants, v1 migration, system accent
    colour.py             OKLCH, WCAG contrast and CIE76 distance in pure Python (the accent's lift)
    nostromo.json  night_city.json  gates.json
  webui/                  index.html, app.js, charts.js, motion.js + motion.css (the shared half of the motion
                          layer), themes/<theme>.css, <theme>.motion.css and <theme>.js (the theme's look,
                          its motion and its scripted flavour),
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
- **Content insets.** A frame may carry a transparent margin round the card the user sees (Gates draws its soft
  shadow there). A skin reports it through the optional `content_insets(surface, theme, model, scale)`, and the
  placement arithmetic (`floating_origin`, `strip_origin`, `tooltip_origin`, each with an optional `insets`) measures
  from the visible card: the floating widget's default corner (`FLOAT_MARGIN`, 12 px at 100 %) and its clamp to a
  work area on drop and on a DPI change, the strip's centring and gap to the notification area, and the tooltip's gap
  (`TOOLTIP_GAP`, 8 px at 100 %) to the widget and to the screen edge. The visible gap is then the same in every theme
  (at 144 DPI the tooltip card sits exactly 12 px above the strip window and the floating card, and the floating card
  18 px from the work-area edges). `skin_insets()` throws away an answer that is unusable (negative, not numbers, as
  large as the frame), so a widget can always be placed. The saved `position` is still the window's.
- **Pointer response.** When the pointer enters the floating widget or the strip, a skin that offers
  `hover_frames(frame, surface, scale, opts)` answers with a `model.HoverPlay`: Nostromo one brightened frame (gain
  1.22 and 30 % bloom), held while the pointer stays; Night City three 60 ms RGB-split glitch frames, once; Gates a
  four-step spring (1, 3, 2, 2 logical px, 40 ms apart) that lifts the card, held, with a 1 px settle frame on the
  way out (the lift stays inside the frame's top margin). The tooltip has no response. Each widget runs a small state
  machine (idle, play, hold, leave) off the layered window's `on_hover`:
  - The skin is asked once per normal frame. Plans are cached, two per surface (one per blink phase), and cleared when
    the frame's cache key changes. Frames are converted to the premultiplied format once, and each also covers at
    least 1/255 wherever the normal frame was covered: Windows lets the pointer through pixels with no coverage, so a
    card that lifts away from under a resting pointer would otherwise end the hover it is answering.
  - Playing uses one-shot host timers. At idle there is no timer, no redraw and no derive, so the idle cost is what it
    was. A response frame is a lookup and one window update (measured on a real layered window: median 0.07 to
    0.57 ms, worst 1.9 ms at 200 %); the first hover per frame content also derives it (Nostromo 1.4/3.0/4.7 ms,
    Night City 0.9/1.6/3.8 ms, Gates 4.6/9.7/19.9 ms at 100/150/200 %).
  - It does not start under `reduce_motion`, with Windows' "Show animations in Windows" switch off
    (`SPI_GETCLIENTAREAANIMATION`, read through `os_animations_enabled()`, or the platform's `animations_enabled`),
    or while the widget is hidden or being dragged. It ends on `reduce_motion`, a hide, a fullscreen app, destroy, a
    theme change to a skin that cannot hold, or a failing swap. The blink keeps running under a held Nostromo frame.
    Hover frames do not count in `stats.rendered`.
  - `FloatingWidget` and `TaskbarWidget` take an optional `animations_enabled` callable (tests and the smoke run).
- **Lock / screensaver:** `WTSRegisterSessionNotification` (lock/unlock events) plus the v1
  `should_pause()` check; while locked the data service is paused and animations stop.
- **Animation:** one timer, only while something animates (blink, flicker, glitch, a pointer response). Idle
  redraws happen only on new state, settings change, DPI change or hover. Target: ~0% CPU idle.
  `reduce_motion` stops the blink and the pointer response; Windows' own animation switch stops the pointer response.
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
                              .lamp -> "live" | "paused" | "stale" | "offline" | "limited" | "loading"   # LAMP_KINDS
build_model(state: MonitorState | None, now, *, show_scoped="auto", blink_on=True) -> WidgetModel
SURFACE_FLOATING | SURFACE_TASKBAR | SURFACE_TOOLTIP          # the surfaces the optional skin methods name
Insets = (left, top, right, bottom); NO_INSETS = (0, 0, 0, 0)  # transparent margin of a frame, in its own pixels
@dataclass(frozen) class HoverPlay: frames, delays_ms, hold=False, leave=(), leave_ms=40   # plain data, see below
# skins/__init__.py
get_skin(theme_id) -> Skin       # Skin has the five functions below
skin.floating_size(theme, model, scale) -> tuple[int, int]
skin.render_floating(model, theme, variant, scale, opts) -> PIL.Image (RGBA)   # opts: transparent_bg, text_fx, reduce_motion, phase
skin.render_taskbar(model, theme, variant, height_px, scale, opts) -> PIL.Image
skin.render_tooltip(model, theme, variant, scale) -> PIL.Image
skin.render_tray_icon(model, theme, variant, size_px) -> PIL.Image          # 16 / 20 / 24 / 32
# optional: the widgets look these up with getattr, so a skin (or a test double) without them answers "nothing"
skin.content_insets(surface, theme, model, scale) -> Insets                 # NO_INSETS when absent
skin.hover_frames(frame, surface, scale, opts) -> HoverPlay | None          # the pointer response, derived from `frame`
```
Skins draw at 2× and downsample (LANCZOS); colours come only from variant tokens; fonts from
`usage_monitor/webui/fonts/` (bundled OFL) or Windows system fonts. Severity: the API's
`severity` when present, else 75 % warning / 90 % critical (approved 2026-10-21, #16). Pace:
ahead when usage − time > 10 points, behind when < −10, else on pace. The 10 is one number,
`windows.PACE_BAND_POINTS`: the pacing report, every widget and the dashboard (`PACE_BAND` in `app.js`,
which a test keeps equal) read it.

**Lamp kinds.** A status with no room for words (the strip, the tray icon) is drawn by `WidgetModel.lamp`
(`model.LAMP_KINDS`): `live`, `paused`, `stale` (sign-in lost or missing: the user acts), `offline` (no connection),
`limited` (the usage API is throttling the monitor; both of those retry by themselves) and `loading`. Each skin has its
own mark for a kind, and a test (`tests/test_skin_states.py`, every theme and variant) holds the four that matter apart:
tray icons differ by at least max(8, size² / 32) pixels and strips by at least 16 × scale² pixels (for Nostromo only
inside the mark's box, at most 10 × scale px).

| | stale | offline | limited | paused |
|---|---|---|---|---|
| Nostromo strip mark | ring | X | three dots | two bars |
| Nostromo tray centre | empty dashed ring | X | three dots | two bars (the ring dashes drop to the track colour behind a glyph) |
| Night City tray | dim meter | X, in the stale colour | hourglass, in the stale colour | two columns of cells, level kept |
| Gates strip lamp | filled disc | hollow ring | half disc | accent ring |
| Gates tray | dashed ring | dashed ring with an X | dashed ring with three dots | ring at its level, dimmed |

Night City's strip names the state in text on its plate, so it is unchanged. Gates draws `loading` like `offline` (a hollow
ring) and `no_credentials` like `stale`, which is what the lamp kinds say.

**Nostromo tray icon.** The icon is one opaque plate (the variant's `crt` token) with an opaque track, so it reads the same on a
dark and a light taskbar: the track is the translucent token laid over the plate, then dimmed until it sits at least 3.2 : 1
under the weakest lit colour (measured 3.21 to 8.74 over 8 variants, 3 severities and 4 sizes; plate against `#f3f3f3` at
least 17.9 : 1).

**Gates floating card.** It shows the session and weekly rows, then a row for every scoped limit the model carries, in
every state, and the footer last: 128 logical px tall with two rows, 27 px more for each further row (at 150 % the
frame, shadow margin included, is 264 px tall with two rows and 304 px with three). `content_insets` is `round(24 × scale)` on every side for the floating widget and
the tooltip and zero for the strip; Nostromo and Night City have none.

**Night City Relic and Mox** use the `relic_a` and `mox_a` palettes (ids, names and keys as before): the Relic signal is
`#0b8eb9` (ink on it 5.16 : 1), the LIVE cyan `#7ddee7`, plates `#0b8eb9` and body text `#d7d4d0`; the stale colours are
`#a1866f` (Relic) and `#989898` (Mox), at least CIE76 ΔE 28 from the text, accent and status colours and at least 4.69 : 1
on every surface. Body text is at least 6.16 : 1 (Relic) and 5.36 : 1 (Mox) on any surface.

**Frame dump.** `render/dump.py` renders `DUMP_STATE_NAMES`: the ten `STATE_NAMES` (`normal3`, `normal2`, `warning`,
`critical`, `stale`, `offline`, `paused`, `rate_limited`, `no_credentials`, `loading`) plus `SCOPED_STATE_NAMES`
(`stale3`, `offline3`, `paused3`, `rate_limited3`: the not-live states with the scoped row the live data would still
carry, which is what a widget shows on an account that has the row). The contact sheet leaves the scoped ones out.

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
             set_tooltip(text), notify(title, body) -> bool, remove()    # re-adds itself on TaskbarCreated
```

`TrayIcon.notify` answers True only when the shell took the balloon. A shell that refuses the modify (the icon is gone, as after an
Explorer restart) gets the icon added again and the same balloon sent once more, and the answer is that second call's, so a toast
that was lost is never counted as shown (the update announcement is then offered again, not marked as told).

### Dev isolation
`CUM_APPDATA_DIR=<dir>` puts settings, DB and log in `<dir>`. `CUM_DEV_INSTANCE=1` uses the
mutex `Global\ClaudeUsageMonitor-dev`, its own controller window class, and tags the tray
tooltip "(dev)", so a test build runs alongside an installed copy. `main.py --dump-frames <dir>`
renders every theme × variant × state × scale to PNGs without opening windows;
`--smoke-test <seconds> <report.json>` runs the real app, exercises the API server (state with its
`update` block, themes, live settings, the dashboard child, every statistics route and both exports), then exits
with a report. The report holds statuses, sizes and timings, never usage content; it also says whether the
six-hourly update re-check timer is armed (`update_recheck`).

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
| GET | `/api/state` | MonitorState + settings subset + theme + `update` block + version (ETag) |
| GET | `/api/themes` | themes with variants and tokens; the Gates `system-*` variants show the live Windows accent, as applied |
| GET | `/api/analytics/<name>` | statistics report: `cost`, `efficiency`, `pacing`, `history`, `sessions` (below) |
| GET | `/api/export?format=csv\|json` | daily rollups download (below) |
| GET | `/api/supporters` | supporters list (cached for an hour; failures come back as an empty list plus the reason) |
| POST | `/api/settings` | validated partial settings update (below) |
| POST | `/api/refresh` | `DataService.refresh_now()` |
| POST | `/api/update/check` · `/api/update/apply` · `/api/update/skip` | updater (see "Updates") |

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

### Updates

`GET /api/state` carries `update: {available, version, url, can_self_update, skipped, info, skip_version}`
(`ApiServer._update_view`, built from the app's `info` callback and the `skip_version` setting):

| Key | Meaning |
|---|---|
| `available` | a newer release was found (also when the user skipped it) |
| `version` | its version, only when it is plain (`[0-9A-Za-z][0-9A-Za-z._+-]{0,39}`, `server.plain_version`), else `null` |
| `url` | its release page, only a `https://github.com/...` address, else `null` |
| `can_self_update` | true only for a literal `true`: *Update now* installs it in place (the installed build, this project's own installer URL, a SHA-256 to check); false means it opens the release page |
| `skipped` | `skip_version == version` |
| `info` | what the check returned, plus `url` and `can_self_update` (`UpdateTracker.api_info`) |
| `skip_version` | the skipped version, `""` for none |

The page shows its banner when `available and not skipped`. `version` and `url` come off the network, so only a plain version and
a github.com page reach the page, a menu or a toast. The three routes answer `{"ok": true, "result": ...}`: `check` runs a check
and waits for it (up to 8 s; `result` is `{"available", "version", "checked"}`, and a release it finds is counted as told about,
since the user is looking at the page, so no toast follows), `apply` installs (`{"started": true, "mode": "installer"}`) or opens
the release page (`"mode": "browser"`), and `skip` sets `skip_version` (`{"skipped": <version>}`). The page re-reads `/api/state`
after `check` and `skip`. `checked` is false when the monitor could not look: the lookup raised (offline, GitHub's rate limit, a
bad answer: `update_check.check_async` hands the error to its `on_error`, which `UpdateTracker.check_now` collects) or did not
answer in time. The page then says "Could not reach GitHub; try again later." and never "You are up to date", which only a
lookup that ran and found nothing may say; a monitor that sends no `checked` is taken to have looked.

The monitor looks for a release at start and every six hours (`app.UPDATE_RECHECK_S`, one UI-host timer that is always armed
and gated on `check_updates` when it fires; a check that fails is a debug line, nothing more, and the next one tries again). A
release that was found, is not skipped, is newer than the setting `notified_version` and arrives while `check_updates` is on is
announced once with a tray toast, "Claude Usage Monitor vX.Y is available" (a click opens the dashboard), by
`MonitorApp._announce_update` on the UI thread. The toast is held back while nobody would see it (session locked, a screensaver, a
fullscreen app in front, the user's quiet hours, `app.in_quiet_hours`), and the release stays untold, so the next state, an
unlock or the next check tries again. A shell that refuses the toast is retried too; a bug showing it is logged once and counts
as told. The usage-alert switches (`alerts_enabled`, `alert_on_*`) do not apply to this toast; `check_updates` and the quiet hours
do. `notified_version` is only ever raised (under a lock) and is an unknown key to the settings API, so the page can neither read
nor write it. The same release is offered in the tray menu (*Update to vX*, unless skipped) for as long as it is on offer.

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
- **Window background.** `dashboard_main` reads `/api/state` once before it makes the window (`_read_state`: the saved bounds and
  the theme come from that one call) and passes pywebview's `background_color`, the colour the native window shows until the page
  has painted, as the active theme's page background (`theme.css_vars["--bg"]`, `_window_background`; the server bakes the same
  colour into the first paint). A light Gates window opens light, not as a black rectangle that jumps to light. `#090909` is
  the fallback when the state cannot be read or holds no `#rrggbb` (pywebview takes nothing else).
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
  visible with ETag, hash router: `#overview #limits #cost #efficiency #history #settings #about`) +
  `charts.js` (dependency-free SVG: sparkline, bars, stacked bar, ring, heatmap). The shell loads, in this order and all
  from `/static/`: `motion.css`, the three `themes/<id>.motion.css` sheets (each scoped by `html[data-theme]`, so all three can
  load whatever the theme) and `motion.js` in the `<head>` (so a boot curtain can start before the body is parsed), then
  `charts.js` and `app.js` last: `app.js` starts the page. A test pins the order.
- **Efficiency.** The page that was called *Nerds only* is `#efficiency`. `PAGE_ALIASES = { nerds: "efficiency" }` opens an old
  link or bookmark and rewrites the address with `history.replaceState`. Its saved window preference is `efficiencyWindow`;
  an old `nerdsWindow` is read when there is no new value. Its Context card says "prompt tokens per request, API count" and
  its compaction figure "Size at compaction ... Claude Code's own count": the two come from different counters and can differ.
- Markup is shared; each theme's CSS (`themes/<id>.css`) owns layout, shapes and effects and
  reads colours from CSS variables set from the theme tokens. Optional `themes/<id>.js` adds
  theme flavour (the boot, the refresh glitch, the loading screen) and registers the theme's motion hooks; everything that
  moves honours `reduce_motion` / `prefers-reduced-motion` (see "Motion"). The header comment of `app.js` is the markup
  contract for theme authors: shell, page, card, figure, control and data-hook classes, the pending marks and the hooks.
- **A page is never empty (pending).** A page opens as its whole card layout in the first frame: every card, title, label,
  chart frame and bar track, built from placeholder data of the right shape (`skelUsage`, `skelCost`, `skelEfficiency`,
  `skelHistory`, `skelPacingBlock`, `skelWindow`) for whatever has not arrived (a report, or the first `/api/state`). Cards
  are laid over what is on screen part by part (`paint` / `reconcile`): a card is filled in place (its element and frame stay,
  its contents are replaced) and a card whose markup is unchanged is not touched. A card says how it is doing in
  `data-state="pending | ready | error"` and which reports it draws from in `data-needs`; a figure (`.nm-big`, `.nm-stat-v`, a
  table cell, `.nm-toolbar-n` ...), a bar (`.nm-cells`, `.nm-ratio`) or a chart whose value is not in carries `data-pending`
  and `aria-hidden`, and the structural stylesheet makes its text transparent (the text only sizes the slot) while the theme
  draws what stands in its place. Once in: `data-final` (a figure's final text), `data-fill` (a bar's 0 to 100) and, for what is
  new or changed in the last paint, `data-fresh`. A failed report replaces only its own cards' contents with one `.nm-error`
  line (and settles the toolbar note beside the window buttons, which is outside any card); `data-updating` marks a card whose
  numbers are on screen and are being fetched again, only after 600 ms. The last good report of each page and window is kept
  in memory (never persisted), so a revisit shows the old numbers at once and refreshes behind them. At 1280 px the card boxes
  are identical before and after the data lands in all three themes; a user with more or fewer rows than the placeholder shape
  (13 months, 9 built-in tools, 2 MCP servers, one scoped limit) sees a card resize when the data arrives. The Cost page is held
  to the same standard at 1200x780 and 1000x700, where no row of a taller card hides a jump: its Fast mode card says the same
  explainer sentence in every state (the placeholder, the report with no fast-mode use and the report with some, as a full-width
  tile so its sub-lines do not wrap differently) and its thread tiles are sized for a real figure, and a test in a real web view
  holds the reports back, lets them land and fails if any card changed height (`test_server.py`, `CUM_UI_TESTS=1`). The Overview's
  cost, efficiency and model cards draw from the cost report like the Cost page does: while it is on its way they are pending,
  and when it fails they say so (`data-state="error"` and the page's `problem` banner) instead of staying pending for good.
- **Update banner.** `.nm-update[role=status]` is the first banner of every page but Settings, whose System card has the same
  buttons (the toast says "Open the tray menu or the dashboard to update", and the dashboard may be on any page: `pageModel`
  adds the banner to every page's top parts; the About page, which is built once and not as parts, keeps it in a
  `#about-update` slot that `syncAboutBanner` fills only when the markup changed, so a poll never drops the focus of someone
  tabbing through its buttons). It is present while `/api/state`'s `update` block says `available and not skipped` and carries
  a version: "Claude Usage Monitor vX.Y is available", the sentence "Update now installs it and restarts the monitor."
  (`can_self_update`) or "Update now opens the release page.", and three buttons.
  *Update now* posts `/api/update/apply` and the status line says the mode (`installer` or `browser`) or the error; *Release
  notes* opens the github.com page through `open_external` (shown only when there is one); *Skip this version* posts
  `/api/update/skip` and hides the banner at once, rolling back on failure. The status text is tied to the release it was
  about, so it never shows against a newer one. The dev server fakes no update (its `info` callback returns nothing): to see
  the banner, answer `/api/state` with an `update` block (a Playwright route does it).
- **Forecast.** A limit's forecast runs from now at the average rate so far (used / elapsed, `averageRateForecast`) to where
  it crosses 100 % (the end is marked "100% ~ <time>", the same moment as the card's "Limit reached") or to the reset when it
  stays below; the end label and the "Projected end" fact read the same value, so they cannot disagree. Scoped limits use the
  same function.
- Fonts are bundled under `webui/fonts/` (OFL) — no network requests at runtime.
- **Text is measured, not guessed.** `charts.js` asks the page how wide a run of text is (a probe `<text>`
  element; answers are cached and `CUMCharts.refresh()` clears them when the font or size changes), so a bar
  chart's label column and the value text beside its bar fit the real font of any theme. A label that does not
  fit is cut in the middle with an ellipsis (`fitLabel`), so folders that differ at the end still differ.
- **Test hook.** With `window.__CUM_TEST__` set before the script runs, `app.js` hands its helpers back
  (`__CUM_TEST__.api`: `esc`, `fmtPct`, `paceOf`, `ratioCells`, `remainHTML`, `sessionsTable`, ...) and returns
  without starting the page. Nothing else sets it. `tests/webui_checks.js` loads `charts.js` and `app.js` in a
  node `vm` context with a fake DOM and prints what they do as JSON; `tests/test_webui.py` asserts on it. Those
  tests start node, so they need `CUM_UI_TESTS=1`, and `tests.yml` runs them (and `tests/test_motion.py`) once more with it set.
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

## Motion

The dashboard's motion layer is one shared engine and three looks, one per theme. It is optional: without `motion.js` and the
theme's script the page is complete and still, and every piece of it can be absent. What moves is CSS (transform, opacity,
clip-path and a registered custom property, `@property --t`; filter, shadow and colour only on small things); the scripts decide
when, and draw the characters of a figure that types, decodes or rolls in.

| File | What it is |
|---|---|
| `webui/motion.js` | The engine, `window.CUM_FX`: one `requestAnimationFrame` loop for every character-level job (`typer`, `decoder`, `roller`), ghost text, the pending and arrival watcher, `FX.director(cfg)`, the boot curtain and the reduced-motion listener |
| `webui/motion.css` | What every theme shares: the ghost text, the typing cursor, `@property --t`, `.fx-land`, the boot curtain and the reduced-motion guard. It holds no colour and no timing |
| `webui/themes/<id>.motion.css` | The theme's look: entrance, hover, placeholders, arrival and boot. Every rule is scoped by `html[data-theme="<id>"]`, colours come only from the theme's tokens |
| `webui/themes/<id>.js` | The theme's director: registers `window.CUM_THEME_FX[id] = { start, enter, cancel, arrive }` and plays the boot |

**Hooks (`app.js` calls them; the header comment of `app.js` is the contract).** `start()` runs once the theme's script has
loaded and again whenever the theme is applied. `enter(main, page)`: the page just drawn into `#main` is due its entrance (a
tab, a link, back or forward, the dashboard starting, a theme change; not a refresh, a variant change or a card filled in
place). It is called once the theme's stylesheet and script are in and the window is shown, and never waits for a report.
`cancel(main)`: finish what plays, at the final state, before another page or theme replaces the page and when `reduce_motion`
turns on (not when a card is filled in place). `arrive(card, info)`: a card's numbers arrived or changed in place; `info` is
`{ page, card, first, from, to, fresh, entering, reduced }` (`from` and `to` are the card's `data-state` before and after).
For code that is not a theme script, `cum:card-data` bubbles from the card with the same `info` and `cum:page-rendered` fires
on `#main` with `{ page, opened, reduced, updated, added }` (the card keys). With `reduce_motion` or `prefers-reduced-motion`
`enter` and `arrive` are never called (`cancel` still is, since it only finishes what was playing), and the files add no
animation of their own.

**Classes and marks.** On `#main`: `fx-run` (the theme's entrance is playing; the theme sets and clears it, and every entrance
rule hangs on it and fills `backwards`, so removing it leaves exactly the markup `app.js` wrote) and `fx-quiet` (the page was
just redrawn and the pointer has not moved since: one-shot hover animations wait, and a hover transition is taken away, never
the hover look itself; `app.js` and the engine both set it and both clear it on the first pointer or wheel movement). On a
slot: `data-pending` (read by the engine, written by `app.js`), `data-fx-pend="num | text | bar | rail | chart"` (written by the
engine for the themes' CSS), `.fx-land` (a bar, rail or chart whose data just arrived plays its fill from its own start, with
no page stagger), and `data-fx` / `data-fx-d` (a figure whose characters the script draws: the ghost text). The orders the CSS
reads are custom properties the engine writes (`--fx-i` a card's order, clamped to 8 so a long page ends inside its budget,
`--fx-k` a cell's order in its bar, `--fx-c`, `--fx-p`, `--fx-d`, `--fx-r` for chart columns, pips, dots and rows, `--fx-cn` a
heat map's column count (one write on the map's svg, from the `data-cols` attribute `charts.js` puts there; no write on its cells),
`--gk-d` and `--gk-l` Gates' bar timing).

**Engine rules.** The real text of a figure is never changed while it is drawn: a decoded or rolling figure is drawn by its
`::after` from `data-fx` while the real text sits transparent, so the theme scripts that watch the page for text see nothing
happen and a screen reader reads the final figure. A frame only writes (a text node, an attribute, a class) and every read is
batched before the writes; layouts run about one per frame. The state of a ghost figure is an attribute, never a class (Night
City keys its figures by `className`). Arrival is detected by a microtask observer, before the browser paints the markup that
was just written, with `app.js`'s `arrive` as a second trigger; an arrival plays once (600 ms dedupe). Loops run only while a
slot is pending: after the data lands there are no DOM writes and no layer loops, and none while the window is hidden. A figure
that changes value without having been pending first is not animated (it just changes).

**Boots.** Each theme has a boot, played once per browser session (`sessionStorage` flags `nm-boot-session`, `nc-boot-session`,
`g-boot-session`, always inside `try`) and never under reduced motion; a click, Esc, Enter or Space skips it. While a boot is
due and the theme's script has not taken over, `html[data-fx-boot]` hides the dashboard (`.nm-content`) so it never flashes
before the overlay (the curtain also lifts after 3.5 s whatever happens), and the entrance parks until the overlay starts to
leave (`FX.boot.begin()`, `end()`).

| | Nostromo: a CRT terminal that prints the page | Night City: a HUD that jacks in | Gates: Windows 11 Fluent |
|---|---|---|---|
| Entrance | cards flicker on under a phosphor line (about 1.1 s, card *i* at 40 + 55 *i* ms), figures typed behind a block cursor, pips light cell by cell | cards tear on under a cyan scan line (about 0.9 s, 40 + 45 *i* ms), figures decode and lock left to right, RAM cells fill with a colour split that snaps clean | cards fade and rise 8 px (about 0.8 s, 30 + 30 *i* ms), figures roll up, bars fill on the decelerate curve, rails grow |
| Placeholder | scanline block and blinking block cursor, unlit pips, "AWAITING DATA" on a chart | glyph noise at about 12 frames a second (only while shown), a scanning track on a bar, "SYNCING" on a chart | Fluent shimmer skeleton |
| Arrival | typing and pips as in the entrance | decode and fill as in the entrance | roll and fill as in the entrance |
| Hover (only where a pointer can hover) | the card brightens (a persistent look) and settles once (200 ms) | the card jitters and tears once (190 ms), figures and titles get a colour fringe | the card lifts 2 px on a spring (a persistent look) |
| Boot | the typed boot log, then the tube collapses (3.65 s with its 700 ms collapse) | jack-in: a breach-protocol panel, a log that resolves, the RAM filling, one RGB split, "access granted" (2.04 s and a 240 ms collapse) | a Windows 11 loading screen: a Mica-like surface from the theme tokens (dark and light), the app glyph, the name, five orbiting dots and "Getting your usage ready" (1.35 s and a 333 ms leave), crossfading into the page |

In every theme a heat map comes in as one unit, wiped on from the left a column at a time (4 ms a column, at most 400 ms, from
`--fx-cn`): one `clip-path` animation on the map's svg, in steps in Nostromo and Night City and on the decelerate curve with a
fade in Gates. It used to be one animation a cell (about 540 for a year of days, each with its own timeline and style write),
which left History at 46 to 59 of 90 frames in its first 1.5 s at a 4x CPU slowdown, and gave Gates two 50 ms long tasks at full
speed; one wipe keeps 77 to 83 frames and starts no long task at full speed. Gates uses Fluent's durations (167, 250 and 333 ms) with its decelerate curve `cubic-bezier(0.1, 0.9, 0.2, 1)`, its accelerate
curve `cubic-bezier(0.7, 0, 1, 0.5)` and a damped spring written as a CSS `linear()` easing (browsers without `linear()` get a
bezier). Nostromo and Night City move in steps, never in curves. A persistent hover look keeps its look through a data refresh
(`fx-quiet` only removes its transition); a one-shot hover animation replays once, on the first pointer movement after an
update changed the numbers under a resting pointer, because that is when `fx-quiet` ends (a page that was redrawn never moves
by itself).

**Reduced motion.** Everything checks `data-reduce-motion="1"` on `<html>` (the `reduce_motion` setting) and
`prefers-reduced-motion` (which WebView2 takes from Windows' animation setting). `app.js` announces only the setting, so
`motion.js` also listens to the OS preference (`FX.onReduced`): when either turns on in the middle of an entrance or a boot, the
entrance, the boot overlay and the noise end within 0.5 s at their final state. Placeholders are static ones, no hover
animation starts, and `html[data-reduce-motion="1"]` and `@media (prefers-reduced-motion: reduce)` switch off any animation and
transition in `motion.css`.

**Tests.** `tests/test_motion.py` has static checks of the scripts and sheets (the budgets, the properties animated, the
hygiene `tests/test_webui.py` keeps for the other scripts) and node checks that run `motion.js` on a minimal fake DOM and each
theme script (`themes/<id>.js`) with no `motion.js` at all (`tests/theme_scripts_checks.js`: the script has to load, start twice,
survive its timers, a theme switch and reduced motion without throwing, because the page is complete without the engine); the node
ones need `CUM_UI_TESTS=1`, start node with `CREATE_NO_WINDOW`, and `tests.yml` runs them with `tests/test_webui.py`. What a
real browser does (entrances, boots, hover, reduced motion, placeholders, a failed report) is checked with Playwright against
the dev server (`python -m usage_monitor.ui.devserver --theme <id>[/<variant>]`), which the tests do not cover.
