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
from helix.orchestration.orchestrator import (
    run_adaptive_agent,
    run_dual_agent,
    run_multi_agent,
    run_parallel_multi_agent,
    run_single_agent,
)
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


@pytest.fixture
def final_test_dataset_dir(tmp_path):
    """Same shape as `dataset_dir`, but partitioned `final_test` - used to
    prove the orchestrator's single-shot enforcement actually changes
    behavior, not just a status label."""
    config = DatasetGenerationConfig(
        dataset_id="orch_final_test_ds",
        seed=101,
        partition="final_test",
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


# --- dataset partitions -----------------------------------------------------


def test_final_test_partition_forces_single_shot_for_multi_agent(final_test_dataset_dir, tmp_path):
    """A final_test dataset must get exactly one attempt even when the
    Reviewer would otherwise force a revision - "no iterative feedback"
    means the cap changes real behavior, not just a status label."""
    provider = _AlwaysRejectProvider(MockProvider())
    result = run_multi_agent(
        "exp_final_test_multi", final_test_dataset_dir, "Detect target.", provider, tmp_path / "sandbox",
        max_iterations=5, timeout_seconds=30,
    )
    assert result.state.iteration == 1
    assert result.state.max_iterations == 1
    assert any(e.event_type == "ablation_applied" and e.payload.get("ablation") == "final_test_single_shot"
               for e in result.event_log)


def test_final_test_partition_forces_single_shot_for_single_agent(final_test_dataset_dir, tmp_path):
    result = run_single_agent(
        "exp_final_test_single", final_test_dataset_dir, "Detect target.", MockProvider(), tmp_path / "sandbox",
        max_iterations=5, timeout_seconds=30,
    )
    assert result.state.iteration <= 1
    assert result.state.max_iterations == 1


def test_dev_partitioned_dataset_keeps_the_full_iteration_budget(dataset_dir, tmp_path):
    """The default partition ("dev") must not be silently tightened - this
    is the existing, unchanged behavior every other test in this file
    already relies on."""
    provider = _AlwaysRejectProvider(MockProvider())
    result = run_multi_agent(
        "exp_dev_partition", dataset_dir, "Detect target.", provider, tmp_path / "sandbox",
        max_iterations=4, timeout_seconds=30,
    )
    assert result.state.max_iterations == 4
    assert result.state.iteration == 4


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


# --- dual-agent workflow ----------------------------------------------------


def test_dual_agent_runs_both_branches_and_records_a_comparison(dataset_dir, tmp_path):
    result = run_dual_agent(
        "exp_dual_basic", dataset_dir, "Detect target.", MockProvider(), tmp_path / "sandbox",
        max_iterations=3, timeout_seconds=30,
    )
    assert result.state.workflow == "dual_agent"
    comparison = result.state.dual_agent_comparison
    assert comparison is not None
    assert comparison.branch_a.branch == "a"
    assert comparison.branch_b.branch == "b"
    assert comparison.winner in ("a", "b", "tie")
    # Both branches' code and verification outcomes are recorded, not just the winner's.
    assert len(result.state.generated_code) >= 2
    assert len(result.state.validation_results) == 2


def test_dual_agent_branches_fail_independently(dataset_dir, tmp_path):
    """Branch b using a provider that makes execution always fail must not
    affect branch a's outcome (plain MockProvider) at all - proves the two
    branches don't share intermediate state, not just that both run."""
    base = MockProvider()

    class _AlwaysBreakExecution(LLMProvider):
        name = "always_break_execution"

        def complete(self, system, prompt, *, max_tokens=1024):
            response = base.complete(system, prompt, max_tokens=max_tokens)
            if "TASK_TYPE: implementation_write_code" in prompt or "TASK_TYPE: implementation_revise_code" in prompt:
                data = json.loads(response.text)
                data["code"] = "raise RuntimeError('branch b is always broken')"
                return ProviderResponse(
                    text=json.dumps(data), model=response.model,
                    input_tokens=response.input_tokens, output_tokens=response.output_tokens,
                )
            return response

    result = run_dual_agent(
        "exp_dual_independent", dataset_dir, "Detect target.", base, tmp_path / "sandbox",
        provider_b=_AlwaysBreakExecution(), max_iterations=3, timeout_seconds=30,
    )
    comparison = result.state.dual_agent_comparison
    # Branch a: plain MockProvider, same as every other test in this file - succeeds.
    assert comparison.branch_a.execution_result is not None
    assert comparison.branch_a.execution_result.success is True
    assert comparison.branch_a.validation_result is not None
    # Branch b: every execution attempt fails, so it never even reaches verification.
    assert comparison.branch_b.validation_result is None
    assert comparison.winner == "a"


def test_dual_agent_stops_at_token_budget(dataset_dir, tmp_path):
    result = run_dual_agent(
        "exp_dual_budget", dataset_dir, "Detect target.", MockProvider(), tmp_path / "sandbox",
        max_iterations=5, timeout_seconds=30, max_tokens=1,
    )
    assert result.state.status == "budget_exceeded"


def test_dual_agent_final_test_partition_forces_single_shot_per_branch(final_test_dataset_dir, tmp_path):
    provider = _AlwaysRejectProvider(MockProvider())
    result = run_dual_agent(
        "exp_dual_final_test", final_test_dataset_dir, "Detect target.", provider, tmp_path / "sandbox",
        max_iterations=5, timeout_seconds=30,
    )
    assert result.state.max_iterations == 1
    comparison = result.state.dual_agent_comparison
    assert comparison.branch_a.iteration_count == 1
    assert comparison.branch_b.iteration_count == 1


# --- Verifier role split: regression detection + per-cycle records --------


@pytest.fixture
def two_target_dataset_dir(tmp_path):
    """Two present targets of equal abundance, low error - lets a scripted
    provider control recall precisely (detect 0, 1, or 2 of them) by
    patching which target_ids the generated code marks as detected."""
    config = DatasetGenerationConfig(
        dataset_id="orch_two_target_ds",
        seed=102,
        sequence_length=40,
        read_depth=2000,
        targets=[
            TargetSpec(target_id="target_a", abundance=0.1, present=True),
            TargetSpec(target_id="target_b", abundance=0.1, present=True),
        ],
        n_background_sequences=40,
        error_model=ErrorModel(substitution_rate=0.0, insertion_rate=0.0, deletion_rate=0.0),
    )
    paths = write_dataset(config, tmp_path / "datasets")
    return paths["reads_fastq"].parent


class _ScriptedDetectionProvider(LLMProvider):
    """Wraps MockProvider and patches the `detected` logic of each
    generated code version by call order, to force a specific
    fail -> worse-fail (regression) -> pass sequence deterministically,
    without depending on real detection noise."""

    name = "scripted_detection"

    # One detection rule per write/revise call, keyed by call index (0-based).
    _RULES = [
        '"detected": (count >= MIN_COUNT) if target_id == "target_a" else False,',  # attempt 1: partial (recall 0.5)
        '"detected": False,',  # attempt 2: worse (recall 0.0) - a real regression
        '"detected": count >= MIN_COUNT,',  # attempt 3: unmodified, correct
    ]

    def __init__(self, base: LLMProvider):
        self._base = base
        self._code_calls = 0

    def complete(self, system: str, prompt: str, *, max_tokens: int = 1024) -> ProviderResponse:
        response = self._base.complete(system, prompt, max_tokens=max_tokens)
        if "TASK_TYPE: implementation_write_code" in prompt or "TASK_TYPE: implementation_revise_code" in prompt:
            rule = self._RULES[min(self._code_calls, len(self._RULES) - 1)]
            self._code_calls += 1
            data = json.loads(response.text)
            data["code"] = data["code"].replace('"detected": count >= MIN_COUNT,', rule)
            return ProviderResponse(
                text=json.dumps(data), model=response.model,
                input_tokens=response.input_tokens, output_tokens=response.output_tokens,
            )
        return response


def test_multi_agent_records_revision_cycles_and_detects_regression(two_target_dataset_dir, tmp_path):
    provider = _ScriptedDetectionProvider(MockProvider())
    result = run_multi_agent(
        "exp_regression", two_target_dataset_dir, "Detect both targets.", provider, tmp_path / "sandbox",
        max_iterations=4, timeout_seconds=30, enable_experiment_designer=False, enable_reviewer=False,
    )
    assert result.state.status == "completed"
    cycles = result.state.revision_cycles
    assert len(cycles) == 3

    first, second, third = cycles
    assert first.outcome == "verification_failed"
    assert first.validation_result.recall == pytest.approx(0.5)
    assert first.regression_detected is False  # no previous attempt to regress against

    assert second.outcome == "verification_failed"
    assert second.validation_result.recall == pytest.approx(0.0)
    assert second.regression_detected is True
    assert "decreased" in second.regression_explanation
    assert second.corrective_instructions  # the Verifier Agent issued guidance

    assert third.outcome == "verification_passed"
    assert third.validation_result.passed is True
    assert third.regression_detected is False


def test_multi_agent_verifier_agent_produces_different_guidance_than_raw_message(two_target_dataset_dir, tmp_path):
    """With the Verifier Agent enabled, review_issues fed into the next
    revision are its interpreted corrective instructions, not simply the
    raw ValidationResult.message - the actual behavior change Decision 1
    calls for, not just a new field that nothing reads."""
    provider = _ScriptedDetectionProvider(MockProvider())
    result = run_multi_agent(
        "exp_guidance", two_target_dataset_dir, "Detect both targets.", provider, tmp_path / "sandbox",
        max_iterations=4, timeout_seconds=30, enable_experiment_designer=False, enable_reviewer=False,
    )
    guidance_events = [e for e in result.event_log if e.event_type == "verifier_guidance"]
    assert len(guidance_events) == 2  # one per failed verification attempt
    assert all(e.actor == "verifier_agent" for e in guidance_events)
    first_cycle = result.state.revision_cycles[0]
    assert first_cycle.corrective_instructions != [first_cycle.validation_result.message]


def test_multi_agent_verifier_agent_disabled_falls_back_to_raw_message(two_target_dataset_dir, tmp_path):
    provider = _ScriptedDetectionProvider(MockProvider())
    result = run_multi_agent(
        "exp_no_verifier_agent", two_target_dataset_dir, "Detect both targets.", provider, tmp_path / "sandbox",
        max_iterations=4, timeout_seconds=30, enable_experiment_designer=False, enable_reviewer=False, enable_verifier_agent=False,
    )
    assert "no_verifier_agent" in result.state.ablation
    assert not any(e.event_type == "verifier_guidance" for e in result.event_log)
    first_cycle = result.state.revision_cycles[0]
    assert first_cycle.corrective_instructions == [first_cycle.validation_result.message]


# --- Adaptive coordination (Experiment E) -----------------------------------


@pytest.fixture
def difficult_dataset_dir(tmp_path):
    """Deliberately scores in the difficult/ambiguous tier: high combined
    error rate with indels present, sparse read depth relative to a large
    diverse background, heavy duplication."""
    config = DatasetGenerationConfig(
        dataset_id="orch_difficult_ds",
        seed=103,
        sequence_length=40,
        read_depth=80,
        targets=[TargetSpec(target_id="t1", abundance=0.1, present=True)],
        n_background_sequences=200,
        background_diversity="high",
        error_model=ErrorModel(substitution_rate=0.08, insertion_rate=0.02, deletion_rate=0.02),
        duplicate_rate=0.3,
    )
    paths = write_dataset(config, tmp_path / "datasets")
    return paths["reads_fastq"].parent


def test_adaptive_agent_easy_dataset_starts_and_stays_single_agent(dataset_dir, tmp_path):
    result = run_adaptive_agent(
        "exp_adaptive_easy", dataset_dir, "Detect target.", MockProvider(), tmp_path / "sandbox",
        max_iterations=3, timeout_seconds=30,
    )
    decision = result.state.coordination_decision
    assert decision is not None
    assert decision.difficulty_tier in ("easy", "moderate")
    assert decision.initial_agent_count == 1
    assert decision.final_agent_count == 1
    assert decision.escalations == []
    assert result.state.dual_agent_comparison is None
    assert result.state.status == "completed"
    # The single-agent phase keeps the Phase 3 role split's richness.
    assert result.state.revision_cycles


def test_adaptive_agent_difficult_dataset_starts_with_two_agents(difficult_dataset_dir, tmp_path):
    result = run_adaptive_agent(
        "exp_adaptive_difficult", difficult_dataset_dir, "Detect target.", MockProvider(), tmp_path / "sandbox",
        max_iterations=4, timeout_seconds=30,
    )
    decision = result.state.coordination_decision
    assert decision.difficulty_tier in ("difficult", "ambiguous")
    assert decision.initial_agent_count == 2
    assert decision.final_agent_count == 2
    assert result.state.dual_agent_comparison is not None


class _ForceFirstAttemptMiss(LLMProvider):
    """Patches the first implementation_write_code call so every target is
    reported undetected - forces f1=0.0 on iteration 1, deterministically
    crossing the adaptive policy's far-failure escalation threshold."""

    name = "force_first_attempt_miss"

    def __init__(self, base: LLMProvider):
        self._base = base
        self._patched = False

    def complete(self, system: str, prompt: str, *, max_tokens: int = 1024) -> ProviderResponse:
        response = self._base.complete(system, prompt, max_tokens=max_tokens)
        if "TASK_TYPE: implementation_write_code" in prompt and not self._patched:
            self._patched = True
            data = json.loads(response.text)
            data["code"] = data["code"].replace('"detected": count >= MIN_COUNT,', '"detected": False,')
            return ProviderResponse(
                text=json.dumps(data), model=response.model,
                input_tokens=response.input_tokens, output_tokens=response.output_tokens,
            )
        return response


def test_adaptive_agent_escalates_on_far_verification_failure(dataset_dir, tmp_path):
    provider = _ForceFirstAttemptMiss(MockProvider())
    result = run_adaptive_agent(
        "exp_adaptive_escalate", dataset_dir, "Detect target.", provider, tmp_path / "sandbox",
        max_iterations=3, timeout_seconds=30, enable_reviewer=False, enable_experiment_designer=False,
    )
    decision = result.state.coordination_decision
    assert decision.initial_agent_count == 1
    assert decision.final_agent_count == 2
    assert len(decision.escalations) == 1
    assert decision.escalations[0].trigger == "far_verification_failure"
    assert result.state.dual_agent_comparison is not None
    assert result.state.status == "completed"


def test_adaptive_agent_escalates_on_execution_failure(dataset_dir, tmp_path):
    base = MockProvider()

    class _AlwaysBreakFirstVersion(LLMProvider):
        name = "always_break_first_version"

        def __init__(self):
            self._patched = False

        def complete(self, system, prompt, *, max_tokens=1024):
            response = base.complete(system, prompt, max_tokens=max_tokens)
            if "TASK_TYPE: implementation_write_code" in prompt and not self._patched:
                self._patched = True
                data = json.loads(response.text)
                data["code"] = "raise RuntimeError('deliberately broken for test')"
                return ProviderResponse(
                    text=json.dumps(data), model=response.model,
                    input_tokens=response.input_tokens, output_tokens=response.output_tokens,
                )
            return response

    result = run_adaptive_agent(
        "exp_adaptive_exec_escalate", dataset_dir, "Detect target.", _AlwaysBreakFirstVersion(), tmp_path / "sandbox",
        max_iterations=3, timeout_seconds=30, enable_reviewer=False, enable_experiment_designer=False,
    )
    decision = result.state.coordination_decision
    assert decision.final_agent_count == 2
    assert any(e.trigger == "ambiguous_execution_failure" for e in decision.escalations)


def test_adaptive_agent_only_escalates_once(dataset_dir, tmp_path):
    """A second far failure (or execution failure) after a second agent is
    already running must not spawn a third."""
    provider = _ForceFirstAttemptMiss(MockProvider())
    result = run_adaptive_agent(
        "exp_adaptive_single_escalation", dataset_dir, "Detect target.", provider, tmp_path / "sandbox",
        max_iterations=3, timeout_seconds=30, enable_reviewer=False, enable_experiment_designer=False,
    )
    assert len(result.state.coordination_decision.escalations) <= 1
    assert result.state.coordination_decision.final_agent_count <= 2


def test_adaptive_agent_stops_at_token_budget(dataset_dir, tmp_path):
    result = run_adaptive_agent(
        "exp_adaptive_budget", dataset_dir, "Detect target.", MockProvider(), tmp_path / "sandbox",
        max_iterations=3, timeout_seconds=30, max_tokens=1,
    )
    assert result.state.status == "budget_exceeded"


def test_adaptive_agent_times_out(dataset_dir, tmp_path):
    result = run_adaptive_agent(
        "exp_adaptive_timeout", dataset_dir, "Detect target.", MockProvider(), tmp_path / "sandbox",
        max_iterations=3, timeout_seconds=0.0,
    )
    assert result.state.status == "timed_out"


def test_adaptive_agent_two_agents_never_credited_without_cost_accounting(difficult_dataset_dir, tmp_path):
    """Design doc: 'two agents shouldn't automatically be considered
    better just because they produce a better score' - the record must
    carry enough to check cost against benefit, not just the winner."""
    result = run_adaptive_agent(
        "exp_adaptive_cost", difficult_dataset_dir, "Detect target.", MockProvider(), tmp_path / "sandbox",
        max_iterations=4, timeout_seconds=30,
    )
    assert result.state.token_usage["input_tokens"] > 0
    assert result.state.coordination_decision.final_agent_count == 2
    comparison = result.state.dual_agent_comparison
    assert comparison.branch_a.estimated_cost_usd is not None
    assert comparison.branch_b.estimated_cost_usd is not None


def test_adaptive_agent_final_test_partition_caps_iterations(tmp_path):
    config = DatasetGenerationConfig(
        dataset_id="orch_adaptive_final_test", seed=104, partition="final_test",
        sequence_length=40, read_depth=1500,
        targets=[TargetSpec(target_id="t1", abundance=0.1, present=True)],
        n_background_sequences=40, error_model=ErrorModel(substitution_rate=0.005),
    )
    paths = write_dataset(config, tmp_path / "datasets")
    result = run_adaptive_agent(
        "exp_adaptive_final_test", paths["reads_fastq"].parent, "Detect target.", MockProvider(), tmp_path / "sandbox",
        max_iterations=5, timeout_seconds=30,
    )
    assert result.state.max_iterations == 1
