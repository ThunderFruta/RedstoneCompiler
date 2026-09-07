"""Focused contracts for timestamped routing-design evidence snapshots."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PhysicalDesign.Policy import RoutingStrategy
from Tools.Routing.RunRouterAcceptance import (
    AcceptanceCase,
    AcceptanceCommandResult,
    AcceptanceConfiguration,
    BuildPolicyProvenanceRecord,
    BuildRunArtifacts,
    EvaluateRun,
    RunAcceptance,
)


RepositoryRoot = Path(__file__).resolve().parents[2]
LiteralProfileCases = {
    "legacy-four": (
        ("FullAdder", 5, 8, 8, 15.0, 13.0, 10.0, 8.0, False),
        ("RippleCarryAdder4", 3, 512, 20, 25.0, 23.0, 25.0, 23.0, False),
        ("RippleCarryAdder8", 3, 131_072, 36, 30.0, 28.0, 30.0, 28.0, False),
        ("CarryLookaheadAdder4", 2, 512, 20, 120.0, 118.0, 120.0, 118.0, True),
    ),
    "expanded-seven": (
        ("HalfAdder", 3, 4, 4, 10.0, 8.0, 10.0, 8.0, False),
        ("FullAdder", 5, 8, 8, 15.0, 13.0, 15.0, 13.0, False),
        ("RippleCarryAdder4", 3, 512, 20, 25.0, 23.0, 25.0, 23.0, False),
        ("RippleCarryAdder8", 3, 131_072, 36, 30.0, 28.0, 30.0, 28.0, False),
        ("DecimalToBinary4", 3, 1_024, 22, 30.0, 28.0, 30.0, 28.0, False),
        ("TFlipFlopLatch", 3, 8, 8, 15.0, 13.0, 15.0, 13.0, False),
        ("CarryLookaheadAdder4", 2, 512, 20, 120.0, 118.0, 120.0, 118.0, True),
    ),
}
ScriptPath = RepositoryRoot / "Tools/Routing/CaptureRoutingDesignSnapshot.py"
ModuleSpec = importlib.util.spec_from_file_location(
    "CaptureRoutingDesignSnapshot",
    ScriptPath,
)
if ModuleSpec is None or ModuleSpec.loader is None:
    raise RuntimeError("could not load routing design snapshot module")
SnapshotTool = importlib.util.module_from_spec(ModuleSpec)
sys.modules[ModuleSpec.name] = SnapshotTool
ModuleSpec.loader.exec_module(SnapshotTool)


def BuildLiteralProfileAuthority(
    ProfileId: str,
    *,
    BaselineMode: str | None,
) -> dict[str, object]:
    """Build the independent, literal public-profile oracle."""
    MatrixMode = "expanded" if ProfileId == "expanded-seven" else "default"
    CaseTable = []
    for (
        Name,
        HistoricalRuns,
        TruthRows,
        FabricCanaries,
        StandaloneCeiling,
        StandaloneDeadline,
        HistoricalCeiling,
        HistoricalDeadline,
        NeedsExactProof,
    ) in LiteralProfileCases[ProfileId]:
        Historical = BaselineMode is not None
        CaseTable.append({
            "Name": Name,
            "ExamplePath": f"Assets/Examples/{Name}.sv",
            "TopModule": Name,
            "RequiredRuns": HistoricalRuns if Historical else 1,
            "HistoricalBaselineRequiredRuns": HistoricalRuns,
            "MchprsTruthTableRows": TruthRows,
            "FabricCanaryCount": FabricCanaries,
            "RuntimeCeilingSeconds": (
                HistoricalCeiling if Historical else StandaloneCeiling
            ),
            "PublicationReserveSeconds": 2.0,
            "RoutingDeadlineSeconds": (
                HistoricalDeadline if Historical else StandaloneDeadline
            ),
            "MaximumOverflowPeak": 1,
            "NeedsExactInterfaceProof": NeedsExactProof,
        })
    Authority = {
        "ProfileSchemaVersion": "routing-design-acceptance-profile-v1",
        "ProfileId": ProfileId,
        "CaseCount": len(CaseTable),
        "CaseTable": CaseTable,
        "MatrixMode": MatrixMode,
        "BaselineMode": BaselineMode,
    }
    Authority["ProfileSha256"] = sha256(json.dumps(
        Authority,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")).hexdigest()
    return Authority


def BuildSyntheticFailurePayload(
    *,
    CheckoutRoot: str = "/arbitrary/checkout",
    OutputRoot: str = "/arbitrary/output",
) -> dict[str, object]:
    """Return a minimal typed CLA4 structural failure with budget remaining."""
    PolicyIdentity = BuildPolicyProvenanceRecord("default")
    OutputIdentity = {
        "Directory": OutputRoot,
        "Format": "litematic",
        "Name": "CLA4.litematic",
        "Path": f"{OutputRoot}/CLA4.litematic",
        "Stem": "CLA4",
    }
    return {
        "SchemaVersion": "routing-failure-v1",
        "RuntimeSeconds": 16.376594,
        "SourceState": {
            "Revision": "0123456789abcdef",
            "Dirty": False,
        },
        "Failure": {
            "Stage": "Placement",
            "Reason": "PlacementOverlap",
            "Detail": "no exact-legal placement candidate was generated",
            "Diagnostics": {
                "Deadline": {
                    "Expired": False,
                    "RemainingMilliseconds": 101700,
                },
                "PlacementGenerationDecisions": [
                    {
                        "SourceGenerator": "row-beam",
                        "Result": "rejected-mandatory-access-conflict",
                        "ElapsedSeconds": 13.810852,
                        "RoutingSpacing": 5,
                        "JointPlacementCandidateIndex": 0,
                        "MandatoryAccessProfile": {
                            "SignalCount": 81,
                            "ClaimCount": 8814,
                            "ExactConflictCount": 2,
                            "ConflictResourceCount": 2,
                            "ConflictSignals": ["NandNet0", "Propagate0"],
                            "CrossConflicts": [
                                {
                                    "Kind": "Electrical",
                                    "Position": [16, 1, 5],
                                    "Owners": ["NandNet0", "Propagate0"],
                                }
                            ],
                            "SelfConflicts": [],
                            "OwnershipFingerprint": "ownership",
                            "ConflictFingerprint": "conflict",
                        },
                    }
                ],
            },
        },
        "NativeWork": {"RequestCounts": {}},
        "Policy": PolicyIdentity["Snapshot"],
        "Strategy": {
            "Requested": "default",
            "Used": "default",
            "FallbackUsed": False,
        },
        "Technology": {"TechnologyVersion": "redstone-routing-v1"},
        "OutputIdentity": OutputIdentity,
        "Reproduction": {
            "TopModule": "CarryLookaheadAdder4",
            "RequestedStrategy": "default",
            "Input": {
                "Path": f"{CheckoutRoot}/Assets/Examples/CarryLookaheadAdder4.sv",
                "Sha256": "input-hash",
                "SizeBytes": 123,
            },
            "Command": [
                f"{CheckoutRoot}/.venv/bin/python",
                f"{CheckoutRoot}/Main.py",
                "--input",
                f"{CheckoutRoot}/Assets/Examples/CarryLookaheadAdder4.sv",
                "--output",
                OutputRoot,
                "--outputname",
                "CLA4",
                "--routing-deadline-seconds",
                "118.0",
                "--routing-strategy",
                "default",
            ],
            "Output": OutputIdentity,
        },
    }


def WriteSyntheticFailure(
    Directory: Path,
    *,
    CheckoutRoot: str = "/arbitrary/checkout",
    OutputRoot: str = "/arbitrary/output",
) -> Path:
    """Write one filename-consistent synthetic CLA4 failure artifact."""
    FailurePath = Directory / "CLA4.RoutingFailure.json"
    FailurePath.write_text(
        json.dumps(BuildSyntheticFailurePayload(
            CheckoutRoot=CheckoutRoot,
            OutputRoot=OutputRoot,
        )),
        encoding="utf-8",
    )
    return FailurePath


def WriteSealedAcceptanceFixture(
    ManifestPath: Path,
    Manifest: dict[str, object],
    ArtifactPaths: tuple[Path, ...] = (),
) -> None:
    """Write one minimal archive seal from the independent test fixture."""
    ManifestPath.write_text(json.dumps(Manifest), encoding="utf-8")
    ArchiveRoot = ManifestPath.parent
    Files = []
    Seen: set[str] = set()
    for PathValue in (ManifestPath, *ArtifactPaths):
        RelativePath = PathValue.relative_to(ArchiveRoot).as_posix()
        if RelativePath in Seen:
            continue
        Seen.add(RelativePath)
        Files.append({
            "Path": RelativePath,
            "SizeBytes": PathValue.stat().st_size,
            "Sha256": SnapshotTool.Sha256File(PathValue),
        })
    Files.sort(key=lambda Record: Record["Path"])
    SourceState = Manifest["SourceState"]
    Command = Manifest["Runs"][0]["Command"]
    ProducerRoot = str(Path(Command[1]).parent)
    Archive = {
        "SchemaVersion": "router-benchmark-archive-v1",
        "ArchiveId": "synthetic-sealed-profile",
        "Publication": {
            "Complete": True,
            "Failure": None,
            "Status": "SEALED",
        },
        "Invocation": {
            "MatrixMode": Manifest["MatrixMode"],
            "BaselineMode": Manifest["BaselineMode"],
            "WorkingDirectory": ProducerRoot,
        },
        "Source": {
            "Stable": True,
            "AcceptanceProvenanceStable": True,
            "Start": {
                "Head": SourceState["Revision"],
                "Dirty": SourceState["Dirty"],
            },
            "End": {
                "Head": SourceState["Revision"],
                "Dirty": SourceState["Dirty"],
            },
        },
        "Files": Files,
    }
    ArchiveManifestPath = ArchiveRoot / "ArchiveManifest.json"
    ArchiveManifestPath.write_text(json.dumps(Archive), encoding="utf-8")
    ChecksumRecords = [
        *Files,
        {
            "Path": "ArchiveManifest.json",
            "Sha256": SnapshotTool.Sha256File(ArchiveManifestPath),
        },
    ]
    (ArchiveRoot / "SHA256SUMS").write_text(
        "".join(
            f"{Record['Sha256']}  {Record['Path']}\n"
            for Record in sorted(
                ChecksumRecords,
                key=lambda Record: Record["Path"],
            )
        ),
        encoding="utf-8",
    )


def BuildPublicAcceptanceSnapshotFixture(
    Root: Path,
    RequestedStrategy: str = RoutingStrategy.Default.value,
    *,
    MatrixMode: str = "default",
    BaselineMode: str | None = None,
    Sealed: bool = True,
) -> tuple[Path, Path, dict[str, object], dict[str, object], dict[str, object]]:
    """Create a valid public acceptance producer fixture and CLA4 failure."""
    Root.mkdir(parents=True, exist_ok=True)
    Configuration = AcceptanceConfiguration(
        RepositoryRoot=RepositoryRoot,
        OutputRoot=Root / "Acceptance",
        DateLabel="2026-09-06",
        PythonExecutable=Path(sys.executable),
        DryRun=True,
        IncludeCla4=True,
        RequestedRoutingStrategy=RequestedStrategy,
        MatrixMode=MatrixMode,
        BaselineMode=BaselineMode,
        BaselinePath=(
            Root / "Baseline.json" if BaselineMode is not None else None
        ),
        RoutingThreads=16 if BaselineMode is not None else None,
    )
    Manifest = RunAcceptance(Configuration)
    Manifest["SourceProvenanceStable"] = True
    Manifest["SourceState"] = dict(Manifest["SourceProvenance"]["Git"])
    ManifestPath = Root / "AcceptanceManifest.json"
    ManifestPath.write_text(json.dumps(Manifest), encoding="utf-8")

    FailurePayload = BuildSyntheticFailurePayload()
    FailurePayload["SourceState"] = dict(Manifest["SourceState"])
    FailurePolicy = deepcopy(
        Manifest["SourceProvenance"]["Policy"]["Snapshot"]
    )
    FailurePolicy["RuntimeBudgetSeconds"] = 118.0
    FailurePolicy["AdaptiveRouting"]["MaximumRuntimeSeconds"] = 118.0
    FailurePayload["Policy"] = FailurePolicy
    UsedStrategy = Manifest["SourceProvenance"]["ExpectedUsedRoutingStrategy"]
    FailurePayload["Strategy"] = {
        "Requested": RequestedStrategy,
        "Used": UsedStrategy,
        "FallbackUsed": False,
    }
    FailurePayload["Reproduction"]["RequestedStrategy"] = RequestedStrategy
    FailurePayload["Reproduction"]["Command"][-1] = RequestedStrategy
    FailurePayload["Reproduction"]["Input"]["Sha256"] = (
        Manifest["SourceProvenance"]["BenchmarkInputs"]
        ["CarryLookaheadAdder4"]["Sha256"]
    )
    FailurePath = Root / "CLA4.RoutingFailure.json"
    FailurePath.write_text(json.dumps(FailurePayload), encoding="utf-8")
    if Sealed:
        WriteSealedAcceptanceFixture(
            ManifestPath,
            Manifest,
            (FailurePath,),
        )
    return (
        ManifestPath,
        FailurePath,
        Manifest,
        SnapshotTool.BuildRoutingSourceManifest(RepositoryRoot),
        SnapshotTool.BuildCurrentRuntimeProvenance(RepositoryRoot),
    )


def AttachSelectedCla4Failure(
    Root: Path,
    Manifest: dict[str, object],
) -> Path:
    """Attach one evaluator-selected typed stop without file discovery."""
    Cla4Run = next(
        Run
        for Run in Manifest["Runs"]
        if Run["Circuit"] == "CarryLookaheadAdder4"
        and Run["Warmup"] is False
        and Run["Repetition"] == 1
    )
    FailureDirectory = Root / "SelectedFailure"
    FailureDirectory.mkdir(parents=True, exist_ok=True)
    FailurePath = FailureDirectory / "CLA4.RoutingFailure.json"
    RequestedStrategy = Manifest["SourceProvenance"][
        "RequestedRoutingStrategy"
    ]
    PolicyRecord = Manifest["SourceProvenance"]["Policy"]
    Payload = BuildSyntheticFailurePayload()
    Payload["SourceState"] = deepcopy(Manifest["SourceState"])
    EffectivePolicy = deepcopy(PolicyRecord["Snapshot"])
    EffectivePolicy["RuntimeBudgetSeconds"] = 118.0
    EffectivePolicy["AdaptiveRouting"]["MaximumRuntimeSeconds"] = 118.0
    Payload["Policy"] = EffectivePolicy
    Payload["Strategy"] = {
        "Requested": RequestedStrategy,
        "Used": Manifest["SourceProvenance"][
            "ExpectedUsedRoutingStrategy"
        ],
        "FallbackUsed": False,
    }
    Command = Cla4Run["Command"]
    InputPath = Command[Command.index("--input") + 1]
    InputRecord = deepcopy(
        Manifest["SourceProvenance"]["BenchmarkInputs"][
            "CarryLookaheadAdder4"
        ]
    )
    InputRecord["Path"] = InputPath
    Payload["Reproduction"].update({
        "TopModule": "CarryLookaheadAdder4",
        "RequestedStrategy": RequestedStrategy,
        "Command": Command,
        "Input": InputRecord,
    })
    FailurePath.write_text(json.dumps(Payload), encoding="utf-8")
    FailureRecord = {
        "Exists": True,
        "Path": str(FailurePath),
        "SizeBytes": FailurePath.stat().st_size,
        "Sha256": SnapshotTool.Sha256File(FailurePath),
    }
    Cla4Run["Status"] = "FAILED"
    Cla4Run["Accepted"] = False
    Cla4Run["Evaluation"] = {
        "Accepted": False,
        "Failures": ["routing failure artifact exists"],
        "Process": {
            "TimedOut": False,
            "ReturnCode": 1,
            "RequestedRoutingDeadlineSeconds": 118.0,
        },
        "Artifacts": {"RoutingFailure": FailureRecord},
        "Observed": {
            "ConfiguredRoutingIdentity": {
                "RequestedStrategy": RequestedStrategy,
                "UsedStrategy": Manifest["SourceProvenance"][
                    "ExpectedUsedRoutingStrategy"
                ],
                "CommandRoutingStrategy": RequestedStrategy,
                "PolicyIdentity": PolicyRecord,
            },
            "ActualRoutingIdentity": None,
            "FailureRoutingIdentity": CompleteFailureRoutingIdentity(
                PolicyRecord,
                RequestedStrategy,
            ),
            "RoutingIdentityChecks": {
                "FailureArtifactPresent": True,
                "FailureStrategyMatches": True,
                "FailureReproductionMatches": True,
                "FailurePolicyIdentityMatches": True,
            },
            "FailureArtifactResolution": {
                "Status": "nested",
                "CandidatePath": str(FailurePath),
                "Path": str(FailurePath),
                "Diagnostic": None,
            },
        },
    }
    Manifest["Status"] = "FAILED"
    Manifest["Accepted"] = False
    return FailurePath


def BuildSyntheticRunReceipt(
    PolicyIdentity: dict[str, object],
    RequestedStrategy: str,
    *,
    RunName: str,
    Status: str,
    Accepted: bool,
    ActualRoutingIdentity: dict[str, object] | None = None,
    FailureRoutingIdentity: dict[str, object] | None = None,
    RoutingIdentityChecks: dict[str, object] | None = None,
    TimedOut: bool = False,
    ReturnCode: int = 1,
    RoutingDeadlineSeconds: float = 118.0,
) -> dict[str, object]:
    """Build one explicit evaluator-receipt fixture without a live backend."""
    ConfiguredRoutingIdentity = {
        "RequestedStrategy": RequestedStrategy,
        "UsedStrategy": RequestedStrategy,
        "CommandRoutingStrategy": RequestedStrategy,
        "PolicyIdentity": PolicyIdentity,
    }
    return {
        "RunName": RunName,
        "Status": Status,
        "Accepted": Accepted,
        "RequestedRoutingDeadlineSeconds": RoutingDeadlineSeconds,
        "Requirements": {
            "RoutingDeadlineSeconds": RoutingDeadlineSeconds,
        },
        "Command": [
            "python",
            "Main.py",
            "--routing-strategy",
            RequestedStrategy,
            "--routing-deadline-seconds",
            str(RoutingDeadlineSeconds),
        ],
        "Evaluation": {
            "Process": {
                "TimedOut": TimedOut,
                "ReturnCode": ReturnCode,
                "RequestedRoutingDeadlineSeconds": RoutingDeadlineSeconds,
            },
            "Observed": {
                "ConfiguredRoutingIdentity": ConfiguredRoutingIdentity,
                "ActualRoutingIdentity": ActualRoutingIdentity,
                "FailureRoutingIdentity": FailureRoutingIdentity,
                "RoutingIdentityChecks": (
                    RoutingIdentityChecks
                    if RoutingIdentityChecks is not None
                    else {}
                ),
            },
        },
    }


def CompleteActualRoutingIdentity(
    PolicyIdentity: dict[str, object],
    RequestedStrategy: str,
    *,
    UsedStrategy: str | None = None,
) -> dict[str, object]:
    ReceiptPolicyIdentity = {
        Name: PolicyIdentity[Name]
        for Name in ("PolicyVersion", "Seed", "Sha256", "Snapshot")
    }
    return {
        "RequestedStrategy": RequestedStrategy,
        "UsedStrategy": UsedStrategy or RequestedStrategy,
        "FallbackUsed": False,
        "PolicyIdentity": ReceiptPolicyIdentity,
    }


def CompleteFailureRoutingIdentity(
    PolicyIdentity: dict[str, object],
    RequestedStrategy: str,
    RoutingDeadlineSeconds: float = 118.0,
) -> dict[str, object]:
    EffectivePolicyIdentity = SnapshotTool.BuildEffectiveRoutingPolicyIdentity(
        deepcopy(PolicyIdentity["Snapshot"]),
        RoutingDeadlineSeconds,
    )
    return {
        **CompleteActualRoutingIdentity(
            EffectivePolicyIdentity,
            RequestedStrategy,
        ),
        "ReproductionRequestedStrategy": RequestedStrategy,
        "ReproductionCommandRoutingStrategy": RequestedStrategy,
    }


def SummarizeSyntheticRunReceipt(
    Root: Path,
    Receipt: dict[str, object],
    RequestedStrategy: str = RoutingStrategy.Default.value,
) -> dict[str, object]:
    """Project one supplied receipt through the public snapshot boundary."""
    del Root
    PolicyRecord = BuildPolicyProvenanceRecord(RequestedStrategy)
    return SnapshotTool.BuildAcceptanceRunSummary(
        Receipt,
        RequestedStrategy=RequestedStrategy,
        ResolvedUsedStrategy=SnapshotTool.ResolveUsedRoutingStrategy(
            RequestedStrategy
        ),
        PolicyRecord=PolicyRecord,
        PolicyIdentity=SnapshotTool.BuildPolicySnapshotIdentity(
            PolicyRecord["Snapshot"]
        ),
    )


def BuildValidNandPayload() -> dict[str, object]:
    """Return a structurally valid minimal CLA4 NAND diagram."""
    return {
        "Module": "CarryLookaheadAdder4",
        "Inputs": ["A", "B"],
        "Outputs": ["Y"],
        "Gates": [
            {
                "Name": "InputA",
                "Kind": "INPUT",
                "Inputs": [],
                "Outputs": ["A"],
            },
            {
                "Name": "InputB",
                "Kind": "INPUT",
                "Inputs": [],
                "Outputs": ["B"],
            },
            {
                "Name": "Nand0",
                "Kind": "NAND",
                "Inputs": ["A", "B"],
                "Outputs": ["Y"],
            },
            {
                "Name": "OutputY",
                "Kind": "OUTPUT",
                "Inputs": ["Y"],
                "Outputs": ["Y$Output"],
            },
        ],
    }


class RoutingDesignSnapshotTests(unittest.TestCase):
    """Protect evidence identity, typed failure meaning, and fresh publication."""

    def test_process_timeout_preserves_absence_of_compiler_proof(self):
        with TemporaryDirectory() as Directory:
            Root = Path(Directory)
            ManifestPath = Root / "AcceptanceManifest.json"
            PolicyIdentity = BuildPolicyProvenanceRecord("default")
            Paths = {Key: str(Root / Name) for Key, Name in {
                "RoutingFailure": "CLA4.RoutingFailure.json", "Schematic": "CLA4.litematic",
                "PhysicalDesign": "CLA4.PhysicalDesign.json", "TruthTable": "CLA4.TruthTable.txt",
            }.items()}
            Payload = {
                "SchemaVersion": SnapshotTool.AcceptanceManifestSchemaVersion,
                "SourceState": {"Revision": "baseline"},
                "RoutingIdentity": {
                    "ConfiguredRequestedStrategy": "default",
                    "ExpectedUsedStrategy": "default",
                    "PolicyIdentity": PolicyIdentity,
                },
                "SourceProvenance": {
                    "ExpectedPolicyVersion": PolicyIdentity["PolicyVersion"],
                    "RequestedRoutingStrategy": "default",
                    "ExpectedUsedRoutingStrategy": "default",
                    "Policy": PolicyIdentity,
                    "BenchmarkInputs": {
                        "CarryLookaheadAdder4": {"Sha256": "input"},
                    },
                },
                "Runs": [{
                    "Circuit": "CarryLookaheadAdder4",
                    "RunName": "CLA4",
                    "Accepted": False,
                    "Command": [
                        "python",
                        "Main.py",
                        "--routing-strategy",
                        "default",
                    ],
                    "ArtifactPaths": Paths,
                    "Evaluation": {
                        "Process": {
                            "TimedOut": True,
                            "ReturnCode": 124,
                            "WallRuntimeSeconds": 125.0,
                        },
                    },
                }],
            }
            ManifestPath.write_text(json.dumps(Payload))
            Result = SnapshotTool.SummarizeCla4ProcessTimeout(ManifestPath)
            self.assertEqual(Result["EvidenceKind"], "ACCEPTANCE_PROCESS_TIMEOUT")
            self.assertEqual(Result["Reason"], "ProcessTimeout")
            self.assertTrue(Result["TimedOut"])
            self.assertIsNone(Result["DetailedRoutingStarted"])
            Path(Paths["RoutingFailure"]).write_text("{}")
            with self.assertRaisesRegex(ValueError, "mixed compiler evidence"):
                SnapshotTool.SummarizeCla4ProcessTimeout(ManifestPath)

    def test_non_timeout_manifest_cannot_supply_a_compiler_failure(self):
        with TemporaryDirectory() as Directory:
            PathValue = Path(Directory) / "AcceptanceManifest.json"
            PathValue.write_text(json.dumps({
                "SchemaVersion": SnapshotTool.AcceptanceManifestSchemaVersion,
                "Runs": [{"Circuit": "CarryLookaheadAdder4", "Accepted": False, "Evaluation": {"Process": {"TimedOut": False, "ReturnCode": 1}}}],
            }))
            with self.assertRaisesRegex(ValueError, "not a recorded process timeout"):
                SnapshotTool.SummarizeCla4ProcessTimeout(PathValue)

    def testCla4PlacementOverlapWithRemainingBudgetIsNotTimeout(self) -> None:
        PolicyProvenance = BuildPolicyProvenanceRecord("default")
        ExpectedPolicyIdentity = {
            Key: PolicyProvenance[Key]
            for Key in ("PolicyVersion", "Seed", "Sha256", "Snapshot")
        }
        with TemporaryDirectory() as Directory:
            FailurePath = WriteSyntheticFailure(Path(Directory))

            Summary = SnapshotTool.SummarizeCla4Failure(FailurePath)

        self.assertEqual(Summary["Stage"], "Placement")
        self.assertEqual(Summary["Reason"], "PlacementOverlap")
        self.assertFalse(Summary["TimedOut"])
        self.assertFalse(Summary["DetailedRoutingStarted"])
        self.assertEqual(
            Summary["Deadline"]["RemainingMilliseconds"],
            101700,
        )
        self.assertEqual(Summary["CandidateSummary"][0]["ClaimCount"], 8814)
        self.assertEqual(
            Summary["CandidateSummary"][0]["ConflictResourceCount"],
            2,
        )
        self.assertEqual(
            Summary["ObservedRoutingIdentity"]["RequestedStrategy"],
            "default",
        )
        self.assertEqual(
            Summary["ObservedRoutingIdentity"]["UsedStrategy"],
            "default",
        )
        self.assertFalse(
            Summary["ObservedRoutingIdentity"]["FallbackUsed"]
        )
        self.assertEqual(
            Summary["ObservedRoutingIdentity"]["PolicyIdentity"],
            ExpectedPolicyIdentity,
        )
        self.assertEqual(
            Summary["ObservedRoutingIdentity"]
            ["ReproductionCommandRoutingStrategy"],
            "default",
        )

    def testCla4SummaryRejectsWrongSchemaAndWrongCircuit(self) -> None:
        with TemporaryDirectory() as Directory:
            FailurePath = Path(Directory) / "Wrong.json"
            Payload = BuildSyntheticFailurePayload()
            Payload["SchemaVersion"] = "wrong"
            FailurePath.write_text(json.dumps(Payload))
            with self.assertRaisesRegex(ValueError, "routing-failure-v1"):
                SnapshotTool.SummarizeCla4Failure(FailurePath)

            Payload = BuildSyntheticFailurePayload()
            Payload["Reproduction"]["TopModule"] = "FullAdder"
            FailurePath.write_text(json.dumps(Payload))
            with self.assertRaisesRegex(ValueError, "not CarryLookaheadAdder4"):
                SnapshotTool.SummarizeCla4Failure(FailurePath)

    def testPorcelainParserPreservesRenameOriginAndUntrackedState(self) -> None:
        Status = (
            b"R  New.py\0Old.py\0"
            b" M Compilation/Pipeline.py\0"
            b"?? Notes.md\0"
        )

        Entries = SnapshotTool.ParsePorcelainV1Z(Status)

        self.assertEqual(Entries[0], {
            "IndexStatus": "R",
            "WorktreeStatus": " ",
            "Path": "New.py",
            "OriginalPath": "Old.py",
        })
        self.assertEqual(Entries[1]["WorktreeStatus"], "M")
        self.assertEqual(Entries[2]["Path"], "Notes.md")

    def testPortableSemanticEvidenceIgnoresEmbeddedAbsolutePaths(self) -> None:
        with TemporaryDirectory() as Directory:
            Root = Path(Directory)
            FirstRoot = Root / "First"
            SecondRoot = Root / "Second"
            FirstRoot.mkdir()
            SecondRoot.mkdir()
            FirstFailure = WriteSyntheticFailure(
                FirstRoot,
                CheckoutRoot="/first/checkout",
                OutputRoot="/first/output",
            )
            SecondFailure = WriteSyntheticFailure(
                SecondRoot,
                CheckoutRoot="/second/checkout",
                OutputRoot="/second/output",
            )
            FirstFailureSha256 = SnapshotTool.Sha256File(FirstFailure)
            SecondFailureSha256 = SnapshotTool.Sha256File(SecondFailure)
            CapturedAtUtc = datetime(
                2026,
                8,
                28,
                0,
                5,
                0,
                tzinfo=timezone.utc,
            )

            First = SnapshotTool.BuildRoutingDesignSnapshot(
                SnapshotTool.SnapshotConfiguration(
                    RepositoryRoot=RepositoryRoot,
                    OutputRoot=Root / "FirstSnapshots",
                    CapturedAtUtc=CapturedAtUtc,
                    Cla4FailurePath=FirstFailure,
                )
            )
            Second = SnapshotTool.BuildRoutingDesignSnapshot(
                SnapshotTool.SnapshotConfiguration(
                    RepositoryRoot=RepositoryRoot,
                    OutputRoot=Root / "SecondSnapshots",
                    CapturedAtUtc=CapturedAtUtc,
                    Cla4FailurePath=SecondFailure,
                )
            )

        self.assertNotEqual(FirstFailureSha256, SecondFailureSha256)
        self.assertNotEqual(
            First["ExactEvidenceSha256"],
            Second["ExactEvidenceSha256"],
        )
        self.assertEqual(
            First["PortableSemanticEvidenceSha256"],
            Second["PortableSemanticEvidenceSha256"],
        )

    def testSourceChangeBetweenBuildPassesIsRejected(self) -> None:
        with TemporaryDirectory() as Directory:
            Root = Path(Directory)
            FailurePath = WriteSyntheticFailure(Root)
            Configuration = SnapshotTool.SnapshotConfiguration(
                RepositoryRoot=RepositoryRoot,
                OutputRoot=Root / "Snapshots",
                CapturedAtUtc=datetime(
                    2026,
                    8,
                    28,
                    0,
                    5,
                    0,
                    tzinfo=timezone.utc,
                ),
                Cla4FailurePath=FailurePath,
            )
            Checkout = {"Revision": "revision", "Dirty": False}
            Runtime = {"SchemaVersion": "runtime"}
            with (
                patch.object(
                    SnapshotTool,
                    "ReadDetailedGitState",
                    return_value=Checkout,
                ),
                patch.object(
                    SnapshotTool,
                    "BuildRoutingSourceManifest",
                    side_effect=[
                        {"AggregateSha256": "before"},
                        {"AggregateSha256": "after"},
                    ],
                ),
                patch.object(
                    SnapshotTool,
                    "BuildCurrentRuntimeProvenance",
                    return_value=Runtime,
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "^source/provenance changed during capture$",
                ):
                    SnapshotTool.BuildRoutingDesignSnapshot(Configuration)

    def testMalformedAndWrongAcceptanceManifestAreRejected(self) -> None:
        with TemporaryDirectory() as Directory:
            Root = Path(Directory)
            FailureSummary = SnapshotTool.SummarizeCla4Failure(
                WriteSyntheticFailure(Root)
            )
            MalformedPath = Root / "MalformedAcceptance.json"
            MalformedPath.write_text("{", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                SnapshotTool.SummarizeAcceptanceManifest(
                    MalformedPath,
                    FailureSummary,
                    {},
                    {},
                )

            (
                WrongPath,
                WrongFailurePath,
                WrongManifest,
                CurrentSource,
                CurrentRuntime,
            ) = BuildPublicAcceptanceSnapshotFixture(Root / "Wrong")
            WrongManifest["SourceState"]["Revision"] = "wrong-revision"
            WrongManifest["SourceProvenance"]["Git"][
                "Revision"
            ] = "wrong-revision"
            WriteSealedAcceptanceFixture(
                WrongPath,
                WrongManifest,
                (WrongFailurePath,),
            )
            with self.assertRaisesRegex(
                ValueError,
                "source revisions disagree",
            ):
                SnapshotTool.SummarizeAcceptanceManifest(
                    WrongPath,
                    SnapshotTool.SummarizeCla4Failure(WrongFailurePath),
                    CurrentSource,
                    CurrentRuntime,
                )

    def test_explicit_v17_used_alias_mismatch_is_not_selected_policy_match(
        self,
    ) -> None:
        """The v17 snapshot cannot make a coherent wrong used alias valid."""
        RequestedStrategy = (
            RoutingStrategy.RoutingAwarePlacementAccess.value
        )
        with TemporaryDirectory() as Directory:
            Root = Path(Directory)
            (
                ManifestPath,
                FailurePath,
                Manifest,
                CurrentSource,
                CurrentRuntime,
            ) = BuildPublicAcceptanceSnapshotFixture(
                Root,
                RequestedStrategy,
            )
            BaselineFailure = SnapshotTool.SummarizeCla4Failure(FailurePath)
            Baseline = SnapshotTool.SummarizeAcceptanceManifest(
                ManifestPath,
                BaselineFailure,
                CurrentSource,
                CurrentRuntime,
            )
            self.assertTrue(
                Baseline["CrossChecks"]["CurrentSelectedPolicyMatches"]
            )

            TamperedManifest = deepcopy(Manifest)
            TamperedManifest["SourceProvenance"][
                "ExpectedUsedRoutingStrategy"
            ] = RoutingStrategy.Default.value
            TamperedManifest["SourceProvenance"]["Policy"][
                "UsedRoutingStrategy"
            ] = RoutingStrategy.Default.value
            TamperedManifest["RoutingIdentity"][
                "ExpectedUsedStrategy"
            ] = RoutingStrategy.Default.value
            FailurePayload = json.loads(FailurePath.read_text(encoding="utf-8"))
            FailurePayload["Strategy"]["Used"] = RoutingStrategy.Default.value
            FailurePath.write_text(json.dumps(FailurePayload), encoding="utf-8")
            WriteSealedAcceptanceFixture(
                ManifestPath,
                TamperedManifest,
                (FailurePath,),
            )
            TamperedFailure = SnapshotTool.SummarizeCla4Failure(FailurePath)
            Result = SnapshotTool.SummarizeAcceptanceManifest(
                ManifestPath,
                TamperedFailure,
                CurrentSource,
                CurrentRuntime,
            )

        self.assertFalse(
            Result["CrossChecks"]["CurrentSelectedPolicyMatches"]
        )

    def test_source_content_aggregate_corruption_is_rejected(self) -> None:
        """A recorded source-file inventory cannot accept another aggregate."""
        with TemporaryDirectory() as Directory:
            Root = Path(Directory)
            (
                ManifestPath,
                FailurePath,
                Manifest,
                CurrentSource,
                CurrentRuntime,
            ) = BuildPublicAcceptanceSnapshotFixture(Root)
            BaselineFailure = SnapshotTool.SummarizeCla4Failure(FailurePath)
            SnapshotTool.SummarizeAcceptanceManifest(
                ManifestPath,
                BaselineFailure,
                CurrentSource,
                CurrentRuntime,
            )
            TamperedManifest = deepcopy(Manifest)
            Original = TamperedManifest["SourceProvenance"]["SourceContent"]
            Digest = Original["AggregateSha256"]
            Original["AggregateSha256"] = (
                ("0" if Digest[0] != "0" else "1") + Digest[1:]
            )
            WriteSealedAcceptanceFixture(
                ManifestPath,
                TamperedManifest,
                (FailurePath,),
            )

            with self.assertRaises(ValueError):
                SnapshotTool.SummarizeAcceptanceManifest(
                    ManifestPath,
                    BaselineFailure,
                    CurrentSource,
                    CurrentRuntime,
                )

    def test_physical_template_aggregate_corruption_is_rejected(self) -> None:
        """A recorded template inventory cannot accept another aggregate."""
        with TemporaryDirectory() as Directory:
            Root = Path(Directory)
            (
                ManifestPath,
                FailurePath,
                Manifest,
                CurrentSource,
                CurrentRuntime,
            ) = BuildPublicAcceptanceSnapshotFixture(Root)
            BaselineFailure = SnapshotTool.SummarizeCla4Failure(FailurePath)
            SnapshotTool.SummarizeAcceptanceManifest(
                ManifestPath,
                BaselineFailure,
                CurrentSource,
                CurrentRuntime,
            )
            TamperedManifest = deepcopy(Manifest)
            Original = TamperedManifest["SourceProvenance"]["PhysicalTemplates"]
            Digest = Original["AggregateSha256"]
            Original["AggregateSha256"] = (
                ("0" if Digest[0] != "0" else "1") + Digest[1:]
            )
            WriteSealedAcceptanceFixture(
                ManifestPath,
                TamperedManifest,
                (FailurePath,),
            )

            with self.assertRaises(ValueError):
                SnapshotTool.SummarizeAcceptanceManifest(
                    ManifestPath,
                    BaselineFailure,
                    CurrentSource,
                    CurrentRuntime,
                )

    def test_versioned_profiles_are_inferred_from_literal_public_oracles(
        self,
    ) -> None:
        """Legacy and expanded producer documents select only literal profiles."""
        Profiles = (
            ("legacy-four", "default", None),
            ("legacy-four", "default", "compare"),
            ("expanded-seven", "expanded", None),
        )
        for ProfileId, MatrixMode, BaselineMode in Profiles:
            with self.subTest(ProfileId=ProfileId, BaselineMode=BaselineMode), TemporaryDirectory() as Directory:
                Root = Path(Directory)
                (
                    ManifestPath,
                    FailurePath,
                    Manifest,
                    CurrentSource,
                    CurrentRuntime,
                ) = BuildPublicAcceptanceSnapshotFixture(
                    Root,
                    MatrixMode=MatrixMode,
                    BaselineMode=BaselineMode,
                )
                if BaselineMode is not None:
                    FullAdderCase = next(
                        Case
                        for Case in Manifest["Cases"]
                        if Case["Name"] == "FullAdder"
                    )
                    FullAdderCase["RuntimeCeilingSeconds"] = 10.0
                    FullAdderCase["RoutingDeadlineSeconds"] = 8.0
                    for Run in Manifest["Runs"]:
                        if Run["Circuit"] != "FullAdder":
                            continue
                        Run["Requirements"]["RuntimeCeilingSeconds"] = 10.0
                        Run["Requirements"]["RoutingDeadlineSeconds"] = 8.0
                        Run["RequestedRoutingDeadlineSeconds"] = 8.0
                        DeadlineIndex = Run["Command"].index(
                            "--routing-deadline-seconds"
                        ) + 1
                        Run["Command"][DeadlineIndex] = "8.0"
                Manifest["Cases"].reverse()
                Manifest["Runs"].reverse()
                WriteSealedAcceptanceFixture(
                    ManifestPath,
                    Manifest,
                    (FailurePath,),
                )

                Summary = SnapshotTool.SummarizeAcceptanceManifest(
                    ManifestPath,
                    SnapshotTool.SummarizeCla4Failure(FailurePath),
                    CurrentSource,
                    CurrentRuntime,
                )

                ExpectedAuthority = BuildLiteralProfileAuthority(
                    ProfileId,
                    BaselineMode=BaselineMode,
                )
                self.assertEqual(
                    Summary["AcceptanceProfile"],
                    ExpectedAuthority,
                )
                self.assertEqual(
                    [Case["Name"] for Case in Summary["CaseMatrix"]],
                    [Case["Name"] for Case in ExpectedAuthority["CaseTable"]],
                )
                self.assertEqual(
                    [Run["RunName"] for Run in Summary["Runs"]],
                    [
                        RunName
                        for _Sequence, RunName in sorted(
                            (Run["Sequence"], Run["RunName"])
                            for Run in Manifest["Runs"]
                        )
                    ],
                )
                self.assertTrue(all(
                    Run["BackendState"]["State"] == "unknown"
                    for Run in Summary["Runs"]
                ))

                if ProfileId == "expanded-seven":
                    Snapshot = SnapshotTool.BuildRoutingDesignSnapshot(
                        SnapshotTool.SnapshotConfiguration(
                            RepositoryRoot=RepositoryRoot,
                            OutputRoot=Root / "Snapshots",
                            CapturedAtUtc=datetime(
                                2026,
                                9,
                                7,
                                5,
                                30,
                                tzinfo=timezone.utc,
                            ),
                            Cla4FailurePath=FailurePath,
                            AcceptanceManifestPath=ManifestPath,
                        )
                    )
                    self.assertEqual(
                        Snapshot["SchemaVersion"],
                        "routing-design-snapshot-v3",
                    )

    def test_profile_selection_rejects_credible_manifest_shortcuts(self) -> None:
        """Names alone cannot authorize altered cases, runs, modes, or provenance."""
        def Mutate(Name: str, Manifest: dict[str, object]) -> None:
            Cases = Manifest["Cases"]
            Runs = Manifest["Runs"]
            if Name == "missing-case":
                Cases.pop()
            elif Name == "extra-case":
                Extra = deepcopy(Cases[0])
                Extra["Name"] = "InventedCircuit"
                Cases.append(Extra)
            elif Name == "duplicate-case":
                Cases.append(deepcopy(Cases[0]))
            elif Name == "matrix-mode":
                Manifest["MatrixMode"] = "default"
            elif Name == "execution-mode":
                Manifest["ExecutionMode"] = "parallel"
            elif Name == "fail-fast-coercion":
                Manifest["FailFast"] = 0
            elif Name == "case-count":
                Cases[0]["TruthTableRows"] += 1
            elif Name == "case-path":
                Cases[0]["ExamplePath"] = "Assets/Examples/FullAdder.sv"
            elif Name == "missing-run":
                Runs.pop()
            elif Name == "duplicate-run":
                Runs.append(deepcopy(Runs[0]))
            elif Name == "run-occurrence":
                Runs[0]["Repetition"] = 2
            elif Name == "run-command":
                Runs[0]["Command"].extend([
                    "--input",
                    Runs[0]["Command"][
                        Runs[0]["Command"].index("--input") + 1
                    ],
                ])
            elif Name == "source-path":
                Manifest["SourceProvenance"]["BenchmarkInputs"][
                    "HalfAdder"
                ]["Path"] = "Assets/Examples/FullAdder.sv"
            elif Name == "source-identity":
                Manifest["SourceProvenance"]["Git"]["Revision"] = "wrong"
            elif Name == "producer-dirty-coercion":
                Manifest["SourceState"]["Dirty"] = 0
            elif Name == "manifest-accepted-coercion":
                Manifest["Accepted"] = 0
            elif Name == "run-accepted-coercion":
                Runs[0]["Accepted"] = 0
            elif Name == "caller-profile-override":
                Manifest["AcceptanceProfile"] = {"ProfileId": "legacy-four"}
            else:
                raise AssertionError(Name)

        MutationNames = (
            "missing-case",
            "extra-case",
            "duplicate-case",
            "matrix-mode",
            "execution-mode",
            "fail-fast-coercion",
            "case-count",
            "case-path",
            "missing-run",
            "duplicate-run",
            "run-occurrence",
            "run-command",
            "source-path",
            "source-identity",
            "producer-dirty-coercion",
            "manifest-accepted-coercion",
            "run-accepted-coercion",
            "caller-profile-override",
        )
        for MutationName in MutationNames:
            with self.subTest(MutationName=MutationName), TemporaryDirectory() as Directory:
                Root = Path(Directory)
                (
                    ManifestPath,
                    FailurePath,
                    Manifest,
                    CurrentSource,
                    CurrentRuntime,
                ) = BuildPublicAcceptanceSnapshotFixture(
                    Root,
                    MatrixMode="expanded",
                )
                Mutate(MutationName, Manifest)
                WriteSealedAcceptanceFixture(
                    ManifestPath,
                    Manifest,
                    (FailurePath,),
                )

                with self.assertRaises(ValueError):
                    SnapshotTool.SummarizeAcceptanceManifest(
                        ManifestPath,
                        SnapshotTool.SummarizeCla4Failure(FailurePath),
                        CurrentSource,
                        CurrentRuntime,
                    )

    def test_unsealed_selected_typed_failure_is_rejected(self) -> None:
        """An evaluator-local hash cannot replace a verified archive seal."""
        with TemporaryDirectory() as Directory:
            Root = Path(Directory)
            (
                ManifestPath,
                _FailurePath,
                Manifest,
                CurrentSource,
                CurrentRuntime,
            ) = BuildPublicAcceptanceSnapshotFixture(
                Root,
                MatrixMode="expanded",
                Sealed=False,
            )
            SelectedFailure = AttachSelectedCla4Failure(Root, Manifest)
            ManifestPath.write_text(json.dumps(Manifest), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "archive seal"):
                SnapshotTool.SummarizeAcceptanceManifest(
                    ManifestPath,
                    SnapshotTool.SummarizeCla4Failure(SelectedFailure),
                    CurrentSource,
                    CurrentRuntime,
                )

    def test_sealed_selected_typed_failure_drives_not_run_backend_state(
        self,
    ) -> None:
        """Only a sealed evaluator-selected typed stop supplies stage/reason."""
        with TemporaryDirectory() as Directory:
            Root = Path(Directory)
            (
                ManifestPath,
                ExplicitFailure,
                Manifest,
                CurrentSource,
                CurrentRuntime,
            ) = BuildPublicAcceptanceSnapshotFixture(
                Root,
                MatrixMode="expanded",
            )
            SelectedFailure = AttachSelectedCla4Failure(Root, Manifest)
            WriteSealedAcceptanceFixture(
                ManifestPath,
                Manifest,
                (ExplicitFailure, SelectedFailure),
            )

            Summary = SnapshotTool.SummarizeAcceptanceManifest(
                ManifestPath,
                SnapshotTool.SummarizeCla4Failure(SelectedFailure),
                CurrentSource,
                CurrentRuntime,
            )

            Cla4Run = Summary["Runs"][-1]
            self.assertEqual(Cla4Run["FailureArtifact"]["Stage"], "Placement")
            self.assertEqual(
                Cla4Run["FailureArtifact"]["Reason"],
                "PlacementOverlap",
            )
            self.assertEqual(
                Cla4Run["BackendState"],
                {
                    "State": "not-run",
                    "ProfileChecked": False,
                    "UpstreamStage": "Placement",
                    "UpstreamReason": "PlacementOverlap",
                },
            )
            self.assertFalse(Cla4Run["Accepted"])

    def test_selected_failure_rejects_unsafe_or_unbound_artifacts(self) -> None:
        """No newest-file scan, symlink, non-file, escape, or bad digest is evidence."""
        Variants = (
            "resolution-mismatch",
            "digest-mismatch",
            "out-of-root",
            "symlink",
            "parent-symlink",
            "directory",
            "fifo",
        )
        for Variant in Variants:
            with self.subTest(Variant=Variant), TemporaryDirectory() as Directory:
                Root = Path(Directory)
                (
                    ManifestPath,
                    ExplicitFailure,
                    Manifest,
                    CurrentSource,
                    CurrentRuntime,
                ) = BuildPublicAcceptanceSnapshotFixture(
                    Root,
                    MatrixMode="expanded",
                )
                SelectedFailure = AttachSelectedCla4Failure(Root, Manifest)
                Cla4Run = next(
                    Run
                    for Run in Manifest["Runs"]
                    if Run["Circuit"] == "CarryLookaheadAdder4"
                )
                Artifacts = Cla4Run["Evaluation"]["Artifacts"]
                Resolution = Cla4Run["Evaluation"]["Observed"][
                    "FailureArtifactResolution"
                ]
                if Variant == "resolution-mismatch":
                    Resolution["Path"] = str(ExplicitFailure)
                elif Variant == "digest-mismatch":
                    Artifacts["RoutingFailure"]["Sha256"] = "0" * 64
                elif Variant == "out-of-root":
                    Outside = Root.parent / f"{Root.name}-outside.json"
                    Outside.write_bytes(SelectedFailure.read_bytes())
                    Artifacts["RoutingFailure"]["Path"] = str(Outside)
                    Resolution["Path"] = str(Outside)
                elif Variant == "symlink":
                    Link = Root / "SelectedFailureLink.json"
                    Link.symlink_to(SelectedFailure)
                    Artifacts["RoutingFailure"]["Path"] = str(Link)
                    Resolution["Path"] = str(Link)
                elif Variant == "parent-symlink":
                    OutsideDirectory = Root.parent / f"{Root.name}-outside"
                    OutsideDirectory.mkdir()
                    OutsideFailure = OutsideDirectory / "Injected.json"
                    OutsideFailure.write_bytes(SelectedFailure.read_bytes())
                    ParentLink = Root / "ParentLink"
                    ParentLink.symlink_to(OutsideDirectory, target_is_directory=True)
                    ThroughParentLink = ParentLink / OutsideFailure.name
                    Artifacts["RoutingFailure"]["Path"] = str(
                        ThroughParentLink
                    )
                    Resolution["Path"] = str(ThroughParentLink)
                elif Variant == "directory":
                    DirectoryPath = Root / "FailureDirectory"
                    DirectoryPath.mkdir()
                    Artifacts["RoutingFailure"]["Path"] = str(DirectoryPath)
                    Resolution["Path"] = str(DirectoryPath)
                elif Variant == "fifo":
                    FifoPath = Root / "FailurePipe"
                    os.mkfifo(FifoPath)
                    Artifacts["RoutingFailure"]["Path"] = str(FifoPath)
                    Resolution["Path"] = str(FifoPath)
                WriteSealedAcceptanceFixture(
                    ManifestPath,
                    Manifest,
                    (ExplicitFailure, SelectedFailure),
                )

                with self.assertRaises(ValueError):
                    SnapshotTool.SummarizeAcceptanceManifest(
                        ManifestPath,
                        SnapshotTool.SummarizeCla4Failure(ExplicitFailure),
                        CurrentSource,
                        CurrentRuntime,
                    )

                if Variant == "out-of-root":
                    Outside.unlink()
                elif Variant == "parent-symlink":
                    ParentLink.unlink()
                    OutsideFailure.unlink()
                    OutsideDirectory.rmdir()

    def test_snapshot_archive_read_fails_closed_without_safe_primitives(
        self,
    ) -> None:
        with TemporaryDirectory() as Directory:
            Root = Path(Directory)
            Evidence = Root / "evidence.bin"
            Evidence.write_bytes(b"inside")
            with patch.object(
                SnapshotTool.os,
                "supports_dir_fd",
                frozenset(),
            ):
                with self.assertRaisesRegex(ValueError, "unavailable"):
                    SnapshotTool.ReadRegularArchiveBytes(Root, Evidence)

    def test_sealed_summary_does_not_reopen_archive_members_by_path(
        self,
    ) -> None:
        """One verified seal observation supplies parse, hashes, and failures."""
        with TemporaryDirectory() as Directory:
            Root = Path(Directory)
            (
                ManifestPath,
                ExplicitFailure,
                Manifest,
                CurrentSource,
                CurrentRuntime,
            ) = BuildPublicAcceptanceSnapshotFixture(
                Root,
                MatrixMode="expanded",
            )
            SelectedFailure = AttachSelectedCla4Failure(Root, Manifest)
            WriteSealedAcceptanceFixture(
                ManifestPath,
                Manifest,
                (ExplicitFailure, SelectedFailure),
            )
            Cla4Failure = SnapshotTool.SummarizeCla4Failure(
                SelectedFailure
            )
            OriginalReadText = Path.read_text
            OriginalStat = Path.stat
            OriginalResolve = Path.resolve
            OriginalSha256File = SnapshotTool.Sha256File
            AbsoluteRoot = Path(os.path.abspath(Root))

            def IsArchiveMember(PathValue: Path) -> bool:
                Absolute = Path(os.path.abspath(PathValue))
                return Absolute != AbsoluteRoot and Absolute.is_relative_to(
                    AbsoluteRoot
                )

            def GuardedReadText(PathValue: Path, *Arguments, **Keywords):
                if IsArchiveMember(PathValue):
                    raise AssertionError(
                        "pathname read of verified archive member"
                    )
                return OriginalReadText(PathValue, *Arguments, **Keywords)

            def GuardedStat(PathValue: Path, *Arguments, **Keywords):
                if IsArchiveMember(PathValue):
                    raise AssertionError(
                        "pathname stat of verified archive member"
                    )
                return OriginalStat(PathValue, *Arguments, **Keywords)

            def GuardedResolve(PathValue: Path, *Arguments, **Keywords):
                if IsArchiveMember(PathValue):
                    raise AssertionError(
                        "pathname resolve of verified archive member"
                    )
                return OriginalResolve(PathValue, *Arguments, **Keywords)

            def GuardedSha256File(PathValue: Path) -> str:
                if IsArchiveMember(PathValue):
                    raise AssertionError(
                        "pathname hash of verified archive member"
                    )
                return OriginalSha256File(PathValue)

            with (
                patch.object(Path, "read_text", GuardedReadText),
                patch.object(Path, "stat", GuardedStat),
                patch.object(Path, "resolve", GuardedResolve),
                patch.object(
                    SnapshotTool,
                    "Sha256File",
                    GuardedSha256File,
                ),
            ):
                Summary = SnapshotTool.SummarizeAcceptanceManifest(
                    ManifestPath,
                    Cla4Failure,
                    CurrentSource,
                    CurrentRuntime,
                )

            self.assertEqual(Summary["Status"], "FAILED")
            self.assertEqual(
                Summary["Runs"][-1]["FailureArtifact"]["Stage"],
                "Placement",
            )

    def test_sealed_summary_uses_cached_members_after_leaf_replacement(
        self,
    ) -> None:
        """Manifest and selected failure remain one sealed observation."""
        with TemporaryDirectory() as Directory:
            Root = Path(Directory)
            (
                ManifestPath,
                ExplicitFailure,
                Manifest,
                CurrentSource,
                CurrentRuntime,
            ) = BuildPublicAcceptanceSnapshotFixture(
                Root,
                MatrixMode="expanded",
            )
            SelectedFailure = AttachSelectedCla4Failure(Root, Manifest)
            WriteSealedAcceptanceFixture(
                ManifestPath,
                Manifest,
                (ExplicitFailure, SelectedFailure),
            )
            Evidence = SnapshotTool.BuildSealedArchiveEvidence(ManifestPath)
            FailureObservation = Evidence.ObservationForPath(SelectedFailure)
            self.assertIsNotNone(FailureObservation)
            assert FailureObservation is not None
            Cla4Failure = SnapshotTool.SummarizeCla4Failure(
                SelectedFailure,
                Observation=FailureObservation,
                ArchiveEvidence=Evidence,
            )
            ManifestPath.write_text(
                json.dumps({"SchemaVersion": "malicious"}),
                encoding="utf-8",
            )
            SelectedFailure.write_text(
                json.dumps({
                    "SchemaVersion": "routing-failure-v1",
                    "Failure": {
                        "Stage": "External",
                        "Reason": "Replacement",
                    },
                }),
                encoding="utf-8",
            )

            with patch.object(
                SnapshotTool,
                "ReadRegularArchiveBytes",
                side_effect=AssertionError("archive member was reopened"),
            ):
                Summary = SnapshotTool.SummarizeAcceptanceManifest(
                    ManifestPath,
                    Cla4Failure,
                    CurrentSource,
                    CurrentRuntime,
                    ArchiveEvidence=Evidence,
                )

            Cla4Run = Summary["Runs"][-1]
            self.assertEqual(
                Cla4Run["FailureArtifact"]["Stage"],
                "Placement",
            )
            self.assertEqual(
                Cla4Run["FailureArtifact"]["Reason"],
                "PlacementOverlap",
            )
            self.assertEqual(
                Summary["ArtifactSha256"],
                Evidence.AcceptanceManifest.Sha256,
            )

    def test_snapshot_staging_uses_captured_bytes_after_archive_replacement(
        self,
    ) -> None:
        """Publication copies observations, never a later pathname target."""
        with TemporaryDirectory() as Directory:
            Workspace = Path(Directory)
            ArchiveRoot = Workspace / "Archive"
            ArchiveRoot.mkdir()
            (
                ManifestPath,
                FailurePath,
                _Manifest,
                _CurrentSource,
                _CurrentRuntime,
            ) = BuildPublicAcceptanceSnapshotFixture(
                ArchiveRoot,
                MatrixMode="expanded",
            )
            Configuration = SnapshotTool.SnapshotConfiguration(
                RepositoryRoot=RepositoryRoot,
                OutputRoot=Workspace / "Snapshots",
                CapturedAtUtc=datetime(
                    2026,
                    9,
                    7,
                    6,
                    45,
                    tzinfo=timezone.utc,
                ),
                Cla4FailurePath=FailurePath,
                AcceptanceManifestPath=ManifestPath,
            )
            Snapshot = SnapshotTool.BuildRoutingDesignSnapshot(Configuration)
            ExpectedBytes = {
                str(Artifact["SnapshotPath"]): Path(
                    str(Artifact["OriginalPath"])
                ).read_bytes()
                for Artifact in Snapshot["Artifacts"]
            }
            OriginalArchive = Workspace / "OriginalArchive"
            ArchiveRoot.rename(OriginalArchive)
            ArchiveRoot.mkdir()

            with patch.object(
                SnapshotTool.shutil,
                "copyfile",
                side_effect=AssertionError(
                    "snapshot staging reopened an archive pathname"
                ),
            ):
                OutputPath = SnapshotTool.WriteSnapshotStaged(
                    Configuration,
                    Snapshot,
                )

            for RelativePath, Data in ExpectedBytes.items():
                self.assertEqual((OutputPath / RelativePath).read_bytes(), Data)

    def test_snapshot_safe_open_requires_every_named_primitive(self) -> None:
        with TemporaryDirectory() as Directory:
            Root = Path(Directory)
            Evidence = Root / "evidence.bin"
            Evidence.write_bytes(b"inside")
            for AttributeName in (
                "O_DIRECTORY",
                "O_NOFOLLOW",
                "O_NONBLOCK",
            ):
                with self.subTest(AttributeName=AttributeName):
                    Original = getattr(SnapshotTool.os, AttributeName)
                    delattr(SnapshotTool.os, AttributeName)
                    try:
                        with self.assertRaisesRegex(ValueError, "unavailable"):
                            SnapshotTool.ReadRegularArchiveBytes(
                                Root,
                                Evidence,
                            )
                    finally:
                        setattr(SnapshotTool.os, AttributeName, Original)

    def test_accepted_backend_receipt_must_match_literal_profile(self) -> None:
        """Profile validity alone cannot promote a wrong backend observation."""
        PolicyRecord = BuildPolicyProvenanceRecord("default")
        Receipt = BuildSyntheticRunReceipt(
            PolicyRecord,
            "default",
            RunName="HalfAdderRun1",
            Status="PASSED",
            Accepted=True,
            ActualRoutingIdentity=CompleteActualRoutingIdentity(
                PolicyRecord,
                "default",
            ),
        )
        Receipt["Evaluation"].update({
            "Accepted": True,
            "Failures": [],
            "Artifacts": {},
        })
        Receipt["Evaluation"]["Observed"].update({
            "FabricValidationStatus": "passed",
            "FabricValidationVectors": 5,
        })
        ProfileCase = BuildLiteralProfileAuthority(
            "expanded-seven",
            BaselineMode=None,
        )["CaseTable"][0]

        with self.assertRaisesRegex(
            ValueError,
            "profile-matching backend receipt",
        ):
            SnapshotTool.BuildAcceptanceRunSummary(
                Receipt,
                RequestedStrategy="default",
                ResolvedUsedStrategy="default",
                PolicyRecord=PolicyRecord,
                PolicyIdentity=SnapshotTool.BuildPolicySnapshotIdentity(
                    PolicyRecord["Snapshot"]
                ),
                ProfileCase=ProfileCase,
            )

    def test_run_projection_preserves_coherent_failed_default_and_explicit_receipts(
        self,
    ) -> None:
        """A failed backend does not make a complete observed receipt inconsistent."""
        for RequestedStrategy in (
            RoutingStrategy.Default.value,
            RoutingStrategy.RoutingAwarePlacementAccess.value,
        ):
            with self.subTest(RequestedStrategy=RequestedStrategy), TemporaryDirectory() as Directory:
                Root = Path(Directory)
                PolicyIdentity = BuildPolicyProvenanceRecord(
                    RequestedStrategy
                )
                Receipt = BuildSyntheticRunReceipt(
                    PolicyIdentity,
                    RequestedStrategy,
                    RunName=f"{RequestedStrategy}-failed",
                    Status="FAILED",
                    Accepted=False,
                    ActualRoutingIdentity=CompleteActualRoutingIdentity(
                        PolicyIdentity,
                        RequestedStrategy,
                    ),
                    RoutingIdentityChecks={
                        "ActualUsedStrategyMatches": True,
                    },
                )
                Run = SummarizeSyntheticRunReceipt(
                    Root,
                    Receipt,
                    RequestedStrategy,
                )

                self.assertEqual(Run["RunName"], Receipt["RunName"])
                self.assertEqual(Run["Status"], "FAILED")
                self.assertFalse(Run["Accepted"])
                self.assertEqual(
                    Run["CommandRoutingStrategy"],
                    RequestedStrategy,
                )
                self.assertEqual(
                    Run["ConfiguredRoutingIdentity"],
                    Receipt["Evaluation"]["Observed"][
                        "ConfiguredRoutingIdentity"
                    ],
                )
                self.assertEqual(
                    Run["ActualRoutingIdentity"],
                    Receipt["Evaluation"]["Observed"][
                        "ActualRoutingIdentity"
                    ],
                )
                self.assertIsNone(Run["FailureRoutingIdentity"])
                self.assertEqual(
                    Run["RoutingIdentityChecks"],
                    Receipt["Evaluation"]["Observed"][
                        "RoutingIdentityChecks"
                    ],
                )
                self.assertTrue(Run["RoutingIdentityConsistent"])

    def test_run_projection_computes_false_from_wrong_actual_used_alias(
        self,
    ) -> None:
        """Stored evaluator success cannot conceal a wrong explicit receipt."""
        RequestedStrategy = (
            RoutingStrategy.RoutingAwarePlacementAccess.value
        )
        with TemporaryDirectory() as Directory:
            Root = Path(Directory)
            PolicyIdentity = BuildPolicyProvenanceRecord(RequestedStrategy)
            Receipt = BuildSyntheticRunReceipt(
                PolicyIdentity,
                RequestedStrategy,
                RunName="explicit-wrong-used",
                Status="FAILED",
                Accepted=False,
                ActualRoutingIdentity=CompleteActualRoutingIdentity(
                    PolicyIdentity,
                    RequestedStrategy,
                    UsedStrategy=RoutingStrategy.Default.value,
                ),
                RoutingIdentityChecks={
                    "ActualUsedStrategyMatches": True,
                },
            )
            Run = SummarizeSyntheticRunReceipt(
                Root,
                Receipt,
                RequestedStrategy,
            )

        self.assertEqual(
            Run["ActualRoutingIdentity"]["UsedStrategy"],
            RoutingStrategy.Default.value,
        )
        self.assertTrue(
            Run["RoutingIdentityChecks"]["ActualUsedStrategyMatches"]
        )
        self.assertFalse(Run["RoutingIdentityConsistent"])

    def test_run_projection_requires_exact_boolean_false_receipts(
        self,
    ) -> None:
        """Integer zero is not an explicit no-fallback observation."""
        for ReceiptKind in ("actual", "typed-failure"):
            with (
                self.subTest(ReceiptKind=ReceiptKind),
                TemporaryDirectory() as Directory,
            ):
                Root = Path(Directory)
                RequestedStrategy = RoutingStrategy.Default.value
                PolicyIdentity = BuildPolicyProvenanceRecord(
                    RequestedStrategy
                )
                ActualIdentity = CompleteActualRoutingIdentity(
                    PolicyIdentity,
                    RequestedStrategy,
                )
                ActualIdentity["FallbackUsed"] = 0
                if ReceiptKind == "actual":
                    Receipt = BuildSyntheticRunReceipt(
                        PolicyIdentity,
                        RequestedStrategy,
                        RunName="integer-zero-actual",
                        Status="FAILED",
                        Accepted=False,
                        ActualRoutingIdentity=ActualIdentity,
                        RoutingIdentityChecks={
                            "ActualFallbackDisabled": True,
                        },
                    )
                else:
                    FailureIdentity = CompleteFailureRoutingIdentity(
                        PolicyIdentity,
                        RequestedStrategy,
                    )
                    FailureIdentity["FallbackUsed"] = 0
                    Receipt = BuildSyntheticRunReceipt(
                        PolicyIdentity,
                        RequestedStrategy,
                        RunName="integer-zero-failure",
                        Status="FAILED",
                        Accepted=False,
                        FailureRoutingIdentity=FailureIdentity,
                        RoutingIdentityChecks={
                            "FailureStrategyMatches": True,
                        },
                    )

                Run = SummarizeSyntheticRunReceipt(
                    Root,
                    Receipt,
                    RequestedStrategy,
                )

                Identity = (
                    Run["ActualRoutingIdentity"]
                    if ReceiptKind == "actual"
                    else Run["FailureRoutingIdentity"]
                )
                self.assertIsNotNone(Identity)
                self.assertEqual(Identity["FallbackUsed"], 0)
                self.assertIs(type(Identity["FallbackUsed"]), int)
                self.assertFalse(Run["RoutingIdentityConsistent"])

    def test_run_projection_preserves_explicit_evaluator_rejection(
        self,
    ) -> None:
        """A raw match cannot overwrite an applicable failed evaluator check."""
        for ReceiptKind in ("actual", "typed-failure"):
            with (
                self.subTest(ReceiptKind=ReceiptKind),
                TemporaryDirectory() as Directory,
            ):
                Root = Path(Directory)
                RequestedStrategy = RoutingStrategy.Default.value
                PolicyIdentity = BuildPolicyProvenanceRecord(
                    RequestedStrategy
                )
                ReceiptArguments: dict[str, object]
                if ReceiptKind == "actual":
                    ReceiptArguments = {
                        "ActualRoutingIdentity": CompleteActualRoutingIdentity(
                            PolicyIdentity,
                            RequestedStrategy,
                        ),
                        "RoutingIdentityChecks": {
                            "ActualFallbackDisabled": False,
                        },
                    }
                else:
                    ReceiptArguments = {
                        "FailureRoutingIdentity": CompleteFailureRoutingIdentity(
                            PolicyIdentity,
                            RequestedStrategy,
                        ),
                        "RoutingIdentityChecks": {
                            "FailureStrategyMatches": False,
                        },
                    }
                Receipt = BuildSyntheticRunReceipt(
                    PolicyIdentity,
                    RequestedStrategy,
                    RunName=f"evaluator-rejected-{ReceiptKind}",
                    Status="FAILED",
                    Accepted=False,
                    **ReceiptArguments,
                )

                Run = SummarizeSyntheticRunReceipt(
                    Root,
                    Receipt,
                    RequestedStrategy,
                )

                self.assertFalse(Run["RoutingIdentityConsistent"])

    def test_evaluator_zero_fallback_rejection_reaches_public_snapshot(
        self,
    ) -> None:
        """The strict evaluator and public snapshot agree that zero is invalid."""
        RequestedStrategy = RoutingStrategy.Default.value
        with TemporaryDirectory() as Directory:
            Root = Path(Directory)
            (
                ManifestPath,
                FailurePath,
                Manifest,
                CurrentSource,
                CurrentRuntime,
            ) = BuildPublicAcceptanceSnapshotFixture(Root, RequestedStrategy)
            PolicyIdentity = Manifest["SourceProvenance"]["Policy"]
            RunName = "evaluator-integer-zero"
            Artifacts = BuildRunArtifacts(Root / "EvaluatorRun", RunName)
            Artifacts["RunDirectory"].mkdir(parents=True)
            Artifacts["PhysicalDesign"].write_text(
                json.dumps({
                    "Strategy": {
                        "Requested": RequestedStrategy,
                        "Used": RequestedStrategy,
                        "FallbackUsed": 0,
                    },
                    "Policy": PolicyIdentity["Snapshot"],
                }),
                encoding="utf-8",
            )
            Command = [
                "python",
                "Main.py",
                "--routing-strategy",
                RequestedStrategy,
            ]
            Evaluation, _Evidence = EvaluateRun(
                Case=AcceptanceCase(
                    Name="EvaluatorReceipt",
                    ExamplePath=Root / "EvaluatorReceipt.sv",
                    TopModule="EvaluatorReceipt",
                    RequiredRuns=1,
                    TruthTableRows=1,
                    RuntimeCeilingSeconds=2.0,
                    PublicationReserveSeconds=1.0,
                ),
                Process=AcceptanceCommandResult(1, "", "", 0.0),
                Artifacts=Artifacts,
                ExpectedSeed=0,
                ExpectedPolicyVersion=str(PolicyIdentity["PolicyVersion"]),
                ExpectedRoutingStrategy=RequestedStrategy,
                ExpectedPolicyProvenance=PolicyIdentity,
                ExpectedCommand=Command,
            )
            self.assertFalse(
                Evaluation["Observed"]["RoutingIdentityChecks"][
                    "ActualFallbackDisabled"
                ]
            )
            Receipt = {
                "RunName": RunName,
                "Status": "FAILED",
                "Accepted": False,
                "Command": Command,
                "Evaluation": Evaluation,
            }
            Run = SnapshotTool.BuildAcceptanceRunSummary(
                Receipt,
                RequestedStrategy=RequestedStrategy,
                ResolvedUsedStrategy=RequestedStrategy,
                PolicyRecord=PolicyIdentity,
                PolicyIdentity=SnapshotTool.BuildPolicySnapshotIdentity(
                    PolicyIdentity["Snapshot"]
                ),
            )

        self.assertIs(type(Run["ActualRoutingIdentity"]["FallbackUsed"]), int)
        self.assertFalse(Run["RoutingIdentityConsistent"])

    def test_run_projection_leaves_absent_receipts_unknown(self) -> None:
        """Timeout, skipped, and planned runs cannot infer actual identity."""
        with TemporaryDirectory() as Directory:
            Root = Path(Directory)
            PolicyIdentity = BuildPolicyProvenanceRecord("default")
            Receipts = [
                BuildSyntheticRunReceipt(
                    PolicyIdentity,
                    RoutingStrategy.Default.value,
                    RunName=RunName,
                    Status=Status,
                    Accepted=False,
                    RoutingIdentityChecks={"ActualArtifactPresent": False},
                    TimedOut=RunName == "timed-out",
                    ReturnCode=124 if RunName == "timed-out" else 1,
                )
                for RunName, Status in (
                    ("timed-out", "FAILED"),
                    ("skipped", "SKIPPED"),
                    ("planned", "PLANNED"),
                )
            ]
            Runs = [
                SummarizeSyntheticRunReceipt(Root, Receipt)
                for Receipt in Receipts
            ]

        for Run, Receipt in zip(Runs, Receipts, strict=True):
            self.assertEqual(Run["RunName"], Receipt["RunName"])
            self.assertEqual(Run["Status"], Receipt["Status"])
            self.assertFalse(Run["Accepted"])
            self.assertEqual(
                Run["CommandRoutingStrategy"],
                RoutingStrategy.Default.value,
            )
            self.assertEqual(
                Run["ConfiguredRoutingIdentity"],
                Receipt["Evaluation"]["Observed"][
                    "ConfiguredRoutingIdentity"
                ],
            )
            self.assertIsNone(Run["ActualRoutingIdentity"])
            self.assertIsNone(Run["FailureRoutingIdentity"])
            self.assertEqual(
                Run["RoutingIdentityChecks"],
                Receipt["Evaluation"]["Observed"][
                    "RoutingIdentityChecks"
                ],
            )
            self.assertEqual(Run["RoutingIdentityConsistent"], None)

    def test_run_projection_rejects_claimed_but_missing_receipt(self) -> None:
        """An artifact-presence claim requires its corresponding receipt."""
        for PresenceFlag in (
            "ActualArtifactPresent",
            "FailureArtifactPresent",
        ):
            with self.subTest(PresenceFlag=PresenceFlag), TemporaryDirectory() as Directory:
                Root = Path(Directory)
                PolicyIdentity = BuildPolicyProvenanceRecord("default")
                Receipt = BuildSyntheticRunReceipt(
                    PolicyIdentity,
                    RoutingStrategy.Default.value,
                    RunName=f"missing-{PresenceFlag}",
                    Status="FAILED",
                    Accepted=False,
                    RoutingIdentityChecks={PresenceFlag: True},
                )
                Run = SummarizeSyntheticRunReceipt(Root, Receipt)
                self.assertIsNone(Run["ActualRoutingIdentity"])
                self.assertIsNone(Run["FailureRoutingIdentity"])
                self.assertTrue(
                    Run["RoutingIdentityChecks"][PresenceFlag]
                )
                self.assertFalse(Run["RoutingIdentityConsistent"])

    def test_run_projection_typed_failure_is_consistent_without_actual_success(
        self,
    ) -> None:
        """A matching typed failure is evidence, but not an actual success."""
        RequestedStrategy = (
            RoutingStrategy.RoutingAwarePlacementAccess.value
        )
        with TemporaryDirectory() as Directory:
            Root = Path(Directory)
            PolicyIdentity = BuildPolicyProvenanceRecord(RequestedStrategy)
            Receipt = BuildSyntheticRunReceipt(
                PolicyIdentity,
                RequestedStrategy,
                RunName="typed-failure",
                Status="FAILED",
                Accepted=False,
                FailureRoutingIdentity=CompleteFailureRoutingIdentity(
                    PolicyIdentity,
                    RequestedStrategy,
                ),
            )
            Run = SummarizeSyntheticRunReceipt(
                Root,
                Receipt,
                RequestedStrategy,
            )

        self.assertFalse(Run["Accepted"])
        self.assertIsNone(Run["ActualRoutingIdentity"])
        self.assertEqual(
            Run["ConfiguredRoutingIdentity"],
            Receipt["Evaluation"]["Observed"][
                "ConfiguredRoutingIdentity"
            ],
        )
        self.assertEqual(
            Run["FailureRoutingIdentity"],
            Receipt["Evaluation"]["Observed"]["FailureRoutingIdentity"],
        )
        self.assertEqual(
            Run["RoutingIdentityChecks"],
            Receipt["Evaluation"]["Observed"]["RoutingIdentityChecks"],
        )
        self.assertTrue(Run["RoutingIdentityConsistent"])

    def test_failure_projection_requires_exact_effective_deadline_policy(
        self,
    ) -> None:
        PolicyRecord = BuildPolicyProvenanceRecord("default")
        Deadline = 13.0
        ValidReceipt = BuildSyntheticRunReceipt(
            PolicyRecord,
            "default",
            RunName="FullAdderRun1",
            Status="FAILED",
            Accepted=False,
            RoutingDeadlineSeconds=Deadline,
            FailureRoutingIdentity=CompleteFailureRoutingIdentity(
                PolicyRecord,
                "default",
                Deadline,
            ),
        )

        Valid = SnapshotTool.BuildAcceptanceRunSummary(
            ValidReceipt,
            RequestedStrategy="default",
            ResolvedUsedStrategy="default",
            PolicyRecord=PolicyRecord,
            PolicyIdentity=SnapshotTool.BuildPolicySnapshotIdentity(
                PolicyRecord["Snapshot"]
            ),
        )
        self.assertTrue(Valid["RoutingIdentityConsistent"])
        self.assertEqual(Valid["FailureRoutingDeadlineSeconds"], Deadline)
        self.assertNotEqual(
            Valid["FailureRoutingIdentity"]["PolicyIdentity"],
            SnapshotTool.BuildPolicySnapshotIdentity(PolicyRecord["Snapshot"]),
        )

        WrongPolicy = deepcopy(ValidReceipt)
        WrongPolicy["Evaluation"]["Observed"]["FailureRoutingIdentity"] = (
            CompleteFailureRoutingIdentity(PolicyRecord, "default", 120.0)
        )
        WrongCommandDeadline = deepcopy(ValidReceipt)
        Command = WrongCommandDeadline["Command"]
        Command[Command.index("--routing-deadline-seconds") + 1] = "12"
        WrongProcessDeadline = deepcopy(ValidReceipt)
        WrongProcessDeadline["Evaluation"]["Process"][
            "RequestedRoutingDeadlineSeconds"
        ] = 12.0
        PreservedEvaluatorRejection = deepcopy(ValidReceipt)
        PreservedEvaluatorRejection["Evaluation"]["Observed"][
            "RoutingIdentityChecks"
        ] = {"FailurePolicyIdentityMatches": False}

        for Name, Receipt in (
            ("wrong-policy", WrongPolicy),
            ("wrong-command-deadline", WrongCommandDeadline),
            ("wrong-process-deadline", WrongProcessDeadline),
            ("evaluator-rejection", PreservedEvaluatorRejection),
        ):
            with self.subTest(Name=Name):
                Result = SnapshotTool.BuildAcceptanceRunSummary(
                    Receipt,
                    RequestedStrategy="default",
                    ResolvedUsedStrategy="default",
                    PolicyRecord=PolicyRecord,
                    PolicyIdentity=SnapshotTool.BuildPolicySnapshotIdentity(
                        PolicyRecord["Snapshot"]
                    ),
                )
                self.assertFalse(Result["RoutingIdentityConsistent"])

    def test_cla4_failure_summary_uses_the_effective_case_deadline(self) -> None:
        with TemporaryDirectory() as Directory:
            Root = Path(Directory)
            (
                ManifestPath,
                FailurePath,
                Manifest,
                CurrentSource,
                CurrentRuntime,
            ) = BuildPublicAcceptanceSnapshotFixture(Root)
            Failure = json.loads(FailurePath.read_text(encoding="utf-8"))
            Failure["Policy"]["RuntimeBudgetSeconds"] = 120.0
            Failure["Policy"]["AdaptiveRouting"][
                "MaximumRuntimeSeconds"
            ] = 120.0
            FailurePath.write_text(json.dumps(Failure), encoding="utf-8")
            WriteSealedAcceptanceFixture(
                ManifestPath,
                Manifest,
                (FailurePath,),
            )
            Cla4Failure = SnapshotTool.SummarizeCla4Failure(FailurePath)

            with self.assertRaisesRegex(
                ValueError,
                "CLA4 failure routing identities disagree",
            ):
                SnapshotTool.SummarizeAcceptanceManifest(
                    ManifestPath,
                    Cla4Failure,
                    CurrentSource,
                    CurrentRuntime,
                )

    def testSiblingSuccessArtifactsAreRejectedAsMixedEvidence(self) -> None:
        for SuccessName in (
            "CLA4.litematic",
            "CLA4.PhysicalDesign.json",
            "CLA4.TruthTable.txt",
        ):
            with self.subTest(SuccessName=SuccessName):
                with TemporaryDirectory() as Directory:
                    Root = Path(Directory)
                    FailurePath = WriteSyntheticFailure(Root)
                    (Root / SuccessName).write_bytes(b"stale-success")

                    with self.assertRaisesRegex(
                        ValueError,
                        "mixed/stale CLA4 evidence",
                    ):
                        SnapshotTool.SummarizeCla4Failure(FailurePath)

    def testMultipleAndWrongNandDiagramsAreRejected(self) -> None:
        with TemporaryDirectory() as Directory:
            Root = Path(Directory)
            FirstPath = Root / "First.Nand.json"
            SecondPath = Root / "Second.Nand.json"
            FirstPath.write_text(
                json.dumps(BuildValidNandPayload()),
                encoding="utf-8",
            )
            SecondPath.write_text(
                json.dumps(BuildValidNandPayload()),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "exactly one"):
                SnapshotTool.SummarizeNandDiagram((FirstPath, SecondPath))

            WrongPayload = BuildValidNandPayload()
            WrongPayload["Module"] = "FullAdder"
            FirstPath.write_text(
                json.dumps(WrongPayload),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ValueError,
                "not CarryLookaheadAdder4",
            ):
                SnapshotTool.SummarizeNandDiagram((FirstPath,))

    def testCapturePublishesFreshBundleWithoutChangingRepositoryStatus(self) -> None:
        StatusBefore = SnapshotTool.RunGit(
            RepositoryRoot,
            ("status", "--porcelain=v1", "-z", "--untracked-files=all"),
        )
        with TemporaryDirectory() as Directory:
            Root = Path(Directory)
            DiagramPath = Root / "CLA4.Nand.json"
            FailurePath = WriteSyntheticFailure(Root)
            DiagramPath.write_text(
                json.dumps(BuildValidNandPayload()),
                encoding="utf-8",
            )
            Configuration = SnapshotTool.SnapshotConfiguration(
                RepositoryRoot=RepositoryRoot,
                OutputRoot=Root / "Snapshots",
                CapturedAtUtc=datetime(
                    2026,
                    8,
                    28,
                    0,
                    5,
                    0,
                    tzinfo=timezone.utc,
                ),
                Cla4FailurePath=FailurePath,
                ArtifactPaths=(DiagramPath,),
            )

            Snapshot = SnapshotTool.BuildRoutingDesignSnapshot(Configuration)
            OutputPath = SnapshotTool.WriteSnapshotStaged(
                Configuration,
                Snapshot,
            )

            self.assertTrue((OutputPath / "Snapshot.json").is_file())
            self.assertTrue((OutputPath / "Snapshot.md").is_file())
            self.assertTrue((OutputPath / "SHA256SUMS").is_file())
            self.assertEqual(
                SnapshotTool.Sha256File(
                    OutputPath / "Artifacts" / FailurePath.name
                ),
                SnapshotTool.Sha256File(FailurePath),
            )
            with self.assertRaises(FileExistsError):
                SnapshotTool.WriteSnapshotStaged(Configuration, Snapshot)

        StatusAfter = SnapshotTool.RunGit(
            RepositoryRoot,
            ("status", "--porcelain=v1", "-z", "--untracked-files=all"),
        )
        self.assertEqual(StatusAfter, StatusBefore)

    def testPublicationRejectsSnapshotMutationAfterIdentityBuild(self) -> None:
        with TemporaryDirectory() as Directory:
            Root = Path(Directory)
            FailurePath = WriteSyntheticFailure(Root)
            Configuration = SnapshotTool.SnapshotConfiguration(
                RepositoryRoot=RepositoryRoot,
                OutputRoot=Root / "Snapshots",
                CapturedAtUtc=datetime(
                    2026,
                    8,
                    28,
                    0,
                    6,
                    0,
                    tzinfo=timezone.utc,
                ),
                Cla4FailurePath=FailurePath,
            )
            Snapshot = SnapshotTool.BuildRoutingDesignSnapshot(Configuration)
            Snapshot["Cla4Failure"]["Detail"] = "mutated after hashing"

            with self.assertRaisesRegex(
                RuntimeError,
                "^snapshot evidence identity mismatch$",
            ):
                SnapshotTool.WriteSnapshotStaged(Configuration, Snapshot)

            self.assertFalse(Configuration.OutputRoot.exists())

    def testExplicitMissingArtifactIsHardFailure(self) -> None:
        with TemporaryDirectory() as Directory:
            MissingPath = Path(Directory) / "Missing.json"
            with self.assertRaises(FileNotFoundError):
                SnapshotTool.BuildArtifactManifest((MissingPath,))


if __name__ == "__main__":
    unittest.main()
