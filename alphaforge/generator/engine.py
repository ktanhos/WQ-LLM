"""Bộ sinh biểu thức.

Ba chiến lược:
    template   điền trường dữ liệu vào mẫu có sẵn
    pairwise   ghép cặp hai trường dữ liệu cho các mẫu cần hai đầu vào
    mutate     biến đổi một biểu thức gốc bằng cách bọc thêm toán tử

Bộ sinh không gọi mạng. Trường dữ liệu được lấy từ kho SQLite hoặc từ danh sách
truyền vào, nhờ vậy có thể chạy và kiểm thử hoàn toàn ngoại tuyến.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from itertools import combinations
from typing import TYPE_CHECKING, Dict, Iterable, List, Optional, Sequence

from .templates import GROUPS, TEMPLATES, TEMPLATES_BY_NAME, WINDOWS, Template

if TYPE_CHECKING:  # tránh phụ thuộc vòng giữa generator và research
    from ..research.memory import GenerationContext

WRAPPERS = (
    "rank({expr})",
    "zscore({expr})",
    "winsorize({expr}, std=4)",
    "ts_decay_linear({expr}, 5)",
    "ts_decay_linear({expr}, 10)",
    "group_neutralize({expr}, subindustry)",
    "group_neutralize({expr}, industry)",
    "-({expr})",
)


@dataclass
class GenerationRequest:
    strategy: str = "template"
    limit: int = 100
    fields: Sequence[str] = field(default_factory=tuple)
    templates: Optional[Sequence[str]] = None
    categories: Optional[Sequence[str]] = None
    windows: Sequence[int] = WINDOWS
    groups: Sequence[str] = GROUPS
    seed: Optional[int] = None
    max_length: int = 480
    seed_expressions: Sequence[str] = field(default_factory=tuple)
    #: Ngữ cảnh lấy từ trí nhớ nghiên cứu. Để None thì bộ sinh chạy đúng như
    #: trước khi có trí nhớ: phân phối đều, không loại trừ gì.
    context: Optional["GenerationContext"] = None
    #: Bỏ qua biểu thức đã từng xuất hiện trong lịch sử nghiên cứu.
    skip_known: bool = True


class GeneratorEngine:
    def __init__(self, request: GenerationRequest):
        self.request = request
        self.random = random.Random(request.seed)
        #: Đếm ứng viên bị loại theo lý do, phục vụ chẩn đoán khi lô sinh ngắn
        #: hơn mong đợi.
        self.rejected: Dict[str, int] = {
            "duplicate": 0, "too_long": 0, "known": 0, "deprioritized": 0,
        }

    # ------------------------------------------------------------------
    def selected_templates(self) -> List[Template]:
        templates = list(TEMPLATES)
        if self.request.templates:
            templates = [
                TEMPLATES_BY_NAME[name]
                for name in self.request.templates
                if name in TEMPLATES_BY_NAME
            ]
        if self.request.categories:
            wanted = set(self.request.categories)
            templates = [t for t in templates if t.category in wanted]
        if not templates:
            raise ValueError("Không có mẫu nào khớp với điều kiện lọc đã cho.")
        return templates

    def _raw_target(self) -> int:
        """Số ứng viên thô cần sinh trước khi lọc.

        Khi trí nhớ nghiên cứu đang bật, một phần ứng viên sẽ bị loại vì trùng
        hoặc vì thuộc họ đã bão hòa. Sinh dư để lô cuối cùng vẫn đủ số lượng
        người dùng yêu cầu.
        """
        context = self.request.context
        if context is not None and context.enabled:
            return self.request.limit * 4
        return self.request.limit

    def generate(self) -> List[str]:
        strategy = self.request.strategy
        if strategy == "template":
            expressions = self._generate_template()
        elif strategy == "pairwise":
            expressions = self._generate_pairwise()
        elif strategy == "mutate":
            expressions = self._generate_mutate()
        else:
            raise ValueError(f"Chiến lược không hợp lệ: {strategy}")
        return self._finalize(expressions)

    # ------------------------------------------------------------------
    def _generate_template(self) -> Iterable[str]:
        fields = list(self.request.fields)
        if not fields:
            raise ValueError("Cần ít nhất một trường dữ liệu để sinh biểu thức.")
        templates = [t for t in self.selected_templates() if "field" in t.slots]
        if not templates:
            templates = self.selected_templates()

        results: List[str] = []
        guard = 0
        target = self._raw_target()
        max_guard = target * 60 + 1000
        while len(results) < target and guard < max_guard:
            guard += 1
            template = self.random.choice(templates)
            values = self._fill_slots(template, fields)
            if values is None:
                continue
            results.append(template.pattern.format(**values))
        return results

    def _generate_pairwise(self) -> Iterable[str]:
        fields = list(self.request.fields)
        if len(fields) < 2:
            raise ValueError("Chiến lược pairwise cần ít nhất hai trường dữ liệu.")
        templates = [
            t for t in self.selected_templates() if "field_a" in t.slots
        ]
        if not templates:
            raise ValueError("Không có mẫu nào nhận hai trường dữ liệu.")

        pairs = list(combinations(fields, 2))
        self.random.shuffle(pairs)
        results: List[str] = []
        target = self._raw_target()
        for field_a, field_b in pairs:
            for template in templates:
                if len(results) >= target:
                    return results
                values: Dict[str, object] = {"field_a": field_a, "field_b": field_b}
                if "d" in template.slots:
                    values["d"] = self.random.choice(list(self.request.windows))
                if "d2" in template.slots:
                    values["d2"] = self.random.choice(list(self.request.windows))
                if "group" in template.slots:
                    values["group"] = self.random.choice(list(self.request.groups))
                if "field" in template.slots:
                    values["field"] = field_a
                results.append(template.pattern.format(**values))
        return results

    def _generate_mutate(self) -> Iterable[str]:
        seeds = list(self.request.seed_expressions)
        if not seeds:
            raise ValueError("Chiến lược mutate cần danh sách biểu thức gốc.")
        results: List[str] = []
        guard = 0
        target = self._raw_target()
        max_guard = target * 60 + 1000
        while len(results) < target and guard < max_guard:
            guard += 1
            base = self.random.choice(seeds)
            wrapper = self.random.choice(WRAPPERS)
            results.append(wrapper.format(expr=base))
        return results

    # ------------------------------------------------------------------
    def _fill_slots(
        self, template: Template, fields: Sequence[str]
    ) -> Optional[Dict[str, object]]:
        values: Dict[str, object] = {}
        for slot in template.slots:
            if slot == "field":
                values["field"] = self.random.choice(list(fields))
            elif slot == "field_a":
                values["field_a"] = self.random.choice(list(fields))
            elif slot == "field_b":
                if len(fields) < 2:
                    return None
                choice = self.random.choice(list(fields))
                if choice == values.get("field_a"):
                    return None
                values["field_b"] = choice
            elif slot == "d":
                values["d"] = self.random.choice(list(self.request.windows))
            elif slot == "d2":
                # Cửa sổ phụ luôn ngắn hơn cửa sổ chính. Ràng buộc này giữ đúng
                # ý nghĩa của các mẫu so sánh ngắn hạn với dài hạn.
                current = values.get("d")
                windows = [
                    w
                    for w in self.request.windows
                    if current is None or w < float(current)
                ]
                if not windows:
                    return None
                values["d2"] = self.random.choice(windows)
            elif slot == "group":
                values["group"] = self.random.choice(list(self.request.groups))
        return values

    def _finalize(self, expressions: Iterable[str]) -> List[str]:
        """Khử trùng lặp, loại biểu thức quá dài và áp dụng trí nhớ nghiên cứu.

        Khi không có ngữ cảnh nghiên cứu, hàm hoạt động y như trước: giữ thứ tự,
        khử trùng lặp theo chuỗi, cắt theo giới hạn độ dài và số lượng.

        Khi có ngữ cảnh, thêm hai bước. Biểu thức đã tồn tại trong lịch sử bị bỏ
        qua. Biểu thức thuộc họ cấu trúc bị hạ ưu tiên được giữ lại theo xác
        suất bằng trọng số của họ đó, thay vì bị loại hẳn: một họ từng cho kết
        quả kém vẫn có thể tốt trở lại khi thị trường đổi trạng thái, nên khóa
        cứng không gian tìm kiếm theo quá khứ là sai.
        """
        context = self.request.context
        seen = set()
        output: List[str] = []
        for expression in expressions:
            cleaned = " ".join(expression.split())
            if len(cleaned) > self.request.max_length:
                self.rejected["too_long"] += 1
                continue
            if cleaned in seen:
                self.rejected["duplicate"] += 1
                continue
            seen.add(cleaned)

            if context is not None and context.enabled:
                if self.request.skip_known and context.is_duplicate(cleaned):
                    self.rejected["known"] += 1
                    continue
                weight = context.weight_for(cleaned)
                # Trọng số dưới 1 là xác suất giữ lại. Trên 1 thì luôn giữ.
                if weight < 1.0 and self.random.random() > weight:
                    self.rejected["deprioritized"] += 1
                    continue

            output.append(cleaned)
            if len(output) >= self.request.limit:
                break
        return output
