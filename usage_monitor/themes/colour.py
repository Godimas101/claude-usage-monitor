"""Colour maths for the Gates system accent: contrast, CIE76 distance and the OKLCH lift.

Windows hands over its accent colour raw, and a raw accent can be anything: too dark for a dark
window, too light for a light one, or close to the colours that mean "ok", "warn", "alert" and
"stale". The theme loader uses these functions to turn it into one usable accent that the Pillow
skins and the dashboard CSS both read (usage_monitor/themes/__init__.py, "Gates accents").

Colours are "#rrggbb" strings (a trailing alpha pair is ignored). Everything here is a pure function
of its arguments: no state, no I/O, no Win32.

  contrast_ratio   WCAG 2 contrast of two colours (1 to 21).
  delta_e76        CIE76 colour difference in Lab (D65), the metric the approved Gates notes use.
  lift_accent      OKLCH lightness bound, then a contrast floor against the surfaces it is drawn on.
"""
from __future__ import annotations

import math
from collections.abc import Iterable

# OKLCH lightness bounds of the usable accent. They are the ones the Gates dashboard CSS applied to the
# raw accent before it was done here: a dark window lifts to at least 0.69, a light window caps at 0.60.
# The 12 designed accents already sit inside them and are returned unchanged.
LIGHTNESS_MIN_DARK = 0.69
LIGHTNESS_MAX_LIGHT = 0.60
# WCAG 2.1 floor for graphical objects (a bar, a ring, an icon) against what they sit on.
GRAPHIC_CONTRAST = 3.0
# CSS Color 4 gamut mapping: a clipped colour closer than this (OKLab distance) to the unclipped one is
# accepted; the search for the largest in-gamut chroma stops when its bracket is narrower than _EPSILON.
_JND = 0.02
_EPSILON = 1e-4
_LIFT_STEP = 0.01
_LIFT_STEPS = 80

Linear = tuple[float, float, float]


def parse_hex(value: str) -> tuple[int, int, int]:
    """The red, green and blue bytes of "#rrggbb" or "#rrggbbaa"."""
    return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)


def to_hex(rgb: Iterable[float]) -> str:
    """"#rrggbb" for channels in 0..255 (rounded and clamped)."""
    return "#" + "".join(f"{round(max(0.0, min(255.0, channel))):02x}" for channel in rgb)


def _decode(channel: float) -> float:
    """One sRGB channel in 0..1 to linear light."""
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def _encode(linear: float) -> float:
    """Linear light to one sRGB channel in 0..1 (the sign is kept for out-of-gamut values)."""
    if linear < 0:
        return -_encode(-linear)
    return 12.92 * linear if linear <= 0.0031308 else 1.055 * linear ** (1 / 2.4) - 0.055


def _linear(value: str) -> Linear:
    r, g, b = parse_hex(value)
    return _decode(r / 255), _decode(g / 255), _decode(b / 255)


def relative_luminance(value: str) -> float:
    """WCAG relative luminance, 0 (black) to 1 (white)."""
    r, g, b = _linear(value)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(first: str, second: str) -> float:
    """WCAG 2 contrast ratio of two colours, 1 to 21."""
    a, b = relative_luminance(first), relative_luminance(second)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def _lab(value: str) -> tuple[float, float, float]:
    r, g, b = _linear(value)
    x = 0.4124564 * r + 0.3575761 * g + 0.1804375 * b
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = 0.0193339 * r + 0.1191920 * g + 0.9503041 * b

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x / 0.95047), f(y), f(z / 1.08883)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e76(first: str, second: str) -> float:
    """CIE76 difference of two colours: the Euclidean distance in Lab (D65)."""
    return math.dist(_lab(first), _lab(second))


# ── OKLCH ─────────────────────────────────────────────────────────────────────

def _cbrt(value: float) -> float:
    return math.copysign(abs(value) ** (1 / 3), value)


def _linear_to_oklab(r: float, g: float, b: float) -> tuple[float, float, float]:
    l_ = _cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
    m_ = _cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    s_ = _cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    return (0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
            1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
            0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_)


def _oklab_to_linear(lightness: float, a: float, b: float) -> Linear:
    l_ = lightness + 0.3963377774 * a + 0.2158037573 * b
    m_ = lightness - 0.1055613458 * a - 0.0638541728 * b
    s_ = lightness - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    return (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
            -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
            -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)


def to_oklch(value: str) -> tuple[float, float, float]:
    """(lightness 0..1, chroma, hue in degrees) of a colour."""
    lightness, a, b = _linear_to_oklab(*_linear(value))
    return lightness, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360


def _oklch_to_oklab(lightness: float, chroma: float, hue: float) -> tuple[float, float, float]:
    radians = math.radians(hue)
    return lightness, chroma * math.cos(radians), chroma * math.sin(radians)


def _in_gamut(linear: Linear) -> bool:
    return all(-1e-6 <= channel <= 1 + 1e-6 for channel in linear)


def _clipped(linear: Linear) -> Linear:
    r, g, b = (max(0.0, min(1.0, channel)) for channel in linear)
    return r, g, b


def from_oklch(lightness: float, chroma: float, hue: float) -> str:
    """The sRGB colour for an OKLCH value. An out-of-gamut value keeps its lightness and hue and loses
    chroma until it fits (CSS Color 4, "binary search gamut mapping"), so a vivid colour is not shifted."""
    if lightness >= 1:
        return "#ffffff"
    if lightness <= 0:
        return "#000000"
    point = _oklch_to_oklab(lightness, chroma, hue)
    linear = _oklab_to_linear(*point)
    if not _in_gamut(linear):
        linear = _map_into_gamut(lightness, chroma, hue)
    return to_hex(255 * _encode(channel) for channel in linear)


def _map_into_gamut(lightness: float, chroma: float, hue: float) -> Linear:
    """Linear sRGB of the largest chroma at this lightness and hue that is, within one JND, in gamut."""
    low, high = 0.0, chroma
    best = _clipped(_oklab_to_linear(*_oklch_to_oklab(lightness, 0.0, hue)))
    while high - low > _EPSILON:
        middle = (low + high) / 2
        point = _oklch_to_oklab(lightness, middle, hue)
        linear = _oklab_to_linear(*point)
        clipped = _clipped(linear)
        if _in_gamut(linear) or math.dist(_linear_to_oklab(*clipped), point) < _JND:
            low, best = middle, clipped
        else:
            high = middle
    return best


# ── The usable accent ─────────────────────────────────────────────────────────

def lift_accent(accent: str, mode: str, surfaces: Iterable[str] = ()) -> str:
    """``accent`` as a mark on a dark or a light window: the colour to draw with.

    Hue and chroma are kept. Dark mode raises the OKLCH lightness to at least LIGHTNESS_MIN_DARK and
    light mode lowers it to at most LIGHTNESS_MAX_LIGHT; a colour inside the bound is returned as it
    came. Then, for any surface the mark sits on that it does not reach GRAPHIC_CONTRAST against, the
    lightness keeps moving the same way (up on dark, down on light) until it does, or until it runs out
    of lightness.
    """
    dark = mode != "light"
    lightness, chroma, hue = to_oklch(accent)
    bound = max(lightness, LIGHTNESS_MIN_DARK) if dark else min(lightness, LIGHTNESS_MAX_LIGHT)
    colour = accent[:7] if bound == lightness else from_oklch(bound, chroma, hue)
    backdrops = tuple(surface[:7] for surface in surfaces)
    step = _LIFT_STEP if dark else -_LIFT_STEP
    for _ in range(_LIFT_STEPS):
        if all(contrast_ratio(colour, backdrop) >= GRAPHIC_CONTRAST for backdrop in backdrops):
            break
        bound += step
        if not 0 < bound < 1:
            break
        colour = from_oklch(bound, chroma, hue)
    return colour.lower()


def min_separation(colour: str, others: Iterable[str]) -> float:
    """The smallest CIE76 distance from ``colour`` to any of ``others`` (infinity for none)."""
    return min((delta_e76(colour, other[:7]) for other in others), default=math.inf)
