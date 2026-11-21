"""Deterministic, non-LLM sequence-analysis baseline.

This is the "conventional bioinformatics pipeline" baseline required by
the benchmark: no model calls, fully auditable, same interface as any
agent-produced analysis. It only ever reads the public dataset files
(reads.fastq, reference_panel.fasta) - never the ground truth.
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from pathlib import Path

from helix.biology.sequences import bounded_edit_distance, hamming_distance
from helix.config.models import AnalysisResult, MatchingConfig, PredictedTarget, SequencingRead
from helix.io.fastq import read_fastq, read_reference_panel


def _distance(a: str, b: str, max_mismatches: int) -> int:
    if len(a) == len(b):
        return hamming_distance(a, b)
    return bounded_edit_distance(a, b, max_mismatches)


def _collapse_duplicates(reads: list[SequencingRead]) -> list[SequencingRead]:
    """Collapse reads sharing a barcode into one consensus-ish molecule.

    Picks the most common observed sequence per barcode (majority vote
    across duplicate reads), which denoises independent per-read
    sequencing errors on the same original template.
    """
    by_barcode: dict[str, list[SequencingRead]] = defaultdict(list)
    for r in reads:
        by_barcode[r.barcode].append(r)

    collapsed: list[SequencingRead] = []
    for barcode, group in by_barcode.items():
        counts = Counter(r.sequence for r in group)
        consensus_seq, _ = counts.most_common(1)[0]
        representative = next(r for r in group if r.sequence == consensus_seq)
        collapsed.append(representative)
    return collapsed


def analyze_reads(
    reads: list[SequencingRead],
    reference_panel: list[tuple[str, str]],
    config: MatchingConfig,
    dataset_id: str = "",
    pipeline_name: str = "deterministic_baseline",
) -> AnalysisResult:
    start = time.monotonic()
    errors: list[str] = []

    try:
        molecules = _collapse_duplicates(reads) if config.collapse_duplicates else reads
        total_molecules = len(molecules)

        match_counts: Counter[str] = Counter()
        for molecule in molecules:
            best_target_id = None
            best_distance = config.max_mismatches + 1
            for target_id, target_seq in reference_panel:
                d = _distance(molecule.sequence, target_seq, config.max_mismatches)
                if d <= config.max_mismatches and d < best_distance:
                    best_distance = d
                    best_target_id = target_id
            if best_target_id is not None:
                match_counts[best_target_id] += 1

        predictions = []
        for target_id, _ in reference_panel:
            count = match_counts.get(target_id, 0)
            freq = count / total_molecules if total_molecules else 0.0
            predictions.append(
                PredictedTarget(
                    target_id=target_id,
                    matched_read_count=count,
                    matched_molecule_count=count,
                    estimated_frequency=freq,
                    detected=count >= config.min_count,
                )
            )
    except Exception as exc:  # pragma: no cover - defensive, reported not raised
        errors.append(f"{type(exc).__name__}: {exc}")
        predictions = []
        total_molecules = 0

    runtime = time.monotonic() - start
    return AnalysisResult(
        dataset_id=dataset_id,
        pipeline_name=pipeline_name,
        predictions=predictions,
        total_reads_processed=len(reads),
        total_molecules_after_dedup=total_molecules,
        runtime_seconds=runtime,
        errors=errors,
    )


def analyze_dataset(
    dataset_dir: str | Path,
    config: MatchingConfig,
    pipeline_name: str = "deterministic_baseline",
) -> AnalysisResult:
    """Run the baseline against a dataset's *public* files only.

    Deliberately takes a directory path rather than in-memory objects so
    that the only inputs available are the ones actually written to disk
    for agent consumption - `_ground_truth/` is never touched.
    """
    dataset_dir = Path(dataset_dir)
    reads = read_fastq(dataset_dir / "reads.fastq")
    reference_panel = read_reference_panel(dataset_dir / "reference_panel.fasta")
    return analyze_reads(
        reads, reference_panel, config, dataset_id=dataset_dir.name, pipeline_name=pipeline_name
    )
