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
from ..research.advisor import RuleBasedResearchAdvisor
from ..research.gap import ResearchGap
from ..research.memory import ResearchMemory
from ..research.priority import ResearchPriority
from ..research.report import ExperimentReport
from ..research.store import ResearchStore
from ..storage.db import Database, Status

TEMPLATE_PATH = Path(__file__).parent / "templates" / "index.html"


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or load_settings()
    db = Database(settings.db_path)
    research = ResearchStore(db)
    memory = ResearchMemory(db)
    app = FastAPI(title="Alpha Research Hub", version="0.3.0")

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
        """Phả hệ của một alpha, tra được bằng mã nền tảng lẫn mã cục bộ.

        Một alpha có mã cục bộ ngay khi sinh ra và chỉ có mã nền tảng sau khi
        mô phỏng xong, nên màn hình phả hệ phải nhận cả hai; nếu không thì
        alpha chưa chạy sẽ không tra cứu được.
        """
        connection = db.connect()
        try:
            row = connection.execute(
                "SELECT id, local_id, alpha_id, expression, status, evaluation_status,"
                " score, experiment_id, variant_id, family FROM alphas"
                " WHERE alpha_id = ? OR local_id = ? LIMIT 1",
                (alpha_id, alpha_id),
            ).fetchone()
        finally:
            connection.close()

        record = dict(row) if row is not None else None
        # Phả hệ được ghi theo mã cục bộ lúc sinh, rồi bổ sung mã nền tảng sau
        # khi mô phỏng. Tra theo cả hai để không phụ thuộc vào việc người dùng
        # cầm mã nào.
        keys = [alpha_id]
        if record:
            keys = [key for key in (record.get("alpha_id"), record.get("local_id"))
                    if key] or keys

        ancestry: list = []
        children: list = []
        seen = set()
        for key in keys:
            for item in research.ancestry(key):
                marker = item.get("alpha_id")
                if marker not in seen:
                    seen.add(marker)
                    ancestry.append(item)
            children.extend(research.get_children(key))

        return JSONResponse(
            {
                "alpha_id": alpha_id,
                "alpha": record,
                "ancestry": ancestry,
                "children": children,
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

    @app.get("/api/research/coverage")
    def coverage() -> JSONResponse:
        """Độ phủ nghiên cứu trên chín chiều."""
        return JSONResponse(
            {
                dimension: dict(counter.most_common(50))
                for dimension, counter in memory.coverage().items()
            }
        )

    @app.get("/api/research/overview")
    def research_overview() -> JSONResponse:
        """Một màn hình trả lời: hệ thống đã nghiên cứu những gì và tới đâu.

        Gộp sẵn ở máy chủ thay vì để trình duyệt gọi bốn địa chỉ rồi tự ghép,
        vì mọi con số ở đây phải đến từ cùng một lần đọc kho.
        """
        return JSONResponse(
            {
                "statistics": memory.statistics(),
                "top_values": {
                    dimension: dict(counter.most_common(10))
                    for dimension, counter in memory.coverage().items()
                },
                "candidates": len(memory.candidates(limit=500)),
            }
        )

    @app.get("/api/research/next")
    def research_next(limit: int = Query(5, ge=1, le=50)) -> JSONResponse:
        """Đề xuất hướng nghiên cứu tiếp theo, kèm lý do và thí nghiệm gợi ý."""
        advisor = RuleBasedResearchAdvisor(memory)
        return JSONResponse(
            {"items": [item.as_dict() for item in advisor.suggest(limit)]}
        )

    @app.get("/api/research/gaps/detailed")
    def detailed_gaps(
        limit: int = Query(30, ge=1, le=200),
        per_kind: int = Query(5, ge=1, le=50),
    ) -> JSONResponse:
        """Thiếu hụt theo từng loại, kèm lý do và độ ưu tiên.

        `per_kind` tách khỏi `limit` là có chủ đích. Một chiều nhiều giá trị,
        chẳng hạn cửa sổ nhìn lại, sinh ra hàng chục thiếu hụt gần giống nhau
        và sẽ đẩy mọi loại khác ra khỏi màn hình nếu dùng chung một giới hạn.
        """
        gaps = ResearchGap(memory).find(limit_per_kind=per_kind)
        return JSONResponse({"items": [gap.as_dict() for gap in gaps[:limit]]})

    @app.get("/api/research/priorities")
    def priorities(
        dimension: str = Query("family"),
        limit: int = Query(20, ge=1, le=200),
    ) -> JSONResponse:
        """Xếp hạng vùng nghiên cứu, mỗi điểm kèm lý do."""
        engine = ResearchPriority()
        profiles = memory.profiles()
        scores = [
            engine.score(
                key, dimension=dimension, sample_size=count,
                median_sharpe=profiles[key].median_sharpe if key in profiles else None,
                pass_rate=profiles[key].pass_rate if key in profiles else 0.0,
            )
            for key, count in memory.coverage()[dimension].items()
        ]
        return JSONResponse(
            {"items": [score.as_dict() for score in engine.rank(scores, limit=limit)]}
        )

    @app.get("/api/experiments")
    def experiments() -> JSONResponse:
        connection = db.connect()
        try:
            rows = [dict(row) for row in connection.execute(
                "SELECT id, hypothesis_id, name, variable_changed, status,"
                " base_expression, created_at FROM experiments ORDER BY id DESC"
            ).fetchall()]
        finally:
            connection.close()
        for row in rows:
            row["outcome"] = memory.experiment_outcome(int(row["id"]))
        return JSONResponse({"items": rows})

    @app.get("/api/experiments/{experiment_id}")
    def experiment_detail(experiment_id: int) -> JSONResponse:
        """Chi tiết một thí nghiệm, kể cả khi chưa có alpha nào chạy xong."""
        detail = research.experiment_detail(experiment_id)
        if detail is None:
            return JSONResponse(
                {"error": f"Không có thí nghiệm {experiment_id}."}, status_code=404
            )
        return JSONResponse(detail)

    @app.get("/api/experiments/{experiment_id}/report")
    def experiment_report(experiment_id: int) -> JSONResponse:
        try:
            return JSONResponse(ExperimentReport(db).build(experiment_id))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.get("/api/alphas")
    def alphas(
        status: Optional[str] = Query(None),
        experiment_id: Optional[int] = Query(None),
        limit: int = Query(100, ge=1, le=1000),
    ) -> JSONResponse:
        """Tra cứu alpha, lọc theo trạng thái hoặc thí nghiệm."""
        query = (
            "SELECT id, local_id, alpha_id, expression, status, evaluation_status,"
            " score, metrics_json, family, experiment_id, variant_id, self_correlation,"
            " reject_reason, validation_error FROM alphas WHERE 1 = 1"
        )
        params: list = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if experiment_id is not None:
            query += " AND experiment_id = ?"
            params.append(int(experiment_id))
        query += " ORDER BY COALESCE(score, -999) DESC, id DESC LIMIT ?"
        params.append(int(limit))

        connection = db.connect()
        try:
            rows = [dict(row) for row in connection.execute(query, params).fetchall()]
        finally:
            connection.close()
        import json as _json
        for row in rows:
            try:
                row["metrics"] = _json.loads(row.pop("metrics_json") or "{}")
            except (TypeError, ValueError):
                row["metrics"] = {}
        return JSONResponse({"items": rows})

    @app.get("/api/candidates")
    def candidates(limit: int = Query(50, ge=1, le=500)) -> JSONResponse:
        """Alpha đã qua mọi bước tự động, đang chờ người quyết định."""
        return JSONResponse({"items": memory.candidates(limit=limit)})

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
