"use strict";
/* Runs the dashboard's real scripts (charts.js, app.js) under node and prints what they do as one JSON object.
 *
 * tests/test_webui.py starts this with `node tests/webui_checks.js <webui folder>` and asserts on the result.
 * app.js is loaded in a vm context with a small fake DOM and window.__CUM_TEST__ set, so it hands back its
 * helpers (see the test hook at the end of app.js) instead of starting the page. Nothing here needs a browser,
 * a window, the network or a timer, and nothing in it reads or prints a user's data: every input is made up.
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const webui = process.argv[2];

function element(id) {
  const attrs = {};
  return {
    id: id || "", innerHTML: "", textContent: "", className: "", style: {}, hidden: false, disabled: false,
    getAttribute: (k) => (Object.prototype.hasOwnProperty.call(attrs, k) ? attrs[k] : null),
    setAttribute: (k, v) => { attrs[k] = String(v); },
    removeAttribute: (k) => { delete attrs[k]; },
    classList: { toggle() {}, add() {}, remove() {}, contains: () => false },
    querySelector: () => null, querySelectorAll: () => [], addEventListener() {}, appendChild() {},
    insertBefore() {}, focus() {}, closest: () => null,
  };
}

// A stand-in for the browser's text measure: every character is this many px wide (a proportional font is narrower
// than the 7.9 px charts.js assumes without a page to ask). Tests change it between measurements.
let pxPerChar = 6;

function svgElement(tag) {
  const el = element(tag);
  el.parentNode = null;
  el.appendChild = (child) => { child.parentNode = el; return child; };
  el.getComputedTextLength = () => String(el.textContent).length * pxPerChar;
  return el;
}

function load() {
  const elements = {};
  const doc = {
    readyState: "complete", visibilityState: "visible", documentElement: element("html"), head: element("head"),
    body: svgElement("body"), querySelector: () => null, querySelectorAll: () => [],
    getElementById: (id) => elements[id] || null, createElement: (tag) => element(tag),
    createElementNS: (_ns, tag) => svgElement(tag), addEventListener() {},
  };
  const box = { console, document: doc, __CUM_TEST__: {} };
  box.window = box;
  box.location = { hash: "" };
  box.localStorage = { getItem: () => null, setItem() {} };
  box.matchMedia = () => ({ matches: false });
  box.getComputedStyle = () => ({ getPropertyValue: () => "" });
  box.requestAnimationFrame = () => 0;
  box.setInterval = () => 0;
  box.setTimeout = () => 0;
  box.addEventListener = () => {};
  box.fetch = () => { throw new Error("no network in the script tests"); };
  vm.createContext(box);
  for (const name of ["charts.js", "app.js"]) {
    vm.runInContext(fs.readFileSync(path.join(webui, name), "utf8"), box, { filename: name });
  }
  return { api: box.__CUM_TEST__.api, charts: box.CUMCharts, elements, box };
}

const out = {};
const { api, charts, elements, box } = load();
const S = api.S;
const NOW = Date.UTC(2026, 9, 21, 16, 12);

function reset() {
  S.data = null; S.stats = {}; S.settings = {}; S.linkOk = null;
}

// ── escaping: titles, folder names and labels come from the user's own files ─────────────────────────────
const hostile = "<img src=x onerror=alert(1)> \"quoted\" 'single' & done";
out.esc = api.esc(hostile);
out.escNull = api.esc(null) + "|" + api.esc(undefined) + "|" + api.esc(0);
out.chartLabelMarkup = charts.hbars([{ label: hostile, value: 1, text: "<b>1</b>", tip: hostile }], { width: 400 });
out.sessionsTable = api.sessionsTable([{
  title: hostile, project: "p\"q<", project_name: "<b>name</b>", cost_usd: 1.5, requests: 3,
  models: ["<m>"], prs: 1, subagent_cost_usd: 0.25,
}]);
out.sessionsTableUntitled = api.sessionsTable([{ title: null, project: "p", project_name: "p", cost_usd: 1, requests: 1, models: [], prs: 0, subagent_cost_usd: 0 }]);

// ── heat maps: each says how many columns it has, so the motion layer can wipe it on without reading its cells ──
{
  const day = (i) => { const d = new Date(2026, 9, 22 - 370 + i); return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"); };
  const days = Array.from({ length: 371 }, (_, i) => ({ date: day(i), value: (i * 37) % 50, tip: "t" }));
  const grid = Array.from({ length: 7 }, (_, r) => Array.from({ length: 24 }, (_, c) => (r * c) % 9));
  const drawn = (svg) => new Set([...svg.matchAll(/ data-c="(\d+)"/g)].map((m) => m[1])).size;       // the columns that hold a cell
  const said = (svg) => { const m = /<svg[^>]* data-cols="(\d+)"/.exec(svg); return m ? Number(m[1]) : null; };
  const calendar = charts.calendar(days, { width: 700, maxCell: 22 });
  const matrix = charts.matrix(grid, { width: 700 });
  out.heatCols = {
    calendar: { said: said(calendar), drawn: drawn(calendar) },
    matrix: { said: said(matrix), drawn: drawn(matrix) },
    empty: said(charts.calendar([], {})),                                             // nothing to draw, nothing to say
    other: [said(charts.hbars([{ label: "a", value: 1, text: "1" }], { width: 400 })), said(charts.columns([1, 2, 3], { width: 300, height: 100 }))],
  };
}

// ── chart labels: cut to the measured width, in the middle, so the end that tells names apart stays ──────────
function labelsOf(rows, width, opts) {
  const svg = charts.hbars(rows.map((l, i) => ({ label: l, value: 10 - i, text: "$20.31 · 52%" })), Object.assign({ width }, opts || {}));
  return [...svg.matchAll(/<text class="cum-t" x="0" y="[\d.]+">([^<]*)<\/text>/g)].map((m) => m[1]);
}
const folders = ["se-infolcd-apex-advanced", "se-infolcd-apex-update", "se-infolcd-apex-server", "SpaceEngineersModSDK", "claude-usage-monitor", "x"];
out.labels = {};
for (const per of [6, 7.9, 9]) {
  pxPerChar = per;
  charts.refresh();
  out.labels[per] = labelsOf(folders, 406);
}
pxPerChar = 6;
charts.refresh();
out.labelsNarrow = labelsOf(folders, 300);
// the bar takes what the label column and the value text leave; the value text is measured, not assumed
const barWidth = () => +charts.hbars([{ label: "a", value: 1, text: "$20.31 · 52%" }], { width: 406, valueWidth: 0 })
  .match(/<rect class="cum-track" x="[\d.]+" y="[\d.]+" width="([\d.]+)"/)[1];
out.barWidth = { six: barWidth() };           // 12 characters of 6 px: the 84 px default is wider than the text
pxPerChar = 10;
out.barWidthStale = barWidth();               // answers are kept until refresh() ...
charts.refresh();
out.barWidthWide = barWidth();                // ... and asked again after it: 12 characters of 10 px
pxPerChar = 6;
charts.refresh();

// ── number formats ────────────────────────────────────────────────────────────────────────────────────────
out.fmtRate = [api.fmtRate(0.87), api.fmtRate(0), api.fmtRate(1), api.fmtRate(null), api.fmtRate(0.8765, 2)];
out.fmtPct = [api.fmtPct(12.4), api.fmtPct(12.6), api.fmtPct(12.345, 1), api.fmtPct(null)];

// ── pace: the band is the one the widgets use ─────────────────────────────────────────────────────────────
function paceAt(delta) {
  const span = 7 * 24 * 3600 * 1000;
  const limit = { percent: 50 + delta, resets_at: new Date(NOW + span / 2).toISOString(), window_seconds: 7 * 24 * 3600 };
  const p = api.paceOf(limit, NOW);
  return [p.kind, Math.round(p.diff * 100) / 100];
}
out.paceBand = api.PACE_BAND;
out.pace = {};
for (const delta of [-11, -10, -7, 0, 7, 9, 10, 11, 30]) out.pace[delta] = paceAt(delta);

// ── model mix: shares are percentages of cost, 0 to 100 ───────────────────────────────────────────────────
reset();
S.data = { state: { status: "live", plan: { name: "Max 5x", monthly_price_usd: 100 }, windows: {} }, model_names: {} };
S.stats["cost?window=cycle"] = {
  key: "cost?window=cycle", name: "cost", url: "", error: "", status: 200, at: Date.now(), errAt: 0, pending: false, digest: "x",
  data: {
    windows: { cycle: { tokens: { input: 10, output: 20, cache_read: 60, cache_write: 30, thinking: 5 }, cost_usd: 40, nocache_cost_usd: 100 },
               today: { cost_usd: 3 }, week: { cost_usd: 9 } },
    value: { multiplier: 0.4, projected_cycle_cost_usd: 80, cycle_start: "2026-10-01T04:00:00Z", cycle_end: "2026-11-01T04:00:00Z" },
    plan: { name: "Max 5x", price_usd: 100 },
    breakdowns: { by_model: [{ model: "claude-opus-5-5", name: "Opus 5.5", share: 0.75 }, { model: "claude-sonnet-5-5", name: "Sonnet 5.5", share: 0.25 }] },
  },
};
const numbers = api.costNumbers();
out.modelShares = numbers.models.map((m) => m.share);
out.modelMixCard = api.modelMixCard(numbers);
out.cacheHit = numbers.hit;
out.multiplier = numbers.multiplier;

// ── real money keeps its own currency ─────────────────────────────────────────────────────────────────────
function spend(currency) {
  return api.spendCardBody({ enabled: true, used: 12.5, limit: 50, percent: 25, currency });
}
out.spend = { EUR: spend("EUR"), GBP: spend("GBP"), USD: spend("USD"), none: spend(null), lower: spend("eur"), bad: spend("EURO"), jpy: spend("JPY") };
out.fmtMoney = [api.fmtMoney(1234.5, "EUR"), api.fmtMoney(null, "EUR"), api.fmtMoney(0, "USD")];
out.fmtUsdUnchanged = [api.fmtUsd(12.5), api.fmtUsd(1234.5)];

// ── the plan multiple: a cell a month, or a month per N cells beyond 24 ───────────────────────────────────
function cells(ratio) {
  const html = api.ratioCells(ratio, "r");
  return {
    count: (html.match(/<i/g) || []).length, on: (html.match(/<i class="on">/g) || []).length,
    part: (html.match(/<i class="part" style="--f:(\d+)%"/) || [])[1] || null, label: html.match(/aria-label="([^"]*)"/)[1],
    scale: api.ratioScale(ratio), note: api.ratioNote(ratio, "Pro"),
  };
}
out.ratio = {};
for (const r of [0, 0.01, 1, 2.3, 23.5, 24, 24.5, 28.15, 30.5, 48, 100, 1000]) out.ratio[r] = cells(r);

// ── data health: the link is "ok" only while it is live ──────────────────────────────────────────────────
const statuses = ["live", "loading", "stale", "offline", "rate_limited", "no_credentials", "paused"];
out.link = {};
for (const status of statuses) {
  reset();
  elements.health = element("health");
  S.data = { state: { status, ingest: { running: false, files_done: 1, files_total: 1 }, unpriced_models: [] } };
  api.renderHealth();
  out.link[status] = (elements.health.innerHTML.match(/<span>LINK<\/span><b class="([^"]*)">/) || [])[1] || null;
}
reset();
elements.health = element("health");
S.data = { state: { status: "live", ingest: null, unpriced_models: [] } };
S.linkOk = false;
api.renderHealth();
out.link.nolink = (elements.health.innerHTML.match(/<span>LINK<\/span><b class="([^"]*)">/) || [])[1] || null;
reset();
elements.health = element("health");
api.renderHealth();
out.link.nostate = (elements.health.innerHTML.match(/<span>LINK<\/span><b class="([^"]*)">/) || [])[1] || null;

// ── the plan price: the override wins where a price is used, the detected price is still known ───────────
reset();
S.data = { state: { status: "live", plan: { name: "Max 5x", monthly_price_usd: 100, rate_limit_tier: "default_claude_max_5x" }, windows: {}, ingest: null, unpriced_models: [] } };
out.price = { detected: [api.effectivePlanPrice(), api.planFromState().price] };
S.settings = { plan_price_override: 50 };
out.price.override = [api.effectivePlanPrice(), api.planFromState().price];
out.price.overrideFallback = api.costNumbers().price;
out.price.pipeline = api.pipelineCard(S.data.state);
S.settings = { plan_price_override: null };
out.price.cleared = [api.effectivePlanPrice(), api.costNumbers().price];

// ── the time left in a window: a bar for the themes that draw one (Night City's Edgerun) ────────────────────
function remainAt(elapsed, windowSeconds) {
  const span = windowSeconds * 1000;
  const limit = { percent: 20, resets_at: new Date(NOW + span * (1 - elapsed)).toISOString(), window_seconds: windowSeconds };
  return api.remainHTML(api.paceOf(limit, NOW));
}
out.remain = {
  fresh: remainAt(0, 5 * 3600), mid: remainAt(0.25, 5 * 3600), week: remainAt(0.5, 7 * 24 * 3600), done: remainAt(1, 5 * 3600),
  none: api.remainHTML(api.paceOf({ percent: 5, resets_at: null, window_seconds: 5 * 3600 }, NOW)),
  card: api.limitCard({ kind: "session", percent: 13, resets_at: new Date(Date.now() + 3.5 * 3600 * 1000).toISOString(), window_seconds: 5 * 3600 }, {}),
};

// ── a page is never empty: the whole card layout from the first frame, each card marked by how its numbers are doing ────
function modelOf(page) {
  const model = api.buildModel(page);
  const cards = [];
  const walk = (parts) => parts.forEach((p) => { if (p.grid) { walk(p.parts); } else if (p.card) { cards.push({ key: p.key, state: p.state, html: p.html }); } });
  walk(model.cards);
  return { cls: model.cls, top: model.top.map((p) => p.key), cards };
}
// the window toolbar of the Cost and Efficiency pages: a part of the page, not a card
function toolbarOf(page) {
  let html = null;
  const walk = (parts) => parts.forEach((p) => { if (p.grid) { walk(p.parts); } else if (p.key === "toolbar") { html = p.html; } });
  walk(api.buildModel(page).cards);
  return html;
}
const PAGE_LIST = ["overview", "limits", "cost", "efficiency", "history"];
const states = (page) => modelOf(page).cards.map((c) => c.key + ":" + c.state);
out.pageKeys = api.PAGE_KEYS;
reset();                                                      // nothing has arrived: no state, no report
out.empty = {};
for (const page of PAGE_LIST) out.empty[page] = modelOf(page);
// the state has arrived and no report has
const liveLimit = (kind, label, percent, scope) => ({ kind, label, percent, severity: "normal", window_seconds: kind === "session" ? 18000 : 604800, resets_at: new Date(NOW + 3 * 3600 * 1000).toISOString(), scope_model: scope || null, allowance_pct: scope ? 50 : null, used_pct_of_weekly: scope ? 0 : null });
reset();
S.data = { state: { status: "live", plan: { name: "Max 5x", monthly_price_usd: 100 }, windows: {}, ingest: null, unpriced_models: [],
  usage: { limits: [liveLimit("session", "SESSION", 13), liveLimit("weekly_all", "WEEKLY", 2), liveLimit("weekly_scoped", "SCOPED", 0, "Fable")], breakdown: [], spend: null } }, model_names: {} };
out.stateOnly = {};
for (const page of PAGE_LIST) out.stateOnly[page] = states(page);
// a report failed and there is nothing to show: its cards say so, each in its own frame, and the other cards stand
const failed = (name, params, status) => { const e = api.statEntry(name, params); e.error = "HTTP " + status; e.status = status; e.errAt = 1; return e; };
reset();
failed("efficiency", { window: "cycle" }, 503);
out.failed = { efficiency: modelOf("efficiency") };
reset();
failed("cost", { window: "cycle" }, 500);
out.failed.cost = modelOf("cost");
// the toolbar note beside the window buttons is a placeholder while the report is on its way, and settled text once it failed
out.toolbar = { costFailed: toolbarOf("cost") };
reset();
failed("efficiency", { window: "cycle" }, 500);
out.toolbar.efficiencyFailed = toolbarOf("efficiency");
reset();
out.toolbar.costWaiting = toolbarOf("cost");
out.toolbar.efficiencyWaiting = toolbarOf("efficiency");
// the reports are in
reset();
const filled = (name, params, data) => { const e = api.statEntry(name, params); e.data = data; e.digest = "d"; e.at = Date.now(); return e; };
filled("efficiency", { window: "cycle" }, api.skelEfficiency());
filled("history", {}, api.skelHistory());
out.arrived = { efficiency: states("efficiency"), history: states("history") };
// a window that has not come yet shows the last window's numbers where they do not depend on the window
reset();
S.ui.costWindow = "cycle";
filled("cost", { window: "cycle" }, api.skelCost());
S.ui.costWindow = "week";
out.lastKnown = states("cost");
S.ui.costWindow = "cycle";

// ── the Efficiency page, under its old name too ────────────────────────────────────────────────────────────
box.location.hash = "#nerds";
out.alias = api.currentPage();
box.location.hash = "#efficiency";
out.aliasNew = api.currentPage();
box.location.hash = "#nowhere";
out.aliasUnknown = api.currentPage();
box.location.hash = "";
box.localStorage = { getItem: () => JSON.stringify({ nerdsWindow: "week", costWindow: "month" }), setItem() {} };
api.loadPrefs();
out.legacyPrefs = [S.ui.efficiencyWindow, S.ui.costWindow];
box.localStorage = { getItem: () => JSON.stringify({ efficiencyWindow: "today", nerdsWindow: "week" }), setItem() {} };
api.loadPrefs();
out.newPrefsWin = S.ui.efficiencyWindow;
S.ui.efficiencyWindow = "cycle"; S.ui.costWindow = "cycle";
box.localStorage = { getItem: () => null, setItem() {} };

// the compaction figure and the largest request are different counters, and say so
reset();
filled("efficiency", { window: "cycle" }, api.skelEfficiency());
out.counters = modelOf("efficiency").cards.filter((c) => c.key === "compactions" || c.key === "context").map((c) => c.html);
reset();
S.data = { state: { status: "live", plan: { name: "Max 5x", monthly_price_usd: 100, rate_limit_tier: "default_claude_max_5x" }, ingest: null, unpriced_models: [] }, model_names: {} };
out.pipeline = api.pipelineCard(S.data.state);

// ── the update banner ──────────────────────────────────────────────────────────────────────────────────────
const RELEASE = "https://github.com/Godimas101/claude-usage-monitor/releases/tag/v2.1.0";
function bannerFor(update) {
  reset();
  S.skipped = "";
  S.data = { state: { status: "live" }, model_names: {}, update };
  return { found: api.pendingUpdate(), html: api.updateBanner() };
}
out.banner = {
  none: bannerFor({ available: false, version: "", url: "", can_self_update: false, skipped: false, info: null, skip_version: "" }),
  noBlock: bannerFor(undefined),
  available: bannerFor({ available: true, version: "2.1.0", url: RELEASE, can_self_update: true, skipped: false, info: null, skip_version: "" }),
  browser: bannerFor({ available: true, version: "2.1", url: RELEASE, can_self_update: false, skipped: false, info: null, skip_version: "" }),
  skipped: bannerFor({ available: true, version: "2.1.0", url: RELEASE, can_self_update: true, skipped: true, info: null, skip_version: "2.1.0" }),
  skippedByVersion: bannerFor({ available: true, version: "2.1.0", url: RELEASE, can_self_update: true, skipped: false, info: null, skip_version: "2.1.0" }),
  newerThanSkipped: bannerFor({ available: true, version: "2.2.0", url: RELEASE, can_self_update: true, skipped: false, info: null, skip_version: "2.1.0" }),
  fromInfo: bannerFor({ available: true, info: { version: "3.0", url: RELEASE, can_self_update: true } }),
  hostileVersion: bannerFor({ available: true, version: "<img src=x onerror=alert(1)>", url: RELEASE, can_self_update: true }),
  foreignUrl: bannerFor({ available: true, version: "2.1.0", url: "https://example.com/evil", can_self_update: true }),
};
// skipped in this window a moment ago, before the state has caught up
reset();
S.data = { state: { status: "live" }, model_names: {}, update: { available: true, version: "2.1.0", url: RELEASE, can_self_update: true, skipped: false, skip_version: "" } };
S.skipped = "2.1.0";
out.banner.skippedHere = api.updateBanner();
S.skipped = "";
S.update = { text: "THIS VERSION WILL BE SKIPPED.", version: "2.0.9" };
out.banner.staleStatus = api.updateBanner();
S.update = { text: "" };

// ── the theme picker shows one swatch per accent ───────────────────────────────────────────────────────────
const accents = ["system", "blue", "teal", "purple", "rose", "orange", "green"];
const gatesVariants = [];
for (const mode of ["dark", "light"]) {
  accents.forEach((accent, i) => gatesVariants.push({ id: accent + "-" + mode, name: accent, accent, mode, colors: { accent: "#" + (0x100000 * (i + 1) + (mode === "dark" ? 0x1111 : 0x2222)).toString(16).padStart(6, "0") } }));
}
out.swatches = {
  gatesDark: api.themeSwatches({ id: "gates", variants: gatesVariants }, { id: "gates", mode: "dark" }),
  gatesLight: api.themeSwatches({ id: "gates", variants: gatesVariants }, { id: "gates", mode: "light" }),
  gatesOther: api.themeSwatches({ id: "gates", variants: gatesVariants }, { id: "nostromo", mode: "" }),
  nostromo: api.themeSwatches({ id: "nostromo", variants: ["amber", "green", "white", "red", "blue", "cyan", "orange", "purple"].map((id, i) => ({ id, accent: id, colors: { accent: "#" + (0x200000 * (i + 1)).toString(16).padStart(6, "0") } })) }, null),
  nightCity: api.themeSwatches({ id: "night_city", variants: ["blackwall", "relic", "edgerun", "mox"].map((id, i) => ({ id, colors: { accent: "#" + (0x300000 * (i + 1)).toString(16).padStart(6, "0") } })) }, null),
};

// ── the forecast of a weekly limit: from now at the average rate so far ─────────────────────────────────────────
const HOUR = 3600 * 1000;
const reset7 = NOW + 100 * HOUR;
const blockOf = (percent, end, resets) => ({ percent, projected_end_pct: end, resets_at: new Date(resets === undefined ? reset7 : resets).toISOString() });
out.forecast = {
  below: api.averageRateForecast(blockOf(10, 40), NOW),
  crossing: api.averageRateForecast(blockOf(60, 120), NOW),
  exact: api.averageRateForecast(blockOf(50, 100), NOW),
  reached: api.averageRateForecast(blockOf(100, 130), NOW),
  noProjection: api.averageRateForecast({ percent: 10, projected_end_pct: null, resets_at: new Date(reset7).toISOString() }, NOW),
  over: api.averageRateForecast(blockOf(10, 40, NOW - HOUR), NOW),
  expectCross: NOW + (100 - 60) / (120 - 60) * (reset7 - NOW),
  reset: reset7,
};
function weeklyCard(percent, end) {
  const limit = { kind: "weekly_all", label: "WEEKLY", percent, severity: "normal", window_seconds: 7 * 24 * 3600, resets_at: new Date(reset7).toISOString() };
  const blk = Object.assign(blockOf(percent, end), { window_start: new Date(reset7 - 7 * 24 * HOUR).toISOString(), points: [[new Date(NOW - 20 * HOUR).toISOString(), percent / 2], [new Date(NOW - HOUR).toISOString(), percent]], verdict: "AHEAD", pace_delta_pp: 5, daily_budget_pct: 10, even_daily_pct: 14.3, slope_pct_per_hour: 0.1 });
  S.nextCharts = {};
  const host = api.pacingChart(blk, "weekly", NOW, "d", false);
  const spec = S.nextCharts[/data-chart="([^"]+)"/.exec(host)[1]];
  return { svg: spec.build(480), facts: api.limitFacts(limit, blk, "weekly", api.paceOf(limit, NOW), false) };
}
reset();
const crossing = weeklyCard(60, 120.4);
const staying = weeklyCard(10, 40);
out.forecastCard = {
  crossingLabel: (/100% ~ ([^<]+)</.exec(crossing.svg) || [])[1] || null, crossingClock: api.clockText(NOW + (100 - 60) / (120.4 - 60) * (reset7 - NOW)),
  crossingTip: (/aria-label="(Reaches 100% about [^"]+)"/.exec(crossing.svg) || [])[1] || null,
  crossingFacts: [/Projected end<\/span><b>([^<]+)</.exec(crossing.facts)[1], (/Limit reached<\/span><b>([^<]+)</.exec(crossing.facts) || [])[1] || null],
  stayingLabel: /100% ~/.test(staying.svg), stayingFacts: [/Projected end<\/span><b>([^<]+)</.exec(staying.facts)[1], /Limit reached/.test(staying.facts)],
  stayingForecastDrawn: /class="cum-fore"/.test(staying.svg), crossingForecastDrawn: /class="cum-fore"/.test(crossing.svg),
};

// ── Top sessions: the folder column is as wide as the longest folder name needs ─────────────────────────────
const folderColumn = (names) => (/<col class="c-folder" style="width:(\d+)%">/.exec(api.sessionsTable(names.map((n) => ({ title: "t", project: n, project_name: n, cost_usd: 1, requests: 1, models: [], prs: 0, subagent_cost_usd: 0 })))) || [])[1];
out.folderColumn = { short: folderColumn(["x", "api"]), usual: folderColumn(["claude-usage-monitor", "x"]), long: folderColumn(["a-folder-name-that-is-much-longer-than-any-table-has-room-for"]) };

// ── the Fast mode card says the same thing before its report, with no fast-mode request and with some: only its tone changes ──
// (a note that said something else per state wrapped to another line count, and the card grew when the report landed)
const cardHtml = (page, key) => { const c = modelOf(page).cards.filter((x) => x.key === key)[0]; return c ? { state: c.state, html: c.html } : null; };
const costWith = (fast) => { const d = api.skelCost(); d.breakdowns.fast = fast; return d; };
reset();
S.ui.costWindow = "cycle";
out.fast = { pending: cardHtml("cost", "fast") };
reset();
filled("cost", { window: "cycle" }, costWith({ requests: 0, cost_usd: 0 }));
out.fast.none = cardHtml("cost", "fast");
reset();
filled("cost", { window: "cycle" }, costWith({ requests: 29, cost_usd: 15.44 }));
out.fast.some = cardHtml("cost", "fast");
// the placeholder threads carry a typical request count, so their tiles wrap like the real ones
reset();
out.threadsPending = cardHtml("cost", "threads");

// ── the Overview says so when the cost report failed and the monitor has no month of its own to stand in ────────────────────
const overviewPart = (key) => { const p = api.buildModel("overview").top.filter((x) => x.key === key)[0]; return p ? p.html : null; };
const overviewState = (settings, windows, entry) => {
  reset();
  S.settings = settings;
  S.data = { state: { status: "live", plan: { name: "Max 5x", monthly_price_usd: 100 }, windows, ingest: null, unpriced_models: [],
    usage: { limits: [liveLimit("session", "SESSION", 13), liveLimit("weekly_all", "WEEKLY", 2)], breakdown: [], spend: null } }, model_names: {} };
  if (entry) { failed("cost", { window: "cycle" }, entry); }
  const cards = modelOf("overview").cards.filter((c) => ["cost", "efficiency", "models"].indexOf(c.key) >= 0);
  return { states: cards.map((c) => c.key + ":" + c.state), stillPending: cards.some((c) => /data-pending/.test(c.html)), errorLines: cards.filter((c) => /class="nm-error"/.test(c.html)).length, problem: overviewPart("problem") };
};
const MONTH = { month: { totals: { cost_usd: 40, caching_saved_usd: 5, cache_hit_rate: 0.5, output: 20, thinking: 5 }, by_model: { "claude-opus-5-5": { cost_usd: 40, total_tokens: 100 } } },
  today: { totals: { cost_usd: 3 } }, week: { totals: { cost_usd: 9 } } };
out.overviewCost = {
  failedBilling: overviewState({ billing_day: 5 }, MONTH, 500),                 // a billing day: the month is not the cycle, so nothing stands in
  failedNoMonth: overviewState({ billing_day: null }, {}, 500),                 // no month in the state either
  failed501: overviewState({ billing_day: 5 }, {}, 501),
  waiting: overviewState({ billing_day: 5 }, {}, null),                         // still on its way: placeholders, no banner
  fallback: overviewState({ billing_day: null }, MONTH, 500),                   // the monitor's own month stands in: numbers, no banner
};

// ── the update banner is on every page the page model draws, not only the Overview ──────────────────────────────────────────
const UPDATE = { available: true, version: "2.1.0", url: RELEASE, can_self_update: true, skipped: false, info: null, skip_version: "" };
function bannerOn(page, update) {
  reset();
  S.skipped = "";
  S.data = { state: { status: "live", plan: { name: "Max 5x", monthly_price_usd: 100 }, windows: {}, ingest: null, unpriced_models: [] }, model_names: {}, update };
  return (api.buildModel(page).top.filter((x) => x.key === "update")[0] || {}).html;
}
out.bannerPages = {};
for (const page of PAGE_LIST) out.bannerPages[page] = { shown: bannerOn(page, UPDATE), none: bannerOn(page, undefined), skipped: bannerOn(page, Object.assign({}, UPDATE, { skipped: true })) };
// the About page is built once, so its banner slot is kept in step by hand
function aboutBanner(update) {
  reset();
  S.skipped = "";
  S.formKey = null;
  S.supportersRequested = true;
  S.data = { state: { status: "live" }, model_names: {}, update, version: "2.0.0" };
  const slot = element("about-update");
  const main = element("main");
  main.querySelector = (sel) => (sel === "#about-update" ? slot : null);
  api.aboutPage(main);
  const first = slot.innerHTML;
  api.aboutPage(main);                                                           // a second render with the same state leaves the slot alone
  return { html: first, sig: slot.getAttribute("data-sig"), again: slot.innerHTML === first, hasSlot: main.innerHTML.indexOf('id="about-update"') >= 0 };
}
out.bannerAbout = { shown: aboutBanner(UPDATE), none: aboutBanner(undefined) };
S.formKey = null;

// ── a status line written in capitals is capitals all the way: the Gates theme sets a line in sentence case only when all of it is ──
// (isCaps is gates.js's own test). The monitor's reason is lower case ("update apply failed"), so each failure path has to raise it.
const isCaps = (text) => /[A-Z]/.test(text) && !/[a-z]/.test(text);
const answer = (status, body) => () => Promise.resolve({ ok: status >= 200 && status < 300, status, headers: { get: () => null }, text: () => Promise.resolve(body === undefined ? "" : JSON.stringify(body)) });
const settle = async () => { for (let i = 0; i < 12; i++) { await new Promise((resolve) => setImmediate(resolve)); } };
const mainElement = element("main");
const savedQuery = box.document.querySelector;
const click = (action) => ({ target: { closest: () => ({ hasAttribute: () => false, getAttribute: (name) => (name === "data-action" ? action : null) }) } });

async function failures() {
  box.document.querySelector = (selector) => (selector === "#main" ? mainElement : null);
  box.clearTimeout = () => {};                                                  // poll() reschedules itself
  const lines = {};
  // the monitor answers a server error with its lower-case reason, and an unreadable body with only the status
  for (const [label, reply] of [["reason", answer(503, { error: "update apply failed" })], ["status", answer(502)]]) {
    box.fetch = reply;
    reset();
    S.data = { state: { status: "live" }, model_names: {}, update: UPDATE };
    api.settingsClick(click("refresh"));
    await settle();
    const refresh = S.status.text;
    api.settingsClick(click("check-update"));
    await settle();
    const check = S.update.text;
    api.updateAction("update-now");
    await settle();
    const install = S.update.text;
    api.updateAction("update-skip");
    await settle();
    const skip = S.update.text;
    lines[label] = { refresh, check, install, skip };
  }
  // the dashboard losing its link to the monitor
  box.fetch = () => Promise.reject(new Error("link lost"));
  reset();
  S.data = { state: { status: "live" }, model_names: {}, update: UPDATE };
  api.settingsClick(click("refresh"));
  await settle();
  const lost = { refresh: S.status.text };
  api.settingsClick(click("check-update"));
  await settle();
  lost.check = S.update.text;
  lines.lost = lost;
  // "Check for updates now" when it worked: a lookup that could not be made is not "up to date"
  const checkText = async (result) => {
    box.fetch = answer(200, { ok: true, result });
    reset();
    api.settingsClick(click("check-update"));
    await settle();
    return S.update.text;
  };
  out.checkText = {
    upToDate: await checkText({ available: false, version: null, checked: true }),
    notChecked: await checkText({ available: false, version: null, checked: false }),
    foundBefore: await checkText({ available: true, version: "2.1.0", checked: false }),
    found: await checkText({ available: true, version: "2.1.0", checked: true }),
    oldMonitor: await checkText({ available: false, version: null }),                // a monitor that sends no `checked` looked, as before
    noResult: await checkText(undefined),
  };
  out.failureLines = lines;
  out.failureLinesCaps = Object.keys(lines).every((label) => Object.keys(lines[label]).every((name) => isCaps(lines[label][name])));
  box.document.querySelector = savedQuery;
  box.fetch = () => { throw new Error("no network in the script tests"); };
  reset();
}

failures().then(() => console.log(JSON.stringify(out)), (error) => { console.error(error && error.stack ? error.stack : String(error)); process.exit(1); });
