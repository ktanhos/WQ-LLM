"""Kế hoạch sinh biểu thức đã được kiểm tra trước khi chạy.

Bộ sinh không nhận tham số rời rạc từ CLI hay giao diện web nữa. Nó nhận một
`GenerationPlan` đã qua kiểm tra, và kế hoạch đó ghi lại đầy đủ ngữ cảnh
nghiên cứu: thuộc dự án nào, giả thuyết nào, thí nghiệm nào, dùng trường dữ
liệu và cửa sổ nào, giới hạn ra sao.

Lý do bắt buộc đi qua kế hoạch:

    * mỗi biểu thức truy ngược được về giả thuyết sinh ra nó;
    * lô sinh tái lập được, vì hạt giống và mọi tham số đều nằm trong kế hoạch;
    * không đường nào bỏ qua bước kiểm tra để đẩy thẳng biểu thức vào hàng đợi.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from ..generator.templates import TEMPLATES_BY_NAME
from ..storage.db import Database, utc_now

#: Chiến lược sinh được hỗ trợ. Giữ đúng ba chiến lược đang có, không thêm.
STRATEGIES = ("template", "pairwise", "mutate")


class PlanError(ValueError):
    """Kế hoạch không hợp lệ. Không được đưa vào bộ sinh."""


@dataclass
class GenerationPlan:
    """Mô tả đầy đủ một lô sinh biểu thức."""

    strategy: str = "template"
    data_fields: Sequence[str] = field(default_factory=tuple)
    operators: Sequence[str] = field(default_factory=tuple)
    lookbacks: Sequence[int] = field(default_factory=tuple)
    templates: Sequence[str] = field(default_factory=tuple)
    max_candidates: int = 100
    seed: Optional[int] = None
    #: Thiết lập mô phỏng dùng cho toàn lô. Lưu kèm để tái lập được.
    settings: Dict[str, Any] = field(default_factory=dict)
    #: Ràng buộc chuyển tiếp xuống bộ kiểm tra.
    constraints: Dict[str, Any] = field(default_factory=dict)
    #: Ngữ cảnh nghiên cứu.
    research_id: Optional[int] = None
    hypothesis_id: Optional[int] = None
    experiment_id: Optional[int] = None
    #: Biểu thức gốc cho chiến lược mutate.
    seed_expressions: Sequence[str] = field(default_factory=tuple)
    notes: str = ""
    id: Optional[int] = None

    # ------------------------------------------------------------------
    def validate(self) -> "GenerationPlan":
        """Kiểm tra tính nhất quán. Ném `PlanError` khi không dùng được.

        Trả về chính nó để gọi nối chuỗi được.
        """
        if self.strategy not in STRATEGIES:
            raise PlanError(
                f"Chiến lược không hợp lệ: {self.strategy}. "
                f"Chọn một trong {', '.join(STRATEGIES)}."
            )
        if self.max_candidates <= 0:
            raise PlanError("max_candidates phải lớn hơn không.")

        if self.strategy in ("template", "pairwise") and not self.data_fields:
            raise PlanError(
                f"Chiến lược {self.strategy} cần ít nhất một trường dữ liệu."
            )
        if self.strategy == "pairwise" and len(self.data_fields) < 2:
            raise PlanError("Chiến lược pairwise cần ít nhất hai trường dữ liệu.")
        if self.strategy == "mutate" and not self.seed_expressions:
            raise PlanError(
                "Chiến lược mutate cần danh sách biểu thức gốc. "
                "Chưa có alpha nào đạt ngưỡng thì chưa chạy được chiến lược này."
            )

        unknown = [name for name in self.templates if name not in TEMPLATES_BY_NAME]
        if unknown:
            raise PlanError("Mẫu không tồn tại: " + ", ".join(sorted(unknown)) + ".")

        for value in self.lookbacks:
            if int(value) <= 1:
                raise PlanError(
                    f"Cửa sổ nhìn lại {value} không hợp lệ, phải lớn hơn một."
                )

        if self.hypothesis_id is not None and self.research_id is None:
            raise PlanError(
                "Kế hoạch gắn với giả thuyết thì phải nêu rõ dự án nghiên cứu."
            )
        return self

    @property
    def is_valid(self) -> bool:
        try:
            self.validate()
        except PlanError:
            return False
        return True

    def describe(self) -> str:
        """Mô tả ngắn để in ra màn hình hoặc ghi vào nhật ký."""
        parts = [
            f"chiến lược={self.strategy}",
            f"tối đa={self.max_candidates}",
            f"trường={len(self.data_fields)}",
        ]
        if self.templates:
            parts.append(f"mẫu={len(self.templates)}")
        if self.lookbacks:
            parts.append(f"cửa sổ={list(self.lookbacks)}")
        if self.experiment_id:
            parts.append(f"thí nghiệm={self.experiment_id}")
        if self.seed is not None:
            parts.append(f"hạt giống={self.seed}")
        return ", ".join(parts)

    # ------------------------------------------------------------------
    def save(self, db: Database) -> int:
        """Lưu kế hoạch vào kho và trả về mã định danh.

        Kế hoạch được lưu trước khi sinh, nên ngay cả khi lô sinh bị ngắt giữa
        chừng vẫn còn bản ghi cho biết định chạy gì.
        """
        self.validate()
        connection = db.connect()
        try:
            cursor = connection.execute(
                """
                INSERT INTO generation_plans (
                    research_id, hypothesis_id, experiment_id, strategy,
                    data_fields_json, operators_json, lookbacks_json,
                    templates_json, constraints_json, settings_json,
                    max_candidates, seed, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.research_id, self.hypothesis_id, self.experiment_id,
                    self.strategy,
                    _dump(list(self.data_fields)), _dump(list(self.operators)),
                    _dump([int(value) for value in self.lookbacks]),
                    _dump(list(self.templates)), _dump(self.constraints),
                    _dump(self.settings), int(self.max_candidates), self.seed,
                    self.notes, utc_now(),
                ),
            )
            self.id = int(cursor.lastrowid)
            return self.id
        finally:
            connection.close()

    @classmethod
    def load(cls, db: Database, plan_id: int) -> Optional["GenerationPlan"]:
        connection = db.connect()
        try:
            row = connection.execute(
                "SELECT * FROM generation_plans WHERE id = ?", (int(plan_id),)
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        return cls(
            id=int(row["id"]),
            research_id=row["research_id"],
            hypothesis_id=row["hypothesis_id"],
            experiment_id=row["experiment_id"],
            strategy=str(row["strategy"]),
            data_fields=_load(row["data_fields_json"]),
            operators=_load(row["operators_json"]),
            lookbacks=[int(value) for value in _load(row["lookbacks_json"])],
            templates=_load(row["templates_json"]),
            constraints=_load(row["constraints_json"]) or {},
            settings=_load(row["settings_json"]) or {},
            max_candidates=int(row["max_candidates"]),
            seed=row["seed"],
            notes=str(row["notes"] or ""),
        )


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _load(raw: Any) -> Any:
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return []
