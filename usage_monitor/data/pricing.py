"""API-equivalent cost of Claude requests at list prices (USD per million tokens).

Rates live in pricing.json beside this module. refresh_remote() can cache a newer table
in the app data folder, and load_best() prefers whichever table has the newer `version`
(plain string compare, so use ISO dates).

These figures are never real spend. Real money comes only from the usage API's `spend`.
"""
from __future__ import annotations

import contextlib
import json
import math
import os
import re
import tempfile
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from usage_monitor.data.models import CostBreakdown, RequestRecord
from usage_monitor.log import get_logger

log = get_logger(__name__)

BUNDLED_PATH = Path(__file__).with_name("pricing.json")

_PER_MILLION = 1_000_000.0
_MAX_RATE = 1000.0                  # USD per million tokens
_MAX_MULTIPLIER = 100.0
_MIN_MODELS = 5
_MAX_REMOTE_BYTES = 512 * 1024
_MAX_MEMO = 1024                    # raw model strings remembered per Pricing
_MISSING = object()

_ID_RE = re.compile(r"[a-z0-9][a-z0-9.\-]*")
_PROVIDER_RE = re.compile(r"^(?:[a-z0-9-]+\.)*anthropic\.")
_SUFFIX_RES = (
    re.compile(r"\[[^\]]*\]$"),     # "[1m]" context-window tag
    re.compile(r"@[^@]*$"),         # Vertex "@20251101"
    re.compile(r":\d+$"),           # Bedrock ":0"
    re.compile(r"-v\d+$"),          # Bedrock "-v2"
    re.compile(r"-\d{8}$"),         # release date "-20251001"
)
# A row id also matches a longer model id only when a separator follows it and that
# separator is not followed by a digit, so "claude-opus-4-70" never falls back to "claude-opus-4".
_SEPARATORS = frozenset("-_ .:@[/")
_DIGITS = frozenset("0123456789")


@dataclass(frozen=True, slots=True)
class LongContext:
    """Rates that replace the base rates when the prompt is above threshold_tokens."""
    threshold_tokens: int
    input: float                    # USD per million tokens
    output: float
    cache_read: float


@dataclass(frozen=True, slots=True)
class PriceRow:
    """One model's list price. cache_read is an absolute rate; cache writes derive from input."""
    id: str
    name: str
    input: float
    output: float
    cache_read: float
    fast_multiplier: float | None = None
    long_context: LongContext | None = None


@dataclass(frozen=True, slots=True)
class _Tier:
    """Per-token USD rates for one tier and speed, with cache writes already priced."""
    input: float
    output: float
    cache_read: float
    cache_write_5m: float
    cache_write_1h: float


@dataclass(frozen=True, slots=True)
class _Compiled:
    row: PriceRow
    threshold: int | None           # prompt tokens above this use the long-context tiers
    standard: _Tier
    fast: _Tier                     # equals standard when the row has no fast multiplier
    long: _Tier                     # equals standard when the row has no long-context tier
    long_fast: _Tier


class Pricing:
    """A validated price table. Build it with load() or from_dict(); treat it as read-only."""

    def __init__(
        self,
        version: str,
        source: str,
        cache_write_multipliers: Mapping[str, float],
        rows: Sequence[PriceRow],
    ) -> None:
        self.version = version
        self.source = source
        self.cache_write_multipliers: dict[str, float] = dict(cache_write_multipliers)
        self._rows: tuple[PriceRow, ...] = tuple(rows)
        self._by_id = {row.id: _compile(row, self.cache_write_multipliers) for row in self._rows}
        self._memo: dict[str, _Compiled | None] = {}

    @property
    def rows(self) -> tuple[PriceRow, ...]:
        return self._rows

    @classmethod
    def load(cls, path: Path | None = None) -> Pricing:
        """Read and validate a pricing file (default: the bundled one).

        Raises OSError when the file cannot be read and ValueError when it is not a valid table.
        """
        target = BUNDLED_PATH if path is None else Path(path)
        text = target.read_text(encoding="utf-8-sig")
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ValueError(f"{target.name} is not valid JSON: {exc}") from exc
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Pricing:
        """Validate a parsed pricing document. Raises ValueError naming the first problem."""
        if not isinstance(data, dict):
            raise ValueError("pricing must be a JSON object")
        version = _text(data, "version", "pricing", max_len=64)
        source = data.get("source", "")
        if not isinstance(source, str):
            raise ValueError("pricing.source must be a string")
        if data.get("currency") != "USD":
            raise ValueError("pricing.currency must be 'USD'")
        if data.get("unit") != "per_million_tokens":
            raise ValueError("pricing.unit must be 'per_million_tokens'")
        multipliers = _cache_multipliers(data.get("cache_write_multipliers"))
        models = data.get("models")
        if not isinstance(models, list) or len(models) < _MIN_MODELS:
            raise ValueError(f"pricing.models must list at least {_MIN_MODELS} models")
        rows: list[PriceRow] = []
        ids: set[str] = set()
        for index, item in enumerate(models):
            row = _parse_row(item, f"pricing.models[{index}]")
            if row.id in ids:
                raise ValueError(f"pricing.models[{index}]: duplicate id {row.id!r}")
            ids.add(row.id)
            rows.append(row)
        return cls(version, source, multipliers, rows)

    def match(self, model: str | None) -> PriceRow | None:
        """The price row for a model id as recorded in transcripts, or None when unknown."""
        comp = self._resolve(model)
        return comp.row if comp is not None else None

    def display_name(self, model: str | None) -> str:
        """Display name such as "Opus 5.5"; the raw model id when the model is unknown."""
        comp = self._resolve(model)
        if comp is not None:
            return comp.row.name
        return model if isinstance(model, str) else ""

    def cost(self, rec: RequestRecord) -> CostBreakdown:
        """API-equivalent cost of one request. Unknown models give priced=False and zeros."""
        comp = self._resolve(rec.model)
        if comp is None:
            return CostBreakdown(priced=False)
        prompt = rec.prompt_tokens
        fast = rec.speed == "fast"
        if comp.threshold is not None and prompt > comp.threshold:
            tier = comp.long_fast if fast else comp.long
        else:
            tier = comp.fast if fast else comp.standard
        output = rec.output_tokens * tier.output
        return CostBreakdown(
            input=rec.input_tokens * tier.input,
            output=output,
            cache_read=rec.cache_read_tokens * tier.cache_read,
            cache_write_5m=rec.cache_write_5m_tokens * tier.cache_write_5m,
            cache_write_1h=rec.cache_write_1h_tokens * tier.cache_write_1h,
            nocache_total=prompt * tier.input + output,
        )

    def _resolve(self, model: Any) -> _Compiled | None:
        if not isinstance(model, str):
            return None
        hit = self._memo.get(model, _MISSING)
        if hit is _MISSING:
            hit = _lookup(self._by_id, _normalize(model))
            if len(self._memo) >= _MAX_MEMO:
                self._memo.clear()
            self._memo[model] = hit
        return hit


def load_best(bundled: Path, cached: Path) -> Pricing:
    """The bundled table or the cached download, whichever has the newer `version`.

    A missing or invalid cache is ignored. A broken bundled file raises, since that is a build error.
    """
    base = Pricing.load(bundled)
    try:
        candidate = Pricing.load(cached)
    except FileNotFoundError:
        return base
    except (OSError, ValueError) as exc:
        log.warning("ignoring cached pricing: %s", exc)
        return base
    return candidate if candidate.version > base.version else base


# ── Validation helpers ───────────────────────────────────────────────────────────

def _parse_row(item: Any, where: str) -> PriceRow:
    if not isinstance(item, dict):
        raise ValueError(f"{where} must be an object")
    return PriceRow(
        id=_model_id(item.get("id"), f"{where}.id"),
        name=_text(item, "name", where, max_len=60),
        input=_rate(item, "input", where),
        output=_rate(item, "output", where),
        cache_read=_rate(item, "cache_read", where),
        fast_multiplier=_optional_multiplier(item, "fast_multiplier", where),
        long_context=_long_context(item.get("long_context"), where),
    )


def _model_id(value: Any, where: str) -> str:
    model_id = value.strip().lower() if isinstance(value, str) else ""
    if not _ID_RE.fullmatch(model_id):
        raise ValueError(f"{where} must be a model id of a-z, 0-9, '.' and '-'")
    if _normalize(model_id) != model_id:
        raise ValueError(f"{where} must be a bare id with no provider prefix or date suffix")
    return model_id


def _text(obj: Mapping[str, Any], key: str, where: str, *, max_len: int) -> str:
    value = obj.get(key)
    text = value.strip() if isinstance(value, str) else ""
    if not text or len(text) > max_len:
        raise ValueError(f"{where}.{key} must be a non-empty string of at most {max_len} characters")
    return text


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{where} must be a number")
    try:
        number = float(value)
    except OverflowError:  # a huge JSON integer
        raise ValueError(f"{where} is out of range") from None
    if not math.isfinite(number):
        raise ValueError(f"{where} must be finite")
    return number


def _rate(obj: Mapping[str, Any], key: str, where: str) -> float:
    rate = _number(obj.get(key), f"{where}.{key}")
    if not 0.0 <= rate <= _MAX_RATE:
        raise ValueError(f"{where}.{key} must be between 0 and {_MAX_RATE:g}")
    return rate


def _multiplier(value: Any, where: str) -> float:
    factor = _number(value, where)
    if not 0.0 < factor <= _MAX_MULTIPLIER:
        raise ValueError(f"{where} must be above 0 and at most {_MAX_MULTIPLIER:g}")
    return factor


def _optional_multiplier(obj: Mapping[str, Any], key: str, where: str) -> float | None:
    value = obj.get(key)
    return None if value is None else _multiplier(value, f"{where}.{key}")


def _long_context(value: Any, where: str) -> LongContext | None:
    if value is None:
        return None
    lc = f"{where}.long_context"
    if not isinstance(value, dict):
        raise ValueError(f"{lc} must be an object or null")
    threshold = value.get("threshold_tokens")
    if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold <= 0:
        raise ValueError(f"{lc}.threshold_tokens must be a positive integer")
    return LongContext(
        threshold_tokens=threshold,
        input=_rate(value, "input", lc),
        output=_rate(value, "output", lc),
        cache_read=_rate(value, "cache_read", lc),
    )


def _cache_multipliers(value: Any) -> dict[str, float]:
    where = "pricing.cache_write_multipliers"
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be an object")
    return {key: _multiplier(value.get(key), f"{where}.{key}") for key in ("5m", "1h")}


# ── Matching ─────────────────────────────────────────────────────────────────────

def _normalize(model: str) -> str:
    """Drop whitespace, provider prefix and release suffixes.

    "us.anthropic.claude-opus-4-6[1m]" -> "claude-opus-4-6"
    "claude-haiku-4-5-20251001" -> "claude-haiku-4-5"
    """
    text = _PROVIDER_RE.sub("", model.strip().lower(), count=1)
    while True:
        before = text
        for pattern in _SUFFIX_RES:
            text = pattern.sub("", text).rstrip()
        if text == before:
            return text


def _lookup(by_id: Mapping[str, _Compiled], norm: str) -> _Compiled | None:
    """The row whose id equals `norm`, else the longest row id that `norm` continues past a separator."""
    if not norm:
        return None
    hit = by_id.get(norm)
    if hit is not None:
        return hit
    for i in range(len(norm) - 2, 0, -1):
        if norm[i] in _SEPARATORS and norm[i + 1] not in _DIGITS:
            hit = by_id.get(norm[:i])
            if hit is not None:
                return hit
    return None


# ── Precomputed rates ────────────────────────────────────────────────────────────

def _make_tier(inp: float, out: float, cache_read: float, speed: float, cw: Mapping[str, float]) -> _Tier:
    scale = speed / _PER_MILLION
    return _Tier(
        input=inp * scale,
        output=out * scale,
        cache_read=cache_read * scale,
        cache_write_5m=inp * cw["5m"] * scale,
        cache_write_1h=inp * cw["1h"] * scale,
    )


def _compile(row: PriceRow, cw: Mapping[str, float]) -> _Compiled:
    fast_mult = 1.0 if row.fast_multiplier is None else row.fast_multiplier
    standard = _make_tier(row.input, row.output, row.cache_read, 1.0, cw)
    fast = _make_tier(row.input, row.output, row.cache_read, fast_mult, cw)
    lc = row.long_context
    if lc is None:
        return _Compiled(row, None, standard, fast, standard, fast)
    long = _make_tier(lc.input, lc.output, lc.cache_read, 1.0, cw)
    long_fast = _make_tier(lc.input, lc.output, lc.cache_read, fast_mult, cw)
    return _Compiled(row, lc.threshold_tokens, standard, fast, long, long_fast)


# ── Remote refresh ───────────────────────────────────────────────────────────────

def _fetch(url: str, timeout: float) -> bytes:
    """Default opener: one anonymous HTTPS GET, reading at most one byte past the size cap."""
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "claude-usage-monitor"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(_MAX_REMOTE_BYTES + 1)


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _refresh(
    url: str,
    cached: Path,
    timeout: float,
    opener: Callable[[str, float], bytes],
    bundled: Path,
) -> bool:
    if not url.startswith("https://"):
        log.warning("pricing refresh skipped: URL is not https")
        return False
    body = bytes(opener(url, timeout))
    if len(body) > _MAX_REMOTE_BYTES:
        log.warning("pricing refresh skipped: response is over %d bytes", _MAX_REMOTE_BYTES)
        return False
    remote = Pricing.from_dict(json.loads(body.decode("utf-8-sig")))
    current = load_best(bundled, cached).version
    if remote.version <= current:
        log.debug("pricing %s is not newer than %s", remote.version, current)
        return False
    _write_atomic(cached, body)
    log.info("pricing updated to %s", remote.version)
    return True


def refresh_remote(
    url: str,
    cached: Path,
    timeout: float = 10,
    opener: Callable[[str, float], bytes] = _fetch,
    *,
    bundled: Path | None = None,
) -> bool:
    """Save the table at `url` into `cached` when its version is newer than the one in use.

    `opener(url, timeout)` returns the raw body (default: an HTTPS GET). The remote table
    must validate and be newer than the bundled table and any cached copy. Returns True only
    when a newer table was written. Never raises: failures are logged and return False.
    """
    try:
        return _refresh(url, cached, timeout, opener, BUNDLED_PATH if bundled is None else bundled)
    except Exception as exc:  # thread boundary: a failed download must not stop the service
        log.warning("pricing refresh failed (%s): %s", type(exc).__name__, exc)
        return False
