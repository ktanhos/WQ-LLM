"""Báo cáo nghiên cứu trên lịch sử alpha đã nhập.

Mô đun chỉ đọc kho SQLite, không gọi mạng, nên chạy lại bao nhiêu lần cũng
không tốn hạn mức máy chủ.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..storage.db import Database
from .analyzer import _json_list, analyze, research_gaps, summarize


def load_history(
    db: Database | str | Path,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Đọc bảng historical_alphas trong khoảng ngày.

    Bản ghi thiếu ngày nộp được giữ lại khi không giới hạn khoảng, và bị loại
    khi có khoảng, vì không thể khẳng định nó thuộc khoảng nào.
    """
    database = db if isinstance(db, Database) else Database(db)
    query = "SELECT * FROM historical_alphas WHERE 1 = 1"
    params: List[Any] = []
    if start_date:
        query += " AND submitted IS NOT NULL AND submitted >= ?"
        params.append(str(start_date))
    if end_date:
        query += " AND submitted IS NOT NULL AND submitted <= ?"
        params.append(str(end_date))
    query += " ORDER BY submitted DESC, alpha_id DESC"

    connection = database.connect()
    try:
        return [dict(row) for row in connection.execute(query, params).fetchall()]
    finally:
        connection.close()


def build_report(
    db: Database | str | Path,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Báo cáo tổng hợp: thống kê, phân phối, cấu trúc lặp và khoảng trống."""
    rows = load_history(db, start_date, end_date)

    operator_counts: Counter = Counter()
    field_counts: Counter = Counter()
    family_counts: Counter = Counter()
    for row in rows:
        operator_counts.update(_json_list(row.get("operators_json")))
        field_counts.update(_json_list(row.get("fields_json")))
        family = row.get("family") or row.get("fingerprint")
        if family:
            family_counts[family] += 1

    analysis = analyze(rows)

    # Bắt đầu từ các khóa tóm tắt để mã cũ và bảng theo dõi không phải sửa,
    # sau đó bổ sung phần phân tích sâu.
    report: Dict[str, Any] = dict(summarize(rows))
    report.update(
        {
            "window": {"start": start_date, "end": end_date},
            "total": len(rows),
            "top_operators": operator_counts.most_common(20),
            "top_fields": field_counts.most_common(20),
            "repeated_structures": [
                {"fingerprint": key, "count": count}
                for key, count in family_counts.most_common(20)
                if count > 1
            ],
            "analysis": analysis,
            "research_gaps": research_gaps(analysis),
        }
    )
    return report


