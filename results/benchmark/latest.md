# Benchmark report 20261121T015141Z

- Date (UTC): 2026-11-21T01:52:14+00:00; wall time 32.0 s
- Dataset: dataset.v1.jsonl (8 synthetic cases, author-labeled), repeats per provider: 2
- Policy: billing-support-policy/v1 (sha256 20606ec3c7cb), engine: Orkes Conductor BUSINESS_RULE via billing_policy_eval v1
- Policy evaluations: 2 distinct (facts, intent, amount) combinations, 0 failed; gold-decision failures: 0
- Facts: frozen fixtures normalized from real Stripe sandbox reads (fixtures/facts)
- Adapter settings: {"claude_model": "claude-haiku-5-5", "claude_effort": "low", "jev_model": "jev-1.14.0", "retries": 0, "concurrency": 4}
- Costs are ESTIMATES: recorded token usage x list prices in benchmark/pricing.json (retrieved 2026-11-20); cost ceiling 0.05 USD.
- Provider confidence/probability values are not used by the policy and are not compared across providers.
- Raw rows: results/runs/20261121T015141Z

## Model-only classification and policy outcomes

| Metric | anthropic | typesafe |
|---|---|---|
| Model version(s) | claude-haiku-5-5 | jev-1.14.0 |
| Calls (expected) | 16 (16) | 16 (16) |
| Intent accuracy (all calls) | 100.0% | 100.0% |
| Intent accuracy (majority of repeats) | 100.0% | 100.0% |
| Consistency (all repeats agree) | 100.0% | 100.0% |
| Schema-valid outputs | 100.0% (0 invalid) | 100.0% (0 invalid) |
| Provider errors | 0.0% (0) | 0.0% (0) |
| Decision agreement with gold-label policy decision | 100.0% | 100.0% |
| Intent errors whose policy decision was unchanged | 0 of 0 | 0 of 0 |
| Correctly automated | 87.5% | 87.5% |
| Escalated (human review / investigate) | 12.5% | 12.5% |
| Policy violations (more permissive than gold) | 0.0% (0) | 0.0% (0) |
| Policy evaluation failures | 0 | 0 |
| Model latency p50 / p95 (ms) | 1195.8 / 1309.3 | 65.1 / 132.8 |
| Input / output tokens | 11112 / 464 | 8814 / 1424 |
| Est. model cost total (USD) | 0.001343 | 0.00037 |
| Est. cost per correct automated decision (USD) | 9.593e-05 | 2.643e-05 |
| Provider-native confidence returned | no | yes |

## Intent accuracy by difficulty

| Difficulty | anthropic | typesafe |
|---|---|---|
| ambiguous | 100.0% | 100.0% |
| obvious | 100.0% | 100.0% |

## typesafe: accuracy by its own reported confidence (within-provider only)

| Confidence bucket | Calls | Intent accuracy |
|---|---|---|
| [0.95,1.00] | 16 | 100.0% |

## Full workflows on Orkes (stratified sample; separate from model-only latency)

| Mode | Runs | Completed | Model intent accuracy | Workflow decision = gold-policy decision | Workflow p50 / p95 ms | Classify task p50 | Facts task p50 | Business Rule task p50 | Est. model cost |
|---|---|---|---|---|---|---|---|---|---|
| claude | 4 | 4 | 100.0% | 100.0% | 2059.0 / 2404.2 | 1357.5 | 79.0 | 9.5 | 0.000336 |
| jev | 4 | 4 | 100.0% | 100.0% | 1039.5 / 1062.5 | 136.0 | 79.5 | 6.5 | 9.3e-05 |
| rules | 4 | 4 | n/a | 100.0% | 505.5 / 689.6 | None | 81.0 | 10.0 | 0.0 |

## Confusion matrix: anthropic (rows = gold, columns = predicted; all repeats)

| gold \ pred | DUPLICATE_CH | PRORATION_QU | REFUND_REQUE | CANCELLATION | FAILED_PAYME | UNKNOWN |
|---|---|---|---|---|---|---|
| DUPLICATE_CHARGE_CLAIM | 16 | 0 | 0 | 0 | 0 | 0 |
| PRORATION_QUESTION | 0 | 0 | 0 | 0 | 0 | 0 |
| REFUND_REQUEST | 0 | 0 | 0 | 0 | 0 | 0 |
| CANCELLATION_REQUEST | 0 | 0 | 0 | 0 | 0 | 0 |
| FAILED_PAYMENT | 0 | 0 | 0 | 0 | 0 | 0 |
| UNKNOWN | 0 | 0 | 0 | 0 | 0 | 0 |

## Confusion matrix: typesafe (rows = gold, columns = predicted; all repeats)

| gold \ pred | DUPLICATE_CH | PRORATION_QU | REFUND_REQUE | CANCELLATION | FAILED_PAYME | UNKNOWN |
|---|---|---|---|---|---|---|
| DUPLICATE_CHARGE_CLAIM | 16 | 0 | 0 | 0 | 0 | 0 |
| PRORATION_QUESTION | 0 | 0 | 0 | 0 | 0 | 0 |
| REFUND_REQUEST | 0 | 0 | 0 | 0 | 0 | 0 |
| CANCELLATION_REQUEST | 0 | 0 | 0 | 0 | 0 | 0 |
| FAILED_PAYMENT | 0 | 0 | 0 | 0 | 0 | 0 |
| UNKNOWN | 0 | 0 | 0 | 0 | 0 | 0 |
