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

from ..storage.db import Database, load_json_dict as _load_json
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

    def experiment_detail(self, experiment_id: int) -> Optional[Dict[str, Any]]:
        """Thí nghiệm kèm thiết kế, biến thể và alpha đã sinh ra từ nó.

        Dòng lệnh và bảng theo dõi cùng gọi hàm này, để hai nơi không bao giờ
        mô tả một thí nghiệm theo hai cách khác nhau.

        Không dùng `ExperimentReport` ở đây vì báo cáo cần kết quả mô phỏng,
        còn màn hình chi tiết phải trả lời được ngay cả khi thí nghiệm vừa
        thiết kế xong và chưa có alpha nào chạy.
        """
        experiment = self.get_experiment(int(experiment_id))
        if experiment is None:
            return None

        settings = dict(experiment.get("settings") or {})
        # Thiết kế nằm lồng trong settings chứ không có cột riêng. Tách ra để
        # người đọc phân biệt được thiết kế với thiết lập mô phỏng.
        design = settings.pop("_design", {})
        experiment["settings"] = settings
        # Bỏ cột JSON thô: nó lặp lại nguyên phần vừa tách ra, kể cả `_design`,
        # khiến người đọc thấy thiết kế ở hai chỗ với hai hình dạng khác nhau.
        experiment.pop("settings_json", None)

        connection = self.connect()
        try:
            alphas = [dict(row) for row in connection.execute(
                "SELECT id, local_id, alpha_id, variant_id, status, evaluation_status,"
                " score, expression FROM alphas WHERE experiment_id = ? ORDER BY id",
                (int(experiment_id),),
            ).fetchall()]
        finally:
            connection.close()

        counts: Dict[str, int] = {}
        for row in alphas:
            key = str(row.get("status") or "UNKNOWN")
            counts[key] = counts.get(key, 0) + 1

        return {
            "experiment": experiment,
            "design": design,
            "variants": self.list_variants(int(experiment_id)),
            "alphas": alphas,
            "status_counts": counts,
        }

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
                 source, source_alpha_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(alpha_id) DO UPDATE SET
                    parent_alpha_id = excluded.parent_alpha_id,
                    research_id = excluded.research_id,
                    hypothesis_id = excluded.hypothesis_id,
                    experiment_id = excluded.experiment_id,
                    variant_id = excluded.variant_id,
                    generation_strategy = excluded.generation_strategy,
                    generation_seed = excluded.generation_seed,
                    mutation_type = excluded.mutation_type,
                    source = excluded.source,
                    source_alpha_id = excluded.source_alpha_id
                """,
                (lineage.alpha_id, lineage.parent_alpha_id, lineage.research_id,
                 lineage.hypothesis_id, lineage.experiment_id, lineage.variant_id,
                 lineage.generation_strategy, lineage.generation_seed,
                 lineage.mutation_type, lineage.source, lineage.source_alpha_id,
                 lineage.created_at),
            )
        finally:
            connection.close()

    def link_platform_alpha(
        self, local_id: str, alpha_id: str, *, mutation_type: str = ""
    ) -> bool:
        """Nối mã alpha của nền tảng vào chuỗi phả hệ đã có.

        Alpha ID chỉ xuất hiện sau khi mô phỏng, còn phả hệ được ghi ngay lúc
        sinh dưới định danh cục bộ. Hàm này thêm một mắt xích mới trỏ về mắt
        xích cục bộ, nên chuỗi đầy đủ trở thành:

            Research → Hypothesis → Experiment → Variant → LOCAL-128 → A12345

        Giữ cả hai mắt xích thay vì đổi tên mắt xích cũ: alpha cục bộ vẫn tồn
        tại kể cả khi mô phỏng thất bại, và đó cũng là thông tin nghiên cứu.
        """
        local = self.get_lineage(str(local_id))
        if local is None or not alpha_id:
            return False
        self.save_lineage(
            AlphaLineage(
                alpha_id=str(alpha_id),
                parent_alpha_id=str(local_id),
                research_id=local.get("research_id"),
                hypothesis_id=local.get("hypothesis_id"),
                experiment_id=local.get("experiment_id"),
                variant_id=local.get("variant_id"),
                generation_strategy=str(local.get("generation_strategy") or ""),
                generation_seed=local.get("generation_seed"),
                mutation_type=mutation_type or str(local.get("mutation_type") or ""),
                source=str(local.get("source") or ""),
                source_alpha_id=str(local_id),
            )
        )
        return True

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


