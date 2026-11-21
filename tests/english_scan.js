"use strict";
/* The dashboard half of the "no English left" scan (#89; tests/english_left.py, tests/test_english_left.py).
 *
 * Runs the dashboard's real scripts (charts.js, app.js) under node against a running dev server, with the small fake DOM
 * and the test hook tests/webui_checks.js uses. The page the server serves, its catalog script, the state and every
 * report a page asks for come from that server, as they would in the app. For each page it prints the HTML app.js builds
 * (the page model's parts, or the Settings and About forms, and each chart drawn at one width), and it prints every text,
 * markup and attribute app.js writes into the page's fixed elements (the header's link status, the data age).
 *
 *   node tests/english_scan.js <webui folder> <the page's URL, with its key> <now, in ms since 1970>
 *
 * The page's clock stands still at <now> (the dev server's, so its times agree), and english_left.py runs node with
 * TZ pinned, so two runs draw the same words. Prints {shell, title, pages: {page: [html]}, writes: [{id, kind, name,
 * value}], errors: {page: message}}. The theme scripts are not run: their boot texts play only in a browser (#101 moves
 * them). Nothing here reads a user's data.
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const [, , webui, pageUrl, nowArg] = process.argv;
const NOW = Number(nowArg);
if (!Number.isFinite(NOW)) {
  throw new Error("usage: node tests/english_scan.js <webui folder> <page URL> <now in ms>");
}
const origin = new URL(pageUrl).origin;
const CHART_WIDTH = 640;
const writes = [];

function element(id, record) {
  const attrs = {};
  let html = "";
  let text = "";
  const el = {
    id: id || "", className: "", dataset: {}, hidden: false, disabled: false, value: "", children: [],
    style: { setProperty() {}, removeProperty() {}, getPropertyValue: () => "" },
    getAttribute: (k) => (Object.prototype.hasOwnProperty.call(attrs, k) ? attrs[k] : null),
    setAttribute: (k, v) => { attrs[k] = String(v); if (record) { writes.push({ id, kind: "attr", name: k, value: String(v) }); } },
    removeAttribute: (k) => { delete attrs[k]; }, hasAttribute: (k) => Object.prototype.hasOwnProperty.call(attrs, k),
    classList: { toggle() {}, add() {}, remove() {}, contains: () => false },
    querySelector: () => null, querySelectorAll: () => [], addEventListener() {}, removeEventListener() {},
    appendChild: (child) => child, insertBefore() {}, removeChild() {}, focus() {}, closest: () => null,
    contains: () => false, matches: () => false, dispatchEvent: () => true, scrollIntoView() {},
    getBoundingClientRect: () => ({ left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 }),
  };
  Object.defineProperty(el, "innerHTML", {
    get: () => html,
    set: (v) => { html = String(v); text = ""; if (record) { writes.push({ id, kind: "html", name: "", value: html }); } },
  });
  Object.defineProperty(el, "textContent", {
    get: () => text,
    set: (v) => { text = String(v); html = ""; if (record) { writes.push({ id, kind: "text", name: "", value: text }); } },
  });
  return el;
}

function svgElement(tag) {
  const el = element(tag, false);
  el.getComputedTextLength = () => String(el.textContent).length * 6;
  return el;
}

function unescape(text) {
  return text.replace(/&quot;/g, '"').replace(/&#x27;/g, "'").replace(/&#39;/g, "'").replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}

(async () => {
  const shell = await (await fetch(pageUrl)).text();
  const metaOf = (name) => {
    const found = new RegExp('<meta name="' + name + '" content="([^"]*)">').exec(shell);
    return found ? unescape(found[1]) : "";
  };
  const catalogPath = /<script src="(\/static\/i18n\/[a-z]+\.js)"><\/script>/.exec(shell);
  if (!catalogPath) {
    throw new Error("the page names no catalog script");
  }
  const catalog = await (await fetch(origin + catalogPath[1])).text();

  // The fixed elements of the page (index.html): one recording element per id, made on first ask. #main is not one: the
  // page model is read from app.js itself, so a render into #main (which needs a real DOM) finds no #main and stops.
  const fixed = {};
  const byId = (id) => (id === "main" ? null : (fixed[id] = fixed[id] || element(id, true)));
  let title = (/<title>([^<]*)<\/title>/.exec(shell) || [null, ""])[1];
  const doc = {
    readyState: "complete", visibilityState: "visible", documentElement: element("html", false),
    head: element("head", false), body: svgElement("body"), activeElement: null,
    querySelector: (selector) => {
      const meta = /^meta\[name="([^"]+)"\]$/.exec(selector);
      if (meta) {
        const content = metaOf(meta[1]);
        return { getAttribute: (k) => (k === "content" ? content : null) };
      }
      const id = /^#([\w-]+)$/.exec(selector);
      return id ? byId(id[1]) : null;
    },
    querySelectorAll: () => [], getElementById: byId, createElement: (tag) => element(tag, false),
    createElementNS: (_ns, tag) => svgElement(tag), createTextNode: (text) => ({ nodeType: 3, nodeValue: text }),
    addEventListener() {}, removeEventListener() {},
  };
  Object.defineProperty(doc, "title", {
    get: () => title,
    set: (v) => { title = String(v); writes.push({ id: "", kind: "title", name: "", value: title }); },
  });

  let pending = 0;
  const box = { console, document: doc, __USAGE_MONITOR_TEST__: {} };
  box.window = box;
  box.location = { hash: "#overview", reload() {} };
  box.history = { replaceState() {} };
  box.name = "";
  box.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  box.getComputedStyle = () => ({ getPropertyValue: () => "" });
  box.requestAnimationFrame = () => 0;
  box.cancelAnimationFrame = () => {};
  box.setInterval = () => 0;
  box.clearInterval = () => {};
  box.setTimeout = () => 0;
  box.clearTimeout = () => {};
  box.addEventListener = () => {};
  box.removeEventListener = () => {};
  box.fetch = (url, options) => {
    pending += 1;
    return fetch(new URL(String(url), origin), options).finally(() => { pending -= 1; });
  };
  vm.createContext(box);
  vm.runInContext("(function (now) { var Real = Date;" +
    " class Still extends Real { constructor(...a) { super(...(a.length ? a : [now])); } static now() { return now; } }" +
    " globalThis.Date = Still; })(" + NOW + ");", box, { filename: "still-clock.js" });
  vm.runInContext(catalog, box, { filename: catalogPath[1] });
  for (const name of ["charts.js", "app.js"]) {
    vm.runInContext(fs.readFileSync(path.join(webui, name), "utf8"), box, { filename: name });
  }
  const api = box.__USAGE_MONITOR_TEST__.api;
  const S = api.S;
  const settle = async () => {
    for (let round = 0; round < 3000 && (pending || round < 3); round++) {
      await new Promise((resolve) => setTimeout(resolve, pending ? 5 : 0));
    }
    if (pending) {
      throw new Error("the dev server did not answer");
    }
  };
  const partsOf = (parts, into) => {
    parts.forEach((p) => {
      if (p.grid) {
        partsOf(p.parts, into);
      } else if (p.html) {
        into.push(p.html);
      }
    });
    return into;
  };

  api.poll(true);
  await settle();
  const pages = {};
  const errors = {};
  for (const key of api.PAGE_KEYS) {
    try {
      box.location.hash = "#" + key;
      S.page = key;
      S.formKey = null;
      S.charts = {};
      api.pageStats(key).forEach((entry) => api.loadStat(entry, true));
      await settle();
      const html = [];
      if (key === "settings" || key === "about") {
        const main = element("main", false);
        (key === "settings" ? api.settingsPage : api.aboutPage)(main);
        await settle();
        html.push(main.innerHTML);
      } else {
        const model = api.buildModel(key);
        partsOf(model.top, html);
        partsOf(model.cards, html);
      }
      Object.keys(S.charts).forEach((id) => html.push(S.charts[id].build(CHART_WIDTH)));
      pages[key] = html;
    } catch (e) {
      errors[key] = String((e && e.stack) || e).split("\n").slice(0, 4).join(" | ");
    }
  }
  process.stdout.write(JSON.stringify({ shell, title, pages, writes, errors }));
})().catch((e) => {
  console.error(e && e.stack ? e.stack : e);
  process.exit(1);
});
