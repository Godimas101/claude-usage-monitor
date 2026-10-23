/* app.js: the dashboard client. Polls /api/state every 2 s while the window is visible (ETag, so an
 * unchanged state costs a 304), routes on the hash (#overview #limits #cost #efficiency #history #settings
 * #about; #nerds, the old name of Efficiency, still opens it), and renders from the live MonitorState plus the
 * statistics reports (/api/analytics/<name>, /api/export). Settings apply at once through POST /api/settings.
 * Dependencies: charts.js (window.CUMCharts). The theme's own script (themes/<id>.js, which registers
 * window.CUM_THEME_FX[id]) and the motion layer (motion.js) are optional: without them the page is complete and still.
 * Nothing here stores a token, a message or a file body. The token comes from the page's meta tag.
 *
 * ── Markup contract for theme authors ─────────────────────────────────────────────────────────────
 * The markup is shared by every theme. A theme's CSS (themes/<id>.css) owns layout, shape and effect and
 * reads colour from the token variables the server sets on :root: --bg --surface --surface-alt --border
 * --border-dim --text --text-bright --text-dim --accent --accent-alt --ok --warn --alert --track --glow
 * --stale, plus --font-display/-body/-mono, --radius, --radius-control, --chamfer, --stroke. The class
 * prefix nm- is historic ("monitor") and shared by every theme. Chart colours come from --chart-* variables
 * that fall back to those tokens (charts.js lists them); --chart-pips: 1 draws bars as pips.
 * Two tiny structural stylesheets (this file's and charts.js's, each inserted first in <head> so any theme rule
 * wins) make the parts work with no theme at all: hidden helper text (.nm-sr), the tooltip's position,
 * the odometer's digit strips, segmented bars (.nm-cells, .nm-ratio), the time-left bar (.nm-remain, hidden), the
 * chart hosts, and the invisible text of a value that has not arrived (see Pending). Everything
 * else, and all of the look, is the theme's. Text is natural case wherever a theme may want a different
 * one (use text-transform); the overview cards keep their approved upper case.
 *
 * Shell       .nm-screen > .nm-content > header.nm-bar (.nm-brand .nm-bar-r .nm-uplink .nm-alive
 *             .nm-refresh) + .nm-app > nav.nm-nav (.nm-kick .nm-menu a.on .nm-health .nm-kv .nm-phos
 *             .nm-sw) + main.nm-main; overlays .nm-boot .nm-scan .nm-vig; #tip.nm-tip is the one tooltip.
 * Page        .nm-head > h1.nm-h1 (+ .nm-cur) + p.nm-sub; .nm-msg is a status banner (role=status);
 *             .nm-hazard the critical stripe. The overview body is .nm-grid (3 columns, 3 rows); every
 *             other page is .nm-pgrid (12 columns) whose children take .span-3 .span-4 .span-5 .span-6
 *             .span-7 .span-8 or .wide (all 12); below 1000 px everything is one column.
 *             .nm-colstack stacks two cards in one grid cell; .nm-toolbar (.nm-toolbar-k, .nm-toolbar-n)
 *             is a full-width row of controls.
 * Update      .nm-update[role=status] > .nm-update-t (b: "Claude Usage Monitor vX.Y is available", span: what Update now
 *             does) + .nm-actions (button.nm-btn[data-action=update-now | update-notes | update-skip]) + .nm-status.
 *             The first banner of every page but Settings (which has the same buttons in its System card), present only while
 *             /api/state reports a release that was not skipped.
 * Card        section.nm-card[.wide|.span-N][.is-warn|.is-crit|.is-real] > .nm-card-in > .nm-card-t
 *             (.nm-tag | h2.nm-ttl, and .nm-meta-t) + the body. .is-real marks the one card of real money.
 * Values      .nm-big (sizes .h .m .s), .nm-big-row, .nm-row2 (.is-base), .nm-facts, .nm-chip (.solid
 *             .is-ok .is-warn .is-crit), .nm-verdict, .nm-note, .nm-disc (a disclaimer), .nm-lede,
 *             .nm-story (a sentence), .nm-kv-grid (.is-one) > .nm-kv > span + b, .nm-kpi (.is-row) > .nm-kl,
 *             .nm-pace (usage and time; .fc is the forecast line), .nm-rail, .nm-ratio, .nm-cells (a segmented
 *             bar: i.on / i.part with --f), .nm-mix > .nm-mrow (.k label, cells, .v value), .nm-meter
 *             (two .nm-mrow: time against use), .nm-legend, .nm-stack-wrap, .nm-remain (the time left in a window as a
 *             bar: style="--r:<share left>%" > .l label + .v time; hidden until a theme draws it, as Edgerun does in
 *             place of the RESETS and IN facts).
 * Stat        .nm-stats (.is-quad) > .nm-stat (.is-wide, .is-block) [role=group, tabindex=0, title,
 *             aria-describedby] > .nm-stat-k + .nm-stat-v (.is-big, .is-text) + .nm-stat-s + .nm-cells +
 *             .nm-why.nm-sr: the one-line explanation, also shown in #tip on hover or focus. A figure
 *             carries style="--n:<characters>" so a theme can size it to its tile (Nostromo does).
 * Tables      .nm-table-wrap > table.nm-table (.nm-fixed, .nm-tokens; th.r / td.r right-aligned, td.v a value,
 *             td.lead a row label, tr.is-today a highlighted row, .nm-trunc truncates).
 * Controls    .nm-seg > button[.on][aria-pressed] (segmented), .nm-btn (.solid), .nm-toggle > input + .box +
 *             .lbl, .nm-select, .nm-input (.is-time; aria-invalid), .nm-set > .nm-row-set (.is-stack; .t title,
 *             .d description), .nm-actions, .nm-status (.is-err), .nm-path (a file path, never changed case),
 *             .nm-err (inline validation, role=alert), .nm-levels, .nm-chips > .nm-tchip > b + button,
 *             .nm-addlevel, .nm-quiet, .nm-quiet-to, .nm-themes > .nm-theme (+ .nm-swatches), .nm-variants,
 *             .nm-mode-k, .nm-links, .nm-supp (a list of names), .nm-tier, .nm-authors. A switched-off group
 *             (.nm-set.is-off, Alerts) disables its controls (:disabled); its text is not dimmed.
 * States      .nm-empty, .nm-error, .nm-pending, .nm-callout (.is-warn: a boxed note; with data-pending it keeps its room and shows
 *             nothing, so a note that is there before and after its report does not move the card).
 * Overview    .nm-cost > .nm-cost-l (.nm-hero, .nm-plan, .nm-tiles > .nm-tile > small + .nm-big) + .nm-cost-r
 *             (.nm-chart-t, .nm-chart > the daily chart host .nm-daily). The figure in .nm-hero is
 *             .nm-big.h with style="--n:<characters>", its length, so a theme can size it to the column
 *             (Nostromo does, with a container query) and let the .nm-plan text wrap beside it.
 * History     .nm-odo-row > .nm-odo-main (.nm-odo > .nm-od-d > .strip > i, the digit shown set by --d;
 *             .nm-od-sep; .nm-odo-facts) + .nm-compare (.nm-cmp-body > .nm-cmp-n .nm-cmp-u .nm-cmp-t; .nm-dots
 *             > button, the comparison picker).
 * Charts      .nm-chart-host[data-chart] holds one SVG from charts.js (cum-* classes, see that file); the
 *             hosts carry .nm-line .nm-hbars .nm-calendar .nm-matrix .nm-daily so a theme can size them.
 * Data hooks  data-seg / data-value (a segmented button), data-action (a button), data-key (a setting),
 *             data-quiet and data-needs-alerts (the alert form), data-fid (focus returns to this id after a
 *             redraw), data-tip (tooltip text), data-external (a link opened in the browser).
 *
 * ── Pending: a page is never empty ───────────────────────────────────────────────────────────────
 * A page opens as its whole card layout in the first frame: every card, title, label, chart frame and bar track. What has
 * not arrived yet (a report, or the first /api/state) is built from placeholder data of the right shape, so no card is
 * missing and nothing moves when the numbers land. A card is filled in place: its element and frame stay, only its
 * contents are replaced, and a card whose markup is unchanged is not touched at all.
 *   Card      section.nm-card[data-card=<key>][data-state=pending | ready | error][data-needs=<report keys>]. pending: the
 *             numbers are not in (aria-busy=true). error: the report failed; the card shows its title and one .nm-error line
 *             in place of its contents, and the other cards are not affected. data-updating is on a card whose numbers are
 *             on screen and are being fetched again, only once that has taken over 600 ms (a quiet cue, never a blank).
 *   Marks     A figure (.nm-big .nm-stat-v .nm-stat-s .nm-mrow .v .nm-kv b .nm-facts spans .nm-pace spans .nm-story table
 *             cells .nm-odo-facts b .nm-cmp-n .nm-toolbar-n), a bar (.nm-cells .nm-ratio) or a chart (.nm-chart-host .cum-spark
 *             .cum-stack .nm-odo) whose value is not in carries data-pending (and aria-hidden). Its text is placeholder text
 *             that only sizes the slot; the structural stylesheet makes it transparent and the theme draws what stands in its
 *             place (a block, a cursor, noise). When the value is in: data-final=<its final text> on a figure and data-fill=<0
 *             to 100> on a bar; and data-fresh on the ones that are new or changed in the last paint (a figure showing what
 *             it showed before is not fresh, so a refresh of unchanged numbers replays nothing).
 *   Revisits  The last good report of each page and window is kept in memory: a page opened again shows those numbers at
 *             once and refreshes behind them, without a placeholder and without replaying the figures that did not change.
 *
 * Motion      Everything that moves checks data-reduce-motion="1" on <html> (the reduce_motion setting) and
 *             prefers-reduced-motion. With either, nothing animates, enter and arrive are never called (cancel still is:
 *             it only finishes what was playing) and the comparison does not rotate. The hooks a theme script
 *             (window.CUM_THEME_FX[id]) or the motion layer may implement:
 *               enter(main, page)   the page just drawn into #main is due its entrance: a page was opened (a tab, a link,
 *                                   back or forward), the dashboard started, or the theme changed. Not for a refresh of the
 *                                   same page, a variant or colour change, or a card filled in place. It is called once the
 *                                   theme's stylesheet and script are in and the window is shown, never waiting for reports.
 *               cancel(main)        finish what is playing, at the final state. Called before another page or theme replaces
 *                                   the page and when reduce_motion turns on; not when a card is filled in place.
 *               arrive(card, info)  a card's numbers arrived or changed in place. info is { page, card, first, from, to,
 *                                   fresh, entering, reduced }: first is true for the first numbers of a card, fresh counts
 *                                   the figures and bars marked data-fresh, entering is true while an entrance plays.
 *             Classes on #main: fx-run (the theme's entrance is playing; the theme sets and clears it), fx-quiet (the page just
 *             changed under the pointer: hover effects wait until the pointer moves or the wheel turns; the page sets it and
 *             clears it itself). fx-hold, which kept the page hidden until its reports arrived, is retired.
 *             Events, for code that is not a theme script: cum:card-data on the card (bubbles; detail is the info above) and
 *             cum:page-rendered on #main (detail { page, opened, reduced, updated: [card keys], added: [card keys] }).
 */
(function () {
  "use strict";

  var POLL_MS = 2000;
  var FALLBACK_CSS = "/static/themes/nostromo.css";
  var TOKEN = (function () {
    var meta = document.querySelector('meta[name="monitor-token"]');
    return meta ? meta.getAttribute("content") || "" : "";
  })();
  var CHARTS = window.CUMCharts;
  var PAGES = [
    { key: "overview", label: "OVERVIEW", title: "OVERVIEW", sub: "Session telemetry for the current window. Pace is usage against elapsed time." },
    { key: "limits", label: "LIMITS", title: "LIMITS", sub: "Every limit window, its reset and where it is heading." },
    { key: "cost", label: "COST & VALUE", title: "COST & VALUE", sub: "API-equivalent at list prices. Not what your plan bills." },
    { key: "efficiency", label: "EFFICIENCY", title: "EFFICIENCY", sub: "Efficiency counters from your own transcripts. Hover or focus a figure to see what it means." },
    { key: "history", label: "HISTORY", title: "HISTORY", sub: "A year of use, hour by hour, and the all-time odometer." },
    { key: "settings", label: "SETTINGS", title: "SETTINGS", sub: "Every change applies at once. Nothing needs a restart." },
    { key: "about", label: "ABOUT", title: "ABOUT", sub: "Claude Usage Monitor, the people behind it, and where to report problems." }
  ];
  var PAGE_KEYS = PAGES.map(function (p) { return p.key; });
  // Old names of pages, so a saved link or a bookmark (#nerds, from before the page was called Efficiency) still opens it.
  var PAGE_ALIASES = { nerds: "efficiency" };
  var STATUS = {
    live: { text: "UPLINK LIVE", ok: true },
    loading: { text: "ACQUIRING", ok: false },
    stale: { text: "AUTH LOST", ok: false },
    offline: { text: "NO CARRIER", ok: false },
    rate_limited: { text: "RATE LIMITED", ok: false },
    no_credentials: { text: "NO CREDENTIALS", ok: false },
    paused: { text: "PAUSED", ok: false }
  };
  var ACCENT_ABBR = {
    amber: "AMB", green: "GRN", white: "WHT", red: "RED", blue: "BLU", cyan: "CYN", orange: "ORG",
    purple: "PUR", system: "SYS", teal: "TEA", rose: "ROS"
  };
  var WINDOW_ORDER = ["session", "today", "week", "month", "cycle", "all"];
  var WINDOW_LABEL = {
    session: "SESSION (5 H)", today: "TODAY", week: "7 DAYS", month: "THIS MONTH",
    cycle: "BILLING CYCLE", all: "ALL TIME"
  };
  var WINDOW_SEG = [["session", "SESSION"], ["today", "TODAY"], ["week", "7 DAYS"], ["month", "MONTH"], ["cycle", "CYCLE"], ["all", "ALL"]];
  var WINDOW_PHRASE = {
    session: "this session", today: "today", week: "the last 7 days", month: "this month",
    cycle: "this billing cycle", all: "all time"
  };
  var DAYS = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];
  var WEEKDAYS_MON = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];
  var MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
  var LINKS = {
    repo: "https://github.com/Godimas101/claude-usage-monitor",
    bug: "https://github.com/Godimas101/claude-usage-monitor/issues/new?template=bug_report.md&labels=bug",
    patreon: "https://patreon.com/Godimas101"
  };
  var POLL_CHOICES = [
    [60000, "1 MIN"], [300000, "5 MIN"], [600000, "10 MIN"], [900000, "15 MIN"], [1800000, "30 MIN"], [3600000, "60 MIN"]
  ];
  var BASIS = "API-equivalent at list prices";
  // The Fast mode card's note. It is the same words before the report, with no fast-mode request and with some, so the card is
  // as tall in all three and only its tone changes (a note that said something else per state wrapped to another line count).
  var FAST_NOTE = "Fast mode on Pro and Max plans is billed to your usage credits, not to your plan&rsquo;s limits.";
  // Points of the clock within which a limit counts as on pace. The same number as windows.PACE_BAND_POINTS on the
  // Python side (the widgets and the pacing report use it), which tests/test_webui.py checks.
  var PACE_BAND = 10;
  var PREFS_KEY = "cum-dashboard-prefs";
  // What the page needs to work whatever the theme: the odometer's strips, segmented bars, chart hosts.
  var BASE_CSS = [
    ".nm-sr{position:absolute;width:1px;height:1px;margin:-1px;padding:0;border:0;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}",
    ".nm-chart-host{width:100%;min-width:0}",
    ".nm-remain{display:none}",
    ".nm-cells,.nm-ratio{display:flex;gap:2px;height:10px;flex:none}",
    ".nm-cells i,.nm-ratio i{display:block;flex:1 1 0;background:var(--track)}",
    ".nm-cells i.on,.nm-ratio i.on{background:var(--accent)}",
    ".nm-cells i.part,.nm-ratio i.part{background:linear-gradient(90deg,var(--accent) var(--f,0%),var(--track) var(--f,0%))}",
    ".nm-odo{display:flex;align-items:center;flex-wrap:nowrap}",
    ".nm-od-d{position:relative;display:block;flex:none;width:.6em;height:1.1em;overflow:hidden}",
    ".nm-od-d .strip{display:flex;flex-direction:column;transform:translateY(calc(var(--d,0) * -1.1em))}",
    ".nm-od-d .strip i{display:block;flex:none;height:1.1em;line-height:1.1em;text-align:center;font-style:normal}",
    // A value that is not known yet keeps its room and shows nothing: the placeholder text under it is only there to size it.
    // A theme draws what stands in its place (a block, a cursor, noise) with a more specific !important rule of its own.
    "[data-pending],[data-pending] *{color:transparent!important;text-shadow:none!important}",
    "[data-pending] .cum-t{fill:transparent!important}",
    "[data-pending] .on,[data-pending] .part{box-shadow:none!important}",
    // A boxed note (the Fast mode card's) that is there before its report and after it: before, it keeps its room and its text
    // is transparent like any placeholder's, and its box is not drawn either.
    ".nm-callout[data-pending]{border-color:transparent!important;background:transparent!important;box-shadow:none!important}",
    // A placeholder may carry a typical number (it sizes the slot like the real one), so a pending bar never shows a fill.
    "[data-pending] i.on,[data-pending] i.part,[data-pending] i.part::after{background:var(--track)!important}"
  ].join("\n");
  // How long a report is shown before it is fetched again, and how soon a failed one is retried.
  var STAT_TTL = { pacing: 20000, cost: 30000, efficiency: 60000, history: 120000, sessions: 60000 };
  var STAT_RETRY_MS = 5000;
  // A report that is being refreshed behind numbers already on screen shows the "updating" cue only if it takes this long.
  var UPDATING_DELAY_MS = 600;
  var COMPARE_MS = 8000;

  // One line each: what a figure means. Shown on hover or focus, and read by aria-describedby.
  var WHY = {
    "cache-hit": "How much of what Claude read came from the cache instead of being sent fresh. Higher means cheaper and faster.",
    "cache-saved": "What the same tokens would have cost with no caching, minus what they cost. List prices, not a bill.",
    "cache-fresh": "Input tokens sent fresh, with no cache involved. This is the expensive kind of input.",
    "cache-read": "Tokens read back from the cache, billed at a small fraction of the normal input price.",
    "cache-write-5m": "Tokens stored in the 5-minute cache. Writing costs 25% more than normal input.",
    "cache-write-1h": "Tokens stored in the 1-hour cache. Writing costs twice the normal input price.",
    "thinking-share": "The part of Claude's output that was extended thinking rather than visible text. Thinking is billed as output.",
    "thinking-tokens": "Output tokens Claude spent on extended thinking.",
    "output-tokens": "Everything Claude wrote back to you, thinking included.",
    "ctx-avg": "The average size of a request: everything Claude read, fresh and cached together.",
    "ctx-p50": "Half of your requests carried less context than this.",
    "ctx-p95": "Nineteen in twenty requests carried less than this. High values mean long sessions close to the context limit.",
    "ctx-max": "The largest single request in this window, by the API's own token count. Claude Code counts a compaction itself, so the two can differ.",
    "compactions": "Times Claude Code summarised the conversation to make room. Auto means it ran out of space; manual means you ran /compact.",
    "compactions-day": "Compactions per day you used Claude Code. Fewer usually means shorter, more focused sessions.",
    "compact-pre": "The average conversation size just before a compaction, by Claude Code's own count. The API's request counts can differ.",
    "active-days": "Local days with at least one request in this window.",
    "tool-total": "Tool calls Claude made: reading and editing files, running commands, calling MCP servers.",
    "tool-errors": "Tool calls that came back with an error. Some are normal, like a failing test or a file that is not there.",
    "tool-rate": "Errors divided by tool calls. A rate that keeps climbing can mean Claude is fighting your setup.",
    "lines-added": "Lines Claude added to files through its edit and write tools.",
    "lines-removed": "Lines Claude removed from files through its edit tools.",
    "files-created": "New files Claude created with its write tool.",
    "commits": "Git commits Claude ran from the shell in this window. A failed commit is not counted.",
    "pushes": "Git pushes Claude ran from the shell in this window.",
    "prs": "Pull requests Claude opened in this window.",
    "cost-pr": "API-equivalent cost of this window divided by the pull requests opened in it.",
    "cost-100": "API-equivalent cost per 100 changed lines (added plus removed).",
    "api-errors": "Times the API itself failed a request, such as overloaded or rate limited. Not tool errors.",
    "web-searches": "Web searches Claude ran for you.",
    "web-fetches": "Web pages Claude fetched for you.",
    "streak-current": "Days in a row, ending today (or yesterday if you have not started today), with at least one request.",
    "streak-longest": "The longest run of days in a row with at least one request.",
    "busiest-day": "The day with the highest API-equivalent cost, across all time.",
    "first-use": "The first request this monitor has seen.",
    "history-active": "Days with at least one request, across all time.",
    "haiku-share": "Haiku is the small, cheap model Claude Code uses for background work. A high share usually means subagents are doing a lot of the work.",
    "haiku-main": "Haiku work in the conversations you drive yourself.",
    "haiku-sub": "Haiku work done by subagents that Claude started on its own.",
    "thread-main": "Work in the conversations you drive yourself.",
    "thread-sub": "Work done by subagents that Claude started on its own.",
    "fast-mode": "Fast mode answers quicker at a higher price. On Pro and Max plans it is paid from usage credits, not from your plan limits.",
    "tool-mix": "Which tools Claude reached for. MCP tools are grouped by the server that provides them.",
    "effort-mix": "How hard Claude was set to think on each request. Higher effort costs more tokens."
  };

  var S = {
    data: null,
    etag: null,
    linkOk: null,
    lastUpdatedAt: null,
    samples: [],
    settings: {},
    themes: null,
    themesRequested: false,
    themeKey: "",
    appliedVars: [],
    lastTheme: null,
    loadedScripts: {},
    page: null,
    formKey: null,
    minuteKey: "",
    status: { text: "", error: false },
    update: { text: "" },
    supporters: null,
    supportersRequested: false,
    inFlight: false,
    stats: {},               // "<name>?<params>" -> a statistics report and how it is going
    charts: {},              // chart host id -> how to build it, for the page on screen
    nextCharts: null,
    sig: "",                 // the markup of a form page on screen, to skip a redraw that would change nothing
    building: false,         // true while a page's markup is built: placeholders stand in where nothing has arrived
    enterPending: true,      // an entrance is due: the first page, a page change or a theme change (see playEnterIfDue)
    repaint: false,          // the next paint replaces every card (a theme change)
    skipped: "",             // the version the user skipped in this window, so its banner goes at once
    chartCard: "",           // the card whose markup is being built, and how many charts it has drawn so far
    chartN: 0,
    exportStatus: { text: "", path: "", error: false },
    odoShown: null,
    cmpTimer: 0,
    tipApi: null,
    ui: { costWindow: "cycle", folderMode: "folder", efficiencyWindow: "cycle", calMetric: "cost", matrixMetric: "cost", cmp: 0 }
  };

  // ── Helpers ─────────────────────────────────────────────────────────────────────

  function $(selector, root) {
    return (root || document).querySelector(selector);
  }

  function esc(value) {
    return String(value === null || value === undefined ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function num(value) {
    if (value === null || value === undefined || value === "") {
      return null;
    }
    var n = Number(value);
    return isFinite(n) ? n : null;
  }

  function clamp(v, lo, hi) {
    return Math.max(lo, Math.min(hi, v));
  }

  function pad2(n) {
    return (n < 10 ? "0" : "") + n;
  }

  function plural(n, word) {
    return fmtInt(n) + " " + word + (Math.round(n) === 1 ? "" : "s");
  }

  function fmtPct(value, digits) {
    var n = num(value);
    if (n === null) {
      return "--";
    }
    return (digits ? n.toFixed(digits) : String(Math.round(n))) + "%";
  }

  /* A ratio 0..1 as a percentage; "--" when there is none. */
  function fmtRate(value, digits) {
    var n = num(value);
    return n === null ? "--" : fmtPct(n * 100, digits === undefined ? 1 : digits);
  }

  function fmtUsd(value) {
    var n = num(value);
    if (n === null) {
      return "--";
    }
    var a = Math.abs(n);
    var body = a >= 1000 ? Math.round(a).toLocaleString("en-US") : a.toFixed(2);
    return (n < 0 ? "-$" : "$") + body;
  }

  function fmtUsdExact(value) {
    var n = num(value);
    return n === null ? "--" : (n < 0 ? "-$" : "$") + Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  /* Real money (usage credits) in the currency the account is billed in, which the usage API reports. The figures
   * of API-equivalent cost stay dollars (fmtUsd). An account that reports no currency is read as USD, and a code
   * the browser does not know is shown as the number and the code. */
  function fmtMoney(value, currency) {
    var n = num(value);
    if (n === null) {
      return "--";
    }
    var code = String(currency || "USD").trim().toUpperCase();
    try {
      return new Intl.NumberFormat("en-US", { style: "currency", currency: code }).format(n);
    } catch (e) {
      return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " " + code;
    }
  }

  function fmtPlanPrice(value) {
    var n = num(value);
    if (n === null) {
      return "--";
    }
    return n % 1 === 0 ? "$" + Math.round(n).toLocaleString("en-US") : fmtUsd(n);
  }

  function fmtUsdShort(value) {
    var n = num(value);
    if (n === null) {
      return "--";
    }
    return Math.abs(n) >= 1000 ? "$" + (n / 1000).toFixed(1) + "K" : fmtUsd(n);
  }

  /* A cost for a tile: at most six characters, so three tiles fit a narrow card. */
  function fmtUsdTile(value) {
    var n = num(value);
    if (n === null) {
      return "--";
    }
    var a = Math.abs(n);
    if (a >= 1e6) {
      return "$" + (n / 1e6).toFixed(1) + "M";
    }
    if (a >= 1000) {
      return "$" + (n / 1000).toFixed(1) + "K";
    }
    return a >= 100 ? "$" + Math.round(n) : fmtUsd(n);
  }

  /* The big cost figure: cents under $100, whole dollars to $10,000, then K. Six characters at most. */
  function fmtUsdHero(value) {
    var n = num(value);
    if (n === null) {
      return "--";
    }
    var a = Math.abs(n);
    if (a >= 1e6) {
      return "$" + (n / 1e6).toFixed(1) + "M";
    }
    if (a >= 10000) {
      return "$" + (n / 1000).toFixed(1) + "K";
    }
    return a >= 100 ? (n < 0 ? "-$" : "$") + Math.round(a).toLocaleString("en-US") : fmtUsd(n);
  }

  function fmtMult(value) {
    var n = num(value);
    if (n === null) {
      return "--";
    }
    return (n >= 100 ? String(Math.round(n)) : n.toFixed(1)) + "×";
  }

  function fmtCount(value) {
    var n = num(value);
    if (n === null) {
      return "--";
    }
    var a = Math.abs(n);
    if (a >= 1e9) {
      return (n / 1e9).toFixed(2) + "B";
    }
    if (a >= 1e6) {
      return (n / 1e6).toFixed(1) + "M";
    }
    if (a >= 1e3) {
      return (n / 1e3).toFixed(1) + "K";
    }
    return String(Math.round(n));
  }

  function fmtInt(value) {
    var n = num(value);
    return n === null ? "--" : Math.round(n).toLocaleString("en-US");
  }

  function fmtRemain(ms) {
    if (ms === null || !isFinite(ms)) {
      return "--";
    }
    var minutes = Math.floor(ms / 60000);
    if (minutes <= 0) {
      return "NOW";
    }
    var days = Math.floor(minutes / 1440);
    var hours = Math.floor((minutes % 1440) / 60);
    var mins = minutes % 60;
    if (days >= 1) {
      return days + " D " + hours + " H";
    }
    if (hours >= 1) {
      return hours + " H " + mins + " M";
    }
    return mins + " M";
  }

  function fmtAge(ms) {
    if (ms === null || !isFinite(ms)) {
      return "--";
    }
    var s = Math.max(0, Math.floor(ms / 1000));
    if (s < 60) {
      return s + " S";
    }
    var m = Math.floor(s / 60);
    if (m < 60) {
      return m + " M";
    }
    var h = Math.floor(m / 60);
    if (h < 48) {
      return h + " H";
    }
    return Math.floor(h / 24) + " D";
  }

  function parseTime(iso) {
    if (!iso) {
      return null;
    }
    var t = Date.parse(iso);
    return isFinite(t) ? t : null;
  }

  function clockText(ms) {
    if (ms === null) {
      return "--";
    }
    var d = new Date(ms);
    var now = new Date();
    var h = d.getHours();
    var text = (h % 12 || 12) + ":" + pad2(d.getMinutes()) + " " + (h >= 12 ? "PM" : "AM");
    var sameDay = d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
    return sameDay ? text : DAYS[d.getDay()] + " " + text;
  }

  function stampText(ms) {
    if (ms === null) {
      return "--";
    }
    var d = new Date(ms);
    var h = d.getHours();
    return MONTHS[d.getMonth()] + " " + d.getDate() + " " + (h % 12 || 12) + ":" + pad2(d.getMinutes()) + " " + (h >= 12 ? "PM" : "AM");
  }

  /* "YYYY-MM-DD" as a local date (new Date("YYYY-MM-DD") would read it as UTC and shift the day). */
  function parseYmd(text) {
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(text || ""));
    return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null;
  }

  function ymdOf(d) {
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
  }

  function dayText(ymd, withYear) {
    var d = parseYmd(ymd);
    if (!d) {
      return "--";
    }
    return DAYS[d.getDay()] + ", " + MONTHS[d.getMonth()] + " " + d.getDate() + (withYear ? ", " + d.getFullYear() : "");
  }

  function dateText(ms) {
    if (ms === null) {
      return "--";
    }
    var d = new Date(ms);
    return MONTHS[d.getMonth()] + " " + d.getDate() + ", " + d.getFullYear();
  }

  function hourText(ms) {
    var h = new Date(ms).getHours();
    return (h % 12 || 12) + " " + (h >= 12 ? "PM" : "AM");
  }

  function accentOf(variantId) {
    return String(variantId || "").replace(/-(dark|light)$/, "");
  }

  function hex6(value) {
    var m = /^#([0-9a-fA-F]{6})/.exec(String(value || ""));
    return m ? "#" + m[1] : null;
  }

  function errorText(res) {
    return res && res.data && res.data.error ? res.data.error : "HTTP " + (res ? res.status : "?");
  }

  /* A status line written in capitals ("UPDATE FAILED: UPDATE APPLY FAILED"): the monitor's reason is lower case, and the Gates
   * theme sets a line in sentence case only when all of it is capitals, so a reason left as it came would be shouted in
   * Nostromo and Night City (which upper-case it in CSS) and left half and half in Gates. */
  function failedText(prefix, res) {
    return prefix + ": " + errorText(res).toUpperCase();
  }

  /* The status line after "Check for updates now". `checked` is false when the monitor could not look (offline, GitHub's rate
   * limit, a slow answer): that is not "up to date". A monitor that sends no `checked` is taken to have looked. One sentence:
   * the Gates theme capitalises only the first letter of a line. */
  function checkUpdateText(res) {
    if (!res.ok) {
      return failedText("CHECK FAILED", res);
    }
    var result = res.data && res.data.result ? res.data.result : {};
    if (result.available) {
      return "AN UPDATE IS AVAILABLE.";
    }
    return result.checked === false ? "COULD NOT REACH GITHUB; TRY AGAIN LATER." : "YOU ARE UP TO DATE.";
  }

  /* A short stable digest of a string: enough to tell whether a report changed. */
  function hashText(text) {
    var h = 5381;
    for (var i = 0; i < text.length; i++) {
      h = ((h << 5) + h + text.charCodeAt(i)) | 0;
    }
    return (h >>> 0).toString(36) + "." + text.length.toString(36);
  }

  function reduceMotion() {
    if (document.documentElement.getAttribute("data-reduce-motion") === "1") {
      return true;
    }
    return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }

  // ── Per-viewer preferences (the last window and toggles chosen) ─────────────────

  function loadPrefs() {
    try {
      var saved = JSON.parse(window.localStorage.getItem(PREFS_KEY) || "null");
      if (!saved || typeof saved !== "object") {
        return;
      }
      var allowed = {
        costWindow: WINDOW_ORDER, efficiencyWindow: WINDOW_ORDER, folderMode: ["folder", "launch"],
        calMetric: ["cost", "tokens"], matrixMetric: ["cost", "requests"]
      };
      // The Efficiency page was "Nerds only", and kept its window under that name: a choice saved then still holds.
      if (saved.efficiencyWindow === undefined && saved.nerdsWindow !== undefined) {
        saved.efficiencyWindow = saved.nerdsWindow;
      }
      Object.keys(allowed).forEach(function (key) {
        if (allowed[key].indexOf(saved[key]) >= 0) {
          S.ui[key] = saved[key];
        }
      });
    } catch (e) {
      /* blocked or empty storage: the defaults stand */
    }
  }

  function savePrefs() {
    try {
      window.localStorage.setItem(PREFS_KEY, JSON.stringify(S.ui));
    } catch (e) {
      /* a private window keeps nothing; the choice still holds until the window closes */
    }
  }

  // ── API client ──────────────────────────────────────────────────────────────────

  function api(method, path, body, etag) {
    var headers = { "X-Monitor-Token": TOKEN, Accept: "application/json" };
    if (body !== undefined && body !== null) {
      headers["Content-Type"] = "application/json";
    }
    if (etag) {
      headers["If-None-Match"] = etag;
    }
    return fetch(path, {
      method: method,
      headers: headers,
      body: body !== undefined && body !== null ? JSON.stringify(body) : undefined,
      cache: "no-store",
      credentials: "omit"
    }).then(function (res) {
      return res.text().then(function (text) {
        var data = null;
        try {
          data = text ? JSON.parse(text) : null;
        } catch (e) {
          data = null;
        }
        return { status: res.status, ok: res.ok, data: data, etag: res.headers.get("ETag") };
      });
    });
  }

  // ── Statistics reports ──────────────────────────────────────────────────────────

  /* The cache cell for a report and its parameters. Pages ask for what they need; a report is fetched when
   * it is missing or older than its TTL, shown stale while the next one loads, and retried after a failure. */
  function statEntry(name, params) {
    var query = Object.keys(params || {}).sort().map(function (k) {
      return k + "=" + encodeURIComponent(params[k]);
    }).join("&");
    var key = name + (query ? "?" + query : "");
    var e = S.stats[key];
    if (!e) {
      e = S.stats[key] = {
        key: key, name: name, url: "/api/analytics/" + key, data: null, digest: "", error: "", status: 0,
        at: 0, errAt: 0, pending: false
      };
    }
    return e;
  }

  function loadStat(e, force) {
    if (e.pending) {
      return;
    }
    var now = Date.now();
    var fresh = e.data && now - e.at < (STAT_TTL[e.name] || 30000);
    var cooling = e.error && now - e.errAt < STAT_RETRY_MS;
    if (!force && (fresh || cooling)) {
      return;
    }
    if (document.visibilityState === "hidden" && e.data) {
      return;
    }
    e.pending = true;
    e.slow = false;
    if (e.data) {
      // The numbers on screen stay, and the cards that show them say so only if the refresh takes a while.
      window.clearTimeout(e.slowTimer);
      e.slowTimer = window.setTimeout(function () {
        if (e.pending) {
          e.slow = true;
          syncUpdating($("#main"));
        }
      }, UPDATING_DELAY_MS);
    }
    api("GET", e.url).then(function (res) {
      e.pending = false;
      window.clearTimeout(e.slowTimer);
      e.slow = false;
      if (res.ok && res.data) {
        var digest = hashText(JSON.stringify(res.data));
        e.data = res.data;
        e.digest = digest;
        e.error = "";
        e.status = 200;
        e.at = Date.now();
      } else {
        e.error = errorText(res);
        e.status = res.status;
        e.errAt = Date.now();
      }
      statArrived();
    }, function () {
      e.pending = false;
      window.clearTimeout(e.slowTimer);
      e.slow = false;
      e.error = "the dashboard lost its link";
      e.status = 0;
      e.errAt = Date.now();
      statArrived();
    });
  }

  function statArrived() {
    if (S.page !== "settings" && S.page !== "about") {
      renderPage();
    }
  }

  /* The reports the page on screen draws from. */
  function pageStats(page) {
    switch (page) {
      case "overview":
        return [statEntry("cost", { window: "cycle" }), statEntry("pacing"), statEntry("history")];
      case "limits":
        return [statEntry("pacing")];
      case "cost":
        return [statEntry("cost", { window: S.ui.costWindow })];
      case "efficiency":
        return [statEntry("efficiency", { window: S.ui.efficiencyWindow })];
      case "history":
        return [statEntry("history")];
      default:
        return [];
    }
  }

  function ensureStats() {
    pageStats(S.page || currentPage()).forEach(function (e) { loadStat(e, false); });
  }

  /* Marks every report old so the next look refetches it (a settings change that moves the numbers). */
  function invalidateStats() {
    Object.keys(S.stats).forEach(function (k) {
      S.stats[k].at = 0;
      S.stats[k].errAt = 0;
    });
    ensureStats();
  }

  function statProblem(e) {
    if (e.status === 503) {
      return "The usage database is not ready yet. The monitor creates it when it first reads your transcripts; this page tries again every few seconds.";
    }
    if (e.status === 501) {
      return "Statistics are not available in this build.";
    }
    return "Statistics could not be loaded (" + e.error + "). Trying again shortly.";
  }

  /* The one line a card shows in place of its numbers when its report failed (the whole sentence is in the banner). */
  function statProblemShort(e) {
    return e.status === 503 ? "Not ready yet. Trying again shortly." : e.status === 501 ? "Not available in this build." : "Could not be loaded. Trying again shortly.";
  }

  /* How a card that draws from these reports is doing: "ready" (every report is in, possibly an older one that is being
   * refreshed), "pending" (waiting for the first answer) or "error" (a report failed and there is nothing to show). */
  function entryState(entries) {
    var pending = false;
    var failed = false;
    entries.forEach(function (e) {
      if (!e.data) {
        if (e.error) {
          failed = true;
        } else {
          pending = true;
        }
      }
    });
    return failed ? "error" : pending ? "pending" : "ready";
  }

  /* The entry of the newest report of a kind that has arrived, whatever its parameters: the parts of a page that do not
   * depend on the window chosen (the plan multiple, the months) keep showing it while the new window's report loads. */
  function lastGoodEntry(name) {
    var best = null;
    Object.keys(S.stats).forEach(function (k) {
      var e = S.stats[k];
      if (e.name === name && e.data && (!best || e.at > best.at)) {
        best = e;
      }
    });
    return best;
  }

  /* Marks the cards whose report is being refreshed slowly (the cue is the card's frame; see the theme's CSS). */
  function syncUpdating(main) {
    if (!main) {
      return;
    }
    var cards = main.querySelectorAll("[data-needs]");
    for (var i = 0; i < cards.length; i++) {
      var keys = String(cards[i].getAttribute("data-needs") || "").split(" ");
      var busy = keys.some(function (k) {
        var e = S.stats[k];
        return !!(e && e.pending && e.data && e.slow);
      });
      if (busy) {
        cards[i].setAttribute("data-updating", "");
        cards[i].setAttribute("aria-busy", "true");
      } else {
        cards[i].removeAttribute("data-updating");
        if (cards[i].getAttribute("data-state") !== "pending") {
          cards[i].removeAttribute("aria-busy");
        }
      }
    }
  }

  /* A banner when a report on screen is older than it should be because the refresh failed. */
  function staleBanner(entries) {
    var bad = entries.filter(function (e) { return e.data && e.error; });
    if (!bad.length) {
      return "";
    }
    return '<div class="nm-msg" role="status"><b>STATISTICS OLD</b>' + esc("The latest refresh failed (" + bad[0].error + "). These figures are from " + fmtAge(Date.now() - bad[0].at).toLowerCase() + " ago.") + "</div>";
  }

  /* A banner with the whole sentence when a report failed and there is no older one to show: each card it feeds says only
   * that its numbers are not there. */
  function problemBanner(entries) {
    var bad = entries.filter(function (e) { return !e.data && e.error; });
    if (!bad.length) {
      return "";
    }
    return '<div class="nm-msg" role="status"><b>STATISTICS UNAVAILABLE</b>' + esc(statProblem(bad[0])) + "</div>";
  }

  // ── State accessors ─────────────────────────────────────────────────────────────

  function st() {
    return S.data && S.data.state ? S.data.state : null;
  }

  /* The limits and the surface split. While a page is being built and the monitor has sent no state at all, they are the
   * placeholder shape (skelUsage), so the page opens as a full set of cards; a state without usage stays an empty card. */
  function usage() {
    var s = st();
    if (s && s.usage) {
      return s.usage;
    }
    return S.building && !s ? skelUsage() : null;
  }

  function limits() {
    var u = usage();
    return u && u.limits ? u.limits : [];
  }

  function limitOf(kind) {
    var list = limits();
    for (var i = 0; i < list.length; i++) {
      if (list[i].kind === kind) {
        return list[i];
      }
    }
    return null;
  }

  function scopedLimits() {
    return limits().filter(function (l) { return l.kind !== "session" && l.kind !== "weekly_all"; });
  }

  function windowOf(name) {
    var s = st();
    return s && s.windows && s.windows[name] ? s.windows[name] : null;
  }

  function modelName(id) {
    var names = S.data && S.data.model_names ? S.data.model_names : {};
    return names[id] || id;
  }

  function currentTheme() {
    return S.data && S.data.theme ? S.data.theme : null;
  }

  function findTheme(id) {
    var list = S.themes || [];
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === id) {
        return list[i];
      }
    }
    return null;
  }

  // ── Limits: severity, pace, cells ───────────────────────────────────────────────

  function windowSeconds(limit) {
    return num(limit.window_seconds) || (limit.kind === "session" ? 18000 : 604800);
  }

  function severityOf(limit) {
    var raw = String(limit.severity || "").toLowerCase();
    if (raw) {
      if (raw.indexOf("crit") >= 0 || raw.indexOf("alert") >= 0) {
        return "critical";
      }
      if (raw.indexOf("warn") >= 0 || raw.indexOf("advis") >= 0) {
        return "warning";
      }
      return "normal";
    }
    var pct = num(limit.percent) || 0;
    if (pct >= 90) {
      return "critical";
    }
    return pct >= 75 ? "warning" : "normal";
  }

  var SEVERITY_WORD = { normal: "NOMINAL", warning: "ADVISORY", critical: "CRITICAL" };

  function worstSeverity(list) {
    var rank = { normal: 0, warning: 1, critical: 2 };
    var worst = "normal";
    list.forEach(function (l) {
      var s = severityOf(l);
      if (rank[s] > rank[worst]) {
        worst = s;
      }
    });
    return worst;
  }

  function paceOf(limit, now) {
    var reset = parseTime(limit.resets_at);
    if (reset === null) {
      return { elapsed: null, diff: null, kind: null, reset: null, remain: null };
    }
    var span = windowSeconds(limit) * 1000;
    var elapsed = clamp((now - (reset - span)) / span, 0, 1);
    var pct = num(limit.percent) || 0;
    var diff = pct - elapsed * 100;
    var kind = diff > PACE_BAND ? "ahead" : diff < -PACE_BAND ? "behind" : "on_pace";
    return { elapsed: elapsed, diff: diff, kind: kind, reset: reset, remain: reset - now };
  }

  function paceChip(limit, p, scoped) {
    var pct = num(limit.percent) || 0;
    if (pct <= 0) {
      return { text: scoped ? "NO DRAW" : "UNUSED", cls: "" };
    }
    if (p.kind === "ahead") {
      return { text: "AHEAD OF PACE", cls: " solid" };
    }
    if (p.kind === "behind") {
      return { text: "UNDER PACE", cls: "" };
    }
    if (p.kind === "on_pace") {
      return { text: "ON PACE", cls: "" };
    }
    return { text: "NO CLOCK", cls: "" };
  }

  function cellsHTML(n, value) {
    var x = clamp(num(value) || 0, 0, 100) / 100 * n;
    var full = Math.floor(x);
    var frac = x - full;
    var out = "";
    for (var i = 0; i < n; i++) {
      if (i < full) {
        out += '<i class="on"></i>';
      } else if (i === full && frac > 0.02) {
        out += '<i class="part" style="--f:' + (frac * 100).toFixed(1) + '%"></i>';
      } else {
        out += "<i></i>";
      }
    }
    return out;
  }

  function railHTML(p) {
    var t = p.elapsed === null ? 0 : p.elapsed * 100;
    return '<div class="nm-rail" style="--t:' + t.toFixed(1) + '%" title="Time elapsed in this window"><i></i><b></b></div>';
  }

  /* The time left in a window as a bar: filled by the share of the window that remains, labelled with the time. Every
   * theme gets the markup and only the one that draws it (Night City's Edgerun) shows it. */
  function remainHTML(p) {
    if (p.reset === null || p.elapsed === null) {
      return "";
    }
    var left = fmtRemain(p.remain);
    var text = "Time remaining " + left + ", resets " + clockText(p.reset);
    return '<div class="nm-remain" style="--r:' + ((1 - p.elapsed) * 100).toFixed(1) + '%" role="img" aria-label="' + esc(text) + '" title="' + esc(text) +
      '"><span class="l">TIME REMAINING</span><span class="v">' + esc(left) + "</span></div>";
  }

  function sevClass(limit) {
    var s = severityOf(limit);
    return s === "warning" ? " is-warn" : s === "critical" ? " is-crit" : "";
  }

  /* The forecast line under a limit's pace: from the pacing report, or nothing before it arrives. */
  function sessionForecast(blk) {
    if (!blk || num(blk.percent) === null) {
      return "";
    }
    switch (blk.verdict) {
      case "LIMITED":
        return "<b>LIMIT REACHED</b>";
      case "AT_RISK":
        return "<b>LIMIT ~" + esc(clockText(parseTime(blk.projected_limit_at))) + "</b> BEFORE RESET";
      case "SAFE":
        return "<b>SAFE</b> UNTIL RESET";
      case "IDLE":
        return "<b>IDLE</b> NO RECENT DRAW";
      default:
        return "FORECAST NEEDS MORE READINGS";
    }
  }

  function weeklyForecast(blk) {
    if (!blk || num(blk.percent) === null) {
      return "";
    }
    var end = num(blk.projected_end_pct);
    var budget = num(blk.daily_budget_pct);
    var parts = [end === null ? "PROJECTION NEEDS MORE TIME" : "<b>ENDS ~" + Math.round(end) + "%</b>"];
    if (budget !== null) {
      parts.push("BUDGET " + Math.round(budget) + "%/DAY");
    }
    return parts.join(" &middot; ");
  }

  function matchScoped(list, limit, index) {
    var found = (list || []).filter(function (b) { return b.label === limit.label; })[0];
    return found || (list || [])[index] || null;
  }

  function limitCard(limit, opts) {
    opts = opts || {};
    var now = Date.now();
    var p = paceOf(limit, now);
    var pct = num(limit.percent) || 0;
    var chip = paceChip(limit, p, !!opts.share);
    var tag = limit.label || (limit.kind === "session" ? "SESSION" : "WEEKLY");
    var facts = [];
    if (opts.share) {
      facts.push("<span>OF " + esc(fmtPct(opts.share)) + " SHARE</span>");
    }
    if (p.reset !== null) {
      facts.push("<span>RESETS <b>" + esc(clockText(p.reset)) + "</b></span>");
      if (!opts.share) {
        facts.push("<span>IN " + esc(fmtRemain(p.remain)) + "</span>");
      }
    }
    facts.push('<span class="nm-chip' + chip.cls + '">' + esc(chip.text) + "</span>");
    var diffText = p.diff === null ? "NO RESET TIME" : (p.diff >= 0 ? "+" : "") + Math.round(p.diff) + " PTS, " + chip.text;
    return '<section class="nm-card' + (opts.wide ? " wide" : "") + sevClass(limit) + '" data-limit="' + esc(limit.kind) + '">' +
      '<div class="nm-card-in">' +
      '<div class="nm-card-t"><span class="nm-tag">' + esc(tag) + '</span><span class="nm-meta-t">' + esc(opts.meta || "") + "</span></div>" +
      '<div class="nm-row2"><div class="nm-big">' + esc(fmtPct(pct)) + '</div><div class="nm-facts">' + facts.join("") + "</div></div>" +
      remainHTML(p) +
      '<div class="nm-pace"><span>USAGE ' + esc(fmtPct(pct)) + " / TIME " + esc(p.elapsed === null ? "--" : fmtPct(p.elapsed * 100)) +
      "</span><b>" + esc(diffText) + "</b>" +
      (opts.fcPending ? '<span class="fc" data-pending="">FORECAST NEEDS MORE READINGS</span>' : opts.fc ? '<span class="fc">' + opts.fc + "</span>" : "") + "</div>" +
      '<div class="nm-cells" aria-hidden="true">' + cellsHTML(32, pct) + "</div>" +
      railHTML(p) +
      "</div></section>";
  }

  function emptyLimitCard(tag, meta) {
    return '<section class="nm-card"><div class="nm-card-in"><div class="nm-card-t"><span class="nm-tag">' + esc(tag) +
      '</span><span class="nm-meta-t">' + esc(meta) + '</span></div><p class="nm-empty">Waiting for the first reading.</p></div></section>';
  }

  // ── Building blocks shared by the pages ─────────────────────────────────────────

  /* A card. o: {title | tag, meta (text) or metaHtml (markup, for controls), cls, id, body, label}. */
  function card(o) {
    var meta = o.metaHtml !== undefined ? o.metaHtml : '<span class="nm-meta-t">' + esc(o.meta || "") + "</span>";
    var head = o.tag
      ? '<div class="nm-card-t"><span class="nm-tag">' + esc(o.tag) + "</span>" + meta + "</div>"
      : '<div class="nm-card-t"><h2 class="nm-ttl">' + esc(o.title) + "</h2>" + meta + "</div>";
    return '<section class="nm-card' + (o.cls ? " " + o.cls : "") + '"' + (o.id ? ' id="' + o.id + '"' : "") +
      ' aria-label="' + esc(o.label || o.title || o.tag || "") + '"><div class="nm-card-in">' + head + o.body + "</div></section>";
  }

  /* The heading of a page. */
  function pageHead(key) {
    var page = PAGES.filter(function (p) { return p.key === key; })[0];
    return '<div class="nm-head"><h1 class="nm-h1">' + esc(page.title) + '<span class="nm-cur" aria-hidden="true"></span></h1><p class="nm-sub">' + esc(page.sub) + "</p></div>";
  }

  /* A segmented control: options [[value, label]]. Clicks are handled by the page's one listener. */
  function seg(name, label, options, current) {
    return '<span class="nm-seg" role="group" aria-label="' + esc(label) + '">' + options.map(function (o) {
      var on = o[0] === current;
      return '<button type="button" data-seg="' + name + '" data-value="' + esc(o[0]) + '" data-fid="seg-' + name + "-" + esc(o[0]) + '"' +
        (on ? ' class="on"' : "") + ' aria-pressed="' + (on ? "true" : "false") + '">' + esc(o[1]) + "</button>";
    }).join("") + "</span>";
  }

  /* One figure with its one-line explanation. key picks the text in WHY and makes the ids unique on a page.
   * o: {sub, big, text (a date or a word, in a smaller face), bar (0..100 for a cells bar), cls}. */
  function statTile(key, label, value, o) {
    o = o || {};
    var why = WHY[key] || "";
    return '<div class="nm-stat' + (o.cls ? " " + o.cls : "") + '" role="group" tabindex="0" data-fid="stat-' + key + '" data-tip="' + esc(why) +
      '" title="' + esc(why) + '" aria-labelledby="stat-k-' + key + '" aria-describedby="why-' + key + '">' +
      '<div class="nm-stat-k" id="stat-k-' + key + '">' + esc(label) + "</div>" +
      '<div class="nm-stat-v' + (o.big ? " is-big" : "") + (o.text ? " is-text" : "") + '" style="--n:' + String(value).length + '">' + esc(value) + "</div>" +
      (o.sub ? '<div class="nm-stat-s">' + esc(o.sub) + "</div>" : "") +
      (o.bar !== undefined && o.bar !== null ? '<div class="nm-cells" aria-hidden="true">' + cellsHTML(20, o.bar) + "</div>" : "") +
      '<span class="nm-why nm-sr" id="why-' + key + '">' + esc(why) + "</span></div>";
  }

  /* A chart that is drawn after the page is in the DOM, at the width of the place it sits in.
   * build(width) returns the SVG; key is whatever the picture depends on (it decides when to redraw). The id is the card's
   * key and the chart's number in it, so the same card has the same markup on every render and is not replaced for nothing.
   * pending marks a chart drawn from placeholder data (its frame and nothing else; see the placeholder rules above). */
  function chartHost(build, key, cls, pending) {
    var id = S.chartCard + ":" + (++S.chartN);
    var digest = hashText(JSON.stringify(key));
    S.nextCharts[id] = { build: build, digest: digest };
    return '<div class="nm-chart-host' + (cls ? " " + cls : "") + '" data-chart="' + esc(id) + '" data-d="' + digest + '"' + (pending ? ' data-pending=""' : "") + "></div>";
  }

  function hydrateCharts(main) {
    if (!CHARTS) {
      return;
    }
    var pips = CHARTS.refresh();
    var hosts = main.querySelectorAll("[data-chart]");
    for (var i = 0; i < hosts.length; i++) {
      var host = hosts[i];
      var spec = S.charts[host.getAttribute("data-chart")];
      var width = Math.floor(host.clientWidth);
      if (!spec || width < 40) {
        continue;
      }
      var key = width + "|" + spec.digest + "|" + (pips ? "p" : "s");
      if (host.__built === key) {
        continue;
      }
      host.__built = key;
      try {
        host.innerHTML = spec.build(width);
      } catch (e) {
        host.innerHTML = '<p class="nm-error" role="status">This chart could not be drawn.</p>';
        if (window.console && console.error) {
          console.error("chart failed", e);
        }
      }
    }
  }

  function statusBanner() {
    var s = st();
    if (!s || s.status === "live") {
      return "";
    }
    var info = STATUS[s.status] || STATUS.loading;
    return '<div class="nm-msg" role="status"><b>' + esc(info.text) + "</b>" + esc(s.status_detail || "") + "</div>";
  }

  function hazardBand(sev) {
    return '<div class="nm-hazard' + (sev === "critical" ? "" : " is-off") + '" aria-hidden="true"></div>';
  }

  var RATIO_CELLS_MAX = 24;

  /* Months of the plan one cell stands for: 1 up to 24 months, then as many as keep the bar within 24 cells, so it
   * still grows with the number (30.5 months is 16 cells of 2 months, not 24 full cells of 1). */
  function ratioScale(ratio) {
    var r = num(ratio);
    return r === null || r <= RATIO_CELLS_MAX ? 1 : Math.ceil(r / RATIO_CELLS_MAX);
  }

  /* The caption under the bar: what one cell is. */
  function ratioNote(ratio, planName) {
    var step = ratioScale(ratio);
    return "1 cell = " + step + (step === 1 ? " month" : " months") + " of " + (planName ? planName + " plan" : "the plan");
  }

  /* cells: a length for the row when the multiple is not known yet (an empty track of that many cells). */
  function ratioCells(ratio, label, cells) {
    var step = ratioScale(ratio);
    var units = Math.max(num(ratio) || 0, 0) / step;
    var whole = Math.floor(units);
    var frac = units - whole;
    var n = cells ? clamp(cells, 1, RATIO_CELLS_MAX) : clamp(Math.ceil(units), 1, RATIO_CELLS_MAX);
    var out = "";
    for (var k = 0; k < n; k++) {
      if (k < whole) {
        out += '<i class="on"></i>';
      } else if (k === whole && frac > 0.02) {
        out += '<i class="part" style="--f:' + Math.round(frac * 100) + '%"></i>';
      } else {
        out += "<i></i>";
      }
    }
    var named = step > 1 ? label + ", 1 cell = " + step + " months" : label;
    return '<div class="nm-ratio" role="img" aria-label="' + esc(named) + '">' + out + "</div>";
  }

  /* A row of cells with a label and a value: the model mix and the pace meter use it. */
  function mixRow(label, percent, valueText, title) {
    return '<div class="nm-mrow"><span class="k"' + (title ? ' title="' + esc(title) + '"' : "") + ">" + esc(label) + '</span><div class="nm-cells" aria-hidden="true">' +
      cellsHTML(20, percent) + '</div><span class="v">' + esc(valueText) + "</span></div>";
  }

  // ── Placeholders: the shape of what has not arrived yet ─────────────────────────

  /* A page opens as a full set of cards. Where a report (or the monitor's state) has not arrived, the card is built from the
   * data below, which has the shape of the real thing and nothing in it: every label, frame and track is there, every figure is
   * zero. The card is then marked data-state="pending" and its figures, bars and charts data-pending (see markCard), so they are
   * invisible until the real numbers replace them in place. The counts of rows are what a typical account has. */

  function blankRows(n, make) {
    var out = [];
    for (var i = 0; i < n; i++) {
      out.push(make(i));
    }
    return out;
  }

  /* Now, held still for a minute: a placeholder's markup must be the same on every render until the data replaces it. */
  function stillNow() {
    var now = Date.now();
    return now - now % 60000;
  }

  /* The limits, the surface split and the credits as the monitor will send them. */
  function skelUsage() {
    var now = stillNow();
    function limit(kind, label, seconds, scope) {
      return {
        kind: kind, label: label, percent: 0, severity: "normal", window_seconds: seconds,
        resets_at: new Date(now + seconds * 500).toISOString(), scope_model: scope || null,
        allowance_pct: scope ? 50 : null, used_pct_of_weekly: scope ? 0 : null
      };
    }
    return {
      skeleton: true,
      limits: [limit("session", "SESSION", 18000), limit("weekly_all", "WEEKLY", 604800), limit("weekly_scoped", "SCOPED", 604800, "-")],
      breakdown: blankRows(3, function () { return { key: "", name: "", percent: 0 }; }),
      spend: null
    };
  }

  function skelWindow() {
    return {
      start: null, end: null, requests: 0, tokens: { input: 0, output: 0, cache_read: 0, cache_write: 0, thinking: 0, total: 0 },
      cost_usd: 0, nocache_cost_usd: 0, unpriced_requests: 0
    };
  }

  function skelBreakdown(n) {
    return blankRows(n, function () { return { model: "", project: "", folder: "", name: "", requests: 0, tokens: 0, cost_usd: 0, share: 0 }; });
  }

  /* The first of this month (offset 0) or of the next (1), as an ISO time: a placeholder billing cycle with the width of a real one. */
  function monthEdge(offset) {
    var d = new Date(stillNow());
    d.setDate(1);
    d.setHours(0, 0, 0, 0);
    d.setMonth(d.getMonth() + offset);
    return d.toISOString();
  }

  /* The cost report of any window. */
  function skelCost() {
    var windows = {};
    WINDOW_ORDER.forEach(function (name) { windows[name] = skelWindow(); });
    function thread() {
      // A typical request count, not zero: the tile's "97% · 2,670 requests" wraps in a narrow tile, and "0% · 0 requests" did
      // not, so the Main and subagents card grew a line when the report landed. The placeholder text is never shown.
      return { requests: 88888, tokens: 0, cost_usd: 0 };
    }
    return {
      skeleton: true, basis: BASIS, windows: windows,
      plan: { name: "", subscription_type: "", rate_limit_tier: "", price_usd: 100, price_source: "detected" },
      value: { cycle_start: monthEdge(0), cycle_end: monthEdge(1), cycle_cost_usd: 0, multiplier: 0, projected_cycle_cost_usd: 0 },
      monthly: blankRows(13, function () { return { month: "", requests: 0, tokens: 0, cost_usd: 0, multiplier: null }; }),
      breakdowns: {
        window: "cycle", by_model: skelBreakdown(4), by_project: skelBreakdown(6), by_folder: skelBreakdown(8),
        by_thread: { main: thread(), subagent: thread() }, haiku: { main: thread(), subagent: thread() }, fast: { requests: 0, cost_usd: 0 }
      },
      top_sessions_week: blankRows(5, function () {
        return { title: "", project: "", project_name: "", cost_usd: 0, requests: 0, models: [], prs: 0, subagent_cost_usd: 0 };
      }),
      spend: null
    };
  }

  function skelEfficiency() {
    return {
      skeleton: true, start: null, end: null, basis: BASIS,
      cache: { hit_rate: 0, fresh_input_tokens: 1, read_tokens: 0, write_tokens: 0, write_5m_tokens: 0, write_1h_tokens: 0, cost_usd: 0, nocache_cost_usd: 0, saved_usd: 0 },
      thinking: { thinking_tokens: 0, output_tokens: 0, share: 0 },
      context: { requests: 0, avg: 347400, p50: 337600, p95: 626100, max: 636800 },      // typical sizes, so the tiles are as tall as real ones
      compactions: { count: 0, auto: 0, manual: 0, per_active_day: null, avg_pre_tokens: null },
      active_days: 0,
      tools: {
        total: 0, errors: 0, error_rate: 0,
        builtin: blankRows(9, function () { return { name: "", count: 0 }; }),
        mcp: blankRows(2, function () { return { server: "", count: 0, tools: 0 }; })
      },
      productivity: { lines_added: 0, lines_removed: 0, files_created: 0, commits: 0, pushes: 0, prs: 0, cost_per_pr_usd: null, cost_per_100_lines_usd: null },
      effort: blankRows(4, function () { return { effort: "", requests: 0, cost_usd: 0 }; }),
      api_errors: 0, web: { searches: 0, fetches: 0 }
    };
  }

  /* n consecutive local days ending today, zero-filled. */
  function skelDays(n) {
    var out = [];
    var d = new Date(stillNow());
    d.setHours(0, 0, 0, 0);
    d.setDate(d.getDate() - (n - 1));
    for (var i = 0; i < n; i++) {
      out.push({ date: ymdOf(d), requests: 0, tokens: 0, output_tokens: 0, cost_usd: 0 });
      d.setDate(d.getDate() + 1);
    }
    return out;
  }

  function skelHistory() {
    var grid = function () { return blankRows(7, function () { return blankRows(24, function () { return 0; }); }); };
    var days = skelDays(371);
    return {
      skeleton: true, tz: "", days: days, hour_weekday: { requests: grid(), cost_usd: grid() },
      streaks: { current_days: 9, longest_days: 26, longest_start: "2026-05-20", longest_end: "2026-08-26" },   // two Wednesdays: the widest text a range has
      busiest_day: { date: days[days.length - 1].date, cost_usd: 0, tokens: 0 }, first_use: new Date(stillNow()).toISOString(), active_days: 0,
      odometer: { total_tokens: 0, output_tokens: 0, requests: 0, cost_usd: 0, comparisons: [] }
    };
  }

  /* The pacing block of a limit, before the pacing report has said anything about it. */
  function skelPacingBlock(limit) {
    var seconds = windowSeconds(limit);
    var reset = parseTime(limit.resets_at);
    var now = stillNow();
    return {
      skeleton: true, percent: num(limit.percent) || 0, resets_at: limit.resets_at || null,
      window_start: reset === null ? null : new Date(reset - seconds * 1000).toISOString(),
      slope_pct_per_hour: 8, projected_limit_at: null, verdict: "UNKNOWN", points: [],
      elapsed_pct: reset === null ? null : clamp((now - (reset - seconds * 1000)) / (seconds * 10), 0, 100),
      pace_delta_pp: -55.7, daily_budget_pct: 33.3, even_daily_pct: 100 / 7, projected_end_pct: 33.3
    };
  }

  // ── Parts of a page: cards that fill in place ───────────────────────────────────

  /* A page is a list of parts: the heading, the banners and the cards. Each has a key; the next render of the page is laid
   * over what is on screen part by part (paint), so a card whose markup has not changed is not touched, and a card whose
   * numbers have just arrived keeps its place and its frame and only gets new contents. A card says how it is doing in
   * data-state ("pending": its numbers have not arrived, "ready", "error": its report failed) and which reports it draws
   * from in data-needs (the keys of S.stats). The markup contract at the top of this file lists what the theme and the
   * motion layer can rely on. */

  function part(key, html, state) {
    return { key: key, html: html, state: state || "static", card: false, needs: [] };
  }

  /* A card (or anything that fills in like one): built under its key, tagged, and in place of its numbers shows the problem
   * when its report failed. needs: the stat entries it draws from. */
  function cardPart(key, state, needs, build) {
    S.chartCard = key;
    S.chartN = 0;
    var html = build();
    if (state === "error") {
      var failed = needs.filter(function (e) { return !e.data && e.error; })[0];
      html = errorSwap(html, failed ? statProblemShort(failed) : "Could not be loaded.");
    }
    var p = part(key, tagCard(html, key, state, needs), state);
    p.card = true;
    p.needs = needs;
    return p;
  }

  function tagCard(html, key, state, needs) {
    var attrs = ' data-card="' + esc(key) + '" data-state="' + state + '"' +
      (needs.length ? ' data-needs="' + esc(needs.map(function (e) { return e.key; }).join(" ")) + '"' : "") +
      (state === "pending" ? ' aria-busy="true"' : "");
    return html.replace(/^<(\w+)([^>]*)>/, function (all, tag, rest) {
      return "<" + tag + rest.replace(/\sdata-card="[^"]*"/, "") + attrs + ">";
    });
  }

  /* The card as it is with the problem in the place of its contents: the same frame and title, one line. */
  function errorSwap(html, message) {
    var open = /^<section\b[^>]*>/.exec(html);
    var head = /<div class="nm-card-t[^"]*">[\s\S]*?<\/div>/.exec(html);
    if (!open || !head) {
      return html;
    }
    return open[0] + '<div class="nm-card-in">' + head[0] + '<p class="nm-error" role="status">' + esc(message) + "</p></div></section>";
  }

  var FIGURES = ".nm-big, .nm-stat-v, .nm-stat-s, .nm-mrow .v, .nm-kv b, .nm-facts > span, .nm-pace > span, .nm-pace > b, .nm-pace > .fc, " +
    ".nm-story, .nm-table td.r, .nm-table td.nm-trunc, .nm-odo-facts b, .nm-cmp-n, .nm-toolbar-n";
  var BARS = ".nm-cells, .nm-ratio";
  var CHARTS_SEL = ".nm-chart-host, .cum-spark, .cum-stack, .nm-odo";

  function textOf(el) {
    return String(el.textContent || "").replace(/\s+/g, " ").trim();
  }

  /* How full a bar of cells is, 0 to 100. */
  function barFill(bar) {
    var cells = bar.children;
    var lit = 0;
    for (var i = 0; i < cells.length; i++) {
      var cls = cells[i].className;
      if (cls === "on") {
        lit += 1;
      } else if (cls === "part") {
        lit += (parseFloat(cells[i].style.getPropertyValue("--f")) || 0) / 100;
      }
    }
    return cells.length ? Math.round(lit / cells.length * 1000) / 10 : 0;
  }

  /* Puts the marks on a card the theme and the motion layer read. A card that waits for its numbers has every figure, bar
   * and chart in it marked data-pending (aria-hidden, since there is nothing to read yet); a card with its numbers has
   * every figure marked with its final text (data-final) and every bar with its fill (data-fill, 0 to 100). */
  function markCard(el) {
    var pending = el.getAttribute("data-state") === "pending";
    var figures = el.querySelectorAll(FIGURES);
    var bars = el.querySelectorAll(BARS);
    var charts = el.querySelectorAll(CHARTS_SEL);
    var i;
    for (i = 0; i < figures.length; i++) {
      if (pending) {
        figures[i].setAttribute("data-pending", "");
      } else if (!figures[i].hasAttribute("data-pending")) {
        figures[i].setAttribute("data-final", textOf(figures[i]));
      }
    }
    for (i = 0; i < bars.length; i++) {
      if (pending) {
        bars[i].setAttribute("data-pending", "");
      } else if (!bars[i].hasAttribute("data-pending")) {
        bars[i].setAttribute("data-fill", String(barFill(bars[i])));
      }
    }
    for (i = 0; i < charts.length; i++) {
      if (pending) {
        charts[i].setAttribute("data-pending", "");
      }
    }
    var marked = el.querySelectorAll("[data-pending]");
    for (i = 0; i < marked.length; i++) {
      marked[i].setAttribute("aria-hidden", "true");
    }
  }

  /* What a card shows now, to tell afterwards which of its figures and bars changed. */
  function snapshotCard(el) {
    var figures = el.querySelectorAll(FIGURES);
    var bars = el.querySelectorAll(BARS);
    var out = { figures: [], bars: [], pending: el.querySelectorAll("[data-pending]").length };
    var i;
    for (i = 0; i < figures.length; i++) {
      out.figures.push(figures[i].hasAttribute("data-pending") ? null : textOf(figures[i]));
    }
    for (i = 0; i < bars.length; i++) {
      out.bars.push(bars[i].hasAttribute("data-pending") ? null : bars[i].getAttribute("data-fill"));
    }
    return out;
  }

  /* Marks the figures and bars that differ from the snapshot data-fresh, and says how many there are. A figure or bar that
   * was a placeholder is fresh; one that shows what it showed before is not, so it is not animated again. */
  function markFresh(el, before) {
    var figures = el.querySelectorAll(FIGURES);
    var bars = el.querySelectorAll(BARS);
    var fresh = 0;
    var i;
    for (i = 0; i < figures.length; i++) {
      if (!figures[i].hasAttribute("data-pending") && before.figures[i] !== textOf(figures[i])) {
        figures[i].setAttribute("data-fresh", "");
        fresh++;
      }
    }
    for (i = 0; i < bars.length; i++) {
      if (!bars[i].hasAttribute("data-pending") && before.bars[i] !== bars[i].getAttribute("data-fill")) {
        bars[i].setAttribute("data-fresh", "");
        fresh++;
      }
    }
    return fresh;
  }

  function parsePart(html) {
    var box = document.createElement("template");
    box.innerHTML = html;
    return box.content.firstElementChild;
  }

  /* Makes the attributes of el those of fresh, except the ones other code owns: the inline style and the data-fx marks of the
   * motion layer, the updating cue (syncUpdating) and any fx- class. */
  function patchShell(el, fresh) {
    var want = {};
    var i;
    for (i = 0; i < fresh.attributes.length; i++) {
      want[fresh.attributes[i].name] = fresh.attributes[i].value;
    }
    var kept = String(el.className || "").split(/\s+/).filter(function (c) { return /^fx-/.test(c); });
    want["class"] = (want["class"] ? want["class"] + " " : "") + kept.join(" ");
    for (i = el.attributes.length - 1; i >= 0; i--) {
      var name = el.attributes[i].name;
      if (!(name in want) && !/^(style|data-fx|data-updating)/.test(name)) {
        el.removeAttribute(name);
      }
    }
    Object.keys(want).forEach(function (name) {
      var value = String(want[name]).trim();
      if (el.getAttribute(name) !== value) {
        el.setAttribute(name, value);
      }
    });
  }

  function placeAfter(container, el, prev) {
    var next = prev ? prev.nextSibling : container.firstChild;
    if (next !== el) {
      container.insertBefore(el, next);
    }
  }

  /* Lays parts over the children of container: a part that is not there yet is added, one whose markup changed gets its
   * contents replaced (its frame stays, with whatever the motion layer put on it), one that is the same is left alone, and a
   * child that is no longer a part goes. ctx collects what changed, for the hooks. */
  function reconcile(container, parts, ctx, force) {
    var olds = {};
    var i;
    for (i = 0; i < container.children.length; i++) {
      if (container.children[i].__key) {
        olds[container.children[i].__key] = container.children[i];
      }
    }
    var prev = null;
    parts.forEach(function (p) {
      var el = olds[p.key];
      delete olds[p.key];
      if (p.grid) {
        if (!el) {
          el = document.createElement("div");
          el.__key = p.key;
        }
        if (el.className !== p.cls) {
          el.className = p.cls;
        }
        placeAfter(container, el, prev);
        reconcile(el, p.parts, ctx, force);
      } else if (!el) {
        el = parsePart(p.html);
        el.__key = p.key;
        el.__src = p.html;
        placeAfter(container, el, prev);
        if (p.card) {
          markCard(el);
          ctx.added.push({ el: el, key: p.key, state: p.state });
        }
      } else {
        if (el.__src !== p.html || force) {
          patchPart(el, p, ctx);
        }
        placeAfter(container, el, prev);
      }
      prev = el;
    });
    Object.keys(olds).forEach(function (k) {
      container.removeChild(olds[k]);
    });
  }

  function patchPart(el, p, ctx) {
    var fresh = parsePart(p.html);
    var from = el.getAttribute("data-state");
    var before = p.card ? snapshotCard(el) : null;
    if (S.tipApi && (el.matches(":hover") || el.contains(document.activeElement))) {
      S.tipApi.hide();
    }
    patchShell(el, fresh);
    replaceHtml(el, fresh.innerHTML);
    el.__src = p.html;
    if (!p.card) {
      return;
    }
    markCard(el);
    var freshCount = markFresh(el, before);
    var left = el.querySelectorAll("[data-pending]").length;
    if (freshCount || left !== before.pending || from !== p.state) {
      ctx.updated.push({
        el: el, key: p.key, from: from, to: p.state, fresh: freshCount,
        first: before.pending > left || (from === "pending" && p.state !== "pending")
      });
    }
  }

  /* Draws a page model on #main. force replaces every part's contents whatever changed (a new theme). */
  function paint(main, model, force) {
    var ctx = { updated: [], added: [] };
    var parts = model.top.filter(function (p) { return p.html; });
    parts.push({ key: "grid", grid: true, cls: model.cls, parts: model.cards });
    reconcile(main, parts, ctx, force);
    return ctx;
  }

  function dispatch(target, name, detail) {
    try {
      target.dispatchEvent(new window.CustomEvent(name, { bubbles: true, detail: detail }));
    } catch (e) {
      /* an engine without CustomEvent: the theme's own hooks have already run */
    }
  }

  function guarded(label, fn) {
    try {
      fn();
    } catch (e) {
      if (window.console && console.error) {
        console.error(label + " failed", e);
      }
    }
  }

  /* Tells the theme and the motion layer what a paint did: for every card whose numbers arrived (or changed), the theme's
   * arrive(card, info) and a cum:card-data event on the card; for the page, a cum:page-rendered event on #main. */
  function announce(main, key, opened, ctx) {
    var fx = themeFx();
    var reduced = reduceMotion();
    var entering = main.classList.contains("fx-run");
    ctx.updated.forEach(function (u) {
      var info = { page: key, card: u.key, first: u.first, from: u.from, to: u.to, fresh: u.fresh, entering: entering, reduced: reduced };
      if (fx && typeof fx.arrive === "function" && !reduced) {
        guarded("arrive", function () { fx.arrive(u.el, info); });
      }
      dispatch(u.el, "cum:card-data", info);
    });
    dispatch(main, "cum:page-rendered", {
      page: key, opened: opened, reduced: reduced,
      updated: ctx.updated.map(function (u) { return u.key; }), added: ctx.added.map(function (a) { return a.key; })
    });
  }

  // ── The theme's motion hooks ────────────────────────────────────────────────────

  function themeFx() {
    var t = S.lastTheme;
    return t && window.CUM_THEME_FX ? window.CUM_THEME_FX[t.id] : null;
  }

  /* The theme's own stylesheet AND script are in. Changing the link's href does not clear link.sheet (Chromium keeps the old
   * sheet there until the new one is in), so the sheet's URL says whose CSS it is. loadThemeScript() leaves "loading" in
   * S.loadedScripts[id] until the script has run. */
  function themeReady() {
    var t = S.lastTheme;
    var link = document.getElementById("theme-css");
    var sheet = link && link.sheet;
    return !!(t && sheet && sheet.href && sheet.href.indexOf("/themes/" + encodeURIComponent(t.id) + ".css") >= 0 &&
      S.loadedScripts[t.id] !== "loading");
  }

  /* The page is due an entrance: the first page, a page change, a theme change. */
  function wantEnter() {
    S.enterPending = true;
  }

  /* The entrance plays when it is due, the monitor has answered, the theme's files are in and the window is shown. It does not
   * wait for the reports: a page is made of placeholders from the first frame, and each card's numbers arrive in place later
   * (the theme's arrive hook). Every "not yet" returns with the flag still set, so the next render, the script's load or the
   * window being shown asks again; every "never" clears it, so a stale flag cannot play an entrance out of the blue. */
  function playEnterIfDue(main, key) {
    if (!S.enterPending || !S.data || !themeReady() || document.visibilityState === "hidden") {
      return;
    }
    S.enterPending = false;
    var fx = themeFx();
    if (!fx || typeof fx.enter !== "function" || reduceMotion()) {
      return;
    }
    guarded("enter", function () { fx.enter(main, key); });
  }

  /* Finishes whatever the theme's motion is doing to the page. */
  function cancelFx(main) {
    var fx = themeFx();
    if (main && fx && typeof fx.cancel === "function") {
      guarded("cancel", function () { fx.cancel(main); });
    }
  }

  // ── Overview ────────────────────────────────────────────────────────────────────

  /* The plan the sign-in detected, with the price the plan costs (never the override). */
  function planFromState() {
    var s = st();
    return s && s.plan ? { name: s.plan.name, price: num(s.plan.monthly_price_usd) } : { name: "", price: null };
  }

  /* The price the cost reports divide by: the override when the user set one, else the detected price. */
  function effectivePlanPrice() {
    var override = num(S.settings && S.settings.plan_price_override);
    return override !== null ? override : planFromState().price;
  }

  function topModels(month, count) {
    var rows = month && month.by_model ? Object.keys(month.by_model).map(function (id) {
      return { id: id, cost: num(month.by_model[id].cost_usd) || 0, tokens: num(month.by_model[id].total_tokens) || 0 };
    }) : [];
    rows.sort(function (a, b) { return b.cost - a.cost || b.tokens - a.tokens; });
    var totalCost = rows.reduce(function (a, r) { return a + r.cost; }, 0);
    var totalTokens = rows.reduce(function (a, r) { return a + r.tokens; }, 0);
    return rows.slice(0, count).map(function (r) {
      var share = totalCost > 0 ? r.cost / totalCost * 100 : (totalTokens > 0 ? r.tokens / totalTokens * 100 : 0);
      return { id: r.id, name: modelName(r.id), share: share };
    });
  }

  /* The cost figures the overview shows: from the cost report once it is in, from the data service's own
   * windows until then (the same windows, so the numbers do not jump when the report arrives). */
  function costNumbers() {
    var entry = statEntry("cost", { window: "cycle" });
    var d = entry.data;
    var out = { loaded: !!d, pending: false, entry: entry, cycle: null, today: null, week: null, saved: null, hit: null, thinking: null,
      multiplier: null, projected: null, planName: "", price: null, cycleStart: null, cycleEnd: null, models: [] };
    if (d) {
      var c = d.windows.cycle, tk = c.tokens || {};
      var prompt = (tk.input || 0) + (tk.cache_read || 0) + (tk.cache_write || 0);
      out.cycle = num(c.cost_usd);
      out.today = num(d.windows.today.cost_usd);
      out.week = num(d.windows.week.cost_usd);
      out.saved = Math.max(0, (num(c.nocache_cost_usd) || 0) - (num(c.cost_usd) || 0));
      out.hit = prompt > 0 ? tk.cache_read / prompt : null;
      out.thinking = tk.output > 0 ? tk.thinking / tk.output : null;
      out.multiplier = num(d.value.multiplier);
      out.projected = num(d.value.projected_cycle_cost_usd);
      out.planName = d.plan.name;
      out.price = num(d.plan.price_usd);
      out.cycleStart = d.value.cycle_start;
      out.cycleEnd = d.value.cycle_end;
      out.models = d.breakdowns.by_model.slice(0, 4).map(function (m) { return { id: m.model, name: m.name, share: m.share * 100 }; });
      return out;
    }
    var plan = planFromState();
    var month = windowOf("month"), today = windowOf("today"), week = windowOf("week");
    var calendarCycle = S.settings.billing_day === null || S.settings.billing_day === undefined;
    out.planName = plan.name;
    out.price = effectivePlanPrice();
    if (month && calendarCycle) {
      out.cycle = num(month.totals.cost_usd);
      out.saved = num(month.totals.caching_saved_usd);
      out.hit = num(month.totals.cache_hit_rate);
      out.thinking = month.totals.output > 0 ? month.totals.thinking / month.totals.output : null;
      out.models = topModels(month, 4);
      out.multiplier = out.cycle !== null && out.price ? out.cycle / out.price : null;
    }
    out.today = today ? num(today.totals.cost_usd) : null;
    out.week = week ? num(week.totals.cost_usd) : null;
    if (out.cycle === null) {
      // Nothing to show yet (no report, and the monitor has no month to stand in): a placeholder of the right shape.
      out.pending = true;
      out.cycle = 0;
      out.today = 0;
      out.week = 0;
      out.saved = 0;
      out.hit = 0;
      out.thinking = 0;
      out.multiplier = 0;
      out.price = out.price === null ? 100 : out.price;              // the plan the monitor knows, if it knows one
      out.planName = out.planName || "PLAN";
      out.models = blankRows(4, function () { return { id: "", name: "", share: 0 }; });
    }
    return out;
  }

  function rangeText(startIso, endIso) {
    var a = parseTime(startIso), b = parseTime(endIso);
    if (a === null || b === null) {
      return "";
    }
    var da = new Date(a), db = new Date(b);
    return MONTHS[da.getMonth()] + " " + da.getDate() + " - " + MONTHS[db.getMonth()] + " " + db.getDate();
  }

  /* The last 30 days of cost as columns (one pip is a round dollar amount), with the average as a dashed line. */
  function dailyChart(cycleStart) {
    var he = statEntry("history");
    var d = he.data;
    if (!d && he.error) {
      return '<div class="nm-card-t nm-chart-t"><span class="nm-ttl">DAILY &middot; LAST 30 DAYS</span><span class="nm-meta-t">&nbsp;</span></div>' +
        '<div class="nm-chart"><p class="nm-pending">Daily history is not available yet.</p></div>';
    }
    var waiting = !d;
    if (waiting) {
      d = skelHistory();
    }
    var days = d.days.slice(-30);
    var costs = days.map(function (x) { return num(x.cost_usd) || 0; });
    var max = Math.max.apply(null, costs.concat([0]));
    var avg = costs.length ? costs.reduce(function (a, v) { return a + v; }, 0) / costs.length : 0;
    var step = CHARTS.niceStep(max / 14);
    var pipsOn = CHARTS.refresh();
    var meta = max <= 0 ? "NO USE YET" : pipsOn ? "1 PIP = " + fmtPlanPrice(step) : "TOP " + fmtUsdShort(max);
    var labels = [{ i: days.length - 1, text: "TODAY" }];
    if (cycleStart) {
      var cs = ymdOf(new Date(parseTime(cycleStart)));
      days.forEach(function (x, i) { if (x.date === cs && i > 0) { labels.push({ i: i, text: dayLabel(x.date) }); } });
    }
    labels.push({ i: 0, text: dayLabel(days[0] && days[0].date) });
    var tips = days.map(function (x) {
      return dayText(x.date) + ": " + fmtUsd(x.cost_usd) + " API-equivalent, " + plural(x.requests, "request");
    });
    var avgText = avg >= 1000 ? fmtUsdShort(avg) : avg >= 10 ? "$" + Math.round(avg) : fmtUsd(avg);
    var host = chartHost(function (w) {
      return CHARTS.columns(costs, {
        width: w, height: 132, gutterRight: 68, step: step, labels: labels, tips: tips, current: days.length - 1,
        avg: { value: avg, label: "AVG " + avgText },
        label: "Daily API-equivalent cost, last 30 days, average " + fmtUsd(avg) + " a day"
      });
    }, [he.digest, pipsOn, cycleStart], "nm-daily", waiting);
    return '<div class="nm-card-t nm-chart-t"><span class="nm-ttl">DAILY &middot; LAST 30 DAYS</span><span class="nm-meta-t"' + (waiting ? ' data-pending=""' : "") + ">" + esc(waiting ? "TOP $00.00" : meta) + "</span></div>" +
      '<div class="nm-chart">' + host + "</div>";
  }

  function dayLabel(ymd) {
    var d = parseYmd(ymd);
    return d ? MONTHS[d.getMonth()] + " " + d.getDate() : "";
  }

  function costCard(n) {
    var heroText = fmtUsdHero(n.cycle);
    var planBlock;
    if (n.price === null) {
      planBlock = '<div class="nm-plan"><div class="nm-big m">--</div><div class="nm-facts"><span>PLAN PRICE UNKNOWN</span><span>SET IT IN SETTINGS</span></div></div>';
    } else {
      var proj = n.projected !== null ? "At this pace the cycle ends near " + fmtUsd(n.projected) + " (" + fmtMult(n.projected / n.price) + ")." : "";
      planBlock = '<div class="nm-plan"><div class="nm-big m"' + (proj ? ' title="' + esc(proj) + '"' : "") + ">" + esc(fmtMult(n.multiplier)) + '</div><div class="nm-facts"><span>YOUR PLAN</span><span>' +
        esc((n.planName || "PLAN").toUpperCase()) + " &middot; " + esc(fmtPlanPrice(n.price)) + " / MONTH</span></div></div>";
    }
    var ratio = n.multiplier;
    var note = n.price === null
      ? "Set the plan price in Settings to see the multiple."
      : esc(ratioNote(ratio, n.planName));
    var title = "API-equivalent cost, this cycle";
    var meta = (n.cycleStart ? rangeText(n.cycleStart, n.cycleEnd) + " &middot; " : "") + "AT LIST PRICES";
    return '<section class="nm-card wide" id="cost" aria-label="' + esc(title) + '"><div class="nm-card-in">' +
      '<div class="nm-card-t"><h2 class="nm-ttl">' + esc(title) + '</h2><span class="nm-meta-t">' + meta + "</span></div>" +
      '<div class="nm-cost"><div class="nm-cost-l"><div class="nm-hero"><div class="nm-big h" style="--n:' + heroText.length + '">' + esc(heroText) + "</div>" + planBlock + "</div>" +
      (ratio === null || n.price === null ? "" : ratioCells(ratio, fmtMult(ratio) + " months of the plan", n.pending ? 12 : 0)) +
      '<div class="nm-note">' + note + "</div>" +
      '<div class="nm-note nm-disc">' + BASIS + ", not amounts billed to your plan.</div>" +
      '<div class="nm-tiles"><div class="nm-tile"><small>Today</small><div class="nm-big m">' + esc(fmtUsdTile(n.today)) +
      '</div></div><div class="nm-tile"><small>7 days</small><div class="nm-big m">' + esc(fmtUsdTile(n.week)) + "</div></div></div></div>" +
      '<div class="nm-cost-r">' + dailyChart(n.cycleStart) + "</div>" +
      "</div></div></section>";
  }

  function efficiencyCard(n) {
    var hit = n.hit === null ? null : n.hit * 100;
    var thinking = n.thinking === null ? null : n.thinking * 100;
    return '<section class="nm-card" id="efficiency" aria-label="Cache and thinking"><div class="nm-card-in">' +
      '<div class="nm-card-t"><h2 class="nm-ttl">Cache &amp; thinking</h2><span class="nm-meta-t">' + (n.loaded ? "this cycle" : "this month") + "</span></div>" +
      '<div class="nm-kpi"><div class="nm-kl">Cache hit rate</div><div class="nm-big m">' + esc(hit === null ? "--" : hit.toFixed(1) + "%") +
      '</div><div class="nm-cells" aria-hidden="true">' + cellsHTML(20, hit || 0) + "</div></div>" +
      '<div class="nm-kpi"><div class="nm-kl">Thinking share of output</div><div class="nm-big m">' + esc(thinking === null ? "--" : fmtPct(thinking)) +
      '</div><div class="nm-cells" aria-hidden="true">' + cellsHTML(20, thinking || 0) + "</div></div>" +
      '<div class="nm-kpi is-row"><div class="nm-kl">Caching saved</div><div class="nm-big s" title="What the same tokens would cost with no caching, minus what they cost">' + esc(fmtUsdShort(n.saved)) + "</div></div>" +
      "</div></section>";
  }

  function modelMixCard(n) {
    var rows = n.models;
    var body = rows.length
      ? rows.map(function (r) {
        return '<div class="nm-mrow"><span class="k" title="' + esc(r.id) + '">' + esc(r.name.toUpperCase()) + '</span><div class="nm-cells" aria-hidden="true">' +
          cellsHTML(20, r.share) + '</div><span class="v">' + esc(fmtPct(r.share)) + "</span></div>";
      }).join("")
      : '<p class="nm-empty">No model mix yet.</p>';
    return '<section class="nm-card" id="models" aria-label="Model mix"><div class="nm-card-in"><div class="nm-card-t"><h2 class="nm-ttl">Model mix</h2><span class="nm-meta-t">share of cost, ' + (n.loaded ? "cycle" : "month") + "</span></div>" +
      '<div class="nm-mix">' + body + "</div></div></section>";
  }

  function surfaceCard(u) {
    var rows = u && u.breakdown ? u.breakdown : [];
    var body = rows.length
      ? rows.map(function (r) {
        return '<div class="nm-mrow"><span class="k">' + esc(String(r.name || r.key || "").toUpperCase()) + '</span><div class="nm-cells" aria-hidden="true">' +
          cellsHTML(20, r.percent) + '</div><span class="v">' + esc(fmtPct(r.percent)) + "</span></div>";
      }).join("")
      : '<p class="nm-empty">No surface split reported yet.</p>';
    return '<section class="nm-card" aria-label="Where the week went"><div class="nm-card-in"><div class="nm-card-t"><h2 class="nm-ttl">Where the week went</h2><span class="nm-meta-t">share, 7 days</span></div>' +
      '<div class="nm-mix">' + body + "</div></div></section>";
  }

  function burnCard(s) {
    var burn = s && s.burn ? s.burn : null;
    var velocity = burn ? String(burn.velocity || "IDLE") : "IDLE";
    // Until two samples are in (one every poll), the trace is its frame alone.
    var spark = S.samples.length > 1
      ? CHARTS.sparkline(S.samples, { width: 300, height: 58, label: "Burn rate, recent samples" })
      : CHARTS.sparkline([], { width: 300, height: 58, label: "Burn rate, sampling" }).replace("<svg ", '<svg data-pending="" ');
    return '<section class="nm-card" id="burn" aria-label="Burn rate"><div class="nm-card-in"><div class="nm-card-t"><h2 class="nm-ttl">Burn rate</h2><span class="nm-chip">' + esc(velocity) + "</span></div>" +
      '<div class="nm-row2 is-base"><div class="nm-big m">' + esc(burn ? fmtInt(burn.tokens_per_min) : "--") +
      '</div><div class="nm-facts"><span>TOK / MIN</span><span>' + esc(burn ? fmtUsd(burn.usd_per_hour) : "--") + " / HOUR</span></div></div>" +
      spark + "</div></section>";
  }

  /* The page's heading, its banners and its cards. cls is the grid the cards sit in (nm-grid, or nm-pgrid for every
   * page but the overview). o: {hazard, update, problem, stale} are the banners that may sit between the heading and the grid.
   * The update banner is on every page (the toast says "Open the tray menu or the dashboard to update", and the dashboard may
   * be on any page); it is empty while no release waits, so a page passes `update` only to replace it. */
  function pageModel(key, cls, cards, o) {
    o = o || {};
    return {
      key: key, cls: cls, cards: cards,
      top: [part("hazard", o.hazard || ""), part("head", pageHead(key)), part("update", o.update !== undefined ? o.update : updateBanner()),
        part("status", statusBanner()), part("problem", o.problem || ""), part("stale", o.stale || "")]
    };
  }

  function overviewPage() {
    var s = st();
    var u = usage();
    var pe = statEntry("pacing");
    var pd = pe.data;
    var waiting = s ? "ready" : "pending";            // the monitor's own numbers: ready as soon as it has sent a state
    var forecast = !pd && !pe.error;                  // the forecast line is a placeholder until the pacing report answers
    var session = limitOf("session");
    var weekly = limitOf("weekly_all");
    var scoped = scopedLimits();
    var cards = [];
    cards.push(cardPart("limit-session", waiting, [pe], function () {
      return session ? limitCard(session, { meta: "5-hour window", fc: pd ? sessionForecast(pd.session) : "", fcPending: forecast }) : emptyLimitCard("SESSION", "5-hour window");
    }));
    cards.push(cardPart("limit-weekly", waiting, [pe], function () {
      return weekly ? limitCard(weekly, { meta: "7-day window", fc: pd ? weeklyForecast(pd.weekly) : "", fcPending: forecast }) : emptyLimitCard("WEEKLY", "7-day window");
    }));
    cards.push(cardPart("limit-scoped", waiting, [pe], function () {
      if (!scoped.length) {
        return emptyLimitCard("SCOPED", "No scoped limit reported");
      }
      var first = scoped[0];
      return limitCard(first, { meta: "7-day window", share: num(first.allowance_pct), fc: pd ? weeklyForecast(matchScoped(pd.scoped, first, 0)) : "", fcPending: forecast });
    }));
    var n = costNumbers();
    var he = statEntry("history");
    // Nothing to show yet (n.pending): the cost report is on its way, or it failed and the monitor has no month of its own to
    // stand in (a billing day is set). The three cards that draw from it say which, as they do on the Cost page.
    var costState = n.pending ? entryState([n.entry]) : "ready";
    cards.push(cardPart("cost", costState, [n.entry, he], function () { return costCard(n); }));
    cards.push(cardPart("efficiency", costState, [n.entry], function () { return efficiencyCard(n); }));
    cards.push(cardPart("models", costState, [n.entry], function () { return modelMixCard(n); }));
    cards.push(cardPart("surfaces", waiting, [], function () { return surfaceCard(u); }));
    cards.push(cardPart("burn", waiting, [], function () { return burnCard(s); }));
    return pageModel("overview", "nm-grid", cards, {
      hazard: hazardBand(worstSeverity(limits())), problem: n.pending ? problemBanner([n.entry]) : "", stale: staleBanner([n.entry, pe, he])
    });
  }

  // ── Limits: pacing ──────────────────────────────────────────────────────────────

  var SESSION_VERDICT = { LIMITED: "LIMITED", AT_RISK: "AT RISK", SAFE: "SAFE", IDLE: "IDLE", UNKNOWN: "UNKNOWN" };
  var WEEKLY_VERDICT = { AHEAD: "AHEAD", ON_PACE: "ON PACE", BEHIND: "BEHIND", UNKNOWN: "UNKNOWN" };

  /* Rows of a label and a value: [[label, value, pending]]. A row marked pending shows a placeholder where its value will be. */
  function kvGrid(pairs, cls) {
    return '<div class="nm-kv-grid' + (cls ? " " + cls : "") + '">' + pairs.map(function (row) {
      return '<div class="nm-kv"><span>' + esc(row[0]) + "</span><b" + (row[2] ? ' data-pending=""' : "") + ">" + esc(row[1]) + "</b></div>";
    }).join("") + "</div>";
  }

  function signed(n, digits) {
    var v = num(n);
    if (v === null) {
      return "--";
    }
    return (v > 0 ? "+" : v < 0 ? "-" : "") + Math.abs(v).toFixed(digits === undefined ? 1 : digits);
  }

  function sessionStory(blk) {
    var v = blk && SESSION_VERDICT[blk.verdict] ? blk.verdict : "UNKNOWN";
    var reset = blk ? parseTime(blk.resets_at) : null;
    var text;
    if (v === "LIMITED") {
      text = "The whole 5-hour allowance is used. It resets " + clockText(reset) + ".";
    } else if (v === "AT_RISK") {
      var hit = parseTime(blk.projected_limit_at);
      text = "At the current rate you reach the limit around " + clockText(hit) + ", before the window resets at " + clockText(reset) + ".";
    } else if (v === "SAFE") {
      text = "At the current rate you stay under the limit until the window resets at " + clockText(reset) + ".";
    } else if (v === "IDLE") {
      text = "Almost no draw in the last 90 minutes, so the limit is not in reach.";
    } else {
      text = "A forecast needs two readings at least 10 minutes apart. It appears as soon as there are two.";
    }
    return { chip: SESSION_VERDICT[v], tone: v === "LIMITED" ? " is-crit" : v === "AT_RISK" ? " is-warn" : "", text: text };
  }

  function weeklyStory(blk) {
    var v = blk && WEEKLY_VERDICT[blk.verdict] ? blk.verdict : "UNKNOWN";
    var delta = blk ? num(blk.pace_delta_pp) : null;
    var text;
    if (v === "AHEAD") {
      text = "You are using this window " + Math.abs(delta).toFixed(1) + " points faster than an even pace.";
    } else if (v === "BEHIND") {
      text = "You are " + Math.abs(delta).toFixed(1) + " points under an even pace, so there is room to spare.";
    } else if (v === "ON_PACE") {
      text = "You are keeping step with the clock.";
    } else {
      text = "No window reported for this limit yet.";
    }
    return { chip: WEEKLY_VERDICT[v], tone: v === "AHEAD" ? " is-warn" : "", text: text };
  }

  /* Time elapsed against usage: the gap between the two bars is the pace. */
  function meterHTML(elapsedPct, usedPct) {
    var label = "Time elapsed " + fmtPct(elapsedPct, 1) + ", used " + fmtPct(usedPct, 1);
    return '<div class="nm-meter" role="img" aria-label="' + esc(label) + '">' +
      mixRow("Time elapsed", elapsedPct, fmtPct(elapsedPct)) + mixRow("Used", usedPct, fmtPct(usedPct)) + "</div>";
  }

  function hourTicks(t0, t1) {
    var out = [];
    var d = new Date(t0);
    d.setMinutes(0, 0, 0);
    d.setHours(d.getHours() + 1);
    for (var t = d.getTime(); t < t1 && out.length < 12; t += 3600000) {
      out.push({ t: t, label: hourText(t) });
    }
    return out;
  }

  function dayTicks(t0, t1) {
    var out = [];
    var d = new Date(t0);
    d.setHours(24, 0, 0, 0);
    while (d.getTime() < t1 && out.length < 9) {
      out.push({ t: d.getTime(), label: DAYS[d.getDay()] });
      d.setDate(d.getDate() + 1);
    }
    return out;
  }

  /* The forecast of a weekly or scoped window, drawn from the average rate so far: from now to the moment it reaches 100 % (that
   * moment is marked) or, if it stays under 100 %, to the reset. The line's value at the reset is the pacing report's
   * projected_end_pct, the figure the cards print as "Projected end", so the line and the figure cannot disagree.
   * Returns {points, reaches (ms, or null when it stays under), end} or null when there is nothing to draw: no window, the
   * limit is already reached, or the report has no projection yet (it needs 5 % of the window). */
  function averageRateForecast(blk, nowMs) {
    var pct = num(blk.percent);
    var end = num(blk.projected_end_pct);
    var t1 = parseTime(blk.resets_at);
    if (pct === null || end === null || t1 === null || nowMs >= t1 || pct >= 100) {
      return null;
    }
    if (end >= 100) {
      var reaches = nowMs + (100 - pct) / (end - pct) * (t1 - nowMs);
      return { points: [[nowMs, pct], [reaches, 100]], reaches: reaches, end: end };
    }
    return { points: [[nowMs, pct], [t1, end]], reaches: null, end: end };
  }

  /* The readings in a window as a line with markers, the even-pace diagonal and the forecast. pending draws the frame alone
   * (the axes, the clock and the diagonal), for a card whose pacing report has not answered. */
  function pacingChart(blk, kind, nowMs, digest, pending) {
    var t0 = parseTime(blk.window_start), t1 = parseTime(blk.resets_at);
    if (t0 === null || t1 === null || t1 <= t0) {
      return "";
    }
    var pts = (blk.points || []).map(function (p) { return [parseTime(p[0]), num(p[1])]; })
      .filter(function (p) { return p[0] !== null && p[1] !== null; });
    var pct = num(blk.percent);
    var top = Math.max.apply(null, pts.map(function (p) { return p[1]; }).concat([pct || 0, 100]));
    var yMax = Math.ceil(top / 10) * 10;
    var yTicks = [0, 25, 50, 75, 100].map(function (v) { return { v: v, label: v + "%", limit: v === 100 }; });
    if (yMax > 100) {
      yTicks.push({ v: yMax, label: yMax + "%" });
    }
    var series = [];
    if (kind !== "session") {
      series.push({ kind: "ideal", points: [[t0, 0], [t1, 100]] });
    }
    var forecast = null;                 // {points, reaches}
    if (!pending && kind === "session" && pct !== null) {
      var limitAt = blk.projected_limit_at ? parseTime(blk.projected_limit_at) : null;
      if (limitAt !== null) {
        forecast = { points: [[nowMs, pct], [limitAt, 100]], reaches: limitAt };
      } else if (num(blk.slope_pct_per_hour) !== null && blk.verdict !== "LIMITED") {
        var left = Math.max(0, (t1 - nowMs) / 3600000);
        forecast = { points: [[nowMs, pct], [t1, Math.min(100, pct + Math.max(0, blk.slope_pct_per_hour) * left)]], reaches: null };
      }
    } else if (!pending && kind !== "session") {
      forecast = averageRateForecast(blk, nowMs);
    }
    var forecastText = "";
    if (forecast) {
      var at = forecast.reaches === null ? "" : clockText(forecast.reaches);
      forecastText = forecast.reaches === null ? "" : "Reaches 100% about " + at + " at this rate";
      series.push({
        kind: "forecast", points: forecast.points,
        endLabel: forecast.reaches === null ? "" : "100% ~ " + at, endTip: forecastText
      });
    }
    var every = Math.max(1, Math.ceil(pts.length / 24));
    var marks = [];
    pts.forEach(function (p, i) {
      if (i % every === 0 || i === pts.length - 1) {
        marks.push({ i: i, tip: clockText(p[0]) + ": " + fmtPct(p[1], 1) + " used" });
      }
    });
    series.push({ kind: "actual", points: pts, marks: marks });
    var ticks = kind === "session" ? hourTicks(t0, t1) : dayTicks(t0, t1);
    var label = (kind === "session" ? "Session" : "Weekly") + " usage readings, " + pts.length + " points" + (forecastText ? ". " + forecastText : "");
    return chartHost(function (w) {
      return CHARTS.line({
        width: w, height: 176, xMin: t0, xMax: t1, yMin: 0, yMax: yMax, yTicks: yTicks, xTicks: ticks,
        marks: nowMs >= t0 && nowMs <= t1 ? [{ t: nowMs, label: "NOW" }] : [], series: series, label: label
      });
    }, [digest, kind, nowMs - nowMs % 60000, t0, t1, !!pending], "nm-line", pending);
  }

  /* The facts under a limit. pend marks the rows that come from the pacing report as placeholders. */
  function limitFacts(limit, blk, kind, p, pend) {
    var span = windowSeconds(limit);
    var start = p.reset !== null ? p.reset - span * 1000 : null;
    var rows = [
      ["Resets", p.reset === null ? "--" : stampText(p.reset)],
      ["Resets in", p.reset === null ? "--" : fmtRemain(p.remain)],
      ["Window started", start === null ? "--" : stampText(start)],
      ["Window", span >= 86400 ? Math.round(span / 86400) + " days" : Math.round(span / 3600) + " hours"]
    ];
    if (kind === "session" && blk) {
      rows.push(["Rate", num(blk.slope_pct_per_hour) === null ? "--" : signed(blk.slope_pct_per_hour) + " pts / hour", pend]);
      if (blk.projected_limit_at) {
        rows.push(["Limit reached", clockText(parseTime(blk.projected_limit_at))]);
      }
    }
    if (kind !== "session" && blk) {
      rows.push(["Budget left", num(blk.daily_budget_pct) === null ? "--" : fmtPct(blk.daily_budget_pct, 1) + " / day", pend]);
      rows.push(["Even pace", num(blk.even_daily_pct) === null ? "--" : fmtPct(blk.even_daily_pct, 1) + " / day", pend]);
      rows.push(["Projected end", num(blk.projected_end_pct) === null ? "needs more time" : fmtPct(blk.projected_end_pct, 1), pend]);
      rows.push(["Against the clock", num(blk.pace_delta_pp) === null ? "--" : signed(blk.pace_delta_pp) + " pts", pend]);
      var forecast = !pend && p.reset !== null ? averageRateForecast(blk, p.reset - p.remain) : null;
      if (forecast && forecast.reaches !== null) {
        rows.push(["Limit reached", clockText(forecast.reaches)]);
      }
    }
    if (kind === "scoped") {
      rows.push(["Allowance", num(limit.allowance_pct) === null ? "--" : fmtPct(limit.allowance_pct) + " of weekly"]);
      rows.push(["Used of weekly", num(limit.used_pct_of_weekly) === null ? "--" : fmtPct(limit.used_pct_of_weekly, 1)]);
    }
    rows.push(["Severity", SEVERITY_WORD[severityOf(limit)].toLowerCase()]);
    if (limit.locked_reason) {
      rows.push(["Locked", limit.locked_reason]);
    }
    if (limit.dollars) {
      rows.push(["Used", fmtUsd(limit.dollars.used)], ["Limit", fmtUsd(limit.dollars.limit)]);
    }
    return kvGrid(rows);
  }

  function pacingCard(limit, blk, kind, entry, nowMs) {
    var p = paceOf(limit, nowMs);
    var pct = num(limit.percent) || 0;
    var waiting = !blk && !entry.data && !entry.error;       // the pacing report has not answered yet: its parts are placeholders
    if (waiting) {
      blk = skelPacingBlock(limit);
    }
    var story = waiting
      ? { chip: "UNKNOWN", tone: "", text: kind === "session"
        ? "At the current rate you stay under the limit until the window resets at 12:00 PM, and a little more."
        : "You are 00.0 points under an even pace, so there is room to spare." }
      : !blk && !entry.data
        ? { chip: "", tone: "", text: "The forecast is not available right now." }
        : kind === "session" ? sessionStory(blk) : weeklyStory(blk);
    var elapsed = !waiting && blk && num(blk.elapsed_pct) !== null ? blk.elapsed_pct : (p.elapsed === null ? null : p.elapsed * 100);
    var tag = limit.label || (kind === "session" ? "SESSION" : "WEEKLY");
    var meta = limit.scope_model ? "scoped to " + limit.scope_model : kind === "session" ? "5-hour window" : "7-day window";
    var chart;
    if (blk) {
      chart = pacingChart(blk, kind, nowMs, entry.digest + "|" + tag, waiting) || '<p class="nm-empty">This limit has no window to chart yet.</p>';
    } else if (entry.error && !entry.data) {
      chart = '<p class="nm-error" role="status">' + esc(statProblem(entry)) + "</p>";
    } else {
      chart = '<p class="nm-empty">No pacing data for this limit yet.</p>';
    }
    var hold = waiting ? ' data-pending=""' : "";
    var body = '<div class="nm-big-row"><div class="nm-big">' + esc(fmtPct(pct)) + '</div><div class="nm-verdict">' +
      (story.chip ? '<span class="nm-chip solid' + story.tone + '"' + hold + ">" + esc(story.chip) + "</span>" : "") + "</div></div>" +
      '<p class="nm-story"' + hold + ">" + esc(story.text) + "</p>" +
      (elapsed === null ? "" : meterHTML(elapsed, pct)) + chart + limitFacts(limit, blk, kind, p, waiting);
    return '<section class="nm-card span-6' + sevClass(limit) + '" data-limit="' + esc(limit.kind) + '" aria-label="' + esc(tag) + '"><div class="nm-card-in">' +
      '<div class="nm-card-t"><span class="nm-tag">' + esc(tag) + '</span><span class="nm-meta-t">' + esc(meta) + "</span></div>" + body + "</div></section>";
  }

  /* The credits card's body. pending is the placeholder, the shape of the common case (credits off). */
  function spendCardBody(spend, pending) {
    if (pending) {
      return '<p class="nm-empty" data-pending="">Usage credits are off for this account.</p>' +
        '<p class="nm-note nm-disc" data-pending="">When they are on, this is the one figure that is real money.</p>';
    }
    if (spend && spend.enabled) {
      return '<div class="nm-big-row"><div class="nm-big m">' + esc(fmtMoney(spend.used, spend.currency)) + '</div><div class="nm-facts"><span>OF ' + esc(fmtMoney(spend.limit, spend.currency)) + "</span><span>" +
        esc(fmtPct(spend.percent)) + " USED</span></div></div>" +
        '<div class="nm-cells" aria-hidden="true">' + cellsHTML(24, num(spend.percent) || 0) + "</div>" +
        '<p class="nm-note nm-disc">Real money: usage credits reported by the usage API, billed by Anthropic.</p>';
    }
    return '<p class="nm-empty">Usage credits are off for this account.</p>' +
      '<p class="nm-note nm-disc">When they are on, this is the one figure that is real money.</p>';
  }

  function limitsPage() {
    var pe = statEntry("pacing");
    var pd = pe.data;
    var u = usage();
    var s = st();
    var nowMs = (pd && parseTime(pd.now)) || Date.now();
    var waiting = s ? "ready" : "pending";
    var session = limitOf("session"), weekly = limitOf("weekly_all"), scoped = scopedLimits();
    var cards = [];
    if (session) {
      cards.push(cardPart("limit-session", waiting, [pe], function () { return pacingCard(session, pd ? pd.session : null, "session", pe, nowMs); }));
    }
    if (weekly) {
      cards.push(cardPart("limit-weekly", waiting, [pe], function () { return pacingCard(weekly, pd ? pd.weekly : null, "weekly", pe, nowMs); }));
    }
    scoped.forEach(function (limit, i) {
      cards.push(cardPart("limit-scoped-" + i, waiting, [pe], function () {
        return pacingCard(limit, pd ? matchScoped(pd.scoped, limit, i) : null, "scoped", pe, nowMs);
      }));
    });
    if (!cards.length) {
      cards.push(cardPart("limit-none", "ready", [], function () { return emptyLimitCard("LIMITS", "No reading yet"); }));
    }
    var spendCard = card({ title: "Usage credits", meta: "real money", cls: "is-real", body: spendCardBody(u && u.spend, !!(u && u.skeleton)) });
    var breakdown = u && u.breakdown ? u.breakdown : [];
    var colours = ["main", "alt", "dim"];
    var stack = breakdown.length
      ? CHARTS.stackedBar(breakdown.map(function (r, i) {
        return { value: r.percent, cls: colours[i % 3], tip: String(r.name || r.key || "") + ": " + fmtPct(r.percent) };
      }), { width: 520, height: 16, label: "Share by surface" })
      : '<p class="nm-empty">No surface split yet.</p>';
    var legend = breakdown.map(function (r) {
      return mixRow(String(r.name || r.key || "").toUpperCase(), r.percent, fmtPct(r.percent));
    }).join("");
    var surfaceCard = card({
      title: "Seven-day surface breakdown", meta: "share of the weekly window",
      body: '<div class="nm-stack-wrap">' + stack + '</div><div class="nm-legend">' + legend + "</div>"
    });
    var odd = (cards.length % 2) === 1;
    if (odd) {
      cards.push(cardPart("credits-surfaces", waiting, [], function () { return '<div class="nm-colstack span-6">' + spendCard + surfaceCard + "</div>"; }));
    } else {
      cards.push(cardPart("credits", waiting, [], function () { return spendCard.replace('class="nm-card is-real"', 'class="nm-card span-6 is-real"'); }));
      cards.push(cardPart("surfaces", waiting, [], function () { return surfaceCard.replace('class="nm-card"', 'class="nm-card span-6"'); }));
    }
    return pageModel("limits", "nm-pgrid", cards, { stale: staleBanner([pe]) });
  }

  // ── Cost & value ────────────────────────────────────────────────────────────────

  function cacheHitOf(tokens) {
    var t = tokens || {};
    var prompt = (t.input || 0) + (t.cache_read || 0) + (t.cache_write || 0);
    return prompt > 0 ? t.cache_read / prompt : null;
  }

  function windowRow(name, w) {
    var label = WINDOW_LABEL[name] || name.toUpperCase();
    var title = w.start ? stampText(parseTime(w.start)) + " to " + (w.end ? stampText(parseTime(w.end)) : "now") : "since the first request";
    if (w.start && w.end && w.start === w.end) {
      return '<tr><td class="lead" title="' + esc(title) + '">' + esc(label) + '</td><td colspan="5" class="r">NOT OPEN</td></tr>';
    }
    var t = w.tokens || {};
    var saved = Math.max(0, (num(w.nocache_cost_usd) || 0) - (num(w.cost_usd) || 0));
    return '<tr class="' + (name === "cycle" ? "is-today" : "") + '"><td class="lead" title="' + esc(title) + '">' + esc(label) + '</td><td class="r">' + esc(fmtInt(w.requests)) +
      '</td><td class="r">' + esc(fmtCount(t.total)) + '</td><td class="r">' + esc(fmtRate(cacheHitOf(t))) +
      '</td><td class="r">' + esc(fmtUsd(saved)) + '</td><td class="r v">' + esc(fmtUsd(w.cost_usd)) + "</td></tr>";
  }

  function valueCard(d) {
    var v = d.value, plan = d.plan;
    var price = num(plan.price_usd);
    var mult = num(v.multiplier);
    var projected = num(v.projected_cycle_cost_usd);
    var body;
    if (price === null) {
      body = '<div class="nm-big-row"><div class="nm-big h">' + esc(fmtUsd(v.cycle_cost_usd)) + '</div><div class="nm-facts"><span>THIS CYCLE</span><span>PLAN PRICE UNKNOWN</span></div></div>' +
        '<p class="nm-note">Set the plan price in <a href="#settings">Settings</a> to see how many times over the plan pays for itself.</p>';
    } else {
      body = '<div class="nm-big-row"><div class="nm-big h">' + esc(fmtMult(mult)) + '</div><div class="nm-facts"><span>THIS CYCLE</span><span>YOUR PLAN</span></div></div>' +
        (mult === null ? "" : ratioCells(mult, fmtMult(mult) + " months of the plan", d.skeleton ? 12 : 0)) +
        '<p class="nm-note">' + esc(ratioNote(mult, plan.name)) + " (" + esc(fmtPlanPrice(price)) + " a month, " + esc(plan.price_source === "override" ? "your override" : "detected") + ")</p>" +
        kvGrid([
          ["Cycle so far", fmtUsd(v.cycle_cost_usd)],
          ["Projected", projected === null ? "needs an hour of data" : fmtUsd(projected) + " (" + fmtMult(projected / price) + ")"],
          ["Cycle", rangeText(v.cycle_start, v.cycle_end)],
          ["Plan", fmtPlanPrice(price) + " / month"]
        ], "is-one");
    }
    return card({ title: "Value of your plan", meta: "this cycle", cls: "span-5", id: "value",
      body: body + '<p class="nm-note nm-disc">' + BASIS + ", not amounts billed to your plan.</p>" });
  }

  function monthLabel(ym) {
    var m = /^(\d{4})-(\d{2})$/.exec(String(ym || ""));
    return m ? MONTHS[+m[2] - 1] + " " + m[1].slice(2) : String(ym || "");
  }

  function monthlyCard(d, e) {
    var rows = (d.monthly || []).map(function (m, i, all) {
      var last = i === all.length - 1;
      var mult = num(m.multiplier);
      var tip = monthLabel(m.month) + (last ? " (so far)" : "") + ": " + fmtUsdExact(m.cost_usd) + " API-equivalent" +
        (mult === null ? "" : ", " + fmtMult(mult) + " the plan") + ", " + plural(m.requests, "request") + ", " + fmtCount(m.tokens) + " tokens";
      return { label: monthLabel(m.month), value: num(m.cost_usd) || 0, text: fmtUsdShort(m.cost_usd) + (mult === null ? "" : " · " + fmtMult(mult)), tip: tip, alt: last };
    });
    var body = rows.length
      ? chartHost(function (w) {
        return CHARTS.hbars(rows, { width: w, labelWidth: 64, valueWidth: 118, label: "API-equivalent cost by month" });
      }, [e.digest, "monthly"], "nm-hbars") + '<p class="nm-note">The bar is the month&rsquo;s API-equivalent cost; the multiple is against the plan price.</p>'
      : '<p class="nm-empty">No monthly history yet.</p>';
    return card({ title: "Month by month", meta: "API-equivalent, with the plan multiple", cls: "span-7", body: body });
  }

  function breakdownList(rows, key, noun) {
    var list = rows.slice(0, 12).map(function (r) {
      var share = (num(r.share) || 0) * 100;
      var where = r.folder || r.project || "";
      return {
        label: r.name || r.model || "(unknown)", value: num(r.cost_usd) || 0, text: fmtUsd(r.cost_usd) + " · " + fmtPct(share),
        tip: (r.name || r.model || "") + ": " + fmtUsdExact(r.cost_usd) + " (" + fmtPct(share, 1) + " of cost), " + plural(r.requests, "request") + ", " +
          fmtCount(r.tokens) + " tokens" + (where && noun === "folder" ? ". " + where : "")
      };
    });
    return { list: list, more: Math.max(0, rows.length - list.length) };
  }

  function breakdownCard(title, meta, rows, e, key, noun, extraHtml) {
    var b = breakdownList(rows, key, noun);
    var body = b.list.length
      ? chartHost(function (w) {
        return CHARTS.hbars(b.list, { width: w, valueWidth: 124, label: title });
      }, [e.digest, key], "nm-hbars") + (b.more ? '<p class="nm-note">' + esc("and " + b.more + " more, smaller") + "</p>" : "")
      : '<p class="nm-empty">No usage in this window.</p>';
    return body + (extraHtml || "");
  }

  function sessionsTable(list) {
    if (!list.length) {
      return '<p class="nm-empty">No sessions this week yet.</p>';
    }
    var rows = list.slice(0, 5);
    // The folder column is as wide as the longest folder name needs (a column of a fixed table takes its width in percent: a
    // character is about 1.3 % of the 600 px a table has at its narrowest), up to 40 %, so a name is cut with an ellipsis only
    // when the table really has no room; the session title takes what is left.
    var longest = rows.reduce(function (n, r) { return Math.max(n, String(r.project_name || r.project || "").length); }, 0);
    var folderPct = clamp(Math.ceil((longest + 2) * 1.3), 14, 40);
    return '<div class="nm-table-wrap"><table class="nm-table nm-fixed"><caption class="nm-sr">The five most expensive sessions this week</caption>' +
      '<colgroup><col class="c-rank"><col class="c-title"><col class="c-folder" style="width:' + folderPct + '%"><col class="c-num"><col class="c-num"></colgroup>' +
      '<thead><tr><th scope="col">#</th><th scope="col">Session</th><th scope="col">Folder</th><th scope="col" class="r">API-equiv.</th><th scope="col" class="r">Requests</th></tr></thead><tbody>' +
      rows.map(function (r, i) {
        var title = r.title || "Untitled session";
        var tip = title + " · " + (r.models || []).join(", ") + (r.prs ? " · " + plural(r.prs, "pull request") : "") + " · subagents " + fmtUsd(r.subagent_cost_usd);
        return "<tr><td>" + (i + 1) + '</td><td class="lead nm-trunc" title="' + esc(tip) + '">' + esc(title) + '</td><td class="nm-trunc" title="' + esc(r.project || "") + '">' + esc(r.project_name || r.project || "") +
          '</td><td class="r v">' + esc(fmtUsd(r.cost_usd)) + '</td><td class="r">' + esc(fmtInt(r.requests)) + "</td></tr>";
      }).join("") + "</tbody></table></div>";
  }

  /* winCost null: the window's report is not in, and the note is a placeholder (hidden text of the length the real note has).
   * failed: the report is not coming (it failed and there is no older one), so the note is settled text, not a placeholder
   * that would wait for ever. */
  function costToolbar(pick, winCost, failed) {
    var gone = failed && winCost === null;
    var waiting = winCost === null && !gone;
    return '<div class="nm-toolbar wide"><span class="nm-toolbar-k">Breakdowns for</span>' + seg("cost-window", "Window for the breakdowns", WINDOW_SEG, pick) +
      '<span class="nm-toolbar-n"' + (waiting ? ' data-pending="" aria-hidden="true"' : "") + ">" +
      esc(gone ? WINDOW_PHRASE[pick] + ", not loaded" : WINDOW_PHRASE[pick] + ", " + (waiting ? "$000.00" : fmtUsd(winCost)) + " " + BASIS.toLowerCase()) + "</span></div>";
  }

  function costPage() {
    var e = statEntry("cost", { window: S.ui.costWindow });
    var d = e.data;
    // The parts that do not depend on the window (the plan multiple, the months, the windows table, the top sessions) are
    // drawn from the newest report of any window while this window's is on its way; the breakdowns wait for their own.
    var src = d ? e : lastGoodEntry("cost");
    var base = src ? src.data : skelCost();
    var whole = src ? "ready" : entryState([e]);
    var broken = d ? "ready" : entryState([e]);
    var skel = !d;
    var b = d ? d.breakdowns : skelCost().breakdowns;
    var pick = S.ui.costWindow;
    var s = st();
    var u = usage();
    var cards = [];
    cards.push(cardPart("value", whole, [e], function () { return valueCard(base); }));
    cards.push(cardPart("monthly", whole, [e], function () { return monthlyCard(base, src || e); }));
    cards.push(cardPart("windows", whole, [e], function () {
      return card({
        title: "Windows", meta: "requests, tokens and cost by window", cls: "wide",
        body: '<div class="nm-table-wrap"><table class="nm-table"><caption class="nm-sr">Cost by time window, API-equivalent at list prices</caption><thead><tr><th scope="col">Window</th><th scope="col" class="r">Requests</th><th scope="col" class="r">Tokens</th>' +
          '<th scope="col" class="r">Cache hit</th><th scope="col" class="r">Saved by cache</th><th scope="col" class="r">API-equiv.</th></tr></thead><tbody>' +
          WINDOW_ORDER.map(function (name) { return windowRow(name, base.windows[name]); }).join("") + "</tbody></table></div>"
      });
    }));
    var winCost = src ? num(base.windows[pick] && base.windows[pick].cost_usd) : null;
    cards.push(part("toolbar", costToolbar(pick, winCost, whole === "error")));
    cards.push(cardPart("by-model", broken, [e], function () {
      return card({ title: "By model", meta: "share of cost", cls: "span-6", body: breakdownCard("By model", "", b.by_model, e, "model", "model") });
    }));
    var launch = S.ui.folderMode === "launch";
    cards.push(cardPart("by-folder", broken, [e], function () {
      return card({
        title: launch ? "By launch folder" : "By folder", cls: "span-6",
        metaHtml: seg("folder-mode", "Group by", [["folder", "FOLDER"], ["launch", "LAUNCH FOLDER"]], S.ui.folderMode),
        body: breakdownCard(launch ? "By launch folder" : "By folder", "", launch ? b.by_project : b.by_folder, e, launch ? "launch" : "folder", launch ? "launch" : "folder",
          '<p class="nm-note">' + (launch ? "Where Claude Code was started. One launch folder can hold work from many repositories." : "Where the work happened: the repository the files live in. Worktrees count toward their repository.") + "</p>")
      });
    }));
    var main = b.by_thread.main, sub = b.by_thread.subagent;
    var threadTotal = (num(main.cost_usd) || 0) + (num(sub.cost_usd) || 0);
    var share = function (v) { return threadTotal > 0 ? (num(v) || 0) / threadTotal * 100 : 0; };
    cards.push(cardPart("threads", broken, [e], function () {
      return card({
        title: "Main and subagents", meta: "cost split", cls: "span-4",
        body: CHARTS.stackedBar([
          { value: num(main.cost_usd) || 0, cls: "main", tip: "Main threads: " + fmtUsdExact(main.cost_usd) },
          { value: num(sub.cost_usd) || 0, cls: "alt", tip: "Subagents: " + fmtUsdExact(sub.cost_usd) }
        ], { width: 300, height: 14, label: "Cost split between main threads and subagents" }) +
          '<div class="nm-stats">' +
          statTile("thread-main", "Main threads", fmtUsd(main.cost_usd), { sub: fmtPct(share(main.cost_usd)) + " · " + plural(main.requests, "request") }) +
          statTile("thread-sub", "Subagents", fmtUsd(sub.cost_usd), { sub: fmtPct(share(sub.cost_usd)) + " · " + plural(sub.requests, "request") }) + "</div>"
      });
    }));
    var haikuCost = (num(b.haiku.main.cost_usd) || 0) + (num(b.haiku.subagent.cost_usd) || 0);
    var haikuReq = (num(b.haiku.main.requests) || 0) + (num(b.haiku.subagent.requests) || 0);
    var chosenCost = d ? num(d.windows[pick] && d.windows[pick].cost_usd) : null;
    var haikuShare = skel ? 0 : chosenCost > 0 ? haikuCost / chosenCost * 100 : null;
    cards.push(cardPart("haiku", broken, [e], function () {
      return card({
        title: "Haiku share", meta: "of cost", cls: "span-4",
        body: '<div class="nm-stats">' + statTile("haiku-share", "Haiku share of cost", haikuShare === null ? "--" : fmtPct(haikuShare, 1), {
          big: true, bar: haikuShare, sub: plural(haikuReq, "request"), cls: "is-wide"
        }) + statTile("haiku-main", "Main threads", fmtUsd(b.haiku.main.cost_usd), { sub: plural(b.haiku.main.requests, "request") }) +
          statTile("haiku-sub", "Subagents", fmtUsd(b.haiku.subagent.cost_usd), { sub: plural(b.haiku.subagent.requests, "request") }) + "</div>"
      });
    }));
    var fast = b.fast || { requests: 0, cost_usd: 0 };
    cards.push(cardPart("fast", broken, [e], function () {
      return card({
        title: "Fast mode", meta: "paid from usage credits", cls: "span-4" + (fast.requests > 0 ? " is-warn" : ""),
        body: '<div class="nm-stats">' + statTile("fast-mode", "Fast-mode requests", fmtInt(fast.requests), { big: true, sub: fmtUsd(fast.cost_usd) + " API-equivalent", cls: "is-wide" }) + "</div>" +
          '<div class="nm-callout' + (fast.requests > 0 ? " is-warn" : "") + '" role="note"' + (skel ? ' data-pending=""' : "") + "><b>Heads up.</b> " + FAST_NOTE + "</div>"
      });
    }));
    cards.push(cardPart("sessions", whole, [e], function () {
      return card({ title: "Top sessions this week", meta: "by API-equivalent cost", cls: "span-8", body: sessionsTable(base.top_sessions_week || []) });
    }));
    var spend = (base.spend && !base.skeleton) ? base.spend : (u && u.spend);
    cards.push(cardPart("credits", s ? "ready" : "pending", [], function () {
      return card({
        title: "Usage credits", meta: "real money", cls: "span-4 is-real",
        body: spendCardBody(spend, !!(u && u.skeleton)) + '<p class="nm-note nm-disc">Only this card is real money. Every other figure on this page is ' + BASIS + ".</p>"
      });
    }));
    if (s && s.unpriced_models && s.unpriced_models.length) {
      cards.push(cardPart("unpriced", "ready", [], function () {
        return card({ title: "Not priced yet", meta: "excluded from cost", cls: "wide", body: '<ul class="nm-supp">' + s.unpriced_models.map(function (id) { return "<li>" + esc(id) + "</li>"; }).join("") + "</ul>" });
      }));
    }
    return pageModel("cost", "nm-pgrid", cards, { problem: problemBanner([e]), stale: staleBanner([e]) });
  }

  // ── Efficiency ──────────────────────────────────────────────────────────────────

  /* A block of any content (a chart, a table) with one explanation, like a stat tile. */
  function statBlock(key, label, inner) {
    var why = WHY[key] || "";
    return '<div class="nm-stat is-block" role="group" tabindex="0" data-fid="stat-' + key + '" data-tip="' + esc(why) + '" title="' + esc(why) +
      '" aria-labelledby="stat-k-' + key + '" aria-describedby="why-' + key + '"><div class="nm-stat-k" id="stat-k-' + key + '">' + esc(label) + "</div>" + inner +
      '<span class="nm-why nm-sr" id="why-' + key + '">' + esc(why) + "</span></div>";
  }

  function pct100(ratio) {
    var n = num(ratio);
    return n === null ? null : n * 100;
  }

  function toolRows(list, otherLabel) {
    var shown = list.slice(0, 10);
    var rest = list.slice(10);
    var rows = shown.map(function (t) {
      return { label: t.name, value: t.count, text: fmtInt(t.count), tip: t.name + ": " + plural(t.count, "call") };
    });
    if (rest.length) {
      var sum = rest.reduce(function (a, t) { return a + t.count; }, 0);
      rows.push({ label: rest.length + " " + otherLabel, value: sum, text: fmtInt(sum), tip: rest.length + " " + otherLabel + ": " + plural(sum, "call"), alt: true });
    }
    return rows;
  }

  /* The Efficiency page's cards, one part each. state is how the report is doing; d is the report, or the placeholder shape. */
  function efficiencyCards(d, e, state) {
    var c = d.cache, th = d.thinking, cx = d.context, cp = d.compactions, t = d.tools, pr = d.productivity, w = d.web;
    var cards = [];
    var prompt = (c.fresh_input_tokens || 0) + (c.read_tokens || 0) + (c.write_tokens || 0);
    function add(key, build) {
      cards.push(cardPart(key, state, [e], build));
    }
    add("cache", function () {
      return card({
        title: "Cache", meta: "prompt tokens from cache", cls: "span-4",
        body: '<div class="nm-stats">' +
          statTile("cache-hit", "Cache hit rate", fmtRate(c.hit_rate), { big: true, bar: pct100(c.hit_rate), cls: "is-wide" }) +
          statTile("cache-saved", "Caching saved", fmtUsd(c.saved_usd), { big: true, sub: "list prices", cls: "is-wide" }) +
          statTile("cache-read", "Cache reads", fmtCount(c.read_tokens), { sub: fmtInt(c.read_tokens) }) +
          statTile("cache-fresh", "Fresh input", fmtCount(c.fresh_input_tokens), { sub: fmtInt(c.fresh_input_tokens) }) +
          statTile("cache-write-5m", "Writes, 5 min", fmtCount(c.write_5m_tokens), { sub: fmtInt(c.write_5m_tokens) }) +
          statTile("cache-write-1h", "Writes, 1 hour", fmtCount(c.write_1h_tokens), { sub: fmtInt(c.write_1h_tokens) }) + "</div>" +
          (prompt > 0 ? CHARTS.stackedBar([
            { value: c.read_tokens, cls: "main", tip: "Read from cache: " + fmtInt(c.read_tokens) },
            { value: c.write_tokens, cls: "alt", tip: "Written to cache: " + fmtInt(c.write_tokens) },
            { value: c.fresh_input_tokens, cls: "dim", tip: "Fresh input: " + fmtInt(c.fresh_input_tokens) }
          ], { width: 300, height: 12, label: "Where prompt tokens came from: cache reads, cache writes, fresh input" }) +
          '<p class="nm-note">Prompt tokens: cache reads, cache writes, fresh input</p>' : "")
      });
    });
    add("thinking", function () {
      return card({
        title: "Thinking", meta: "share of output", cls: "span-4",
        body: '<div class="nm-stats">' +
          statTile("thinking-share", "Thinking share", fmtRate(th.share), { big: true, bar: pct100(th.share), cls: "is-wide" }) +
          statTile("thinking-tokens", "Thinking tokens", fmtCount(th.thinking_tokens), { sub: fmtInt(th.thinking_tokens) }) +
          statTile("output-tokens", "Output tokens", fmtCount(th.output_tokens), { sub: fmtInt(th.output_tokens) }) + "</div>"
      });
    });
    add("compactions", function () {
      return card({
        title: "Compactions", meta: "conversation summaries", cls: "span-4",
        body: '<div class="nm-stats">' +
          statTile("compactions", "Compactions", fmtInt(cp.count), { big: true, sub: fmtInt(cp.auto) + " auto · " + fmtInt(cp.manual) + " manual", cls: "is-wide" }) +
          statTile("compactions-day", "Per active day", num(cp.per_active_day) === null ? "--" : cp.per_active_day.toFixed(2)) +
          statTile("active-days", "Active days", fmtInt(d.active_days)) +
          // Claude Code's own count when it compacted, not the API's per-request count under Context size: the two can differ.
          statTile("compact-pre", "Size at compaction", cp.avg_pre_tokens === null ? "--" : fmtCount(cp.avg_pre_tokens), { sub: "tokens, average, Claude Code's own count" }) + "</div>"
      });
    });
    var cmax = num(cx.max) || 0;
    var ctxBar = function (v) { return cmax > 0 && num(v) !== null ? v / cmax * 100 : 0; };
    add("context", function () {
      return card({
        title: "Context size", meta: "prompt tokens per request, API count", cls: "span-6",
        body: '<div class="nm-stats is-quad">' +
          statTile("ctx-avg", "Average", cx.avg === null ? "--" : fmtCount(cx.avg), { big: true, bar: ctxBar(cx.avg) }) +
          statTile("ctx-p50", "Median (p50)", cx.p50 === null ? "--" : fmtCount(cx.p50), { big: true, bar: ctxBar(cx.p50) }) +
          statTile("ctx-p95", "p95", cx.p95 === null ? "--" : fmtCount(cx.p95), { big: true, bar: ctxBar(cx.p95) }) +
          statTile("ctx-max", "Largest request", cx.max === null ? "--" : fmtCount(cx.max), { big: true, bar: cx.max === null ? 0 : 100 }) + "</div>" +
          '<p class="nm-note">' + esc(fmtInt(cx.requests)) + " requests measured, by the API&rsquo;s token counts. The size at compaction is Claude Code&rsquo;s own count, so the two can differ.</p>"
      });
    });
    add("tools", function () {
      return card({
        title: "Tool health", meta: "calls and errors", cls: "span-6",
        body: '<div class="nm-stats">' +
          statTile("tool-total", "Tool calls", fmtInt(t.total), { big: true }) +
          statTile("tool-errors", "Tool errors", fmtInt(t.errors), { big: true }) +
          statTile("tool-rate", "Error rate", t.error_rate === null ? "--" : fmtRate(t.error_rate, 1), { big: true, bar: pct100(t.error_rate) }) + "</div>"
      });
    });
    var builtin = toolRows(t.builtin || [], "other built-in tools");
    var mcp = (t.mcp || []).map(function (m) {
      return { label: m.server, value: m.count, text: fmtInt(m.count), alt: true, tip: m.server + " (MCP server): " + plural(m.count, "call") + " across " + plural(m.tools, "tool") };
    });
    add("tool-mix", function () {
      return card({
        title: "Tool mix: built-in", meta: "calls by tool", cls: "span-7",
        body: builtin.length ? statBlock("tool-mix", "Calls by tool", chartHost(function (width) {
          return CHARTS.hbars(builtin, { width: width, valueWidth: 70, label: "Built-in tool calls" });
        }, [e.digest, "builtin"], "nm-hbars")) : '<p class="nm-empty">No tool calls in this window.</p>'
      });
    });
    add("mcp-mix", function () {
      return card({
        title: "Tool mix: MCP", meta: "calls by server", cls: "span-5",
        body: mcp.length ? chartHost(function (width) {
          return CHARTS.hbars(mcp, { width: width, valueWidth: 70, label: "MCP server calls" });
        }, [e.digest, "mcp"], "nm-hbars") + '<p class="nm-note">MCP tools are grouped by the server that provides them.</p>' : '<p class="nm-empty">No MCP servers were called.</p>'
      });
    });
    add("productivity", function () {
      return card({
        title: "Productivity", meta: "what the tokens produced", cls: "span-8",
        body: '<div class="nm-stats is-quad">' +
          statTile("lines-added", "Lines added", (pr.lines_added > 0 ? "+" : "") + fmtInt(pr.lines_added), { big: true }) +
          statTile("lines-removed", "Lines removed", (pr.lines_removed > 0 ? "-" : "") + fmtInt(pr.lines_removed), { big: true }) +
          statTile("files-created", "Files created", fmtInt(pr.files_created), { big: true }) +
          statTile("commits", "Commits", fmtInt(pr.commits), { big: true }) +
          statTile("pushes", "Pushes", fmtInt(pr.pushes), { big: true }) +
          statTile("prs", "Pull requests", fmtInt(pr.prs), { big: true }) +
          statTile("cost-pr", "Cost per PR", pr.cost_per_pr_usd === null ? "--" : fmtUsd(pr.cost_per_pr_usd), { big: true, sub: "API-equivalent" }) +
          statTile("cost-100", "Per 100 changed lines", pr.cost_per_100_lines_usd === null ? "--" : fmtUsd(pr.cost_per_100_lines_usd), { big: true, sub: "API-equivalent" }) + "</div>"
      });
    });
    var effort = (d.effort || []).map(function (x) {
      return { label: x.effort, value: num(x.cost_usd) || 0, text: fmtUsd(x.cost_usd), tip: x.effort + ": " + fmtUsdExact(x.cost_usd) + " API-equivalent, " + plural(x.requests, "request") };
    });
    add("effort", function () {
      return card({
        title: "Effort mix", meta: "cost by effort level", cls: "span-4",
        body: effort.length ? statBlock("effort-mix", "Cost by effort", chartHost(function (width) {
          return CHARTS.hbars(effort, { width: width, valueWidth: 80, label: "Cost by effort level" });
        }, [e.digest, "effort"], "nm-hbars")) : '<p class="nm-empty">No requests in this window.</p>'
      });
    });
    var tokenRows = [
      ["Fresh input", c.fresh_input_tokens], ["Output", th.output_tokens], ["Cache reads", c.read_tokens],
      ["Writes, 5 min", c.write_5m_tokens], ["Writes, 1 hour", c.write_1h_tokens],
      ["Thinking", th.thinking_tokens],
      ["Total", (c.fresh_input_tokens || 0) + (th.output_tokens || 0) + (c.read_tokens || 0) + (c.write_tokens || 0)]
    ];
    add("tokens", function () {
      return card({
        title: "Exact token counts", meta: "this window", cls: "span-4",
        body: '<div class="nm-table-wrap"><table class="nm-table nm-tokens"><caption class="nm-sr">Exact token counts for the chosen window</caption><tbody>' + tokenRows.map(function (row) {
          return '<tr class="' + (row[0] === "Total" ? "is-today" : "") + '"><th scope="row" class="lead">' + esc(row[0]) + '</th><td class="r v">' + esc(fmtInt(row[1])) + "</td></tr>";
        }).join("") + "</tbody></table></div>"
      });
    });
    add("api-web", function () {
      return card({
        title: "API errors and web", meta: "outside the tools", cls: "span-4",
        body: '<div class="nm-stats">' +
          statTile("api-errors", "API errors", fmtInt(d.api_errors), { big: true }) +
          statTile("web-searches", "Web searches", fmtInt(w.searches), { big: true }) +
          statTile("web-fetches", "Web fetches", fmtInt(w.fetches), { big: true }) + "</div>"
      });
    });
    return cards;
  }

  /* The pipeline card: how the monitor's own data is doing. The plan is named by its plan name ("Max 5x"), never by the
   * internal tier id the usage API reports. */
  function pipelineCard(s) {
    var ingest = s && s.ingest ? s.ingest : null;
    var ingestText = ingest
      ? (ingest.running ? "RUNNING " + fmtInt(ingest.files_done) + " / " + fmtInt(ingest.files_total) : "IDLE, " + fmtInt(ingest.files_total) + " FILES")
      : "--";
    var kv = [
      ["LINK", s ? (STATUS[s.status] || STATUS.loading).text : "--"],
      ["FETCHED", s && s.usage_fetched_at ? stampText(parseTime(s.usage_fetched_at)) : "--"],
      ["UPDATED", s && s.updated_at ? stampText(parseTime(s.updated_at)) : "--"],
      ["INGEST", ingestText],
      ["LAST INGEST", ingest && ingest.last_duration_s !== null && ingest.last_duration_s !== undefined ? num(ingest.last_duration_s).toFixed(2) + " S" : "--"],
      ["LAST ERROR", ingest && ingest.last_error ? ingest.last_error : "NONE"],
      ["PLAN", s && s.plan && s.plan.name ? String(s.plan.name).toUpperCase() : "--"],
      ["PRICE", effectivePlanPrice() !== null ? fmtPlanPrice(effectivePlanPrice()) + " / MO" : "UNKNOWN"]
    ];
    return card({ title: "Pipeline", meta: "data health", cls: "span-6", body: kvGrid(kv) });
  }

  function notPricedCard(s) {
    var unpriced = s && s.unpriced_models && s.unpriced_models.length
      ? s.unpriced_models.map(function (id) { return "<li>" + esc(id) + "</li>"; }).join("") : "<li>NONE</li>";
    return card({ title: "Not priced", meta: "models without a price row", cls: "span-6", body: '<ul class="nm-supp">' + unpriced + "</ul>" });
  }

  function efficiencyPage() {
    var e = statEntry("efficiency", { window: S.ui.efficiencyWindow });
    var d = e.data;
    var s = st();
    var pick = S.ui.efficiencyWindow;
    var state = entryState([e]);
    var base = d || skelEfficiency();
    var gone = !d && state === "error";                // the report failed: the note says so, it is not a placeholder that waits for ever
    var note = d
      ? WINDOW_PHRASE[pick] + (d.start && d.end && pick !== "session" && pick !== "today" ? ", " + rangeText(d.start, d.end) : "") + ", " + plural(d.active_days, "active day")
      : gone ? WINDOW_PHRASE[pick] + ", not loaded"
      : WINDOW_PHRASE[pick] + (pick !== "session" && pick !== "today" ? ", Oct 00 - Nov 00" : "") + ", 00 active days";      // hidden: it keeps the note's room
    var toolbar = '<div class="nm-toolbar wide"><span class="nm-toolbar-k">Figures for</span>' + seg("efficiency-window", "Window for the figures", WINDOW_SEG, pick) +
      '<span class="nm-toolbar-n"' + (d || gone ? "" : ' data-pending="" aria-hidden="true"') + ">" + esc(note) + "</span></div>";
    var cards = [part("toolbar", toolbar)].concat(efficiencyCards(base, e, state));
    var waiting = s ? "ready" : "pending";
    cards.push(cardPart("pipeline", waiting, [], function () { return pipelineCard(s); }));
    cards.push(cardPart("not-priced", waiting, [], function () { return notPricedCard(s); }));
    return pageModel("efficiency", "nm-pgrid", cards, { problem: problemBanner([e]), stale: staleBanner([e]) });
  }

  // ── History ─────────────────────────────────────────────────────────────────────

  /* placeholder: ten zeros, the width a typical total has, for a total that has not arrived. */
  function odometerHTML(total, placeholder) {
    var text = placeholder ? "0,000,000,000" : Math.round(Math.max(0, num(total) || 0)).toLocaleString("en-US");
    var strip = "0123456789".split("").map(function (n) { return "<i>" + n + "</i>"; }).join("");
    return '<div class="nm-odo" role="img" aria-label="' + esc(fmtInt(total) + " tokens, all time") + '">' + text.split("").map(function (ch) {
      return ch === "," ? '<span class="nm-od-sep" aria-hidden="true">,</span>'
        : '<span class="nm-od-d" aria-hidden="true" data-d="' + ch + '"><span class="strip" style="--d:' + ch + '">' + strip + "</span></span>";
    }).join("") + "</div>";
  }

  function fmtCompare(v) {
    var n = num(v);
    if (n === null) {
      return "--";
    }
    return n >= 100 ? Math.round(n).toLocaleString("en-US") : n >= 10 ? n.toFixed(0) : n.toFixed(1);
  }

  function comparisonList(od) {
    var out = [];
    ((od && od.comparisons) || []).forEach(function (c) {
      var v = num(c.value);
      if (v === null || v <= 0) {
        return;
      }
      if (c.key === "war_and_peace") {
        out.push({ n: fmtCompare(v), u: "copies of War and Peace", t: "Everything Claude has written back to you, thinking included, would fill this many copies." });
      } else if (c.key === "britannica") {
        out.push({ n: fmtCompare(v), u: "sets of the Encyclopaedia Britannica", t: "Every token you have exchanged, cache reads included, would fill this many complete sets." });
      } else if (c.key === "page_stack_m") {
        out.push({ n: v >= 1000 ? (v / 1000).toFixed(1) : v >= 100 ? Math.round(v) : v.toFixed(1), u: (v >= 1000 ? "km" : "metres") + " of printed pages", t: "Claude’s output printed at 500 words a page and stacked, 0.1 mm a sheet, would stand this tall." });
      } else {
        out.push({ n: fmtCompare(v), u: String(c.label || c.key || ""), t: "" });
      }
    });
    return out;
  }

  /* Fills the comparison box for the current index. Called after a redraw and by the rotation timer;
   * the box is only touched when its content changes, so it does not fade in again for nothing. */
  function updateCompare() {
    var box = $("#compare");
    var list = S.cmpList || [];
    if (!box) {
      return;
    }
    var html;
    if (S.cmpList === null) {
      // The same three parts a real comparison has (the text, and the picker dots, so the box keeps its height), none of them live yet.
      html = '<div class="nm-cmp-body"><div class="nm-cmp-n" data-pending="" aria-hidden="true">000</div><div class="nm-cmp-u" data-pending="" aria-hidden="true">copies of War and Peace</div>' +
        '<p class="nm-cmp-t" data-pending="" aria-hidden="true">Everything Claude has written back to you, thinking included, would fill this many copies.</p></div>' +
        '<div class="nm-dots" aria-hidden="true"><button type="button" tabindex="-1" disabled></button><button type="button" tabindex="-1" disabled></button><button type="button" tabindex="-1" disabled></button></div>';
    } else if (!list.length) {
      html = '<p class="nm-empty">Nothing to compare yet.</p>';
    } else {
      var i = S.ui.cmp % list.length;
      var c = list[i];
      html = '<div class="nm-cmp-body' + (reduceMotion() ? "" : " is-in") + '"><div class="nm-cmp-n">' + esc(c.n) + '</div><div class="nm-cmp-u">' + esc(c.u) + "</div>" +
        (c.t ? '<p class="nm-cmp-t">' + esc(c.t) + "</p>" : "") + "</div>" +
        (list.length > 1 ? '<div class="nm-dots" role="group" aria-label="Comparisons">' + list.map(function (_, k) {
          return '<button type="button" data-cmp="' + k + '" data-fid="cmp-' + k + '" aria-pressed="' + (k === i ? "true" : "false") + '" aria-label="Comparison ' + (k + 1) + " of " + list.length + '"></button>';
        }).join("") + "</div>" : "");
    }
    if (box.__html !== html) {
      box.__html = html;
      replaceHtml(box, html);
    }
  }

  function stopCompareTimer() {
    if (S.cmpTimer) {
      window.clearInterval(S.cmpTimer);
      S.cmpTimer = 0;
    }
  }

  function syncCompareTimer() {
    var want = S.page === "history" && !reduceMotion() && (S.cmpList || []).length > 1;
    if (!want) {
      stopCompareTimer();
      return;
    }
    if (S.cmpTimer) {
      return;
    }
    S.cmpTimer = window.setInterval(function () {
      var box = $("#compare");
      if (!box || document.visibilityState === "hidden" || box.matches(":hover") || box.matches(":focus-within")) {
        return;
      }
      S.ui.cmp = (S.ui.cmp + 1) % S.cmpList.length;
      updateCompare();
    }, COMPARE_MS);
  }

  function playOdometer(main) {
    var total = S.odoTotal;
    if (total === null || S.odoShown === total) {
      return;
    }
    S.odoShown = total;
    if (reduceMotion()) {
      return;
    }
    var strips = main.querySelectorAll(".nm-od-d .strip");
    for (var i = 0; i < strips.length; i++) {
      var el = strips[i];
      var digit = el.parentNode.getAttribute("data-d");
      el.style.transition = "none";
      el.style.setProperty("--d", "0");
      void el.offsetHeight;
      el.style.transition = "transform 1.5s cubic-bezier(.2,.8,.25,1) " + (i * 80) + "ms";
      el.style.setProperty("--d", digit);
    }
  }

  function hourRange(c) {
    var a = c % 12 || 12, b = (c + 1) % 12 || 12;
    return a + (c < 12 ? " AM" : " PM") + " to " + b + ((c + 1) % 24 < 12 ? " AM" : " PM");
  }

  function historyPage() {
    var e = statEntry("history");
    var d = e.data;
    var state = entryState([e]);
    var skel = !d;
    var h = d || skelHistory();
    var od = h.odometer;
    var cards = [];
    // The odometer rolls when the real total arrives, and the comparison box has nothing to say about a placeholder.
    S.odoTotal = skel ? null : num(od.total_tokens) || 0;
    S.cmpList = skel ? null : comparisonList(od);
    cards.push(cardPart("odometer", state, [e], function () {
      return card({
        title: "All-time odometer", meta: "tokens, everything Claude has read and written", cls: "wide", id: "odometer",
        body: '<div class="nm-odo-row"><div class="nm-odo-main">' + odometerHTML(od.total_tokens, skel) +
          '<div class="nm-odo-facts"><span><b>' + esc(fmtInt(od.requests)) + "</b> requests</span><span><b>" + esc(fmtCount(od.output_tokens)) + "</b> output tokens</span><span><b>" +
          esc(fmtUsd(od.cost_usd)) + "</b> API-equivalent</span><span><b>" + esc(fmtInt(h.active_days)) + "</b> active days</span></div></div>" +
          '<div class="nm-compare" id="compare" aria-live="off"></div></div>'
      });
    }));
    var metric = S.ui.calMetric;
    var days = h.days.map(function (x) {
      var has = (num(x.requests) || 0) > 0;
      return {
        date: x.date, value: metric === "cost" ? num(x.cost_usd) || 0 : num(x.tokens) || 0,
        tip: dayText(x.date, true) + ": " + (has ? fmtUsd(x.cost_usd) + " API-equivalent, " + fmtCount(x.tokens) + " tokens, " + plural(x.requests, "request") : "no activity")
      };
    });
    cards.push(cardPart("calendar", state, [e], function () {
      return card({
        title: "Every day", cls: "wide", metaHtml: seg("cal-metric", "Colour each day by", [["cost", "COST"], ["tokens", "TOKENS"]], metric),
        body: chartHost(function (w) {
          return CHARTS.calendar(days, { width: w, maxCell: 22, label: "Daily " + (metric === "cost" ? "cost" : "token") + " heatmap, " + days.length + " days", legend: ["LESS", "MORE"] });
        }, [e.digest, metric, CHARTS.pips()], "nm-calendar") +
          '<p class="nm-note">A stronger colour is more ' + (metric === "cost" ? "API-equivalent cost" : "tokens") + ". Today is outlined. Arrow keys move between days.</p>"
      });
    }));
    var mm = S.ui.matrixMetric;
    var grid = mm === "cost" ? h.hour_weekday.cost_usd : h.hour_weekday.requests;
    var colLabels = [];
    for (var hr = 0; hr < 24; hr += 3) {
      colLabels.push({ c: hr, text: pad2(hr) });
    }
    cards.push(cardPart("matrix", state, [e], function () {
      return card({
        title: "When you work", cls: "span-8", metaHtml: seg("matrix-metric", "Colour each hour by", [["cost", "COST"], ["requests", "REQUESTS"]], mm),
        body: chartHost(function (w) {
          return CHARTS.matrix(grid, {
            width: w, maxCell: 34, rowLabels: WEEKDAYS_MON, colLabels: colLabels, label: "Activity by weekday and hour of day, local time",
            tip: function (r, c, v) {
              return WEEKDAYS_MON[r] + " " + hourRange(c) + ": " + (num(v) > 0 ? (mm === "cost" ? fmtUsd(v) + " API-equivalent" : plural(v, "request")) : "no activity");
            }
          });
        }, [e.digest, mm], "nm-matrix") + '<p class="nm-note">Local time, Monday first, over the last ' + esc(fmtInt(h.days.length)) + " days.</p>"
      });
    }));
    var sk = h.streaks;
    var first = parseTime(h.first_use);
    var sinceFirst = first === null ? null : Math.max(1, Math.round((Date.now() - first) / 86400000));
    cards.push(cardPart("streaks", state, [e], function () {
      return card({
        title: "Streaks and records", meta: "all time", cls: "span-4",
        body: '<div class="nm-stats">' +
          statTile("streak-current", "Current streak", plural(sk.current_days, "day"), { big: true }) +
          statTile("streak-longest", "Longest streak", plural(sk.longest_days, "day"), { big: true, sub: sk.longest_start ? dayText(sk.longest_start) + " to " + dayText(sk.longest_end) : "" }) +
          statTile("busiest-day", "Busiest day", h.busiest_day ? dayText(h.busiest_day.date, true) : "--", { text: true, cls: "is-wide", sub: h.busiest_day ? fmtUsd(h.busiest_day.cost_usd) + " · " + fmtCount(h.busiest_day.tokens) + " tokens" : "" }) +
          statTile("first-use", "First use", first === null ? "--" : dateText(first), { text: true, cls: "is-wide", sub: sinceFirst === null ? "" : plural(sinceFirst, "day") + " ago" }) +
          statTile("history-active", "Days used", fmtInt(h.active_days), { sub: sinceFirst ? fmtPct(Math.min(100, h.active_days / sinceFirst * 100)) + " of days since first use" : "" }) + "</div>"
      });
    }));
    cards.push(cardPart("export", "ready", [], function () {
      return card({
        title: "Export", meta: "daily totals", cls: "wide",
        body: '<p class="nm-lede">One row for each day you used Claude Code: requests, tokens by kind, and cost (' + BASIS + '). Prompts, replies and code are never included.</p>' +
          '<div class="nm-actions"><button class="nm-btn" type="button" data-action="export" data-format="csv" data-fid="export-csv">Export CSV</button>' +
          '<button class="nm-btn" type="button" data-action="export" data-format="json" data-fid="export-json">Export JSON</button></div>' +
          '<p class="nm-status' + (S.exportStatus.error ? " is-err" : "") + '" id="export-status" role="status" aria-live="polite">' + exportStatusHtml() + "</p>"
      });
    }));
    return pageModel("history", "nm-pgrid", cards, { problem: problemBanner([e]), stale: staleBanner([e]) });
  }

  /* Work to do after a page is in the DOM. */
  function afterRender(key, main) {
    if (key === "history") {
      updateCompare();
      playOdometer(main);
    }
    syncCompareTimer();
  }

  // ── Settings ────────────────────────────────────────────────────────────────────

  function toggleRow(key, title, desc, attrs) {
    return '<label class="nm-row-set"><span><span class="t">' + esc(title) + '</span><span class="d">' + esc(desc) +
      '</span></span><span class="nm-toggle"><input type="checkbox" data-key="' + esc(key) + '" data-kind="bool"' + (attrs || "") + '><span class="box" aria-hidden="true"></span><span class="lbl" data-lbl-for="' +
      esc(key) + '">OFF</span></span></label>';
  }

  function selectRow(key, title, desc, options) {
    return '<label class="nm-row-set"><span><span class="t">' + esc(title) + '</span><span class="d">' + esc(desc) +
      '</span></span><select class="nm-select" data-key="' + esc(key) + '">' + options.map(function (o) {
        return '<option value="' + esc(o[0]) + '">' + esc(o[1]) + "</option>";
      }).join("") + "</select></label>";
  }

  function errLine(key) {
    return '<p class="nm-err" id="err-' + key + '" role="alert" hidden></p>';
  }

  function alertsCard() {
    var dependent = ' data-needs-alerts="1"';
    return '<section class="nm-card wide" aria-label="Alerts"><div class="nm-card-in">' +
      '<div class="nm-card-t"><h2 class="nm-ttl">Alerts</h2><span class="nm-meta-t">Windows notifications from the tray</span></div><div class="nm-set" id="alerts-set">' +
      toggleRow("alerts_enabled", "Alerts", "Show a Windows notice when a limit reaches a level or starts over. Off silences every alert below.") +
      '<div class="nm-row-set is-stack"><span><span class="t">Levels</span><span class="d">A notice appears the first time a limit reaches each level in its window. One to five levels, each from 1 to 100.</span></span>' +
      '<div class="nm-levels"><div class="nm-chips" id="alert-chips" role="list" aria-label="Alert levels"></div>' +
      '<div class="nm-addlevel"><label class="nm-sr" for="alert-add">New level, percent</label>' +
      '<input class="nm-input" id="alert-add" type="number" min="1" max="100" step="1" inputmode="numeric" placeholder="95" data-fid="alert-add"' + dependent + '>' +
      '<button class="nm-btn" type="button" data-action="alert-add" data-fid="alert-add-btn"' + dependent + ">Add level</button></div>" +
      errLine("alert_thresholds") + "</div></div>" +
      toggleRow("alert_on_threshold", "Level reached", "Notify when a limit reaches one of the levels above.", dependent) +
      toggleRow("alert_on_reset", "Limit reset", "Notify when a limit window starts over.", dependent) +
      toggleRow("alert_on_credits", "Usage credits in use", "Notify, at most once a day, when usage credits start being spent. That is real money.", dependent) +
      '<div class="nm-row-set"><span><span class="t">Quiet hours</span><span class="d">No notices between these times. They may cross midnight.</span></span>' +
      '<span class="nm-quiet"><label class="nm-toggle"><input type="checkbox" data-quiet="on"' + dependent + '><span class="box" aria-hidden="true"></span><span class="lbl" data-lbl-quiet="1">OFF</span></label>' +
      '<label class="nm-sr" for="quiet-start">Quiet hours start</label><input class="nm-input is-time" type="time" id="quiet-start" data-quiet="start" value="22:00"' + dependent + ">" +
      '<span class="nm-quiet-to">to</span>' +
      '<label class="nm-sr" for="quiet-end">Quiet hours end</label><input class="nm-input is-time" type="time" id="quiet-end" data-quiet="end" value="07:00"' + dependent + "></span></div>" +
      errLine("alert_quiet_hours") + "</div></div></section>";
  }

  function settingsPage(main) {
    if (S.formKey !== "settings") {
      S.formKey = "settings";
      S.sig = "";
      var plan = planFromState();
      var detected = plan.price !== null ? plan.name + ", " + fmtPlanPrice(plan.price) + " a month" : "no price known";
      main.innerHTML = pageHead("settings") +
        '<div class="nm-pgrid">' +
        '<section class="nm-card wide" aria-label="Theme"><div class="nm-card-in"><div class="nm-card-t"><h2 class="nm-ttl">Theme</h2><span class="nm-meta-t" id="set-theme-meta"></span></div>' +
        '<div class="nm-themes" id="set-themes"></div><div id="set-mode" class="nm-actions" hidden></div><div class="nm-variants" id="set-accents"></div></div></section>' +
        '<section class="nm-card" aria-label="Widgets and display"><div class="nm-card-in"><div class="nm-card-t"><h2 class="nm-ttl">Widgets &amp; display</h2><span class="nm-meta-t">floating and taskbar</span></div><div class="nm-set">' +
        toggleRow("show_floating", "Floating widget", "Show the always-on-top widget on the desktop.") +
        toggleRow("show_taskbar", "Taskbar strip", "Show the strip beside the clock.") +
        toggleRow("float_transparent_bg", "Transparent widget", "No plate behind the floating widget.") +
        toggleRow("float_text_fx", "Widget text effects", "Glow and flicker on the floating widget.") +
        toggleRow("taskbar_transparent_bg", "Transparent strip", "No plate behind the taskbar strip.") +
        toggleRow("taskbar_text_fx", "Strip text effects", "Glow on the taskbar strip.") +
        toggleRow("hide_floating_in_fullscreen", "Hide in fullscreen", "Hide the floating widget while an app is fullscreen.") +
        selectRow("show_scoped_rows", "Scoped rows", "Show rows for scoped limits on the widget.", [["auto", "AUTO"], ["always", "ALWAYS"], ["never", "NEVER"]]) +
        toggleRow("reduce_motion", "Reduce motion", "Stop the boot sequences, page entrances, hover effects (here and on the desktop widgets), flicker, blinking and the rotating comparison.") +
        "</div></div></section>" +
        '<section class="nm-card" aria-label="Data and plan"><div class="nm-card-in"><div class="nm-card-t"><h2 class="nm-ttl">Data &amp; plan</h2><span class="nm-meta-t">how often, and what you pay</span></div><div class="nm-set">' +
        selectRow("poll_interval_ms", "Fetch usage every", "How often the usage API is asked. The minimum is one minute.", POLL_CHOICES) +
        '<label class="nm-row-set"><span><span class="t">Billing day</span><span class="d">The day of the month your billing cycle starts. Blank uses the calendar month.</span></span>' +
        '<select class="nm-select" data-key="billing_day" aria-describedby="err-billing_day"><option value="">CALENDAR MONTH</option>' +
        Array.from({ length: 31 }, function (_, i) { return '<option value="' + (i + 1) + '">DAY ' + (i + 1) + "</option>"; }).join("") + "</select></label>" + errLine("billing_day") +
        '<label class="nm-row-set"><span><span class="t">Plan price override</span><span class="d" id="plan-hint">USD a month. Blank uses the detected price (' + esc(detected) + ").</span></span>" +
        '<input class="nm-input" type="number" data-key="plan_price_override" min="0.01" max="10000" step="0.01" placeholder="AUTO" inputmode="decimal" aria-describedby="plan-hint err-plan_price_override"></label>' + errLine("plan_price_override") +
        '</div><div class="nm-actions"><button class="nm-btn" type="button" data-action="refresh">Refresh now</button></div></div></section>' +
        alertsCard() +
        '<section class="nm-card wide" aria-label="System"><div class="nm-card-in"><div class="nm-card-t"><h2 class="nm-ttl">System</h2><span class="nm-meta-t">start-up and updates</span></div><div class="nm-set">' +
        toggleRow("launch_on_startup", "Start with Windows", S.data && S.data.dev ? "Dev instance: this is not saved to Windows." : "Run the monitor when you sign in.") +
        toggleRow("check_updates", "Check for updates", "Look for a newer release once in a while.") +
        '</div><div class="nm-actions" id="set-update-actions"><button class="nm-btn" type="button" data-action="check-update">Check for updates now</button>' +
        '<button class="nm-btn" type="button" data-action="install-update" hidden>Install update</button><button class="nm-btn" type="button" data-action="skip-update" hidden>Skip this version</button></div>' +
        '<p class="nm-status" id="set-update-status" role="status"></p></div></section>' +
        "</div>" +
        '<p class="nm-status" id="set-status" role="status" aria-live="polite"></p>';
    }
    syncSettings(main);
  }

  function syncSettings(main) {
    var settings = S.settings || {};
    main = main || $("#main");
    main.querySelectorAll("[data-key]").forEach(function (el) {
      var key = el.getAttribute("data-key");
      if (!Object.prototype.hasOwnProperty.call(settings, key)) {
        return;
      }
      var value = settings[key];
      var editing = document.activeElement === el;
      if (el.type === "checkbox") {
        // A switch that has just been clicked keeps its own state until the monitor has answered (a state that is already on its
        // way could still carry the old value), but its ON / OFF word always says what the switch shows.
        if (!editing) {
          el.checked = !!value;
        }
        var lbl = main.querySelector('[data-lbl-for="' + key + '"]');
        if (lbl) {
          lbl.textContent = el.checked ? "ON" : "OFF";
        }
      } else if (editing) {
        return;
      } else if (key === "billing_day" || key === "plan_price_override") {
        el.value = value === null || value === undefined ? "" : String(value);
      } else if (key === "poll_interval_ms") {
        var has = Array.prototype.some.call(el.options, function (o) { return o.value === String(value); });
        if (!has) {
          var option = document.createElement("option");
          option.value = String(value);
          option.textContent = Math.round(Number(value) / 60000) + " MIN";
          el.appendChild(option);
        }
        el.value = String(value);
      } else {
        el.value = value === null || value === undefined ? "" : String(value);
      }
    });
    syncThemePicker(main);
    syncAlerts(main);
    syncUpdate(main);
  }

  function alertLevels() {
    var raw = S.settings && Array.isArray(S.settings.alert_thresholds) ? S.settings.alert_thresholds : [];
    return raw.filter(function (v) { return typeof v === "number"; }).sort(function (a, b) { return a - b; });
  }

  function syncAlerts(main) {
    var enabled = !S.settings || S.settings.alerts_enabled !== false;
    var chips = $("#alert-chips", main);
    if (chips) {
      var html = alertLevels().map(function (v) {
        return '<span class="nm-tchip" role="listitem"><b>' + v + '%</b><button type="button" data-action="alert-remove" data-level="' + v + '" data-fid="alert-rm-' + v +
          '" aria-label="Remove the ' + v + ' percent level"' + (enabled ? "" : " disabled") + ' data-needs-alerts="1">&times;</button></span>';
      }).join("") || '<span class="nm-note">No levels set</span>';
      if (chips.getAttribute("data-sig") !== html) {
        chips.setAttribute("data-sig", html);
        replaceHtml(chips, html);
      }
    }
    main.querySelectorAll("[data-needs-alerts]").forEach(function (el) { el.disabled = !enabled; });
    var set = $("#alerts-set", main);
    if (set) {
      set.classList.toggle("is-off", !enabled);
    }
    var qh = S.settings ? S.settings.alert_quiet_hours : null;
    var on = $('[data-quiet="on"]', main), start = $("#quiet-start", main), end = $("#quiet-end", main);
    if (on && document.activeElement !== on) {
      on.checked = !!qh;
    }
    var lbl = $("[data-lbl-quiet]", main);
    if (lbl) {
      lbl.textContent = qh ? "ON" : "OFF";
    }
    if (qh && start && document.activeElement !== start) {
      start.value = qh.start;
    }
    if (qh && end && document.activeElement !== end) {
      end.value = qh.end;
    }
    [start, end].forEach(function (el) {
      if (el) {
        el.disabled = !enabled || !qh;
      }
    });
  }

  /* The colours a theme's card shows: one per accent, however many modes a theme has (Gates has each of its seven accents in
   * a dark and a light variant, so reading its variants one by one showed the system accent twice). The mode is the one the
   * page is in when it is the chosen theme, else the theme's first. */
  function themeSwatches(theme, current) {
    var mode = current && current.id === theme.id && current.mode ? current.mode : "";
    var order = [];
    var byAccent = {};
    (theme.variants || []).forEach(function (v) {
      var accent = v.accent || accentOf(v.id);
      if (!byAccent[accent]) {
        byAccent[accent] = [];
        order.push(accent);
      }
      byAccent[accent].push(v);
    });
    return order.map(function (accent) {
      var variants = byAccent[accent];
      var chosen = variants.filter(function (v) { return mode && v.mode === mode; })[0] || variants[0];
      return hex6(chosen.colors && (chosen.colors.accent || chosen.colors.text)) || "var(--border)";
    });
  }

  function syncThemePicker(main) {
    var cards = $("#set-themes", main);
    var accents = $("#set-accents", main);
    var mode = $("#set-mode", main);
    var meta = $("#set-theme-meta", main);
    if (!cards || !accents || !mode) {
      return;
    }
    var current = currentTheme();
    var list = S.themes || [];
    var cardsHtml = list.map(function (t) {
      var on = current && current.id === t.id;
      var swatches = themeSwatches(t, current).map(function (colour) {
        return '<i style="background:' + esc(colour) + '"></i>';
      }).join("");
      return '<button type="button" class="nm-theme" data-theme-id="' + esc(t.id) + '" aria-pressed="' + (on ? "true" : "false") + '"><b>' + esc(t.name) +
        "</b><small>" + esc(t.tagline || "") + '</small><span class="nm-swatches" aria-hidden="true">' + swatches + "</span></button>";
    }).join("");
    if (cards.getAttribute("data-sig") !== cardsHtml) {
      cards.setAttribute("data-sig", cardsHtml);
      cards.innerHTML = cardsHtml || '<p class="nm-empty">Themes load with the dashboard.</p>';
    }
    var theme = current ? findTheme(current.id) : null;
    meta.textContent = current ? current.name.toUpperCase() + " \u00b7 " + String(current.variant_name || current.variant).toUpperCase() : "";
    var seen = {};
    var accentList = [];
    ((theme && theme.variants) || []).forEach(function (v) {
      var a = v.accent || accentOf(v.id);
      if (!seen[a]) {
        seen[a] = true;
        accentList.push(a);
      }
    });
    var active = current ? accentOf(current.variant) : "";
    var accentHtml = accentList.map(function (a) {
      return '<button type="button" class="nm-btn' + (a === active ? " solid" : "") + '" data-accent="' + esc(a) + '" aria-pressed="' + (a === active ? "true" : "false") + '">' +
        esc(a.toUpperCase()) + "</button>";
    }).join("");
    if (accents.getAttribute("data-sig") !== accentHtml) {
      accents.setAttribute("data-sig", accentHtml);
      accents.innerHTML = accentHtml;
    }
    var modes = theme && theme.modes && theme.modes.length ? theme.modes : [];
    mode.hidden = !modes.length;
    if (modes.length) {
      var currentMode = S.settings.theme_mode || "system";
      var modeHtml = '<span class="nm-status nm-mode-k">MODE</span><span class="nm-seg">' + modes.map(function (m) {
        return '<button type="button" data-mode="' + esc(m) + '" class="' + (currentMode === m ? "on" : "") + '" aria-pressed="' + (currentMode === m ? "true" : "false") + '">' + esc(m.toUpperCase()) + "</button>";
      }).join("") + "</span>";
      if (mode.getAttribute("data-sig") !== modeHtml) {
        mode.setAttribute("data-sig", modeHtml);
        mode.innerHTML = modeHtml;
      }
    }
  }

  function syncUpdate(main) {
    var u = S.data && S.data.update ? S.data.update : null;
    var install = $('[data-action="install-update"]', main);
    var skip = $('[data-action="skip-update"]', main);
    if (install) {
      install.hidden = !(u && u.available);
    }
    if (skip) {
      skip.hidden = !(u && u.available);
    }
    var status = $("#set-update-status", main);
    if (status) {
      status.textContent = S.update.text || (u && u.available && u.info && u.info.version ? "Version " + u.info.version + " is available." : "");
    }
  }

  // ── The update banner ───────────────────────────────────────────────────────────

  var PLAIN_VERSION = /^[0-9A-Za-z][0-9A-Za-z._+-]{0,39}$/;

  /* The newer release the monitor has found, as {version, url, self}, or null: none found, or the user skipped exactly this
   * version (in the monitor's settings, or in this window a moment ago). Read from /api/state.update, whose block is
   * {available, version, url, can_self_update, skipped, info, skip_version}. */
  function pendingUpdate() {
    var u = S.data && S.data.update ? S.data.update : null;
    if (!u || !u.available) {
      return null;
    }
    var info = u.info || {};
    var version = String(u.version || info.version || "");
    if (!PLAIN_VERSION.test(version)) {
      return null;
    }
    var skipped = u.skipped === true || (!!u.skip_version && u.skip_version === version) || S.skipped === version;
    if (skipped) {
      return null;
    }
    var url = String(u.url || info.url || "");
    return { version: version, url: /^https:\/\/github\.com\//.test(url) ? url : "", self: u.can_self_update === true || info.can_self_update === true };
  }

  /* "Claude Usage Monitor vX.Y is available", with the three things to do about it. Nothing while no update is waiting. */
  function updateBanner() {
    var found = pendingUpdate();
    if (!found) {
      return "";
    }
    return '<div class="nm-update" role="status" aria-label="Update available"><div class="nm-update-t"><b>Claude Usage Monitor v' + esc(found.version) + " is available</b>" +
      "<span>" + (found.self ? "Update now installs it and restarts the monitor." : "Update now opens the release page.") + "</span></div>" +
      '<div class="nm-actions"><button class="nm-btn solid" type="button" data-action="update-now" data-fid="update-now">Update now</button>' +
      (found.url ? '<button class="nm-btn" type="button" data-action="update-notes" data-fid="update-notes">Release notes</button>' : "") +
      '<button class="nm-btn" type="button" data-action="update-skip" data-fid="update-skip">Skip this version</button></div>' +
      '<p class="nm-status" role="status">' + esc(S.update.version === found.version ? S.update.text || "" : "") + "</p></div>";
  }

  /* The banner's three buttons. */
  function updateAction(action) {
    var found = pendingUpdate();
    if (action === "update-now") {
      installUpdate();
    } else if (action === "update-skip") {
      skipUpdate();
    } else if (action === "update-notes" && found && found.url) {
      openExternal(found.url);
    }
  }

  // ── About ───────────────────────────────────────────────────────────────────────

  /* The fonts the app bundles, by theme. All are under the SIL Open Font License 1.1, and each family's licence
   * text ships beside its files. Gates reads the fonts Windows already has and bundles none. */
  var BUNDLED_FONTS = [
    { theme: "Nostromo", families: ["Michroma", "IBM Plex Mono", "VT323"] },
    { theme: "Night City", families: ["Chakra Petch", "Rajdhani", "Share Tech Mono"] }
  ];

  function fontsCardBody() {
    return BUNDLED_FONTS.map(function (group) {
      return '<p class="nm-note nm-tier">' + esc(group.theme) + '</p><ul class="nm-supp">' +
        group.families.map(function (name) { return "<li>" + esc(name) + "</li>"; }).join("") + "</ul>";
    }).join("") +
      '<p class="nm-note nm-disc">Gates uses fonts from your own Windows install (Segoe UI Variable, Segoe Fluent Icons and Cascadia Mono, with Segoe UI and Consolas as fallbacks) and bundles none. ' +
      "The licence text of each bundled font sits beside it in the app&rsquo;s fonts folder.</p>";
  }

  /* The About page is built once, not as parts, so its update banner is kept in step by hand: the slot's markup is replaced only
   * when it changed (a banner rebuilt on every poll would drop the focus of whoever is tabbing through its buttons). display:
   * contents keeps the slot out of the layout, so the banner is a child of the page like it is on every other page. */
  function syncAboutBanner(main) {
    var slot = $("#about-update", main);
    if (!slot) {
      return;
    }
    var html = updateBanner();
    if (slot.getAttribute("data-sig") !== html) {
      slot.setAttribute("data-sig", html);
      replaceHtml(slot, html);
    }
  }

  function aboutPage(main) {
    if (S.formKey !== "about") {
      S.formKey = "about";
      S.sig = "";
      main.innerHTML = pageHead("about") +
        '<div id="about-update" style="display:contents"></div>' +
        '<div class="nm-pgrid">' +
        card({
          title: "Claude Usage Monitor", metaHtml: '<span class="nm-meta-t" id="about-version"></span>',
          body: '<p class="nm-lede">A local monitor for Claude Code plan limits. It reads the usage API with your existing Claude Code sign-in, ' +
            "and your own transcripts. It never sends a model request.</p>" +
            '<p class="nm-lede">Free and open source, under the MIT licence.</p>' +
            '<p class="nm-note nm-disc">Cost figures are ' + BASIS + ": what the same tokens would cost at the published API prices. " +
            "They are not amounts billed to your plan. Only the usage credits card shows real money.</p>"
        }) +
        card({
          title: "Links", meta: "opens in your browser",
          body: '<div class="nm-links"><a href="' + esc(LINKS.repo) + '" data-external="1">Repository on GitHub</a>' +
            '<a href="' + esc(LINKS.bug) + '" data-external="1">Issues: report a bug</a>' +
            '<a href="' + esc(LINKS.patreon) + '" data-external="1">Support on Patreon</a></div>'
        }) +
        card({
          title: "Credits", meta: "built with Claude Code",
          body: '<div class="nm-kv nm-authors"><span>AUTHORS</span><b>CHRIS CARPENTER, WITH CLAUDE</b></div>' +
            '<p class="nm-lede">Version 2 was built with Claude Code: Claude Opus 5.5, with Sonnet 5.5 and Haiku 5.5 subagents.</p>' +
            '<p class="nm-lede">Version 1 was built with Claude Sonnet 4.6.</p>'
        }) +
        card({ title: "Fonts", meta: "SIL Open Font License 1.1", body: fontsCardBody() }) +
        '<section class="nm-card wide" aria-label="Supporters"><div class="nm-card-in"><div class="nm-card-t"><h2 class="nm-ttl">Supporters</h2><span class="nm-meta-t" id="about-supporters-meta">Thank You</span></div>' +
        '<div id="about-supporters"><p class="nm-empty">Loading the supporters list&hellip;</p></div></div></section>' +
        "</div>";
    }
    syncAboutBanner(main);
    var v = $("#about-version", main);
    if (v) {
      v.textContent = S.data && S.data.version ? "VERSION " + S.data.version + (S.data.dev ? " · DEV" : "") : "VERSION --";
    }
    if (!S.supportersRequested) {
      S.supportersRequested = true;
      api("GET", "/api/supporters").then(function (res) {
        S.supporters = res.ok ? res.data : { supporters: [], error: errorText(res) };
        renderSupporters(main);
      }, function () {
        S.supporters = { supporters: [], error: "the dashboard lost its link" };
        renderSupporters(main);
      });
    }
    renderSupporters(main);
  }

  function renderSupporters(main) {
    var box = $("#about-supporters", main);
    if (!box) {
      return;
    }
    if (!S.supporters) {
      box.innerHTML = '<p class="nm-empty">Loading the supporters list&hellip;</p>';
      return;
    }
    var groups = S.supporters.tiers && S.supporters.tiers.length
      ? S.supporters.tiers
      : (S.supporters.supporters || []).length ? [{ tier: "", names: S.supporters.supporters }] : [];
    var note = S.supporters.error ? '<p class="nm-note nm-disc">Reason: ' + esc(S.supporters.error) + "</p>" : "";
    if (groups.length) {
      box.innerHTML = groups.map(function (g) {
        return (g.tier ? '<p class="nm-note nm-tier">' + esc(g.tier) + "</p>" : "") +
          '<ul class="nm-supp">' + g.names.map(function (n) { return "<li>" + esc(n) + "</li>"; }).join("") + "</ul>";
      }).join("") + note;
    } else {
      box.innerHTML = '<p class="nm-empty">' + (S.supporters.error ? "The supporters list is unavailable right now." : "No supporters listed yet.") + "</p>" + note;
    }
    var meta = $("#about-supporters-meta", main);
    if (meta) {
      meta.textContent = S.supporters.error ? "List unavailable" : "Thank You";
    }
  }

  function openExternal(url) {
    var bridge = window.pywebview && window.pywebview.api;
    if (bridge && typeof bridge.open_external === "function") {
      try {
        bridge.open_external(url);
        return;
      } catch (e) {
        /* fall through to the browser */
      }
    }
    window.open(url, "_blank", "noopener");
  }

  // ── Settings actions ────────────────────────────────────────────────────────────

  function setStatus(text, isError) {
    S.status = { text: text, error: !!isError };
    var el = $("#set-status");
    if (el) {
      el.textContent = text;
      el.className = "nm-status" + (isError ? " is-err" : "");
    }
  }

  /* An inline message under a setting, with the field marked invalid for assistive technology. */
  function fieldError(key, text) {
    var line = document.getElementById("err-" + key);
    if (line) {
      line.textContent = text || "";
      line.hidden = !text;
    }
    var fields = document.querySelectorAll('[data-key="' + key + '"]');
    if (key === "alert_thresholds") {
      fields = document.querySelectorAll("#alert-add");
    } else if (key === "alert_quiet_hours") {
      fields = document.querySelectorAll("#quiet-start, #quiet-end");
    }
    Array.prototype.forEach.call(fields, function (el) {
      if (text) {
        el.setAttribute("aria-invalid", "true");
      } else {
        el.removeAttribute("aria-invalid");
      }
    });
  }

  /* Posts a change. Resolves true when it was saved. errKey names the line the server's refusal shows under. */
  function saveSettings(patch, label, errKey) {
    setStatus("SAVING " + label + "\u2026", false);
    return api("POST", "/api/settings", patch).then(function (res) {
      if (res.ok && res.data) {
        S.settings = res.data.settings || S.settings;
        setStatus("SAVED " + label, false);
        if (errKey) {
          fieldError(errKey, "");
        }
        if (Object.prototype.hasOwnProperty.call(patch, "billing_day") || Object.prototype.hasOwnProperty.call(patch, "plan_price_override")) {
          invalidateStats();
        }
        poll(true);
        syncSettings($("#main"));
        return true;
      }
      var why = errorText(res);
      setStatus("NOT SAVED: " + why.toUpperCase(), true);
      if (errKey) {
        fieldError(errKey, "Not saved: " + why + ".");
      }
      syncSettings($("#main"));
      return false;
    }, function () {
      setStatus("NOT SAVED: THE DASHBOARD LOST ITS LINK", true);
      if (errKey) {
        fieldError(errKey, "Not saved: the dashboard lost its link to the monitor.");
      }
      return false;
    });
  }

  function labelFor(key) {
    return String(key).replace(/_/g, " ").toUpperCase();
  }

  function applyThemeChoice(themeId, variantId) {
    var patch = { theme_id: themeId, theme_variant: variantId };
    var theme = findTheme(themeId);
    var name = theme ? theme.name : themeId;
    saveSettings(patch, name.toUpperCase());
  }

  function quietChange(el) {
    var kind = el.getAttribute("data-quiet");
    var main = $("#main");
    var start = $("#quiet-start", main).value, end = $("#quiet-end", main).value;
    if (kind === "on") {
      if (el.checked) {
        saveSettings({ alert_quiet_hours: { start: start || "22:00", end: end || "07:00" } }, "QUIET HOURS", "alert_quiet_hours");
      } else {
        saveSettings({ alert_quiet_hours: null }, "QUIET HOURS", "alert_quiet_hours");
      }
      return;
    }
    if (!S.settings.alert_quiet_hours) {
      return;
    }
    if (!/^\d\d:\d\d$/.test(start) || !/^\d\d:\d\d$/.test(end)) {
      fieldError("alert_quiet_hours", "Enter both times as hours and minutes.");
      return;
    }
    saveSettings({ alert_quiet_hours: { start: start, end: end } }, "QUIET HOURS", "alert_quiet_hours");
  }

  function addLevel() {
    var input = $("#alert-add");
    var raw = String(input.value).trim();
    var levels = alertLevels();
    if (!/^\d{1,3}$/.test(raw) || +raw < 1 || +raw > 100) {
      fieldError("alert_thresholds", "Enter a whole number from 1 to 100.");
      return;
    }
    var n = parseInt(raw, 10);
    if (levels.indexOf(n) >= 0) {
      fieldError("alert_thresholds", n + "% is already a level.");
      return;
    }
    if (levels.length >= 5) {
      fieldError("alert_thresholds", "At most five levels. Remove one first.");
      return;
    }
    levels.push(n);
    levels.sort(function (a, b) { return a - b; });
    saveSettings({ alert_thresholds: levels }, "ALERT LEVELS", "alert_thresholds").then(function (ok) {
      if (ok) {
        input.value = "";
      }
    });
  }

  function removeLevel(level) {
    var levels = alertLevels().filter(function (v) { return v !== level; });
    if (!levels.length) {
      fieldError("alert_thresholds", "At least one level is needed. Turn \u201cLevel reached\u201d off to stop these alerts.");
      return;
    }
    saveSettings({ alert_thresholds: levels }, "ALERT LEVELS", "alert_thresholds");
  }

  function settingsChange(event) {
    var el = event.target;
    if (el.getAttribute && el.hasAttribute("data-quiet")) {
      quietChange(el);
      return;
    }
    var key = el.getAttribute && el.getAttribute("data-key");
    if (!key) {
      return;
    }
    var value;
    if (el.type === "checkbox") {
      value = el.checked;
    } else if (key === "billing_day") {
      value = el.value === "" ? null : parseInt(el.value, 10);
    } else if (key === "plan_price_override") {
      if (el.value === "") {
        value = null;
      } else {
        value = Number(el.value);
        if (!isFinite(value) || value <= 0 || value > 10000) {
          fieldError(key, "Enter a price above 0 and up to 10,000, or leave it blank to use the detected price.");
          return;
        }
      }
    } else if (key === "poll_interval_ms") {
      value = parseInt(el.value, 10);
    } else {
      value = el.value;
    }
    var patch = {};
    patch[key] = value;
    saveSettings(patch, labelFor(key), key);
  }

  function settingsClick(event) {
    var target = event.target.closest ? event.target.closest("button") : null;
    if (!target) {
      return;
    }
    if (target.hasAttribute("data-theme-id")) {
      var t = findTheme(target.getAttribute("data-theme-id"));
      if (t) {
        applyThemeChoice(t.id, accentOf(t.default_variant));
      }
      return;
    }
    if (target.hasAttribute("data-accent")) {
      var accent = target.getAttribute("data-accent");
      saveSettings({ theme_variant: accent }, accent.toUpperCase());
      return;
    }
    if (target.hasAttribute("data-mode")) {
      var mode = target.getAttribute("data-mode");
      saveSettings({ theme_mode: mode }, "MODE " + mode.toUpperCase());
      return;
    }
    var action = target.getAttribute("data-action");
    if (action === "alert-add") {
      addLevel();
    } else if (action === "alert-remove") {
      removeLevel(parseInt(target.getAttribute("data-level"), 10));
    } else if (action === "refresh") {
      api("POST", "/api/refresh").then(function (res) {
        setStatus(res.ok ? "REFRESH REQUESTED" : failedText("REFRESH FAILED", res), !res.ok);
        poll(true);
      }, function () {
        setStatus("REFRESH FAILED: THE DASHBOARD LOST ITS LINK", true);
      });
    } else if (action === "check-update") {
      S.update = { text: "CHECKING\u2026" };
      api("POST", "/api/update/check").then(function (res) {
        S.update = { text: checkUpdateText(res) };
        api("GET", "/api/state").then(function (r) { if (r.ok) { S.data = r.data; S.etag = r.etag; } syncSettings($("#main")); });
      }, function () {
        S.update = { text: "CHECK FAILED: THE DASHBOARD LOST ITS LINK" };
        syncSettings($("#main"));
      });
    } else if (action === "install-update") {
      installUpdate();
    } else if (action === "skip-update") {
      skipUpdate();
    }
  }

  /* Update now. The monitor says what it did. "browser": it opened the release page (a source run, or a release it will not
   * install silently). "installer": it started the checked silent update, and only restarts if the download passes the check.
   * One sentence per text: the Gates theme writes sentence case and capitalises only the first letter of a string. */
  function installUpdate() {
    var found = pendingUpdate();
    var forVersion = found ? found.version : "";                // the banner shows this status only while it is about that release
    S.update = { text: "INSTALLING\u2026", version: forVersion };
    refreshUpdateViews();
    api("POST", "/api/update/apply").then(function (res) {
      var mode = res.ok && res.data && res.data.result ? res.data.result.mode : "";
      var text;
      if (!res.ok) {
        text = failedText("UPDATE FAILED", res);
      } else if (mode === "browser") {
        text = "OPENED THE RELEASE PAGE IN YOUR BROWSER.";
      } else if (mode === "installer") {
        text = "DOWNLOADING AND CHECKING THE UPDATE; IF IT CHECKS OUT, THE MONITOR CLOSES AND RESTARTS BY ITSELF.";
      } else {
        text = "NO UPDATE WAS STARTED.";
      }
      S.update = { text: text, version: forVersion };
      refreshUpdateViews();
    }, function () {
      S.update = { text: "UPDATE FAILED: THE DASHBOARD LOST ITS LINK", version: forVersion };
      refreshUpdateViews();
    });
  }

  /* Skip this version: the banner goes at once, and comes back only for a newer one. */
  function skipUpdate() {
    var found = pendingUpdate();
    var forVersion = found ? found.version : "";
    if (found) {
      S.skipped = found.version;
    }
    S.update = { text: "THIS VERSION WILL BE SKIPPED.", version: forVersion };
    refreshUpdateViews();
    api("POST", "/api/update/skip").then(function (res) {
      if (!res.ok) {
        S.skipped = "";
        S.update = { text: failedText("COULD NOT SKIP", res), version: forVersion };
      }
      refreshUpdateViews();
      poll(true);
    }, function () {
      S.skipped = "";
      S.update = { text: "COULD NOT SKIP: THE DASHBOARD LOST ITS LINK", version: forVersion };
      refreshUpdateViews();
    });
  }

  /* The update banner (on every page but Settings) and the Settings buttons say what S.update and the state now say. */
  function refreshUpdateViews() {
    if (S.page === "settings") {
      syncSettings($("#main"));
    } else {
      renderPage();
    }
  }

  // ── Export ──────────────────────────────────────────────────────────────────────

  function pywebviewBridge() {
    return window.pywebview && window.pywebview.api ? window.pywebview.api : null;
  }

  /* The status line: the words in capitals, a file name or path exactly as it is. */
  function exportStatusHtml() {
    var x = S.exportStatus;
    return esc(x.text) + (x.path ? ' <span class="nm-path">' + esc(x.path) + "</span>" : "");
  }

  function setExportStatus(text, isError, path) {
    S.exportStatus = { text: text, path: path || "", error: !!isError };
    var el = document.getElementById("export-status");
    if (el) {
      el.innerHTML = exportStatusHtml();
      el.className = "nm-status" + (isError ? " is-err" : "");
    }
  }

  /* No bridge (a plain browser): fetch with the token header and hand the file to the browser. */
  function downloadExport(fmt) {
    return fetch("/api/export?format=" + encodeURIComponent(fmt), {
      headers: { "X-Monitor-Token": TOKEN }, cache: "no-store", credentials: "omit"
    }).then(function (res) {
      if (!res.ok) {
        throw new Error("the monitor answered " + res.status);
      }
      var named = /filename="([A-Za-z0-9._-]+)"/.exec(res.headers.get("Content-Disposition") || "");
      var name = named ? named[1] : "claude-usage-daily." + fmt;
      return res.blob().then(function (blob) {
        var url = URL.createObjectURL(blob);
        var link = document.createElement("a");
        link.href = url;
        link.download = name;
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.setTimeout(function () { URL.revokeObjectURL(url); }, 5000);
        return name;
      });
    });
  }

  function exportData(fmt) {
    if (fmt !== "csv" && fmt !== "json") {
      return;
    }
    var label = fmt.toUpperCase();
    setExportStatus("PREPARING " + label + "\u2026", false);
    var bridge = pywebviewBridge();
    if (bridge && typeof bridge.save_export === "function") {
      var call;
      try {
        call = Promise.resolve(bridge.save_export(fmt));
      } catch (e) {
        call = Promise.reject(e);
      }
      call.then(function (result) {
        if (result && result.ok) {
          setExportStatus("SAVED " + label + " TO", false, String(result.path || "the file you chose"));
        } else if (result && !result.error) {
          setExportStatus("EXPORT CANCELLED", false);
        } else {
          setExportStatus("NOT SAVED:", true, String(result && result.error ? result.error : "unknown error"));
        }
      }, function () {
        setExportStatus("NOT SAVED: THE SAVE DIALOG COULD NOT OPEN", true);
      });
      return;
    }
    downloadExport(fmt).then(function (name) {
      setExportStatus("DOWNLOADED", false, name);
    }, function (err) {
      setExportStatus("NOT SAVED:", true, String(err && err.message ? err.message : "the download failed"));
    });
  }

  // ── Theme application ───────────────────────────────────────────────────────────

  function pushChrome(theme) {
    var bridge = pywebviewBridge();
    if (!bridge || typeof bridge.set_chrome !== "function" || !theme) {
      return;
    }
    var css = theme.css_vars || {};
    var caption = hex6(css["--bg"]);
    var text = hex6(css["--text"] || css["--text-bright"]);
    var border = hex6(css["--border"]);
    if (!caption || !text || !border) {
      return;
    }
    try {
      bridge.set_chrome(caption, text, border, theme.mode !== "light");
    } catch (e) {
      /* the native caption is cosmetic */
    }
  }

  /* A theme's stylesheet decides --chart-pips and every size, so what was drawn before it loaded is drawn again. */
  function redrawAfterStyles() {
    S.sig = "";
    S.repaint = true;
    if (S.data) {
      renderPage();
    }
  }

  function loadThemeCss(id, hasCss) {
    var link = document.getElementById("theme-css");
    if (!link || link.getAttribute("data-theme") === id) {
      return;
    }
    link.setAttribute("data-theme", id);
    link.onload = redrawAfterStyles;
    link.onerror = function () {
      link.onerror = null;
      link.setAttribute("href", FALLBACK_CSS);
    };
    link.setAttribute("href", hasCss ? "/static/themes/" + encodeURIComponent(id) + ".css" : FALLBACK_CSS);
  }

  function startThemeFx(id) {
    var fx = window.CUM_THEME_FX && window.CUM_THEME_FX[id];
    if (fx && typeof fx.start === "function" && document.documentElement.getAttribute("data-theme") === id) {
      fx.start();
    }
  }

  function loadThemeScript(id, hasJs) {
    if (!hasJs) {
      return;
    }
    if (S.loadedScripts[id]) {
      startThemeFx(id);
      return;
    }
    S.loadedScripts[id] = "loading";
    var script = document.createElement("script");
    script.src = "/static/themes/" + encodeURIComponent(id) + ".js";
    script.onload = function () {
      S.loadedScripts[id] = "ready";
      startThemeFx(id);
      if (S.enterPending && S.data) {
        renderPage();                                  // an entrance that was waiting for this script
      }
    };
    script.onerror = function () {
      S.loadedScripts[id] = "none";
    };
    document.head.appendChild(script);
  }

  function applyTheme(theme) {
    if (!theme || !theme.css_vars) {
      return;
    }
    var key = [theme.id, theme.variant, theme.mode, JSON.stringify(theme.css_vars)].join("|");
    if (key === S.themeKey) {
      return;
    }
    S.themeKey = key;
    if (S.lastTheme && S.lastTheme.id !== theme.id) {
      // another theme takes the page over: the old one's motion ends, every card is laid again and an entrance is due
      var host = document.getElementById("main");
      if (host) {
        cancelFx(host);
      }
      wantEnter();
      S.repaint = true;
    }
    S.lastTheme = theme;
    var root = document.documentElement;
    S.appliedVars.forEach(function (name) { root.style.removeProperty(name); });
    S.appliedVars = Object.keys(theme.css_vars);
    S.appliedVars.forEach(function (name) { root.style.setProperty(name, String(theme.css_vars[name])); });
    root.setAttribute("data-theme", theme.id);
    root.setAttribute("data-variant", theme.variant);
    root.style.colorScheme = theme.mode === "light" ? "light" : "dark";
    var assets = theme.assets || { css: true, js: false };
    loadThemeCss(theme.id, !!assets.css);
    loadThemeScript(theme.id, !!assets.js);
    pushChrome(theme);
    S.sig = "";
  }

  // ── Rendering ───────────────────────────────────────────────────────────────────

  function currentPage() {
    var key = (window.location.hash || "").replace(/^#/, "");
    key = PAGE_ALIASES[key] || key;
    return PAGE_KEYS.indexOf(key) >= 0 ? key : "overview";
  }

  function renderChrome() {
    var s = st();
    var stale = !!(s && s.status !== "live");
    var info = s ? (STATUS[s.status] || STATUS.loading) : STATUS.loading;
    var linkDown = S.linkOk === false;
    var text = linkDown ? "NO LINK" : info.text;
    var up = document.getElementById("uplink");
    var dot = document.getElementById("uplink-dot");
    var upText = document.getElementById("uplink-text");
    if (upText) {
      upText.textContent = text;
    }
    if (up) {
      up.className = "nm-uplink" + (linkDown || !info.ok ? " is-down" : "");
    }
    if (dot) {
      dot.className = "nm-alive" + (linkDown || !info.ok ? " is-off" : "");
    }
    var screen = document.getElementById("screen");
    if (screen) {
      screen.classList.toggle("is-stale", linkDown || stale && s.status !== "paused");
    }
    renderAge();
    renderHealth();
    var refresh = document.getElementById("refresh");
    if (refresh) {
      refresh.disabled = linkDown;
    }
  }

  function ageMs() {
    var u = usage();
    var when = parseTime(u && u.fetched_at) || parseTime(st() && st().updated_at);
    return when === null ? null : Date.now() - when;
  }

  function renderAge() {
    var a = ageMs();
    var text = a === null ? "--" : fmtAge(a);
    var el = document.getElementById("data-age");
    if (el) {
      el.textContent = "DATA AGE " + text;
    }
    var updated = document.querySelector("#health [data-updated]");
    if (updated) {
      updated.textContent = a === null ? "--" : text + " AGO";
    }
  }

  /* Rebuilt only when its content changes, so the alive dot keeps its blink across the 1 s ticks. */
  function renderHealth() {
    var box = document.getElementById("health");
    if (!box) {
      return;
    }
    var s = st();
    var ingest = s && s.ingest;
    var sources = ingest
      ? (ingest.running ? "INGEST " + Math.floor(100 * (ingest.files_done || 0) / Math.max(1, ingest.files_total || 1)) + "%"
        : fmtInt(ingest.files_total) + " OK")
      : "--";
    var unpriced = s && s.unpriced_models ? s.unpriced_models.length : 0;
    var link = S.linkOk === false ? "NO LINK" : (s ? (STATUS[s.status] || STATUS.loading).text.replace("UPLINK ", "") : "--");
    var live = S.linkOk !== false && !!(s && s.status === "live");
    var signature = [link, sources, unpriced, live].join("|");
    if (box.getAttribute("data-sig") !== signature) {
      box.setAttribute("data-sig", signature);
      box.innerHTML = '<div class="nm-kick">DATA HEALTH</div>' +
        '<div class="nm-kv"><span>LINK</span><b class="' + (live ? "ok" : "is-down") + '"><i class="nm-alive' + (live ? "" : " is-off") + '"></i>' + esc(link) + "</b></div>" +
        '<div class="nm-kv"><span>UPDATED</span><b data-updated="1">--</b></div>' +
        '<div class="nm-kv"><span>SOURCES</span><b>' + esc(sources) + "</b></div>" +
        '<div class="nm-kv"><span>UNPRICED</span><b>' + esc(String(unpriced)) + "</b></div>";
    }
    renderAge();
  }

  function renderVariants() {
    var box = document.getElementById("variants");
    var label = document.getElementById("phos-label");
    if (!box) {
      return;
    }
    var theme = currentTheme();
    var meta = theme ? findTheme(theme.id) : null;
    if (label) {
      label.textContent = theme && theme.id === "nostromo" ? "PHOSPHOR" : "ACCENT";
    }
    if (!meta) {
      box.innerHTML = "";
      return;
    }
    var seen = {};
    var accents = [];
    (meta.variants || []).forEach(function (v) {
      var a = v.accent || accentOf(v.id);
      if (!seen[a]) {
        seen[a] = true;
        accents.push(a);
      }
    });
    var active = theme ? accentOf(theme.variant) : "";
    var html = accents.slice(0, 8).map(function (a) {
      return '<button type="button" data-accent="' + esc(a) + '" class="' + (a === active ? "on" : "") + '" aria-pressed="' + (a === active ? "true" : "false") +
        '" title="' + esc(a) + '">' + esc(ACCENT_ABBR[a] || a.slice(0, 3).toUpperCase()) + "</button>";
    }).join("");
    if (box.getAttribute("data-sig") !== html) {
      box.setAttribute("data-sig", html);
      box.innerHTML = html;
    }
  }

  function markNav(key) {
    document.querySelectorAll("#menu a").forEach(function (a) {
      var on = a.getAttribute("data-page") === key;
      a.classList.toggle("on", on);
      if (on) {
        a.setAttribute("aria-current", "page");
      } else {
        a.removeAttribute("aria-current");
      }
    });
  }

  /* Sets the markup of a container and puts keyboard focus back on the control that had it (matched by data-fid). */
  function replaceHtml(el, html) {
    var active = document.activeElement;
    var fid = active && el.contains(active) && active.getAttribute ? active.getAttribute("data-fid") : null;
    var top = el.scrollTop;
    el.innerHTML = html;
    el.scrollTop = top;
    if (fid) {
      var again = el.querySelectorAll("[data-fid]");
      for (var i = 0; i < again.length; i++) {
        if (again[i].getAttribute("data-fid") === fid) {
          again[i].focus({ preventScroll: true });
          break;
        }
      }
    }
  }

  var PAGE_MODELS = { overview: overviewPage, limits: limitsPage, cost: costPage, efficiency: efficiencyPage, history: historyPage };

  /* The parts of a page, built from what has arrived and from placeholders for what has not (S.building lets the state
   * accessors stand in for a monitor that has not answered). The charts the parts ask for are kept in S.charts. */
  function buildModel(key) {
    S.nextCharts = {};
    S.building = true;
    try {
      return (PAGE_MODELS[key] || overviewPage)();
    } finally {
      S.building = false;
      S.charts = S.nextCharts;
    }
  }

  /* Draws the page the address names. It opens at once as a full set of cards: a card whose numbers have not arrived is a
   * placeholder (see "Placeholders"), and each card fills in place when its report lands. Opening a page (or the first
   * render, or a new theme) makes an entrance due (playEnterIfDue); a card that fills in later calls the theme's arrive. */
  function renderPage() {
    var key = currentPage();
    var main = document.getElementById("main");
    if (!main) {
      return;
    }
    markNav(key);
    var opened = key !== S.page;
    if (opened) {
      cancelFx(main);
      S.page = key;
      S.formKey = null;
      S.sig = "";
      main.innerHTML = "";
      main.scrollTop = 0;
      if (S.tipApi) {
        S.tipApi.hide();
      }
      wantEnter();
      var alias = "#" + key;
      if (window.location.hash && window.location.hash !== alias && PAGE_ALIASES[window.location.hash.slice(1)] === key && window.history && window.history.replaceState) {
        window.history.replaceState(null, "", alias);          // an old link (#nerds) is the page's own address from here on
      }
    }
    ensureStats();
    if (key === "settings" || key === "about") {
      if (key === "settings") {
        settingsPage(main);
      } else {
        aboutPage(main);
      }
      afterRender(key, main);
      playEnterIfDue(main, key);
      return;
    }
    S.formKey = null;
    if (CHARTS) {
      CHARTS.refresh();
    }
    var model = buildModel(key);
    var report = paint(main, model, S.repaint);
    S.repaint = false;
    if (report.updated.length || report.added.length || opened) {
      main.classList.add("fx-quiet");                            // a card that was just replaced under a resting pointer must not play its hover
    }
    hydrateCharts(main);
    syncUpdating(main);
    afterRender(key, main);
    announce(main, key, opened, report);
    playEnterIfDue(main, key);
  }

  function render() {
    renderChrome();
    renderVariants();
    renderPage();
  }

  function addSample(value) {
    var n = num(value);
    if (n === null) {
      return;
    }
    S.samples.push(n);
    if (S.samples.length > 24) {
      S.samples.shift();
    }
  }

  function ensureThemes() {
    if (S.themesRequested) {
      return;
    }
    S.themesRequested = true;
    api("GET", "/api/themes").then(function (res) {
      if (res.ok && res.data && res.data.themes) {
        S.themes = res.data.themes;
        render();
      } else {
        S.themesRequested = false;
      }
    }, function () {
      S.themesRequested = false;
    });
  }

  function onState() {
    var s = st();
    if (s && s.updated_at && s.updated_at !== S.lastUpdatedAt) {
      S.lastUpdatedAt = s.updated_at;
      if (s.burn) {
        addSample(s.burn.tokens_per_min);
      }
    }
    S.settings = S.data.settings || {};
    var wasReduced = document.documentElement.getAttribute("data-reduce-motion") === "1";
    document.documentElement.setAttribute("data-reduce-motion", S.settings.reduce_motion ? "1" : "0");
    if (S.settings.reduce_motion && !wasReduced) {
      cancelFx(document.getElementById("main"));       // the setting just came on: whatever moves ends in its final state
    }
    applyTheme(S.data.theme);
    ensureThemes();
    render();
  }

  // ── Polling ─────────────────────────────────────────────────────────────────────

  var pollTimer = null;
  var pollAgain = false;

  function schedule() {
    window.clearTimeout(pollTimer);
    pollTimer = window.setTimeout(poll, POLL_MS);
  }

  /* force: fetch now and ignore the cached ETag. Used after a change, so the new state shows at once. */
  function poll(force) {
    window.clearTimeout(pollTimer);
    if (force === true) {
      S.etag = null;
    }
    if (S.inFlight) {
      if (force === true) {
        pollAgain = true;
      }
      return;
    }
    if (document.visibilityState === "hidden" && force !== true) {
      return;
    }
    S.inFlight = true;
    api("GET", "/api/state", null, S.etag).then(function (res) {
      if (res.status === 304) {
        S.linkOk = true;
      } else if (res.ok && res.data) {
        S.linkOk = true;
        S.etag = res.etag;
        S.data = res.data;
        onState();
      } else {
        S.linkOk = false;
        renderChrome();
      }
      afterRequest();
    }, function () {
      S.linkOk = false;
      renderChrome();
      afterRequest();
    });
  }

  function afterRequest() {
    S.inFlight = false;
    if (pollAgain) {
      pollAgain = false;
      poll(true);
      return;
    }
    schedule();
  }

  function tick() {
    renderAge();
    renderHealth();
    if (S.data) {
      ensureStats();
    }
    var minute = String(Math.floor(Date.now() / 60000));
    if (minute !== S.minuteKey) {
      S.minuteKey = minute;
      if (S.data) {
        renderPage();
      }
    }
  }

  // ── Start-up ────────────────────────────────────────────────────────────────────

  function onSegment(name, value) {
    var map = { "cost-window": "costWindow", "folder-mode": "folderMode", "efficiency-window": "efficiencyWindow", "cal-metric": "calMetric", "matrix-metric": "matrixMetric" };
    if (!map[name]) {
      return;
    }
    S.ui[map[name]] = value;
    savePrefs();
    renderPage();
  }

  function installBaseStyle() {
    if (document.getElementById("cum-app-style")) {
      return;
    }
    var style = document.createElement("style");
    style.id = "cum-app-style";
    style.textContent = BASE_CSS;
    document.head.insertBefore(style, document.head.firstChild);
  }

  function init() {
    installBaseStyle();
    var main = document.getElementById("main");
    var variants = document.getElementById("variants");
    var refresh = document.getElementById("refresh");
    var tipEl = document.getElementById("tip");
    loadPrefs();
    window.addEventListener("hashchange", function () {
      renderPage();
      if (main) {
        main.focus({ preventScroll: true });
      }
    });
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") {
        if (S.enterPending && S.data) {
          renderPage();                                // a window that was hidden when the page opened: its entrance plays now
        }
        poll();
        ensureStats();
      }
    });
    window.addEventListener("pywebviewready", function () {
      pushChrome(S.lastTheme);
    });
    if (refresh) {
      refresh.addEventListener("click", function () {
        api("POST", "/api/refresh").then(function () {
          poll(true);
          invalidateStats();
        });
      });
    }
    if (variants) {
      variants.addEventListener("click", function (event) {
        var button = event.target.closest ? event.target.closest("button[data-accent]") : null;
        if (button) {
          saveSettings({ theme_variant: button.getAttribute("data-accent") }, button.getAttribute("data-accent").toUpperCase());
        }
      });
    }
    if (main) {
      if (CHARTS && tipEl) {
        S.tipApi = CHARTS.bind(main, tipEl);
      }
      main.addEventListener("change", function (event) {
        if (S.formKey === "settings") {
          settingsChange(event);
        }
      });
      // A card that is replaced under a resting pointer meets a pointer that is "over" it without having moved: the theme's hover
      // effects wait (main.fx-quiet) for the first movement or scroll after a render.
      var wake = function () { main.classList.remove("fx-quiet"); };
      main.addEventListener("pointermove", wake, { passive: true });
      main.addEventListener("wheel", wake, { passive: true });
      main.addEventListener("keydown", function (event) {
        if (event.key === "Enter" && event.target && event.target.id === "alert-add" && S.formKey === "settings") {
          event.preventDefault();
          addLevel();
        }
      });
      main.addEventListener("click", function (event) {
        var from = event.target;
        var closest = function (selector) { return from && from.closest ? from.closest(selector) : null; };
        var link = closest("a[data-external]");
        if (link) {
          event.preventDefault();
          openExternal(link.href);
          return;
        }
        var segment = closest("button[data-seg]");
        if (segment) {
          onSegment(segment.getAttribute("data-seg"), segment.getAttribute("data-value"));
          return;
        }
        var dot = closest("button[data-cmp]");
        if (dot) {
          S.ui.cmp = parseInt(dot.getAttribute("data-cmp"), 10) || 0;
          updateCompare();
          return;
        }
        var exporter = closest('button[data-action="export"]');
        if (exporter) {
          exportData(exporter.getAttribute("data-format"));
          return;
        }
        var banner = closest(".nm-update button[data-action]");
        if (banner) {
          updateAction(banner.getAttribute("data-action"));
          return;
        }
        if (S.formKey === "settings") {
          settingsClick(event);
        }
      });
      if (window.ResizeObserver) {
        var pending = 0;
        new window.ResizeObserver(function () {
          if (pending) {
            return;
          }
          pending = window.requestAnimationFrame(function () {
            pending = 0;
            hydrateCharts(main);
          });
        }).observe(main);
      }
    }
    window.setInterval(tick, 1000);
    renderPage();                                      // the page opens at once as placeholders; the first state fills it in
    poll();
  }

  /* Test hook. tests/test_webui.py loads this file in node with window.__CUM_TEST__ set and reads the helpers it
   * needs from there; the page is then not started. Nothing in the app sets it (the page allows no inline script),
   * so in the dashboard this is a no-op. */
  if (window.__CUM_TEST__) {
    window.__CUM_TEST__.api = {
      S: S, PACE_BAND: PACE_BAND, esc: esc, num: num, fmtPct: fmtPct, fmtRate: fmtRate, fmtUsd: fmtUsd, fmtMoney: fmtMoney,
      fmtMult: fmtMult, paceOf: paceOf, ratioCells: ratioCells, ratioNote: ratioNote, ratioScale: ratioScale,
      costNumbers: costNumbers, modelMixCard: modelMixCard, spendCardBody: spendCardBody, sessionsTable: sessionsTable,
      renderHealth: renderHealth, weeklyStory: weeklyStory, effectivePlanPrice: effectivePlanPrice,
      planFromState: planFromState, pipelineCard: pipelineCard, topModels: topModels, railHTML: railHTML,
      limitCard: limitCard, remainHTML: remainHTML, buildModel: buildModel, statEntry: statEntry, entryState: entryState,
      averageRateForecast: averageRateForecast, pacingChart: pacingChart, limitFacts: limitFacts, pendingUpdate: pendingUpdate,
      updateBanner: updateBanner, themeSwatches: themeSwatches, currentPage: currentPage, skelCost: skelCost,
      skelEfficiency: skelEfficiency, skelHistory: skelHistory, errorSwap: errorSwap, tagCard: tagCard, cardPart: cardPart,
      playEnterIfDue: playEnterIfDue, wantEnter: wantEnter, loadPrefs: loadPrefs, PAGE_KEYS: PAGE_KEYS, clockText: clockText,
      errorText: errorText, failedText: failedText, checkUpdateText: checkUpdateText, settingsClick: settingsClick,
      updateAction: updateAction, installUpdate: installUpdate, skipUpdate: skipUpdate, aboutPage: aboutPage
    };
    return;
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
