"""Guarded Stripe client: sandbox/test keys only, optionally pinned to one account."""

from __future__ import annotations

import functools

import stripe

from .config import SafetyError, expected_stripe_account, stripe_secret_key


@functools.lru_cache(maxsize=1)
def get_client() -> stripe.StripeClient:
    client = stripe.StripeClient(stripe_secret_key(), max_network_retries=2)
    _verify_sandbox(client)
    return client


def _verify_sandbox(client: stripe.StripeClient) -> None:
    balance = client.v1.balance.retrieve()
    if balance.livemode:
        raise SafetyError("Stripe API reports livemode=true. Refusing to continue.")
    pinned = expected_stripe_account()
    if pinned:
        acct = client.v1.accounts.retrieve_current()
        if acct.id != pinned:
            raise SafetyError(f"Stripe key belongs to {acct.id}, expected {pinned} (STRIPE_EXPECTED_ACCOUNT).")


def account_summary() -> dict:
    client = get_client()
    acct = client.v1.accounts.retrieve_current()
    dash = getattr(getattr(acct, "settings", None), "dashboard", None)
    return {
        "account_id": acct.id,
        "display_name": getattr(dash, "display_name", None),
        "country": acct.country,
        "default_currency": acct.default_currency,
        "livemode": client.v1.balance.retrieve().livemode,
    }


def assert_test_object(obj) -> None:
    """Every Stripe object we act on must be a test-mode object."""
    if getattr(obj, "livemode", False):
        raise SafetyError(f"Refusing to act on live-mode object {getattr(obj, 'id', '?')}")
