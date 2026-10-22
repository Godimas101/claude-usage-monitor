"""Pricing: bundled table, model matching, cost tiers, validation, load_best and refresh_remote.

Synthetic records only. Everything runs offline: refresh tests inject an opener, and the
default opener is exercised with urlopen replaced.
"""
from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from usage_monitor.data import pricing
from usage_monitor.data.models import CostBreakdown, RequestRecord
from usage_monitor.data.pricing import BUNDLED_PATH, Pricing, load_best, refresh_remote

MILLION = 1_000_000
TS = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
REMOTE_URL = "https://pricing.example.test/pricing.json"
NEWER = "2099-01-01"        # synthetic: newer than any bundled table will be
BUNDLED = Pricing.load()
BUNDLED_VERSION = BUNDLED.version

# The table from the task, one row per tuple: id, name, input, output, cache_read, fast, long-context.
SPEC_ROWS = [
    ("claude-fable-5-1", "Fable 5.1", 10, 50, 0.25, None, None),
    ("claude-mythos-5-1", "Mythos 5.1", 10, 50, 0.25, None, None),
    ("claude-fable-5", "Fable 5", 10, 50, 1.00, None, None),
    ("claude-mythos-5", "Mythos 5", 10, 50, 1.00, None, None),
    ("claude-opus-5-5", "Opus 5.5", 4, 20, 0.20, 2, None),
    ("claude-opus-5", "Opus 5", 5, 25, 0.50, 2, None),
    ("claude-opus-4-8", "Opus 4.8", 5, 25, 0.50, 2, None),
    ("claude-opus-4-7", "Opus 4.7", 5, 25, 0.50, None, None),
    ("claude-opus-4-6", "Opus 4.6", 5, 25, 0.50, None, None),
    ("claude-opus-4-5", "Opus 4.5", 5, 25, 0.50, None, None),
    ("claude-opus-4-1", "Opus 4.1", 15, 75, 1.50, None, None),
    ("claude-opus-4", "Opus 4", 15, 75, 1.50, None, None),
    ("claude-3-opus", "Opus 3", 15, 75, 1.50, None, None),
    ("claude-sonnet-5-5", "Sonnet 5.5", 2, 10, 0.20, None, None),
    ("claude-sonnet-5", "Sonnet 5", 2, 10, 0.20, None, None),
    ("claude-sonnet-4-6", "Sonnet 4.6", 3, 15, 0.30, None, None),
    ("claude-sonnet-4-5", "Sonnet 4.5", 3, 15, 0.30, None, (200_000, 6, 22.5, 0.60)),
    ("claude-sonnet-4", "Sonnet 4", 3, 15, 0.30, None, (200_000, 6, 22.5, 0.60)),
    ("claude-3-7-sonnet", "Sonnet 3.7", 3, 15, 0.30, None, None),
    ("claude-3-5-sonnet", "Sonnet 3.5", 3, 15, 0.30, None, None),
    ("claude-haiku-5-5", "Haiku 5.5", 0.10, 0.50, 0.01, None, (100_000, 0.50, 2.50, 0.05)),
    ("claude-haiku-4-5", "Haiku 4.5", 1, 5, 0.10, None, None),
    ("claude-3-5-haiku", "Haiku 3.5", 0.80, 4, 0.08, None, None),
    ("claude-3-haiku", "Haiku 3", 0.25, 1.25, 0.03, None, None),
]

MATCH_CASES = [
    ("claude-opus-4-7", "claude-opus-4-7"),                    # must not fall back to claude-opus-4
    ("claude-opus-4", "claude-opus-4"),
    ("claude-opus-4-1-20250805", "claude-opus-4-1"),
    ("claude-haiku-4-5-20251001", "claude-haiku-4-5"),
    ("claude-sonnet-4-20250514", "claude-sonnet-4"),           # must not become Sonnet 4.5
    ("claude-sonnet-4-5-20250929", "claude-sonnet-4-5"),
    ("claude-opus-4-5-20251101", "claude-opus-4-5"),
    ("claude-opus-4-6[1m]", "claude-opus-4-6"),
    ("us.anthropic.claude-opus-5-5", "claude-opus-5-5"),
    ("anthropic.claude-sonnet-5-5", "claude-sonnet-5-5"),
    ("claude-opus-4-5@20251101", "claude-opus-4-5"),
    ("claude-opus-5-5-20260101", "claude-opus-5-5"),          # must not fall back to claude-opus-5
    ("claude-opus-5-20260101", "claude-opus-5"),
    ("claude-3-opus-20240229", "claude-3-opus"),
    ("claude-3-5-sonnet-20241022", "claude-3-5-sonnet"),
    ("claude-3-5-sonnet-latest", "claude-3-5-sonnet"),
    ("anthropic.claude-3-5-haiku-20241022-v1:0", "claude-3-5-haiku"),
    ("Claude-Opus-4-7", "claude-opus-4-7"),
    ("  claude-haiku-5-5  ", "claude-haiku-5-5"),
    ("claude-opus-4-7-thinking", "claude-opus-4-7"),
]

UNKNOWN_MODELS = [
    "<synthetic>",
    "claude-opus-6",         # unknown version: never the nearest family
    "claude-opus-4-9",
    "claude-opus-5-50",      # version-like digits after the family are not a match
    "claude-opus-4-70",
    "claude-haiku-4",
    "claude-opus",
    "",
    "   ",
]


# ── helpers ──────────────────────────────────────────────────────────────────────

def rec(model: str, *, speed: str | None = None, **tokens: int) -> RequestRecord:
    """A synthetic request record. Token counts come in as keyword arguments."""
    return RequestRecord(
        key="msg-synth|req-synth", message_id="msg-synth", request_id="req-synth", ts=TS,
        session_id="sess-synth", project="synthetic", cwd=None, git_branch=None,
        is_subagent=False, agent_id=None, model=model, speed=speed, **tokens,
    )


def _bundled_doc() -> dict[str, Any]:
    return json.loads(BUNDLED_PATH.read_text(encoding="utf-8"))


def _row(doc: dict[str, Any], model_id: str) -> dict[str, Any]:
    return next(m for m in doc["models"] if m["id"] == model_id)


def _model(model_id: str) -> dict[str, Any]:
    return {"id": model_id, "name": model_id.title(), "input": 1, "output": 5,
            "cache_read": 0.1, "fast_multiplier": None, "long_context": None}


def _doc(**overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "version": "2026-01-01",
        "source": "https://example.test/pricing",
        "currency": "USD",
        "unit": "per_million_tokens",
        "cache_write_multipliers": {"5m": 1.25, "1h": 2.0},
        "models": [_model(f"synth-{letter}") for letter in "abcde"],
    }
    doc.update(overrides)
    return doc


def _lc(**overrides: Any) -> dict[str, Any]:
    return {"threshold_tokens": 10, "input": 1, "output": 1, "cache_read": 1, **overrides}


def _remote_doc(version: str = NEWER, opus_47_input: float = 4.5) -> dict[str, Any]:
    doc = _bundled_doc()
    doc["version"] = version
    _row(doc, "claude-opus-4-7")["input"] = opus_47_input
    return doc


def _body(doc: dict[str, Any]) -> bytes:
    return json.dumps(doc).encode("utf-8")


def _write_json(path: Path, doc: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


class FakeOpener:
    """Stands in for the HTTP opener. Returns a canned body or raises, and records each call."""

    def __init__(self, body: Any = b"", error: BaseException | None = None) -> None:
        self.body = body
        self.error = error
        self.calls: list[tuple[str, float]] = []

    def __call__(self, url: str, timeout: float) -> bytes:
        self.calls.append((url, timeout))
        if self.error is not None:
            raise self.error
        return self.body


class _FakeResponse(io.BytesIO):
    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


# ── bundled table ────────────────────────────────────────────────────────────────

def test_bundled_file_header_and_row_count():
    assert BUNDLED.version == "2026-10-21"
    assert BUNDLED.source == "https://platform.claude.com/docs/en/about-claude/pricing"
    assert BUNDLED.cache_write_multipliers == {"5m": 1.25, "1h": 2.0}
    assert [row.id for row in BUNDLED.rows] == [spec[0] for spec in SPEC_ROWS]


@pytest.mark.parametrize("spec", SPEC_ROWS, ids=[spec[0] for spec in SPEC_ROWS])
def test_bundled_row_matches_the_task_table(spec):
    model_id, name, inp, out, cache_read, fast, long = spec
    row = BUNDLED.match(model_id)
    assert row is not None and row.id == model_id
    assert row.name == name
    assert (row.input, row.output, row.cache_read) == (inp, out, cache_read)
    assert row.fast_multiplier == fast
    if long is None:
        assert row.long_context is None
    else:
        threshold, lc_in, lc_out, lc_cache = long
        assert row.long_context.threshold_tokens == threshold
        assert (row.long_context.input, row.long_context.output, row.long_context.cache_read) == (
            lc_in, lc_out, lc_cache)


# ── matching ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("model", "row_id"), MATCH_CASES, ids=[m for m, _ in MATCH_CASES])
def test_match_resolves_to_the_expected_row(model, row_id):
    row = BUNDLED.match(model)
    assert row is not None and row.id == row_id


@pytest.mark.parametrize("model", UNKNOWN_MODELS, ids=[repr(m) for m in UNKNOWN_MODELS])
def test_unknown_synthetic_and_blank_models_do_not_match(model):
    assert BUNDLED.match(model) is None


@pytest.mark.parametrize("model", [None, 123, b"claude-opus-4-7"])
def test_non_string_models_do_not_match(model):
    assert BUNDLED.match(model) is None
    assert BUNDLED.display_name(model) == ""


def test_display_name_is_the_row_name_or_the_raw_id():
    assert BUNDLED.display_name("us.anthropic.claude-opus-5-5") == "Opus 5.5"
    assert BUNDLED.display_name("claude-opus-6") == "claude-opus-6"
    assert BUNDLED.display_name("<synthetic>") == "<synthetic>"


def test_match_memo_is_bounded_and_still_correct():
    table = Pricing.load()
    for i in range(pricing._MAX_MEMO * 2):
        assert table.match(f"unknown-model-{i}") is None
    assert len(table._memo) <= pricing._MAX_MEMO
    assert table.match("claude-opus-4-7").id == "claude-opus-4-7"


# ── cost ─────────────────────────────────────────────────────────────────────────

def test_cache_writes_use_input_rate_times_each_multiplier():
    c = BUNDLED.cost(rec("claude-opus-4-6", cache_write_5m_tokens=MILLION, cache_write_1h_tokens=MILLION))
    assert c.priced is True
    assert c.cache_write_5m == pytest.approx(5 * 1.25)     # 6.25
    assert c.cache_write_1h == pytest.approx(5 * 2.0)      # 10.0
    assert c.total == pytest.approx(16.25)


@pytest.mark.parametrize(
    ("model", "tokens", "rate"),
    [
        ("claude-fable-5-1", MILLION, 0.25),
        ("claude-fable-5", MILLION, 1.00),
        ("claude-opus-4-1", MILLION, 1.50),
        ("claude-sonnet-4-6", MILLION, 0.30),
        ("claude-haiku-5-5", 100_000, 0.01),                 # at threshold: base rate, not long
        ("claude-3-haiku", MILLION, 0.03),
    ],
)
def test_cache_reads_use_each_rows_own_rate(model, tokens, rate):
    c = BUNDLED.cost(rec(model, cache_read_tokens=tokens))
    assert c.cache_read == pytest.approx(tokens * rate / MILLION)


def test_haiku_55_switches_to_long_context_above_100k_prompt_tokens():
    at_threshold = BUNDLED.cost(rec("claude-haiku-5-5", input_tokens=100_000))
    assert at_threshold.input == pytest.approx(100_000 * 0.10 / MILLION)
    over = BUNDLED.cost(rec("claude-haiku-5-5", input_tokens=100_001))
    assert over.input == pytest.approx(100_001 * 0.50 / MILLION)


def test_haiku_55_long_tier_changes_output_and_cache_rates_too():
    base = BUNDLED.cost(rec("claude-haiku-5-5", input_tokens=1_000, output_tokens=MILLION))
    long = BUNDLED.cost(rec("claude-haiku-5-5", input_tokens=100_001, output_tokens=MILLION))
    assert base.output == pytest.approx(0.50)
    assert long.output == pytest.approx(2.50)


def test_prompt_tokens_for_the_tier_include_cache_reads_and_writes():
    c = BUNDLED.cost(rec("claude-haiku-5-5", input_tokens=40_000, cache_read_tokens=30_000,
                         cache_write_5m_tokens=30_001))
    # prompt = 100_001 -> long tier everywhere: cache writes at 0.50 * 1.25 per million
    assert c.input == pytest.approx(40_000 * 0.50 / MILLION)
    assert c.cache_read == pytest.approx(30_000 * 0.05 / MILLION)
    assert c.cache_write_5m == pytest.approx(30_001 * 0.625 / MILLION)


@pytest.mark.parametrize("model", ["claude-sonnet-4-5", "claude-sonnet-4"])
def test_sonnet_45_and_sonnet_4_switch_above_200k_prompt_tokens(model):
    at_threshold = BUNDLED.cost(rec(model, input_tokens=200_000))
    assert at_threshold.input == pytest.approx(200_000 * 3 / MILLION)
    over = BUNDLED.cost(rec(model, input_tokens=200_001))
    assert over.input == pytest.approx(200_001 * 6 / MILLION)
    cached = BUNDLED.cost(rec(model, cache_read_tokens=200_001))
    assert cached.cache_read == pytest.approx(200_001 * 0.60 / MILLION)


def test_rows_without_long_context_never_switch_tiers():
    c = BUNDLED.cost(rec("claude-sonnet-4-6", input_tokens=900_000))
    assert c.input == pytest.approx(2.7)


def test_opus_55_fast_speed_rates_are_exact():
    c = BUNDLED.cost(rec("claude-opus-5-5", speed="fast", input_tokens=MILLION, output_tokens=MILLION,
                         cache_write_5m_tokens=MILLION))
    assert c.input == pytest.approx(8.0)            # 4 * 2
    assert c.output == pytest.approx(40.0)          # 20 * 2
    assert c.cache_write_5m == pytest.approx(10.0)  # 4 * 1.25 * 2


@pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-opus-5", "claude-opus-4-8"])
def test_fast_speed_doubles_every_component_on_fast_rows(model):
    tokens = dict(input_tokens=MILLION, output_tokens=MILLION, cache_read_tokens=MILLION,
                  cache_write_5m_tokens=MILLION, cache_write_1h_tokens=MILLION)
    std = BUNDLED.cost(rec(model, **tokens))
    fast = BUNDLED.cost(rec(model, speed="fast", **tokens))
    for field in ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h", "nocache_total"):
        assert getattr(fast, field) == pytest.approx(2 * getattr(std, field)), field


@pytest.mark.parametrize("model", ["claude-opus-4-6", "claude-sonnet-4-5", "claude-haiku-4-5"])
def test_fast_speed_is_ignored_when_the_row_has_no_multiplier(model):
    std = BUNDLED.cost(rec(model, input_tokens=MILLION, output_tokens=MILLION))
    fast = BUNDLED.cost(rec(model, speed="fast", input_tokens=MILLION, output_tokens=MILLION))
    assert fast == std


def test_standard_speed_is_not_multiplied():
    c = BUNDLED.cost(rec("claude-opus-5-5", speed="standard", input_tokens=MILLION))
    assert c.input == pytest.approx(4.0)


def test_nocache_total_prices_every_prompt_token_at_the_input_rate():
    c = BUNDLED.cost(rec("claude-sonnet-4-6", input_tokens=100, cache_read_tokens=200,
                         cache_write_5m_tokens=300, output_tokens=50))
    assert c.nocache_total == pytest.approx(600 * 3 / MILLION + 50 * 15 / MILLION)   # 0.00255
    assert c.total == pytest.approx(100 * 3 / MILLION + 200 * 0.30 / MILLION
                                    + 300 * 3 * 1.25 / MILLION + 50 * 15 / MILLION)  # 0.002235
    assert c.nocache_total > c.total


def test_nocache_total_follows_the_selected_long_tier_and_fast_multiplier():
    fast = BUNDLED.cost(rec("claude-opus-5-5", speed="fast", input_tokens=MILLION, output_tokens=MILLION))
    assert fast.nocache_total == pytest.approx(8.0 + 40.0)
    long = BUNDLED.cost(rec("claude-haiku-5-5", input_tokens=50_000, cache_read_tokens=50_001,
                            output_tokens=MILLION))
    assert long.nocache_total == pytest.approx(100_001 * 0.50 / MILLION + 2.50)


def test_total_is_the_sum_of_the_priced_components():
    c = BUNDLED.cost(rec("claude-opus-4-7", input_tokens=1000, output_tokens=2000, cache_read_tokens=3000,
                         cache_write_5m_tokens=4000, cache_write_1h_tokens=5000))
    expected = (1000 * 5 + 2000 * 25 + 3000 * 0.5 + 4000 * 5 * 1.25 + 5000 * 5 * 2.0) / MILLION
    assert c.total == pytest.approx(expected)


@pytest.mark.parametrize("model", ["claude-opus-6", "<synthetic>", "", "gpt-4o"])
def test_unknown_models_are_unpriced_with_zero_components(model):
    c = BUNDLED.cost(rec(model, input_tokens=MILLION, output_tokens=MILLION, cache_read_tokens=MILLION))
    assert c == CostBreakdown(priced=False)
    assert c.total == 0.0


def test_zero_token_record_costs_nothing_and_stays_priced():
    c = BUNDLED.cost(rec("claude-opus-5-5", speed="fast"))
    assert c == CostBreakdown()
    assert c.priced is True


def test_cost_returns_a_fresh_breakdown_each_call():
    first = BUNDLED.cost(rec("claude-opus-4-7", input_tokens=1))
    second = BUNDLED.cost(rec("claude-opus-4-7", input_tokens=1))
    assert first == second and first is not second


# ── validation ───────────────────────────────────────────────────────────────────

def test_minimal_valid_document_loads_and_matches():
    table = Pricing.from_dict(_doc())
    assert table.version == "2026-01-01"
    assert table.match("synth-c").name == "Synth-C"


def test_rate_bounds_are_inclusive_at_zero_and_1000():
    doc = _doc()
    doc["models"][0].update(input=0, output=1000)
    row = Pricing.from_dict(doc).match("synth-a")
    assert (row.input, row.output) == (0.0, 1000.0)


def test_unknown_keys_are_ignored_for_forward_compatibility():
    doc = _doc(extra_top_level="x")
    doc["models"][0]["notes"] = "a field this version does not know"
    assert Pricing.from_dict(doc).match("synth-a") is not None


INVALID_EDITS = [
    pytest.param(lambda d: d.pop("version"), id="missing-version"),
    pytest.param(lambda d: d.update(version=""), id="empty-version"),
    pytest.param(lambda d: d.update(version=20261021), id="numeric-version"),
    pytest.param(lambda d: d.update(source=None), id="source-not-string"),
    pytest.param(lambda d: d.update(currency="EUR"), id="currency-not-usd"),
    pytest.param(lambda d: d.update(unit="per_1k_tokens"), id="unit-not-per-million"),
    pytest.param(lambda d: d["cache_write_multipliers"].pop("1h"), id="missing-1h-multiplier"),
    pytest.param(lambda d: d["cache_write_multipliers"].update({"5m": 0}), id="zero-cache-multiplier"),
    pytest.param(lambda d: d.update(models="synth"), id="models-not-a-list"),
    pytest.param(lambda d: d.update(models=d["models"][:4]), id="fewer-than-five-models"),
    pytest.param(lambda d: d["models"].append(_model("synth-a")), id="duplicate-id"),
    pytest.param(lambda d: d["models"].append(_model("Synth-A")), id="duplicate-id-other-case"),
    pytest.param(lambda d: d["models"].__setitem__(0, "not-a-row"), id="row-not-object"),
    pytest.param(lambda d: d["models"][0].update(id="bad id!"), id="id-bad-characters"),
    pytest.param(lambda d: d["models"][0].update(id="us.anthropic.synth-a"), id="id-has-provider-prefix"),
    pytest.param(lambda d: d["models"][0].update(id="synth-a-20251001"), id="id-has-date-suffix"),
    pytest.param(lambda d: d["models"][0].update(name=""), id="empty-name"),
    pytest.param(lambda d: d["models"][0].pop("input"), id="missing-input-rate"),
    pytest.param(lambda d: d["models"][0].update(input=float("nan")), id="nan-rate"),
    pytest.param(lambda d: d["models"][0].update(output=float("inf")), id="infinite-rate"),
    pytest.param(lambda d: d["models"][0].update(cache_read=-0.1), id="negative-rate"),
    pytest.param(lambda d: d["models"][0].update(output=1000.5), id="rate-above-1000"),
    pytest.param(lambda d: d["models"][0].update(input=True), id="boolean-rate"),
    pytest.param(lambda d: d["models"][0].update(input="1"), id="string-rate"),
    pytest.param(lambda d: d["models"][0].update(fast_multiplier=0), id="zero-fast-multiplier"),
    pytest.param(lambda d: d["models"][0].update(fast_multiplier=-2), id="negative-fast-multiplier"),
    pytest.param(lambda d: d["models"][0].update(long_context=[]), id="long-context-not-object"),
    pytest.param(lambda d: d["models"][0].update(long_context=_lc(threshold_tokens=0)), id="zero-threshold"),
    pytest.param(lambda d: d["models"][0].update(long_context=_lc(threshold_tokens=10.5)),
                 id="float-threshold"),
    pytest.param(lambda d: d["models"][0].update(long_context=_lc(threshold_tokens=True)),
                 id="boolean-threshold"),
    pytest.param(lambda d: d["models"][0].update(long_context=_lc(output=None)),
                 id="long-context-rate-missing"),
]


@pytest.mark.parametrize("edit", INVALID_EDITS)
def test_from_dict_rejects_invalid_documents(edit):
    doc = _doc()
    edit(doc)
    with pytest.raises(ValueError):
        Pricing.from_dict(doc)


def test_from_dict_rejects_a_non_object_top_level():
    with pytest.raises(ValueError):
        Pricing.from_dict([_doc()])


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_json_literals_are_rejected(literal):
    text = json.dumps(_doc()).replace('"input": 1,', f'"input": {literal},', 1)
    with pytest.raises(ValueError):
        Pricing.from_dict(json.loads(text))


def test_huge_json_integer_is_a_value_error_not_an_overflow():
    text = json.dumps(_doc()).replace('"input": 1,', '"input": 1' + "0" * 400 + ",", 1)
    with pytest.raises(ValueError):
        Pricing.from_dict(json.loads(text))


# ── load ─────────────────────────────────────────────────────────────────────────

def test_load_reads_a_custom_file(tmp_path):
    path = _write_json(tmp_path / "custom.json", _doc(version="2026-02-02"))
    assert Pricing.load(path).version == "2026-02-02"


def test_load_accepts_a_byte_order_mark(tmp_path):
    path = tmp_path / "bom.json"
    path.write_text(json.dumps(_doc(version="2026-03-03")), encoding="utf-8-sig")
    assert Pricing.load(path).version == "2026-03-03"


def test_load_raises_value_error_for_invalid_json(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        Pricing.load(path)


def test_load_raises_oserror_for_a_missing_file(tmp_path):
    with pytest.raises(OSError):
        Pricing.load(tmp_path / "missing.json")


# ── load_best ────────────────────────────────────────────────────────────────────

def test_load_best_without_a_cache_returns_the_bundled_table(tmp_path):
    assert load_best(BUNDLED_PATH, tmp_path / "missing.json").version == BUNDLED_VERSION


def test_load_best_ignores_an_unparseable_cache(tmp_path):
    cached = tmp_path / "pricing.json"
    cached.write_text("garbage", encoding="utf-8")
    assert load_best(BUNDLED_PATH, cached).version == BUNDLED_VERSION


def test_load_best_ignores_a_cache_that_fails_validation(tmp_path):
    cached = _write_json(tmp_path / "pricing.json", _doc(version="2099-01-01", models=[]))
    assert load_best(BUNDLED_PATH, cached).version == BUNDLED_VERSION


def test_load_best_keeps_the_bundled_table_when_the_cache_is_older(tmp_path):
    older = _write_json(tmp_path / "older.json", _doc(version="2026-01-01"))
    assert load_best(BUNDLED_PATH, older).version == BUNDLED_VERSION


def test_load_best_keeps_the_bundled_table_on_a_version_tie(tmp_path):
    same_version = _remote_doc(version=BUNDLED_VERSION, opus_47_input=99)
    cached = _write_json(tmp_path / "same.json", same_version)
    assert load_best(BUNDLED_PATH, cached).match("claude-opus-4-7").input == 5


def test_load_best_prefers_a_newer_valid_cache(tmp_path):
    cached = _write_json(tmp_path / "pricing.json", _remote_doc(NEWER, opus_47_input=4.5))
    best = load_best(BUNDLED_PATH, cached)
    assert best.version == NEWER
    assert best.match("claude-opus-4-7").input == 4.5


# ── refresh_remote ───────────────────────────────────────────────────────────────

def test_refresh_saves_a_newer_table_atomically(tmp_path):
    cached = tmp_path / "data" / "pricing.json"           # parent folder is created on demand
    opener = FakeOpener(_body(_remote_doc()))
    assert refresh_remote(REMOTE_URL, cached, timeout=3, opener=opener) is True
    assert opener.calls == [(REMOTE_URL, 3)]
    assert Pricing.load(cached).version == NEWER
    assert load_best(BUNDLED_PATH, cached).match("claude-opus-4-7").input == 4.5
    assert list(cached.parent.glob("*.tmp")) == []


@pytest.mark.parametrize("version", [BUNDLED_VERSION, "2026-01-01"])
def test_refresh_ignores_a_table_that_is_not_newer_than_the_bundled_one(tmp_path, version):
    cached = tmp_path / "pricing.json"
    assert refresh_remote(REMOTE_URL, cached, opener=FakeOpener(_body(_remote_doc(version)))) is False
    assert not cached.exists()


def test_refresh_never_downgrades_a_newer_cached_table(tmp_path):
    cached = _write_json(tmp_path / "pricing.json", {**_bundled_doc(), "version": "2099-06-01"})
    before = cached.read_bytes()
    assert refresh_remote(REMOTE_URL, cached, opener=FakeOpener(_body(_remote_doc(NEWER)))) is False
    assert cached.read_bytes() == before


def test_refresh_replaces_an_older_cached_table(tmp_path):
    cached = _write_json(tmp_path / "pricing.json", {**_bundled_doc(), "version": "2026-10-25"})
    assert refresh_remote(REMOTE_URL, cached, opener=FakeOpener(_body(_remote_doc(NEWER)))) is True
    assert Pricing.load(cached).version == NEWER


def test_refresh_compares_against_the_bundled_table_it_is_given(tmp_path):
    bundled = _write_json(tmp_path / "bundled.json", {**_bundled_doc(), "version": "2099-12-31"})
    cached = tmp_path / "pricing.json"
    opener = FakeOpener(_body(_remote_doc(NEWER)))
    assert refresh_remote(REMOTE_URL, cached, opener=opener, bundled=bundled) is False
    assert not cached.exists()


FAILING_OPENERS = [
    pytest.param(FakeOpener(error=OSError("network down")), id="network-error"),
    pytest.param(FakeOpener(error=TimeoutError()), id="timeout"),
    pytest.param(FakeOpener(error=RuntimeError("unexpected")), id="unexpected-error"),
    pytest.param(FakeOpener(body=b"<html>not json</html>"), id="html-body"),
    pytest.param(FakeOpener(body=b""), id="empty-body"),
    pytest.param(FakeOpener(body=b"\xff\xfe not utf-8"), id="bad-encoding"),
    pytest.param(FakeOpener(body="{}"), id="text-instead-of-bytes"),
    pytest.param(FakeOpener(body=_body({**_remote_doc(), "models": []})), id="too-few-models"),
    pytest.param(FakeOpener(body=_body(_remote_doc(opus_47_input=float("nan")))), id="nan-rate"),
    pytest.param(FakeOpener(body=b" " * (pricing._MAX_REMOTE_BYTES + 1)), id="oversized-body"),
]


@pytest.mark.parametrize("opener", FAILING_OPENERS)
def test_refresh_failures_return_false_and_write_nothing(tmp_path, opener):
    cached = tmp_path / "pricing.json"
    assert refresh_remote(REMOTE_URL, cached, opener=opener) is False
    assert not cached.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_failed_refresh_keeps_an_existing_cache(tmp_path):
    cached = _write_json(tmp_path / "pricing.json", {**_bundled_doc(), "version": "2026-10-25"})
    before = cached.read_bytes()
    assert refresh_remote(REMOTE_URL, cached, opener=FakeOpener(error=OSError("offline"))) is False
    assert cached.read_bytes() == before


def test_refresh_never_raises_for_a_garbage_url(tmp_path):
    opener = FakeOpener(_body(_remote_doc()))
    assert refresh_remote(None, tmp_path / "pricing.json", opener=opener) is False  # type: ignore[arg-type]


def test_refresh_refuses_non_https_urls_without_fetching(tmp_path):
    opener = FakeOpener(_body(_remote_doc()))
    assert refresh_remote("http://pricing.example.test/p.json", tmp_path / "p.json", opener=opener) is False
    assert opener.calls == []


def test_failed_write_keeps_the_previous_cache(tmp_path, monkeypatch):
    cached = _write_json(tmp_path / "pricing.json", {**_bundled_doc(), "version": "2026-10-25"})
    before = cached.read_bytes()

    def broken_replace(src, dst):
        raise PermissionError("file in use")

    monkeypatch.setattr(pricing.os, "replace", broken_replace)
    assert refresh_remote(REMOTE_URL, cached, opener=FakeOpener(_body(_remote_doc()))) is False
    assert cached.read_bytes() == before
    assert list(tmp_path.glob("*.tmp")) == []


def test_default_opener_sends_the_timeout_and_caps_the_body(monkeypatch):
    seen: dict[str, Any] = {}
    payload = b"x" * (pricing._MAX_REMOTE_BYTES + 100)

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        return _FakeResponse(payload)

    monkeypatch.setattr(pricing.urllib.request, "urlopen", fake_urlopen)
    body = pricing._fetch(REMOTE_URL, 4)
    assert len(body) == pricing._MAX_REMOTE_BYTES + 1
    assert seen == {"url": REMOTE_URL, "timeout": 4}


def test_refresh_with_the_default_opener_fails_closed_when_offline(tmp_path, monkeypatch):
    def offline(request, timeout):
        raise OSError("offline")

    monkeypatch.setattr(pricing.urllib.request, "urlopen", offline)
    assert refresh_remote(REMOTE_URL, tmp_path / "pricing.json") is False
