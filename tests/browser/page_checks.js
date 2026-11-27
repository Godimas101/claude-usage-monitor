"use strict";
/* What tests/browser/page_probe.js (headless Chromium) and the real-web-view tests in tests/test_server.py (WebView2) read
 * from the dashboard, the same way in both (#98). Each function runs in the page and uses nothing outside itself, so Playwright
 * can hand it to page.evaluate and pywebview can evaluate this whole file before calling one of them.
 *
 *   settled()          the page has drawn its cards, nothing in #main is a placeholder (but the burn card's), and no boot
 *                      and no entrance is playing
 *   measure()          everything in the header, the nav and #main that runs past its room (page_probe.js's header says what
 *                      counts); {vw, vh, lang, over: [{at, kind, ...}], meters}
 *   regions(args)      every region tests/page_words.js reads (the header, the sidebar, each top part of #main, the cards
 *                      the record keeps), each text node in its element's computed text-transform, with the attributes
 *                      people read: args [page, with the header and sidebar, the cards kept ("*", a list, or null)]
 *   readCaps(sels)     each element of each selector: shown (innerText), stored (textContent), text-transform, hidden
 *   CSS_CAPS           the elements that have their capitals from the look's stylesheet since #98, and their page
 *   writeLines(args)   writes each line into an element (its textContent) and reads it back: args [selector, lines]
 *   readUpdate()       Settings' update row and buttons, and the update banner's heading, sentence and buttons
 */

function settled() {
  var main = document.getElementById("main");
  var cards = main ? main.querySelectorAll(".nm-card") : [];
  if (!cards.length || main.classList.contains("fx-run")) { return false; }
  // a boot is due or playing (WebView2 without reduced motion, once a session): the entrance waits for it (motion.js)
  var fx = window.USAGE_MONITOR_FX;
  if (document.documentElement.hasAttribute("data-fx-boot") || (fx && fx.boot && fx.boot.active)) { return false; }
  return !Array.prototype.some.call(main.querySelectorAll("[data-pending], .nm-card[data-state=pending]"),
    function (el) { return !el.closest('[data-card="burn"]'); });
}

/* Everything in the header, the nav and the page that runs past its room. */
function measure() {
  const where = (el) => {
    const parts = [];
    for (let n = el; n && n.nodeType === 1 && parts.length < 6; n = n.parentElement) {
      let p = n.tagName.toLowerCase();
      if (n.id) { p += '#' + n.id; }
      const cls = [...n.classList].filter((c) => /^(nm-|mon-|nc-|k$|v$|l$|fc$|t$|d$)/.test(c)).slice(0, 2);
      if (cls.length) { p += '.' + cls.join('.'); }
      for (const a of ['data-card', 'data-limit', 'data-page']) { if (n.hasAttribute(a)) { p += '[' + a + '=' + n.getAttribute(a) + ']'; } }
      parts.unshift(p);
      if (n.matches('.nm-card, .nm-bar, .nm-nav, .nm-msg, .nm-update')) { break; }
    }
    return parts.join('>');
  };
  const shown = (el) => {
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      const cs = getComputedStyle(n);
      if (cs.display === 'none' || cs.visibility === 'hidden' || cs.visibility === 'collapse') { return false; }
    }
    return el.getClientRects().length > 0;
  };
  // the box of the element's own words as drawn, each line as high as its line box (a big figure set at line-height 1 has a
  // glyph box taller than its line, and that overhang is the design), and how many lines they take
  const textRect = (el) => {
    let r = null;
    const tops = [];
    const lh = parseFloat(getComputedStyle(el).lineHeight);
    for (const n of el.childNodes) {
      if (n.nodeType !== 3 || !n.nodeValue.trim()) { continue; }
      const range = document.createRange();
      range.selectNodeContents(n);
      for (const q of range.getClientRects()) {
        if (q.width <= 0) { continue; }
        if (!tops.some((t) => Math.abs(t - q.top) < 2)) { tops.push(q.top); }
        const trim = lh > 0 && q.height > lh ? (q.height - lh) / 2 : 0;
        const top = q.top + trim;
        const bottom = q.bottom - trim;
        r = r ? { left: Math.min(r.left, q.left), right: Math.max(r.right, q.right), top: Math.min(r.top, top), bottom: Math.max(r.bottom, bottom) }
          : { left: q.left, right: q.right, top, bottom };
      }
    }
    if (r) { r.lines = tops.length; }
    return r;
  };
  // the part of r an element cuts off (overflow hidden or clip) leaves no mark: r is clamped to every such box from el up to top
  const visibleRect = (el, top, r) => {
    const out = Object.assign({}, r);
    for (let n = el; n && n !== top; n = n.parentElement) {
      const cs = getComputedStyle(n);
      const b = n.getBoundingClientRect();
      if (/hidden|clip/.test(cs.overflowX)) { out.left = Math.max(out.left, b.left); out.right = Math.min(out.right, b.right); }
      if (/hidden|clip/.test(cs.overflowY)) { out.top = Math.max(out.top, b.top); out.bottom = Math.min(out.bottom, b.bottom); }
    }
    return out;
  };
  const own = (el) => [...el.childNodes].some((n) => n.nodeType === 3 && n.nodeValue.trim());
  const WIDE = '.nm-menu a, .nm-mrow > *, .nm-chip, .nm-tag, .nm-uplink, #data-age, .nm-refresh, .nm-sw button, .nm-kick';
  const FIXED = '.nm-mrow, .nm-menu a, .nm-bar, .nm-refresh, .nm-chip, .nm-sw button, .nm-btn, .nm-pill';
  const over = [];
  const roots = [document.querySelector('.nm-bar'), document.querySelector('.nm-nav'), document.getElementById('main')].filter(Boolean);
  for (const root of roots) {
    for (const el of [root, ...root.querySelectorAll('*')]) {
      if (el.closest('svg, script, style, .nm-sr, [aria-hidden="true"]') || !shown(el)) { continue; }
      const cs = getComputedStyle(el);
      const inline = cs.display === 'inline';
      const text = (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 90);
      // wider than its box
      if (!inline && el.clientWidth > 0 && (/nowrap|pre/.test(cs.whiteSpace) || el.matches(WIDE)) && el.scrollWidth > el.clientWidth + 1) {
        over.push({ at: where(el), kind: 'wide', sw: el.scrollWidth, cw: el.clientWidth, text });
      }
      // taller than a box that cannot grow: one that cuts what runs past it, or one of a fixed height whose words take more
      // lines than it has room for (a figure taller than its row, as Nostromo's model rows draw theirs, is the design)
      const shut = (v) => v === 'hidden' || v === 'clip';
      if (!inline && el !== root && el.clientHeight > 0 && (shut(cs.overflowX) || shut(cs.overflowY)) && el.scrollHeight > el.clientHeight + 1) {
        over.push({ at: where(el), kind: 'tall', sh: el.scrollHeight, ch: el.clientHeight, text });
      } else if (!inline && el.matches(FIXED)) {
        const b = el.getBoundingClientRect();
        for (const d of [el, ...el.querySelectorAll('*')]) {
          const r = own(d) && shown(d) ? textRect(d) : null;
          if (r && r.lines > 1 && (r.bottom > b.bottom + 0.5 || r.top < b.top - 0.5)) {
            over.push({ at: where(el), kind: 'tall', lines: r.lines, by: +Math.max(r.bottom - b.bottom, b.top - r.top).toFixed(1), text });
            break;
          }
        }
      }
      // words that leave their card, the bar or the nav
      if (!own(el)) { continue; }
      const r = textRect(el);
      if (!r) { continue; }
      const card = el.closest('.nm-card-in');
      const bar = el.closest('.nm-bar');
      const nav = el.closest('.nm-nav');
      const box = card || bar || nav;
      if (!box) { continue; }
      const b = box.getBoundingClientRect();
      const v = visibleRect(el, box, r);
      if (v.right <= v.left || v.bottom <= v.top) { continue; }
      const sides = Math.max(v.right - b.right, b.left - v.left);
      const ends = box === nav ? 0 : Math.max(v.bottom - b.bottom, b.top - v.top);
      if (sides > 0.5 || ends > 0.5) {
        over.push({ at: where(el), kind: sides > 0.5 ? 'leaves-side' : 'leaves-end', by: +Math.max(sides, ends).toFixed(1),
          box: card ? 'card' : bar ? 'bar' : 'nav', text });
      }
    }
  }
  // words drawn over other words in the same card, the bar or the nav (more than 2 px each way: a tall figure's glyph box may
  // touch the line beside it)
  for (const box of document.querySelectorAll('.nm-card-in, .nm-bar, .nm-nav')) {
    if (!shown(box)) { continue; }
    const words = [];
    for (const el of box.querySelectorAll('*')) {
      if (el.closest('svg, script, style, .nm-sr, [aria-hidden="true"]') || !own(el) || !shown(el)) { continue; }
      const r = textRect(el);
      if (!r) { continue; }
      const v = visibleRect(el, box, r);
      if (v.right > v.left && v.bottom > v.top) { words.push([el, v]); }
    }
    for (let i = 0; i < words.length; i++) {
      for (let j = i + 1; j < words.length; j++) {
        const [a, ra] = words[i];
        const [b, rb] = words[j];
        if (a.contains(b) || b.contains(a)) { continue; }
        const x = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left);
        const y = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
        if (x > 2 && y > 2) {
          over.push({ at: where(a) + ' / ' + where(b), kind: 'words-overlap', by: [+x.toFixed(1), +y.toFixed(1)],
            text: (a.innerText || '').trim().slice(0, 40) + ' / ' + (b.innerText || '').trim().slice(0, 40) });
        }
      }
    }
  }
  // the nav's blocks, and the header's parts, on top of each other
  const overlap = (a, b) => {
    const x = Math.min(a.right, b.right) - Math.max(a.left, b.left);
    const y = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
    return x > 0.5 && y > 0.5;
  };
  const pairs = (els, kind) => {
    const boxes = [...new Set(els)].filter((el) => el && shown(el)).map((el) => [el, el.getBoundingClientRect()]).filter(([, r]) => r.width > 0 && r.height > 0);
    for (let i = 0; i < boxes.length; i++) {
      for (let j = i + 1; j < boxes.length; j++) {
        if (overlap(boxes[i][1], boxes[j][1])) { over.push({ at: where(boxes[i][0]) + ' / ' + where(boxes[j][0]), kind }); }
      }
    }
  };
  pairs(['.nm-nav > .nm-kick', '.nm-menu', '.nm-health', '.nm-phos', '#nc-deco', '.nc-deco'].map((s) => document.querySelector(s)), 'nav-overlap');
  const right = document.querySelector('.nm-bar-r');
  pairs([document.querySelector('.nm-brand'), right], 'bar-overlap');
  if (right) { pairs([...right.children], 'bar-overlap'); }
  // a meter's two bars start at the same x
  const meters = [...document.querySelectorAll('#main .nm-meter')].filter(shown).map((m) => {
    const lefts = [...m.querySelectorAll('.nm-cells')].map((c) => +c.getBoundingClientRect().left.toFixed(2));
    return { at: where(m), lefts };
  });
  meters.forEach((m) => {
    if (m.lefts.length >= 2 && Math.max(...m.lefts) - Math.min(...m.lefts) > 0.5) { over.push({ at: m.at, kind: 'meter', lefts: m.lefts }); }
  });
  return { vw: innerWidth, vh: innerHeight, lang: document.documentElement.lang, over, meters: meters.length };
}

/* Every region tests/page_words.js reads, as the browser draws it: page_words.js's region and mainParts, with the browser's own
 * cascade for the case. Night City's nav decoration (night_city.js, which the record does not run) is left out. */
function regions(args) {
  const [page, withChrome, kept] = args;
  const ATTRS = ['title', 'aria-label', 'data-tip', 'alt', 'placeholder'];
  const shownIn = (text, tt) => (tt === 'uppercase' ? text.toUpperCase() : tt === 'lowercase' ? text.toLowerCase() : text);
  const region = (root) => {
    let text = '';
    const attrs = [];
    const walk = (node) => {
      if (node.nodeType === 3) {
        const host = node.parentNode;
        text += shownIn(node.data, host && host.nodeType === 1 ? getComputedStyle(host).textTransform : 'none');
        return;
      }
      if (node.nodeType !== 1 || node.localName === 'script' || node.localName === 'style' || node.id === 'nc-deco' ||
          (node.classList && node.classList.contains('nc-deco'))) { return; }
      ATTRS.forEach((name) => { if (node.hasAttribute(name)) { attrs.push([name, node.getAttribute(name)]); } });
      node.childNodes.forEach(walk);
    };
    walk(root);
    return { text, attrs };
  };
  const out = {};
  if (withChrome) {
    [['brand', '.nm-brand'], ['uplink', '#uplink'], ['age', '#data-age'], ['refresh', '#refresh'], ['nav', 'nav.nm-nav']]
      .forEach(([name, sel]) => { out['chrome:' + name] = region(document.querySelector(sel)); });
  }
  const main = document.getElementById('main');
  [...main.children].forEach((el) => {
    if (el.__key === 'grid') {
      [...el.children].forEach((c) => { if (kept === '*' || (kept && kept.indexOf(c.__key) >= 0)) { out['card:' + c.__key] = region(c); } });
    } else if (el.__key) {
      out['top:' + el.__key] = region(el);
    } else if (!el.classList.contains('nm-pgrid') && !el.classList.contains('nm-grid')) {
      out['top:' + (el.classList.contains('nm-head') ? 'head' : el.id || el.className)] = region(el);
    }
  });
  return out;
}

/* The elements that have their capitals from the look's stylesheet since #98 (the page used to store them), and the page each
 * is on ("*": both). Edgerun alone shows the time-left bar; an element hidden in a look is left out by readCaps. */
var CSS_CAPS = [
  ["#uplink-text", "*"], ["#data-age", "*"], ["#refresh", "*"], [".nm-menu a > span", "*"], [".nm-nav > .nm-kick", "*"],
  [".nm-health .nm-kick", "*"], [".nm-health .nm-kv span", "*"], [".nm-health .nm-kv b", "*"], ["#phos-label", "*"], [".nm-h1", "*"],
  ["#main .nm-tag", "*"], ["#main .nm-facts span", "overview"], ["#main .nm-chip", "*"], ["#main .nm-pace", "overview"],
  ["#main .fc", "overview"], ["#main .nm-meta-t", "*"], ["#main .nm-ttl", "*"], ["#main .nm-tile small", "overview"],
  ["#main .nm-msg > b", "*"], ["#main .nm-remain .l", "overview"]
];

/* Each element of each selector: its text as shown (innerText, which applies text-transform) and as stored (textContent), its
 * text-transform, whether it is hidden (display: none on it or above it), and its language. */
function readCaps(sels) {
  return sels.map(function (sel) {
    return Array.prototype.map.call(document.querySelectorAll(sel), function (el) {
      var hidden = false;
      for (var n = el; n && n.nodeType === 1; n = n.parentElement) {
        if (getComputedStyle(n).display === "none") { hidden = true; }
      }
      var marked = el.closest("[lang]");
      return { sel: sel, shown: el.innerText, stored: el.textContent, tt: getComputedStyle(el).textTransform, hidden: hidden,
        lang: marked ? marked.getAttribute("lang") : null };
    });
  });
}

/* Writes each line into the element (its textContent, as the page writes a line) and reads it back as shown and as stored. */
function writeLines(args) {
  var el = document.querySelector(args[0]);
  if (!el) { return null; }
  el.hidden = false;
  return args[1].map(function (text) {
    el.textContent = text;
    return { shown: el.innerText, stored: el.textContent, tt: getComputedStyle(el).textTransform };
  });
}

/* Settings' update row (its title and help) and its buttons, and the update banner's heading, sentence and buttons: each as
 * shown and as stored. */
function readUpdate() {
  var item = function (el) { return el ? { shown: el.innerText, stored: el.textContent } : null; };
  var row = document.querySelector('input[data-key="check_updates"]');
  var label = row ? row.closest("label") : null;
  var banner = document.querySelector(".nm-update");
  return {
    settings: label ? { title: item(label.querySelector(".t")), help: item(label.querySelector(".d")),
      buttons: Array.prototype.filter.call(document.querySelectorAll("#set-update-actions .nm-btn"), function (b) { return !b.hidden; }).map(item) } : null,
    banner: banner ? { title: item(banner.querySelector(".nm-update-t > b")), sentence: item(banner.querySelector(".nm-update-t > span")),
      buttons: Array.prototype.map.call(banner.querySelectorAll(".nm-btn"), item) } : null
  };
}

if (typeof module !== "undefined") {
  module.exports = { settled: settled, measure: measure, regions: regions, readCaps: readCaps, CSS_CAPS: CSS_CAPS,
    writeLines: writeLines, readUpdate: readUpdate };
}
