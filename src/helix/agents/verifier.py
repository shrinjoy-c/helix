"""Independent Verification Agent, and the separate Verifier Agent that
interprets its output.

`VerificationAgent` deliberately does NOT call an LLM provider for its
pass/fail decision: verification against ground truth is an objective
metric computation against acceptance criteria that were fixed before
the run, not a judgment call. This is the one agent whose core output
would be identical regardless of which provider (or none) backs the rest
of the system - that is the point of having it be "independent."

It receives a path to the dataset directory and loads ground truth
itself (via `helix.biology.evaluation`, the only module allowed to do
so) - it is never handed ground truth by another agent, and it returns
only a redacted `ValidationResult` for the shared state that other
agents may read, keeping the full `EvaluationMetrics` (which contains
true counts/frequencies) out of the iterative feedback loop. The full
metrics are still returned to the orchestrator for the final persisted
experiment record.

`VerifierAgent`, defined below, is the deliberately different other half
of the Verifier role split: it calls a provider, and its job is to
interpret `VerificationAgent`'s already-fixed score into corrective
instructions - never to touch the score itself. See
docs/agent_design.md's "The Verifier Agent vs. the Verification Agent".
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from helix.agents.base import Agent, call_structured
from helix.biology.evaluation import evaluate, load_ground_truth
from helix.config.models import AnalysisResult, EvaluationMetrics
from helix.orchestration.messages import ValidationResult, VerifierGuidance, VerifierGuidanceRequest
from helix.providers.base import ProviderResponse


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


_VERIFIER_SYSTEM = (
    "You are the Verifier agent in a computational biology research team. The Evaluation "
    "Engine has already scored the Implementation Agent's latest attempt against fixed, "
    "objective acceptance criteria - that score is not negotiable and you cannot see or "
    "recompute it. Your job is different: turn the redacted score and message you are given "
    "into specific, actionable corrective instructions the Implementation Agent can act on in "
    "its next revision. If a regression was detected relative to the previous attempt, your "
    "instructions must address it explicitly. Respond with only JSON."
)


class VerifierAgent(Agent):
    """LLM-driven interpretation of the Evaluation Engine's output - the
    other half of the Verifier role split (Decision 1 in the extension
    plan). Deliberately the mirror image of `VerificationAgent` above:
    this class never imports `helix.biology.evaluation`, never calls
    `load_ground_truth`, and never recomputes a metric. It only ever sees
    the same redacted `ValidationResult` fields the revision loop already
    has, plus a regression signal computed separately by
    `helix.evaluation_engine.detect_regression` (a deterministic
    comparison, not a judgment call, so it stays out of this agent's
    hands). What it decides is purely about *framing feedback*, never
    about *what counts as passing*.
    """

    role = "verifier_agent"

    def interpret(self, request: VerifierGuidanceRequest) -> tuple[VerifierGuidance, ProviderResponse]:
        return call_structured(self.provider, _VERIFIER_SYSTEM, "verifier_interpret", request, VerifierGuidance)
