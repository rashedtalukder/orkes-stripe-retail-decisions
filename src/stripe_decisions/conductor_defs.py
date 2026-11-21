"""Generate importable Conductor definitions (JSON) from one template.

The BUSINESS_RULE task block is produced by a single function so every workflow that makes a
billing decision uses the identical rule file URL, execution strategy and output columns.
tests/test_conductor_defs.py asserts this and that the committed JSON matches the generator.
"""

from __future__ import annotations

import json
from typing import Any

from .config import CONDUCTOR_DIR
from .policy import POLICY_VERSION, RULE_INPUT_COLUMNS, RULE_OUTPUT_COLUMNS, PolicyRelease, load_release

OWNER_EMAIL = "orkes-stripe-decisions@example.com"
WORKFLOW_VERSION = 1
INTENTS = ["DUPLICATE_CHARGE_CLAIM", "PRORATION_QUESTION", "REFUND_REQUEST", "CANCELLATION_REQUEST",
           "FAILED_PAYMENT", "UNKNOWN"]

TASK_DEFS: list[dict[str, Any]] = [
    {
        "name": "sd_fetch_billing_facts",
        "description": "Read and normalize verified Stripe sandbox billing facts for a case (or load a labeled fixture).",
        "retryCount": 2, "retryLogic": "EXPONENTIAL_BACKOFF", "retryDelaySeconds": 2,
        "timeoutSeconds": 60, "responseTimeoutSeconds": 45, "timeoutPolicy": "TIME_OUT_WF",
        "inputKeys": ["case", "factsSource", "fixtureId"],
        "outputKeys": ["factsStatus", "factsSource", "currency", "alreadyRefunded", "duplicateVerified",
                       "duplicateRefundableCents", "duplicateChargeId", "prorationPresent", "paymentFailed",
                       "requestedCents", "targetChargeId", "evidence", "error"],
        "ownerEmail": OWNER_EMAIL,
    },
    {
        "name": "sd_classify_intent",
        "description": "Classify customer intent with a configured model adapter (TypeSafe Jev or Claude). Interpretation only.",
        "retryCount": 1, "retryLogic": "FIXED", "retryDelaySeconds": 1,
        "timeoutSeconds": 60, "responseTimeoutSeconds": 45, "timeoutPolicy": "TIME_OUT_WF",
        "inputKeys": ["message", "classifier"],
        "outputKeys": ["provider", "model", "intent", "schemaValid", "latencyMs", "probabilities", "confidence",
                       "usage", "rawIntent", "error"],
        "ownerEmail": OWNER_EMAIL,
    },
    {
        "name": "sd_execute_refund",
        "description": "Guarded, idempotent Stripe sandbox refund. Dry-run unless explicitly enabled twice.",
        "retryCount": 1, "retryLogic": "FIXED", "retryDelaySeconds": 5,
        "timeoutSeconds": 60, "responseTimeoutSeconds": 45, "timeoutPolicy": "TIME_OUT_WF",
        "inputKeys": ["caseId", "customerId", "decision", "ruleId", "policyVersion", "chargeId", "amountCents",
                      "currency", "factsSource", "dryRun"],
        "outputKeys": ["executed", "dryRun", "refundId", "status", "idempotencyKey", "reason"],
        "ownerEmail": OWNER_EMAIL,
    },
]


def business_rule_task(release: PolicyRelease, input_columns: dict[str, str]) -> dict[str, Any]:
    assert sorted(input_columns) == sorted(RULE_INPUT_COLUMNS), "rule inputs must match the decision table"
    return {
        "name": "billing_support_policy",
        "taskReferenceName": "policy_ref",
        "type": "BUSINESS_RULE",
        "description": f"Native Orkes Business Rule. Policy {release.policy_version}, sha256 {release.sha256[:12]}",
        "inputParameters": {
            "ruleFileLocation": release.url,
            "executionStrategy": "FIRE_FIRST",
            "cacheTimeoutMinutes": 5,
            "inputColumns": {k: input_columns[k] for k in RULE_INPUT_COLUMNS},
            "outputColumns": list(RULE_OUTPUT_COLUMNS),
        },
    }


def _facts_columns(intent_expr: str) -> dict[str, str]:
    f = "${facts_ref.output.%s}"
    cols = {c: f % c for c in RULE_INPUT_COLUMNS if c != "intent"}
    cols["intent"] = intent_expr
    return cols


def _normalize_intent_task(raw_expr: str, schema_valid_expr: str | None) -> dict[str, Any]:
    """Native JQ: anything outside the intent enum (or schema-invalid model output) becomes UNKNOWN."""
    query = ('.raw as $r | .schemaValid as $v | '
             'if ((.allowed | any(. == $r)) and ($v == null or $v == true)) then $r else "UNKNOWN" end')
    return {
        "name": "normalize_intent", "taskReferenceName": "intent_ref", "type": "JSON_JQ_TRANSFORM",
        "inputParameters": {"raw": raw_expr, "schemaValid": schema_valid_expr, "allowed": INTENTS,
                            "queryExpression": query},
    }


def _finalize_task() -> dict[str, Any]:
    """Safe default: if no rule matched (empty result), the decision is HUMAN_REVIEW."""
    query = ('.r as $r | {decision: ($r.decision // "HUMAN_REVIEW"), allowedAction: ($r.allowedAction // "ESCALATE_TO_HUMAN"), '
             'ruleId: ($r.ruleId // "NO_MATCH"), ruleDescription: ($r.ruleDescription // "No rule matched; safe default"), '
             'policyVersion: ($r.policyVersion // .pv)}')
    return {
        "name": "finalize_decision", "taskReferenceName": "decision_ref", "type": "JSON_JQ_TRANSFORM",
        "inputParameters": {"r": "${policy_ref.output.result}", "pv": POLICY_VERSION, "queryExpression": query},
    }


def _refund_switch() -> dict[str, Any]:
    return {
        "name": "permitted_action", "taskReferenceName": "action_ref", "type": "SWITCH",
        "evaluatorType": "value-param", "expression": "decision",
        "inputParameters": {"decision": "${decision_ref.output.result.decision}"},
        "decisionCases": {
            "APPROVE_REFUND": [{
                "name": "sd_execute_refund", "taskReferenceName": "refund_ref", "type": "SIMPLE",
                "inputParameters": {
                    "caseId": "${workflow.input.case.caseId}",
                    "customerId": "${workflow.input.case.customerId}",
                    "decision": "${decision_ref.output.result.decision}",
                    "ruleId": "${decision_ref.output.result.ruleId}",
                    "policyVersion": "${decision_ref.output.result.policyVersion}",
                    # Refund amount comes ONLY from verified Stripe facts, never from the customer or a model.
                    "chargeId": "${facts_ref.output.duplicateChargeId}",
                    "amountCents": "${facts_ref.output.duplicateRefundableCents}",
                    "currency": "${facts_ref.output.currency}",
                    "factsSource": "${facts_ref.output.factsSource}",
                    "dryRun": "${workflow.input.dryRun}",
                },
            }],
        },
        "defaultCase": [],
    }


def _facts_task() -> dict[str, Any]:
    return {
        "name": "sd_fetch_billing_facts", "taskReferenceName": "facts_ref", "type": "SIMPLE",
        "inputParameters": {"case": "${workflow.input.case}", "factsSource": "${workflow.input.factsSource}",
                            "fixtureId": "${workflow.input.fixtureId}"},
    }


def _common_output(intent_source: str) -> dict[str, str]:
    return {
        "caseId": "${workflow.input.case.caseId}",
        "intentSource": intent_source,
        "intent": "${intent_ref.output.result}",
        "facts": "${facts_ref.output}",
        "ruleEngineResult": "${policy_ref.output.result}",
        "decision": "${decision_ref.output.result}",
        "refund": "${refund_ref.output}",
        "ruleFileLocation": "${policy_ref.input.ruleFileLocation}",
    }


def _workflow(name: str, description: str, tasks: list[dict], output: dict, input_params: list[str]) -> dict:
    return {
        "name": name, "description": description, "version": WORKFLOW_VERSION, "schemaVersion": 2,
        "ownerEmail": OWNER_EMAIL, "restartable": True, "timeoutPolicy": "TIME_OUT_WF", "timeoutSeconds": 300,
        "inputParameters": input_params, "outputParameters": output, "tasks": tasks,
    }


def build_definitions(release: PolicyRelease | None = None) -> dict[str, dict]:
    release = release or load_release()
    rules_only = _workflow(
        "billing_rules_only",
        "Rules only: structured billing case + verified Stripe facts -> native Business Rule -> permitted action. No model inference.",
        [_facts_task(), _normalize_intent_task("${workflow.input.case.claimType}", None),
         business_rule_task(release, _facts_columns("${intent_ref.output.result}")), _finalize_task(), _refund_switch()],
        _common_output("structured_form_field"),
        ["case", "factsSource", "fixtureId", "dryRun"],
    )
    ai_assisted = _workflow(
        "billing_rules_ai",
        "Rules + AI: model classifies free-text intent (interpretation only) -> same verified facts -> SAME pinned Business Rule.",
        [{"name": "sd_classify_intent", "taskReferenceName": "classify_ref", "type": "SIMPLE",
          "inputParameters": {"message": "${workflow.input.case.message}", "classifier": "${workflow.input.classifier}"}},
         _facts_task(), _normalize_intent_task("${classify_ref.output.intent}", "${classify_ref.output.schemaValid}"),
         business_rule_task(release, _facts_columns("${intent_ref.output.result}")), _finalize_task(), _refund_switch()],
        {**_common_output("model:${classify_ref.output.provider}/${classify_ref.output.model}"),
         "classification": "${classify_ref.output}"},
        ["case", "classifier", "factsSource", "fixtureId", "dryRun"],
    )
    policy_eval = _workflow(
        "billing_policy_eval",
        "Benchmark helper: evaluate the SAME pinned Business Rule on supplied (frozen) facts + intent. No side effects.",
        [_normalize_intent_task("${workflow.input.intent}", None),
         business_rule_task(release, {c: ("${intent_ref.output.result}" if c == "intent" else "${workflow.input.facts.%s}" % c)
                                      for c in RULE_INPUT_COLUMNS}),
         _finalize_task()],
        {"intent": "${intent_ref.output.result}", "ruleEngineResult": "${policy_ref.output.result}",
         "decision": "${decision_ref.output.result}", "ruleFileLocation": "${policy_ref.input.ruleFileLocation}"},
        ["facts", "intent"],
    )
    return {w["name"]: w for w in (rules_only, ai_assisted, policy_eval)}


def write_definitions() -> list[str]:
    out = []
    (CONDUCTOR_DIR / "workflows").mkdir(parents=True, exist_ok=True)
    (CONDUCTOR_DIR / "tasks").mkdir(parents=True, exist_ok=True)
    for name, wf in build_definitions().items():
        p = CONDUCTOR_DIR / "workflows" / f"{name}.json"
        p.write_text(json.dumps(wf, indent=2) + "\n")
        out.append(str(p))
    p = CONDUCTOR_DIR / "tasks" / "task_definitions.json"
    p.write_text(json.dumps(TASK_DEFS, indent=2) + "\n")
    out.append(str(p))
    return out
