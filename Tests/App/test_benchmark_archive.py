from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
import App.BenchmarkArchive as BenchmarkArchive

from App.BenchmarkArchive import (
    ArchiveChecksumsName,
    ArchiveManifestName,
    BenchmarkArchiveContext,
    BenchmarkArchiveIdentity,
    BenchmarkSourceIdentity,
    BuildBenchmarkArchiveIdentity,
    BuildArchiveRunSurface,
    EnsureArchiveTargetAvailable,
    PublishBenchmarkArchive,
    ReadBenchmarkSourceIdentity,
)


Head = "0123456789abcdef0123456789abcdef01234567"
PolicySnapshot = {
    "PolicyVersion": "physical-design-v17-routing-aware-placement-access",
    "RuntimeBudgetSeconds": 120.0,
    "Seed": 0,
}
PolicyIdentity = {
    "RoutingStrategy": "default",
    "RequestedRoutingStrategy": "default",
    "UsedRoutingStrategy": "default",
    "PolicyVersion": PolicySnapshot["PolicyVersion"],
    "Seed": 0,
    "Sha256": sha256(json.dumps(
        PolicySnapshot,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest(),
    "Snapshot": PolicySnapshot,
}


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
        "RoutingIdentity": {
            "ConfiguredRequestedStrategy": "default",
            "ExpectedUsedStrategy": "default",
            "PolicyIdentity": PolicyIdentity,
        },
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
            "Policy": PolicyIdentity,
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
                        "ConfiguredRoutingIdentity": {
                            "RequestedStrategy": "default",
                            "UsedStrategy": "default",
                            "CommandRoutingStrategy": "default",
                            "PolicyIdentity": PolicyIdentity,
                        },
                        "ActualRoutingIdentity": {
                            "RequestedStrategy": None,
                            "UsedStrategy": None,
                            "FallbackUsed": None,
                            "PolicyIdentity": None,
                        },
                        "RoutingIdentityChecks": {
                            "ConfiguredCommandMatches": True,
                            "ConfiguredSourcePolicyMatches": True,
                            "ActualArtifactPresent": False,
                            "ActualPolicyIdentityMatches": None,
                        },
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
        json.dumps({
            "Strategy": {
                "Requested": "default",
                "Used": "default",
                "FallbackUsed": False,
            },
            "Policy": PolicySnapshot,
            "Reproduction": {"RequestedStrategy": "default"},
            "Failure": {"Stage": "route", "Reason": "blocked"},
        }),
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
    FailurePath = (
        ArchiveRoot
        / "FullAdder-Run1"
        / "Runs"
        / "compiler-run"
        / "FullAdder.RoutingFailure.json"
    )
    StalePath = (
        ArchiveRoot
        / "FullAdder-Run1"
        / "Runs"
        / "stale-run"
        / "FullAdder.RoutingFailure.json"
    )
    StalePath.parent.mkdir(parents=True)
    StalePath.write_text(
        json.dumps({"Failure": {"Stage": "stale", "Reason": "wrong"}}),
        encoding="utf-8",
    )
    Manifest = _Manifest()
    Evaluation = Manifest["Runs"][0]["Evaluation"]
    assert isinstance(Evaluation, dict)
    Evaluation["Artifacts"] = {
        "RoutingFailure": {
            "Path": str(FailurePath),
            "Exists": True,
            "Sha256": sha256(FailurePath.read_bytes()).hexdigest(),
        },
    }
    Evaluation["Observed"]["FailureArtifactResolution"] = {
        "Status": "nested",
        "Path": str(FailurePath),
        "CandidatePath": str(FailurePath),
        "Diagnostic": None,
    }
    Context = _ArchiveContext(ArchiveRoot, ArchiveRoot)

    Published = PublishBenchmarkArchive(
        Context,
        Manifest,
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
    ArchivedRun = ArchiveManifest["Benchmark"]["Runs"][0]
    assert ArchivedRun["ConfiguredRoutingIdentity"][
        "PolicyIdentity"
    ] == PolicyIdentity
    assert ArchivedRun["ActualRoutingIdentity"]["PolicyIdentity"] is None
    assert ArchivedRun["FailureRoutingIdentity"] == {
        "RequestedStrategy": "default",
        "UsedStrategy": "default",
        "FallbackUsed": False,
        "PolicyIdentity": {
            "PolicyVersion": PolicySnapshot["PolicyVersion"],
            "Seed": 0,
            "Sha256": PolicyIdentity["Sha256"],
            "Snapshot": PolicySnapshot,
        },
        "ReproductionRequestedStrategy": "default",
    }
    assert ArchiveManifest["Runtime"]["RoutingIdentity"] == {
        "ConfiguredRequestedStrategy": "default",
        "ExpectedUsedStrategy": "default",
        "PolicyIdentity": PolicyIdentity,
    }
    assert not list(ArchiveRoot.glob(".*.tmp-*"))
    _VerifyChecksums(ArchiveRoot)


def test_archive_surface_keeps_nonobject_failure_evidence_unknown(
    tmp_path: Path,
) -> None:
    ArchiveRoot = tmp_path / "archive"
    FailurePath = (
        ArchiveRoot
        / "FullAdder-Run1"
        / "Runs"
        / "current-run"
        / "FullAdder-Run1.RoutingFailure.json"
    )
    FailurePath.parent.mkdir(parents=True)
    FailurePath.write_text("[]\n", encoding="utf-8")
    Manifest = _Manifest()
    Evaluation = Manifest["Runs"][0]["Evaluation"]
    assert isinstance(Evaluation, dict)
    Evaluation["Artifacts"] = {
        "RoutingFailure": {
            "Path": str(FailurePath),
            "Exists": True,
            "Sha256": sha256(FailurePath.read_bytes()).hexdigest(),
        },
    }
    Evaluation["Observed"]["FailureArtifactResolution"] = {
        "Status": "nested",
        "Path": str(FailurePath),
        "CandidatePath": str(FailurePath),
        "Diagnostic": None,
    }

    Surface = BuildArchiveRunSurface(Manifest, ArchiveRoot)

    assert len(Surface) == 1
    assert Surface[0]["Status"] == "FAILED"
    assert Surface[0]["Accepted"] is False
    assert Surface[0]["Stage"] is None
    assert Surface[0]["Reason"] is None
    assert Surface[0]["FailureRoutingIdentity"] is None


def test_archive_surface_does_not_project_rejected_raw_failure(
    tmp_path: Path,
) -> None:
    ArchiveRoot = tmp_path / "archive"
    FailurePath = (
        ArchiveRoot
        / "FullAdder-Run1"
        / "FullAdder-Run1.RoutingFailure.json"
    )
    FailurePath.parent.mkdir(parents=True)
    FailurePath.write_text(
        json.dumps({
            "Failure": {"Stage": "stale", "Reason": "wrong-source"},
        }) + "\n",
        encoding="utf-8",
    )
    Manifest = _Manifest()
    Evaluation = Manifest["Runs"][0]["Evaluation"]
    assert isinstance(Evaluation, dict)
    Evaluation["Artifacts"] = {
        "RoutingFailure": {
            "Path": str(FailurePath),
            "Exists": True,
            "SizeBytes": FailurePath.stat().st_size,
            "Sha256": sha256(FailurePath.read_bytes()).hexdigest(),
        },
    }
    Evaluation["Observed"]["FailureArtifactResolution"] = {
        "Status": "rejected",
        "Path": None,
        "CandidatePath": str(FailurePath),
        "Diagnostic": "routing failure source revision does not match",
    }

    Surface = BuildArchiveRunSurface(Manifest, ArchiveRoot)

    assert Evaluation["Artifacts"]["RoutingFailure"]["Exists"] is True
    assert Surface[0]["Status"] == "FAILED"
    assert Surface[0]["Stage"] is None
    assert Surface[0]["Reason"] is None
    assert Surface[0]["FailureRoutingIdentity"] is None


def test_archive_surface_never_follows_a_recorded_failure_symlink(
    tmp_path: Path,
) -> None:
    ArchiveRoot = tmp_path / "archive"
    FailurePath = (
        ArchiveRoot
        / "FullAdder-Run1"
        / "Runs"
        / "current-run"
        / "FullAdder-Run1.RoutingFailure.json"
    )
    FailurePath.parent.mkdir(parents=True)
    ExternalTarget = tmp_path / "external-secret.json"
    ExternalTarget.write_text(
        json.dumps({
            "Failure": {"Stage": "secret-stage", "Reason": "secret-reason"},
        }) + "\n",
        encoding="utf-8",
    )
    FailurePath.symlink_to(ExternalTarget)
    Manifest = _Manifest()
    Evaluation = Manifest["Runs"][0]["Evaluation"]
    assert isinstance(Evaluation, dict)
    Evaluation["Artifacts"] = {
        "RoutingFailure": {
            "Path": str(FailurePath),
            "Exists": True,
            "SizeBytes": ExternalTarget.stat().st_size,
            "Sha256": sha256(ExternalTarget.read_bytes()).hexdigest(),
        },
    }
    Evaluation["Observed"]["FailureArtifactResolution"] = {
        "Status": "nested",
        "Path": str(FailurePath),
        "CandidatePath": str(FailurePath),
        "Diagnostic": None,
    }

    Surface = BuildArchiveRunSurface(Manifest, ArchiveRoot)

    assert Surface[0]["Stage"] is None
    assert Surface[0]["Reason"] is None
    assert Surface[0]["FailureRoutingIdentity"] is None
    assert "secret" not in json.dumps(Surface[0])


def test_archive_surface_rejects_failure_path_that_escapes_archive_root(
    tmp_path: Path,
) -> None:
    ArchiveRoot = tmp_path / "archive"
    ArchiveRoot.mkdir()
    Outside = tmp_path / "outside"
    Outside.mkdir()
    ExternalTarget = Outside / "FullAdder-Run1.RoutingFailure.json"
    ExternalTarget.write_text(
        json.dumps({
            "Failure": {
                "Stage": "external-stage",
                "Reason": "external-reason",
                "Detail": "external-secret",
            },
        }) + "\n",
        encoding="utf-8",
    )
    RunName = "FullAdder-Run1"
    EscapingPath = (
        tmp_path
        / "producer"
        / RunName
        / ".."
        / ".."
        / Outside.name
        / ExternalTarget.name
    )
    Manifest = _Manifest()
    Evaluation = Manifest["Runs"][0]["Evaluation"]
    assert isinstance(Evaluation, dict)
    Evaluation["Artifacts"] = {
        "RoutingFailure": {
            "Path": str(EscapingPath),
            "Exists": True,
            "SizeBytes": ExternalTarget.stat().st_size,
            "Sha256": sha256(ExternalTarget.read_bytes()).hexdigest(),
        },
    }
    Evaluation["Observed"]["FailureArtifactResolution"] = {
        "Status": "nested",
        "Path": str(EscapingPath),
        "CandidatePath": str(EscapingPath),
        "Diagnostic": None,
    }

    Surface = BuildArchiveRunSurface(Manifest, ArchiveRoot)

    assert Surface[0]["Stage"] is None
    assert Surface[0]["Reason"] is None
    assert Surface[0]["Detail"] is None
    assert Surface[0]["FailureRoutingIdentity"] is None
    assert "external-secret" not in json.dumps(Surface[0])


def test_archive_reader_never_follows_an_intermediate_directory_swap(
    tmp_path: Path,
) -> None:
    ArchiveRoot = tmp_path / "archive"
    Parent = ArchiveRoot / "parent"
    Outside = tmp_path / "outside"
    Parent.mkdir(parents=True)
    Outside.mkdir()
    Candidate = Parent / "evidence.bin"
    Candidate.write_bytes(b"inside")
    (Outside / Candidate.name).write_bytes(b"outside")
    OriginalOpen = BenchmarkArchive.os.open
    Swapped = False

    def RacingOpen(PathValue, Flags, *Arguments, **Keywords):
        nonlocal Swapped
        if Path(PathValue).name == Candidate.name and not Swapped:
            Swapped = True
            Parent.rename(ArchiveRoot / "original-parent")
            Parent.symlink_to(Outside, target_is_directory=True)
        return OriginalOpen(PathValue, Flags, *Arguments, **Keywords)

    with (
        patch.object(BenchmarkArchive.os, "open", RacingOpen),
        patch.object(
            BenchmarkArchive,
            "_SafeOpenPrimitivesAvailable",
            return_value=True,
        ),
    ):
        Observed = BenchmarkArchive._ReadRegularFileWithoutFollowing(
            Candidate
        )

    assert Swapped
    assert Observed in {None, b"inside"}
    assert Observed != b"outside"


def test_archive_reader_keeps_opened_leaf_bytes_after_path_replacement(
    tmp_path: Path,
) -> None:
    Candidate = tmp_path / "evidence.bin"
    Replacement = tmp_path / "replacement.bin"
    Candidate.write_bytes(b"inside")
    Replacement.write_bytes(b"outside")
    OriginalOpen = BenchmarkArchive.os.open
    Swapped = False

    def RacingOpen(PathValue, Flags, *Arguments, **Keywords):
        nonlocal Swapped
        Descriptor = OriginalOpen(PathValue, Flags, *Arguments, **Keywords)
        if Path(PathValue).name == Candidate.name and not Swapped:
            Swapped = True
            Candidate.rename(tmp_path / "original.bin")
            Replacement.rename(Candidate)
        return Descriptor

    with (
        patch.object(BenchmarkArchive.os, "open", RacingOpen),
        patch.object(
            BenchmarkArchive,
            "_SafeOpenPrimitivesAvailable",
            return_value=True,
        ),
    ):
        Observed = BenchmarkArchive._ReadRegularFileWithoutFollowing(
            Candidate
        )

    assert Swapped
    assert Observed == b"inside"


def test_archive_reader_fails_closed_without_safe_open_primitives(
    tmp_path: Path,
) -> None:
    Candidate = tmp_path / "evidence.bin"
    Candidate.write_bytes(b"inside")

    with patch.object(BenchmarkArchive.os, "supports_dir_fd", frozenset()):
        Observed = BenchmarkArchive._ReadRegularFileWithoutFollowing(
            Candidate
        )

    assert Observed is None


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


def test_legacy_mirror_keeps_opened_source_root_after_real_replacement(
    tmp_path: Path,
) -> None:
    SourceRoot = tmp_path / "BaselineCapture"
    OriginalRaw = _WriteEvidence(SourceRoot)
    ReplacementRoot = tmp_path / "ReplacementCapture"
    _WriteEvidence(ReplacementRoot)
    ReplacementRaw = b"OUTSIDE-replacement\n"
    (ReplacementRoot / "RawDump.txt").write_bytes(ReplacementRaw)
    OriginalRoot = tmp_path / "OriginalCapture"
    Target = tmp_path / "Archives" / "archive"
    Context = _ArchiveContext(SourceRoot, Target, BaselineMode="capture")
    OriginalResolve = Path.resolve
    OriginalOpen = BenchmarkArchive.os.open
    Swapped = False

    def SwapRoots() -> None:
        nonlocal Swapped
        if Swapped:
            return
        Swapped = True
        SourceRoot.rename(OriginalRoot)
        ReplacementRoot.rename(SourceRoot)

    def RacingResolve(PathValue: Path, *Arguments, **Keywords):
        Resolved = OriginalResolve(PathValue, *Arguments, **Keywords)
        if PathValue == SourceRoot:
            SwapRoots()
        return Resolved

    def RacingOpen(PathValue, Flags, *Arguments, **Keywords):
        Descriptor = OriginalOpen(PathValue, Flags, *Arguments, **Keywords)
        if (
            Path(PathValue).name == SourceRoot.name
            and Flags & BenchmarkArchive.os.O_DIRECTORY
        ):
            SwapRoots()
        return Descriptor

    with (
        patch.object(Path, "resolve", RacingResolve),
        patch.object(BenchmarkArchive.os, "open", RacingOpen),
        patch.object(
            BenchmarkArchive,
            "_SafeOpenPrimitivesAvailable",
            return_value=True,
        ),
    ):
        PublishBenchmarkArchive(
            Context,
            _Manifest(),
            CompletedAtUtc="2026-09-03T12:00:13+00:00",
            WallSeconds=13.0,
            ExitCode=1,
            ExitClassification="benchmark-failed",
            SourceIdentityReader=lambda _Root: Context.Identity.Source,
        )

    assert Swapped
    assert (Target / "RawDump.txt").read_bytes() == OriginalRaw
    assert (Target / "RawDump.txt").read_bytes() != ReplacementRaw
    _VerifyChecksums(Target)


def test_legacy_mirror_rejects_source_root_symlink(tmp_path: Path) -> None:
    RealSource = tmp_path / "RealSource"
    _WriteEvidence(RealSource)
    (RealSource / "RawDump.txt").write_bytes(b"OUTSIDE-symlink-target\n")
    SourceLink = tmp_path / "BaselineCapture"
    SourceLink.symlink_to(RealSource, target_is_directory=True)
    Target = tmp_path / "Archives" / "archive"
    Context = _ArchiveContext(SourceLink, Target, BaselineMode="capture")

    with pytest.raises(ValueError, match="symlink|safely|directory"):
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


def test_legacy_mirror_avoids_pathname_operations_after_root_selection(
    tmp_path: Path,
) -> None:
    SourceRoot = tmp_path / "BaselineCapture"
    OriginalRaw = _WriteEvidence(SourceRoot)
    Target = tmp_path / "Archives" / "archive"
    Context = _ArchiveContext(SourceRoot, Target, BaselineMode="capture")
    OriginalResolve = Path.resolve
    OriginalIsDir = Path.is_dir
    OriginalRglob = Path.rglob
    OriginalStat = Path.stat
    OriginalOpen = Path.open
    OriginalOsOpen = BenchmarkArchive.os.open
    SourceRootSelected = False

    def IsGuarded(PathValue: Path) -> bool:
        Absolute = Path(os.path.abspath(PathValue))
        return (
            Absolute == SourceRoot
            or Absolute.is_relative_to(SourceRoot)
            or Absolute.name.startswith(f".{Target.name}.tmp-P")
        )

    def Guarded(Method, Name):
        def Invoke(PathValue: Path, *Arguments, **Keywords):
            if SourceRootSelected and IsGuarded(PathValue):
                raise AssertionError(f"forbidden pathname {Name}: {PathValue}")
            return Method(PathValue, *Arguments, **Keywords)

        return Invoke

    def ObserveRootSelection(PathValue, Flags, *Arguments, **Keywords):
        nonlocal SourceRootSelected
        Descriptor = OriginalOsOpen(PathValue, Flags, *Arguments, **Keywords)
        if (
            Path(PathValue).name == SourceRoot.name
            and Flags & BenchmarkArchive.os.O_DIRECTORY
        ):
            SourceRootSelected = True
        return Descriptor

    with (
        patch.object(Path, "resolve", Guarded(OriginalResolve, "resolve")),
        patch.object(Path, "is_dir", Guarded(OriginalIsDir, "is_dir")),
        patch.object(Path, "rglob", Guarded(OriginalRglob, "rglob")),
        patch.object(Path, "stat", Guarded(OriginalStat, "stat")),
        patch.object(Path, "open", Guarded(OriginalOpen, "open")),
        patch.object(BenchmarkArchive.os, "open", ObserveRootSelection),
        patch.object(
            BenchmarkArchive,
            "_SafeOpenPrimitivesAvailable",
            return_value=True,
        ),
        patch.object(
            BenchmarkArchive.shutil,
            "copytree",
            side_effect=AssertionError("forbidden copytree"),
        ),
        patch.object(
            BenchmarkArchive.shutil,
            "copy2",
            side_effect=AssertionError("forbidden copy2"),
        ),
    ):
        PublishBenchmarkArchive(
            Context,
            _Manifest(),
            CompletedAtUtc="2026-09-03T12:00:13+00:00",
            WallSeconds=13.0,
            ExitCode=1,
            ExitClassification="benchmark-failed",
            SourceIdentityReader=lambda _Root: Context.Identity.Source,
        )

    assert SourceRootSelected
    assert (Target / "RawDump.txt").read_bytes() == OriginalRaw
    _VerifyChecksums(Target)


def test_rejected_mirror_never_deletes_replacement_staging_root(
    tmp_path: Path,
) -> None:
    SourceRoot = tmp_path / "CandidateComparison"
    _WriteEvidence(SourceRoot)
    Secret = tmp_path / "runtime-secret.txt"
    Secret.write_text("must not be copied", encoding="utf-8")
    (SourceRoot / "unsafe-link").symlink_to(Secret)
    Target = tmp_path / "Archives" / "archive"
    Staging = Target.parent / f".{Target.name}.tmp-P{os.getpid()}"
    HiddenOpenedStaging = tmp_path / "opened-staging-object"
    Replacement = tmp_path / "replacement-staging-object"
    Replacement.mkdir()
    Sentinel = Replacement / "sentinel.txt"
    SentinelBytes = b"replacement directory must remain untouched\n"
    Sentinel.write_bytes(SentinelBytes)
    Context = _ArchiveContext(SourceRoot, Target, BaselineMode="compare")
    OriginalOsOpen = BenchmarkArchive.os.open
    Replaced = False

    def ReplaceAfterStagingOpen(PathValue, Flags, *Arguments, **Keywords):
        nonlocal Replaced
        Descriptor = OriginalOsOpen(PathValue, Flags, *Arguments, **Keywords)
        if (
            not Replaced
            and Path(PathValue).name == Staging.name
            and Flags & BenchmarkArchive.os.O_DIRECTORY
        ):
            Replaced = True
            Staging.rename(HiddenOpenedStaging)
            Replacement.rename(Staging)
        return Descriptor

    with (
        patch.object(
            BenchmarkArchive.os,
            "open",
            ReplaceAfterStagingOpen,
        ),
        patch.object(
            BenchmarkArchive,
            "_SafeOpenPrimitivesAvailable",
            return_value=True,
        ),
    ):
        with pytest.raises(ValueError, match="symlink|changed"):
            PublishBenchmarkArchive(
                Context,
                _Manifest(),
                CompletedAtUtc="2026-09-03T12:00:13+00:00",
                WallSeconds=13.0,
                ExitCode=1,
                ExitClassification="benchmark-failed",
                SourceIdentityReader=lambda _Root: Context.Identity.Source,
            )

    assert Replaced
    assert Staging.is_dir()
    assert (Staging / Sentinel.name).read_bytes() == SentinelBytes
    assert not Target.exists()


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


def test_archive_publication_rejects_mutation_between_inventory_and_checksums(
    tmp_path: Path,
) -> None:
    ArchiveRoot = tmp_path / "archive"
    _WriteEvidence(ArchiveRoot)
    Evidence = ArchiveRoot / "RawDump.txt"
    Context = _ArchiveContext(ArchiveRoot, ArchiveRoot)
    OriginalWriteChecksums = BenchmarkArchive._WriteChecksums
    Mutated = False

    def RacingWriteChecksums(Root: Path, *Arguments, **Keywords):
        nonlocal Mutated
        Evidence.write_bytes(b"changed after inventory\n")
        Mutated = True
        return OriginalWriteChecksums(Root, *Arguments, **Keywords)

    with patch.object(
        BenchmarkArchive,
        "_WriteChecksums",
        RacingWriteChecksums,
    ):
        with pytest.raises(ValueError, match="changed|seal|checksum"):
            PublishBenchmarkArchive(
                Context,
                _Manifest(),
                CompletedAtUtc="2026-09-03T12:00:13+00:00",
                WallSeconds=13.0,
                ExitCode=1,
                ExitClassification="benchmark-failed",
                SourceIdentityReader=lambda _Root: Context.Identity.Source,
            )

    assert Mutated
    if (ArchiveRoot / ArchiveManifestName).exists():
        ArchiveManifest = json.loads(
            (ArchiveRoot / ArchiveManifestName).read_text(encoding="utf-8")
        )
        assert ArchiveManifest["Publication"]["Status"] != "SEALED"


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


@pytest.mark.parametrize(
    ("PublicationStatus", "ExitClassification"),
    (("PARTIAL", "unexpected-harness-failure"), ("INTERRUPTED", "interrupted")),
)
def test_unobserved_end_source_content_remains_unknown_for_incomplete_archives(
    tmp_path: Path,
    PublicationStatus: str,
    ExitClassification: str,
):
    """An incomplete session cannot manufacture end content provenance."""
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
        PublicationFailure="end content observation unavailable",
        SourceIdentityReader=lambda _Root: Context.Identity.Source,
    )

    ArchiveManifest = json.loads(
        (ArchiveRoot / ArchiveManifestName).read_text(encoding="utf-8")
    )
    assert ArchiveManifest["Publication"]["Status"] == PublicationStatus
    assert ArchiveManifest["Benchmark"]["Accepted"] is False
    assert ArchiveManifest["Source"]["Start"] == ArchiveManifest["Source"]["End"]
    SourceContent = ArchiveManifest["Source"]["SourceContent"]
    assert SourceContent["Start"] == {
        "AggregateSha256": "a" * 64,
        "FileCount": 123,
    }
    assert SourceContent["End"] is None
    assert SourceContent["Stable"] is None


@pytest.mark.parametrize(
    ("EndAggregateSha256", "EndFileCount", "ExpectedStable"),
    (
        ("a" * 64, 123, True),
        ("b" * 64, 123, False),
        ("a" * 64, 124, False),
    ),
)
def test_observed_end_source_content_records_stability(
    tmp_path: Path,
    EndAggregateSha256: str,
    EndFileCount: int,
    ExpectedStable: bool,
):
    """Observed end content remains distinct from the Git end identity."""
    ArchiveRoot = tmp_path / "observed-content"
    _WriteEvidence(ArchiveRoot)
    Context = _ArchiveContext(ArchiveRoot, ArchiveRoot)
    Manifest = _Manifest()
    Manifest["ProvenanceChecks"] = [{
        "SourceProvenance": {
            "SourceContent": {
                "AggregateSha256": EndAggregateSha256,
                "FileCount": EndFileCount,
            },
        },
    }]

    PublishBenchmarkArchive(
        Context,
        Manifest,
        CompletedAtUtc="2026-09-03T12:00:13+00:00",
        WallSeconds=13.0,
        ExitCode=1,
        ExitClassification="benchmark-failed",
        SourceIdentityReader=lambda _Root: Context.Identity.Source,
    )

    ArchiveManifest = json.loads(
        (ArchiveRoot / ArchiveManifestName).read_text(encoding="utf-8")
    )
    assert ArchiveManifest["Source"]["Start"] == ArchiveManifest["Source"]["End"]
    assert ArchiveManifest["Source"]["SourceContent"]["End"] == {
        "AggregateSha256": EndAggregateSha256,
        "FileCount": EndFileCount,
    }
    assert ArchiveManifest["Source"]["SourceContent"]["Stable"] is ExpectedStable


@pytest.mark.parametrize(
    ("StartSourceContent", "EndSourceContent"),
    (
        (None, {"AggregateSha256": "a" * 64, "FileCount": 123}),
        ({"AggregateSha256": "a" * 64, "FileCount": 123}, None),
        ({}, {}),
        (
            {"AggregateSha256": "a" * 64},
            {"AggregateSha256": "a" * 64, "FileCount": 123},
        ),
        (
            {"FileCount": 123},
            {"AggregateSha256": "a" * 64, "FileCount": 123},
        ),
        (
            {"AggregateSha256": "a" * 64, "FileCount": 123},
            {"AggregateSha256": "a" * 64},
        ),
        (
            {"AggregateSha256": "a" * 64, "FileCount": 123},
            {"FileCount": 123},
        ),
        (
            {"AggregateSha256": 7, "FileCount": 123},
            {"AggregateSha256": "a" * 64, "FileCount": 123},
        ),
        (
            {"AggregateSha256": "not-a-sha256", "FileCount": 123},
            {"AggregateSha256": "a" * 64, "FileCount": 123},
        ),
        (
            {"AggregateSha256": "a" * 64, "FileCount": "123"},
            {"AggregateSha256": "a" * 64, "FileCount": 123},
        ),
        (
            {"AggregateSha256": "a" * 64, "FileCount": 123},
            {"AggregateSha256": "a" * 64, "FileCount": True},
        ),
    ),
)
def test_source_content_stability_requires_two_complete_valid_observations(
    tmp_path: Path,
    StartSourceContent: dict[str, object] | None,
    EndSourceContent: dict[str, object] | None,
) -> None:
    """Missing or invalid observations cannot manufacture stability."""
    ArchiveRoot = tmp_path / "invalid-source-observation"
    _WriteEvidence(ArchiveRoot)
    Context = _ArchiveContext(ArchiveRoot, ArchiveRoot)
    Manifest = _Manifest()
    SourceProvenance = Manifest["SourceProvenance"]
    assert isinstance(SourceProvenance, dict)
    if StartSourceContent is None:
        SourceProvenance.pop("SourceContent")
    else:
        SourceProvenance["SourceContent"] = StartSourceContent
    Manifest["ProvenanceChecks"] = (
        []
        if EndSourceContent is None
        else [{"SourceProvenance": {"SourceContent": EndSourceContent}}]
    )

    PublishBenchmarkArchive(
        Context,
        Manifest,
        CompletedAtUtc="2026-09-03T12:00:13+00:00",
        WallSeconds=13.0,
        ExitCode=1,
        ExitClassification="benchmark-failed",
        SourceIdentityReader=lambda _Root: Context.Identity.Source,
    )

    ArchiveManifest = json.loads(
        (ArchiveRoot / ArchiveManifestName).read_text(encoding="utf-8")
    )
    assert ArchiveManifest["Source"]["SourceContent"]["Stable"] is None


@pytest.mark.parametrize(
    ("ProvenanceChecks", "ExpectedStable"),
    (
        (
            [
                {"SourceProvenance": {"SourceContent": {
                    "AggregateSha256": "a" * 64,
                    "FileCount": 123,
                }}},
                "invalid-newest-observation",
            ],
            None,
        ),
        ([{"SourceProvenance": {"SourceContent": {
            "AggregateSha256": "a" * 64,
            "FileCount": 123,
        }}}, {}], None),
        ([{"SourceProvenance": {"SourceContent": {
            "AggregateSha256": "a" * 64,
            "FileCount": 123,
        }}}, {"SourceProvenance": "invalid"}], None),
        ([{"SourceProvenance": {"SourceContent": {
            "AggregateSha256": "a" * 64,
            "FileCount": 123,
        }}}, {"SourceProvenance": {}}], None),
        ([{"SourceProvenance": {"SourceContent": {
            "AggregateSha256": "a" * 64,
            "FileCount": 123,
        }}}, {"SourceProvenance": {
            "SourceContent": "invalid",
        }}], None),
        (["invalid-older-observation", {"SourceProvenance": {
            "SourceContent": {
                "AggregateSha256": "a" * 64,
                "FileCount": 123,
            },
        }}], True),
        ([
            {"SourceProvenance": {"SourceContent": {
                "AggregateSha256": "b" * 64,
                "FileCount": 999,
            }}},
            {"SourceProvenance": {"SourceContent": {
                "AggregateSha256": "a" * 64,
                "FileCount": 123,
            }}},
        ], True),
        (["invalid-older-observation", {"SourceProvenance": {
            "SourceContent": {
                "AggregateSha256": "b" * 64,
                "FileCount": 123,
            },
        }}], False),
        (["invalid-older-observation", {"SourceProvenance": {
            "SourceContent": {
                "AggregateSha256": "a" * 64,
                "FileCount": 124,
            },
        }}], False),
    ),
)
def test_source_content_stability_uses_latest_provenance_observation(
    tmp_path: Path,
    ProvenanceChecks: list[object],
    ExpectedStable: bool | None,
) -> None:
    """Malformed latest evidence cannot be replaced by an older observation."""
    ArchiveRoot = tmp_path / "latest-source-observation"
    _WriteEvidence(ArchiveRoot)
    Context = _ArchiveContext(ArchiveRoot, ArchiveRoot)
    Manifest = _Manifest()
    Manifest["ProvenanceChecks"] = ProvenanceChecks

    PublishBenchmarkArchive(
        Context,
        Manifest,
        CompletedAtUtc="2026-09-03T12:00:13+00:00",
        WallSeconds=13.0,
        ExitCode=1,
        ExitClassification="benchmark-failed",
        SourceIdentityReader=lambda _Root: Context.Identity.Source,
    )

    ArchiveManifest = json.loads(
        (ArchiveRoot / ArchiveManifestName).read_text(encoding="utf-8")
    )
    assert (
        ArchiveManifest["Source"]["SourceContent"]["Stable"]
        is ExpectedStable
    )


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
