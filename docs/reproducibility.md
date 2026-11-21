# Reproducibility

## Determinism guarantee

`helix.biology.simulation.generate_dataset(config)` uses a single `numpy.random.Generator`
seeded from `config.seed` and consumes it in a fixed order. Two calls with an identical
`DatasetGenerationConfig` (field-for-field, including `seed`) produce byte-identical reads
and an identical `GroundTruth` object. This is directly tested in
`tests/test_simulation.py::test_generation_is_deterministic_given_seed`.

`helix.biology.simulation.config_hash(config)` is a SHA-256 hash (truncated to 16 hex
characters) of the config's canonical JSON serialization (sorted keys). It is stored inside
every `GroundTruth` record and lets a researcher confirm after the fact that a given ground
truth file actually corresponds to a given saved config, without re-running generation.

## Trust boundary between agent-visible data and ground truth

Every generated dataset is written as:

```
datasets/generated/<dataset_id>/
├── reads.fastq                 # public
├── reference_panel.fasta       # public — target_id + sequence only, no abundance/presence
├── dataset_metadata.json       # public — redacted config (no abundance, no presence)
└── _ground_truth/
    ├── ground_truth.json       # private — true counts, frequencies, presence, read origins
    └── full_config.json        # private — the complete DatasetGenerationConfig
```

- `helix.biology.matching.analyze_dataset(dataset_dir, ...)` only ever opens `reads.fastq` and
  `reference_panel.fasta`. It has no code path that reads `_ground_truth/`.
- `helix.biology.evaluation` is the only module that calls `load_ground_truth`, and it is only
  ever invoked after an `AnalysisResult` already exists — it scores a finished answer, it does
  not inform the analysis.
- `dataset_metadata.json` is deliberately redacted: any field that would reveal which targets
  are actually present, or at what abundance, is excluded. `tests/test_simulation.py::test_write_dataset_separates_ground_truth`
  asserts the literal strings `"abundance"` and `"present"` do not appear in that file.

This separation is currently enforced at the API and directory level (what code calls what,
and what's physically written where), not by filesystem permissions or a sandboxed process.
That stronger enforcement is planned for the agent-execution phase, where untrusted,
model-generated code is the thing being isolated — see `docs/architecture.md`.

## Reproducing an experiment

Every `ExperimentRecord` persisted via `helix.experiments.store.ExperimentStore` embeds the
complete `dataset_config` it was run against (plus the `matching_config`, if applicable).
Regenerating the same dataset and re-running the same pipeline against it is:

```python
from helix.biology.simulation import write_dataset
from helix.biology.matching import analyze_dataset
from helix.experiments.store import ExperimentStore

store = ExperimentStore("experiments/results/helix.db")
record = store.load("exp_...")

paths = write_dataset(record.dataset_config, "/tmp/reproduced")
result = analyze_dataset(paths["reads_fastq"].parent, record.matching_config)
```

Because generation is seeded and the baseline is deterministic, `result` will match the
original `record.analysis_result` field-for-field (floating-point runtime fields aside).
`scripts/run_example_experiment.py` is the canonical worked example of the full
generate → analyze → evaluate → persist loop.

## Known limitations of Release 0.1

- Reproducibility across *machine/numpy-version* changes is not yet tested — only
  same-process, same-run determinism is verified.
- There is no CI workflow yet verifying tests pass on a clean checkout (planned for Release 0.5).
- The deterministic baseline's edit-distance matching is unoptimized (plain DP); dataset sizes
  much larger than the example scenarios (tens of thousands of reads × many targets) will be slow.
