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
  return { api: box.__CUM_TEST__.api, charts: box.CUMCharts, elements };
}

const out = {};
const { api, charts, elements } = load();
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

console.log(JSON.stringify(out));
