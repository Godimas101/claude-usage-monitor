"use strict";
/* Runs each theme script (webui/themes/<id>.js) the way the page runs it when motion.js is missing: in a vm with no
 * window.CUM_FX, so the script's `var FX = window.CUM_FX || null` is null. It prints what happened as one JSON object.
 *
 * tests/test_motion.py starts this with `node tests/theme_scripts_checks.js <webui folder>` and asserts on the result: the
 * script loads, registers its `start` hook and nothing else (the motion hooks exist only with an engine), and `start()`, a
 * second `start()`, the timers and animation frames it leaves behind, the theme being switched off and on again, and a
 * reduced-motion preference all run without throwing. A later `FX.x()` that is not behind `if (FX)` throws here, which a
 * test that only read the source for `var FX = window.CUM_FX || null;` never saw.
 *
 * The DOM is a small fake: elements keep their attributes, classes and children, the page's own landmarks (#main, #screen, the
 * boot overlay, the nav) are registered by the scenario, and everything a script looks for that is not registered is not found.
 * Nothing here needs a browser or the network; every input is made up.
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const webui = process.argv[2];

class El {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.nodeType = 1;
    this._a = {};
    this._cls = new Set();
    this.children = [];
    this.childNodes = this.children;
    this.parentNode = null;
    this.firstChild = null;
    this.style = {};
    this.textContent = "";
    this.innerHTML = "";
    this.hidden = false;
    this.isConnected = true;
  }
  get id() { return this._a.id || ""; }
  set id(v) { this._a.id = String(v); }
  get className() { return Array.from(this._cls).join(" "); }
  set className(v) { this._cls = new Set(String(v).split(/\s+/).filter(Boolean)); }
  get classList() {
    const el = this;
    return {
      add: (...names) => names.forEach((n) => el._cls.add(n)),
      remove: (...names) => names.forEach((n) => el._cls.delete(n)),
      contains: (n) => el._cls.has(n),
      toggle: (n, on) => { if (on === undefined ? !el._cls.has(n) : on) { el._cls.add(n); } else { el._cls.delete(n); } },
    };
  }
  setAttribute(k, v) { this._a[k] = String(v); if (k === "class") { this.className = v; } }
  getAttribute(k) { return Object.prototype.hasOwnProperty.call(this._a, k) ? this._a[k] : null; }
  hasAttribute(k) { return Object.prototype.hasOwnProperty.call(this._a, k); }
  removeAttribute(k) { delete this._a[k]; }
  appendChild(n) { n.parentNode = this; this.children.push(n); return n; }
  insertBefore(n, ref) {
    n.parentNode = this;
    const at = ref ? this.children.indexOf(ref) : -1;
    if (at >= 0) { this.children.splice(at, 0, n); } else { this.children.push(n); }
    return n;
  }
  removeChild(n) { const at = this.children.indexOf(n); if (at >= 0) { this.children.splice(at, 1); } n.parentNode = null; return n; }
  remove() { if (this.parentNode) { this.parentNode.removeChild(this); } }
  addEventListener(type, fn) { (this._on = this._on || {})[type] = fn; }
  removeEventListener(type) { if (this._on) { delete this._on[type]; } }
  contains() { return false; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  closest() { return null; }
  matches() { return false; }
  cloneNode() { return new El(this.tagName); }
  focus() {}
  getContext() { return { measureText: () => ({ width: 10 }) }; }
  get offsetWidth() { return 100; }
}

/* One page: <html data-theme=...>, a manual clock with timers and animation frames, a session store, and the landmarks a
 * scenario asks for. No window.CUM_FX. */
function world(theme, opts) {
  opts = opts || {};
  const w = { now: 0, timers: [], frames: [], timerId: 0, store: Object.assign({}, opts.store), observers: [], listeners: [], thrown: [] };
  const root = new El("html");
  root.setAttribute("data-theme", theme);
  const bySelector = {};
  const find = (el, id) => {
    if (el._a.id === id) { return el; }
    for (const child of el.children) { const hit = find(child, id); if (hit) { return hit; } }
    return null;
  };
  const doc = {
    documentElement: root, body: new El("body"), head: new El("head"), visibilityState: "visible",
    getElementById: (id) => find(root, id),
    querySelector: (selector) => bySelector[selector] || null,
    querySelectorAll: () => [],
    createElement: (tag) => new El(tag),
    createTextNode: (text) => ({ nodeType: 3, nodeValue: text }),
    createTreeWalker: () => ({ nextNode: () => null }),
    addEventListener: (type, fn) => w.listeners.push({ type, fn }),
    removeEventListener() {},
  };
  const sandbox = {
    console, Math, JSON, Object, Array, String, Number, RegExp, Set, Map, WeakMap, WeakRef, Promise, Date,
    parseFloat, parseInt, isFinite, isNaN, queueMicrotask,
    document: doc, NodeFilter: { SHOW_TEXT: 4 }, location: { hash: "#overview" },
    performance: { now: () => w.now },
    setTimeout: (fn, ms) => { const id = ++w.timerId; w.timers.push({ id, at: w.now + (ms || 0), fn }); return id; },
    clearTimeout: (id) => { w.timers = w.timers.filter((t) => t.id !== id); },
    requestAnimationFrame: (fn) => { w.frames.push(fn); return w.frames.length; },
    matchMedia: () => ({ matches: !!opts.reduced, addEventListener() {} }),
    getComputedStyle: () => ({ color: "", textShadow: "" }),
    MutationObserver: class { constructor(cb) { this.cb = cb; this.watching = null; w.observers.push(this); } observe(target) { this.watching = target; } disconnect() { this.watching = null; } },
    fetch: () => Promise.resolve({ ok: false }),
    sessionStorage: { getItem: (k) => (k in w.store ? w.store[k] : null), setItem: (k, v) => { w.store[k] = String(v); } },
    addEventListener() {},
  };
  sandbox.window = sandbox;
  w.root = root;
  w.sandbox = sandbox;
  root.appendChild(doc.head);
  root.appendChild(doc.body);
  w.element = (id, tag) => { const el = new El(tag || "div"); el.id = id; doc.body.appendChild(el); return el; };
  w.landmark = (selector, el) => { bySelector[selector] = el; doc.body.appendChild(el); return el; };
  /* what a page left alone does next: every animation frame, then every timer in time order, until nothing is left */
  w.settle = () => {
    for (let guard = 0; (w.timers.length || w.frames.length) && guard < 5000; guard++) {
      const frames = w.frames;
      w.frames = [];
      frames.forEach((fn) => attempt(w, fn, w.now));
      w.timers.sort((a, b) => a.at - b.at);
      const next = w.timers.shift();
      if (next) { w.now = Math.max(w.now, next.at); attempt(w, next.fn); }
    }
    return !w.timers.length && !w.frames.length;
  };
  return w;
}

/* Runs fn; anything it throws is recorded (a page would log it and carry on) and answered as the message. */
function attempt(w, fn, arg) {
  try {
    fn(arg);
    return null;
  } catch (error) {
    const message = String(error && error.message ? error.message : error);
    w.thrown.push(message);
    return message;
  }
}

function load(w, theme) {
  const code = fs.readFileSync(path.join(webui, "themes", theme + ".js"), "utf8");
  vm.createContext(w.sandbox);
  let error = null;
  try {
    vm.runInContext(code, w.sandbox, { filename: theme + ".js" });
  } catch (e) {
    error = String(e && e.message ? e.message : e);
  }
  const hooks = (w.sandbox.CUM_THEME_FX && w.sandbox.CUM_THEME_FX[theme]) || null;
  return { error, hooks, engine: typeof w.sandbox.CUM_FX };
}

function run(theme, opts, scenario) {
  const w = world(theme, opts);
  const main = w.element("main", "main");
  const screen = w.element("screen", "div");
  const result = { loaded: false, error: null, engine: null, hooks: [], startIsFunction: false };
  if (scenario) { scenario.before(w, { main, screen }); }
  const loaded = load(w, theme);
  result.error = loaded.error;
  result.engine = loaded.engine;
  result.loaded = loaded.error === null && loaded.hooks !== null;
  result.hooks = loaded.hooks ? Object.keys(loaded.hooks).sort() : [];
  result.startIsFunction = !!loaded.hooks && typeof loaded.hooks.start === "function";
  if (!result.startIsFunction) { return { result, w }; }
  result.start = attempt(w, loaded.hooks.start);                 // null: it did not throw
  result.restart = attempt(w, loaded.hooks.start);               // app.js calls start() again whenever the theme is applied
  result.settled = w.settle();                                   // the frames and timers it left behind
  result.thrown = w.thrown.slice();
  return { result, w, hooks: loaded.hooks };
}

const out = {};

for (const theme of ["nostromo", "night_city", "gates"]) {
  const plain = run(theme, {});
  out[theme] = plain.result;

  // a user who asked for reduced motion
  out[theme].reduced = run(theme, { reduced: true }).result;

  // the theme switched off and on again, as it is when another theme is chosen in Settings and then this one
  if (plain.hooks) {
    const w = plain.w;
    const watching = w.observers.filter((o) => o.watching === w.root);
    w.thrown.length = 0;
    for (const to of ["nostromo", "night_city", "gates"].filter((id) => id !== theme).concat([theme])) {
      w.root.setAttribute("data-theme", to);
      watching.forEach((o) => attempt(w, o.cb, []));
      w.settle();
    }
    out[theme].themeSwitch = { observers: watching.length, thrown: w.thrown.slice() };
  }
}

// Nostromo's boot has to finish and flag the session by itself, with nobody to tell it the page is ready
{
  const boot = run("nostromo", {}, { before: (w) => { w.element("boot", "div"); w.element("boot-text", "pre"); } });
  out.nostromoBoot = { start: boot.result.start, restart: boot.result.restart, thrown: boot.result.thrown, settled: boot.result.settled,
    flagged: boot.w.store["nm-boot-session"] === "1", overlayHidden: boot.w.sandbox.document.getElementById("boot").hasAttribute("hidden") };
  const seen = run("nostromo", { store: { "nm-boot-session": "1" } }, { before: (w) => { w.element("boot", "div"); w.element("boot-text", "pre"); } });
  out.nostromoSeen = { thrown: seen.result.thrown, overlayHidden: seen.w.sandbox.document.getElementById("boot").hasAttribute("hidden") };
}

// Night City puts its decoration in the nav once (start() runs twice), and takes it out again when the theme goes
{
  let nav;
  const city = run("night_city", {}, { before: (w) => { nav = w.landmark(".nm-nav", new El("nav")); } });
  const w = city.w;
  const watching = w.observers.filter((o) => o.watching === w.root);
  const added = nav.children.length;
  const hiddenAtFirst = nav.children[0] ? nav.children[0].style.display : null;
  w.root.setAttribute("data-theme", "gates");
  watching.forEach((o) => attempt(w, o.cb, []));
  const afterOff = nav.children.length;
  w.root.setAttribute("data-theme", "night_city");
  watching.forEach((o) => attempt(w, o.cb, []));
  w.settle();
  out.nightCityDecoration = { added, hiddenAtFirst, afterOff, afterOn: nav.children.length, thrown: w.thrown.slice() };
}

console.log(JSON.stringify(out));
