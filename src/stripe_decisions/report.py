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
    lines = [
        f"# Benchmark report {m['run_id']}",
        "",
        f"- Date (UTC): {m['date_utc']}",
        f"- Dataset: {m['dataset']} ({m['dataset_rows']} synthetic cases), repeats per provider: {m['repeats']}",
        f"- Policy: {m['policy_version']} (sha256 {m['rule_file_sha256'][:12]}), engine: {m['policy_engine']}",
        f"- Facts: {m['facts']}",
        f"- Adapter settings: {json.dumps(m['adapter_settings'])}",
        "- Costs are ESTIMATES: recorded token usage x list prices in benchmark/pricing.json "
        f"(retrieved {m['pricing']['retrieved_at']}).",
        "- Provider confidence/probability values are not used by the policy and are not compared across providers.",
        "",
        "| Metric | " + " | ".join(names) + " |",
        "|---|" + "---|" * len(names),
    ]
    rows = [
        ("Model version(s)", lambda p: ", ".join(p["model_versions"])),
        ("Calls", lambda p: p["calls"]),
        ("Intent accuracy", lambda p: _fmt(p["intent_accuracy"], True)),
        ("Consistency (all repeats agree)", lambda p: _fmt(p["consistency_all_repeats_agree"], True)),
        ("Schema-valid outputs", lambda p: _fmt(p["schema_valid_rate"], True)),
        ("Provider errors", lambda p: _fmt(p["error_rate"], True)),
        ("End-to-end decision correct", lambda p: _fmt(p["end_to_end_decision_correct_rate"], True)),
        ("Correctly automated", lambda p: _fmt(p["correctly_automated_rate"], True)),
        ("Escalated (human review / investigate)", lambda p: _fmt(p["escalation_rate"], True)),
        ("Policy violations (more permissive than gold)", lambda p: _fmt(p["policy_violation_rate"], True)),
        ("Model latency p50 / p95 (ms)", lambda p: f"{p['model_latency_ms']['p50']} / {p['model_latency_ms']['p95']}"),
        ("Input / output tokens", lambda p: f"{p['usage_totals'].get('input_tokens', 0)} / {p['usage_totals'].get('output_tokens', 0)}"),
        ("Est. cost total (USD)", lambda p: _fmt(p["estimated_cost_usd_total"])),
        ("Est. cost per correct automated decision (USD)", lambda p: _fmt(p["estimated_cost_per_correct_automated_decision_usd"])),
        ("Provider-native confidence returned", lambda p: "yes" if p["confidence_reported_by_provider"] else "no"),
    ]
    for label, fn in rows:
        lines.append(f"| {label} | " + " | ".join(str(fn(provs[n])) for n in names) + " |")
    lines += ["", "## Accuracy by difficulty", "", "| Difficulty | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    diffs = sorted({d for n in names for d in provs[n]["accuracy_by_difficulty"]})
    for d in diffs:
        lines.append(f"| {d} | " + " | ".join(_fmt(provs[n]["accuracy_by_difficulty"].get(d), True) for n in names) + " |")
    if rep.get("workflow_latency_ms"):
        lines += ["", "## Full workflow latency on Orkes (separate from model-only latency)", "",
                  "| Mode | Runs | Workflow p50 | Workflow p95 | Classify task p50 | Business Rule task p50 |",
                  "|---|---|---|---|---|---|"]
        for mode, w in rep["workflow_latency_ms"].items():
            lines.append(f"| {mode} | {w['runs']} | {w['workflow_p50']} | {w['workflow_p95']} | "
                         f"{w['classify_task_p50']} | {w['business_rule_task_p50']} |")
    for n in names:
        cm = provs[n]["confusion_matrix"]
        labels = list(cm)
        lines += ["", f"## Confusion matrix: {n} (rows = gold, columns = predicted)", "",
                  "| gold \\ pred | " + " | ".join(l[:12] for l in labels) + " |", "|---|" + "---|" * len(labels)]
        for g in labels:
            lines.append(f"| {g} | " + " | ".join(str(cm[g][p]) for p in labels) + " |")
    md = "\n".join(lines) + "\n"
    out_md = path.with_suffix(".md")
    out_md.write_text(md)
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
    metrics = [("intent_accuracy", "Intent accuracy"), ("end_to_end_decision_correct_rate", "Decision correct"),
               ("correctly_automated_rate", "Correctly automated"), ("escalation_rate", "Escalated"),
               ("policy_violation_rate", "Policy violations")]
    fig, ax = plt.subplots(figsize=(9, 4))
    width = 0.8 / len(names)
    for i, n in enumerate(names):
        ax.bar([j + i * width for j in range(len(metrics))], [provs[n][k] or 0 for k, _ in metrics], width, label=n)
    ax.set_xticks([j + width * (len(names) - 1) / 2 for j in range(len(metrics))], [l for _, l in metrics])
    ax.set_ylim(0, 1)
    ax.set_title(f"Same pinned Business Rule, different intent classifiers (n={rep['meta']['dataset_rows']} x {rep['meta']['repeats']})")
    ax.legend()
    fig.tight_layout()
    fig.savefig(f"{stem}-rates.png", dpi=150)
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar(names, [provs[n]["model_latency_ms"]["p50"] or 0 for n in names], label="p50")
    ax.bar(names, [(provs[n]["model_latency_ms"]["p95"] or 0) - (provs[n]["model_latency_ms"]["p50"] or 0) for n in names],
           bottom=[provs[n]["model_latency_ms"]["p50"] or 0 for n in names], alpha=0.5, label="p95 - p50")
    ax.set_ylabel("ms (model call only)")
    ax.set_title("Model-only classification latency")
    ax.legend()
    fig.tight_layout()
    fig.savefig(f"{stem}-latency.png", dpi=150)
    plt.close(fig)
