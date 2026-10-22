/* app.js: the dashboard client. Polls /api/state every 2 s while the window is visible (ETag, so an
 * unchanged state costs a 304), routes on the hash (#overview #limits #cost #nerds #history #settings
 * #about), and renders from the live MonitorState. Settings apply at once through POST /api/settings.
 * Dependencies: charts.js (window.CUMCharts) and, for the Nostromo flavour, themes/nostromo.js.
 * Nothing here stores a token, a message or a file body. The token comes from the page's meta tag.
 */
(function () {
  "use strict";

  var POLL_MS = 2000;
  var FALLBACK_CSS = "/static/themes/nostromo.css";
  var TOKEN = (function () {
    var meta = document.querySelector('meta[name="monitor-token"]');
    return meta ? meta.getAttribute("content") || "" : "";
  })();
  var CHARTS = window.CUMCharts;
  var PAGES = [
    { key: "overview", label: "OVERVIEW", title: "OVERVIEW", sub: "Session telemetry for the current window. Pace is usage against elapsed time." },
    { key: "limits", label: "LIMITS", title: "LIMITS", sub: "Every limit window, its reset and its pace against the clock." },
    { key: "cost", label: "COST & VALUE", title: "COST & VALUE", sub: "API-equivalent at list prices. Not what your plan bills." },
    { key: "nerds", label: "NERDS ONLY", title: "NERDS ONLY", sub: "Raw counters and pipeline health, all from this machine." },
    { key: "history", label: "HISTORY", title: "HISTORY", sub: "Window bounds and recent burn. Daily history arrives with analytics." },
    { key: "settings", label: "SETTINGS", title: "SETTINGS", sub: "Every change applies at once. Nothing needs a restart." },
    { key: "about", label: "ABOUT", title: "ABOUT", sub: "Claude Usage Monitor, the people behind it, and where to report problems." }
  ];
  var PAGE_KEYS = PAGES.map(function (p) { return p.key; });
  var STATUS = {
    live: { text: "UPLINK LIVE", ok: true },
    loading: { text: "ACQUIRING", ok: false },
    stale: { text: "AUTH LOST", ok: false },
    offline: { text: "NO CARRIER", ok: false },
    rate_limited: { text: "RATE LIMITED", ok: false },
    no_credentials: { text: "NO CREDENTIALS", ok: false },
    paused: { text: "PAUSED", ok: false }
  };
  var ACCENT_ABBR = {
    amber: "AMB", green: "GRN", white: "WHT", red: "RED", blue: "BLU", cyan: "CYN", orange: "ORG",
    purple: "PUR", system: "SYS", teal: "TEA", rose: "ROS"
  };
  var WINDOW_LABEL = {
    session: "SESSION (5 H)", today: "TODAY", week: "7 DAYS", month: "THIS MONTH",
    cycle: "BILLING CYCLE", all: "ALL TIME"
  };
  var WINDOW_ORDER = ["session", "today", "week", "month", "cycle", "all"];
  var WINDOW_SHORT = { today: "TODAY", week: "7 DAYS", month: "MONTH", all: "ALL" };
  var DAYS = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];
  var LINKS = {
    repo: "https://github.com/Godimas101/claude-usage-monitor",
    bug: "https://github.com/Godimas101/claude-usage-monitor/issues/new?template=bug_report.md&labels=bug",
    patreon: "https://patreon.com/Godimas101"
  };
  var POLL_CHOICES = [
    [60000, "1 MIN"], [300000, "5 MIN"], [600000, "10 MIN"], [900000, "15 MIN"], [1800000, "30 MIN"], [3600000, "60 MIN"]
  ];

  var S = {
    data: null,
    etag: null,
    linkOk: null,
    lastUpdatedAt: null,
    samples: [],
    settings: {},
    themes: null,
    themesRequested: false,
    themeKey: "",
    appliedVars: [],
    lastTheme: null,
    loadedScripts: {},
    page: null,
    formKey: null,
    minuteKey: "",
    status: { text: "", error: false },
    update: { text: "" },
    supporters: null,
    supportersRequested: false,
    inFlight: false,
    costWindow: "month"
  };

  // ── Helpers ─────────────────────────────────────────────────────────────────────

  function $(selector, root) {
    return (root || document).querySelector(selector);
  }

  function esc(value) {
    return String(value === null || value === undefined ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function num(value) {
    if (value === null || value === undefined || value === "") {
      return null;
    }
    var n = Number(value);
    return isFinite(n) ? n : null;
  }

  function clamp(v, lo, hi) {
    return Math.max(lo, Math.min(hi, v));
  }

  function pad2(n) {
    return (n < 10 ? "0" : "") + n;
  }

  function fmtPct(value, digits) {
    var n = num(value);
    if (n === null) {
      return "--";
    }
    return (digits ? n.toFixed(digits) : String(Math.round(n))) + "%";
  }

  function fmtUsd(value) {
    var n = num(value);
    if (n === null) {
      return "--";
    }
    var a = Math.abs(n);
    var body = a >= 1000 ? Math.round(a).toLocaleString("en-US") : a.toFixed(2);
    return (n < 0 ? "-$" : "$") + body;
  }

  function fmtPlanPrice(value) {
    var n = num(value);
    if (n === null) {
      return "--";
    }
    return n % 1 === 0 ? "$" + Math.round(n).toLocaleString("en-US") : fmtUsd(n);
  }

  function fmtUsdShort(value) {
    var n = num(value);
    if (n === null) {
      return "--";
    }
    return Math.abs(n) >= 1000 ? "$" + (n / 1000).toFixed(1) + "K" : fmtUsd(n);
  }

  function fmtCount(value) {
    var n = num(value);
    if (n === null) {
      return "--";
    }
    var a = Math.abs(n);
    if (a >= 1e9) {
      return (n / 1e9).toFixed(2) + "B";
    }
    if (a >= 1e6) {
      return (n / 1e6).toFixed(1) + "M";
    }
    if (a >= 1e3) {
      return (n / 1e3).toFixed(1) + "K";
    }
    return String(Math.round(n));
  }

  function fmtInt(value) {
    var n = num(value);
    return n === null ? "--" : Math.round(n).toLocaleString("en-US");
  }

  function fmtRemain(ms) {
    if (ms === null || !isFinite(ms)) {
      return "--";
    }
    var minutes = Math.floor(ms / 60000);
    if (minutes <= 0) {
      return "NOW";
    }
    var days = Math.floor(minutes / 1440);
    var hours = Math.floor((minutes % 1440) / 60);
    var mins = minutes % 60;
    if (days >= 1) {
      return days + " D " + hours + " H";
    }
    if (hours >= 1) {
      return hours + " H " + mins + " M";
    }
    return mins + " M";
  }

  function fmtAge(ms) {
    if (ms === null || !isFinite(ms)) {
      return "--";
    }
    var s = Math.max(0, Math.floor(ms / 1000));
    if (s < 60) {
      return s + " S";
    }
    var m = Math.floor(s / 60);
    if (m < 60) {
      return m + " M";
    }
    var h = Math.floor(m / 60);
    if (h < 48) {
      return h + " H";
    }
    return Math.floor(h / 24) + " D";
  }

  function parseTime(iso) {
    if (!iso) {
      return null;
    }
    var t = Date.parse(iso);
    return isFinite(t) ? t : null;
  }

  function clockText(ms) {
    if (ms === null) {
      return "--";
    }
    var d = new Date(ms);
    var now = new Date();
    var h = d.getHours();
    var text = (h % 12 || 12) + ":" + pad2(d.getMinutes()) + " " + (h >= 12 ? "PM" : "AM");
    var sameDay = d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
    return sameDay ? text : DAYS[d.getDay()] + " " + text;
  }

  function stampText(ms) {
    if (ms === null) {
      return "--";
    }
    var d = new Date(ms);
    var months = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
    var h = d.getHours();
    return months[d.getMonth()] + " " + d.getDate() + " " + (h % 12 || 12) + ":" + pad2(d.getMinutes()) + " " + (h >= 12 ? "PM" : "AM");
  }

  function accentOf(variantId) {
    return String(variantId || "").replace(/-(dark|light)$/, "");
  }

  function hex6(value) {
    var m = /^#([0-9a-fA-F]{6})/.exec(String(value || ""));
    return m ? "#" + m[1] : null;
  }

  function errorText(res) {
    return res && res.data && res.data.error ? res.data.error : "HTTP " + (res ? res.status : "?");
  }

  // ── API client ──────────────────────────────────────────────────────────────────

  function api(method, path, body, etag) {
    var headers = { "X-Monitor-Token": TOKEN, Accept: "application/json" };
    if (body !== undefined && body !== null) {
      headers["Content-Type"] = "application/json";
    }
    if (etag) {
      headers["If-None-Match"] = etag;
    }
    return fetch(path, {
      method: method,
      headers: headers,
      body: body !== undefined && body !== null ? JSON.stringify(body) : undefined,
      cache: "no-store",
      credentials: "omit"
    }).then(function (res) {
      return res.text().then(function (text) {
        var data = null;
        try {
          data = text ? JSON.parse(text) : null;
        } catch (e) {
          data = null;
        }
        return { status: res.status, ok: res.ok, data: data, etag: res.headers.get("ETag") };
      });
    });
  }

  // ── State accessors ─────────────────────────────────────────────────────────────

  function st() {
    return S.data && S.data.state ? S.data.state : null;
  }

  function usage() {
    var s = st();
    return s && s.usage ? s.usage : null;
  }

  function limits() {
    var u = usage();
    return u && u.limits ? u.limits : [];
  }

  function limitOf(kind) {
    var list = limits();
    for (var i = 0; i < list.length; i++) {
      if (list[i].kind === kind) {
        return list[i];
      }
    }
    return null;
  }

  function scopedLimits() {
    return limits().filter(function (l) { return l.kind !== "session" && l.kind !== "weekly_all"; });
  }

  function windowOf(name) {
    var s = st();
    return s && s.windows && s.windows[name] ? s.windows[name] : null;
  }

  function modelName(id) {
    var names = S.data && S.data.model_names ? S.data.model_names : {};
    return names[id] || id;
  }

  function currentTheme() {
    return S.data && S.data.theme ? S.data.theme : null;
  }

  function findTheme(id) {
    var list = S.themes || [];
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === id) {
        return list[i];
      }
    }
    return null;
  }

  // ── Limits: severity, pace, cells ───────────────────────────────────────────────

  function windowSeconds(limit) {
    return num(limit.window_seconds) || (limit.kind === "session" ? 18000 : 604800);
  }

  function severityOf(limit) {
    var raw = String(limit.severity || "").toLowerCase();
    if (raw) {
      if (raw.indexOf("crit") >= 0 || raw.indexOf("alert") >= 0) {
        return "critical";
      }
      if (raw.indexOf("warn") >= 0 || raw.indexOf("advis") >= 0) {
        return "warning";
      }
      return "normal";
    }
    var pct = num(limit.percent) || 0;
    if (pct >= 90) {
      return "critical";
    }
    return pct >= 75 ? "warning" : "normal";
  }

  var SEVERITY_WORD = { normal: "NOMINAL", warning: "ADVISORY", critical: "CRITICAL" };

  function worstSeverity(list) {
    var rank = { normal: 0, warning: 1, critical: 2 };
    var worst = "normal";
    list.forEach(function (l) {
      var s = severityOf(l);
      if (rank[s] > rank[worst]) {
        worst = s;
      }
    });
    return worst;
  }

  function paceOf(limit, now) {
    var reset = parseTime(limit.resets_at);
    if (reset === null) {
      return { elapsed: null, diff: null, kind: null, reset: null, remain: null };
    }
    var span = windowSeconds(limit) * 1000;
    var elapsed = clamp((now - (reset - span)) / span, 0, 1);
    var pct = num(limit.percent) || 0;
    var diff = pct - elapsed * 100;
    var kind = diff > 10 ? "ahead" : diff < -10 ? "behind" : "on_pace";
    return { elapsed: elapsed, diff: diff, kind: kind, reset: reset, remain: reset - now };
  }

  function paceChip(limit, p, scoped) {
    var pct = num(limit.percent) || 0;
    if (pct <= 0) {
      return { text: scoped ? "NO DRAW" : "UNUSED", cls: "" };
    }
    if (p.kind === "ahead") {
      return { text: "AHEAD OF PACE", cls: " solid" };
    }
    if (p.kind === "behind") {
      return { text: "UNDER PACE", cls: "" };
    }
    if (p.kind === "on_pace") {
      return { text: "ON PACE", cls: "" };
    }
    return { text: "NO CLOCK", cls: "" };
  }

  function cellsHTML(n, value) {
    var x = clamp(num(value) || 0, 0, 100) / 100 * n;
    var full = Math.floor(x);
    var frac = x - full;
    var out = "";
    for (var i = 0; i < n; i++) {
      if (i < full) {
        out += '<i class="on"></i>';
      } else if (i === full && frac > 0.02) {
        out += '<i class="part" style="--f:' + (frac * 100).toFixed(1) + '%"></i>';
      } else {
        out += "<i></i>";
      }
    }
    return out;
  }

  function railHTML(p) {
    var t = p.elapsed === null ? 0 : p.elapsed * 100;
    return '<div class="nm-rail" style="--t:' + t.toFixed(1) + '%" title="Time elapsed in this window"><i></i><b></b></div>';
  }

  function sevClass(limit) {
    var s = severityOf(limit);
    return s === "warning" ? " is-warn" : s === "critical" ? " is-crit" : "";
  }

  function limitCard(limit, opts) {
    opts = opts || {};
    var now = Date.now();
    var p = paceOf(limit, now);
    var pct = num(limit.percent) || 0;
    var chip = paceChip(limit, p, !!opts.share);
    var tag = limit.label || (limit.kind === "session" ? "SESSION" : "WEEKLY");
    var facts = [];
    if (opts.share) {
      facts.push("<span>OF " + esc(fmtPct(opts.share)) + " SHARE</span>");
    }
    if (p.reset !== null) {
      facts.push("<span>RESETS <b>" + esc(clockText(p.reset)) + "</b></span>");
      if (!opts.share) {
        facts.push("<span>IN " + esc(fmtRemain(p.remain)) + "</span>");
      }
    }
    facts.push('<span class="nm-chip' + chip.cls + '">' + esc(chip.text) + "</span>");
    var diffText = p.diff === null ? "NO RESET TIME" : (p.diff >= 0 ? "+" : "") + Math.round(p.diff) + " PTS, " + chip.text;
    return '<section class="nm-card' + (opts.wide ? " wide" : "") + sevClass(limit) + '" data-limit="' + esc(limit.kind) + '">' +
      '<div class="nm-card-in">' +
      '<div class="nm-card-t"><span class="nm-tag">' + esc(tag) + '</span><span class="nm-meta-t">' + esc(opts.meta || "") + "</span></div>" +
      '<div class="nm-row2"><div class="nm-big">' + esc(fmtPct(pct)) + '</div><div class="nm-facts">' + facts.join("") + "</div></div>" +
      '<div class="nm-pace"><span>USAGE ' + esc(fmtPct(pct)) + " / TIME " + esc(p.elapsed === null ? "--" : fmtPct(p.elapsed * 100)) +
      "</span><b>" + esc(diffText) + "</b></div>" +
      '<div class="nm-cells" aria-hidden="true">' + cellsHTML(32, pct) + "</div>" +
      railHTML(p) +
      "</div></section>";
  }

  function emptyLimitCard(tag, meta) {
    return '<section class="nm-card"><div class="nm-card-in"><div class="nm-card-t"><span class="nm-tag">' + esc(tag) +
      '</span><span class="nm-meta-t">' + esc(meta) + '</span></div><p class="nm-empty">Waiting for the first reading.</p></div></section>';
  }

  // ── Banners ─────────────────────────────────────────────────────────────────────

  function statusBanner() {
    var s = st();
    if (!s || s.status === "live") {
      return "";
    }
    var info = STATUS[s.status] || STATUS.loading;
    return '<div class="nm-msg" role="status"><b>' + esc(info.text) + "</b>" + esc(s.status_detail || "") + "</div>";
  }

  function hazardBand(sev) {
    return '<div class="nm-hazard' + (sev === "critical" ? "" : " is-off") + '" aria-hidden="true"></div>';
  }

  // ── Overview ────────────────────────────────────────────────────────────────────

  function overviewPage() {
    var s = st();
    var u = usage();
    var session = limitOf("session");
    var weekly = limitOf("weekly_all");
    var scoped = scopedLimits();
    var month = windowOf("month");
    var today = windowOf("today");
    var cards = [];
    cards.push(session ? limitCard(session, { meta: "5-hour window" }) : emptyLimitCard("SESSION", "5-hour window"));
    cards.push(weekly ? limitCard(weekly, { meta: "7-day window" }) : emptyLimitCard("WEEKLY", "7-day window"));
    if (scoped.length) {
      var first = scoped[0];
      cards.push(limitCard(first, { meta: "7-day window", share: num(first.allowance_pct) }));
    } else {
      cards.push(emptyLimitCard("SCOPED", "No scoped limit reported"));
    }
    cards.push(costCard(month, today));
    cards.push(efficiencyCard(month));
    cards.push(modelMixCard(month));
    cards.push(surfaceCard(u));
    cards.push(burnCard(s));
    var worst = worstSeverity(limits());
    return hazardBand(worst) + '<div class="nm-head"><h1 class="nm-h1">OVERVIEW<span class="nm-cur" aria-hidden="true"></span></h1>' +
      '<p class="nm-sub">Session telemetry for the current window. Pace is usage against elapsed time.</p></div>' +
      statusBanner() + '<div class="nm-grid">' + cards.join("") + "</div>";
  }

  function ratioCells(ratio, label) {
    var whole = Math.floor(ratio);
    var frac = ratio - whole;
    var n = clamp(Math.ceil(ratio), 1, 24);
    var out = "";
    for (var k = 0; k < n; k++) {
      if (k < whole) {
        out += '<i class="on"></i>';
      } else if (k === whole && frac > 0.02) {
        out += '<i class="part" style="--f:' + Math.round(frac * 100) + '%"></i>';
      } else {
        out += "<i></i>";
      }
    }
    return '<div class="nm-ratio" role="img" aria-label="' + esc(label) + '">' + out + "</div>";
  }

  function planPrice() {
    var s = st();
    return s && s.plan ? num(s.plan.monthly_price_usd) : null;
  }

  function costCard(month, today) {
    var s = st();
    var plan = s && s.plan ? s.plan : null;
    var monthCost = month ? num(month.totals.cost_usd) : null;
    var price = planPrice();
    var ratio = monthCost !== null && price ? monthCost / price : null;
    var heroText = monthCost === null ? "--" : fmtUsd(monthCost);
    var planBlock = ratio === null
      ? '<div class="nm-plan"><div class="nm-big m">--</div><div class="nm-facts" style="align-items:flex-start;text-align:left"><span>PLAN PRICE UNKNOWN</span><span>SET IT IN SETTINGS</span></div></div>'
      : '<div class="nm-plan"><div class="nm-big m">' + ratio.toFixed(1) + '&times;</div><div class="nm-facts" style="align-items:flex-start;text-align:left"><span>YOUR PLAN</span><span>' +
        esc((plan ? plan.name : "PLAN").toUpperCase()) + " &middot; " + esc(fmtPlanPrice(price)) + " / MONTH</span></div></div>";
    var todayCost = today ? fmtUsd(today.totals.cost_usd) : "--";
    var saved = month ? fmtUsdShort(month.totals.caching_saved_usd) : "--";
    return '<section class="nm-card wide" id="cost" data-card="cost"><div class="nm-card-in">' +
      '<div class="nm-card-t"><span class="nm-ttl">API-equivalent cost, month to date</span><span class="nm-meta-t">AT LIST PRICES</span></div>' +
      '<div class="nm-cost"><div class="nm-cost-l"><div class="nm-hero"><div class="nm-big h">' + esc(heroText) + "</div>" + planBlock + "</div>" +
      (ratio === null ? "" : ratioCells(ratio, ratio.toFixed(1) + " months of the plan")) +
      '<div class="nm-note">' + (ratio === null ? "Set the plan price in Settings to see the multiple." : "1 cell = 1 month of " + esc(plan ? plan.name : "plan") + " plan") + "</div>" +
      '<div class="nm-note nm-disc">API-equivalent at list prices, not amounts billed to your plan.</div>' +
      '<div class="nm-tiles"><div class="nm-tile"><small>Today</small><div class="nm-big m">' + esc(todayCost) +
      '</div></div><div class="nm-tile"><small>Caching saved, MTD</small><div class="nm-big m">' + esc(saved) + "</div></div></div></div>" +
      '<div class="nm-cost-r"><div class="nm-card-t" style="padding:0"><span class="nm-ttl" style="font-size:11px">DAILY &middot; LAST 30 DAYS</span><span class="nm-meta-t">1 PIP = $10</span></div>' +
      '<div class="nm-chart"><div class="nm-days" aria-hidden="true">' + emptyDays() + '</div><div class="nm-pending">Daily series arrives with analytics (Phase 2)</div></div></div>' +
      "</div></div></section>";
  }

  function emptyDays() {
    var out = "";
    for (var i = 0; i < 30; i++) {
      out += '<div class="nm-col"><i></i></div>';
    }
    return out;
  }

  function efficiencyCard(month) {
    var t = month ? month.totals : null;
    var hit = t ? t.cache_hit_rate * 100 : null;
    var thinking = t && t.output > 0 ? t.thinking / t.output * 100 : null;
    return '<section class="nm-card" id="efficiency" data-card="efficiency"><div class="nm-card-in">' +
      '<div class="nm-card-t"><span class="nm-ttl">Cache &amp; thinking</span><span class="nm-meta-t">this month</span></div>' +
      '<div class="nm-kpi"><div class="nm-kl">Cache hit rate</div><div class="nm-big m">' + esc(hit === null ? "--" : hit.toFixed(1) + "%") +
      '</div><div class="nm-cells" aria-hidden="true">' + cellsHTML(20, hit || 0) + "</div></div>" +
      '<div class="nm-kpi"><div class="nm-kl">Thinking share of output</div><div class="nm-big m">' + esc(thinking === null ? "--" : fmtPct(thinking)) +
      '</div><div class="nm-cells" aria-hidden="true">' + cellsHTML(20, thinking || 0) + "</div></div>" +
      "</div></section>";
  }

  function topModels(month, count) {
    var rows = month && month.by_model ? Object.keys(month.by_model).map(function (id) {
      return { id: id, cost: num(month.by_model[id].cost_usd) || 0, tokens: num(month.by_model[id].total_tokens) || 0 };
    }) : [];
    rows.sort(function (a, b) { return b.cost - a.cost || b.tokens - a.tokens; });
    var totalCost = rows.reduce(function (a, r) { return a + r.cost; }, 0);
    var totalTokens = rows.reduce(function (a, r) { return a + r.tokens; }, 0);
    return rows.slice(0, count).map(function (r) {
      var share = totalCost > 0 ? r.cost / totalCost * 100 : (totalTokens > 0 ? r.tokens / totalTokens * 100 : 0);
      return { id: r.id, name: modelName(r.id), share: share };
    });
  }

  function modelMixCard(month) {
    var rows = topModels(month, 4);
    var body = rows.length
      ? rows.map(function (r) {
        return '<div class="nm-mrow"><span class="k" title="' + esc(r.id) + '">' + esc(r.name.toUpperCase()) + '</span><div class="nm-cells" aria-hidden="true">' +
          cellsHTML(20, r.share) + '</div><span class="v">' + esc(fmtPct(r.share)) + "</span></div>";
      }).join("")
      : '<p class="nm-empty">No model mix yet this month.</p>';
    return '<section class="nm-card" id="models" data-card="models"><div class="nm-card-in"><div class="nm-card-t"><span class="nm-ttl">Model mix</span><span class="nm-meta-t">share, MTD</span></div>' +
      '<div class="nm-mix">' + body + "</div></div></section>";
  }

  function surfaceCard(u) {
    var rows = u && u.breakdown ? u.breakdown : [];
    var body = rows.length
      ? rows.map(function (r) {
        return '<div class="nm-mrow"><span class="k">' + esc(String(r.name || r.key || "").toUpperCase()) + '</span><div class="nm-cells" aria-hidden="true">' +
          cellsHTML(20, r.percent) + '</div><span class="v">' + esc(fmtPct(r.percent)) + "</span></div>";
      }).join("")
      : '<p class="nm-empty">No surface split reported yet.</p>';
    return '<section class="nm-card" data-card="surfaces"><div class="nm-card-in"><div class="nm-card-t"><span class="nm-ttl">Where the week went</span><span class="nm-meta-t">share, 7 days</span></div>' +
      '<div class="nm-mix">' + body + "</div></div></section>";
  }

  function burnCard(s) {
    var burn = s && s.burn ? s.burn : null;
    var velocity = burn ? String(burn.velocity || "IDLE") : "IDLE";
    var spark = S.samples.length > 1
      ? CHARTS.sparkline(S.samples, { width: 300, height: 58, label: "Burn rate, recent samples" })
      : '<p class="nm-note">Sampling the burn rate&hellip;</p>';
    return '<section class="nm-card" id="burn" data-card="burn"><div class="nm-card-in"><div class="nm-card-t"><span class="nm-ttl">Burn rate</span><span class="nm-chip">' + esc(velocity) + "</span></div>" +
      '<div class="nm-row2" style="align-items:baseline"><div class="nm-big m">' + esc(burn ? fmtInt(burn.tokens_per_min) : "--") +
      '</div><div class="nm-facts" style="align-items:flex-end"><span>TOK / MIN</span><span>' + esc(burn ? fmtUsd(burn.usd_per_hour) : "--") + " / HOUR</span></div></div>" +
      spark + "</div></section>";
  }

  // ── Limits ──────────────────────────────────────────────────────────────────────

  function limitsPage() {
    var list = limits();
    var u = usage();
    var s = st();
    var cards = [];
    if (!list.length) {
      cards.push(emptyLimitCard("LIMITS", "No reading yet"));
    }
    list.forEach(function (limit) {
      var p = paceOf(limit, Date.now());
      var span = windowSeconds(limit);
      var start = p.reset !== null ? p.reset - span * 1000 : null;
      var detail = [
        ["WINDOW START", start === null ? "--" : stampText(start)],
        ["RESETS", p.reset === null ? "--" : stampText(p.reset)],
        ["WINDOW", span >= 86400 ? Math.round(span / 86400) + " DAYS" : Math.round(span / 3600) + " HOURS"],
        ["SEVERITY", SEVERITY_WORD[severityOf(limit)]],
        ["ACTIVE", limit.is_active ? "YES" : "NO"],
        ["LOCKED", limit.locked_reason || "NO"]
      ];
      if (limit.dollars) {
        detail.push(["USED", fmtUsd(limit.dollars.used)], ["LIMIT", fmtUsd(limit.dollars.limit)]);
      }
      var table = '<table class="nm-table"><tbody>' + detail.map(function (row) {
        return "<tr><td>" + esc(row[0]) + '</td><td class="r lead">' + esc(row[1]) + "</td></tr>";
      }).join("") + "</tbody></table>";
      cards.push('<section class="nm-card' + sevClass(limit) + '" data-limit="' + esc(limit.kind) + '"><div class="nm-card-in">' +
        '<div class="nm-card-t"><span class="nm-tag">' + esc(limit.label || limit.kind) + '</span><span class="nm-meta-t">' +
        esc(limit.scope_model ? "SCOPED TO " + limit.scope_model.toUpperCase() : span >= 86400 ? "7-DAY WINDOW" : "5-HOUR WINDOW") + "</span></div>" +
        '<div class="nm-big-row"><div class="nm-big">' + esc(fmtPct(limit.percent)) + "</div>" +
        '<div class="nm-pace" style="text-align:right"><span>USAGE ' + esc(fmtPct(limit.percent)) + " / TIME " + esc(p.elapsed === null ? "--" : fmtPct(p.elapsed * 100)) +
        "</span><b>" + esc(p.diff === null ? "NO RESET TIME" : (p.diff >= 0 ? "+" : "") + Math.round(p.diff) + " PTS, " + paceChip(limit, p, limit.kind !== "session" && limit.kind !== "weekly_all").text) + "</b></div></div>" +
        '<div class="nm-cells tall" aria-hidden="true">' + cellsHTML(32, limit.percent) + "</div>" + railHTML(p) + table +
        "</div></section>");
    });
    var breakdown = u && u.breakdown ? u.breakdown : [];
    var colours = ["main", "alt", "dim"];
    var stack = breakdown.length
      ? CHARTS.stackedBar(breakdown.map(function (r, i) { return { value: r.percent, cls: colours[i % 3] }; }), { width: 520, height: 16, label: "Share by surface" })
      : '<p class="nm-empty">No surface split yet.</p>';
    var legend = breakdown.map(function (r, i) {
      return '<div class="nm-mrow"><span class="k">' + esc(String(r.name || r.key || "").toUpperCase()) + '</span><div class="nm-cells" aria-hidden="true">' +
        cellsHTML(20, r.percent) + '</div><span class="v">' + esc(fmtPct(r.percent)) + "</span></div>";
    }).join("");
    cards.push('<section class="nm-card wide" data-card="surfaces"><div class="nm-card-in"><div class="nm-card-t"><span class="nm-ttl">Seven-day surface breakdown</span><span class="nm-meta-t">share of the weekly window</span></div>' +
      '<div class="nm-stack-wrap">' + stack + '</div><div class="nm-legend">' + legend + "</div></div></section>");
    var shares = scopedLimits().map(function (l) {
      return '<div class="nm-mrow"><span class="k">' + esc((l.scope_model || l.label || "SCOPED").toUpperCase()) +
        '</span><div class="nm-cells" aria-hidden="true">' + cellsHTML(20, l.allowance_pct) + '</div><span class="v">' + esc(fmtPct(l.allowance_pct)) + "</span></div>";
    }).join("");
    cards.push('<section class="nm-card" data-card="shares"><div class="nm-card-in"><div class="nm-card-t"><span class="nm-ttl">Scoped shares</span><span class="nm-meta-t">allowance of the weekly limit</span></div>' +
      '<div class="nm-mix">' + (shares || '<p class="nm-empty">No scoped limits reported.</p>') + "</div></div></section>");
    var spend = u && u.spend;
    var spendBody = spend && spend.enabled
      ? '<div class="nm-big-row"><div class="nm-big m">' + esc(fmtUsd(spend.used)) + '</div><div class="nm-facts"><span>OF ' + esc(fmtUsd(spend.limit)) + "</span><span>" +
        esc(fmtPct(spend.percent)) + " USED</span></div></div><p class=\"nm-note\">Real money: usage credits reported by the usage API.</p>"
      : '<p class="nm-empty">Usage credits are off for this account.</p>';
    cards.push('<section class="nm-card" data-card="spend"><div class="nm-card-in"><div class="nm-card-t"><span class="nm-ttl">Usage credits</span><span class="nm-meta-t">real money</span></div>' + spendBody + "</div></section>");
    return '<div class="nm-head"><h1 class="nm-h1">LIMITS<span class="nm-cur" aria-hidden="true"></span></h1><p class="nm-sub">' +
      "Every limit window, its reset and its pace against the clock." + "</p></div>" +
      statusBanner() + '<div class="nm-pgrid">' + cards.join("") + "</div>";
  }

  // ── Cost & value ────────────────────────────────────────────────────────────────

  function totalsRow(label, w, tone) {
    var t = w && w.totals ? w.totals : null;
    if (!w || !w.start) {
      return '<tr class="' + (tone || "") + '"><td class="lead">' + esc(label) + '</td><td colspan="5" class="r">NOT OPEN</td></tr>';
    }
    return '<tr class="' + (tone || "") + '"><td class="lead">' + esc(label) + '</td><td class="r">' + esc(fmtCount(t.requests)) +
      '</td><td class="r">' + esc(fmtCount(t.total_tokens)) + '</td><td class="r">' + esc(t.cache_hit_rate === undefined ? "--" : fmtPct(t.cache_hit_rate * 100)) +
      '</td><td class="r v">' + esc(fmtUsd(t.cost_usd)) + '</td><td class="r">' + esc(fmtUsd(t.caching_saved_usd)) + "</td></tr>";
  }

  function costWindowButtons(active) {
    var keys = ["today", "week", "month", "all"].filter(function (k) { return windowOf(k); });
    return '<span class="nm-seg" role="group" aria-label="Window for the model table">' + keys.map(function (k) {
      var on = k === active;
      return '<button type="button" data-cost-window="' + k + '" class="' + (on ? "on" : "") + '" aria-pressed="' + (on ? "true" : "false") + '">' +
        esc(WINDOW_SHORT[k]) + "</button>";
    }).join("") + "</span>";
  }

  function costPage() {
    var s = st();
    var month = windowOf("month");
    var names = WINDOW_ORDER.filter(function (k) { return windowOf(k); });
    var windowRows = names.map(function (k) {
      return totalsRow(WINDOW_LABEL[k] || k.toUpperCase(), windowOf(k), k === "today" ? "is-today" : "");
    }).join("");
    var pick = windowOf(S.costWindow) ? S.costWindow : "month";
    var modelWindow = windowOf(pick);
    var models = topModels(modelWindow, 8);
    var modelRows = models.length ? models.map(function (r) {
      var t = modelWindow.by_model[r.id];
      return '<tr><td class="lead">' + esc(r.name.toUpperCase()) + '</td><td class="r">' + esc(fmtCount(t.requests)) + '</td><td class="r">' +
        esc(fmtCount(t.total_tokens)) + '</td><td class="r v">' + esc(fmtUsd(t.cost_usd)) + '</td><td class="r">' + esc(fmtPct(r.share)) + "</td></tr>";
    }).join("") : '<tr><td colspan="5">No model usage this month yet.</td></tr>';
    var price = planPrice();
    var monthCost = month ? num(month.totals.cost_usd) : null;
    var ratio = monthCost !== null && price ? monthCost / price : null;
    var plan = s && s.plan ? s.plan : null;
    var value = '<div class="nm-big-row"><div class="nm-big h" style="font-size:66px">' + esc(fmtUsd(monthCost)) + '</div><div class="nm-facts"><span>' +
      esc(plan ? plan.name.toUpperCase() : "PLAN") + "</span><span>" + esc(price === null ? "PRICE UNKNOWN" : fmtPlanPrice(price) + " / MONTH") + "</span></div></div>" +
      (ratio === null ? '<p class="nm-note">Set the plan price in Settings to see the value multiple.</p>'
        : '<div class="nm-big m">' + esc(ratio.toFixed(1)) + "&times; the plan</div>" + ratioCells(ratio, ratio.toFixed(1) + " plan months")) +
      '<p class="nm-note nm-disc">API-equivalent at list prices, not amounts billed to your plan.</p>';
    var unpriced = s && s.unpriced_models && s.unpriced_models.length
      ? '<section class="nm-card"><div class="nm-card-in"><div class="nm-card-t"><span class="nm-ttl">Not priced yet</span><span class="nm-meta-t">excluded from cost</span></div>' +
        '<ul class="nm-supp">' + s.unpriced_models.map(function (id) { return "<li>" + esc(id) + "</li>"; }).join("") + "</ul></div></section>"
      : "";
    return '<div class="nm-head"><h1 class="nm-h1">COST &amp; VALUE<span class="nm-cur" aria-hidden="true"></span></h1><p class="nm-sub">API-equivalent at list prices. Not what your plan bills.</p></div>' +
      statusBanner() + '<div class="nm-pgrid">' +
      '<section class="nm-card wide"><div class="nm-card-in"><div class="nm-card-t"><span class="nm-ttl">Windows</span><span class="nm-meta-t">requests, tokens and cost by window</span></div>' +
      '<div class="nm-table-wrap"><table class="nm-table"><thead><tr><th>Window</th><th class="r">Requests</th><th class="r">Tokens</th><th class="r">Cache hit</th><th class="r">API-equiv.</th><th class="r">Saved by cache</th></tr></thead><tbody>' +
      windowRows + "</tbody></table></div></div></section>" +
      '<section class="nm-card"><div class="nm-card-in"><div class="nm-card-t"><span class="nm-ttl">Value</span><span class="nm-meta-t">this month</span></div>' + value + "</div></section>" +
      '<section class="nm-card"><div class="nm-card-in"><div class="nm-card-t"><span class="nm-ttl">By model</span>' + costWindowButtons(pick) + "</div>" +
      '<div class="nm-table-wrap"><table class="nm-table"><thead><tr><th>Model</th><th class="r">Requests</th><th class="r">Tokens</th><th class="r">API-equiv.</th><th class="r">Share</th></tr></thead><tbody>' +
      modelRows + "</tbody></table></div></div></section>" + unpriced +
      futurePanel("Daily cost and value history", "The 30-day cost series, per-day value and cost-per-session arrive with the analytics endpoints (Phase 2).") +
      "</div>";
  }

  function futurePanel(title, text) {
    return '<section class="nm-future" data-future="1"><b>More stats arriving</b><p><span class="nm-ttl" style="font-size:10px">' + esc(title) +
      "</span></p><p>" + esc(text) + "</p></section>";
  }

  // ── Nerds only ──────────────────────────────────────────────────────────────────

  function nerdsPage() {
    var s = st();
    var month = windowOf("month");
    var t = month ? month.totals : null;
    var tokenRows = t ? [
      ["Input", t.input], ["Output", t.output], ["Cache read", t.cache_read],
      ["Cache write, 5 min", t.cache_write_5m], ["Cache write, 1 hour", t.cache_write_1h],
      ["Thinking (within output)", t.thinking], ["Total", t.total_tokens]
    ].map(function (row) {
      return '<tr class="' + (row[0] === "Total" ? "is-today" : "") + '"><td class="lead">' + esc(row[0].toUpperCase()) + '</td><td class="r v">' +
        esc(fmtInt(row[1])) + "</td></tr>";
    }).join("") : '<tr><td colspan="2">No tokens counted this month yet.</td></tr>';
    var ingest = s && s.ingest ? s.ingest : null;
    var ingestText = ingest
      ? (ingest.running ? "RUNNING " + fmtInt(ingest.files_done) + " / " + fmtInt(ingest.files_total) : "IDLE, " + fmtInt(ingest.files_total) + " FILES")
      : "--";
    var kv = [
      ["LINK", s ? (STATUS[s.status] || STATUS.loading).text : "--"],
      ["FETCHED", s && s.usage_fetched_at ? stampText(parseTime(s.usage_fetched_at)) : "--"],
      ["UPDATED", s && s.updated_at ? stampText(parseTime(s.updated_at)) : "--"],
      ["INGEST", ingestText],
      ["LAST INGEST", ingest && ingest.last_duration_s !== null && ingest.last_duration_s !== undefined ? num(ingest.last_duration_s).toFixed(2) + " S" : "--"],
      ["LAST ERROR", ingest && ingest.last_error ? ingest.last_error : "NONE"],
      ["TIER", s && s.plan ? String(s.plan.rate_limit_tier || "--").replace(/_/g, " ").toUpperCase() : "--"],
      ["PRICE", s && s.plan && num(s.plan.monthly_price_usd) !== null ? fmtPlanPrice(s.plan.monthly_price_usd) + " / MO" : "UNKNOWN"]
    ];
    var burn = s && s.burn ? s.burn : null;
    var spark = S.samples.length > 1 ? CHARTS.sparkline(S.samples, { width: 520, height: 80, label: "Burn samples" }) : '<p class="nm-note">Sampling&hellip;</p>';
    var unpriced = s && s.unpriced_models && s.unpriced_models.length
      ? s.unpriced_models.map(function (id) { return "<li>" + esc(id) + "</li>"; }).join("") : "<li>NONE</li>";
    return '<div class="nm-head"><h1 class="nm-h1">NERDS ONLY<span class="nm-cur" aria-hidden="true"></span></h1><p class="nm-sub">Raw counters and pipeline health, all from this machine.</p></div>' +
      statusBanner() + '<div class="nm-pgrid">' +
      '<section class="nm-card"><div class="nm-card-in"><div class="nm-card-t"><span class="nm-ttl">Tokens this month</span><span class="nm-meta-t">exact counts</span></div>' +
      '<div class="nm-table-wrap"><table class="nm-table"><tbody>' + tokenRows + "</tbody></table></div></div></section>" +
      '<section class="nm-card"><div class="nm-card-in"><div class="nm-card-t"><span class="nm-ttl">Pipeline</span><span class="nm-meta-t">data health</span></div>' +
      '<div class="nm-kv-grid">' + kv.map(function (row) {
        return '<div class="nm-kv"><span>' + esc(row[0]) + "</span><b>" + esc(row[1]) + "</b></div>";
      }).join("") + "</div></div></section>" +
      '<section class="nm-card"><div class="nm-card-in"><div class="nm-card-t"><span class="nm-ttl">Burn</span><span class="nm-chip">' + esc(burn ? burn.velocity : "IDLE") + "</span></div>" +
      '<div class="nm-kv-grid"><div class="nm-kv"><span>TOKENS / MIN</span><b>' + esc(burn ? fmtInt(burn.tokens_per_min) : "--") +
      "</b></div><div class=\"nm-kv\"><span>USD / HOUR</span><b>" + esc(burn ? fmtUsd(burn.usd_per_hour) : "--") + "</b></div>" +
      '<div class="nm-kv"><span>WINDOW</span><b>' + esc(burn ? burn.window_minutes + " MIN" : "--") + '</b></div></div>' + spark + "</div></section>" +
      '<section class="nm-card"><div class="nm-card-in"><div class="nm-card-t"><span class="nm-ttl">Not priced</span><span class="nm-meta-t">models without a price row</span></div>' +
      '<ul class="nm-supp">' + unpriced + "</ul></div></section>" +
      futurePanel("Efficiency analytics", "Per-session efficiency, pacing curves and the compaction and error log arrive with the analytics endpoints (Phase 2).") +
      "</div>";
  }

  // ── History ─────────────────────────────────────────────────────────────────────

  function historyPage() {
    var s = st();
    var rows = WINDOW_ORDER.filter(function (k) { return windowOf(k); }).map(function (k) {
      var w = windowOf(k);
      var t = w.totals || {};
      return "<tr><td class=\"lead\">" + esc(WINDOW_LABEL[k] || k.toUpperCase()) + "</td><td>" + esc(w.start ? stampText(parseTime(w.start)) : "--") +
        "</td><td>" + esc(w.end ? stampText(parseTime(w.end)) : "OPEN") + '</td><td class="r">' + esc(fmtCount(t.requests)) +
        '</td><td class="r v">' + esc(fmtUsd(t.cost_usd)) + "</td></tr>";
    }).join("");
    var spark = S.samples.length > 1 ? CHARTS.sparkline(S.samples, { width: 900, height: 120, label: "Burn rate history" }) : '<p class="nm-note">Sampling&hellip;</p>';
    return '<div class="nm-head"><h1 class="nm-h1">HISTORY<span class="nm-cur" aria-hidden="true"></span></h1><p class="nm-sub">Window bounds and recent burn. Daily history arrives with analytics.</p></div>' +
      statusBanner() + '<div class="nm-pgrid">' +
      '<section class="nm-card wide"><div class="nm-card-in"><div class="nm-card-t"><span class="nm-ttl">Windows</span><span class="nm-meta-t">local time</span></div>' +
      '<div class="nm-table-wrap"><table class="nm-table"><thead><tr><th>Window</th><th>Starts</th><th>Ends</th><th class="r">Requests</th><th class="r">API-equiv.</th></tr></thead><tbody>' +
      (rows || '<tr><td colspan="5">No windows yet.</td></tr>') + "</tbody></table></div></div></section>" +
      '<section class="nm-card wide"><div class="nm-card-in"><div class="nm-card-t"><span class="nm-ttl">Burn rate, this session</span><span class="nm-meta-t">' +
      S.samples.length + (S.samples.length === 1 ? " sample" : " samples") + ' since the dashboard opened</span></div>' + spark + "</div></section>" +
      futurePanel("Daily history", "Day-by-day cost, usage and session history come from the analytics endpoints (Phase 2).") +
      "</div>";
  }

  // ── Settings ────────────────────────────────────────────────────────────────────

  function toggleRow(key, title, desc) {
    return '<label class="nm-row-set"><span><span class="t">' + esc(title) + '</span><span class="d" style="display:block">' + esc(desc) +
      '</span></span><span class="nm-toggle"><input type="checkbox" data-key="' + esc(key) + '" data-kind="bool"><span class="box" aria-hidden="true"></span><span class="lbl" data-lbl-for="' +
      esc(key) + '">OFF</span></span></label>';
  }

  function selectRow(key, title, desc, options) {
    return '<label class="nm-row-set"><span><span class="t">' + esc(title) + '</span><span class="d" style="display:block">' + esc(desc) +
      '</span></span><select class="nm-select" data-key="' + esc(key) + '">' + options.map(function (o) {
        return '<option value="' + esc(o[0]) + '">' + esc(o[1]) + "</option>";
      }).join("") + "</select></label>";
  }

  function settingsPage(main) {
    if (S.formKey !== "settings") {
      S.formKey = "settings";
      main.innerHTML = '<div class="nm-head"><h1 class="nm-h1">SETTINGS<span class="nm-cur" aria-hidden="true"></span></h1>' +
        '<p class="nm-sub">Every change applies at once. Nothing needs a restart.</p></div>' +
        '<div class="nm-pgrid">' +
        '<section class="nm-card wide"><div class="nm-card-in"><div class="nm-card-t"><span class="nm-ttl">Theme</span><span class="nm-meta-t" id="set-theme-meta"></span></div>' +
        '<div class="nm-themes" id="set-themes"></div><div id="set-mode" class="nm-actions" hidden></div><div class="nm-variants" id="set-accents"></div></div></section>' +
        '<section class="nm-card"><div class="nm-card-in"><div class="nm-card-t"><span class="nm-ttl">Widgets &amp; display</span><span class="nm-meta-t">floating and taskbar</span></div><div class="nm-set">' +
        toggleRow("show_floating", "Floating widget", "Show the always-on-top widget on the desktop.") +
        toggleRow("show_taskbar", "Taskbar strip", "Show the strip beside the clock.") +
        toggleRow("float_transparent_bg", "Transparent widget", "No plate behind the floating widget.") +
        toggleRow("float_text_fx", "Widget text effects", "Glow and flicker on the floating widget.") +
        toggleRow("taskbar_transparent_bg", "Transparent strip", "No plate behind the taskbar strip.") +
        toggleRow("taskbar_text_fx", "Strip text effects", "Glow on the taskbar strip.") +
        toggleRow("hide_floating_in_fullscreen", "Hide in fullscreen", "Hide the floating widget while an app is fullscreen.") +
        selectRow("show_scoped_rows", "Scoped rows", "Show rows for scoped limits on the widget.", [["auto", "AUTO"], ["always", "ALWAYS"], ["never", "NEVER"]]) +
        toggleRow("reduce_motion", "Reduce motion", "Stop the boot log, flicker and blinking.") +
        "</div></div></section>" +
        '<section class="nm-card"><div class="nm-card-in"><div class="nm-card-t"><span class="nm-ttl">Data &amp; plan</span><span class="nm-meta-t">how often, and what you pay</span></div><div class="nm-set">' +
        selectRow("poll_interval_ms", "Fetch usage every", "How often the usage API is asked. The minimum is one minute.", POLL_CHOICES) +
        selectRow("billing_day", "Billing day", "The day of the month your billing cycle starts.", [["", "NOT SET"]].concat(Array.from({ length: 31 }, function (_, i) { return [String(i + 1), "DAY " + (i + 1)]; }))) +
        '<label class="nm-row-set"><span><span class="t">Plan price override</span><span class="d" style="display:block">USD per month. Blank uses the plan&rsquo;s list price.</span></span>' +
        '<input class="nm-input" type="number" data-key="plan_price_override" min="0" max="10000" step="0.01" placeholder="AUTO" inputmode="decimal"></label>' +
        "</div><div class=\"nm-actions\"><button class=\"nm-btn\" type=\"button\" data-action=\"refresh\">Refresh now</button></div></div></section>" +
        '<section class="nm-card wide"><div class="nm-card-in"><div class="nm-card-t"><span class="nm-ttl">System</span><span class="nm-meta-t">start-up, updates and alerts</span></div><div class="nm-set">' +
        toggleRow("launch_on_startup", "Start with Windows", S.data && S.data.dev ? "Dev instance: this is not saved to Windows." : "Run the monitor when you sign in.") +
        toggleRow("check_updates", "Check for updates", "Look for a newer release once in a while.") +
        toggleRow("notifications_enabled", "Notifications", "Show a notice when a limit is nearly used up.") +
        '</div><div class="nm-actions" id="set-update-actions"><button class="nm-btn" type="button" data-action="check-update">Check for updates now</button>' +
        '<button class="nm-btn" type="button" data-action="install-update" hidden>Install update</button><button class="nm-btn" type="button" data-action="skip-update" hidden>Skip this version</button></div>' +
        '<p class="nm-status" id="set-update-status" role="status"></p></div></section>' +
        "</div>" +
        '<p class="nm-status" id="set-status" role="status" aria-live="polite"></p>';
    }
    syncSettings(main);
  }

  function syncSettings(main) {
    var settings = S.settings || {};
    main = main || $("#main");
    main.querySelectorAll("[data-key]").forEach(function (el) {
      var key = el.getAttribute("data-key");
      if (!Object.prototype.hasOwnProperty.call(settings, key) || document.activeElement === el) {
        return;
      }
      var value = settings[key];
      if (el.type === "checkbox") {
        el.checked = !!value;
        var lbl = main.querySelector('[data-lbl-for="' + key + '"]');
        if (lbl) {
          lbl.textContent = value ? "ON" : "OFF";
        }
      } else if (key === "billing_day") {
        el.value = value === null || value === undefined ? "" : String(value);
      } else if (key === "plan_price_override") {
        el.value = value === null || value === undefined ? "" : String(value);
      } else if (key === "poll_interval_ms") {
        var has = Array.prototype.some.call(el.options, function (o) { return o.value === String(value); });
        if (!has) {
          var option = document.createElement("option");
          option.value = String(value);
          option.textContent = Math.round(Number(value) / 60000) + " MIN";
          el.appendChild(option);
        }
        el.value = String(value);
      } else {
        el.value = value === null || value === undefined ? "" : String(value);
      }
    });
    syncThemePicker(main);
    syncUpdate(main);
  }

  function syncThemePicker(main) {
    var cards = $("#set-themes", main);
    var accents = $("#set-accents", main);
    var mode = $("#set-mode", main);
    var meta = $("#set-theme-meta", main);
    if (!cards || !accents || !mode) {
      return;
    }
    var current = currentTheme();
    var list = S.themes || [];
    var cardsHtml = list.map(function (t) {
      var on = current && current.id === t.id;
      var swatches = (t.variants || []).slice(0, 8).map(function (v) {
        return '<i style="background:' + esc(hex6(v.colors && (v.colors.accent || v.colors.text)) || "#888888") + '"></i>';
      }).join("");
      return '<button type="button" class="nm-theme" data-theme-id="' + esc(t.id) + '" aria-pressed="' + (on ? "true" : "false") + '"><b>' + esc(t.name) +
        "</b><small>" + esc(t.tagline || "") + '</small><span class="nm-swatches" aria-hidden="true">' + swatches + "</span></button>";
    }).join("");
    if (cards.getAttribute("data-sig") !== cardsHtml) {
      cards.setAttribute("data-sig", cardsHtml);
      cards.innerHTML = cardsHtml || '<p class="nm-empty">Themes load with the dashboard.</p>';
    }
    var theme = current ? findTheme(current.id) : null;
    meta.textContent = current ? current.name.toUpperCase() + " · " + String(current.variant_name || current.variant).toUpperCase() : "";
    var seen = {};
    var accentList = [];
    ((theme && theme.variants) || []).forEach(function (v) {
      var a = v.accent || accentOf(v.id);
      if (!seen[a]) {
        seen[a] = true;
        accentList.push(a);
      }
    });
    var active = current ? accentOf(current.variant) : "";
    var accentHtml = accentList.map(function (a) {
      return '<button type="button" class="nm-btn' + (a === active ? " solid" : "") + '" data-accent="' + esc(a) + '" aria-pressed="' + (a === active ? "true" : "false") + '">' +
        esc(a.toUpperCase()) + "</button>";
    }).join("");
    if (accents.getAttribute("data-sig") !== accentHtml) {
      accents.setAttribute("data-sig", accentHtml);
      accents.innerHTML = accentHtml;
    }
    var modes = theme && theme.modes && theme.modes.length ? theme.modes : [];
    mode.hidden = !modes.length;
    if (modes.length) {
      var currentMode = S.settings.theme_mode || "system";
      var modeHtml = '<span class="nm-status" style="margin-right:6px">MODE</span><span class="nm-seg">' + modes.map(function (m) {
        return '<button type="button" data-mode="' + esc(m) + '" class="' + (currentMode === m ? "on" : "") + '" aria-pressed="' + (currentMode === m ? "true" : "false") + '">' + esc(m.toUpperCase()) + "</button>";
      }).join("") + "</span>";
      if (mode.getAttribute("data-sig") !== modeHtml) {
        mode.setAttribute("data-sig", modeHtml);
        mode.innerHTML = modeHtml;
      }
    }
  }

  function syncUpdate(main) {
    var u = S.data && S.data.update ? S.data.update : null;
    var install = $('[data-action="install-update"]', main);
    var skip = $('[data-action="skip-update"]', main);
    if (install) {
      install.hidden = !(u && u.available);
    }
    if (skip) {
      skip.hidden = !(u && u.available);
    }
    var status = $("#set-update-status", main);
    if (status) {
      status.textContent = S.update.text || (u && u.available && u.info && u.info.version ? "Version " + u.info.version + " is available." : "");
    }
  }

  // ── About ───────────────────────────────────────────────────────────────────────

  function aboutPage(main) {
    if (S.formKey !== "about") {
      S.formKey = "about";
      main.innerHTML = '<div class="nm-head"><h1 class="nm-h1">ABOUT<span class="nm-cur" aria-hidden="true"></span></h1>' +
        '<p class="nm-sub">Claude Usage Monitor, the people behind it, and where to report problems.</p></div>' +
        '<div class="nm-pgrid">' +
        '<section class="nm-card"><div class="nm-card-in"><div class="nm-card-t"><span class="nm-ttl">Claude Usage Monitor</span><span class="nm-meta-t" id="about-version"></span></div>' +
        '<p class="nm-lede">A local monitor for Claude Code plan limits. It reads your own transcripts and the usage API. It never sends a model request.</p>' +
        '<div class="nm-kv" style="margin-top:6px;padding:9px 0"><span>AUTHORS</span><b style="font-size:18px;line-height:18px">CHRIS CARPENTER, WITH CLAUDE</b></div>' +
        '<p class="nm-note nm-disc" style="margin-top:10px">Money figures are API-equivalent at list prices, not amounts billed to your plan.</p></div></section>' +
        '<section class="nm-card"><div class="nm-card-in"><div class="nm-card-t"><span class="nm-ttl">Links</span><span class="nm-meta-t">opens in your browser</span></div>' +
        '<div class="nm-links"><a href="' + esc(LINKS.repo) + '" data-external="1">Repository on GitHub</a>' +
        '<a href="' + esc(LINKS.bug) + '" data-external="1">Report a bug</a>' +
        '<a href="' + esc(LINKS.patreon) + '" data-external="1">Support on Patreon</a></div></div></section>' +
        '<section class="nm-card wide"><div class="nm-card-in"><div class="nm-card-t"><span class="nm-ttl">Supporters</span><span class="nm-meta-t" id="about-supporters-meta">thank you</span></div>' +
        '<div id="about-supporters"><p class="nm-empty">Loading the supporters list&hellip;</p></div></div></section>' +
        "</div>";
    }
    var v = $("#about-version", main);
    if (v) {
      v.textContent = S.data && S.data.version ? "VERSION " + S.data.version + (S.data.dev ? " · DEV" : "") : "VERSION --";
    }
    if (!S.supportersRequested) {
      S.supportersRequested = true;
      api("GET", "/api/supporters").then(function (res) {
        S.supporters = res.ok ? res.data : { supporters: [], error: errorText(res) };
        renderSupporters(main);
      }, function () {
        S.supporters = { supporters: [], error: "the dashboard lost its link" };
        renderSupporters(main);
      });
    }
    renderSupporters(main);
  }

  function renderSupporters(main) {
    var box = $("#about-supporters", main);
    if (!box) {
      return;
    }
    if (!S.supporters) {
      box.innerHTML = '<p class="nm-empty">Loading the supporters list&hellip;</p>';
      return;
    }
    var groups = S.supporters.tiers && S.supporters.tiers.length
      ? S.supporters.tiers
      : (S.supporters.supporters || []).length ? [{ tier: "", names: S.supporters.supporters }] : [];
    var note = S.supporters.error ? '<p class="nm-note nm-disc" style="margin-top:10px">Reason: ' + esc(S.supporters.error) + "</p>" : "";
    if (groups.length) {
      box.innerHTML = groups.map(function (g) {
        return (g.tier ? '<p class="nm-note" style="margin-top:8px">' + esc(g.tier) + "</p>" : "") +
          '<ul class="nm-supp">' + g.names.map(function (n) { return "<li>" + esc(n) + "</li>"; }).join("") + "</ul>";
      }).join("") + note;
    } else {
      box.innerHTML = '<p class="nm-empty">' + (S.supporters.error ? "The supporters list is unavailable right now." : "No supporters listed yet.") + "</p>" + note;
    }
    var meta = $("#about-supporters-meta", main);
    if (meta) {
      meta.textContent = S.supporters.error ? "list unavailable" : "thank you";
    }
  }

  function openExternal(url) {
    var bridge = window.pywebview && window.pywebview.api;
    if (bridge && typeof bridge.open_external === "function") {
      try {
        bridge.open_external(url);
        return;
      } catch (e) {
        /* fall through to the browser */
      }
    }
    window.open(url, "_blank", "noopener");
  }

  // ── Settings actions ────────────────────────────────────────────────────────────

  function setStatus(text, isError) {
    S.status = { text: text, error: !!isError };
    var el = $("#set-status");
    if (el) {
      el.textContent = text;
      el.className = "nm-status" + (isError ? " is-err" : "");
    }
  }

  function saveSettings(patch, label) {
    setStatus("SAVING " + label + "…", false);
    return api("POST", "/api/settings", patch).then(function (res) {
      if (res.ok && res.data) {
        S.settings = res.data.settings || S.settings;
        setStatus("SAVED " + label, false);
        poll(true);
      } else {
        setStatus("NOT SAVED: " + errorText(res), true);
      }
      syncSettings($("#main"));
    }, function () {
      setStatus("NOT SAVED: THE DASHBOARD LOST ITS LINK", true);
    });
  }

  function labelFor(key) {
    return String(key).replace(/_/g, " ").toUpperCase();
  }

  function applyThemeChoice(themeId, variantId) {
    var patch = { theme_id: themeId, theme_variant: variantId };
    var theme = findTheme(themeId);
    var name = theme ? theme.name : themeId;
    saveSettings(patch, name.toUpperCase());
  }

  function settingsChange(event) {
    var el = event.target;
    var key = el.getAttribute && el.getAttribute("data-key");
    if (!key) {
      return;
    }
    var value;
    if (el.type === "checkbox") {
      value = el.checked;
    } else if (key === "billing_day") {
      value = el.value === "" ? null : parseInt(el.value, 10);
    } else if (key === "plan_price_override") {
      if (el.value === "") {
        value = null;
      } else {
        value = Number(el.value);
        if (!isFinite(value) || value < 0 || value > 10000) {
          setStatus("PLAN PRICE MUST BE BETWEEN 0 AND 10000", true);
          syncSettings($("#main"));
          return;
        }
      }
    } else if (key === "poll_interval_ms") {
      value = parseInt(el.value, 10);
    } else {
      value = el.value;
    }
    var patch = {};
    patch[key] = value;
    saveSettings(patch, labelFor(key));
  }

  function settingsClick(event) {
    var target = event.target.closest ? event.target.closest("button") : null;
    if (!target) {
      return;
    }
    if (target.hasAttribute("data-theme-id")) {
      var t = findTheme(target.getAttribute("data-theme-id"));
      if (t) {
        applyThemeChoice(t.id, accentOf(t.default_variant));
      }
      return;
    }
    if (target.hasAttribute("data-accent")) {
      var accent = target.getAttribute("data-accent");
      saveSettings({ theme_variant: accent }, accent.toUpperCase());
      return;
    }
    if (target.hasAttribute("data-mode")) {
      var mode = target.getAttribute("data-mode");
      saveSettings({ theme_mode: mode }, "MODE " + mode.toUpperCase());
      return;
    }
    var action = target.getAttribute("data-action");
    if (action === "refresh") {
      api("POST", "/api/refresh").then(function (res) {
        setStatus(res.ok ? "REFRESH REQUESTED" : "REFRESH FAILED: " + errorText(res), !res.ok);
        poll(true);
      });
    } else if (action === "check-update") {
      S.update = { text: "CHECKING…" };
      api("POST", "/api/update/check").then(function (res) {
        S.update = { text: res.ok ? (res.data && res.data.result && res.data.result.available ? "AN UPDATE IS AVAILABLE." : "YOU ARE UP TO DATE.") : "CHECK FAILED: " + errorText(res) };
        api("GET", "/api/state").then(function (r) { if (r.ok) { S.data = r.data; S.etag = r.etag; } syncSettings($("#main")); });
      });
    } else if (action === "install-update") {
      S.update = { text: "INSTALLING…" };
      api("POST", "/api/update/apply").then(function (res) {
        S.update = { text: res.ok ? "THE UPDATE WILL INSTALL WHEN THE MONITOR RESTARTS." : "UPDATE FAILED: " + errorText(res) };
        syncSettings($("#main"));
      });
    } else if (action === "skip-update") {
      api("POST", "/api/update/skip").then(function (res) {
        S.update = { text: res.ok ? "THIS VERSION WILL BE SKIPPED." : "COULD NOT SKIP: " + errorText(res) };
        syncSettings($("#main"));
      });
    }
  }

  // ── Theme application ───────────────────────────────────────────────────────────

  function pywebviewBridge() {
    return window.pywebview && window.pywebview.api ? window.pywebview.api : null;
  }

  function pushChrome(theme) {
    var bridge = pywebviewBridge();
    if (!bridge || typeof bridge.set_chrome !== "function" || !theme) {
      return;
    }
    var css = theme.css_vars || {};
    var caption = hex6(css["--bg"]);
    var text = hex6(css["--text"] || css["--text-bright"]);
    var border = hex6(css["--border"]);
    if (!caption || !text || !border) {
      return;
    }
    try {
      bridge.set_chrome(caption, text, border, theme.mode !== "light");
    } catch (e) {
      /* the native caption is cosmetic */
    }
  }

  function loadThemeCss(id, hasCss) {
    var link = document.getElementById("theme-css");
    if (!link || link.getAttribute("data-theme") === id) {
      return;
    }
    link.setAttribute("data-theme", id);
    link.onerror = function () {
      link.onerror = null;
      link.setAttribute("href", FALLBACK_CSS);
    };
    link.setAttribute("href", hasCss ? "/static/themes/" + encodeURIComponent(id) + ".css" : FALLBACK_CSS);
  }

  function startThemeFx(id) {
    var fx = window.CUM_THEME_FX && window.CUM_THEME_FX[id];
    if (fx && typeof fx.start === "function" && document.documentElement.getAttribute("data-theme") === id) {
      fx.start();
    }
  }

  function loadThemeScript(id, hasJs) {
    if (!hasJs) {
      return;
    }
    if (S.loadedScripts[id]) {
      startThemeFx(id);
      return;
    }
    S.loadedScripts[id] = "loading";
    var script = document.createElement("script");
    script.src = "/static/themes/" + encodeURIComponent(id) + ".js";
    script.onload = function () {
      S.loadedScripts[id] = "ready";
      startThemeFx(id);
    };
    script.onerror = function () {
      S.loadedScripts[id] = "none";
    };
    document.head.appendChild(script);
  }

  function applyTheme(theme) {
    if (!theme || !theme.css_vars) {
      return;
    }
    var key = [theme.id, theme.variant, theme.mode, JSON.stringify(theme.css_vars)].join("|");
    if (key === S.themeKey) {
      return;
    }
    S.themeKey = key;
    S.lastTheme = theme;
    var root = document.documentElement;
    S.appliedVars.forEach(function (name) { root.style.removeProperty(name); });
    S.appliedVars = Object.keys(theme.css_vars);
    S.appliedVars.forEach(function (name) { root.style.setProperty(name, String(theme.css_vars[name])); });
    root.setAttribute("data-theme", theme.id);
    root.setAttribute("data-variant", theme.variant);
    root.style.colorScheme = theme.mode === "light" ? "light" : "dark";
    var assets = theme.assets || { css: true, js: false };
    loadThemeCss(theme.id, !!assets.css);
    loadThemeScript(theme.id, !!assets.js);
    pushChrome(theme);
  }

  // ── Rendering ───────────────────────────────────────────────────────────────────

  function currentPage() {
    var key = (window.location.hash || "").replace(/^#/, "");
    return PAGE_KEYS.indexOf(key) >= 0 ? key : "overview";
  }

  function renderChrome() {
    var s = st();
    var stale = !!(s && s.status !== "live");
    var info = s ? (STATUS[s.status] || STATUS.loading) : STATUS.loading;
    var linkDown = S.linkOk === false;
    var text = linkDown ? "NO LINK" : info.text;
    var up = document.getElementById("uplink");
    var dot = document.getElementById("uplink-dot");
    var upText = document.getElementById("uplink-text");
    if (upText) {
      upText.textContent = text;
    }
    if (up) {
      up.className = "nm-uplink" + (linkDown || !info.ok ? " is-down" : "");
    }
    if (dot) {
      dot.className = "nm-alive" + (linkDown || !info.ok ? " is-off" : "");
    }
    var screen = document.getElementById("screen");
    if (screen) {
      screen.classList.toggle("is-stale", linkDown || stale && s.status !== "paused");
    }
    renderAge();
    renderHealth();
    var refresh = document.getElementById("refresh");
    if (refresh) {
      refresh.disabled = linkDown;
    }
  }

  function ageMs() {
    var u = usage();
    var when = parseTime(u && u.fetched_at) || parseTime(st() && st().updated_at);
    return when === null ? null : Date.now() - when;
  }

  function renderAge() {
    var a = ageMs();
    var text = a === null ? "--" : fmtAge(a);
    var el = document.getElementById("data-age");
    if (el) {
      el.textContent = "DATA AGE " + text;
    }
    var updated = document.querySelector("#health [data-updated]");
    if (updated) {
      updated.textContent = a === null ? "--" : text + " AGO";
    }
  }

  /* Rebuilt only when its content changes, so the alive dot keeps its blink across the 1 s ticks. */
  function renderHealth() {
    var box = document.getElementById("health");
    if (!box) {
      return;
    }
    var s = st();
    var ingest = s && s.ingest;
    var sources = ingest
      ? (ingest.running ? "INGEST " + Math.floor(100 * (ingest.files_done || 0) / Math.max(1, ingest.files_total || 1)) + "%"
        : fmtInt(ingest.files_total) + " OK")
      : "--";
    var unpriced = s && s.unpriced_models ? s.unpriced_models.length : 0;
    var link = S.linkOk === false ? "NO LINK" : (s ? (STATUS[s.status] || STATUS.loading).text.replace("UPLINK ", "") : "--");
    var live = !!(s && s.status === "live");
    var signature = [link, sources, unpriced, live].join("|");
    if (box.getAttribute("data-sig") !== signature) {
      box.setAttribute("data-sig", signature);
      box.innerHTML = '<div class="nm-kick">DATA HEALTH</div>' +
        '<div class="nm-kv"><span>LINK</span><b class="ok"><i class="nm-alive' + (live ? "" : " is-off") + '"></i>' + esc(link) + "</b></div>" +
        '<div class="nm-kv"><span>UPDATED</span><b data-updated="1">--</b></div>' +
        '<div class="nm-kv"><span>SOURCES</span><b>' + esc(sources) + "</b></div>" +
        '<div class="nm-kv"><span>UNPRICED</span><b>' + esc(String(unpriced)) + "</b></div>";
    }
    renderAge();
  }

  function renderVariants() {
    var box = document.getElementById("variants");
    var label = document.getElementById("phos-label");
    if (!box) {
      return;
    }
    var theme = currentTheme();
    var meta = theme ? findTheme(theme.id) : null;
    if (label) {
      label.textContent = theme && theme.id === "nostromo" ? "PHOSPHOR" : "ACCENT";
    }
    if (!meta) {
      box.innerHTML = "";
      return;
    }
    var seen = {};
    var accents = [];
    (meta.variants || []).forEach(function (v) {
      var a = v.accent || accentOf(v.id);
      if (!seen[a]) {
        seen[a] = true;
        accents.push(a);
      }
    });
    var active = theme ? accentOf(theme.variant) : "";
    var html = accents.slice(0, 8).map(function (a) {
      return '<button type="button" data-accent="' + esc(a) + '" class="' + (a === active ? "on" : "") + '" aria-pressed="' + (a === active ? "true" : "false") +
        '" title="' + esc(a) + '">' + esc(ACCENT_ABBR[a] || a.slice(0, 3).toUpperCase()) + "</button>";
    }).join("");
    if (box.getAttribute("data-sig") !== html) {
      box.setAttribute("data-sig", html);
      box.innerHTML = html;
    }
  }

  function markNav(key) {
    document.querySelectorAll("#menu a").forEach(function (a) {
      var on = a.getAttribute("data-page") === key;
      a.classList.toggle("on", on);
      if (on) {
        a.setAttribute("aria-current", "page");
      } else {
        a.removeAttribute("aria-current");
      }
    });
  }

  function renderPage() {
    var key = currentPage();
    var main = document.getElementById("main");
    if (!main) {
      return;
    }
    markNav(key);
    if (key !== S.page) {
      S.page = key;
      S.formKey = null;
      main.scrollTop = 0;
    }
    if (!S.data) {
      main.innerHTML = S.linkOk === false
        ? '<p class="nm-empty">No link to the monitor. Retrying every two seconds.</p>'
        : '<p class="nm-empty">Waiting for the first usage reading.</p>';
      return;
    }
    if (key === "settings") {
      settingsPage(main);
    } else if (key === "about") {
      aboutPage(main);
    } else {
      S.formKey = null;
      var renderers = { overview: overviewPage, limits: limitsPage, cost: costPage, nerds: nerdsPage, history: historyPage };
      main.innerHTML = (renderers[key] || overviewPage)();
    }
  }

  function render() {
    renderChrome();
    renderVariants();
    renderPage();
  }

  function addSample(value) {
    var n = num(value);
    if (n === null) {
      return;
    }
    S.samples.push(n);
    if (S.samples.length > 24) {
      S.samples.shift();
    }
  }

  function ensureThemes() {
    if (S.themesRequested) {
      return;
    }
    S.themesRequested = true;
    api("GET", "/api/themes").then(function (res) {
      if (res.ok && res.data && res.data.themes) {
        S.themes = res.data.themes;
        render();
      } else {
        S.themesRequested = false;
      }
    }, function () {
      S.themesRequested = false;
    });
  }

  function onState() {
    var s = st();
    if (s && s.updated_at && s.updated_at !== S.lastUpdatedAt) {
      S.lastUpdatedAt = s.updated_at;
      if (s.burn) {
        addSample(s.burn.tokens_per_min);
      }
    }
    S.settings = S.data.settings || {};
    document.documentElement.setAttribute("data-reduce-motion", S.settings.reduce_motion ? "1" : "0");
    applyTheme(S.data.theme);
    ensureThemes();
    render();
  }

  // ── Polling ─────────────────────────────────────────────────────────────────────

  var pollTimer = null;
  var pollAgain = false;

  function schedule() {
    window.clearTimeout(pollTimer);
    pollTimer = window.setTimeout(poll, POLL_MS);
  }

  /* force: fetch now and ignore the cached ETag. Used after a change, so the new state shows at once. */
  function poll(force) {
    window.clearTimeout(pollTimer);
    if (force === true) {
      S.etag = null;
    }
    if (S.inFlight) {
      if (force === true) {
        pollAgain = true;
      }
      return;
    }
    if (document.visibilityState === "hidden" && force !== true) {
      return;
    }
    S.inFlight = true;
    api("GET", "/api/state", null, S.etag).then(function (res) {
      if (res.status === 304) {
        S.linkOk = true;
      } else if (res.ok && res.data) {
        S.linkOk = true;
        S.etag = res.etag;
        S.data = res.data;
        onState();
      } else {
        S.linkOk = false;
        renderChrome();
      }
      afterRequest();
    }, function () {
      S.linkOk = false;
      renderChrome();
      afterRequest();
    });
  }

  function afterRequest() {
    S.inFlight = false;
    if (pollAgain) {
      pollAgain = false;
      poll(true);
      return;
    }
    schedule();
  }

  function tick() {
    renderAge();
    renderHealth();
    var minute = String(Math.floor(Date.now() / 60000));
    if (minute !== S.minuteKey) {
      S.minuteKey = minute;
      if (S.data) {
        renderPage();
      }
    }
  }

  // ── Start-up ────────────────────────────────────────────────────────────────────

  function init() {
    var main = document.getElementById("main");
    var variants = document.getElementById("variants");
    var refresh = document.getElementById("refresh");
    window.addEventListener("hashchange", function () {
      renderPage();
      if (main) {
        main.focus({ preventScroll: true });
      }
    });
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") {
        poll();
      }
    });
    window.addEventListener("pywebviewready", function () {
      pushChrome(S.lastTheme);
    });
    if (refresh) {
      refresh.addEventListener("click", function () {
        api("POST", "/api/refresh").then(function () {
          poll(true);
        });
      });
    }
    if (variants) {
      variants.addEventListener("click", function (event) {
        var button = event.target.closest ? event.target.closest("button[data-accent]") : null;
        if (button) {
          saveSettings({ theme_variant: button.getAttribute("data-accent") }, button.getAttribute("data-accent").toUpperCase());
        }
      });
    }
    if (main) {
      main.addEventListener("change", function (event) {
        if (S.formKey === "settings") {
          settingsChange(event);
        }
      });
      main.addEventListener("click", function (event) {
        var link = event.target.closest ? event.target.closest("a[data-external]") : null;
        if (link) {
          event.preventDefault();
          openExternal(link.href);
          return;
        }
        var windowChoice = event.target.closest ? event.target.closest("button[data-cost-window]") : null;
        if (windowChoice) {
          S.costWindow = windowChoice.getAttribute("data-cost-window");
          renderPage();
          return;
        }
        if (S.formKey === "settings") {
          settingsClick(event);
        }
      });
    }
    window.setInterval(tick, 1000);
    poll();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
