"""Kho SQLite cho Research Project, Hypothesis và Experiment.

Đây là lớp lưu trữ độc lập với kho alpha hiện tại. Mục tiêu của giai đoạn
đầu là bổ sung trí nhớ nghiên cứu mà không thay đổi pipeline mô phỏng đang
hoạt động.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from research_models import (
    AlphaLineage,
    Experiment,
    ExperimentVariant,
    Hypothesis,
    ResearchProject,
)


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS research_projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    family TEXT NOT NULL DEFAULT '',
    objective TEXT NOT NULL DEFAULT '',
    region TEXT NOT NULL DEFAULT 'USA',
    universe TEXT NOT NULL DEFAULT 'TOP3000',
    delay INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hypotheses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    research_id INTEGER NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
    statement TEXT NOT NULL,
    economic_intuition TEXT NOT NULL DEFAULT '',
    expected_direction TEXT NOT NULL DEFAULT '',
    expected_horizon TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'OPEN',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hypothesis_id INTEGER NOT NULL REFERENCES hypotheses(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    objective TEXT NOT NULL DEFAULT '',
    base_expression TEXT NOT NULL DEFAULT '',
    variable_changed TEXT NOT NULL DEFAULT '',
    expected_effect TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'DESIGNED',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiment_variants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id INTEGER NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    expression TEXT NOT NULL,
    parameters_json TEXT NOT NULL DEFAULT '{}',
    alpha_id TEXT,
    result_status TEXT NOT NULL DEFAULT 'PENDING',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alpha_lineage (
    alpha_id TEXT PRIMARY KEY,
    parent_alpha_id TEXT,
    research_id INTEGER REFERENCES research_projects(id) ON DELETE SET NULL,
    hypothesis_id INTEGER REFERENCES hypotheses(id) ON DELETE SET NULL,
    experiment_id INTEGER REFERENCES experiments(id) ON DELETE SET NULL,
    generation_strategy TEXT NOT NULL DEFAULT '',
    mutation_type TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_hypotheses_research ON hypotheses(research_id);
CREATE INDEX IF NOT EXISTS idx_experiments_hypothesis ON experiments(hypothesis_id);
CREATE INDEX IF NOT EXISTS idx_variants_experiment ON experiment_variants(experiment_id);
CREATE INDEX IF NOT EXISTS idx_lineage_parent ON alpha_lineage(parent_alpha_id);
"""


class ResearchStore:
    """Repository đơn giản cho lớp nghiên cứu.

    Store không gọi BRAIN và không biết chi tiết simulation. Nhờ vậy có thể
    kiểm thử độc lập và tích hợp dần vào kho alpha hiện tại.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    @staticmethod
    def _row(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
        return dict(row) if row is not None else None

    def create_project(self, project: ResearchProject) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO research_projects
                (name, family, objective, region, universe, delay, status, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (project.name, project.family, project.objective, project.region,
                 project.universe, project.delay, project.status, project.notes,
                 project.created_at, project.updated_at),
            )
            return int(cursor.lastrowid)

    def get_project(self, project_id: int) -> Optional[Dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM research_projects WHERE id = ?", (project_id,)
            ).fetchone()
            return self._row(row)

    def list_projects(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        query = "SELECT * FROM research_projects"
        params: tuple[Any, ...] = ()
        if status:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY updated_at DESC, id DESC"
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(query, params).fetchall()]

    def create_hypothesis(self, hypothesis: Hypothesis) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO hypotheses
                (research_id, statement, economic_intuition, expected_direction,
                 expected_horizon, status, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (hypothesis.research_id, hypothesis.statement,
                 hypothesis.economic_intuition, hypothesis.expected_direction,
                 hypothesis.expected_horizon, hypothesis.status, hypothesis.notes,
                 hypothesis.created_at, hypothesis.updated_at),
            )
            return int(cursor.lastrowid)

    def list_hypotheses(self, research_id: int) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM hypotheses WHERE research_id = ? ORDER BY id",
                    (research_id,),
                ).fetchall()
            ]

    def create_experiment(self, experiment: Experiment) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO experiments
                (hypothesis_id, name, objective, base_expression, variable_changed,
                 expected_effect, status, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (experiment.hypothesis_id, experiment.name, experiment.objective,
                 experiment.base_expression, experiment.variable_changed,
                 experiment.expected_effect, experiment.status, experiment.notes,
                 experiment.created_at, experiment.updated_at),
            )
            return int(cursor.lastrowid)

    def list_experiments(self, hypothesis_id: int) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM experiments WHERE hypothesis_id = ? ORDER BY id",
                    (hypothesis_id,),
                ).fetchall()
            ]

    def add_variant(self, variant: ExperimentVariant) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO experiment_variants
                (experiment_id, label, expression, parameters_json, alpha_id,
                 result_status, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (variant.experiment_id, variant.label, variant.expression,
                 json.dumps(variant.parameters, ensure_ascii=False, sort_keys=True),
                 variant.alpha_id, variant.result_status, variant.notes,
                 variant.created_at),
            )
            return int(cursor.lastrowid)

    def list_variants(self, experiment_id: int) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM experiment_variants WHERE experiment_id = ? ORDER BY id",
                (experiment_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["parameters"] = json.loads(item.pop("parameters_json") or "{}")
            result.append(item)
        return result

    def save_lineage(self, lineage: AlphaLineage) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO alpha_lineage
                (alpha_id, parent_alpha_id, research_id, hypothesis_id, experiment_id,
                 generation_strategy, mutation_type, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(alpha_id) DO UPDATE SET
                    parent_alpha_id = excluded.parent_alpha_id,
                    research_id = excluded.research_id,
                    hypothesis_id = excluded.hypothesis_id,
                    experiment_id = excluded.experiment_id,
                    generation_strategy = excluded.generation_strategy,
                    mutation_type = excluded.mutation_type
                """,
                (lineage.alpha_id, lineage.parent_alpha_id, lineage.research_id,
                 lineage.hypothesis_id, lineage.experiment_id,
                 lineage.generation_strategy, lineage.mutation_type,
                 lineage.created_at),
            )

    def get_children(self, parent_alpha_id: str) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM alpha_lineage WHERE parent_alpha_id = ? ORDER BY created_at",
                    (parent_alpha_id,),
                ).fetchall()
            ]
