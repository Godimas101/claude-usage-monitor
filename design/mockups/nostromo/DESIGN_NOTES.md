# nostromo — designer notes (theme workflow, 2026-10-21)

Winner: iconic (judge tally {'iconic': 3, 'instrument': 0}). Written by the synthesizing designer; the Pillow recipe below is the reference for render/skins/nostromo.py.

Both deliverables are written and the captures are refreshed. Final report follows.

FILES WRITTEN
- C:/Users/Chris Carpenter/VS Code Projects/tools/claude-usage-monitor/design/mockups/nostromo/final.html (one self-contained page, five surfaces)
- C:/Users/Chris Carpenter/VS Code Projects/tools/claude-usage-monitor/usage_monitor/themes/nostromo.json (token spec, validated; created the themes folder)
- Build parts and generator (scratch only, not in the repo): C:/Users/CHRISC~1/AppData/Local/Temp/claude/d--SteamLibrary-steamapps-common-SpaceEngineersModSDK/1a067791-bfa1-49a4-bd6c-bf75b40026f3/scratchpad/nostromo-build/ (gen_spec.py regenerates the JSON)

SCREENSHOTS (all under C:/Users/CHRISC~1/AppData/Local/Temp/claude/d--SteamLibrary-steamapps-common-SpaceEngineersModSDK/1a067791-bfa1-49a4-bd6c-bf75b40026f3/scratchpad/pw/shots/nostromo/)
- final-full-1x.png (full page)
- final-widget-states-1x.png (normal, fable hidden, warning, critical, stale, offline, paused)
- final-widget-zoom-3x.png (3x copy of the widget frame)
- final-taskbar-1x.png (Win11 taskbar mock, strip, tray, clock 4:12 PM / 2026-10-21)
- final-strip-states-1x.png and final-strip-zoom-3x.png
- final-tray-1x.png (16/24/32 px, four states, 8x loupe, light-taskbar row)
- final-dashboard-1x.png (1200 x 760 screen)
- final-boot-1x.png (typed boot frame)
- final-variants-1x.png (all eight variants)

CONCEPT
Phosphor Console keeps the shipped amber-on-CRT identity and sharpens it. Widgets are flat and chamfered, with 16-cell pip bars and a time rail under each bar. Status is carried by shape and word (a boxed status pill, a severity word, and a static inverted chip for critical), so no state depends on blinking or colour alone. The dashboard adds bloom, scanlines, tube curvature and a typed boot once per session; the $1,659 hero, a 16.6x plan bar with one cell per month, and explicit "USAGE x% / TIME y%" pace lines carry the cost story. Every colour is a token, the eight variants cover all eight app accents, and the tray icon carries its own dark plate so it reads on light taskbars.

VARIANTS (ids and names; all dark, default = amber)
amber "AMBER P3" (default, app "Default"); green "TERMINAL GREEN P1"; white "WHITE P4"; red "RED ALERT"; blue "BLUE BRIDGE"; cyan "CYAN"; orange "ORANGE"; purple "PURPLE". The app's THEMES dict has Default, Red, Blue, Green, Purple, Cyan, Orange, White, so all eight are covered. The accent map is in extras.accent_map.

FONTS AND LICENCES
- Michroma (display): SIL Open Font License 1.1
- IBM Plex Mono SemiBold 600 and Regular 400 (labels): SIL OFL 1.1
- VT323 (readouts, percentages, tray centre): SIL OFL 1.1
- Segoe UI Variable Text (taskbar clock only, Windows system font, not bundled)

PILLOW NOTES (widget coordinates in pixels, origin top-left; frame 280 x 126, or 280 x 94 for two rows)
1. Outer frame: polygon (10,0) (279,0) (279,H-11) (269,H-1) (0,H-1) (0,10), fill --edge. Body: same shape inset 1 px with inner chamfer 9.4 (inner = outer + sqrt2 - 2). Body fill: vertical gradient --plate at y=1 to --crt at y = 1 + 0.4 x (H-2), then --crt.
2. Top hairline: y=1, x 10 to 270, --edge-hi.
3. Scanlines: every row with y % 3 == 2 inside the body is multiplied by 0.70 (30 percent black). Widgets only; the dashboard uses 0.22.
4. Glow layer, drawn under the crisp layer: lit pip cells blurred with GaussianBlur(3) at alpha 0.22 in --glow. Values: GaussianBlur(2) at alpha 0.6 plus GaussianBlur(7) at alpha 0.22, in --glow. Alive dot: blur 2 at 0.6.
5. Header: alive dot 6 px circle at x 14 to 20, y 9 to 15. Pill: rectangle x 26 to (26+w), y 6 to 17, 1 px --rule-strong outline, text IBM Plex Mono SemiBold 10 px, tracking 1.2 px, text starts x 31. Age text (Regular 10 px, label colour) starts at pill right + 6. Severity word (SemiBold 10 px) right-aligned to x 266. Critical: static chip, rectangle behind the word in --bright with --ink text.
6. Rule: y=21, x 14 to 266, --rule.
7. Rows at y0 = 28, 60, 92. Name at x 14, cap top about y0+2, SemiBold 10 px, tracking 1.2 px, --main. Qualifier after 6 px, Regular 10 px, tracking 0.6 px, --label. Value: VT323 22 px, right edge x 266, baseline about y0+14 (verify against the 3x zoom), full-size percent sign, --bright (warn: --hot; critical: chip).
8. Time rail at y0+17, x 14 to 266: dotted 2 on / 2 off in --ghost; solid to x = 14 + round(t x 252) in --label; tick 2 px wide, y0+15 to y0+19, centred on that x, --hot. Here t is the elapsed fraction of the window.
9. Pip bar at y0+21 to y0+29 (8 px high): 16 cells, each 12 px wide, cell i at x = 14 + 16i. Usage p: n = floor(p/100 x 16) full cells, boundary cell filled from the left by round(frac x 12) px, rest --ghost. Lit cells --main (warn --bright, crit --hot).
10. Stale: all tones become --stale. Dot becomes a 1 px hollow ring. Row 3 is replaced by a dashed box, x 14 to 266, y 92 to 119, 1 px dashed --label, fill --crt. Two lines, 10 px SemiBold, line boxes at y 94 and 106.
11. Paused: pause glyph (two 2 x 6 bars at x 14 and 18) in --label in place of the dot.
12. Hazard (critical only): y 0 to 3, stripes at -45 degrees, period 8 px, 4 px --bright then 4 px --crt, clipped by the outer frame.

Bars in the taskbar strip (264 x 34, chamfer 8 and 7.4): dot at (10,14). Labels at x 22 and x 144 (IBM Plex Mono SemiBold 11 px). 5H bar cells x = 44 + 6i, 4 px wide, y 13 to 20. 7D bar cells x = 166 + 6i. Values VT323 18 px, right edges x 126 and x 256. Divider x 134, y 9 to 24, --rule-strong. Glow blur 2 and 5 px for values.

Tray (per pixel, no AA at 16 and 24): plate square S (16, 24 or 32), fill --crt, 1 px --edge border, chamfer C = 3 / 4 / 5 on top-left and bottom-right. Ring centre (S/2, S/2), pixel centres at (x+0.5, y+0.5). Outer radius R = 6.0 / 9.5 / 14.0, thickness T = 3.0 / 3.5 / 4.0. Arc from 12 o'clock clockwise to pct x 360 degrees, lit in --main (warn --bright, critical --hot), track in --tray-track. Stale: no arc, dashed track (2 on, 2 off along the mid radius) in --stale, no centre. Centre: 16 px square at pixels 7 to 8, 24 px square at pixels 10 to 13, both in the lit colour. 32 px: VT323 18 px percent, centred, --hot (stale shows dashes).

Type sizes: widget label 10 px, value VT323 22 px, pill and state 10 px, message 10 px. Strip label 11 px, value VT323 18 px. Dashboard: Michroma 26 px title, VT323 88 / 66 / 44 / 22 px.

TOKEN SPEC NOTES
- Every colour is #rrggbb or #rrggbbaa. The 16 required keys are per variant; extras.variant_tokens adds ghost, dim, ink, glow_soft, rule_strong, edge_hi, tray_track and crt_deep.
- Mapping: text = --main, text_bright = --hot, text_dim = --label, accent = --bright, track = --ghost.
- Light/dark: all variants are dark (mode "dark"). The light taskbar is handled by the tray plate and strip plate, not a light variant.
- Label-tier contrast on CRT black (approximate): amber 6.9, green 6.4, white 9.5, red 5.1, blue 7.4, cyan 7.6, orange 6.0, purple 5.3.

MUST-FIX STATUS
- Reduced-motion guard added. Captures use a mock-only freeze (data-capture is set when navigator.webdriver is true).
- Critical value is a static chip and never blinks. Only the alive dot blinks.
- Stale tray has a dashed track and no arc at 16, 24 and 32 px, matching the spec.
- Coordinate origin is x 14 everywhere (CSS and notes agree).
- Taskbar labels 11 px.
- Chart labelled "DAILY · LAST 30 DAYS" with "1 PIP = $10" and an MTD average marker.
- Time sample is one clock: Wed 2026-10-21 4:12 PM. Session reset 7:39 PM (31 percent elapsed). Weekly and FABLE reset Sat 4:00 PM (57 percent elapsed).
- Boot plays once per session (sessionStorage, try/catch). Click skips. Reduced motion shows the text at once.
- Literal colours outside the token blocks removed. Dashboard classes are namespaced nm-, mock chrome mk-, tr-, st-, var-. Amber has an explicit data-variant block.
- Line-only vector element added (brand mark, plus the burn-rate trace).
- Font licences are on the sheet footer.

OPEN ITEMS AND FLAGS
- Not checked on a real 100 percent taskbar. The light-taskbar row is a mock only.
- Baselines marked "verify" come from CSS measurement and need a check against the Pillow render.
- Weekly reset changed from the brief's Wed 4:00 PM to Sat 4:00 PM, because Wed 4:00 PM had already passed at the 4:12 PM sample clock.
- Severity thresholds (70 and 90 percent) are placeholders in extras.states.
- The codename "nostromo" is the theme id only. Product copy says "Phosphor Console", and the codename appears only in the sheet kicker.
- Settings, History and About are nav entries only. No pages were designed.
- A file I did not write, usage_monitor/themes/gates.json (24957 bytes, written 20:04), appeared in the themes folder during this task. I left it untouched.
- No git commands were run, and no other repo files were edited.
