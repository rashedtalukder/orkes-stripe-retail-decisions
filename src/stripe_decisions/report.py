"""Render a recorded benchmark report to markdown (+ PNG charts if matplotlib is installed).

Everything here is derived from the JSON written by `benchmark`; nothing is computed from
assumptions. Install charts support with `uv sync --group charts`.
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import REPO_ROOT


def _fmt(v, pct=False):
    if v is None:
        return "n/a"
    return f"{v * 100:.1f}%" if pct else f"{v}"


def render(path: str | Path) -> str:
    path = Path(path)
    if not path.is_absolute():
        path = REPO_ROOT / path
    rep = json.loads(path.read_text())
    m, provs = rep["meta"], rep["providers"]
    names = sorted(provs)
    pe = rep.get("policy_evaluations", {})
    lines = [
        f"# Benchmark report {m['run_id']}",
        "",
        f"- Date (UTC): {m['date_utc']}; wall time {m.get('wall_seconds')} s",
        f"- Dataset: {m['dataset']} ({m['dataset_rows']} synthetic cases, author-labeled), repeats per provider: {m['repeats']}",
        f"- Policy: {m['policy_version']} (sha256 {m['rule_file_sha256'][:12]}), engine: {m['policy_engine']}",
        f"- Policy evaluations: {pe.get('distinct_combinations')} distinct (facts, intent, amount) combinations, "
        f"{pe.get('failed')} failed; gold-decision failures: {len(pe.get('gold_decision_failures', []))}",
        f"- Facts: {m['facts']}",
        f"- Adapter settings: {json.dumps(m['adapter_settings'])}",
        "- Costs are ESTIMATES: recorded token usage x list prices in benchmark/pricing.json "
        f"(retrieved {m['pricing']['retrieved_at']}); cost ceiling {m.get('max_cost_usd')} USD.",
        "- Provider confidence/probability values are not used by the policy and are not compared across providers.",
        f"- Raw rows: {m.get('raw_dir')}",
        "",
        "## Model-only classification and policy outcomes",
        "",
        "| Metric | " + " | ".join(names) + " |",
        "|---|" + "---|" * len(names),
    ]
    rows = [
        ("Model version(s)", lambda p: ", ".join(p["model_versions"])),
        ("Calls (expected)", lambda p: f"{p['calls']} ({p['expected_calls']}){'' if p['complete'] else ' INCOMPLETE'}"),
        ("Intent accuracy (all calls)", lambda p: _fmt(p["intent_accuracy"], True)),
        ("Intent accuracy (majority of repeats)", lambda p: _fmt(p["intent_accuracy_majority_vote"], True)),
        ("Consistency (all repeats agree)", lambda p: _fmt(p["consistency_all_repeats_agree"], True)),
        ("Schema-valid outputs", lambda p: f"{_fmt(p['schema_valid_rate'], True)} ({p['invalid_outputs']} invalid)"),
        ("Provider errors", lambda p: f"{_fmt(p['error_rate'], True)} ({p['errors']})"),
        ("Decision agreement with gold-label policy decision", lambda p: _fmt(p["decision_agreement_with_gold_policy"], True)),
        ("Intent errors whose policy decision was unchanged", lambda p: f"{p['intent_errors_with_same_decision']} of {p['intent_errors']}"),
        ("Correctly automated", lambda p: f"{_fmt(p['correctly_automated_rate'], True)} (ceiling {_fmt(rep['dataset'].get('gold_automated_rate'), True)})"),
        ("Escalated (human review / investigate)", lambda p: _fmt(p["escalation_rate"], True)),
        ("Policy violations (more permissive than gold)", lambda p: f"{_fmt(p['policy_violation_rate'], True)} ({p['policy_violations']})"),
        ("Policy evaluation failures", lambda p: p["policy_evaluation_failures"]),
        ("Model latency p50 / p95 (ms)", lambda p: f"{p['model_latency_ms']['p50']} / {p['model_latency_ms']['p95']}"),
        ("Input / output tokens", lambda p: f"{p['usage_totals'].get('input_tokens', 0)} / {p['usage_totals'].get('output_tokens', 0)}"),
        ("Est. model cost total (USD)", lambda p: _fmt(p["estimated_cost_usd_total"])),
        ("Est. cost per correct automated decision (USD)", lambda p: _fmt(p["estimated_cost_per_correct_automated_decision_usd"])),
        ("Provider-native confidence returned", lambda p: "yes" if p["confidence_reported_by_provider"] else "no"),
    ]
    for label, fn in rows:
        lines.append(f"| {label} | " + " | ".join(str(fn(provs[n])) for n in names) + " |")
    lines += ["", "## Intent accuracy by difficulty", "", "| Difficulty | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    diffs = sorted({d for n in names for d in provs[n]["accuracy_by_difficulty"]})
    for d in diffs:
        lines.append(f"| {d} | " + " | ".join(_fmt(provs[n]["accuracy_by_difficulty"].get(d), True) for n in names) + " |")
    for n in names:
        conf = provs[n].get("accuracy_by_provider_confidence")
        if conf:
            lines += ["", f"## {n}: accuracy by its own reported confidence (within-provider only)", "",
                      "| Confidence bucket | Calls | Intent accuracy |", "|---|---|---|"]
            for b, v in conf.items():
                lines.append(f"| {b} | {v['n']} | {_fmt(v['accuracy'], True)} |")
    if rep.get("workflow"):
        lines += ["", "## Full workflows on Orkes (stratified sample; separate from model-only latency)", "",
                  "Rates use all sampled runs as the denominator (a run that failed or never started counts as wrong); "
                  "the completed-only rate is shown in parentheses.", "",
                  "| Mode | Runs | Completed | Not started (client error) | Model intent accuracy | Workflow decision = gold-policy decision | "
                  "Workflow p50 / p95 ms | Classify task p50 | Facts task p50 | Business Rule task p50 | Est. model cost |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
        for mode, w in rep["workflow"].items():
            lines.append(f"| {mode} | {w['runs']} | {w['completed']} | {w.get('not_started_client_error', 'n/a')} | "
                         f"{_fmt(w['model_intent_accuracy'], True)} ({_fmt(w.get('model_intent_accuracy_completed_runs'), True)}) | "
                         f"{_fmt(w['workflow_decision_agreement_with_gold_policy'], True)} ({_fmt(w.get('workflow_decision_agreement_completed_runs'), True)}) | "
                         f"{w['workflow_ms']['p50']} / {w['workflow_ms']['p95']} | {w['classify_task_ms_p50']} | "
                         f"{w['facts_task_ms_p50']} | {w['business_rule_task_ms_p50']} | {w['estimated_model_cost_usd']} |")
        fails = [(mode, f) for mode, w in rep["workflow"].items() for f in w.get("failures", [])]
        if fails:
            lines += ["", "Workflow runs that did not complete:", ""]
            lines += [f"- {mode} {f['id']}: status {f['status']}, workflowId {f['workflowId']}, error: {f['error']}" for mode, f in fails]
    for n in names:
        mis = provs[n].get("misclassified_cases")
        if mis is not None:
            lines += ["", f"## Misclassified cases: {n} ({len(mis)} distinct)", "",
                      "| Case | Difficulty | Gold intent | Predicted | Repeats | Gold-label decision | Decision with prediction |",
                      "|---|---|---|---|---|---|---|"]
            for x in mis:
                lines.append(f"| {x['id']} | {x['difficulty']} | {x['gold_intent']} | {x['predicted_intent']} | {x['repeats']} | "
                             f"{x['gold_decision']} | {x['model_decision']} |")
    for n in names:
        cm = provs[n]["confusion_matrix"]
        labels = list(cm)
        lines += ["", f"## Confusion matrix: {n} (rows = gold, columns = predicted; all repeats)", "",
                  "| gold \\ pred | " + " | ".join(lbl[:12] for lbl in labels) + " |", "|---|" + "---|" * len(labels)]
        for g in labels:
            lines.append(f"| {g} | " + " | ".join(str(cm[g][p]) for p in labels) + " |")
    md = "\n".join(lines) + "\n"
    path.with_suffix(".md").write_text(md)
    _charts(rep, path.with_suffix(""))
    return md


def _charts(rep: dict, stem: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    provs = rep["providers"]
    names = sorted(provs)
    meta = rep["meta"]
    label = {n: f"{n} ({', '.join(provs[n]['model_versions'])})" for n in names}
    metrics = [("intent_accuracy", "Intent accuracy\n(model)"),
               ("decision_agreement_with_gold_policy", "Decision agrees\nwith gold policy"),
               ("correctly_automated_rate", "Correctly\nautomated"), ("escalation_rate", "Escalated"),
               ("policy_violation_rate", "Policy\nviolations")]
    fig, ax = plt.subplots(figsize=(10, 4.5))
    width = 0.8 / len(names)
    for i, n in enumerate(names):
        vals = [provs[n][k] or 0 for k, _ in metrics]
        bars = ax.bar([j + i * width for j in range(len(metrics))], vals, width, label=label[n])
        ax.bar_label(bars, labels=[f"{v * 100:.1f}%" for v in vals], fontsize=8)
    ax.set_xticks([j + width * (len(names) - 1) / 2 for j in range(len(metrics))], [lbl for _, lbl in metrics])
    ceiling = rep["dataset"].get("gold_automated_rate")
    if ceiling:
        ax.hlines(ceiling, 2 - width / 2, 2 + width * (len(names) - 0.5), colors="gray", linestyles="dashed")
        ax.text(2 + width * (len(names) - 1) / 2, ceiling + 0.07, f"max possible {ceiling * 100:.1f}%",
                ha="center", fontsize=8, color="dimgray")
    ax.set_ylim(0, 1.08)
    ax.set_title(f"Same pinned Business Rule ({meta['policy_version']}), two intent classifiers\n"
                 f"n = {meta['dataset_rows']} cases x {meta['repeats']} repeats, run {meta['run_id']}", fontsize=10)
    ax.legend()
    fig.tight_layout()
    fig.savefig(f"{stem}-rates.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6, 4))
    p50 = [provs[n]["model_latency_ms"]["p50"] or 0 for n in names]
    p95 = [provs[n]["model_latency_ms"]["p95"] or 0 for n in names]
    ax.bar([label[n] for n in names], p50, label="p50")
    ax.bar([label[n] for n in names], [b - a for a, b in zip(p50, p95)], bottom=p50, alpha=0.5, label="p95")
    for i, (a, b) in enumerate(zip(p50, p95)):
        ax.text(i, b, f"p50 {a:.0f} / p95 {b:.0f} ms", ha="center", va="bottom", fontsize=8)
    ax.set_ylabel("ms (adapter call only)")
    ax.set_title(f"Model-only classification latency (run {meta['run_id']})", fontsize=10)
    ax.legend()
    fig.tight_layout()
    fig.savefig(f"{stem}-latency.png", dpi=150)
    plt.close(fig)

    diffs = sorted({d for n in names for d in provs[n]["accuracy_by_difficulty"]})
    fig, ax = plt.subplots(figsize=(8, 4))
    for i, n in enumerate(names):
        vals = [provs[n]["accuracy_by_difficulty"].get(d) or 0 for d in diffs]
        bars = ax.bar([j + i * width for j in range(len(diffs))], vals, width, label=label[n])
        ax.bar_label(bars, labels=[f"{v * 100:.0f}%" for v in vals], fontsize=8)
    counts = rep["dataset"]["by_difficulty"]
    ax.set_xticks([j + width * (len(names) - 1) / 2 for j in range(len(diffs))], [f"{d}\n(n={counts.get(d)})" for d in diffs])
    ax.set_ylim(0, 1.1)
    ax.set_title("Intent accuracy by case difficulty (all repeats)", fontsize=10)
    ax.legend()
    fig.tight_layout()
    fig.savefig(f"{stem}-difficulty.png", dpi=150)
    plt.close(fig)

    if rep.get("workflow"):
        modes = list(rep["workflow"])
        fig, ax = plt.subplots(figsize=(7, 4))
        p50 = [rep["workflow"][m]["workflow_ms"]["p50"] or 0 for m in modes]
        p95 = [rep["workflow"][m]["workflow_ms"]["p95"] or 0 for m in modes]
        ax.bar(modes, p50, label="p50")
        ax.bar(modes, [b - a for a, b in zip(p50, p95)], bottom=p50, alpha=0.5, label="p95")
        ax.set_ylabel("ms (Conductor start to end)")
        ax.set_title(f"End-to-end workflow latency, n = {rep['workflow'][modes[0]]['runs']} per mode", fontsize=10)
        ax.legend()
        fig.tight_layout()
        fig.savefig(f"{stem}-workflow-latency.png", dpi=150)
        plt.close(fig)
