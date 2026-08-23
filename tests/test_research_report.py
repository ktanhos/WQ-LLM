"""Báo cáo kết quả thí nghiệm.

Điều quan trọng nhất mà báo cáo phải làm đúng: **không kết luận khi chưa đủ
bằng chứng**. Bốn alpha chênh nhau chút ít không phải là phát hiện, và một báo
cáo gọi đó là phát hiện còn tệ hơn không có báo cáo, vì nó khiến người đọc tin
vào thứ không có thật.
"""

import pytest

from alphaforge.research.models import Experiment, Hypothesis, ResearchProject
from alphaforge.research.report import (
    EVIDENCE_INSUFFICIENT,
    EVIDENCE_MODERATE,
    EVIDENCE_STRONG,
    EVIDENCE_WEAK,
    ExperimentReport,
    evidence_level,
)
from alphaforge.research.store import ResearchStore
from alphaforge.storage.db import Status

SETTINGS = {"region": "USA", "universe": "TOP3000", "delay": 1}


@pytest.fixture()
def store(db):
    return ResearchStore(db)


@pytest.fixture()
def experiment_id(store):
    research_id = store.create_project(ResearchProject(name="Du an"))
    hypothesis_id = store.create_hypothesis(
        Hypothesis(research_id=research_id, statement="Cua so dai on dinh hon")
    )
    return store.create_experiment(
        Experiment(hypothesis_id=hypothesis_id, name="Khao sat cua so",
                   base_expression="rank(ts_mean(close, 20))",
                   variable_changed="lookback", settings=dict(SETTINGS))
    )


def _add_alpha(db, experiment_id, expression, *, sharpe, status=Status.PASSED):
    db.add_alphas([expression], SETTINGS, experiment_id=experiment_id)
    row_id = next(
        record.id for record in db.fetch_by_status(Status.PENDING)
        if record.expression == expression
    )
    db.update_alpha(
        row_id, status=status, alpha_id=f"BRAIN-{row_id:05d}",
        metrics={"sharpe": sharpe, "fitness": 1.0, "turnover": 0.3},
    )
    return row_id


# ----------------------------------------------------------------------
# Mức bằng chứng
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "sample_size,expected",
    [
        (0, EVIDENCE_INSUFFICIENT),
        (7, EVIDENCE_INSUFFICIENT),
        (8, EVIDENCE_WEAK),
        (29, EVIDENCE_WEAK),
        (30, EVIDENCE_MODERATE),
        (99, EVIDENCE_MODERATE),
        (100, EVIDENCE_STRONG),
        (5000, EVIDENCE_STRONG),
    ],
)
def test_evidence_level_follows_sample_size(sample_size, expected):
    assert evidence_level(sample_size) == expected


def test_evidence_threshold_is_configurable():
    """Ngưỡng cỡ mẫu tối thiểu là tham số, không viết cứng."""
    assert evidence_level(5, min_sample=3) == EVIDENCE_INSUFFICIENT
    assert evidence_level(20, min_sample=3) == EVIDENCE_WEAK


# ----------------------------------------------------------------------
# Thí nghiệm rỗng và cỡ mẫu nhỏ
# ----------------------------------------------------------------------
def test_report_for_a_missing_experiment_raises(db):
    with pytest.raises(ValueError, match="Không tìm thấy"):
        ExperimentReport(db).build(999)


def test_report_for_an_experiment_with_no_alphas(db, experiment_id):
    """Thí nghiệm chưa chạy vẫn dựng được báo cáo, chỉ là không kết luận gì."""
    report = ExperimentReport(db).build(experiment_id)
    assert report["counts"]["alpha_count"] == 0
    assert report["conclusion"]["evidence"] == EVIDENCE_INSUFFICIENT
    assert report["conclusion"]["verdict"]


def test_small_sample_is_not_reported_as_a_finding(db, experiment_id):
    """Ba alpha thì chưa nói được gì, dù chênh lệch trông có vẻ lớn."""
    _add_alpha(db, experiment_id, "rank(ts_mean(close, 20))", sharpe=0.4)
    _add_alpha(db, experiment_id, "rank(ts_mean(close, 60))", sharpe=1.2)
    _add_alpha(db, experiment_id, "rank(ts_mean(close, 120))", sharpe=2.1)

    conclusion = ExperimentReport(db).build(experiment_id)["conclusion"]
    assert conclusion["evidence"] == EVIDENCE_INSUFFICIENT
    assert "chưa đủ" in conclusion["summary"].lower()
    # Báo cáo phải nói rõ cần bao nhiêu, không chỉ nói là chưa đủ.
    assert conclusion["required_sample"] > conclusion["sample_size"]


def test_sample_size_alone_is_not_enough_to_conclude(db, experiment_id):
    """Mười alpha nhưng không quy về biến thể nào thì vẫn không kết luận được.

    Kết luận của một thí nghiệm là phép **so sánh** giữa các biến thể. Không có
    ít nhất hai biến thể có kết quả thì cỡ mẫu lớn tới đâu cũng vô nghĩa.
    """
    for index in range(10):
        _add_alpha(db, experiment_id, f"rank(ts_mean(close, {5 + index}))",
                   sharpe=1.0 + index * 0.1)
    conclusion = ExperimentReport(db).build(experiment_id)["conclusion"]
    assert conclusion["evidence"] == EVIDENCE_INSUFFICIENT
    assert "biến thể" in conclusion["summary"]


def test_two_measured_variants_allow_a_conclusion(db, store, experiment_id):
    """Đủ cỡ mẫu và có hai biến thể so được thì báo cáo mới kết luận."""
    from alphaforge.research.models import ExperimentVariant

    variants = {
        window: store.add_variant(ExperimentVariant(
            experiment_id=experiment_id, label=f"lookback={window}",
            expression=f"rank(ts_mean(close, {window}))",
        ))
        for window in (20, 60)
    }
    for window, variant_id in variants.items():
        for index in range(5):
            row_id = _add_alpha(
                db, experiment_id, f"rank(ts_mean(close, {window + index * 1000}))",
                sharpe=(1.8 if window == 60 else 0.4) + index * 0.01,
            )
            db.update_alpha(row_id, variant_id=variant_id)

    conclusion = ExperimentReport(db).build(experiment_id)["conclusion"]
    assert conclusion["evidence"] != EVIDENCE_INSUFFICIENT
    assert conclusion["sample_size"] == 10


# ----------------------------------------------------------------------
# Đếm và độ đa dạng
# ----------------------------------------------------------------------
def test_generated_and_validated_are_counted_separately(db, experiment_id):
    """Sinh ra khác với qua được bộ kiểm tra, và báo cáo phải phân biệt."""
    _add_alpha(db, experiment_id, "rank(ts_mean(close, 20))", sharpe=1.5)
    db.add_alphas(["rank(ts_mean(close, ))"], SETTINGS, experiment_id=experiment_id)
    bad = next(record.id for record in db.fetch_by_status(Status.PENDING))
    db.update_alpha(bad, status=Status.INVALID, validation_error="Cú pháp sai.")

    counts = ExperimentReport(db).build(experiment_id)["counts"]
    assert counts["generated"] == 2
    assert counts["validated"] == 1
    assert counts["invalid"] == 1


def test_structural_diversity_is_reported(db, experiment_id):
    """Mười alpha cùng một họ không đa dạng bằng mười alpha khác họ."""
    for window in (20, 60, 120):
        _add_alpha(db, experiment_id, f"rank(ts_mean(close, {window}))", sharpe=1.2)

    diversity = ExperimentReport(db).build(experiment_id)["structural_diversity"]
    assert diversity["alphas"] == 3
    assert diversity["distinct_expressions"] == 3
    # Đổi cửa sổ không tạo họ mới, nên chỉ có một họ.
    assert diversity["distinct_families"] == 1
    assert diversity["family_ratio"] < 1.0


def test_rejected_alphas_are_in_the_report_too(db, experiment_id):
    _add_alpha(db, experiment_id, "rank(ts_mean(close, 20))", sharpe=1.6)
    _add_alpha(db, experiment_id, "rank(ts_mean(close, 60))", sharpe=0.1,
               status=Status.REJECTED)

    counts = ExperimentReport(db).build(experiment_id)["counts"]
    assert counts["alpha_count"] == 2
    assert counts["rejected"] == 1
    assert 0.0 < ExperimentReport(db).build(experiment_id)["pass_rate"] < 1.0


# ----------------------------------------------------------------------
# Bản văn bản
# ----------------------------------------------------------------------
def test_text_report_names_the_experiment_and_the_variable(db, experiment_id):
    _add_alpha(db, experiment_id, "rank(ts_mean(close, 20))", sharpe=1.5)
    text = ExperimentReport(db).render_text(experiment_id)
    assert "Khao sat cua so" in text
    assert "lookback" in text
    assert "Cua so dai on dinh hon" in text


def test_text_report_works_with_no_alphas(db, experiment_id):
    text = ExperimentReport(db).render_text(experiment_id)
    assert "Khao sat cua so" in text
