"""Machine-readable and human-readable exports of a benchmark sweep's
aggregated results. Every number here comes directly from
`helix.benchmarking.metrics.aggregate_runs` - nothing is computed or
adjusted in this module, so a report can never silently diverge from the
underlying persisted ExperimentRecords.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from helix.benchmarking.metrics import BenchmarkGroupSummary


def export_summary_json(summaries: list[BenchmarkGroupSummary], path: str | Path) -> None:
    path = Path(path)
    path.write_text(json.dumps([s.model_dump() for s in summaries], indent=2))


def export_summary_csv(summaries: list[BenchmarkGroupSummary], path: str | Path) -> None:
    path = Path(path)
    fieldnames = [
        "dataset_id", "pipeline_name", "n_runs", "success_rate",
        "precision_mean", "precision_std", "recall_mean", "recall_std",
        "f1_mean", "f1_std", "false_positive_rate_mean", "false_negative_rate_mean",
        "abundance_mae_mean", "runtime_seconds_mean", "iteration_count_mean",
        "total_tokens_mean", "estimated_cost_usd_mean",
    ]
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for s in summaries:
            scored = s.f1.n > 0  # see generate_markdown_report: n==0 means "never measured"
            writer.writerow({
                "dataset_id": s.dataset_id,
                "pipeline_name": s.pipeline_name,
                "n_runs": s.n_runs,
                "success_rate": round(s.success_rate, 4),
                "precision_mean": round(s.precision.mean, 4) if scored else "",
                "precision_std": round(s.precision.std, 4) if scored else "",
                "recall_mean": round(s.recall.mean, 4) if scored else "",
                "recall_std": round(s.recall.std, 4) if scored else "",
                "f1_mean": round(s.f1.mean, 4) if scored else "",
                "f1_std": round(s.f1.std, 4) if scored else "",
                "false_positive_rate_mean": round(s.false_positive_rate.mean, 4) if scored else "",
                "false_negative_rate_mean": round(s.false_negative_rate.mean, 4) if scored else "",
                "abundance_mae_mean": round(s.abundance_mae.mean, 4) if s.abundance_mae else "",
                "runtime_seconds_mean": round(s.runtime_seconds.mean, 4),
                "iteration_count_mean": round(s.iteration_count.mean, 4) if s.iteration_count else "",
                "total_tokens_mean": round(s.total_tokens.mean, 1) if s.total_tokens else "",
                "estimated_cost_usd_mean": round(s.estimated_cost_usd.mean, 6) if s.estimated_cost_usd else "",
            })


def generate_markdown_report(summaries: list[BenchmarkGroupSummary], path: str | Path) -> None:
    path = Path(path)
    lines = ["# HELIX Benchmark Report", ""]
    by_dataset: dict[str, list[BenchmarkGroupSummary]] = {}
    for s in summaries:
        by_dataset.setdefault(s.dataset_id, []).append(s)

    for dataset_id, group in by_dataset.items():
        lines.append(f"## Dataset: `{dataset_id}`")
        lines.append("")
        lines.append("| Pipeline | n | Success | Precision | Recall | F1 | FP rate | Abundance MAE | Runtime (s) | Iterations | Tokens | Cost (USD) |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for s in sorted(group, key=lambda s: s.pipeline_name):
            # n==0 means no run ever reached verification (e.g. every repeat was
            # rejected by review or failed execution first) - "never measured",
            # not a measured score of zero. Render those distinctly as n/a.
            scored = s.f1.n > 0
            precision = f"{s.precision.mean:.2f}±{s.precision.std:.2f}" if scored else "n/a"
            recall = f"{s.recall.mean:.2f}±{s.recall.std:.2f}" if scored else "n/a"
            f1 = f"{s.f1.mean:.2f}±{s.f1.std:.2f}" if scored else "n/a"
            fpr = f"{s.false_positive_rate.mean:.2f}" if scored else "n/a"
            abundance = f"{s.abundance_mae.mean:.4f}" if s.abundance_mae else "n/a"
            iterations = f"{s.iteration_count.mean:.2f}" if s.iteration_count else "n/a"
            tokens = f"{s.total_tokens.mean:.0f}" if s.total_tokens else "n/a"
            cost = f"${s.estimated_cost_usd.mean:.6f}" if s.estimated_cost_usd else "n/a"
            lines.append(
                f"| {s.pipeline_name} | {s.n_runs} | {s.success_rate:.0%} | "
                f"{precision} | {recall} | {f1} | {fpr} | {abundance} | "
                f"{s.runtime_seconds.mean:.3f}±{s.runtime_seconds.std:.3f} | {iterations} | {tokens} | {cost} |"
            )
        lines.append("")

    lines.append(
        "_Generated directly from `helix.benchmarking.metrics.aggregate_runs` over persisted "
        "`ExperimentRecord`s - no numbers in this report are hand-edited._"
    )
    path.write_text("\n".join(lines))
