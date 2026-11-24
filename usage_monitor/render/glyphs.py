"""Which characters a font file maps to a glyph: its character map (cmap), read with the standard library (#92).

The engine picks a face per character with it (engine.face_index), as CSS font matching does: a character the run's
own face lacks comes from the first fallback face that has it. Only a face that has fallbacks, and those, are
read, each once.

The cmap's formats 4, 6 and 12 are read, from the first Unicode subtable found in SUBTABLES' order. A font collection
(.ttc) uses its first font. Every read is held to the end of the cmap table, and a subtable must keep its format's
order, as FreeType's validator requires: format 4's segments and format 12's groups ascend and none overlaps another.
That caps the work at one entry per code point, so a damaged file costs no more to read than a sound one. A glyph id at
or above the font's glyph count (maxp) is dropped, as FreeType drops it; a font without maxp keeps every id.

A file that cannot be read or parsed, or whose map breaks those rules, gives None and one warning in the log: as a
run's own face it then counts as having every character, and as a fallback face as having none, so the run is drawn
in its own face, as runs were before fallbacks existed.
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
_UNREADABLE = (OSError, struct.error, KeyError, ValueError, IndexError, OverflowError, MemoryError)
_LAST_CODE = 0x10FFFF                                     # the last Unicode code point


def _tables(data: bytes) -> dict[bytes, tuple[int, int]]:
    """The table directory: tag -> (offset, length). A collection gives its first font's tables."""
    base = struct.unpack_from(">I", data, 12)[0] if data[:4] == b"ttcf" else 0
    count = struct.unpack_from(">H", data, base + 4)[0]
    found: dict[bytes, tuple[int, int]] = {}
    for i in range(count):
        tag, _checksum, offset, length = struct.unpack_from(">4sIII", data, base + 12 + 16 * i)
        found[tag] = (offset, length)
    return found


def _unpack(fmt: str, data: bytes, at: int, end: int) -> tuple[int, ...]:
    """struct.unpack_from, held to ``data[:end]``: ValueError when the bytes run past ``end``."""
    if at + struct.calcsize(fmt) > end:
        raise ValueError(f"{struct.calcsize(fmt)} bytes at {at} run past the table's end at {end}")
    return struct.unpack_from(fmt, data, at)


def _format_4(data: bytes, at: int, end: int) -> dict[int, int]:
    """Segments of 16-bit codes. The subtable's own length is not used: it is a uint16, and it wraps in a large font,
    so every read is held to the cmap table's end instead, as FreeType holds it. The segments must ascend without
    overlap (each starts after the one before it ends, and ends at or after its own start), which caps the codes
    read at 65,536, and a glyph id read through idRangeOffset must lie in the glyph id array."""
    segments = _unpack(">H", data, at + 6, end)[0] // 2
    ends = _unpack(f">{segments}H", data, at + 14, end)
    starts = _unpack(f">{segments}H", data, at + 16 + 2 * segments, end)
    deltas = _unpack(f">{segments}h", data, at + 16 + 4 * segments, end)
    range_at = at + 16 + 6 * segments
    ranges = _unpack(f">{segments}H", data, range_at, end)
    glyph_ids_at = range_at + 2 * segments
    previous = -1
    for start, stop in zip(starts, ends):
        if not previous < start <= stop:
            raise ValueError(f"format 4 segment {start:#06x}..{stop:#06x} is out of order or overlaps the one before")
        previous = stop
    found: dict[int, int] = {}
    for k in range(segments):
        for code in range(starts[k], ends[k] + 1):
            if code == 0xFFFF:
                continue
            if ranges[k] == 0:
                glyph = (code + deltas[k]) & 0xFFFF
            else:
                where = range_at + 2 * k + ranges[k] + 2 * (code - starts[k])
                if where < glyph_ids_at or where + 2 > end:
                    raise ValueError(f"format 4 idRangeOffset {ranges[k]} reads outside the glyph id array")
                glyph = struct.unpack_from(">H", data, where)[0]
                if glyph:
                    glyph = (glyph + deltas[k]) & 0xFFFF
            if glyph:
                found[code] = glyph
    return found


def _format_6(data: bytes, at: int, end: int) -> dict[int, int]:
    first, count = _unpack(">HH", data, at + 6, end)
    glyphs = _unpack(f">{count}H", data, at + 10, end)
    return {first + i: glyph for i, glyph in enumerate(glyphs) if glyph}


def _format_12(data: bytes, at: int, end: int) -> dict[int, int]:
    """Groups of 32-bit codes. The subtable's length (a uint32) must hold its groups and end inside the cmap table,
    and the groups must ascend without overlap and stop at U+10FFFF, which caps the codes read at 0x110000."""
    length, _language, groups = _unpack(">III", data, at + 4, end)
    if 16 + 12 * groups > length or at + length > end:
        raise ValueError(f"format 12's {groups} groups run past its length ({length}) or the character map's end")
    found: dict[int, int] = {}
    previous = -1
    for k in range(groups):
        first, last, start = struct.unpack_from(">III", data, at + 16 + 12 * k)
        if not previous < first <= last <= _LAST_CODE:
            raise ValueError(f"format 12 group {first:#x}..{last:#x} is out of order, overlaps or passes U+10FFFF")
        previous = last
        for step, code in enumerate(range(first, last + 1)):
            if start + step:
                found[code] = start + step
    return found


_READERS = {4: _format_4, 6: _format_6, 12: _format_12}
_FORMATS = tuple(_READERS)


def _glyph_count(data: bytes, tables: dict[bytes, tuple[int, int]]) -> int | None:
    """maxp's numGlyphs, or None when the font has no maxp table."""
    if b"maxp" not in tables:
        return None
    offset, length = tables[b"maxp"]
    return _unpack(">H", data, offset + 4, min(offset + length, len(data)))[0]


def _read_cmap(data: bytes) -> dict[int, int] | None:
    tables = _tables(data)
    base, length = tables[b"cmap"]
    end = min(base + length, len(data))
    count = _unpack(">H", data, base + 2, end)[0]
    subtables: dict[tuple[int, int], int] = {}
    for i in range(count):
        platform, encoding, offset = _unpack(">HHI", data, base + 4 + 8 * i, end)
        subtables.setdefault((platform, encoding), base + offset)
    for key in SUBTABLES:
        at = subtables.get(key)
        if at is None:
            continue
        reader = _READERS.get(_unpack(">H", data, at, end)[0])
        if reader is None:
            continue
        found = reader(data, at, end)
        glyph_count = _glyph_count(data, tables)
        if glyph_count is None:
            return found
        return {code: glyph for code, glyph in found.items() if glyph < glyph_count}
    return None


@functools.lru_cache(maxsize=None)
def cmap(path: str) -> Mapping[int, int] | None:
    """Code point -> glyph id for the font at ``path``; None (logged once) when the file cannot be read or parsed, its
    map breaks its format's rules, or it has no Unicode subtable in a format this reads (4, 6 or 12)."""
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
