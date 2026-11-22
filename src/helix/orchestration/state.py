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
    Disagreement,
    ExecutionResult,
    MethodProposal,
    PIFinalReview,
    ReviewFindings,
    ValidationResult,
)

ExperimentStatus = Literal[
    "pending", "running", "completed", "needs_revision", "failed", "cancelled", "timed_out"
]


class ExperimentState(BaseModel):
    experiment_id: str
    dataset_id: str
    research_objective: str
    workflow: Literal["single_agent", "multi_agent", "parallel_multi_agent"]
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
    final_review: Optional[PIFinalReview] = None

    iteration: int = 0
    max_iterations: int = 3
    token_usage: dict[str, int] = Field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})
    estimated_cost_usd: float = 0.0
    runtime_seconds: float = 0.0

    status: ExperimentStatus = "pending"
