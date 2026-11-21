from helix.config.models import SequencingRead, TargetSpec
from helix.io.fastq import read_fastq, read_reference_panel, write_fastq, write_reference_panel


def test_fastq_round_trip(tmp_path):
    reads = [
        SequencingRead(read_id="read_0", sequence="ACGTACGT", quality="IIIIIIII", barcode="AAAACCCC"),
        SequencingRead(read_id="read_1", sequence="TTTTGGGG", quality="!!!!####", barcode="GGGGTTTT"),
    ]
    path = tmp_path / "reads.fastq"
    write_fastq(reads, path)

    loaded = read_fastq(path)
    assert len(loaded) == 2
    assert loaded[0].read_id == "read_0"
    assert loaded[0].sequence == "ACGTACGT"
    assert loaded[0].quality == "IIIIIIII"
    assert loaded[0].barcode == "AAAACCCC"
    assert loaded[1].barcode == "GGGGTTTT"


def test_reference_panel_round_trip(tmp_path):
    targets = [
        TargetSpec(target_id="t1", sequence="ACGTACGT", abundance=0.1, present=True),
        TargetSpec(target_id="t2", sequence="TTTTAAAA", abundance=0.0, present=False),
    ]
    path = tmp_path / "panel.fasta"
    write_reference_panel(targets, path)

    loaded = read_reference_panel(path)
    assert loaded == [("t1", "ACGTACGT"), ("t2", "TTTTAAAA")]
