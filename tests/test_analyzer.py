"""Kiểm thử phân tích lịch sử nghiên cứu.

Trọng tâm: thống kê phải chịu được ngoại lai, và tỷ lệ đạt phải phản ánh cả
alpha thất bại chứ không chỉ alpha thành công.
"""

import json

from alphaforge.history.analyzer import (
    analyze,
    classify_status,
    describe,
    research_gaps,
    summarize,
)


# ----------------------------------------------------------------------
# Thống kê mô tả
# ----------------------------------------------------------------------
def test_describe_reports_median_and_quantiles_with_sample_size():
    result = describe([1.0, 2.0, 3.0, 4.0])
    assert result["count"] == 4
    assert result["median"] == 2.5
    assert result["p25"] == 1.75
    assert result["p75"] == 3.25
    assert result["min"] == 1.0
    assert result["max"] == 4.0


def test_median_resists_a_single_extreme_outlier():
    """Đây là lý do báo cáo dùng trung vị thay vì trung bình."""
    normal = [1.0, 1.1, 1.2, 1.3, 1.4]
    with_outlier = normal + [50.0]
    assert describe(with_outlier)["median"] == 1.25
    # Trung bình bị kéo lên trên mọi quan sát thực tế.
    assert describe(with_outlier)["mean"] > max(normal)


def test_describe_handles_empty_input_with_consistent_shape():
    result = describe([])
    assert result["count"] == 0
    assert result["median"] is None
    assert set(result) == set(describe([1.0]))


def test_describe_ignores_none_values():
    assert describe([1.0, None, 3.0])["count"] == 2


def test_single_value_has_zero_stdev():
    assert describe([2.0])["stdev"] == 0.0


# ----------------------------------------------------------------------
# Quy đổi trạng thái
# ----------------------------------------------------------------------
def test_status_labels_are_normalised_into_four_buckets():
    assert classify_status("ACTIVE") == "passed"
    assert classify_status("pass") == "passed"
    assert classify_status("FAILED") == "failed"
    assert classify_status("UNSUBMITTED") == "rejected"
    assert classify_status(None) == "other"


# ----------------------------------------------------------------------
# Tóm tắt tương thích ngược
# ----------------------------------------------------------------------
def test_summarize_keeps_legacy_keys():
    rows = [
        {"status": "PASS", "region": "USA", "universe": "TOP3000",
         "sharpe": 1.0, "fitness": 0.5, "turnover": 0.2},
        {"status": "FAIL", "region": "USA", "universe": "TOP3000",
         "sharpe": 2.0, "fitness": 1.0, "turnover": 0.4},
    ]
    result = summarize(rows)
    assert result["total"] == 2
    assert result["best_sharpe"] == 2.0
    assert result["status_counts"]["PASS"] == 1
    assert result["average_sharpe"] == 1.5
    # Khóa mới bổ sung bên cạnh khóa cũ.
    assert result["median_sharpe"] == 1.5
    assert result["sharpe"]["count"] == 2


def test_summarize_on_empty_input():
    result = summarize([])
    assert result["total"] == 0
    assert result["best_sharpe"] is None
    assert result["median_sharpe"] is None


# ----------------------------------------------------------------------
# Phân tích đầy đủ và thiên lệch sống sót
# ----------------------------------------------------------------------
def _row(family, status, sharpe, **extra):
    row = {
        "family": family, "status": status, "sharpe": sharpe,
        "fitness": 1.0, "turnover": 0.3, "region": "USA",
        "universe": "TOP3000", "delay": 1,
        "operators_json": json.dumps(["rank", "ts_mean"]),
        "fields_json": json.dumps(["close"]),
        "windows_json": json.dumps([20]),
        "template": "T1",
    }
    row.update(extra)
    return row


def test_pass_rate_reflects_failures_not_only_successes():
    """Một họ 100 alpha với 2 alpha đạt phải cho tỷ lệ đạt 2 phần trăm."""
    rows = [_row("F1", "ACTIVE", 2.0) for _ in range(2)]
    rows += [_row("F1", "FAILED", 0.1) for _ in range(98)]
    result = analyze(rows)
    family = result["distribution"]["family"][0]
    assert family["count"] == 100
    assert family["passed"] == 2
    assert family["pass_rate"] == 0.02
    assert family["failed"] == 98


def test_overall_counts_split_by_outcome():
    rows = [
        _row("F1", "ACTIVE", 1.5), _row("F1", "FAILED", 0.2),
        _row("F2", "UNSUBMITTED", 0.5),
    ]
    result = analyze(rows)
    assert result["counts"] == {"passed": 1, "rejected": 1, "failed": 1, "other": 0}


def test_small_families_are_not_ranked():
    """Một họ hai alpha Sharpe cao không đủ căn cứ để gọi là cấu trúc tốt."""
    rows = [_row("TINY", "ACTIVE", 5.0), _row("TINY", "ACTIVE", 5.0)]
    rows += [_row("BIG", "ACTIVE", 1.5) for _ in range(10)]
    result = analyze(rows, min_sample=5)
    ranked = {item["family"] for item in result["high_performing_structures"]}
    assert "BIG" in ranked
    assert "TINY" not in ranked


def test_frequently_tested_and_underexplored_are_separated():
    rows = [_row("HOT", "FAILED", 0.3) for _ in range(30)]
    rows += [_row("COLD", "ACTIVE", 1.8) for _ in range(2)]
    result = analyze(rows, saturated_threshold=25, underexplored_threshold=5)
    assert {item["family"] for item in result["frequently_tested_structures"]} == {"HOT"}
    assert {item["family"] for item in result["underexplored_structures"]} == {"COLD"}


def test_distribution_covers_all_requested_dimensions():
    result = analyze([_row("F1", "ACTIVE", 1.5)])
    assert set(result["distribution"]) == {
        "region", "universe", "delay", "field", "operator", "family", "template",
    }


def test_operator_and_field_distributions_expand_json_lists():
    rows = [
        _row("F1", "ACTIVE", 1.5, operators_json=json.dumps(["rank"]),
             fields_json=json.dumps(["close", "volume"])),
    ]
    result = analyze(rows)
    fields = {item["field"] for item in result["distribution"]["field"]}
    assert fields == {"close", "volume"}


def test_windows_by_family_records_tested_lookbacks_and_best():
    rows = [
        _row("F1", "ACTIVE", 1.0, windows_json=json.dumps([20])),
        _row("F1", "ACTIVE", 3.0, windows_json=json.dumps([60])),
    ]
    result = analyze(rows)
    entry = result["windows_by_family"]["F1"]
    assert set(entry["tested_windows"]) == {"20", "60"}
    assert entry["best_sharpe"] == 3.0
    assert entry["best_window"] == "60"


def test_analyze_handles_empty_history():
    result = analyze([])
    assert result["total"] == 0
    assert result["pass_rate"] == 0.0
    assert result["high_performing_structures"] == []


def test_malformed_json_columns_do_not_raise():
    rows = [_row("F1", "ACTIVE", 1.0, operators_json="{khong-phai-json", fields_json=None)]
    result = analyze(rows)
    assert result["total"] == 1


# ----------------------------------------------------------------------
# Khoảng trống nghiên cứu
# ----------------------------------------------------------------------
def test_research_gaps_prefer_promising_but_lightly_tested_families():
    rows = [_row("SATURATED", "ACTIVE", 1.6) for _ in range(60)]
    rows += [_row("FRESH", "ACTIVE", 1.6) for _ in range(4)]
    gaps = research_gaps(analyze(rows))
    assert gaps[0]["family"] == "FRESH"
    assert gaps[0]["priority"] > gaps[-1]["priority"]


def test_research_gaps_respects_limit():
    rows = [_row(f"F{i}", "ACTIVE", 1.5) for i in range(30)]
    assert len(research_gaps(analyze(rows), limit=5)) == 5
