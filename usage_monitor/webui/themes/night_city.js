/* night_city.js: the Night City flavour.
 *
 * 1. The refresh hit. When a figure on the page changes value (new data arrived), it plays the brief three-frame
 *    RGB split once: the .nc-hit class, whose keyframes live in night_city.css. Nothing loops and nothing runs on a
 *    timer. A figure the first time a page shows it is only recorded, so opening a page or switching variant never
 *    glitches. reduce_motion (data-reduce-motion="1" on <html>) and prefers-reduced-motion switch it off here, and
 *    the stylesheet stops the animation too.
 * 2. Two small decorations in the nav, from the approved design: the Blackwall reticle and the Edgerun code matrix
 *    (nothing but shapes and the noise text the design shows; the stylesheet decides which one a variant gets and
 *    when there is room). They sit in one aria-hidden block that starts out hidden, so a theme that does not know
 *    it never shows it, and the block is removed when the theme changes.
 * 3. The motion layer (motion.js, night_city.motion.css): a HUD that jacks in. enter(main, page) plays the page's
 *    entrance (cards tear on under a scan line, the figures decode and lock left to right, the RAM cells fill with a colour
 *    split that snaps clean), cancel(main) finishes it, and arrive(card) plays the decode and the fill for a card whose data
 *    has just landed. A figure that waits for its data cycles glyph noise (12 frames a second, only while the window is
 *    shown) and a bar shows a faint scan along its track.
 * 4. The boot: a jack-in sequence (a breach-protocol panel, a log that resolves, the RAM filling, one RGB split, access
 *    granted) once per browser session, a click or Esc skips it, never under reduced motion. The page enters as it collapses.
 *
 * app.js calls window.CUM_THEME_FX.night_city.start() after this file loads, and again whenever the theme is
 * applied, so start() is safe to call many times. It reads only the text of figures already on the page and never
 * stores or sends anything.
 */
(function () {
  "use strict";

  var THEME = "night_city";
  var FIGURES = ".nm-big, .nm-stat-v, .nm-cmp-n";
  var HIT = "nc-hit";
  var HIT_MS = 180;
  var DECO_ID = "nc-deco";
  var CODES = ["BD", "E9", "1C", "BD", "BD", "1C", "1C", "BD", "BD", "55", "BD", "BD", "55", "BD", "BD", "1C", "55", "55", "E9", "BD"];
  var LIT = { 2: true, 8: true, 12: true, 16: true };     // the cells drawn inverse, as in the design
  var RETICLE =
    '<svg class="nc-reticle" width="64" height="64" viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="1">' +
    '<circle cx="32" cy="32" r="27"/><circle cx="32" cy="32" r="13" stroke-dasharray="3 3"/>' +
    '<path d="M32 3v11M32 50v11M3 32h11M50 32h11"/><path d="M7 15V7h8M57 15V7h-8M7 49v8h8M57 49v8h-8"/></svg>';

  var FX = window.CUM_FX || null;
  var started = false;
  var memory = {};        // figure key -> the text it showed last, for the page on screen
  var memoryPage = null;
  var queued = 0;

  function reducedMotion() {
    if (document.documentElement.getAttribute("data-reduce-motion") === "1") {
      return true;
    }
    return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }

  function themeIsOn() {
    return document.documentElement.getAttribute("data-theme") === THEME;
  }

  // ── the refresh hit ──────────────────────────────────────────────────────────────

  /* Where a figure lives: its stat tile, else its card (by what names the card), so two renders of the same
   * page pair the same figures up even when a card appears or goes. */
  function ownerKey(el) {
    var stat = el.closest(".nm-stat");
    if (stat) {
      return "stat:" + (stat.getAttribute("data-fid") || "");
    }
    var card = el.closest(".nm-card, .nm-compare");
    if (card) {
      return "card:" + (card.getAttribute("data-limit") || card.getAttribute("id") || card.getAttribute("aria-label") || card.className);
    }
    return "page";
  }

  function textOf(el) {
    return String(el.textContent || "").replace(/\s+/g, " ").trim();
  }

  function play(el) {
    if (el.classList.contains(HIT)) {
      el.classList.remove(HIT);
      void el.offsetWidth;                   // restart the animation if one was still running
    }
    el.classList.add(HIT);
    window.setTimeout(function () { el.classList.remove(HIT); }, HIT_MS);
  }

  function scan(main) {
    if (!themeIsOn()) {
      return;
    }
    var page = window.location.hash || "#overview";
    var firstOfPage = page !== memoryPage;
    memoryPage = page;
    var allow = !firstOfPage && !reducedMotion() && document.visibilityState !== "hidden";
    var seen = {};
    var next = {};
    var nodes = main.querySelectorAll(FIGURES);
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      var base = ownerKey(el) + "|" + el.className.replace(/\s*nc-hit\b/, "");
      seen[base] = (seen[base] || 0) + 1;
      var key = base + "#" + seen[base];
      var text = textOf(el);
      next[key] = text;
      /* a figure that is being decoded (data-fx) or waits for its data (data-pending) already has its own effect: the
         placeholder giving way to the value is not a refresh */
      if (allow && Object.prototype.hasOwnProperty.call(memory, key) && memory[key] !== text && !el.hasAttribute("data-fx")) {
        play(el);
      }
    }
    memory = next;
  }

  // ── the nav decoration ───────────────────────────────────────────────────────────

  function matrixHtml() {
    var cells = CODES.map(function (code, i) {
      return "<i" + (LIT[i] ? ' class="hi"' : "") + ">" + code + "</i>";
    }).join("");
    return '<div class="nc-matrix"><span class="mh">Code matrix</span><div class="nc-mx">' + cells + "</div>" +
      '<div class="nc-buf"><i class="on">BD</i><i class="on">E9</i><i class="on">1C</i><i>55</i></div></div>';
  }

  function addDecoration() {
    var nav = document.querySelector(".nm-nav");
    if (!nav || document.getElementById(DECO_ID)) {
      return;
    }
    var box = document.createElement("div");
    box.id = DECO_ID;
    box.className = "nc-deco";
    box.setAttribute("aria-hidden", "true");
    box.style.display = "none";              // night_city.css shows it (with !important); other themes never do
    box.innerHTML = RETICLE + matrixHtml();
    nav.insertBefore(box, nav.querySelector(".nm-health"));
  }

  function removeDecoration() {
    var box = document.getElementById(DECO_ID);
    if (box && box.parentNode) {
      box.parentNode.removeChild(box);
    }
  }

  // ── placeholders: a figure that waits for its data cycles glyph noise ─────────────

  var NOISE_MS = 83;                         // about 12 frames a second: cheap, and it reads as a stuttering signal
  var HEX = "0123456789ABCDEF";
  var noiseEls = [];
  var noiseTimer = 0;

  /* Hex digits in the places the placeholder has digits or letters, its punctuation where it has it ("00%" cycles as "A7%"),
   * so the noise takes the room the value will. A placeholder with nothing to copy gets four digits. */
  function noiseText(el) {
    var text = textOf(el);
    var out = "";
    var any = false;
    for (var i = 0; i < text.length; i++) {
      var ch = text.charAt(i);
      if (/[0-9A-Za-z]/.test(ch)) {
        any = true;
        out += HEX.charAt(Math.floor(Math.random() * 16));
      } else {
        out += ch;
      }
    }
    if (!any) {
      out = "";
      for (var j = 0; j < 4; j++) {
        out += HEX.charAt(Math.floor(Math.random() * 16));
      }
    }
    return out;
  }

  function noiseTick() {
    noiseTimer = 0;
    if (!themeIsOn() || reducedMotion() || document.visibilityState === "hidden") {
      return;                                // paused: visibilitychange starts it again
    }
    noiseEls = noiseEls.filter(function (el) { return el.isConnected && el.hasAttribute("data-fx-pend"); });
    noiseEls.forEach(function (el) { el.setAttribute("data-fx", noiseText(el)); });
    if (noiseEls.length) {
      noiseTimer = window.setTimeout(noiseTick, NOISE_MS);
    }
  }

  function noiseKick() {
    if (!noiseTimer && noiseEls.length && document.visibilityState !== "hidden") {
      noiseTimer = window.setTimeout(noiseTick, NOISE_MS);
    }
  }

  /* motion.js marked a slot as a placeholder: a figure starts to cycle noise. Under reduced motion it stays what it is, a
   * static placeholder in the dim tone. */
  function pend(el, kind) {
    if (kind !== "num" || reducedMotion() || el.hasAttribute("data-fx")) {
      return;
    }
    noiseEls.push(el);
    el.setAttribute("data-fx", noiseText(el));
    noiseKick();
  }

  /* The slot is no longer a placeholder: it stops cycling. With keep, the noise stays on screen for the decode to carry on
   * from; without, the figure is just its text again. */
  function unpend(el, keep) {
    var at = noiseEls.indexOf(el);
    if (at >= 0) {
      noiseEls.splice(at, 1);
      if (!keep) {
        FX.ghost.off(el);
      }
    }
  }

  function noiseStop() {
    noiseEls.forEach(function (el) { if (el.isConnected) { FX.ghost.off(el); } });
    noiseEls = [];
    window.clearTimeout(noiseTimer);
    noiseTimer = 0;
  }

  // ── the motion layer ─────────────────────────────────────────────────────────────

  var FRAME_MS = 38;                         // ms between two scramble frames

  /* Hard frames, then everything snaps clean. Card i starts at 40 + 45 i ms; `at` is ms after the card starts. A text
   * decodes over `dur`: character i locks at 22 % + 78 % * (i + 1) / n of it. */
  var dir = FX ? FX.director({
    id: THEME, c0: 40, step: 45, total: 940, landMs: 700, ghost: true, defer: false,
    head: [{ sel: ".nm-h1", at: 0, dur: 260 }],
    card: [
      { sel: ".nm-tag, .nm-ttl", at: 80, dur: 220 },
      { sel: ".nm-big, .nm-stat-v", at: 100, dur: 380 },
      { sel: ".nm-mrow .v, .nm-kv-grid .nm-kv b", at: 120, dur: 300 }
    ],
    fallback: { at: 100, dur: 380 },
    make: function (owner, el, at, g, style, early) {
      return FX.decoder(owner, el, at, g.dur, style, FRAME_MS, early);
    },
    pend: pend,
    unpend: unpend
  }) : null;

  // ── the boot: jack-in ────────────────────────────────────────────────────────────

  var BOOT_LINES = [
    ["JACK-IN .........................", "OK"],
    ["ICE BYPASSED ....................", "OK"],
    ["DAEMONS LOADED ..................", "4/4"],
    ["RAM ALLOCATED ...................", "32/32"],
    ["COSTS ARE API-EQUIVALENT AT LIST PRICES", ""]
  ];
  var BOOT_NOISE = ["BD", "E9", "1C", "55", "FF", "7A", "A1", "00"];
  /* ms from the first frame: the title resolves, the log lines resolve one after another, the RAM fills, the matrix locks
   * column by column, the buffer slots light, access is granted, one RGB split, then the screen collapses to a line */
  var BT = { title: 420, line0: 260, lineStep: 170, lineDur: 300, matrix: 480, matrixStep: 150, slots: 1180, slotStep: 130,
    ramAt: 300, ramMs: 1150, grant: 1640, split: 1860, go: 2040, hide: 240 };
  var bootChecked = false;
  var bootAbort = null;

  function buildBoot() {
    var box = document.createElement("div");
    box.className = "nc-boot";
    box.setAttribute("aria-hidden", "true");
    box.hidden = true;
    var cells = CODES.map(function (code) { return "<i>" + code + "</i>"; }).join("");
    var ram = "";
    for (var i = 0; i < 32; i++) {
      ram += "<i></i>";
    }
    var log = BOOT_LINES.map(function () {
      return '<span class="ln"><span class="pfx">&gt; </span><span class="tx"></span><span class="st"></span></span>';
    }).join("");
    box.innerHTML = '<div class="nc-b-hz"></div><div class="nc-b-scan"></div><div class="nc-b-wrap"><div class="nc-b-in"><div class="nc-b-pan">' +
      '<div class="nc-b-head"><span class="nc-b-plate">NC//USAGE</span><span class="nc-b-title"></span><span class="nc-b-rev">NETRUNNER LINK &nbsp;REV 2</span></div>' +
      '<div class="nc-b-main"><pre class="nc-b-log">' + log + '</pre><div class="nc-b-side"><span class="mh">Breach protocol</span>' +
      '<div class="nc-mx nc-b-mx">' + cells + '</div><div class="nc-buf nc-b-buf"><i>BD</i><i>E9</i><i>1C</i><i>55</i></div></div></div>' +
      '<div class="nc-b-ram"><span class="nc-b-lab">RAM</span><div class="nc-b-cells">' + ram + '</div><span class="nc-b-cnt">00/32</span></div>' +
      '<div class="nc-b-grant"><span class="nc-b-plate">ACCESS GRANTED</span><span class="tag">// STAY SHARP</span></div>' +
      "</div></div></div>";
    return box;
  }

  function sel(root, selector) {
    return Array.prototype.slice.call(root.querySelectorAll(selector));
  }

  function playBoot(screen) {
    var ov = buildBoot();
    screen.appendChild(ov);
    var title = ov.querySelector(".nc-b-title");
    var lines = sel(ov, ".ln");
    var cells = sel(ov, ".nc-b-mx i");
    var slots = sel(ov, ".nc-b-buf i");
    var ram = sel(ov, ".nc-b-cells i");
    var cnt = ov.querySelector(".nc-b-cnt");
    var grant = ov.querySelector(".nc-b-grant");
    var t0 = 0;
    var lastNoise = -1e9;
    var lastCells = -1;
    var over = false;
    var went = false;
    var split = false;
    var hideTimer = 0;
    var job = null;

    function scr(text, p) {                  // text with its first p share locked, the rest scrambled
      var n = text.length;
      var lock = Math.floor(n * p);
      var out = "";
      for (var i = 0; i < n; i++) {
        out += i < lock ? text.charAt(i) : FX.text.scramble(text.charAt(i));
      }
      return out;
    }

    function noise() { return BOOT_NOISE[Math.floor(Math.random() * BOOT_NOISE.length)]; }

    function cleanup() {
      ov.removeEventListener("click", onSkip);
      document.removeEventListener("keydown", onKey);
    }

    function hide() {
      if (over) {
        return;                              // (jobs.cancel below calls back into this)
      }
      window.clearTimeout(hideTimer);
      over = true;
      cleanup();
      FX.jobs.cancel(ov);
      ov.classList.remove("is-on", "is-collapse", "is-split");
      ov.hidden = true;
      bootAbort = null;
    }

    function collapse() {
      if (went) {
        return;
      }
      went = true;
      ov.classList.remove("is-split");
      ov.classList.add("is-collapse");
      FX.boot.mark(THEME);
      FX.boot.end();                         // the page enters under the collapsing screen
      hideTimer = window.setTimeout(hide, BT.hide);
    }

    function onKey(event) {
      if (event.key === "Escape" || event.key === "Enter" || event.key === " ") {
        collapse();
      }
    }

    function onSkip() { collapse(); }

    function reset() {
      title.textContent = "";
      lines.forEach(function (l) {
        l.className = "ln";
        l.querySelector(".tx").textContent = "";
        l.querySelector(".st").textContent = "";
        l.querySelector(".st").className = "st";
      });
      cells.forEach(function (c) { c.className = "is-scr"; c.textContent = noise(); });
      slots.forEach(function (s) { s.className = ""; });
      ram.forEach(function (r) { r.className = ""; });
      cnt.textContent = "00/32";
      grant.classList.remove("is-on");
    }

    reset();
    ov.hidden = false;
    ov.classList.add("is-on");
    ov.addEventListener("click", onSkip);
    document.addEventListener("keydown", onKey);
    FX.boot.begin();                         // the overlay covers the page: the curtain goes, the entrance waits
    t0 = window.performance.now();
    job = {
      owner: ov,
      update: function (now) {
        if (over) {
          return true;
        }
        var t = now - t0;
        if (went) {
          return false;                      // the collapse is CSS; hide() ends the job
        }
        title.textContent = scr("JACKING IN", Math.max(0, Math.min(1, t / BT.title)));
        var j;
        var p;
        for (j = 0; j < lines.length; j++) {
          var at = BT.line0 + j * BT.lineStep;
          if (t < at) {
            continue;
          }
          lines[j].classList.add("is-on");
          p = Math.max(0, Math.min(1, (t - at) / BT.lineDur));
          lines[j].querySelector(".tx").textContent = scr(BOOT_LINES[j][0], p);
          var st = lines[j].querySelector(".st");
          st.textContent = scr(BOOT_LINES[j][1], p);
          if (p >= 1) {
            st.classList.add("is-hot");
          }
        }
        if (t - lastNoise >= 70) {           // the matrix: noise until a column locks, then its true codes
          lastNoise = t;
          cells.forEach(function (c, i) {
            if (t >= BT.matrix + (i % 5) * BT.matrixStep) {
              c.textContent = CODES[i];
              c.className = LIT[i] ? "hi" : "";
            } else {
              c.textContent = noise();
            }
          });
        }
        slots.forEach(function (s, k) {
          if (t >= BT.slots + k * BT.slotStep && !s.classList.contains("on")) {
            s.classList.add("on");
          }
        });
        var n = Math.max(0, Math.min(32, Math.floor((t - BT.ramAt) / (BT.ramMs / 32)) + 1));
        if (n !== lastCells) {
          lastCells = n;
          ram.forEach(function (r, i) { r.className = i < n - 1 ? "on" : i === n - 1 ? (n < 32 ? "lead" : "on") : ""; });
          cnt.textContent = (n < 10 ? "0" : "") + n + "/32";
        }
        if (t >= BT.grant) {
          grant.classList.add("is-on");
        }
        if (t >= BT.split && !split) {
          split = true;
          ov.classList.add("is-split");
        }
        if (t >= BT.go) {
          collapse();
        }
        return false;
      },
      cancel: function () { hide(); }
    };
    /* the window can be hidden or reduce motion switched on while it plays: the boot ends at once */
    bootAbort = function () {
      if (!went) {
        FX.boot.mark(THEME);
        FX.boot.end();
      }
      went = true;
      hide();
    };
    FX.jobs.add(job);
  }

  function maybeBoot() {
    if (bootChecked || !FX || !themeIsOn()) {
      return;
    }
    bootChecked = true;
    var screen = document.getElementById("screen");
    if (reducedMotion()) {
      FX.boot.mark(THEME);                   // no overlay, flag set, the page at once
      FX.boot.release();
      return;
    }
    if (!screen || !FX.boot.due(THEME)) {
      FX.boot.release();
      return;
    }
    playBoot(screen);
  }

  // ── life cycle ───────────────────────────────────────────────────────────────────

  function start() {
    var main = document.getElementById("main");
    if (!main || typeof window.MutationObserver !== "function") {
      return;
    }
    if (themeIsOn()) {
      addDecoration();
      if (FX) {
        FX.watch(main);
      }
      maybeBoot();
    }
    if (started) {
      return;
    }
    started = true;
    function later() {
      if (queued) {
        return;
      }
      queued = window.requestAnimationFrame(function () {
        queued = 0;
        try {
          scan(main);
        } catch (e) {
          /* a figure the scan cannot read is simply not animated */
        }
      });
    }
    new window.MutationObserver(later).observe(main, { childList: true, subtree: true, characterData: true });
    new window.MutationObserver(function () {
      if (themeIsOn()) {
        addDecoration();
      } else {
        removeDecoration();
        if (FX) {
          noiseStop();
          if (bootAbort) {
            bootAbort();
          }
        }
      }
    }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    if (FX && FX.onReduced) {
      FX.onReduced(function () {             // reduce motion was switched on (the setting or the OS): a boot ends, the noise stops
        if (bootAbort) {
          bootAbort();
        }
        noiseStop();
      });
    }
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") {
        noiseKick();
      }
    });
    later();
  }

  window.CUM_THEME_FX = window.CUM_THEME_FX || {};
  window.CUM_THEME_FX.night_city = { start: start };
  if (dir) {
    window.CUM_THEME_FX.night_city.enter = dir.enter;
    window.CUM_THEME_FX.night_city.cancel = dir.cancel;
    window.CUM_THEME_FX.night_city.arrive = FX.noticeCard;
  }
})();
