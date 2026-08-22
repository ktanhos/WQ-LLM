"""Analysis helpers for imported submitted alpha history."""

from __future__ import annotations

from collections import Counter
from typing import Iterable


def summarize(rows: Iterable[dict]) -> dict:
    rows = list(rows)
    numeric = lambda key: [float(r[key]) for r in rows if r.get(key) is not None]
    return {
        "total": len(rows),
        "status_counts": dict(Counter(str(r.get("status")) for r in rows)),
        "region_counts": dict(Counter(str(r.get("region")) for r in rows)),
        "universe_counts": dict(Counter(str(r.get("universe")) for r in rows)),
        "average_sharpe": _mean(numeric("sharpe")),
        "average_fitness": _mean(numeric("fitness")),
        "average_turnover": _mean(numeric("turnover")),
        "best_sharpe": max(numeric("sharpe"), default=None),
        "best_fitness": max(numeric("fitness"), default=None),
    }


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None
