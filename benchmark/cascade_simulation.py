"""SIMULATION (no API calls): confidence-gated cascade, replayed from a recorded benchmark run.

For every (case, repeat): accept Jev's label when its provider-native confidence >= threshold; otherwise use
Claude's recorded label for the same case and repeat. Decisions come from the recorded Orkes policy evaluations.

Caveats (also written to the output): thresholds are chosen on the same data they are scored on (in-sample, so
optimistic); latency assumes the escalation call happens after the Jev call (sequential); costs are list-price
estimates. Validate any threshold on held-out data before relying on it.

usage: uv run python benchmark/cascade_simulation.py [run_id]
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from stripe_decisions.benchmark import pct  # noqa: E402

RUN = sys.argv[1] if len(sys.argv) > 1 else "20261121T022545Z"
RAW = ROOT / "results" / "benchmark" / "raw" / RUN
OUT = ROOT / "results" / "benchmark" / f"cascade-simulation-{RUN}.json"


def main() -> None:
    rows = [r for r in map(json.loads, (RAW / "classifications.jsonl").read_text().splitlines()) if not r.get("probe")]
    ds = {r["id"]: r for r in map(json.loads, (ROOT / "benchmark" / "dataset.v1.jsonl").read_text().splitlines())}
    pol = {(r["facts_fixture"], r["intent"], r["requestedCents"]): r["decision"]
           for r in map(json.loads, (RAW / "policy_evaluations.jsonl").read_text().splitlines())}
    pairs: dict[tuple, dict] = defaultdict(dict)
    for r in rows:
        pairs[(r["id"], r["rep"])][r["provider"]] = r

    def decision(case_id, intent):
        d = ds[case_id]
        return pol.get((d["facts_fixture"], intent, d["requestedCents"]))

    results = {}
    for name, th in [("jev_only", None), ("claude_only", "claude"), ("cascade_0.80", 0.80), ("cascade_0.90", 0.90),
                     ("cascade_0.95", 0.95)]:
        ok = agree = escalated = 0
        cost = 0.0
        lat = []
        for (cid, _rep), p in pairs.items():
            j, c = p["typesafe"], p["anthropic"]
            if th is None:
                use, latency, k = j, j["latencyMs"], j["est_cost_usd"] or 0
            elif th == "claude":
                use, latency, k = c, c["latencyMs"], c["est_cost_usd"] or 0
            else:
                latency, k = j["latencyMs"], j["est_cost_usd"] or 0
                if (j.get("confidence") or 0) >= th:
                    use = j
                else:
                    use, escalated = c, escalated + 1
                    latency += c["latencyMs"]
                    k += c["est_cost_usd"] or 0
            ok += use["intent"] == ds[cid]["gold_intent"]
            agree += decision(cid, use["intent"]) == decision(cid, ds[cid]["gold_intent"])
            cost += k
            lat.append(latency)
        n = len(pairs)
        results[name] = {"calls": n, "intent_accuracy": round(ok / n, 4), "decision_agreement": round(agree / n, 4),
                         "escalated_to_claude": round(escalated / n, 4),
                         "est_cost_usd_per_1k_cases": round(cost / n * 1000, 4),
                         "latency_ms_p50": pct(lat, 0.5), "latency_ms_p95": pct(lat, 0.95)}
    out = {"type": "SIMULATION replayed from recorded rows; no API calls", "run_id": RUN,
           "caveats": ["thresholds chosen on the same data they are scored on (in-sample, optimistic)",
                       "latency assumes the escalation call runs after the Jev call",
                       "costs are list-price estimates from benchmark/pricing.json",
                       "validate thresholds on held-out data before use"],
           "results": results}
    OUT.write_text(json.dumps(out, indent=2) + "\n")
    for k, v in results.items():
        print(f"{k:13} {v}")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
