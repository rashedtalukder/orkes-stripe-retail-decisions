"""Claude adapter: JSON structured output constrained to the shared intent enum.

Claude does not return a calibrated confidence or class probabilities, so those fields stay
None. We do not ask the model to self-report a confidence number.
"""

from __future__ import annotations

import time
from typing import Literal

import anthropic
from pydantic import BaseModel

from ..schemas import IntentResult
from .base import TASK_INSTRUCTION, coerce_intent, intent_catalog_text

IntentLiteral = Literal["DUPLICATE_CHARGE_CLAIM", "PRORATION_QUESTION", "REFUND_REQUEST", "CANCELLATION_REQUEST",
                        "FAILED_PAYMENT", "UNKNOWN"]


class IntentOut(BaseModel):
    intent: IntentLiteral


SYSTEM_PROMPT = f"{TASK_INSTRUCTION}\n\nIntents:\n{intent_catalog_text()}"


class ClaudeClassifier:
    provider = "anthropic"

    def __init__(self, model: str = "claude-haiku-5-5", effort: str | None = "low", max_retries: int | None = None,
                 client: anthropic.Anthropic | None = None):
        self.model = model
        self.effort = effort
        kwargs: dict = {"timeout": 30.0}
        if max_retries is not None:
            kwargs["max_retries"] = max_retries
        self._client = client or anthropic.Anthropic(**kwargs)

    def classify(self, message: str) -> IntentResult:
        t0 = time.perf_counter()
        extra = {"output_config": {"effort": self.effort}} if self.effort else {}
        try:
            resp = self._client.messages.parse(
                model=self.model, max_tokens=1024, system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": f"<customer_message>\n{message}\n</customer_message>"}],
                output_format=IntentOut, **extra)
        except Exception as e:
            return IntentResult(provider="anthropic", model=self.model, intent="UNKNOWN", schemaValid=False,
                                latencyMs=(time.perf_counter() - t0) * 1000, error=f"{type(e).__name__}: {e}"[:500])
        latency = (time.perf_counter() - t0) * 1000
        parsed = getattr(resp, "parsed_output", None)
        raw = parsed.intent if parsed is not None else None
        intent, valid = coerce_intent(raw)
        u = resp.usage
        usage = {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens}
        for k in ("cache_read_input_tokens", "cache_creation_input_tokens"):
            if getattr(u, k, None):
                usage[k] = getattr(u, k)
        return IntentResult(provider="anthropic", model=resp.model, intent=intent,
                            schemaValid=valid and parsed is not None, latencyMs=latency, usage=usage, rawIntent=raw,
                            error=None if parsed is not None else f"no parsed output (stop_reason={resp.stop_reason})")
