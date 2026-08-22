"""CLI tối thiểu cho lớp Research.

Chạy độc lập ở giai đoạn đầu để không ảnh hưởng CLI BRAIN hiện tại.
Khi lớp nghiên cứu ổn định, các lệnh này sẽ được hợp nhất vào cli.py.

Ví dụ:
    python research_cli.py init --db data/research.db
    python research_cli.py project-create --db data/research.db --name Momentum --family Momentum
    python research_cli.py project-list --db data/research.db
"""

from __future__ import annotations

import argparse
import json

from research_models import Experiment, Hypothesis, ResearchProject
from research_store import ResearchStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Quản lý Research Project của Alpha Forge")
    parser.add_argument("--db", default="research.db", help="Đường dẫn SQLite cho research layer")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Khởi tạo kho research")

    project = sub.add_parser("project-create")
    project.add_argument("--name", required=True)
    project.add_argument("--family", default="")
    project.add_argument("--objective", default="")
    project.add_argument("--region", default="USA")
    project.add_argument("--universe", default="TOP3000")
    project.add_argument("--delay", type=int, default=1)
    project.add_argument("--notes", default="")

    sub.add_parser("project-list")

    hypothesis = sub.add_parser("hypothesis-create")
    hypothesis.add_argument("--research-id", type=int, required=True)
    hypothesis.add_argument("--statement", required=True)
    hypothesis.add_argument("--intuition", default="")
    hypothesis.add_argument("--direction", default="")
    hypothesis.add_argument("--horizon", default="")
    hypothesis.add_argument("--notes", default="")

    hlist = sub.add_parser("hypothesis-list")
    hlist.add_argument("--research-id", type=int, required=True)

    experiment = sub.add_parser("experiment-create")
    experiment.add_argument("--hypothesis-id", type=int, required=True)
    experiment.add_argument("--name", required=True)
    experiment.add_argument("--objective", default="")
    experiment.add_argument("--base-expression", default="")
    experiment.add_argument("--variable", default="")
    experiment.add_argument("--expected-effect", default="")
    experiment.add_argument("--notes", default="")

    elist = sub.add_parser("experiment-list")
    elist.add_argument("--hypothesis-id", type=int, required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = ResearchStore(args.db)

    if args.command == "init":
        print(f"Đã khởi tạo research database: {args.db}")
        return 0

    if args.command == "project-create":
        project_id = store.create_project(
            ResearchProject(
                name=args.name,
                family=args.family,
                objective=args.objective,
                region=args.region,
                universe=args.universe,
                delay=args.delay,
                notes=args.notes,
            )
        )
        print(json.dumps({"id": project_id, "name": args.name}, ensure_ascii=False))
        return 0

    if args.command == "project-list":
        print(json.dumps(store.list_projects(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "hypothesis-create":
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

    if args.command == "hypothesis-list":
        print(json.dumps(store.list_hypotheses(args.research_id), ensure_ascii=False, indent=2))
        return 0

    if args.command == "experiment-create":
        experiment_id = store.create_experiment(
            Experiment(
                hypothesis_id=args.hypothesis_id,
                name=args.name,
                objective=args.objective,
                base_expression=args.base_expression,
                variable_changed=args.variable,
                expected_effect=args.expected_effect,
                notes=args.notes,
            )
        )
        print(json.dumps({"id": experiment_id, "name": args.name}, ensure_ascii=False))
        return 0

    if args.command == "experiment-list":
        print(json.dumps(store.list_experiments(args.hypothesis_id), ensure_ascii=False, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
