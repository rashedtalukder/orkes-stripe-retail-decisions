# Stripe sandbox setup

## Sandbox used during development

| Field | Value |
|---|---|
| Account | `acct_1UdzTR40CXDSlSkI` |
| Dashboard name | `orkes-stripe-decisions` |
| Mode | sandbox / test (`livemode: false`, verified through the API) |
| Seed version | `seed-v1` |

Non-secret object IDs are listed in `data/stripe_manifest.json`. That file is a cache. The seed script
identifies existing objects by querying Stripe (custom product IDs, price `lookup_key`s, customer emails,
`metadata.osd_seed_key`, test clock names) and never relies on the manifest.

## Use your own sandbox

1. In the Stripe Dashboard, create a sandbox (see https://docs.stripe.com/sandboxes), or use `stripe sandbox create`.
2. Copy the sandbox secret key (`sk_test_...`) into your secret manager. Never put it in a file in this repo.
3. Point `STRIPE_SECRET_KEY` at it, as in `.env.example`, and optionally set `STRIPE_EXPECTED_ACCOUNT`.
4. Check it:

```bash
uv run --env-file .env python -m stripe_decisions stripe-whoami   # must show livemode: false
uv run --env-file .env python -m stripe_decisions seed            # safe to re-run
uv run --env-file .env python -m stripe_decisions verify-seed     # 9/9 expected (S1-S8 demo, S9 refund target)
```

## What gets created (all tagged `metadata.osd_project=orkes-stripe-decisions`)

| Object | Detail |
|---|---|
| Products | `osd_prod_starter|pro|business|enterprise` |
| Prices | monthly USD, lookup keys `osd_<plan>_monthly_usd`: $19, $49, $149, $499 |
| Test clocks | `osd-S3|S4|S6|S7-seed-v1` (one per subscription scenario) |

| Scenario | Real Stripe objects | Verified facts |
|---|---|---|
| S1 small duplicate | 2 succeeded $35 PaymentIntents, same billing ref | duplicate YES, 3500 cents refundable |
| S2 large duplicate | 2 succeeded $150 PaymentIntents, same billing ref | duplicate YES, 15000 cents |
| S3 proration | test clock, Starter subscription, clock +15 days, upgrade to Pro with `proration_behavior=always_invoice` | proration YES, two different charges |
| S4 failed renewal | test clock, Pro subscription, default card switched to `pm_card_chargeCustomerFail`, clock +32 days | invoice open/attempted, subscription `past_due` |
| S5 refunded | succeeded $49 PaymentIntent, then full refund | target charge fully refunded |
| S6 ambiguous | test clock, Starter subscription, paid | nothing anomalous |
| S7 goodwill | test clock, Pro subscription, paid | nothing anomalous (request $15 lives in the case) |
| S8 contradictory | one succeeded $49 PaymentIntent, one `pm_card_chargeDeclined` attempt for $49 | 1 succeeded, 1 failed charge, no duplicate |
| S9 refund target (not in the demo) | 2 succeeded $12 PaymentIntents, same billing ref | duplicate YES; refunded once during [refund verification](refund-verification.md) |

Every scenario uses real API objects; none needed a synthetic stand-in. `verify-seed --write-fixtures` snapshots the
normalized facts to `fixtures/facts/S*.json` with provenance, for offline use.

## Guardrails

- Live keys (`sk_live_`/`rk_live_`) are rejected before any API call. `livemode` from `/v1/balance` must be false.
- The seed only creates and reads objects tagged for this project. There is no delete or cleanup code.
- Duplicate detection needs two **succeeded** charges with the same amount, currency and billing reference
  (`metadata.osd_billing_ref`, else the description) within 72 hours. A customer's claim is never evidence.
- Seeding notes: test-clock customers are only returned by `customers.list` when the `test_clock` is passed, and
  `pm_card_chargeDeclined` cannot be attached to a customer, so it is used directly on the PaymentIntent.
