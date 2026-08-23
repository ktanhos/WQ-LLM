"""Phân tích lịch sử nghiên cứu để tìm khoảng trống và cấu trúc đã bão hòa.

Phân phối chỉ số alpha có đuôi dày. Một alpha Sharpe 6 lọt vào nhóm mười alpha
sẽ kéo trung bình lên tới mức không mô tả đúng bất kỳ alpha nào trong nhóm. Vì
vậy mọi kết luận ở đây dựa trên trung vị và phân vị, còn trung bình chỉ được
giữ lại để đối chiếu.

Kèm theo mỗi con số luôn có cỡ mẫu. Một họ cấu trúc có trung vị Sharpe 2.4 trên
hai alpha không nói lên điều gì; cùng con số đó trên tám mươi alpha lại là một
phát hiện. Không có cỡ mẫu thì hai trường hợp trông giống hệt nhau.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Sequence

#: Số alpha tối thiểu để một họ cấu trúc được xếp hạng thay vì chỉ được liệt kê.
MIN_SAMPLE_FOR_RANKING = 5
#: Dưới ngưỡng này thì một họ được coi là chưa khai thác đủ.
UNDEREXPLORED_THRESHOLD = 5
#: Trên ngưỡng này thì một họ được coi là đã thử nhiều.
SATURATED_THRESHOLD = 25

#: Trạng thái được coi là alpha đạt. BRAIN dùng nhiều nhãn khác nhau tùy điểm cuối.
PASSED_STATUSES = frozenset({"PASSED", "PASS", "ACTIVE", "SUBMITTED", "IS"})
FAILED_STATUSES = frozenset({"FAILED", "FAIL", "ERROR"})
REJECTED_STATUSES = frozenset({"REJECTED", "REJECT", "DECOMMISSIONED", "UNSUBMITTED"})


# ----------------------------------------------------------------------
# Thống kê cơ bản
# ----------------------------------------------------------------------
def describe(values: Sequence[float]) -> Dict[str, Any]:
    """Mô tả một dãy số bằng trung vị và phân vị, kèm cỡ mẫu.

    Trả về cấu trúc rỗng nhất quán khi không có dữ liệu để nơi gọi không phải
    kiểm tra None ở mọi khóa.
    """
    numbers = [float(value) for value in values if value is not None]
    if not numbers:
        return {
            "count": 0, "mean": None, "median": None, "p25": None, "p75": None,
            "min": None, "max": None, "stdev": None,
        }
    ordered = sorted(numbers)
    return {
        "count": len(ordered),
        "mean": round(statistics.fmean(ordered), 6),
        "median": round(statistics.median(ordered), 6),
        "p25": round(_quantile(ordered, 0.25), 6),
        "p75": round(_quantile(ordered, 0.75), 6),
        "min": round(ordered[0], 6),
        "max": round(ordered[-1], 6),
        "stdev": round(statistics.stdev(ordered), 6) if len(ordered) > 1 else 0.0,
    }


def _quantile(ordered: Sequence[float], fraction: float) -> float:
    """Phân vị theo nội suy tuyến tính, không phụ thuộc thư viện ngoài."""
    if len(ordered) == 1:
        return float(ordered[0])
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return float(ordered[lower]) * (1 - weight) + float(ordered[upper]) * weight


def _numeric(rows: Iterable[Dict[str, Any]], key: str) -> List[float]:
    values = []
    for row in rows:
        value = row.get(key)
        if value is None:
            continue
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            continue
    return values


def _json_list(raw: Any) -> List[str]:
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def classify_status(status: Any) -> str:
    """Quy nhiều nhãn trạng thái của máy chủ về bốn nhóm để đếm nhất quán."""
    text = str(status or "").upper()
    if text in PASSED_STATUSES:
        return "passed"
    if text in FAILED_STATUSES:
        return "failed"
    if text in REJECTED_STATUSES:
        return "rejected"
    return "other"


# ----------------------------------------------------------------------
# Tóm tắt tương thích ngược
# ----------------------------------------------------------------------
def summarize(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Tóm tắt ngắn gọn. Giữ nguyên khóa cũ, bổ sung trung vị và phân vị."""
    rows = list(rows)
    sharpe = _numeric(rows, "sharpe")
    fitness = _numeric(rows, "fitness")
    turnover = _numeric(rows, "turnover")
    return {
        "total": len(rows),
        "status_counts": dict(Counter(str(row.get("status")) for row in rows)),
        "region_counts": dict(Counter(str(row.get("region")) for row in rows)),
        "universe_counts": dict(Counter(str(row.get("universe")) for row in rows)),
        "average_sharpe": statistics.fmean(sharpe) if sharpe else None,
        "average_fitness": statistics.fmean(fitness) if fitness else None,
        "average_turnover": statistics.fmean(turnover) if turnover else None,
        "best_sharpe": max(sharpe, default=None),
        "best_fitness": max(fitness, default=None),
        "median_sharpe": statistics.median(sharpe) if sharpe else None,
        "median_fitness": statistics.median(fitness) if fitness else None,
        "median_turnover": statistics.median(turnover) if turnover else None,
        "sharpe": describe(sharpe),
        "fitness": describe(fitness),
        "turnover": describe(turnover),
    }


# ----------------------------------------------------------------------
# Phân tích đầy đủ
# ----------------------------------------------------------------------
def _group_stats(
    rows: Sequence[Dict[str, Any]], key_fn, *, label: str
) -> List[Dict[str, Any]]:
    """Gom nhóm theo một khóa rồi mô tả từng nhóm kèm tỷ lệ đạt.

    Tỷ lệ đạt tính trên toàn bộ alpha của nhóm, kể cả alpha hỏng. Nếu chỉ đếm
    alpha đạt thì mọi nhóm đều có tỷ lệ đạt bằng một, và kết luận rút ra sẽ
    mang thiên lệch sống sót.
    """
    buckets: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        for key in key_fn(row):
            buckets[key].append(row)

    output: List[Dict[str, Any]] = []
    for key, group in buckets.items():
        statuses = Counter(classify_status(row.get("status")) for row in group)
        total = len(group)
        output.append(
            {
                label: key,
                "count": total,
                "passed": statuses.get("passed", 0),
                "rejected": statuses.get("rejected", 0),
                "failed": statuses.get("failed", 0),
                "other": statuses.get("other", 0),
                "pass_rate": round(statuses.get("passed", 0) / total, 4) if total else 0.0,
                "sharpe": describe(_numeric(group, "sharpe")),
                "fitness": describe(_numeric(group, "fitness")),
                "turnover": describe(_numeric(group, "turnover")),
            }
        )
    output.sort(key=lambda item: (-item["count"], str(item[label])))
    return output


def analyze(
    rows: Iterable[Dict[str, Any]],
    *,
    min_sample: int = MIN_SAMPLE_FOR_RANKING,
    underexplored_threshold: int = UNDEREXPLORED_THRESHOLD,
    saturated_threshold: int = SATURATED_THRESHOLD,
) -> Dict[str, Any]:
    """Báo cáo nghiên cứu đầy đủ trên tập alpha lịch sử.

    Đầu vào là các hàng của bảng historical_alphas hoặc bất kỳ danh sách từ
    điển nào có cùng tên khóa.
    """
    rows = list(rows)
    status_groups = Counter(classify_status(row.get("status")) for row in rows)

    sharpe = _numeric(rows, "sharpe")
    fitness = _numeric(rows, "fitness")
    turnover = _numeric(rows, "turnover")

    families = _group_stats(
        rows, lambda row: [row.get("family") or row.get("fingerprint") or "unknown"],
        label="family",
    )
    templates = _group_stats(
        rows, lambda row: [row.get("template") or "unknown"], label="template"
    )
    operators = _group_stats(
        rows, lambda row: _json_list(row.get("operators_json")) or ["none"],
        label="operator",
    )
    fields = _group_stats(
        rows, lambda row: _json_list(row.get("fields_json")) or ["none"], label="field"
    )
    regions = _group_stats(rows, lambda row: [row.get("region") or "unknown"], label="region")
    universes = _group_stats(
        rows, lambda row: [row.get("universe") or "unknown"], label="universe"
    )
    delays = _group_stats(rows, lambda row: [row.get("delay")], label="delay")

    # Chỉ xếp hạng họ đủ cỡ mẫu. Họ hai alpha không đủ căn cứ để gọi là tốt.
    rankable = [item for item in families if item["count"] >= min_sample]
    by_median_sharpe = sorted(
        [item for item in rankable if item["sharpe"]["median"] is not None],
        key=lambda item: item["sharpe"]["median"],
        reverse=True,
    )
    # Hai nhóm phải rời nhau. Cắt mười đầu và mười cuối của cùng một danh sách
    # sẽ khiến một họ duy nhất xuất hiện ở cả "tốt nhất" lẫn "kém nhất" khi kho
    # còn ít họ, và bảng theo dõi khi đó tự mâu thuẫn với chính nó.
    # Làm tròn lên để một họ đủ cỡ mẫu duy nhất vẫn được xếp vào nhóm trên,
    # thay vì rơi xuống nhóm dưới chỉ vì không có họ nào khác để so.
    half = (len(by_median_sharpe) + 1) // 2
    high_performing = by_median_sharpe[:half][:10]
    low_performing = list(reversed(by_median_sharpe[half:]))[:10]

    windows_by_family: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        family = row.get("family") or row.get("fingerprint") or "unknown"
        entry = windows_by_family.setdefault(
            family, {"windows": Counter(), "best": None, "best_window": None}
        )
        row_windows = _json_list(row.get("windows_json"))
        entry["windows"].update(row_windows)
        row_sharpe = row.get("sharpe")
        if row_sharpe is not None and row_windows:
            try:
                value = float(row_sharpe)
            except (TypeError, ValueError):
                continue
            if entry["best"] is None or value > entry["best"]:
                entry["best"] = value
                entry["best_window"] = row_windows[0]

    return {
        "total": len(rows),
        "counts": {
            "passed": status_groups.get("passed", 0),
            "rejected": status_groups.get("rejected", 0),
            "failed": status_groups.get("failed", 0),
            "other": status_groups.get("other", 0),
        },
        "status_counts": dict(Counter(str(row.get("status")) for row in rows)),
        "pass_rate": round(status_groups.get("passed", 0) / len(rows), 4) if rows else 0.0,
        "metrics": {
            "sharpe": describe(sharpe),
            "fitness": describe(fitness),
            "turnover": describe(turnover),
            "returns": describe(_numeric(rows, "returns")),
            "margin": describe(_numeric(rows, "margin")),
        },
        "distribution": {
            "region": regions,
            "universe": universes,
            "delay": delays,
            "field": fields,
            "operator": operators,
            "family": families,
            "template": templates,
        },
        "high_performing_structures": high_performing,
        "low_performing_structures": low_performing,
        "frequently_tested_structures": [
            item for item in families if item["count"] >= saturated_threshold
        ][:20],
        "underexplored_structures": [
            item for item in families if item["count"] <= underexplored_threshold
        ][:20],
        "windows_by_family": {
            key: {
                "tested_windows": sorted(value["windows"], key=lambda w: int(w) if str(w).isdigit() else 0),
                "window_counts": dict(value["windows"]),
                "best_sharpe": value["best"],
                "best_window": value["best_window"],
            }
            for key, value in windows_by_family.items()
        },
        "notes": [
            "Trung vị và phân vị được ưu tiên hơn trung bình vì phân phối chỉ số có đuôi dày.",
            f"Chỉ họ cấu trúc có từ {min_sample} alpha trở lên mới được xếp hạng.",
            "Tỷ lệ đạt tính trên toàn bộ alpha kể cả alpha hỏng, nhằm tránh thiên lệch sống sót.",
        ],
    }


def research_gaps(
    analysis: Dict[str, Any], *, limit: int = 20
) -> List[Dict[str, Any]]:
    """Xếp hạng họ cấu trúc đáng khảo sát tiếp.

    Đây là góc nhìn hẹp, chỉ theo họ cấu trúc, giữ lại vì mã và bảng theo dõi
    hiện có đang dùng. Bản đầy đủ theo tám chiều nằm ở `research.gap.ResearchGap`.

    Điểm ưu tiên được tính bằng `research.priority.ResearchPriority`, cùng một
    công thức với phần còn lại của hệ thống. Trước đây chỗ này có công thức
    riêng, dẫn tới hai nơi trả về hai con số khác nhau cho cùng một họ.
    """
    # Nhập tại chỗ để lớp lịch sử không phụ thuộc lớp nghiên cứu lúc nạp mô đun.
    from ..research.priority import ResearchPriority

    engine = ResearchPriority()
    gaps: List[Dict[str, Any]] = []
    for item in analysis.get("distribution", {}).get("family", []):
        median_sharpe = item["sharpe"]["median"]
        if median_sharpe is None:
            continue
        score = engine.score(
            str(item["family"]),
            dimension="family",
            sample_size=item["count"],
            median_sharpe=median_sharpe,
            pass_rate=item.get("pass_rate", 0.0),
        )
        gaps.append(
            {
                "family": item["family"],
                "count": item["count"],
                "median_sharpe": median_sharpe,
                "pass_rate": item.get("pass_rate", 0.0),
                "priority": score.score,
                "reasons": score.reasons,
                "confidence": score.confidence,
            }
        )
    gaps.sort(key=lambda item: item["priority"], reverse=True)
    return gaps[:limit]
