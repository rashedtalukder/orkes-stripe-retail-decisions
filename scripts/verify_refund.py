"""Verify guarded sandbox refund execution on the dedicated S9 scenario (Stripe sandbox only).

  pre   - read-only: show the S9 duplicate charge, its refund state, and that it is test-mode
  post  - read-only + idempotency checks after the workflow ran with --execute-refunds:
          * read back the refund and the charge from the Stripe API
          * replay the worker's refund function with identical inputs (expects ALREADY_EXECUTED, no new refund)
          * replay the exact Stripe create call with the same Idempotency-Key (Stripe returns the same refund)
          * confirm exactly one refund exists on the charge
Writes results/refund-verification/<utc>.json. Never prints secrets.
"""

import json
import os
import sys
from datetime import datetime, timezone

from stripe_decisions.config import RESULTS_DIR
from stripe_decisions.facts import fetch_stripe_facts
from stripe_decisions.policy import POLICY_VERSION
from stripe_decisions.refunds import idempotency_key, safe_execute_refund
from stripe_decisions.seed import load_manifest
from stripe_decisions.stripe_client import account_summary, get_client


def charge_state(c, charge_id):
    ch = c.charges.retrieve(charge_id)
    refunds = [r for r in c.refunds.list({"charge": charge_id, "limit": 100}).auto_paging_iter()]
    return {"charge": ch.id, "livemode": ch.livemode, "customer": ch.customer, "amount": ch.amount,
            "currency": ch.currency, "amount_refunded": ch.amount_refunded, "refunded": ch.refunded,
            "refunds": [{"id": r.id, "amount": r.amount, "status": r.status, "reason": r.reason,
                         "metadata": r.metadata.to_dict() if r.metadata else {}} for r in refunds]}


def main(step):
    c = get_client().v1
    acct = account_summary()
    assert acct["livemode"] is False
    s9 = load_manifest()["scenarios"]["S9"]
    out = {"step": step, "at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "stripe_account": acct["account_id"], "scenario": "S9", "customer": s9["customer"]}
    facts = fetch_stripe_facts(s9["customer"])
    out["facts"] = {k: getattr(facts, k) for k in ("factsStatus", "duplicateVerified", "duplicateRefundableCents",
                                                   "duplicateChargeId", "alreadyRefunded")}
    # The charge the workflow targets is the one the facts normalizer identifies as the duplicate.
    dup_charge = facts.duplicateChargeId
    assert dup_charge in s9["charges"], "duplicate charge must be one of S9's seeded charges"
    out["state"] = charge_state(c, dup_charge)
    if step == "post":
        key = idempotency_key(dup_charge, 1200, POLICY_VERSION)
        os.environ["OSD_ALLOW_SANDBOX_REFUNDS"] = "1"  # this process only; needed to reach the idempotency check
        replay = safe_execute_refund(case_id="S9", customer_id=s9["customer"], decision="APPROVE_REFUND", rule_id="R07",
                                     policy_version=POLICY_VERSION, charge_id=dup_charge, amount_cents=1200,
                                     currency="usd", facts_source="stripe_sandbox", dry_run=False)
        out["worker_replay"] = replay
        existing = [r for r in out["state"]["refunds"] if r["metadata"].get("osd_refund_key") == key]
        if len(existing) == 1:
            # Exact same create call + same Idempotency-Key: Stripe must return the original refund, not a new one.
            again = c.refunds.create(
                {"charge": dup_charge, "amount": 1200, "reason": "duplicate",
                 "metadata": {"osd_project": "orkes-stripe-decisions", "osd_refund_key": key, "osd_case_id": "S9",
                              "osd_rule_id": "R07", "osd_policy_version": POLICY_VERSION}},
                {"idempotency_key": key})
            out["stripe_idempotent_replay"] = {"returned_refund": again.id, "same_as_original": again.id == existing[0]["id"]}
        out["state_after_replays"] = charge_state(c, dup_charge)
        out["checks"] = {
            "exactly_one_refund": len(out["state_after_replays"]["refunds"]) == 1,
            "refund_succeeded": all(r["status"] == "succeeded" for r in out["state_after_replays"]["refunds"]),
            "amount_refunded_1200": out["state_after_replays"]["amount_refunded"] == 1200,
            "worker_replay_already_executed": replay["status"] == "ALREADY_EXECUTED",
            "stripe_replay_same_refund": out.get("stripe_idempotent_replay", {}).get("same_as_original") is True,
            "test_mode": out["state_after_replays"]["livemode"] is False,
        }
    d = RESULTS_DIR / "refund-verification"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{step}-{out['at_utc'].replace(':', '')}.json"
    path.write_text(json.dumps(out, indent=2, default=str) + "\n")
    print(json.dumps(out, indent=2, default=str))
    print("wrote", path)


if __name__ == "__main__":
    main(sys.argv[1])
