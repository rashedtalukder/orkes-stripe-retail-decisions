# Screenshots

These were captured on 2026-11-23 from the real Stripe sandbox (`orkes-stripe-decisions`, test mode) and Orkes
Conductor Developer Edition, after the benchmark and refund verification. They contain no real customer data: every
customer is a synthetic `osd-s*@example.com` sandbox customer. The signed-in Orkes user's name and email were hidden
with CSS before capture. Nothing in either UI was changed while capturing.

Execution views show the Business Rule's `ruleFileLocation` with the commit SHA that was current when the workflows
ran. The repository history has since been rewritten, so that SHA no longer exists; the rule file at the current
pinned commit is byte-identical (SHA-256 `20606ec3c7cb…`, see `rules/policy_release.json`).

## Orkes Conductor (`orkes/`)

| File | What it shows |
|---|---|
| `01-def-billing-rules-only.png` | Rules-only workflow definition: facts → JQ intent normalizer → **BUSINESS_RULE** → finalize → SWITCH → guarded refund |
| `02-def-billing-rules-ai.png` | AI workflow definition: one added task (`sd_classify_intent`), the same Business Rule |
| `03-def-billing-policy-eval.png` | Side-effect-free policy-evaluation workflow the benchmark uses |
| `04-s1-rules-only-execution.png` | S1 execution: every task completed, APPROVE_REFUND branch taken |
| `05-s1-facts-task-output.png` | Verified Stripe facts: duplicate YES, 3500 cents refundable, the two charge IDs |
| `06-s1-business-rule-input.png` | Business Rule input: pinned `ruleFileLocation`, `FIRE_FIRST`, input and output columns |
| `07-s1-business-rule-output-R07.png` | Business Rule output: `APPROVE_REFUND`, rule **R07**, policy version |
| `08-s1-refund-task-output-dry-run.png` | Refund task: `DRY_RUN` with its idempotency key, so no money moved |
| `09-s1-workflow-input-output.png` | Workflow input (structured case) and output (decision, facts, refund result) |
| `10-s1-timeline.png` | Task timeline: the Business Rule evaluates in milliseconds |
| `11-s3-claude-ai-execution.png` | S3 with Claude: "charged twice" after an upgrade |
| `12-s3-claude-classify-output.png` | Claude's recommendation: DUPLICATE_CHARGE_CLAIM, latency, tokens, no confidence field |
| `13-s3-facts-output-proration.png` | Stripe facts: proration YES, no verified duplicate |
| `14-s3-business-rule-output-R09.png` | Business Rule: EXPLAIN_CHARGE (R09), not a refund |
| `15-s3-jev-classify-output.png` | Jev's recommendation for the same case, with per-label probabilities and confidence |
| `16-s8-claude-workflow-output-R12.png` | S8: one success plus one decline, so INVESTIGATE (R12) |
| `17-s9-real-refund-execution.png` | The one real sandbox refund execution |
| `18-s9-refund-task-output-executed.png` | Refund task: `executed: true`, refund `re_3Ue3zQ40…`, SUCCEEDED |
| `19-s9-rerun-execution-no-refund.png` | Re-run: default branch, refund task not executed |
| `20-s9-rerun-business-rule-output-R05.png` | Business Rule: NO_ADDITIONAL_REFUND (R05) |
| `21-benchmark-policy-eval-execution.png` / `22-…-output.png` | A benchmark policy evaluation on Orkes |
| `23-executions-list.png` | Recent executions |
| `24-task-definitions.png` | The three worker task definitions |

## Stripe sandbox (`stripe/`)

| File | What it shows |
|---|---|
| `01-customers.png` | The nine synthetic scenario customers |
| `02-products-plans.png` | Starter $19, Pro $49, Business $149, Enterprise $499 monthly plans |
| `03-s1-customer-duplicate-35.png` / `04-…-detail.png` | S1: two $35 charges with the same billing reference |
| `05-s2-customer-duplicate-150.png` | S2: two $150 charges |
| `06-s3-customer-upgrade-proration.png` / `07-s3-proration-invoice.png` / `08-s3-subscription.png` | S3: Starter → Pro mid-cycle; invoice lines −$9.50 unused Starter time and +$24.50 remaining Pro time |
| `09-s4-subscription-past-due.png` / `10-s4-failed-renewal-invoice.png` | S4: renewal failed, subscription past due |
| `11-s5-refunded-payment.png` | S5: payment already fully refunded |
| `12-s6-…png` / `13-s7-…png` | S6/S7: normal subscriptions, nothing anomalous |
| `14-s8-customer-one-success-one-decline.png` / `15-s8-declined-attempt.png` | S8: one succeeded and one declined $49 attempt |
| `16-s9-customer-refund-target.png` / `17-s9-workflow-refund-payment.png` | S9: $12 duplicate, refunded by the workflow ("refunded due to duplicate charge") |
| `18-payments-list.png` | All sandbox payments, including refunded and failed |
| `19-test-clocks.png` / `20-s3-test-clock-simulation.png` | Test-clock simulations used for proration and renewal |
