"""Kiểm thử vân tay cấu trúc và phát hiện trùng lặp."""

from alphaforge.history.fingerprint import (
    RELATION_EXACT,
    RELATION_FIELD,
    RELATION_PARAMETER,
    RELATION_SAME_OPERATORS_DIFFERENT_FIELDS,
    RELATION_UNRELATED,
    compare,
    family_of,
    fingerprint,
    find_duplicates,
    template_of,
)


# ----------------------------------------------------------------------
# Ba mức trừu tượng
# ----------------------------------------------------------------------
def test_same_structure_different_window_shares_family():
    """Đổi cửa sổ thời gian là đổi tham số, không phải đổi ý tưởng."""
    a = fingerprint("rank(ts_mean(returns, 20))")
    b = fingerprint("rank(ts_mean(returns, 60))")
    assert a["family"] == b["family"]
    assert a["exact"] != b["exact"]


def test_same_structure_different_field_differs_at_family_but_shares_template():
    """Đổi trường dữ liệu là một thí nghiệm khác, nhưng vẫn cùng khuôn."""
    a = fingerprint("rank(ts_mean(returns, 20))")
    c = fingerprint("rank(ts_mean(volume, 20))")
    assert a["family"] != c["family"]
    assert a["template"] == c["template"]


def test_all_three_levels_agree_for_identical_expressions():
    a = fingerprint("rank(ts_mean(returns, 20))")
    b = fingerprint("rank( ts_mean( returns , 20 ) )")
    assert a["exact"] == b["exact"]
    assert a["family"] == b["family"]
    assert a["template"] == b["template"]


def test_case_is_normalized():
    assert fingerprint("RANK(close)")["exact"] == fingerprint("rank(close)")["exact"]


def test_legacy_hash_key_still_maps_to_family():
    """Mã và dữ liệu cũ dùng khóa hash. Nó phải tiếp tục là mức family."""
    a = fingerprint("rank(ts_mean(returns, 20))")
    assert a["hash"] == a["family"]


def test_shortcut_helpers_match_full_fingerprint():
    expression = "group_neutralize(ts_zscore(close, 20), subindustry)"
    assert family_of(expression) == fingerprint(expression)["family"]
    assert template_of(expression) == fingerprint(expression)["template"]


# ----------------------------------------------------------------------
# Nhận diện thành phần
# ----------------------------------------------------------------------
def test_operator_detected_by_syntax_not_by_fixed_list():
    """Toán tử chưa từng biết vẫn được nhận ra nhờ dấu mở ngoặc đứng sau."""
    meta = fingerprint("toan_tu_moi_chua_tung_co(close, 10)")
    assert "toan_tu_moi_chua_tung_co" in meta["operators"]
    assert "close" in meta["fields"]


def test_group_token_is_not_counted_as_data_field():
    meta = fingerprint("group_neutralize(close, subindustry)")
    assert "subindustry" in meta["groups"]
    assert "subindustry" not in meta["fields"]
    assert meta["fields"] == ["close"]


def test_keyword_argument_name_is_not_a_data_field():
    """std trong winsorize(x, std=4) là tên đối số, không phải trường dữ liệu."""
    meta = fingerprint("winsorize(ts_zscore(close, 20), std=4)")
    assert "std" not in meta["fields"]
    assert "std" in meta["keyword_args"]
    assert meta["fields"] == ["close"]


def test_lookback_windows_extracted_from_timeseries_operators():
    meta = fingerprint("ts_mean(close, 20) - ts_mean(close, 60)")
    assert meta["windows"] == [20, 60]


def test_keyword_values_are_not_treated_as_lookback_windows():
    """rettype=0 là cờ chế độ, không phải cửa sổ nhìn lại."""
    meta = fingerprint("ts_regression(close, open, 20, rettype=0)")
    assert meta["windows"] == [20]


def test_neutralization_and_condition_are_flagged():
    meta = fingerprint("trade_when(volume > 0, group_neutralize(close, industry), -1)")
    assert meta["has_neutralization"] is True
    assert meta["has_condition"] is True
    assert "group_neutralize" in meta["neutralization"]


def test_depth_and_complexity_increase_with_nesting():
    shallow = fingerprint("rank(close)")
    deep = fingerprint("rank(ts_decay_linear(ts_zscore(ts_mean(close, 20), 10), 5))")
    assert deep["depth"] > shallow["depth"]
    assert deep["complexity"] > shallow["complexity"]


def test_empty_expression_does_not_raise():
    meta = fingerprint("")
    assert meta["operators"] == []
    assert meta["fields"] == []
    assert meta["depth"] == 0


def test_fingerprint_is_deterministic_across_calls():
    first = fingerprint("rank(ts_corr(close, volume, 20))")
    second = fingerprint("rank(ts_corr(close, volume, 20))")
    assert first == second


# ----------------------------------------------------------------------
# Phân loại trùng lặp
# ----------------------------------------------------------------------
def test_compare_detects_exact_duplicate():
    result = compare("rank(close)", "rank( close )")
    assert result["relation"] == RELATION_EXACT
    assert result["is_duplicate"] is True


def test_compare_detects_parameter_only_difference():
    result = compare("ts_mean(close, 20)", "ts_mean(close, 60)")
    assert result["relation"] == RELATION_PARAMETER
    assert result["is_duplicate"] is True


def test_compare_detects_field_only_difference():
    result = compare("ts_mean(close, 20)", "ts_mean(volume, 20)")
    assert result["relation"] == RELATION_FIELD


def test_compare_detects_same_operators_different_fields():
    result = compare(
        "rank(ts_mean(close, 20)) + zscore(open)",
        "rank(ts_mean(volume, 20)) + zscore(vwap)",
    )
    assert result["relation"] in (
        RELATION_FIELD, RELATION_SAME_OPERATORS_DIFFERENT_FIELDS,
    )


def test_compare_marks_unrelated_expressions():
    result = compare("rank(close)", "trade_when(ts_std_dev(returns, 20) < 1, cap, -1)")
    assert result["relation"] == RELATION_UNRELATED
    assert result["is_duplicate"] is False


def test_find_duplicates_orders_strictest_relation_first():
    candidates = [
        "ts_mean(volume, 20)",      # khác trường
        "ts_mean(close, 60)",       # khác tham số
        "ts_mean(close, 20)",       # trùng khít
        "rank(cap)",                # không liên quan
    ]
    matches = find_duplicates("ts_mean(close, 20)", candidates)
    assert matches[0]["relation"] == RELATION_EXACT
    assert matches[1]["relation"] == RELATION_PARAMETER
    assert all(match["relation"] != RELATION_UNRELATED for match in matches)


def test_find_duplicates_returns_empty_for_no_matches():
    assert find_duplicates("rank(close)", []) == []
