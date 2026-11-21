"""Decision-table design tests: priority, $50/$20 boundaries, fallback, and every demo scenario.

These read the CSV with a test-only reference reader (see rule_table_reference.py). The live
conformance test re-checks the same matrix against the real Orkes BUSINESS_RULE task.
"""

import json
from pathlib import Path

import pytest

from rule_table_reference import INPUTS, first_match, load_rows

ROOT = Path(__file__).resolve().parents[1]
BASE = {"factsStatus": "VERIFIED", "currency": "usd", "intent": "UNKNOWN", "alreadyRefunded": "NO",
        "duplicateVerified": "NO", "duplicateRefundableCents": 0, "prorationPresent": "NO", "paymentFailed": "NO",
        "requestedCents": 0}


def decide(**kw):
    row = first_match({**BASE, **kw})
    return (row["decision"], row["ruleId"]) if row else (None, None)


def test_table_shape():
    rows = load_rows()
    assert set(INPUTS) <= set(rows[0].keys())
    assert all(r["policyVersion"] == "billing-support-policy/v1" for r in rows)
    ids = [r["ruleId"] for r in rows]
    assert len(ids) == len(set(ids)), "rule ids must be unique"
    last = rows[-1]
    assert all(last[c] == "" for c in INPUTS) and last["decision"] == "HUMAN_REVIEW", "catch-all must be last"
    allowed = {"APPROVE_REFUND", "HUMAN_REVIEW", "EXPLAIN_CHARGE", "NO_ADDITIONAL_REFUND", "INVESTIGATE"}
    assert {r["decision"] for r in rows} <= allowed


def test_only_one_rule_can_approve_a_refund():
    approving = [r for r in load_rows() if r["decision"] == "APPROVE_REFUND"]
    assert len(approving) == 1
    r = approving[0]
    assert (r["factsStatus"], r["intent"], r["duplicateVerified"], r["alreadyRefunded"]) == \
        ("VERIFIED", "DUPLICATE_CHARGE_CLAIM", "YES", "NO")
    assert r["duplicateRefundableCents"].replace(" ", "") == "<=5000"


@pytest.mark.parametrize("cents,expected", [(1, "APPROVE_REFUND"), (3500, "APPROVE_REFUND"), (5000, "APPROVE_REFUND"),
                                            (5001, "HUMAN_REVIEW"), (15000, "HUMAN_REVIEW")])
def test_duplicate_threshold_boundary(cents, expected):
    assert decide(intent="DUPLICATE_CHARGE_CLAIM", duplicateVerified="YES", duplicateRefundableCents=cents)[0] == expected


@pytest.mark.parametrize("cents,rule", [(0, "R15"), (2000, "R15"), (2001, "R16")])
def test_goodwill_boundary_always_human(cents, rule):
    assert decide(intent="REFUND_REQUEST", requestedCents=cents) == ("HUMAN_REVIEW", rule)


@pytest.mark.parametrize("status,expected", [("ERROR", "HUMAN_REVIEW"), ("CONFLICTING", "INVESTIGATE"),
                                             ("INSUFFICIENT", "INVESTIGATE")])
def test_unverified_facts_never_approve(status, expected):
    # Even a model/customer asserting a small duplicate cannot get an automatic refund without verified facts.
    assert decide(factsStatus=status, intent="DUPLICATE_CHARGE_CLAIM", duplicateVerified="YES",
                  duplicateRefundableCents=100)[0] == expected


def test_already_refunded_beats_duplicate():
    assert decide(intent="DUPLICATE_CHARGE_CLAIM", duplicateVerified="YES", duplicateRefundableCents=0,
                  alreadyRefunded="YES") == ("NO_ADDITIONAL_REFUND", "R05")


def test_non_usd_goes_to_human():
    assert decide(currency="eur", intent="DUPLICATE_CHARGE_CLAIM", duplicateVerified="YES",
                  duplicateRefundableCents=100) == ("HUMAN_REVIEW", "R04")


def test_claimed_duplicate_without_evidence_is_investigated():
    assert decide(intent="DUPLICATE_CHARGE_CLAIM") == ("INVESTIGATE", "R12")


def test_unrecognized_intent_falls_back_to_human_review():
    assert decide(intent="SOMETHING_ELSE") == ("HUMAN_REVIEW", "R99")


def test_intent_cannot_create_refund_without_duplicate_fact():
    for intent in ["PRORATION_QUESTION", "REFUND_REQUEST", "CANCELLATION_REQUEST", "FAILED_PAYMENT", "UNKNOWN"]:
        d, _ = decide(intent=intent, duplicateVerified="YES", duplicateRefundableCents=100)
        assert d != "APPROVE_REFUND", intent


EXPECTED = {"S1": "APPROVE_REFUND", "S2": "HUMAN_REVIEW", "S3": "EXPLAIN_CHARGE", "S4": "INVESTIGATE",
            "S5": "NO_ADDITIONAL_REFUND", "S6": "INVESTIGATE", "S7": "HUMAN_REVIEW", "S8": "INVESTIGATE"}
CLAIMS = {"S1": "DUPLICATE_CHARGE_CLAIM", "S2": "DUPLICATE_CHARGE_CLAIM", "S3": "DUPLICATE_CHARGE_CLAIM",
          "S4": "FAILED_PAYMENT", "S5": "REFUND_REQUEST", "S6": "UNKNOWN", "S7": "REFUND_REQUEST",
          "S8": "DUPLICATE_CHARGE_CLAIM"}


@pytest.mark.parametrize("sid", sorted(EXPECTED))
def test_seeded_scenario_fixtures(sid):
    facts = json.loads((ROOT / "fixtures" / "facts" / f"{sid}.json").read_text())
    inputs = {c: facts.get(c) for c in INPUTS}
    inputs["intent"] = CLAIMS[sid]
    inputs["requestedCents"] = 1500 if sid == "S7" else 0
    assert first_match(inputs)["decision"] == EXPECTED[sid]
