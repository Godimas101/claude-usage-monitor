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
ahead when usage − time > 10 points, behind when < −10, else on pace.

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
mutex `Global\ClaudeUsageMonitor-dev` and tags the tray tooltip "(dev)", so a test build runs
alongside an installed copy. `main.py --dump-frames <dir>` renders every theme × variant ×
state × scale to PNGs without opening windows; `--smoke-test <seconds> <report.json>` runs the
real app, exercises the API server, then exits with a report.

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

- Every request: `Host` must be `127.0.0.1:<port>` or `localhost:<port>` (DNS-rebinding guard).
- `/api/*` requires header `X-Monitor-Token` (random per run, embedded in `index.html` as
  `<meta name="monitor-token">`). No CORS headers are ever sent.
- Analytics queries use their own read-only `Store` (WAL) so they never block ingest.

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | `index.html` with the token injected |
| GET | `/static/<path>` | webui assets (fonts cached; HTML/JS/CSS `no-store` in dev) |
| GET | `/api/state` | MonitorState + settings subset + theme + update info + version (ETag) |
| GET | `/api/themes` | themes with variants and tokens |
| GET | `/api/analytics/<name>` | Phase 2 analytics (cost, efficiency, pacing, history, sessions …) |
| POST | `/api/settings` | validated partial settings update |
| POST | `/api/refresh` | `DataService.refresh_now()` |
| POST | `/api/update/check` · `/api/update/apply` · `/api/update/skip` | updater |
| GET | `/api/export?format=csv\|json` | daily rollups download |

## Dashboard process

`ClaudeUsageMonitor.exe --dashboard --port <n>` (source: `pythonw main.py --dashboard …`).
pywebview window titled "Claude Usage Monitor", native frame. After it's shown, the caption,
caption text and border colours are set from the theme via `DwmSetWindowAttribute`
(attributes 35/36/34, dark mode 20) on `window.native.Handle`, and again whenever the page
calls `pywebview.api.set_chrome(...)` after a theme change. Bounds persist in settings
(`dashboard_bounds`). If WebView2 is missing, the launcher opens the same URL in the default
browser instead.

## webui

- `index.html` shell + `app.js` (API client with token, polling `/api/state` every 2 s while
  visible with ETag, hash router: `#overview #limits #cost #nerds #history #settings #about`) +
  `charts.js` (dependency-free SVG: sparkline, bars, stacked bar, ring, heatmap).
- Markup is shared; each theme's CSS (`themes/<id>.css`) owns layout, shapes and effects and
  reads colours from CSS variables set from the theme tokens. Optional `themes/<id>.js` adds
  theme flavour (Nostromo boot sequence, Night City refresh glitch) and honours
  `reduce_motion` / `prefers-reduced-motion`.
- Fonts are bundled under `webui/fonts/` (OFL) — no network requests at runtime.
