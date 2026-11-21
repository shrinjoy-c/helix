"""End-to-end demonstration of the HELIX Release 0.1 scientific core.

For each scenario: generate a synthetic dataset -> run the deterministic
baseline against the dataset's *public* directory only -> independently
load ground truth and evaluate -> persist an ExperimentRecord to SQLite.

Run with:
    python scripts/run_example_experiment.py
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from helix.biology.evaluation import evaluate, load_ground_truth
from helix.biology.matching import analyze_dataset
from helix.biology.simulation import write_dataset
from helix.config.models import (
    BackgroundDiversity,
    DatasetGenerationConfig,
    ErrorModel,
    ExperimentRecord,
    MatchingConfig,
    TargetSpec,
)
from helix.experiments.store import ExperimentStore

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASETS_DIR = REPO_ROOT / "datasets" / "generated"
DB_PATH = REPO_ROOT / "experiments" / "results" / "helix.db"


def _scenario_easy() -> DatasetGenerationConfig:
    return DatasetGenerationConfig(
        dataset_id="example_001_easy_moderate_target",
        seed=42,
        description="Moderate-abundance target, low error rate, some duplicates.",
        sequence_length=45,
        read_depth=5000,
        targets=[TargetSpec(target_id="target_a", abundance=0.05, present=True, label="moderate")],
        n_background_sequences=100,
        background_diversity=BackgroundDiversity.MEDIUM,
        error_model=ErrorModel(substitution_rate=0.01, insertion_rate=0.001, deletion_rate=0.001),
        duplicate_rate=0.15,
    )


def _scenario_no_target_present() -> DatasetGenerationConfig:
    return DatasetGenerationConfig(
        dataset_id="example_002_no_target_present",
        seed=43,
        description="Reference panel is searched for but genuinely absent from the sample "
        "(true negative control). A reliable pipeline must report absence, not force a detection.",
        sequence_length=45,
        read_depth=5000,
        targets=[TargetSpec(target_id="target_a", sequence="A" * 45, abundance=0.0, present=False)],
        n_background_sequences=150,
        background_diversity=BackgroundDiversity.HIGH,
        error_model=ErrorModel(substitution_rate=0.01, insertion_rate=0.001, deletion_rate=0.001),
        duplicate_rate=0.1,
    )


def _scenario_hard_rare_with_confuser() -> DatasetGenerationConfig:
    return DatasetGenerationConfig(
        dataset_id="example_003_hard_rare_target_with_confuser",
        seed=44,
        description="Rare target (1%) hidden among a diverse background, plus a deliberate "
        "near-match background sequence (edit distance 1) designed to trigger false positives, "
        "under a higher sequencing-error rate.",
        sequence_length=45,
        read_depth=8000,
        targets=[TargetSpec(target_id="target_a", abundance=0.01, present=True, label="rare")],
        n_background_sequences=300,
        background_diversity=BackgroundDiversity.HIGH,
        error_model=ErrorModel(substitution_rate=0.02, insertion_rate=0.002, deletion_rate=0.002),
        duplicate_rate=0.2,
        near_match_background_count=10,
        near_match_edit_distance=1,
    )


SCENARIOS = [_scenario_easy, _scenario_no_target_present, _scenario_hard_rare_with_confuser]


def run_scenario(config: DatasetGenerationConfig, store: ExperimentStore) -> None:
    print(f"\n=== {config.dataset_id} ===")
    print(config.description)

    paths = write_dataset(config, DATASETS_DIR)
    dataset_dir = paths["reads_fastq"].parent
    print(f"Public files written to: {dataset_dir}")
    print(f"Ground truth written to: {paths['ground_truth']} (not read by the analysis step)")

    matching_config = MatchingConfig(max_mismatches=1, collapse_duplicates=True, min_count=2)

    start = time.monotonic()
    result = analyze_dataset(dataset_dir, matching_config)
    runtime = time.monotonic() - start

    ground_truth = load_ground_truth(dataset_dir)
    metrics = evaluate(result, ground_truth)

    print(
        f"predictions: {[(p.target_id, p.detected, round(p.estimated_frequency, 4)) for p in result.predictions]}"
    )
    print(
        f"precision={metrics.precision:.2f} recall={metrics.recall:.2f} f1={metrics.f1:.2f} "
        f"fp_rate={metrics.false_positive_rate:.2f} fn_rate={metrics.false_negative_rate:.2f} "
        f"abundance_mae={metrics.abundance_mae}"
    )
    print(f"runtime: {runtime:.3f}s")

    record = ExperimentRecord(
        experiment_id=f"exp_{uuid.uuid4().hex[:12]}",
        dataset_id=config.dataset_id,
        pipeline_name=result.pipeline_name,
        status="completed",
        dataset_config=config,
        matching_config=matching_config,
        analysis_result=result,
        evaluation_metrics=metrics,
        runtime_seconds=runtime,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    store.save(record)
    print(f"Saved experiment record: {record.experiment_id}")


def main() -> None:
    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    with ExperimentStore(DB_PATH) as store:
        for scenario_fn in SCENARIOS:
            run_scenario(scenario_fn(), store)
        print(f"\nAll experiments persisted to: {DB_PATH}")
        print(f"Experiment IDs: {store.list_ids()}")


if __name__ == "__main__":
    main()
