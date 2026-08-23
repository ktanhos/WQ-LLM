"""Lõi kết nối tới máy chủ WorldQuant BRAIN.

Lớp này chịu trách nhiệm duy nhất là nói chuyện với máy chủ. Mọi quyết định
nghiệp vụ như chấm điểm hay lọc tương quan đều nằm ở lớp pipeline.

Các điểm cuối được dùng:
    POST /authentication              đăng nhập bằng xác thực cơ bản
    POST /simulations                 gửi một mô phỏng, trả về địa chỉ theo dõi ở header Location
    GET  <địa chỉ theo dõi>           thăm dò tiến độ cho tới khi có mã alpha
    GET  /alphas/{id}                 đọc chỉ số hiệu năng và danh sách kiểm tra
    GET  /alphas/{id}/correlations/self   tự tương quan với các alpha khác của tài khoản
    GET  /alphas/{id}/correlations/prod   tương quan với danh mục sản phẩm
    GET  /data-fields                 tra cứu trường dữ liệu theo khu vực và tập dữ liệu
    GET  /operators                   danh mục toán tử khả dụng
    PATCH /alphas/{id}                đặt tên, màu, thẻ cho alpha

Cấu trúc điểm cuối do bên thứ ba vận hành và có thể thay đổi. Khi một lệnh
trả về lỗi phân tích dữ liệu, cần đối chiếu tài liệu API trước khi sửa mã.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

import requests
from requests.auth import HTTPBasicAuth

from ..config import Settings
from .errors import (
    AuthenticationError,
    BrainError,
    RateLimitError,
    SimulationError,
    TransientError,
)

logger = logging.getLogger(__name__)


@dataclass
class SimulationResult:
    """Kết quả một lượt mô phỏng đã hoàn tất."""

    alpha_id: str
    expression: str
    status: str = "SIMULATED"
    metrics: Dict[str, Any] = field(default_factory=dict)
    checks: List[Dict[str, Any]] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)
    elapsed_seconds: float = 0.0

    def failed_checks(self) -> List[str]:
        return [
            str(item.get("name"))
            for item in self.checks
            if str(item.get("result", "")).upper() == "FAIL"
        ]


class BrainClient:
    """Bọc phiên HTTP tới BRAIN kèm tự động đăng nhập lại và thử lại có lùi thời gian."""

    def __init__(self, settings: Settings, session: Optional[requests.Session] = None):
        self.settings = settings
        self.base_url = settings.base_url()
        self.session = session or requests.Session()
        self.timeout = float(settings.api.get("request_timeout", 60))
        self.max_retries = int(settings.api.get("max_retries", 4))
        self.backoff = float(settings.api.get("backoff_seconds", 5))
        self.poll_interval = float(settings.api.get("poll_interval", 5))
        self.poll_timeout = float(settings.api.get("poll_timeout", 900))
        self._auth_lock = threading.Lock()
        self._authenticated = False

    # ------------------------------------------------------------------
    # Xác thực
    # ------------------------------------------------------------------
    def authenticate(self, force: bool = False) -> Dict[str, Any]:
        """Đăng nhập và giữ cookie phiên trong session.

        Máy chủ có thể trả về 201 kèm thông tin người dùng, hoặc 401 kèm yêu cầu
        xác thực sinh trắc học. Trường hợp thứ hai cần người dùng mở trình duyệt
        hoàn tất thủ công, mã không thể xử lý thay.
        """
        with self._auth_lock:
            if self._authenticated and not force:
                return {}
            creds = self.settings.credentials
            if not creds.is_complete():
                raise AuthenticationError(
                    "Thiếu BRAIN_EMAIL hoặc BRAIN_PASSWORD. Hãy điền vào tệp .env."
                )
            url = f"{self.base_url}/authentication"
            try:
                response = self.session.post(
                    url,
                    auth=HTTPBasicAuth(creds.email, creds.password),
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                raise TransientError(f"Không kết nối được tới máy chủ BRAIN: {exc}") from exc

            if response.status_code in (200, 201):
                self._authenticated = True
                return _safe_json(response)

            if response.status_code == 401:
                header = response.headers.get("WWW-Authenticate", "")
                if "persona" in header.lower():
                    location = response.headers.get("Location", "")
                    raise AuthenticationError(
                        "Máy chủ yêu cầu xác thực sinh trắc học. "
                        f"Hãy mở trình duyệt và hoàn tất tại: {location or 'trang biometrics của BRAIN'}"
                    )
                raise AuthenticationError("Thông tin đăng nhập không hợp lệ.")

            raise AuthenticationError(
                f"Đăng nhập thất bại, mã trạng thái {response.status_code}."
            )

    # ------------------------------------------------------------------
    # Lớp yêu cầu chung
    # ------------------------------------------------------------------
    def request(
        self,
        method: str,
        path_or_url: str,
        *,
        expected: Iterable[int] = (200, 201),
        allow_reauth: bool = True,
        **kwargs: Any,
    ) -> requests.Response:
        """Gửi một yêu cầu, tự đăng nhập lại một lần khi gặp 401."""
        if not self._authenticated:
            self.authenticate()

        url = (
            path_or_url
            if path_or_url.startswith("http")
            else f"{self.base_url}/{path_or_url.lstrip('/')}"
        )
        kwargs.setdefault("timeout", self.timeout)

        attempt = 0
        while True:
            attempt += 1
            try:
                response = self.session.request(method, url, **kwargs)
            except requests.RequestException as exc:
                if attempt > self.max_retries:
                    raise TransientError(f"Lỗi mạng sau {attempt} lần thử: {exc}") from exc
                time.sleep(self.backoff * attempt)
                continue

            if response.status_code in expected:
                return response

            if response.status_code == 401 and allow_reauth:
                logger.info("Phiên hết hạn, đang đăng nhập lại.")
                self._authenticated = False
                self.authenticate(force=True)
                allow_reauth = False
                continue

            if response.status_code == 429:
                retry_after = _retry_after(response, default=self.backoff * attempt)
                if attempt > self.max_retries:
                    raise RateLimitError(
                        "Vượt hạn mức yêu cầu của máy chủ BRAIN.", retry_after
                    )
                logger.warning("Bị giới hạn tần suất, chờ %.1f giây.", retry_after)
                time.sleep(retry_after)
                continue

            if 500 <= response.status_code < 600:
                if attempt > self.max_retries:
                    raise TransientError(
                        f"Máy chủ lỗi {response.status_code} sau {attempt} lần thử."
                    )
                time.sleep(self.backoff * attempt)
                continue

            raise BrainError(
                f"Yêu cầu {method} {url} trả về {response.status_code}: {response.text[:400]}"
            )

    # ------------------------------------------------------------------
    # Mô phỏng
    # ------------------------------------------------------------------
    def submit_simulation(self, expression: str, settings: Dict[str, Any]) -> str:
        """Gửi một mô phỏng và trả về địa chỉ theo dõi tiến độ."""
        payload = {
            "type": self.settings.simulation.get("type", "REGULAR"),
            "settings": settings,
            "regular": expression,
        }
        response = self.request(
            "POST", "/simulations", json=payload, expected=(201, 200)
        )
        location = response.headers.get("Location")
        if not location:
            body = _safe_json(response)
            location = body.get("location") or body.get("id")
        if not location:
            raise SimulationError(
                "Máy chủ không trả về địa chỉ theo dõi tiến độ.",
                detail=response.text[:400],
            )
        if not str(location).startswith("http"):
            location = f"{self.base_url}/simulations/{location}"
        return str(location)

    def wait_for_simulation(self, progress_url: str) -> Dict[str, Any]:
        """Thăm dò tiến độ cho tới khi mô phỏng kết thúc."""
        deadline = time.time() + self.poll_timeout
        while True:
            if time.time() > deadline:
                raise TransientError(
                    f"Quá thời gian chờ {self.poll_timeout:.0f} giây cho mô phỏng."
                )
            response = self.request("GET", progress_url, expected=(200, 201, 202))
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                time.sleep(max(float(retry_after), 1.0))
                continue

            body = _safe_json(response)
            status = str(body.get("status", "")).upper()

            if body.get("alpha"):
                return body
            if status in ("ERROR", "FAIL", "FAILED"):
                raise SimulationError(
                    "Mô phỏng kết thúc ở trạng thái lỗi.",
                    detail=str(body.get("message") or body)[:400],
                )
            if status in ("WARNING", "COMPLETE", "COMPLETED") and not body.get("alpha"):
                raise SimulationError(
                    "Mô phỏng hoàn tất nhưng không sinh ra mã alpha.",
                    detail=str(body)[:400],
                )
            time.sleep(self.poll_interval)

    def simulate(self, expression: str, settings: Dict[str, Any]) -> SimulationResult:
        """Gửi mô phỏng, chờ kết quả và đọc chỉ số trong một lần gọi."""
        started = time.time()
        progress_url = self.submit_simulation(expression, settings)
        progress = self.wait_for_simulation(progress_url)
        alpha_id = str(progress.get("alpha"))
        detail = self.get_alpha(alpha_id)
        return SimulationResult(
            alpha_id=alpha_id,
            expression=expression,
            metrics=extract_metrics(detail),
            checks=list((detail.get("is") or {}).get("checks") or []),
            raw=detail,
            elapsed_seconds=time.time() - started,
        )

    # ------------------------------------------------------------------
    # Alpha
    # ------------------------------------------------------------------
    def get_alpha(self, alpha_id: str) -> Dict[str, Any]:
        response = self.request("GET", f"/alphas/{alpha_id}")
        return _safe_json(response)

    def update_alpha(
        self,
        alpha_id: str,
        *,
        name: Optional[str] = None,
        color: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if color is not None:
            payload["color"] = color
        if tags is not None:
            payload["tags"] = tags
        if not payload:
            return {}
        response = self.request("PATCH", f"/alphas/{alpha_id}", json=payload)
        return _safe_json(response)

    def get_self_correlation(self, alpha_id: str) -> Dict[str, Any]:
        return self._correlation(alpha_id, "self")

    def get_prod_correlation(self, alpha_id: str) -> Dict[str, Any]:
        return self._correlation(alpha_id, "prod")

    def _correlation(self, alpha_id: str, kind: str) -> Dict[str, Any]:
        """Điểm cuối tương quan tính toán chậm và có thể trả về rỗng kèm Retry-After."""
        deadline = time.time() + self.poll_timeout
        url = f"/alphas/{alpha_id}/correlations/{kind}"
        while True:
            if time.time() > deadline:
                raise TransientError("Quá thời gian chờ kết quả tương quan.")
            response = self.request("GET", url, expected=(200, 201, 202, 204))
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                time.sleep(max(float(retry_after), 1.0))
                continue
            if response.status_code == 204 or not response.content:
                time.sleep(self.poll_interval)
                continue
            return _safe_json(response)

    # ------------------------------------------------------------------
    # Danh mục dữ liệu
    # ------------------------------------------------------------------
    def get_data_fields(
        self,
        *,
        region: str = "USA",
        universe: str = "TOP3000",
        delay: int = 1,
        instrument_type: str = "EQUITY",
        dataset_id: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 50,
        max_records: int = 2000,
    ) -> List[Dict[str, Any]]:
        """Tải trường dữ liệu theo trang. Máy chủ giới hạn 50 bản ghi mỗi lần."""
        collected: List[Dict[str, Any]] = []
        offset = 0
        while len(collected) < max_records:
            params: Dict[str, Any] = {
                "instrumentType": instrument_type,
                "region": region,
                "universe": universe,
                "delay": delay,
                "limit": min(limit, 50),
                "offset": offset,
            }
            if dataset_id:
                params["dataset.id"] = dataset_id
            if search:
                params["search"] = search
            response = self.request("GET", "/data-fields", params=params)
            body = _safe_json(response)
            results = body.get("results") or []
            if not results:
                break
            collected.extend(results)
            offset += len(results)
            total = body.get("count")
            if isinstance(total, int) and offset >= total:
                break
        return collected[:max_records]

    def get_operators(self) -> List[Dict[str, Any]]:
        response = self.request("GET", "/operators")
        body = _safe_json(response)
        if isinstance(body, list):
            return body
        return body.get("results") or []

    def get_self(self) -> Dict[str, Any]:
        response = self.request("GET", "/users/self")
        return _safe_json(response)


# ----------------------------------------------------------------------
# Tiện ích
# ----------------------------------------------------------------------
def _safe_json(response: requests.Response) -> Dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    if isinstance(data, dict):
        return data
    return {"results": data}


def _retry_after(response: requests.Response, default: float) -> float:
    raw = response.headers.get("Retry-After")
    if not raw:
        return default
    try:
        return max(float(raw), 1.0)
    except (TypeError, ValueError):
        return default


METRIC_KEYS = (
    "sharpe",
    "fitness",
    "turnover",
    "returns",
    "drawdown",
    "margin",
    "longCount",
    "shortCount",
    "pnl",
    "bookSize",
)


def extract_metrics(alpha_detail: Dict[str, Any]) -> Dict[str, Any]:
    """Rút gọn phần chỉ số trong bản ghi alpha thành từ điển phẳng.

    Trường margin do máy chủ trả về ở dạng thập phân. Ở đây quy đổi thêm sang
    điểm cơ bản vì tiêu chí nộp thường phát biểu theo đơn vị đó.
    """
    stats = alpha_detail.get("is") or {}
    metrics: Dict[str, Any] = {}
    for key in METRIC_KEYS:
        if key in stats:
            metrics[key] = stats[key]
    if isinstance(metrics.get("margin"), (int, float)):
        metrics["marginBps"] = float(metrics["margin"]) * 10000.0
    metrics["checkFailures"] = [
        str(item.get("name"))
        for item in (stats.get("checks") or [])
        if str(item.get("result", "")).upper() == "FAIL"
    ]
    return metrics
