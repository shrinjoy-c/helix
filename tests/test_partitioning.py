from __future__ import annotations

import pytest

from helix.benchmarking.partitioning import partitioned_scenarios
from helix.biology.simulation import write_dataset
from helix.orchestration.adaptive import estimate_difficulty
from helix.orchestration.orchestrator import run_multi_agent
from helix.providers.mock_provider import MockProvider


def test_unknown_tier_raises():
    with pytest.raises(ValueError):
        partitioned_scenarios("nonsense", base_seed=1)


def test_returns_all_three_partitions():
    scenarios = partitioned_scenarios("moderate", base_seed=10)
    assert set(scenarios) == {"dev", "validation", "final_test"}
    for partition, config in scenarios.items():
        assert config.partition == partition


def test_partitions_are_genuinely_different_dataset_instances():
    """Not the same literal dataset relabeled three times - a different
    seed each, so final_test is a real held-out instance, not dev
    under an alias."""
    scenarios = partitioned_scenarios("difficult", base_seed=20)
    seeds = {p: c.seed for p, c in scenarios.items()}
    assert len(set(seeds.values())) == 3


def test_all_three_partitions_still_score_into_the_requested_tier(tmp_path):
    scenarios = partitioned_scenarios("ambiguous", base_seed=30)
    for partition, config in scenarios.items():
        paths = write_dataset(config, tmp_path / partition)
        import json

        metadata = json.loads(paths["dataset_metadata"].read_text())
        assert estimate_difficulty(metadata).tier == "ambiguous"


def test_final_test_partition_actually_enforces_single_shot_end_to_end(tmp_path):
    """Not just a label on the config - running it through a real
    orchestrator workflow genuinely caps the revision loop."""
    scenarios = partitioned_scenarios("easy", base_seed=40)
    paths = write_dataset(scenarios["final_test"], tmp_path / "datasets")
    result = run_multi_agent(
        "exp_partition_final_test", paths["reads_fastq"].parent, "Detect target.", MockProvider(),
        tmp_path / "sandbox", max_iterations=5, timeout_seconds=30,
    )
    assert result.state.max_iterations == 1


def test_dev_and_validation_partitions_keep_the_full_iteration_budget(tmp_path):
    scenarios = partitioned_scenarios("easy", base_seed=50)
    for partition in ("dev", "validation"):
        paths = write_dataset(scenarios[partition], tmp_path / partition)
        result = run_multi_agent(
            f"exp_partition_{partition}", paths["reads_fastq"].parent, "Detect target.", MockProvider(),
            tmp_path / "sandbox", max_iterations=5, timeout_seconds=30,
        )
        assert result.state.max_iterations == 5
