"""Unit tests for individual agents: message validation and each role's
distinct operation, using the deterministic MockProvider.
"""

import pytest
from pydantic import ValidationError

from helix.agents.base import AgentCallError, build_task_prompt, call_structured
from helix.agents.strategist import StrategistAgent
from helix.agents.experiment_designer import ExperimentDesignerAgent
from helix.agents.implementation import ImplementationAgent
from helix.agents.principal_investigator import PrincipalInvestigatorAgent
from helix.agents.reviewer import ReviewerAgent, static_security_scan
from helix.agents.verifier import AcceptanceCriteria, VerificationAgent, VerifierAgent
from helix.config.models import (
    AnalysisResult,
    GroundTruth,
    GroundTruthTarget,
    MatchingConfig,
    PredictedTarget,
)
from helix.orchestration.messages import CodeArtifact, MethodProposal, VerifierGuidanceRequest
from helix.providers.mock_provider import MockProvider


@pytest.fixture
def provider():
    return MockProvider()


@pytest.fixture
def low_error_metadata():
    return {
        "dataset_id": "unit_ds",
        "sequence_length": 45,
        "error_model": {"substitution_rate": 0.005, "insertion_rate": 0.0, "deletion_rate": 0.0},
        "duplicate_rate": 0.0,
    }


# --- message validation ---------------------------------------------------


def test_method_proposal_rejects_negative_mismatches():
    with pytest.raises(ValidationError):
        MethodProposal(
            approach_summary="x",
            recommended_max_mismatches=-1,
            recommended_collapse_duplicates=True,
            recommended_min_count=1,
        )


def test_method_proposal_rejects_zero_min_count():
    with pytest.raises(ValidationError):
        MethodProposal(
            approach_summary="x",
            recommended_max_mismatches=0,
            recommended_collapse_duplicates=True,
            recommended_min_count=0,
        )


def test_call_structured_raises_on_schema_mismatch(provider):
    from helix.agents.strategist import ProposeRequest

    # strategist_propose's handler returns a MethodProposal shape; asking
    # for CodeArtifact instead must surface a clear AgentCallError, not a
    # silently wrong object.
    with pytest.raises(AgentCallError):
        call_structured(
            provider, "system", "strategist_propose",
            ProposeRequest(
                dataset_metadata={"dataset_id": "d", "sequence_length": 45, "error_model": {}},
                research_objective="detect target",
            ),
            CodeArtifact,  # wrong expected type for this task
        )


def test_build_task_prompt_contains_task_type_and_json():
    proposal = MethodProposal(
        approach_summary="x", recommended_max_mismatches=1,
        recommended_collapse_duplicates=True, recommended_min_count=1,
    )
    prompt = build_task_prompt("strategist_propose", proposal)
    assert "TASK_TYPE: strategist_propose" in prompt
    assert "```json" in prompt


# --- Strategist agent: genuinely data-derived, not hardcoded ----------


def test_strategist_recommendation_scales_with_error_rate(provider):
    strategist = StrategistAgent(provider)
    low_metadata = {
        "dataset_id": "d", "sequence_length": 45,
        "error_model": {"substitution_rate": 0.0, "insertion_rate": 0.0, "deletion_rate": 0.0},
        "duplicate_rate": 0.0,
    }
    high_metadata = {
        "dataset_id": "d", "sequence_length": 45,
        "error_model": {"substitution_rate": 0.05, "insertion_rate": 0.0, "deletion_rate": 0.0},
        "duplicate_rate": 0.0,
    }
    low_proposal, _ = strategist.propose_approach(low_metadata, "detect target")
    high_proposal, _ = strategist.propose_approach(high_metadata, "detect target")
    assert low_proposal.recommended_max_mismatches < high_proposal.recommended_max_mismatches


def test_strategist_flags_indel_qc_check_when_indels_present(provider):
    strategist = StrategistAgent(provider)
    metadata = {
        "dataset_id": "d", "sequence_length": 45,
        "error_model": {"substitution_rate": 0.0, "insertion_rate": 0.01, "deletion_rate": 0.0},
        "duplicate_rate": 0.0,
    }
    proposal, _ = strategist.propose_approach(metadata, "detect target")
    assert any("indel" in q.lower() for q in proposal.qc_checks)


# --- Experiment Designer: records a real disagreement, not silent choice --


def test_experiment_designer_records_disagreement(provider):
    designer = ExperimentDesignerAgent(provider)
    proposal = MethodProposal(
        approach_summary="x", recommended_max_mismatches=1,
        recommended_collapse_duplicates=True, recommended_min_count=1,
    )
    decision, _ = designer.resolve_matching_config(proposal, total_error_rate=0.05)
    assert len(decision.disagreements) == 1
    assert decision.matching_config.max_mismatches >= proposal.recommended_max_mismatches


# --- Implementation agent: code actually compiles and runs its contract --


def test_generated_code_compiles(provider):
    impl = ImplementationAgent(provider)
    config = MatchingConfig(max_mismatches=1, collapse_duplicates=True, min_count=1)
    artifact, _ = impl.write_analysis_code(config, "ds1")
    compile(artifact.code, "<generated>", "exec")  # raises SyntaxError if malformed


def test_revision_adds_indel_handling_when_flagged(provider):
    impl = ImplementationAgent(provider)
    config = MatchingConfig(max_mismatches=1, collapse_duplicates=True, min_count=1)
    original, _ = impl.write_analysis_code(config, "ds1")
    assert "bounded_edit_distance" not in original.code

    revised, _ = impl.revise_code(
        config, "ds1", review_issues=["No evident handling of indel-shifted sequences"],
        execution_error=None, previous_version=original.version,
    )
    assert "bounded_edit_distance" in revised.code
    assert revised.version == original.version + 1


def test_revision_without_indel_issue_does_not_add_it(provider):
    impl = ImplementationAgent(provider)
    config = MatchingConfig(max_mismatches=1, collapse_duplicates=True, min_count=1)
    original, _ = impl.write_analysis_code(config, "ds1")
    revised, _ = impl.revise_code(
        config, "ds1", review_issues=["Some unrelated issue"], execution_error=None, previous_version=1
    )
    assert "bounded_edit_distance" not in revised.code


def test_naive_code_misses_indel_shifted_read_but_revised_code_catches_it(provider, tmp_path):
    """Directly exercises the Reviewer's concern: run both code versions
    in-process against a hand-crafted indel-shifted read."""
    impl = ImplementationAgent(provider)
    config = MatchingConfig(max_mismatches=2, collapse_duplicates=False, min_count=1)
    target = "A" * 20
    indel_shifted_read = "A" * 19 + "C" + "A"  # one inserted base relative to target, +1 length

    (tmp_path / "reads.fastq").write_text(
        f"@r0 barcode=b0\n{indel_shifted_read}\n+\n{'I' * len(indel_shifted_read)}\n"
    )
    (tmp_path / "reference_panel.fasta").write_text(f">t1\n{target}\n")

    naive, _ = impl.write_analysis_code(config, "ds1")
    namespace = {}
    import os

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        exec(compile(naive.code, "<naive>", "exec"), namespace)
        naive_result = __import__("json").loads((tmp_path / "result.json").read_text())
    finally:
        os.chdir(cwd)
    assert naive_result["predictions"][0]["matched_molecule_count"] == 0  # naive code misses it

    revised, _ = impl.revise_code(
        config, "ds1", review_issues=["No evident handling of indel-shifted sequences"],
        execution_error=None, previous_version=1,
    )
    namespace2 = {}
    os.chdir(tmp_path)
    try:
        exec(compile(revised.code, "<revised>", "exec"), namespace2)
        revised_result = __import__("json").loads((tmp_path / "result.json").read_text())
    finally:
        os.chdir(cwd)
    assert revised_result["predictions"][0]["matched_molecule_count"] == 1  # revised code catches it


# --- Reviewer: real static security gate, independent of provider --------


def test_static_security_scan_detects_banned_patterns():
    malicious_code = "import os\nos.system('rm -rf /')\n"
    violations = static_security_scan(malicious_code)
    assert violations


def test_reviewer_blocks_on_security_violation_even_if_mock_would_approve(provider):
    reviewer = ReviewerAgent(provider)
    code_with_eval = CodeArtifact(
        code=(
            "import json\nfrom collections import Counter\nMIN_COUNT=1\n"
            "eval('1+1')\n"
            "import json as j\n"
            "open('result.json','w').write(j.dumps({'dataset_id':'x','pipeline_name':'p',"
            "'predictions':[],'total_reads_processed':0,'total_molecules_after_dedup':0,"
            "'runtime_seconds':0.0,'errors':[]}))\n"
        ),
        version=1,
    )
    findings, _ = reviewer.review(code_with_eval)
    assert findings.approved is False
    assert findings.security_violations


def test_reviewer_approves_well_formed_code(provider):
    impl = ImplementationAgent(provider)
    config = MatchingConfig(max_mismatches=1, collapse_duplicates=True, min_count=1)
    original, _ = impl.write_analysis_code(config, "ds1")
    revised, _ = impl.revise_code(
        config, "ds1", review_issues=["No evident handling of indel-shifted sequences"],
        execution_error=None, previous_version=original.version,
    )
    reviewer = ReviewerAgent(provider)
    findings, _ = reviewer.review(revised)
    assert findings.approved is True
    assert findings.issues == []


# --- Verification agent: objective, no provider call, no ground-truth leak


def test_verification_agent_has_no_provider_dependency():
    import inspect

    assert "provider" not in inspect.signature(VerificationAgent.__init__).parameters


def test_verification_agent_applies_fixed_criteria(tmp_path):
    gt = GroundTruth(
        dataset_id="ds1", config_hash="h", total_reads=100, total_templates=100,
        duplicate_reads=0, background_template_count=90,
        targets=[GroundTruthTarget(target_id="t1", sequence="A" * 10, true_count=10, true_frequency=0.1, present=True)],
    )
    gt_dir = tmp_path / "_ground_truth"
    gt_dir.mkdir()
    (gt_dir / "ground_truth.json").write_text(gt.model_dump_json())

    result = AnalysisResult(
        dataset_id="ds1", pipeline_name="p",
        predictions=[PredictedTarget(target_id="t1", matched_read_count=10, matched_molecule_count=10, estimated_frequency=0.1, detected=True)],
        total_reads_processed=100, total_molecules_after_dedup=100, runtime_seconds=0.01,
    )
    verifier = VerificationAgent()
    validation, metrics = verifier.verify(result, tmp_path, AcceptanceCriteria(min_precision=0.9, min_recall=0.9))
    assert validation.passed is True
    assert metrics.true_positives == 1


def test_validation_result_excludes_true_frequency_fields():
    from helix.orchestration.messages import ValidationResult

    fields = ValidationResult.model_fields.keys()
    assert "true_frequency" not in fields
    assert "true_count" not in fields


# --- Verifier Agent: the LLM-driven half of the role split -----------------


def test_verifier_agent_requires_a_provider():
    """The mirror image of test_verification_agent_has_no_provider_dependency -
    this is the agent that's supposed to call one."""
    import inspect

    assert "provider" in inspect.signature(VerifierAgent.__init__).parameters


def test_verifier_agent_never_imports_ground_truth_access():
    """Structural guarantee that the interpretive half of the role split
    cannot accidentally gain the one capability reserved for the scoring
    half: loading ground truth."""
    assert "load_ground_truth" not in dir(VerifierAgent)


def test_verifier_agent_issues_corrective_instructions_on_failure():
    agent = VerifierAgent(MockProvider())
    request = VerifierGuidanceRequest(
        validation_message="Acceptance criteria not met: precision 0.50 < required 0.80",
        passed=False, precision=0.5, recall=0.9, f1=0.64,
        regression_detected=False, iteration=1,
    )
    guidance, response = agent.interpret(request)
    assert guidance.corrective_instructions
    assert any("0.50" in i for i in guidance.corrective_instructions)
    assert response.input_tokens > 0


def test_verifier_agent_surfaces_regression_in_its_guidance():
    agent = VerifierAgent(MockProvider())
    request = VerifierGuidanceRequest(
        validation_message="Acceptance criteria not met: recall 0.00 < required 0.80",
        passed=False, precision=0.0, recall=0.0, f1=0.0,
        regression_detected=True, regression_explanation="f1 decreased from 0.67 to 0.00 relative to the previous attempt.",
        iteration=2,
    )
    guidance, _ = agent.interpret(request)
    assert any("Regression" in i for i in guidance.corrective_instructions)


def test_verifier_agent_has_nothing_to_add_once_criteria_are_met():
    agent = VerifierAgent(MockProvider())
    request = VerifierGuidanceRequest(
        validation_message="All acceptance criteria met.", passed=True,
        precision=1.0, recall=1.0, f1=1.0, regression_detected=False, iteration=1,
    )
    guidance, _ = agent.interpret(request)
    assert guidance.corrective_instructions == []
    assert "no corrective action" in guidance.summary.lower()


# --- Principal Investigator -----------------------------------------------


def test_pi_decompose_assigns_all_five_specialist_roles(provider):
    pi = PrincipalInvestigatorAgent(provider)
    plan, _ = pi.decompose_objective("Detect the target.", "ds1")
    roles = {t.role for t in plan.tasks}
    assert roles == {"strategist", "implementation", "reviewer", "experiment_designer", "verifier"}


def test_pi_final_review_reflects_validation_outcome(provider):
    pi = PrincipalInvestigatorAgent(provider)
    passed_review, _ = pi.review_final_results("obj", True, 1, 3)
    failed_review, _ = pi.review_final_results("obj", False, 3, 3)
    assert passed_review.status == "completed"
    assert failed_review.status == "failed"
