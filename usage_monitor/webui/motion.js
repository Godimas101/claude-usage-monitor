/* motion.js: the shared engine of the dashboard's motion layer. window.CUM_FX is used by the three theme scripts
 * (themes/<id>.js), which register one director each (FX.director) and expose it to app.js as
 * window.CUM_THEME_FX[id] = { start, enter(main, page), cancel(main), arrive(card, info) }. What moves is CSS (motion.css and
 * themes/<id>.motion.css); this file decides when, and draws the characters (typing, decoding, rolling).
 *
 * The contract with app.js (docs/UI_ARCHITECTURE.md "Motion"):
 *   - enter(main, page)  plays the entrance of the page that was just drawn into #main (a page opened, the dashboard
 *     started, the theme changed). cancel(main) finishes whatever plays, at the final text, before the markup is replaced.
 *   - Pending placeholders. A card, figure, bar or chart whose data is not in yet carries data-pending (or .is-pending).
 *     This file reads that and nothing else: it marks each placeholder slot data-fx-pend="num|bar|rail|chart" for the
 *     themes' CSS, and when the mark goes (the card was filled in place, or the markup replaced) it plays the theme's
 *     "data arrived" effect on just those slots. FX.arrive(slots) does the same by hand if app.js wants to say so itself.
 *   - #main also gets .fx-quiet after its content is replaced, until the pointer moves (a pointer resting on a card must
 *     not replay that card's hover effect).
 *   - Reduced motion (data-reduce-motion="1" on <html>, or prefers-reduced-motion): nothing is scheduled, the final
 *     text is the plain text, a placeholder is a static one.
 *
 * Rules the code keeps: frames only write (nodeValue, setAttribute, a class), every read is batched before the writes,
 * one requestAnimationFrame loop for every counter, and the real text of a figure is never changed while it is
 * drawn: a decoded or rolling figure is drawn by its ::after from data-fx while the real text sits transparent (so
 * night_city.js and gates.js, which watch the page for text, see nothing happen, and a screen reader reads the final
 * figure). The state of a ghost figure is an ATTRIBUTE, never a class: night_city.js keys its figures by className.
 * Nothing here stores, sends or prints anything; sessionStorage holds only the "boot played" flags, always guarded.
 */
(function () {
  "use strict";

  var FX = window.CUM_FX = window.CUM_FX || {};
  var ROOT = document.documentElement;
  var STAGGER_MAX = 8;
  var ROWS_MAX = 12;
  var PENDING = "[data-pending], .is-pending";
  /* What a placeholder can be, by kind. num: a figure (it types, decodes or rolls in); text: a line of words or a table cell (it
   * just appears); bar: a run of cells (it fills); rail: the pace hairline and the time-left bar (they grow); chart: a chart. */
  var SLOT_SELECTORS = {
    num: ".nm-big, .nm-stat-v, .nm-mrow .v, .nm-kv-grid .nm-kv b, .nm-cmp-n",
    text: ".nm-stat-s, .nm-kv b, .nm-facts > span, .nm-pace > span, .nm-pace > b, .nm-pace > .fc, .nm-story, .nm-table td.r, " +
      ".nm-table td.nm-trunc, .nm-odo-facts b, .nm-toolbar-n",
    bar: ".nm-cells, .nm-ratio",
    rail: ".nm-rail, .nm-remain",
    chart: ".nm-chart-host, svg.cum-spark, svg.cum-stack"
  };
  var KINDS = ["num", "text", "bar", "rail", "chart"];

  function reduced() {
    if (ROOT.getAttribute("data-reduce-motion") === "1") {
      return true;
    }
    return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }

  function hidden() {
    return document.visibilityState === "hidden";
  }

  function all(selector, root) {
    return Array.prototype.slice.call(root.querySelectorAll(selector));
  }

  function clamp(v, lo, hi) {
    return Math.max(lo, Math.min(hi, v));
  }

  function rnd(n) {
    return Math.floor(Math.random() * n);
  }

  // ── easing ──────────────────────────────────────────────────────────────────────

  function bezier(x1, y1, x2, y2) {
    var cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx;
    var cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by;
    function X(t) { return ((ax * t + bx) * t + cx) * t; }
    function Y(t) { return ((ay * t + by) * t + cy) * t; }
    function dX(t) { return (3 * ax * t + 2 * bx) * t + cx; }
    function solve(x) {
      var t = x, i, e, d;
      for (i = 0; i < 8; i++) {
        e = X(t) - x;
        if (Math.abs(e) < 1e-6) { return t; }
        d = dX(t);
        if (Math.abs(d) < 1e-6) { break; }
        t -= e / d;
      }
      var lo = 0, hi = 1;
      t = x;
      for (i = 0; i < 40; i++) {
        e = X(t) - x;
        if (Math.abs(e) < 1e-6) { return t; }
        if (e < 0) { lo = t; } else { hi = t; }
        t = (lo + hi) / 2;
      }
      return t;
    }
    return function (x) { return x <= 0 ? 0 : x >= 1 ? 1 : Y(solve(x)); };
  }

  function inverse(f) {                      // the time at which an increasing easing reaches progress y
    return function (y) {
      var lo = 0, hi = 1, i;
      for (i = 0; i < 28; i++) {
        var m = (lo + hi) / 2;
        if (f(m) < y) { lo = m; } else { hi = m; }
      }
      return (lo + hi) / 2;
    };
  }

  var ease = bezier(0.1, 0.9, 0.2, 1);       // Fluent decelerate
  var easeInv = inverse(ease);

  // ── jobs: one requestAnimationFrame loop for every character-level effect ───────

  var jobs = (function () {
    var list = [];
    var raf = 0;
    function tick(t) {
      raf = 0;
      for (var i = list.length - 1; i >= 0; i--) {
        if (list[i].update(t) === true) {
          list.splice(i, 1);
        }
      }
      if (list.length) {
        raf = window.requestAnimationFrame(tick);
      }
    }
    return {
      add: function (job) {
        if (job) {
          list.push(job);
          if (!raf) {
            raf = window.requestAnimationFrame(tick);
          }
        }
      },
      /* finish every job of one owner at its final text */
      cancel: function (owner) {
        for (var i = list.length - 1; i >= 0; i--) {
          if (list[i].owner === owner) {
            list[i].cancel();
            list.splice(i, 1);
          }
        }
      },
      count: function () { return list.length; }
    };
  })();

  // ── text helpers ────────────────────────────────────────────────────────────────

  function directText(el) {
    var s = "";
    for (var n = el.firstChild; n; n = n.nextSibling) {
      if (n.nodeType === 3) {
        s += n.nodeValue;
      }
    }
    return s;
  }

  function firstText(el) {                   // the first text node with something in it (the figure, not a <small>)
    for (var n = el.firstChild; n; n = n.nextSibling) {
      if (n.nodeType === 3 && /\S/.test(n.nodeValue)) {
        return n;
      }
    }
    return null;
  }

  /* "$1,107.50 x" -> { pre: "$", val: 1107.5, dec: 2, comma: true, post: " x" } */
  function parseNum(s) {
    var m = /^([^\d]*)(\d[\d,]*(?:\.\d+)?)([\s\S]*)$/.exec(s);
    if (!m) {
      return null;
    }
    var raw = m[2];
    var dot = raw.indexOf(".");
    return { pre: m[1], val: parseFloat(raw.replace(/,/g, "")), dec: dot >= 0 ? raw.length - dot - 1 : 0, comma: raw.indexOf(",") >= 0, post: m[3] };
  }

  function fmtNum(p, v) {
    var s = p.comma ? v.toLocaleString("en-US", { minimumFractionDigits: p.dec, maximumFractionDigits: p.dec }) : v.toFixed(p.dec);
    return p.pre + s + p.post;
  }

  // ── ghost text: a figure whose characters the script draws (decode, roll) ───────

  /* Read first (the resting colour and text-shadow, before main.fx-run is added), write after. */
  function ghostPrep(el) {
    var cs = window.getComputedStyle(el);
    return { c: cs.color, s: cs.textShadow };
  }

  function ghostOn(el, style) {
    if (style) {
      el.style.setProperty("--fx-gc", style.c);
      el.style.setProperty("--fx-gs", style.s);
    }
    el.setAttribute("data-fx", el.getAttribute("data-fx") || "");
  }

  function ghostOff(el) {
    el.removeAttribute("data-fx-d");
    el.removeAttribute("data-fx");
    el.style.removeProperty("--fx-gc");
    el.style.removeProperty("--fx-gs");
  }

  // ── roller (Gates) ──────────────────────────────────────────────────────────────

  /* Counts a figure up from `fromText` (or 0) to its own text with the decelerate curve. A figure that is zero, not a
   * number, or a number with more digits after it (a date, a range) is left alone. The real text never changes. */
  function roller(owner, el, startAt, dur, style, fromText) {
    var to = parseNum(directText(el));
    if (!to || !(to.val > 0) || /\d/.test(to.post)) {
      return null;
    }
    var from = fromText !== undefined ? parseNum(fromText) : null;
    var v0 = from ? from.val : 0;
    ghostOn(el, style);
    el.setAttribute("data-fx", fmtNum(to, v0));
    var shown = "";
    return {
      owner: owner,
      update: function (t) {
        if (t < startAt) {
          return false;
        }
        var p = clamp((t - startAt) / dur, 0, 1);
        if (p >= 1) {
          ghostOff(el);
          return true;
        }
        var s = fmtNum(to, v0 + (to.val - v0) * ease(p));
        if (s !== shown) {
          shown = s;
          el.setAttribute("data-fx", s);
        }
        return false;
      },
      cancel: function () { ghostOff(el); }
    };
  }

  // ── typer (Nostromo) ────────────────────────────────────────────────────────────

  /* Types a text node: <span class="fx-t">typed</span><span class="fx-u">rest</span> (the rest is visibility:hidden, so
   * the line keeps its width), a block cursor on the typed part while it types and `hold` ms after, then the original
   * text node is put back. per = min(perChar, cap / length). With `early` the cursor shows from the start (an arriving
   * figure keeps the cursor its placeholder blinked) instead of from the first character. */
  function typer(owner, textNode, startAt, perChar, cap, hold, early) {
    var text = textNode.nodeValue;
    var n = text.length;
    var parent = textNode.parentNode;
    if (!parent || n === 0) {
      return null;
    }
    var per = Math.min(perChar, cap / n);
    var wait = hold || 0;
    var t = document.createElement("span");
    var u = document.createElement("span");
    t.className = early ? "fx-t is-typing" : "fx-t";
    u.className = "fx-u";
    var tn = document.createTextNode("");
    var un = document.createTextNode(text);
    t.appendChild(tn);
    u.appendChild(un);
    parent.insertBefore(t, textNode);
    parent.replaceChild(u, textNode);
    var k = -1;
    var started = false;
    function restore() {
      if (u.parentNode) { u.parentNode.replaceChild(textNode, u); }
      if (t.parentNode) { t.parentNode.removeChild(t); }
    }
    return {
      owner: owner,
      update: function (now) {
        if (now < startAt) {
          return false;
        }
        if (!started) {
          started = true;
          t.className = "fx-t is-typing";
        }
        var kk = Math.min(n, Math.floor((now - startAt) / per) + 1);
        if (kk !== k) {
          k = kk;
          tn.nodeValue = text.slice(0, k);
          un.nodeValue = text.slice(k);
        }
        if (now >= startAt + per * n + wait) {
          restore();
          return true;
        }
        if (k >= n) {
          t.className = "fx-t";
        }
        return false;
      },
      cancel: restore
    };
  }

  // ── decoder (Night City) ────────────────────────────────────────────────────────

  var HEX = "0123456789ABCDEF";
  var ALPHA = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";

  function scramble(ch) {
    if (/[0-9]/.test(ch)) { return HEX.charAt(rnd(16)); }
    if (/[A-Za-z]/.test(ch)) {
      var u = ALPHA.charAt(rnd(26));
      return ch === ch.toLowerCase() ? u.toLowerCase() : u;
    }
    return ch;                               // spaces, punctuation and symbols keep their place
  }

  /* Scrambles a figure, locking characters left to right: character i locks at start + dur * (0.22 + 0.78 * (i + 1) / n).
   * With `preroll` it scrambles from the moment it is made (an arriving figure carries on from its placeholder's noise)
   * instead of from `startAt`. */
  function decoder(owner, el, startAt, dur, style, frameMs, preroll) {
    var text = directText(el);
    var n = text.length;
    if (!n) {
      return null;
    }
    var lock = [];
    var last = -1e9;
    for (var i = 0; i < n; i++) {
      lock.push(startAt + dur * (0.22 + 0.78 * (i + 1) / n));
    }
    ghostOn(el, style);
    if (preroll) {
      var first = "";
      for (var c = 0; c < n; c++) { first += scramble(text.charAt(c)); }
      el.setAttribute("data-fx", first);
      el.setAttribute("data-fx-d", "");
    }
    return {
      owner: owner,
      update: function (t) {
        if (t < startAt && !preroll) {
          return false;
        }
        if (t >= startAt + dur) {
          ghostOff(el);
          return true;
        }
        if (t - last < frameMs) {
          return false;
        }
        last = t;
        var out = "";
        var open = false;
        for (var j = 0; j < n; j++) {
          if (t >= lock[j]) { out += text.charAt(j); } else { out += scramble(text.charAt(j)); open = true; }
        }
        if (open) { el.setAttribute("data-fx-d", ""); } else { el.removeAttribute("data-fx-d"); }
        el.setAttribute("data-fx", out);
        return false;
      },
      cancel: function () { ghostOff(el); }
    };
  }

  // ── prepare: the custom properties the entrance CSS reads (writes only) ─────────

  /* The lit cells of a bar get their order (--fx-k); with barMs (Gates) also the start and length of their wipe so the
   * cells open when the decelerate curve passes their left edge: --gk-d = barMs * easeInv(k / m), --gk-l = the gap to
   * the next cell's start (at least 16 ms), m being the number of lit cells counting the part cell as its fraction. */
  function indexBar(bar, barMs) {
    var cells = Array.prototype.filter.call(bar.children, function (c) { return c.tagName === "I"; });
    var lit = cells.filter(function (c) { return c.classList.contains("on") || c.classList.contains("part"); });
    if (!lit.length) {
      return;
    }
    var on = cells.filter(function (c) { return c.classList.contains("on"); }).length;
    var part = cells.filter(function (c) { return c.classList.contains("part"); })[0];
    var frac = part ? (parseFloat(part.style.getPropertyValue("--f")) || 0) / 100 : 0;
    var m = on + frac;
    lit.forEach(function (c, k) {
      c.style.setProperty("--fx-k", k);
      if (barMs && m > 0) {
        var a = k / m;
        var b = c === part ? 1 : Math.min(1, (k + 1) / m);
        var d0 = barMs * easeInv(a);
        var d1 = barMs * easeInv(b);
        c.style.setProperty("--gk-d", Math.round(d0));
        c.style.setProperty("--gk-l", Math.max(16, Math.round(d1 - d0)));
      }
    });
  }

  /* Column, pip, dot and row order inside the charts below `root`. */
  function indexCharts(root) {
    all(".cum-columns", root).forEach(function (svg) {
      all(".cum-col", svg).forEach(function (g, c) {
        g.style.setProperty("--fx-c", c);
        all(".cum-pip.cum-mark, .cum-pip.cum-hot", g).forEach(function (r, p) { r.style.setProperty("--fx-p", p); });
      });
    });
    all(".cum-linechart, .cum-spark", root).forEach(function (svg) {
      var dots = all(".cum-dot", svg);
      dots.forEach(function (d, i) { d.style.setProperty("--fx-d", i); });
      svg.style.setProperty("--fx-dn", Math.max(1, dots.length));
    });
    all(".cum-hbars", root).forEach(function (svg) {
      all(".cum-row", svg).forEach(function (g, r) { g.style.setProperty("--fx-r", r); });
    });
    all(".cum-calendar, .cum-matrix", root).forEach(function (svg) {          // a heat map comes in as one unit: its columns set the pace
      svg.style.setProperty("--fx-cn", Math.max(1, parseInt(svg.getAttribute("data-cols"), 10) || 1));
    });
  }

  /* Writes custom properties only. The head and every card get their order (--fx-i, clamped so a long page still ends
   * inside its budget), each bar its cells, each chart its marks, each table its first rows. o.barMs: Gates' bar timing. */
  function prepare(main, o) {
    o = o || {};
    var head = main.querySelector(".nm-head");
    if (head) {
      head.style.setProperty("--fx-i", 0);
    }
    var bar = main.querySelector(".nm-toolbar");
    if (bar) {
      bar.style.setProperty("--fx-i", 0);
    }
    all(".nm-card", main).forEach(function (c, i) { c.style.setProperty("--fx-i", Math.min(i, STAGGER_MAX)); });
    all(".nm-cells, .nm-ratio", main).forEach(function (b) { indexBar(b, o.barMs); });
    indexCharts(main);
    all(".nm-table tbody", main).forEach(function (body) {
      Array.prototype.slice.call(body.rows, 0, ROWS_MAX).forEach(function (row, r) { row.style.setProperty("--fx-r", r); });
    });
  }

  /* A slot that landed on its own (a bar, a rail, a chart): its marks get their order, so the theme's CSS can play. */
  function prepareSlot(slot, o) {
    if (slot.kind === "bar") {
      indexBar(slot.el, (o || {}).barMs);
    } else if (slot.kind === "chart") {
      indexCharts(slot.el.tagName === "svg" ? slot.el.parentNode : slot.el);
    }
  }

  // ── pending placeholders and the "data arrived" detection ───────────────────────

  var directors = FX.themes = FX.themes || {};
  var watch = { main: null, observer: null, queued: false, page: "", prev: {}, known: {}, fresh: true };

  function activeDirector() {
    return directors[ROOT.getAttribute("data-theme")] || null;
  }

  /* Where a figure lives: its stat tile, else its card (by what names the card), so two renders of the same page
   * pair the same slots up even when a card appears or goes. */
  function ownerOf(el) {
    var stat = el.closest(".nm-stat");
    if (stat) {
      return "stat:" + (stat.getAttribute("data-fid") || "");
    }
    var card = el.closest(".nm-card, .nm-compare");
    if (card) {
      return "card:" + (card.getAttribute("data-card") || card.getAttribute("data-limit") || card.getAttribute("id") ||
        card.getAttribute("aria-label") || card.className);
    }
    return "page";
  }

  function isPending(el) {
    return !!(el.closest && el.closest(PENDING));
  }

  /* Every slot below root (root itself when it is one), by kind and in document order: { el, kind, key }. */
  function collectSlots(root) {
    var out = [];
    var counts = {};
    var taken = new Set();                   // an element is one kind of slot, the first that names it
    KINDS.forEach(function (kind) {
      var found = all(SLOT_SELECTORS[kind], root);
      if (root.matches && root.matches(SLOT_SELECTORS[kind])) {
        found.unshift(root);
      }
      found.forEach(function (el) {
        if (taken.has(el)) {
          return;
        }
        taken.add(el);
        var base = ownerOf(el) + "|" + kind;
        counts[base] = (counts[base] || 0) + 1;
        out.push({ el: el, kind: kind, key: base + "#" + counts[base], group: base });
      });
    });
    return out;
  }

  function scan() {
    watch.queued = false;
    var main = watch.main;
    if (!main || !main.isConnected) {
      return;
    }
    var page = window.location.hash || "#overview";
    var fresh = watch.fresh || page !== watch.page;
    watch.page = page;
    watch.fresh = false;
    var dir = activeDirector();
    var now = {};
    var known = {};
    var arrived = [];
    var landed = {};                         // owner|kind -> true: a slot of that kind in that card just arrived
    var extra = [];                          // slots the card did not have before (a placeholder with fewer rows than the data)
    collectSlots(main).forEach(function (s) {
      known[s.key] = true;
      if (isPending(s.el)) {
        now[s.key] = true;
        if (s.el.getAttribute("data-fx-pend") !== s.kind) {
          s.el.setAttribute("data-fx-pend", s.kind);
          if (dir && dir.pend) {
            dir.pend(s.el, s.kind);
          }
        }
      } else {
        if (s.el.hasAttribute("data-fx-pend")) {
          s.el.removeAttribute("data-fx-pend");
        }
        if (!fresh && watch.prev[s.key]) {
          arrived.push(s);
          landed[s.group] = true;
        } else if (!fresh && !watch.known[s.key]) {
          extra.push(s);
        }
      }
    });
    extra.forEach(function (s) {
      if (landed[s.group]) {
        arrived.push(s);
      }
    });
    watch.prev = now;
    watch.known = known;
    if (arrived.length) {
      FX.arrive(arrived);
    }
  }

  function later() {
    if (!watch.queued && watch.main) {
      watch.queued = true;
      /* a microtask, not a frame: the placeholders are marked and an arrival is started before the browser paints the
       * markup that was just written, so a figure never shows its final text for a frame before its effect */
      window.Promise.resolve().then(scan);
    }
  }

  var landedAt = new WeakMap();              // slot element -> when it last arrived: one effect, however many ways say so

  /* Hands slots that have just landed (their data-pending mark is gone) to the active theme's arrival effect. */
  FX.arrive = function (slots) {
    var dir = activeDirector();
    var stamp = window.performance.now();
    var fresh = slots.filter(function (s) {
      var at = landedAt.get(s.el);
      landedAt.set(s.el, stamp);
      return at === undefined || stamp - at > 600;
    });
    fresh.forEach(function (s) { s.el.removeAttribute("data-fx-pend"); });
    if (!fresh.length) {
      return;
    }
    if (!dir || !dir.arrive || reduced() || hidden()) {
      fresh.forEach(function (s) { if (dir && dir.unpend) { dir.unpend(s.el, false); } });
      return;
    }
    dir.arrive(fresh);
  };

  /* The card (or any element) whose data has just been written into the page: every slot in it that is not a placeholder
   * any more plays the theme's arrival effect. app.js may call this itself (window.CUM_THEME_FX[id].arrive); the watcher
   * above finds the same arrivals from the data-pending marks alone, and an arrival is only played once. */
  FX.arriveIn = function (root) {
    var roots = root && root.querySelectorAll ? [root] : (root && root.length ? Array.prototype.slice.call(root) : []);
    var list = [];
    roots.forEach(function (el) {
      collectSlots(el).forEach(function (s) {
        if (!isPending(s.el)) {
          list.push(s);
        }
      });
    });
    if (list.length) {
      FX.arrive(list);
    }
  };

  /* Starts watching #main (idempotent). Called when the page is ready, by the theme scripts' start(). */
  FX.watch = function (main) {
    if (!main || watch.main === main || typeof window.MutationObserver !== "function") {
      return;
    }
    watch.main = main;
    watch.fresh = true;
    watch.observer = new window.MutationObserver(function (records) {
      for (var i = 0; i < records.length; i++) {
        if (records[i].type === "childList" && records[i].target === main) {
          main.classList.add("fx-quiet");        // the content was replaced: a resting pointer is not a hover
          break;
        }
      }
      later();
    });
    watch.observer.observe(main, { childList: true, subtree: true, attributes: true, attributeFilter: ["data-pending"] });
    var wake = function () { main.classList.remove("fx-quiet"); };
    main.addEventListener("pointermove", wake, { passive: true });
    main.addEventListener("wheel", wake, { passive: true });
    new window.MutationObserver(function () {      // another theme took over: its placeholders are not this one's
      all("[data-fx-pend]", main).forEach(function (el) { el.removeAttribute("data-fx-pend"); });
      watch.prev = {};
      watch.known = {};
      watch.fresh = true;
      later();
    }).observe(ROOT, { attributes: true, attributeFilter: ["data-theme"] });
    later();
  };

  // ── director: the page entrance, the arrival effect and the pending hooks of one theme ──

  /* cfg: id, c0 / step (ms: card i starts at c0 + i * step), total (ms the entrance lasts), head / card (text groups:
   * { sel, at (ms after the card starts), ... }), make(el, at, group, style, early) -> job, ghost (the figures need
   * their resting colour read first), defer (schedule the text in a microtask: gates.js re-cases the page first),
   * barMs (Gates' bar timing), landMs (how long a landing slot keeps .fx-land), fallback (the group of a figure no
   * group names), pend / unpend (Night City's placeholder noise). Returns { enter, cancel, arrive, pend, unpend }. */
  FX.director = function (cfg) {
    var running = null;                      // { main, timer } while an entrance plays
    var landings = [];                       // { els, timer } for every batch of arrivals still playing
    var parked = null;                       // an entrance that waited for a boot overlay to go
    var LAND_BACK = 80;                      // an arriving figure starts this much sooner than the same figure of an entrance

    function endRun() {
      if (running) {
        window.clearTimeout(running.timer);
        running.main.classList.remove("fx-run");
        running = null;
      }
    }

    function endLanding(batch) {
      var rest = [];
      landings.forEach(function (b) {
        if (batch && b !== batch) {
          rest.push(b);
          return;
        }
        window.clearTimeout(b.timer);
        b.els.forEach(function (el) { el.classList.remove("fx-land"); });
      });
      landings = rest;
    }

    function groupOf(el, groups) {
      for (var i = 0; i < groups.length; i++) {
        if (el.matches(groups[i].sel)) {
          return groups[i];
        }
      }
      return null;
    }

    /* [{ el, at, g }] of the figures an entrance writes: the head's, then each card's, skipping placeholders. */
    function entranceTargets(main, t0) {
      var out = [];
      var seen = new Set();
      function add(el, at, g) {
        if (!seen.has(el) && !isPending(el)) {
          seen.add(el);
          out.push({ el: el, at: at, g: g });
        }
      }
      (cfg.head || []).forEach(function (g) {
        all(g.sel, main).forEach(function (el) {
          if (!el.closest(".nm-card")) {
            add(el, t0 + g.at, g);
          }
        });
      });
      all(".nm-card", main).forEach(function (card, i) {
        var base = t0 + cfg.c0 + Math.min(i, STAGGER_MAX) * cfg.step;
        (cfg.card || []).forEach(function (g) {
          all(g.sel, card).forEach(function (el) { add(el, base + g.at, g); });
        });
      });
      return out;
    }

    function schedule(owner, targets, early) {
      var styles = [];
      if (cfg.ghost) {
        /* read every resting style first, then write. A figure that already wears a ghost (its placeholder's noise) is
         * read without it: the ghost makes its own text transparent */
        var held = targets.map(function (t) {
          var v = t.el.getAttribute("data-fx");
          if (v !== null) {
            t.el.removeAttribute("data-fx");
          }
          return v;
        });
        styles = targets.map(function (t) { return ghostPrep(t.el); });
        targets.forEach(function (t, i) {
          if (held[i] !== null) {
            t.el.setAttribute("data-fx", held[i]);
          }
        });
      }
      targets.forEach(function (t, i) { jobs.add(cfg.make(owner, t.el, t.at, t.g, styles[i], early)); });
    }

    /* Finishes the entrance now: every figure at its final text, main.fx-run off. Safe to call at any time. */
    function cancel(main) {
      endRun();
      endLanding();
      if (main) {
        jobs.cancel(main);
      }
      var on = ROOT.getAttribute("data-theme") === cfg.id;
      if (!on || reduced()) {
        parked = null;
      }
    }

    /* Plays the entrance on the page that was just drawn into main. The CSS entrance hangs on main.fx-run, so removing the
     * class (or never adding it) leaves exactly the markup app.js wrote. */
    function enter(main, page) {
      cancel(main);
      if (!main || reduced()) {
        return;
      }
      if (FX.boot.active) {
        parked = { main: main, page: page };   // the boot overlay is up: the page enters when it goes
        return;
      }
      parked = null;
      prepare(main, { barMs: cfg.barMs });                    // custom properties only
      void main.offsetWidth;                                   // the entrance rules start from their first frame
      var t0 = window.performance.now();
      var words = page !== "settings" && page !== "about";     // forms get the card entrance only: nothing types on a control
      var go = function () {
        if (!running || running.main !== main) {
          return;                                              // finished or replaced in the meantime
        }
        if (words) {
          schedule(main, entranceTargets(main, t0), false);
        }
      };
      running = {
        main: main,
        timer: window.setTimeout(function () { if (running && running.main === main) { endRun(); } }, cfg.total + 40)
      };
      if (cfg.defer) {
        /* gates.js re-cases the page's text and splits "13%" into "13" and a small "%" in a MutationObserver that runs
         * right after the render task: the figures are read after it, still before the first frame */
        main.classList.add("fx-run");
        window.queueMicrotask(go);
      } else {
        /* Night City animates text-shadow in its entrance, so a ghost reads its resting style before fx-run is added */
        go();
        main.classList.add("fx-run");
      }
    }

    /* Slots whose data just landed: numbers type, decode or roll in, bars, rails and charts play their fill. */
    function arrive(list) {
      var main = watch.main;
      var t0 = window.performance.now();
      var els = [];
      var figures = [];
      list.forEach(function (s) {
        if (s.kind === "num") {
          figures.push(s);
          return;
        }
        if (s.kind === "text") {
          return;                                              // a line of words just appears
        }
        prepareSlot(s, { barMs: cfg.barMs });
        s.el.classList.add("fx-land");
        els.push(s.el);
      });
      if (els.length) {
        var batch = { els: els, timer: 0 };
        batch.timer = window.setTimeout(function () { endLanding(batch); }, (cfg.landMs || 800) + 40);
        landings.push(batch);
      }
      var go = function () {
        var targets = [];
        figures.forEach(function (s) {
          if (!s.el.isConnected) {
            return;
          }
          var g = groupOf(s.el, cfg.card || []) || cfg.fallback;
          var back = Math.min(LAND_BACK, g.at || 0);
          targets.push({ el: s.el, at: t0 + Math.max(0, (g.at || 0) - back), g: g });
        });
        schedule(main, targets, true);
        figures.forEach(function (s) { if (cfg.unpend) { cfg.unpend(s.el, true); } });
      };
      if (cfg.defer) {
        window.queueMicrotask(go);
      } else {
        go();
      }
    }

    function release() {                                       // the boot overlay went: the parked entrance plays now
      var p = parked;
      parked = null;
      if (p && p.main.isConnected && ROOT.getAttribute("data-theme") === cfg.id) {
        enter(p.main, p.page);
      }
    }

    var dir = {
      enter: enter,
      cancel: cancel,
      arrive: arrive,
      release: release,
      pend: cfg.pend || null,
      unpend: cfg.unpend || null
    };
    directors[cfg.id] = dir;
    return dir;
  };

  // ── boots: a curtain until the theme's script takes over, and the entrance that waits for the overlay ──

  var BOOT_KEYS = { nostromo: "nm-boot-session", night_city: "nc-boot-session", gates: "g-boot-session" };
  var failsafe = 0;

  var boot = FX.boot = {
    active: false,                           // an overlay is up: the page entrance is parked
    seen: function (id) {
      try {
        return window.sessionStorage.getItem(BOOT_KEYS[id]) === "1";
      } catch (e) {
        return false;                        // blocked storage: the boot plays again, as it always did
      }
    },
    mark: function (id) {
      try {
        window.sessionStorage.setItem(BOOT_KEYS[id], "1");
      } catch (e) {
        /* a private window: the boot simply plays again next time */
      }
    },
    due: function (id) {
      return !!BOOT_KEYS[id] && !reduced() && !boot.seen(id);
    },
    /* The theme's script is about to show its overlay (or has decided there is none): the curtain goes. */
    release: function () {
      window.clearTimeout(failsafe);
      failsafe = 0;
      ROOT.removeAttribute("data-fx-boot");
    },
    begin: function () {
      boot.active = true;
      boot.release();
    },
    /* The overlay started to collapse: the entrance that waited plays now. */
    end: function () {
      boot.active = false;
      var dir = activeDirector();
      if (dir && dir.release) {
        dir.release();
      }
    }
  };

  /* A boot is due: the page is hidden behind the theme's own empty window until the script that plays the boot has
   * loaded (it arrives after the first state), so the dashboard never shows for a few frames and then vanishes. A
   * failsafe lifts the curtain if that script never comes. */
  (function () {
    var id = ROOT.getAttribute("data-theme");
    if (boot.due(id)) {
      ROOT.setAttribute("data-fx-boot", id);
      failsafe = window.setTimeout(boot.release, 3500);
    }
  })();

  // ── reduced motion switched on while something plays ────────────────────────────

  /* The setting (data-reduce-motion on <html>) is also announced by app.js, which calls cancel(main); the OS preference is
   * announced by nobody. Whichever it is, the active theme finishes what plays at its final text, and the theme scripts
   * (FX.onReduced) end what they own: a boot overlay, a noise. Everything here is safe to run twice. */
  var reducedSubs = [];

  FX.onReduced = function (fn) {
    if (typeof fn === "function") {
      reducedSubs.push(fn);
    }
  };

  function reducedTurnedOn() {
    if (!reduced()) {
      return;
    }
    var dir = activeDirector();
    var main = document.getElementById("main");
    if (dir && main) {
      dir.cancel(main);
    }
    reducedSubs.forEach(function (fn) {
      try {
        fn();
      } catch (e) {
        /* one subscriber failing must not keep the others from stopping */
      }
    });
  }

  if (typeof window.MutationObserver === "function") {
    new window.MutationObserver(reducedTurnedOn).observe(ROOT, { attributes: true, attributeFilter: ["data-reduce-motion"] });
  }
  if (window.matchMedia) {
    var reducedQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (reducedQuery.addEventListener) {
      reducedQuery.addEventListener("change", reducedTurnedOn);
    } else if (reducedQuery.addListener) {
      reducedQuery.addListener(reducedTurnedOn);
    }
  }

  // ── exports ─────────────────────────────────────────────────────────────────────

  FX.STAGGER_MAX = STAGGER_MAX;
  FX.reduced = reduced;
  FX.ease = ease;
  FX.easeInv = easeInv;
  FX.bezier = bezier;
  FX.jobs = jobs;
  FX.text = { directText: directText, firstText: firstText, parseNum: parseNum, fmtNum: fmtNum, scramble: scramble };
  FX.ghost = { prep: ghostPrep, on: ghostOn, off: ghostOff };
  FX.roller = roller;
  FX.typer = typer;
  FX.decoder = decoder;
  FX.prepare = prepare;
  FX.isPending = isPending;
  FX.scan = scan;
  /* app.js says a card was filled in place (the theme's arrive(card, info)): look at the placeholder marks now. Nothing
   * more is needed from it, since scan() finds the same arrivals from the marks, and an arrival plays only once. */
  FX.noticeCard = function () {
    scan();
  };
})();
