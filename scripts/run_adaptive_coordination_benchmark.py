"""Phase 5 worked example: Experiments A-E (the extension plan's
single-vs-dual-agent, verification on/off, and adaptive-coordination
grid) across the four difficulty tiers the adaptive policy itself
recognizes (helix.benchmarking.difficulty_scenarios), with repeated runs.

This is the "does the adaptive policy actually behave differently across
tiers, and is it worth what it costs" check the extension plan's Decision
3 calls for: Experiment E's choices (1 vs. 2 agents) are reported
alongside B (single + verification, always 1) and D (dual + verification,
always 2) - fixed baselines it is *compared against*, never assumed
better than.

Uses the deterministic, free MockProvider by default (see
docs/agent_design.md's MockProvider-convergence caveat - two independent
coding agents on the same MockProvider instance will typically converge,
so don't read a "tie" here as evidence dual-agent approaches are
equivalent in general).

Run with:
    python scripts/run_adaptive_coordination_benchmark.py
"""

from __future__ import annotations

from pathlib import Path

from helix.agents.verifier import AcceptanceCriteria
from helix.benchmarking.baselines import EXPERIMENT_A_TO_E, BenchmarkBudget
from helix.benchmarking.difficulty_scenarios import DIFFICULTY_SCENARIOS
from helix.benchmarking.experiments import run_benchmark_sweep
from helix.benchmarking.metrics import aggregate_runs, compare_configurations
from helix.experiments.store import ExperimentStore
from helix.providers.mock_provider import MockProvider
from helix.reporting.reports import export_summary_csv, export_summary_json, generate_markdown_report

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASETS_DIR = REPO_ROOT / "datasets" / "generated"
SANDBOX_ROOT = REPO_ROOT / "experiments" / "results" / "_sandbox"
DB_PATH = REPO_ROOT / "experiments" / "results" / "helix.db"
REPORT_DIR = REPO_ROOT / "experiments" / "results" / "adaptive_coordination_report"

N_REPEATS = 2


def main() -> None:
    datasets = [
        builder(f"adaptive_bench_{tier}", seed=600 + i)
        for i, (tier, builder) in enumerate(DIFFICULTY_SCENARIOS.items())
    ]
    budget = BenchmarkBudget(max_iterations=4, timeout_seconds=30.0)
    criteria = AcceptanceCriteria(min_precision=0.8, min_recall=0.8, max_false_positive_rate=0.2)
    provider = MockProvider()

    print(
        f"Running {len(EXPERIMENT_A_TO_E)} configurations (Experiments A-E) x {len(datasets)} "
        f"difficulty tiers x {N_REPEATS} repeats = {len(EXPERIMENT_A_TO_E) * len(datasets) * N_REPEATS} experiments..."
    )

    with ExperimentStore(DB_PATH) as store:
        records = run_benchmark_sweep(
            datasets, EXPERIMENT_A_TO_E, provider, DATASETS_DIR, SANDBOX_ROOT, store,
            budget=budget, criteria=criteria, n_repeats=N_REPEATS,
        )

    print(f"\n{len(records)} experiment records persisted to {DB_PATH}\n")

    summaries = aggregate_runs(records)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    export_summary_json(summaries, REPORT_DIR / "summary.json")
    export_summary_csv(summaries, REPORT_DIR / "summary.csv")
    generate_markdown_report(summaries, REPORT_DIR / "report.md")

    for s in sorted(summaries, key=lambda s: (s.dataset_id, s.pipeline_name)):
        f1_display = f"{s.f1.mean:.2f}+-{s.f1.std:.2f}" if s.f1.n > 0 else "n/a"
        cost_display = f"${s.estimated_cost_usd.mean:.4f}" if s.estimated_cost_usd and s.estimated_cost_usd.n > 0 else "n/a"
        print(
            f"{s.dataset_id:>25s} | {s.pipeline_name:<36s} | n={s.n_runs} | "
            f"success={s.success_rate:.0%} | f1={f1_display} | cost={cost_display}"
        )

    # What did the adaptive policy actually choose, per tier?
    adaptive_records = [r for r in records if r.pipeline_name == "experiment_e_adaptive"]
    print("\nExperiment E's actual per-tier agent-count choice:")
    for r in sorted(adaptive_records, key=lambda r: r.dataset_id):
        decision = r.coordination_decision or {}
        print(
            f"  {r.dataset_id:>25s} | tier={decision.get('difficulty_tier', '?'):<10s} | "
            f"initial={decision.get('initial_agent_count', '?')} -> final={decision.get('final_agent_count', '?')} | "
            f"escalations={len(decision.get('escalations', []))}"
        )

    # The actual Decision-3 comparison: E vs. the fixed B/D baselines it's
    # weighed against, on F1 - never assume E "wins" just because it's new.
    for dataset in datasets:
        e_runs = [r for r in records if r.dataset_id == dataset.dataset_id and r.pipeline_name == "experiment_e_adaptive"]
        b_runs = [r for r in records if r.dataset_id == dataset.dataset_id and r.pipeline_name == "experiment_b_single_with_verification"]
        d_runs = [r for r in records if r.dataset_id == dataset.dataset_id and r.pipeline_name == "experiment_d_dual_with_verification"]
        print(f"\n{dataset.dataset_id} - Experiment E vs. fixed baselines (F1):")
        for label, other_runs in (("B (always 1 agent)", b_runs), ("D (always 2 agents)", d_runs)):
            if not (e_runs and other_runs):
                continue
            comparison = compare_configurations(e_runs, other_runs, metric="f1")
            print(f"  E vs {label}: E={comparison.mean_a:.3f} (n={comparison.n_a}) vs {comparison.mean_b:.3f} (n={comparison.n_b})")
            print(f"    {comparison.note}" if comparison.p_value is None else f"    t={comparison.t_statistic:.3f}, p={comparison.p_value:.4f}")

    print(f"\nReport written to: {REPORT_DIR}")
    print(f"All {len(records)} experiment records stored in {DB_PATH}")


if __name__ == "__main__":
    main()
