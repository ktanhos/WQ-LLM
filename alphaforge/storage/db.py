"""Kho SQLite hợp nhất cho toàn bộ Alpha Forge.

Một tệp cơ sở dữ liệu duy nhất chứa cả pipeline mô phỏng lẫn trí nhớ nghiên cứu.
Lý do hợp nhất thay vì tách nhiều kho:

    * Câu hỏi nghiên cứu quan trọng nhất là "cấu trúc này đã thử chưa" luôn cần
      nối alpha đang sinh với alpha đã submit và với experiment đã thiết kế.
      Ba thứ đó nằm ở ba tệp khác nhau thì phải nối thủ công trong Python.
    * SQLite khóa theo tệp. Nhiều tệp không làm giảm tranh chấp vì tiến trình
      vẫn ghi tuần tự, đổi lại mất khả năng JOIN và mất tính nguyên tử khi một
      thao tác chạm vào hai lớp.

Chế độ WAL được bật để nhiều luồng đọc song song với một luồng ghi. Việc lấy
bản ghi khỏi hàng đợi dùng giao dịch IMMEDIATE nên hai luồng không thể nhận
cùng một bản ghi.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Status:
    """Trạng thái vòng đời của một biểu thức.

    Dùng hằng chuỗi thay vì Enum để giá trị đi thẳng vào SQLite và làm khóa
    từ điển mà không cần chuyển đổi ở mọi nơi.
    """

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SIMULATED = "SIMULATED"
    PASSED = "PASSED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    SUBMITTED = "SUBMITTED"

    ALL = (
        PENDING, RUNNING, SIMULATED, PASSED, REJECTED, FAILED, SUBMITTED,
    )
    #: Trạng thái đã có kết luận, không quay lại hàng đợi.
    TERMINAL = (PASSED, REJECTED, FAILED, SUBMITTED)


@dataclass
class AlphaRecord:
    """Một biểu thức trong hàng đợi kèm ngữ cảnh tái lập được."""

    id: int
    expression: str
    settings: Dict[str, Any] = field(default_factory=dict)
    status: str = Status.PENDING
    attempts: int = 0
    alpha_id: Optional[str] = None
    score: Optional[float] = None
    metrics: Dict[str, Any] = field(default_factory=dict)
    self_correlation: Optional[float] = None
    prod_correlation: Optional[float] = None
    reject_reason: Optional[str] = None
    error: Optional[str] = None
    run_id: Optional[int] = None
    experiment_id: Optional[int] = None
    variant_id: Optional[int] = None
    parent_alpha_id: Optional[str] = None
    generation_strategy: str = ""
    #: Hạt giống ngẫu nhiên của lô sinh. Không có nó thì không tái lập được lô.
    generation_seed: Optional[int] = None
    fingerprint: Optional[str] = None
    family: Optional[str] = None


def expression_hash(expression: str, settings: Optional[Dict[str, Any]] = None) -> str:
    """Khóa khử trùng lặp.

    Khoảng trắng bị loại bỏ hoàn toàn chứ không chỉ rút gọn, vì trong FASTEXPR
    khoảng trắng giữa các token không mang ý nghĩa: "rank( close )" và
    "rank(close)" là cùng một biểu thức và không đáng tốn hai lượt mô phỏng.

    Cùng biểu thức nhưng khác thiết lập mô phỏng vẫn là hai thí nghiệm khác
    nhau, nên thiết lập cũng tham gia vào khóa.
    """
    cleaned = "".join((expression or "").split())
    payload = json.dumps(settings or {}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(f"{cleaned}||{payload}".encode("utf-8")).hexdigest()


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tag TEXT NOT NULL DEFAULT '',
    strategy TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alphas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    expression TEXT NOT NULL,
    expression_hash TEXT NOT NULL UNIQUE,
    settings_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'PENDING',
    attempts INTEGER NOT NULL DEFAULT 0,
    alpha_id TEXT,
    score REAL,
    metrics_json TEXT NOT NULL DEFAULT '{}',
    self_correlation REAL,
    prod_correlation REAL,
    reject_reason TEXT,
    error TEXT,
    run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL,
    experiment_id INTEGER,
    variant_id INTEGER,
    parent_alpha_id TEXT,
    generation_strategy TEXT NOT NULL DEFAULT '',
    generation_seed INTEGER,
    fingerprint TEXT,
    family TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);


CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT NOT NULL DEFAULT 'INFO',
    message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);


CREATE TABLE IF NOT EXISTS data_fields (
    id TEXT NOT NULL,
    region TEXT NOT NULL,
    universe TEXT NOT NULL,
    delay INTEGER NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    dataset_id TEXT NOT NULL DEFAULT '',
    field_type TEXT NOT NULL DEFAULT '',
    coverage REAL,
    user_count INTEGER,
    alpha_count INTEGER,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (id, region, universe, delay)
);

CREATE TABLE IF NOT EXISTS historical_alphas (
    alpha_id TEXT PRIMARY KEY,
    submitted TEXT,
    status TEXT,
    region TEXT,
    universe TEXT,
    delay INTEGER,
    neutralization TEXT,
    decay INTEGER,
    truncation REAL,
    expression TEXT,
    sharpe REAL,
    fitness REAL,
    returns REAL,
    turnover REAL,
    margin REAL,
    drawdown REAL,
    long_count INTEGER,
    short_count INTEGER,
    source TEXT NOT NULL DEFAULT 'brain_submitted',
    fingerprint TEXT,
    family TEXT,
    template TEXT,
    normalized_expression TEXT,
    operators_json TEXT NOT NULL DEFAULT '[]',
    fields_json TEXT NOT NULL DEFAULT '[]',
    windows_json TEXT NOT NULL DEFAULT '[]',
    raw_json TEXT NOT NULL DEFAULT '{}',
    imported_at TEXT NOT NULL
);


CREATE TABLE IF NOT EXISTS research_projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    family TEXT NOT NULL DEFAULT '',
    objective TEXT NOT NULL DEFAULT '',
    region TEXT NOT NULL DEFAULT 'USA',
    universe TEXT NOT NULL DEFAULT 'TOP3000',
    delay INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hypotheses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    research_id INTEGER NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
    statement TEXT NOT NULL,
    economic_intuition TEXT NOT NULL DEFAULT '',
    expected_direction TEXT NOT NULL DEFAULT '',
    expected_horizon TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'OPEN',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hypothesis_id INTEGER NOT NULL REFERENCES hypotheses(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    objective TEXT NOT NULL DEFAULT '',
    base_expression TEXT NOT NULL DEFAULT '',
    variable_changed TEXT NOT NULL DEFAULT '',
    expected_effect TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'DESIGNED',
    settings_json TEXT NOT NULL DEFAULT '{}',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiment_variants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id INTEGER NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    expression TEXT NOT NULL,
    parameters_json TEXT NOT NULL DEFAULT '{}',
    alpha_id TEXT,
    result_status TEXT NOT NULL DEFAULT 'PENDING',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alpha_lineage (
    alpha_id TEXT PRIMARY KEY,
    parent_alpha_id TEXT,
    research_id INTEGER REFERENCES research_projects(id) ON DELETE SET NULL,
    hypothesis_id INTEGER REFERENCES hypotheses(id) ON DELETE SET NULL,
    experiment_id INTEGER REFERENCES experiments(id) ON DELETE SET NULL,
    variant_id INTEGER REFERENCES experiment_variants(id) ON DELETE SET NULL,
    generation_strategy TEXT NOT NULL DEFAULT '',
    generation_seed INTEGER,
    mutation_type TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

"""

#: Chỉ mục được tạo sau khi di trú cột, vì một kho cũ có thể thiếu đúng cột mà
#: chỉ mục tham chiếu tới.
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_alphas_status ON alphas(status);
CREATE INDEX IF NOT EXISTS idx_alphas_score ON alphas(score);
CREATE INDEX IF NOT EXISTS idx_alphas_alpha_id ON alphas(alpha_id);
CREATE INDEX IF NOT EXISTS idx_alphas_fingerprint ON alphas(fingerprint);
CREATE INDEX IF NOT EXISTS idx_alphas_family ON alphas(family);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);
CREATE INDEX IF NOT EXISTS idx_hist_submitted ON historical_alphas(submitted);
CREATE INDEX IF NOT EXISTS idx_hist_fingerprint ON historical_alphas(fingerprint);
CREATE INDEX IF NOT EXISTS idx_hist_family ON historical_alphas(family);
CREATE INDEX IF NOT EXISTS idx_hist_status ON historical_alphas(status);
CREATE INDEX IF NOT EXISTS idx_hypotheses_research ON hypotheses(research_id);
CREATE INDEX IF NOT EXISTS idx_experiments_hypothesis ON experiments(hypothesis_id);
CREATE INDEX IF NOT EXISTS idx_variants_experiment ON experiment_variants(experiment_id);
CREATE INDEX IF NOT EXISTS idx_lineage_parent ON alpha_lineage(parent_alpha_id);
"""

#: Từ khóa mở đầu một ràng buộc bảng, không phải một cột.
_TABLE_CONSTRAINTS = (
    "primary", "foreign", "unique", "check", "constraint",
)

_CREATE_TABLE_RE = re.compile(
    r"CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\((.*?)\n\)", re.IGNORECASE | re.DOTALL
)


def _declared_columns(schema: str) -> Dict[str, List[tuple]]:
    """Đọc tên và định nghĩa cột từ chính chuỗi schema.

    Suy ra từ schema thay vì khai báo lại danh sách cột ở nơi thứ hai, nhờ vậy
    hai nơi không thể lệch nhau khi thêm cột mới.
    """
    tables: Dict[str, List[tuple]] = {}
    for match in _CREATE_TABLE_RE.finditer(schema):
        name = match.group(1)
        columns: List[tuple] = []
        depth = 0
        current = ""
        for char in match.group(2):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            if char == "," and depth == 0:
                columns.append(current)
                current = ""
            else:
                current += char
        columns.append(current)

        parsed: List[tuple] = []
        for definition in columns:
            cleaned = definition.strip()
            if not cleaned or cleaned.split()[0].lower() in _TABLE_CONSTRAINTS:
                continue
            parsed.append((cleaned.split()[0], cleaned))
        tables[name] = parsed
    return tables


SCHEMA_COLUMNS = _declared_columns(SCHEMA)


#: Cột của bảng alphas được phép cập nhật qua update_alpha.
_ALPHA_COLUMNS = {
    "expression", "status", "attempts", "alpha_id", "score", "self_correlation",
    "prod_correlation", "reject_reason", "error", "run_id", "experiment_id",
    "variant_id", "parent_alpha_id", "generation_strategy", "generation_seed",
    "fingerprint", "family",
}
#: Cột lưu dưới dạng JSON, nhận vào là đối tượng Python.
_ALPHA_JSON_COLUMNS = {"settings": "settings_json", "metrics": "metrics_json"}


class Database:
    """Truy cập kho SQLite hợp nhất.

    Mỗi thao tác mở một kết nối ngắn rồi đóng lại. Cách này tốn hơn việc giữ
    kết nối lâu dài nhưng tránh hoàn toàn lỗi dùng chung kết nối giữa các luồng,
    vốn là ràng buộc của sqlite3 trong Python.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._claim_lock = threading.Lock()
        self.initialize()

    # ------------------------------------------------------------------
    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        """Tạo bảng còn thiếu, bổ sung cột còn thiếu, rồi tạo chỉ mục.

        Thứ tự này quan trọng. Kho tạo bởi phiên bản trước có bảng
        historical_alphas thiếu các cột vân tay. Nếu tạo chỉ mục trước khi bổ
        sung cột thì lệnh tạo chỉ mục lỗi và cả kho không mở được.
        """
        connection = self.connect()
        try:
            connection.executescript(SCHEMA)
            self._migrate(connection)
            connection.executescript(INDEXES)
        finally:
            connection.close()

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        """Bổ sung cột còn thiếu vào bảng đã tồn tại từ phiên bản trước.

        SQLite chỉ cho phép thêm cột chứ không sửa cột, nhưng thêm cột là đủ
        cho mọi thay đổi cho tới nay. Ràng buộc NOT NULL bị lược bỏ khi cột
        không có giá trị mặc định, vì thêm cột NOT NULL không mặc định vào bảng
        đã có dữ liệu luôn thất bại.
        """
        for table, columns in SCHEMA_COLUMNS.items():
            existing = {
                str(row["name"])
                for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if not existing:
                continue
            for name, definition in columns:
                if name in existing:
                    continue
                # Khóa chính và ràng buộc duy nhất không thể thêm sau khi bảng
                # đã tồn tại, bỏ qua thay vì làm hỏng cả lượt khởi tạo.
                if re.search(r"PRIMARY\s+KEY|UNIQUE", definition, flags=re.IGNORECASE):
                    continue
                safe = definition
                if "DEFAULT" not in safe.upper():
                    safe = re.sub(r"\s+NOT\s+NULL", "", safe, flags=re.IGNORECASE)
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {safe}")

    # ------------------------------------------------------------------
    # Phiên chạy
    # ------------------------------------------------------------------
    def create_run(self, tag: str = "", strategy: str = "", notes: str = "") -> int:
        connection = self.connect()
        try:
            cursor = connection.execute(
                "INSERT INTO runs (tag, strategy, notes, created_at) VALUES (?, ?, ?, ?)",
                (tag, strategy, notes, utc_now()),
            )
            return int(cursor.lastrowid)
        finally:
            connection.close()

    # ------------------------------------------------------------------
    # Hàng đợi alpha
    # ------------------------------------------------------------------
    def add_alphas(
        self,
        expressions: Iterable[str],
        settings: Optional[Dict[str, Any]] = None,
        run_id: Optional[int] = None,
        *,
        experiment_id: Optional[int] = None,
        variant_id: Optional[int] = None,
        parent_alpha_id: Optional[str] = None,
        generation_strategy: str = "",
        generation_seed: Optional[int] = None,
        fingerprints: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> int:
        """Thêm biểu thức vào hàng đợi, bỏ qua bản ghi đã tồn tại.

        Trả về số bản ghi thực sự được thêm mới.
        """
        settings = dict(settings or {})
        settings_json = json.dumps(settings, sort_keys=True, ensure_ascii=False, default=str)
        now = utc_now()
        added = 0
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            for expression in expressions:
                cleaned = " ".join((expression or "").split())
                if not cleaned:
                    continue
                meta = (fingerprints or {}).get(cleaned) or {}
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO alphas (
                        expression, expression_hash, settings_json, status,
                        run_id, experiment_id, variant_id, parent_alpha_id,
                        generation_strategy, generation_seed, fingerprint, family,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cleaned, expression_hash(cleaned, settings), settings_json,
                        Status.PENDING, run_id, experiment_id, variant_id,
                        parent_alpha_id, generation_strategy, generation_seed,
                        meta.get("fingerprint"), meta.get("family"), now, now,
                    ),
                )
                added += cursor.rowcount or 0
            connection.execute("COMMIT")
            return added
        except Exception:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def claim_pending(self, count: int) -> List[AlphaRecord]:
        """Lấy tối đa `count` bản ghi khỏi hàng đợi và đánh dấu đang chạy.

        Dùng giao dịch IMMEDIATE kết hợp khóa trong tiến trình. Hai luồng gọi
        đồng thời không bao giờ nhận cùng một bản ghi: luồng thứ hai chờ khóa,
        khi vào thì các bản ghi đã chuyển sang RUNNING nên không còn khớp điều
        kiện lọc.
        """
        if count <= 0:
            return []
        with self._claim_lock:
            connection = self.connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                rows = connection.execute(
                    "SELECT id FROM alphas WHERE status = ? ORDER BY id LIMIT ?",
                    (Status.PENDING, int(count)),
                ).fetchall()
                ids = [int(row["id"]) for row in rows]
                if not ids:
                    connection.execute("COMMIT")
                    return []
                placeholders = ",".join("?" for _ in ids)
                connection.execute(
                    f"""
                    UPDATE alphas
                       SET status = ?, attempts = attempts + 1, updated_at = ?
                     WHERE id IN ({placeholders})
                    """,
                    (Status.RUNNING, utc_now(), *ids),
                )
                claimed = connection.execute(
                    f"SELECT * FROM alphas WHERE id IN ({placeholders}) ORDER BY id",
                    ids,
                ).fetchall()
                connection.execute("COMMIT")
                return [_to_record(row) for row in claimed]
            except Exception:
                connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()

    def update_alpha(self, alpha_row_id: int, **fields: Any) -> None:
        """Cập nhật một bản ghi. Khóa không hợp lệ bị bỏ qua có chủ đích."""
        assignments: List[str] = []
        values: List[Any] = []
        for key, value in fields.items():
            if key in _ALPHA_JSON_COLUMNS:
                assignments.append(f"{_ALPHA_JSON_COLUMNS[key]} = ?")
                values.append(json.dumps(value or {}, ensure_ascii=False, default=str))
            elif key in _ALPHA_COLUMNS:
                assignments.append(f"{key} = ?")
                values.append(value)
        if not assignments:
            return
        assignments.append("updated_at = ?")
        values.append(utc_now())
        values.append(int(alpha_row_id))
        connection = self.connect()
        try:
            connection.execute(
                f"UPDATE alphas SET {', '.join(assignments)} WHERE id = ?", values
            )
        finally:
            connection.close()

    def get_alpha(self, alpha_row_id: int) -> Optional[AlphaRecord]:
        connection = self.connect()
        try:
            row = connection.execute(
                "SELECT * FROM alphas WHERE id = ?", (int(alpha_row_id),)
            ).fetchone()
            return _to_record(row) if row else None
        finally:
            connection.close()

    def fetch_by_status(self, status: str, limit: int = 100) -> List[AlphaRecord]:
        connection = self.connect()
        try:
            rows = connection.execute(
                "SELECT * FROM alphas WHERE status = ? ORDER BY id LIMIT ?",
                (status, int(limit)),
            ).fetchall()
            return [_to_record(row) for row in rows]
        finally:
            connection.close()

    def counts_by_status(self) -> Dict[str, int]:
        """Số bản ghi theo trạng thái.

        Mọi trạng thái đều xuất hiện trong kết quả, kể cả khi bằng không, để
        nơi gọi không phải phân biệt giữa "không có bản ghi nào" và "khóa không
        tồn tại".
        """
        counts = {status: 0 for status in Status.ALL}
        connection = self.connect()
        try:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS total FROM alphas GROUP BY status"
            ).fetchall()
        finally:
            connection.close()
        for row in rows:
            counts[str(row["status"])] = int(row["total"])
        return counts

    def reset_stuck(self) -> int:
        """Đưa bản ghi kẹt ở RUNNING về hàng đợi sau khi tiến trình bị ngắt."""
        connection = self.connect()
        try:
            cursor = connection.execute(
                "UPDATE alphas SET status = ?, updated_at = ? WHERE status = ?",
                (Status.PENDING, utc_now(), Status.RUNNING),
            )
            return int(cursor.rowcount or 0)
        finally:
            connection.close()

    # ------------------------------------------------------------------
    # Truy vấn phục vụ báo cáo và bảng theo dõi
    # ------------------------------------------------------------------
    def top_alphas(self, limit: int = 25) -> List[Dict[str, Any]]:
        connection = self.connect()
        try:
            rows = connection.execute(
                """
                SELECT * FROM alphas
                 WHERE score IS NOT NULL
                 ORDER BY score DESC, id ASC
                 LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
            return [_to_dict(row) for row in rows]
        finally:
            connection.close()

    def recent_activity(self, limit: int = 25) -> List[Dict[str, Any]]:
        connection = self.connect()
        try:
            rows = connection.execute(
                "SELECT * FROM alphas ORDER BY updated_at DESC, id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
            return [_to_dict(row) for row in rows]
        finally:
            connection.close()

    def log_event(self, level: str, message: str) -> None:
        connection = self.connect()
        try:
            connection.execute(
                "INSERT INTO events (level, message, created_at) VALUES (?, ?, ?)",
                (str(level), str(message), utc_now()),
            )
        finally:
            connection.close()

    def recent_events(self, limit: int = 30) -> List[Dict[str, Any]]:
        connection = self.connect()
        try:
            rows = connection.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()

    # ------------------------------------------------------------------
    # Danh mục trường dữ liệu
    # ------------------------------------------------------------------
    def save_data_fields(
        self,
        fields: Iterable[Dict[str, Any]],
        *,
        region: str,
        universe: str,
        delay: int,
    ) -> int:
        now = utc_now()
        saved = 0
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            for item in fields:
                identifier = item.get("id")
                if not identifier:
                    continue
                dataset = item.get("dataset") or {}
                connection.execute(
                    """
                    INSERT INTO data_fields (
                        id, region, universe, delay, description, dataset_id,
                        field_type, coverage, user_count, alpha_count, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id, region, universe, delay) DO UPDATE SET
                        description = excluded.description,
                        dataset_id = excluded.dataset_id,
                        field_type = excluded.field_type,
                        coverage = excluded.coverage,
                        user_count = excluded.user_count,
                        alpha_count = excluded.alpha_count,
                        updated_at = excluded.updated_at
                    """,
                    (
                        str(identifier), region, universe, int(delay),
                        str(item.get("description") or ""),
                        str(dataset.get("id") if isinstance(dataset, dict) else dataset or ""),
                        str(item.get("type") or ""),
                        _maybe_float(item.get("coverage")),
                        _maybe_int(item.get("userCount")),
                        _maybe_int(item.get("alphaCount")),
                        now,
                    ),
                )
                saved += 1
            connection.execute("COMMIT")
            return saved
        except Exception:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def load_data_fields(
        self,
        *,
        region: str,
        universe: str,
        delay: int,
        field_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM data_fields WHERE region = ? AND universe = ? AND delay = ?"
        params: List[Any] = [region, universe, int(delay)]
        if field_type:
            query += " AND field_type = ?"
            params.append(field_type)
        query += " ORDER BY COALESCE(coverage, 0) DESC, id ASC LIMIT ?"
        params.append(int(limit))
        connection = self.connect()
        try:
            return [dict(row) for row in connection.execute(query, params).fetchall()]
        finally:
            connection.close()


# ----------------------------------------------------------------------
def _to_record(row: sqlite3.Row) -> AlphaRecord:
    return AlphaRecord(
        id=int(row["id"]),
        expression=str(row["expression"]),
        settings=_load_json(row["settings_json"]),
        status=str(row["status"]),
        attempts=int(row["attempts"]),
        alpha_id=row["alpha_id"],
        score=row["score"],
        metrics=_load_json(row["metrics_json"]),
        self_correlation=row["self_correlation"],
        prod_correlation=row["prod_correlation"],
        reject_reason=row["reject_reason"],
        error=row["error"],
        run_id=row["run_id"],
        experiment_id=row["experiment_id"],
        variant_id=row["variant_id"],
        parent_alpha_id=row["parent_alpha_id"],
        generation_strategy=str(row["generation_strategy"] or ""),
        generation_seed=row["generation_seed"],
        fingerprint=row["fingerprint"],
        family=row["family"],
    )


def _to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    item["settings"] = _load_json(item.pop("settings_json", "{}"))
    item["metrics"] = _load_json(item.pop("metrics_json", "{}"))
    return item


def _load_json(raw: Any) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _maybe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _maybe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
