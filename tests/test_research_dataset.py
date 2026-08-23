"""Trí nhớ nghiên cứu trên tập dữ liệu giả lập cỡ vừa.

Tập gồm 50 alpha chia thành bốn họ cấu trúc có đặc điểm khác nhau, dùng để
kiểm tra thống kê, mức bão hòa và cách bộ sinh phản ứng. Không có dữ liệu thật
và không có lệnh gọi mạng nào.
"""

import json

import pytest

from alphaforge.generator.engine import GenerationRequest, GeneratorEngine
from alphaforge.history.analyzer import analyze, describe, research_gaps
from alphaforge.history.fingerprint import fingerprint
from alphaforge.research.memory import GenerationContext, ResearchMemory

# Bốn họ với hồ sơ lịch sử khác nhau:
#   BÃO HÒA      thử rất nhiều, gần như không đạt
#   TỐT          thử nhiều, kết quả tốt đều
#   MỚI          mới thử vài lần, kết quả khả quan
#   HIẾM         chỉ có hai quan sát, không đủ kết luận
DATASET = (
    # (biểu thức, số lượng, sharpe, trạng thái)
    ("rank(ts_mean(volume, {w}))", 25, 0.2, "FAILED"),
    ("group_neutralize(ts_zscore(returns, {w}), subindustry)", 15, 2.2, "ACTIVE"),
    ("ts_corr(close, vwap, {w})", 8, 1.7, "ACTIVE"),
    ("winsorize(ts_delta(cap, {w}), std=4)", 2, 3.4, "ACTIVE"),
)
WINDOWS = (5, 10, 20, 60, 120)


@pytest.fixture()
def seeded(db):
    """Nạp 50 alpha lịch sử vào kho."""
    connection = db.connect()
    total = 0
    try:
        for template, count, sharpe, status in DATASET:
            for index in range(count):
                expression = template.format(w=WINDOWS[index % len(WINDOWS)])
                meta = fingerprint(expression)
                connection.execute(
                    """
                    INSERT INTO historical_alphas (
                        alpha_id, submitted, status, region, universe, delay,
                        expression, sharpe, fitness, turnover, source,
                        fingerprint, family, template, operators_json,
                        fields_json, windows_json, imported_at
                    ) VALUES (?, '2026-08-15', ?, 'USA', 'TOP3000', 1, ?, ?, 1.1,
                              0.3, 'brain_submitted', ?, ?, ?, ?, ?, ?, 'now')
                    """,
                    (
                        f"{meta['exact'][:8]}-{index}", status, expression,
                        # Biến thiên nhẹ quanh giá trị trung tâm để có phân tán.
                        sharpe + (index % 5) * 0.05,
                        meta["family"], meta["family"], meta["template"],
                        json.dumps(meta["operators"]), json.dumps(meta["fields"]),
                        json.dumps(meta["windows"]),
                    ),
                )
                total += 1
    finally:
        connection.close()
    assert total == 50
    return db


def _family(template):
    return fingerprint(template.format(w=20))["family"]


SATURATED = _family(DATASET[0][0])
STRONG = _family(DATASET[1][0])
FRESH = _family(DATASET[2][0])
RARE = _family(DATASET[3][0])


# ======================================================================
# Mục 10: thống kê trên tập dữ liệu
# ======================================================================
def test_dataset_has_fifty_alphas(seeded):
    assert analyze(ResearchMemory(seeded).load_rows())["total"] == 50


def test_family_frequency(seeded):
    profiles = ResearchMemory(seeded).profiles()
    assert profiles[SATURATED].count == 25
    assert profiles[STRONG].count == 15
    assert profiles[FRESH].count == 8
    assert profiles[RARE].count == 2


def test_field_frequency(seeded):
    rows = ResearchMemory(seeded).load_rows()
    distribution = {
        item["field"]: item["count"]
        for item in analyze(rows)["distribution"]["field"]
    }
    assert distribution["volume"] == 25
    assert distribution["returns"] == 15
    # ts_corr(close, vwap, w) đóng góp cả hai trường.
    assert distribution["close"] == 8
    assert distribution["vwap"] == 8
    assert distribution["cap"] == 2


def test_operator_frequency(seeded):
    rows = ResearchMemory(seeded).load_rows()
    distribution = {
        item["operator"]: item["count"]
        for item in analyze(rows)["distribution"]["operator"]
    }
    assert distribution["ts_mean"] == 25
    assert distribution["group_neutralize"] == 15
    assert distribution["ts_corr"] == 8
    assert distribution["winsorize"] == 2


def test_summary_statistics_are_complete(seeded):
    stats = analyze(ResearchMemory(seeded).load_rows())["metrics"]["sharpe"]
    for key in ("count", "mean", "median", "p25", "p75", "min", "max", "stdev"):
        assert stats[key] is not None, f"thiếu {key}"
    assert stats["count"] == 50
    assert stats["p25"] <= stats["median"] <= stats["p75"]
    assert stats["min"] <= stats["p25"]
    assert stats["max"] >= stats["p75"]


def test_median_is_not_dragged_by_the_rare_high_family(seeded):
    """Họ HIẾM có Sharpe cao nhất nhưng chỉ hai quan sát.

    Trung vị toàn tập phải phản ánh khối đông alpha kém, không phải ngoại lai.
    """
    stats = analyze(ResearchMemory(seeded).load_rows())["metrics"]["sharpe"]
    assert stats["max"] >= 3.4
    assert stats["median"] < 2.0


def test_per_family_sample_size_is_reported(seeded):
    families = {
        item["family"]: item
        for item in analyze(ResearchMemory(seeded).load_rows())["distribution"]["family"]
    }
    assert families[RARE]["sharpe"]["count"] == 2
    assert families[SATURATED]["sharpe"]["count"] == 25


def test_saturation_score_ranks_families_correctly(seeded):
    profiles = ResearchMemory(seeded).profiles()
    assert profiles[SATURATED].saturation > profiles[FRESH].saturation
    assert profiles[SATURATED].saturation > profiles[STRONG].saturation
    # Họ tốt tuy thử nhiều nhưng đạt đều nên không bị coi là bão hòa.
    assert profiles[STRONG].saturation < 0.5


def test_confidence_is_low_for_the_rare_family(seeded):
    profiles = ResearchMemory(seeded).profiles()
    assert profiles[RARE].confidence < 0.2
    assert profiles[SATURATED].confidence > profiles[RARE].confidence


def test_rare_family_is_not_ranked_as_high_performing(seeded):
    """Cỡ mẫu hai không đủ để kết luận, dù Sharpe cao nhất tập."""
    analysis = analyze(ResearchMemory(seeded).load_rows(), min_sample=5)
    ranked = {item["family"] for item in analysis["high_performing_structures"]}
    assert RARE not in ranked
    assert STRONG in ranked


def test_rare_family_is_listed_as_underexplored(seeded):
    analysis = analyze(ResearchMemory(seeded).load_rows(), underexplored_threshold=5)
    assert RARE in {item["family"] for item in analysis["underexplored_structures"]}


def test_saturated_family_is_listed_as_frequently_tested(seeded):
    analysis = analyze(ResearchMemory(seeded).load_rows(), saturated_threshold=20)
    assert SATURATED in {item["family"] for item in analysis["frequently_tested_structures"]}


def test_pass_rate_reflects_the_failing_family(seeded):
    families = {
        item["family"]: item
        for item in analyze(ResearchMemory(seeded).load_rows())["distribution"]["family"]
    }
    assert families[SATURATED]["pass_rate"] == 0.0
    assert families[STRONG]["pass_rate"] == 1.0


def test_research_priority_favours_lightly_tested_families(seeded):
    gaps = {item["family"]: item["priority"] for item in
            research_gaps(analyze(ResearchMemory(seeded).load_rows()))}
    assert gaps[FRESH] > gaps[SATURATED]


def test_tested_windows_recorded_per_family(seeded):
    profiles = ResearchMemory(seeded).profiles()
    assert set(profiles[SATURATED].windows) == set(WINDOWS)
    # Họ HIẾM chỉ có hai quan sát nên chỉ thử được hai cửa sổ đầu.
    assert set(profiles[RARE].windows) == {WINDOWS[0], WINDOWS[1]}


def test_small_sample_does_not_produce_a_strong_claim(seeded):
    """describe_family phải nói rõ nhóm còn ít quan sát."""
    result = ResearchMemory(seeded).describe_family(DATASET[3][0].format(w=20))
    assert result["count"] == 2
    assert result["is_underexplored"] is True
    assert result["confidence"] < 0.2


def test_describe_on_two_point_group_still_returns_numbers(seeded):
    """Không được ném lỗi khi cỡ mẫu bằng hai, chỉ được báo độ tin cậy thấp."""
    stats = describe([3.4, 3.5])
    assert stats["count"] == 2
    assert stats["median"] == pytest.approx(3.45)
    assert stats["stdev"] is not None


# ======================================================================
# Mục 11: bộ sinh dùng trí nhớ
# ======================================================================
def test_context_exposes_everything_the_generator_needs(seeded):
    context = ResearchMemory(seeded).build_context(target_sharpe=1.25)
    assert context.family_weights
    assert context.tested_windows[SATURATED]
    assert "volume" in context.tested_fields[SATURATED]
    assert "ts_mean" in context.tested_operators[SATURATED]
    assert context.family_saturation[SATURATED] > 0
    assert context.family_priority[SATURATED] < 1.0


def test_context_lookup_helpers(seeded):
    context = ResearchMemory(seeded).build_context()
    saturated_expression = DATASET[0][0].format(w=20)
    # Họ bão hòa: 25 alpha, không alpha nào đạt -> 25/50 * (1 - 0) = 0.5
    assert context.saturation_for(saturated_expression) == pytest.approx(0.5)
    assert context.priority_for(saturated_expression) == pytest.approx(0.5)
    fresh_expression = DATASET[2][0].format(w=20)
    assert context.saturation_for(saturated_expression) > context.saturation_for(fresh_expression)
    assert context.has_tried_field(saturated_expression, "volume") is True
    assert context.has_tried_field(saturated_expression, "cap") is False


def test_unseen_family_gets_maximum_priority(seeded):
    context = ResearchMemory(seeded).build_context()
    assert context.priority_for("ts_regression(open, high, 20, rettype=0)") == 1.0
    assert context.saturation_for("ts_regression(open, high, 20, rettype=0)") == 0.0


def test_generator_prefers_the_less_saturated_direction(seeded):
    """Với cùng hai lựa chọn, họ bão hòa phải xuất hiện ít hơn.

    So sánh trên nhiều hạt giống vì việc hạ ưu tiên là xác suất, không tuyệt đối.
    """
    context = ResearchMemory(seeded).build_context(target_sharpe=1.25)
    saturated_count = fresh_count = 0
    for seed in range(30):
        expressions = GeneratorEngine(
            GenerationRequest(
                strategy="template", limit=20, fields=["volume", "close"],
                templates=["scaled_decay"], seed=seed, context=context,
                skip_known=False,
            )
        ).generate()
        saturated_count += sum(1 for e in expressions if "volume" in e)
        fresh_count += sum(1 for e in expressions if "close" in e)
    assert saturated_count < fresh_count


def test_generator_still_produces_output_when_everything_is_saturated(seeded):
    """Bão hòa không được làm bộ sinh trả về rỗng."""
    context = ResearchMemory(seeded).build_context(target_sharpe=1.25)
    expressions = GeneratorEngine(
        GenerationRequest(
            strategy="template", limit=15, fields=["volume"], seed=1,
            context=context, skip_known=False,
        )
    ).generate()
    assert expressions


def test_generator_accepts_a_hand_built_context_without_a_database():
    """Bộ sinh chỉ phụ thuộc vào GenerationContext, không phụ thuộc kho dữ liệu."""
    context = GenerationContext(
        family_weights={fingerprint("rank(ts_mean(volume, 20))")["family"]: 0.15},
        family_saturation={fingerprint("rank(ts_mean(volume, 20))")["family"]: 0.95},
    )
    expressions = GeneratorEngine(
        GenerationRequest(
            strategy="template", limit=10, fields=["volume", "returns"],
            seed=5, context=context,
        )
    ).generate()
    assert expressions
