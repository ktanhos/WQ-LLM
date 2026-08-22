"""Lớp mô hình ngôn ngữ tùy chọn.

Toàn bộ quy trình nghiên cứu chạy được mà không cần mô hình ngôn ngữ. Lớp này
chỉ bổ sung phần diễn giải bằng văn xuôi: tóm tắt lịch sử, đề xuất giả thuyết,
phân tích khoảng trống và giải thích một biểu thức.

Mô hình ngôn ngữ không bao giờ được phép nộp alpha, sửa cấu hình hay gọi BRAIN.
Đầu ra của nó là văn bản để người nghiên cứu đọc, không phải lệnh để hệ thống
thi hành.
"""

from .base import LLMProvider, LLMResponse, NullProvider, get_provider
from .claude import ClaudeProvider
from .ollama import OllamaProvider

__all__ = [
    "LLMProvider",
    "LLMResponse",
    "NullProvider",
    "ClaudeProvider",
    "OllamaProvider",
    "get_provider",
]
