# Research Questions and Hypotheses

## Central question

When, why, and under what constraints do multi-agent AI systems improve the reliability and
performance of computational biology workflows?

## Research questions

| ID | Question | Addressed by (status) |
|---|---|---|
| RQ1 | Do specialized agents improve bioinformatics analysis accuracy compared with a single agent? | **Measured, preliminarily**: `scripts/run_benchmark.py` runs single-agent, sequential, and full multi-agent configurations across two datasets with repeats. Under `MockProvider` on these two datasets, single-agent, the full multi-agent system, and the deterministic baseline all reach F1=1.0 - no accuracy advantage observed *yet*, because these datasets aren't hard enough to separate them on accuracy alone. See RQ3 for where they *do* separate (reliability, not accuracy). |
| RQ2 | Does independent code review reduce implementation errors and false positives? | **Measured**: the Reviewer agent caught a real, reproducible code defect (missing indel handling) in 100% of first-draft code versions across both datasets tested (`tests/test_agents.py::test_naive_code_misses_indel_shifted_read_but_revised_code_catches_it` proves the defect is real, not cosmetic). It did not change final accuracy on these two datasets (the defect didn't happen to matter at their indel rates) - a clean example of catching a real issue that doesn't always show up in the headline metric. |
| RQ3 | Does iterative verification improve recovery from failed analyses? | **Measured, and this is the clearest signal so far**: configurations with no revision loop (`sequential_multi_agent`, `parallel_multi_agent`, the `no_verification_loop` ablation) failed **100% of the time** on both benchmark datasets, while every configuration retaining a revision loop succeeded 100% of the time. See `docs/agent_design.md`'s "genuine, reproducible failure finding." |
| RQ4 | What are the computational and financial costs of agent coordination? | Token usage and cost are tracked per run and reported in every benchmark summary (`BenchmarkGroupSummary.total_tokens`, `.estimated_cost_usd`). With `MockProvider` cost is always $0 and token counts are a rough proxy only (see `docs/agent_design.md`) - a real cost comparison requires running a funded real-model provider (`AnthropicProvider` or
`GeminiProvider`), which has not been done yet. |
| RQ5 | Which agent configurations perform best under different data-quality conditions? | **Partially measured**: two difficulty levels (moderate abundance/low error vs. rare target/higher indel rate) show the *same* pattern (revision-loop presence dominates) in this run - suggesting the mechanism matters more than the data condition tested so far, but only two conditions have been tried. `helix.benchmarking.experiments.run_benchmark_sweep` accepts an arbitrary list of dataset configs, so extending this is adding datasets, not new code. |

This phase's result is genuinely informative but narrower than it might look: on the two
datasets tested, **the presence of a revision loop was the single dominant factor** - not which
specific roles (Reviewer vs. Experiment Designer) were present, and not which workflow shape
(sequential vs. parallel) was used. That is itself a real, falsifiable finding (H5 is partially
supported: removing the loop matters enormously; removing the Reviewer or Experiment Designer
alone did not, on these datasets). It is *not* evidence that review/design agents don't matter
in general - it's evidence that, under `MockProvider`'s specific code-writing policy and these
two datasets' specific error profiles, the loop was the load-bearing mechanism. A broader sweep
(more datasets, more repeats, a real model provider) is needed before drawing general
conclusions, and `helix.benchmarking` is built specifically to make running that sweep cheap.

## Hypotheses

- **H1**: Multi-agent systems with independent verification will produce fewer undetected
  implementation errors than single-agent systems.
- **H2**: Independent code review will improve performance on datasets with higher
  sequencing-error rates.
- **H3**: Multi-agent systems will require more tokens and execution time than single-agent
  systems.
- **H4**: The benefits of coordination will depend on task difficulty and dataset
  characteristics.
- **H5**: Removing specific agents will reveal which roles contribute meaningfully to
  performance.

Preliminary read against the first benchmark sweep (two datasets, `MockProvider`, 2 repeats -
see `scripts/run_benchmark.py`):

- **H1**: Not supported on these two datasets - the deterministic baseline matched the full
  multi-agent system's accuracy exactly (both F1=1.0). Multi-agent's advantage, if any, has not
  yet been isolated from "the task was easy enough that nothing failed."
- **H2**: Not yet testable from accuracy alone - the Reviewer caught a real defect that didn't
  happen to affect these datasets' final scores (see RQ2). A dataset where the indel-handling
  gap actually changes the outcome is needed to test this properly.
- **H3**: Supported directionally (multi-agent made 6-7 provider calls per run vs. 1-2 for
  single-agent on these datasets; see token_usage in the benchmark report) - not yet measured
  against wall-clock cost with a real, latency-bearing provider.
- **H4**: Supported: the revision-loop ablations behaved identically across both difficulty
  levels tested, while review/design ablations didn't change the outcome on either - task
  difficulty (at least these two levels) did not change *which* mechanism mattered.
- **H5**: Partially supported - removing the verification loop mattered enormously; removing
  the Reviewer or Experiment Designer alone did not, on these two datasets (see RQ5 note above).

These are preliminary, two-dataset, single-provider readings, recorded as a first pass against
pre-registered expectations - not a final answer to any hypothesis. Negative or null results
(e.g., the deterministic baseline matching every agent configuration on these particular
datasets) are reported as-is, not treated as a failure of the project.

## A note on what "improve accuracy" can mean here

Because the benchmark's reference panel is always visible to an analysis pipeline but target
*presence* and *abundance* are hidden ground truth, "accuracy" decomposes into at least two
genuinely different things: (a) whether a pipeline correctly calls presence/absence
(precision/recall/F1 over detection), and (b) how close its abundance estimate is to the true
frequency. A pipeline can score well on one and poorly on the other (Release 0.1's example
scenario 3 — a rare target plus a one-nucleotide-away background confuser — already shows the
deterministic baseline getting presence/absence right while its abundance estimate is
inflated by the confuser). Future agent evaluations should report both, not just a single
composite score.
