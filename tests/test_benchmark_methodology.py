"""Regression tests for benchmark methodology (no network)."""

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import pytest

from stripe_decisions import benchmark as bm
from stripe_decisions.schemas import IntentResult

ROOT = Path(__file__).resolve().parents[1]

DS = [
    {"id": "B001", "message": "m1", "gold_intent": "DUPLICATE_CHARGE_CLAIM", "difficulty": "obvious", "facts_fixture": "S1", "requestedCents": 0},
    {"id": "B002", "message": "m2", "gold_intent": "REFUND_REQUEST", "difficulty": "adversarial", "facts_fixture": "S7", "requestedCents": 1500},
]


def row(provider, rep, id_, intent, gold, diff="obvious", error=None, valid=True, conf=None):
    return {"provider": provider, "rep": rep, "id": id_, "intent": intent, "gold_intent": gold, "difficulty": diff,
            "schemaValid": valid, "error": error, "latencyMs": 100.0, "model": "m", "usage": {"input_tokens": 10},
            "est_cost_usd": 0.001, "confidence": conf}


def ok(decision):
    return {"ok": True, "decision": decision}


# ---------- dataset integrity ----------
def test_dataset_ids_labels_and_categories():
    rows = bm.load_dataset()
    assert len(rows) == 120
    assert [r["id"] for r in rows] == [f"B{i:03d}" for i in range(1, 121)]
    assert {r["gold_intent"] for r in rows} == set(bm.INTENTS)
    assert set(Counter(r["difficulty"] for r in rows)) == set(bm.DIFFICULTIES)
    assert all((ROOT / "fixtures" / "facts" / f"{r['facts_fixture']}.json").exists() for r in rows)
    assert all(isinstance(r["requestedCents"], int) and r["requestedCents"] >= 0 for r in rows)


def test_dataset_file_is_reproducible_from_builder(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_dataset", ROOT / "benchmark" / "build_dataset.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = tmp_path / "d.jsonl"
    mod.build(out)
    assert hashlib.sha256(out.read_bytes()).hexdigest() == hashlib.sha256((ROOT / "benchmark" / "dataset.v1.jsonl").read_bytes()).hexdigest()


# ---------- denominators / failures ----------
def test_failed_policy_evaluations_never_count_as_agreement():
    # Both gold and predicted evaluations failed: must NOT count as agreement (old bug: None == None).
    decided = {("S1", "DUPLICATE_CHARGE_CLAIM", 0): {"ok": False, "decision": None},
               ("S7", "REFUND_REQUEST", 1500): ok("HUMAN_REVIEW")}
    cls = [row("p", 1, "B001", "DUPLICATE_CHARGE_CLAIM", "DUPLICATE_CHARGE_CLAIM"),
           row("p", 1, "B002", "REFUND_REQUEST", "REFUND_REQUEST", "adversarial")]
    rep = bm.summarize(cls, decided, DS, [], {})
    p = rep["providers"]["p"]
    assert p["decision_agreement_with_gold_policy"] == 0.5
    assert p["policy_evaluation_failures"] == 1
    assert rep["policy_evaluations"]["gold_decision_failures"] == ["B001"]


def test_errors_and_invalid_outputs_stay_in_denominators():
    decided = {("S1", "DUPLICATE_CHARGE_CLAIM", 0): ok("APPROVE_REFUND"), ("S1", "UNKNOWN", 0): ok("INVESTIGATE"),
               ("S7", "REFUND_REQUEST", 1500): ok("HUMAN_REVIEW")}
    cls = [row("p", 1, "B001", "UNKNOWN", "DUPLICATE_CHARGE_CLAIM", error="Timeout", valid=False),
           row("p", 1, "B002", "REFUND_REQUEST", "REFUND_REQUEST", "adversarial")]
    p = bm.summarize(cls, decided, DS, [], {})["providers"]["p"]
    assert p["calls"] == 2 and p["intent_accuracy"] == 0.5 and p["errors"] == 1 and p["invalid_outputs"] == 1
    assert p["decision_agreement_with_gold_policy"] == 0.5
    assert p["model_latency_ms"]["n"] == 1  # latency excludes errored calls, and says so via n


def test_intent_accuracy_and_rule_agreement_are_reported_separately():
    # Wrong intent that the policy maps to the same decision: intent wrong, decision agrees.
    decided = {("S7", "REFUND_REQUEST", 1500): ok("HUMAN_REVIEW"), ("S7", "CANCELLATION_REQUEST", 1500): ok("HUMAN_REVIEW"),
               ("S1", "DUPLICATE_CHARGE_CLAIM", 0): ok("APPROVE_REFUND")}
    cls = [row("p", 1, "B002", "CANCELLATION_REQUEST", "REFUND_REQUEST", "adversarial"),
           row("p", 1, "B001", "DUPLICATE_CHARGE_CLAIM", "DUPLICATE_CHARGE_CLAIM")]
    p = bm.summarize(cls, decided, DS, [], {})["providers"]["p"]
    assert p["intent_accuracy"] == 0.5
    assert p["decision_agreement_with_gold_policy"] == 1.0
    assert p["intent_errors"] == 1 and p["intent_errors_with_same_decision"] == 1


def test_policy_violation_detects_more_permissive_outcome():
    decided = {("S1", "DUPLICATE_CHARGE_CLAIM", 0): ok("APPROVE_REFUND"), ("S1", "UNKNOWN", 0): ok("INVESTIGATE"),
               ("S7", "REFUND_REQUEST", 1500): ok("HUMAN_REVIEW")}
    ds = [dict(DS[0], gold_intent="UNKNOWN")]
    cls = [row("p", 1, "B001", "DUPLICATE_CHARGE_CLAIM", "UNKNOWN")]
    p = bm.summarize(cls, decided, ds, [], {})["providers"]["p"]
    assert p["policy_violations"] == 1


def test_completeness_flags_missing_repeats():
    decided = {("S1", "DUPLICATE_CHARGE_CLAIM", 0): ok("APPROVE_REFUND"), ("S7", "REFUND_REQUEST", 1500): ok("HUMAN_REVIEW")}
    cls = [row("p", 1, "B001", "DUPLICATE_CHARGE_CLAIM", "DUPLICATE_CHARGE_CLAIM"),
           row("p", 2, "B001", "DUPLICATE_CHARGE_CLAIM", "DUPLICATE_CHARGE_CLAIM"),
           row("p", 1, "B002", "REFUND_REQUEST", "REFUND_REQUEST", "adversarial")]  # B002 rep 2 missing
    p = bm.summarize(cls, decided, DS, [], {})["providers"]["p"]
    assert p["expected_calls"] == 4 and p["calls"] == 3 and p["complete"] is False


def test_confidence_analyzed_only_when_provider_reports_it():
    decided = {("S1", "DUPLICATE_CHARGE_CLAIM", 0): ok("APPROVE_REFUND"), ("S7", "REFUND_REQUEST", 1500): ok("HUMAN_REVIEW")}
    with_conf = [row("jev", 1, "B001", "DUPLICATE_CHARGE_CLAIM", "DUPLICATE_CHARGE_CLAIM", conf=0.99),
                 row("jev", 1, "B002", "REFUND_REQUEST", "REFUND_REQUEST", "adversarial", conf=0.6)]
    without = [row("claude", 1, "B001", "DUPLICATE_CHARGE_CLAIM", "DUPLICATE_CHARGE_CLAIM"),
               row("claude", 1, "B002", "REFUND_REQUEST", "REFUND_REQUEST", "adversarial")]
    rep = bm.summarize(with_conf + without, decided, DS, [], {})["providers"]
    assert rep["jev"]["confidence_reported_by_provider"] and rep["jev"]["accuracy_by_provider_confidence"]
    assert not rep["claude"]["confidence_reported_by_provider"] and rep["claude"]["accuracy_by_provider_confidence"] is None


def test_failed_workflow_runs_stay_in_denominator():
    decided = {("S1", "DUPLICATE_CHARGE_CLAIM", 0): ok("APPROVE_REFUND"), ("S7", "REFUND_REQUEST", 1500): ok("HUMAN_REVIEW")}
    wf = [{"mode": "jev", "id": "B001", "gold_intent": "DUPLICATE_CHARGE_CLAIM", "status": "COMPLETED", "decision": "APPROVE_REFUND",
           "model_intent": "DUPLICATE_CHARGE_CLAIM", "workflow_ms": 900},
          {"mode": "jev", "id": "B002", "gold_intent": "REFUND_REQUEST", "status": "FAILED", "decision": None,
           "model_intent": None, "workflow_ms": None}]
    cls = [row("jev", 1, "B001", "DUPLICATE_CHARGE_CLAIM", "DUPLICATE_CHARGE_CLAIM"),
           row("jev", 1, "B002", "REFUND_REQUEST", "REFUND_REQUEST", "adversarial")]
    w = bm.summarize(cls, decided, DS, wf, {})["workflow"]["jev"]
    assert w["runs"] == 2 and w["completed"] == 1 and w["failed_or_incomplete"] == 1
    assert w["workflow_decision_agreement_with_gold_policy"] == 0.5 and w["model_intent_accuracy"] == 0.5


# ---------- sampling and cost ----------
def test_stratified_sample_is_deterministic_and_covers_all_categories():
    rows = bm.load_dataset()
    a, b = bm.stratified_sample(rows, 24), bm.stratified_sample(rows, 24)
    assert a == b and len(a) == 24
    assert Counter(r["difficulty"] for r in a) == {d: 6 for d in bm.DIFFICULTIES}


def test_cost_projection_includes_workflow_phase():
    assert bm.project_cost(0.001, 120, 3, 24) == pytest.approx(0.001 * 384)


class FakeClf:
    provider = "anthropic"

    def __init__(self, fail_ids=()):
        self.calls = 0
        self.fail_ids = set(fail_ids)

    def classify(self, message):
        self.calls += 1
        if message in self.fail_ids:
            return IntentResult(provider="anthropic", model="claude-haiku-5-5", intent="UNKNOWN", schemaValid=False,
                                latencyMs=5, error="Timeout")
        return IntentResult(provider="anthropic", model="claude-haiku-5-5", intent="DUPLICATE_CHARGE_CLAIM",
                            schemaValid=True, latencyMs=5, usage={"input_tokens": 700, "output_tokens": 20})


def test_model_only_runs_every_repeat_independently_and_records_failures(tmp_path):
    clf = FakeClf(fail_ids={"m2"})
    rows = bm.run_model_only(["claude"], 3, 1.0, 2, tmp_path, DS, 0, classifier_factory=lambda _: clf)
    assert clf.calls == 1 + 2 * 3  # probe + rows x repeats, each a separate call
    assert len(rows) == 6 and Counter(r["rep"] for r in rows) == {1: 2, 2: 2, 3: 2}
    assert sum(bool(r["error"]) for r in rows) == 3  # failures kept, not dropped
    lines = [json.loads(l) for l in (tmp_path / "classifications.jsonl").read_text().splitlines()]
    assert len(lines) == 7 and lines[0]["probe"] is True  # raw outputs written before any policy step


def test_cost_ceiling_aborts_before_spending(tmp_path):
    clf = FakeClf()
    with pytest.raises(SystemExit):
        bm.run_model_only(["claude"], 3, 0.0000001, 2, tmp_path, DS, 0, classifier_factory=lambda _: clf)
    assert clf.calls == 1  # only the probe


def test_policy_eval_exception_is_recorded_not_raised(tmp_path):
    class Boom:
        def execute(self, *a, **k):
            raise RuntimeError("orkes down")
    out = bm.evaluate_policy({("S1", "UNKNOWN", 0)}, tmp_path, api=Boom())
    assert out[("S1", "UNKNOWN", 0)]["ok"] is False and "orkes down" in out[("S1", "UNKNOWN", 0)]["error"]


def test_recorded_spend_hard_stop_between_repeats(tmp_path):
    class Escalating(FakeClf):
        def classify(self, message):
            r = super().classify(message)
            if self.calls > 1:  # probe is cheap, real calls are 1000x more expensive than projected
                r = r.model_copy(update={"usage": {"input_tokens": 700_000, "output_tokens": 0}})
            return r
    clf = Escalating()
    with pytest.raises(SystemExit, match="Recorded spend"):
        bm.run_model_only(["claude"], 3, 0.1, 2, tmp_path, DS, 0, classifier_factory=lambda _: clf)
    assert clf.calls == 1 + len(DS)  # stopped after the first repeat


def test_workflow_failures_are_attributed():
    decided = {("S1", "DUPLICATE_CHARGE_CLAIM", 0): ok("APPROVE_REFUND"), ("S7", "REFUND_REQUEST", 1500): ok("HUMAN_REVIEW")}
    wf = [{"mode": "jev", "id": "B001", "gold_intent": "DUPLICATE_CHARGE_CLAIM", "status": "COMPLETED", "decision": "APPROVE_REFUND",
           "model_intent": "DUPLICATE_CHARGE_CLAIM", "workflowId": "w1", "workflow_ms": 900},
          {"mode": "jev", "id": "B002", "gold_intent": "REFUND_REQUEST", "status": "ERROR", "decision": None,
           "model_intent": None, "workflowId": None, "error": "URLError: handshake timed out"}]
    w = bm.summarize([], decided, DS, wf, {})["workflow"]["jev"]
    assert w["not_started_client_error"] == 1 and w["started_not_completed"] == 0
    assert w["model_intent_accuracy"] == 0.5 and w["model_intent_accuracy_completed_runs"] == 1.0
    assert w["failures"][0]["error"].startswith("URLError")


# ---------- statistics and cross-model analysis ----------
def test_wilson_and_mcnemar_reference_values():
    assert bm.wilson_ci(0, 0) is None
    lo, hi = bm.wilson_ci(50, 100)
    assert lo == pytest.approx(0.4038, abs=1e-3) and hi == pytest.approx(0.5962, abs=1e-3)
    assert bm.mcnemar_exact(0, 0) is None
    assert bm.mcnemar_exact(5, 1) == pytest.approx(0.2188, abs=1e-3)  # 2 * 7/64
    assert bm.mcnemar_exact(10, 0) == pytest.approx(0.002, abs=1e-3)


def test_p99_requires_enough_samples():
    assert bm.p99(list(range(99))) is None
    assert bm.p99([float(x) for x in range(1, 101)]) == pytest.approx(99.01, abs=0.05)


def test_cross_model_disagreements_and_category_breakdown():
    decided = {("S1", "DUPLICATE_CHARGE_CLAIM", 0): ok("APPROVE_REFUND"), ("S1", "UNKNOWN", 0): ok("INVESTIGATE"),
               ("S7", "REFUND_REQUEST", 1500): ok("HUMAN_REVIEW")}
    cls = [row("a", 1, "B001", "DUPLICATE_CHARGE_CLAIM", "DUPLICATE_CHARGE_CLAIM"),
           row("a", 1, "B002", "REFUND_REQUEST", "REFUND_REQUEST", "adversarial"),
           row("b", 1, "B001", "UNKNOWN", "DUPLICATE_CHARGE_CLAIM"),
           row("b", 1, "B002", "REFUND_REQUEST", "REFUND_REQUEST", "adversarial")]
    rep = bm.summarize(cls, decided, DS, [], {})
    c = rep["comparison"]
    assert c["only_a_correct"] == ["B001"] and c["only_b_correct"] == []
    assert len(c["disagreements"]) == 1 and c["disagreements"][0]["b_decision"] == "INVESTIGATE"
    assert rep["providers"]["b"]["by_category"]["obvious"]["intent_accuracy"] == 0.0
    assert rep["providers"]["b"]["by_category"]["adversarial"]["decision_agreement"] == 1.0
    assert rep["providers"]["a"]["estimated_cost_per_evaluated_call_usd"] == pytest.approx(0.001)


def test_all_modes_get_identical_case_inputs_in_workflow_phase(tmp_path):
    seen = []

    class Recorder:
        def execute(self, name, version, inp, wait_seconds=30):
            seen.append((name, inp))
            return {"workflowId": f"w{len(seen)}", "status": "COMPLETED"}

        def wait_for(self, wid, timeout):
            return {"status": "COMPLETED", "startTime": 0, "endTime": 10, "tasks": [], "output": {}}

    bm.run_workflow_sample(["jev", "claude"], DS, tmp_path, api=Recorder())
    by_mode = defaultdict(list)
    for name, inp in seen:
        mode = inp.get("classifier", "rules")
        case = {k: v for k, v in inp["case"].items() if k not in ("claimType", "message")}
        by_mode[mode].append((case, inp["factsSource"], inp["fixtureId"], inp["dryRun"]))
    assert by_mode["rules"] == by_mode["jev"] == by_mode["claude"] and len(by_mode["rules"]) == len(DS)
    assert all(d is True for *_, d in by_mode["rules"])  # benchmark never executes refunds
