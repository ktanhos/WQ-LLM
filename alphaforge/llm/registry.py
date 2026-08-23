"""Chọn nhà cung cấp mô hình ngôn ngữ theo cấu hình.

Việc chọn nhà cung cấp tách khỏi `base.py` có lý do cấu trúc: nếu `base` biết
tới `claude` và `ollama`, mà hai mô đun đó lại kế thừa lớp trong `base`, thì đồ
thị phụ thuộc thành vòng. Vòng đó chạy được nhờ nhập trễ bên trong hàm, nhưng
đọc mã lại khó và dễ vỡ khi ai đó chuyển lệnh nhập lên đầu tệp.

Đặt ở đây thì chiều phụ thuộc chỉ đi một hướng:

    registry -> claude -> base
    registry -> ollama -> base
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from .base import LLMProvider, NullProvider
from .claude import ClaudeProvider
from .ollama import OllamaProvider

#: Tên nhà cung cấp được hiểu là "không dùng mô hình ngôn ngữ".
DISABLED_NAMES = frozenset({"", "none", "off", "disabled", "false", "no"})

PROVIDERS = {
    "claude": ClaudeProvider,
    "ollama": OllamaProvider,
}


def get_provider(config: Optional[Dict[str, Any]] = None) -> LLMProvider:
    """Chọn nhà cung cấp theo cấu hình, mặc định là không dùng gì.

    Thứ tự ưu tiên: biến môi trường, rồi tệp cấu hình. Tên không nhận diện được
    trả về NullProvider thay vì ném lỗi, để một cấu hình sai không làm hỏng các
    lệnh không liên quan tới mô hình ngôn ngữ.
    """
    config = dict(config or {})
    name = str(
        os.environ.get("ALPHAFORGE_LLM_PROVIDER") or config.get("provider") or "none"
    ).strip().lower()

    if name in DISABLED_NAMES:
        return NullProvider()

    provider_class = PROVIDERS.get(name)
    if provider_class is None:
        return NullProvider()

    model = str(os.environ.get("ALPHAFORGE_LLM_MODEL") or config.get("model") or "")
    options = {
        key: value for key, value in config.items() if key not in ("provider", "model")
    }
    return provider_class(model=model, **options)
