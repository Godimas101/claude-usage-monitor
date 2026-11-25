"use strict";
/* Runs the dashboard's real scripts (formats.js, charts.js, app.js) under node and prints what they do as one JSON
 * object.
 *
 * tests/test_webui.py starts this with `node tests/webui_checks.js <webui folder>` and asserts on the result.
 * app.js is loaded in a vm context with a small fake DOM and window.__USAGE_MONITOR_TEST__ set, so it hands back its
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

// The catalog the server builds from en.json (#87; i18n.page_script). Every page here gets it, as in the app, unless a
// check gives another one, or none.
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

function load(query, globals) {
  const elements = {};
  const doc = {
    readyState: "complete", visibilityState: "visible", documentElement: element("html"), head: element("head"),
    body: svgElement("body"), querySelector: query || (() => null), querySelectorAll: () => [],
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
  box.MonitorI18n = EN_CATALOG;                    // what /static/i18n/en.js sets before app.js runs (#87)
  Object.assign(box, globals || {});               // what the page's other scripts set first: another catalog, or none
  vm.createContext(box);
  for (const name of ["formats.js", "charts.js", "app.js"]) {
    vm.runInContext(fs.readFileSync(path.join(webui, name), "utf8"), box, { filename: name });
  }
  return { api: box.__USAGE_MONITOR_TEST__.api, charts: box.MonitorCharts, elements, box };
}

const out = {};
// node's ICU and CLDR: test_webui.py keys a figure by the CLDR version that writes it, where versions differ.
out.versions = { node: process.version, icu: process.versions.icu, cldr: process.versions.cldr };
const { api, charts, elements, box } = load();
const S = api.S;
const NOW = Date.UTC(2026, 9, 21, 16, 12);

function reset() {
  S.data = null; S.stats = {}; S.settings = {}; S.linkOk = null;
}

// ── languages (#87): the catalog, the locale the monitor chose, the Language row, a reload on a change ──────
{
  const catalog = {
    language: "fr",
    strings: { "k.own": "Clé {name}", "k.plural": { one: "{count} session", many: "{count} de sessions", other: "{count} sessions" } },
    fallback: {
      "k.own": "Key {name}", "k.en": "Session <b> & {x}", "k.plural": { one: "{count} session", other: "{count} sessions" },
      "k.enplural": { one: "{count} session", other: "{count} sessions" },
      "settings.language.label": "Language", "settings.language.help": "Help", "settings.language.system": "System default ({language})",
      "settings.language.apply": "Apply",
    },
  };
  const localeMeta = (content) => (sel) => (sel === 'meta[name="monitor-locale"]' ? { getAttribute: (k) => (k === "content" ? content : null) } : null);
  const french = { setting: "fr", language: "fr", system: "tr", format: "fr-CA", clock: 24, offered: ["en", "fr", "tr"] };
  const holder = { select: null };
  const frenchQuery = (sel) => (sel === "#set-language" ? holder.select : localeMeta(JSON.stringify(french))(sel));
  const loaded = load(frenchQuery, { MonitorI18n: catalog });
  const fr = loaded.api;
  out.i18n = {
    own: fr.t("k.own", { name: "x" }), ownLang: fr.tLang("k.own"),
    english: fr.t("k.en", { x: 1 }), englishLang: fr.tLang("k.en"),
    missing: fr.t("no.such.key"), unfilled: fr.t("k.own"),
    html: fr.tHtml("k.en", { x: "<i>" }), htmlMarkup: fr.tHtml("k.own", { name: fr.markup("<code>claude</code>") }),
    htmlForged: fr.tHtml("k.own", { name: { html: "<b>x</b>" } }),       // data shaped like markup is still data
    code: fr.tCode("claude --resume <id>").html, path: fr.tPath("C:\\Users\\t & co").html, name: fr.tName("Opus <4>").html,
    pluralZero: fr.tPlural("k.plural", 0), pluralTwo: fr.tPlural("k.plural", 2), pluralMillion: fr.tPlural("k.plural", 1000000),
    pluralFallbackZero: fr.tPlural("k.enplural", 0),                       // English text, English rules: 0 is other
    row: fr.languageRow(), locale: fr.LOCALE, known: fr.LOCALE_KNOWN, names: fr.LANGUAGE_NAMES,
  };
  // What counts as a change of locale: the language, the format and the clock, nothing else.
  out.i18nChanged = [
    fr.localeChanged({ ...french }), fr.localeChanged(null), fr.localeChanged({}),
    fr.localeChanged({ ...french, language: "en" }), fr.localeChanged({ ...french, format: "fr-FR" }),
    fr.localeChanged({ ...french, clock: 12 }), fr.localeChanged({ ...french, setting: "system", system: "en", offered: ["en"] }),
  ];
  // The list keeps a pick that is not applied yet, and follows a stored change.
  {
    const attrs = { "data-synced": "fr" };
    const select = {
      value: "tr", options: [{ value: "system" }, { value: "en" }, { value: "fr" }, { value: "tr" }],
      getAttribute: (k) => (k in attrs ? attrs[k] : null), setAttribute: (k, v) => { attrs[k] = String(v); },
    };
    const main = { querySelector: (sel) => (sel === "#set-language" ? select : null) };
    fr.S.data = { locale: { ...french } };
    fr.syncLanguage(main);
    const kept = select.value;
    fr.S.data = { locale: { ...french, setting: "system" } };
    fr.syncLanguage(main);
    out.i18nSync = { kept, followed: select.value, synced: attrs["data-synced"] };
  }
  // Apply saves the pick; the next state brings the reload.
  {
    const sent = [];
    loaded.box.fetch = (url, opts) => {
      sent.push({ url, method: opts.method, body: JSON.parse(opts.body) });
      return new Promise(() => {});
    };
    holder.select = { value: "tr" };
    fr.applyLanguage();
    out.i18nApply = sent.splice(0);
    holder.select = { value: "es" };                       // and through the button's click, as the dashboard does it
    fr.settingsClick({ target: { closest: () => ({ hasAttribute: () => false, getAttribute: (n) => (n === "data-action" ? "language-apply" : null) }) } });
    out.i18nApplyClick = sent.splice(0);
  }
  // What a new state does first, on a page that knows its locale: nothing for its own locale or none, a reload for another.
  {
    const page = load(localeMeta(JSON.stringify(french)), { MonitorI18n: catalog });
    let count = 0;
    page.box.location.reload = () => { count += 1; };
    out.i18nLocaleReload = [{ locale: { ...french } }, {}, { locale: null }, { locale: { ...french, clock: 12 } }].map((data) => {
      page.api.S.data = data;
      return [page.api.localeReload(), count];
    });
  }
  // A change of locale reloads the page once.
  const reloads = [];
  loaded.box.location.reload = () => reloads.push("reload");
  fr.S.data = { locale: { ...french, language: "en", format: "en-US", clock: 12 }, settings: {}, theme: null };
  fr.onState();
  fr.onState();
  out.i18nReloads = reloads.length;
  // Never twice in a row for the same locale in a window (its name carries the mark over the reload); never on a page
  // that did not know its own locale.
  const reloadIn = (meta, name) => {
    const page = load(localeMeta(meta), { MonitorI18n: catalog, name });
    let count = 0;
    page.box.location.reload = () => { count += 1; };
    const answered = page.api.reloadForLocale({ language: "en", format: "en-US", clock: 12 });
    return { answered, count, name: page.box.name };
  };
  const first = reloadIn(JSON.stringify(french), "");
  out.i18nMarks = [first, reloadIn(JSON.stringify(french), first.name),
                   reloadIn(JSON.stringify(french), "usage-monitor-locale-reload:fr|fr-FR|24")];
  out.i18nUnknownPage = [reloadIn("{not json", ""), reloadIn("", ""), reloadIn(JSON.stringify({ format: "x" }), "")];
  // A page that arrives in the locale it was reloaded for clears the mark; one that did not keeps it.
  out.i18nArrived = load(localeMeta(JSON.stringify(french)), { MonitorI18n: catalog, name: "usage-monitor-locale-reload:fr|fr-CA|24" }).box.name;
  out.i18nNotArrived = load(localeMeta(JSON.stringify(french)), { MonitorI18n: catalog, name: "usage-monitor-locale-reload:en|en-US|12" }).box.name;
  // A dev instance showing a language that is not offered lists it too.
  out.i18nDevRow = load(localeMeta(JSON.stringify({ setting: "system", language: "fr", system: "en", format: "fr-FR", clock: 24,
                                                     offered: ["en"] })), { MonitorI18n: catalog }).api.languageRow();
  const badMeta = load(localeMeta("{not json")).api;
  out.i18nBadMeta = badMeta.LOCALE;
  out.i18nBadMetaKnown = badMeta.LOCALE_KNOWN;
}
out.i18nEnglishRow = api.languageRow();
out.i18nDefaultLocale = api.LOCALE;
out.i18nDefaultCatalog = api.t("settings.language.label");
out.i18nEnglishHtml = api.tHtml("settings.language.label");
out.i18nNoCatalog = load(null, { MonitorI18n: undefined }).api.t("settings.language.label");
// The page's plural rules, for test_webui.py to hold against i18n.plural_category.
out.i18nPlurals = [];
for (const language of ["en", "fr", "es", "tr"]) {
  for (const n of [0, 1, 1.5, 2, 5, -1, -2, 0.5, 1000000, 2000000, 1000001, Infinity, -Infinity, NaN]) {
    out.i18nPlurals.push([language, String(n), api.pluralCategory(language, n)]);
  }
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
  return [...svg.matchAll(/<text class="mon-t" x="0" y="[\d.]+">([^<]*)<\/text>/g)].map((m) => m[1]);
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
  .match(/<rect class="mon-track" x="[\d.]+" y="[\d.]+" width="([\d.]+)"/)[1];
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

// ── formats (#91): formats.js on its own, in English, the pseudo-locale and the languages' format tags ─────────
{
  const source = fs.readFileSync(path.join(webui, "formats.js"), "utf8");
  // formats.js in a context of its own; meta is the monitor-locale meta's content (none: no meta, undefined: no page).
  const formatsIn = (meta) => {
    const b = {};
    if (meta !== undefined) {
      const found = meta === null ? null : { getAttribute: (k) => (k === "content" ? meta : null) };
      b.document = { querySelector: (sel) => (sel === 'meta[name="monitor-locale"]' ? found : null) };
    }
    b.window = b;
    vm.createContext(b);
    vm.runInContext(source, b, { filename: "formats.js" });
    return b.MonitorFormats;
  };
  const M = formatsIn(undefined);
  const of = (language, format) => M.make({ setting: "system", language, system: "en", format, clock: 24, offered: ["en"] });
  const sample = (F) => ({
    tag: F.tag, english: F.english, signs: [F.group, F.decimal],
    number: [F.number("1234.5"), F.number("1234.5", { group: false }), F.number(1234.5), F.number(1234.5, { digits: 2 }),
      F.number(-0), F.number("-0.0"), F.number("x"), F.number("1234"), F.number("12345")],
    percent: [F.percent("34"), F.percent("33.5"), F.percent("-34"), F.percent("34", { tight: true })],
    parts: [F.percentParts("34"), F.percentParts("-34"), F.percentParts("34", { tight: true })],
    usd: [F.usd("5.96"), F.usd("-5.96"), F.usd("1235"), F.usd(1234.5, { digits: 2 }), F.usd(-1234.5, { digits: 0 }),
      F.usd("1.2", { scale: 3 }), F.usd("-1.2", { scale: 3 }), F.usd("5.96", { tight: true }),
      F.usd("1.2", { scale: 3, tight: true })],
    money: [F.money(5.96, "USD"), F.money(5.96, "EUR"), F.money(5.96, "EURO"), F.money(1234.5, "USD")],
    compact: [F.compact("1.5", 3), F.compact("1.5", 6), F.compact("-1.5", 3)],
  });
  out.formats = {};
  for (const [name, language, format] of [["en", "en", "en-US"], ["pseudo", "pseudo", "en-US"], ["frFR", "fr", "fr-FR"],
    ["frCA", "fr", "fr-CA"], ["frCH", "fr", "fr-CH"], ["esES", "es", "es-ES"], ["esMX", "es", "es-MX"],
    ["trTR", "tr", "tr-TR"]]) {
    out.formats[name] = sample(of(language, format));
  }
  // The tag Intl gets: the format tag of the same language, else the language's base; English for anything else.
  out.formatsTags = [of("fr", "en-US").tag, of("fr", "").tag, of("fr", "fr_CA").tag, of("tr", "not a tag").tag,
    of("es", "es-419").tag, of("de", "de-DE").tag, of("en", "fr-FR").tag, M.make(null).tag, M.make("fr").tag,
    M.make({ language: 7 }).tag];
  // active(): the page's own formats, from the monitor-locale meta; English without one or with one it cannot read.
  out.formatsActive = [JSON.stringify({ language: "tr", format: "tr-TR", clock: 24 }), null, "{not json", "",
    JSON.stringify({ format: "fr-FR" }), undefined].map((meta) => {
    const formats = formatsIn(meta);
    const active = formats.active();
    return [active.tag, active.percent("34"), formats.active() === active];
  });
}

// ── the page's figures in the locale (#91): numbers, percents, money, compact figures, the odometer ──────────
{
  const localeMeta = (locale) => (sel) => (sel === 'meta[name="monitor-locale"]'
    ? { getAttribute: (k) => (k === "content" ? JSON.stringify(locale) : null) } : null);
  const pageIn = (language, format) => load(localeMeta({ setting: "system", language, system: language, format,
    clock: 24, offered: ["en", language] })).api;
  const odometer = (html) => [(html.match(/aria-label="([^"]*)"/) || [])[1],
    Array.from(html.matchAll(/data-d="([^"]*)"|<span class="nm-od-sep"[^>]*>([^<]*)</g), (m) => (m[1] !== undefined ? m[1] : m[2])).join(""),
    (html.match(/class="nm-od-sep"/g) || []).length];
  const figures = (a) => {
    a.S.nextCharts = {};
    a.pacingChart({ window_start: "2026-10-15T16:00:00Z", resets_at: "2026-10-22T16:00:00Z", percent: 40, verdict: "AHEAD",
      points: [["2026-10-16T16:00:00Z", 10], ["2026-10-20T16:00:00Z", 133.3]] }, "weekly", NOW, "d", false);
    const ticks = Object.keys(a.S.nextCharts).map((id) => Array.from(String(a.S.nextCharts[id].build(500))
      .matchAll(/<text[^>]*>([^<]*%[^<]*)<\/text>/g), (m) => m[1]))[0];
    return {
      percent: [a.fmtPct(33.5, 1), a.fmtPct(34), a.fmtRate(0.335), a.pctWords(34.5), a.fmtPct(-0.4)],
      usd: [a.fmtUsd(5.96), a.fmtUsd(-5.96), a.fmtUsd(1234.5), a.fmtUsdExact(1234.5), a.fmtUsdShort(1234.5),
        a.fmtPlanPrice(100), a.fmtPlanPrice(17.5)],
      tile: [a.fmtUsdTile(5.96), a.fmtUsdTile(-5.96), a.fmtUsdTile(123), a.fmtUsdTile(1234.5), a.fmtUsdTile(1234567)],
      hero: [a.fmtUsdHero(5.96), a.fmtUsdHero(1234.5), a.fmtUsdHero(12345), a.fmtUsdHero(1234567)],
      money: [a.fmtMoney(5.96, "usd"), a.fmtMoney(5.96, "EUR"), a.fmtMoney(5.96, "EURO")],
      count: [a.fmtCount(1500), a.fmtCount(1.5e6), a.fmtCount(999), a.fmtInt(1234), a.fmtInt(12345), a.fmtMult(6.04),
        a.fmtCompare(1234.5), a.fmtCompare(12.34), a.signed(-3.25, 1), a.plural(1234, "request")],
      prose: [a.weeklyStory({ verdict: "AHEAD", pace_delta_pp: 12.345 }).text,
        a.weeklyForecast({ percent: 40, projected_end_pct: 33.3, daily_budget_pct: 14.28 }),
        (a.efficiencyCard({ hit: 0.3345, thinking: 0.125, saved: 5.96, loaded: true }).match(/class="nm-big m">([^<]*)</) || [])[1],
        a.comparisonList({ comparisons: [{ key: "page_stack_m", value: 1234.5 }] })[0].n],
      ticks: ticks,
      odometer: [odometer(a.odometerHTML(1234567, false)), odometer(a.odometerHTML(null, true))],
    };
  };
  out.pageFormats = {};
  for (const [name, language, format] of [["frFR", "fr", "fr-FR"], ["frCA", "fr", "fr-CA"], ["esES", "es", "es-ES"],
    ["esMX", "es", "es-MX"], ["trTR", "tr", "tr-TR"], ["pseudo", "pseudo", "en-US"], ["en", "en", "en-US"]]) {
    out.pageFormats[name] = figures(pageIn(language, format));
  }
  // The money boxes sized for English's figures (fmtUsdTile, fmtUsdHero), at the amounts where English's own figure has
  // six characters at most, in every format tag the test names (tests/budgets.py's PAGE_MONEY is held to these).
  const TILE_AT = [0, 5.96, 99.99, 100, 999, 1000, 9949, 99949, 1e6, 99.94e6, -9.99, -100, -999, -1000, -9949, -1e6, -9.94e6];
  const HERO_AT = [0, 5.96, 99.99, 100, 999, 9999, 10000, 99949, 1e6, 99.94e6, -9.99, -100, -999];
  const tags = process.argv[3] ? JSON.parse(process.argv[3]) : ["fr-FR", "es-ES", "tr-TR"];
  const english = pageIn("en", "en-US");
  out.pageMoney = { english: TILE_AT.map(english.fmtUsdTile).concat(HERO_AT.map(english.fmtUsdHero)), tags: {} };
  for (const tag of tags) {
    const a = pageIn(tag.split("-")[0], tag);
    out.pageMoney.tags[tag] = TILE_AT.map(a.fmtUsdTile).concat(HERO_AT.map(a.fmtUsdHero));
  }
}

// ── the page's dates, times, durations, lists and case in the locale (#91, steps 2.3 to 2.7) ─────────────────
{
  const localeMeta = (locale) => (sel) => (sel === 'meta[name="monitor-locale"]'
    ? { getAttribute: (k) => (k === "content" ? JSON.stringify(locale) : null) } : null);
  const pageIn = (language, format, clock) => load(localeMeta({ setting: "system", language, system: language, format,
    clock, offered: ["en", language] }));
  // Local wall times, so the answers do not depend on the zone the test runs in: today at 16:12 and 9:05 (a same-day clock
  // has no weekday), and Saturday 2026-10-24 at 16:12.
  const todayAt = (h, m) => { const d = new Date(); d.setHours(h, m, 0, 0); return d.getTime(); };
  const SAT = new Date(2026, 9, 24, 16, 12).getTime();
  const texts = (svg) => Array.from(String(svg).matchAll(/<text[^>]*>([^<]*)<\/text>/g), (m) => m[1]);
  const dates = (page) => {
    const a = page.api;
    const days = [];
    for (let i = 0; i < 120; i++) {
      const d = new Date(2026, 0, 1 + i);
      days.push({ date: d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"), value: i % 5, tip: "t" });
    }
    return {
      constants: { DAYS: a.DAYS, WEEKDAYS_MON: a.WEEKDAYS_MON, MONTHS: a.MONTHS },
      clock: [a.clockText(todayAt(16, 12)), a.clockText(todayAt(9, 5)), a.clockText(SAT)],
      stamp: a.stampText(SAT), day: [a.dayText("2026-10-24"), a.dayText("2026-10-24", true)], date: a.dateText(SAT),
      hour: [a.hourText(new Date(2026, 9, 24, 16).getTime()), a.hourText(new Date(2026, 9, 24, 9).getTime())],
      range: a.rangeText("2026-04-01T12:00:00", "2026-10-24T12:00:00"), hourRange: a.hourRange(16),
      labels: [a.dayLabel("2026-04-09"), a.monthLabel("2026-10"), a.monthLabel("2027-01")],
      calendar: texts(page.charts.calendar(days, { width: 1000, maxCell: 22, legend: ["LESS", "MORE"] })).filter((t) => !/^(LESS|MORE)$/.test(t)),
      remain: [a.fmtRemain(183600000), a.fmtRemain(172800000), a.fmtRemain(12420000), a.fmtRemain(3600000), a.fmtRemain(1620000),
        a.fmtRemain(30000)],
      age: [0, 12000, 180000, 3600000, 10800000, 172800000].map((ms) => [a.ageWords(a.fmtAge(ms), a.AGE_UNIT),
        a.ageWords(a.fmtAge(ms), a.AGE_UNIT_IN_SENTENCE)]),
      list: [a.listWords([]), a.listWords(["A"]), a.listWords(["A", "B"]), a.listWords(["A", "B", "C"])],
      plural: [a.plural(1, "session"), a.plural(1234, "request")],
      // a name in capitals (the model mix, the surfaces), and English words in capitals (whoWords, a status line)
      named: [(a.modelMixCard({ models: [{ id: "claude-haiku-4-5", name: "Haiku 4.5", share: 50 }], loaded: true })
        .match(/<span class="k"[^>]*>([^<]*)</) || [])[1],
        (a.surfaceCard({ breakdown: [{ key: "chrome", name: "Claude in Chrome", percent: 50 }] }).match(/<span class="k">([^<]*)</) || [])[1]],
      english: [a.whoWords({ agents: 2 }), a.workWords({ session: 2 }),
        a.failedText("CHECK FAILED", { ok: false, data: { error: "signing is missing" } })],
    };
  };
  out.pageDates = {};
  for (const [name, language, format, clock] of [["en", "en", "en-US", 12], ["pseudo", "pseudo", "en-US", 12],
    ["frFR", "fr", "fr-FR", 24], ["frFR12", "fr", "fr-FR", 12], ["frCA", "fr", "fr-CA", 24], ["esES", "es", "es-ES", 24],
    ["esMX12", "es", "es-MX", 12], ["trTR", "tr", "tr-TR", 24], ["trTR12", "tr", "tr-TR", 12]]) {
    out.pageDates[name] = dates(pageIn(language, format, clock));
  }
  // charts.js without the formats layer (a page that has none) keeps English's names
  {
    const b = { document: { getElementById: () => ({}) } };            // its style is there; no text to measure
    b.window = b;
    vm.createContext(b);
    vm.runInContext(fs.readFileSync(path.join(webui, "charts.js"), "utf8"), b, { filename: "charts.js" });
    const days = [];
    for (let i = 0; i < 90; i++) {
      const d = new Date(2026, 11, 1 + i);
      days.push({ date: d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"), value: 1, tip: "t" });
    }
    out.chartsAlone = texts(b.MonitorCharts.calendar(days, { width: 1000, maxCell: 22, legend: ["LESS", "MORE"] }));
  }
  // formats.js's own case rules, by a language or a tag; English has no duration of its own (it throws, as formats.py's)
  {
    const F = pageIn("tr", "tr-TR", 24).box.MonitorFormats;
    const tr = F.make({ language: "tr", format: "tr-TR", clock: 24 });
    const en = F.make({ language: "en", format: "en-US", clock: 12 });
    let threw = null;
    try {
      en.duration([[1, "h"]]);
    } catch (e) {
      threw = e.message;
    }
    out.formatsCase = {
      tr: [tr.upper("istanbul \u0131l\u0131k"), tr.lower("\u0130STANBUL ILIK"), tr.lower("I\u0307"), tr.upper("i", "en"), tr.lower("I", "en")],
      en: [en.upper("i"), en.upper("i", "tr-TR"), en.upper("i", "az"), en.lower("I", "tr_TR"), en.lower("\u0130")],
      hours: [tr.hours, en.hours, F.make({ language: "fr", format: "fr-FR", clock: 12 }).hours, F.make({ language: "fr", format: "fr-FR" }).hours,
        F.make({ language: "pseudo", format: "en-US", clock: 24 }).hours],
      englishDuration: threw,
    };
  }
}

// ── #91's review: billions, other currencies, the alert levels, the pending money notes, the month labels ───
{
  const meta = (locale) => ({ getAttribute: (k) => (k === "content" ? JSON.stringify(locale) : null) });
  // A page in a format tag; input stands for the alert level field (#alert-add) the Settings page has.
  const pageIn = (tag, input) => {
    const found = meta({ setting: "system", language: tag.split("-")[0], system: tag.split("-")[0], format: tag, clock: 24,
      offered: ["en", tag.split("-")[0]] });
    return load((sel) => (sel === 'meta[name="monitor-locale"]' ? found : sel === "#alert-add" ? input || null : null));
  };
  const TAGS = ["en-US", "fr-FR", "fr-CA", "es-ES", "es-MX", "es-US", "tr-TR"];
  out.review = { billions: {}, money: {}, levels: {}, pending: {}, months: {} };
  const MONTHS_OF = (year) => Array.from({ length: 12 }, (_, i) => year + "-" + String(i + 1).padStart(2, "0"));
  const labelsIn = (svg) => [...String(svg).matchAll(/<text class="mon-t" x="0" y="[\d.]+">([^<]*)<\/text>/g)].map((m) => m[1]);
  for (const tag of TAGS) {
    const input = { value: "80" };
    const page = pageIn(tag, input);
    const a = page.api;
    // a count in the billions: Spanish writes it in millions ("2254 M"), with the digits of the unrounded count
    out.review.billions[tag] = [2.254e9, 9.995e9, 1.5e9, 1.25e10, -2.254e9].map(a.fmtCount);
    // real money in another currency is written with its code, never with the plain "$" of the US-dollar figures
    out.review.money[tag] = [a.fmtMoney(5.96, "cad"), a.fmtMoney(5.96, "MXN"), a.fmtMoney(-1234.5, "EUR"), a.fmtUsd(5.96)];
    // the alert levels' chips and the error for a level that is set already
    a.S.settings = { alert_thresholds: [80, 50], alerts_enabled: true };
    const chips = element("alert-chips");
    a.syncAlerts({ querySelector: (sel) => (sel === "#alert-chips" ? chips : null), querySelectorAll: () => [] });
    page.elements["err-alert_thresholds"] = element("err-alert_thresholds");
    a.addLevel();
    out.review.levels[tag] = {
      chips: [...chips.innerHTML.matchAll(/<b>([^<]*)<\/b>/g)].map((m) => m[1]),
      labels: [...chips.innerHTML.matchAll(/aria-label="([^"]*)"/g)].map((m) => m[1]),
      error: page.elements["err-alert_thresholds"].textContent,
    };
    // the money a pending note shows until its figure comes, beside the figure that comes
    a.S.stats = {};
    a.S.nextCharts = {};
    out.review.pending[tag] = [(a.dailyChart(null).match(/<span class="nm-meta-t"[^>]*>([^<]*)</) || [])[1],
      (a.costToolbar("week", null, false).match(/<span class="nm-toolbar-n"[^>]*>([^<]*)</) || [])[1],
      (a.costToolbar("week", 123.45, false).match(/<span class="nm-toolbar-n"[^>]*>([^<]*)</) || [])[1]];
    // the month-by-month chart's labels, measured with charts.js's own estimate (7.9 px a character, wider than the
    // themes' fonts): none is cut, and English's label column stays 64 px
    pxPerChar = 7.9;
    page.charts.refresh();
    a.S.nextCharts = {};
    const monthly = MONTHS_OF(2026).map((month, i) => ({ month, cost_usd: 100 + i, multiplier: 1.5, requests: 10, tokens: 1e6 }));
    a.monthlyCard({ monthly }, { digest: "d" });
    const built = Object.keys(a.S.nextCharts).map((id) => a.S.nextCharts[id].build(500))[0];
    const rows = monthly.map((m) => ({ label: a.monthLabel(m.month), value: m.cost_usd, text: "x" }));
    out.review.months[tag] = { labels: labelsIn(built), full: rows.map((r) => r.label),
      bars: (String(built).match(/<rect class="mon-(?:track|mark)[^"]*" x="([\d.]+)"/) || [])[1],
      plain: (page.charts.hbars(rows, { width: 500, labelWidth: 64 })
        .match(/<rect class="mon-(?:track|mark)[^"]*" x="([\d.]+)"/) || [])[1] };
    pxPerChar = 6;
    page.charts.refresh();
  }
}

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
// ── Part 3 (#90): the scripts read ids, never the words on the page ─────────────────────────────────────────
// Every card names itself by data-card (gates.css picks its icons by it), the Settings and About cards too, and the
// Limits wrapper's two inner sections.
const cardKeyOf = (html) => { const m = /^<\w+[^>]*data-card="([^"]*)"/.exec(html); return m ? m[1] : null; };
const keysIn = (html) => (html.match(/data-card="[^"]*"/g) || []).map((attr) => attr.slice(11, -1));
const pageHtml = (fn) => {
  const main = element("main");
  S.formKey = null;
  try { fn(main); } catch (error) { /* the page asks for the guard once it is written; the fetch is refused here */ }
  return main.innerHTML;
};
reset();
out.cards = {};
for (const page of PAGE_LIST) out.cards[page] = modelOf(page).cards.map((c) => cardKeyOf(c.html));
out.cards.settings = keysIn(pageHtml(api.settingsPage));
out.cards.about = keysIn(pageHtml(api.aboutPage));
reset();
S.noActivity = true;                                                              // no Claude Code activity: the one explanatory card
out.cards.none = modelOf("cost").cards.map((c) => cardKeyOf(c.html));
reset();
S.noActivity = false;
S.data = { state: { status: "live", plan: { name: "Max 5x", monthly_price_usd: 100 }, windows: {}, ingest: null,
  unpriced_models: ["mystery-model"], usage: { limits: [], breakdown: [], spend: null } }, model_names: {} };
out.cards.unpriced = modelOf("cost").cards.map((c) => cardKeyOf(c.html));
reset();
out.tokensTotal = (modelOf("efficiency").cards.filter((c) => c.key === "tokens")[0].html.match(/<tr class="([^"]*)"><th scope="row" class="lead">Total<\/th>/) || [])[1] || null;
out.tokensToday = (modelOf("efficiency").cards.filter((c) => c.key === "tokens")[0].html.match(/is-today/g) || []).length;
S.data = { state: { status: "live", plan: { name: "Max 5x", monthly_price_usd: 100 }, windows: {}, ingest: null, unpriced_models: [],
  usage: { limits: [liveLimit("session", "SESSION", 13), liveLimit("weekly_all", "WEEKLY", 2), liveLimit("weekly_scoped", "SCOPED", 0, "Fable")], breakdown: [], spend: null } }, model_names: {} };
out.cards.limitsInner = keysIn(((modelOf("limits").cards.filter((c) => c.key === "credits-surfaces")[0] || { html: "" }).html).replace(/^<\w+[^>]*>/, ""));
out.cards.limitsKeys = modelOf("limits").cards.map((c) => cardKeyOf(c.html));

// the chips: each names its tone by id (data-tone); the tone table is the status's, not its words
const tagsOf = (html) => html.match(/<span class="nm-chip[^"]*"[^>]*>/g) || [];
const toneOfTag = (tag) => { const m = /data-tone="([^"]*)"/.exec(tag); return m ? m[1] : null; };
out.chipTags = [];
for (const page of ["overview", "limits"]) for (const c of modelOf(page).cards) out.chipTags.push(...tagsOf(c.html));
out.burnTone = {};
for (const velocity of ["IDLE", "SLOW", "NORMAL", "FAST", "VERY FAST", "constructor"]) {
  S.data.state.burn = { velocity, tokens_per_min: 900, usd_per_hour: 1.1 };
  const burn = modelOf("overview").cards.filter((c) => c.key === "burn")[0];
  out.burnTone[velocity] = toneOfTag(tagsOf(burn.html)[0]);
}
out.tones = {
  pace: {
    ahead: api.paceChip({ percent: 13 }, { kind: "ahead" }, false).tone,
    behind: api.paceChip({ percent: 13 }, { kind: "behind" }, false).tone,
    onPace: api.paceChip({ percent: 13 }, { kind: "on_pace" }, false).tone,
    noClock: api.paceChip({ percent: 13 }, { kind: "none" }, false).tone,
    unused: api.paceChip({ percent: 0 }, { kind: "ahead" }, false).tone,
    noDraw: api.paceChip({ percent: 0 }, { kind: "ahead" }, true).tone,
  },
  session: ["LIMITED", "AT_RISK", "SAFE", "IDLE", "UNKNOWN", "constructor"].map((v) => api.sessionStory({ verdict: v, percent: 50, resets_at: null, projected_limit_at: null }).toneId),
  weekly: ["AHEAD", "ON_PACE", "BEHIND", "UNKNOWN", "constructor"].map((v) => api.weeklyStory({ verdict: v, percent: 50, pace_delta_pp: 3 }).toneId),
};
out.paceWords = [api.paceChip({ percent: 13 }, { kind: "ahead" }, false).text, api.paceChip({ percent: 13 }, { kind: "behind" }, false).text,
  api.paceChip({ percent: 0 }, { kind: "ahead" }, true).text];

// the uplink line: its words come from the status code, and the health box shows them
out.uplinkWords = {};
for (const status of ["live", "loading", "stale", "offline", "rate_limited", "no_credentials", "paused", "unknown"]) {
  reset();
  elements.health = element("health");
  S.data = { state: { status, ingest: { running: false, files_done: 1, files_total: 1 }, unpriced_models: [] } };
  api.renderHealth();
  out.uplinkWords[status] = (elements.health.innerHTML.match(/<span>LINK<\/span><b class="[^"]*"><i[^>]*><\/i>([^<]*)<\/b>/) || [])[1] || null;
}
reset();
elements.health = element("health");
S.data = { state: { status: "live", ingest: null, unpriced_models: [] } };
S.linkOk = false;
api.renderHealth();
out.uplinkWords.nolink = (elements.health.innerHTML.match(/<span>LINK<\/span><b class="[^"]*"><i[^>]*><\/i>([^<]*)<\/b>/) || [])[1] || null;
S.linkOk = null;

// the age: a number and a unit code, written as the badges write it and inside the stale sentence
const AGES = [0, 999, 59000, 60000, 3599000, 3600000, 47 * 3600000, 48 * 3600000, 4 * 86400000];
out.age = AGES.map((ms) => ({ badge: api.ageWords(api.fmtAge(ms), api.AGE_UNIT), sentence: api.ageWords(api.fmtAge(ms), api.AGE_UNIT_IN_SENTENCE) }));
out.ageNone = api.ageWords(api.fmtAge(null), api.AGE_UNIT);
out.staleBanner = api.staleBanner([{ data: { x: 1 }, error: "HTTP 500", at: Date.now() - 180000 }]);

// scoped limits: the row is matched by kind and model, so two limits with one label keep their own rows
const scopedRows = [
  { label: "SCOPED", kind: "weekly_scoped", scope_model: "Opus", percent: 90 },
  { label: "SCOPED", kind: "weekly_scoped", scope_model: "Fable", percent: 10 },
];
out.scoped = {
  fable: api.matchScoped(scopedRows, { label: "SCOPED", kind: "weekly_scoped", scope_model: "Fable" }, 0).percent,
  opus: api.matchScoped(scopedRows, { label: "SCOPED", kind: "weekly_scoped", scope_model: "Opus" }, 1).percent,
  unnamed: api.matchScoped(scopedRows, { label: "SCOPED", kind: "weekly_scoped", scope_model: "Sonnet" }, 1).percent,
  none: api.matchScoped(null, { label: "SCOPED", kind: "weekly_scoped", scope_model: "Fable" }, 0),
};
// limits scoped by surface have no model: the surface keeps them apart, and a model-scoped one beside them keeps its own row
// (each index points at the wrong row, so only the match can find the right one)
const surfaceRows = [
  { label: "X", kind: "weekly_scoped", scope_model: null, scope_surface: "claude_code", percent: 40 },
  { label: "X", kind: "weekly_scoped", scope_model: null, scope_surface: "cowork", percent: 5 },
  { label: "X", kind: "weekly_scoped", scope_model: "Fable", scope_surface: null, percent: 70 },
];
out.scopedSurface = {
  code: api.matchScoped(surfaceRows, { kind: "weekly_scoped", scope_model: null, scope_surface: "claude_code" }, 2).percent,
  cowork: api.matchScoped(surfaceRows, { kind: "weekly_scoped", scope_model: null, scope_surface: "cowork" }, 0).percent,
  fable: api.matchScoped(surfaceRows, { kind: "weekly_scoped", scope_model: "Fable", scope_surface: null }, 1).percent,
};

// the accent tooltip: a name that is not the colour's id is shown, so the id is the tooltip
const nostromoTheme = { variants: [{ id: "amber", name: "RAY TUBE" }, { id: "blue", name: "CRYO" }] };
const cityTheme = { variants: [{ id: "blackwall", name: "Blackwall" }] };
out.accentTitled = {
  amber: api.accentTitled(nostromoTheme, "amber"), blue: api.accentTitled(nostromoTheme, "blue"),
  blackwall: api.accentTitled(cityTheme, "blackwall"), unnamed: api.accentTitled(nostromoTheme, "green"),
};
reset();

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
// the dashboard's choices (#54): taken from the page's monitor-prefs meta, saved through POST /api/settings
const shown = (a) => Object.fromEntries(Object.keys(a.PREF_CHOICES).map((k) => [k, a.S.ui[k]]));   // the choices in force
api.loadPrefs({ nerdsWindow: "today" });                     // the old name alone: nothing carries it over (#54)
out.prefsAfterOldNameAlone = shown(api);
api.loadPrefs({ costWindow: "month", efficiencyWindow: "week", folderMode: "launch", calMetric: "bogus", nerdsWindow: "today" });
out.loadedPrefs = shown(api);
api.loadPrefs(null);
api.loadPrefs("costWindow=all");
out.loadedPrefsAfterJunk = shown(api);
out.prefChoices = api.PREF_CHOICES;
out.savedPrefsWithoutMeta = api.SAVED_PREFS;
{
  const sent = [];
  const realFetch = box.fetch;
  box.fetch = (url, opts) => {
    sent.push({ url, method: opts.method, body: JSON.parse(opts.body), token: Object.prototype.hasOwnProperty.call(opts.headers, "X-Monitor-Token") });
    return new Promise(() => {});                          // never answers: the choice holds in the window anyway
  };
  api.savePrefs("costWindow");
  out.savedPrefsPost = sent.splice(0);
  api.savePrefs("nerdsWindow");                              // not a choice: nothing is sent
  out.savedPrefsUnknown = sent.splice(0);
  api.onSegment("folder-mode", "folder");                    // a click on a segment saves that one choice
  out.segmentPost = sent.splice(0);
  out.segmentShown = shown(api);
  api.onSegment("no-such-segment", "x");
  out.segmentUnknown = sent.splice(0);
  box.fetch = realFetch;
}
const prefsMeta = (content) => (sel) => (sel === 'meta[name="monitor-prefs"]' ? { getAttribute: (k) => (k === "content" ? content : null) } : null);
const withPrefs = load(prefsMeta('{"costWindow":"week","calMetric":"tokens","folderMode":"nope"}')).api;
out.savedPrefsFromMeta = withPrefs.SAVED_PREFS;
withPrefs.loadPrefs(withPrefs.SAVED_PREFS);
out.prefsFromMeta = shown(withPrefs);
out.savedPrefsBadMeta = load(prefsMeta("{not json")).api.SAVED_PREFS;
out.savedPrefsEmptyMeta = load(prefsMeta("")).api.SAVED_PREFS;
S.ui.efficiencyWindow = "cycle"; S.ui.costWindow = "cycle"; S.ui.folderMode = "folder"; S.ui.calMetric = "cost"; S.ui.matrixMetric = "cost";

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
  nostromo: api.themeSwatches({ id: "nostromo", variants: ["amber", "green", "white", "red", "blue", "purple"].map((id, i) => ({ id, accent: id, colors: { accent: "#" + (0x200000 * (i + 1)).toString(16).padStart(6, "0") } })) }, null),
  nightCity: api.themeSwatches({ id: "night_city", variants: ["blackwall", "relic", "edgerun", "mox"].map((id, i) => ({ id, colors: { accent: "#" + (0x300000 * (i + 1)).toString(16).padStart(6, "0") } })) }, null),
};

// ── the colour buttons say each colour's name: Nostromo's phosphors and Night City's colourways by name, Gates by accent ──
// The themes are made up the way /api/themes sends them: a theme with a light variant lists its modes.
const nostromoMeta = { id: "nostromo", modes: [], variants: [["amber", "RAY TUBE"], ["green", "TERMINAL"], ["white", "STATIC"], ["red", "ALERT"], ["blue", "CRYO"], ["purple", "PLASMA"]].map(([id, name]) => ({ id, name, accent: id, mode: "dark", colors: {} })) };
const nightCityMeta = { id: "night_city", modes: [], variants: [["blackwall", "Blackwall"], ["relic", "Relic"], ["edgerun", "Edgerun"], ["mox", "Mox"]].map(([id, name]) => ({ id, name, accent: id, mode: "dark", colors: {} })) };
const gatesMeta = { id: "gates", modes: ["dark", "light", "system"], variants: gatesVariants.map((v) => Object.assign({}, v, { name: v.accent + " · " + v.mode })) };
out.accentLabels = {
  nostromo: nostromoMeta.variants.map((v) => api.accentLabel(nostromoMeta, v.accent)),
  nightCity: nightCityMeta.variants.map((v) => api.accentLabel(nightCityMeta, v.accent)),
  gates: accents.map((accent) => api.accentLabel(gatesMeta, accent)),
  retired: api.accentLabel(nostromoMeta, "cyan"),                                       // no variant has it: the id, in capitals
  noTheme: api.accentLabel(null, "amber"),
};
{
  // the sidebar: Nostromo names its six phosphors in full, the other themes keep their short labels
  elements.variants = element("variants");
  elements["phos-label"] = element("phos-label");
  const sidebar = (meta, variant) => {
    S.themes = [meta];
    S.data = { theme: { id: meta.id, variant } };
    api.renderVariants();
    return { label: elements["phos-label"].textContent, html: elements.variants.innerHTML };
  };
  out.sidebar = { nostromo: sidebar(nostromoMeta, "blue"), nightCity: sidebar(nightCityMeta, "relic"), gates: sidebar(gatesMeta, "teal-dark") };
  delete elements.variants;
  delete elements["phos-label"];
  // the Settings page: the same names, and a name that is not the colour's id keeps the id as its tooltip
  const picker = (meta, variant) => {
    const parts = { "#set-themes": element("set-themes"), "#set-accents": element("set-accents"), "#set-mode": element("set-mode"), "#set-theme-meta": element("set-theme-meta") };
    const main = Object.assign(element("main"), { querySelector: (selector) => parts[selector] || null });
    S.themes = [meta];
    S.data = { theme: { id: meta.id, name: meta.id, variant, variant_name: api.accentLabel(meta, variant.replace(/-(dark|light)$/, "")) } };
    api.syncThemePicker(main);
    return { accents: parts["#set-accents"].innerHTML, meta: parts["#set-theme-meta"].textContent };
  };
  out.settingsAccents = { nostromo: picker(nostromoMeta, "blue"), nightCity: picker(nightCityMeta, "relic"), gates: picker(gatesMeta, "teal-dark") };
  S.themes = null;
  reset();
}

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
  stayingForecastDrawn: /class="mon-fore"/.test(staying.svg), crossingForecastDrawn: /class="mon-fore"/.test(crossing.svg),
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
const NO_SIGN_IN = "No Claude sign-in on this PC; sign in to Claude Code once (the dashboard shows how)";
const ingestOf = (patch) => Object.assign({ running: false, files_done: 0, files_total: 0, last_duration_s: 0.01, last_error: null }, patch);
const requestsIn = (n) => ({ all: { name: "all", start: null, end: null, totals: { requests: n } } });
function forget() {
  reset();
  S.noActivity = false;
  S.copy = "";
  S.copyOf = "";
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
monitorState("stale", { status_detail: "The Claude sign-in has expired; send Claude Code a message to renew it" });
out.signIn.expired = api.statusBanner();
monitorState("stale", { status_detail: hostile });
out.signIn.hostile = api.statusBanner();
monitorState("stale", { status_detail: "" });
out.signIn.noDetail = api.statusBanner();
monitorState("stale", { status_detail: "Claude did not accept the sign-in (HTTP 403); sign in to Claude Code again with /login", signin_refused: 403 });
out.signIn.refused = api.statusBanner();
monitorState("stale", { status_detail: "Claude did not accept the sign-in (HTTP 401); send Claude Code a message, and sign in again if it asks", signin_refused: 401 });
out.signIn.refused401 = api.statusBanner();

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
  doneWithErrors: decided({ ingest: ingestOf({ last_error: "PermissionError on a.jsonl" }) }),
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
// Every input is made up. The page asks /api/guard, which answers { guard, told, waiting, watching, setup }.
const GUARD_T = Date.UTC(2026, 9, 21, 16, 12);
const isoAt = (ms) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z");
const POLICY = { enabled: true, session_cap: 95, weekly_cap: 90, scoped_cap: null, stagger_s: 120 };
const RESETS = isoAt(GUARD_T + 3 * HOUR + 27 * 60000);
const SESSION_OVER = { name: "session", label: "session", percent: 96, cap: 95, resets_at: RESETS };
const FABLE_OVER = { name: "Fable weekly", label: "Fable weekly", percent: 91.5, cap: 90, resets_at: RESETS };
const toldN = (n) => Array.from({ length: n }, (_, i) => ({ session_id: "3f9a1c2e-" + i + "aaa-bbbb", told_at: isoAt(GUARD_T - (30 - i) * 60000) }));
// a workflow's agents heard it but its conversation did not (#41), and another conversation that did
const AGENTS_TOLD = [
  { session_id: "3f9a1c2e-0aaa-bbbb", told_at: isoAt(GUARD_T - 20 * 60000), main: false, agents: 3 },
  { session_id: "5d2b7e41-1ccc-dddd", told_at: isoAt(GUARD_T - 10 * 60000), main: true, agents: 0 },
];
const waiterOf = (id, cwd, pid) => ({ pid: pid || 4242, session_id: id, since: isoAt(GUARD_T - 18 * 60000), cwd });
const guardAnswer = (guard, told, waiting, setup, watching) => ({ guard, told: told || [], waiting: waiting || [], watching: watching || [], setup: setup || null });
// a conversation the Stop hook watches while it waits for its background work (#41)
const watcherOf = (id, work) => ({ pid: 5151, session_id: id, since: isoAt(GUARD_T - 25 * 60000), work });
const ARMED_GUARD = { state: "armed", over: [], resume_at: null, policy: POLICY };
const holding = (over, extra) => Object.assign({ state: "holding", over, resume_at: RESETS, policy: POLICY }, extra || {});
const SETUP_OK = {
  available: true, can_remove: true, reason: null, manual: "docs/AGENT_HOOK.md", exe: "C:/x/ClaudeUsageMonitor.exe", settings_path: "C:/Users/t/.claude/settings.json", errors: [],
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
    armedNoHooks: liveFor(guardAnswer({ state: "armed", over: [], resume_at: null, policy: POLICY }, [], [], SETUP_OK)),
    armedHooked: liveFor(guardAnswer({ state: "armed", over: [], resume_at: null, policy: POLICY }, [], [], setupWith({ hooks: { state: "installed", installed: 3, total: 3, events: {} } }))),
    holdingPartHooked: liveFor(guardAnswer(holding([SESSION_OVER]), [], [], setupWith({ hooks: { state: "partial", installed: 2, total: 3, events: {} } }))),
    holding: liveFor(guardAnswer(holding([SESSION_OVER, FABLE_OVER]), toldN(2), [waiterOf("77b0aa10-9d3e", "C:\\Demo\\atlas-web")])),
    holdingAgents: liveFor(guardAnswer(holding([SESSION_OVER]), AGENTS_TOLD)),
    armedWatching: liveFor(guardAnswer(ARMED_GUARD, [], [], null, [watcherOf("3f9a1c2e-0aaa-bbbb", { workflow: 1, agent: 2 })])),
    offWatching: liveFor(guardAnswer({ state: "off", over: [], resume_at: null, policy: Object.assign({}, POLICY, { enabled: false }) }, [], [], null,
      [watcherOf("3f9a1c2e-0aaa-bbbb", { workflow: 1 })])),
    holdingWatchingMany: liveFor(guardAnswer(holding([SESSION_OVER]), [], [], null,
      Array.from({ length: 7 }, (_, i) => watcherOf("5d2b7e4" + i + "-1ccc", { agent: i + 1 })))),
    watchingOdd: liveFor(guardAnswer(ARMED_GUARD, [], [], null, [{ session_id: null, since: null, work: "x" },
      { session_id: "abc", since: "nonsense", work: { agent: -1, workflow: "2", teammate: 1 } }])),
    holdingBusy: liveFor(guardAnswer(holding([SESSION_OVER]), toldN(1), [waiterOf("77b0aa10-9d3e", "C:\\Demo\\atlas-web")]), { busy: true }),
    holdingNobody: liveFor(guardAnswer(holding([SESSION_OVER]))),
    holdingNoResetTime: liveFor(guardAnswer(holding([SESSION_OVER], { resume_at: null }), toldN(1))),
    holdingAMomentLeft: liveFor(guardAnswer(holding([SESSION_OVER]), toldN(1)), { now: Date.parse(RESETS) - 20000 }),
    holdingMany: liveFor(guardAnswer(holding([SESSION_OVER]), toldN(7), manyWaiting)),
    holdingHostile: liveFor(guardAnswer(holding([{ name: hostile, label: hostile, percent: 96, cap: 95, resets_at: RESETS }]),
      [{ session_id: hostile, told_at: isoAt(GUARD_T) }], [waiterOf(hostile, "C:\\<b>x</b>\\" + hostile)], null,
      [{ session_id: hostile, since: isoAt(GUARD_T), work: { [hostile]: 1 } }])),
    odd: liveFor({ guard: "x", told: "y", waiting: 3, setup: [] }),
  },
  clock: { resumes: api.clockText(Date.parse(RESETS)), told: api.clockText(GUARD_T - 30 * 60000), since: api.clockText(GUARD_T - 18 * 60000),
    watched: api.clockText(GUARD_T - 25 * 60000) },
  setup: {
    pending: setupFor(null),
    missing: setupFor(SETUP_OK),
    installed: setupFor(setupWith({ hooks: { state: "installed", installed: 3, total: 3, events: {} }, mcp: { state: "registered" } })),
    partial: setupFor(setupWith({ hooks: { state: "partial", installed: 2, total: 3, events: {} }, mcp: { state: "other" } })),
    tooOld: setupFor(setupWith({ claude: { found: true, version: "2.1.94", min_version: "2.1.139", supported: false } })),
    tooOldOnNoPath: setupFor(setupWith({ claude: { found: true, path: "C:\\Users\\t\\.local\\bin\\claude.exe", command: "& 'C:\\Users\\t\\.local\\bin\\claude.exe'", version: "2.1.94", min_version: "2.1.139", supported: false } })),
    tooOldAt: setupFor(setupWith({ claude: { found: true, path: "C:\\Users\\t\\AppData\\Roaming\\npm\\claude.CMD", version: "2.1.94", min_version: "2.1.139", supported: false } })),
    versionUnknown: setupFor(setupWith({ claude: { found: true, version: null, min_version: "2.1.139", supported: null } })),
    noClaude: setupFor(setupWith({ available: false, reason: "Claude Code was not found on this PC: there is no claude command on the PATH.", claude: { found: false, version: null, min_version: "2.1.139", supported: null } })),
    fromSource: setupFor(setupWith({ available: false, can_remove: false, reason: "One-click setup works in the installed app. This copy runs from source.", hooks: { state: "unknown", installed: 0, total: 3, events: {} }, mcp: { state: "unknown" }, claude: { found: false, version: null, min_version: "2.1.139", supported: null } })),
    unreadable: setupFor(setupWith({ errors: ["The Claude Code settings file is not valid JSON, so setup left it alone."], hooks: { state: "unknown", installed: 0, total: 3, events: {} } })),
    installing: setupFor(SETUP_OK, null, "install"),
    removing: setupFor(SETUP_OK, null, "remove"),
    installed_ok: setupFor(SETUP_OK, done({ changed: ["Added the SessionStart hook.", "Registered the claude-usage MCP tool."], backup: "C:\\Users\\t\\.claude\\settings.json.usage-monitor-backup-20261028-180509", warnings: ["This card checks only the claude command. Claude Code in VS Code has its own version: in VS Code, check under Extensions that it is 2.1.139 or newer."] })),
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
  // badInput is what a number box reports for text it cannot hold ("9e", "-"): its value then reads as ""
  const field = (key, value, badInput) => ({ target: { type: "number", value, validity: { badInput: !!badInput }, getAttribute: (name) => (name === "data-key" ? key : null), hasAttribute: () => false } });
  const typed = async (key, value, badInput) => {
    elements["err-" + key] = element("err-" + key);
    const lines = await run({ "POST /api/settings": [200, { ok: true, applied: {}, settings: {} }] }, () => api.settingsChange(field(key, value, badInput)));
    const wrote = calls.filter((c) => c.key === "POST /api/settings").map((c) => c.body);
    return { wrote, error: elements["err-" + key].textContent, shown: elements["err-" + key].hidden === false, status: S.status.text, lines: lines.length };
  };
  actions.fields = {};
  for (const [label, key, value, bad] of [
    ["capBlank", "guard_session_cap", ""], ["capSpaces", "guard_weekly_cap", "  "], ["cap1", "guard_session_cap", "1"], ["cap95", "guard_session_cap", "95"],
    ["cap100", "guard_scoped_cap", "100"], ["cap0", "guard_session_cap", "0"], ["cap101", "guard_weekly_cap", "101"], ["capDecimal", "guard_session_cap", "9.5"],
    ["capNegative", "guard_scoped_cap", "-5"], ["capText", "guard_session_cap", "abc"], ["capExp", "guard_session_cap", "1e2"], ["capLong", "guard_session_cap", "0095"],
    ["stagger0", "guard_resume_stagger_s", "0"], ["stagger120", "guard_resume_stagger_s", "120"], ["stagger600", "guard_resume_stagger_s", "600"],
    ["stagger601", "guard_resume_stagger_s", "601"], ["staggerBlank", "guard_resume_stagger_s", ""], ["staggerNegative", "guard_resume_stagger_s", "-1"], ["staggerDecimal", "guard_resume_stagger_s", "1.5"],
    ["capBadInput", "guard_session_cap", "", true],
  ]) {
    fresh();
    actions.fields[label] = await typed(key, value, bad);
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
  monitorState("stale", { status_detail: "The Claude sign-in has expired; send Claude Code a message to renew it" });
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

  // Copy: the command of the button pressed goes to the clipboard once, and that button says so for a moment; where the clipboard
  // refuses or is not there the command is selected instead (no selection API in this page, which must not matter). A command the
  // panel does not have copies nothing.
  const writes = [];
  const writer = { writeText: (text) => { writes.push(text); return Promise.resolve(); } };
  // renderPage asks for #main first, so each ask is a paint: what the copy state was when the page was drawn
  const painted = [];
  const byId = box.document.getElementById;
  box.document.getElementById = (id) => { if (id === "main") { painted.push([S.copy, S.copyOf]); } return byId(id); };
  const copyWith = async (clipboard, which = "install", status = "no_credentials") => {
    monitorState(status);
    writes.length = 0;
    timers.length = 0;
    painted.length = 0;
    box.navigator = clipboard === undefined ? undefined : { clipboard };
    api.helpAction("copy-command", which);
    await settle();
    const shown = { copy: S.copy, copyOf: S.copyOf, html: api.statusBanner(), timers: timers.map((t) => t.ms), writes: writes.slice() };
    if (timers.length) { timers[timers.length - 1].fn(); }
    shown.after = { copy: S.copy, copyOf: S.copyOf, html: api.statusBanner(), painted: painted.slice() };
    return shown;
  };
  out2.copy = {
    written: await copyWith(writer),
    refused: await copyWith({ writeText: (text) => { writes.push(text); return Promise.reject(new Error("denied")); } }),
    throws: await copyWith({ writeText: () => { throw new Error("no permission"); } }),
    missing: await copyWith({}),
    noClipboard: await copyWith(undefined),
    start: await copyWith(writer, "start"),
    expiredStart: await copyWith(writer, "start", "stale"),
    unknownCommand: await copyWith(writer, "toString"),
    noCommand: await copyWith(writer, null),
  };

  // What "Press Ctrl+C" selects. This page has no selection of its own, so these stand in for one: a found code element is its selector.
  const chosen = [];
  const query = box.document.querySelector;
  box.document.querySelector = (selector) => (selector.startsWith(".nm-help") ? { selector } : null);
  box.document.createRange = () => ({ selectNodeContents(node) { this.node = node; } });
  box.getSelection = () => ({ removeAllRanges() {}, addRange(range) { chosen.push(range.node.selector); } });
  const selectedBy = async (clipboard, which, status) => {
    chosen.length = 0;
    await copyWith(clipboard, which, status);
    return chosen.slice();
  };
  out2.selected = {
    install: await selectedBy({}, "install"),
    start: await selectedBy({}, "start"),
    expiredStart: await selectedBy({}, "start", "stale"),
    copied: await selectedBy(writer, "start"),
  };
  box.document.querySelector = query;
  delete box.document.createRange;
  delete box.getSelection;

  // Both buttons within the moment: the answer moves to the second, and the first press's timer is called off
  const cleared = [];
  monitorState("no_credentials");
  writes.length = 0;
  timers.length = 0;
  box.clearTimeout = (id) => { cleared.push(id); };
  box.navigator = { clipboard: writer };
  api.helpAction("copy-command", "install");
  await settle();
  api.helpAction("copy-command", "start");
  await settle();
  out2.handOver = { copyOf: S.copyOf, html: api.statusBanner(), writes: writes.slice(), cleared, timers: timers.length };
  box.clearTimeout = () => {};
  box.document.getElementById = byId;

  monitorState("no_credentials");
  out2.unknownAction ={ calls: await run({}, () => api.helpAction("nothing-here")), copy: S.copy, check: where() };      // an unknown action does nothing

  box.fetch = saved.fetch; box.setTimeout = saved.set; box.clearTimeout = saved.clear; box.navigator = saved.navigator;
  forget();
  out.signInActions = out2;
}

// ── saving a colour names it on the status line (SAVED CRYO, not SAVED BLUE) and sends the colour's id ──────────────
async function colourSaves() {
  const saved = { fetch: box.fetch, clear: box.clearTimeout };
  const posts = [];
  box.document.querySelector = (selector) => (selector === "#main" ? mainElement : null);
  box.clearTimeout = () => {};                                                  // poll() reschedules itself
  box.fetch = (url, init) => {
    if (init && init.method === "POST") {
      posts.push(url + " " + init.body);
      return answer(200, { settings: {} })();
    }
    return answer(404, { error: "not in this test" })();
  };
  const accentClick = (accent) => ({ target: { closest: () => ({ hasAttribute: (name) => name === "data-accent", getAttribute: (name) => (name === "data-accent" ? accent : null) }) } });
  const saveOf = async (meta, variant, click) => {
    reset();
    posts.length = 0;
    S.themes = [meta];
    S.data = { state: { status: "live" }, model_names: {}, theme: { id: meta.id, variant } };
    click();
    const saving = S.status.text;
    await settle();
    return { saving, saved: S.status.text, posts: posts.slice() };
  };
  out.colourSaves = {
    nostromo: await saveOf(nostromoMeta, "amber", () => api.settingsClick(accentClick("blue"))),
    gates: await saveOf(gatesMeta, "system-dark", () => api.settingsClick(accentClick("teal"))),
    sidebar: await saveOf(nostromoMeta, "amber", () => api.chooseAccent("purple")),       // what the sidebar's buttons call
  };
  box.document.querySelector = savedQuery;
  box.fetch = saved.fetch;
  box.clearTimeout = saved.clear;
  S.themes = null;
  reset();
}

// the plan price hint: loaded on #settings, the page is drawn before the first state, and a first sign-in or a new plan changes the plan
// under a page that is open. These are init()'s two steps and then polls, on a fake page that keeps the hint's text as a browser does:
// parsed out of what the page drew, and changed after that only through the hint's own element.
async function planHint() {
  const saved = { hash: box.location.hash, main: elements.main, fetch: box.fetch, clear: box.clearTimeout };
  const main = element("main");
  const hint = element("plan-hint");
  const parsed = (html) => html.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, "&");
  let drawn = "";
  let text = null;
  let writes = 0;
  let asMarkup = 0;
  Object.defineProperty(main, "innerHTML", {
    get: () => drawn,
    set: (html) => { drawn = html; const found = /id="plan-hint">([^<]*)</.exec(html); text = found ? parsed(found[1]) : null; },
  });
  Object.defineProperty(hint, "textContent", { get: () => text, set: (value) => { writes += 1; text = value; } });
  Object.defineProperty(hint, "innerHTML", { get: () => "", set: () => { asMarkup += 1; } });
  main.querySelector = (selector) => (selector === "#plan-hint" ? hint : null);
  elements.main = main;
  box.location.hash = "#settings";
  box.clearTimeout = () => {};
  let answerWith = null;
  box.fetch = (url) => (String(url).indexOf("/api/state") >= 0 ? answer(200, answerWith)() : answer(404, { error: "not in this test" })());
  const stateWith = (plan, settings) => ({ state: { status: "live", plan }, settings: settings || {}, model_names: {} });
  const planOf = (name, price) => ({ name, monthly_price_usd: price });
  const seen = () => ({ text, writes });
  const arrives = async (state) => { answerWith = state; api.poll(true); await settle(); return seen(); };

  reset();
  S.page = null;
  api.renderPage();
  out.planHint = { before: seen() };
  out.planHint.firstState = await arrives(stateWith(planOf("Max 5x", 100)));
  out.planHint.sameState = await arrives(stateWith(planOf("Max 5x", 100)));
  out.planHint.override = await arrives(stateWith(planOf("Max 5x", 100), { plan_price_override: 50 }));   // it names the detected price
  out.planHint.newPlan = await arrives(stateWith(planOf("Max 20x", 200)));
  out.planHint.noPrice = await arrives(stateWith(planOf("Unknown plan", null)));
  out.planHint.signedIn = await arrives(stateWith(planOf("Pro", 20)));
  out.planHint.noPlan = await arrives(stateWith(undefined));
  out.planHint.hostile = await arrives(stateWith(planOf(hostile, 20)));
  S.page = null;                                                                 // what leaving Settings and coming back does
  api.renderPage();
  out.planHint.drawnAgain = seen();
  out.planHint.hostileName = hostile;
  out.planHint.drawnEscaped = drawn.indexOf(api.esc(hostile)) >= 0 && drawn.indexOf(hostile) < 0;
  out.planHint.asMarkup = asMarkup;

  box.location.hash = saved.hash;
  elements.main = saved.main;
  box.fetch = saved.fetch;
  box.clearTimeout = saved.clear;
  S.page = null;
  S.formKey = null;
  reset();
}

// three texts that outlived what they described (#47), each through the page's real paths: the first paint, a poll, a click
async function staleTexts() {
  const saved = { hash: box.location.hash, main: elements.main, fetch: box.fetch, clear: box.clearTimeout, query: box.document.querySelector };
  const main = element("main");
  const statusLine = element("set-update-status");
  const installButton = element("install");
  const startupHint = element("startup-hint");
  const supportersBox = element("about-supporters");
  const parts = {
    "#set-update-status": statusLine, '[data-action="install-update"]': installButton, '[data-action="skip-update"]': element("skip"),
    "#startup-hint": startupHint, "#about-supporters": supportersBox,
  };
  let drawn = "";
  let startup = null;
  let startupWrites = 0;
  Object.defineProperty(main, "innerHTML", {
    get: () => drawn,
    set: (html) => { drawn = html; const found = /id="startup-hint">([^<]*)</.exec(html); startup = found ? found[1] : null; },
  });
  Object.defineProperty(startupHint, "textContent", { get: () => startup, set: (value) => { startupWrites += 1; startup = value; } });
  main.querySelector = (selector) => parts[selector] || null;
  elements.main = main;
  box.document.querySelector = (selector) => (selector === "#main" ? main : null);
  box.clearTimeout = () => {};
  let stateNow = null;
  let checkResult = null;
  let heldCheck = null;
  let supportersReply = null;
  let asks = 0;
  box.fetch = (url) => {
    const path = String(url);
    if (path.indexOf("/api/supporters") >= 0) { asks += 1; return supportersReply(); }
    if (path.indexOf("/api/update/check") >= 0) { return heldCheck || answer(200, { ok: true, result: checkResult })(); }
    if (path.indexOf("/api/update/apply") >= 0) { return answer(200, { result: { mode: "browser" } })(); }
    if (path.indexOf("/api/update/skip") >= 0) { return answer(200, { ok: true })(); }
    if (path.indexOf("/api/state") >= 0) { return answer(200, stateNow)(); }
    return answer(404, { error: "not in this test" })();
  };
  const offering = (version, skipped) => ({
    state: { status: "live" }, settings: {}, model_names: {},
    update: version ? { available: true, version, skipped: !!skipped, skip_version: skipped ? version : null, info: { version } } : { available: false },
  });
  const line = () => ({ line: statusLine.textContent, install: !installButton.hidden });
  const lands = async (state) => { stateNow = state; api.poll(true); await settle(); return line(); };
  const clicked = async (action, state, result) => { stateNow = state; checkResult = result; api.settingsClick(click(action)); await settle(); return line(); };
  const opens = async (hash) => { box.location.hash = hash; S.page = null; api.renderPage(); await settle(); };

  // 1. The line under the update buttons: what a click said lasts only while it is about the release on offer.
  reset();
  S.update = { text: "" };
  S.skipped = "";
  stateNow = offering(null);
  await opens("#settings");
  await lands(offering(null));
  out.updateLine = {};
  out.updateLine.upToDate = await clicked("check-update", offering(null), { available: false, version: null, checked: true });
  out.updateLine.foundLater = await lands(offering("2.3.0"));
  let finishCheck = null;
  heldCheck = new Promise((resolve) => { finishCheck = resolve; }).then(() => answer(200, { ok: true, result: { available: true, version: "2.3.0", checked: true } })());
  api.settingsClick(click("check-update"));
  out.updateLine.checking = await lands(offering("2.3.0"));
  finishCheck();
  heldCheck = null;
  await settle();
  out.updateLine.checked = line();
  out.updateLine.banner = api.updateBanner();
  heldCheck = Promise.reject(new Error("link lost"));
  out.updateLine.linkLost = await clicked("check-update", offering("2.3.0"));
  heldCheck = null;
  out.updateLine.noGithub = await clicked("check-update", offering("2.3.0"), { available: false, version: null, checked: false });
  out.updateLine.newerAfterThat = await lands(offering("2.4.0"));
  out.updateLine.skipped = await clicked("skip-update", offering("2.4.0", true));
  out.updateLine.newerAfterSkip = await lands(offering("2.5.0"));
  api.installUpdate();
  await settle();
  out.updateLine.installing = line();
  out.updateLine.newerAfterInstall = await lands(offering("2.6.0"));
  // the monitor goes on offering a release that was skipped, and Settings is the one place left to install it from
  await lands(offering("2.6.0", true));
  S.update = { text: "" };
  out.updateLine.skippedOnOffer = line();
  api.installUpdate();
  await settle();
  out.updateLine.installSkipped = line();
  await clicked("skip-update", offering("2.6.0", true));
  out.updateLine.skipSkipped = line();

  // 2. The note under Start with Windows: a page loaded on #settings is drawn before the state says it is a dev instance.
  reset();
  S.update = { text: "" };
  S.skipped = "";
  const hinted = () => ({ text: startup, writes: startupWrites });
  const devState = Object.assign(offering(null), { dev: true });
  await opens("#settings");
  out.startupHint = { before: hinted() };
  await lands(devState);
  out.startupHint.dev = hinted();
  await lands(devState);
  out.startupHint.sameState = hinted();
  await lands(offering(null));
  out.startupHint.notDev = hinted();
  await lands(devState);
  await opens("#settings");
  out.startupHint.drawnAgain = hinted();

  // 3. The supporters: a list that failed is asked for at the next visit, not on every state, and one that worked is not asked for again.
  reset();
  S.supporters = null;
  S.supportersRequested = false;
  const listed = () => ({ asks, unavailable: /unavailable/.test(supportersBox.innerHTML), named: /Ada/.test(supportersBox.innerHTML) });
  const works = answer(200, { supporters: ["Ada"] });
  stateNow = offering(null);
  supportersReply = answer(200, { supporters: [], error: "the list could not be fetched" });
  await opens("#about");
  out.supporters = { failed: listed() };
  await lands(offering(null));
  out.supporters.sameVisit = listed();
  supportersReply = works;
  await opens("#about");
  out.supporters.nextVisit = listed();
  await opens("#about");
  out.supporters.visitAfterThat = listed();
  S.supporters = null;
  S.supportersRequested = false;
  supportersReply = () => Promise.reject(new Error("link lost"));
  await opens("#about");
  out.supporters.linkLost = listed();
  supportersReply = works;
  await opens("#about");
  out.supporters.afterLinkLost = listed();

  box.location.hash = saved.hash;
  elements.main = saved.main;
  box.fetch = saved.fetch;
  box.clearTimeout = saved.clear;
  box.document.querySelector = saved.query;
  S.page = null;
  S.formKey = null;
  S.update = { text: "" };
  S.skipped = "";
  S.supporters = null;
  reset();
}

failures().then(guardPolling).then(guardActions).then(signInActions).then(colourSaves).then(planHint).then(staleTexts).then(() => console.log(JSON.stringify(out)), (error) => { console.error(error && error.stack ? error.stack : String(error)); process.exit(1); });
