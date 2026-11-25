"""The words of the view model, the tray tooltip and the tray menu come from the catalog (#93), and English stays what
it was.

tests/fixtures/widget_words_english.json holds today's English of all three, captured before #93 moved a word: the
model's texts in every sample and edge state, the tooltip of each (with and without the dev mark, the guard at every
countdown shape) and every menu. tests/widget_words.py works them out in a fresh Python with the time zone pinned, since
reset times are local.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

import english_left
import widget_words
from usage_monitor import i18n
from usage_monitor.agent import guard
from usage_monitor.data.identity import legacy_label
from usage_monitor.data.models import ApiStatus, Limit
from usage_monitor.render import dump
from usage_monitor.render import model as view
from usage_monitor.render.model import build_model

FIXTURE = Path(__file__).parent / "fixtures" / "widget_words_english.json"


@pytest.fixture(scope="module")
def recorded() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["words"]


# ── English stays what it was (step 0) ────────────────────────────────────────

def test_the_english_words_are_the_recorded_ones(recorded):
    """Every model text, tooltip and menu row, byte for byte, as they were before #93."""
    now = widget_words.collect()
    assert sorted(now) == sorted(recorded)
    changed = {key: (recorded[key], now[key]) for key in recorded if now[key] != recorded[key]}
    assert not changed, json.dumps(dict(list(changed.items())[:5]), ensure_ascii=False, indent=1)


def test_the_record_is_whole(recorded):
    """So the test above cannot pass on a thin record: every state, mode, countdown and menu is in it."""
    assert sum(key.startswith("menu/") for key in recorded) == 2 * 2 * 2 * len(widget_words.WAITING) * len(
        widget_words.UPDATES)
    assert any("(1000)" in row[0] for key, menu in recorded.items() if key.startswith("menu/") for row in menu)
    tips = [text for key, pair in recorded.items() if key.startswith("tooltip/") for text in pair]
    for shape in ("(in <1m)", "(in 9m)", "(in 2h 14m)", "(in 2d 3h)", "(in 6d 23h)"):
        assert any(shape in text for text in tips), shape
    assert any(text.endswith(" - guard holding") for text in tips)
    assert any(text.endswith(" (dev)") for text in tips)
    models = [value for key, value in recorded.items() if key.startswith("model/")]
    assert {model["status_text"] for model in models} == {"LOADING", "LIVE", "STALE", "OFFLINE", "RATE LIMITED",
                                                          "NO LOGIN", "PAUSED"}
    assert {model["stale_message"] for model in models} >= {"LOGIN EXPIRED - USE CLAUDE CODE",
                                                            "NO LOGIN FOUND - OPEN THE DASHBOARD",
                                                            "LOGIN REFUSED - RUN /login IN CLAUDE CODE"}


# ── The keys hold today's English (step 1) ────────────────────────────────────

# Each status code's key part, and a recorded state with that status.
STATUS_STATES = {"loading": "loading", "live": "normal3", "stale": "stale", "offline": "offline",
                 "rate_limited": "rate_limited", "no_login": "no_credentials", "paused": "paused"}
NO_READING = {"loading": "loading", "live": "live", "stale": "stale", "offline": "offline",
              "rate_limited": "rate_limited", "no_login": "no_credentials", "paused": "paused"}


def test_the_english_catalog_is_todays_english(recorded):
    """Each key of en.json, filled with the values English gives it, is the text English showed before #93, so a retyped
    value fails. The templates and their fills: the stale messages ({product} CLAUDE CODE, {command} /login), the scoped
    row ({name} FABLE) and guard label ({name} Fable), the share ({percent} 50%), the tooltip's limits ({percent}) and
    guard phrase ({clock}, {countdown}), the menu's plural ({count}) and update ({version}). Three keys have no one
    English text of their own: the scoped row and guard label with no name are the shape of identity.legacy_label's
    "WEEKLY SCOPED", which English shows as the label, and tray.tip.guard_until is guard_until_in without its countdown
    (English always has the countdown)."""
    en = i18n.load_catalog("en")
    text = en.text
    words = {key[len("model/"):]: value for key, value in recorded.items() if key.startswith("model/")}
    tips = {key[len("tooltip/"):]: value[0] for key, value in recorded.items() if key.startswith("tooltip/")}
    for code, state in STATUS_STATES.items():
        assert text(f"widget.status.{code}") == words[f"{state}/auto"]["status_text"], code
        assert tips[f"no_reading/{NO_READING[code]}"] == f"{text('tray.tip.lead')} - {text(f'tray.tip.status.{code}')}"
    assert text("widget.stale.expired", {"product": "CLAUDE CODE"}) == view.STALE_MESSAGE_EXPIRED == words[
        "stale/auto"]["stale_message"]
    assert text("widget.stale.no_login") == view.STALE_MESSAGE_NO_LOGIN == words["no_credentials/auto"]["stale_message"]
    assert text("widget.stale.refused", {"command": "/login", "product": "CLAUDE CODE"}) == view.STALE_MESSAGE_REFUSED \
        == words["refused"]["stale_message"]
    rows = {row[0]: row for row in words["normal3/auto"]["rows"]}
    assert (text("widget.limit.session"), text("widget.limit.weekly")) == (rows["session"][1], rows["weekly"][1])
    assert text("widget.limit.scoped", {"name": "FABLE"}) == rows["scoped:fable"][1] == "FABLE WEEKLY"
    assert text("widget.limit.scoped_unnamed") == legacy_label("weekly_scoped", None, None) == words[
        "scoped_neither"]["rows"][2][1]
    assert text("widget.share", {"percent": "50%"}) == rows["scoped:fable"][2] == "50% SHARE"
    labels = words["guard_over_a_limit_gone"]["guard"]["labels"]
    assert (text("widget.guard.session"), text("widget.guard.weekly")) == (labels["session"], labels["weekly"])
    assert text("widget.guard.scoped", {"name": "Fable"}) == labels["scoped:fable"] == "Fable weekly"
    assert text("widget.guard.scoped_unnamed") == words["scoped_neither/guard_holding"]["guard"]["labels"][
        "scoped:weekly scoped"] == "weekly scoped"
    lead, session, weekly = tips["normal3/auto"].split(" - ")
    assert (text("tray.tip.lead"), text("tray.tip.session", {"percent": "13%"}),
            text("tray.tip.weekly", {"percent": "2%"})) == (lead, session, weekly)
    assert tips["guard_armed/auto"].endswith(" - " + text("tray.tip.guard_armed"))
    assert tips["guard_hold_session/no_resume_time"].endswith(" - " + text("tray.tip.guard_holding"))
    held = words["guard_hold_session/auto"]["guard"]
    assert tips["guard_hold_session/auto"].endswith(" - " + text("tray.tip.guard_until_in", {
        "clock": held["resume_clock"], "countdown": "2h 14m"}))
    assert text("tray.tip.guard_until_in") == text("tray.tip.guard_until") + " (in {countdown})"
    menu = [row[0] for row in recorded["menu/floating=True taskbar=True guard=True waiting=2 update=2.1.0"]
            if row != ["-"]]
    keys = ("open_dashboard", "floating", "taskbar", "guard", None, "refresh", "report_bug", None, "exit")
    shown = [text(f"tray.menu.{key}") if key else None for key in keys]
    assert [label if key else None for key, label in zip(keys, menu)] == shown
    for count in (1, 2, 12, 1000):
        row = next(row[0] for row in recorded[f"menu/floating=True taskbar=True guard=True waiting={count} update=None"]
                   if row[0].startswith("Resume"))
        assert en.plural("tray.menu.resume_waiting", count) == row
    assert en.lookup("tray.menu.resume_waiting").value == {"one": "Resume waiting sessions ({count})",
                                                          "other": "Resume waiting sessions ({count})"}
    assert text("tray.menu.update", {"version": "2.1.0"}) == menu[7] == "Update to v2.1.0"
    english = en.english()
    assert {key for key in english if key.startswith(("widget.", "tray."))} == {
        *(f"widget.status.{code}" for code in STATUS_STATES), "widget.stale.expired", "widget.stale.no_login",
        "widget.stale.refused", "widget.limit.session", "widget.limit.weekly", "widget.limit.scoped",
        "widget.limit.scoped_unnamed", "widget.share", "widget.guard.session", "widget.guard.weekly",
        "widget.guard.scoped", "widget.guard.scoped_unnamed", "tray.tip.lead", "tray.tip.session", "tray.tip.weekly",
        *(f"tray.tip.status.{code}" for code in STATUS_STATES), "tray.tip.guard_armed", "tray.tip.guard_holding",
        "tray.tip.guard_until", "tray.tip.guard_until_in", *(f"tray.menu.{key}" for key in keys if key),
        "tray.menu.resume_waiting", "tray.menu.update"}


def test_each_note_sits_before_its_key():
    """A translator's note (``_note.<key>``, #93) names a key en.json has and sits just before it, so the budget note
    (``_budget.<key>``) can sit just after it."""
    raw = json.loads((i18n.CATALOG_DIR / "en.json").read_text(encoding="utf-8"))
    order = list(raw)
    notes = [key for key in order if key.startswith("_note.")]
    assert notes, "no notes, so nothing is checked"
    for note in notes:
        key = note[len("_note."):]
        assert key in raw and order.index(key) == order.index(note) + 1, note
        assert isinstance(raw[note], str) and raw[note].strip(), note


# ── The view model's words (step 2) ───────────────────────────────────────────

def catalog(language: str, own: dict[str, Any]) -> i18n.Catalog:
    """A catalog of ``language`` with ``own`` as its text and the shipped English behind it."""
    return i18n.Catalog(language, own, i18n.load_catalog("en").english())


@pytest.fixture
def catalogs(monkeypatch: pytest.MonkeyPatch) -> dict[str, i18n.Catalog]:
    """i18n.app_catalog patched: a language the test puts here gets that catalog, any other the shipped one."""
    given: dict[str, i18n.Catalog] = {}
    shipped = i18n.app_catalog
    monkeypatch.setattr(i18n, "app_catalog", lambda language: given.get(language) or shipped(language))
    return given


def loc(tag: str, clock: int = 24) -> i18n.Locale:
    return i18n.Locale(setting="system", language=i18n.primary(tag), system="en", format=tag, clock=clock,
                       offered=("en",))


FR, TR = loc("fr-FR"), loc("tr-TR")
PSEUDO = i18n.dev_locale(i18n.PSEUDO)
# Made-up words, longer than English's: only where they show matters.
FRENCH = {
    "widget.status.live": "EN DIRECT", "widget.status.stale": "PÉRIMÉ",
    "widget.stale.refused": "CONNEXION REFUSÉE - LANCEZ {command} DANS {product}",
    "widget.limit.session": "SESSION 5 H", "widget.limit.scoped": "{name} HEBDO",
    "widget.limit.scoped_unnamed": "HEBDO LIMITÉ", "widget.share": "PART {percent}",
    "widget.guard.session": "session de 5 h", "widget.guard.scoped": "{name} hebdo",
    "widget.guard.scoped_unnamed": "hebdo limité",
}


def scoped_state(scope_model: str | None, scope_surface: str | None = None, *, label: str = "ANOTHER WEEKLY",
                 kind: str = "weekly_scoped") -> Any:
    """A live reading with one scoped limit, at 60 %, held by the guard on it (a scoped cap of 50 %)."""
    now = dump.SAMPLE_NOW
    at = now + timedelta(days=3)

    def limit(kind: str, percent: float, label: str, **extra: Any) -> Limit:
        return Limit(kind=kind, group="session" if kind == "session" else "weekly", percent=percent, severity=None,
                     resets_at=at, is_active=True, label=label, **extra)

    state = dump._state(now, ApiStatus.LIVE, [
        limit("session", 10.0, "SESSION"), limit("weekly_all", 10.0, "WEEKLY"),
        limit(kind, 60.0, label, scope_model=scope_model, scope_surface=scope_surface, allowance_pct=25.0)], 12)
    verdict = guard.evaluate(guard.limits_doc(state.usage), {**dump.GUARD_SAMPLE_POLICY, "scoped_cap": 50}, now)
    return dataclasses.replace(state, guard=verdict)


def test_the_models_words_come_from_the_catalog(catalogs):
    """The status, the sign-in message, the row labels, the share and the guard's labels are the catalog's words in
    the model's language. A name goes in as a placeholder: in the language's capitals on a row (Turkish's İ), as
    written in a guard label. The command keeps its case."""
    catalogs["fr"] = catalog("fr", FRENCH)
    catalogs["tr"] = catalog("tr", {"widget.limit.scoped": "{name} HAFTALIK"})
    live = build_model(scoped_state("Fable"), dump.SAMPLE_NOW, show_scoped="always", locale=FR)
    assert (live.status_text, live.status_text.language) == ("EN DIRECT", "fr")
    assert [row.label for row in live.rows] == ["SESSION 5 H", "WEEKLY", "FABLE HEBDO"]
    assert live.rows[2].share_text == "PART 25 %"
    assert live.guard is not None and live.guard.labels == {"session": "session de 5 h", "weekly": "weekly",
                                                            "scoped:fable": "Fable hebdo"}
    refused = build_model(dataclasses.replace(dump.sample_state("stale"), signin_refused=403), dump.SAMPLE_NOW,
                          locale=FR)
    assert refused.stale_message == "CONNEXION REFUSÉE - LANCEZ /login DANS CLAUDE CODE"
    unnamed = build_model(scoped_state(None, label="WEEKLY SCOPED"), dump.SAMPLE_NOW, show_scoped="always", locale=FR)
    assert unnamed.rows[2].label == "HEBDO LIMITÉ" and unnamed.guard.labels["scoped:weekly scoped"] == "hebdo limité"
    surface = build_model(scoped_state(None, "Claude Code", label="CLAUDE CODE WEEKLY"), dump.SAMPLE_NOW,
                          show_scoped="always", locale=FR)
    assert surface.rows[2].label == "CLAUDE CODE HEBDO"
    assert surface.guard.labels["scoped:claude code weekly"] == "Claude Code hebdo"
    haiku = build_model(scoped_state("Haiku", label=""), dump.SAMPLE_NOW, show_scoped="always", locale=TR)
    assert (haiku.rows[2].label, haiku.rows[2].label.language) == ("HAİKU HAFTALIK", "tr")


def test_english_keeps_the_labels_that_are_data(catalogs):
    """English keeps a scoped row's label and guard label as the data gives them (the identity strings, #90), even
    where the template would read otherwise; another language words them. A kind with no words, and a limit gone from
    the reading, keep their own labels in every language."""
    catalogs["fr"] = catalog("fr", FRENCH)
    odd = scoped_state(None, "Claude Code", label="Claude Code weekly")
    english = build_model(odd, dump.SAMPLE_NOW, show_scoped="always")
    assert english.rows[2].label == "Claude Code weekly"
    assert english.guard.labels["scoped:claude code weekly"] == "claude code weekly"
    french = build_model(odd, dump.SAMPLE_NOW, show_scoped="always", locale=FR)
    assert french.rows[2].label == "CLAUDE CODE HEBDO"
    unknown = scoped_state(None, kind="design_weekly", label=legacy_label("design_weekly", None, None))
    for locale in (i18n.ENGLISH, FR, PSEUDO):
        model = build_model(unknown, dump.SAMPLE_NOW, show_scoped="always", locale=locale)
        assert model.rows[2].label == "DESIGN WEEKLY" and model.guard.labels["scoped:design weekly"] == "design weekly"
    held = dump.sample_state("guard_hold_session")
    gone = dataclasses.replace(held, guard={**held.guard, "over": [
        *held.guard["over"], {"name": "OPUS WEEKLY", "label": "Opus weekly", "percent": 99.0, "cap": 90}]})
    for locale in (i18n.ENGLISH, FR, PSEUDO):
        assert build_model(gone, dump.SAMPLE_NOW, locale=locale).guard.labels["scoped:opus"] == "Opus weekly"


def test_a_key_the_language_lacks_is_english_and_says_so(catalogs):
    catalogs["fr"] = catalog("fr", FRENCH)
    offline = build_model(dump.sample_state("offline"), dump.SAMPLE_NOW, locale=FR)
    assert (offline.status_text, offline.status_text.language) == ("OFFLINE", "en")
    assert (offline.rows[1].label, offline.rows[1].label.language) == ("WEEKLY", "en")
    expired = build_model(dump.sample_state("stale"), dump.SAMPLE_NOW, locale=FR)
    assert (expired.status_text.language, expired.stale_message, expired.stale_message.language) == (
        "fr", view.STALE_MESSAGE_EXPIRED, "en")


def model_words(model: Any) -> list[str]:
    """Every word field of a model: the status, the sign-in message, each row's label and share, the guard's labels.
    The other texts (age_text, resets_text, resume_text, resume_in, resume_clock) are #91's formats, which give the
    pseudo-locale English's shapes (D3 of #93's plan), not catalog words."""
    texts = [model.status_text, model.stale_message]
    texts += [text for row in model.rows for text in (row.label, row.share_text)]
    texts += list(model.guard.labels.values()) if model.guard is not None else []
    return [text for text in texts if text]


def test_no_model_word_is_english_in_the_pseudo_locale():
    """Every word field of every dump state in every show_scoped mode, and of the refused sign-in, is the
    pseudo-locale's. The names (the product, the brand marks, Claude Code and the sample models) and the commands are
    not words."""
    names = (english_left.PRODUCT, *english_left.WIDGET_MARKS, *english_left.skin_names(), *i18n.COMMANDS)
    texts = [text for name in dump.DUMP_STATE_NAMES for mode in view.SHOW_SCOPED_MODES
             for text in model_words(dump.sample_model(name, show_scoped=mode, locale=PSEUDO))]
    refused = dataclasses.replace(dump.sample_state("stale3"), signin_refused=403)
    texts += model_words(build_model(refused, dump.SAMPLE_NOW, locale=PSEUDO))
    assert any("/login" in text for text in texts) and any("Fable" in text for text in texts)
    assert english_left.english(texts, names) == set()
    assert english_left.english(model_words(dump.sample_model("guard_hold_weekly")), names)    # English is caught
