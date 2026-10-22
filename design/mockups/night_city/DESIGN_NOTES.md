# night_city — designer notes (theme workflow, 2026-10-21)

Winner: iconic (judge tally {'iconic': 2, 'instrument': 1}). Written by the synthesizing designer; the Pillow recipe below is the reference for render/skins/night_city.py.

> Superseded in part by the Night City v2 revision (see review.html and night_city.json): variants are now Blackwall (default), Relic, Edgerun, Mox; Edgerun is a yellow plate; tray style is the RAM meter (extras.tray_style).

Night City final is done: `final.html` and `night_city.json` are written, and the twelve screenshots are rendered and checked. The main design change is that the widget and strip use a solid bar with a slanted leading edge and no segment mask, and the dashboard keeps the 31-cell segmented bars.

FILES WRITTEN
C:/Users/Chris Carpenter/VS Code Projects/tools/claude-usage-monitor/design/mockups/night_city/final.html
C:/Users/Chris Carpenter/VS Code Projects/tools/claude-usage-monitor/usage_monitor/themes/night_city.json
Scratch only, not a deliverable: C:/Users/CHRISC~1/AppData/Local/Temp/claude/d--SteamLibrary-steamapps-common-SpaceEngineersModSDK/1a067791-bfa1-49a4-bd6c-bf75b40026f3/scratchpad/pw/measure.js (font-metric probe)
The themes folder already held gates.json and nostromo.json, which I left untouched. No git commands and no other repo edits.

SCREENSHOTS (C:/Users/CHRISC~1/AppData/Local/Temp/claude/d--SteamLibrary-steamapps-common-SpaceEngineersModSDK/1a067791-bfa1-49a4-bd6c-bf75b40026f3/scratchpad/pw/shots/night_city/)
final-full-1x.png, final-widget-zoom3.png, final-states-1x.png, final-hit-zoom3.png, final-stale-zoom3.png, final-taskbar-1x.png, final-taskbar-zoom2.png, final-tray-1x.png, final-tray-zoom4.png, final-dashboard-1x.png (1200 x 760), final-variants-1x.png, final-variant-cards-1x.png

CONCEPT
Night City as an in-game HUD: every frame is a chamfered polygon with a hairline edge and corner brackets. Salmon carries usage, cyan carries LIVE and the pace hairline, and signal yellow is reserved for WARN, CRIT, STALE and stamps. Hazard stripes appear only on the critical fill, the stale band and the CRIT cap. Numerals refresh with a three-frame RGB split once per data refresh, the LIVE pip breathes on a 2.4 s pulse instead of blinking, and scanlines sit at 15 percent under the content.

VARIANTS (all dark; default Street Red)
street-red: salmon primary, yellow signature and WARN, cyan time line and LIVE.
netrunner-cyan: usage 2fe6ff, time line bff6ff, LIVE mint 7dfff0.
signal-yellow: cream usage f6e9a6, WARN red-orange ff7a1a, cyan time line.
neon-pink: pink usage ff7ac3, cyan time line and LIVE, yellow WARN.
Night City has no light pair (extras.light_mode).

FONTS AND LICENCES
Chakra Petch, SIL OFL 1.1: numerals and headings. Bundle ChakraPetch-Bold.ttf and ChakraPetch-SemiBold.ttf.
Rajdhani, SIL OFL 1.1: labels, chips, nav. Bundle Rajdhani-Bold.ttf and Rajdhani-SemiBold.ttf.
Share Tech Mono, SIL OFL 1.1: values, reset times, data noise. Bundle ShareTechMono-Regular.ttf.
Segoe UI Variable: Microsoft, Windows 11 system font. Taskbar mock only, not bundled.
Not used: franchise or studio marks, game art. The tray octagon is a generic placeholder.

PILLOW RECIPE: WIDGET (264 x 96 two-row; 264 x 126 with the FABLE row; origin top-left)
1. Frame: polygon (8,0),(263,0),(263,H-9),(255,H-1),(0,H-1),(0,8), 1 px, #ff6a4f at alpha 0.6. Panel: same polygon inset 1 px, flat #160a0d. Scanlines: rows where y mod 3 is 0 darkened by 15 percent, drawn before text.
2. Brackets: accent #3ef0ff, 2 px. TR: x251-259 at y4-5, plus x258-259 at y4-12. BL: x4-12 at y(H-6)-(H-5), plus x4-5 at y(H-13)-(H-5).
3. Header: pip 6x6 at (14,11), accent, pulsed. Brand CLAUDE//USAGE, Rajdhani Bold 12, tracking 1.92 px, #ffd9cd, x26, baseline 17.5. LIVE, Rajdhani Bold 11, tracking 1.32 px, #3ef0ff, right edge x250, baseline 17.
4. Rows at top y 28, 58, 88; content x14-249 (236 px). For each row top y0:
   Label: Rajdhani Bold 13, tracking 1.56 px, #ff9a82, baseline y0+11. Any chip sits 6 px after the name.
   Value: Share Tech Mono 15, #ffd9cd, right edge x249, baseline y0+11.5. Percent sign: Share Tech Mono 11, #e8957f, 1 px gap, baseline y0+10.5.
   Reset: Share Tech Mono 11, #e8957f, baseline y0+10.5, right edge 8 px left of the value.
   Time line: y0+15 to y0+16, x14-249. Accent from x14 to x=14+2.36t, track #4a2a2f after. Tick 2 px wide at x=14+2.36t-1, y0+13 to y0+18.
   Bar: y0+19 to y0+26, track #4a2a2f across x14-249. Fill polygon (14,y0+19), (14+2.36p,y0+19), (14+2.36p-4,y0+26), (14,y0+26), colour #ff9a82. End tick 1 px at x249, y0+17 to y0+28, #a27664.
5. Warn (used at 70 percent or more): fill and value #ffe600, outline WARN chip (1 px #ffe600, 5 px top-left chamfer).
   Crit (90 percent or more): fill is the hazard pattern, value #ff2e3d with glow alpha 0.35, CRIT chip. The chip is a #0a0506 box 14 px tall with a 7 px hazard cap at the left, text #ffe600 Rajdhani Bold 11, left pad 11, right pad 6, 5 px top-right chamfer.
6. Stale: the band replaces the header. Band x12-247, y6-21, #0a0506 fill, 1 px #ffe600 outline, 2 px hazard strips at y6-7 and y20-21. Text "RELINK // OPEN CLAUDE CODE", Rajdhani Bold 11, #ffe600, x20, baseline 17. Tag "14 M OLD", Share Tech Mono 11, #e8957f, right edge x239, baseline 17. Rows drop to #a27664 for labels, values, fills and time line, with no opacity.
7. Glow (values only): draw the glyphs in #ff6a4f on a transparent layer, GaussianBlur(radius=3), alpha 0.45, composited under the crisp glyphs. Critical value: #ff2e3d at alpha 0.35. Pip: #3ef0ff at alpha 0.5.
8. Pulse: pip and strip dot alpha(t) = 0.65 + 0.35*cos(2*pi*t/2.4). No stepped blink.
9. Refresh hit, value numerals only, once per refresh: frame 1 (0-50 ms) cyan copy dx -2, red copy dx +2. Frame 2 (50-100 ms) cyan -1, red +1. Frame 3 (100-150 ms) no offset. The base glyphs in #ffd9cd are drawn last on every frame. The channels never flip sides.
10. Hazard: pixel is #ffe600 when floor((x+y)/4) is even, else #0a0506. Horizontal period 8 px, stripes run bottom-left to top-right.

PILLOW RECIPE: TASKBAR STRIP (240 x 38, placed 5 px below the top of the 48 px bar, left of the tray)
Frame: chamfer 7 top-left and bottom-right, 1 px line, panel, scanlines as the widget.
Row A (top 6) and row B (top 22). Label 5H or 7D, Rajdhani Bold 11, #ffd9cd, x10, baseline 15 (row B 31).
Pace hairline x28-151, y8-9 (row B y24-25). Tick 2 x 6 at y6-11 (row B y22-27).
Bar x28-151 (124 px), y11-16 (row B y27-32), fill with a 3 px slant, #ff9a82.
Percent: Share Tech Mono 11, #ffd9cd, right edge x176, baseline 15.5 (row B 31.5).
Status: dot 6x6 at (186,16), accent, pulsed. Word LIVE, Rajdhani Bold 11, #3ef0ff, x196, baseline 23.

PILLOW RECIPE: TRAY ICON (16, 20, 24, 32 px; 16 for 100 percent, 20 for 125, 24 for 150, 32 for 200)
Masters are an outer octagon minus an inner octagon (even-odd), with integer vertices in night_city.json under extras.tray.masters. Band is 4 px at 16, 20 and 32, and 3 px at 24.
Centre cell: none at 16 or 20. At 24 a 4x4 square (x10-13). At 32 a 6x6 square (x13-18). Accent when normal, otherwise the state colour.
Arc: the band between perimeter fraction 0 and pct/100, walked clockwise from top-centre. Colours: normal #ff9a82, warn #ffe600, crit #ff2e3d. Stale uses #a27664 as 2 px dashes with 1 px gaps.
Track: #6a4540, about 2.0:1 on the taskbar.
Checked at 1x and 4x in the mock. A real notification-area test at 100, 125 and 150 percent was not run.

TYPE SIZES AND BASELINES
Widget and strip: brand 12, labels 13, values 15, units, reset, tags, chips, band and time 11 (minimum 11).
Dashboard: title 12, h1 23, h2 30, big numerals 48, cost 52, minis 24, burn 40.
Baselines from line top, measured with Chromium canvas metrics (accurate to about 0.5 px): Rajdhani Bold 12/lh12 = 9.5; Rajdhani Bold 13/lh14 = 11; Rajdhani SemiBold 11/lh14 = 10; Rajdhani Bold 11/lh12 = 9; Rajdhani Bold 11/lh11 = 8.5; Share Tech Mono 11/lh14 = 10.5; Share Tech Mono 15/lh14 = 11.5; Share Tech Mono 11/lh12 = 9.5. Full table in extras.text_baselines.

DASHBOARD (1200 x 760, every rule scoped under .nc-win)
Title bar 34, nav 200, body 726. Limit cards 176 high, three across. Cost row 216 (hero 1.25fr, 2x2 minis 1.15fr, burn 1fr). Row three 178 (mix 1.05fr, week 0.9fr, daily 1.35fr). Footer 26, gaps 14.
Segmented bars: 31 cells, 7 px wide, 2 px gaps, 277 px row. Cell fill = clamp(p/100*31 - i, 0, 1).
Pace: a 3 px hairline above each bar, the same rule as the widget.
Scanlines: 15 percent overlay above the content.
Refresh hit: the nc-hit class goes on value numerals on each refresh and is never looped.

SAMPLE SET (reconciled; replaces the brief's values)
Clock: Wed 2026-10-21, 4:12 PM.
Session, 5 h window: opened 2:39 PM, resets 7:39 PM. The brief's 10:39 PM cannot hold at 4:12 PM on a 5 h window. Elapsed 31 percent, used 13, delta -18.
Weekly, 7 d window: opened Mon 2026-10-19 9:00 AM, resets Mon 2026-10-26 9:00 AM. The brief's Wed 4:00 PM is already past. Elapsed 33 percent, used 2, delta -31.
Fable: 50 percent share, 0 used, resets with weekly.
Cost: $1,659 API-equivalent against a $100 plan, 16.6x. Today $84.20, peak $141, average $55.

MUST-FIX STATUS
Dashboard numerals hit once per refresh with no loop. Widget and strip pulses are eased at 2.4 s. No stepped blink.
Functional text moved off fg-faint to ink-2 and ink-3, minimum 11 px. The widget hex is removed. Scanlines are 15 percent under content, and the dashboard overlay is 15 percent.
Stale: no row opacity, thin 2 px hazard strips, and one cause per message.
Critical numerals use the danger red and the CRIT chip carries the hazard cap. WARN is an outline chip. WARN and CRIT are 11 px.
Track lifted to 1.54:1 with a 100 percent end tick. Reset text is 11 px in ink-2.
Pace hairline above the bar on the widget, strip and dashboard. The strip has its time line and tick.
Refresh hit: three frames with fixed channel offsets.
16 px tray: 4 px band with a heavier arc. Stale is dashed. Masters at 16, 20, 24 and 32 with integer edges.
Signal Yellow: warn moved to red-orange. Netrunner: usage, time and LIVE checked distinct in the variant capture.
Limit cards rendered in all four variants. The font credit with licences is on the sheet.

OPEN ITEMS
No real notification-area DPI test. Today's bar is cyan, not yellow as in the instrument mock. The slice-shift refresh frame is not adopted. The mock loads Google Fonts for preview; the app bundles the TTFs. The mock plays the hit once on load so it is visible; the app should add nc-hit only on real refreshes.
