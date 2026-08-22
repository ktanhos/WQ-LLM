from research_models import AlphaLineage, Experiment, ExperimentVariant, Hypothesis, ResearchProject
from research_store import ResearchStore


def test_research_lifecycle(tmp_path):
    store = ResearchStore(tmp_path / "research.db")

    research_id = store.create_project(
        ResearchProject(
            name="Momentum Volume",
            family="Momentum",
            objective="Kiểm tra volume xác nhận momentum",
        )
    )
    project = store.get_project(research_id)
    assert project["name"] == "Momentum Volume"

    hypothesis_id = store.create_hypothesis(
        Hypothesis(
            research_id=research_id,
            statement="Momentum có thể mạnh hơn khi volume xác nhận biến động giá.",
            economic_intuition="Volume phản ánh mức độ tham gia của thị trường.",
            expected_direction="positive",
            expected_horizon="5 đến 20 ngày",
        )
    )
    assert len(store.list_hypotheses(research_id)) == 1

    experiment_id = store.create_experiment(
        Experiment(
            hypothesis_id=hypothesis_id,
            name="Lookback test",
            base_expression="rank(ts_rank(returns,20))",
            variable_changed="lookback",
        )
    )
    assert store.list_experiments(hypothesis_id)[0]["name"] == "Lookback test"

    variant_id = store.add_variant(
        ExperimentVariant(
            experiment_id=experiment_id,
            label="20 ngày",
            expression="rank(ts_rank(returns,20))",
            parameters={"lookback": 20},
        )
    )
    variants = store.list_variants(experiment_id)
    assert variants[0]["id"] == variant_id
    assert variants[0]["parameters"] == {"lookback": 20}

    store.save_lineage(
        AlphaLineage(
            alpha_id="ALPHA-002",
            parent_alpha_id="ALPHA-001",
            research_id=research_id,
            hypothesis_id=hypothesis_id,
            experiment_id=experiment_id,
            generation_strategy="mutate",
            mutation_type="lookback",
        )
    )
    children = store.get_children("ALPHA-001")
    assert children[0]["alpha_id"] == "ALPHA-002"
