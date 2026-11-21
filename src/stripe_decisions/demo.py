"""Run demo scenarios through the real Conductor workflows and record execution evidence."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from .conductor_api import ConductorREST
from .conductor_defs import WORKFLOW_VERSION
from .config import RESULTS_DIR
from .scenarios import demo_cases


def run_demo(mode: str, scenario: str | None = None, offline: bool = False, dry_run: bool = True) -> dict:
    api = ConductorREST()
    rows = []
    for c in demo_cases(scenario, offline):
        case = dict(c["case"])
        if mode == "rules":
            wf, inp = "billing_rules_only", {"case": {k: v for k, v in case.items() if k != "message"}}
        else:
            wf, inp = "billing_rules_ai", {"case": {k: v for k, v in case.items() if k != "claimType"},
                                           "classifier": mode}
        inp.update({"factsSource": "fixture" if offline else "stripe", "fixtureId": c["scenario"], "dryRun": dry_run})
        t0 = time.perf_counter()
        run = api.execute(wf, WORKFLOW_VERSION, inp, wait_seconds=30, correlation_id=f"demo-{mode}-{c['scenario']}")
        wid = run.get("workflowId") or run.get("id")
        run = api.wait_for(wid, timeout_s=90)  # full execution record (status, timings, tasks)
        wall_ms = (time.perf_counter() - t0) * 1000
        out = run.get("output") or {}
        decision = out.get("decision") or {}
        rows.append({
            "scenario": c["scenario"], "title": c["title"], "workflow": wf, "workflowId": wid,
            "status": run.get("status"), "expected": c["expected_decision"], "decision": decision.get("decision"),
            "ruleId": decision.get("ruleId"), "policyVersion": decision.get("policyVersion"),
            "intent": out.get("intent"), "intentSource": out.get("intentSource"),
            "refund": out.get("refund"), "classification": out.get("classification"),
            "factsStatus": (out.get("facts") or {}).get("factsStatus"), "factsSource": inp["factsSource"],
            "ruleFileLocation": out.get("ruleFileLocation"), "clientWallMs": round(wall_ms, 1),
            "serverDurationMs": (run.get("endTime") or 0) - (run.get("startTime") or 0) if run.get("endTime") else None,
        })
        r = rows[-1]
        ok = "OK " if r["decision"] == r["expected"] else "MISMATCH"
        print(f"{ok} {r['scenario']} intent={str(r['intent']):<22} decision={str(r['decision']):<20} rule={r['ruleId']} "
              f"wf={wid} status={r['status']}")
    report = {"mode": mode, "offline_facts": offline, "dryRun": dry_run, "ran_at": datetime.now(timezone.utc).isoformat(),
              "server": api.s.ui_base, "rows": rows,
              "matched": sum(r["decision"] == r["expected"] for r in rows), "total": len(rows)}
    out_dir = RESULTS_DIR / "demo"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"demo-{mode}{'-fixtures' if offline else ''}.json"
    path.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(f"{report['matched']}/{report['total']} matched expected decisions; wrote {path.relative_to(RESULTS_DIR.parent)}")
    return report
