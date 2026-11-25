"use strict";
/* Today's English page formats (#91, step 2.0 of its plan), as one JSON object: tests/fixtures/webui_formats_english.json.
 *
 * Runs the dashboard's real scripts under node, as tests/webui_checks.js does (formats.js, charts.js and app.js in a vm
 * context with the test hook set, so app.js hands back its helpers and does not start; formats.js and motion.js in a
 * context of their own, with English's monitor-locale meta, as index.html loads them), and calls every formatter of the
 * page on a fixed grid of inputs: the numbers, percents and money, the counts and compact figures, the durations and
 * ages, the clocks, dates and weekdays, the plurals and lists, the odometer and the rolling figures, and the sites that
 * write a number into a sentence, a card or a chart (their text only, not their markup).
 * tests/test_webui.py runs it with TZ=UTC and compares what it prints with the fixture, which was recorded before #91
 * changed the page: English must not change by a byte. The page's clock stands still at NOW. CALLS names each recorded
 * formatter and how it is called; a later step that reshapes one changes its entry here, never the fixture. An output is
 * what the call returns, or {"threw": name}. tName is not here: Chris's decision on #91 drops its lang="en". Nothing
 * here reads a user's data: every input is made up.
 *
 *   node tests/webui_formats.js <webui folder>
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const webui = process.argv[2];
if (!webui) {
  throw new Error("usage: node tests/webui_formats.js <webui folder>");
}
// Local times are the fixture's own: the zone must be UTC (english_left.py pins it the same way).
if (new Date(2026, 0, 15).getTimezoneOffset() !== 0 || new Date(2026, 6, 15).getTimezoneOffset() !== 0) {
  throw new Error("run with TZ=UTC: the fixture's clocks are UTC wall times");
}

const NOW = Date.UTC(2026, 9, 21, 16, 12, 30);            // a Wednesday afternoon
const DAY = 86400000;

// ── The page, as webui_checks.js loads it ─────────────────────────────────────────────────────────────────────

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

function svgElement(tag) {
  const el = element(tag);
  el.parentNode = null;
  el.appendChild = (child) => { child.parentNode = el; return child; };
  el.getComputedTextLength = () => String(el.textContent).length * 6;
  return el;
}

const EN_CATALOG = (() => {
  const data = JSON.parse(fs.readFileSync(path.join(webui, "i18n", "en.json"), "utf8"));
  const fallback = {};
  for (const key of Object.keys(data)) {
    if (!key.startsWith("_")) {
      fallback[key] = data[key];
    }
  }
  return { language: "en", strings: {}, fallback };
})();

/* A context whose clock stands still at NOW (as tests/english_scan.js stills the page's). */
function stillContext(box) {
  vm.createContext(box);
  vm.runInContext("(function (now) { var Real = Date;" +
    " class Still extends Real { constructor(...a) { super(...(a.length ? a : [now])); } static now() { return now; } }" +
    " globalThis.Date = Still; })(" + NOW + ");", box, { filename: "still-clock.js" });
  return box;
}

/* The page's scripts in the order index.html loads them, minus the catalog (given) and motion.js (run apart). */
const PAGE_ORDER = ["formats.js", "charts.js", "app.js"];

function loadPage() {
  const elements = {};
  const doc = {
    readyState: "complete", visibilityState: "visible", documentElement: element("html"), head: element("head"),
    body: svgElement("body"), querySelector: () => null, querySelectorAll: () => [],
    getElementById: (id) => elements[id] || null, createElement: (tag) => element(tag),
    createElementNS: (_ns, tag) => svgElement(tag), addEventListener() {},
  };
  const box = { console, document: doc, __USAGE_MONITOR_TEST__: {} };
  box.window = box;
  box.location = { hash: "" };
  box.matchMedia = () => ({ matches: false });
  box.getComputedStyle = () => ({ getPropertyValue: () => "" });
  box.requestAnimationFrame = () => 0;
  box.setInterval = () => 0;
  box.setTimeout = () => 0;
  box.addEventListener = () => {};
  box.fetch = () => { throw new Error("no network in the script tests"); };
  box.MonitorI18n = EN_CATALOG;
  stillContext(box);
  for (const name of PAGE_ORDER) {
    vm.runInContext(fs.readFileSync(path.join(webui, name), "utf8"), box, { filename: name });
  }
  return { api: box.__USAGE_MONITOR_TEST__.api, charts: box.MonitorCharts, elements };
}

/* motion.js as the page runs it: formats.js loaded first, with English's locale in the monitor-locale meta, so the rolling
 * figures read and write through the formats layer. (test_motion.py holds the page without the layer to the same.) */
function loadMotion() {
  const root = { getAttribute: () => null, setAttribute() {}, removeAttribute() {} };
  const english = { setting: "system", language: "en", system: "en", format: "en-US", clock: 12, offered: ["en"] };
  const meta = { getAttribute: (k) => (k === "content" ? JSON.stringify(english) : null) };
  const box = { console, document: { documentElement: root, visibilityState: "visible",
    querySelector: (sel) => (sel === 'meta[name="monitor-locale"]' ? meta : null) } };
  box.window = box;
  stillContext(box);
  for (const name of ["formats.js", "motion.js"]) {
    vm.runInContext(fs.readFileSync(path.join(webui, name), "utf8"), box, { filename: name });
  }
  if (!box.MonitorFormats || box.MonitorFormats.active().language !== "en") {
    throw new Error("motion.js's formats layer is missing or not English's");
  }
  return box.USAGE_MONITOR_FX;
}

const { api, charts, elements } = loadPage();
const FX = loadMotion();
const S = api.S;

function reset() {
  S.data = null; S.stats = {}; S.settings = {}; S.linkOk = null; S.nextCharts = {};
}

// ── Recording ─────────────────────────────────────────────────────────────────────────────────────────────────

/* An argument as text: the key of its case (JSON has no undefined, NaN, Infinity or -0). */
function show(value) {
  if (value === undefined) {
    return "undefined";
  }
  if (typeof value === "number") {
    return Object.is(value, -0) ? "-0" : String(value);
  }
  return JSON.stringify(value);
}

function outcome(fn) {
  try {
    const value = fn();
    return value === undefined ? "undefined" : value;
  } catch (e) {
    return { threw: e && e.name ? e.name : String(e) };
  }
}

/* { "(args)": output } for each argument list. */
function cases(fn, argLists) {
  const out = {};
  for (const args of argLists) {
    const key = "(" + args.map(show).join(", ") + ")";
    if (Object.prototype.hasOwnProperty.call(out, key)) {
      throw new Error("two cases with the same key: " + key);
    }
    out[key] = outcome(() => fn.apply(null, args));
  }
  return out;
}

const cross = (xs, ys) => xs.flatMap((x) => ys.map((y) => [x, y]));
const one = (xs) => xs.map((x) => [x]);
const texts = (svg) => Array.from(String(svg).matchAll(/<text[^>]*>([^<]*)<\/text>/g), (m) => m[1]);
const bolds = (html) => Array.from(String(html).matchAll(/<b[^>]*>([^<]*)<\/b>/g), (m) => m[1]);
const bigs = (html) => Array.from(String(html).matchAll(/class="nm-(?:big|stat-v|stat-s)[^"]*"[^>]*>([^<]*)</g), (m) => m[1]);

/* The odometer as it reads: its label, and its glyphs (a digit strip by its digit, a separator by its text). */
function odometer(html) {
  const label = (String(html).match(/aria-label="([^"]*)"/) || [])[1];
  const glyphs = Array.from(String(html).matchAll(/data-d="([^"]*)"|<span class="nm-od-sep"[^>]*>([^<]*)</g),
    (m) => (m[1] !== undefined ? m[1] : m[2])).join("");
  return [label, glyphs];
}

/* The charts a page function put on the page, drawn at a width (what hydrateCharts does). */
function drawn(width) {
  const out = Object.keys(S.nextCharts).map((id) => texts(S.nextCharts[id].build(width)));
  S.nextCharts = {};
  return out;
}

// ── The grid ──────────────────────────────────────────────────────────────────────────────────────────────────

// Every threshold the formatters branch at, each side of it, the halves that round one way or the other, and what
// num() turns away. 999.995, 999.999 and -999.995 (cents that round up to 1000.00) came with #91's review: their cases
// were recorded from the page at 1193edb, the one the rest was recorded from.
const NUMBERS = [
  null, undefined, "", "x", "5.96", "1,234", 0, -0, 0.004, 0.005, 0.0049, 0.04, 0.05, 0.25, 0.5, 0.95, 1, 1.005, 1.15,
  1.25, 1.5, 2.5, 5.96, 9.94, 9.95, 9.994, 9.995, 9.999, 10, 12.345, 12.5, 33.35, 33.5, 49.95, 99.4, 99.5, 99.94,
  99.95, 99.99, 99.995, 100, 100.4, 100.5, 123.456, 999, 999.4, 999.5, 999.6, 999.995, 999.999, 1000, 1149.99, 1150,
  1234.5, 1234.567, 9949, 9950, 9999, 9999.5, 10000, 12345.678, 99949, 99950, 99999, 100000, 123456.7, 999949, 999950,
  999999, 1e6, 1234567.89, 9.94e6, 9.95e6, 99.94e6, 99.95e6, 999.9e6, 999.95e6, 1e9, 1.5e9, 1234567890, 9.995e9, 1e12,
  1e21, -0.004, -0.4, -0.5, -1, -2.5, -5.96, -9.99, -9.995, -10, -99.99, -100, -123.5, -999, -999.5, -999.995, -1000,
  -1234.5, -9999, -10000, -99999, -1e6, -9.95e6, -1e9, Infinity, NaN,
];
const DIGITS = [undefined, 0, 1, 2];
const AMOUNTS = [null, 0, 5.96, -5.96, 0.005, 99.995, 1234.5, -1234.5, 1234567.891];
const CURRENCIES = [undefined, null, "", "USD", "usd", " cad ", "CAD", "EUR", "GBP", "JPY", "KWD", "CHF", "XYZ", "EURO", "US"];
const COUNTS = [null, 0, 1, 1.4, 1.5, 2, 21, 999.5, 1000, 1234.5, 1e6, -1];
const DURATIONS = [null, NaN, Infinity, -1, 0, 59999, 60000, 119999, 3599999, 3600000, 3660000, 86399999, 86400000,
  90061000, 172800000, 183600000, 1e10];
const AGES = [null, NaN, -5, 0, 999, 1000, 59999, 60000, 3599999, 3600000, 172799999, 172800000, 1e10];
// Today at every hour, at :00, :05, :39 and :59; each hour at :05 yesterday and on each of the next six days; none.
const TODAY = Date.UTC(2026, 9, 21);
const TODAYS = [];
for (let hour = 0; hour < 24; hour++) {
  for (const minute of [0, 5, 39, 59]) {
    TODAYS.push(TODAY + hour * 3600000 + minute * 60000);
  }
}
const CLOCKS = [null].concat(TODAYS);
for (const day of [-1, 1, 2, 3, 4, 5, 6]) {
  for (let hour = 0; hour < 24; hour++) {
    CLOCKS.push(TODAY + day * DAY + hour * 3600000 + 5 * 60000);
  }
}
// Dates across a year's edges and each month.
const DATES = [null];
for (let month = 0; month < 12; month++) {
  DATES.push(Date.UTC(2026, month, 1, 0, 0), Date.UTC(2026, month, 9, 12, 5), Date.UTC(2026, month, 28, 23, 59));
}
DATES.push(Date.UTC(2025, 11, 31, 23, 59), Date.UTC(2027, 0, 1, 0, 0));
const YMDS = ["2026-10-18", "2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24",
  "2026-01-01", "2026-02-28", "2026-12-31", "2026-10-21T08:00:00Z", "x", "", null];

// ── Each formatter and how it is called ───────────────────────────────────────────────────────────────────────

const CALLS = {
  fmtPct: () => cases(api.fmtPct, cross(NUMBERS, DIGITS)),
  fmtRate: () => cases(api.fmtRate, cross(NUMBERS, DIGITS)),
  pctWords: () => cases(api.pctWords, one(NUMBERS)),
  fmtUsd: () => cases(api.fmtUsd, one(NUMBERS)),
  fmtUsdExact: () => cases(api.fmtUsdExact, one(NUMBERS)),
  fmtUsdShort: () => cases(api.fmtUsdShort, one(NUMBERS)),
  fmtUsdTile: () => cases(api.fmtUsdTile, one(NUMBERS)),
  fmtUsdHero: () => cases(api.fmtUsdHero, one(NUMBERS)),
  fmtPlanPrice: () => cases(api.fmtPlanPrice, one(NUMBERS)),
  fmtMoney: () => cases(api.fmtMoney, cross(AMOUNTS, CURRENCIES)),
  fmtMult: () => cases(api.fmtMult, one(NUMBERS)),
  fmtCount: () => cases(api.fmtCount, one(NUMBERS)),
  fmtInt: () => cases(api.fmtInt, one(NUMBERS)),
  fmtCompare: () => cases(api.fmtCompare, one(NUMBERS)),
  signed: () => cases(api.signed, cross(NUMBERS, DIGITS)),
  plural: () => cases(api.plural, cross(COUNTS, ["request", "session"])),
  listWords: () => cases(api.listWords, one([[], ["A"], ["A", "B"], ["A", "B", "C"], ["A", "B", "C", "D"]])),
  fmtRemain: () => cases(api.fmtRemain, one(DURATIONS)),
  fmtAge: () => cases(api.fmtAge, one(AGES)),
  ageWords: () => cases((ms, sentence) => api.ageWords(api.fmtAge(ms), sentence ? api.AGE_UNIT_IN_SENTENCE : api.AGE_UNIT),
    cross(AGES, [false, true])),
  clockText: () => cases(api.clockText, one(CLOCKS)),
  stampText: () => cases(api.stampText, one(DATES.concat(TODAYS))),
  dateText: () => cases(api.dateText, one(DATES)),
  hourText: () => cases(api.hourText, one(TODAYS)),
  dayText: () => cases(api.dayText, cross(YMDS, [false, true])),
  rangeText: () => cases(api.rangeText, [["2026-10-01T00:00:00Z", "2026-10-31T23:59:00Z"],
    ["2026-12-25T12:00:00Z", "2027-01-24T12:00:00Z"], ["2026-02-01T00:00:00Z", "2026-02-28T00:00:00Z"],
    [null, "2026-10-31T00:00:00Z"], ["2026-10-01T00:00:00Z", "x"]]),
  hourRange: () => cases(api.hourRange, one(Array.from({ length: 24 }, (_, c) => c))),
  calendarMonths: () => {          // charts.js's month names over a year of the calendar
    const days = [];
    for (let i = 0; i < 371; i++) {
      const d = new Date(Date.UTC(2026, 9, 21) - (370 - i) * DAY);
      days.push({ date: d.toISOString().slice(0, 10), value: i % 7, tip: "t" });
    }
    return texts(charts.calendar(days, { width: 1000, maxCell: 22, legend: ["LESS", "MORE"] }));
  },
  constants: () => ({ DAYS: api.DAYS, WEEKDAYS_MON: api.WEEKDAYS_MON, MONTHS: api.MONTHS }),
  odometerHTML: () => cases((total, placeholder) => odometer(api.odometerHTML(total, placeholder)),
    cross([null, 0, 7, 1234, 1234567, 1234567890, -5, 12.5], [false, true])),
  // What a rolling figure reads from a text: before the number, its value, its decimals, after it (null: no number).
  parseNum: () => cases((text) => {
    const p = FX.text.parseNum(text);
    return p === null ? null : [p.pre, p.val, p.dec, p.post];
  }, one(["$1,107.50 x", "13%", "6.0×", "Mon", "1,234,567", "0.5", "$0.00", "-$5.96", "12 K", "100%", "$1.2K", "",
    "abc 12", "1,234.5 tokens", "33.5%"])),
  fmtNum: () => cases((text, v) => FX.text.fmtNum(FX.text.parseNum(text), v),
    cross(["$1,107.50 x", "13%", "6.0×", "1,234,567", "33.5%", "$0.00"], [0, 0.5, 6.4, 3.04, 553.75, 1107.5, 1234567.891])),
  // The sites that write a number into a sentence, a card or a chart.
  weeklyStory: () => cases((verdict, delta) => api.weeklyStory({ verdict: verdict, pace_delta_pp: delta }).text,
    cross(["AHEAD", "BEHIND"], [0.04, 1.25, 12.345, -3.5, 1234.56]).concat([["ON_PACE", 0], ["UNKNOWN", null]])),
  sessionStory: () => cases((verdict, reset, hit) => api.sessionStory({ verdict: verdict, resets_at: reset, projected_limit_at: hit }).text,
    [["LIMITED", "2026-10-21T19:39:00Z", null], ["AT_RISK", "2026-10-21T19:39:00Z", "2026-10-21T18:05:00Z"],
      ["SAFE", "2026-10-22T00:05:00Z", null], ["IDLE", null, null]]),
  weeklyForecast: () => cases((end, budget) => api.weeklyForecast({ percent: 40, projected_end_pct: end, daily_budget_pct: budget }),
    cross([null, 33.3, 99.5, 120.4], [null, 14.28, 0.4])),
  efficiencyCard: () => cases((hit, thinking, saved) => bigs(api.efficiencyCard({ hit, thinking, saved, loaded: true })),
    [[null, null, null], [0.3345, 0.125, 5.96], [0.9999, 1, 1234.5], [0.00049, 0.5, 99999]]),
  compactions: () => cases((perDay) => {
    const d = api.skelEfficiency();
    d.skeleton = false;
    d.compactions = { count: 1234, auto: 1200, manual: 34, per_active_day: perDay, avg_pre_tokens: 156789 };
    const e = { key: "efficiency", data: d, error: "" };
    const card = api.efficiencyCards(d, e, "ready").filter((p) => p.key === "compactions")[0];
    return bigs(card.html);
  }, one([null, 0, 1.005, 12.3456])),
  // The comparison's figure and unit as the box shows them (as text: updateCompare writes them so).
  comparisonList: () => cases((key, value) => api.comparisonList({ comparisons: [{ key: key, value: value, label: "things" }] })
    .map((c) => [String(c.n), c.u]),
    cross(["war_and_peace", "britannica", "page_stack_m", "other"], [0.04, 5.55, 12.5, 123.4, 999.95, 1234.5, 123456])),
  pipelineCard: () => cases((running, seconds) => {
    reset();
    S.data = { state: { status: "live", usage_fetched_at: "2026-10-21T16:10:05Z", updated_at: "2026-10-21T16:12:00Z",
      ingest: { running: running, files_done: 1234, files_total: 5678, last_duration_s: seconds, last_error: null },
      plan: { name: "Max 5x", monthly_price_usd: 100 }, unpriced_models: [] } };
    return bolds(api.pipelineCard(S.data.state));
  }, cross([false, true], [null, 0.5, 12.345])),
  health: () => cases((running, done, total) => {
    reset();
    elements.health = element("health");
    S.data = { state: { status: "live", ingest: { running: running, files_done: done, files_total: total }, unpriced_models: [] } };
    api.renderHealth();
    return bolds(elements.health.innerHTML);
  }, [[true, 0, 0], [true, 1, 3], [true, 999, 1000], [false, 1234, 1234]]),
  pacingChart: () => cases((top) => {
    reset();
    const blk = { window_start: "2026-10-15T16:00:00Z", resets_at: "2026-10-22T16:00:00Z", percent: 40, verdict: "AHEAD",
      points: [["2026-10-16T16:00:00Z", 10], ["2026-10-20T16:00:00Z", top]], projected_end_pct: 80, daily_budget_pct: 10 };
    api.pacingChart(blk, "weekly", NOW, "d", false);
    return drawn(500);
  }, one([40, 100, 133.3])),
  dailyChart: () => cases((cost) => {
    reset();
    const e = api.statEntry("history");
    e.data = api.skelHistory();
    e.data.days.slice(-30).forEach((x, i) => { x.cost_usd = cost * (i % 3); x.requests = i; });
    const html = api.dailyChart(null);
    return [String(html).match(/<span class="nm-meta-t">([^<]*)</) ? String(html).match(/<span class="nm-meta-t">([^<]*)</)[1] : null]
      .concat(drawn(500));
  }, one([0, 0.5, 7.5, 40, 1500])),
};

const out = {
  _about: "Today's English page formats, captured before #91 changed the page (step 2.0 of its plan, at 94f53c3, whose " +
    "usage_monitor/webui is the same as ed5b2fe's, with TZ=UTC and the page's clock at 2026-10-21 16:12:30). " +
    "tests/webui_formats.js prints this and tests/test_webui.py holds it: English must not change by a byte. Each " +
    "case is keyed by its arguments; {\"threw\": name} is a call that threw. Never re-recorded.",
};
for (const name of Object.keys(CALLS)) {
  reset();
  out[name] = CALLS[name]();
}
// ASCII only, so a U+00A0 or a U+202F in a figure shows as its escape.
process.stdout.write(JSON.stringify(out, null, 1).replace(/[\u007f-\uffff]/g,
  (ch) => "\\u" + ch.charCodeAt(0).toString(16).padStart(4, "0")) + "\n");
