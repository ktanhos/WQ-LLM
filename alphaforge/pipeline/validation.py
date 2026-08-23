"""Kiểm tra biểu thức tại chỗ trước khi đưa vào hàng đợi mô phỏng.

Mỗi lượt mô phỏng tốn hạn mức tài khoản và không hoàn lại được. Một biểu thức
sai cú pháp hay dùng trường dữ liệu không tồn tại chắc chắn bị máy chủ từ chối,
nên gửi nó đi là lãng phí thuần túy. Lớp này chặn những trường hợp đó bằng
kiểm tra cục bộ, không gọi mạng.

Nguyên tắc phân biệt lỗi và cảnh báo:

    lỗi       chắc chắn máy chủ từ chối, hoặc vi phạm ràng buộc nghiên cứu
    cảnh báo  đáng ngờ nhưng vẫn có thể hợp lệ, không chặn

Danh mục toán tử và trường dữ liệu là tùy chọn. Khi chưa tải về, mô đun chỉ
kiểm tra được cú pháp và giới hạn kích thước; nó không đoán bừa rằng một tên
lạ là sai, vì nền tảng liên tục bổ sung toán tử mới.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from ..history.fingerprint import TOKEN_RE, fingerprint

#: Giới hạn mặc định, khớp với cấu hình generator.
DEFAULT_MAX_LENGTH = 480
DEFAULT_MAX_OPERATORS = 40
DEFAULT_MAX_FIELDS = 12
DEFAULT_MAX_DEPTH = 15


@dataclass
class ValidationResult:
    """Kết luận cho một biểu thức."""

    expression: str
    valid: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def reason(self) -> str:
        """Lý do gộp thành một chuỗi, dùng để lưu vào kho."""
        return "; ".join(self.errors)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "expression": self.expression,
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
        }


@dataclass
class ValidationConstraints:
    """Ràng buộc kích thước và ràng buộc nghiên cứu.

    Ràng buộc nghiên cứu cho phép một thí nghiệm bó hẹp không gian: ví dụ chỉ
    chấp nhận biểu thức dùng đúng những trường dữ liệu đã khai báo, để kết quả
    quy được về đúng biến đang khảo sát.
    """

    max_length: int = DEFAULT_MAX_LENGTH
    max_operators: int = DEFAULT_MAX_OPERATORS
    max_fields: int = DEFAULT_MAX_FIELDS
    max_depth: int = DEFAULT_MAX_DEPTH
    #: Mặc định 0 vì biểu thức chỉ dùng toán tử trung tố, ví dụ "close * -open",
    #: vẫn hợp lệ. Thí nghiệm nào cần ít nhất một lời gọi toán tử thì tự đặt.
    min_operators: int = 0
    #: Chỉ chấp nhận biểu thức nằm trong tập trường dữ liệu này.
    allowed_fields: Optional[Sequence[str]] = None
    #: Chỉ chấp nhận biểu thức nằm trong tập toán tử này.
    allowed_operators: Optional[Sequence[str]] = None
    #: Bắt buộc phải có mặt các toán tử này.
    required_operators: Sequence[str] = ()
    #: Cấm hẳn các toán tử này.
    forbidden_operators: Sequence[str] = ()


class AlphaValidator:
    """Kiểm tra biểu thức mà không gọi mạng.

    Đối tượng giữ trạng thái các vân tay đã thấy trong phiên, nhờ vậy phát hiện
    được trùng lặp ngay trong một lô sinh chứ không chỉ trùng với kho.
    """

    def __init__(
        self,
        *,
        known_operators: Optional[Iterable[str]] = None,
        known_fields: Optional[Iterable[str]] = None,
        constraints: Optional[ValidationConstraints] = None,
        seen_fingerprints: Optional[Iterable[str]] = None,
    ):
        self.known_operators: Optional[Set[str]] = (
            {str(name).lower() for name in known_operators}
            if known_operators is not None else None
        )
        self.known_fields: Optional[Set[str]] = (
            {str(name).lower() for name in known_fields}
            if known_fields is not None else None
        )
        self.constraints = constraints or ValidationConstraints()
        self.seen: Set[str] = set(seen_fingerprints or ())

    # ------------------------------------------------------------------
    def validate(self, expression: str) -> ValidationResult:
        """Kiểm tra một biểu thức và trả về kết luận đầy đủ."""
        text = " ".join((expression or "").split())
        result = ValidationResult(expression=text)

        if not text:
            result.valid = False
            result.errors.append("Biểu thức rỗng.")
            return result

        self._check_syntax(text, result)
        if not result.valid:
            # Cú pháp sai thì các kiểm tra sau không còn ý nghĩa.
            return result

        meta = fingerprint(text)
        result.meta = {
            "exact": meta["exact"],
            "family": meta["family"],
            "template": meta["template"],
            "operators": meta["operators"],
            "fields": meta["fields"],
            "windows": meta["windows"],
            "depth": meta["depth"],
        }

        self._check_size(text, meta, result)
        self._check_catalogue(meta, result)
        self._check_constraints(meta, result)
        self._check_duplicate(meta, result)

        result.valid = not result.errors
        return result

    def accept(self, result: ValidationResult) -> None:
        """Ghi nhận một biểu thức đã được chấp nhận, để lô sau biết là trùng."""
        exact = result.meta.get("exact")
        if exact:
            self.seen.add(exact)

    def validate_many(self, expressions: Iterable[str]) -> List[ValidationResult]:
        """Kiểm tra cả lô, đồng thời phát hiện trùng lặp trong chính lô đó."""
        results = []
        for expression in expressions:
            result = self.validate(expression)
            if result.valid:
                self.accept(result)
            results.append(result)
        return results

    # ------------------------------------------------------------------
    def _check_syntax(self, text: str, result: ValidationResult) -> None:
        """Kiểm tra cú pháp ở mức có thể làm mà không cần bộ phân tích đầy đủ."""
        depth = 0
        for character in text:
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth < 0:
                    result.valid = False
                    result.errors.append("Thừa dấu đóng ngoặc.")
                    return
        if depth != 0:
            result.valid = False
            result.errors.append(
                f"Ngoặc không cân bằng, thiếu {depth} dấu đóng ngoặc."
            )
            return

        if re.search(r"\(\s*\)", text):
            result.valid = False
            result.errors.append("Lời gọi toán tử không có đối số.")
            return

        if re.search(r",\s*,", text) or re.search(r"\(\s*,", text) or re.search(r",\s*\)", text):
            result.valid = False
            result.errors.append("Danh sách đối số có chỗ trống.")
            return

        # Hai toán tử trung tố liền nhau, ví dụ "close + * open". Dấu trừ được
        # loại trừ vì nó hợp lệ ở dạng một ngôi: "close * -open".
        if re.search(r"[+*/]\s*[+*/]", text):
            result.valid = False
            result.errors.append("Hai toán tử trung tố đứng liền nhau.")
            return

        tokens = TOKEN_RE.findall(text)
        if not tokens:
            result.valid = False
            result.errors.append("Không nhận ra token nào hợp lệ.")

    def _check_size(self, text: str, meta: Dict[str, Any], result: ValidationResult) -> None:
        limits = self.constraints
        if len(text) > limits.max_length:
            result.errors.append(
                f"Biểu thức dài {len(text)} ký tự, vượt giới hạn {limits.max_length}."
            )
        operator_count = meta["operator_count"]
        if operator_count > limits.max_operators:
            result.errors.append(
                f"Dùng {operator_count} toán tử, vượt giới hạn {limits.max_operators}."
            )
        if operator_count < limits.min_operators:
            result.errors.append(
                f"Chỉ có {operator_count} toán tử, cần tối thiểu {limits.min_operators}."
            )
        if meta["field_count"] > limits.max_fields:
            result.errors.append(
                f"Dùng {meta['field_count']} trường dữ liệu, vượt giới hạn {limits.max_fields}."
            )
        if meta["depth"] > limits.max_depth:
            result.errors.append(
                f"Lồng sâu {meta['depth']} cấp, vượt giới hạn {limits.max_depth}."
            )
        if not meta["fields"]:
            result.warnings.append(
                "Biểu thức không tham chiếu trường dữ liệu nào, nhiều khả năng là hằng."
            )

    def _check_catalogue(self, meta: Dict[str, Any], result: ValidationResult) -> None:
        """Đối chiếu với danh mục tải về, nếu có.

        Không có danh mục thì bỏ qua thay vì đoán, vì nền tảng liên tục bổ sung
        toán tử và trường dữ liệu mới.
        """
        if self.known_operators is not None:
            unknown = [
                name for name in meta["operators"] if name not in self.known_operators
            ]
            if unknown:
                result.errors.append(
                    "Toán tử không có trong danh mục: " + ", ".join(sorted(unknown)) + "."
                )
        if self.known_fields is not None:
            unknown = [name for name in meta["fields"] if name not in self.known_fields]
            if unknown:
                result.errors.append(
                    "Trường dữ liệu không có trong danh mục: "
                    + ", ".join(sorted(unknown)) + "."
                )

    def _check_constraints(self, meta: Dict[str, Any], result: ValidationResult) -> None:
        """Ràng buộc do thí nghiệm đặt ra."""
        limits = self.constraints
        if limits.allowed_fields is not None:
            allowed = {str(name).lower() for name in limits.allowed_fields}
            outside = [name for name in meta["fields"] if name not in allowed]
            if outside:
                result.errors.append(
                    "Trường dữ liệu ngoài phạm vi thí nghiệm: "
                    + ", ".join(sorted(outside)) + "."
                )
        if limits.allowed_operators is not None:
            allowed = {str(name).lower() for name in limits.allowed_operators}
            outside = [name for name in meta["operators"] if name not in allowed]
            if outside:
                result.errors.append(
                    "Toán tử ngoài phạm vi thí nghiệm: " + ", ".join(sorted(outside)) + "."
                )
        missing = [
            name for name in limits.required_operators
            if str(name).lower() not in meta["operators"]
        ]
        if missing:
            result.errors.append(
                "Thiếu toán tử bắt buộc: " + ", ".join(sorted(missing)) + "."
            )
        forbidden = [
            name for name in limits.forbidden_operators
            if str(name).lower() in meta["operators"]
        ]
        if forbidden:
            result.errors.append(
                "Dùng toán tử bị cấm: " + ", ".join(sorted(forbidden)) + "."
            )

    def _check_duplicate(self, meta: Dict[str, Any], result: ValidationResult) -> None:
        if meta["exact"] in self.seen:
            result.errors.append("Biểu thức đã tồn tại, trùng khít với một alpha đã có.")


def validator_from_database(
    db,
    *,
    region: str = "USA",
    universe: str = "TOP3000",
    delay: int = 1,
    constraints: Optional[ValidationConstraints] = None,
    include_seen: bool = True,
) -> AlphaValidator:
    """Dựng bộ kiểm tra từ danh mục và lịch sử đã lưu trong kho.

    Danh mục trường dữ liệu chỉ được dùng khi kho đã có dữ liệu. Kho rỗng thì
    bỏ qua bước đối chiếu, tránh loại nhầm mọi biểu thức chỉ vì chưa chạy lệnh
    tải danh mục.
    """
    fields = [
        str(row["id"]) for row in db.load_data_fields(
            region=region, universe=universe, delay=delay, limit=100000
        )
    ]
    seen: Set[str] = set()
    if include_seen:
        connection = db.connect()
        try:
            for table in ("alphas", "historical_alphas"):
                for row in connection.execute(
                    f"SELECT expression FROM {table} "
                    "WHERE expression IS NOT NULL AND expression != ''"
                ).fetchall():
                    try:
                        seen.add(fingerprint(row["expression"])["exact"])
                    except Exception:
                        continue
        finally:
            connection.close()

    return AlphaValidator(
        known_fields=fields or None,
        constraints=constraints,
        seen_fingerprints=seen,
    )
