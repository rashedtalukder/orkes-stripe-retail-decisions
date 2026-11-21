# Benchmark methodology

## Question

With the **same pinned Business Rule** and the **same verified facts**, how do two intent classifiers compare
(TypeSafe Jev and Claude Haiku 5.5)? And once policy is applied, how much does the difference between them matter?

The rules-only mode is not a competitor. It takes the intent from a structured form field, so its intent is
correct by construction. It is the baseline that shows what the policy does when interpretation is not in question.

## Dataset

`benchmark/dataset.v1.jsonl`, built by `benchmark/build_dataset.py` (a test checks that the builder reproduces the
file byte for byte). It has 120 synthetic messages with stable IDs `B001`–`B120`.

- Gold intents: DUPLICATE_CHARGE_CLAIM 30, PRORATION_QUESTION 18, REFUND_REQUEST 22, CANCELLATION_REQUEST 14, FAILED_PAYMENT 16, UNKNOWN 20.
- Difficulty: obvious 69, ambiguous 29, contradictory 11 (the claim conflicts with the paired facts), adversarial 11 (prompt injection or social engineering).
- Each row is paired with a frozen facts fixture (`fixtures/facts/S1..S8.json`), normalized from real Stripe sandbox reads.
- The dataset author assigned the labels, and each row is marked `label_review: pending independent review`. Labels
  were not changed after any model results were seen. Have someone else review them before results are published.

## Phases (reported separately)

| Phase | What runs | Measured |
|---|---|---|
| A. model-only | every adapter on every message, `--repeats` independent calls each, retries disabled | intent accuracy (per call and by majority of repeats), confusion matrix, repeat consistency, schema validity, provider errors, adapter call latency, token usage, estimated cost |
| B. policy | every distinct (facts, intent, requestedCents) combination, for the gold label and every prediction, run through `billing_policy_eval` v1 on Orkes | decision agreement with the gold-label decision, automation, escalation, policy violations |
| C. workflow | a deterministic stratified sample (`--workflow-sample`, default 24 = 6 per difficulty) of full executions per mode (`rules`, `jev`, `claude`) with fixture facts | workflow start-to-end latency, task durations, in-workflow model intent, workflow decision |

How the phases relate:

- Phase A output is written to `classifications.jsonl` before any policy step runs. Each row keeps the adapter's
  normalized result plus `rawResponse` (Jev's answer payload; Claude's stop reason, content block types and text).
- Phase B never runs Python policy. Decisions come from the pinned Orkes `BUSINESS_RULE`, and each evaluation's
  workflow ID is recorded in `policy_evaluations.jsonl`.
- Phase C reports the model's recommendation (`model_intent`) and the workflow's decision (`decision`) as separate
  fields. Workflow IDs are recorded in `workflow_runs.jsonl`.
- Model latency (phase A) is measured around the adapter call. It includes network time to the provider, but not
  Stripe or Conductor. Workflow latency (phase C) is Conductor's start-to-end time, which includes worker polling and
  the facts task. Don't compare the two directly.

## Metric definitions

Unless stated otherwise, every rate's denominator is **all calls** (cases × repeats), including provider errors,
schema-invalid outputs and failed policy evaluations.

- **Intent accuracy**: predicted intent == gold intent.
- **Intent accuracy (majority)**: the most common intent across a case's repeats == gold. Denominator: 120 cases.
- **Consistency**: share of cases where every repeat returned the same intent. This is a measured result; typed
  outputs alone don't guarantee determinism.
- **Schema valid**: the provider output parsed into the intent enum. Invalid output is recorded, counted, and mapped to UNKNOWN.
- **Decision agreement with the gold-label policy decision**: the Orkes decision for the predicted intent equals the
  Orkes decision for the gold intent. This is *rule agreement*. It is reported separately from intent accuracy,
  because a wrong intent can still lead to the same safe decision, and the report counts those cases explicitly
  ("intent errors whose policy decision was unchanged").
- **Policy evaluation failure**: an Orkes evaluation that didn't complete with a decision. It counts as disagreement, never as a match.
- **Correctly automated**: decision agrees with gold and is in {APPROVE_REFUND, EXPLAIN_CHARGE, NO_ADDITIONAL_REFUND}.
- **Escalation rate**: decision in {HUMAN_REVIEW, INVESTIGATE}.
- **Policy violation**: the model-driven decision is more permissive than the gold decision. Ordering:
  INVESTIGATE = HUMAN_REVIEW < EXPLAIN_CHARGE = NO_ADDITIONAL_REFUND < APPROVE_REFUND. Approval requires verified
  duplicate facts, so a misclassification can approve a refund only when such facts exist for that case.
- **Estimated cost**: recorded token usage × list prices in `benchmark/pricing.json`, which has source URLs and a
  retrieval date. No caching, batch or enterprise discounts are assumed. These are estimates, not invoices. The one
  probe call per provider is recorded and counted toward the spend ceiling, but excluded from metrics.
- **Cost per correct automated decision**: estimated model cost / number of correctly automated decisions.
- **Completeness**: `calls == cases × repeats`, every case present with every repeat. The report flags incomplete runs.

## Confidence

Jev returns per-label probabilities and a confidence value. The report shows Jev's accuracy grouped by its own
confidence buckets, which is a within-provider check of whether that number tracks correctness. Claude returns no
confidence or probabilities, and the adapter doesn't ask it to make one up, so no confidence analysis exists for
Claude and no cross-provider confidence comparison is made. Neither field is used by the policy.

## Statistics and what is (not) claimed

- **Intervals**: 95% Wilson score intervals on proportions. Per-call intervals treat a case's 3 repeats as
  independent, which they aren't, so they are optimistic. The per-case (majority-vote) interval is the conservative reading.
- **Paired comparison**: an exact two-sided McNemar test on per-case majority correctness (cases only one model got
  right). Official run: 5 vs 1 discordant cases, p = 0.22, which is **not significant**. No other significance claims are made.
- **Latency percentiles**: p50 and p95 are always reported, with `n`. p99 is reported only when n ≥ 100 (model-only
  phase, n = 360 per model). It is withheld for the 24-run workflow sample.
- **Category results** (obvious 69, ambiguous 29, contradictory 11, adversarial 11 cases) are descriptive. The
  11-case categories have wide intervals.

## Results

- Official run: [results/benchmark/report-20261121T022545Z.md](../results/benchmark/report-20261121T022545Z.md),
  with charts `report-20261121T022545Z-{rates,categories,latency}.png` and raw rows in
  `results/benchmark/raw/20261121T022545Z/`.
- Completion: 360/360 calls per model, 33/33 policy evaluations, 72/72 workflow runs, 0 provider errors, 0 invalid outputs.
- An earlier full run (`results/benchmark/earlier/`) with the same configuration had 2 Jev workflow runs that never
  started because of a client TLS timeout to Orkes. It is kept for transparency. Its model-only results were close
  (Jev 94.7%, Claude 98.3% intent accuracy).

## Cost ceiling

`--max-cost-usd` is enforced before spending. A probe call per provider measures the per-call cost, and the run
aborts if the projected spend for phase A (cases × repeats) plus phase C (in-workflow classifications) would exceed
the ceiling.

## Reproduce

```bash
uv run --env-file .env python -m stripe_decisions workers &      # workers for phase C (one instance)
uv run --env-file .env python -m stripe_decisions benchmark --repeats 3 --max-cost-usd 1
uv sync --group charts && uv run python -m stripe_decisions report             # markdown + PNG charts
```

Each run records the date, model versions returned by the APIs, adapter settings (models, Claude effort, retries,
concurrency), policy version, rule file SHA-256 and URL, dataset size, repeat count, workflow sample IDs, and the cost ceiling.

## Limitations

- The data is synthetic and mostly English (two non-English rows). One author labeled it. The contradictory and
  adversarial categories have 11 cases each, so per-category rates have wide uncertainty.
- Facts are frozen fixtures from 8 scenarios. The benchmark tests interpretation under fixed facts, not Stripe edge cases.
- Each provider is tested in one configuration: Jev 1.14 with default settings, and Claude Haiku 5.5 with
  `effort=low` and structured outputs. Other settings will give other results.
- Phase A runs 4 concurrent requests per provider, and repeats run back to back. Latency depends on network
  location, provider load and time of day; report it with the run's date.
- The workflow phase is a 24-case sample per mode, so its latency percentiles are less stable than phase A's.
