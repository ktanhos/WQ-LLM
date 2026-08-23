"""Các mô hình dữ liệu cho lớp nghiên cứu Alpha Research Hub.

Lớp này cố ý độc lập với BRAIN API và simulation pipeline. Research là
ngữ cảnh nghiên cứu; simulation là một tác vụ nằm bên dưới research.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ResearchProject:
    id: Optional[int] = None
    name: str = ""
    family: str = ""
    objective: str = ""
    region: str = "USA"
    universe: str = "TOP3000"
    delay: int = 1
    status: str = "ACTIVE"
    notes: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)


@dataclass(frozen=True)
class Hypothesis:
    id: Optional[int] = None
    research_id: int = 0
    statement: str = ""
    economic_intuition: str = ""
    expected_direction: str = ""
    expected_horizon: str = ""
    status: str = "OPEN"
    notes: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)


@dataclass(frozen=True)
class Experiment:
    id: Optional[int] = None
    hypothesis_id: int = 0
    name: str = ""
    objective: str = ""
    base_expression: str = ""
    variable_changed: str = ""
    expected_effect: str = ""
    status: str = "DESIGNED"
    #: Thiết lập mô phỏng đầy đủ của thí nghiệm. Lưu kèm để tái lập được về sau
    #: kể cả khi cấu hình mặc định của dự án đã thay đổi.
    settings: Dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)


@dataclass(frozen=True)
class ExperimentVariant:
    id: Optional[int] = None
    experiment_id: int = 0
    label: str = ""
    expression: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    alpha_id: Optional[str] = None
    result_status: str = "PENDING"
    notes: str = ""
    created_at: str = field(default_factory=utc_now)


@dataclass(frozen=True)
class AlphaLineage:
    alpha_id: str
    parent_alpha_id: Optional[str] = None
    research_id: Optional[int] = None
    hypothesis_id: Optional[int] = None
    experiment_id: Optional[int] = None
    variant_id: Optional[int] = None
    generation_strategy: str = ""
    #: Hạt giống của lô sinh, cần để tái lập chính xác biểu thức.
    generation_seed: Optional[int] = None
    mutation_type: str = ""
    #: Cách alpha ra đời: template, pairwise, mutate, historical hoặc manual.
    source: str = ""
    #: Alpha gốc khi biểu thức được biến đổi từ một alpha đã có. Khác
    #: parent_alpha_id ở chỗ cha có thể là alpha nội bộ, còn nguồn có thể là
    #: một alpha lịch sử nhập từ nền tảng.
    source_alpha_id: Optional[str] = None
    created_at: str = field(default_factory=utc_now)

    @property
    def source_type(self) -> str:
        """Tên gọi khác của `source`, khớp với thuật ngữ trong tài liệu."""
        return self.source
