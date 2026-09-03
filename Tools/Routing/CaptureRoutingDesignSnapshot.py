#!/usr/bin/env python3
"""Capture a reproducible routing-design source and evidence snapshot.

The capture is intentionally read-only until final publication into a fresh
timestamped directory. It records exact Git/source identities, summarizes one
explicit CLA4 routing-failure artifact without reclassifying structural
failure as timeout, copies only explicitly named evidence, and emits stable
JSON, Markdown, and SHA-256 manifests.
"""

from __future__ import annotations

import argparse
import ast
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import importlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
from typing import Iterable, Sequence
from zoneinfo import ZoneInfo


RepositoryRoot = Path(__file__).resolve().parents[2]
GeneratorPath = Path(__file__).resolve()
if str(RepositoryRoot) not in sys.path:
    sys.path.insert(0, str(RepositoryRoot))
SchemaVersion = "routing-design-snapshot-v2"
SourceScopeVersion = "routing-implementation-source-v1"
RuntimeProvenanceVersion = "routing-runtime-provenance-v1"
AcceptanceManifestSchemaVersion = "router-acceptance-manifest-v2"
DefaultOutputRoot = (
    RepositoryRoot
    / "Output/DesignSnapshots/RoutingAwarePlacementAccess"
)
LocalTimeZone = ZoneInfo("America/New_York")
ExpectedAcceptanceCases = (
    {
        "Name": "FullAdder",
        "ExamplePath": "Examples/FullAdder.sv",
        "TopModule": "FullAdder",
        "RequiredRuns": 5,
        "TruthTableRows": 8,
        "RuntimeCeilingSeconds": 10.0,
        "RoutingDeadlineSeconds": 8.0,
        "NeedsExactInterfaceProof": False,
    },
    {
        "Name": "RippleCarryAdder4",
        "ExamplePath": "Examples/RippleCarryAdder4.sv",
        "TopModule": "RippleCarryAdder4",
        "RequiredRuns": 3,
        "TruthTableRows": 512,
        "RuntimeCeilingSeconds": 25.0,
        "RoutingDeadlineSeconds": 23.0,
        "NeedsExactInterfaceProof": False,
    },
    {
        "Name": "RippleCarryAdder8",
        "ExamplePath": "Examples/RippleCarryAdder8.sv",
        "TopModule": "RippleCarryAdder8",
        "RequiredRuns": 3,
        "TruthTableRows": 131_072,
        "RuntimeCeilingSeconds": 30.0,
        "RoutingDeadlineSeconds": 28.0,
        "NeedsExactInterfaceProof": False,
    },
    {
        "Name": "CarryLookaheadAdder4",
        "ExamplePath": "Examples/CarryLookaheadAdder4.sv",
        "TopModule": "CarryLookaheadAdder4",
        "RequiredRuns": 2,
        "TruthTableRows": 512,
        "RuntimeCeilingSeconds": 120.0,
        "RoutingDeadlineSeconds": 118.0,
        "NeedsExactInterfaceProof": True,
    },
)
RoutingEnvironmentNames = (
    "PYTHONHASHSEED",
    "RAYON_NUM_THREADS",
    "OMP_NUM_THREADS",
    "RUST_BACKTRACE",
    "RUST_LOG",
)


@dataclass(frozen=True)
class SnapshotConfiguration:
    """Immutable inputs for one fresh, timestamped evidence capture."""

    RepositoryRoot: Path
    OutputRoot: Path
    CapturedAtUtc: datetime
    Cla4FailurePath: Path
    AcceptanceManifestPath: Path | None = None
    ArtifactPaths: tuple[Path, ...] = ()


def CanonicalJsonBytes(Value: object) -> bytes:
    """Encode portable evidence with sorted keys and fixed separators."""
    return json.dumps(
        Value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def PrettyJsonText(Value: object) -> str:
    """Encode one stable human-readable JSON document."""
    return json.dumps(
        Value,
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    ) + "\n"


def Sha256Bytes(Value: bytes) -> str:
    """Return the hexadecimal SHA-256 digest of exact bytes."""
    return sha256(Value).hexdigest()


def Sha256File(InputPath: Path) -> str:
    """Return a streaming SHA-256 digest without changing the input file."""
    Digest = sha256()
    with InputPath.open("rb") as InputFile:
        for Chunk in iter(lambda: InputFile.read(1024 * 1024), b""):
            Digest.update(Chunk)
    return Digest.hexdigest()


def RunGit(
    Root: Path,
    Arguments: Sequence[str],
) -> bytes:
    """Run one read-only Git query and return exact stdout bytes."""
    Result = subprocess.run(
        ("git", *Arguments),
        cwd=Root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return Result.stdout


def RelativeDisplayPath(PathValue: Path, Root: Path) -> str:
    """Return a repository-relative path when possible, else the basename."""
    try:
        return PathValue.resolve().relative_to(Root.resolve()).as_posix()
    except ValueError:
        return PathValue.name


def BuildSnapshotFileRecord(
    PathValue: Path,
    DisplayPath: str,
) -> dict[str, object]:
    """Return existence, byte size, and SHA-256 without mutating a file."""
    if not PathValue.exists():
        return {
            "Path": DisplayPath,
            "Exists": False,
        }
    if PathValue.is_symlink():
        LinkTarget = os.readlink(PathValue)
        LinkBytes = LinkTarget.encode("utf-8")
        return {
            "Path": DisplayPath,
            "Exists": True,
            "IsSymlink": True,
            "LinkTarget": LinkTarget,
            "SizeBytes": len(LinkBytes),
            "Sha256": Sha256Bytes(LinkBytes),
        }
    if not PathValue.is_file():
        return {
            "Path": DisplayPath,
            "Exists": True,
            "IsRegularFile": False,
        }
    return {
        "Path": DisplayPath,
        "Exists": True,
        "IsRegularFile": True,
        "SizeBytes": PathValue.stat().st_size,
        "Sha256": Sha256File(PathValue),
    }


def ParsePorcelainV1Z(StatusBytes: bytes) -> list[dict[str, object]]:
    """Parse `git status --porcelain=v1 -z` including rename origins."""
    Fields = StatusBytes.split(b"\0")
    Entries: list[dict[str, object]] = []
    FieldIndex = 0
    while FieldIndex < len(Fields):
        Field = Fields[FieldIndex]
        FieldIndex += 1
        if not Field:
            continue
        if len(Field) < 4 or Field[2:3] != b" ":
            raise ValueError(f"invalid porcelain-v1-z entry: {Field!r}")
        IndexStatus = chr(Field[0])
        WorktreeStatus = chr(Field[1])
        PathText = Field[3:].decode("utf-8", errors="surrogateescape")
        Entry: dict[str, object] = {
            "IndexStatus": IndexStatus,
            "WorktreeStatus": WorktreeStatus,
            "Path": PathText,
        }
        if IndexStatus in {"R", "C"} or WorktreeStatus in {"R", "C"}:
            if FieldIndex >= len(Fields) or not Fields[FieldIndex]:
                raise ValueError("rename/copy status is missing its origin")
            Entry["OriginalPath"] = Fields[FieldIndex].decode(
                "utf-8",
                errors="surrogateescape",
            )
            FieldIndex += 1
        Entries.append(Entry)
    return Entries


def ReadDetailedGitState(Root: Path) -> dict[str, object]:
    """Capture revision, branch, patches, and untracked state read-only."""
    Revision = RunGit(Root, ("rev-parse", "HEAD")).decode().strip()
    Branch = RunGit(Root, ("branch", "--show-current")).decode().strip()
    StatusBytes = RunGit(
        Root,
        ("status", "--porcelain=v1", "-z", "--untracked-files=all"),
    )
    Entries = ParsePorcelainV1Z(StatusBytes)
    StagedPatch = RunGit(
        Root,
        (
            "diff",
            "--cached",
            "--binary",
            "--no-ext-diff",
            "--no-textconv",
        ),
    )
    UnstagedPatch = RunGit(
        Root,
        ("diff", "--binary", "--no-ext-diff", "--no-textconv"),
    )

    UntrackedFiles: list[dict[str, object]] = []
    for Entry in Entries:
        if (
            Entry["IndexStatus"] != "?"
            or Entry["WorktreeStatus"] != "?"
        ):
            continue
        RelativePath = Path(str(Entry["Path"]))
        UntrackedFiles.append(BuildSnapshotFileRecord(
            Root / RelativePath,
            RelativePath.as_posix(),
        ))
    UntrackedFiles.sort(key=lambda Value: str(Value["Path"]))
    UntrackedAggregate = Sha256Bytes(CanonicalJsonBytes(UntrackedFiles))

    return {
        "Revision": Revision,
        "Branch": Branch,
        "Dirty": bool(Entries),
        "StatusEntries": Entries,
        "StatusPorcelainBytes": len(StatusBytes),
        "StatusPorcelainSha256": Sha256Bytes(StatusBytes),
        "StagedPatchBytes": len(StagedPatch),
        "StagedPatchSha256": Sha256Bytes(StagedPatch),
        "UnstagedPatchBytes": len(UnstagedPatch),
        "UnstagedPatchSha256": Sha256Bytes(UnstagedPatch),
        "UntrackedTree": {
            "FileCount": len(UntrackedFiles),
            "AggregateSha256": UntrackedAggregate,
            "Files": UntrackedFiles,
        },
    }


def IsRoutingImplementationSource(RelativePath: str) -> bool:
    """Return whether a path belongs to the explicit implementation scope."""
    if RelativePath == "Main.py":
        return True
    if RelativePath.endswith(".py") and RelativePath.startswith((
        "Compiler/",
        "Compiler/Frontend/",
        "SchemEncoder/",
        "RedstoneCompiler/",
    )):
        return True
    return (
        RelativePath.endswith(".rs")
        and RelativePath.startswith("RustRouting/Src/")
    )


def IterPythonDefinitions(
    SourceText: str,
    RelativePath: str,
) -> Iterable[dict[str, object]]:
    """Yield deterministic Python AST definition spans with qualified names."""
    RootNode = ast.parse(SourceText, filename=RelativePath)

    def VisitBody(
        Body: Sequence[ast.stmt],
        Prefix: tuple[str, ...],
    ) -> Iterable[dict[str, object]]:
        for Node in Body:
            if isinstance(Node, ast.ClassDef):
                QualifiedName = ".".join((*Prefix, Node.name))
                EndLine = int(Node.end_lineno or Node.lineno)
                yield {
                    "Path": RelativePath,
                    "Kind": "Class",
                    "QualifiedName": QualifiedName,
                    "Line": Node.lineno,
                    "EndLine": EndLine,
                    "PythonAstSpanLines": EndLine - Node.lineno + 1,
                }
                yield from VisitBody(Node.body, (*Prefix, Node.name))
            elif isinstance(Node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                QualifiedName = ".".join((*Prefix, Node.name))
                EndLine = int(Node.end_lineno or Node.lineno)
                yield {
                    "Path": RelativePath,
                    "Kind": (
                        "AsyncFunction"
                        if isinstance(Node, ast.AsyncFunctionDef)
                        else "Function"
                    ),
                    "QualifiedName": QualifiedName,
                    "Line": Node.lineno,
                    "EndLine": EndLine,
                    "PythonAstSpanLines": EndLine - Node.lineno + 1,
                }
                yield from VisitBody(Node.body, (*Prefix, Node.name))

    yield from VisitBody(RootNode.body, ())


def BuildRoutingSourceManifest(Root: Path) -> dict[str, object]:
    """Hash and measure the explicitly versioned implementation-source scope."""
    InventoryBytes = RunGit(
        Root,
        ("ls-files", "--cached", "--others", "--exclude-standard", "-z"),
    )
    Inventory = sorted({
        Value.decode("utf-8", errors="surrogateescape")
        for Value in InventoryBytes.split(b"\0")
        if Value
    })
    DeletedInventoryBytes = RunGit(
        Root,
        ("ls-files", "--deleted", "-z"),
    )
    DeletedPaths = frozenset(
        Value.decode("utf-8", errors="surrogateescape")
        for Value in DeletedInventoryBytes.split(b"\0")
        if Value
    )
    ResolvedRoot = Root.resolve()
    SourcePaths: list[str] = []
    for RelativePath in Inventory:
        if not IsRoutingImplementationSource(RelativePath):
            continue
        AbsolutePath = Root / RelativePath
        CurrentPath = Root
        for Part in Path(RelativePath).parts:
            CurrentPath = CurrentPath / Part
            if CurrentPath.is_symlink():
                raise ValueError(
                    "implementation-source symlink is not allowed: "
                    f"{RelativePath}"
                )
        if not AbsolutePath.exists():
            # A clean-break refactor can legitimately remove tracked source
            # before the audited commit is created.  Git's deleted set is
            # already captured in checkout provenance, while the source
            # manifest describes only implementation bytes that still exist.
            if RelativePath in DeletedPaths:
                continue
            raise FileNotFoundError(
                f"implementation source is missing: {RelativePath}"
            )
        if not AbsolutePath.is_file():
            raise ValueError(
                f"implementation source is not a regular file: {RelativePath}"
            )
        if not AbsolutePath.resolve().is_relative_to(ResolvedRoot):
            raise ValueError(
                f"implementation source escapes repository: {RelativePath}"
            )
        SourcePaths.append(RelativePath)

    FileRecords: list[dict[str, object]] = []
    Definitions: list[dict[str, object]] = []
    ByLanguage: dict[str, dict[str, int]] = {}
    for RelativePath in SourcePaths:
        AbsolutePath = Root / RelativePath
        SourceBytes = AbsolutePath.read_bytes()
        SourceText = SourceBytes.decode("utf-8")
        Lines = SourceText.splitlines()
        Language = "Python" if RelativePath.endswith(".py") else "Rust"
        Record = {
            "Path": RelativePath,
            "Language": Language,
            "SizeBytes": len(SourceBytes),
            "Sha256": Sha256Bytes(SourceBytes),
            "PhysicalLines": len(Lines),
            "NonBlankLines": sum(1 for Line in Lines if Line.strip()),
        }
        FileRecords.append(Record)
        LanguageTotals = ByLanguage.setdefault(Language, {
            "FileCount": 0,
            "SizeBytes": 0,
            "PhysicalLines": 0,
            "NonBlankLines": 0,
        })
        for Key in (
            "FileCount",
            "SizeBytes",
            "PhysicalLines",
            "NonBlankLines",
        ):
            LanguageTotals[Key] += (
                1 if Key == "FileCount" else int(Record[Key])
            )
        if Language == "Python":
            Definitions.extend(IterPythonDefinitions(
                SourceText,
                RelativePath,
            ))

    Totals = {
        "FileCount": len(FileRecords),
        "SizeBytes": sum(int(Value["SizeBytes"]) for Value in FileRecords),
        "PhysicalLines": sum(
            int(Value["PhysicalLines"]) for Value in FileRecords
        ),
        "NonBlankLines": sum(
            int(Value["NonBlankLines"]) for Value in FileRecords
        ),
        "PythonDefinitionCount": len(Definitions),
    }
    LargestFiles = sorted(
        FileRecords,
        key=lambda Value: (
            -int(Value["PhysicalLines"]),
            str(Value["Path"]),
        ),
    )[:20]
    LargestDefinitions = sorted(
        Definitions,
        key=lambda Value: (
            -int(Value["PythonAstSpanLines"]),
            str(Value["Path"]),
            int(Value["Line"]),
        ),
    )[:30]
    ContentIdentity = [
        {
            "Path": Value["Path"],
            "SizeBytes": Value["SizeBytes"],
            "Sha256": Value["Sha256"],
        }
        for Value in FileRecords
    ]
    return {
        "ScopeVersion": SourceScopeVersion,
        "AggregateSha256": Sha256Bytes(CanonicalJsonBytes(ContentIdentity)),
        "FileCount": len(FileRecords),
        "Files": FileRecords,
        "Metrics": {
            "Definitions": {
                "PhysicalLines": "UTF-8 splitlines count",
                "NonBlankLines": "lines whose stripped value is nonempty",
                "PythonAstSpanLines": "AST end_lineno - lineno + 1",
            },
            "Totals": Totals,
            "ByLanguage": ByLanguage,
            "LargestFiles": LargestFiles,
            "LargestPythonDefinitions": LargestDefinitions,
        },
    }


def BuildRequiredRepositoryFileRecord(
    Root: Path,
    RelativePath: str,
) -> dict[str, object]:
    """Hash one required regular repository file without following symlinks."""
    PathValue = Root / RelativePath
    if PathValue.is_symlink():
        raise ValueError(f"required provenance file is a symlink: {RelativePath}")
    if not PathValue.is_file():
        raise FileNotFoundError(
            f"required provenance file is missing: {RelativePath}"
        )
    if not PathValue.resolve().is_relative_to(Root.resolve()):
        raise ValueError(
            f"required provenance file escapes repository: {RelativePath}"
        )
    return {
        "Path": RelativePath,
        "Exists": True,
        "IsRegularFile": True,
        "SizeBytes": PathValue.stat().st_size,
        "Sha256": Sha256File(PathValue),
    }


def BuildFileRecordSet(
    Records: Sequence[dict[str, object]],
) -> dict[str, object]:
    """Return a sorted file-record collection with one aggregate identity."""
    SortedRecords = sorted(Records, key=lambda Value: str(Value["Path"]))
    Identity = [
        {
            "Path": Value["Path"],
            "SizeBytes": Value["SizeBytes"],
            "Sha256": Value["Sha256"],
        }
        for Value in SortedRecords
    ]
    return {
        "FileCount": len(SortedRecords),
        "AggregateSha256": Sha256Bytes(CanonicalJsonBytes(Identity)),
        "Files": SortedRecords,
    }


def BuildCurrentRuntimeProvenance(Root: Path) -> dict[str, object]:
    """Capture current interpreter, inputs, templates, build files, and native code."""
    BenchmarkRecords = [
        BuildRequiredRepositoryFileRecord(Root, str(Case["ExamplePath"]))
        for Case in ExpectedAcceptanceCases
    ]
    TemplateInventory = RunGit(
        Root,
        ("ls-files", "-z", "--", "Assets/Templates"),
    )
    TemplatePaths = sorted(
        Value.decode("utf-8", errors="surrogateescape")
        for Value in TemplateInventory.split(b"\0")
        if Value
    )
    if not TemplatePaths:
        raise ValueError("no tracked template files were found")
    TemplateRecords = [
        BuildRequiredRepositoryFileRecord(Root, RelativePath)
        for RelativePath in TemplatePaths
    ]
    BuildRecords = [
        BuildRequiredRepositoryFileRecord(Root, RelativePath)
        for RelativePath in (
            "RustRouting/Cargo.toml",
            "RustRouting/Cargo.lock",
            "Tools/Routing/RunRouterAcceptance.py",
            "pyproject.toml",
            "Assets/Templates/__init__.py",
        )
    ]

    NativeModule = importlib.import_module("RedstoneCompiler.RustRouting")
    RawNativePath = getattr(NativeModule, "__file__", None)
    if not isinstance(RawNativePath, str) or not RawNativePath:
        raise ValueError("loaded native extension has no __file__")
    NativePath = Path(RawNativePath)
    if NativePath.is_symlink():
        raise ValueError("loaded native extension is a symlink")
    ResolvedNativePath = NativePath.resolve()
    if not ResolvedNativePath.is_file():
        raise FileNotFoundError(
            f"loaded native extension is missing: {ResolvedNativePath}"
        )
    if not ResolvedNativePath.is_relative_to(Root.resolve()):
        raise ValueError(
            "loaded native extension is outside the snapshot repository: "
            f"{ResolvedNativePath}"
        )
    NativeRelativePath = ResolvedNativePath.relative_to(
        Root.resolve()
    ).as_posix()
    NativeRecord = BuildRequiredRepositoryFileRecord(
        Root,
        NativeRelativePath,
    )
    NativeRecord.update({
        "Loaded": True,
        "Module": "RedstoneCompiler.RustRouting",
    })
    PolicyModule = importlib.import_module("Compiler.Routing.Policy")
    RoutingStrategy = getattr(PolicyModule, "RoutingStrategy")
    PolicyForRoutingStrategy = getattr(
        PolicyModule,
        "PolicyForRoutingStrategy",
    )
    Policy = PolicyForRoutingStrategy(RoutingStrategy.Default)
    PolicySnapshot = Policy.ToDictionary()
    PolicyRecord = {
        "PolicyVersion": Policy.PolicyVersion,
        "Seed": Policy.Seed,
        "Sha256": Sha256Bytes(CanonicalJsonBytes(PolicySnapshot)),
        "Snapshot": PolicySnapshot,
    }

    RoutingEnvironment = {
        Name: Value
        for Name, Value in sorted(os.environ.items())
        if (
            Name in RoutingEnvironmentNames
            or Name.startswith("RC_")
            or Name.startswith("RCS_")
        )
    }
    return {
        "SchemaVersion": RuntimeProvenanceVersion,
        "Python": {
            "Version": platform.python_version(),
            "Implementation": platform.python_implementation(),
            "Executable": sys.executable,
            "ResolvedExecutable": str(Path(sys.executable).resolve()),
            "Prefix": sys.prefix,
            "BasePrefix": sys.base_prefix,
        },
        "Platform": {
            "Description": platform.platform(),
            "System": platform.system(),
            "Release": platform.release(),
            "Machine": platform.machine(),
        },
        "RoutingEnvironment": RoutingEnvironment,
        "BenchmarkInputs": BuildFileRecordSet(BenchmarkRecords),
        "TrackedTemplates": BuildFileRecordSet(TemplateRecords),
        "BuildInputs": BuildFileRecordSet(BuildRecords),
        "LoadedNativeExtension": NativeRecord,
        "DefaultRoutingPolicy": PolicyRecord,
    }


def SummarizeCla4Failure(FailurePath: Path) -> dict[str, object]:
    """Extract typed CLA4 placement evidence without timeout reclassification."""
    Payload = json.loads(FailurePath.read_text(encoding="utf-8"))
    if not isinstance(Payload, dict):
        raise ValueError("CLA4 failure artifact must contain a JSON object")
    if Payload.get("SchemaVersion") != "routing-failure-v1":
        raise ValueError(
            "CLA4 failure must use routing-failure-v1, got "
            f"{Payload.get('SchemaVersion')!r}"
        )
    Reproduction = dict(Payload.get("Reproduction", {}))
    if Reproduction.get("TopModule") != "CarryLookaheadAdder4":
        raise ValueError(
            "failure artifact is not CarryLookaheadAdder4: "
            f"{Reproduction.get('TopModule')!r}"
        )
    OutputIdentity = Payload.get("OutputIdentity")
    if not isinstance(OutputIdentity, dict):
        OutputIdentity = Reproduction.get("Output")
    if not isinstance(OutputIdentity, dict):
        raise ValueError("CLA4 failure has no output identity object")
    OutputStem = OutputIdentity.get("Stem")
    OutputName = OutputIdentity.get("Name")
    OutputFormat = OutputIdentity.get("Format")
    if not isinstance(OutputStem, str) or not OutputStem:
        raise ValueError("CLA4 failure output identity has no stem")
    if not isinstance(OutputName, str) or not OutputName:
        raise ValueError("CLA4 failure output identity has no name")
    if Path(OutputName).name != OutputName:
        raise ValueError("CLA4 failure output name must be a basename")
    ExpectedFailureName = f"{OutputStem}.RoutingFailure.json"
    if FailurePath.name != ExpectedFailureName:
        raise ValueError(
            "CLA4 failure filename does not match output identity: "
            f"{FailurePath.name!r} != {ExpectedFailureName!r}"
        )
    ReproductionOutput = Reproduction.get("Output")
    if isinstance(ReproductionOutput, dict):
        for Key in ("Stem", "Name", "Format"):
            if (
                ReproductionOutput.get(Key) is not None
                and ReproductionOutput.get(Key) != OutputIdentity.get(Key)
            ):
                raise ValueError(
                    "CLA4 failure output identities disagree for "
                    f"{Key}"
                )
    SuccessArtifactPaths = (
        FailurePath.with_name(OutputName),
        FailurePath.with_name(f"{OutputStem}.PhysicalDesign.json"),
        FailurePath.with_name(f"{OutputStem}.TruthTable.txt"),
    )
    ExistingSuccessArtifacts = [
        PathValue.name
        for PathValue in SuccessArtifactPaths
        if PathValue.exists()
    ]
    if ExistingSuccessArtifacts:
        raise ValueError(
            "mixed/stale CLA4 evidence: failure artifact coexists with "
            "success artifacts: "
            + ", ".join(ExistingSuccessArtifacts)
        )
    Failure = dict(Payload.get("Failure", {}))
    Diagnostics = dict(Failure.get("Diagnostics", {}))
    Deadline = dict(
        Diagnostics.get("Deadline")
        or Payload.get("Deadline")
        or {}
    )
    Reason = str(Failure.get("Reason", ""))
    TimedOut = bool(Deadline.get("Expired", False)) or Reason in {
        "RuntimeBudgetExceeded",
        "Timeout",
    }

    CandidateSummary: list[dict[str, object]] = []
    for Decision in Diagnostics.get("PlacementGenerationDecisions", ()):
        if not isinstance(Decision, dict):
            continue
        Profile = Decision.get("MandatoryAccessProfile")
        if not isinstance(Profile, dict):
            continue
        CandidateSummary.append({
            "SourceGenerator": Decision.get("SourceGenerator"),
            "Result": Decision.get("Result"),
            "ElapsedSeconds": Decision.get("ElapsedSeconds"),
            "RoutingSpacing": Decision.get("RoutingSpacing"),
            "JointPlacementCandidateIndex": Decision.get(
                "JointPlacementCandidateIndex"
            ),
            "SignalCount": Profile.get("SignalCount"),
            "ClaimCount": Profile.get("ClaimCount"),
            "ExactConflictCount": Profile.get("ExactConflictCount"),
            "ConflictResourceCount": Profile.get("ConflictResourceCount"),
            "ConflictSignals": Profile.get("ConflictSignals", []),
            "CrossConflicts": Profile.get("CrossConflicts", []),
            "SelfConflicts": Profile.get("SelfConflicts", []),
            "OwnershipFingerprint": Profile.get("OwnershipFingerprint"),
            "ConflictFingerprint": Profile.get("ConflictFingerprint"),
        })

    NativeWork = dict(Payload.get("NativeWork", {}))
    RequestCounts = dict(NativeWork.get("RequestCounts", {}))
    return {
        "EvidenceKind": "DIAGNOSTIC_FAILURE",
        "ArtifactSha256": Sha256File(FailurePath),
        "ArtifactSourceState": Payload.get("SourceState", {}),
        "Stage": Failure.get("Stage"),
        "Reason": Reason,
        "Detail": Failure.get("Detail"),
        "RuntimeSeconds": Payload.get("RuntimeSeconds"),
        "Deadline": Deadline,
        "TimedOut": TimedOut,
        "CandidateSummary": CandidateSummary,
        "NativeRequestCounts": RequestCounts,
        "DetailedRoutingStarted": any(
            int(Value or 0) > 0 for Value in RequestCounts.values()
        ),
        "SuccessArtifactsPublished": bool(ExistingSuccessArtifacts),
        "SuccessArtifactAbsence": {
            "Verified": True,
            "CheckedNames": [
                PathValue.name for PathValue in SuccessArtifactPaths
            ],
        },
        "OutputIdentity": {
            "Stem": OutputStem,
            "Name": OutputName,
            "Format": OutputFormat,
        },
        "PolicyVersion": dict(Payload.get("Policy", {})).get(
            "PolicyVersion"
        ),
        "TechnologyVersion": dict(Payload.get("Technology", {})).get(
            "TechnologyVersion"
        ),
        "Reproduction": {
            "TopModule": Reproduction.get("TopModule"),
            "RequestedStrategy": Reproduction.get("RequestedStrategy"),
            "Input": Reproduction.get("Input"),
            "Command": Reproduction.get("Command"),
        },
    }


def SummarizeNandDiagram(ArtifactPaths: Sequence[Path]) -> dict[str, object] | None:
    """Summarize the explicitly supplied CLA4 NAND JSON when present."""
    DiagramPaths = [
        PathValue
        for PathValue in ArtifactPaths
        if PathValue.name.endswith(".Nand.json")
    ]
    if not DiagramPaths:
        return None
    if len(DiagramPaths) != 1:
        raise ValueError(
            "exactly one explicit CLA4 .Nand.json artifact is allowed"
        )
    DiagramPath = DiagramPaths[0]
    Payload = json.loads(DiagramPath.read_text(encoding="utf-8"))
    if not isinstance(Payload, dict):
        raise ValueError("CLA4 NAND diagram root must be a JSON object")
    if Payload.get("Module") != "CarryLookaheadAdder4":
        raise ValueError(
            "NAND diagram is not CarryLookaheadAdder4: "
            f"{Payload.get('Module')!r}"
        )
    for Field in ("Inputs", "Outputs", "Gates"):
        if not isinstance(Payload.get(Field), list):
            raise ValueError(f"CLA4 NAND diagram {Field} must be a list")
    Inputs = list(Payload["Inputs"])
    Outputs = list(Payload["Outputs"])
    Gates = list(Payload["Gates"])
    if not all(isinstance(Value, str) for Value in (*Inputs, *Outputs)):
        raise ValueError("CLA4 NAND diagram root ports must be strings")
    GateCounts: dict[str, int] = {}
    for GateIndex, Gate in enumerate(Gates):
        if not isinstance(Gate, dict):
            raise ValueError(
                f"CLA4 NAND gate {GateIndex} must be an object"
            )
        if not isinstance(Gate.get("Name"), str) or not Gate.get("Name"):
            raise ValueError(
                f"CLA4 NAND gate {GateIndex} has no string Name"
            )
        if not isinstance(Gate.get("Kind"), str) or not Gate.get("Kind"):
            raise ValueError(
                f"CLA4 NAND gate {GateIndex} has no string Kind"
            )
        if not isinstance(Gate.get("Inputs"), list):
            raise ValueError(
                f"CLA4 NAND gate {GateIndex} Inputs must be a list"
            )
        if not isinstance(Gate.get("Outputs"), list):
            raise ValueError(
                f"CLA4 NAND gate {GateIndex} Outputs must be a list"
            )
        Kind = str(Gate["Kind"])
        GateCounts[Kind] = GateCounts.get(Kind, 0) + 1
    return {
        "ArtifactSha256": Sha256File(DiagramPath),
        "Module": Payload.get("Module"),
        "InputCount": len(Inputs),
        "OutputCount": len(Outputs),
        "GateCount": len(Gates),
        "GateCountsByKind": dict(sorted(GateCounts.items())),
    }


def BuildFileIdentityMap(
    Records: object,
) -> dict[str, tuple[object, object]]:
    """Index well-formed file records by relative path for cross-checks."""
    if not isinstance(Records, list):
        raise ValueError("provenance file records must be a list")
    Result: dict[str, tuple[object, object]] = {}
    for Record in Records:
        if not isinstance(Record, dict):
            raise ValueError("provenance file record must be an object")
        RelativePath = Record.get("Path")
        Digest = Record.get("Sha256")
        SizeBytes = Record.get("SizeBytes")
        if (
            not isinstance(RelativePath, str)
            or not RelativePath
            or not isinstance(Digest, str)
            or len(Digest) != 64
            or not isinstance(SizeBytes, int)
        ):
            raise ValueError("provenance file record is incomplete")
        if RelativePath in Result:
            raise ValueError(
                f"duplicate provenance file path: {RelativePath}"
            )
        Result[RelativePath] = (SizeBytes, Digest)
    return Result


def SummarizeAcceptanceManifest(
    ManifestPath: Path,
    Cla4Failure: dict[str, object],
    CurrentSource: dict[str, object],
    CurrentRuntime: dict[str, object],
) -> dict[str, object]:
    """Validate and summarize one authoritative native acceptance manifest."""
    Payload = json.loads(ManifestPath.read_text(encoding="utf-8"))
    if not isinstance(Payload, dict):
        raise ValueError("acceptance manifest root must be a JSON object")
    if Payload.get("SchemaVersion") != AcceptanceManifestSchemaVersion:
        raise ValueError(
            "acceptance manifest must use "
            f"{AcceptanceManifestSchemaVersion}, got "
            f"{Payload.get('SchemaVersion')!r}"
        )
    if Payload.get("SourceProvenanceStable") is not True:
        raise ValueError("acceptance manifest source provenance is not stable")

    FailureSourceState = Cla4Failure.get("ArtifactSourceState")
    ManifestSourceState = Payload.get("SourceState")
    SourceProvenance = Payload.get("SourceProvenance")
    if not isinstance(FailureSourceState, dict):
        raise ValueError("CLA4 failure has no source-state object")
    if not isinstance(ManifestSourceState, dict):
        raise ValueError("acceptance manifest has no source-state object")
    if not isinstance(SourceProvenance, dict):
        raise ValueError("acceptance manifest has no SourceProvenance object")
    FailureRevision = FailureSourceState.get("Revision")
    ManifestRevision = ManifestSourceState.get("Revision")
    GitProvenance = SourceProvenance.get("Git")
    if not isinstance(GitProvenance, dict):
        raise ValueError("acceptance source provenance has no Git object")
    ProvenanceRevision = GitProvenance.get("Revision")
    if (
        not isinstance(FailureRevision, str)
        or not FailureRevision
        or ManifestRevision != FailureRevision
        or ProvenanceRevision != FailureRevision
    ):
        raise ValueError(
            "acceptance manifest and CLA4 failure source revisions disagree"
        )

    FailurePolicyVersion = Cla4Failure.get("PolicyVersion")
    ExpectedPolicyVersion = SourceProvenance.get("ExpectedPolicyVersion")
    PolicyRecord = SourceProvenance.get("Policy")
    if not isinstance(PolicyRecord, dict):
        raise ValueError("acceptance source provenance has no Policy object")
    ManifestPolicyVersion = PolicyRecord.get("PolicyVersion")
    if (
        not isinstance(FailurePolicyVersion, str)
        or not FailurePolicyVersion
        or ExpectedPolicyVersion != FailurePolicyVersion
        or ManifestPolicyVersion != FailurePolicyVersion
    ):
        raise ValueError(
            "acceptance manifest and CLA4 failure policy versions disagree"
        )

    Cases = Payload.get("Cases")
    if not isinstance(Cases, list):
        raise ValueError("acceptance manifest Cases must be a list")
    CasesByName: dict[str, dict[str, object]] = {}
    for Case in Cases:
        if not isinstance(Case, dict) or not isinstance(Case.get("Name"), str):
            raise ValueError("acceptance manifest case must be a named object")
        CaseName = str(Case["Name"])
        if CaseName in CasesByName:
            raise ValueError(f"duplicate acceptance case: {CaseName}")
        CasesByName[CaseName] = Case
    ExpectedNames = {str(Value["Name"]) for Value in ExpectedAcceptanceCases}
    if set(CasesByName) != ExpectedNames:
        raise ValueError("acceptance manifest case set is not authoritative")
    CaseMatrix: list[dict[str, object]] = []
    for ExpectedCase in ExpectedAcceptanceCases:
        Case = CasesByName[str(ExpectedCase["Name"])]
        for Key, ExpectedValue in ExpectedCase.items():
            if Case.get(Key) != ExpectedValue:
                raise ValueError(
                    "acceptance manifest case matrix mismatch for "
                    f"{ExpectedCase['Name']}.{Key}: "
                    f"{Case.get(Key)!r} != {ExpectedValue!r}"
                )
        CaseMatrix.append({
            Key: Case[Key]
            for Key in ExpectedCase
        })

    SourceContent = SourceProvenance.get("SourceContent")
    if not isinstance(SourceContent, dict):
        raise ValueError(
            "acceptance source provenance has no SourceContent object"
        )
    HistoricalSourceFiles = BuildFileIdentityMap(SourceContent.get("Files"))
    if not isinstance(SourceContent.get("AggregateSha256"), str):
        raise ValueError("acceptance SourceContent has no aggregate digest")
    if SourceContent.get("FileCount") != len(HistoricalSourceFiles):
        raise ValueError("acceptance SourceContent file count is inconsistent")
    CurrentSourceFiles = BuildFileIdentityMap(CurrentSource.get("Files"))
    CurrentBuildFiles = BuildFileIdentityMap(
        dict(CurrentRuntime["BuildInputs"])["Files"]
    )
    CurrentSourceCoverage = dict(CurrentSourceFiles)
    CurrentSourceCoverage.update(CurrentBuildFiles)
    CurrentSourceMatches = all(
        CurrentSourceCoverage.get(RelativePath) == Identity
        for RelativePath, Identity in HistoricalSourceFiles.items()
    )

    BenchmarkInputs = SourceProvenance.get("BenchmarkInputs")
    if not isinstance(BenchmarkInputs, dict):
        raise ValueError(
            "acceptance source provenance has no BenchmarkInputs object"
        )
    CurrentBenchmarkFiles = BuildFileIdentityMap(
        dict(CurrentRuntime["BenchmarkInputs"])["Files"]
    )
    BenchmarkSummary: dict[str, dict[str, object]] = {}
    BenchmarkInputsMatchCurrent = True
    for ExpectedCase in ExpectedAcceptanceCases:
        CaseName = str(ExpectedCase["Name"])
        Record = BenchmarkInputs.get(CaseName)
        if not isinstance(Record, dict):
            raise ValueError(
                f"acceptance benchmark input is missing: {CaseName}"
            )
        RelativePath = ExpectedCase["ExamplePath"]
        if (
            Record.get("Exists") is not True
            or Record.get("Path") != RelativePath
            or not isinstance(Record.get("Sha256"), str)
            or not isinstance(Record.get("SizeBytes"), int)
        ):
            raise ValueError(
                f"acceptance benchmark input is incomplete: {CaseName}"
            )
        Identity = (Record["SizeBytes"], Record["Sha256"])
        BenchmarkInputsMatchCurrent &= (
            CurrentBenchmarkFiles.get(str(RelativePath)) == Identity
        )
        BenchmarkSummary[CaseName] = {
            "Path": RelativePath,
            "SizeBytes": Record["SizeBytes"],
            "Sha256": Record["Sha256"],
        }
    FailureInput = dict(
        dict(Cla4Failure.get("Reproduction", {})).get("Input", {})
    )
    Cla4Benchmark = BenchmarkSummary["CarryLookaheadAdder4"]
    if FailureInput.get("Sha256") != Cla4Benchmark["Sha256"]:
        raise ValueError(
            "acceptance CLA4 input does not match failure reproduction input"
        )

    PhysicalTemplates = SourceProvenance.get("PhysicalTemplates")
    if not isinstance(PhysicalTemplates, dict):
        raise ValueError(
            "acceptance source provenance has no PhysicalTemplates object"
        )
    TemplateRecords = PhysicalTemplates.get("Templates")
    if not isinstance(TemplateRecords, dict):
        raise ValueError("acceptance PhysicalTemplates has no Templates object")
    CurrentTemplateFiles = BuildFileIdentityMap(
        dict(CurrentRuntime["TrackedTemplates"])["Files"]
    )
    TemplateSummary: dict[str, dict[str, object]] = {}
    PhysicalTemplatesMatchCurrent = True
    for TemplateName in ("Input", "Nand", "Output"):
        Record = TemplateRecords.get(TemplateName)
        if not isinstance(Record, dict):
            raise ValueError(
                f"acceptance physical template is missing: {TemplateName}"
            )
        RelativePath = Record.get("Path")
        if (
            Record.get("Exists") is not True
            or not isinstance(RelativePath, str)
            or not isinstance(Record.get("Sha256"), str)
            or not isinstance(Record.get("SizeBytes"), int)
        ):
            raise ValueError(
                f"acceptance physical template is incomplete: {TemplateName}"
            )
        Identity = (Record["SizeBytes"], Record["Sha256"])
        PhysicalTemplatesMatchCurrent &= (
            CurrentTemplateFiles.get(RelativePath) == Identity
        )
        TemplateSummary[TemplateName] = {
            "Path": RelativePath,
            "SizeBytes": Record["SizeBytes"],
            "Sha256": Record["Sha256"],
        }

    NativeExtension = SourceProvenance.get("NativeExtension")
    if not isinstance(NativeExtension, dict):
        raise ValueError(
            "acceptance source provenance has no NativeExtension object"
        )
    if (
        NativeExtension.get("Exists") is not True
        or NativeExtension.get("Loaded") is not True
        or not isinstance(NativeExtension.get("Path"), str)
        or not isinstance(NativeExtension.get("Sha256"), str)
        or not isinstance(NativeExtension.get("SizeBytes"), int)
    ):
        raise ValueError("acceptance native extension record is incomplete")
    CurrentNative = dict(CurrentRuntime["LoadedNativeExtension"])
    NativeExtensionMatchesCurrent = (
        NativeExtension.get("Path") == CurrentNative.get("Path")
        and NativeExtension.get("SizeBytes") == CurrentNative.get("SizeBytes")
        and NativeExtension.get("Sha256") == CurrentNative.get("Sha256")
    )

    BuildInputsMatchCurrent = all(
        HistoricalSourceFiles.get(RelativePath) == Identity
        for RelativePath, Identity in CurrentBuildFiles.items()
    )
    CurrentPolicy = dict(CurrentRuntime["DefaultRoutingPolicy"])
    PolicyMatchesCurrent = (
        PolicyRecord.get("PolicyVersion")
        == CurrentPolicy.get("PolicyVersion")
        and PolicyRecord.get("Sha256") == CurrentPolicy.get("Sha256")
    )

    return {
        "EvidenceKind": "NATIVE_ACCEPTANCE_MANIFEST",
        "ArtifactSha256": Sha256File(ManifestPath),
        "SchemaVersion": Payload["SchemaVersion"],
        "Accepted": Payload.get("Accepted"),
        "Status": Payload.get("Status"),
        "ExecutionMode": Payload.get("ExecutionMode"),
        "BaselineMode": Payload.get("BaselineMode"),
        "SourceProvenanceStable": True,
        "SourceState": ManifestSourceState,
        "ExpectedPolicyVersion": ExpectedPolicyVersion,
        "PolicySha256": PolicyRecord.get("Sha256"),
        "CaseMatrix": CaseMatrix,
        "BenchmarkInputs": BenchmarkSummary,
        "SourceContent": {
            "FileCount": SourceContent.get("FileCount"),
            "AggregateSha256": SourceContent.get("AggregateSha256"),
        },
        "PhysicalTemplates": {
            "AggregateSha256": PhysicalTemplates.get("AggregateSha256"),
            "Templates": TemplateSummary,
        },
        "NativeExtension": {
            "Path": NativeExtension.get("Path"),
            "SizeBytes": NativeExtension.get("SizeBytes"),
            "Sha256": NativeExtension.get("Sha256"),
        },
        "CrossChecks": {
            "FailureRevisionMatches": True,
            "FailurePolicyMatches": True,
            "FailureInputMatches": True,
            "AuthoritativeCaseMatrixMatches": True,
            "CurrentRoutingSourceMatches": CurrentSourceMatches,
            "CurrentBenchmarkInputsMatch": BenchmarkInputsMatchCurrent,
            "CurrentPhysicalTemplatesMatch": PhysicalTemplatesMatchCurrent,
            "CurrentNativeExtensionMatches": NativeExtensionMatchesCurrent,
            "CurrentBuildInputsMatch": BuildInputsMatchCurrent,
            "CurrentDefaultPolicyMatches": PolicyMatchesCurrent,
        },
    }


def BuildArtifactManifest(
    ArtifactPaths: Sequence[Path],
) -> list[dict[str, object]]:
    """Hash explicitly selected evidence and assign collision-free copy paths."""
    Records: list[dict[str, object]] = []
    SeenSources: set[Path] = set()
    SeenNames: set[str] = set()
    for PathValue in ArtifactPaths:
        ResolvedPath = PathValue.resolve()
        if ResolvedPath in SeenSources:
            continue
        SeenSources.add(ResolvedPath)
        if not ResolvedPath.is_file():
            raise FileNotFoundError(f"explicit artifact is missing: {PathValue}")
        if ResolvedPath.name in SeenNames:
            raise ValueError(
                "explicit artifacts have a duplicate basename: "
                f"{ResolvedPath.name}"
            )
        SeenNames.add(ResolvedPath.name)
        Records.append({
            "OriginalPath": str(ResolvedPath),
            "SnapshotPath": f"Artifacts/{ResolvedPath.name}",
            "SizeBytes": ResolvedPath.stat().st_size,
            "Sha256": Sha256File(ResolvedPath),
        })
    Records.sort(key=lambda Value: str(Value["SnapshotPath"]))
    return Records


def BuildExactEvidence(Snapshot: dict[str, object]) -> dict[str, object]:
    """Build a timestamp-free identity that retains every raw artifact hash."""
    Exact = deepcopy(Snapshot)
    for Key in (
        "SnapshotId",
        "CapturedAtUtc",
        "CapturedAtLocal",
        "ExactEvidenceSha256",
        "PortableSemanticEvidenceSha256",
    ):
        Exact.pop(Key, None)
    for Artifact in Exact.get("Artifacts", []):
        Artifact.pop("OriginalPath", None)
    return Exact


def BuildPortableSemanticEvidence(
    Snapshot: dict[str, object],
) -> dict[str, object]:
    """Build path-free semantic evidence without raw evidence-file hashes."""
    Failure = deepcopy(dict(Snapshot.get("Cla4Failure", {})))
    Failure.pop("ArtifactSha256", None)
    Reproduction = dict(Failure.get("Reproduction", {}))
    Reproduction.pop("Command", None)
    InputRecord = dict(Reproduction.get("Input", {}))
    InputRecord.pop("Path", None)
    if InputRecord:
        Reproduction["Input"] = InputRecord
    Failure["Reproduction"] = Reproduction

    NandDiagram = deepcopy(Snapshot.get("NandDiagram"))
    if isinstance(NandDiagram, dict):
        NandDiagram.pop("ArtifactSha256", None)
    AcceptanceManifest = deepcopy(Snapshot.get("AcceptanceManifest"))
    if isinstance(AcceptanceManifest, dict):
        AcceptanceManifest.pop("ArtifactSha256", None)

    Runtime = deepcopy(dict(Snapshot.get("CurrentRuntimeProvenance", {})))
    PythonRecord = dict(Runtime.get("Python", {}))
    for Key in (
        "Executable",
        "ResolvedExecutable",
        "Prefix",
        "BasePrefix",
    ):
        PythonRecord.pop(Key, None)
    Runtime["Python"] = PythonRecord

    Checkout = dict(Snapshot.get("Checkout", {}))
    Generator = dict(Snapshot.get("Generator", {}))
    Generator.pop("Path", None)
    return {
        "SchemaVersion": Snapshot.get("SchemaVersion"),
        "Generator": Generator,
        "Checkout": {
            "Revision": Checkout.get("Revision"),
            "Dirty": Checkout.get("Dirty"),
        },
        "Source": deepcopy(Snapshot.get("Source")),
        "CurrentRuntimeProvenance": Runtime,
        "Cla4Failure": Failure,
        "AcceptanceManifest": AcceptanceManifest,
        "NandDiagram": NandDiagram,
    }


def BuildRoutingDesignSnapshot(
    Configuration: SnapshotConfiguration,
) -> dict[str, object]:
    """Assemble stable machine evidence and reject mixed source states."""
    CapturedAtUtc = Configuration.CapturedAtUtc.astimezone(timezone.utc).replace(
        microsecond=0
    )
    CapturedAtLocal = CapturedAtUtc.astimezone(LocalTimeZone)
    Checkout = ReadDetailedGitState(Configuration.RepositoryRoot)
    Source = BuildRoutingSourceManifest(Configuration.RepositoryRoot)
    CurrentRuntime = BuildCurrentRuntimeProvenance(
        Configuration.RepositoryRoot
    )
    Cla4Failure = SummarizeCla4Failure(Configuration.Cla4FailurePath)
    ArtifactInputs: list[Path] = [Configuration.Cla4FailurePath]
    if Configuration.AcceptanceManifestPath is not None:
        ArtifactInputs.append(Configuration.AcceptanceManifestPath)
    ArtifactInputs.extend(Configuration.ArtifactPaths)
    Artifacts = BuildArtifactManifest(ArtifactInputs)
    NandDiagram = SummarizeNandDiagram(Configuration.ArtifactPaths)
    AcceptanceManifest = None
    if Configuration.AcceptanceManifestPath is not None:
        AcceptanceManifest = SummarizeAcceptanceManifest(
            Configuration.AcceptanceManifestPath,
            Cla4Failure,
            Source,
            CurrentRuntime,
        )
    ArtifactHashes = {
        Path(str(Value["OriginalPath"])): Value["Sha256"]
        for Value in Artifacts
    }
    if ArtifactHashes.get(Configuration.Cla4FailurePath.resolve()) != (
        Cla4Failure["ArtifactSha256"]
    ):
        raise RuntimeError("CLA4 failure evidence changed during capture")
    if NandDiagram is not None:
        NandPaths = [
            PathValue.resolve()
            for PathValue in Configuration.ArtifactPaths
            if PathValue.name.endswith(".Nand.json")
        ]
        if ArtifactHashes.get(NandPaths[0]) != NandDiagram["ArtifactSha256"]:
            raise RuntimeError("NAND evidence changed during capture")
    if AcceptanceManifest is not None:
        AcceptancePath = Configuration.AcceptanceManifestPath
        assert AcceptancePath is not None
        if ArtifactHashes.get(AcceptancePath.resolve()) != (
            AcceptanceManifest["ArtifactSha256"]
        ):
            raise RuntimeError(
                "acceptance evidence changed during capture"
            )
    Cla4Failure["CurrentRevisionMatchesArtifact"] = (
        str(dict(Cla4Failure.get("ArtifactSourceState", {})).get(
            "Revision",
            "",
        ))
        == str(Checkout["Revision"])
    )
    Generator = BuildSnapshotFileRecord(
        GeneratorPath,
        RelativeDisplayPath(GeneratorPath, Configuration.RepositoryRoot),
    )
    CheckoutAfter = ReadDetailedGitState(Configuration.RepositoryRoot)
    SourceAfter = BuildRoutingSourceManifest(Configuration.RepositoryRoot)
    CurrentRuntimeAfter = BuildCurrentRuntimeProvenance(
        Configuration.RepositoryRoot
    )
    if (
        CheckoutAfter != Checkout
        or SourceAfter != Source
        or CurrentRuntimeAfter != CurrentRuntime
    ):
        raise RuntimeError("source/provenance changed during capture")

    TimestampText = CapturedAtUtc.strftime("%Y-%m-%dT%H:%M:%SZ")
    Snapshot: dict[str, object] = {
        "SchemaVersion": SchemaVersion,
        "CapturedAtUtc": TimestampText,
        "CapturedAtLocal": CapturedAtLocal.isoformat(timespec="seconds"),
        "Generator": Generator,
        "Checkout": Checkout,
        "Source": Source,
        "CurrentRuntimeProvenance": CurrentRuntime,
        "Cla4Failure": Cla4Failure,
        "AcceptanceManifest": AcceptanceManifest,
        "NandDiagram": NandDiagram,
        "Artifacts": Artifacts,
    }
    ExactDigest = Sha256Bytes(CanonicalJsonBytes(
        BuildExactEvidence(Snapshot)
    ))
    PortableSemanticDigest = Sha256Bytes(CanonicalJsonBytes(
        BuildPortableSemanticEvidence(Snapshot)
    ))
    SnapshotId = (
        CapturedAtUtc.strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + PortableSemanticDigest[:16]
    )
    Snapshot["SnapshotId"] = SnapshotId
    Snapshot["ExactEvidenceSha256"] = ExactDigest
    Snapshot["PortableSemanticEvidenceSha256"] = PortableSemanticDigest
    return Snapshot


def RenderSnapshotMarkdown(Snapshot: dict[str, object]) -> str:
    """Render a deterministic human projection of `Snapshot.json`."""
    Checkout = dict(Snapshot["Checkout"])
    Source = dict(Snapshot["Source"])
    Metrics = dict(Source["Metrics"])
    Totals = dict(Metrics["Totals"])
    Runtime = dict(Snapshot["CurrentRuntimeProvenance"])
    PythonRecord = dict(Runtime["Python"])
    NativeRecord = dict(Runtime["LoadedNativeExtension"])
    Failure = dict(Snapshot["Cla4Failure"])
    Deadline = dict(Failure.get("Deadline", {}))
    Lines = [
        "# Routing design snapshot",
        "",
        f"- Snapshot ID: `{Snapshot['SnapshotId']}`",
        f"- Captured UTC: `{Snapshot['CapturedAtUtc']}`",
        f"- Captured local: `{Snapshot['CapturedAtLocal']}`",
        f"- Exact evidence SHA-256: `{Snapshot['ExactEvidenceSha256']}`",
        "- Portable semantic evidence SHA-256: "
        f"`{Snapshot['PortableSemanticEvidenceSha256']}`",
        f"- Revision: `{Checkout['Revision']}`",
        f"- Branch: `{Checkout['Branch']}`",
        f"- Dirty: `{str(Checkout['Dirty']).lower()}`",
        f"- Status SHA-256: `{Checkout['StatusPorcelainSha256']}`",
        "",
        "## Source",
        "",
        f"- Scope: `{Source['ScopeVersion']}`",
        f"- Aggregate SHA-256: `{Source['AggregateSha256']}`",
        f"- Files: `{Totals['FileCount']}`",
        f"- Physical lines: `{Totals['PhysicalLines']}`",
        f"- Nonblank lines: `{Totals['NonBlankLines']}`",
        "",
        "### Largest Python definitions",
        "",
        "| Definition | File | AST span lines |",
        "| --- | --- | ---: |",
    ]
    for Definition in Metrics["LargestPythonDefinitions"][:10]:
        Lines.append(
            f"| `{Definition['QualifiedName']}` | "
            f"`{Definition['Path']}` | "
            f"{Definition['PythonAstSpanLines']} |"
        )
    Lines.extend([
        "",
        "## Current runtime provenance",
        "",
        f"- Python: `{PythonRecord.get('Implementation')} "
        f"{PythonRecord.get('Version')}`",
        f"- Python executable: `{PythonRecord.get('Executable')}`",
        f"- Platform: `{dict(Runtime['Platform']).get('Description')}`",
        f"- Default policy: "
        f"`{dict(Runtime['DefaultRoutingPolicy']).get('PolicyVersion')}`",
        f"- Native extension: `{NativeRecord.get('Path')}`",
        f"- Native SHA-256: `{NativeRecord.get('Sha256')}`",
        f"- Benchmark input aggregate: "
        f"`{dict(Runtime['BenchmarkInputs']).get('AggregateSha256')}`",
        f"- Tracked template aggregate: "
        f"`{dict(Runtime['TrackedTemplates']).get('AggregateSha256')}`",
        "",
    ])
    Acceptance = Snapshot.get("AcceptanceManifest")
    if isinstance(Acceptance, dict):
        CrossChecks = dict(Acceptance.get("CrossChecks", {}))
        Lines.extend([
            "## Native acceptance-manifest evidence",
            "",
            f"- Manifest status: `{Acceptance.get('Status')}`",
            f"- Accepted: `{str(Acceptance.get('Accepted')).lower()}`",
            f"- Policy: `{Acceptance.get('ExpectedPolicyVersion')}`",
            f"- Source provenance stable: "
            f"`{str(Acceptance.get('SourceProvenanceStable')).lower()}`",
            "",
            "| Cross-check | Result |",
            "| --- | --- |",
        ])
        for Name, Result in sorted(CrossChecks.items()):
            Lines.append(f"| `{Name}` | `{str(Result).lower()}` |")
    Lines.extend([
        "",
        "## CLA4 failure",
        "",
        f"- Stage: `{Failure.get('Stage')}`",
        f"- Reason: `{Failure.get('Reason')}`",
        f"- Detail: `{Failure.get('Detail')}`",
        f"- Runtime seconds: `{Failure.get('RuntimeSeconds')}`",
        f"- Timed out: `{str(Failure.get('TimedOut')).lower()}`",
        f"- Deadline expired: `{str(Deadline.get('Expired')).lower()}`",
        f"- Remaining milliseconds: `{Deadline.get('RemainingMilliseconds')}`",
        f"- Detailed routing started: "
        f"`{str(Failure.get('DetailedRoutingStarted')).lower()}`",
        "- Success-artifact absence verified: "
        f"`{str(dict(Failure.get('SuccessArtifactAbsence', {})).get('Verified')).lower()}`",
        "",
        "### Placement candidates",
        "",
        "| Generator | Elapsed s | Claims | Conflicts | Signals |",
        "| --- | ---: | ---: | ---: | --- |",
    ])
    for Candidate in Failure.get("CandidateSummary", []):
        Signals = ", ".join(Candidate.get("ConflictSignals", []))
        Lines.append(
            f"| `{Candidate.get('SourceGenerator')}` | "
            f"{Candidate.get('ElapsedSeconds')} | "
            f"{Candidate.get('ClaimCount')} | "
            f"{Candidate.get('ConflictResourceCount')} | "
            f"{Signals} |"
        )
    Lines.extend([
        "",
        "## Copied artifacts",
        "",
        "| Snapshot path | Bytes | SHA-256 |",
        "| --- | ---: | --- |",
    ])
    for Artifact in Snapshot["Artifacts"]:
        Lines.append(
            f"| [{Path(str(Artifact['SnapshotPath'])).name}]"
            f"({Artifact['SnapshotPath']}) | "
            f"{Artifact['SizeBytes']} | `{Artifact['Sha256']}` |"
        )
    Lines.extend([
        "",
        "This snapshot records a typed structural placement failure. It does "
        "not establish CLA4 routing acceptance.",
        "",
    ])
    return "\n".join(Lines)


def ValidateSnapshotIdentities(
    Configuration: SnapshotConfiguration,
    Snapshot: dict[str, object],
) -> None:
    """Reject mutation of schema, timestamps, evidence, or snapshot identity."""
    CapturedAtUtc = Configuration.CapturedAtUtc.astimezone(timezone.utc).replace(
        microsecond=0
    )
    ExpectedCapturedAtUtc = CapturedAtUtc.strftime("%Y-%m-%dT%H:%M:%SZ")
    ExpectedCapturedAtLocal = CapturedAtUtc.astimezone(LocalTimeZone).isoformat(
        timespec="seconds"
    )
    if Snapshot.get("SchemaVersion") != SchemaVersion:
        raise RuntimeError("snapshot evidence identity mismatch")
    if (
        Snapshot.get("CapturedAtUtc") != ExpectedCapturedAtUtc
        or Snapshot.get("CapturedAtLocal") != ExpectedCapturedAtLocal
    ):
        raise RuntimeError("snapshot evidence identity mismatch")
    ExactDigest = Sha256Bytes(CanonicalJsonBytes(
        BuildExactEvidence(Snapshot)
    ))
    PortableSemanticDigest = Sha256Bytes(CanonicalJsonBytes(
        BuildPortableSemanticEvidence(Snapshot)
    ))
    ExpectedSnapshotId = (
        CapturedAtUtc.strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + PortableSemanticDigest[:16]
    )
    if (
        Snapshot.get("ExactEvidenceSha256") != ExactDigest
        or Snapshot.get("PortableSemanticEvidenceSha256")
        != PortableSemanticDigest
        or Snapshot.get("SnapshotId") != ExpectedSnapshotId
    ):
        raise RuntimeError("snapshot evidence identity mismatch")


def WriteSnapshotStaged(
    Configuration: SnapshotConfiguration,
    Snapshot: dict[str, object],
) -> Path:
    """Stage a checked bundle and rename it into one fresh final path."""
    CapturedAtUtc = Configuration.CapturedAtUtc.astimezone(timezone.utc).replace(
        microsecond=0
    )
    DirectoryName = CapturedAtUtc.strftime("%Y%m%dT%H%M%SZ")
    OutputRoot = Configuration.OutputRoot.resolve()
    TargetDirectory = OutputRoot / DirectoryName
    if TargetDirectory.exists():
        raise FileExistsError(
            f"snapshot target already exists: {TargetDirectory}"
        )
    ValidateSnapshotIdentities(Configuration, Snapshot)
    CheckoutNow = ReadDetailedGitState(Configuration.RepositoryRoot)
    SourceNow = BuildRoutingSourceManifest(Configuration.RepositoryRoot)
    RuntimeNow = BuildCurrentRuntimeProvenance(
        Configuration.RepositoryRoot
    )
    if (
        CheckoutNow != Snapshot.get("Checkout")
        or SourceNow != Snapshot.get("Source")
        or RuntimeNow != Snapshot.get("CurrentRuntimeProvenance")
    ):
        raise RuntimeError("source/provenance changed during capture")
    OutputRoot.mkdir(parents=True, exist_ok=True)
    TemporaryDirectory = Path(tempfile.mkdtemp(
        prefix=f".{DirectoryName}.",
        dir=OutputRoot,
    ))
    try:
        ResolvedTemporaryDirectory = TemporaryDirectory.resolve()
        ArtifactDirectory = TemporaryDirectory / "Artifacts"
        ArtifactDirectory.mkdir()
        for Artifact in Snapshot["Artifacts"]:
            SourcePath = Path(str(Artifact["OriginalPath"]))
            RelativeSnapshotPath = Path(str(Artifact["SnapshotPath"]))
            if RelativeSnapshotPath.is_absolute():
                raise ValueError(
                    "snapshot artifact path must be relative: "
                    f"{RelativeSnapshotPath}"
                )
            DestinationPath = (
                TemporaryDirectory / RelativeSnapshotPath
            ).resolve()
            if not DestinationPath.is_relative_to(ResolvedTemporaryDirectory):
                raise ValueError(
                    "snapshot artifact path escapes temporary bundle: "
                    f"{RelativeSnapshotPath}"
                )
            DestinationPath.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SourcePath, DestinationPath)
            if Sha256File(DestinationPath) != Artifact["Sha256"]:
                raise RuntimeError(
                    f"copied artifact hash mismatch: {SourcePath}"
                )

        SnapshotJsonPath = TemporaryDirectory / "Snapshot.json"
        SnapshotMarkdownPath = TemporaryDirectory / "Snapshot.md"
        SnapshotJsonPath.write_text(
            PrettyJsonText(Snapshot),
            encoding="utf-8",
            newline="\n",
        )
        SnapshotMarkdownPath.write_text(
            RenderSnapshotMarkdown(Snapshot),
            encoding="utf-8",
            newline="\n",
        )

        HashedPaths = sorted((
            SnapshotJsonPath,
            SnapshotMarkdownPath,
            *ArtifactDirectory.iterdir(),
        ), key=lambda Value: Value.relative_to(TemporaryDirectory).as_posix())
        ChecksumLines = [
            f"{Sha256File(PathValue)}  "
            f"{PathValue.relative_to(TemporaryDirectory).as_posix()}"
            for PathValue in HashedPaths
        ]
        (TemporaryDirectory / "SHA256SUMS").write_text(
            "\n".join(ChecksumLines) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        TemporaryDirectory.replace(TargetDirectory)
    except Exception:
        shutil.rmtree(TemporaryDirectory, ignore_errors=True)
        raise
    return TargetDirectory


def ParseTimestamp(Value: str) -> datetime:
    """Parse a UTC CLI timestamp in compact or ISO filesystem-safe form."""
    for Format in ("%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H-%M-%SZ"):
        try:
            return datetime.strptime(Value, Format).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(
        "timestamp must be YYYYMMDDTHHMMSSZ or YYYY-MM-DDTHH-MM-SSZ"
    )


def ParseArguments(Arguments: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse one explicit, non-discovering snapshot request."""
    Parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  %(prog)s --cla4-failure Output/Failure.RoutingFailure.json "
            "--artifact Output/Failure.Nand.json\n\n"
            "The source artifact is read-only; output is published only to a "
            "fresh timestamped directory."
        ),
    )
    Parser.add_argument(
        "--output-root",
        type=Path,
        default=DefaultOutputRoot,
        help="fresh timestamped snapshot parent directory",
    )
    Parser.add_argument(
        "--timestamp",
        type=ParseTimestamp,
        help="fixed UTC capture timestamp for reproduction/tests",
    )
    Parser.add_argument(
        "--cla4-failure",
        type=Path,
        required=True,
        help="explicit CarryLookaheadAdder4 routing-failure JSON",
    )
    Parser.add_argument(
        "--acceptance-manifest",
        type=Path,
        help="explicit native acceptance manifest to copy and hash",
    )
    Parser.add_argument(
        "--artifact",
        type=Path,
        action="append",
        default=[],
        help="additional explicit evidence file; repeat as needed",
    )
    return Parser.parse_args(Arguments)


def GuidedArguments() -> list[str]:
    """Collect the explicit evidence inputs required for a snapshot."""
    print("RedstoneCompiler routing evidence snapshot")
    Failure = input("CLA4 routing-failure JSON path: ").strip()
    if not Failure:
        raise ValueError("a CLA4 routing-failure JSON path is required")
    OutputRoot = input(
        f"Output root [{DefaultOutputRoot.relative_to(RepositoryRoot)}]: "
    ).strip() or str(DefaultOutputRoot.relative_to(RepositoryRoot))
    Arguments = ["--cla4-failure", Failure, "--output-root", OutputRoot]
    Manifest = input("Acceptance manifest path (optional): ").strip()
    if Manifest:
        Arguments.extend(["--acceptance-manifest", Manifest])
    while True:
        Artifact = input("Additional artifact path (blank to finish): ").strip()
        if not Artifact:
            break
        Arguments.extend(["--artifact", Artifact])
    return Arguments


def Main(Arguments: Sequence[str] | None = None) -> int:
    """Capture and publish one timestamped design snapshot."""
    RawArguments = list(sys.argv[1:] if Arguments is None else Arguments)
    if not RawArguments:
        try:
            RawArguments = GuidedArguments()
        except (EOFError, KeyboardInterrupt):
            print("No snapshot source selected. Run with --help for explicit commands.")
            return 2
        except ValueError as Error:
            raise SystemExit(str(Error)) from Error
    Parsed = ParseArguments(RawArguments)
    CapturedAtUtc = Parsed.timestamp or datetime.now(timezone.utc).replace(
        microsecond=0
    )
    Configuration = SnapshotConfiguration(
        RepositoryRoot=RepositoryRoot,
        OutputRoot=Parsed.output_root,
        CapturedAtUtc=CapturedAtUtc,
        Cla4FailurePath=Parsed.cla4_failure,
        AcceptanceManifestPath=Parsed.acceptance_manifest,
        ArtifactPaths=tuple(Parsed.artifact),
    )
    Snapshot = BuildRoutingDesignSnapshot(Configuration)
    OutputDirectory = WriteSnapshotStaged(Configuration, Snapshot)
    print(OutputDirectory)
    print(f"SnapshotId={Snapshot['SnapshotId']}")
    print(
        "ExactEvidenceSha256="
        f"{Snapshot['ExactEvidenceSha256']}"
    )
    print(
        "PortableSemanticEvidenceSha256="
        f"{Snapshot['PortableSemanticEvidenceSha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(Main())
