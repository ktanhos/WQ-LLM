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
from ..history.analyzer import analyze, research_gaps
from ..history.report import build_report, load_history
from ..research.store import ResearchStore
from ..storage.db import Database, Status

TEMPLATE_PATH = Path(__file__).parent / "templates" / "index.html"


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or load_settings()
    db = Database(settings.db_path)
    research = ResearchStore(db)
    app = FastAPI(title="Alpha Forge", version="0.2.0")

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

    # ------------------------------------------------------------------
    # Trí nhớ nghiên cứu. Mọi điểm cuối dưới đây chỉ đọc SQLite, không gọi BRAIN.
    # ------------------------------------------------------------------
    @app.get("/api/history")
    def history(
        limit: int = Query(100, ge=1, le=1000),
        start_date: Optional[str] = Query(None),
        end_date: Optional[str] = Query(None),
    ) -> JSONResponse:
        rows = load_history(db, start_date, end_date)
        # Bỏ cột raw_json khỏi phản hồi: nó chứa toàn bộ bản ghi gốc và làm
        # phình phản hồi lên nhiều lần mà bảng theo dõi không dùng tới.
        items = [
            {key: value for key, value in row.items() if key != "raw_json"}
            for row in rows[:limit]
        ]
        return JSONResponse({"items": items, "total": len(rows)})

    @app.get("/api/history/report")
    def history_report(
        start_date: Optional[str] = Query(None),
        end_date: Optional[str] = Query(None),
    ) -> JSONResponse:
        return JSONResponse(build_report(db, start_date, end_date))

    @app.get("/api/research/families")
    def families(limit: int = Query(50, ge=1, le=500)) -> JSONResponse:
        analysis = analyze(load_history(db))
        distribution = analysis.get("distribution", {}).get("family", [])
        return JSONResponse(
            {
                "items": distribution[:limit],
                "high_performing": analysis.get("high_performing_structures", []),
                "low_performing": analysis.get("low_performing_structures", []),
                "frequently_tested": analysis.get("frequently_tested_structures", []),
                "underexplored": analysis.get("underexplored_structures", []),
            }
        )

    @app.get("/api/research/gaps")
    def gaps(limit: int = Query(20, ge=1, le=200)) -> JSONResponse:
        return JSONResponse(
            {"items": research_gaps(analyze(load_history(db)), limit=limit)}
        )

    @app.get("/api/research/projects")
    def projects() -> JSONResponse:
        items = []
        for project in research.list_projects():
            hypotheses = research.list_hypotheses(int(project["id"]))
            for hypothesis in hypotheses:
                hypothesis["experiments"] = research.list_experiments(int(hypothesis["id"]))
            project["hypotheses"] = hypotheses
            items.append(project)
        return JSONResponse({"items": items})

    @app.get("/api/research/lineage/{alpha_id}")
    def lineage(alpha_id: str) -> JSONResponse:
        return JSONResponse(
            {
                "alpha_id": alpha_id,
                "ancestry": research.ancestry(alpha_id),
                "children": research.get_children(alpha_id),
            }
        )

    @app.get("/api/correlation")
    def correlation(limit: int = Query(50, ge=1, le=500)) -> JSONResponse:
        """Alpha đã có kết quả kiểm tra tương quan."""
        connection = db.connect()
        try:
            rows = connection.execute(
                """
                SELECT id, alpha_id, expression, status, score,
                       self_correlation, prod_correlation, reject_reason
                  FROM alphas
                 WHERE self_correlation IS NOT NULL OR prod_correlation IS NOT NULL
                 ORDER BY ABS(COALESCE(self_correlation, 0)) DESC
                 LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        finally:
            connection.close()
        return JSONResponse({"items": [dict(row) for row in rows]})

    @app.get("/api/submissions")
    def submissions(limit: int = Query(50, ge=1, le=500)) -> JSONResponse:
        """Lịch sử nộp, lấy từ alpha lịch sử đã nhập về."""
        rows = [
            row for row in load_history(db)
            if str(row.get("status") or "").upper() not in ("", "UNSUBMITTED")
        ]
        items = [
            {
                key: value for key, value in row.items()
                if key in (
                    "alpha_id", "submitted", "status", "region", "universe",
                    "delay", "expression", "sharpe", "fitness", "turnover",
                )
            }
            for row in rows[:limit]
        ]
        return JSONResponse({"items": items, "total": len(rows)})

    return app


app = None  # tạo bằng create_app trong cli.py để tránh nạp cấu hình khi nhập mô đun
