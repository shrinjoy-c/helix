"""Independent Verification Agent.

Deliberately does NOT call an LLM provider for its pass/fail decision:
verification against ground truth is an objective metric computation
against acceptance criteria that were fixed before the run, not a
judgment call. This is the one agent whose core output would be
identical regardless of which provider (or none) backs the rest of the
system - that is the point of having it be "independent."

It receives a path to the dataset directory and loads ground truth
itself (via `helix.biology.evaluation`, the only module allowed to do
so) - it is never handed ground truth by another agent, and it returns
only a redacted `ValidationResult` for the shared state that other
agents may read, keeping the full `EvaluationMetrics` (which contains
true counts/frequencies) out of the iterative feedback loop. The full
metrics are still returned to the orchestrator for the final persisted
experiment record.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from helix.biology.evaluation import evaluate, load_ground_truth
from helix.config.models import AnalysisResult, EvaluationMetrics
from helix.orchestration.messages import ValidationResult


class AcceptanceCriteria(BaseModel):
    min_precision: float = 0.8
    min_recall: float = 0.8
    max_false_positive_rate: float = 0.2


class VerificationAgent:
    role = "verifier"

    def verify(
        self,
        analysis_result: AnalysisResult,
        dataset_dir: str | Path,
        criteria: AcceptanceCriteria,
    ) -> tuple[ValidationResult, EvaluationMetrics]:
        ground_truth = load_ground_truth(dataset_dir)
        metrics = evaluate(analysis_result, ground_truth)

        passed = (
            metrics.precision >= criteria.min_precision
            and metrics.recall >= criteria.min_recall
            and metrics.false_positive_rate <= criteria.max_false_positive_rate
        )
        if passed:
            message = "All acceptance criteria met."
        else:
            reasons = []
            if metrics.precision < criteria.min_precision:
                reasons.append(f"precision {metrics.precision:.2f} < required {criteria.min_precision:.2f}")
            if metrics.recall < criteria.min_recall:
                reasons.append(f"recall {metrics.recall:.2f} < required {criteria.min_recall:.2f}")
            if metrics.false_positive_rate > criteria.max_false_positive_rate:
                reasons.append(
                    f"false_positive_rate {metrics.false_positive_rate:.2f} > allowed "
                    f"{criteria.max_false_positive_rate:.2f}"
                )
            message = "Acceptance criteria not met: " + "; ".join(reasons)

        validation_result = ValidationResult(
            passed=passed,
            precision=metrics.precision,
            recall=metrics.recall,
            f1=metrics.f1,
            false_positive_rate=metrics.false_positive_rate,
            false_negative_rate=metrics.false_negative_rate,
            message=message,
        )
        return validation_result, metrics
