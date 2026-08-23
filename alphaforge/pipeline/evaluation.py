"""Bậc thang thẩm định từ alpha đã mô phỏng tới ứng viên chờ người quyết định.

Thứ tự các bước không tùy tiện. Mỗi bước tốn tài nguyên hơn bước trước, nên
alpha phải vượt bước rẻ mới được đưa lên bước đắt:

    1. chấm điểm            đọc chỉ số đã lưu, không tốn gì
    2. độ bền               đọc chỉ số đã lưu, không tốn gì
    3. tương đồng cấu trúc  so vân tay trong kho, không gọi mạng
    4. tự tương quan        gọi máy chủ, tốn tài nguyên
    5. tương quan sản phẩm  gọi máy chủ, tốn nhất

Bước ba đặc biệt đáng giá: nó loại được alpha trùng ý tưởng với thứ đã có
**trước khi** tốn một lượt gọi tương quan. Hai biểu thức cùng họ cấu trúc gần
như chắc chắn tương quan cao, và điều đó biết được tại chỗ.

Không bước nào ở đây tự nộp alpha. Bậc cuối cùng là `CANDIDATE`, và chỉ người
nghiên cứu mới chuyển tiếp được.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from ..history.fingerprint import fingerprint
from ..storage.db import Database, EvaluationStatus, Status, utc_now
from .robustness import RobustnessChecker
from .scorer import Scorer

logger = logging.getLogger(__name__)

#: Ngưỡng tương đồng cấu trúc cục bộ. Trên mức này thì coi như trùng ý tưởng
#: với một alpha đã có và không đáng tốn lượt gọi tương quan.
DEFAULT_STRUCTURAL_LIMIT = 0.85


@dataclass
class EvaluationOutcome:
    """Số bản ghi đi qua từng bậc trong một lượt thẩm định."""

    scored: int = 0
    robust: int = 0
    robust_failed: int = 0
    structural_duplicates: int = 0
    candidates: int = 0
    skipped: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "scored": self.scored,
            "robust": self.robust,
            "robust_failed": self.robust_failed,
            "structural_duplicates": self.structural_duplicates,
            "candidates": self.candidates,
            "skipped": self.skipped,
        }


class EvaluationPipeline:
    """Đưa alpha đã mô phỏng lên các bậc thẩm định tiếp theo.

    Lớp này không gọi mạng. Bước tương quan cần máy chủ nằm ở
    `pipeline.correlation.CorrelationChecker` và chạy sau, chỉ trên những bản
    ghi đã đạt `CORRELATION_PASS` ở đây.
    """

    def __init__(
        self,
        db: Database,
        scorer: Scorer,
        *,
        robustness: Optional[RobustnessChecker] = None,
        structural_limit: float = DEFAULT_STRUCTURAL_LIMIT,
    ):
        self.db = db
        self.scorer = scorer
        self.robustness = robustness or RobustnessChecker()
        self.structural_limit = float(structural_limit)

    # ------------------------------------------------------------------
    def run(self, limit: int = 500) -> EvaluationOutcome:
        """Chạy toàn bộ bậc thang trên các alpha đã mô phỏng."""
        outcome = EvaluationOutcome()
        records = [
            record for record in self.db.fetch_by_status(Status.PASSED, limit=limit)
        ]
        records.extend(self.db.fetch_by_status(Status.SIMULATED, limit=limit))

        reference = self._reference_expressions()
        for record in records:
            if not record.metrics:
                outcome.skipped += 1
                continue

            verdict = self.scorer.evaluate(record.metrics)
            fields: Dict[str, Any] = {
                "score": verdict.score,
                "evaluation_status": EvaluationStatus.SCORED,
            }
            outcome.scored += 1

            if not verdict.passed:
                fields["status"] = Status.REJECTED
                fields["reject_reason"] = verdict.reason_text or None
                self.db.update_alpha(record.id, **fields)
                continue

            report = self.robustness.evaluate(
                record.metrics, variants=self._variant_metrics(record)
            )
            fields["robustness"] = report.as_dict()
            if not report.passed:
                fields["evaluation_status"] = EvaluationStatus.ROBUST_FAILED
                fields["status"] = Status.REJECTED
                fields["reject_reason"] = (
                    "Không đạt kiểm tra độ bền: " + ", ".join(report.failures()) + "."
                )
                outcome.robust_failed += 1
                self.db.update_alpha(record.id, **fields)
                continue

            fields["evaluation_status"] = EvaluationStatus.ROBUST
            outcome.robust += 1

            similar = self._structural_match(record, reference)
            if similar is not None:
                fields["evaluation_status"] = EvaluationStatus.CORRELATION_FAILED
                fields["status"] = Status.REJECTED
                fields["reject_reason"] = (
                    f"Trùng cấu trúc với alpha đã có ({similar}). "
                    "Không tốn lượt gọi tương quan cho trường hợp này."
                )
                outcome.structural_duplicates += 1
                self.db.update_alpha(record.id, **fields)
                self.record_correlation(
                    record.id, record.alpha_id, "structural",
                    value=1.0, reference=similar, threshold=self.structural_limit,
                    status="FAILED",
                )
                continue

            fields["evaluation_status"] = EvaluationStatus.CORRELATION_PASS
            fields["status"] = Status.PASSED
            self.db.update_alpha(record.id, **fields)

        return outcome

    # ------------------------------------------------------------------
    def promote_to_candidate(self, alpha_row_id: int) -> bool:
        """Đưa một alpha đã qua mọi bước tự động lên bậc ứng viên.

        Ứng viên **không** đồng nghĩa với đã nộp. Nó chỉ nghĩa là mọi bước máy
        làm được đều đã xong và giờ tới lượt người xem xét.
        """
        record = self.db.get_alpha(alpha_row_id)
        if record is None:
            return False
        if record.evaluation_status != EvaluationStatus.CORRELATION_PASS:
            logger.info(
                "Alpha %s chưa qua bước tương quan nên chưa thể thành ứng viên.",
                alpha_row_id,
            )
            return False
        self.db.update_alpha(
            alpha_row_id,
            status=Status.CANDIDATE,
            evaluation_status=EvaluationStatus.CANDIDATE,
        )
        return True

    def send_to_review(self, alpha_row_id: int) -> bool:
        """Chuyển ứng viên sang trạng thái người đang xem xét."""
        record = self.db.get_alpha(alpha_row_id)
        if record is None or record.status != Status.CANDIDATE:
            return False
        self.db.update_alpha(alpha_row_id, status=Status.HUMAN_REVIEW)
        return True

    def mark_submitted(self, alpha_row_id: int, *, confirmed_by: str = "") -> bool:
        """Ghi nhận người dùng đã tự nộp alpha trên nền tảng.

        Hệ thống không nộp thay. Hàm này chỉ ghi lại việc người dùng nói rằng
        họ đã nộp, và chỉ chấp nhận khi alpha đang ở bậc do người kiểm soát.
        """
        record = self.db.get_alpha(alpha_row_id)
        if record is None:
            return False
        if record.status not in (Status.CANDIDATE, Status.HUMAN_REVIEW):
            logger.warning(
                "Alpha %s đang ở trạng thái %s, không thể đánh dấu đã nộp.",
                alpha_row_id, record.status,
            )
            return False
        self.db.update_alpha(alpha_row_id, status=Status.SUBMITTED)
        self.db.log_event(
            "INFO",
            f"Alpha {record.alpha_id or alpha_row_id} được đánh dấu đã nộp"
            + (f" bởi {confirmed_by}." if confirmed_by else "."),
        )
        return True

    # ------------------------------------------------------------------
    def record_correlation(
        self,
        alpha_row_id: int,
        alpha_id: Optional[str],
        correlation_type: str,
        *,
        value: Optional[float],
        reference: Optional[str] = None,
        threshold: Optional[float] = None,
        status: str = "",
    ) -> None:
        """Lưu một kết quả tương quan kèm ngưỡng đã dùng.

        Lưu cả ngưỡng vì ngưỡng thay đổi theo thời gian; không có nó thì sau
        này không hiểu vì sao alpha bị loại.
        """
        connection = self.db.connect()
        try:
            connection.execute(
                """
                INSERT INTO correlation_results (
                    alpha_row_id, alpha_id, correlation_type, correlation_value,
                    reference_alpha, threshold, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (int(alpha_row_id), alpha_id, correlation_type, value,
                 reference, threshold, status, utc_now()),
            )
        finally:
            connection.close()

    def correlation_history(self, alpha_row_id: int) -> List[Dict[str, Any]]:
        connection = self.db.connect()
        try:
            return [
                dict(row) for row in connection.execute(
                    "SELECT * FROM correlation_results WHERE alpha_row_id = ?"
                    " ORDER BY id",
                    (int(alpha_row_id),),
                ).fetchall()
            ]
        finally:
            connection.close()

    # ------------------------------------------------------------------
    def _reference_expressions(self) -> List[str]:
        """Biểu thức đã đạt hoặc đã nộp, dùng làm mốc so tương đồng cấu trúc."""
        connection = self.db.connect()
        try:
            rows = connection.execute(
                """
                SELECT expression FROM historical_alphas
                 WHERE expression IS NOT NULL AND expression != ''
                UNION
                SELECT expression FROM alphas
                 WHERE status IN (?, ?, ?)
                   AND expression IS NOT NULL AND expression != ''
                """,
                (Status.CANDIDATE, Status.HUMAN_REVIEW, Status.SUBMITTED),
            ).fetchall()
            return [str(row["expression"]) for row in rows]
        finally:
            connection.close()

    def _structural_match(self, record, reference: Sequence[str]) -> Optional[str]:
        """Tìm alpha đã có trùng ý tưởng với bản ghi đang xét.

        Mức so sánh phụ thuộc vào việc alpha có thuộc một thí nghiệm hay không,
        và đây là điểm mấu chốt:

            sinh tự do   so ở mức họ cấu trúc. Một biểu thức tình cờ trùng ý
                         tưởng với alpha đã có thì không đáng tốn lượt gọi
                         tương quan.

            thí nghiệm   so ở mức tham số. Khảo sát nhiều cửa sổ trong cùng một
                         họ chính là mục đích của thí nghiệm có kiểm soát, nên
                         chặn ở mức họ sẽ giết sạch mọi biến thể và làm thí
                         nghiệm vô nghĩa.

        Nói cách khác: trùng lặp ngẫu nhiên bị chặn, còn khảo sát có chủ đích
        thì không.
        """
        if not reference:
            return None
        target = fingerprint(record.expression)
        level = "parameter" if record.experiment_id else "family"
        for expression in reference:
            if expression == record.expression:
                continue
            other = fingerprint(expression)
            if other[level] == target[level]:
                return expression[:80]
        return None

    def _variant_metrics(self, record) -> List[Dict[str, Any]]:
        """Chỉ số của các alpha cùng thí nghiệm, dùng cho phép kiểm độ nhạy."""
        if not record.experiment_id:
            return []
        connection = self.db.connect()
        try:
            rows = connection.execute(
                """
                SELECT metrics_json FROM alphas
                 WHERE experiment_id = ? AND id != ? AND metrics_json != '{}'
                """,
                (int(record.experiment_id), int(record.id)),
            ).fetchall()
        finally:
            connection.close()

        import json

        variants = []
        for row in rows:
            try:
                value = json.loads(row["metrics_json"])
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict):
                variants.append(value)
        return variants
