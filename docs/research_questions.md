# Research Questions and Hypotheses

## Central question

When, why, and under what constraints do multi-agent AI systems improve the reliability and
performance of computational biology workflows?

## Research questions

| ID | Question | Addressed by (status) |
|---|---|---|
| RQ1 | Do specialized agents improve bioinformatics analysis accuracy compared with a single agent? | Single-agent and multi-agent workflows **implemented** (`helix.orchestration.orchestrator`); no multi-dataset, repeated-run comparison has been run yet — that's the controlled-benchmark phase, not this one. |
| RQ2 | Does independent code review reduce implementation errors and false positives? | Reviewer agent **implemented**, with a demonstrated real catch (missing indel handling) that gates execution; not yet measured across many datasets/seeds. |
| RQ3 | Does iterative verification improve recovery from failed analyses? | Verification agent + bounded revision loop **implemented**, including recovery from both reviewer rejection and execution failure (tested in `tests/test_orchestration.py`); not yet measured as a statistic across runs. |
| RQ4 | What are the computational and financial costs of agent coordination? | Token usage and estimated cost are tracked per run (`ExperimentState.token_usage`, `.estimated_cost_usd`) with `MockProvider` (free) and `AnthropicProvider` (real pricing) behind the same interface; no comparative cost analysis across configurations has been run yet. |
| RQ5 | Which agent configurations perform best under different data-quality conditions? | Requires the configurable benchmark generator (**generator implemented**) and an ablation runner sweeping conditions (**not yet implemented**). |

Release 0.1 built the scientific core (benchmark + non-LLM baseline); this phase built the
orchestration layer and ran both workflows end to end once, with passing tests for the
specific mechanisms (review-gated execution, objective verification, bounded iteration,
cancellation, timeout, cost tracking) RQ1–RQ4 depend on. None of RQ1–RQ5 has been *answered*
yet in the sense of a statistically meaningful, repeated, multi-condition comparison — that is
explicitly the next phase (the controlled benchmark runner), not a claim made here.

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
