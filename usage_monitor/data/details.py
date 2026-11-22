"""The status of a usage reading, as codes and values, and the one fixed English wording of them (#90).

The data layer carries a ``Detail``: a code and the values it needs ("updated" with its clock, "offline" with its reason).
``english()`` words it. That English is what state.json, the agent modes and the dashboard's status line carry today, so it
must not change. Nothing reads the English back as data, and the UI may word the code itself later.

No i18n import here: the agent modes (--hook, --status, ...) run this and must never load a catalog.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

LOADING = "loading"            # nothing fetched yet
PAUSED = "paused"              # the workstation is locked and no Claude session asked for the usage lately
UPDATED = "updated"            # a reading arrived; values: clock (local HH:MM:SS)
EXPIRED = "expired"            # the sign-in's token ran out; Claude Code renews it when it next sends a request
REFUSED = "refused"            # the usage API turned the sign-in down; values: http (401 or 403), advice (renew or login)
NO_SIGN_IN = "no_sign_in"      # no sign-in on this PC
RATE_LIMITED = "rate_limited"  # HTTP 429
OFFLINE = "offline"            # the usage API could not be reached; values: reason, and http for an HTTP reason
NEXT_TRY = "next_try"          # a value on rate_limited and offline details: the clock of the next request

# Claude Code renews the sign-in only when it sends a request, so an open but idle Claude Code lets it run out (#52).
EXPIRED_DETAIL = "The Claude sign-in has expired; send Claude Code a message to renew it"
# What fixes a refused sign-in. Claude Code's next request renews a token refused with 401, or asks for a sign-in when it
# can't. A 403 is a sign-in that works for Claude Code but can't read usage, which only a new sign-in fixes (#52).
ADVICE_TEXT = {"renew": "send Claude Code a message, and sign in again if it asks",
               "login": "sign in to Claude Code again with /login"}
REJECTED_ADVICE = {401: ADVICE_TEXT["renew"], 403: ADVICE_TEXT["login"]}
_ADVICE_CODE = {401: "renew", 403: "login"}
# Said to people who may never have used Claude Code (claude.ai and the Claude apps share its limits, #28), so it names
# no file and no token, and points at the dashboard, which has the steps.
NO_SIGN_IN_DETAIL = "No Claude sign-in on this PC; sign in to Claude Code once (the dashboard shows how)"
_REASONS = {"timed_out": "timed out", "network_error": "network error", "bad_response": "bad response",
            "unexpected_response": "unexpected response", "unexpected_error": "unexpected error"}
_BACKOFF = frozenset({RATE_LIMITED, OFFLINE})


@dataclass(frozen=True)
class Detail:
    """A status and its values. ``values`` holds only what the wording of ``code`` needs; ``next_try`` is added while
    the usage API is backing off."""
    code: str
    values: Mapping[str, Any] = field(default_factory=dict)

    def with_next_try(self, clock: str) -> "Detail":
        """This status, with the clock of the next request, for a status that backs off."""
        return Detail(self.code, {**self.values, NEXT_TRY: clock}) if self.code in _BACKOFF else self


def refused(http: int) -> Detail:
    """The usage API turned the sign-in down with ``http`` (401 or 403)."""
    return Detail(REFUSED, {"http": http, "advice": _ADVICE_CODE[http]})


def offline(reason: str, http: int | None = None) -> Detail:
    """The usage API could not be reached. ``reason`` is "http" (with ``http``) or one of the keys of _REASONS."""
    return Detail(OFFLINE, {"reason": reason, **({"http": http} if http is not None else {})})


def english(detail: Detail) -> str:
    """The English wording of ``detail``: the status text of state.json, --status and the dashboard today."""
    values, code = detail.values, detail.code
    if code == LOADING:
        text = "Loading usage"
    elif code == PAUSED:
        text = "Paused"
    elif code == UPDATED:
        text = f"Updated {values['clock']}"
    elif code == EXPIRED:
        text = EXPIRED_DETAIL
    elif code == REFUSED:
        text = f"Claude did not accept the sign-in (HTTP {values['http']}); {ADVICE_TEXT[values['advice']]}"
    elif code == NO_SIGN_IN:
        text = NO_SIGN_IN_DETAIL
    elif code == RATE_LIMITED:
        text = "Usage API rate limited"
    elif code == OFFLINE:
        text = f"Usage API unreachable ({_reason_text(values)})"
    else:
        text = code
    if values.get(NEXT_TRY):
        text += f"; next try at {values[NEXT_TRY]}"
    return text


def _reason_text(values: Mapping[str, Any]) -> str:
    reason = values.get("reason")
    if reason == "http":
        return f"HTTP {values.get('http')}"
    return _REASONS.get(str(reason), str(reason))
