"""Kiểm tra độ bền của một alpha từ dữ liệu đã lưu.

Một alpha có Sharpe cao nhưng toàn bộ lợi nhuận đến từ một năm, hoặc sụp đổ
khi đổi nhẹ tham số, thì con số đó không phản ánh một quy luật thị trường mà
phản ánh một sự trùng hợp. Lớp này tìm những trường hợp đó.

Mọi phép kiểm ở đây chạy trên chỉ số **đã lưu trong kho**, không gọi mạng và
không chạy thêm mô phỏng nào. Điều đó giới hạn những gì kiểm được: phép kiểm
nào cần dữ liệu mà máy chủ không trả về sẽ báo `unavailable` thay vì đoán bừa.
Báo thiếu dữ liệu trung thực hơn là bịa ra một kết luận.

Ba hồ sơ có sẵn:

    standard  các phép kiểm rẻ, chạy được với dữ liệu thông thường
    strict    thêm phép kiểm về tập trung lợi nhuận và độ nhạy tham số
    custom    thí nghiệm tự chọn danh sách phép kiểm
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ..storage.db import maybe_float as _as_float

#: Tên các phép kiểm.
CHECK_YEAR_BY_YEAR = "year_by_year"
CHECK_PARAMETER_SENSITIVITY = "parameter_sensitivity"
CHECK_DECAY_SENSITIVITY = "decay_sensitivity"
CHECK_TURNOVER_STABILITY = "turnover_stability"
CHECK_DRAWDOWN = "drawdown"
CHECK_RETURN_CONCENTRATION = "return_concentration"

ALL_CHECKS = (
    CHECK_YEAR_BY_YEAR,
    CHECK_PARAMETER_SENSITIVITY,
    CHECK_DECAY_SENSITIVITY,
    CHECK_TURNOVER_STABILITY,
    CHECK_DRAWDOWN,
    CHECK_RETURN_CONCENTRATION,
)

PROFILES: Dict[str, Sequence[str]] = {
    "standard": (CHECK_YEAR_BY_YEAR, CHECK_DRAWDOWN, CHECK_TURNOVER_STABILITY),
    "strict": ALL_CHECKS,
}

#: Kết quả một phép kiểm.
PASS = "pass"
FAIL = "fail"
UNAVAILABLE = "unavailable"


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str = ""
    value: Optional[float] = None

    @property
    def passed(self) -> bool:
        return self.status == PASS

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "status": self.status,
            "detail": self.detail, "value": self.value,
        }


@dataclass
class RobustnessReport:
    profile: str
    checks: List[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Đạt khi không phép kiểm nào thất bại.

        Phép kiểm thiếu dữ liệu không tính là thất bại, nhưng được ghi lại để
        người đọc biết kết luận này dựa trên bằng chứng tới đâu.
        """
        return not any(check.status == FAIL for check in self.checks)

    @property
    def coverage(self) -> float:
        """Tỷ lệ phép kiểm thực sự chạy được."""
        if not self.checks:
            return 0.0
        ran = sum(1 for check in self.checks if check.status != UNAVAILABLE)
        return round(ran / len(self.checks), 4)

    def failures(self) -> List[str]:
        return [check.name for check in self.checks if check.status == FAIL]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "profile": self.profile,
            "passed": self.passed,
            "coverage": self.coverage,
            "failures": self.failures(),
            "checks": [check.as_dict() for check in self.checks],
        }


@dataclass
class RobustnessThresholds:
    """Ngưỡng cho từng phép kiểm. Cấu hình được để hợp với từng khu vực."""

    max_drawdown: float = 0.50
    #: Tỷ lệ năm phải dương tối thiểu.
    min_positive_year_ratio: float = 0.6
    #: Chênh lệch vòng quay cho phép giữa các giai đoạn.
    max_turnover_spread: float = 0.30
    #: Sharpe không được giảm quá tỷ lệ này khi đổi tham số.
    max_sensitivity_drop: float = 0.50
    #: Một năm không được đóng góp quá tỷ lệ này trong tổng lợi nhuận.
    max_year_contribution: float = 0.70


class RobustnessChecker:
    """Chạy các phép kiểm độ bền trên chỉ số đã lưu."""

    def __init__(
        self,
        thresholds: Optional[RobustnessThresholds] = None,
        profile: str = "standard",
        checks: Optional[Sequence[str]] = None,
    ):
        self.thresholds = thresholds or RobustnessThresholds()
        self.profile = profile
        if checks is not None:
            self.checks = tuple(checks)
            self.profile = "custom"
        else:
            self.checks = tuple(PROFILES.get(profile, PROFILES["standard"]))

    # ------------------------------------------------------------------
    def evaluate(
        self,
        metrics: Dict[str, Any],
        *,
        variants: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> RobustnessReport:
        """Chấm độ bền của một alpha.

        `variants` là chỉ số của các biến thể cùng cấu trúc khác tham số, dùng
        cho phép kiểm độ nhạy. Không có thì phép kiểm đó báo thiếu dữ liệu.
        """
        metrics = metrics or {}
        report = RobustnessReport(profile=self.profile)
        runners = {
            CHECK_YEAR_BY_YEAR: lambda: self._year_by_year(metrics),
            CHECK_DRAWDOWN: lambda: self._drawdown(metrics),
            CHECK_TURNOVER_STABILITY: lambda: self._turnover(metrics),
            CHECK_RETURN_CONCENTRATION: lambda: self._concentration(metrics),
            CHECK_PARAMETER_SENSITIVITY: lambda: self._sensitivity(
                metrics, variants, CHECK_PARAMETER_SENSITIVITY, "tham số"
            ),
            CHECK_DECAY_SENSITIVITY: lambda: self._sensitivity(
                metrics, variants, CHECK_DECAY_SENSITIVITY, "decay", key="decay"
            ),
        }
        for name in self.checks:
            runner = runners.get(name)
            if runner is None:
                continue
            report.checks.append(runner())
        return report

    # ------------------------------------------------------------------
    def _year_by_year(self, metrics: Dict[str, Any]) -> CheckResult:
        """Lợi nhuận phải trải đều qua nhiều năm, không dồn vào một năm."""
        yearly = _yearly_values(metrics)
        if not yearly:
            return CheckResult(
                CHECK_YEAR_BY_YEAR, UNAVAILABLE,
                "Máy chủ không trả về phân rã theo năm.",
            )
        positive = sum(1 for value in yearly if value > 0)
        ratio = positive / len(yearly)
        threshold = self.thresholds.min_positive_year_ratio
        if ratio < threshold:
            return CheckResult(
                CHECK_YEAR_BY_YEAR, FAIL,
                f"Chỉ {positive}/{len(yearly)} năm dương, dưới ngưỡng {threshold:.0%}.",
                round(ratio, 4),
            )
        return CheckResult(
            CHECK_YEAR_BY_YEAR, PASS,
            f"{positive}/{len(yearly)} năm dương.", round(ratio, 4),
        )

    def _drawdown(self, metrics: Dict[str, Any]) -> CheckResult:
        value = _as_float(metrics.get("drawdown"))
        if value is None:
            return CheckResult(CHECK_DRAWDOWN, UNAVAILABLE, "Không có chỉ số drawdown.")
        limit = self.thresholds.max_drawdown
        if abs(value) > limit:
            return CheckResult(
                CHECK_DRAWDOWN, FAIL,
                f"Drawdown {abs(value):.3f} vượt ngưỡng {limit:.3f}.", abs(value),
            )
        return CheckResult(
            CHECK_DRAWDOWN, PASS, f"Drawdown {abs(value):.3f}.", abs(value)
        )

    def _turnover(self, metrics: Dict[str, Any]) -> CheckResult:
        """Vòng quay phải ổn định, không nhảy giữa các giai đoạn."""
        series = _numeric_series(metrics, ("turnoverByYear", "turnover_by_year"))
        if not series:
            value = _as_float(metrics.get("turnover"))
            if value is None:
                return CheckResult(
                    CHECK_TURNOVER_STABILITY, UNAVAILABLE, "Không có dữ liệu vòng quay."
                )
            # Chỉ có một con số thì không đánh giá được độ ổn định.
            return CheckResult(
                CHECK_TURNOVER_STABILITY, UNAVAILABLE,
                "Chỉ có vòng quay tổng, không có phân rã theo giai đoạn.",
                value,
            )
        spread = max(series) - min(series)
        limit = self.thresholds.max_turnover_spread
        if spread > limit:
            return CheckResult(
                CHECK_TURNOVER_STABILITY, FAIL,
                f"Vòng quay dao động {spread:.3f}, vượt ngưỡng {limit:.3f}.", spread,
            )
        return CheckResult(
            CHECK_TURNOVER_STABILITY, PASS, f"Dao động {spread:.3f}.", spread
        )

    def _concentration(self, metrics: Dict[str, Any]) -> CheckResult:
        """Một năm không được chiếm gần hết lợi nhuận."""
        yearly = [value for value in _yearly_values(metrics) if value > 0]
        if not yearly:
            return CheckResult(
                CHECK_RETURN_CONCENTRATION, UNAVAILABLE,
                "Không có phân rã lợi nhuận theo năm.",
            )
        total = sum(yearly)
        if total <= 0:
            return CheckResult(
                CHECK_RETURN_CONCENTRATION, FAIL, "Tổng lợi nhuận không dương."
            )
        share = max(yearly) / total
        limit = self.thresholds.max_year_contribution
        if share > limit:
            return CheckResult(
                CHECK_RETURN_CONCENTRATION, FAIL,
                f"Một năm đóng góp {share:.0%} tổng lợi nhuận, vượt ngưỡng {limit:.0%}.",
                round(share, 4),
            )
        return CheckResult(
            CHECK_RETURN_CONCENTRATION, PASS,
            f"Năm lớn nhất đóng góp {share:.0%}.", round(share, 4),
        )

    def _sensitivity(
        self,
        metrics: Dict[str, Any],
        variants: Optional[Sequence[Dict[str, Any]]],
        name: str,
        label: str,
        key: Optional[str] = None,
    ) -> CheckResult:
        """Sharpe không được sụp khi đổi nhẹ tham số.

        Cần chỉ số của các biến thể. Đây chính là lý do nên chạy thí nghiệm có
        kiểm soát: nó sinh ra sẵn dữ liệu cho phép kiểm này.
        """
        if not variants:
            return CheckResult(
                name, UNAVAILABLE,
                f"Cần chỉ số của biến thể để đánh giá độ nhạy {label}.",
            )
        base = _as_float(metrics.get("sharpe"))
        if base is None:
            return CheckResult(name, UNAVAILABLE, "Alpha gốc không có Sharpe.")

        values = [
            _as_float(variant.get("sharpe")) for variant in variants
            if _as_float(variant.get("sharpe")) is not None
        ]
        if not values:
            return CheckResult(name, UNAVAILABLE, "Biến thể không có Sharpe.")

        median = statistics.median([abs(value) for value in values])
        base_abs = abs(base)
        if base_abs <= 0:
            return CheckResult(name, UNAVAILABLE, "Sharpe gốc bằng không.")
        drop = max(0.0, (base_abs - median) / base_abs)
        limit = self.thresholds.max_sensitivity_drop
        if drop > limit:
            return CheckResult(
                name, FAIL,
                f"Sharpe trung vị của biến thể thấp hơn gốc {drop:.0%}, "
                f"vượt ngưỡng {limit:.0%}. Kết quả phụ thuộc mạnh vào {label}.",
                round(drop, 4),
            )
        return CheckResult(
            name, PASS,
            f"Biến thể giữ được Sharpe, chênh {drop:.0%}.", round(drop, 4),
        )


# ----------------------------------------------------------------------
def _yearly_values(metrics: Dict[str, Any]) -> List[float]:
    """Rút dãy lợi nhuận theo năm từ nhiều dạng khóa mà máy chủ có thể dùng."""
    for key in ("pnlByYear", "returnsByYear", "yearlyReturns", "yearly_returns"):
        series = _numeric_series(metrics, (key,))
        if series:
            return series
    return []


def _numeric_series(metrics: Dict[str, Any], keys: Iterable[str]) -> List[float]:
    for key in keys:
        raw = metrics.get(key)
        if isinstance(raw, dict):
            values = [_as_float(value) for value in raw.values()]
        elif isinstance(raw, (list, tuple)):
            values = [_as_float(value) for value in raw]
        else:
            continue
        cleaned = [value for value in values if value is not None]
        if cleaned:
            return cleaned
    return []


