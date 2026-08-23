"""Kiểm tra tương quan.

Tự tương quan cao là nguyên nhân bị loại phổ biến hơn cả chỉ số kém, vì một
biểu thức tốt nhưng trùng ý tưởng với alpha đã có thì không đóng góp thêm giá trị.

Điểm cuối tương quan tốn tài nguyên máy chủ nên chỉ chạy trên nhóm đã đạt ngưỡng
chỉ số, và có nghỉ giữa các lần gọi.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from ..brain.client import BrainClient
from ..brain.errors import BrainError
from ..storage.db import Database, Status

logger = logging.getLogger(__name__)


def max_correlation(payload: Dict[str, Any]) -> Optional[float]:
    """Rút giá trị tương quan lớn nhất theo trị tuyệt đối từ phản hồi của máy chủ.

    Phản hồi có dạng bảng gồm schema và records. Cấu trúc này có thể thay đổi,
    nên hàm dò theo tên cột thay vì giả định vị trí cố định.
    """
    records = payload.get("records") or []
    if not records:
        return None

    schema = payload.get("schema") or {}
    properties = schema.get("properties") or []
    names = [str(item.get("name", "")).lower() for item in properties]

    index = None
    for candidate in ("correlation", "max", "value"):
        if candidate in names:
            index = names.index(candidate)
            break

    values: List[float] = []
    for record in records:
        if isinstance(record, dict):
            for key in ("correlation", "max", "value"):
                if key in record:
                    values.append(_to_float(record[key]))
                    break
            continue
        if isinstance(record, (list, tuple)) and record:
            if index is not None and index < len(record):
                values.append(_to_float(record[index]))
            else:
                numeric = [_to_float(item) for item in record]
                numeric = [item for item in numeric if item is not None]
                if numeric:
                    values.append(max(numeric, key=abs))

    values = [value for value in values if value is not None]
    if not values:
        return None
    return max(values, key=abs)


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class CorrelationChecker:
    def __init__(
        self,
        client: BrainClient,
        db: Database,
        config: Dict[str, Any],
        check_prod: bool = False,
    ):
        self.client = client
        self.db = db
        self.config = config or {}
        self.check_prod = check_prod
        self.pause = float(self.config.get("batch_pause_seconds", 2))

    def run(self, limit: int = 200) -> Dict[str, int]:
        max_self = float(self.config.get("max_self_correlation", 0.7))
        max_prod = float(self.config.get("max_prod_correlation", 0.7))
        stats = {"checked": 0, "rejected": 0, "skipped": 0, "errors": 0}

        for record in self.db.fetch_by_status(Status.PASSED, limit=limit):
            if not record.alpha_id:
                stats["skipped"] += 1
                continue
            if record.self_correlation is not None:
                stats["skipped"] += 1
                continue
            try:
                self_corr = max_correlation(
                    self.client.get_self_correlation(record.alpha_id)
                )
                prod_corr = None
                if self.check_prod:
                    time.sleep(self.pause)
                    prod_corr = max_correlation(
                        self.client.get_prod_correlation(record.alpha_id)
                    )
            except BrainError as exc:
                logger.warning("Không đọc được tương quan cho %s: %s", record.alpha_id, exc)
                stats["errors"] += 1
                continue

            fields: Dict[str, Any] = {
                "self_correlation": self_corr,
                "prod_correlation": prod_corr,
            }
            reasons = []
            if self_corr is not None and abs(self_corr) > max_self:
                reasons.append(
                    f"Tự tương quan {self_corr:.3f} vượt ngưỡng {max_self:.3f}."
                )
            if prod_corr is not None and abs(prod_corr) > max_prod:
                reasons.append(
                    f"Tương quan với danh mục sản phẩm {prod_corr:.3f} vượt ngưỡng {max_prod:.3f}."
                )
            if reasons:
                fields["status"] = Status.REJECTED
                existing = record.reject_reason or ""
                fields["reject_reason"] = "; ".join(filter(None, [existing, *reasons]))
                stats["rejected"] += 1

            self.db.update_alpha(record.id, **fields)
            stats["checked"] += 1
            time.sleep(self.pause)

        return stats
