"""Kế hoạch sinh: vòng đời từ dựng, kiểm tra, lưu tới đọc lại.

Kế hoạch được lưu **trước** khi sinh, nên ngay cả khi lô sinh bị ngắt giữa
chừng vẫn còn bản ghi cho biết định chạy gì. Kiểm thử dưới đây bảo vệ tính
chất đó: mọi thứ cần để tái lập một lô sinh phải đi qua được vòng lưu và đọc.
"""

import pytest

from alphaforge.research.plan import (
    GENERATOR_STRATEGIES,
    STRATEGY_DIRECT,
    GenerationPlan,
    PlanError,
)


def _plan(**overrides) -> GenerationPlan:
    base = dict(
        strategy="template",
        data_fields=("close", "volume"),
        lookbacks=(20, 60),
        max_candidates=10,
    )
    base.update(overrides)
    return GenerationPlan(**base)


# ----------------------------------------------------------------------
# Chiến lược
# ----------------------------------------------------------------------
def test_direct_strategy_is_separate_from_the_generator_strategies():
    """Chế độ direct không nằm trong danh sách chiến lược của bộ sinh.

    Đây là điểm mấu chốt của thí nghiệm có kiểm soát: biến thể phải vào hàng
    đợi đúng như đã thiết kế. Nếu direct bị coi là một chiến lược sinh thì bộ
    sinh sẽ bọc thêm toán tử và phá đúng thứ thí nghiệm muốn cô lập.
    """
    assert STRATEGY_DIRECT not in GENERATOR_STRATEGIES


def test_direct_requires_explicit_expressions():
    with pytest.raises(PlanError, match="direct"):
        _plan(strategy=STRATEGY_DIRECT, seed_expressions=()).validate()


def test_direct_plan_with_expressions_is_valid():
    plan = _plan(strategy=STRATEGY_DIRECT,
                 seed_expressions=("rank(ts_mean(close, 20))",))
    assert plan.is_valid


def test_unknown_strategy_is_rejected():
    with pytest.raises(PlanError, match="Chiến lược không hợp lệ"):
        _plan(strategy="khong_co").validate()


# ----------------------------------------------------------------------
# Trường hợp biên của tham số
# ----------------------------------------------------------------------
def test_zero_candidates_is_rejected():
    """Kế hoạch sinh không alpha nào là lỗi cấu hình, không phải lô rỗng hợp lệ."""
    with pytest.raises(PlanError, match="max_candidates"):
        _plan(max_candidates=0).validate()


@pytest.mark.parametrize("window", [0, 1, -5])
def test_degenerate_lookback_is_rejected(window):
    with pytest.raises(PlanError, match="Cửa sổ nhìn lại"):
        _plan(lookbacks=(window,)).validate()


def test_hypothesis_without_project_is_rejected():
    """Kế hoạch gắn với giả thuyết mà không có dự án thì không truy vết được."""
    with pytest.raises(PlanError, match="dự án nghiên cứu"):
        _plan(hypothesis_id=1).validate()


# ----------------------------------------------------------------------
# Lưu và đọc lại
# ----------------------------------------------------------------------
def test_plan_round_trips_every_field_needed_to_reproduce_it(db):
    plan = _plan(
        strategy="template",
        data_fields=("close", "volume"),
        operators=("ts_mean", "rank"),
        lookbacks=(20, 60, 120),
        groups=("subindustry", "sector"),
        templates=(),
        max_candidates=25,
        seed=1234,
        settings={"region": "USA", "delay": 1},
        constraints={"max_length": 200},
        notes="ghi chu",
    )
    plan_id = plan.save(db)

    loaded = GenerationPlan.load(db, plan_id)
    assert loaded is not None
    assert list(loaded.data_fields) == ["close", "volume"]
    assert list(loaded.lookbacks) == [20, 60, 120]
    assert list(loaded.groups) == ["subindustry", "sector"]
    assert loaded.seed == 1234
    assert loaded.settings == {"region": "USA", "delay": 1}
    assert loaded.constraints == {"max_length": 200}


def test_loading_a_missing_plan_returns_none(db):
    assert GenerationPlan.load(db, 999) is None


def test_invalid_plan_is_never_written(db):
    with pytest.raises(PlanError):
        _plan(max_candidates=0).save(db)
    connection = db.connect()
    try:
        count = connection.execute(
            "SELECT COUNT(*) AS n FROM generation_plans"
        ).fetchone()["n"]
    finally:
        connection.close()
    assert count == 0


# ----------------------------------------------------------------------
# Mô tả
# ----------------------------------------------------------------------
def test_as_dict_carries_the_whole_plan():
    plan = _plan(groups=("subindustry",), seed=7,
                 seed_expressions=("rank(close)",), variant_ids={"rank(close)": 3})
    payload = plan.as_dict()
    assert payload["strategy"] == "template"
    assert payload["groups"] == ["subindustry"]
    assert payload["seed"] == 7
    assert payload["variant_ids"] == {"rank(close)": 3}


def test_describe_is_one_readable_line():
    text = _plan(groups=("subindustry",), seed=7).describe()
    assert "chiến lược=template" in text
    assert "hạt giống=7" in text
    assert "\n" not in text
