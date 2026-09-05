"""Focused contracts for timestamped routing-design evidence snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


RepositoryRoot = Path(__file__).resolve().parents[2]
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


def BuildSyntheticFailurePayload(
    *,
    CheckoutRoot: str = "/arbitrary/checkout",
    OutputRoot: str = "/arbitrary/output",
) -> dict[str, object]:
    """Return a minimal typed CLA4 structural failure with budget remaining."""
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
        "Policy": {"PolicyVersion": "physical-design-v16-test"},
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
            Paths = {Key: str(Root / Name) for Key, Name in {
                "RoutingFailure": "CLA4.RoutingFailure.json", "Schematic": "CLA4.litematic",
                "PhysicalDesign": "CLA4.PhysicalDesign.json", "TruthTable": "CLA4.TruthTable.txt",
            }.items()}
            Payload = {
                "SchemaVersion": SnapshotTool.AcceptanceManifestSchemaVersion,
                "SourceState": {"Revision": "baseline"},
                "SourceProvenance": {"ExpectedPolicyVersion": "baseline-policy", "BenchmarkInputs": {"CarryLookaheadAdder4": {"Sha256": "input"}}},
                "Runs": [{"Circuit": "CarryLookaheadAdder4", "RunName": "CLA4", "Accepted": False, "ArtifactPaths": Paths, "Evaluation": {"Process": {"TimedOut": True, "ReturnCode": 124, "WallRuntimeSeconds": 125.0}}}],
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

    def testPythonDefinitionMetricsIncludeNestedQualifiedSpans(self) -> None:
        Source = """class Outer:
    @staticmethod
    def Build():
        def Inner():
            return 1
        return Inner()

async def Run():
    return None
"""

        Definitions = list(SnapshotTool.IterPythonDefinitions(
            Source,
            "Demo.py",
        ))

        ByName = {Value["QualifiedName"]: Value for Value in Definitions}
        self.assertEqual(ByName["Outer"]["Kind"], "Class")
        self.assertEqual(ByName["Outer.Build"]["Kind"], "Function")
        self.assertEqual(ByName["Outer.Build.Inner"]["PythonAstSpanLines"], 2)
        self.assertEqual(ByName["Run"]["Kind"], "AsyncFunction")

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

            WrongPath = Root / "WrongAcceptance.json"
            WrongPath.write_text(json.dumps({
                "SchemaVersion": SnapshotTool.AcceptanceManifestSchemaVersion,
                "SourceProvenanceStable": True,
                "SourceState": {"Revision": "wrong-revision"},
                "SourceProvenance": {
                    "Git": {"Revision": "wrong-revision"},
                },
            }), encoding="utf-8")
            with self.assertRaisesRegex(
                ValueError,
                "source revisions disagree",
            ):
                SnapshotTool.SummarizeAcceptanceManifest(
                    WrongPath,
                    FailureSummary,
                    {},
                    {},
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

    def testNandDiagramSummaryCountsKinds(self) -> None:
        with TemporaryDirectory() as Directory:
            DiagramPath = Path(Directory) / "CLA4.Nand.json"
            DiagramPath.write_text(
                json.dumps(BuildValidNandPayload()),
                encoding="utf-8",
            )

            Summary = SnapshotTool.SummarizeNandDiagram((DiagramPath,))

        self.assertEqual(Summary["GateCount"], 4)
        self.assertEqual(Summary["GateCountsByKind"]["NAND"], 1)
        self.assertEqual(Summary["InputCount"], 2)

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

    def testSourceManifestIsDeterministicAndResolvesInventory(self) -> None:
        Source = SnapshotTool.BuildRoutingSourceManifest(RepositoryRoot)
        Repeated = SnapshotTool.BuildRoutingSourceManifest(RepositoryRoot)
        Files = Source["Files"]
        LargestDefinitions = Source["Metrics"]["LargestPythonDefinitions"]

        self.assertEqual(Source, Repeated)
        self.assertEqual(Source["FileCount"], len(Files))
        self.assertEqual(
            [Value["Path"] for Value in Files],
            sorted(Value["Path"] for Value in Files),
        )
        self.assertEqual(
            len({Value["Path"] for Value in Files}),
            len(Files),
        )
        for Value in Files:
            self.assertTrue((RepositoryRoot / Value["Path"]).is_file())
            self.assertGreaterEqual(Value["PhysicalLines"], 1)
        self.assertTrue(LargestDefinitions)
        self.assertEqual(
            LargestDefinitions,
            sorted(
                LargestDefinitions,
                key=lambda Value: (
                    -Value["PythonAstSpanLines"],
                    Value["Path"],
                    Value["Line"],
                ),
            )[:len(LargestDefinitions)],
        )

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
