/* charts.js: dependency-free SVG helpers for the dashboard. Each function returns an SVG string.
 * Colours come from the theme through CSS custom properties (set with style="fill:var(--main)"),
 * so a theme change repaints every chart without redrawing it. Inputs are numbers only; nothing
 * from the API is put into the markup as text.
 */
(function (root) {
  "use strict";

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

  /* Line through values, with a dot on each point. opts: width, height, pad, min, max, dots */
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
    var out = '<svg class="cum-spark" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none" role="img" aria-label="' +
      (opts.label || "trend") + '">';
    [top, (top + base) / 2, base].forEach(function (y) {
      out += '<line x1="0" x2="' + w + '" y1="' + f1(y) + '" y2="' + f1(y) +
        '" style="stroke:var(--ghost)" stroke-width="1" stroke-dasharray="1 3"/>';
    });
    if (pts.length > 1) {
      out += '<polyline fill="none" style="stroke:var(--bright)" stroke-width="1.2" points="' +
        pts.map(function (p) { return f1(p[0]) + "," + f1(p[1]); }).join(" ") + '"/>';
    }
    if (opts.dots !== false) {
      out += pts.map(function (p) {
        return '<circle cx="' + f1(p[0]) + '" cy="' + f1(p[1]) + '" r="1.6" style="fill:var(--hot)"/>';
      }).join("");
    }
    return out + "</svg>";
  }

  /* Vertical columns. opts: width, height, gap, max, label */
  function bars(values, opts) {
    opts = opts || {};
    var w = num(opts.width, 300), h = num(opts.height, 80), gap = num(opts.gap, 3);
    var list = (values || []).map(function (v) { return Math.max(0, num(v, 0)); });
    var hi = opts.max !== undefined ? num(opts.max) : Math.max.apply(null, list.concat([1]));
    var n = Math.max(list.length, 1);
    var colW = (w - gap * (n - 1)) / n;
    var out = '<svg class="cum-bars" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none" role="img" aria-label="' +
      (opts.label || "bars") + '">';
    list.forEach(function (v, i) {
      var bh = hi ? v / hi * h : 0;
      var x = i * (colW + gap);
      var cls = opts.current !== undefined && i === opts.current ? "var(--hot)" : "var(--main)";
      out += '<rect x="' + f1(x) + '" y="' + f1(h - bh) + '" width="' + f1(colW) + '" height="' + f1(bh) +
        '" style="fill:' + cls + '"/>';
    });
    return out + "</svg>";
  }

  /* One horizontal bar split into parts. parts: [{value, cls: "main" | "alt" | "dim"}] */
  function stackedBar(parts, opts) {
    opts = opts || {};
    var w = num(opts.width, 300), h = num(opts.height, 16), gap = num(opts.gap, 2);
    var list = (parts || []).map(function (p) { return { value: Math.max(0, num(p.value, 0)), cls: p.cls || "main" }; });
    var total = list.reduce(function (a, p) { return a + p.value; }, 0);
    var scale = total > 0 ? total : 1;
    var usable = w - gap * Math.max(0, list.length - 1);
    var out = '<svg class="cum-stack" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none" role="img" aria-label="' +
      (opts.label || "share") + '">';
    var x = 0;
    list.forEach(function (p) {
      var pw = p.value / scale * usable;
      var fill = p.cls === "alt" ? "var(--label)" : p.cls === "dim" ? "var(--ghost)" : "var(--main)";
      out += '<rect x="' + f1(x) + '" y="0" width="' + f1(Math.max(0, pw)) + '" height="' + h + '" style="fill:' + fill + '"/>';
      x += pw + gap;
    });
    return out + "</svg>";
  }

  /* Ring: a track and an arc from 12 o'clock clockwise. opts: size, stroke, track */
  function ring(percent, opts) {
    opts = opts || {};
    var size = num(opts.size, 72), stroke = num(opts.stroke, 6);
    var r = (size - stroke) / 2, c = size / 2;
    var p = clamp(num(percent, 0), 0, 100);
    var angle = p / 100 * Math.PI * 2;
    var x = c + r * Math.sin(angle), y = c - r * Math.cos(angle);
    var large = p > 50 ? 1 : 0;
    var out = '<svg class="cum-ring" viewBox="0 0 ' + size + ' ' + size + '" role="img" aria-label="' +
      f1(p) + ' percent">';
    out += '<circle cx="' + c + '" cy="' + c + '" r="' + f1(r) + '" fill="none" stroke-width="' + stroke +
      '" style="stroke:var(--track)"/>';
    if (p > 0) {
      var d = p >= 100
        ? 'M ' + c + ' ' + (c - r) + ' A ' + r + ' ' + r + ' 0 1 1 ' + c + ' ' + (c + r) + ' A ' + r + ' ' + r + ' 0 1 1 ' + c + ' ' + (c - r)
        : 'M ' + c + ' ' + (c - r) + ' A ' + f1(r) + ' ' + f1(r) + ' 0 ' + large + ' 1 ' + f1(x) + ' ' + f1(y);
      out += '<path d="' + d + '" fill="none" stroke-width="' + stroke + '" style="stroke:var(--main)"/>';
    }
    return out + "</svg>";
  }

  /* Grid of cells; each value 0..1 sets the opacity of the accent. grid: array of rows */
  function heatmap(grid, opts) {
    opts = opts || {};
    var cell = num(opts.cell, 12), gap = num(opts.gap, 2);
    var rows = grid || [];
    var cols = rows.reduce(function (a, r) { return Math.max(a, r.length); }, 0);
    var w = cols * (cell + gap) - gap, h = rows.length * (cell + gap) - gap;
    var out = '<svg class="cum-heat" viewBox="0 0 ' + Math.max(w, 1) + ' ' + Math.max(h, 1) + '" role="img" aria-label="' +
      (opts.label || "heatmap") + '">';
    rows.forEach(function (row, y) {
      row.forEach(function (v, x) {
        var a = clamp(num(v, 0), 0, 1);
        out += '<rect x="' + (x * (cell + gap)) + '" y="' + (y * (cell + gap)) + '" width="' + cell + '" height="' + cell +
          '" style="fill:var(--main);opacity:' + (a > 0 ? f1(0.12 + a * 0.88) : "0.1") + '"/>';
      });
    });
    return out + "</svg>";
  }

  root.CUMCharts = { sparkline: sparkline, bars: bars, stackedBar: stackedBar, ring: ring, heatmap: heatmap };
})(window);
