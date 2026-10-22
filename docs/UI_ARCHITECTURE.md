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
                          fonts/ (bundled OFL fonts)
assets/fonts/             TTFs for Pillow (same families as webui/fonts)
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

## Renderer contract

```python
class WidgetModel:          # render/model.py — plain data, no Win32
    status: ApiStatus; status_text: str; rows: list[Row]   # session, weekly, scoped (opt-in/auto)
    blink: bool; stale: bool; updated_text: str
class Row: label, percent (0..100), time_fraction (0..1 | None), severity ("normal|warning|critical"),
           resets_text, pace ("ahead|on_pace|behind" | None), share_text (scoped limits)

skin.render_floating(model, theme, variant, scale, opts) -> PIL.Image (RGBA)
skin.render_taskbar(model, theme, variant, height_px, scale, opts) -> PIL.Image
skin.render_tooltip(model, theme, variant, scale) -> PIL.Image
skin.render_tray_icon(model, theme, variant, size_px) -> PIL.Image      # 16/20/24/32
```

Skins draw at 2× and downsample (LANCZOS). Colours come only from the variant's tokens.
Severity: the API's `severity` when present, else 60 / 85 % thresholds.

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
