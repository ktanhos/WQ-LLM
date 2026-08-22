"""Command line entry point for historical submitted-alpha scanning."""

from __future__ import annotations

import argparse
import json

from ...client import BrainClient
from ...config import load_settings
from .analyzer import summarize
from .scanner import HistoricalAlphaScanner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Quét lịch sử alpha đã submit trên BRAIN.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    args = parser.parse_args(argv)

    settings = load_settings(args.config)
    client = BrainClient(settings)
    client.authenticate()
    scanner = HistoricalAlphaScanner(client, settings.db_path)
    result = scanner.scan(args.start_date, args.end_date)

    import sqlite3
    connection = sqlite3.connect(settings.db_path)
    try:
        rows = [
            dict(zip(
                ["alpha_id", "submitted", "status", "region", "universe", "delay",
                 "expression", "sharpe", "fitness", "returns", "turnover", "margin"],
                row,
            ))
            for row in connection.execute(
                """SELECT alpha_id, submitted, status, region, universe, delay,
                          expression, sharpe, fitness, returns, turnover, margin
                   FROM historical_alphas
                   WHERE (? IS NULL OR submitted >= ?)
                     AND (? IS NULL OR submitted <= ?)
                   ORDER BY submitted DESC""",
                (args.start_date, args.start_date, args.end_date, args.end_date),
            )
        ]
    finally:
        connection.close()

    result["summary"] = summarize(rows)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
