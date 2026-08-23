"""Kiểm thử trí nhớ nghiên cứu và ảnh hưởng của nó lên bộ sinh."""

import json

from alphaforge.generator.engine import GenerationRequest, GeneratorEngine
from alphaforge.history.fingerprint import fingerprint
from alphaforge.research.memory import (
    MIN_SAMPLE,
    GenerationContext,
    ResearchMemory,
    ResearchProfile,
)
from alphaforge.storage.db import Status

FIELDS = ["close", "volume", "returns", "cap", "vwap"]
SETTINGS = {"region": "USA", "universe": "TOP3000", "delay": 1}


def _seed_history(db, expression, count, sharpe, status="ACTIVE"):
    """Nạp `count` alpha lịch sử cùng một họ cấu trúc."""
    meta = fingerprint(expression)
    # Mã alpha phải duy nhất kể cả khi hai lời gọi dùng chung một họ cấu trúc,
    # nên lấy vân tay chính xác chứ không lấy vân tay họ.
    prefix = meta["exact"][:8]
    connection = db.connect()
    try:
        for index in range(count):
            connection.execute(
                """
                INSERT INTO historical_alphas (
                    alpha_id, submitted, status, expression, sharpe, source,
                    fingerprint, family, template, operators_json, fields_json,
                    windows_json, imported_at
                ) VALUES (?, ?, ?, ?, ?, 'brain_submitted', ?, ?, ?, ?, ?, ?, 'now')
                """,
                (
                    f"{prefix}-{status}-{index}", "2026-08-01", status,
                    expression, sharpe, meta["family"], meta["family"],
                    meta["template"], json.dumps(meta["operators"]),
                    json.dumps(meta["fields"]), json.dumps(meta["windows"]),
                ),
            )
    finally:
        connection.close()


# ----------------------------------------------------------------------
# Hồ sơ họ cấu trúc
# ----------------------------------------------------------------------
def test_profile_counts_and_pass_rate(db):
    _seed_history(db, "rank(ts_mean(returns, 20))", 6, 1.8, status="ACTIVE")
    _seed_history(db, "rank(ts_mean(returns, 60))", 4, 0.3, status="FAILED")

    profiles = ResearchMemory(db).profiles()
    # Hai biểu thức chỉ khác cửa sổ nên thuộc cùng một họ.
    assert len(profiles) == 1
    profile = next(iter(profiles.values()))
    assert profile.count == 10
    assert profile.passed == 6
    assert profile.pass_rate == 0.6


def test_profile_records_tested_windows_and_fields(db):
    _seed_history(db, "ts_mean(close, 20)", 2, 1.0)
    _seed_history(db, "ts_mean(close, 60)", 2, 1.0)
    profile = next(iter(ResearchMemory(db).profiles().values()))
    assert set(profile.windows) == {20, 60}
    assert profile.fields == ["close"]


def test_generated_alphas_also_count_towards_history(db):
    """Alpha tự sinh cũng là bằng chứng một cấu trúc đã được thử."""
    db.add_alphas(["rank(ts_mean(close, 20))"], SETTINGS)
    record = db.fetch_by_status(Status.PENDING)[0]
    meta = fingerprint(record.expression)
    db.update_alpha(
        record.id, status=Status.REJECTED, family=meta["family"],
        fingerprint=meta["family"], metrics={"sharpe": 0.4},
    )
    profiles = ResearchMemory(db).profiles()
    assert profiles[meta["family"]].count == 1


def test_empty_history_produces_neutral_context(db):
    context = ResearchMemory(db).build_context()
    assert context.family_weights == {}
    assert context.weight_for("rank(close)") == 1.0


# ----------------------------------------------------------------------
# Trọng số
# ----------------------------------------------------------------------
def test_saturated_low_performing_family_is_deprioritised(db):
    _seed_history(db, "rank(ts_mean(volume, 20))", 60, 0.2, status="FAILED")
    context = ResearchMemory(db).build_context(target_sharpe=1.25)
    weight = context.weight_for("rank(ts_mean(volume, 20))")
    assert weight < 1.0


def test_high_performing_family_is_not_penalised(db):
    _seed_history(db, "rank(ts_mean(returns, 20))", 40, 2.5, status="ACTIVE")
    context = ResearchMemory(db).build_context(target_sharpe=1.25)
    assert context.weight_for("rank(ts_mean(returns, 20))") > 1.0


def test_small_sample_family_keeps_neutral_weight(db):
    _seed_history(db, "rank(ts_mean(cap, 20))", 3, 0.1, status="FAILED")
    context = ResearchMemory(db).build_context(target_sharpe=1.25)
    assert context.weight_for("rank(ts_mean(cap, 20))") == 1.0


def test_weight_never_reaches_zero(db):
    """Không họ nào bị loại hẳn: thị trường đổi trạng thái thì họ cũ có thể tốt lại."""
    _seed_history(db, "rank(ts_mean(volume, 20))", 200, 0.01, status="FAILED")
    context = ResearchMemory(db).build_context(target_sharpe=1.25)
    assert context.weight_for("rank(ts_mean(volume, 20))") > 0.0


def test_unknown_family_defaults_to_neutral_weight(db):
    _seed_history(db, "rank(ts_mean(volume, 20))", 30, 0.2)
    context = ResearchMemory(db).build_context()
    assert context.weight_for("ts_corr(close, vwap, 60)") == 1.0


# ----------------------------------------------------------------------
# Trả lời câu hỏi nghiên cứu
# ----------------------------------------------------------------------
def test_describe_family_reports_not_researched_for_new_structure(db):
    result = ResearchMemory(db).describe_family("rank(close)")
    assert result["researched"] is False
    assert result["count"] == 0


def test_describe_family_answers_what_has_been_tried(db):
    _seed_history(db, "rank(ts_mean(returns, 20))", 5, 1.4)
    _seed_history(db, "rank(ts_mean(returns, 60))", 5, 2.2)
    result = ResearchMemory(db).describe_family("rank(ts_mean(returns, 120))")
    assert result["researched"] is True
    assert result["count"] == 10
    assert set(result["tested_windows"]) == {20, 60}
    assert result["best_sharpe"] == 2.2
    assert result["tested_fields"] == ["returns"]


def test_saturated_flag_requires_no_passes(db):
    _seed_history(db, "rank(ts_mean(volume, 20))", MIN_SAMPLE + 2, 0.2, status="FAILED")
    result = ResearchMemory(db).describe_family("rank(ts_mean(volume, 20))")
    assert result["is_saturated"] is True


# ----------------------------------------------------------------------
# Tích hợp với bộ sinh
# ----------------------------------------------------------------------
def test_generator_without_context_is_unchanged():
    """Không có trí nhớ thì bộ sinh phải cho kết quả y hệt trước đây."""
    first = GeneratorEngine(
        GenerationRequest(strategy="template", limit=25, fields=FIELDS, seed=42)
    ).generate()
    second = GeneratorEngine(
        GenerationRequest(strategy="template", limit=25, fields=FIELDS, seed=42)
    ).generate()
    assert first == second
    assert len(first) == 25


def test_generator_skips_expressions_already_in_history(db):
    known = "rank(ts_mean(close, 20))"
    _seed_history(db, known, 1, 1.5)
    context = ResearchMemory(db).build_context()

    engine = GeneratorEngine(
        GenerationRequest(
            strategy="mutate", limit=10, fields=FIELDS, seed=1,
            seed_expressions=[known], context=context,
        )
    )
    # Biểu thức gốc bị bọc thêm toán tử nên không trùng, nhưng chính nó thì có.
    assert known not in engine.generate()


def test_generator_reports_why_candidates_were_dropped(db):
    _seed_history(db, "rank(ts_mean(close, 20))", 80, 0.05, status="FAILED")
    context = ResearchMemory(db).build_context(target_sharpe=1.25)
    engine = GeneratorEngine(
        GenerationRequest(
            strategy="template", limit=30, fields=["close"], seed=7,
            templates=["scaled_decay"], context=context,
        )
    )
    engine.generate()
    assert sum(engine.rejected.values()) > 0


def test_disabled_context_behaves_like_no_context():
    context = GenerationContext(enabled=False)
    engine = GeneratorEngine(
        GenerationRequest(strategy="template", limit=20, fields=FIELDS, seed=5, context=context)
    )
    plain = GeneratorEngine(
        GenerationRequest(strategy="template", limit=20, fields=FIELDS, seed=5)
    )
    assert engine.generate() == plain.generate()


def test_profile_helpers_flag_underexplored():
    profile = ResearchProfile(family="F", count=2, passed=0)
    assert profile.is_underexplored is True
    assert profile.is_saturated is False
