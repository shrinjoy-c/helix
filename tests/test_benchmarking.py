"""Tests for the controlled benchmarking framework: metric aggregation,
statistical comparison, budget fairness across configurations,
reproducibility, and report/plot generation.
"""

from __future__ import annotations

import json

import pytest

from helix.benchmarking.ablations import ABLATION_CONFIGURATIONS
from helix.benchmarking.baselines import (
    ADAPTIVE_COORDINATION,
    BASELINE_CONFIGURATIONS,
    DETERMINISTIC_BASELINE,
    DUAL_AGENT_NO_VERIFICATION,
    DUAL_AGENT_WITH_VERIFICATION,
    SINGLE_AGENT,
    BenchmarkBudget,
)
from helix.benchmarking.experiments import run_benchmark_sweep
from helix.benchmarking.metrics import aggregate_runs, compare_configurations
from helix.config.models import (
    AnalysisResult,
    DatasetGenerationConfig,
    ErrorModel,
    EvaluationMetrics,
    ExperimentRecord,
    TargetSpec,
)
from helix.experiments.store import ExperimentStore
from helix.providers.mock_provider import MockProvider
from helix.reporting.plots import save_all_plots
from helix.reporting.reports import export_summary_csv, export_summary_json, generate_markdown_report


def _record(dataset_id: str, pipeline_name: str, precision: float, recall: float, f1: float, runtime: float) -> ExperimentRecord:
    dataset_config = DatasetGenerationConfig(
        dataset_id=dataset_id, seed=1, targets=[TargetSpec(target_id="t1", abundance=0.1)]
    )
    metrics = EvaluationMetrics(
        dataset_id=dataset_id, pipeline_name=pipeline_name,
        true_positives=1, false_positives=0, false_negatives=0, true_negatives=0,
        precision=precision, recall=recall, f1=f1,
        false_positive_rate=0.0, false_negative_rate=0.0, abundance_mae=0.01,
    )
    return ExperimentRecord(
        experiment_id=f"exp_{pipeline_name}_{precision}_{recall}",
        dataset_id=dataset_id, pipeline_name=pipeline_name, status="completed",
        dataset_config=dataset_config, evaluation_metrics=metrics,
        runtime_seconds=runtime, created_at="2026-11-21T00:00:00Z", iteration_count=1,
    )


# --- aggregation -----------------------------------------------------------


def test_aggregate_runs_computes_mean_and_std():
    records = [
        _record("d1", "p1", precision=1.0, recall=1.0, f1=1.0, runtime=1.0),
        _record("d1", "p1", precision=0.8, recall=0.8, f1=0.8, runtime=2.0),
    ]
    summaries = aggregate_runs(records)
    assert len(summaries) == 1
    s = summaries[0]
    assert s.n_runs == 2
    assert s.f1.mean == pytest.approx(0.9)
    assert s.f1.std > 0
    assert s.runtime_seconds.mean == pytest.approx(1.5)


def test_aggregate_runs_single_run_has_zero_std():
    records = [_record("d1", "p1", precision=1.0, recall=1.0, f1=1.0, runtime=1.0)]
    summaries = aggregate_runs(records)
    assert summaries[0].n_runs == 1
    assert summaries[0].f1.std == 0.0


def test_aggregate_runs_groups_by_dataset_and_pipeline():
    records = [
        _record("d1", "p1", 1.0, 1.0, 1.0, 1.0),
        _record("d1", "p2", 0.5, 0.5, 0.5, 1.0),
        _record("d2", "p1", 1.0, 1.0, 1.0, 1.0),
    ]
    summaries = aggregate_runs(records)
    keys = {(s.dataset_id, s.pipeline_name) for s in summaries}
    assert keys == {("d1", "p1"), ("d1", "p2"), ("d2", "p1")}


def test_aggregate_runs_success_rate_reflects_failed_status():
    records = [
        _record("d1", "p1", 1.0, 1.0, 1.0, 1.0),
        _record("d1", "p1", 1.0, 1.0, 1.0, 1.0).model_copy(update={"status": "failed", "evaluation_metrics": None}),
    ]
    summaries = aggregate_runs(records)
    assert summaries[0].success_rate == pytest.approx(0.5)


def test_aggregate_runs_abundance_mae_none_without_scored_runs():
    failed = _record("d1", "p1", 1.0, 1.0, 1.0, 1.0).model_copy(
        update={"status": "failed", "evaluation_metrics": None}
    )
    summaries = aggregate_runs([failed])
    assert summaries[0].abundance_mae is None


# --- statistical comparison -------------------------------------------------


def test_compare_configurations_requires_shared_dataset():
    a = [_record("d1", "p1", 1.0, 1.0, 1.0, 1.0), _record("d1", "p1", 0.9, 0.9, 0.9, 1.0)]
    b = [_record("d2", "p2", 0.5, 0.5, 0.5, 1.0), _record("d2", "p2", 0.4, 0.4, 0.4, 1.0)]
    with pytest.raises(ValueError):
        compare_configurations(a, b)


def test_compare_configurations_needs_at_least_two_runs_per_side():
    a = [_record("d1", "p1", 1.0, 1.0, 1.0, 1.0)]
    b = [_record("d1", "p2", 0.5, 0.5, 0.5, 1.0), _record("d1", "p2", 0.4, 0.4, 0.4, 1.0)]
    result = compare_configurations(a, b, metric="f1")
    assert result.t_statistic is None
    assert result.p_value is None
    assert "fewer than 2" in result.note.lower()


def test_compare_configurations_zero_variance_is_explained_not_nan():
    a = [_record("d1", "p1", 1.0, 1.0, 1.0, 1.0), _record("d1", "p1", 1.0, 1.0, 1.0, 1.0)]
    b = [_record("d1", "p2", 1.0, 1.0, 1.0, 1.0), _record("d1", "p2", 1.0, 1.0, 1.0, 1.0)]
    result = compare_configurations(a, b, metric="f1")
    assert result.t_statistic is None
    assert result.p_value is None
    assert "zero variance" in result.note.lower()


def test_compare_configurations_detects_a_real_difference():
    a = [_record("d1", "p1", 1.0, 1.0, 1.0, 1.0), _record("d1", "p1", 0.95, 0.95, 0.95, 1.0)]
    b = [_record("d1", "p2", 0.2, 0.2, 0.2, 1.0), _record("d1", "p2", 0.1, 0.1, 0.1, 1.0)]
    result = compare_configurations(a, b, metric="f1")
    assert result.mean_a > result.mean_b
    assert result.t_statistic is not None
    assert result.t_statistic > 0


# --- benchmark runner: budget fairness, persistence, reproducibility ------


@pytest.fixture
def small_dataset_config():
    return DatasetGenerationConfig(
        dataset_id="bench_small", seed=200, sequence_length=35, read_depth=800,
        targets=[TargetSpec(target_id="t1", abundance=0.1, present=True)],
        n_background_sequences=20,
        error_model=ErrorModel(substitution_rate=0.005),
    )


def test_run_benchmark_sweep_applies_identical_budget_to_every_pipeline(tmp_path, small_dataset_config):
    store = ExperimentStore(tmp_path / "bench.db")
    budget = BenchmarkBudget(max_iterations=2, timeout_seconds=20.0)
    records = run_benchmark_sweep(
        [small_dataset_config], BASELINE_CONFIGURATIONS, MockProvider(),
        tmp_path / "datasets", tmp_path / "sandbox", store, budget=budget, n_repeats=1,
    )
    agent_records = [r for r in records if r.pipeline_name != "deterministic_baseline"]
    for r in agent_records:
        assert r.iteration_count is not None
        assert r.iteration_count <= budget.max_iterations


def test_run_benchmark_sweep_records_provider_and_model(tmp_path, small_dataset_config):
    """Phase 1 audit finding: a saved ExperimentRecord must say which
    provider/model actually produced it, not just that *some* run happened -
    otherwise two experiments that differ only by provider are
    indistinguishable after the fact."""
    store = ExperimentStore(tmp_path / "bench.db")
    budget = BenchmarkBudget(max_iterations=2, timeout_seconds=20.0)
    records = run_benchmark_sweep(
        [small_dataset_config], [SINGLE_AGENT, DETERMINISTIC_BASELINE], MockProvider(),
        tmp_path / "datasets", tmp_path / "sandbox", store, budget=budget, n_repeats=1,
    )
    agent_record = next(r for r in records if r.pipeline_name == "single_agent")
    assert agent_record.provider == "mock"
    assert agent_record.model == "helix-mock-v1"

    deterministic_record = next(r for r in records if r.pipeline_name == "deterministic_baseline")
    assert deterministic_record.provider is None
    assert deterministic_record.model is None


def test_run_benchmark_sweep_dispatches_adaptive_coordination(tmp_path, small_dataset_config):
    """Experiment E from the extension plan: dispatched through the same
    machinery as every fixed configuration, and the persisted record
    carries a coordination_decision so it can be compared against B/D on
    accuracy per unit of added cost, not accuracy alone."""
    store = ExperimentStore(tmp_path / "bench.db")
    budget = BenchmarkBudget(max_iterations=3, timeout_seconds=20.0)
    records = run_benchmark_sweep(
        [small_dataset_config], [ADAPTIVE_COORDINATION], MockProvider(),
        tmp_path / "datasets", tmp_path / "sandbox", store, budget=budget, n_repeats=1,
    )
    record = records[0]
    assert record.pipeline_name == "adaptive_coordination"
    assert record.coordination_decision is not None
    assert record.coordination_decision["initial_agent_count"] in (1, 2)
    assert record.coordination_decision["difficulty_tier"] in ("easy", "moderate", "difficult", "ambiguous")


def test_run_benchmark_sweep_dispatches_dual_agent_configurations(tmp_path, small_dataset_config):
    """Experiments C and D from the extension plan: two independent coding
    agents, fixed (always both), dispatched through the same
    PipelineSpec/run_pipeline/BenchmarkBudget machinery as every other
    configuration - no parallel comparison system."""
    store = ExperimentStore(tmp_path / "bench.db")
    budget = BenchmarkBudget(max_iterations=3, timeout_seconds=20.0)
    records = run_benchmark_sweep(
        [small_dataset_config], [DUAL_AGENT_NO_VERIFICATION, DUAL_AGENT_WITH_VERIFICATION], MockProvider(),
        tmp_path / "datasets", tmp_path / "sandbox", store, budget=budget, n_repeats=1,
    )
    assert {r.pipeline_name for r in records} == {"dual_agent_no_verification", "dual_agent_with_verification"}
    for r in records:
        assert r.dual_agent_comparison is not None
        assert r.dual_agent_comparison["branch_a"]["branch"] == "a"
        assert r.dual_agent_comparison["branch_b"]["branch"] == "b"
        assert r.dual_agent_comparison["winner"] in ("a", "b", "tie")
        # The persisted evaluation_metrics must come from one of the two
        # actual branches, not a stale/mismatched value - a real bug the
        # orchestrator-level tests guard more precisely (see
        # test_dual_agent_branches_fail_independently).
        if r.evaluation_metrics is not None:
            branch_precisions = {
                (r.dual_agent_comparison["branch_a"].get("validation_result") or {}).get("precision"),
                (r.dual_agent_comparison["branch_b"].get("validation_result") or {}).get("precision"),
            }
            assert r.evaluation_metrics.precision in branch_precisions


def test_run_benchmark_sweep_enforces_a_token_budget_across_every_pipeline(tmp_path, small_dataset_config):
    store = ExperimentStore(tmp_path / "bench.db")
    budget = BenchmarkBudget(max_iterations=5, timeout_seconds=20.0, max_tokens=1)
    records = run_benchmark_sweep(
        [small_dataset_config], BASELINE_CONFIGURATIONS, MockProvider(),
        tmp_path / "datasets", tmp_path / "sandbox", store, budget=budget, n_repeats=1,
    )
    agent_records = [r for r in records if r.pipeline_name != "deterministic_baseline"]
    assert agent_records  # sanity: there are agent-driven pipelines in BASELINE_CONFIGURATIONS
    assert all(r.status == "budget_exceeded" for r in agent_records)
    # The deterministic baseline makes no LLM calls at all, so an impossibly
    # small token budget has nothing to do with whether it can complete.
    deterministic = [r for r in records if r.pipeline_name == "deterministic_baseline"]
    assert all(r.status == "completed" for r in deterministic)


def test_run_benchmark_sweep_persists_every_run_including_failures(tmp_path, small_dataset_config):
    store = ExperimentStore(tmp_path / "bench.db")
    budget = BenchmarkBudget(max_iterations=1, timeout_seconds=20.0)
    records = run_benchmark_sweep(
        [small_dataset_config], BASELINE_CONFIGURATIONS + ABLATION_CONFIGURATIONS, MockProvider(),
        tmp_path / "datasets", tmp_path / "sandbox", store, budget=budget, n_repeats=1,
    )
    assert any(r.status == "failed" for r in records)  # single-iteration budget forces some failures
    stored_ids = set(store.list_ids())
    assert stored_ids == {r.experiment_id for r in records}


def test_run_benchmark_sweep_reuses_the_same_dataset_across_pipelines(tmp_path, small_dataset_config):
    store = ExperimentStore(tmp_path / "bench.db")
    records = run_benchmark_sweep(
        [small_dataset_config], BASELINE_CONFIGURATIONS, MockProvider(),
        tmp_path / "datasets", tmp_path / "sandbox", store, n_repeats=1,
    )
    dataset_dirs = list((tmp_path / "datasets").iterdir())
    assert len(dataset_dirs) == 1  # written once, not once per pipeline
    assert {r.dataset_id for r in records} == {small_dataset_config.dataset_id}


def test_benchmark_sweep_is_reproducible_with_mock_provider(tmp_path, small_dataset_config):
    store_a = ExperimentStore(tmp_path / "a.db")
    store_b = ExperimentStore(tmp_path / "b.db")
    records_a = run_benchmark_sweep(
        [small_dataset_config], BASELINE_CONFIGURATIONS, MockProvider(),
        tmp_path / "datasets_a", tmp_path / "sandbox_a", store_a, n_repeats=1,
    )
    records_b = run_benchmark_sweep(
        [small_dataset_config], BASELINE_CONFIGURATIONS, MockProvider(),
        tmp_path / "datasets_b", tmp_path / "sandbox_b", store_b, n_repeats=1,
    )
    by_pipeline_a = {r.pipeline_name: r for r in records_a}
    by_pipeline_b = {r.pipeline_name: r for r in records_b}
    for name in by_pipeline_a:
        ra, rb = by_pipeline_a[name], by_pipeline_b[name]
        assert ra.status == rb.status
        if ra.evaluation_metrics and rb.evaluation_metrics:
            assert ra.evaluation_metrics.f1 == rb.evaluation_metrics.f1


def test_repeat_index_is_recorded(tmp_path, small_dataset_config):
    store = ExperimentStore(tmp_path / "bench.db")
    from helix.benchmarking.baselines import DETERMINISTIC_BASELINE

    records = run_benchmark_sweep(
        [small_dataset_config], [DETERMINISTIC_BASELINE], MockProvider(),
        tmp_path / "datasets", tmp_path / "sandbox", store, n_repeats=3,
    )
    assert sorted(r.repeat_index for r in records) == [0, 1, 2]


# --- reporting ---------------------------------------------------------


def test_reports_and_plots_are_generated(tmp_path, small_dataset_config):
    store = ExperimentStore(tmp_path / "bench.db")
    records = run_benchmark_sweep(
        [small_dataset_config], BASELINE_CONFIGURATIONS, MockProvider(),
        tmp_path / "datasets", tmp_path / "sandbox", store, n_repeats=1,
    )
    summaries = aggregate_runs(records)

    export_summary_json(summaries, tmp_path / "summary.json")
    export_summary_csv(summaries, tmp_path / "summary.csv")
    generate_markdown_report(summaries, tmp_path / "report.md")

    parsed = json.loads((tmp_path / "summary.json").read_text())
    assert len(parsed) == len(summaries)
    assert (tmp_path / "summary.csv").read_text().startswith("dataset_id,pipeline_name")
    assert "HELIX Benchmark Report" in (tmp_path / "report.md").read_text()

    plot_paths = save_all_plots(summaries, tmp_path / "plots")
    for path in plot_paths.values():
        assert path.exists()
        assert path.stat().st_size > 0
