from helix.config.models import (
    AnalysisResult,
    DatasetGenerationConfig,
    EvaluationMetrics,
    ExperimentRecord,
    MatchingConfig,
    TargetSpec,
)
from helix.experiments.store import ExperimentStore


def _record(experiment_id: str) -> ExperimentRecord:
    dataset_config = DatasetGenerationConfig(
        dataset_id="ds1",
        seed=1,
        targets=[TargetSpec(target_id="t1", abundance=0.1, present=True)],
    )
    return ExperimentRecord(
        experiment_id=experiment_id,
        dataset_id="ds1",
        pipeline_name="deterministic_baseline",
        status="completed",
        dataset_config=dataset_config,
        matching_config=MatchingConfig(),
        analysis_result=AnalysisResult(
            dataset_id="ds1",
            pipeline_name="deterministic_baseline",
            predictions=[],
            total_reads_processed=100,
            total_molecules_after_dedup=100,
            runtime_seconds=0.01,
        ),
        evaluation_metrics=EvaluationMetrics(
            dataset_id="ds1",
            pipeline_name="deterministic_baseline",
            true_positives=0,
            false_positives=0,
            false_negatives=1,
            true_negatives=0,
            precision=0.0,
            recall=0.0,
            f1=0.0,
            false_positive_rate=0.0,
            false_negative_rate=1.0,
        ),
        runtime_seconds=0.01,
        created_at="2026-11-21T00:00:00Z",
    )


def test_save_and_load_round_trip(tmp_path):
    store = ExperimentStore(tmp_path / "helix.db")
    record = _record("exp_1")
    store.save(record)

    loaded = store.load("exp_1")
    assert loaded == record


def test_load_missing_returns_none(tmp_path):
    store = ExperimentStore(tmp_path / "helix.db")
    assert store.load("does_not_exist") is None


def test_list_ids_and_by_dataset(tmp_path):
    store = ExperimentStore(tmp_path / "helix.db")
    store.save(_record("exp_1"))
    store.save(_record("exp_2"))

    assert set(store.list_ids()) == {"exp_1", "exp_2"}
    by_dataset = store.list_by_dataset("ds1")
    assert {r.experiment_id for r in by_dataset} == {"exp_1", "exp_2"}


def test_save_is_idempotent_upsert(tmp_path):
    store = ExperimentStore(tmp_path / "helix.db")
    record = _record("exp_1")
    store.save(record)
    store.save(record)
    assert store.list_ids() == ["exp_1"]
