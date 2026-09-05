from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from App.BenchmarkArchive import (
    ArchiveChecksumsName,
    ArchiveManifestName,
    BenchmarkArchiveContext,
    BenchmarkArchiveIdentity,
    BenchmarkSourceIdentity,
    BuildBenchmarkArchiveIdentity,
    EnsureArchiveTargetAvailable,
    PublishBenchmarkArchive,
    ReadBenchmarkSourceIdentity,
)


Head = "0123456789abcdef0123456789abcdef01234567"


def _GitQuery(Status: bytes, Branch: bytes = b"Archive-Feature\n"):
    def Query(_Root: Path, Arguments: tuple[str, ...]) -> bytes:
        if Arguments == ("rev-parse", "HEAD"):
            return Head.encode("ascii") + b"\n"
        if Arguments == ("branch", "--show-current"):
            return Branch
        if Arguments == (
            "status",
            "--porcelain=v2",
            "-z",
            "--untracked-files=all",
        ):
            return Status
        raise AssertionError(Arguments)

    return Query


def _SourceIdentity(
    *,
    HeadValue: str = Head,
    Status: bytes = b"",
) -> BenchmarkSourceIdentity:
    return BenchmarkSourceIdentity(
        Head=HeadValue,
        ShortHead=HeadValue[:12],
        Branch="Archive-Feature",
        Detached=False,
        Dirty=bool(Status),
        StatusSha256=sha256(Status).hexdigest(),
        StatusEntries=tuple(
            Entry.decode("utf-8") for Entry in Status.split(b"\0") if Entry
        ),
    )


def _ArchiveContext(
    SourceDirectory: Path,
    ArchiveDirectory: Path,
    *,
    Source: BenchmarkSourceIdentity | None = None,
    BaselineMode: str | None = None,
) -> BenchmarkArchiveContext:
    SourceValue = Source or _SourceIdentity()
    return BenchmarkArchiveContext(
        Identity=BenchmarkArchiveIdentity(
            ArchiveId="20260903T120000.123456Z-0123456789ab",
            CapturedAtUtc="2026-09-03T12:00:00.123456+00:00",
            Source=SourceValue,
        ),
        ArchiveDirectory=ArchiveDirectory,
        SourceDirectory=SourceDirectory,
        Command=("python", "RunRouterAcceptance.py", "--matrix", "expanded"),
        WorkingDirectory=ArchiveDirectory.parent,
        MatrixMode="expanded",
        RoutingThreads=16,
        BaselineMode=BaselineMode,
        StartedAtUtc="2026-09-03T12:00:00+00:00",
    )


def _Manifest() -> dict[str, object]:
    return {
        "SchemaVersion": "router-acceptance-v1",
        "Status": "FAILED",
        "Accepted": False,
        "SourceProvenanceStable": True,
        "Environment": {
            "CpuProfile": {"LogicalCpuCount": 32},
            "LoadProfile": {"Load1": 1.25},
        },
        "SourceProvenance": {
            "SourceContent": {
                "AggregateSha256": "a" * 64,
                "FileCount": 123,
            },
            "NativeExtension": {"Sha256": "b" * 64},
            "Policy": {"Version": "v16"},
            "BenchmarkInputs": {"FabricHarness": {"Sha256": "c" * 64}},
        },
        "Runs": [
            {
                "Sequence": 1,
                "RunName": "FullAdder-Run1",
                "Circuit": "FullAdder",
                "Status": "FAILED",
                "Accepted": False,
                "Evaluation": {
                    "Process": {
                        "WallRuntimeSeconds": 12.5,
                        "ReturnCode": 1,
                        "TimedOut": False,
                    },
                    "Observed": {
                        "FabricValidationStatus": "not-run",
                        "FabricValidationVectors": None,
                    },
                    "Failures": [
                        "missing required artifact: FullAdder.litematic"
                    ],
                },
            }
        ],
    }


def _WriteEvidence(Root: Path) -> bytes:
    Payload = b"raw\x00evidence\n"
    RunDirectory = Root / "FullAdder-Run1" / "Runs" / "compiler-run"
    RunDirectory.mkdir(parents=True)
    (Root / "Summary.txt").write_text(
        "RESULT: FAILURE\nTIME: wall=12.500s\nOUTPUT: failed\n",
        encoding="utf-8",
    )
    (Root / "RawDump.txt").write_bytes(Payload)
    (RunDirectory / "trace.jsonl").write_bytes(b'{"event":"partial"}\n')
    (RunDirectory / "FullAdder.RoutingFailure.json").write_text(
        json.dumps({"Failure": {"Stage": "route", "Reason": "blocked"}}),
        encoding="utf-8",
    )
    return Payload


def _VerifyChecksums(ArchiveRoot: Path) -> None:
    Lines = (ArchiveRoot / ArchiveChecksumsName).read_text(
        encoding="utf-8"
    ).splitlines()
    ListedPaths = set()
    for Line in Lines:
        Digest, RelativePath = Line.split("  ", 1)
        ListedPaths.add(RelativePath)
        assert sha256((ArchiveRoot / RelativePath).read_bytes()).hexdigest() == Digest
    ExpectedPaths = {
        PathValue.relative_to(ArchiveRoot).as_posix()
        for PathValue in ArchiveRoot.rglob("*")
        if PathValue.is_file() and PathValue.name != ArchiveChecksumsName
    }
    assert ListedPaths == ExpectedPaths


def test_clean_and_dirty_archive_ids_are_commit_and_status_stamped(tmp_path: Path):
    Captured = datetime(2026, 9, 3, 12, 34, 56, 123456, tzinfo=timezone.utc)
    Clean = BuildBenchmarkArchiveIdentity(
        tmp_path,
        CapturedAtUtc=Captured,
        GitQuery=_GitQuery(b""),
    )
    Status = b"1 M. N... 100644 100644 100644 abc def App/Main.py\0"
    Dirty = BuildBenchmarkArchiveIdentity(
        tmp_path,
        CapturedAtUtc=Captured,
        GitQuery=_GitQuery(Status),
    )

    assert Clean.ArchiveId == "20260903T123456.123456Z-0123456789ab"
    assert Dirty.ArchiveId == (
        "20260903T123456.123456Z-0123456789ab-dirty-"
        f"{sha256(Status).hexdigest()[:12]}"
    )
    assert Dirty.Source.StatusSha256 == sha256(Status).hexdigest()
    assert Dirty.Source.StatusEntries == (Status[:-1].decode("utf-8"),)


def test_staged_unstaged_and_untracked_statuses_have_distinct_stable_digests(
    tmp_path: Path,
):
    Statuses = (
        b"1 M. N... staged.py\0",
        b"1 .M N... unstaged.py\0",
        b"? untracked.py\0",
    )
    FirstReads = [
        ReadBenchmarkSourceIdentity(tmp_path, GitQuery=_GitQuery(Status))
        for Status in Statuses
    ]
    SecondReads = [
        ReadBenchmarkSourceIdentity(tmp_path, GitQuery=_GitQuery(Status))
        for Status in Statuses
    ]

    assert len({Identity.StatusSha256 for Identity in FirstReads}) == 3
    assert [Identity.StatusSha256 for Identity in FirstReads] == [
        Identity.StatusSha256 for Identity in SecondReads
    ]


def test_archive_collision_is_refused(tmp_path: Path):
    Target = tmp_path / "archive"
    Target.mkdir()
    with pytest.raises(FileExistsError, match="already exists"):
        EnsureArchiveTargetAvailable(Target)


def test_in_place_archive_seals_complete_inventory_and_checksums(tmp_path: Path):
    ArchiveRoot = tmp_path / "archive"
    _WriteEvidence(ArchiveRoot)
    Context = _ArchiveContext(ArchiveRoot, ArchiveRoot)

    Published = PublishBenchmarkArchive(
        Context,
        _Manifest(),
        CompletedAtUtc="2026-09-03T12:00:13+00:00",
        WallSeconds=13.0,
        ExitCode=1,
        ExitClassification="benchmark-failed",
        SourceIdentityReader=lambda _Root: Context.Identity.Source,
    )

    assert Published == ArchiveRoot.resolve()
    ArchiveManifest = json.loads(
        (ArchiveRoot / ArchiveManifestName).read_text(encoding="utf-8")
    )
    assert ArchiveManifest["SchemaVersion"] == "router-benchmark-archive-v1"
    assert ArchiveManifest["Publication"] == {
        "Complete": True,
        "Failure": None,
        "Status": "SEALED",
    }
    assert ArchiveManifest["Benchmark"]["ExitClassification"] == "benchmark-failed"
    assert ArchiveManifest["Benchmark"]["Runs"][0]["Stage"] == "route"
    assert ArchiveManifest["Benchmark"]["Runs"][0]["Reason"] == "blocked"
    assert ArchiveManifest["Benchmark"]["Runs"][0][
        "MissingRequiredArtifacts"
    ] == ["FullAdder.litematic"]
    assert not list(ArchiveRoot.glob(".*.tmp-*"))
    _VerifyChecksums(ArchiveRoot)


def test_legacy_session_is_mirrored_byte_for_byte(tmp_path: Path):
    SourceRoot = tmp_path / "BaselineCapture"
    OriginalRaw = _WriteEvidence(SourceRoot)
    Target = tmp_path / "Archives" / "archive"
    Context = _ArchiveContext(
        SourceRoot,
        Target,
        BaselineMode="capture",
    )

    PublishBenchmarkArchive(
        Context,
        _Manifest(),
        CompletedAtUtc="2026-09-03T12:00:13+00:00",
        WallSeconds=13.0,
        ExitCode=1,
        ExitClassification="benchmark-failed",
        SourceIdentityReader=lambda _Root: Context.Identity.Source,
    )

    assert (Target / "RawDump.txt").read_bytes() == OriginalRaw
    assert (SourceRoot / "RawDump.txt").read_bytes() == OriginalRaw
    assert not (SourceRoot / ArchiveManifestName).exists()
    _VerifyChecksums(Target)


def test_archive_rejects_symlinks_without_following_them(tmp_path: Path):
    SourceRoot = tmp_path / "CandidateComparison"
    SourceRoot.mkdir()
    Secret = tmp_path / "runtime-secret.txt"
    Secret.write_text("do not copy", encoding="utf-8")
    (SourceRoot / "unsafe-link").symlink_to(Secret)
    Target = tmp_path / "Archives" / "archive"
    Context = _ArchiveContext(SourceRoot, Target, BaselineMode="compare")

    with pytest.raises(ValueError, match="must not contain symlinks"):
        PublishBenchmarkArchive(
            Context,
            _Manifest(),
            CompletedAtUtc="2026-09-03T12:00:13+00:00",
            WallSeconds=13.0,
            ExitCode=1,
            ExitClassification="benchmark-failed",
            SourceIdentityReader=lambda _Root: Context.Identity.Source,
        )

    assert not Target.exists()


@pytest.mark.parametrize(
    ("PublicationStatus", "ExitClassification"),
    (("PARTIAL", "unexpected-harness-failure"), ("INTERRUPTED", "interrupted")),
)
def test_incomplete_archives_preserve_failure_surface(
    tmp_path: Path,
    PublicationStatus: str,
    ExitClassification: str,
):
    ArchiveRoot = tmp_path / PublicationStatus.lower()
    _WriteEvidence(ArchiveRoot)
    Context = _ArchiveContext(ArchiveRoot, ArchiveRoot)

    PublishBenchmarkArchive(
        Context,
        _Manifest(),
        CompletedAtUtc="2026-09-03T12:00:13+00:00",
        WallSeconds=13.0,
        ExitCode=130 if PublicationStatus == "INTERRUPTED" else 1,
        ExitClassification=ExitClassification,
        PublicationStatus=PublicationStatus,
        PublicationFailure="synthetic failure",
        SourceIdentityReader=lambda _Root: Context.Identity.Source,
    )

    ArchiveManifest = json.loads(
        (ArchiveRoot / ArchiveManifestName).read_text(encoding="utf-8")
    )
    assert ArchiveManifest["Publication"]["Status"] == PublicationStatus
    assert ArchiveManifest["Publication"]["Complete"] is False
    assert ArchiveManifest["Benchmark"]["ExitClassification"] == ExitClassification
    _VerifyChecksums(ArchiveRoot)


def test_source_drift_is_preserved_and_marked_unstable(tmp_path: Path):
    ArchiveRoot = tmp_path / "archive"
    _WriteEvidence(ArchiveRoot)
    Context = _ArchiveContext(ArchiveRoot, ArchiveRoot)
    EndSource = _SourceIdentity(
        Status=b"? source-created-during-run.py\0",
    )

    PublishBenchmarkArchive(
        Context,
        _Manifest(),
        CompletedAtUtc="2026-09-03T12:00:13+00:00",
        WallSeconds=13.0,
        ExitCode=1,
        ExitClassification="benchmark-failed",
        SourceIdentityReader=lambda _Root: EndSource,
    )

    ArchiveManifest = json.loads(
        (ArchiveRoot / ArchiveManifestName).read_text(encoding="utf-8")
    )
    assert ArchiveManifest["Source"]["Stable"] is False
    assert ArchiveManifest["Source"]["Start"]["Dirty"] is False
    assert ArchiveManifest["Source"]["End"]["Dirty"] is True
