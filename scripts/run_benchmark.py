"""End-to-end demonstration of the HELIX Release 0.5 controlled benchmark.

Runs every configuration (A-F: deterministic baseline, single-agent,
sequential multi-agent, multi-agent with verification, parallel
multi-agent, and three single-factor ablations) against two datasets of
different difficulty, with repeated runs, under one shared resource
budget - then aggregates, reports, and plots the results.

Uses the deterministic, free MockProvider by default (see
docs/agent_design.md). Swap in AnthropicProvider for a real model run
(requires `pip install -e ".[providers]"` and a funded ANTHROPIC_API_KEY).

Run with:
    python scripts/run_benchmark.py
"""

from __future__ import annotations

from pathlib import Path

from helix.agents.verifier import AcceptanceCriteria
from helix.benchmarking.ablations import ABLATION_CONFIGURATIONS
from helix.benchmarking.baselines import BASELINE_CONFIGURATIONS, BenchmarkBudget
from helix.benchmarking.experiments import run_benchmark_sweep
from helix.benchmarking.metrics import aggregate_runs, compare_configurations
from helix.config.models import BackgroundDiversity, DatasetGenerationConfig, ErrorModel, TargetSpec
from helix.experiments.store import ExperimentStore
from helix.providers.mock_provider import MockProvider
from helix.reporting.plots import save_all_plots
from helix.reporting.reports import export_summary_csv, export_summary_json, generate_markdown_report

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASETS_DIR = REPO_ROOT / "datasets" / "generated"
SANDBOX_ROOT = REPO_ROOT / "experiments" / "results" / "_sandbox"
DB_PATH = REPO_ROOT / "experiments" / "results" / "helix.db"
REPORT_DIR = REPO_ROOT / "experiments" / "results" / "benchmark_report"


def _easy_dataset() -> DatasetGenerationConfig:
    return DatasetGenerationConfig(
        dataset_id="benchmark_easy",
        seed=501,
        description="Moderate target abundance, low error rate - the 'should always work' case.",
        sequence_length=40,
        read_depth=1500,
        targets=[TargetSpec(target_id="target_a", abundance=0.1, present=True)],
        n_background_sequences=40,
        background_diversity=BackgroundDiversity.MEDIUM,
        error_model=ErrorModel(substitution_rate=0.005, insertion_rate=0.0, deletion_rate=0.0),
        duplicate_rate=0.05,
    )


def _hard_dataset() -> DatasetGenerationConfig:
    return DatasetGenerationConfig(
        dataset_id="benchmark_hard_indels",
        seed=502,
        description="Rare target, nonzero indel rate - requires indel-aware matching to get "
        "abundance right, and is where the Reviewer's missing-indel-handling catch matters.",
        sequence_length=40,
        read_depth=2000,
        targets=[TargetSpec(target_id="target_a", abundance=0.03, present=True)],
        n_background_sequences=80,
        background_diversity=BackgroundDiversity.HIGH,
        error_model=ErrorModel(substitution_rate=0.01, insertion_rate=0.01, deletion_rate=0.01),
        duplicate_rate=0.1,
    )


def main() -> None:
    datasets = [_easy_dataset(), _hard_dataset()]
    pipelines = BASELINE_CONFIGURATIONS + ABLATION_CONFIGURATIONS
    budget = BenchmarkBudget(max_iterations=3, timeout_seconds=30.0, max_branches=3)
    criteria = AcceptanceCriteria(min_precision=0.8, min_recall=0.8, max_false_positive_rate=0.2)
    provider = MockProvider()

    print(f"Running {len(pipelines)} configurations x {len(datasets)} datasets x 2 repeats "
          f"= {len(pipelines) * len(datasets) * 2} experiments...")

    with ExperimentStore(DB_PATH) as store:
        records = run_benchmark_sweep(
            datasets, pipelines, provider, DATASETS_DIR, SANDBOX_ROOT, store,
            budget=budget, criteria=criteria, n_repeats=2,
        )

    print(f"\n{len(records)} experiment records persisted to {DB_PATH}\n")

    summaries = aggregate_runs(records)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    export_summary_json(summaries, REPORT_DIR / "summary.json")
    export_summary_csv(summaries, REPORT_DIR / "summary.csv")
    generate_markdown_report(summaries, REPORT_DIR / "report.md")
    plot_paths = save_all_plots(summaries, REPORT_DIR / "plots")

    for s in sorted(summaries, key=lambda s: (s.dataset_id, s.pipeline_name)):
        f1_display = f"{s.f1.mean:.2f}+-{s.f1.std:.2f}" if s.f1.n > 0 else "n/a (never reached verification)"
        print(
            f"{s.dataset_id:>25s} | {s.pipeline_name:<32s} | n={s.n_runs} | "
            f"success={s.success_rate:.0%} | f1={f1_display} | "
            f"iters={s.iteration_count.mean if s.iteration_count else float('nan'):.1f} | "
            f"runtime={s.runtime_seconds.mean:.3f}s"
        )

    print(f"\nReport written to: {REPORT_DIR}")
    print(f"Plots: {list(plot_paths.keys())}")

    # Statistical comparison: does the full system beat the ablation that
    # removes the revision loop, on the hard dataset?
    hard_full = [r for r in records if r.dataset_id == "benchmark_hard_indels" and r.pipeline_name == "multi_agent_with_verification"]
    hard_no_loop = [r for r in records if r.dataset_id == "benchmark_hard_indels" and r.pipeline_name == "ablation_no_verification_loop"]
    if hard_full and hard_no_loop:
        comparison = compare_configurations(hard_full, hard_no_loop, metric="f1")
        print(f"\nFull system vs. no-verification-loop ablation on {comparison.dataset_id} (F1):")
        print(f"  {comparison.pipeline_a}: {comparison.mean_a:.3f} (n={comparison.n_a})")
        print(f"  {comparison.pipeline_b}: {comparison.mean_b:.3f} (n={comparison.n_b})")
        if comparison.p_value is not None:
            print(f"  t={comparison.t_statistic:.3f}, p={comparison.p_value:.4f}")
        else:
            print(f"  {comparison.note}")

    print(f"\nAll {len(records)} experiment records stored in {DB_PATH}")


if __name__ == "__main__":
    main()
