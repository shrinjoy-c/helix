"""The benchmark runner: a dataset x configuration x repeat grid, with a
single shared resource budget enforced across every cell, persisting
every run (not just the successful ones) via ExperimentStore.

Each dataset is generated exactly once (seeded, deterministic) and reused
across every configuration and repeat that targets it, so differences
between configurations can never be explained by differences in the data
they saw.
"""

from __future__ import annotations

from pathlib import Path

from helix.agents.verifier import AcceptanceCriteria
from helix.benchmarking.baselines import BenchmarkBudget, PipelineSpec, new_experiment_id, run_pipeline
from helix.biology.simulation import write_dataset
from helix.config.models import DatasetGenerationConfig, ExperimentRecord
from helix.experiments.store import ExperimentStore
from helix.providers.base import LLMProvider


def run_benchmark_sweep(
    dataset_configs: list[DatasetGenerationConfig],
    pipeline_specs: list[PipelineSpec],
    provider: LLMProvider,
    datasets_root: str | Path,
    sandbox_root: str | Path,
    store: ExperimentStore,
    budget: BenchmarkBudget | None = None,
    criteria: AcceptanceCriteria | None = None,
    n_repeats: int = 1,
) -> list[ExperimentRecord]:
    """Run every (dataset, pipeline) cell `n_repeats` times, persist each
    run immediately (so a crash partway through loses nothing already
    completed), and return the full flat list of records for aggregation.
    """
    budget = budget or BenchmarkBudget()
    criteria = criteria or AcceptanceCriteria()
    records: list[ExperimentRecord] = []

    for dataset_config in dataset_configs:
        paths = write_dataset(dataset_config, datasets_root)
        dataset_dir = paths["reads_fastq"].parent

        for spec in pipeline_specs:
            for repeat_index in range(n_repeats):
                experiment_id = new_experiment_id(spec.name)
                record = run_pipeline(
                    spec=spec,
                    experiment_id=experiment_id,
                    dataset_dir=dataset_dir,
                    dataset_config=dataset_config,
                    provider=provider,
                    sandbox_root=sandbox_root,
                    budget=budget,
                    criteria=criteria,
                )
                record = record.model_copy(update={"repeat_index": repeat_index})
                store.save(record)
                records.append(record)

    return records
