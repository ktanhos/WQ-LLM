"""Giao diện chung cho các nhà cung cấp mô hình ngôn ngữ.

Thiết kế theo hướng tùy chọn hoàn toàn. Khi không cấu hình nhà cung cấp nào,
`get_provider` trả về NullProvider và mọi tính năng diễn giải báo là không khả
dụng thay vì ném lỗi. Bộ sinh tất định và toàn bộ pipeline BRAIN không phụ
thuộc vào lớp này.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class LLMResponse:
    """Kết quả một lượt gọi mô hình."""

    text: str
    provider: str = ""
    model: str = ""
    available: bool = True
    error: Optional[str] = None
    usage: Dict[str, Any] = field(default_factory=dict)


class LLMProvider:
    """Lớp cơ sở. Nhà cung cấp cụ thể chỉ cần cài đặt `complete`."""

    name: str = "base"

    def __init__(self, model: str = "", **options: Any):
        self.model = model
        self.options = options

    def is_available(self) -> bool:
        """Có đủ thông tin cấu hình để gọi hay không. Không gọi mạng."""
        return False

    def complete(self, prompt: str, *, system: str = "", max_tokens: int = 1024) -> LLMResponse:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Các tác vụ nghiên cứu được hỗ trợ. Tất cả đều chỉ sinh văn bản.
    # ------------------------------------------------------------------
    def summarize_research(self, analysis: Dict[str, Any]) -> LLMResponse:
        return self.complete(
            _prompt_summarize(analysis),
            system="Bạn là trợ lý nghiên cứu định lượng. Trả lời ngắn gọn bằng tiếng Việt.",
        )

    def propose_hypotheses(self, analysis: Dict[str, Any], count: int = 3) -> LLMResponse:
        return self.complete(
            _prompt_hypotheses(analysis, count),
            system="Bạn là trợ lý nghiên cứu định lượng. Đề xuất giả thuyết có cơ sở kinh tế.",
        )

    def analyze_gaps(self, gaps: List[Dict[str, Any]]) -> LLMResponse:
        return self.complete(
            _prompt_gaps(gaps),
            system="Bạn là trợ lý nghiên cứu định lượng. Chỉ ra khoảng trống đáng khảo sát.",
        )

    def explain_alpha(self, expression: str, metrics: Optional[Dict[str, Any]] = None) -> LLMResponse:
        return self.complete(
            _prompt_explain(expression, metrics or {}),
            system="Bạn là trợ lý nghiên cứu định lượng. Giải thích ý nghĩa kinh tế của biểu thức.",
        )


class NullProvider(LLMProvider):
    """Nhà cung cấp rỗng, dùng khi người dùng không bật mô hình ngôn ngữ."""

    name = "none"

    def is_available(self) -> bool:
        return False

    def complete(self, prompt: str, *, system: str = "", max_tokens: int = 1024) -> LLMResponse:
        return LLMResponse(
            text="",
            provider=self.name,
            available=False,
            error=(
                "Chưa cấu hình mô hình ngôn ngữ. Đặt llm.provider trong "
                "config/settings.yaml hoặc biến môi trường ALPHAFORGE_LLM_PROVIDER. "
                "Mọi chức năng còn lại của Alpha Forge không cần mô hình ngôn ngữ."
            ),
        )


def get_provider(config: Optional[Dict[str, Any]] = None) -> LLMProvider:
    """Chọn nhà cung cấp theo cấu hình, mặc định là không dùng gì.

    Thứ tự ưu tiên: biến môi trường, rồi tệp cấu hình. Nhà cung cấp không nhận
    diện được sẽ trả về NullProvider thay vì ném lỗi, để một cấu hình sai không
    làm hỏng các lệnh không liên quan tới mô hình ngôn ngữ.
    """
    config = dict(config or {})
    provider_name = str(
        os.environ.get("ALPHAFORGE_LLM_PROVIDER") or config.get("provider") or "none"
    ).strip().lower()

    if provider_name in ("", "none", "off", "disabled"):
        return NullProvider()

    model = str(os.environ.get("ALPHAFORGE_LLM_MODEL") or config.get("model") or "")
    options = {
        key: value
        for key, value in config.items()
        if key not in ("provider", "model")
    }

    if provider_name == "claude":
        from .claude import ClaudeProvider

        return ClaudeProvider(model=model, **options)
    if provider_name == "ollama":
        from .ollama import OllamaProvider

        return OllamaProvider(model=model, **options)
    return NullProvider()


# ----------------------------------------------------------------------
# Dựng lời nhắc. Tách riêng để kiểm thử được mà không cần gọi mạng.
# ----------------------------------------------------------------------
def _prompt_summarize(analysis: Dict[str, Any]) -> str:
    metrics = analysis.get("metrics", {})
    sharpe = metrics.get("sharpe", {})
    return (
        "Tóm tắt kết quả nghiên cứu alpha dưới đây trong khoảng năm câu.\n\n"
        f"Tổng số alpha: {analysis.get('total')}\n"
        f"Phân bố trạng thái: {analysis.get('counts')}\n"
        f"Tỷ lệ đạt: {analysis.get('pass_rate')}\n"
        f"Sharpe trung vị: {sharpe.get('median')}, phân vị 25: {sharpe.get('p25')}, "
        f"phân vị 75: {sharpe.get('p75')}, cỡ mẫu: {sharpe.get('count')}\n\n"
        "Nêu rõ nếu cỡ mẫu quá nhỏ để kết luận."
    )


def _prompt_hypotheses(analysis: Dict[str, Any], count: int) -> str:
    underexplored = analysis.get("underexplored_structures", [])[:5]
    return (
        f"Đề xuất {count} giả thuyết nghiên cứu alpha mới.\n\n"
        f"Các họ cấu trúc còn ít được khảo sát: {underexplored}\n"
        f"Các họ đã thử nhiều: {analysis.get('frequently_tested_structures', [])[:5]}\n\n"
        "Mỗi giả thuyết cần có: phát biểu, trực giác kinh tế, chiều kỳ vọng và "
        "khoảng thời gian kỳ vọng. Không đề xuất lại các họ đã bão hòa."
    )


def _prompt_gaps(gaps: List[Dict[str, Any]]) -> str:
    return (
        "Phân tích các khoảng trống nghiên cứu sau và xếp thứ tự ưu tiên khảo sát.\n\n"
        f"{gaps[:15]}\n\n"
        "Với mỗi khoảng trống, nêu lý do nên hoặc không nên đầu tư thêm hạn mức mô phỏng. "
        "Lưu ý cỡ mẫu nhỏ thì kết luận chưa chắc chắn."
    )


def _prompt_explain(expression: str, metrics: Dict[str, Any]) -> str:
    return (
        "Giải thích ý nghĩa kinh tế của biểu thức alpha sau.\n\n"
        f"Biểu thức: {expression}\n"
        f"Chỉ số: {metrics}\n\n"
        "Nêu giả định về hành vi thị trường mà biểu thức đang khai thác, và "
        "trường hợp nào biểu thức sẽ hoạt động kém."
    )
