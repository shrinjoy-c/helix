from __future__ import annotations

import pytest

from helix.orchestration.adaptive import estimate_difficulty, initial_agent_count


def _metadata(**overrides) -> dict:
    base = {
        "error_model": {"substitution_rate": 0.0, "insertion_rate": 0.0, "deletion_rate": 0.0},
        "read_depth": 2000,
        "n_background_sequences": 50,
        "background_diversity": "medium",
        "duplicate_rate": 0.0,
    }
    base.update(overrides)
    return base


def test_clean_easy_dataset_scores_low_and_tiers_easy():
    metadata = _metadata(read_depth=4000, n_background_sequences=20, background_diversity="low")
    estimate = estimate_difficulty(metadata)
    assert estimate.tier == "easy"
    assert estimate.score < 0.25


def test_high_error_rate_pushes_toward_harder_tiers():
    easy = estimate_difficulty(_metadata())
    harder = estimate_difficulty(_metadata(error_model={"substitution_rate": 0.05, "insertion_rate": 0.0, "deletion_rate": 0.0}))
    assert harder.score > easy.score


def test_indel_presence_is_a_binary_factor_not_a_rate():
    """A tiny nonzero indel rate should contribute the same difficulty as
    a large one - it's the documented hardest case, triggered by presence
    alone, not magnitude."""
    tiny_indels = estimate_difficulty(_metadata(error_model={"substitution_rate": 0.0, "insertion_rate": 0.0001, "deletion_rate": 0.0}))
    large_indels = estimate_difficulty(_metadata(error_model={"substitution_rate": 0.0, "insertion_rate": 0.3, "deletion_rate": 0.0}))
    indel_factor_tiny = next(f for f in tiny_indels.factors if f.name == "indel_presence")
    indel_factor_large = next(f for f in large_indels.factors if f.name == "indel_presence")
    assert indel_factor_tiny.normalized == indel_factor_large.normalized == 1.0


def test_sparse_read_depth_relative_to_background_increases_difficulty():
    adequate = estimate_difficulty(_metadata(read_depth=10000, n_background_sequences=50))
    sparse = estimate_difficulty(_metadata(read_depth=100, n_background_sequences=50))
    assert sparse.score > adequate.score


def test_background_diversity_ordering_is_monotonic():
    low = estimate_difficulty(_metadata(background_diversity="low"))
    medium = estimate_difficulty(_metadata(background_diversity="medium"))
    high = estimate_difficulty(_metadata(background_diversity="high"))
    assert low.score <= medium.score <= high.score


def test_duplicate_rate_contributes_directly():
    no_dupes = estimate_difficulty(_metadata(duplicate_rate=0.0))
    heavy_dupes = estimate_difficulty(_metadata(duplicate_rate=0.5))
    assert heavy_dupes.score > no_dupes.score


def test_worst_case_dataset_tiers_ambiguous():
    metadata = _metadata(
        error_model={"substitution_rate": 0.1, "insertion_rate": 0.05, "deletion_rate": 0.05},
        read_depth=50, n_background_sequences=200, background_diversity="high", duplicate_rate=0.4,
    )
    estimate = estimate_difficulty(metadata)
    assert estimate.tier == "ambiguous"
    assert estimate.score > 0.75


def test_missing_optional_fields_default_gracefully():
    """A dataset_metadata.json predating a field (or a hand-built dict in a
    test) must not raise - only `dataset_id`-shaped presence is assumed."""
    estimate = estimate_difficulty({})
    assert estimate.tier in ("easy", "moderate", "difficult", "ambiguous")


def test_factors_are_interpretable_not_just_a_bare_score():
    estimate = estimate_difficulty(_metadata())
    names = {f.name for f in estimate.factors}
    assert names == {
        "combined_error_rate", "indel_presence", "read_depth_inadequacy",
        "background_diversity", "duplicate_rate",
    }
    assert sum(f.weight for f in estimate.factors) == pytest.approx(1.0)


@pytest.mark.parametrize("tier,expected", [("easy", 1), ("moderate", 1), ("difficult", 2), ("ambiguous", 2)])
def test_initial_agent_count_follows_the_design_doc_split(tier, expected):
    assert initial_agent_count(tier) == expected
