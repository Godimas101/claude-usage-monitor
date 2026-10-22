/* nostromo.js: the Nostromo flavour. A typed boot log plays once per browser session (sessionStorage),
 * a click or Esc skips it, and reduce_motion (or prefers-reduced-motion) shows the finished text at once.
 * app.js calls window.CUM_THEME_FX.nostromo.start() after this file loads, once per page load.
 * Every storage call is wrapped: a private window or blocked storage just means the boot plays again.
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
    if (started) {
      return;
    }
    started = true;
    var overlay = document.getElementById("boot");
    var box = document.getElementById("boot-text");
    if (!overlay || !box) {
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
      return;
    }

    overlay.removeAttribute("hidden");
    overlay.classList.add("is-on");
    overlay.addEventListener("click", finish);
    document.addEventListener("keydown", onKey);
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

  window.CUM_THEME_FX = window.CUM_THEME_FX || {};
  window.CUM_THEME_FX.nostromo = { start: start };
})();
