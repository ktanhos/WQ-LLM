"""Vân tay cấu trúc cho biểu thức alpha.

Băm toàn bộ chuỗi biểu thức không trả lời được câu hỏi nghiên cứu thực sự.
Hai biểu thức chỉ khác cửa sổ thời gian là cùng một ý tưởng đã được thử với
tham số khác, không phải hai ý tưởng độc lập. Vì vậy mô đun này tạo ba mức
vân tay lồng nhau:

    exact     chuẩn hóa khoảng trắng và chữ hoa thường
              rank(ts_mean(returns, 20)) khác rank(ts_mean(returns, 60))

    family    trừu tượng hóa hằng số, giữ nguyên trường dữ liệu
              rank(ts_mean(returns, 20)) trùng rank(ts_mean(returns, 60))
              nhưng khác rank(ts_mean(volume, 20))

    template  trừu tượng hóa cả hằng số lẫn trường dữ liệu
              cả ba biểu thức trên cùng một template

Nhờ ba mức này có thể trả lời: cấu trúc đã thử chưa, đã thử cửa sổ nào, cửa sổ
nào cho kết quả tốt nhất, trường dữ liệu nào đã khai thác, tổ hợp toán tử nào
đang bị lặp nhiều.

Toán tử được nhận diện theo cú pháp: tên định danh đứng ngay trước dấu mở ngoặc.
Cách này không cần danh sách toán tử cố định nên không lỗi thời khi BRAIN bổ
sung toán tử mới.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence

#: Định danh, số thực, hoặc một ký tự dấu.
TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*|\d+(?:\.\d+)?|[^\sA-Za-z0-9_]")

#: Nhóm phân loại dùng cho các toán tử group_*. Đây là hằng của nền tảng,
#: không phải trường dữ liệu, nên không được tính vào tập trường.
GROUP_TOKENS = frozenset({
    "subindustry", "industry", "sector", "market", "country", "exchange",
    "bucket", "sparse", "densify",
})

#: Hằng và từ khóa không phải trường dữ liệu.
RESERVED_TOKENS = frozenset({
    "true", "false", "nan", "inf", "none", "null",
})

#: Toán tử làm trung tính hóa. Sự có mặt của chúng thay đổi bản chất tín hiệu.
NEUTRALIZE_OPERATORS = frozenset({
    "group_neutralize", "vector_neut", "regression_neut", "normalize",
})

#: Toán tử điều kiện giao dịch.
CONDITION_OPERATORS = frozenset({"trade_when", "if_else"})

#: Tiền tố toán tử chuỗi thời gian. Đối số số nguyên của chúng là cửa sổ nhìn lại.
_TIMESERIES_PREFIXES = ("ts_", "hump", "days_from", "last_diff")
_DECAY_HINT = "decay"


def _tokenize(text: str) -> List[str]:
    return TOKEN_RE.findall(text)


def _is_identifier(token: str) -> bool:
    return bool(token) and (token[0].isalpha() or token[0] == "_")


def _is_number(token: str) -> bool:
    return bool(token) and token.replace(".", "", 1).isdigit()


def _is_lookback_operator(name: str) -> bool:
    lowered = name.lower()
    return lowered.startswith(_TIMESERIES_PREFIXES) or _DECAY_HINT in lowered


class _Analysis:
    """Kết quả quét một lượt qua chuỗi token."""

    def __init__(self) -> None:
        self.operators: List[str] = []
        self.fields: List[str] = []
        self.groups: List[str] = []
        self.numbers: List[str] = []
        self.windows: List[int] = []
        self.keyword_args: List[str] = []
        self.max_depth: int = 0


def _analyse(tokens: Sequence[str]) -> _Analysis:
    """Quét token một lượt, suy ra vai trò của từng định danh và từng hằng số.

    Dùng ngăn xếp khung lời gọi thay vì phân tích cú pháp đầy đủ. Với cú pháp
    FASTEXPR chỉ gồm lời gọi hàm lồng nhau và toán tử trung tố, ngăn xếp là đủ
    để biết mỗi hằng số thuộc lời gọi nào và ở vị trí đối số thứ mấy.
    """
    analysis = _Analysis()
    # Mỗi khung: [tên toán tử, chỉ số đối số hiện tại]
    stack: List[List[Any]] = []

    index = 0
    total = len(tokens)
    while index < total:
        token = tokens[index]
        following = tokens[index + 1] if index + 1 < total else ""

        if _is_identifier(token):
            lowered = token.lower()
            if following == "(":
                # Định danh đứng trước dấu mở ngoặc là một toán tử.
                analysis.operators.append(lowered)
                stack.append([lowered, 0])
                analysis.max_depth = max(analysis.max_depth, len(stack))
                index += 2
                continue
            if following == "=":
                # Tên đối số theo khóa, ví dụ std trong winsorize(x, std=4).
                analysis.keyword_args.append(lowered)
                index += 2
                continue
            if lowered in GROUP_TOKENS:
                analysis.groups.append(lowered)
            elif lowered not in RESERVED_TOKENS:
                analysis.fields.append(lowered)
            index += 1
            continue

        if _is_number(token):
            analysis.numbers.append(token)
            # Hằng số nguyên nằm trong toán tử chuỗi thời gian là cửa sổ nhìn lại.
            previous = tokens[index - 1] if index else ""
            is_keyword_value = previous == "="
            if stack and not is_keyword_value:
                operator_name = str(stack[-1][0])
                if _is_lookback_operator(operator_name) and token.isdigit():
                    window = int(token)
                    # Loại 0 và 1: đó là cờ chế độ, không phải cửa sổ.
                    if window > 1:
                        analysis.windows.append(window)
            index += 1
            continue

        if token == "(":
            # Ngoặc nhóm, không phải lời gọi. Đẩy khung ẩn danh để cân bằng.
            stack.append(["", 0])
            index += 1
            continue

        if token == ")":
            if stack:
                stack.pop()
            index += 1
            continue

        if token == "," and stack:
            stack[-1][1] = int(stack[-1][1]) + 1
            index += 1
            continue

        index += 1

    return analysis


def _signatures(text: str, fields: Sequence[str]) -> Dict[str, str]:
    """Sinh chuỗi đại diện cho từng mức trừu tượng."""
    exact = re.sub(r"\s+", "", text.lower())

    # Mức family: mọi hằng số thành #, trường dữ liệu giữ nguyên.
    family = re.sub(r"\b\d+(?:\.\d+)?\b", "#", exact)

    # Mức template: thêm một bước thay từng trường dữ liệu thành $.
    template = family
    if fields:
        # Thay trường dài trước để "close" không phá "close_adj".
        for name in sorted(set(fields), key=len, reverse=True):
            template = re.sub(rf"\b{re.escape(name)}\b", "$", template)

    return {"exact": exact, "family": family, "template": template}


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def fingerprint(expression: str) -> Dict[str, Any]:
    """Phân tích cấu trúc một biểu thức, không thực thi và không gọi mạng.

    Kết quả là từ điển tất định: cùng đầu vào luôn cho cùng đầu ra, nhờ vậy
    dùng làm khóa lưu trữ và so sánh giữa các lần chạy.
    """
    text = " ".join((expression or "").split())
    tokens = _tokenize(text)
    analysis = _analyse(tokens)

    unique_fields = sorted(set(analysis.fields))
    signatures = _signatures(text, unique_fields)
    operator_counts = Counter(analysis.operators)

    return {
        # Ba mức vân tay.
        "exact": _sha(signatures["exact"]),
        "family": _sha(signatures["family"]),
        "template": _sha(signatures["template"]),
        # Giữ khóa hash trỏ vào mức family để mã và dữ liệu cũ không phải sửa.
        "hash": _sha(signatures["family"]),
        # Chuỗi đại diện, hữu ích khi cần đọc bằng mắt lúc gỡ lỗi.
        "exact_signature": signatures["exact"],
        "family_signature": signatures["family"],
        "template_signature": signatures["template"],
        "normalized": signatures["family"],
        # Thành phần cấu trúc.
        "operators": sorted(operator_counts),
        "operator_counts": dict(operator_counts),
        "operator_count": len(analysis.operators),
        "fields": unique_fields,
        "field_count": len(unique_fields),
        "groups": sorted(set(analysis.groups)),
        "windows": sorted(set(analysis.windows)),
        "numeric_literals": list(analysis.numbers),
        "keyword_args": sorted(set(analysis.keyword_args)),
        # Đặc trưng ngữ nghĩa.
        "neutralization": sorted(
            set(analysis.operators) & NEUTRALIZE_OPERATORS
        ),
        "has_neutralization": bool(set(analysis.operators) & NEUTRALIZE_OPERATORS),
        "has_condition": bool(set(analysis.operators) & CONDITION_OPERATORS),
        "depth": analysis.max_depth,
        "complexity": len(tokens),
    }


def family_of(expression: str) -> str:
    """Đường tắt lấy vân tay mức family."""
    return fingerprint(expression)["family"]


def template_of(expression: str) -> str:
    """Đường tắt lấy vân tay mức template."""
    return fingerprint(expression)["template"]


# ----------------------------------------------------------------------
# Phân loại trùng lặp
# ----------------------------------------------------------------------
#: Các mức quan hệ, xếp từ trùng khít tới không liên quan.
RELATION_EXACT = "exact_duplicate"
RELATION_PARAMETER = "same_structure_different_parameter"
RELATION_FIELD = "same_structure_different_field"
RELATION_SAME_FIELDS_DIFFERENT_OPERATORS = "same_fields_different_operators"
RELATION_SAME_OPERATORS_DIFFERENT_FIELDS = "same_operators_different_fields"
RELATION_NEAR = "near_duplicate"
RELATION_UNRELATED = "unrelated"

#: Ngưỡng Jaccard để coi hai biểu thức là gần trùng.
NEAR_DUPLICATE_THRESHOLD = 0.80


def _jaccard(left: Sequence[str], right: Sequence[str]) -> float:
    first, second = set(left), set(right)
    if not first and not second:
        return 1.0
    union = first | second
    return len(first & second) / len(union) if union else 0.0


def compare(left: str, right: str) -> Dict[str, Any]:
    """So sánh hai biểu thức và mô tả quan hệ giữa chúng.

    Thứ tự kiểm tra đi từ ràng buộc chặt tới lỏng, dừng ở quan hệ đầu tiên khớp.
    """
    a = fingerprint(left)
    b = fingerprint(right)

    if a["exact"] == b["exact"]:
        relation = RELATION_EXACT
    elif a["family"] == b["family"]:
        # Cùng cấu trúc, cùng trường, chỉ khác hằng số.
        relation = RELATION_PARAMETER
    elif a["template"] == b["template"]:
        # Cùng khuôn, khác trường dữ liệu.
        relation = RELATION_FIELD
    elif a["fields"] == b["fields"] and a["operators"] != b["operators"]:
        relation = RELATION_SAME_FIELDS_DIFFERENT_OPERATORS
    elif a["operators"] == b["operators"] and a["fields"] != b["fields"]:
        relation = RELATION_SAME_OPERATORS_DIFFERENT_FIELDS
    else:
        operator_similarity = _jaccard(a["operators"], b["operators"])
        field_similarity = _jaccard(a["fields"], b["fields"])
        combined = (operator_similarity + field_similarity) / 2.0
        relation = (
            RELATION_NEAR if combined >= NEAR_DUPLICATE_THRESHOLD else RELATION_UNRELATED
        )

    return {
        "relation": relation,
        "is_duplicate": relation
        in (RELATION_EXACT, RELATION_PARAMETER, RELATION_NEAR),
        "operator_similarity": round(_jaccard(a["operators"], b["operators"]), 4),
        "field_similarity": round(_jaccard(a["fields"], b["fields"]), 4),
        "left": a,
        "right": b,
    }


def find_duplicates(
    expression: str, candidates: Sequence[str]
) -> List[Dict[str, Any]]:
    """Tìm trong `candidates` những biểu thức có quan hệ với `expression`.

    Kết quả bỏ qua quan hệ `unrelated` và được sắp theo mức chặt giảm dần.
    """
    order = {
        RELATION_EXACT: 0,
        RELATION_PARAMETER: 1,
        RELATION_FIELD: 2,
        RELATION_NEAR: 3,
        RELATION_SAME_FIELDS_DIFFERENT_OPERATORS: 4,
        RELATION_SAME_OPERATORS_DIFFERENT_FIELDS: 5,
    }
    matches: List[Dict[str, Any]] = []
    for candidate in candidates:
        result = compare(expression, candidate)
        if result["relation"] == RELATION_UNRELATED:
            continue
        matches.append(
            {
                "expression": candidate,
                "relation": result["relation"],
                "operator_similarity": result["operator_similarity"],
                "field_similarity": result["field_similarity"],
            }
        )
    matches.sort(key=lambda item: (order.get(item["relation"], 9), item["expression"]))
    return matches
