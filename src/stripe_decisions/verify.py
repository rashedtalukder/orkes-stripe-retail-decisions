"""Read-back verification of seeded Stripe scenarios.

Each scenario is re-read from the Stripe API, normalized with the same code the workflow worker
uses, and checked against the expected *facts* (not decisions; decisions come from Conductor).
Optionally snapshots the normalized facts as offline fixtures, labeled with provenance.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from .config import FIXTURES_DIR
from .facts import fetch_stripe_facts
from .seed import load_manifest

EXPECTED_FACTS: dict[str, dict] = {
    "S1": {"factsStatus": "VERIFIED", "duplicateVerified": "YES", "duplicateRefundableCents": 3500, "alreadyRefunded": "NO"},
    "S2": {"factsStatus": "VERIFIED", "duplicateVerified": "YES", "duplicateRefundableCents": 15000, "alreadyRefunded": "NO"},
    "S3": {"factsStatus": "VERIFIED", "duplicateVerified": "NO", "prorationPresent": "YES"},
    "S4": {"factsStatus": "VERIFIED", "paymentFailed": "YES", "duplicateVerified": "NO"},
    "S5": {"factsStatus": "VERIFIED", "alreadyRefunded": "YES", "duplicateVerified": "NO"},
    "S6": {"factsStatus": "VERIFIED", "duplicateVerified": "NO", "prorationPresent": "NO", "paymentFailed": "NO", "alreadyRefunded": "NO"},
    "S7": {"factsStatus": "VERIFIED", "duplicateVerified": "NO", "prorationPresent": "NO", "paymentFailed": "NO", "alreadyRefunded": "NO"},
    "S8": {"factsStatus": "VERIFIED", "duplicateVerified": "NO", "prorationPresent": "NO", "paymentFailed": "NO"},
}
EXPECTED_EVIDENCE: dict[str, dict] = {
    "S8": {"succeededCharges": 1, "failedCharges": 1},
    "S5": {"succeededCharges": 1},
}


def verify_seed(write_fixtures: bool = False) -> dict:
    manifest = load_manifest()
    results = {}
    for sid, scen in sorted(manifest["scenarios"].items()):
        facts = fetch_stripe_facts(scen["customer"], requested_cents=scen.get("requestedCents", 0))
        got = facts.model_dump(mode="json")
        mismatches = {k: {"expected": v, "actual": got.get(k)} for k, v in EXPECTED_FACTS.get(sid, {}).items()
                      if got.get(k) != v}
        mismatches.update({f"evidence.{k}": {"expected": v, "actual": got["evidence"].get(k)}
                           for k, v in EXPECTED_EVIDENCE.get(sid, {}).items() if got["evidence"].get(k) != v})
        results[sid] = {"title": scen["title"], "customer": scen["customer"], "ok": not mismatches,
                        "mismatches": mismatches, "facts": got}
        print(f"{sid} {'OK  ' if not mismatches else 'FAIL'} {scen['title']}"
              + ("" if not mismatches else f"  {json.dumps(mismatches)}"))
        if write_fixtures:
            out = FIXTURES_DIR / "facts" / f"{sid}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            snapshot = {k: v for k, v in got.items() if k not in ("factsSource", "requestedCents")}
            snapshot["_provenance"] = {
                "source": "Normalized from real Stripe sandbox API reads (test mode)",
                "stripe_account": manifest["stripe_account"],
                "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "note": "Object IDs are sandbox test-mode IDs. No customer PII is stored.",
            }
            out.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    passed = sum(r["ok"] for r in results.values())
    return {"summary": {"passed": passed, "failed": len(results) - passed, "stripe_account": manifest["stripe_account"]},
            "results": results}
