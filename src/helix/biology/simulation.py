"""Deterministic synthetic immune-repertoire sequencing dataset generator.

Given a `DatasetGenerationConfig` (including a random seed), `generate_dataset`
produces exactly the same reads and ground truth every time. The generator
and the files it writes maintain a strict trust boundary: `write_dataset`
splits output into a public directory (reads, reference panel, redacted
metadata) and a `_ground_truth/` subdirectory (full config + ground truth)
that analysis code must never be given a path to.

Biological simplifications (see docs/benchmark_methodology.md for the full
list): sequences are opaque CDR3-length nucleotide strings, not full V(D)J
recombinants; sequencing errors are i.i.d. per base; "abundance" refers to
template-molecule proportions assigned a barcode before any PCR/sequencing
duplication.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from helix.biology.sequences import generate_barcode, generate_random_sequence, make_near_match, mutate_sequence
from helix.config.models import (
    BackgroundDiversity,
    DatasetGenerationConfig,
    GroundTruth,
    GroundTruthTarget,
    SequencingRead,
    TargetSpec,
)
from helix.io.fastq import write_fastq, write_reference_panel

BACKGROUND_LABEL = "background"

_DIVERSITY_CLUSTER_FRACTION = {
    BackgroundDiversity.LOW: 0.1,
    BackgroundDiversity.MEDIUM: 0.4,
    BackgroundDiversity.HIGH: 1.0,
}


@dataclass
class _Template:
    sequence: str
    barcode: str
    source_label: str  # target_id or "background"


def config_hash(config: DatasetGenerationConfig) -> str:
    payload = json.dumps(config.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _resolve_target_sequences(
    targets: list[TargetSpec], sequence_length: int, rng: np.random.Generator
) -> list[TargetSpec]:
    resolved = []
    for t in targets:
        if t.sequence is None:
            seq = generate_random_sequence(sequence_length, rng)
            resolved.append(t.model_copy(update={"sequence": seq}))
        else:
            resolved.append(t)
    return resolved


def _build_background_pool(
    n_distinct: int,
    sequence_length: int,
    diversity: BackgroundDiversity,
    near_match_count: int,
    near_match_edit_distance: int,
    present_targets: list[TargetSpec],
    rng: np.random.Generator,
) -> tuple[list[str], list[float]]:
    """Return (distinct background sequences, sampling weights).

    `diversity` controls how clustered the distinct background sequences
    are around a handful of seed sequences (LOW = few seeds, many
    near-identical variants; HIGH = fully independent sequences).
    """
    pool: list[str] = []
    weights: list[float] = []

    if n_distinct > 0:
        fraction = _DIVERSITY_CLUSTER_FRACTION[diversity]
        n_clusters = max(1, round(n_distinct * fraction))
        seeds = [generate_random_sequence(sequence_length, rng) for _ in range(n_clusters)]
        for i in range(n_distinct):
            seed = seeds[i % n_clusters]
            if i < n_clusters:
                seq = seed
            else:
                edit_dist = int(rng.integers(1, 4))
                edit_dist = min(edit_dist, sequence_length)
                seq = make_near_match(seed, edit_dist, rng)
            pool.append(seq)
            weights.append(1.0)

    for i in range(near_match_count):
        if not present_targets:
            break
        target = present_targets[i % len(present_targets)]
        pool.append(make_near_match(target.sequence, near_match_edit_distance, rng))
        weights.append(3.0)  # over-weighted: these are deliberately hard confusers

    if not pool:
        # Guarantee at least one background sequence so sampling never fails.
        pool.append(generate_random_sequence(sequence_length, rng))
        weights.append(1.0)

    total = sum(weights)
    norm_weights = [w / total for w in weights]
    return pool, norm_weights


def generate_dataset(
    config: DatasetGenerationConfig,
) -> tuple[list[SequencingRead], GroundTruth]:
    rng = np.random.default_rng(config.seed)

    targets = _resolve_target_sequences(config.targets, config.sequence_length, rng)
    present_targets = [t for t in targets if t.present and t.abundance > 0]

    n_templates = max(1, round(config.read_depth * (1 - config.duplicate_rate)))
    n_duplicate_reads = config.read_depth - n_templates

    target_template_counts: dict[str, int] = {
        t.target_id: round(n_templates * t.abundance) for t in present_targets
    }
    total_target_templates = sum(target_template_counts.values())
    if total_target_templates > n_templates:
        # Safety clip: scale down proportionally so background never goes negative.
        scale = n_templates / total_target_templates
        target_template_counts = {k: int(v * scale) for k, v in target_template_counts.items()}
        total_target_templates = sum(target_template_counts.values())

    n_background_templates = n_templates - total_target_templates

    background_pool, background_weights = _build_background_pool(
        n_distinct=config.n_background_sequences,
        sequence_length=config.sequence_length,
        diversity=config.background_diversity,
        near_match_count=config.near_match_background_count,
        near_match_edit_distance=config.near_match_edit_distance,
        present_targets=present_targets,
        rng=rng,
    )

    templates: list[_Template] = []
    for target_id, count in target_template_counts.items():
        seq = next(t.sequence for t in present_targets if t.target_id == target_id)
        for _ in range(count):
            templates.append(
                _Template(sequence=seq, barcode=generate_barcode(config.barcode_length, rng), source_label=target_id)
            )

    for _ in range(n_background_templates):
        seq = background_pool[rng.choice(len(background_pool), p=background_weights)]
        templates.append(
            _Template(sequence=seq, barcode=generate_barcode(config.barcode_length, rng), source_label=BACKGROUND_LABEL)
        )

    rng.shuffle(templates)  # avoid artifacts from construction order

    reads: list[SequencingRead] = []
    read_origin: dict[str, str] = {}
    read_counter = 0

    def _emit_read(template: _Template) -> None:
        nonlocal read_counter
        seq, qual, _ = mutate_sequence(
            template.sequence,
            rng,
            substitution_rate=config.error_model.substitution_rate,
            insertion_rate=config.error_model.insertion_rate,
            deletion_rate=config.error_model.deletion_rate,
        )
        read_id = f"read_{read_counter:08d}"
        read_counter += 1
        reads.append(SequencingRead(read_id=read_id, sequence=seq, quality=qual, barcode=template.barcode))
        read_origin[read_id] = template.source_label

    for template in templates:
        _emit_read(template)

    for _ in range(n_duplicate_reads):
        template = templates[rng.integers(0, len(templates))]
        _emit_read(template)

    order = rng.permutation(len(reads))
    reads = [reads[i] for i in order]

    total_reads = len(reads)
    gt_targets = []
    for t in targets:
        true_count = sum(1 for origin in read_origin.values() if origin == t.target_id)
        gt_targets.append(
            GroundTruthTarget(
                target_id=t.target_id,
                sequence=t.sequence,
                true_count=true_count,
                true_frequency=(true_count / total_reads) if total_reads else 0.0,
                present=t.present and true_count > 0,
            )
        )

    resolved_config = config.model_copy(update={"targets": targets})
    ground_truth = GroundTruth(
        dataset_id=config.dataset_id,
        config_hash=config_hash(resolved_config),
        total_reads=total_reads,
        total_templates=len(templates),
        duplicate_reads=n_duplicate_reads,
        background_template_count=n_background_templates,
        targets=gt_targets,
        read_origin=read_origin,
    )

    return reads, ground_truth


def _public_metadata(config: DatasetGenerationConfig, targets: list[TargetSpec]) -> dict:
    """Redacted dataset metadata: no abundance, no presence/absence signal."""
    return {
        "dataset_id": config.dataset_id,
        "description": config.description,
        "sequence_length": config.sequence_length,
        "read_depth": config.read_depth,
        "n_background_sequences": config.n_background_sequences,
        "background_diversity": config.background_diversity.value,
        "error_model": config.error_model.model_dump(),
        "duplicate_rate": config.duplicate_rate,
        "barcode_length": config.barcode_length,
        "reference_target_ids": [t.target_id for t in targets],
    }


def write_dataset(config: DatasetGenerationConfig, output_root: str | Path) -> dict[str, Path]:
    """Generate and persist a dataset, enforcing the public/ground-truth split.

    Returns a dict of paths: reads_fastq, reference_panel, dataset_metadata,
    ground_truth, full_config (the last two under `_ground_truth/`).
    """
    reads, ground_truth = generate_dataset(config)
    targets = _resolve_target_sequences(config.targets, config.sequence_length, np.random.default_rng(config.seed))
    resolved_config = config.model_copy(update={"targets": targets})

    dataset_dir = Path(output_root) / config.dataset_id
    gt_dir = dataset_dir / "_ground_truth"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    gt_dir.mkdir(parents=True, exist_ok=True)

    paths = {
        "reads_fastq": dataset_dir / "reads.fastq",
        "reference_panel": dataset_dir / "reference_panel.fasta",
        "dataset_metadata": dataset_dir / "dataset_metadata.json",
        "ground_truth": gt_dir / "ground_truth.json",
        "full_config": gt_dir / "full_config.json",
    }

    write_fastq(reads, paths["reads_fastq"])
    write_reference_panel(targets, paths["reference_panel"])
    paths["dataset_metadata"].write_text(json.dumps(_public_metadata(resolved_config, targets), indent=2))
    paths["ground_truth"].write_text(ground_truth.model_dump_json(indent=2))
    paths["full_config"].write_text(resolved_config.model_dump_json(indent=2))

    return paths
