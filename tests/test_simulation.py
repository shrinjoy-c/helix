import pytest

from helix.biology.simulation import config_hash, generate_dataset, write_dataset
from helix.biology.evaluation import load_ground_truth, verify_reference_panel_matches_ground_truth
from helix.config.models import DatasetGenerationConfig, ErrorModel, TargetSpec


def _base_config(**overrides) -> DatasetGenerationConfig:
    defaults = dict(
        dataset_id="test_ds",
        seed=1234,
        sequence_length=30,
        read_depth=500,
        targets=[TargetSpec(target_id="t1", abundance=0.1, present=True)],
        n_background_sequences=10,
    )
    defaults.update(overrides)
    return DatasetGenerationConfig(**defaults)


def test_generation_is_deterministic_given_seed():
    config = _base_config()
    reads_a, gt_a = generate_dataset(config)
    reads_b, gt_b = generate_dataset(config)

    assert [r.model_dump() for r in reads_a] == [r.model_dump() for r in reads_b]
    assert gt_a == gt_b


def test_different_seed_changes_output():
    reads_a, _ = generate_dataset(_base_config(seed=1))
    reads_b, _ = generate_dataset(_base_config(seed=2))
    assert [r.sequence for r in reads_a] != [r.sequence for r in reads_b]


def test_read_depth_is_respected_exactly():
    config = _base_config(read_depth=777, duplicate_rate=0.2)
    reads, gt = generate_dataset(config)
    assert len(reads) == 777
    assert gt.total_reads == 777


def test_target_abundance_is_approximately_achieved():
    config = _base_config(
        read_depth=20000,
        targets=[TargetSpec(target_id="t1", abundance=0.2, present=True)],
    )
    _, gt = generate_dataset(config)
    target = next(t for t in gt.targets if t.target_id == "t1")
    assert target.true_frequency == pytest.approx(0.2, abs=0.02)
    assert target.present is True


def test_no_target_present_case_has_zero_true_count():
    config = _base_config(
        targets=[TargetSpec(target_id="t1", sequence="A" * 30, abundance=0.0, present=False)]
    )
    _, gt = generate_dataset(config)
    target = next(t for t in gt.targets if t.target_id == "t1")
    assert target.true_count == 0
    assert target.true_frequency == 0.0
    assert target.present is False
    # The "null" dataset still has reads - just none of them are the target.
    assert gt.total_reads == config.read_depth
    assert all(origin != "t1" for origin in gt.read_origin.values())


def test_duplicate_rate_reduces_unique_template_count():
    config = _base_config(read_depth=1000, duplicate_rate=0.3)
    _, gt = generate_dataset(config)
    assert gt.total_templates == 700
    assert gt.duplicate_reads == 300


def test_zero_error_rate_reproduces_target_sequence_exactly():
    config = _base_config(
        targets=[TargetSpec(target_id="t1", sequence="ACGT" * 7 + "AC", abundance=0.5, present=True)],
        sequence_length=30,
        error_model=ErrorModel(substitution_rate=0.0, insertion_rate=0.0, deletion_rate=0.0),
    )
    reads, gt = generate_dataset(config)
    target_reads = [r for r in reads if gt.read_origin[r.read_id] == "t1"]
    assert len(target_reads) > 0
    assert all(r.sequence == "ACGT" * 7 + "AC" for r in target_reads)


def test_config_hash_stable_for_identical_config():
    config = _base_config()
    assert config_hash(config) == config_hash(config)


def test_config_hash_changes_with_seed():
    assert config_hash(_base_config(seed=1)) != config_hash(_base_config(seed=2))


def test_write_dataset_separates_ground_truth(tmp_path):
    config = _base_config()
    paths = write_dataset(config, tmp_path)

    assert paths["reads_fastq"].exists()
    assert paths["reference_panel"].exists()
    assert paths["dataset_metadata"].exists()
    assert paths["ground_truth"].exists()
    assert paths["ground_truth"].parent.name == "_ground_truth"

    # Public metadata must not leak abundance or presence.
    import json

    metadata = json.loads(paths["dataset_metadata"].read_text())
    assert "abundance" not in json.dumps(metadata)
    assert "present" not in json.dumps(metadata)

    gt = load_ground_truth(tmp_path / config.dataset_id)
    assert gt.dataset_id == config.dataset_id
    assert verify_reference_panel_matches_ground_truth(tmp_path / config.dataset_id)


def test_near_match_background_is_within_edit_distance(tmp_path):
    from helix.biology.sequences import bounded_edit_distance

    config = _base_config(
        targets=[TargetSpec(target_id="t1", sequence="G" * 30, abundance=0.1, present=True)],
        near_match_background_count=5,
        near_match_edit_distance=2,
        read_depth=2000,
    )
    reads, gt = generate_dataset(config)
    background_reads = [r for r in reads if gt.read_origin[r.read_id] == "background"]
    # At least one background read should be close (within a few errors) to the target.
    distances = [
        bounded_edit_distance(r.sequence, "G" * 30, 5)
        for r in background_reads
    ]
    assert min(distances) <= 3
