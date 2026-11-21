"""Read authoritative billing facts from Stripe and normalize them for the Business Rule.

This module *verifies and normalizes facts*; it does not decide anything. Each output field is
a statement about Stripe state (e.g. "two succeeded charges with the same amount and billing
reference exist within 72 hours"). Which action those facts permit is decided only by the
Conductor BUSINESS_RULE task.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

import stripe

from .config import FIXTURES_DIR
from .schemas import BillingFacts, FactsStatus

DUPLICATE_WINDOW_SECONDS = 72 * 3600
PRORATION_LOOKBACK_SECONDS = 62 * 86_400


def _d(obj: Any) -> dict:
    return obj.to_dict() if hasattr(obj, "to_dict") else dict(obj or {})


def _yn(flag: bool) -> Literal["YES", "NO"]:
    return "YES" if flag else "NO"


def _billing_key(ch: dict) -> str:
    md = ch.get("metadata") or {}
    return md.get("osd_billing_ref") or (ch.get("description") or "").strip().lower()


def find_duplicate(charges: list[dict]) -> list[dict] | None:
    """Return the first group of >=2 succeeded charges with same amount, currency and billing reference
    created within the duplicate window, oldest first. Pure function so it can be unit tested."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for ch in charges:
        if ch.get("status") == "succeeded" and ch.get("paid", True):
            groups[(ch["amount"], ch["currency"], _billing_key(ch))].append(ch)
    for key, group in groups.items():
        if not key[2]:
            continue  # no billing reference/description: cannot prove two charges are for the same thing
        group.sort(key=lambda c: c["created"])
        for a, b in zip(group, group[1:]):
            if b["created"] - a["created"] <= DUPLICATE_WINDOW_SECONDS:
                return [a, b]
    return None


def has_proration(invoices: list[dict], now: int) -> list[str]:
    ids = []
    for inv in invoices:
        if inv.get("status") not in ("paid", "open"):
            continue
        if now - inv.get("created", 0) > PRORATION_LOOKBACK_SECONDS:
            continue
        for line in (inv.get("lines") or {}).get("data", []):
            parent = line.get("parent") or {}
            details = parent.get("subscription_item_details") or parent.get("invoice_item_details") or {}
            if details.get("proration") or line.get("proration"):
                ids.append(inv["id"])
                break
    return ids


def normalize(customer: dict | None, charges: list[dict], invoices: list[dict], subscriptions: list[dict],
              *, charge_id: str | None, requested_cents: int, now: int) -> BillingFacts:
    """Pure normalization from raw Stripe dicts to BillingFacts (unit-testable without network)."""
    if customer is None or customer.get("deleted"):
        return BillingFacts(factsStatus=FactsStatus.INSUFFICIENT, factsSource="stripe_sandbox",
                            requestedCents=requested_cents, evidence={"reason": "customer_not_found"})
    if any(o.get("livemode") for o in [customer, *charges, *invoices, *subscriptions]):
        return BillingFacts(factsStatus=FactsStatus.ERROR, factsSource="stripe_sandbox",
                            requestedCents=requested_cents, error="live-mode object encountered")

    succeeded = sorted((c for c in charges if c.get("status") == "succeeded"), key=lambda c: c["created"])
    failed = [c for c in charges if c.get("status") == "failed"]
    evidence: dict[str, Any] = {
        "succeededCharges": len(succeeded),
        "failedCharges": len(failed),
        "invoices": len(invoices),
        "subscriptionStatuses": sorted({str(s.get("status")) for s in subscriptions}),
    }
    if not charges and not invoices:
        return BillingFacts(factsStatus=FactsStatus.INSUFFICIENT, factsSource="stripe_sandbox",
                            requestedCents=requested_cents, evidence={**evidence, "reason": "no_billing_history"})

    target = None
    if charge_id:
        target = next((c for c in charges if c["id"] == charge_id), None)
        if target is None:
            return BillingFacts(factsStatus=FactsStatus.CONFLICTING, factsSource="stripe_sandbox",
                                requestedCents=requested_cents,
                                evidence={**evidence, "reason": "referenced_charge_not_on_customer"})

    dup = find_duplicate(charges)
    dup_charge = dup[1] if dup else None
    if dup:
        evidence["duplicateGroup"] = [c["id"] for c in dup]
    if target is None:
        target = dup_charge or (succeeded[-1] if succeeded else None)

    dup_refundable = (dup_charge["amount"] - dup_charge.get("amount_refunded", 0)) if dup_charge else 0
    proration_ids = has_proration(invoices, now)
    open_failed = [i["id"] for i in invoices
                   if i.get("status") == "open" and i.get("attempted") and i.get("amount_remaining", 0) > 0]
    past_due = any(s.get("status") in ("past_due", "unpaid") for s in subscriptions)
    evidence.update({"prorationInvoices": proration_ids, "openFailedInvoices": open_failed})

    currency = (target or {}).get("currency") or customer.get("currency") or "usd"
    return BillingFacts(
        factsStatus=FactsStatus.VERIFIED,
        factsSource="stripe_sandbox",
        currency=currency,
        alreadyRefunded=_yn(bool(target and target.get("refunded"))),
        duplicateVerified=_yn(dup_charge is not None),
        duplicateRefundableCents=max(0, dup_refundable),
        duplicateChargeId=dup_charge["id"] if dup_charge else None,
        prorationPresent=_yn(bool(proration_ids)),
        paymentFailed=_yn(bool(open_failed) or past_due),
        requestedCents=requested_cents,
        targetChargeId=target["id"] if target else None,
        evidence=evidence,
    )


def fetch_stripe_facts(customer_id: str, *, charge_id: str | None = None, requested_cents: int = 0) -> BillingFacts:
    from .stripe_client import get_client

    try:
        c = get_client().v1
        try:
            customer = _d(c.customers.retrieve(customer_id))
        except stripe.InvalidRequestError as e:
            if getattr(e, "code", None) == "resource_missing":
                customer = None
            else:
                raise
        charges = invoices = subs = []
        if customer:
            charges = [_d(x) for x in c.charges.list({"customer": customer_id, "limit": 100}).auto_paging_iter()]
            invoices = [_d(x) for x in c.invoices.list({"customer": customer_id, "limit": 24}).data]
            subs = [_d(x) for x in c.subscriptions.list({"customer": customer_id, "status": "all", "limit": 20}).data]
        clock_now = int(time.time())
        if customer and customer.get("test_clock"):
            # Interpret recency relative to the customer's simulated time, not wall-clock time.
            clock_now = c.test_helpers.test_clocks.retrieve(customer["test_clock"]).frozen_time
        return normalize(customer, charges, invoices, subs, charge_id=charge_id,
                         requested_cents=requested_cents, now=clock_now)
    except stripe.StripeError as e:
        return BillingFacts(factsStatus=FactsStatus.ERROR, factsSource="stripe_sandbox",
                            requestedCents=requested_cents, error=f"{type(e).__name__}: {e.user_message or 'stripe error'}")


def load_fixture_facts(fixture_id: str, requested_cents: int = 0, base: Path | None = None) -> BillingFacts:
    path = (base or FIXTURES_DIR / "facts") / f"{fixture_id}.json"
    if not path.exists():
        return BillingFacts(factsStatus=FactsStatus.INSUFFICIENT, factsSource="fixture",
                            requestedCents=requested_cents, evidence={"reason": f"fixture {fixture_id} not found"})
    data = json.loads(path.read_text())
    data.pop("_provenance", None)
    data["factsSource"] = "fixture"
    data["requestedCents"] = requested_cents
    return BillingFacts.model_validate(data)
