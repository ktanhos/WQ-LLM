"""Kiểm thử phả hệ alpha và khả năng tái lập thí nghiệm."""

import pytest

from alphaforge.research.models import (
    AlphaLineage,
    Experiment,
    ExperimentVariant,
    Hypothesis,
    ResearchProject,
)
from alphaforge.research.store import ResearchStore
from alphaforge.storage.db import Status

SETTINGS = {
    "region": "USA", "universe": "TOP3000", "delay": 1,
    "neutralization": "SUBINDUSTRY", "decay": 0, "truncation": 0.08,
    "pasteurization": "ON", "unitHandling": "VERIFY", "nanHandling": "OFF",
}


@pytest.fixture()
def store(db) -> ResearchStore:
    return ResearchStore(db)


# ----------------------------------------------------------------------
# Phả hệ
# ----------------------------------------------------------------------
def test_lineage_is_upserted_not_duplicated(store):
    store.save_lineage(AlphaLineage(alpha_id="A1", generation_strategy="template"))
    store.save_lineage(AlphaLineage(alpha_id="A1", generation_strategy="mutate"))
    assert store.get_lineage("A1")["generation_strategy"] == "mutate"


def test_ancestry_walks_the_parent_chain(store):
    store.save_lineage(AlphaLineage(alpha_id="GEN3", parent_alpha_id="GEN2"))
    store.save_lineage(AlphaLineage(alpha_id="GEN2", parent_alpha_id="GEN1"))
    store.save_lineage(AlphaLineage(alpha_id="GEN1"))
    chain = [item["alpha_id"] for item in store.ancestry("GEN3")]
    assert chain == ["GEN3", "GEN2", "GEN1"]


def test_ancestry_stops_on_cycles(store):
    """Dữ liệu hỏng có thể tạo vòng cha con. Không được lặp vô hạn."""
    store.save_lineage(AlphaLineage(alpha_id="A", parent_alpha_id="B"))
    store.save_lineage(AlphaLineage(alpha_id="B", parent_alpha_id="A"))
    assert len(store.ancestry("A")) == 2


def test_ancestry_of_unknown_alpha_is_empty(store):
    assert store.ancestry("KHONG-CO") == []


def test_alpha_generated_from_historical_alpha_records_its_source(store):
    """Alpha sinh ra từ một alpha lịch sử cũng phải có phả hệ."""
    store.save_lineage(
        AlphaLineage(
            alpha_id="NEW1",
            parent_alpha_id="HISTORICAL-42",
            generation_strategy="mutate",
            mutation_type="lookback",
            source="historical",
        )
    )
    record = store.get_lineage("NEW1")
    assert record["source"] == "historical"
    assert record["parent_alpha_id"] == "HISTORICAL-42"
    assert store.get_children("HISTORICAL-42")[0]["alpha_id"] == "NEW1"


def test_full_research_chain_is_linked(store):
    project_id = store.create_project(ResearchProject(name="P", family="Momentum"))
    hypothesis_id = store.create_hypothesis(Hypothesis(research_id=project_id, statement="S"))
    experiment_id = store.create_experiment(
        Experiment(hypothesis_id=hypothesis_id, name="E", settings=SETTINGS)
    )
    variant_id = store.add_variant(
        ExperimentVariant(
            experiment_id=experiment_id, label="20 ngay",
            expression="ts_mean(close, 20)", parameters={"lookback": 20},
        )
    )
    store.save_lineage(
        AlphaLineage(
            alpha_id="A1", research_id=project_id, hypothesis_id=hypothesis_id,
            experiment_id=experiment_id, variant_id=variant_id,
            generation_strategy="template",
        )
    )
    lineage = store.get_lineage("A1")
    assert lineage["research_id"] == project_id
    assert lineage["hypothesis_id"] == hypothesis_id
    assert lineage["experiment_id"] == experiment_id
    assert lineage["variant_id"] == variant_id


# ----------------------------------------------------------------------
# Khả năng tái lập
# ----------------------------------------------------------------------
def test_experiment_stores_full_simulation_settings(store):
    hypothesis_id = store.create_hypothesis(
        Hypothesis(
            research_id=store.create_project(ResearchProject(name="P")),
            statement="S",
        )
    )
    experiment_id = store.create_experiment(
        Experiment(hypothesis_id=hypothesis_id, name="E", settings=SETTINGS)
    )
    stored = store.get_experiment(experiment_id)["settings"]
    # Mọi tham số ảnh hưởng tới kết quả mô phỏng đều phải được lưu lại.
    for key in (
        "region", "universe", "delay", "neutralization", "decay",
        "truncation", "pasteurization", "unitHandling", "nanHandling",
    ):
        assert stored[key] == SETTINGS[key]


def test_variant_parameters_round_trip(store):
    hypothesis_id = store.create_hypothesis(
        Hypothesis(research_id=store.create_project(ResearchProject(name="P")), statement="S")
    )
    experiment_id = store.create_experiment(Experiment(hypothesis_id=hypothesis_id, name="E"))
    store.add_variant(
        ExperimentVariant(
            experiment_id=experiment_id, label="v1", expression="x",
            parameters={"lookback": 20, "decay": 5},
        )
    )
    assert store.list_variants(experiment_id)[0]["parameters"] == {"lookback": 20, "decay": 5}


def test_alpha_record_keeps_settings_for_reproduction(db):
    """Mỗi alpha phải nhớ thiết lập đã dùng, không phụ thuộc cấu hình hiện tại."""
    db.add_alphas(["rank(close)"], SETTINGS, generation_strategy="template")
    record = db.fetch_by_status(Status.PENDING)[0]
    assert record.settings == SETTINGS
    assert record.generation_strategy == "template"


def test_same_expression_different_settings_is_a_separate_experiment(db):
    db.add_alphas(["rank(close)"], SETTINGS)
    assert db.add_alphas(["rank(close)"], dict(SETTINGS, region="EUR")) == 1
    assert db.add_alphas(["rank(close)"], SETTINGS) == 0


def test_foreign_keys_cascade_on_project_delete(store, db):
    project_id = store.create_project(ResearchProject(name="P"))
    store.create_hypothesis(Hypothesis(research_id=project_id, statement="S"))
    connection = db.connect()
    try:
        connection.execute("DELETE FROM research_projects WHERE id = ?", (project_id,))
    finally:
        connection.close()
    assert store.list_hypotheses(project_id) == []
