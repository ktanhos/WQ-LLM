"""Kho cho Research Project, Hypothesis, Experiment và phả hệ alpha.

Lớp này dùng chung tệp SQLite với kho alpha thay vì giữ tệp riêng. Lý do: câu
hỏi thường gặp nhất của người nghiên cứu là "biểu thức này thuộc thí nghiệm
nào và đã cho kết quả gì", và câu hỏi đó cần nối bảng experiments với bảng
alphas. Hai tệp riêng buộc phải nối thủ công trong Python và mất tính nguyên
tử khi một thao tác chạm vào cả hai lớp.

Schema do storage.db quản lý tập trung. Store chỉ đọc ghi, không tự tạo bảng,
nhờ vậy chỉ có một nơi duy nhất định nghĩa cấu trúc dữ liệu.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..storage.db import Database
from .models import (
    AlphaLineage,
    Experiment,
    ExperimentVariant,
    Hypothesis,
    ResearchProject,
)


class ResearchStore:
    """Repository cho lớp nghiên cứu.

    Store không gọi BRAIN và không biết chi tiết mô phỏng, nên kiểm thử được
    hoàn toàn ngoại tuyến.
    """

    def __init__(self, db: Database | str | Path):
        self.db = db if isinstance(db, Database) else Database(db)
        self.db_path = str(self.db.path)

    def connect(self) -> sqlite3.Connection:
        return self.db.connect()

    def initialize(self) -> None:
        """Giữ lại cho tương thích ngược. Schema đã được tạo ở Database."""
        self.db.initialize()

    @staticmethod
    def _row(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
        return dict(row) if row is not None else None

    # ------------------------------------------------------------------
    # Research project
    # ------------------------------------------------------------------
    def create_project(self, project: ResearchProject) -> int:
        connection = self.connect()
        try:
            cursor = connection.execute(
                """
                INSERT INTO research_projects
                (name, family, objective, region, universe, delay, status, notes,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (project.name, project.family, project.objective, project.region,
                 project.universe, project.delay, project.status, project.notes,
                 project.created_at, project.updated_at),
            )
            return int(cursor.lastrowid)
        finally:
            connection.close()

    def get_project(self, project_id: int) -> Optional[Dict[str, Any]]:
        connection = self.connect()
        try:
            return self._row(
                connection.execute(
                    "SELECT * FROM research_projects WHERE id = ?", (int(project_id),)
                ).fetchone()
            )
        finally:
            connection.close()

    def list_projects(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        query = "SELECT * FROM research_projects"
        params: tuple[Any, ...] = ()
        if status:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY updated_at DESC, id DESC"
        connection = self.connect()
        try:
            return [dict(row) for row in connection.execute(query, params).fetchall()]
        finally:
            connection.close()

    # ------------------------------------------------------------------
    # Hypothesis
    # ------------------------------------------------------------------
    def create_hypothesis(self, hypothesis: Hypothesis) -> int:
        connection = self.connect()
        try:
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
        finally:
            connection.close()

    def list_hypotheses(self, research_id: int) -> List[Dict[str, Any]]:
        connection = self.connect()
        try:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM hypotheses WHERE research_id = ? ORDER BY id",
                    (int(research_id),),
                ).fetchall()
            ]
        finally:
            connection.close()

    # ------------------------------------------------------------------
    # Experiment
    # ------------------------------------------------------------------
    def create_experiment(self, experiment: Experiment) -> int:
        connection = self.connect()
        try:
            cursor = connection.execute(
                """
                INSERT INTO experiments
                (hypothesis_id, name, objective, base_expression, variable_changed,
                 expected_effect, status, settings_json, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (experiment.hypothesis_id, experiment.name, experiment.objective,
                 experiment.base_expression, experiment.variable_changed,
                 experiment.expected_effect, experiment.status,
                 json.dumps(experiment.settings, ensure_ascii=False, sort_keys=True,
                            default=str),
                 experiment.notes, experiment.created_at, experiment.updated_at),
            )
            return int(cursor.lastrowid)
        finally:
            connection.close()

    def get_experiment(self, experiment_id: int) -> Optional[Dict[str, Any]]:
        connection = self.connect()
        try:
            row = connection.execute(
                "SELECT * FROM experiments WHERE id = ?", (int(experiment_id),)
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        item = dict(row)
        item["settings"] = _load_json(item.get("settings_json"))
        return item

    def list_experiments(self, hypothesis_id: int) -> List[Dict[str, Any]]:
        connection = self.connect()
        try:
            rows = connection.execute(
                "SELECT * FROM experiments WHERE hypothesis_id = ? ORDER BY id",
                (int(hypothesis_id),),
            ).fetchall()
        finally:
            connection.close()
        result = []
        for row in rows:
            item = dict(row)
            item["settings"] = _load_json(item.get("settings_json"))
            result.append(item)
        return result

    # ------------------------------------------------------------------
    # Variant
    # ------------------------------------------------------------------
    def add_variant(self, variant: ExperimentVariant) -> int:
        connection = self.connect()
        try:
            cursor = connection.execute(
                """
                INSERT INTO experiment_variants
                (experiment_id, label, expression, parameters_json, alpha_id,
                 result_status, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (variant.experiment_id, variant.label, variant.expression,
                 json.dumps(variant.parameters, ensure_ascii=False, sort_keys=True,
                            default=str),
                 variant.alpha_id, variant.result_status, variant.notes,
                 variant.created_at),
            )
            return int(cursor.lastrowid)
        finally:
            connection.close()

    def list_variants(self, experiment_id: int) -> List[Dict[str, Any]]:
        connection = self.connect()
        try:
            rows = connection.execute(
                "SELECT * FROM experiment_variants WHERE experiment_id = ? ORDER BY id",
                (int(experiment_id),),
            ).fetchall()
        finally:
            connection.close()
        result = []
        for row in rows:
            item = dict(row)
            item["parameters"] = _load_json(item.pop("parameters_json", "{}"))
            result.append(item)
        return result

    # ------------------------------------------------------------------
    # Phả hệ alpha
    # ------------------------------------------------------------------
    def save_lineage(self, lineage: AlphaLineage) -> None:
        connection = self.connect()
        try:
            connection.execute(
                """
                INSERT INTO alpha_lineage
                (alpha_id, parent_alpha_id, research_id, hypothesis_id, experiment_id,
                 variant_id, generation_strategy, generation_seed, mutation_type,
                 source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(alpha_id) DO UPDATE SET
                    parent_alpha_id = excluded.parent_alpha_id,
                    research_id = excluded.research_id,
                    hypothesis_id = excluded.hypothesis_id,
                    experiment_id = excluded.experiment_id,
                    variant_id = excluded.variant_id,
                    generation_strategy = excluded.generation_strategy,
                    generation_seed = excluded.generation_seed,
                    mutation_type = excluded.mutation_type,
                    source = excluded.source
                """,
                (lineage.alpha_id, lineage.parent_alpha_id, lineage.research_id,
                 lineage.hypothesis_id, lineage.experiment_id, lineage.variant_id,
                 lineage.generation_strategy, lineage.generation_seed,
                 lineage.mutation_type, lineage.source, lineage.created_at),
            )
        finally:
            connection.close()

    def get_lineage(self, alpha_id: str) -> Optional[Dict[str, Any]]:
        connection = self.connect()
        try:
            return self._row(
                connection.execute(
                    "SELECT * FROM alpha_lineage WHERE alpha_id = ?", (str(alpha_id),)
                ).fetchone()
            )
        finally:
            connection.close()

    def get_children(self, parent_alpha_id: str) -> List[Dict[str, Any]]:
        connection = self.connect()
        try:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM alpha_lineage WHERE parent_alpha_id = ? ORDER BY created_at",
                    (str(parent_alpha_id),),
                ).fetchall()
            ]
        finally:
            connection.close()

    def ancestry(self, alpha_id: str, max_depth: int = 20) -> List[Dict[str, Any]]:
        """Lần ngược chuỗi cha của một alpha.

        Có chặn độ sâu vì dữ liệu hỏng có thể tạo vòng cha con.
        """
        chain: List[Dict[str, Any]] = []
        seen: set[str] = set()
        current = str(alpha_id)
        for _ in range(max_depth):
            if current in seen:
                break
            seen.add(current)
            record = self.get_lineage(current)
            if record is None:
                break
            chain.append(record)
            parent = record.get("parent_alpha_id")
            if not parent:
                break
            current = str(parent)
        return chain


def _load_json(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}
