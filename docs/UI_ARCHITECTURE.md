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
├─ self-update thread       only while an update runs (update_check.apply_update): download, check,
│                           helper, then the exit; writes update_check.PROGRESS (see "Updates")
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
  i18n.py                 the app's language (#87): detection, the locale, catalogs, plural forms
  formats.py              numbers, percents, money, clocks, weekdays and durations in the locale (#91)
  casing.py               letter case by the language's rules: Turkish's dotted and dotless i (#91)
  cldr.py                 CLDR 47 data for French, Spanish and Turkish, written by tests/cldr_gen.py (#91)
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
  webui/                  index.html, app.js, charts.js, formats.js (numbers and money in the locale, #91),
                          motion.js + motion.css (the shared half of the motion layer),
                          themes/<theme>.css, <theme>.motion.css and <theme>.js (the theme's look,
                          its motion and its scripted flavour),
                          fonts/<family>/*.ttf + OFL.txt — bundled fonts, used by BOTH the
                          dashboard (@font-face via /static/fonts) and the Pillow skins,
                          i18n/<lang>.json — the catalogs of text (#87), read by the page and by
                          Python (the server, and since #93 the view model, the tray tooltip and the tray menu)
```

## UI thread behaviour

- **DPI:** `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)` before any window. Widgets
  render at the DPI of their monitor (`scale = dpi / 96`) and re-render on `WM_DPICHANGED`.
- **Floating widget:** `WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_NOACTIVATE`.
  `WM_NCHITTEST` → `HTCAPTION` for native drag; `WM_EXITSIZEMOVE` saves `position` (clamped to a
  visible monitor work area); `WM_NCLBUTTONDBLCLK` → dashboard; `WM_NCRBUTTONUP` → menu.
  Hidden while a fullscreen app runs, when `hide_floating_in_fullscreen` is on (default off).
  Excluded from Aero Peek (`DWMWA_EXCLUDED_FROM_PEEK`), like every top-level layered window, so
  peeking at a window from its taskbar preview leaves it shown.
- **Taskbar strip:** sized to the taskbar height, placed left of `TrayNotifyWnd` (bottom
  taskbar), re-placed on `WM_DISPLAYCHANGE`, `WM_SETTINGCHANGE` (work area), `TaskbarCreated`
  and a 3 s fallback timer. A layered **child** of `Shell_TrayWnd` (#29): while Start, Search or a
  tray flyout is open, Windows 11 lifts the taskbar above every always-on-top window until the
  shell lets it down, and only the taskbar's own children go with it. Layered, because the
  taskbar's XAML content covers a plain GDI child. Raised over its siblings every 500 ms. A new
  taskbar (Explorer restarted), or a strip window that died with the old one, gets a new window at
  the next re-place. A taskbar that refuses the child (`CreateWindowExW` fails) leaves the strip a
  top-level window, floating over it. When the taskbar is gone (`ERROR_INVALID_WINDOW_HANDLE`, Explorer
  restarting) it floats until a re-place finds another taskbar; any other refusal (`ERROR_ACCESS_DENIED`,
  say) is asked again at a re-place `REFUSED_RETRY_S` (1 minute) later, and a refusal again keeps the
  floating window as it is (#32). `create_window`'s `OSError` carries the code as `winerror`. A child gets
  `WM_DPICHANGED_AFTERPARENT`, never `WM_DPICHANGED`, when the taskbar moves to a monitor of
  another DPI; `LayeredWindow` turns both into `on_dpi_changed`. Windows ties the taskbar's input to
  the thread of its child windows, so the strip is the first thing shutdown destroys: the steps
  after it wait seconds for the dashboard and the data service. Hidden while a fullscreen app runs.
  Click → dashboard; right click → menu.
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
    def variant(self, variant_id: str | None) -> Variant          # a retired id -> its replacement; unknown/None -> the default variant
load_themes(directory: Path | None = None) -> dict[str, Theme]     # validates; shipped files must pass
get_theme(theme_id: str | None) -> Theme                          # cached; unknown -> nostromo
resolve(settings) -> tuple[Theme, Variant]                        # theme_id / theme_variant / theme_mode (+ v1 "theme")
migrate_v1(settings) -> bool                                       # v1 "theme" name -> theme_id + theme_variant, once
css_variables(theme, variant) -> dict[str, str]                    # {"--bg": "#090909", ...} for the webui
font_path(theme, role: "display" | "body" | "mono", weight: str = "regular") -> Path | None   # bundled or system TTF
fallback_paths(theme, role, weight: str = "regular") -> tuple[Path, ...]   # fonts.<role>.fallback in order; a missing one left out
```
Gates "system" variants take the live Windows accent and light/dark mode through injected
callables (defaults read the registry via `ui/platform.py`). v1 map: Default→amber, Red, Blue,
Green, Purple, White → the Nostromo variants of the same colour, and Cyan→blue, Orange→amber (both
retired in 2.2). A stored `cyan` or `orange` reads as `blue` or `amber` the same way, through the
theme's `extras.retired_variants`; `Theme.variant()` applies it, and the loader checks that every
retired id is gone and points to a variant that exists.

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
                      resets_at, resets_text, pace ("ahead" | "on_pace" | "behind" | None), share_text,
                      kind ("session" | "weekly_all" | "weekly_scoped" | another kind; None on a row built by hand),
                      scope_model, scope_name (the model, else the surface), share_pct,
                      resets_same_day, resets_weekday (0 is Monday)
@dataclass class WidgetModel: status: ApiStatus, status_text (i18n.Text), age_text, rows: list[Row],
                              stale: bool, stale_message: str | None, blink_on: bool, updated_at,
                              guard: GuardMark | None,     # the usage guard (#27); None while it is off
                              age_seconds (None exactly when age_text is),
                              stale_reason ("expired" | "refused" | "no_login"; None exactly when stale_message is),
                              locale: Locale     # i18n.ENGLISH unless build_model is given one (#91)
                              .lamp -> "live" | "paused" | "stale" | "offline" | "limited" | "loading"   # LAMP_KINDS
@dataclass class GuardMark: state ("armed" | "holding"), over_keys (Row keys at or past their caps),
                            caps {key: %}, labels {key: "Fable weekly"}, readings {key: %}, resume_at,
                            resume_text ("2H 14M" under a day, else "Wed 4:00 PM"), resume_in (always the
                            countdown), resume_clock (always the time), resume_seconds, resume_same_day,
                            resume_weekday, kinds {key: kind}, models {key: scoped model}, scope_names {key: model,
                            else surface}; .holding, .over_limits(), .watched()
build_model(state: MonitorState | None, now, *, show_scoped="auto", blink_on=True, locale=ENGLISH) -> WidgetModel
age_unit(seconds) -> (12, "s") | (41, "m") | (3, "h") | (2, "d")   # the unit an age is told in: UNIT_* codes
countdown_units(seconds) -> ((2, "d"), (3, "h")) | ((2, "h"), (14, "m")) | ((9, "m"),) | ()   # () under a minute
# the texts below are in the locale given (English by default): other languages' times from formats, their ages and
# countdowns from CLDR's units in capitals (the examples' spaces may be CLDR's no-break ones)
format_countdown(seconds, locale=ENGLISH) -> "2D 3H" | "2H 14M" | "9M" | "<1M"     # whole minutes, rounded down
format_age_short(seconds, locale=ENGLISH) -> "12 S" | "41 M" | "3 H" | "2 D"      # one unit, no AGO: fr "41MIN"
format_resets(resets_at, now, locale=ENGLISH) -> "7:39 PM" | "Sat 4:00 PM"        # fr-CA: "16 h 12" | "sam. 16 h 12"
format_age(fetched_at, now, locale=ENGLISH) -> "12 S AGO"                         # fr: "IL Y A 12 S"
share_text(allowance_pct, locale=ENGLISH) -> "50% SHARE" | None                   # fr: "50 % SHARE"
reset_day(moment, now) -> (on now's local day, local weekday)
english_stale_message(reason) -> STALE_MESSAGE_EXPIRED | STALE_MESSAGE_REFUSED | STALE_MESSAGE_NO_LOGIN | None
stale_message(reason, locale=ENGLISH) -> i18n.Text | None     # widget.stale.* in the locale's language (#93)
STATUS_KEYS {ApiStatus: "widget.status.<code>"}               # the status word's key (#93)
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

**Values, not English (#90).** The model keeps its texts (`label`, `age_text`, `stale_message`, `share_text`,
`resets_text`, and the guard's `labels`, `resume_text`, `resume_in` and `resume_clock`), and the dump and the tests read
them. Their words come from the catalog in the model's language (#93, see "Widget and tray words" under "Languages");
their times, ages, countdowns and percents are in the model's locale (#91). The skins and the tray tooltip word their
own text from the values beside them, and never cut, split or match those texts to find a value:
- A row is named from its `kind`: session and weekly by the skin's own words, a scoped limit for its `scope_name`
  ("FABLE WEEKLY", Night City's "FABLE", Gates' "Fable weekly"), or "WEEKLY SCOPED" when it has neither. A kind a skin
  has no words for, and a row built without a kind, keep their label whole. Where the template is English's (English,
  or a language without the key), Gates puts the name in capitals before `capitalize()`, as it did the label, so a
  model name reads "Claude design weekly" (a label with a letter that has no capital stays in capitals, "ĸRA WEEKLY",
  as before); in another language's words the name goes in as written, its first letter in capitals by the language's
  rule where it starts the row's name (`casing.cased`, #96, Q4).
- The share is `share_pct`, rounded with `round()` as `share_text` is ("50% SHARE", Gates' "50% share").
- The age is `age_seconds` told in its unit by `age_unit`; each skin has its own words for the unit codes
  ("12 S", Night City's "12 S OLD", Gates' "12 s" and "41 min" in English and the pseudo-locale, CLDR's narrow units
  elsewhere, #96).
- The sign-in copy is picked by `stale_reason` (see "Sign-in copy" below).
- The guard's limits are worded from `GuardMark.kinds`, `models` and `scope_names` as the rows are. A limit the
  reading no longer carries, and a kind the skin has no words for, keep the guard's own label. Gates keeps a model's own
  case there ("Fable weekly"); where the template is English's (English, or a language without the key), a limit named
  for its surface goes in lower case ("claude_code weekly", as the guard does), and in another language's words as
  written (mid-sentence, no capitals, #96); one named for nothing is worded by its key ("weekly scoped").
- Every countdown (the HOLD chips, the hover card, Gates' footer, the tray) is `resume_seconds` through
  `countdown_units`, in the skin's units ("2H 14M", Gates' and the tray's "2h 14m"; Gates' in English and the
  pseudo-locale, CLDR's narrow units elsewhere, as the tray's, #96).
- The tray tooltip names the session and weekly rows by kind (`app.TRAY_LIMITS`) and words its status from the status
  itself (`app.TRAY_STATUS`), each a catalog key (#93).

`age_seconds` and `resume_seconds` are whole seconds, never negative. The reset and resume times are still drawn from
`resets_text` and `resume_clock`; `resets_same_day`, `resets_weekday`, `resume_same_day` and `resume_weekday` carry
their day as values, and no skin reads them yet.

**Numbers in the locale (#91).** Each skin rounds a value as it always has (half up, `round()` for a share, one decimal
for an exact value) and writes the digits with `formats.number` and `formats.percent` in `model.locale`; Night City
draws its small percent sign on the side `formats.percent_parts` gives (before the numerals in Turkish). The boxes
sized for English's figures (Nostromo's strip values, Night City's strip percent, the cap badge on Gates' hover card)
take the percent without its space ("100%"; Turkish "%100", Chris's decision on #91); Gates' floating badge keeps the
language's space (beside a row's own spaced share and value it is then left out where it does not fit, #96). The tray
icons' numbers stay digits. Nostromo's own words, units and case calls come from the catalog and `casing` since #94,
Night City's since #95 and Gates' since #96 (see "Languages", "Skin words").

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
under the weakest lit colour (measured 3.21 to 8.74 over 6 variants, 3 severities and 4 sizes; plate against `#f3f3f3` at
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
carry, which is what a widget shows on an account that has the row) and `GUARD_STATE_NAMES` (`guard_armed`,
`guard_hold_session`, `guard_hold_weekly`: a live account with the Fable row, judged by the real `agent/guard.py` at the
default caps; the session hold resumes within hours, the weekly one days away). The contact sheet leaves the scoped
ones out.

**The usage guard on the widgets (#27).** `WidgetModel.guard` is None while the guard is off, and every skin then draws
exactly what it drew before the guard existed. Armed is quiet by design, and never changes a strip
(`tests/test_skin_guard.py` compares the bytes); holding names the hold and the time left. A row the guard holds on is
drawn critical whatever its severity (on a live card): a cap set at 60 % is as final to the user as the API's critical.
The strips have no room for the countdown, so the hover card says it. The tray icon does not change; the tray tooltip
adds "guard armed" or "guard holding, resumes 6:26 PM (in 2h 14m)". While the guard holds, the app's clock tick also
lands just past each whole minute of the countdown (`tick_delay_ms`).

| | Armed | Holding |
|---|---|---|
| Nostromo floating | "[GUARD]" (or "[G]") after the age, when the header has room | the header's right-hand word becomes the chip "HOLD 2H 14M"; a "CAP 95%" pill after each held row's name, where it fits |
| Nostromo strip | nothing | a chip with an hourglass in the divider's place |
| Night City floating | a quiet GUARD chip before the status word in the header, when the brand leaves room (the stale, offline and rate-limited bands draw no armed mark) | the HOLD chip, with a standing glitch, in the status word's place; a held row's critical chip reads "CAP 95" |
| Night City strip | nothing | the HOLD chip in the status tag |
| Gates floating | a bare shield in the dim tone, left of the status pill | the shield in a capsule tinted with the alert colour; while the status is LIVE the footer reads "Guard holding · Resumes 6:26 PM (in 2h 14m)" (another status keeps its own footer); a "Cap 95%" badge after each held row's name |
| Gates strip | nothing | a small filled shield in the alert colour above the lamp |
| Hover card | "Guard armed: session 95%, weekly 90%" | "Guard holding: weekly 91% (cap 90%). Resumes Fri 7:12 PM (in 2d 3h)." |

Each skin words the hover card in its own voice: Nostromo in capitals, Night City as "GUARD // HOLDING:", Gates as an
InfoBar under the rows.

While the guard holds, its HOLD chip takes the place of the status word in Nostromo's and Night City's headers and in
Night City's strip tag, whatever the link status is: a paused or loading widget then says HOLD. Gates keeps its status
pill beside the shield. Without a reading (no sign-in, still loading) no surface shows the guard: it cannot act on
nothing.

Sign-in copy. `WidgetModel.stale_reason` says why the user must act (`expired`, `refused`, `no_login`), and
`stale_message` is its neutral wording ("LOGIN EXPIRED - USE CLAUDE CODE", `widget.stale.*` in the model's language
since #93). Each skin picks its copy by the reason,
as its approved mockup words it, and never reads the neutral text. Nostromo: the dashed auth box reads
"AUTH LINK LOST." / "USE CLAUDE CODE TO RESTORE." (`AUTH_KEYS`, by reason: `widget.nostromo.auth.*`). Night City: the hazard band reads "RELINK // USE CLAUDE CODE"
(`widget.night_city.band.expired`; the theme file's `extras.copy` keeps the mockup's English and is no longer read, #95).
Gates: the footer reads "Hmm, we lost touch. Send Claude Code a message." (`widget.gates.footer.stale` and
`.stale_step`, #96).
Each says to use Claude Code, not to open it: Claude Code renews the sign-in only when it sends a request, so
an open but idle Claude Code lets it run out (#52).

A sign-in Claude turned down with HTTP 403 (`MonitorState.signin_refused`, #55) is not renewed by a message: only a
new sign-in fixes it. Its reason is `refused`, and its neutral wording `STALE_MESSAGE_REFUSED` ("LOGIN REFUSED - RUN /login IN CLAUDE CODE").
Nostromo: "AUTH REFUSED." / "RUN /login IN CLAUDE CODE." Night City: "RELINK // RUN /login"
(`widget.night_city.band.refused`). Gates: "Sign-in refused. Run /login in Claude Code." (`widget.gates.footer.refused`
and `.refused_step`). The dashboard's sign-in
help asks for `/login` too. A 401 keeps the expired wording: Claude Code's next request renews the sign-in, or asks
for a new one.

No sign-in at all is a different case (#28): the person may never have used Claude Code (claude.ai and the
Claude apps share its limits), so the widgets send them to the dashboard, which has the steps, rather than to
Claude Code. The neutral text is "NO LOGIN FOUND - OPEN THE DASHBOARD" (Nostromo's auth box has the same words, as
its two lines), Night City's band "NO LOGIN // OPEN DASHBOARD" (`widget.night_city.band.no_login`), and Gates' footer
"No sign-in found. Open the dashboard for help." (`widget.gates.footer.no_login` and `.no_login_step`). The
dashboard's link status reads "NO LOGIN" too (Gates: "Not signed in"),
and its status banner becomes the sign-in help (see "webui").

### `ui/`
```python
class UIHost:                                   # host.py
    def run(self) -> int; def quit(self)
    def post(self, fn) -> None                   # thread-safe: run fn on the UI thread
    def set_timer(self, ms: int, fn, *, repeat=True) -> int; def kill_timer(self, timer_id)
    def on_session_change(self, cb) / on_settings_change(cb) / on_display_change(cb) / on_taskbar_created(cb)
class LayeredWindow:                            # layered.py
    def __init__(self, host, *, draggable: bool, topmost=True, parent=None): ...   # parent: a layered child
    def update(self, image, x: int, y: int) -> None    # premultiplied BGRA via UpdateLayeredWindow
    def move(self, x, y); show(); hide(); destroy(); bring_to_top(); set_click_through(on)
    on_click / on_double_click / on_right_click(x, y) / on_drag_end(x, y) / on_hover(bool): callbacks
    hwnd; dpi; rect; visible; parent; alive        # x, y are always screen coordinates
platform.py: taskbar_info(), work_areas(), dpi_for_point(x, y), is_fullscreen_foreground(),
             is_locked(), screensaver_running(), windows_accent_hex(), apps_use_light_theme(),
             clamp_to_work_area(x, y, w, h)
menu.py:     show_menu(host, items: list[MenuItem], x, y)        # MenuItem(label, action, checked, enabled, separator,
                                                                 #   id): id names the row in every language (#93)
             run_chosen(item)        # runs the chosen row; a failure is logged by its id, never its label
tray.py:     TrayIcon(host, tooltip, on_left_click, on_menu): set_images({16: img, 24: img, 32: img}),
             set_tooltip(text), notify(title, body) -> bool, remove()    # re-adds itself on TaskbarCreated
```

`TrayIcon.notify` answers True only when the shell took the balloon. A shell that refuses the modify (the icon is gone, as after an
Explorer restart) gets the icon added again and the same balloon sent once more, and the answer is that second call's, so a toast
that was lost is never counted as shown (the update announcement is then offered again, not marked as told).

### Dev isolation
`USAGE_MONITOR_APPDATA_DIR=<dir>` puts settings, DB and log in `<dir>`. `USAGE_MONITOR_DEV_INSTANCE=1` uses the
mutex `Global\ClaudeUsageMonitor-dev`, its own controller window class, and tags the tray
tooltip "(dev)", so a test build runs alongside an installed copy. `main.py --dump-frames <dir> [--lang <id>]`
renders every theme × variant × state × scale to PNGs without opening windows, in English, or with `--lang` (en, fr, es,
tr or pseudo) in `i18n.dev_locale`: the language with its base format tag (en-US, fr-FR, es-ES, tr-TR; en-US for the
pseudo-locale) and its own clock, 24-hour for fr, es and tr. That is what the dev server's `--lang` shows, and what a
dev instance shows for `USAGE_MONITOR_LANG=<id>` on a Windows that reports nothing. On a real Windows a dev instance in
fr, es or tr takes its clock from the short-time pattern and its format tag from the regional format when that is of
the same language (fr-CA), so its frames can differ. The locale is built in `main.py`, which imports `i18n` only for
that flag, since every hook call parses the command line;
`--smoke-test <seconds> <report.json>` runs the real app, exercises the API server (state with its
`update` block, themes, live settings, the dashboard child, every statistics route and both exports), then exits
with a report. The report holds statuses, sizes and timings, never usage content; it also says whether the
six-hourly update re-check timer is armed (`update_recheck`). It names the tray tooltip and the menu's labels in the
app's language (`tray_tooltip`, `menu_labels`, #93), and the run fails when a tooltip phrase or a label is a whole
catalog key (`i18n._KEY`), which is what a build without its catalog files would show.

- **The sign-in entry belongs to the installed copy.** A dev instance never reads, writes or
  repairs the HKCU Run entry (`ClaudeUsageMonitor`): its *Start with Windows* switch lives in
  memory (shown in `/api/state`, gone when the process ends, logged as skipped). Only the frozen
  installed build repairs the entry at launch, so a source checkout cannot repoint it.
  `--smoke-test` always runs as a dev instance, whatever the environment says. The API server is
  a dev server when its `dev` flag is set **or** `USAGE_MONITOR_DEV_INSTANCE=1` is in the environment.
- **Dev server.** `python -m usage_monitor.ui.devserver [--port N] [--scenario
  normal|warning|critical|stale|refused|guard|no_login|limits_only] [--db PATH] [--theme ID[/VARIANT]] [--lang ID]
  [--update-demo [MODE]]` serves the real webui
  and API from a fake `MonitorState`, with settings in `USAGE_MONITOR_APPDATA_DIR` (or a temporary folder). `stale` and `no_login`
  carry the data layer's own sign-in texts (`usage_api.EXPIRED_DETAIL`, `usage_api.NO_SIGN_IN_DETAIL`). `refused` is a sign-in
  the usage API turned down with HTTP 403 (`signin_refused` 403, #55): its help asks for `/login`, not for a message.
  The two scenarios of someone who uses Claude on the web or in the app (#28) have no Claude Code on the PC:
  `no_login` has no sign-in (no limits, no plan), and `limits_only` is signed in with the limits of `normal` and a week
  spent in chats. In both every window is empty and the ingest has finished with no file to read, and the synthetic
  history holds no transcript (`limits_only` keeps the usage readings a poll stores, so its pace works), so the dashboard
  shows its pages for a PC with no Claude Code activity. A refresh (the Refresh button, *Check again*) moves the fake
  state's `updated_at` by a millisecond, as a real cycle publishes a new one, so the page sees the cycle it asked for.
  The usage guard works in every scenario: its verdict is the real `agent/guard.py` on the fake
  limits and the dev settings, so arming it or changing a cap shows at once. The `guard` scenario
  arms it at the default caps with the session past its cap, and fills a folder of its own with
  sessions that were told and one that waits, so the Settings card has everything to show. Its
  one-click setup works on a sandbox (`dev_setup`): the real `agent/setup.py` with a fake exe, a
  settings file in the dev folder and a `claude` kept in memory, so Install and Remove can be
  clicked through and nothing under `~/.claude` is read or written.
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
  `--update-demo [ok|stall|fail|unknown]` (#58; `ok` when no mode is named) offers a made-up release, 9.9.9, that
  installs in place (the banner and Settings → System offer it). *Install update* runs a made-up update
  (`devserver.UpdateDemo`): a timer fills a real `update_check.UpdateProgress` (21 MB in 64 KB blocks over about 12 s) and
  `/api/update/status` answers it, so the route and the page read what they read in the app. It never calls
  `apply_update`, opens no network connection, writes no file, starts no helper and never exits. `ok` ends in
  `restarting` and stays there (a real monitor closes then: stop the dev server to see the page lose it); `stall` stops at
  37 % and fails with `timeout` after 60 s, the real socket timeout; `fail` fails the SHA-256 check; `unknown` sends no
  size. A failed run can be started again. Its clock, sleep and thread are injectable (`UpdateDemo(mode, clock=,
  sleep=, spawn=)`), so a test runs it at once.

## Languages (#87)

Chris's rule (2026-11-21, epic #49): English by default, plus French, Spanish and Turkish. The app follows the Windows
display language when it is one the app offers, and falls back to English. `usage_monitor/i18n.py` (no UI imports)
holds the rule; the localization tickets (#86 to #109) fill the catalogs and move each surface's text into them.

- **Offered.** `i18n.OFFERED` is the list Windows' language or the setting may pick. A language joins it only in its
  sign-off change (#107), which also moves its `[Languages]` entry in `installer.iss` into the release build
  ("Installer and uninstaller", below; a test holds the two lists equal). Until then it holds English alone, so a
  half-done catalog is never shown. A dev instance can show any catalog language with `USAGE_MONITOR_LANG=<id>`
  (honoured only with `USAGE_MONITOR_DEV_INSTANCE=1`). The resolver, the locale source and the server read `OFFERED`
  and `WALK_DISPLAY_LANGUAGES` when they use them, so the change (or a test) that sets them reaches all three; only the
  `i18n.ENGLISH` constant takes the list at import.
- **Detection.** Only the first entry of `GetUserPreferredUILanguages(MUI_LANGUAGE_NAME)` counts (fallback
  `GetUserDefaultUILanguage`, named by `LCIDToLocaleName`). Setup reads `GetUserDefaultUILanguage`, Windows' display
  language (#103). The two are the same language, so the app and Setup agree, except when the first preferred language
  is not the display language: one moved to the top without its display pack, or a change not applied until the user
  signs out. Then the app follows the list and a first install by hand follows the display language (#108 checks it on
  a real profile). `WALK_DISPLAY_LANGUAGES = True` would take
  the first offered language anywhere in the list instead (#86's other choice). Never `locale.getlocale()` or
  `getdefaultlocale()`, which report the regional format, not a language. The app makes its reader
  (`i18n.LazyWindows`) and reads Windows on first use, when the log is set up, and reads the display languages only
  once: a change signs the user out. A call that fails (an error code or an exception), or a reader that raises, reads
  as nothing, so the app runs in English. `Win32Language` logs the first failure of each call as a warning;
  `LocaleSource` logs a reader that raises each time.
- **The setting.** `language` is `system` (the default), `en`, `fr`, `es` or `tr`, checked by both `settings.py` and the
  server's `SETTING_SPECS`. A language that is not offered yet is stored, so the release that offers it picks the
  choice up. Until then it reads as `system`, and the locale reports it as `system`; the `settings` block of
  `/api/state` still shows the stored value.
- **The locale.** `i18n.Locale`: the setting as read, the language in use, the language `system` resolves to, the
  format tag, the clock and the offered list. English keeps today's en-US formats and 12-hour clock. French, Spanish
  and Turkish take Windows' regional format when it is of the same language (fr-CA, es-MX), else fr-FR, es-ES, tr-TR,
  and Windows' own 12/24-hour clock (`LOCALE_SSHORTTIME`). `MonitorApp.locale` (an `i18n.LocaleSource`) reads Windows;
  WM_SETTINGCHANGE "intl" makes it read the regional format and the clock again. The server takes it as
  `locale_provider`; without one (tests, the dev server) the setting and `USAGE_MONITOR_LANG` decide, with no Windows.
  No test depends on this PC's language: an autouse fixture in `tests/conftest.py` gives every test a reader that
  knows nothing and drops `USAGE_MONITOR_LANG`. One smoke test reads the real Windows calls and checks only their
  shape.
- **Formats (#91).** `usage_monitor/formats.py`, standard library only. English keeps today's US formats byte for byte:
  each English branch is the code its callers had (`tests/fixtures/formats_english.json` holds both), and English never
  reads the CLDR data. French, Spanish and Turkish follow `Locale.format` through `usage_monitor/cldr.py`: CLDR 47 from
  Babel 2.18.0 for every CLDR region of the three languages, each region kept as its differences from the language's
  base, and a tag with no region of its own takes the base. `Locale.clock` picks CLDR's 24-hour or 12-hour pattern.
  `cldr.py` is generated, never edited by hand: `python tests/cldr_gen.py` writes it on a dev PC with Babel 2.18.0
  (CLDR 47), and `--check` exits 1 when the committed file differs. Babel is a dev tool only: the app never imports
  it, and the generator's tests in `tests/test_cldr.py` skip where Babel 2.18.0 is missing (CI). A number symbol the
  generator does not know stops it. Three fields Babel does not keep are rules in the generator, each with its source:
  minimum grouping digits (es-ES `1234` but `12.345`), the plain `$` for US dollars (Chris), and a U+00A0 between a
  currency code and a digit. Where the page's data differs from Babel's, the widgets follow the page (#111): five more
  rules (`PAGE_RULES`), each with Babel's value and ICU's, as node's `Intl` writes it on ICU 77.1 and 78.2 (a Babel
  that gives anything else for one stops the run): es-AR's and es-CO's `$ 5,96` (a U+00A0 after the `$`), es-GQ's sign
  first (`$5,96`), fr-MA's `.` group and its `a.m.` and `p.m.`, and tr-CY's day period after the time on the 12-hour
  clock (`4:12 ÖS`). An amount with a currency takes the tag's currency decimal and group, which CLDR sets
  apart for fr-CH (`5.96 $` but `1 234,5`, as Windows' ICU writes them). The pseudo-locale takes English's shapes with
  two changes: numbers grouped with U+202F and a U+00A0 before `%`; it keeps English's 12-hour clock. Rounding stays
  with the caller: `number` and `percent` take the digits the caller rounded to. `tight=True` drops the space beside
  the sign for the boxes sized for English's figures, and the one between a `$` and a minus too (es-PY's `$-5,96`, as
  the page's tight writes it, #111); a currency code keeps its U+00A0. Nothing reads the process
  locale: `tests/test_formats.py` parses the package, `main.py`, `update_check.py` and `version.py` and fails an
  import of `locale`, a `setlocale`, `getlocale` or `localeconv` call, or a `strftime` format that names a weekday, a
  month, a day period or the zone (the agent modes' English `Mon 09:00` in `agent/status.py` is the one listed
  exception). The widgets, the tray tooltip and the alerts write their numbers, percents, clocks and weekdays this
  way. The view model's, the tray tooltip's and the tray menu's words come from the catalog since #93, Nostromo's own
  words since #94, Night City's since #95, Gates' since #96, the toasts' and the alerts' since #97 ("Toast and alert
  words", below). The alerts read the app's locale on each evaluation
  (`AlertEngine(settings, locale_provider=)`, English without one or when it fails); English alerts keep their trailing
  `USD`, other languages write each amount with the plain `$` (US dollars) or the currency's code. The dashboard has its
  own: `webui/formats.js` (`window.MonitorFormats`: `make(locale)`, and `active()` from the `monitor-locale` meta),
  loaded before every other page script. English keeps the page's own shapes (`tests/fixtures/webui_formats_english.json`
  holds them), the pseudo-locale takes them with the same two changes, and French, Spanish and Turkish go through `Intl`
  with the format tag, so the WebView's own CLDR data; US dollars are the plain `$` (`narrowSymbol`), and any other
  currency is written with its code, as `formats.py` writes it (`5,96 CAD` in fr-CA, where the local symbol would be the
  same `$`; English keeps Intl's `CA$5.96`). Its `number`, `percent`, `usd` and `compact` take the caller's digits too,
  and `tight` drops the space beside `%` and `$`; where Spanish has no compact unit for a count (10^9 to 10^10, written
  in millions) `compact` writes the unrounded count (`2254 M`, never `2250,00 M`). `app.js` writes every number, percent
  and dollar through it (`FMT`), the figures in its sentences, charts, alert levels and pending notes too (`TOP 00,00 $`
  until the daily chart's figure comes); English keeps its own text where it was odd (a tile's `$-123`, `$1000.00` for
  cents that round up to a thousand), and the odometer's separators are the locale's group sign. The month-by-month
  chart's label column grows to its widest label (fr-CA `JUILL. 26`), never under English's 64 px. The cost
  tiles and the big cost figure are the page's boxes sized for English's figures: their dollar is tight. A compact
  figure keeps CLDR's space before its unit, so French's `99,9 k$` has seven characters where English's `$99.9K` has six
  (`tests/budgets.py` measures them with `PAGE_MONEY`, which `test_webui.py` holds to the page in every format tag,
  with the `$` on either side, since the boxes count characters; `FOR_91` in `test_budgets.py` lists them until Chris
  decides). Dates and times are the page's local time: English
  writes them as it always did, in capitals and on the 12-hour clock (`WED 7:39 PM`, `OCT 21`); French, Spanish and
  Turkish take `Intl.DateTimeFormat` for the tag on the monitor's clock (`hourCycle` from `Locale.clock`), the hour as
  wide as the tag's own short time writes it (`09:05` in fr-FR, `9:05` in es-ES, as CLDR's `Hm`), and `app.js` sets
  them in its capitals with `FMT.upper` (`SAM. 16:12`, Turkish `NİS`); a month alone takes the form a date writes, as
  `formats.month` does (`ene`, not es-CL's stand-alone `ene.`). `charts.js` takes the calendar's month and weekday names
  from `MonitorFormats.active()`, English's on a page without the layer. Durations (`fmtRemain` and the ages) are CLDR's
  narrow units in French, Spanish and Turkish (`2J 3H`; Turkish `3S 27D` is 3 hours 27 minutes), with the widgets'
  parts, so the page's countdown is `render.model.format_countdown`'s (under a minute the page still says `NOW`); they
  come from `Intl.DurationFormat` where the WebView has it, else from each unit through `NumberFormat`, joined as a unit
  list (D14 of #91's plan: the two must write the same text, and `DURATION_FORMAT = false` in `formats.js` would write
  them all the second way). English keeps `2 D 3 H`. Lists are English's `X, Y and Z` (no Oxford comma), else
  `Intl.ListFormat`'s; `plural()` keeps English's `s` rule for its English words, with the locale's number. `motion.js`
  reads and writes a rolling figure in the page's group and decimal signs, a sign-first percent too (`34,5 %` rolls as
  34.5; outside English a group sign counts only between digits), and an amount in the currency's own decimal sign
  where CLDR sets it apart (fr-CH's `1 107.50 $` rolls and ends on its point); Gates sets the small percent sign on the
  side the language writes it (`%34`). V2:
  `tests/webui_cldr.js` writes the page's formats in every format tag over a grid (real codes, EUR and CAD, as well as
  one Intl refuses), and `test_webui.py` holds them to
  `formats.py`'s and `casing.py`'s, except a list of known differences, each with its tag, field, both values and both
  versions. Since #111 the rows #91 found (es-AR's and es-CO's space after `$`, es-GQ's sign or code first, fr-MA's
  `.` group and `a.m.`, tr-CY's day period after the time, and es-PY's tight `$-5,96`) follow the page, the same on
  ICU 77.1 and on Chromium's ICU 78.2 data (VS Code's Electron). One row is left, and it is a node build's, not the
  page's: CI's node 20 (ICU 78.2, full data) writes fr-MA's day periods as `AM`/`PM`; it is listed for that node
  only, and a listed row is checked as still different only on the versions it was seen on. Seen on node 24.13 (ICU 77.1, CLDR 47.0): in every 12-hour clock `Intl` writes
  U+0020 where CLDR's patterns put U+202F, and ICU's Spanish day periods hold U+00A0 where Babel's hold U+202F. On
  ICU 78.2 (CLDR 48.0, CI's node 20.20) `Intl` writes U+0020 inside Spanish's day period too (`p. m.`):
  V2 lets U+0020, U+00A0 and U+202F differ inside or beside a day period, and nowhere else (the widgets draw U+202F as
  U+00A0, so the clock reads the same). CLDR 48 also writes the page's compact dollars otherwise: Turkish's and es-MX's
  `$` first (`$1,2 B`, CLDR 47 `1,2 B $`), Latin American Spanish's thousand as `k` (CLDR 47 `K`) and fr-CA's with a
  space before the `$` (`1,2 k $`); the tests take the money boxes' sign on either side, and list by CLDR version the
  forms they pin.
- **Letter case (#91).** `usage_monitor/casing.py`: `upper`, `lower`, `capitalize` and `cased` by the language's rules,
  so Turkish (and Azerbaijani) give `İ` and `ı` and never a combining dot (U+0307); every other language gets Python's
  own. A name in capitals is cased with the words by the language's rule (`HAİKU` in Turkish, Chris's decision); in
  lower or sentence case it keeps its own letters (`Claude Instant`, never `ınstant`). English keeps its old quirks byte
  for byte. Ids (limit kinds, keys, the guard's identity strings, tags, currency codes) keep the plain calls:
  `tests/test_casing_sites.py` lists every bare call allowed in the files the widgets', the tray's and the alerts' words
  come from (render/model.py, app.py, data/alerts.py, data/identity.py, data/usage_api.py, formats.py) and Nostromo's
  and Night City's skins, whose own calls moved to `casing` in #94 and #95 with no bare call left, and Gates' (#96),
  whose two English name quirks (`gates._english_name`, Q4) are its listed calls. `tests/test_locale_surfaces.py` draws
  every
  theme, state and surface in Turkish and finds no U+0307. On the page, `formats.js`'s `upper` and `lower` follow the
  same rule (for a language or a tag; the page's language when none is given): `app.js` cases a name in capitals by the
  page's language (`FMT.upper(name)`: a model, a plan, a surface), English words by English's (`FMT.upper(text, "en")`,
  until the text tickets #98 to #101 give each its catalog's language), and theme and accent names English's until
  #102. `test_webui.py` lists each bare case call `app.js` may make. Each one cases an id, never words, by the plain
  mapping on purpose: a currency code, a severity, a colour's name against its id, an export format, an accent id. Three
  of them reach the page as text made from an id: `PREPARING CSV…`, an accent button's three letters, and a currency
  code Intl does not know.
- **Catalogs.** `webui/i18n/<lang>.json`, shared by Python and the page. `en.json` is the source of truth: a key missing
  from another catalog shows its English text, and the lookup says it came from English (`Catalog.text()` and
  `plural()` return an `i18n.Text`, a `str` that knows its `.language`), so the caller marks it `lang="en"` and cases it
  by English rules. Keys are stable dotted names; placeholders are `{named}`; plural entries are objects keyed by the
  language's CLDR categories (en and tr: one, other; fr and es: one, many, other), and an English entry behind another
  language follows English rules. Strings are plain text: no tags, no entities. Commands and paths are placeholders,
  inserted after any case change, with `tCode()` (a block) or `tPath()` (in a sentence): `lang="en"`, `translate="no"`
  and `.nm-path`'s `text-transform: none`. Names (models, plans, folders, sessions, tools, Claude Code) go in `tName()`:
  `translate="no"` only, with no `lang` of its own, so the theme's capitals reach them by the page's language (Chris's
  decision on #91: Turkish `HAİKU`). A running update on Settings (#58) takes its words from `settings.update.progress.*`
  (natural case: Nostromo and Night City set the chips and the status line in capitals in CSS, the sizes line as written);
  its sizes are the language's numbers inside the catalog's words, and "MB" is a word of the string, for a translator to
  abbreviate as the language does (French `Mo`). The rest of the update flow is #97's: `settings.update.*` (the System
  card's "Check for updates" row, its three buttons and the line under them: `row`, `button`, `offered`, `status`,
  `failed.<check|install|skip>.<reason>`) and `banner.update.*` (the update banner), in natural case like #58's:
  Nostromo and Night City set the buttons, the row's title, the banner's heading and the lines in capitals in CSS
  (Nostromo the banner's sentence too; the row's help stays as written), and Gates shows them as stored. What a click
  said is a line,
  `tLine(key, values)`: its text and the language it came from, so the element showing it (`#set-update-status`,
  `syncUpdate`; the banner's `.nm-status`, in its markup) carries `lang="en"` when it is English on another language's
  page. The banner's `aria-label` (`banner.update.label`), like #58's progress bar's, is an attribute and stays unmarked
  (as below), until the language's catalog has the key. `labelFor`'s saved-setting words and the card's title and meta
  stay #100's.
- **The page.** `_send_index` writes `<html lang>`, the `monitor-locale` meta (the locale as JSON) and the catalog's
  `<script src="/static/i18n/<lang>.js">` before `charts.js`, and fills the shell's own words, all before the theme,
  which can give up on a page it cannot resolve. A marked element holds text only: `data-i18n="<key>"` replaces its
  text, and `data-i18n-title`, `-aria-label`, `-placeholder` and `-alt` set that attribute; marks are written with
  double quotes. English text filled into an element on another language's page gets `lang="en"`, unless the element
  names a language already; an attribute filled with English is not marked. A key no catalog has, a mark the server
  cannot read, and a marked element that holds markup stay as written and are logged. In English the page is
  `index.html` with its metas filled (and the theme's attributes on `<html>`). Since #98 the shell's own words are marks
  (the Refresh button and its title, the link word and the data age it shows before the first state, the nav's label,
  kicker and page names, each name in a `<span>` beside its number, and the colour heading); the product name `CLAUDE USAGE
  MONITOR` stays as written and unmarked, and #86's brand mark `// ONBOARD COMPUTER` is `shell.brand.mark`, kept as
  written in every language. The server builds the catalog script
  from the JSON (`window.MonitorI18n = {language, strings, fallback}`), so each catalog exists once and
  `script-src 'self'` still holds. `app.js` reads the locale from the meta (`LOCALE`) and the catalog (`I18N`), never
  `navigator.language`, and words text with `t()`, `tHtml()` and `tPlural()`. `tHtml()` escapes everything except a
  value made with `markup()`, which only `app.js` can make (it is known by a prototype nothing from outside has).
  `tPlural()` uses the page's own copy of the CLDR rules (`pluralCategory()`), so a WebView with other ICU data cannot
  disagree with the app. A key is a literal string in its call and the values are an object literal written there, so
  `test_webui.py` checks each call against `en.json`. No page script (`app.js`, `charts.js`, `formats.js`, `motion.js`, the themes')
  may format in the browser's own locale (`toLocale*()` or `Intl.*()` without a locale) or read `navigator.language`;
  `test_webui.py` checks them all.
- **A new locale.** When `/api/state`'s `locale` has another language, format or clock than the page, the page
  reloads. It never does when it could not read its own locale. Before reloading it leaves a mark in `window.name`,
  which belongs to the window and ends with it (`app.js` keeps nothing in browser storage, #54), and a page that
  arrives in the marked locale clears it. So only a reload that failed to bring the page into its locale holds back
  the next one for that locale, and a server that kept serving another locale cannot loop the page. The Language
  setting shows once a second language is offered, or when a dev instance shows one that is not: "System default (…)"
  naming what it resolves to, then each language named in itself. Its Apply button saves the choice, and the state
  that follows brings the reload; until then the list keeps the pick.
- **Case** (the rule the dashboard tickets, #98 to #101, carry out). Dashboard strings are stored in natural case:
  Nostromo's and Night City's capitals come from CSS `text-transform`, and an SVG label's from `tUpper` (the text in its
  language's capitals, `FMT.upper`, its values filled in after, so a figure keeps its own case); Gates shows strings as
  stored. #98 did this for the shell, the header, the sidebar, every page's heading and banners, Overview, Limits and the
  cards elsewhere that share their code (Cost's value and credits cards). Nostromo gained `text-transform: uppercase` on
  `.nm-uplink`, `#data-age`, `.nm-refresh` (a button takes the browser's own `none`, not its parent's case), `.nm-kick`,
  `.nm-menu a`, `.nm-health .nm-kv` and `.nm-h1`, and Nostromo and Night City on `.nm-remain .l`; Night City set the
  others already. Gates' own wordings are `page.gates.*` keys (its link words, `Updated {age} ago`, `Updated --`,
  `Tokens / min`, `-- / Hour`) that `app.js` writes when the look is Gates (`gatesLook()`: `data-theme` on `<html>`,
  which the server writes into the first paint), so Gates shows its own words from `app.js`'s first render (`init`'s
  `renderFirstChrome`, before any state). Until then the served shell shows the shell's own words in every look (the server
  fills them from `header.link.loading`, `header.age_none` and `nav.colour.label.phosphor`): with motion on and a Gates boot
  due, the boot curtain (`html[data-fx-boot]`) hides them; with reduced motion, or once the boot has played, they show
  for a moment. Efficiency's pipeline
  card (#99's) keeps its link row in capitals (`FMT.upper` of the header's word) until #99 moves the card. The other pages
  still write many labels in capitals, and `gates.js` re-cases them, and what #98 leaves in capitals (English limit tags,
  names, dates, the SVG labels), through `VOCAB` and `PHRASES`. It keeps a compact figure's magnitude in capitals (`1.5M`, and
  `1,5 M` or Turkish `1,5 B` after the formats' no-break space). That re-casing lowers ASCII letters only, by English's
  rule, so on a non-English page it breaks words the page writes in its language's capitals: the formats' dates (Turkish
  `24 ekİ`, `AĞu 9`; Spanish `SÁb, 16:12`, `MiÉ`) and, on a Turkish page, the model and product names in the model rows
  (`Haİku 4.5`, `Claude İn Chrome`, where English capitals gave `Haiku`). It goes with those tickets (#101 for Gates).
- **The page's words (#98).** The shell, the header, the sidebar, every page's heading and banners, Overview and Limits
  take their words from the catalog: `shell.*` (the shell's marks), `header.*` (the link word by status code, `lost` when
  the monitor does not answer, and the data age), `nav.*` (the health box, the colour heading, and the sidebar's colour
  buttons: `nav.colour.title.<id>` words an accent or a phosphor from its id, `nav.colour.short.<id>` Gates' three
  letters, each button marked `lang="en"` while its tooltip or Gates' letters on it are English on another language's
  page; Night City's cuts of its variant names stay its own, #102's), `heading.<page>.title` and `.sub`,
  `banner.stats.*` (the problem and stale banners), `stats.*` (the problem lines, the cards' short lines, the reasons),
  `limit.*` (shared by Overview's limit cards and Limits' pacing cards), `overview.*`, `limits.*`, `plan.unknown`,
  `ratio.*`, `format.now` and `page.gates.*`. No key is also the start of another (`a.b` and `a.b.c` never both:
  `test_i18n.py`). Text set with `textContent` is a line (`tLine`: its text and the language it came from), and its
  element carries `lang="en"` when English stands on another language's page (`markLineLang`, `lineLangAttr`). An
  element a theme's entrance animates (the directors' selectors: `.nm-h1`, `.nm-sub`, `.nm-tag`, `.nm-ttl`,
  `.nm-meta-t`, `.nm-mrow .k`, `.nm-kv-grid .nm-kv b`) holds its words as its own text, never in an inner span, since
  Nostromo's typer and Night City's decoder read only the element's own text: `tHtmlLine` gives such an element its
  markup and language, and `lineAttrs` puts `lang="en"` and, for a name (`nameLine`), `translate="no"` on the element
  itself, off English only, so English's markup is as it was. A name inside one of these sentences (the cost card's plan,
  Claude Code in the no-activity line: `nameInLine`) is plain text in English, as English drew it (a span of its own splits
  the text run and moves its glyphs a sub-pixel), and `tName`'s span (`translate="no"`) elsewhere. In Gates' English the
  plan's name has a bare span of its own (`planNameInLine`, no attribute), so `gates.js` still sets it in sentence case
  (Gates draws the split run as before, pixel for pixel); the update banner keeps `tName`'s span in English, as #97
  wrote it. A
  report's failure is worded by the page where the page knows it
  (`statReason`): no answer is `lost`; each of the five texts the statistics routes answer with (501, the two 503s, 500
  and 401: `STAT_ERRORS`, which a test holds to `server.py`) is its reason when its status matches; any other text the
  monitor sends (a query it refused, a report it does not know: `e.sent`) is shown as it came, Python's English, marked
  `lang="en"` on another language's page (#102's to give it a code); `HTTP {status}` names only an answer with no text. So
  English reads as before for every answer (`page_words_english_more.json` holds the 400 and 404 texts). A reason is one
  placeholder in a whole sentence, and #102 may give the answers codes in `statReason` alone. `fmtRemain(ms,
  inSentence)` keeps the badge form (`3 H 27 M`, `NOW`) and has a sentence form, its units in lower case (`In 3 h 27 m`,
  `format.now`). In English a limit's tag is its own label (the
  identity string); another language names it from its kind and scope (`limit.name.*`). A plan whose sign-in names no
  subscription shows `plan.unknown` (`planWords`, #90's hand-off): a catalog word, not a name, so the cost card never marks
  it `translate="no"` and marks it with its language where that is not the plan line's. `ratioNote` stays plain text,
  since Cost escapes it. The forecast's end label on a limit's chart (`limits.chart.end_label`) and the surface bar's tips
  (`limits.surfaces.tip`) are keys too, so the label takes the language's percent as its tooltip does.
  The pacing card's hidden sizing texts come from the story keys, with the language's clock of noon and its `00.0`.
  English is unchanged on screen, byte for byte in every look, as `tests/fixtures/page_words_english.json` holds it
  ("Test tools"); the one difference is Gates before its first state, which shows its own words from `app.js`'s first render
  instead of the shell's capitals until `gates.js` arrived. One deliberate change, attributes only (Chris on #98,
  2026-11-27): every Overview card subtitle (`.nm-meta-t` on the Overview's cards, the cost card's daily chart's too) carries
  its full text in a `title` in every language (`metaAttrs`), so a subtitle English's own rule cuts with "…" can be read;
  nothing on screen moves (#118 moves a subtitle that does not fit under its title). In Gates the title follows
  `gates.js`'s re-casing of the subtitle's words (the cost card's `Oct 1 - Nov 1 · at list prices`, where `app.js`
  writes `OCT 1`), so the tooltip says what the card shows; `app.js`'s title comes back with the words in another look.
  Both records gained those titles, derived from the records, not re-recorded. Three English forms changed that cannot
  show: the one forms of `limits.fact.value.days` and `.hours` (`1 day`, `1 hour`; the windows are 5 hours and 7 days)
  and of `ratio.scaled` (a cell of one month is never scaled).
- **Longer words (#98, owner question 1).** As the owner decided on #98, after a look at what other apps do: outside
  English a line longer than its room wraps and its box grows first; the translator writes a short form to the room its
  key's note gives (below); an ellipsis comes last, only where a row's height is fixed and the whole text can still be
  read. Nothing shrinks, and no code cuts or abbreviates a translation (numbers and units take their short forms from
  `Intl` alone). The rules sit at the end of each theme sheet under `:root:not(:lang(en))`, so English keeps its
  one-line layout, its own ellipses included. By element:
  - A nav tab wraps to a second line and grows (Night City's past 34 px, Gates' past 36 px; Nostromo's two lines fit its
    34 px); it is never cut, and a third line is a fault for the translator.
  - The header's link words: the data age gives way first (Nostromo's below 1101 px and then its brand mark below 901 px,
    Night City's below 801 px; a pseudo-locale bar with its longest link word needs 1048 px, and 860 px without the data
    age; under 801 px every language hides Nostromo's data age and brand mark), then the words wrap to a second line
    (`.nm-uplink`; two lines fit each bar, Gates' pill grows). They are never cut.
  - Chips: a card's title row wraps, so a chip moves below its title; a chip's own word never wraps. Night City's chips and
    its framed pace verdict grow past their 14 px line, so an accented capital or a cedilla clears the 1 px frame.
  - The Overview's card metas (Nostromo, Night City, Edgerun) are a designed cut: the title and the meta keep one row, the
    title keeps its words whole up to 70 % of the row (no shrinking; past that it wraps), and the meta takes the rest, cut
    with English's own ellipsis. Nostromo's pace chip in a limit card's facts is the other designed cut, where the column beside the
    figure has no more room. Gates moves a meta below its title, as in English, and on the other pages a meta moves below
    the title in every look.
  - The pace line, its verdict and the forecast are sentences: they wrap, a word longer than its line breaks (no hyphen),
    never an ellipsis: `overflow-wrap: break-word` in Nostromo's pace column, `anywhere` in Night City's and Gates' pace
    rows, whose items wrap as flex items and narrow below their longest word only with `anywhere` (#98's second review).
  - A limit card's facts wrap within at most 60 % of the row; their figure (`b`) stays on one line.
  - The meter's two rows share one first column (a grid with the rows as subgrids, `fit-content(50%)`, never under
    English's 112 px), so their bars start at the same x, and the bar keeps at least 96 px (`minmax(96px, 1fr)`); a longer
    label wraps (Gates' English ellipsis on the label stays English's).
  - Nostromo's Overview grid rows grow with their cards (`minmax(auto, ...)`, from 1001 px), as Night City's always did,
    and Night City's slanted cursor after the page heading gets 4 px of room. Without that growth the pseudo-locale's limit
    cards draw their meta over their facts and run their facts off the card (default window and 1060 x 640), and French
    does too at 1060 x 640 (the stand-in, #98's second review).

  A designed cut keeps its whole text in the element (the CSS cuts only what is drawn), in its `title` (a meta's
  `metaAttrs`, which English's subtitles have too; the pace chip's `cutAttrs`, outside English only) and in the card's name
  (`aria-label`, `cardName`: `overview.card.name`, and `overview.card.name_chip` for a limit card, with its chip's word),
  outside English only. `DESIGNED_CUTS` in
  `tests/browser/page_checks.js` (the same list in `tests/test_server.py`) names them, `NEVER_CUT` the elements never cut
  (a nav tab, the link's words, a sentence line, a figure) and `BAR_FLOOR` the meter's 96 px ("Test tools"). The designed
  cuts are the one exception to #98's acceptance "nothing cut in the pseudo-locale"; in a real language the target is
  none in the default window (#108).
  Rooms: each constrained key's note in `en.json` says "room about N px, about C capitals", the narrowest look's room in
  the default window as the stand-in's rooms step measures it (and holds the note to, within 1 px and 1 capital): a nav
  tab 120 px (Night City), an Overview card's meta from 76 px (the surfaces card) to 311 px (the cost card), a limit
  card's facts 120 px and its pace chip's words 106 px (Nostromo, beside the figure at its widest, 100 % as French writes
  it, and the chip's words without its frame and padding: #98's second review), the burn card's chip 147 px, a meter's
  label 112 px. A string over its room goes back
  to its translator; the CSS is not changed to fit it. The header's link words have no px room (they share the row with
  the data age and Refresh), and a sentence line's note asks for no shorthand.
  The data age cannot be read at all where the header hides it and the sidebar's health box is hidden too (every look
  hides the box under 1001 px, where the nav is a row): in Nostromo under 801 px in every language, as at c05f45d, and
  under 1001 px outside English; in Night City and Edgerun under 801 px outside English. Recorded (`AGE_GAPS` in
  `page_probe.js`), not fixed here. English keeps its own overflows, which
  `tests/fixtures/page_overflow_english.json` names element by element, each with the owner's decision that keeps it
  (`owner`), as Chris made them on #98 (owner question 3 on 2026-11-26, then all of them looked at one by one on
  2026-11-27): Night City's page heading (its slanted cursor runs 3 px past the box; nothing is cut), "decided, kept (Chris
  2026-11-26)"; the Overview card subtitles cut with English's own ellipsis (Edgerun's surfaces meta in the default window,
  the others at 1060 x 640, which #98's critic 22 measured first; two of those by less than a px, named since #98's second
  review measures such cuts), "kept until #118 (approved 2026-11-27) moves the subtitle under the title"; and six entries of one find, not a design (at 1060 x 640 Nostromo's weekly and scoped Overview cards
  cut their reset lines and their chip off when the figure has two digits, in the warning and critical scenarios), "kept
  until #117". Until #117 and #118 land the record keeps them all, so English's layout does not change here. The real web
  view's overflow test lets an English Overview subtitle cut pass at any width while its whole text is in its title
  (`english_subtitle_cut` in `tests/test_server.py`): WebView2 cuts Night City's English subtitles wider than the headless
  stand-in measured them (even "share, 7 days" in the default window), so the record's widths cannot pin those; every other
  English overflow stays pinned to its width.
- **Rendering never reads Windows.** The locale rides on the view model (#91, not #93: it is the first ticket in which
  the widgets draw something that depends on the language): `build_model(..., locale=)` fills `WidgetModel.locale`,
  English by default, and the app passes `MonitorApp.locale()`. A locale change redraws, since the frame caches key on
  the model. `dump.sample_model`, `dump.dump_frames` and `tests/frame_digests.digests` take the locale as an argument;
  the golden frames are drawn in English. Nothing under `render/` (or `tests/frame_digests.py`) may reach
  `LocaleSource` or the Windows calls, and one eighth of the golden frames, drawn on a French Windows (fr-CA format,
  24-hour clock) with French offered and a dev instance asking for French, give the same digests as drawn plain
  (`tests/test_i18n.py` checks both). `main.py --dump-frames <dir> --lang <id>` builds its locale in `main.py`
  (`i18n.dev_locale`) and passes it in (#93).
- **Widget and tray words (#93).** The view model, the tray tooltip and the tray menu read the shared catalogs through
  `i18n.app_catalog(language)`, which reads each language once per process (the tooltip is worded on every blink); a
  `Catalog` logs a missing key once, not on every call. The agent modes read no catalog (#90; the test refuses
  `app_catalog` too). The keys: `widget.status.*`, `widget.stale.*`, `widget.limit.*`, `widget.share` and
  `widget.guard.*` (the model's words; only the status word is drawn today, by every skin; the rest are fallbacks for a
  skin with no words of its own, and their notes say so), `tray.tip.*` and `tray.menu.*`. The case is the surface's
  (`LIVE` on a widget, `live` in the tooltip), never made by re-casing; names, commands, numbers and times are
  placeholders. English keeps its text where it is data: a scoped row's label is the limit's own (the identity string),
  a guard label with no model is the limit's label in lower case, and a limit gone from the reading keeps the guard's
  English label in every language (identity text: `test_english_keeps_the_labels_that_are_data`). Outside English a
  scoped row's label holds its scope's name in the language's capitals (Turkish's `HAİKU`) inside the template, and
  its `Text.language` is the template's, English's until the language has the key: that label is final and nothing
  cases it again (English's rule would give the İ a U+0307). The skins word scoped rows from `scope_name`, and
  `test_no_model_text_cased_again_writes_a_combining_dot` holds every other text of the model. The tooltip's lead
  "Claude usage" is `tray.tip.lead`, kept as written in every language as #86's brand mark (Chris may override it);
  its " - " joins and its " (dev)" mark stay code. Its drop rule (`app.tray_tooltip(model, dev=, limit=TIP_LIMIT)`):
  past 127 UTF-16 units the guard's countdown goes, then its resume time, each form a whole phrase
  (`tray.tip.guard_until_in`, `.guard_until`, `.guard_holding`); the percents, the status word and the dev mark stay,
  and only a tooltip still too long is cut (`tray.clip`). English never comes near (113 units at worst); the countdown
  is English's letters in English and the pseudo-locale and CLDR's narrow units in their own case elsewhere. Each menu
  row has an id (`MenuItem.id`: `open_dashboard`, `floating`, `taskbar`, `guard`, `resume_waiting`, `refresh`,
  `report_bug`, `update`, `exit`), and the menu's count has no group sign. The tray icon stays digits only. In
  `en.json` a translator's note `_note.<key>` sits just before its key and the budget note `_budget.<key>` just after
  it. `tests/fixtures/widget_words_english.json`, recorded before #93 moved a word, holds the English of all three
  byte for byte (`tests/test_widget_words.py`, worked out in a fresh Python with the time zone pinned).
- **Skin words (#94).** A skin's own words are catalog keys named surface first, as the other keys are:
  `widget.<theme id>.<group>.<name>`. Nostromo's are `widget.nostromo.state.*` (the header's word), `.guard.*` (the
  hold chip, the armed mark, the hover card's guard line), `.auth.*.line1`/`.line2` (the sign-in box), `.limit.*`,
  `.window.*`, `.share.of`, `.cap.tag`, `.resets.at`/`.unknown` and `.pace.*`; Night City's (#95) take
  `widget.night_city.*` with these groups but `.auth` and `.resets.unknown`, and two of their own, `.band` and `.age`
  (below); Gates' (#96) `widget.gates.*`; `tests/test_widget_words.py`'s key set leaves `widget.<theme id>.*` out. A
  value is in the theme's case (Nostromo's capitals) and nothing cases it again. A name goes
  in in the theme's case (`casing.upper(name, language)` for Nostromo: `HAİKU WEEKLY` in Turkish), the sign-in command
  as typed, and Claude Code's name, a percent, a clock, a countdown and the pace's signed points as placeholders; ages
  and countdowns are formats (`format_age_short`, `format_countdown`). The code keeps the `, ` between the guard line's
  limits, the space before a cap, its closing periods and the ellipsis, which the four languages write alike; it keeps
  the guard line's lead on one line, so a French ` :` never starts the second. `tests/fixtures/skin_words_english.json`
  holds each skin's English as it drew it before its words moved (every helper's text and every drawn run, worked out
  by `tests/skin_words.py` in a fresh Python with the time zone pinned), and `tests/test_skin_words.py` draws every
  recorded state with each key marked, to show every word comes from its key as written. Where a language's words are
  longer than English's, Nostromo's frames give way by pure helpers the tests read; English never meets any of them, so
  its frames are as before:
  - The header (`_header_plan`) gives way in `HEADER_GIVE_WAY`'s order, an owner default (Q2) Chris may overturn. The
    armed mark goes first, as before #94: it is drawn only in the room the rest leaves. Then the state word or the
    `CRITICAL` chip is cut with an ellipsis, since it mostly repeats the pill and in LIVE the colours and the hazard band
    carry the severity; then the age goes; then the pill's word is cut, to one letter at least; last the hold chip drops
    its countdown and reads `HOLD` alone. The age and the countdown are facts the floating widget shows nowhere else.
    The two sides keep `QUALIFIER_GAP` (6 px) apart, and a word cut at one step takes back the room a later step frees.
    `_header_plan` takes the steps in the tuple's order, whatever it is (a text a later step cuts keeps the most room;
    one an earlier step cut is at its floor first), and `tests/budgets.py` derives the header line's give-way and its
    boxes' rooms from the tuple, so an answer changes `HEADER_GIVE_WAY` alone (then `LIMITS` and the `_budget` notes
    are printed again).
  - The hover card's reset line (`_info_plan`): the pace takes its short form, the signed points alone
    (`widget.nostromo.pace.points`, `+21 PTS`), then is left out; the reset never gives way.
  - The sign-in box (`_message_fit`): both lines take one step of `MESSAGE_STEPS`, the tracking to 0 and then 9 px; only
    a line too long at 9 px is cut. The strip's window tags (`_strip_tag`) shrink from 11 px to 9 px (`STRIP_TAG_SIZES`,
    the tracking 0.1 em), keeping `STRIP_TAG_AIR` (2 px) off the bar, then are left out.
  - Marks (Q1, an owner default Chris may overturn): the status pill, the header's chip and a cap tag grow round a word
    whose marks would touch their edge, decided by its characters (`_mark_growth`), so a box is the same at every scale.
    A capital with a mark above grows an outlined box (the pill, a cap tag) 2 px up and the filled chip 1 px; a mark
    below, or a glyph that drops below the line (`DESCENDERS`), grows any of them 2 px down: the header's pill and chip
    then end 1 px over the rule, and a cap tag 1 px over its row's tick (top + 15). English never moves. The conflicts
    with the default, recorded in `MARK_GROWTH_UP`'s comment: a word with no mark but a `DESCENDERS` glyph (Q, a comma,
    a bracket) grows down too (`CRITIQUE`, `CAP 95,5 %`), since its ink reaches the edge; the grown pill's top is y = 4,
    the hazard band's bottom edge, so in a LIVE frame with a critical limit the two meet with no air between (they never
    overlap), where 1 px up would keep the air but leaves the stroke on the mark in the hover card at 1.25x; and the
    deepest glyphs (`¡ Ș ș Ț ț`: `DEEP_DESCENDERS`, `DEEP_MARKS`) grow an outlined box 3 px down (`MARK_GROWTH_DEEP`),
    the least that keeps their ink off it, so the pill then meets the rule and a cap tag its tick.
  - `tests/test_skin_nostromo_fit.py` reads each decision; `tests/test_budgets.py` holds the budgets' give-way to the
    skin's; the frame check (`tests/frame_layout.py`, below) holds every frame clear.
- **Skin words (#95).** Night City's words are `widget.night_city.<group>.<name>`, 38 keys: `.state.*` (the strip's
  status tag, the header's LIVE, the rows' WARN and CRIT chips), `.band.*` (the stale band's four causes together, for a
  translator), `.guard.*`, `.limit.*`, `.window.*` (the strip's 5H and 7D), `.share.of`, `.cap.tag` (`CAP {number}`, a
  number without its sign), `.resets.at`, `.pace.*` and `.age.old` (`{age} OLD`). The brand mark `CLAUDE//USAGE` is
  drawn as written (#86). The rules are Nostromo's: values in capitals, a name in the language's capitals (`HAİKU` in
  Turkish), the command as typed, ages and countdowns as formats; the code keeps the guard line's
  `, `, the space before a cap, its closing periods, a scoped row's name alone, the pace's short form (its signed
  points, a figure) and the cut mark: Night City cuts with its three dots `...`, not `…` (Q3, an owner default: an
  English cut never changes). The guard line's lead is a run of its own, never wrapped, so it keeps its colon whatever
  space a language puts before it. The theme file's `extras.copy` is the mockup's record and is no longer read.
  `STRIP_WORDS` and the `DEFAULT_*` causes stay as English records for the older tests, held to `en.json`. Where a
  language's words are longer than English's, Night City's frames give way by pure helpers the tests read
  (`tests/test_skin_night_city_fit.py`); English never meets any of them:
  - The header (`_header_plan`): the right-hand side keeps `GUARD_GAP` (8 px) off the brand, which never gives way. In
    `HEADER_GIVE_WAY`'s order, an owner default (Q2) Chris may overturn: the hold chip drops its countdown (`HOLD`
    alone), the order Night City's band already took; then the right-hand word (the LIVE tag's, the status chip's or
    the hold chip's) is cut with the dots, one letter at least. Nostromo's default on #94 drops the countdown last. The
    armed guard's GUARD chip is drawn only in the room left, as before.
  - The stale band (`_band_plan`, `BAND_GIVE_WAY`, one tuple for both paths): the hold chip drops its countdown, the age
    is left out, and last the cause is cut. Before #95 the band without the guard cut its cause beside an age that did
    not fit and then dropped the age too; now that Night City writes its age with `format_age_short`, es-CO's
    `100 DÍAS OLD` would have met that. A missing sign-in has no age but keeps the last reading, so the guard can hold
    and its cause sit beside the hold chip.
  - The hover card's line (`_info_plan`, `INFO_GIVE_WAY`): the reset never gives way; the pace keeps `VALUE_GAP` off
    it, takes its signed points (`-18`, in its chip and colour, or plain on a stale card), then is left out; a pace with
    no points (ON PACE, PACE --) is left out at once.
  - The strip: its window labels (`_strip_label`) shrink from 11 px to 9 px (`STRIP_LABEL_SIZES`, the tracking 0.12 em),
    keeping `STRIP_LABEL_AIR` (2 px) off the bar, then are left out (two characters fit); its LIVE, status words and
    HOLD shrink to `STRIP_FLOOR` (9 px, `_fit_type`) and only then are cut.
  - Marks (Q1, an owner default Chris may overturn, as Nostromo's): a box Night City outlines round a word grows round
    marks whose ink would touch its stroke, decided by the word's characters per face (`_mark_growth`), the least growth
    that keeps the ink off at every scale and card height. A capital with a mark above (and in Share Tech Mono one of
    `$()/[\]^{|}`) grows the outlined chip (the header's status and GUARD chips, a row's WARN, the pace chip) and
    Blackwall's LIVE and strip tags 1 px up, Blackwall's reset box 2 px up (`MARK_GROWTH_UP`), and the stale band as
    far as its words ask, its top hazard strip, outline and hold chip with it, the chip's word on the band's baseline.
    A mark below (combining class 202 or 220, as Nostromo's rule: a cedilla, an ogonek, a comma or a dot below, Ç Ş Ș
    Ą Ạ), or a Rajdhani glyph that drops below the line (`DESCENDERS`: `();Q[]gjpqy{}`), grows Blackwall's LIVE and
    strip tags 2 px down; the outlined chip grows 1 px down round a comma below alone. The filled chips and the Edgerun
    plate's tags never grow. English never moves. Two conflicts, recorded in `GROWN_MEETS`: a row's WARN chip grown
    round a comma below meets its row's rail with no air (never overlapping); and the row's pace tick (top + 13 to
    top + 19) crosses the bottom stroke of its WARN chip and of Blackwall's reset box by design, in English too, and
    round a comma below the grown chip's stroke and its last row inside. Every other grown box keeps air round its
    neighbours.
- **Skin words (#96).** Gates' words are `widget.gates.<group>.<name>`, 41 keys, in sentence case (the 5H and 7D tags
  in capitals): `.state.critical` (the footer's lead for a critical limit), `.guard.*`, `.limit.*`, `.window.*`,
  `.share.of`, `.cap.tag`, `.resets.at`, `.pace.*` (the footer's leads, the hover card's pace and its short form
  `.pace.points`, `+21 pts`) and `.footer.*` (each footer's sentences together, a lead and the step the user needs
  side by side, for a translator). The status word is the view model's; the title `Claude usage` is the brand mark,
  drawn as written (#86). Values are drawn as written and nothing cases them again. Ages and countdowns are formats:
  Gates' own English shapes (`12 s`, `41 min`, `2h 14m`) in English and the pseudo-locale, CLDR's narrow units in their
  own case elsewhere; the countdown is the tray's. English's two name quirks stay byte for byte where the template is
  English's (`_english_name`, Q4, an owner default: "Claude design weekly" on a row, "claude_code weekly" in the guard
  bar); in another language a name goes in as written, its first letter in capitals by the language's rule where it
  starts the row's name (`casing.cased`). Where a language's words are longer than English's, Gates' frames give way
  by pure helpers the tests read (`tests/test_skin_gates_fit.py`); English never meets a step it did not take before:
  - The header (`_header_plan`) in `HEADER_GIVE_WAY`'s order, an owner default (Q2) Chris may overturn: the pill's age
    is left out (as before #96), then the word shrinks from 10 px to 9.5 and 9 (`PILL_SIZES`), then it is cut with `…`,
    one letter at least. The title and the guard's shield or capsule never give way; the lamp still tells the status.
  - The hover card's line (`_info_plan`, `INFO_GIVE_WAY`): the reset never gives way; the pace keeps `INFO_GAP` (8 px)
    off it, takes its short form (its signed points, in its colour), then is left out; On pace has no short form.
  - The sign-in footers (`_footer_plan`, `FOOTER_GIVE_WAY`, Q3, an owner default; `STEP_FOOTERS`: a stale reading's
    and a missing sign-in's): when the lead and the step do not fit together the lead is left out and the step drawn
    whole from LEFT, without the space that followed the lead; only a step too long for the line is cut. The other
    footers keep their cut: the lead is the news, the rest is cut to the room it leaves, as English's own widest guard
    footer is.
  - The strip's window tags (`_strip_tag`) shrink from 11 px to 9 px in half pixels (`STRIP_TAG_SIZES`), keeping
    `STRIP_TAG_AIR` (2 px) off the rail, then are left out; any two capitals or figures fit at 11 px.
  - Marks (Q1, an owner default Chris may overturn): the filled pill and cap badge hold every mark, so no box grows. On
    a plate too short for the second row's 14 px line (under 32 px: the 30 px plate of a short taskbar, and the 30.8
    to 31.6 px plates some taskbars give) the second tag rises `STRIP_TAG_RAISE` (2 px, the least that keeps every mark
    on the plate; 1 px does not) where it holds a mark below (combining class 202 or 220, as Nostromo's and Night
    City's rule) or a glyph of its face that drops below the line (`DESCENDERS`: `()[]_gjpqy{|}¡¿`, `µ þ ŋ ǥ ȷ` and
    the face's other Latin letters that do, measured over the Latin blocks it maps; a language in another script adds
    its letters to that test), decided by its letters (`_strip_tag_raise`). English's tags never rise. Three of the
    face's letters no language writes (`ȴ`, `Ḁ`, `ḁ`) still leave a 32 or 33 px plate by 1 px unraised: the raise is
    held to plates whose line runs past them. The conflicts, recorded in `STRIP_TAG_MEETS` for tags of capitals or
    figures (the strip tags' rule): raised, a second tag's capital with a mark above can meet the first tag's ink where
    that one reaches the foot of its line (Q, a capital with a mark below) by up to 3 px; and unraised by up to 1 px,
    as before #96, on any plate whose rows are 14 px apart (under 36 px, the 34 px plate too).
  - `tests/test_budgets.py` holds the budgets' give-way to the skin's; the frame check (`tests/frame_layout.py`,
    below) holds every frame clear.
- **Toast and alert words (#97).** The tray's toasts read `toast.*` (18 keys, a top-level group: `tray.*` holds the
  tooltip's and the menu's words), worded in the app's language when each is shown, so a language change reaches the
  next one. `app.py` words the release toast (`update_toast_title`, `update_toast_body`: `{app}` is `app.APP_NAME`, the
  monitor's name as written, `{version}` a plain version) and the note after a silent update (`update_note_title`,
  `update_note_words`, `update_note_text`; see "Updates"), reading the locale through `toast_locale(self.locale)`
  (English when it cannot be read). `data/alerts.py` words the alerts from `toast.alert.*` (`_title`, `_name`,
  `_reset_text`, `_credits_text`). English keeps its own case of a limit's name byte for byte: `_name`'s
  `text[:1].upper() + text[1:].lower()` over the catalog's English, the scope in capitals first ("Claude opus 5
  weekly"; `tests/test_casing_sites.py` lists the four calls). Every other language, the pseudo-locale too, takes the
  catalog's words as written and puts a name in as written, its first letter in capitals by the locale's rule only
  where it starts the title (`_starts`); the template's words are never cased. The `gates` skin's row differs: it keeps
  English's case wherever its own template is English's (#96 Q4), so until a language has both keys a row and an
  alert can name one limit differently ("Claude design weekly", "Claude Design weekly"), as the name keys' notes say. A
  kind no catalog has words for is named from its code (identity text), a limit with no label by its kind, else by its
  model.
  A reset announced as its window ends is named then, in that moment's locale, from the kind, label, model and surface
  kept from the limit's last reading. English's credits keep the code after the last amount (`{limit}` is
  `10,000.00 USD`). `app.UPDATE_TOAST_BODY` and `UPDATE_NOTE_TITLE` stay as English's records, held to `en.json`; no
  `MonitorApp` method names them. Each toast's budget box composes every toast the code words in the language
  (`tests/toast_words.toast_sweep`: every format tag and clock, three release versions, every count pair of the note,
  the alerts of every name shape, a week of reset times, credits with and without a limit and a currency) and carries
  its keys: English keeps within Windows' 48 characters of title and 200 of text (47 and 180 at worst), and no
  language passes the tray's 63 and 255 or the note's 250; the pseudo-locale's longest note (one session and 9999
  waits) sits exactly at 250 (`test_budgets.LONGEST_NOTES`). `tests/fixtures/toast_words_english.json`, recorded before
  #97 moved a word, holds every English toast and alert byte for byte (`tests/test_toast_words.py`, worked out in a
  fresh Python with the time zone pinned).
- **Installer and uninstaller (#103).** Setup's and the uninstaller's own texts (24 messages: the boxes, the status
  lines, the download page, the Finish page's sentences, the task and run boxes) are `[CustomMessages]` in
  `installer-messages/<lang>.isl`, one file per language, each the second `MessagesFile` of its `[Languages]` entry after
  Inno Setup's own file (`compiler:Default.isl`, `compiler:Languages\French.isl`, ...), which words Inno's pages and
  buttons. `installer.iss` stays ASCII and names each text by `{cm:Name}` or `CustomMessage('Name')`; `en.isl` holds
  the English with a note above each message (where it shows, its arguments, its line breaks), and `fr.isl`, `es.isl`
  and `tr.isl` the drafts (#104 to #106), empty until then. The entries' names are the app's ids (`en`, `fr`, `es`,
  `tr`). English is the first entry, so it is Setup's language for a Windows language it does not offer, and ISCC fills
  a message a language's file lacks from the first language that defines it: English, one warning per missing message
  and language. The release build declares the languages the app offers (`i18n.OFFERED`, English alone today); the
  others sit behind `#ifdef AllLanguages`, the review build (`ISCC /DAllLanguages`), never published, and a language
  moves above it in its sign-off change (#107).
  Setup's language: `LanguageDetectionMethod=uilanguage` (Windows' display language, `GetUserDefaultUILanguage`: the
  entry with its LANGID, else one with its primary language, else English), `ShowLanguageDialog=no`,
  `UsePreviousLanguage=no` (Inno Setup would otherwise take the last install's language, in a silent run too); a
  `/LANG=<name>` naming an entry comes first, and one naming none is ignored. The in-app update passes the app's own
  language, `/LANG=<id>` (`MonitorApp._apply_update`, `app.setup_language`; `update_check` passes only an id of
  `SETUP_LANGUAGES`, the entries' names, so never the pseudo-locale, and nothing for anything else), so Setup and the
  uninstaller it leaves speak the language chosen in the app; a build that lacks it passes over it. An updater from
  before this change (v1.1.5's, 2.3.0's and older) passes none: that update, like a first install by hand, follows
  Windows' display language, the app's pick for the same display language; the inputs can differ, as "Detection"
  says. The uninstaller speaks the language of the last Setup run (Setup records it and hands it
  that language's messages) and passes it to the exe as `--remove-setup <report> --lang <name>`, after the report's
  path, for #102 to word the report in; `main.py` ignores it until then. Inno Setup has no plural forms: each count has
  two messages, `...One` for 1 and `...Other` (the count as `%1`) for more, chosen in `EndedNote`: CLDR's choice for
  every count from 1 to 999,999 in all four languages. Setup's boxes are all `SuppressibleMsgBox`: in a silent run with
  `/SUPPRESSMSGBOXES` (every update passes it) a box is not shown but logged with its text
  (`Defaulting to <answer> for suppressed message box (<buttons>):` and the text on the next line); a silent run without
  that switch shows it (logged as `Message box (<buttons>):` and the text). The uninstaller's three boxes (a monitor
  that stays, deleting the settings and usage history, what is left in Claude Code) are plain `MsgBox`es behind
  `UninstallSilent`, never shown or logged in a silent uninstall. Names stay as written in every language (#86): the
  product, the publisher, the Start-menu folder and its two shortcuts, and the name in Apps
  (`UninstallDisplayName=Claude Usage Monitor version {#AppVer}`, which Setup would otherwise build from the language's
  `NameAndVersion`). `tests/fixtures/installer_words_english.json`, recorded from the script before #103 moved a word,
  holds every English text and name byte for byte; `tests/test_installer_words.py` renders each site of today's script
  with `en.isl` against it (`tests/installer_words.py`), and holds each language's file to English's shape (arguments,
  line breaks, the access key, no literal `%`, the names that stay, UTF-8 without a BOM, NFC). A language the release
  build declares must have every message; the others' gaps are listed (`python tests/installer_words.py --todo`). Inno
  Setup is 6.7.1, exactly: the script's guard refuses any other version.
- **Test tools (#89).** The pseudo-locale (`i18n.PSEUDO`, `pseudo`) is `en.json` made longer (about 40%), accented
  (`İ ı ğ ş é ñ` among the look-alikes) and bracketed (`i18n.pseudo_text`), so English left over, a missing glyph
  and a cut end all show. A dev instance shows it with `USAGE_MONITOR_LANG=pseudo`, and the dev server with
  `--lang pseudo` (or any catalog language, offered or not); it is never offered and never a setting, and its formats
  are English's with #91's two changes (see "Formats"). `python -m usage_monitor.i18n` runs the catalog check
  (`i18n.check_catalog`) on every catalog language. It fails on a key `en.json` lacks, a key that is not a dotted name,
  text that is not plain NFC, a placeholder that is not a plain `{name}` or differs from English's, a command
  (`i18n.COMMANDS`) inside a string, plural forms other than the language's CLDR categories, and a Save-dialog filter
  label (a key ending in `.filter`) that pywebview's rule (`i18n.FILE_FILTER`) refuses. The keys a language lacks fail
  it only once it is offered; until then they are its to-do list. Width budgets (`tests/budgets.py`) list the widgets'
  fixed boxes with their room, and the lines that hold several side by side. Each box is measured as its skin draws
  it: Nostromo on its own canvas at every display scale and at the smaller scales a short taskbar draws its strip at,
  Night City through its ink and its fit function (a box that shrinks its text is measured at the size it shrinks to),
  Gates in its own faces (its pill word and strip tags at the size they shrink to, `gates_fit`, `gates_tag`), the
  tray numbers in device px, and the money figures, the tray tooltip and the toasts in
  UTF-16 units. A pixel box's budget is its room, or what English already takes where it runs past by design; a
  character box's budget is its limit. Each language's longest figures go in too, as `formats` writes them for every
  format tag (`100 %`, `%100`, `5,96 $`, each tag's times and day clocks on either clock, the widest countdowns and
  ages), in capitals in Nostromo and Night City and glued in the guard's statement, as the skins draw them; the
  dashboard's money boxes take the page's own widest figures (`PAGE_MONEY`), tight as the page draws them. A box's
  catalog keys (`Box.keys`) are measured in each language too; since #93 the boxes that draw the view model's status
  word carry its key, and each key's `_budget.<key>` note in `en.json` names every box it sits in. The tray tooltip is
  a composing box (so is each toast's since #97, "Toast and alert words"): it measures the worst-case tooltips
  `app.tray_tooltip` words in the language, after the drop rule
  (`tests/widget_words.tooltip_sweep`). Those are two limits at 100 % in each status, in every format tag and clock,
  as a dev instance, with the guard holding at a resume clock and a countdown of every length they take: the clock
  alone (a resume the same day) and with its weekday (another day), on every weekday at hours of one and two digits
  before and after noon; the countdown at every minute under a day, every hour from a day to a week, and under a
  minute. Clock and countdown are paired freely, so a figure can come out longer than the longest real tooltip, never
  shorter.
  Since #94 a key with a placeholder is filled as the skin fills it (`Box.fills`: each placeholder takes the language's
  longest figures, the signed points, an age, a cap's number, a name, Claude Code's name or the command), or its box
  composes its whole texts (each skin's guard line composes through the skin's own `_guard_statement`). A box of a skin
  whose own words are keys (`budgets.WORDED`: Nostromo, Night City and Gates) is measured in the pseudo-locale by
  those keys made pseudo and filled, not by its English made pseudo, and a box of figures alone, or Night City's and
  Gates' brand marks (`Box.words=False`), by the text as the pseudo-locale draws it. A line
  gives way as its skin does (`Line.gives_way`: a box cut to its first letter and the ellipsis, left out, or replaced by
  another box) and is reported only if it is still over after its last step; Nostromo's sign-in lines and strip tags
  are measured at the step they shrink to (`nostromo_fit`). Nostromo's header boxes take their rooms from Q2's order
  (`_header_room`), and in a language its own rooms (`header_room`): beside its own widest texts and each format tag's
  own age and hold chip, so a text past them is reported as the cut the header makes. Night City's header, band and
  hover line take their give-way and their boxes' rooms from the skin's tuples the same way (`NC_PLANS`, `_nc_room`; a
  box that gives way by becoming another, the hold chip HOLD alone and the pace its points, has that box's room), and
  in a language its own (`nc_room`, beside each format tag's own ages, hold chips and resets, `nc_tags`). Its GUARD
  chip, chosen last in the room the header's word leaves, is held to the least of the headers English draws it in
  (LIVE's and PAUSED's: the stale band takes the header's place in the other statuses), beside the language's own
  LIVE tag and paused word (`nc_guard_room`). Its strip
  labels are measured at the size they shrink to (`night_city_label`), and the pace's points, which have no key, as
  English writes them in every language. Gates' header, hover line and footers (#96) take their give-way and rooms from
  its tuples the same way (`G_PLANS`, one plan per footer kind, so no lead is budgeted beside another kind's rest; the
  header's word carries the pill's age beside it where Q2's order keeps it there, `GHeaderPlan`, so any order of
  `HEADER_GIVE_WAY` is followed; a sign-in step past its room beside its lead is reported as the lead left out,
  `Box.gives`, since the step is drawn whole), and in a language its own (`g_room`, beside each format tag's resets,
  ages and the widest guard rest its clocks and countdowns make, `g_tags`); its rows' rooms are beside the language's
  own widest label, tag and value. The pill's text, its
  word and age, is held per status to the least of the headers English shows that status's widest age in, beside the
  language's own word, at the size Q2's order draws it whole at (`g_pill_room`: RATE LIMITED's without the holding
  capsule only; each status word's note gives its own), and the cap badge to the least
  its rows leave it where English shows its widest badge (`g_badge_room`). `ACCEPTED_CUTS` in `test_budgets.py` holds,
  apart from `FOR_91`, the cuts a CLDR format causes: the ages Nostromo's header leaves out beside a tag's own hold chip
  (every French tag's but fr-CA's, every Spanish tag's, and in es-CO every age) and the state word Spanish's widest ages
  leave less room; and Night City's (#95), each with its tags and whether a draft's word could settle it: fr-CA's
  12-hour day clock, which the floating row leaves out and beside which the hover line's pace takes its short form;
  the 12-hour day clock of es-ES and most Latin American tags, beside which the pace takes it too; and es-CO's spaced
  day, which the band leaves out beside its widest cause; and Gates' (#96): es-CO's spaced day, beside which the pill
  leaves its age out; French's and Spanish's spaced percent (every French tag but fr-CH; es-ES, es-EA, es-GQ, es-IC
  and es-PH), beside which a full share's row leaves its cap badge out; and most French and Spanish tags' 12-hour day
  clocks (fr-CA's and es-CO's the widest), whose guard footer's rest is cut at its end as English's widest is. A
  draft's shorter word could settle each.
  `python tests/budgets.py --tooltips` prints each language's
  longest tooltip, which `test_budgets.py` pins as `TOOLTIPS` (en 113, fr 120, es 121, tr 114, pseudo 127 units today;
  the pseudo-locale's is a same-day resume that keeps its countdown at the shell's limit).
  `tests/test_budgets.py` holds each box's measure to the widths the skins work out as the frame dump draws, and every
  drawn run to a box. It pins every room (`python tests/budgets.py --limits` prints them after a skin
  change), fails English that runs past a room it did not run past before, and fails an offered language past a box
  that has no fallback or only shrinks; `python tests/budgets.py --lang <id>` reports any language. The "no English
  left" scan (`tests/english_left.py`) lists the English the frame dump, the tray tooltip, the tray menu and the
  dashboard show under the pseudo-locale. Since #93 the widget half draws in the pseudo-locale a dev instance shows,
  reads each sample state's tooltip plain and with the dev mark, and the menu with every row; since #97 it also reads
  every toast and alert of the toast sweep, with the data a toast holds taken out (`toast_names`,
  `toast_format_words`: the limits' names it words, and as whole words the commands, the currency codes and the
  release versions). The dashboard runs on a
  small DOM (`tests/mini_dom.js`, `tests/english_scan.js`) through every
  dev-server scenario, as an installed monitor and as a dev instance, and in some scenarios with its actions, its
  update banner, Settings during an update in each state of its run (the `update-run:*` passes, #58; a lost link and the
  monitor closing need timed asks the scan does not make, and the page tests hold them), what each update button says
  for every answer the monitor gives (the `update-said:*` passes, #97: Settings' three buttons and the banner's Update
  now and Skip this version, each answered with a result, a refusal by status, no answer or a wait) and its loading and
  failed states. The scan leaves out numbers (the pseudo-locale's U+202F groups and
  U+00A0 before `%` read as English's, and a digit, a plain space and `%` as a digit and `%`, since a widget's wrap
  gives the glued U+00A0 back as a space), what the page marks as data (`translate="no"`, or a `lang` other than the
  page's), the names the skins draw, the product name and #86's brand marks. On the widgets it also leaves out the dev
  instance's "(dev)" mark and, as whole words, the pseudo-locale's weekday names and AM/PM, which are English's formats
  (#91).
  `tests/test_english_left.py` holds the result to `tests/fixtures/english_left.json`, which each extraction ticket
  (#93 to #101) shrinks by recording again (`python tests/english_left.py --record`, which adds new English only with
  `--allow-new`). #98's passes open Overview and Limits: `status:offline`, `status:rate_limited`, `status:paused` and
  `status:loading` (each with the detail the data layer words for it, `english_left.STATUS_DETAILS`), `link-lost` (the
  monitor never answers the state), `reports-stale` (Refresh pressed while every report fails) and `gates` (the state's
  theme and the shell's). The only English they add is those four details (#102's), recorded with `--allow-new` and held
  by `test_english_left.py`. The dashboard half also takes out, as whole words, the page's weekday and month names and
  AM/PM, and the dev server's plan, scope and surface names (`dashboard_format_words`, `dashboard_names`), which the
  catalog's sentences hold as plain text.
  #98's English guard: `tests/page_words.js` runs the real page (init, the poll loop, the router and the theme scripts it
  loads) on the small DOM against a server of its own that answers from `tests/fixtures/page_words_inputs.json` (the dev
  server's answers at 2026-10-21 16:12 UTC, frozen), with a still clock and a timer queue (TZ=UTC). It reads each region
  (the header's parts, the sidebar, every top part of every page, every card of Overview and Limits, Cost's value and
  credits cards, Efficiency's pipeline card) as drawn in each of the five looks: each text node in the case its element
  takes from the look's sheets (`tests/css_case.js`: the cascade, with the browser's own `text-transform: none` on
  `button`, `input`, `select` and `textarea`), in Gates after `gates.js`, with the attributes people read. `--record`
  wrote `tests/fixtures/page_words_english.json` from the code before #98 moved a word, and refuses to write it again;
  `--record-more` wrote `tests/fixtures/page_words_english_more.json` the same way, from a copy of that code (`git archive
  c05f45d`), for answers the first record lacked (`MORE_CASES`: a 400 and a 404 that carry the monitor's own text, and the
  stale banner after a 400). `test_webui.py` holds the page to both, and holds English's cards to no `translate` mark (as
  before #98). Its other outputs feed the gated tests: the first chrome (before the first
  state, after the 1 s tick and after a failed first poll), the entrances (`motion.js` and every theme script under
  reduced motion, each director's settings kept: every element a director animates holds its words as its own text, in
  English and on a French page) and the French page's unmarked English. The small DOM has a `MutationObserver` (its
  records delivered level by level, where a browser delivers them per task) and a tree walker, for `gates.js`.
  In a real browser, `tests/browser/page_probe.js` (Playwright for node with an installed Chromium, not part of pytest)
  is the headless stand-in for the real-web-view tests below. It runs dev servers of its own on 127.0.0.1 with throwaway
  settings, their clock and the page's held at the record's moment, the page in UTC. Its steps: widths (Overview and
  Limits in English, the pseudo-locale and French, every look, the normal, warning, critical and stale scenarios and four
  states it answers itself, in 1200 x 780, 1060 x 640 and 720 x 480 windows), clip (the clip test's measure at its four
  sizes, in English and the pseudo-locale), header (every language and look, each link word, the lost link too, at page
  widths from 704 to 1184 px: the bar's one row never runs past the window, Refresh stays inside it, nothing in the bar is
  cut, `header()`), caps (the CSS capitals, in the normal scenario and then in the states it does not draw, each answered
  as `page_words.js --worlds` answers its case: the status, stale and problem banners' headings, every pace verdict and
  chip and every burn velocity, each view's regions equal to the record's view of that case), record (the page answered
  from the page-words inputs: every region equals the record's live view in every look and page), update (#97's lines)
  and motion (without reduced motion, in English and French: every element a theme's entrance animates is animated). The
  widths step also judges each chip (`chips()`): its words' ink, from the font's metrics, clear of a 1 px frame, and the
  chip inside its card. Outside English (owner question 1) it passes a cut with an ellipsis only as a designed cut
  (`DESIGNED_CUTS`): in one of its looks, its text a whole catalog string, its `title` that text and an ancestor's
  `aria-label` holding it, and in a real language never in the default window but where English's own cut shows through
  a key the language has no words for yet. It fails any other ellipsis, an ellipsis set on a `NEVER_CUT` element, a nav
  tab over two lines, a chip whose word wraps and a meter bar under `BAR_FLOOR`, and on Limits it reads the first meter
  again with each label far longer than a translation should be (`meterStress()`). On the Overview, outside English, it
  writes what the real words never reach (#98's second review): the burn card's chip longer than its room beside the title
  (`burnStress()`: it moves below the title, leaves the title's lines as they were and stays in the card), a nav tab's
  label long enough for two lines (`navStress()`: its words stay in the tab) and one word far longer than a line in the
  first limit card's pace line, verdict and forecast (`sentenceStress()`: each breaks inside the card). An element cut with
  an ellipsis counts as cut once its words run past its content box by more than Chromium's 1/64 px layout unit, which
  whole-px scroll widths do not show under about 1 px (`measure()` gives such a cut's `by` to 0.1 px). In English every
  overflow the record names for a view must be measured there (#98's third review: a record entry the page, or the
  measure, no longer shows fails), and before the step `measure()` is checked on a page of its own (`measureSelfCheck`:
  an ellipsis cut by a third of a px is one item, its `by` under 1 px). The header step also fails where the
  data age is in neither the header nor the health box but the gaps `AGE_GAPS` records, and outside English it writes the
  link's words twice as long: they wrap to at most two lines inside the bar. The rooms step (English, the default window)
  measures each constrained text's room (`rooms()`) and holds each key `ROOM_KEYS` names to its note. What it reads from
  the page is `tests/browser/page_checks.js`, which the real-web-view tests
  evaluate in WebView2 too. The capitals of the elements only those states draw (the banners' headings, the verdicts
  and velocities the normal scenario does not show) are read in a browser by the stand-in alone; the real-web-view
  capitals test reads the normal scenario, and the static cascade (`tests/css_case.js`) covers the rest. Its limits: headless
  Chromium at a device pixel ratio of 1 (WebView2 at 125 or 150 % display scaling rounds text widths otherwise), and the
  daily chart's axis may draw a date fewer than the small DOM's (a note, not a failure).
  The real-web-view tests in `tests/test_server.py` (gated; they open WebView2 windows parked off screen): the clip test
  in English and the pseudo-locale; `test_overview_and_limits_overflow_nothing_in_the_real_web_view` (every look,
  English and the pseudo-locale, `measure()` at the three sizes, English allowed only the record's known overflows, each
  measured in every view it names (since #98's third review; a cut under a px at a device pixel ratio of 1 alone), and the
  pseudo-locale only the designed cuts, judged by `overflow_problems` as the stand-in judges them, and since #98's second
  review `chips()` there too, each chip held by `chip_problems` to the stand-in's rules: its ink clear of a 1 px frame,
  its frame inside its card, and outside English its word on one line);
  `test_css_capitals_read_as_todays_english_in_the_real_web_view` (the header, the sidebar and the headings against the
  page-words record, the CSS capitals, and #97's update texts, which #97 left to #98); and
  `test_webview2_follows_the_language_the_dashboard_passes_in_the_real_web_view` (`navigator.language` follows
  `--lang`). The page they measure is a copy of `webui/` whose `index.html` loads a script that holds `Date` at the dev
  server's moment (`held_webui`), so the page draws what its stand-in draws. They read a page once `settled()` says no
  boot and no entrance is playing: WebView2 plays both unless Windows reduces motion.
  The frame check (`tests/frame_layout.py`, held by `tests/test_frame_layout.py`; no window, so not gated, #94) draws
  every state `tests/skin_words.py` records for the skins in `frame_layout.THEMES` (Nostromo, Night City since #95
  and Gates since #96) in English, the pseudo-locale and the French, Spanish and Turkish dev locales: the floating
  widget and the hover card at every display scale up to 200 % (1.75 too) and the strip at every scale a taskbar draws
  it at. Nostromo draws its first variant (every variant's runs are the first's, checked at 1.0 in the pseudo-locale).
  Night City's looks place their runs differently, so its jobs are per look (`NIGHT_CITY_JOBS`): each layout that
  draws its runs apart (Blackwall, Relic, Edgerun, and Edgerun with `transparent_bg`, which drops its plate there) in
  the pseudo-locale, English, French, Spanish and Turkish, and fr-CA's and es-CO's formats on a 12-hour clock
  (`TAGS`). The variant check, in the pseudo-locale alone, holds every variant to the first of its layout
  (`layout_of`), with and without `transparent_bg`: Mox to Relic, and Blackwall and Relic drawn with `transparent_bg`
  to their own runs, which covers those three in the other languages. Beside the recorded states it draws a missing
  sign-in that keeps its reading while the guard holds (`night_city_states`). Gates (#96) draws its first variant in
  the pseudo-locale, English, French, Spanish, Turkish and the two tags (`GATES_JOBS`): its text is recorded with the
  most ink any of its colours gets (`GAMMA_LIGHT_MIN`, white text), which holds every variant's, and the variant check
  holds every variant, with and without `transparent_bg`, to the first's runs. Beside the recorded states it draws two
  strip tags from a test catalog at every strip layout (`GATES_WORDED`: `WWI`, shrunk, and `7Ç`, raised), since the
  pseudo-locale's are left out. Its shapes are recorded, not painted, and its text is drawn at output size, where its
  ink lands; its frames also fail a run that touches a bar (`Frame.bar_touch`, 1 px of air), but for `KNOWN_TOUCHES`
  (the pseudo-locale's share tag, whose `İ` meets the bar of the row above). The check hooks Night City's own drawing:
  its ink queue's text layers, a value's numerals and sign as one run, its chips' polygon outlines and fills, its
  hazard tiles, bars, pip and body. Each job is a process of its own, all side by side. It fails a run whose ink
  (coverage 64 of 255 and up) shares a pixel with another run or comes within 2 px of one on its line, meets an
  outline (or touches its stroke with no buffer px of air), a rule, a bar or the hazard band, crosses a fill's edge,
  or leaves the frame's body, its chamfered corners included. `KNOWN` lists what English draws so by design (the
  strip's 5H value at 100 % on its own bar's cells, on the budgets' record too). A cut a fit makes in the
  pseudo-locale (`[THR…`) is the pseudo-locale's to the English scan: a
  bracketed string a run ends inside with the ellipsis was cut.
- **Fonts (#92).** The widgets draw U+202F, the narrow no-break space of French grouping and of some 12-hour clocks, as
  U+00A0 (`engine.drawn`), in all three run builders. No bundled face has U+202F, and U+00A0 is as wide as a space in
  every face the widgets use, so a figure measures the same on every machine. A theme role may list fallback families,
  `fonts.<role>.fallback`, found as `family_font_path` finds any family (bundled first, then Windows); the page never
  sees the list. A character the run's face lacks comes from the first fallback face whose character map has it
  (`engine.face_index`). `render/glyphs.py` reads the maps, each read held to the cmap table and each map to its
  format's order (segments and groups ascending, none overlapping), as FreeType's validator holds them; a map it cannot
  read gives `None` and one warning, and the run is drawn in its own face. A donor glyph is drawn at the run's size
  with its own advance, and the run keeps its own face's metrics. The engine, Night City's `_advance_gap` and Gates'
  `_text_px` and `_run` pick the face alike, so a run measures what it draws. Night City's mono lists Consolas, then
  the bundled IBM Plex Mono, for the ğ Ğ ş Ş İ that Share Tech Mono lacks; Nostromo and Gates list none. An
  `engine.Donor` with `chars` (a CSS `unicode-range`) is tried before the run's own face, for S3 (#61). The dashboard
  falls to Consolas through its own stacks (`--font-mono`, `--f-data`), so with Consolas both draw the same letters.
  Without it the page takes them from the browser's `monospace` and the widgets from Plex: accepted.
  `tests/test_font_coverage.py` is the gate for every face: each character of the catalogs, the pseudo-locale, the
  CLDR formats and a floor of French, Spanish and Turkish letters must be mapped, with an outline, in the face that
  draws it. It reads every shipped theme through the skin `get_skin` gives it, so a new theme, skin or face (S3, O3)
  joins through its theme JSON and its skin's `_FACE_ROLES` with no edit to the test.
- **Stays English:** the agent modes and `state.json`, the logs (Setup's and the uninstaller's `Log` lines too), the
  export's columns, and the dashboard window title "Claude Usage Monitor" (the installer and the app find the window
  by it).

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
- **The page needs the key.** `GET /` writes the token into the page, so serving it to anyone who can reach the port
  would hand the token to every other account or session on the PC (fast user switching, terminal servers, a shared
  family PC): they find the port with `netstat`, read the token and have the whole API. The Host and header checks only
  stop web pages. So `GET /` answers `401` (plain text) unless the query holds exactly one `k` equal to the token,
  compared in constant time, and `ApiServer.url` is `http://127.0.0.1:<port>/?k=<token>`. The dashboard process
  opens that address (it has the token in `USAGE_MONITOR_DASHBOARD_TOKEN`, which only the same user can read), and so does the
  browser fallback; the dev server prints it. The key stays in the address, so a reload works; `Referrer-Policy:
  no-referrer` keeps it from leaving, and the request log drops the query. `/static/` holds only the app's own files
  and needs no key.
- **The shell.** `GET /` serves `index.html` with the per-run token and the active theme already in
  it: `data-theme` and `data-variant` on `<html>`, the colour tokens as inline custom properties
  (the `css_vars` of `/api/state`) with `color-scheme`, the `color-scheme` meta and the theme's own
  stylesheet link. The page sets the same values when its first state arrives, so the first frame is
  never another theme's. A theme that cannot be resolved, or a shell without the markers, is served
  as it is. The dashboard's saved choices (`dashboard_prefs`, below) go in the same way, as JSON in
  `<meta name="monitor-prefs">`, with only the names and values the page knows. The page reads them before it
  first renders and saves a change with `POST /api/settings`: its own storage does not last, since pywebview's
  private mode (its default) keeps nothing once the window closes and the browser fallback's port is new on every
  run (#54).
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
| GET | `/?k=<token>` | `index.html` with the token, the active theme and the saved dashboard choices injected (below); `401` without the key |
| GET | `/static/<path>` | webui assets (fonts cached; HTML/JS/CSS `no-store` in dev) |
| GET | `/static/i18n/<lang>.js` | a catalog as the page loads it, built from `i18n/<lang>.json` (#87; "Languages") |
| GET | `/api/state` | MonitorState + settings subset + theme + `update` block + version + `locale` (ETag) |
| GET | `/api/themes` | themes with variants and tokens; the Gates `system-*` variants show the live Windows accent, as applied |
| GET | `/api/analytics/<name>` | statistics report: `cost`, `efficiency`, `pacing`, `history`, `sessions` (below) |
| GET | `/api/export?format=csv\|json` | daily rollups download (below) |
| GET | `/api/supporters` | supporters list (cached for an hour; failures come back as an empty list plus the reason, and the About page asks again at its next visit) |
| POST | `/api/settings` | validated partial settings update (below) |
| POST | `/api/refresh` | `DataService.refresh_now()` |
| POST | `/api/dashboard/closed` | `{"pid": n}`: the dashboard process says its window is gone (`dashboard_closed_cb`, wired to `DashboardLauncher.window_closed`); `400` without a pid, `501` where nothing manages the dashboard |
| POST | `/api/update/check` · `/api/update/apply` · `/api/update/skip` | updater (see "Updates") |
| GET | `/api/update/status` | the record of a running update: phase, bytes, failure code (#58; see "Updates"); `501` without a source |
| GET | `/api/guard` | the usage guard for the Settings card: `{guard, told, waiting, watching, setup}` (below) |
| POST | `/api/guard/resume` | "Resume now": every running `--wait` ends at once, whatever the limits say |
| POST | `/api/guard/setup` | `{"action": "install" \| "remove"}`: the one-click setup (below) |

The MonitorState's `detail` (#90) is the status as a code and its values (`{"code": "offline", "values": {...}}`, or null);
`status_detail` is its English text. The dashboard still reads `status_detail`; it does not read `detail` yet.

### The usage guard: `/api/guard` (#27)

`ApiServer(..., guard_dir=None, setup_cb=None)`. `guard_dir` is the folder of the guard's session files
(`agent/sessions.py`: `hook-seen.json`, `waiting/`, `resume-now`), the app data folder by default.
`setup_cb` maps `status`, `install` and `remove` to callables, by default `agent/setup.py`'s.

- **`GET /api/guard`** answers `guard` (the state's guard block, `{}` before the first cycle), `told` (the
  sessions told of the current holding episode, with when and who: `main`, whether its main conversation heard it,
  and `agents`, how many of its agents did; #41), `waiting` (the live `--wait` processes, oldest
  first), `watching` (the live `--watch` processes, oldest first: the conversations that wait for their background
  work, and that work; #41) and `setup` (`setup.status()`). The status runs `claude --version` and `claude mcp get`, so it is kept
  for `SETUP_STATUS_TTL_S` (10 minutes) and computed by one look at a time (`_TimedValue`). A setup click
  and `POST /api/dashboard/closed` clear it, so each dashboard window and each click look afresh. The answer
  waits for a look at most `SETUP_STATUS_WAIT_S` (0.5 s): past that the look goes on in the background, and
  `setup` is the last status, or `null` until the first is in, which the card shows as pending (#32). The card
  asks every `GUARD_POLL_MS` (3 s) while it is on screen, so the setup part fills in at a later ask.
- **`POST /api/guard/setup`** answers `setup.install()` or `setup.remove()` as it is: `ok: false` with
  `errors` is a refusal in plain words (Claude Code too old, a settings file that is not JSON), shown beside
  the buttons, not an HTTP error. An unknown action is `400`.
- **A dev instance never edits Claude Code's settings.** Unless it is handed a `setup_cb` (the dev server
  hands it its sandbox), it answers that setup is off and refuses both clicks, as it never touches the Run
  entry either. `setup.py` itself also refuses outside the frozen build: from source there is no exe to point
  the hooks at.

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
| `language` | `system` (default), `en`, `fr`, `es` or `tr`. A language that is not offered yet is stored but reads as `system` (#87) |
| `dashboard_prefs` | `null`, or an object with any of the dashboard's choices: `costWindow` and `efficiencyWindow` (`session`, `today`, `week`, `month`, `cycle` or `all`), `folderMode` (`folder` or `launch`), `calMetric` (`cost` or `tokens`), `matrixMetric` (`cost` or `requests`). The page sends only the choice that changed: an object is merged into the held choices (a stored entry the page does not know is dropped), so two windows never undo each other's; `null` clears them all |
| `guard_enabled` | boolean (default off) |
| `guard_session_cap`, `guard_weekly_cap`, `guard_scoped_cap` | `null` (that limit is not watched), or a whole number 1 to 100 (defaults 95, 90 and `null`) |
| `guard_resume_stagger_s` | whole number of seconds, 0 to 600 (default 120): the gap between waking one waiting session and the next |

The alert rules repeat `Settings`' own (a test keeps the two identical), so a bad value is refused
before anything is applied; a value `Settings` still refuses is a `400` too. There is no
`notifications_enabled` key: `alerts_enabled` and the three `alert_on_*` kinds replace it.

- **Theme completion.** `theme_id` alone stores the new theme's default variant with it (the stored
  variant belonged to the old theme); posting the current theme again keeps its variant. A whole
  Gates id such as `blue-light` is split into the accent (`blue`) and the mode (`light`) that
  `themes.resolve` reads; a `theme_mode` in the same request wins over the one in the id.
- **`launch_on_startup`** writes the HKCU Run entry. It reads as on only while Windows has not
  turned the entry off: Settings > Apps > Startup and Task Manager leave the Run value in place and
  record the choice under `Explorer\StartupApproved\Run` (an odd first byte means off), and
  `startup.is_enabled()` honours that. Switching it on removes such a record, so the switch can
  re-enable what was turned off in Windows; switching it off, and the launch-time repair, leave
  the record alone. A dev server (see Dev isolation) keeps the choice in memory instead and logs
  that the entry was left alone.
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

**The update's record (#58).** One update runs per process (`update_check._update_lock`), so one record,
`update_check.PROGRESS` (an `UpdateProgress`, thread-safe, on an injectable monotonic clock), says how it goes.
`apply_update`'s worker starts it (`begin`) only once it holds the lock, so a second request, which the lock ignores,
never resets the running one; its version is the asset's tag without the `v`, as the check reports it. `_download` reports
the bytes after the headers and after every block it writes (`on_progress(done, announced)`, a callback that raises is
logged and the download goes on); blocks are read with `read1`, so a slow line reports what has arrived, not only every
64 KB. `_stage_update` moves it to `checking` when the file is in (the SHA-256 was worked out as it arrived, so this is
one comparison) and to `starting` once it checked out, and `_apply` to `restarting` before the app's teardown. The record
only watches: the size cap, the Content-Length test, the SHA-256 compare before the helper starts and the HTTPS-only
redirects are `update_check.py`'s (its header). Calling `_download` or `_stage_update` directly writes nothing to it.
`update_check.status()` returns `PROGRESS.snapshot()`, and `GET /api/update/status`
(`ApiServer(update_status=update_check.status)`; `501` without one, `500` with no details when it raises) answers it,
rebuilt by `server.update_status_view` from the known fields only:

| Key | Meaning |
|---|---|
| `phase` | `idle` (no run since the monitor started), `downloading`, `checking`, `starting`, `restarting` or `failed` (`update_check.PHASES`); anything else answers the idle record |
| `version` | the release being installed, a plain version or `null` |
| `done`, `total` | bytes written so far, and the Content-Length (`null` when the server did not send one) |
| `age_s`, `idle_s` | seconds since the run began and since its last change, new bytes or a new phase (0 with no run), to a tenth |
| `stalled` | downloading, bytes still missing, and no new bytes for `update_check.STALL_S` (15) seconds (from the start of the run until the first arrive; the headers alone do not count); the socket timeout (`DOWNLOAD_TIMEOUT_S`, 60) then fails it |
| `reason` | a failure's code (`update_check.REASONS`, from `failure_code`): `timeout`, `network` (the connection: a URLError that is no HTTP answer or timeout, a ConnectionError, TLS, a name lookup, a socket error naming the network), `http` (with `http_status`), `redirect` (a refused redirect to any address that is not https: plain http, file:, data: and the rest), `too_large`, `cut_short`, `digest` (the file did not match), `rejected` (not this project's link), `helper`, `local` (any other OSError: a full disk, a file held by a scanner, the temp folder, the helper file) or `error` |
| `failed_in` | the phase it failed in, so the page marks that step |
| `http_status` | the HTTP status of an `http` or `redirect` failure, else `null` |
| `limit_mb`, `timeout_s` | the size cap and the socket timeout, for the page's words |

The record holds codes, never words: the page words them. A failed run stays recorded until the next run starts (or the
monitor restarts). The record is this copy of `update_check.py`'s own; the other tools' copies do not have it.

The monitor looks for a release at start and every six hours (`app.UPDATE_RECHECK_S`, one UI-host timer that is always armed
and gated on `check_updates` when it fires; a check that fails is a debug line, nothing more, and the next one tries again). A
release that was found, is not skipped, is newer than the setting `notified_version` and arrives while `check_updates` is on is
announced once with a tray toast, "Claude Usage Monitor vX.Y is available" (`toast.update.*`, in the app's language; a
click opens the dashboard), by
`MonitorApp._announce_update` on the UI thread. The toast is held back while nobody would see it (session locked, a screensaver, a
fullscreen app in front, the user's quiet hours, `app.in_quiet_hours`), and the release stays untold, so the next state, an
unlock or the next check tries again. A shell that refuses the toast is retried too; a bug showing it is logged once and counts
as told. The usage-alert switches (`alerts_enabled`, `alert_on_*`) do not apply to this toast; `check_updates` and the quiet hours
do. `notified_version` is only ever raised (under a lock) and is an unknown key to the settings API, so the page can neither read
nor write it. The same release is offered in the tray menu (*Update to vX*, unless skipped) for as long as it is on offer.

To replace the files, Setup ends the monitor's MCP tool in open Claude Code sessions and their waits on the usage guard
(installer.iss counts them: `EndedTools`, `EndedWaits`). Run by hand, it says so on its last page (`EndedNote`). After a
silent update, which is how the app updates itself, nobody saw Setup, so it leaves the counts in `update-note.txt` in the
app data folder (`app.UPDATE_NOTE_FILE`, `WriteUpdateNote`): `{"tools": 1, "waits": 0}`, one ASCII line, written only
when a count is above 0. Counts, not sentences, since #97: `SaveStringToFile` writes the system's ANSI code page, which
would garble a translated sentence, and the monitor words them in its own language (`app.update_note_text`:
`toast.updated.tools`, then `toast.updated.waits`, a space between, as `EndedNote` joins them; cut at
`app.UPDATE_NOTE_MAX`, 250). A note that does not start with `{` is an older Setup's English sentences, said as they
are on one line; one that starts with `{` and is not the counts (whole numbers from 0 to 9999) says nothing and goes,
logged without its text. `MonitorApp._announce_update_note` shows it once as a toast, "Claude Usage Monitor updated",
at the first state or unlock, held back like the update toast and kept when the shell refuses it, and then deletes the
file. `EndedNote`'s four forms are `installer-messages/en.isl`'s `EndedToolsOne`, `EndedToolsOther`, `EndedWaitsOne`
and `EndedWaitsOther` since #103 (the count as `%1`), the same English as `en.json`'s `toast.updated.*`
(`test_update_check.py::test_the_finish_page_and_the_toast_say_the_same_english`) and the same words in each language
that has both (`test_installer_words.py`); the counts line stays ASCII.

## Dashboard process

`ClaudeUsageMonitor.exe --dashboard --port <n>` (source: `pythonw main.py --dashboard …`).
pywebview window titled "Claude Usage Monitor", native frame. After it's shown, the caption,
caption text and border colours are set from the theme via `DwmSetWindowAttribute`
(attributes 35/36/34, dark mode 20) on `window.native.Handle`, and again whenever the page
calls `pywebview.api.set_chrome(...)` after a theme change. Bounds persist in settings
(`dashboard_bounds`). The window opens `ApiServer.url`, `http://127.0.0.1:<port>/?k=<token>`: see
"Local API" for why the page needs the key. When the page cannot be shown the same address opens in the
default browser instead, and the exit code says so (below).

- **Job object.** The launcher puts the child in a kill-on-close job object that only the monitor
  holds, so the dashboard ends with the monitor however the monitor ends (End task, `taskkill /F`,
  a crash) instead of leaving a window that polls a dead API. `DashboardLauncher.close()` closes
  the job too, which also ends WebView2 helpers the child left behind.
- **Exit codes** of `dashboard_main`: `0` a window that was shown and closed, `1` our own window call
  failed (a bug; the page is opened in the browser), `2` no token or port, `3` pywebview or WebView2
  is missing or would not start (the page is opened in the browser).
- **No usable WebView2.** pywebview raises nothing for either failure, so `dashboard_main` does not wait for an
  exception. Before it makes a window it asks `webview2_runtime_available()`: the runtime must be registered under
  `Software\Microsoft\EdgeUpdate\Clients\{...}` (`pv` of 86.0.622.0 or later, per user or per PC, Evergreen or a
  preview channel; the installer's `WebView2Installed` and pywebview's own check use the same keys) or named in
  pywebview's `WEBVIEW2_RUNTIME_PATH`. Without it pywebview would quietly pick its IE11 engine, which has no
  `fetch()` and no CSS variables, and the window would stay blank; instead the browser opens and the exit code is `3`.
  A registry that cannot be read leaves the decision to pywebview. Then `webview.start(guard)` runs `_window_guard`
  beside the GUI loop: it falls back the same way (log line, browser, `window.destroy()`, exit code `3`) when
  `webview.renderer` is not `edgechromium`, or when the window's `loaded` event has not fired `LOAD_TIMEOUT_S` (20 s,
  against 2 to 3 s when healthy) after the loop started, which is what a runtime that is installed but cannot start
  looks like. A window the user closes first, or a loop that has ended, ends the guard without a fallback. The child's
  log (`dashboard.log`) also carries pywebview's own logger (`log.mirror_logger("pywebview")` in `main.py`), so the
  reason is on record. The release smoke test requires the page to have asked for `/api/themes`, which only a page whose
  script ran does, so a build whose dashboard does not start fails it.
- **The launcher and a window that is closing.** After the user closes the window the process lingers for several
  seconds while WebView2 shuts down. The child's `closed` handler therefore posts `POST /api/dashboard/closed
  {"pid": <its pid>}` after saving its bounds (with or without bounds), `DashboardLauncher.window_closed(pid)` marks
  that child as closing, and the next `open()` starts a new child at once instead of answering for the old one, which is
  left to finish and is still ended by `close()` and by the job object. A notice from a child that was replaced already,
  or from a stranger, changes nothing. As a safety net a child that is alive, has shown no window and is older than
  `START_GRACE_S` (30 s, longer than the guard's wait so a child about to fall back by itself is left alone) is
  terminated and replaced; younger than that, "the window is not visible yet" still means the child is starting.
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
  `save_export(fmt)` (`dashboard.BRIDGE_FUNCTIONS`). They are registered one by one with `window.expose()`
  (`expose_api`); the object is **not** passed as `js_api`. pywebview resolves a `js_api` call with plain `getattr`
  over a dotted name and checks no leading underscore (only the list it advertises to the page leaves those out), and
  the api object holds the window, whose `gui` is a module that imports `os`: a script in the page could call
  `_window.gui.os.system`. With `expose()` only the four names resolve. A test sends that name through pywebview's own
  `js_bridge_call` and asserts that nothing runs.
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
- **The window's language (#88, #98).** `dashboard_main` sets `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` before pywebview
  starts, from the `/api/state` it reads first: `--lang=<locale.format>` (English `en-US`) beside pywebview's own
  `--disable-features=ElasticOverscroll` (`webview2_arguments`). pywebview's other default switch,
  `--allow-file-access-from-files` (its `ALLOW_FILE_URLS` is True by default), is not repeated, on purpose: the page is
  `http://127.0.0.1`, never a `file://` page, so it would only loosen `file://` isolation the window does not need (a test
  holds both defaults). A developer's own value is kept: its `--lang` wins, and
  its `--disable-features` lists become one switch with ElasticOverscroll in it (two such switches do not add up). A
  format that is not a language tag leaves the variable as it is, with a warning. Only the child's environment changes.
  WebView2 takes its language when it starts, and for now a language change does not reopen the window: the page
  reloads into the new language at once, and the time and number fields follow from the next time the dashboard
  opens. Reopening the window on a language change, with its opening and a keep-or-revert prompt, is #115's (#98's
  owner question 2); it relies on this `--lang`.
- **`find_window_of(pid)`** (formerly the private `_find_window_of`, kept as an alias) finds the
  visible dashboard window a process owns; the monitor closes the dashboard through it, by owner and
  not by title, because an installed copy beside a dev build can have a window of the same title.

## webui

- `index.html` shell + `app.js` (API client with token, polling `/api/state` every 2 s while
  visible with ETag, hash router: `#overview #limits #cost #efficiency #history #settings #about`) +
  `charts.js` (dependency-free SVG: sparkline, bars, stacked bar, ring, heatmap). The shell loads, in this order and all
  from `/static/`: `motion.css`, the three `themes/<id>.motion.css` sheets (each scoped by `html[data-theme]`, so all three can
  load whatever the theme), `formats.js` and `motion.js` in the `<head>` (the formats first, so every script after them
  can use them; a boot curtain can start before the body is parsed), then the catalog, `charts.js` and `app.js` last:
  `app.js` starts the page. A test pins the order.
- **Efficiency.** The page that was called *Nerds only* is `#efficiency`. `PAGE_ALIASES = { nerds: "efficiency" }` opens an old
  link or bookmark and rewrites the address with `history.replaceState`. Its saved window choice is `efficiencyWindow`.
  The old `nerdsWindow` lived in the page's own storage, which never outlived the window (#54), so nothing carries it
  over. Its Context card says "prompt tokens per request, API count" and
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
  holds the reports back, lets them land and fails if any card changed height (`test_server.py`, `USAGE_MONITOR_UI_TESTS=1`). The Overview's
  cost, efficiency and model cards draw from the cost report like the Cost page does: while it is on its way they are pending,
  and when it fails they say so (`data-state="error"` and the page's `problem` banner) instead of staying pending for good.
  A PC with no Claude Code activity is the exception: nothing is asked for there (below).
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
  about, so it never shows against a newer one. The line under the Settings buttons follows the same rule (`updateLine`), and a
  check's words are tied there through `S.update.offered`, which keeps them out of the banner. Without `--update-demo` the dev
  server fakes no update (its `info` callback returns nothing): to see the banner, answer `/api/state` with an `update` block
  (a Playwright route does it), or start it with `--update-demo` (Dev isolation), which offers a made-up release. The banner
  keeps its one line after *Update now*; Settings shows the run (below; the banner's turn is #114).
  Its words, the System card's update row, buttons and line, and what each click says are the catalog's since #97
  (`banner.update.*`, `settings.update.*`; "Languages"), stored in natural case: Nostromo sets the banner's heading,
  sentence, buttons and line, Settings' buttons, the row's title and the line under them in capitals, Night City all of
  those but the banner's sentence, and Gates shows them as stored, which is what `gates.js` made of the capitals the page
  wrote before. A refusal is worded by the HTTP status of the monitor's
  answer, never by the English `error` it carries (`updateRefusal`): on these routes 501 is a step this monitor does not
  have, 500 a step that failed (`error`) or, when the error is exactly "internal error", a handler that raised
  (`internal`), 401 the dashboard's token (`token`), any other status `http` with its number, and no answer `lost`. The
  English reads as before (`tests/fixtures/update_lines_english.json`, recorded from the code before the move). Error
  codes in the server's replies are #102's: it would change `updateRefusal` alone. `failedText` stays for the refresh and
  resume lines (#100).
- **A running update on Settings (#58).** The System card has `#set-update-progress` (`.nm-uprog`) between its buttons and
  its line, filled from `GET /api/update/status` (the record, "Updates"): three step chips (Download, Check, Restart: done,
  under way, still to come, stalled or failed, with the guard card's tones; a step still to come is dimmed as the theme dims
  a disabled control), the limit cards' 32-cell bar, the sizes ("4.2 of 21.0 MB": 1 MB is 1,048,576 bytes, the total rounded
  and the done floored to a tenth, and written a tenth lower where it would read the total's figure before the last byte; a
  line high when there is nothing to say, so a failure before the first byte does not shrink the block) and a status line.
  An unknown size is a pending bar (`data-pending`, so each theme draws its placeholder bar, which holds still while the
  download stalls), "4.2 MB so far" under it, and before the first byte "Connecting to GitHub…". The page asks every `UPDATE_POLL_MS` (500 ms) while a run shows, every `UPDATE_IDLE_POLL_MS`
  (3 s, from `tick()`) while Settings is open with a release on offer (the tray can start a run), never while the window is
  hidden, another page is open or nothing is on offer, and not again after a 404 or a 501 (an older monitor, the dev server
  without `--update-demo`). A run shows only for the release on offer (a failure whose link named no version too). For
  `UPDATE_CLICK_GRACE_MS` (3 s) after *Install update* answered `installer`, a record with no run yet (none, idle, or a
  failure older than the click) shows the run's start; a running record shows as it is. Three asks in a row with no answer
  (`UPDATE_LOST_AFTER`) read as a lost link while the update downloads or is checked, and as the monitor closing once it
  had begun to (`starting`, `restarting`; no version is named, since a helper that dies brings the old one back), after
  which nothing more is asked; with no run, nothing changes (the header already says the link is lost); the first answer
  after shows the run again. A failure shows until the next click on an update button (owner question 4:
  `updateFailureLasts`; each click gives `S.update` a new answer, which then shows) or a new run, and only while its release
  is on offer (one that names no version is the release's that was on offer when it showed). Which failures show at all is
  `updateFailureNew`: the first one since a run was seen or started here, and one whose run began more than
  `UPDATE_RUN_APART_MS` (1 s) after the shown one's (each record's start is the answer's time less its `age_s`), so a click
  that ended a failure is not undone by the next answer of the same run, while a later run's failure (the tray started it)
  shows. While the block shows, the line under the buttons is hidden when it is the run's own (an answer given after the run
  began shows under the block), and *Install update* is `aria-disabled` while a run goes: `settingsClick` sends nothing for
  it. Clicked over a failure that shows, *Install update* (`installUpdateFromSettings`) keeps the block, as the new run's
  start, while the monitor answers (at most `UPDATE_CLICK_GRACE_MS`): the card does not drop to the one line and grow again;
  any answer but `installer` shows in its place. The Check step is shown done as soon as the record has passed it
  (owner question 1: `UPDATE_CHECK_HOLD_MS` is 0). The block is drawn once and patched part by part (`data-sig` on the
  chips, the cells, with the part of the next cell, the sizes and the line), so an answer every half second rebuilds only
  what moved. It holds no `.nm-big`, `.nm-stat-v` or `.nm-cmp-n`, which Night City glitches on every change. Each failure
  code has its own words (`UPDATE_FAILED`, a table of functions, so a text is the catalog's when it is drawn).
  `installUpdate` has one line for it (`watchUpdateRun()`, after the installer's line): #97, which moved the update rows'
  English to the catalog after this, kept it, the hooks in `syncUpdate` and `settingsClick` (its `install-update` branch
  calls `installUpdateFromSettings`, which calls `installUpdate`), #58's functions byte for byte and
  `settings.update.progress.*`.
  The Starbase (#66, #67) and Overseer (#80) sheets will need `.nm-uprog` rules: the column and its wrapping chips, the
  steps told apart by `[data-step]` with next at the theme's disabled opacity, `.nm-uprog .nm-story:empty { min-height:
  1lh; }`, a stalled and a failed bar, a failed step that differs from a stalled one by more than its fill where the warn
  and alert tones are close, and, if their placeholder bar moves, a rule in their motion sheet that holds it still under
  `.nm-uprog.is-warn`. `tests/browser/update_probe.js` (Playwright,
  not part of pytest) checks it in a real browser in every look, with and without reduced motion, and shoots every state;
  its step 5 (#97, `--said-only` runs it alone) clicks each update button with every answer and reads the line, the six
  buttons, the row and the banner's heading as shown (English's capitals in Nostromo, Night City and Edgerun, as stored
  in Gates), and at the narrow width in French and the pseudo-locale finds nothing wider than its card.
- **Usage guard card (#27).** The Settings card's two blocks, the live status (`#guard-live`) and the one-click setup
  (`#guard-setup`), are filled by `syncGuard` from `/api/guard`, asked once at a time every `GUARD_POLL_MS` while Settings is
  on screen; a block is rewritten only when its markup changed (`data-sig`), so a poll never drops the focus. The status is
  an OFF, ARMED or HOLDING chip with one line of story, which adds that sessions may not be told while setup reports the
  hooks missing or partly installed. While holding, the told rows count conversations and agents apart, each session's
  row says who of it heard, and a NOT TOLD row counts the conversations only their agents heard (#41; an entry without
  `main` counts as its conversation). Armed or holding, a WATCHING block lists the conversations the Stop hook watches,
  with since when and their background work (#41). Install, Remove and Resume now are `disabled` only where they cannot work (no setup
  here, nothing waiting; Remove needs `can_remove`, not Claude Code) and only `aria-disabled` while a click is on its way,
  so the focus stays. A blank cap is "not watched"; text a number box cannot hold (`validity.badInput`) is refused under the
  field instead of read as blank.
- **Sign-in help (#28).** For the two statuses only the user can fix, `no_credentials` and `stale`, the status banner grows into
  a help panel (`.nm-msg.nm-help[role=region]`, built by `signInHelp`). With no sign-in it says why the monitor needs the
  sign-in Claude Code saves (even for someone who uses Claude on the web or in the app), gives four steps and links the
  README section on using Claude on the web or in the app. The second step shows the install command
  (`irm https://claude.ai/install.ps1 | iex`) and the third the command that starts Claude Code from the installer's folder
  (`& "$env:USERPROFILE\.local\bin\claude.exe"`; the installer leaves the PATH alone, so `claude` is not found after it, #42),
  each in `.nm-cmd[data-command]` with its own *Copy* button; the commands are text (`COMMANDS`), never linked or fetched.
  The second step also says to carry on past the installer's note about the PATH.
  An expired or rejected sign-in keeps the heading and the monitor's reason and adds what to do, with the start command for a
  PC where `claude` is not recognized or opens an older Claude app that took the name. *Check again* posts
  `/api/refresh`, one at a time, and keeps "Checking…" on the button (`aria-disabled`, so the focus stays) until the state's
  `updated_at` moves (the cycle it asked for is published) or 15 s pass; a state that still needs a sign-in then says so in
  the panel's `.nm-status`. *Copy* writes its own command with `navigator.clipboard`, or selects it and says "Press Ctrl+C"
  where the page cannot; only that button answers. The check's state is `S.check`, the button's answer `S.copy` and which
  button gives it `S.copyOf`.
- **No Claude Code activity (#28).** Someone who uses Claude on the web or in the app has limits but no transcripts. Once an
  ingest has finished (`ingest.running` false, `ingest.last_duration_s` set, no `last_error`) with no request in the all-time window
  (`windows.all.totals.requests` is 0), `limitsOnly()` decides that this PC has none. It is never decided before a state
  arrives or while the first ingest runs (the windows read zero then), it holds through the ingests that follow (`S.noActivity`)
  and it ends when a request is counted. Cost & value, Efficiency and History are then one wide card that explains it, and
  the Overview's cost, efficiency and model cards keep their frame and title with one `.nm-empty` line (`data-state="ready"`,
  no `data-needs`). No cost, efficiency or history report is asked for; the limits, the pace and the alerts are as before.
- **Ids, not words (#90, part 3).** `app.js` decides nothing from English text on the page; `gates.js`'s re-casing still
  reads the English words until #101. Every card carries `data-card="<key>"`: `card({key, ...})` writes it, every card
  written out by hand (the Overview's, the Settings sections, Supporters) sets it itself, and a page part's `tagCard` sets
  its part key on the card it builds (`cost`, `value`, `by-model` ...). `gates.css` picks each card's icon by
  `[data-card="..."]`, never by `aria-label` (which keeps the title), and `tests/test_webui.py` pins every key's icon.
  A chip carries `data-tone` (`ok`, `warn`, `alert`, `acc` or `neutral`) from the status code it shows: the pace chip, the
  session and weekly verdicts (`SESSION_TONE`, `WEEKLY_TONE`), the burn velocity (`VELOCITY_TONE`) and the guard's OFF, ARMED and
  HOLDING. `gates.js` copies it to `data-g-tone`, and a value it does not know is `neutral`. `matchScoped` finds a scoped limit's
  pacing row by `kind`, `scope_model` and `scope_surface` (the dashboard's entries carry them, `state.json` does not), so two
  limits that share a label, or have no model, keep their own rows. The link's words are tables of catalog keys by status
  code (`LINK_WORDS`, and Gates' `GATES_LINK_WORDS`, #98; `STATUS` keeps only whether the link is up) and the age is built
  from `fmtAge` (`{n, unit}` for `ageWords`, with `AGE_UNIT` and `AGE_UNIT_IN_SENTENCE`), so no word is cut or
  lower-cased; the pipeline's LINK row shows the header's word in capitals until #99. Night City
  pairs a card across two renders by its `data-card`. The English on the page is unchanged; the one visible change is that the
  Settings "Data and plan" card now has its Gates icon.
- **Forecast.** A limit's forecast runs from now at the average rate so far (used / elapsed, `averageRateForecast`) to where
  it crosses 100 % (the end is marked "100% ~ <time>", the same moment as the card's "Limit reached") or to the reset when it
  stays below; the end label and the "Projected end" fact read the same value, so they cannot disagree. Scoped limits use the
  same function.
- Fonts are bundled under `webui/fonts/` (OFL) — no network requests at runtime.
- **Text is measured, not guessed.** `charts.js` asks the page how wide a run of text is (a probe `<text>`
  element; answers are cached and `MonitorCharts.refresh()` clears them when the font or size changes), so a bar
  chart's label column and the value text beside its bar fit the real font of any theme. A label that does not
  fit is cut in the middle with an ellipsis (`fitLabel`), so folders that differ at the end still differ.
- **Test hook.** With `window.__USAGE_MONITOR_TEST__` set before the script runs, `app.js` hands its helpers back
  (`__USAGE_MONITOR_TEST__.api`: `esc`, `fmtPct`, `paceOf`, `ratioCells`, `remainHTML`, `sessionsTable`, ...) and returns
  without starting the page. Nothing else sets it. `tests/webui_checks.js` loads `formats.js`, `charts.js` and `app.js`
  in a node `vm` context with a fake DOM and prints what they do as JSON; `tests/test_webui.py` asserts on it. Those
  tests start node, so they need `USAGE_MONITOR_UI_TESTS=1`, and `tests.yml` runs them (and `tests/test_motion.py` and
  `tests/test_english_left.py`) once more with it set. `tests/webui_formats.js` runs every formatter of the page
  (numbers, money, durations, clocks, dates, lists, the odometer, the rolling figures and the sentences and charts that
  hold a number) on a fixed grid with TZ=UTC and the page's clock still, `motion.js` behind `formats.js` with English's
  locale as on the page, and `test_webui.py` holds it to `tests/fixtures/webui_formats_english.json`, recorded from the
  page before #91 changed it (three amounts that round up to $1000.00 were added in #91's review, recorded from the
  same page): English never changes by a byte.
  `tests/webui_cldr.js` runs `formats.js` alone in every format tag of French, Spanish and Turkish over the grid
  `test_webui.py` gives it, with TZ=UTC, and prints node's ICU and CLDR versions with the result (V2, in Formats above).
- **Money.** Real money (the extra-usage Spend card) is shown in the account's own currency (`fmtMoney`, an
  `Intl.NumberFormat` with the ISO code the usage API reports; USD where it reports none), through `formats.js`: US
  dollars are the plain `$` in French, Spanish and Turkish, and another currency is written with its code there
  (`5,96 CAD`), as the alerts write it. API-equivalent cost stays dollars. `/api/state.plan` carries the detected plan and price only: `plan_price_override` is a setting,
  applied where a price is used (the reports, and `effectivePlanPrice()` in the page), so setting or clearing it
  never rewrites state. The hint beside the override names the detected plan and price, and `syncHints` keeps it
  to the latest state: loaded on `#settings` the page is drawn before the first state, and a first sign-in or a new
  plan changes the plan under a page that is open. It writes only when the words change.
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
| `webui/motion.js` | The engine, `window.USAGE_MONITOR_FX`: one `requestAnimationFrame` loop for every character-level job (`typer`, `decoder`, `roller`), ghost text, the pending and arrival watcher, `FX.director(cfg)`, the boot curtain and the reduced-motion listener |
| `webui/motion.css` | What every theme shares: the ghost text, the typing cursor, `@property --t`, `.fx-land`, the boot curtain and the reduced-motion guard. It holds no colour and no timing |
| `webui/themes/<id>.motion.css` | The theme's look: entrance, hover, placeholders, arrival and boot. Every rule is scoped by `html[data-theme="<id>"]`, colours come only from the theme's tokens |
| `webui/themes/<id>.js` | The theme's director: registers `window.USAGE_MONITOR_THEME_FX[id] = { start, enter, cancel, arrive }` and plays the boot |

**Hooks (`app.js` calls them; the header comment of `app.js` is the contract).** `start()` runs once the theme's script has
loaded and again whenever the theme is applied. `enter(main, page)`: the page just drawn into `#main` is due its entrance (a
tab, a link, back or forward, the dashboard starting, a theme change; not a refresh, a variant change or a card filled in
place). It is called once the theme's stylesheet and script are in and the window is shown, and never waits for a report.
`cancel(main)`: finish what plays, at the final state, before another page or theme replaces the page and when `reduce_motion`
turns on (not when a card is filled in place). `arrive(card, info)`: a card's numbers arrived or changed in place; `info` is
`{ page, card, first, from, to, fresh, entering, reduced }` (`from` and `to` are the card's `data-state` before and after).
For code that is not a theme script, `mon:card-data` bubbles from the card with the same `info` and `mon:page-rendered` fires
on `#main` with `{ page, opened, reduced, updated, added }` (the card keys). With `reduce_motion` or `prefers-reduced-motion`
`enter` and `arrive` are never called (`cancel` still is, since it only finishes what was playing), and the files add no
animation of their own.

**Classes and marks.** On `#main`: `fx-run` (the theme's entrance is playing; the theme sets and clears it, and every entrance
rule hangs on it and fills `backwards`, so removing it leaves exactly the markup `app.js` wrote) and `fx-quiet` (the page was
just redrawn and the pointer has not moved since: one-shot hover animations wait, and a hover transition is taken away, never
the hover look itself; `app.js` and the engine both set it and both clear it on the first pointer or wheel movement). On a
slot: `data-pending` (read by the engine, written by `app.js`; a running update whose size is not known yet is a pending bar
too, #58, so each theme draws its placeholder bar there and its arrival when the size comes, and nothing else of the update's
block moves; Gates' and Night City's motion sheets hold that placeholder still under `.nm-uprog.is-warn`, while the
download stalls), `data-fx-pend="num | text | bar | rail | chart"` (written by the engine for the themes' CSS), `.fx-land` (a bar, rail or chart whose data just arrived plays its fill from its own start, with
no page stagger), and `data-fx` / `data-fx-d` (a figure whose characters the script draws: the ghost text). The orders the CSS
reads are custom properties the engine writes (`--fx-i` a card's order, clamped to 8 so a long page ends inside its budget,
`--fx-k` a cell's order in its bar, `--fx-c`, `--fx-p`, `--fx-d`, `--fx-r` for chart columns, pips, dots and rows, `--fx-cn` a
heat map's column count (one write on the map's svg, from the `data-cols` attribute `charts.js` puts there; no write on its cells),
`--gk-d` and `--gk-l` Gates' bar timing).

**Hover, once per entry.** A hover effect moves what the pointer is on (Night City's glitch jitters the card 2 px and its tear
clips slits out of it, Nostromo's settle moves it 1 px, Gates lifts a card 2 px and nudges a menu link), so one bound to `:hover`
alone cuts its own ground away: a pointer resting at the edge of a card, or on a slit, is off the card as soon as it moves,
`:hover` goes, the card comes back, `:hover` returns and the effect starts again, for as long as the pointer rests there (Night
City's glitch played about 30 times a second on a fill bar by the edge of the "Cache and thinking" card). So the effects hang on
two attributes that `motion.js` sets from the pointer's entries, and the animated ones never on `:hover`:

- `data-fx-play` (the one-shots: Night City's jitter, tear, fringe and nav, Nostromo's settle) is set on a target's `pointerenter`
  and taken off 320 ms later (`PLAY_MS`; the longest one-shot is 200 ms). The target is armed again 400 ms after the pointer
  really left it (`REARM_MS`), so one entry plays once, whatever the effect does to the target's own hit area while it plays.
  It is not set while the page is entering (`fx-run`) or was redrawn under a resting pointer (`fx-quiet`), under reduced motion,
  in a hidden page, or for a touch.
- `data-fx-hot` (the persistent looks: Gates' lift, Nostromo's brightness, glow and menu nudge) is set on `pointerenter` and kept
  while the target moves under a pointer that has not: a `pointerleave` within 4 px of where the target is now (`HOLD_SLOP`) is
  the target moving, not the pointer leaving. It is cleared when the pointer is really away (a later `pointermove` finds it more
  than 4 px off, or the leave itself was far), when the pointer leaves the window, when the window loses focus and when the page
  is hidden. The rules read it besides `:hover`, which still answers at once and puts the look back on a card a refresh redrew.

The targets are `.nm-card`, `.nm-stat`, `.nm-menu a`, `.nm-refresh`, `.nm-sw button` and `.nm-btn` (`HOVER_TARGETS`). The
listeners are capturing ones on the `document`, because `pointerenter` does not bubble, which is the point: a bar coming under the
pointer inside a card is not a new entry of the card (a `mouseover` would be). A theme that adds a hover effect hangs it on one
of the two attributes and adds any new kind of target to `HOVER_TARGETS`; `tests/test_motion.py` fails a `:hover` rule that
animates, moves or clips its target on its own, and a rule that names a target `HOVER_TARGETS` lacks. `app.js` keeps both
attributes (`patchShell` leaves every `data-fx*` attribute alone) when it patches a card in place. The hover marks do not change
what the native widgets do: their pointer response is a handful of prepared frames played on a timer, and every frame covers (by
one 255th, invisibly) whatever the normal frame covered, so a frame never lets the pointer through and cannot end its own hover
(see `ui/widgets.py`).

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
(`fx-quiet` only removes its transition); a one-shot hover animation never replays because of a refresh: the card that an update
redrew under a resting pointer is not entered again (the pointer is already on it), and the pointer moving afterwards is not an
entry either.

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
ones need `USAGE_MONITOR_UI_TESTS=1`, start node with `CREATE_NO_WINDOW`, and `tests.yml` runs them with `tests/test_webui.py`. The
hover rule above is pinned from both sides: the static checks fail a `:hover` rule that animates, moves or clips its target
without `[data-fx-hot]` or `[data-fx-play]`, and the node scenarios drive the hover marks through a fake pointer (one entry, a
card that moves off a resting pointer and back thirty times, the cool-down, the 4 px hold, the entrance, a quiet page, reduced
motion, a touch, the window, a blur and a hidden page). What a real browser does (entrances, boots, reduced motion,
placeholders, a failed report) is checked with Playwright against the dev server (`python -m usage_monitor.ui.devserver
--theme <id>[/<variant>]`), which the tests do not cover. The hover rule is the exception: `tests/browser/hover_probe.js`
(`NODE_PATH=<folder with node_modules/playwright> node tests/browser/hover_probe.js`, not part of pytest) starts the dev server
on a throwaway settings folder, enters the "Cache and thinking" card from every side and 1 px inside every edge in three fast
steps, holds 2 s, and then enters every kind of target on every page, checks replays (a sweep inside the card, a quick return,
a return after the cool-down) and a rest across a data refresh; every hover effect must start exactly once per entry, in all
three themes (it exits 1 and prints `VERDICT: FAIL` otherwise; `--reduced` runs the reduced-motion check the other way round:
nothing may play, and the card must still be marked hot). Run it after any change to a hover rule or to the hover code in
`motion.js`.
