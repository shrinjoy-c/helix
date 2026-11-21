"""Bridges the Implementation Agent's generated code to the restricted
sandbox, and parses its declared output contract back into typed results.

Contract generated code must follow: read `reads.fastq` and
`reference_panel.fasta` from the current working directory, and write a
single JSON object to `result.json` matching `helix.config.models.AnalysisResult`.
Anything else (stdout chatter, a missing/invalid result.json, a non-zero
exit code, a timeout) is treated as a failed execution to be diagnosed and
revised - never silently ignored.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from helix.config.models import AnalysisResult
from helix.execution.sandbox import RestrictedSandbox
from helix.orchestration.messages import CodeArtifact, ExecutionResult


def execute_analysis_code(
    code_artifact: CodeArtifact,
    dataset_public_dir: str | Path,
    sandbox: RestrictedSandbox,
) -> ExecutionResult:
    dataset_public_dir = Path(dataset_public_dir)
    input_files = {
        "reads.fastq": dataset_public_dir / "reads.fastq",
        "reference_panel.fasta": dataset_public_dir / "reference_panel.fasta",
    }
    for dest, src in input_files.items():
        if not src.exists():
            return ExecutionResult(
                success=False,
                error_message=f"required public input file missing: {src}",
            )

    result = sandbox.run_python(code_artifact.code, input_files=input_files)

    if result.timed_out:
        return ExecutionResult(
            success=False,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            timed_out=True,
            runtime_seconds=result.runtime_seconds,
            error_message="execution exceeded the sandbox timeout",
        )

    if result.exit_code != 0:
        return ExecutionResult(
            success=False,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            runtime_seconds=result.runtime_seconds,
            error_message=f"analysis script exited with code {result.exit_code}",
        )

    raw_result = result.output_files.get("result.json")
    if raw_result is None:
        return ExecutionResult(
            success=False,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            runtime_seconds=result.runtime_seconds,
            error_message="analysis script did not write result.json",
        )

    try:
        payload = json.loads(raw_result)
        analysis_result = AnalysisResult.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        return ExecutionResult(
            success=False,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            runtime_seconds=result.runtime_seconds,
            error_message=f"result.json did not match the AnalysisResult schema: {exc}",
        )

    return ExecutionResult(
        success=True,
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.exit_code,
        runtime_seconds=result.runtime_seconds,
        analysis_result=analysis_result,
    )
