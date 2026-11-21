"""Drives a single-agent or multi-agent experiment run against one dataset.

Both workflows share: the restricted execution sandbox, the independent
Verification Agent, bounded iteration/timeout budgets, and complete event
logging. They differ in which agent roles actually participate - that
difference *is* the single-agent vs. multi-agent baseline the benchmark
compares (see docs/agent_design.md).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from helix.agents.strategist import StrategistAgent
from helix.agents.experiment_designer import ExperimentDesignerAgent
from helix.agents.implementation import ImplementationAgent
from helix.agents.principal_investigator import PrincipalInvestigatorAgent
from helix.agents.reviewer import ReviewerAgent
from helix.agents.verifier import AcceptanceCriteria, VerificationAgent
from helix.config.models import EvaluationMetrics, MatchingConfig
from helix.execution.sandbox import RestrictedSandbox
from helix.orchestration.event_log import EventLog
from helix.orchestration.scheduler import CancellationToken, ExperimentCancelled, ExperimentTimedOut, IterationBudget
from helix.orchestration.state import ExperimentState
from helix.providers.base import LLMProvider, ProviderResponse


def _total_error_rate(dataset_metadata: dict) -> float:
    error_model = dataset_metadata.get("error_model", {})
    return (
        error_model.get("substitution_rate", 0.0)
        + error_model.get("insertion_rate", 0.0)
        + error_model.get("deletion_rate", 0.0)
    )


def _track_usage(state: ExperimentState, response: ProviderResponse) -> None:
    state.token_usage["input_tokens"] += response.input_tokens
    state.token_usage["output_tokens"] += response.output_tokens
    state.estimated_cost_usd += response.estimated_cost_usd


def _load_dataset_metadata(dataset_dir: Path) -> dict:
    return json.loads((dataset_dir / "dataset_metadata.json").read_text())


class OrchestratorResult:
    def __init__(self, state: ExperimentState, event_log: EventLog, metrics: Optional[EvaluationMetrics]):
        self.state = state
        self.event_log = event_log
        self.metrics = metrics


def run_single_agent(
    experiment_id: str,
    dataset_dir: str | Path,
    research_objective: str,
    provider: LLMProvider,
    sandbox_root: str | Path,
    max_iterations: int = 3,
    timeout_seconds: float = 60.0,
    criteria: AcceptanceCriteria | None = None,
    cancellation_token: Optional[CancellationToken] = None,
) -> OrchestratorResult:
    """One generalist agent proposes an approach, writes code, executes it,
    and is scored by the (always-independent) Verification Agent - with no
    separate Reviewer, Experiment Designer, or PI role in the loop."""
    dataset_dir = Path(dataset_dir)
    criteria = criteria or AcceptanceCriteria()
    metadata = _load_dataset_metadata(dataset_dir)
    dataset_id = metadata["dataset_id"]

    event_log = EventLog()
    state = ExperimentState(
        experiment_id=experiment_id,
        dataset_id=dataset_id,
        research_objective=research_objective,
        workflow="single_agent",
        max_iterations=max_iterations,
    )

    strategist = StrategistAgent(provider)
    impl = ImplementationAgent(provider)
    verifier = VerificationAgent()
    sandbox = RestrictedSandbox(sandbox_root, timeout_seconds=min(15.0, timeout_seconds))

    proposal, resp = strategist.propose_approach(metadata, research_objective)
    _track_usage(state, resp)
    state.proposed_methods.append(proposal)
    event_log.append("method_proposed", "single_agent", proposal.approach_summary)

    matching_config = MatchingConfig(
        max_mismatches=proposal.recommended_max_mismatches,
        collapse_duplicates=proposal.recommended_collapse_duplicates,
        min_count=proposal.recommended_min_count,
    )
    state.matching_config = matching_config

    budget = IterationBudget(max_iterations, timeout_seconds)
    code_artifact = None
    execution_error: Optional[str] = None
    final_metrics: Optional[EvaluationMetrics] = None
    passed = False

    try:
        while budget.has_iterations_remaining():
            if cancellation_token:
                cancellation_token.raise_if_cancelled()
            iteration = budget.start_iteration()
            state.iteration = iteration

            if code_artifact is None:
                code_artifact, resp = impl.write_analysis_code(matching_config, dataset_id)
            else:
                code_artifact, resp = impl.revise_code(
                    matching_config, dataset_id, review_issues=[], execution_error=execution_error,
                    previous_version=code_artifact.version,
                )
            _track_usage(state, resp)
            state.generated_code.append(code_artifact)
            event_log.append("code_written", "single_agent", f"version {code_artifact.version}")

            execution_result = impl.execute(code_artifact, dataset_dir, sandbox)
            state.execution_results.append(execution_result)
            event_log.append(
                "code_executed", "single_agent",
                "success" if execution_result.success else f"failed: {execution_result.error_message}",
            )

            if not execution_result.success:
                execution_error = execution_result.error_message
                event_log.append("revision_requested", "single_agent", f"Execution failed: {execution_error}")
                continue

            validation_result, metrics = verifier.verify(execution_result.analysis_result, dataset_dir, criteria)
            state.validation_results.append(validation_result)
            final_metrics = metrics
            event_log.append("verification_run", "verifier", validation_result.message, passed=validation_result.passed)

            if validation_result.passed:
                passed = True
                break
            execution_error = validation_result.message
            event_log.append("revision_requested", "verifier", "Verification did not pass; requesting revision.")
    except ExperimentCancelled:
        state.status = "cancelled"
        event_log.append("experiment_cancelled", "orchestrator", "Experiment cancelled.")
        return OrchestratorResult(state, event_log, final_metrics)
    except ExperimentTimedOut as exc:
        state.status = "timed_out"
        event_log.append("experiment_timed_out", "orchestrator", str(exc))
        return OrchestratorResult(state, event_log, final_metrics)

    state.status = "completed" if passed else "failed"
    state.runtime_seconds = budget.elapsed_seconds
    event_log.append(
        "experiment_completed" if passed else "experiment_failed",
        "orchestrator",
        f"status={state.status} after {state.iteration}/{max_iterations} iteration(s)",
    )
    return OrchestratorResult(state, event_log, final_metrics)


def run_multi_agent(
    experiment_id: str,
    dataset_dir: str | Path,
    research_objective: str,
    provider: LLMProvider,
    sandbox_root: str | Path,
    max_iterations: int = 3,
    timeout_seconds: float = 60.0,
    criteria: AcceptanceCriteria | None = None,
    cancellation_token: Optional[CancellationToken] = None,
) -> OrchestratorResult:
    """Principal Investigator -> Strategist -> Experiment Designer ->
    [Implementation -> Reviewer -> Execution] -> Verification, with bounded
    revision cycles whenever the Reviewer blocks, execution fails, or
    verification does not pass."""
    dataset_dir = Path(dataset_dir)
    criteria = criteria or AcceptanceCriteria()
    metadata = _load_dataset_metadata(dataset_dir)
    dataset_id = metadata["dataset_id"]

    event_log = EventLog()
    state = ExperimentState(
        experiment_id=experiment_id,
        dataset_id=dataset_id,
        research_objective=research_objective,
        workflow="multi_agent",
        max_iterations=max_iterations,
    )

    pi = PrincipalInvestigatorAgent(provider)
    strategist = StrategistAgent(provider)
    designer = ExperimentDesignerAgent(provider)
    impl = ImplementationAgent(provider)
    reviewer = ReviewerAgent(provider)
    verifier = VerificationAgent()
    sandbox = RestrictedSandbox(sandbox_root, timeout_seconds=min(15.0, timeout_seconds))

    plan, resp = pi.decompose_objective(research_objective, dataset_id)
    _track_usage(state, resp)
    state.agent_tasks = plan.tasks
    event_log.append(
        "task_assigned", "principal_investigator", plan.objective_interpretation,
        tasks=[t.model_dump() for t in plan.tasks],
    )

    proposal, resp = strategist.propose_approach(metadata, research_objective)
    _track_usage(state, resp)
    state.proposed_methods.append(proposal)
    event_log.append("method_proposed", "strategist", proposal.approach_summary)

    decision, resp = designer.resolve_matching_config(proposal, _total_error_rate(metadata))
    _track_usage(state, resp)
    state.matching_config = decision.matching_config
    state.disagreements.extend(decision.disagreements)
    event_log.append(
        "disagreement_resolved", "experiment_designer",
        f"matching_config={decision.matching_config.model_dump()}",
        disagreements=[d.model_dump() for d in decision.disagreements],
    )

    budget = IterationBudget(max_iterations, timeout_seconds)
    code_artifact = None
    review_issues: list[str] = []
    execution_error: Optional[str] = None
    final_metrics: Optional[EvaluationMetrics] = None
    passed = False

    try:
        while budget.has_iterations_remaining():
            if cancellation_token:
                cancellation_token.raise_if_cancelled()
            iteration = budget.start_iteration()
            state.iteration = iteration

            if code_artifact is None:
                code_artifact, resp = impl.write_analysis_code(state.matching_config, dataset_id)
            else:
                code_artifact, resp = impl.revise_code(
                    state.matching_config, dataset_id, review_issues, execution_error, code_artifact.version
                )
            _track_usage(state, resp)
            state.generated_code.append(code_artifact)
            event_log.append("code_written", "implementation", f"version {code_artifact.version}")

            findings, resp = reviewer.review(code_artifact)
            _track_usage(state, resp)
            state.review_findings.append(findings)
            event_log.append(
                "code_reviewed", "reviewer", f"approved={findings.approved}",
                issues=findings.issues, security_violations=findings.security_violations,
            )

            if findings.is_blocking:
                review_issues = findings.issues + findings.security_violations
                execution_error = None
                event_log.append("revision_requested", "reviewer", "Reviewer rejected code; requesting revision.")
                continue

            execution_result = impl.execute(code_artifact, dataset_dir, sandbox)
            state.execution_results.append(execution_result)
            event_log.append(
                "code_executed", "implementation",
                "success" if execution_result.success else f"failed: {execution_result.error_message}",
            )

            if not execution_result.success:
                review_issues = []
                execution_error = execution_result.error_message
                event_log.append("revision_requested", "implementation", f"Execution failed: {execution_error}")
                continue

            validation_result, metrics = verifier.verify(execution_result.analysis_result, dataset_dir, criteria)
            state.validation_results.append(validation_result)
            final_metrics = metrics
            event_log.append("verification_run", "verifier", validation_result.message, passed=validation_result.passed)

            if validation_result.passed:
                passed = True
                break
            review_issues = [validation_result.message]
            execution_error = None
            event_log.append("revision_requested", "verifier", "Verification did not pass; requesting revision.")
    except ExperimentCancelled:
        state.status = "cancelled"
        event_log.append("experiment_cancelled", "orchestrator", "Experiment cancelled.")
        return OrchestratorResult(state, event_log, final_metrics)
    except ExperimentTimedOut as exc:
        state.status = "timed_out"
        event_log.append("experiment_timed_out", "orchestrator", str(exc))
        return OrchestratorResult(state, event_log, final_metrics)

    pi_review, resp = pi.review_final_results(research_objective, passed, state.iteration, max_iterations)
    _track_usage(state, resp)
    state.final_review = pi_review
    state.status = pi_review.status
    state.runtime_seconds = budget.elapsed_seconds
    event_log.append(
        "experiment_completed" if passed else "experiment_failed", "principal_investigator", pi_review.summary
    )
    return OrchestratorResult(state, event_log, final_metrics)
