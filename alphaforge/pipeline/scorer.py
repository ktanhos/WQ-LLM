"""Chấm điểm và lọc alpha theo ngưỡng cấu hình.

Bộ chấm điểm chỉ đọc chỉ số đã lưu, không gọi mạng, nên chạy lại được nhiều lần
sau khi thay đổi ngưỡng mà không tốn thêm hạn mức mô phỏng.

Điểm tổng hợp chỉ dùng để xếp thứ tự ưu tiên xem xét, không phải kết luận về
chất lượng alpha. Quyết định nộp vẫn thuộc về người nghiên cứu.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ScoreResult:
    passed: bool
    score: float
    reasons: List[str] = field(default_factory=list)

    @property
    def reason_text(self) -> str:
        return "; ".join(self.reasons)


class Scorer:
    def __init__(self, config: Dict[str, Any]):
        self.config = config or {}
        self.weights = self.config.get("weights", {}) or {}

    # ------------------------------------------------------------------
    def evaluate(self, metrics: Optional[Dict[str, Any]]) -> ScoreResult:
        if not metrics:
            return ScoreResult(False, 0.0, ["Không có chỉ số để chấm."])

        reasons: List[str] = []
        sharpe = _as_float(metrics.get("sharpe"))
        fitness = _as_float(metrics.get("fitness"))
        turnover = _as_float(metrics.get("turnover"))
        drawdown = _as_float(metrics.get("drawdown"))
        margin_bps = _as_float(metrics.get("marginBps"))
        if margin_bps is None:
            margin = _as_float(metrics.get("margin"))
            margin_bps = margin * 10000.0 if margin is not None else None
        long_count = _as_float(metrics.get("longCount"))
        short_count = _as_float(metrics.get("shortCount"))

        # Sharpe âm vẫn có giá trị nếu đảo dấu biểu thức, nên so sánh theo trị tuyệt đối
        # và ghi chú lại để người nghiên cứu biết cần đảo dấu.
        abs_sharpe = abs(sharpe) if sharpe is not None else None
        min_sharpe = _as_float(self.config.get("min_sharpe"))
        if min_sharpe is not None:
            if abs_sharpe is None:
                reasons.append("Thiếu chỉ số Sharpe.")
            elif abs_sharpe < min_sharpe:
                reasons.append(f"Sharpe {abs_sharpe:.3f} dưới ngưỡng {min_sharpe:.3f}.")
        if sharpe is not None and sharpe < 0:
            reasons.append("Sharpe âm, cần đảo dấu biểu thức trước khi xem xét.")

        min_fitness = _as_float(self.config.get("min_fitness"))
        abs_fitness = abs(fitness) if fitness is not None else None
        if min_fitness is not None:
            if abs_fitness is None:
                reasons.append("Thiếu chỉ số Fitness.")
            elif abs_fitness < min_fitness:
                reasons.append(
                    f"Fitness {abs_fitness:.3f} dưới ngưỡng {min_fitness:.3f}."
                )

        min_turnover = _as_float(self.config.get("min_turnover"))
        max_turnover = _as_float(self.config.get("max_turnover"))
        if turnover is not None:
            if min_turnover is not None and turnover < min_turnover:
                reasons.append(
                    f"Turnover {turnover:.3f} thấp hơn mức tối thiểu {min_turnover:.3f}."
                )
            if max_turnover is not None and turnover > max_turnover:
                reasons.append(
                    f"Turnover {turnover:.3f} vượt mức tối đa {max_turnover:.3f}."
                )

        max_drawdown = _as_float(self.config.get("max_drawdown"))
        if drawdown is not None and max_drawdown is not None and drawdown > max_drawdown:
            reasons.append(
                f"Drawdown {drawdown:.3f} vượt mức tối đa {max_drawdown:.3f}."
            )

        min_margin = _as_float(self.config.get("min_margin_bps"))
        if margin_bps is not None and min_margin is not None and abs(margin_bps) < min_margin:
            reasons.append(
                f"Margin {margin_bps:.2f} điểm cơ bản dưới ngưỡng {min_margin:.2f}."
            )

        min_long = _as_float(self.config.get("min_long_count"))
        if long_count is not None and min_long is not None and long_count < min_long:
            reasons.append(f"Số vị thế mua {long_count:.0f} dưới ngưỡng {min_long:.0f}.")

        min_short = _as_float(self.config.get("min_short_count"))
        if short_count is not None and min_short is not None and short_count < min_short:
            reasons.append(f"Số vị thế bán {short_count:.0f} dưới ngưỡng {min_short:.0f}.")

        failures = metrics.get("checkFailures") or []
        if self.config.get("require_pass_all_checks") and failures:
            reasons.append("Không đạt kiểm tra: " + ", ".join(failures) + ".")

        score = self.compute_score(metrics)
        return ScoreResult(passed=not reasons, score=score, reasons=reasons)

    # ------------------------------------------------------------------
    def compute_score(self, metrics: Dict[str, Any]) -> float:
        """Điểm tổng hợp có trọng số, dùng để xếp thứ tự ưu tiên xem xét."""
        sharpe = abs(_as_float(metrics.get("sharpe")) or 0.0)
        fitness = abs(_as_float(metrics.get("fitness")) or 0.0)
        margin_bps = _as_float(metrics.get("marginBps"))
        if margin_bps is None:
            margin = _as_float(metrics.get("margin")) or 0.0
            margin_bps = margin * 10000.0
        turnover = _as_float(metrics.get("turnover")) or 0.0

        w = self.weights
        max_turnover = _as_float(self.config.get("max_turnover")) or 0.7
        turnover_excess = max(0.0, turnover - max_turnover) / max(max_turnover, 1e-6)

        score = (
            float(w.get("sharpe", 0.45)) * sharpe
            + float(w.get("fitness", 0.35)) * fitness
            + float(w.get("margin", 0.10)) * (abs(margin_bps) / 10.0)
            - float(w.get("turnover_penalty", 0.10)) * turnover_excess
        )
        return round(score, 6)


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
