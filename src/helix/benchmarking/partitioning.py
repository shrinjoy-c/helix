"""Builds the dev/validation/final_test triple for one difficulty
scenario - the piece `docs/benchmark_methodology.md`'s "Dataset
partitions" section explicitly flagged as not yet built on top of the
partition-enforcement primitive: "partitioning datasets across
dev/validation/final_test for an actual experiment... [is] not yet built
on top of this - this is the enforcement primitive, not the full
benchmarking workflow."

Each partition is a genuinely different dataset instance (a different
seed), not the same literal dataset relabeled three times - reusing one
seed across partitions would make "final_test" meaningless, since
whatever an implementation learned to pass on `dev` would trivially
transfer to an identical `validation`/`final_test` instance. The three
seeds are derived deterministically from one `base_seed` so the whole
triple is still fully reproducible from a single number.
"""

from __future__ import annotations

from helix.benchmarking.difficulty_scenarios import DIFFICULTY_SCENARIOS
from helix.config.models import DatasetGenerationConfig

_SEED_OFFSETS = {"dev": 0, "validation": 1000, "final_test": 2000}


def partitioned_scenarios(tier: str, base_seed: int) -> dict[str, DatasetGenerationConfig]:
    """Three distinct `DatasetGenerationConfig`s for one difficulty tier -
    one per partition, each a different seed of the same scenario shape.

    Intended workflow (§9/§10 of the original project brief): develop and
    tune against `"dev"` (iterative feedback allowed, as always), sanity
    check generalization against `"validation"` (same feedback policy,
    different data), then run the frozen configuration against
    `"final_test"` exactly once, after configuration choices are frozen -
    `run_single_agent`/`run_multi_agent`/`run_dual_agent`/`run_adaptive_agent`
    already enforce the "no revision based on this run's own result" half
    of that via `final_test`'s single-shot cap; the "only evaluate once,
    after freezing" half is a process discipline this function doesn't
    (and can't) enforce by itself - see
    `scripts/run_adaptive_coordination_benchmark.py` for where it's
    deliberately *not* invoked, since that script is a dev-partition
    demonstration, not a frozen final evaluation.
    """
    if tier not in DIFFICULTY_SCENARIOS:
        raise ValueError(f"unknown difficulty tier: {tier!r} (expected one of {sorted(DIFFICULTY_SCENARIOS)})")
    builder = DIFFICULTY_SCENARIOS[tier]
    return {
        partition: builder(f"{tier}_{partition}", seed=base_seed + offset, partition=partition)
        for partition, offset in _SEED_OFFSETS.items()
    }
