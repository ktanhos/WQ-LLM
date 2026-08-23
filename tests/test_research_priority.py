"""Điểm ưu tiên nghiên cứu.

Điểm ưu tiên là tích của bốn thành phần, mỗi thành phần trong khoảng 0 tới 1.
Kiểm thử dưới đây quan tâm tới hai thứ: điểm phải giải thích được từng bước,
và cỡ mẫu nhỏ phải kéo độ tin cậy xuống thay vì kéo điểm lên.
"""

import pytest

from alphaforge.research.memory import ResearchMemory
from alphaforge.research.priority import PriorityWeights, ResearchPriority
from conftest import insert_historical


@pytest.fixture()
def priority():
    return ResearchPriority()


# ----------------------------------------------------------------------
# Kho rỗng và cỡ mẫu nhỏ
# ----------------------------------------------------------------------
def test_unexplored_key_scores_without_any_data(priority):
    """Một vùng chưa ai thử vẫn chấm được điểm, chỉ là độ tin cậy bằng không."""
    score = priority.score("moi", sample_size=0)
    assert 0.0 <= score.score <= 1.0
    assert score.confidence == 0.0
    assert score.reasons


def test_confidence_grows_with_sample_size(priority):
    small = priority.score("a", sample_size=2, median_sharpe=1.5, pass_rate=1.0)
    large = priority.score("b", sample_size=80, median_sharpe=1.5, pass_rate=1.0)
    assert small.confidence < large.confidence


def test_tiny_sample_is_flagged_in_the_reasons(priority):
    """Cỡ mẫu quá nhỏ phải được nói thẳng, không để người đọc tự đoán."""
    score = priority.score("a", sample_size=2, median_sharpe=3.0, pass_rate=1.0)
    assert any("phỏng đoán" in reason or "quá nhỏ" in reason for reason in score.reasons)


def test_missing_metrics_are_treated_as_neutral_not_as_failure(priority):
    """Thiếu chỉ số khác với chỉ số kém, và điểm phải phản ánh đúng khác biệt đó."""
    unknown = priority.score("a", sample_size=10, median_sharpe=None)
    poor = priority.score("b", sample_size=10, median_sharpe=-0.5)
    assert unknown.score > poor.score
    assert any("trung tính" in reason for reason in unknown.reasons)


# ----------------------------------------------------------------------
# Thành phần và xếp hạng
# ----------------------------------------------------------------------
def test_every_component_is_reported(priority):
    score = priority.score("a", sample_size=10, median_sharpe=1.4, pass_rate=0.5)
    assert score.components
    assert all(0.0 <= value <= 1.0 for value in score.components.values())
    # Điểm là tích của các thành phần, nên không bao giờ vượt thành phần nhỏ nhất.
    assert score.score <= min(score.components.values()) + 1e-9


def test_heavily_explored_area_scores_lower_than_fresh_one(priority):
    fresh = priority.score("moi", sample_size=3, median_sharpe=1.3, pass_rate=0.4)
    tired = priority.score("cu", sample_size=300, median_sharpe=1.3, pass_rate=0.4)
    assert fresh.score > tired.score


def test_rank_orders_by_score_then_sample_size(priority):
    scores = [
        priority.score("a", sample_size=5, median_sharpe=0.1),
        priority.score("b", sample_size=5, median_sharpe=2.0),
        priority.score("c", sample_size=50, median_sharpe=2.0),
    ]
    ranked = priority.rank(scores, limit=3)
    # "b" và "c" cùng Sharpe, nhưng "c" đã thử 50 lần nên bị hạ ưu tiên; "a"
    # có Sharpe kém nhất. Cả hai yếu tố đều phải thể hiện trong thứ tự.
    assert ranked[0].key == "b"
    assert ranked.index(next(i for i in ranked if i.key == "c")) > 0


def test_rank_respects_limit(priority):
    scores = [priority.score(str(index), sample_size=index) for index in range(10)]
    assert len(priority.rank(scores, limit=3)) == 3


def test_explain_mentions_sample_size_and_components(priority):
    text = priority.explain(priority.score("a", sample_size=12, median_sharpe=1.4))
    assert "Cỡ mẫu 12" in text
    assert "Thành phần" in text


def test_weights_are_configurable():
    """Không ngưỡng nào được viết cứng; đổi trọng số phải đổi kết quả."""
    strict = ResearchPriority(PriorityWeights(target_sharpe=3.0))
    lenient = ResearchPriority(PriorityWeights(target_sharpe=0.5))
    key = dict(sample_size=10, median_sharpe=1.0, pass_rate=0.5)
    assert strict.score("a", **key).score < lenient.score("a", **key).score


# ----------------------------------------------------------------------
# Nối với trí nhớ nghiên cứu
# ----------------------------------------------------------------------
def test_priority_over_real_coverage(db, priority):
    """Vùng đã thử ba mươi lần mà không đạt phải xếp dưới vùng mới chớm."""
    for index in range(30):
        insert_historical(db, f"rank(ts_rank(returns, {5 + index}))",
                          alpha_id=f"R{index}", status="FAILED", sharpe=0.2)
    insert_historical(db, "rank(ts_mean(volume, 20))", alpha_id="V1", sharpe=1.7)

    memory = ResearchMemory(db)
    coverage = memory.coverage()["field"]
    profiles = memory.profiles()
    scores = [
        priority.score(
            key, dimension="field", sample_size=count,
            median_sharpe=profiles[key].median_sharpe if key in profiles else None,
            pass_rate=profiles[key].pass_rate if key in profiles else 0.0,
        )
        for key, count in coverage.items()
    ]
    ranked = priority.rank(scores)
    assert [item.key for item in ranked][0] == "volume"
    assert [item.key for item in ranked][-1] == "returns"


def test_priority_on_empty_database_returns_nothing(db, priority):
    memory = ResearchMemory(db)
    assert priority.rank([]) == []
    assert memory.coverage()["field"] == {}
