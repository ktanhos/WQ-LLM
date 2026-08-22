"""Bảng theo dõi chạy trên FastAPI.

Giao diện chỉ đọc kho SQLite, không giữ trạng thái riêng và không gọi máy chủ
BRAIN. Nhờ vậy có thể mở nhiều tab, tắt đi bật lại tùy ý mà không ảnh hưởng
tiến trình mô phỏng đang chạy ở cửa sổ dòng lệnh khác.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse

from ..config import Settings, load_settings
from ..storage.db import Database, Status

TEMPLATE_PATH = Path(__file__).parent / "templates" / "index.html"


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or load_settings()
    db = Database(settings.db_path)
    app = FastAPI(title="Alpha Forge", version="0.1.0")

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(TEMPLATE_PATH.read_text(encoding="utf-8"))

    @app.get("/api/stats")
    def stats() -> JSONResponse:
        counts = db.counts_by_status()
        total = sum(counts.values())
        finished = (
            counts.get(Status.PASSED, 0)
            + counts.get(Status.REJECTED, 0)
            + counts.get(Status.FAILED, 0)
            + counts.get(Status.SUBMITTED, 0)
        )
        scored = counts.get(Status.PASSED, 0) + counts.get(Status.REJECTED, 0)
        payload: Dict[str, Any] = {
            "counts": counts,
            "total": total,
            "finished": finished,
            "progress": round(finished / total * 100, 2) if total else 0.0,
            "pass_rate": round(counts.get(Status.PASSED, 0) / scored * 100, 2)
            if scored
            else 0.0,
        }
        return JSONResponse(payload)

    @app.get("/api/top")
    def top(limit: int = Query(25, ge=1, le=200)) -> JSONResponse:
        return JSONResponse({"items": db.top_alphas(limit)})

    @app.get("/api/recent")
    def recent(limit: int = Query(25, ge=1, le=200)) -> JSONResponse:
        return JSONResponse({"items": db.recent_activity(limit)})

    @app.get("/api/events")
    def events(limit: int = Query(30, ge=1, le=200)) -> JSONResponse:
        return JSONResponse({"items": db.recent_events(limit)})

    return app


app = None  # tạo bằng create_app trong cli.py để tránh nạp cấu hình khi nhập mô đun
