# Benchmark Methodology

## Scope of this document

This describes the synthetic immune-repertoire sequencing benchmark: the dataset generator,
the deterministic baseline, the evaluator, and (as of the controlled-benchmark phase) the
multi-configuration sweep runner, ablations, repeated-run statistics, and cost tracking.

## Important scope statement

All sequences in this benchmark are **fully synthetic**. They are not derived from, matched
to, or validated against real patient samples. Nothing in this repository is a validated
clinical diagnostic tool, and results produced by it must not be described as clinical MRD
(minimal residual disease) detection performance. It is a research benchmark for studying
analysis-pipeline and agent behavior under controlled, known ground truth.

## What a "sequence" is in this benchmark

A simulated sequence is an opaque nucleotide string (default length 45, alphabet ACGT)
standing in for a CDR3-like region. There is no V(D)J gene model, no translation to amino
acids, and no attempt to mimic real germline gene usage frequencies. This is a deliberate
simplification: it lets the benchmark isolate detection/matching/abundance-estimation
performance under controlled noise, without requiring (or implying) a biologically faithful
repertoire model.

## Generation parameters (`DatasetGenerationConfig`)

| Parameter | Meaning |
|---|---|
| `seed` | Fixes every random draw; identical config + seed → byte-identical dataset. |
| `sequence_length` | Length of generated sequences. |
| `read_depth` | Total number of simulated reads. |
| `targets` | List of `TargetSpec`: `target_id`, optional explicit `sequence`, `abundance` (relative proportion of template molecules), `present` (whether it truly occurs at all). |
| `n_background_sequences` | Number of distinct background template sequences. |
| `background_diversity` | `low` / `medium` / `high` — controls how clustered the distinct background sequences are around a handful of seeds (low diversity = many near-identical background variants; high = fully independent). |
| `error_model` | Per-base i.i.d. substitution / insertion / deletion rates. |
| `duplicate_rate` | Expected fraction of reads that are repeat observations of an already-sampled template (same barcode, independent sequencing errors per read). |
| `near_match_background_count` / `near_match_edit_distance` | Deliberately construct background sequences that are a small, controlled edit distance from a target — the "abundant background sequence resembling a target" hard case. |
| `partition` | `dev` (default) / `validation` / `final_test` — see "Dataset partitions" below. |

A `TargetSpec` with `present=False` models a dataset where the reference panel is searched
for but the target genuinely is not in the sample — the "correct answer is no target" case
the project spec calls out as important, because a system that always reports a detection is
not a reliable one.

## Dataset partitions

Every `DatasetGenerationConfig` carries a `partition` field, recorded in both the private
full config and the public `dataset_metadata.json` (knowing *which* partition a dataset
belongs to isn't itself sensitive — it's experimental bookkeeping, not a ground-truth value).
The ground-truth trust boundary described above is identical across all three; what differs
is the orchestrator's **feedback policy**:

| Partition | Ground truth hidden? | Iterative feedback during a run? |
|---|---|---|
| `dev` (default) | Yes | Yes — today's existing revision-loop behavior, unchanged. |
| `validation` | Yes | Yes — behaviorally identical to `dev` right now (see limitation below). |
| `final_test` | Yes | **No.** `max_iterations` is forced to 1 regardless of what the caller requests. |

`final_test` exists specifically to close a real leakage vector found in the Phase 1
architecture audit: the revision loop already feeds `validation_result.message`
(precision/recall-derived text) back to the Implementation Agent on every failed iteration.
That's fine for development - it's exactly what "no iterative feedback" must prevent once a
result is meant to represent genuine, unseen-data performance. `final_test` doesn't just
soften that feedback text, it removes the revision loop entirely: whatever a frozen
implementation produces on its one attempt is the recorded result, full stop. Enforced in
`helix.orchestration.orchestrator._partition_capped_max_iterations`, logged to the event log
as an `ablation_applied` entry (`ablation="final_test_single_shot"`) whenever it actually caps
a run, and covered by
`tests/test_orchestration.py::test_final_test_partition_forces_single_shot_for_multi_agent`.

**Known limitation:** the original design called for `dev` to additionally grant agents direct
label access for debugging. That would weaken a load-bearing invariant this project has
maintained since Release 0.1 (ground truth never reaches an agent-visible path) and was
deliberately not implemented without a separate decision - `dev` and `validation` are
currently identical in behavior.

`helix.benchmarking.partitioning.partitioned_scenarios(tier, base_seed)` builds the full
dev/validation/final_test triple for one of the four difficulty tiers below - three genuinely
different dataset instances (different seeds derived from one `base_seed`), not the same
dataset relabeled three times, since reusing one seed across partitions would make
`final_test` meaningless. This closes the "not yet built on top of the enforcement
primitive" gap for *generating* a partitioned experiment; the "evaluate `final_test` only
once, after configurations are frozen" half of §9/§10's workflow is a process discipline this
function can describe but can't itself enforce - deliberately not invoked from
`scripts/run_adaptive_coordination_benchmark.py`, which is a `dev`-partition demonstration,
not a frozen final evaluation.

## Difficulty cases implemented as reusable building blocks

The generator and `helix.biology.sequences` provide the primitives for every hard case named
in the project brief:

- **Rare target in a large background** — set a small `abundance` with a large `n_background_sequences` / `read_depth`.
- **Two targets one nucleotide apart** — construct a second `TargetSpec.sequence` with `make_near_match(first_target_sequence, edit_distance=1, rng)`.
- **Abundant background resembling a target** — `near_match_background_count` + `near_match_edit_distance`.
- **Heavy duplication** — high `duplicate_rate`.
- **High sequencing-error target** — high `error_model` rates.
- **True no-target dataset** — `TargetSpec(present=False)`.

`scripts/run_example_experiment.py` currently exercises three of these combinations end to
end (moderate target, true-negative control, and rare-target-with-confuser-under-higher-error).
A fuller library of named benchmark configs (one JSON file per hard case, under
`datasets/benchmark_configs/`) is planned for Release 0.5 alongside the multi-configuration
benchmark runner.

## Deterministic baseline

`helix.biology.matching.analyze_dataset` is a conventional, non-LLM pipeline:

1. Read the public `reads.fastq` and `reference_panel.fasta` only (never ground truth).
2. Optionally collapse reads sharing a barcode into one "molecule" via majority-vote consensus
   sequence (`MatchingConfig.collapse_duplicates`).
3. Assign each molecule to the closest reference target within `max_mismatches` (Hamming
   distance for equal-length sequences, a bounded Levenshtein distance otherwise), or leave it
   unassigned.
4. A target is called "detected" if its matched-molecule count meets `min_count`; its estimated
   frequency is matched-molecules / total-molecules.

This baseline is intentionally simple and auditable. It is **not** a state-of-the-art
repertoire aligner, and its purpose in the project is to be the non-LLM point of comparison
every future agent configuration is measured against — not to be the best possible pipeline.

## Independent evaluation

`helix.biology.evaluation.evaluate` compares an `AnalysisResult` to a separately-loaded
`GroundTruth` and computes, per target and aggregated: true/false positives and negatives,
precision, recall, F1, false-positive rate, false-negative rate, and abundance mean absolute
error (over truly-present targets only — an absent target has no "true frequency" to be
inaccurate about). A dataset where no target is present and the pipeline correctly reports
no detections scores a perfect precision/recall, rather than being penalized for "finding
nothing."

## The controlled benchmark runner

`helix.benchmarking` implements the configurations (A-I) named in the project's original
design doc, extended with G/H/I for the extension plan's additions. **This lettering
predates, and is unrelated to, the extension plan's "Experiment A-E" framework**
(single-agent / single+verification / dual-agent / dual+verification / adaptive) - the two
use the same letters for genuinely different things. Where this document says
"configuration," it means a row in the table below; where the extension plan says
"Experiment," it means one of its five research conditions. G, H, and I below *are*
Experiments C, D, and E under that other framework - noted explicitly at the row, not left
to be inferred.

| | Configuration | Module |
|---|---|---|
| A | Deterministic baseline | `baselines.DETERMINISTIC_BASELINE` |
| B | Single-agent | `baselines.SINGLE_AGENT` |
| C | Sequential multi-agent (no revision loop) | `baselines.SEQUENTIAL_MULTI_AGENT` |
| D | Parallel multi-agent | `baselines.PARALLEL_MULTI_AGENT` |
| E | Multi-agent with independent verification and feedback | `baselines.MULTI_AGENT_WITH_VERIFICATION` |
| F | Ablations (remove Reviewer / Experiment Designer / the revision loop / the Verifier Agent's interpretation step, one at a time) | `ablations.ABLATION_CONFIGURATIONS` |
| G | Dual-agent, no reviewer or verification loop - extension plan's **Experiment C** | `baselines.DUAL_AGENT_NO_VERIFICATION` |
| H | Dual-agent with reviewer and verification loop - extension plan's **Experiment D** | `baselines.DUAL_AGENT_WITH_VERIFICATION` |
| I | Adaptive coordination - HELIX decides 1 or 2 agents itself - extension plan's **Experiment E** | `baselines.ADAPTIVE_COORDINATION` |

G and H always run both independent coding agents - fixed, not gated by difficulty - so a
sweep across them and B/E (the single-agent equivalents) isolates the effect of agent count
under identical conditions. See `docs/agent_design.md`'s "Independent coding agents" for the
isolation mechanism and its honest MockProvider-convergence caveat. I is the same mechanism
as Experiment E - see `docs/agent_design.md`'s "Adaptive coordination" for the difficulty
scorer and escalation policy it runs on.

## Experiment A-E: explicit, unambiguous names (Phase 5)

A Phase 1 gap-analysis finding, now closed: Experiment A ("single agent, no verification")
had no clean existing config to point at, since `SINGLE_AGENT`/B above always runs the
revision loop. `run_single_agent` gained its own `enable_verification_loop` flag (mirroring
the one `run_multi_agent` already had), and `baselines.EXPERIMENT_A_TO_E` is the extension
plan's Decision 3 grid made literal - five configurations named exactly
`experiment_a_single_no_verification` through `experiment_e_adaptive`, run through the same
`run_pipeline` dispatcher as everything else:

| | Experiment | Module | Agent count |
|---|---|---|---|
| A | Single agent, no verification | `baselines.EXPERIMENT_A_SINGLE_NO_VERIFICATION` | always 1 |
| B | Single agent + verification | `baselines.EXPERIMENT_B_SINGLE_WITH_VERIFICATION` | always 1 |
| C | Dual agent, no verification | `baselines.EXPERIMENT_C_DUAL_NO_VERIFICATION` | always 2 |
| D | Dual agent + verification | `baselines.EXPERIMENT_D_DUAL_WITH_VERIFICATION` | always 2 |
| E | Adaptive coordination | `baselines.EXPERIMENT_E_ADAPTIVE` | 1 or 2, decided by HELIX |

`helix.benchmarking.difficulty_scenarios.DIFFICULTY_SCENARIOS` provides one dataset-generation
function per tier (`easy`/`moderate`/`difficult`/`ambiguous`) the adaptive policy itself
recognizes - each verified, in `tests/test_difficulty_scenarios.py`, to actually score into
its named tier via `estimate_difficulty`, not just "sound hard." Running
`scripts/run_adaptive_coordination_benchmark.py` sweeps the A-E grid across all four tiers
with repeats and reports, per tier, what Experiment E actually chose (initial/final agent
count, any escalations) alongside its F1 against the fixed B/D baselines - a genuine,
reproducible finding from one such run on `MockProvider`: on the `ambiguous` tier, A/B/C all
failed outright (0% success) while D and E (which both started with two agents) succeeded -
evidence that, for that specific hard case under `MockProvider`'s code-writing policy, a
second independent attempt mattered more than verification alone. This is not evidence it
mattered because it was adaptive - E matched D, a fixed configuration, not beat it; re-running
with a real provider could produce different, and equally legitimate, results.

Every configuration is invoked through one dispatcher, `baselines.run_pipeline(spec, ...)`,
given the same `BenchmarkBudget` (`max_iterations`, `timeout_seconds`, `max_branches`) and the
same `AcceptanceCriteria` - fairness (section 11 of the project spec: same dataset, same
budget, no unlimited retries for one configuration while another gets one attempt) is a
structural property of the dispatcher signature, not something a caller can forget.
`experiments.run_benchmark_sweep` generates each dataset exactly once and reuses it across
every configuration and repeat that targets it, then persists every run - including failed
ones - via `ExperimentStore`.

## Reporting

`helix.benchmarking.metrics.aggregate_runs` groups persisted runs by `(dataset_id,
pipeline_name)` and reports mean + standard deviation (never a single best run) for precision,
recall, F1, false-positive/negative rate, abundance MAE, runtime, iteration count, token usage,
and estimated cost. `compare_configurations` runs a two-sample Welch's t-test (via SciPy)
between two configurations' repeated runs on one dataset, and explicitly refuses to report a
significance result from fewer than two runs per side, or fabricate one when both samples have
zero variance (expected with the deterministic `MockProvider` - see `docs/agent_design.md`).
`helix.reporting` exports machine-readable JSON/CSV and a Markdown report
(`reports.py`), plus Plotly comparison charts (`plots.py`): F1 by configuration, precision vs.
recall, runtime, and cost. `scripts/run_benchmark.py` is the worked, reproducible example.

## Fairness principles

Every benchmark configuration compared against another uses the same dataset, the same seed,
and the same declared resource budget (iteration limit, time limit, and - since
`max_tokens`/`max_cost_usd` were added to `BenchmarkBudget` - token/cost limit too, all
applied identically across every configuration in a sweep). Results are reported as means
and variability across repeated runs, never best-of-N.

## Known limitations of the benchmark runner

- Iteration count, wall-clock time, and (optionally) token/cost are all hard-enforced
  budgets for every sweep (`docs/agent_design.md`'s "Hard-enforced token/cost budgets").
  Token/cost enforcement is still off by default (`max_tokens`/`max_cost_usd` default to
  `None`/unbounded on `BenchmarkBudget`, same as the orchestrator) - a sweep that doesn't
  set them explicitly gets exactly the prior tracked-not-capped behavior.
- `scripts/run_benchmark.py`'s own results (two datasets, `MockProvider`, 2 repeats) show
  every single-shot configuration (C, D, and the `no_verification_loop` ablation) failing
  outright - this is a real, reproducible finding under this provider's specific code-writing
  policy (see `docs/agent_design.md`), not evidence that parallel/single-shot approaches are
  inherently worse in general. Re-running with a real model provider could produce different,
  and equally legitimate, results.
- Repeated runs under `MockProvider` have zero variance by construction (it's deterministic),
  so `compare_configurations` will report "undefined" rather than a p-value whenever both
  configurations being compared used it - this is expected, not a bug, and resolves itself
  once a real, stochastic provider is used.
