# Research Questions and Hypotheses

## Central question

When, why, and under what constraints do multi-agent AI systems improve the reliability and
performance of computational biology workflows?

## Research questions

| ID | Question | Addressed by (status) |
|---|---|---|
| RQ1 | Do specialized agents improve bioinformatics analysis accuracy compared with a single agent? | Requires agent orchestration (planned) |
| RQ2 | Does independent code review reduce implementation errors and false positives? | Requires Reviewer agent (planned) |
| RQ3 | Does iterative verification improve recovery from failed analyses? | Requires Verification agent + iteration loop (planned) |
| RQ4 | What are the computational and financial costs of agent coordination? | Requires provider-layer token/cost tracking (planned) |
| RQ5 | Which agent configurations perform best under different data-quality conditions? | Requires the configurable benchmark generator (**generator implemented**; ablation runner planned) |

Release 0.1 does not yet answer any of RQ1–RQ5: it builds the scientific instrument the later
phases need to answer them with — a deterministic, ground-truth-backed benchmark and a
non-LLM baseline to compare every future agent configuration against.

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

None of these hypotheses have been tested yet — no agents exist in the codebase as of
Release 0.1. They are recorded here so that later results can be reported against
pre-registered expectations rather than hypotheses chosen after seeing the data. Negative or
null results (e.g., the deterministic baseline outperforming an agent configuration on a given
task) are an expected and acceptable outcome, not a failure of the project.

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
