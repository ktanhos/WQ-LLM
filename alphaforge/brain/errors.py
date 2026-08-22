"""Phân loại lỗi để lớp gọi quyết định thử lại hay dừng hẳn."""

from __future__ import annotations


class BrainError(Exception):
    """Lỗi gốc của mọi tương tác với máy chủ BRAIN."""


class AuthenticationError(BrainError):
    """Thông tin đăng nhập sai, phiên hết hạn hoặc bị yêu cầu xác thực bổ sung."""


class RateLimitError(BrainError):
    """Vượt hạn mức yêu cầu hoặc hạn mức mô phỏng đồng thời."""

    def __init__(self, message: str, retry_after: float = 10.0):
        super().__init__(message)
        self.retry_after = retry_after


class TransientError(BrainError):
    """Lỗi tạm thời phía máy chủ hoặc lỗi mạng, nên thử lại."""


class SimulationError(BrainError):
    """Mô phỏng bị máy chủ từ chối hoặc kết thúc ở trạng thái lỗi.

    Đây là lỗi thuộc về bản thân biểu thức nên không thử lại.
    """

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.detail = detail
