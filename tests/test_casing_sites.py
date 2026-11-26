"""Every bare letter-case call where the widgets', the tray's and the alerts' words come from is an id or English on
purpose (#91).

Text people read is cased with usage_monitor/casing.py, by the language's rules (Turkish's dotted i). A bare
str.upper(), lower(), capitalize(), title(), casefold() or swapcase() uses the default rules, which is right only for an
id (a limit kind, a key, the guard's identity strings, a tag or a currency code) or for English that is cased the way it
always was. The files the widgets', the tray's and the alerts' words come from may hold only the calls listed here, each
with its reason; a new one fails until it goes through casing or is listed. A listed call that goes away is struck, so
the list only shrinks. The skins' own calls move to casing in #94 to #96, and each adds its skin here: Nostromo (#94)
and Night City (#95) have none left.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CASE_METHODS = frozenset({"upper", "lower", "capitalize", "title", "casefold", "swapcase"})

# (file, enclosing function, the call as ast.unparse writes it): why the plain rules are right there.
ALLOWED: dict[tuple[str, str, str], str] = {
    ("usage_monitor/render/model.py", "severity_for", "str(api_severity).strip().lower()"):
        "id: the API's severity name, matched against a fixed set",
    ("usage_monitor/render/model.py", "format_age", "unit.upper()"):
        "English: the unit code in capitals, as English always wrote it (other languages go through casing)",
    ("usage_monitor/render/model.py", "format_countdown", "unit.upper()"):
        "English: the unit code in capitals, as English always wrote it (other languages go through casing)",
    ("usage_monitor/render/model.py", "format_age_short", "unit.upper()"):
        "English: the unit code in capitals, as Nostromo always wrote it (other languages go through casing)",
    ("usage_monitor/render/model.py", "_scoped_name", "(limit.scope_model or limit.label or '').strip().lower()"):
        "id: a scoped row's key",
    ("usage_monitor/render/model.py", "_guard_mark", "str(limit.label or 'scoped').lower()"):
        "id: the guard's label for a limit, as agent/guard.py names it",
    ("usage_monitor/render/model.py", "_over_key", "str(label or name or '').strip().lower()"):
        "id: the row key of a limit that is gone",
    ("usage_monitor/data/alerts.py", "_name", "text[:1].upper()"):
        "English: a title's name in sentence case, as English always wrote it (other languages go through casing)",
    ("usage_monitor/data/alerts.py", "_name", "text[1:].lower()"):
        "English: as above (\"Claude opus 5 weekly\" is English's own quirk, kept byte for byte)",
    ("usage_monitor/data/alerts.py", "_kind_words", "scope.upper()"):
        "English: the scope in capitals before _name lowers it, as the label always was",
    ("usage_monitor/data/alerts.py", "_kind_words", "limit.kind.replace('_', ' ').upper()"):
        "English: a kind the app has no words for, named from its code",
    ("usage_monitor/data/identity.py", "legacy_label", "name.upper()"):
        "id: Limit.label, the identity string alerts, guard episodes and usage.db key on",
    ("usage_monitor/data/identity.py", "legacy_label", "kind.replace('_', ' ').upper()"):
        "id: Limit.label for a kind with no words",
    ("usage_monitor/data/usage_api.py", "plan_from", "sub.replace('_', ' ').title()"):
        "data: an unknown plan's name from the API's code",
    ("usage_monitor/data/usage_api.py", "_legacy_window", "key[len('seven_day_'):].replace('_', ' ').title()"):
        "data: a scoped limit's model name from the API's key",
    ("usage_monitor/data/usage_api.py", "_lower", "text.lower()"):
        "id: the API's subscription type and tier",
    ("usage_monitor/data/usage_api.py", "_header", "name.lower()"):
        "id: an HTTP header name",
    ("usage_monitor/formats.py", "canonical_tag", "parts[0].lower()"):
        "id: a BCP 47 tag's language subtag",
    ("usage_monitor/formats.py", "canonical_tag", "part.upper()"):
        "id: a BCP 47 tag's region subtag",
    ("usage_monitor/formats.py", "canonical_tag", "part.title()"):
        "id: a BCP 47 tag's script subtag",
    ("usage_monitor/formats.py", "canonical_tag", "part.lower()"):
        "id: a BCP 47 tag's other subtags",
    ("usage_monitor/formats.py", "money", "(currency or '').strip().upper()"):
        "id: an ISO 4217 currency code",
}
SCANNED = ("usage_monitor/render/model.py", "usage_monitor/app.py", "usage_monitor/data/alerts.py",
           "usage_monitor/data/identity.py", "usage_monitor/data/usage_api.py", "usage_monitor/formats.py",
           "usage_monitor/render/skins/nostromo.py", "usage_monitor/render/skins/night_city.py")


class _Finder(ast.NodeVisitor):
    """Bare case calls, x.upper(), and the unbound methods, str.upper; casing.upper() is the rule itself."""

    def __init__(self) -> None:
        self.where: list[str] = []
        self.found: list[tuple[str, str]] = []

    def _scope(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        self.where.append(node.name)
        self.generic_visit(node)
        self.where.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scope

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if (isinstance(func, ast.Attribute) and func.attr in CASE_METHODS
                and not (isinstance(func.value, ast.Name) and func.value.id in ("casing", "str"))):
            self.found.append((".".join(self.where) or "<module>", ast.unparse(node)))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in CASE_METHODS and isinstance(node.value, ast.Name) and node.value.id == "str":
            self.found.append((".".join(self.where) or "<module>", ast.unparse(node)))
        self.generic_visit(node)


def case_calls(source: str) -> list[tuple[str, str]]:
    """(enclosing function, call) for each bare case call in ``source``."""
    finder = _Finder()
    finder.visit(ast.parse(source))
    return finder.found


def test_the_scan_finds_bare_calls_and_leaves_the_rule_and_data_alone():
    source = (
        "import casing\n"
        "def f(text, alert):\n"
        "    a = text.upper()\n"
        "    b = sorted(names, key=str.lower)\n"
        "    c = casing.upper(text, 'tr')\n"
        "    d = alert.title\n"
        "    return f'{text.strip().capitalize()}'\n"
        "class K:\n"
        "    def g(self, s):\n"
        "        return s.casefold() + s.title() + s.swapcase()\n"
    )
    assert case_calls(source) == [("f", "text.upper()"), ("f", "str.lower"), ("f", "text.strip().capitalize()"),
                                  ("K.g", "s.casefold()"), ("K.g", "s.title()"), ("K.g", "s.swapcase()")]


def test_every_bare_case_call_is_an_id_or_english_on_purpose():
    """A new bare call fails here: case text people read with casing.upper/lower/capitalize/cased, by the language's
    rules, or list an id with its reason. A listed call that is gone fails too, so it is struck from ALLOWED."""
    found = sorted((name, *call) for name in SCANNED
                   for call in case_calls((REPO / name).read_text(encoding="utf-8")))
    assert found == sorted(ALLOWED)
    assert all(name in SCANNED for name, _where, _call in ALLOWED)
