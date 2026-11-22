"""Request/response models for the HELIX API - deliberately separate
from `helix.config.models` so the wire contract can evolve independently
of the internal data model, and so dataset responses can be constructed
to guarantee the redaction (no abundance/presence) at the type level.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from helix.benchmarking.baselines import BenchmarkBudget
from helix.benchmarking.metrics import BenchmarkGroupSummary

DEFAULT_RESEARCH_OBJECTIVE = (
    "Determine whether the reference target is present in this sample and, if so, estimate "
    "its abundance, while minimizing false positive detections."
)

ProviderName = Literal["mock", "anthropic"]


class DatasetSummary(BaseModel):
    """Redacted: only what the generator writes to the public directory."""

    dataset_id: str
    description: str
    metadata: dict[str, Any]


class PipelineInfo(BaseModel):
    name: str
    kind: str
    enable_experiment_designer: bool
    enable_reviewer: bool
    enable_verification_loop: bool


class AcceptanceCriteriaRequest(BaseModel):
    min_precision: float = 0.8
    min_recall: float = 0.8
    max_false_positive_rate: float = 0.2


class ExperimentLaunchRequest(BaseModel):
    dataset_id: str
    pipeline_name: str
    research_objective: str = DEFAULT_RESEARCH_OBJECTIVE
    provider: ProviderName = "mock"
    max_iterations: int = Field(3, ge=1)
    timeout_seconds: float = Field(60.0, gt=0)
    max_branches: int = Field(3, ge=1)
    criteria: AcceptanceCriteriaRequest = Field(default_factory=AcceptanceCriteriaRequest)


class BenchmarkSweepRequest(BaseModel):
    dataset_ids: list[str]
    pipeline_names: list[str]
    n_repeats: int = Field(1, ge=1)
    provider: ProviderName = "mock"
    max_iterations: int = Field(3, ge=1)
    timeout_seconds: float = Field(60.0, gt=0)
    max_branches: int = Field(3, ge=1)
    criteria: AcceptanceCriteriaRequest = Field(default_factory=AcceptanceCriteriaRequest)

    def to_budget(self) -> BenchmarkBudget:
        return BenchmarkBudget(
            max_iterations=self.max_iterations, timeout_seconds=self.timeout_seconds,
            max_branches=self.max_branches,
        )


class BenchmarkSweepResult(BaseModel):
    job_id: str
    experiment_ids: list[str]
    summaries: list[BenchmarkGroupSummary]


class ErrorResponse(BaseModel):
    detail: str
