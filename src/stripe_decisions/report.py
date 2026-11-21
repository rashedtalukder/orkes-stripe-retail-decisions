"""Render a recorded benchmark report to markdown (+ PNG charts if matplotlib is installed).

Everything here is derived from the JSON written by `benchmark` (or `report --rebuild`); nothing is
computed from assumptions. Install chart support with `uv sync --group charts`.
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import REPO_ROOT

MODE_OF = {"anthropic": "claude", "typesafe": "jev"}  # provider name in phase A -> workflow mode in phase C
DISPLAY = {"anthropic": "Claude", "typesafe": "Jev", "claude": "Claude", "jev": "Jev", "rules": "Rules only"}


def _fmt(v, pct=False):
    if v is None:
        return "n/a"
    return f"{v * 100:.1f}%" if pct else f"{v}"


def _ci(ci):
    return "n/a" if not ci else f"[{ci[0] * 100:.1f}, {ci[1] * 100:.1f}]"


def _ms(v):
    return "n/a" if v is None else f"{v:.0f}"


def render(path: str | Path) -> str:
    path = Path(path)
    if not path.is_absolute():
        path = REPO_ROOT / path
    rep = json.loads(path.read_text())
    m, provs = rep["meta"], rep["providers"]
    names = sorted(provs)
    head = [DISPLAY.get(n, n) for n in names]
    pe = rep.get("policy_evaluations", {})
    ds = rep["dataset"]
    lines = [
        f"# Benchmark report {m['run_id']}",
        "",
        "Same pinned Orkes Business Rule, same frozen Stripe-derived facts, two intent classifiers.",
        "",
        "## Run",
        "",
        f"- Date (UTC): {m['date_utc']}; wall time {m.get('wall_seconds')} s" + (f"; report rebuilt {m['report_rebuilt_utc']}" if m.get("report_rebuilt_utc") else ""),
        f"- Dataset: {m['dataset']}: {ds['cases']} synthetic cases (single author's labels, pending independent review); "
        f"categories {json.dumps(ds['by_difficulty'])}; {m['repeats']} independent calls per case per provider",
        f"- Policy: {m['policy_version']} (sha256 {m['rule_file_sha256'][:12]}), evaluated by {m['policy_engine']}",
        f"- Policy evaluations: {pe.get('distinct_combinations')} distinct (facts, intent, amount) combinations, "
        f"{pe.get('failed')} failed; gold-decision failures: {len(pe.get('gold_decision_failures', []))}",
        f"- Facts: {m['facts']}",
        f"- Adapter settings: {json.dumps(m['adapter_settings'])}",
        f"- Raw rows: `{m.get('raw_dir')}` (published copy: `results/benchmark/raw/{m['run_id']}/`)",
        "",
        "**Observed vs estimated.** Accuracy, agreement, counts and latency are observed measurements. Costs are "
        "estimates: recorded token usage x list prices in `benchmark/pricing.json` "
        f"(retrieved {m['pricing']['retrieved_at']}), not invoices. Cost ceiling for the run: {m.get('max_cost_usd')} USD.",
        "",
        "**Confidence.** Only Jev returns a provider-native confidence. It is analyzed within Jev only. Claude returns "
        "none, and none was invented. Neither is used by the policy.",
        "",
        "**Intervals.** 95% Wilson intervals. Per-call intervals treat the 3 repeats of a case as independent, "
        "which they are not, so they are optimistic. The per-case (majority) interval is the conservative one.",
        "",
        "## Model-only classification and policy outcomes (phase A + B)",
        "",
        "| Metric | " + " | ".join(head) + " |",
        "|---|" + "---|" * len(names),
    ]
    rows = [
        ("Model version(s) returned by API", lambda p: ", ".join(p["model_versions"])),
        ("Calls completed / expected", lambda p: f"{p['calls']} / {p['expected_calls']}{'' if p['complete'] else ' INCOMPLETE'}"),
        ("Intent accuracy, all calls [95% CI]", lambda p: f"{_fmt(p['intent_accuracy'], True)} {_ci(p.get('intent_accuracy_ci95'))}"),
        ("Intent accuracy, per case majority [95% CI]", lambda p: f"{_fmt(p['intent_accuracy_majority_vote'], True)} {_ci(p.get('intent_accuracy_majority_ci95'))}"),
        ("Consistency: cases where all repeats agree", lambda p: _fmt(p["consistency_all_repeats_agree"], True)),
        ("Schema-invalid outputs (count)", lambda p: f"{p['invalid_outputs']} of {p['calls']}"),
        ("Provider errors (count)", lambda p: f"{p['errors']} of {p['calls']}"),
        ("Decision agreement with gold-label decision [95% CI]", lambda p: f"{_fmt(p['decision_agreement_with_gold_policy'], True)} {_ci(p.get('decision_agreement_ci95'))}"),
        ("Intent errors whose decision was unchanged", lambda p: f"{p['intent_errors_with_same_decision']} of {p['intent_errors']}"),
        ("Correctly automated (ceiling)", lambda p: f"{_fmt(p['correctly_automated_rate'], True)} ({_fmt(ds.get('gold_automated_rate'), True)})"),
        ("Escalated (human review / investigate)", lambda p: _fmt(p["escalation_rate"], True)),
        ("Policy violations: more permissive than gold", lambda p: f"{p['policy_violations']} of {p['calls']}"),
        ("Policy evaluation failures", lambda p: p["policy_evaluation_failures"]),
        ("Model-only latency p50 / p95 / p99 ms (n)", lambda p: f"{_ms(p['model_latency_ms']['p50'])} / {_ms(p['model_latency_ms']['p95'])} / "
                                                              f"{_ms(p['model_latency_ms'].get('p99'))} ({p['model_latency_ms']['n']})"),
        ("Input / output tokens (observed)", lambda p: f"{p['usage_totals'].get('input_tokens', 0)} / {p['usage_totals'].get('output_tokens', 0)}"),
        ("Est. model cost, all calls (USD)", lambda p: _fmt(p["estimated_cost_usd_total"])),
        ("Est. cost per evaluated call (USD)", lambda p: _fmt(p.get("estimated_cost_per_evaluated_call_usd"))),
        ("Est. cost per correct automated decision (USD)", lambda p: _fmt(p["estimated_cost_per_correct_automated_decision_usd"])),
        ("Provider-native confidence returned", lambda p: "yes" if p["confidence_reported_by_provider"] else "no"),
    ]
    for label, fn in rows:
        lines.append(f"| {label} | " + " | ".join(str(fn(provs[n])) for n in names) + " |")
    lines += [f"", f"p99 is reported only when n >= {provs[names[0]]['model_latency_ms'].get('p99_min_n', 100)}; latency excludes errored calls."]

    lines += ["", "## By scenario category (all calls)", "",
              "| Category | Provider | Cases | Calls | Intent accuracy [95% CI] | Decision agreement | Policy violations | Invalid | Errors |",
              "|---|---|---|---|---|---|---|---|---|"]
    for d in ["obvious", "ambiguous", "contradictory", "adversarial"]:
        for n in names:
            c = (provs[n].get("by_category") or {}).get(d)
            if c:
                lines.append(f"| {d} | {DISPLAY.get(n, n)} | {c['cases']} | {c['calls']} | {_fmt(c['intent_accuracy'], True)} "
                             f"{_ci(c['intent_accuracy_ci95'])} | {_fmt(c['decision_agreement'], True)} ({c['decision_agree']}/{c['calls']}) | "
                             f"{c['policy_violations']} | {c['invalid_outputs']} | {c['errors']} |")
    lines += ["", "Contradictory cases pair a claim with Stripe facts that contradict it (e.g. \"charged twice\" with one "
              "success and one decline). Adversarial cases try to steer the outcome (prompt injection, false authority). "
              "With 11 cases each, the intervals are wide."]

    cmp_ = rep.get("comparison")
    if cmp_:
        a, b = cmp_["providers"]
        lines += ["", "## Model disagreements (per case, majority intent)", "",
                  f"- Cases only {DISPLAY.get(a, a)} labeled correctly: {len(cmp_[f'only_{a}_correct'])} "
                  f"({', '.join(cmp_[f'only_{a}_correct']) or 'none'})",
                  f"- Cases only {DISPLAY.get(b, b)} labeled correctly: {len(cmp_[f'only_{b}_correct'])} "
                  f"({', '.join(cmp_[f'only_{b}_correct']) or 'none'})",
                  f"- Exact McNemar test (two-sided) on those discordant cases: p = {cmp_['mcnemar_exact_p_two_sided']}. "
                  + ("Not statistically significant at the 0.05 level on this dataset."
                     if (cmp_['mcnemar_exact_p_two_sided'] or 1) >= 0.05 else
                     "Below 0.05 on this dataset; this is a single synthetic dataset with one labeler."),
                  "",
                  f"| Case | Category | Message | Gold intent -> decision | {DISPLAY.get(a, a)} -> decision | {DISPLAY.get(b, b)} -> decision |",
                  "|---|---|---|---|---|---|"]
        for x in cmp_["disagreements"]:
            msg = x["message"].replace("|", "\\|")
            lines.append(f"| {x['id']} | {x['difficulty']} | {msg} | {x['gold_intent']} -> {x['gold_decision']} | "
                         f"{x[f'{a}_intent']} -> {x[f'{a}_decision']} | {x[f'{b}_intent']} -> {x[f'{b}_decision']} |")

    for n in names:
        conf = provs[n].get("accuracy_by_provider_confidence")
        if conf:
            lines += ["", f"## {DISPLAY.get(n, n)}: accuracy by its own reported confidence (within-provider only)", "",
                      "| Confidence bucket | Calls | Intent accuracy |", "|---|---|---|"]
            for bkt, v in conf.items():
                lines.append(f"| {bkt} | {v['n']} | {_fmt(v['accuracy'], True)} |")

    if rep.get("workflow"):
        wf = rep["workflow"]
        any_mode = next(iter(wf.values()))
        lines += ["", f"## Full workflows on Orkes (phase C: stratified sample of {any_mode['runs']} cases per mode)", "",
                  "Separate from model-only latency. Workflow time is Conductor start-to-end, including worker polling, "
                  "the Stripe-facts task (fixture mode here) and the Business Rule task. Rates use all sampled runs as the "
                  "denominator (a run that failed or never started counts as wrong); completed-only rates are in parentheses.",
                  "",
                  "| Mode | Runs | Completed | Not started (client error) | Model intent accuracy | Workflow decision = gold decision | "
                  "Workflow p50 / p95 ms | Classify task p50 | In-task model call p50 | Business Rule task p50 | Est. model cost (USD) |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
        for mode, w in wf.items():
            lines.append(f"| {DISPLAY.get(mode, mode)} | {w['runs']} | {w['completed']} | {w.get('not_started_client_error', 'n/a')} | "
                         f"{_fmt(w['model_intent_accuracy'], True)} ({_fmt(w.get('model_intent_accuracy_completed_runs'), True)}) | "
                         f"{_fmt(w['workflow_decision_agreement_with_gold_policy'], True)} ({_fmt(w.get('workflow_decision_agreement_completed_runs'), True)}) | "
                         f"{_ms(w['workflow_ms']['p50'])} / {_ms(w['workflow_ms']['p95'])} | {_ms(w['classify_task_ms_p50'])} | "
                         f"{_ms(w.get('in_workflow_model_latency_ms_p50'))} | {_ms(w['business_rule_task_ms_p50'])} | {w['estimated_model_cost_usd']} |")
        lines += ["", f"p99 is not reported for workflows: n = {any_mode['runs']} per mode is below {any_mode['workflow_ms'].get('p99_min_n', 100)}."]
        fails = [(mode, f) for mode, w in wf.items() for f in w.get("failures", [])]
        lines += ["", "Workflow runs that did not complete: " + ("none." if not fails else "")]
        lines += [f"- {mode} {f['id']}: status {f['status']}, workflowId {f['workflowId']}, error: {f['error']}" for mode, f in fails]

    for n in names:
        mis = provs[n].get("misclassified_cases")
        if mis is not None:
            lines += ["", f"## Misclassified cases: {DISPLAY.get(n, n)} ({len(mis)} distinct)", "",
                      "| Case | Category | Gold intent | Predicted | Repeats | Gold-label decision | Decision with prediction |",
                      "|---|---|---|---|---|---|---|"]
            for x in mis:
                lines.append(f"| {x['id']} | {x['difficulty']} | {x['gold_intent']} | {x['predicted_intent']} | {x['repeats']} | "
                             f"{x['gold_decision']} | {x['model_decision']} |")
    for n in names:
        cm = provs[n]["confusion_matrix"]
        labels = list(cm)
        lines += ["", f"## Confusion matrix: {DISPLAY.get(n, n)} (rows = gold, columns = predicted; all calls)", "",
                  "| gold \\ pred | " + " | ".join(lbl[:12] for lbl in labels) + " |", "|---|" + "---|" * len(labels)]
        for g in labels:
            lines.append(f"| {g} | " + " | ".join(str(cm[g][p]) for p in labels) + " |")
    lines += ["", "## Limitations", "",
              "- Synthetic messages, labeled by one author. Labels were not changed after seeing results.",
              "- Facts are frozen fixtures from 8 sandbox scenarios. This measures interpretation under fixed facts.",
              "- One configuration per provider; other settings (effort, prompts, model versions) will differ.",
              "- Latency depends on network location, provider load and time of day. Phase A uses 4 concurrent requests.",
              "- The workflow sample is small, so its percentiles are less stable than model-only ones.",
              "- Differences between providers are reported as observed. Only the McNemar test above addresses significance."]
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
    label = {n: f"{DISPLAY.get(n, n)} ({', '.join(provs[n]['model_versions'])})" for n in names}
    width = 0.8 / len(names)

    metrics = [("intent_accuracy", "Intent accuracy\n(model)"),
               ("decision_agreement_with_gold_policy", "Decision agrees\nwith gold policy"),
               ("correctly_automated_rate", "Correctly\nautomated"), ("escalation_rate", "Escalated"),
               ("policy_violation_rate", "Policy\nviolations")]
    fig, ax = plt.subplots(figsize=(10, 4.5))
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
    ax.set_ylabel(f"share of {provs[names[0]]['calls']} calls per model")
    ax.set_title(f"Same pinned Business Rule ({meta['policy_version']}), two intent classifiers\n"
                 f"n = {meta['dataset_rows']} cases x {meta['repeats']} repeats = "
                 f"{provs[names[0]]['calls']} calls per model, run {meta['run_id']}", fontsize=10)
    ax.legend()
    fig.tight_layout()
    fig.savefig(f"{stem}-rates.png", dpi=150)
    plt.close(fig)

    cats = ["obvious", "ambiguous", "contradictory", "adversarial"]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.2), sharey=True)
    for ax, (key, title) in zip(axes, [("intent_accuracy", "Intent accuracy (model)"),
                                       ("decision_agreement", "Decision agreement with gold-label decision")]):
        for i, n in enumerate(names):
            vals = [((provs[n].get("by_category") or {}).get(c) or {}).get(key) or 0 for c in cats]
            bars = ax.bar([j + i * width for j in range(len(cats))], vals, width, label=label[n])
            ax.bar_label(bars, labels=[f"{v * 100:.0f}%" for v in vals], fontsize=8)
        counts = rep["dataset"]["by_difficulty"]
        ax.set_xticks([j + width * (len(names) - 1) / 2 for j in range(len(cats))], [f"{c}\n(n={counts.get(c)} cases)" for c in cats])
        ax.set_ylim(0, 1.12)
        ax.set_title(title, fontsize=10)
    handles, labels_ = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels_, loc="lower center", ncol=len(names), fontsize=8, frameon=False)
    fig.suptitle(f"By scenario category, all repeats (run {meta['run_id']})", fontsize=10)
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    fig.savefig(f"{stem}-categories.png", dpi=150)
    plt.close(fig)

    # Model-only vs end-to-end latency (p50 bars, p95 whiskers), clearly separated.
    wf = rep.get("workflow") or {}
    groups = []
    for n in names:
        lat = provs[n]["model_latency_ms"]
        groups.append((f"{DISPLAY.get(n, n)}\nmodel call only\n(n={lat['n']})", lat["p50"], lat["p95"], "tab:blue"))
    for mode in ["rules", *[MODE_OF.get(n, n) for n in names]]:
        w = wf.get(mode)
        if w:
            groups.append((f"{DISPLAY.get(mode, mode)}\nfull workflow\n(n={w['workflow_ms']['n']})",
                           w["workflow_ms"]["p50"], w["workflow_ms"]["p95"], "tab:orange"))
    fig, ax = plt.subplots(figsize=(10, 4.5))
    xs = range(len(groups))
    p50s = [g[1] or 0 for g in groups]
    p95s = [g[2] or 0 for g in groups]
    ax.bar(xs, p50s, color=[g[3] for g in groups])
    ax.errorbar(xs, p50s, yerr=[[0] * len(groups), [b - a for a, b in zip(p50s, p95s)]], fmt="none", ecolor="black", capsize=4)
    top = max(p95s) if p95s else 1
    for x, a, b in zip(xs, p50s, p95s):  # label above the whisker so text never overlaps it
        ax.text(x, b + top * 0.03, f"p50 {a:.0f}\np95 {b:.0f}", ha="center", va="bottom", fontsize=7)
    ax.set_xticks(list(xs), [g[0] for g in groups], fontsize=8)
    ax.set_ylabel("milliseconds")
    ax.set_ylim(0, max(p95s) * 1.35 if p95s else 1)
    ax.set_title("Model-only latency (blue) vs end-to-end Orkes workflow latency (orange); bar = p50, whisker = p95\n"
                 f"Different measurements: do not subtract one from the other. Run {meta['run_id']}", fontsize=9)
    fig.tight_layout()
    fig.savefig(f"{stem}-latency.png", dpi=150)
    plt.close(fig)
