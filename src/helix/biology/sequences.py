"""Low-level sequence utilities: generation, mutation, and distance.

Simplification note: sequencing errors are modeled as i.i.d. per-base
events (see `helix.config.models.ErrorModel`), not the position- and
context-dependent profiles of real instruments. Quality scores are
derived directly from whether an error was actually applied at that
position, so they are internally consistent but not calibrated against
any real platform.
"""

from __future__ import annotations

import numpy as np
from Bio.Seq import Seq

BASES = "ACGT"
BASELINE_QUALITY = 37
ERROR_QUALITY = 10


def generate_random_sequence(length: int, rng: np.random.Generator, alphabet: str = BASES) -> str:
    """Generate a uniform-random nucleotide sequence of the given length."""
    idx = rng.integers(0, len(alphabet), size=length)
    return str(Seq("".join(alphabet[i] for i in idx)))


def generate_barcode(length: int, rng: np.random.Generator) -> str:
    return generate_random_sequence(length, rng)


def make_near_match(sequence: str, edit_distance: int, rng: np.random.Generator) -> str:
    """Return a variant of `sequence` with exactly `edit_distance` point
    substitutions at distinct, randomly chosen positions.

    Used to construct deliberately hard cases: a background sequence that
    closely resembles a target, or a second target that differs from the
    first by a single nucleotide.
    """
    if edit_distance > len(sequence):
        raise ValueError("edit_distance cannot exceed sequence length")
    positions = rng.choice(len(sequence), size=edit_distance, replace=False)
    chars = list(sequence)
    for pos in positions:
        current = chars[pos]
        choices = [b for b in BASES if b != current]
        chars[pos] = choices[rng.integers(0, len(choices))]
    return "".join(chars)


def mutate_sequence(
    sequence: str,
    rng: np.random.Generator,
    substitution_rate: float = 0.0,
    insertion_rate: float = 0.0,
    deletion_rate: float = 0.0,
) -> tuple[str, str, int]:
    """Apply i.i.d. per-base substitution/insertion/deletion events.

    Returns (mutated_sequence, phred33_quality_string, total_error_count).
    Quality at a position reflects whether an error was applied there,
    not an independently sampled instrument noise model.
    """
    out_bases: list[str] = []
    out_quals: list[int] = []
    n_errors = 0

    for base in sequence:
        r = rng.random()
        if r < deletion_rate:
            n_errors += 1
            # deletion: emit nothing for this input base
        elif r < deletion_rate + substitution_rate:
            choices = [b for b in BASES if b != base]
            out_bases.append(choices[rng.integers(0, len(choices))])
            out_quals.append(ERROR_QUALITY)
            n_errors += 1
        else:
            out_bases.append(base)
            out_quals.append(BASELINE_QUALITY)

        if rng.random() < insertion_rate:
            out_bases.append(BASES[rng.integers(0, len(BASES))])
            out_quals.append(ERROR_QUALITY)
            n_errors += 1

    quality = "".join(chr(q + 33) for q in out_quals)
    return "".join(out_bases), quality, n_errors


def hamming_distance(a: str, b: str) -> int:
    if len(a) != len(b):
        raise ValueError("hamming_distance requires equal-length sequences")
    return sum(1 for x, y in zip(a, b) if x != y)


def bounded_edit_distance(a: str, b: str, max_distance: int) -> int:
    """Levenshtein edit distance, capped at `max_distance` + 1 for speed.

    Returns max_distance + 1 if the true distance exceeds max_distance
    (callers typically only care whether distance <= threshold).
    """
    if a == b:
        return 0
    len_a, len_b = len(a), len(b)
    if abs(len_a - len_b) > max_distance:
        return max_distance + 1

    prev = list(range(len_b + 1))
    for i in range(1, len_a + 1):
        curr = [i] + [0] * len_b
        row_min = curr[0]
        for j in range(1, len_b + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            curr[j] = min(
                prev[j] + 1,  # deletion
                curr[j - 1] + 1,  # insertion
                prev[j - 1] + cost,  # substitution
            )
            row_min = min(row_min, curr[j])
        if row_min > max_distance:
            return max_distance + 1
        prev = curr
    return prev[len_b]
