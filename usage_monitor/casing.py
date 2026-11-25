"""Letter case by the language's rules (#91): Turkish's dotted and dotless i, and names that keep their own case.

Python's str.upper() and str.lower() follow Unicode's default rules, which Turkish (and Azerbaijani) do not: "i" is
"İ" in capitals and "I" is "ı" in lower case, and str.lower() would turn "İ" into "i" with a combining dot (U+0307).
The app's other languages use the default rules, so English, French and Spanish give exactly what str.upper() and
str.lower() give. Ids (limit kinds, keys, the guard's identity strings) are not text and keep the plain calls.

``lang`` is a language or a format tag ("tr", "tr-TR"). None takes the language an i18n.Text carries (catalog text
knows the catalog it came from); other text is English. An i18n.Text comes back as one, with its language.

Names (models, surfaces) in a sentence: in capitals a name is cased with the words, by the language's rule (Chris:
"Haiku" in Turkish capitals is "HAİKU"); in lower case or sentence case the words are cased and the name goes in as
written, except that a name starting a sentence gets its first letter in capitals ("cased").
"""
from __future__ import annotations

import re
from typing import Any

from usage_monitor.i18n import DEFAULT, Text, primary

TURKIC: frozenset[str] = frozenset({"tr", "az"})
CASES: tuple[str, ...] = ("upper", "lower", "sentence")
_DOTTED_CAPITAL_I = "\u0130"
_DOTLESS_I = "\u0131"
_COMBINING_DOT = "\u0307"
_NAMES = re.compile(r"(\{[A-Za-z_][A-Za-z0-9_]*\})")         # the catalog's {name} placeholders (i18n)


def language_of(text: Any, lang: str | None = None) -> str:
    """The language whose rules case ``text``: ``lang``'s primary subtag, else the language of an i18n.Text, else
    English."""
    if lang is None:
        return text.language if isinstance(text, Text) else DEFAULT
    return primary(lang) or str(lang).strip().lower()


def _same_kind(source: Any, result: str) -> str:
    return Text(result, source.language) if isinstance(source, Text) else result


def upper(text: str, lang: str | None = None) -> str:
    """``text`` in capitals: "HAİKU" for "Haiku" in Turkish, "HAIKU" in English."""
    source = str(text)
    if language_of(text, lang) in TURKIC:
        source = source.replace("i", _DOTTED_CAPITAL_I)
    return _same_kind(text, source.upper())


def lower(text: str, lang: str | None = None) -> str:
    """``text`` in lower case: "haftalık" for "HAFTALIK" in Turkish, and never an "i" with a combining dot."""
    source = str(text)
    if language_of(text, lang) in TURKIC:
        source = (source.replace("I" + _COMBINING_DOT, "i").replace(_DOTTED_CAPITAL_I, "i")
                  .replace("I", _DOTLESS_I))
    return _same_kind(text, source.lower())


def capitalize(text: str, lang: str | None = None) -> str:
    """The first character in capitals and the rest in lower case, by the language's rule: "Haftalık" for "HAFTALIK"
    in Turkish. As the alerts' titles do it (text[:1].upper() + text[1:].lower()), not str.capitalize(), which puts a
    digraph's first character in title case."""
    language = language_of(text, lang)
    source = str(text)
    return _same_kind(text, upper(source[:1], language) + lower(source[1:], language))


def cased(template: str, case: str, lang: str | None = None, /, **names: Any) -> str:
    """``template``'s words in ``case`` ("upper", "lower" or "sentence"), with each {name} put in: cased with the words
    in capitals, as written otherwise, its first letter in capitals when it starts a sentence. A placeholder ``names``
    lacks is left as written. In Turkish: ``cased("{name} weekly", "upper", "tr", name="Haiku")`` is "HAİKU WEEKLY",
    and ``cased("{name} WEEKLY", "sentence", "tr", name="Claude Instant")`` is "Claude Instant weekly"."""
    if case not in CASES:
        raise ValueError(f"case must be one of {CASES}, not {case!r}")
    language = language_of(template, lang)
    words = lower if case in ("lower", "sentence") else upper
    pieces: list[tuple[str, bool]] = []                     # (text, whether it is ours to case: not a stray {slot})
    for index, part in enumerate(_NAMES.split(str(template))):
        if index % 2 == 0:
            pieces.append((words(part, language), True))
        elif part[1:-1] in names:
            name = str(names[part[1:-1]])
            pieces.append((upper(name, language) if case == "upper" else name, True))
        else:
            pieces.append((part, False))
    if case == "sentence":
        for index, (piece, ours) in enumerate(pieces):
            if not ours:
                break                                       # the sentence starts with what fills the slot later
            at = next((i for i, ch in enumerate(piece) if ch.isalpha()), None)
            if at is not None:
                pieces[index] = (piece[:at] + upper(piece[at], language) + piece[at + 1:], ours)
                break
    return _same_kind(template, "".join(piece for piece, _ours in pieces))
