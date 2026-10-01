"""Public diagnostic artifacts admit only expected regular evidence files."""

from hashlib import sha256
import json
import os

import pytest

from Tools.CI.DiagnoseOverlap import StageEvidence


def test_staging_preserves_public_evidence_and_indexes_exact_bytes(tmp_path):
    Source = tmp_path / "private"
    Destination = tmp_path / "public"
    Source.mkdir()
    Expected = {
        "runner-traceback.log": b"AssertionError: public source traceback\n",
        "native-build.stdout.log": b"Finished release build\n",
    }
    for Name, Data in Expected.items():
        (Source / Name).write_bytes(Data)
    StageEvidence(Source, Destination)
    assert {Item.name for Item in Destination.iterdir()} == set(Expected) | {"EvidenceIndex.json"}
    assert {Name: (Destination / Name).read_bytes() for Name in Expected} == Expected
    assert json.loads((Destination / "EvidenceIndex.json").read_text()) == {
        "Files": {Name: sha256(Data).hexdigest() for Name, Data in Expected.items()},
    }


@pytest.mark.parametrize("Kind", ["symlink", "directory", "fifo"])
def test_expected_name_must_be_a_regular_file(tmp_path, Kind):
    Source = tmp_path / "private"
    Source.mkdir()
    Secret = tmp_path / "secret"
    Secret.write_text("private sentinel")
    Entry = Source / "pytest.stdout.log"
    if Kind == "symlink":
        Entry.symlink_to(Secret)
    elif Kind == "directory":
        Entry.mkdir()
    else:
        os.mkfifo(Entry)
    Destination = tmp_path / "public"
    with pytest.raises(ValueError):
        StageEvidence(Source, Destination)
    assert not Destination.exists()
    assert Secret.read_text() == "private sentinel"


@pytest.mark.parametrize("Name", ["credentials.json", "environment.txt", ".env", "Cache", "subject"])
def test_unexpected_files_and_trees_abort_public_staging(tmp_path, Name):
    Source = tmp_path / "private"
    Source.mkdir()
    (Source / "Run.json").write_text('{}')
    Entry = Source / Name
    if Name in {"Cache", "subject"}:
        Entry.mkdir()
        (Entry / "private.txt").write_text("private sentinel")
    else:
        Entry.write_text("private sentinel")
    Destination = tmp_path / "public"
    with pytest.raises(ValueError):
        StageEvidence(Source, Destination)
    assert not Destination.exists()


def test_symlinked_evidence_root_is_rejected(tmp_path):
    Actual = tmp_path / "actual"
    Actual.mkdir()
    (Actual / "Run.json").write_text('{}')
    Source = tmp_path / "private"
    Source.symlink_to(Actual, target_is_directory=True)
    with pytest.raises(ValueError):
        StageEvidence(Source, tmp_path / "public")
    assert not (tmp_path / "public").exists()


def test_existing_public_directory_is_not_reused(tmp_path):
    Source = tmp_path / "private"
    Source.mkdir()
    Destination = tmp_path / "public"
    Destination.mkdir()
    (Destination / "old.txt").write_text("prior evidence")
    with pytest.raises(ValueError):
        StageEvidence(Source, Destination)
    assert (Destination / "old.txt").read_text() == "prior evidence"
