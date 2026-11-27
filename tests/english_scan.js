"use strict";
/* The dashboard half of the "no English left" scan (#89; tests/english_left.py, tests/test_english_left.py).
 *
 * Runs the real page under node with no browser: index.html as the dev server serves it, then its scripts (formats.js,
 * the catalog, charts.js, app.js) from the same server, on the small DOM in tests/mini_dom.js. app.js starts as it does
 * in the app (init: the first render and the first state), and each pass then works the page the way a person would: it
 * opens every page from the menu, presses the segment buttons (the windows, the metrics, the folder mode), and in some
 * passes every action button, every Settings control and the update banner's buttons. After each step it keeps the
 * page's title and body, once each. A pass is one fresh load of the page; it can rewrite what the server answers, to
 * show the page as an installed monitor (not a dev instance), with an update offered, with the reports still loading or
 * failing, or with every change refused:
 *
 *   installed       every page, with every segment, as an installed monitor shows them
 *   dev             every page as the dev server serves it (its dev-instance wording)
 *   actions         Settings, About and the sign-in help: every action button and every control, one at a time
 *   update          an update offered (opened in the browser): the banner on every page, then each of its buttons
 *   update-self     the same, with the monitor able to install it itself
 *   pending         every page while nothing it asks for besides the state has arrived (reports, themes, ...)
 *   reports-503     ... while the usage database is not ready (503)
 *   reports-501     ... in a build without statistics (501)
 *   reports-failed  ... when the request itself fails
 *   lookups-failed  every page when what it looks up besides the state and the reports fails (themes, supporters...)
 *   posts-failed    actions and the update banner when every change the page sends fails
 *   update-run:S    Settings while the monitor installs an update itself, in state S of the run (downloading, unknown,
 *                   connecting, checking, starting, restarting, stalled, or failed-CODE for each failure code): the
 *                   update status route answers that state, and Settings shows it at its first ask (#58)
 *   update-said:A   what each update button says (#97): a release on offer that the monitor installs itself, and every
 *                   POST to /api/update/<check|apply|skip> answered A (found, none, unchecked: the three results of each;
 *                   error, internal, unavailable, token, http: the monitor's refusals; lost: no answer; hang: none yet);
 *                   Settings' three update buttons and the banner's Update now and Skip this version are pressed, each on
 *                   a page opened afresh (in the hang pass the page is then opened again, as its next state redraws it)
 *   status:S        Overview and Limits with the state's status S (offline, rate_limited, paused, loading) and the detail
 *                   the data layer words for it (#98: the header's link word, the status banner, the health box's link row;
 *                   the detail is the data layer's English, #102's)
 *   link-lost       Overview and Limits when the monitor never answers the state (#98: the header and the health box say the
 *                   link is lost: a first poll that fails draws them)
 *   reports-stale   Overview and Limits with their reports in, then Refresh pressed while every report fails (#98: the
 *                   banner that says the figures are old)
 *   gates           Overview and Limits in the Gates theme (the state's theme and the shell's, as the server writes them):
 *                   the words app.js gives Gates alone (#98's page.gates.* keys)
 *
 *   node tests/english_scan.js <webui folder> <the page's URL, with its key> <now, in ms since 1970> <passes as JSON>
 *        [<the status details as JSON: {code: text}, for the status passes>]
 *
 * The page's clock stands still at <now> (the dev server's, so its times agree), its timers never fire (nothing is
 * left to a later tick), and english_left.py runs node with TZ pinned, so two runs give the same snapshots. motion.js
 * and the theme scripts are not run: they move and decorate what app.js draws, and the themes' boot texts are #101's.
 * Prints {shell, snapshots: [{pass, step, title, theme, body}], problems: [text]}; a problem is an error the page met (a
 * script or a listener that threw, a rejected promise, a console.error) or a step the scan could not take. Nothing
 * here reads a user's data: the dev server's data is made up.
 */
const vm = require("vm");
const D = require("./mini_dom.js");

const [, , webui, pageUrl, nowArg, passesArg, detailsArg] = process.argv;
const NOW = Number(nowArg);
if (!webui || !pageUrl || !Number.isFinite(NOW)) {
  throw new Error("usage: node tests/english_scan.js <webui folder> <page URL> <now in ms> <passes as JSON>");
}
const PASSES = JSON.parse(passesArg || '["installed"]');
const DETAILS = JSON.parse(detailsArg || "{}");          // each status pass's detail, as the data layer words it
const MINE = ["overview", "limits"];                     // the pages #98's passes open
const flags = { failing: false };                        // reports-stale: the reports fail from the moment this is set
const page = new URL(pageUrl);
const origin = page.origin;
const WIDTH = 640;
const UPDATE = { available: true, version: "9.9.9", url: "https://github.com/example/example/releases/tag/v9.9.9" };
const STAT_PAGES = ["overview", "limits", "cost", "efficiency", "history"];
// A run of the self-update (#58), as GET /api/update/status answers it: 4.2 of 21.0 MB of the release on offer, and what
// each update-run pass changes in it. A failed-CODE pass fails with that code, in the step it fails in.
const UPDATE_RUN = { phase: "downloading", version: UPDATE.version, done: 4404020, total: 22020096, age_s: 3, idle_s: 0,
  stalled: false, reason: null, failed_in: null, http_status: null, limit_mb: 512, timeout_s: 60 };
const UPDATE_RUNS = {
  downloading: {}, unknown: { total: null }, connecting: { done: 0, total: null },
  checking: { phase: "checking", done: 22020096 }, starting: { phase: "starting", done: 22020096 },
  restarting: { phase: "restarting", done: 22020096 }, stalled: { stalled: true, idle_s: 16 },
};
const FAILED_IN = { digest: "checking", helper: "starting" };
// What the monitor answers each update button in an update-said pass (#97), as ui/server.py does: the result of a check, an
// apply and a skip, or the refusal of each (ROUTE_STEP names the step a refusal names).
const ROUTE_STEP = { "/api/update/check": "check", "/api/update/apply": "apply", "/api/update/skip": "skip" };
const UPDATE_SAID = {
  found: { check: { available: true, version: UPDATE.version, checked: true }, apply: { started: true, mode: "browser" } },
  none: { check: { available: false, version: null, checked: true }, apply: { started: false, mode: "none" } },
  unchecked: { check: { available: false, version: null, checked: false }, apply: { started: true, mode: "installer" } },
  error: (step) => ({ status: 500, body: { error: "update " + step + " failed" } }),
  internal: () => ({ status: 500, body: { error: "internal error" } }),
  unavailable: (step) => ({ status: 501, body: { error: "update " + step + " is not available" } }),
  token: () => ({ status: 401, body: { error: "missing or wrong token" } }),
  http: () => ({ status: 502, body: null }),
};
function updateSaid(answer, api) {
  const step = ROUTE_STEP[api];
  if (answer === "lost") { return "fail"; }
  if (answer === "hang") { return "hang"; }
  const said = UPDATE_SAID[answer];
  if (typeof said === "function") { return said(step); }
  return { status: 200, body: step === "skip" ? { ok: true } : { ok: true, result: said[step] } };
}

function updateRun(state) {
  if (state.startsWith("failed-")) {
    const code = state.slice("failed-".length);
    return { ...UPDATE_RUN, phase: "failed", reason: code, failed_in: FAILED_IN[code] || "downloading",
      http_status: code === "http" ? 404 : code === "redirect" ? 302 : null };
  }
  return Object.prototype.hasOwnProperty.call(UPDATE_RUNS, state) ? { ...UPDATE_RUN, ...UPDATE_RUNS[state] } : null;
}

const problems = [];
const snapshots = [];
const kept = new Set();
let current = "";                      // the pass and step being taken, for the problems

const describe = (e) => String((e && e.stack) || e).split("\n").slice(0, 3).join(" | ");
process.on("unhandledRejection", (e) => problems.push(current + ": a promise was rejected: " + describe(e)));

// What a pass does to the server's answers: undefined passes one through; {rewrite: fn} changes its JSON; {status}
// answers in the server's place; "fail" makes the request fail; "hang" leaves it unanswered.
function rulesFor(pass) {
  const state = (patch) => ({ rewrite: (data) => { Object.assign(data, patch(data)); return data; } });
  const installed = { dev: false };
  return (url, method) => {
    const api = url.pathname;
    if (pass === "dev") {
      return undefined;
    }
    if (pass === "posts-failed" && method !== "GET") {
      return "fail";
    }
    if (pass.startsWith("status:") && api === "/api/state") {
      const code = pass.slice("status:".length);
      return state((data) => ({ dev: false, state: Object.assign(data.state, { status: code, status_detail: DETAILS[code] }) }));
    }
    if (pass === "link-lost" && api === "/api/state") {
      return "fail";
    }
    if (pass === "reports-stale" && api.startsWith("/api/analytics/") && flags.failing) {
      return "fail";
    }
    if (pass === "gates" && api === "/api/state") {
      return state((data) => ({ dev: false, theme: Object.assign(data.theme, { id: "gates", variant: "blue", mode: "dark" }),
        settings: Object.assign(data.settings, { theme_id: "gates", theme_variant: "blue", theme_mode: "dark" }) }));
    }
    if (pass.startsWith("update-said:")) {
      if (api === "/api/state") {
        return state(() => ({ dev: false, update: { ...UPDATE, can_self_update: true, info: { version: UPDATE.version, can_self_update: true } } }));
      }
      if (method === "POST" && ROUTE_STEP[api]) {
        return updateSaid(pass.slice("update-said:".length), api);
      }
    }
    if (pass.startsWith("update-run:")) {
      if (api === "/api/state") {
        // a release the monitor installs itself, with the check's info, so Settings has one on offer and asks
        return state(() => ({ dev: false, update: { ...UPDATE, can_self_update: true, info: { version: UPDATE.version, can_self_update: true } } }));
      }
      if (api === "/api/update/status") {
        return { status: 200, body: updateRun(pass.slice("update-run:".length)) };
      }
    }
    if (api === "/api/state") {
      if (pass === "update" || pass === "posts-failed") {
        return state(() => ({ dev: false, update: { ...UPDATE, can_self_update: false } }));
      }
      if (pass === "update-self") {
        return state(() => ({ dev: false, update: { ...UPDATE, can_self_update: true } }));
      }
      return state(() => installed);
    }
    if (pass === "lookups-failed" && method === "GET" && api.startsWith("/api/") && !api.startsWith("/api/analytics/")) {
      return "fail";
    }
    if (pass === "pending" && method === "GET" && api.startsWith("/api/")) {
      return "hang";
    }
    if (api.startsWith("/api/analytics/")) {
      if (pass === "reports-503") { return { status: 503, body: { error: "the usage database is not ready" } }; }
      if (pass === "reports-501") { return { status: 501, body: { error: "statistics are not in this build" } }; }
      if (pass === "reports-failed") { return "fail"; }
    }
    return undefined;
  };
}

function response(status, body, headers) {
  return {
    status, ok: status >= 200 && status < 300, headers: { get: (name) => (headers ? headers.get(name) : null) },
    text: () => Promise.resolve(body), json: () => Promise.resolve(JSON.parse(body)),
  };
}

function window_(doc, pass, waiting, reloads) {
  const rules = rulesFor(pass);
  const frames = [];
  const box = {
    console: {
      log() {}, info() {}, debug() {},
      warn: (...a) => problems.push(current + ": console.warn: " + a.map(String).join(" ")),
      error: (...a) => problems.push(current + ": console.error: " + a.map((x) => (x && x.stack) || String(x)).join(" ")),
    },
    document: doc, Event: D.Event, CustomEvent: D.CustomEvent, name: "",
    location: {
      href: page.href.split("#")[0] + "#overview", origin, protocol: page.protocol, host: page.host, pathname: page.pathname,
      search: page.search, hash: "#overview", reload: () => { reloads.count += 1; }, assign() {}, replace() {},
    },
    history: { replaceState: (_s, _t, url) => { if (typeof url === "string" && url[0] === "#") { box.location.hash = url; } }, pushState() {} },
    navigator: { userAgent: "node (tests/english_scan.js)", clipboard: { writeText: () => Promise.resolve() } },
    matchMedia: (media) => ({ media, matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }),
    getComputedStyle: () => ({ getPropertyValue: () => "" }),
    requestAnimationFrame: (fn) => { frames.push(fn); return frames.length; },
    cancelAnimationFrame() {}, setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
    open: () => null, scrollTo() {}, focus() {}, addEventListener() {}, removeEventListener() {},
    __frames: frames,
  };
  // The window takes part in events (a click bubbles up to it), like the document's own nodes.
  Object.setPrototypeOf(box, D.Target.prototype);
  delete box.addEventListener;
  delete box.removeEventListener;
  box.window = box;
  box.self = box;
  doc.defaultView = box;
  box.fetch = (url, options) => {
    const target = new URL(String(url), page.href);
    const method = String((options && options.method) || "GET").toUpperCase();
    const rule = rules(target, method);
    if (rule === "hang") {
      return new Promise(() => {});                    // never answered: the page waits, and so the scan does not
    }
    waiting.count += 1;
    const done = (value) => { waiting.count -= 1; return value; };
    const failed = (e) => { waiting.count -= 1; throw e; };
    if (rule === "fail") {
      return Promise.reject(new TypeError("Failed to fetch")).then(done, failed);
    }
    if (rule && rule.status) {
      return Promise.resolve(response(rule.status, JSON.stringify(rule.body))).then(done, failed);
    }
    return fetch(target, options).then(async (res) => {
      let body = await res.text();                    // read here, so waiting counts until the page has the body
      if (rule && rule.rewrite && res.ok && body) {
        body = JSON.stringify(rule.rewrite(JSON.parse(body)));
      }
      return response(res.status, body, res.headers);
    }).then(done, failed);
  };
  return box;
}

async function settle(box, waiting) {
  for (let idle = 0, round = 0; idle < 3; round++) {
    if (round > 20000) {
      problems.push(current + ": the page did not settle (requests still out: " + waiting.count + ")");
      return;
    }
    await new Promise((resolve) => setImmediate(resolve));
    const frames = box.__frames.splice(0);
    frames.forEach((fn) => {
      try { fn(NOW); } catch (e) { problems.push(current + ": an animation frame threw: " + describe(e)); }
    });
    if (waiting.count || frames.length) {
      idle = 0;
      await new Promise((resolve) => setTimeout(resolve, 1));
    } else {
      idle += 1;
    }
  }
}

function keep(doc, pass, step) {
  const body = doc.body ? doc.body.outerHTML : "";
  if (!kept.has(body)) {
    kept.add(body);
    snapshots.push({ pass, step, title: doc.title, theme: doc.documentElement.getAttribute("data-theme"), body });
  }
}

async function load(pass) {
  current = pass + "/load";
  let shell = await (await fetch(page.href)).text();
  if (pass === "gates") {
    // the shell as the server writes it for the Gates theme (ui/server.py's _themed_shell): the theme on <html>
    shell = shell.replace(/<html\b[^>]*>/, (tag) => tag.replace(/\sdata-theme="[^"]*"/, ' data-theme="gates"'));
  }
  const doc = D.createDocument(shell, {
    width: WIDTH, report: (e, event) => problems.push(current + ": a " + event.type + " listener threw: " + describe(e)),
  });
  const waiting = { count: 0 };
  const reloads = { count: 0 };
  const box = window_(doc, pass, waiting, reloads);
  vm.createContext(box);
  vm.runInContext("(function (now) { var Real = Date;" +
    " class Still extends Real { constructor(...a) { super(...(a.length ? a : [now])); } static now() { return now; } }" +
    " globalThis.Date = Still; })(" + NOW + ");", box, { filename: "still-clock.js" });
  for (const script of D.createDocument(shell).querySelectorAll("script[src]")) {
    const src = script.getAttribute("src");
    if (/\/motion\.js$/.test(src)) {
      continue;
    }
    const code = await (await fetch(new URL(src, page.href))).text();
    try {
      vm.runInContext(code, box, { filename: src });
    } catch (e) {
      problems.push(current + ": " + src + " threw: " + describe(e));
    }
  }
  await settle(box, waiting);
  return { shell, doc, box, waiting, reloads };
}

const pageKeys = (doc) => doc.querySelectorAll("#menu a[data-page]").map((a) => a.getAttribute("data-page"));

async function open(view, pass, key) {
  current = pass + "/" + key;
  view.box.location.hash = "#" + key;
  view.box._fire(new D.Event("hashchange"), (e) => problems.push(current + ": hashchange threw: " + describe(e)));
  await settle(view.box, view.waiting);
  keep(view.doc, pass, key);
}

async function press(view, pass, step, el) {
  current = pass + "/" + step;
  el.click();
  if (el.localName === "input" && (el.type === "checkbox" || el.type === "radio")) {
    el.dispatchEvent(new D.Event("change", { bubbles: true }));
  }
  await settle(view.box, view.waiting);
  keep(view.doc, pass, step);
}

async function change(view, pass, step, el, value) {
  current = pass + "/" + step;
  el.value = value;
  el.dispatchEvent(new D.Event("input", { bubbles: true }));
  el.dispatchEvent(new D.Event("change", { bubbles: true }));
  await settle(view.box, view.waiting);
  keep(view.doc, pass, step);
}

const label = (el) => el.getAttribute("data-action") || el.getAttribute("data-seg") + "=" + el.getAttribute("data-value");

async function everyPage(pass, segments) {
  const view = await load(pass);
  keep(view.doc, pass, "load");
  for (const key of pageKeys(view.doc)) {
    await open(view, pass, key);
    if (!segments) {
      continue;
    }
    const seen = new Set();
    for (;;) {
      const next = view.doc.querySelectorAll("#main button[data-seg], #main button[data-cmp]")
        .find((b) => !seen.has(label(b) + "|" + b.getAttribute("data-cmp")));
      if (!next) {
        break;
      }
      seen.add(label(next) + "|" + next.getAttribute("data-cmp"));
      await press(view, pass, key + ":" + label(next), next);
    }
  }
}

// Every action button and control of a page, one at a time, each on a page opened afresh (a button can change what
// the next one does: skip the update, remove the setup). Applying a language reloads the page, so it is left out.
async function everyAction(pass, key, scope) {
  const probe = await load(pass);
  await open(probe, pass, key);
  const actions = probe.doc.querySelectorAll(scope + " button[data-action], " + scope + " a[data-action]")
    .map(label).filter((a) => a !== "language-apply");
  for (const action of [...new Set(actions)]) {
    const view = await load(pass);
    await open(view, pass, key);
    const el = view.doc.querySelectorAll(scope + " [data-action]").find((b) => label(b) === action);
    if (el) {
      await press(view, pass, key + ":" + action, el);
    } else {
      problems.push(pass + "/" + key + ": the " + action + " button went away when the page was opened again");
    }
  }
  if (key !== "settings") {
    return;
  }
  const view = await load(pass);
  await open(view, pass, key);
  for (const el of view.doc.querySelectorAll("#main [data-key]")) {
    const name = el.getAttribute("data-key");
    if (el.localName === "input" && el.type === "checkbox") {
      await press(view, pass, key + ":" + name, el);
      await press(view, pass, key + ":" + name + " again", el);
    } else if (el.localName === "select") {
      const was = el.value;
      for (const option of el.options.map((o) => o.value)) {
        await change(view, pass, key + ":" + name + "=" + option, el, option);
      }
      await change(view, pass, key + ":" + name + "=" + was, el, was);
    } else if (el.localName === "input" || el.localName === "textarea") {
      const was = el.value;
      await change(view, pass, key + ":" + name + " (not a value)", el, "x?");
      await change(view, pass, key + ":" + name + " (cleared)", el, "");
      await change(view, pass, key + ":" + name + "=" + was, el, was);
    }
  }
}

(async () => {
  for (const pass of PASSES) {
    try {
      if (pass === "installed") {
        await everyPage(pass, true);
      } else if (pass === "dev" || pass === "lookups-failed" || pass === "pending") {
        await everyPage(pass, false);
      } else if (pass === "actions") {
        await everyAction(pass, "settings", "#main");
        await everyAction(pass, "about", "#main");
        await everyAction(pass, "overview", "#main .nm-help");
      } else if (pass === "update" || pass === "update-self") {
        await everyPage(pass, false);
        await everyAction(pass, "overview", "#main .nm-update");
        await everyAction(pass, "about", "#main");
      } else if (pass.startsWith("status:") || pass === "link-lost" || pass === "gates") {
        if (pass.startsWith("status:") && !DETAILS[pass.slice("status:".length)]) {
          problems.push("no detail for " + pass);
          continue;
        }
        const view = await load(pass);
        keep(view.doc, pass, "load");
        for (const key of MINE) {
          await open(view, pass, key);
        }
      } else if (pass === "reports-stale") {
        flags.failing = false;
        const view = await load(pass);
        for (const key of MINE) {
          await open(view, pass, key);
        }
        flags.failing = true;
        await press(view, pass, "limits:refresh", view.doc.getElementById("refresh"));
        await open(view, pass, "overview");
        flags.failing = false;
      } else if (pass.startsWith("reports-")) {
        const view = await load(pass);
        for (const key of STAT_PAGES) {
          await open(view, pass, key);
        }
      } else if (pass.startsWith("update-said:")) {
        const answer = pass.slice("update-said:".length);
        if (!Object.prototype.hasOwnProperty.call(UPDATE_SAID, answer) && answer !== "lost" && answer !== "hang") {
          problems.push("no such update answer: " + pass);
          continue;
        }
        for (const [key, scope, actions] of [["settings", "#main", ["check-update", "install-update", "skip-update"]],
          ["overview", "#main .nm-update", ["update-now", "update-skip"]]]) {
          for (const action of actions) {
            const view = await load(pass);
            await open(view, pass, key);
            const el = view.doc.querySelectorAll(scope + " [data-action]").find((b) => label(b) === action);
            if (!el) {
              problems.push(pass + "/" + key + ": no " + action + " button");
              continue;
            }
            await press(view, pass, key + ":" + action, el);
            if (answer === "hang") {
              // a check that waits shows its line at the page's next draw (the next state, in 2 s): the page opened again
              current = pass + "/" + key + ":" + action + " again";
              view.box._fire(new D.Event("hashchange"), (e) => problems.push(current + ": hashchange threw: " + describe(e)));
              await settle(view.box, view.waiting);
              keep(view.doc, pass, key + ":" + action + " again");
            }
          }
        }
      } else if (pass.startsWith("update-run:")) {
        if (!updateRun(pass.slice("update-run:".length))) {
          problems.push("no such update run: " + pass);
          continue;
        }
        await open(await load(pass), pass, "settings");
      } else if (pass === "posts-failed") {
        await everyAction(pass, "settings", "#main");
        await everyAction(pass, "about", "#main");
        await everyAction(pass, "overview", "#main .nm-update");
        await everyAction(pass, "overview", "#main .nm-help");
      } else {
        problems.push("no such pass: " + pass);
      }
    } catch (e) {
      problems.push(current + ": the scan stopped: " + describe(e));
    }
  }
  const shell = await (await fetch(page.href)).text();
  process.stdout.write(JSON.stringify({ shell, snapshots, problems }));
})().catch((e) => {
  console.error(e && e.stack ? e.stack : e);
  process.exit(1);
});
