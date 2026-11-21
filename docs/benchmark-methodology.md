# Benchmark methodology

## Question

With the **same pinned Business Rule** and the **same verified facts**, how do two intent classifiers differ
(TypeSafe Jev vs Claude Haiku 5.5), and how much does that difference matter once policy is applied?

The rules-only mode is not a competitor. It takes the intent from a structured form field, so its intent is
correct by construction. It is the baseline that shows what the policy does when interpretation is not in question.

## Dataset

`benchmark/dataset.v1.jsonl`, built by `benchmark/build_dataset.py`: 120 synthetic messages.

- Gold intents: DUPLICATE_CHARGE_CLAIM 30, PRORATION_QUESTION 18, REFUND_REQUEST 22, CANCELLATION_REQUEST 14, FAILED_PAYMENT 16, UNKNOWN 20.
- Difficulty: obvious 69, ambiguous 29, contradictory 11 (claim conflicts with facts), adversarial 11 (prompt injection / social engineering).
- Each row is paired with a frozen facts fixture (`fixtures/facts/S1..S8.json`), normalized from real Stripe sandbox reads.
- Labels were assigned by the dataset author and are marked `label_review: pending independent review`. Have
  someone else review them before you publish results.

## Phases (reported separately)

| Phase | What runs | Measured |
|---|---|---|
| A. model-only | each adapter on every message, `--repeats` times, retries disabled | intent accuracy, confusion matrix, repeat consistency, schema validity, errors, model call latency, token usage |
| B. policy | every distinct (facts, intent, requestedCents) pair, for the gold label and every prediction, run through `billing_policy_eval` on Orkes | decision correctness, automation, escalation, policy violations |
| C. workflow | `--workflow-sample` full executions per mode (`rules`, `jev`, `claude`) with fixture facts | workflow latency p50/p95, classify task and Business Rule task durations |

Model latency (phase A) is measured around the adapter call. It includes network time to the provider but not
Stripe or Conductor. Workflow latency (phase C) is Conductor's start-to-end time, which includes worker polling.
Don't compare the two directly.

## Metric definitions

- **Intent accuracy**: predicted intent == gold intent, over all calls (cases x repeats).
- **Consistency**: share of cases where every repeat returned the same intent. This is a measured result; typed
  outputs alone don't guarantee determinism.
- **Schema valid**: the provider output parsed into the intent enum. Invalid output becomes UNKNOWN.
- **End-to-end decision correct**: decision with the predicted intent == decision with the gold intent, both from the Orkes rule.
- **Correctly automated**: decision correct and in {APPROVE_REFUND, EXPLAIN_CHARGE, NO_ADDITIONAL_REFUND}.
- **Escalation rate**: decision in {HUMAN_REVIEW, INVESTIGATE}.
- **Policy violation**: the model-driven decision is more permissive than the gold decision (ordering:
  INVESTIGATE = HUMAN_REVIEW < EXPLAIN_CHARGE = NO_ADDITIONAL_REFUND < APPROVE_REFUND). Approval requires verified
  duplicate facts, so a misclassification can approve a refund only when such facts exist.
- **Estimated cost**: recorded token usage x list prices in `benchmark/pricing.json` (with source URLs and retrieval
  date). No caching, batch or enterprise discounts are assumed. These are estimates, not invoices.
- **Cost per correct automated decision**: estimated model cost / number of correctly automated decisions.

## Provider-specific fields

Jev returns per-label probabilities and a confidence value. Claude returns neither, and the adapter doesn't ask it
to make one up. These fields are stored but not used by the policy, and they aren't compared across providers.

## Reproduce

```bash
uv run --env-file .env python -m stripe_decisions workers &      # workers for phase C
uv run --env-file .env python -m stripe_decisions benchmark --repeats 3 --max-cost-usd 1.00
uv sync --group charts && uv run python -m stripe_decisions report             # markdown + PNG charts
```

Each run records the date, model versions returned by the APIs, adapter settings (Claude effort, retries,
concurrency), policy version, rule file SHA-256, dataset size and repeat count. The `--max-cost-usd` ceiling stops
the run before spending if the projected cost (from a probe call) exceeds it.

## Limitations

- The data is synthetic and English-heavy (two non-English rows). Labels have one author.
- Facts are frozen fixtures from 8 scenarios. The benchmark tests interpretation under fixed facts, not Stripe edge cases.
- One provider configuration each (Jev 1.14 default settings, Claude Haiku 5.5 with `effort=low`). Other settings
  will give other results.
- Latency depends on network location and time of day. Report it with the run's date.
