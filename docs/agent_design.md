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

## Real providers: what actually happened the first time one was used

Every provider before this point had been exercised only via `MockProvider` -
`AnthropicProvider` existed in code but had never been run against a funded key.
The first real test (`helix.providers.ollama_provider.OllamaProvider`, local,
free, against `qwen3:4b` via a running `ollama serve`) immediately surfaced two
genuine bugs in `helix.agents.base`, not in the new provider:

1. **Empty responses.** A reasoning-capable model can spend its *entire* token
   budget on a `<think>` block and never emit the actual answer - a real
   `StrategistAgent.propose_approach()` call with a 1024-token budget came
   back with `done_reason="length"` and empty content. Fixed by giving
   `OllamaProvider` its own much larger floor (8192 tokens; `complete()`'s
   `max_tokens` argument is raised to at least this, not honored literally) -
   see `OllamaProvider.__init__`'s docstring for the exact numbers reproduced.
2. **Wrong field names.** Even with enough tokens to finish, the model's JSON
   used its own invented field names (`sequence_matching_approach` instead of
   `approach_summary`, etc.) - conceptually on-task, but unparseable as the
   required Pydantic model. The root cause: `build_task_prompt` had never
   actually told any provider what the output schema's field names *were* -
   only a generic "match the requested schema" instruction. `MockProvider`
   never needed this (it dispatches on `task_type`, not on reading the
   instruction), and no real provider had exercised this path before. Fixed by
   embedding `output_model.model_json_schema()` directly in the prompt -
   benefits every real provider, not just Ollama.

After both fixes, the same real `propose_approach()` call succeeded in ~64s,
producing a correctly-typed `MethodProposal` with sensible reasoning
(correctly read the 0.01 substitution rate and 45nt length to recommend
`max_mismatches=1`). `helix.agents.base._extract_json_text` (markdown-fence
and `<think>` stripping, added defensively before any of this was tested)
also turned out to matter in practice, not just in theory.

A third, distinct gap surfaced running the full `run_single_agent`
orchestrator loop (propose -> write code -> execute -> verify) end to end:
the Implementation Agent's *own* JSON reply was schema-compliant (the fix
above worked), but the *Python code it wrote* used its own invented field
names for `result.json` (`variant_id`/`mismatches` instead of
`target_id`/`matched_read_count`/etc.) - the code ran fine, it just produced
a file that failed `AnalysisResult` validation 3000 ways (one dataset, many
predictions). This is a different failure mode from the first two: the
agent's own reply was valid JSON matching the right contract; the bug was
one level removed, in a downstream artifact that same JSON described how to
build. The first fix attempted - embedding one concrete, fully-worked
`AnalysisResult` example as a ```json-fenced block in the system prompt -
*backfired*: the model echoed that example straight back as its own reply
(a `CodeArtifact` validation error - the literal result.json content, with
no `code` field at all) despite an explicit "this describes what your code
writes, not what you reply with" sentence. A second, more concrete JSON
blob apparently out-competed the abstract `CodeArtifact` schema for
salience. The actual fix: describe the same contract in plain prose (field
names, types, structure - no parseable JSON at all), which can't be
mistaken for a ready-to-copy reply the same way.

Two further live attempts to confirm this fix timed out (240s, then 300s
after also raising `num_ctx` - see below) before the model finished writing
code at all - inconclusive on whether the prose fix works, not evidence it
doesn't. Those attempts incidentally surfaced a fourth, likely-contributing
issue: `ollama ps` showed the model's context window defaulting to a
4096-token window, while `complete()` was requesting up to 8192 output
tokens alone, before the prompt even counts against it - requesting more
output than fits the context is not just wasted, it can produce exactly
this kind of request that never returns. Fixed by passing `num_ctx`
explicitly (default 16384, comfortably under qwen3's own 262144 maximum).
Whether that alone is sufficient for reliable code-generation-length
replies on modest local hardware remains unconfirmed - this is recorded
honestly as an open item, not papered over. The shorter, JSON-reply-only
call (`propose_approach`) consistently completes in confirmed under a
minute; the longer code-writing call has not yet been confirmed to
complete within any tested budget on the hardware this was developed on.

The throughline across all four: **a real local model needs the exact
output contract spelled out redundantly and concretely at every level where
it matters, not described once and assumed to propagate - and even "more
explicit" isn't automatically better, if the extra detail is salient enough
to be mistaken for the reply itself.** A MockProvider dispatching on
`task_type` never needed any of this, and no real provider (including
`AnthropicProvider`, still untested) had ever exercised these paths before
Ollama was wired up and actually run.

## Provider layer: "any API key"

Four real-model providers now share the identical `complete(system, prompt)`
interface, so every agent's code is unchanged regardless of which one answers:

- **`AnthropicProvider`** - requires `ANTHROPIC_API_KEY`. Implemented but still
  not run against a funded key (unlike `OllamaProvider`/`OpenAICompatibleProvider`
  below, which have now actually been exercised against a live endpoint).
- **`GeminiProvider`** - requires `GEMINI_API_KEY`. Talks to Google's native
  Generative Language REST API directly (`generateContent`), not the
  OpenAI-compatibility shim Gemini also exposes, so it stays consistent with
  `OllamaProvider`'s choice to use each service's own native endpoint rather
  than a lowest-common-denominator shim. Implemented against the documented
  contract and covered by mocked tests (including the "blocked by safety
  filters" empty-candidates case, which raises a clear error rather than an
  `IndexError`), but - like `AnthropicProvider` - not yet run against a funded
  key. Same honest status Ollama/OpenAICompatible carried before their own
  live testing: "should work," not "confirmed working."
- **`OllamaProvider`** - local, free, no API key. Talks to Ollama's native
  `/api/chat` (not its OpenAI-compatibility shim, so its token-count fields are
  reported accurately). This is the cheapest way to exercise the real
  agent/provider code path - see "what actually happened" above.
- **`OpenAICompatibleProvider`** - one class, parameterized by `base_url` +
  `api_key_env`, for *any* service implementing the OpenAI chat-completions
  format: OpenAI itself, Groq, Together, Fireworks, DeepSeek, Azure OpenAI, or
  a self-hosted vLLM/LM Studio/text-generation-webui server. "Any API key" is
  a matter of pointing this at the right URL and environment variable, not
  writing a new provider per vendor. Never accepts a raw key over the API -
  only an environment-variable *name*, read server-side (`helix/api/schemas.py`'s
  `ProviderSelection` docstring explains why: a secret has no business
  travelling through an HTTP request body or a frontend form).

All four are selectable per-experiment via the API (`ExperimentLaunchRequest`/
`BenchmarkSweepRequest`'s `provider`/`model`/`base_url`/`api_key_env` fields) and
the dashboard's Research Workspace and Experiment Lab (a shared
`ProviderSelector` component).

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

## The independent Evaluation Engine service (`helix.evaluation_engine`)

`helix.evaluation_engine` packages the deterministic scorer above and the persisted
`ExperimentStore` behind five named operations: `run_evaluation`, `get_metrics`,
`get_failure_report`, `compare_implementations`, `get_validation_results`. This is the
"Evaluation Engine" half of the Verifier role split (see Decision 1 in the extension
plan): `VerificationAgent` stays the one call site that scores a *live* run; this
module is the read-side surface anything else - a human, a report, a future
dashboard panel, or the new `VerifierAgent` below - uses to ask about an
already-scored run without reinventing "what counts as a failure" per caller.

It is a plain internal Python module, not a network service or an MCP server. The
extension plan's Decision 2 considered MCP for exactly this surface and declined it:
zero new dependency, revisit only if evaluation ever needs to be reachable from
outside this one process. The access boundary is architectural, not a process
boundary - `load_ground_truth` is still only ever called from here and from
`VerificationAgent`, same as before this module existed.

`get_failure_report` classifies a failed/needs-revision run by reading only its
persisted `event_log` and `evaluation_metrics` - never by re-scoring anything - into
one of `execution_failed` / `reviewer_rejected` / `metric_miss` / `timed_out` /
`budget_exceeded` / `cancelled` / `unknown`. `unknown` is a real, honest outcome: if
the event log doesn't make the cause legible, the function says so rather than
guessing. This is the §10.E failure-analysis gap's first concrete tool, not the full
"mine failures for patterns across many runs" report that section ultimately asks
for.

`compare_implementations` is the same kind of comparison `_compare_coding_branches`
makes inside a live `run_dual_agent` call, but over two already-persisted experiments
instead of two in-memory branches - useful once Experiments C/D/E produce many
separate runs to compare, not just one dual-agent call's two branches.

`detect_regression` is a small, pure function living in this module rather than in
the Verifier Agent: comparing one attempt's F1 to the previous attempt's F1 is a
scoring computation with one correct answer, not a judgment call, so it stays on the
deterministic side of the role split. See "The Verifier Agent" below for how its
output is used.

## The Verifier Agent (`agents/verifier.VerifierAgent`) vs. the Verification Agent

Decision 1 from the extension plan, implemented: `VerificationAgent` (unchanged, see
above) stays the scoring authority. A new, separate `VerifierAgent` *interprets* that
score and turns it into corrective instructions for the Implementation Agent -
genuinely the different thing the original project spec asked for, not a renamed
copy of the same class. Neither can touch the other's responsibility: `VerifierAgent`
has no `load_ground_truth` access and never recomputes a metric; `VerificationAgent`
still never calls a provider.

Only `run_multi_agent` wires this in today, gated by a new `enable_verifier_agent`
flag (default `True`, matching the style of `enable_experiment_designer`/
`enable_reviewer`/`enable_verification_loop`) - when disabled, the loop falls back to
exactly the pre-Phase-3 behavior of feeding `validation_result.message` straight into
`revise_code()`. `run_single_agent`, `run_parallel_multi_agent`, and `run_dual_agent`
keep the deterministic-only verification they've always had; extending the role split
to those workflows is not yet done and is tracked, not silently skipped.

Each revision attempt inside `run_multi_agent` is now also recorded as a
`helix.orchestration.messages.RevisionCycleRecord` (cycle number, code version,
outcome, review issues, execution error, the redacted `ValidationResult`, the
regression signal from `evaluation_engine.detect_regression`, and the
`VerifierAgent`'s corrective instructions) in `ExperimentState.revision_cycles`, and
persisted on `ExperimentRecord.revision_cycles` - the structured per-cycle record
`helix.evaluation_engine.get_validation_results` reads back, closing the "no
structured per-cycle record beyond free-text event-log entries" gap for this
workflow specifically.

## The seven agents

| Agent | Module | Calls provider for its core output? | Distinct operation |
|---|---|---|---|
| Principal Investigator | `agents/principal_investigator.py` | Yes | Decomposes the objective into the five specialist tasks up front; at the end, reviews whether validation passed and assigns final experiment status (`completed`/`needs_revision`/`failed`). |
| Strategist | `agents/strategist.py` | Yes | Reads only `dataset_metadata.json` (redacted - no abundance/presence) and proposes `max_mismatches`/duplicate handling/QC checks *derived from the dataset's actual error model*, not fixed constants. |
| Experiment Designer | `agents/experiment_designer.py` | Yes | Takes the Strategist proposal and reconciles it against the dataset's total error rate into one concrete `MatchingConfig`, recording a `Disagreement` (two stated positions + a resolution + a rationale) rather than silently picking a number. |
| Implementation | `agents/implementation.py` | Yes | Writes a real, compiling Python script; executes it through `helix.execution.runner` against the restricted sandbox; revises it based on reviewer issues or execution errors. `run_dual_agent` instantiates it twice, independently - see "Independent coding agents" below. |
| Independent Reviewer | `agents/reviewer.py` | Yes (plus an always-on deterministic static scan) | Inspects the actual code text for a concrete, checkable gap (missing indel/edit-distance handling, missing threshold application) and for banned security patterns; can block approval even if its own provider call would have approved. |
| Independent Verification (Evaluation Engine) | `agents/verifier.VerificationAgent` + `helix.evaluation_engine` | **No** | Computes real precision/recall/F1/FP-rate/FN-rate against ground truth and applies acceptance criteria fixed before the run; returns a redacted result for shared state and the full metrics for the final record. The scoring authority - see Decision 1's role split above. |
| Verifier | `agents/verifier.VerifierAgent` | Yes | Interprets the Evaluation Engine's redacted output plus its regression signal and turns it into corrective instructions for the Implementation Agent. `run_multi_agent` only so far, gated by `enable_verifier_agent`. Never touches ground truth or recomputes a score. |

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

## Independent coding agents (`run_dual_agent`)

Two separately-instantiated `ImplementationAgent`s receive the same Strategist proposal
and the same Experiment Designer-resolved `matching_config` - "the same scientific task
and requirements," per the extension plan's wording - then each independently writes,
reviews, executes, and revises its own implementation **in its own thread**, with no code
path connecting one branch's intermediate code, review findings, or execution errors to
the other's prompt. Isolated working directories fall out of the existing sandbox design
for free: `RestrictedSandbox.run_python` already creates a fresh `tempfile.mkdtemp()`
workdir per call, so branch a and b never share a filesystem path regardless of which
agent is running.

`_compare_coding_branches` runs only after both branches have already finished - it is a
comparison of two completed, independent attempts (approach, output, performance, failure
mode), never a channel either branch could use to influence the other. The comparison
records whether both branches detected the same targets, the mean abundance-estimate
delta where they overlap, and a winner picked by `(passed, f1, precision)` - the same
ranking rule `run_parallel_multi_agent` already uses to pick among its candidate
branches, reused rather than reinvented.

**Honest limitation, stated plainly rather than glossed over:** `MockProvider` is a
deterministic rule engine. Given identical inputs, two `ImplementationAgent`s backed by
the same `MockProvider` instance will typically converge on near-identical code - real
approach diversity (different algorithms, different edge-case handling, different
tradeoffs) is only observable with a real, sampling-based provider, or by deliberately
passing a different `provider_b` to `run_dual_agent`. Don't read a `"tie"` result under
MockProvider as evidence that two independent agents produce equivalent quality in
general - it's evidence that one deterministic rule engine asked the same question twice
gives the same answer twice, which was never in doubt.

`max_tokens`/`max_cost_usd` apply to the *combined* total across both branches, not each
independently - see "Hard-enforced token/cost budgets" below for why, and note the same
caveat `run_parallel_multi_agent` already carries: once the shared budget is crossed,
HELIX stops waiting on whichever branch(es) haven't finished, but a branch already
executing in its own sandboxed subprocess is not forcibly killed.

**Fixed, not adaptive, by design.** `run_dual_agent` always runs both branches - there is
no difficulty-based gating here. That's deliberate: Experiments C and D (see
`docs/benchmark_methodology.md`) exist specifically to answer "does a second independent
implementation help," which requires a fixed agent count so the comparison isn't
confounded with a gating policy's own decisions. The adaptive question - *when* should
HELIX decide to use one agent vs. two - is Experiment E, implemented separately as
`run_adaptive_agent` - see "Adaptive coordination" below.

## Adaptive coordination (`run_adaptive_agent`, Experiment E)

A deliberately simple, interpretable weighted-sum heuristic over public dataset
metadata (`helix.orchestration.adaptive`), not a learned policy - per the extension
plan's own instruction to start as simple as possible and prove it out before anything
more elaborate. Two halves, matching the design document's own split:

**Static: `estimate_difficulty`.** Five factors, each normalized to `[0, 1]` against a
fixed reference band and combined by fixed weights summing to 1.0: combined error rate
(0.30), indel presence as a binary flag rather than a rate - the project's documented
hardest case (0.20), read-depth adequacy relative to background size, inverted so
*sparse* depth scores *harder* (0.20), background diversity low/medium/high → 0/0.5/1
(0.20), duplicate rate directly (0.10). Every factor is read from
`dataset_metadata.json` alone - target abundance and presence are redacted from that
file by design, so neither factor the original project brief suggested can ever inform
a live, blind decision; they only ever inform post-hoc analysis of a finished sweep.
Four tiers: easy (<0.25), moderate (0.25-0.50), difficult (0.50-0.75), ambiguous
(>0.75) - each with a fixed per-tier iteration ceiling
(`adaptive.TIER_MAX_ITERATIONS`), always capped by the caller's own `max_iterations`.

**Dynamic: escalation inside `run_adaptive_agent`.** `difficult`/`ambiguous` tiers start
with two independent coding agents from iteration 1, running exactly like
`run_dual_agent`. `easy`/`moderate` tiers start with one, running the full Phase 3 role
set (Reviewer, the Verifier Agent, regression detection, a `RevisionCycleRecord` per
cycle) sequentially - and a second, independent coding agent is activated **in a
background thread**, so the first agent's own revision loop keeps running rather than
being discarded, the moment either trigger fires: a verification failure with F1 below
`adaptive.FAR_FAILURE_F1_THRESHOLD` (0.5 - "far," not "close enough for one more
revision alone"), or an execution failure while still running a single agent (treated
as not obviously a simple code bug). Escalation happens **at most once** per run - the
policy's ceiling is two agents, not three. A `"disagreement"` entry is also recorded,
post-hoc, if two already-activated agents detected different targets - for the audit
trail, even though it doesn't itself trigger further action today (verification is
already always-on, and two is already the ceiling).

Every decision is recorded on `ExperimentState`/`ExperimentRecord.coordination_decision`
(`messages.CoordinationDecision`): the difficulty score and tier, the planned iteration
ceiling, the initial and final agent count, and every escalation with its trigger and
reason - "an adaptive run is exactly as auditable as a fixed one," per the design
document. Two agents are never credited as "better" on accuracy alone: the usual
token/cost/iteration accounting on `ExperimentRecord` is unaffected, so a benchmark
comparing `baselines.ADAPTIVE_COORDINATION` (Experiment E) against the fixed
`MULTI_AGENT_WITH_VERIFICATION`/`DUAL_AGENT_WITH_VERIFICATION` baselines (B/D) can weigh
accuracy improvement against the resources actually spent to get it, not just whichever
run scored higher.

**Known scope limit, stated plainly rather than glossed over:** the richer tracking
(Verifier Agent, regression detection, `RevisionCycleRecord`) only applies to the
sequential single-agent phase before any escalation. Once two agents are running -
whether from the start (difficult/ambiguous tiers) or via escalation - both branches use
the same `VerificationAgent`-only scoring `run_dual_agent` does; extending the Phase 3
role split to the dual-agent phase is not yet done.

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

## Hard-enforced token/cost budgets

`run_single_agent`/`run_multi_agent`/`run_parallel_multi_agent` all accept optional
`max_tokens`/`max_cost_usd` (both default `None`, meaning unbounded - identical to prior
behavior for every existing caller). Unlike `max_iterations`/`timeout_seconds`, which were
hard-enforced from Release 0.1, token/cost usage was only ever *tracked* until now - a
pathologically verbose model could in principle run up a large bill before an
iteration/timeout limit kicked in.

Enforcement differs by workflow, because the two workflows have genuinely different
structure, not because one got the lazy version:

- **Single-agent and multi-agent** (`TokenBudget.check`, raises `ExperimentBudgetExceeded`):
  checked after every agent call made inside the revision loop. A run that crosses either
  limit mid-revision stops immediately - it does not finish its current iteration first -
  and the experiment is recorded with `status="budget_exceeded"`, a new terminal status
  alongside `completed`/`failed`/`timed_out`/`cancelled`.
- **Parallel multi-agent** (`TokenBudget.is_exceeded`, checked around the
  `ThreadPoolExecutor` call): there is no iteration boundary to interrupt mid-round - all
  branches run concurrently in real OS subprocesses. Once a branch's tracked usage crosses
  the budget, HELIX stops *waiting on* the remaining branches' results and reports
  `budget_exceeded` promptly, but branches already running in their own sandboxed
  subprocess are not forcibly killed - the same caveat that already applies to
  cancellation here (see Known limitations below). This is a real, documented gap, not a
  silent one: the budget stops HELIX from consuming more results, not from every
  in-flight subprocess finishing its work.

`helix.benchmarking`'s `BenchmarkBudget` (the dataset x configuration x repeat sweep runner)
also accepts `max_tokens`/`max_cost_usd` and applies them identically to every configuration
in a sweep, same as `max_iterations`/`timeout_seconds` already did - so "comparable resource
budgets" now covers token/cost too, not just iteration/time. Both the single-experiment
launch form and the benchmark sweep form in the dashboard expose these as optional fields
(blank = unbounded).

## Known limitations of Release 0.1's agent layer

- `AnthropicProvider` and `GeminiProvider` exist and are wired through the same interface,
  but neither has been run end-to-end in this repository (no funded API key for either was
  used to build this project) - treat both as implemented-but-unverified until a real run is
  recorded for each.
- `run_parallel_multi_agent` is deliberately scoped to one round of concurrent
  exploration with no cross-round revision - a branch that fails review or
  execution is not retried. Combining parallel breadth with iterative depth
  (e.g. N branches, each allowed its own revision cycle) is a natural next
  extension, not attempted here to keep this phase's scope bounded.
- Mid-flight cancellation of `run_parallel_multi_agent` is only checked before
  the concurrent round starts, not once branches are already running in their
  sandboxed subprocesses. The same caveat applies to its token/cost budget: once
  crossed, HELIX stops waiting on remaining branches but does not kill ones
  already running (see "Hard-enforced token/cost budgets" above).
- The benchmark sweep (`scripts/run_benchmark.py`) currently exercises two
  dataset difficulty levels with 2 repeats each; a fuller sweep across every
  documented hard case (closely-related targets, heavy duplication, the
  true-no-target case) through every configuration is straightforward to add
  with the existing `run_benchmark_sweep` machinery but has not been run yet.
