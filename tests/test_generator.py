"""Kiểm thử bộ sinh biểu thức. Không cần mạng."""

import pytest

from alphaforge.generator.engine import GenerationRequest, GeneratorEngine

FIELDS = ["close", "volume", "returns", "cap", "vwap"]


def test_template_strategy_respects_limit_and_uniqueness():
    request = GenerationRequest(strategy="template", limit=40, fields=FIELDS, seed=7)
    expressions = GeneratorEngine(request).generate()
    assert len(expressions) <= 40
    assert len(set(expressions)) == len(expressions)
    assert all(expression.strip() for expression in expressions)


def test_template_strategy_is_reproducible_with_seed():
    first = GeneratorEngine(
        GenerationRequest(strategy="template", limit=25, fields=FIELDS, seed=42)
    ).generate()
    second = GeneratorEngine(
        GenerationRequest(strategy="template", limit=25, fields=FIELDS, seed=42)
    ).generate()
    assert first == second


def test_pairwise_uses_two_distinct_fields():
    request = GenerationRequest(
        strategy="pairwise", limit=30, fields=FIELDS, seed=3
    )
    expressions = GeneratorEngine(request).generate()
    assert expressions
    assert len(set(expressions)) == len(expressions)


def test_pairwise_requires_two_fields():
    request = GenerationRequest(strategy="pairwise", limit=5, fields=["close"])
    with pytest.raises(ValueError):
        GeneratorEngine(request).generate()


def test_mutate_wraps_seed_expressions():
    request = GenerationRequest(
        strategy="mutate",
        limit=10,
        fields=FIELDS,
        seed=1,
        seed_expressions=["rank(close)"],
    )
    expressions = GeneratorEngine(request).generate()
    assert expressions
    assert all("rank(close)" in expression for expression in expressions)


def test_max_length_filter_drops_long_expressions():
    request = GenerationRequest(
        strategy="template", limit=20, fields=FIELDS, seed=5, max_length=5
    )
    assert GeneratorEngine(request).generate() == []


def test_unknown_strategy_raises():
    with pytest.raises(ValueError):
        GeneratorEngine(
            GenerationRequest(strategy="khong_ton_tai", fields=FIELDS)
        ).generate()


def test_category_filter_limits_templates():
    request = GenerationRequest(
        strategy="template", limit=15, fields=FIELDS, seed=2, categories=["reversal"]
    )
    expressions = GeneratorEngine(request).generate()
    assert expressions
    assert all(
        expression.startswith("-rank(ts_delta") or expression.startswith("-ts_zscore")
        for expression in expressions
    )
