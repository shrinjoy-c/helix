"""The adaptive coordination policy (Experiment E): a deliberately simple,
interpretable weighted-sum heuristic over public dataset metadata, not a
learned policy. See the project's extension-plan document, "Adaptive
coordination - design document" (§4) for the full rationale; this module
implements that section's static difficulty score (sub-questions 1 & 2) and
the fixed per-tier iteration/agent-count tables (6 & 7). The dynamic half
of the policy - escalation during a run, recording the decision, handling
a wrong prediction, fair comparison against fixed configurations (3, 5, 8,
9, 10) - lives in `helix.orchestration.orchestrator.run_adaptive_agent`
instead, since those are about *using* this score mid-run, not computing
it.

Every factor below is read from the *public* `dataset_metadata.json` -
abundance and presence are redacted from that file by design (see
`helix.biology.simulation._public_metadata`), so neither can ever inform a
live, blind coordination decision, only post-hoc analysis of a finished
benchmark sweep. This is a hard constraint, not an oversight: a factor
that required reading ground truth would make every adaptive decision a
leakage vector in disguise.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DifficultyTier = Literal["easy", "moderate", "difficult", "ambiguous"]

# Fixed weights, chosen to sum to 1.0 so the combined score stays in [0, 1]
# without a separate renormalization step. A starting point the design doc
# itself calls "configurable but starting equal-ish" - not tuned against
# any benchmark result, deliberately, since tuning them against results
# produced using them would be circular.
_ERROR_RATE_WEIGHT = 0.30
_INDEL_PRESENCE_WEIGHT = 0.20
_READ_DEPTH_WEIGHT = 0.20
_BACKGROUND_DIVERSITY_WEIGHT = 0.20
_DUPLICATE_RATE_WEIGHT = 0.10

# A combined substitution+insertion+deletion rate at or above this is
# treated as maximally difficult (1.0). The project's own example
# scenarios all sit under 2% - 10% is a deliberately generous reference
# band, not a tight one tuned to any particular dataset.
_ERROR_RATE_REFERENCE = 0.10

# read_depth / n_background_sequences ratio reference band: at or above
# this many reads per background sequence, depth is "fully adequate"
# (contributes 0 difficulty); at or below the lower bound, "fully
# inadequate" (contributes 1.0 difficulty). Linear in between.
_READ_DEPTH_RATIO_ADEQUATE = 20.0
_READ_DEPTH_RATIO_INADEQUATE = 2.0

_DIVERSITY_SCORE = {"low": 0.0, "medium": 0.5, "high": 1.0}

TIER_MAX_ITERATIONS: dict[DifficultyTier, int] = {
    "easy": 2, "moderate": 3, "difficult": 4, "ambiguous": 5,
}
"""Per-tier *recommended* revision-cycle ceiling - design doc §6: "easy 1-2,
moderate 3, difficult 3-4, ambiguous 4-5, all capped at the global max".
Reuses the existing `IterationBudget` mechanism unchanged; this table only
decides what value `run_adaptive_agent` passes it, and the caller's own
`max_iterations` is always the hard ceiling on top of this."""

FAR_FAILURE_F1_THRESHOLD = 0.5
"""Design doc §5: a failed verification with F1 below this is "far" (an
escalation signal towards a second coding agent, if not already running);
at or above it, "close" (worth one more revision alone, no escalation)."""


class DifficultyFactor(BaseModel):
    name: str
    raw_value: float
    normalized: float = Field(ge=0.0, le=1.0)
    weight: float
    detail: str = ""


class DifficultyEstimate(BaseModel):
    """The static, pre-run half of the adaptive policy (design doc §1-2):
    a weighted sum of five factors readable from `dataset_metadata.json`
    alone. `factors` is kept on the result specifically so a coordination
    decision is inspectable, not just a bare number - "as interpretable as
    possible" extends to showing the breakdown, not only the score."""

    score: float = Field(ge=0.0, le=1.0)
    tier: DifficultyTier
    factors: list[DifficultyFactor]


def _normalize_linear(value: float, low: float, high: float) -> float:
    """Clamp (value - low) / (high - low) to [0, 1]. `low` may be greater
    than `high` to express an inverted band (e.g. a ratio where *lower*
    means *harder*) - the clamp still holds either way."""
    if high == low:
        return 0.0
    return max(0.0, min(1.0, (value - low) / (high - low)))


def estimate_difficulty(metadata: dict) -> DifficultyEstimate:
    """Score one dataset's `dataset_metadata.json` (the redacted, public
    dict every `run_*` function already loads via `_load_dataset_metadata`)
    into a difficulty tier. Pure function of its input - no I/O, no
    provider call, same score for the same metadata every time."""
    error_model = metadata.get("error_model", {})
    combined_error_rate = (
        error_model.get("substitution_rate", 0.0)
        + error_model.get("insertion_rate", 0.0)
        + error_model.get("deletion_rate", 0.0)
    )
    error_factor = _normalize_linear(combined_error_rate, 0.0, _ERROR_RATE_REFERENCE)

    indel_present = (error_model.get("insertion_rate", 0.0) + error_model.get("deletion_rate", 0.0)) > 0.0
    indel_factor = 1.0 if indel_present else 0.0

    read_depth = metadata.get("read_depth", 0)
    n_background = metadata.get("n_background_sequences", 0)
    depth_ratio = read_depth / max(1, n_background)
    # Inverse band: a HIGH ratio is adequate (low difficulty), so the
    # difficulty contribution is 1 minus the adequacy fraction.
    adequacy = _normalize_linear(depth_ratio, _READ_DEPTH_RATIO_INADEQUATE, _READ_DEPTH_RATIO_ADEQUATE)
    depth_factor = 1.0 - adequacy

    diversity = str(metadata.get("background_diversity", "medium")).lower()
    diversity_factor = _DIVERSITY_SCORE.get(diversity, 0.5)

    duplicate_rate = max(0.0, min(1.0, metadata.get("duplicate_rate", 0.0)))

    factors = [
        DifficultyFactor(
            name="combined_error_rate", raw_value=combined_error_rate, normalized=error_factor,
            weight=_ERROR_RATE_WEIGHT,
        ),
        DifficultyFactor(
            name="indel_presence", raw_value=float(indel_present), normalized=indel_factor,
            weight=_INDEL_PRESENCE_WEIGHT,
        ),
        DifficultyFactor(
            name="read_depth_inadequacy", raw_value=depth_ratio, normalized=depth_factor,
            weight=_READ_DEPTH_WEIGHT, detail=f"read_depth/n_background_sequences={depth_ratio:.2f}",
        ),
        DifficultyFactor(
            name="background_diversity", raw_value=diversity_factor, normalized=diversity_factor,
            weight=_BACKGROUND_DIVERSITY_WEIGHT, detail=diversity,
        ),
        DifficultyFactor(
            name="duplicate_rate", raw_value=duplicate_rate, normalized=duplicate_rate,
            weight=_DUPLICATE_RATE_WEIGHT,
        ),
    ]
    score = sum(f.weight * f.normalized for f in factors)

    if score < 0.25:
        tier: DifficultyTier = "easy"
    elif score < 0.50:
        tier = "moderate"
    elif score < 0.75:
        tier = "difficult"
    else:
        tier = "ambiguous"

    return DifficultyEstimate(score=score, tier=tier, factors=factors)


def initial_agent_count(tier: DifficultyTier) -> Literal[1, 2]:
    """Design doc §3.B: "start with one agent for easy/moderate tiers,
    activate a second on difficult/ambiguous tiers"."""
    return 2 if tier in ("difficult", "ambiguous") else 1
