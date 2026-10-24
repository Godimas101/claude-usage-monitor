# gates — designer notes (theme workflow, 2026-10-21)

Winner: instrument (judge tally {'iconic': 1, 'instrument': 2}). Written by the synthesizing designer; the Pillow recipe below is the reference for render/skins/gates.py.

Gates final is done: the mock, the theme JSON, the screenshots, and a Pillow check. Every must-fix is addressed except the two gaps listed under Open items. The base is instrument.html with grafts from iconic.html.

FILES WRITTEN
- C:/Users/Chris Carpenter/VS Code Projects/tools/claude-usage-monitor/design/mockups/gates/final.html
- C:/Users/Chris Carpenter/VS Code Projects/tools/claude-usage-monitor/usage_monitor/themes/gates.json (new directory `usage_monitor/themes/`; 14 variants; validated by reload)
- Scratch only, not in the repo: C:/Users/CHRISC~1/AppData/Local/Temp/claude/d--SteamLibrary-steamapps-common-SpaceEngineersModSDK/1a067791-bfa1-49a4-bd6c-bf75b40026f3/scratchpad/final/ (make_gates_json.py is the palette source of truth; pillow_proto.py; colour_check.py; search_accents.py; audit_tokens.py; pillow_out/)

SCREENSHOTS (all under C:/Users/CHRISC~1/AppData/Local/Temp/claude/d--SteamLibrary-steamapps-common-SpaceEngineersModSDK/1a067791-bfa1-49a4-bd6c-bf75b40026f3/scratchpad/pw/shots/gates/)
- final-full-1x.png
- final-widget-1x.png (normal with Fable row, and without it)
- final-states-1x.png (normal, warn, crit, stale, offline, paused)
- final-widget-zoom-3x.png
- final-taskbar-dark-2x.png, final-taskbar-light-2x.png, final-taskbar-strip-3x.png
- final-tray-dark-2x.png, final-tray-light-2x.png, final-tray-4x.png
- final-dashboard-dark-1x.png, final-dashboard-light-1x.png (1200x760)
- final-variants-dark-1x.png, final-variants-light-1x.png

CONCEPT
Each limit row is a 288 x 128 Quick Settings card: a thin time rail (share of the window already gone, with an index tick at its end) sits above a usage bar, so pace is the gap between them. Status is a tinted pill with its age ("LIVE · 12 s"). Severity colours only the bar and the value. The third slot holds the Fable row (normal state only) or the footer with the reset time, so the card is 128 px in every state. The dashboard follows the Settings shell (search, profile, icon nav with a 3 px accent indicator, rounded content layer, timer-glyph caption) with pace lanes and data-driven charts. Fixtures derive from one clock (Wed 2026-10-21, 4:12 PM). Session resets 7:39 PM, so 31 % of the 5 h window is gone. Weekly resets Sun 4:00 PM, so 43 % is gone. The warning and critical fixtures use a session reset of 6:12 PM (60 % gone).

VARIANTS (ids are "<accent>-dark" and "<accent>-light"; "system-dark" is the default)
- System accent: mock uses the Windows default blue, #60CDFF dark / #0067C0 light. The app reads the live Windows accent and falls back to these.
- Blue: #5B9DFF / #2D5BD9
- Teal: #4DD6C2 / #018574
- Purple: #B39AE6 / #6F4FA8
- Rose: #FF8AC5 / #C10E74
- Orange: #FF9F5A / #C75300
- Green: #C3E86B / #5A7F00 (shifted off the ok green)
- Light and dark are separate variants. System mode is resolved by the host from AppsUseLightTheme and picks the dark or light entry of the chosen accent. Documented in extras.mode_policy.
- Locked per mode, not per accent: ok #6CCB5F / #0B6B0B, warn and usage-warn #FCE100 / #8A5200, alert #FF8A80 / #B3261E, usage-alert #FF5F5F / #B3261E, stale #8C8C8C / #6F6F6F.

FONTS AND LICENCES
- Segoe UI Variable Display and Text (SegUIVar.ttf): Windows 11 system font, Microsoft terms. Not bundled; the app reads C:\Windows\Fonts. Named instances used: Semibold Display, Semibold Text, Regular.
- Segoe Fluent Icons (SegoeIcons.ttf): Windows 11 system font, same terms.
- Cascadia Mono (CascadiaMono.ttf, variable 200–700): SIL OFL 1.1.
- Mock-only fallbacks: Consolas (Windows system font), system-ui.

PILLOW RECIPE (widget)
- Canvas 336 x 176 px, card at (24, 24). Draw at 4x as RGBA, convert to RGBa, LANCZOS to 1x, convert back. Text origin is card origin plus (x, baseline), with baseline = line_top + (line_h − (ascent+descent)/4)/2 + ascent/4, using 4x font metrics.
- Shadow main: rounded-rect mask offset dy 8, GaussianBlur radius 32 (sigma 8 at 1x). Alpha 34 % dark / 14 % light, black.
- Shadow tight: same rect, dy 0, GaussianBlur radius 4 (sigma 1). Alpha 40 % dark / 12 % light.
- Card: rounded_rectangle r 32 at 4x. Fill #262626 @95 % (dark) or #FCFCFC @95 % (light). Outline #FFFFFF17 / #00000014, width 4.
- Top highlight, dark only: 4 px high at y 4, x 72 to 1080, #FFFFFF @16 % with a linear alpha ramp (0 at the ends, peak at centre).
- Header: E916 at 14 px, accent, centred on x 22. Title "Claude usage" at x 36, SegUIVar wght 600, opsz 12, 12 px.
- Pill: right edge x 252, height 18, radius 9. Width = 6 + 8 + 6 + text + 9. LIVE fill is ok @16 % dark / 12 % light. STALE, OFFLINE and PAUSED fill is track. Dot 8 px; LIVE adds a 2 px halo ring at ok @24 %. Word 10 px wght 600 at pill_left + 20.
- Three 3 px menu dots, centres x 261.5, 266.5, 271.5, y 21.
- Rows at y 38, 65, 92 (pitch 27). Label 12 px wght 400 at x 15, line 16. Tag 11 px Cascadia Mono 400 at label end + 6. Value 15 px Cascadia Mono 700, right edge x 273.
- Time rail: 2 px at row + 19, x 15 to 273. Track is time_track; fill to 15 + 258 × t % in time colour. Index tick 1 × 6 px at the fill end, from row + 17.
- Usage bar: 4 px at row + 23, radius 2, track colour across 258 px. Fill to 258 × u %. Colour is accent, usage_warn at ≥ 60 %, or usage_alert at ≥ 85 %. Stale uses the stale colour.
- Fable share tick: 1 × 10 px at x 144, from row + 20.
- Footer: 11 px wght 400 at x 15, baseline from line top y 98, line 16. The lead word is wght 600 in the state colour. Stale lead is "Hmm, we lost touch." in warn colour.
- Measured footer widths: stale 220.0 px, crit 133.2, warn 152.8, offline 186.2, paused 172.5, all within 258.

PILLOW RECIPE (tray ring, 16/24/32 px)
- Centre radius and stroke: 16 px gives 5.25 and 2.5; 24 px gives 8.0 and 3.4; 32 px gives 11.5 and 4.2.
- Draw at 4x in an L mask with bbox radius = (centre + stroke/2) × 4, because Pillow draws the stroke inward. That is 6.5, 9.7 and 13.6 px.
- Track: ellipse with width stroke × 4.
- Arc: arc(bbox, 270, (270 + 3.6 × pct) mod 360, width = stroke × 4). Clockwise from 12 o'clock.
- Stale track is dashed as arcs. Dash on/off in pathLength units (1 unit = 3.6°): 16 px is 6/4.5, 24 px is 4/3, 32 px is 3.2/2.4. Stale arc uses text-3.
- Downsample the mask with BOX, then colourise. Arc colour is tray-fg, or usage-warn at ≥ 60 %, usage-alert at ≥ 85 %.
- Only the 32 px size prints the number: 10 px wght 600, centred.

MEASURED (scratch Pillow at 4x)
- Centre-radius bbox: outer 5.38, inner 3.0. Rule bbox: outer 6.62, inner 4.25 against CSS 6.5 and 4.0; the 0.25 px gap is quantisation at 4x.
- Arc sweeps: 13 % = 46.8°, 72 % = 259.2°, 93 % = 334.8°.
- The 13 % arc at 1x is 12 px at full alpha. It is visible at 6x and 2x.

TAILORING AND OTHER NOTES
- Taskbar strip (248 x 40): rows at y 6 and 25. Rail 2 px at row + 1 over x 34 to 184. Bar 4 px at row + 5 using the track token, not tray-track. Label 11 px wght 600 at x 10. Value 11 px Cascadia Mono 700, right edge x 222. Status dot 8 px at (230, 16).
- No glow blur in the widgets. The only halo is the 2 px lamp ring. The dashboard nav indicator uses a 6 px glow from --glow (accent @36 % dark / 28 % light).
- Dashboard type: page title 28/34 display 600, card headings 14/20 semibold, body 12–14, numbers Cascadia Mono 12–13. No text below 11 px, no caps tracking, no ellipsis.

CHECKS
- Token audit: 52 declared tokens, none unused. No literal colours in component CSS.
- Separation (CIE76, accent vs ok, warn, alert, usage-alert, stale), lowest first: Green light 21.0, Orange light 24.7, Green dark 25.3, Orange dark 28.6, Teal light 35.0. All other pairs are above 35.
- Contrast: light warn text on taskbar 5.76 (old #b26a00 was 3.82); dark alert pill text 4.97; light stale label 5.17; stale values at 70 % alpha 8.16 dark / 6.26 light.
- Fixture changes from the originals: session reset 10:39 PM became 7:39 PM, and weekly "Wed 4:00 PM" (already past) became Sun 4:00 PM. Rails are derived from reset timestamps, not typed.

OPEN ITEMS
1. Dashed stale ring at 16 px reads as a dotted ring. At 24 and 32 px it can resemble a loading ring. The arc is now text-3 for contrast, but it stays the weakest state. Keep the solid grey fallback (judge graft) if users confuse it.
2. Not verified: whether WebView2 resolves the "Segoe UI Variable Display" and "Segoe UI Variable Text" family names, or falls back to "Segoe UI Variable". Chromium here renders Segoe letterforms. The Pillow named-instance mapping is in gates.json.
3. System and Blue are both blue by design. The mock values are the Windows default; the app will read the live accent.
