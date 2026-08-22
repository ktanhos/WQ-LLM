"""Kiểm thử cơ chế tự giảm tốc của hàng đợi mô phỏng."""

import threading

from alphaforge.brain.client import SimulationResult
from alphaforge.brain.errors import RateLimitError
from alphaforge.pipeline.runner import AdaptiveLimiter, SimulationRunner
from alphaforge.pipeline.scorer import Scorer
from alphaforge.storage.db import Status

SETTINGS = {"region": "USA", "universe": "TOP3000", "delay": 1}
SCORING = {"min_sharpe": 1.0, "min_fitness": 0.8, "max_turnover": 0.7}


class RateLimitedClient:
    """Trả lỗi vượt hạn mức ở `failures` lượt đầu, sau đó chạy bình thường."""

    def __init__(self, failures: int = 1, retry_after: float = 0.01):
        self.failures = failures
        self.retry_after = retry_after
        self.calls = 0
        self.lock = threading.Lock()

    def simulate(self, expression, settings):
        with self.lock:
            self.calls += 1
            should_fail = self.calls <= self.failures
        if should_fail:
            raise RateLimitError("Vuot han muc.", self.retry_after)
        return SimulationResult(
            alpha_id=f"A{abs(hash(expression)) % 10000}",
            expression=expression,
            metrics={"sharpe": 1.6, "fitness": 1.1, "turnover": 0.2, "checkFailures": []},
        )


# ----------------------------------------------------------------------
# Bộ giảm tốc
# ----------------------------------------------------------------------
def test_limiter_starts_at_configured_maximum():
    assert AdaptiveLimiter(4).slots == 4


def test_rate_limit_halves_available_slots():
    limiter = AdaptiveLimiter(8)
    limiter.on_rate_limit(0.01)
    assert limiter.slots == 4
    limiter.on_rate_limit(0.01)
    assert limiter.slots == 2


def test_slots_never_drop_below_one():
    limiter = AdaptiveLimiter(2)
    for _ in range(10):
        limiter.on_rate_limit(0.01)
    assert limiter.slots == 1


def test_success_streak_restores_capacity_gradually():
    limiter = AdaptiveLimiter(4)
    limiter.on_rate_limit(0.01)
    assert limiter.slots == 2
    for _ in range(AdaptiveLimiter.RECOVERY_STREAK):
        limiter.on_success()
    assert limiter.slots == 3


def test_recovery_never_exceeds_configured_maximum():
    limiter = AdaptiveLimiter(2)
    for _ in range(AdaptiveLimiter.RECOVERY_STREAK * 10):
        limiter.on_success()
    assert limiter.slots == 2


def test_wait_returns_immediately_when_not_throttled():
    limiter = AdaptiveLimiter(2)
    limiter.wait()  # không được treo


def test_limiter_is_thread_safe():
    limiter = AdaptiveLimiter(16)

    def worker():
        for _ in range(50):
            limiter.on_success()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert 1 <= limiter.slots <= 16


# ----------------------------------------------------------------------
# Tích hợp với hàng đợi
# ----------------------------------------------------------------------
def test_runner_slows_down_after_rate_limit_then_completes(db):
    db.add_alphas([f"rank(f{index})" for index in range(6)], SETTINGS)
    client = RateLimitedClient(failures=2)
    runner = SimulationRunner(client, db, Scorer(SCORING), concurrency=4, max_attempts=5)

    runner.run()
    # Mức đồng thời đã bị hạ so với mức người dùng đặt.
    assert runner.limiter.slots < runner.concurrency
    # Bản ghi bị từ chối được trả về hàng đợi rồi chạy lại cho tới khi xong.
    runner.run()
    counts = db.counts_by_status()
    assert counts[Status.PENDING] == 0
    assert counts[Status.PASSED] == 6


def test_rate_limited_record_is_requeued_not_failed(db):
    db.add_alphas(["rank(close)"], SETTINGS)
    client = RateLimitedClient(failures=1)
    runner = SimulationRunner(client, db, Scorer(SCORING), concurrency=1, max_attempts=3)
    runner.run(limit=1)
    assert db.counts_by_status()[Status.PENDING] == 1
    assert db.counts_by_status()[Status.FAILED] == 0


def test_rate_limit_still_respects_max_attempts(db):
    """Giảm tốc không được biến thành vòng thử lại vô hạn."""
    db.add_alphas(["rank(close)"], SETTINGS)
    client = RateLimitedClient(failures=99)
    runner = SimulationRunner(client, db, Scorer(SCORING), concurrency=1, max_attempts=1)
    runner.run(limit=1)
    assert db.counts_by_status()[Status.FAILED] == 1
