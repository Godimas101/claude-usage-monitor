/* gates.js: the Gates flavour of the dashboard (Windows 11 Fluent), a companion to gates.css.
 * The page works without it: capitals stay capitals, and icons and swatch colours are simply absent.
 *
 * app.js writes many labels in capitals (the approved Nostromo wording). A theme cannot undo that with CSS, because the
 * text itself is upper case, and Windows 11 writes sentence case. So this script rewrites those strings as they appear
 * ("RESETS 2:30 PM" -> "Resets 2:30 PM") and again whenever app.js draws them, and puts every one back when another theme
 * takes over. Only the app's own wording is touched: names and titles that come from your data (session titles, folders,
 * supporters, file paths, chart labels) are left exactly as they are. It also
 *   - flags html[data-g-icons] when Segoe Fluent Icons (or Segoe MDL2 Assets) is installed, which turns the glyphs on;
 *   - gives each status chip a tone from its words (data-g-tone), so "Under pace" is green and "At risk" amber;
 *   - sets the small percent sign on the big percentages;
 *   - reads the accent colours from /api/themes (the call app.js makes too) and flags html[data-g-swatches].
 * Nothing here stores or prints a token, a message or a file. app.js calls window.CUM_THEME_FX.gates.start() once this
 * file has loaded and again whenever the theme is applied; the script watches data-theme and stops when it is not gates.
 */
(function () {
  "use strict";

  var THEME = "gates";
  var ROOT = document.documentElement;
  // never rewritten: markup that is not wording, and names that come from your data
  var SKIP = "script,style,textarea,input,code,pre,svg,#tip,.nm-supp,.nm-path,td.nm-trunc,.nm-od-d,.nm-sr";
  var KEEP = { AM: 1, PM: 1, API: 1, MCP: 1, CSV: 1, JSON: 1, UTC: 1, OK: 1, ID: 1, PR: 1, PRS: 1, HTTP: 1, HTTPS: 1, URL: 1, USD: 1, UI: 1 };
  var NAMES = {
    SUN: "Sun", MON: "Mon", TUE: "Tue", WED: "Wed", THU: "Thu", FRI: "Fri", SAT: "Sat",
    JAN: "Jan", FEB: "Feb", MAR: "Mar", APR: "Apr", MAY: "May", JUN: "Jun", JUL: "Jul", AUG: "Aug", SEP: "Sep", OCT: "Oct", NOV: "Nov", DEC: "Dec",
    CLAUDE: "Claude", CHRIS: "Chris", CARPENTER: "Carpenter", GATES: "Gates", NOSTROMO: "Nostromo", GITHUB: "GitHub", PATREON: "Patreon", ANTHROPIC: "Anthropic",
    WINDOWS: "Windows"
  };
  var SMALL = { a: 1, an: 1, and: 1, as: 1, at: 1, by: 1, for: 1, in: 1, of: 1, on: 1, or: 1, the: 1, to: 1, with: 1 };
  // The link states app.js writes, as Windows 11 words them (the widget's own words where it has them).
  var VOCAB = {
    "UPLINK LIVE": "Live", "LIVE": "Live", "ACQUIRING": "Connecting", "AUTH LOST": "Sign-in needed", "NO CARRIER": "Offline",
    "RATE LIMITED": "Rate limited", "NO CREDENTIALS": "Not signed in", "PAUSED": "Paused", "NO LINK": "No connection"
  };
  var TONES = {
    "UNDER PACE": "ok", "ON PACE": "ok", "SAFE": "ok", "BEHIND": "ok", "NORMAL": "ok", "NOMINAL": "ok",
    "AHEAD OF PACE": "warn", "AHEAD": "warn", "AT RISK": "warn", "ADVISORY": "warn", "VERY FAST": "warn",
    "LIMITED": "alert", "CRITICAL": "alert", "FAST": "acc"
  };
  var ICON_PROBE = String.fromCharCode(0xE916);   // Segoe Fluent Icons: the stopwatch glyph
  var PHRASES = { "NONE": "None", "TOK / MIN": "Tokens / min" };
  var SVG_WORDS = { TODAY: "Today", NOW: "Now", LESS: "Less", MORE: "More", AVG: "Avg" };

  var active = false;
  var screen = null;
  var observer = null;
  var undo = [];                 // [{ref: WeakRef(text node or element), text}] so a theme change can put the wording back
  var swatchState = "idle";      // idle | loading | ready
  var swatchStyle = null;

  // ── wording ─────────────────────────────────────────────────────────────────────

  function isCaps(text) {
    return /[A-Z]/.test(text) && !/[a-z]/.test(text);
  }

  function lowerWords(text) {
    return text.replace(/[A-Za-z]+/g, function (word, at) {
      if (KEEP[word]) {
        return word;
      }
      if (NAMES[word]) {
        return NAMES[word];
      }
      // 2.6K, 1.5M, 3.2B: a magnitude written straight after its digits keeps its capital
      return /^[KMBT]$/.test(word) && /[\d.]/.test(text.charAt(at - 1)) ? word : word.toLowerCase();
    });
  }

  /* Dates and words the app writes in capitals inside an otherwise ordinary sentence. */
  function repair(text) {
    return text
      .replace(/\b(SUN|MON|TUE|WED|THU|FRI|SAT)\b(?=,| \d)/g, function (d) { return NAMES[d]; })
      .replace(/\b(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\b(?= \d)/g, function (m) { return NAMES[m]; })
      .replace(/\bapi-equivalent\b/g, "API-equivalent");
  }

  function raise(text) {
    return text.replace(/^([^A-Za-z0-9]*)([a-z])/, function (all, lead, letter) {
      return lead + letter.toUpperCase();
    });
  }

  function sentence(text, starts) {
    var out = lowerWords(text);
    return starts ? raise(out) : out;
  }

  function titleCase(text) {
    var out = lowerWords(text).replace(/(^|\s)([a-z]+)/g, function (all, gap, word) {
      return gap + (SMALL[word] ? word : word.charAt(0).toUpperCase() + word.slice(1));
    });
    return raise(out);
  }

  /* The new text for a text node, or null when it stays as it is. */
  function recase(node) {
    var raw = node.nodeValue;
    var host = node.parentElement;
    if (!raw || !host) {
      return null;
    }
    var lead = /^\s*/.exec(raw)[0];
    var tail = /\s*$/.exec(raw)[0];
    var core = raw.slice(lead.length, raw.length - tail.length);
    if (PHRASES[core]) {
      return lead + PHRASES[core] + tail;
    }
    // the tooltip shows chart text ("OCT 25: $610.31 ..."): its dates get the same repair, nothing else is touched
    if (host.closest(SKIP) && !host.closest("#tip")) {
      return null;
    }
    if (!isCaps(raw) || host.closest("#tip")) {
      var fixed = repair(raw);
      return fixed === raw ? null : fixed;
    }
    if (VOCAB[core]) {
      return lead + VOCAB[core] + tail;
    }
    var age = /^DATA AGE (.+)$/.exec(core);
    if (age) {
      return lead + "Updated " + lowerWords(age[1]) + (/\d/.test(age[1]) ? " ago" : "") + tail;
    }
    var next;
    if (host.closest(".nm-brand, .nm-mrow .k, .nm-authors")) {
      next = titleCase(core);
    } else if (host.closest("#set-theme-meta")) {
      next = core.split(" · ").map(function (part) { return sentence(part, true); }).join(" · ");
    } else {
      next = sentence(core, !textBefore(node));
    }
    return lead + next + tail;
  }

  /* True when words come before this text inside the same parent: the text then continues a sentence. */
  function textBefore(node) {
    for (var n = node.previousSibling; n; n = n.previousSibling) {
      if (/[A-Za-z]/.test(n.textContent || "")) {
        return true;
      }
    }
    return false;
  }

  function remember(target, text, attr) {
    if (typeof WeakRef !== "function") {
      return;
    }
    undo.push({ ref: new WeakRef(target), text: text, attr: attr || "" });
    if (undo.length > 2000) {
      // text that app.js has since replaced (the age ticks every second) needs no putting back
      undo = undo.filter(function (entry) {
        var node = entry.ref.deref();
        return node !== undefined && node.isConnected;
      });
    }
  }

  function fixText(node) {
    var next = recase(node);
    if (next !== null && next !== node.nodeValue) {
      remember(node, node.nodeValue);
      node.nodeValue = next;
    }
    var host = node.parentElement;
    if (host) {
      var chip = host.closest(".nm-chip");
      if (chip) {
        toneChip(chip);
      }
      splitPercent(host);
    }
  }

  /* "13%" in a big figure: the sign is set smaller, as in the approved design. */
  function splitPercent(el) {
    if (!el.classList.contains("nm-big") || el.classList.contains("m") || el.classList.contains("s") || el.childNodes.length !== 1) {
      return;
    }
    var m = /^(\d[\d.,]*)%$/.exec(el.textContent);
    if (!m) {
      return;
    }
    remember(el, el.textContent);
    el.textContent = m[1];
    var sign = document.createElement("small");
    sign.textContent = "%";
    el.appendChild(sign);
  }

  function toneChip(chip) {
    var words = (chip.textContent || "").trim().toUpperCase();
    chip.setAttribute("data-g-tone", TONES[words] || "neutral");
  }

  /* Chart text carries no user data except the folder and tool names on the left of the bars, so only strings made
   * wholly of the app's own words (a day, a month, "today", "avg") are rewritten. */
  function fixChartText(text) {
    var raw = text.textContent;
    if (!raw || !isCaps(raw)) {
      return;
    }
    var words = raw.match(/[A-Za-z]+/g) || [];
    var known = words.every(function (w) { return SVG_WORDS[w] || NAMES[w] || KEEP[w]; });
    if (!known) {
      return;
    }
    var next = raw.replace(/[A-Za-z]+/g, function (w) { return SVG_WORDS[w] || NAMES[w] || w; });
    if (next !== raw && text.childNodes.length === 1) {
      remember(text.firstChild, raw);
      text.firstChild.nodeValue = next;
    }
  }

  function fixTree(node) {
    if (node.nodeType === 3) {
      fixText(node);
      return;
    }
    if (node.nodeType !== 1) {
      return;
    }
    if (node.closest && node.closest("svg")) {
      fixSvg(node.closest("svg"));
      return;
    }
    var walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT, null);
    var list = [];
    for (var n = walker.nextNode(); n; n = walker.nextNode()) {
      list.push(n);
    }
    list.forEach(fixText);
    Array.prototype.forEach.call(node.querySelectorAll ? node.querySelectorAll("svg") : [], fixSvg);
    Array.prototype.forEach.call(node.querySelectorAll ? node.querySelectorAll("input[placeholder]") : [], fixPlaceholder);
    if (node.matches && node.matches("input[placeholder]")) {
      fixPlaceholder(node);
    }
  }

  /* A placeholder is an attribute, not text: "AUTO" in an empty box becomes "Auto". */
  function fixPlaceholder(input) {
    var raw = input.getAttribute("placeholder") || "";
    if (isCaps(raw)) {
      remember(input, raw, "placeholder");
      input.setAttribute("placeholder", sentence(raw, true));
    }
  }

  function fixSvg(svg) {
    Array.prototype.forEach.call(svg.querySelectorAll("text.cum-t"), fixChartText);
  }

  function onMutations(records) {
    var seen = new Set();
    records.forEach(function (record) {
      if (record.type === "characterData") {
        seen.add(record.target);
        return;
      }
      Array.prototype.forEach.call(record.addedNodes, function (node) { seen.add(node); });
    });
    seen.forEach(function (node) {
      try {
        if (node.isConnected) {
          fixTree(node);
        }
      } catch (e) {
        /* one odd node must not stop the rest */
      }
    });
  }

  function restore() {
    var list = undo;
    undo = [];
    list.forEach(function (entry) {
      var target = entry.ref.deref();
      if (!target || !target.isConnected) {
        return;
      }
      if (entry.attr) {
        target.setAttribute(entry.attr, entry.text);
      } else if (target.nodeType === 3) {
        target.nodeValue = entry.text;
      } else {
        target.textContent = entry.text;
      }
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-g-tone]"), function (el) { el.removeAttribute("data-g-tone"); });
  }

  // ── icons and swatches ──────────────────────────────────────────────────────────

  function hasFont(family) {
    try {
      var ctx = document.createElement("canvas").getContext("2d");
      ctx.font = '32px "' + family + '", monospace';
      var a = ctx.measureText(ICON_PROBE).width;
      ctx.font = '32px "' + family + '", serif';
      return a === ctx.measureText(ICON_PROBE).width;
    } catch (e) {
      return false;
    }
  }

  function flagIcons() {
    if (hasFont("Segoe Fluent Icons") || hasFont("Segoe MDL2 Assets")) {
      ROOT.setAttribute("data-g-icons", "1");
    }
  }

  function swatchRules(themes) {
    var gates = (themes || []).filter(function (t) { return t && t.id === THEME; })[0];
    var rules = [];
    ((gates && gates.variants) || []).forEach(function (v) {
      var hex = v && v.colors && /^#[0-9a-f]{6}/i.test(v.colors.accent || "") ? v.colors.accent.slice(0, 7).toLowerCase() : "";
      var accent = String(v && v.accent || "");
      var mode = v && (v.mode === "light" || v.mode === "dark") ? v.mode : "";
      if (hex && mode && /^[a-z0-9_-]+$/.test(accent)) {
        rules.push("html[data-variant$=\"-" + mode + "\"] [data-accent=\"" + accent + "\"]{--g-swatch:" + hex + "}");
      }
    });
    return rules.join("\n");
  }

  function loadSwatches() {
    if (swatchState !== "idle") {
      return;
    }
    var meta = document.querySelector('meta[name="monitor-token"]');
    swatchState = "loading";
    fetch("/api/themes", {
      headers: { "X-Monitor-Token": meta ? meta.getAttribute("content") || "" : "", Accept: "application/json" },
      cache: "no-store", credentials: "omit"
    }).then(function (res) {
      return res.ok ? res.json() : null;
    }).then(function (data) {
      var css = data ? swatchRules(data.themes) : "";
      if (!css || !active) {
        swatchState = "idle";
        return;
      }
      swatchStyle = document.createElement("style");
      swatchStyle.id = "gates-swatches";
      swatchStyle.textContent = css;
      document.head.appendChild(swatchStyle);
      ROOT.setAttribute("data-g-swatches", "1");
      swatchState = "ready";
    }).catch(function () {
      swatchState = "idle";
    });
  }

  // ── life cycle ──────────────────────────────────────────────────────────────────

  function start() {
    if (active || ROOT.getAttribute("data-theme") !== THEME) {
      return;
    }
    screen = document.getElementById("screen") || document.body;
    active = true;
    flagIcons();
    try {
      fixTree(screen);
    } catch (e) {
      /* the page still reads fine in capitals */
    }
    observer = new MutationObserver(onMutations);
    observer.observe(screen, { childList: true, subtree: true, characterData: true });
    loadSwatches();
  }

  function stop() {
    if (!active) {
      return;
    }
    active = false;
    if (observer) {
      observer.disconnect();
      observer = null;
    }
    restore();
    ROOT.removeAttribute("data-g-icons");
    ROOT.removeAttribute("data-g-swatches");
    if (swatchStyle && swatchStyle.parentNode) {
      swatchStyle.parentNode.removeChild(swatchStyle);
    }
    swatchStyle = null;
    swatchState = "idle";
  }

  new MutationObserver(function () {
    if (ROOT.getAttribute("data-theme") === THEME) {
      start();
    } else {
      stop();
    }
  }).observe(ROOT, { attributes: true, attributeFilter: ["data-theme"] });

  window.CUM_THEME_FX = window.CUM_THEME_FX || {};
  window.CUM_THEME_FX.gates = { start: start };
})();
