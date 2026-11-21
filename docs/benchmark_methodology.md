# Benchmark Methodology

## Scope of this document

This describes the synthetic immune-repertoire sequencing benchmark as implemented in
Release 0.1: the dataset generator, the deterministic baseline, and the evaluator. The
controlled multi-configuration benchmark runner (baselines B–F in the full project spec,
repeated-run statistics, ablations, cost tracking) is a later-phase extension and is not yet
built; this document is updated as those pieces land.

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

A `TargetSpec` with `present=False` models a dataset where the reference panel is searched
for but the target genuinely is not in the sample — the "correct answer is no target" case
the project spec calls out as important, because a system that always reports a detection is
not a reliable one.

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

## Fairness principles carried forward to later phases

Every benchmark configuration compared against another must use the same dataset, the same
seed, and declared, comparable resource budgets (model, token limit, iteration limit, time
limit). Results must be reported as means and variability across repeated runs, not
best-of-N. None of this is enforced by code yet in Release 0.1 (there is only one pipeline to
compare, the deterministic baseline) — it is recorded here as a binding constraint on the
benchmark runner built in the next phase.
