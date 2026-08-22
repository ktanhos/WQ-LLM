"""Nhà cung cấp Claude qua Anthropic Messages API.

Khóa API chỉ đọc từ biến môi trường ANTHROPIC_API_KEY. Không bao giờ đọc từ
tệp cấu hình trong kho mã, để khóa không thể vô tình bị commit.

Thư viện anthropic là phụ thuộc tùy chọn. Thiếu nó thì nhà cung cấp báo không
khả dụng thay vì làm hỏng phần còn lại của chương trình.
"""

from __future__ import annotations

import os
from typing import Any

from .base import LLMProvider, LLMResponse

DEFAULT_MODEL = "claude-sonnet-4-5"


class ClaudeProvider(LLMProvider):
    name = "claude"

    def __init__(self, model: str = "", **options: Any):
        super().__init__(model=model or DEFAULT_MODEL, **options)
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    def is_available(self) -> bool:
        if not self.api_key:
            return False
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False
        return True

    def complete(self, prompt: str, *, system: str = "", max_tokens: int = 1024) -> LLMResponse:
        if not self.api_key:
            return LLMResponse(
                text="", provider=self.name, model=self.model, available=False,
                error="Thiếu biến môi trường ANTHROPIC_API_KEY.",
            )
        try:
            import anthropic
        except ImportError:
            return LLMResponse(
                text="", provider=self.name, model=self.model, available=False,
                error="Chưa cài gói anthropic. Chạy: pip install anthropic",
            )

        try:
            client = anthropic.Anthropic(api_key=self.api_key)
            message = client.messages.create(
                model=self.model,
                max_tokens=int(max_tokens),
                system=system or "Bạn là trợ lý nghiên cứu định lượng.",
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(
                block.text for block in message.content if getattr(block, "type", "") == "text"
            )
            return LLMResponse(
                text=text,
                provider=self.name,
                model=self.model,
                usage={
                    "input_tokens": getattr(message.usage, "input_tokens", None),
                    "output_tokens": getattr(message.usage, "output_tokens", None),
                },
            )
        except Exception as exc:
            # Lỗi mạng hay lỗi hạn mức không được làm dừng lệnh CLI đang chạy.
            return LLMResponse(
                text="", provider=self.name, model=self.model, available=False,
                error=f"Gọi Claude thất bại: {exc}",
            )
