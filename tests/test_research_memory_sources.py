"""Phase 1 tới 3: trí nhớ nghiên cứu, thiếu hụt và độ ưu tiên."""

import json

import pytest

from alphaforge.history.fingerprint import fingerprint
from alphaforge.research.gap import (
    GAP_FIELD,
    GAP_LOOKBACK,
    GAP_SETTING,
    ResearchGap,
)
from alphaforge.research.memory import (
    SOURCE_CURRENT,
    SOURCE_EXPERIMENT,
    SOURCE_HISTORICAL,
    SOURCE_SIMULATION,
    SOURCE_SUBMITTED,
    ResearchMemory,
)
from alphaforge.research.priority import PriorityWeights, ResearchPriority
from alphaforge.storage.db import EvaluationStatus, Status

SETTINGS = {"region": "USA", "universe": "TOP3000", "delay": 1,
            "neutralization": "SUBINDUSTRY"}


def _add_history(db, alpha_id, expression, *, status="ACTIVE", sharpe=1.5,
                 region="USA", universe="TOP3000", delay=1,
                 neutralization="SUBINDUSTRY"):
    meta = fingerprint(expression)
    connection = db.connect()
    try:
        connection.execute(
            """
            INSERT INTO historical_alphas (
                alpha_id, submitted, status, region, universe, delay,
                neutralization, expression, sharpe, source, fingerprint, family,
                template, operators_json, fields_json, windows_json, imported_at
            ) VALUES (?, '2026-08-15', ?, ?, ?, ?, ?, ?, ?, 'brain_submitted',
                      ?, ?, ?, ?, ?, ?, 'now')
            """,
            (alpha_id, status, region, universe, delay, neutralization,
             expression, sharpe, meta["family"], meta["family"], meta["template"],
             json.dumps(meta["operators"]), json.dumps(meta["fields"]),
             json.dumps(meta["windows"])),
        )
    finally:
        connection.close()


# ======================================================================
# Phase 1: nguồn dữ liệu
# ======================================================================
def test_sources_are_distinguished(db):
    _add_history(db, "H1", "rank(close)", status="DECOMMISSIONED")
    _add_history(db, "S1", "rank(open)", status="ACTIVE")

    db.add_alphas(["rank(vwap)"], SETTINGS)                      # đang chờ
    db.add_alphas(["rank(cap)"], SETTINGS, experiment_id=7)      # thuộc thí nghiệm
    db.add_alphas(["rank(high)"], SETTINGS)
    record = [r for r in db.fetch_by_status(Status.PENDING) if "high" in r.expression][0]
    db.update_alpha(record.id, status=Status.PASSED)             # đã mô phỏng
    variant = [r for r in db.fetch_by_status(Status.PENDING) if "cap" in r.expression][0]
    db.update_alpha(variant.id, status=Status.SIMULATED)

    memory = ResearchMemory(db)
    by_source = {}
    for row in memory.load_rows():
        by_source.setdefault(row["source"], []).append(row)

    assert SOURCE_HISTORICAL in by_source
    assert SOURCE_SUBMITTED in by_source
    assert SOURCE_CURRENT in by_source
    assert SOURCE_EXPERIMENT in by_source
    assert SOURCE_SIMULATION in by_source


def test_load_rows_can_filter_by_source(db):
    _add_history(db, "H1", "rank(close)", status="DECOMMISSIONED")
    _add_history(db, "S1", "rank(open)", status="ACTIVE")
    rows = ResearchMemory(db).load_rows([SOURCE_SUBMITTED])
    assert [row["alpha_id"] for row in rows] == ["S1"]


def test_coverage_reports_every_dimension(db):
    _add_history(db, "H1", "group_neutralize(ts_mean(close, 20), subindustry)",
                 region="EUR", universe="TOP1200", delay=0, neutralization="MARKET")
    coverage = ResearchMemory(db).coverage()
    assert coverage["field"]["close"] == 1
    assert coverage["operator"]["ts_mean"] == 1
    assert coverage["lookback"]["20"] == 1
    assert coverage["region"]["EUR"] == 1
    assert coverage["universe"]["TOP1200"] == 1
    assert coverage["delay"]["0"] == 1
    assert coverage["neutralization"]["MARKET"] == 1
    assert coverage["family"] and coverage["template"]


@pytest.mark.parametrize(
    "method,expected_key",
    [
        ("fields_researched", "close"),
        ("operators_researched", "ts_mean"),
        ("lookbacks_tried", "20"),
        ("regions_tried", "USA"),
        ("universes_tried", "TOP3000"),
        ("neutralizations_tried", "SUBINDUSTRY"),
        ("delays_tried", "1"),
    ],
)
def test_query_helpers(db, method, expected_key):
    _add_history(db, "H1", "ts_mean(close, 20)")
    assert expected_key in getattr(ResearchMemory(db), method)()


def test_successful_and_failed_alphas_are_separated(db):
    _add_history(db, "GOOD", "rank(close)", status="ACTIVE", sharpe=2.5)
    _add_history(db, "BAD", "rank(open)", status="FAILED", sharpe=0.1)
    memory = ResearchMemory(db)
    assert [row["alpha_id"] for row in memory.successful_alphas()] == ["GOOD"]
    assert [row["alpha_id"] for row in memory.failed_alphas()] == ["BAD"]


def test_correlation_rejected_is_separate_from_low_metrics(db):
    """Bị loại vì tương quan khác hẳn bị loại vì chỉ số kém."""
    db.add_alphas(["rank(close)", "rank(open)"], SETTINGS)
    records = db.fetch_by_status(Status.PENDING, limit=10)
    db.update_alpha(records[0].id, status=Status.REJECTED,
                    evaluation_status=EvaluationStatus.CORRELATION_FAILED,
                    self_correlation=0.93)
    db.update_alpha(records[1].id, status=Status.REJECTED,
                    reject_reason="Sharpe 0.4 dưới ngưỡng 1.25.")

    rejected = ResearchMemory(db).correlation_rejected()
    assert len(rejected) == 1
    assert rejected[0]["self_correlation"] == 0.93


def test_candidates_are_listed(db):
    db.add_alphas(["rank(close)"], SETTINGS)
    record = db.fetch_by_status(Status.PENDING)[0]
    db.update_alpha(record.id, status=Status.CANDIDATE,
                    evaluation_status=EvaluationStatus.CANDIDATE, score=2.1)
    candidates = ResearchMemory(db).candidates()
    assert len(candidates) == 1
    assert candidates[0]["score"] == 2.1


def test_experiment_outcome_feeds_back(db):
    """Phase 14: kết quả thí nghiệm quay lại trí nhớ nghiên cứu."""
    db.add_alphas([f"rank(f{i})" for i in range(10)], SETTINGS, experiment_id=42)
    records = db.fetch_by_status(Status.PENDING, limit=20)
    for record in records[:6]:
        db.update_alpha(record.id, status=Status.REJECTED, metrics={"sharpe": 0.3})
    for record in records[6:9]:
        db.update_alpha(record.id, status=Status.PASSED, metrics={"sharpe": 1.8},
                        evaluation_status=EvaluationStatus.ROBUST)
    db.update_alpha(records[9].id, status=Status.CANDIDATE, metrics={"sharpe": 2.4},
                    evaluation_status=EvaluationStatus.CANDIDATE)

    outcome = ResearchMemory(db).experiment_outcome(42)
    assert outcome["total"] == 10
    assert outcome["passed"] == 4
    assert outcome["rejected"] == 6
    assert outcome["robust"] == 4
    assert outcome["candidates"] == 1
    assert outcome["median_sharpe"] is not None
    assert outcome["sample_size"] == 10


def test_experiment_outcome_on_unknown_experiment(db):
    outcome = ResearchMemory(db).experiment_outcome(999)
    assert outcome["total"] == 0
    assert outcome["pass_rate"] == 0.0
    assert outcome["median_sharpe"] is None


# ======================================================================
# Phase 3: độ ưu tiên
# ======================================================================
def test_priority_never_concludes_from_tiny_sample():
    score = ResearchPriority().score("F", sample_size=2, median_sharpe=5.0)
    assert score.components["performance"] == 0.5
    assert any("chưa phải kết luận" in reason for reason in score.reasons)


def test_priority_falls_as_saturation_rises():
    priority = ResearchPriority()
    fresh = priority.score("A", sample_size=3, median_sharpe=1.5, pass_rate=0.3)
    saturated = priority.score("B", sample_size=200, median_sharpe=0.2, pass_rate=0.0)
    assert fresh.score > saturated.score


def test_priority_rewards_proven_performance():
    priority = ResearchPriority()
    good = priority.score("A", sample_size=10, median_sharpe=2.0, pass_rate=0.5)
    poor = priority.score("B", sample_size=10, median_sharpe=0.2, pass_rate=0.0)
    assert good.score > poor.score


def test_priority_components_are_all_present_and_bounded():
    score = ResearchPriority().score("A", sample_size=10, median_sharpe=1.5)
    assert set(score.components) == {
        "underexplored", "performance", "diversity", "recency",
    }
    assert all(0.0 <= value <= 1.0 for value in score.components.values())


def test_priority_weights_are_configurable():
    """Đặt trọng số về 0 thì thành phần đó không còn ảnh hưởng."""
    without = ResearchPriority(PriorityWeights(performance=0.0))
    a = without.score("A", sample_size=10, median_sharpe=2.0)
    b = without.score("B", sample_size=10, median_sharpe=0.1)
    assert a.score == b.score


def test_priority_is_reproducible():
    priority = ResearchPriority()
    args = dict(sample_size=7, median_sharpe=1.4, pass_rate=0.2)
    assert priority.score("A", **args).score == priority.score("A", **args).score


def test_priority_explanation_is_human_readable():
    score = ResearchPriority().score("close", dimension="field", sample_size=3)
    text = ResearchPriority().explain(score)
    assert "Research Priority" in text
    assert "Lý do:" in text
    assert score.reasons


def test_recency_favours_long_untouched_areas():
    priority = ResearchPriority()
    old = priority.score("A", sample_size=10, median_sharpe=1.5, days_since_last=365)
    recent = priority.score("B", sample_size=10, median_sharpe=1.5, days_since_last=1)
    assert old.score > recent.score


def test_diversity_penalises_already_varied_areas():
    priority = ResearchPriority()
    varied = priority.score("A", sample_size=10, median_sharpe=1.5,
                            distinct_neighbours=9, total_neighbours=10)
    narrow = priority.score("B", sample_size=10, median_sharpe=1.5,
                            distinct_neighbours=1, total_neighbours=10)
    assert narrow.score > varied.score


def test_rank_orders_by_score():
    priority = ResearchPriority()
    scores = [
        priority.score("low", sample_size=100, median_sharpe=0.1),
        priority.score("high", sample_size=6, median_sharpe=2.0, pass_rate=0.5),
    ]
    assert priority.rank(scores)[0].key == "high"


# ======================================================================
# Phase 2: thiếu hụt nghiên cứu
# ======================================================================
def test_underexplored_field_is_reported(db):
    for index in range(30):
        _add_history(db, f"V{index}", f"rank(ts_mean(volume, {5 + index}))")
    _add_history(db, "C1", "rank(ts_mean(cap, 20))")

    gaps = ResearchGap(ResearchMemory(db)).find([GAP_FIELD])
    keys = {gap.key for gap in gaps}
    assert "cap" in keys
    assert "volume" not in keys


def test_never_tried_value_is_detected_from_known_values(db):
    """Giá trị chưa xuất hiện lần nào không thể thấy bằng cách đếm dữ liệu đã có."""
    _add_history(db, "H1", "rank(close)")
    gaps = ResearchGap(ResearchMemory(db)).find(
        [GAP_FIELD], known_values={"field": ["close", "volume", "cap"]}
    )
    never = {gap.key for gap in gaps if gap.detail.get("never_tried")}
    assert never == {"volume", "cap"}


def test_setting_coverage_gap_when_all_alphas_share_one_delay(db):
    """25 alpha nhưng toàn delay 1 thì vẫn là thiếu hụt độ phủ."""
    for index in range(25):
        _add_history(db, f"D{index}", f"rank(ts_mean(close, {5 + index}))", delay=1)
    gaps = ResearchGap(ResearchMemory(db)).find([GAP_SETTING])
    delay_gaps = [gap for gap in gaps if gap.detail.get("dimension") == "delay"]
    assert delay_gaps
    assert delay_gaps[0].detail["dominant_value"] == "1"
    assert delay_gaps[0].detail["share"] == 1.0


def test_no_coverage_gap_when_settings_are_varied(db):
    for index in range(25):
        _add_history(db, f"D{index}", f"rank(ts_mean(close, {5 + index}))",
                     delay=index % 2)
    gaps = ResearchGap(ResearchMemory(db)).find([GAP_SETTING])
    assert not [gap for gap in gaps if gap.detail.get("dimension") == "delay"]


def test_coverage_gap_needs_enough_sample(db):
    """Ba alpha cùng delay chưa đủ để gọi là thiếu độ phủ."""
    for index in range(3):
        _add_history(db, f"D{index}", f"rank(ts_mean(close, {5 + index}))", delay=1)
    gaps = ResearchGap(ResearchMemory(db)).find([GAP_SETTING])
    assert not [gap for gap in gaps if gap.detail.get("dimension") == "delay"]


def test_lookback_gap_detects_untried_windows(db):
    _add_history(db, "H1", "ts_mean(close, 5)")
    gaps = ResearchGap(ResearchMemory(db)).find(
        [GAP_LOOKBACK], known_values={"lookback": ["5", "20", "250"]}
    )
    assert {gap.key for gap in gaps if gap.detail.get("never_tried")} == {"20", "250"}


def test_gaps_carry_priority_and_reason(db):
    _add_history(db, "H1", "rank(cap)")
    gap = ResearchGap(ResearchMemory(db)).find([GAP_FIELD])[0]
    assert gap.priority is not None
    assert gap.reason
    assert "priority" in gap.as_dict()


def test_summary_groups_by_kind(db):
    _add_history(db, "H1", "rank(cap)")
    summary = ResearchGap(ResearchMemory(db)).summary(
        known_values={"field": ["cap", "volume"]}
    )
    assert summary["total"] > 0
    assert GAP_FIELD in summary["by_kind"]
    assert summary["thresholds"]["underexplored"] > 0


def test_thresholds_are_configurable(db):
    for index in range(10):
        _add_history(db, f"H{index}", f"rank(ts_mean(close, {5 + index}))")
    strict = ResearchGap(ResearchMemory(db), underexplored_threshold=1)
    loose = ResearchGap(ResearchMemory(db), underexplored_threshold=50)
    assert len(loose.find([GAP_FIELD])) > len(strict.find([GAP_FIELD]))


def test_empty_memory_produces_no_gaps_without_known_values(db):
    assert ResearchGap(ResearchMemory(db)).find() == []
