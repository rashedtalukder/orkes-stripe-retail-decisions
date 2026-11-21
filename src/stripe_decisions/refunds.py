"""Guarded, idempotent Stripe sandbox refund execution.

This function does not decide *whether* to refund; the Conductor Business Rule did that. It
enforces safety invariants before acting on that decision:
  * only an APPROVE_REFUND decision with a rule id is actionable
  * dry-run unless the workflow input says dryRun=false AND the worker process has
    OSD_ALLOW_SANDBOX_REFUNDS=1 (two independent opt-ins)
  * never refunds from fixture facts; only verified sandbox facts
  * re-reads the charge: test mode, belongs to the case's customer, enough refundable balance
  * a stable idempotency key, plus a metadata check, prevents duplicate execution
"""

from __future__ import annotations

import hashlib
from typing import Any

import stripe

from .config import sandbox_refunds_enabled


def idempotency_key(charge_id: str, amount_cents: int, policy_version: str) -> str:
    digest = hashlib.sha256(f"{charge_id}:{amount_cents}:{policy_version}".encode()).hexdigest()[:24]
    return f"osd-refund-{digest}"


def _is_dry_run(dry_run: Any) -> bool:
    # Anything other than an explicit false (bool or string) is treated as dry-run.
    return not (dry_run is False or (isinstance(dry_run, str) and dry_run.strip().lower() == "false"))


def execute_refund(*, case_id: str, customer_id: str, decision: str, rule_id: str | None, policy_version: str,
                   charge_id: str | None, amount_cents: Any, currency: str, facts_source: str,
                   dry_run: Any = True, client: Any = None) -> dict[str, Any]:
    base = {"executed": False, "dryRun": True, "refundId": None, "idempotencyKey": None}
    if decision != "APPROVE_REFUND" or not rule_id:
        return {**base, "status": "REJECTED", "reason": "no APPROVE_REFUND decision from the Business Rule"}
    if not charge_id or not isinstance(amount_cents, int) or isinstance(amount_cents, bool) or amount_cents <= 0:
        return {**base, "status": "REJECTED", "reason": "missing verified charge or positive integer amount"}
    key = idempotency_key(charge_id, amount_cents, policy_version)
    base["idempotencyKey"] = key
    if facts_source != "stripe_sandbox":
        return {**base, "status": "DRY_RUN", "reason": "facts came from a fixture; refunds require live sandbox facts"}
    if _is_dry_run(dry_run) or not sandbox_refunds_enabled():
        why = "dryRun requested (default)" if _is_dry_run(dry_run) else "OSD_ALLOW_SANDBOX_REFUNDS!=1 on worker"
        return {**base, "status": "DRY_RUN", "reason": f"would refund {amount_cents} {currency} of {charge_id}; {why}"}

    if client is None:
        from .stripe_client import get_client
        client = get_client().v1
    charge = client.charges.retrieve(charge_id)
    if charge.livemode:
        return {**base, "status": "REJECTED", "reason": "live-mode charge"}
    if charge.customer != customer_id:
        return {**base, "status": "REJECTED", "reason": "charge does not belong to the case customer"}
    for r in client.refunds.list({"charge": charge_id, "limit": 100}).auto_paging_iter():
        md = r.metadata.to_dict() if r.metadata else {}
        if md.get("osd_refund_key") == key:
            return {**base, "dryRun": False, "refundId": r.id, "status": "ALREADY_EXECUTED",
                    "reason": "refund with this idempotency key already exists"}
    refundable = charge.amount - charge.amount_refunded
    if amount_cents > refundable or charge.currency != currency:
        return {**base, "dryRun": False, "status": "REJECTED",
                "reason": f"refundable balance {refundable} {charge.currency} < requested {amount_cents} {currency}"}
    refund = client.refunds.create(
        {"charge": charge_id, "amount": amount_cents, "reason": "duplicate",
         "metadata": {"osd_project": "orkes-stripe-decisions", "osd_refund_key": key, "osd_case_id": case_id,
                      "osd_rule_id": rule_id, "osd_policy_version": policy_version}},
        {"idempotency_key": key})
    return {**base, "executed": True, "dryRun": False, "refundId": refund.id, "status": refund.status.upper(),
            "reason": f"refunded per {rule_id} ({policy_version})"}


def safe_execute_refund(**kwargs: Any) -> dict[str, Any]:
    try:
        return execute_refund(**kwargs)
    except stripe.StripeError as e:
        return {"executed": False, "dryRun": False, "refundId": None, "idempotencyKey": None,
                "status": "ERROR", "reason": f"{type(e).__name__}: {e.user_message or 'stripe error'}"}
