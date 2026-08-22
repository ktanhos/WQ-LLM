"""Nạp và hợp nhất cấu hình từ tệp YAML và biến môi trường."""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

import yaml

try:  # python-dotenv là tùy chọn, thiếu nó thì đọc thẳng biến môi trường
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*_args, **_kwargs):
        return False


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "settings.yaml"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "alphaforge.sqlite3"


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Hợp nhất hai từ điển lồng nhau, giá trị của override được ưu tiên."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


@dataclass
class Credentials:
    """Thông tin đăng nhập BRAIN.

    Mật khẩu được đánh dấu repr=False để không lọt vào nhật ký hay thông báo
    lỗi khi một đối tượng chứa nó vô tình bị in ra.
    """

    email: str
    password: str = field(default="", repr=False)

    def is_complete(self) -> bool:
        return bool(self.email and self.password)

    def __str__(self) -> str:
        return f"Credentials(email={self.email!r})"


@dataclass
class Settings:
    raw: Dict[str, Any] = field(default_factory=dict)
    db_path: Path = DEFAULT_DB_PATH
    credentials: Credentials = field(default_factory=lambda: Credentials("", ""))

    @property
    def api(self) -> Dict[str, Any]:
        return self.raw.get("api", {})

    @property
    def simulation(self) -> Dict[str, Any]:
        return self.raw.get("simulation", {})

    @property
    def scoring(self) -> Dict[str, Any]:
        return self.raw.get("scoring", {})

    @property
    def correlation(self) -> Dict[str, Any]:
        return self.raw.get("correlation", {})

    @property
    def generator(self) -> Dict[str, Any]:
        return self.raw.get("generator", {})

    @property
    def llm(self) -> Dict[str, Any]:
        """Cấu hình mô hình ngôn ngữ. Rỗng nghĩa là không dùng."""
        return self.raw.get("llm", {})

    @property
    def history(self) -> Dict[str, Any]:
        return self.raw.get("history", {})

    def base_url(self) -> str:
        env_url = os.environ.get("BRAIN_API_BASE")
        if env_url:
            return env_url.rstrip("/")
        return str(self.api.get("base_url", "https://api.worldquantbrain.com")).rstrip("/")


def load_settings(config_path: str | os.PathLike | None = None) -> Settings:
    """Đọc cấu hình theo thứ tự ưu tiên: tham số, biến môi trường, giá trị mặc định."""
    load_dotenv(PROJECT_ROOT / ".env")

    path = Path(
        config_path
        or os.environ.get("ALPHAFORGE_CONFIG")
        or DEFAULT_CONFIG_PATH
    )
    raw: Dict[str, Any] = {}
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}

    db_env = os.environ.get("ALPHAFORGE_DB")
    db_path = Path(db_env) if db_env else DEFAULT_DB_PATH
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)

    credentials = Credentials(
        email=os.environ.get("BRAIN_EMAIL", ""),
        password=os.environ.get("BRAIN_PASSWORD", ""),
    )
    return Settings(raw=raw, db_path=db_path, credentials=credentials)
