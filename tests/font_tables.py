"""Glyph outlines for the coverage test (#92), standard library only, read-only.

usage_monitor/render/glyphs.py says which glyph a character maps to. This module says whether that glyph has an
outline, from the head, maxp, loca and glyf tables, so a character mapped to an empty glyph (a blank, which no
screenshot can catch) is found. A composite glyph has an outline when one of its parts has one. A face whose outlines
are not in glyf (a CFF font) is refused with "outline format not read": none ships today, and the first one must teach
this reader its format before it can pass the test.
"""
from __future__ import annotations

import functools
import struct

MORE_COMPONENTS = 0x0020
ARGS_ARE_WORDS = 0x0001
HAVE_A_SCALE = 0x0008
HAVE_X_AND_Y_SCALE = 0x0040
HAVE_TWO_BY_TWO = 0x0080
MAX_DEPTH = 8                   # composites nest a level or two; a loop in a broken file stops here


@functools.lru_cache(maxsize=None)
def font_bytes(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


@functools.lru_cache(maxsize=None)
def tables(path: str) -> dict[bytes, tuple[int, int]]:
    """tag -> (offset, length) of the font at ``path`` (a collection's first font)."""
    data = font_bytes(path)
    base = struct.unpack_from(">I", data, 12)[0] if data[:4] == b"ttcf" else 0
    count = struct.unpack_from(">H", data, base + 4)[0]
    found: dict[bytes, tuple[int, int]] = {}
    for i in range(count):
        tag, _checksum, offset, length = struct.unpack_from(">4sIII", data, base + 12 + 16 * i)
        found[tag] = (offset, length)
    return found


def outline_format(path: str) -> str:
    """"glyf" (TrueType outlines), "CFF" (PostScript outlines) or "?"."""
    found = tables(path)
    if b"glyf" in found and b"loca" in found:
        return "glyf"
    return "CFF" if b"CFF " in found or b"CFF2" in found else "?"


@functools.lru_cache(maxsize=None)
def _locations(path: str) -> tuple[int, ...]:
    """Each glyph's start in glyf, plus the end of the last one."""
    if outline_format(path) != "glyf":
        raise ValueError(f"{path}: outline format not read ({outline_format(path)})")
    data = font_bytes(path)
    found = tables(path)
    long_offsets = struct.unpack_from(">h", data, found[b"head"][0] + 50)[0]
    count = struct.unpack_from(">H", data, found[b"maxp"][0] + 4)[0]
    at = found[b"loca"][0]
    if long_offsets:
        return struct.unpack_from(f">{count + 1}I", data, at)
    return tuple(2 * value for value in struct.unpack_from(f">{count + 1}H", data, at))


def glyph_count(path: str) -> int:
    return len(_locations(path)) - 1


def has_outline(path: str, glyph: int, _depth: int = 0) -> bool:
    """True when glyph ``glyph`` of the font at ``path`` has at least one contour, itself or in a part."""
    locations = _locations(path)
    start, end = locations[glyph], locations[glyph + 1]
    if end <= start:
        return False                                    # an empty glyph: a space, or a blank
    data = font_bytes(path)
    at = tables(path)[b"glyf"][0] + start
    contours = struct.unpack_from(">h", data, at)[0]
    if contours >= 0:
        return contours > 0
    if _depth >= MAX_DEPTH:
        return False
    at += 10                                            # a composite: its parts follow the glyph header
    while True:
        flags, part = struct.unpack_from(">HH", data, at)
        if has_outline(path, part, _depth + 1):
            return True
        at += 4 + (4 if flags & ARGS_ARE_WORDS else 2)
        if flags & HAVE_A_SCALE:
            at += 2
        elif flags & HAVE_X_AND_Y_SCALE:
            at += 4
        elif flags & HAVE_TWO_BY_TWO:
            at += 8
        if not flags & MORE_COMPONENTS:
            return False
