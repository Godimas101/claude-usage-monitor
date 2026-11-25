"""The names a limit is keyed by and titled with (#90).

Limit.label keeps its English identity string (identity.legacy_label): alerts, the guard's episodes, hook-seen.json and
usage.db key on it, so it must not change. The alert titles are worded from the kind and the scope, and come out as
they always did.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from usage_monitor.data import alerts, identity
from usage_monitor.data.models import Limit
from usage_monitor.i18n import ENGLISH
from usage_monitor.data.usage_api import _label

RESETS = datetime(2026, 11, 25, 16, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(("kind", "model", "surface", "label"), [
    ("session", None, None, "SESSION"),
    ("weekly_all", None, None, "WEEKLY"),
    ("weekly_scoped", "Fable", None, "FABLE WEEKLY"),
    ("weekly_scoped", None, None, "WEEKLY SCOPED"),
    ("weekly_scoped", None, "claude_code", "CLAUDE_CODE WEEKLY"),
    ("weekly_scoped", "", "x", "X WEEKLY"),
    ("monthly_all", None, None, "MONTHLY ALL"),
    ("amber_gauge", None, None, "AMBER GAUGE"),
    ("weekly_scoped", "Claude Opus 5", None, "CLAUDE OPUS 5 WEEKLY"),
    ("weekly_scoped", "Straße", None, "STRASSE WEEKLY"),    # str.upper() of ß is SS: the identity has always said so
])
def test_the_legacy_label_is_the_identity_string(kind, model, surface, label):
    assert identity.legacy_label(kind, model, surface) == label
    assert _label(kind, model, surface) == label            # usage_api builds Limit.label from it


def test_the_scope_is_the_model_else_the_surface_else_nothing():
    assert identity.scope_name("Fable", "claude_code") == "Fable"
    assert identity.scope_name(None, "claude_code") == "claude_code"
    assert identity.scope_name("", None) is None
    assert identity.scope_name(None, None) is None


def limit(kind: str, label: str, *, model: str | None = None, surface: str | None = None) -> Limit:
    return Limit(kind=kind, group=None, percent=85.0, severity="normal", resets_at=RESETS, is_active=True,
                 label=label, scope_model=model, scope_surface=surface)


@pytest.mark.parametrize(("item", "title"), [
    (limit("session", "SESSION"), "Session"),
    (limit("weekly_all", "WEEKLY"), "Weekly"),
    (limit("weekly_scoped", "FABLE WEEKLY", model="Fable"), "Fable weekly"),
    (limit("weekly_scoped", "CLAUDE OPUS 5 WEEKLY", model="Claude Opus 5"), "Claude opus 5 weekly"),
    (limit("weekly_scoped", "STRASSE WEEKLY", model="Straße"), "Strasse weekly"),
    (limit("weekly_scoped", "CLAUDE_CODE WEEKLY", surface="claude_code"), "Claude_code weekly"),
    (limit("weekly_scoped", "WEEKLY SCOPED"), "Weekly scoped"),
    (limit("monthly_all", "MONTHLY ALL"), "Monthly all"),
])
def test_a_title_is_worded_from_the_kind_and_scope(item, title):
    assert alerts._name(item, ENGLISH) == title


def test_a_title_does_not_read_its_label_when_it_has_one():
    """The words come from the kind and the model; whatever the label says, the title is the same."""
    assert alerts._name(limit("weekly_scoped", "WHATEVER", model="Fable"), ENGLISH) == "Fable weekly"
    assert alerts._name(limit("session", "anything at all"), ENGLISH) == "Session"


@pytest.mark.parametrize(("item", "title"), [
    (limit("weekly_scoped", "", model="Fable"), "Fable"),    # a limit with no label is named by its model
    (limit("session", ""), "Session"),
    (limit("weekly_all", "   "), "Weekly"),
])
def test_a_limit_with_no_label_falls_back_to_its_kind_then_its_model(item, title):
    assert alerts._name(item, ENGLISH) == title
