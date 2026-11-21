"""Implementation Agent: writes Python analysis code, executes it through
the restricted sandbox, and revises it in response to reviewer feedback
or execution failures. This is the only agent that produces and runs code.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from helix.agents.base import Agent, call_structured
from helix.config.models import MatchingConfig
from helix.execution.runner import execute_analysis_code
from helix.execution.sandbox import RestrictedSandbox
from helix.orchestration.messages import CodeArtifact, ExecutionResult
from helix.providers.base import ProviderResponse

_SYSTEM = (
    "You are the Implementation agent in a computational biology research team. You write a "
    "self-contained Python script that reads reads.fastq and reference_panel.fasta from the "
    "current directory and writes result.json matching the AnalysisResult schema. Respond with "
    "only JSON containing the code."
)


class WriteCodeRequest(BaseModel):
    matching_config: dict
    dataset_id: str


class ReviseCodeRequest(BaseModel):
    matching_config: dict
    dataset_id: str
    review_issues: list[str]
    execution_error: str | None = None
    previous_version: int = 1


class ImplementationAgent(Agent):
    role = "implementation"

    def write_analysis_code(
        self, matching_config: MatchingConfig, dataset_id: str
    ) -> tuple[CodeArtifact, ProviderResponse]:
        request = WriteCodeRequest(matching_config=matching_config.model_dump(), dataset_id=dataset_id)
        return call_structured(self.provider, _SYSTEM, "implementation_write_code", request, CodeArtifact)

    def revise_code(
        self,
        matching_config: MatchingConfig,
        dataset_id: str,
        review_issues: list[str],
        execution_error: str | None,
        previous_version: int,
    ) -> tuple[CodeArtifact, ProviderResponse]:
        request = ReviseCodeRequest(
            matching_config=matching_config.model_dump(),
            dataset_id=dataset_id,
            review_issues=review_issues,
            execution_error=execution_error,
            previous_version=previous_version,
        )
        return call_structured(self.provider, _SYSTEM, "implementation_revise_code", request, CodeArtifact)

    def execute(
        self, code_artifact: CodeArtifact, dataset_public_dir: str | Path, sandbox: RestrictedSandbox
    ) -> ExecutionResult:
        return execute_analysis_code(code_artifact, dataset_public_dir, sandbox)
