"""Command-line entry point: `uv run python -m stripe_decisions <command>`."""

from __future__ import annotations

import argparse
import json
import sys


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, default=str))


def cmd_stripe_whoami(_: argparse.Namespace) -> int:
    from .stripe_client import account_summary
    _print(account_summary())
    return 0


def cmd_seed(_: argparse.Namespace) -> int:
    from .seed import Seeder
    Seeder().run()
    return 0


def cmd_verify_seed(args: argparse.Namespace) -> int:
    from .verify import verify_seed
    report = verify_seed(write_fixtures=args.write_fixtures)
    _print(report["summary"])
    return 0 if report["summary"]["failed"] == 0 else 1


def cmd_conductor(args: argparse.Namespace) -> int:
    from .conductor_defs import TASK_DEFS, build_definitions, write_definitions
    from .policy import load_release, sha256

    if args.action == "generate":
        for p in write_definitions():
            print("wrote", p)
        return 0
    release = load_release()
    if release.sha256 != sha256():
        print("ERROR: rules CSV changed since the pinned release; publish a new policy version first.")
        return 2
    if not release.pinned:
        print("ERROR: ruleFileLocation is not pinned to an immutable commit URL (rules/policy_release.json).")
        return 2
    from .conductor_api import ConductorREST
    api = ConductorREST()
    defs = build_definitions(release)
    api.register_task_defs(TASK_DEFS)
    api.register_workflows(list(defs.values()))
    for name, wf in defs.items():
        got = api.get_workflow_def(name, wf["version"])
        print(f"registered {name} v{got['version']} ({len(got['tasks'])} tasks)  {api.s.ui_base}/workflowDef/{name}")
    print(f"Business Rule: {release.policy_version} sha256={release.sha256[:12]} url={release.url}")
    return 0


def cmd_workers(_: argparse.Namespace) -> int:
    from .workers import run_workers
    run_workers()
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    from .demo import run_demo
    report = run_demo(args.mode, args.scenario, offline=args.fixtures, dry_run=not args.execute_refunds)
    return 0 if report["matched"] == report["total"] else 1


def cmd_benchmark(args: argparse.Namespace) -> int:
    from .benchmark import run_benchmark
    run_benchmark([p.strip() for p in args.providers.split(",") if p.strip()], args.repeats, args.max_cost_usd,
                  args.concurrency, args.workflow_sample, args.limit)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from .report import render
    print(render(args.path))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="stripe_decisions", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("stripe-whoami", help="Show the (sandbox) Stripe account the key belongs to").set_defaults(fn=cmd_stripe_whoami)
    sub.add_parser("seed", help="Idempotently seed the Stripe sandbox with demo scenarios").set_defaults(fn=cmd_seed)
    vs = sub.add_parser("verify-seed", help="Read back Stripe and verify every scenario's facts")
    vs.add_argument("--write-fixtures", action="store_true", help="Snapshot normalized facts to fixtures/facts/")
    vs.set_defaults(fn=cmd_verify_seed)
    cd = sub.add_parser("conductor", help="Generate or register Conductor definitions")
    cd.add_argument("action", choices=["generate", "register"])
    cd.set_defaults(fn=cmd_conductor)
    sub.add_parser("workers", help="Run the Conductor workers (long-running)").set_defaults(fn=cmd_workers)
    dm = sub.add_parser("demo", help="Run the 8 scenarios through the real Conductor workflows")
    dm.add_argument("--mode", choices=["rules", "jev", "claude"], default="rules")
    dm.add_argument("--scenario", help="Run one scenario, e.g. S1")
    dm.add_argument("--fixtures", action="store_true", help="Use frozen fixture facts instead of live Stripe reads")
    dm.add_argument("--execute-refunds", action="store_true",
                    help="Pass dryRun=false (worker ALSO needs OSD_ALLOW_SANDBOX_REFUNDS=1)")
    dm.set_defaults(fn=cmd_demo)
    bm = sub.add_parser("benchmark", help="Model-only classification + Orkes policy outcomes + workflow latency")
    bm.add_argument("--providers", default="jev,claude")
    bm.add_argument("--repeats", type=int, default=3)
    bm.add_argument("--max-cost-usd", type=float, default=1.00, help="Abort if projected model spend exceeds this")
    bm.add_argument("--concurrency", type=int, default=4)
    bm.add_argument("--workflow-sample", type=int, default=10, help="Full workflow runs per mode (0 to skip)")
    bm.add_argument("--limit", type=int, help="Only the first N dataset rows (smoke test)")
    bm.set_defaults(fn=cmd_benchmark)
    rp = sub.add_parser("report", help="Render a benchmark report (markdown + charts) from recorded results")
    rp.add_argument("path", nargs="?", default="results/benchmark/latest.json")
    rp.set_defaults(fn=cmd_report)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
