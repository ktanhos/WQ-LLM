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

                free_slots = self.concurrency - len(futures)
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
            time.sleep(exc.retry_after)
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
        self.db.update_alpha(record.id, **fields)

        if self.progress_callback:
            self.progress_callback(
                {
                    "id": record.id,
                    "alpha_id": result.alpha_id,
                    "status": fields["status"],
                    "metrics": result.metrics,
                }
            )

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
