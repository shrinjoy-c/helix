"""Structured messages exchanged between agents and the orchestrator.

Every agent-to-agent handoff is one of these typed models - never a raw
string or an unstructured dict. This is what makes agent contributions
independently measurable (e.g. "how many issues did the Reviewer raise",
"how many disagreements did the Experiment Designer resolve") instead of
being buried in free-form chat transcripts.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from helix.config.models import AnalysisResult, MatchingConfig

AgentRole = Literal[
    "principal_investigator",
    "strategist",
    "implementation",
    "reviewer",
    "experiment_designer",
    "verifier",
]

TaskStatus = Literal["pending", "in_progress", "completed", "failed"]


class AgentTask(BaseModel):
    task_id: str
    role: AgentRole
    description: str
    status: TaskStatus = "pending"


class ResearchPlan(BaseModel):
    tasks: list[AgentTask]
    objective_interpretation: str


class MethodProposal(BaseModel):
    approach_summary: str
    recommended_max_mismatches: int = Field(ge=0)
    recommended_collapse_duplicates: bool
    recommended_min_count: int = Field(ge=1)
    assumptions: list[str] = Field(default_factory=list)
    qc_checks: list[str] = Field(default_factory=list)


class CodeArtifact(BaseModel):
    code: str
    language: str = "python"
    description: str = ""
    version: int = 1


class ReviewFindings(BaseModel):
    approved: bool
    issues: list[str] = Field(default_factory=list)
    suggested_tests: list[str] = Field(default_factory=list)
    security_violations: list[str] = Field(default_factory=list)

    @property
    def is_blocking(self) -> bool:
        return bool(self.security_violations) or not self.approved


class Disagreement(BaseModel):
    topic: str
    position_a: str
    position_b: str
    resolution: str
    rationale: str


class MatchingDecision(BaseModel):
    matching_config: MatchingConfig
    disagreements: list[Disagreement] = Field(default_factory=list)


class ExecutionResult(BaseModel):
    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    timed_out: bool = False
    runtime_seconds: float = 0.0
    analysis_result: Optional[AnalysisResult] = None
    error_message: Optional[str] = None


class ValidationResult(BaseModel):
    """Redacted verification feedback safe to place in shared state.

    Deliberately excludes per-target true counts/frequencies - only
    aggregate pass/fail and scores, so agents revising their approach
    cannot reconstruct the hidden ground truth from iteration feedback.
    """

    passed: bool
    precision: float
    recall: float
    f1: float
    false_positive_rate: float
    false_negative_rate: float
    message: str


class PIFinalReview(BaseModel):
    status: Literal["completed", "needs_revision", "failed"]
    summary: str
