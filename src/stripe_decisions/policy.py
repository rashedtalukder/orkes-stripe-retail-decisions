"""Business Rule file metadata: location, content hash, and the pinned published URL.

The decision table (rules/billing_support_policy.v1.csv) is the policy. This module never
interprets it; it only tells the workflow *which immutable copy* of the table to load.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .config import RULES_DIR

RULE_FILE = RULES_DIR / "billing_support_policy.v1.csv"
RELEASE_FILE = RULES_DIR / "policy_release.json"
POLICY_VERSION = "billing-support-policy/v1"
RULE_INPUT_COLUMNS = [
    "factsStatus", "currency", "intent", "alreadyRefunded", "duplicateVerified",
    "duplicateRefundableCents", "prorationPresent", "paymentFailed", "requestedCents",
]
RULE_OUTPUT_COLUMNS = ["decision", "allowedAction", "ruleId", "ruleDescription", "policyVersion"]


def sha256(path: Path = RULE_FILE) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class PolicyRelease:
    policy_version: str
    rule_file: str
    sha256: str
    url: str
    commit: str | None

    @property
    def pinned(self) -> bool:
        """True only when the URL points at an immutable commit, not a branch."""
        return bool(self.commit) and self.commit in self.url


def load_release() -> PolicyRelease:
    data = json.loads(RELEASE_FILE.read_text())
    return PolicyRelease(data["policy_version"], data["rule_file"], data["sha256"], data["url"], data.get("commit"))
