"""Small read-only web dashboard for historical alpha intelligence."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse

from config import load_settings
from history_report import build_report


HTML = """
<!doctype html><html lang='vi'><head><meta charset='utf-8'>
<title>Alpha History Intelligence</title>
<style>body{font-family:system-ui;margin:32px;max-width:1200px}button{padding:8px 14px}pre{background:#f5f5f5;padding:16px;overflow:auto}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:7px;text-align:left}</style>
</head><body><h1>Alpha History Intelligence</h1>
<p>Đọc dữ liệu lịch sử alpha đã submit từ kho SQLite. Dashboard không gọi BRAIN.</p>
<button onclick='loadReport()'>Làm mới</button><div id='summary'></div><h2>Cấu trúc lặp lại</h2><pre id='structures'></pre>
<h2>Toán tử phổ biến</h2><pre id='operators'></pre><h2>Trường dữ liệu phổ biến</h2><pre id='fields'></pre>
<script>
async function loadReport(){const r=await fetch('/api/report');const d=await r.json();document.getElementById('summary').innerHTML=`<h2>Tổng số ${d.total}</h2><p>Sharpe trung bình: ${d.average_sharpe ?? 'N/A'} | Fitness trung bình: ${d.average_fitness ?? 'N/A'} | Turnover trung bình: ${d.average_turnover ?? 'N/A'}</p>`;document.getElementById('structures').textContent=JSON.stringify(d.repeated_structures,null,2);document.getElementById('operators').textContent=JSON.stringify(d.top_operators,null,2);document.getElementById('fields').textContent=JSON.stringify(d.top_fields,null,2)}loadReport();
</script></body></html>
"""


def create_app(db_path: str | Path | None = None) -> FastAPI:
    settings = load_settings()
    database = Path(db_path or settings.db_path)
    app = FastAPI(title="Alpha History Intelligence", version="1.0.0")

    @app.get("/", response_class=HTMLResponse)
    def index():
        return HTMLResponse(HTML)

    @app.get("/api/report")
    def report(start_date: str | None = Query(None), end_date: str | None = Query(None)):
        return JSONResponse(build_report(database, start_date, end_date))

    @app.get("/api/alphas")
    def alphas(limit: int = Query(100, ge=1, le=1000)):
        with sqlite3.connect(database) as connection:
            connection.row_factory = sqlite3.Row
            rows = [dict(r) for r in connection.execute(
                "SELECT alpha_id, submitted, status, region, universe, delay, expression, sharpe, fitness, turnover, fingerprint FROM historical_alphas ORDER BY submitted DESC LIMIT ?",
                (limit,),
            ).fetchall()]
        return JSONResponse({"items": rows})

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
