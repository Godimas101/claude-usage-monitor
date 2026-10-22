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
      if (allow && Object.prototype.hasOwnProperty.call(memory, key) && memory[key] !== text) {
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

  function start() {
    var main = document.getElementById("main");
    if (!main || typeof window.MutationObserver !== "function") {
      return;
    }
    if (themeIsOn()) {
      addDecoration();
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
      }
    }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    later();
  }

  window.CUM_THEME_FX = window.CUM_THEME_FX || {};
  window.CUM_THEME_FX.night_city = { start: start };
})();
