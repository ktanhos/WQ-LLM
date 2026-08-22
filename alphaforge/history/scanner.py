"""Historical alpha scanner backed by the BRAIN submitted-alpha endpoint."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .fingerprint import fingerprint
from .submitted import get_submitted_alphas


class HistoricalAlphaScanner:
    """Scan BRAIN submission history and persist structural metadata locally."""

    def __init__(self, client, db_path: str | Path):
        self.client = client
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def scan(self, start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
        rows = get_submitted_alphas(
            self.client.session,
            self.client.base_url,
            start_date=start_date,
            end_date=end_date,
        )
        connection = sqlite3.connect(self.db_path)
        try:
            self._ensure_schema(connection)
            inserted = 0
            for row in rows:
                meta = fingerprint(row.get("expression") or "")
                connection.execute(
                    """
                    INSERT INTO historical_alphas (
                        alpha_id, submitted, status, region, universe, delay,
                        expression, sharpe, fitness, returns, turnover, margin,
                        source, fingerprint, normalized_expression,
                        operators_json, fields_json, imported_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(alpha_id) DO UPDATE SET
                        submitted=excluded.submitted,
                        status=excluded.status,
                        region=excluded.region,
                        universe=excluded.universe,
                        delay=excluded.delay,
                        expression=excluded.expression,
                        sharpe=excluded.sharpe,
                        fitness=excluded.fitness,
                        returns=excluded.returns,
                        turnover=excluded.turnover,
                        margin=excluded.margin,
                        fingerprint=excluded.fingerprint,
                        normalized_expression=excluded.normalized_expression,
                        operators_json=excluded.operators_json,
                        fields_json=excluded.fields_json,
                        imported_at=excluded.imported_at
                    """,
                    (
                        row.get("alpha_id"), row.get("submitted"), row.get("status"),
                        row.get("region"), row.get("universe"), row.get("delay"),
                        row.get("expression"), row.get("sharpe"), row.get("fitness"),
                        row.get("returns"), row.get("turnover"), row.get("margin"),
                        row.get("source", "brain_submitted"), meta["hash"],
                        meta["normalized"], json.dumps(meta["operators"]),
                        json.dumps(meta["fields"]), _now(),
                    ),
                )
                inserted += 1
            connection.commit()
            return {"scanned": len(rows), "stored": inserted, "db_path": str(self.db_path)}
        finally:
            connection.close()

    @staticmethod
    def _ensure_schema(connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS historical_alphas (
                alpha_id TEXT PRIMARY KEY,
                submitted TEXT NOT NULL,
                status TEXT,
                region TEXT,
                universe TEXT,
                delay INTEGER,
                expression TEXT,
                sharpe REAL,
                fitness REAL,
                returns REAL,
                turnover REAL,
                margin REAL,
                source TEXT NOT NULL,
                fingerprint TEXT,
                normalized_expression TEXT,
                operators_json TEXT,
                fields_json TEXT,
                imported_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_historical_submitted ON historical_alphas(submitted)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_historical_fingerprint ON historical_alphas(fingerprint)"
        )


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
