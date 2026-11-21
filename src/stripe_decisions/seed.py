"""Idempotent Stripe sandbox seeding for the eight demo scenarios.

Idempotency strategy (does not rely on the local manifest):
  * Products use fixed custom IDs (osd_prod_*); prices use lookup_keys.
  * Customers are found by a deterministic email (customers.list(email=...) is strongly consistent).
  * PaymentIntents / refunds are found by metadata.osd_seed_key on the customer's objects.
  * Test clocks are found by name.
  * Every create call also carries a deterministic Stripe idempotency key as a second guard.
The manifest written at the end is a *cache* of IDs, rebuilt from Stripe read-backs each run.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

import stripe

from .config import DATA_DIR, PROJECT_TAG
from .stripe_client import account_summary, get_client

MANIFEST_PATH = DATA_DIR / "stripe_manifest.json"
SEED_VERSION = "seed-v1"
DAY = 86_400

PLANS = {
    "starter": ("Starter", 1900),
    "pro": ("Pro", 4900),
    "business": ("Business", 14900),
    "enterprise": ("Enterprise", 49900),
}

SCENARIOS: dict[str, dict[str, Any]] = {
    "S1": {"title": "Small verified duplicate payment", "expected_decision": "APPROVE_REFUND", "claimType": "DUPLICATE_CHARGE_CLAIM"},
    "S2": {"title": "Large verified duplicate payment", "expected_decision": "HUMAN_REVIEW", "claimType": "DUPLICATE_CHARGE_CLAIM"},
    "S3": {"title": "Legitimate subscription proration", "expected_decision": "EXPLAIN_CHARGE", "claimType": "DUPLICATE_CHARGE_CLAIM"},
    "S4": {"title": "Failed renewal payment", "expected_decision": "INVESTIGATE", "claimType": "FAILED_PAYMENT"},
    "S5": {"title": "Previously refunded payment", "expected_decision": "NO_ADDITIONAL_REFUND", "claimType": "REFUND_REQUEST"},
    "S6": {"title": "Ambiguous billing complaint", "expected_decision": "INVESTIGATE", "claimType": "UNKNOWN"},
    "S7": {"title": "Goodwill credit request", "expected_decision": "HUMAN_REVIEW", "claimType": "REFUND_REQUEST", "requestedCents": 1500},
    "S8": {"title": "Contradictory evidence (one success, one decline)", "expected_decision": "INVESTIGATE", "claimType": "DUPLICATE_CHARGE_CLAIM"},
}


def _meta(**extra: str) -> dict[str, str]:
    return {"osd_project": PROJECT_TAG, "osd_seed_version": SEED_VERSION, **extra}


def _idem(*parts: str) -> dict[str, str]:
    return {"idempotency_key": "osd-" + "-".join(parts) + f"-{SEED_VERSION}"}


def _md(obj: Any, key: str) -> str | None:
    """Read one metadata value from a StripeObject (which is not a dict in stripe-python >= 13)."""
    md = getattr(obj, "metadata", None)
    return md.to_dict().get(key) if md is not None else None


class Seeder:
    def __init__(self, log=print):
        self.c = get_client().v1
        self.log = log
        self.manifest: dict[str, Any] = {"plans": {}, "scenarios": {}}

    # ---------- catalog ----------
    def ensure_catalog(self) -> None:
        for plan, (name, cents) in PLANS.items():
            pid = f"osd_prod_{plan}"
            try:
                prod = self.c.products.retrieve(pid)
            except stripe.InvalidRequestError:
                prod = self.c.products.create(
                    {"id": pid, "name": f"Orkes Demo Co {name}", "description": f"Fictional {name} plan (demo data)",
                     "metadata": _meta(osd_plan=plan)}, _idem("prod", plan))
                self.log(f"created product {pid}")
            lookup = f"osd_{plan}_monthly_usd"
            prices = self.c.prices.list({"lookup_keys": [lookup], "limit": 1}).data
            if prices:
                price = prices[0]
            else:
                price = self.c.prices.create(
                    {"product": prod.id, "unit_amount": cents, "currency": "usd", "recurring": {"interval": "month"},
                     "lookup_key": lookup, "metadata": _meta(osd_plan=plan)}, _idem("price", plan))
                self.log(f"created price {lookup}")
            self.manifest["plans"][plan] = {"product": prod.id, "price": price.id, "unit_amount": price.unit_amount,
                                            "currency": price.currency, "lookup_key": lookup}

    # ---------- primitives ----------
    def ensure_clock(self, sid: str) -> stripe.test_helpers.TestClock:
        name = f"osd-{sid}-{SEED_VERSION}"
        for clock in self.c.test_helpers.test_clocks.list({"limit": 100}).auto_paging_iter():
            if clock.name == name:
                return clock
        clock = self.c.test_helpers.test_clocks.create({"frozen_time": int(time.time()), "name": name}, _idem("clock", sid))
        self.log(f"created test clock {name}")
        return clock

    def advance_clock(self, clock_id: str, to: int) -> None:
        clock = self.c.test_helpers.test_clocks.retrieve(clock_id)
        if clock.frozen_time >= to:
            return
        self.c.test_helpers.test_clocks.advance(clock_id, {"frozen_time": to})
        for _ in range(120):
            time.sleep(2)
            clock = self.c.test_helpers.test_clocks.retrieve(clock_id)
            if clock.status == "ready":
                return
            if clock.status == "internal_failure":
                raise RuntimeError(f"test clock {clock_id} failed to advance")
        raise TimeoutError(f"test clock {clock_id} still advancing")

    def ensure_customer(self, sid: str, clock_id: str | None = None) -> stripe.Customer:
        email = f"osd-{sid.lower()}@example.com"
        query: dict[str, Any] = {"email": email, "limit": 10}
        if clock_id:
            # customers.list omits test-clock customers unless the clock is named explicitly.
            query["test_clock"] = clock_id
        found = self.c.customers.list(query).data
        found = [c for c in found if _md(c, "osd_project") == PROJECT_TAG]
        if found:
            return found[0]
        params: dict[str, Any] = {"email": email, "name": f"Demo Customer {sid}",
                                  "description": f"Synthetic demo customer for scenario {sid}",
                                  "metadata": _meta(osd_scenario=sid)}
        if clock_id:
            params["test_clock"] = clock_id
        cust = self.c.customers.create(params, _idem("cust", sid))
        self.log(f"created customer {cust.id} ({sid})")
        return cust

    def ensure_default_pm(self, cust: stripe.Customer, token: str = "pm_card_visa", tag: str = "default") -> str:
        for pm in self.c.payment_methods.list({"customer": cust.id, "limit": 20}).data:
            if _md(pm, "osd_pm_tag") == tag:
                pm_id = pm.id
                break
        else:
            pm_id = self.c.payment_methods.attach(token, {"customer": cust.id}).id
            self.c.payment_methods.update(pm_id, {"metadata": _meta(osd_pm_tag=tag)})
        self.c.customers.update(cust.id, {"invoice_settings": {"default_payment_method": pm_id}})
        return pm_id

    def find_pi(self, cust_id: str, seed_key: str) -> stripe.PaymentIntent | None:
        for pi in self.c.payment_intents.list({"customer": cust_id, "limit": 100}).auto_paging_iter():
            if _md(pi, "osd_seed_key") == seed_key:
                return pi
        return None

    def ensure_payment(self, cust_id: str, pm_id: str, seed_key: str, amount: int, description: str,
                       billing_ref: str, expect_fail: bool = False) -> stripe.PaymentIntent:
        pi = self.find_pi(cust_id, seed_key)
        if pi:
            return pi
        params = {"amount": amount, "currency": "usd", "customer": cust_id, "payment_method": pm_id,
                  "confirm": True, "off_session": True, "description": description,
                  "metadata": _meta(osd_seed_key=seed_key, osd_billing_ref=billing_ref)}
        try:
            pi = self.c.payment_intents.create(params, _idem("pi", seed_key))
        except stripe.CardError as e:
            if not expect_fail:
                raise
            pi = self.c.payment_intents.retrieve(e.error.payment_intent.id)
        self.log(f"payment {seed_key}: {pi.id} status={pi.status}")
        return pi

    def ensure_subscription(self, cust_id: str, plan: str, seed_key: str) -> stripe.Subscription:
        for sub in self.c.subscriptions.list({"customer": cust_id, "status": "all", "limit": 20}).data:
            if _md(sub, "osd_seed_key") == seed_key:
                return sub
        sub = self.c.subscriptions.create(
            {"customer": cust_id, "items": [{"price": self.manifest["plans"][plan]["price"]}],
             "metadata": _meta(osd_seed_key=seed_key)}, _idem("sub", seed_key))
        self.log(f"created subscription {sub.id} ({seed_key}) status={sub.status}")
        return sub

    # ---------- scenarios ----------
    def seed_duplicate(self, sid: str, amount: int, product_desc: str) -> dict:
        cust = self.ensure_customer(sid)
        pm = self.ensure_default_pm(cust)
        ref = f"OSD-{sid}-0001"
        p1 = self.ensure_payment(cust.id, pm, f"{sid}-pay-1", amount, product_desc, ref)
        p2 = self.ensure_payment(cust.id, pm, f"{sid}-pay-2", amount, product_desc, ref)
        return {"customer": cust.id, "payment_intents": [p1.id, p2.id],
                "charges": [p1.latest_charge, p2.latest_charge]}

    def seed_s3_proration(self) -> dict:
        clock = self.ensure_clock("S3")
        cust = self.ensure_customer("S3", clock.id)
        self.ensure_default_pm(cust)
        sub = self.ensure_subscription(cust.id, "starter", "S3-sub")
        pro_price = self.manifest["plans"]["pro"]["price"]
        item = sub["items"].data[0]
        if item.price.id != pro_price:
            start = self.c.test_helpers.test_clocks.retrieve(clock.id).frozen_time
            self.advance_clock(clock.id, start + 15 * DAY)
            sub = self.c.subscriptions.update(
                sub.id, {"items": [{"id": item.id, "price": pro_price}], "proration_behavior": "always_invoice"},
                _idem("upgrade", "S3"))
            self.log(f"S3 upgraded {sub.id} starter->pro with always_invoice proration")
        return {"customer": cust.id, "test_clock": clock.id, "subscription": sub.id}

    def seed_s4_failed_renewal(self) -> dict:
        clock = self.ensure_clock("S4")
        cust = self.ensure_customer("S4", clock.id)
        self.ensure_default_pm(cust)
        sub = self.ensure_subscription(cust.id, "pro", "S4-sub")
        sub = self.c.subscriptions.retrieve(sub.id)
        if sub.status == "active":
            # Card that attaches fine but declines on charge, so the next renewal fails.
            self.ensure_default_pm(cust, "pm_card_chargeCustomerFail", tag="failing")
            frozen = self.c.test_helpers.test_clocks.retrieve(clock.id).frozen_time
            self.advance_clock(clock.id, frozen + 32 * DAY)
            sub = self.c.subscriptions.retrieve(sub.id)
            self.log(f"S4 subscription after renewal: status={sub.status}")
        return {"customer": cust.id, "test_clock": clock.id, "subscription": sub.id}

    def seed_s5_refunded(self) -> dict:
        cust = self.ensure_customer("S5")
        pm = self.ensure_default_pm(cust)
        pi = self.ensure_payment(cust.id, pm, "S5-pay-1", 4900, "Orkes Demo Co Pro plan (one month)", "OSD-S5-0001")
        charge = self.c.charges.retrieve(pi.latest_charge)
        refund_id = None
        for r in self.c.refunds.list({"charge": charge.id, "limit": 10}).data:
            if _md(r, "osd_seed_key") == "S5-refund-1":
                refund_id = r.id
        if not refund_id and not charge.refunded:
            r = self.c.refunds.create({"charge": charge.id, "reason": "requested_by_customer",
                                       "metadata": _meta(osd_seed_key="S5-refund-1")}, _idem("refund", "S5"))
            refund_id = r.id
            self.log(f"S5 refunded {charge.id} via {r.id}")
        return {"customer": cust.id, "payment_intents": [pi.id], "charges": [charge.id], "refund": refund_id}

    def seed_subscription_only(self, sid: str, plan: str) -> dict:
        clock = self.ensure_clock(sid)
        cust = self.ensure_customer(sid, clock.id)
        self.ensure_default_pm(cust)
        sub = self.ensure_subscription(cust.id, plan, f"{sid}-sub")
        return {"customer": cust.id, "test_clock": clock.id, "subscription": sub.id}

    def seed_s8_contradictory(self) -> dict:
        cust = self.ensure_customer("S8")
        pm = self.ensure_default_pm(cust)
        ok = self.ensure_payment(cust.id, pm, "S8-pay-1", 4900, "Orkes Demo Co Pro plan (one month)", "OSD-S8-0001")
        # Attempt the same amount with Stripe's always-declining test card: a real failed charge.
        # (pm_card_chargeDeclined cannot be attached to a customer, so it is used directly.)
        failed = self.ensure_payment(cust.id, "pm_card_chargeDeclined", "S8-pay-2-declined", 4900,
                                     "Orkes Demo Co Pro plan (one month)", "OSD-S8-0001", expect_fail=True)
        return {"customer": cust.id, "payment_intents": [ok.id, failed.id], "charges": [ok.latest_charge]}

    def run(self) -> dict:
        acct = account_summary()
        self.log(f"Stripe sandbox {acct['account_id']} ({acct['display_name']}), livemode={acct['livemode']}")
        self.ensure_catalog()
        builders = {
            "S1": lambda: self.seed_duplicate("S1", 3500, "Orkes Demo Co extra seats add-on (5 seats)"),
            "S2": lambda: self.seed_duplicate("S2", 15000, "Orkes Demo Co onboarding package"),
            "S3": self.seed_s3_proration,
            "S4": self.seed_s4_failed_renewal,
            "S5": self.seed_s5_refunded,
            "S6": lambda: self.seed_subscription_only("S6", "starter"),
            "S7": lambda: self.seed_subscription_only("S7", "pro"),
            "S8": self.seed_s8_contradictory,
        }
        for sid, build in builders.items():
            self.log(f"--- {sid}: {SCENARIOS[sid]['title']}")
            self.manifest["scenarios"][sid] = {**SCENARIOS[sid], **build()}
        self.manifest.update({
            "stripe_account": acct["account_id"], "stripe_display_name": acct["display_name"],
            "livemode": acct["livemode"], "seed_version": SEED_VERSION,
            "seeded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        MANIFEST_PATH.write_text(json.dumps(self.manifest, indent=2, sort_keys=True) + "\n")
        self.log(f"wrote {MANIFEST_PATH.relative_to(DATA_DIR.parent)}")
        return self.manifest


def load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(f"{MANIFEST_PATH} not found; run `seed` first or use fixture mode.")
    return json.loads(MANIFEST_PATH.read_text())
