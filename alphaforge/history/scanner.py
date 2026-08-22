"""Quét lịch sử alpha đã nộp trên BRAIN và đưa vào kho nghiên cứu.

Nguyên tắc của mô đun này: không bao giờ làm mất một alpha chỉ vì một bản ghi
hỏng. Một trường ngày thiếu, một khối chỉ số rỗng hay một bản ghi sai kiểu đều
được ghi nhận vào phần chẩn đoán và bản ghi vẫn được giữ lại. Nghiên cứu cần
biết cả những gì đã thử và thất bại, nên mất dữ liệu nguy hiểm hơn dữ liệu bẩn.

Mọi lệnh gọi mạng đều đi qua BrainClient để thừa hưởng đăng nhập lại khi hết
phiên, lùi thời gian khi bị giới hạn tần suất và thử lại khi máy chủ lỗi tạm
thời. Không gọi thẳng requests ở đây.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..brain.errors import BrainError
from ..storage.db import Database, utc_now
from .fingerprint import fingerprint

logger = logging.getLogger(__name__)

#: Máy chủ giới hạn số bản ghi mỗi trang. Yêu cầu cao hơn bị cắt bớt im lặng.
MAX_PAGE_LIMIT = 100
#: Chặn trên số trang để một phản hồi bất thường không tạo vòng lặp vô hạn.
MAX_PAGES = 500
#: Cửa sổ mặc định khi người dùng không chỉ định khoảng ngày.
DEFAULT_WINDOW_DAYS = 30


# ----------------------------------------------------------------------
# Xử lý ngày tháng
# ----------------------------------------------------------------------
def parse_timestamp(value: Any) -> Optional[datetime]:
    """Đọc dấu thời gian ISO 8601 của BRAIN và quy về UTC.

    Máy chủ trả về múi giờ kèm theo, ví dụ 2026-08-20T14:33:00-04:00. So sánh
    chuỗi trực tiếp sẽ sai khi hai bản ghi ở hai múi giờ khác nhau, nên phải
    quy đổi về UTC trước khi lấy phần ngày.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        # fromisoformat của Python 3.10 chưa nhận hậu tố Z.
        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            # Một số phản hồi chỉ có phần ngày.
            try:
                parsed = datetime.strptime(str(value)[:10], "%Y-%m-%d")
            except ValueError:
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def submitted_date(value: Any) -> Optional[str]:
    """Ngày nộp dạng YYYY-MM-DD theo UTC, hoặc None nếu không đọc được."""
    parsed = parse_timestamp(value)
    return parsed.date().isoformat() if parsed else None


def _parse_boundary(value: Any, fallback: date) -> date:
    if value is None:
        return fallback
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    parsed = parse_timestamp(value)
    return parsed.date() if parsed else fallback


# ----------------------------------------------------------------------
# Đọc dữ liệu từ máy chủ
# ----------------------------------------------------------------------
def _build_query(limit: int, offset: int, include_unsubmitted: bool) -> str:
    """Dựng chuỗi truy vấn.

    Bộ lọc của BRAIN dùng cú pháp `status!=UNSUBMITTED`. Nếu để thư viện HTTP
    mã hóa tham số thì dấu `!=` bị đổi thành `%21%3D` và máy chủ không nhận ra
    bộ lọc, dẫn tới trả về toàn bộ alpha. Vì vậy chuỗi truy vấn được dựng tay.
    """
    parts = [
        f"limit={int(limit)}",
        f"offset={int(offset)}",
        "order=-dateSubmitted",
        "hidden=false",
    ]
    if not include_unsubmitted:
        parts.append("status!=UNSUBMITTED")
    return "&".join(parts)


def _extract_row(alpha: Dict[str, Any]) -> Dict[str, Any]:
    """Chuyển một bản ghi alpha của máy chủ thành hàng phẳng.

    Mọi trường đều có thể vắng mặt. Hàm không ném lỗi khi thiếu dữ liệu, phần
    thiếu được để None để bước phân tích tự loại khỏi phép thống kê.
    """
    settings = alpha.get("settings") or {}
    regular = alpha.get("regular") or {}
    stats = alpha.get("is") or {}

    # Biểu thức có thể nằm ở regular.code hoặc ngay ở regular khi là chuỗi.
    if isinstance(regular, str):
        expression = regular
    else:
        expression = regular.get("code") or regular.get("expression") or ""

    raw_submitted = alpha.get("dateSubmitted") or alpha.get("dateCreated")

    return {
        "alpha_id": str(alpha.get("id") or "").strip() or None,
        "submitted": submitted_date(raw_submitted),
        "submitted_raw": raw_submitted,
        "status": alpha.get("status"),
        "region": settings.get("region"),
        "universe": settings.get("universe"),
        "delay": _as_int(settings.get("delay")),
        "neutralization": settings.get("neutralization"),
        "decay": _as_int(settings.get("decay")),
        "truncation": _as_float(settings.get("truncation")),
        "expression": expression,
        "sharpe": _as_float(stats.get("sharpe")),
        "fitness": _as_float(stats.get("fitness")),
        "returns": _as_float(stats.get("returns")),
        "turnover": _as_float(stats.get("turnover")),
        "margin": _as_float(stats.get("margin")),
        "drawdown": _as_float(stats.get("drawdown")),
        "long_count": _as_int(stats.get("longCount")),
        "short_count": _as_int(stats.get("shortCount")),
        "source": "brain_submitted",
        "raw": alpha,
    }


def fetch_submitted_alphas(
    client,
    start_date: Any = None,
    end_date: Any = None,
    *,
    limit: int = MAX_PAGE_LIMIT,
    max_records: Optional[int] = None,
    include_unsubmitted: bool = False,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Tải alpha đã nộp trong khoảng ngày, trả về (bản ghi, chẩn đoán).

    Bản ghi được khử trùng lặp theo mã alpha. Bản ghi thiếu ngày nộp vẫn được
    giữ lại vì không thể khẳng định nó nằm ngoài khoảng quan tâm.

    Phân trang dừng khi: hết dữ liệu, đã đi qua mốc ngày bắt đầu, chạm trần số
    bản ghi, hoặc máy chủ ngừng trả về bản ghi mới. Điều kiện cuối bảo vệ khỏi
    phản hồi bỏ qua tham số offset.
    """
    today = datetime.now(timezone.utc).date()
    start = _parse_boundary(start_date, today - timedelta(days=DEFAULT_WINDOW_DAYS))
    end = _parse_boundary(end_date, today)
    if start > end:
        raise ValueError(
            f"Ngày bắt đầu {start.isoformat()} muộn hơn ngày kết thúc {end.isoformat()}."
        )

    page_limit = max(1, min(int(limit), MAX_PAGE_LIMIT))
    collected: Dict[str, Dict[str, Any]] = {}
    diagnostics: Dict[str, Any] = {
        "pages": 0,
        "records_seen": 0,
        "missing_date": 0,
        "outside_window": 0,
        "malformed": 0,
        "duplicates": 0,
        "missing_alpha_id": 0,
        "stopped_because": "exhausted",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }

    offset = 0
    next_url: Optional[str] = None
    reached_start_boundary = False

    for page in range(MAX_PAGES):
        if next_url:
            target = next_url
        else:
            target = f"/users/self/alphas?{_build_query(page_limit, offset, include_unsubmitted)}"

        # Đi qua BrainClient nên tự động đăng nhập lại, lùi thời gian khi 429
        # và thử lại khi máy chủ trả 5xx.
        response = client.request("GET", target, expected=(200, 201))
        try:
            payload = response.json()
        except ValueError as exc:
            raise BrainError(
                f"Máy chủ trả về nội dung không phải JSON khi đọc lịch sử alpha: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            payload = {"results": payload if isinstance(payload, list) else []}

        results = payload.get("results")
        if not isinstance(results, list):
            results = []
        diagnostics["pages"] = page + 1
        diagnostics["records_seen"] += len(results)

        if not results:
            diagnostics["stopped_because"] = "empty_page"
            break

        new_on_page = 0
        for alpha in results:
            if not isinstance(alpha, dict):
                diagnostics["malformed"] += 1
                continue
            try:
                row = _extract_row(alpha)
            except Exception as exc:  # bản ghi hỏng không được làm hỏng cả lượt quét
                diagnostics["malformed"] += 1
                logger.warning("Bỏ qua một bản ghi lịch sử không đọc được: %s", exc)
                continue

            if not row["alpha_id"]:
                diagnostics["missing_alpha_id"] += 1
                continue

            if row["submitted"] is None:
                # Không có ngày thì không thể loại theo khoảng. Giữ lại để
                # người nghiên cứu tự quyết định thay vì âm thầm đánh mất.
                diagnostics["missing_date"] += 1
            else:
                current = date.fromisoformat(row["submitted"])
                if current < start:
                    reached_start_boundary = True
                    diagnostics["outside_window"] += 1
                    continue
                if current > end:
                    diagnostics["outside_window"] += 1
                    continue

            if row["alpha_id"] in collected:
                diagnostics["duplicates"] += 1
            else:
                new_on_page += 1
            # Bản ghi sau ghi đè bản ghi trước: chỉ số có thể đã được máy chủ
            # tính lại giữa hai trang.
            collected[row["alpha_id"]] = row

        if max_records is not None and len(collected) >= max_records:
            diagnostics["stopped_because"] = "max_records"
            break

        if reached_start_boundary:
            # Kết quả sắp xếp mới nhất trước, nên khi đã chạm bản ghi cũ hơn
            # mốc bắt đầu thì các trang sau chỉ còn cũ hơn nữa. Vẫn xử lý hết
            # trang hiện tại trước khi dừng, phòng khi thứ tự không hoàn hảo.
            diagnostics["stopped_because"] = "reached_start_date"
            break

        following = payload.get("next")
        if not following:
            diagnostics["stopped_because"] = "no_next_page"
            break
        if new_on_page == 0:
            # Máy chủ trả về trang mới nhưng không có bản ghi nào chưa thấy.
            # Nhiều khả năng tham số offset bị bỏ qua.
            diagnostics["stopped_because"] = "no_new_records"
            break

        next_url = following if isinstance(following, str) and following.startswith("http") else None
        offset += page_limit
    else:
        diagnostics["stopped_because"] = "max_pages"

    rows = sorted(
        collected.values(),
        key=lambda item: (item["submitted"] or "", item["alpha_id"]),
        reverse=True,
    )
    if max_records is not None:
        rows = rows[:max_records]
    diagnostics["collected"] = len(rows)
    return rows, diagnostics


# ----------------------------------------------------------------------
class HistoricalAlphaScanner:
    """Đưa lịch sử nộp alpha vào kho nghiên cứu hợp nhất."""

    def __init__(self, client, db: Database | str):
        self.client = client
        self.db = db if isinstance(db, Database) else Database(db)

    def scan(
        self,
        start_date: Any = None,
        end_date: Any = None,
        *,
        limit: int = MAX_PAGE_LIMIT,
        max_records: Optional[int] = None,
        include_unsubmitted: bool = False,
    ) -> Dict[str, Any]:
        rows, diagnostics = fetch_submitted_alphas(
            self.client,
            start_date,
            end_date,
            limit=limit,
            max_records=max_records,
            include_unsubmitted=include_unsubmitted,
        )
        stored = self.store(rows)
        result = {"scanned": len(rows), "diagnostics": diagnostics}
        result.update(stored)
        return result

    def store(self, rows: Iterable[Dict[str, Any]]) -> Dict[str, int]:
        """Ghi bản ghi vào kho, cập nhật khi chỉ số đã thay đổi.

        Toàn bộ lượt ghi nằm trong một giao dịch. Một bản ghi lỗi được bỏ qua
        và đếm riêng thay vì hủy cả lượt.
        """
        rows = list(rows)
        inserted = 0
        updated = 0
        unchanged = 0
        errors = 0
        now = utc_now()

        connection = self.db.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            for row in rows:
                try:
                    meta = fingerprint(row.get("expression") or "")
                    existing = connection.execute(
                        "SELECT sharpe, fitness, turnover, status FROM historical_alphas WHERE alpha_id = ?",
                        (row["alpha_id"],),
                    ).fetchone()

                    connection.execute(
                        """
                        INSERT INTO historical_alphas (
                            alpha_id, submitted, status, region, universe, delay,
                            neutralization, decay, truncation, expression,
                            sharpe, fitness, returns, turnover, margin, drawdown,
                            long_count, short_count, source, fingerprint, family,
                            template, normalized_expression, operators_json,
                            fields_json, windows_json, raw_json, imported_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                  ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(alpha_id) DO UPDATE SET
                            submitted = excluded.submitted,
                            status = excluded.status,
                            region = excluded.region,
                            universe = excluded.universe,
                            delay = excluded.delay,
                            neutralization = excluded.neutralization,
                            decay = excluded.decay,
                            truncation = excluded.truncation,
                            expression = excluded.expression,
                            sharpe = excluded.sharpe,
                            fitness = excluded.fitness,
                            returns = excluded.returns,
                            turnover = excluded.turnover,
                            margin = excluded.margin,
                            drawdown = excluded.drawdown,
                            long_count = excluded.long_count,
                            short_count = excluded.short_count,
                            fingerprint = excluded.fingerprint,
                            family = excluded.family,
                            template = excluded.template,
                            normalized_expression = excluded.normalized_expression,
                            operators_json = excluded.operators_json,
                            fields_json = excluded.fields_json,
                            windows_json = excluded.windows_json,
                            raw_json = excluded.raw_json,
                            imported_at = excluded.imported_at
                        """,
                        (
                            row["alpha_id"], row.get("submitted"), row.get("status"),
                            row.get("region"), row.get("universe"), row.get("delay"),
                            row.get("neutralization"), row.get("decay"),
                            row.get("truncation"), row.get("expression"),
                            row.get("sharpe"), row.get("fitness"), row.get("returns"),
                            row.get("turnover"), row.get("margin"), row.get("drawdown"),
                            row.get("long_count"), row.get("short_count"),
                            row.get("source", "brain_submitted"),
                            meta["family"], meta["family"], meta["template"],
                            meta["family_signature"],
                            json.dumps(meta["operators"], ensure_ascii=False),
                            json.dumps(meta["fields"], ensure_ascii=False),
                            json.dumps(meta["windows"], ensure_ascii=False),
                            json.dumps(row.get("raw") or {}, ensure_ascii=False, default=str),
                            now,
                        ),
                    )

                    if existing is None:
                        inserted += 1
                    elif _metrics_changed(existing, row):
                        updated += 1
                    else:
                        unchanged += 1
                except Exception as exc:
                    errors += 1
                    logger.warning(
                        "Không ghi được alpha lịch sử %s: %s", row.get("alpha_id"), exc
                    )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

        return {
            "inserted": inserted,
            "updated": updated,
            "unchanged": unchanged,
            "errors": errors,
            "stored": inserted + updated + unchanged,
        }

    # ------------------------------------------------------------------
    def load(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Đọc lại lịch sử đã nhập, dùng cho báo cáo và bảng theo dõi."""
        query = "SELECT * FROM historical_alphas WHERE 1 = 1"
        params: List[Any] = []
        if start_date:
            query += " AND submitted IS NOT NULL AND submitted >= ?"
            params.append(str(start_date))
        if end_date:
            query += " AND submitted IS NOT NULL AND submitted <= ?"
            params.append(str(end_date))
        query += " ORDER BY submitted DESC, alpha_id DESC"
        if limit:
            query += " LIMIT ?"
            params.append(int(limit))
        connection = self.db.connect()
        try:
            return [dict(row) for row in connection.execute(query, params).fetchall()]
        finally:
            connection.close()


def _metrics_changed(existing, row: Dict[str, Any]) -> bool:
    """Chỉ số do máy chủ tính lại theo thời gian nên cần phát hiện thay đổi."""
    for column in ("sharpe", "fitness", "turnover", "status"):
        if existing[column] != row.get(column):
            return True
    return False


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
