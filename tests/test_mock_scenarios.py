"""Kịch bản giả lập máy chủ cho bộ quét lịch sử và hàng đợi mô phỏng.

Toàn bộ tệp này chạy ngoại tuyến. Không có thông tin đăng nhập, không có lệnh
gọi BRAIN thật, không có mô phỏng thật và không có thao tác nộp alpha nào.
"""

import threading

import pytest

from alphaforge.brain.errors import (
    AuthenticationError,
    BrainError,
    RateLimitError,
    SimulationError,
    TransientError,
)
from alphaforge.brain.client import SimulationResult
from alphaforge.history.scanner import fetch_submitted_alphas, submitted_date
from alphaforge.pipeline.runner import SimulationRunner
from alphaforge.pipeline.scorer import Scorer
from alphaforge.storage.db import Status
from conftest import FakeBrainClient, FakeResponse, make_alpha, page

SETTINGS = {"region": "USA", "universe": "TOP3000", "delay": 1}
SCORING = {"min_sharpe": 1.0, "min_fitness": 0.8, "max_turnover": 0.7}


# ======================================================================
# Mục 7: kịch bản phản hồi của điểm cuối lịch sử
# ======================================================================
def test_single_full_page_with_next_null():
    client = FakeBrainClient([page([make_alpha(f"A{i}") for i in range(100)], next_url=None)])
    rows, diagnostics = fetch_submitted_alphas(client, "2026-08-01", "2026-08-31")
    assert len(rows) == 100
    assert diagnostics["stopped_because"] == "no_next_page"
    assert diagnostics["pages"] == 1


def test_three_pages_followed_via_next():
    client = FakeBrainClient([
        page([make_alpha(f"P1-{i}") for i in range(5)], next_url="https://api.test/p2"),
        page([make_alpha(f"P2-{i}") for i in range(5)], next_url="https://api.test/p3"),
        page([make_alpha(f"P3-{i}") for i in range(5)], next_url=None),
    ])
    rows, diagnostics = fetch_submitted_alphas(client, "2026-08-01", "2026-08-31")
    assert len(rows) == 15
    assert diagnostics["pages"] == 3


def test_next_absent_from_payload_stops_cleanly():
    """Máy chủ có thể không trả trường next, không được coi là lỗi."""
    client = FakeBrainClient([{"count": 1, "results": [make_alpha("A1")]}])
    rows, diagnostics = fetch_submitted_alphas(client, "2026-08-01", "2026-08-31")
    assert len(rows) == 1
    assert diagnostics["stopped_because"] == "no_next_page"


@pytest.mark.parametrize("missing", ["settings", "regular", "is"])
def test_record_missing_a_block_is_kept(missing):
    """Thiếu khối dữ liệu không phải lý do vứt bỏ một alpha."""
    alpha = make_alpha("SPARSE")
    del alpha[missing]
    client = FakeBrainClient([page([alpha])])
    rows, diagnostics = fetch_submitted_alphas(client, "2026-08-01", "2026-08-31")
    assert len(rows) == 1
    assert rows[0]["alpha_id"] == "SPARSE"
    assert diagnostics["malformed"] == 0


def test_record_missing_all_blocks_is_still_kept():
    alpha = {"id": "BARE", "dateSubmitted": "2026-08-15T00:00:00Z"}
    client = FakeBrainClient([page([alpha])])
    rows, _ = fetch_submitted_alphas(client, "2026-08-01", "2026-08-31")
    assert rows[0]["alpha_id"] == "BARE"
    assert rows[0]["expression"] == ""
    assert rows[0]["sharpe"] is None


def test_record_with_wrong_types_does_not_crash_scan():
    alpha = make_alpha("WEIRD")
    alpha["settings"] = {"delay": "khong-phai-so", "region": 12345}
    alpha["is"] = {"sharpe": "khong-phai-so"}
    client = FakeBrainClient([page([alpha, make_alpha("GOOD")])])
    rows, _ = fetch_submitted_alphas(client, "2026-08-01", "2026-08-31")
    assert {row["alpha_id"] for row in rows} == {"WEIRD", "GOOD"}
    weird = next(row for row in rows if row["alpha_id"] == "WEIRD")
    assert weird["delay"] is None
    assert weird["sharpe"] is None


def test_duplicate_alpha_ids_across_pages_are_merged():
    client = FakeBrainClient([
        page([make_alpha("DUP", sharpe=1.0)], next_url="https://api.test/p2"),
        page([make_alpha("DUP", sharpe=9.9), make_alpha("NEW")], next_url=None),
    ])
    rows, diagnostics = fetch_submitted_alphas(client, "2026-08-01", "2026-08-31")
    assert len(rows) == 2
    assert next(r for r in rows if r["alpha_id"] == "DUP")["sharpe"] == 9.9
    assert diagnostics["duplicates"] == 1


@pytest.mark.parametrize(
    "error",
    [
        RateLimitError("HTTP 429", 1.0),
        AuthenticationError("HTTP 401"),
        TransientError("HTTP 500"),
        TransientError("timeout"),
    ],
)
def test_http_errors_surface_from_client_layer(error):
    """Scanner không nuốt lỗi. Việc thử lại là trách nhiệm của BrainClient."""
    client = FakeBrainClient([])
    client.raise_on_call[0] = error
    with pytest.raises(type(error)):
        fetch_submitted_alphas(client, "2026-08-01", "2026-08-31")


def test_error_on_second_page_does_not_lose_first_page_silently():
    """Lỗi giữa chừng phải nổi lên, không được trả về dữ liệu thiếu mà im lặng."""
    client = FakeBrainClient([page([make_alpha("A1")], next_url="https://api.test/p2")])
    client.raise_on_call[1] = TransientError("HTTP 500")
    with pytest.raises(TransientError):
        fetch_submitted_alphas(client, "2026-08-01", "2026-08-31")


def test_html_error_body_becomes_brain_error():
    client = FakeBrainClient([FakeResponse(payload=None, text="<html>502 Bad Gateway</html>")])
    with pytest.raises(BrainError):
        fetch_submitted_alphas(client, "2026-08-01", "2026-08-31")


# ======================================================================
# Mục 8: lọc theo ngày với nhiều múi giờ
# ======================================================================
@pytest.mark.parametrize(
    "timestamp,expected",
    [
        ("2026-08-20T12:00:00Z", "2026-08-20"),
        ("2026-08-20T12:00:00+00:00", "2026-08-20"),
        # 23:30 tại UTC-05:00 là 04:30 ngày hôm sau theo UTC.
        ("2026-08-20T23:30:00-05:00", "2026-08-21"),
        # 01:00 tại UTC+09:00 là 16:00 ngày hôm trước theo UTC.
        ("2026-08-21T01:00:00+09:00", "2026-08-20"),
        ("2026-08-20", "2026-08-20"),
        ("2026-08-20T12:00:00.123456Z", "2026-08-20"),
    ],
)
def test_timezone_conversion_to_utc_date(timestamp, expected):
    assert submitted_date(timestamp) == expected


def test_boundary_dates_are_inclusive():
    """Bản ghi rơi đúng vào mốc đầu và mốc cuối đều phải được giữ."""
    client = FakeBrainClient([
        page([
            make_alpha("END", submitted="2026-08-31T23:59:59Z"),
            make_alpha("MID", submitted="2026-08-15T00:00:00Z"),
            make_alpha("START", submitted="2026-08-01T00:00:00Z"),
        ], next_url=None),
    ])
    rows, _ = fetch_submitted_alphas(client, "2026-08-01", "2026-08-31")
    assert {row["alpha_id"] for row in rows} == {"START", "MID", "END"}


def test_record_just_outside_each_boundary_is_excluded():
    client = FakeBrainClient([
        page([
            make_alpha("TOO_NEW", submitted="2026-09-01T00:00:00Z"),
            make_alpha("INSIDE", submitted="2026-08-15T00:00:00Z"),
            make_alpha("TOO_OLD", submitted="2026-07-31T23:59:59Z"),
        ], next_url=None),
    ])
    rows, _ = fetch_submitted_alphas(client, "2026-08-01", "2026-08-31")
    assert {row["alpha_id"] for row in rows} == {"INSIDE"}


def test_timezone_boundary_moves_a_record_into_range():
    """Bản ghi trông như ngoài khoảng theo giờ địa phương nhưng trong khoảng theo UTC."""
    client = FakeBrainClient([
        page([make_alpha("SHIFTED", submitted="2026-07-31T20:00:00-05:00")], next_url=None),
    ])
    rows, _ = fetch_submitted_alphas(client, "2026-08-01", "2026-08-31")
    assert [row["alpha_id"] for row in rows] == ["SHIFTED"]
    assert rows[0]["submitted"] == "2026-08-01"


# ======================================================================
# Mục 14: hàng đợi mô phỏng, toàn bộ bằng giả lập
# ======================================================================
class ScriptedClient:
    """Máy chủ giả lập trả kết quả theo kịch bản định trước cho từng lượt gọi."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.seen = []
        self.lock = threading.Lock()

    def simulate(self, expression, settings):
        with self.lock:
            index = self.calls
            self.calls += 1
            self.seen.append(expression)
        outcome = self.script[index] if index < len(self.script) else "ok"
        if isinstance(outcome, Exception):
            raise outcome
        return SimulationResult(
            alpha_id=f"A{index}",
            expression=expression,
            metrics={"sharpe": 1.6, "fitness": 1.1, "turnover": 0.2, "checkFailures": []},
        )


def test_invalid_expression_fails_without_retry(db):
    """Lỗi thuộc về biểu thức thì thử lại cũng vô ích, không được tốn hạn mức."""
    db.add_alphas(["bieu_thuc_sai(close)"], SETTINGS)
    client = ScriptedClient([SimulationError("Cú pháp sai.", detail="syntax")])
    SimulationRunner(client, db, Scorer(SCORING), concurrency=1, max_attempts=5).run()
    assert db.counts_by_status()[Status.FAILED] == 1
    assert client.calls == 1


def test_auth_error_requeues_rather_than_failing(db):
    db.add_alphas(["rank(close)"], SETTINGS)
    client = ScriptedClient([AuthenticationError("HTTP 401")])
    SimulationRunner(client, db, Scorer(SCORING), concurrency=1, max_attempts=3).run(limit=1)
    assert db.counts_by_status()[Status.PENDING] == 1


def test_server_error_then_success_completes(db):
    db.add_alphas(["rank(close)"], SETTINGS)
    client = ScriptedClient([TransientError("HTTP 500")])
    runner = SimulationRunner(client, db, Scorer(SCORING), concurrency=1, max_attempts=3)
    runner.run(limit=1)
    assert db.counts_by_status()[Status.PENDING] == 1
    runner.run(limit=1)
    assert db.counts_by_status()[Status.PASSED] == 1


def test_timeout_is_treated_as_transient(db):
    db.add_alphas(["rank(close)"], SETTINGS)
    client = ScriptedClient([TransientError("Quá thời gian chờ 900 giây.")])
    SimulationRunner(client, db, Scorer(SCORING), concurrency=1, max_attempts=3).run(limit=1)
    assert db.counts_by_status()[Status.PENDING] == 1
    assert db.counts_by_status()[Status.FAILED] == 0


def test_retry_is_bounded_and_never_infinite(db):
    """Một biểu thức luôn lỗi tạm thời vẫn phải kết thúc sau max_attempts."""
    db.add_alphas(["rank(close)"], SETTINGS)
    client = ScriptedClient([TransientError("HTTP 500")] * 50)
    runner = SimulationRunner(client, db, Scorer(SCORING), concurrency=1, max_attempts=3)
    for _ in range(10):
        runner.run(limit=1)
    assert db.counts_by_status()[Status.FAILED] == 1
    # Đúng ba lượt gọi máy chủ, không nhiều hơn.
    assert client.calls == 3


def test_no_duplicate_jobs_under_concurrency(db):
    """Không biểu thức nào được gửi hai lần trong cùng một lượt chạy."""
    db.add_alphas([f"rank(f{i})" for i in range(30)], SETTINGS)
    client = ScriptedClient([])
    SimulationRunner(client, db, Scorer(SCORING), concurrency=6).run()
    assert len(client.seen) == len(set(client.seen)) == 30


def test_concurrency_never_exceeds_configured_ceiling(db):
    """Số mô phỏng chạy song song không được vượt trần người dùng đặt."""
    db.add_alphas([f"rank(f{i})" for i in range(40)], SETTINGS)
    peak = 0
    active = 0
    lock = threading.Lock()

    class Counting(ScriptedClient):
        def simulate(self, expression, settings):
            nonlocal peak, active
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                return super().simulate(expression, settings)
            finally:
                with lock:
                    active -= 1

    SimulationRunner(Counting([]), db, Scorer(SCORING), concurrency=4).run()
    assert peak <= 4


def test_resume_after_interruption_leaves_no_record_behind(db):
    """Tiến trình bị ngắt để lại bản ghi RUNNING; reset đưa chúng về hàng đợi."""
    db.add_alphas([f"rank(f{i})" for i in range(6)], SETTINGS)
    db.claim_pending(3)  # giả lập ba bản ghi đang chạy khi tiến trình chết
    assert db.counts_by_status()[Status.RUNNING] == 3

    assert db.reset_stuck() == 3
    SimulationRunner(ScriptedClient([]), db, Scorer(SCORING), concurrency=2).run()

    counts = db.counts_by_status()
    assert counts[Status.PENDING] == 0
    assert counts[Status.RUNNING] == 0
    assert counts[Status.PASSED] == 6


def test_quota_is_protected_by_limit_argument(db):
    """Tham số limit chặn không cho một lượt chạy tiêu hết hạn mức."""
    db.add_alphas([f"rank(f{i})" for i in range(50)], SETTINGS)
    client = ScriptedClient([])
    SimulationRunner(client, db, Scorer(SCORING), concurrency=2).run(limit=5)
    assert client.calls <= 5
    assert db.counts_by_status()[Status.PENDING] >= 45


def test_stop_returns_in_flight_record_to_queue(db):
    db.add_alphas(["rank(close)"], SETTINGS)
    runner = SimulationRunner(ScriptedClient([]), db, Scorer(SCORING), concurrency=1)
    runner.stop()
    runner.run()
    assert db.counts_by_status()[Status.PENDING] == 1


# ======================================================================
# Mục 15: an toàn nộp alpha
# ======================================================================
def test_runner_never_marks_an_alpha_submitted(db):
    """Mô phỏng đạt ngưỡng chỉ được chuyển sang PASSED, không phải SUBMITTED."""
    db.add_alphas([f"rank(f{i})" for i in range(5)], SETTINGS)
    SimulationRunner(ScriptedClient([]), db, Scorer(SCORING), concurrency=2).run()
    counts = db.counts_by_status()
    assert counts[Status.PASSED] == 5
    assert counts[Status.SUBMITTED] == 0
