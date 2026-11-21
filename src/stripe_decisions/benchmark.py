"""Reproducible benchmark: model-only intent classification vs. policy outcomes via the real Business Rule.

Phases (kept separate on purpose):
  A. model-only  - each adapter classifies every dataset message `repeats` times (direct API calls,
                   latency = adapter call time only; no Stripe, no Conductor).
  B. policy      - every distinct (facts fixture, intent, requestedCents) combination -- for the gold
                   label and for every model prediction -- is evaluated by the SAME pinned Business Rule
                   on Orkes via the side-effect-free `billing_policy_eval` workflow. No Python policy.
  C. workflow    - optional sample of full `billing_rules_ai` / `billing_rules_only` executions with
                   fixture facts, to report end-to-end workflow latency separately from model latency.

Raw rows go to results/runs/<run_id>/ (gitignored); the aggregate report goes to results/benchmark/.
"""

from __future__ import annotations

import json
import statistics
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .adapters import get_classifier
from .config import REPO_ROOT, RESULTS_DIR
from .facts import load_fixture_facts
from .schemas import INTENT_DESCRIPTIONS

DATASET = REPO_ROOT / "benchmark" / "dataset.v1.jsonl"
PRICING = json.loads((REPO_ROOT / "benchmark" / "pricing.json").read_text())
INTENTS = list(INTENT_DESCRIPTIONS)
AUTOMATED = {"APPROVE_REFUND", "EXPLAIN_CHARGE", "NO_ADDITIONAL_REFUND"}
ESCALATED = {"HUMAN_REVIEW", "INVESTIGATE"}
# Larger number = more permissive financial outcome. Used only to flag policy-violating outcomes.
PERMISSIVENESS = {"INVESTIGATE": 0, "HUMAN_REVIEW": 0, "EXPLAIN_CHARGE": 1, "NO_ADDITIONAL_REFUND": 1, "APPROVE_REFUND": 2}


def load_dataset(path: Path = DATASET) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 1)


def price_for(provider: str, model: str) -> dict | None:
    table = PRICING.get(provider, {})
    return table.get(model) or next((v for k, v in table.items() if model.startswith(k)), None)


def est_cost(provider: str, model: str, usage: dict | None) -> float | None:
    p = price_for(provider, model)
    if not p or not usage:
        return None
    return (usage.get("input_tokens", 0) * p["input_usd_per_mtok"] + usage.get("output_tokens", 0) * p["output_usd_per_mtok"]) / 1e6


# ---------------- phase A ----------------
def run_model_only(providers: list[str], repeats: int, max_cost_usd: float, concurrency: int, out_dir: Path,
                   limit: int | None = None) -> list[dict]:
    rows = load_dataset()[:limit] if limit else load_dataset()
    results: list[dict] = []
    spent = 0.0
    for provider in providers:
        clf = get_classifier(provider, max_retries=0)
        # Probe one call to project cost before spending more.
        probe = clf.classify(rows[0]["message"])
        unit = est_cost(clf.provider, probe.model, probe.usage) or 0.0
        projected = unit * len(rows) * repeats
        print(f"[{provider}] model={probe.model} projected est. cost ${projected:.4f} for {len(rows)}x{repeats} calls")
        if spent + projected > max_cost_usd:
            raise SystemExit(f"Projected cost ${spent + projected:.4f} exceeds --max-cost-usd {max_cost_usd}; aborting.")
        for rep in range(1, repeats + 1):
            def one(row, rep=rep):
                r = clf.classify(row["message"])
                return {"provider": provider, "rep": rep, "id": row["id"], "gold_intent": row["gold_intent"],
                        "difficulty": row["difficulty"], **r.model_dump(mode="json"),
                        "est_cost_usd": est_cost(r.provider, r.model, r.usage)}
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                batch = list(pool.map(one, rows))
            results.extend(batch)
            spent += sum(b["est_cost_usd"] or 0 for b in batch)
            acc = sum(b["intent"] == b["gold_intent"] for b in batch) / len(batch)
            print(f"[{provider}] rep {rep}/{repeats}: accuracy {acc:.3f}, est. spent so far ${spent:.4f}")
    with (out_dir / "classifications.jsonl").open("w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    return results


# ---------------- phase B ----------------
def evaluate_policy(combos: set[tuple[str, str, int]], out_dir: Path, concurrency: int = 4) -> dict[tuple, dict]:
    from .conductor_api import ConductorREST
    api = ConductorREST()

    def one(combo):
        fixture, intent, cents = combo
        facts = load_fixture_facts(fixture, requested_cents=cents).model_dump(mode="json")
        run = api.execute("billing_policy_eval", 1, {"facts": facts, "intent": intent}, wait_seconds=20)
        wid = run.get("workflowId") or run.get("id")
        if run.get("status") == "RUNNING":
            run = api.wait_for(wid, 60)
        out = run.get("output") or {}
        return combo, {"workflowId": wid, "status": run.get("status"), **(out.get("decision") or {}),
                       "normalizedIntent": out.get("intent")}

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        decided = dict(pool.map(one, sorted(combos)))
    with (out_dir / "policy_evaluations.jsonl").open("w") as f:
        for (fx, intent, cents), d in sorted(decided.items()):
            f.write(json.dumps({"facts_fixture": fx, "intent": intent, "requestedCents": cents, **d}) + "\n")
    return decided


# ---------------- phase C ----------------
def run_workflow_sample(providers: list[str], sample: int, out_dir: Path) -> list[dict]:
    from .conductor_api import ConductorREST
    api = ConductorREST()
    rows = load_dataset()[::max(1, len(load_dataset()) // sample)][:sample]
    records = []
    for mode in ["rules", *providers]:
        for row in rows:
            case = {"caseId": row["id"], "customerId": "cus_fixture" + row["facts_fixture"],
                    "requestedCents": row["requestedCents"], "currency": "usd"}
            if mode == "rules":
                name, inp = "billing_rules_only", {"case": {**case, "claimType": row["gold_intent"]}}
            else:
                name, inp = "billing_rules_ai", {"case": {**case, "message": row["message"]}, "classifier": mode}
            inp.update({"factsSource": "fixture", "fixtureId": row["facts_fixture"], "dryRun": True})
            run = api.execute(name, 1, inp, wait_seconds=30)
            wid = run.get("workflowId") or run.get("id")
            full = api.wait_for(wid, 90)
            tasks = {t["referenceTaskName"]: t for t in full.get("tasks", [])}
            dur = lambda ref: (tasks[ref]["endTime"] - tasks[ref]["startTime"]) if ref in tasks and tasks[ref].get("endTime") else None
            records.append({"mode": mode, "id": row["id"], "workflowId": wid, "status": full.get("status"),
                            "workflow_ms": (full.get("endTime") or 0) - (full.get("startTime") or 0),
                            "classify_task_ms": dur("classify_ref"), "facts_task_ms": dur("facts_ref"),
                            "business_rule_ms": dur("policy_ref"),
                            "decision": ((full.get("output") or {}).get("decision") or {}).get("decision")})
            print(f"[workflow:{mode}] {row['id']} {wid} {records[-1]['status']} {records[-1]['workflow_ms']}ms")
    with (out_dir / "workflow_runs.jsonl").open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return records


# ---------------- metrics ----------------
def summarize(cls_rows: list[dict], decided: dict, dataset: list[dict], wf_rows: list[dict], meta: dict) -> dict:
    by_id = {r["id"]: r for r in dataset}
    gold_decision = {r["id"]: decided[(r["facts_fixture"], r["gold_intent"], r["requestedCents"])] for r in dataset}
    report = {"meta": meta, "dataset": {"cases": len(dataset), "by_intent": Counter(r["gold_intent"] for r in dataset),
                                        "by_difficulty": Counter(r["difficulty"] for r in dataset),
                                        "gold_decisions": Counter(d.get("decision") for d in gold_decision.values())},
              "providers": {}}
    for provider in sorted({r["provider"] for r in cls_rows}):
        rows = [r for r in cls_rows if r["provider"] == provider]
        n = len(rows)
        confusion = {g: {p: 0 for p in INTENTS} for g in INTENTS}
        per_diff = defaultdict(lambda: [0, 0])
        e2e_ok = automated_ok = escalated = violations = 0
        for r in rows:
            confusion[r["gold_intent"]][r["intent"]] += 1
            per_diff[r["difficulty"]][0] += r["intent"] == r["gold_intent"]
            per_diff[r["difficulty"]][1] += 1
            ds = by_id[r["id"]]
            got = decided[(ds["facts_fixture"], r["intent"], ds["requestedCents"])].get("decision")
            want = gold_decision[r["id"]].get("decision")
            e2e_ok += got == want
            automated_ok += got == want and got in AUTOMATED
            escalated += got in ESCALATED
            violations += PERMISSIVENESS.get(got, 0) > PERMISSIVENESS.get(want, 0)
        by_case = defaultdict(list)
        for r in rows:
            by_case[r["id"]].append(r["intent"])
        reps = max(r["rep"] for r in rows)
        lat = [r["latencyMs"] for r in rows if not r.get("error")]
        costs = [r["est_cost_usd"] for r in rows if r["est_cost_usd"] is not None]
        usage = Counter()
        for r in rows:
            usage.update(r.get("usage") or {})
        total_cost = round(sum(costs), 6) if costs else None
        report["providers"][provider] = {
            "model_versions": sorted({r["model"] for r in rows}),
            "calls": n, "repeats": reps,
            "intent_accuracy": round(sum(r["intent"] == r["gold_intent"] for r in rows) / n, 4),
            "accuracy_by_difficulty": {k: round(a / b, 4) for k, (a, b) in sorted(per_diff.items())},
            "consistency_all_repeats_agree": round(sum(len(set(v)) == 1 for v in by_case.values()) / len(by_case), 4) if reps > 1 else None,
            "schema_valid_rate": round(sum(r["schemaValid"] for r in rows) / n, 4),
            "error_rate": round(sum(bool(r.get("error")) for r in rows) / n, 4),
            "end_to_end_decision_correct_rate": round(e2e_ok / n, 4),
            "correctly_automated_rate": round(automated_ok / n, 4),
            "escalation_rate": round(escalated / n, 4),
            "policy_violation_rate": round(violations / n, 4),
            "model_latency_ms": {"p50": pct(lat, 0.5), "p95": pct(lat, 0.95), "mean": round(statistics.mean(lat), 1) if lat else None},
            "usage_totals": dict(usage),
            "estimated_cost_usd_total": total_cost,
            "estimated_cost_per_correct_automated_decision_usd": round(total_cost / automated_ok, 8) if total_cost and automated_ok else None,
            "confidence_reported_by_provider": any(r.get("confidence") is not None for r in rows),
            "confusion_matrix": confusion,
        }
    if wf_rows:
        report["workflow_latency_ms"] = {}
        for mode in sorted({r["mode"] for r in wf_rows}):
            ms = [r["workflow_ms"] for r in wf_rows if r["mode"] == mode and r["status"] == "COMPLETED"]
            cl = [r["classify_task_ms"] for r in wf_rows if r["mode"] == mode and r["classify_task_ms"] is not None]
            br = [r["business_rule_ms"] for r in wf_rows if r["mode"] == mode and r["business_rule_ms"] is not None]
            report["workflow_latency_ms"][mode] = {"runs": len(ms), "workflow_p50": pct(ms, .5), "workflow_p95": pct(ms, .95),
                                                   "classify_task_p50": pct(cl, .5), "business_rule_task_p50": pct(br, .5)}
    return report


def run_benchmark(providers: list[str], repeats: int, max_cost_usd: float, concurrency: int,
                  workflow_sample: int, limit: int | None = None) -> Path:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = RESULTS_DIR / "runs" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    dataset = load_dataset()[:limit] if limit else load_dataset()
    t0 = time.time()
    cls_rows = run_model_only(providers, repeats, max_cost_usd, concurrency, out_dir, limit)
    combos = {(r["facts_fixture"], r["gold_intent"], r["requestedCents"]) for r in dataset}
    ds = {r["id"]: r for r in dataset}
    combos |= {(ds[r["id"]]["facts_fixture"], r["intent"], ds[r["id"]]["requestedCents"]) for r in cls_rows}
    print(f"[policy] evaluating {len(combos)} distinct (facts, intent, amount) combinations on Orkes")
    decided = evaluate_policy(combos, out_dir)
    wf_rows = run_workflow_sample(providers, workflow_sample, out_dir) if workflow_sample else []
    from .policy import load_release
    rel = load_release()
    meta = {"run_id": run_id, "date_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "dataset": DATASET.name, "dataset_rows": len(dataset), "repeats": repeats, "providers": providers,
            "policy_version": rel.policy_version, "rule_file_sha256": rel.sha256, "rule_file_url": rel.url,
            "adapter_settings": {"claude_effort": __import__("os").environ.get("ANTHROPIC_EFFORT", "low"),
                                 "retries": 0, "concurrency": concurrency},
            "pricing": PRICING, "wall_seconds": round(time.time() - t0, 1),
            "facts": "frozen fixtures normalized from real Stripe sandbox reads (fixtures/facts)",
            "policy_engine": "Orkes Conductor BUSINESS_RULE via billing_policy_eval v1"}
    report = summarize(cls_rows, decided, dataset, wf_rows, meta)
    bench_dir = RESULTS_DIR / "benchmark"
    bench_dir.mkdir(parents=True, exist_ok=True)
    path = bench_dir / f"report-{run_id}.json"
    path.write_text(json.dumps(report, indent=2, default=dict) + "\n")
    (bench_dir / "latest.json").write_text(json.dumps(report, indent=2, default=dict) + "\n")
    print(f"wrote {path.relative_to(REPO_ROOT)}")
    return path
