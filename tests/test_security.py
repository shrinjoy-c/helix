"""Tests for the restricted execution sandbox: timeouts, environment
scrubbing, import blocking, and ground-truth unreachability. See
docs/security.md for the honest scope of what this sandbox does and does
not guarantee.
"""

from helix.execution.sandbox import RestrictedSandbox
from helix.execution.runner import execute_analysis_code
from helix.orchestration.messages import CodeArtifact


def test_timeout_kills_a_runaway_script(tmp_path):
    sandbox = RestrictedSandbox(tmp_path, timeout_seconds=1.0)
    result = sandbox.run_python("while True:\n    pass\n")
    assert result.timed_out is True
    assert result.runtime_seconds < 5.0


def test_sandbox_scrubs_secrets_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-super-secret-value")
    monkeypatch.setenv("OPENAI_API_KEY", "also-secret")
    sandbox = RestrictedSandbox(tmp_path, timeout_seconds=5.0)
    result = sandbox.run_python(
        "import os, json\n"
        "from pathlib import Path\n"
        "Path('result.json').write_text(json.dumps(dict(os.environ)))\n"
    )
    assert result.exit_code == 0
    env_seen_by_code = result.output_files.get("result.json", "{}")
    assert "sk-super-secret-value" not in env_seen_by_code
    assert "also-secret" not in env_seen_by_code
    assert "ANTHROPIC_API_KEY" not in env_seen_by_code


def test_sandbox_blocks_socket_import(tmp_path):
    sandbox = RestrictedSandbox(tmp_path, timeout_seconds=5.0)
    result = sandbox.run_python("import socket\n")
    assert result.exit_code != 0
    assert "blocked in the HELIX execution sandbox" in result.stderr


def test_sandbox_blocks_subprocess_import(tmp_path):
    sandbox = RestrictedSandbox(tmp_path, timeout_seconds=5.0)
    result = sandbox.run_python("import subprocess\nsubprocess.run(['ls'])\n")
    assert result.exit_code != 0
    assert "blocked in the HELIX execution sandbox" in result.stderr


def test_sandbox_does_not_block_unrelated_stdlib_imports(tmp_path):
    """Regression test: the import guard must match on the exact blocked
    module (or its submodules), not on any module sharing a package
    prefix with a blocked entry - `urllib.parse` (used internally by
    `pathlib`) must still work even though `urllib.request` is blocked."""
    sandbox = RestrictedSandbox(tmp_path, timeout_seconds=5.0)
    result = sandbox.run_python(
        "from pathlib import Path\nimport json\nPath('result.json').write_text(json.dumps({'ok': True}))\n"
    )
    assert result.exit_code == 0
    assert result.output_files.get("result.json") == '{"ok": true}'


def test_sandbox_cleans_up_workdir_by_default(tmp_path):
    sandbox = RestrictedSandbox(tmp_path, timeout_seconds=5.0)
    sandbox.run_python("print('hello')")
    leftover = list(tmp_path.glob("helix_exec_*"))
    assert leftover == []


def test_sandbox_keep_artifacts_preserves_workdir(tmp_path):
    sandbox = RestrictedSandbox(tmp_path, timeout_seconds=5.0, keep_artifacts=True)
    sandbox.run_python("print('hello')")
    leftover = list(tmp_path.glob("helix_exec_*"))
    assert len(leftover) == 1


def test_ground_truth_is_not_reachable_from_the_sandbox(tmp_path):
    """The dataset's `_ground_truth/` directory must never be copied into
    the sandbox workdir, even if code tries relative or absolute paths
    used elsewhere in the dataset layout."""
    from helix.biology.simulation import write_dataset
    from helix.config.models import DatasetGenerationConfig, TargetSpec

    config = DatasetGenerationConfig(
        dataset_id="sec_ds", seed=1, sequence_length=20, read_depth=50,
        targets=[TargetSpec(target_id="t1", abundance=0.1, present=True)],
        n_background_sequences=5,
    )
    paths = write_dataset(config, tmp_path / "datasets")
    dataset_dir = paths["reads_fastq"].parent

    code_artifact = CodeArtifact(
        code=(
            "import json\n"
            "from pathlib import Path\n"
            "found = Path('_ground_truth/ground_truth.json').exists()\n"
            "Path('result.json').write_text(json.dumps({'dataset_id': 'sec_ds', 'pipeline_name': 'p', "
            "'predictions': [], 'total_reads_processed': 0, 'total_molecules_after_dedup': 0, "
            "'runtime_seconds': 0.0, 'errors': [str(found)]}))\n"
        ),
        version=1,
    )
    sandbox = RestrictedSandbox(tmp_path / "sandbox", timeout_seconds=5.0)
    result = execute_analysis_code(code_artifact, dataset_dir, sandbox)
    assert result.success is True
    assert result.analysis_result.errors == ["False"]


def test_resource_limits_do_not_break_normal_execution(tmp_path):
    sandbox = RestrictedSandbox(tmp_path, timeout_seconds=5.0, cpu_seconds=5, memory_mb=256)
    result = sandbox.run_python(
        "import json\nfrom pathlib import Path\n"
        "data = [i * i for i in range(10000)]\n"
        "Path('result.json').write_text(json.dumps({'sum': sum(data)}))\n"
    )
    assert result.exit_code == 0
    assert '"sum"' in result.output_files.get("result.json", "")
