"use strict";
/* The letter case a look's stylesheets give an element: text-transform as the browser's cascade decides it (#97, #98).
 *
 * A look's sheets (its theme sheet, motion.css and the three motion sheets, as index.html links them) are read rule by rule.
 * For an element, the author rules whose selectors match it (tests/mini_dom.js's matching, :root read as html) give their
 * text-transform by importance, specificity and order. Without one, a button, an input, a select or a textarea takes the
 * browser's own sheet's `text-transform: none` (Chromium's html.css), and any other element its parent's value (the property
 * is inherited), as a browser's cascade does. A text-transform inside an at-rule, on ::first-letter or ::first-line, or behind a
 * pseudo-class this cannot judge is an error, so no rule is skipped unread.
 *
 * Used by tests/webui_checks.js (the update texts, #97) and tests/page_words.js (the page's English in every look, #98).
 */
const fs = require("fs");
const path = require("path");
const DOM = require("./mini_dom.js");

const AS_DRAWN_FALSE = new Set(["hover", "focus", "focus-visible", "focus-within", "active", "empty"]);  // never true of these texts as drawn
const SHEETS = (theme) => ["themes/" + theme + ".css", "motion.css", "themes/nostromo.motion.css", "themes/night_city.motion.css",
  "themes/gates.motion.css"];
// The elements the browser's own sheet resets to text-transform: none (they do not inherit their parent's case).
const UA_RESET = new Set(["button", "input", "select", "textarea"]);

function splitTopLevel(text, sep) {
  const out = [];
  let depth = 0;
  let quote = null;
  let start = 0;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quote) { if (c === quote) { quote = null; } continue; }
    if (c === '"' || c === "'") { quote = c; } else if (c === "(" || c === "[") { depth++; } else if (c === ")" || c === "]") { depth--; }
    else if (c === sep && depth === 0) { out.push(text.slice(start, i)); start = i + 1; }
  }
  out.push(text.slice(start));
  return out.map((part) => part.trim()).filter(Boolean);
}

function declared(body, property) {
  let found = null;
  for (const declaration of splitTopLevel(body, ";")) {
    const colon = declaration.indexOf(":");
    if (colon > 0 && declaration.slice(0, colon).trim().toLowerCase() === property) {
      const value = declaration.slice(colon + 1).trim();
      const important = /!\s*important$/i.test(value);
      if (!found || important || !found.important) {
        found = { value: value.replace(/!\s*important$/i, "").trim().toLowerCase(), important };
      }
    }
  }
  return found;
}

function specificity(selector) {
  let a = 0;
  let b = 0;
  let c = 0;
  let rest = selector.replace(/\[[^\]]*\]/g, () => { b += 1; return " "; });
  rest = rest.replace(/:not\(([^()]*)\)/g, (whole, inner) => { const [x, y, z] = specificity(inner); a += x; b += y; c += z; return " "; });
  rest = rest.replace(/::?(before|after|placeholder|selection|marker|backdrop)\b/g, () => { c += 1; return " "; });
  rest = rest.replace(/#[\w-]+/g, () => { a += 1; return " "; });
  rest = rest.replace(/\.[\w-]+/g, () => { b += 1; return " "; });
  rest = rest.replace(/:[\w-]+/g, () => { b += 1; return " "; });
  rest.replace(/(^|[\s>+~])([A-Za-z][\w-]*)/g, (whole) => { c += 1; return whole; });
  return [a, b, c];
}

function caseRule(selector, where) {
  const bare = selector.replace(/\[[^\]]*\]/g, "");
  if (/::?(first-letter|first-line)\b/.test(bare)) {
    throw new Error(where + ": " + selector + " sets the case of a first letter or line, which the case check cannot judge");
  }
  if (/::?(before|after|placeholder|selection|marker|backdrop)\b/.test(bare)) {
    return null;                                               // generated text, not the element's own
  }
  for (const m of bare.matchAll(/(?<!:):([\w-]+)/g)) {
    if (m[1] !== "not" && m[1] !== "root" && !AS_DRAWN_FALSE.has(m[1])) {
      throw new Error(where + ": " + selector + " has :" + m[1] + ", which the case check cannot judge");
    }
  }
  return { test: DOM.compile(selector.replace(/:root\b/g, "html")), spec: specificity(selector) };
}

/* Every rule of a theme's look that sets text-transform: {test, spec, selector, sheet, value, important, order}. */
function caseRules(webui, theme) {
  const rules = [];
  for (const sheet of SHEETS(theme)) {
    const css = fs.readFileSync(path.join(webui, sheet), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
    const block = (text, at) => {
      let i = 0;
      while (i < text.length) {
        const open = text.indexOf("{", i);
        if (open < 0) { break; }
        const prelude = text.slice(i, open).trim();
        let depth = 0;
        let j = open;
        for (; j < text.length; j++) {
          if (text[j] === "{") { depth += 1; } else if (text[j] === "}") { depth -= 1; if (depth === 0) { break; } }
        }
        const body = text.slice(open + 1, j);
        i = j + 1;
        if (prelude.startsWith("@")) {
          if (/^@(media|supports|container|layer|scope)\b/.test(prelude)) { block(body, prelude); }
          continue;                                            // @keyframes, @font-face: no element's case
        }
        const transform = declared(body, "text-transform");
        if (transform === null) { continue; }
        if (at) { throw new Error(sheet + ": a text-transform inside " + at + " (" + prelude + "), which the case check cannot judge"); }
        for (const selector of splitTopLevel(prelude, ",")) {
          const rule = caseRule(selector, sheet);
          if (rule) { rules.push({ ...rule, selector, sheet, value: transform.value, important: transform.important, order: rules.length }); }
        }
      }
    };
    block(css, "");
  }
  return rules;
}

/* The author rule that wins on node itself, or null. */
function winner(node, rules) {
  const rank = (r) => [r.important ? 1 : 0, ...r.spec, r.order];
  const higher = (x, y) => { for (let i = 0; i < x.length; i++) { if (x[i] !== y[i]) { return x[i] > y[i]; } } return false; };
  let best = null;
  for (const rule of rules) {
    if (rule.test(node) && (!best || higher(rank(rule), rank(best)))) { best = rule; }
  }
  return best && best.value !== "inherit" && best.value !== "unset" ? best : null;
}

/* The case el is drawn in: {value, by}. value is "none", "uppercase" or another text-transform; by names the rule (with
 * " (inherited)" when it is an ancestor's) or the browser's own sheet. memo, a Map, may hold what earlier calls found. */
function caseOf(el, rules, memo) {
  const chain = [];                                            // the elements that take their case from an ancestor
  let found = null;
  let node = el;
  for (; node && node.nodeType === 1; node = node.parentNode) {
    if (memo && memo.has(node)) {
      found = memo.get(node);
      break;
    }
    const best = winner(node, rules);
    if (best) {
      found = { value: best.value === "initial" ? "none" : best.value, by: best.sheet + " " + best.selector };
    } else if (UA_RESET.has(node.localName)) {
      found = { value: "none", by: "the browser's own sheet (" + node.localName + ")" };
    }
    if (found) {
      if (memo) { memo.set(node, found); }
      break;
    }
    chain.push(node);
  }
  found = found || { value: "none", by: "" };
  const inherited = { value: found.value, by: found.by ? found.by.replace(/ \(inherited\)$/, "") + " (inherited)" : "" };
  if (memo) { chain.forEach((n) => memo.set(n, inherited)); }
  return chain.length ? inherited : found;
}

/* text as an element drawn in `value` shows it (English: the case of the text's own language, which the page's records are). */
function shownIn(text, value) {
  return value === "uppercase" ? text.toUpperCase() : value === "lowercase" ? text.toLowerCase() : text;
}

module.exports = { caseRules, caseOf, shownIn, SHEETS, UA_RESET };
