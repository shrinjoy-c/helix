from __future__ import annotations

import json

import pytest

from helix.benchmarking.difficulty_scenarios import DIFFICULTY_SCENARIOS
from helix.biology.simulation import write_dataset
from helix.orchestration.adaptive import estimate_difficulty


@pytest.mark.parametrize("tier", ["easy", "moderate", "difficult", "ambiguous"])
def test_scenario_metadata_scores_into_its_named_tier(tmp_path, tier):
    config = DIFFICULTY_SCENARIOS[tier](f"scenario_{tier}", seed=42)
    paths = write_dataset(config, tmp_path / "datasets")
    metadata = json.loads(paths["dataset_metadata"].read_text())
    estimate = estimate_difficulty(metadata)
    assert estimate.tier == tier


def test_tiers_are_monotonically_increasing_in_difficulty_score(tmp_path):
    scores = {}
    for tier, builder in DIFFICULTY_SCENARIOS.items():
        config = builder(f"order_{tier}", seed=1)
        paths = write_dataset(config, tmp_path / tier)
        metadata = json.loads(paths["dataset_metadata"].read_text())
        scores[tier] = estimate_difficulty(metadata).score
    assert scores["easy"] < scores["moderate"] < scores["difficult"] < scores["ambiguous"]
