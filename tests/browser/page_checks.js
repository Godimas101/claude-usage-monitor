"use strict";
/* What tests/browser/page_probe.js (headless Chromium) and the real-web-view tests in tests/test_server.py (WebView2) read
 * from the dashboard, the same way in both (#98). Each function runs in the page and uses nothing outside itself, so Playwright
 * can hand it to page.evaluate and pywebview can evaluate this whole file before calling one of them.
 *
 *   settled()          the page has drawn its cards, nothing in #main is a placeholder (but the burn card's), and no boot
 *                      and no entrance is playing
 *   DESIGNED_CUTS      the elements a look may cut with an ellipsis outside English, where a row's height is fixed (#98's
 *                      owner question 1, decided on #98); NEVER_CUT the kinds of element never cut; BAR_FLOOR the narrowest
 *                      a meter's bar may be outside English
 *   measure()          everything in the header, the nav and #main that runs past its room (page_probe.js's header says what
 *                      counts); {vw, vh, dpr, lang, over: [{at, kind, ...}], meters, bars, styled}. An element cut with a visible
 *                      ellipsis carries cut: {designed, role, full, title, named, catalog, lang}; one cut by less than a px
 *                      (its words past its content box, which whole-px scroll widths do not show) carries by, to 0.1 px
 *   meterStress(text)  the first meter with each label replaced by text, read and put back: its bars and its labels
 *   burnStress()       the burn card's chip with a word longer than its room beside the title, read and put back: whether it
 *                      moved below the title, the title's lines before and with it, and its right edge against the card's
 *   navStress()        the first nav tab with its label written long enough for two lines, read and put back: its lines, and
 *                      how far its words sit inside the tab's top and bottom
 *   sentenceStress(w)  the first limit card's pace line, verdict and forecast each written as the one word w, read and put
 *                      back: how far each one's words run past the card
 *   rooms()            the room each constrained text has in the window: px, and about how many capitals fit
 *   regions(args)      every region tests/page_words.js reads (the header, the sidebar, each top part of #main, the cards
 *                      the record keeps), each text node in its element's computed text-transform, with the attributes
 *                      people read: args [page, with the header and sidebar, the cards kept ("*", a list, or null)]
 *   readCaps(sels)     each element of each selector: shown (innerText), stored (textContent), text-transform, hidden
 *   CSS_CAPS           the elements that have their capitals from the look's stylesheet since #98, and their page
 *   writeLines(args)   writes each line into an element (its textContent) and reads it back: args [selector, lines]
 *   readUpdate()       Settings' update row and buttons, and the update banner's heading, sentence and buttons
 *   header()           the header bar's row at the window's width: {vw, sw, cw, refresh, ageShown, healthShown, link,
 *                      lines, inBar, cut, overlap}
 *   chips()            each chip in #main (.nm-chip, and .nm-pace > b where a look frames it as one): where its words' ink sits
 *                      against its box and whether the look draws a frame there, and how far the chip and its words run out
 *                      of its card, and how many lines its words take: [{at, text, top, bottom, h, framed, chip, lines,
 *                      out, wordsOut}]
 *
 * The real-web-view overflow test reads measure() and chips(); header(), meterStress(), burnStress(), navStress(),
 * sentenceStress() and rooms() are the stand-in's alone (they write words into the page or need its routed states).
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

/* The designed cuts outside English (#98's owner question 1, decided on #98: wrap first, then the translator's short form,
 * an ellipsis last, only where a row's height is fixed and the whole text can still be read). Each: its id, the elements
 * (sel), the looks that cut them (by the records' names) and why. A cut of one passes only in one of its looks, with a
 * visible ellipsis (computed text-overflow: ellipsis, overflow hidden, nowrap), its text a whole catalog string (no script
 * cut it), its title that text and an ancestor's aria-label holding it; and, in a real language, never in the default
 * window (#108). tests/test_server.py keeps the same list beside its record of English's own overflows. */
var DESIGNED_CUTS = [
  {"id": "overview-meta", "sel": "#main .nm-grid .nm-card-t:not(.nm-chart-t) > .nm-meta-t", "looks": ["nostromo", "night_city", "edgerun"],
   "why": "an Overview card's meta: the Overview's rows keep their height, and the meta describes the card; it is not data"},
  {"id": "overview-chip", "sel": "#main .nm-grid .nm-row2 > .nm-facts > .nm-chip", "looks": ["nostromo"],
   "why": "a limit card's pace chip on the Overview: its facts column beside the figure has no more room, and the pace line under it says the same in a sentence"}
];

/* The kinds of element never cut, in any language: a nav tab, the header's link words, a sentence line, a figure. Outside
 * English an ellipsis set on one is a fault even where its words fit (measure()'s styled). */
var NEVER_CUT = [
  ["nav tab", ".nm-menu a, .nm-menu a *"], ["header status word", ".nm-uplink, .nm-uplink *"],
  ["sentence line", ".nm-pace, .nm-pace *, .nm-story, .nm-sub"], ["figure", ".nm-big, .nm-facts b, .nm-mrow .v, .nm-kv b, .nm-stat-v"]
];

/* Outside English a meter's bar keeps at least this many px, however long its label (each look's sheet). */
var BAR_FLOOR = 96;

/* Everything in the header, the nav and the page that runs past its room. lists: {cuts, never} (DESIGNED_CUTS and NEVER_CUT,
 * which a caller that hands this function alone to the page passes; with none, the file's own). */
function measure(lists) {
  const LISTS = lists || { cuts: DESIGNED_CUTS, never: NEVER_CUT };
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
  const squash = (s) => String(s || '').replace(/\s+/g, ' ').trim();
  const roleOf = (el) => (LISTS.never.find(([, sel]) => el.matches(sel)) || [null])[0];
  const ellipsis = (cs) => cs.textOverflow === 'ellipsis' && /hidden|clip/.test(cs.overflowX);
  // how far an element's words run past its content box, in px: a range over its contents keeps the words' whole width where
  // the element clips them; more than CUT_TOLERANCE (a little over Chromium's 1/64 px layout unit) and "…" is drawn
  const CUT_TOLERANCE = 0.02;
  const wordsPast = (el, cs) => {
    const range = document.createRange();
    range.selectNodeContents(el);
    const box = el.getBoundingClientRect().width - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) -
      parseFloat(cs.borderLeftWidth) - parseFloat(cs.borderRightWidth);
    return range.getBoundingClientRect().width - box;
  };
  // the page's catalog strings (its language's, English's behind them), each as a pattern its filled text matches whole; a
  // string with fewer than three letters outside its placeholders ("{name}, {meta}") would match any text and is left out
  let patterns = null;
  const catalog = (text) => {
    if (!patterns) {
      const cat = window.MonitorI18n || {};
      const values = [];
      for (const src of [cat.strings || {}, cat.fallback || {}]) {
        for (const v of Object.values(src)) {
          if (typeof v === 'string') { values.push(v); }
          if (v && typeof v === 'object') { values.push(...Object.values(v).filter((x) => typeof x === 'string')); }
        }
      }
      patterns = values.filter((v) => (v.replace(/\{\w+\}/g, '').match(/\p{L}/gu) || []).length >= 3)
        .map((v) => new RegExp('^' + v.split(/\{\w+\}/).map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('.+') + '$', 'su'));
    }
    return !text.includes('\u2026') && patterns.some((re) => re.test(text));
  };
  // an element its look cuts with a visible ellipsis: which designed cut it is (if any), what kind of element, its whole
  // text, its title, whether an ancestor's aria-label holds that text, whether the text is a whole catalog string, and its
  // language (en: English's words on another language's page, a key the language has no words for yet)
  const cutOf = (el) => {
    const full = squash(el.textContent);
    let named = false;
    for (let n = el.parentElement; n && !named; n = n.parentElement) { named = squash(n.getAttribute('aria-label')).includes(full); }
    const d = LISTS.cuts.find((c) => el.matches(c.sel));
    const marked = el.closest('[lang]');
    return { designed: d ? d.id : null, role: roleOf(el), full, title: el.hasAttribute('title') ? squash(el.getAttribute('title')) : null,
      named, catalog: catalog(full), lang: marked ? marked.getAttribute('lang') : null };
  };
  const WIDE = '.nm-menu a, .nm-mrow > *, .nm-chip, .nm-tag, .nm-uplink, #data-age, .nm-refresh, .nm-sw button, .nm-kick';
  const FIXED = '.nm-mrow, .nm-menu a, .nm-bar, .nm-refresh, .nm-chip, .nm-sw button, .nm-btn, .nm-pill';
  const over = [];
  const styled = [];
  const roots = [document.querySelector('.nm-bar'), document.querySelector('.nm-nav'), document.getElementById('main')].filter(Boolean);
  for (const root of roots) {
    for (const el of [root, ...root.querySelectorAll('*')]) {
      if (el.closest('svg, script, style, .nm-sr, [aria-hidden="true"]') || !shown(el)) { continue; }
      const cs = getComputedStyle(el);
      const inline = cs.display === 'inline';
      const text = (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 90);
      // wider than its box; an element its look cuts with an ellipsis is cut as soon as its words are wider than its content
      // box by more than Chromium's layout unit (1/64 px), which the whole-px scroll widths do not show under about 1 px:
      // such a cut's by is how far its words run past, to 0.1 px (#98's second review)
      const nowrap = /nowrap|pre/.test(cs.whiteSpace);
      const wide = el.scrollWidth > el.clientWidth + 1;
      const fine = !inline && !wide && el.clientWidth > 0 && nowrap && ellipsis(cs) ? wordsPast(el, cs) : 0;
      if (!inline && el.clientWidth > 0 && (nowrap || el.matches(WIDE)) && (wide || fine > CUT_TOLERANCE)) {
        const item = { at: where(el), kind: 'wide', sw: el.scrollWidth, cw: el.clientWidth, text };
        if (!wide) { item.by = Math.ceil(fine * 10) / 10; }
        if (ellipsis(cs) && nowrap) { item.cut = cutOf(el); }      // cut with a visible ellipsis
        over.push(item);
      }
      // an ellipsis set on an element that is never cut, whether its words fit or not
      if (!inline && ellipsis(cs) && roleOf(el)) { styled.push({ at: where(el), role: roleOf(el), text }); }
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
  // a nav tab's words take at most two lines
  for (const a of document.querySelectorAll('.nm-menu a')) {
    if (!shown(a)) { continue; }
    const lines = Math.max(0, ...[a, ...a.querySelectorAll('*')].map((d) => (own(d) && shown(d) ? (textRect(d) || { lines: 0 }).lines : 0)));
    if (lines > 2) { over.push({ at: where(a), kind: 'nav-lines', lines, text: squash(a.innerText) }); }
  }
  // a meter's two bars start at the same x; how wide each bar is (the judges hold it to BAR_FLOOR outside English)
  const meters = [...document.querySelectorAll('#main .nm-meter')].filter(shown).map((m) => {
    const cells = [...m.querySelectorAll('.nm-cells')].map((c) => c.getBoundingClientRect());
    return { at: where(m), lefts: cells.map((r) => +r.left.toFixed(2)), widths: cells.map((r) => +r.width.toFixed(2)) };
  });
  meters.forEach((m) => {
    if (m.lefts.length >= 2 && Math.max(...m.lefts) - Math.min(...m.lefts) > 0.5) { over.push({ at: m.at, kind: 'meter', lefts: m.lefts }); }
  });
  return { vw: innerWidth, vh: innerHeight, dpr: devicePixelRatio, lang: document.documentElement.lang, over, meters: meters.length,
    bars: meters.map((m) => ({ at: m.at, widths: m.widths })), styled };
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

/* The header bar's one row at the window's width (#98's review): its scroll width against its width, Refresh's right edge
 * against the window's, whether the data age is shown, the link's words, the bar's elements cut (wider than their box) and
 * whether the brand's words run into the right-hand group. */
function header() {
  var bar = document.querySelector(".nm-bar");
  var refresh = document.getElementById("refresh");
  var age = document.getElementById("data-age");
  var brand = document.querySelector(".nm-brand");
  var right = document.querySelector(".nm-bar-r");
  if (!bar) { return null; }
  var shown = function (el) { return !!el && getComputedStyle(el).display !== "none" && el.getClientRects().length > 0; };
  var cut = Array.prototype.filter.call(bar.querySelectorAll("*"), function (el) {
    return shown(el) && getComputedStyle(el).display !== "inline" && el.clientWidth > 0 && el.scrollWidth > el.clientWidth + 1;
  }).map(function (el) { return (el.id ? "#" + el.id : "." + String(el.className).split(" ")[0]) + " " + el.scrollWidth + ">" + el.clientWidth; });
  // the brand's own words (its text nodes) against the right-hand group's box
  var words = 0;
  if (brand) {
    var range = document.createRange();
    range.selectNodeContents(brand);
    Array.prototype.forEach.call(range.getClientRects(), function (q) { if (q.width > 0) { words = Math.max(words, q.right); } });
  }
  var r = refresh ? refresh.getBoundingClientRect() : null;
  // the link's words: how many lines they take, and whether they stay inside the bar
  var text = document.getElementById("uplink-text");
  var tops = [], top = Infinity, bottom = -Infinity;
  if (text) {
    var lr = document.createRange();
    lr.selectNodeContents(text);
    Array.prototype.forEach.call(lr.getClientRects(), function (q) {
      if (q.width <= 0) { return; }
      if (!tops.some(function (t) { return Math.abs(t - q.top) < 2; })) { tops.push(q.top); }
      top = Math.min(top, q.top);
      bottom = Math.max(bottom, q.bottom);
    });
  }
  var b = bar.getBoundingClientRect();
  return { vw: innerWidth, sw: bar.scrollWidth, cw: bar.clientWidth, refresh: r ? +r.right.toFixed(1) : null,
    ageShown: shown(age), healthShown: shown(document.querySelector(".nm-health")),
    link: text ? text.textContent : "", lines: tops.length, inBar: !tops.length || (top >= b.top - 0.5 && bottom <= b.bottom + 0.5), cut: cut,
    overlap: right && words ? +(words - right.getBoundingClientRect().left).toFixed(1) : null };
}

/* Each chip in #main (.nm-chip, and .nm-pace > b where a look frames the verdict as a chip): where the ink of its words sits
 * against its border box, from the font's own metrics (a canvas's measureText at the chip's computed font), whether the look
 * draws a 1 px frame there (a border, or a ::before ring as Night City's), and how far the chip runs out of its card. */
function chips() {
  var canvas = document.createElement("canvas");
  var ctx = canvas.getContext("2d");
  var out = [];
  Array.prototype.forEach.call(document.querySelectorAll("#main .nm-chip, #main .nm-pace > b"), function (el) {
    var cs = getComputedStyle(el);
    if (cs.display === "inline" || cs.display === "none" || !el.getClientRects().length || el.closest("[aria-hidden='true']")) { return; }
    var ring = getComputedStyle(el, "::before");
    var framed = parseFloat(cs.borderTopWidth) >= 1 || (ring.content !== "none" && ring.position === "absolute");
    if (!framed && !el.classList.contains("nm-chip")) { return; }          // a verdict the look sets as plain words
    for (var n = el; n && n.nodeType === 1; n = n.parentElement) {
      if (getComputedStyle(n).display === "none" || getComputedStyle(n).visibility === "hidden") { return; }
    }
    var text = (el.innerText || "").replace(/\s+/g, " ").trim();
    if (!text) { return; }
    var box = el.getBoundingClientRect();
    ctx.font = cs.fontStyle + " " + cs.fontWeight + " " + cs.fontSize + " " + cs.fontFamily;
    var m = ctx.measureText(text);
    var inkTop = Infinity, inkBottom = -Infinity, left = Infinity, right = -Infinity, tops = [];
    Array.prototype.forEach.call(el.childNodes, function (node) {
      if (node.nodeType !== 3 || !node.nodeValue.trim()) { return; }
      var range = document.createRange();
      range.selectNodeContents(node);
      Array.prototype.forEach.call(range.getClientRects(), function (q) {
        if (q.width <= 0) { return; }
        if (!tops.some(function (t) { return Math.abs(t - q.top) < 2; })) { tops.push(q.top); }
        // a text fragment's box is the font's ascent over its descent, centred on its line: the baseline sits at the box's
        // top plus the font's ascent
        var pad = (q.height - (m.fontBoundingBoxAscent + m.fontBoundingBoxDescent)) / 2;
        var baseline = q.top + pad + m.fontBoundingBoxAscent;
        inkTop = Math.min(inkTop, baseline - m.actualBoundingBoxAscent);
        inkBottom = Math.max(inkBottom, baseline + m.actualBoundingBoxDescent);
        left = Math.min(left, q.left);
        right = Math.max(right, q.right);
      });
    });
    if (!isFinite(inkTop)) { return; }
    var card = el.closest(".nm-card-in");
    var c = card ? card.getBoundingClientRect() : null;
    var host = el.closest("[data-card]");
    out.push({ at: (host ? host.getAttribute("data-card") + " " : "") + el.tagName.toLowerCase() + (el.classList.length ? "." + el.classList[0] : ""),
      text: text.slice(0, 60),
      top: +(inkTop - box.top).toFixed(2), bottom: +(box.bottom - inkBottom).toFixed(2), h: +box.height.toFixed(2), framed: framed,
      chip: el.classList.contains("nm-chip"), lines: tops.length,
      out: c ? +Math.max(box.right - c.right, c.left - box.left, 0).toFixed(1) : 0,
      wordsOut: c ? +Math.max(right - c.right, c.left - left, 0).toFixed(1) : 0 });
  });
  return out;
}

/* The first meter on the page with each of its labels replaced by text (a label far longer than a translation should be),
 * read, and put back: each bar's left edge and width, the meter's right edge against its card's, and each label's scroll
 * width against its width. Outside English the bar keeps BAR_FLOOR and both bars start at the same x however long the
 * label (#98's owner question 1). */
function meterStress(text) {
  var meter = document.querySelector("#main .nm-meter");
  if (!meter) { return null; }
  var labels = Array.prototype.slice.call(meter.querySelectorAll(".nm-mrow .k"));
  var kept = labels.map(function (k) { return k.textContent; });
  labels.forEach(function (k) { k.textContent = text; });
  var card = meter.closest(".nm-card-in");
  var out = {
    bars: Array.prototype.map.call(meter.querySelectorAll(".nm-cells"), function (c) {
      var r = c.getBoundingClientRect();
      return { left: +r.left.toFixed(2), width: +r.width.toFixed(2) };
    }),
    right: +meter.getBoundingClientRect().right.toFixed(1), card: card ? +card.getBoundingClientRect().right.toFixed(1) : null,
    labels: labels.map(function (k) { return { sw: k.scrollWidth, cw: k.clientWidth }; })
  };
  labels.forEach(function (k, i) { k.textContent = kept[i]; });
  return out;
}

/* The burn card's chip with one word longer than its room beside the title but no wider than the card (#98's second review),
 * read, and put back: outside English the card's title row wraps, so the chip moves to a line of its own below its title
 * (its top past the title's middle: a look's line boxes may overlap by a px or two), the title keeps its lines and the chip
 * stays inside the card. The word is capitals of the chip's own font, one more at a time until the chip is wider than the
 * room beside the title; null without the card, skipped where the card has no room for it. */
function burnStress() {
  var row = document.querySelector("#main #burn .nm-card-t");
  var chip = row && row.querySelector(":scope > .nm-chip");
  var title = row && row.querySelector(":scope > .nm-ttl");
  var card = row && row.closest(".nm-card-in");
  if (!chip || !title || !card || !chip.getClientRects().length) { return null; }
  var inner = function (el) {
    var b = el.getBoundingClientRect();
    var s = getComputedStyle(el);
    return { left: b.left + parseFloat(s.paddingLeft) + parseFloat(s.borderLeftWidth), right: b.right - parseFloat(s.paddingRight) - parseFloat(s.borderRightWidth) };
  };
  var lines = function (el) {
    var range = document.createRange();
    range.selectNodeContents(el);
    var tops = [];
    Array.prototype.forEach.call(range.getClientRects(), function (q) { if (q.width > 0 && !tops.some(function (t) { return Math.abs(t - q.top) < 2; })) { tops.push(q.top); } });
    return tops.length;
  };
  var cs = getComputedStyle(chip);
  var ctx = document.createElement("canvas").getContext("2d");
  ctx.font = cs.fontStyle + " " + cs.fontWeight + " " + cs.fontSize + " " + cs.fontFamily;
  var each = ctx.measureText("ABCDEFGHIJKLMNOPQRSTUVWXYZ").width / 26 + (parseFloat(cs.letterSpacing) || 0);
  var frame = chip.offsetWidth - chip.clientWidth + parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight);
  var beside = inner(row).right - title.getBoundingClientRect().right - (parseFloat(getComputedStyle(row).columnGap) || 0);
  var room = beside - frame;
  var box = inner(card);
  var most = Math.floor((box.right - box.left - frame) / each) - 1;
  var kept = chip.innerHTML;
  var before = lines(title);
  // the word grows a capital at a time from the room's estimate until the chip is wider than the room beside the title
  var count = Math.max(1, Math.floor(room / each) + 1);
  for (; count <= most; count++) {
    chip.textContent = "ABCDEFGHIJKLMNOPQRSTUVWXYZ".repeat(8).slice(0, count);
    if (chip.getBoundingClientRect().width > beside + 0.5) { break; }
  }
  if (count > most) {
    chip.innerHTML = kept;
    return { skipped: true, room: +room.toFixed(1) };
  }
  var t = title.getBoundingClientRect();
  var c = chip.getBoundingClientRect();
  var out = { count: count, room: +room.toFixed(1), below: c.top >= (t.top + t.bottom) / 2, lines: lines(title), before: before,
    right: +c.right.toFixed(1), card: +card.getBoundingClientRect().right.toFixed(1) };
  chip.innerHTML = kept;
  return out;
}

/* The first nav tab with its label written long enough to take two lines (#98's second review: one word at a time is added
 * until it wraps), read, and put back: outside English its words stay inside the tab, which grows where its look lets it. */
function navStress() {
  var a = Array.prototype.filter.call(document.querySelectorAll(".nm-menu a"), function (x) { return x.getClientRects().length > 0; })[0];
  var span = a && a.querySelector("span");
  if (!span) { return null; }
  var words = function () {
    var range = document.createRange();
    range.selectNodeContents(span);
    var tops = [], top = Infinity, bottom = -Infinity;
    Array.prototype.forEach.call(range.getClientRects(), function (q) {
      if (q.width <= 0) { return; }
      if (!tops.some(function (t) { return Math.abs(t - q.top) < 2; })) { tops.push(q.top); }
      top = Math.min(top, q.top);
      bottom = Math.max(bottom, q.bottom);
    });
    return { lines: tops.length, top: top, bottom: bottom };
  };
  var kept = span.textContent;
  var text = kept;
  for (var i = 0; i < 40 && words().lines < 2; i++) {
    text += " Mxw";
    span.textContent = text;
  }
  var w = words();
  var tab = a.getBoundingClientRect();
  var out = { text: text, lines: w.lines, top: +(w.top - tab.top).toFixed(1), bottom: +(tab.bottom - w.bottom).toFixed(1), h: +tab.height.toFixed(1),
    min: parseFloat(getComputedStyle(a).minHeight) || 0 };
  span.textContent = kept;
  return out;
}

/* The first limit card's sentences on the page (its pace line, its verdict, its forecast) each written as one word far longer
 * than any line (#98's second review: a word longer than its line breaks), read, and put back: how far each one's words run
 * past the card at either side. */
function sentenceStress(word) {
  var pace = document.querySelector("#main .nm-grid > [data-limit] .nm-pace");
  var card = pace && pace.closest(".nm-card-in");
  if (!card) { return null; }
  var parts = [["pace line", pace.querySelector(":scope > span:first-child")], ["verdict", pace.querySelector(":scope > b")],
    ["forecast", pace.querySelector(":scope > .fc")]].filter(function (p) { return p[1]; });
  var kept = parts.map(function (p) { return p[1].innerHTML; });
  parts.forEach(function (p) { p[1].textContent = word; });
  var c = card.getBoundingClientRect();
  var out = parts.map(function (p) {
    var range = document.createRange();
    range.selectNodeContents(p[1]);
    var left = Infinity, right = -Infinity;
    Array.prototype.forEach.call(range.getClientRects(), function (q) { if (q.width > 0) { left = Math.min(left, q.left); right = Math.max(right, q.right); } });
    return { part: p[0], out: isFinite(left) ? +Math.max(right - c.right, c.left - left, 0).toFixed(1) : 0 };
  });
  parts.forEach(function (p, i) { p[1].innerHTML = kept[i]; });
  return out;
}

/* The room each constrained text has in the window, as en.json's notes give it to translators (#98's owner question 1):
 * px, and about how many capitals of the element's font fit (its letter spacing included). nav: a tab's words, from their
 * left edge to the tab's padding; metas: an Overview card's meta beside its title (the row's width less the title and the
 * gap); facts: a limit card's facts column beside its figure written as 100 % at its widest (as English, French, Spanish
 * or Turkish writes it), and at most 60 % of the row as outside English (a look whose facts are not a column of their own is
 * left out), with its chip's words' own room (chipRoom: the column less the chip's frame and padding); burn: the burn
 * card's chip beside its title; meter: a meter's label column. */
function rooms() {
  var canvas = document.createElement("canvas");
  var ctx = canvas.getContext("2d");
  var caps = function (el, room) {
    var cs = getComputedStyle(el);
    ctx.font = cs.fontStyle + " " + cs.fontWeight + " " + cs.fontSize + " " + cs.fontFamily;
    var spacing = parseFloat(cs.letterSpacing) || 0;
    var each = ctx.measureText("ABCDEFGHIJKLMNOPQRSTUVWXYZ").width / 26 + spacing;
    return Math.floor(room / each);
  };
  var px = function (n) { return +n.toFixed(1); };
  var inner = function (el) {          // the content box's left and right edges
    var b = el.getBoundingClientRect();
    var cs = getComputedStyle(el);
    return { left: b.left + parseFloat(cs.paddingLeft) + parseFloat(cs.borderLeftWidth),
      right: b.right - parseFloat(cs.paddingRight) - parseFloat(cs.borderRightWidth) };
  };
  var shown = function (el) { return !!el && getComputedStyle(el).display !== "none" && el.getClientRects().length > 0; };
  var out = { nav: [], metas: [], facts: [], burn: [], meter: [] };
  Array.prototype.forEach.call(document.querySelectorAll(".nm-menu a"), function (a) {
    var words = a.querySelector("span");
    if (!shown(a) || !words) { return; }
    var room = inner(a).right - words.getBoundingClientRect().left;
    out.nav.push({ page: a.getAttribute("data-page"), room: px(room), caps: caps(words, room) });
  });
  Array.prototype.forEach.call(document.querySelectorAll("#main .nm-grid .nm-card"), function (card) {
    var row = card.querySelector(".nm-card-t:not(.nm-chart-t)");
    var meta = row && row.querySelector(":scope > .nm-meta-t");
    var title = row && row.querySelector(":scope > .nm-tag, :scope > .nm-ttl");
    if (!meta || !title || !shown(meta)) { return; }
    var room = inner(row).right - title.getBoundingClientRect().right - (parseFloat(getComputedStyle(row).columnGap) || 0);
    out.metas.push({ card: card.getAttribute("data-card"), room: px(room), caps: caps(meta, room) });
  });
  Array.prototype.forEach.call(document.querySelectorAll("#main .nm-grid > [data-limit] .nm-row2"), function (row) {
    var facts = row.querySelector(":scope > .nm-facts");
    var figure = row.querySelector(":scope > .nm-big");
    if (!facts || !figure || getComputedStyle(facts).display !== "flex") { return; }
    // the figure at its widest: 100 % as any language the page is built for writes it (English 100%, French and Spanish with
    // a no-break space, Turkish %100; #98's second review), the widest of them
    var kept = figure.innerHTML;
    var wide = 0;
    ["100%", "100\u00a0%", "100\u202f%", "%100"].forEach(function (form) {
      figure.textContent = form;
      wide = Math.max(wide, figure.getBoundingClientRect().width);
    });
    figure.innerHTML = kept;
    var box = inner(row);
    var room = Math.min(box.right - box.left - wide - (parseFloat(getComputedStyle(row).columnGap) || 0), 0.6 * (box.right - box.left));
    var label = facts.querySelector(":scope > span:not(.nm-chip)") || facts;
    var chip = facts.querySelector(":scope > .nm-chip");
    var host = row.closest("[data-card]");
    // a chip's word has the column less the chip's own frame and padding
    var chipRoom = chip ? room - (chip.offsetWidth - chip.clientWidth) - parseFloat(getComputedStyle(chip).paddingLeft) -
      parseFloat(getComputedStyle(chip).paddingRight) : 0;
    out.facts.push({ card: host ? host.getAttribute("data-card") : "?", room: px(room), row: px(box.right - box.left), figure: px(wide),
      caps: caps(label, room), chipRoom: chip ? px(chipRoom) : null, chipCaps: chip ? caps(chip, chipRoom) : null });
  });
  var burn = document.querySelector("#main #burn .nm-card-t");
  var chip = burn && burn.querySelector(":scope > .nm-chip");
  var title = burn && burn.querySelector(":scope > .nm-ttl");
  if (chip && title) {
    var cs = getComputedStyle(chip);
    var room = inner(burn).right - title.getBoundingClientRect().right - (parseFloat(getComputedStyle(burn).columnGap) || 0) -
      parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) - parseFloat(cs.borderLeftWidth) - parseFloat(cs.borderRightWidth);
    out.burn.push({ card: "burn", room: px(room), caps: caps(chip, room) });
  }
  Array.prototype.forEach.call(document.querySelectorAll("#main .nm-meter .nm-mrow .k"), function (k) {
    if (shown(k)) { out.meter.push({ room: px(k.clientWidth), caps: caps(k, k.clientWidth) }); }
  });
  return out;
}

if (typeof module !== "undefined") {
  module.exports = { settled: settled, DESIGNED_CUTS: DESIGNED_CUTS, NEVER_CUT: NEVER_CUT, BAR_FLOOR: BAR_FLOOR, measure: measure,
    regions: regions, readCaps: readCaps, CSS_CAPS: CSS_CAPS, writeLines: writeLines, readUpdate: readUpdate, header: header,
    chips: chips, meterStress: meterStress, burnStress: burnStress, navStress: navStress, sentenceStress: sentenceStress, rooms: rooms };
}
