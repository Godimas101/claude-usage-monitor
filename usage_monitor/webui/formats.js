/* formats.js: the dashboard's numbers, percents and money in the monitor's locale (#91), for every page script.
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
 * Intl as WebView2 86 has it (ui/dashboard.py WEBVIEW2_MIN_VERSION): NumberFormat with compact notation, narrowSymbol
 * and formatToParts.
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

  function own(object, key) {
    return Object.prototype.hasOwnProperty.call(object, key);
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
    var made = {};

    /* An Intl.NumberFormat of the tag, made once for each set of options. */
    function nf(options) {
      var key = JSON.stringify(options);
      if (!own(made, key)) {
        made[key] = new Intl.NumberFormat(tag, options);
      }
      return made[key];
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
     * is part of before. opts.tight drops the space beside the sign. */
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
     * French, Spanish and Turkish write it as their tag does, with the plain "$" for US dollars, and a code Intl refuses
     * where the tag puts a code, as it writes US dollars by their code ("5,96 EURO" in fr, "EURO 5.96" in es-MX). */
    function money(value, code) {
      var options = { style: "currency", currency: code };
      if (cldr && code === "USD") {
        options.currencyDisplay = "narrowSymbol";
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
     * "1,2 B" (a thousand) in Turkish, "1,2 mil" in Spanish. */
    function compact(value, scale) {
      var text = String(value);
      var m = DIGITS.exec(text);
      if (!cldr || !m) {
        return text + SCALES[scale];
      }
      var d = fraction(m);
      return nf({ notation: "compact", minimumFractionDigits: d, maximumFractionDigits: d })
        .format(Number(text + "e" + scale));
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

    return {
      language: language, tag: tag, english: !cldr, group: pseudo ? PSEUDO_GROUP : group, decimal: decimal,
      number: number, percent: percent, percentParts: percentParts, usd: usd, money: money, compact: compact
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
