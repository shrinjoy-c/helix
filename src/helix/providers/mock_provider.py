"""A deterministic, rule-based stand-in for a real LLM provider.

This is the default provider used by the demo script and all tests, so
that the full agent pipeline runs for free, offline, and reproducibly.
It is explicitly NOT a simulation of what a language model would say -
it is a small, honest rule engine: each handler below computes its
output directly from the structured input JSON an agent embeds in its
prompt (see `helix.agents.base.call_structured`), using the same
reasoning a human would (e.g. the strategist proposal's mismatch
tolerance is derived from the dataset's actual substitution rate, not a
hardcoded constant).

Swap in `helix.providers.anthropic_provider.AnthropicProvider` to get
real model-driven reasoning through the exact same agent code path -
every agent calls `provider.complete(system, prompt)` and parses the
response as JSON matching its expected output schema, regardless of
which provider is behind it.
"""

from __future__ import annotations

import json
import re

from helix.providers.base import LLMProvider, ProviderResponse

_TASK_TYPE_RE = re.compile(r"TASK_TYPE:\s*(\w+)")
_JSON_BLOCK_RE = re.compile(r"```json\s*(.*?)\s*```", re.DOTALL)


def _extract_task(prompt: str) -> tuple[str, dict]:
    type_match = _TASK_TYPE_RE.search(prompt)
    json_match = _JSON_BLOCK_RE.search(prompt)
    if not type_match or not json_match:
        raise ValueError("MockProvider requires a 'TASK_TYPE: <type>' line and a ```json ... ``` block")
    task_type = type_match.group(1)
    data = json.loads(json_match.group(1))
    return task_type, data


def _handle_pi_decompose(data: dict) -> dict:
    objective = data.get("research_objective", "")
    tasks = [
        {"role": "strategist", "description": "Propose a matching strategy and QC checks for the dataset.", "status": "pending", "task_id": "task_strategist"},
        {"role": "implementation", "description": "Implement and execute the matching analysis.", "status": "pending", "task_id": "task_implementation"},
        {"role": "reviewer", "description": "Independently review the proposed method and generated code.", "status": "pending", "task_id": "task_reviewer"},
        {"role": "experiment_designer", "description": "Resolve methodological disagreements into concrete parameters.", "status": "pending", "task_id": "task_experiment_designer"},
        {"role": "verifier", "description": "Independently verify outputs against predefined acceptance criteria.", "status": "pending", "task_id": "task_verifier"},
    ]
    interpretation = (
        "Objective emphasizes minimizing false positives; precision should be weighted "
        "at least as heavily as recall."
        if "false positive" in objective.lower()
        else "Standard presence/absence detection and abundance-estimation objective."
    )
    return {"tasks": tasks, "objective_interpretation": interpretation}


def _handle_strategist_propose(data: dict) -> dict:
    metadata = data["dataset_metadata"]
    error_model = metadata.get("error_model", {})
    sub_rate = error_model.get("substitution_rate", 0.0)
    ins_rate = error_model.get("insertion_rate", 0.0)
    del_rate = error_model.get("deletion_rate", 0.0)
    seq_len = metadata.get("sequence_length", 45)
    duplicate_rate = metadata.get("duplicate_rate", 0.0)

    expected_substitution_errors = sub_rate * seq_len
    recommended_max_mismatches = max(0, round(expected_substitution_errors))
    has_indels = (ins_rate + del_rate) > 0

    assumptions = [
        "Sequencing errors are modeled as i.i.d. per-base events (HELIX's documented simplification).",
        "The reference panel contains every sequence of biological interest.",
    ]
    qc_checks = ["Flag datasets with zero reads.", "Flag an empty reference panel."]
    if has_indels:
        qc_checks.append(
            "Non-zero indel rate: reads may vary in length, so exact-length-only "
            "matching will silently miss indel-affected reads."
        )

    return {
        "approach_summary": (
            f"Reference-based matching with up to {recommended_max_mismatches} mismatch(es), "
            f"derived from an expected ~{expected_substitution_errors:.2f} substitution errors "
            f"per {seq_len}nt read."
        ),
        "recommended_max_mismatches": recommended_max_mismatches,
        "recommended_collapse_duplicates": duplicate_rate > 0,
        "recommended_min_count": 2 if duplicate_rate > 0.05 else 1,
        "assumptions": assumptions,
        "qc_checks": qc_checks,
    }


def _render_analysis_code(matching_config: dict, dataset_id: str, handle_indels: bool) -> str:
    indel_helper = ""
    distance_call = "hamming(seq, target_seq) if len(seq) == len(target_seq) else MAX_MISMATCHES + 1"
    if handle_indels:
        indel_helper = '''
def bounded_edit_distance(a, b, max_distance):
    if a == b:
        return 0
    len_a, len_b = len(a), len(b)
    if abs(len_a - len_b) > max_distance:
        return max_distance + 1
    prev = list(range(len_b + 1))
    for i in range(1, len_a + 1):
        curr = [i] + [0] * len_b
        for j in range(1, len_b + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[len_b]
'''
        distance_call = (
            "hamming(seq, target_seq) if len(seq) == len(target_seq) "
            "else bounded_edit_distance(seq, target_seq, MAX_MISMATCHES)"
        )

    return f'''
import json
from collections import Counter
from pathlib import Path

def hamming(a, b):
    return sum(x != y for x, y in zip(a, b))
{indel_helper}

def read_fastq(path):
    reads = []
    with open(path) as f:
        lines = [l.rstrip("\\n") for l in f]
    for i in range(0, len(lines), 4):
        header = lines[i]
        seq = lines[i + 1]
        read_id = header[1:].split()[0]
        barcode = ""
        if "barcode=" in header:
            barcode = header.split("barcode=")[1].split()[0]
        reads.append({{"read_id": read_id, "sequence": seq, "barcode": barcode}})
    return reads

def read_fasta(path):
    targets = []
    content = Path(path).read_text()
    for block in content.strip().split(">")[1:]:
        lines = block.splitlines()
        target_id = lines[0].split()[0]
        seq = "".join(lines[1:])
        targets.append((target_id, seq))
    return targets

MAX_MISMATCHES = {matching_config["max_mismatches"]}
MIN_COUNT = {matching_config["min_count"]}
COLLAPSE_DUPLICATES = {matching_config["collapse_duplicates"]}

reads = read_fastq("reads.fastq")
targets = read_fasta("reference_panel.fasta")

if COLLAPSE_DUPLICATES:
    by_barcode = {{}}
    for r in reads:
        by_barcode.setdefault(r["barcode"], []).append(r["sequence"])
    molecules = [Counter(seqs).most_common(1)[0][0] for seqs in by_barcode.values()]
else:
    molecules = [r["sequence"] for r in reads]

total_molecules = len(molecules)
match_counts = {{target_id: 0 for target_id, _ in targets}}
for seq in molecules:
    best_id, best_dist = None, MAX_MISMATCHES + 1
    for target_id, target_seq in targets:
        d = {distance_call}
        if d <= MAX_MISMATCHES and d < best_dist:
            best_dist = d
            best_id = target_id
    if best_id is not None:
        match_counts[best_id] += 1

predictions = []
for target_id, _ in targets:
    count = match_counts[target_id]
    freq = count / total_molecules if total_molecules else 0.0
    predictions.append({{
        "target_id": target_id,
        "matched_read_count": count,
        "matched_molecule_count": count,
        "estimated_frequency": freq,
        "detected": count >= MIN_COUNT,
    }})

result = {{
    "dataset_id": {dataset_id!r},
    "pipeline_name": "implementation_agent_v1",
    "predictions": predictions,
    "total_reads_processed": len(reads),
    "total_molecules_after_dedup": total_molecules,
    "runtime_seconds": 0.0,
    "errors": [],
}}
Path("result.json").write_text(json.dumps(result))
'''.strip()


def _handle_implementation_write_code(data: dict) -> dict:
    code = _render_analysis_code(data["matching_config"], data["dataset_id"], handle_indels=False)
    return {"code": code, "language": "python", "description": "Initial reference-matching analysis script.", "version": 1}


def _handle_implementation_revise_code(data: dict) -> dict:
    issues_text = " ".join(data.get("review_issues", [])).lower()
    needs_indel_fix = any(k in issues_text for k in ("indel", "unequal length", "edit distance"))
    code = _render_analysis_code(data["matching_config"], data["dataset_id"], handle_indels=needs_indel_fix)
    return {
        "code": code,
        "language": "python",
        "description": "Revised analysis script addressing reviewer/verification feedback.",
        "version": data.get("previous_version", 1) + 1,
    }


_BANNED_PATTERNS = (
    "eval(", "exec(", "os.system", "subprocess", "socket.", "import requests", "urllib.request", "__import__",
)


def _handle_reviewer_review(data: dict) -> dict:
    code = data["code"]
    issues: list[str] = []
    security_violations: list[str] = []

    for pattern in _BANNED_PATTERNS:
        if pattern in code:
            security_violations.append(f"Disallowed pattern found in generated code: `{pattern}`")

    has_indel_handling = any(k in code.lower() for k in ("edit_distance", "levenshtein", "bounded_edit_distance"))
    if not has_indel_handling:
        issues.append(
            "No evident handling of indel-shifted (unequal length) sequences; reads affected by "
            "insertions/deletions may be silently left unmatched."
        )
    if "MIN_COUNT" not in code and "min_count" not in code:
        issues.append("Detection threshold does not appear to be applied anywhere in the code.")

    approved = not issues and not security_violations
    return {
        "approved": approved,
        "issues": issues,
        "suggested_tests": [
            "A read exactly one insertion away from a target sequence.",
            "An empty reads.fastq (zero reads) input.",
        ],
        "security_violations": security_violations,
    }


def _handle_experiment_designer_resolve(data: dict) -> dict:
    strategist_max_mismatches = data["strategist_max_mismatches"]
    error_rate = data.get("total_error_rate", 0.0)
    widen = error_rate > 0.03
    chosen = strategist_max_mismatches + 1 if widen else strategist_max_mismatches

    disagreement = {
        "topic": "max_mismatches",
        "position_a": f"Strategist agent recommends max_mismatches={strategist_max_mismatches}, derived from the substitution rate alone.",
        "position_b": f"A more permissive tolerance (max_mismatches={strategist_max_mismatches + 1}) would also absorb indel-driven edit distance.",
        "resolution": f"max_mismatches={chosen}",
        "rationale": (
            "Widen tolerance by one beyond the substitution-only estimate when the combined error "
            "rate exceeds 3%, to avoid under-calling true positives; otherwise keep the stricter, "
            "data-derived value to control false positives."
        ),
    }
    return {
        "matching_config": {
            "max_mismatches": chosen,
            "collapse_duplicates": data.get("collapse_duplicates", True),
            "min_count": data.get("min_count", 1),
        },
        "disagreements": [disagreement],
    }


def _handle_pi_review(data: dict) -> dict:
    validation_passed = data.get("validation_passed", False)
    iterations_used = data.get("iterations_used", 1)
    max_iterations = data.get("max_iterations", 1)
    if validation_passed:
        status = "completed"
        summary = f"Verification passed within {iterations_used}/{max_iterations} iteration(s); objective addressed."
    elif iterations_used >= max_iterations:
        status = "failed"
        summary = f"Verification did not pass within the {max_iterations}-iteration budget."
    else:
        status = "needs_revision"
        summary = "Verification has not yet passed; another revision cycle is warranted."
    return {"status": status, "summary": summary}


def _handle_verifier_interpret(data: dict) -> dict:
    instructions = list(data.get("review_issues", []))
    if not data.get("passed", False):
        instructions.append(
            f"Acceptance criteria not met (precision={data.get('precision', 0.0):.2f}, "
            f"recall={data.get('recall', 0.0):.2f}): {data.get('validation_message', '')}"
        )
    if data.get("regression_detected"):
        instructions.append(
            f"Regression vs. the previous attempt: {data.get('regression_explanation', '')} Do not "
            "reintroduce whatever changed between the two attempts without addressing this."
        )
    if data.get("passed", False):
        summary = "Acceptance criteria met; no corrective action needed."
    else:
        summary = f"{len(instructions)} corrective instruction(s) issued for the next revision attempt."
    return {"corrective_instructions": instructions, "summary": summary}


_HANDLERS = {
    "pi_decompose": _handle_pi_decompose,
    "pi_review": _handle_pi_review,
    "strategist_propose": _handle_strategist_propose,
    "implementation_write_code": _handle_implementation_write_code,
    "implementation_revise_code": _handle_implementation_revise_code,
    "reviewer_review": _handle_reviewer_review,
    "experiment_designer_resolve": _handle_experiment_designer_resolve,
    "verifier_interpret": _handle_verifier_interpret,
}


class MockProvider(LLMProvider):
    """Deterministic, free, offline. See module docstring."""

    name = "mock"

    def complete(self, system: str, prompt: str, *, max_tokens: int = 1024) -> ProviderResponse:
        task_type, data = _extract_task(prompt)
        handler = _HANDLERS.get(task_type)
        if handler is None:
            raise ValueError(f"MockProvider has no handler for task_type={task_type!r}")
        result = handler(data)
        text = json.dumps(result)
        return ProviderResponse(
            text=text,
            model="helix-mock-v1",
            input_tokens=max(1, len(prompt) // 4),
            output_tokens=max(1, len(text) // 4),
            estimated_cost_usd=0.0,
        )
