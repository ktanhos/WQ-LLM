"""Kiểm thử kho SQLite hợp nhất, bao gồm di trú từ schema phiên bản trước."""

import sqlite3
import threading

import pytest

from alphaforge.storage.db import (
    Database,
    Status,
    expression_hash,
    _declared_columns,
)

SETTINGS = {"region": "USA", "universe": "TOP3000", "delay": 1}


# ----------------------------------------------------------------------
# Khử trùng lặp
# ----------------------------------------------------------------------
def test_expression_hash_ignores_whitespace():
    assert expression_hash("rank( close )", SETTINGS) == expression_hash(
        "rank(close)", SETTINGS
    )


def test_expression_hash_separates_different_settings():
    other = dict(SETTINGS, region="EUR")
    assert expression_hash("rank(close)", SETTINGS) != expression_hash("rank(close)", other)


def test_add_alphas_skips_blank_expressions(db):
    assert db.add_alphas(["rank(close)", "", "   "], SETTINGS) == 1


# ----------------------------------------------------------------------
# Hàng đợi
# ----------------------------------------------------------------------
def test_counts_by_status_reports_zero_for_absent_statuses(db):
    counts = db.counts_by_status()
    assert counts[Status.PENDING] == 0
    assert set(counts) == set(Status.ALL)


def test_claim_increments_attempts(db):
    db.add_alphas(["rank(close)"], SETTINGS)
    assert db.claim_pending(1)[0].attempts == 1
    db.reset_stuck()
    assert db.claim_pending(1)[0].attempts == 2


def test_concurrent_claims_never_hand_out_the_same_record(db):
    db.add_alphas([f"rank(f{i})" for i in range(40)], SETTINGS)
    seen = []
    lock = threading.Lock()

    def worker():
        local = []
        for _ in range(10):
            local.extend(record.id for record in db.claim_pending(2))
        with lock:
            seen.extend(local)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(seen) == len(set(seen))
    assert db.counts_by_status()[Status.PENDING] == 0


def test_update_alpha_ignores_unknown_columns(db):
    db.add_alphas(["rank(close)"], SETTINGS)
    record = db.fetch_by_status(Status.PENDING)[0]
    # Không được ném lỗi, và cũng không được tạo cột mới.
    db.update_alpha(record.id, khong_ton_tai="x", score=1.5)
    assert db.get_alpha(record.id).score == 1.5


def test_metrics_and_settings_round_trip_as_json(db):
    db.add_alphas(["rank(close)"], SETTINGS)
    record = db.fetch_by_status(Status.PENDING)[0]
    db.update_alpha(record.id, metrics={"sharpe": 1.5, "checkFailures": ["A"]})
    assert db.get_alpha(record.id).metrics["checkFailures"] == ["A"]
    assert db.get_alpha(record.id).settings == SETTINGS


def test_generation_metadata_is_persisted(db):
    db.add_alphas(
        ["rank(close)"], SETTINGS,
        generation_strategy="mutate",
        parent_alpha_id="PARENT1",
        fingerprints={"rank(close)": {"fingerprint": "fp1", "family": "fam1"}},
    )
    record = db.fetch_by_status(Status.PENDING)[0]
    assert record.generation_strategy == "mutate"
    assert record.parent_alpha_id == "PARENT1"
    assert record.family == "fam1"


# ----------------------------------------------------------------------
# Trường dữ liệu
# ----------------------------------------------------------------------
def test_data_fields_upsert_and_filter(db):
    db.save_data_fields(
        [
            {"id": "close", "type": "MATRIX", "coverage": 0.99,
             "dataset": {"id": "pv1"}, "description": "gia dong cua"},
            {"id": "grp", "type": "GROUP", "coverage": 0.5},
        ],
        region="USA", universe="TOP3000", delay=1,
    )
    matrix = db.load_data_fields(
        region="USA", universe="TOP3000", delay=1, field_type="MATRIX", limit=10
    )
    assert [row["id"] for row in matrix] == ["close"]
    assert matrix[0]["dataset_id"] == "pv1"


def test_data_fields_upsert_is_idempotent(db):
    payload = [{"id": "close", "type": "MATRIX", "coverage": 0.9}]
    db.save_data_fields(payload, region="USA", universe="TOP3000", delay=1)
    db.save_data_fields(payload, region="USA", universe="TOP3000", delay=1)
    assert len(db.load_data_fields(region="USA", universe="TOP3000", delay=1)) == 1


# ----------------------------------------------------------------------
# Nhật ký
# ----------------------------------------------------------------------
def test_events_are_recorded_newest_first(db):
    db.log_event("INFO", "mot")
    db.log_event("ERROR", "hai")
    events = db.recent_events(10)
    assert events[0]["message"] == "hai"
    assert events[0]["level"] == "ERROR"


# ----------------------------------------------------------------------
# Di trú schema
# ----------------------------------------------------------------------
def test_schema_columns_are_parsed_from_schema_text():
    columns = _declared_columns(
        "CREATE TABLE IF NOT EXISTS t (\n a TEXT NOT NULL,\n b INTEGER,\n"
        " PRIMARY KEY (a)\n)"
    )
    assert [name for name, _ in columns["t"]] == ["a", "b"]


def test_legacy_database_is_migrated_without_losing_rows(tmp_path):
    """Kho tạo bởi phiên bản trước thiếu cột vân tay. Mở lại không được hỏng."""
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE historical_alphas (
            alpha_id TEXT PRIMARY KEY, submitted TEXT NOT NULL, status TEXT,
            region TEXT, universe TEXT, delay INTEGER, expression TEXT,
            sharpe REAL, fitness REAL, returns REAL, turnover REAL, margin REAL,
            source TEXT NOT NULL, fingerprint TEXT, normalized_expression TEXT,
            operators_json TEXT, fields_json TEXT, imported_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "INSERT INTO historical_alphas (alpha_id, submitted, source, imported_at, sharpe)"
        " VALUES ('OLD1', '2026-08-01', 'brain_submitted', 'truoc', 1.25)"
    )
    connection.commit()
    connection.close()

    database = Database(path)
    connection = database.connect()
    try:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(historical_alphas)")
        }
        row = connection.execute(
            "SELECT alpha_id, sharpe FROM historical_alphas"
        ).fetchone()
    finally:
        connection.close()

    assert {"family", "template", "windows_json", "raw_json"} <= columns
    assert row["alpha_id"] == "OLD1"
    assert row["sharpe"] == 1.25


def test_initialize_is_idempotent(tmp_path):
    path = tmp_path / "repeat.sqlite3"
    Database(path)
    Database(path)
    database = Database(path)
    assert database.counts_by_status()[Status.PENDING] == 0
