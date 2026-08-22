"""Kiểm thử bộ quét lịch sử alpha.

Máy chủ BRAIN được thay bằng bản giả lập trong conftest, không có lệnh gọi mạng
thật nào.
"""

import pytest

from alphaforge.brain.errors import BrainError, RateLimitError, TransientError
from alphaforge.history.scanner import (
    HistoricalAlphaScanner,
    fetch_submitted_alphas,
    parse_timestamp,
    submitted_date,
)
from conftest import FakeBrainClient, FakeResponse, make_alpha, page


# ----------------------------------------------------------------------
# Xử lý ngày tháng
# ----------------------------------------------------------------------
def test_timestamp_with_offset_is_converted_to_utc_date():
    """23:30 giờ UTC-05:00 là ngày hôm sau theo UTC."""
    assert submitted_date("2026-08-20T23:30:00-05:00") == "2026-08-21"


def test_zulu_suffix_is_parsed():
    assert submitted_date("2026-08-20T12:00:00Z") == "2026-08-20"


def test_date_only_value_is_parsed():
    assert submitted_date("2026-08-20") == "2026-08-20"


def test_unparseable_timestamp_returns_none_instead_of_raising():
    assert submitted_date("khong-phai-ngay") is None
    assert parse_timestamp(None) is None


# ----------------------------------------------------------------------
# Phân trang
# ----------------------------------------------------------------------
def test_pagination_follows_next_until_exhausted():
    client = FakeBrainClient([
        page([make_alpha("A1"), make_alpha("A2")], next_url="https://api.test/next1"),
        page([make_alpha("A3")], next_url=None),
    ])
    rows, diagnostics = fetch_submitted_alphas(client, "2026-08-01", "2026-08-31")
    assert {row["alpha_id"] for row in rows} == {"A1", "A2", "A3"}
    assert diagnostics["pages"] == 2
    assert diagnostics["stopped_because"] == "no_next_page"


def test_query_uses_unencoded_status_filter_and_newest_first_order():
    """Bộ lọc status!=UNSUBMITTED phải giữ nguyên dấu, nếu không máy chủ bỏ qua."""
    client = FakeBrainClient([page([make_alpha("A1")])])
    fetch_submitted_alphas(client, "2026-08-01", "2026-08-31")
    url = client.calls[0]
    assert "status!=UNSUBMITTED" in url
    assert "order=-dateSubmitted" in url
    assert "hidden=false" in url


def test_include_unsubmitted_drops_the_status_filter():
    client = FakeBrainClient([page([make_alpha("A1")])])
    fetch_submitted_alphas(client, "2026-08-01", "2026-08-31", include_unsubmitted=True)
    assert "status!=UNSUBMITTED" not in client.calls[0]


def test_page_limit_is_clamped_to_server_maximum():
    client = FakeBrainClient([page([make_alpha("A1")])])
    fetch_submitted_alphas(client, "2026-08-01", "2026-08-31", limit=5000)
    assert "limit=100" in client.calls[0]


def test_pagination_stops_when_server_ignores_offset():
    """Máy chủ trả mãi cùng một trang thì phải dừng, không lặp vô hạn."""
    same = page([make_alpha("A1")], next_url="https://api.test/next")
    client = FakeBrainClient([same] * 50)
    rows, diagnostics = fetch_submitted_alphas(client, "2026-08-01", "2026-08-31")
    assert diagnostics["stopped_because"] == "no_new_records"
    assert len(rows) == 1


def test_empty_first_page_stops_immediately():
    client = FakeBrainClient([page([])])
    rows, diagnostics = fetch_submitted_alphas(client, "2026-08-01", "2026-08-31")
    assert rows == []
    assert diagnostics["stopped_because"] == "empty_page"


def test_max_records_caps_collection():
    client = FakeBrainClient([
        page([make_alpha(f"A{i}") for i in range(10)], next_url="https://api.test/n"),
        page([make_alpha(f"B{i}") for i in range(10)], next_url=None),
    ])
    rows, diagnostics = fetch_submitted_alphas(
        client, "2026-08-01", "2026-08-31", max_records=5
    )
    assert len(rows) == 5
    assert diagnostics["stopped_because"] == "max_records"


# ----------------------------------------------------------------------
# Lọc theo khoảng ngày
# ----------------------------------------------------------------------
def test_records_older_than_start_date_stop_pagination():
    client = FakeBrainClient([
        page(
            [
                make_alpha("NEW", submitted="2026-08-20T00:00:00Z"),
                make_alpha("OLD", submitted="2026-07-01T00:00:00Z"),
            ],
            next_url="https://api.test/next",
        ),
        page([make_alpha("OLDER", submitted="2026-06-01T00:00:00Z")]),
    ])
    rows, diagnostics = fetch_submitted_alphas(client, "2026-08-01", "2026-08-31")
    assert {row["alpha_id"] for row in rows} == {"NEW"}
    assert diagnostics["stopped_because"] == "reached_start_date"
    # Chỉ gọi một trang: không tải thêm khi đã đi qua mốc bắt đầu.
    assert len(client.calls) == 1


def test_records_newer_than_end_date_are_skipped_but_scan_continues():
    client = FakeBrainClient([
        page(
            [
                make_alpha("FUTURE", submitted="2026-09-15T00:00:00Z"),
                make_alpha("INSIDE", submitted="2026-08-15T00:00:00Z"),
            ],
            next_url=None,
        ),
    ])
    rows, _ = fetch_submitted_alphas(client, "2026-08-01", "2026-08-31")
    assert {row["alpha_id"] for row in rows} == {"INSIDE"}


def test_start_date_after_end_date_is_rejected():
    client = FakeBrainClient([])
    with pytest.raises(ValueError):
        fetch_submitted_alphas(client, "2026-09-01", "2026-08-01")


def test_default_window_is_used_when_dates_are_omitted():
    client = FakeBrainClient([page([])])
    _, diagnostics = fetch_submitted_alphas(client)
    assert diagnostics["start_date"] < diagnostics["end_date"]


# ----------------------------------------------------------------------
# Không được làm mất alpha
# ----------------------------------------------------------------------
def test_record_without_submission_date_is_kept_not_dropped():
    """Thiếu ngày nộp không đủ căn cứ để loại một alpha khỏi nghiên cứu."""
    client = FakeBrainClient([
        page([
            make_alpha("HASDATE", submitted="2026-08-15T00:00:00Z"),
            make_alpha("NODATE", submitted=None),
        ]),
    ])
    rows, diagnostics = fetch_submitted_alphas(client, "2026-08-01", "2026-08-31")
    assert {row["alpha_id"] for row in rows} == {"HASDATE", "NODATE"}
    assert diagnostics["missing_date"] == 1


def test_malformed_record_does_not_abort_the_whole_scan():
    client = FakeBrainClient([
        page([make_alpha("GOOD1")] + ["day-la-chuoi-khong-phai-tu-dien"] + [make_alpha("GOOD2")]),
    ])
    rows, diagnostics = fetch_submitted_alphas(client, "2026-08-01", "2026-08-31")
    assert {row["alpha_id"] for row in rows} == {"GOOD1", "GOOD2"}
    assert diagnostics["malformed"] == 1


def test_record_missing_metrics_block_is_kept_with_none_values():
    alpha = make_alpha("SPARSE")
    del alpha["is"]
    del alpha["settings"]
    client = FakeBrainClient([page([alpha])])
    rows, _ = fetch_submitted_alphas(client, "2026-08-01", "2026-08-31")
    assert rows[0]["alpha_id"] == "SPARSE"
    assert rows[0]["sharpe"] is None
    assert rows[0]["region"] is None


def test_duplicate_alpha_ids_are_deduplicated_keeping_latest():
    client = FakeBrainClient([
        page(
            [make_alpha("A1", sharpe=1.0), make_alpha("A1", sharpe=2.0)],
            next_url=None,
        ),
    ])
    rows, diagnostics = fetch_submitted_alphas(client, "2026-08-01", "2026-08-31")
    assert len(rows) == 1
    assert rows[0]["sharpe"] == 2.0
    assert diagnostics["duplicates"] == 1


def test_record_without_alpha_id_is_counted_and_skipped():
    client = FakeBrainClient([page([make_alpha(""), make_alpha("GOOD")])])
    rows, diagnostics = fetch_submitted_alphas(client, "2026-08-01", "2026-08-31")
    assert {row["alpha_id"] for row in rows} == {"GOOD"}
    assert diagnostics["missing_alpha_id"] == 1


def test_expression_read_from_regular_string_form():
    alpha = make_alpha("A1")
    alpha["regular"] = "rank(close)"
    client = FakeBrainClient([page([alpha])])
    rows, _ = fetch_submitted_alphas(client, "2026-08-01", "2026-08-31")
    assert rows[0]["expression"] == "rank(close)"


# ----------------------------------------------------------------------
# Lỗi máy chủ
# ----------------------------------------------------------------------
def test_rate_limit_error_propagates_from_client_layer():
    """Lùi thời gian khi 429 là việc của BrainClient, scanner không tự xử lý."""
    client = FakeBrainClient([])
    client.raise_on_call[0] = RateLimitError("Vượt hạn mức.", 30.0)
    with pytest.raises(RateLimitError):
        fetch_submitted_alphas(client, "2026-08-01", "2026-08-31")


def test_transient_error_propagates():
    client = FakeBrainClient([])
    client.raise_on_call[0] = TransientError("Máy chủ bận.")
    with pytest.raises(TransientError):
        fetch_submitted_alphas(client, "2026-08-01", "2026-08-31")


def test_non_json_response_raises_brain_error():
    client = FakeBrainClient([FakeResponse(payload=None, text="<html>loi</html>")])
    with pytest.raises(BrainError):
        fetch_submitted_alphas(client, "2026-08-01", "2026-08-31")


def test_results_not_a_list_is_treated_as_empty():
    client = FakeBrainClient([{"results": "khong-phai-danh-sach", "next": None}])
    rows, diagnostics = fetch_submitted_alphas(client, "2026-08-01", "2026-08-31")
    assert rows == []
    assert diagnostics["stopped_because"] == "empty_page"


# ----------------------------------------------------------------------
# Lưu vào kho
# ----------------------------------------------------------------------
def test_scan_stores_rows_with_fingerprints(db):
    client = FakeBrainClient([
        page([
            make_alpha("A1", expression="rank(ts_mean(returns, 20))"),
            make_alpha("A2", expression="rank(ts_mean(returns, 60))"),
        ]),
    ])
    scanner = HistoricalAlphaScanner(client, db)
    result = scanner.scan("2026-08-01", "2026-08-31")

    assert result["inserted"] == 2
    stored = scanner.load()
    assert len(stored) == 2
    # Hai biểu thức chỉ khác cửa sổ nên phải cùng họ cấu trúc.
    assert stored[0]["family"] == stored[1]["family"]
    assert stored[0]["operators_json"]


def test_rescanning_updates_changed_metrics_without_duplicating(db):
    first = FakeBrainClient([page([make_alpha("A1", sharpe=1.0)])])
    HistoricalAlphaScanner(first, db).scan("2026-08-01", "2026-08-31")

    second = FakeBrainClient([page([make_alpha("A1", sharpe=2.5)])])
    result = HistoricalAlphaScanner(second, db).scan("2026-08-01", "2026-08-31")

    assert result["inserted"] == 0
    assert result["updated"] == 1
    rows = HistoricalAlphaScanner(second, db).load()
    assert len(rows) == 1
    assert rows[0]["sharpe"] == 2.5


def test_rescanning_identical_data_reports_unchanged(db):
    for _ in range(2):
        client = FakeBrainClient([page([make_alpha("A1", sharpe=1.0)])])
        result = HistoricalAlphaScanner(client, db).scan("2026-08-01", "2026-08-31")
    assert result["unchanged"] == 1
    assert result["inserted"] == 0


def test_store_preserves_failed_and_rejected_alphas(db):
    """Trí nhớ nghiên cứu phải giữ cả alpha hỏng, nếu không sẽ thiên lệch sống sót."""
    client = FakeBrainClient([
        page([
            make_alpha("OK", status="ACTIVE"),
            make_alpha("BAD", status="FAILED", sharpe=0.1),
            make_alpha("NO", status="UNSUBMITTED", sharpe=0.4),
        ]),
    ])
    scanner = HistoricalAlphaScanner(client, db)
    scanner.scan("2026-08-01", "2026-08-31", include_unsubmitted=True)
    statuses = {row["status"] for row in scanner.load()}
    assert statuses == {"ACTIVE", "FAILED", "UNSUBMITTED"}


def test_load_filters_by_date_range(db):
    client = FakeBrainClient([
        page([
            make_alpha("EARLY", submitted="2026-08-02T00:00:00Z"),
            make_alpha("LATE", submitted="2026-08-28T00:00:00Z"),
        ]),
    ])
    scanner = HistoricalAlphaScanner(client, db)
    scanner.scan("2026-08-01", "2026-08-31")
    assert {row["alpha_id"] for row in scanner.load("2026-08-20", "2026-08-31")} == {"LATE"}
