"""Điểm vào dòng lệnh của Alpha Forge."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from typing import Any, Dict, List, Optional

from .brain.client import BrainClient
from .brain.errors import BrainError
from .config import Settings, load_settings, deep_merge
from .generator.engine import GenerationRequest, GeneratorEngine
from .generator.templates import TEMPLATE_CATEGORIES, TEMPLATES
from .history.analyzer import analyze, research_gaps
from .history.fingerprint import fingerprint, find_duplicates
from .history.report import build_report, load_history
from .history.scanner import HistoricalAlphaScanner
from .llm import get_provider
from .pipeline.correlation import CorrelationChecker
from .pipeline.runner import SimulationRunner
from .pipeline.scorer import Scorer
from .research.memory import ResearchMemory
from .research.models import Experiment, Hypothesis, ResearchProject
from .research.store import ResearchStore
from .storage.db import Database, Status

logger = logging.getLogger("alphaforge")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alphaforge",
        description="Quy trình tạo, mô phỏng, chấm điểm và lọc alpha trên nền tảng WorldQuant BRAIN.",
    )
    parser.add_argument("--config", help="Đường dẫn tệp cấu hình YAML.")
    parser.add_argument("--verbose", action="store_true", help="In nhật ký chi tiết.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("auth", help="Kiểm tra đăng nhập và thông tin tài khoản.")

    p_fields = sub.add_parser("fields", help="Tải danh mục trường dữ liệu về kho cục bộ.")
    p_fields.add_argument("--region", default=None)
    p_fields.add_argument("--universe", default=None)
    p_fields.add_argument("--delay", type=int, default=None)
    p_fields.add_argument("--dataset", default=None, help="Mã tập dữ liệu, ví dụ fundamental6.")
    p_fields.add_argument("--search", default=None)
    p_fields.add_argument("--max-records", type=int, default=500)

    p_gen = sub.add_parser("generate", help="Sinh biểu thức và đưa vào hàng đợi.")
    p_gen.add_argument("--strategy", default="template", choices=["template", "pairwise", "mutate"])
    p_gen.add_argument("--limit", type=int, default=100)
    p_gen.add_argument("--tag", default="mac_dinh")
    p_gen.add_argument("--fields", nargs="*", default=None, help="Danh sách trường dữ liệu chỉ định thủ công.")
    p_gen.add_argument("--field-limit", type=int, default=60)
    p_gen.add_argument("--templates", nargs="*", default=None)
    p_gen.add_argument("--categories", nargs="*", default=None, choices=TEMPLATE_CATEGORIES)
    p_gen.add_argument("--seed", type=int, default=None)
    p_gen.add_argument("--region", default=None)
    p_gen.add_argument("--universe", default=None)
    p_gen.add_argument("--delay", type=int, default=None)
    p_gen.add_argument("--neutralization", default=None)
    p_gen.add_argument("--decay", type=int, default=None)
    p_gen.add_argument("--truncation", type=float, default=None)
    p_gen.add_argument("--dry-run", action="store_true", help="Chỉ in biểu thức, không ghi vào kho.")
    p_gen.add_argument(
        "--no-memory", action="store_true",
        help="Bỏ qua trí nhớ nghiên cứu, sinh theo phân phối đều như trước.",
    )

    p_run = sub.add_parser("run", help="Chạy mô phỏng cho các biểu thức đang chờ.")
    p_run.add_argument("--concurrency", type=int, default=None)
    p_run.add_argument("--limit", type=int, default=None)
    p_run.add_argument("--max-attempts", type=int, default=3)
    p_run.add_argument("--reset-stuck", action="store_true", help="Đưa bản ghi kẹt về hàng đợi trước khi chạy.")

    p_score = sub.add_parser("score", help="Chấm điểm lại toàn bộ alpha đã mô phỏng.")
    p_score.add_argument("--limit", type=int, default=5000)
    p_score.add_argument("--rescore-all", action="store_true", help="Chấm lại cả bản ghi đã có kết luận.")

    p_corr = sub.add_parser("correlate", help="Kiểm tra tương quan cho nhóm đã đạt ngưỡng.")
    p_corr.add_argument("--limit", type=int, default=100)
    p_corr.add_argument("--max-self", type=float, default=None)
    p_corr.add_argument("--check-prod", action="store_true")

    p_report = sub.add_parser("report", help="Xuất danh sách alpha xếp hạng cao nhất.")
    p_report.add_argument("--top", type=int, default=50)
    p_report.add_argument("--out", default=None, help="Đường dẫn tệp CSV. Bỏ trống thì in ra màn hình.")

    sub.add_parser("status", help="Xem nhanh số lượng theo trạng thái.")
    sub.add_parser("reset", help="Đưa các bản ghi kẹt ở trạng thái đang chạy về hàng đợi.")
    sub.add_parser("templates", help="Liệt kê danh mục mẫu biểu thức.")

    p_web = sub.add_parser("web", help="Mở bảng theo dõi.")
    p_web.add_argument("--host", default="127.0.0.1")
    p_web.add_argument("--port", type=int, default=8000)

    _add_history_commands(sub)
    _add_research_commands(sub)

    return parser


def _add_history_commands(sub) -> None:
    """Nhóm lệnh làm việc với lịch sử alpha đã nộp trên BRAIN."""
    p_history = sub.add_parser(
        "history", help="Trí nhớ nghiên cứu: quét và phân tích alpha đã nộp."
    )
    history_sub = p_history.add_subparsers(dest="subcommand", required=True)

    p_scan = history_sub.add_parser("scan", help="Tải lịch sử alpha đã nộp về kho.")
    p_scan.add_argument("--start-date", default=None, help="Ngày bắt đầu, dạng YYYY-MM-DD.")
    p_scan.add_argument("--end-date", default=None, help="Ngày kết thúc, dạng YYYY-MM-DD.")
    p_scan.add_argument("--page-limit", type=int, default=100, help="Số bản ghi mỗi trang, tối đa 100.")
    p_scan.add_argument("--max-records", type=int, default=None)
    p_scan.add_argument(
        "--include-unsubmitted", action="store_true",
        help="Lấy cả alpha chưa nộp. Mặc định chỉ lấy alpha đã nộp.",
    )

    p_hreport = history_sub.add_parser("report", help="Báo cáo phân tích lịch sử nghiên cứu.")
    p_hreport.add_argument("--start-date", default=None)
    p_hreport.add_argument("--end-date", default=None)
    p_hreport.add_argument("--out", default=None, help="Ghi JSON ra tệp thay vì in ra màn hình.")
    p_hreport.add_argument("--full", action="store_true", help="In cả phần phân phối chi tiết.")

    p_gaps = history_sub.add_parser("gaps", help="Liệt kê khoảng trống nghiên cứu đáng khảo sát.")
    p_gaps.add_argument("--limit", type=int, default=20)

    p_check = history_sub.add_parser(
        "check", help="Kiểm tra một biểu thức đã được nghiên cứu hay chưa."
    )
    p_check.add_argument("expression", help="Biểu thức cần kiểm tra.")
    p_check.add_argument("--duplicates", action="store_true", help="Liệt kê biểu thức tương tự.")

    p_explain = history_sub.add_parser(
        "explain", help="Nhờ mô hình ngôn ngữ diễn giải lịch sử nghiên cứu."
    )
    p_explain.add_argument(
        "--task", default="summary",
        choices=["summary", "hypotheses", "gaps"],
        help="Loại diễn giải cần sinh.",
    )


def _add_research_commands(sub) -> None:
    """Nhóm lệnh quản lý dự án, giả thuyết và thí nghiệm."""
    p_research = sub.add_parser(
        "research", help="Quản lý dự án nghiên cứu, giả thuyết và thí nghiệm."
    )
    research_sub = p_research.add_subparsers(dest="subcommand", required=True)

    p_project = research_sub.add_parser("project", help="Dự án nghiên cứu.")
    p_project.add_argument("action", choices=["create", "list"])
    p_project.add_argument("--name", default="")
    p_project.add_argument("--family", default="")
    p_project.add_argument("--objective", default="")
    p_project.add_argument("--region", default=None)
    p_project.add_argument("--universe", default=None)
    p_project.add_argument("--delay", type=int, default=None)
    p_project.add_argument("--notes", default="")
    p_project.add_argument("--status", default=None)

    p_hypothesis = research_sub.add_parser("hypothesis", help="Giả thuyết nghiên cứu.")
    p_hypothesis.add_argument("action", choices=["create", "list"])
    p_hypothesis.add_argument("--research-id", type=int, required=True)
    p_hypothesis.add_argument("--statement", default="")
    p_hypothesis.add_argument("--intuition", default="")
    p_hypothesis.add_argument("--direction", default="")
    p_hypothesis.add_argument("--horizon", default="")
    p_hypothesis.add_argument("--notes", default="")

    p_experiment = research_sub.add_parser("experiment", help="Thí nghiệm.")
    p_experiment.add_argument("action", choices=["create", "list"])
    p_experiment.add_argument("--hypothesis-id", type=int, required=True)
    p_experiment.add_argument("--name", default="")
    p_experiment.add_argument("--objective", default="")
    p_experiment.add_argument("--base-expression", default="")
    p_experiment.add_argument("--variable", default="")
    p_experiment.add_argument("--expected-effect", default="")
    p_experiment.add_argument("--notes", default="")

    p_lineage = research_sub.add_parser("lineage", help="Phả hệ của một alpha.")
    p_lineage.add_argument("alpha_id")


# ----------------------------------------------------------------------
def simulation_settings(settings: Settings, args: argparse.Namespace) -> Dict[str, Any]:
    base = dict(settings.simulation.get("settings", {}))
    overrides: Dict[str, Any] = {}
    for attr, key in (
        ("region", "region"),
        ("universe", "universe"),
        ("delay", "delay"),
        ("neutralization", "neutralization"),
        ("decay", "decay"),
        ("truncation", "truncation"),
    ):
        value = getattr(args, attr, None)
        if value is not None:
            overrides[key] = value
    return deep_merge(base, overrides)


def resolve_fields(
    db: Database, args: argparse.Namespace, sim_settings: Dict[str, Any]
) -> List[str]:
    if args.fields:
        return list(args.fields)
    rows = db.load_data_fields(
        region=str(sim_settings.get("region", "USA")),
        universe=str(sim_settings.get("universe", "TOP3000")),
        delay=int(sim_settings.get("delay", 1)),
        field_type="MATRIX",
        limit=args.field_limit,
    )
    fields = [row["id"] for row in rows if row.get("id")]
    if fields:
        return fields
    # Không có kho cục bộ thì dùng nhóm trường giá và khối lượng luôn tồn tại.
    logger.warning(
        "Kho trường dữ liệu rỗng, dùng tạm nhóm trường cơ bản. "
        "Hãy chạy lệnh fields để tải danh mục đầy đủ."
    )
    return ["close", "open", "high", "low", "volume", "returns", "vwap", "cap"]


# ----------------------------------------------------------------------
def cmd_auth(settings: Settings, args: argparse.Namespace) -> int:
    client = BrainClient(settings)
    client.authenticate()
    info = client.get_self()
    print("Đăng nhập thành công.")
    for key in ("id", "email", "status"):
        if key in info:
            print(f"  {key}: {info[key]}")
    return 0


def cmd_fields(settings: Settings, args: argparse.Namespace) -> int:
    db = Database(settings.db_path)
    sim = simulation_settings(settings, args)
    client = BrainClient(settings)
    fields = client.get_data_fields(
        region=str(sim.get("region", "USA")),
        universe=str(sim.get("universe", "TOP3000")),
        delay=int(sim.get("delay", 1)),
        instrument_type=str(sim.get("instrumentType", "EQUITY")),
        dataset_id=args.dataset,
        search=args.search,
        max_records=args.max_records,
    )
    saved = db.save_data_fields(
        fields,
        region=str(sim.get("region", "USA")),
        universe=str(sim.get("universe", "TOP3000")),
        delay=int(sim.get("delay", 1)),
    )
    print(f"Đã tải {len(fields)} trường dữ liệu, ghi vào kho {saved} bản ghi.")
    return 0


def cmd_generate(settings: Settings, args: argparse.Namespace) -> int:
    db = Database(settings.db_path)
    sim = simulation_settings(settings, args)
    fields = resolve_fields(db, args, sim)

    seed_expressions: List[str] = []
    if args.strategy == "mutate":
        seed_expressions = [
            record.expression for record in db.fetch_by_status(Status.PASSED, limit=200)
        ]
        if not seed_expressions:
            print("Chưa có alpha nào đạt ngưỡng để làm gốc biến đổi.", file=sys.stderr)
            return 1

    # Trí nhớ nghiên cứu hạ ưu tiên những họ cấu trúc đã thử nhiều mà kết quả
    # kém, và bỏ qua biểu thức đã từng xuất hiện. Tắt bằng --no-memory để quay
    # lại hành vi phân phối đều như trước.
    context = None
    if not args.no_memory:
        context = ResearchMemory(db).build_context(
            target_sharpe=float(settings.scoring.get("min_sharpe", 1.25) or 1.25),
        )

    request = GenerationRequest(
        strategy=args.strategy,
        limit=args.limit,
        fields=fields,
        templates=args.templates,
        categories=args.categories,
        seed=args.seed,
        max_length=int(settings.generator.get("max_expression_length", 480)),
        seed_expressions=seed_expressions,
        context=context,
    )
    engine = GeneratorEngine(request)
    expressions = engine.generate()

    if context is not None and any(engine.rejected.values()):
        print(
            "Trí nhớ nghiên cứu đã loại: "
            + ", ".join(f"{key}={value}" for key, value in engine.rejected.items() if value),
            file=sys.stderr,
        )

    if args.dry_run:
        for expression in expressions:
            print(expression)
        print(f"\nTổng cộng {len(expressions)} biểu thức. Không ghi vào kho.", file=sys.stderr)
        return 0

    run_id = db.create_run(tag=args.tag, strategy=args.strategy)
    # Vân tay được tính một lần ở đây rồi lưu kèm, để bước phân tích sau không
    # phải quét lại toàn bộ biểu thức.
    fingerprints = {}
    for expression in expressions:
        meta = fingerprint(expression)
        fingerprints[expression] = {
            "fingerprint": meta["family"],
            "family": meta["family"],
        }
    added = db.add_alphas(
        expressions, sim, run_id=run_id,
        generation_strategy=args.strategy,
        generation_seed=args.seed,
        fingerprints=fingerprints,
    )
    print(
        f"Sinh {len(expressions)} biểu thức, thêm mới {added} bản ghi. "
        f"Bỏ qua {len(expressions) - added} bản ghi trùng."
    )
    return 0


def cmd_run(settings: Settings, args: argparse.Namespace) -> int:
    db = Database(settings.db_path)
    if args.reset_stuck:
        print(f"Đã đưa {db.reset_stuck()} bản ghi kẹt về hàng đợi.")
    concurrency = args.concurrency or int(settings.api.get("concurrency", 3))
    client = BrainClient(settings)
    runner = SimulationRunner(
        client=client,
        db=db,
        scorer=Scorer(settings.scoring),
        concurrency=concurrency,
        max_attempts=args.max_attempts,
        progress_callback=lambda payload: print(
            f"  {payload['status']:<9} {payload['alpha_id']} "
            f"sharpe={payload['metrics'].get('sharpe')}"
        ),
    )
    print(f"Chạy với {concurrency} mô phỏng đồng thời. Dừng bằng tổ hợp phím ngắt.")
    try:
        stats = runner.run(limit=args.limit)
    except KeyboardInterrupt:
        runner.stop()
        print("\nĐã nhận lệnh dừng, đang thu dọn hàng đợi.")
        stats = dict(runner.stats)
    print(json.dumps(stats, ensure_ascii=False))
    return 0


def cmd_score(settings: Settings, args: argparse.Namespace) -> int:
    db = Database(settings.db_path)
    scorer = Scorer(settings.scoring)
    statuses = [Status.SIMULATED]
    if args.rescore_all:
        statuses += [Status.PASSED, Status.REJECTED]

    changed = 0
    for status in statuses:
        for record in db.fetch_by_status(status, limit=args.limit):
            verdict = scorer.evaluate(record.metrics)
            db.update_alpha(
                record.id,
                score=verdict.score,
                status=Status.PASSED if verdict.passed else Status.REJECTED,
                reject_reason=verdict.reason_text or None,
            )
            changed += 1
    counts = db.counts_by_status()
    print(f"Đã chấm lại {changed} bản ghi.")
    print(json.dumps(counts, ensure_ascii=False))
    return 0


def cmd_correlate(settings: Settings, args: argparse.Namespace) -> int:
    db = Database(settings.db_path)
    config = dict(settings.correlation)
    if args.max_self is not None:
        config["max_self_correlation"] = args.max_self
    checker = CorrelationChecker(
        client=BrainClient(settings),
        db=db,
        config=config,
        check_prod=args.check_prod,
    )
    stats = checker.run(limit=args.limit)
    print(json.dumps(stats, ensure_ascii=False))
    return 0


def cmd_report(settings: Settings, args: argparse.Namespace) -> int:
    db = Database(settings.db_path)
    rows = db.top_alphas(args.top)
    if not rows:
        print("Chưa có alpha nào được chấm điểm.")
        return 0

    header = [
        "alpha_id", "status", "score", "sharpe", "fitness",
        "turnover", "drawdown", "marginBps", "self_correlation", "expression",
    ]
    records = []
    for row in rows:
        metrics = row.get("metrics", {})
        records.append(
            [
                row.get("alpha_id"),
                row.get("status"),
                row.get("score"),
                metrics.get("sharpe"),
                metrics.get("fitness"),
                metrics.get("turnover"),
                metrics.get("drawdown"),
                metrics.get("marginBps"),
                row.get("self_correlation"),
                row.get("expression"),
            ]
        )

    if args.out:
        with open(args.out, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(records)
        print(f"Đã ghi {len(records)} dòng vào {args.out}.")
    else:
        print("\t".join(header))
        for record in records:
            print("\t".join("" if item is None else str(item) for item in record))
    return 0


def cmd_status(settings: Settings, args: argparse.Namespace) -> int:
    db = Database(settings.db_path)
    print(json.dumps(db.counts_by_status(), ensure_ascii=False, indent=2))
    return 0


def cmd_reset(settings: Settings, args: argparse.Namespace) -> int:
    db = Database(settings.db_path)
    print(f"Đã đưa {db.reset_stuck()} bản ghi về hàng đợi.")
    return 0


def cmd_templates(settings: Settings, args: argparse.Namespace) -> int:
    for template in TEMPLATES:
        print(f"{template.name:<24} {template.category:<16} {template.pattern}")
        if template.note:
            print(f"{'':<24} {template.note}")
    return 0


def cmd_web(settings: Settings, args: argparse.Namespace) -> int:
    import uvicorn

    from .web.app import create_app

    uvicorn.run(create_app(settings), host=args.host, port=args.port)
    return 0


# ----------------------------------------------------------------------
# Trí nhớ nghiên cứu
# ----------------------------------------------------------------------
def cmd_history(settings: Settings, args: argparse.Namespace) -> int:
    db = Database(settings.db_path)

    if args.subcommand == "scan":
        # Quét lịch sử là thao tác gọi mạng duy nhất trong nhóm lệnh này.
        client = BrainClient(settings)
        scanner = HistoricalAlphaScanner(client, db)
        result = scanner.scan(
            args.start_date,
            args.end_date,
            limit=args.page_limit,
            max_records=args.max_records,
            include_unsubmitted=args.include_unsubmitted,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        diagnostics = result.get("diagnostics", {})
        if diagnostics.get("missing_date"):
            print(
                f"Ghi chú: {diagnostics['missing_date']} bản ghi thiếu ngày nộp đã được "
                "giữ lại thay vì bỏ đi.",
                file=sys.stderr,
            )
        return 0

    if args.subcommand == "report":
        report = build_report(db, args.start_date, args.end_date)
        if not args.full:
            # Phần phân phối rất dài, mặc định lược bớt cho dễ đọc trên màn hình.
            report.get("analysis", {}).pop("distribution", None)
        payload = json.dumps(report, ensure_ascii=False, indent=2, default=str)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as handle:
                handle.write(payload)
            print(f"Đã ghi báo cáo vào {args.out}.")
        else:
            print(payload)
        return 0

    if args.subcommand == "gaps":
        rows = load_history(db)
        if not rows:
            print("Kho lịch sử rỗng. Hãy chạy: alphaforge history scan", file=sys.stderr)
            return 1
        gaps = research_gaps(analyze(rows), limit=args.limit)
        print(json.dumps(gaps, ensure_ascii=False, indent=2, default=str))
        return 0

    if args.subcommand == "check":
        memory = ResearchMemory(db)
        result = memory.describe_family(args.expression)
        result["fingerprint"] = {
            key: value
            for key, value in fingerprint(args.expression).items()
            if key in ("exact", "family", "template", "operators", "fields", "windows", "depth")
        }
        if args.duplicates:
            known = [
                row["expression"]
                for row in load_history(db)
                if row.get("expression")
            ]
            result["similar"] = find_duplicates(args.expression, known)[:20]
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0

    if args.subcommand == "explain":
        provider = get_provider(settings.llm)
        if not provider.is_available():
            print(
                "Mô hình ngôn ngữ chưa sẵn sàng. Đây là tính năng tùy chọn, "
                "mọi lệnh khác vẫn dùng được bình thường.",
                file=sys.stderr,
            )
        rows = load_history(db)
        analysis = analyze(rows)
        if args.task == "summary":
            response = provider.summarize_research(analysis)
        elif args.task == "hypotheses":
            response = provider.propose_hypotheses(analysis)
        else:
            response = provider.analyze_gaps(research_gaps(analysis))
        if not response.available:
            print(response.error or "Không gọi được mô hình ngôn ngữ.", file=sys.stderr)
            return 3
        print(response.text)
        return 0

    return 1


# ----------------------------------------------------------------------
# Lớp nghiên cứu
# ----------------------------------------------------------------------
def cmd_research(settings: Settings, args: argparse.Namespace) -> int:
    store = ResearchStore(Database(settings.db_path))
    sim = settings.simulation.get("settings", {})

    if args.subcommand == "project":
        if args.action == "create":
            if not args.name:
                print("Cần --name để tạo dự án.", file=sys.stderr)
                return 1
            project_id = store.create_project(
                ResearchProject(
                    name=args.name,
                    family=args.family,
                    objective=args.objective,
                    region=args.region or str(sim.get("region", "USA")),
                    universe=args.universe or str(sim.get("universe", "TOP3000")),
                    delay=args.delay if args.delay is not None else int(sim.get("delay", 1)),
                    notes=args.notes,
                )
            )
            print(json.dumps({"id": project_id, "name": args.name}, ensure_ascii=False))
            return 0
        print(json.dumps(store.list_projects(args.status), ensure_ascii=False, indent=2))
        return 0

    if args.subcommand == "hypothesis":
        if args.action == "create":
            if not args.statement:
                print("Cần --statement để tạo giả thuyết.", file=sys.stderr)
                return 1
            hypothesis_id = store.create_hypothesis(
                Hypothesis(
                    research_id=args.research_id,
                    statement=args.statement,
                    economic_intuition=args.intuition,
                    expected_direction=args.direction,
                    expected_horizon=args.horizon,
                    notes=args.notes,
                )
            )
            print(json.dumps({"id": hypothesis_id}, ensure_ascii=False))
            return 0
        print(
            json.dumps(store.list_hypotheses(args.research_id), ensure_ascii=False, indent=2)
        )
        return 0

    if args.subcommand == "experiment":
        if args.action == "create":
            if not args.name:
                print("Cần --name để tạo thí nghiệm.", file=sys.stderr)
                return 1
            experiment_id = store.create_experiment(
                Experiment(
                    hypothesis_id=args.hypothesis_id,
                    name=args.name,
                    objective=args.objective,
                    base_expression=args.base_expression,
                    variable_changed=args.variable,
                    expected_effect=args.expected_effect,
                    # Lưu kèm thiết lập mô phỏng đang dùng để tái lập được về sau.
                    settings=dict(sim),
                    notes=args.notes,
                )
            )
            print(json.dumps({"id": experiment_id, "name": args.name}, ensure_ascii=False))
            return 0
        print(
            json.dumps(store.list_experiments(args.hypothesis_id), ensure_ascii=False, indent=2)
        )
        return 0

    if args.subcommand == "lineage":
        chain = store.ancestry(args.alpha_id)
        children = store.get_children(args.alpha_id)
        print(
            json.dumps(
                {"alpha_id": args.alpha_id, "ancestry": chain, "children": children},
                ensure_ascii=False, indent=2, default=str,
            )
        )
        return 0

    return 1


COMMANDS = {
    "auth": cmd_auth,
    "fields": cmd_fields,
    "generate": cmd_generate,
    "run": cmd_run,
    "score": cmd_score,
    "correlate": cmd_correlate,
    "report": cmd_report,
    "status": cmd_status,
    "reset": cmd_reset,
    "templates": cmd_templates,
    "web": cmd_web,
    "history": cmd_history,
    "research": cmd_research,
}


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # Thư viện HTTP ở mức DEBUG in ra địa chỉ và tiêu đề của mọi yêu cầu. Giữ
    # chúng ở mức INFO để --verbose không vô tình đưa dữ liệu phiên vào nhật ký.
    for noisy in ("urllib3", "requests", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.INFO)
    settings = load_settings(args.config)
    handler = COMMANDS[args.command]
    try:
        return handler(settings, args)
    except BrainError as exc:
        print(f"Lỗi khi làm việc với máy chủ BRAIN: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"Tham số không hợp lệ: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
