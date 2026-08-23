"""Chấm độ ưu tiên nghiên cứu, kèm lý do đọc được.

Quy tắc "ít alpha thì ưu tiên cao" là sai. Một vùng chưa ai thử có thể chưa ai
thử vì nó vô nghĩa. Ngược lại, một vùng đã thử nhiều mà vẫn cho kết quả tốt thì
vẫn đáng đầu tư tiếp.

Điểm ưu tiên ở đây là tích của bốn thành phần, mỗi thành phần nằm trong khoảng
0 tới 1:

    underexplored   còn ít quan sát tới mức nào
    performance     kết quả đã quan sát được có hứa hẹn không
    diversity       vùng này có mở rộng không gian tìm kiếm không
    recency         đã lâu chưa khảo sát lại chưa

Dùng tích chứ không dùng tổng có chủ đích: một thành phần bằng 0 thì cả điểm
bằng 0. Vùng không có triển vọng nào thì dù chưa ai thử cũng không đáng ưu tiên.

Mọi hệ số đều cấu hình được, và mọi điểm đều kèm danh sách lý do, để người
nghiên cứu kiểm chứng được thay vì phải tin vào một con số.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

#: Cỡ mẫu coi là đã khảo sát đầy đủ một vùng.
DEFAULT_SATURATION_SAMPLE = 40
#: Cỡ mẫu tối thiểu để tin vào phần hiệu năng quan sát được.
DEFAULT_MIN_SAMPLE = 5


@dataclass
class PriorityWeights:
    """Trọng số của bốn thành phần. Đặt về 0 để bỏ hẳn một thành phần."""

    underexplored: float = 1.0
    performance: float = 1.0
    diversity: float = 1.0
    recency: float = 1.0
    #: Cỡ mẫu coi là đã khảo sát đầy đủ.
    saturation_sample: int = DEFAULT_SATURATION_SAMPLE
    #: Sharpe mục tiêu, dùng làm mốc đánh giá hiệu năng.
    target_sharpe: float = 1.25
    #: Điểm hiệu năng tối thiểu, để một vùng kết quả kém vẫn còn cơ hội mỏng.
    performance_floor: float = 0.1


@dataclass
class PriorityScore:
    """Một điểm ưu tiên kèm toàn bộ căn cứ dẫn tới nó."""

    key: str
    dimension: str
    score: float
    sample_size: int
    components: Dict[str, float] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)
    #: Mức tin cậy dựa trên cỡ mẫu. Điểm cao với cỡ mẫu nhỏ vẫn là phỏng đoán.
    confidence: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "dimension": self.dimension,
            "score": self.score,
            "sample_size": self.sample_size,
            "confidence": self.confidence,
            "components": self.components,
            "reasons": self.reasons,
        }


class ResearchPriority:
    """Tính điểm ưu tiên cho một vùng nghiên cứu.

    Đầu vào là số liệu quan sát được, không phải bản ghi thô, nên lớp này kiểm
    thử được độc lập và không phụ thuộc vào kho dữ liệu.
    """

    def __init__(self, weights: Optional[PriorityWeights] = None):
        self.weights = weights or PriorityWeights()

    # ------------------------------------------------------------------
    def score(
        self,
        key: str,
        *,
        dimension: str = "family",
        sample_size: int = 0,
        median_sharpe: Optional[float] = None,
        pass_rate: float = 0.0,
        distinct_neighbours: int = 0,
        total_neighbours: int = 0,
        days_since_last: Optional[float] = None,
    ) -> PriorityScore:
        """Chấm điểm một vùng nghiên cứu.

        `distinct_neighbours` và `total_neighbours` mô tả độ đa dạng: một họ đã
        thử với nhiều trường dữ liệu khác nhau thì việc thử thêm trường nữa ít
        mang lại thông tin mới hơn.
        """
        weights = self.weights
        reasons: List[str] = []

        underexplored = self._underexplored(sample_size, reasons)
        performance = self._performance(median_sharpe, pass_rate, sample_size, reasons)
        diversity = self._diversity(distinct_neighbours, total_neighbours, reasons)
        recency = self._recency(days_since_last, reasons)

        components = {
            "underexplored": round(underexplored, 4),
            "performance": round(performance, 4),
            "diversity": round(diversity, 4),
            "recency": round(recency, 4),
        }

        # Tích có trọng số dạng lũy thừa: trọng số 0 làm thành phần đó thành 1,
        # tức là vô hiệu hóa nó thay vì triệt tiêu cả điểm.
        total = 1.0
        for name, value in components.items():
            exponent = float(getattr(weights, name))
            total *= value ** exponent if exponent else 1.0

        confidence = round(min(1.0, sample_size / max(weights.saturation_sample, 1)), 4)
        if sample_size < DEFAULT_MIN_SAMPLE:
            reasons.append(
                f"Cỡ mẫu {sample_size} còn quá nhỏ, điểm này là phỏng đoán chứ chưa phải kết luận."
            )

        return PriorityScore(
            key=key,
            dimension=dimension,
            score=round(total, 6),
            sample_size=sample_size,
            components=components,
            reasons=reasons,
            confidence=confidence,
        )

    # ------------------------------------------------------------------
    def _underexplored(self, sample_size: int, reasons: List[str]) -> float:
        """Càng ít quan sát thì càng còn chỗ để khảo sát."""
        reference = max(self.weights.saturation_sample, 1)
        value = max(0.0, 1.0 - min(1.0, sample_size / reference))
        if sample_size == 0:
            reasons.append("Chưa có alpha nào trong vùng này.")
        elif value > 0.7:
            reasons.append(f"Còn ít quan sát, mới {sample_size} alpha.")
        elif value < 0.2:
            reasons.append(f"Đã khảo sát dày, {sample_size} alpha.")
        # Vùng chưa ai thử vẫn giữ chút bất định thay vì điểm tuyệt đối.
        return value if sample_size else 0.9

    def _performance(
        self,
        median_sharpe: Optional[float],
        pass_rate: float,
        sample_size: int,
        reasons: List[str],
    ) -> float:
        """Kết quả đã quan sát có hứa hẹn không.

        Khi chưa đủ cỡ mẫu, trả về mức trung tính thay vì kết luận, vì hai hay
        ba quan sát không nói lên điều gì.
        """
        if median_sharpe is None or sample_size < DEFAULT_MIN_SAMPLE:
            reasons.append("Chưa đủ dữ liệu hiệu năng, tạm coi là trung tính.")
            return 0.5

        target = self.weights.target_sharpe or 1.0
        ratio = abs(float(median_sharpe)) / target
        value = max(self.weights.performance_floor, min(1.0, ratio))
        if ratio >= 1.0:
            reasons.append(
                f"Sharpe trung vị {median_sharpe:.2f} đạt hoặc vượt mốc {target:.2f}."
            )
        else:
            reasons.append(
                f"Sharpe trung vị {median_sharpe:.2f} dưới mốc {target:.2f}."
            )
        if pass_rate > 0:
            reasons.append(f"Tỷ lệ đạt {pass_rate * 100:.1f} phần trăm.")
            # Vùng từng ra alpha đạt thì đáng tin hơn vùng chỉ có Sharpe cao.
            value = min(1.0, value * (1.0 + pass_rate))
        elif sample_size >= DEFAULT_MIN_SAMPLE:
            reasons.append("Chưa alpha nào trong vùng này đạt ngưỡng.")
        return value

    def _diversity(
        self, distinct_neighbours: int, total_neighbours: int, reasons: List[str]
    ) -> float:
        """Vùng đã thử nhiều biến thể thì thêm một biến thể ít giá trị hơn."""
        if not total_neighbours:
            return 1.0
        ratio = distinct_neighbours / total_neighbours
        value = max(0.2, 1.0 - ratio)
        if ratio > 0.7:
            reasons.append(
                f"Đã thử {distinct_neighbours} biến thể khác nhau, không gian gần cạn."
            )
        return value

    def _recency(self, days_since_last: Optional[float], reasons: List[str]) -> float:
        """Vùng lâu chưa khảo sát lại đáng xem xét vì thị trường đã đổi."""
        if days_since_last is None:
            return 1.0
        if days_since_last > 180:
            reasons.append(f"Đã {days_since_last:.0f} ngày chưa khảo sát lại.")
            return 1.0
        if days_since_last < 7:
            reasons.append("Vừa khảo sát gần đây.")
            return 0.5
        # Tăng tuyến tính từ 0.5 tới 1.0 trong khoảng bảy tới một trăm tám mươi ngày.
        return round(0.5 + 0.5 * (days_since_last - 7) / (180 - 7), 4)

    # ------------------------------------------------------------------
    def rank(self, scores: List[PriorityScore], limit: int = 20) -> List[PriorityScore]:
        """Xếp hạng giảm dần, ưu tiên cỡ mẫu lớn khi điểm bằng nhau."""
        ordered = sorted(
            scores, key=lambda item: (item.score, item.sample_size), reverse=True
        )
        return ordered[:limit]

    def explain(self, score: PriorityScore) -> str:
        """Diễn giải một điểm thành văn bản ngắn cho người đọc."""
        lines = [
            f"Research Priority = {score.score:.2f} ({score.dimension}: {score.key})",
            f"Cỡ mẫu {score.sample_size}, độ tin cậy {score.confidence:.2f}",
            "Thành phần: "
            + ", ".join(f"{name}={value}" for name, value in score.components.items()),
            "Lý do:",
        ]
        lines.extend(f"  - {reason}" for reason in score.reasons)
        return "\n".join(lines)
