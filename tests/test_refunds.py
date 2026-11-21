from types import SimpleNamespace

import pytest

from stripe_decisions.refunds import execute_refund, idempotency_key

BASE = dict(case_id="S1", customer_id="cus_1", decision="APPROVE_REFUND", rule_id="R07",
            policy_version="billing-support-policy/v1", charge_id="ch_1", amount_cents=3500, currency="usd",
            facts_source="stripe_sandbox")


class FakeStripe:
    def __init__(self, existing_key=None, customer="cus_1", amount=3500, refunded=0, livemode=False):
        self.created = []
        md = SimpleNamespace(to_dict=lambda: {"osd_refund_key": existing_key} if existing_key else {})
        self._refunds = [SimpleNamespace(id="re_old", metadata=md)] if existing_key else []
        charge = SimpleNamespace(id="ch_1", livemode=livemode, customer=customer, amount=amount,
                                 amount_refunded=refunded, currency="usd")
        self.charges = SimpleNamespace(retrieve=lambda _id: charge)
        self.refunds = SimpleNamespace(
            list=lambda p: SimpleNamespace(auto_paging_iter=lambda: iter(self._refunds)),
            create=self._create)

    def _create(self, params, options):
        self.created.append((params, options))
        return SimpleNamespace(id="re_new", status="succeeded")


@pytest.fixture
def refunds_enabled(monkeypatch):
    monkeypatch.setenv("OSD_ALLOW_SANDBOX_REFUNDS", "1")


def test_dry_run_is_default(refunds_enabled):
    fake = FakeStripe()
    r = execute_refund(**BASE, client=fake)
    assert r["status"] == "DRY_RUN" and not r["executed"] and fake.created == []


@pytest.mark.parametrize("dry", [None, True, "true", "yes", 0, ""])
def test_anything_but_explicit_false_is_dry_run(refunds_enabled, dry):
    assert execute_refund(**BASE, dry_run=dry, client=FakeStripe())["status"] == "DRY_RUN"


def test_requires_process_opt_in(monkeypatch):
    monkeypatch.delenv("OSD_ALLOW_SANDBOX_REFUNDS", raising=False)
    fake = FakeStripe()
    assert execute_refund(**BASE, dry_run=False, client=fake)["status"] == "DRY_RUN" and fake.created == []


@pytest.mark.parametrize("decision", ["HUMAN_REVIEW", "INVESTIGATE", "EXPLAIN_CHARGE", "NO_ADDITIONAL_REFUND", None])
def test_only_approve_refund_is_actionable(refunds_enabled, decision):
    fake = FakeStripe()
    r = execute_refund(**{**BASE, "decision": decision}, dry_run=False, client=fake)
    assert r["status"] == "REJECTED" and fake.created == []


def test_fixture_facts_never_refund(refunds_enabled):
    fake = FakeStripe()
    assert execute_refund(**{**BASE, "facts_source": "fixture"}, dry_run=False, client=fake)["status"] == "DRY_RUN"
    assert fake.created == []


@pytest.mark.parametrize("amount", [0, -5, "3500", 35.0, True])
def test_amount_must_be_positive_int_cents(refunds_enabled, amount):
    assert execute_refund(**{**BASE, "amount_cents": amount}, dry_run=False, client=FakeStripe())["status"] == "REJECTED"


def test_executes_with_stable_idempotency_key(refunds_enabled):
    fake = FakeStripe()
    r = execute_refund(**BASE, dry_run=False, client=fake)
    assert r["executed"] and r["refundId"] == "re_new"
    params, options = fake.created[0]
    assert options["idempotency_key"] == idempotency_key("ch_1", 3500, BASE["policy_version"]) == r["idempotencyKey"]
    assert params["amount"] == 3500 and params["metadata"]["osd_rule_id"] == "R07"


def test_second_execution_is_detected(refunds_enabled):
    key = idempotency_key("ch_1", 3500, BASE["policy_version"])
    fake = FakeStripe(existing_key=key)
    r = execute_refund(**BASE, dry_run=False, client=fake)
    assert r["status"] == "ALREADY_EXECUTED" and fake.created == []


def test_rejects_foreign_or_overdrawn_or_live_charge(refunds_enabled):
    assert execute_refund(**BASE, dry_run=False, client=FakeStripe(customer="cus_2"))["status"] == "REJECTED"
    assert execute_refund(**BASE, dry_run=False, client=FakeStripe(refunded=1000))["status"] == "REJECTED"
    assert execute_refund(**BASE, dry_run=False, client=FakeStripe(livemode=True))["status"] == "REJECTED"
