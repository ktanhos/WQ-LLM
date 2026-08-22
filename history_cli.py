"""End-to-end CLI for WorldQuant BRAIN historical alpha intelligence."""
from __future__ import annotations

import argparse
import json
import sqlite3

from client import BrainClient
from config import load_settings
from alphaforge.history.scanner import HistoricalAlphaScanner
from alphaforge.history.analyzer import summarize


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Scan and analyze submitted alpha history.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    args = parser.parse_args(argv)

    settings = load_settings(args.config)
    client = BrainClient(settings)
    client.authenticate()
    scanner = HistoricalAlphaScanner(client, settings.db_path)
    result = scanner.scan(args.start_date, args.end_date)

    with sqlite3.connect(settings.db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = [dict(row) for row in connection.execute(
            """SELECT * FROM historical_alphas
               WHERE (? IS NULL OR submitted >= ?)
                 AND (? IS NULL OR submitted <= ?)
               ORDER BY submitted DESC""",
            (args.start_date, args.start_date, args.end_date, args.end_date),
        ).fetchall()]

    result["summary"] = summarize(rows)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
