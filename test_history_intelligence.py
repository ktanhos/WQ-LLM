import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from alphaforge.history.fingerprint import fingerprint
from alphaforge.history.analyzer import summarize
from history_report import build_report


class HistoryIntelligenceTests(unittest.TestCase):
    def test_fingerprint_normalizes_numeric_windows(self):
        a = fingerprint("rank(ts_mean(returns, 20))")
        b = fingerprint("rank(ts_mean(returns, 60))")
        self.assertEqual(a["hash"], b["hash"])
        self.assertIn("rank", a["operators"])
        self.assertIn("returns", a["fields"])

    def test_summary(self):
        rows = [
            {"status": "PASS", "region": "USA", "universe": "TOP3000", "sharpe": 1.0, "fitness": 0.5, "turnover": 0.2},
            {"status": "FAIL", "region": "USA", "universe": "TOP3000", "sharpe": 2.0, "fitness": 1.0, "turnover": 0.4},
        ]
        result = summarize(rows)
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["best_sharpe"], 2.0)
        self.assertEqual(result["status_counts"]["PASS"], 1)

    def test_report_reads_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.sqlite3"
            with sqlite3.connect(path) as db:
                db.execute("""CREATE TABLE historical_alphas (
                    alpha_id TEXT PRIMARY KEY, submitted TEXT, status TEXT, region TEXT,
                    universe TEXT, delay INTEGER, expression TEXT, sharpe REAL, fitness REAL,
                    returns REAL, turnover REAL, margin REAL, source TEXT, fingerprint TEXT,
                    normalized_expression TEXT, operators_json TEXT, fields_json TEXT,
                    imported_at TEXT)""")
                db.execute("""INSERT INTO historical_alphas VALUES
                    ('A1','2026-08-20','PASS','USA','TOP3000',1,'rank(returns)',1.5,0.8,0.1,0.2,0.01,'brain_submitted','fp','rank(returns)','[\"rank\"]','[\"returns\"]','now')""")
                db.commit()
            report = build_report(path)
            self.assertEqual(report["total"], 1)
            self.assertEqual(report["top_operators"][0], ("rank", 1))


if __name__ == "__main__":
    unittest.main()
