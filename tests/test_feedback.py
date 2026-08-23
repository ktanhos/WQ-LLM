"""Đường phản hồi từ kết quả mô phỏng quay lại trí nhớ nghiên cứu.

Vòng nghiên cứu chỉ khép kín khi kết quả chạy được đọc ngược lại thành bằng
chứng cho lần chọn hướng tiếp theo. Chỗ dễ hỏng nhất của đường này là thiên
lệch sống sót: nếu chỉ alpha đạt được ghi nhận thì mọi hướng đều trông như tỷ
lệ thành công tuyệt đối, và hệ thống sẽ lặp lại đúng những gì đã thất bại.
"""

import json

import pytest

from alphaforge.research.memory import ResearchMemory
from alphaforge.research.models import Hypothesis, ResearchProject
from alphaforge.research.store import ResearchStore
from alphaforge.storage.db import EvaluationStatus, Status
from conftest import insert_historical

SETTINGS = {"region": "USA", "universe": "TOP3000", "delay": 1}


@pytest.fixture()
def experiment_id(db):
    store = ResearchStore(db)
    research_id = store.create_project(ResearchProject(name="Du an"))
    hypothesis_id = store.create_hypothesis(
        Hypothesis(research_id=research_id, statement="Gia thuyet")
    )
    from alphaforge.research.models import Experiment

    return store.create_experiment(
        Experiment(hypothesis_id=hypothesis_id, name="Thi nghiem",
                   base_expression="rank(ts_mean(close, 20))",
                   variable_changed="lookback", settings=dict(SETTINGS))
    )


def _queue(db, expression, experiment_id):
    db.add_alphas([expression], SETTINGS, experiment_id=experiment_id)
    return next(
        record.id for record in db.fetch_by_status(Status.PENDING)
        if record.expression == expression
    )


def _simulate(db, row_id, *, status, sharpe=None, evaluation=None):
    """Ghi kết quả như máy chủ sẽ ghi, không gọi mạng."""
    fields = {"status": status, "alpha_id": f"BRAIN-{row_id:05d}"}
    if sharpe is not None:
        fields["metrics"] = {"sharpe": sharpe, "fitness": 1.0, "turnover": 0.3}
    if evaluation is not None:
        fields["evaluation_status"] = evaluation
    db.update_alpha(row_id, **fields)


# ----------------------------------------------------------------------
# Kho rỗng
# ----------------------------------------------------------------------
def test_outcome_of_an_experiment_with_no_alphas(db, experiment_id):
    """Thí nghiệm chưa chạy vẫn phải trả về cấu trúc đầy đủ, chỉ toàn số không."""
    outcome = ResearchMemory(db).experiment_outcome(experiment_id)
    assert outcome["total"] == 0
    assert outcome["pass_rate"] == 0.0
    assert outcome["median_sharpe"] is None
    assert outcome["sample_size"] == 0


def test_outcome_of_a_missing_experiment_is_empty_not_an_error(db):
    outcome = ResearchMemory(db).experiment_outcome(999)
    assert outcome["total"] == 0


# ----------------------------------------------------------------------
# Thiên lệch sống sót
# ----------------------------------------------------------------------
def test_failed_alphas_are_kept_and_counted(db, experiment_id):
    """Alpha bị loại vẫn nằm trong kho và vẫn vào mẫu số của tỷ lệ đạt."""
    passed = _queue(db, "rank(ts_mean(close, 20))", experiment_id)
    _simulate(db, passed, status=Status.PASSED, sharpe=1.6)
    for index, window in enumerate((60, 120, 250)):
        row_id = _queue(db, f"rank(ts_mean(close, {window}))", experiment_id)
        _simulate(db, row_id, status=Status.REJECTED, sharpe=0.2)

    outcome = ResearchMemory(db).experiment_outcome(experiment_id)
    assert outcome["total"] == 4
    assert outcome["passed"] == 1
    assert outcome["rejected"] == 3
    # Mẫu số là mọi alpha, không chỉ alpha đạt.
    assert outcome["pass_rate"] == 0.25


def test_pass_rate_never_reaches_one_when_alphas_were_rejected(db, experiment_id):
    for window in (20, 60):
        _simulate(db, _queue(db, f"rank(ts_mean(close, {window}))", experiment_id),
                  status=Status.REJECTED, sharpe=0.1)
    _simulate(db, _queue(db, "rank(ts_mean(close, 120))", experiment_id),
              status=Status.PASSED, sharpe=1.5)
    assert ResearchMemory(db).experiment_outcome(experiment_id)["pass_rate"] < 1.0


def test_invalid_alphas_count_as_generated_but_not_as_simulated(db, experiment_id):
    """Biểu thức hỏng đã tốn công sinh nhưng chưa tốn lượt mô phỏng nào.

    Gộp hai con số này lại sẽ khiến tỷ lệ đạt bị pha loãng bởi lỗi cú pháp,
    một thứ chẳng nói gì về chất lượng của hướng nghiên cứu.
    """
    good = _queue(db, "rank(ts_mean(close, 20))", experiment_id)
    _simulate(db, good, status=Status.PASSED, sharpe=1.5)
    bad = _queue(db, "rank(ts_mean(close, 60))", experiment_id)
    db.update_alpha(bad, status=Status.INVALID, validation_error="Cú pháp sai.")

    outcome = ResearchMemory(db).experiment_outcome(experiment_id)
    assert outcome["total"] == 2
    assert outcome["invalid"] == 1
    assert outcome["simulated"] == 1


# ----------------------------------------------------------------------
# Chỉ số thiếu
# ----------------------------------------------------------------------
def test_alphas_without_metrics_do_not_break_the_median(db, experiment_id):
    """Alpha thiếu chỉ số bị bỏ khỏi phép tính trung vị, không bị coi là 0."""
    with_metrics = _queue(db, "rank(ts_mean(close, 20))", experiment_id)
    _simulate(db, with_metrics, status=Status.PASSED, sharpe=1.5)
    without = _queue(db, "rank(ts_mean(close, 60))", experiment_id)
    _simulate(db, without, status=Status.FAILED)

    outcome = ResearchMemory(db).experiment_outcome(experiment_id)
    assert outcome["total"] == 2
    assert outcome["sample_size"] == 1
    assert outcome["median_sharpe"] == 1.5


def test_a_run_where_every_alpha_lacks_metrics(db, experiment_id):
    for window in (20, 60):
        _simulate(db, _queue(db, f"rank(ts_mean(close, {window}))", experiment_id),
                  status=Status.FAILED)
    outcome = ResearchMemory(db).experiment_outcome(experiment_id)
    assert outcome["median_sharpe"] is None
    assert outcome["best_sharpe"] is None


# ----------------------------------------------------------------------
# Trạng thái lẫn lộn
# ----------------------------------------------------------------------
def test_mixed_statuses_are_each_counted_once(db, experiment_id):
    plan = [
        ("rank(ts_mean(close, 20))", Status.PASSED, 1.6, None),
        ("rank(ts_mean(close, 60))", Status.REJECTED, 0.3, None),
        ("rank(ts_mean(close, 120))", Status.FAILED, None, None),
        ("rank(ts_mean(close, 250))", Status.CANDIDATE, 1.9,
         EvaluationStatus.CANDIDATE),
    ]
    for expression, status, sharpe, evaluation in plan:
        _simulate(db, _queue(db, expression, experiment_id),
                  status=status, sharpe=sharpe, evaluation=evaluation)
    _queue(db, "rank(ts_mean(volume, 20))", experiment_id)  # còn trong hàng đợi

    outcome = ResearchMemory(db).experiment_outcome(experiment_id)
    assert outcome["total"] == 5
    # PASSED và CANDIDATE đều tính là đạt; alpha còn chờ không tính vào đâu cả.
    assert outcome["passed"] == 2
    assert outcome["rejected"] == 1
    assert outcome["failed"] == 1
    assert outcome["candidates"] == 1
    assert outcome["simulated"] == 4


# ----------------------------------------------------------------------
# Phản hồi tới trí nhớ dùng cho lần chọn hướng sau
# ----------------------------------------------------------------------
def test_simulation_results_become_evidence_in_the_memory(db, experiment_id):
    """Alpha vừa chạy phải xuất hiện trong độ phủ, không chỉ trong bảng alphas.

    Không có bước này thì hệ thống quên ngay thứ nó vừa thử, và lần chọn hướng
    sau sẽ đề xuất lại đúng chỗ đó.
    """
    _simulate(db, _queue(db, "zscore(ts_std(volume, 60))", experiment_id),
              status=Status.PASSED, sharpe=1.7)

    coverage = ResearchMemory(db).coverage()
    assert "volume" in coverage["field"]
    assert "ts_std" in coverage["operator"]
    assert "60" in coverage["lookback"] or 60 in coverage["lookback"]


def test_history_and_fresh_results_are_both_evidence(db, experiment_id):
    insert_historical(db, "rank(ts_mean(close, 20))", alpha_id="OLD1")
    _simulate(db, _queue(db, "zscore(ts_std(volume, 60))", experiment_id),
              status=Status.PASSED, sharpe=1.7)

    statistics = ResearchMemory(db).statistics()
    assert statistics["counts"]["tested"] == 2
    assert set(ResearchMemory(db).coverage()["field"]) == {"close", "volume"}


def test_candidates_are_listed_for_human_review(db, experiment_id):
    """Ứng viên là bậc cuối cùng máy làm được; nộp hay không là việc của người."""
    row_id = _queue(db, "rank(ts_mean(close, 20))", experiment_id)
    _simulate(db, row_id, status=Status.CANDIDATE, sharpe=1.8,
              evaluation=EvaluationStatus.CANDIDATE)

    candidates = ResearchMemory(db).candidates()
    assert len(candidates) == 1
    assert candidates[0]["status"] == Status.CANDIDATE
    # Không có gì tự chuyển sang đã nộp.
    assert db.counts_by_status()[Status.SUBMITTED] == 0
