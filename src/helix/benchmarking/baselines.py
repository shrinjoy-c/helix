"""The benchmark's core configurations (A-E from the project's own
design doc): deterministic baseline, single-agent, sequential multi-agent
(with and without the verification/revision loop), and parallel
multi-agent. Ablation configurations (F) live in `ablations.py` and
reuse `PipelineSpec`/`run_pipeline` from here.

Every configuration is invoked through the same `run_pipeline(spec, ...)`
call with the same `BenchmarkBudget`, so "comparable resource budgets"
(section 11 of the project spec) is a structural property of the
dispatcher, not something callers have to remember to enforce by hand.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from helix.agents.verifier import AcceptanceCriteria
from helix.biology.evaluation import evaluate, load_ground_truth
from helix.biology.matching import analyze_dataset
from helix.config.models import DatasetGenerationConfig, ExperimentRecord, MatchingConfig
from helix.orchestration.orchestrator import (
    run_adaptive_agent,
    run_dual_agent,
    run_multi_agent,
    run_parallel_multi_agent,
    run_single_agent,
)
from helix.providers.base import LLMProvider

PipelineKind = Literal[
    "deterministic", "single_agent", "multi_agent", "parallel_multi_agent", "dual_agent", "adaptive_agent",
]


class BenchmarkBudget(BaseModel):
    """Resource budget applied identically across every configuration in a sweep."""

    max_iterations: int = Field(3, ge=1)
    timeout_seconds: float = Field(60.0, gt=0)
    max_branches: int = Field(3, ge=1, description="parallel_multi_agent only")
    max_tokens: Optional[int] = Field(
        None, ge=1, description="Hard per-run cap, unbounded if unset - see docs/agent_design.md."
    )
    max_cost_usd: Optional[float] = Field(None, ge=0.0, description="Hard per-run cap, unbounded if unset.")


class PipelineSpec(BaseModel):
    """One benchmark configuration: what to run and which coordination
    mechanisms (if any) are disabled relative to the full system."""

    name: str
    kind: PipelineKind
    enable_experiment_designer: bool = True
    enable_reviewer: bool = True
    enable_verification_loop: bool = True
    enable_verifier_agent: bool = True
    deterministic_matching_config: Optional[MatchingConfig] = None


# --- The five non-ablation configurations (A-E) ---------------------------

DETERMINISTIC_BASELINE = PipelineSpec(
    name="deterministic_baseline",
    kind="deterministic",
    deterministic_matching_config=MatchingConfig(max_mismatches=1, collapse_duplicates=True, min_count=1),
)
SINGLE_AGENT = PipelineSpec(name="single_agent", kind="single_agent")
SEQUENTIAL_MULTI_AGENT = PipelineSpec(
    name="sequential_multi_agent", kind="multi_agent", enable_verification_loop=False
)
MULTI_AGENT_WITH_VERIFICATION = PipelineSpec(
    name="multi_agent_with_verification", kind="multi_agent", enable_verification_loop=True
)
PARALLEL_MULTI_AGENT = PipelineSpec(name="parallel_multi_agent", kind="parallel_multi_agent")

# Experiments C and D from the extension plan: two independent coding agents,
# always both - fixed, not adaptively gated, deliberately. Only a fixed agent
# count isolates "does a second independent implementation help" from
# "did the adaptive policy choose to use one," which is a different question
# (Experiment E). enable_reviewer and enable_verification_loop toggle
# together here, matching the literal "no verifier" / "with reviewer and
# verifier" wording of C/D - unlike the single-agent configs above, where
# only enable_verification_loop varies and the reviewer stays on regardless.
DUAL_AGENT_NO_VERIFICATION = PipelineSpec(
    name="dual_agent_no_verification", kind="dual_agent",
    enable_reviewer=False, enable_verification_loop=False,
)
DUAL_AGENT_WITH_VERIFICATION = PipelineSpec(
    name="dual_agent_with_verification", kind="dual_agent",
    enable_reviewer=True, enable_verification_loop=True,
)

# Experiment E from the extension plan: HELIX decides 1 vs. 2 coding agents
# itself (helix.orchestration.adaptive), instead of the count being fixed
# by the spec the way every configuration above is. Compared against B
# (single + verification) and D (dual + verification) - the two fixed
# configurations Experiment E's adaptive choice is actually weighed
# against - never credited as "better" on accuracy alone, see
# run_adaptive_agent's docstring and ExperimentRecord.coordination_decision.
ADAPTIVE_COORDINATION = PipelineSpec(name="adaptive_coordination", kind="adaptive_agent")

# --- Explicit Experiment A-E aliases (extension plan's framework) --------
#
# A Phase 1 gap-analysis finding: Experiment A ("single agent, no
# verification") had no clean, unambiguous existing config to point at -
# `SINGLE_AGENT` above always runs the revision loop. These five names are
# the extension plan's "Experiment A-E" framework made literal and
# unambiguous, specifically for sweeps that compare agent count and
# verification as a controlled 2x2(+1) grid - do not confuse with the
# "(A-E)" comment four configurations above, which labels this file's
# *original*, differently-ordered A-H lettering (see
# docs/benchmark_methodology.md's explicit disambiguation).
EXPERIMENT_A_SINGLE_NO_VERIFICATION = PipelineSpec(
    name="experiment_a_single_no_verification", kind="single_agent", enable_verification_loop=False,
)
EXPERIMENT_B_SINGLE_WITH_VERIFICATION = PipelineSpec(
    name="experiment_b_single_with_verification", kind="single_agent", enable_verification_loop=True,
)
EXPERIMENT_C_DUAL_NO_VERIFICATION = DUAL_AGENT_NO_VERIFICATION.model_copy(
    update={"name": "experiment_c_dual_no_verification"}
)
EXPERIMENT_D_DUAL_WITH_VERIFICATION = DUAL_AGENT_WITH_VERIFICATION.model_copy(
    update={"name": "experiment_d_dual_with_verification"}
)
EXPERIMENT_E_ADAPTIVE = ADAPTIVE_COORDINATION.model_copy(update={"name": "experiment_e_adaptive"})

EXPERIMENT_A_TO_E: list[PipelineSpec] = [
    EXPERIMENT_A_SINGLE_NO_VERIFICATION,
    EXPERIMENT_B_SINGLE_WITH_VERIFICATION,
    EXPERIMENT_C_DUAL_NO_VERIFICATION,
    EXPERIMENT_D_DUAL_WITH_VERIFICATION,
    EXPERIMENT_E_ADAPTIVE,
]
"""The extension plan's Decision 3 grid, run as five independently-named
configurations through the same `run_pipeline` dispatcher as everything
else - no parallel comparison system. See
`scripts/run_adaptive_coordination_benchmark.py` for the worked example
and `docs/benchmark_methodology.md`'s "Experiment A-E" section."""

BASELINE_CONFIGURATIONS: list[PipelineSpec] = [
    DETERMINISTIC_BASELINE,
    SINGLE_AGENT,
    SEQUENTIAL_MULTI_AGENT,
    MULTI_AGENT_WITH_VERIFICATION,
    PARALLEL_MULTI_AGENT,
    DUAL_AGENT_NO_VERIFICATION,
    DUAL_AGENT_WITH_VERIFICATION,
    ADAPTIVE_COORDINATION,
    *EXPERIMENT_A_TO_E,
]


def run_pipeline(
    spec: PipelineSpec,
    experiment_id: str,
    dataset_dir: str | Path,
    dataset_config: DatasetGenerationConfig,
    provider: LLMProvider,
    sandbox_root: str | Path,
    budget: BenchmarkBudget,
    criteria: AcceptanceCriteria | None = None,
    research_objective: str = "Determine whether the reference target is present and, if so, "
    "estimate its abundance, while minimizing false positive detections.",
) -> ExperimentRecord:
    """Run one configuration against one dataset and return a complete,
    persistable ExperimentRecord - the uniform unit every other part of
    the benchmarking package operates on."""
    dataset_dir = Path(dataset_dir)
    criteria = criteria or AcceptanceCriteria()
    start = time.monotonic()

    if spec.kind == "deterministic":
        matching_config = spec.deterministic_matching_config or MatchingConfig()
        result = analyze_dataset(dataset_dir, matching_config)
        metrics = evaluate(result, load_ground_truth(dataset_dir))
        runtime = time.monotonic() - start
        return ExperimentRecord(
            experiment_id=experiment_id,
            dataset_id=dataset_config.dataset_id,
            pipeline_name=spec.name,
            status="completed" if not result.errors else "failed",
            dataset_config=dataset_config,
            matching_config=matching_config,
            analysis_result=result,
            evaluation_metrics=metrics,
            runtime_seconds=runtime,
            created_at=datetime.now(timezone.utc).isoformat(),
            iteration_count=1,
        )

    if spec.kind == "single_agent":
        outcome = run_single_agent(
            experiment_id, dataset_dir, research_objective, provider, sandbox_root,
            max_iterations=budget.max_iterations, timeout_seconds=budget.timeout_seconds, criteria=criteria,
            enable_verification_loop=spec.enable_verification_loop,
            max_tokens=budget.max_tokens, max_cost_usd=budget.max_cost_usd,
        )
    elif spec.kind == "multi_agent":
        outcome = run_multi_agent(
            experiment_id, dataset_dir, research_objective, provider, sandbox_root,
            max_iterations=budget.max_iterations, timeout_seconds=budget.timeout_seconds, criteria=criteria,
            enable_experiment_designer=spec.enable_experiment_designer,
            enable_reviewer=spec.enable_reviewer,
            enable_verification_loop=spec.enable_verification_loop,
            enable_verifier_agent=spec.enable_verifier_agent,
            max_tokens=budget.max_tokens, max_cost_usd=budget.max_cost_usd,
        )
    elif spec.kind == "parallel_multi_agent":
        outcome = run_parallel_multi_agent(
            experiment_id, dataset_dir, research_objective, provider, sandbox_root,
            max_branches=budget.max_branches, timeout_seconds=budget.timeout_seconds, criteria=criteria,
            max_tokens=budget.max_tokens, max_cost_usd=budget.max_cost_usd,
        )
    elif spec.kind == "dual_agent":
        outcome = run_dual_agent(
            experiment_id, dataset_dir, research_objective, provider, sandbox_root,
            max_iterations=budget.max_iterations, timeout_seconds=budget.timeout_seconds, criteria=criteria,
            enable_experiment_designer=spec.enable_experiment_designer,
            enable_reviewer=spec.enable_reviewer,
            enable_verification_loop=spec.enable_verification_loop,
            max_tokens=budget.max_tokens, max_cost_usd=budget.max_cost_usd,
        )
    elif spec.kind == "adaptive_agent":
        outcome = run_adaptive_agent(
            experiment_id, dataset_dir, research_objective, provider, sandbox_root,
            max_iterations=budget.max_iterations, timeout_seconds=budget.timeout_seconds, criteria=criteria,
            enable_experiment_designer=spec.enable_experiment_designer,
            enable_reviewer=spec.enable_reviewer,
            max_tokens=budget.max_tokens, max_cost_usd=budget.max_cost_usd,
        )
    else:  # pragma: no cover - exhaustive Literal
        raise ValueError(f"unknown pipeline kind: {spec.kind}")

    comparison = outcome.state.dual_agent_comparison
    if comparison is not None:
        # execution_results[-1] would always be branch b's (appended last
        # regardless of which branch actually won) - the winning branch's
        # own result is the one that belongs in the record's singular
        # analysis_result/evaluation_metrics fields.
        winning_branch = comparison.branch_b if comparison.winner == "b" else comparison.branch_a
        analysis_result = winning_branch.execution_result.analysis_result if winning_branch.execution_result else None
    else:
        analysis_result = (
            outcome.state.execution_results[-1].analysis_result if outcome.state.execution_results else None
        )
    return ExperimentRecord(
        experiment_id=experiment_id,
        dataset_id=dataset_config.dataset_id,
        pipeline_name=spec.name,
        status=outcome.state.status,
        dataset_config=dataset_config,
        matching_config=outcome.state.matching_config,
        analysis_result=analysis_result,
        evaluation_metrics=outcome.metrics,
        runtime_seconds=outcome.state.runtime_seconds,
        created_at=datetime.now(timezone.utc).isoformat(),
        iteration_count=outcome.state.iteration,
        token_usage=outcome.state.token_usage,
        estimated_cost_usd=outcome.state.estimated_cost_usd,
        event_log=[e.model_dump() for e in outcome.event_log],
        ablation=outcome.state.ablation,
        dual_agent_comparison=comparison.model_dump() if comparison is not None else None,
        revision_cycles=[r.model_dump() for r in outcome.state.revision_cycles] or None,
        coordination_decision=(
            outcome.state.coordination_decision.model_dump() if outcome.state.coordination_decision else None
        ),
        provider=provider.name,
        model=outcome.state.model_name,
    )


def new_experiment_id(pipeline_name: str) -> str:
    return f"exp_{pipeline_name}_{uuid.uuid4().hex[:10]}"
