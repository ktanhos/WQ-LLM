"""Phát hiện thiếu hụt nghiên cứu, tập trung vào các trường hợp biên.

Kiểm thử ở đây cố ý dựng những hình dạng dữ liệu dễ khiến bộ phát hiện kết
luận sai: kho rỗng, cỡ mẫu quá nhỏ để nói được gì, và trường hợp nguy hiểm
nhất là nhiều quan sát nhưng tất cả rơi vào một giá trị duy nhất.
"""

import pytest

from alphaforge.research.gap import (
    GAP_FIELD,
    GAP_LOOKBACK,
    GAP_SETTING,
    GAP_TEMPLATE,
    ResearchGap,
)
from alphaforge.research.memory import ResearchMemory
from conftest import insert_historical


@pytest.fixture()
def gap(db):
    return ResearchGap(ResearchMemory(db))


# ----------------------------------------------------------------------
# Kho rỗng
# ----------------------------------------------------------------------
def test_empty_database_yields_no_gaps(gap):
    """Kho rỗng không có thiếu hụt nào, và cũng không được ném lỗi.

    Đây là trạng thái của mọi người dùng mới. Trả về danh sách rỗng chứ không
    phải bịa ra thiếu hụt từ không có dữ liệu.
    """
    assert gap.find() == []


def test_empty_database_still_reports_never_tried_values(db):
    """Danh mục trường dữ liệu cho biết giá trị nào chưa ai thử.

    Không có danh mục thì đếm trên dữ liệu đã có sẽ không bao giờ thấy được
    một trường chưa xuất hiện lần nào.
    """
    gaps = ResearchGap(ResearchMemory(db)).find(
        [GAP_FIELD], known_values={"field": ["close", "volume", "cap"]}
    )
    assert {item.key for item in gaps} == {"close", "volume", "cap"}
    assert all(item.count == 0 for item in gaps)


# ----------------------------------------------------------------------
# Cỡ mẫu nhỏ
# ----------------------------------------------------------------------
def test_small_sample_is_reported_as_a_gap_not_as_a_verdict(db):
    """Một giá trị mới thử vài lần là thiếu hụt, không phải kết luận xấu."""
    insert_historical(db, "rank(ts_mean(volume, 20))", alpha_id="V1", sharpe=1.8)
    gaps = ResearchGap(ResearchMemory(db)).find([GAP_FIELD])
    volume = next(item for item in gaps if item.key == "volume")
    assert volume.count == 1
    assert "1 alpha" in volume.reason
    # Lý do nói về số quan sát, không nói giá trị này tốt hay xấu.
    assert "tốt" not in volume.reason.lower()


def test_low_confidence_when_sample_is_tiny(db):
    insert_historical(db, "rank(ts_mean(volume, 20))", alpha_id="V1")
    gaps = ResearchGap(ResearchMemory(db)).find([GAP_FIELD])
    volume = next(item for item in gaps if item.key == "volume")
    assert volume.priority is not None
    assert volume.priority.confidence < 0.3


# ----------------------------------------------------------------------
# Nhiều quan sát nhưng thiếu độ phủ
# ----------------------------------------------------------------------
def test_many_alphas_all_on_one_setting_is_a_coverage_gap(db):
    """Hai mươi alpha cùng một delay không phải là đã khảo sát delay.

    Đây là loại thiếu hụt nguy hiểm nhất: nhìn vào số lượng sẽ tưởng đã khảo
    sát kỹ, trong khi thực ra chưa biết gì về các giá trị còn lại.
    """
    for index in range(25):
        insert_historical(db, f"rank(ts_mean(close, {5 + index}))",
                          alpha_id=f"C{index}", delay=1)

    gaps = ResearchGap(ResearchMemory(db)).find([GAP_SETTING])
    assert gaps, "Tập trung 100 phần trăm vào một giá trị phải bị phát hiện."
    reasons = " ".join(item.reason for item in gaps)
    assert "100" in reasons
    assert "delay=1" in reasons or "region=USA" in reasons


def test_coverage_gap_needs_enough_observations(db):
    """Ba alpha cùng delay chưa đủ để gọi là thiếu độ phủ.

    Dưới cỡ mẫu tối thiểu thì mọi thứ đều tập trung vào một giá trị, và báo
    thiếu độ phủ ở đó chỉ tạo ra tiếng ồn.
    """
    for index in range(3):
        insert_historical(db, f"rank(ts_mean(close, {5 + index}))", alpha_id=f"C{index}")
    coverage_gaps = [
        item for item in ResearchGap(ResearchMemory(db)).find([GAP_SETTING])
        if item.detail.get("dimension")
    ]
    assert coverage_gaps == []


# ----------------------------------------------------------------------
# Cùng họ khác tham số, khác trường cùng mẫu
# ----------------------------------------------------------------------
def test_same_family_different_parameters_counts_as_one_family(db):
    """Đổi cửa sổ không tạo ra họ cấu trúc mới.

    Nếu mỗi cửa sổ được tính thành một họ riêng thì mọi họ đều có cỡ mẫu một
    và toàn bộ phần xếp hạng theo họ trở nên vô nghĩa.
    """
    for window in (10, 20, 60, 120):
        insert_historical(db, f"rank(ts_mean(close, {window}))", alpha_id=f"W{window}")

    coverage = ResearchMemory(db).coverage()
    assert len(coverage["family"]) == 1
    assert len(coverage["lookback"]) == 4


def test_different_field_same_template_shares_the_template(db):
    """Đổi trường dữ liệu giữ nguyên mẫu biểu thức nhưng đổi họ."""
    insert_historical(db, "rank(ts_mean(close, 20))", alpha_id="F1")
    insert_historical(db, "rank(ts_mean(volume, 20))", alpha_id="F2")

    coverage = ResearchMemory(db).coverage()
    assert len(coverage["template"]) == 1
    assert len(coverage["family"]) == 2
    assert set(coverage["field"]) == {"close", "volume"}


def test_template_gap_is_reported_separately_from_family_gap(db):
    insert_historical(db, "rank(ts_mean(close, 20))", alpha_id="T1")
    kinds = {item.kind for item in ResearchGap(ResearchMemory(db)).find()}
    assert GAP_TEMPLATE in kinds
    assert GAP_LOOKBACK in kinds


# ----------------------------------------------------------------------
# Trạng thái lẫn lộn
# ----------------------------------------------------------------------
def test_failed_alphas_still_count_as_evidence(db):
    """Alpha hỏng vẫn là bằng chứng rằng vùng đó đã được thử.

    Bỏ alpha hỏng ra khỏi phép đếm sẽ khiến một vùng đã thử ba mươi lần và
    hỏng cả ba mươi trông như chưa ai đụng tới.
    """
    for index in range(10):
        insert_historical(db, f"rank(ts_mean(close, {5 + index}))",
                          alpha_id=f"C{index}", status="FAILED", sharpe=None)

    gaps = ResearchGap(ResearchMemory(db)).find([GAP_FIELD])
    close = next((item for item in gaps if item.key == "close"), None)
    # Mười alpha vượt ngưỡng chưa khảo sát, nên trường này không còn là thiếu hụt.
    assert close is None
    assert ResearchMemory(db).coverage()["field"]["close"] == 10


def test_mixed_statuses_are_all_counted(db):
    insert_historical(db, "rank(ts_mean(close, 20))", alpha_id="A1", status="ACTIVE")
    insert_historical(db, "rank(ts_mean(close, 30))", alpha_id="A2", status="FAILED")
    insert_historical(db, "rank(ts_mean(close, 40))", alpha_id="A3", status="UNSUBMITTED")

    statistics = ResearchMemory(db).statistics()
    assert statistics["counts"]["tested"] == 3
    assert statistics["counts"]["passed"] == 1
    assert statistics["counts"]["rejected"] == 2


def test_missing_metrics_do_not_break_gap_detection(db):
    """Bản ghi thiếu chỉ số vẫn phải đếm được, chỉ là không có Sharpe."""
    insert_historical(db, "rank(ts_mean(volume, 20))", alpha_id="V1",
                      sharpe=None, fitness=None, turnover=None)
    gaps = ResearchGap(ResearchMemory(db)).find([GAP_FIELD])
    volume = next(item for item in gaps if item.key == "volume")
    assert volume.count == 1
    assert volume.detail.get("median_sharpe") is None
