import pytest

from helix.biology.evaluation import evaluate
from helix.config.models import (
    AnalysisResult,
    GroundTruth,
    GroundTruthTarget,
    PredictedTarget,
)


def _gt(targets: list[GroundTruthTarget]) -> GroundTruth:
    return GroundTruth(
        dataset_id="ds1",
        config_hash="abc123",
        total_reads=1000,
        total_templates=1000,
        duplicate_reads=0,
        background_template_count=900,
        targets=targets,
    )


def test_perfect_detection_yields_perfect_metrics():
    gt = _gt([GroundTruthTarget(target_id="t1", sequence="A" * 10, true_count=100, true_frequency=0.1, present=True)])
    result = AnalysisResult(
        dataset_id="ds1",
        pipeline_name="baseline",
        predictions=[
            PredictedTarget(target_id="t1", matched_read_count=100, matched_molecule_count=100, estimated_frequency=0.1, detected=True)
        ],
        total_reads_processed=1000,
        total_molecules_after_dedup=1000,
        runtime_seconds=0.01,
    )
    metrics = evaluate(result, gt)
    assert metrics.precision == 1.0
    assert metrics.recall == 1.0
    assert metrics.f1 == 1.0
    assert metrics.false_positive_rate == 0.0
    assert metrics.abundance_mae == pytest.approx(0.0)


def test_false_positive_on_absent_target():
    gt = _gt([GroundTruthTarget(target_id="t1", sequence="A" * 10, true_count=0, true_frequency=0.0, present=False)])
    result = AnalysisResult(
        dataset_id="ds1",
        pipeline_name="baseline",
        predictions=[
            PredictedTarget(target_id="t1", matched_read_count=5, matched_molecule_count=5, estimated_frequency=0.005, detected=True)
        ],
        total_reads_processed=1000,
        total_molecules_after_dedup=1000,
        runtime_seconds=0.01,
    )
    metrics = evaluate(result, gt)
    assert metrics.false_positives == 1
    assert metrics.true_positives == 0
    assert metrics.precision == 0.0
    assert metrics.false_positive_rate == 1.0


def test_false_negative_on_missed_target():
    gt = _gt([GroundTruthTarget(target_id="t1", sequence="A" * 10, true_count=50, true_frequency=0.05, present=True)])
    result = AnalysisResult(
        dataset_id="ds1",
        pipeline_name="baseline",
        predictions=[
            PredictedTarget(target_id="t1", matched_read_count=0, matched_molecule_count=0, estimated_frequency=0.0, detected=False)
        ],
        total_reads_processed=1000,
        total_molecules_after_dedup=1000,
        runtime_seconds=0.01,
    )
    metrics = evaluate(result, gt)
    assert metrics.false_negatives == 1
    assert metrics.recall == 0.0
    assert metrics.f1 == 0.0


def test_true_negative_no_target_present_correctly_identified():
    """A system that reports nothing on a true no-target dataset should
    score perfectly, not be penalized for 'finding nothing'."""
    gt = _gt([GroundTruthTarget(target_id="t1", sequence="A" * 10, true_count=0, true_frequency=0.0, present=False)])
    result = AnalysisResult(
        dataset_id="ds1",
        pipeline_name="baseline",
        predictions=[
            PredictedTarget(target_id="t1", matched_read_count=0, matched_molecule_count=0, estimated_frequency=0.0, detected=False)
        ],
        total_reads_processed=1000,
        total_molecules_after_dedup=1000,
        runtime_seconds=0.01,
    )
    metrics = evaluate(result, gt)
    assert metrics.true_negatives == 1
    assert metrics.precision == 1.0
    assert metrics.recall == 1.0
    assert metrics.abundance_mae is None  # no truly-present targets to average over


def test_missing_prediction_for_a_target_counts_as_not_detected():
    gt = _gt([GroundTruthTarget(target_id="t1", sequence="A" * 10, true_count=50, true_frequency=0.05, present=True)])
    result = AnalysisResult(
        dataset_id="ds1",
        pipeline_name="baseline",
        predictions=[],  # pipeline didn't even report on t1
        total_reads_processed=1000,
        total_molecules_after_dedup=1000,
        runtime_seconds=0.01,
    )
    metrics = evaluate(result, gt)
    assert metrics.false_negatives == 1


def test_dataset_id_mismatch_raises():
    gt = _gt([GroundTruthTarget(target_id="t1", sequence="A" * 10, true_count=0, true_frequency=0.0, present=False)])
    result = AnalysisResult(
        dataset_id="wrong_ds",
        pipeline_name="baseline",
        predictions=[],
        total_reads_processed=1000,
        total_molecules_after_dedup=1000,
        runtime_seconds=0.01,
    )
    with pytest.raises(ValueError):
        evaluate(result, gt)
