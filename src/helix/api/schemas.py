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

ProviderName = Literal["mock", "anthropic", "ollama", "openai_compatible"]


class ProviderSelection(BaseModel):
    """Which provider to use and how to reach it.

    `api_key` is never accepted here - a raw secret has no business
    travelling through an HTTP request body or a frontend form. Point
    `api_key_env` at an environment variable the *server* process already
    has set (e.g. "GROQ_API_KEY", "TOGETHER_API_KEY"); `openai_compatible`
    reads it server-side via `os.environ`, exactly like `anthropic` already
    does for `ANTHROPIC_API_KEY`. Nothing here needs a key at all for
    `mock` or `ollama` (local).
    """

    provider: ProviderName = "mock"
    model: Optional[str] = Field(
        default=None, description="Defaults to each provider's own default model if omitted."
    )
    base_url: Optional[str] = Field(
        default=None,
        description="ollama: defaults to http://localhost:11434. openai_compatible: defaults to "
        "https://api.openai.com/v1 - point this at Groq/Together/a local vLLM server/etc. instead.",
    )
    api_key_env: Optional[str] = Field(
        default=None, description="openai_compatible only: env var holding the API key (default OPENAI_API_KEY)."
    )


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
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key_env: Optional[str] = None
    max_iterations: int = Field(3, ge=1)
    timeout_seconds: float = Field(60.0, gt=0)
    max_branches: int = Field(3, ge=1)
    criteria: AcceptanceCriteriaRequest = Field(default_factory=AcceptanceCriteriaRequest)

    def provider_selection(self) -> ProviderSelection:
        return ProviderSelection(
            provider=self.provider, model=self.model, base_url=self.base_url, api_key_env=self.api_key_env
        )


class BenchmarkSweepRequest(BaseModel):
    dataset_ids: list[str]
    pipeline_names: list[str]
    n_repeats: int = Field(1, ge=1)
    provider: ProviderName = "mock"
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key_env: Optional[str] = None
    max_iterations: int = Field(3, ge=1)
    timeout_seconds: float = Field(60.0, gt=0)
    max_branches: int = Field(3, ge=1)
    criteria: AcceptanceCriteriaRequest = Field(default_factory=AcceptanceCriteriaRequest)

    def provider_selection(self) -> ProviderSelection:
        return ProviderSelection(
            provider=self.provider, model=self.model, base_url=self.base_url, api_key_env=self.api_key_env
        )

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
