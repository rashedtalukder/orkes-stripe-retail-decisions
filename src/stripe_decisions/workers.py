"""Conductor workers (official conductor-python SDK). Run with: python -m stripe_decisions workers

Workers do I/O, validation and normalization. None of them decides policy.
"""

from __future__ import annotations

import logging
from typing import Any

from conductor.client.automator.task_handler import TaskHandler
from conductor.client.configuration.configuration import Configuration
from conductor.client.worker.worker_task import worker_task
from pydantic import ValidationError

from .adapters import get_classifier
from .facts import fetch_stripe_facts, load_fixture_facts
from .refunds import safe_execute_refund
from .schemas import BillingCase, BillingFacts, FactsStatus, IntentResult

log = logging.getLogger("stripe_decisions.workers")
_classifiers: dict[str, Any] = {}


def fetch_billing_facts(case: dict | None, factsSource: str | None = None, fixtureId: str | None = None) -> dict:
    try:
        c = BillingCase.model_validate(case or {})
    except ValidationError as e:
        return BillingFacts(factsStatus=FactsStatus.ERROR, factsSource="stripe_sandbox",
                            error=f"invalid case: {e.error_count()} validation error(s)").model_dump(mode="json")
    if (factsSource or "stripe").lower() == "fixture":
        facts = load_fixture_facts(fixtureId or c.caseId, requested_cents=c.requestedCents)
    else:
        facts = fetch_stripe_facts(c.customerId, charge_id=c.chargeId, requested_cents=c.requestedCents)
    if facts.factsStatus == FactsStatus.VERIFIED and facts.currency != c.currency:
        facts = facts.model_copy(update={"factsStatus": FactsStatus.CONFLICTING,
                                         "evidence": {**facts.evidence, "reason": "case currency != Stripe currency"}})
    return facts.model_dump(mode="json")


def classify_intent(message: str | None, classifier: str | None) -> dict:
    name = (classifier or "").lower()
    try:
        if name not in _classifiers:
            _classifiers[name] = get_classifier(name)
        if not message or not message.strip():
            raise ValueError("empty customer message")
        return _classifiers[name].classify(message).model_dump(mode="json")
    except Exception as e:
        return IntentResult(provider="fixture", model="none", intent="UNKNOWN", schemaValid=False, latencyMs=0,
                            error=f"{type(e).__name__}: {e}"[:500]).model_dump(mode="json")


@worker_task(task_definition_name="sd_fetch_billing_facts", thread_count=4)
def sd_fetch_billing_facts(case: dict = None, factsSource: str = None, fixtureId: str = None) -> dict:
    return fetch_billing_facts(case, factsSource, fixtureId)


@worker_task(task_definition_name="sd_classify_intent", thread_count=4)
def sd_classify_intent(message: str = None, classifier: str = None) -> dict:
    return classify_intent(message, classifier)


@worker_task(task_definition_name="sd_execute_refund", thread_count=1)
def sd_execute_refund(caseId: str = None, customerId: str = None, decision: str = None, ruleId: str = None,
                      policyVersion: str = None, chargeId: str = None, amountCents: int = None,
                      currency: str = None, factsSource: str = None, dryRun: Any = True) -> dict:
    return safe_execute_refund(case_id=caseId, customer_id=customerId, decision=decision, rule_id=ruleId,
                               policy_version=policyVersion, charge_id=chargeId, amount_cents=amountCents,
                               currency=currency, facts_source=factsSource, dry_run=dryRun)


def run_workers() -> None:
    config = Configuration()  # reads CONDUCTOR_SERVER_URL / CONDUCTOR_AUTH_KEY / CONDUCTOR_AUTH_SECRET
    with TaskHandler(configuration=config, scan_for_annotated_workers=True,
                     import_modules=["stripe_decisions.workers"]) as handler:
        handler.start_processes()
        handler.join_processes()
