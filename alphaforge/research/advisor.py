"""Đề xuất hướng nghiên cứu tiếp theo.

Trả lời câu hỏi "nên nghiên cứu gì tiếp" bằng cách gộp ba thứ đã có: độ phủ từ
trí nhớ, thiếu hụt từ `ResearchGap`, và điểm ưu tiên từ `ResearchPriority`. Bản
thân mô đun không tính toán thống kê mới nào.

Giao diện `ResearchAdvisor` được tách riêng để sau này cắm được bản dùng mô hình
ngôn ngữ vào cùng chỗ. Ở phase này chỉ có `RuleBasedResearchAdvisor`, và nó cố ý
không phụ thuộc vào bất kỳ dịch vụ ngoài nào.

Ranh giới quan trọng: cố vấn **chỉ đề xuất**. Nó không sinh alpha, không chạy mô
phỏng, không sửa trí nhớ nghiên cứu và không nộp gì cả. Đầu ra là bản thiết kế
thí nghiệm để người đọc quyết định có chạy hay không.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from ..generator.templates import GROUPS, WINDOWS
from .gap import GAP_DIMENSIONS, Gap, ResearchGap
from .memory import ResearchMemory
from .priority import ResearchPriority

#: Biến thiết kế nên khảo sát ứng với từng loại thiếu hụt. Ánh xạ này là chỗ
#: duy nhất nối "thiếu ở chiều nào" với "thí nghiệm nên đổi biến gì".
GAP_TO_VARIABLE = {
    "field_gap": "field",
    "lookback_gap": "lookback",
    "operator_gap": "operator",
    "group_gap": "neutralization",
    "region_gap": "region",
    "universe_gap": "universe",
    "neutralization_gap": "neutralization",
    "setting_gap": "delay",
    "family_gap": "lookback",
    "template_gap": "lookback",
}

#: Bộ giá trị gợi ý mặc định cho từng biến, khi lịch sử chưa đủ để suy ra.
DEFAULT_VALUES = {
    "lookback": [20, 60, 120],
    "delay": [0, 1],
    "neutralization": ["SUBINDUSTRY", "INDUSTRY", "MARKET"],
    "region": ["USA", "EUR", "ASI"],
    "universe": ["TOP3000", "TOP1000", "TOP500"],
}


@dataclass
class Suggestion:
    """Một hướng nghiên cứu được đề xuất, kèm thiết kế thí nghiệm cụ thể."""

    rank: int
    direction: str
    kind: str
    key: str
    reasons: List[str] = field(default_factory=list)
    sample_size: int = 0
    median_sharpe: Optional[float] = None
    pass_rate: float = 0.0
    saturation: float = 0.0
    priority: float = 0.0
    confidence: float = 0.0
    suggested_experiment: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "rank": self.rank,
            "direction": self.direction,
            "kind": self.kind,
            "key": self.key,
            "reasons": self.reasons,
            "sample_size": self.sample_size,
            "median_sharpe": self.median_sharpe,
            "pass_rate": self.pass_rate,
            "saturation": self.saturation,
            "priority": self.priority,
            "confidence": self.confidence,
            "suggested_experiment": self.suggested_experiment,
        }

    def render(self) -> str:
        lines = [
            f"{self.rank}. {self.direction}",
            "",
            "Lý do:",
        ]
        lines.extend(f"  {reason}" for reason in self.reasons)
        lines += [
            "",
            f"Cỡ mẫu: {self.sample_size}"
            + (f"   Sharpe trung vị: {self.median_sharpe}" if self.median_sharpe is not None else "")
            + f"   Bão hòa: {self.saturation}   Ưu tiên: {self.priority}",
        ]
        experiment = self.suggested_experiment
        if experiment:
            lines += [
                "",
                "Thí nghiệm gợi ý:",
                f"  Biểu thức gốc: {experiment.get('base_expression')}",
                f"  Biến khảo sát: {experiment.get('variable')}",
                f"  Giá trị:       {experiment.get('values')}",
            ]
        if self.confidence < 0.3:
            lines += [
                "",
                "Lưu ý: cỡ mẫu còn nhỏ nên đây là hướng đáng thử, "
                "không phải bằng chứng rằng nó tốt.",
            ]
        return "\n".join(lines)


class ResearchAdvisor:
    """Giao diện chung. Bản dùng mô hình ngôn ngữ sau này cắm vào đây."""

    name = "base"

    def suggest(self, limit: int = 5) -> List[Suggestion]:
        raise NotImplementedError


class RuleBasedResearchAdvisor(ResearchAdvisor):
    """Đề xuất theo luật, không cần mô hình ngôn ngữ.

    Toàn bộ logic là gộp và sắp xếp thứ đã tính ở nơi khác. Cố ý giữ như vậy để
    kết quả tất định và giải thích được từng bước.
    """

    name = "rule_based"

    def __init__(
        self,
        memory: ResearchMemory,
        *,
        gap: Optional[ResearchGap] = None,
        priority: Optional[ResearchPriority] = None,
    ):
        self.memory = memory
        self.gap = gap or ResearchGap(memory)
        self.priority = priority or ResearchPriority()

    # ------------------------------------------------------------------
    def suggest(
        self,
        limit: int = 5,
        *,
        known_values: Optional[Dict[str, Sequence[str]]] = None,
    ) -> List[Suggestion]:
        """Xếp hạng hướng nghiên cứu tiếp theo."""
        gaps = self.gap.find(known_values=known_values)
        profiles = self.memory.profiles()
        coverage = self.memory.coverage()

        suggestions: List[Suggestion] = []
        for index, item in enumerate(gaps[:limit], start=1):
            profile = profiles.get(item.key)
            saturation = profile.saturation if profile else 0.0
            suggestions.append(
                Suggestion(
                    rank=index,
                    direction=self._describe(item, coverage),
                    kind=item.kind,
                    key=item.key,
                    reasons=self._reasons(item, saturation),
                    sample_size=item.count,
                    median_sharpe=item.detail.get("median_sharpe"),
                    pass_rate=item.detail.get("pass_rate", 0.0),
                    saturation=round(saturation, 4),
                    priority=item.priority.score if item.priority else 0.0,
                    confidence=item.priority.confidence if item.priority else 0.0,
                    suggested_experiment=self._design(item),
                )
            )
        return suggestions

    # ------------------------------------------------------------------
    def _describe(self, item: Gap, coverage: Dict[str, Any]) -> str:
        """Mô tả hướng nghiên cứu bằng lời, không dùng mã băm thô."""
        dimension = GAP_DIMENSIONS.get(item.kind, item.kind)
        readable = {
            "field": "Trường dữ liệu",
            "operator": "Toán tử",
            "lookback": "Cửa sổ nhìn lại",
            "group": "Nhóm phân loại",
            "region": "Khu vực",
            "universe": "Universe",
            "neutralization": "Cách trung tính hóa",
            "delay": "Độ trễ dữ liệu",
        }.get(dimension, "Họ cấu trúc")

        key = item.key
        if dimension in ("family", "template") or len(key) > 24:
            # Vân tay là chuỗi băm, rút gọn cho người đọc.
            key = f"{key[:12]}…"
        if item.detail.get("dimension"):
            return (
                f"{readable} của cấu trúc {key} — mới khảo sát ở một giá trị duy nhất"
            )
        return f"{readable}: {key}"

    def _reasons(self, item: Gap, saturation: float) -> List[str]:
        """Lý do đề xuất, viết dưới dạng phát biểu về độ phủ chứ không về chất lượng.

        Điểm quan trọng: một vùng ít được nghiên cứu **không có nghĩa là nó tốt
        hơn**. Câu chữ ở đây cố ý chỉ nói về mức độ đã khảo sát.
        """
        reasons = [f"- {item.reason}"]
        if item.priority is not None:
            reasons.extend(f"- {line}" for line in item.priority.reasons)
        if saturation > 0.6:
            reasons.append(f"- Mức bão hòa {saturation:.2f}, đã khai thác khá kỹ.")
        elif saturation:
            reasons.append(f"- Mức bão hòa {saturation:.2f}, còn chỗ để khảo sát.")
        reasons.append(
            "- Đây là mức độ đã nghiên cứu, không phải dự báo về hiệu năng."
        )
        return reasons

    def _design(self, item: Gap) -> Dict[str, Any]:
        """Dựng bản thiết kế thí nghiệm cho một thiếu hụt.

        Chỉ đề xuất, không tạo thí nghiệm. Người đọc quyết định có chạy hay không.
        """
        variable = GAP_TO_VARIABLE.get(item.kind, "lookback")
        dimension = GAP_DIMENSIONS.get(item.kind, "family")

        if variable == "field":
            # Thiếu ở chiều trường dữ liệu thì so chính trường đó với trường đã quen.
            base = f"rank(ts_mean({item.key}, 20))"
            values = [item.key]
            for known in ("close", "returns", "volume"):
                if known != item.key and len(values) < 3:
                    values.append(known)
            return {
                "base_expression": base,
                "variable": "field",
                "values": values,
                "note": "So trường ít được khảo sát với trường đã quen thuộc.",
            }

        if variable == "operator":
            return {
                "base_expression": f"rank({item.key}(close, 20))",
                "variable": "lookback",
                "values": DEFAULT_VALUES["lookback"],
                "note": f"Khảo sát toán tử {item.key} qua nhiều cửa sổ.",
            }

        if variable == "lookback":
            tried = {int(value) for value in self.memory.lookbacks_tried()
                     if str(value).isdigit()}
            untried = [value for value in WINDOWS if value not in tried]
            values = untried[:3] or DEFAULT_VALUES["lookback"]
            base = item.detail.get("base_expression") or "rank(ts_mean(close, {lookback}))"
            return {
                "base_expression": base,
                "variable": "lookback",
                "values": values,
                "note": "So các cửa sổ chưa từng thử với cửa sổ đã quen.",
            }

        values = DEFAULT_VALUES.get(variable, list(GROUPS)[:3])
        detail_dimension = item.detail.get("dimension")
        if detail_dimension:
            tried = set(item.detail.get("values_tried", {}))
            candidates = DEFAULT_VALUES.get(detail_dimension, [])
            untried = [value for value in candidates if str(value) not in tried]
            if untried:
                values = untried[:3]
                variable = detail_dimension
        return {
            "base_expression": "rank(ts_mean(close, 20))",
            "variable": variable,
            "values": values,
            "note": f"Khảo sát {variable} ở các giá trị chưa thử.",
        }

    # ------------------------------------------------------------------
    def render(self, limit: int = 5, **kwargs: Any) -> str:
        """Bản văn bản trả lời câu hỏi: nên nghiên cứu gì tiếp."""
        suggestions = self.suggest(limit, **kwargs)
        if not suggestions:
            return (
                "Chưa phát hiện khoảng trống nào. Kho lịch sử có thể còn rỗng — "
                "chạy 'alphaforge history scan' trước."
            )
        blocks = ["Nên nghiên cứu gì tiếp", "=" * 40, ""]
        blocks.extend(suggestion.render() + "\n" for suggestion in suggestions)
        return "\n".join(blocks)
