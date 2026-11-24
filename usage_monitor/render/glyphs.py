"""Which characters a font file maps to a glyph: its character map (cmap), read with the standard library (#92).

The engine picks a face per character with it (engine.face_index), as CSS font matching does: a character the run's
own face lacks comes from the first fallback face that has it. Only a face that has fallbacks, and those, are
read, each once.

The cmap's formats 4, 6 and 12 are read, from the first Unicode subtable found in SUBTABLES' order. A font collection
(.ttc) uses its first font. A file that cannot be read or parsed gives None and one warning in the log: as a run's
own face it then counts as having every character, and as a fallback face as having none, so the run is drawn in its
own face, as runs were before fallbacks existed.
"""
from __future__ import annotations

import functools
import struct
from collections.abc import Mapping
from types import MappingProxyType

from usage_monitor.log import get_logger

log = get_logger(__name__)

# (platform, encoding) of the Unicode subtables, best first: full Unicode (3,10 and 0,6 or 0,4), then the BMP.
SUBTABLES: tuple[tuple[int, int], ...] = ((3, 10), (0, 6), (0, 4), (3, 1), (0, 3), (0, 2), (0, 1), (0, 0))
_FORMATS = (4, 6, 12)
_UNREADABLE = (OSError, struct.error, KeyError, ValueError, IndexError, OverflowError)


def _tables(data: bytes) -> dict[bytes, tuple[int, int]]:
    """The table directory: tag -> (offset, length). A collection gives its first font's tables."""
    base = struct.unpack_from(">I", data, 12)[0] if data[:4] == b"ttcf" else 0
    count = struct.unpack_from(">H", data, base + 4)[0]
    found: dict[bytes, tuple[int, int]] = {}
    for i in range(count):
        tag, _checksum, offset, length = struct.unpack_from(">4sIII", data, base + 12 + 16 * i)
        found[tag] = (offset, length)
    return found


def _format_4(data: bytes, at: int) -> dict[int, int]:
    segments = struct.unpack_from(">H", data, at + 6)[0] // 2
    ends = struct.unpack_from(f">{segments}H", data, at + 14)
    starts = struct.unpack_from(f">{segments}H", data, at + 16 + 2 * segments)
    deltas = struct.unpack_from(f">{segments}h", data, at + 16 + 4 * segments)
    range_at = at + 16 + 6 * segments
    ranges = struct.unpack_from(f">{segments}H", data, range_at)
    found: dict[int, int] = {}
    for k in range(segments):
        for code in range(starts[k], ends[k] + 1):
            if code == 0xFFFF:
                continue
            if ranges[k] == 0:
                glyph = (code + deltas[k]) & 0xFFFF
            else:
                glyph = struct.unpack_from(">H", data, range_at + 2 * k + ranges[k] + 2 * (code - starts[k]))[0]
                if glyph:
                    glyph = (glyph + deltas[k]) & 0xFFFF
            if glyph:
                found[code] = glyph
    return found


def _format_6(data: bytes, at: int) -> dict[int, int]:
    first, count = struct.unpack_from(">HH", data, at + 6)
    glyphs = struct.unpack_from(f">{count}H", data, at + 10)
    return {first + i: glyph for i, glyph in enumerate(glyphs) if glyph}


def _format_12(data: bytes, at: int) -> dict[int, int]:
    groups = struct.unpack_from(">I", data, at + 12)[0]
    found: dict[int, int] = {}
    for k in range(groups):
        first, last, start = struct.unpack_from(">III", data, at + 16 + 12 * k)
        for step, code in enumerate(range(first, last + 1)):
            if start + step:
                found[code] = start + step
    return found


def _read_cmap(data: bytes) -> dict[int, int] | None:
    base = _tables(data)[b"cmap"][0]
    count = struct.unpack_from(">H", data, base + 2)[0]
    subtables: dict[tuple[int, int], int] = {}
    for i in range(count):
        platform, encoding, offset = struct.unpack_from(">HHI", data, base + 4 + 8 * i)
        subtables.setdefault((platform, encoding), base + offset)
    for key in SUBTABLES:
        at = subtables.get(key)
        if at is None:
            continue
        fmt = struct.unpack_from(">H", data, at)[0]
        if fmt == 4:
            return _format_4(data, at)
        if fmt == 6:
            return _format_6(data, at)
        if fmt == 12:
            return _format_12(data, at)
    return None


@functools.lru_cache(maxsize=None)
def cmap(path: str) -> Mapping[int, int] | None:
    """Code point -> glyph id for the font at ``path``; None (logged once) when the file cannot be read or parsed, or
    has no Unicode subtable in a format this reads (4, 6 or 12)."""
    try:
        with open(path, "rb") as handle:
            found = _read_cmap(handle.read())
    except _UNREADABLE as exc:
        log.warning("cannot read the character map of %s (%s: %s): no fallback face is chosen by it", path,
                    type(exc).__name__, exc)
        return None
    if found is None:
        log.warning("%s has no Unicode character map in format %s: no fallback face is chosen by it", path,
                    ", ".join(str(fmt) for fmt in _FORMATS))
        return None
    return MappingProxyType(found)


@functools.lru_cache(maxsize=None)
def codepoints(path: str) -> frozenset[int] | None:
    """The code points the font at ``path`` maps to a glyph, or None when its character map cannot be read."""
    found = cmap(path)
    return None if found is None else frozenset(found)
