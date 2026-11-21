import json

from stripe_decisions.conductor_defs import TASK_DEFS, build_definitions
from stripe_decisions.config import CONDUCTOR_DIR
from stripe_decisions.policy import RULE_INPUT_COLUMNS, load_release, sha256


def _br(wf):
    return next(t for t in wf["tasks"] if t["type"] == "BUSINESS_RULE")


def test_release_hash_matches_rule_file():
    assert load_release().sha256 == sha256(), "rules CSV changed: bump the policy version and re-publish"


def test_every_workflow_uses_the_identical_business_rule():
    defs = build_definitions()
    brs = [_br(w)["inputParameters"] for w in defs.values()]
    for key in ("ruleFileLocation", "executionStrategy", "outputColumns", "cacheTimeoutMinutes"):
        assert len({json.dumps(b[key]) for b in brs}) == 1, key
    assert all(sorted(b["inputColumns"]) == sorted(RULE_INPUT_COLUMNS) for b in brs)
    assert brs[0]["executionStrategy"] == "FIRE_FIRST"
    assert brs[0]["ruleFileLocation"] == load_release().url


def test_rules_only_and_ai_share_facts_and_policy_inputs_except_intent():
    d = build_definitions()
    a, b = _br(d["billing_rules_only"])["inputParameters"]["inputColumns"], _br(d["billing_rules_ai"])["inputParameters"]["inputColumns"]
    assert a == b  # both read intent from the native JQ normalizer, everything else from verified facts


def test_rules_only_has_no_model_step():
    wf = build_definitions()["billing_rules_only"]
    assert all(t["name"] != "sd_classify_intent" for t in wf["tasks"])


def test_refund_amount_comes_from_verified_facts_only():
    for name in ("billing_rules_only", "billing_rules_ai"):
        sw = next(t for t in build_definitions()[name]["tasks"] if t["type"] == "SWITCH")
        refund = sw["decisionCases"]["APPROVE_REFUND"][0]["inputParameters"]
        assert refund["amountCents"] == "${facts_ref.output.duplicateRefundableCents}"
        assert refund["chargeId"] == "${facts_ref.output.duplicateChargeId}"
        assert list(sw["decisionCases"]) == ["APPROVE_REFUND"]


def test_simple_tasks_have_task_definitions():
    names = {t["name"] for t in TASK_DEFS}
    for wf in build_definitions().values():
        stack = list(wf["tasks"])
        while stack:
            t = stack.pop()
            if t["type"] == "SIMPLE":
                assert t["name"] in names
            for branch in t.get("decisionCases", {}).values():
                stack.extend(branch)


def test_committed_json_matches_generator():
    for name, wf in build_definitions().items():
        assert json.loads((CONDUCTOR_DIR / "workflows" / f"{name}.json").read_text()) == wf, \
            f"run `python -m stripe_decisions conductor generate` ({name})"
    assert json.loads((CONDUCTOR_DIR / "tasks" / "task_definitions.json").read_text()) == TASK_DEFS
