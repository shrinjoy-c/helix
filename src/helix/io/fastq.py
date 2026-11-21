"""FASTQ and FASTA I/O built on Biopython's SeqIO.

Reads are stored/exchanged as `helix.config.models.SequencingRead` objects
everywhere outside this module; Bio.SeqRecord is only used as the
on-disk serialization format.
"""

from __future__ import annotations

from pathlib import Path

from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord
from Bio import SeqIO

from helix.config.models import SequencingRead, TargetSpec


def write_fastq(reads: list[SequencingRead], path: str | Path) -> None:
    path = Path(path)
    records = []
    for r in reads:
        rec = SeqRecord(Seq(r.sequence), id=r.read_id, description=f"barcode={r.barcode}")
        rec.letter_annotations["phred_quality"] = [ord(c) - 33 for c in r.quality]
        records.append(rec)
    with path.open("w") as fh:
        SeqIO.write(records, fh, "fastq")


def read_fastq(path: str | Path) -> list[SequencingRead]:
    path = Path(path)
    reads: list[SequencingRead] = []
    for rec in SeqIO.parse(str(path), "fastq"):
        barcode = ""
        if rec.description and "barcode=" in rec.description:
            barcode = rec.description.split("barcode=", 1)[1].split()[0]
        quality = "".join(chr(q + 33) for q in rec.letter_annotations["phred_quality"])
        reads.append(
            SequencingRead(
                read_id=rec.id,
                sequence=str(rec.seq),
                quality=quality,
                barcode=barcode,
            )
        )
    return reads


def write_reference_panel(targets: list[TargetSpec], path: str | Path) -> None:
    """Write only target_id + sequence - never abundance/present."""
    path = Path(path)
    records = [
        SeqRecord(Seq(t.sequence), id=t.target_id, description="") for t in targets if t.sequence
    ]
    with path.open("w") as fh:
        SeqIO.write(records, fh, "fasta")


def read_reference_panel(path: str | Path) -> list[tuple[str, str]]:
    """Return list of (target_id, sequence) from a reference panel FASTA."""
    return [(rec.id, str(rec.seq)) for rec in SeqIO.parse(str(path), "fasta")]
