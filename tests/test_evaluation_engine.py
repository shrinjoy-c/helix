from __future__ import annotations

from datetime import datetime, timezone

import pytest

from helix.biology.matching import analyze_dataset
from helix.biology.simulation import write_dataset
from helix.config.models import (
    DatasetGenerationConfig,
    ErrorModel,
    EvaluationMetrics,
    ExperimentRecord,
    MatchingConfig,
    TargetSpec,
)
from helix.evaluation_engine import (
    compare_implementations,
    detect_regression,
    get_failure_report,
    get_metrics,
    get_validation_results,
    run_evaluation,
)
from helix.experiments.store import ExperimentStore


@pytest.fixture
def dataset_dir(tmp_path):
    config = DatasetGenerationConfig(
        dataset_id="eval_engine_ds", seed=11, sequence_length=35, read_depth=600,
        targets=[TargetSpec(target_id="t1", abundance=0.1, present=True)],
        n_background_sequences=20,
        error_model=ErrorModel(substitution_rate=0.0),
    )
    paths = write_dataset(config, tmp_path / "datasets")
    return paths["reads_fastq"].parent


def _metrics(f1: float, precision: float = 1.0, recall: float = 1.0) -> EvaluationMetrics:
    return EvaluationMetrics(
        dataset_id="ds", pipeline_name="p",
        true_positives=1, false_positives=0, false_negatives=0, true_negatives=0,
        precision=precision, recall=recall, f1=f1, false_positive_rate=0.0, false_negative_rate=0.0,
    )


def _record(experiment_id: str, status: str, **extra) -> ExperimentRecord:
    return ExperimentRecord(
        experiment_id=experiment_id, dataset_id="ds", pipeline_name="p", status=status,
        dataset_config=DatasetGenerationConfig(dataset_id="ds", seed=1),
        runtime_seconds=1.0, created_at=datetime.now(timezone.utc).isoformat(),
        **extra,
    )


# --- run_evaluation: the live-run scoring path --------------------------


def test_run_evaluation_scores_a_perfect_deterministic_run(dataset_dir):
    analysis_result = analyze_dataset(dataset_dir, MatchingConfig(max_mismatches=0, min_count=1))
    metrics = run_evaluation(analysis_result, dataset_dir)
    assert metrics.precision == 1.0
    assert metrics.recall == 1.0


# --- get_metrics ----------------------------------------------------------


def test_get_metrics_returns_none_for_unknown_experiment(tmp_path):
    store = ExperimentStore(tmp_path / "db.sqlite")
    assert get_metrics(store, "nope") is None


def test_get_metrics_returns_the_persisted_metrics(tmp_path):
    store = ExperimentStore(tmp_path / "db.sqlite")
    store.save(_record("exp1", "completed", evaluation_metrics=_metrics(f1=0.9)))
    metrics = get_metrics(store, "exp1")
    assert metrics is not None
    assert metrics.f1 == 0.9


# --- get_failure_report ----------------------------------------------------


def test_get_failure_report_none_for_unknown_experiment(tmp_path):
    store = ExperimentStore(tmp_path / "db.sqlite")
    assert get_failure_report(store, "nope") is None


def test_get_failure_report_completed_run_reports_no_failure(tmp_path):
    store = ExperimentStore(tmp_path / "db.sqlite")
    store.save(_record("exp1", "completed", evaluation_metrics=_metrics(f1=1.0)))
    report = get_failure_report(store, "exp1")
    assert report.failure_mode == "none"


@pytest.mark.parametrize(
    "status,failure_mode",
    [("timed_out", "timed_out"), ("cancelled", "cancelled"), ("budget_exceeded", "budget_exceeded")],
)
def test_get_failure_report_maps_terminal_statuses_directly(tmp_path, status, failure_mode):
    store = ExperimentStore(tmp_path / "db.sqlite")
    store.save(_record("exp1", status))
    report = get_failure_report(store, "exp1")
    assert report.failure_mode == failure_mode


def test_get_failure_report_detects_reviewer_rejection(tmp_path):
    store = ExperimentStore(tmp_path / "db.sqlite")
    event_log = [
        {"sequence": 0, "event_type": "code_reviewed", "actor": "reviewer", "message": "approved=False",
         "payload": {"approved": False}},
    ]
    store.save(_record("exp1", "failed", event_log=event_log))
    report = get_failure_report(store, "exp1")
    assert report.failure_mode == "reviewer_rejected"


def test_get_failure_report_detects_execution_failure(tmp_path):
    store = ExperimentStore(tmp_path / "db.sqlite")
    event_log = [
        {"sequence": 0, "event_type": "code_executed", "actor": "implementation",
         "message": "failed: boom", "payload": {}},
    ]
    store.save(_record("exp1", "failed", event_log=event_log))
    report = get_failure_report(store, "exp1")
    assert report.failure_mode == "execution_failed"


def test_get_failure_report_prefers_metric_miss_when_metrics_exist(tmp_path):
    store = ExperimentStore(tmp_path / "db.sqlite")
    store.save(_record("exp1", "failed", evaluation_metrics=_metrics(f1=0.3, precision=0.3, recall=0.5)))
    report = get_failure_report(store, "exp1")
    assert report.failure_mode == "metric_miss"


def test_get_failure_report_falls_back_to_unknown(tmp_path):
    store = ExperimentStore(tmp_path / "db.sqlite")
    store.save(_record("exp1", "failed"))
    report = get_failure_report(store, "exp1")
    assert report.failure_mode == "unknown"


# --- compare_implementations -----------------------------------------------


def test_compare_implementations_incomparable_when_metrics_missing(tmp_path):
    store = ExperimentStore(tmp_path / "db.sqlite")
    store.save(_record("exp_a", "completed"))
    store.save(_record("exp_b", "completed", evaluation_metrics=_metrics(f1=0.5)))
    comparison = compare_implementations(store, "exp_a", "exp_b")
    assert comparison.winner == "incomparable"


def test_compare_implementations_picks_the_higher_f1(tmp_path):
    store = ExperimentStore(tmp_path / "db.sqlite")
    store.save(_record("exp_a", "completed", evaluation_metrics=_metrics(f1=0.9)))
    store.save(_record("exp_b", "completed", evaluation_metrics=_metrics(f1=0.6)))
    comparison = compare_implementations(store, "exp_a", "exp_b")
    assert comparison.winner == "a"
    assert comparison.f1_delta == pytest.approx(0.3)


def test_compare_implementations_ties_on_equal_f1(tmp_path):
    store = ExperimentStore(tmp_path / "db.sqlite")
    store.save(_record("exp_a", "completed", evaluation_metrics=_metrics(f1=0.7)))
    store.save(_record("exp_b", "completed", evaluation_metrics=_metrics(f1=0.7)))
    comparison = compare_implementations(store, "exp_a", "exp_b")
    assert comparison.winner == "tie"


# --- get_validation_results -------------------------------------------------


def test_get_validation_results_empty_for_unknown_experiment(tmp_path):
    store = ExperimentStore(tmp_path / "db.sqlite")
    assert get_validation_results(store, "nope") == []


def test_get_validation_results_empty_when_not_recorded(tmp_path):
    store = ExperimentStore(tmp_path / "db.sqlite")
    store.save(_record("exp1", "completed"))
    assert get_validation_results(store, "exp1") == []


def test_get_validation_results_returns_the_persisted_cycles(tmp_path):
    store = ExperimentStore(tmp_path / "db.sqlite")
    cycles = [{"cycle": 1, "outcome": "verification_passed"}]
    store.save(_record("exp1", "completed", revision_cycles=cycles))
    assert get_validation_results(store, "exp1") == cycles


# --- detect_regression -------------------------------------------------------


def test_detect_regression_first_attempt_is_never_a_regression():
    regressed, _ = detect_regression(current_f1=0.3, current_passed=False, previous_f1=None, previous_passed=None)
    assert regressed is False


def test_detect_regression_flags_pass_to_fail():
    regressed, explanation = detect_regression(
        current_f1=0.7, current_passed=False, previous_f1=0.9, previous_passed=True,
    )
    assert regressed is True
    assert "passed" in explanation


def test_detect_regression_flags_f1_drop():
    regressed, explanation = detect_regression(
        current_f1=0.5, current_passed=False, previous_f1=0.8, previous_passed=False,
    )
    assert regressed is True
    assert "decreased" in explanation


def test_detect_regression_improvement_is_not_a_regression():
    regressed, _ = detect_regression(current_f1=0.9, current_passed=True, previous_f1=0.5, previous_passed=False)
    assert regressed is False
