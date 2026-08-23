"""Kiểm thử kho dữ liệu, hàng đợi chạy và trích tương quan.

Máy chủ BRAIN được thay bằng bản giả lập nên toàn bộ kiểm thử chạy ngoại tuyến.
"""

import threading

import pytest

from alphaforge.brain.client import SimulationResult, extract_metrics
from alphaforge.brain.errors import SimulationError, TransientError
from alphaforge.pipeline.correlation import max_correlation
from alphaforge.pipeline.runner import SimulationRunner
from alphaforge.pipeline.scorer import Scorer
from alphaforge.storage.db import Database, Status, expression_hash

SETTINGS = {"region": "USA", "universe": "TOP3000", "delay": 1}

SCORING = {
    "min_sharpe": 1.0,
    "min_fitness": 0.8,
    "max_turnover": 0.7,
    "weights": {"sharpe": 0.5, "fitness": 0.3, "margin": 0.1, "turnover_penalty": 0.1},
}


@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "test.sqlite3")


class FakeClient:
    """Bản giả lập máy chủ. Biểu thức chứa chuỗi bad thì lỗi, chứa flaky thì lỗi tạm thời."""

    def __init__(self):
        self.calls = 0
        self.lock = threading.Lock()
        self.flaky_seen = set()

    def simulate(self, expression, settings):
        with self.lock:
            self.calls += 1
        if "bad" in expression:
            raise SimulationError("Biểu thức không hợp lệ.", detail="syntax")
        if "flaky" in expression and expression not in self.flaky_seen:
            self.flaky_seen.add(expression)
            raise TransientError("Máy chủ bận.")
        return SimulationResult(
            alpha_id=f"A{abs(hash(expression)) % 100000}",
            expression=expression,
            metrics={
                "sharpe": 1.6,
                "fitness": 1.1,
                "turnover": 0.2,
                "marginBps": 9.0,
                "checkFailures": [],
            },
        )


def test_add_alphas_skips_duplicates(db):
    added = db.add_alphas(["rank(close)", "rank(close)", "rank(open)"], SETTINGS)
    assert added == 2
    assert db.add_alphas(["rank(close)"], SETTINGS) == 0


def test_same_expression_different_settings_is_not_a_duplicate(db):
    db.add_alphas(["rank(close)"], SETTINGS)
    other = dict(SETTINGS, region="EUR")
    assert db.add_alphas(["rank(close)"], other) == 1


def test_expression_hash_ignores_whitespace():
    assert expression_hash("rank( close )", SETTINGS) == expression_hash(
        "rank( close )", SETTINGS
    )
    assert expression_hash("rank(close)", SETTINGS) != expression_hash(
        "rank(open)", SETTINGS
    )


def test_claim_pending_marks_records_running(db):
    db.add_alphas(["rank(close)", "rank(open)"], SETTINGS)
    claimed = db.claim_pending(1)
    assert len(claimed) == 1
    assert db.counts_by_status()[Status.RUNNING] == 1
    assert db.counts_by_status()[Status.PENDING] == 1


def test_claim_pending_does_not_hand_out_same_record_twice(db):
    db.add_alphas([f"rank(f{i})" for i in range(6)], SETTINGS)
    seen = []

    def worker():
        for _ in range(3):
            seen.extend(record.id for record in db.claim_pending(1))

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(seen) == len(set(seen))


def test_runner_scores_and_persists(db):
    db.add_alphas(["rank(close)", "rank(open)"], SETTINGS)
    runner = SimulationRunner(FakeClient(), db, Scorer(SCORING), concurrency=2)
    stats = runner.run()
    assert stats["simulated"] == 2
    assert stats["passed"] == 2
    counts = db.counts_by_status()
    assert counts[Status.PASSED] == 2
    assert counts[Status.PENDING] == 0
    assert db.top_alphas(5)[0]["score"] > 0


def test_runner_marks_invalid_expression_failed_without_retry(db):
    db.add_alphas(["bad_expression(close)"], SETTINGS)
    client = FakeClient()
    runner = SimulationRunner(client, db, Scorer(SCORING), concurrency=1)
    runner.run()
    assert db.counts_by_status()[Status.FAILED] == 1
    assert client.calls == 1


def test_runner_requeues_transient_error(db):
    db.add_alphas(["flaky(close)"], SETTINGS)
    client = FakeClient()
    runner = SimulationRunner(client, db, Scorer(SCORING), concurrency=1, max_attempts=3)
    runner.run(limit=1)
    assert db.counts_by_status()[Status.PENDING] == 1
    runner.run(limit=1)
    assert db.counts_by_status()[Status.PASSED] == 1


def test_runner_gives_up_after_max_attempts(db):
    db.add_alphas(["flaky(close)"], SETTINGS)
    client = FakeClient()
    runner = SimulationRunner(client, db, Scorer(SCORING), concurrency=1, max_attempts=1)
    runner.run(limit=1)
    assert db.counts_by_status()[Status.FAILED] == 1


def test_reset_stuck_returns_records_to_queue(db):
    db.add_alphas(["rank(close)"], SETTINGS)
    db.claim_pending(1)
    assert db.reset_stuck() == 1
    assert db.counts_by_status()[Status.PENDING] == 1


def test_extract_metrics_converts_margin_to_basis_points():
    metrics = extract_metrics(
        {"is": {"sharpe": 1.5, "margin": 0.0012, "checks": [
            {"name": "LOW_SHARPE", "result": "PASS"},
            {"name": "CONCENTRATED_WEIGHT", "result": "FAIL"},
        ]}}
    )
    assert metrics["marginBps"] == pytest.approx(12.0)
    assert metrics["checkFailures"] == ["CONCENTRATED_WEIGHT"]


def test_max_correlation_reads_column_by_name():
    payload = {
        "schema": {"properties": [{"name": "alphas"}, {"name": "correlation"}]},
        "records": [["A1", 0.31], ["A2", -0.82], ["A3", 0.44]],
    }
    assert max_correlation(payload) == pytest.approx(-0.82)


def test_max_correlation_handles_dict_records_and_empty_payload():
    assert max_correlation({"records": [{"correlation": 0.9}]}) == pytest.approx(0.9)
    assert max_correlation({"records": []}) is None
