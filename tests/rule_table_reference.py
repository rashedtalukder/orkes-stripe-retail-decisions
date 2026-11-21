"""TEST-ONLY reference reading of the decision table.

This is NOT a policy engine and is never used by the application. It exists so offline tests can
check the *table's* design (row order, boundaries, safe fallback) before it is published, and so the
`tests/test_rule_conformance_live.py` check can compare its expectations with the real Orkes
BUSINESS_RULE results. If the two ever disagree, Orkes is authoritative and this file is wrong.

Assumed cell semantics (verified against Orkes by the live conformance test):
  blank -> matches anything; "<= 5000", "> 5000", "!= usd" -> comparison; otherwise exact string match.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

RULE_FILE = Path(__file__).resolve().parents[1] / "rules" / "billing_support_policy.v1.csv"
INPUTS = ["factsStatus", "currency", "intent", "alreadyRefunded", "duplicateVerified", "duplicateRefundableCents",
          "prorationPresent", "paymentFailed", "requestedCents"]
_OP = re.compile(r"^\s*(<=|>=|!=|=|<|>)\s*(.+?)\s*$")


def load_rows(path: Path = RULE_FILE) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def cell_matches(cell: str, value) -> bool:
    cell = (cell or "").strip()
    if cell == "":
        return True
    m = _OP.match(cell)
    if m:
        op, rhs = m.groups()
        try:
            lv, rv = float(value), float(rhs)
        except (TypeError, ValueError):
            lv, rv = str(value), rhs
            if op in ("<", ">", "<=", ">="):
                return False
        return {"<=": lv <= rv, ">=": lv >= rv, "<": lv < rv, ">": lv > rv, "=": lv == rv, "!=": lv != rv}[op]
    return str(value) == cell


def first_match(inputs: dict, rows: list[dict[str, str]] | None = None) -> dict[str, str] | None:
    for row in rows or load_rows():
        if all(cell_matches(row[c], inputs.get(c)) for c in INPUTS):
            return row
    return None
