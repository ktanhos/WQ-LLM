"""Danh mục mẫu biểu thức.

Mỗi mẫu là một khuôn có chỗ trống. Chỗ trống được điền bằng trường dữ liệu,
cửa sổ thời gian hoặc nhóm phân loại. Cách tiếp cận theo mẫu cho kết quả tái lập
được và dễ truy vết nguồn gốc của từng biểu thức, đổi lại độ đa dạng thấp hơn
so với hướng dùng mô hình sinh.

Ký hiệu chỗ trống:
    {field}, {field_a}, {field_b}   tên trường dữ liệu dạng ma trận
    {d}, {d2}                       số ngày của cửa sổ
    {group}                         nhóm phân loại dùng cho các toán tử group
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Sequence


@dataclass(frozen=True)
class Template:
    name: str
    pattern: str
    category: str
    slots: Sequence[str] = field(default_factory=tuple)
    note: str = ""


GROUPS = (
    "subindustry",
    "industry",
    "sector",
    "market",
)

WINDOWS = (5, 10, 20, 60, 120, 250)


TEMPLATES: List[Template] = [
    Template(
        name="rank_reversal",
        pattern="-rank(ts_delta({field}, {d}))",
        category="reversal",
        slots=("field", "d"),
        note="Đảo chiều theo mức thay đổi của trường dữ liệu.",
    ),
    Template(
        name="zscore_reversal",
        pattern="-ts_zscore({field}, {d})",
        category="reversal",
        slots=("field", "d"),
    ),
    Template(
        name="momentum",
        pattern="ts_delta({field}, {d}) / (ts_std_dev({field}, {d}) + 1e-6)",
        category="momentum",
        slots=("field", "d"),
        note="Chuẩn hóa động lượng theo độ biến động cùng cửa sổ.",
    ),
    Template(
        name="group_neutral_rank",
        pattern="group_rank({field}, {group})",
        category="cross_sectional",
        slots=("field", "group"),
    ),
    Template(
        name="group_zscore",
        pattern="group_zscore({field}, {group})",
        category="cross_sectional",
        slots=("field", "group"),
    ),
    Template(
        name="ratio_rank",
        pattern="rank({field_a} / ({field_b} + 1e-6))",
        category="value",
        slots=("field_a", "field_b"),
        note="Tỷ số hai trường, thường dùng cho nhóm dữ liệu cơ bản.",
    ),
    Template(
        name="diff_rank",
        pattern="rank({field_a}) - rank({field_b})",
        category="value",
        slots=("field_a", "field_b"),
    ),
    Template(
        name="ts_rank_signal",
        pattern="ts_rank({field}, {d}) - 0.5",
        category="time_series",
        slots=("field", "d"),
    ),
    Template(
        name="mean_deviation",
        pattern="({field} - ts_mean({field}, {d})) / (ts_std_dev({field}, {d}) + 1e-6)",
        category="time_series",
        slots=("field", "d"),
    ),
    Template(
        name="corr_signal",
        pattern="-ts_corr({field_a}, {field_b}, {d})",
        category="relation",
        slots=("field_a", "field_b", "d"),
    ),
    Template(
        name="regression_residual",
        pattern="ts_regression({field_a}, {field_b}, {d}, rettype=0)",
        category="relation",
        slots=("field_a", "field_b", "d"),
        note="Phần dư hồi quy theo chuỗi thời gian, rettype 0 là residual.",
    ),
    Template(
        name="scaled_decay",
        pattern="ts_decay_linear(rank({field}), {d})",
        category="smoothing",
        slots=("field", "d"),
        note="Làm mượt tín hiệu để hạ vòng quay danh mục.",
    ),
    Template(
        name="winsorized_signal",
        pattern="winsorize(ts_zscore({field}, {d}), std=4)",
        category="robustness",
        slots=("field", "d"),
    ),
    Template(
        name="conditional_signal",
        pattern="trade_when(ts_std_dev(returns, {d2}) < ts_mean(ts_std_dev(returns, {d2}), {d}), -ts_delta({field}, {d2}), -1)",
        category="conditional",
        slots=("field", "d", "d2"),
        note="Chỉ vào lệnh khi độ biến động thấp hơn mức trung bình dài hạn.",
    ),
    Template(
        name="double_group_neutral",
        pattern="group_neutralize(ts_zscore({field}, {d}), {group})",
        category="cross_sectional",
        slots=("field", "d", "group"),
    ),
    Template(
        name="vector_signal",
        pattern="rank(ts_mean({field}, {d})) - rank(ts_mean({field}, {d2}))",
        category="momentum",
        slots=("field", "d", "d2"),
        note="Chênh lệch hai cửa sổ, tương tự giao cắt trung bình động.",
    ),
]


TEMPLATES_BY_NAME = {template.name: template for template in TEMPLATES}
TEMPLATE_CATEGORIES = sorted({template.category for template in TEMPLATES})
