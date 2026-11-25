"use strict";
/* V2 of #91's plan: the page's formats (formats.js, through this node's Intl and ICU) for every format tag of French,
 * Spanish and Turkish over a grid, as one JSON object, with node's ICU and CLDR versions.
 *
 * tests/test_webui.py gives the tags and the grid (CLDR_GRID), writes the same grid with usage_monitor/formats.py and
 * casing.py (cldr.py: CLDR 47 from Babel 2.18.0), and holds the two equal, except the differences it lists as known (each
 * with its tag, field, both values and both versions). A WebView with other CLDR data shows up there first.
 * "durationUnits" is each duration written without Intl.DurationFormat (D14 of the plan), for the check that the two ways
 * agree. Times are UTC wall times: run it with TZ=UTC. Every input is made up.
 *
 *   node tests/webui_cldr.js <webui folder> <tags as a JSON list> <grid as JSON>
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const webui = process.argv[2];
const tags = JSON.parse(process.argv[3] || "[]");
const grid = JSON.parse(process.argv[4] || "null");
if (!webui || !tags.length || !grid) {
  throw new Error("usage: node tests/webui_cldr.js <webui folder> <tags as a JSON list> <grid as JSON>");
}
if (new Date(2026, 0, 15).getTimezoneOffset() !== 0 || new Date(2026, 6, 15).getTimezoneOffset() !== 0) {
  throw new Error("run with TZ=UTC: the grid's clocks are UTC wall times");
}

/* formats.js in a context of its own (and whether that context has Intl.DurationFormat); without: Intl constructors
 * taken out of that context's Intl first. */
function formatsIn(without) {
  const box = {};
  box.window = box;
  vm.createContext(box);
  for (const name of without) {
    vm.runInContext("delete Intl." + name + ";", box);
  }
  vm.runInContext(fs.readFileSync(path.join(webui, "formats.js"), "utf8"), box, { filename: "formats.js" });
  return { formats: box.MonitorFormats, durationFormat: vm.runInContext("typeof Intl.DurationFormat", box) === "function" };
}

const M = formatsIn([]).formats;
const units = formatsIn(["DurationFormat"]);       // D14: every duration through NumberFormat's units and a unit list
const UNITS = units.formats;
const at = (day, hour, minute) => Date.UTC(2026, 9, 19 + day, hour, minute);      // day 0 is Monday 2026-10-19

const out = {
  versions: { node: process.version, icu: process.versions.icu, cldr: process.versions.cldr, unicode: process.versions.unicode,
    durationFormat: typeof Intl.DurationFormat === "function", unitsWithDurationFormat: units.durationFormat },
  tags: {},
};
for (const tag of tags) {
  const row = {};
  const make = (formats, clock) => formats.make({ setting: "system", language: tag.split("-")[0], system: "en", format: tag,
    clock, offered: ["en"] });
  for (const clock of [24, 12]) {
    const F = make(M, clock);
    if (clock === 24) {
      const U = make(UNITS, clock);
      row.tag = F.tag;
      row.number = grid.numbers.map((d) => F.number(d)).concat(grid.ungrouped.map((d) => F.number(d, { group: false })));
      row.percent = grid.percents.map((d) => F.percent(d)).concat(grid.tight.map((d) => F.percent(d, { tight: true })));
      row.usd = grid.amounts.map((a) => F.usd(a.toFixed(2)));
      row.usdTight = grid.amounts.map((a) => F.usd(a.toFixed(2), { tight: true }));
      row.moneyUsd = grid.amounts.map((a) => F.money(a, "USD"));
      row.moneyCode = grid.amounts.map((a) => F.money(a, grid.code));
      row.weekday = [0, 1, 2, 3, 4, 5, 6].map((i) => F.weekday(i));
      row.month = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11].map((i) => F.month(i));
      row.duration = grid.durations.map((parts) => F.duration(parts));
      row.durationUnits = grid.durations.map((parts) => U.duration(parts));
      row.upper = grid.cases.map((text) => F.upper(text));
      row.lower = grid.cases.map((text) => F.lower(text));
    }
    row["clock" + clock] = grid.times.map(([h, m]) => F.clock(at(0, h, m)));
    row["dayClock" + clock] = grid.dayTimes.map(([day, h, m]) => F.dayClock(at(day, h, m)));
  }
  out.tags[tag] = row;
}
// ASCII only, so a U+00A0 or a U+202F shows as its escape.
process.stdout.write(JSON.stringify(out).replace(/[\u007f-\uffff]/g,
  (ch) => "\\u" + ch.charCodeAt(0).toString(16).padStart(4, "0")) + "\n");
