"""LIVE conformance: the real Orkes BUSINESS_RULE vs. the test-only reference reading of the table.

Runs the pinned rule via the side-effect-free `billing_policy_eval` workflow. Skipped unless Conductor
credentials are present. Run: uv run --env-file .env pytest -m live -v
"""

import itertools
import os

import pytest

from rule_table_reference import first_match

pytestmark = [pytest.mark.live,
              pytest.mark.skipif(not os.environ.get("CONDUCTOR_AUTH_KEY"), reason="needs Orkes credentials")]

BASE = {"factsStatus": "VERIFIED", "currency": "usd", "intent": "UNKNOWN", "alreadyRefunded": "NO",
        "duplicateVerified": "NO", "duplicateRefundableCents": 0, "prorationPresent": "NO", "paymentFailed": "NO",
        "requestedCents": 0}

CASES = [
    {}, {"intent": "DUPLICATE_CHARGE_CLAIM", "duplicateVerified": "YES", "duplicateRefundableCents": 5000},
    {"intent": "DUPLICATE_CHARGE_CLAIM", "duplicateVerified": "YES", "duplicateRefundableCents": 5001},
    {"intent": "DUPLICATE_CHARGE_CLAIM", "duplicateVerified": "YES", "duplicateRefundableCents": 1},
    {"intent": "DUPLICATE_CHARGE_CLAIM", "duplicateVerified": "YES", "duplicateRefundableCents": 15000},
    {"intent": "DUPLICATE_CHARGE_CLAIM", "alreadyRefunded": "YES"},
    {"intent": "DUPLICATE_CHARGE_CLAIM", "prorationPresent": "YES"},
    {"intent": "DUPLICATE_CHARGE_CLAIM"},
    {"intent": "REFUND_REQUEST", "requestedCents": 2000}, {"intent": "REFUND_REQUEST", "requestedCents": 2001},
    {"intent": "REFUND_REQUEST", "alreadyRefunded": "YES"}, {"intent": "REFUND_REQUEST", "prorationPresent": "YES"},
    {"intent": "PRORATION_QUESTION", "prorationPresent": "YES"}, {"intent": "PRORATION_QUESTION"},
    {"intent": "FAILED_PAYMENT", "paymentFailed": "YES"}, {"intent": "FAILED_PAYMENT"},
    {"intent": "CANCELLATION_REQUEST"}, {"intent": "UNKNOWN", "paymentFailed": "YES"},
    {"factsStatus": "ERROR", "intent": "DUPLICATE_CHARGE_CLAIM", "duplicateVerified": "YES", "duplicateRefundableCents": 100},
    {"factsStatus": "CONFLICTING"}, {"factsStatus": "INSUFFICIENT", "intent": "REFUND_REQUEST"},
    {"currency": "eur", "intent": "DUPLICATE_CHARGE_CLAIM", "duplicateVerified": "YES", "duplicateRefundableCents": 100},
    {"intent": "NOT_AN_INTENT"},  # normalize_intent maps to UNKNOWN before the rule
]


@pytest.fixture(scope="module")
def api():
    from stripe_decisions.conductor_api import ConductorREST
    return ConductorREST()


@pytest.mark.parametrize("delta", CASES, ids=[str(i) for i in range(len(CASES))])
def test_orkes_matches_reference(api, delta):
    inputs = {**BASE, **delta}
    run = api.execute("billing_policy_eval", 1, {"facts": inputs, "intent": inputs["intent"]}, wait_seconds=20)
    assert run["status"] == "COMPLETED", run.get("reasonForIncompletion")
    got = run["output"]["decision"]
    ref_inputs = {**inputs, "intent": run["output"]["intent"]}
    want = first_match(ref_inputs)
    print(run.get("workflowId"), delta, got)
    assert (got["decision"], got["ruleId"]) == (want["decision"], want["ruleId"])
    assert got["policyVersion"] == "billing-support-policy/v1"
