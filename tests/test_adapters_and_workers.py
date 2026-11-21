from types import SimpleNamespace

import pytest

from stripe_decisions.adapters.base import coerce_intent
from stripe_decisions.adapters.claude import ClaudeClassifier
from stripe_decisions.config import SafetyError, stripe_secret_key
from stripe_decisions.workers import classify_intent, fetch_billing_facts


def test_coerce_intent_rejects_unknown_labels():
    assert coerce_intent("REFUND_REQUEST") == ("REFUND_REQUEST", True)
    assert coerce_intent("APPROVE_REFUND") == ("UNKNOWN", False)
    assert coerce_intent(None) == ("UNKNOWN", False)


class _FakeMessages:
    def __init__(self, parsed=None, exc=None):
        self.parsed, self.exc = parsed, exc

    def parse(self, **kw):
        if self.exc:
            raise self.exc
        return SimpleNamespace(parsed_output=self.parsed, model="claude-haiku-5-5", stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=10, output_tokens=3))


def test_claude_result_has_no_invented_confidence():
    c = ClaudeClassifier(client=SimpleNamespace(messages=_FakeMessages(SimpleNamespace(intent="PRORATION_QUESTION"))))
    r = c.classify("why two charges after upgrade?")
    assert r.intent == "PRORATION_QUESTION" and r.schemaValid
    assert r.confidence is None and r.probabilities is None
    assert r.usage == {"input_tokens": 10, "output_tokens": 3}


def test_claude_missing_parse_is_invalid_unknown():
    r = ClaudeClassifier(client=SimpleNamespace(messages=_FakeMessages(None))).classify("x")
    assert r.intent == "UNKNOWN" and not r.schemaValid and r.error


def test_provider_error_becomes_unknown_not_approval():
    r = ClaudeClassifier(client=SimpleNamespace(messages=_FakeMessages(exc=RuntimeError("boom")))).classify("x")
    assert r.intent == "UNKNOWN" and not r.schemaValid and "boom" in r.error


def test_worker_unknown_classifier_is_safe():
    out = classify_intent("hello", "gpt-something")
    assert out["intent"] == "UNKNOWN" and out["schemaValid"] is False


def test_worker_malformed_case_gives_error_facts():
    out = fetch_billing_facts({"caseId": "x", "customerId": "not-a-customer"})
    assert out["factsStatus"] == "ERROR"


def test_worker_fixture_facts_labeled():
    out = fetch_billing_facts({"caseId": "S1", "customerId": "cus_fixtureS1"}, "fixture", "S1")
    assert out["factsSource"] == "fixture" and out["duplicateRefundableCents"] == 3500


def test_currency_mismatch_is_conflicting():
    out = fetch_billing_facts({"caseId": "S1", "customerId": "cus_fixtureS1", "currency": "eur"}, "fixture", "S1")
    assert out["factsStatus"] == "CONFLICTING"


@pytest.mark.parametrize("key", ["sk_live_abc", "rk_live_abc", "pk_test_abc", "abc"])
def test_rejects_non_sandbox_stripe_keys(monkeypatch, key):
    monkeypatch.setenv("STRIPE_SECRET_KEY", key)
    with pytest.raises(SafetyError):
        stripe_secret_key()
