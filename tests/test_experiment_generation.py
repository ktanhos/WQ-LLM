"""Sinh biến thể của thí nghiệm và cách hệ thống xử lý trùng lặp.

Trọng tâm là ranh giới giữa hai loại trùng lặp có ý nghĩa trái ngược nhau:

    trùng ngẫu nhiên   hai lô sinh tự do tình cờ ra cùng ý tưởng. Cần chặn, vì
                       nó tiêu hạn mức mô phỏng mà không tạo thêm thông tin.

    khảo sát có chủ đích  thí nghiệm quét nhiều cửa sổ trong cùng một họ. Không
                       được chặn, vì đó chính là mục đích của thí nghiệm.

Chặn nhầm loại thứ hai sẽ giết sạch mọi biến thể và làm thí nghiệm vô nghĩa,
và lỗi đó không lộ ra ở đâu ngoài việc mọi báo cáo đều có cỡ mẫu bằng không.
"""

import pytest

from alphaforge.pipeline.generation import generate_and_queue
from alphaforge.research.experiment import (
    ExperimentDesign,
    ExperimentEngine,
    ExperimentError,
)
from alphaforge.research.models import Hypothesis, ResearchProject
from alphaforge.research.store import ResearchStore
from alphaforge.storage.db import Status

SETTINGS = {"region": "USA", "universe": "TOP3000", "delay": 1}


@pytest.fixture()
def store(db):
    return ResearchStore(db)


@pytest.fixture()
def hypothesis_id(store):
    research_id = store.create_project(ResearchProject(name="Du an"))
    return store.create_hypothesis(
        Hypothesis(research_id=research_id, statement="Gia thuyet")
    )


def _design(store, hypothesis_id, **overrides):
    base = dict(
        hypothesis_id=hypothesis_id,
        name="Khao sat cua so",
        base_expression="rank(ts_mean(close, {lookback}))",
        variable="lookback",
        values=[20, 60, 120],
        settings=dict(SETTINGS),
    )
    base.update(overrides)
    return ExperimentEngine(store).create(ExperimentDesign(**base))


# ----------------------------------------------------------------------
# Sinh biến thể
# ----------------------------------------------------------------------
def test_variants_reach_the_queue_exactly_as_designed(db, store, hypothesis_id):
    """Biểu thức vào hàng đợi phải giống hệt biến thể đã thiết kế.

    Không thêm `rank(...)`, không bọc `zscore(...)`. Bọc thêm bất kỳ toán tử
    nào là phá đúng thứ thí nghiệm đang cô lập.
    """
    result = _design(store, hypothesis_id)
    plan = ExperimentEngine(store).build_plan(result["experiment_id"], seed=7)
    plan.save(db)
    generate_and_queue(db, plan, tag="tn", variant_ids=plan.variant_ids)

    queued = {record.expression for record in db.fetch_by_status(Status.PENDING)}
    assert queued == {
        "rank(ts_mean(close, 20))",
        "rank(ts_mean(close, 60))",
        "rank(ts_mean(close, 120))",
    }


def test_every_queued_alpha_is_linked_to_its_variant(db, store, hypothesis_id):
    """Không có liên kết này thì báo cáo thí nghiệm có cỡ mẫu bằng không."""
    result = _design(store, hypothesis_id)
    plan = ExperimentEngine(store).build_plan(result["experiment_id"], seed=7)
    plan.save(db)
    generate_and_queue(db, plan, tag="tn", variant_ids=plan.variant_ids)

    records = db.fetch_by_status(Status.PENDING)
    assert records
    assert all(record.variant_id for record in records)
    assert {record.variant_id for record in records} == set(result["variant_ids"])


def test_every_queued_alpha_gets_a_local_id(db, store, hypothesis_id):
    """Mã cục bộ có ngay khi sinh, trước khi tồn tại mã của nền tảng."""
    result = _design(store, hypothesis_id)
    plan = ExperimentEngine(store).build_plan(result["experiment_id"], seed=7)
    plan.save(db)
    generate_and_queue(db, plan, tag="tn", variant_ids=plan.variant_ids)

    records = db.fetch_by_status(Status.PENDING)
    assert all(record.local_id for record in records)
    assert all(record.alpha_id is None for record in records)


# ----------------------------------------------------------------------
# Trùng lặp
# ----------------------------------------------------------------------
def test_an_expression_already_queued_is_not_queued_twice(db, store, hypothesis_id):
    """Cùng biểu thức, cùng thiết lập thì chỉ tốn một lượt mô phỏng."""
    result = _design(store, hypothesis_id)
    engine = ExperimentEngine(store)
    for _ in range(2):
        plan = engine.build_plan(result["experiment_id"], seed=7)
        generate_and_queue(db, plan, tag="tn", variant_ids=plan.variant_ids)

    assert db.counts_by_status()[Status.PENDING] == 3


def test_the_same_expression_under_different_settings_is_not_a_duplicate(db):
    """Cùng biểu thức ở hai khu vực là hai quan sát khác nhau, không phải trùng."""
    db.add_alphas(["rank(ts_mean(close, 20))"], {"region": "USA"})
    db.add_alphas(["rank(ts_mean(close, 20))"], {"region": "EUR"})
    assert db.counts_by_status()[Status.PENDING] == 2


def test_two_experiments_may_share_an_expression(db, store, hypothesis_id):
    """Hai thí nghiệm khác nhau có thể chạm tới cùng một biểu thức.

    Biểu thức chỉ vào hàng đợi một lần, nhưng cả hai thí nghiệm vẫn tồn tại và
    vẫn giữ được biến thể của mình. Chặn ở đây bằng cách xóa thí nghiệm thứ hai
    sẽ làm mất một thiết kế mà người nghiên cứu đã cố ý tạo ra.
    """
    first = _design(store, hypothesis_id, values=[20, 60])
    second = _design(store, hypothesis_id, name="Khao sat khac", values=[20, 120])

    engine = ExperimentEngine(store)
    for result in (first, second):
        plan = engine.build_plan(result["experiment_id"], seed=7)
        generate_and_queue(db, plan, tag="tn", variant_ids=plan.variant_ids)

    # 20, 60, 120: ba biểu thức phân biệt, dù đã sinh bốn biến thể.
    assert db.counts_by_status()[Status.PENDING] == 3
    assert len(store.list_variants(first["experiment_id"])) == 2
    assert len(store.list_variants(second["experiment_id"])) == 2


def test_variants_within_one_family_are_all_kept(db, store, hypothesis_id):
    """Ba cửa sổ trong cùng một họ là ba biến thể, không phải ba bản trùng."""
    result = _design(store, hypothesis_id)
    variants = store.list_variants(result["experiment_id"])
    assert len(variants) == 3
    assert len({item["expression"] for item in variants}) == 3


# ----------------------------------------------------------------------
# Thiết kế không hợp lệ
# ----------------------------------------------------------------------
def test_one_value_is_not_an_experiment(store, hypothesis_id):
    """Một giá trị thì không có gì để so, nên không phải là thí nghiệm."""
    with pytest.raises(ExperimentError):
        _design(store, hypothesis_id, values=[20])


def test_repeated_values_are_rejected(store, hypothesis_id):
    with pytest.raises(ExperimentError):
        _design(store, hypothesis_id, values=[20, 20, 60])


def test_an_invalid_design_writes_nothing(db, store, hypothesis_id):
    """Thiết kế sai bị chặn trước khi ghi bất kỳ bản ghi nào."""
    with pytest.raises(ExperimentError):
        _design(store, hypothesis_id, values=[20])

    connection = db.connect()
    try:
        experiments = connection.execute(
            "SELECT COUNT(*) AS n FROM experiments"
        ).fetchone()["n"]
        variants = connection.execute(
            "SELECT COUNT(*) AS n FROM experiment_variants"
        ).fetchone()["n"]
    finally:
        connection.close()
    assert experiments == 0
    assert variants == 0


def test_building_a_plan_for_a_missing_experiment_fails(store):
    with pytest.raises(ExperimentError):
        ExperimentEngine(store).build_plan(999)


# ----------------------------------------------------------------------
# Hồi quy: hai lỗi im lặng đã từng làm mất alpha khỏi hàng đợi
# ----------------------------------------------------------------------
def test_a_later_batch_does_not_invalidate_an_already_queued_alpha(db, store, hypothesis_id):
    """Lô sau từ chối một biểu thức trùng thì lô trước vẫn phải giữ chỗ.

    Trước đây câu lệnh đánh dấu INVALID không giới hạn theo lượt sinh, nên nó
    lật cả bản ghi đang chờ của lô trước. Alpha đó không bao giờ được mô phỏng
    và không có gì báo cho ai biết.
    """
    first = _design(store, hypothesis_id, values=[20, 60])
    second = _design(store, hypothesis_id, name="Lo sau", values=[20, 120])

    engine = ExperimentEngine(store)
    for result in (first, second):
        plan = engine.build_plan(result["experiment_id"], seed=7)
        generate_and_queue(db, plan, tag="tn", variant_ids=plan.variant_ids)

    pending = {record.expression for record in db.fetch_by_status(Status.PENDING)}
    assert "rank(ts_mean(close, 20))" in pending
    assert pending == {
        "rank(ts_mean(close, 20))",
        "rank(ts_mean(close, 60))",
        "rank(ts_mean(close, 120))",
    }


def test_design_metadata_never_reaches_the_simulation_settings(db, store, hypothesis_id):
    """Thiết kế không được lẫn vào thiết lập mô phỏng của alpha.

    Thiết kế nằm lồng trong `settings["_design"]` của thí nghiệm. Để nó lọt
    xuống alpha gây hai hậu quả: mã băm chống trùng tính cả thiết kế nên bản
    trùng lọt qua ràng buộc duy nhất, và siêu dữ liệu thiết kế bị gửi lên máy
    chủ như thể là tham số mô phỏng.
    """
    result = _design(store, hypothesis_id)
    plan = ExperimentEngine(store).build_plan(result["experiment_id"], seed=7)
    assert "_design" not in plan.settings
    assert plan.settings == SETTINGS

    generate_and_queue(db, plan, tag="tn", variant_ids=plan.variant_ids)
    for record in db.fetch_by_status(Status.PENDING):
        assert "_design" not in record.settings
        assert record.settings == SETTINGS
