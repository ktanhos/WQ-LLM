"""Hàng đợi chạy mô phỏng.

Giới hạn đồng thời là ràng buộc quan trọng nhất. Tài khoản BRAIN có hạn mức mô
phỏng chạy song song theo cấp bậc người dùng. Vượt hạn mức thì máy chủ trả về
mã 429 và toàn bộ hàng đợi chậm lại thay vì nhanh hơn.

Mọi thay đổi trạng thái đều ghi xuống SQLite ngay lập tức để tiến trình bị ngắt
giữa chừng vẫn khôi phục được bằng lệnh reset.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, Optional

from ..brain.client import BrainClient
from ..brain.errors import (
    AuthenticationError,
    RateLimitError,
    SimulationError,
    TransientError,
)
from ..storage.db import AlphaRecord, Database, Status
from .scorer import Scorer

logger = logging.getLogger(__name__)


class AdaptiveLimiter:
    """Tự giảm tốc khi máy chủ báo vượt hạn mức.

    Không có lớp này, một lần bị 429 chỉ làm luồng gặp lỗi ngủ, còn các luồng
    khác vẫn gửi tiếp với đúng nhịp cũ và tiếp tục bị từ chối. Kết quả là hàng
    đợi chậm đi chứ không nhanh lên.

    Cơ chế: khi gặp 429 thì tạm dừng cả hàng đợi cho tới hết thời gian máy chủ
    yêu cầu, đồng thời giảm một nửa số mô phỏng chạy song song. Sau một chuỗi
    lượt thành công thì nới dần trở lại, không bao giờ vượt mức người dùng đặt.
    """

    #: Số lượt thành công liên tiếp cần có trước khi nới thêm một chỗ.
    RECOVERY_STREAK = 10

    def __init__(self, max_slots: int):
        self.max_slots = max(1, int(max_slots))
        self._slots = self.max_slots
        self._throttled_until = 0.0
        self._successes = 0
        self._lock = threading.Lock()

    @property
    def slots(self) -> int:
        with self._lock:
            return self._slots

    def wait(self) -> None:
        """Chặn cho tới khi hết thời gian tạm dừng chung."""
        while True:
            with self._lock:
                remaining = self._throttled_until - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 1.0))

    def on_rate_limit(self, retry_after: float) -> None:
        with self._lock:
            self._throttled_until = max(
                self._throttled_until, time.monotonic() + max(float(retry_after), 1.0)
            )
            self._successes = 0
            if self._slots > 1:
                self._slots = max(1, self._slots // 2)
                logger.warning(
                    "Bị giới hạn tần suất. Giảm mức đồng thời xuống %d, chờ %.1f giây.",
                    self._slots, retry_after,
                )

    def on_success(self) -> None:
        with self._lock:
            if self._slots >= self.max_slots:
                return
            self._successes += 1
            if self._successes >= self.RECOVERY_STREAK:
                self._successes = 0
                self._slots += 1
                logger.info("Máy chủ đã ổn định. Nâng mức đồng thời lên %d.", self._slots)


class SimulationRunner:
    def __init__(
        self,
        client: BrainClient,
        db: Database,
        scorer: Optional[Scorer] = None,
        concurrency: int = 3,
        max_attempts: int = 3,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self.client = client
        self.db = db
        self.scorer = scorer
        self.concurrency = max(1, int(concurrency))
        self.max_attempts = max(1, int(max_attempts))
        self.progress_callback = progress_callback
        self._stop = threading.Event()
        self._counter_lock = threading.Lock()
        #: Mức đồng thời do người dùng đặt là trần, không phải giá trị cố định.
        self.limiter = AdaptiveLimiter(self.concurrency)
        self.stats = {"simulated": 0, "passed": 0, "rejected": 0, "failed": 0}

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------
    def run(self, limit: Optional[int] = None) -> Dict[str, int]:
        """Chạy cho tới khi hết bản ghi chờ hoặc đạt số lượng yêu cầu."""
        processed = 0
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = set()
            while not self._stop.is_set():
                if limit is not None and processed >= limit:
                    break

                # Mức đồng thời hiệu dụng do bộ giảm tốc quyết định, nên hàng
                # đợi tự co lại khi máy chủ đang từ chối.
                free_slots = self.limiter.slots - len(futures)
                if free_slots > 0:
                    remaining = None if limit is None else limit - processed - len(futures)
                    take = free_slots if remaining is None else max(0, min(free_slots, remaining))
                    if take > 0:
                        records = self.db.claim_pending(take)
                        for record in records:
                            futures.add(pool.submit(self._process, record))

                if not futures:
                    break

                done = {future for future in futures if future.done()}
                if not done:
                    time.sleep(0.5)
                    continue
                for future in done:
                    futures.discard(future)
                    processed += 1
                    try:
                        future.result()
                    except Exception as exc:  # pragma: no cover
                        logger.exception("Lỗi ngoài dự kiến trong luồng chạy: %s", exc)

            for future in as_completed(list(futures)):
                try:
                    future.result()
                except Exception as exc:  # pragma: no cover
                    logger.exception("Lỗi ngoài dự kiến khi thu dọn hàng đợi: %s", exc)

        return dict(self.stats)

    # ------------------------------------------------------------------
    def _process(self, record: AlphaRecord) -> None:
        if self._stop.is_set():
            self.db.update_alpha(record.id, status=Status.PENDING)
            return
        # Chờ hết thời gian tạm dừng chung trước khi gửi thêm yêu cầu.
        self.limiter.wait()
        try:
            result = self.client.simulate(record.expression, record.settings)
        except SimulationError as exc:
            # Lỗi thuộc về biểu thức, thử lại cũng vô ích.
            self.db.update_alpha(
                record.id,
                status=Status.FAILED,
                error=f"{exc} {exc.detail}".strip(),
            )
            self._bump("failed")
            self.db.log_event("ERROR", f"Biểu thức lỗi id={record.id}: {exc}")
            return
        except RateLimitError as exc:
            # Giảm tốc toàn hàng đợi, không chỉ luồng hiện tại.
            self.limiter.on_rate_limit(exc.retry_after)
            self._requeue(record, str(exc))
            return
        except (TransientError, AuthenticationError) as exc:
            self._requeue(record, str(exc))
            return

        fields: Dict[str, Any] = {
            "alpha_id": result.alpha_id,
            "metrics": result.metrics,
            "status": Status.SIMULATED,
            "error": None,
        }
        if self.scorer is not None:
            verdict = self.scorer.evaluate(result.metrics)
            fields["score"] = verdict.score
            fields["status"] = Status.PASSED if verdict.passed else Status.REJECTED
            fields["reject_reason"] = verdict.reason_text or None
            self._bump("passed" if verdict.passed else "rejected")
        self._bump("simulated")
        self.limiter.on_success()
        self.db.update_alpha(record.id, **fields)
        self._link_lineage(record, result.alpha_id)

        if self.progress_callback:
            self.progress_callback(
                {
                    "id": record.id,
                    "alpha_id": result.alpha_id,
                    "status": fields["status"],
                    "metrics": result.metrics,
                }
            )

    def _link_lineage(self, record: AlphaRecord, alpha_id: str) -> None:
        """Nối mã alpha của nền tảng vào phả hệ cục bộ.

        Bọc trong try vì phả hệ là dữ liệu bổ trợ: hỏng phả hệ không được làm
        mất kết quả mô phỏng vừa tốn hạn mức để có.
        """
        if not record.local_id or not alpha_id:
            return
        try:
            from ..research.store import ResearchStore

            ResearchStore(self.db).link_platform_alpha(record.local_id, alpha_id)
        except Exception as exc:  # pragma: no cover
            logger.warning("Không nối được phả hệ cho %s: %s", alpha_id, exc)

    def _requeue(self, record: AlphaRecord, message: str) -> None:
        """Trả bản ghi về hàng đợi, chuyển sang FAILED khi hết số lần thử."""
        if record.attempts >= self.max_attempts:
            self.db.update_alpha(
                record.id,
                status=Status.FAILED,
                error=f"Hết số lần thử. Lỗi cuối: {message}",
            )
            self._bump("failed")
            return
        self.db.update_alpha(record.id, status=Status.PENDING, error=message)

    def _bump(self, key: str) -> None:
        with self._counter_lock:
            self.stats[key] = self.stats.get(key, 0) + 1
