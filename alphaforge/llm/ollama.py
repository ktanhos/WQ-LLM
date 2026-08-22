"""Nhà cung cấp Ollama chạy cục bộ.

Dùng khi muốn giữ toàn bộ dữ liệu nghiên cứu trong máy. Ollama không cần khóa
API, chỉ cần dịch vụ đang chạy ở địa chỉ cấu hình.
"""

from __future__ import annotations

import os
from typing import Any

from .base import LLMProvider, LLMResponse

DEFAULT_MODEL = "llama3.1"
DEFAULT_HOST = "http://127.0.0.1:11434"


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, model: str = "", host: str = "", timeout: float = 120.0, **options: Any):
        super().__init__(model=model or DEFAULT_MODEL, **options)
        self.host = (
            host or os.environ.get("OLLAMA_HOST") or DEFAULT_HOST
        ).rstrip("/")
        self.timeout = float(timeout)

    def is_available(self) -> bool:
        """Kiểm tra dịch vụ có đang chạy không.

        Đây là lệnh gọi mạng cục bộ và có thể thất bại khi Ollama chưa bật, nên
        mọi lỗi đều quy về không khả dụng.
        """
        try:
            import requests

            response = requests.get(f"{self.host}/api/tags", timeout=3.0)
            return response.status_code == 200
        except Exception:
            return False

    def complete(self, prompt: str, *, system: str = "", max_tokens: int = 1024) -> LLMResponse:
        try:
            import requests
        except ImportError:
            return LLMResponse(
                text="", provider=self.name, model=self.model, available=False,
                error="Chưa cài gói requests.",
            )

        try:
            response = requests.post(
                f"{self.host}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "system": system,
                    "stream": False,
                    "options": {"num_predict": int(max_tokens)},
                },
                timeout=self.timeout,
            )
            if response.status_code != 200:
                return LLMResponse(
                    text="", provider=self.name, model=self.model, available=False,
                    error=f"Ollama trả về mã {response.status_code}.",
                )
            body = response.json()
            return LLMResponse(
                text=str(body.get("response", "")),
                provider=self.name,
                model=self.model,
            )
        except Exception as exc:
            return LLMResponse(
                text="", provider=self.name, model=self.model, available=False,
                error=f"Gọi Ollama thất bại: {exc}",
            )
