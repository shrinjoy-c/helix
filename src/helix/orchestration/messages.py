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


class CodingBranchResult(BaseModel):
    """One independent coding agent's complete outcome within a dual-agent
    run - everything needed to compare approaches without re-running
    anything. `"a"`/`"b"` label which independently-instantiated
    `ImplementationAgent` produced it, nothing more - there is no implied
    ranking in the label itself."""

    branch: Literal["a", "b"]
    final_code: CodeArtifact
    iteration_count: int
    review_findings: list[ReviewFindings] = Field(default_factory=list)
    execution_result: Optional[ExecutionResult] = None
    validation_result: Optional[ValidationResult] = None
    token_usage: dict[str, int] = Field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})
    estimated_cost_usd: float = 0.0


class VerifierGuidanceRequest(BaseModel):
    """What the Verifier Agent is handed - exactly the redacted signal
    already available to the revision loop (the same `ValidationResult`
    fields plus a deterministic regression flag), never ground truth."""

    validation_message: str
    passed: bool
    precision: float
    recall: float
    f1: float
    regression_detected: bool
    regression_explanation: str = ""
    review_issues: list[str] = Field(default_factory=list)
    iteration: int


class VerifierGuidance(BaseModel):
    """The Verifier Agent's interpretation of one failed/regressed
    validation attempt: concrete, actionable corrective instructions for
    the next `revise_code()` call, plus a short human-readable summary."""

    corrective_instructions: list[str] = Field(default_factory=list)
    summary: str = ""


RevisionOutcome = Literal["reviewer_rejected", "execution_failed", "verification_failed", "verification_passed"]


class RevisionCycleRecord(BaseModel):
    """One complete attempt inside a bounded revision loop: what was tried,
    what happened, what feedback was given for the next attempt, and
    whether this attempt was a regression relative to the previous one.
    The structured record the Phase 1 gap analysis found missing from the
    event log's free-text entries alone (see `helix.evaluation_engine`'s
    `get_validation_results`)."""

    cycle: int
    code_version: int
    outcome: RevisionOutcome
    review_issues: list[str] = Field(default_factory=list)
    execution_error: Optional[str] = None
    validation_result: Optional[ValidationResult] = None
    regression_detected: bool = False
    regression_explanation: str = ""
    corrective_instructions: list[str] = Field(default_factory=list)
    verifier_summary: str = ""


class DualAgentComparison(BaseModel):
    """Produced once both independent coding agents have submitted their
    implementations. Neither branch is ever shown the other's code,
    reasoning, or intermediate output before this point exists - this is
    strictly a post-hoc comparison of two already-finished, independent
    attempts, never a channel either agent could use to influence the
    other."""

    branch_a: CodingBranchResult
    branch_b: CodingBranchResult
    both_detected_same_targets: Optional[bool] = None
    abundance_estimate_delta: Optional[float] = Field(
        default=None, description="|branch_a - branch_b| mean estimated_frequency across shared target_ids."
    )
    winner: Optional[Literal["a", "b", "tie"]] = None
    winner_rationale: str = ""
