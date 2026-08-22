"""Research-oriented reports over historical submitted alphas."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path


def build_report(db_path: str | Path, start_date: str | None = None, end_date: str | None = None) -> dict:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = [dict(row) for row in connection.execute(
            """SELECT * FROM historical_alphas
               WHERE (? IS NULL OR submitted >= ?)
                 AND (? IS NULL OR submitted <= ?)
               ORDER BY submitted DESC""",
            (start_date, start_date, end_date, end_date),
        ).fetchall()]

    def numeric(key):
        return [float(r[key]) for r in rows if r.get(key) is not None]

    operator_counts = Counter()
    field_counts = Counter()
    fingerprint_counts = Counter()
    for row in rows:
        try:
            operator_counts.update(json.loads(row.get("operators_json") or "[]"))
            field_counts.update(json.loads(row.get("fields_json") or "[]"))
        except json.JSONDecodeError:
            pass
        if row.get("fingerprint"):
            fingerprint_counts[row["fingerprint"]] += 1

    return {
        "window": {"start": start_date, "end": end_date},
        "total": len(rows),
        "status_counts": dict(Counter(str(r.get("status")) for r in rows)),
        "region_counts": dict(Counter(str(r.get("region")) for r in rows)),
        "universe_counts": dict(Counter(str(r.get("universe")) for r in rows)),
        "average_sharpe": _mean(numeric("sharpe")),
        "average_fitness": _mean(numeric("fitness")),
        "average_turnover": _mean(numeric("turnover")),
        "best_sharpe": max(numeric("sharpe"), default=None),
        "best_fitness": max(numeric("fitness"), default=None),
        "top_operators": operator_counts.most_common(20),
        "top_fields": field_counts.most_common(20),
        "repeated_structures": [
            {"fingerprint": key, "count": count}
            for key, count in fingerprint_counts.most_common(20)
            if count > 1
        ],
    }


def _mean(values):
    return sum(values) / len(values) if values else None
