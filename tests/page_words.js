"use strict";
/* The dashboard's English as a person sees it, in every look (#98): the record that holds the shell, the header, the sidebar,
 * the page headings and banners, Overview and Limits, and the cards elsewhere that share their code, to today's English
 * while their words move into the catalog.
 *
 *   node tests/page_words.js <webui folder>             prints the views as one JSON object
 *   node tests/page_words.js <webui folder> --record    writes tests/fixtures/page_words_english.json (refused once it exists)
 *
 * Each view is one fresh load of the real page on the small DOM (tests/mini_dom.js): index.html as the server serves it in
 * English (its theme on <html>, as ui/server.py's _themed_shell writes it), then formats.js, the English catalog, charts.js
 * and app.js, which starts as it does in the app (init: the first render, the first poll). A small server in this file
 * answers the page from tests/fixtures/page_words_inputs.json (the dev server's made-up answers, frozen), changed by the case:
 * another status, a report that fails, waits or goes stale, another verdict, and so on. Then the page is opened at each of the
 * case's pages from the menu. The clock stands still at the inputs' moment (a case moves it on and fires the timers that fall
 * due, as the 1 s tick and the 2 s poll do); run with TZ=UTC (test_webui.py does).
 *
 * In every look the text is read as it is drawn: each text node in the case its element takes from the look's stylesheets
 * (tests/css_case.js), and in Gates after gates.js, which the page loads with the theme as it does in the app, has reworded it.
 * A region is the header's brand, link line, data age and Refresh button, the whole sidebar, each part above a page's cards
 * (the hazard band, the heading, the banners) and the cards the record keeps (every card of Overview and Limits; Cost's value
 * and credits cards; Efficiency's pipeline card). Each region gives its text and its attributes people read (title,
 * aria-label, data-tip, alt, placeholder) in document order. In Gates, a view before the first state (gates.js comes with
 * the state's theme) also gives the text once gates.js has run over it.
 *
 * motion.js and the other theme scripts do not run: they move and decorate what app.js draws, and the record is the settled
 * page. Nothing here reads a user's data or needs a browser, a window or the network.
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const D = require("./mini_dom.js");
const CSS_CASE = require("./css_case.js");

const webui = process.argv[2];
const RECORD = process.argv.includes("--record");
const FIXTURE = path.join(__dirname, "fixtures", "page_words_english.json");
const INPUTS = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures", "page_words_inputs.json"), "utf8"));
const NOW = Date.parse(INPUTS.now);
const WIDTH = 1184;
if (!webui) {
  throw new Error("usage: node tests/page_words.js <webui folder> [--record]");
}
if (new Date(NOW).getTimezoneOffset() !== 0) {
  throw new Error("run with TZ=UTC: the record's clocks are UTC");
}

const LOOKS = [
  { name: "nostromo", theme: "nostromo", variant: "amber" },
  { name: "night_city", theme: "night_city", variant: "blackwall" },
  { name: "edgerun", theme: "night_city", variant: "edgerun" },
  { name: "gates-dark", theme: "gates", variant: "blue-dark" },
  { name: "gates-light", theme: "gates", variant: "blue-light" },
];
const ALL_PAGES = ["overview", "limits", "cost", "efficiency", "history", "settings", "about"];
const MODEL_PAGES = ["overview", "limits", "cost", "efficiency", "history"];      // the pages with banners
const STAT_PAGES = ["overview", "limits"];
// The cards each page's view keeps, besides its top parts ("*": every card).
const KEPT_CARDS = { overview: "*", limits: "*", cost: ["value", "credits"], efficiency: ["pipeline"] };
const TEXT_ATTRS = ["title", "aria-label", "data-tip", "alt", "placeholder"];
const CHROME = [["brand", ".nm-brand"], ["uplink", "#uplink"], ["age", "#data-age"], ["refresh", "#refresh"], ["nav", "nav.nm-nav"]];

const clone = (value) => JSON.parse(JSON.stringify(value));
const iso = (ms) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z");
const HOUR = 3600000;

// ── the cases ────────────────────────────────────────────────────────────────────

// A world is what the small server answers: the state, the four reports (undefined: as the inputs have it; "hang": never
// answered; "fail": the request fails; {status, body}: that answer), and the themes.
function world() {
  return {
    state: clone(INPUTS.state), themes: clone(INPUTS.themes), stateAnswer: undefined, serial: 1,
    reports: { pacing: clone(INPUTS.pacing), cost: clone(INPUTS.cost), history: clone(INPUTS.history), efficiency: clone(INPUTS.efficiency) },
    answers: { pacing: undefined, cost: undefined, history: undefined, efficiency: undefined },
  };
}
const st = (w) => w.state.state;
const limitOf = (w, kind) => st(w).usage.limits.filter((l) => l.kind === kind)[0];
const allReports = (w, answer) => { Object.keys(w.answers).forEach((k) => { w.answers[k] = answer; }); };
const failWith = (status, error) => (w) => allReports(w, { status, body: error === undefined ? null : { error } });

const STATUS_DETAILS = {
  loading: "Loading usage", offline: "Usage API unreachable (timed out); next try at 4:20 PM", rate_limited: "Usage API rate limited",
  paused: "Paused", stale: "The sign-in has expired. Claude Code renews it when you use it.",
};
function status(code, extra) {
  return (w) => {
    st(w).status = code;
    st(w).status_detail = STATUS_DETAILS[code] || "";
    if (extra) { extra(w); }
  };
}

/* The pacing report's blocks for a verdict. */
function sessionVerdict(w, verdict) {
  const blk = w.reports.pacing.session;
  blk.verdict = verdict;
  if (verdict === "LIMITED") { blk.percent = 100; limitOf(w, "session").percent = 100; blk.projected_limit_at = null; }
  if (verdict === "AT_RISK") { blk.percent = 71; limitOf(w, "session").percent = 71; blk.projected_limit_at = iso(NOW + 1.25 * HOUR); blk.slope_pct_per_hour = 23.4; }
  if (verdict === "IDLE") { blk.slope_pct_per_hour = 0.2; }
  if (verdict === "UNKNOWN") { blk.slope_pct_per_hour = null; blk.points = blk.points.slice(0, 1); }
}
function weeklyVerdict(blk, verdict) {
  blk.verdict = verdict;
  if (verdict === "AHEAD") { blk.percent = 78; blk.pace_delta_pp = 20.3; blk.projected_end_pct = 135.1; blk.daily_budget_pct = 7.2; }
  if (verdict === "ON_PACE") { blk.percent = 55; blk.pace_delta_pp = -2.7; blk.projected_end_pct = 95.3; blk.daily_budget_pct = 15.0; }
  if (verdict === "UNKNOWN") { blk.pace_delta_pp = null; blk.projected_end_pct = null; blk.daily_budget_pct = null; blk.even_daily_pct = null; }
}
function verdicts(session, weekly, scoped) {
  return (w) => {
    sessionVerdict(w, session);
    weeklyVerdict(w.reports.pacing.weekly, weekly);
    w.reports.pacing.scoped.forEach((b) => weeklyVerdict(b, scoped));
    limitOf(w, "weekly_all").percent = w.reports.pacing.weekly.percent;
  };
}
function limits(patches) {
  return (w) => {
    Object.keys(patches).forEach((kind) => Object.assign(limitOf(w, kind), patches[kind]));
  };
}
function velocity(code) {
  return (w) => { st(w).burn = code === null ? null : { ...st(w).burn, velocity: code }; };
}
const SECOND_SCOPED = { kind: "weekly_scoped", group: "weekly", percent: 31, severity: "normal", resets_at: "2026-10-24T15:12:00.000Z",
  is_active: true, label: "CLAUDE CODE WEEKLY", scope_model: null, scope_surface: "claude_code", window_seconds: 604800, allowance_pct: 80,
  used_pct_of_weekly: 24.8, locked_reason: null, dollars: null, fraction: 0.31 };

/* Each case: a name, what it changes in the world, what it does once the page is up (steps), and the pages it views. */
const CASES = [
  { name: "live", pages: ALL_PAGES },
  { name: "status-loading", world: status("loading"), pages: MODEL_PAGES },
  { name: "status-stale", world: status("stale"), pages: MODEL_PAGES },
  { name: "status-stale-refused", world: status("stale", (w) => { st(w).signin_refused = 403; }), pages: ["overview"] },
  { name: "status-offline", world: status("offline"), pages: MODEL_PAGES },
  { name: "status-rate-limited", world: status("rate_limited"), pages: MODEL_PAGES },
  { name: "status-no-credentials", world: status("no_credentials", (w) => {
    st(w).usage.limits = []; st(w).usage.breakdown = []; st(w).plan = null;
  }), pages: MODEL_PAGES },
  { name: "status-paused", world: status("paused"), pages: MODEL_PAGES },
  { name: "status-unknown", world: status("mystery"), pages: ["overview"] },
  { name: "link-lost", steps: [{ state: "fail" }, { advance: 2000 }], pages: STAT_PAGES },
  { name: "before-state", world: (w) => { w.stateAnswer = "hang"; allReports(w, "hang"); }, pages: ALL_PAGES },
  { name: "before-state-tick", world: (w) => { w.stateAnswer = "hang"; allReports(w, "hang"); }, steps: [{ advance: 1000 }], pages: STAT_PAGES },
  { name: "first-poll-failed", world: (w) => { w.stateAnswer = "fail"; allReports(w, "hang"); }, steps: [{ advance: 1000 }], pages: ["overview", "limits", "about"] },
  { name: "reports-pending", world: (w) => allReports(w, "hang"), pages: MODEL_PAGES },
  { name: "reports-503-busy", world: failWith(503, "statistics are not available right now"), pages: MODEL_PAGES },
  { name: "reports-503-closed", world: failWith(503, "the statistics database could not be opened"), pages: STAT_PAGES },
  { name: "reports-501", world: failWith(501, "statistics are not available"), pages: MODEL_PAGES },
  { name: "reports-500", world: failWith(500, "internal error"), pages: MODEL_PAGES },
  { name: "reports-401", world: failWith(401, "missing or wrong token"), pages: STAT_PAGES },
  { name: "reports-502", world: failWith(502), pages: STAT_PAGES },
  { name: "reports-lost", world: (w) => allReports(w, "fail"), pages: MODEL_PAGES },
  // a report on screen whose refresh failed, once each report has been asked for again (history's is the slowest, 120 s)
  { name: "reports-stale", steps: [{ reports: { status: 503, body: { error: "statistics are not available right now" } } }, { advance: 125000 }],
    pages: MODEL_PAGES },
  { name: "reports-stale-seconds", steps: [{ reports: "fail" }, { advance: 35000 }], pages: ["overview", "limits", "cost"] },
  { name: "pacing-pending", world: (w) => { w.answers.pacing = "hang"; }, pages: STAT_PAGES },
  { name: "pacing-lost", world: (w) => { w.answers.pacing = "fail"; }, pages: STAT_PAGES },
  { name: "cost-pending", world: (w) => { w.answers.cost = "hang"; }, pages: ["overview", "cost"] },
  { name: "cost-pending-no-plan", world: (w) => { w.answers.cost = "hang"; st(w).plan = null; }, pages: ["overview", "cost", "efficiency"] },
  { name: "cost-failed", world: (w) => { w.answers.cost = { status: 503, body: { error: "statistics are not available right now" } }; },
    pages: ["overview", "cost"] },
  // with a billing day the monitor has no month of its own to stand in, so the Overview's cost cards wait for the report
  { name: "cost-failed-billing-day", world: (w) => {
    w.state.settings.billing_day = 15;
    w.answers.cost = { status: 503, body: { error: "the statistics database could not be opened" } };
  }, pages: ["overview"] },
  { name: "cost-pending-billing-day", world: (w) => { w.state.settings.billing_day = 15; w.answers.cost = "hang"; }, pages: ["overview"] },
  { name: "history-failed", world: (w) => { w.answers.history = { status: 500, body: { error: "internal error" } }; }, pages: ["overview", "history"] },
  { name: "verdicts-limited-ahead", world: verdicts("LIMITED", "AHEAD", "ON_PACE"), pages: STAT_PAGES },
  { name: "verdicts-at-risk-on-pace", world: verdicts("AT_RISK", "ON_PACE", "UNKNOWN"), pages: STAT_PAGES },
  { name: "verdicts-idle-unknown", world: verdicts("IDLE", "UNKNOWN", "AHEAD"), pages: STAT_PAGES },
  { name: "verdicts-unknown-behind", world: verdicts("UNKNOWN", "BEHIND", "BEHIND"), pages: STAT_PAGES },
  { name: "chips-unused-on-pace-ahead", world: limits({ session: { percent: 0 }, weekly_all: { percent: 60, severity: "warning" },
    weekly_scoped: { percent: 80, severity: "warning" } }), pages: STAT_PAGES },
  { name: "chips-no-clock-ahead", world: limits({ session: { percent: 20, resets_at: null }, weekly_all: { percent: 93, severity: "critical" },
    weekly_scoped: { percent: 12, locked_reason: "Weekly limit reached for this model" } }), pages: STAT_PAGES },
  { name: "velocity-idle", world: velocity("IDLE"), pages: ["overview"] },
  { name: "velocity-slow", world: velocity("SLOW"), pages: ["overview"] },
  { name: "velocity-fast", world: velocity("FAST"), pages: ["overview"] },
  { name: "velocity-very-fast", world: velocity("VERY FAST"), pages: ["overview"] },
  { name: "velocity-unknown", world: velocity("WARP"), pages: ["overview"] },
  { name: "burn-none", world: velocity(null), pages: ["overview"] },
  { name: "credits-on", world: (w) => {
    st(w).usage.spend = { enabled: true, used: 12.5, limit: 50, percent: 25, severity: "normal", currency: "USD", balance: null, disabled_reason: null };
    w.reports.cost.spend = clone(st(w).usage.spend);
  }, pages: ["limits", "cost"] },
  { name: "scoped-none", world: (w) => { st(w).usage.limits = st(w).usage.limits.filter((l) => l.kind !== "weekly_scoped"); w.reports.pacing.scoped = []; },
    pages: STAT_PAGES },
  { name: "scoped-two", world: (w) => {
    st(w).usage.limits.push(clone(SECOND_SCOPED));
    w.reports.pacing.scoped.push({ ...clone(w.reports.pacing.scoped[0]), label: "CLAUDE CODE WEEKLY", model: null, scope_model: null,
      scope_surface: "claude_code", allowance_pct: 80, used_pct_of_weekly: 24.8, percent: 31, verdict: "ON_PACE", pace_delta_pp: -1.4,
      projected_end_pct: 53.7 });
  }, pages: STAT_PAGES },
  { name: "plan-unknown", world: (w) => {
    st(w).plan = null;
    Object.assign(w.reports.cost.plan, { name: "", subscription_type: null, rate_limit_tier: null, price_usd: null, price_source: "none" });
    w.reports.cost.value.multiplier = null;
  }, pages: ["overview", "cost", "efficiency"] },
  { name: "plan-override-unknown", world: (w) => {
    st(w).plan = { subscription_type: null, rate_limit_tier: null, name: null, monthly_price_usd: null };
    w.state.settings.plan_price_override = 50;
    Object.assign(w.reports.cost.plan, { name: "", subscription_type: null, rate_limit_tier: null, price_usd: 50, price_source: "override" });
    w.reports.cost.value.multiplier = w.reports.cost.value.cycle_cost_usd / 50;
  }, pages: ["overview", "cost", "efficiency"] },
  { name: "ingest-running", world: (w) => { Object.assign(st(w).ingest, { running: true, files_done: 1200 }); }, pages: ["overview", "efficiency"] },
  { name: "unpriced", world: (w) => { st(w).unpriced_models = ["mystery-model", "claude-next-1"]; }, pages: ["overview", "efficiency"] },
  { name: "no-activity", world: (w) => {
    st(w).windows.all.totals.requests = 0;
    Object.assign(st(w).ingest, { running: false, files_done: 0, files_total: 0, last_duration_s: 0.05, last_error: null });
  }, pages: MODEL_PAGES },
  { name: "update-offered", world: (w) => {
    w.state.update = { available: true, version: "9.9.9", url: "https://github.com/example/example/releases/tag/v9.9.9", can_self_update: false,
      skipped: false, info: null, skip_version: "" };
  }, pages: ALL_PAGES },
  { name: "age-minutes", world: (w) => { st(w).usage.fetched_at = iso(NOW - 5 * 60000 - 7000); }, pages: ["overview"] },
  { name: "age-hours", world: (w) => { st(w).usage.fetched_at = iso(NOW - 3 * HOUR - 60000); }, pages: ["overview"] },
  { name: "age-days", world: (w) => { st(w).usage.fetched_at = iso(NOW - 50 * HOUR); }, pages: ["overview"] },
];

// ── the page ─────────────────────────────────────────────────────────────────────

// read with LF endings: a checkout may have CRLF, and the page does not depend on it
const read = (name) => fs.readFileSync(path.join(webui, name), "utf8").replace(/\r\n/g, "\n");
const SCRIPTS = {};
const script = (name) => (SCRIPTS[name] = SCRIPTS[name] || new vm.Script(read(name), { filename: name }));
const EN_CATALOG = (() => {
  const data = JSON.parse(read(path.join("i18n", "en.json")));
  const fallback = {};
  Object.keys(data).filter((k) => !k.startsWith("_")).forEach((k) => { fallback[k] = data[k]; });
  return { language: "en", strings: {}, fallback };
})();
// --chart-pips as each theme's sheet sets it on :root (the page reads it with getComputedStyle; charts.js draws pips by it).
const PIPS = {};
for (const theme of ["nostromo", "night_city", "gates"]) {
  const m = /--chart-pips:\s*([^;]+);/.exec(read(path.join("themes", theme + ".css")));
  PIPS[theme] = m ? m[1].trim() : "";
}

function themeView(w, look) {
  const theme = w.themes.filter((t) => t.id === look.theme)[0];
  const variant = theme.variants.filter((v) => v.id === look.variant)[0];
  return { id: theme.id, name: theme.name, variant: variant.id, variant_name: variant.name, mode: variant.mode, css_vars: {}, assets: theme.assets };
}

/* index.html as ui/server.py serves it in English: the token, the locale, and the theme on <html> and the stylesheet link. */
function shell(w, look) {
  const view = w.state.theme;
  const scheme = view.mode === "light" ? "light" : "dark";
  return read("index.html")
    .replace(/(<meta name="monitor-token" content=")[^"]*(")/, "$1page-words$2")
    .replace(/(<meta name="monitor-locale" content=")[^"]*(")/, (all, a, b) => a + JSON.stringify(w.state.locale).replace(/"/g, "&quot;") + b)
    .replace(/<html\b[^>]*>/, (tag) => tag.replace(/\s(data-theme|data-variant|style)="[^"]*"/g, "").slice(0, -1) +
      ' data-theme="' + look.theme + '" data-variant="' + look.variant + '" style="color-scheme:' + scheme + '">')
    .replace(/(<meta name="color-scheme" content=")[^"]*(")/, "$1" + scheme + "$2")
    .replace(/<link id="theme-css"[^>]*>/, '<link id="theme-css" rel="stylesheet" href="/static/themes/' + look.theme + '.css" data-theme="' + look.theme + '">');
}

function response(status, body, etag) {
  const text = body === null || body === undefined ? "" : JSON.stringify(body);
  return { status, ok: status >= 200 && status < 300, headers: { get: (name) => (name.toLowerCase() === "etag" ? etag || null : null) },
    text: () => Promise.resolve(text), json: () => Promise.resolve(JSON.parse(text)) };
}

/* What the small server answers a request: a promise of a response, a rejected one, or one never settled. The state carries
 * an ETag and an unchanged one is answered 304, as ui/server.py does. */
function answer(w, url, method, headers) {
  const route = url.pathname;
  const give = (spec, body) => {
    if (spec === "hang") { return new Promise(() => {}); }
    if (spec === "fail") { return Promise.reject(new TypeError("Failed to fetch")); }
    if (spec && spec.status) { return Promise.resolve(response(spec.status, spec.body)); }
    return Promise.resolve(response(200, body));
  };
  if (route === "/api/state") {
    const etag = '"' + JSON.stringify(w.state).length + "-" + w.serial + '"';
    if (w.stateAnswer === undefined && headers && headers["If-None-Match"] === etag) {
      return Promise.resolve(response(304, null, etag));
    }
    return w.stateAnswer === undefined ? Promise.resolve(response(200, w.state, etag)) : give(w.stateAnswer, w.state);
  }
  if (route === "/api/themes") {
    return give(undefined, { themes: w.themes });
  }
  if (route.startsWith("/api/analytics/")) {
    const name = route.slice("/api/analytics/".length);
    return give(w.answers[name], w.reports[name]);
  }
  if (route === "/api/refresh" && method === "POST") {
    return give(undefined, { ok: true });
  }
  return Promise.resolve(response(404, { error: "not found" }));
}

// A French page whose catalog has none of the page's words yet: every one of them is English's, marked lang="en".
const FRENCH = { setting: "fr", language: "fr", system: "fr", format: "fr-FR", clock: 24, offered: ["en", "fr"] };
const FRENCH_CATALOG = { language: "fr", strings: {}, fallback: EN_CATALOG.fallback };

/* A fresh page for one case in one look. opts.lang "fr": the French page above. opts.motion: motion.js and every theme script
 * run too, and each theme's entrance (its FX.director settings) is kept in box.__directors, by theme. */
function load(w, look, problems, opts) {
  opts = opts || {};
  if (opts.lang === "fr") { w.state.locale = clone(FRENCH); }
  let html = shell(w, look);
  if (opts.lang === "fr") { html = html.replace('<html lang="en"', '<html lang="fr"'); }
  const doc = D.createDocument(html, { width: WIDTH, report: (e) => problems.push("a listener threw: " + describe(e)) });
  const clock = { now: NOW };
  const timers = [];
  let timerId = 0;
  const box = {
    console: {
      log() {}, info() {}, debug() {},
      warn: (...a) => problems.push("console.warn: " + a.map(String).join(" ")),
      error: (...a) => problems.push("console.error: " + a.map((x) => (x && x.stack) || String(x)).join(" ")),
    },
    document: doc, Event: D.Event, CustomEvent: D.CustomEvent, MutationObserver: D.MutationObserver, NodeFilter: D.NodeFilter,
    WeakRef, name: "",
    location: { href: "http://127.0.0.1:1/#overview", origin: "http://127.0.0.1:1", protocol: "http:", host: "127.0.0.1:1", pathname: "/",
      search: "", hash: "#overview", reload: () => problems.push("the page reloaded"), assign() {}, replace() {} },
    history: { replaceState: (_s, _t, url) => { if (typeof url === "string" && url[0] === "#") { box.location.hash = url; } }, pushState() {} },
    navigator: { userAgent: "node (tests/page_words.js)", language: "en-US", clipboard: { writeText: () => Promise.resolve() } },
    // with the motion layer, motion is reduced: the clock stands still, so a boot or an entrance would never end; the
    // directors are made either way
    matchMedia: (media) => ({ media, matches: !!opts.motion && /prefers-reduced-motion:\s*reduce/.test(media), addEventListener() {},
      removeEventListener() {}, addListener() {}, removeListener() {} }),
    performance: { now: () => clock.now - NOW },
    getComputedStyle: () => ({ getPropertyValue: (name) => (name === "--chart-pips" ? PIPS[doc.documentElement.getAttribute("data-theme")] || "" : "") }),
    requestAnimationFrame: (fn) => { timers.push({ id: ++timerId, at: clock.now, fn, frame: true }); return timerId; },
    cancelAnimationFrame: (id) => { drop(id); },
    setTimeout: (fn, ms) => { timers.push({ id: ++timerId, at: clock.now + Math.max(0, Number(ms) || 0), fn }); return timerId; },
    clearTimeout: (id) => { drop(id); },
    setInterval: (fn, ms) => { timers.push({ id: ++timerId, at: clock.now + Number(ms), fn, every: Number(ms) }); return timerId; },
    clearInterval: (id) => { drop(id); },
    open: () => null, scrollTo() {}, focus() {},
  };
  function drop(id) {
    const at = timers.findIndex((t) => t.id === id);
    if (at >= 0) { timers.splice(at, 1); }
  }
  Object.setPrototypeOf(box, D.Target.prototype);
  box.window = box;
  box.self = box;
  doc.defaultView = box;
  box.fetch = (url, options) => answer(w, new URL(String(url), box.location.href), String((options && options.method) || "GET").toUpperCase(),
    options && options.headers);
  vm.createContext(box);
  vm.runInContext("(function (clock) { var Real = Date;" +
    " class Still extends Real { constructor(...a) { super(...(a.length ? a : [clock.now])); } static now() { return clock.now; } }" +
    " globalThis.Date = Still; })", box, { filename: "still-clock.js" })(clock);
  // A theme script app.js adds (themes/<id>.js) loads as in a browser: gates.js runs (its rewording is part of what Gates
  // shows); the others only report that they loaded (they move and decorate the page, which the record does not read).
  const head = doc.head;
  const append = head.appendChild.bind(head);
  head.appendChild = (node) => {
    const added = append(node);
    if (node.localName === "script") {
      Promise.resolve().then(() => {
        const src = String(node.src || node.getAttribute("src") || "");         // app.js sets script.src
        try {
          const theme = /\/themes\/([a-z_]+)\.js$/.exec(src);
          if (theme && (theme[1] === "gates" || opts.motion)) { script("themes/" + theme[1] + ".js").runInContext(box); }
          if (typeof node.onload === "function") { node.onload(); }
        } catch (e) {
          problems.push(src + " threw: " + describe(e));
        }
      });
    }
    return added;
  };
  box.MonitorI18n = opts.lang === "fr" ? FRENCH_CATALOG : EN_CATALOG;
  script("formats.js").runInContext(box);
  if (opts.motion) {
    script("motion.js").runInContext(box);
    const FX = box.USAGE_MONITOR_FX;
    const director = FX.director;
    box.__directors = {};
    FX.director = (config) => { box.__directors[config.id] = config; return director(config); };
  }
  for (const name of ["charts.js", "app.js"]) {
    script(name).runInContext(box);
  }
  return { doc, box, clock, timers, world: w, look };
}

const describe = (e) => String((e && e.stack) || e).split("\n").slice(0, 3).join(" | ");

async function settle(view) {
  for (let idle = 0, round = 0; idle < 3; round++) {
    if (round > 5000) {
      throw new Error("the page did not settle");
    }
    await new Promise((resolve) => setImmediate(resolve));
    const due = view.timers.filter((t) => t.frame);
    due.forEach((t) => { view.timers.splice(view.timers.indexOf(t), 1); t.fn(view.clock.now); });
    idle = due.length ? 0 : idle + 1;
  }
}

/* Moves the clock on by ms, firing each timer as it falls due (in order), and lets the page settle after each. */
async function advance(view, ms) {
  const end = view.clock.now + ms;
  for (;;) {
    const next = view.timers.filter((t) => !t.frame && t.at <= end).sort((a, b) => a.at - b.at || a.id - b.id)[0];
    if (!next) { break; }
    view.clock.now = Math.max(view.clock.now, next.at);
    if (next.every) { next.at += next.every; } else { view.timers.splice(view.timers.indexOf(next), 1); }
    next.fn();
    await settle(view);
  }
  view.clock.now = end;
  await settle(view);
}

async function step(view, s) {
  if (s.state !== undefined) { view.world.stateAnswer = s.state; }
  if (s.reports !== undefined) { allReports(view.world, s.reports); }
  if (s.click) { view.doc.querySelector(s.click).click(); await settle(view); }
  if (s.advance) { await advance(view, s.advance); }
}

async function open(view, key) {
  if (view.box.location.hash !== "#" + key) {
    view.box.location.hash = "#" + key;
    view.box._fire(new D.Event("hashchange"), (e) => { throw e; });
    await settle(view);
  }
}

// ── reading what is drawn ────────────────────────────────────────────────────────

const RULES = {};
const rulesOf = (theme) => (RULES[theme] = RULES[theme] || CSS_CASE.caseRules(webui, theme));

/* The text of root as drawn in the look (each text node in its element's case) and its attributes people read. */
function region(root, rules, memo) {
  let text = "";
  const attrs = [];
  const walk = (node) => {
    if (node.nodeType === 3) {
      const host = node.parentNode;
      text += CSS_CASE.shownIn(node.data, host && host.nodeType === 1 ? CSS_CASE.caseOf(host, rules, memo).value : "none");
      return;
    }
    if (node.nodeType !== 1 || node.localName === "script" || node.localName === "style") {
      return;
    }
    TEXT_ATTRS.forEach((name) => { if (node.hasAttribute(name)) { attrs.push([name, node.getAttribute(name)]); } });
    node.childNodes.forEach(walk);
  };
  walk(root);
  return { text, attrs };
}

/* The page's parts in #main by the keys app.js gives them, or for the two form pages (Settings, About) by their place. */
function mainParts(doc, page) {
  const main = doc.getElementById("main");
  const parts = [];
  const kept = KEPT_CARDS[page];
  main.children.forEach((el) => {
    if (el.__key === "grid") {
      el.children.forEach((c) => { if (kept === "*" || (kept && kept.indexOf(c.__key) >= 0)) { parts.push(["card:" + c.__key, c]); } });
    } else if (el.__key) {
      parts.push(["top:" + el.__key, el]);
    } else if (!el.classList.contains("nm-pgrid") && !el.classList.contains("nm-grid")) {
      parts.push(["top:" + (el.classList.contains("nm-head") ? "head" : el.id || el.className), el]);
    }
  });
  return parts;
}

function readView(view, page, withChrome) {
  const rules = rulesOf(view.look.theme);
  const memo = new Map();
  const out = {};
  if (withChrome) {
    CHROME.forEach(([name, sel]) => { out["chrome:" + name] = region(view.doc.querySelector(sel), rules, memo); });
  }
  mainParts(view.doc, page).forEach(([name, el]) => { out[name] = region(el, rules, memo); });
  return out;
}

/* Gates before its first state: the same view once gates.js has run over it (it comes with the state's theme in the app). */
async function gatesOver(view, page, withChrome) {
  script("themes/gates.js").runInContext(view.box);
  view.box.USAGE_MONITOR_THEME_FX.gates.start();
  await settle(view);
  return readView(view, page, withChrome);
}

async function run() {
  const views = {};
  const problems = [];
  for (const c of CASES) {
    for (const look of LOOKS) {
      for (const [i, page] of c.pages.entries()) {
        // a fresh page for every page of a case, so what one page left behind never colours the next (and Gates before
        // its first state is read twice, before and after gates.js)
        const w = world();
        w.state.theme = themeView(w, look);
        Object.assign(w.state.settings, { theme_id: look.theme, theme_variant: look.variant });
        if (c.world) { c.world(w); }
        const seen = [];
        const view = load(w, look, seen);
        await settle(view);
        await open(view, page);
        for (const s of c.steps || []) { await step(view, s); }
        const key = c.name + "|" + look.name + "|" + page;
        const shown = readView(view, page, i === 0);
        if (look.theme === "gates" && !view.box.USAGE_MONITOR_THEME_FX) {
          const after = await gatesOver(view, page, i === 0);
          Object.keys(shown).forEach((name) => { shown[name].gates = after[name] ? after[name].text : null; });
        }
        views[key] = shown;
        seen.forEach((p) => problems.push(key + ": " + p));
      }
    }
  }
  return { views, problems };
}

/* The record: every distinct text once, each view's regions as [text, attributes] indexes. */
function pack(views) {
  const strings = [];
  const index = new Map();
  const ref = (value) => {
    const text = typeof value === "string" ? value : JSON.stringify(value);
    if (!index.has(text)) { index.set(text, strings.length); strings.push(text); }
    return index.get(text);
  };
  const packed = {};
  Object.keys(views).forEach((key) => {
    packed[key] = {};
    Object.keys(views[key]).forEach((name) => {
      const r = views[key][name];
      packed[key][name] = r.gates === undefined ? [ref(r.text), ref(r.attrs)] : [ref(r.text), ref(r.attrs), r.gates === null ? null : ref(r.gates)];
    });
  });
  return { strings, views: packed };
}

/* The record as JSON with one case, string or view a line, so a change reads as a diff of the lines it touches. */
function format(record) {
  const list = (items) => items.map((item, i) => "  " + item + (i < items.length - 1 ? "," : "")).join("\n");
  return "{\n" +
    ' "_about": ' + JSON.stringify(record._about) + ",\n" +
    ' "looks": ' + JSON.stringify(record.looks) + ",\n" +
    ' "cases": [\n' + list(record.cases.map((c) => JSON.stringify(c))) + "\n ],\n" +
    ' "strings": [\n' + list(record.strings.map((s) => JSON.stringify(s))) + "\n ],\n" +
    ' "views": {\n' + list(Object.keys(record.views).map((k) => JSON.stringify(k) + ": " + JSON.stringify(record.views[k]))) + "\n }\n}\n";
}

// ── the header before the first state, and the themes' entrances (beside the record, not in it) ─────────────────────

/* The header and the health box before the monitor has answered (#98's critic 10, 11 and 17), in each look: as the page
 * opens, after its first 1 s tick, and after a first poll that failed; and the shell's own words, in index.html. */
async function firstChrome(problems) {
  const words = (doc) => ({
    uplink: doc.getElementById("uplink-text").textContent, uplinkLang: doc.getElementById("uplink-text").getAttribute("lang"),
    uplinkClass: doc.getElementById("uplink").className, dotClass: doc.getElementById("uplink-dot").className,
    age: doc.getElementById("data-age").textContent, ageLang: doc.getElementById("data-age").getAttribute("lang"),
    health: doc.getElementById("health").textContent,
  });
  const out = { shell: words(D.createDocument(read("index.html"))), looks: {} };
  for (const look of LOOKS) {
    const moments = {};
    for (const [moment, state, tick] of [["opened", "hang", 0], ["tick", "hang", 1000], ["failed", "fail", 0]]) {
      const w = world();
      w.state.theme = themeView(w, look);
      w.stateAnswer = state;
      allReports(w, "hang");
      const view = load(w, look, problems);
      await settle(view);
      if (tick) { await advance(view, tick); }
      moments[moment] = words(view.doc);
    }
    out.looks[look.name] = moments;
  }
  return out;
}

const norm = (text) => String(text || "").replace(/\s+/g, " ").trim();

/* What each theme's entrance animates (#98's critic 2): every element its director names (its head and card groups, read
 * from the theme script itself), on Overview and Limits (live and before the first state) and on every page's heading, in
 * English and on a French page with English's words. Nostromo's typer types an element's first text node
 * (FX.text.firstText), Night City's decoder its own text nodes (FX.text.directText): words that sit in a child instead (a span
 * for a name or a language) show at once while the rest moves. Gives what was checked, and each element whose animated text
 * is not all of its text. */
async function entrance(problems) {
  const out = { checked: {}, untyped: [] };
  const runs = [];
  for (const lang of ["en", "fr"]) {
    ALL_PAGES.forEach((page) => runs.push({ lang, caseName: "live", page }));
    STAT_PAGES.forEach((page) => runs.push({ lang, caseName: "before-state", page }));
  }
  for (const look of LOOKS.filter((l) => l.name === "nostromo" || l.name === "night_city")) {
    for (const r of runs) {
      const w = world();
      w.state.theme = themeView(w, look);
      Object.assign(w.state.settings, { theme_id: look.theme, theme_variant: look.variant });
      if (r.caseName === "before-state") { w.stateAnswer = "hang"; allReports(w, "hang"); }
      const view = load(w, look, problems, { lang: r.lang, motion: true });
      await settle(view);
      await open(view, r.page);
      if (!view.box.__directors[look.theme]) {
        script("themes/" + look.theme + ".js").runInContext(view.box);      // before the first state the theme script waits
      }
      const FX = view.box.USAGE_MONITOR_FX;
      const director = view.box.__directors[look.theme];
      const key = look.name + "|" + r.lang + "|" + r.caseName + "|" + r.page;
      out.checked[key] = {};
      const main = view.doc.getElementById("main");
      for (const group of (director.head || []).concat(director.card || [])) {
        let count = 0;
        for (const el of main.querySelectorAll(group.sel)) {
          const all = norm(el.textContent);
          if (!all) { continue; }
          count += 1;
          const first = look.theme === "nostromo" ? FX.text.firstText(el) : null;
          const moved = look.theme === "nostromo" ? norm(first ? first.nodeValue : "") : norm(FX.text.directText(el));
          if (moved !== all) { out.untyped.push({ view: key, sel: group.sel, text: all, moved }); }
        }
        out.checked[key][group.sel] = count;
      }
    }
  }
  return out;
}

run().then(async ({ views, problems }) => {
  const record = {
    _about: ["The dashboard's English before #98 moved its words into the catalog: tests/page_words.js on the code at c05f45d, " +
      "with TZ=UTC. Each view is case|look|page; each region [text, attributes] (and, in Gates before the first state, the text " +
      "once gates.js has run), as indexes into strings. Never re-recorded."],
    looks: LOOKS, cases: CASES.map((c) => ({ name: c.name, pages: c.pages })), ...pack(views),
  };
  if (RECORD) {
    if (fs.existsSync(FIXTURE)) {
      throw new Error(FIXTURE + " exists: the record is made once, from the code before the change");
    }
    if (problems.length) {
      throw new Error("the page met problems; nothing recorded:\n" + problems.slice(0, 20).join("\n"));
    }
    fs.writeFileSync(FIXTURE, format(record));
    process.stdout.write(JSON.stringify({ recorded: FIXTURE, views: Object.keys(views).length, strings: record.strings.length }) + "\n");
    return;
  }
  const first = await firstChrome(problems);
  const moving = await entrance(problems);
  process.stdout.write(JSON.stringify({ ...record, problems, first, entrance: moving }));
}).catch((e) => {
  console.error(e && e.stack ? e.stack : e);
  process.exit(1);
});
