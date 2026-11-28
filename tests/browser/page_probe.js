// Real-browser check of the shell, the header, the sidebar, Overview and Limits in every language the page is built for (#98):
// the headless stand-in for the real-web-view tests in tests/test_server.py (the overflow probe, the CSS capitals and the clip
// test), which builders cannot run because they open a WebView2 window.
//
// Not part of the pytest run (it needs Playwright for node and a Chromium). It starts dev servers itself (ui/devserver.py's
// make_server, in a Python of their own), on throwaway settings folders (USAGE_MONITOR_DEV_INSTANCE=1,
// USAGE_MONITOR_APPDATA_DIR under --data: never your real settings or data), their clock held at MOMENT, the record's moment;
// the page's clock is held there too (Playwright's clock) and its time zone is UTC, so every run draws the same dates. Every
// request the page makes goes to such a server on 127.0.0.1, or is answered by the probe. What it reads from the page is
// tests/browser/page_checks.js, which the real-web-view tests evaluate in WebView2 too.
//
//   NODE_PATH=<folder with node_modules/playwright> node tests/browser/page_probe.js [options]
//
//   --repo <dir>        the checkout to run (default: this one)
//   --out <dir>         where the screenshots and page_probe.json go (default: a new temp folder)
//   --data <dir>        where the dev servers keep their settings (default: a new temp folder)
//   PYTHON              the Python that runs the dev servers (default: python)
//   --looks a,b         nostromo,night_city,edgerun,gates-dark,gates-light by default
//   --langs a,b         en,pseudo,fr by default: the languages the widths step runs in
//   --sizes a,b         the windows, as WxH (default 1200x780,1060x640,720x480; the page is 16 x 39 px smaller, as in WebView2)
//   --scenarios a,b     the dev server's scenarios the widths step shows (default normal,warning,critical,stale)
//   --routed a,b        the states the probe makes by answering the page itself (default status-offline,link-lost,
//                       reports-failed,reports-stale; "none": none)
//   --steps a,b         widths,header,clip,caps,record,update,motion,rooms by default
//   --shots all|narrow|none   which views of the widths step are shot (default narrow: the narrowest size; all: every size)
//   CHROMIUM_PATH       a Chromium to launch instead of Playwright's own
//
// The steps (each view: one page load, then the window's sizes one after the other):
//   widths  Overview and Limits in each language, look, scenario and routed state, at each size. Fails on: an element that
//           runs wider than its box (a nowrap or pre element, a nav item, a meter or model row's cell, the header's words, a
//           chip or a tag: scrollWidth > clientWidth + 1); an element taller than its box where the box cannot grow (its
//           overflow is not visible, or its height is fixed: a model row, a nav item, the header bar, a chip, a button:
//           scrollHeight > clientHeight + 1); text that leaves its card or the header bar at a side, the top or the bottom,
//           or the nav at a side (the nav scrolls); words drawn over other words in a card, the bar or the nav; the nav's
//           blocks or the header's parts on top of each other; the two rows of a Limits meter whose bars do not start at the
//           same x; a chip (a box of its own: .nm-chip, .nm-pace > b) whose words' ink reaches its 1 px frame at the top or
//           the bottom (an accented capital, a cedilla: chips() measures the ink from the font's metrics), or whose frame runs
//           out of its card at a side while its words stay in (words that leave are the check above). English has exactly the overflows tests/fixtures/page_overflow_english.json names (#98's
//           owner question 3 decided two; made from an English run of this step), each no more than 1 px past its English
//           width; anything else fails. Another language (#98's owner question 1, as decided) has none but the designed cuts
//           (DESIGNED_CUTS, page_checks.js): an Overview card's meta cut with an ellipsis in Nostromo, Night City and Edgerun,
//           which passes only with its whole catalog text in the element, in its title and in an ancestor's aria-label, and
//           in a real language (not the pseudo-locale) never in the default window but where its words are English's (a key
//           the language has no words for yet) and English's record names the same cut. Also outside English: an ellipsis set on
//           a nav tab, the header's link words, a sentence line or a figure (NEVER_CUT), a nav tab over two lines, a chip
//           whose word wraps, and a meter's bar narrower than BAR_FLOOR fail; on Limits the first meter is read again with
//           each label far longer than a translation should be (meterStress): its bars keep BAR_FLOOR and start at the same
//           x, and nothing leaves the card.
//   header  The header bar in each language and look, with each link word (live, loading, stale, offline, rate_limited,
//           no_credentials, paused, and lost: the monitor stops answering), at every page width from 704 to 1184 px (most
//           20 px apart): the bar's one row never runs past the window (its scroll width), Refresh's right edge stays inside
//           it, nothing in the bar is cut, and the brand's words never run into the right-hand group (#98's review: outside
//           English the data age gives way first). Where the header hides the data age and the sidebar's health box (its
//           UPDATED row) is hidden too, the age cannot be read at all: AGE_GAPS names where that is so today (a gap found
//           elsewhere fails). Outside English the link's words are then written twice as long as the longest: they wrap to at
//           most two lines inside the bar, and the bar still holds its row.
//   clip    tests/test_server.py's clip test (test_overview_cards_never_clip_their_content_in_the_real_web_view) at its four
//           sizes, in English and the pseudo-locale: no Overview card cuts its content off, the cost tiles' numerals keep
//           4 px off the card's edge, and the plan block's two lines are the catalog's.
//   caps    English, with a release on offer (the dev server's update demo), as tests/test_server.py's capitals test has it:
//           every element that has its capitals from the look's stylesheet since #98 (CSS_CAPS) shows them (innerText) in
//           Nostromo, Night City and Edgerun and stores the natural case (textContent), and in Gates shows what it stores,
//           with no text-transform; and the regions that do not hang on the clock's zone (the header, the sidebar, the
//           heading) equal tests/fixtures/page_words_english.json's live view. Then the same judge in the states the normal
//           scenario does not draw (#98's review), each answered as tests/page_words.js answers its case (--worlds): the
//           status banners (Auth lost, No carrier, Rate limited, Paused), the stale and problem banners' headings, every
//           pace verdict and chip and every burn velocity; where the record's view has a banner, its heading
//           (#main .nm-msg > b) must be there, and every region of Overview and Limits equals the record's view of that case.
//   record  English, every look, every page: the probe answers the page with tests/fixtures/page_words_inputs.json (the
//           dev server's answers, frozen) at its moment (the page's clock is held there, the time zone UTC), and every region
//           tests/page_words.js reads (the header, the sidebar, each page's top parts and the cards the record keeps), read
//           here by the browser's own cascade (each text node in its element's computed text-transform) with its attributes,
//           equals tests/fixtures/page_words_english.json's "live" view. Night City's nav decoration (night_city.js, which
//           the record does not run) is left out, and a date the daily chart's axis leaves out at the browser's width (the
//           small DOM's is not a browser's) is a note, not a failure.
//   update  English, the dev server's update demo: each of tests/fixtures/update_lines_english.json's 28 lines, as the catalog
//           words it (its key filled with its values), is written into
//           Settings' line under the update buttons and into the update banner's line, and read back: shown in capitals in
//           Nostromo, Night City and Edgerun (the line's "today"), as stored in Gates; the buttons, the row's title and the
//           banner's heading likewise, the banner's sentence in capitals in Nostromo only, the row's help as written.
//   rooms   English, the default window, every look: the room each constrained text has (rooms() in page_checks.js: a nav
//           tab, an Overview card's meta, a limit card's facts and chip, the burn card's chip, a meter's label), in px and
//           in capitals of its font; each key ROOM_KEYS names must say the narrowest look's room in its en.json note
//           ("room about N px, about C capitals"), within 1 px and 1 capital.
//   motion  With motion (not reduced), English and French: every element a theme's entrance takes on Overview and Limits
//           (its director's selectors, the elements there when the entrance starts) is typed or decoded by the time it ends,
//           which it can only be when its words are its own text (#98's critic 2: no inner span in an animated element; on the
//           French page the English fallbacks carry lang="en" on the element itself).
//
// Its limits: headless Chromium is not WebView2. It draws at a device pixel ratio of 1 (WebView2 at 125 or 150 % display
// scaling rounds text widths otherwise) and, but for the motion step, with reduced motion (it measures the settled page).
// Exit code 1 and "VERDICT: FAIL" when any check fails.
'use strict';
const { chromium } = require('playwright');
const { spawn, spawnSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const CHECKS = require('./page_checks.js');            // what is read from the page, as tests/test_server.py reads it too

const args = process.argv.slice(2);
const opt = (name, def) => { const i = args.indexOf('--' + name); return i >= 0 ? args[i + 1] : def; };
const list = (name, def) => opt(name, def).split(',').filter((x) => x && x !== 'none');
const REPO = path.resolve(opt('repo', path.join(__dirname, '..', '..')));
const OUT = path.resolve(opt('out', '') || fs.mkdtempSync(path.join(os.tmpdir(), 'usage-monitor-page-probe-')));
const DATA = path.resolve(opt('data', '') || fs.mkdtempSync(path.join(os.tmpdir(), 'usage-monitor-page-data-')));
const LOOKS = list('looks', 'nostromo,night_city,edgerun,gates-dark,gates-light');
const LANGS = list('langs', 'en,pseudo,fr');
const SIZES = list('sizes', '1200x780,1060x640,720x480').map((s) => s.split('x').map(Number));
const SCENARIOS = list('scenarios', 'normal,warning,critical,stale');
const ROUTED = list('routed', 'status-offline,link-lost,reports-failed,reports-stale');
const STEPS = list('steps', 'widths,header,clip,caps,record,update,motion,rooms');
const SHOTS = opt('shots', 'narrow');
const FRAME = [16, 39];               // a WebView2 window's frame: a 1200 x 780 window holds a 1184 x 741 page
const PAGES = ['overview', 'limits'];
fs.mkdirSync(OUT, { recursive: true });
fs.mkdirSync(DATA, { recursive: true });

const LOOK = {
  nostromo: { settings: { theme_id: 'nostromo', theme_variant: 'amber' }, caps: true },
  night_city: { settings: { theme_id: 'night_city', theme_variant: 'blackwall' }, caps: true },
  edgerun: { settings: { theme_id: 'night_city', theme_variant: 'edgerun' }, caps: true },
  'gates-dark': { settings: { theme_id: 'gates', theme_variant: 'blue', theme_mode: 'dark' }, caps: false },
  'gates-light': { settings: { theme_id: 'gates', theme_variant: 'blue', theme_mode: 'light' }, caps: false },
};

// English's own overflows, as English draws them today (#98's owner question 3 decided two: Night City's heading and Edgerun's
// surfaces meta in the default window, each entry's owner "decided"; the others, first seen at 1060 x 640, "awaiting" the
// owner on #98). Each is named with the
// views it shows in and how far it runs past its box (wide: scrollWidth - clientWidth; leaves-side: px past its card); in
// English a known one may run at most 1 px further, and anything else fails. Another language has none but the designed
// cuts (DESIGNED_CUTS below): it wraps.
//   heading   Night City's page heading: its slanted cursor runs 3 px past the heading's box (nothing is cut).
//   ellipsis  the Overview grid's card metas, cut with an ellipsis by the theme's own rule (.nm-grid .nm-card-t .nm-meta-t):
//             Edgerun's surfaces meta in the default window, and at the 1060 x 640 window (Gates' 232 px nav column, which
//             the plan never measured) more of them in Nostromo, Night City and Edgerun.
//   find      not by design, and outside #98 (English must not change here): at 1060 x 640 Nostromo's weekly and scoped
//             Overview cards cut their reset lines and chip off when the figure has two digits (the warning and critical
//             scenarios), for a ticket of its own.
// Made from an English run of the widths step (all its scenarios and states) with the clocks held at MOMENT.
const KNOWN = JSON.parse(fs.readFileSync(path.join(REPO, 'tests', 'fixtures', 'page_overflow_english.json'), 'utf8')).known;
// Outside English: the designed cuts (#98's owner question 1, decided on #98), and what is never cut. The lists are
// page_checks.js's, which measure() reads in the page; the judge below holds each cut to the brief's rules.
const DESIGNED_CUTS = CHECKS.DESIGNED_CUTS;
const LISTS = { cuts: CHECKS.DESIGNED_CUTS, never: CHECKS.NEVER_CUT };
const DEFAULT_SIZE = '1200x780';              // the default window: a designed cut never fires there in a real language

const failures = [];
const fail = (msg) => { failures.push(msg); console.log('FAIL', msg); };
const notes = [];
const sizeName = ([w, h]) => w + 'x' + h;

// ── the dev server ───────────────────────────────────────────────────────────────────────────────────────────────────

// The moment every view is shown at: the record's (tests/fixtures/page_words_inputs.json), as tests/test_server.py pins its dev
// servers. The dev server's clock (its state, its invented history, every report) and the page's (Date) are both held there,
// in UTC, so each run draws the same dates and English's known widths stay comparable.
const MOMENT = '2026-10-21T16:12:00Z';
const SERVE = [
  'import sys, threading',
  'from datetime import datetime',
  'from usage_monitor.ui import devserver',
  'scenario, lang, demo, moment = sys.argv[1:5]',
  'now = datetime.fromisoformat(moment.replace("Z", "+00:00"))',
  'server, settings = devserver.make_server(scenario, 0, lang=lang, warm=True, clock=lambda: now, update_demo=demo or None)',
  'server.start()',
  'print("devserver url=" + server.url, flush=True)',
  'try:',
  '    threading.Event().wait()',
  'finally:',
  '    server.stop()',
  '    settings.close()',
].join(String.fromCharCode(10));

let serial = 0;
/* A dev server (ui/devserver.py's make_server, on 127.0.0.1) for a scenario and a language, with or without its update
 * demo, its clock held at MOMENT. */
function startServer(scenario, lang, demo) {
  return new Promise((resolve, reject) => {
    const data = path.join(DATA, 'server-' + (++serial));
    fs.mkdirSync(data, { recursive: true });
    const env = Object.assign({}, process.env, { USAGE_MONITOR_DEV_INSTANCE: '1', USAGE_MONITOR_APPDATA_DIR: data, PYTHONUNBUFFERED: '1' });
    const child = spawn(process.env.PYTHON || 'python', ['-c', SERVE, scenario, lang, demo || '', MOMENT], { cwd: REPO, env, windowsHide: true });
    let buf = '';
    const timer = setTimeout(() => { child.kill(); reject(new Error('dev server did not print its address: ' + buf)); }, 90000);
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
    if (server.child.exitCode !== null || server.child.signalCode !== null) { resolve(); return; }
    server.child.removeAllListeners('exit');
    server.child.once('exit', () => resolve());
    server.child.kill();
  });
}

// ── opening a page ───────────────────────────────────────────────────────────────────────────────────────────────────

/* A page in a look: the look is set through the server's settings route (as the theme picker sets it), then the page is
 * loaded afresh at hash. opts: size, reduced (default true), routes (page => Promise), clock (a fixed time), init (a script
 * before the page's own). */
async function openPage(browser, server, look, hash, opts) {
  const size = opts.size || SIZES[0];
  const context = await browser.newContext({ viewport: { width: size[0] - FRAME[0], height: size[1] - FRAME[1] }, deviceScaleFactor: 1,
    reducedMotion: opts.reduced === false ? 'no-preference' : 'reduce', timezoneId: 'UTC', locale: 'en-US' });
  if (opts.init) { await context.addInitScript(opts.init[0], opts.init[1]); }
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => {
    if (m.type() === 'error' && !/Failed to load resource|ERR_FAILED|ERR_CONNECTION_REFUSED|net::/.test(m.text())) { errors.push(m.text()); }
  });
  const token = decodeURIComponent((/[?&]k=([^&#]+)/.exec(server.url) || [])[1] || '');
  const base = server.url.replace(/\/\?.*$/, '');
  const set = await context.request.post(base + '/api/settings', { headers: { 'X-Monitor-Token': token, 'Content-Type': 'application/json' },
    data: Object.assign({}, LOOK[look].settings, opts.settings || {}) });
  if (!set.ok()) { throw new Error(look + ': settings ' + set.status() + ' ' + (await set.text())); }
  await page.clock.setFixedTime(new Date(opts.clock || MOMENT));
  if (opts.routes) { await opts.routes(page); }
  await page.goto(server.url + '#' + hash, { waitUntil: 'load', timeout: 30000 });
  await page.waitForFunction((id) => document.documentElement.getAttribute('data-theme') === id, LOOK[look].settings.theme_id, { timeout: 15000 });
  if (opts.wait !== false) { await settle(page, opts.reduced === false); }
  return { context, page, errors, base, token };
}

async function settle(page, motion) {
  await page.waitForFunction(CHECKS.settled, null, { timeout: 30000 }).catch(() => { /* measured as it is: a check below says what is wrong */ });
  await page.waitForTimeout(motion ? 1800 : 700);
}

async function resize(page, size) {
  await page.setViewportSize({ width: size[0] - FRAME[0], height: size[1] - FRAME[1] });
  await page.waitForTimeout(500);
  await page.waitForFunction(CHECKS.settled, null, { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(250);
}

// ── step widths: what runs past its room ─────────────────────────────────────────────────────────────────────────────

const pastBy = (item) => (item.kind === 'wide' ? item.sw - item.cw : item.by);

/* English's own overflow of this item in this view, as tests/fixtures/page_overflow_english.json names it. */
function englishKnown(item, look, state, page, size) {
  return KNOWN.find((k) => k.kind === item.kind && k.at === item.at && k.page === page && k.looks.includes(look) &&
    k.sizes.includes(sizeName(size)) && (k.states === '*' || k.states.includes(state)));
}

/* Why a cut with a visible ellipsis outside English is not a designed cut that passes, or null when it is one. In a real
 * language a designed cut in the default window passes only where its words are English's (a key the language has no words
 * for yet, lang="en") and English's record names the same cut there. */
function cutProblem(item, look, lang, state, page, size) {
  const c = item.cut;
  const d = DESIGNED_CUTS.find((x) => x.id === c.designed);
  if (!d) { return 'an ellipsis not in DESIGNED_CUTS' + (c.role ? ` (on a ${c.role})` : ''); }
  if (!d.looks.includes(look)) { return `a designed cut (${d.id}) in a look that does not cut it`; }
  if (!c.catalog) { return `a designed cut (${d.id}) whose text is not a whole catalog string ("${c.full}")`; }
  if (c.title !== c.full) { return `a designed cut (${d.id}) without its whole text in its title (title ${JSON.stringify(c.title)})`; }
  if (!c.named) { return `a designed cut (${d.id}) whose whole text is in no ancestor's aria-label`; }
  if (lang !== 'pseudo' && sizeName(size) === DEFAULT_SIZE && !(c.lang === 'en' && englishKnown(item, look, state, page, size))) {
    return `a designed cut (${d.id}) in a real language in the default window`;
  }
  return null;
}

function judgeWidths(where, lang, look, state, page, size, m) {
  for (const item of m.over) {
    const known = lang === 'en' ? englishKnown(item, look, state, page, size) : null;
    if (known) {
      if (pastBy(item) > known.by + 1) { fail(`${where}: ${item.at} runs ${pastBy(item)} px past its box, English's known ${known.by} px (${known.why})`); }
      continue;
    }
    if (lang !== 'en' && item.cut) {
      const why = cutProblem(item, look, lang, state, page, size);
      if (!why) { result.cuts.push({ where, at: item.at, by: pastBy(item), full: item.cut.full }); continue; }
      fail(`${where}: ${why}: ${item.at} "${item.cut.full}"`);
      continue;
    }
    fail(`${where}: ${item.kind} ${item.at} ${JSON.stringify(Object.fromEntries(Object.entries(item).filter(([k]) => !['at', 'kind'].includes(k))))}`);
  }
  if (lang === 'en') { return; }
  for (const s of m.styled) { fail(`${where}: an ellipsis on a ${s.role}: ${s.at} "${s.text}"`); }
  for (const b of m.bars) {
    if (b.widths.some((w) => w < CHECKS.BAR_FLOOR - 0.5)) {
      fail(`${where}: a meter's bar narrower than ${CHECKS.BAR_FLOOR} px: ${b.at} ${JSON.stringify(b.widths)}`);
    }
  }
}

/* The first meter on Limits with each label far longer than a translation should be: its bars keep BAR_FLOOR and start at the
 * same x, the meter stays inside its card and no label runs past its box. */
function judgeStress(where, s) {
  if (!s) { return; }
  const lefts = s.bars.map((b) => b.left);
  if (s.bars.some((b) => b.width < CHECKS.BAR_FLOOR - 0.5)) {
    fail(`${where}: a long label leaves the bar under ${CHECKS.BAR_FLOOR} px: ${JSON.stringify(s.bars)}`);
  }
  if (Math.max(...lefts) - Math.min(...lefts) > 0.5) { fail(`${where}: with a long label the bars start at ${JSON.stringify(lefts)}`); }
  if (s.card !== null && s.right > s.card + 0.5) { fail(`${where}: with a long label the meter ends at ${s.right}, past its card's ${s.card}`); }
  if (s.labels.some((l) => l.sw > l.cw + 1)) { fail(`${where}: a long label runs past its box: ${JSON.stringify(s.labels)}`); }
}

// What the probe answers for a routed state (route), and what it does once the page has loaded (then).
const DETAIL = 'Usage API unreachable (timed out); next try at 4:20 PM';
const ROUTES = {
  'status-offline': {
    route: async (page) => {
      await page.route('**/api/state*', async (r) => {
        try {
          const res = await r.fetch();
          if (res.status() !== 200) { return await r.fulfill({ response: res }); }
          const body = await res.json();
          body.state.status = 'offline';
          body.state.status_detail = DETAIL;
          return await r.fulfill({ response: res, body: JSON.stringify(body), headers: Object.assign({}, res.headers(), { etag: '' }) });
        } catch (e) {
          return undefined;                         // the page closed while the answer was on its way
        }
      });
    },
  },
  'link-lost': {
    route: async (page, flags) => {
      await page.route('**/api/state*', (r) => (flags.lost ? r.abort() : r.continue()));
    },
    then: async (page, flags) => {
      flags.lost = true;
      await page.waitForFunction(() => /\S/.test(document.getElementById('uplink-text').textContent) &&
        document.getElementById('uplink').classList.contains('is-down'), null, { timeout: 20000 }).catch(() => {});
      await page.waitForTimeout(800);
    },
  },
  'reports-failed': {
    route: async (page) => {
      await page.route('**/api/analytics/**', (r) => r.fulfill({ status: 503, contentType: 'application/json',
        body: JSON.stringify({ error: 'statistics are not available right now' }) }));
    },
  },
  'reports-stale': {
    route: async (page, flags) => {
      await page.route('**/api/analytics/**', (r) => (flags.failing ? r.fulfill({ status: 503, contentType: 'application/json',
        body: JSON.stringify({ error: 'the statistics database could not be opened' }) }) : r.continue()));
    },
    then: async (page, flags) => {
      flags.failing = true;
      // the pacing report is 20 s old by then: the next poll asks again, and fails
      await page.waitForFunction(() => !!document.querySelector('#main .nm-msg'), null, { timeout: 40000 }).catch(() => {});
      await page.waitForTimeout(800);
    },
  },
};

async function widthsView(browser, server, lang, look, state, routed) {
  const flags = {};
  const spec = routed ? ROUTES[state] : null;
  for (const hash of PAGES) {
    const { context, page, errors } = await openPage(browser, server, look, hash, { size: SIZES[0], routes: spec && spec.route ? (p) => spec.route(p, flags) : null });
    try {
      if (spec && spec.then) { await spec.then(page, flags); }
      for (const size of SIZES) {
        await resize(page, size);
        const where = `widths/${lang}/${look}/${state}/${hash}/${sizeName(size)}`;
        const m = await page.evaluate(CHECKS.measure, LISTS);
        // a chip's words clear of its frame (#98's review: accented capitals on Night City's 14 px chips), and inside its card
        for (const c of await page.evaluate(CHECKS.chips)) {
          const room = c.framed ? 1 : 0;              // clear of a 1 px frame, or inside an unframed chip's box
          if (c.top < room || c.bottom < room) { m.over.push({ at: c.at, kind: 'ink', top: c.top, bottom: c.bottom, h: c.h, text: c.text }); }
          // a chip whose frame leaves its card while its words stay in (words that leave are measure()'s leaves-side, which
          // English's record judges: Nostromo's find at 1060 x 640)
          if (c.out > 0.5 && c.wordsOut <= 0.5) { m.over.push({ at: c.at, kind: 'chip-out', by: c.out, text: c.text }); }
          // outside English a chip's own word stays on one line (#98's owner question 1)
          if (lang !== 'en' && c.chip && c.lines > 1) { m.over.push({ at: c.at, kind: 'chip-wraps', lines: c.lines, text: c.text }); }
        }
        judgeWidths(where, lang, look, state, hash, size, m);
        if (lang !== 'en' && hash === 'limits') {
          const label = await page.evaluate(() => { const k = document.querySelector('#main .nm-meter .nm-mrow .k'); return k ? k.textContent : null; });
          if (label) { judgeStress(where + '/long label', await page.evaluate(CHECKS.meterStress, (label + ' ').repeat(4).trim() + ' ' + 'W'.repeat(40))); }
        }
        if (hash === 'limits' && !m.meters && !routed) { fail(`${where}: no meter on Limits`); }
        if (SHOTS === 'all' || (SHOTS === 'narrow' && size === SIZES[SIZES.length - 1])) {
          await page.screenshot({ path: path.join(OUT, `widths-${lang}-${look}-${state}-${hash}-${sizeName(size)}.png`) });
        }
        result.widths[where] = { over: m.over, vw: m.vw };
      }
    } catch (e) {
      fail(`widths/${lang}/${look}/${state}/${hash}: ${String(e).split('\n')[0]}`);
    }
    if (errors.length) { fail(`widths/${lang}/${look}/${state}/${hash}: page errors: ${errors.slice(0, 3).join(' | ')}`); }
    await context.close();
  }
}

// ── step header: the header's one row at every width, with every link word ───────────────────────────────────────────

// Where the data age cannot be read at all today: the header hides it and the sidebar's health box (its UPDATED row) is
// hidden too (every look hides the health box under 1001 px, where the nav is a row). Recorded, not fixed here (#98's owner
// question 1: the 704 px check). below: the page widths under it; langs: 'all', or 'not-en' (outside English the header
// hides the data age at wider widths).
const AGE_GAPS = [
  { looks: ['nostromo'], langs: 'all', below: 801 },                     // as at c05f45d: Nostromo hides the data age under 801 px
  { looks: ['nostromo'], langs: 'not-en', below: 1001 },                 // outside English it goes under 1101 px
  { looks: ['night_city', 'edgerun'], langs: 'not-en', below: 801 },     // outside English it goes under 801 px
];
const ageGap = (look, lang, width) => AGE_GAPS.some((g) => g.looks.includes(look) && (g.langs === 'all' || lang !== 'en') && width < g.below);
const HEADER_WIDTHS = [704, 744, 784, 801, 820, 840, 860, 880, 900, 920, 940, 960, 980, 1000, 1020, 1044, 1064, 1084, 1104, 1124, 1144, 1164, 1184];
const LINK_STATES = ['live', 'loading', 'stale', 'offline', 'rate_limited', 'no_credentials', 'paused', 'lost'];
const LINK_DETAIL = { offline: DETAIL, rate_limited: 'Usage API rate limited', paused: 'Paused', loading: 'Loading usage',
  stale: 'The sign-in has expired. Claude Code renews it when you use it.' };

async function headerLook(browser, server, lang, look) {
  for (const code of LINK_STATES) {
    const flags = {};
    const routes = async (page) => {
      await page.route('**/api/state*', async (r) => {
        try {
          if (flags.lost) { return await r.abort(); }
          const res = await r.fetch();
          if (res.status() !== 200 || code === 'live' || code === 'lost') { return await r.fulfill({ response: res }); }
          const body = await res.json();
          body.state.status = code;
          body.state.status_detail = LINK_DETAIL[code] || '';
          return await r.fulfill({ response: res, body: JSON.stringify(body), headers: Object.assign({}, res.headers(), { etag: '' }) });
        } catch (e) {
          return undefined;                         // the page closed while the answer was on its way
        }
      });
    };
    const { context, page, errors } = await openPage(browser, server, look, 'overview', { size: SIZES[0], routes });
    const where = `header/${lang}/${look}/${code}`;
    try {
      if (code === 'lost') { await ROUTES['link-lost'].then(page, flags); }
      const seen = [];
      const judgeHeader = (at, h) => {
        if (h.sw > h.cw + 1) { fail(`${at}: the bar runs ${h.sw - h.cw} px past the window ("${h.link}")`); }
        if (h.refresh !== null && h.refresh > h.vw + 0.5) { fail(`${at}: Refresh ends at ${h.refresh}, past the window's ${h.vw} ("${h.link}")`); }
        if (h.cut.length) { fail(`${at}: cut in the bar: ${h.cut.join(', ')}`); }
        if (h.overlap !== null && h.overlap > 0.5) { fail(`${at}: the brand's words run ${h.overlap} px into the bar's right-hand group`); }
        if (h.lines > 2 || !h.inBar) { fail(`${at}: the link's words take ${h.lines} lines${h.inBar ? '' : ', past the bar'} ("${h.link}")`); }
      };
      for (const width of HEADER_WIDTHS) {
        await page.setViewportSize({ width, height: 601 });
        await page.waitForTimeout(120);
        const h = await page.evaluate(CHECKS.header);
        seen.push([width, h.sw, h.refresh, h.ageShown, h.healthShown]);
        const at = `${where}/${width}`;
        judgeHeader(at, h);
        // the data age readable somewhere: in the header, or in the health box's UPDATED row
        if (!h.ageShown && !h.healthShown) {
          if (ageGap(look, lang, width)) { result.ageGaps.push(at); } else { fail(`${at}: the data age is in neither the header nor the health box`); }
        }
      }
      result.header[where] = seen;
      // outside English, the link's words far longer than any (the longest written twice): they wrap inside the bar, and the
      // bar holds its row at every width (#98's owner question 1: the status word wraps, never cut)
      if (lang !== 'en' && code === 'offline') {
        await page.evaluate(`window.__probeHeader = ${CHECKS.header.toString()}`);
        for (const width of HEADER_WIDTHS) {
          await page.setViewportSize({ width, height: 601 });
          await page.waitForTimeout(80);
          const h = await page.evaluate(() => {
            const el = document.getElementById('uplink-text');
            const kept = el.textContent;
            el.textContent = kept + ' ' + kept;
            const got = window.__probeHeader();
            el.textContent = kept;
            return got;
          });
          judgeHeader(`${where}/long words/${width}`, h);
        }
      }
    } catch (e) {
      fail(`${where}: ${String(e).split('\n')[0]}`);
    }
    if (errors.length) { fail(`${where}: page errors: ${errors.slice(0, 3).join(' | ')}`); }
    await context.close();
  }
}

// ── step clip: tests/test_server.py's clip test ──────────────────────────────────────────────────────────────────────

const CLIP_SIZES = [[1200, 780], [1200, 700], [1100, 780], [1060, 640]];
const CLIP = () => {
  const cards = [...document.querySelectorAll('.nm-grid > .nm-card')].map((card) => {
    const inner = card.querySelector('.nm-card-in');
    const tile = card.querySelector('.nm-tile .nm-big');
    return { name: card.id || card.getAttribute('data-limit') || card.getAttribute('data-card') || '?', over: inner.scrollHeight - inner.clientHeight,
      tiles: tile ? +(inner.getBoundingClientRect().bottom - tile.getBoundingClientRect().bottom).toFixed(2) : null };
  });
  const plan = [...document.querySelectorAll('#cost .nm-plan .nm-facts > span')].map((span) => span.textContent);   // the lines, not the name's own span
  // the plan block's two lines as the page's catalog writes them, for the cost card's plan and price
  const cat = window.MonitorI18n || {};
  const text = (key) => (cat.strings && cat.strings[key] !== undefined ? cat.strings[key] : (cat.fallback || {})[key]);
  return { vw: innerWidth, cards, plan, want: [text('overview.cost.your_plan'), text('overview.cost.plan_price')] };
};

async function clipLook(browser, server, lang, look) {
  const { context, page, errors } = await openPage(browser, server, look, 'overview', { size: CLIP_SIZES[0] });
  try {
    await page.waitForFunction(() => !!document.querySelector('#cost .nm-tile') && document.querySelectorAll('#cost .nm-plan .nm-facts > span').length >= 2,
      null, { timeout: 20000 });
    for (const size of CLIP_SIZES) {
      await resize(page, size);
      const where = `clip/${lang}/${look}/${sizeName(size)}`;
      const m = await page.evaluate(CLIP);
      result.clip[where] = m;
      if (m.vw <= 1000) { fail(`${where}: the page is in the one column layout`); }
      const cut = m.cards.filter((c) => c.over > 0);
      if (cut.length) { fail(`${where}: cards cut their content off: ${JSON.stringify(cut.map((c) => [c.name, c.over]))}`); }
      const cost = m.cards.find((c) => c.name === 'cost');
      if (!cost || cost.tiles === null || cost.tiles < 4) { fail(`${where}: the tile numerals touch the card edge (${cost && cost.tiles})`); }
      const want = [m.want[0], m.want[1].replace('{plan}', 'MAX 5X').replace('{price}', '$100')].map((s) => s.toUpperCase());
      if (JSON.stringify(m.plan.map((s) => s.toUpperCase())) !== JSON.stringify(want)) { fail(`${where}: plan lines ${JSON.stringify(m.plan)} (want ${JSON.stringify(want)})`); }
    }
  } catch (e) {
    fail(`clip/${lang}/${look}: ${String(e).split('\n')[0]}`);
  }
  if (errors.length) { fail(`clip/${lang}/${look}: page errors: ${errors.slice(0, 3).join(' | ')}`); }
  await context.close();
}

// ── step caps: the capitals that come from the stylesheets ───────────────────────────────────────────────────────────

const squash = (s) => String(s).replace(/\s+/g, ' ').trim();

function judgeCaps(where, look, items) {
  const caps = LOOK[look].caps;
  let seen = 0;
  for (const it of items) {
    if (it.hidden) { continue; }
    seen++;
    // compared without white space: innerText puts a line break between the blocks an element holds, textContent nothing
    const stored = it.stored.replace(/\s+/g, '');
    const shown = it.shown.replace(/\s+/g, '');
    if (caps) {
      if (it.tt !== 'uppercase') { fail(`${where}: ${it.sel} "${squash(it.stored)}" has text-transform ${it.tt}`); }
      if (shown !== stored.toUpperCase()) { fail(`${where}: ${it.sel} shows "${squash(it.shown)}" (want "${squash(it.stored).toUpperCase()}")`); }
    } else {
      if (it.tt !== 'none') { fail(`${where}: ${it.sel} "${squash(it.stored)}" has text-transform ${it.tt} in Gates`); }
      if (shown !== stored) { fail(`${where}: ${it.sel} shows "${squash(it.shown)}", stores "${squash(it.stored)}"`); }
    }
  }
  return seen;
}

async function capsLook(browser, server, look) {
  for (const hash of PAGES) {
    const { context, page, errors } = await openPage(browser, server, look, hash, { size: SIZES[0] });
    const where = `caps/${look}/${hash}`;
    try {
      const sels = CHECKS.CSS_CAPS.filter(([, p]) => p === '*' || p === hash).map(([s]) => s);
      const found = await page.evaluate(CHECKS.readCaps, sels);
      found.forEach((items, i) => {
        if (!items.length && !/nm-msg|nm-remain|nm-chip|nm-tile|\.fc|nm-pace/.test(sels[i])) { fail(`${where}: no ${sels[i]} on the page`); }
      });
      const seen = judgeCaps(where, look, found.flat());
      // the regions that do not hang on the clock's zone (the header, the sidebar, the heading) against the record's live
      // view, on the update demo's live page, as tests/test_server.py's capitals test reads them in WebView2
      const got = await page.evaluate(CHECKS.regions, [hash, true, null]);
      const want = RECORD.views['live|' + look + '|' + hash];
      Object.keys(want).filter((name) => /^(chrome|top):/.test(name)).forEach((name) => {
        const text = RECORD.strings[want[name][0]];
        const attrs = RECORD.strings[want[name][1]];
        if (!got[name]) { fail(`${where}: no region ${name}`); return; }
        if (got[name].text !== text || JSON.stringify(got[name].attrs) !== attrs) {
          fail(`${where}: ${name} shows ${JSON.stringify(got[name].text).slice(0, 300)}, the record ${JSON.stringify(text).slice(0, 300)}`);
        }
      });
      result.caps[where] = { seen, items: found.flat() };
      // a page heading's sentence: in capitals in Nostromo only; the others as written
      const sub = await page.evaluate(() => { const s = document.querySelector('.nm-sub'); return s ? [s.innerText, s.textContent] : null; });
      if (sub && look === 'nostromo' && squash(sub[0]) !== squash(sub[1]).toUpperCase()) { fail(`${where}: the heading's sentence is not in capitals`); }
      if (sub && look !== 'nostromo' && squash(sub[0]) !== squash(sub[1])) { fail(`${where}: the heading's sentence is re-cased ("${squash(sub[0])}")`); }
    } catch (e) {
      fail(`${where}: ${String(e).split('\n')[0]}`);
    }
    if (errors.length) { fail(`${where}: page errors: ${errors.slice(0, 3).join(' | ')}`); }
    await context.close();
  }
}

// ── step caps, in the states the normal scenario does not draw ──────────────────────────────────────────────────────

const CAPS_CASES = ['status-stale', 'status-offline', 'status-rate-limited', 'status-paused', 'reports-stale', 'cost-failed-billing-day',
  'reports-501', 'verdicts-limited-ahead', 'verdicts-at-risk-on-pace', 'verdicts-idle-unknown', 'verdicts-unknown-behind',
  'chips-unused-on-pace-ahead', 'chips-no-clock-ahead', 'velocity-idle', 'velocity-slow', 'velocity-fast', 'velocity-very-fast'];
let WORLDS = null;
/* Each case's world as tests/page_words.js builds it (what its small server answers, and its steps). */
function worlds() {
  if (!WORLDS) {
    const run = spawnSync(process.execPath, [path.join(REPO, 'tests', 'page_words.js'), path.join(REPO, 'usage_monitor', 'webui'), '--worlds',
      CAPS_CASES.join(',')], { cwd: REPO, env: Object.assign({}, process.env, { TZ: 'UTC' }), encoding: 'utf8', windowsHide: true,
      maxBuffer: 256 * 1024 * 1024 });
    if (run.status !== 0) { throw new Error('page_words.js --worlds: ' + run.stderr); }
    WORLDS = JSON.parse(run.stdout);
  }
  return WORLDS;
}

/* Answers a route as the case's world has it: undefined (the report as the inputs have it), "hang", "fail" or {status, body}. */
function answerWith(r, spec, body) {
  if (spec === 'hang') { return undefined; }
  if (spec === 'fail') { return r.abort(); }
  if (spec && spec.status) {
    return r.fulfill({ status: spec.status, contentType: 'application/json', body: spec.body === null || spec.body === undefined ? '' : JSON.stringify(spec.body) });
  }
  return r.fulfill({ status: 200, contentType: 'application/json', headers: { 'Cache-Control': 'no-store' }, body: JSON.stringify(body) });
}

async function capsStatesLook(browser, server, look) {
  const all = worlds();
  for (const name of CAPS_CASES) {
    const w = JSON.parse(JSON.stringify(all[name]));
    const pages = w.pages.filter((p) => PAGES.includes(p));
    for (const [i, hash] of pages.entries()) {
      const answers = JSON.parse(JSON.stringify(w.answers));
      const flags = { state: w.stateAnswer };
      const routes = async (page) => {
        await page.route('**/api/state*', async (r) => {
          try {
            if (flags.state) { return await answerWith(r, flags.state, null); }
            const res = await r.fetch();
            if (res.status() !== 200) { return await r.fulfill({ response: res }); }
            const live = await res.json();
            const body = JSON.parse(JSON.stringify(w.state));
            body.theme = live.theme;
            Object.assign(body.settings, { theme_id: live.settings.theme_id, theme_variant: live.settings.theme_variant, theme_mode: live.settings.theme_mode });
            return await r.fulfill({ status: 200, contentType: 'application/json', headers: { 'Cache-Control': 'no-store' }, body: JSON.stringify(body) });
          } catch (e) {
            return undefined;                       // the page closed while the answer was on its way
          }
        });
        await page.route('**/api/analytics/**', (r) => {
          const report = new URL(r.request().url()).pathname.split('/').pop();
          return w.reports[report] ? answerWith(r, answers[report], w.reports[report]) : r.continue();
        });
      };
      const { context, page, errors } = await openPage(browser, server, look, hash, { size: SIZES[0], routes, clock: w.now });
      const where = `caps/${look}/${name}/${hash}`;
      try {
        for (const s of w.steps) {
          if (s.state !== undefined) { flags.state = s.state; }
          if (s.reports !== undefined) { Object.keys(w.reports).forEach((k) => { answers[k] = s.reports; }); }
          if (s.advance) {
            // the page's timers run on: its clock moves on and its next polls ask again
            await page.clock.setFixedTime(new Date(Date.parse(w.now) + s.advance));
            await page.waitForTimeout(4500);
          }
        }
        await settle(page, false);
        const want = RECORD.views[name + '|' + look + '|' + hash];
        const banner = Object.keys(want).some((r) => /^top:(status|stale|problem)$/.test(r));
        const sels = CHECKS.CSS_CAPS.filter(([, p]) => p === '*' || p === hash).map(([sel]) => sel);
        const found = await page.evaluate(CHECKS.readCaps, sels);
        found.forEach((items, k) => {
          if (sels[k] === '#main .nm-msg > b' && banner && !items.length) { fail(`${where}: no banner heading (${sels[k]})`); }
        });
        const seen = judgeCaps(where, look, found.flat());
        const got = await page.evaluate(CHECKS.regions, [hash, i === 0, KEPT_CARDS[hash] || null]);
        for (const region of Object.keys(want)) {
          const text = RECORD.strings[want[region][0]];
          const attrs = RECORD.strings[want[region][1]];
          if (!got[region]) { fail(`${where}: no region ${region}`); continue; }
          const axis = region === 'card:cost' && text.startsWith(got[region].text) && /^[A-Z][A-Za-z]{2} \d{1,2}$/.test(text.slice(got[region].text.length));
          if (axis) {
            notes.push(`${where}: ${region}: the daily chart's axis leaves out "${text.slice(got[region].text.length)}" at this width`);
          } else if (got[region].text !== text) {
            fail(`${where}: ${region} shows ${JSON.stringify(got[region].text).slice(0, 300)}, the record ${JSON.stringify(text).slice(0, 300)}`);
          }
          if (JSON.stringify(got[region].attrs) !== attrs) { fail(`${where}: ${region}'s attributes ${JSON.stringify(got[region].attrs).slice(0, 300)}, the record ${attrs.slice(0, 300)}`); }
        }
        result.caps[where] = { seen, banner, items: found.flat().length };
      } catch (e) {
        fail(`${where}: ${String(e).split('\n')[0]}`);
      }
      if (errors.length) { fail(`${where}: page errors: ${errors.slice(0, 3).join(' | ')}`); }
      await context.close();
    }
  }
}

// ── step record: the page against the page-words record, in a real browser ──────────────────────────────────────────

const RECORD = JSON.parse(fs.readFileSync(path.join(REPO, 'tests', 'fixtures', 'page_words_english.json'), 'utf8'));
const INPUTS = JSON.parse(fs.readFileSync(path.join(REPO, 'tests', 'fixtures', 'page_words_inputs.json'), 'utf8'));
const RECORD_PAGES = ['overview', 'limits', 'cost', 'efficiency', 'history', 'settings', 'about'];
const KEPT_CARDS = { overview: '*', limits: '*', cost: ['value', 'credits'], efficiency: ['pipeline'] };

async function recordLook(browser, server, look) {
  const routes = async (page) => {
    await page.route('**/api/state*', async (r) => {
      try {
        const res = await r.fetch();
        if (res.status() !== 200) { return await r.fulfill({ response: res }); }
        const live = await res.json();
        const body = JSON.parse(JSON.stringify(INPUTS.state));
        body.theme = live.theme;
        Object.assign(body.settings, { theme_id: live.settings.theme_id, theme_variant: live.settings.theme_variant, theme_mode: live.settings.theme_mode });
        return await r.fulfill({ status: 200, contentType: 'application/json', headers: { 'Cache-Control': 'no-store' }, body: JSON.stringify(body) });
      } catch (e) {
        return undefined;                         // the page closed while the answer was on its way
      }
    });
    await page.route('**/api/analytics/**', (r) => {
      const name = new URL(r.request().url()).pathname.split('/').pop();
      return INPUTS[name] ? r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(INPUTS[name]) }) : r.continue();
    });
  };
  for (const [i, hash] of RECORD_PAGES.entries()) {
    const { context, page, errors } = await openPage(browser, server, look, hash, { size: SIZES[0], routes, clock: INPUTS.now });
    const where = `record/${look}/${hash}`;
    try {
      if (hash === 'settings' || hash === 'about') { await page.waitForTimeout(1200); }
      const got = await page.evaluate(CHECKS.regions, [hash, i === 0, KEPT_CARDS[hash] || null]);
      const want = RECORD.views['live|' + look + '|' + hash];
      if (!want) { fail(`${where}: the record has no live view`); } else {
        for (const name of Object.keys(want)) {
          const text = RECORD.strings[want[name][0]];
          const attrs = RECORD.strings[want[name][1]];
          if (!got[name]) { fail(`${where}: no region ${name}`); continue; }
          // the daily chart draws as many date labels on its axis as its drawn width has room for (charts.js), and the small
          // DOM's width is not a browser's: a label the record has past the browser's last one is noted, not failed
          const axis = name === 'card:cost' && text.startsWith(got[name].text) && /^[A-Z][A-Za-z]{2} \d{1,2}$/.test(text.slice(got[name].text.length));
          if (axis) {
            notes.push(`${where}: ${name}: the daily chart's axis leaves out "${text.slice(got[name].text.length)}" at this width`);
          } else if (got[name].text !== text) {
            fail(`${where}: ${name} shows ${JSON.stringify(got[name].text).slice(0, 400)}, the record ${JSON.stringify(text).slice(0, 400)}`);
          }
          if (JSON.stringify(got[name].attrs) !== attrs) { fail(`${where}: ${name}'s attributes ${JSON.stringify(got[name].attrs).slice(0, 300)}, the record ${attrs.slice(0, 300)}`); }
        }
        const extra = Object.keys(got).filter((n) => !want[n]);
        if (extra.length) { fail(`${where}: regions the record does not have: ${extra}`); }
        result.record[where] = Object.keys(want).length;
      }
    } catch (e) {
      fail(`${where}: ${String(e).split('\n')[0]}`);
    }
    if (errors.length) { fail(`${where}: page errors: ${errors.slice(0, 3).join(' | ')}`); }
    await context.close();
  }
}

// ── step update: #97's update texts as each look shows them ──────────────────────────────────────────────────────────

const UPDATE_LINES = JSON.parse(fs.readFileSync(path.join(REPO, 'tests', 'fixtures', 'update_lines_english.json'), 'utf8')).lines;
const EN_CATALOG = JSON.parse(fs.readFileSync(path.join(REPO, 'usage_monitor', 'webui', 'i18n', 'en.json'), 'utf8'));
const SENTENCE_CAPS = { nostromo: true, night_city: false, edgerun: false, 'gates-dark': false, 'gates-light': false };

function judgeShown(where, item, want, caps) {
  if (!item) { fail(`${where}: not drawn`); return; }
  if (squash(item.stored) !== want) { fail(`${where}: stores "${squash(item.stored)}" (want "${want}")`); }
  const shown = caps ? want.toUpperCase() : want;
  if (squash(item.shown) !== shown) { fail(`${where}: shows "${squash(item.shown)}" (want "${shown}")`); }
}

async function updateLook(browser, server, look) {
  const caps = LOOK[look].caps;
  // each line as the page writes it: the catalog's English for its key, filled with its values
  const lines = UPDATE_LINES.map((l) => EN_CATALOG[l.key].replace(/\{(\w+)\}/g, (all, name) => (name in l.values ? String(l.values[name]) : all)));
  // the line as Nostromo and Night City showed it before #97: the record's "today" (the offered line's is as app.js wrote it,
  // which the line's stylesheet set in capitals then as now)
  const today = UPDATE_LINES.map((l) => l.today.toUpperCase());
  for (const [hash, sel] of [['settings', '#set-update-status'], ['overview', '.nm-update .nm-status']]) {
    const { context, page, errors } = await openPage(browser, server, look, hash, { size: SIZES[0], wait: false });
    const where = `update/${look}/${hash}`;
    try {
      await page.waitForSelector(hash === 'settings' ? '[data-action="install-update"]:not([hidden])' : '.nm-update', { timeout: 30000 });
      await page.waitForTimeout(900);
      const first = await page.evaluate(CHECKS.readUpdate);
      if (hash === 'settings') {
        ['Check for updates now', 'Install update', 'Skip this version'].forEach((w, i) => judgeShown(`${where}/button ${i + 1}`, first.settings.buttons[i], w, caps));
        judgeShown(`${where}/row title`, first.settings.title, 'Check for updates', caps);
        judgeShown(`${where}/row help`, first.settings.help, 'Look for a newer release once in a while.', false);
      } else {
        judgeShown(`${where}/heading`, first.banner.title, 'Claude Usage Monitor v9.9.9 is available', caps);
        judgeShown(`${where}/sentence`, first.banner.sentence, 'Update now installs it and restarts the monitor.', SENTENCE_CAPS[look]);
        ['Update now', 'Release notes', 'Skip this version'].forEach((w, i) => judgeShown(`${where}/button ${i + 1}`, first.banner.buttons[i], w, caps));
      }
      // the line element of each place, written with each line in turn
      const said = await page.evaluate(CHECKS.writeLines, [sel, lines]);
      if (!said) { fail(`${where}: no ${sel}`); } else {
        said.forEach((got, i) => {
          // stored as the record's natural English (the text Gates showed before #97), shown as each look showed it then
          if (squash(got.stored) !== UPDATE_LINES[i].gates) { fail(`${where}/${UPDATE_LINES[i].said}: stores "${got.stored}" (want "${UPDATE_LINES[i].gates}")`); }
          const want = caps ? today[i] : UPDATE_LINES[i].gates;
          if (squash(got.shown) !== want) { fail(`${where}/${UPDATE_LINES[i].said}: shows "${squash(got.shown)}" (want "${want}")`); }
        });
      }
      result.update[where] = { first, said };
    } catch (e) {
      fail(`${where}: ${String(e).split('\n')[0]}`);
    }
    if (errors.length) { fail(`${where}: page errors: ${errors.slice(0, 3).join(' | ')}`); }
    await context.close();
  }
}

// ── step rooms: each constrained text's room, against its note ───────────────────────────────────────────────────────

// The keys whose en.json note gives translators a room, by what rooms() measures for them: nav (a nav tab), meta:<card>
// (an Overview card's meta; limit.meta.weekly and overview.limit.no_scoped sit on the weekly and scoped cards), facts (a
// limit card's facts column; its chip's words, limit.pace.*, in caps of the chip's font), burn (the burn card's chip) and
// meter (a meter's label).
const ROOM_KEYS = {
  nav: ['shell.nav.overview', 'shell.nav.limits', 'shell.nav.cost', 'shell.nav.efficiency', 'shell.nav.history', 'shell.nav.settings', 'shell.nav.about'],
  'meta:limit-session': ['limit.meta.session'],
  'meta:limit-weekly,limit-scoped': ['limit.meta.weekly', 'overview.limit.no_scoped'],
  'meta:cost': ['overview.cost.meta', 'overview.cost.meta_range'],
  'meta:efficiency': ['overview.efficiency.cycle', 'overview.efficiency.month'],
  'meta:models': ['overview.models.cycle', 'overview.models.month'],
  'meta:surfaces': ['overview.surfaces.meta'],
  facts: ['limit.fact.share', 'limit.fact.resets', 'limit.fact.in'],
  'facts:chip': ['limit.pace.unused', 'limit.pace.no_draw', 'limit.pace.ahead', 'limit.pace.under', 'limit.pace.on', 'limit.pace.no_clock'],
  burn: ['overview.burn.velocity.idle', 'overview.burn.velocity.slow', 'overview.burn.velocity.normal', 'overview.burn.velocity.fast',
    'overview.burn.velocity.very_fast'],
  meter: ['limits.meter.elapsed', 'limits.meter.used'],
};
const ROOM_NOTE = /room about (\d+) px, about (\d+) capitals/;

async function roomsLook(browser, server, look) {
  const found = {};
  for (const hash of PAGES) {
    const { context, page, errors } = await openPage(browser, server, look, hash, { size: SIZES[0] });
    try {
      const r = await page.evaluate(CHECKS.rooms);
      if (hash === 'overview') {
        found.nav = r.nav;
        r.metas.forEach((m) => { found['meta:' + m.card] = [m]; });
        found.facts = r.facts;
        found['facts:chip'] = r.facts.filter((f) => f.chipCaps !== null).map((f) => ({ card: f.card, room: f.room, caps: f.chipCaps }));
        found.burn = r.burn;
      } else {
        found.meter = r.meter;
      }
    } catch (e) {
      fail(`rooms/${look}/${hash}: ${String(e).split('\n')[0]}`);
    }
    if (errors.length) { fail(`rooms/${look}/${hash}: page errors: ${errors.slice(0, 3).join(' | ')}`); }
    await context.close();
  }
  result.rooms[look] = found;
}

/* Each ROOM_KEYS group's narrowest room over the looks that have it (px, and capitals), against the note of each of its keys. */
function judgeRooms() {
  const least = {};
  for (const [group, keys] of Object.entries(ROOM_KEYS)) {
    const items = [];
    for (const [look, found] of Object.entries(result.rooms)) {
      for (const part of group.split(':')[0] === 'meta' ? group.slice(5).split(',').map((c) => 'meta:' + c) : [group]) {
        (found[part] || []).forEach((x) => items.push(Object.assign({ look }, x)));
      }
    }
    if (!items.length) { fail(`rooms: nothing measured for ${group}`); continue; }
    const px = Math.floor(Math.min(...items.map((x) => x.room)));
    const caps = Math.min(...items.map((x) => x.caps));
    least[group] = { px, caps, pxLook: items.find((x) => x.room === Math.min(...items.map((y) => y.room))).look,
      capsLook: items.find((x) => x.caps === caps).look };
    for (const key of keys) {
      const note = EN_CATALOG['_note.' + key] || '';
      const m = ROOM_NOTE.exec(note);
      if (!m) { fail(`rooms: the note of ${key} gives no room ("room about N px, about C capitals"); measured ${px} px, ${caps} capitals`); continue; }
      if (Math.abs(Number(m[1]) - px) > 1 || Math.abs(Number(m[2]) - caps) > 1) {
        fail(`rooms: the note of ${key} says ${m[1]} px, ${m[2]} capitals; measured ${px} px (${least[group].pxLook}), ${caps} capitals (${least[group].capsLook})`);
      }
    }
  }
  result.roomsLeast = least;
  console.log('rooms', JSON.stringify(least));
}

// ── step motion: every animated element is animated ──────────────────────────────────────────────────────────────────

// The words each theme's entrance animates: its director's head selectors (outside the cards) and card selectors (inside
// them), as themes/nostromo.js and themes/night_city.js set them; Gates animates figures only.
const ANIMATED = {
  nostromo: { head: ['.nm-h1', '.nm-sub'], card: ['.nm-tag, .nm-ttl', '.nm-meta-t', '.nm-mrow .k', '.nm-kv-grid .nm-kv b'] },
  night_city: { head: ['.nm-h1'], card: ['.nm-tag, .nm-ttl', '.nm-kv-grid .nm-kv b'] },
};
// Before the page's own scripts. While the entrance plays (#main.fx-run), every element whose own children a script changes
// (a typer puts its typed and untyped halves in place of the element's text node) or that a decoder marks (data-fx) is noted
// (app.js draws an element with its words in it, so drawing one changes its parent's children, not its own). When fx-run
// comes on, the words the director takes are listed; when it goes, each is judged: animated, or gone from the page.
const WATCH = (animated) => {
  const moved = new WeakSet();
  let targets = null;
  window.__fxResult = null;
  const pending = (el) => !!el.closest('[data-pending], .nm-card[data-state=pending]');
  const list = (main) => {
    const set = animated[document.documentElement.getAttribute('data-theme')];
    if (!set) { return []; }
    const out = [];
    set.head.forEach((sel) => main.querySelectorAll(sel).forEach((el) => { if (!el.closest('.nm-card')) { out.push([sel, el]); } }));
    main.querySelectorAll('.nm-card').forEach((card) => set.card.forEach((sel) => card.querySelectorAll(sel).forEach((el) => out.push([sel, el]))));
    return out.filter(([, el]) => el.textContent.trim() && !pending(el));
  };
  new MutationObserver((records) => {
    for (const r of records) {
      const el = r.target;
      if (!el || el.nodeType !== 1) { continue; }
      if (r.type === 'attributes' && r.attributeName === 'class' && el.id === 'main') {
        const on = el.classList.contains('fx-run');
        if (on && !targets) { targets = list(el); }
        if (!on && targets && !window.__fxResult) {
          window.__fxResult = targets.map(([sel, t]) => ({ sel, text: t.textContent.trim().slice(0, 60), moved: moved.has(t),
            gone: !t.isConnected, lang: t.getAttribute('lang'),
            inner: ![...t.childNodes].some((n) => n.nodeType === 3 && n.nodeValue.trim()) }));
        }
        continue;
      }
      moved.add(el);
    }
  }).observe(document, { subtree: true, childList: true, attributes: true, attributeFilter: ['data-fx', 'class'] });
};

async function motionLook(browser, server, lang, look) {
  const theme = LOOK[look].settings.theme_id;
  if (!ANIMATED[theme]) { return; }
  for (const hash of PAGES) {
    const { context, page, errors } = await openPage(browser, server, look, hash, { size: SIZES[0], reduced: false, wait: false,
      init: [WATCH, ANIMATED] });
    const where = `motion/${lang}/${look}/${hash}`;
    try {
      // the boot (once a browser session) plays first, and the page enters as it goes
      await page.waitForFunction(() => !!window.__fxResult, null, { timeout: 30000 });
      const found = await page.evaluate(() => window.__fxResult);
      const still = found.filter((f) => !f.moved && !f.gone);
      if (!found.length) { fail(`${where}: nothing the entrance animates`); }
      still.forEach((f) => fail(`${where}: the entrance did not animate ${f.sel} "${f.text}"${f.inner ? ' (its words are in an inner element)' : ''}`));
      if (lang === 'fr' && !found.some((f) => f.lang === 'en')) { fail(`${where}: no English fallback marked on its own element to check`); }
      result.motion[where] = { found: found.length, still: still.length, gone: found.filter((f) => f.gone).length,
        marked: found.filter((f) => f.lang).length };
    } catch (e) {
      fail(`${where}: ${String(e).split('\n')[0]}`);
    }
    if (errors.length) { fail(`${where}: page errors: ${errors.slice(0, 3).join(' | ')}`); }
    await context.close();
  }
}

// ── the run ──────────────────────────────────────────────────────────────────────────────────────────────────────────

const result = { out: OUT, widths: {}, header: {}, clip: {}, caps: {}, record: {}, update: {}, motion: {}, rooms: {}, cuts: [], ageGaps: [] };

(async () => {
  const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
  const t0 = Date.now();
  try {
    for (const lang of LANGS) {
      if (STEPS.includes('widths')) {
        for (const scenario of SCENARIOS) {
          const server = await startServer(scenario, lang);
          try {
            for (const look of LOOKS) {
              await widthsView(browser, server, lang, look, scenario, false);
              if (scenario === 'normal') {
                for (const state of ROUTED) { await widthsView(browser, server, lang, look, state, true); }
              }
              console.log('widths', lang, scenario, look, 'done', Math.round((Date.now() - t0) / 1000) + ' s');
            }
          } finally {
            await stopServer(server);
          }
        }
      }
      const needs = (STEPS.includes('clip') && lang !== 'fr') || (STEPS.includes('motion') && lang !== 'pseudo') ||
        (lang === 'en' && (STEPS.includes('record') || STEPS.includes('caps'))) || STEPS.includes('header');
      if (needs) {
        const server = await startServer('normal', lang);
        try {
          for (const look of LOOKS) {
            if (STEPS.includes('header')) { await headerLook(browser, server, lang, look); }
            if (STEPS.includes('clip') && lang !== 'fr') { await clipLook(browser, server, lang, look); }
            if (STEPS.includes('record') && lang === 'en') { await recordLook(browser, server, look); }
            if (STEPS.includes('caps') && lang === 'en') { await capsStatesLook(browser, server, look); }
            if (STEPS.includes('motion') && lang !== 'pseudo') { await motionLook(browser, server, lang, look); }
            console.log('steps', lang, look, 'done', Math.round((Date.now() - t0) / 1000) + ' s');
          }
        } finally {
          await stopServer(server);
        }
      }
      if (STEPS.includes('update') && lang === 'en') {
        const server = await startServer('normal', 'en', 'ok');
        try {
          for (const look of LOOKS) { await updateLook(browser, server, look); }
          console.log('update done', Math.round((Date.now() - t0) / 1000) + ' s');
        } finally {
          await stopServer(server);
        }
      }
      if (STEPS.includes('rooms') && lang === 'en') {
        const server = await startServer('normal', 'en');
        try {
          for (const look of LOOKS) { await roomsLook(browser, server, look); }
          judgeRooms();
          console.log('rooms done', Math.round((Date.now() - t0) / 1000) + ' s');
        } finally {
          await stopServer(server);
        }
      }
      if (STEPS.includes('caps') && lang === 'en') {
        const server = await startServer('normal', 'en', 'ok');           // with a release on offer, as the capitals test has
        try {
          for (const look of LOOKS) { await capsLook(browser, server, look); }
          console.log('caps done', Math.round((Date.now() - t0) / 1000) + ' s');
        } finally {
          await stopServer(server);
        }
      }
    }
  } finally {
    await browser.close();
  }
  result.failures = failures;
  result.notes = notes;
  if (result.cuts.length) { console.log('designed cuts that fired (passed):', result.cuts.length); }
  if (result.ageGaps.length) { console.log('data age gaps (AGE_GAPS, recorded):', result.ageGaps.length); }
  notes.forEach((n) => console.log('NOTE', n));
  result.seconds = Math.round((Date.now() - t0) / 1000);
  fs.writeFileSync(path.join(OUT, 'page_probe.json'), JSON.stringify(result, null, 1));
  console.log('screens and page_probe.json in ' + OUT);
  console.log(failures.length ? 'VERDICT: FAIL (' + failures.length + ')' : 'VERDICT: PASS');
  if (failures.length) { process.exitCode = 1; }
})().catch((e) => { console.error(e); process.exit(1); });
