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
//   --looks a,b         nostromo,night_city,edgerun,gates-dark,gates-light by default ("none": no routed or live run)
//   --states a,b        the states to show (default: all of STATES below; "none": none)
//   --langs a,b         the languages shot at a narrow width as well (default fr,pseudo; "none" skips them)
//   --narrow <px>       that width (default 640)
//   --skip-live         leave out the live runs of the demo
//   --skip-variants     leave out the pass over every variant (step 4)
//   --variants a,b      only these of step 4's variants (their names in VARIANTS)
//   CHROMIUM_PATH       a Chromium to launch instead of Playwright's own
//
// What it does, per look, without and with reduced motion:
//   1. Live: a dev server of its own runs the demo. Install update; the cells light as the demo moves (never fewer than the
//      ask before), the steps go on to Restart and Install update is busy all the while; once the size is in, the bar is no
//      longer the pending one. Then the server stops, as the monitor does when it closes for the installer, and the block says
//      so ("gone").
//   2. Every state of a run, one after the other: GET /api/update/status is answered by the probe with that state (lost and
//      gone: a running state, then no answer), and the System card is shot. For each it checks the steps; the case (chips and
//      status line in capitals in Nostromo and Night City, as written in Gates; the sizes line as written everywhere, owner
//      question 3); that the three steps look different (now, next and done: their colour and opacity); the pending bar and
//      the bar's figure for a screen reader (aria-valuenow exactly when the bar is not pending); the hot bar of a failure or a
//      lost link; that nothing runs in the block under reduced motion, nor in a stalled bar of unknown size with motion on;
//      that a failure or a lost link before the first byte leaves the block as high as "connecting"; and that nothing in it
//      is wider than the card.
//   3. The same states (a few) in French and in the pseudo-locale at a narrow width, where the chips must wrap, not overflow.
//   4. Every variant of every theme (VARIANTS): the next step's contrast, measured from a screenshot as a disabled control's
//      is, stays within NEXT_TOLERANCE of the disabled Install update button beside it; and in Nostromo a stalled download and
//      a failed one differ by more than their fill (in a monochrome variant the two tones are one colour).
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
const LOOKS = opt('looks', 'nostromo,night_city,edgerun,gates-dark,gates-light').split(',').filter((l) => l && l !== 'none');
const LANGS = opt('langs', 'fr,pseudo').split(',').filter((l) => l && l !== 'none');
const NARROW = +opt('narrow', '640');
const SKIP_LIVE = args.includes('--skip-live');
const SKIP_VARIANTS = args.includes('--skip-variants');
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
  // no size and nothing arriving: the pending bar holds still, motion or not
  'stalled-unknown': { record: { done: 4404020, total: null, stalled: true, idle_s: 16 }, steps: ['stalled', 'next', 'next'], tone: 'is-warn',
    pending: true, still: true },
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
// a failure before the first byte (offline, an HTTP error, a refused redirect): the record keeps 0 bytes of no known size
STATES['failed-early'] = { record: { phase: 'failed', reason: 'network', failed_in: 'downloading', done: 0, total: null },
  steps: ['failed', 'next', 'next'], tone: 'is-crit', high: 'connecting' };
STATES.lost = { record: {}, then: 'lost', steps: ['failed', 'next', 'next'], tone: 'is-crit' };
STATES['lost-early'] = { record: { done: 0, total: null }, then: 'lost', steps: ['failed', 'next', 'next'], tone: 'is-crit', high: 'connecting' };
STATES.gone = { record: { phase: 'restarting', done: 22020096 }, then: 'lost', steps: ['done', 'done', 'now'] };
const ALL_STATES = Object.keys(STATES);
// Step 4's variants, and how far the next step's contrast may stray from the disabled button's (as a share of it)
const VARIANTS = [
  ...['amber', 'green', 'white', 'red', 'blue', 'purple'].map((v) => ({ name: 'nostromo-' + v, settings: { theme_id: 'nostromo', theme_variant: v } })),
  ...['blackwall', 'relic', 'edgerun', 'mox'].map((v) => ({ name: 'night_city-' + v, settings: { theme_id: 'night_city', theme_variant: v } })),
  ...['dark', 'light'].map((m) => ({ name: 'gates-blue-' + m, settings: { theme_id: 'gates', theme_variant: 'blue', theme_mode: m } })),
];
const NEXT_TOLERANCE = 0.15;
const SHOWN = opt('states', '') ? opt('states', '').split(',').filter((s) => s && s !== 'none') : ALL_STATES;
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
  const settings = opts.settings || LOOK[look].settings;
  await page.evaluate(async ([s, tok]) => {
    await fetch('/api/settings', { method: 'POST', headers: { 'X-Monitor-Token': tok, 'Content-Type': 'application/json' },
      body: JSON.stringify(s) });
  }, [settings, token]);
  await page.waitForFunction((id) => document.documentElement.getAttribute('data-theme') === id, settings.theme_id, { timeout: 15000 });
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
  const bar = box.querySelector('[role="progressbar"]');
  const running = box.getAnimations ? box.getAnimations({ subtree: true }).filter((a) => a.playState === 'running')
    .map((a) => a.animationName || a.transitionProperty || 'animation') : [];
  const lit = cells ? cells.querySelector('i.on') || cells.querySelector('i.part') : null;
  return {
    hidden: box.hidden, cls: box.className, height: box.getBoundingClientRect().height,
    steps: chips.map((c) => c.getAttribute('data-step')),
    chipText: chips.map((c) => c.innerText.trim()),
    chipLook: chips.map((c) => { const s = cs(c); return { color: s.color, bg: s.backgroundColor, border: s.borderTopColor, opacity: s.opacity, oc: s.getPropertyValue('--oc').trim() }; }),
    // what tells a stalled step from a failed one besides the fill: its mark, its border, the bar's pattern
    chipMark: chips.map((c) => { const s = cs(c), b = cs(c, '::before'); return [b.content, s.borderTopStyle].join('|'); }),
    barPattern: lit ? cs(lit).backgroundImage : null,
    story: story ? story.innerText.trim() : null, storyTransform: story ? cs(story).textTransform : null,
    line: line ? line.innerText.trim() : null, lineErr: line ? line.classList.contains('is-err') : null,
    pending: cells ? cells.hasAttribute('data-pending') : null, fxPend: cells ? cells.getAttribute('data-fx-pend') : null,
    hot: cells ? cells.classList.contains('hot') : null,
    valueNow: bar ? bar.getAttribute('aria-valuenow') : null, valueText: bar ? bar.getAttribute('aria-valuetext') : null,
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
  if ((info.valueNow !== null) === info.pending) fail(`${where}: aria-valuenow ${info.valueNow} on a bar that is ${info.pending ? '' : 'not '}pending`);
  const crit = (want.tone || '') === 'is-crit';
  if (info.hot !== crit) fail(`${where}: the bar is ${info.hot ? '' : 'not '}hot`);
  if (reduced && (info.running.length || (info.afterAnimation && info.afterAnimation !== 'none'))) {
    fail(`${where}: moves under reduced motion: ${info.running} ${info.afterAnimation}`);
  }
  if (want.still && (info.running.length || (info.afterAnimation && info.afterAnimation !== 'none'))) {
    fail(`${where}: the bar moves while nothing arrives: ${info.running} ${info.afterAnimation}`);
  }
  if (info.overflow) fail(`${where}: something in the block is wider than its card`);
  if (crit && !info.lineErr) fail(`${where}: a failure's line is not in the error tone`);
  if (!info.statusHidden) fail(`${where}: the line under the buttons shows beside the block`);
}

// The three steps told apart: the computed look (colour with opacity, fill, outline) of a done, a current and a next chip
// all differ (critic 1 on #58). A next step dimmed only by its opacity, as a disabled control is, counts.
function judgeColours(where, info) {
  const by = {};
  info.steps.forEach((s, i) => { by[s] = info.chipLook[i]; });
  const look = (l) => [l.color, l.opacity, l.bg, l.oc].join(' / ');
  const pairs = [['now', 'next'], ['now', 'done'], ['next', 'done']];
  for (const [a, b] of pairs) {
    if (!by[a] || !by[b]) { fail(`${where}: no ${a} or ${b} chip to compare`); continue; }
    if (look(by[a]) === look(by[b])) fail(`${where}: ${a} and ${b} chips look the same: ${look(by[a])}`);
  }
}

// The contrast of an element as it is drawn, from a screenshot (as tests' pix tools measure it): the most common colour is
// its background, its text the mean of the 3% of pixels farthest from it; WCAG's ratio of the two.
async function drawnContrast(page, locator) {
  const png = await locator.screenshot();
  return page.evaluate(async (b64) => {
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const bmp = await createImageBitmap(new Blob([bytes], { type: 'image/png' }));
    const canvas = new OffscreenCanvas(bmp.width, bmp.height);
    const g = canvas.getContext('2d');
    g.drawImage(bmp, 0, 0);
    const d = g.getImageData(0, 0, bmp.width, bmp.height).data;
    const counts = new Map();
    const px = [];
    for (let i = 0; i < d.length; i += 4) {
      const key = (d[i] << 16) | (d[i + 1] << 8) | d[i + 2];
      counts.set(key, (counts.get(key) || 0) + 1);
      px.push([d[i], d[i + 1], d[i + 2]]);
    }
    let bgKey = 0, most = -1;
    for (const [key, n] of counts) if (n > most) { most = n; bgKey = key; }
    const bg = [(bgKey >> 16) & 255, (bgKey >> 8) & 255, bgKey & 255];
    const far = (c) => (c[0] - bg[0]) ** 2 + (c[1] - bg[1]) ** 2 + (c[2] - bg[2]) ** 2;
    px.sort((a, b) => far(b) - far(a));
    const n = Math.max(3, Math.floor(px.length * 3 / 100));
    const text = [0, 1, 2].map((j) => px.slice(0, n).reduce((s, c) => s + c[j], 0) / n);
    const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
    const x = lum(bg), y = lum(text);
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
  }, png.toString('base64'));
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
    if (STATES[state].then === 'lost') {
      answer = IDLE;                                          // a fresh page for what follows: lost and gone change what is asked
      await page.reload({ waitUntil: 'load' });
      await page.waitForSelector('[data-action="install-update"]:not([hidden])', { timeout: 30000 });
      await page.waitForTimeout(reduced ? 800 : 2500);
    }
  }
  // a failure or a lost link before the first byte keeps the block as high as it was while connecting (its sizes line stays)
  for (const state of states) {
    const as = STATES[state].high;
    if (as && result[state] && result[as] && Math.abs(result[state].height - result[as].height) > 0.5) {
      fail(`${prefix}${look}${reduced ? '-reduced' : ''}/${state}: the block is ${result[state].height} px high, ${as} ${result[as].height} px`);
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
      out.samples.push({ t: Date.now() - t0, steps: info.steps, lit: info.lit, story: info.story, install: info.install, pending: info.pending,
        now: info.valueNow });
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
    const sized = out.samples.findIndex((s) => s.now !== null);  // the block is drawn once and patched: the size ends the pending bar
    if (sized < 0 || out.samples.slice(sized).some((s) => s.pending)) fail(`${where}: the bar stayed pending after the size came`);
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

// Step 4, one variant: the next step against the disabled Install update button (both shown while the download is checked),
// and in Nostromo the stalled Download step against a failed one by what is not their fill.
async function variantPass(browser, server, variant) {
  let answer = IDLE;
  const route = (r) => r.fulfill({ status: 200, contentType: 'application/json', headers: { 'Cache-Control': 'no-store' }, body: JSON.stringify(answer) });
  const { context, page, errors } = await openSettings(browser, server, null, { reduced: true, route, settings: variant.settings });
  const where = `variants/${variant.name}`;
  const out = {};
  try {
    await showState(page, (a) => { answer = a; }, 'checking', true);
    const install = page.locator('[data-action="install-update"]');
    if ((await install.getAttribute('aria-disabled')) !== 'true') fail(`${where}: Install update is not disabled while the download is checked`);
    out.next = await drawnContrast(page, page.locator('#set-update-progress .nm-chip[data-step="next"]'));
    out.disabled = await drawnContrast(page, install);
    await page.locator('#set-update-progress').screenshot({ path: path.join(OUT, `variant-${variant.name}-checking.png`) });
    if (Math.abs(out.next / out.disabled - 1) > NEXT_TOLERANCE) {
      fail(`${where}: the next step's contrast ${out.next.toFixed(2)} is not the disabled button's ${out.disabled.toFixed(2)} (±${NEXT_TOLERANCE * 100}%)`);
    }
    if (variant.settings.theme_id === 'nostromo') {
      for (const state of ['stalled', 'failed-timeout']) {
        await showState(page, (a) => { answer = a; }, state, true);
        const info = await page.evaluate(READ);
        out[state] = { mark: info.chipMark[0], bar: info.barPattern, look: info.chipLook[0] };
        await page.locator('#set-update-progress').screenshot({ path: path.join(OUT, `variant-${variant.name}-${state}.png`) });
      }
      if (out.stalled.mark === out['failed-timeout'].mark && out.stalled.bar === out['failed-timeout'].bar) {
        fail(`${where}: a stalled and a failed download differ only by their fill (${out.stalled.mark})`);
      }
    }
  } catch (e) {
    fail(`${where}: ${String(e).split('\n')[0]}`);
  }
  if (errors.length) fail(`${where}: page errors: ${errors.slice(0, 3).join(' | ')}`);
  await context.close();
  return out;
}

(async () => {
  const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
  const result = { out: OUT, routed: {}, narrow: {}, live: {}, variants: {} };
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
      if (!SKIP_VARIANTS) {
        for (const variant of VARIANTS.filter((v) => !opt('variants', '') || opt('variants', '').split(',').includes(v.name))) {
          result.variants[variant.name] = await variantPass(browser, server, variant);
          const v = result.variants[variant.name];
          console.log('variant', variant.name, v.next ? `next ${v.next.toFixed(2)} disabled ${v.disabled.toFixed(2)}` : '', 'done');
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
