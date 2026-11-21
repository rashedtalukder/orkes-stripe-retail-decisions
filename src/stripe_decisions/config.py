"""Runtime configuration and safety guards.

All secrets come from environment variables (for example `uv run --env-file .env`, or any secret
manager that exports them). Nothing here reads files containing secrets.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_TAG = "orkes-stripe-decisions"
REPO_ROOT = Path(__file__).resolve().parents[2]
RULES_DIR = REPO_ROOT / "rules"
CONDUCTOR_DIR = REPO_ROOT / "conductor"
DATA_DIR = REPO_ROOT / "data"
FIXTURES_DIR = REPO_ROOT / "fixtures"
RESULTS_DIR = REPO_ROOT / "results"


class SafetyError(RuntimeError):
    """Raised when an operation would violate a project safety invariant."""


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    if value is not None:
        value = value.strip()
    return value or default


def require_env(name: str) -> str:
    value = env(name)
    if not value:
        raise SafetyError(f"Missing required environment variable {name}. See .env.example.")
    return value


def stripe_secret_key() -> str:
    """Return the Stripe key only if it is a sandbox/test key. Live keys are rejected outright."""
    key = require_env("STRIPE_SECRET_KEY")
    if key.startswith(("sk_live_", "rk_live_")):
        raise SafetyError("Live-mode Stripe key detected. This demo only runs against a Stripe sandbox.")
    if not key.startswith(("sk_test_", "rk_test_")):
        raise SafetyError("STRIPE_SECRET_KEY must be a sandbox/test secret or restricted key (sk_test_/rk_test_).")
    return key


def expected_stripe_account() -> str | None:
    """Optional pin: refuse to touch any Stripe account other than this one."""
    return env("STRIPE_EXPECTED_ACCOUNT")


def sandbox_refunds_enabled() -> bool:
    """Second, process-level opt-in for real sandbox refunds (in addition to workflow dryRun=false)."""
    return env("OSD_ALLOW_SANDBOX_REFUNDS", "0") == "1"


@dataclass(frozen=True)
class ConductorSettings:
    server_url: str
    key_id: str
    key_secret: str

    @classmethod
    def from_env(cls) -> "ConductorSettings":
        url = require_env("CONDUCTOR_SERVER_URL").rstrip("/")
        if not url.endswith("/api"):
            url += "/api"
        return cls(url, require_env("CONDUCTOR_AUTH_KEY"), require_env("CONDUCTOR_AUTH_SECRET"))

    @property
    def ui_base(self) -> str:
        return self.server_url[: -len("/api")]
