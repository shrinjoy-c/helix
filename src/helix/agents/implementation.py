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

# A plain-English field-by-field description of the file the generated
# *code* must write - not the schema of this agent's own JSON reply (that's
# CodeArtifact, which call_structured already embeds). This is deliberately
# NOT a JSON/code-fenced example: an earlier version embedded one, and a
# real model (qwen3:4b via Ollama) echoed that example straight back as its
# own reply (a CodeArtifact validation error: the literal result.json
# content, not an object with a `code` field) - it read as "the JSON to
# return" despite an explicit disclaimer sentence, apparently because a
# second, more concrete JSON blob out-competed the abstract CodeArtifact
# schema for salience. A prose description can't be mistaken for a
# ready-to-copy reply the same way. See docs/agent_design.md for the
# earlier, narrower bug this patch replaced (wrong field names entirely -
# `variant_id` instead of `target_id` - from giving no contract at all).
_RESULT_JSON_CONTRACT = (
    "result.json must be a single JSON object with exactly these top-level fields: "
    "dataset_id (string), pipeline_name (string), predictions (a list with one entry per "
    "reference sequence in reference_panel.fasta, each entry an object with target_id matching "
    "that sequence's id, matched_read_count (integer), matched_molecule_count (integer), "
    "estimated_frequency (number between 0 and 1), and detected (boolean)), "
    "total_reads_processed (integer), total_molecules_after_dedup (integer), "
    "runtime_seconds (number), and errors (a list of strings, empty if there were none). "
    "Do not rename, omit, or add fields, and do not nest predictions under any other key."
)

_SYSTEM = (
    "You are the Implementation agent in a computational biology research team. You write a "
    "self-contained Python script that reads reads.fastq and reference_panel.fasta from the "
    "current directory and writes result.json to the current directory. "
    f"{_RESULT_JSON_CONTRACT} "
    "Respond with only JSON matching the schema given below for this request - that schema "
    "describes your reply (the generated code), not result.json, which your code writes "
    "separately when it runs."
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
