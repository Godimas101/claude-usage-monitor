/* formats.js: the dashboard's numbers, percents, money, dates, times, durations, lists and letter case in the monitor's
 * locale (#91), for every page script.
 *
 * window.MonitorFormats.make(locale) gives the formats of a locale as the monitor-locale meta holds it ({ language,
 * format, ... }); MonitorFormats.active() gives this page's, from that meta (English when it is missing or cannot be
 * read, as app.js's LOCALE). Nothing here reads the browser's language: every Intl call names the format tag.
 *
 * English keeps today's US shapes byte for byte: its branch of each format is the code the page had, which
 * tests/fixtures/webui_formats_english.json holds. The pseudo-locale (#89) takes English's shapes with two changes, as
 * usage_monitor/formats.py does: numbers grouped with U+202F, and a U+00A0 before "%". So does any other language without
 * formats of its own. French, Spanish and Turkish follow their format tag (Windows' regional format when it is of the
 * same language, else fr-FR, es-ES, tr-TR: i18n.format_tag) through Intl, from the WebView's own CLDR data.
 *
 * Rounding stays with the caller. number, percent, usd and compact take the digits the caller rounded to, as text
 * ("34", "33.5", "-1235"), and change only the signs, the grouping and the sign's side, so every language rounds as
 * English does; text that is not plain digits comes back as given. A number, not text, is written as toLocaleString
 * writes it (number, usd and money), for the callers that had that.
 *
 * US dollars are the plain "$" in every language (Chris's decision on #91: Intl's narrowSymbol, "5,96 $", not
 * "5,96 $US"). tight drops the space between a number and its "%" or "$" (Chris's decision on #91: the boxes sized for
 * English's figures; on the page, the money tiles and the big cost figure); the sign keeps its side, and a currency code
 * keeps its space.
 *
 * Dates and times are the page's local time. English writes them as the page did, in capitals ("WED 7:39 PM", "OCT 21",
 * 12-hour with an ASCII space); French, Spanish and Turkish take Intl's for the tag in CLDR's own case ("sam. 16:12"),
 * on the clock the monitor follows (Windows' 12- or 24-hour setting: the locale's clock), and the page puts them in its
 * capitals with upper(). Durations (fr, es, tr only, as formats.py's) are CLDR's narrow units ("2j 3h", Turkish "3s
 * 27d"). Lists are English's "X, Y and Z" (no Oxford comma), else Intl's. upper() and lower() follow the language's rule
 * (Turkish "HAİKU"), as usage_monitor/casing.py does.
 *
 * Intl as WebView2 86 has it (ui/dashboard.py WEBVIEW2_MIN_VERSION): NumberFormat with compact notation, narrowSymbol,
 * units and formatToParts; DateTimeFormat with hourCycle and timeStyle; ListFormat. Intl.DurationFormat is newer: it is
 * used where the WebView has it, and the same CLDR units through NumberFormat and ListFormat otherwise (D14 of #91's
 * plan; by ECMA-402 the two write the same text: tests/webui_cldr.js writes both, and test_webui.py's D14 test compares
 * them).
 */
(function (root) {
  "use strict";

  // The languages with formats of their own, and each one's base tag (i18n.BASE_FORMATS).
  var BASES = { fr: "fr-FR", es: "es-ES", tr: "tr-TR" };
  var PSEUDO = "pseudo";
  var PSEUDO_GROUP = "\u202f";
  var PSEUDO_PERCENT = "\u00a0%";
  // English's compact letters, by the power of ten the digits count in.
  var SCALES = { 3: "K", 6: "M", 9: "B" };
  var DIGITS = /^(-?)(\d+)(?:\.(\d+))?$/;
  var NUMERIC = { integer: true, group: true, decimal: true, fraction: true, nan: true, infinity: true };
  // English's weekday and month names as the page writes them, in capitals. Weekdays count from Monday, as formats.py's
  // (datetime.weekday()); Date.getDay() counts from Sunday.
  var WEEKDAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];
  var MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
  var MONDAY = Date.UTC(2026, 9, 19, 12);        // a Monday at noon, UTC: the weekdays Intl names
  var DAY_MS = 86400000;
  // formats.py's duration units (countdown_units and age_unit's codes), as Intl's NumberFormat and DurationFormat name them.
  var UNITS = { d: "day", h: "hour", m: "minute", s: "second" };
  var DURATION_FIELDS = { d: "days", h: "hours", m: "minutes", s: "seconds" };
  // Intl.DurationFormat when the WebView has it (D14). Set to false to write every duration through NumberFormat's units.
  var DURATION_FORMAT = typeof Intl.DurationFormat === "function";
  // The languages whose capitals and lower case keep the dot on the i (usage_monitor/casing.py's TURKIC).
  var TURKIC = { tr: true, az: true };

  function own(object, key) {
    return Object.prototype.hasOwnProperty.call(object, key);
  }

  function pad2(n) {
    return (n < 10 ? "0" : "") + n;
  }

  /* options with extra's keys over them, as a new object. */
  function joinedOptions(options, extra) {
    var out = {};
    var key;
    for (key in options) {
      if (own(options, key)) {
        out[key] = options[key];
      }
    }
    for (key in extra || {}) {
      if (own(extra, key)) {
        out[key] = extra[key];
      }
    }
    return out;
  }

  /* The tag Intl is given: the format tag when it is of the language and Intl takes it, else the language's base. */
  function tagOf(language, format) {
    var tag = String(format || "").replace(/_/g, "-");
    if (tag.split("-")[0].toLowerCase() !== language) {
      return BASES[language];
    }
    try {
      new Intl.NumberFormat(tag);
      return tag;
    } catch (e) {
      return BASES[language];
    }
  }

  /* The parts of a formatted number as one text. */
  function joined(parts) {
    var out = "";
    for (var i = 0; i < parts.length; i++) {
      out += parts[i].value;
    }
    return out;
  }

  /* A percent's or an amount's parts as (before, number, after): the sign and the minus, the digits, the rest. */
  function around(parts) {
    var out = ["", "", ""];
    var at = 0;
    for (var i = 0; i < parts.length; i++) {
      var numeric = own(NUMERIC, parts[i].type);
      if (numeric && at === 0) {
        at = 1;
      } else if (!numeric && at === 1) {
        at = 2;
      }
      out[at] += parts[i].value;
    }
    return out;
  }

  /* The parts without the space between the number and the currency's "$" (a currency code keeps its space). */
  function tightened(parts) {
    var out = [];
    for (var i = 0; i < parts.length; i++) {
      var part = parts[i];
      var space = part.type === "literal" && /^\s+$/.test(part.value);
      var beside = (i > 0 && parts[i - 1].type === "currency" && !/[A-Za-z]/.test(parts[i - 1].value)) ||
        (i + 1 < parts.length && parts[i + 1].type === "currency" && !/[A-Za-z]/.test(parts[i + 1].value));
      if (!(space && beside)) {
        out.push(part);
      }
    }
    return out;
  }

  /* The formats of a locale: { language, format } as the monitor-locale meta holds it; anything else is English. */
  function make(locale) {
    var given = locale !== null && typeof locale === "object" ? locale : {};
    var language = typeof given.language === "string" ? given.language : "en";
    var cldr = own(BASES, language);
    var pseudo = language === PSEUDO;
    var tag = cldr ? tagOf(language, given.format) : "en-US";
    // The clock the monitor follows (Windows' 12- or 24-hour setting, i18n.clock_hours). English's is always 12-hour.
    var hours = cldr && given.clock !== 12 ? 24 : 12;
    var hourCycle = hours === 12 ? "h12" : "h23";
    var hourWidth = null;
    var made = {};

    /* An Intl formatter of the tag (Intl.NumberFormat, Intl.DateTimeFormat, ...), made once for each set of options. */
    function intl(Kind, name, options) {
      var key = name + JSON.stringify(options);
      if (!own(made, key)) {
        made[key] = new Kind(tag, options);
      }
      return made[key];
    }

    function nf(options) {
      return intl(Intl.NumberFormat, "n", options);
    }

    function dtf(options) {
      return intl(Intl.DateTimeFormat, "d", options);
    }

    /* English's own text, with the pseudo-locale's groups. */
    function grouped(parts) {
      if (!pseudo) {
        return joined(parts);
      }
      var out = "";
      for (var i = 0; i < parts.length; i++) {
        out += parts[i].type === "group" ? PSEUDO_GROUP : parts[i].value;
      }
      return out;
    }

    function fraction(match) {
      return match[3] ? match[3].length : 0;
    }

    /* Intl's options for d decimals (Intl's own when d is undefined), and no group separator when group is false. The
     * grouping is otherwise the locale's: useGrouping: true would group where CLDR does not (es-ES's 1234). */
    function digits(d, group) {
      var options = group === false ? { useGrouping: false } : {};
      if (d !== undefined) {
        options.minimumFractionDigits = d;
        options.maximumFractionDigits = d;
      }
      return options;
    }

    /* The caller's digits ("1234.5"), grouped unless opts.group is false: "1,234.5" in English, "1 234,5" (U+202F) in
     * fr-FR, "1234,5" in es-ES (two digits before a group, CLDR's minimum), "1.234,5" in Turkish. A number is written
     * as toLocaleString writes it, with opts.digits decimals when given ("-0" stays "-0"). */
    function number(value, opts) {
      var o = opts || {};
      if (typeof value === "number") {
        return grouped(nf(digits(o.digits, o.group)).formatToParts(value));
      }
      var text = String(value);
      var m = DIGITS.exec(text);
      if (!m) {
        return text;
      }
      if (!cldr) {
        return o.group === false ? text :
          m[1] + m[2].replace(/\B(?=(\d{3})+$)/g, pseudo ? PSEUDO_GROUP : ",") + (m[3] ? "." + m[3] : "");
      }
      return nf(digits(fraction(m), o.group)).format(Number(text));
    }

    /* (before, number, after) of the caller's digits as a percent, so a figure can set the sign apart: ("", "34", "%")
     * in English (the digits as given), ("", "34", "\u00a0%") in French and Spanish, ("%", "34", "") in Turkish. A minus
     * stays with the digits in English and the pseudo-locale (English's ("", "-34", "%")); in French, Spanish and Turkish
     * it is part of before (("-", "34", "\u00a0%"), Turkish ("-%", "34", "")). opts.tight drops the space beside
     * the sign. */
    function percentParts(value, opts) {
      var text = String(value);
      var m = DIGITS.exec(text);
      var parts;
      if (!cldr || !m) {
        parts = ["", text, pseudo ? PSEUDO_PERCENT : "%"];
      } else {
        var d = fraction(m);
        parts = around(nf({ style: "percent", minimumFractionDigits: d, maximumFractionDigits: d })
          .formatToParts(Number(text) / 100));
      }
      if (opts && opts.tight) {
        parts = [parts[0].replace(/\s+$/, ""), parts[1], parts[2].replace(/^\s+/, "")];
      }
      return parts;
    }

    /* The caller's digits as a percent: "34%" in English, "34 %" (U+00A0) in French and Spanish, "%34" in Turkish. */
    function percent(value, opts) {
      return percentParts(value, opts).join("");
    }

    /* US dollars, from the caller's digits as text or from a number with opts.digits decimals: "$5.96", "-$1,235" in
     * English (as fmtUsd wrote them), "5,96 $" in fr, "$5.96" in es-MX, "$5,96" in tr. opts.scale (3, 6 or 9) counts
     * the digits in thousands, millions or billions: "$1.2K" in English (a minus after the "$", as the page had it),
     * compact notation otherwise ("1,2 k $", tr "1,2 B $"). opts.tight drops the space beside the "$". */
    function usd(value, opts) {
      var o = opts || {};
      var isText = typeof value !== "number";
      var text = String(value);
      var m = isText ? DIGITS.exec(text) : null;
      if (!cldr) {
        if (o.scale) {
          return "$" + text + SCALES[o.scale];
        }
        if (!isText) {
          return (value < 0 ? "-$" : "$") + number(Math.abs(value), { digits: o.digits });
        }
        return m ? (m[1] ? "-$" : "$") + number(text.slice(m[1].length)) : "$" + text;
      }
      if (isText && !m) {
        return text;
      }
      var options = digits(isText ? fraction(m) : o.digits);
      options.style = "currency";
      options.currency = "USD";
      options.currencyDisplay = "narrowSymbol";
      if (o.scale) {
        options.notation = "compact";
      }
      var parts = nf(options).formatToParts(isText ? Number(text + (o.scale ? "e" + o.scale : "")) : value);
      return joined(o.tight ? tightened(parts) : parts);
    }

    /* An amount in a currency by its ISO code (fmtMoney: real money in the account's own currency). English writes it
     * as Intl's en-US does ("CA$5.96", as the page had it), and a code Intl refuses after the number ("5.96 EURO").
     * French, Spanish and Turkish write US dollars with the plain "$" and every other currency by its code, where the
     * tag puts a code, as usage_monitor/formats.py's money does ("5,96 CAD" in fr-CA, "MXN 5.96" in es-MX): a local
     * symbol can be a plain "$" too (fr-CA's CAD, es-MX's MXN), which the page's US-dollar figures use. A code Intl
     * refuses goes in the same place ("5,96 EURO" in fr, "EURO 5.96" in es-MX). */
    function money(value, code) {
      var options = { style: "currency", currency: code };
      if (cldr) {
        options.currencyDisplay = code === "USD" ? "narrowSymbol" : "code";
      }
      var format;
      try {
        format = nf(options);
      } catch (e) {
        if (!cldr) {
          return number(value, { digits: 2 }) + " " + code;
        }
        var parts = nf({ style: "currency", currency: "USD", currencyDisplay: "code", minimumFractionDigits: 2,
          maximumFractionDigits: 2 }).formatToParts(value);
        var out = "";
        for (var i = 0; i < parts.length; i++) {
          out += parts[i].type === "currency" ? code : parts[i].value;
        }
        return out;
      }
      return grouped(format.formatToParts(value));
    }

    /* A count in compact notation, from the caller's digits counted in 10^scale: "1.2K" in English, "1,2 k" in French,
     * "1,2 B" (a thousand) in Turkish, "1,2 mil" in Spanish. Where the tag's unit is smaller than the caller's (Spanish
     * has none for 10^9 to 10^10 and writes those in millions, "2254 M"), the digits would claim a precision the
     * caller rounded away ("2250,00 M"): the figure is then written from exact, the unrounded count, with as many fewer
     * decimals as the tag writes more whole digits. */
    function compact(value, scale, exact) {
      var text = String(value);
      var m = DIGITS.exec(text);
      if (!cldr || !m) {
        return text + SCALES[scale];
      }
      var d = fraction(m);
      var extra = typeof exact === "number" && isFinite(exact) ? compactDigits(m[2].length + scale - 1) - m[2].length : 0;
      if (extra > 0) {
        var kept = Math.max(0, d - extra);
        return nf({ notation: "compact", minimumFractionDigits: kept, maximumFractionDigits: kept }).format(exact);
      }
      return nf({ notation: "compact", minimumFractionDigits: d, maximumFractionDigits: d })
        .format(Number(text + "e" + scale));
    }

    /* How many whole digits the tag's compact notation writes at 10^power: 1 in "1 Md", 4 in Spanish's "1000 M". */
    function compactDigits(power) {
      var parts = nf({ notation: "compact", maximumFractionDigits: 0 }).formatToParts(Number("1e" + power));
      var count = 0;
      for (var i = 0; i < parts.length; i++) {
        if (parts[i].type === "integer") {
          count += parts[i].value.length;
        }
      }
      return count;
    }

    // ── Dates and times: the page's local time ────────────────────────────────────────────────────────────────

    /* How wide Intl writes the hour on the 24-hour clock: Intl writes it as wide as it is asked, so it is read once from
     * the tag's own short time ("09:05" in fr-FR and es-MX, "9:05" in es-ES, as CLDR's Hm patterns have it). CLDR's
     * 12-hour patterns of fr, es and tr all write the hour as is ("9:05 PM"). */
    function hourDigits() {
      if (hourWidth === null) {
        hourWidth = "numeric";
        if (hours === 24) {
          try {
            var parts = dtf({ timeStyle: "short", hourCycle: hourCycle, timeZone: "UTC" })
              .formatToParts(Date.UTC(2026, 0, 5, 9, 5));
            for (var i = 0; i < parts.length; i++) {
              if (parts[i].type === "hour" && parts[i].value.length === 2) {
                hourWidth = "2-digit";
              }
            }
          } catch (e) {
            /* a WebView without timeStyle: the hour as is */
          }
        }
      }
      return hourWidth;
    }

    /* The tag's time of day with extra fields (a weekday, a date), on the monitor's clock. */
    function timeOf(extra) {
      return dtf(joinedOptions({ hour: hourDigits(), minute: "2-digit", hourCycle: hourCycle }, extra));
    }

    function at(value) {
      return new Date(+value);
    }

    /* The time of day: "7:39 PM" in English (always 12-hour, an ASCII space), "16:12" or "4:12 PM" in fr-FR, "16 h 12" in
     * fr-CA, "ÖS 4:12" in Turkish on the 12-hour clock. */
    function clock(value) {
      var d = at(value);
      if (!cldr) {
        var h = d.getHours();
        return (h % 12 || 12) + ":" + pad2(d.getMinutes()) + " " + (h >= 12 ? "PM" : "AM");
      }
      return timeOf({}).format(d);
    }

    /* The weekday and the time: "WED 7:39 PM" in English, "sam. 16:12" in fr-FR, "sáb, 16:12" in es-ES, "Cmt 16:12" in
     * Turkish. */
    function dayClock(value) {
      var d = at(value);
      return cldr ? timeOf({ weekday: "short" }).format(d) : WEEKDAYS[(d.getDay() + 6) % 7] + " " + clock(d);
    }

    /* The hour alone, for a chart's axis: "4 PM" in English, "16 h" in fr-FR, "16" in es-ES and Turkish. */
    function hour(value) {
      var d = at(value);
      if (!cldr) {
        var h = d.getHours();
        return (h % 12 || 12) + " " + (h >= 12 ? "PM" : "AM");
      }
      return dtf({ hour: hourDigits(), hourCycle: hourCycle }).format(d);
    }

    /* The date and the time: "OCT 21 7:39 PM" in English, "21 oct., 19:39" in fr-FR. */
    function stamp(value) {
      var d = at(value);
      return cldr ? timeOf({ month: "short", day: "numeric" }).format(d) : MONTHS[d.getMonth()] + " " + d.getDate() + " " + clock(d);
    }

    /* The day and the month: "OCT 21" in English, "21 oct." in French, "21 Eki" in Turkish. */
    function monthDay(value) {
      var d = at(value);
      return cldr ? dtf({ month: "short", day: "numeric" }).format(d) : MONTHS[d.getMonth()] + " " + d.getDate();
    }

    /* A date with its year: "OCT 21, 2026" in English, "21 oct. 2026" in French. */
    function date(value) {
      var d = at(value);
      return cldr ? dtf({ month: "short", day: "numeric", year: "numeric" }).format(d) :
        MONTHS[d.getMonth()] + " " + d.getDate() + ", " + d.getFullYear();
    }

    /* A date with its weekday, and its year when withYear: "WED, OCT 21" or "WED, OCT 21, 2026" in English, "mer. 21
     * oct." in French. */
    function dayDate(value, withYear) {
      var d = at(value);
      if (!cldr) {
        return WEEKDAYS[(d.getDay() + 6) % 7] + ", " + MONTHS[d.getMonth()] + " " + d.getDate() + (withYear ? ", " + d.getFullYear() : "");
      }
      var options = { weekday: "short", month: "short", day: "numeric" };
      if (withYear) {
        options.year = "numeric";
      }
      return dtf(options).format(d);
    }

    /* A month of a year (month 0 is January), the year in two digits: "OCT 26" in English, "oct. 26" in French. */
    function monthYear(year, month) {
      return cldr ? dtf({ month: "short", year: "2-digit", timeZone: "UTC" }).format(Date.UTC(+year, month, 15)) :
        MONTHS[month] + " " + String(year).slice(2);
    }

    /* A weekday's short name, 0 for Monday: "MON" in English, "lun." in French, "lun" in Spanish, "Pzt" in Turkish. */
    function weekday(index) {
      return cldr ? dtf({ weekday: "short", timeZone: "UTC" }).format(MONDAY + index * DAY_MS) : WEEKDAYS[index];
    }

    /* A month's short name, 0 for January: "JAN" in English, "janv." in French, "ene" in Spanish, "Oca" in Turkish. The
     * name a date writes (CLDR's format form, as formats.py's month): Intl's month alone is the stand-alone form, which a
     * few tags write otherwise (es-CL "ene."). */
    function month(index) {
      if (!cldr) {
        return MONTHS[index];
      }
      var parts = dtf({ month: "short", day: "numeric", timeZone: "UTC" }).formatToParts(Date.UTC(2026, index, 15));
      for (var i = 0; i < parts.length; i++) {
        if (parts[i].type === "month") {
          return parts[i].value;
        }
      }
      return dtf({ month: "short", timeZone: "UTC" }).format(Date.UTC(2026, index, 15));
    }

    // ── Durations, lists and case ─────────────────────────────────────────────────────────────────────────────

    /* Time as formats.py's duration takes it, [[2, "d"], [3, "h"]], in CLDR's narrow units: "2j 3h" in French, "3s 27d"
     * in Turkish (3 hours 27 minutes; one hour is "1 sa"). French, Spanish and Turkish only: English keeps its callers'
     * own text ("2 D 3 H"), so it throws there, as formats.py's does. No parts give "". */
    function duration(parts) {
      if (!cldr) {
        throw new Error("MonitorFormats.duration serves French, Spanish and Turkish; English keeps its callers' own text");
      }
      if (!parts.length) {
        return "";
      }
      var i;
      if (DURATION_FORMAT) {
        // Each unit given is shown, a 0 too ("0s", as a unit through NumberFormat writes it); the others are not.
        var fields = {};
        var options = { style: "narrow" };
        for (i = 0; i < parts.length; i++) {
          fields[DURATION_FIELDS[parts[i][1]]] = parts[i][0];
          options[DURATION_FIELDS[parts[i][1]] + "Display"] = "always";
        }
        return intl(Intl.DurationFormat, "t", options).format(fields);
      }
      var words = [];
      for (i = 0; i < parts.length; i++) {
        words.push(nf({ style: "unit", unit: UNITS[parts[i][1]], unitDisplay: "narrow" }).format(parts[i][0]));
      }
      return intl(Intl.ListFormat, "l", { type: "unit", style: "narrow" }).format(words);
    }

    /* Words joined as a list: "X, Y and Z" in English (as the page wrote it: no Oxford comma), "X, Y et Z" in French,
     * "X, Y y Z" in Spanish, "X, Y ve Z" in Turkish. */
    function list(items) {
      if (!cldr) {
        return items.length < 2 ? items.join("") : items.slice(0, -1).join(", ") + " and " + items[items.length - 1];
      }
      var words = [];
      for (var i = 0; i < items.length; i++) {
        words.push(String(items[i]));
      }
      return intl(Intl.ListFormat, "l", { type: "conjunction" }).format(words);
    }

    /* Whether lang (a language or a tag; this locale's language when it is not given) writes Turkish's i. */
    function turkic(lang) {
      var code = lang === undefined || lang === null ? language : String(lang).split(/[-_]/)[0].toLowerCase();
      return own(TURKIC, code);
    }

    /* text in capitals by the rule of lang (this locale's language when it is not given): "HAİKU" for "Haiku" in
     * Turkish, "HAIKU" in English (casing.upper). */
    function upper(text, lang) {
      var s = String(text);
      return (turkic(lang) ? s.replace(/i/g, "\u0130") : s).toUpperCase();
    }

    /* text in lower case by the rule of lang: "haftalık" for "HAFTALIK" in Turkish, never an i with a combining dot
     * (casing.lower). */
    function lower(text, lang) {
      var s = String(text);
      if (turkic(lang)) {
        s = s.replace(/I\u0307/g, "i").replace(/\u0130/g, "i").replace(/I/g, "\u0131");
      }
      return s.toLowerCase();
    }

    var signs = nf({}).formatToParts(1234567.5);
    var group = ",";
    var decimal = ".";
    for (var i = 0; i < signs.length; i++) {
      if (signs[i].type === "group") {
        group = signs[i].value;
      } else if (signs[i].type === "decimal") {
        decimal = signs[i].value;
      }
    }
    // An amount's decimal sign, which CLDR sets apart from a number's for a few tags (fr-CH: "5.96 $", but "1 234,5").
    var currencyDecimal = decimal;
    var amount = nf({ style: "currency", currency: "USD", currencyDisplay: "narrowSymbol" }).formatToParts(1234567.5);
    for (i = 0; i < amount.length; i++) {
      if (amount[i].type === "decimal") {
        currencyDecimal = amount[i].value;
      }
    }

    return {
      language: language, tag: tag, english: !cldr, hours: hours, group: pseudo ? PSEUDO_GROUP : group, decimal: decimal,
      currencyDecimal: currencyDecimal,
      number: number, percent: percent, percentParts: percentParts, usd: usd, money: money, compact: compact,
      clock: clock, dayClock: dayClock, hour: hour, stamp: stamp, monthDay: monthDay, date: date, dayDate: dayDate,
      monthYear: monthYear, weekday: weekday, month: month, duration: duration, list: list, upper: upper, lower: lower
    };
  }

  /* The locale of this page, from the monitor-locale meta the server fills in; null when there is none to read. */
  function metaLocale() {
    try {
      var doc = root.document;
      var meta = doc && doc.querySelector ? doc.querySelector('meta[name="monitor-locale"]') : null;
      var text = meta ? meta.getAttribute("content") || "" : "";
      var parsed = text ? JSON.parse(text) : null;
      return parsed && typeof parsed === "object" && typeof parsed.language === "string" ? parsed : null;
    } catch (e) {
      return null;
    }
  }

  var active = null;
  root.MonitorFormats = {
    make: make,
    active: function () {
      if (active === null) {
        active = make(metaLocale());
      }
      return active;
    }
  };
}(window));
