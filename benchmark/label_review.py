"""Blind label review for benchmark/dataset.v1.jsonl.

  uv run python benchmark/label_review.py export    # writes the blind sheet (no labels, shuffled)
  uv run python benchmark/label_review.py compare   # after the reviewer fills it in: agreement, kappa, disagreements

The sheet hides the author's gold labels, difficulty tags, Stripe facts, and all model outputs.
"""

from __future__ import annotations

import csv
import json
import random
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATASET = HERE / "dataset.v1.jsonl"
OUT_DIR = HERE / "label-review"
SHEET = OUT_DIR / "blind_review_v1.csv"
INTENTS = ["DUPLICATE_CHARGE_CLAIM", "PRORATION_QUESTION", "REFUND_REQUEST", "CANCELLATION_REQUEST",
           "FAILED_PAYMENT", "UNKNOWN"]
SEED = 20261121


def _shuffled() -> list[dict]:
    """Dataset rows in review order. Case IDs (B001...) are grouped by intent, so the sheet uses opaque
    review IDs (R001...) instead; the mapping is reproduced from the fixed seed, not stored next to the sheet."""
    rows = [json.loads(line) for line in DATASET.read_text().splitlines() if line.strip()]
    random.Random(SEED).shuffle(rows)
    return rows


def review_id_map() -> dict[str, str]:
    return {f"R{i:03d}": r["id"] for i, r in enumerate(_shuffled(), start=1)}


def export() -> None:
    rows = _shuffled()
    OUT_DIR.mkdir(exist_ok=True)
    if SHEET.exists():
        sys.exit(f"{SHEET} already exists; not overwriting a sheet that may contain review work.")
    with SHEET.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["review_id", "message", "reviewer_intent", "notes"])
        for i, r in enumerate(rows, start=1):
            w.writerow([f"R{i:03d}", r["message"], "", ""])
    print(f"wrote {SHEET} ({len(rows)} rows)")


def kappa(a: list[str], b: list[str]) -> float:
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def compare() -> None:
    gold = {r["id"]: r for r in map(json.loads, DATASET.read_text().splitlines()) if r}
    to_case = review_id_map()
    with SHEET.open(newline="", encoding="utf-8-sig") as f:
        review = {to_case[r["review_id"]]: r for r in csv.DictReader(f)}
    missing = [i for i in gold if not (review.get(i, {}).get("reviewer_intent") or "").strip()]
    invalid = [i for i, r in review.items() if (r.get("reviewer_intent") or "").strip()
               and r["reviewer_intent"].strip().upper() not in INTENTS]
    if missing or invalid:
        print(f"Not ready: {len(missing)} rows unlabeled, {len(invalid)} rows with an invalid intent {invalid[:10]}")
        print(f"Allowed values: {', '.join(INTENTS)}")
        sys.exit(1)
    ids = sorted(gold)
    a = [gold[i]["gold_intent"] for i in ids]
    b = [review[i]["reviewer_intent"].strip().upper() for i in ids]
    agree = sum(x == y for x, y in zip(a, b))
    print(f"Agreement: {agree}/{len(ids)} = {agree / len(ids):.1%}; Cohen's kappa = {kappa(a, b):.3f}")
    out = OUT_DIR / "disagreements_v1.csv"
    if out.exists():
        with out.open(newline="", encoding="utf-8-sig") as f:
            if any((r.get("final_intent") or "").strip() for r in csv.DictReader(f)):
                print(f"{out.name} already contains adjudication decisions; not overwriting it.")
                return
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["case_id", "message", "author_intent", "reviewer_intent", "reviewer_notes", "final_intent", "reason"])
        for i, x, y in zip(ids, a, b):
            if x != y:
                w.writerow([i, gold[i]["message"], x, y, review[i].get("notes", ""), "", ""])
    print(f"wrote {out} with {len(ids) - agree} disagreements to adjudicate (fill final_intent and reason)")


if __name__ == "__main__":
    {"export": export, "compare": compare}[sys.argv[1] if len(sys.argv) > 1 else "export"]()
