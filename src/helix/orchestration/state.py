"""Shared experiment state.

Agents read and write through the orchestrator, never through shared
mutable globals or raw conversational history. Every agent is handed only
the slice of this state its role needs (see each agent's method
signature in `helix.agents`) - nothing here grants an agent access to
`helix.config.models.GroundTruth`; `ValidationResult` entries are the
redacted summary produced by the Verification Agent, not the full
`EvaluationMetrics`.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from helix.config.models import MatchingConfig
from helix.orchestration.messages import (
    AgentTask,
    CodeArtifact,
    CoordinationDecision,
    Disagreement,
    DualAgentComparison,
    ExecutionResult,
    MethodProposal,
    PIFinalReview,
    RevisionCycleRecord,
    ReviewFindings,
    ValidationResult,
)

ExperimentStatus = Literal[
    "pending", "running", "completed", "needs_revision", "failed", "cancelled", "timed_out",
    "budget_exceeded",
]


class ExperimentState(BaseModel):
    experiment_id: str
    dataset_id: str
    research_objective: str
    workflow: Literal["single_agent", "multi_agent", "parallel_multi_agent", "dual_agent", "adaptive_agent"]
    ablation: Optional[str] = Field(
        default=None,
        description="Which coordination mechanism was removed from a full multi_agent run, "
        "e.g. 'no_reviewer', 'no_experiment_designer', 'no_verification_loop'. None = full system.",
    )

    agent_tasks: list[AgentTask] = Field(default_factory=list)
    proposed_methods: list[MethodProposal] = Field(default_factory=list)
    generated_code: list[CodeArtifact] = Field(default_factory=list)
    review_findings: list[ReviewFindings] = Field(default_factory=list)
    disagreements: list[Disagreement] = Field(default_factory=list)
    matching_config: Optional[MatchingConfig] = None
    candidate_matching_configs: list[MatchingConfig] = Field(
        default_factory=list, description="parallel_multi_agent only: every branch explored."
    )

    execution_results: list[ExecutionResult] = Field(default_factory=list)
    validation_results: list[ValidationResult] = Field(default_factory=list)
    revision_cycles: list[RevisionCycleRecord] = Field(
        default_factory=list,
        description="run_multi_agent only so far - see docs/agent_design.md's Verifier role split.",
    )
    final_review: Optional[PIFinalReview] = None
    dual_agent_comparison: Optional[DualAgentComparison] = Field(
        default=None, description="dual_agent workflow only: both independent coding agents' outcomes compared."
    )
    coordination_decision: Optional[CoordinationDecision] = Field(
        default=None, description="adaptive_agent workflow only: the policy's difficulty score and escalation history."
    )

    iteration: int = 0
    max_iterations: int = 3
    token_usage: dict[str, int] = Field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})
    estimated_cost_usd: float = 0.0
    runtime_seconds: float = 0.0
    model_name: Optional[str] = Field(
        default=None, description="First ProviderResponse.model seen this run - see ExperimentRecord.model."
    )

    status: ExperimentStatus = "pending"
