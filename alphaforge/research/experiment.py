"""Thiết kế thí nghiệm có kiểm soát.

Một thí nghiệm chỉ có giá trị khi nó thay đổi **một** biến và giữ nguyên phần
còn lại. Nếu đổi đồng thời cửa sổ nhìn lại, trường dữ liệu và cách trung tính
hóa rồi thấy kết quả tốt lên, không thể quy kết quả đó cho yếu tố nào.

Vì vậy mặc định của mô đun này là:

    MỘT THÍ NGHIỆM = MỘT THAY ĐỔI THIẾT KẾ CHÍNH

Muốn đổi nhiều biến cùng lúc vẫn được, nhưng phải khai báo rõ ràng bằng
`allow_multiple_changes=True`. Khai báo tường minh khiến người thiết kế phải
ý thức rằng kết quả sẽ khó quy kết, thay vì vô tình rơi vào tình huống đó.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from ..history.fingerprint import fingerprint
from .models import Experiment, ExperimentVariant
from .plan import GenerationPlan
from .store import ResearchStore

#: Các biến thiết kế mà một thí nghiệm có thể khảo sát.
VARIABLE_LOOKBACK = "lookback"
VARIABLE_FIELD = "field"
VARIABLE_OPERATOR = "operator"
VARIABLE_NEUTRALIZATION = "neutralization"
VARIABLE_DECAY = "decay"
VARIABLE_TRUNCATION = "truncation"
VARIABLE_UNIVERSE = "universe"
VARIABLE_REGION = "region"
VARIABLE_DELAY = "delay"

VARIABLES = (
    VARIABLE_LOOKBACK, VARIABLE_FIELD, VARIABLE_OPERATOR,
    VARIABLE_NEUTRALIZATION, VARIABLE_DECAY, VARIABLE_TRUNCATION,
    VARIABLE_UNIVERSE, VARIABLE_REGION, VARIABLE_DELAY,
)

#: Biến thuộc về thiết lập mô phỏng chứ không nằm trong biểu thức.
SETTING_VARIABLES = {
    VARIABLE_NEUTRALIZATION: "neutralization",
    VARIABLE_DECAY: "decay",
    VARIABLE_TRUNCATION: "truncation",
    VARIABLE_UNIVERSE: "universe",
    VARIABLE_REGION: "region",
    VARIABLE_DELAY: "delay",
}


class ExperimentError(ValueError):
    """Thiết kế thí nghiệm không hợp lệ."""


@dataclass
class VariantSpec:
    """Một biến thể trong thí nghiệm: giá trị của biến và biểu thức tương ứng."""

    label: str
    expression: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    settings: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExperimentDesign:
    """Thiết kế đầy đủ của một thí nghiệm có kiểm soát."""

    hypothesis_id: int
    name: str
    base_expression: str
    variable: str
    values: Sequence[Any]
    objective: str = ""
    expected_effect: str = ""
    settings: Dict[str, Any] = field(default_factory=dict)
    #: Tiêu chí đánh giá, ví dụ {"min_sharpe": 1.25, "min_sample": 5}.
    evaluation_criteria: Dict[str, Any] = field(default_factory=dict)
    #: Cho phép đổi nhiều biến cùng lúc. Phải khai báo tường minh.
    allow_multiple_changes: bool = False
    notes: str = ""

    # ------------------------------------------------------------------
    def validate(self) -> "ExperimentDesign":
        if self.variable not in VARIABLES:
            raise ExperimentError(
                f"Biến khảo sát không hợp lệ: {self.variable}. "
                f"Chọn một trong {', '.join(VARIABLES)}."
            )
        if not self.base_expression.strip():
            raise ExperimentError("Thí nghiệm cần một biểu thức gốc.")
        if len(self.values) < 2:
            raise ExperimentError(
                "Thí nghiệm cần ít nhất hai giá trị để so sánh. "
                "Một giá trị duy nhất không tạo ra đối chứng."
            )
        if len(set(map(str, self.values))) != len(self.values):
            raise ExperimentError("Danh sách giá trị có phần tử trùng nhau.")
        return self

    def build_variants(self) -> List[VariantSpec]:
        """Sinh biến thể bằng cách chỉ thay đúng biến đang khảo sát."""
        self.validate()
        if self.variable in SETTING_VARIABLES:
            return self._settings_variants()
        return self._expression_variants()

    # ------------------------------------------------------------------
    def _settings_variants(self) -> List[VariantSpec]:
        """Biến nằm ở thiết lập mô phỏng: biểu thức giữ nguyên hoàn toàn."""
        key = SETTING_VARIABLES[self.variable]
        variants = []
        for value in self.values:
            settings = dict(self.settings)
            settings[key] = value
            variants.append(
                VariantSpec(
                    label=f"{self.variable}={value}",
                    expression=self.base_expression,
                    parameters={self.variable: value},
                    settings=settings,
                )
            )
        return variants

    def _expression_variants(self) -> List[VariantSpec]:
        """Biến nằm trong biểu thức: thay đúng một chỗ, giữ nguyên phần còn lại."""
        variants = []
        # Biểu thức gốc có thể còn chỗ giữ chỗ nên chưa phân tích được. Khi đó
        # lấy biến thể đầu tiên làm mốc đối chiếu cho các biến thể sau.
        reference = self.base_expression
        if "{" in reference:
            reference = self._substitute(self.values[0])

        for value in self.values:
            expression = self._substitute(value)
            self._assert_single_change(expression, value, reference)
            variants.append(
                VariantSpec(
                    label=f"{self.variable}={value}",
                    expression=expression,
                    parameters={self.variable: value},
                    settings=dict(self.settings),
                )
            )
        return variants

    def _substitute(self, value: Any) -> str:
        """Thay giá trị của biến vào biểu thức gốc.

        Biểu thức gốc có thể chứa chỗ giữ chỗ dạng `{lookback}`. Không có chỗ
        giữ chỗ thì thay theo quy tắc riêng của từng loại biến.
        """
        placeholder = "{" + self.variable + "}"
        if placeholder in self.base_expression:
            return self.base_expression.replace(placeholder, str(value))

        if self.variable == VARIABLE_LOOKBACK:
            meta = fingerprint(self.base_expression)
            if not meta["windows"]:
                raise ExperimentError(
                    "Biểu thức gốc không có cửa sổ nhìn lại nào để thay. "
                    f"Hãy dùng chỗ giữ chỗ {placeholder} để chỉ rõ vị trí."
                )
            if len(meta["windows"]) > 1 and not self.allow_multiple_changes:
                raise ExperimentError(
                    f"Biểu thức gốc có {len(meta['windows'])} cửa sổ khác nhau nên "
                    f"không rõ phải thay cái nào. Dùng chỗ giữ chỗ {placeholder}, "
                    "hoặc đặt allow_multiple_changes=True nếu thật sự muốn thay tất cả."
                )
            # Thay mọi số nguyên là cửa sổ, giữ nguyên hằng số khác.
            target = str(meta["windows"][0])
            return re.sub(rf"\b{target}\b", str(value), self.base_expression)

        if self.variable == VARIABLE_FIELD:
            meta = fingerprint(self.base_expression)
            if not meta["fields"]:
                raise ExperimentError("Biểu thức gốc không có trường dữ liệu nào để thay.")
            if len(meta["fields"]) > 1 and not self.allow_multiple_changes:
                raise ExperimentError(
                    f"Biểu thức gốc dùng {len(meta['fields'])} trường dữ liệu nên không "
                    f"rõ phải thay cái nào. Dùng chỗ giữ chỗ {placeholder}."
                )
            return re.sub(rf"\b{meta['fields'][0]}\b", str(value), self.base_expression)

        raise ExperimentError(
            f"Biến {self.variable} cần chỗ giữ chỗ {placeholder} trong biểu thức gốc."
        )

    def _assert_single_change(
        self, expression: str, value: Any, reference: Optional[str] = None
    ) -> None:
        """Đối chiếu biến thể với biểu thức gốc, bảo đảm chỉ một thứ đổi.

        So sánh ở mức vân tay: cấu trúc phải giữ nguyên. Đổi cửa sổ thì cùng
        họ cấu trúc; đổi trường dữ liệu thì cùng khuôn. Bất kỳ thay đổi nào
        vượt quá mức đó nghĩa là biến thể đã đụng tới nhiều hơn một biến.
        """
        if self.allow_multiple_changes:
            return
        base = fingerprint(reference if reference is not None else self.base_expression)
        variant = fingerprint(expression)

        if self.variable == VARIABLE_LOOKBACK and variant["family"] != base["family"]:
            raise ExperimentError(
                f"Biến thể {self.variable}={value} làm đổi cả cấu trúc, không chỉ tham số. "
                "Thí nghiệm có kiểm soát chỉ được đổi đúng một biến."
            )
        if self.variable == VARIABLE_FIELD and variant["template"] != base["template"]:
            raise ExperimentError(
                f"Biến thể {self.variable}={value} làm đổi cả khuôn cấu trúc. "
                "Thí nghiệm có kiểm soát chỉ được đổi đúng một biến."
            )
        if variant["operators"] != base["operators"] and self.variable != VARIABLE_OPERATOR:
            raise ExperimentError(
                f"Biến thể {self.variable}={value} làm đổi tập toán tử, "
                f"trong khi biến khảo sát là {self.variable}."
            )


class ExperimentEngine:
    """Tạo thí nghiệm, lưu biến thể và dựng kế hoạch sinh tương ứng."""

    def __init__(self, store: ResearchStore):
        self.store = store

    # ------------------------------------------------------------------
    def create(self, design: ExperimentDesign) -> Dict[str, Any]:
        """Lưu thí nghiệm cùng toàn bộ biến thể của nó.

        Biến thể được dựng trước khi lưu, nên một thiết kế sai bị chặn lại
        trước khi kịp ghi bản ghi nào vào kho.
        """
        variants = design.build_variants()

        experiment_id = self.store.create_experiment(
            Experiment(
                hypothesis_id=design.hypothesis_id,
                name=design.name,
                objective=design.objective,
                base_expression=design.base_expression,
                variable_changed=design.variable,
                expected_effect=design.expected_effect,
                settings=dict(design.settings),
                notes=design.notes,
            )
        )
        variant_ids = [
            self.store.add_variant(
                ExperimentVariant(
                    experiment_id=experiment_id,
                    label=spec.label,
                    expression=spec.expression,
                    parameters=dict(spec.parameters, **{"_settings": spec.settings}),
                )
            )
            for spec in variants
        ]
        return {
            "experiment_id": experiment_id,
            "variant_ids": variant_ids,
            "variants": [
                {"label": spec.label, "expression": spec.expression,
                 "parameters": spec.parameters}
                for spec in variants
            ],
            "variable": design.variable,
            "evaluation_criteria": design.evaluation_criteria,
        }

    # ------------------------------------------------------------------
    def build_plan(
        self,
        experiment_id: int,
        *,
        research_id: Optional[int] = None,
        hypothesis_id: Optional[int] = None,
        seed: Optional[int] = None,
        max_candidates: Optional[int] = None,
    ) -> GenerationPlan:
        """Dựng kế hoạch sinh từ các biến thể đã thiết kế.

        Biến thể của một thí nghiệm là biểu thức cụ thể chứ không phải khuôn,
        nên kế hoạch dùng chiến lược mutate với chính chúng làm gốc. Nhờ vậy
        bộ sinh không tự ý thêm biến nào ngoài biến đang khảo sát.
        """
        experiment = self.store.get_experiment(experiment_id)
        if experiment is None:
            raise ExperimentError(f"Không tìm thấy thí nghiệm {experiment_id}.")
        variants = self.store.list_variants(experiment_id)
        if not variants:
            raise ExperimentError(
                f"Thí nghiệm {experiment_id} chưa có biến thể nào."
            )

        expressions = [variant["expression"] for variant in variants]
        fields = sorted({
            name for expression in expressions
            for name in fingerprint(expression)["fields"]
        })
        lookbacks = sorted({
            window for expression in expressions
            for window in fingerprint(expression)["windows"]
        })

        plan = GenerationPlan(
            strategy="mutate",
            data_fields=fields,
            lookbacks=lookbacks,
            seed_expressions=expressions,
            max_candidates=max_candidates or len(expressions),
            seed=seed,
            settings=dict(experiment.get("settings") or {}),
            research_id=research_id,
            hypothesis_id=hypothesis_id if hypothesis_id is not None
            else experiment.get("hypothesis_id"),
            experiment_id=experiment_id,
            constraints={"allowed_fields": fields},
            notes=f"Kế hoạch cho thí nghiệm {experiment_id}: {experiment.get('name')}",
        )
        # Kế hoạch gắn với giả thuyết thì phải nêu dự án, nhưng thí nghiệm không
        # lưu dự án trực tiếp. Bỏ giả thuyết ra nếu nơi gọi không cung cấp dự án.
        if plan.research_id is None:
            plan.hypothesis_id = None
        return plan.validate()
