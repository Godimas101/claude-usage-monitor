"""Skins: one renderer per theme. Each skin draws the four surfaces from a WidgetModel.

get_skin(theme_id) returns the skin for a theme. Themes without a skin of their own use the
Nostromo skin, so a new theme renders (plainly) before its own skin exists. The skin modules are
imported lazily, which keeps this package importable without Pillow drawing code loaded.

Every skin method takes the same arguments: the model, the theme, the variant (a Theme and a
Variant from usage_monitor.themes, or any objects with the same attributes), a scale or a size,
and opts. Colours come only from the variant's tokens.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from PIL import Image

from usage_monitor.render.model import WidgetModel


@dataclass(frozen=True, slots=True)
class RenderOptions:
    """Per-call switches. Defaults are the normal desktop look.

    transparent_bg: leave the widget's body plate out, so only the content and frame draw.
    text_fx: glow and scanlines on (False gives flat text and no CRT overlay).
    reduce_motion: the alive mark stays lit instead of blinking.
    phase: animation phase in 0..1; reserved for skins that animate beyond the blink.
    """
    transparent_bg: bool = False
    text_fx: bool = True
    reduce_motion: bool = False
    phase: float = 0.0


_OPTION_NAMES = ("transparent_bg", "text_fx", "reduce_motion", "phase")


def coerce_options(opts: RenderOptions | Mapping[str, Any] | None) -> RenderOptions:
    """Accept None, a RenderOptions, or a mapping with some of the option names."""
    if opts is None:
        return RenderOptions()
    if isinstance(opts, RenderOptions):
        return opts
    if isinstance(opts, Mapping):
        unknown = set(opts) - set(_OPTION_NAMES)
        if unknown:
            raise ValueError(f"unknown render option(s): {', '.join(sorted(unknown))}")
        return RenderOptions(**dict(opts))
    raise TypeError(f"opts must be RenderOptions, a mapping or None, not {type(opts).__name__}")


class Skin(Protocol):
    """What the host calls. Every method returns a PIL RGBA image with real alpha."""

    def floating_size(self, theme: Any, model: WidgetModel, scale: float) -> tuple[int, int]: ...

    def render_floating(self, model: WidgetModel, theme: Any, variant: Any, scale: float,
                        opts: RenderOptions | Mapping[str, Any] | None = None) -> Image.Image: ...

    def render_taskbar(self, model: WidgetModel, theme: Any, variant: Any, height_px: int, scale: float,
                       opts: RenderOptions | Mapping[str, Any] | None = None) -> Image.Image: ...

    def render_tooltip(self, model: WidgetModel, theme: Any, variant: Any, scale: float) -> Image.Image: ...

    def render_tray_icon(self, model: WidgetModel, theme: Any, variant: Any, size_px: int) -> Image.Image: ...


_skins: dict[str, Skin] = {}


def get_skin(theme_id: str | None) -> Skin:
    """The skin for ``theme_id``; any theme without its own skin gets the Nostromo skin."""
    key = (theme_id or "nostromo").strip().lower()
    if key not in _skins:
        _skins[key] = _build(key)
    return _skins[key]


def _build(key: str) -> Skin:
    # Only Nostromo has a skin so far. night_city and gates render through it until theirs land.
    from usage_monitor.render.skins.nostromo import NostromoSkin
    return NostromoSkin()
