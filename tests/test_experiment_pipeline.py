"""Phase 4 tới 13: thí nghiệm, kế hoạch, kiểm tra, độ bền, thẩm định, ứng viên.

Không kiểm thử nào ở đây gọi BRAIN. Bước mô phỏng được thay bằng cách ghi thẳng
chỉ số vào kho, đúng như máy chủ sẽ làm.
"""

import pytest

from alphaforge.pipeline.evaluation import EvaluationPipeline
from alphaforge.pipeline.generation import generate_and_queue, request_from_plan
from alphaforge.pipeline.robustness import (
    CHECK_DRAWDOWN,
    CHECK_PARAMETER_SENSITIVITY,
    CHECK_RETURN_CONCENTRATION,
    CHECK_YEAR_BY_YEAR,
    PROFILES,
    RobustnessChecker,
    RobustnessThresholds,
)
from alphaforge.pipeline.scorer import Scorer
from alphaforge.pipeline.validation import (
    AlphaValidator,
    ValidationConstraints,
    validator_from_database,
)
from alphaforge.research.experiment import (
    ExperimentDesign,
    ExperimentEngine,
    ExperimentError,
)
from alphaforge.research.memory import ResearchMemory
from alphaforge.research.models import Hypothesis, ResearchProject
from alphaforge.research.plan import GenerationPlan, PlanError
from alphaforge.research.store import ResearchStore
from alphaforge.storage.db import EvaluationStatus, Status

SETTINGS = {"region": "USA", "universe": "TOP3000", "delay": 1}
SCORING = {"min_sharpe": 1.0, "min_fitness": 0.8, "max_turnover": 0.7}
GOOD_METRICS = {
    "sharpe": 1.8, "fitness": 1.2, "turnover": 0.25, "drawdown": 0.15,
    "marginBps": 12.0, "checkFailures": [],
    "pnlByYear": {"2022": 1.0, "2023": 1.1, "2024": 0.9, "2025": 1.2},
}


@pytest.fixture()
def store(db):
    return ResearchStore(db)


@pytest.fixture()
def hypothesis_id(store):
    research_id = store.create_project(ResearchProject(name="P", family="Momentum"))
    return store.create_hypothesis(
        Hypothesis(research_id=research_id, statement="Volume xác nhận momentum.")
    )


# ======================================================================
# Phase 8: kiểm tra biểu thức
# ======================================================================
@pytest.mark.parametrize(
    "expression,fragment",
    [
        ("rank(close", "Ngoặc không cân bằng"),
        ("rank(close))", "Thừa dấu đóng ngoặc"),
        ("rank()", "không có đối số"),
        ("ts_mean(close,, 20)", "chỗ trống"),
        ("close + * open", "liền nhau"),
        ("", "rỗng"),
    ],
)
def test_syntax_errors_are_caught(expression, fragment):
    result = AlphaValidator().validate(expression)
    assert not result.valid
    assert any(fragment in error for error in result.errors)


def test_valid_expression_passes():
    result = AlphaValidator().validate("group_neutralize(ts_zscore(close, 20), subindustry)")
    assert result.valid
    assert result.errors == []
    assert result.meta["family"]


def test_unary_minus_is_not_mistaken_for_double_operator():
    assert AlphaValidator().validate("close * -open").valid


def test_length_limit_is_enforced():
    constraints = ValidationConstraints(max_length=10)
    result = AlphaValidator(constraints=constraints).validate("rank(ts_mean(close, 20))")
    assert not result.valid
    assert any("dài" in error for error in result.errors)


def test_unknown_field_is_rejected_when_catalogue_is_known():
    validator = AlphaValidator(known_fields=["close", "open"])
    assert not validator.validate("rank(khong_ton_tai)").valid
    assert validator.validate("rank(close)").valid


def test_unknown_operator_is_rejected_when_catalogue_is_known():
    validator = AlphaValidator(known_operators=["rank"])
    assert not validator.validate("ts_mean(close, 20)").valid


def test_missing_catalogue_does_not_reject_new_names():
    """Nền tảng liên tục thêm toán tử mới, không có danh mục thì không đoán bừa."""
    assert AlphaValidator().validate("toan_tu_moi(close, 20)").valid


def test_research_constraint_limits_fields():
    constraints = ValidationConstraints(allowed_fields=["close"])
    validator = AlphaValidator(constraints=constraints)
    assert validator.validate("rank(close)").valid
    assert not validator.validate("rank(volume)").valid


def test_required_and_forbidden_operators():
    validator = AlphaValidator(
        constraints=ValidationConstraints(
            required_operators=["rank"], forbidden_operators=["trade_when"]
        )
    )
    assert validator.validate("rank(close)").valid
    assert not validator.validate("ts_mean(close, 20)").valid
    assert not validator.validate("rank(trade_when(close > 0, close, -1))").valid


def test_duplicate_within_a_batch_is_detected():
    validator = AlphaValidator()
    results = validator.validate_many(["rank(close)", "rank( close )", "rank(open)"])
    assert results[0].valid
    assert not results[1].valid          # cùng biểu thức, chỉ khác khoảng trắng
    assert results[2].valid


def test_validator_built_from_database_uses_stored_catalogue(db):
    db.save_data_fields(
        [{"id": "close", "type": "MATRIX"}], region="USA", universe="TOP3000", delay=1
    )
    validator = validator_from_database(db)
    assert validator.validate("rank(close)").valid
    assert not validator.validate("rank(khong_co)").valid


def test_empty_catalogue_does_not_block_everything(db):
    """Kho chưa tải danh mục thì không được loại sạch mọi biểu thức."""
    assert validator_from_database(db).validate("rank(close)").valid


# ======================================================================
# Phase 5: kế hoạch sinh
# ======================================================================
def test_plan_requires_known_strategy():
    with pytest.raises(PlanError):
        GenerationPlan(strategy="khong_ton_tai", data_fields=["close"]).validate()


def test_plan_requires_fields_for_template_strategy():
    with pytest.raises(PlanError):
        GenerationPlan(strategy="template").validate()


def test_pairwise_needs_two_fields():
    with pytest.raises(PlanError):
        GenerationPlan(strategy="pairwise", data_fields=["close"]).validate()


def test_mutate_needs_seed_expressions():
    with pytest.raises(PlanError):
        GenerationPlan(strategy="mutate").validate()


def test_plan_rejects_unknown_template():
    with pytest.raises(PlanError):
        GenerationPlan(
            strategy="template", data_fields=["close"], templates=["khong_co"]
        ).validate()


def test_plan_rejects_invalid_lookback():
    with pytest.raises(PlanError):
        GenerationPlan(
            strategy="template", data_fields=["close"], lookbacks=[1]
        ).validate()


def test_plan_requires_project_when_hypothesis_is_set():
    with pytest.raises(PlanError):
        GenerationPlan(
            strategy="template", data_fields=["close"], hypothesis_id=3
        ).validate()


def test_valid_plan_round_trips_through_storage(db):
    plan = GenerationPlan(
        strategy="template", data_fields=["close", "volume"],
        templates=["scaled_decay"], lookbacks=[20, 60], max_candidates=25, seed=7,
        settings=SETTINGS, notes="thử nghiệm",
    )
    plan_id = plan.save(db)
    loaded = GenerationPlan.load(db, plan_id)
    assert loaded.strategy == "template"
    assert list(loaded.data_fields) == ["close", "volume"]
    assert list(loaded.lookbacks) == [20, 60]
    assert loaded.seed == 7
    assert loaded.settings == SETTINGS


def test_plan_describe_is_readable():
    text = GenerationPlan(strategy="template", data_fields=["close"], seed=3).describe()
    assert "chiến lược=template" in text
    assert "hạt giống=3" in text


def test_request_from_plan_carries_limits():
    plan = GenerationPlan(
        strategy="template", data_fields=["close"], lookbacks=[20, 60],
        max_candidates=15, seed=2,
    )
    request = request_from_plan(plan)
    assert request.limit == 15
    assert request.windows == (20, 60)
    assert request.seed == 2


# ======================================================================
# Phase 6 và 9: sinh có kiểm tra rồi mới vào hàng đợi
# ======================================================================
def test_generate_and_queue_only_queues_valid_expressions(db):
    plan = GenerationPlan(
        strategy="template", data_fields=["close", "volume"],
        max_candidates=10, seed=1, settings=SETTINGS,
    )
    outcome = generate_and_queue(db, plan, tag="lo1")
    assert outcome.generated > 0
    assert outcome.queued == outcome.valid - outcome.duplicates
    pending = db.fetch_by_status(Status.PENDING, limit=100)
    assert len(pending) == outcome.queued
    assert all(record.generation_strategy == "template" for record in pending)
    assert all(record.generation_seed == 1 for record in pending)


def test_invalid_expressions_are_stored_not_discarded(db):
    """Biểu thức hỏng vẫn là thông tin nghiên cứu.

    Dùng ràng buộc chỉ bộ kiểm tra biết, để biểu thức thật sự đi qua bước kiểm
    tra thay vì bị bộ sinh lọc trước.
    """
    plan = GenerationPlan(
        strategy="template", data_fields=["close"], max_candidates=5, seed=1,
        settings=SETTINGS, constraints={"forbidden_operators": ["rank", "ts_mean",
                                        "ts_zscore", "ts_delta", "ts_rank",
                                        "group_rank", "group_zscore",
                                        "ts_decay_linear", "winsorize",
                                        "group_neutralize", "trade_when",
                                        "ts_corr", "ts_regression", "ts_std_dev",
                                        "ts_mean"]},
    )
    outcome = generate_and_queue(db, plan)
    assert outcome.invalid > 0
    assert outcome.queued == 0
    invalid = db.fetch_by_status(Status.INVALID, limit=50)
    assert invalid
    assert all(record.validation_error for record in invalid)


def test_dry_run_writes_nothing(db):
    plan = GenerationPlan(
        strategy="template", data_fields=["close"], max_candidates=5, seed=1,
        settings=SETTINGS,
    )
    outcome = generate_and_queue(db, plan, dry_run=True)
    assert outcome.generated > 0
    assert db.counts_by_status()[Status.PENDING] == 0


def test_plan_id_is_stamped_on_queued_alphas(db):
    plan = GenerationPlan(
        strategy="template", data_fields=["close"], max_candidates=5, seed=1,
        settings=SETTINGS,
    )
    plan.save(db)
    generate_and_queue(db, plan)
    assert all(
        record.plan_id == plan.id for record in db.fetch_by_status(Status.PENDING)
    )


def test_generation_respects_research_constraints(db):
    plan = GenerationPlan(
        strategy="template", data_fields=["close", "volume"], max_candidates=20,
        seed=3, settings=SETTINGS, constraints={"allowed_fields": ["close"]},
    )
    generate_and_queue(db, plan)
    for record in db.fetch_by_status(Status.PENDING, limit=100):
        assert "volume" not in record.expression


# ======================================================================
# Phase 4: thí nghiệm có kiểm soát
# ======================================================================
def test_lookback_variants_change_only_the_window(store, hypothesis_id):
    design = ExperimentDesign(
        hypothesis_id=hypothesis_id, name="Lookback",
        base_expression="rank(ts_rank(returns, 20))",
        variable="lookback", values=[5, 10, 60],
    )
    variants = design.build_variants()
    assert [variant.expression for variant in variants] == [
        "rank(ts_rank(returns, 5))",
        "rank(ts_rank(returns, 10))",
        "rank(ts_rank(returns, 60))",
    ]


def test_experiment_needs_at_least_two_values(store, hypothesis_id):
    design = ExperimentDesign(
        hypothesis_id=hypothesis_id, name="E", base_expression="rank(close)",
        variable="lookback", values=[20],
    )
    with pytest.raises(ExperimentError, match="đối chứng"):
        design.build_variants()


def test_duplicate_values_are_rejected(store, hypothesis_id):
    design = ExperimentDesign(
        hypothesis_id=hypothesis_id, name="E",
        base_expression="rank(ts_mean(close, 20))",
        variable="lookback", values=[20, 20, 60],
    )
    with pytest.raises(ExperimentError, match="trùng"):
        design.build_variants()


def test_ambiguous_lookback_requires_a_placeholder(store, hypothesis_id):
    """Biểu thức có hai cửa sổ thì không rõ phải thay cái nào."""
    design = ExperimentDesign(
        hypothesis_id=hypothesis_id, name="E",
        base_expression="ts_mean(close, 20) - ts_mean(close, 60)",
        variable="lookback", values=[5, 10],
    )
    with pytest.raises(ExperimentError, match="chỗ giữ chỗ"):
        design.build_variants()


def test_placeholder_resolves_ambiguity(store, hypothesis_id):
    design = ExperimentDesign(
        hypothesis_id=hypothesis_id, name="E",
        base_expression="ts_mean(close, {lookback}) - ts_mean(close, 60)",
        variable="lookback", values=[5, 10],
    )
    variants = design.build_variants()
    assert variants[0].expression == "ts_mean(close, 5) - ts_mean(close, 60)"


def test_multiple_changes_require_explicit_opt_in(store, hypothesis_id):
    design = ExperimentDesign(
        hypothesis_id=hypothesis_id, name="E",
        base_expression="ts_corr(close, volume, 20)",
        variable="field", values=["open", "vwap"],
    )
    with pytest.raises(ExperimentError):
        design.build_variants()

    design.allow_multiple_changes = True
    assert design.build_variants()


def test_setting_variants_keep_the_expression_identical(store, hypothesis_id):
    design = ExperimentDesign(
        hypothesis_id=hypothesis_id, name="Neutralization",
        base_expression="rank(ts_mean(close, 20))",
        variable="neutralization", values=["SUBINDUSTRY", "MARKET"],
        settings=dict(SETTINGS),
    )
    variants = design.build_variants()
    assert {variant.expression for variant in variants} == {"rank(ts_mean(close, 20))"}
    assert variants[0].settings["neutralization"] == "SUBINDUSTRY"
    assert variants[1].settings["neutralization"] == "MARKET"


def test_engine_persists_experiment_and_variants(store, hypothesis_id):
    engine = ExperimentEngine(store)
    result = engine.create(
        ExperimentDesign(
            hypothesis_id=hypothesis_id, name="Lookback",
            base_expression="rank(ts_rank(returns, 20))",
            variable="lookback", values=[5, 20, 60],
            evaluation_criteria={"min_sharpe": 1.25, "min_sample": 3},
        )
    )
    assert len(result["variant_ids"]) == 3
    stored = store.list_variants(result["experiment_id"])
    assert len(stored) == 3
    assert store.get_experiment(result["experiment_id"])["variable_changed"] == "lookback"


def test_invalid_design_writes_nothing(store, hypothesis_id):
    """Thiết kế sai bị chặn trước khi ghi bản ghi nào."""
    engine = ExperimentEngine(store)
    with pytest.raises(ExperimentError):
        engine.create(
            ExperimentDesign(
                hypothesis_id=hypothesis_id, name="E",
                base_expression="rank(close)", variable="lookback", values=[5, 10],
            )
        )
    assert store.list_experiments(hypothesis_id) == []


def test_engine_builds_plan_from_experiment(store, hypothesis_id):
    engine = ExperimentEngine(store)
    result = engine.create(
        ExperimentDesign(
            hypothesis_id=hypothesis_id, name="Lookback",
            base_expression="rank(ts_rank(returns, 20))",
            variable="lookback", values=[5, 20, 60],
        )
    )
    plan = engine.build_plan(result["experiment_id"])
    assert plan.strategy == "mutate"
    assert plan.experiment_id == result["experiment_id"]
    assert len(plan.seed_expressions) == 3
    assert "returns" in plan.data_fields
    assert set(plan.lookbacks) == {5, 20, 60}


# ======================================================================
# Phase 11: độ bền
# ======================================================================
def test_profiles_differ_in_coverage():
    assert len(PROFILES["strict"]) > len(PROFILES["standard"])


def test_good_metrics_pass_standard_profile():
    report = RobustnessChecker(profile="standard").evaluate(GOOD_METRICS)
    assert report.passed
    assert report.coverage > 0


def test_excessive_drawdown_fails():
    report = RobustnessChecker(checks=[CHECK_DRAWDOWN]).evaluate(
        dict(GOOD_METRICS, drawdown=0.9)
    )
    assert not report.passed
    assert CHECK_DRAWDOWN in report.failures()


def test_mostly_negative_years_fail():
    metrics = dict(GOOD_METRICS, pnlByYear={"2022": -1.0, "2023": -0.5, "2024": 1.0})
    report = RobustnessChecker(checks=[CHECK_YEAR_BY_YEAR]).evaluate(metrics)
    assert not report.passed


def test_return_concentration_fails_when_one_year_dominates():
    metrics = dict(GOOD_METRICS, pnlByYear={"2022": 0.05, "2023": 0.05, "2024": 9.9})
    report = RobustnessChecker(checks=[CHECK_RETURN_CONCENTRATION]).evaluate(metrics)
    assert not report.passed


def test_missing_data_is_reported_as_unavailable_not_as_failure():
    """Thiếu dữ liệu khác với không đạt. Không được lẫn hai thứ."""
    report = RobustnessChecker(checks=[CHECK_YEAR_BY_YEAR]).evaluate({"sharpe": 1.5})
    assert report.passed          # không có bằng chứng thất bại
    assert report.coverage == 0.0  # nhưng cũng không có bằng chứng nào cả
    assert report.checks[0].status == "unavailable"


def test_parameter_sensitivity_needs_variants():
    report = RobustnessChecker(checks=[CHECK_PARAMETER_SENSITIVITY]).evaluate(GOOD_METRICS)
    assert report.checks[0].status == "unavailable"


def test_parameter_sensitivity_fails_when_variants_collapse():
    report = RobustnessChecker(checks=[CHECK_PARAMETER_SENSITIVITY]).evaluate(
        GOOD_METRICS, variants=[{"sharpe": 0.1}, {"sharpe": 0.2}],
    )
    assert not report.passed


def test_parameter_sensitivity_passes_when_variants_hold_up():
    report = RobustnessChecker(checks=[CHECK_PARAMETER_SENSITIVITY]).evaluate(
        GOOD_METRICS, variants=[{"sharpe": 1.7}, {"sharpe": 1.6}],
    )
    assert report.passed


def test_thresholds_are_configurable():
    strict = RobustnessChecker(
        checks=[CHECK_DRAWDOWN], thresholds=RobustnessThresholds(max_drawdown=0.05)
    )
    assert not strict.evaluate(GOOD_METRICS).passed


# ======================================================================
# Phase 12 và 13: thẩm định và ứng viên
# ======================================================================
def _simulated(db, expression, metrics, **extra):
    db.add_alphas([expression], SETTINGS, **extra)
    record = [
        r for r in db.fetch_by_status(Status.PENDING, limit=100)
        if r.expression == expression
    ][0]
    db.update_alpha(record.id, status=Status.PASSED, alpha_id=f"A{record.id}",
                    metrics=metrics)
    return record.id


def test_pipeline_promotes_good_alpha_through_the_ladder(db):
    row_id = _simulated(db, "rank(ts_mean(close, 20))", GOOD_METRICS)
    outcome = EvaluationPipeline(db, Scorer(SCORING)).run()
    assert outcome.scored == 1
    assert outcome.robust == 1
    record = db.get_alpha(row_id)
    assert record.evaluation_status == EvaluationStatus.CORRELATION_PASS
    assert record.robustness["passed"] is True


def test_low_score_stops_before_robustness(db):
    row_id = _simulated(db, "rank(ts_mean(close, 20))", dict(GOOD_METRICS, sharpe=0.2))
    outcome = EvaluationPipeline(db, Scorer(SCORING)).run()
    assert outcome.robust == 0
    record = db.get_alpha(row_id)
    assert record.status == Status.REJECTED
    assert record.evaluation_status == EvaluationStatus.SCORED


def test_fragile_alpha_is_rejected_at_robustness(db):
    row_id = _simulated(db, "rank(ts_mean(close, 20))", dict(GOOD_METRICS, drawdown=0.95))
    outcome = EvaluationPipeline(db, Scorer(SCORING), robustness=RobustnessChecker(
        checks=[CHECK_DRAWDOWN]
    )).run()
    assert outcome.robust_failed == 1
    assert db.get_alpha(row_id).evaluation_status == EvaluationStatus.ROBUST_FAILED


def test_structural_duplicate_is_caught_before_calling_the_server(db):
    """Trùng ý tưởng với alpha đã nộp thì không đáng tốn lượt gọi tương quan."""
    connection = db.connect()
    try:
        from alphaforge.history.fingerprint import fingerprint
        expression = "rank(ts_mean(close, 60))"
        meta = fingerprint(expression)
        connection.execute(
            "INSERT INTO historical_alphas (alpha_id, status, expression, family,"
            " source, imported_at) VALUES ('OLD', 'ACTIVE', ?, ?, 'brain_submitted', 'now')",
            (expression, meta["family"]),
        )
    finally:
        connection.close()

    row_id = _simulated(db, "rank(ts_mean(close, 20))", GOOD_METRICS)
    pipeline = EvaluationPipeline(db, Scorer(SCORING))
    outcome = pipeline.run()

    assert outcome.structural_duplicates == 1
    record = db.get_alpha(row_id)
    assert record.evaluation_status == EvaluationStatus.CORRELATION_FAILED
    history = pipeline.correlation_history(row_id)
    assert history[0]["correlation_type"] == "structural"
    assert history[0]["threshold"] is not None


def test_correlation_results_store_full_context(db):
    row_id = _simulated(db, "rank(close)", GOOD_METRICS)
    pipeline = EvaluationPipeline(db, Scorer(SCORING))
    pipeline.record_correlation(
        row_id, "A1", "self", value=0.82, reference="B2", threshold=0.7, status="FAILED"
    )
    entry = pipeline.correlation_history(row_id)[0]
    assert entry["correlation_value"] == 0.82
    assert entry["correlation_type"] == "self"
    assert entry["reference_alpha"] == "B2"
    assert entry["threshold"] == 0.7
    assert entry["status"] == "FAILED"


def test_candidate_promotion_requires_passing_correlation(db):
    row_id = _simulated(db, "rank(ts_mean(close, 20))", GOOD_METRICS)
    pipeline = EvaluationPipeline(db, Scorer(SCORING))

    # Chưa chạy thẩm định thì chưa thể thành ứng viên.
    assert pipeline.promote_to_candidate(row_id) is False

    pipeline.run()
    assert pipeline.promote_to_candidate(row_id) is True
    assert db.get_alpha(row_id).status == Status.CANDIDATE


def test_submission_requires_human_controlled_state(db):
    """Hệ thống không tự nộp. Chỉ ghi nhận khi alpha đã ở bậc do người kiểm soát."""
    row_id = _simulated(db, "rank(ts_mean(close, 20))", GOOD_METRICS)
    pipeline = EvaluationPipeline(db, Scorer(SCORING))
    pipeline.run()

    # Đang ở PASSED, chưa phải ứng viên.
    assert pipeline.mark_submitted(row_id) is False

    pipeline.promote_to_candidate(row_id)
    assert pipeline.send_to_review(row_id) is True
    assert pipeline.mark_submitted(row_id, confirmed_by="nguoi_dung") is True
    assert db.get_alpha(row_id).status == Status.SUBMITTED


def test_evaluation_never_produces_submitted_on_its_own(db):
    for index in range(5):
        _simulated(db, f"rank(ts_mean(f{index}, 20))", GOOD_METRICS)
    EvaluationPipeline(db, Scorer(SCORING)).run()
    assert db.counts_by_status()[Status.SUBMITTED] == 0


def test_experiment_feedback_reaches_memory(db, store, hypothesis_id):
    """Phase 14: kết quả chạy quay lại trí nhớ nghiên cứu."""
    engine = ExperimentEngine(store)
    result = engine.create(
        ExperimentDesign(
            hypothesis_id=hypothesis_id, name="Lookback",
            base_expression="rank(ts_rank(returns, 20))",
            variable="lookback", values=[5, 20, 60],
        )
    )
    experiment_id = result["experiment_id"]
    for index, spec in enumerate(result["variants"]):
        row_id = _simulated(
            db, spec["expression"],
            dict(GOOD_METRICS, sharpe=1.8 if index else 0.2),
            experiment_id=experiment_id,
        )
    EvaluationPipeline(db, Scorer(SCORING)).run()

    outcome = ResearchMemory(db).experiment_outcome(experiment_id)
    assert outcome["total"] == 3
    assert outcome["passed"] >= 1
    assert outcome["sample_size"] == 3
