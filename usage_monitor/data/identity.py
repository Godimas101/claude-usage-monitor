"""The names a limit is keyed by, and the one English string that stays in them (#90).

``Limit.label`` keeps its English text, as "SESSION", "WEEKLY" or "FABLE WEEKLY". Alert identities, the guard's episodes,
hook-seen.json and the limits_json column of usage.db all key on that text, so changing it would repeat alerts and start new
guard episodes. Nothing reads it for words any more: titles and dashboard text are worded from ``kind`` and the scope.
"""
from __future__ import annotations


def scope_name(scope_model: str | None, scope_surface: str | None) -> str | None:
    """The scoped model's name, else the surface's: what a scoped limit is named for. None when it has neither."""
    return scope_model or scope_surface or None


def legacy_label(kind: str, scope_model: str | None, scope_surface: str | None) -> str:
    """The English label of a limit, as ``Limit.label`` has always held it: the identity string, not a sentence."""
    if kind == "session":
        return "SESSION"
    if kind == "weekly_all":
        return "WEEKLY"
    if kind == "weekly_scoped":
        name = scope_name(scope_model, scope_surface)
        return f"{name.upper()} WEEKLY" if name else "WEEKLY SCOPED"
    return kind.replace("_", " ").upper()
