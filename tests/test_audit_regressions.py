"""Kiểm thử hồi quy cho các lỗi phát hiện trong đợt audit mã nguồn.

Mỗi kiểm thử ở đây gắn với một lỗi cụ thể đã sửa. Mục đích là lỗi đó không
quay lại, chứ không phải kiểm tra tính năng nói chung.
"""

import ast
import importlib
import pathlib
import sqlite3

import pytest

from alphaforge.history.fingerprint import fingerprint
from alphaforge.research.memory import (
    MIN_SAMPLE,
    SATURATION_REFERENCE,
    ResearchMemory,
    ResearchProfile,
)
from alphaforge.research.models import AlphaLineage
from alphaforge.research.store import ResearchStore
from alphaforge.storage.db import SCHEMA_COLUMNS, Database, Status

REPO = pathlib.Path(__file__).resolve().parent.parent
SETTINGS = {"region": "USA", "universe": "TOP3000", "delay": 1}


# ----------------------------------------------------------------------
# Lỗi 1: mô đun chết history/submitted.py gọi thẳng session.get
# ----------------------------------------------------------------------
def test_dead_submitted_module_is_gone():
    """Mô đun cũ bỏ qua BrainClient nên không được phép tồn tại trở lại."""
    assert not (REPO / "alphaforge" / "history" / "submitted.py").exists()


def test_no_module_calls_http_outside_brain_client():
    """Chỉ brain/client.py được phép gọi requests trực tiếp.

    Mọi lệnh gọi mạng khác phải đi qua BrainClient để thừa hưởng đăng nhập lại,
    lùi thời gian khi bị giới hạn tần suất và thử lại khi máy chủ lỗi.
    """
    allowed = {
        pathlib.Path("alphaforge/brain/client.py"),
        # Nhà cung cấp mô hình ngôn ngữ gọi dịch vụ riêng, không phải BRAIN.
        pathlib.Path("alphaforge/llm/ollama.py"),
        pathlib.Path("alphaforge/llm/claude.py"),
    }
    offenders = []
    for path in (REPO / "alphaforge").rglob("*.py"):
        relative = path.relative_to(REPO)
        if relative in allowed:
            continue
        source = path.read_text(encoding="utf-8")
        for name in ("requests.get", "requests.post", "session.get", "session.post"):
            if name in source:
                offenders.append(f"{relative}: {name}")
    assert offenders == [], f"Gọi mạng ngoài BrainClient: {offenders}"


# ----------------------------------------------------------------------
# Lỗi 2: phụ thuộc vòng trong lớp mô hình ngôn ngữ
# ----------------------------------------------------------------------
def test_llm_base_does_not_import_providers():
    """base không được biết tới nhà cung cấp cụ thể, nếu không sẽ thành vòng."""
    source = (REPO / "alphaforge" / "llm" / "base.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert not {"claude", "ollama"} & imported


def test_llm_dependency_graph_is_acyclic():
    """Đồ thị phụ thuộc trong gói llm phải là một chiều."""
    edges = {}
    for path in (REPO / "alphaforge" / "llm").glob("*.py"):
        name = path.stem
        tree = ast.parse(path.read_text(encoding="utf-8"))
        targets = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
                targets.add(node.module.split(".")[0])
        edges[name] = targets

    visiting, done = set(), set()

    def visit(node):
        if node in visiting:
            pytest.fail(f"Phụ thuộc vòng tại {node}")
        if node in done or node not in edges:
            return
        visiting.add(node)
        for target in edges[node]:
            visit(target)
        visiting.discard(node)
        done.add(node)

    for node in list(edges):
        visit(node)


def test_get_provider_still_importable_from_package_root():
    """Vị trí mới không được phá đường nhập cũ."""
    module = importlib.import_module("alphaforge.llm")
    assert callable(module.get_provider)


# ----------------------------------------------------------------------
# Lỗi 3: jinja2 khai báo nhưng không dùng
# ----------------------------------------------------------------------
def test_declared_dependencies_are_actually_imported():
    requirements = [
        line.split(">=")[0].split("[")[0].strip()
        for line in (REPO / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    import_names = {
        "requests": "requests", "PyYAML": "yaml", "python-dotenv": "dotenv",
        "fastapi": "fastapi", "uvicorn": "uvicorn", "jinja2": "jinja2",
    }
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in (REPO / "alphaforge").rglob("*.py")
    )
    unused = []
    for name in requirements:
        module = import_names.get(name, name)
        # Cả hai dạng đều là dùng: "import X" và "from X import ...".
        if f"import {module}" not in source and f"from {module}" not in source:
            unused.append(name)
    assert unused == [], f"Khai báo nhưng không dùng: {unused}"


# ----------------------------------------------------------------------
# Lỗi 4: thiếu generation_seed nên không tái lập được lô sinh
# ----------------------------------------------------------------------
def test_generation_seed_exists_in_both_tables():
    for table in ("alphas", "alpha_lineage"):
        columns = [name for name, _ in SCHEMA_COLUMNS[table]]
        assert "generation_seed" in columns, f"{table} thiếu generation_seed"


def test_generation_seed_round_trips_through_alpha_record(db):
    db.add_alphas(
        ["rank(close)"], SETTINGS,
        generation_strategy="template", generation_seed=4242,
    )
    record = db.fetch_by_status(Status.PENDING)[0]
    assert record.generation_seed == 4242


def test_generation_seed_round_trips_through_lineage(db):
    store = ResearchStore(db)
    store.save_lineage(
        AlphaLineage(alpha_id="A1", generation_strategy="mutate", generation_seed=7)
    )
    assert store.get_lineage("A1")["generation_seed"] == 7


def test_reproducibility_fields_are_complete(db):
    """Toàn bộ thông tin cần để tái hiện một lô sinh phải nằm trong bản ghi."""
    db.add_alphas(
        ["rank(close)"], SETTINGS,
        generation_strategy="template", generation_seed=99, run_id=db.create_run("lo1", "template"),
    )
    record = db.fetch_by_status(Status.PENDING)[0]
    assert record.settings == SETTINGS
    assert record.generation_strategy == "template"
    assert record.generation_seed == 99
    assert record.run_id is not None


# ----------------------------------------------------------------------
# Lỗi 5: mức bão hòa chỉ là cờ đúng sai, không so sánh được
# ----------------------------------------------------------------------
def test_saturation_is_zero_for_untouched_family():
    assert ResearchProfile(family="F", count=0, passed=0).saturation == 0.0


def test_saturation_rises_with_effort_and_falls_with_success():
    many_failures = ResearchProfile(family="F", count=SATURATION_REFERENCE, passed=0)
    many_successes = ResearchProfile(
        family="G", count=SATURATION_REFERENCE, passed=SATURATION_REFERENCE // 2
    )
    few_attempts = ResearchProfile(family="H", count=4, passed=0)

    assert many_failures.saturation == 1.0
    assert many_successes.saturation < many_failures.saturation
    assert few_attempts.saturation < many_failures.saturation


def test_saturation_is_capped_at_one():
    profile = ResearchProfile(family="F", count=SATURATION_REFERENCE * 10, passed=0)
    assert profile.saturation == 1.0


def test_confidence_reflects_sample_size():
    """Nhóm ít quan sát phải có độ tin cậy thấp để không kết luận mạnh."""
    assert ResearchProfile(family="F", count=1).confidence < 0.2
    assert ResearchProfile(family="F", count=MIN_SAMPLE * 4).confidence == 1.0


def test_describe_family_exposes_saturation_and_confidence(db):
    expression = "rank(ts_mean(close, 20))"
    family = fingerprint(expression)["family"]
    connection = db.connect()
    try:
        for index in range(12):
            connection.execute(
                "INSERT INTO historical_alphas (alpha_id, status, expression, sharpe,"
                " family, source, imported_at) VALUES (?, 'FAILED', ?, 0.1, ?,"
                " 'brain_submitted', 'now')",
                (f"A{index}", expression, family),
            )
    finally:
        connection.close()
    result = ResearchMemory(db).describe_family(expression)
    assert 0.0 < result["saturation"] <= 1.0
    assert 0.0 < result["confidence"] <= 1.0
    assert result["is_saturated"] is True


# ----------------------------------------------------------------------
# Di trú kho: chạy hai lần không được lỗi, không được mất dữ liệu
# ----------------------------------------------------------------------
def test_migration_is_idempotent_and_preserves_rows(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE historical_alphas (
            alpha_id TEXT PRIMARY KEY, submitted TEXT NOT NULL, status TEXT,
            expression TEXT, sharpe REAL, source TEXT NOT NULL,
            imported_at TEXT NOT NULL
        )
        """
    )
    connection.executemany(
        "INSERT INTO historical_alphas VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(f"OLD{i}", "2026-08-01", "ACTIVE", "rank(close)", 1.0 + i, "brain_submitted", "x")
         for i in range(5)],
    )
    connection.commit()
    connection.close()

    # Mở ba lần: lần đầu di trú, hai lần sau không được lỗi và không đổi dữ liệu.
    for _ in range(3):
        database = Database(path)

    connection = database.connect()
    try:
        rows = connection.execute(
            "SELECT alpha_id, sharpe FROM historical_alphas ORDER BY alpha_id"
        ).fetchall()
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(historical_alphas)")
        }
        indexes = {
            row["name"] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
    finally:
        connection.close()

    assert len(rows) == 5, "Di trú làm mất hoặc nhân bản dữ liệu"
    assert [row["alpha_id"] for row in rows] == [f"OLD{i}" for i in range(5)]
    assert {"family", "template", "windows_json", "raw_json"} <= columns
    assert {"idx_hist_family", "idx_hist_submitted"} <= indexes


def test_migration_creates_every_expected_table(tmp_path):
    database = Database(tmp_path / "fresh.sqlite3")
    connection = database.connect()
    try:
        tables = {
            row["name"] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    finally:
        connection.close()
    assert set(SCHEMA_COLUMNS) <= tables


def test_foreign_keys_are_enforced(db):
    """Khóa ngoại phải thật sự bật, nếu không quan hệ nghiên cứu sẽ rời rạc."""
    connection = db.connect()
    try:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO hypotheses (research_id, statement, created_at, updated_at)"
                " VALUES (999999, 'mo coi', 'now', 'now')"
            )
    finally:
        connection.close()


# ----------------------------------------------------------------------
# Bất biến: số placeholder phải khớp số tham số trong mọi lệnh INSERT
# ----------------------------------------------------------------------
def test_every_insert_binds_the_right_number_of_values():
    """Bắt cả lớp lỗi lệch placeholder thay vì từng trường hợp một.

    Lỗi này đã xảy ra hai lần khi thêm cột mới: câu lệnh SQL được cập nhật
    nhưng tuple giá trị thì không. Nó chỉ lộ ra lúc chạy, và chỉ ở đúng nhánh
    mã gọi tới câu lệnh đó.
    """
    import sqlite3

    from alphaforge.storage.db import SCHEMA, INDEXES

    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(SCHEMA)
        connection.executescript(INDEXES)
        offenders = []
        for path in (REPO / "alphaforge").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                # Tìm lời gọi execute(sql, params) có SQL là hằng chuỗi và
                # params là tuple hằng, tức là đếm được tĩnh.
                if not isinstance(node, ast.Call):
                    continue
                if getattr(node.func, "attr", "") != "execute" or len(node.args) != 2:
                    continue
                sql_node, params_node = node.args
                if not isinstance(sql_node, ast.Constant) or not isinstance(sql_node.value, str):
                    continue
                if not isinstance(params_node, (ast.Tuple, ast.List)):
                    continue
                sql = sql_node.value
                if "INSERT" not in sql.upper():
                    continue
                # Bỏ qua tuple có phần tử mở rộng, không đếm tĩnh được.
                if any(isinstance(element, ast.Starred) for element in params_node.elts):
                    continue
                placeholders = sql.count("?")
                supplied = len(params_node.elts)
                if placeholders != supplied:
                    offenders.append(
                        f"{path.relative_to(REPO)}:{node.lineno} "
                        f"{placeholders} dấu hỏi nhưng {supplied} giá trị"
                    )
        assert offenders == [], "Lệch placeholder: " + "; ".join(offenders)
    finally:
        connection.close()
