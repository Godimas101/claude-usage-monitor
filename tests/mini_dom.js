"use strict";
/* A small DOM, for running the dashboard's real scripts under node with no browser (tests/english_scan.js,
 * tests/page_words.js).
 *
 * It parses HTML into a tree and writes the tree back out, finds elements with the selectors the dashboard uses (tag, *,
 * #id, .class, [attr], [attr=value] and the other attribute tests, :not() and unknown pseudo-classes, which match
 * nothing; descendant, child and sibling combinators; comma lists), keeps form state (value, checked, the selected
 * option), and dispatches events that bubble from the target to the document and the window. A MutationObserver hears
 * of added and removed children, text changes and attribute changes as a browser's does (its records arrive in a
 * microtask), and createTreeWalker walks a subtree in document order, so a theme script that rewrites the page's words
 * (gates.js) runs on it. There is no layout and no style: every element reports one fixed width, so the charts draw at
 * one size. Anything the scripts call that is not here throws, so a gap shows as an error rather than as missing text.
 */

const HTML_NS = "http://www.w3.org/1999/xhtml";
const SVG_NS = "http://www.w3.org/2000/svg";
const VOID = new Set(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"]);
const RAW_TEXT = new Set(["script", "style"]);              // their text is not HTML
const ESCAPABLE_RAW = new Set(["textarea", "title"]);       // their text is not HTML, but entities count
const ENTITIES = {
  amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " ", middot: "·", hellip: "…", times: "×",
  rsquo: "’", lsquo: "‘", rdquo: "”", ldquo: "“", mdash: "—", ndash: "–", copy: "©",
  rarr: "→", larr: "←", uarr: "↑", darr: "↓", bull: "•", deg: "°", plusmn: "±",
  minus: "−", thinsp: " ", ensp: " ", emsp: " ", zwj: "‍", zwnj: "‌", shy: "­",
};

// The end tags HTML leaves out: a start tag of `tag` closes an open element in `closes`, unless an element in `stops`
// lies between (a nested list, table or select keeps its own).
const IMPLIED = {
  li: { closes: ["li"], stops: ["ul", "ol", "menu"] },
  option: { closes: ["option"], stops: ["select", "datalist", "optgroup"] },
  optgroup: { closes: ["optgroup", "option"], stops: ["select"] },
  td: { closes: ["td", "th"], stops: ["tr", "table"] },
  th: { closes: ["td", "th"], stops: ["tr", "table"] },
  tr: { closes: ["tr", "td", "th"], stops: ["table", "thead", "tbody", "tfoot"] },
  thead: { closes: ["thead", "tbody", "tfoot", "tr", "td", "th"], stops: ["table"] },
  tbody: { closes: ["thead", "tbody", "tfoot", "tr", "td", "th"], stops: ["table"] },
  tfoot: { closes: ["thead", "tbody", "tfoot", "tr", "td", "th"], stops: ["table"] },
  dt: { closes: ["dt", "dd"], stops: ["dl"] },
  dd: { closes: ["dt", "dd"], stops: ["dl"] },
};
// Block starts that close an open <p>.
const CLOSES_P = new Set(["address", "article", "aside", "blockquote", "details", "div", "dl", "fieldset", "figcaption",
  "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "main", "menu", "nav", "ol", "p", "pre",
  "section", "table", "ul"]);

function decode(text) {
  return text.replace(/&(#[0-9]+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);/g, (whole, name) => {
    if (name[0] === "#") {
      const code = name[1] === "x" || name[1] === "X" ? parseInt(name.slice(2), 16) : parseInt(name.slice(1), 10);
      return Number.isFinite(code) && code >= 0 && code <= 0x10ffff ? String.fromCodePoint(code) : whole;
    }
    return Object.prototype.hasOwnProperty.call(ENTITIES, name) ? ENTITIES[name] : whole;
  });
}

const escapeText = (text) => text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const escapeAttr = (text) => text.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

// ── Events ─────────────────────────────────────────────────────────────────────

class Event {
  constructor(type, init) {
    this.type = String(type);
    this.bubbles = !!(init && init.bubbles);
    this.cancelable = !!(init && init.cancelable);
    this.detail = init && "detail" in init ? init.detail : null;
    this.key = init && init.key;
    this.defaultPrevented = false;
    this.target = null;
    this.currentTarget = null;
    this._stopped = false;
    this._stoppedNow = false;
  }
  preventDefault() { if (this.cancelable) { this.defaultPrevented = true; } }
  stopPropagation() { this._stopped = true; }
  stopImmediatePropagation() { this._stopped = true; this._stoppedNow = true; }
}
class CustomEvent extends Event {}

class Target {
  addEventListener(type, fn, options) {
    if (typeof fn !== "function" && !(fn && typeof fn.handleEvent === "function")) {
      return;
    }
    this._listeners = this._listeners || {};
    const list = (this._listeners[type] = this._listeners[type] || []);
    const once = !!(options && typeof options === "object" && options.once);
    if (!list.some((l) => l.fn === fn)) {
      list.push({ fn, once });
    }
  }
  removeEventListener(type, fn) {
    const list = this._listeners && this._listeners[type];
    if (list) {
      this._listeners[type] = list.filter((l) => l.fn !== fn);
    }
  }
  _fire(event, report) {
    const list = this._listeners && this._listeners[event.type];
    if (!list) {
      return;
    }
    for (const l of list.slice()) {
      if (l.once) {
        this.removeEventListener(event.type, l.fn);
      }
      event.currentTarget = this;
      try {
        if (typeof l.fn === "function") { l.fn.call(this, event); } else { l.fn.handleEvent(event); }
      } catch (e) {
        report(e, event);
      }
      if (event._stoppedNow) {
        return;
      }
    }
  }
}

// ── Mutation observers ─────────────────────────────────────────────────────────

// What a node's document's observers hear: a record goes to every observer of the node (or, with subtree, of a node that
// holds it) that asked for its type, and each observer's records arrive together in a microtask, as in a browser.
function notify(node, record) {
  const doc = node.ownerDocument;
  const regs = doc && doc._observers;
  if (!regs || !regs.length) {
    return;
  }
  for (const reg of regs.slice()) {
    const o = reg.options;
    const near = reg.target === record.target || (o.subtree && reg.target.contains(record.target));
    const wanted = record.type === "childList" ? o.childList
      : record.type === "characterData" ? o.characterData || o.characterDataOldValue
      : (o.attributes || o.attributeFilter || o.attributeOldValue) && (!o.attributeFilter || o.attributeFilter.indexOf(record.attributeName) >= 0);
    if (near && wanted) {
      reg.observer._take(record, doc);
    }
  }
}

class MutationObserver {
  constructor(callback) {
    if (typeof callback !== "function") {
      throw new TypeError("MutationObserver: the callback is not a function");
    }
    this._callback = callback;
    this._records = [];
    this._docs = new Set();
    this._due = false;
  }
  observe(target, options) {
    const doc = target.nodeType === 9 ? target : target.ownerDocument;
    doc._observers = (doc._observers || []).filter((r) => !(r.observer === this && r.target === target));
    doc._observers.push({ observer: this, target, options: options || {} });
    this._docs.add(doc);
  }
  disconnect() {
    this._docs.forEach((doc) => { doc._observers = (doc._observers || []).filter((r) => r.observer !== this); });
    this._docs.clear();
    this._records = [];
  }
  takeRecords() {
    const out = this._records;
    this._records = [];
    return out;
  }
  _take(record, doc) {
    this._records.push(record);
    if (this._due) {
      return;
    }
    this._due = true;
    Promise.resolve().then(() => {
      this._due = false;
      const records = this.takeRecords();
      if (records.length) {
        try { this._callback(records, this); } catch (e) { doc._report(e, { type: "mutation" }); }
      }
    });
  }
}

const childRecord = (target, added, removed) => ({ type: "childList", target, addedNodes: added, removedNodes: removed,
  attributeName: null, oldValue: null, previousSibling: null, nextSibling: null });

// What createTreeWalker's whatToShow names, by nodeType.
const NodeFilter = { SHOW_ALL: 0xFFFFFFFF, SHOW_ELEMENT: 0x1, SHOW_TEXT: 0x4, SHOW_COMMENT: 0x80, FILTER_ACCEPT: 1, FILTER_REJECT: 2,
  FILTER_SKIP: 3 };

// ── Nodes ──────────────────────────────────────────────────────────────────────

class Node extends Target {
  constructor(doc) {
    super();
    this.ownerDocument = doc;
    this.parentNode = null;
    this.childNodes = [];
  }
  get firstChild() { return this.childNodes[0] || null; }
  get lastChild() { return this.childNodes[this.childNodes.length - 1] || null; }
  get parentElement() { return this.parentNode && this.parentNode.nodeType === 1 ? this.parentNode : null; }
  get isConnected() {
    for (let n = this; n; n = n.parentNode) {
      if (n.nodeType === 9) { return true; }
    }
    return false;
  }
  get nextSibling() { return this._sibling(1); }
  get previousSibling() { return this._sibling(-1); }
  _sibling(step) {
    const p = this.parentNode;
    return p ? p.childNodes[p.childNodes.indexOf(this) + step] || null : null;
  }
  hasChildNodes() { return this.childNodes.length > 0; }
  appendChild(child) { return this.insertBefore(child, null); }
  append(...items) { items.forEach((i) => this.appendChild(typeof i === "string" ? this.ownerDocument.createTextNode(i) : i)); }
  insertBefore(child, ref) {
    if (!(child instanceof Node)) {
      throw new TypeError("insertBefore: not a node");
    }
    if (child.nodeType === 11) {
      child.childNodes.slice().forEach((c) => this.insertBefore(c, ref));
      return child;
    }
    for (let n = this; n; n = n.parentNode) {
      if (n === child) {
        throw new Error("HierarchyRequestError: a node cannot hold itself");
      }
    }
    if (ref !== null && ref !== undefined && ref.parentNode !== this) {
      throw new Error("NotFoundError: the reference node is not a child");
    }
    if (child.parentNode) {
      child.parentNode.removeChild(child);
    }
    const at = ref ? this.childNodes.indexOf(ref) : this.childNodes.length;
    this.childNodes.splice(at, 0, child);
    child.parentNode = this;
    notify(this, childRecord(this, [child], []));
    return child;
  }
  removeChild(child) {
    const at = this.childNodes.indexOf(child);
    if (at < 0) {
      throw new Error("NotFoundError: not a child");
    }
    this.childNodes.splice(at, 1);
    child.parentNode = null;
    notify(this, childRecord(this, [], [child]));
    return child;
  }
  _clear() {
    const gone = this.childNodes;
    gone.forEach((c) => { c.parentNode = null; });
    this.childNodes = [];
    if (gone.length) {
      notify(this, childRecord(this, [], gone));
    }
  }
  replaceChild(fresh, old) {
    this.insertBefore(fresh, old);
    return this.removeChild(old);
  }
  remove() { if (this.parentNode) { this.parentNode.removeChild(this); } }
  contains(other) {
    for (let n = other; n; n = n.parentNode) {
      if (n === this) { return true; }
    }
    return false;
  }
  get textContent() { return this.childNodes.map((c) => (c.nodeType === 8 ? "" : c.textContent)).join(""); }
  set textContent(value) {
    this._clear();
    const text = value === null || value === undefined ? "" : String(value);
    if (text) {
      this.appendChild(this.ownerDocument.createTextNode(text));
    }
  }
  dispatchEvent(event) {
    event.target = this;
    const path = [];
    for (let n = this; n; n = n.parentNode) {
      path.push(n);
    }
    const doc = this.ownerDocument;
    if (doc && path[path.length - 1] === doc && doc.defaultView) {
      path.push(doc.defaultView);
    }
    const report = doc ? doc._report : (e) => { throw e; };
    for (let i = 0; i < path.length; i++) {
      path[i]._fire(event, report);
      if (event._stopped || (!event.bubbles && i === 0)) {
        break;
      }
    }
    event.currentTarget = null;
    return !event.defaultPrevented;
  }
}

class Text extends Node {
  constructor(doc, data) {
    super(doc);
    this.nodeType = 3;
    this.nodeName = "#text";
    this._data = String(data);
  }
  get data() { return this._data; }
  set data(v) {
    const old = this._data;
    this._data = String(v);
    notify(this, { type: "characterData", target: this, addedNodes: [], removedNodes: [], attributeName: null, oldValue: old });
  }
  get nodeValue() { return this.data; }
  set nodeValue(v) { this.data = String(v); }
  get textContent() { return this.data; }
  set textContent(v) { this.data = String(v); }
}

class Comment extends Node {
  constructor(doc, data) {
    super(doc);
    this.nodeType = 8;
    this.nodeName = "#comment";
    this.data = String(data);
  }
  get textContent() { return this.data; }
}

class Fragment extends Node {
  constructor(doc) {
    super(doc);
    this.nodeType = 11;
    this.nodeName = "#document-fragment";
  }
  get children() { return this.childNodes.filter((c) => c.nodeType === 1); }
  get firstElementChild() { return this.children[0] || null; }
  querySelectorAll(selector) { return selectAll(this, selector); }
  querySelector(selector) { return selectAll(this, selector)[0] || null; }
}

class ClassList {
  constructor(el) { this.el = el; }
  _get() { return this._names().slice(); }
  _names() {                                          // the class names, read again only when the attribute changed
    const text = String(this.el.getAttribute("class") || "");
    if (text !== this._text) {
      this._text = text;
      this._list = text.split(/\s+/).filter(Boolean);
    }
    return this._list;
  }
  _set(list) { this.el.setAttribute("class", list.join(" ")); }
  get length() { return this._get().length; }
  item(i) { return this._get()[i] || null; }
  contains(c) { return this._names().indexOf(String(c)) >= 0; }
  add(...cs) { const l = this._get(); cs.forEach((c) => { if (l.indexOf(String(c)) < 0) { l.push(String(c)); } }); this._set(l); }
  remove(...cs) { const drop = cs.map(String); this._set(this._get().filter((c) => drop.indexOf(c) < 0)); }
  toggle(c, force) {
    const has = this.contains(c);
    const want = force === undefined ? !has : !!force;
    if (want && !has) { this.add(c); } else if (!want && has) { this.remove(c); }
    return want;
  }
  replace(a, b) { if (!this.contains(a)) { return false; } this._set(this._get().map((c) => (c === a ? String(b) : c))); return true; }
  forEach(fn) { this._get().forEach(fn); }
  toString() { return this._get().join(" "); }
}

class Style {
  constructor() { this._props = {}; }
  setProperty(name, value) { this._props[String(name)] = String(value); }
  removeProperty(name) { const v = this._props[name] || ""; delete this._props[name]; return v; }
  getPropertyValue(name) { return this._props[name] || ""; }
}

const dataName = (prop) => "data-" + String(prop).replace(/[A-Z]/g, (c) => "-" + c.toLowerCase());

class Element extends Node {
  constructor(doc, name, ns) {
    super(doc);
    this.nodeType = 1;
    this.namespaceURI = ns || HTML_NS;
    this.localName = this.namespaceURI === HTML_NS ? String(name).toLowerCase() : String(name);
    this._attrs = [];
    this._value = null;
    this._checked = null;
    this._selected = null;
    this.style = new Style();
    this.scrollTop = 0;
    this.scrollLeft = 0;
    this.classList = new ClassList(this);
    const el = this;
    this.dataset = new Proxy({}, {
      get: (_t, prop) => (typeof prop === "string" ? (el.getAttribute(dataName(prop)) ?? undefined) : undefined),
      set: (_t, prop, value) => { el.setAttribute(dataName(prop), value); return true; },
      deleteProperty: (_t, prop) => { el.removeAttribute(dataName(prop)); return true; },
      has: (_t, prop) => el.hasAttribute(dataName(prop)),
    });
    if (this.localName === "template") {
      this.content = new Fragment(doc);
    }
  }
  get tagName() { return this.namespaceURI === HTML_NS ? this.localName.toUpperCase() : this.localName; }
  get nodeName() { return this.tagName; }
  get attributes() { return this._attrs; }
  _attr(name) {
    const n = this.namespaceURI === HTML_NS ? String(name).toLowerCase() : String(name);
    return this._attrs.find((a) => a.name === n) || null;
  }
  getAttribute(name) { const a = this._attr(name); return a ? a.value : null; }
  setAttribute(name, value) {
    const a = this._attr(name);
    const old = a ? a.value : null;
    if (a) {
      a.value = String(value);
    } else {
      this._attrs.push({ name: this.namespaceURI === HTML_NS ? String(name).toLowerCase() : String(name), value: String(value) });
    }
    notify(this, { type: "attributes", target: this, addedNodes: [], removedNodes: [], attributeName: (a || this._attrs[this._attrs.length - 1]).name,
      oldValue: old });
  }
  removeAttribute(name) {
    const a = this._attr(name);
    if (a) {
      this._attrs.splice(this._attrs.indexOf(a), 1);
      notify(this, { type: "attributes", target: this, addedNodes: [], removedNodes: [], attributeName: a.name, oldValue: a.value });
    }
  }
  hasAttribute(name) { return this._attr(name) !== null; }
  toggleAttribute(name, force) {
    const want = force === undefined ? !this.hasAttribute(name) : !!force;
    if (want) { if (!this.hasAttribute(name)) { this.setAttribute(name, ""); } } else { this.removeAttribute(name); }
    return want;
  }
  get id() { return this.getAttribute("id") || ""; }
  set id(v) { this.setAttribute("id", v); }
  get className() { return this.getAttribute("class") || ""; }
  set className(v) { this.setAttribute("class", v); }
  get title() { return this.getAttribute("title") || ""; }
  set title(v) { this.setAttribute("title", v); }
  get href() { return this.getAttribute("href") || ""; }
  set href(v) { this.setAttribute("href", v); }
  get name() { return this.getAttribute("name") || ""; }
  get type() { return String(this.getAttribute("type") || (this.localName === "input" ? "text" : this.localName === "button" ? "submit" : "")).toLowerCase(); }
  set type(v) { this.setAttribute("type", v); }
  get hidden() { return this.hasAttribute("hidden"); }
  set hidden(v) { this.toggleAttribute("hidden", !!v); }
  get disabled() { return this.hasAttribute("disabled"); }
  set disabled(v) { this.toggleAttribute("disabled", !!v); }
  get checked() { return this._checked !== null ? this._checked : this.hasAttribute("checked"); }
  set checked(v) { this._checked = !!v; }
  get selected() { return this._selected !== null ? this._selected : this.hasAttribute("selected"); }
  set selected(v) {
    const select = this.closest ? this.closest("select") : null;
    if (v && select) { select.options.forEach((o) => { o._selected = false; }); }
    this._selected = !!v;
  }
  get options() { return this.localName === "select" ? selectAll(this, "option") : undefined; }
  get selectedIndex() {
    if (this.localName !== "select") { return undefined; }
    const opts = this.options;
    const at = opts.findIndex((o) => o.selected);
    return at >= 0 ? at : (opts.length ? 0 : -1);
  }
  set selectedIndex(i) { this.options.forEach((o, n) => { o._selected = n === i; }); }
  get value() {
    if (this.localName === "select") {
      const opt = this.options[this.selectedIndex];
      return opt ? opt.value : "";
    }
    if (this.localName === "option") {
      const v = this.getAttribute("value");
      return v !== null ? v : this.textContent.trim();
    }
    if (this.localName === "textarea") {
      return this._value !== null ? this._value : this.textContent;
    }
    if (this._value !== null) { return this._value; }
    const v = this.getAttribute("value");
    return v !== null ? v : (this.localName === "input" && (this.type === "checkbox" || this.type === "radio") ? "on" : "");
  }
  set value(v) {
    if (this.localName === "select") {
      this.options.forEach((o) => { o._selected = o.value === String(v); });
      return;
    }
    this._value = String(v);
  }
  get children() { return this.childNodes.filter((c) => c.nodeType === 1); }
  get childElementCount() { return this.children.length; }
  get firstElementChild() { return this.children[0] || null; }
  get lastElementChild() { const c = this.children; return c[c.length - 1] || null; }
  get nextElementSibling() { let n = this.nextSibling; while (n && n.nodeType !== 1) { n = n.nextSibling; } return n; }
  get previousElementSibling() { let n = this.previousSibling; while (n && n.nodeType !== 1) { n = n.previousSibling; } return n; }
  get innerHTML() {
    const host = this.localName === "template" ? this.content : this;
    return host.childNodes.map((c) => serialize(c, this.localName)).join("");
  }
  set innerHTML(html) {
    const host = this.localName === "template" ? this.content : this;
    host._clear();
    parseInto(host, String(html), this.ownerDocument, this.namespaceURI === SVG_NS || this.localName === "svg" ? SVG_NS : HTML_NS,
      RAW_TEXT.has(this.localName) || ESCAPABLE_RAW.has(this.localName) ? this.localName : null);
  }
  get outerHTML() { return serialize(this, null); }
  insertAdjacentHTML(where, html) {
    const box = new Fragment(this.ownerDocument);
    parseInto(box, String(html), this.ownerDocument, this.namespaceURI, null);
    const at = String(where).toLowerCase();
    if (at === "beforeend") { this.appendChild(box); } else if (at === "afterbegin") { this.insertBefore(box, this.firstChild); }
    else if (at === "beforebegin") { this.parentNode.insertBefore(box, this); } else if (at === "afterend") { this.parentNode.insertBefore(box, this.nextSibling); }
    else { throw new Error("insertAdjacentHTML: " + where); }
  }
  querySelectorAll(selector) { return selectAll(this, selector); }
  querySelector(selector) { return selectAll(this, selector)[0] || null; }
  getElementsByTagName(tag) { return selectAll(this, tag); }
  matches(selector) { return compile(selector)(this); }
  closest(selector) {
    const test = compile(selector);
    for (let n = this; n && n.nodeType === 1; n = n.parentNode) {
      if (test(n)) { return n; }
    }
    return null;
  }
  focus() { if (this.ownerDocument) { this.ownerDocument._active = this; } }
  blur() { if (this.ownerDocument && this.ownerDocument._active === this) { this.ownerDocument._active = null; } }
  click() {
    if (this.disabled) { return; }
    if (this.localName === "input" && this.type === "checkbox") { this.checked = !this.checked; }
    this.dispatchEvent(new Event("click", { bubbles: true, cancelable: true }));
  }
  select() {}
  scrollIntoView() {}
  scrollTo() {}
  getBoundingClientRect() {
    const w = this.ownerDocument ? this.ownerDocument._width : 0;
    return { x: 0, y: 0, left: 0, top: 0, right: w, bottom: 20, width: w, height: 20 };
  }
  get clientWidth() { return this.ownerDocument ? this.ownerDocument._width : 0; }
  get offsetWidth() { return this.clientWidth; }
  get clientHeight() { return 20; }
  get offsetHeight() { return 20; }
  get scrollHeight() { return 20; }
  getComputedTextLength() { return this.textContent.length * 7; }
  getBBox() { return { x: 0, y: 0, width: this.textContent.length * 7, height: 12 }; }
}

class Document extends Node {
  constructor(width) {
    super(null);
    this.ownerDocument = this;
    this.nodeType = 9;
    this.nodeName = "#document";
    this.readyState = "complete";
    this.visibilityState = "visible";
    this.hidden = false;
    this.defaultView = null;
    this._active = null;
    this._width = width;
    this._report = (e) => { throw e; };
  }
  get documentElement() { return this.childNodes.find((c) => c.nodeType === 1) || null; }
  get head() { return selectAll(this, "head")[0] || null; }
  get body() { return selectAll(this, "body")[0] || null; }
  get activeElement() { return this._active && this.contains(this._active) ? this._active : this.body; }
  get title() { const t = selectAll(this, "title")[0]; return t ? t.textContent.replace(/\s+/g, " ").trim() : ""; }
  set title(v) {
    let t = selectAll(this, "title")[0];
    if (!t) { t = this.createElement("title"); this.head.appendChild(t); }
    t.textContent = v;
  }
  get children() { return this.childNodes.filter((c) => c.nodeType === 1); }
  createElement(tag) { return new Element(this, tag, HTML_NS); }
  createElementNS(ns, tag) { return new Element(this, tag, ns === HTML_NS ? HTML_NS : ns); }
  createTextNode(text) { return new Text(this, text); }
  createComment(text) { return new Comment(this, text); }
  createDocumentFragment() { return new Fragment(this); }
  /* A walker over root's subtree in document order, root itself not included. whatToShow is NodeFilter's bits; filter, a
   * function or {acceptNode}, accepts a node with FILTER_ACCEPT (here a rejected node's children are still visited). */
  createTreeWalker(root, whatToShow, filter) {
    const show = whatToShow === undefined || whatToShow === null ? NodeFilter.SHOW_ALL : whatToShow;
    const accepts = (n) => {
      if (!(show & (1 << (n.nodeType - 1)))) { return false; }
      if (!filter) { return true; }
      return (typeof filter === "function" ? filter(n) : filter.acceptNode(n)) === NodeFilter.FILTER_ACCEPT;
    };
    const after = (n) => {
      if (n.childNodes.length && n.localName !== "template") { return n.childNodes[0]; }
      for (let m = n; m && m !== root; m = m.parentNode) {
        const next = m.nextSibling;
        if (next) { return next; }
      }
      return null;
    };
    const walker = {
      root, whatToShow: show, filter: filter || null, currentNode: root,
      nextNode() {
        for (let n = after(walker.currentNode); n; n = after(n)) {
          if (accepts(n)) { walker.currentNode = n; return n; }
        }
        return null;
      },
    };
    return walker;
  }
  getElementById(id) {
    const want = String(id);
    const find = (node) => {
      for (const c of node.childNodes) {
        if (c.nodeType === 1) {
          if (c.getAttribute("id") === want) { return c; }
          const inner = find(c);
          if (inner) { return inner; }
        }
      }
      return null;
    };
    return find(this);
  }
  querySelectorAll(selector) { return selectAll(this, selector); }
  querySelector(selector) { return selectAll(this, selector)[0] || null; }
  getElementsByTagName(tag) { return selectAll(this, tag); }
  hasFocus() { return true; }
}

// ── Parsing ────────────────────────────────────────────────────────────────────

const TAG = /<!--[\s\S]*?-->|<!\[CDATA\[[\s\S]*?\]\]>|<![^>]*>|<\?[^>]*>|<\/([A-Za-z][\w:.-]*)\s*>|<([A-Za-z][\w:.-]*)((?:\s+[^\s"'>\/=]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'=<>`]+))?)*)\s*(\/?)>/g;
const ATTR = /([^\s"'>\/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;

/* Parses html into parent's children. ns: the namespace new elements start in (an <svg> moves its contents into SVG's).
 * raw: the tag name when parent's text is not HTML (a <script> or a <title> given innerHTML). */
function parseInto(parent, html, doc, ns, raw) {
  if (raw) {
    if (html) { parent.appendChild(doc.createTextNode(ESCAPABLE_RAW.has(raw) ? decode(html) : html)); }
    return;
  }
  const open = [{ node: parent, ns: ns || HTML_NS, name: null }];
  const top = () => open[open.length - 1];
  const text = (t) => { if (t) { top().node.appendChild(doc.createTextNode(decode(t))); } };
  const closeTo = (index) => { open.length = Math.max(1, index); };
  const find = (names, stops) => {
    for (let i = open.length - 1; i >= 1; i--) {
      const name = open[i].name;
      if (names.indexOf(name) >= 0) { return i; }
      if (stops && stops.indexOf(name) >= 0) { return -1; }
    }
    return -1;
  };
  let at = 0;
  TAG.lastIndex = 0;
  let m;
  while ((m = TAG.exec(html)) !== null) {
    text(html.slice(at, m.index));
    at = TAG.lastIndex;
    const whole = m[0];
    if (whole.startsWith("<!--")) {
      top().node.appendChild(doc.createComment(whole.slice(4, -3)));
      continue;
    }
    if (whole.startsWith("<!") || whole.startsWith("<?")) {
      continue;                                                    // a doctype or CDATA: nothing the page shows
    }
    if (m[1]) {                                                    // an end tag closes its element and all opened after it
      const name = m[1].toLowerCase();
      const i = find([name, m[1]], null);
      if (i >= 1) { closeTo(i); }
      continue;
    }
    const given = m[2];
    const name = given.toLowerCase();
    const rule = IMPLIED[name];
    if (rule) {
      const i = find(rule.closes, rule.stops);
      if (i >= 1) { closeTo(i); }
    }
    if (CLOSES_P.has(name)) {
      const i = find(["p"], ["button", "table", "ul", "ol", "li", "td", "th", "div", "section", "article", "nav", "header", "footer", "main", "aside"]);
      if (i >= 1) { closeTo(i); }
    }
    const space = name === "svg" ? SVG_NS : top().ns;
    const el = new Element(doc, space === HTML_NS ? name : given, space);
    ATTR.lastIndex = 0;
    let a;
    while ((a = ATTR.exec(m[3] || "")) !== null) {
      const value = a[2] !== undefined ? a[2] : a[3] !== undefined ? a[3] : a[4] !== undefined ? a[4] : "";
      if (!el.hasAttribute(a[1])) { el.setAttribute(a[1], decode(value)); }
    }
    top().node.appendChild(el);
    if (VOID.has(name) && space === HTML_NS) {
      continue;
    }
    if (m[4] === "/") {
      continue;                                                    // <path/>: an element with no contents
    }
    if (space === HTML_NS && (RAW_TEXT.has(name) || ESCAPABLE_RAW.has(name))) {
      // The closing tag is looked for in the markup itself: a lowercased copy can be longer than it (İ lowercases to two
      // code units), so its indexes are not the markup's.
      const closing = new RegExp("</" + name, "ig");
      closing.lastIndex = at;
      const found = closing.exec(html);
      const end = found ? found.index : -1;
      const body = html.slice(at, end < 0 ? html.length : end);
      if (body) { el.appendChild(doc.createTextNode(RAW_TEXT.has(name) ? body : decode(body))); }
      const close = end < 0 ? html.length : html.indexOf(">", end);
      at = close < 0 ? html.length : close + 1;
      TAG.lastIndex = at;
      continue;
    }
    open.push({ node: el.localName === "template" ? el.content : el, ns: space, name: el.localName });
  }
  text(html.slice(at));
}

// ── Writing ────────────────────────────────────────────────────────────────────

function serialize(node, parentName) {
  if (node.nodeType === 3) {
    return parentName && (RAW_TEXT.has(parentName)) ? node.data : escapeText(node.data);
  }
  if (node.nodeType === 8) {
    return "<!--" + node.data + "-->";
  }
  if (node.nodeType === 11 || node.nodeType === 9) {
    return node.childNodes.map((c) => serialize(c, null)).join("");
  }
  const name = node.localName;
  const attrs = node._attrs.map((a) => " " + a.name + '="' + escapeAttr(a.value) + '"').join("");
  if (VOID.has(name) && node.namespaceURI === HTML_NS) {
    return "<" + name + attrs + ">";
  }
  const kids = (name === "template" ? node.content : node).childNodes.map((c) => serialize(c, name)).join("");
  return "<" + name + attrs + ">" + kids + "</" + name + ">";
}

// ── Selectors ──────────────────────────────────────────────────────────────────

const SIMPLE = /^(?:(\*|[A-Za-z][\w-]*)|#((?:[\w-]|\\.)+)|\.((?:[\w-]|\\.)+)|\[\s*([\w:-]+)\s*(?:([~|^$*]?=)\s*(?:"([^"]*)"|'([^']*)'|([^\]\s]+))\s*(i)?)?\s*\]|:(not)\(([^()]*(?:\([^()]*\))?[^()]*)\)|::?([\w-]+)(?:\([^)]*\))?)/;
const cache = new Map();

function splitTop(text, sep) {
  const out = [];
  let depth = 0;
  let quote = null;
  let start = 0;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quote) { if (c === quote) { quote = null; } continue; }
    if (c === '"' || c === "'") { quote = c; } else if (c === "(" || c === "[") { depth++; } else if (c === ")" || c === "]") { depth--; }
    else if (c === sep && depth === 0) { out.push(text.slice(start, i)); start = i + 1; }
  }
  out.push(text.slice(start));
  return out;
}

function compound(text) {
  const tests = [];
  let rest = text;
  while (rest) {
    const m = SIMPLE.exec(rest);
    if (!m) {
      throw new SyntaxError("the small DOM cannot read the selector " + JSON.stringify(text));
    }
    rest = rest.slice(m[0].length);
    if (m[1]) {
      const tag = m[1];
      if (tag !== "*") { tests.push((el) => el.localName.toLowerCase() === tag.toLowerCase()); }
    } else if (m[2]) {
      const id = m[2].replace(/\\(.)/g, "$1");
      tests.push((el) => el.getAttribute("id") === id);
    } else if (m[3]) {
      const cls = m[3].replace(/\\(.)/g, "$1");
      tests.push((el) => el.classList.contains(cls));
    } else if (m[4]) {
      const name = m[4];
      const op = m[5];
      const want = m[6] !== undefined ? m[6] : m[7] !== undefined ? m[7] : m[8];
      const fold = m[9] === "i" ? (s) => s.toLowerCase() : (s) => s;
      tests.push((el) => {
        const v = el.getAttribute(name);
        if (v === null) { return false; }
        if (!op) { return true; }
        const have = fold(v);
        const w = fold(String(want));
        switch (op) {
          case "=": return have === w;
          case "~=": return have.split(/\s+/).indexOf(w) >= 0;
          case "|=": return have === w || have.startsWith(w + "-");
          case "^=": return w !== "" && have.startsWith(w);
          case "$=": return w !== "" && have.endsWith(w);
          case "*=": return w !== "" && have.indexOf(w) >= 0;
          default: return false;
        }
      });
    } else if (m[10]) {
      const inner = compile(m[11]);
      tests.push((el) => !inner(el));
    } else {
      tests.push(() => false);                      // :hover, :focus and the like: no pointer, nothing is under it
    }
  }
  return (el) => tests.every((t) => t(el));
}

function complex(text) {
  // Compound selectors and the combinators between them; a space or a > inside [..], (..) or quotes is not one.
  const parts = [];
  const src = text.trim();
  let depth = 0;
  let quote = null;
  let piece = "";
  let comb = null;
  for (let i = 0; i < src.length; i++) {
    const c = src[i];
    if (quote) {
      piece += c;
      if (c === quote) { quote = null; }
      continue;
    }
    if (c === '"' || c === "'") { quote = c; } else if (c === "[" || c === "(") { depth++; } else if (c === "]" || c === ")") { depth--; }
    if (depth === 0 && (c === " " || c === ">" || c === "+" || c === "~" || c === "\t" || c === "\n")) {
      if (c !== " " && c !== "\t" && c !== "\n") { comb = c; } else if (!comb) { comb = " "; }
      continue;
    }
    if (comb !== null && piece) {
      parts.push({ test: compound(piece) });
      parts.push({ comb: comb });
      piece = "";
    }
    comb = null;
    piece += c;
  }
  if (!piece) {
    throw new SyntaxError("the small DOM cannot read the selector " + JSON.stringify(text));
  }
  parts.push({ test: compound(piece) });
  return (el) => {
    const match = (i, node) => {
      if (!parts[i].test(node)) { return false; }
      if (i === 0) { return true; }
      const comb = parts[i - 1].comb;
      if (comb === ">") {
        const p = node.parentNode;
        return !!(p && p.nodeType === 1 && match(i - 2, p));
      }
      if (comb === " ") {
        for (let p = node.parentNode; p && p.nodeType === 1; p = p.parentNode) {
          if (match(i - 2, p)) { return true; }
        }
        return false;
      }
      if (comb === "+") {
        const s = node.previousElementSibling;
        return !!(s && match(i - 2, s));
      }
      for (let s = node.previousElementSibling; s; s = s.previousElementSibling) {
        if (match(i - 2, s)) { return true; }
      }
      return false;
    };
    return match(parts.length - 1, el);
  };
}

function compile(selector) {
  const key = String(selector);
  if (!cache.has(key)) {
    const groups = splitTop(key, ",").map((g) => complex(g));
    cache.set(key, (el) => el.nodeType === 1 && groups.some((g) => g(el)));
  }
  return cache.get(key);
}

function selectAll(root, selector) {
  const test = compile(selector);
  const out = [];
  const walk = (node) => {
    const kids = node.localName === "template" && node.content ? [] : node.childNodes;
    for (const c of kids) {
      if (c.nodeType === 1) {
        if (test(c)) { out.push(c); }
        walk(c);
      }
    }
  };
  walk(root);
  return out;
}

// ── A page ─────────────────────────────────────────────────────────────────────

/* A document parsed from html. width: what every element reports as its width (charts draw at it). report(error, event):
 * called with any error a listener throws (a browser reports it and goes on). */
function createDocument(html, options) {
  const doc = new Document((options && options.width) || 640);
  if (options && options.report) {
    doc._report = options.report;
  }
  parseInto(doc, String(html), doc, HTML_NS, null);
  if (!doc.documentElement) {
    doc.appendChild(doc.createElement("html"));
  }
  return doc;
}

module.exports = { createDocument, Event, CustomEvent, Target, Node, Element, Text, Document, serialize, compile, decode, HTML_NS, SVG_NS,
  MutationObserver, NodeFilter };
