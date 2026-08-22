"""Kiểm thử bộ chấm điểm."""

from alphaforge.pipeline.scorer import Scorer

CONFIG = {
    "min_sharpe": 1.25,
    "min_fitness": 1.0,
    "min_turnover": 0.01,
    "max_turnover": 0.70,
    "max_drawdown": 0.50,
    "min_margin_bps": 5.0,
    "min_long_count": 100,
    "min_short_count": 100,
    "require_pass_all_checks": False,
    "weights": {"sharpe": 0.45, "fitness": 0.35, "margin": 0.10, "turnover_penalty": 0.10},
}

GOOD = {
    "sharpe": 1.8,
    "fitness": 1.4,
    "turnover": 0.25,
    "drawdown": 0.12,
    "marginBps": 12.0,
    "longCount": 400,
    "shortCount": 380,
    "checkFailures": [],
}


def test_good_metrics_pass():
    result = Scorer(CONFIG).evaluate(GOOD)
    assert result.passed
    assert result.score > 0
    assert result.reasons == []


def test_low_sharpe_is_rejected():
    metrics = dict(GOOD, sharpe=0.6)
    result = Scorer(CONFIG).evaluate(metrics)
    assert not result.passed
    assert any("Sharpe" in reason for reason in result.reasons)


def test_negative_sharpe_is_flagged_even_when_magnitude_is_high():
    metrics = dict(GOOD, sharpe=-1.9)
    result = Scorer(CONFIG).evaluate(metrics)
    assert not result.passed
    assert any("đảo dấu" in reason for reason in result.reasons)


def test_turnover_above_limit_is_rejected():
    result = Scorer(CONFIG).evaluate(dict(GOOD, turnover=0.95))
    assert not result.passed
    assert any("Turnover" in reason for reason in result.reasons)


def test_missing_metrics_returns_failure():
    result = Scorer(CONFIG).evaluate(None)
    assert not result.passed
    assert result.score == 0.0


def test_check_failures_only_matter_when_required():
    metrics = dict(GOOD, checkFailures=["LOW_SHARPE"])
    assert Scorer(CONFIG).evaluate(metrics).passed
    strict = dict(CONFIG, require_pass_all_checks=True)
    assert not Scorer(strict).evaluate(metrics).passed


def test_score_ranks_higher_sharpe_first():
    scorer = Scorer(CONFIG)
    high = scorer.compute_score(dict(GOOD, sharpe=2.5))
    low = scorer.compute_score(dict(GOOD, sharpe=1.3))
    assert high > low
