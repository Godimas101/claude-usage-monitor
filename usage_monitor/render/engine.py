"""Pillow toolkit shared by every skin.

Skins draw in logical pixels (the 1x design pixels). A Canvas maps them onto a buffer that is
two times the output size (more at fractional DPI), and finish() downsamples that buffer with
LANCZOS. Chamfers, text and glow come out smooth while 1 px rules stay crisp at 100 %.

Compositing is straight-alpha throughout. Translucent colours and anti-aliased text are always
stamped through a coverage mask with alpha_composite, never drawn straight onto the buffer,
because drawing them there leaves dark fringes wherever the background is transparent.
"""
from __future__ import annotations

import functools
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from usage_monitor import paths
from usage_monitor.log import get_logger

log = get_logger(__name__)

Color = tuple[int, int, int, int]
Point = tuple[float, float]

TRANSPARENT: Color = (0, 0, 0, 0)
SUPERSAMPLE = 2
_LANCZOS = Image.Resampling.LANCZOS

FONT_RELATIVE_ROOT = Path("webui") / "fonts"    # under usage_monitor/ (bundled OFL fonts)


# ── Colours ───────────────────────────────────────────────────────────────────

def parse_color(text: str) -> Color:
    """"#rgb", "#rrggbb" or "#rrggbbaa" to an (r, g, b, a) tuple."""
    value = text.strip()
    if not value.startswith("#"):
        raise ValueError(f"colour must start with '#': {text!r}")
    digits = value[1:]
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    if len(digits) not in (6, 8):
        raise ValueError(f"colour must be #rgb, #rrggbb or #rrggbbaa: {text!r}")
    try:
        channels = [int(digits[i:i + 2], 16) for i in range(0, len(digits), 2)]
    except ValueError as exc:
        raise ValueError(f"colour is not hexadecimal: {text!r}") from exc
    if len(channels) == 3:
        channels.append(255)
    return (channels[0], channels[1], channels[2], channels[3])


def with_alpha(color: Color, alpha: int) -> Color:
    return (color[0], color[1], color[2], max(0, min(255, alpha)))


def mix(a: Color, b: Color, t: float) -> Color:
    """Straight linear interpolation from a (t = 0) to b (t = 1)."""
    t = max(0.0, min(1.0, t))
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))  # type: ignore[return-value]


# ── Fonts ─────────────────────────────────────────────────────────────────────

@functools.lru_cache(maxsize=1)
def bundled_font_root() -> Path:
    """usage_monitor/webui/fonts, from the source tree or the frozen bundle."""
    frozen = paths.resource_dir() / "usage_monitor" / FONT_RELATIVE_ROOT
    if frozen.is_dir():
        return frozen
    return Path(__file__).resolve().parents[1] / FONT_RELATIVE_ROOT


@functools.lru_cache(maxsize=128)
def load_font(path: str, size: float) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """A TrueType face at ``size`` device pixels. Missing files fall back to Pillow's default."""
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        log.warning("font file missing or unreadable: %s (using Pillow's default)", path)
        return ImageFont.load_default(size=size)


def font_file(relative: str) -> Path:
    """A bundled font, e.g. "ibmplexmono/IBMPlexMono-SemiBold.ttf"."""
    return bundled_font_root() / relative


def _build_run(font, text: str, tracking_px: float) -> tuple[Image.Image, int, int, float]:
    """One run of glyphs with its baseline at a fixed row: (mask, pad, ascent, width_px).

    Advances are the font's own plus tracking after every glyph, the CSS letter-spacing model.
    The mask is drawn from an integer origin, so the run can be pasted at any integer position
    without being rendered again.
    """
    ascent, descent = font.getmetrics()
    advances = [font.getlength(ch) + tracking_px for ch in text]
    width_px = sum(advances)
    pad = int(font.size) // 2 + 2
    mask = Image.new("L", (math.ceil(width_px) + 2 * pad, ascent + descent + 2 * pad), 0)
    draw = ImageDraw.Draw(mask)
    pen = float(pad)
    for ch, advance in zip(text, advances):
        draw.text((pen, pad + ascent), ch, font=font, fill=255, anchor="ls")
        pen += advance
    return mask.point(_GLYPH_COVERAGE), pad, ascent, width_px


# Browsers draw text heavier than FreeType's linear coverage (gamma and contrast boost on
# Windows), so glyph coverage is lifted to match the approved screenshots.
GLYPH_GAMMA = 0.6
_GLYPH_COVERAGE = [round(255 * (v / 255) ** GLYPH_GAMMA) for v in range(256)]

@functools.lru_cache(maxsize=2048)
def _cached_run(path: str, size: float, text: str, tracking_px: float) -> tuple[Image.Image, int, int, float]:
    return _build_run(load_font(path, size), text, tracking_px)


def _run(font, text: str, tracking_px: float) -> tuple[Image.Image, int, int, float]:
    """The cached glyph run for a font, or a fresh one for a font without a file path."""
    path = getattr(font, "path", None)
    size = getattr(font, "size", None)
    if isinstance(path, str) and isinstance(size, (int, float)):
        return _cached_run(path, float(size), text, tracking_px)
    return _build_run(font, text, tracking_px)


# ── Byte helpers for the host ─────────────────────────────────────────────────

def to_bgra_straight(image: Image.Image) -> bytes:
    """BGRA bytes with straight alpha, top row first."""
    r, g, b, a = image.convert("RGBA").split()
    return Image.merge("RGBA", (b, g, r, a)).tobytes()


def to_bgra_premultiplied(image: Image.Image) -> bytes:
    """BGRA bytes with premultiplied colour, as UpdateLayeredWindow expects (ULW_ALPHA)."""
    r, g, b, a = image.convert("RGBA").split()
    r = ImageChops.multiply(r, a)
    g = ImageChops.multiply(g, a)
    b = ImageChops.multiply(b, a)
    return Image.merge("RGBA", (b, g, r, a)).tobytes()


# ── Masks ─────────────────────────────────────────────────────────────────────

@dataclass(slots=True)
class Layer:
    """A coverage mask (mode L) placed at buffer position (x, y)."""
    mask: Image.Image
    x: int
    y: int


@functools.lru_cache(maxsize=256)
def _alpha_table(alpha: int) -> list[int]:
    return [(value * alpha + 127) // 255 for value in range(256)]


def _scaled(mask: Image.Image, alpha: int) -> Image.Image:
    if alpha >= 255:
        return mask
    return mask.point(_alpha_table(max(0, alpha)))


# ── Canvas ────────────────────────────────────────────────────────────────────

class Canvas:
    """A logical-pixel drawing surface that renders at ``scale * SUPERSAMPLE`` and downsamples.

    width and height are logical (design) pixels. The output is round(width * scale) by
    round(height * scale). Every method takes logical coordinates, which may be fractional.
    """

    def __init__(self, width: float, height: float, scale: float = 1.0, ss: int = SUPERSAMPLE):
        if width <= 0 or height <= 0:
            raise ValueError("canvas size must be positive")
        if scale <= 0:
            raise ValueError("scale must be positive")
        self.width = float(width)
        self.height = float(height)
        self.scale = float(scale)
        self.out_w = max(1, round(self.width * self.scale))
        self.out_h = max(1, round(self.height * self.scale))
        self.ss = ss
        self.buf_w = self.out_w * ss
        self.buf_h = self.out_h * ss
        self.kx = self.buf_w / self.width
        self.ky = self.buf_h / self.height
        self.k = self.kx                    # font and blur scale (kx and ky differ by under 0.5 %)
        self.image = Image.new("RGBA", (self.buf_w, self.buf_h), TRANSPARENT)
        self.draw = ImageDraw.Draw(self.image)
        # Optional map from a font key to the face file a theme chose for it (see skins).
        self.faces: dict[str, Path] | None = None

    # ── Coordinates ──

    def px(self, x: float) -> float:
        return x * self.kx

    def py(self, y: float) -> float:
        return y * self.ky

    def _rect_px(self, x0: float, y0: float, x1: float, y1: float) -> tuple[int, int, int, int]:
        return (round(x0 * self.kx), round(y0 * self.ky), round(x1 * self.kx), round(y1 * self.ky))

    def _points_px(self, points: Sequence[Point]) -> list[Point]:
        """Buffer coordinates for a polygon whose far edges are exclusive, like the rectangle
        helpers. Pillow's polygon fill includes the far pixel row and column, so vertices on the
        far x or y are pulled back half a pixel."""
        if not points:
            return []
        far_x = max(x for x, _ in points)
        far_y = max(y for _, y in points)
        return [
            (x * self.kx - (0.5 if x == far_x else 0.0), y * self.ky - (0.5 if y == far_y else 0.0))
            for x, y in points
        ]

    # ── Font helpers ──

    def font(self, key: str, size: float) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        """The face for ``key`` at ``size`` logical px, scaled to the buffer.

        ``key`` is looked up in ``faces`` first, so a theme can supply its own files. Otherwise it
        is a path under usage_monitor/webui/fonts, e.g. "vt323/VT323-Regular.ttf".
        """
        path = self.faces[key] if self.faces and key in self.faces else font_file(key)
        return load_font(str(path), round(size * self.k, 3))

    def advance(self, text: str, font, tracking: float = 0.0) -> float:
        """Logical width of ``text`` with letter-spacing ``tracking`` after every glyph (CSS style)."""
        return _run(font, text, tracking * self.k)[3] / self.k

    def baseline_in_box(self, top: float, line_h: float, font) -> float:
        """Baseline (logical) of text centred in a CSS-style line box of ``line_h`` at ``top``."""
        ascent, descent = font.getmetrics()
        return top + (line_h * self.k - (ascent + descent)) / 2 / self.k + ascent / self.k

    # ── Layers ──

    def rect_layer(self, x0: float, y0: float, x1: float, y1: float) -> Layer:
        left, top, right, bottom = self._rect_px(x0, y0, x1, y1)
        mask = Image.new("L", (max(1, right - left), max(1, bottom - top)), 255)
        return Layer(mask, left, top)

    def rects_layer(self, rects: Sequence[tuple[float, float, float, float]]) -> Layer | None:
        """One mask covering several rectangles (x0, y0, x1, y1), or None when there are none."""
        if not rects:
            return None
        boxes = [self._rect_px(*rect) for rect in rects]
        left = min(b[0] for b in boxes)
        top = min(b[1] for b in boxes)
        right = max(b[2] for b in boxes)
        bottom = max(b[3] for b in boxes)
        mask = Image.new("L", (max(1, right - left), max(1, bottom - top)), 0)
        draw = ImageDraw.Draw(mask)
        for b_left, b_top, b_right, b_bottom in boxes:
            if b_right > b_left and b_bottom > b_top:
                draw.rectangle([b_left - left, b_top - top, b_right - left - 1, b_bottom - top - 1], fill=255)
        return Layer(mask, left, top)

    def rounded_rect_layer(self, x0: float, y0: float, x1: float, y1: float, radius: float) -> Layer:
        """Rectangle with circular corners of ``radius`` logical px (0 gives a plain rectangle)."""
        if radius <= 0:
            return self.rect_layer(x0, y0, x1, y1)
        left, top, right, bottom = self._rect_px(x0, y0, x1, y1)
        r = radius * self.k
        mask = Image.new("L", (max(1, right - left), max(1, bottom - top)), 0)
        draw = ImageDraw.Draw(mask)
        width, height = right - left - 1, bottom - top - 1
        draw.rounded_rectangle([0, 0, width, height], radius=r, fill=255)
        return Layer(mask, left, top)

    def segment_layers(
        self,
        x: float,
        y: float,
        step: float,
        cell_w: float,
        cell_h: float,
        cells: int,
        full: int,
        partial_w: float = 0.0,
    ) -> tuple[Layer | None, Layer | None]:
        """Masks for a segmented bar: (unlit cells, lit cells). ``full`` cells are lit, and the cell
        after them is lit for ``partial_w`` logical px from its left edge."""
        ghosts = [(x + step * i, y, x + step * i + cell_w, y + cell_h) for i in range(full, cells)]
        lit = [(x + step * i, y, x + step * i + cell_w, y + cell_h) for i in range(full)]
        if full < cells and partial_w > 0:
            start = x + step * full
            lit.append((start, y, start + partial_w, y + cell_h))
        return self.rects_layer(ghosts), self.rects_layer(lit)

    def polygon_layer(self, points: Sequence[Point]) -> Layer:
        pts = self._points_px(points)
        left, top, right, bottom = _bounds(pts)
        mask = Image.new("L", (max(1, right - left + 1), max(1, bottom - top + 1)), 0)
        ImageDraw.Draw(mask).polygon([(x - left, y - top) for x, y in pts], fill=255)
        return Layer(mask, left, top)

    def ellipse_layer(self, x0: float, y0: float, x1: float, y1: float, ring: float = 0.0) -> Layer:
        left, top, right, bottom = self._rect_px(x0, y0, x1, y1)
        mask = Image.new("L", (max(1, right - left), max(1, bottom - top)), 0)
        draw = ImageDraw.Draw(mask)
        draw.ellipse([0, 0, right - left - 1, bottom - top - 1], fill=255)
        if ring > 0:
            inset = round(ring * self.kx)
            draw.ellipse([inset, inset, right - left - 1 - inset, bottom - top - 1 - inset], fill=0)
        return Layer(mask, left, top)

    def text_layer(
        self,
        text: str,
        x: float,
        baseline: float,
        font,
        tracking: float = 0.0,
        align: str = "left",
    ) -> tuple[Layer, float]:
        """Coverage mask for one run of text. Returns (layer, advance in logical px).

        align "right" puts the run's right edge (including its trailing tracking, as CSS does)
        at x; "centre" centres the run on x. Positions snap to whole buffer pixels, under a
        quarter of a logical pixel at every supported scale.
        """
        mask, pad, ascent, width_px = _run(font, text, tracking * self.k)
        bx = x * self.kx
        if align == "right":
            start_px = bx - width_px
        elif align == "centre":
            start_px = bx - width_px / 2
        else:
            start_px = bx
        origin_x = round(start_px) - pad
        origin_y = round(baseline * self.ky) - ascent - pad
        return Layer(mask, origin_x, origin_y), width_px / self.k

    # ── Painting ──

    def stamp(self, layer: Layer, color: Color) -> None:
        """Composite ``color`` through the layer's coverage (straight alpha)."""
        self._composite(layer.mask, layer.x, layer.y, color)

    def glow(self, layer: Layer, sigma: float, color: Color, strength: float = 1.0) -> None:
        """Gaussian halo of ``layer`` in ``color``. Call before the crisp content it sits under.

        sigma is in logical px (CSS blur radius / 2). strength scales the halo's alpha.
        """
        if sigma <= 0 or strength <= 0 or color[3] == 0:
            return
        sigma_px = sigma * self.k
        pad = math.ceil(3 * sigma_px) + 1
        halo = Image.new("L", (layer.mask.width + 2 * pad, layer.mask.height + 2 * pad), 0)
        halo.paste(layer.mask, (pad, pad))
        halo = halo.filter(ImageFilter.GaussianBlur(sigma_px))
        halo = halo.point(_alpha_table(round(255 * min(1.0, strength))))
        self._composite(halo, layer.x - pad, layer.y - pad, color)

    def fill_rect(self, x0: float, y0: float, x1: float, y1: float, color: Color) -> None:
        if color[3] == 0:
            return
        left, top, right, bottom = self._rect_px(x0, y0, x1, y1)
        if right <= left or bottom <= top:
            return
        if color[3] == 255:
            self.draw.rectangle([left, top, right - 1, bottom - 1], fill=color)
        else:
            self.stamp(self.rect_layer(x0, y0, x1, y1), color)

    def fill_ring(self, outer: Sequence[Point], inner: Sequence[Point], color: Color) -> None:
        """Fill the area inside the ``outer`` polygon but outside ``inner``: a border drawn inside a shape."""
        ring = ImageChops.subtract(self._full_mask(outer), self._full_mask(inner))
        self._composite(ring, 0, 0, color)

    def _full_mask(self, points: Sequence[Point]) -> Image.Image:
        mask = Image.new("L", (self.buf_w, self.buf_h), 0)
        ImageDraw.Draw(mask).polygon(self._points_px(points), fill=255)
        return mask

    def fill_rounded_rect(self, x0: float, y0: float, x1: float, y1: float, radius: float, color: Color) -> None:
        self.stamp(self.rounded_rect_layer(x0, y0, x1, y1, radius), color)

    def fill_polygon(self, points: Sequence[Point], color: Color) -> None:
        if color[3] == 0:
            return
        if color[3] == 255:
            self.draw.polygon(self._points_px(points), fill=color)
        else:
            self.stamp(self.polygon_layer(points), color)

    def fill_ellipse(self, x0: float, y0: float, x1: float, y1: float, color: Color) -> None:
        self.stamp(self.ellipse_layer(x0, y0, x1, y1), color)

    def outline_rect(self, x0: float, y0: float, x1: float, y1: float, color: Color, width: float = 1) -> None:
        """Border drawn inside the rectangle (CSS border-box behaviour)."""
        self.fill_rect(x0, y0, x1, y0 + width, color)
        self.fill_rect(x0, y1 - width, x1, y1, color)
        self.fill_rect(x0, y0, x0 + width, y1, color)
        self.fill_rect(x1 - width, y0, x1, y1, color)

    def dotted_hline(self, x0: float, x1: float, y: float, on: float, off: float, color: Color) -> None:
        """Repeating dash pattern starting at x0: ``on`` px lit, ``off`` px dark."""
        segments = []
        x = x0
        while x < x1:
            segments.append((x, y, min(x + on, x1), y + 1))
            x += on + off
        layer = self.rects_layer(segments)
        if layer is not None:
            self.stamp(layer, color)

    def text(self, text: str, x: float, baseline: float, font, color: Color,
             tracking: float = 0.0, align: str = "left") -> float:
        """Draw text and return its logical advance."""
        layer, advance = self.text_layer(text, x, baseline, font, tracking, align)
        self.stamp(layer, color)
        return advance

    def scanlines(self, y0: float, y1: float, strength: float, period: float = 3.0, phase: float = 2.0,
                  clip: Sequence[Point] | None = None) -> None:
        """Darken every ``period``-th logical row, rows y0 + phase, y0 + phase + period, ..., by
        ``strength`` (0.3 is 30 % black). Only the colour channels are multiplied, so transparent
        pixels stay transparent. ``clip`` limits the darkening to a polygon (the body, so the
        frame's own edge is not touched). Rows are whole logical rows, so each darkens crisply."""
        if strength <= 0:
            return
        if clip is not None:
            left, top, right, bottom = _bounds(self._points_px(clip))
        else:
            left, top, right, bottom = 0, 0, self.buf_w, self.buf_h
        left, top = max(0, left), max(0, top)
        right, bottom = min(self.buf_w, right), min(self.buf_h, bottom)
        if right <= left or bottom <= top:
            return
        shade = round(255 * (1.0 - min(1.0, strength)))
        dark = Image.new("L", (right - left, bottom - top), 0)
        dark_draw = ImageDraw.Draw(dark)
        row = y0 + phase
        while row < y1:
            _, r_top, _, r_bottom = self._rect_px(0.0, row, self.width, row + 1)
            dark_draw.rectangle([0, r_top - top, right - left - 1, r_bottom - top - 1], fill=255)
            row += period
        if clip is not None:
            inside = Image.new("L", dark.size, 0)
            ImageDraw.Draw(inside).polygon([(x - left, y - top) for x, y in self._points_px(clip)], fill=255)
            dark = ImageChops.multiply(dark, inside)
        factor = Image.new("RGBA", dark.size, (255, 255, 255, 255))
        factor.paste((shade, shade, shade, 255), (0, 0), dark)
        region = ImageChops.multiply(self.image.crop((left, top, right, bottom)), factor)
        self.image.paste(region, (left, top))

    def gradient_fill(self, points: Sequence[Point], top_color: Color, bottom_color: Color,
                      stop: float = 1.0, y_top: float | None = None, y_bottom: float | None = None) -> None:
        """Vertical gradient inside a polygon: top_color at y_top, reaching bottom_color at
        y_top + (y_bottom - y_top) * stop, then flat. Both y values are logical."""
        ys = [y for _, y in points]
        y_top = min(ys) if y_top is None else y_top
        y_bottom = max(ys) if y_bottom is None else y_bottom
        span = max(1e-6, (y_bottom - y_top) * stop)
        left, top, right, bottom = _bounds(self._points_px(points))
        width = max(1, right - left + 1)
        height = max(1, bottom - top + 1)
        column = Image.new("RGBA", (1, height), top_color)
        pixels = column.load()
        for row in range(height):
            y_logical = (top + row) / self.ky
            pixels[0, row] = mix(top_color, bottom_color, (y_logical - y_top) / span)
        grad = column.resize((width, height), Image.Resampling.NEAREST)
        mask = Image.new("L", (width, height), 0)
        ImageDraw.Draw(mask).polygon([(x - left, y - top) for x, y in self._points_px(points)], fill=255)
        grad.putalpha(mask)
        self.image.alpha_composite(grad, (left, top))

    def hazard(self, points: Sequence[Point], x0: float, x1: float, y0: float, y1: float,
               period: float, bright: Color, dark: Color) -> None:
        """Diagonal hazard stripes running "/" (CSS -45 degrees), clipped to the polygon ``points``.

        Each stripe is ``period / 2`` wide across its direction, and the pattern starts at the
        region's top-left corner (x0, y0). Stripes are bright, the gaps are ``dark``.
        """
        left, top, right, bottom = self._rect_px(x0, y0, x1, y1)
        width, height = max(1, right - left), max(1, bottom - top)
        band = Image.new("RGBA", (width, height), dark)
        draw = ImageDraw.Draw(band)
        step = period * math.sqrt(2) * self.k          # one period measured along x + y, buffer px
        index = 0
        while index * step < width + height:
            stripe = _clip_band(width, height, index * step, index * step + step / 2)
            if stripe:
                draw.polygon(stripe, fill=bright)
            index += 1
        mask = Image.new("L", (width, height), 0)
        ImageDraw.Draw(mask).polygon([(x - left, y - top) for x, y in self._points_px(points)], fill=255)
        band.putalpha(mask)
        self.image.alpha_composite(band, (left, top))

    def vignette(self, strength: float) -> None:
        """Darken towards the corners. strength 0 is a no-op; 1 takes the corners to black."""
        if strength <= 0:
            return
        radial = Image.radial_gradient("L").resize(self.image.size, _LANCZOS)
        amount = min(1.0, strength)
        table = [round(255 * (1.0 - amount * max(0.0, (v - 140) / 115) ** 2)) for v in range(256)]
        shade = radial.point(table)
        factor = Image.merge("RGBA", (shade, shade, shade, Image.new("L", self.image.size, 255)))
        self.image = ImageChops.multiply(self.image, factor)
        self.draw = ImageDraw.Draw(self.image)

    def finish(self, method: str = "box") -> Image.Image:
        """The downsampled frame at round(width * scale) by round(height * scale).

        "box" (the default) averages each 2 x 2 block: the exact area sample of the supersampled
        buffer. It keeps 1 px rules and text at their true colour, where LANCZOS overshoots the
        accent colour by about 10 % on hard edges. "lanczos" is kept for comparison.
        """
        if self.image.size == (self.out_w, self.out_h):
            return self.image
        if method == "lanczos":
            return self.image.resize((self.out_w, self.out_h), _LANCZOS)
        if method != "box":
            raise ValueError(f"unknown downsample method {method!r}")
        return self.image.reduce(self.ss)

    # ── Internals ──

    def _composite(self, mask: Image.Image, x: int, y: int, color: Color) -> None:
        if color[3] == 0:
            return
        mask = _scaled(mask, color[3])
        buf_w, buf_h = self.image.size
        left, top = max(0, x), max(0, y)
        right, bottom = min(buf_w, x + mask.width), min(buf_h, y + mask.height)
        if right <= left or bottom <= top:
            return
        if (left, top, right, bottom) != (x, y, x + mask.width, y + mask.height):
            mask = mask.crop((left - x, top - y, right - x, bottom - y))
        layer = Image.new("RGBA", mask.size, (color[0], color[1], color[2], 0))
        layer.putalpha(mask)
        self.image.alpha_composite(layer, (left, top))


# ── Geometry helpers ──────────────────────────────────────────────────────────

def chamfer_polygon(x0: float, y0: float, x1: float, y1: float, tl: float, br: float) -> list[Point]:
    """Rectangle with chamfered top-left and bottom-right corners (CSS clip-path order)."""
    return [
        (x0 + tl, y0), (x1, y0), (x1, y1 - br), (x1 - br, y1), (x0, y1), (x0, y0 + tl),
    ]


def inset_chamfer(x0: float, y0: float, x1: float, y1: float, tl: float, br: float, inset: float) -> list[Point]:
    """The chamfered rectangle shrunk by ``inset`` on every side.

    A 45-degree chamfer moves outward by inset * (sqrt(2) - 1) in absolute terms when the box
    is inset, so the relative chamfer shrinks by inset * (2 - sqrt(2)). For a 10 px chamfer
    inset by 1 px this gives 9.41, the CSS value used by the approved mockup.
    """
    shrink = inset * (2 - math.sqrt(2))
    return chamfer_polygon(x0 + inset, y0 + inset, x1 - inset, y1 - inset,
                           max(0.0, tl - shrink), max(0.0, br - shrink))


NO_BREAK = "\u00a0"            # a space wrap_words never breaks a line at (and gives back as a plain one)


def glue(text: str) -> str:
    """``text`` with its spaces made unbreakable, so a time, a cap or a countdown stays on one line if a line can hold it."""
    return text.replace(" ", NO_BREAK)


def wrap_words(text: str, width_of: Callable[[str], float], limit: float, first_limit: float | None = None) -> list[str]:
    """``text`` broken at its spaces into lines no wider than ``limit``, as ``width_of`` measures a line, the first
    line no wider than ``first_limit`` when that is given (room taken by a lead set in another face). A first word
    wider than ``first_limit`` leaves the first line empty, beside the lead, and starts the second. Words joined by
    NO_BREAK (see glue) stay together, and come back with plain spaces. A word wider than the limit stands on a line
    of its own, whole: the caller decides what to do with it."""
    lines: list[str] = []
    current = ""
    room = limit if first_limit is None else first_limit
    for token in text.split(" "):
        if not token:
            continue
        word = token.replace(NO_BREAK, " ")
        if not current and not lines and first_limit is not None and width_of(word) > first_limit:
            lines.append("")
            room = limit
        trial = f"{current} {word}" if current else word
        if current and width_of(trial) > room:
            lines.append(current)
            current, room = word, limit
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


def split_segments(percent: float | None, cells: int) -> tuple[int, float]:
    """How a segmented bar fills: (full lit cells, fraction of the next cell that is lit).

    The fraction is 0 when the next cell stays dark. Values below 2 % of a cell count as dark,
    which keeps a sliver of noise out of an otherwise empty bar.
    """
    if percent is None:
        return 0, 0.0
    scaled = max(0.0, min(100.0, percent)) / 100.0 * cells
    full = min(cells, math.floor(scaled))
    if full >= cells:
        return cells, 0.0
    fraction = scaled - full
    return full, fraction if fraction > 0.02 else 0.0


def _place(mask: Image.Image, size: tuple[int, int], x: int, y: int) -> Image.Image:
    """``mask`` placed at (x, y) on a black mask of ``size``."""
    canvas = Image.new("L", size, 0)
    canvas.paste(mask, (x, y))
    return canvas


def _bounds(points: Sequence[Point]) -> tuple[int, int, int, int]:
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    return math.floor(min(xs)), math.floor(min(ys)), math.ceil(max(xs)), math.ceil(max(ys))


def _clip_band(width: int, height: int, u_lo: float, u_hi: float) -> list[Point] | None:
    """The part of the rectangle [0, width] x [0, height] where u_lo <= x + y <= u_hi, as a polygon."""
    rect: list[Point] = [(0.0, 0.0), (float(width), 0.0), (float(width), float(height)), (0.0, float(height))]
    clipped = _clip_half_plane(rect, lambda x, y: x + y - u_lo)
    clipped = _clip_half_plane(clipped, lambda x, y: u_hi - (x + y))
    return clipped if len(clipped) >= 3 else None


def _clip_half_plane(poly: list[Point], value: Callable[[float, float], float]) -> list[Point]:
    """Sutherland-Hodgman: keep the part of ``poly`` where value(x, y) >= 0."""
    out: list[Point] = []
    count = len(poly)
    for index in range(count):
        current = poly[index]
        previous = poly[index - 1]
        v_cur = value(*current)
        v_prev = value(*previous)
        if v_cur >= 0:
            if v_prev < 0:
                out.append(_intersect(previous, current, v_prev, v_cur))
            out.append(current)
        elif v_prev >= 0:
            out.append(_intersect(previous, current, v_prev, v_cur))
    return out


def _intersect(a: Point, b: Point, va: float, vb: float) -> Point:
    t = va / (va - vb)
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
