# Orkes + Stripe Decisions

**Stop replacing business logic with prompts. Add AI to your rules instead.**

This reference app handles billing support for a fictional SaaS company on [Orkes Conductor](https://orkes.io):

- **Stripe sandbox** data is the only financial evidence.
- A native, version-pinned Conductor **`BUSINESS_RULE`** task is the only policy authority.
- **AI** (TypeSafe Jev or Claude Haiku 5.5) only interprets what the customer's message means.

> AI expands what the workflow can *understand*. Deterministic policy still governs what it can *do*.

This is educational software. The refund policy is a fictional demo policy, not Stripe guidance or legal advice.
Stripe access is sandbox-only, and refunds are dry-run by default.

## Three modes, one pinned rule

| Mode | Workflow | How intent is obtained | Policy |
|---|---|---|---|
| Rules only | `billing_rules_only` v1 | structured form field | `billing_support_policy` BUSINESS_RULE, `FIRE_FIRST`, pinned CSV |
| Rules + Jev | `billing_rules_ai` v1 (`classifier=jev`) | TypeSafe Jev `jev-1.14.0` Choice question | **same** rule file, same version |
| Rules + Claude | `billing_rules_ai` v1 (`classifier=claude`) | Claude `claude-haiku-5-5`, structured output | **same** rule file, same version |

See [docs/architecture.md](docs/architecture.md) for the full design.

## Results (benchmark run `20261121T015231Z`)

120 synthetic, author-labeled cases × 3 repeats per provider. Every case was evaluated against frozen facts captured
from real Stripe sandbox reads, using the same pinned Orkes Business Rule. All 720 model calls completed, with
0 provider errors and 0 schema-invalid outputs.

| | Claude Haiku 5.5 | TypeSafe Jev 1.14 |
|---|---|---|
| Intent accuracy (model, all calls) | 98.3% | 94.7% |
| Consistency across 3 repeats | 100% of cases | 99.2% of cases |
| Decision agreement with the gold-label policy decision | 99.2% | 95.6% |
| Correctly automated (ceiling 29.2%: only 35 of 120 gold decisions are automatable) | 29.2% | 28.1% |
| Policy violations (decision more permissive than gold) | 0 | 0 |
| Model-only latency p50 / p95 | 1194 / 1420 ms | 59 / 82 ms |
| Estimated model cost for 360 calls | $0.0289 | $0.0083 |
| Provider-native confidence | not provided | provided (within-provider calibration in report) |

Full report with confusion matrices, every misclassified case, workflow latency and failure details:
[results/benchmark/report-20261121T015231Z.md](results/benchmark/report-20261121T015231Z.md). Raw rows (model
outputs, Orkes workflow IDs, timings, errors, cost estimates) are in
[results/benchmark/raw/20261121T015231Z/](results/benchmark/raw/20261121T015231Z/).

Read these numbers with their limits in mind: the data is synthetic, one person wrote the labels, and the
contradictory and adversarial categories have only 11 cases each. Each provider was tested in one configuration.
Costs are estimates from list prices. During the workflow phase, 2 of 24 Jev runs never started because the
benchmark client hit a TLS handshake timeout reaching Orkes; those runs still count against Jev in the
all-runs rates. See [docs/benchmark-methodology.md](docs/benchmark-methodology.md).

## Quick start

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), a Stripe **sandbox** secret key, and an Orkes Conductor
application key (the free [Developer Edition](https://developer.orkescloud.com) works). Model keys are only needed
for the AI modes.

```bash
uv sync
cp .env.example .env         # fill in your own values (.env is gitignored)

uv run --env-file .env python -m stripe_decisions stripe-whoami        # must show livemode: false
uv run --env-file .env python -m stripe_decisions seed                 # idempotent sandbox seed
uv run --env-file .env python -m stripe_decisions verify-seed          # 8/8 scenarios from API reads
uv run --env-file .env python -m stripe_decisions conductor register   # task defs + 3 workflows
uv run --env-file .env python -m stripe_decisions workers              # run exactly one instance

uv run --env-file .env python -m stripe_decisions demo --mode rules
uv run --env-file .env python -m stripe_decisions demo --mode jev
uv run --env-file .env python -m stripe_decisions demo --mode claude
```

To reproduce the benchmark:

```bash
uv run --env-file .env python -m stripe_decisions benchmark --repeats 3 --max-cost-usd 1
uv sync --group charts && uv run python -m stripe_decisions report
uv run python -m stripe_decisions report --rebuild <run_id>    # recompute from recorded raw rows, no API calls
```

Tests:

```bash
uv run pytest                                              # offline (93 tests)
uv run --env-file .env pytest -m live -v      # 23 boundary cases against the real Orkes BUSINESS_RULE
```

### Without your own Stripe sandbox

`fixtures/facts/S1..S8.json` hold normalized facts captured from real sandbox reads, labeled with their provenance.
`demo --fixtures` runs the workflows on those facts. You still need an Orkes server, because the decisions come
from the native Business Rule.

## Scenarios (real Stripe sandbox objects)

| | Scenario | Facts verified via the Stripe API | Decision (rule) |
|---|---|---|---|
| S1 | Small duplicate payment | 2 succeeded $35 charges, same billing ref | APPROVE_REFUND (R07), dry-run refund |
| S2 | Large duplicate payment | 2 succeeded $150 charges | HUMAN_REVIEW (R08) |
| S3 | Mid-cycle upgrade with proration | test clock + `always_invoice` proration | EXPLAIN_CHARGE (R09) |
| S4 | Failed renewal | test clock, renewal declined, `past_due` | INVESTIGATE (R13) |
| S5 | Already refunded | full refund on record | NO_ADDITIONAL_REFUND (R06) |
| S6 | "Something seems wrong with my bill." | nothing anomalous | INVESTIGATE (R19) |
| S7 | $15 goodwill request | nothing anomalous | HUMAN_REVIEW (R15) |
| S8 | "Charged twice" but one success + one decline | no verified duplicate | INVESTIGATE (R12) |

All three modes give these decisions against live sandbox data. Setup details: [docs/stripe-setup.md](docs/stripe-setup.md).

## Safety

- Stripe keys must be `sk_test_`/`rk_test_`, the API must report `livemode=false`, and the account can be pinned.
- Refunds run only on `APPROVE_REFUND` with a rule ID, require two separate opt-ins, are never made from fixture
  data, re-read the charge before acting, and use a stable idempotency key.
- Malformed cases, invalid model output and provider errors all become a review or investigation outcome, never an approval.
- Secrets are read from environment variables at runtime (`uv run --env-file .env`). `.env` is gitignored.

## Docs

[Architecture](docs/architecture.md) · [Stripe setup](docs/stripe-setup.md) · [Demo script](docs/demo-script.md) ·
[Benchmark methodology](docs/benchmark-methodology.md) · [Blog outline](docs/blog-outline.md)
