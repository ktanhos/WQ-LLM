"""Kiểm thử lớp dòng lệnh.

Các lệnh cần mạng được kiểm thử bằng cách thay BrainClient, không lệnh nào
trong tệp này gọi máy chủ thật.
"""

import json

import pytest

from alphaforge.cli import build_parser, main
from alphaforge.config import Settings
from alphaforge.storage.db import Database, Status
from conftest import FakeBrainClient, make_alpha, page


@pytest.fixture()
def settings(tmp_path) -> Settings:
    """Cấu hình trỏ vào kho tạm, không đụng tới kho thật của người dùng."""
    return Settings(
        raw={
            "simulation": {"settings": {"region": "USA", "universe": "TOP3000", "delay": 1}},
            "scoring": {"min_sharpe": 1.25},
            "generator": {"max_expression_length": 480},
            "correlation": {"max_self_correlation": 0.7},
        },
        db_path=tmp_path / "cli.sqlite3",
    )


def run(monkeypatch, settings, argv, client=None):
    """Chạy một lệnh CLI với cấu hình tạm và client giả lập."""
    monkeypatch.setattr("alphaforge.cli.load_settings", lambda *_: settings)
    if client is not None:
        monkeypatch.setattr("alphaforge.cli.BrainClient", lambda *_a, **_k: client)
    return main(argv)


# ----------------------------------------------------------------------
# Cấu trúc lệnh
# ----------------------------------------------------------------------
def test_all_documented_commands_are_registered():
    choices = build_parser()._subparsers._group_actions[0].choices
    expected = {
        "auth", "fields", "generate", "run", "score", "correlate",
        "report", "status", "reset", "templates", "web", "history", "research",
    }
    assert expected <= set(choices)


def test_history_has_scan_and_report_subcommands():
    parser = build_parser()
    assert parser.parse_args(["history", "scan"]).subcommand == "scan"
    assert parser.parse_args(["history", "report"]).subcommand == "report"


def test_research_has_project_hypothesis_experiment_subcommands():
    parser = build_parser()
    assert parser.parse_args(["research", "project", "list"]).subcommand == "project"
    assert parser.parse_args(
        ["research", "hypothesis", "list", "--research-id", "1"]
    ).subcommand == "hypothesis"
    assert parser.parse_args(
        ["research", "experiment", "list", "--hypothesis-id", "1"]
    ).subcommand == "experiment"


# ----------------------------------------------------------------------
# Lệnh không cần mạng
# ----------------------------------------------------------------------
def test_status_prints_all_statuses(monkeypatch, settings, capsys):
    assert run(monkeypatch, settings, ["status"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[Status.PENDING] == 0


def test_templates_lists_catalogue(monkeypatch, settings, capsys):
    assert run(monkeypatch, settings, ["templates"]) == 0
    assert "rank_reversal" in capsys.readouterr().out


def test_generate_dry_run_writes_nothing(monkeypatch, settings, capsys):
    code = run(
        monkeypatch, settings,
        ["generate", "--limit", "5", "--fields", "close", "volume", "--dry-run", "--seed", "1"],
    )
    assert code == 0
    assert capsys.readouterr().out.strip()
    assert Database(settings.db_path).counts_by_status()[Status.PENDING] == 0


def test_generate_persists_expressions_with_fingerprints(monkeypatch, settings):
    code = run(
        monkeypatch, settings,
        ["generate", "--limit", "5", "--fields", "close", "volume", "--seed", "1"],
    )
    assert code == 0
    db = Database(settings.db_path)
    records = db.fetch_by_status(Status.PENDING, limit=50)
    assert records
    assert all(record.family for record in records)
    assert all(record.generation_strategy == "template" for record in records)


def test_generate_no_memory_flag_disables_research_context(monkeypatch, settings):
    code = run(
        monkeypatch, settings,
        ["generate", "--limit", "5", "--fields", "close", "volume",
         "--seed", "1", "--no-memory"],
    )
    assert code == 0


def test_invalid_argument_returns_error_code(monkeypatch, settings, capsys):
    """Chiến lược mutate không có alpha gốc thì phải báo lỗi, không sập."""
    code = run(monkeypatch, settings, ["generate", "--strategy", "mutate", "--limit", "5"])
    assert code == 1


# ----------------------------------------------------------------------
# Lệnh lịch sử
# ----------------------------------------------------------------------
def test_history_scan_stores_and_reports_diagnostics(monkeypatch, settings, capsys):
    client = FakeBrainClient([
        page([
            make_alpha("A1", expression="rank(ts_mean(returns, 20))"),
            make_alpha("A2", submitted=None),
        ]),
    ])
    code = run(
        monkeypatch, settings,
        ["history", "scan", "--start-date", "2026-08-01", "--end-date", "2026-08-31"],
        client=client,
    )
    assert code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["inserted"] == 2
    # Bản ghi thiếu ngày được giữ lại và được nhắc tới ở luồng lỗi chuẩn.
    assert payload["diagnostics"]["missing_date"] == 1
    assert "thiếu ngày nộp" in captured.err


def test_history_report_runs_on_empty_history(monkeypatch, settings, capsys):
    assert run(monkeypatch, settings, ["history", "report"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["total"] == 0


def test_history_report_writes_to_file(monkeypatch, settings, tmp_path):
    out = tmp_path / "report.json"
    code = run(monkeypatch, settings, ["history", "report", "--out", str(out)])
    assert code == 0
    assert json.loads(out.read_text(encoding="utf-8"))["total"] == 0


def test_history_gaps_requires_history(monkeypatch, settings, capsys):
    assert run(monkeypatch, settings, ["history", "gaps"]) == 1
    assert "history scan" in capsys.readouterr().err


def test_history_check_reports_unresearched_structure(monkeypatch, settings, capsys):
    code = run(monkeypatch, settings, ["history", "check", "rank(ts_mean(close, 20))"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["researched"] is False
    assert payload["fingerprint"]["family"]
    assert payload["fingerprint"]["windows"] == [20]


def test_history_check_finds_prior_research(monkeypatch, settings, capsys):
    client = FakeBrainClient([page([make_alpha("A1", expression="rank(ts_mean(returns, 20))")])])
    run(
        monkeypatch, settings,
        ["history", "scan", "--start-date", "2026-08-01", "--end-date", "2026-08-31"],
        client=client,
    )
    capsys.readouterr()
    # Cùng họ cấu trúc, chỉ khác cửa sổ.
    code = run(monkeypatch, settings, ["history", "check", "rank(ts_mean(returns, 60))"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["researched"] is True
    assert payload["tested_windows"] == [20]


def test_history_explain_reports_missing_llm_without_crashing(monkeypatch, settings, capsys):
    """Mô hình ngôn ngữ là tùy chọn: thiếu nó thì báo rõ chứ không sập."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ALPHAFORGE_LLM_PROVIDER", raising=False)
    code = run(monkeypatch, settings, ["history", "explain", "--task", "summary"])
    assert code == 3
    assert "mô hình ngôn ngữ" in capsys.readouterr().err.lower()


# ----------------------------------------------------------------------
# Lệnh nghiên cứu
# ----------------------------------------------------------------------
def test_research_project_create_and_list(monkeypatch, settings, capsys):
    assert run(
        monkeypatch, settings,
        ["research", "project", "create", "--name", "Momentum", "--family", "Momentum"],
    ) == 0
    project_id = json.loads(capsys.readouterr().out)["id"]

    assert run(monkeypatch, settings, ["research", "project", "list"]) == 0
    projects = json.loads(capsys.readouterr().out)
    assert projects[0]["id"] == project_id
    # Thiết lập mô phỏng mặc định được thừa kế để tái lập được về sau.
    assert projects[0]["region"] == "USA"


def test_research_project_create_requires_name(monkeypatch, settings, capsys):
    assert run(monkeypatch, settings, ["research", "project", "create"]) == 1
    assert "--name" in capsys.readouterr().err


def test_research_full_chain_project_hypothesis_experiment(monkeypatch, settings, capsys):
    run(monkeypatch, settings, ["research", "project", "create", "--name", "P1"])
    project_id = json.loads(capsys.readouterr().out)["id"]

    run(
        monkeypatch, settings,
        ["research", "hypothesis", "create", "--research-id", str(project_id),
         "--statement", "Volume xac nhan momentum"],
    )
    hypothesis_id = json.loads(capsys.readouterr().out)["id"]

    run(
        monkeypatch, settings,
        ["research", "experiment", "create", "--hypothesis-id", str(hypothesis_id),
         "--name", "Lookback test", "--variable", "lookback"],
    )
    capsys.readouterr()

    run(monkeypatch, settings, ["research", "experiment", "list",
                                "--hypothesis-id", str(hypothesis_id)])
    experiments = json.loads(capsys.readouterr().out)
    assert experiments[0]["name"] == "Lookback test"
    # Thiết lập mô phỏng được lưu kèm thí nghiệm để tái lập.
    assert experiments[0]["settings"]["region"] == "USA"


def test_research_lineage_of_unknown_alpha_is_empty(monkeypatch, settings, capsys):
    assert run(monkeypatch, settings, ["research", "lineage", "KHONG-CO"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ancestry"] == []
    assert payload["children"] == []
