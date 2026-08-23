"""Tìm vùng nghiên cứu chưa được khai thác hoặc khai thác chưa đầy đủ.

Mục tiêu của lớp này **không phải** dự đoán alpha nào sẽ tốt. Mục tiêu là chỉ
ra chỗ nào trong không gian nghiên cứu còn thiếu bằng chứng, để lượt mô phỏng
tiếp theo tạo thêm thông tin thay vì lặp lại thứ đã biết.

Hai loại thiếu hụt được phân biệt rõ:

    thiếu theo lượng   một chiều có ít quan sát, ví dụ trường dữ liệu mới thử
                       năm alpha trong khi trường khác đã thử hai trăm
    thiếu theo phủ     một chiều đã có nhiều quan sát nhưng tất cả đều rơi vào
                       một giá trị, ví dụ trăm alpha nhưng đều delay 1

Loại thứ hai nguy hiểm hơn vì nhìn vào số lượng sẽ tưởng đã khảo sát kỹ.

Không kết luận nào bị viết cứng trong mã. Mọi ngưỡng đều là tham số, và mọi
phát hiện đều kèm số liệu để người đọc tự kiểm chứng.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .memory import ResearchMemory
from .priority import PriorityScore, PriorityWeights, ResearchPriority

#: Các loại thiếu hụt được phát hiện.
GAP_FIELD = "field_gap"
GAP_OPERATOR = "operator_gap"
GAP_LOOKBACK = "lookback_gap"
GAP_FAMILY = "family_gap"
GAP_SETTING = "setting_gap"
GAP_REGION = "region_gap"
GAP_UNIVERSE = "universe_gap"
GAP_NEUTRALIZATION = "neutralization_gap"
GAP_TEMPLATE = "template_gap"
GAP_GROUP = "group_gap"

#: Chiều nghiên cứu tương ứng với từng loại thiếu hụt.
GAP_DIMENSIONS = {
    GAP_FIELD: "field",
    GAP_OPERATOR: "operator",
    GAP_LOOKBACK: "lookback",
    GAP_FAMILY: "family",
    GAP_REGION: "region",
    GAP_UNIVERSE: "universe",
    GAP_NEUTRALIZATION: "neutralization",
    GAP_TEMPLATE: "template",
    GAP_GROUP: "group",
    GAP_SETTING: "delay",
}

#: Dưới ngưỡng này thì một giá trị được coi là chưa khảo sát đủ.
DEFAULT_UNDEREXPLORED = 5
#: Trên ngưỡng này thì coi là đã khảo sát dày.
DEFAULT_SATURATED = 40
#: Một họ có nhiều quan sát nhưng tập trung quá mức vào một giá trị thiết lập
#: thì bị coi là thiếu độ phủ.
DEFAULT_COVERAGE_CONCENTRATION = 0.9
#: Số quan sát tối thiểu để việc thiếu độ phủ có ý nghĩa.
DEFAULT_COVERAGE_MIN_SAMPLE = 20


@dataclass
class Gap:
    """Một vùng nghiên cứu còn thiếu bằng chứng."""

    kind: str
    key: str
    count: int
    reason: str
    detail: Dict[str, Any] = field(default_factory=dict)
    priority: Optional[PriorityScore] = None

    def as_dict(self) -> Dict[str, Any]:
        payload = {
            "kind": self.kind,
            "key": self.key,
            "count": self.count,
            "reason": self.reason,
            "detail": self.detail,
        }
        if self.priority is not None:
            payload["priority"] = self.priority.score
            payload["priority_reasons"] = self.priority.reasons
            payload["confidence"] = self.priority.confidence
        return payload


class ResearchGap:
    """Phát hiện thiếu hụt trên tám chiều nghiên cứu."""

    def __init__(
        self,
        memory: ResearchMemory,
        *,
        underexplored_threshold: int = DEFAULT_UNDEREXPLORED,
        saturated_threshold: int = DEFAULT_SATURATED,
        concentration_threshold: float = DEFAULT_COVERAGE_CONCENTRATION,
        coverage_min_sample: int = DEFAULT_COVERAGE_MIN_SAMPLE,
        priority: Optional[ResearchPriority] = None,
    ):
        self.memory = memory
        self.underexplored_threshold = int(underexplored_threshold)
        self.saturated_threshold = int(saturated_threshold)
        self.concentration_threshold = float(concentration_threshold)
        self.coverage_min_sample = int(coverage_min_sample)
        self.priority = priority or ResearchPriority()

    # ------------------------------------------------------------------
    def find(
        self,
        kinds: Optional[Sequence[str]] = None,
        *,
        limit_per_kind: int = 20,
        known_values: Optional[Dict[str, Sequence[str]]] = None,
    ) -> List[Gap]:
        """Tìm mọi thiếu hụt, hoặc chỉ những loại được chỉ định.

        `known_values` cho biết toàn bộ giá trị hợp lệ của một chiều, ví dụ danh
        sách trường dữ liệu tải về từ nền tảng. Nhờ đó phát hiện được giá trị
        **chưa từng xuất hiện lần nào**, thứ mà đếm trên dữ liệu đã có không thể
        thấy: một trường chưa ai thử thì không có mặt trong bảng đếm.
        """
        selected = set(kinds) if kinds else set(GAP_DIMENSIONS)
        coverage = self.memory.coverage()
        rows = self.memory.load_rows()
        known_values = known_values or {}

        gaps: List[Gap] = []
        for kind in selected:
            dimension = GAP_DIMENSIONS.get(kind)
            if dimension is None:
                continue
            counts = coverage.get(dimension, Counter())
            found = self._quantity_gaps(kind, dimension, counts, rows)
            found.extend(
                self._never_tried(kind, dimension, counts, known_values.get(dimension, ()))
            )
            found.sort(
                key=lambda gap: (
                    -(gap.priority.score if gap.priority else 0.0), gap.count, gap.key
                )
            )
            gaps.extend(found[:limit_per_kind])

        # Thiếu hụt độ phủ được xét riêng vì nó nói về phân bố, không về số lượng.
        if GAP_SETTING in selected:
            gaps.extend(self._coverage_gaps(rows)[:limit_per_kind])

        gaps.sort(key=lambda gap: -(gap.priority.score if gap.priority else 0.0))
        return gaps

    # ------------------------------------------------------------------
    def _quantity_gaps(
        self, kind: str, dimension: str, counts: Counter, rows: List[Dict[str, Any]]
    ) -> List[Gap]:
        """Giá trị đã xuất hiện nhưng còn ít quan sát."""
        stats = self._dimension_stats(dimension, rows)
        gaps: List[Gap] = []
        for key, count in counts.items():
            if count > self.underexplored_threshold:
                continue
            entry = stats.get(key, {})
            score = self.priority.score(
                key,
                dimension=dimension,
                sample_size=count,
                median_sharpe=entry.get("median_sharpe"),
                pass_rate=entry.get("pass_rate", 0.0),
                distinct_neighbours=entry.get("distinct_families", 0),
                total_neighbours=count,
            )
            gaps.append(
                Gap(
                    kind=kind,
                    key=key,
                    count=count,
                    reason=f"Mới có {count} alpha, dưới ngưỡng {self.underexplored_threshold}.",
                    detail={
                        "median_sharpe": entry.get("median_sharpe"),
                        "pass_rate": entry.get("pass_rate", 0.0),
                    },
                    priority=score,
                )
            )
        return gaps

    def _never_tried(
        self, kind: str, dimension: str, counts: Counter, known: Iterable[str]
    ) -> List[Gap]:
        """Giá trị hợp lệ nhưng chưa từng xuất hiện trong bất kỳ alpha nào."""
        gaps: List[Gap] = []
        for value in known:
            if str(value) in counts:
                continue
            score = self.priority.score(str(value), dimension=dimension, sample_size=0)
            gaps.append(
                Gap(
                    kind=kind,
                    key=str(value),
                    count=0,
                    reason="Chưa từng được thử lần nào.",
                    detail={"never_tried": True},
                    priority=score,
                )
            )
        return gaps

    def _coverage_gaps(self, rows: List[Dict[str, Any]]) -> List[Gap]:
        """Họ cấu trúc có nhiều quan sát nhưng dồn hết vào một giá trị thiết lập.

        Đây là dạng thiếu hụt dễ bị bỏ sót nhất: nhìn số lượng thì tưởng đã
        khảo sát kỹ, nhưng thực ra chỉ khảo sát kỹ ở đúng một điều kiện.
        """
        by_family: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for row in rows:
            family = row.get("family") or row.get("fingerprint")
            if family:
                by_family[str(family)].append(row)

        gaps: List[Gap] = []
        for family, group in by_family.items():
            if len(group) < self.coverage_min_sample:
                continue
            for dimension in ("delay", "region", "universe", "neutralization"):
                values = Counter(
                    str(row.get(dimension)) for row in group if row.get(dimension) is not None
                )
                if not values or len(values) > 1 and max(values.values()) / sum(values.values()) < self.concentration_threshold:
                    continue
                dominant, dominant_count = values.most_common(1)[0]
                share = dominant_count / sum(values.values())
                if share < self.concentration_threshold:
                    continue
                score = self.priority.score(
                    f"{family[:12]}:{dimension}",
                    dimension=dimension,
                    # Coi như chưa khảo sát ở các giá trị còn lại.
                    sample_size=len(values) - 1,
                    median_sharpe=None,
                )
                gaps.append(
                    Gap(
                        kind=GAP_SETTING,
                        key=f"{family}:{dimension}",
                        count=len(group),
                        reason=(
                            f"{len(group)} alpha nhưng {share * 100:.0f} phần trăm dùng "
                            f"{dimension}={dominant}. Chưa biết cấu trúc này hoạt động ra sao ở giá trị khác."
                        ),
                        detail={
                            "family": family,
                            "dimension": dimension,
                            "dominant_value": dominant,
                            "share": round(share, 4),
                            "values_tried": dict(values),
                        },
                        priority=score,
                    )
                )
        gaps.sort(key=lambda gap: -(gap.priority.score if gap.priority else 0.0))
        return gaps

    # ------------------------------------------------------------------
    def _dimension_stats(
        self, dimension: str, rows: List[Dict[str, Any]]
    ) -> Dict[str, Dict[str, Any]]:
        """Thống kê hiệu năng theo từng giá trị của một chiều."""
        import statistics

        from .memory import _int_list, _is_successful, _str_list

        buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for row in rows:
            for key in self._keys_for(dimension, row, _str_list, _int_list):
                buckets[str(key)].append(row)

        stats: Dict[str, Dict[str, Any]] = {}
        for key, group in buckets.items():
            sharpes = []
            for row in group:
                value = row.get("sharpe")
                if value is None:
                    continue
                try:
                    sharpes.append(abs(float(value)))
                except (TypeError, ValueError):
                    continue
            passed = sum(1 for row in group if _is_successful(row.get("status")))
            families = {
                str(row.get("family") or row.get("fingerprint") or "")
                for row in group
            }
            stats[key] = {
                "median_sharpe": statistics.median(sharpes) if sharpes else None,
                "pass_rate": round(passed / len(group), 4) if group else 0.0,
                "distinct_families": len(families - {""}),
            }
        return stats

    @staticmethod
    def _keys_for(dimension: str, row: Dict[str, Any], str_list, int_list) -> List[str]:
        if dimension == "field":
            return str_list(row.get("fields_json"))
        if dimension == "operator":
            return str_list(row.get("operators_json"))
        if dimension == "lookback":
            return [str(value) for value in int_list(row.get("windows_json"))]
        if dimension == "family":
            key = row.get("family") or row.get("fingerprint")
            return [str(key)] if key else []
        if dimension == "group":
            from .memory import _groups_of
            return _groups_of(row)
        value = row.get(dimension)
        return [str(value)] if value is not None else []

    # ------------------------------------------------------------------
    def summary(self, known_values: Optional[Dict[str, Sequence[str]]] = None) -> Dict[str, Any]:
        """Tóm tắt thiếu hụt theo từng loại, dùng cho báo cáo và bảng theo dõi."""
        gaps = self.find(known_values=known_values)
        grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for gap in gaps:
            grouped[gap.kind].append(gap.as_dict())
        return {
            "total": len(gaps),
            "by_kind": {kind: items for kind, items in grouped.items()},
            "counts": {kind: len(items) for kind, items in grouped.items()},
            "thresholds": {
                "underexplored": self.underexplored_threshold,
                "saturated": self.saturated_threshold,
                "concentration": self.concentration_threshold,
                "coverage_min_sample": self.coverage_min_sample,
            },
        }
