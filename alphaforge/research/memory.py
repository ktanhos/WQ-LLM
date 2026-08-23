"""Trí nhớ nghiên cứu dùng để lái bộ sinh biểu thức.

Bộ sinh mặc định chọn mẫu theo phân phối đều. Sau vài nghìn alpha, cách này
lặp lại chính những họ cấu trúc đã cho kết quả kém, vì nó không nhớ gì cả.

Mô đun này đọc lịch sử đã nhập rồi tính một trọng số cho từng họ cấu trúc:

    họ đã thử nhiều mà trung vị Sharpe thấp   → giảm ưu tiên
    họ mới thử vài lần                        → giữ hoặc tăng ưu tiên
    họ đã thử nhiều và trung vị Sharpe cao    → giữ nguyên, không phạt

Trọng số chỉ thay đổi xác suất chọn mẫu, không loại bỏ hẳn mẫu nào. Loại hẳn
sẽ khóa cứng không gian tìm kiếm theo dữ liệu quá khứ, trong khi thị trường
thay đổi và một họ từng kém có thể tốt trở lại.

Lớp này không gọi mạng và hoạt động bình thường khi kho lịch sử rỗng: khi đó
mọi trọng số bằng 1 và bộ sinh chạy đúng như trước khi có trí nhớ.
"""

from __future__ import annotations

import json
import logging
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ..history.fingerprint import fingerprint
from ..storage.db import Database

logger = logging.getLogger(__name__)


def _dampen(weight: float, factor: float) -> float:
    """Kéo một trọng số về gần 1 theo hệ số cho trước.

    factor = 1 giữ nguyên, factor = 0 vô hiệu hóa hoàn toàn.
    """
    return round(1.0 + (weight - 1.0) * factor, 4)

#: Số alpha tối thiểu để lịch sử của một họ được coi là có ý nghĩa thống kê.
MIN_SAMPLE = 8
#: Trọng số thấp nhất. Không bao giờ bằng 0 để không khóa cứng không gian tìm kiếm.
MIN_WEIGHT = 0.15
#: Trọng số cao nhất, chặn để một họ may mắn không chiếm toàn bộ lô sinh.
MAX_WEIGHT = 2.5
#: Số alpha coi là đã khai thác hết một họ cấu trúc, dùng để quy mức bão hòa
#: về thang 0..1.
SATURATION_REFERENCE = 50


@dataclass
class ResearchProfile:
    """Hồ sơ lịch sử của một họ cấu trúc."""

    family: str
    count: int = 0
    passed: int = 0
    median_sharpe: Optional[float] = None
    best_sharpe: Optional[float] = None
    windows: List[int] = field(default_factory=list)
    fields: List[str] = field(default_factory=list)
    operators: List[str] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.count if self.count else 0.0

    @property
    def saturation(self) -> float:
        """Mức bão hòa từ 0 tới 1.

        Kết hợp hai yếu tố: đã thử bao nhiêu lần, và thu được bao nhiêu. Thử
        nhiều mà tỷ lệ đạt thấp thì bão hòa cao. Thử nhiều mà vẫn ra alpha đạt
        đều thì không coi là bão hòa, vì hướng đó vẫn đang sinh lợi.

        Trả về số thay vì cờ đúng sai để nơi gọi tự chọn ngưỡng, và để so sánh
        được mức độ giữa hai họ cùng vượt ngưỡng.
        """
        if not self.count:
            return 0.0
        # Cỡ mẫu quy về thang 0..1, bão hòa hoàn toàn ở mốc SATURATION_REFERENCE.
        effort = min(1.0, self.count / SATURATION_REFERENCE)
        return round(effort * (1.0 - self.pass_rate), 4)

    @property
    def is_saturated(self) -> bool:
        """Đã thử nhiều mà chưa ra kết quả nào đạt."""
        return self.count >= MIN_SAMPLE and self.passed == 0

    @property
    def is_underexplored(self) -> bool:
        return self.count < MIN_SAMPLE

    @property
    def confidence(self) -> float:
        """Mức tin cậy của kết luận, dựa trên cỡ mẫu.

        Dùng để không kết luận mạnh từ nhóm quá ít quan sát.
        """
        return round(min(1.0, self.count / (MIN_SAMPLE * 4)), 4)


@dataclass
class GenerationContext:
    """Ngữ cảnh truyền vào bộ sinh.

    Bộ sinh chỉ cần hai thứ: trọng số theo họ cấu trúc, và tập vân tay cần
    tránh sinh lại. Giữ giao diện hẹp như vậy để bộ sinh không phụ thuộc vào
    toàn bộ lớp nghiên cứu.
    """

    #: Vân tay mức family → trọng số ưu tiên.
    family_weights: Dict[str, float] = field(default_factory=dict)
    #: Vân tay mức exact đã tồn tại, dùng để bỏ qua biểu thức trùng khít.
    seen_exact: set = field(default_factory=set)
    #: Cửa sổ thời gian đã thử theo từng họ, để tránh lặp lại tham số cũ.
    tested_windows: Dict[str, List[int]] = field(default_factory=dict)
    #: Trường dữ liệu và toán tử đã thử theo từng họ.
    tested_fields: Dict[str, List[str]] = field(default_factory=dict)
    tested_operators: Dict[str, List[str]] = field(default_factory=dict)
    #: Mức bão hòa và độ ưu tiên nghiên cứu theo họ, thang 0..1.
    family_saturation: Dict[str, float] = field(default_factory=dict)
    family_priority: Dict[str, float] = field(default_factory=dict)
    enabled: bool = True

    #: Trọng số ở hai mức trừu tượng rộng hơn family.
    template_weights: Dict[str, float] = field(default_factory=dict)
    field_weights: Dict[str, float] = field(default_factory=dict)

    #: Hệ số làm nhẹ khi phải suy ra từ mức rộng hơn. Bằng chứng ở mức template
    #: hay mức trường yếu hơn bằng chứng ở đúng họ cấu trúc, nên hình phạt phải
    #: nhẹ hơn tương ứng.
    TEMPLATE_DAMPING = 0.5
    FIELD_DAMPING = 0.3

    def weight_for(self, expression: str) -> float:
        """Trọng số của một biểu thức ứng viên.

        Tra theo ba mức, dừng ở mức hẹp nhất có dữ liệu:

            family    đã thử đúng cấu trúc này với đúng trường này
            template  đã thử cấu trúc này với trường khác
            field     đã khai thác trường này ở cấu trúc khác

        Không có mức nào khớp thì trả về 1, tức là không can thiệp. Nhờ cách
        tra dần này, một biểu thức có cấu trúc mới nhưng dùng lại trường đã
        khai thác kiệt vẫn bị hạ ưu tiên nhẹ, thay vì thoát hoàn toàn.
        """
        if not self.enabled:
            return 1.0
        meta = fingerprint(expression)

        weight = self.family_weights.get(meta["family"])
        if weight is not None:
            return weight

        weight = self.template_weights.get(meta["template"])
        if weight is not None:
            return _dampen(weight, self.TEMPLATE_DAMPING)

        if meta["fields"]:
            known = [
                self.field_weights[name]
                for name in meta["fields"]
                if name in self.field_weights
            ]
            if known:
                # Lấy mức phạt nặng nhất trong các trường xuất hiện.
                return _dampen(min(known), self.FIELD_DAMPING)
        return 1.0

    def saturation_for(self, expression: str) -> float:
        """Mức bão hòa của họ chứa biểu thức. Chưa từng thử thì bằng 0."""
        if not self.enabled:
            return 0.0
        return self.family_saturation.get(fingerprint(expression)["family"], 0.0)

    def priority_for(self, expression: str) -> float:
        """Độ ưu tiên nghiên cứu. Họ chưa thử được coi là đáng khảo sát nhất."""
        if not self.enabled:
            return 1.0
        return self.family_priority.get(fingerprint(expression)["family"], 1.0)

    def has_tried_field(self, expression: str, field_name: str) -> bool:
        """Trường dữ liệu này đã được thử trong họ cấu trúc đó chưa."""
        family = fingerprint(expression)["family"]
        return field_name in self.tested_fields.get(family, ())

    def is_duplicate(self, expression: str) -> bool:
        if not self.enabled or not self.seen_exact:
            return False
        return fingerprint(expression)["exact"] in self.seen_exact


class ResearchMemory:
    """Đọc lịch sử alpha và dựng hồ sơ theo họ cấu trúc."""

    def __init__(self, db: Database | str | Path):
        self.db = db if isinstance(db, Database) else Database(db)

    # ------------------------------------------------------------------
    def load_rows(self) -> List[Dict[str, Any]]:
        """Gộp alpha lịch sử đã nộp với alpha do hệ thống tự sinh.

        Cả hai đều là bằng chứng về việc một cấu trúc đã được thử. Bỏ qua một
        trong hai sẽ đánh giá thấp mức độ bão hòa của họ cấu trúc đó.
        """
        rows: List[Dict[str, Any]] = []
        connection = self.db.connect()
        try:
            for row in connection.execute(
                """
                SELECT family, fingerprint, expression, status, sharpe,
                       fields_json, operators_json, windows_json
                  FROM historical_alphas
                """
            ).fetchall():
                item = dict(row)
                item["origin"] = "historical"
                rows.append(item)

            for row in connection.execute(
                """
                SELECT family, fingerprint, expression, status, score, metrics_json
                  FROM alphas
                 WHERE status IN ('SIMULATED', 'PASSED', 'REJECTED', 'SUBMITTED')
                """
            ).fetchall():
                item = dict(row)
                metrics = _load_json(item.pop("metrics_json", "{}"))
                item["sharpe"] = metrics.get("sharpe")
                item["origin"] = "generated"
                rows.append(item)
        finally:
            connection.close()
        return rows

    # ------------------------------------------------------------------
    def profiles(self) -> Dict[str, ResearchProfile]:
        """Dựng hồ sơ cho từng họ cấu trúc từ toàn bộ lịch sử."""
        buckets: Dict[str, List[Dict[str, Any]]] = {}
        for row in self.load_rows():
            family = row.get("family") or row.get("fingerprint")
            if not family:
                # Bản ghi cũ chưa có vân tay thì tính lại từ biểu thức.
                expression = row.get("expression")
                if not expression:
                    continue
                family = fingerprint(expression)["family"]
            buckets.setdefault(str(family), []).append(row)

        result: Dict[str, ResearchProfile] = {}
        for family, group in buckets.items():
            sharpes = []
            for row in group:
                value = row.get("sharpe")
                if value is None:
                    continue
                try:
                    sharpes.append(abs(float(value)))
                except (TypeError, ValueError):
                    continue
            passed = sum(
                1 for row in group
                if str(row.get("status") or "").upper() in {"PASSED", "PASS", "ACTIVE", "SUBMITTED"}
            )
            windows: List[int] = []
            fields: List[str] = []
            operators: List[str] = []
            for row in group:
                windows.extend(_int_list(row.get("windows_json")))
                fields.extend(_str_list(row.get("fields_json")))
                operators.extend(_str_list(row.get("operators_json")))

            result[family] = ResearchProfile(
                family=family,
                count=len(group),
                passed=passed,
                median_sharpe=statistics.median(sharpes) if sharpes else None,
                best_sharpe=max(sharpes) if sharpes else None,
                windows=sorted(set(windows)),
                fields=sorted(set(fields)),
                operators=sorted(set(operators)),
            )
        return result

    # ------------------------------------------------------------------
    def _weights_by(
        self, key_of, *, target_sharpe: float
    ) -> Dict[str, float]:
        """Tính trọng số cho một cách gom nhóm bất kỳ.

        `key_of` nhận một hàng và trả về danh sách khóa mà hàng đó thuộc về.
        Một hàng có thể thuộc nhiều khóa, ví dụ biểu thức hai trường dữ liệu.
        """
        buckets: Dict[str, List[Dict[str, Any]]] = {}
        for row in self.load_rows():
            for key in key_of(row):
                if key:
                    buckets.setdefault(str(key), []).append(row)

        weights: Dict[str, float] = {}
        for key, group in buckets.items():
            if len(group) < MIN_SAMPLE:
                continue
            sharpes = []
            for row in group:
                value = row.get("sharpe")
                if value is None:
                    continue
                try:
                    sharpes.append(abs(float(value)))
                except (TypeError, ValueError):
                    continue
            if not sharpes:
                continue
            median = statistics.median(sharpes)
            ratio = median / target_sharpe if target_sharpe else 1.0
            confidence = min(1.0, len(group) / (MIN_SAMPLE * 4))
            weight = 1.0 + (ratio - 1.0) * confidence
            weights[key] = round(max(MIN_WEIGHT, min(MAX_WEIGHT, weight)), 4)
        return weights

    def build_context(
        self,
        *,
        target_sharpe: float = 1.25,
        avoid_duplicates: bool = True,
        enabled: bool = True,
    ) -> GenerationContext:
        """Dựng ngữ cảnh sinh từ lịch sử.

        `target_sharpe` là mốc để đánh giá một họ là tốt hay kém, thường lấy
        bằng ngưỡng Sharpe tối thiểu trong cấu hình chấm điểm.
        """
        if not enabled:
            return GenerationContext(enabled=False)

        profiles = self.profiles()
        weights: Dict[str, float] = {}
        tested_windows: Dict[str, List[int]] = {}
        tested_fields: Dict[str, List[str]] = {}
        tested_operators: Dict[str, List[str]] = {}
        saturation: Dict[str, float] = {}
        priority: Dict[str, float] = {}

        for family, profile in profiles.items():
            tested_windows[family] = profile.windows
            tested_fields[family] = profile.fields
            tested_operators[family] = profile.operators
            saturation[family] = profile.saturation
            # Ưu tiên là phần bù của bão hòa: càng ít khai thác càng đáng thử.
            priority[family] = round(1.0 - profile.saturation, 4)
            if profile.is_underexplored:
                # Chưa đủ dữ liệu để kết luận. Giữ nguyên ưu tiên.
                weights[family] = 1.0
                continue

            median = profile.median_sharpe
            if median is None:
                weights[family] = 1.0
                continue

            # Tỷ lệ giữa kết quả thực tế và mốc kỳ vọng.
            ratio = median / target_sharpe if target_sharpe else 1.0
            # Cỡ mẫu càng lớn thì kết luận càng đáng tin, nên phạt càng mạnh.
            confidence = min(1.0, profile.count / (MIN_SAMPLE * 4))
            weight = 1.0 + (ratio - 1.0) * confidence
            if profile.is_saturated:
                # Đã thử nhiều lần, chưa có alpha nào đạt.
                weight *= 0.5
            weights[family] = round(max(MIN_WEIGHT, min(MAX_WEIGHT, weight)), 4)

        # Trọng số ở hai mức rộng hơn, dùng khi một ứng viên chưa từng thuộc
        # họ cấu trúc nào đã biết.
        template_weights = self._weights_by(
            lambda row: [row.get("template")], target_sharpe=target_sharpe
        )
        field_weights = self._weights_by(
            lambda row: _str_list(row.get("fields_json")), target_sharpe=target_sharpe
        )

        seen_exact: set = set()
        if avoid_duplicates:
            seen_exact = self._existing_exact_fingerprints()

        logger.info(
            "Trí nhớ nghiên cứu: %d họ cấu trúc, %d biểu thức đã biết.",
            len(weights), len(seen_exact),
        )
        return GenerationContext(
            family_weights=weights,
            seen_exact=seen_exact,
            tested_windows=tested_windows,
            tested_fields=tested_fields,
            tested_operators=tested_operators,
            family_saturation=saturation,
            family_priority=priority,
            template_weights=template_weights,
            field_weights=field_weights,
            enabled=True,
        )

    def _existing_exact_fingerprints(self) -> set:
        """Tập vân tay chính xác của mọi biểu thức đã từng xuất hiện."""
        found: set = set()
        connection = self.db.connect()
        try:
            for table in ("historical_alphas", "alphas"):
                for row in connection.execute(
                    f"SELECT expression FROM {table} WHERE expression IS NOT NULL AND expression != ''"
                ).fetchall():
                    try:
                        found.add(fingerprint(row["expression"])["exact"])
                    except Exception:  # bản ghi hỏng không được chặn cả quá trình
                        continue
        finally:
            connection.close()
        return found

    # ------------------------------------------------------------------
    def describe_family(self, expression: str) -> Dict[str, Any]:
        """Trả lời câu hỏi: cấu trúc này đã được nghiên cứu chưa."""
        meta = fingerprint(expression)
        profiles = self.profiles()
        profile = profiles.get(meta["family"])
        if profile is None:
            return {
                "family": meta["family"],
                "researched": False,
                "count": 0,
                "message": "Chưa có alpha nào thuộc họ cấu trúc này.",
            }
        return {
            "family": meta["family"],
            "researched": True,
            "count": profile.count,
            "passed": profile.passed,
            "pass_rate": round(profile.pass_rate, 4),
            "median_sharpe": profile.median_sharpe,
            "best_sharpe": profile.best_sharpe,
            "tested_windows": profile.windows,
            "tested_fields": profile.fields,
            "operators": profile.operators,
            "is_saturated": profile.is_saturated,
            "is_underexplored": profile.is_underexplored,
            "saturation": profile.saturation,
            "confidence": profile.confidence,
        }


def _load_json(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _str_list(raw: Any) -> List[str]:
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def _int_list(raw: Any) -> List[int]:
    values = []
    for item in _str_list(raw):
        try:
            values.append(int(item))
        except (TypeError, ValueError):
            continue
    return values
