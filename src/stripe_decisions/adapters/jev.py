"""TypeSafe Jev adapter: one Choice question over the shared intent labels.

Jev returns provider-native per-label probabilities and a confidence value; both are passed
through unchanged. They are NOT used by the policy and are not comparable to any Claude field.
"""

from __future__ import annotations

import time

from typesafe_sdk import Choice, RetryPolicy, TypeSafeClient

from ..schemas import INTENT_DESCRIPTIONS, IntentResult
from .base import TASK_INSTRUCTION, coerce_intent


class JevClassifier:
    provider = "typesafe"

    def __init__(self, model: str = "jev-latest", max_retries: int | None = None, client: TypeSafeClient | None = None):
        self.model = model
        retry = RetryPolicy(max_retries=max_retries) if max_retries is not None else None
        self._client = client or TypeSafeClient(model=model, retry=retry, timeout=30)

    def classify(self, message: str) -> IntentResult:
        question = Choice(instructions=TASK_INSTRUCTION, criteria=dict(INTENT_DESCRIPTIONS))
        t0 = time.perf_counter()
        try:
            resp = self._client.system_one(state=message, questions={"intent": question})
        except Exception as e:  # provider/network/validation errors become a reviewable UNKNOWN
            return IntentResult(provider="typesafe", model=self.model, intent="UNKNOWN", schemaValid=False,
                                latencyMs=(time.perf_counter() - t0) * 1000, error=f"{type(e).__name__}: {e}"[:500])
        latency = (time.perf_counter() - t0) * 1000
        answer = resp.choices.get("intent")
        raw = getattr(answer, "choice", None)
        intent, valid = coerce_intent(raw)
        return IntentResult(
            provider="typesafe", model=resp.model, intent=intent, schemaValid=valid and answer is not None,
            latencyMs=latency,
            probabilities=dict(answer.probabilities) if answer is not None else None,
            confidence=answer.confidence if answer is not None else None,
            usage={"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens},
            rawIntent=raw,
        )
