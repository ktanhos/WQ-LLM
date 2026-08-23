"""Nối kế hoạch sinh với hàng đợi mô phỏng.

Đây là con đường duy nhất để một biểu thức đi từ ý tưởng vào hàng đợi:

    GenerationPlan → GeneratorEngine → AlphaValidator → hàng đợi

Không có lối tắt. Biểu thức không qua được kiểm tra được ghi lại với trạng thái
`INVALID` kèm lý do, chứ không bị vứt đi im lặng: biết một biểu thức sai ở đâu
cũng là thông tin nghiên cứu, và giữ lại giúp không sinh lại nó lần sau.

Mô đun không gọi mạng. Nó dừng ngay trước bước mô phỏng.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..generator.engine import GenerationRequest, GeneratorEngine
from ..history.fingerprint import fingerprint
from ..research.memory import GenerationContext
from ..research.plan import STRATEGY_DIRECT, GenerationPlan
from ..storage.db import Database, Status
from .validation import AlphaValidator, ValidationConstraints, validator_from_database

logger = logging.getLogger(__name__)


@dataclass
class GenerationOutcome:
    """Kết quả một lượt sinh, đủ chi tiết để giải thích vì sao lô ngắn hơn mong đợi."""

    plan_id: Optional[int] = None
    run_id: Optional[int] = None
    generated: int = 0
    valid: int = 0
    invalid: int = 0
    queued: int = 0
    duplicates: int = 0
    rejected_by_memory: Dict[str, int] = field(default_factory=dict)
    invalid_reasons: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "run_id": self.run_id,
            "generated": self.generated,
            "valid": self.valid,
            "invalid": self.invalid,
            "queued": self.queued,
            "duplicates": self.duplicates,
            "rejected_by_memory": self.rejected_by_memory,
            "invalid_reasons": self.invalid_reasons[:20],
        }


def request_from_plan(
    plan: GenerationPlan, context: Optional[GenerationContext] = None
) -> GenerationRequest:
    """Chuyển kế hoạch đã kiểm tra thành yêu cầu cho bộ sinh.

    Bộ sinh giữ nguyên giao diện cũ. Hàm này chỉ dịch, không thay đổi hành vi
    của ba chiến lược đang có.
    """
    plan.validate()
    kwargs: Dict[str, Any] = {
        "strategy": plan.strategy,
        "limit": plan.max_candidates,
        "fields": tuple(plan.data_fields),
        "seed": plan.seed,
        "seed_expressions": tuple(plan.seed_expressions),
        "context": context,
    }
    if plan.templates:
        kwargs["templates"] = tuple(plan.templates)
    if plan.lookbacks:
        kwargs["windows"] = tuple(int(value) for value in plan.lookbacks)
    max_length = plan.constraints.get("max_length")
    if max_length:
        kwargs["max_length"] = int(max_length)
    return GenerationRequest(**kwargs)


def constraints_from_plan(plan: GenerationPlan) -> ValidationConstraints:
    """Đưa ràng buộc của kế hoạch xuống bộ kiểm tra."""
    raw = dict(plan.constraints or {})
    constraints = ValidationConstraints()
    for name in (
        "max_length", "max_operators", "max_fields", "max_depth", "min_operators",
    ):
        if name in raw:
            setattr(constraints, name, int(raw[name]))
    if raw.get("allowed_fields"):
        constraints.allowed_fields = list(raw["allowed_fields"])
    if raw.get("allowed_operators"):
        constraints.allowed_operators = list(raw["allowed_operators"])
    if raw.get("required_operators"):
        constraints.required_operators = tuple(raw["required_operators"])
    if raw.get("forbidden_operators"):
        constraints.forbidden_operators = tuple(raw["forbidden_operators"])
    return constraints


def generate_and_queue(
    db: Database,
    plan: GenerationPlan,
    *,
    context: Optional[GenerationContext] = None,
    validator: Optional[AlphaValidator] = None,
    tag: str = "",
    dry_run: bool = False,
    store_invalid: bool = True,
    variant_ids: Optional[Dict[str, int]] = None,
) -> GenerationOutcome:
    """Sinh biểu thức theo kế hoạch, kiểm tra, rồi đưa vào hàng đợi.

    Trả về `GenerationOutcome` mô tả đầy đủ chuyện gì đã xảy ra. Khi `dry_run`
    bật, không có gì được ghi vào kho.

    `variant_ids` ánh xạ biểu thức sang mã biến thể, dùng khi lô sinh đến từ một
    thí nghiệm. Nhờ nó báo cáo quy được kết quả về đúng biến thể đã thiết kế.
    """
    plan.validate()
    settings = dict(plan.settings or {})

    if plan.strategy == STRATEGY_DIRECT:
        # Biểu thức đã xác định. Không đụng tới bộ sinh, vì bọc thêm toán tử sẽ
        # làm hỏng thiết kế của thí nghiệm.
        expressions = list(plan.seed_expressions)[: plan.max_candidates]
        rejected: Dict[str, int] = {}
    else:
        engine = GeneratorEngine(request_from_plan(plan, context))
        expressions = engine.generate()
        rejected = {key: value for key, value in engine.rejected.items() if value}

    outcome = GenerationOutcome(
        plan_id=plan.id, generated=len(expressions), rejected_by_memory=rejected,
    )

    if validator is None:
        validator = validator_from_database(
            db,
            region=str(settings.get("region", "USA")),
            universe=str(settings.get("universe", "TOP3000")),
            delay=int(settings.get("delay", 1)),
            constraints=constraints_from_plan(plan),
        )
    else:
        validator.constraints = constraints_from_plan(plan)

    results = validator.validate_many(expressions)
    accepted = [result for result in results if result.valid]
    refused = [result for result in results if not result.valid]

    outcome.valid = len(accepted)
    outcome.invalid = len(refused)
    outcome.invalid_reasons = [result.reason for result in refused if result.reason]

    if dry_run:
        return outcome

    outcome.run_id = db.create_run(tag=tag, strategy=plan.strategy)

    if accepted:
        fingerprints = {
            result.expression: {
                "fingerprint": result.meta.get("family"),
                "family": result.meta.get("family"),
            }
            for result in accepted
        }
        added = db.add_alphas(
            [result.expression for result in accepted],
            settings,
            run_id=outcome.run_id,
            experiment_id=plan.experiment_id,
            generation_strategy=plan.strategy,
            generation_seed=plan.seed,
            fingerprints=fingerprints,
        )
        outcome.queued = added
        outcome.duplicates = len(accepted) - added
        _stamp_plan_metadata(db, outcome.run_id, plan)
        if variant_ids:
            _link_variants(db, outcome.run_id, variant_ids)

    if store_invalid and refused:
        # Biểu thức hỏng vẫn được ghi lại: biết nó hỏng ở đâu là thông tin
        # nghiên cứu, và lần sau không sinh lại nó nữa.
        _store_invalid(db, refused, settings, plan, outcome.run_id)

    logger.info(
        "Sinh %d biểu thức: %d hợp lệ, %d không hợp lệ, %d vào hàng đợi.",
        outcome.generated, outcome.valid, outcome.invalid, outcome.queued,
    )
    return outcome


def _stamp_plan_metadata(db: Database, run_id: int, plan: GenerationPlan) -> None:
    """Gắn mã kế hoạch và nguồn gốc lên các bản ghi vừa thêm."""
    connection = db.connect()
    try:
        connection.execute(
            """
            UPDATE alphas
               SET plan_id = ?, source_type = ?
             WHERE run_id = ?
            """,
            (plan.id, plan.strategy, int(run_id)),
        )
    finally:
        connection.close()


def _store_invalid(
    db: Database,
    refused: List[Any],
    settings: Dict[str, Any],
    plan: GenerationPlan,
    run_id: Optional[int],
) -> None:
    """Ghi biểu thức không hợp lệ với trạng thái INVALID kèm lý do."""
    expressions = [result.expression for result in refused]
    fingerprints = {}
    for result in refused:
        family = result.meta.get("family")
        if not family:
            try:
                family = fingerprint(result.expression)["family"]
            except Exception:
                family = None
        fingerprints[result.expression] = {"fingerprint": family, "family": family}

    db.add_alphas(
        expressions, settings, run_id=run_id,
        experiment_id=plan.experiment_id,
        generation_strategy=plan.strategy,
        generation_seed=plan.seed,
        fingerprints=fingerprints,
    )

    connection = db.connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        for result in refused:
            connection.execute(
                """
                UPDATE alphas
                   SET status = ?, validation_error = ?, plan_id = ?, source_type = ?
                 WHERE expression = ? AND status = ?
                """,
                (Status.INVALID, result.reason, plan.id, plan.strategy,
                 result.expression, Status.PENDING),
            )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def _link_variants(db: Database, run_id: int, variant_ids: Dict[str, int]) -> None:
    """Gắn mã biến thể lên bản ghi tương ứng.

    Không có bước này, báo cáo thí nghiệm không quy được alpha về biến thể nào
    và mọi biến thể đều hiện ra với cỡ mẫu bằng không.
    """
    connection = db.connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        for expression, variant_id in variant_ids.items():
            connection.execute(
                "UPDATE alphas SET variant_id = ? WHERE run_id = ? AND expression = ?",
                (int(variant_id), int(run_id), expression),
            )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()
