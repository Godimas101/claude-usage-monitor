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

// ── the sign-in help and the pages of a PC with no Claude Code (#28) ─────────────────────────────────────────────────────────
// A sign-in that is missing or has expired gets the help panel in place of the plain status line, on every page. A PC whose records
// the monitor has read and found empty (someone who uses Claude on the web or in the app) has nothing for the cost, efficiency and
// history pages to count. Every state here is made up; the page is as it starts (nothing decided, nothing being checked).
const SIGN_UPDATED = "2026-10-21T16:11:48.000Z";
const SIGN_LATER = "2026-10-21T16:12:05.000Z";
const NO_SIGN_IN = "No Claude sign-in found; open Claude Code to sign in";
const ingestOf = (patch) => Object.assign({ running: false, files_done: 0, files_total: 0, last_duration_s: 0.01, last_error: null }, patch);
const requestsIn = (n) => ({ all: { name: "all", start: null, end: null, totals: { requests: n } } });
function forget() {
  reset();
  S.noActivity = false;
  S.copy = "";
  S.check = { busy: false, sent: false, from: "", at: 0, still: false, failed: false };
  S.themesRequested = true;                                       // no /api/themes ask in the recorded calls
}
function monitorState(status, patch) {
  forget();
  S.data = { state: Object.assign({
    status, status_detail: NO_SIGN_IN, plan: { name: "Max 5x", monthly_price_usd: 100 }, windows: requestsIn(0), ingest: ingestOf({}),
    unpriced_models: [], updated_at: SIGN_UPDATED,
    usage: { limits: [liveLimit("session", "SESSION", 13), liveLimit("weekly_all", "WEEKLY", 2)], breakdown: [], spend: null },
  }, patch || {}), model_names: {}, settings: {} };
}
const topPart = (page, key) => (api.buildModel(page).top.filter((x) => x.key === key)[0] || {}).html;
out.signIn = { banner: {}, onPages: {} };
for (const status of statuses) {
  monitorState(status, { status_detail: "detail of " + status });
  out.signIn.banner[status] = api.statusBanner();
}
for (const page of PAGE_LIST) {
  monitorState("no_credentials");
  out.signIn.onPages[page] = topPart(page, "status");
}
monitorState("stale", { status_detail: "Sign-in token expired; open Claude Code to refresh it" });
out.signIn.expired = api.statusBanner();
monitorState("stale", { status_detail: hostile });
out.signIn.hostile = api.statusBanner();
monitorState("stale", { status_detail: "" });
out.signIn.noDetail = api.statusBanner();

// limits only: decided from the all-time window once an ingest is done, held through the ingests that follow
function decided(patch, before) {
  monitorState("live", patch);
  S.noActivity = !!before;
  return api.limitsOnly();
}
const reading = ingestOf({ running: true, files_done: 3, files_total: 40 });
out.limitsOnly = {
  noState: (forget(), api.limitsOnly()),
  noIngest: decided({ ingest: null }),
  firstIngestRunning: decided({ ingest: ingestOf({ running: true, files_done: 3, files_total: 40, last_duration_s: null }) }),
  firstIngestFailed: decided({ ingest: ingestOf({ last_duration_s: null, last_error: "RuntimeError: boom" }) }),
  done: decided({}),
  doneNoTotals: decided({ windows: { all: {} } }),
  noWindow: decided({ windows: {} }),
  withRequests: decided({ windows: requestsIn(7) }),
  laterIngestHolds: decided({ ingest: reading }, true),
  laterIngestFromNothing: decided({ ingest: reading }, false),
  aRequestEndsIt: decided({ windows: requestsIn(3), ingest: reading }, true),
};

// the pages of such a PC: three of them are one card, the Overview keeps its limit, surface and burn cards, nothing is asked for
monitorState("live");
out.noRecords = { pages: {}, stats: {}, top: {} };
for (const page of ["cost", "efficiency", "history"]) {
  out.noRecords.pages[page] = modelOf(page);
  out.noRecords.top[page] = { problem: topPart(page, "problem"), stale: topPart(page, "stale") };
}
for (const page of PAGE_LIST) out.noRecords.stats[page] = api.pageStats(page).map((e) => e.key);
out.noRecords.overview = modelOf("overview");
out.noRecords.limits = states("limits");
// a failed cost report is nothing to report when nothing is counted
monitorState("live");
failed("cost", { window: "cycle" }, 500);
out.noRecords.failedReport = { states: states("overview"), problem: topPart("overview", "problem"), stale: topPart("overview", "stale") };
// a PC with Claude Code records, and a first ingest that is still running: the pages are what they were
monitorState("live", { windows: requestsIn(9800) });
out.records = { overview: states("overview"), cost: states("cost"), efficiency: states("efficiency"), history: states("history"), stats: api.pageStats("cost").map((e) => e.key) };
monitorState("live", { ingest: ingestOf({ running: true, files_done: 3, files_total: 40, last_duration_s: null }) });
out.firstIngest = { overview: states("overview"), cost: states("cost"), efficiency: states("efficiency"), history: states("history") };
forget();

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

// ── the usage guard card: its markup, the status block in every state, the setup block, the fields and the clicks ──────────────
// Every input is made up. The page asks /api/guard, which answers { guard, told, waiting, setup }.
const GUARD_T = Date.UTC(2026, 9, 21, 16, 12);
const isoAt = (ms) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z");
const POLICY = { enabled: true, session_cap: 95, weekly_cap: 90, scoped_cap: null, stagger_s: 120 };
const RESETS = isoAt(GUARD_T + 3 * HOUR + 27 * 60000);
const SESSION_OVER = { name: "session", label: "session", percent: 96, cap: 95, resets_at: RESETS };
const FABLE_OVER = { name: "Fable weekly", label: "Fable weekly", percent: 91.5, cap: 90, resets_at: RESETS };
const toldN = (n) => Array.from({ length: n }, (_, i) => ({ session_id: "3f9a1c2e-" + i + "aaa-bbbb", told_at: isoAt(GUARD_T - (30 - i) * 60000) }));
const waiterOf = (id, cwd, pid) => ({ pid: pid || 4242, session_id: id, since: isoAt(GUARD_T - 18 * 60000), cwd });
const guardAnswer = (guard, told, waiting, setup) => ({ guard, told: told || [], waiting: waiting || [], setup: setup || null });
const holding = (over, extra) => Object.assign({ state: "holding", over, resume_at: RESETS, policy: POLICY }, extra || {});
const SETUP_OK = {
  available: true, reason: null, manual: "docs/AGENT_HOOK.md", exe: "C:/x/ClaudeUsageMonitor.exe", settings_path: "C:/Users/t/.claude/settings.json", errors: [],
  hooks: { state: "missing", installed: 0, total: 3, events: {} }, mcp: { state: "missing" },
  claude: { found: true, path: "C:/c/claude.exe", version: "2.1.160", min_version: "2.1.139", supported: true },
};
const setupWith = (patch) => Object.assign({}, SETUP_OK, patch);

function liveFor(answer, opts) {
  reset();
  S.guard = answer;
  S.guardLost = !!(opts && opts.lost);
  S.resumeBusy = !!(opts && opts.busy);
  const html = api.guardLiveHtml(api.guardView(), (opts && opts.now) || GUARD_T);
  S.resumeBusy = false;
  return html;
}
function setupFor(setup, result, busy) {
  reset();
  S.guard = setup ? guardAnswer({}, [], [], setup) : null;
  S.setup = { busy: busy || "", result: result || null };
  const html = api.guardSetupHtml(api.guardView());
  S.setup = { busy: "", result: null };
  return html;
}
const done = (patch) => Object.assign({ ok: true, action: "install", changed: [], unchanged: [], backup: null, warnings: [], errors: [], manual: null }, patch);
const manyWaiting = Array.from({ length: 7 }, (_, i) => waiterOf("77b0aa1" + i + "-9d3e", "C:\\Demo\\project-" + i, 4000 + i));

out.guard = {
  card: api.guardCard(),
  live: {
    pending: liveFor(null),
    lost: liveFor(null, { lost: true }),
    beforeTheFirstCycle: liveFor(guardAnswer({})),
    off: liveFor(guardAnswer({ state: "off", over: [], resume_at: null, policy: Object.assign({}, POLICY, { enabled: false }) })),
    armed: liveFor(guardAnswer({ state: "armed", over: [], resume_at: null, policy: POLICY })),
    armedThree: liveFor(guardAnswer({ state: "armed", over: [], resume_at: null, policy: Object.assign({}, POLICY, { scoped_cap: 80 }) })),
    armedNoCaps: liveFor(guardAnswer({ state: "armed", over: [], resume_at: null, policy: { enabled: true, session_cap: null, weekly_cap: null, scoped_cap: null, stagger_s: 120 } })),
    armedStillWaiting: liveFor(guardAnswer({ state: "armed", over: [], resume_at: null, policy: POLICY }, [], [waiterOf("77b0aa10-9d3e", "C:\\Demo\\atlas-web")])),
    holding: liveFor(guardAnswer(holding([SESSION_OVER, FABLE_OVER]), toldN(2), [waiterOf("77b0aa10-9d3e", "C:\\Demo\\atlas-web")])),
    holdingBusy: liveFor(guardAnswer(holding([SESSION_OVER]), toldN(1), [waiterOf("77b0aa10-9d3e", "C:\\Demo\\atlas-web")]), { busy: true }),
    holdingNobody: liveFor(guardAnswer(holding([SESSION_OVER]))),
    holdingNoResetTime: liveFor(guardAnswer(holding([SESSION_OVER], { resume_at: null }), toldN(1))),
    holdingAMomentLeft: liveFor(guardAnswer(holding([SESSION_OVER]), toldN(1)), { now: Date.parse(RESETS) - 20000 }),
    holdingMany: liveFor(guardAnswer(holding([SESSION_OVER]), toldN(7), manyWaiting)),
    holdingHostile: liveFor(guardAnswer(holding([{ name: hostile, label: hostile, percent: 96, cap: 95, resets_at: RESETS }]),
      [{ session_id: hostile, told_at: isoAt(GUARD_T) }], [waiterOf(hostile, "C:\\<b>x</b>\\" + hostile)])),
    odd: liveFor({ guard: "x", told: "y", waiting: 3, setup: [] }),
  },
  clock: { resumes: api.clockText(Date.parse(RESETS)), told: api.clockText(GUARD_T - 30 * 60000), since: api.clockText(GUARD_T - 18 * 60000) },
  setup: {
    pending: setupFor(null),
    missing: setupFor(SETUP_OK),
    installed: setupFor(setupWith({ hooks: { state: "installed", installed: 3, total: 3, events: {} }, mcp: { state: "registered" } })),
    partial: setupFor(setupWith({ hooks: { state: "partial", installed: 2, total: 3, events: {} }, mcp: { state: "other" } })),
    tooOld: setupFor(setupWith({ claude: { found: true, version: "2.1.94", min_version: "2.1.139", supported: false } })),
    versionUnknown: setupFor(setupWith({ claude: { found: true, version: null, min_version: "2.1.139", supported: null } })),
    noClaude: setupFor(setupWith({ available: false, reason: "Claude Code was not found on this PC: there is no claude command on the PATH.", claude: { found: false, version: null, min_version: "2.1.139", supported: null } })),
    fromSource: setupFor(setupWith({ available: false, reason: "One-click setup works in the installed app. This copy runs from source.", hooks: { state: "unknown", installed: 0, total: 3, events: {} }, mcp: { state: "unknown" }, claude: { found: false, version: null, min_version: "2.1.139", supported: null } })),
    unreadable: setupFor(setupWith({ errors: ["The Claude Code settings file is not valid JSON, so setup left it alone."], hooks: { state: "unknown", installed: 0, total: 3, events: {} } })),
    installing: setupFor(SETUP_OK, null, "install"),
    removing: setupFor(SETUP_OK, null, "remove"),
    installed_ok: setupFor(SETUP_OK, done({ changed: ["Added the SessionStart hook.", "Registered the claude-usage MCP tool."], backup: "C:\\Users\\t\\.claude\\settings.json.cum-backup-20261028-180509", warnings: ["The VS Code extension has its own Claude Code version; it also needs 2.1.139 or newer."] })),
    nothing: setupFor(SETUP_OK, done({ unchanged: ["The three hooks were already in place."] })),
    removed_ok: setupFor(SETUP_OK, done({ action: "remove", changed: ["Removed the PostToolUse hook."], backup: "C:\\b" })),
    refused: setupFor(SETUP_OK, done({ ok: false, errors: ["Claude Code 2.1.94 is older than 2.1.139, which hooks need, so setup changed nothing. Update it with: claude update"] })),
    byHand: setupFor(SETUP_OK, done({ changed: ["Added the SessionStart hook."], warnings: ["The MCP tool was not registered."], manual: "claude mcp add --transport stdio --scope user claude-usage '--' 'C:\\Users\\A&B\\x.exe' --mcp" })),
    hostile: setupFor(setupWith({ available: false, reason: hostile }), done({ ok: false, changed: [hostile], unchanged: [hostile], backup: hostile, warnings: [hostile], errors: [hostile], manual: hostile })),
  },
  labels: ["guard_enabled", "guard_session_cap", "guard_weekly_cap", "guard_scoped_cap", "guard_resume_stagger_s", "poll_interval_ms"].map((key) => api.labelFor(key)),
};

// the two blocks are rewritten only when their markup changed, so a poll never drops the focus of someone tabbing through them
{
  reset();
  const live = element("guard-live");
  const setupBox = element("guard-setup");
  const main = element("main");
  let writes = 0;
  for (const box2 of [live, setupBox]) {
    let held = "";
    Object.defineProperty(box2, "innerHTML", { get: () => held, set: (v) => { writes += 1; held = v; } });
  }
  main.querySelector = (selector) => (selector === "#guard-live" ? live : selector === "#guard-setup" ? setupBox : null);
  S.guard = guardAnswer(holding([SESSION_OVER]), toldN(1), [waiterOf("77b0aa10-9d3e", "C:\\Demo\\atlas-web")]);
  api.syncGuard(main);
  const first = writes;
  api.syncGuard(main);
  api.syncGuard(main);
  const unchanged = writes;
  S.guard = guardAnswer(holding([SESSION_OVER]), toldN(1), []);
  api.syncGuard(main);
  out.guard.sync = { first, unchanged, afterOneChange: writes, liveHasButtonDisabled: /data-action="guard-resume"[^>]* disabled/.test(live.innerHTML) };
  S.guard = null;
}

// the tick: the Settings page asks again every few seconds while it is on screen and visible, one ask at a time
async function guardPolling() {
  const saved = { query: box.document.querySelector, visible: box.document.visibilityState };
  const asked = [];
  box.document.querySelector = () => null;
  box.fetch = (url, init) => { asked.push((init && init.method) + " " + url); return answer(200, guardAnswer({})) (); };
  const tick = async (page, form, visibility, ago, flight) => {
    reset();
    S.page = page; S.formKey = form; S.guardFlight = !!flight; S.guardAt = Date.now() - ago;
    box.document.visibilityState = visibility;
    asked.length = 0;
    api.guardTick();
    await settle();
    return asked.slice();
  };
  out.guard.polling = {
    due: await tick("settings", "settings", "visible", 4000),
    notYet: await tick("settings", "settings", "visible", 1000),
    inFlight: await tick("settings", "settings", "visible", 4000, true),
    elsewhere: await tick("overview", null, "visible", 4000),
    hidden: await tick("settings", "settings", "hidden", 4000),
    pageNotBuilt: await tick("settings", null, "visible", 4000),
  };
  box.document.querySelector = saved.query;
  box.document.visibilityState = saved.visible;
  box.fetch = () => { throw new Error("no network in the script tests"); };
  reset();
}

// the fields and the clicks, against a recording monitor
async function guardActions() {
  const saved = box.document.querySelector;
  const main = element("main");
  const live = element("guard-live");
  const setupBox = element("guard-setup");
  main.querySelector = (selector) => (selector === "#guard-live" ? live : selector === "#guard-setup" ? setupBox : null);
  box.document.querySelector = (selector) => (selector === "#main" ? main : null);
  box.clearTimeout = () => {};
  const calls = [];
  const run = async (table, act) => {
    calls.length = 0;
    box.fetch = (url, init) => {
      const key = ((init && init.method) || "GET") + " " + url;
      calls.push({ key, body: init && init.body ? JSON.parse(init.body) : null });
      const hit = table[key] || [404, { error: "not in this test" }];
      return hit === "lost" ? Promise.reject(new Error("link lost")) : answer(hit[0], hit[1])();
    };
    act();
    await settle();
    return calls.map((c) => c.key + (c.body ? " " + JSON.stringify(c.body) : ""));
  };
  const GOOD = [200, guardAnswer(holding([SESSION_OVER]), toldN(2), [waiterOf("77b0aa10-9d3e", "C:\\Demo\\atlas-web")], SETUP_OK)];
  const fresh = () => { reset(); S.page = "settings"; S.formKey = "settings"; S.setup = { busy: "", result: null }; S.guard = null; S.resumeBusy = false; S.guardFlight = false; S.status = { text: "", error: false }; };
  const actions = {};

  fresh();
  actions.resume = { calls: await run({ "POST /api/guard/resume": [200, { ok: true }], "GET /api/guard": GOOD }, () => api.settingsClick(click("guard-resume"))), status: S.status, busy: S.resumeBusy, learned: !!S.guard };
  fresh();
  actions.resumeRefused = { calls: await run({ "POST /api/guard/resume": [500, { error: "could not record the resume request" }], "GET /api/guard": GOOD }, () => api.settingsClick(click("guard-resume"))), status: S.status, busy: S.resumeBusy };
  fresh();
  actions.resumeLost = { calls: await run({ "POST /api/guard/resume": "lost" }, () => api.settingsClick(click("guard-resume"))), status: S.status, busy: S.resumeBusy };
  fresh();
  S.resumeBusy = true;                                    // a click while one is on its way does nothing
  actions.resumeTwice = await run({}, () => api.settingsClick(click("guard-resume")));
  S.resumeBusy = false;

  const RESULT = done({ changed: ["Added the SessionStart hook."], backup: "C:\\b", warnings: ["w"] });
  fresh();
  const installing = await run({ "POST /api/guard/setup": [200, RESULT], "GET /api/guard": GOOD }, () => { api.settingsClick(click("guard-install")); actions.whileRunning = { busy: S.setup.busy, live: setupBox.innerHTML }; });
  actions.install = { calls: installing, result: S.setup.result, busy: S.setup.busy, shown: setupBox.innerHTML };
  fresh();
  actions.remove = { calls: await run({ "POST /api/guard/setup": [200, done({ action: "remove", changed: ["Removed the SessionStart hook."] })], "GET /api/guard": GOOD }, () => api.settingsClick(click("guard-remove"))), result: S.setup.result };
  fresh();
  actions.refusedByTheMonitor = { calls: await run({ "POST /api/guard/setup": [200, done({ ok: false, errors: ["not valid JSON"] })], "GET /api/guard": GOOD }, () => api.settingsClick(click("guard-install"))), result: S.setup.result };
  fresh();
  actions.serverError = { calls: await run({ "POST /api/guard/setup": [500, { error: "setup install failed" }], "GET /api/guard": GOOD }, () => api.settingsClick(click("guard-install"))), result: S.setup.result };
  fresh();
  actions.setupLost = { calls: await run({ "POST /api/guard/setup": "lost" }, () => api.settingsClick(click("guard-remove"))), result: S.setup.result, busy: S.setup.busy };
  fresh();
  S.setup = { busy: "install", result: null };            // one at a time
  actions.setupTwice = await run({}, () => api.settingsClick(click("guard-remove")));
  fresh();

  // the fields: a blank cap is off, a stagger is a whole number of seconds, and what the server would refuse is refused here
  const field = (key, value) => ({ target: { type: "number", value, getAttribute: (name) => (name === "data-key" ? key : null), hasAttribute: () => false } });
  const typed = async (key, value) => {
    elements["err-" + key] = element("err-" + key);
    const lines = await run({ "POST /api/settings": [200, { ok: true, applied: {}, settings: {} }] }, () => api.settingsChange(field(key, value)));
    const wrote = calls.filter((c) => c.key === "POST /api/settings").map((c) => c.body);
    return { wrote, error: elements["err-" + key].textContent, shown: elements["err-" + key].hidden === false, status: S.status.text, lines: lines.length };
  };
  actions.fields = {};
  for (const [label, key, value] of [
    ["capBlank", "guard_session_cap", ""], ["capSpaces", "guard_weekly_cap", "  "], ["cap1", "guard_session_cap", "1"], ["cap95", "guard_session_cap", "95"],
    ["cap100", "guard_scoped_cap", "100"], ["cap0", "guard_session_cap", "0"], ["cap101", "guard_weekly_cap", "101"], ["capDecimal", "guard_session_cap", "9.5"],
    ["capNegative", "guard_scoped_cap", "-5"], ["capText", "guard_session_cap", "abc"], ["capExp", "guard_session_cap", "1e2"], ["capLong", "guard_session_cap", "0095"],
    ["stagger0", "guard_resume_stagger_s", "0"], ["stagger120", "guard_resume_stagger_s", "120"], ["stagger600", "guard_resume_stagger_s", "600"],
    ["stagger601", "guard_resume_stagger_s", "601"], ["staggerBlank", "guard_resume_stagger_s", ""], ["staggerNegative", "guard_resume_stagger_s", "-1"], ["staggerDecimal", "guard_resume_stagger_s", "1.5"],
  ]) {
    fresh();
    actions.fields[label] = await typed(key, value);
  }
  fresh();
  const toggled = { target: { type: "checkbox", checked: true, value: "on", getAttribute: (name) => (name === "data-key" ? "guard_enabled" : name === "data-kind" ? "bool" : null), hasAttribute: () => false } };
  actions.toggle = { calls: await run({ "POST /api/settings": [200, { ok: true, applied: {}, settings: { guard_enabled: true } }], "GET /api/guard": GOOD }, () => api.settingsChange(toggled)), status: S.status.text };
  fresh();
  actions.otherSettingDoesNotAskTheGuard = (await run({ "POST /api/settings": [200, { ok: true, applied: {}, settings: {} }] }, () => api.settingsChange({ target: { type: "checkbox", checked: true, getAttribute: (name) => (name === "data-key" ? "reduce_motion" : null), hasAttribute: () => false } }))).filter((line) => /api\/guard/.test(line));
  out.guard.actions = actions;
  box.document.querySelector = saved;
  box.fetch = () => { throw new Error("no network in the script tests"); };
  reset();
}

// "Check again" and "Copy" on the sign-in help, against a recording monitor and a clipboard that answers as the test says
async function signInActions() {
  const saved = { fetch: box.fetch, set: box.setTimeout, clear: box.clearTimeout, navigator: box.navigator };
  const calls = [];
  const timers = [];
  box.clearTimeout = () => {};
  box.setTimeout = (fn, ms) => { timers.push({ fn, ms }); return timers.length; };
  const run = async (table, act) => {
    calls.length = 0;
    box.fetch = (url, init) => {
      const key = ((init && init.method) || "GET") + " " + url;
      calls.push(key);
      const hit = table[key] || [404, { error: "not in this test" }];
      return hit === "lost" ? Promise.reject(new Error("link lost")) : answer(hit[0], hit[1])();
    };
    act();
    await settle();
    return calls.slice();
  };
  const payload = (status, at, extra) => ({ state: Object.assign({}, S.data.state, { status, updated_at: at }, extra || {}), model_names: {}, settings: {} });
  const where = () => Object.assign({}, S.check);
  const REFRESHED = { "POST /api/refresh": [200, { ok: true }] };
  const out2 = {};

  // the monitor looks again and the state of that cycle still has no sign-in: the panel says so
  monitorState("no_credentials");
  const during = {};
  out2.still = {
    calls: await run(Object.assign({ "GET /api/state": [200, payload("no_credentials", SIGN_LATER)] }, REFRESHED), () => {
      api.checkAgain();
      during.check = where();
      during.html = api.statusBanner();
    }),
    during, check: where(), html: api.statusBanner(),
  };
  // the same for an expired sign-in, in its own words
  monitorState("stale", { status_detail: "Sign-in token expired; open Claude Code to refresh it" });
  await run(Object.assign({ "GET /api/state": [200, payload("stale", SIGN_LATER)] }, REFRESHED), () => api.helpAction("check-again"));
  out2.stillExpired = { check: where(), html: api.statusBanner() };
  // one request at a time: clicks while a check is on its way send nothing
  monitorState("no_credentials");
  out2.twice = await run(Object.assign({ "GET /api/state": [200, payload("no_credentials", SIGN_LATER)] }, REFRESHED), () => {
    api.checkAgain();
    api.checkAgain();
    api.helpAction("check-again");
  });
  monitorState("no_credentials");
  S.check = { busy: true, sent: false, from: SIGN_UPDATED, at: Date.now(), still: false, failed: false };
  out2.busyIgnores = await run(REFRESHED, () => api.checkAgain());
  // found: the next state is live, the panel is gone and nothing is left to say
  monitorState("no_credentials");
  await run(Object.assign({ "GET /api/state": [200, payload("live", SIGN_LATER, { status_detail: "Updated 12:12:05" })] }, REFRESHED), () => api.checkAgain());
  out2.found = { check: where(), html: api.statusBanner() };
  // the cycle has not been published yet (updated_at has not moved): the check goes on, until it has waited long enough
  monitorState("no_credentials");
  await run(Object.assign({ "GET /api/state": [200, payload("no_credentials", SIGN_UPDATED)] }, REFRESHED), () => api.checkAgain());
  out2.waiting = { check: where(), ended: api.syncCheck() };
  S.check.at = Date.now() - 20000;
  out2.late = { ended: api.syncCheck(), check: where(), html: api.statusBanner() };
  // not before the monitor has answered the click
  monitorState("no_credentials");
  S.check = { busy: true, sent: false, from: SIGN_UPDATED, at: Date.now() - 20000, still: false, failed: false };
  S.data.state.updated_at = SIGN_LATER;
  out2.notSentYet = { ended: api.syncCheck(), check: where() };
  // the monitor refuses, or the link is lost
  monitorState("no_credentials");
  out2.refused = { calls: await run({ "POST /api/refresh": [501, { error: "refresh is not available" }] }, () => api.checkAgain()), check: where(), html: api.statusBanner() };
  monitorState("no_credentials");
  out2.lost = { calls: await run({ "POST /api/refresh": "lost" }, () => api.checkAgain()), check: where(), html: api.statusBanner() };
  // what a check said does not outlive the panel
  monitorState("live");
  S.check = { busy: false, sent: true, from: SIGN_UPDATED, at: 0, still: true, failed: false };
  out2.outlived = { ended: api.syncCheck(), check: where() };

  // Copy: the command goes to the clipboard once, the button says so for a moment; where the clipboard refuses or is not there the
  // command is selected instead (no selection API in this page, which must not matter)
  const writes = [];
  const copyWith = async (clipboard) => {
    monitorState("no_credentials");
    writes.length = 0;
    timers.length = 0;
    box.navigator = clipboard === undefined ? undefined : { clipboard };
    api.helpAction("copy-command");
    await settle();
    const shown = { copy: S.copy, html: api.statusBanner(), timers: timers.map((t) => t.ms), writes: writes.slice() };
    if (timers.length) { timers[timers.length - 1].fn(); }
    shown.after = { copy: S.copy, html: api.statusBanner() };
    return shown;
  };
  out2.copy = {
    written: await copyWith({ writeText: (text) => { writes.push(text); return Promise.resolve(); } }),
    refused: await copyWith({ writeText: (text) => { writes.push(text); return Promise.reject(new Error("denied")); } }),
    throws: await copyWith({ writeText: () => { throw new Error("no permission"); } }),
    missing: await copyWith({}),
    noClipboard: await copyWith(undefined),
  };
  monitorState("no_credentials");
  out2.unknownAction = { calls: await run({}, () => api.helpAction("nothing-here")), copy: S.copy, check: where() };      // an unknown action does nothing

  box.fetch = saved.fetch; box.setTimeout = saved.set; box.clearTimeout = saved.clear; box.navigator = saved.navigator;
  forget();
  out.signInActions = out2;
}

failures().then(guardPolling).then(guardActions).then(signInActions).then(() => console.log(JSON.stringify(out)), (error) => { console.error(error && error.stack ? error.stack : String(error)); process.exit(1); });
