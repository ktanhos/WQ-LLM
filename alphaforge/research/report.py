"""Báo cáo kết quả một thí nghiệm.

Phần khó nhất của báo cáo không phải thống kê mà là kết luận. Với bốn biến thể
và bốn lượt mô phỏng, không có kết luận nào về giả thuyết là chính đáng, dù
con số trông thuyết phục tới đâu. Vì vậy mô đun này phân biệt rõ ba mức:

    bằng chứng chưa đủ      cỡ mẫu quá nhỏ, chưa kết luận được gì
    ủng hộ giả thuyết       hiệu ứng đúng chiều kỳ vọng và cỡ mẫu đủ
    trái với giả thuyết     hiệu ứng ngược chiều kỳ vọng và cỡ mẫu đủ

"Bằng chứng chưa đủ" là kết luận hợp lệ và thường gặp nhất. Nó không phải thất
bại của thí nghiệm; nó cho biết cần chạy thêm bao nhiêu để kết luận được.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..history.analyzer import describe
from ..storage.db import Database, EvaluationStatus, Status
from .memory import ResearchMemory
from .store import ResearchStore

#: Kết luận có thể rút ra.
CONCLUSION_INSUFFICIENT = "evidence_insufficient"
CONCLUSION_SUPPORTS = "supports_hypothesis"
CONCLUSION_CONTRADICTS = "contradicts_hypothesis"
CONCLUSION_INCONCLUSIVE = "inconclusive"

#: Bốn mức bằng chứng. Tách khỏi kết luận vì hai thứ độc lập: một kết luận
#: "ủng hộ giả thuyết" trên tám alpha và trên tám trăm alpha có sức nặng rất
#: khác nhau, dù nhãn kết luận giống hệt.
EVIDENCE_INSUFFICIENT = "insufficient"
EVIDENCE_WEAK = "weak"
EVIDENCE_MODERATE = "moderate"
EVIDENCE_STRONG = "strong"

#: Cỡ mẫu tối thiểu cho từng mức bằng chứng.
EVIDENCE_THRESHOLDS = (
    (EVIDENCE_STRONG, 100),
    (EVIDENCE_MODERATE, 30),
    (EVIDENCE_WEAK, 8),
)


def evidence_level(sample_size: int, min_sample: int = 8) -> str:
    """Xếp mức bằng chứng theo cỡ mẫu.

    Dưới `min_sample` thì không có mức nào cả: chưa đủ để nói gì.
    """
    if sample_size < min_sample:
        return EVIDENCE_INSUFFICIENT
    for level, threshold in EVIDENCE_THRESHOLDS:
        if sample_size >= threshold:
            return level
    return EVIDENCE_INSUFFICIENT


#: Số alpha có chỉ số tối thiểu để rút bất kỳ kết luận nào.
DEFAULT_MIN_SAMPLE = 8
#: Chênh lệch tương đối tối thiểu giữa biến thể tốt nhất và kém nhất để coi là
#: có hiệu ứng, thay vì chỉ là dao động.
DEFAULT_MIN_EFFECT = 0.15


@dataclass
class VariantResult:
    """Kết quả của một biến thể trong thí nghiệm."""

    label: str
    expression: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    alpha_count: int = 0
    passed: int = 0
    median_sharpe: Optional[float] = None
    best_sharpe: Optional[float] = None
    median_fitness: Optional[float] = None
    median_turnover: Optional[float] = None

    @property
    def pass_rate(self) -> float:
        return round(self.passed / self.alpha_count, 4) if self.alpha_count else 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "expression": self.expression,
            "parameters": self.parameters,
            "alpha_count": self.alpha_count,
            "passed": self.passed,
            "pass_rate": self.pass_rate,
            "median_sharpe": self.median_sharpe,
            "best_sharpe": self.best_sharpe,
            "median_fitness": self.median_fitness,
            "median_turnover": self.median_turnover,
        }


class ExperimentReport:
    """Dựng báo cáo cho một thí nghiệm đã chạy."""

    def __init__(
        self,
        db: Database,
        *,
        min_sample: int = DEFAULT_MIN_SAMPLE,
        min_effect: float = DEFAULT_MIN_EFFECT,
    ):
        self.db = db
        self.store = ResearchStore(db)
        self.memory = ResearchMemory(db)
        self.min_sample = int(min_sample)
        self.min_effect = float(min_effect)

    # ------------------------------------------------------------------
    def build(self, experiment_id: int) -> Dict[str, Any]:
        experiment = self.store.get_experiment(experiment_id)
        if experiment is None:
            raise ValueError(f"Không tìm thấy thí nghiệm {experiment_id}.")

        hypothesis = self._hypothesis(experiment.get("hypothesis_id"))
        variants = self._variant_results(experiment_id)
        alphas = self._alphas(experiment_id)
        outcome = self.memory.experiment_outcome(experiment_id)
        sharpes = [
            abs(value) for value in
            (_metric(row, "sharpe") for row in alphas) if value is not None
        ]

        conclusion = self._conclude(experiment, variants, sharpes)
        return {
            "experiment": {
                "id": experiment_id,
                "name": experiment.get("name"),
                "objective": experiment.get("objective"),
                "variable_changed": experiment.get("variable_changed"),
                "base_expression": experiment.get("base_expression"),
                "expected_effect": experiment.get("expected_effect"),
                "settings": experiment.get("settings"),
                "status": experiment.get("status"),
            },
            "hypothesis": hypothesis,
            "variants": [variant.as_dict() for variant in variants],
            "counts": {
                "alpha_count": outcome["total"],
                "simulated": outcome["simulated"],
                "generated": outcome["total"],
                "validated": outcome["total"] - outcome["invalid"],
                "invalid": outcome["invalid"],
                "passed": outcome["passed"],
                "rejected": outcome["rejected"],
                "failed": outcome["failed"],
                "robust": outcome["robust"],
                "candidates": outcome["candidates"],
            },
            "pass_rate": outcome["pass_rate"],
            "structural_diversity": self._diversity(alphas),
            "metrics": {
                "sharpe": describe(sharpes),
                "fitness": describe(
                    [value for value in
                     (_metric(row, "fitness") for row in alphas) if value is not None]
                ),
                "turnover": describe(
                    [value for value in
                     (_metric(row, "turnover") for row in alphas) if value is not None]
                ),
            },
            "best_alpha": self._best_alpha(alphas),
            "best_turnover": self._best_turnover(alphas),
            "correlation": self._correlation_summary(experiment_id),
            "robustness": self._robustness_summary(alphas),
            "conclusion": conclusion,
            "next_steps": self._next_steps(experiment, variants, conclusion),
        }

    # ------------------------------------------------------------------
    def _hypothesis(self, hypothesis_id: Optional[int]) -> Optional[Dict[str, Any]]:
        if not hypothesis_id:
            return None
        connection = self.db.connect()
        try:
            row = connection.execute(
                "SELECT * FROM hypotheses WHERE id = ?", (int(hypothesis_id),)
            ).fetchone()
        finally:
            connection.close()
        return dict(row) if row else None

    def _alphas(self, experiment_id: int) -> List[Dict[str, Any]]:
        connection = self.db.connect()
        try:
            return [
                dict(row) for row in connection.execute(
                    """
                    SELECT id, alpha_id, expression, status, evaluation_status,
                           score, metrics_json, robustness_json, variant_id,
                           self_correlation, prod_correlation
                      FROM alphas WHERE experiment_id = ?
                    """,
                    (int(experiment_id),),
                ).fetchall()
            ]
        finally:
            connection.close()

    def _variant_results(self, experiment_id: int) -> List[VariantResult]:
        """Gom alpha theo biến thể để so sánh đúng cái thí nghiệm đang khảo sát."""
        variants = self.store.list_variants(experiment_id)
        alphas = self._alphas(experiment_id)

        by_variant: Dict[Any, List[Dict[str, Any]]] = {}
        by_expression: Dict[str, List[Dict[str, Any]]] = {}
        for row in alphas:
            by_variant.setdefault(row.get("variant_id"), []).append(row)
            by_expression.setdefault(str(row.get("expression")), []).append(row)

        results = []
        for variant in variants:
            group = by_variant.get(variant["id"]) or by_expression.get(
                str(variant["expression"]), []
            )
            parameters = {
                key: value for key, value in (variant.get("parameters") or {}).items()
                if not key.startswith("_")
            }
            results.append(
                VariantResult(
                    label=variant["label"],
                    expression=variant["expression"],
                    parameters=parameters,
                    alpha_count=len(group),
                    passed=sum(
                        1 for row in group
                        if str(row.get("status")) in (
                            Status.PASSED, Status.CANDIDATE, Status.HUMAN_REVIEW,
                            Status.SUBMITTED,
                        )
                    ),
                    median_sharpe=_median(group, "sharpe"),
                    best_sharpe=_best(group, "sharpe"),
                    median_fitness=_median(group, "fitness"),
                    median_turnover=_median(group, "turnover"),
                )
            )
        return results

    def _diversity(self, alphas: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Độ đa dạng cấu trúc của lô alpha.

        Bốn mươi alpha thuộc một họ duy nhất khảo sát ít hơn hẳn bốn mươi alpha
        trải trên mười họ, dù con số tổng giống nhau.
        """
        from ..history.fingerprint import fingerprint as _fp

        exact, families, templates = set(), set(), set()
        for row in alphas:
            expression = row.get("expression")
            if not expression:
                continue
            try:
                meta = _fp(expression)
            except Exception:
                continue
            exact.add(meta["exact"])
            families.add(meta["family"])
            templates.add(meta["template"])
        total = len(alphas)
        return {
            "alphas": total,
            "distinct_expressions": len(exact),
            "distinct_families": len(families),
            "distinct_templates": len(templates),
            # Tỷ lệ họ trên tổng: 1.0 nghĩa là mỗi alpha một ý tưởng riêng,
            # gần 0 nghĩa là cả lô chỉ khảo sát một ý tưởng.
            "family_ratio": round(len(families) / total, 4) if total else 0.0,
        }

    def _best_alpha(self, alphas: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        scored = [row for row in alphas if _metric(row, "sharpe") is not None]
        if not scored:
            return None
        best = max(scored, key=lambda row: abs(_metric(row, "sharpe")))
        return {
            "alpha_id": best.get("alpha_id"),
            "expression": best.get("expression"),
            "sharpe": _metric(best, "sharpe"),
            "fitness": _metric(best, "fitness"),
            "turnover": _metric(best, "turnover"),
            "score": best.get("score"),
            "status": best.get("status"),
        }

    def _best_turnover(self, alphas: List[Dict[str, Any]]) -> Optional[float]:
        """Vòng quay thấp nhất trong nhóm đạt, vì vòng quay thấp giảm chi phí."""
        values = [
            _metric(row, "turnover") for row in alphas
            if str(row.get("status")) in (Status.PASSED, Status.CANDIDATE)
            and _metric(row, "turnover") is not None
        ]
        return round(min(values), 6) if values else None

    def _correlation_summary(self, experiment_id: int) -> Dict[str, Any]:
        connection = self.db.connect()
        try:
            rows = [
                dict(row) for row in connection.execute(
                    """
                    SELECT c.correlation_type, c.correlation_value, c.status,
                           c.threshold
                      FROM correlation_results c
                      JOIN alphas a ON a.id = c.alpha_row_id
                     WHERE a.experiment_id = ?
                    """,
                    (int(experiment_id),),
                ).fetchall()
            ]
        finally:
            connection.close()
        by_type: Dict[str, List[float]] = {}
        for row in rows:
            value = row.get("correlation_value")
            if value is not None:
                by_type.setdefault(str(row["correlation_type"]), []).append(float(value))
        return {
            "checks_run": len(rows),
            "failed": sum(1 for row in rows if str(row.get("status")) == "FAILED"),
            "max_by_type": {
                key: round(max(values, key=abs), 4) for key, values in by_type.items()
            },
        }

    def _robustness_summary(self, alphas: List[Dict[str, Any]]) -> Dict[str, Any]:
        import json

        evaluated = 0
        passed = 0
        failures: Dict[str, int] = {}
        for row in alphas:
            try:
                report = json.loads(row.get("robustness_json") or "{}")
            except (TypeError, ValueError):
                continue
            if not report:
                continue
            evaluated += 1
            if report.get("passed"):
                passed += 1
            for name in report.get("failures", []):
                failures[name] = failures.get(name, 0) + 1
        return {
            "evaluated": evaluated,
            "passed": passed,
            "failures_by_check": failures,
        }

    # ------------------------------------------------------------------
    def _conclude(
        self,
        experiment: Dict[str, Any],
        variants: List[VariantResult],
        sharpes: List[float],
    ) -> Dict[str, Any]:
        """Rút kết luận, và từ chối kết luận khi bằng chứng chưa đủ."""
        sample = len(sharpes)
        variable = experiment.get("variable_changed") or "biến khảo sát"

        if sample < self.min_sample:
            return {
                "verdict": CONCLUSION_INSUFFICIENT,
                "summary": (
                    f"Bằng chứng chưa đủ. Mới có {sample} alpha có chỉ số, "
                    f"cần tối thiểu {self.min_sample} để kết luận về ảnh hưởng của {variable}."
                ),
                "sample_size": sample,
                "required_sample": self.min_sample,
                "evidence": EVIDENCE_INSUFFICIENT,
                "confidence": round(sample / self.min_sample, 4) if self.min_sample else 0.0,
            }

        measured = [
            variant for variant in variants
            if variant.median_sharpe is not None and variant.alpha_count > 0
        ]
        if len(measured) < 2:
            return {
                "verdict": CONCLUSION_INSUFFICIENT,
                "summary": (
                    "Bằng chứng chưa đủ. Cần ít nhất hai biến thể có kết quả "
                    "để so sánh, hiện chỉ có "
                    f"{len(measured)}."
                ),
                "sample_size": sample,
                "required_sample": self.min_sample,
                "evidence": EVIDENCE_INSUFFICIENT,
                "confidence": 0.0,
            }

        best = max(measured, key=lambda variant: variant.median_sharpe)
        worst = min(measured, key=lambda variant: variant.median_sharpe)
        baseline = abs(worst.median_sharpe) or 1e-9
        effect = (best.median_sharpe - worst.median_sharpe) / baseline

        if abs(effect) < self.min_effect:
            return {
                "verdict": CONCLUSION_INCONCLUSIVE,
                "summary": (
                    f"Không thấy hiệu ứng rõ rệt của {variable}. Chênh lệch giữa "
                    f"biến thể tốt nhất và kém nhất chỉ {effect * 100:.1f} phần trăm, "
                    f"dưới ngưỡng {self.min_effect * 100:.0f} phần trăm."
                ),
                "sample_size": sample,
                "effect_size": round(effect, 4),
                "best_variant": best.label,
                "evidence": evidence_level(sample, self.min_sample),
                "confidence": round(min(1.0, sample / (self.min_sample * 2)), 4),
            }

        expected = str(experiment.get("expected_effect") or "").strip().lower()
        verdict = CONCLUSION_SUPPORTS
        note = ""
        if expected:
            # Chỉ đối chiếu được khi kỳ vọng nêu rõ biến thể hay chiều nào.
            if best.label.lower() in expected or str(
                list(best.parameters.values())[0] if best.parameters else ""
            ).lower() in expected:
                verdict = CONCLUSION_SUPPORTS
            else:
                verdict = CONCLUSION_CONTRADICTS
                note = (
                    f" Kỳ vọng ghi là \"{experiment.get('expected_effect')}\" "
                    f"nhưng biến thể tốt nhất là {best.label}."
                )

        return {
            "verdict": verdict,
            "summary": (
                f"Biến thể {best.label} cho Sharpe trung vị {best.median_sharpe:.3f}, "
                f"cao hơn {worst.label} ({worst.median_sharpe:.3f}) khoảng "
                f"{effect * 100:.1f} phần trăm trên {sample} alpha." + note
            ),
            "sample_size": sample,
            "effect_size": round(effect, 4),
            "best_variant": best.label,
            "worst_variant": worst.label,
            "evidence": evidence_level(sample, self.min_sample),
            "confidence": round(min(1.0, sample / (self.min_sample * 2)), 4),
        }

    def _next_steps(
        self,
        experiment: Dict[str, Any],
        variants: List[VariantResult],
        conclusion: Dict[str, Any],
    ) -> List[str]:
        """Đề xuất bước nghiên cứu tiếp theo, bám vào kết luận vừa rút."""
        verdict = conclusion.get("verdict")
        variable = experiment.get("variable_changed") or "biến khảo sát"
        steps: List[str] = []

        if verdict == CONCLUSION_INSUFFICIENT:
            needed = conclusion.get("required_sample", self.min_sample) - conclusion.get(
                "sample_size", 0
            )
            steps.append(
                f"Chạy thêm khoảng {max(needed, 1)} alpha nữa cho cùng thiết kế "
                "trước khi rút bất kỳ kết luận nào."
            )
            steps.append(
                "Không mở rộng sang biến khác khi biến hiện tại chưa kết luận được."
            )
            return steps

        if verdict == CONCLUSION_INCONCLUSIVE:
            steps.append(
                f"Mở rộng khoảng giá trị của {variable}: các giá trị đang thử có thể "
                "quá gần nhau để lộ ra khác biệt."
            )
            steps.append("Cân nhắc chuyển sang khảo sát một biến thiết kế khác.")
            return steps

        # best_variant đã là nhãn dạng "lookback=250" nên không thêm tiền tố nữa.
        best = conclusion.get("best_variant")
        steps.append(
            f"Giữ {best} làm mốc, rồi khảo sát một biến thiết kế khác trên nền đó."
        )
        steps.append(
            "Kiểm tra biến thể tốt nhất ở khu vực hoặc universe khác để biết kết quả "
            "có tổng quát không hay chỉ đúng ở điều kiện đã thử."
        )
        if conclusion.get("confidence", 0.0) < 1.0:
            steps.append(
                "Cỡ mẫu mới ở mức tối thiểu, nên coi kết luận này là tạm thời."
            )
        return steps

    # ------------------------------------------------------------------
    def render_text(self, experiment_id: int) -> str:
        """Bản báo cáo dạng văn bản, đủ đọc trên màn hình dòng lệnh."""
        report = self.build(experiment_id)
        experiment = report["experiment"]
        lines = [
            f"Thí nghiệm {experiment['id']}: {experiment['name']}",
            f"Biến khảo sát: {experiment['variable_changed']}",
            f"Biểu thức gốc: {experiment['base_expression']}",
        ]
        if report["hypothesis"]:
            lines.append(f"Giả thuyết: {report['hypothesis']['statement']}")

        counts = report["counts"]
        lines += [
            "",
            f"Alpha: {counts['alpha_count']} tổng, {counts['passed']} đạt, "
            f"{counts['rejected']} bị loại, {counts['invalid']} không hợp lệ",
            f"Tỷ lệ đạt: {report['pass_rate'] * 100:.1f} phần trăm",
        ]
        sharpe = report["metrics"]["sharpe"]
        if sharpe["count"]:
            lines.append(
                f"Sharpe: trung vị {sharpe['median']}, phân vị 25 {sharpe['p25']}, "
                f"phân vị 75 {sharpe['p75']}, cỡ mẫu {sharpe['count']}"
            )

        lines.append("")
        lines.append("Biến thể:")
        for variant in report["variants"]:
            lines.append(
                f"  {variant['label']:<20} n={variant['alpha_count']:<4} "
                f"đạt={variant['passed']:<3} "
                f"Sharpe trung vị={variant['median_sharpe']}"
            )

        diversity = report["structural_diversity"]
        lines.append(
            f"Đa dạng cấu trúc: {diversity['distinct_families']} họ trên "
            f"{diversity['alphas']} alpha (tỷ lệ {diversity['family_ratio']})"
        )

        conclusion = report["conclusion"]
        lines += [
            "",
            f"Kết luận [{conclusion['verdict']}] "
            f"— mức bằng chứng: {conclusion.get('evidence', EVIDENCE_INSUFFICIENT)}:",
            f"  {conclusion['summary']}",
            "",
            "Bước tiếp theo:",
        ]
        lines.extend(f"  - {step}" for step in report["next_steps"])
        return "\n".join(lines)


# ----------------------------------------------------------------------
def _metric(row: Dict[str, Any], key: str) -> Optional[float]:
    import json

    try:
        metrics = json.loads(row.get("metrics_json") or "{}")
    except (TypeError, ValueError):
        return None
    try:
        return float(metrics.get(key))
    except (TypeError, ValueError):
        return None


def _median(rows: List[Dict[str, Any]], key: str) -> Optional[float]:
    values = [value for value in (_metric(row, key) for row in rows) if value is not None]
    return round(statistics.median(values), 6) if values else None


def _best(rows: List[Dict[str, Any]], key: str) -> Optional[float]:
    values = [value for value in (_metric(row, key) for row in rows) if value is not None]
    return round(max(values, key=abs), 6) if values else None
