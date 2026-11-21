"""Reproducible benchmark: model-only intent classification vs. policy outcomes via the real Business Rule.

Phases (kept separate on purpose):
  A. model-only  - each adapter classifies every dataset message `repeats` times (direct API calls,
                   latency = adapter call time only; no Stripe, no Conductor). Recorded to disk BEFORE
                   any policy is applied. Errors and invalid outputs are kept as rows (intent UNKNOWN).
  B. policy      - every distinct (facts fixture, intent, requestedCents) combination -- for the gold
                   label and for every model prediction -- is evaluated by the SAME pinned Business Rule
                   on Orkes via the side-effect-free `billing_policy_eval` workflow. No Python policy.
                   A failed evaluation is recorded and counted as an incorrect decision, never a match.
  C. workflow    - a stratified sample of full `billing_rules_only` / `billing_rules_ai` executions with
                   fixture facts. Reports end-to-end workflow latency, the in-workflow model intent and
                   the workflow decision separately; failed executions stay in every denominator.

Raw rows go to results/runs/<run_id>/; the aggregate report goes to results/benchmark/.
"""

from __future__ import annotations

import json
import os
import statistics
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .config import REPO_ROOT, RESULTS_DIR
from .facts import load_fixture_facts
from .schemas import INTENT_DESCRIPTIONS

DATASET = REPO_ROOT / "benchmark" / "dataset.v1.jsonl"
PRICING = json.loads((REPO_ROOT / "benchmark" / "pricing.json").read_text())
INTENTS = list(INTENT_DESCRIPTIONS)
DIFFICULTIES = ["obvious", "ambiguous", "contradictory", "adversarial"]
AUTOMATED = {"APPROVE_REFUND", "EXPLAIN_CHARGE", "NO_ADDITIONAL_REFUND"}
ESCALATED = {"HUMAN_REVIEW", "INVESTIGATE"}
# Larger number = more permissive financial outcome. Used only to flag policy-violating outcomes.
PERMISSIVENESS = {"INVESTIGATE": 0, "HUMAN_REVIEW": 0, "EXPLAIN_CHARGE": 1, "NO_ADDITIONAL_REFUND": 1, "APPROVE_REFUND": 2}
CONFIDENCE_BUCKETS = [(0.0, 0.5), (0.5, 0.8), (0.8, 0.95), (0.95, 1.0001)]


def load_dataset(path: Path = DATASET) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 1)


def rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def price_for(provider: str, model: str) -> dict | None:
    table = PRICING.get(provider, {})
    return table.get(model) or next((v for k, v in table.items() if model.startswith(k)), None)


def est_cost(provider: str, model: str, usage: dict | None) -> float | None:
    p = price_for(provider, model)
    if not p or not usage:
        return None
    return (usage.get("input_tokens", 0) * p["input_usd_per_mtok"] + usage.get("output_tokens", 0) * p["output_usd_per_mtok"]) / 1e6


def stratified_sample(dataset: list[dict], n: int) -> list[dict]:
    """Deterministic: round-robin across difficulty categories, in dataset order, so every category is covered."""
    if n <= 0:
        return []
    pools = {d: [r for r in dataset if r["difficulty"] == d] for d in DIFFICULTIES}
    out: list[dict] = []
    i = 0
    while len(out) < min(n, len(dataset)):
        for d in DIFFICULTIES:
            if i < len(pools[d]) and len(out) < n:
                out.append(pools[d][i])
        i += 1
    return sorted(out, key=lambda r: r["id"])


def project_cost(unit_cost: float, rows: int, repeats: int, workflow_sample: int) -> float:
    """Model spend for phase A (rows x repeats) plus phase C (one in-workflow classification per sampled row)."""
    return unit_cost * (rows * repeats + workflow_sample)


# ---------------- phase A ----------------
def run_model_only(providers: list[str], repeats: int, max_cost_usd: float, concurrency: int, out_dir: Path,
                   dataset: list[dict], workflow_sample: int, classifier_factory=None) -> list[dict]:
    if classifier_factory is None:
        from .adapters import get_classifier
        classifier_factory = lambda name: get_classifier(name, max_retries=0)  # noqa: E731
    results: list[dict] = []
    spent = 0.0
    probes = []
    for provider in providers:
        clf = classifier_factory(provider)
        # One probe call (recorded, excluded from metrics) to project cost before spending more.
        probe = clf.classify(dataset[0]["message"])
        unit = est_cost(clf.provider, probe.model, probe.usage) or 0.0
        spent += unit
        probes.append({"provider": provider, "rep": 0, "probe": True, **probe.model_dump(mode="json"), "est_cost_usd": unit})
        projected = project_cost(unit, len(dataset), repeats, workflow_sample)
        print(f"[{provider}] model={probe.model} projected est. cost ${projected:.4f} "
              f"({len(dataset)}x{repeats} model-only + {workflow_sample} in-workflow calls)")
        if spent + projected > max_cost_usd:
            raise SystemExit(f"Projected cost ${spent + projected:.4f} exceeds --max-cost-usd {max_cost_usd}; aborting.")
        for rep in range(1, repeats + 1):
            if spent > max_cost_usd:  # hard stop on actual recorded spend, not just the projection
                raise SystemExit(f"Recorded spend ${spent:.4f} exceeded --max-cost-usd {max_cost_usd}; stopping before "
                                 f"{provider} repeat {rep}. Partial rows are not written as a report.")
            started = datetime.now(timezone.utc).isoformat(timespec="seconds")

            def one(row, rep=rep, started=started):
                r = clf.classify(row["message"])
                return {"provider": provider, "rep": rep, "rep_started_utc": started, "id": row["id"],
                        "gold_intent": row["gold_intent"], "difficulty": row["difficulty"],
                        **r.model_dump(mode="json"), "est_cost_usd": est_cost(r.provider, r.model, r.usage)}
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                batch = list(pool.map(one, dataset))
            results.extend(batch)
            spent += sum(b["est_cost_usd"] or 0 for b in batch)
            acc = sum(b["intent"] == b["gold_intent"] for b in batch) / len(batch)
            errs = sum(bool(b.get("error")) for b in batch)
            print(f"[{provider}] rep {rep}/{repeats}: accuracy {acc:.3f}, errors {errs}, est. spent so far ${spent:.4f}")
    with (out_dir / "classifications.jsonl").open("w") as f:
        for r in probes + results:
            f.write(json.dumps(r) + "\n")
    return results


# ---------------- phase B ----------------
def evaluate_policy(combos: set[tuple[str, str, int]], out_dir: Path, concurrency: int = 4, api=None) -> dict[tuple, dict]:
    if api is None:
        from .conductor_api import ConductorREST
        api = ConductorREST()

    def one(combo):
        fixture, intent, cents = combo
        try:
            facts = load_fixture_facts(fixture, requested_cents=cents).model_dump(mode="json")
            run = api.execute("billing_policy_eval", 1, {"facts": facts, "intent": intent}, wait_seconds=20)
            wid = run.get("workflowId") or run.get("id")
            if run.get("status") in ("RUNNING", None):
                run = api.wait_for(wid, 60)
            out = run.get("output") or {}
            dec = out.get("decision") or {}
            ok = run.get("status") == "COMPLETED" and bool(dec.get("decision"))
            return combo, {"workflowId": wid, "status": run.get("status"), "ok": ok, **dec,
                           "normalizedIntent": out.get("intent"),
                           "error": None if ok else (run.get("reasonForIncompletion") or "no decision in output")}
        except Exception as e:  # recorded, counted as a failed (incorrect) evaluation
            return combo, {"workflowId": None, "status": "ERROR", "ok": False, "decision": None,
                           "error": f"{type(e).__name__}: {e}"[:500]}

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        decided = dict(pool.map(one, sorted(combos)))
    with (out_dir / "policy_evaluations.jsonl").open("w") as f:
        for (fx, intent, cents), d in sorted(decided.items()):
            f.write(json.dumps({"facts_fixture": fx, "intent": intent, "requestedCents": cents, **d}) + "\n")
    return decided


# ---------------- phase C ----------------
def run_workflow_sample(providers: list[str], sample_rows: list[dict], out_dir: Path, api=None) -> list[dict]:
    if api is None:
        from .conductor_api import ConductorREST
        api = ConductorREST()
    records = []
    for mode in ["rules", *providers]:
        for row in sample_rows:
            case = {"caseId": row["id"], "customerId": "cus_fixture" + row["facts_fixture"],
                    "requestedCents": row["requestedCents"], "currency": "usd"}
            if mode == "rules":
                name, inp = "billing_rules_only", {"case": {**case, "claimType": row["gold_intent"]}}
            else:
                name, inp = "billing_rules_ai", {"case": {**case, "message": row["message"]}, "classifier": mode}
            inp.update({"factsSource": "fixture", "fixtureId": row["facts_fixture"], "dryRun": True})
            rec = {"mode": mode, "id": row["id"], "difficulty": row["difficulty"], "gold_intent": row["gold_intent"],
                   "workflowId": None, "status": None, "error": None}
            t0 = time.perf_counter()
            try:
                run = api.execute(name, 1, inp, wait_seconds=30)
                wid = run.get("workflowId") or run.get("id")
                full = api.wait_for(wid, 90)
                tasks = {t["referenceTaskName"]: t for t in full.get("tasks", [])}

                def dur(ref, tasks=tasks):
                    t = tasks.get(ref)
                    return (t["endTime"] - t["startTime"]) if t and t.get("endTime") and t.get("startTime") else None
                out = full.get("output") or {}
                cls = out.get("classification") or {}
                rec.update({
                    "workflowId": wid, "status": full.get("status"),
                    "error": full.get("reasonForIncompletion"),
                    "client_wall_ms": round((time.perf_counter() - t0) * 1000, 1),
                    "workflow_ms": (full["endTime"] - full["startTime"]) if full.get("endTime") and full.get("startTime") else None,
                    "classify_task_ms": dur("classify_ref"), "facts_task_ms": dur("facts_ref"),
                    "business_rule_ms": dur("policy_ref"),
                    "model_intent": cls.get("intent") if mode != "rules" else None,  # model recommendation
                    "model_schema_valid": cls.get("schemaValid") if mode != "rules" else None,
                    "model_latency_ms": cls.get("latencyMs") if mode != "rules" else None,
                    "model_est_cost_usd": est_cost(cls.get("provider", ""), cls.get("model", ""), cls.get("usage")) if cls else None,
                    "normalized_intent": out.get("intent"),
                    "decision": (out.get("decision") or {}).get("decision"),  # workflow (policy) decision
                    "ruleId": (out.get("decision") or {}).get("ruleId"),
                })
            except Exception as e:
                rec.update({"status": "ERROR", "error": f"{type(e).__name__}: {e}"[:500]})
            records.append(rec)
            print(f"[workflow:{mode}] {row['id']} {rec['workflowId']} {rec['status']} {rec.get('workflow_ms')}ms")
    with (out_dir / "workflow_runs.jsonl").open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return records


# ---------------- metrics ----------------
def summarize(cls_rows: list[dict], decided: dict, dataset: list[dict], wf_rows: list[dict], meta: dict) -> dict:
    by_id = {r["id"]: r for r in dataset}

    def key(row, intent):
        return (row["facts_fixture"], intent, row["requestedCents"])

    gold = {r["id"]: decided.get(key(r, r["gold_intent"]), {"ok": False}) for r in dataset}
    gold_failed = sorted(i for i, d in gold.items() if not d.get("ok"))
    report = {
        "meta": meta,
        "dataset": {"cases": len(dataset), "by_intent": dict(Counter(r["gold_intent"] for r in dataset)),
                    "by_difficulty": dict(Counter(r["difficulty"] for r in dataset)),
                    "gold_decisions": dict(Counter(str(d.get("decision")) for d in gold.values())),
                    # Upper bound for "correctly automated": share of cases whose gold-label decision is automatable.
                    "gold_automated_rate": rate(sum(d.get("decision") in AUTOMATED for d in gold.values()), len(dataset))},
        "policy_evaluations": {"distinct_combinations": len(decided),
                               "failed": sum(not d.get("ok") for d in decided.values()),
                               "gold_decision_failures": gold_failed},
        "providers": {},
    }
    for provider in sorted({r["provider"] for r in cls_rows}):
        rows = [r for r in cls_rows if r["provider"] == provider]
        n = len(rows)
        reps = max(r["rep"] for r in rows)
        expected = len(dataset) * reps
        confusion = {g: {p: 0 for p in INTENTS} for g in INTENTS}
        per_diff: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        e2e_ok = automated_ok = escalated = violations = policy_failed = 0
        absorbed = wrong_intent = 0
        conf_buckets: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for r in rows:
            confusion[r["gold_intent"]][r["intent"]] += 1
            correct_intent = r["intent"] == r["gold_intent"]
            per_diff[r["difficulty"]][0] += correct_intent
            per_diff[r["difficulty"]][1] += 1
            if not correct_intent:
                wrong_intent += 1
            if r.get("confidence") is not None:
                for lo, hi in CONFIDENCE_BUCKETS:
                    if lo <= r["confidence"] < hi:
                        label = f"[{lo:.2f},{min(hi, 1.0):.2f}]"
                        conf_buckets[label][0] += correct_intent
                        conf_buckets[label][1] += 1
            ds = by_id[r["id"]]
            got_d = decided.get(key(ds, r["intent"]), {"ok": False})
            want_d = gold[r["id"]]
            if not (got_d.get("ok") and want_d.get("ok")):
                policy_failed += 1  # counted as incorrect (denominator stays n), never as agreement
                continue
            got, want = got_d["decision"], want_d["decision"]
            e2e_ok += got == want
            automated_ok += got == want and got in AUTOMATED
            escalated += got in ESCALATED
            violations += PERMISSIVENESS.get(got, 0) > PERMISSIVENESS.get(want, 0)
            if not correct_intent:
                absorbed += got == want
        by_case: dict[str, list[str]] = defaultdict(list)
        for r in rows:
            by_case[r["id"]].append(r["intent"])
        majority_ok = sum(Counter(v).most_common(1)[0][0] == by_id[i]["gold_intent"] for i, v in by_case.items())
        lat = [r["latencyMs"] for r in rows if not r.get("error")]
        costs = [r["est_cost_usd"] for r in rows if r["est_cost_usd"] is not None]
        usage: Counter = Counter()
        for r in rows:
            usage.update(r.get("usage") or {})
        total_cost = round(sum(costs), 6) if costs else None
        report["providers"][provider] = {
            "misclassified_cases": [
                {"id": i, "difficulty": by_id[i]["difficulty"], "gold_intent": g, "predicted_intent": p, "repeats": c,
                 "gold_decision": gold[i].get("decision"),
                 "model_decision": decided.get(key(by_id[i], p), {}).get("decision")}
                for (i, g, p), c in sorted(Counter((r["id"], r["gold_intent"], r["intent"]) for r in rows
                                                   if r["intent"] != r["gold_intent"]).items())],
            "model_versions": sorted({r["model"] for r in rows}),
            "calls": n, "expected_calls": expected,
            "complete": n == expected and set(by_case) == set(by_id) and all(len(v) == reps for v in by_case.values()),
            "repeats": reps,
            "intent_accuracy": rate(sum(r["intent"] == r["gold_intent"] for r in rows), n),
            "intent_accuracy_majority_vote": rate(majority_ok, len(by_case)),
            "accuracy_by_difficulty": {k: rate(a, b) for k, (a, b) in sorted(per_diff.items())},
            "consistency_all_repeats_agree": rate(sum(len(set(v)) == 1 for v in by_case.values()), len(by_case)) if reps > 1 else None,
            "schema_valid_rate": rate(sum(r["schemaValid"] for r in rows), n),
            "invalid_outputs": sum(not r["schemaValid"] for r in rows),
            "error_rate": rate(sum(bool(r.get("error")) for r in rows), n),
            "errors": sum(bool(r.get("error")) for r in rows),
            "policy_evaluation_failures": policy_failed,
            "decision_agreement_with_gold_policy": rate(e2e_ok, n),
            "intent_errors": wrong_intent,
            "intent_errors_with_same_decision": absorbed,
            "correctly_automated_rate": rate(automated_ok, n),
            "escalation_rate": rate(escalated, n),
            "policy_violation_rate": rate(violations, n),
            "policy_violations": violations,
            "model_latency_ms": {"p50": pct(lat, 0.5), "p95": pct(lat, 0.95),
                                 "mean": round(statistics.mean(lat), 1) if lat else None, "n": len(lat)},
            "usage_totals": dict(usage),
            "estimated_cost_usd_total": total_cost,
            "estimated_cost_per_correct_automated_decision_usd": round(total_cost / automated_ok, 8) if total_cost and automated_ok else None,
            "confidence_reported_by_provider": any(r.get("confidence") is not None for r in rows),
            # Provider-native confidence, analyzed within this provider only. Never compared across providers.
            "accuracy_by_provider_confidence": {k: {"n": b, "accuracy": rate(a, b)} for k, (a, b) in sorted(conf_buckets.items())} or None,
            "confusion_matrix": confusion,
        }
    if wf_rows:
        report["workflow"] = {}
        for mode in sorted({r["mode"] for r in wf_rows}):
            rs = [r for r in wf_rows if r["mode"] == mode]
            done = [r for r in rs if r["status"] == "COMPLETED"]
            not_started = [r for r in rs if not r.get("workflowId")]
            ms = [r["workflow_ms"] for r in done if r.get("workflow_ms") is not None]
            gold_dec = {r["id"]: gold.get(r["id"], {}).get("decision") for r in rs}
            report["workflow"][mode] = {
                "runs": len(rs), "completed": len(done), "failed_or_incomplete": len(rs) - len(done),
                # Executions that never started (client could not reach Orkes) vs. started but did not complete.
                "not_started_client_error": len(not_started),
                "started_not_completed": len(rs) - len(done) - len(not_started),
                "failures": [{"id": r["id"], "workflowId": r.get("workflowId"), "status": r["status"], "error": r.get("error")}
                             for r in rs if r["status"] != "COMPLETED"],
                "model_intent_accuracy": rate(sum(r.get("model_intent") == r["gold_intent"] for r in rs), len(rs)) if mode != "rules" else None,
                "model_intent_accuracy_completed_runs": rate(sum(r.get("model_intent") == r["gold_intent"] for r in done), len(done)) if mode != "rules" else None,
                "workflow_decision_agreement_with_gold_policy": rate(
                    sum(r["status"] == "COMPLETED" and r.get("decision") is not None and r.get("decision") == gold_dec[r["id"]] for r in rs), len(rs)),
                "workflow_decision_agreement_completed_runs": rate(
                    sum(r.get("decision") is not None and r.get("decision") == gold_dec[r["id"]] for r in done), len(done)),
                "workflow_ms": {"p50": pct(ms, .5), "p95": pct(ms, .95), "n": len(ms)},
                "classify_task_ms_p50": pct([r["classify_task_ms"] for r in done if r.get("classify_task_ms") is not None], .5),
                "facts_task_ms_p50": pct([r["facts_task_ms"] for r in done if r.get("facts_task_ms") is not None], .5),
                "business_rule_task_ms_p50": pct([r["business_rule_ms"] for r in done if r.get("business_rule_ms") is not None], .5),
                "in_workflow_model_latency_ms_p50": pct([r["model_latency_ms"] for r in done if r.get("model_latency_ms") is not None], .5),
                "estimated_model_cost_usd": round(sum(r.get("model_est_cost_usd") or 0 for r in rs), 6) if mode != "rules" else 0.0,
            }
    return add_analysis(report, cls_rows, decided, dataset, wf_rows)


# ---------------- statistics (descriptive; no significance claims beyond the explicit paired test) ----------------
P99_MIN_N = 100  # p99 from fewer samples is just the max-ish value; report it as None below this


def wilson_ci(k: int, n: int, z: float = 1.96) -> list[float] | None:
    """95% Wilson score interval for a proportion. Repeats of one case are not independent, so for
    per-call rates this is optimistic; per-case (majority) intervals are the conservative reading."""
    if not n:
        return None
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / den
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def mcnemar_exact(b: int, c: int) -> float | None:
    """Two-sided exact McNemar p-value on discordant pairs (b: only A correct, c: only B correct)."""
    from math import comb
    n = b + c
    if n == 0:
        return None
    k = min(b, c)
    return round(min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n), 4)


def p99(values: list[float]) -> float | None:
    return pct(values, 0.99) if len(values) >= P99_MIN_N else None


def add_analysis(report: dict, cls_rows: list[dict], decided: dict, dataset: list[dict], wf_rows: list[dict]) -> dict:
    by_id = {r["id"]: r for r in dataset}

    def dec(row, intent):
        d = decided.get((row["facts_fixture"], intent, row["requestedCents"]), {})
        return d.get("decision") if d.get("ok") else None

    gold_dec = {i: dec(r, r["gold_intent"]) for i, r in by_id.items()}
    majority: dict[str, dict[str, str]] = {}
    for provider, p in report["providers"].items():
        rows = [r for r in cls_rows if r["provider"] == provider]
        n = len(rows)
        correct = sum(r["intent"] == r["gold_intent"] for r in rows)
        agree = sum(dec(by_id[r["id"]], r["intent"]) is not None and dec(by_id[r["id"]], r["intent"]) == gold_dec[r["id"]] for r in rows)
        p["intent_accuracy_ci95"] = wilson_ci(correct, n)
        p["decision_agreement_ci95"] = wilson_ci(agree, n)
        by_case: dict[str, list[str]] = defaultdict(list)
        for r in rows:
            by_case[r["id"]].append(r["intent"])
        majority[provider] = {i: Counter(v).most_common(1)[0][0] for i, v in by_case.items()}
        maj_ok = sum(majority[provider][i] == by_id[i]["gold_intent"] for i in majority[provider])
        p["intent_accuracy_majority_ci95"] = wilson_ci(maj_ok, len(majority[provider]))
        cats = {}
        for d in DIFFICULTIES:
            rs = [r for r in rows if r["difficulty"] == d]
            k = sum(r["intent"] == r["gold_intent"] for r in rs)
            a = sum(dec(by_id[r["id"]], r["intent"]) is not None and dec(by_id[r["id"]], r["intent"]) == gold_dec[r["id"]] for r in rs)
            v = sum(PERMISSIVENESS.get(dec(by_id[r["id"]], r["intent"]), 0) > PERMISSIVENESS.get(gold_dec[r["id"]], 0) for r in rs)
            cats[d] = {"cases": len({r["id"] for r in rs}), "calls": len(rs), "intent_correct": k,
                       "intent_accuracy": rate(k, len(rs)), "intent_accuracy_ci95": wilson_ci(k, len(rs)),
                       "decision_agree": a, "decision_agreement": rate(a, len(rs)), "policy_violations": v,
                       "invalid_outputs": sum(not r["schemaValid"] for r in rs), "errors": sum(bool(r.get("error")) for r in rs)}
        p["by_category"] = cats
        lat = [r["latencyMs"] for r in rows if not r.get("error")]
        p["model_latency_ms"]["p99"] = p99(lat)
        p["model_latency_ms"]["p99_min_n"] = P99_MIN_N
        total = p.get("estimated_cost_usd_total")
        p["estimated_cost_per_evaluated_call_usd"] = round(total / n, 8) if total and n else None
        p["estimated_cost_per_case_all_repeats_usd"] = round(total / len(by_case), 8) if total and by_case else None
    names = sorted(majority)
    if len(names) == 2:
        a, b = names
        only_a = sorted(i for i in by_id if majority[a].get(i) == by_id[i]["gold_intent"] != majority[b].get(i))
        only_b = sorted(i for i in by_id if majority[b].get(i) == by_id[i]["gold_intent"] != majority[a].get(i))
        dis = []
        for i in sorted(by_id):
            ia, ib = majority[a].get(i), majority[b].get(i)
            if ia != ib:
                dis.append({"id": i, "difficulty": by_id[i]["difficulty"], "message": by_id[i]["message"],
                            "facts_fixture": by_id[i]["facts_fixture"], "gold_intent": by_id[i]["gold_intent"],
                            "gold_decision": gold_dec[i], f"{a}_intent": ia, f"{a}_decision": dec(by_id[i], ia),
                            f"{b}_intent": ib, f"{b}_decision": dec(by_id[i], ib)})
        report["comparison"] = {
            "unit": "case, majority intent over repeats",
            "providers": names, "cases": len(by_id),
            f"only_{a}_correct": only_a, f"only_{b}_correct": only_b,
            "mcnemar_exact_p_two_sided": mcnemar_exact(len(only_a), len(only_b)),
            "note": "Exact McNemar test on per-case majority correctness. With this few discordant cases, a p-value "
                    "above 0.05 means the observed difference is not statistically significant on this dataset.",
            "disagreements": dis,
        }
    for mode, w in (report.get("workflow") or {}).items():
        ms = [r["workflow_ms"] for r in wf_rows if r["mode"] == mode and r["status"] == "COMPLETED" and r.get("workflow_ms") is not None]
        w["workflow_ms"]["p99"] = p99(ms)
        w["workflow_ms"]["p99_min_n"] = P99_MIN_N
    return report


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def rebuild_report(run_id: str) -> Path:
    """Recompute the aggregate report from a run's recorded raw rows (no API calls). Keeps the run's meta."""
    out_dir = RESULTS_DIR / "runs" / run_id
    bench_dir = RESULTS_DIR / "benchmark"
    old = json.loads((bench_dir / f"report-{run_id}.json").read_text())
    meta = {**old["meta"], "report_rebuilt_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    dataset = load_dataset()[: meta["dataset_rows"]]
    cls_rows = [r for r in _read_jsonl(out_dir / "classifications.jsonl") if not r.get("probe")]
    decided = {(r["facts_fixture"], r["intent"], r["requestedCents"]): r for r in _read_jsonl(out_dir / "policy_evaluations.jsonl")}
    for d in decided.values():  # rows from runs before the `ok` field existed
        d.setdefault("ok", d.get("status") == "COMPLETED" and bool(d.get("decision")))
    wf_rows = _read_jsonl(out_dir / "workflow_runs.jsonl")
    report = summarize(cls_rows, decided, dataset, wf_rows, meta)
    path = bench_dir / f"report-{run_id}.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    (bench_dir / "latest.json").write_text(json.dumps(report, indent=2) + "\n")
    return path


def run_benchmark(providers: list[str], repeats: int, max_cost_usd: float, concurrency: int,
                  workflow_sample: int, limit: int | None = None) -> Path:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = RESULTS_DIR / "runs" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    dataset = load_dataset()[:limit] if limit else load_dataset()
    sample_rows = stratified_sample(dataset, workflow_sample)
    t0 = time.time()
    cls_rows = run_model_only(providers, repeats, max_cost_usd, concurrency, out_dir, dataset, len(sample_rows))
    combos = {(r["facts_fixture"], r["gold_intent"], r["requestedCents"]) for r in dataset}
    ds = {r["id"]: r for r in dataset}
    combos |= {(ds[r["id"]]["facts_fixture"], r["intent"], ds[r["id"]]["requestedCents"]) for r in cls_rows}
    print(f"[policy] evaluating {len(combos)} distinct (facts, intent, amount) combinations on Orkes")
    decided = evaluate_policy(combos, out_dir)
    wf_rows = run_workflow_sample(providers, sample_rows, out_dir) if sample_rows else []
    from .policy import load_release
    rel = load_release()
    meta = {"run_id": run_id, "date_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "dataset": DATASET.name, "dataset_rows": len(dataset), "repeats": repeats, "providers": providers,
            "workflow_sample_ids": [r["id"] for r in sample_rows],
            "policy_version": rel.policy_version, "rule_file_sha256": rel.sha256, "rule_file_url": rel.url,
            "adapter_settings": {"claude_model": os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5"),
                                 "claude_effort": os.environ.get("ANTHROPIC_EFFORT", "low"),
                                 "jev_model": os.environ.get("TYPESAFE_MODEL", "jev-1.14.0"),
                                 "retries": 0, "concurrency": concurrency},
            "max_cost_usd": max_cost_usd, "pricing": PRICING, "wall_seconds": round(time.time() - t0, 1),
            "facts": "frozen fixtures normalized from real Stripe sandbox reads (fixtures/facts)",
            "policy_engine": "Orkes Conductor BUSINESS_RULE via billing_policy_eval v1",
            "raw_dir": str(out_dir.relative_to(REPO_ROOT))}
    report = summarize(cls_rows, decided, dataset, wf_rows, meta)
    bench_dir = RESULTS_DIR / "benchmark"
    bench_dir.mkdir(parents=True, exist_ok=True)
    path = bench_dir / f"report-{run_id}.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    (bench_dir / "latest.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {path.relative_to(REPO_ROOT)}")
    return path
