"""Independent Evaluation Engine service surface.

Packages the deterministic ground-truth evaluator and the persisted
experiment store behind the five named operations the project's Verifier
role split calls for: `run_evaluation`, `get_metrics`, `get_failure_report`,
`compare_implementations`, `get_validation_results`.

This is a plain internal Python module, not a network service or an MCP
server - the project's extension plan considered MCP for exactly this
surface and declined it (Decision 2: "zero new dependency... revisit only
if evaluation needs to be reachable from outside this one process"). The
access boundary this module expresses is architectural, not a process
boundary: everything that scores a run or inspects a scored run goes
through here, so no other module re-implements "what counts as a failure"
or "which run was better" with its own ad hoc logic.

Every function here is read-only with respect to ground truth, and none of
them can be asked to redefine acceptance criteria after seeing a result -
`run_evaluation` is the same fixed-formula computation
`helix.biology.evaluation.evaluate` already performs; the rest only read
back already-scored, already-persisted `ExperimentRecord`s. Nothing here
is itself an agent - `helix.agents.verifier.VerificationAgent` remains the
one call site inside a live run that calls `run_evaluation`'s underlying
logic; the new LLM-driven `helix.agents.verifier.VerifierAgent` (the other
half of the role split) only ever sees this module's *output*
(`ValidationResult`, `FailureReport`, regression signals), never ground
truth, never this module's `load_ground_truth` import.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from helix.biology.evaluation import evaluate, load_ground_truth
from helix.config.models import AnalysisResult, EvaluationMetrics
from helix.experiments.store import ExperimentStore

FailureMode = Literal[
    "none",
    "execution_failed",
    "reviewer_rejected",
    "metric_miss",
    "timed_out",
    "budget_exceeded",
    "cancelled",
    "unknown",
]


class FailureReport(BaseModel):
    """The evaluation engine's best determination of *why* a run didn't
    complete successfully, derived only from the persisted event log and
    final status - never a guess, and never re-derived from raw ground
    truth (which this module never re-reads for an already-scored run)."""

    experiment_id: str
    failure_mode: FailureMode
    detail: str
    final_metrics: Optional[EvaluationMetrics] = None


class ImplementationComparison(BaseModel):
    """A post-hoc comparison of two already-persisted, already-scored
    experiments - e.g. two independently-run configurations on the same
    dataset. Distinct from `helix.orchestration.messages.DualAgentComparison`,
    which compares two branches of one *live* `run_dual_agent` call before
    either is persisted; this operates on anything already in the store."""

    experiment_id_a: str
    experiment_id_b: str
    winner: Literal["a", "b", "tie", "incomparable"]
    f1_delta: Optional[float] = Field(
        default=None, description="metrics_a.f1 - metrics_b.f1; None when incomparable."
    )
    rationale: str


def run_evaluation(analysis_result: AnalysisResult, dataset_dir: str | Path) -> EvaluationMetrics:
    """Score one `AnalysisResult` against its dataset's ground truth.

    Identical computation to `helix.agents.verifier.VerificationAgent.verify`,
    exposed under the service's own name for callers (tooling, the Verifier
    Agent's deterministic regression check, future reporting) that want the
    full `EvaluationMetrics` directly rather than the redacted
    `ValidationResult` the iterative revision loop is restricted to.
    """
    ground_truth = load_ground_truth(dataset_dir)
    return evaluate(analysis_result, ground_truth)


def get_metrics(store: ExperimentStore, experiment_id: str) -> Optional[EvaluationMetrics]:
    """The persisted `EvaluationMetrics` for one experiment, or `None` if
    the experiment doesn't exist or was never scored (e.g. it failed before
    producing an `AnalysisResult`)."""
    record = store.load(experiment_id)
    return record.evaluation_metrics if record else None


def get_failure_report(store: ExperimentStore, experiment_id: str) -> Optional[FailureReport]:
    """Classify why a persisted experiment ended the way it did.

    Reads only `ExperimentRecord.status`, `.event_log`, and
    `.evaluation_metrics` - all already public, already-redacted fields. A
    `"completed"` record still gets a report (`failure_mode="none"`) so
    callers never have to special-case the success path separately.
    """
    record = store.load(experiment_id)
    if record is None:
        return None

    if record.status == "completed":
        return FailureReport(
            experiment_id=experiment_id, failure_mode="none",
            detail="Completed successfully.", final_metrics=record.evaluation_metrics,
        )
    if record.status == "timed_out":
        return FailureReport(
            experiment_id=experiment_id, failure_mode="timed_out",
            detail="Experiment exceeded its wall-clock budget.", final_metrics=record.evaluation_metrics,
        )
    if record.status == "cancelled":
        return FailureReport(
            experiment_id=experiment_id, failure_mode="cancelled",
            detail="Experiment was cancelled before completion.", final_metrics=record.evaluation_metrics,
        )
    if record.status == "budget_exceeded":
        return FailureReport(
            experiment_id=experiment_id, failure_mode="budget_exceeded",
            detail="Experiment exceeded its token/cost budget.", final_metrics=record.evaluation_metrics,
        )

    # "failed" / "needs_revision": inspect the event log for the most
    # specific proximate cause, falling back to "unknown" rather than
    # guessing one.
    events = record.event_log or []
    reviewer_blocked = any(
        e.get("event_type") == "code_reviewed" and not e.get("payload", {}).get("approved", True)
        for e in events
    )
    execution_failed = any(
        e.get("event_type") == "code_executed" and "failed" in e.get("message", "") for e in events
    )
    metrics = record.evaluation_metrics
    if metrics is not None and (metrics.precision < 1.0 or metrics.recall < 1.0 or metrics.false_positive_rate > 0.0):
        failure_mode: FailureMode = "metric_miss"
        detail = f"Final attempt did not meet acceptance criteria (f1={metrics.f1:.2f})."
    elif execution_failed:
        failure_mode, detail = "execution_failed", "At least one iteration's generated code failed to execute."
    elif reviewer_blocked:
        failure_mode, detail = "reviewer_rejected", "The Reviewer blocked at least one code revision."
    else:
        failure_mode, detail = "unknown", f"Status={record.status!r} but no specific cause was found in the event log."
    return FailureReport(experiment_id=experiment_id, failure_mode=failure_mode, detail=detail, final_metrics=metrics)


def compare_implementations(
    store: ExperimentStore, experiment_id_a: str, experiment_id_b: str
) -> ImplementationComparison:
    """Compare two already-persisted experiments by their recorded F1.

    Deliberately the same kind of comparison `_compare_coding_branches`
    makes for a live `run_dual_agent` call, but over the store instead of
    two in-memory branches - the same "two independently-reasoned
    implementations" comparison applies whether the two runs happened
    concurrently in one process or were two separate experiments entirely.
    """
    record_a = store.load(experiment_id_a)
    record_b = store.load(experiment_id_b)
    if (
        record_a is None
        or record_b is None
        or record_a.evaluation_metrics is None
        or record_b.evaluation_metrics is None
    ):
        return ImplementationComparison(
            experiment_id_a=experiment_id_a, experiment_id_b=experiment_id_b, winner="incomparable",
            rationale="One or both experiments have no recorded evaluation metrics.",
        )
    metrics_a, metrics_b = record_a.evaluation_metrics, record_b.evaluation_metrics
    delta = metrics_a.f1 - metrics_b.f1
    winner: Literal["a", "b", "tie"] = "tie" if abs(delta) < 1e-9 else ("a" if delta > 0 else "b")
    return ImplementationComparison(
        experiment_id_a=experiment_id_a, experiment_id_b=experiment_id_b, winner=winner, f1_delta=delta,
        rationale=f"{experiment_id_a} f1={metrics_a.f1:.3f} vs. {experiment_id_b} f1={metrics_b.f1:.3f}",
    )


def get_validation_results(store: ExperimentStore, experiment_id: str) -> list[dict]:
    """Every per-cycle revision record for one experiment, in order.

    Returns `ExperimentRecord.revision_cycles` (plain dicts - see that
    field's docstring for why it isn't the typed `RevisionCycleRecord`
    here) - the structured, per-attempt history the Phase 1 gap analysis
    found missing (free-text event-log entries only). Only populated for
    `run_multi_agent` runs so far; `[]` for any other workflow or an
    unknown experiment_id, never an error.
    """
    record = store.load(experiment_id)
    if record is None or not record.revision_cycles:
        return []
    return list(record.revision_cycles)


def detect_regression(
    current_f1: float,
    current_passed: bool,
    previous_f1: Optional[float],
    previous_passed: Optional[bool],
) -> tuple[bool, str]:
    """Deterministic regression check between two consecutive revision
    attempts on the *same* run. A pure scoring computation, not a judgment
    call - this is why it lives in the Evaluation Engine rather than the
    LLM-driven Verifier Agent, which only ever consumes this function's
    output alongside the current attempt's `ValidationResult`.

    `previous_f1 is None` (first attempt) is never a regression, by
    definition. Otherwise: going from passed to not-passed, or F1 dropping
    relative to the previous attempt, both count.
    """
    if previous_f1 is None:
        return False, ""
    if previous_passed and not current_passed:
        return (
            True,
            f"Previous attempt passed (f1={previous_f1:.2f}); this attempt did not (f1={current_f1:.2f}).",
        )
    if current_f1 < previous_f1 - 1e-9:
        return True, f"f1 decreased from {previous_f1:.2f} to {current_f1:.2f} relative to the previous attempt."
    return False, ""
