/* charts.js: dependency-free SVG charts for the dashboard, plus the tooltip and keyboard helpers they share.
 *
 * Every builder returns an SVG string. Nothing from the API is put into the markup unescaped. Charts
 * that carry text are built at the pixel width of the place they live in (app.js measures the host and
 * passes `width`), so text is the same size at 1280 px and at 1920 px and cells fill the space. The
 * SVG itself is `width: 100%` with a viewBox of that width, so it still scales down cleanly if the
 * host shrinks before the next build.
 *
 * Colours: classes only. A small base stylesheet (injected once, first in <head> so a theme's CSS wins)
 * maps each class to a CSS variable, so a theme change repaints every chart without a rebuild. The
 * chart variables fall back to the theme tokens that every theme defines:
 *
 *   --chart-mark    primary marks: bars, heat cells, lit pips   (fallback --accent)
 *   --chart-mark-2  second series                                (fallback --accent-alt)
 *   --chart-hot     highlight: today, hovered or focused item   (fallback --text-bright)
 *   --chart-line    line series                                  (fallback --accent)
 *   --chart-text    axis and label text                          (fallback --text-dim)
 *   --chart-text-strong  values                                  (fallback --text)
 *   --chart-track   empty cells, tracks                          (fallback --track)
 *   --chart-grid    grid lines                                   (fallback --border-dim)
 *   --chart-limit   the 100 % line                               (fallback --alert)
 *   --chart-pips    "1" draws bars as separate pips (segments); anything else draws solid bars
 *
 * SVG classes (a theme may restyle any of them): cum-chart (root) cum-t (text; is-end, is-mid, is-strong)
 * cum-track cum-mark cum-mark-2 cum-hot cum-line cum-ideal cum-fore cum-dot cum-grid (is-limit) cum-now
 * cum-cell (cum-l0 .. cum-l4 heat levels, is-today) cum-pip cum-col cum-row cum-seg (cum-s-main, cum-s-alt,
 * cum-s-dim) cum-item (anything that takes a tooltip and keyboard focus) cum-hit (invisible hit area).
 *
 * Accessibility: an item is `.cum-item` with an aria-label (the tooltip text) and a roving tabindex, so
 * one Tab stop enters a chart and the arrow keys move between its items; the tooltip shows on hover and
 * on focus. bind() wires the tooltip and the arrow keys once for a whole container.
 */
(function (root) {
  "use strict";

  var doc = root.document;
  var pipsOn = false;
  var MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
  // One character of chart text in px, generously: a theme may uppercase and space it (Nostromo's is 7.7). Only the
  // estimate: textWidth() asks the page for the real width and falls back to this where there is no page to ask.
  var CHAR_W = 7.9;

  var STYLE = [
    "[hidden]{display:none!important}",
    ".nm-tip{position:fixed;left:0;top:0;z-index:60;max-width:340px}",
    ".cum-chart{display:block;width:100%;height:auto;overflow:visible}",
    ".cum-t{font:500 11px/1 var(--font-body,system-ui,sans-serif);fill:var(--chart-text,var(--text-dim,currentColor));letter-spacing:.04em}",
    ".cum-t.is-strong{fill:var(--chart-text-strong,var(--text,currentColor))}",
    ".cum-t.is-mid{text-anchor:middle}.cum-t.is-end{text-anchor:end}",
    ".cum-track{fill:var(--chart-track,var(--track,currentColor))}",
    ".cum-mark{fill:var(--chart-mark,var(--accent,currentColor))}",
    ".cum-mark-2{fill:var(--chart-mark-2,var(--accent-alt,currentColor))}",
    ".cum-hot{fill:var(--chart-hot,var(--text-bright,currentColor))}",
    ".cum-s-main{fill:var(--chart-mark,var(--accent,currentColor))}",
    ".cum-s-alt{fill:var(--chart-mark-2,var(--accent-alt,currentColor))}",
    ".cum-s-dim{fill:var(--chart-track,var(--track,currentColor))}",
    ".cum-line{fill:none;stroke:var(--chart-line,var(--accent,currentColor));stroke-width:1.6;stroke-linejoin:round}",
    ".cum-ideal{fill:none;stroke:var(--chart-text,var(--text-dim,currentColor));stroke-width:1;stroke-dasharray:4 4;opacity:.8}",
    ".cum-fore{fill:none;stroke:var(--chart-line,var(--accent,currentColor));stroke-width:1.4;stroke-dasharray:2 4;opacity:.9}",
    ".cum-dot{fill:var(--chart-hot,var(--text-bright,currentColor));stroke:none}",
    ".cum-dot.is-hollow{fill:none;stroke:var(--chart-hot,var(--text-bright,currentColor));stroke-width:1.4}",
    ".cum-grid{fill:none;stroke:var(--chart-grid,var(--border-dim,currentColor));stroke-width:1;stroke-dasharray:1 3}",
    ".cum-grid.is-limit{stroke:var(--chart-limit,var(--alert,currentColor));stroke-dasharray:6 3;opacity:.7}",
    ".cum-now{stroke:var(--chart-hot,var(--text-bright,currentColor));stroke-width:1;stroke-dasharray:2 2}",
    ".cum-cell.cum-l0{fill:var(--chart-track,var(--track,currentColor))}",
    ".cum-cell.cum-l1,.cum-cell.cum-l2,.cum-cell.cum-l3,.cum-cell.cum-l4{fill:var(--chart-mark,var(--accent,currentColor))}",
    ".cum-cell.cum-l1{fill-opacity:.3}.cum-cell.cum-l2{fill-opacity:.52}.cum-cell.cum-l3{fill-opacity:.76}.cum-cell.cum-l4{fill-opacity:1}",
    ".cum-cell.is-today{stroke:var(--chart-hot,var(--text-bright,currentColor));stroke-width:1.5}",
    ".cum-hit{fill:transparent;stroke:none}",
    ".cum-ring-focus{display:none;fill:none;stroke:var(--chart-hot,var(--text-bright,currentColor));stroke-width:1.5}",
    ".cum-item{outline:none}",
    ".cum-cell.cum-item:hover,.cum-cell.cum-item:focus,.cum-dot.cum-item:focus,.cum-dot.cum-item:hover{stroke:var(--chart-hot,var(--text-bright,currentColor));stroke-width:1.8}",
    ".cum-seg.cum-item:hover,.cum-seg.cum-item:focus{stroke:var(--chart-hot,var(--text-bright,currentColor));stroke-width:2}",
    ".cum-col:hover .cum-pip,.cum-col:focus .cum-pip,.cum-col:hover .cum-mark,.cum-col:focus .cum-mark,.cum-row:hover .cum-mark,.cum-row:focus .cum-mark{fill:var(--chart-hot,var(--text-bright,currentColor))}",
    ".cum-col:focus .cum-ring-focus,.cum-row:focus .cum-ring-focus{display:inline}"
  ].join("\n");

  function installStyle() {
    if (!doc || doc.getElementById("cum-charts-style")) {
      return;
    }
    var el = doc.createElement("style");
    el.id = "cum-charts-style";
    el.textContent = STYLE;
    var head = doc.head || doc.documentElement;
    head.insertBefore(el, head.firstChild);
  }

  // ── helpers ───────────────────────────────────────────────────────────────────

  function num(v, fallback) {
    var n = Number(v);
    return isFinite(n) ? n : (fallback || 0);
  }

  function clamp(v, lo, hi) {
    return Math.max(lo, Math.min(hi, v));
  }

  function f1(v) {
    return (Math.round(v * 10) / 10).toString();
  }

  function f2(v) {
    return (Math.round(v * 100) / 100).toString();
  }

  function esc(value) {
    return String(value === null || value === undefined ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // ── measuring text ────────────────────────────────────────────────────────────

  var SVG_NS = "http://www.w3.org/2000/svg";
  var probe = null;
  var measured = {};

  /* The px width of one chart text as the page draws it (this theme's font, size, spacing and case). It asks an
   * invisible <text> inside an <svg> of the chart's own class, so the theme's rules for that chart apply to it.
   * `chart` is the SVG's chart class ("cum-hbars", "cum-columns", "cum-calendar") and `strong` says the text is a
   * value (cum-t is-strong). Without a page to ask (the script tests, no <body> yet) the answer is the estimate,
   * CHAR_W a character. Answers are kept until refresh(), which the page calls before every render pass. */
  function textWidth(text, chart, strong) {
    var s = String(text === null || text === undefined ? "" : text);
    var key = chart + "|" + (strong ? "s" : "n") + "|" + s;
    if (measured[key] === undefined) {
      var w = s ? ask(s, chart, strong) : 0;
      measured[key] = w >= 0 ? w : s.length * CHAR_W;
    }
    return measured[key];
  }

  function ask(text, chart, strong) {
    if (!doc || !doc.body || typeof doc.createElementNS !== "function") {
      return -1;
    }
    try {
      if (!probe) {
        var svg = doc.createElementNS(SVG_NS, "svg");
        svg.setAttribute("aria-hidden", "true");
        svg.setAttribute("style", "position:absolute;left:-9999px;top:0;width:0;height:0;overflow:visible;visibility:hidden;pointer-events:none");
        probe = doc.createElementNS(SVG_NS, "text");
        svg.appendChild(probe);
        doc.body.appendChild(svg);
      }
      probe.parentNode.setAttribute("class", "cum-chart " + chart);
      probe.setAttribute("class", strong ? "cum-t is-strong" : "cum-t");
      probe.textContent = text;
      var w = probe.getComputedTextLength();
      return w > 0 ? w : -1;
    } catch (e) {
      return -1;
    }
  }

  /* A label cut to at most `maxPx` wide. A label that does not fit loses its middle, not its end: the end is
   * the part that tells "se-infolcd-apex-a" from "se-infolcd-apex-u". */
  function fitLabel(text, maxPx, chart) {
    var s = String(text === null || text === undefined ? "" : text);
    if (s.length < 3 || textWidth(s, chart, false) <= maxPx) {
      return s;
    }
    var lo = 1, hi = s.length - 2, best = 1;
    while (lo <= hi) {
      var mid = (lo + hi) >> 1;
      if (textWidth(middleCut(s, mid), chart, false) <= maxPx) {
        best = mid;
        lo = mid + 1;
      } else {
        hi = mid - 1;
      }
    }
    return middleCut(s, best);
  }

  /* `keep` characters of `s` around an ellipsis: a little more of the start than of the end. */
  function middleCut(s, keep) {
    var head = Math.ceil(keep * 0.55);
    return s.slice(0, head) + "…" + s.slice(s.length - (keep - head));
  }

  /* Re-reads --chart-pips from the page. app.js calls it before every render pass. */
  function refresh() {
    measured = {};
    try {
      var v = root.getComputedStyle(doc.documentElement).getPropertyValue("--chart-pips");
      pipsOn = String(v).trim() === "1";
    } catch (e) {
      pipsOn = false;
    }
    return pipsOn;
  }

  /* The smallest 1 / 2 / 2.5 / 5 x 10^k that is at least `raw`: the value of one pip. */
  function niceStep(raw) {
    var r = num(raw, 0);
    if (r <= 0) {
      return 1;
    }
    var pow = Math.pow(10, Math.floor(Math.log(r) / Math.LN10));
    var steps = [1, 2, 2.5, 5, 10];
    for (var i = 0; i < steps.length; i++) {
      if (steps[i] * pow >= r * 0.9999) {
        return steps[i] * pow;
      }
    }
    return 10 * pow;
  }

  function open(cls, w, h, label, o) {
    o = o || {};
    return '<svg class="cum-chart ' + cls + '" viewBox="0 0 ' + f1(w) + " " + f1(h) + '" preserveAspectRatio="' + (o.par || "xMinYMin meet") +
      '" role="' + (o.role || "img") + '" aria-label="' + esc(label || "chart") + '"' + (o.nav ? ' data-nav="' + o.nav + '"' : "") +
      (o.cols ? ' data-cols="' + o.cols + '"' : "") + ">";
  }

  /* Attributes that make a drawn element a tooltip and keyboard stop (a roving tabindex: one Tab stop per chart). */
  function item(cls, tip, index, tabbable, r, c) {
    return ' class="' + cls + ' cum-item" tabindex="' + (tabbable ? 0 : -1) + '" role="img" aria-label="' + esc(tip || "") + '" data-i="' + index + '"' +
      (r !== undefined ? ' data-r="' + r + '" data-c="' + c + '"' : "");
  }

  function parseYmd(text) {
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(text || ""));
    return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null;
  }

  /* Heat level 0..4 for each value: zero is level 0; the rest follow the quartiles of the non-zero values,
   * but never below a linear share of the largest, so the busiest cell is always the strongest. */
  function levels(values) {
    var pos = values.filter(function (v) { return v > 0; }).sort(function (a, b) { return a - b; });
    var top = pos.length ? pos[pos.length - 1] : 0;
    function q(p) {
      return pos.length ? pos[Math.min(pos.length - 1, Math.floor(p * pos.length))] : 0;
    }
    var q1 = q(0.25), q2 = q(0.5), q3 = q(0.75);
    return values.map(function (v) {
      if (!(v > 0)) {
        return 0;
      }
      var byRank = v <= q1 ? 1 : v <= q2 ? 2 : v <= q3 ? 3 : 4;
      return Math.max(byRank, Math.min(4, Math.ceil(4 * v / top)));
    });
  }

  // ── sparkline: a line through values, a dot on each point ────────────────────

  /* opts: width, height, pad, min, max, dots, label, tips (one string per point) */
  function sparkline(values, opts) {
    opts = opts || {};
    var w = num(opts.width, 300), h = num(opts.height, 58), pad = num(opts.pad, 4);
    var list = (values || []).map(function (v) { return num(v, 0); });
    var lo = opts.min !== undefined ? num(opts.min) : Math.min.apply(null, list.concat([0]));
    var hi = opts.max !== undefined ? num(opts.max) : Math.max.apply(null, list.concat([lo + 1]));
    var span = hi - lo || 1;
    var base = h - pad, top = pad + 8;
    var pts = list.map(function (v, i) {
      var x = list.length > 1 ? pad + i * (w - 2 * pad) / (list.length - 1) : w / 2;
      var y = base - (clamp(v, lo, hi) - lo) / span * (base - top);
      return [x, y];
    });
    var tips = opts.tips || null;
    var out = open("cum-spark", w, h, opts.label || "trend", { par: "none", role: tips ? "group" : "img", nav: tips ? "list" : "" });
    [top, (top + base) / 2, base].forEach(function (y) {
      out += '<line class="cum-grid" x1="0" x2="' + f1(w) + '" y1="' + f1(y) + '" y2="' + f1(y) + '" vector-effect="non-scaling-stroke"/>';
    });
    if (pts.length > 1) {
      out += '<polyline class="cum-line" vector-effect="non-scaling-stroke" points="' +
        pts.map(function (p) { return f1(p[0]) + "," + f1(p[1]); }).join(" ") + '"/>';
    }
    if (opts.dots !== false) {
      out += pts.map(function (p, i) {
        var tip = tips && tips[i] !== undefined ? tips[i] : null;
        return '<circle cx="' + f1(p[0]) + '" cy="' + f1(p[1]) + '" r="1.8"' +
          (tip !== null ? item("cum-dot", tip, i, i === pts.length - 1) : ' class="cum-dot"') + "/>";
      }).join("");
    }
    return out + "</svg>";
  }

  // ── line: time series with markers, a forecast and a "now" line ──────────────

  /* opts: width, height, label, xMin, xMax, yMin, yMax,
   *   yTicks [{v, label, limit}], xTicks [{t, label}], marks [{t, label}],
   *   series [{kind: "actual" | "forecast" | "ideal", points: [[t, v], ...], tips: [..] or marks: [{i, tip}], markers,
   *            endLabel, endTip (a forecast only: text above its last point, which is where it reaches the limit, and the
   *            tooltip that point takes)}]
   * t is any number (ms); v is in the y domain. Points outside the domain are clamped to its edge. */
  function line(opts) {
    opts = opts || {};
    var w = Math.max(200, num(opts.width, 560)), h = Math.max(90, num(opts.height, 170));
    var left = opts.yTicks && opts.yTicks.length ? 44 : 12, right = 14, top = 14, bottom = opts.xTicks && opts.xTicks.length ? 26 : 10;
    var pw = w - left - right, ph = h - top - bottom;
    var x0 = num(opts.xMin, 0), x1 = num(opts.xMax, 1);
    if (x1 <= x0) {
      x1 = x0 + 1;
    }
    var y0 = num(opts.yMin, 0), y1 = num(opts.yMax, 100);
    if (y1 <= y0) {
      y1 = y0 + 1;
    }
    function X(t) { return left + clamp((num(t) - x0) / (x1 - x0), 0, 1) * pw; }
    function Y(v) { return top + ph - clamp((num(v) - y0) / (y1 - y0), 0, 1) * ph; }
    var series = opts.series || [];
    var hasItems = series.some(function (s) { return (s.tips && s.tips.length) || (s.marks && s.marks.length) || s.endTip; });
    var out = open("cum-linechart", w, h, opts.label || "line chart", { role: hasItems ? "group" : "img", nav: hasItems ? "list" : "" });
    (opts.yTicks || []).forEach(function (t) {
      var y = Y(t.v);
      out += '<line class="cum-grid' + (t.limit ? " is-limit" : "") + '" x1="' + f1(left) + '" x2="' + f1(w - right) + '" y1="' + f1(y) + '" y2="' + f1(y) + '"/>' +
        '<text class="cum-t is-end" x="' + f1(left - 8) + '" y="' + f1(y + 4) + '">' + esc(t.label) + "</text>";
    });
    var ticks = opts.xTicks || [];
    ticks.forEach(function (t, i) {
      var x = X(t.t);
      var anchor = i === 0 ? "" : i === ticks.length - 1 ? " is-end" : " is-mid";
      out += '<line class="cum-grid" x1="' + f1(x) + '" x2="' + f1(x) + '" y1="' + f1(top) + '" y2="' + f1(top + ph) + '"/>' +
        '<text class="cum-t' + anchor + '" x="' + f1(x) + '" y="' + f1(h - 8) + '">' + esc(t.label) + "</text>";
    });
    function pathOf(points) {
      return points.map(function (p) { return f1(X(p[0])) + "," + f1(Y(p[1])); }).join(" ");
    }
    series.forEach(function (s) {
      var pts = s.points || [];
      if (pts.length > 1 && s.kind === "ideal") {
        out += '<polyline class="cum-ideal" points="' + pathOf(pts) + '"/>';
      }
    });
    series.forEach(function (s) {
      var pts = s.points || [];
      if (pts.length > 1 && s.kind === "forecast") {
        out += '<polyline class="cum-fore" points="' + pathOf(pts) + '"/>';
      }
    });
    series.forEach(function (s) {
      var pts = s.points || [];
      if (s.kind === "ideal" || s.kind === "forecast") {
        return;
      }
      if (pts.length > 1) {
        out += '<polyline class="cum-line" points="' + pathOf(pts) + '"/>';
      }
    });
    (opts.marks || []).forEach(function (m) {
      var x = X(m.t);
      out += '<line class="cum-now" x1="' + f1(x) + '" x2="' + f1(x) + '" y1="' + f1(top - 4) + '" y2="' + f1(top + ph) + '"/>';
      if (m.label) {
        var edge = x > w - right - 40;
        out += '<text class="cum-t is-strong' + (edge ? " is-end" : "") + '" x="' + f1(edge ? x - 4 : x + 4) + '" y="' + f1(top + 18) + '">' + esc(m.label) + "</text>";
      }
    });
    var counter = 0;
    series.forEach(function (s) {
      var pts = s.points || [];
      if (s.kind === "ideal" || s.markers === false) {
        return;
      }
      if (s.kind === "forecast") {
        var last = pts[pts.length - 1];
        if (last) {
          var ex = X(last[0]), ey = Y(last[1]);
          out += '<circle cx="' + f1(ex) + '" cy="' + f1(ey) + '" r="3.4"' +
            (s.endTip ? item("cum-dot is-hollow", s.endTip, counter, false) : ' class="cum-dot is-hollow"') + "/>";
          if (s.endTip) {
            counter++;
          }
          if (s.endLabel) {
            /* above the plot, centred on the point and kept inside the chart, so it never lies on the line it names */
            var tw = textWidth(s.endLabel, "cum-linechart", true);
            var tx = clamp(ex, tw / 2, w - tw / 2);
            out += '<text class="cum-t is-strong is-mid" x="' + f1(tx) + '" y="' + f1(Math.max(10, ey - 6)) + '">' + esc(s.endLabel) + "</text>";
          }
        }
        return;
      }
      /* marks: [{i, tip}] picks the points that get a marker; without it every point with a tip does. */
      var list = s.marks || pts.map(function (p, i) { return { i: i, tip: s.tips && s.tips[i] !== undefined ? s.tips[i] : null }; });
      list.forEach(function (m, k) {
        var p = pts[m.i];
        if (!p) {
          return;
        }
        var tip = m.tip === undefined ? null : m.tip;
        out += '<circle cx="' + f1(X(p[0])) + '" cy="' + f1(Y(p[1])) + '" r="' + (tip !== null ? "2.8" : "2.2") + '"' +
          (tip !== null ? item("cum-dot", tip, counter, k === list.length - 1) : ' class="cum-dot"') + "/>";
        if (tip !== null) {
          counter++;
        }
      });
    });
    return out + "</svg>";
  }

  // ── columns: vertical bars, optionally as stacks of pips ─────────────────────

  /* opts: width, height, max, pips, pipRows, step, gutterRight, labels [{i, text}] (most important first), avg {value, label},
   *   tips [..], current (index to highlight), label.
   * With pips on, each column is a stack of `step`-sized pips (nearest pip, at least one for a non-zero value). */
  function columns(values, opts) {
    opts = opts || {};
    var w = Math.max(120, num(opts.width, 300)), h = Math.max(60, num(opts.height, 140));
    var list = (values || []).map(function (v) { return Math.max(0, num(v, 0)); });
    var n = Math.max(list.length, 1);
    var max = opts.max !== undefined ? num(opts.max) : Math.max.apply(null, list.concat([1e-9]));
    var avgLabel = opts.avg && opts.avg.value > 0 && opts.avg.label ? String(opts.avg.label) : "";
    var gutter = Math.max(num(opts.gutterRight, 0), avgLabel ? Math.ceil(textWidth(avgLabel, "cum-columns", true)) + 8 : 0);
    var topPad = 4, axis = opts.labels && opts.labels.length ? 18 : 2;
    var ph = h - topPad - axis;
    var pitch = (w - gutter) / n;
    var gap = Math.max(1, Math.min(4, pitch * 0.16));
    var cw = Math.max(1, pitch - gap);
    var pips = opts.pips === undefined ? pipsOn : !!opts.pips;
    var rows = num(opts.pipRows, 14);
    var step = num(opts.step) || niceStep(max / rows);
    var vpitch = ph / rows;
    var tips = opts.tips || [];
    var current = opts.current === undefined ? n - 1 : opts.current;
    var out = open("cum-columns", w, h, opts.label || "columns", { role: tips.length ? "group" : "img", nav: tips.length ? "list" : "" });
    list.forEach(function (v, i) {
      var x = i * pitch + gap / 2;
      var tip = tips[i];
      out += "<g" + (tip !== undefined ? item("cum-col", tip, i, i === current) : ' class="cum-col"') + ">";
      out += '<rect class="cum-hit" x="' + f1(i * pitch) + '" y="0" width="' + f1(pitch) + '" height="' + f1(topPad + ph) + '"/>';
      var cls = i === current ? "cum-hot" : "cum-mark";
      if (pips) {
        var lit = v > 0 ? clamp(Math.round(v / step), 1, rows) : 0;
        for (var p = 0; p < rows; p++) {
          var y = topPad + ph - (p + 1) * vpitch + 1;
          out += '<rect class="' + (p < lit ? cls + " cum-pip" : "cum-track cum-pip") + '" x="' + f1(x) + '" y="' + f1(y) + '" width="' + f1(cw) + '" height="' + f1(Math.max(1, vpitch - 2)) + '"/>';
        }
      } else {
        out += '<rect class="cum-track" x="' + f1(x) + '" y="' + f1(topPad) + '" width="' + f1(cw) + '" height="' + f1(ph) + '" opacity=".35"/>';
        var bh = max > 0 ? Math.min(1, v / max) * ph : 0;
        if (v > 0) {
          out += '<rect class="' + cls + '" x="' + f1(x) + '" y="' + f1(topPad + ph - Math.max(1, bh)) + '" width="' + f1(cw) + '" height="' + f1(Math.max(1, bh)) + '"/>';
        }
      }
      if (tip !== undefined) {
        out += '<rect class="cum-ring-focus" x="' + f1(i * pitch) + '" y="0" width="' + f1(pitch) + '" height="' + f1(topPad + ph) + '"/>';
      }
      out += "</g>";
    });
    if (opts.avg && opts.avg.value > 0) {
      var ay = pips ? topPad + ph - opts.avg.value / step * vpitch : topPad + ph - Math.min(1, opts.avg.value / max) * ph;
      ay = clamp(ay, topPad, topPad + ph);
      out += '<line class="cum-now" x1="0" x2="' + f1(w - gutter) + '" y1="' + f1(ay) + '" y2="' + f1(ay) + '"/>';
      if (opts.avg.label) {
        out += '<text class="cum-t is-strong" x="' + f1(w - gutter + 6) + '" y="' + f1(ay + 4) + '">' + esc(opts.avg.label) + "</text>";
      }
    }
    /* Axis labels, most important first: each sits under its column, is pushed back inside the chart at either
     * edge, and is dropped when it would touch a label already placed. */
    var placed = [];
    (opts.labels || []).forEach(function (l) {
      var tw = textWidth(l.text, "cum-columns", false);
      var cx = l.i * pitch + pitch / 2;
      var left = cx - tw / 2, anchor = " is-mid", x = cx;
      if (left < 0) {
        left = 0;
        anchor = "";
        x = 0;
      } else if (left + tw > w - gutter) {
        left = w - gutter - tw;
        anchor = " is-end";
        x = w - gutter;
      }
      var clear = placed.every(function (b) { return left >= b.r + 8 || left + tw <= b.l - 8; });
      if (clear) {
        placed.push({ l: left, r: left + tw, x: x, anchor: anchor, text: l.text });
      }
    });
    placed.forEach(function (b) {
      out += '<text class="cum-t' + b.anchor + '" x="' + f1(b.x) + '" y="' + f1(h - 4) + '">' + esc(b.text) + "</text>";
    });
    return out + "</svg>";
  }

  /* The old name: vertical columns without the extras. */
  function bars(values, opts) {
    return columns(values, Object.assign({ pips: false }, opts || {}));
  }

  // ── hbars: labelled horizontal bars ──────────────────────────────────────────

  /* rows: [{label, value, text, tip, alt}]. opts: width, rowHeight, labelWidth, valueWidth, max, pips, label.
   * `text` is the right-hand value; `tip` is the tooltip and the accessible name; `alt` uses the second colour. */
  function hbars(rows, opts) {
    opts = opts || {};
    rows = rows || [];
    var w = Math.max(220, num(opts.width, 560)), rowH = num(opts.rowHeight, 24), barH = num(opts.barHeight, 10);
    var longest = rows.reduce(function (a, r) { return Math.max(a, textWidth(r.text || "", "cum-hbars", true)); }, 0);
    var labelW = num(opts.labelWidth, clamp(Math.round(w * 0.34), 96, 230)), gap = 10;
    var valueW = Math.max(num(opts.valueWidth, 84), Math.ceil(longest) + 2);             // the value text never runs into the bar
    var barW = Math.max(24, w - labelW - valueW - gap * 2);
    var h = Math.max(rowH, rows.length * rowH + 2);
    var max = opts.max !== undefined ? num(opts.max) : Math.max.apply(null, rows.map(function (r) { return num(r.value, 0); }).concat([1e-9]));
    var pips = opts.pips === undefined ? pipsOn : !!opts.pips;
    var pipW = 6, pipGap = 2;
    var cells = Math.max(4, Math.floor((barW + pipGap) / (pipW + pipGap)));
    var out = open("cum-hbars", w, h, opts.label || "bars", { role: rows.length ? "group" : "img", nav: rows.length ? "list" : "" });
    rows.forEach(function (r, i) {
      var y = i * rowH + 1, cy = y + rowH / 2;
      var v = Math.max(0, num(r.value, 0));
      var frac = max > 0 ? Math.min(1, v / max) : 0;
      var tip = r.tip || (r.label + (r.text ? ": " + r.text : ""));
      var fill = r.alt ? "cum-mark-2" : "cum-mark";
      out += "<g" + item("cum-row", tip, i, i === 0) + ">";
      out += '<rect class="cum-hit" x="0" y="' + f1(y) + '" width="' + f1(w) + '" height="' + f1(rowH) + '"/>';
      out += '<text class="cum-t" x="0" y="' + f1(cy + 4) + '">' + esc(fitLabel(r.label, labelW - 2, "cum-hbars")) + "</text>";
      var bx = labelW + gap, by = cy - barH / 2;
      if (pips) {
        var exact = frac * cells, full = Math.floor(exact), part = exact - full;
        for (var k = 0; k < cells; k++) {
          var px = bx + k * (pipW + pipGap);
          if (k < full) {
            out += '<rect class="' + fill + '" x="' + f1(px) + '" y="' + f1(by) + '" width="' + pipW + '" height="' + barH + '"/>';
          } else if (k === full && part > 0.04) {
            out += '<rect class="cum-track" x="' + f1(px) + '" y="' + f1(by) + '" width="' + pipW + '" height="' + barH + '"/>' +
              '<rect class="' + fill + '" x="' + f1(px) + '" y="' + f1(by) + '" width="' + f1(pipW * part) + '" height="' + barH + '"/>';
          } else {
            out += '<rect class="cum-track" x="' + f1(px) + '" y="' + f1(by) + '" width="' + pipW + '" height="' + barH + '"/>';
          }
        }
      } else {
        out += '<rect class="cum-track" x="' + f1(bx) + '" y="' + f1(by) + '" width="' + f1(barW) + '" height="' + barH + '"/>';
        if (frac > 0) {
          out += '<rect class="' + fill + '" x="' + f1(bx) + '" y="' + f1(by) + '" width="' + f1(Math.max(1.5, barW * frac)) + '" height="' + barH + '"/>';
        }
      }
      out += '<text class="cum-t is-end is-strong" x="' + f1(w) + '" y="' + f1(cy + 4) + '">' + esc(r.text || "") + "</text>";
      out += '<rect class="cum-ring-focus" x="0" y="' + f1(y) + '" width="' + f1(w) + '" height="' + f1(rowH) + '"/></g>';
    });
    return out + "</svg>";
  }

  // ── stacked bar: one bar split into parts ────────────────────────────────────

  /* parts: [{value, cls: "main" | "alt" | "dim", tip}]. opts: width, height, gap, label. No text inside. */
  function stackedBar(parts, opts) {
    opts = opts || {};
    var w = num(opts.width, 300), h = num(opts.height, 16), gap = num(opts.gap, 2);
    var list = (parts || []).map(function (p) { return { value: Math.max(0, num(p.value, 0)), cls: p.cls || "main", tip: p.tip }; });
    var total = list.reduce(function (a, p) { return a + p.value; }, 0);
    var scale = total > 0 ? total : 1;
    var usable = w - gap * Math.max(0, list.length - 1);
    var tipped = list.some(function (p) { return p.tip; });
    var out = open("cum-stack", w, h, opts.label || "share", { par: "none", role: tipped ? "group" : "img", nav: tipped ? "list" : "" });
    out += '<rect class="cum-track" x="0" y="0" width="' + f1(w) + '" height="' + f1(h) + '" opacity=".4"/>';
    var x = 0;
    list.forEach(function (p, i) {
      var pw = p.value / scale * usable;
      out += '<rect' + (p.tip ? item("cum-seg cum-s-" + p.cls, p.tip, i, i === 0) : ' class="cum-seg cum-s-' + p.cls + '"') +
        ' x="' + f1(x) + '" y="0" width="' + f1(Math.max(0, pw)) + '" height="' + f1(h) + '"/>';
      x += pw + gap;
    });
    return out + "</svg>";
  }

  // ── ring: a track and an arc from 12 o'clock, clockwise ──────────────────────

  function ring(percent, opts) {
    opts = opts || {};
    var size = num(opts.size, 72), stroke = num(opts.stroke, 6);
    var r = (size - stroke) / 2, c = size / 2;
    var p = clamp(num(percent, 0), 0, 100);
    var angle = p / 100 * Math.PI * 2;
    var x = c + r * Math.sin(angle), y = c - r * Math.cos(angle);
    var large = p > 50 ? 1 : 0;
    var out = '<svg class="cum-chart cum-ring" viewBox="0 0 ' + size + " " + size + '" role="img" aria-label="' + f1(p) + ' percent">';
    out += '<circle cx="' + c + '" cy="' + c + '" r="' + f1(r) + '" fill="none" stroke-width="' + stroke + '" class="cum-grid" style="stroke-dasharray:none"/>';
    if (p > 0) {
      var d = p >= 100
        ? "M " + c + " " + (c - r) + " A " + r + " " + r + " 0 1 1 " + c + " " + (c + r) + " A " + r + " " + r + " 0 1 1 " + c + " " + (c - r)
        : "M " + c + " " + (c - r) + " A " + f1(r) + " " + f1(r) + " 0 " + large + " 1 " + f1(x) + " " + f1(y);
      out += '<path d="' + d + '" fill="none" stroke-width="' + stroke + '" class="cum-line" style="stroke-width:' + stroke + 'px"/>';
    }
    return out + "</svg>";
  }

  // ── heatmaps ─────────────────────────────────────────────────────────────────

  /* The old generic grid: each value 0..1 sets the strength of the mark colour. */
  function heatmap(grid, opts) {
    opts = opts || {};
    var cell = num(opts.cell, 12), gap = num(opts.gap, 2);
    var rows = grid || [];
    var cols = rows.reduce(function (a, r) { return Math.max(a, r.length); }, 0);
    var w = cols * (cell + gap) - gap, h = rows.length * (cell + gap) - gap;
    var out = '<svg class="cum-chart cum-heat" viewBox="0 0 ' + Math.max(w, 1) + " " + Math.max(h, 1) + '" role="img" aria-label="' + esc(opts.label || "heatmap") + '">';
    rows.forEach(function (row, y) {
      row.forEach(function (v, x) {
        var a = clamp(num(v, 0), 0, 1);
        out += '<rect class="cum-cell cum-l' + (a <= 0 ? 0 : a < 0.25 ? 1 : a < 0.5 ? 2 : a < 0.75 ? 3 : 4) + '" x="' + (x * (cell + gap)) + '" y="' + (y * (cell + gap)) +
          '" width="' + cell + '" height="' + cell + '"/>';
      });
    });
    return out + "</svg>";
  }

  /* Calendar heatmap (weeks in columns, Monday first) from consecutive local days.
   * days: [{date: "YYYY-MM-DD", value, tip}]. opts: width, gap, maxCell, label, legend ("less" / "more" captions). */
  function calendar(days, opts) {
    opts = opts || {};
    days = days || [];
    var first = days.length ? parseYmd(days[0].date) : null;
    if (!first) {
      return open("cum-calendar", 200, 40, opts.label || "calendar") + "</svg>";
    }
    var gap = num(opts.gap, 3), gutter = 34, top = 20;
    var dow0 = (first.getDay() + 6) % 7;
    var cols = Math.ceil((dow0 + days.length) / 7);
    var W = Math.max(260, num(opts.width, 900));
    var cell = clamp(Math.floor((W - gutter + gap) / cols) - gap, 7, num(opts.maxCell, 20));
    var pitch = cell + gap;
    var width = gutter + cols * pitch - gap;
    var legendH = 26;
    var height = top + 7 * pitch - gap + legendH;
    var lv = levels(days.map(function (d) { return num(d.value, 0); }));
    var out = open("cum-calendar", width, height, opts.label || "calendar heatmap", { role: "group", nav: "grid", cols: cols });
    var marks = [];
    days.forEach(function (d, i) {
      var dt = parseYmd(d.date);
      if (dt && dt.getDate() === 1) {
        marks.push({ col: Math.floor((dow0 + i) / 7), dt: dt });
      }
    });
    if (!marks.length) {
      marks.push({ col: 0, dt: first });
    }
    var labelEnd = -1e9;        // a month name is dropped when the one before it (a year is added in January) still reaches there
    marks.forEach(function (m) {
      var name = MONTHS[m.dt.getMonth()] + (m.dt.getMonth() === 0 ? " " + String(m.dt.getFullYear()).slice(2) : "");
      var tw = textWidth(name, "cum-calendar", false);
      var x = Math.min(gutter + m.col * pitch, width - tw);          // a month that starts in the last column is pulled back inside
      if (x >= labelEnd + 6) {
        labelEnd = x + tw;
        out += '<text class="cum-t" x="' + f1(x) + '" y="12">' + name + "</text>";
      }
    });
    ["MON", "WED", "FRI"].forEach(function (name, k) {
      var row = k * 2;
      out += '<text class="cum-t is-end" x="' + (gutter - 8) + '" y="' + f1(top + row * pitch + cell / 2 + 4) + '">' + name + "</text>";
    });
    var startAt = days.length - 1;
    days.forEach(function (d, i) {
      var col = Math.floor((dow0 + i) / 7), row = (dow0 + i) % 7;
      var today = i === days.length - 1;
      out += "<rect" + item("cum-cell cum-l" + lv[i] + (today ? " is-today" : ""), d.tip || d.date, i, i === startAt, row, col) +
        ' x="' + (gutter + col * pitch) + '" y="' + (top + row * pitch) + '" width="' + cell + '" height="' + cell + '"/>';
    });
    var ly = top + 7 * pitch - gap + 8;
    var lx = width - (5 * pitch + 78);
    var lessText = (opts.legend && opts.legend[0]) || "LESS", moreText = (opts.legend && opts.legend[1]) || "MORE";
    out += '<text class="cum-t is-end" x="' + f1(lx - 6) + '" y="' + f1(ly + cell - 1) + '">' + esc(lessText) + "</text>";
    for (var k = 0; k < 5; k++) {
      out += '<rect class="cum-cell cum-l' + k + '" x="' + f1(lx + k * pitch) + '" y="' + f1(ly) + '" width="' + cell + '" height="' + cell + '"/>';
    }
    out += '<text class="cum-t" x="' + f1(lx + 5 * pitch + 4) + '" y="' + f1(ly + cell - 1) + '">' + esc(moreText) + "</text>";
    return out + "</svg>";
  }

  /* Matrix heatmap: rows by columns, e.g. weekday by hour.
   * grid: [[value, ...], ...]. opts: width, rowLabels, colLabels [{c, text}], tip(r, c, value), gap, maxCell, label. */
  function matrix(grid, opts) {
    opts = opts || {};
    grid = grid || [];
    var rows = grid.length, cols = grid.reduce(function (a, r) { return Math.max(a, r.length); }, 0);
    var gap = num(opts.gap, 3), gutter = 40, top = 20;
    var W = Math.max(260, num(opts.width, 700));
    var cw = clamp(Math.floor((W - gutter + gap) / Math.max(cols, 1)) - gap, 8, num(opts.maxCell, 40));
    var ch = clamp(Math.round(cw * 0.75), 8, 26);
    var width = gutter + cols * (cw + gap) - gap, height = top + rows * (ch + gap) - gap + 4;
    var flat = [];
    grid.forEach(function (row) { row.forEach(function (v) { flat.push(num(v, 0)); }); });
    var lv = levels(flat);
    var out = open("cum-matrix", width, height, opts.label || "matrix heatmap", { role: "group", nav: "grid", cols: cols });
    (opts.colLabels || []).forEach(function (l) {
      out += '<text class="cum-t is-mid" x="' + f1(gutter + l.c * (cw + gap) + cw / 2) + '" y="12">' + esc(l.text) + "</text>";
    });
    (opts.rowLabels || []).forEach(function (name, r) {
      out += '<text class="cum-t is-end" x="' + (gutter - 8) + '" y="' + f1(top + r * (ch + gap) + ch / 2 + 4) + '">' + esc(name) + "</text>";
    });
    var k = 0, start = 0, best = -1;
    flat.forEach(function (v, i) { if (v > best) { best = v; start = i; } });
    grid.forEach(function (row, r) {
      row.forEach(function (v, c) {
        var tip = opts.tip ? opts.tip(r, c, v) : String(v);
        out += "<rect" + item("cum-cell cum-l" + lv[k], tip, k, k === start, r, c) +
          ' x="' + (gutter + c * (cw + gap)) + '" y="' + (top + r * (ch + gap)) + '" width="' + cw + '" height="' + ch + '"/>';
        k++;
      });
    });
    return out + "</svg>";
  }

  // ── tooltip and keyboard binding ─────────────────────────────────────────────

  /* Wires one container once: a tooltip on hover and focus for anything with data-tip (or a .cum-item
   * with an aria-label), Escape to dismiss, and arrow-key movement between the items of a [data-nav] chart.
   * `tipEl` is the tooltip element (role="tooltip"). Returns {hide}: call it when the container's content is
   * replaced, so a tooltip never points at a node that is gone. Safe to call again for another container. */
  function bind(container, tipEl) {
    if (!container || !tipEl) {
      return;
    }
    var anchor = null;
    var holdTimer = 0;

    function textOf(el) {
      return el.getAttribute("data-tip") || el.getAttribute("aria-label") || "";
    }

    function targetOf(node) {
      return node && node.closest ? node.closest("[data-tip], .cum-item") : null;
    }

    function place(el) {
      var r = el.getBoundingClientRect();
      var vw = root.innerWidth || doc.documentElement.clientWidth, vh = root.innerHeight || doc.documentElement.clientHeight;
      var tw = tipEl.offsetWidth, th = tipEl.offsetHeight;
      var x = clamp(r.left + r.width / 2 - tw / 2, 8, Math.max(8, vw - tw - 8));
      var y = r.top - th - 8;
      if (y < 8) {
        y = Math.min(vh - th - 8, r.bottom + 8);
      }
      tipEl.style.left = Math.round(x) + "px";
      tipEl.style.top = Math.round(Math.max(8, y)) + "px";
    }

    function restoreTitle() {
      if (anchor && anchor.hasAttribute("data-title-saved")) {
        anchor.setAttribute("title", anchor.getAttribute("data-title-saved"));
        anchor.removeAttribute("data-title-saved");
      }
    }

    function show(el) {
      root.clearTimeout(holdTimer);
      if (anchor && anchor !== el) {
        restoreTitle();
      }
      anchor = el;
      var text = textOf(el);
      if (!text) {
        return;
      }
      if (el.hasAttribute("title")) {
        el.setAttribute("data-title-saved", el.getAttribute("title"));
        el.removeAttribute("title");
      }
      tipEl.textContent = text;
      tipEl.style.left = "0px";
      tipEl.style.top = "0px";
      tipEl.removeAttribute("hidden");
      place(el);
    }

    function hide() {
      root.clearTimeout(holdTimer);
      restoreTitle();
      anchor = null;
      tipEl.setAttribute("hidden", "");
    }

    function hideSoon() {
      root.clearTimeout(holdTimer);
      holdTimer = root.setTimeout(hide, 140);
    }

    container.addEventListener("mouseover", function (e) {
      var t = targetOf(e.target);
      if (t) {
        show(t);
      }
    });
    container.addEventListener("mouseout", function (e) {
      var t = targetOf(e.target);
      if (t && !(e.relatedTarget && t.contains(e.relatedTarget))) {
        hideSoon();
      }
    });
    container.addEventListener("focusin", function (e) {
      var t = targetOf(e.target);
      if (t) {
        show(t);
      }
    });
    container.addEventListener("focusout", function (e) {
      if (targetOf(e.target)) {
        hide();
      }
    });
    container.addEventListener("scroll", function () { if (anchor) { hide(); } }, true);
    tipEl.addEventListener("mouseenter", function () { root.clearTimeout(holdTimer); });
    tipEl.addEventListener("mouseleave", hideSoon);
    doc.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && anchor) {
        hide();
      }
    });

    container.addEventListener("keydown", function (e) {
      var cur = e.target && e.target.closest ? e.target.closest(".cum-item") : null;
      if (!cur) {
        return;
      }
      var group = cur.closest("[data-nav]");
      if (!group) {
        return;
      }
      var next = navigate(group, cur, e.key);
      if (next) {
        e.preventDefault();
        cur.setAttribute("tabindex", "-1");
        next.setAttribute("tabindex", "0");
        next.focus();
      }
    });
    return { hide: hide };
  }

  function navigate(group, cur, key) {
    var items = group.querySelectorAll(".cum-item");
    var i = +cur.getAttribute("data-i");
    function byIndex(k) {
      return group.querySelector('.cum-item[data-i="' + k + '"]');
    }
    if (key === "Home") {
      return items[0] || null;
    }
    if (key === "End") {
      return items[items.length - 1] || null;
    }
    if (group.getAttribute("data-nav") === "grid") {
      var r = +cur.getAttribute("data-r"), c = +cur.getAttribute("data-c");
      var dr = key === "ArrowDown" ? 1 : key === "ArrowUp" ? -1 : 0;
      var dc = key === "ArrowRight" ? 1 : key === "ArrowLeft" ? -1 : 0;
      if (!dr && !dc) {
        return null;
      }
      for (var s = 1; s < 60; s++) {
        var cand = group.querySelector('.cum-item[data-r="' + (r + dr * s) + '"][data-c="' + (c + dc * s) + '"]');
        if (cand) {
          return cand;
        }
        if (r + dr * s < 0 || r + dr * s > 8 || c + dc * s < 0) {
          break;
        }
      }
      return null;
    }
    if (key === "ArrowRight" || key === "ArrowDown") {
      return byIndex(i + 1);
    }
    if (key === "ArrowLeft" || key === "ArrowUp") {
      return byIndex(i - 1);
    }
    return null;
  }

  installStyle();

  root.CUMCharts = {
    sparkline: sparkline, line: line, columns: columns, bars: bars, hbars: hbars, stackedBar: stackedBar,
    ring: ring, heatmap: heatmap, calendar: calendar, matrix: matrix,
    niceStep: niceStep, levels: levels, refresh: refresh, bind: bind, pips: function () { return pipsOn; }
  };
})(window);
