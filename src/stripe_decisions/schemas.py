"""Shared, validated contracts.

Four kinds of data are kept deliberately separate:
  * BillingCase     – what the customer submitted (structured fields and/or free text)
  * BillingFacts    – verified facts read from Stripe (the only financial evidence)
  * IntentResult    – what a model *thinks* the customer means (never financial evidence)
  * PolicyDecision  – what the Conductor Business Rule permits (the only authority)
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Intent(StrEnum):
    DUPLICATE_CHARGE_CLAIM = "DUPLICATE_CHARGE_CLAIM"
    PRORATION_QUESTION = "PRORATION_QUESTION"
    REFUND_REQUEST = "REFUND_REQUEST"
    CANCELLATION_REQUEST = "CANCELLATION_REQUEST"
    FAILED_PAYMENT = "FAILED_PAYMENT"
    UNKNOWN = "UNKNOWN"


INTENT_DESCRIPTIONS: dict[str, str] = {
    "DUPLICATE_CHARGE_CLAIM": "The customer says they were charged or billed more than once for the same thing.",
    "PRORATION_QUESTION": "The customer asks about a partial, prorated, or unexpected amount after changing plans.",
    "REFUND_REQUEST": "The customer asks for money back, a credit, or a discount that is not about a duplicate charge.",
    "CANCELLATION_REQUEST": "The customer wants to cancel or stop their subscription.",
    "FAILED_PAYMENT": "The customer reports a declined or failed payment, or lost access because a payment did not go through.",
    "UNKNOWN": "None of the above, too vague to tell, or not a billing request.",
}


class Decision(StrEnum):
    APPROVE_REFUND = "APPROVE_REFUND"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    EXPLAIN_CHARGE = "EXPLAIN_CHARGE"
    NO_ADDITIONAL_REFUND = "NO_ADDITIONAL_REFUND"
    INVESTIGATE = "INVESTIGATE"


class FactsStatus(StrEnum):
    VERIFIED = "VERIFIED"
    INSUFFICIENT = "INSUFFICIENT"
    CONFLICTING = "CONFLICTING"
    ERROR = "ERROR"


YesNo = Literal["YES", "NO"]


class BillingCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    caseId: str = Field(min_length=1, max_length=64)
    customerId: str = Field(pattern=r"^cus_[A-Za-z0-9]+$")
    claimType: Intent | None = None  # structured form field (rules-only mode)
    message: str | None = Field(default=None, max_length=4000)  # free text (AI modes)
    chargeId: str | None = Field(default=None, pattern=r"^(ch|py)_[A-Za-z0-9]+$")
    requestedCents: int = Field(default=0, ge=0, le=10_000_000)
    currency: str = Field(default="usd", pattern=r"^[a-z]{3}$")


class BillingFacts(BaseModel):
    """Normalized, verified Stripe facts. YES/NO strings keep Business Rule matching explicit."""

    model_config = ConfigDict(extra="forbid")

    factsStatus: FactsStatus
    factsSource: Literal["stripe_sandbox", "fixture"]
    currency: str = "usd"
    alreadyRefunded: YesNo = "NO"
    duplicateVerified: YesNo = "NO"
    duplicateRefundableCents: int = Field(default=0, ge=0)
    duplicateChargeId: str | None = None
    prorationPresent: YesNo = "NO"
    paymentFailed: YesNo = "NO"
    requestedCents: int = Field(default=0, ge=0)
    targetChargeId: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class IntentResult(BaseModel):
    """Provider-neutral classification output. Confidence fields are only set when the provider returns them."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["typesafe", "anthropic", "fixture"]
    model: str
    intent: Intent
    schemaValid: bool
    latencyMs: float
    probabilities: dict[str, float] | None = None  # provider-native (Jev); not calibrated across providers
    confidence: float | None = None  # provider-native (Jev); None for Claude
    usage: dict[str, int] | None = None
    rawIntent: str | None = None  # what the provider literally returned, before validation
    error: str | None = None

    @field_validator("latencyMs")
    @classmethod
    def _non_negative(cls, v: float) -> float:
        return max(0.0, v)


class PolicyDecision(BaseModel):
    decision: Decision
    allowedAction: str
    ruleId: str
    ruleDescription: str | None = None
    policyVersion: str
