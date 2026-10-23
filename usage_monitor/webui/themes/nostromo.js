/* nostromo.js: the Nostromo flavour.
 *
 * 1. The boot. A typed boot log plays once per browser session (sessionStorage), a click or Esc skips it, and reduce_motion
 *    (or prefers-reduced-motion) shows the finished text at once. app.js calls window.CUM_THEME_FX.nostromo.start() after
 *    this file loads, once per page load. Every storage call is wrapped: a private window or blocked storage just means
 *    the boot plays again.
 * 2. The motion layer (motion.js, nostromo.motion.css): a CRT terminal that prints the page. enter(main, page) plays the
 *    page's entrance (cards flicker on under a phosphor line, the figures are typed out with a block cursor, the pips light
 *    cell by cell), cancel(main) finishes it, and arrive(card) plays the same typing and pips for a card whose data has just
 *    landed. While the boot overlay is up the entrance waits and plays as the tube collapses.
 */
(function () {
  "use strict";

  var STORE_KEY = "nm-boot-session";
  var STEP_CHARS = 2;
  var STEP_MS = 14;
  var HOLD_MS = 900;
  var BOOT_LINES = [
    "CLAUDE USAGE MONITOR  //  ONBOARD COMPUTER  REV 2",
    "",
    "> LINK ESTABLISHED ................. OK",
    "> READING SESSION LOG .............. OK",
    "> PACE CURVES CALIBRATED ........... OK",
    "> MONEY FIGURES ARE API-EQUIVALENT AT LIST PRICES",
    "",
    "> ALL SYSTEMS NOMINAL. HAVE A PRODUCTIVE SHIFT."
  ].join("\n");

  var FX = window.CUM_FX || null;
  var started = false;
  var timers = [];

  function seenThisSession() {
    try {
      return window.sessionStorage.getItem(STORE_KEY) === "1";
    } catch (e) {
      return false;
    }
  }

  function markSeen() {
    try {
      window.sessionStorage.setItem(STORE_KEY, "1");
    } catch (e) {
      /* private window: the boot simply plays again next time */
    }
  }

  function reducedMotion() {
    if (document.documentElement.getAttribute("data-reduce-motion") === "1") {
      return true;
    }
    return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }

  function setText(box, cursor, count) {
    box.textContent = BOOT_LINES.slice(0, count);
    box.appendChild(cursor);
  }

  function start() {
    if (FX) {
      FX.watch(document.getElementById("main"));
    }
    if (started) {
      return;
    }
    started = true;
    var overlay = document.getElementById("boot");
    var box = document.getElementById("boot-text");
    if (!overlay || !box) {
      if (FX) {
        FX.boot.release();
      }
      return;
    }
    var cursor = document.createElement("span");
    cursor.className = "nm-boot-cur";
    cursor.setAttribute("aria-hidden", "true");

    function finish() {
      timers.forEach(function (t) { window.clearTimeout(t); });
      timers = [];
      overlay.classList.add("is-done");
      markSeen();
      if (FX) {
        FX.boot.end();                       // the page enters as the tube collapses
      }
      timers.push(window.setTimeout(function () {
        overlay.classList.remove("is-on", "is-done");
        overlay.setAttribute("hidden", "");
      }, 700));
      overlay.removeEventListener("click", finish);
      document.removeEventListener("keydown", onKey);
    }

    function onKey(event) {
      if (event.key === "Escape" || event.key === "Enter" || event.key === " ") {
        finish();
      }
    }

    if (seenThisSession() || reducedMotion()) {
      setText(box, cursor, BOOT_LINES.length);
      overlay.setAttribute("hidden", "");
      markSeen();
      if (FX) {
        FX.boot.release();
      }
      return;
    }

    overlay.removeAttribute("hidden");
    overlay.classList.add("is-on");
    overlay.addEventListener("click", finish);
    document.addEventListener("keydown", onKey);
    if (FX) {
      FX.boot.begin();                       // the overlay covers the page: the curtain goes, the entrance waits
      if (FX.onReduced) {
        FX.onReduced(function () {           // reduce motion was switched on (the setting or the OS) while the boot plays
          if (overlay.classList.contains("is-on") && !overlay.classList.contains("is-done")) {
            finish();
            overlay.classList.remove("is-on", "is-done");      // no collapse to wait for: it is gone now
            overlay.setAttribute("hidden", "");
          }
        });
      }
    }
    var count = 0;
    (function step() {
      count = Math.min(BOOT_LINES.length, count + STEP_CHARS);
      setText(box, cursor, count);
      if (count < BOOT_LINES.length) {
        timers.push(window.setTimeout(step, STEP_MS));
      } else {
        timers.push(window.setTimeout(finish, HOLD_MS));
      }
    })();
  }

  // ── the motion layer ────────────────────────────────────────────────────────────

  /* Steps, not curves. Card i starts at 40 + 55 i ms; `at` is ms after the card starts, `per` the ms a character takes and
   * `cap` the most one text may take (so "13%" types in 132 ms and a long label still ends in time). The title block
   * starts at 0 and 120. */
  var dir = FX ? FX.director({
    id: "nostromo", c0: 40, step: 55, total: 1240, landMs: 760, ghost: false, defer: false,
    head: [
      { sel: ".nm-h1", at: 0, per: 24, cap: 260 },
      { sel: ".nm-sub", at: 120, per: 4, cap: 300 }
    ],
    card: [
      { sel: ".nm-tag, .nm-ttl", at: 100, per: 16, cap: 300 },
      { sel: ".nm-meta-t", at: 150, per: 9, cap: 260 },
      { sel: ".nm-big, .nm-stat-v", at: 150, per: 44, cap: 330 },
      { sel: ".nm-mrow .k", at: 170, per: 14, cap: 200 },
      { sel: ".nm-mrow .v", at: 200, per: 30, cap: 220 },
      { sel: ".nm-kv-grid .nm-kv b", at: 220, per: 12, cap: 240 }
    ],
    fallback: { at: 150, per: 44, cap: 330 },
    make: function (owner, el, at, g, style, early) {
      var text = FX.text.firstText(el);
      return text ? FX.typer(owner, text, at, g.per, g.cap, 90, early) : null;
    }
  }) : null;

  window.CUM_THEME_FX = window.CUM_THEME_FX || {};
  window.CUM_THEME_FX.nostromo = { start: start };
  if (dir) {
    window.CUM_THEME_FX.nostromo.enter = dir.enter;
    window.CUM_THEME_FX.nostromo.cancel = dir.cancel;
    window.CUM_THEME_FX.nostromo.arrive = FX.noticeCard;
  }
})();
