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
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ..history.fingerprint import fingerprint
from ..storage.db import Database, EvaluationStatus, Status

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

#: Nhãn nguồn của một bằng chứng nghiên cứu.
SOURCE_HISTORICAL = "historical"
SOURCE_CURRENT = "current"
SOURCE_EXPERIMENT = "experiment"
SOURCE_SUBMITTED = "submitted"
SOURCE_SIMULATION = "simulation"
SOURCES = (
    SOURCE_HISTORICAL, SOURCE_CURRENT, SOURCE_EXPERIMENT,
    SOURCE_SUBMITTED, SOURCE_SIMULATION,
)

#: Các chiều nghiên cứu được theo dõi độ phủ.
DIMENSIONS = (
    "field", "operator", "lookback", "family", "template",
    "region", "universe", "delay", "neutralization",
)

#: Trạng thái được coi là alpha đạt, gồm cả nhãn của nền tảng lẫn nhãn nội bộ.
SUCCESS_STATUSES = frozenset({
    "PASSED", "PASS", "ACTIVE", "SUBMITTED", "IS", "CANDIDATE", "HUMAN_REVIEW",
})
FAILURE_STATUSES = frozenset({"FAILED", "FAIL", "ERROR", "REJECTED", "REJECT", "INVALID"})


def _is_submitted(status: Any) -> bool:
    return str(status or "").upper() in {"SUBMITTED", "ACTIVE", "IS"}


def _is_successful(status: Any) -> bool:
    return str(status or "").upper() in SUCCESS_STATUSES


def _is_failed(status: Any) -> bool:
    return str(status or "").upper() in FAILURE_STATUSES


def _classify_local(row: Dict[str, Any]) -> str:
    """Xếp nhãn nguồn cho một bản ghi trong bảng alphas."""
    status = str(row.get("status") or "").upper()
    if status == Status.SUBMITTED:
        return SOURCE_SUBMITTED
    if row.get("experiment_id"):
        return SOURCE_EXPERIMENT
    if status in (Status.PENDING, Status.RUNNING, Status.INVALID):
        return SOURCE_CURRENT
    return SOURCE_SIMULATION


def _meta(row: Dict[str, Any]) -> Dict[str, Any]:
    """Vân tay tính lại từ biểu thức khi bản ghi chưa có sẵn thành phần."""
    expression = row.get("expression")
    return fingerprint(expression) if expression else fingerprint("")


def _fields_of(row: Dict[str, Any]) -> List[str]:
    return _meta(row)["fields"]


def _operators_of(row: Dict[str, Any]) -> List[str]:
    return _meta(row)["operators"]


def _windows_of(row: Dict[str, Any]) -> List[int]:
    return _meta(row)["windows"]


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
    def load_rows(self, sources: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
        """Gộp mọi bằng chứng nghiên cứu về một dạng bản ghi thống nhất.

        Alpha đã nộp trên nền tảng và alpha do hệ thống tự sinh đều là bằng
        chứng rằng một cấu trúc đã được thử. Bỏ qua một trong hai sẽ đánh giá
        thấp mức độ bão hòa của họ cấu trúc đó.

        Mỗi bản ghi mang một nhãn `source` để nơi gọi phân biệt được nguồn gốc:

            historical   nhập về từ lịch sử nộp trên nền tảng
            simulation   hệ thống đã mô phỏng, chưa gắn với thí nghiệm nào
            experiment   sinh ra trong khuôn khổ một thí nghiệm
            submitted    đã nộp, dù đến từ nguồn nào
            current      đang trong hàng đợi, chưa có kết quả

        Tham số `sources` lọc theo nhãn đó. Bỏ trống thì lấy tất cả.
        """
        wanted = set(sources) if sources else None
        rows: List[Dict[str, Any]] = []
        connection = self.db.connect()
        try:
            for row in connection.execute(
                """
                SELECT alpha_id, family, fingerprint, template, expression, status,
                       sharpe, fitness, turnover, region, universe, delay,
                       neutralization, submitted, fields_json, operators_json,
                       windows_json
                  FROM historical_alphas
                """
            ).fetchall():
                item = dict(row)
                item["source"] = (
                    SOURCE_SUBMITTED
                    if _is_submitted(item.get("status"))
                    else SOURCE_HISTORICAL
                )
                # Giữ khóa cũ để mã đã viết trước đây không phải sửa.
                item["origin"] = "historical"
                rows.append(item)

            for row in connection.execute(
                """
                SELECT id, alpha_id, family, fingerprint, template, expression,
                       status, evaluation_status, score, metrics_json, settings_json,
                       experiment_id, variant_id, plan_id, source_type,
                       self_correlation, prod_correlation, reject_reason
                  FROM alphas
                """
            ).fetchall():
                item = dict(row)
                metrics = _load_json(item.pop("metrics_json", "{}"))
                settings = _load_json(item.pop("settings_json", "{}"))
                item["sharpe"] = metrics.get("sharpe")
                item["fitness"] = metrics.get("fitness")
                item["turnover"] = metrics.get("turnover")
                item["region"] = settings.get("region")
                item["universe"] = settings.get("universe")
                item["delay"] = settings.get("delay")
                item["neutralization"] = settings.get("neutralization")
                item["metrics"] = metrics
                item["source"] = _classify_local(item)
                item["origin"] = "generated"
                rows.append(item)
        finally:
            connection.close()

        if wanted is not None:
            rows = [row for row in rows if row["source"] in wanted]
        return rows

    # ------------------------------------------------------------------
    # Trả lời câu hỏi "cái gì đã được nghiên cứu"
    # ------------------------------------------------------------------
    def coverage(self, sources: Optional[Sequence[str]] = None) -> Dict[str, Counter]:
        """Đếm số alpha theo từng chiều nghiên cứu.

        Một bản ghi có thể đóng góp vào nhiều khóa của cùng một chiều, ví dụ
        biểu thức dùng hai trường dữ liệu.
        """
        counters: Dict[str, Counter] = {
            dimension: Counter() for dimension in DIMENSIONS
        }
        for row in self.load_rows(sources):
            counters["field"].update(_str_list(row.get("fields_json")) or _fields_of(row))
            counters["operator"].update(
                _str_list(row.get("operators_json")) or _operators_of(row)
            )
            counters["lookback"].update(
                str(value) for value in (_int_list(row.get("windows_json")) or _windows_of(row))
            )
            for dimension, key in (
                ("family", row.get("family") or row.get("fingerprint")),
                ("template", row.get("template")),
                ("region", row.get("region")),
                ("universe", row.get("universe")),
                ("delay", row.get("delay")),
                ("neutralization", row.get("neutralization")),
            ):
                if key is not None and key != "":
                    counters[dimension][str(key)] += 1
        return counters

    def fields_researched(self, sources: Optional[Sequence[str]] = None) -> Dict[str, int]:
        return dict(self.coverage(sources)["field"])

    def operators_researched(self, sources: Optional[Sequence[str]] = None) -> Dict[str, int]:
        return dict(self.coverage(sources)["operator"])

    def families_researched(self, sources: Optional[Sequence[str]] = None) -> Dict[str, int]:
        return dict(self.coverage(sources)["family"])

    def structures_researched(self, sources: Optional[Sequence[str]] = None) -> Dict[str, int]:
        """Đếm theo template, tức cấu trúc đã bỏ qua tên trường dữ liệu."""
        return dict(self.coverage(sources)["template"])

    def lookbacks_tried(self, sources: Optional[Sequence[str]] = None) -> Dict[str, int]:
        return dict(self.coverage(sources)["lookback"])

    def regions_tried(self, sources: Optional[Sequence[str]] = None) -> Dict[str, int]:
        return dict(self.coverage(sources)["region"])

    def universes_tried(self, sources: Optional[Sequence[str]] = None) -> Dict[str, int]:
        return dict(self.coverage(sources)["universe"])

    def neutralizations_tried(self, sources: Optional[Sequence[str]] = None) -> Dict[str, int]:
        return dict(self.coverage(sources)["neutralization"])

    def delays_tried(self, sources: Optional[Sequence[str]] = None) -> Dict[str, int]:
        return dict(self.coverage(sources)["delay"])

    # ------------------------------------------------------------------
    # Trả lời câu hỏi "cái gì đạt, cái gì không"
    # ------------------------------------------------------------------
    def successful_alphas(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Alpha đạt ngưỡng, xếp theo Sharpe tuyệt đối giảm dần."""
        rows = [
            row for row in self.load_rows()
            if _is_successful(row.get("status")) and row.get("sharpe") is not None
        ]
        rows.sort(key=lambda row: abs(float(row["sharpe"])), reverse=True)
        return rows[:limit]

    def failed_alphas(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Alpha thất bại. Vẫn là thông tin nghiên cứu, không phải rác."""
        rows = [row for row in self.load_rows() if _is_failed(row.get("status"))]
        return rows[:limit]

    def correlation_rejected(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Alpha bị loại riêng vì tương quan, không phải vì chỉ số kém.

        Phân biệt hai nhóm này quan trọng: alpha bị loại vì tương quan nghĩa là
        ý tưởng đúng nhưng đã có người khai thác, còn alpha chỉ số kém nghĩa là
        ý tưởng chưa hiệu quả. Hai kết luận nghiên cứu hoàn toàn khác nhau.
        """
        connection = self.db.connect()
        try:
            rows = connection.execute(
                """
                SELECT id, alpha_id, expression, family, self_correlation,
                       prod_correlation, reject_reason, evaluation_status
                  FROM alphas
                 WHERE evaluation_status = ?
                    OR (reject_reason IS NOT NULL AND reject_reason LIKE '%tương quan%')
                 ORDER BY ABS(COALESCE(self_correlation, 0)) DESC
                 LIMIT ?
                """,
                (EvaluationStatus.CORRELATION_FAILED, int(limit)),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()

    def submitted_alphas(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self.load_rows([SOURCE_SUBMITTED])[:limit]

    def candidates(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Alpha đã qua mọi bước tự động và đang chờ người quyết định."""
        connection = self.db.connect()
        try:
            rows = connection.execute(
                """
                SELECT id, alpha_id, expression, score, metrics_json, family,
                       experiment_id, evaluation_status, status
                  FROM alphas
                 WHERE status IN (?, ?) OR evaluation_status = ?
                 ORDER BY COALESCE(score, 0) DESC
                 LIMIT ?
                """,
                (Status.CANDIDATE, Status.HUMAN_REVIEW, EvaluationStatus.CANDIDATE,
                 int(limit)),
            ).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                item["metrics"] = _load_json(item.pop("metrics_json", "{}"))
                items.append(item)
            return items
        finally:
            connection.close()

    # ------------------------------------------------------------------
    def experiment_outcome(self, experiment_id: int) -> Dict[str, Any]:
        """Kết quả tổng hợp của một thí nghiệm.

        Đây là đường phản hồi từ kết quả mô phỏng quay lại trí nhớ nghiên cứu:
        sau khi chạy xong, hệ thống biết thí nghiệm đó sinh ra bao nhiêu alpha,
        bao nhiêu đạt, bao nhiêu bền và bao nhiêu thành ứng viên.
        """
        connection = self.db.connect()
        try:
            rows = [
                dict(row) for row in connection.execute(
                    """
                    SELECT status, evaluation_status, score, metrics_json, expression
                      FROM alphas WHERE experiment_id = ?
                    """,
                    (int(experiment_id),),
                ).fetchall()
            ]
        finally:
            connection.close()

        sharpes = []
        for row in rows:
            metrics = _load_json(row.get("metrics_json"))
            value = metrics.get("sharpe")
            if value is None:
                continue
            try:
                sharpes.append(abs(float(value)))
            except (TypeError, ValueError):
                continue

        statuses = Counter(str(row.get("status") or "") for row in rows)
        evaluations = Counter(str(row.get("evaluation_status") or "") for row in rows)
        total = len(rows)
        passed = statuses.get(Status.PASSED, 0) + statuses.get(Status.CANDIDATE, 0)
        return {
            "experiment_id": int(experiment_id),
            "total": total,
            "simulated": total - statuses.get(Status.PENDING, 0) - statuses.get(Status.INVALID, 0),
            "invalid": statuses.get(Status.INVALID, 0),
            "passed": passed,
            "rejected": statuses.get(Status.REJECTED, 0),
            "failed": statuses.get(Status.FAILED, 0),
            "robust": evaluations.get(EvaluationStatus.ROBUST, 0)
            + evaluations.get(EvaluationStatus.CORRELATION_PASS, 0)
            + evaluations.get(EvaluationStatus.CANDIDATE, 0),
            "candidates": evaluations.get(EvaluationStatus.CANDIDATE, 0),
            "pass_rate": round(passed / total, 4) if total else 0.0,
            "median_sharpe": round(statistics.median(sharpes), 6) if sharpes else None,
            "best_sharpe": round(max(sharpes), 6) if sharpes else None,
            "sample_size": len(sharpes),
        }

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
