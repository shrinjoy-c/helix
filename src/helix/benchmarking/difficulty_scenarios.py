"""Named dataset scenarios spanning the adaptive policy's four difficulty
tiers (`helix.orchestration.adaptive`), for Phase 5 benchmarking:
"repeated runs across easy/moderate/difficult/ambiguous tiers" per the
extension plan's implementation strategy.

Each scenario's generation parameters were chosen, then verified against
`estimate_difficulty` (see `tests/test_difficulty_scenarios.py`), to
actually land in its named tier - this file is deliberately not "a
dataset that sounds hard"; it's one whose metadata the adaptive policy
itself would score into that tier, so a benchmark sweep across these four
is a genuine test of tier-conditioned behavior, not a vibe.
"""

from __future__ import annotations

from helix.config.models import (
    BackgroundDiversity,
    DatasetGenerationConfig,
    ErrorModel,
    TargetSpec,
)


def easy_scenario(dataset_id: str, seed: int, partition: str = "dev") -> DatasetGenerationConfig:
    return DatasetGenerationConfig(
        dataset_id=dataset_id, seed=seed, partition=partition,
        description="Easy tier: clean reads, shallow background, low diversity.",
        sequence_length=45, read_depth=5000,
        targets=[TargetSpec(target_id="target_a", abundance=0.1, present=True)],
        n_background_sequences=20, background_diversity=BackgroundDiversity.LOW,
        error_model=ErrorModel(substitution_rate=0.0),
        duplicate_rate=0.0,
    )


def moderate_scenario(dataset_id: str, seed: int, partition: str = "dev") -> DatasetGenerationConfig:
    return DatasetGenerationConfig(
        dataset_id=dataset_id, seed=seed, partition=partition,
        description="Moderate tier: a small indel rate, moderate background, some duplication.",
        sequence_length=45, read_depth=1200,
        targets=[TargetSpec(target_id="target_a", abundance=0.1, present=True)],
        n_background_sequences=50, background_diversity=BackgroundDiversity.MEDIUM,
        error_model=ErrorModel(substitution_rate=0.01, insertion_rate=0.001, deletion_rate=0.0),
        duplicate_rate=0.1,
    )


def difficult_scenario(dataset_id: str, seed: int, partition: str = "dev") -> DatasetGenerationConfig:
    return DatasetGenerationConfig(
        dataset_id=dataset_id, seed=seed, partition=partition,
        description="Difficult tier: meaningful indel rate, sparse reads vs. a large diverse background.",
        sequence_length=45, read_depth=300,
        targets=[TargetSpec(target_id="target_a", abundance=0.08, present=True)],
        n_background_sequences=100, background_diversity=BackgroundDiversity.HIGH,
        error_model=ErrorModel(substitution_rate=0.02, insertion_rate=0.01, deletion_rate=0.0),
        duplicate_rate=0.1,
    )


def ambiguous_scenario(dataset_id: str, seed: int, partition: str = "dev") -> DatasetGenerationConfig:
    return DatasetGenerationConfig(
        dataset_id=dataset_id, seed=seed, partition=partition,
        description="Ambiguous/noisy tier: heavy error rate with indels, very sparse reads, heavy duplication.",
        sequence_length=45, read_depth=80,
        targets=[TargetSpec(target_id="target_a", abundance=0.08, present=True)],
        n_background_sequences=250, background_diversity=BackgroundDiversity.HIGH,
        error_model=ErrorModel(substitution_rate=0.08, insertion_rate=0.03, deletion_rate=0.03),
        duplicate_rate=0.35,
    )


DIFFICULTY_SCENARIOS = {
    "easy": easy_scenario,
    "moderate": moderate_scenario,
    "difficult": difficult_scenario,
    "ambiguous": ambiguous_scenario,
}
