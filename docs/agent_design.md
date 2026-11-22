# Agent Design

## Scope of this document

Describes the agent orchestration layer as implemented in Release 0.1: six agent
classes, a provider-independent model interface, a shared `ExperimentState`, an
append-only event log, bounded iteration/timeout budgets, and a restricted execution
sandbox for agent-written code. It also states plainly what is genuinely implemented
versus what the default demo deliberately simulates.

## What's real and what's simulated

**Genuinely implemented, regardless of which provider is behind the agents:**

- The orchestration control flow: task assignment, the write → review → execute →
  verify loop, bounded revision cycles, iteration/timeout enforcement, cancellation.
- Code execution: the Implementation Agent's code is actually run, in a real
  (restricted) subprocess, against the real public dataset files.
- The Reviewer's security gate: a deterministic static scan for banned patterns
  (`eval`, `subprocess`, raw sockets, etc.) that runs on every code version regardless
  of provider, and can block approval on its own (`tests/test_agents.py::test_reviewer_blocks_on_security_violation_even_if_mock_would_approve`).
  `RestrictedSandbox`'s own import guard is a second, independent layer of the same control.
- The Verification Agent: always computes real precision/recall/F1/etc. via
  `helix.biology.evaluation.evaluate` against real ground truth, and applies
  acceptance criteria fixed before the run - never an LLM judgment call.
- The event log and `ExperimentState`: a complete, ordered record of what actually
  happened in a given run, not illustrative/decorative text.

**Simulated by default (and this is a deliberate, documented choice, not a
shortcut taken silently):**

- The default provider, `MockProvider`, is a small deterministic rule engine, not a
  language model. Its "reasoning" (e.g. recommending `max_mismatches` from a
  dataset's actual substitution rate, or deciding which code template to emit) is
  real Python logic computed from the real structured input each agent sends it -
  but it is hand-written, not model-generated text. Swap in
  `helix.providers.anthropic_provider.AnthropicProvider` (requires
  `pip install -e ".[providers]"` and a separately-funded `ANTHROPIC_API_KEY` - a
  Claude Code/Ultra subscription does not provide this) to get real model-driven
  reasoning through the *exact same* agent code path: every agent calls
  `provider.complete(system, prompt)` and parses the result as JSON matching its
  expected output schema, whichever provider answers.
- `MockProvider`'s revision policy is narrow: it only adds indel (edit-distance)
  handling when a reviewer/verification message mentions indels/unequal
  length/edit distance by name. If the actual problem is something its simple
  keyword match doesn't recognize, the mock will keep re-submitting materially the
  same code until the iteration budget is exhausted. This is an honest limitation of
  a rule-based stand-in, not of the orchestration loop itself (see
  `tests/test_orchestration.py::test_multi_agent_stops_at_max_iterations_when_never_approved`
  for proof the budget is still respected).

## Why the Verification Agent never calls a provider

`helix.agents.verifier.VerificationAgent` has no `provider` parameter at all
(`tests/test_agents.py::test_verification_agent_has_no_provider_dependency` asserts
this structurally). Verification against ground truth is supposed to be an
objective check against acceptance criteria fixed *before* the run - not a judgment
call that could itself hallucinate or be argued with. It is also the only agent that
is allowed to load `GroundTruth` (via `helix.biology.evaluation.load_ground_truth`),
and it never receives that ground truth from, or hands it to, any other agent.

### The leakage problem this solves

`EvaluationMetrics.per_target` includes each target's true frequency and true
presence - exactly the thing the whole benchmark is trying to keep hidden from the
pipeline under test. If that full object were dropped into `ExperimentState` for
other agents to read while deciding how to revise, the Implementation Agent could
effectively read the answer key on iteration 2. To prevent this, the orchestrator
only ever stores a redacted `helix.orchestration.messages.ValidationResult` (pass/fail
+ aggregate precision/recall/F1/FP-rate/FN-rate + a message) in `ExperimentState`.
The full `EvaluationMetrics`, true counts included, is returned to the orchestrator's
caller for the final persisted experiment record - never written into the state
agents read mid-run. `tests/test_agents.py::test_validation_result_excludes_true_frequency_fields`
pins this down structurally, not just by convention.

This is an imperfect mitigation, not a hard guarantee: a pass/fail signal repeated
over many iterations is still *some* information about the ground truth (this is true
of any iterative evaluate-and-retry loop, including real wet-lab or dry-lab research).
It is recorded here as a known limitation, not swept under the rug.

## The six agents

| Agent | Module | Calls provider for its core output? | Distinct operation |
|---|---|---|---|
| Principal Investigator | `agents/principal_investigator.py` | Yes | Decomposes the objective into the five specialist tasks up front; at the end, reviews whether validation passed and assigns final experiment status (`completed`/`needs_revision`/`failed`). |
| Strategist | `agents/strategist.py` | Yes | Reads only `dataset_metadata.json` (redacted - no abundance/presence) and proposes `max_mismatches`/duplicate handling/QC checks *derived from the dataset's actual error model*, not fixed constants. |
| Experiment Designer | `agents/experiment_designer.py` | Yes | Takes the Strategist proposal and reconciles it against the dataset's total error rate into one concrete `MatchingConfig`, recording a `Disagreement` (two stated positions + a resolution + a rationale) rather than silently picking a number. |
| Implementation | `agents/implementation.py` | Yes | Writes a real, compiling Python script; executes it through `helix.execution.runner` against the restricted sandbox; revises it based on reviewer issues or execution errors. |
| Independent Reviewer | `agents/reviewer.py` | Yes (plus an always-on deterministic static scan) | Inspects the actual code text for a concrete, checkable gap (missing indel/edit-distance handling, missing threshold application) and for banned security patterns; can block approval even if its own provider call would have approved. |
| Independent Verification | `agents/verifier.py` | **No** | Computes real precision/recall/F1/FP-rate/FN-rate against ground truth and applies acceptance criteria fixed before the run; returns a redacted result for shared state and the full metrics for the final record. |

## Coordination: how a disagreement actually gets resolved

The Strategist Agent proposes `max_mismatches` from the substitution rate alone
(it has no way to know the dataset's indel rate from a mismatch count without seeing
the raw error model, which it does have - see `_handle_strategist_propose` in
`helix/providers/mock_provider.py`). The Experiment Designer independently receives
`total_error_rate` (substitution + insertion + deletion) and widens the tolerance by
one when that total exceeds 3%, recording both positions and its rationale as a
`Disagreement` in `ExperimentState.disagreements` - this is real data any later
analysis or UI can inspect, not a hardcoded narrative string.

## Single-agent vs. multi-agent: what actually differs

Both workflows share the same restricted sandbox and the same independent,
objective Verification Agent - that's what makes a comparison between them fair.
They differ only in which roles participate in the loop:

- **Single-agent** (`run_single_agent`): one generalist pass - Strategist-style
  proposal → Implementation writes/executes code → Verification scores it. No
  Principal Investigator decomposition, no Experiment Designer negotiation, and
  critically, **no Reviewer** - nothing inspects the code before it runs except the
  sandbox's own security guard.
- **Multi-agent** (`run_multi_agent`): all six roles participate, with the Reviewer's
  approval gating whether code is even executed.

`tests/test_orchestration.py::test_single_agent_end_to_end_completes` asserts the
single-agent event log contains no `reviewer` or `experiment_designer` actor
entries - the absence is structurally checked, not just described here.

## Execution sandbox

See [`docs/security.md`](security.md) for the full, honest scope statement. In short:
a restricted subprocess (scoped temp dir, scrubbed environment, wall-clock timeout,
best-effort POSIX resource limits, an import-blocking guard) - not a hardened
container.

## Ablations and parallel exploration (Release 0.5)

`run_multi_agent` takes three independent `enable_*` flags
(`enable_experiment_designer`, `enable_reviewer`, `enable_verification_loop`),
each removing exactly one coordination mechanism from the full system. This
is the system's literal implementation of H5 ("removing specific agents
will reveal which roles contribute meaningfully") - see
`helix.benchmarking.ablations` for the three pre-defined single-factor
ablations, and `tests/test_orchestration.py` for structural proof each one
actually removes what it claims to (e.g. `no_reviewer` asserts
`state.review_findings == []` and no `reviewer` actor in the event log).

`run_parallel_multi_agent` is configuration D: instead of one Experiment
Designer resolving a single `max_mismatches` value, it explores up to three
candidate values concurrently - each in its own real sandboxed subprocess
(genuine OS-level parallelism via `ThreadPoolExecutor`, not sequential work
relabeled) - and selects the best-scoring branch. It is scoped to one round
with no cross-round revision; see "Known limitations" below for why.

### A genuine, reproducible failure finding

Under `MockProvider`'s policy, the Implementation Agent's *first* code
version never includes indel/edit-distance handling - it's only added on
*revision*, in response to Reviewer feedback (see
`tests/test_agents.py::test_revision_adds_indel_handling_when_flagged`).
This has a real, reproducible consequence: any configuration that gets
exactly one shot - `sequential_multi_agent` (the `no_verification_loop`
ablation) and `parallel_multi_agent` alike - has its first-draft code
rejected by the Reviewer and never gets to execute at all, regardless of
how good its parameter choice was. `scripts/run_benchmark.py` demonstrates
this directly: both configurations score 0% success across every dataset
tested, while every configuration that retains at least one revision
cycle (the full system, `no_reviewer`, `no_experiment_designer`) succeeds.

This is not a bug being swept under the rug - it's exactly the kind of
result the project's own research question anticipates: **iterative
verification is doing essentially all of the work in this scenario**,
and breadth (parallel exploration of parameters) does not substitute for
depth (a revision cycle that can fix a code-quality defect) under this
particular provider's policy. Whether this generalizes to a real LLM
provider, which might write better first-draft code, is an open question
this benchmark is specifically built to let you re-ask once
`AnthropicProvider` is exercised for real. See
`tests/test_orchestration.py::test_parallel_multi_agent_single_shot_branches_cannot_pass_review`
and `::test_ablation_no_verification_loop_caps_at_one_iteration_and_can_fail`.

## Known limitations of Release 0.1's agent layer

- `AnthropicProvider` exists and is wired through the same interface, but has not
  been run end-to-end in this repository (no funded API key was used to build
  this project) - treat it as implemented-but-unverified until a real run is recorded.
- `run_parallel_multi_agent` is deliberately scoped to one round of concurrent
  exploration with no cross-round revision - a branch that fails review or
  execution is not retried. Combining parallel breadth with iterative depth
  (e.g. N branches, each allowed its own revision cycle) is a natural next
  extension, not attempted here to keep this phase's scope bounded.
- Mid-flight cancellation of `run_parallel_multi_agent` is only checked before
  the concurrent round starts, not once branches are already running in their
  sandboxed subprocesses.
- The benchmark sweep (`scripts/run_benchmark.py`) currently exercises two
  dataset difficulty levels with 2 repeats each; a fuller sweep across every
  documented hard case (closely-related targets, heavy duplication, the
  true-no-target case) through every configuration is straightforward to add
  with the existing `run_benchmark_sweep` machinery but has not been run yet.
