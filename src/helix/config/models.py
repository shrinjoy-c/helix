"""Structured (Pydantic) models shared by the biology, execution, and
experiment-persistence layers of HELIX.

These models are the public interface between modules: the dataset
generator produces `GroundTruth` and public dataset files; the
deterministic baseline (and, in later phases, agents) only ever see the
public files and `TargetSpec.sequence` / `TargetSpec.target_id` (the
"reference panel"); the evaluator only ever sees `AnalysisResult` plus a
`GroundTruth` loaded from a path that was never passed to the analysis
step. See docs/reproducibility.md for the trust-boundary diagram.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class BackgroundDiversity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ErrorModel(BaseModel):
    """Per-base, i.i.d. sequencing error rates.

    Simplification: real sequencers have position- and context-dependent
    error profiles (e.g. homopolymer errors on long-read platforms,
    quality decay toward the 3' end on short-read platforms). This model
    intentionally uses uniform per-base rates so that detection difficulty
    is controlled solely by the configured rate, not by an unstated
    platform-specific pattern.
    """

    substitution_rate: float = Field(0.0, ge=0.0, le=1.0)
    insertion_rate: float = Field(0.0, ge=0.0, le=1.0)
    deletion_rate: float = Field(0.0, ge=0.0, le=1.0)

    @property
    def total_error_rate(self) -> float:
        return self.substitution_rate + self.insertion_rate + self.deletion_rate


class TargetSpec(BaseModel):
    """A reference ("probe panel") sequence the analysis is asked to look for.

    `sequence` and `target_id` are always visible to the analysis step.
    `abundance` and `present` are generation-time parameters that end up
    only in the ground truth, never in anything the analysis reads.
    """

    target_id: str
    sequence: Optional[str] = Field(
        default=None,
        description="Explicit nucleotide sequence. If None, the generator "
        "creates a random one of `sequence_length` at generation time.",
    )
    abundance: float = Field(
        0.01,
        ge=0.0,
        le=1.0,
        description="Relative proportion of template molecules this target "
        "occupies, if present. Ignored if present=False.",
    )
    present: bool = Field(
        True,
        description="Whether this target actually occurs in the simulated "
        "sample. False models a true negative / no-target-present case "
        "while still exposing the sequence as something to search for.",
    )
    label: Optional[str] = Field(
        default=None, description="Free-text tag, e.g. 'rare', 'abundant', 'decoy'."
    )

    @field_validator("sequence")
    @classmethod
    def _upper(cls, v: Optional[str]) -> Optional[str]:
        return v.upper() if v else v


class DatasetGenerationConfig(BaseModel):
    """Complete, deterministic specification of a synthetic dataset.

    Two configs with the same field values (including `seed`) must produce
    byte-identical reads and ground truth. This is the unit that gets
    hashed for reproducibility checks and saved alongside every experiment.
    """

    dataset_id: str
    seed: int
    description: str = ""

    sequence_length: int = Field(45, gt=0)
    read_depth: int = Field(1000, gt=0, description="Total number of reads to simulate.")

    targets: list[TargetSpec] = Field(default_factory=list)

    n_background_sequences: int = Field(
        50, ge=0, description="Number of distinct background template sequences."
    )
    background_diversity: BackgroundDiversity = BackgroundDiversity.MEDIUM

    error_model: ErrorModel = Field(default_factory=ErrorModel)

    duplicate_rate: float = Field(
        0.0,
        ge=0.0,
        lt=1.0,
        description="Expected fraction of reads that are duplicate "
        "observations of an already-sampled template (same barcode, "
        "independent sequencing errors).",
    )
    barcode_length: int = Field(10, gt=0)

    near_match_background_count: int = Field(
        0,
        ge=0,
        description="Number of background sequences deliberately generated "
        "as close variants of a target sequence (hard false-positive case).",
    )
    near_match_edit_distance: int = Field(1, ge=1)

    @field_validator("targets")
    @classmethod
    def _unique_target_ids(cls, v: list[TargetSpec]) -> list[TargetSpec]:
        ids = [t.target_id for t in v]
        if len(ids) != len(set(ids)):
            raise ValueError("target_id values must be unique within a dataset config")
        return v


class SequencingRead(BaseModel):
    """A single simulated FASTQ record, as seen by the analysis step.

    Deliberately carries no information about its true origin (target vs.
    background, true error positions) - that lives only in `GroundTruth`.
    """

    read_id: str
    sequence: str
    quality: str = Field(description="Phred+33 quality string, same length as sequence.")
    barcode: str


class GroundTruthTarget(BaseModel):
    target_id: str
    sequence: str
    true_count: int = Field(ge=0, description="Number of reads truly derived from this target.")
    true_frequency: float = Field(ge=0.0, le=1.0)
    present: bool


class GroundTruth(BaseModel):
    """Independent ground truth for one generated dataset.

    Must be persisted to a path that is never handed to the analysis
    step. Only `helix.biology.evaluation` should ever read this file.
    """

    dataset_id: str
    config_hash: str
    total_reads: int
    total_templates: int
    duplicate_reads: int
    background_template_count: int
    targets: list[GroundTruthTarget]
    read_origin: dict[str, str] = Field(
        default_factory=dict,
        description="read_id -> target_id ('background' if not a target), "
        "kept for failure analysis only.",
    )


class MatchingConfig(BaseModel):
    """Parameters for the deterministic sequence-matching baseline."""

    max_mismatches: int = Field(0, ge=0)
    collapse_duplicates: bool = True
    min_count: int = Field(
        1, ge=1, description="Minimum matched-molecule count to call a target 'detected'."
    )


class PredictedTarget(BaseModel):
    target_id: str
    matched_read_count: int = Field(ge=0)
    matched_molecule_count: int = Field(ge=0)
    estimated_frequency: float = Field(ge=0.0)
    detected: bool


class AnalysisResult(BaseModel):
    """Output of any analysis pipeline (deterministic baseline or an agent)."""

    dataset_id: str
    pipeline_name: str
    predictions: list[PredictedTarget]
    total_reads_processed: int
    total_molecules_after_dedup: int
    runtime_seconds: float
    errors: list[str] = Field(default_factory=list)

    @property
    def no_target_detected(self) -> bool:
        return not any(p.detected for p in self.predictions)


class TargetEvaluation(BaseModel):
    target_id: str
    true_present: bool
    predicted_present: bool
    true_frequency: float
    estimated_frequency: float
    abundance_error: float


class EvaluationMetrics(BaseModel):
    """Independent scoring of an `AnalysisResult` against `GroundTruth`."""

    dataset_id: str
    pipeline_name: str

    true_positives: int
    false_positives: int
    false_negatives: int
    true_negatives: int

    precision: float
    recall: float
    f1: float
    false_positive_rate: float
    false_negative_rate: float

    abundance_mae: Optional[float] = Field(
        default=None, description="Mean absolute error in estimated frequency, over truly-present targets."
    )

    per_target: list[TargetEvaluation] = Field(default_factory=list)


class ExperimentRecord(BaseModel):
    """A persisted, reproducible record of one experiment run."""

    experiment_id: str
    dataset_id: str
    pipeline_name: str
    status: Literal["completed", "failed"]
    dataset_config: DatasetGenerationConfig
    matching_config: Optional[MatchingConfig] = None
    analysis_result: Optional[AnalysisResult] = None
    evaluation_metrics: Optional[EvaluationMetrics] = None
    runtime_seconds: float
    created_at: str
    notes: str = ""
