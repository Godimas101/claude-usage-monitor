// Real-browser check of the dashboard's hover effects: every one plays once per entry of the pointer and never again by itself.
//
// Not part of the pytest run (it needs Playwright for node and a Chromium). It starts the dev server itself, on a throwaway
// settings folder (CUM_DEV_INSTANCE=1, CUM_APPDATA_DIR in the temp folder: never your real settings or data), drives the real
// dashboard and counts the animation starts of the hover effects.
//
//   NODE_PATH=<folder with node_modules/playwright> node tests/browser/hover_probe.js [options]
//
//   --repo <dir>        the checkout to run (default: this one)
//   --themes a,b        nostromo,night_city,gates by default
//   --sides a,b         entry sides for the "Cache and thinking" card: left,right,top,bottom and edge-left,... (1 px inside an edge)
//   --pages a,b         pages whose cards, tiles and buttons are entered once each (default: all seven; "none" skips them)
//   --hold <ms>         how long the pointer rests after an entry (default 2000)
//   --rest <s>          how long the pointer rests on the card to see a data refresh redraw it (default 12; 0 skips it)
//   --reduced           emulate "reduce motion" and check the other way round: the entries must play nothing (the look, data-fx-hot,
//                       still has to follow the pointer); only step 1 is run
//   --out <file.json>   the whole result as JSON
//
// What it does, per theme:
//   1. "Cache and thinking" (Overview), entered from outside in three big steps onto a fill bar at each side, and 1 px inside each
//      edge; held for --hold. Each hover animation must start exactly once per element. (Before the fix, Night City played its
//      glitch about 30 times a second here for as long as the pointer rested.)
//   2. The same card: a sweep over its bars and text does not play anything, a quick leave and return does not replay, a return
//      after the cool-down does, and data-fx-hot is gone once the pointer has left.
//   3. Cards, stat tiles, buttons, the menu links, the refresh and the switch buttons of every page: one entry, same rule.
//   4. The pointer rests on the card across the minute turning (the dev server's data changes then, the page fetches it and draws
//      it): nothing plays again.
// Exit code 1 and "VERDICT: FAIL" when anything plays twice. What counts as one start of a hover effect: an animation start of
// the themes' hover animations (ncx-jit, ncx-tear, ncx-fringe, ncx-nav, nmx-settle), or for Gates a transition of transform on
// the element itself (its lift). Every other animation and transition start is listed ("all ...") but judged only for flicker: a
// transition that runs twice on one element while the pointer rests means the element moves off the pointer and back.
'use strict';
const { chromium } = require('playwright');
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const args = process.argv.slice(2);
const opt = (name, def) => { const i = args.indexOf('--' + name); return i >= 0 ? args[i + 1] : def; };
const REPO = path.resolve(opt('repo', path.join(__dirname, '..', '..')));
const OUT = opt('out', '');
const HOLD = +opt('hold', '2000');
const REST_S = +opt('rest', '12');
const REDUCED = args.includes('--reduced');
const THEMES = opt('themes', 'nostromo,night_city,gates').split(',');
const SIDES = opt('sides', 'left,right,top,bottom,edge-left,edge-right,edge-top,edge-bottom').split(',');
const PAGES = opt('pages', 'overview,limits,cost,efficiency,history,settings,about').split(',').filter(p => p !== 'none');
const APPDATA = fs.mkdtempSync(path.join(os.tmpdir(), 'cum-hover-'));
const HOVER_ANIMATIONS = new Set(['ncx-jit', 'ncx-tear', 'ncx-fringe', 'ncx-nav', 'nmx-settle']);

const THEME_SETTINGS = {
  nostromo: { theme_id: 'nostromo', theme_variant: 'amber' },
  night_city: { theme_id: 'night_city', theme_variant: 'blackwall' },
  gates: { theme_id: 'gates', theme_variant: 'blue', theme_mode: 'dark' },
};

// What a page has that motion.js calls a hover target: [selector, how many of each to enter, on which pages]
const KINDS = [
  ['card', '#main .nm-card', 1, null],
  ['stat tile', '#main .nm-stat', 1, null],
  ['button', '#main .nm-btn', 1, null],
  ['menu link', '#menu a', 2, 'overview'],
  ['refresh', '.nm-refresh', 1, 'overview'],
  ['switch button', '.nm-sw button', 1, 'overview'],
];

function startServer() {
  return new Promise((resolve, reject) => {
    const env = Object.assign({}, process.env, { CUM_DEV_INSTANCE: '1', CUM_APPDATA_DIR: APPDATA, PYTHONUNBUFFERED: '1' });
    const child = spawn(process.env.PYTHON || 'python', ['-m', 'usage_monitor.ui.devserver', '--port', '0', '--scenario', 'normal'],
      { cwd: REPO, env, windowsHide: true });
    let buf = '';
    const timer = setTimeout(() => { child.kill(); reject(new Error('dev server did not print its address: ' + buf)); }, 60000);
    child.stdout.on('data', d => {
      buf += d.toString();
      const m = /url=(\S+)/.exec(buf);
      if (m) { clearTimeout(timer); resolve({ child, url: m[1] }); }
    });
    child.stderr.on('data', d => { buf += d.toString(); });
    child.on('exit', code => { clearTimeout(timer); reject(new Error('dev server exited early (' + code + '): ' + buf)); });
  });
}

const INSTRUMENT = `
(() => {
  const ids = new WeakMap();
  let serial = 0;
  const idOf = (el) => { if (!ids.has(el)) ids.set(el, ++serial); return ids.get(el); };
  const describe = (el) => {
    if (!el || !el.tagName) return '?';
    let s = el.tagName.toLowerCase();
    if (el.id) s += '#' + el.id;
    if (el.classList && el.classList.length) s += '.' + Array.from(el.classList).slice(0, 2).join('.');
    return s + '@' + idOf(el);
  };
  const cardOf = (el) => { const c = el && el.closest ? el.closest('.nm-card') : null; return c ? (c.id || 'card') : null; };
  window.__fx = { starts: [], on: false, watch: null, redraws: 0, mutations: 0 };
  const record = (kind, name, e) => {
    if (!window.__fx.on) return;
    const w = window.__fx.watch;
    window.__fx.starts.push({ kind, name, target: describe(e.target), card: cardOf(e.target), inside: !w || w.contains(e.target), t: Math.round(performance.now()) });
  };
  document.addEventListener('animationstart', (e) => record('animation', e.animationName, e), true);
  document.addEventListener('transitionrun', (e) => record('transition', e.propertyName, e), true);
  document.addEventListener('DOMContentLoaded', () => {
    const main = document.getElementById('main');
    if (main) {
      new MutationObserver((records) => {
        if (window.__fx.on && records.some(r => r.type === 'childList' && r.target === main)) window.__fx.redraws++;
      }).observe(main, { childList: true });
      new MutationObserver((records) => {
        if (window.__fx.on) window.__fx.mutations += records.filter(r => r.type === 'characterData' || r.type === 'childList').length;
      }).observe(main, { childList: true, characterData: true, subtree: true });
    }
  });
})();
`;

// ── helpers ─────────────────────────────────────────────────────────────────────────────────────

const failures = [];
const fail = (msg) => { failures.push(msg); console.log('FAIL', msg); };

async function applyTheme(page, theme, token) {
  await page.evaluate(async ([settings, tok]) => {
    await fetch('/api/settings', { method: 'POST', headers: { 'X-Monitor-Token': tok, 'Content-Type': 'application/json' },
      body: JSON.stringify(settings) });
  }, [THEME_SETTINGS[theme], token]);
  await page.waitForFunction((id) => document.documentElement.getAttribute('data-theme') === id, theme, { timeout: 15000 });
  await page.waitForTimeout(2500);   // the next poll, the page redrawn, its entrance finished
}

async function gotoPage(page, name) {
  await page.evaluate((h) => { location.hash = h; }, '#' + name);
  await page.waitForFunction(() => {
    const main = document.getElementById('main');
    return main && !main.classList.contains('fx-run') && document.querySelectorAll('#main .nm-card').length > 0;
  }, null, { timeout: 30000 });
  try {
    await page.waitForFunction(() => !document.querySelector('#main [data-pending], #main .is-pending'), null, { timeout: 25000 });
  } catch (e) {
    console.log('note: the page ' + name + ' still has placeholders after 25 s');
  }
  await page.waitForTimeout(700);
}

// A redraw of #main sets .fx-quiet until the pointer moves (motion.js): nudge the pointer by one pixel until it is off, so that
// an entry measured below is a real one and not one that a poll's redraw happened to swallow.
async function wake(page, at) {
  for (let i = 0; i < 6; i++) {
    await page.mouse.move(at.x + 1, at.y);
    await page.mouse.move(at.x, at.y);
    await page.waitForTimeout(30);
    const quiet = await page.evaluate(() => document.getElementById('main').classList.contains('fx-quiet'));
    if (!quiet) return;
  }
}

// the hover animations among some starts: those inside the watched element, or (cardId) those inside the card with that id,
// which survives a redraw that replaces the element
const hoverStarts = (starts, cardId) => starts.filter(s => s.kind === 'animation' && HOVER_ANIMATIONS.has(s.name) && (cardId ? s.card === cardId : s.inside));
// One start of a hover effect, whatever the theme calls it: the glitch, the settle and the nudges of Night City and Nostromo are
// animations; Gates' lift is a transition of transform on the element itself.
function effectStarts(starts, theme, cardId) {
  if (theme === 'gates') {
    return starts.filter(s => s.kind === 'transition' && s.name === 'transform' && (cardId ? (s.card === cardId && s.target.includes('#' + cardId)) : s.inside));
  }
  return hoverStarts(starts, cardId);
}
// A state that follows the pointer (a lift, a nudge, a glow) is a transition, and a transition that runs twice on one element while
// the pointer rests is the element flickering under it: moving off the pointer, :hover going, moving back.
function flickers(starts, cardId) {
  return perElement(starts.filter(s => s.kind === 'transition' && (cardId ? s.card === cardId : s.inside)));
}
function perElement(starts) {
  const out = {};
  for (const s of starts) { const k = s.kind + ' ' + s.name + ' on ' + s.target; out[k] = (out[k] || 0) + 1; }
  return out;
}
const total = (o) => Object.values(o).reduce((a, b) => a + b, 0);
const most = (o) => Object.values(o).reduce((a, b) => Math.max(a, b), 0);

async function measure(page, watchHandle, run) {
  await page.evaluate((el) => { window.__fx.starts = []; window.__fx.redraws = 0; window.__fx.mutations = 0; window.__fx.watch = el; window.__fx.on = true; }, watchHandle);
  await run();
  return await page.evaluate(() => {
    window.__fx.on = false; window.__fx.watch = null;
    return { starts: window.__fx.starts.slice(), redraws: window.__fx.redraws, mutations: window.__fx.mutations };
  });
}

// ── 1. the "Cache and thinking" card, from every side ───────────────────────────────────────────

async function cacheCard(page) {
  await page.mouse.move(2, 2);                                 // off every card first: a looping effect keeps its element from being "stable"
  await gotoPage(page, 'overview');
  const handle = await page.$('#efficiency') || await page.$('#main .nm-card');
  await handle.scrollIntoViewIfNeeded();
  return handle;
}

async function bars(page, handle) {
  return await handle.evaluate((card) => Array.from(card.querySelectorAll('.nm-cells, .nm-ratio, .nm-rail')).map(b => {
    const r = b.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height };
  }));
}

async function sideEntry(page, theme, side) {
  const card = await cacheCard(page);
  await page.mouse.move(2, 2);
  await page.waitForTimeout(500);
  const box = await card.boundingBox();
  const fill = (await bars(page, card)).filter(b => b.w > 40 && b.h > 3);
  if (!fill.length) throw new Error('no fill bar in the card');
  const pick = (cmp) => fill.slice().sort(cmp)[0];
  let from, to;
  if (side.startsWith('edge-')) {
    const e = side.slice(5);
    const slitY = box.y + box.height * 0.33;                 // the height of a tear slit
    to = { left: { x: box.x + 1, y: slitY }, right: { x: box.x + box.width - 1, y: slitY },
           top: { x: box.x + box.width * 0.5, y: box.y + 1 }, bottom: { x: box.x + box.width * 0.5, y: box.y + box.height - 1 } }[e];
    from = { left: { x: box.x - 14, y: to.y }, right: { x: box.x + box.width + 14, y: to.y },
             top: { x: to.x, y: box.y - 14 }, bottom: { x: to.x, y: box.y + box.height + 14 } }[e];
  } else {
    // the bar's edge nearest the side the pointer comes from, where the card's edge and its hit area meet
    const t = { left: pick((a, b) => a.x - b.x), right: pick((a, b) => (b.x + b.w) - (a.x + a.w)),
                top: pick((a, b) => a.y - b.y), bottom: pick((a, b) => (b.y + b.h) - (a.y + a.h)) }[side];
    from = { left: { x: box.x - 14, y: t.y + t.h / 2 }, right: { x: box.x + box.width + 14, y: t.y + t.h / 2 },
             top: { x: t.x + t.w / 2, y: box.y - 14 }, bottom: { x: t.x + t.w / 2, y: box.y + box.height + 14 } }[side];
    to = { left: { x: Math.max(box.x + 2, t.x + 3), y: t.y + t.h / 2 }, right: { x: Math.min(box.x + box.width - 2, t.x + t.w - 3), y: t.y + t.h / 2 },
           top: { x: t.x + t.w / 2, y: Math.max(box.y + 2, t.y + 3) }, bottom: { x: t.x + t.w / 2, y: Math.min(box.y + box.height - 2, t.y + t.h - 3) } }[side];
  }
  await page.mouse.move(from.x, from.y);
  await page.waitForTimeout(250);
  await wake(page, from);
  const got = await measure(page, null, async () => {
    await page.mouse.move(to.x, to.y, { steps: 3 });           // fast: three big steps
    await page.waitForTimeout(HOLD);
  });
  const hot = await page.evaluate(() => Array.from(document.querySelectorAll('[data-fx-hot]')).map(e => e.id || e.className));
  const anims = perElement(effectStarts(got.starts, theme, 'efficiency'));
  const all = perElement(got.starts);
  console.log(theme.padEnd(11), side.padEnd(12), 'hover effect starts=' + String(total(anims)).padStart(3), 'most on one element=' + most(anims),
    'all animation and transition starts=' + String(got.starts.length).padStart(3), 'hot=' + JSON.stringify(hot));
  if (most(anims) > 1) fail(theme + ' ' + side + ': a hover effect started more than once on one element ' + JSON.stringify(anims));
  if (REDUCED) {
    if (total(anims) !== 0) fail(theme + ' ' + side + ': a hover effect played under reduced motion ' + JSON.stringify(anims));
    if (!hot.includes('efficiency')) fail(theme + ' ' + side + ': the card was not marked hot under reduced motion (its look would not follow the pointer)');
  } else if (total(anims) === 0) {
    fail(theme + ' ' + side + ': no hover effect played at all (the entry was swallowed)');
  }
  const flick = flickers(got.starts, 'efficiency');
  if (most(flick) > 1) fail(theme + ' ' + side + ': a transition ran more than once on one element while the pointer rested ' + JSON.stringify(flick));
  return { theme, side, from, to, anims, all, hot };
}

// ── 2. replays ─────────────────────────────────────────────────────────────────────────────────

async function replay(page, theme) {
  const card = await cacheCard(page);
  const box = await card.boundingBox();
  const far = { x: 2, y: 2 };
  const inside = { x: box.x + box.width * 0.5, y: box.y + box.height * 0.5 };
  const hotCount = () => page.evaluate(() => document.querySelectorAll('[data-fx-hot]').length);
  const out = { theme };
  await page.mouse.move(far.x, far.y);
  await page.waitForTimeout(700);
  await wake(page, far);
  out.first = perElement(effectStarts((await measure(page, null, async () => {
    await page.mouse.move(inside.x, inside.y, { steps: 3 }); await page.waitForTimeout(900);
  })).starts, theme, 'efficiency'));
  // a sweep over the whole card, bars and text, for 1.5 s: children coming under the pointer are not entries
  out.sweep = perElement(effectStarts((await measure(page, null, async () => {
    for (let i = 0; i < 30; i++) {
      await page.mouse.move(box.x + 6 + (box.width - 12) * ((i * 7) % 30) / 29, box.y + 6 + (box.height - 12) * ((i * 11) % 30) / 29, { steps: 2 });
      await page.waitForTimeout(50);
    }
  })).starts, theme, 'efficiency'));
  await page.mouse.move(far.x, far.y, { steps: 3 });
  await page.waitForTimeout(100);
  out.hotAfterLeave = await hotCount();
  // quick return: back within 80 ms of leaving
  await wake(page, far);
  await page.mouse.move(inside.x, inside.y);
  await page.waitForTimeout(150);
  await page.mouse.move(far.x, far.y);
  await page.waitForTimeout(80);
  out.quick = perElement(effectStarts((await measure(page, null, async () => {
    await page.mouse.move(inside.x, inside.y, { steps: 2 }); await page.waitForTimeout(700);
  })).starts, theme, 'efficiency'));
  // a real return: out for 700 ms, longer than the cool-down
  await page.mouse.move(far.x, far.y, { steps: 3 });
  await page.waitForTimeout(700);
  await wake(page, far);
  out.again = perElement(effectStarts((await measure(page, null, async () => {
    await page.mouse.move(inside.x, inside.y, { steps: 3 }); await page.waitForTimeout(900);
  })).starts, theme, 'efficiency'));
  out.hotWhileInside = await hotCount();
  await page.mouse.move(far.x, far.y, { steps: 3 });
  await page.waitForTimeout(700);
  out.hotAfterLastLeave = await hotCount();
  const label = theme + ' replay';
  console.log(label.padEnd(24), 'first=' + total(out.first), 'sweep=' + total(out.sweep), 'quick=' + total(out.quick), 'again=' + total(out.again),
    'hot: afterLeave=' + out.hotAfterLeave + ' inside=' + out.hotWhileInside + ' afterLastLeave=' + out.hotAfterLastLeave);
  if (most(out.first) > 1 || most(out.again) > 1) fail(label + ': an animation started twice in one entry');
  if (total(out.sweep) !== 0) fail(label + ': a sweep inside the card started animations ' + JSON.stringify(out.sweep));
  // Gates' lift is a state, not a one-shot: a pointer that comes straight back finds the card dropping and lifts it again
  if (theme !== 'gates' && total(out.quick) !== 0) fail(label + ': a quick return replayed ' + JSON.stringify(out.quick));
  if (total(out.first) === 0) fail(label + ': the first entry played nothing');
  if (total(out.first) !== total(out.again)) fail(label + ': a return after the cool-down did not play as the first entry did (' + total(out.first) + ' / ' + total(out.again) + ')');
  if (out.hotAfterLeave !== 0 || out.hotAfterLastLeave !== 0) fail(label + ': data-fx-hot stayed on after the pointer left');
  if (out.hotWhileInside < 1) fail(label + ': data-fx-hot was not set while the pointer was inside');
  return out;
}

// ── 3. every kind of target, on every page ──────────────────────────────────────────────────────

async function targets(page, theme) {
  const results = [];
  for (const name of PAGES) {
    await gotoPage(page, name);
    for (const [kind, selector, count, only] of KINDS) {
      if (only && only !== name) continue;
      const handles = await page.$$(selector);
      let done = 0;
      for (const el of handles) {
        if (done >= count) break;
        await el.scrollIntoViewIfNeeded().catch(() => {});
        const box = await el.boundingBox();
        if (!box || box.width < 10 || box.height < 10) continue;
        if (!(await el.isEnabled())) continue;
        // enter from outside, as close to an edge as the element's own shape allows (Night City cuts corners off)
        const cy = box.y + box.height / 2;
        const width = page.viewportSize().width;
        let way = null;
        for (const side of ['left', 'right']) {
          for (const inset of [2, 5, 8, 12, 20]) {
            const to = side === 'left' ? { x: box.x + inset, y: cy } : { x: box.x + box.width - inset, y: cy };
            const from = side === 'left' ? { x: box.x - 12, y: cy } : { x: box.x + box.width + 12, y: cy };
            if (from.x < 1 || from.x > width - 1) continue;
            const ok = await page.evaluate(([x, y, e]) => { const t = document.elementFromPoint(x, y); return !!(t && e.contains(t)); }, [to.x, to.y, el]);
            if (ok) { way = { to, from }; break; }
          }
          if (way) break;
        }
        if (!way) { console.log('note: a ' + kind + ' on ' + name + ' could not be entered (covered or off screen); skipped'); continue; }
        const { to, from } = way;
        await page.mouse.move(2, 2);
        await page.waitForTimeout(250);
        await page.mouse.move(from.x, from.y);
        await page.waitForTimeout(150);
        await wake(page, from);
        const got = await measure(page, el, async () => {
          await page.mouse.move(to.x, to.y, { steps: 3 });
          await page.waitForTimeout(Math.min(HOLD, 1200));
        });
        const anims = perElement(effectStarts(got.starts, theme));
        results.push({ theme, page: name, kind, anims });
        done++;
        console.log(theme.padEnd(11), name.padEnd(10), kind.padEnd(14), 'hover effect starts=' + String(total(anims)).padStart(2), 'most on one element=' + most(anims));
        if (most(anims) > 1) fail(theme + ' ' + name + ' ' + kind + ': a hover effect started more than once on one element ' + JSON.stringify(anims));
        const flick = flickers(got.starts);
        if (most(flick) > 1) fail(theme + ' ' + name + ' ' + kind + ': a transition ran more than once on one element while the pointer rested ' + JSON.stringify(flick));
      }
    }
  }
  return results;
}

// ── 4. a data refresh redraws the card under a resting pointer ──────────────────────────────────

async function rest(page, theme, token) {
  const card = await cacheCard(page);
  const box = await card.boundingBox();
  const far = { x: 2, y: 2 };
  const inside = { x: box.x + box.width * 0.5, y: box.y + box.height * 0.5 };
  await page.mouse.move(far.x, far.y);
  await page.waitForTimeout(700);
  await wake(page, far);
  // The dev server's state is the same all minute (its clock is rounded down to the minute) and the page asks for it every 2 s with
  // its ETag, so a data refresh happens when the minute turns: start the rest a few seconds before that, so that it covers one.
  const toMinute = 60000 - (Date.now() % 60000);
  const lead = Math.max(0, toMinute - 4000);
  if (lead > 0) console.log('(waiting ' + Math.round(lead / 1000) + ' s for the minute to turn)');
  await page.waitForTimeout(lead);
  const got = await measure(page, null, async () => {
    await page.mouse.move(inside.x, inside.y, { steps: 3 });
    await page.waitForTimeout(REST_S * 1000);
  });
  const anims = perElement(effectStarts(got.starts, theme, 'efficiency'));
  const label = theme + ' rest';
  console.log(label.padEnd(24), 'rested ' + REST_S + ' s, #main replaced ' + got.redraws + ' time(s), ' + got.mutations + ' DOM changes in it, hover effect starts=' + total(anims),
    'most on one element=' + most(anims));
  if (most(anims) > 1) fail(label + ': the redrawn card played again under a resting pointer ' + JSON.stringify(anims));
  if (got.mutations === 0) console.log('note: the page did not change during the rest, so no data refresh was covered; try --rest 20');
  await page.mouse.move(far.x, far.y, { steps: 3 });
  return { theme, redraws: got.redraws, anims };
}

// ── run ─────────────────────────────────────────────────────────────────────────────────────────

(async () => {
  const { child, url } = await startServer();
  const browser = await chromium.launch();
  const result = { errors: [], sides: [], replays: [], targets: [], rests: [] };
  try {
    const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, deviceScaleFactor: 1, colorScheme: 'dark',
      reducedMotion: REDUCED ? 'reduce' : 'no-preference' });
    await ctx.addInitScript(INSTRUMENT);
    const page = await ctx.newPage();
    page.on('pageerror', e => result.errors.push('pageerror: ' + e.message));
    await page.goto(url, { waitUntil: 'load', timeout: 30000 });
    await page.waitForFunction(() => document.querySelectorAll('.nm-card').length > 0, null, { timeout: 30000 });
    const token = await page.evaluate(() => document.querySelector('meta[name=monitor-token]').content);
    for (const theme of THEMES) {
      await applyTheme(page, theme, token);
      for (const side of SIDES) result.sides.push(await sideEntry(page, theme, side));
      if (REDUCED) continue;
      result.replays.push(await replay(page, theme));
      if (PAGES.length) result.targets.push(...await targets(page, theme));
      if (REST_S > 0) result.rests.push(await rest(page, theme, token));
    }
    if (result.errors.length) { console.log('PAGE ERRORS:', result.errors); fail('the page raised errors'); }
  } finally {
    await browser.close();
    child.kill();
    if (OUT) fs.writeFileSync(OUT, JSON.stringify(Object.assign({ failures }, result), null, 2));
    try { fs.rmSync(APPDATA, { recursive: true, force: true }); } catch (e) { /* the server may still hold a file; the OS cleans the temp folder */ }
  }
  console.log(failures.length ? 'VERDICT: FAIL (' + failures.length + ')' : 'VERDICT: PASS');
  if (failures.length) process.exitCode = 1;
})().catch(e => { console.error(e); process.exit(1); });
