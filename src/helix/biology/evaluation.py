"""Independent ground-truth evaluator.

This module is the only code in HELIX that is allowed to read a
`GroundTruth` object. It must never be imported by anything in
`helix.biology.matching` or by an analysis/agent pipeline - evaluation
always happens strictly after an `AnalysisResult` has already been
produced from public files only, and this module cannot be asked to
redefine success criteria after seeing predictions (metrics are fixed
formulas, not negotiable thresholds chosen post hoc).
"""

from __future__ import annotations

from pathlib import Path

from helix.config.models import AnalysisResult, EvaluationMetrics, GroundTruth, TargetEvaluation
from helix.io.fastq import read_reference_panel


def _safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    return numerator / denominator if denominator else default


def evaluate(analysis_result: AnalysisResult, ground_truth: GroundTruth) -> EvaluationMetrics:
    if analysis_result.dataset_id and analysis_result.dataset_id != ground_truth.dataset_id:
        raise ValueError(
            f"dataset_id mismatch: analysis={analysis_result.dataset_id!r} "
            f"ground_truth={ground_truth.dataset_id!r}"
        )

    predictions_by_id = {p.target_id: p for p in analysis_result.predictions}
    gt_by_id = {t.target_id: t for t in ground_truth.targets}

    tp = fp = fn = tn = 0
    per_target: list[TargetEvaluation] = []
    abundance_errors: list[float] = []

    for target_id, gt in gt_by_id.items():
        pred = predictions_by_id.get(target_id)
        predicted_present = bool(pred.detected) if pred else False
        estimated_frequency = pred.estimated_frequency if pred else 0.0

        if gt.present and predicted_present:
            tp += 1
        elif (not gt.present) and predicted_present:
            fp += 1
        elif gt.present and not predicted_present:
            fn += 1
        else:
            tn += 1

        abundance_error = abs(estimated_frequency - gt.true_frequency)
        if gt.present:
            abundance_errors.append(abundance_error)

        per_target.append(
            TargetEvaluation(
                target_id=target_id,
                true_present=gt.present,
                predicted_present=predicted_present,
                true_frequency=gt.true_frequency,
                estimated_frequency=estimated_frequency,
                abundance_error=abundance_error,
            )
        )

    precision = _safe_div(tp, tp + fp, default=1.0 if (tp + fp + fn) == 0 else 0.0)
    recall = _safe_div(tp, tp + fn, default=1.0 if (tp + fp + fn) == 0 else 0.0)
    f1 = _safe_div(2 * precision * recall, precision + recall, default=0.0)
    fpr = _safe_div(fp, fp + tn)
    fnr = _safe_div(fn, fn + tp)
    abundance_mae = (sum(abundance_errors) / len(abundance_errors)) if abundance_errors else None

    return EvaluationMetrics(
        dataset_id=ground_truth.dataset_id,
        pipeline_name=analysis_result.pipeline_name,
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        true_negatives=tn,
        precision=precision,
        recall=recall,
        f1=f1,
        false_positive_rate=fpr,
        false_negative_rate=fnr,
        abundance_mae=abundance_mae,
        per_target=per_target,
    )


def load_ground_truth(dataset_dir: str | Path) -> GroundTruth:
    """Load ground truth from the `_ground_truth/` directory of a dataset.

    Callers that want to demonstrate the trust boundary should only ever
    call this *after* running analysis against the public directory.
    """
    path = Path(dataset_dir) / "_ground_truth" / "ground_truth.json"
    return GroundTruth.model_validate_json(path.read_text())


def verify_reference_panel_matches_ground_truth(dataset_dir: str | Path) -> bool:
    """Sanity check: every target_id in the public reference panel has a
    corresponding ground-truth entry (not a security boundary, just a
    consistency check used in tests)."""
    dataset_dir = Path(dataset_dir)
    panel_ids = {tid for tid, _ in read_reference_panel(dataset_dir / "reference_panel.fasta")}
    gt = load_ground_truth(dataset_dir)
    gt_ids = {t.target_id for t in gt.targets}
    return panel_ids == gt_ids
