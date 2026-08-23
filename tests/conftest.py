"""Đồ dùng chung cho bộ kiểm thử.

Không có kiểm thử nào trong kho này được phép gọi máy chủ BRAIN thật. Máy chủ
được thay bằng bản giả lập dưới đây, nên toàn bộ bộ kiểm thử chạy ngoại tuyến
và không tốn hạn mức tài khoản.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pytest

from alphaforge.brain.errors import BrainError, RateLimitError, TransientError
from alphaforge.storage.db import Database


class FakeResponse:
    """Phản hồi HTTP tối giản, đủ dùng cho lớp client."""

    def __init__(
        self,
        payload: Any = None,
        status_code: int = 200,
        headers: Optional[Dict[str, str]] = None,
        text: str = "",
    ):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text or json.dumps(payload, ensure_ascii=False, default=str)
        self.content = self.text.encode("utf-8")

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("Không có nội dung JSON.")
        return self._payload


class FakeBrainClient:
    """Bản giả lập BrainClient cho lớp lịch sử.

    Ghi lại mọi địa chỉ đã gọi để kiểm thử khẳng định được về phân trang và
    tham số truy vấn. Có thể lập trình để trả lỗi ở lần gọi thứ n.
    """

    def __init__(self, pages: Optional[List[Any]] = None, base_url: str = "https://api.test"):
        self.pages = list(pages or [])
        self.base_url = base_url
        self.calls: List[str] = []
        #: Số lần đã gặp lỗi tạm thời, để kiểm thử phần thử lại.
        self.raise_on_call: Dict[int, Exception] = {}

    def request(self, method: str, path_or_url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(path_or_url)
        index = len(self.calls) - 1
        if index in self.raise_on_call:
            raise self.raise_on_call[index]
        if index < len(self.pages):
            page = self.pages[index]
            if isinstance(page, Exception):
                raise page
            if isinstance(page, FakeResponse):
                return page
            return FakeResponse(page)
        return FakeResponse({"results": [], "next": None})


def make_alpha(
    alpha_id: str,
    *,
    submitted: Optional[str] = "2026-08-20T12:00:00Z",
    expression: str = "rank(ts_mean(returns, 20))",
    status: str = "ACTIVE",
    sharpe: Optional[float] = 1.5,
    fitness: Optional[float] = 1.1,
    region: str = "USA",
    universe: str = "TOP3000",
    delay: int = 1,
    **extra: Any,
) -> Dict[str, Any]:
    """Dựng một bản ghi alpha giống hình dạng máy chủ trả về."""
    payload: Dict[str, Any] = {
        "id": alpha_id,
        "dateSubmitted": submitted,
        "status": status,
        "settings": {
            "region": region, "universe": universe, "delay": delay,
            "neutralization": "SUBINDUSTRY", "decay": 0, "truncation": 0.08,
        },
        "regular": {"code": expression},
        "is": {
            "sharpe": sharpe, "fitness": fitness, "turnover": 0.25,
            "returns": 0.18, "margin": 0.0012, "drawdown": 0.1,
            "longCount": 400, "shortCount": 380,
        },
    }
    payload.update(extra)
    return payload


def page(results: List[Dict[str, Any]], next_url: Optional[str] = None) -> Dict[str, Any]:
    return {"count": len(results), "results": results, "next": next_url}


def insert_historical(
    db: Database,
    expression: str,
    *,
    alpha_id: str = "",
    status: str = "ACTIVE",
    sharpe: Optional[float] = 1.5,
    fitness: Optional[float] = 1.1,
    turnover: Optional[float] = 0.3,
    submitted: str = "2026-06-01",
    region: str = "USA",
    universe: str = "TOP3000",
    delay: int = 1,
    neutralization: str = "SUBINDUSTRY",
) -> str:
    """Ghi một alpha lịch sử giả lập kèm vân tay đã tính sẵn.

    Trí nhớ nghiên cứu đọc các cột vân tay chứ không phân tích lại biểu thức,
    nên bản ghi thiếu vân tay sẽ im lặng không đóng góp vào độ phủ. Hàm này
    tồn tại để mọi tệp kiểm thử gieo dữ liệu theo đúng một cách.
    """
    from alphaforge.history.fingerprint import fingerprint

    meta = fingerprint(expression)
    alpha_id = alpha_id or f"HIST-{abs(hash(expression)) % 10 ** 8:08d}"
    connection = db.connect()
    try:
        connection.execute(
            """
            INSERT OR REPLACE INTO historical_alphas (
                alpha_id, submitted, status, region, universe, delay,
                neutralization, expression, sharpe, fitness, turnover, source,
                fingerprint, family, template, operators_json, fields_json,
                windows_json, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'brain_submitted',
                      ?, ?, ?, ?, ?, ?, '2026-06-01T00:00:00Z')
            """,
            (
                alpha_id, submitted, status, region, universe, delay,
                neutralization, expression, sharpe, fitness, turnover,
                meta["family"], meta["family"], meta["template"],
                json.dumps(meta["operators"]), json.dumps(meta["fields"]),
                json.dumps(meta["windows"]),
            ),
        )
    finally:
        connection.close()
    return alpha_id


@pytest.fixture()
def db(tmp_path) -> Database:
    return Database(tmp_path / "test.sqlite3")
