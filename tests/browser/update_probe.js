// Real-browser check of a running update on Settings (#58): the block's steps, bar, sizes and status line in every look the
// owner checks, with and without reduced motion, and a screenshot of the System card in every state of a run.
//
// Not part of the pytest run (it needs Playwright for node and a Chromium). It starts the dev server itself with its update
// demo (--update-demo: a made-up release 9.9.9 and a made-up run; it never calls the updater, the network or an exit), on a
// throwaway settings folder (USAGE_MONITOR_DEV_INSTANCE=1, USAGE_MONITOR_APPDATA_DIR under --data: never your real settings or
// data). Every request the page makes goes to that server on 127.0.0.1.
//
//   NODE_PATH=<folder with node_modules/playwright> node tests/browser/update_probe.js [options]
//
//   --repo <dir>        the checkout to run (default: this one)
//   --out <dir>         where the screenshots and update_probe.json go (default: a new temp folder)
//   --data <dir>        where the dev servers keep their settings (default: a new temp folder)
//   --looks a,b         nostromo,night_city,edgerun,gates-dark,gates-light by default
//   --states a,b        the states to show (default: all of STATES below)
//   --langs a,b         the languages shot at a narrow width as well (default fr,pseudo; "none" skips them)
//   --narrow <px>       that width (default 640)
//   --skip-live         leave out the live runs of the demo
//   CHROMIUM_PATH       a Chromium to launch instead of Playwright's own
//
// What it does, per look, without and with reduced motion:
//   1. Live: a dev server of its own runs the demo. Install update; the cells light as the demo moves (never fewer than the
//      ask before), the steps go on to Restart and Install update is busy all the while. Then the server stops, as the monitor
//      does when it closes for the installer, and the block says so ("gone").
//   2. Every state of a run, one after the other: GET /api/update/status is answered by the probe with that state (lost and
//      gone: a running state, then no answer), and the System card is shot. For each it checks the steps; the case (chips and
//      status line in capitals in Nostromo and Night City, as written in Gates; the sizes line as written everywhere, owner
//      question 3); that the three steps' computed colours differ (now, next and done); that nothing runs in the block under
//      reduced motion; and that nothing in it is wider than the card.
//   3. The same states (a few) in French and in the pseudo-locale at a narrow width, where the chips must wrap, not overflow.
// Exit code 1 and "VERDICT: FAIL" when any check fails.
'use strict';
const { chromium } = require('playwright');
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const args = process.argv.slice(2);
const opt = (name, def) => { const i = args.indexOf('--' + name); return i >= 0 ? args[i + 1] : def; };
const REPO = path.resolve(opt('repo', path.join(__dirname, '..', '..')));
const OUT = path.resolve(opt('out', '') || fs.mkdtempSync(path.join(os.tmpdir(), 'usage-monitor-update-probe-')));
const DATA = path.resolve(opt('data', '') || fs.mkdtempSync(path.join(os.tmpdir(), 'usage-monitor-update-data-')));
const LOOKS = opt('looks', 'nostromo,night_city,edgerun,gates-dark,gates-light').split(',');
const LANGS = opt('langs', 'fr,pseudo').split(',').filter((l) => l && l !== 'none');
const NARROW = +opt('narrow', '640');
const SKIP_LIVE = args.includes('--skip-live');
fs.mkdirSync(OUT, { recursive: true });
fs.mkdirSync(DATA, { recursive: true });

const LOOK = {
  nostromo: { settings: { theme_id: 'nostromo', theme_variant: 'amber' }, caps: true },
  night_city: { settings: { theme_id: 'night_city', theme_variant: 'blackwall' }, caps: true },
  edgerun: { settings: { theme_id: 'night_city', theme_variant: 'edgerun' }, caps: true },
  'gates-dark': { settings: { theme_id: 'gates', theme_variant: 'blue', theme_mode: 'dark' }, caps: false },
  'gates-light': { settings: { theme_id: 'gates', theme_variant: 'blue', theme_mode: 'light' }, caps: false },
};

// The records the probe answers, shaped as GET /api/update/status answers them; all made up. 4.2 of 21.0 MB of 9.9.9.
const RUN = { phase: 'downloading', version: '9.9.9', done: 4404020, total: 22020096, age_s: 3, idle_s: 0, stalled: false,
  reason: null, failed_in: null, http_status: null, limit_mb: 512, timeout_s: 60 };
const IDLE = { ...RUN, phase: 'idle', version: null, done: 0, total: null, age_s: 0 };
const CODES = ['timeout', 'network', 'http', 'redirect', 'too_large', 'cut_short', 'digest', 'rejected', 'helper', 'local', 'error'];
const FAILED_IN = { digest: 'checking', helper: 'starting' };
const STATES = {
  downloading: { record: {}, steps: ['now', 'next', 'next'] },
  unknown: { record: { total: null }, steps: ['now', 'next', 'next'], pending: true },
  connecting: { record: { done: 0, total: null }, steps: ['now', 'next', 'next'], pending: true },
  checking: { record: { phase: 'checking', done: 22020096 }, steps: ['done', 'now', 'next'] },
  starting: { record: { phase: 'starting', done: 22020096 }, steps: ['done', 'done', 'now'] },
  restarting: { record: { phase: 'restarting', done: 22020096 }, steps: ['done', 'done', 'now'] },
  stalled: { record: { done: 7759462, stalled: true, idle_s: 16 }, steps: ['stalled', 'next', 'next'], tone: 'is-warn' },
};
for (const code of CODES) {
  const failedIn = FAILED_IN[code] || 'downloading';
  STATES['failed-' + code] = {
    record: { phase: 'failed', reason: code, failed_in: failedIn, done: failedIn === 'downloading' ? 7759462 : 22020096,
      http_status: code === 'http' ? 404 : code === 'redirect' ? 302 : null, version: code === 'rejected' ? null : '9.9.9' },
    steps: failedIn === 'checking' ? ['done', 'failed', 'next'] : failedIn === 'starting' ? ['done', 'done', 'failed'] : ['failed', 'next', 'next'],
    tone: 'is-crit',
  };
}
STATES.lost = { record: {}, then: 'lost', steps: ['failed', 'next', 'next'], tone: 'is-crit' };
STATES.gone = { record: { phase: 'restarting', done: 22020096 }, then: 'lost', steps: ['done', 'done', 'now'] };
const ALL_STATES = Object.keys(STATES);
const SHOWN = opt('states', '') ? opt('states', '').split(',') : ALL_STATES;
const NARROW_STATES = ['downloading', 'stalled', 'failed-redirect', 'failed-local', 'gone'];

const failures = [];
const fail = (msg) => { failures.push(msg); console.log('FAIL', msg); };

let serial = 0;
function startServer(extra) {
  return new Promise((resolve, reject) => {
    const data = path.join(DATA, 'server-' + (++serial));
    fs.mkdirSync(data, { recursive: true });
    const env = Object.assign({}, process.env, { USAGE_MONITOR_DEV_INSTANCE: '1', USAGE_MONITOR_APPDATA_DIR: data, PYTHONUNBUFFERED: '1' });
    const child = spawn(process.env.PYTHON || 'python', ['-m', 'usage_monitor.ui.devserver', '--port', '0', '--scenario', 'normal', ...extra],
      { cwd: REPO, env, windowsHide: true });
    let buf = '';
    const timer = setTimeout(() => { child.kill(); reject(new Error('dev server did not print its address: ' + buf)); }, 60000);
    child.stdout.on('data', (d) => {
      buf += d.toString();
      const m = /url=(\S+)/.exec(buf);
      if (m) { clearTimeout(timer); resolve({ child, url: m[1].replace(/#.*$/, '') }); }
    });
    child.stderr.on('data', (d) => { buf += d.toString(); });
    child.on('exit', (code) => { clearTimeout(timer); reject(new Error('dev server exited early (' + code + '): ' + buf)); });
  });
}

function stopServer(server) {
  return new Promise((resolve) => {
    if (server.child.exitCode !== null || server.child.signalCode !== null) { resolve(); return; }   // gone already (killed: signalCode)
    server.child.removeAllListeners('exit');
    server.child.once('exit', () => resolve());
    server.child.kill();
  });
}

async function openSettings(browser, server, look, opts) {
  const context = await browser.newContext({ viewport: { width: opts.width || 1280, height: 1000 }, deviceScaleFactor: 1,
    reducedMotion: opts.reduced ? 'reduce' : 'no-preference' });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource|ERR_FAILED|ERR_CONNECTION_REFUSED/.test(m.text())) errors.push(m.text()); });
  if (opts.route) {
    await page.route('**/api/update/status', opts.route);
  }
  await page.goto(server.url + '#settings', { waitUntil: 'load', timeout: 30000 });
  await page.waitForSelector('section[data-card="system"]', { timeout: 30000 });
  const token = await page.evaluate(() => document.querySelector('meta[name=monitor-token]').content);
  await page.evaluate(async ([settings, tok]) => {
    await fetch('/api/settings', { method: 'POST', headers: { 'X-Monitor-Token': tok, 'Content-Type': 'application/json' },
      body: JSON.stringify(settings) });
  }, [LOOK[look].settings, token]);
  await page.waitForFunction((id) => document.documentElement.getAttribute('data-theme') === id, LOOK[look].settings.theme_id, { timeout: 15000 });
  await page.waitForSelector('[data-action="install-update"]:not([hidden])', { timeout: 30000 });
  await page.waitForTimeout(opts.reduced ? 1500 : 4500);       // the next state, the page redrawn, a boot and an entrance finished
  return { context, page, errors };
}

// What the block shows, read from the page.
const READ = () => {
  const box = document.getElementById('set-update-progress');
  const cs = (el, pseudo) => getComputedStyle(el, pseudo || null);
  const chips = [...box.querySelectorAll('.nm-chip')];
  const cells = box.querySelector('.nm-cells');
  const story = box.querySelector('.nm-story');
  const line = box.querySelector('[data-part="line"]');
  const verdict = box.querySelector('.nm-verdict');
  const card = box.closest('.nm-card');
  const cardRight = card ? card.getBoundingClientRect().right : Infinity;
  const running = box.getAnimations ? box.getAnimations({ subtree: true }).filter((a) => a.playState === 'running')
    .map((a) => a.animationName || a.transitionProperty || 'animation') : [];
  return {
    hidden: box.hidden, cls: box.className,
    steps: chips.map((c) => c.getAttribute('data-step')),
    chipText: chips.map((c) => c.innerText.trim()),
    chipLook: chips.map((c) => { const s = cs(c); return { color: s.color, bg: s.backgroundColor, border: s.borderTopColor, opacity: s.opacity, oc: s.getPropertyValue('--oc').trim() }; }),
    story: story ? story.innerText.trim() : null, storyTransform: story ? cs(story).textTransform : null,
    line: line ? line.innerText.trim() : null, lineErr: line ? line.classList.contains('is-err') : null,
    pending: cells ? cells.hasAttribute('data-pending') : null, fxPend: cells ? cells.getAttribute('data-fx-pend') : null,
    afterAnimation: cells ? cs(cells, '::after').animationName : null,
    lit: cells ? cells.querySelectorAll('i.on').length : null,
    cellOn: cells && cells.querySelector('i.on') ? cs(cells.querySelector('i.on')).backgroundColor : null,
    statusHidden: document.getElementById('set-update-status').hidden,
    install: document.querySelector('[data-action="install-update"]').getAttribute('aria-disabled'),
    running,
    overflow: !!(verdict && (verdict.scrollWidth > verdict.clientWidth + 1 || chips.some((c) => c.getBoundingClientRect().right > cardRight + 1))) ||
      box.scrollWidth > box.clientWidth + 1,
  };
};

const upper = (text) => text === text.toUpperCase() && /[A-ZÀ-ɏ]/.test(text);

function judge(where, look, state, info, reduced) {
  const want = STATES[state];
  if (info.hidden) { fail(`${where}: the block is hidden`); return; }
  if (JSON.stringify(info.steps) !== JSON.stringify(want.steps)) fail(`${where}: steps ${info.steps} (want ${want.steps})`);
  const tone = info.cls.replace('nm-uprog', '').trim();
  if (tone !== (want.tone || '')) fail(`${where}: tone "${tone}" (want "${want.tone || ''}")`);
  if (LOOK[look].caps) {
    if (!info.chipText.every(upper)) fail(`${where}: chips not in capitals: ${info.chipText}`);
    if (!upper(info.line)) fail(`${where}: status line not in capitals: ${info.line}`);
  } else if (info.chipText.some(upper) || upper(info.line)) {
    fail(`${where}: Gates shows capitals: ${info.chipText} / ${info.line}`);
  }
  if (info.storyTransform !== 'none') fail(`${where}: the sizes line is transformed (${info.storyTransform})`);
  if (!!want.pending !== info.pending) fail(`${where}: pending ${info.pending}`);
  if (reduced && (info.running.length || (info.afterAnimation && info.afterAnimation !== 'none'))) {
    fail(`${where}: moves under reduced motion: ${info.running} ${info.afterAnimation}`);
  }
  if (info.overflow) fail(`${where}: something in the block is wider than its card`);
  if (/^failed-/.test(state) && !info.lineErr) fail(`${where}: a failure's line is not in the error tone`);
  if (!info.statusHidden) fail(`${where}: the line under the buttons shows beside the block`);
}

// The three steps told apart: the computed colour of a done, a current and a next chip all differ (critic 1 on #58).
function judgeColours(where, info) {
  const by = {};
  info.steps.forEach((s, i) => { by[s] = info.chipLook[i]; });
  const pairs = [['now', 'next'], ['now', 'done'], ['next', 'done']];
  for (const [a, b] of pairs) {
    if (!by[a] || !by[b]) { fail(`${where}: no ${a} or ${b} chip to compare`); continue; }
    if (by[a].color === by[b].color) fail(`${where}: ${a} and ${b} chips have the same colour ${by[a].color}`);
  }
}

// The block's steps, tone and status line, for waiting on a change.
const SHOWS = () => {
  const box = document.getElementById('set-update-progress');
  const line = box.querySelector('[data-part="line"]');
  return { hidden: box.hidden, tone: box.className.replace('nm-uprog', '').trim(), line: line ? line.getAttribute('data-sig') : '',
    steps: [...box.querySelectorAll('.nm-chip')].map((c) => c.getAttribute('data-step')) };
};

async function showState(page, setAnswer, state, reduced) {
  const want = STATES[state];
  // until the block shows these steps in this tone, and (after "no answer") a status line other than the one it had
  const until = (steps, tone, notLine) => page.waitForFunction(([w, t, l]) => {
    const box = document.getElementById('set-update-progress');
    const line = box.querySelector('[data-part="line"]');
    const steps = [...box.querySelectorAll('.nm-chip')].map((c) => c.getAttribute('data-step'));
    return !box.hidden && JSON.stringify(steps) === JSON.stringify(w) && box.className.replace('nm-uprog', '').trim() === t &&
      (l === null || (line && line.getAttribute('data-sig') !== l));
  }, [steps, tone, notLine], { timeout: 12000 });
  setAnswer({ ...RUN, ...want.record });
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));   // the page asks at once
  let before = null;
  if (want.then === 'lost') {
    await until(want.record.phase === 'restarting' ? ['done', 'done', 'now'] : ['now', 'next', 'next'], '', null);
    before = (await page.evaluate(SHOWS)).line;
    setAnswer('lost');                                        // three asks with no answer
  }
  await until(want.steps, want.tone || '', before);
  // the theme's re-tone of a new chip (Gates), and with motion the bar's arrival (a bar that stops being pending) played out
  await page.waitForTimeout(reduced ? 350 : 1100);
}

async function routedLook(browser, server, look, reduced, states, prefix, width) {
  let answer = IDLE;
  const route = (r) => (answer === 'lost' ? r.abort() : r.fulfill({ status: 200, contentType: 'application/json',
    headers: { 'Cache-Control': 'no-store' }, body: JSON.stringify(answer) }));
  const { context, page, errors } = await openSettings(browser, server, look, { reduced, width, route });
  const card = page.locator('section[data-card="system"]');
  const result = {};
  for (const state of states) {
    const where = `${prefix}${look}${reduced ? '-reduced' : ''}/${state}`;
    try {
      await showState(page, (a) => { answer = a; }, state, reduced);
      const info = await page.evaluate(READ);
      result[state] = info;
      judge(where, look, state, info, reduced);
      if (state === 'checking') judgeColours(where, info);
      await card.screenshot({ path: path.join(OUT, `${prefix}${look}${reduced ? '-reduced' : ''}-${state}.png`) });
    } catch (e) {
      fail(`${where}: ${String(e).split('\n')[0]}`);
    }
    if (state === 'gone' || state === 'lost') {
      answer = IDLE;                                          // a fresh page for what follows: lost and gone change what is asked
      await page.reload({ waitUntil: 'load' });
      await page.waitForSelector('[data-action="install-update"]:not([hidden])', { timeout: 30000 });
      await page.waitForTimeout(reduced ? 800 : 2500);
    }
  }
  if (errors.length) fail(`${prefix}${look}${reduced ? '-reduced' : ''}: page errors: ${errors.slice(0, 3).join(' | ')}`);
  await context.close();
  return result;
}

async function liveLook(browser, look, reduced) {
  const where = `live/${look}${reduced ? '-reduced' : ''}`;
  const server = await startServer(['--update-demo', 'ok']);
  const out = { samples: [] };
  let context = null;
  try {
    const opened = await openSettings(browser, server, look, { reduced });
    context = opened.context;
    const page = opened.page;
    await page.click('[data-action="install-update"]');
    const t0 = Date.now();
    for (;;) {
      await page.waitForTimeout(400);
      const info = await page.evaluate(READ);
      out.samples.push({ t: Date.now() - t0, steps: info.steps, lit: info.lit, story: info.story, install: info.install, pending: info.pending });
      if (JSON.stringify(info.steps) === JSON.stringify(['done', 'done', 'now']) || Date.now() - t0 > 25000) break;
    }
    await page.waitForTimeout(1000);
    const card = page.locator('section[data-card="system"]');
    out.restarting = await page.evaluate(READ);
    await card.screenshot({ path: path.join(OUT, `live-${look}${reduced ? '-reduced' : ''}-restarting.png`) });
    const lits = out.samples.map((s) => s.lit || 0);
    if (!lits.every((n, i) => i === 0 || n >= lits[i - 1])) fail(`${where}: the cells went back: ${lits}`);
    if (!lits.some((n) => n > 0 && n < 32)) fail(`${where}: no sample between empty and full: ${lits}`);
    if (out.restarting.lit !== 32) fail(`${where}: the bar is not full at the restart`);
    if (JSON.stringify(out.restarting.steps) !== JSON.stringify(['done', 'done', 'now'])) fail(`${where}: the restart was not reached: ${out.restarting.steps}`);
    if (out.samples.some((s) => s.steps.length && s.install !== 'true')) fail(`${where}: Install update was not busy during the run`);
    if (reduced && out.restarting.running.length) fail(`${where}: moves under reduced motion: ${out.restarting.running}`);
    if (LOOK[look].caps ? !out.restarting.chipText.every(upper) : out.restarting.chipText.some(upper)) fail(`${where}: chip case ${out.restarting.chipText}`);
    await stopServer(server);                                 // the monitor closes for the installer
    await page.waitForFunction(() => /tray/i.test((document.querySelector('#set-update-progress [data-part="line"]') || {}).textContent || ''), null, { timeout: 15000 })
      .catch(() => fail(`${where}: the block did not say the monitor is closing`));
    out.gone = await page.evaluate(READ);
    await card.screenshot({ path: path.join(OUT, `live-${look}${reduced ? '-reduced' : ''}-gone.png`) });
    if (opened.errors.length) fail(`${where}: page errors: ${opened.errors.slice(0, 3).join(' | ')}`);
  } catch (e) {
    fail(`${where}: ${String(e).split('\n')[0]}`);
  } finally {
    if (context) await context.close();
    await stopServer(server);
  }
  return out;
}

(async () => {
  const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
  const result = { out: OUT, routed: {}, narrow: {}, live: {} };
  const ordered = SHOWN.filter((s) => s !== 'gone').concat(SHOWN.includes('gone') ? ['gone'] : []);
  try {
    const server = await startServer(['--update-demo', 'ok']);
    try {
      for (const look of LOOKS) {
        for (const reduced of [false, true]) {
          result.routed[look + (reduced ? '-reduced' : '')] = await routedLook(browser, server, look, reduced, ordered, '', 1280);
          console.log('routed', look, reduced ? 'reduced' : '', 'done');
        }
      }
    } finally {
      await stopServer(server);
    }
    for (const lang of LANGS) {
      const local = await startServer(['--update-demo', 'ok', '--lang', lang]);
      try {
        for (const look of LOOKS) {
          result.narrow[lang + '/' + look] = await routedLook(browser, local, look, false, NARROW_STATES, lang + '-narrow-', NARROW);
          console.log('narrow', lang, look, 'done');
        }
      } finally {
        await stopServer(local);
      }
    }
    if (!SKIP_LIVE) {
      for (const look of LOOKS) {
        for (const reduced of [false, true]) {
          result.live[look + (reduced ? '-reduced' : '')] = await liveLook(browser, look, reduced);
          console.log('live', look, reduced ? 'reduced' : '', 'done');
        }
      }
    }
  } finally {
    await browser.close();
  }
  result.failures = failures;
  fs.writeFileSync(path.join(OUT, 'update_probe.json'), JSON.stringify(result, null, 1));
  console.log('screens and update_probe.json in ' + OUT);
  console.log(failures.length ? 'VERDICT: FAIL (' + failures.length + ')' : 'VERDICT: PASS');
  if (failures.length) process.exitCode = 1;
})().catch((e) => { console.error(e); process.exit(1); });
