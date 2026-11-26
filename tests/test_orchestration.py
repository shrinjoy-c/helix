"""Integration tests for the orchestration layer: single-agent and
multi-agent end-to-end runs, iteration bounds, cancellation, timeouts,
and recovery from both reviewer rejection and execution failure.

Several tests use a scripted `FakeProvider` rather than `MockProvider` so
that failure/recovery sequences are deterministic and don't depend on
`MockProvider`'s realistic-but-probabilistic code templates.
"""

from __future__ import annotations

import json

import pytest

from helix.biology.simulation import write_dataset
from helix.config.models import DatasetGenerationConfig, ErrorModel, TargetSpec
from helix.orchestration.orchestrator import run_multi_agent, run_parallel_multi_agent, run_single_agent
from helix.orchestration.scheduler import CancellationToken
from helix.providers.base import LLMProvider, ProviderResponse
from helix.providers.mock_provider import MockProvider


@pytest.fixture
def dataset_dir(tmp_path):
    config = DatasetGenerationConfig(
        dataset_id="orch_ds",
        seed=100,
        sequence_length=40,
        read_depth=1500,
        targets=[TargetSpec(target_id="t1", abundance=0.1, present=True)],
        n_background_sequences=40,
        error_model=ErrorModel(substitution_rate=0.005, insertion_rate=0.0, deletion_rate=0.0),
    )
    paths = write_dataset(config, tmp_path / "datasets")
    return paths["reads_fastq"].parent


def test_multi_agent_end_to_end_completes(dataset_dir, tmp_path):
    provider = MockProvider()
    result = run_multi_agent(
        "exp_multi", dataset_dir, "Detect the target and estimate its abundance.",
        provider, tmp_path / "sandbox", max_iterations=3, timeout_seconds=30,
    )
    assert result.state.status == "completed"
    assert result.metrics is not None
    assert result.metrics.precision >= 0.8
    # All five specialist roles produced at least one logged event.
    actors = {e.actor for e in result.event_log}
    assert {"principal_investigator", "strategist", "experiment_designer", "implementation", "reviewer", "verifier"} <= actors


def test_single_agent_end_to_end_completes(dataset_dir, tmp_path):
    provider = MockProvider()
    result = run_single_agent(
        "exp_single", dataset_dir, "Detect the target and estimate its abundance.",
        provider, tmp_path / "sandbox", max_iterations=3, timeout_seconds=30,
    )
    assert result.state.status == "completed"
    assert result.state.workflow == "single_agent"
    # Single-agent workflow has no Reviewer or Experiment Designer involvement.
    actors = {e.actor for e in result.event_log}
    assert "reviewer" not in actors
    assert "experiment_designer" not in actors


def test_multi_agent_reviewer_rejection_triggers_revision(dataset_dir, tmp_path):
    provider = MockProvider()
    result = run_multi_agent(
        "exp_revision", dataset_dir, "Detect the target.", provider, tmp_path / "sandbox",
        max_iterations=3, timeout_seconds=30,
    )
    event_types = [e.event_type for e in result.event_log]
    assert "revision_requested" in event_types
    assert len(result.state.generated_code) >= 2
    assert result.state.generated_code[0].version == 1
    assert result.state.review_findings[0].approved is False


def test_event_log_is_sequential_and_immutable(dataset_dir, tmp_path):
    provider = MockProvider()
    result = run_multi_agent(
        "exp_seq", dataset_dir, "Detect target.", provider, tmp_path / "sandbox",
        max_iterations=3, timeout_seconds=30,
    )
    entries = result.event_log.to_list()
    assert [e.sequence for e in entries] == list(range(len(entries)))

    # to_list() returns a copy: mutating it must not affect the live log.
    entries.pop()
    assert len(entries) == len(result.event_log) - 1

    # EventLog exposes no public API to remove or rewrite past entries.
    public_methods = {name for name in dir(result.event_log) if not name.startswith("_")}
    assert public_methods == {"append", "to_list"}


class _AlwaysRejectProvider(LLMProvider):
    """Forces the Reviewer to reject every code version, to test that the
    iteration budget is strictly enforced rather than looping forever."""

    name = "always_reject"

    def __init__(self, base: LLMProvider):
        self._base = base

    def complete(self, system: str, prompt: str, *, max_tokens: int = 1024) -> ProviderResponse:
        response = self._base.complete(system, prompt, max_tokens=max_tokens)
        if "TASK_TYPE: reviewer_review" in prompt:
            data = json.loads(response.text)
            data["approved"] = False
            data["issues"] = ["forced rejection for test"]
            return ProviderResponse(
                text=json.dumps(data), model=response.model,
                input_tokens=response.input_tokens, output_tokens=response.output_tokens,
            )
        return response


def test_multi_agent_stops_at_max_iterations_when_never_approved(dataset_dir, tmp_path):
    provider = _AlwaysRejectProvider(MockProvider())
    result = run_multi_agent(
        "exp_never_approved", dataset_dir, "Detect target.", provider, tmp_path / "sandbox",
        max_iterations=2, timeout_seconds=30,
    )
    assert result.state.iteration == 2
    assert result.state.status in ("failed", "needs_revision")
    assert all(f.approved is False for f in result.state.review_findings)
    # Never executed because the reviewer never approved.
    assert result.state.execution_results == []


def test_single_agent_recovers_from_execution_failure(dataset_dir, tmp_path):
    base = MockProvider()

    class _BrokenThenFixed(LLMProvider):
        name = "broken_then_fixed"

        def __init__(self):
            self._write_calls = 0

        def complete(self, system, prompt, *, max_tokens=1024):
            response = base.complete(system, prompt, max_tokens=max_tokens)
            if "TASK_TYPE: implementation_write_code" in prompt or "TASK_TYPE: implementation_revise_code" in prompt:
                self._write_calls += 1
                if self._write_calls == 1:
                    data = json.loads(response.text)
                    data["code"] = "raise RuntimeError('deliberately broken for test')"
                    return ProviderResponse(
                        text=json.dumps(data), model=response.model,
                        input_tokens=response.input_tokens, output_tokens=response.output_tokens,
                    )
            return response

    result = run_single_agent(
        "exp_exec_recovery", dataset_dir, "Detect target.", _BrokenThenFixed(), tmp_path / "sandbox",
        max_iterations=3, timeout_seconds=30,
    )
    assert result.state.execution_results[0].success is False
    assert "deliberately broken" in result.state.execution_results[0].stderr or result.state.execution_results[0].error_message
    assert result.state.status == "completed"
    assert result.state.execution_results[-1].success is True


def test_cancellation_stops_the_run(dataset_dir, tmp_path):
    token = CancellationToken()
    token.cancel()
    result = run_single_agent(
        "exp_cancelled", dataset_dir, "Detect target.", MockProvider(), tmp_path / "sandbox",
        max_iterations=3, timeout_seconds=30, cancellation_token=token,
    )
    assert result.state.status == "cancelled"


def test_timeout_stops_the_run(dataset_dir, tmp_path):
    result = run_single_agent(
        "exp_timeout", dataset_dir, "Detect target.", MockProvider(), tmp_path / "sandbox",
        max_iterations=10, timeout_seconds=0.0,
    )
    assert result.state.status == "timed_out"


def test_single_agent_stops_at_token_budget(dataset_dir, tmp_path):
    result = run_single_agent(
        "exp_budget_single", dataset_dir, "Detect target.", MockProvider(), tmp_path / "sandbox",
        max_iterations=10, timeout_seconds=30, max_tokens=1,
    )
    assert result.state.status == "budget_exceeded"
    # Stopped after the first iteration that crossed the budget, not all 10.
    assert result.state.iteration < 10


class _WithFixedCost(LLMProvider):
    """MockProvider always reports $0 (it's free/offline by design) - this
    wraps it with a nonzero per-call cost so max_cost_usd has something
    real to enforce against."""

    name = "with_fixed_cost"

    def __init__(self, base: LLMProvider, cost_per_call: float):
        self._base = base
        self._cost_per_call = cost_per_call

    def complete(self, system: str, prompt: str, *, max_tokens: int = 1024) -> ProviderResponse:
        response = self._base.complete(system, prompt, max_tokens=max_tokens)
        return ProviderResponse(
            text=response.text, model=response.model,
            input_tokens=response.input_tokens, output_tokens=response.output_tokens,
            estimated_cost_usd=self._cost_per_call,
        )


def test_multi_agent_stops_at_cost_budget(dataset_dir, tmp_path):
    provider = _WithFixedCost(MockProvider(), cost_per_call=0.05)
    result = run_multi_agent(
        "exp_budget_multi", dataset_dir, "Detect target.", provider, tmp_path / "sandbox",
        max_iterations=10, timeout_seconds=30, max_cost_usd=0.08,
    )
    assert result.state.status == "budget_exceeded"
    assert result.state.iteration < 10


def test_parallel_multi_agent_stops_at_token_budget(dataset_dir, tmp_path):
    result = run_parallel_multi_agent(
        "exp_budget_parallel", dataset_dir, "Detect target.", MockProvider(), tmp_path / "sandbox",
        timeout_seconds=30, max_tokens=1,
    )
    assert result.state.status == "budget_exceeded"


def test_unset_token_budget_never_stops_the_run(dataset_dir, tmp_path):
    """max_tokens/max_cost_usd default to None (unbounded) - usage is still
    tracked, but a run with real, nonzero usage must not be stopped."""
    result = run_multi_agent(
        "exp_no_budget_set", dataset_dir, "Detect target.", MockProvider(), tmp_path / "sandbox",
        max_iterations=3, timeout_seconds=30,
    )
    assert result.state.token_usage["input_tokens"] > 0
    assert result.state.status != "budget_exceeded"


def test_token_usage_and_cost_are_tracked(dataset_dir, tmp_path):
    result = run_multi_agent(
        "exp_tokens", dataset_dir, "Detect target.", MockProvider(), tmp_path / "sandbox",
        max_iterations=3, timeout_seconds=30,
    )
    assert result.state.token_usage["input_tokens"] > 0
    assert result.state.token_usage["output_tokens"] > 0


def test_experiment_state_is_reproducible_from_same_dataset(dataset_dir, tmp_path):
    """Re-running against the same (deterministic) dataset with MockProvider
    yields the same final status and metrics - the orchestration layer adds
    no hidden randomness."""
    r1 = run_multi_agent("exp_a", dataset_dir, "Detect target.", MockProvider(), tmp_path / "sb1", max_iterations=3, timeout_seconds=30)
    r2 = run_multi_agent("exp_b", dataset_dir, "Detect target.", MockProvider(), tmp_path / "sb2", max_iterations=3, timeout_seconds=30)
    assert r1.state.status == r2.state.status
    assert r1.metrics.precision == r2.metrics.precision
    assert r1.metrics.recall == r2.metrics.recall


# --- ablations --------------------------------------------------------


def test_ablation_no_reviewer_skips_the_reviewer_entirely(dataset_dir, tmp_path):
    result = run_multi_agent(
        "exp_no_reviewer", dataset_dir, "Detect target.", MockProvider(), tmp_path / "sandbox",
        max_iterations=3, timeout_seconds=30, enable_reviewer=False,
    )
    assert result.state.ablation == "no_reviewer"
    assert result.state.review_findings == []
    assert not any(e.actor == "reviewer" for e in result.event_log)
    # It should still complete, in fewer iterations than the full system needs
    # (no reviewer rejection cycle to wait out).
    assert result.state.iteration == 1


def test_ablation_no_experiment_designer_skips_disagreement_resolution(dataset_dir, tmp_path):
    result = run_multi_agent(
        "exp_no_designer", dataset_dir, "Detect target.", MockProvider(), tmp_path / "sandbox",
        max_iterations=3, timeout_seconds=30, enable_experiment_designer=False,
    )
    assert result.state.ablation == "no_experiment_designer"
    assert result.state.disagreements == []
    assert not any(e.actor == "experiment_designer" and e.event_type == "disagreement_resolved" for e in result.event_log)
    proposal = result.state.proposed_methods[0]
    assert result.state.matching_config.max_mismatches == proposal.recommended_max_mismatches


def test_ablation_no_verification_loop_caps_at_one_iteration_and_can_fail(dataset_dir, tmp_path):
    result = run_multi_agent(
        "exp_no_loop", dataset_dir, "Detect target.", MockProvider(), tmp_path / "sandbox",
        max_iterations=5, timeout_seconds=30, enable_verification_loop=False,
    )
    assert result.state.ablation == "no_verification_loop"
    assert result.state.max_iterations == 1
    assert result.state.iteration == 1
    # On this dataset the Reviewer rejects the naive first draft, and with no
    # revision cycle available the run genuinely fails - a real, reproducible
    # demonstration of RQ3 (iterative verification improves recovery).
    assert result.state.status == "failed"
    assert result.state.execution_results == []


def test_combined_ablations_are_recorded_jointly(dataset_dir, tmp_path):
    result = run_multi_agent(
        "exp_combo", dataset_dir, "Detect target.", MockProvider(), tmp_path / "sandbox",
        max_iterations=3, timeout_seconds=30, enable_reviewer=False, enable_experiment_designer=False,
    )
    assert result.state.ablation == "no_experiment_designer+no_reviewer"


# --- parallel multi-agent ----------------------------------------------


def test_parallel_multi_agent_explores_multiple_distinct_candidates(dataset_dir, tmp_path):
    result = run_parallel_multi_agent(
        "exp_parallel", dataset_dir, "Detect target.", MockProvider(), tmp_path / "sandbox",
        max_branches=3, timeout_seconds=30,
    )
    assert result.state.workflow == "parallel_multi_agent"
    assert len(result.state.candidate_matching_configs) >= 2
    mismatches = {c.max_mismatches for c in result.state.candidate_matching_configs}
    assert len(mismatches) == len(result.state.candidate_matching_configs)  # all distinct
    branch_events = [e for e in result.event_log if e.event_type == "parallel_branch_explored"]
    assert len(branch_events) == len(result.state.candidate_matching_configs)


def test_parallel_multi_agent_records_every_branchs_code(dataset_dir, tmp_path):
    result = run_parallel_multi_agent(
        "exp_parallel2", dataset_dir, "Detect target.", MockProvider(), tmp_path / "sandbox",
        max_branches=3, timeout_seconds=30,
    )
    assert len(result.state.generated_code) == len(result.state.candidate_matching_configs)
    assert any(e.event_type == "parallel_branch_selected" for e in result.event_log)


def test_parallel_multi_agent_single_shot_branches_cannot_pass_review(dataset_dir, tmp_path):
    """Documented, reproducible finding (see docs/agent_design.md): since
    every branch gets exactly one shot and MockProvider's first-draft code
    never includes indel handling, every branch is rejected by the
    Reviewer regardless of which max_mismatches it explores - parallel
    breadth does not substitute for a revision cycle under this policy."""
    result = run_parallel_multi_agent(
        "exp_parallel3", dataset_dir, "Detect target.", MockProvider(), tmp_path / "sandbox",
        max_branches=3, timeout_seconds=30,
    )
    assert all(f.approved is False for f in result.state.review_findings)
    assert result.state.execution_results == []
    assert result.state.status == "failed"
