"""Demo cases built from the seeded Stripe manifest (or the fixture snapshots when offline)."""

from __future__ import annotations

from .seed import SCENARIOS, load_manifest

MESSAGES = {
    "S1": "Hi, I just noticed I was charged $35 twice this morning for the extra seats add-on. Can you fix this?",
    "S2": "Your system billed our onboarding package twice, $150 each time. We only bought it once.",
    "S3": "I upgraded from Starter to Pro and now I see two charges this month. Why was I charged twice?",
    "S4": "My account says payment failed and my team lost access. My card should be fine, what happened?",
    "S5": "I'd like a refund for my Pro plan payment of $49.",
    "S6": "Something seems wrong with my bill.",
    "S7": "The app was down for a few hours last week. Could you give me a $15 credit as a courtesy?",
    "S8": "You charged me twice for my Pro plan, I can see two attempts on my card notifications!",
    "S9": "I was billed $12 twice for the extra storage add-on today.",
}


def demo_cases(scenario: str | None = None, offline: bool = False) -> list[dict]:
    manifest = None if offline else load_manifest()
    out = []
    for sid, meta in SCENARIOS.items():
        if scenario and sid != scenario:
            continue
        if not scenario and meta.get("demo") is False:
            continue  # e.g. S9, the refund-execution target, runs only when named explicitly
        customer = manifest["scenarios"][sid]["customer"] if manifest else "cus_fixture" + sid
        out.append({
            "scenario": sid, "title": meta["title"], "expected_decision": meta["expected_decision"],
            "case": {"caseId": sid, "customerId": customer, "claimType": meta["claimType"],
                     "message": MESSAGES[sid], "requestedCents": meta.get("requestedCents", 0), "currency": "usd"},
        })
    return out
