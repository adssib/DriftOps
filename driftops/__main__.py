"""One image, one CLI (ADR-0009): every component is a subcommand.

Commands:
    serve        model server on :8000
    simulate     replay a scenario against the server
    seed         load flights + outcomes, register champion v1
    champion     build champion v1's bundle from 2018
    mlflow       the MLflow tracking + registry server
    migrate      apply the schema (pre-upgrade hook)
    label-feed   move outcomes whose time has come
    monitor X    quality | drift | perf, one catch-up run
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="driftops", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("serve", help="model server")

    s = sub.add_parser("simulate", help="replay a scenario against the server")
    s.add_argument("--scenario", help="scenario YAML (default: $DRIFTOPS_SCENARIO)")

    s = sub.add_parser("seed", help="load flights + outcomes, register champion v1")
    s.add_argument("--months", default="2020-01:2020-06", help="first:last month, YYYY-MM")
    s.add_argument("--history-until", default="2020-03-10", help="outcomes before this are known")

    sub.add_parser("migrate", help="apply the schema (idempotent)")

    sub.add_parser("label-feed", help="move outcomes whose time has come")

    s = sub.add_parser("monitor", help="one monitor's catch-up run")
    s.add_argument("name", choices=["quality", "drift", "perf"])

    s = sub.add_parser("champion", help="build champion v1's bundle from 2018")
    s.add_argument("--out", default="data/bundles/v1")

    s = sub.add_parser("mlflow", help="MLflow tracking + registry server")
    s.add_argument("--port", type=int, default=5000)

    args = p.parse_args(argv)

    if args.command == "serve":
        from driftops.serving.app import run

        return run()
    if args.command == "simulate":
        from driftops.simulator import run

        return run(args.scenario)
    if args.command == "seed":
        from driftops.seed import run

        return run(args.months, args.history_until)
    if args.command == "migrate":
        from driftops import db, log
        from driftops.config import Settings

        lg = log.setup("migrate")
        with db.connect(Settings.from_env().db_url) as conn:
            db.apply_schema(conn)
        lg.info("schema_applied")
        return 0
    if args.command == "label-feed":
        from driftops.feeder import run

        return run()
    if args.command == "monitor":
        from driftops.monitors import run

        return run(args.name)
    if args.command == "champion":
        from driftops.champion import run

        return run(args.out)
    if args.command == "mlflow":
        from driftops.registry import serve

        return serve(args.port)
    return 2


if __name__ == "__main__":
    sys.exit(main())
