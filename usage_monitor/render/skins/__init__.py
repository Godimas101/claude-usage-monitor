"""Skins: one renderer per theme. Each skin draws the four surfaces from a WidgetModel.

get_skin(theme_id) returns the skin for a theme. Themes without a skin of their own use the
Nostromo skin, so a new theme renders (plainly) before its own skin exists. The skin modules are
imported lazily, which keeps this package importable without Pillow drawing code loaded.

Every skin method takes the same arguments: the model, the theme, the variant (a Theme and a
Variant from usage_monitor.themes, or any objects with the same attributes), a scale or a size,
and opts. Colours come only from the variant's tokens.

Two more methods are optional. ui/widgets.py looks them up with getattr, so a skin (or a test double)
without them still works and behaves as if it answered "nothing":

    content_insets(surface, theme, model, scale) -> (left, top, right, bottom)
        The transparent pixels between the edge of a frame and the visible card in it (a frame with a soft
        shadow has them), at the frame's scale, for the surface "floating", "taskbar" or "tooltip". The widgets
        place the card, not the padded frame: the default corner, the clamp to the screen and the tooltip's
        gap all measure from it. model.NO_INSETS is the default.
    hover_frames(frame, surface, scale, opts) -> model.HoverPlay | None
        What the surface does when the pointer enters it, pre-rendered from the frame it is showing: a few
        frames and the time each stays up, and whether the last one is held while the pointer stays. None is
        no response. The widgets play it, and never under reduce_motion or Windows' animations-off switch.

Statuses with no room for words (the strip, the tray icon) are drawn by lamp kind: see model.LAMP_KINDS.
"""
from __future__ import annotations

import importlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from PIL import Image

from usage_monitor import themes as theme_registry
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


def face_donors(theme: Any, face_roles: Mapping[str, tuple[str, str]],
                faces: Mapping[str, Path]) -> dict[str, tuple[Any, ...]]:
    """The fallback faces (engine.Donor) of each face file a skin draws with, keyed by the file's path as a string,
    for Canvas.donors: the theme's fallback families for the font key's role and weight (themes.fallback_paths). A
    face whose role declares none is left out. ``face_roles`` maps a font key to (role, weight), ``faces`` maps it to
    its file. The engine is imported here, so importing this package loads no drawing code. The registry is the
    module-level one the skins use: a late ``from usage_monitor import themes`` can return another copy of it (the
    package attribute an earlier re-import left behind; see themes._platform)."""
    from usage_monitor.render import engine

    found: dict[str, tuple[Any, ...]] = {}
    for font_key, (role, weight) in face_roles.items():
        try:
            paths = theme_registry.fallback_paths(theme, role, weight)
        except (AttributeError, KeyError, TypeError):   # an object that is not a theme
            paths = ()
        if paths and font_key in faces:
            found[str(faces[font_key])] = tuple(engine.Donor(str(path)) for path in paths)
    return found


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


# theme id -> (module in this package, class). A theme whose module does not exist yet, and any
# unknown theme, renders through the Nostromo skin.
SKIN_CLASSES: dict[str, tuple[str, str]] = {
    "nostromo": ("nostromo", "NostromoSkin"),
    "night_city": ("night_city", "NightCitySkin"),
    "gates": ("gates", "GatesSkin"),
}


def _build(key: str) -> Skin:
    module_name, class_name = SKIN_CLASSES.get(key, SKIN_CLASSES["nostromo"])
    qualified = f"{__name__}.{module_name}"
    try:
        module = importlib.import_module(qualified)
    except ModuleNotFoundError as exc:
        if exc.name != qualified:  # the skin exists but one of its own imports is missing
            raise
        module_name, class_name = SKIN_CLASSES["nostromo"]
        module = importlib.import_module(f"{__name__}.{module_name}")
    return getattr(module, class_name)()
