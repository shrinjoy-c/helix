"""Drives a single-agent or multi-agent experiment run against one dataset.

Both workflows share: the restricted execution sandbox, the independent
Verification Agent, bounded iteration/timeout budgets, and complete event
logging. They differ in which agent roles actually participate - that
difference *is* the single-agent vs. multi-agent baseline the benchmark
compares (see docs/agent_design.md).
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
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
from helix.orchestration.scheduler import (
    CancellationToken,
    ExperimentBudgetExceeded,
    ExperimentCancelled,
    ExperimentTimedOut,
    IterationBudget,
    TokenBudget,
)
from helix.orchestration.state import ExperimentState
from helix.providers.base import LLMProvider, ProviderResponse


def _total_error_rate(dataset_metadata: dict) -> float:
    error_model = dataset_metadata.get("error_model", {})
    return (
        error_model.get("substitution_rate", 0.0)
        + error_model.get("insertion_rate", 0.0)
        + error_model.get("deletion_rate", 0.0)
    )


def _track_usage(
    state: ExperimentState, response: ProviderResponse, token_budget: Optional[TokenBudget] = None
) -> None:
    state.token_usage["input_tokens"] += response.input_tokens
    state.token_usage["output_tokens"] += response.output_tokens
    state.estimated_cost_usd += response.estimated_cost_usd
    if token_budget is not None:
        total_tokens = state.token_usage["input_tokens"] + state.token_usage["output_tokens"]
        token_budget.check(total_tokens, state.estimated_cost_usd)


def _load_dataset_metadata(dataset_dir: Path) -> dict:
    return json.loads((dataset_dir / "dataset_metadata.json").read_text())


def _partition_capped_max_iterations(metadata: dict, requested: int, event_log: EventLog) -> int:
    """`final_test`-partitioned datasets get exactly one attempt, regardless
    of what the caller asked for - "no iterative feedback" means no
    revision based on this run's own result is possible, not just that the
    revision prompt's feedback text gets softened. Datasets written before
    this field existed have no `"partition"` key at all; those are treated
    as `"dev"` (unbounded, today's existing behavior), not silently
    tightened.
    """
    partition = metadata.get("partition", "dev")
    if partition == "final_test" and requested != 1:
        event_log.append(
            "ablation_applied", "orchestrator",
            f"final_test partition: max_iterations forced from {requested} to 1 - "
            "no revision based on this run's own result is permitted.",
            ablation="final_test_single_shot",
        )
        return 1
    return requested


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
    max_tokens: Optional[int] = None,
    max_cost_usd: Optional[float] = None,
) -> OrchestratorResult:
    """One generalist agent proposes an approach, writes code, executes it,
    and is scored by the (always-independent) Verification Agent - with no
    separate Reviewer, Experiment Designer, or PI role in the loop.

    `max_tokens`/`max_cost_usd` (both default None, meaning unbounded) are
    checked after every agent call made inside the revision loop - a run
    that crosses either limit mid-revision is stopped immediately rather
    than allowed to finish its current iteration."""
    dataset_dir = Path(dataset_dir)
    criteria = criteria or AcceptanceCriteria()
    metadata = _load_dataset_metadata(dataset_dir)
    dataset_id = metadata["dataset_id"]

    event_log = EventLog()
    max_iterations = _partition_capped_max_iterations(metadata, max_iterations, event_log)
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
    token_budget = TokenBudget(max_tokens, max_cost_usd)
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
            _track_usage(state, resp, token_budget)
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
    except ExperimentBudgetExceeded as exc:
        state.status = "budget_exceeded"
        state.runtime_seconds = budget.elapsed_seconds
        event_log.append("experiment_budget_exceeded", "orchestrator", str(exc))
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
    enable_experiment_designer: bool = True,
    enable_reviewer: bool = True,
    enable_verification_loop: bool = True,
    max_tokens: Optional[int] = None,
    max_cost_usd: Optional[float] = None,
) -> OrchestratorResult:
    """Principal Investigator -> Strategist -> Experiment Designer ->
    [Implementation -> Reviewer -> Execution] -> Verification, with bounded
    revision cycles whenever the Reviewer blocks, execution fails, or
    verification does not pass.

    The three `enable_*` flags are the system's ablation mechanism: each
    independently removes exactly one coordination mechanism from the
    full pipeline, so the benchmark can isolate which role/mechanism
    contributes what (see docs/agent_design.md and RQ1/RQ2/RQ3/H5).

    - `enable_experiment_designer=False`: skip disagreement resolution;
      use the Strategist proposal's parameters directly, same as
      `run_single_agent` does.
    - `enable_reviewer=False`: skip the independent code review gate
      entirely - code always proceeds straight to execution. The
      sandbox's own security guard is unaffected; this only removes the
      *research* review role, not infrastructure security.
    - `enable_verification_loop=False`: cap the run at exactly one
      iteration regardless of `max_iterations` - whatever the first
      attempt produces (reviewer rejection, execution failure, or a
      failed verification) is final, with no revision cycle.

    `max_tokens`/`max_cost_usd` (both default None, meaning unbounded) are
    checked after every agent call made inside the revision loop - a run
    that crosses either limit mid-revision is stopped immediately rather
    than allowed to finish its current iteration.
    """
    dataset_dir = Path(dataset_dir)
    criteria = criteria or AcceptanceCriteria()
    metadata = _load_dataset_metadata(dataset_dir)
    dataset_id = metadata["dataset_id"]

    disabled = []
    if not enable_experiment_designer:
        disabled.append("no_experiment_designer")
    if not enable_reviewer:
        disabled.append("no_reviewer")
    if not enable_verification_loop:
        disabled.append("no_verification_loop")
    ablation = "+".join(disabled) if disabled else None
    effective_max_iterations = 1 if not enable_verification_loop else max_iterations

    event_log = EventLog()
    effective_max_iterations = _partition_capped_max_iterations(metadata, effective_max_iterations, event_log)
    state = ExperimentState(
        experiment_id=experiment_id,
        dataset_id=dataset_id,
        research_objective=research_objective,
        workflow="multi_agent",
        ablation=ablation,
        max_iterations=effective_max_iterations,
    )
    if ablation:
        event_log.append("ablation_applied", "orchestrator", f"Ablation active: {ablation}", ablation=ablation)

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

    if enable_experiment_designer:
        decision, resp = designer.resolve_matching_config(proposal, _total_error_rate(metadata))
        _track_usage(state, resp)
        state.matching_config = decision.matching_config
        state.disagreements.extend(decision.disagreements)
        event_log.append(
            "disagreement_resolved", "experiment_designer",
            f"matching_config={decision.matching_config.model_dump()}",
            disagreements=[d.model_dump() for d in decision.disagreements],
        )
    else:
        state.matching_config = MatchingConfig(
            max_mismatches=proposal.recommended_max_mismatches,
            collapse_duplicates=proposal.recommended_collapse_duplicates,
            min_count=proposal.recommended_min_count,
        )
        event_log.append(
            "ablation_applied", "experiment_designer",
            "Experiment Designer disabled - using the Strategist proposal's parameters directly, "
            "with no disagreement reconciliation.",
            ablation="no_experiment_designer",
        )

    budget = IterationBudget(effective_max_iterations, timeout_seconds)
    token_budget = TokenBudget(max_tokens, max_cost_usd)
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
            _track_usage(state, resp, token_budget)
            state.generated_code.append(code_artifact)
            event_log.append("code_written", "implementation", f"version {code_artifact.version}")

            if enable_reviewer:
                findings, resp = reviewer.review(code_artifact)
                _track_usage(state, resp, token_budget)
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
    except ExperimentBudgetExceeded as exc:
        state.status = "budget_exceeded"
        state.runtime_seconds = budget.elapsed_seconds
        event_log.append("experiment_budget_exceeded", "orchestrator", str(exc))
        return OrchestratorResult(state, event_log, final_metrics)

    pi_review, resp = pi.review_final_results(research_objective, passed, state.iteration, effective_max_iterations)
    _track_usage(state, resp)
    state.final_review = pi_review
    state.status = pi_review.status
    state.runtime_seconds = budget.elapsed_seconds
    event_log.append(
        "experiment_completed" if passed else "experiment_failed", "principal_investigator", pi_review.summary
    )
    return OrchestratorResult(state, event_log, final_metrics)


def run_parallel_multi_agent(
    experiment_id: str,
    dataset_dir: str | Path,
    research_objective: str,
    provider: LLMProvider,
    sandbox_root: str | Path,
    max_branches: int = 3,
    timeout_seconds: float = 60.0,
    criteria: AcceptanceCriteria | None = None,
    cancellation_token: Optional[CancellationToken] = None,
    max_tokens: Optional[int] = None,
    max_cost_usd: Optional[float] = None,
) -> OrchestratorResult:
    """Parallel multi-agent workflow: instead of one Experiment Designer
    resolving a single matching_config and iterating on it sequentially,
    explore several distinct `max_mismatches` candidates concurrently -
    each in its own real sandboxed subprocess (genuine OS-level
    parallelism, not relabeled sequential work) - and select the
    best-scoring branch. One round only; no cross-round revision (see
    docs/agent_design.md for why this is scoped as v1).

    `max_tokens`/`max_cost_usd` (both default None, meaning unbounded) are
    checked as each branch's usage is recorded; once either is crossed,
    waiting on the remaining branches' results is abandoned and the
    experiment is reported as `budget_exceeded` - but branches already
    running in their own sandboxed subprocess are not forcibly killed, the
    same caveat that already applies to cancellation here (see
    docs/agent_design.md's "Known limitations").
    """
    dataset_dir = Path(dataset_dir)
    criteria = criteria or AcceptanceCriteria()
    metadata = _load_dataset_metadata(dataset_dir)
    dataset_id = metadata["dataset_id"]

    event_log = EventLog()
    state = ExperimentState(
        experiment_id=experiment_id,
        dataset_id=dataset_id,
        research_objective=research_objective,
        workflow="parallel_multi_agent",
        max_iterations=1,
    )
    lock = threading.Lock()

    pi = PrincipalInvestigatorAgent(provider)
    strategist = StrategistAgent(provider)
    sandbox = RestrictedSandbox(sandbox_root, timeout_seconds=min(15.0, timeout_seconds))

    budget = IterationBudget(1, timeout_seconds)
    token_budget = TokenBudget(max_tokens, max_cost_usd)
    try:
        if cancellation_token:
            cancellation_token.raise_if_cancelled()
        budget.start_iteration()
    except ExperimentCancelled:
        state.status = "cancelled"
        event_log.append("experiment_cancelled", "orchestrator", "Experiment cancelled.")
        return OrchestratorResult(state, event_log, None)
    except ExperimentTimedOut as exc:
        state.status = "timed_out"
        event_log.append("experiment_timed_out", "orchestrator", str(exc))
        return OrchestratorResult(state, event_log, None)
    state.iteration = 1

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

    recommended = proposal.recommended_max_mismatches
    candidates = sorted({max(0, recommended - 1), recommended, recommended + 1})[:max_branches]
    candidate_configs = [
        MatchingConfig(
            max_mismatches=m,
            collapse_duplicates=proposal.recommended_collapse_duplicates,
            min_count=proposal.recommended_min_count,
        )
        for m in candidates
    ]
    state.candidate_matching_configs = candidate_configs

    def _run_branch(matching_config: MatchingConfig) -> dict:
        branch_impl = ImplementationAgent(provider)
        branch_reviewer = ReviewerAgent(provider)
        branch_verifier = VerificationAgent()

        code_artifact, resp = branch_impl.write_analysis_code(matching_config, dataset_id)
        with lock:
            _track_usage(state, resp, token_budget)

        findings, resp = branch_reviewer.review(code_artifact)
        with lock:
            _track_usage(state, resp, token_budget)

        execution_result = None
        validation_result = None
        metrics = None
        if not findings.is_blocking:
            execution_result = branch_impl.execute(code_artifact, dataset_dir, sandbox)
            if execution_result.success:
                validation_result, metrics = branch_verifier.verify(
                    execution_result.analysis_result, dataset_dir, criteria
                )

        return {
            "matching_config": matching_config,
            "code_artifact": code_artifact,
            "findings": findings,
            "execution_result": execution_result,
            "validation_result": validation_result,
            "metrics": metrics,
        }

    try:
        with ThreadPoolExecutor(max_workers=len(candidate_configs)) as executor:
            branches = list(executor.map(_run_branch, candidate_configs))
    except ExperimentBudgetExceeded as exc:
        # One or more branches may still be running in their own sandboxed
        # subprocess at this point - not forcibly killed, see the
        # docstring's caveat. We stop waiting on results and report
        # promptly rather than block until every branch finishes.
        state.status = "budget_exceeded"
        state.runtime_seconds = budget.elapsed_seconds
        event_log.append("experiment_budget_exceeded", "orchestrator", str(exc))
        return OrchestratorResult(state, event_log, None)

    for branch in branches:
        state.generated_code.append(branch["code_artifact"])
        state.review_findings.append(branch["findings"])
        if branch["execution_result"] is not None:
            state.execution_results.append(branch["execution_result"])
        if branch["validation_result"] is not None:
            state.validation_results.append(branch["validation_result"])
        event_log.append(
            "parallel_branch_explored", "implementation",
            f"max_mismatches={branch['matching_config'].max_mismatches}: "
            f"{'passed' if branch['validation_result'] and branch['validation_result'].passed else 'did not pass'}",
            matching_config=branch["matching_config"].model_dump(),
        )

    def _rank_key(branch: dict) -> tuple[bool, float, float]:
        v = branch["validation_result"]
        if v is None:
            return (False, -1.0, -1.0)
        return (v.passed, v.f1, v.precision)

    best = max(branches, key=_rank_key)
    state.matching_config = best["matching_config"]
    passed = bool(best["validation_result"] and best["validation_result"].passed)
    final_metrics = best["metrics"]
    event_log.append(
        "parallel_branch_selected", "experiment_designer",
        f"Selected max_mismatches={best['matching_config'].max_mismatches} out of "
        f"{len(branches)} candidates explored.",
        matching_config=best["matching_config"].model_dump(),
    )

    pi_review, resp = pi.review_final_results(research_objective, passed, 1, 1)
    _track_usage(state, resp)
    state.final_review = pi_review
    state.status = pi_review.status
    state.runtime_seconds = budget.elapsed_seconds
    event_log.append(
        "experiment_completed" if passed else "experiment_failed", "principal_investigator", pi_review.summary
    )
    return OrchestratorResult(state, event_log, final_metrics)
