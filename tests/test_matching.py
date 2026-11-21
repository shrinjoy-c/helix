from helix.biology.matching import analyze_dataset, analyze_reads
from helix.config.models import DatasetGenerationConfig, MatchingConfig, SequencingRead, TargetSpec
from helix.biology.simulation import write_dataset

TARGET = "ACGTACGTACGTACGTACGTACGTACGTAC"  # 31nt
ONE_MISMATCH = "ACGTACGTACGTACGTACGTACGTACGTAT"  # last base differs
BACKGROUND = "TTTTTTTTTTTTTTTTTTTTTTTTTTTTTTT"


def test_exact_match_counts_correctly():
    reads = [
        SequencingRead(read_id="r0", sequence=TARGET, quality="I" * len(TARGET), barcode="b0"),
        SequencingRead(read_id="r1", sequence=TARGET, quality="I" * len(TARGET), barcode="b1"),
        SequencingRead(read_id="r2", sequence=BACKGROUND, quality="I" * len(BACKGROUND), barcode="b2"),
    ]
    result = analyze_reads(reads, [("t1", TARGET)], MatchingConfig(max_mismatches=0))
    pred = next(p for p in result.predictions if p.target_id == "t1")
    assert pred.matched_molecule_count == 2
    assert pred.detected is True
    assert pred.estimated_frequency == 2 / 3


def test_mismatch_tolerance_required_to_match():
    reads = [SequencingRead(read_id="r0", sequence=ONE_MISMATCH, quality="I" * len(TARGET), barcode="b0")]

    strict = analyze_reads(reads, [("t1", TARGET)], MatchingConfig(max_mismatches=0))
    assert strict.predictions[0].matched_molecule_count == 0
    assert strict.predictions[0].detected is False

    lenient = analyze_reads(reads, [("t1", TARGET)], MatchingConfig(max_mismatches=1))
    assert lenient.predictions[0].matched_molecule_count == 1
    assert lenient.predictions[0].detected is True


def test_duplicate_collapsing_by_barcode():
    # Three reads, same barcode (same original molecule) -> should collapse to 1.
    reads = [
        SequencingRead(read_id=f"r{i}", sequence=TARGET, quality="I" * len(TARGET), barcode="shared")
        for i in range(3)
    ]
    collapsed = analyze_reads(reads, [("t1", TARGET)], MatchingConfig(collapse_duplicates=True))
    uncollapsed = analyze_reads(reads, [("t1", TARGET)], MatchingConfig(collapse_duplicates=False))

    assert collapsed.total_molecules_after_dedup == 1
    assert collapsed.predictions[0].matched_molecule_count == 1
    assert uncollapsed.total_molecules_after_dedup == 3
    assert uncollapsed.predictions[0].matched_molecule_count == 3


def test_min_count_threshold_controls_detection():
    reads = [SequencingRead(read_id="r0", sequence=TARGET, quality="I" * len(TARGET), barcode="b0")]
    result = analyze_reads(reads, [("t1", TARGET)], MatchingConfig(min_count=2))
    assert result.predictions[0].matched_molecule_count == 1
    assert result.predictions[0].detected is False  # below threshold


def test_no_target_detected_when_nothing_matches():
    reads = [SequencingRead(read_id="r0", sequence=BACKGROUND, quality="I" * len(BACKGROUND), barcode="b0")]
    result = analyze_reads(reads, [("t1", TARGET)], MatchingConfig(max_mismatches=0))
    assert result.no_target_detected is True


def test_analyze_dataset_reads_only_public_files(tmp_path):
    config = DatasetGenerationConfig(
        dataset_id="matching_ds",
        seed=7,
        sequence_length=25,
        read_depth=300,
        targets=[TargetSpec(target_id="t1", abundance=0.15, present=True)],
        n_background_sequences=8,
    )
    paths = write_dataset(config, tmp_path)
    dataset_dir = paths["reads_fastq"].parent

    result = analyze_dataset(dataset_dir, MatchingConfig(max_mismatches=0))
    assert result.total_reads_processed == 300
    assert len(result.predictions) == 1
    assert result.predictions[0].target_id == "t1"
