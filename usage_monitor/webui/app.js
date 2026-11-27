/* app.js: the dashboard client. Polls /api/state every 2 s while the window is visible (ETag, so an
 * unchanged state costs a 304), routes on the hash (#overview #limits #cost #efficiency #history #settings
 * #about; #nerds, the old name of Efficiency, still opens it), and renders from the live MonitorState plus the
 * statistics reports (/api/analytics/<name>, /api/export). Settings apply at once through POST /api/settings.
 * Dependencies: charts.js (window.MonitorCharts). The theme's own script (themes/<id>.js, which registers
 * window.USAGE_MONITOR_THEME_FX[id]) and the motion layer (motion.js) are optional: without them the page is complete and still.
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
 * one (use text-transform). The words of the overview and limits cards and of the banners are the catalog's (#98), in natural
 * case: Nostromo and Night City set the cards' approved capitals with text-transform. What stays in capitals in the markup is
 * data and figures: English's limit labels (identity strings), names (FMT.upper, #91), dates and the badges' durations. An
 * element a theme's entrance moves (.nm-tag .nm-ttl .nm-meta-t .nm-mrow .k, .nm-kv-grid b) holds its words as its own text.
 * On another language's page the element itself carries lang="en" for English's words and translate="no" for a name, never a
 * span inside it; an English page's markup has neither.
 *
 * Shell       .nm-screen > .nm-content > header.nm-bar (.nm-brand .nm-bar-r .nm-uplink .nm-alive
 *             .nm-refresh) + .nm-app > nav.nm-nav (.nm-kick .nm-menu a.on .nm-health .nm-kv .nm-phos
 *             .nm-sw) + main.nm-main; overlays .nm-boot .nm-scan .nm-vig; #tip.nm-tip is the one tooltip.
 *             Its words, the header's and the sidebar's are the catalog's (#98: shell.*, header.*, nav.*), in natural case: a
 *             theme sets them in capitals with text-transform. Gates has words of its own (page.gates.*) where it says
 *             something else ("Connecting" for ACQUIRING). The product's name is written in the shell and kept as it is.
 * Page        .nm-head > h1.nm-h1 (+ .nm-cur) + p.nm-sub, the catalog's heading.<page>.*, each the element's own text (a
 *             theme's entrance types it), lang="en" on the element while it is English on another language's page;
 *             .nm-msg is a status banner (role=status);
 *             .nm-hazard the critical stripe. The overview body is .nm-grid (3 columns, 3 rows); every
 *             other page is .nm-pgrid (12 columns) whose children take .span-3 .span-4 .span-5 .span-6
 *             .span-7 .span-8 or .wide (all 12); below 1000 px everything is one column.
 *             .nm-colstack stacks two cards in one grid cell; .nm-toolbar (.nm-toolbar-k, .nm-toolbar-n)
 *             is a full-width row of controls.
 * Update      .nm-update[role=status] > .nm-update-t (b: "Claude Usage Monitor vX.Y is available", span: what Update now
 *             does) + .nm-actions (button.nm-btn[data-action=update-now | update-notes | update-skip]) + .nm-status.
 *             The first banner of every page but Settings (which has the same buttons in its System card), present only while
 *             /api/state reports a release that was not skipped. Its words and the System card's update rows, buttons and line
 *             are the catalog's (#97: banner.update.*, settings.update.*), in natural case: a theme sets them in capitals with
 *             text-transform. A line that is English on another language's page carries lang="en".
 * Progress    A running update on Settings (#58): .nm-uprog#set-update-progress in the System card, hidden while there is none
 *             to show; .is-warn while the download stalls, .is-crit once the update failed or the dashboard lost the monitor.
 *             Made of the guard card's parts: .nm-verdict > three .nm-chip steps (Download, Check, Restart), each with
 *             data-step=done|now|next|stalled|failed and the tones of the guard's chips (done is-ok, now acc, next neutral and
 *             dimmed as the theme dims a disabled control, stalled solid is-warn, failed solid is-crit); div[role=progressbar] >
 *             .nm-cells (data-pending while the size is not known; .hot once it failed); .nm-story (the sizes, as written; a line
 *             high when it has none); .nm-status (what it is doing). A theme tells the three steps apart with what it has and adds
 *             no motion: an unknown size is the theme's own pending bar, which holds still while the download stalls.
 * Help        .nm-msg.nm-help[role=region] is the status banner grown into a help panel, for the two statuses only the user can fix
 *             (no_credentials: the steps to sign in; stale: what to do about an expired sign-in). b (the heading) + p + ol (the steps;
 *             a li may hold a .nm-cmd[data-command] > code.nm-path + button.nm-btn[data-action=copy-command][data-command]; the stale
 *             panel has one after its p) + .nm-actions (button.nm-btn.solid [data-action=check-again], .nm-links > a[data-external])
 *             + p.nm-status[role=status] (what a check found). A code is a command the user types; it keeps its case.
 * Card        section.nm-card[data-card=<key>][.wide|.span-N][.is-warn|.is-crit|.is-real] > .nm-card-in > .nm-card-t
 *             (.nm-tag | h2.nm-ttl, and .nm-meta-t) + the body. .is-real marks the one card of real money.
 * Values      .nm-big (sizes .h .m .s), .nm-big-row, .nm-row2 (.is-base), .nm-facts, .nm-chip (.solid
 *             .is-ok .is-warn .is-crit; [data-tone=ok|warn|alert|acc|neutral]), .nm-verdict, .nm-note, .nm-disc (a disclaimer), .nm-lede,
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
 *             .d description), .nm-actions, .nm-status (.is-err), .nm-path (a command or a path: lang="en" translate="no", never changed case),
 *             .nm-err (inline validation, role=alert), .nm-levels, .nm-chips > .nm-tchip > b + button,
 *             .nm-addlevel, .nm-quiet, .nm-quiet-to, .nm-themes > .nm-theme (+ .nm-swatches), .nm-variants,
 *             .nm-mode-k, .nm-links, .nm-supp (a list of names), .nm-tier, .nm-authors. A switched-off group
 *             (.nm-set.is-off, Alerts) disables its controls (:disabled); its text is not dimmed.
 * Guard       The usage guard card on the Settings page (section.nm-card[data-card="guard"]) is made of the parts above and
 *             nothing of its own: a toggle, four .nm-input number fields (a blank one is off) and two blocks the page fills in,
 *             #guard-live (.nm-verdict > .nm-chip OFF, ARMED or HOLDING, .nm-story, .nm-kv-grid.is-one, .nm-actions) and
 *             #guard-setup (the same parts, with .nm-callout for a note or the result of a click, .nm-error for a refusal, .nm-path
 *             for a file path or a command and .nm-lede for what it costs). A value that has not arrived is data-pending there too.
 * States      .nm-empty, .nm-error, .nm-pending, .nm-callout (.is-warn: a boxed note; with data-pending it keeps its room and shows
 *             nothing, so a note that is there before and after its report does not move the card).
 * Overview    .nm-cost > .nm-cost-l (.nm-hero, .nm-plan, .nm-tiles > .nm-tile > small + .nm-big) + .nm-cost-r
 *             (.nm-chart-t, .nm-chart > the daily chart host .nm-daily). The figure in .nm-hero is
 *             .nm-big.h with style="--n:<characters>", its length, so a theme can size it to the column
 *             (Nostromo does, with a container query) and let the .nm-plan text wrap beside it.
 * History     .nm-odo-row > .nm-odo-main (.nm-odo > .nm-od-d > .strip > i, the digit shown set by --d;
 *             .nm-od-sep; .nm-odo-facts) + .nm-compare (.nm-cmp-body > .nm-cmp-n .nm-cmp-u .nm-cmp-t; .nm-dots
 *             > button, the comparison picker).
 * Charts      .nm-chart-host[data-chart] holds one SVG from charts.js (mon-* classes, see that file); the
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
 *             cells .nm-odo-facts b .nm-cmp-n .nm-toolbar-n), a bar (.nm-cells .nm-ratio) or a chart (.nm-chart-host .mon-spark
 *             .mon-stack .nm-odo) whose value is not in carries data-pending (and aria-hidden). Its text is placeholder text
 *             that only sizes the slot; the structural stylesheet makes it transparent and the theme draws what stands in its
 *             place (a block, a cursor, noise). When the value is in: data-final=<its final text> on a figure and data-fill=<0
 *             to 100> on a bar; and data-fresh on the ones that are new or changed in the last paint (a figure showing what
 *             it showed before is not fresh, so a refresh of unchanged numbers replays nothing).
 *   Revisits  The last good report of each page and window is kept in memory: a page opened again shows those numbers at
 *             once and refreshes behind them, without a placeholder and without replaying the figures that did not change.
 *   No records  A PC with no Claude Code activity (the monitor has finished reading and found no request: someone who uses Claude on
 *             the web or in the app) has nothing for the cost, efficiency and history reports to count, and none is asked for. The
 *             Overview's cost, efficiency and model cards keep their frame and title and say so in one .nm-empty line (data-state=ready,
 *             no data-needs); Cost & value, Efficiency and History are one wide card that explains it.
 *
 * Motion      Everything that moves checks data-reduce-motion="1" on <html> (the reduce_motion setting) and
 *             prefers-reduced-motion. With either, nothing animates, enter and arrive are never called (cancel still is:
 *             it only finishes what was playing) and the comparison does not rotate. The hooks a theme script
 *             (window.USAGE_MONITOR_THEME_FX[id]) or the motion layer may implement:
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
 *             Events, for code that is not a theme script: mon:card-data on the card (bubbles; detail is the info above) and
 *             mon:page-rendered on #main (detail { page, opened, reduced, updated: [card keys], added: [card keys] }).
 */
(function () {
  "use strict";

  var POLL_MS = 2000;
  var FALLBACK_CSS = "/static/themes/nostromo.css";
  var TOKEN = (function () {
    var meta = document.querySelector('meta[name="monitor-token"]');
    return meta ? meta.getAttribute("content") || "" : "";
  })();
  // The dashboard's choices as the monitor keeps them (#54). The server puts them in the page, as it does the token. They used
  // to live in the page's own storage, which does not last: the dashboard window runs pywebview in private mode (its default),
  // which keeps nothing once the window closes, and the browser fallback's origin has a new port on every run.
  var SAVED_PREFS = (function () {
    var meta = document.querySelector('meta[name="monitor-prefs"]');
    var text = meta ? meta.getAttribute("content") || "" : "";
    try {
      return text ? JSON.parse(text) : null;
    } catch (e) {
      return null;
    }
  })();
  // The app's language and formats (#87), as the monitor decided them. The server puts them in the page, as it does the
  // token. The page never reads navigator.language: it can disagree with the Language setting and with Windows.
  var LOCALE_META = (function () {
    var meta = document.querySelector('meta[name="monitor-locale"]');
    var text = meta ? meta.getAttribute("content") || "" : "";
    try {
      var parsed = text ? JSON.parse(text) : null;
      return parsed && typeof parsed === "object" && typeof parsed.language === "string" ? parsed : null;
    } catch (e) {
      return null;
    }
  })();
  // Whether the server said which locale it served the page in. A page that cannot tell (no meta, or one it cannot read)
  // never reloads for a locale: it would not know that it already shows the right one.
  var LOCALE_KNOWN = LOCALE_META !== null;
  var LOCALE = LOCALE_META || { setting: "system", language: "en", system: "en", format: "en-US", clock: 12, offered: ["en"] };
  // Its numbers, percents and money (#91): formats.js, loaded before this file. English keeps the page's own shapes.
  var FMT = window.MonitorFormats.make(LOCALE);
  var NO_GROUP = { group: false };
  // The money boxes sized for English's figures, the cost tiles and the big cost figure, take the "$" without its space
  // (Chris's decision on #91; tests/budgets.py measures them so).
  var TIGHT = { tight: true };
  var TIGHT_WHOLE = { digits: 0, tight: true };
  var TIGHT_K = { scale: 3, tight: true };
  var TIGHT_M = { scale: 6, tight: true };
  // Ten zeros, the width a typical all-time total has, grouped as the locale groups them.
  var ODOMETER_PLACEHOLDER = FMT.number(1e9).replace(/\d/g, "0");
  // The money a pending note shows until its figure comes, in the locale's shape: the cost toolbar's "$000.00" and the
  // daily chart's "TOP $00.00" in English.
  var COST_NOTE_PLACEHOLDER = FMT.usd("123.45").replace(/\d/g, "0");
  var TOP_PLACEHOLDER = FMT.usd("12.34").replace(/\d/g, "0");
  var DAILY_DAYS = 30;                    // the days the Overview's daily chart shows
  var LOCALE_RELOAD_MARK = "usage-monitor-locale-reload:";
  // A reload that brought the page into the locale it was for is done: its mark goes (reloadForLocale).
  if (LOCALE_KNOWN && window.name === localeMark(LOCALE)) {
    window.name = "";
  }
  // The catalog, from /static/i18n/<lang>.js (loaded before this file): the language's own strings, English behind them.
  var I18N = window.MonitorI18n || { language: "en", strings: {}, fallback: {} };
  // Each language named in itself, as the Language setting lists them (the same names as i18n.NAMES).
  var LANGUAGE_NAMES = { en: "English", fr: "Français", es: "Español", tr: "Türkçe" };
  var CHARTS = window.MonitorCharts;
  // The pages, in the menu's order. Their headings are the catalog's (HEADINGS, #98).
  var PAGES = [
    { key: "overview" }, { key: "limits" }, { key: "cost" }, { key: "efficiency" }, { key: "history" }, { key: "settings" }, { key: "about" }
  ];
  var PAGE_KEYS = PAGES.map(function (p) { return p.key; });
  // Old names of pages, so a saved link or a bookmark (#nerds, from before the page was called Efficiency) still opens it.
  var PAGE_ALIASES = { nerds: "efficiency" };
  // The link's states, by the status code the monitor sends: whether the link is up (the alive mark lit). Their words are
  // the catalog's (#98, linkLine).
  var STATUS = {
    live: { ok: true }, loading: { ok: false }, stale: { ok: false }, offline: { ok: false }, rate_limited: { ok: false },
    no_credentials: { ok: false }, paused: { ok: false }
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
  // The weekdays and months in the page's capitals (#91): English's MON and JAN, else the language's own short names,
  // cased by its rule (Turkish NİS, EKİ). WEEKDAYS_MON is Monday first; DAYS is Sunday first, as Date.getDay() counts.
  var WEEKDAYS_MON = [0, 1, 2, 3, 4, 5, 6].map(function (i) { return FMT.upper(FMT.weekday(i)); });
  var DAYS = [6, 0, 1, 2, 3, 4, 5].map(function (i) { return WEEKDAYS_MON[i]; });
  var MONTHS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11].map(function (i) { return FMT.upper(FMT.month(i)); });
  var LINKS = {
    repo: "https://github.com/Godimas101/claude-usage-monitor",
    bug: "https://github.com/Godimas101/claude-usage-monitor/issues/new?template=bug_report.md&labels=bug",
    patreon: "https://patreon.com/Godimas101",
    hooks: "https://github.com/Godimas101/claude-usage-monitor/blob/main/docs/AGENT_HOOK.md",
    readme: "https://github.com/Godimas101/claude-usage-monitor#-using-claude-on-the-web-or-in-the-app"
  };
  var POLL_CHOICES = [
    [60000, "1 MIN"], [300000, "5 MIN"], [600000, "10 MIN"], [900000, "15 MIN"], [1800000, "30 MIN"], [3600000, "60 MIN"]
  ];
  // The usage guard card on the Settings page: how often it asks /api/guard while it is on screen, how many sessions each of its
  // lists names, and its words.
  var GUARD_POLL_MS = 3000;
  var GUARD_LISTED = 5;
  var GUARD_EXPLAIN = "When plan usage reaches a cap, working Claude sessions are told to wrap up, and sessions that wait are woken when every limit is under its cap again. Nothing is blocked.";
  var GUARD_COST = "Adds a quick check after each tool call (about a quarter of a second).";
  var GUARD_LABELS = {
    guard_enabled: "USAGE GUARD", guard_session_cap: "SESSION CAP", guard_weekly_cap: "WEEKLY CAP",
    guard_scoped_cap: "MODEL WEEKLY CAP", guard_resume_stagger_s: "STAGGER"
  };
  var HOOK_WORDS = { installed: "INSTALLED", partial: "PARTLY INSTALLED", missing: "NOT INSTALLED", unknown: "UNKNOWN" };
  var MCP_WORDS = { registered: "REGISTERED", missing: "NOT REGISTERED", other: "A DIFFERENT PROGRAM", unknown: "UNKNOWN" };
  // The sign-in help (#28): what the status banner says when there is no sign-in or it has expired, and the commands it shows.
  // The commands are text for the user to run in PowerShell; the page never fetches or links them. Anthropic's installer leaves
  // the PATH alone, so "claude" on its own is not found after it: START_COMMAND runs Claude Code from the folder the installer
  // puts it in, whatever the PATH holds (#42). The heading is in capitals like the other banners' (the Gates theme sets it in
  // sentence case).
  var INSTALL_COMMAND = "irm https://claude.ai/install.ps1 | iex";
  var START_COMMAND = '& "$env:USERPROFILE\\.local\\bin\\claude.exe"';
  var COMMANDS = { install: INSTALL_COMMAND, start: START_COMMAND };
  var COPY_TITLES = { install: "Copy the install command", start: "Copy the command that starts Claude Code" };
  var SIGNIN_HEAD = "NO CLAUDE SIGN-IN YET";
  var SIGNIN_WHY = "The monitor reads your plan's limits through the sign-in that Claude Code saves on this PC. Signing in to Claude Code once is enough, even if you use Claude on the web or in the Claude app. Claude Code needs a paid plan such as Pro or Max.";
  var SIGNIN_RENEWS = "Claude Code renews the sign-in when you use it. If the monitor later says the sign-in has expired, send Claude Code a message.";
  var CHECK_NONE = "Still no sign-in found.";
  var CHECK_STALE = "Still no working sign-in.";
  var CHECK_FAILED = "Could not check just now. Try again.";
  // How long "Check again" waits for the monitor to publish the cycle it asked for, and how long the copy button's answer stays
  // ("Copied", or "Press Ctrl+C" with the command selected, which needs a moment longer to read and act on).
  var CHECK_WAIT_MS = 15000;
  var COPY_MS = 3000;
  var COPY_SELECT_MS = 8000;
  // The pages that count Claude Code's records, on a PC that has none (#28): someone who uses Claude on the web or in the app.
  var CLAUDE_CODE = "Claude Code";               // a name, as written: never translated (tName)
  var NO_ACTIVITY = "No Claude Code activity on this PC";
  var NO_ACTIVITY_WHY = "These pages count the work Claude Code records on this PC. Claude on the web and in the Claude app keeps no records here, so there is nothing to count. Your limits, pace and alerts work as usual.";
  var BASIS = "API-equivalent at list prices";
  // The Fast mode card's note. It is the same words before the report, with no fast-mode request and with some, so the card is
  // as tall in all three and only its tone changes (a note that said something else per state wrapped to another line count).
  var FAST_NOTE = "Fast mode on Pro and Max plans is billed to your usage credits, not to your plan&rsquo;s limits.";
  // Points of the clock within which a limit counts as on pace. The same number as windows.PACE_BAND_POINTS on the
  // Python side (the widgets and the pacing report use it), which tests/test_webui.py checks.
  var PACE_BAND = 10;
  // The choices the dashboard keeps, with the values each may take. ui/server.py's DASHBOARD_PREF_CHOICES holds the same, and
  // tests/test_webui.py checks that they agree.
  var PREF_CHOICES = {
    costWindow: WINDOW_ORDER, efficiencyWindow: WINDOW_ORDER, folderMode: ["folder", "launch"],
    calMetric: ["cost", "tokens"], matrixMetric: ["cost", "requests"]
  };
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
    "[data-pending] .mon-t{fill:transparent!important}",
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
    guard: null,             // the last /api/guard answer: { guard, told, waiting, setup }
    guardAt: 0,              // when it was asked for (ms), so the Settings page asks again after GUARD_POLL_MS
    guardFlight: false,
    guardLost: false,        // the last ask got no answer
    resumeBusy: false,       // "Resume now" is being sent
    setup: { busy: "", result: null },   // "install" or "remove" while a click runs; the result of the last click
    progress: updateProgressState(),     // what the page knows of a running update (#58; see "The update's progress")
    check: { busy: false, sent: false, from: "", at: 0, still: false, failed: false },   // "Check again" on the sign-in help (see checkAgain)
    copy: "",                // the copy button's answer for a moment: "copied" or "select"
    copyOf: "",              // and which button gives it: "install" or "start"
    copyTimer: 0,
    noActivity: false,       // the monitor has finished reading and found no Claude Code activity on this PC (see limitsOnly)
    odoShown: null,
    cmpTimer: 0,
    tipApi: null,
    ui: { costWindow: "cycle", folderMode: "folder", efficiencyWindow: "cycle", calMetric: "cost", matrixMetric: "cost", cmp: 0 }
  };

  // ── Helpers ─────────────────────────────────────────────────────────────────────

  function $(selector, root) {
    return (root || document).querySelector(selector);
  }

  // ── Text (#87) ───────────────────────────────────────────────────────────────────
  // Catalog strings are plain text with {named} placeholders. t() gives text for textContent; tHtml() gives escaped
  // HTML, inserting a value as is only when it is markup() this file built (tCode, tPath, tName, a link). Nothing from
  // outside can pass for markup: it is known by a prototype only this file holds.

  /* A catalog entry and the language it came from: the page's own, or English when its catalog lacks the key. */
  function lookupText(key) {
    var own = I18N.strings || {};
    if (I18N.language !== "en" && Object.prototype.hasOwnProperty.call(own, key)) {
      return { value: own[key], lang: I18N.language };
    }
    var english = I18N.fallback || {};
    return Object.prototype.hasOwnProperty.call(english, key) ? { value: english[key], lang: "en" } : null;
  }

  function fillText(text, values) {
    return String(text).replace(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g, function (whole, name) {
      return values && Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : whole;
    });
  }

  var MARKUP = {};

  /* HTML this file built, for a tHtml() placeholder. */
  function markup(html) {
    var built = Object.create(MARKUP);
    built.html = String(html);
    return built;
  }

  function isMarkup(value) {
    return value !== null && typeof value === "object" && Object.getPrototypeOf(value) === MARKUP;
  }

  /* A command, for a block of its own: never translated, and never cased or broken by a language's rules. */
  function tCode(text) {
    return markup('<code class="nm-path" lang="en" translate="no">' + esc(text) + "</code>");
  }

  /* A command or a path inside a sentence, as tCode. */
  function tPath(text) {
    return markup('<span class="nm-path" lang="en" translate="no">' + esc(text) + "</span>");
  }

  /* A name as given (a model, a plan, a folder, a session, a tool, Claude Code): never translated. In the theme's capitals
   * it is cased with the words around it, by the page's language (Chris's decision on #91: Turkish HAİKU), so it carries
   * no lang of its own. */
  function tName(text) {
    return markup('<span translate="no">' + esc(text) + "</span>");
  }

  /* The CLDR plural category of count in language: i18n.plural_category's rules, so the page and the app agree whatever
   * the WebView's own ICU says. en and tr have one and other; fr and es have one, many and other; French counts 0, 1
   * and 1.5 as one. A count that is not a finite number is other. */
  function pluralCategory(language, count) {
    if (typeof count !== "number" || !isFinite(count)) {
      return "other";
    }
    var n = Math.abs(count);
    var whole = Math.floor(n);
    var many = n === whole && whole !== 0 && whole % 1000000 === 0;
    if (language === "fr") {
      return whole === 0 || whole === 1 ? "one" : many ? "many" : "other";
    }
    if (language === "es") {
      return n === 1 ? "one" : many ? "many" : "other";
    }
    return n === 1 ? "one" : "other";
  }

  /* The text for key with its placeholders filled; the key itself when no catalog has it (a bug, and easy to see). */
  function t(key, values) {
    var found = lookupText(key);
    return found && typeof found.value === "string" ? fillText(found.value, values) : key;
  }

  /* The language t(key) comes from. An element showing English on another language's page gets lang="en". */
  function tLang(key) {
    var found = lookupText(key);
    return found ? found.lang : "en";
  }

  /* Escaped HTML for key. English text on another language's page is wrapped in lang="en", so it is cased by English
   * rules (a Turkish page would otherwise turn SESSION into SESSİON). */
  function tHtml(key, values) {
    var found = lookupText(key);
    var text = found && typeof found.value === "string" ? found.value : key;
    var out = esc(text).replace(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g, function (whole, name) {
      if (!values || !Object.prototype.hasOwnProperty.call(values, name)) {
        return whole;
      }
      var value = values[name];
      return isMarkup(value) ? value.html : esc(value);
    });
    return found && found.lang !== I18N.language ? '<span lang="en">' + out + "</span>" : out;
  }

  /* The plural entry for count, chosen by the CLDR rules of the language the entry came from; {count} is filled too. */
  function tPlural(key, count, values) {
    var found = lookupText(key);
    if (!found || !found.value || typeof found.value !== "object") {
      return key;
    }
    var category = pluralCategory(found.lang, count);
    var all = { count: count };
    Object.keys(values || {}).forEach(function (name) {
      all[name] = values[name];
    });
    return fillText(found.value[category] || found.value.other || "", all);
  }

  /* The text for key in its language's capitals, its placeholders filled after, so a value keeps its own case ("1,2 k$"): for
   * a chart's SVG label, which a theme cannot set in capitals with text-transform everywhere (#98). The key itself when no
   * catalog has it. */
  function tUpper(key, values) {
    var found = lookupText(key);
    if (!found || typeof found.value !== "string") {
      return key;
    }
    var cased = found.value.split(/(\{[A-Za-z_][A-Za-z0-9_]*\})/).map(function (part, i) {
      return i % 2 ? part : FMT.upper(part, found.lang);
    }).join("");
    return fillText(cased, values);
  }

  /* Escaped HTML for key as tHtml makes it, and the language it came from: { html, lang }, for an element that holds the
   * line as its own text. English on another language's page is marked on that element (lineLangAttr), never by a span
   * inside it: a theme's entrance types and decodes an element's own text (#98's critic 2). */
  function tHtmlLine(key, values) {
    var found = lookupText(key);
    var text = found && typeof found.value === "string" ? found.value : key;
    var html = esc(text).replace(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g, function (whole, name) {
      if (!values || !Object.prototype.hasOwnProperty.call(values, name)) {
        return whole;
      }
      var value = values[name];
      return isMarkup(value) ? value.html : esc(value);
    });
    return { html: html, lang: found ? found.lang : "en" };
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
    return FMT.percent(digits ? n.toFixed(digits) : String(Math.round(n)));
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
    if (a >= 1000) {
      return FMT.usd(n < 0 ? -Math.round(a) : Math.round(a), { digits: 0 });
    }
    // English keeps its own text: an amount that rounds up to 1000.00 has no group ("$1000.00").
    return FMT.english ? (n < 0 ? "-$" : "$") + a.toFixed(2) : FMT.usd((n < 0 ? "-" : "") + a.toFixed(2));
  }

  function fmtUsdExact(value) {
    var n = num(value);
    return n === null ? "--" : FMT.usd(n, { digits: 2 });
  }

  /* Real money (usage credits) in the currency the account is billed in, which the usage API reports. The figures
   * of API-equivalent cost stay dollars (fmtUsd). An account that reports no currency is read as USD, and a code
   * the browser does not know is shown as the number and the code (formats.js's money). */
  function fmtMoney(value, currency) {
    var n = num(value);
    if (n === null) {
      return "--";
    }
    return FMT.money(n, String(currency || "USD").trim().toUpperCase());
  }

  function fmtPlanPrice(value) {
    var n = num(value);
    if (n === null) {
      return "--";
    }
    if (n % 1 !== 0) {
      return fmtUsd(n);
    }
    return FMT.english ? "$" + FMT.number(Math.round(n)) : FMT.usd(Math.round(n), { digits: 0 });
  }

  function fmtUsdShort(value) {
    var n = num(value);
    if (n === null) {
      return "--";
    }
    return Math.abs(n) >= 1000 ? FMT.usd((n / 1000).toFixed(1), { scale: 3 }) : fmtUsd(n);
  }

  /* A cost for a tile: at most six characters, so three tiles fit a narrow card. The "$" goes without its space
   * (TIGHT); English keeps its own text. */
  function fmtUsdTile(value) {
    var n = num(value);
    if (n === null) {
      return "--";
    }
    var a = Math.abs(n);
    if (a >= 1e6) {
      return FMT.usd((n / 1e6).toFixed(1), TIGHT_M);
    }
    if (a >= 1000) {
      return FMT.usd((n / 1000).toFixed(1), TIGHT_K);
    }
    if (a >= 100) {
      return FMT.english ? "$" + Math.round(n) : FMT.usd(String(Math.round(n)), TIGHT);
    }
    return FMT.usd((n < 0 ? "-" : "") + a.toFixed(2), TIGHT);
  }

  /* The big cost figure: cents under $100, whole dollars to $10,000, then K. Six characters at most, tight as a tile. */
  function fmtUsdHero(value) {
    var n = num(value);
    if (n === null) {
      return "--";
    }
    var a = Math.abs(n);
    if (a >= 1e6) {
      return FMT.usd((n / 1e6).toFixed(1), TIGHT_M);
    }
    if (a >= 10000) {
      return FMT.usd((n / 1000).toFixed(1), TIGHT_K);
    }
    return a >= 100 ? FMT.usd(n < 0 ? -Math.round(a) : Math.round(a), TIGHT_WHOLE) : FMT.usd((n < 0 ? "-" : "") + a.toFixed(2), TIGHT);
  }

  function fmtMult(value) {
    var n = num(value);
    if (n === null) {
      return "--";
    }
    return FMT.number(n >= 100 ? String(Math.round(n)) : n.toFixed(1), NO_GROUP) + "×";
  }

  function fmtCount(value) {
    var n = num(value);
    if (n === null) {
      return "--";
    }
    var a = Math.abs(n);
    if (a >= 1e9) {
      return FMT.compact((n / 1e9).toFixed(2), 9, n);
    }
    if (a >= 1e6) {
      return FMT.compact((n / 1e6).toFixed(1), 6, n);
    }
    if (a >= 1e3) {
      return FMT.compact((n / 1e3).toFixed(1), 3, n);
    }
    return FMT.number(String(Math.round(n)), NO_GROUP);
  }

  function fmtInt(value) {
    var n = num(value);
    return n === null ? "--" : FMT.number(Math.round(n));
  }

  /* The time left in a window: "3 H 27 M" on its own (a badge, which a theme may show as it is), or inside a sentence
   * (inSentence) "3 h 27 m", which a theme sets in capitals with the words around it (#98: "In {time}"). Under a minute it is
   * format.now: "NOW" and "now". */
  function fmtRemain(ms, inSentence) {
    if (ms === null || !isFinite(ms)) {
      return "--";
    }
    var minutes = Math.floor(ms / 60000);
    if (minutes <= 0) {
      var now = tLine("format.now");
      return inSentence ? now.text : FMT.upper(now.text, now.lang);
    }
    var days = Math.floor(minutes / 1440);
    var hours = Math.floor((minutes % 1440) / 60);
    var mins = minutes % 60;
    if (!FMT.english) {
      // CLDR's narrow units in capitals (#91: "2J 3H" in French), with the widgets' parts: no zero tail (countdown_units).
      var parts = days >= 1 ? [[days, "d"], [hours, "h"]] : hours >= 1 ? [[hours, "h"], [mins, "m"]] : [[mins, "m"]];
      var words = FMT.duration(parts[1] && parts[1][0] === 0 ? parts.slice(0, 1) : parts);
      return inSentence ? words : FMT.upper(words);
    }
    var unit = inSentence ? AGE_UNIT_IN_SENTENCE : AGE_UNIT;
    if (days >= 1) {
      return days + " " + unit.D + " " + hours + " " + unit.H;
    }
    if (hours >= 1) {
      return hours + " " + unit.H + " " + mins + " " + unit.M;
    }
    return mins + " " + unit.M;
  }

  /* An age as a number and a unit code (S, M, H or D), or null when there is none. ageWords writes it. */
  function fmtAge(ms) {
    if (ms === null || !isFinite(ms)) {
      return null;
    }
    var s = Math.max(0, Math.floor(ms / 1000));
    if (s < 60) {
      return { n: s, unit: "S" };
    }
    var m = Math.floor(s / 60);
    if (m < 60) {
      return { n: m, unit: "M" };
    }
    var h = Math.floor(m / 60);
    if (h < 48) {
      return { n: h, unit: "H" };
    }
    return { n: Math.floor(h / 24), unit: "D" };
  }

  /* The units of an age as the badges write them ("3 M") and inside a sentence ("from 3 m ago"). */
  var AGE_UNIT = { S: "S", M: "M", H: "H", D: "D" };
  var AGE_UNIT_IN_SENTENCE = { S: "s", M: "m", H: "h", D: "d" };

  /* A table entry for a code from the data; a code the table does not name takes the fallback code's entry. Only the table's
   * own keys count, never an object's inherited ones. */
  function entryOf(table, code, fallback) {
    return Object.prototype.hasOwnProperty.call(table, code) ? table[code] : table[fallback];
  }

  /* The tone of a status code (data-tone): ok, warn, alert, acc, or neutral for any code the table does not name. */
  function toneOf(table, code) {
    return Object.prototype.hasOwnProperty.call(table, code) ? table[code] : "neutral";
  }

  // An age's unit as formats.duration takes it (formats.py's age_unit codes).
  var AGE_CODE = { S: "s", M: "m", H: "h", D: "d" };

  /* An age (fmtAge) written with the given unit words: "3 M", or "--" when there is no age. Other languages write CLDR's
   * narrow unit (#91), in capitals on a badge (AGE_UNIT) and as is inside a sentence: "3MIN" and "3min" in French. */
  function ageWords(age, units) {
    if (age === null) {
      return "--";
    }
    if (!FMT.english) {
      var words = FMT.duration([[age.n, entryOf(AGE_CODE, age.unit, "S")]]);
      return units === AGE_UNIT ? FMT.upper(words) : words;
    }
    return age.n + " " + units[age.unit];
  }

  function parseTime(iso) {
    if (!iso) {
      return null;
    }
    var t = Date.parse(iso);
    return isFinite(t) ? t : null;
  }

  /* Dates and times (#91) are the formats' (English as the page always wrote them, else the locale's on the monitor's
   * clock), in the page's capitals. */
  function clockText(ms) {
    if (ms === null) {
      return "--";
    }
    var d = new Date(ms);
    var now = new Date();
    var sameDay = d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
    return FMT.upper(sameDay ? FMT.clock(d) : FMT.dayClock(d));
  }

  function stampText(ms) {
    if (ms === null) {
      return "--";
    }
    return FMT.upper(FMT.stamp(new Date(ms)));
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
    return FMT.upper(FMT.dayDate(d, withYear));
  }

  function dateText(ms) {
    if (ms === null) {
      return "--";
    }
    return FMT.upper(FMT.date(new Date(ms)));
  }

  function hourText(ms) {
    return FMT.upper(FMT.hour(new Date(ms)));
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

  /* A status line written in capitals ("REFRESH FAILED: …"): the monitor's reason is lower case, and the Gates theme sets a
   * line in sentence case only when all of it is capitals, so a reason left as it came would be shouted in Nostromo and Night
   * City (which upper-case it in CSS) and left half and half in Gates. Refresh and resume (#100's); the update buttons' lines
   * are the catalog's since #97 (updateRefusal). */
  function failedText(prefix, res) {
    return prefix + ": " + FMT.upper(errorText(res), "en");
  }

  // ── The update buttons' words (#97) ─────────────────────────────────────────────
  // What a click on an update button says (S.update) is a line: the catalog's text and the language it came from, so the
  // element that shows it is marked lang="en" when it is English on another language's page. Stored in natural case:
  // Nostromo and Night City set the lines in capitals by CSS, Gates shows them as written.

  var APP_NAME = "Claude Usage Monitor";         // the product's name, as written (#86): the update banner's heading

  /* The line for key, its placeholders filled: { text, lang }. */
  function tLine(key, values) {
    var found = lookupText(key);
    return { text: found && typeof found.value === "string" ? fillText(found.value, values) : key, lang: found ? found.lang : "en" };
  }

  /* S.update: a line and the release it is about. A click on Install update or Skip this version gives "version" (the banner
   * shows its line too); a check gives "offered" (its words stay out of the banner; see updateLine). */
  function updateAnswer(line, about, version) {
    var answer = { text: line.text, lang: line.lang };
    answer[about] = version;
    return answer;
  }

  /* The words of each update button's refusal, by its reason (updateRefusal). One literal key per line, so the catalog check
   * and a translator's tools read them all. */
  var UPDATE_REFUSED = {
    check: {
      error: function () { return tLine("settings.update.failed.check.error"); },
      internal: function () { return tLine("settings.update.failed.check.internal"); },
      unavailable: function () { return tLine("settings.update.failed.check.unavailable"); },
      token: function () { return tLine("settings.update.failed.check.token"); },
      http: function (status) { return tLine("settings.update.failed.check.http", { status: status }); },
      lost: function () { return tLine("settings.update.failed.check.lost"); }
    },
    install: {
      error: function () { return tLine("settings.update.failed.install.error"); },
      internal: function () { return tLine("settings.update.failed.install.internal"); },
      unavailable: function () { return tLine("settings.update.failed.install.unavailable"); },
      token: function () { return tLine("settings.update.failed.install.token"); },
      http: function (status) { return tLine("settings.update.failed.install.http", { status: status }); },
      lost: function () { return tLine("settings.update.failed.install.lost"); }
    },
    skip: {
      error: function () { return tLine("settings.update.failed.skip.error"); },
      internal: function () { return tLine("settings.update.failed.skip.internal"); },
      unavailable: function () { return tLine("settings.update.failed.skip.unavailable"); },
      token: function () { return tLine("settings.update.failed.skip.token"); },
      http: function (status) { return tLine("settings.update.failed.skip.http", { status: status }); },
      lost: function () { return tLine("settings.update.failed.skip.lost"); }
    }
  };

  /* The line for an update button the monitor refused: op is "check", "install" or "skip", res its answer (null when the
   * request itself failed: the dashboard lost its link). Worded by the answer's HTTP status, never by the English `error`
   * it carries. On these routes each status has one reason (ui/server.py): 501, this monitor has no such step; 500, the
   * step failed, or, rarely, "internal error" (a handler that raised: the one error that is read, and only to tell the two
   * apart); 401, the dashboard's token. Any other status, which the page's own requests do not get, is named by its number.
   * #102 may give these answers a code: it changes this function only. */
  function updateRefusal(op, res) {
    var reason = !res ? "lost" : res.status === 501 ? "unavailable" : res.status === 401 ? "token" : res.status !== 500 ? "http" :
      res.data && res.data.error === "internal error" ? "internal" : "error";
    return UPDATE_REFUSED[op][reason](res ? String(res.status) : "");
  }

  /* The lang of an element that shows a line: "en" while the line is English on another language's page, none otherwise. */
  function markLineLang(el, lang) {
    if (lang && lang !== I18N.language) {
      el.setAttribute("lang", lang);
    } else {
      el.removeAttribute("lang");
    }
  }

  /* The same in markup (the banner's line): ' lang="en"' or nothing. */
  function lineLangAttr(lang) {
    return lang && lang !== I18N.language ? ' lang="' + esc(lang) + '"' : "";
  }

  // ── The header's, the sidebar's and the headings' words (#98) ─────────────────────
  // In natural case: Nostromo and Night City set them in capitals by CSS. Gates says some of them in words of its own
  // (page.gates.*), which gates.js used to make of the capitals: app.js writes them while the look is Gates, from the first
  // frame. Each table holds one literal key a line (the UPDATE_REFUSED pattern), so the catalog check reads them all.

  /* Shows a line in an element: its text, and lang="en" while it is English on another language's page. */
  function writeLine(el, line) {
    if (el) {
      el.textContent = line.text;
      markLineLang(el, line.lang);
    }
  }

  /* A value that is not a sentence ("--"): no language of its own. */
  function bareLine(text) {
    return { text: text, lang: "" };
  }

  /* A name that stands alone in an element (a model, a surface, a limit's own label): its text as given, with no language of
   * its own, and translate="no" on the element on another language's page. An English page keeps the markup it had. */
  function nameLine(text) {
    return { text: text, lang: "", name: true };
  }

  /* The attributes of an element that shows a line (from tLine, tHtmlLine, bareLine or nameLine): lang="en" while the line is
   * English on another language's page, and translate="no" there for a name. On the element itself, never on a span inside
   * it: a theme's entrance moves an element's own text (#98's critic 2). A plain string has none. */
  function lineAttrs(line) {
    if (!line || typeof line !== "object") {
      return "";
    }
    return lineLangAttr(line.lang) + (line.name && I18N.language !== "en" ? ' translate="no"' : "");
  }

  /* The text of a line, or a plain string as it is. */
  function lineText(line) {
    return line && typeof line === "object" ? line.text : line;
  }

  /* Whether the page is in the Gates look: the server writes data-theme into the first paint, applyTheme on a change. */
  function gatesLook() {
    return document.documentElement.getAttribute("data-theme") === "gates";
  }

  // The link's words for each status code, and for "lost": the dashboard gets no answer from the monitor.
  var LINK_WORDS = {
    live: function () { return tLine("header.link.live"); },
    loading: function () { return tLine("header.link.loading"); },
    stale: function () { return tLine("header.link.stale"); },
    offline: function () { return tLine("header.link.offline"); },
    rate_limited: function () { return tLine("header.link.rate_limited"); },
    no_credentials: function () { return tLine("header.link.no_credentials"); },
    paused: function () { return tLine("header.link.paused"); },
    lost: function () { return tLine("header.link.lost"); }
  };
  var GATES_LINK_WORDS = {
    live: function () { return tLine("page.gates.link.live"); },
    loading: function () { return tLine("page.gates.link.loading"); },
    stale: function () { return tLine("page.gates.link.stale"); },
    offline: function () { return tLine("page.gates.link.offline"); },
    rate_limited: function () { return tLine("page.gates.link.rate_limited"); },
    no_credentials: function () { return tLine("page.gates.link.no_credentials"); },
    paused: function () { return tLine("page.gates.link.paused"); },
    lost: function () { return tLine("page.gates.link.lost"); }
  };

  /* The words of a link state in the look's words: the header's line, the status line's heading, the health box's row. A
   * code the table does not name reads as loading. */
  function linkLine(code) {
    return entryOf(gatesLook() ? GATES_LINK_WORDS : LINK_WORDS, code, "loading")();
  }

  /* The health box's link row: "Live" for a live link (Gates' own word is the header's), else the header's words. */
  function healthLinkLine(code) {
    return code === "live" && !gatesLook() ? tLine("nav.health.live") : linkLine(code);
  }

  /* The header's data age (an age from fmtAge, or null): "Data age 12 s", or in Gates "Updated 12 s ago". The unit is the
   * sentence's (#91: CLDR's in other languages); the capitals are the theme's. */
  function ageLine(age) {
    if (age === null) {
      return gatesLook() ? tLine("page.gates.age_none") : tLine("header.age_none");
    }
    var words = ageWords(age, AGE_UNIT_IN_SENTENCE);
    return gatesLook() ? tLine("page.gates.age", { age: words }) : tLine("header.age", { age: words });
  }

  /* The burn rate's unit beside its figure. */
  function burnUnitLine() {
    return gatesLook() ? tLine("page.gates.burn.unit") : tLine("overview.burn.unit");
  }

  /* The burn rate's cost an hour, under its unit: "$1.10 / hour", "-- / hour" before there is a rate (Gates: "-- / Hour"). */
  function burnHourLine(burn) {
    if (!burn) {
      return gatesLook() ? tLine("page.gates.burn.per_hour_none") : tLine("overview.burn.per_hour", { amount: "--" });
    }
    return tLine("overview.burn.per_hour", { amount: fmtUsd(burn.usd_per_hour) });
  }

  // Each page's heading and the sentence beside it.
  var HEADINGS = {
    overview: function () { return { title: tLine("heading.overview.title"), sub: tLine("heading.overview.sub") }; },
    limits: function () { return { title: tLine("heading.limits.title"), sub: tLine("heading.limits.sub") }; },
    cost: function () { return { title: tLine("heading.cost.title"), sub: tLine("heading.cost.sub") }; },
    efficiency: function () { return { title: tLine("heading.efficiency.title"), sub: tLine("heading.efficiency.sub") }; },
    history: function () { return { title: tLine("heading.history.title"), sub: tLine("heading.history.sub") }; },
    settings: function () { return { title: tLine("heading.settings.title"), sub: tLine("heading.settings.sub") }; },
    about: function () { return { title: tLine("heading.about.title"), sub: tLine("heading.about.sub", { app: APP_NAME }) }; }
  };

  // The sidebar's colour buttons. Their tooltips name the colour by its id's words; a name (Night City's variants) or an id
  // the table does not know is shown as written. Gates' accents have short names of three letters; any other id shows its
  // first three (Night City's names). Nostromo names its phosphors in full (accentLabel, the theme's own names).
  var ACCENT_TIPS = {
    amber: function () { return t("nav.colour.title.amber"); },
    green: function () { return t("nav.colour.title.green"); },
    white: function () { return t("nav.colour.title.white"); },
    red: function () { return t("nav.colour.title.red"); },
    blue: function () { return t("nav.colour.title.blue"); },
    purple: function () { return t("nav.colour.title.purple"); },
    teal: function () { return t("nav.colour.title.teal"); },
    rose: function () { return t("nav.colour.title.rose"); },
    orange: function () { return t("nav.colour.title.orange"); },
    system: function () { return t("nav.colour.title.system"); }
  };
  var ACCENT_SHORT = {
    system: function () { return t("nav.colour.short.system"); },
    blue: function () { return t("nav.colour.short.blue"); },
    teal: function () { return t("nav.colour.short.teal"); },
    purple: function () { return t("nav.colour.short.purple"); },
    rose: function () { return t("nav.colour.short.rose"); },
    orange: function () { return t("nav.colour.short.orange"); },
    green: function () { return t("nav.colour.short.green"); }
  };

  function accentTip(accent) {
    return Object.prototype.hasOwnProperty.call(ACCENT_TIPS, accent) ? ACCENT_TIPS[accent]() : accent;
  }

  function accentShort(accent) {
    return Object.prototype.hasOwnProperty.call(ACCENT_SHORT, accent) ? ACCENT_SHORT[accent]() : accent.slice(0, 3).toUpperCase();
  }

  /* The line after "Check for updates now". `checked` is false when the monitor could not look (offline, GitHub's rate
   * limit, a slow answer): that is not "up to date". A monitor that sends no `checked` is taken to have looked. */
  function checkUpdateText(res) {
    if (!res.ok) {
      return updateRefusal("check", res);
    }
    var result = res.data && res.data.result ? res.data.result : {};
    if (result.available) {
      return tLine("settings.update.status.found");
    }
    return result.checked === false ? tLine("settings.update.status.unreached") : tLine("settings.update.status.up_to_date");
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

  // ── The dashboard's choices (the last window and toggles chosen) ──────────────────

  /* Takes the saved choices (SAVED_PREFS, from the page) into S.ui. A name it does not know, or a value a name cannot take, is
   * skipped, and that choice keeps its default. */
  function loadPrefs(saved) {
    if (!saved || typeof saved !== "object") {
      return;
    }
    Object.keys(PREF_CHOICES).forEach(function (key) {
      if (PREF_CHOICES[key].indexOf(saved[key]) >= 0) {
        S.ui[key] = saved[key];
      }
    });
  }

  /* Keeps one choice in the monitor's settings, so the next window, and the next run of the monitor, opens with it (#54).
   * Only the one that changed is sent: the monitor merges it into the ones it holds, so two windows never undo each other's.
   * Quiet: no status line. A refused or failed save leaves the choice in force for this window. */
  function savePrefs(name) {
    if (!Object.prototype.hasOwnProperty.call(PREF_CHOICES, name)) {
      return Promise.resolve(false);
    }
    var one = {};
    one[name] = S.ui[name];
    return api("POST", "/api/settings", { dashboard_prefs: one }).then(function (res) {
      if (res.ok && res.data && res.data.settings) {
        S.settings = res.data.settings;
      }
      return !!res.ok;
    }, function () {
      return false;
    });
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

  /* The reports the page on screen draws from. A PC with no Claude Code activity (limitsOnly) has nothing for the cost, efficiency
   * and history reports to count, so the pages that show them, and the Overview's cost cards, ask for none. */
  function pageStats(page) {
    var none = limitsOnly();
    switch (page) {
      case "overview":
        return none ? [statEntry("pacing")] : [statEntry("cost", { window: "cycle" }), statEntry("pacing"), statEntry("history")];
      case "limits":
        return [statEntry("pacing")];
      case "cost":
        return none ? [] : [statEntry("cost", { window: S.ui.costWindow })];
      case "efficiency":
        return none ? [] : [statEntry("efficiency", { window: S.ui.efficiencyWindow })];
      case "history":
        return none ? [] : [statEntry("history")];
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

  // ── Why a report failed (#98) ───────────────────────────────────────────────────
  // A failed report's reason is worded by the page, never shown as the monitor sent it: the reason's code comes from the
  // answer's HTTP status and, where a status has more than one reason, from the monitor's own text, compared whole. These are
  // the texts ui/server.py answers the statistics routes with (a test holds each to its literal there). #102 may give the
  // answers codes: it changes statReason only.
  var STAT_ERRORS = {
    unavailable: "statistics are not available",                     // 501: this build has no statistics
    busy: "statistics are not available right now",                  // 503: a report failed on the database twice
    closed: "the statistics database could not be opened",           // 503: the database could not be opened
    internal: "internal error",                                      // 500: a handler raised
    token: "missing or wrong token"                                  // 401: the dashboard's token
  };
  var STAT_ERROR_STATUS = { unavailable: 501, busy: 503, closed: 503, internal: 500, token: 401 };

  // The words of each reason, one literal key a line.
  var STAT_REASONS = {
    lost: function () { return tHtml("stats.reason.lost"); },
    unavailable: function () { return tHtml("stats.reason.unavailable"); },
    busy: function () { return tHtml("stats.reason.busy"); },
    closed: function () { return tHtml("stats.reason.closed"); },
    internal: function () { return tHtml("stats.reason.internal"); },
    token: function () { return tHtml("stats.reason.token"); },
    http: function (status) { return tHtml("stats.reason.http", { status: status }); }
  };

  /* The reason a report failed, as a code: "lost" when the monitor did not answer (the request itself failed), the reason of
   * a known text with its own status, else "http" (named by its status). Every answer the monitor gives the page reads as
   * it did; an answer with a text the page does not know is named by its status. */
  function statReason(e) {
    if (!e.status) {
      return "lost";
    }
    var found = Object.keys(STAT_ERRORS).filter(function (code) {
      return STAT_ERROR_STATUS[code] === e.status && STAT_ERRORS[code] === e.error;
    })[0];
    return found || "http";
  }

  /* The reason in words, as markup for a sentence's {reason}. */
  function statReasonHtml(e) {
    return markup(STAT_REASONS[statReason(e)](String(e.status)));
  }

  /* The whole sentence of a failed report, as markup: for the banner and for a Limits card's chart. */
  function statProblem(e) {
    if (e.status === 503) {
      return markup(tHtml("stats.problem.not_ready"));
    }
    if (e.status === 501) {
      return markup(tHtml("stats.problem.unavailable"));
    }
    return markup(tHtml("stats.problem.failed", { reason: statReasonHtml(e) }));
  }

  /* The one line a card shows in place of its numbers when its report failed (the whole sentence is in the banner). */
  function statProblemShort(e) {
    return markup(e.status === 503 ? tHtml("stats.short.not_ready") : e.status === 501 ? tHtml("stats.short.unavailable") :
      tHtml("stats.short.failed"));
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
    var title = tLine("banner.stats.old_title");
    return '<div class="nm-msg" role="status"><b' + lineLangAttr(title.lang) + ">" + esc(title.text) + "</b>" +
      tHtml("banner.stats.old_line", { reason: statReasonHtml(bad[0]), age: ageWords(fmtAge(Date.now() - bad[0].at), AGE_UNIT_IN_SENTENCE) }) + "</div>";
  }

  /* A banner with the whole sentence when a report failed and there is no older one to show: each card it feeds says only
   * that its numbers are not there. */
  function problemBanner(entries) {
    var bad = entries.filter(function (e) { return !e.data && e.error; });
    if (!bad.length) {
      return "";
    }
    var title = tLine("banner.stats.unavailable_title");
    return '<div class="nm-msg" role="status"><b' + lineLangAttr(title.lang) + ">" + esc(title.text) + "</b>" + htmlOf(statProblem(bad[0])) + "</div>";
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

  // A limit's severity in words (the Limits page's facts), by severityOf's code.
  var SEVERITY_WORDS = {
    normal: function () { return tLine("limits.severity.normal"); },
    warning: function () { return tLine("limits.severity.warning"); },
    critical: function () { return tLine("limits.severity.critical"); }
  };

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

  // A limit's pace chip in words, by its code (paceChip), and the card's pace line that says how far off pace it is. A chip
  // with no clock has no reset time, so its line is limit.diff.no_reset (limitCard).
  var PACE_WORDS = {
    unused: function () { return tLine("limit.pace.unused"); },
    no_draw: function () { return tLine("limit.pace.no_draw"); },
    ahead: function () { return tLine("limit.pace.ahead"); },
    under: function () { return tLine("limit.pace.under"); },
    on: function () { return tLine("limit.pace.on"); },
    no_clock: function () { return tLine("limit.pace.no_clock"); }
  };
  var PACE_DIFF = {
    unused: function (points) { return tLine("limit.diff.unused", { points: points }); },
    no_draw: function (points) { return tLine("limit.diff.no_draw", { points: points }); },
    ahead: function (points) { return tLine("limit.diff.ahead", { points: points }); },
    under: function (points) { return tLine("limit.diff.under", { points: points }); },
    on: function (points) { return tLine("limit.diff.on", { points: points }); }
  };

  /* The chip of a limit: its code, its words (text, lang), its class and its tone (data-tone: the status, which Gates
   * colours). */
  function paceChip(limit, p, scoped) {
    var pct = num(limit.percent) || 0;
    var code = pct <= 0 ? (scoped ? "no_draw" : "unused") : p.kind === "ahead" ? "ahead" : p.kind === "behind" ? "under" :
      p.kind === "on_pace" ? "on" : "no_clock";
    var line = PACE_WORDS[code]();
    return { code: code, text: line.text, lang: line.lang, cls: code === "ahead" ? " solid" : "",
      tone: code === "ahead" ? "warn" : code === "under" || code === "on" ? "ok" : "neutral" };
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
    var gone = p.elapsed === null ? 0 : p.elapsed * 100;
    return '<div class="nm-rail" style="--t:' + gone.toFixed(1) + '%" title="' + esc(t("limit.rail.title")) + '"><i></i><b></b></div>';
  }

  /* The time left in a window as a bar: filled by the share of the window that remains, labelled with the time. Every
   * theme gets the markup and only the one that draws it (Night City's Edgerun) shows it. */
  function remainHTML(p) {
    if (p.reset === null || p.elapsed === null) {
      return "";
    }
    var left = fmtRemain(p.remain);
    var text = t("limit.remain.label", { time: left, reset: clockText(p.reset) });   // the bar's own forms: 3 H 45 M
    var label = tLine("limit.remain.title");
    return '<div class="nm-remain" style="--r:' + ((1 - p.elapsed) * 100).toFixed(1) + '%" role="img" aria-label="' + esc(text) + '" title="' + esc(text) +
      '"><span class="l"' + lineAttrs(label) + ">" + esc(label.text) + '</span><span class="v">' + esc(left) + "</span></div>";
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
        return "<b>" + tHtml("limit.forecast.limited") + "</b>";
      case "AT_RISK":
        return tHtml("limit.forecast.at_risk", { lead: boldLead(tHtml("limit.forecast.at_risk_lead", { time: clockText(parseTime(blk.projected_limit_at)) })) });
      case "SAFE":
        return tHtml("limit.forecast.safe", { lead: boldLead(tHtml("limit.forecast.safe_lead")) });
      case "IDLE":
        return tHtml("limit.forecast.idle", { lead: boldLead(tHtml("limit.forecast.idle_lead")) });
      default:
        return tHtml("limit.forecast.more_readings");
    }
  }

  /* The bold words that lead a forecast line, for its {lead} or {end}. */
  function boldLead(html) {
    return markup("<b>" + html + "</b>");
  }

  function weeklyForecast(blk) {
    if (!blk || num(blk.percent) === null) {
      return "";
    }
    var end = num(blk.projected_end_pct);
    var budget = num(blk.daily_budget_pct);
    var lead = end === null ? markup(tHtml("limit.forecast.more_time")) : boldLead(tHtml("limit.forecast.ends_lead", { percent: FMT.percent(String(Math.round(end))) }));
    return budget === null ? lead.html : tHtml("limit.forecast.weekly", { end: lead, budget: FMT.percent(String(Math.round(budget))) });
  }

  /* The pacing row of a scoped limit: the one of the same kind and scope, its model and its surface (the labels of two limits
   * can be the same, and a limit scoped by surface has no model), else the one at its place in the list. */
  function matchScoped(list, limit, index) {
    var found = (list || []).filter(function (b) {
      return b.kind === limit.kind && (b.scope_model || null) === (limit.scope_model || null) &&
        (b.scope_surface || null) === (limit.scope_surface || null);
    })[0];
    return found || (list || [])[index] || null;
  }

  /* The tag of a limit's card (a line). English shows the limit's own label, the identity string the data layer gives (#90:
   * "SESSION", "FABLE WEEKLY"), as a name. Another language names the limit from its kind and scope, as the widgets do (#93),
   * the scope as the usage API names it; a kind it has no words for keeps the limit's label. The scoped limit of a page that
   * waits for its first reading is "Scoped". */
  function limitTag(limit) {
    if (limit.label && I18N.language === "en") {
      return nameLine(limit.label);
    }
    if (limit.kind === "session") {
      return tLine("limit.name.session");
    }
    if (limit.kind === "weekly_all") {
      return tLine("limit.name.weekly");
    }
    if (limit.kind === "weekly_scoped") {
      var scope = limit.scope_model || limit.scope_surface || "";
      return limit.placeholder ? tLine("limit.name.scoped_pending") : scope ? tLine("limit.name.scoped", { name: scope }) :
        tLine("limit.name.scoped_unnamed");
    }
    return nameLine(limit.label || FMT.upper(String(limit.kind || "").replace(/_/g, " "), "en"));
  }

  /* The sign and the whole points of a pace difference: "+12", "-30". */
  function pacePoints(diff) {
    return (diff >= 0 ? "+" : "") + FMT.number(String(Math.round(diff)), NO_GROUP);
  }

  function limitCard(limit, opts) {
    opts = opts || {};
    var now = Date.now();
    var p = paceOf(limit, now);
    var pct = num(limit.percent) || 0;
    var chip = paceChip(limit, p, !!opts.share);
    var tag = limitTag(limit);
    var facts = [];
    if (opts.share) {
      var share = tLine("limit.fact.share", { percent: fmtPct(opts.share) });
      facts.push("<span" + lineAttrs(share) + ">" + esc(share.text) + "</span>");
    }
    if (p.reset !== null) {
      var resets = tHtmlLine("limit.fact.resets", { time: markup("<b>" + esc(clockText(p.reset)) + "</b>") });
      facts.push("<span" + lineAttrs(resets) + ">" + resets.html + "</span>");
      if (!opts.share) {
        var left = tLine("limit.fact.in", { time: fmtRemain(p.remain, true) });
        facts.push("<span" + lineAttrs(left) + ">" + esc(left.text) + "</span>");
      }
    }
    facts.push('<span class="nm-chip' + chip.cls + '" data-tone="' + chip.tone + '"' + lineAttrs(chip) + ">" + esc(chip.text) + "</span>");
    var diff = p.diff === null ? tLine("limit.diff.no_reset") : PACE_DIFF[chip.code](pacePoints(p.diff));
    var pace = tLine("limit.pace.line", { used: fmtPct(pct), elapsed: p.elapsed === null ? "--" : fmtPct(p.elapsed * 100) });
    var pending = tLine("limit.forecast.more_readings");
    return '<section class="nm-card' + (opts.wide ? " wide" : "") + sevClass(limit) + '" data-limit="' + esc(limit.kind) + '">' +
      '<div class="nm-card-in">' +
      '<div class="nm-card-t"><span class="nm-tag"' + lineAttrs(tag) + ">" + esc(tag.text) + '</span><span class="nm-meta-t"' + lineAttrs(opts.meta) + ">" +
      esc(lineText(opts.meta) || "") + "</span></div>" +
      '<div class="nm-row2"><div class="nm-big">' + esc(fmtPct(pct)) + '</div><div class="nm-facts">' + facts.join("") + "</div></div>" +
      remainHTML(p) +
      '<div class="nm-pace"><span' + lineAttrs(pace) + ">" + esc(pace.text) + "</span><b" + lineAttrs(diff) + ">" + esc(diff.text) + "</b>" +
      (opts.fcPending ? '<span class="fc" data-pending=""' + lineAttrs(pending) + ">" + esc(pending.text) + "</span>" : opts.fc ? '<span class="fc">' + opts.fc + "</span>" : "") + "</div>" +
      '<div class="nm-cells" aria-hidden="true">' + cellsHTML(32, pct) + "</div>" +
      railHTML(p) +
      "</div></section>";
  }

  /* A limit's card before its first reading, or with no limit to show: tag and meta are lines. */
  function emptyLimitCard(tag, meta) {
    var waiting = tLine("overview.limit.waiting");
    return '<section class="nm-card"><div class="nm-card-in"><div class="nm-card-t"><span class="nm-tag"' + lineAttrs(tag) + ">" + esc(tag.text) +
      '</span><span class="nm-meta-t"' + lineAttrs(meta) + ">" + esc(meta.text) + '</span></div><p class="nm-empty"' + lineAttrs(waiting) + ">" +
      esc(waiting.text) + "</p></div></section>";
  }

  // ── Building blocks shared by the pages ─────────────────────────────────────────

  /* A card. o: {key, title | tag, meta (text) or metaHtml (markup, for controls), cls, id, body, label}. key is the card's id
   * (data-card): what the Gates icons and the scripts name it by. The title is its words, for people, and never a key. */
  function card(o) {
    // title, tag, meta and label may be lines (tLine): an English one on another language's page marks its element itself
    var meta = o.metaHtml !== undefined ? o.metaHtml : '<span class="nm-meta-t"' + lineAttrs(o.meta) + ">" + esc(lineText(o.meta) || "") + "</span>";
    var head = o.tag
      ? '<div class="nm-card-t"><span class="nm-tag"' + lineAttrs(o.tag) + ">" + esc(lineText(o.tag)) + "</span>" + meta + "</div>"
      : '<div class="nm-card-t"><h2 class="nm-ttl"' + lineAttrs(o.title) + ">" + esc(lineText(o.title)) + "</h2>" + meta + "</div>";
    return '<section class="nm-card' + (o.cls ? " " + o.cls : "") + '"' + (o.id ? ' id="' + o.id + '"' : "") +
      ' aria-label="' + esc(lineText(o.label) || lineText(o.title) || lineText(o.tag) || "") + '" data-card="' + esc(o.key) + '"><div class="nm-card-in">' + head + o.body +
      "</div></section>";
  }

  /* The heading of a page. Its words are the heading's and the sentence's own text, never in a span of their own: the themes'
   * entrances type and decode .nm-h1 and .nm-sub by their own text (nostromo.js, night_city.js). English on another
   * language's page is marked on the element itself. */
  function pageHead(key) {
    var head = entryOf(HEADINGS, key, "overview")();
    return '<div class="nm-head"><h1 class="nm-h1"' + lineLangAttr(head.title.lang) + ">" + esc(head.title.text) +
      '<span class="nm-cur" aria-hidden="true"></span></h1><p class="nm-sub"' + lineLangAttr(head.sub.lang) + ">" + esc(head.sub.text) + "</p></div>";
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
        host.innerHTML = '<p class="nm-error" role="status">' + tHtml("stats.chart_failed") + "</p>";
        if (window.console && console.error) {
          console.error("chart failed", e);
        }
      }
    }
  }

  /* The line under the heading when the link is not live. No sign-in and an expired one are the two states only the user can fix,
   * so they get the help panel (signInHelp); the rest stay a plain line. */
  function statusBanner() {
    var s = st();
    if (!s || s.status === "live") {
      return "";
    }
    var line = linkLine(s.status);
    if (needsSignIn(s)) {
      return signInHelp(s, line);
    }
    return '<div class="nm-msg" role="status"><b' + lineLangAttr(line.lang) + ">" + esc(line.text) + "</b>" + esc(s.status_detail || "") + "</div>";
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

  /* The caption under the bar: what one cell is, as plain text (the Cost & value page escapes it) and the language it came
   * from: { text, lang }. The plan's name goes in as written. */
  function ratioNoteLine(ratio, planName) {
    var step = ratioScale(ratio);
    var count = FMT.number(String(step), NO_GROUP);
    return planName
      ? { text: tPlural("ratio.note", step, { count: count, plan: planName }), lang: tLang("ratio.note") }
      : { text: tPlural("ratio.note_unnamed", step, { count: count }), lang: tLang("ratio.note_unnamed") };
  }

  function ratioNote(ratio, planName) {
    return ratioNoteLine(ratio, planName).text;
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
    var named = step > 1 ? tPlural("ratio.scaled", step, { label: label, count: FMT.number(String(step), NO_GROUP) }) : label;
    return '<div class="nm-ratio" role="img" aria-label="' + esc(named) + '">' + out + "</div>";
  }

  /* A row of cells with a label and a value: the model mix and the pace meter use it. The label may be a line (tLine, nameLine):
   * it is the label's own text, which Nostromo's entrance types. */
  function mixRow(label, percent, valueText, title) {
    return '<div class="nm-mrow"><span class="k"' + (title ? ' title="' + esc(title) + '"' : "") + lineAttrs(label) + ">" + esc(lineText(label)) +
      '</span><div class="nm-cells" aria-hidden="true">' + cellsHTML(20, percent) + '</div><span class="v">' + esc(valueText) + "</span></div>";
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

  /* The limits, the surface split and the credits as the monitor will send them. Their labels are the identity strings the
   * data layer gives (identity.py), which English shows; placeholder marks them, so another language names the scoped one
   * "Scoped" (limitTag) and not after its stand-in scope. */
  function skelUsage() {
    var now = stillNow();
    function limit(kind, label, seconds, scope) {
      return {
        kind: kind, label: label, percent: 0, severity: "normal", window_seconds: seconds,
        resets_at: new Date(now + seconds * 500).toISOString(), scope_model: scope || null,
        allowance_pct: scope ? 50 : null, used_pct_of_weekly: scope ? 0 : null, placeholder: true
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
   * when its report failed. needs: the stat entries it draws from. none: a line to show in place of its contents when there is
   * nothing for it to count (a PC with no Claude Code activity; the card is ready, it needs no report). */
  function cardPart(key, state, needs, build, none) {
    S.chartCard = key;
    S.chartN = 0;
    var html = build();
    if (state === "error") {
      var failed = needs.filter(function (e) { return !e.data && e.error; })[0];
      html = errorSwap(html, failed ? statProblemShort(failed) : markup(tHtml("stats.short.none")));
    } else if (none) {
      html = emptySwap(html, none);
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

  /* The card as it is with one line in the place of its contents: the same frame and title. */
  function cardSwap(html, line) {
    var open = /^<section\b[^>]*>/.exec(html);
    var head = /<div class="nm-card-t[^"]*">[\s\S]*?<\/div>/.exec(html);
    if (!open || !head) {
      return html;
    }
    return open[0] + '<div class="nm-card-in">' + head[0] + line + "</div></section>";
  }

  /* ... with the problem, when the card's report failed. message: plain text, or markup this file built (a catalog line). */
  function errorSwap(html, message) {
    return cardSwap(html, '<p class="nm-error" role="status">' + htmlOf(message) + "</p>");
  }

  /* ... with what it would have counted, when there is nothing to count. message: as errorSwap's. */
  function emptySwap(html, message) {
    return cardSwap(html, '<p class="nm-empty">' + htmlOf(message) + "</p>");
  }

  var FIGURES = ".nm-big, .nm-stat-v, .nm-stat-s, .nm-mrow .v, .nm-kv b, .nm-facts > span, .nm-pace > span, .nm-pace > b, .nm-pace > .fc, " +
    ".nm-story, .nm-table td.r, .nm-table td.nm-trunc, .nm-odo-facts b, .nm-cmp-n, .nm-toolbar-n";
  var BARS = ".nm-cells, .nm-ratio";
  var CHARTS_SEL = ".nm-chart-host, .mon-spark, .mon-stack, .nm-odo";

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
   * arrive(card, info) and a mon:card-data event on the card; for the page, a mon:page-rendered event on #main. */
  function announce(main, key, opened, ctx) {
    var fx = themeFx();
    var reduced = reduceMotion();
    var entering = main.classList.contains("fx-run");
    ctx.updated.forEach(function (u) {
      var info = { page: key, card: u.key, first: u.first, from: u.from, to: u.to, fresh: u.fresh, entering: entering, reduced: reduced };
      if (fx && typeof fx.arrive === "function" && !reduced) {
        guarded("arrive", function () { fx.arrive(u.el, info); });
      }
      dispatch(u.el, "mon:card-data", info);
    });
    dispatch(main, "mon:page-rendered", {
      page: key, opened: opened, reduced: reduced,
      updated: ctx.updated.map(function (u) { return u.key; }), added: ctx.added.map(function (a) { return a.key; })
    });
  }

  // ── The theme's motion hooks ────────────────────────────────────────────────────

  function themeFx() {
    var t = S.lastTheme;
    return t && window.USAGE_MONITOR_THEME_FX ? window.USAGE_MONITOR_THEME_FX[t.id] : null;
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

  // ── A PC with no Claude Code activity (#28) ─────────────────────────────────────

  /* True when the monitor has finished reading this PC's Claude Code records and found none: the all-time window holds no request,
   * and an ingest is done (not running, and it has taken a time) with nothing it could not read (a file it could not read may hold
   * requests). Someone who uses Claude on the web or in the app has none, and the pages that count records would be cards full of
   * zeros. It is never decided before a state has arrived or while the first ingest still runs (the windows read zero then because
   * nothing is counted yet). Once decided it holds through the ingests that
   * follow (one runs every minute, and the state says "running" while it does) and ends when a request is counted. */
  function limitsOnly() {
    var s = st();
    var all = windowOf("all");
    var requests = all && all.totals ? num(all.totals.requests) : null;
    if (!s || !s.ingest || requests === null) {
      return S.noActivity;
    }
    if (requests > 0) {
      S.noActivity = false;
    } else if (!s.ingest.running && num(s.ingest.last_duration_s) !== null && !s.ingest.last_error) {
      S.noActivity = true;
    }
    return S.noActivity;
  }

  /* Cost & value, Efficiency and History on such a PC: one card that says why there is nothing, in place of cards full of zeros. */
  function noActivityPage(key) {
    var cards = [cardPart("none", "ready", [], function () {
      return card({ key: "none", title: NO_ACTIVITY, cls: "wide", body: '<p class="nm-lede">' + esc(NO_ACTIVITY_WHY) + "</p>" });
    })];
    return pageModel(key, "nm-pgrid", cards);
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

  /* A plan's name for people: the catalog's "Unknown plan" for a plan whose sign-in names no subscription (#90: the data layer
   * keeps that English text in its name, and a cost report copies it), else the plan's own name, as written. */
  function planWords(plan) {
    return plan && plan.name && plan.subscription_type === null ? t("plan.unknown") : plan ? plan.name : "";
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
      out.planName = planWords(d.plan);
      out.price = num(d.plan.price_usd);
      out.cycleStart = d.value.cycle_start;
      out.cycleEnd = d.value.cycle_end;
      out.models = d.breakdowns.by_model.slice(0, 4).map(function (m) { return { id: m.model, name: m.name, share: m.share * 100 }; });
      return out;
    }
    var s = st();
    var month = windowOf("month"), today = windowOf("today"), week = windowOf("week");
    var calendarCycle = S.settings.billing_day === null || S.settings.billing_day === undefined;
    out.planName = s && s.plan ? planWords(s.plan) : "";
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
    return FMT.upper(FMT.monthDay(new Date(a))) + " - " + FMT.upper(FMT.monthDay(new Date(b)));
  }

  /* The last 30 days of cost as columns (one pip is a round dollar amount), with the average as a dashed line. */
  function dailyChart(cycleStart) {
    var he = statEntry("history");
    var d = he.data;
    var title = { text: tPlural("overview.daily.title", DAILY_DAYS, { count: fmtInt(DAILY_DAYS) }), lang: tLang("overview.daily.title") };
    if (!d && he.error) {
      return '<div class="nm-card-t nm-chart-t"><span class="nm-ttl"' + lineAttrs(title) + ">" + esc(title.text) + '</span><span class="nm-meta-t">&nbsp;</span></div>' +
        '<div class="nm-chart"><p class="nm-pending">' + tHtml("overview.daily.unavailable") + "</p></div>";
    }
    var waiting = !d;
    if (waiting) {
      d = skelHistory();
    }
    var days = d.days.slice(-DAILY_DAYS);
    var costs = days.map(function (x) { return num(x.cost_usd) || 0; });
    var max = Math.max.apply(null, costs.concat([0]));
    var avg = costs.length ? costs.reduce(function (a, v) { return a + v; }, 0) / costs.length : 0;
    var step = CHARTS.niceStep(max / 14);
    var pipsOn = CHARTS.refresh();
    var meta = waiting ? tLine("overview.daily.top", { amount: TOP_PLACEHOLDER }) : max <= 0 ? tLine("overview.daily.no_use") :
      pipsOn ? tLine("overview.daily.pip", { amount: fmtPlanPrice(step) }) : tLine("overview.daily.top", { amount: fmtUsdShort(max) });
    var labels = [{ i: days.length - 1, text: tUpper("overview.daily.today") }];
    if (cycleStart) {
      var cs = ymdOf(new Date(parseTime(cycleStart)));
      days.forEach(function (x, i) { if (x.date === cs && i > 0) { labels.push({ i: i, text: dayLabel(x.date) }); } });
    }
    labels.push({ i: 0, text: dayLabel(days[0] && days[0].date) });
    var tips = days.map(function (x) {
      return tPlural("overview.daily.tip", Math.round(num(x.requests) || 0), { day: dayText(x.date), cost: fmtUsd(x.cost_usd), count: fmtInt(x.requests) });
    });
    var avgText = avg >= 1000 ? fmtUsdShort(avg) : avg < 10 ? fmtUsd(avg) :
      FMT.english ? "$" + Math.round(avg) : FMT.usd(String(Math.round(avg)));
    var host = chartHost(function (w) {
      return CHARTS.columns(costs, {
        width: w, height: 132, gutterRight: 68, step: step, labels: labels, tips: tips, current: days.length - 1,
        avg: { value: avg, label: tUpper("overview.daily.avg", { amount: avgText }) },
        label: tPlural("overview.daily.label", DAILY_DAYS, { count: fmtInt(DAILY_DAYS), amount: fmtUsd(avg) })
      });
    }, [he.digest, pipsOn, cycleStart], "nm-daily", waiting);
    return '<div class="nm-card-t nm-chart-t"><span class="nm-ttl"' + lineAttrs(title) + ">" + esc(title.text) + '</span><span class="nm-meta-t"' + (waiting ? ' data-pending=""' : "") +
      lineAttrs(meta) + ">" + esc(meta.text) + "</span></div>" +
      '<div class="nm-chart">' + host + "</div>";
  }

  function dayLabel(ymd) {
    var d = parseYmd(ymd);
    return d ? FMT.upper(FMT.monthDay(d)) : "";
  }

  function costCard(n) {
    var heroText = fmtUsdHero(n.cycle);
    var planBlock;
    if (n.price === null) {
      planBlock = '<div class="nm-plan"><div class="nm-big m">--</div><div class="nm-facts"><span>' + tHtml("overview.cost.price_unknown") + "</span><span>" +
        tHtml("overview.cost.set_price") + "</span></div></div>";
    } else {
      var proj = n.projected !== null ? t("overview.cost.projection", { amount: fmtUsd(n.projected), multiple: fmtMult(n.projected / n.price) }) : "";
      // the plan's name in the page's capitals, as written (#91); a plan with no name is the catalog's
      var plan = n.planName ? tName(FMT.upper(n.planName)) : markup(tHtml("overview.cost.plan_fallback"));
      planBlock = '<div class="nm-plan"><div class="nm-big m"' + (proj ? ' title="' + esc(proj) + '"' : "") + ">" + esc(fmtMult(n.multiplier)) + '</div><div class="nm-facts"><span>' +
        tHtml("overview.cost.your_plan") + "</span><span>" + tHtml("overview.cost.plan_price", { plan: plan, price: fmtPlanPrice(n.price) }) + "</span></div></div>";
    }
    var ratio = n.multiplier;
    var note = n.price === null ? tLine("overview.cost.no_multiple") : ratioNoteLine(ratio, n.planName);
    var title = tLine("overview.cost.title");
    var meta = n.cycleStart ? tLine("overview.cost.meta_range", { range: rangeText(n.cycleStart, n.cycleEnd) }) : tLine("overview.cost.meta");
    var today = tLine("overview.cost.today"), week = tLine("overview.cost.week");
    return '<section class="nm-card wide" id="cost" aria-label="' + esc(title.text) + '" data-card="cost"><div class="nm-card-in">' +
      '<div class="nm-card-t"><h2 class="nm-ttl"' + lineAttrs(title) + ">" + esc(title.text) + '</h2><span class="nm-meta-t"' + lineAttrs(meta) + ">" + esc(meta.text) + "</span></div>" +
      '<div class="nm-cost"><div class="nm-cost-l"><div class="nm-hero"><div class="nm-big h" style="--n:' + heroText.length + '">' + esc(heroText) + "</div>" + planBlock + "</div>" +
      (ratio === null || n.price === null ? "" : ratioCells(ratio, t("overview.cost.ratio_label", { multiple: fmtMult(ratio) }), n.pending ? 12 : 0)) +
      '<div class="nm-note"' + lineAttrs(note) + ">" + esc(note.text) + "</div>" +
      '<div class="nm-note nm-disc">' + tHtml("overview.cost.basis") + "</div>" +
      '<div class="nm-tiles"><div class="nm-tile"><small' + lineAttrs(today) + ">" + esc(today.text) + '</small><div class="nm-big m">' + esc(fmtUsdTile(n.today)) +
      '</div></div><div class="nm-tile"><small' + lineAttrs(week) + ">" + esc(week.text) + '</small><div class="nm-big m">' + esc(fmtUsdTile(n.week)) + "</div></div></div></div>" +
      '<div class="nm-cost-r">' + dailyChart(n.cycleStart) + "</div>" +
      "</div></div></section>";
  }

  function efficiencyCard(n) {
    var hit = n.hit === null ? null : n.hit * 100;
    var thinking = n.thinking === null ? null : n.thinking * 100;
    var title = tLine("overview.efficiency.title");
    var meta = n.loaded ? tLine("overview.efficiency.cycle") : tLine("overview.efficiency.month");
    return '<section class="nm-card" id="efficiency" aria-label="' + esc(t("overview.efficiency.label")) + '" data-card="efficiency"><div class="nm-card-in">' +
      '<div class="nm-card-t"><h2 class="nm-ttl"' + lineAttrs(title) + ">" + esc(title.text) + '</h2><span class="nm-meta-t"' + lineAttrs(meta) + ">" + esc(meta.text) + "</span></div>" +
      '<div class="nm-kpi"><div class="nm-kl">' + tHtml("overview.efficiency.hit") + '</div><div class="nm-big m">' + esc(hit === null ? "--" : FMT.percent(hit.toFixed(1))) +
      '</div><div class="nm-cells" aria-hidden="true">' + cellsHTML(20, hit || 0) + "</div></div>" +
      '<div class="nm-kpi"><div class="nm-kl">' + tHtml("overview.efficiency.thinking") + '</div><div class="nm-big m">' + esc(thinking === null ? "--" : fmtPct(thinking)) +
      '</div><div class="nm-cells" aria-hidden="true">' + cellsHTML(20, thinking || 0) + "</div></div>" +
      '<div class="nm-kpi is-row"><div class="nm-kl">' + tHtml("overview.efficiency.saved") + '</div><div class="nm-big s" title="' + esc(t("overview.efficiency.saved_title")) + '">' +
      esc(fmtUsdShort(n.saved)) + "</div></div>" +
      "</div></section>";
  }

  function modelMixCard(n) {
    var rows = n.models;
    var body = rows.length
      ? rows.map(function (r) {
        var name = nameLine(FMT.upper(r.name));
        return '<div class="nm-mrow"><span class="k" title="' + esc(r.id) + '"' + lineAttrs(name) + ">" + esc(name.text) + '</span><div class="nm-cells" aria-hidden="true">' +
          cellsHTML(20, r.share) + '</div><span class="v">' + esc(fmtPct(r.share)) + "</span></div>";
      }).join("")
      : '<p class="nm-empty">' + tHtml("overview.models.none") + "</p>";
    var title = tLine("overview.models.title");
    var meta = n.loaded ? tLine("overview.models.cycle") : tLine("overview.models.month");
    return '<section class="nm-card" id="models" aria-label="' + esc(title.text) + '" data-card="models"><div class="nm-card-in"><div class="nm-card-t"><h2 class="nm-ttl"' + lineAttrs(title) + ">" +
      esc(title.text) + '</h2><span class="nm-meta-t"' + lineAttrs(meta) + ">" + esc(meta.text) + "</span></div>" +
      '<div class="nm-mix">' + body + "</div></div></section>";
  }

  function surfaceCard(u) {
    var rows = u && u.breakdown ? u.breakdown : [];
    var body = rows.length
      ? rows.map(function (r) {
        var name = nameLine(FMT.upper(String(r.name || r.key || "")));
        return '<div class="nm-mrow"><span class="k"' + lineAttrs(name) + ">" + esc(name.text) + '</span><div class="nm-cells" aria-hidden="true">' +
          cellsHTML(20, r.percent) + '</div><span class="v">' + esc(fmtPct(r.percent)) + "</span></div>";
      }).join("")
      : '<p class="nm-empty">' + tHtml("overview.surfaces.none") + "</p>";
    var title = tLine("overview.surfaces.title");
    var meta = tLine("overview.surfaces.meta");
    return '<section class="nm-card" aria-label="' + esc(title.text) + '" data-card="surfaces"><div class="nm-card-in"><div class="nm-card-t"><h2 class="nm-ttl"' + lineAttrs(title) + ">" +
      esc(title.text) + '</h2><span class="nm-meta-t"' + lineAttrs(meta) + ">" + esc(meta.text) + "</span></div>" +
      '<div class="nm-mix">' + body + "</div></div></section>";
  }

  // The tone of a burn velocity, by the code the monitor sends (IDLE, SLOW, NORMAL, FAST or VERY FAST); the others are neutral.
  var VELOCITY_TONE = { NORMAL: "ok", FAST: "acc", "VERY FAST": "warn" };
  // Its words, by the same code. A code the table does not name is shown as it came, as a name.
  var VELOCITY_WORDS = {
    IDLE: function () { return tLine("overview.burn.velocity.idle"); },
    SLOW: function () { return tLine("overview.burn.velocity.slow"); },
    NORMAL: function () { return tLine("overview.burn.velocity.normal"); },
    FAST: function () { return tLine("overview.burn.velocity.fast"); },
    "VERY FAST": function () { return tLine("overview.burn.velocity.very_fast"); }
  };

  function burnCard(s) {
    var burn = s && s.burn ? s.burn : null;
    var velocity = burn ? String(burn.velocity || "IDLE") : "IDLE";
    var unit = burnUnitLine();
    var title = tLine("overview.burn.title");
    var chip = Object.prototype.hasOwnProperty.call(VELOCITY_WORDS, velocity) ? VELOCITY_WORDS[velocity]() : nameLine(velocity);
    var hour = burnHourLine(burn);
    // Until two samples are in (one every poll), the trace is its frame alone.
    var spark = S.samples.length > 1
      ? CHARTS.sparkline(S.samples, { width: 300, height: 58, label: t("overview.burn.spark") })
      : CHARTS.sparkline([], { width: 300, height: 58, label: t("overview.burn.sampling") }).replace("<svg ", '<svg data-pending="" ');
    return '<section class="nm-card" id="burn" aria-label="' + esc(title.text) + '" data-card="burn"><div class="nm-card-in"><div class="nm-card-t"><h2 class="nm-ttl"' + lineAttrs(title) + ">" +
      esc(title.text) + '</h2><span class="nm-chip" data-tone="' + toneOf(VELOCITY_TONE, velocity) + '"' + lineAttrs(chip) + ">" + esc(chip.text) + "</span></div>" +
      '<div class="nm-row2 is-base"><div class="nm-big m">' + esc(burn ? fmtInt(burn.tokens_per_min) : "--") +
      '</div><div class="nm-facts"><span' + lineLangAttr(unit.lang) + ">" + esc(unit.text) + "</span><span" + lineAttrs(hour) + ">" + esc(hour.text) + "</span></div></div>" +
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
      return session ? limitCard(session, { meta: tLine("limit.meta.session"), fc: pd ? sessionForecast(pd.session) : "", fcPending: forecast }) :
        emptyLimitCard(tLine("limit.name.session"), tLine("limit.meta.session"));
    }));
    cards.push(cardPart("limit-weekly", waiting, [pe], function () {
      return weekly ? limitCard(weekly, { meta: tLine("limit.meta.weekly"), fc: pd ? weeklyForecast(pd.weekly) : "", fcPending: forecast }) :
        emptyLimitCard(tLine("limit.name.weekly"), tLine("limit.meta.weekly"));
    }));
    cards.push(cardPart("limit-scoped", waiting, [pe], function () {
      if (!scoped.length) {
        return emptyLimitCard(tLine("limit.name.scoped_pending"), tLine("overview.limit.no_scoped"));
      }
      var first = scoped[0];
      return limitCard(first, { meta: tLine("limit.meta.weekly"), share: num(first.allowance_pct), fc: pd ? weeklyForecast(matchScoped(pd.scoped, first, 0)) : "",
        fcPending: forecast });
    }));
    var n = costNumbers();
    var he = statEntry("history");
    // No Claude Code activity on this PC (limitsOnly): the three cards that count records say so in place of their figures, and
    // they ask for no report, so they are ready, with no problem to report.
    var none = limitsOnly() ? markup(tHtml("overview.no_activity", { product: tName(CLAUDE_CODE) })) : "";
    // Nothing to show yet (n.pending): the cost report is on its way, or it failed and the monitor has no month of its own to
    // stand in (a billing day is set). The three cards that draw from it say which, as they do on the Cost page.
    var costState = n.pending && !none ? entryState([n.entry]) : "ready";
    cards.push(cardPart("cost", costState, none ? [] : [n.entry, he], function () { return costCard(n); }, none));
    cards.push(cardPart("efficiency", costState, none ? [] : [n.entry], function () { return efficiencyCard(n); }, none));
    cards.push(cardPart("models", costState, none ? [] : [n.entry], function () { return modelMixCard(n); }, none));
    cards.push(cardPart("surfaces", waiting, [], function () { return surfaceCard(u); }));
    cards.push(cardPart("burn", waiting, [], function () { return burnCard(s); }));
    return pageModel("overview", "nm-grid", cards, {
      hazard: hazardBand(worstSeverity(limits())), problem: n.pending && !none ? problemBanner([n.entry]) : "",
      stale: staleBanner(none ? [pe] : [n.entry, pe, he])
    });
  }

  // ── Limits: pacing ──────────────────────────────────────────────────────────────

  // The verdicts of the pacing report, by its codes, in words; a code not named here reads as UNKNOWN.
  var SESSION_VERDICT = {
    LIMITED: function () { return tLine("limits.verdict.session.limited"); },
    AT_RISK: function () { return tLine("limits.verdict.session.at_risk"); },
    SAFE: function () { return tLine("limits.verdict.session.safe"); },
    IDLE: function () { return tLine("limits.verdict.session.idle"); },
    UNKNOWN: function () { return tLine("limits.verdict.session.unknown"); }
  };
  var WEEKLY_VERDICT = {
    AHEAD: function () { return tLine("limits.verdict.weekly.ahead"); },
    ON_PACE: function () { return tLine("limits.verdict.weekly.on_pace"); },
    BEHIND: function () { return tLine("limits.verdict.weekly.behind"); },
    UNKNOWN: function () { return tLine("limits.verdict.weekly.unknown"); }
  };
  // The tone of each verdict (data-tone); a verdict not named here is neutral.
  var SESSION_TONE = { LIMITED: "alert", AT_RISK: "warn", SAFE: "ok" };
  var WEEKLY_TONE = { AHEAD: "warn", ON_PACE: "ok", BEHIND: "ok" };

  /* Rows of a label and a value: [[label, value, pending, title]]. A row marked pending shows a placeholder where its value will
   * be; a title is the label's tooltip (the full text of a name that is shown short). The label and the value may be lines
   * (tLine): the value is its element's own text, which a theme's entrance types. */
  function kvGrid(pairs, cls) {
    return '<div class="nm-kv-grid' + (cls ? " " + cls : "") + '">' + pairs.map(function (row) {
      return '<div class="nm-kv"><span' + (row[3] ? ' title="' + esc(row[3]) + '"' : "") + lineAttrs(row[0]) + ">" + esc(lineText(row[0])) + "</span><b" +
        (row[2] ? ' data-pending="" aria-hidden="true"' : "") + lineAttrs(row[1]) + ">" + esc(lineText(row[1])) + "</b></div>";
    }).join("") + "</div>";
  }

  function signed(n, digits) {
    var v = num(n);
    if (v === null) {
      return "--";
    }
    return (v > 0 ? "+" : v < 0 ? "-" : "") + FMT.number(Math.abs(v).toFixed(digits === undefined ? 1 : digits), NO_GROUP);
  }

  /* A pacing card's verdict and the sentence under it: { chip, chipLang, tone, toneId, text, lang }. */
  function story(chip, tone, toneId, line) {
    return { chip: chip.text, chipLang: chip.lang, tone: tone, toneId: toneId, text: line.text, lang: line.lang };
  }

  function sessionStory(blk) {
    var v = blk && Object.prototype.hasOwnProperty.call(SESSION_VERDICT, blk.verdict) ? blk.verdict : "UNKNOWN";
    var reset = blk ? parseTime(blk.resets_at) : null;
    var line;
    if (v === "LIMITED") {
      line = tLine("limits.story.session.limited", { time: clockText(reset) });
    } else if (v === "AT_RISK") {
      line = tLine("limits.story.session.at_risk", { time: clockText(parseTime(blk.projected_limit_at)), reset: clockText(reset) });
    } else if (v === "SAFE") {
      line = tLine("limits.story.session.safe", { time: clockText(reset) });
    } else if (v === "IDLE") {
      line = tLine("limits.story.session.idle");
    } else {
      line = tLine("limits.story.session.unknown");
    }
    return story(SESSION_VERDICT[v](), v === "LIMITED" ? " is-crit" : v === "AT_RISK" ? " is-warn" : "", toneOf(SESSION_TONE, v), line);
  }

  /* A weekly figure of points for a sentence: one decimal ("12.3"), and the number its plural form is chosen by. */
  function storyPoints(delta) {
    var shown = Math.abs(num(delta) || 0).toFixed(1);
    return { text: FMT.number(shown, NO_GROUP), count: Number(shown) };
  }

  function weeklyStory(blk) {
    var v = blk && Object.prototype.hasOwnProperty.call(WEEKLY_VERDICT, blk.verdict) ? blk.verdict : "UNKNOWN";
    var points = storyPoints(blk ? blk.pace_delta_pp : null);
    var line;
    if (v === "AHEAD") {
      line = { text: tPlural("limits.story.weekly.ahead", points.count, { count: points.text }), lang: tLang("limits.story.weekly.ahead") };
    } else if (v === "BEHIND") {
      line = { text: tPlural("limits.story.weekly.behind", points.count, { count: points.text }), lang: tLang("limits.story.weekly.behind") };
    } else if (v === "ON_PACE") {
      line = tLine("limits.story.weekly.on_pace");
    } else {
      line = tLine("limits.story.weekly.unknown");
    }
    return story(WEEKLY_VERDICT[v](), v === "AHEAD" ? " is-warn" : "", toneOf(WEEKLY_TONE, v), line);
  }

  /* The hidden sentence a pacing card holds until its pacing report answers, as long as the one it waits for, in the page's
   * own words and formats (#98's critic 14): the session's with the clock of noon, a weekly one with the language's 00.0. */
  function pendingStory(kind) {
    if (kind === "session") {
      var noon = new Date(stillNow());
      noon.setHours(12, 0, 0, 0);
      return story(SESSION_VERDICT.UNKNOWN(), "", "neutral", tLine("limits.story.session.pending", { time: clockText(noon.getTime()) }));
    }
    var zeros = FMT.number("12.3", NO_GROUP).replace(/\d/g, "0");
    return story(WEEKLY_VERDICT.UNKNOWN(), "", "neutral",
      { text: tPlural("limits.story.weekly.behind", 12.3, { count: zeros }), lang: tLang("limits.story.weekly.behind") });
  }

  /* Time elapsed against usage: the gap between the two bars is the pace. */
  function meterHTML(elapsedPct, usedPct) {
    var label = t("limits.meter.label", { elapsed: fmtPct(elapsedPct, 1), used: fmtPct(usedPct, 1) });
    return '<div class="nm-meter" role="img" aria-label="' + esc(label) + '">' +
      mixRow(tLine("limits.meter.elapsed"), elapsedPct, fmtPct(elapsedPct)) + mixRow(tLine("limits.meter.used"), usedPct, fmtPct(usedPct)) + "</div>";
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
    var yTicks = [0, 25, 50, 75, 100].map(function (v) { return { v: v, label: FMT.percent(String(v)), limit: v === 100 }; });
    if (yMax > 100) {
      yTicks.push({ v: yMax, label: FMT.percent(String(yMax)) });
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
      forecastText = forecast.reaches === null ? "" : t("limits.chart.reaches", { percent: FMT.percent("100"), time: at });
      series.push({
        kind: "forecast", points: forecast.points,
        endLabel: forecast.reaches === null ? "" : "100% ~ " + at, endTip: forecastText
      });
    }
    var every = Math.max(1, Math.ceil(pts.length / 24));
    var marks = [];
    pts.forEach(function (p, i) {
      if (i % every === 0 || i === pts.length - 1) {
        marks.push({ i: i, tip: t("limits.chart.reading", { time: clockText(p[0]), percent: fmtPct(p[1], 1) }) });
      }
    });
    series.push({ kind: "actual", points: pts, marks: marks });
    var ticks = kind === "session" ? hourTicks(t0, t1) : dayTicks(t0, t1);
    var count = FMT.number(String(pts.length), NO_GROUP);
    var readings = kind === "session" ? tPlural("limits.chart.session", pts.length, { count: count }) : tPlural("limits.chart.weekly", pts.length, { count: count });
    var label = forecastText ? t("limits.chart.label", { readings: readings, forecast: forecastText }) : readings;
    return chartHost(function (w) {
      return CHARTS.line({
        width: w, height: 176, xMin: t0, xMax: t1, yMin: 0, yMax: yMax, yTicks: yTicks, xTicks: ticks,
        marks: nowMs >= t0 && nowMs <= t1 ? [{ t: nowMs, label: tUpper("limits.chart.now") }] : [], series: series, label: label
      });
    }, [digest, kind, nowMs - nowMs % 60000, t0, t1, !!pending], "nm-line", pending);
  }

  /* The facts under a limit. pend marks the rows that come from the pacing report as placeholders. */
  function limitFacts(limit, blk, kind, p, pend) {
    var span = windowSeconds(limit);
    var start = p.reset !== null ? p.reset - span * 1000 : null;
    var none = bareLine("--");
    var days = Math.round(span / 86400), hours = Math.round(span / 3600);
    var rows = [
      [tLine("limits.fact.label.resets"), p.reset === null ? "--" : stampText(p.reset)],
      [tLine("limits.fact.label.resets_in"), p.reset === null ? "--" : fmtRemain(p.remain)],
      [tLine("limits.fact.label.started"), start === null ? "--" : stampText(start)],
      [tLine("limits.fact.label.window"), span >= 86400
        ? { text: tPlural("limits.fact.value.days", days, { count: fmtInt(days) }), lang: tLang("limits.fact.value.days") }
        : { text: tPlural("limits.fact.value.hours", hours, { count: fmtInt(hours) }), lang: tLang("limits.fact.value.hours") }]
    ];
    if (kind === "session" && blk) {
      rows.push([tLine("limits.fact.label.rate"), num(blk.slope_pct_per_hour) === null ? none :
        tLine("limits.fact.value.rate", { points: signed(blk.slope_pct_per_hour) }), pend]);
      if (blk.projected_limit_at) {
        rows.push([tLine("limits.fact.label.limit_at"), clockText(parseTime(blk.projected_limit_at))]);
      }
    }
    if (kind !== "session" && blk) {
      rows.push([tLine("limits.fact.label.budget"), num(blk.daily_budget_pct) === null ? none :
        tLine("limits.fact.value.per_day", { percent: fmtPct(blk.daily_budget_pct, 1) }), pend]);
      rows.push([tLine("limits.fact.label.even"), num(blk.even_daily_pct) === null ? none :
        tLine("limits.fact.value.per_day", { percent: fmtPct(blk.even_daily_pct, 1) }), pend]);
      rows.push([tLine("limits.fact.label.end"), num(blk.projected_end_pct) === null ? tLine("limits.fact.value.needs_time") :
        fmtPct(blk.projected_end_pct, 1), pend]);
      rows.push([tLine("limits.fact.label.clock"), num(blk.pace_delta_pp) === null ? none :
        tLine("limits.fact.value.points", { points: signed(blk.pace_delta_pp) }), pend]);
      var forecast = !pend && p.reset !== null ? averageRateForecast(blk, p.reset - p.remain) : null;
      if (forecast && forecast.reaches !== null) {
        rows.push([tLine("limits.fact.label.limit_at"), clockText(forecast.reaches)]);
      }
    }
    if (kind === "scoped") {
      rows.push([tLine("limits.fact.label.allowance"), num(limit.allowance_pct) === null ? none :
        tLine("limits.fact.value.of_weekly", { percent: fmtPct(limit.allowance_pct) })]);
      rows.push([tLine("limits.fact.label.of_weekly"), num(limit.used_pct_of_weekly) === null ? "--" : fmtPct(limit.used_pct_of_weekly, 1)]);
    }
    rows.push([tLine("limits.fact.label.severity"), SEVERITY_WORDS[severityOf(limit)]()]);
    if (limit.locked_reason) {
      rows.push([tLine("limits.fact.label.locked"), limit.locked_reason]);       // the usage API's own text
    }
    if (limit.dollars) {
      rows.push([tLine("limits.fact.label.used"), fmtUsd(limit.dollars.used)], [tLine("limits.fact.label.limit"), fmtUsd(limit.dollars.limit)]);
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
    var said = waiting
      ? pendingStory(kind)
      : !blk && !entry.data
        ? story(bareLine(""), "", "", tLine("limits.story.unavailable"))
        : kind === "session" ? sessionStory(blk) : weeklyStory(blk);
    var elapsed = !waiting && blk && num(blk.elapsed_pct) !== null ? blk.elapsed_pct : (p.elapsed === null ? null : p.elapsed * 100);
    var tag = limitTag(limit);
    var meta = limit.scope_model ? tLine("limits.meta.scoped", { name: limit.scope_model }) : kind === "session" ? tLine("limit.meta.session") : tLine("limit.meta.weekly");
    var chart;
    if (blk) {
      chart = pacingChart(blk, kind, nowMs, entry.digest + "|" + tag.text, waiting) || '<p class="nm-empty">' + tHtml("limits.chart.none") + "</p>";
    } else if (entry.error && !entry.data) {
      chart = '<p class="nm-error" role="status">' + htmlOf(statProblem(entry)) + "</p>";
    } else {
      chart = '<p class="nm-empty">' + tHtml("limits.chart.no_data") + "</p>";
    }
    var hold = waiting ? ' data-pending=""' : "";
    var body = '<div class="nm-big-row"><div class="nm-big">' + esc(fmtPct(pct)) + '</div><div class="nm-verdict">' +
      (said.chip ? '<span class="nm-chip solid' + said.tone + '" data-tone="' + said.toneId + '"' + hold + lineLangAttr(said.chipLang) + ">" + esc(said.chip) + "</span>" : "") + "</div></div>" +
      '<p class="nm-story"' + hold + lineLangAttr(said.lang) + ">" + esc(said.text) + "</p>" +
      (elapsed === null ? "" : meterHTML(elapsed, pct)) + chart + limitFacts(limit, blk, kind, p, waiting);
    return '<section class="nm-card span-6' + sevClass(limit) + '" data-limit="' + esc(limit.kind) + '" aria-label="' + esc(tag.text) + '"><div class="nm-card-in">' +
      '<div class="nm-card-t"><span class="nm-tag"' + lineAttrs(tag) + ">" + esc(tag.text) + '</span><span class="nm-meta-t"' + lineAttrs(meta) + ">" + esc(meta.text) + "</span></div>" +
      body + "</div></section>";
  }

  /* The credits card's body. pending is the placeholder, the shape of the common case (credits off). */
  function spendCardBody(spend, pending) {
    if (pending) {
      return '<p class="nm-empty" data-pending="">' + tHtml("limits.credits.off") + "</p>" +
        '<p class="nm-note nm-disc" data-pending="">' + tHtml("limits.credits.when_on") + "</p>";
    }
    if (spend && spend.enabled) {
      return '<div class="nm-big-row"><div class="nm-big m">' + esc(fmtMoney(spend.used, spend.currency)) + '</div><div class="nm-facts"><span>' +
        tHtml("limits.credits.of", { limit: fmtMoney(spend.limit, spend.currency) }) + "</span><span>" + tHtml("limits.credits.used", { percent: fmtPct(spend.percent) }) +
        "</span></div></div>" +
        '<div class="nm-cells" aria-hidden="true">' + cellsHTML(24, num(spend.percent) || 0) + "</div>" +
        '<p class="nm-note nm-disc">' + tHtml("limits.credits.real") + "</p>";
    }
    return '<p class="nm-empty">' + tHtml("limits.credits.off") + "</p>" +
      '<p class="nm-note nm-disc">' + tHtml("limits.credits.when_on") + "</p>";
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
      cards.push(cardPart("limit-none", "ready", [], function () { return emptyLimitCard(tLine("limits.none.tag"), tLine("limits.none.meta")); }));
    }
    var spendCard = card({ key: "credits", title: tLine("limits.credits.title"), meta: tLine("limits.credits.meta"), cls: "is-real",
      body: spendCardBody(u && u.spend, !!(u && u.skeleton)) });
    var breakdown = u && u.breakdown ? u.breakdown : [];
    var colours = ["main", "alt", "dim"];
    var stack = breakdown.length
      ? CHARTS.stackedBar(breakdown.map(function (r, i) {
        return { value: r.percent, cls: colours[i % 3], tip: String(r.name || r.key || "") + ": " + fmtPct(r.percent) };
      }), { width: 520, height: 16, label: t("limits.surfaces.label") })
      : '<p class="nm-empty">' + tHtml("limits.surfaces.none") + "</p>";
    var legend = breakdown.map(function (r) {
      return mixRow(nameLine(FMT.upper(String(r.name || r.key || ""))), r.percent, fmtPct(r.percent));
    }).join("");
    var surfaceCard = card({
      key: "surfaces", title: tLine("limits.surfaces.title"), meta: tLine("limits.surfaces.meta"),
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
    var label = WINDOW_LABEL[name] || FMT.upper(name, "en");
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
    return card({ key: "value", title: "Value of your plan", meta: "this cycle", cls: "span-5", id: "value",
      body: body + '<p class="nm-note nm-disc">' + BASIS + ", not amounts billed to your plan.</p>" });
  }

  function monthLabel(ym) {
    var m = /^(\d{4})-(\d{2})$/.exec(String(ym || ""));
    return m ? FMT.upper(FMT.monthYear(m[1], +m[2] - 1)) : String(ym || "");
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
        return CHARTS.hbars(rows, { width: w, labelWidth: 64, fitLabels: true, valueWidth: 118, label: "API-equivalent cost by month" });
      }, [e.digest, "monthly"], "nm-hbars") + '<p class="nm-note">The bar is the month&rsquo;s API-equivalent cost; the multiple is against the plan price.</p>'
      : '<p class="nm-empty">No monthly history yet.</p>';
    return card({ key: "monthly", title: "Month by month", meta: "API-equivalent, with the plan multiple", cls: "span-7", body: body });
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
      esc(gone ? WINDOW_PHRASE[pick] + ", not loaded" : WINDOW_PHRASE[pick] + ", " + (waiting ? COST_NOTE_PLACEHOLDER : fmtUsd(winCost)) + " " + FMT.lower(BASIS, "en")) + "</span></div>";
  }

  function costPage() {
    if (limitsOnly()) {
      return noActivityPage("cost");
    }
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
        key: "windows", title: "Windows", meta: "requests, tokens and cost by window", cls: "wide",
        body: '<div class="nm-table-wrap"><table class="nm-table"><caption class="nm-sr">Cost by time window, API-equivalent at list prices</caption><thead><tr><th scope="col">Window</th><th scope="col" class="r">Requests</th><th scope="col" class="r">Tokens</th>' +
          '<th scope="col" class="r">Cache hit</th><th scope="col" class="r">Saved by cache</th><th scope="col" class="r">API-equiv.</th></tr></thead><tbody>' +
          WINDOW_ORDER.map(function (name) { return windowRow(name, base.windows[name]); }).join("") + "</tbody></table></div>"
      });
    }));
    var winCost = src ? num(base.windows[pick] && base.windows[pick].cost_usd) : null;
    cards.push(part("toolbar", costToolbar(pick, winCost, whole === "error")));
    cards.push(cardPart("by-model", broken, [e], function () {
      return card({ key: "by-model", title: "By model", meta: "share of cost", cls: "span-6", body: breakdownCard("By model", "", b.by_model, e, "model", "model") });
    }));
    var launch = S.ui.folderMode === "launch";
    cards.push(cardPart("by-folder", broken, [e], function () {
      return card({
        key: "by-folder", title: launch ? "By launch folder" : "By folder", cls: "span-6",
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
        key: "threads", title: "Main and subagents", meta: "cost split", cls: "span-4",
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
        key: "haiku", title: "Haiku share", meta: "of cost", cls: "span-4",
        body: '<div class="nm-stats">' + statTile("haiku-share", "Haiku share of cost", haikuShare === null ? "--" : fmtPct(haikuShare, 1), {
          big: true, bar: haikuShare, sub: plural(haikuReq, "request"), cls: "is-wide"
        }) + statTile("haiku-main", "Main threads", fmtUsd(b.haiku.main.cost_usd), { sub: plural(b.haiku.main.requests, "request") }) +
          statTile("haiku-sub", "Subagents", fmtUsd(b.haiku.subagent.cost_usd), { sub: plural(b.haiku.subagent.requests, "request") }) + "</div>"
      });
    }));
    var fast = b.fast || { requests: 0, cost_usd: 0 };
    cards.push(cardPart("fast", broken, [e], function () {
      return card({
        key: "fast", title: "Fast mode", meta: "paid from usage credits", cls: "span-4" + (fast.requests > 0 ? " is-warn" : ""),
        body: '<div class="nm-stats">' + statTile("fast-mode", "Fast-mode requests", fmtInt(fast.requests), { big: true, sub: fmtUsd(fast.cost_usd) + " API-equivalent", cls: "is-wide" }) + "</div>" +
          '<div class="nm-callout' + (fast.requests > 0 ? " is-warn" : "") + '" role="note"' + (skel ? ' data-pending=""' : "") + "><b>Heads up.</b> " + FAST_NOTE + "</div>"
      });
    }));
    cards.push(cardPart("sessions", whole, [e], function () {
      return card({ key: "sessions", title: "Top sessions this week", meta: "by API-equivalent cost", cls: "span-8", body: sessionsTable(base.top_sessions_week || []) });
    }));
    var spend = (base.spend && !base.skeleton) ? base.spend : (u && u.spend);
    cards.push(cardPart("credits", s ? "ready" : "pending", [], function () {
      return card({
        key: "credits", title: "Usage credits", meta: "real money", cls: "span-4 is-real",
        body: spendCardBody(spend, !!(u && u.skeleton)) + '<p class="nm-note nm-disc">Only this card is real money. Every other figure on this page is ' + BASIS + ".</p>"
      });
    }));
    if (s && s.unpriced_models && s.unpriced_models.length) {
      cards.push(cardPart("unpriced", "ready", [], function () {
        return card({ key: "unpriced", title: "Not priced yet", meta: "excluded from cost", cls: "wide", body: '<ul class="nm-supp">' + s.unpriced_models.map(function (id) { return "<li>" + esc(id) + "</li>"; }).join("") + "</ul>" });
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
        key: "cache", title: "Cache", meta: "prompt tokens from cache", cls: "span-4",
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
        key: "thinking", title: "Thinking", meta: "share of output", cls: "span-4",
        body: '<div class="nm-stats">' +
          statTile("thinking-share", "Thinking share", fmtRate(th.share), { big: true, bar: pct100(th.share), cls: "is-wide" }) +
          statTile("thinking-tokens", "Thinking tokens", fmtCount(th.thinking_tokens), { sub: fmtInt(th.thinking_tokens) }) +
          statTile("output-tokens", "Output tokens", fmtCount(th.output_tokens), { sub: fmtInt(th.output_tokens) }) + "</div>"
      });
    });
    add("compactions", function () {
      return card({
        key: "compactions", title: "Compactions", meta: "conversation summaries", cls: "span-4",
        body: '<div class="nm-stats">' +
          statTile("compactions", "Compactions", fmtInt(cp.count), { big: true, sub: fmtInt(cp.auto) + " auto · " + fmtInt(cp.manual) + " manual", cls: "is-wide" }) +
          statTile("compactions-day", "Per active day", num(cp.per_active_day) === null ? "--" : FMT.number(cp.per_active_day.toFixed(2), NO_GROUP)) +
          statTile("active-days", "Active days", fmtInt(d.active_days)) +
          // Claude Code's own count when it compacted, not the API's per-request count under Context size: the two can differ.
          statTile("compact-pre", "Size at compaction", cp.avg_pre_tokens === null ? "--" : fmtCount(cp.avg_pre_tokens), { sub: "tokens, average, Claude Code's own count" }) + "</div>"
      });
    });
    var cmax = num(cx.max) || 0;
    var ctxBar = function (v) { return cmax > 0 && num(v) !== null ? v / cmax * 100 : 0; };
    add("context", function () {
      return card({
        key: "context", title: "Context size", meta: "prompt tokens per request, API count", cls: "span-6",
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
        key: "tools", title: "Tool health", meta: "calls and errors", cls: "span-6",
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
        key: "tool-mix", title: "Tool mix: built-in", meta: "calls by tool", cls: "span-7",
        body: builtin.length ? statBlock("tool-mix", "Calls by tool", chartHost(function (width) {
          return CHARTS.hbars(builtin, { width: width, valueWidth: 70, label: "Built-in tool calls" });
        }, [e.digest, "builtin"], "nm-hbars")) : '<p class="nm-empty">No tool calls in this window.</p>'
      });
    });
    add("mcp-mix", function () {
      return card({
        key: "mcp-mix", title: "Tool mix: MCP", meta: "calls by server", cls: "span-5",
        body: mcp.length ? chartHost(function (width) {
          return CHARTS.hbars(mcp, { width: width, valueWidth: 70, label: "MCP server calls" });
        }, [e.digest, "mcp"], "nm-hbars") + '<p class="nm-note">MCP tools are grouped by the server that provides them.</p>' : '<p class="nm-empty">No MCP servers were called.</p>'
      });
    });
    add("productivity", function () {
      return card({
        key: "productivity", title: "Productivity", meta: "what the tokens produced", cls: "span-8",
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
        key: "effort", title: "Effort mix", meta: "cost by effort level", cls: "span-4",
        body: effort.length ? statBlock("effort-mix", "Cost by effort", chartHost(function (width) {
          return CHARTS.hbars(effort, { width: width, valueWidth: 80, label: "Cost by effort level" });
        }, [e.digest, "effort"], "nm-hbars")) : '<p class="nm-empty">No requests in this window.</p>'
      });
    });
    var tokenRows = [
      ["Fresh input", c.fresh_input_tokens], ["Output", th.output_tokens], ["Cache reads", c.read_tokens],
      ["Writes, 5 min", c.write_5m_tokens], ["Writes, 1 hour", c.write_1h_tokens],
      ["Thinking", th.thinking_tokens],
      ["Total", (c.fresh_input_tokens || 0) + (th.output_tokens || 0) + (c.read_tokens || 0) + (c.write_tokens || 0), "total"]
    ];
    add("tokens", function () {
      return card({
        key: "tokens", title: "Exact token counts", meta: "this window", cls: "span-4",
        body: '<div class="nm-table-wrap"><table class="nm-table nm-tokens"><caption class="nm-sr">Exact token counts for the chosen window</caption><tbody>' + tokenRows.map(function (row) {
          return '<tr class="' + (row[2] === "total" ? "is-today" : "") + '"><th scope="row" class="lead">' + esc(row[0]) + '</th><td class="r v">' + esc(fmtInt(row[1])) + "</td></tr>";
        }).join("") + "</tbody></table></div>"
      });
    });
    add("api-web", function () {
      return card({
        key: "api-web", title: "API errors and web", meta: "outside the tools", cls: "span-4",
        body: '<div class="nm-stats">' +
          statTile("api-errors", "API errors", fmtInt(d.api_errors), { big: true }) +
          statTile("web-searches", "Web searches", fmtInt(w.searches), { big: true }) +
          statTile("web-fetches", "Web fetches", fmtInt(w.fetches), { big: true }) + "</div>"
      });
    });
    return cards;
  }

  /* A line in its language's capitals. */
  function upperLine(line) {
    return FMT.upper(line.text, line.lang);
  }

  /* The pipeline card: how the monitor's own data is doing. The plan is named by its plan name ("Max 5x"), never by the
   * internal tier id the usage API reports. */
  function pipelineCard(s) {
    var ingest = s && s.ingest ? s.ingest : null;
    var ingestText = ingest
      ? (ingest.running ? "RUNNING " + fmtInt(ingest.files_done) + " / " + fmtInt(ingest.files_total) : "IDLE, " + fmtInt(ingest.files_total) + " FILES")
      : "--";
    var kv = [
      ["LINK", s ? upperLine(entryOf(LINK_WORDS, s.status, "loading")()) : "--"],
      ["FETCHED", s && s.usage_fetched_at ? stampText(parseTime(s.usage_fetched_at)) : "--"],
      ["UPDATED", s && s.updated_at ? stampText(parseTime(s.updated_at)) : "--"],
      ["INGEST", ingestText],
      ["LAST INGEST", ingest && ingest.last_duration_s !== null && ingest.last_duration_s !== undefined ? FMT.number(num(ingest.last_duration_s).toFixed(2), NO_GROUP) + " S" : "--"],
      ["LAST ERROR", ingest && ingest.last_error ? ingest.last_error : "NONE"],
      ["PLAN", s && s.plan && s.plan.name ? FMT.upper(String(s.plan.name)) : "--"],
      ["PRICE", effectivePlanPrice() !== null ? fmtPlanPrice(effectivePlanPrice()) + " / MO" : "UNKNOWN"]
    ];
    return card({ key: "pipeline", title: "Pipeline", meta: "data health", cls: "span-6", body: kvGrid(kv) });
  }

  function notPricedCard(s) {
    var unpriced = s && s.unpriced_models && s.unpriced_models.length
      ? s.unpriced_models.map(function (id) { return "<li>" + esc(id) + "</li>"; }).join("") : "<li>NONE</li>";
    return card({ key: "not-priced", title: "Not priced", meta: "models without a price row", cls: "span-6", body: '<ul class="nm-supp">' + unpriced + "</ul>" });
  }

  function efficiencyPage() {
    if (limitsOnly()) {
      return noActivityPage("efficiency");
    }
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

  /* placeholder: ODOMETER_PLACEHOLDER, for a total that has not arrived. A separator is the locale's group sign. */
  function odometerHTML(total, placeholder) {
    var text = placeholder ? ODOMETER_PLACEHOLDER : FMT.number(Math.round(Math.max(0, num(total) || 0)));
    var strip = "0123456789".split("").map(function (n) { return "<i>" + n + "</i>"; }).join("");
    return '<div class="nm-odo" role="img" aria-label="' + esc(fmtInt(total) + " tokens, all time") + '">' + text.split("").map(function (ch) {
      return ch === FMT.group ? '<span class="nm-od-sep" aria-hidden="true">' + esc(ch) + "</span>"
        : '<span class="nm-od-d" aria-hidden="true" data-d="' + ch + '"><span class="strip" style="--d:' + ch + '">' + strip + "</span></span>";
    }).join("") + "</div>";
  }

  function fmtCompare(v) {
    var n = num(v);
    if (n === null) {
      return "--";
    }
    return n >= 100 ? FMT.number(Math.round(n)) : FMT.number(n >= 10 ? n.toFixed(0) : n.toFixed(1), NO_GROUP);
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
        out.push({ n: FMT.number(v >= 1000 ? (v / 1000).toFixed(1) : v >= 100 ? String(Math.round(v)) : v.toFixed(1), NO_GROUP), u: (v >= 1000 ? "km" : "metres") + " of printed pages", t: "Claude’s output printed at 500 words a page and stacked, 0.1 mm a sheet, would stand this tall." });
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

  /* The hour of a column (0 to 23) and the next, local time: "4 PM to 5 PM". */
  function hourRange(c) {
    return hourText(new Date(2026, 0, 1, c).getTime()) + " to " + hourText(new Date(2026, 0, 1, c + 1).getTime());
  }

  function historyPage() {
    if (limitsOnly()) {
      S.odoTotal = null;                               // no odometer to roll and no comparison to turn on this page
      S.cmpList = [];
      return noActivityPage("history");
    }
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
        key: "odometer", title: "All-time odometer", meta: "tokens, everything Claude has read and written", cls: "wide", id: "odometer",
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
        key: "calendar", title: "Every day", cls: "wide", metaHtml: seg("cal-metric", "Colour each day by", [["cost", "COST"], ["tokens", "TOKENS"]], metric),
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
        key: "matrix", title: "When you work", cls: "span-8", metaHtml: seg("matrix-metric", "Colour each hour by", [["cost", "COST"], ["requests", "REQUESTS"]], mm),
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
        key: "streaks", title: "Streaks and records", meta: "all time", cls: "span-4",
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
        key: "export", title: "Export", meta: "daily totals", cls: "wide",
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

  /* A row's words: plain text, escaped, or markup this file built (a catalog text through tHtml, #97's update row). */
  function htmlOf(value) {
    return isMarkup(value) ? value.html : esc(value);
  }

  function toggleRow(key, title, desc, attrs, descId) {
    return '<label class="nm-row-set"><span><span class="t">' + htmlOf(title) + '</span><span class="d"' + (descId ? ' id="' + descId + '"' : "") + ">" + htmlOf(desc) +
      '</span></span><span class="nm-toggle"><input type="checkbox" data-key="' + esc(key) + '" data-kind="bool"' + (attrs || "") + '><span class="box" aria-hidden="true"></span><span class="lbl" data-lbl-for="' +
      esc(key) + '">OFF</span></span></label>';
  }

  function selectRow(key, title, desc, options) {
    return '<label class="nm-row-set"><span><span class="t">' + esc(title) + '</span><span class="d">' + esc(desc) +
      '</span></span><select class="nm-select" data-key="' + esc(key) + '">' + options.map(function (o) {
        return '<option value="' + esc(o[0]) + '">' + esc(o[1]) + "</option>";
      }).join("") + "</select></label>";
  }

  /* The Language setting (#87). Shown once a second language is offered: with English alone there is nothing to choose
   * (a dev instance that shows a language not offered yet lists that one too). "System default" names what it resolves
   * to; each language is named in itself, with its own lang. The list is applied with its button, not when it changes:
   * the dashboard reloads in the new language, which would get in the way of someone moving through the list. */
  function languageRow() {
    var languages = (LOCALE.offered || []).filter(function (language) {
      return Object.prototype.hasOwnProperty.call(LANGUAGE_NAMES, language);
    });
    if (languages.indexOf(LOCALE.language) < 0 && Object.prototype.hasOwnProperty.call(LANGUAGE_NAMES, LOCALE.language)) {
      languages.push(LOCALE.language);
    }
    if (languages.length < 2) {
      return "";
    }
    var system = LANGUAGE_NAMES[LOCALE.system] || LANGUAGE_NAMES.en;
    var chosen = typeof LOCALE.setting === "string" ? LOCALE.setting : "system";
    function option(value, label, lang) {
      return '<option value="' + esc(value) + '" lang="' + esc(lang) + '"' + (value === chosen ? " selected" : "") + ">" +
        esc(label) + "</option>";
    }
    return '<div class="nm-row-set"><span><span class="t" id="language-t">' + tHtml("settings.language.label") +
      '</span><span class="d" id="language-d">' + tHtml("settings.language.help") + "</span></span>" +
      '<span class="nm-quiet"><select class="nm-select" id="set-language" aria-labelledby="language-t" aria-describedby="language-d"' +
      ' data-synced="' + esc(chosen) + '">' +
      option("system", t("settings.language.system", { language: system }), tLang("settings.language.system")) +
      languages.map(function (language) {
        return option(language, LANGUAGE_NAMES[language], language);
      }).join("") +
      '</select><button class="nm-btn" type="button" data-action="language-apply">' + tHtml("settings.language.apply") +
      "</button></span></div>";
  }

  function errLine(key) {
    return '<p class="nm-err" id="err-' + key + '" role="alert" hidden></p>';
  }

  function alertsCard() {
    var dependent = ' data-needs-alerts="1"';
    return '<section class="nm-card wide" aria-label="Alerts" data-card="alerts"><div class="nm-card-in">' +
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
      main.innerHTML = pageHead("settings") +
        '<div class="nm-pgrid">' +
        '<section class="nm-card wide" aria-label="Theme" data-card="theme"><div class="nm-card-in"><div class="nm-card-t"><h2 class="nm-ttl">Theme</h2><span class="nm-meta-t" id="set-theme-meta"></span></div>' +
        '<div class="nm-themes" id="set-themes"></div><div id="set-mode" class="nm-actions" hidden></div><div class="nm-variants" id="set-accents"></div></div></section>' +
        '<section class="nm-card" aria-label="Widgets and display" data-card="widgets"><div class="nm-card-in"><div class="nm-card-t"><h2 class="nm-ttl">Widgets &amp; display</h2><span class="nm-meta-t">floating and taskbar</span></div><div class="nm-set">' +
        languageRow() +
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
        '<section class="nm-card" aria-label="Data and plan" data-card="data-plan"><div class="nm-card-in"><div class="nm-card-t"><h2 class="nm-ttl">Data &amp; plan</h2><span class="nm-meta-t">how often, and what you pay</span></div><div class="nm-set">' +
        selectRow("poll_interval_ms", "Fetch usage every", "How often the usage API is asked. The minimum is one minute.", POLL_CHOICES) +
        '<label class="nm-row-set"><span><span class="t">Billing day</span><span class="d">The day of the month your billing cycle starts. Blank uses the calendar month.</span></span>' +
        '<select class="nm-select" data-key="billing_day" aria-describedby="err-billing_day"><option value="">CALENDAR MONTH</option>' +
        Array.from({ length: 31 }, function (_, i) { return '<option value="' + (i + 1) + '">DAY ' + (i + 1) + "</option>"; }).join("") + "</select></label>" + errLine("billing_day") +
        '<label class="nm-row-set"><span><span class="t">Plan price override</span><span class="d" id="plan-hint">' + esc(planHintText()) + "</span></span>" +
        '<input class="nm-input" type="number" data-key="plan_price_override" min="0.01" max="10000" step="0.01" placeholder="AUTO" inputmode="decimal" aria-describedby="plan-hint err-plan_price_override"></label>' + errLine("plan_price_override") +
        '</div><div class="nm-actions"><button class="nm-btn" type="button" data-action="refresh">Refresh now</button></div></div></section>' +
        alertsCard() +
        guardCard() +
        '<section class="nm-card wide" aria-label="System" data-card="system"><div class="nm-card-in"><div class="nm-card-t"><h2 class="nm-ttl">System</h2><span class="nm-meta-t">start-up and updates</span></div><div class="nm-set">' +
        toggleRow("launch_on_startup", "Start with Windows", startupHintText(), "", "startup-hint") +
        toggleRow("check_updates", markup(tHtml("settings.update.row.label")), markup(tHtml("settings.update.row.help"))) +
        '</div><div class="nm-actions" id="set-update-actions"><button class="nm-btn" type="button" data-action="check-update">' +
        tHtml("settings.update.button.check") + "</button>" +
        '<button class="nm-btn" type="button" data-action="install-update" hidden>' + tHtml("settings.update.button.install") +
        '</button><button class="nm-btn" type="button" data-action="skip-update" hidden>' + tHtml("settings.update.button.skip") + "</button></div>" +
        '<div class="nm-uprog" id="set-update-progress" hidden></div>' +
        '<p class="nm-status" id="set-update-status" role="status"></p></div></section>' +
        "</div>" +
        '<p class="nm-status" id="set-status" role="status" aria-live="polite"></p>';
      if (!S.setup.busy) {
        S.setup.result = null;                            // what the last click said belongs to that visit
      }
      S.guardAt = 0;
      syncSettings(main);
      pollGuard();                                        // what was last known shows at once, and is asked for again
      askUpdateStatus();                                  // the same for a running update, while a release is on offer
      return;
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
    syncHints(main);
    syncThemePicker(main);
    syncAlerts(main);
    syncGuard(main);
    syncUpdate(main);
    syncLanguage(main);
  }

  /* The Language list follows the stored choice as the monitor reads it (#87; a language that is not offered yet reads
   * as System default), but only when that changes: a pick not applied yet is never undone by the next state. */
  function syncLanguage(main) {
    var select = $("#set-language", main);
    var locale = S.data && S.data.locale;
    if (!select || !locale) {
      return;
    }
    var value = typeof locale.setting === "string" ? locale.setting : "system";
    if (select.getAttribute("data-synced") === value) {
      return;
    }
    select.setAttribute("data-synced", value);
    if (Array.prototype.some.call(select.options, function (o) { return o.value === value; })) {
      select.value = value;
    }
  }

  /* The hint beside the override names the detected price, never the override: that is what a blank field goes back to. */
  function planHintText() {
    var plan = planFromState();
    var detected = plan.price !== null ? plan.name + ", " + fmtPlanPrice(plan.price) + " a month" : "no price known";
    return "USD a month. Blank uses the detected price (" + detected + ").";
  }

  function startupHintText() {
    return S.data && S.data.dev ? "Dev instance: this is not saved to Windows." : "Run the monitor when you sign in.";
  }

  /* Words that follow the state. The page can be drawn before the first state, and a first sign-in or a new plan changes the plan
   * under a page that is open. Each is written only when it changes. */
  function syncHints(main) {
    [["#plan-hint", planHintText()], ["#startup-hint", startupHintText()]].forEach(function (pair) {
      var hint = $(pair[0], main);
      if (hint && hint.textContent !== pair[1]) {
        hint.textContent = pair[1];
      }
    });
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
        return '<span class="nm-tchip" role="listitem"><b>' + esc(FMT.percent(String(v))) + '</b><button type="button" data-action="alert-remove" data-level="' + v + '" data-fid="alert-rm-' + v +
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

  // ── The usage guard ─────────────────────────────────────────────────────────────

  /* A number setting that may be left blank: a blank field is "off" (guard caps) or the default (the stagger). */
  function capRow(key, title, desc, placeholder, min, max) {
    return '<label class="nm-row-set"><span><span class="t">' + esc(title) + '</span><span class="d" id="desc-' + key + '">' + esc(desc) +
      '</span></span><input class="nm-input" type="number" data-key="' + key + '" min="' + min + '" max="' + max + '" step="1" placeholder="' + esc(placeholder) +
      '" inputmode="numeric" aria-describedby="desc-' + key + " err-" + key + '"></label>' + errLine(key);
  }

  /* The Settings card. Its two blocks are filled by syncGuard: the live status and the one-click setup. */
  function guardCard() {
    return '<section class="nm-card wide" aria-label="Usage guard" data-card="guard"><div class="nm-card-in">' +
      '<div class="nm-card-t"><h2 class="nm-ttl">Usage guard</h2><span class="nm-meta-t">tells working sessions to wrap up</span></div><div class="nm-set" id="guard-set">' +
      toggleRow("guard_enabled", "Arm the guard", GUARD_EXPLAIN) +
      capRow("guard_session_cap", "Session cap", "Percent of the 5-hour session limit, 1 to 100. Blank leaves it unwatched.", "OFF", 1, 100) +
      capRow("guard_weekly_cap", "Weekly cap", "Percent of the weekly limit, 1 to 100. Blank leaves it unwatched.", "OFF", 1, 100) +
      capRow("guard_scoped_cap", "Model weekly cap", "Percent of a model’s own weekly limit, such as Fable’s, 1 to 100. Blank leaves it unwatched.", "OFF", 1, 100) +
      capRow("guard_resume_stagger_s", "Stagger", "Seconds between waking the sessions that wait, so they do not all start at once. 0 to 600.", "120", 0, 600) +
      '<div class="nm-row-set is-stack"><span><span class="t">Status</span></span><div id="guard-live"></div></div>' +
      '<div class="nm-row-set is-stack"><span><span class="t">One-click setup</span><span class="d">Adds the hooks and the MCP tool to Claude Code for you. Your other settings keep their values, a backup is kept, and the file is saved in a standard layout.</span></span>' +
      '<div id="guard-setup"></div></div></div></div></section>';
  }

  /* What /api/guard last said, in a shape the card can rely on whatever came back. */
  function guardView() {
    var answer = S.guard && typeof S.guard === "object" ? S.guard : {};
    var guard = answer.guard && typeof answer.guard === "object" ? answer.guard : {};
    return {
      known: !!S.guard,
      state: guard.state === "off" || guard.state === "armed" || guard.state === "holding" ? guard.state : "",
      over: Array.isArray(guard.over) ? guard.over : [],
      resumeAt: parseTime(guard.resume_at),
      policy: guard.policy && typeof guard.policy === "object" && !Array.isArray(guard.policy) ? guard.policy : {},
      told: Array.isArray(answer.told) ? answer.told : [],
      waiting: Array.isArray(answer.waiting) ? answer.waiting : [],
      watching: Array.isArray(answer.watching) ? answer.watching : [],
      setup: answer.setup && typeof answer.setup === "object" && !Array.isArray(answer.setup) ? answer.setup : null
    };
  }

  /* A session id as people say it: its first eight characters. */
  function shortId(id) {
    return id ? String(id).slice(0, 8) : "";
  }

  function folderName(path) {
    var parts = String(path || "").split(/[\\/]+/).filter(Boolean);
    return parts.length ? parts[parts.length - 1] : "";
  }

  function pctWords(value) {
    var n = num(value);
    return n === null ? "--" : n === Math.round(n) ? fmtPct(n) : fmtPct(n, 1);
  }

  /* "X, Y and Z" (English keeps its join, no Oxford comma; other languages join as Intl does: #91). */
  function listWords(items) {
    return FMT.list(items);
  }

  /* The limits the caps watch, as words: "session at 95%". */
  function watchedWords(policy) {
    var out = [];
    [["session_cap", "the session limit"], ["weekly_cap", "the weekly limit"], ["scoped_cap", "each model’s weekly limit"]].forEach(function (pair) {
      if (num(policy[pair[0]]) !== null) {
        out.push(pair[1] + " at " + pctWords(policy[pair[0]]));
      }
    });
    return out;
  }

  /* The status block as it is before the monitor has answered: the shape of the common case, so nothing moves when it does. */
  function guardLivePlaceholder() {
    return '<div class="nm-verdict"><span class="nm-chip" data-pending="" aria-hidden="true" data-tone="neutral">ARMED</span></div>' +
      '<p class="nm-story" data-pending="" aria-hidden="true">Watching the session limit at 95% and the weekly limit at 90%. Every watched limit is under its cap.</p>' +
      '<div class="nm-actions"><button class="nm-btn" type="button" data-fid="guard-resume" disabled>Resume now</button></div>';
  }

  /* Who of a told session heard the notice: its main conversation, and how many of its agents (#41). An entry without
     these fields counts as its conversation, as the card counted before. */
  function toldWho(t) {
    var n = t && typeof t.agents === "number" && isFinite(t.agents) && t.agents > 0 ? Math.round(t.agents) : 0;
    return { main: !t || t.main !== false, agents: n };
  }

  function toldCount(mains, agents, sessions) {
    var parts = [];
    if (mains) { parts.push(plural(mains, "conversation")); }
    if (agents) { parts.push(plural(agents, "agent")); }
    return FMT.upper(parts.length ? parts.join(", ") : plural(sessions, "session"), "en");
  }

  function whoWords(w) {
    if (w.main && w.agents) { return "CONVERSATION + " + FMT.upper(plural(w.agents, "agent"), "en"); }
    if (w.main) { return "CONVERSATION"; }
    return w.agents ? FMT.upper(plural(w.agents, "agent"), "en") + " ONLY" : "SESSION";
  }

  /* The background work a watched conversation waits for, as words: "1 WORKFLOW, 2 AGENTS" (#41). */
  function workWords(work) {
    if (!work || typeof work !== "object" || Array.isArray(work)) {
      return "";
    }
    return Object.keys(work).filter(function (word) {
      return typeof work[word] === "number" && isFinite(work[word]) && work[word] >= 1;
    }).map(function (word) { return FMT.upper(plural(Math.round(work[word]), word), "en"); }).join(", ");
  }

  /* Off, Armed or Holding. While holding: which limits are over their caps, when the hold ends, the sessions told of it and the
   * sessions that wait. Armed or holding, the conversations the Stop hook watches (#41). "Resume now" is there always and
   * works only while something waits. */
  function guardLiveHtml(v, nowMs) {
    if (!v.known) {
      return S.guardLost ? '<p class="nm-error" role="status">The dashboard could not get the guard’s status from the monitor.</p>' : guardLivePlaceholder();
    }
    if (!v.state) {
      return guardLivePlaceholder();
    }
    var chip;
    var story;
    if (v.state === "off") {
      chip = '<span class="nm-chip" data-tone="neutral">OFF</span>';
      story = "The guard is off. Nothing is watched, and no session is told anything.";
    } else if (v.state === "armed") {
      var watched = watchedWords(v.policy);
      chip = '<span class="nm-chip is-ok" data-tone="ok">ARMED</span>';
      story = watched.length ? "Watching " + listWords(watched) + ". Every watched limit is under its cap." : "No cap is set, so no limit is watched. Set at least one cap above.";
    } else {
      chip = '<span class="nm-chip solid is-crit" data-tone="alert">HOLDING</span>';
      story = "A cap was reached. Working sessions are told once to wrap up, and sessions that wait wake when every watched limit is under its cap again.";
    }
    var hooks = v.setup && v.setup.hooks ? v.setup.hooks.state : "";
    if (v.state !== "off" && (hooks === "missing" || hooks === "partial")) {
      story += " The hooks are not all installed, so sessions may not be told: press Install below.";  // the guard speaks through them
    }
    var rows = [];
    if (v.state === "holding") {
      v.over.forEach(function (item) {
        rows.push([FMT.upper(String(item.label || item.name || "limit"), "en"), pctWords(item.percent) + " OF A " + pctWords(item.cap) + " CAP"]);
      });
      var left = v.resumeAt === null ? null : v.resumeAt - nowMs;
      rows.push(["RESUMES", left === null ? "NO RESET TIME KNOWN" : clockText(v.resumeAt) + (left > 60000 ? " · IN " + fmtRemain(left) : " · NOW")]);
      var heard = v.told.map(toldWho);
      var mains = heard.filter(function (w) { return w.main; }).length;
      var agents = heard.reduce(function (sum, w) { return sum + w.agents; }, 0);
      var untold = heard.filter(function (w) { return !w.main && w.agents; }).length;
      rows.push(["TOLD THIS EPISODE", v.told.length ? toldCount(mains, agents, v.told.length) : "NONE YET"]);
      v.told.slice(0, GUARD_LISTED).forEach(function (t, i) {
        rows.push([shortId(t.session_id) || "unknown session", clockText(parseTime(t.told_at)) + " · " + whoWords(heard[i])]);
      });
      if (v.told.length > GUARD_LISTED) {
        rows.push(["AND " + (v.told.length - GUARD_LISTED) + " MORE", ""]);
      }
      if (untold) {    // it waits for its agents, so it hears at its next prompt or tool call (#41)
        rows.push(["NOT TOLD", untold === 1 ? "1 CONVERSATION: ITS AGENTS WERE" : FMT.upper(plural(untold, "conversation"), "en") + ": THEIR AGENTS WERE"]);
      }
    }
    if (v.state !== "off" && v.watching.length) {   // they wait for their background work: woken when the guard holds (#41)
      rows.push(["WATCHING", FMT.upper(plural(v.watching.length, "conversation"), "en")]);
      v.watching.slice(0, GUARD_LISTED).forEach(function (w) {
        var work = workWords(w && w.work);
        rows.push([shortId(w && w.session_id) || "unknown session", "SINCE " + clockText(parseTime(w && w.since)) + (work ? " · " + work : "")]);
      });
      if (v.watching.length > GUARD_LISTED) {
        rows.push(["AND " + (v.watching.length - GUARD_LISTED) + " MORE", ""]);
      }
    }
    if (v.state === "holding" || v.waiting.length) {
      rows.push(["WAITING", v.waiting.length ? FMT.upper(plural(v.waiting.length, "session"), "en") : "NONE"]);
      v.waiting.slice(0, GUARD_LISTED).forEach(function (w) {
        var folder = folderName(w.cwd) || "unknown folder";
        rows.push([folder + (w.session_id ? " · " + shortId(w.session_id) : ""), "SINCE " + clockText(parseTime(w.since)), false, w.cwd || ""]);
      });
      if (v.waiting.length > GUARD_LISTED) {
        rows.push(["AND " + (v.waiting.length - GUARD_LISTED) + " MORE", ""]);
      }
    }
    // While it is sent the button only says it is busy (aria-disabled keeps the keyboard focus on it); it is disabled when nothing waits.
    var state = S.resumeBusy ? ' aria-disabled="true"' : v.waiting.length > 0 ? "" : " disabled";
    return '<div class="nm-verdict">' + chip + '</div><p class="nm-story" role="status">' + esc(story) + "</p>" +
      (rows.length ? kvGrid(rows, "is-one") : "") +
      '<div class="nm-actions"><button class="nm-btn" type="button" data-action="guard-resume" data-fid="guard-resume"' + state + ">" +
      (S.resumeBusy ? "Resuming…" : "Resume now") + "</button></div>";
  }

  function hooksWords(hooks) {
    if (!hooks) {
      return "UNKNOWN";
    }
    return hooks.state === "partial" ? "PARTLY INSTALLED (" + hooks.installed + " OF " + hooks.total + ")" : HOOK_WORDS[hooks.state] || "UNKNOWN";
  }

  function claudeWords(claude) {
    if (!claude || !claude.found) {
      return "NOT FOUND";
    }
    if (!claude.version) {
      return "VERSION UNKNOWN";
    }
    return claude.version + (claude.supported === false ? " · NEEDS " + claude.min_version + " OR NEWER" : "");
  }

  /* What an Install or a Remove did, in the monitor's plain words: a summary of what changed or was already so and where the backup
   * is, the warnings (and the command to run by hand when the monitor could not), and what went wrong, each in a box of its own and
   * only when it has something to say. Nothing here is a token or a secret. */
  function guardResultHtml(r) {
    if (!r || typeof r !== "object") {
      return "";
    }
    var changed = Array.isArray(r.changed) ? r.changed : [];
    var said = changed.concat(Array.isArray(r.unchanged) ? r.unchanged : []).join(" ");
    var head = r.ok ? (changed.length ? (r.action === "remove" ? "Removed." : "Installed.") : "Nothing to change.") : changed.length ? "Partly done." : "";
    var summary = (head ? "<b>" + esc(head) + "</b> " : "") + esc(said);
    if (r.backup) {
      summary += (summary ? "<br>" : "") + "Backup: " + tPath(r.backup).html;
    }
    var notes = (Array.isArray(r.warnings) ? r.warnings : []).map(esc);
    if (r.manual) {
      notes.push("To do it by hand, run: " + tPath(r.manual).html);
    }
    return (summary ? '<div class="nm-callout" role="status">' + summary + "</div>" : "") +
      (notes.length ? '<div class="nm-callout is-warn" role="note">' + notes.join("<br>") + "</div>" : "") +
      (Array.isArray(r.errors) ? r.errors : []).map(function (error) {
        return '<p class="nm-error" role="alert">' + esc(error) + "</p>";
      }).join("");
  }

  /* The one-click setup block: where setup stands, Install and Remove, what the last click did, and what it costs. When setup
   * cannot run here, why, and where to do it by hand. */
  function guardSetupHtml(v) {
    var s = v.setup;
    var busy = S.setup.busy;
    var rows;
    var notes = "";
    if (!s) {
      rows = kvGrid([["HOOKS", "NOT INSTALLED", true], ["MCP TOOL", "NOT REGISTERED", true], ["CLAUDE CODE", "2.1.160", true]], "is-one");
    } else {
      var where = s.claude && s.claude.path ? String(s.claude.path) : "";
      rows = kvGrid([["HOOKS", hooksWords(s.hooks)], ["MCP TOOL", MCP_WORDS[(s.mcp || {}).state] || "UNKNOWN"], ["CLAUDE CODE", claudeWords(s.claude), false, where]], "is-one");
      if (!s.available) {
        notes += '<div class="nm-callout is-warn" role="note"><b>Setup is not available here.</b> ' + esc(s.reason || "") +
          ' Set it up by hand instead: <a href="' + esc(LINKS.hooks) + '" data-external="1">docs/AGENT_HOOK.md</a>.</div>';
      } else if (s.claude && s.claude.supported === false) {
        // Which claude: an old npm copy can come first on the PATH while another one is up to date.
        notes += '<div class="nm-callout is-warn" role="note">Claude Code ' + esc(s.claude.version) +
          (where ? " (" + tPath(where).html + ")" : "") + " is older than " + esc(s.claude.min_version) +
          ", which the hooks need. Update it with " + tPath((s.claude.command || "claude") + " update").html + " first.</div>";
      }
      (Array.isArray(s.errors) ? s.errors : []).forEach(function (error) {
        notes += '<p class="nm-error" role="alert">' + esc(error) + "</p>";
      });
    }
    // A button that cannot work here is disabled; one waiting on a click is only aria-disabled, so it keeps the keyboard focus.
    // Remove needs no Claude Code: it edits the settings file, and leaves the MCP tool to the manual line when claude is missing.
    var canRemove = !!s && (s.can_remove === undefined ? !!s.available : !!s.can_remove);
    var installState = !s || !s.available ? " disabled" : busy ? ' aria-disabled="true"' : "";
    var removeState = !canRemove ? " disabled" : busy ? ' aria-disabled="true"' : "";
    return rows + notes + '<div class="nm-actions">' +
      '<button class="nm-btn solid" type="button" data-action="guard-install" data-fid="guard-install"' + installState + ">" + (busy === "install" ? "Installing…" : "Install") + "</button>" +
      '<button class="nm-btn" type="button" data-action="guard-remove" data-fid="guard-remove"' + removeState + ">" + (busy === "remove" ? "Removing…" : "Remove") + "</button></div>" +
      guardResultHtml(S.setup.result) + '<p class="nm-lede">' + esc(GUARD_COST) + "</p>";
  }

  /* Puts the two blocks in step with what the monitor last said. A block whose markup did not change is left alone, so a poll never
   * drops the focus of whoever is tabbing through its buttons. */
  function syncGuard(main) {
    main = main || $("#main");
    var v = guardView();
    var now = Date.now();
    [["#guard-live", guardLiveHtml(v, now)], ["#guard-setup", guardSetupHtml(v)]].forEach(function (pair) {
      var box = $(pair[0], main);
      if (box && box.getAttribute("data-sig") !== pair[1]) {
        box.setAttribute("data-sig", pair[1]);
        replaceHtml(box, pair[1]);
      }
    });
  }

  /* Asks /api/guard (the state, who was told, who waits, where setup stands). One ask at a time; the Settings page asks again every
   * GUARD_POLL_MS while it is on screen, and at once after a change. */
  function pollGuard() {
    if (S.guardFlight) {
      return;
    }
    S.guardFlight = true;
    S.guardAt = Date.now();
    api("GET", "/api/guard").then(function (res) {
      S.guardFlight = false;
      S.guardLost = !(res.ok && res.data);
      if (!S.guardLost) {
        S.guard = res.data;
      }
      syncGuard();
    }, function () {
      S.guardFlight = false;
      S.guardLost = true;
      syncGuard();
    });
  }

  /* Once a second, from tick(): ask again when it is time, and let the countdown move. */
  function guardTick() {
    if (S.page !== "settings" || S.formKey !== "settings" || document.visibilityState === "hidden") {
      return;
    }
    if (!S.guardFlight && Date.now() - S.guardAt >= GUARD_POLL_MS) {
      pollGuard();
    }
    syncGuard();
  }

  /* "Resume now": every session that waits on the guard is woken at once, whatever the limits say. */
  function guardResume() {
    if (S.resumeBusy) {
      return;
    }
    S.resumeBusy = true;
    syncGuard();
    api("POST", "/api/guard/resume").then(function (res) {
      S.resumeBusy = false;
      setStatus(res.ok ? "RESUME REQUESTED" : failedText("RESUME FAILED", res), !res.ok);
      pollGuard();
    }, function () {
      S.resumeBusy = false;
      setStatus("RESUME FAILED: THE DASHBOARD LOST ITS LINK", true);
      syncGuard();
    });
  }

  /* Install or Remove. The monitor answers with what it did, or why it did not; either way the result stays beside the buttons. */
  function guardSetup(action) {
    if (S.setup.busy) {
      return;
    }
    var failed = function (why) {
      return { ok: false, action: action, changed: [], unchanged: [], backup: null, warnings: [], errors: [why], manual: null };
    };
    S.setup = { busy: action, result: null };
    syncGuard();
    api("POST", "/api/guard/setup", { action: action }).then(function (res) {
      S.setup = { busy: "", result: res.ok && res.data && typeof res.data === "object" ? res.data : failed("The monitor could not run setup (" + errorText(res) + ").") };
      pollGuard();
      syncGuard();
    }, function () {
      S.setup = { busy: "", result: failed("The dashboard lost its link to the monitor.") };
      syncGuard();
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

  /* The words on a colour button, in capitals. A theme with one variant per colour shows that variant's name (Nostromo's
   * RAY TUBE for amber, Night City's RELIC). Gates has a dark and a light variant of each accent, so it shows the accent. */
  /* The name the theme gives an accent, or null when it gives none: a theme with modes names no accent (its picker shows the id). */
  function accentName(theme, accent) {
    var perMode = theme && theme.modes && theme.modes.length;
    var named = theme && !perMode ? (theme.variants || []).filter(function (v) {
      return (v.accent || accentOf(v.id)) === accent;
    })[0] : null;
    return named && named.name ? String(named.name) : null;
  }

  function accentLabel(theme, accent) {
    var name = accentName(theme, accent);
    return FMT.upper(String(name === null ? accent : name), "en");
  }

  /* A name that is not the colour's id is shown, so the id is kept as the tooltip. */
  function accentTitled(theme, accent) {
    var name = accentName(theme, accent);
    return name !== null && name.toLowerCase() !== accent.toLowerCase();
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
    meta.textContent = current ? FMT.upper(current.name, "en") + " \u00b7 " + FMT.upper(String(current.variant_name || current.variant), "en") : "";
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
      var label = accentLabel(theme, a);
      return '<button type="button" class="nm-btn' + (a === active ? " solid" : "") + '" data-accent="' + esc(a) + '"' +
        (accentTitled(theme, a) ? ' title="' + esc(a) + '"' : "") + ' aria-pressed="' + (a === active ? "true" : "false") + '">' +
        esc(label) + "</button>";
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
        return '<button type="button" data-mode="' + esc(m) + '" class="' + (currentMode === m ? "on" : "") + '" aria-pressed="' + (currentMode === m ? "true" : "false") + '">' + esc(FMT.upper(m, "en")) + "</button>";
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
      var line = updateLineSaid();
      status.textContent = line.text;
      markLineLang(status, line.lang);                 // syncUpdateProgress writes the same text again, or hides it
    }
    syncUpdateProgress(main);                          // a running update: its block, the line hidden under it, Install busy
  }

  /* The release the state offers, "" for none. */
  function offeredVersion() {
    var u = S.data && S.data.update ? S.data.update : null;
    return u && u.available && u.info && u.info.version ? String(u.info.version) : "";
  }

  /* The line under the update buttons. What a click said stays while nothing is on offer, or while it is about the release that is:
   * "You are up to date." beside an Install button says nothing true. A check keeps `offered`, not `version`, which would also put
   * its words in the banner. */
  function updateLine() {
    return updateLineSaid().text;
  }

  /* The same line with the language it is in: { text, lang }. */
  function updateLineSaid() {
    var offered = offeredVersion();
    var about = S.update.version !== undefined ? S.update.version : S.update.offered;
    if (S.update.text && (!offered || about === offered)) {
      return { text: S.update.text, lang: S.update.lang };
    }
    return offered ? tLine("settings.update.offered", { version: offered }) : { text: "", lang: "" };
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
    var said = S.update.version === found.version && S.update.text ? S.update : { text: "", lang: "" };
    return '<div class="nm-update" role="status" aria-label="' + esc(t("banner.update.label")) + '"><div class="nm-update-t"><b>' +
      tHtml("banner.update.title", { app: tName(APP_NAME), version: found.version }) + "</b>" +
      "<span>" + (found.self ? tHtml("banner.update.installs") : tHtml("banner.update.opens")) + "</span></div>" +
      '<div class="nm-actions"><button class="nm-btn solid" type="button" data-action="update-now" data-fid="update-now">' +
      tHtml("banner.update.button.now") + "</button>" +
      (found.url ? '<button class="nm-btn" type="button" data-action="update-notes" data-fid="update-notes">' + tHtml("banner.update.button.notes") + "</button>" : "") +
      '<button class="nm-btn" type="button" data-action="update-skip" data-fid="update-skip">' + tHtml("banner.update.button.skip") + "</button></div>" +
      '<p class="nm-status" role="status"' + lineLangAttr(said.lang) + ">" + esc(said.text) + "</p></div>";
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

  // ── The sign-in help (#28) ──────────────────────────────────────────────────────

  /* The two states only the user can fix: there is no sign-in (someone who uses Claude on the web or in the app and never ran
   * Claude Code), or the sign-in has expired or was turned down. */
  function needsSignIn(s) {
    return !!s && (s.status === "no_credentials" || s.status === "stale");
  }

  /* A command the user types: it keeps its case in every theme. */
  function codeText(text) {
    return tCode(text).html;
  }

  /* The copy button of one command (a key of COMMANDS). Only the button that was pressed gives the answer. */
  function copyButton(which) {
    var answer = S.copyOf === which ? S.copy : "";
    var label = answer === "copied" ? "Copied" : answer === "select" ? "Press Ctrl+C" : "Copy";
    var title = answer === "copied" ? "Copied to the clipboard" : answer === "select" ? "Press Ctrl+C to copy the selected command" : COPY_TITLES[which];
    return '<button class="nm-btn" type="button" data-action="copy-command" data-command="' + which + '" data-fid="signin-copy-' + which + '" aria-label="' +
      esc(title) + '">' + esc(label) + "</button>";
  }

  /* A command to run, with its copy button. */
  function commandRow(which) {
    return '<div class="nm-cmd" data-command="' + which + '">' + codeText(COMMANDS[which]) + copyButton(which) + "</div>";
  }

  /* The status banner for a missing or expired sign-in: what happened, what to do about it, and a button that looks again. With no
   * sign-in the steps start from nothing (installing Claude Code, which also gives the plan's limits to someone who uses Claude on
   * the web or in the app) and start Claude Code by its folder, since the installer leaves the PATH alone (#42); with an expired
   * one the monitor's own reason stays and the one thing to do is added, with the same command for a PC where claude is not
   * found, or opens an older Claude app that took the name. line is the status's words (linkLine), the expired one's heading. */
  function signInHelp(s, line) {
    var missing = s.status === "no_credentials";
    var busy = S.check.busy;
    var found = S.check.failed ? CHECK_FAILED : S.check.still ? (missing ? CHECK_NONE : CHECK_STALE) : "";
    var body;
    if (missing) {
      var steps = [
        esc("Open PowerShell: press Start and type PowerShell."),
        esc("Install Claude Code:") + commandRow("install") + esc("If it warns that a folder is not in your PATH, carry on. The next step does not need it."),
        esc("Start Claude Code:") + commandRow("start") + esc("Then sign in with your Claude account in the browser that opens."),
        esc("Come back here and press Check again.")
      ];
      body = "<p>" + esc(SIGNIN_WHY) + "</p><ol>" + steps.map(function (step) { return "<li>" + step + "</li>"; }).join("") + "</ol><p>" + esc(SIGNIN_RENEWS) + "</p>";
    } else {
      // An HTTP 403 (#55): Claude turned the sign-in down. Only a new sign-in fixes it, so its help asks for /login instead of
      // a message to Claude Code.
      var refused = s.signin_refused === 403;
      body = (s.status_detail ? "<p>" + esc(s.status_detail) + "</p>" : "") + "<p>" +
        (refused
          ? esc("Only a new sign-in fixes this. Run ") + codeText("claude") + esc(" in PowerShell, type ") + codeText("/login") +
            esc(" and sign in, then press Check again.")
          : esc("Claude Code renews the sign-in when you use it. Send it a message, or run ") + codeText("claude") +
            esc(" in PowerShell, then press Check again.")) +
        "</p><p>" + esc("If PowerShell says ") + codeText("claude") +
        esc(" is not recognized, or the Claude app opens instead, run this:") + "</p>" + commandRow("start");
    }
    return '<div class="nm-msg nm-help" role="region" aria-label="Claude sign-in"><b' + (missing ? "" : lineLangAttr(line.lang)) + ">" +
      esc(missing ? SIGNIN_HEAD : line.text) + "</b>" + body +
      '<div class="nm-actions"><button class="nm-btn solid" type="button" data-action="check-again" data-fid="check-again"' + (busy ? ' aria-disabled="true"' : "") + ">" +
      (busy ? "Checking…" : "Check again") + "</button>" +
      (missing ? '<span class="nm-links"><a href="' + esc(LINKS.readme) + '" data-external="1">More in the README</a></span>' : "") + "</div>" +
      (found ? '<p class="nm-status" role="status">' + esc(found) + "</p>" : "") + "</div>";
  }

  /* Check again: asks the monitor to read the sign-in now (POST /api/refresh answers at once, the cycle runs after it) and keeps
   * "Checking…" on the button until the state of that cycle is in (syncCheck). One at a time. */
  function checkAgain() {
    if (S.check.busy) {
      return;
    }
    var s = st();
    var lost = function () {
      S.check = { busy: false, sent: true, from: "", at: 0, still: false, failed: true };
      renderPage();
    };
    S.check = { busy: true, sent: false, from: s && s.updated_at ? s.updated_at : "", at: Date.now(), still: false, failed: false };
    renderPage();
    api("POST", "/api/refresh").then(function (res) {
      if (!res.ok) {
        lost();
        return;
      }
      S.check.sent = true;
      poll(true);
    }, lost);
  }

  /* Brings a check to its end and says whether the panel changed. It is over when the monitor has published the cycle it was asked
   * for (the state's updated_at moved) or, whatever has arrived, after CHECK_WAIT_MS; a state that still needs a sign-in then
   * makes the panel say so. When the sign-in is found, or the status is another one, there is nothing left to say. */
  function syncCheck() {
    var c = S.check;
    var s = st();
    if (!needsSignIn(s)) {
      if (c.busy || c.still || c.failed) {
        S.check = { busy: false, sent: false, from: "", at: 0, still: false, failed: false };
        return true;
      }
      return false;
    }
    if (!c.busy || !c.sent || (s.updated_at === c.from && Date.now() - c.at < CHECK_WAIT_MS)) {
      return false;
    }
    S.check = { busy: false, sent: true, from: c.from, at: c.at, still: true, failed: false };
    return true;
  }

  /* The command, selected, so Ctrl+C takes it where the clipboard could not be written. which is a key of COMMANDS. */
  function selectCommand(which) {
    var code = $('.nm-help .nm-cmd[data-command="' + which + '"] code');
    var picked = window.getSelection ? window.getSelection() : null;
    if (code && picked && document.createRange) {
      var range = document.createRange();
      range.selectNodeContents(code);
      picked.removeAllRanges();
      picked.addRange(range);
    }
  }

  /* Copy: one command (a key of COMMANDS; any other does nothing) to the clipboard. Where the page may not write it (no clipboard,
   * or it refuses) the command is selected instead. The answer shows on that command's button for COPY_MS. */
  function copyCommand(which) {
    if (!Object.prototype.hasOwnProperty.call(COMMANDS, which)) {
      return;
    }
    var clip = window.navigator && window.navigator.clipboard;
    var answer = function (ok) {
      S.copy = ok ? "copied" : "select";
      S.copyOf = which;
      renderPage();
      if (!ok) {
        selectCommand(which);
      }
      window.clearTimeout(S.copyTimer);
      S.copyTimer = window.setTimeout(function () {
        S.copy = "";
        S.copyOf = "";
        renderPage();
      }, ok ? COPY_MS : COPY_SELECT_MS);
    };
    if (clip && typeof clip.writeText === "function") {
      try {
        clip.writeText(COMMANDS[which]).then(function () { answer(true); }, function () { answer(false); });
        return;
      } catch (e) {
        /* a clipboard that throws: the command is selected instead */
      }
    }
    answer(false);
  }

  /* The help panel's buttons. which is the copy button's data-command. */
  function helpAction(action, which) {
    if (action === "check-again") {
      checkAgain();
    } else if (action === "copy-command") {
      copyCommand(which);
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
      if (S.supporters && S.supporters.error) {
        S.supporters = null;                              // a list that failed is asked for again at this visit, not on every state
        S.supportersRequested = false;
      }
      main.innerHTML = pageHead("about") +
        '<div id="about-update" style="display:contents"></div>' +
        '<div class="nm-pgrid">' +
        card({
          key: "about-app", title: "Claude Usage Monitor", metaHtml: '<span class="nm-meta-t" id="about-version"></span>',
          body: '<p class="nm-lede">A local monitor for Claude Code plan limits. It reads the usage API with your existing Claude Code sign-in, ' +
            "and your own transcripts. It never sends a model request.</p>" +
            '<p class="nm-lede">Free and open source, under the MIT licence.</p>' +
            '<p class="nm-note nm-disc">Cost figures are ' + BASIS + ": what the same tokens would cost at the published API prices. " +
            "They are not amounts billed to your plan. Only the usage credits card shows real money.</p>"
        }) +
        card({
          key: "about-links", title: "Links", meta: "opens in your browser",
          body: '<div class="nm-links"><a href="' + esc(LINKS.repo) + '" data-external="1">Repository on GitHub</a>' +
            '<a href="' + esc(LINKS.bug) + '" data-external="1">Issues: report a bug</a>' +
            '<a href="' + esc(LINKS.patreon) + '" data-external="1">Support on Patreon</a></div>'
        }) +
        card({
          key: "about-credits", title: "Credits", meta: "built with Claude Code",
          body: '<div class="nm-kv nm-authors"><span>AUTHORS</span><b>CHRIS CARPENTER, WITH CLAUDE</b></div>' +
            '<p class="nm-lede">Version 2 was built with Claude Code: Claude Opus 5.5, with Sonnet 5.5 and Haiku 5.5 subagents.</p>' +
            '<p class="nm-lede">Version 1 was built with Claude Sonnet 4.6.</p>'
        }) +
        card({ key: "about-fonts", title: "Fonts", meta: "SIL Open Font License 1.1", body: fontsCardBody() }) +
        '<section class="nm-card wide" aria-label="Supporters" data-card="about-supporters"><div class="nm-card-in"><div class="nm-card-t"><h2 class="nm-ttl">Supporters</h2><span class="nm-meta-t" id="about-supporters-meta">Thank You</span></div>' +
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
        if (Object.keys(patch).some(function (k) { return k.indexOf("guard_") === 0; })) {
          pollGuard();                                    // the guard's status follows its settings at once
        }
        syncSettings($("#main"));
        return true;
      }
      var why = errorText(res);
      setStatus("NOT SAVED: " + FMT.upper(why, "en"), true);
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
    return GUARD_LABELS[key] || FMT.upper(String(key).replace(/_/g, " "), "en");
  }

  function applyThemeChoice(themeId, variantId) {
    var patch = { theme_id: themeId, theme_variant: variantId };
    var theme = findTheme(themeId);
    var name = theme ? theme.name : themeId;
    saveSettings(patch, FMT.upper(name, "en"));
  }

  // A colour button in Settings or the sidebar: save the colour, named on the status line as its button names it.
  function chooseAccent(accent) {
    saveSettings({ theme_variant: accent }, accentLabel(findTheme((currentTheme() || {}).id), accent));
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
      fieldError("alert_thresholds", FMT.percent(String(n)) + " is already a level.");
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
    } else if (key === "guard_session_cap" || key === "guard_weekly_cap" || key === "guard_scoped_cap") {
      var cap = String(el.value).trim();
      if (el.validity && el.validity.badInput) {          // text a number box cannot hold ("9e", "-") reads as "": not a blank
        fieldError(key, "Enter a whole number from 1 to 100, or leave it blank to stop watching this limit.");
        return;
      } else if (cap === "") {
        value = null;                                     // blank is off: the limit is not watched
      } else if (!/^\d{1,3}$/.test(cap) || +cap < 1 || +cap > 100) {
        fieldError(key, "Enter a whole number from 1 to 100, or leave it blank to stop watching this limit.");
        return;
      } else {
        value = parseInt(cap, 10);
      }
    } else if (key === "guard_resume_stagger_s") {
      var stagger = String(el.value).trim();
      if (!/^\d{1,3}$/.test(stagger) || +stagger > 600) {
        fieldError(key, "Enter a whole number of seconds from 0 to 600.");
        return;
      }
      value = parseInt(stagger, 10);
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
      chooseAccent(target.getAttribute("data-accent"));
      return;
    }
    if (target.hasAttribute("data-mode")) {
      var mode = target.getAttribute("data-mode");
      saveSettings({ theme_mode: mode }, "MODE " + FMT.upper(mode, "en"));
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
      S.update = updateAnswer(tLine("settings.update.status.checking"), "offered", offeredVersion());
      api("POST", "/api/update/check").then(function (res) {
        S.update = updateAnswer(checkUpdateText(res), "offered", offeredVersion());
        api("GET", "/api/state").then(function (r) { if (r.ok) { S.data = r.data; S.etag = r.etag; } syncSettings($("#main")); });
      }, function () {
        S.update = updateAnswer(updateRefusal("check", null), "offered", offeredVersion());
        syncSettings($("#main"));
      });
    } else if (action === "install-update") {
      if (!updateRunBusy()) {                          // while a run goes the button is aria-disabled and a click sends nothing (#58)
        installUpdateFromSettings();
      }
    } else if (action === "skip-update") {
      skipUpdate();
    } else if (action === "language-apply") {
      applyLanguage();
    } else if (action === "guard-resume") {
      guardResume();
    } else if (action === "guard-install" || action === "guard-remove") {
      guardSetup(action === "guard-install" ? "install" : "remove");
    }
  }

  /* Apply the Language choice (#87). The next state carries the new locale, and onState reloads the page into it. */
  function applyLanguage() {
    var select = $("#set-language");
    if (select) {
      saveSettings({ language: select.value }, "LANGUAGE");
    }
  }

  /* Update now. The monitor says what it did. "browser": it opened the release page (a source run, or a release it will not
   * install silently). "installer": it started the checked silent update, and only restarts if the download passes the check.
   * One sentence per line, as before #97. */
  function installUpdate() {
    var found = pendingUpdate();
    var forVersion = found ? found.version : offeredVersion();  // a skipped release is still on offer here, and the click gets its answer
    S.update = updateAnswer(tLine("settings.update.status.installing"), "version", forVersion);
    refreshUpdateViews();
    api("POST", "/api/update/apply").then(function (res) {
      var mode = res.ok && res.data && res.data.result ? res.data.result.mode : "";
      var line;
      if (!res.ok) {
        line = updateRefusal("install", res);
      } else if (mode === "browser") {
        line = tLine("settings.update.status.browser");
      } else if (mode === "installer") {
        line = tLine("settings.update.status.installer");
        watchUpdateRun();
      } else {
        line = tLine("settings.update.status.not_started");
      }
      S.update = updateAnswer(line, "version", forVersion);
      refreshUpdateViews();
    }, function () {
      S.update = updateAnswer(updateRefusal("install", null), "version", forVersion);
      refreshUpdateViews();
    });
  }

  /* Skip this version: the banner goes at once, and comes back only for a newer one. */
  function skipUpdate() {
    var found = pendingUpdate();
    var forVersion = found ? found.version : offeredVersion();
    if (found) {
      S.skipped = found.version;
    }
    S.update = updateAnswer(tLine("settings.update.status.skipping"), "version", forVersion);
    refreshUpdateViews();
    api("POST", "/api/update/skip").then(function (res) {
      if (!res.ok) {
        S.skipped = "";
        S.update = updateAnswer(updateRefusal("skip", res), "version", forVersion);
      }
      refreshUpdateViews();
      poll(true);
    }, function () {
      S.skipped = "";
      S.update = updateAnswer(updateRefusal("skip", null), "version", forVersion);
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

  // ── The update's progress (#58) ─────────────────────────────────────────────────
  // While an update runs, Settings → System shows its three steps, the limit cards' bar, the sizes and what it is doing, read
  // from GET /api/update/status: the updater's record (update_check.PROGRESS), ids and numbers that the page words. The page
  // asks every UPDATE_POLL_MS while a run is shown and every UPDATE_IDLE_POLL_MS while Settings is open with a release on offer
  // (the tray can start a run too); never while the window is hidden, another page is open or nothing is on offer, and not
  // again once the monitor has no such route (404 or 501: an older monitor, or the dev server without --update-demo). The
  // banner on the other pages keeps its one line (#114).

  var UPDATE_STATUS_PATH = "/api/update/status";
  var UPDATE_POLL_MS = 500;                // while a run is shown
  var UPDATE_IDLE_POLL_MS = 3000;          // while Settings is open with a release on offer (the guard card's cadence)
  var UPDATE_LOST_AFTER = 3;               // asks in a row with no answer before a running update counts as lost
  var UPDATE_CLICK_GRACE_MS = 3000;        // after Install update, how long its start shows before the record has the run
  var UPDATE_RUN_APART_MS = 1000;          // two failures whose runs began further apart than this are two runs (age_s is to a tenth)
  // Owner question 1 on #58: how long the Check step stays the current one once the record has passed it. The SHA-256 is worked
  // out as the file arrives, so the check itself is one comparison; at 0 the Check chip shows done as soon as it has passed.
  var UPDATE_CHECK_HOLD_MS = 0;
  var UPDATE_CELLS = 32;                   // the limit cards' bar
  var MB_TENTH = 104857.6;                 // bytes in a tenth of a megabyte (1 MB = 1,048,576 bytes, as Windows counts them)
  // The step of each phase the record names (update_check.ACTIVE_PHASES): 0 Download, 1 Check, 2 Restart.
  var UPDATE_STEP_OF = { downloading: 0, checking: 1, starting: 2, restarting: 2 };
  // How a step chip is drawn, by how the step stands: [classes, data-tone], the guard card's tones.
  var UPDATE_STEP_LOOK = {
    done: ["is-ok", "ok"], now: ["", "acc"], next: ["", "neutral"], stalled: ["solid is-warn", "warn"], failed: ["solid is-crit", "alert"]
  };
  // The status line of an update that failed, by its code (update_check.REASONS), r being the record. Functions, so each text
  // is the catalog's at the moment it is drawn.
  var UPDATE_FAILED = {
    timeout: function (r) { return tHtml("settings.update.progress.failed.timeout", { seconds: FMT.number(String(r.timeout_s)) }); },
    network: function () { return tHtml("settings.update.progress.failed.network"); },
    http: function (r) { return tHtml("settings.update.progress.failed.http", { status: String(r.http_status) }); },
    redirect: function () { return tHtml("settings.update.progress.failed.redirect"); },
    too_large: function (r) { return tHtml("settings.update.progress.failed.too_large", { limit: FMT.number(String(r.limit_mb)) }); },
    cut_short: function () { return tHtml("settings.update.progress.failed.cut_short"); },
    digest: function () { return tHtml("settings.update.progress.failed.digest"); },
    rejected: function () { return tHtml("settings.update.progress.failed.rejected"); },
    helper: function () { return tHtml("settings.update.progress.failed.helper"); },
    local: function () { return tHtml("settings.update.progress.failed.local"); },
    error: function () { return tHtml("settings.update.progress.failed.error"); }
  };

  /* What the page knows of a running update (S.progress). */
  function updateProgressState() {
    return {
      status: null,        // the last record the route answered
      flight: false,       // an ask is on its way
      timer: 0,            // the next quick ask
      askedAt: 0,          // when the last ask went (ms)
      misses: 0,           // asks in a row that got no answer
      none: false,         // the monitor has no status route: nothing more is asked in this page load
      clickedAt: 0,        // when Install update started a run here (ms): the start view and the age rule count from it
      active: "",          // the record's phase after the last answer while it showed a run ("downloading" for the start), else ""
      checkAt: 0,          // when the record was first seen past the download (ms), for UPDATE_CHECK_HOLD_MS
      over: null,          // the S.update the block was drawn over: that line is the run's own, and stays hidden under it
      failOver: null,      // the S.update when the failure was first shown: a click gives S.update a new answer and ends it
      failStart: null,     // when that failure's run began (ms: the answer's time less its age_s), to tell a later run's from it
      failFor: "",         // the release on offer when it was shown: a failure that names no version is that release's only
      applying: null       // Install update clicked over a shown failure: { over, at }, the S.update its start shows over (true
                           // until installUpdate has drawn its own) and when, while the monitor answers the click
    };
  }

  function updateCount(value) {
    return typeof value === "number" && isFinite(value) && value >= 0 ? value : null;
  }

  function isRunPhase(phase) {
    return Object.prototype.hasOwnProperty.call(UPDATE_STEP_OF, phase);
  }

  /* Whether a record is about the release on offer. A failed one may name no version (a link that is not this project's). */
  function updateIsFor(rec, offered) {
    return !!rec && (rec.version === offered || (rec.version === null && rec.phase === "failed"));
  }

  /* Just after Install update started a run, a record with no run in it yet (none answered, idle, or a failure older than the
   * click) is the click's run not begun: the start view shows instead. A running record is shown as it is. */
  function updateStale(p, rec, now) {
    var since = now - p.clickedAt;
    if (!p.clickedAt || since > UPDATE_CLICK_GRACE_MS) {
      return false;
    }
    return !rec || rec.phase === "idle" || (rec.phase === "failed" && (updateCount(rec.age_s) || 0) * 1000 > since + 1000);
  }

  /* Owner question 4 on #58: how long a failure stays. Until the next click on an update button (Check for updates now, Install
   * update, Skip this version, or the banner's), each of which gives S.update a new answer that then shows in its place, or
   * until a new run starts; and only while its release is on offer (a failure that names no version, a link that was not this
   * project's, is the release's that was on offer when it was shown). updateFailureNew says which failures are shown at all. */
  function updateFailureLasts(p, update, offered) {
    return p.failOver !== null && p.failOver === update && ((p.status !== null && p.status.version !== null) || p.failFor === offered);
  }

  /* Part of question 4: whether a failed record whose run began at start (the page's clock) is shown. Yes for the first one
   * since a run was seen going or started here, and for one whose run began later than the shown one's (the tray started
   * another); a failure that a click ended does not come back with the next answer of the same run. */
  function updateFailureNew(p, start) {
    return p.failOver === null || p.failStart === null || start - p.failStart > UPDATE_RUN_APART_MS;
  }

  /* A run was seen going, or was started here: the failure shown before it is over. */
  function endUpdateFailure(p) {
    p.failOver = null;
    p.failStart = null;
    p.failFor = "";
  }

  /* What Settings shows of the update, from what the page knows (p is S.progress), or null for nothing (today's line). kind is
   * "start" (just after Install update, before the record has the run), "run", "failed", "lost" (the monitor stopped answering
   * while the update downloaded or was checked) or "gone" (it stopped answering once it had begun to close for the installer).
   * hold is UPDATE_CHECK_HOLD_MS unless given. */
  function progressView(p, now, offered, update, hold) {
    if (p.none || !offered) {
      return null;
    }
    var rec = p.status;
    var phase = rec ? rec.phase : "";
    if (p.misses >= UPDATE_LOST_AFTER && p.active) {       // never with no run: a monitor that quits keeps today's line
      var last = isRunPhase(phase) && updateIsFor(rec, offered) ? rec : null;
      var going = p.active === "starting" || p.active === "restarting";
      return updateViewOf(going ? "gone" : "lost", going ? "restarting" : p.active, last, offered);
    }
    if (isRunPhase(phase) && updateIsFor(rec, offered)) {
      var waited = hold === undefined ? UPDATE_CHECK_HOLD_MS : hold;
      var held = (phase === "starting" || phase === "restarting") && waited > 0 && p.checkAt > 0 && now - p.checkAt < waited;
      return updateViewOf("run", held ? "checking" : phase, rec, offered);
    }
    var a = p.applying;
    if ((a && (a.over === true || a.over === update) && now - a.at <= UPDATE_CLICK_GRACE_MS) || updateStale(p, rec, now)) {
      return updateViewOf("start", "downloading", null, offered);
    }
    if (phase === "failed" && updateIsFor(rec, offered) && updateFailureLasts(p, update, offered)) {
      return updateViewOf("failed", rec.failed_in, rec, offered);
    }
    return null;
  }

  function updateViewOf(kind, phase, rec, offered) {
    var step = isRunPhase(phase) ? UPDATE_STEP_OF[phase] : 0;        // a failure outside every phase is the download's
    var stalled = kind === "run" && phase === "downloading" && rec.stalled === true;
    return {
      kind: kind, phase: isRunPhase(phase) ? phase : "downloading", step: step,
      state: kind === "failed" || kind === "lost" ? "failed" : stalled ? "stalled" : "now",
      done: rec ? updateCount(rec.done) || 0 : 0, total: rec ? updateCount(rec.total) : null,
      full: step > 0,                                                // past the download: the whole file is in
      stalled: stalled, version: rec && typeof rec.version === "string" ? rec.version : offered, record: rec,
      busy: kind === "start" || kind === "run" || kind === "gone"
    };
  }

  /* A megabyte figure from tenths, in the locale's numbers ("4.2"; French "4,2"). */
  function mbText(tenths) {
    return FMT.number((Math.max(0, tenths) / 10).toFixed(1));
  }

  /* done of total, to a tenth of a megabyte: the total rounded and the done floored, so the done never reads the total's figure
   * before the last byte (it is written a tenth lower where the two would read the same) and reads it at the end. */
  function updateSizes(done, total) {
    var whole = Math.round(total / MB_TENTH);
    var got = done >= total ? whole : Math.floor(done / MB_TENTH);
    if (done < total && got >= whole) {
      got = whole - 1;
    }
    return { done: mbText(got), total: mbText(whole) };
  }

  /* Which sizes line a view has, with its figures: "connecting" (no byte yet and no size), "size" (done of total), "so_far"
   * (the size not known), or "" (nothing to say). A whole file of no announced size reads as done of itself. */
  function updateSizeOf(view) {
    if (view.kind === "start" || (view.kind === "run" && view.phase === "downloading" && view.done === 0 && view.total === null)) {
      return { which: "connecting" };
    }
    if (view.total !== null || (view.full && view.done > 0)) {
      var sizes = updateSizes(view.done, view.total !== null ? view.total : view.done);
      return { which: "size", done: sizes.done, total: sizes.total };
    }
    return view.done > 0 ? { which: "so_far", done: mbText(Math.floor(view.done / MB_TENTH)) } : { which: "" };
  }

  function updateSizeHtml(size) {
    if (size.which === "connecting") {
      return tHtml("settings.update.progress.connecting");
    }
    if (size.which === "size") {
      return tHtml("settings.update.progress.size", { done: size.done, total: size.total });
    }
    return size.which === "so_far" ? tHtml("settings.update.progress.size_unknown", { done: size.done }) : "";
  }

  /* The same as plain text, for what a screen reader says of the bar. */
  function updateSizeText(size) {
    if (size.which === "connecting") {
      return t("settings.update.progress.connecting");
    }
    if (size.which === "size") {
      return t("settings.update.progress.size", { done: size.done, total: size.total });
    }
    return size.which === "so_far" ? t("settings.update.progress.size_unknown", { done: size.done }) : "";
  }

  /* The steps as three chips: those before the current one done, the current one as the view stands, the rest next. */
  function updateStepsHtml(view) {
    var words = [tHtml("settings.update.progress.step.download"), tHtml("settings.update.progress.step.check"),
      tHtml("settings.update.progress.step.restart")];
    return words.map(function (word, i) {
      var stand = i < view.step ? "done" : i === view.step ? view.state : "next";
      var look = UPDATE_STEP_LOOK[stand];
      return '<span class="nm-chip' + (look[0] ? " " + look[0] : "") + '" data-tone="' + look[1] + '" data-step="' + stand + '">' +
        word + "</span>";
    }).join("");
  }

  /* The bar: the share written so far, whole once the download is in, the theme's pending bar while the size is not known
   * (data-pending, which motion.js marks), and hot once the update failed. */
  function updateBar(view) {
    var pending = (view.kind === "start" || view.kind === "run") && !view.full && view.total === null;
    var pct = view.full ? 100 : view.total ? clamp(view.done / view.total * 100, 0, 100) : 0;
    return { pct: pending ? 0 : pct, pending: pending, hot: view.state === "failed" };
  }

  /* What the bar shows, as its data-sig: the lit cells and the part of the next one, to a tenth as cellsHTML writes it, so the
   * bar moves with every block that arrives and not only once a whole cell (a 32nd of the file) is full. */
  function updateBarSig(bar) {
    var x = bar.pct / 100 * UPDATE_CELLS;
    var lit = Math.floor(x);
    return (bar.pending ? "pending:" : "") + (bar.hot ? "hot:" : "") + lit + ":" + ((x - lit) * 100).toFixed(1);
  }

  /* The status line: what the update is doing, or why it stopped. */
  function updateLineHtml(view) {
    if (view.kind === "lost") {
      return tHtml("settings.update.progress.lost");
    }
    if (view.kind === "gone") {
      return tHtml("settings.update.progress.gone");                 // no version: if the installer cannot run, the old one comes back
    }
    if (view.kind === "failed") {
      var r = view.record;
      var code = Object.prototype.hasOwnProperty.call(UPDATE_FAILED, r.reason) ? r.reason : "error";
      var number = { http: r.http_status, timeout: r.timeout_s, too_large: r.limit_mb }[code];
      return UPDATE_FAILED[number === undefined || updateCount(number) !== null ? code : "error"](r);
    }
    if (view.stalled) {
      return tHtml("settings.update.progress.stalled");
    }
    if (view.phase === "checking") {
      return tHtml("settings.update.progress.checking");
    }
    if (view.phase === "starting") {
      return tHtml("settings.update.progress.starting");
    }
    if (view.phase === "restarting") {
      return tHtml("settings.update.progress.restarting", { version: view.version });
    }
    return view.total === null && view.done > 0 ? tHtml("settings.update.progress.downloading_unknown", { version: view.version }) :
      tHtml("settings.update.progress.downloading", { version: view.version });
  }

  /* The parts of the block for a view: its tone, the chips, the bar, the sizes and the status line. */
  function progressParts(view) {
    var bar = updateBar(view);
    var size = updateSizeOf(view);
    return {
      tone: view.state === "failed" ? "is-crit" : view.state === "stalled" ? "is-warn" : "",
      steps: updateStepsHtml(view), bar: bar, barSig: updateBarSig(bar), cells: cellsHTML(UPDATE_CELLS, bar.pct),
      now: bar.pending ? null : Math.floor(bar.pct), text: updateSizeText(size), size: updateSizeHtml(size),
      line: updateLineHtml(view), err: view.state === "failed"
    };
  }

  /* The block's markup as it is first drawn; later answers patch its parts (patchUpdateBlock). */
  function progressHtml(parts) {
    return '<div class="nm-verdict" data-part="steps" data-sig="' + esc(parts.steps) + '">' + parts.steps + "</div>" +
      '<div role="progressbar" data-part="bar" aria-label="' + esc(t("settings.update.progress.label")) + '" aria-valuemin="0" aria-valuemax="100"' +
      (parts.now === null ? "" : ' aria-valuenow="' + parts.now + '"') + (parts.text ? ' aria-valuetext="' + esc(parts.text) + '"' : "") + ">" +
      '<div class="nm-cells' + (parts.bar.hot ? " hot" : "") + '" aria-hidden="true" data-sig="' + esc(parts.barSig) + '"' +
      (parts.bar.pending ? ' data-pending=""' : "") + ">" + parts.cells + "</div></div>" +
      '<p class="nm-story" data-part="size" data-sig="' + esc(parts.size) + '">' + parts.size + "</p>" +
      '<p class="nm-status' + (parts.err ? " is-err" : "") + '" role="status" data-part="line" data-sig="' + esc(parts.line) + '">' +
      parts.line + "</p>";
  }

  /* Draws the block, or patches only the parts that changed (each keeps its data-sig), so an answer every half second leaves
   * alone what did not move: the chips are rebuilt only when a step changes (Gates tones a chip when it is added), the cells
   * when the bar moves, and the status line, a live region, only when its words change. */
  function patchUpdateBlock(box, parts) {
    if (!parts) {
      if (!box.hidden || box.getAttribute("data-sig")) {
        box.hidden = true;
        box.className = "nm-uprog";
        box.removeAttribute("data-sig");
        box.innerHTML = "";
      }
      return;
    }
    var tone = "nm-uprog" + (parts.tone ? " " + parts.tone : "");
    if (box.className !== tone) {
      box.className = tone;
    }
    box.hidden = false;
    var steps = $('[data-part="steps"]', box);
    var bar = $('[data-part="bar"]', box);
    var cells = bar ? $(".nm-cells", bar) : null;
    var size = $('[data-part="size"]', box);
    var line = $('[data-part="line"]', box);
    if (!box.getAttribute("data-sig") || !steps || !cells || !size || !line) {
      box.setAttribute("data-sig", "drawn");
      box.innerHTML = progressHtml(parts);
      return;
    }
    if (steps.getAttribute("data-sig") !== parts.steps) {
      steps.setAttribute("data-sig", parts.steps);
      steps.innerHTML = parts.steps;
    }
    if (parts.now === null) {
      bar.removeAttribute("aria-valuenow");
    } else {
      bar.setAttribute("aria-valuenow", String(parts.now));
    }
    if (parts.text) {
      bar.setAttribute("aria-valuetext", parts.text);
    } else {
      bar.removeAttribute("aria-valuetext");
    }
    if (cells.getAttribute("data-sig") !== parts.barSig) {
      cells.setAttribute("data-sig", parts.barSig);
      cells.className = "nm-cells" + (parts.bar.hot ? " hot" : "");
      cells.innerHTML = parts.cells;
      if (parts.bar.pending) {
        cells.setAttribute("data-pending", "");
      } else {
        cells.removeAttribute("data-pending");                       // the size came: the theme's arrival plays (motion.js)
      }
    }
    if (size.getAttribute("data-sig") !== parts.size) {
      size.setAttribute("data-sig", parts.size);
      size.innerHTML = parts.size;
    }
    var lineClass = "nm-status" + (parts.err ? " is-err" : "");
    if (line.className !== lineClass) {
      line.className = lineClass;
    }
    if (line.getAttribute("data-sig") !== parts.line) {
      line.setAttribute("data-sig", parts.line);
      line.innerHTML = parts.line;
    }
  }

  /* Puts Settings' update rows in step with what the page knows: the block; the line under the buttons, hidden while it is the
   * run's own (an answer given after the run began shows under the block); and Install update, aria-disabled while a run goes
   * (it keeps the keyboard focus, and a click sends nothing). */
  function syncUpdateProgress(main) {
    main = main || $("#main");
    var box = main ? $("#set-update-progress", main) : null;
    if (!box) {
      return;
    }
    var p = S.progress;
    var view = progressView(p, Date.now(), offeredVersion(), S.update);
    p.over = view ? (p.over === null ? S.update : p.over) : null;
    patchUpdateBlock(box, view ? progressParts(view) : null);
    var hide = !!view && S.update === p.over;
    var status = $("#set-update-status", main);
    if (status) {
      var text = hide ? "" : updateLine();
      status.hidden = hide;
      if (status.textContent !== text) {
        status.textContent = text;
      }
    }
    var install = $('[data-action="install-update"]', main);
    if (install) {
      if (view && view.busy) {
        install.setAttribute("aria-disabled", "true");
      } else {
        install.removeAttribute("aria-disabled");
      }
    }
  }

  /* A run is going, or the monitor is closing for it: Install update sends nothing. */
  function updateRunBusy() {
    var view = progressView(S.progress, Date.now(), offeredVersion(), S.update);
    return !!view && view.busy;
  }

  /* Whether the page asks about an update now: Settings is on screen, the window is visible, a release is on offer (without one
   * no run can start: the monitor needs its link) and the monitor has the route. */
  function updateAsksWanted() {
    return !S.progress.none && S.page === "settings" && S.formKey === "settings" && document.visibilityState !== "hidden" &&
      offeredVersion() !== "";
  }

  /* Asks GET /api/update/status, one ask at a time. */
  function askUpdateStatus() {
    var p = S.progress;
    window.clearTimeout(p.timer);
    p.timer = 0;
    if (p.flight || !updateAsksWanted()) {
      return;
    }
    p.flight = true;
    p.askedAt = Date.now();
    api("GET", UPDATE_STATUS_PATH).then(function (res) {
      p.flight = false;
      if (res.status === 404 || res.status === 501) {
        p.none = true;                                               // an older monitor, or a dev server without the demo
      } else if (res.ok && res.data && typeof res.data.phase === "string") {
        takeUpdateStatus(res.data);
      } else {
        p.misses += 1;
      }
      updateAnswered();
    }, function () {
      p.flight = false;
      p.misses += 1;
      updateAnswered();
    });
  }

  /* A record the monitor answered. A running one for the release on offer ends a failure shown before it; a failure is shown
   * from the first answer that has it (not one older than a click that started a new run), once for its run (updateFailureNew). */
  function takeUpdateStatus(rec) {
    var p = S.progress;
    var now = Date.now();
    var mine = isRunPhase(rec.phase) && updateIsFor(rec, offeredVersion());
    var start = now - (updateCount(rec.age_s) || 0) * 1000;
    p.status = rec;
    p.misses = 0;
    if (mine) {
      endUpdateFailure(p);
    } else if (rec.phase === "failed" && !updateStale(p, rec, now) && updateFailureNew(p, start)) {
      p.failOver = S.update;
      p.failStart = start;
      p.failFor = offeredVersion();
    }
    p.checkAt = mine && rec.phase !== "downloading" ? p.checkAt || now : 0;
    p.active = mine ? rec.phase : updateStale(p, rec, now) ? "downloading" : "";
  }

  /* After an answer, or an ask that got none: the rows follow, and while a run shows the next ask is due in UPDATE_POLL_MS. */
  function updateAnswered() {
    syncUpdateProgress();
    var p = S.progress;
    var view = progressView(p, Date.now(), offeredVersion(), S.update);
    if (view && (view.kind === "start" || view.kind === "run") && updateAsksWanted()) {
      p.timer = window.setTimeout(askUpdateStatus, UPDATE_POLL_MS);
    }
  }

  /* Once a second, from tick(): ask every UPDATE_IDLE_POLL_MS unless the quick asks of a run are going (a tray click can start a
   * run while Settings is open), and let the start view and a lost link follow the clock. Nothing is asked once the monitor has
   * gone to install the update. */
  function updateTick() {
    if (!updateAsksWanted()) {
      return;
    }
    var p = S.progress;
    var view = progressView(p, Date.now(), offeredVersion(), S.update);
    if (!p.flight && !p.timer && !(view && view.kind === "gone") && Date.now() - p.askedAt >= UPDATE_IDLE_POLL_MS) {
      askUpdateStatus();
    }
    syncUpdateProgress();
  }

  /* Another page opened: the quick asks are Settings' own. */
  function stopUpdateAsks() {
    window.clearTimeout(S.progress.timer);
    S.progress.timer = 0;
  }

  /* Install update started the checked silent update (the monitor answered "installer"): the run shows at once, as its start
   * until the record has it, and is asked for every UPDATE_POLL_MS. Called from installUpdate. */
  function watchUpdateRun() {
    var p = S.progress;
    p.clickedAt = Date.now();
    p.active = "downloading";
    p.misses = 0;
    p.checkAt = 0;
    p.over = null;
    p.status = null;                                                 // what the monitor said before is the run before's
    endUpdateFailure(p);
    askUpdateStatus();
  }

  /* Install update on Settings. Over a failure that shows, the block stays, as the new run's start, while the monitor answers
   * the click, so the card does not drop to the one line and grow again: if it starts the update, watchUpdateRun takes over;
   * any other answer replaces the S.update the start shows over, and shows in its place. With no failure shown it is
   * installUpdate alone (a source run answers "browser", and the start's "Downloading version" would not be true). */
  function installUpdateFromSettings() {
    var p = S.progress;
    var view = progressView(p, Date.now(), offeredVersion(), S.update);
    if (view && view.kind === "failed") {
      p.applying = { over: true, at: Date.now() };
      p.over = null;                                                 // the INSTALLING… answer is then the block's own, hidden under it
    }
    installUpdate();
    if (p.applying && p.applying.over === true) {
      p.applying.over = S.update;
    }
  }

  // ── Export ──────────────────────────────────────────────────────────────────────

  function pywebviewBridge() {
    return window.pywebview && window.pywebview.api ? window.pywebview.api : null;
  }

  /* The status line: the words in capitals, a file name or path exactly as it is. */
  function exportStatusHtml() {
    var x = S.exportStatus;
    return esc(x.text) + (x.path ? " " + tPath(x.path).html : "");
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
    var fx = window.USAGE_MONITOR_THEME_FX && window.USAGE_MONITOR_THEME_FX[id];
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
    var info = s ? entryOf(STATUS, s.status, "loading") : STATUS.loading;
    var linkDown = S.linkOk === false;
    var up = document.getElementById("uplink");
    var dot = document.getElementById("uplink-dot");
    writeLine(document.getElementById("uplink-text"), linkLine(linkDown ? "lost" : s ? s.status : "loading"));
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
    var age = fmtAge(ageMs());
    writeLine(document.getElementById("data-age"), ageLine(age));
    var updated = document.querySelector("#health [data-updated]");
    if (updated) {
      writeLine(updated, age === null ? bareLine("--") : tLine("nav.health.ago", { age: ageWords(age, AGE_UNIT_IN_SENTENCE) }));
    }
  }

  /* The header before the monitor has answered: the shell's own words, in the look's (Gates' from the first frame). Only the
   * words: the link's mark stays as the shell has it until a state or a failed poll says how the link is. */
  function renderFirstChrome() {
    writeLine(document.getElementById("uplink-text"), linkLine("loading"));
    renderAge();
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
      ? (ingest.running
        ? tLine("nav.health.ingest", { percent: FMT.percent(String(Math.floor(100 * (ingest.files_done || 0) / Math.max(1, ingest.files_total || 1)))) })
        : tLine("nav.health.files", { count: fmtInt(ingest.files_total) }))
      : bareLine("--");
    var unpriced = s && s.unpriced_models ? s.unpriced_models.length : 0;
    var link = S.linkOk === false ? linkLine("lost") : (s ? healthLinkLine(s.status) : bareLine("--"));
    var live = S.linkOk !== false && !!(s && s.status === "live");
    var signature = [link.text, link.lang, sources.text, sources.lang, unpriced, live, gatesLook()].join("|");   // the look picks words too
    if (box.getAttribute("data-sig") !== signature) {
      box.setAttribute("data-sig", signature);
      box.innerHTML = '<div class="nm-kick">' + tHtml("nav.health.title") + "</div>" +
        '<div class="nm-kv"><span>' + tHtml("nav.health.link") + '</span><b class="' + (live ? "ok" : "is-down") + '"' + lineLangAttr(link.lang) + '><i class="nm-alive' +
        (live ? "" : " is-off") + '"></i>' + esc(link.text) + "</b></div>" +
        '<div class="nm-kv"><span>' + tHtml("nav.health.updated") + '</span><b data-updated="1">--</b></div>' +
        '<div class="nm-kv"><span>' + tHtml("nav.health.sources") + "</span><b" + lineLangAttr(sources.lang) + ">" + esc(sources.text) + "</b></div>" +
        '<div class="nm-kv"><span>' + tHtml("nav.health.unpriced") + "</span><b>" + esc(String(unpriced)) + "</b></div>";
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
      writeLine(label, theme && theme.id === "nostromo" ? tLine("nav.colour.label.phosphor") : tLine("nav.colour.label.accent"));
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
    var full = theme && theme.id === "nostromo";          // Nostromo's six phosphors fit by name, two to a row
    var html = accents.slice(0, 8).map(function (a) {
      return '<button type="button" data-accent="' + esc(a) + '" class="' + (a === active ? "on" : "") + '" aria-pressed="' + (a === active ? "true" : "false") +
        '" title="' + esc(accentTip(a)) + '">' + esc(full ? accentLabel(meta, a) : accentShort(a)) + "</button>";
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
      stopUpdateAsks();                                  // the update's quick asks are Settings' own
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

  /* Whether the monitor would now serve the page in another locale than this one's (#87): the Language setting changed, or
   * Windows' regional format or clock did. Only what the page shows counts. */
  function localeChanged(locale) {
    return !!locale && typeof locale.language === "string" &&
      (locale.language !== LOCALE.language || locale.format !== LOCALE.format || locale.clock !== LOCALE.clock);
  }

  /* The mark a reload into locale leaves in the window's name. */
  function localeMark(locale) {
    return LOCALE_RELOAD_MARK + [locale.language, locale.format, locale.clock].join("|");
  }

  /* Reload into the monitor's new locale. Never when this page did not know its own. The window's name carries a mark over
   * the reload (it belongs to this window and ends with it; app.js keeps nothing in browser storage, #54), and a page that
   * arrives in the marked locale clears it. So only a reload that failed to bring the page into its locale holds back the
   * next one for that locale, and a server that kept serving another locale could not loop the page. */
  function reloadForLocale(locale) {
    if (!LOCALE_KNOWN) {
      return false;
    }
    if (S.reloading) {
      return true;
    }
    var mark = localeMark(locale);
    if (window.name === mark) {
      return false;
    }
    window.name = mark;
    S.reloading = true;
    window.location.reload();
    return true;
  }

  /* What a new state does first: reload into the monitor's new locale, if it has one. */
  function localeReload() {
    var locale = S.data && S.data.locale;
    return localeChanged(locale) && reloadForLocale(locale);
  }

  function onState() {
    if (localeReload()) {
      return;                                            // the page comes back in the monitor's new locale
    }
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
    syncCheck();                                       // a check of the sign-in ends with the state of the cycle it asked for
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
    guardTick();
    updateTick();
    if (syncCheck()) {
      renderPage();                                    // a check that waited too long ends, or the sign-in was found
    }
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
    savePrefs(map[name]);
    renderPage();
  }

  function installBaseStyle() {
    if (document.getElementById("mon-app-style")) {
      return;
    }
    var style = document.createElement("style");
    style.id = "mon-app-style";
    style.textContent = BASE_CSS;
    document.head.insertBefore(style, document.head.firstChild);
  }

  function init() {
    installBaseStyle();
    var main = document.getElementById("main");
    var variants = document.getElementById("variants");
    var refresh = document.getElementById("refresh");
    var tipEl = document.getElementById("tip");
    loadPrefs(SAVED_PREFS);
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
        askUpdateStatus();                             // a running update is asked for again at once (none while hidden)
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
          chooseAccent(button.getAttribute("data-accent"));
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
        var help = closest(".nm-help button[data-action]");
        if (help) {
          helpAction(help.getAttribute("data-action"), help.getAttribute("data-command"));
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
    renderFirstChrome();                               // the shell's words in the look's (the 1 s tick and a failed poll follow it)
    renderPage();                                      // the page opens at once as placeholders; the first state fills it in
    poll();
  }

  /* Test hook. tests/test_webui.py loads this file in node with window.__USAGE_MONITOR_TEST__ set and reads the helpers it
   * needs from there; the page is then not started. Nothing in the app sets it (the page allows no inline script),
   * so in the dashboard this is a no-op. */
  if (window.__USAGE_MONITOR_TEST__) {
    window.__USAGE_MONITOR_TEST__.api = {
      S: S, PACE_BAND: PACE_BAND, esc: esc, num: num, fmtPct: fmtPct, fmtRate: fmtRate, fmtUsd: fmtUsd, fmtMoney: fmtMoney,
      fmtMult: fmtMult, paceOf: paceOf, ratioCells: ratioCells, ratioNote: ratioNote, ratioScale: ratioScale,
      costNumbers: costNumbers, modelMixCard: modelMixCard, spendCardBody: spendCardBody, sessionsTable: sessionsTable,
      renderHealth: renderHealth, weeklyStory: weeklyStory, effectivePlanPrice: effectivePlanPrice,
      planFromState: planFromState, pipelineCard: pipelineCard, topModels: topModels, railHTML: railHTML,
      limitCard: limitCard, remainHTML: remainHTML, buildModel: buildModel, matchScoped: matchScoped, paceChip: paceChip,
      sessionStory: sessionStory, weeklyStory: weeklyStory, fmtAge: fmtAge, ageWords: ageWords, AGE_UNIT: AGE_UNIT,
      AGE_UNIT_IN_SENTENCE: AGE_UNIT_IN_SENTENCE, staleBanner: staleBanner, renderHealth: renderHealth, accentName: accentName,
      accentTitled: accentTitled, entryOf: entryOf, statEntry: statEntry, entryState: entryState,
      averageRateForecast: averageRateForecast, pacingChart: pacingChart, limitFacts: limitFacts, pendingUpdate: pendingUpdate,
      updateBanner: updateBanner, themeSwatches: themeSwatches, accentLabel: accentLabel, renderVariants: renderVariants,
      syncThemePicker: syncThemePicker, chooseAccent: chooseAccent, currentPage: currentPage, skelCost: skelCost,
      skelEfficiency: skelEfficiency, skelHistory: skelHistory, errorSwap: errorSwap, tagCard: tagCard, cardPart: cardPart,
      playEnterIfDue: playEnterIfDue, wantEnter: wantEnter, loadPrefs: loadPrefs, savePrefs: savePrefs, onSegment: onSegment, t: t, tLang: tLang, tHtml: tHtml, tPlural: tPlural,
      LOCALE: LOCALE, I18N: I18N, LANGUAGE_NAMES: LANGUAGE_NAMES, languageRow: languageRow, onState: onState,
      LOCALE_KNOWN: LOCALE_KNOWN, markup: markup, tCode: tCode, tPath: tPath, tName: tName, pluralCategory: pluralCategory,
      localeChanged: localeChanged, reloadForLocale: reloadForLocale, syncLanguage: syncLanguage, applyLanguage: applyLanguage,
      localeReload: localeReload, localeMark: localeMark,
      PREF_CHOICES: PREF_CHOICES, SAVED_PREFS: SAVED_PREFS, PAGE_KEYS: PAGE_KEYS, clockText: clockText,
      errorText: errorText, failedText: failedText, checkUpdateText: checkUpdateText, settingsClick: settingsClick,
      tLine: tLine, updateRefusal: updateRefusal, updateLineSaid: updateLineSaid, toggleRow: toggleRow, APP_NAME: APP_NAME,
      updateAction: updateAction, installUpdate: installUpdate, skipUpdate: skipUpdate, aboutPage: aboutPage,
      settingsPage: settingsPage, settingsChange: settingsChange, labelFor: labelFor, guardCard: guardCard, guardView: guardView,
      guardLiveHtml: guardLiveHtml, guardSetupHtml: guardSetupHtml, guardResultHtml: guardResultHtml, syncGuard: syncGuard,
      pollGuard: pollGuard, guardTick: guardTick, statusBanner: statusBanner, limitsOnly: limitsOnly, pageStats: pageStats,
      checkAgain: checkAgain, syncCheck: syncCheck, copyCommand: copyCommand, helpAction: helpAction, renderPage: renderPage, poll: poll,
      loadStat: loadStat,
      // the header's, the sidebar's and the headings' words (#98)
      renderChrome: renderChrome, renderAge: renderAge, renderFirstChrome: renderFirstChrome, linkLine: linkLine,
      healthLinkLine: healthLinkLine, ageLine: ageLine, gatesLook: gatesLook, pageHead: pageHead, accentTip: accentTip,
      accentShort: accentShort, tUpper: tUpper, burnUnitLine: burnUnitLine, writeLine: writeLine,
      // the banners', Overview's and Limits' words (#98)
      statReason: statReason, statProblem: statProblem, statProblemShort: statProblemShort, problemBanner: problemBanner,
      STAT_ERRORS: STAT_ERRORS, STAT_ERROR_STATUS: STAT_ERROR_STATUS, limitTag: limitTag, planWords: planWords, costCard: costCard,
      pacingCard: pacingCard, pendingStory: pendingStory, burnHourLine: burnHourLine, ratioNoteLine: ratioNoteLine,
      tHtmlLine: tHtmlLine, lineAttrs: lineAttrs, nameLine: nameLine,
      // The formatters and the sites tests/webui_formats.js holds to today's English (#91).
      fmtUsdExact: fmtUsdExact, fmtPlanPrice: fmtPlanPrice, fmtUsdShort: fmtUsdShort, fmtUsdTile: fmtUsdTile,
      fmtUsdHero: fmtUsdHero, fmtCount: fmtCount, fmtInt: fmtInt, fmtRemain: fmtRemain, fmtCompare: fmtCompare,
      signed: signed, pctWords: pctWords, plural: plural, listWords: listWords, stampText: stampText, dayText: dayText,
      dateText: dateText, hourText: hourText, rangeText: rangeText, hourRange: hourRange, odometerHTML: odometerHTML,
      comparisonList: comparisonList, efficiencyCard: efficiencyCard, efficiencyCards: efficiencyCards,
      weeklyForecast: weeklyForecast, dailyChart: dailyChart, DAYS: DAYS, WEEKDAYS_MON: WEEKDAYS_MON, MONTHS: MONTHS,
      // and the date, word and case sites of steps 2.3 to 2.7
      dayLabel: dayLabel, monthLabel: monthLabel, whoWords: whoWords, workWords: workWords, surfaceCard: surfaceCard,
      // and #91's review: the alert levels, the pending money notes and the month-by-month chart
      syncAlerts: syncAlerts, addLevel: addLevel, costToolbar: costToolbar, monthlyCard: monthlyCard,
      // and the update's progress (#58)
      progressView: progressView, progressParts: progressParts, progressHtml: progressHtml, patchUpdateBlock: patchUpdateBlock,
      syncUpdateProgress: syncUpdateProgress, askUpdateStatus: askUpdateStatus, takeUpdateStatus: takeUpdateStatus,
      updateTick: updateTick, watchUpdateRun: watchUpdateRun, stopUpdateAsks: stopUpdateAsks, updateRunBusy: updateRunBusy,
      updateSizes: updateSizes, updateProgressState: updateProgressState, syncUpdate: syncUpdate, updateLine: updateLine,
      UPDATE_POLL_MS: UPDATE_POLL_MS, UPDATE_IDLE_POLL_MS: UPDATE_IDLE_POLL_MS, UPDATE_LOST_AFTER: UPDATE_LOST_AFTER,
      UPDATE_CLICK_GRACE_MS: UPDATE_CLICK_GRACE_MS, UPDATE_CHECK_HOLD_MS: UPDATE_CHECK_HOLD_MS
    };
    return;
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
