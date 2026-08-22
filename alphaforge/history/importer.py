"""Persistence helpers for imported historical alpha records."""

from datetime import datetime, timezone


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS submitted_alphas (
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
    source TEXT NOT NULL DEFAULT 'brain_submitted',
    imported_at TEXT NOT NULL
)
"""


def ensure_table(connection) -> None:
    connection.execute(CREATE_TABLE_SQL)
    connection.commit()


def upsert_submitted_alphas(connection, rows: list[dict]) -> int:
    """Insert or update imported BRAIN submission records."""
    ensure_table(connection)
    imported_at = datetime.now(timezone.utc).isoformat()
    sql = """
    INSERT INTO submitted_alphas (
        alpha_id, submitted, status, region, universe, delay,
        expression, sharpe, fitness, returns, turnover, margin,
        source, imported_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        source=excluded.source,
        imported_at=excluded.imported_at
    """
    for row in rows:
        connection.execute(
            sql,
            (
                row.get("alpha_id"), row.get("submitted"), row.get("status"),
                row.get("region"), row.get("universe"), row.get("delay"),
                row.get("expression"), row.get("sharpe"), row.get("fitness"),
                row.get("returns"), row.get("turnover"), row.get("margin"),
                row.get("source", "brain_submitted"), imported_at,
            ),
        )
    connection.commit()
    return len(rows)
