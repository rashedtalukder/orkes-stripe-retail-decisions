# Refund verification (Stripe sandbox)

On 2026-11-21 (UTC), one real refund was executed through the Orkes workflow in Stripe sandbox
`acct_1UdzTR40CXDSlSkI` (`orkes-stripe-decisions`, `livemode=false`), and then verified. The evidence files are
`results/refund-verification/` and `results/demo/demo-rules-S9-executed*.json`.

## Why a dedicated scenario

Refunding S1 would permanently change a demo scenario. The seed script instead creates **S9**: two succeeded
$12.00 charges with the same billing reference, for a separate customer. S9 is skipped by `demo` unless it is
named explicitly. Customer `cus_VfOmr2BhyLnwC2`; duplicate charge `ch_3Ue3zQ40CXDSlSkI0l7iQJXL`.

## Preconditions checked before mutating (`scripts/verify_refund.py pre`)

- The API reports sandbox `livemode=false`, and the account matches `STRIPE_EXPECTED_ACCOUNT`.
- Facts: `VERIFIED`, `duplicateVerified=YES`, `duplicateRefundableCents=1200`, `alreadyRefunded=NO`.
- The charge is test-mode, belongs to the S9 customer, amount 1200 USD, `amount_refunded=0`, no existing refunds.

## Execution

1. The workers were restarted with `OSD_ALLOW_SANDBOX_REFUNDS=1`, for this step only.
2. `demo --mode rules --scenario S9 --execute-refunds` ran workflow `z8af9e2dfe4f-e553-11f1-8cb5-f617bd02df86`:
   decision `APPROVE_REFUND` (rule R07). The `sd_execute_refund` output was `executed: true`, status `SUCCEEDED`,
   refund `re_3Ue3zQ40CXDSlSkI0e4DI4Y5`, idempotency key `osd-refund-070cbe331882aca7702edf67`.
3. The same command ran again, as workflow `z8afa085767c-e553-11f1-b66e-465d83d3abdb`. It re-read Stripe
   (`alreadyRefunded=YES`), and the Business Rule returned `NO_ADDITIONAL_REFUND` (R05), so the refund task did not run.
4. The workers were restarted **without** the opt-in.

## Read-back and idempotency (`scripts/verify_refund.py post`)

| Check | Result |
|---|---|
| Refunds on the charge | exactly 1: `re_3Ue3zQ40CXDSlSkI0e4DI4Y5`, 1200 USD, `succeeded`, reason `duplicate` |
| Refund metadata | `osd_rule_id=R07`, `osd_policy_version=billing-support-policy/v1`, `osd_case_id=S9`, `osd_refund_key=…` |
| Charge after refund | `amount_refunded=1200`, `refunded=true`, `livemode=false` |
| Worker refund function replayed with identical inputs (both opt-ins on) | `ALREADY_EXECUTED`, no new refund |
| Exact Stripe `refunds.create` replayed with the same `Idempotency-Key` | Stripe returned the same `re_3Ue3zQ40CXDSlSkI0e4DI4Y5` |
| Refund count after all replays | still 1 |

## Not tested

- Partial refunds, non-USD charges, and refunds above the $50 automatic limit. Policy sends those to
  HUMAN_REVIEW; only unit tests cover the corresponding guards.
- Stripe-side asynchronous refund failures, which happen after a refund is created.
- Idempotency-key replay after Stripe's ~24-hour key retention. The metadata check covers that case and is
  exercised by the worker replay above.
