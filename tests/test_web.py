"""Kiểm thử bảng theo dõi.

Yêu cầu quan trọng nhất: bảng theo dõi chỉ đọc SQLite và không bao giờ gọi
BRAIN. Kiểm thử dưới đây dựng ứng dụng mà không cấu hình client nào, nên nếu
có lệnh gọi mạng thì sẽ lộ ra ngay.
"""

import json

import pytest
from fastapi.testclient import TestClient

from alphaforge.config import Settings
from alphaforge.research.models import AlphaLineage, Experiment, Hypothesis, ResearchProject
from alphaforge.research.store import ResearchStore
from alphaforge.storage.db import Database, Status
from alphaforge.web.app import create_app


@pytest.fixture()
def client(tmp_path):
    settings = Settings(raw={}, db_path=tmp_path / "web.sqlite3")
    return TestClient(create_app(settings)), Database(settings.db_path)


def _seed_history(db, alpha_id, family, status="ACTIVE", sharpe=1.5):
    connection = db.connect()
    try:
        connection.execute(
            """
            INSERT INTO historical_alphas (
                alpha_id, submitted, status, region, universe, delay, expression,
                sharpe, fitness, turnover, source, fingerprint, family, template,
                operators_json, fields_json, windows_json, raw_json, imported_at
            ) VALUES (?, '2026-08-15', ?, 'USA', 'TOP3000', 1, 'rank(ts_mean(close, 20))',
                      ?, 1.1, 0.3, 'brain_submitted', ?, ?, 'T1',
                      '["rank"]', '["close"]', '[20]', '{"lon":"nhieu du lieu"}', 'now')
            """,
            (alpha_id, status, sharpe, family, family),
        )
    finally:
        connection.close()


# ----------------------------------------------------------------------
def test_index_page_renders(client):
    app, _ = client
    response = app.get("/")
    assert response.status_code == 200
    assert "Alpha Forge" in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/api/stats", "/api/top", "/api/recent", "/api/events",
        "/api/history", "/api/history/report", "/api/research/families",
        "/api/research/gaps", "/api/research/projects", "/api/correlation",
        "/api/submissions",
    ],
)
def test_endpoints_work_on_empty_database(client, path):
    app, _ = client
    assert app.get(path).status_code == 200


def test_stats_reports_progress_and_pass_rate(client):
    app, db = client
    db.add_alphas(["rank(close)", "rank(open)"], {"region": "USA"})
    record = db.fetch_by_status(Status.PENDING)[0]
    db.update_alpha(record.id, status=Status.PASSED, score=1.5)

    payload = app.get("/api/stats").json()
    assert payload["total"] == 2
    assert payload["counts"][Status.PASSED] == 1
    assert payload["pass_rate"] == 100.0


def test_history_endpoint_excludes_bulky_raw_column(client):
    """raw_json chứa toàn bộ bản ghi gốc, không được trả về cho bảng theo dõi."""
    app, db = client
    _seed_history(db, "A1", "fam1")
    items = app.get("/api/history").json()["items"]
    assert items[0]["alpha_id"] == "A1"
    assert "raw_json" not in items[0]


def test_history_endpoint_filters_by_date(client):
    app, db = client
    _seed_history(db, "A1", "fam1")
    assert app.get("/api/history?start_date=2026-09-01").json()["items"] == []
    assert app.get("/api/history?start_date=2026-08-01").json()["items"]


def test_families_endpoint_splits_performance_groups(client):
    app, db = client
    for index in range(8):
        _seed_history(db, f"GOOD{index}", "good", status="ACTIVE", sharpe=2.5)
    for index in range(8):
        _seed_history(db, f"BAD{index}", "bad", status="FAILED", sharpe=0.1)

    payload = app.get("/api/research/families").json()
    high = {item["family"] for item in payload["high_performing"]}
    assert "good" in high
    assert payload["items"]


def test_gaps_endpoint_returns_ranked_items(client):
    app, db = client
    for index in range(3):
        _seed_history(db, f"A{index}", "fresh", sharpe=1.8)
    items = app.get("/api/research/gaps").json()["items"]
    assert items[0]["family"] == "fresh"
    assert "priority" in items[0]


def test_projects_endpoint_nests_hypotheses_and_experiments(client):
    app, db = client
    store = ResearchStore(db)
    project_id = store.create_project(ResearchProject(name="P1", family="Momentum"))
    hypothesis_id = store.create_hypothesis(
        Hypothesis(research_id=project_id, statement="S1")
    )
    store.create_experiment(Experiment(hypothesis_id=hypothesis_id, name="E1"))

    items = app.get("/api/research/projects").json()["items"]
    assert items[0]["name"] == "P1"
    assert items[0]["hypotheses"][0]["statement"] == "S1"
    assert items[0]["hypotheses"][0]["experiments"][0]["name"] == "E1"


def test_lineage_endpoint_returns_chain(client):
    app, db = client
    store = ResearchStore(db)
    store.save_lineage(AlphaLineage(alpha_id="CHILD", parent_alpha_id="PARENT"))
    store.save_lineage(AlphaLineage(alpha_id="PARENT"))

    payload = app.get("/api/research/lineage/CHILD").json()
    assert [item["alpha_id"] for item in payload["ancestry"]] == ["CHILD", "PARENT"]
    assert app.get("/api/research/lineage/PARENT").json()["children"][0]["alpha_id"] == "CHILD"


def test_correlation_endpoint_lists_checked_alphas_only(client):
    app, db = client
    db.add_alphas(["rank(close)", "rank(open)"], {"region": "USA"})
    records = db.fetch_by_status(Status.PENDING, limit=10)
    db.update_alpha(records[0].id, self_correlation=0.85, status=Status.REJECTED)

    items = app.get("/api/correlation").json()["items"]
    assert len(items) == 1
    assert items[0]["self_correlation"] == 0.85


def test_submissions_endpoint_skips_unsubmitted(client):
    app, db = client
    _seed_history(db, "SENT", "fam1", status="ACTIVE")
    _seed_history(db, "NOTSENT", "fam1", status="UNSUBMITTED")
    items = app.get("/api/submissions").json()["items"]
    assert {item["alpha_id"] for item in items} == {"SENT"}


def test_limits_are_bounded(client):
    """Tham số limit quá lớn phải bị từ chối, không được kéo cả kho ra."""
    app, _ = client
    assert app.get("/api/history?limit=99999").status_code == 422
    assert app.get("/api/top?limit=0").status_code == 422


# ======================================================================
# Phase 16: các trang nghiên cứu bổ sung
# ======================================================================
@pytest.mark.parametrize(
    "path",
    [
        "/api/research/coverage", "/api/research/gaps/detailed",
        "/api/research/priorities", "/api/experiments", "/api/alphas",
        "/api/candidates",
    ],
)
def test_research_endpoints_work_on_empty_database(client, path):
    app, _ = client
    assert app.get(path).status_code == 200


def test_coverage_endpoint_reports_dimensions(client):
    app, db = client
    _seed_history(db, "A1", "fam1")
    payload = app.get("/api/research/coverage").json()
    assert "field" in payload and "operator" in payload
    assert payload["field"]["close"] == 1


def test_alphas_endpoint_filters_by_status(client):
    app, db = client
    db.add_alphas(["rank(close)", "rank(open)"], {"region": "USA"})
    record = db.fetch_by_status(Status.PENDING)[0]
    db.update_alpha(record.id, status=Status.CANDIDATE, score=2.0)
    items = app.get(f"/api/alphas?status={Status.CANDIDATE}").json()["items"]
    assert len(items) == 1
    assert items[0]["score"] == 2.0


def test_experiment_report_endpoint_returns_404_for_unknown(client):
    app, _ = client
    assert app.get("/api/experiments/999/report").status_code == 404


def test_priorities_endpoint_includes_reasons(client):
    app, db = client
    for index in range(3):
        _seed_history(db, f"A{index}", "fam1")
    items = app.get("/api/research/priorities").json()["items"]
    assert items
    assert "reasons" in items[0]
    assert "components" in items[0]
