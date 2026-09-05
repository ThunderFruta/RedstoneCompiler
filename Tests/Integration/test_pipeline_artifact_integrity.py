import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
from time import monotonic
import unittest
from unittest.mock import patch

from Compilation.Pipeline import (
    BuildRoutingFailureArtifactSnapshot,
    BuildSuccessRouterReliability,
    ClearStaleSuccessArtifacts,
    CompileSvToLitematic,
    ObsoleteArtifactPaths,
    PublishSuccessArtifacts,
    SuccessArtifactPaths,
    WriteRoutingFailureArtifact,
)
from PhysicalDesign.Contracts.Failures import RoutingFailure, RoutingFailureReason, RoutingStageError
from PhysicalDesign.Policy import RoutingStrategy


class PipelineArtifactIntegrityTests(unittest.TestCase):
    def testFailureArtifactSnapshotDoesNotDuplicateAggregateEvidence(
        self,
    ) -> None:
        Failure = RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="TrackAssignment",
            Diagnostics={
                "ConflictGraph": {"ConflictSignals": ["A", "B"]},
                "PlacementAttempts": [{"CandidateId": "Placement-001"}],
                "EscalationHistory": [{"Action": "retry"}],
                "AdaptiveEscalationHistory": [{"Action": "legacy-retry"}],
            },
        )

        Snapshot = BuildRoutingFailureArtifactSnapshot(Failure)

        self.assertEqual(
            Snapshot["Diagnostics"],
            {"ConflictGraph": {"ConflictSignals": ["A", "B"]}},
        )

    def testFailureAndSuccessReliabilityUseParallelEvidence(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Directory = Path(DirectoryValue)
            InputPath = Directory / "Design.sv"
            InputPath.write_text("module Design; endmodule\n", encoding="utf-8")
            OutputPath = Directory / "Design.litematic"
            DiagramPath = Directory / "Design.Nand.json"
            DiagramPath.write_text("{}\n", encoding="utf-8")
            CheckpointPath = Directory / "Candidate.json"
            CheckpointPath.write_text("{}\n", encoding="utf-8")
            ResourceGraph = {
                "Version": "resource-graph-v1",
                "NodeCount": 12,
                "EdgeCount": 16,
            }
            Evidence = {
                "PlacementFingerprint": "placement-fingerprint",
                "ResourceGraph": ResourceGraph,
                "CandidateFingerprint": "candidate-fingerprint",
                "ConflictFingerprint": "conflict-fingerprint",
                "EffectiveWorkFingerprint": "work-fingerprint",
                "NativeBatching": {
                    "PortalRequestCount": 8,
                    "RouteTreeRequestCount": 5,
                },
                "WorkTelemetry": {
                    "PortalCompletedWork": 7,
                    "RouteTreeCompletedWork": 3,
                },
                "RustAssignmentUsed": True,
                "RustAssignmentExpansionLimit": 100,
                "RustAssignmentExpansions": 42,
                "PartialArtifactPaths": {
                    "CandidateCheckpoint": CheckpointPath,
                },
            }
            Failure = RoutingFailure(
                Reason=RoutingFailureReason.TrackAssignmentConflict,
                Stage="TrackAssignment",
                AffectedNets=("A", "B"),
                Resources=("wire:4",),
                Locations=((4, 1, 2),),
                Detail="no capacity-one assignment",
                Diagnostics=Evidence,
            )
            FailurePath = WriteRoutingFailureArtifact(
                OutputPath=OutputPath,
                RequestedStrategy=RoutingStrategy.Default,
                Failure=Failure,
                StartedAt=monotonic(),
                InputPath=InputPath,
                DiagramPath=DiagramPath,
                Workdir=Directory / "Frontend",
                TopModule="Design",
            )
            FailureDocument = json.loads(
                FailurePath.read_text(encoding="utf-8")
            )

        self.assertEqual(FailureDocument["SchemaVersion"], "routing-failure-v1")
        self.assertEqual(FailureDocument["OutputIdentity"]["Name"], "Design.litematic")
        self.assertEqual(FailureDocument["OutputIdentity"]["Path"], str(OutputPath.resolve()))
        self.assertEqual(
            FailureDocument["Technology"]["TechnologyVersion"],
            "redstone-routing-v1",
        )
        self.assertEqual(
            FailureDocument["Affected"],
            {
                "Nets": ["A", "B"],
                "Resources": ["wire:4"],
                "Locations": [[4, 1, 2]],
            },
        )
        self.assertEqual(
            FailureDocument["Fingerprints"]["Placement"],
            "placement-fingerprint",
        )
        self.assertIsNotNone(
            FailureDocument["Fingerprints"]["ResourceGraph"]
        )
        self.assertEqual(
            FailureDocument["NativeWork"]["CompletedWork"],
            {
                "PortalCompletedWork": 7,
                "RouteTreeCompletedWork": 3,
            },
        )
        self.assertIn("Diagram", FailureDocument["PartialArtifactPaths"])
        self.assertIn(
            "CandidateCheckpoint",
            FailureDocument["PartialArtifactPaths"],
        )
        self.assertEqual(
            FailureDocument["Reproduction"]["Input"]["Path"],
            str(InputPath.resolve()),
        )
        self.assertIn("Sha256", FailureDocument["Reproduction"]["Input"])
        self.assertIn("Command", FailureDocument["Reproduction"])
        self.assertIn("PythonVersion", FailureDocument["Environment"])

        SuccessEvidence = {
            **Evidence,
            "RoutingResourceGraph": ResourceGraph,
            "SelectedPlacementCandidate": {
                "PlacementFingerprint": "placement-fingerprint",
            },
        }
        SuccessDocument = BuildSuccessRouterReliability(SuccessEvidence)
        self.assertEqual(
            SuccessDocument["Fingerprints"],
            FailureDocument["Fingerprints"],
        )
        self.assertEqual(
            SuccessDocument["NativeWork"],
            FailureDocument["NativeWork"],
        )

    def testDefaultFailureArtifactNamesAuthoritativePolicy(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            OutputPath = Path(DirectoryValue) / "Default.litematic"
            FailurePath = WriteRoutingFailureArtifact(
                OutputPath=OutputPath,
                RequestedStrategy=RoutingStrategy.Default,
                Failure=RoutingFailure(
                    Reason=RoutingFailureReason.DetailedSearchExhausted,
                    Stage="AuthoritativeRouter",
                    Detail="controlled failure",
                ),
                StartedAt=monotonic(),
            )
            Document = json.loads(FailurePath.read_text(encoding="utf-8"))

        self.assertEqual(Document["Strategy"], {
            "Requested": "default",
            "Used": "default",
            "FallbackUsed": False,
        })
        self.assertEqual(
            Document["Policy"]["PolicyVersion"],
            "physical-design-v17-routing-aware-placement-access",
        )

    def testTypedRoutingFailureEscapesAndLeavesOnlyFailureArtifact(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Directory = Path(DirectoryValue)
            OutputPath = Directory / "Failed.litematic"
            Failure = RoutingFailure(
                Reason=RoutingFailureReason.NoBoundaryEscape,
                Stage="PortalGeneration",
                AffectedNets=("A",),
                Detail="controlled routing failure",
            )
            with patch(
                "Compilation.Pipeline.PlaceAndRoutePcb",
                side_effect=RoutingStageError(Failure),
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "PortalGeneration:NoBoundaryEscape",
                ):
                    CompileSvToLitematic(
                        InputPath=Path("Assets/Examples/FullAdder.sv"),
                        OutputPath=OutputPath,
                        DiagramPath=Directory / "Failed.Nand.json",
                        TopModule="FullAdder",
                        Workdir=Directory / "Frontend",
                        RoutingDeadlineSeconds=0.25,
                    )

            FailurePath = OutputPath.with_suffix(".RoutingFailure.json")
            Document = json.loads(FailurePath.read_text(encoding="utf-8"))
            self.assertEqual(Document["SchemaVersion"], "routing-failure-v1")
            self.assertEqual(Document["Failure"]["Reason"], "NoBoundaryEscape")
            self.assertEqual(Document["Policy"]["RuntimeBudgetSeconds"], 0.25)
            self.assertEqual(
                Document["Policy"]["AdaptiveRouting"][
                    "MaximumRuntimeSeconds"
                ],
                0.25,
            )
            self.assertFalse(any(
                ArtifactPath.exists()
                for ArtifactPath in SuccessArtifactPaths(OutputPath).values()
            ))

    def testStaleSuccessArtifactsAreClearedTogether(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Directory = Path(DirectoryValue)
            OutputPath = Directory / "Design.litematic"
            for ArtifactPath in SuccessArtifactPaths(OutputPath).values():
                ArtifactPath.write_text("stale", encoding="utf-8")
            for ArtifactPath in ObsoleteArtifactPaths(OutputPath):
                ArtifactPath.write_text("obsolete", encoding="utf-8")
            DiagramPath = Directory / "Design.Nand.json"
            DiagramPath.write_text("partial", encoding="utf-8")

            Removed = ClearStaleSuccessArtifacts(OutputPath)

            self.assertEqual(len(Removed), 5)
            self.assertTrue(DiagramPath.exists())
            self.assertFalse(any(
                ArtifactPath.exists()
                for ArtifactPath in SuccessArtifactPaths(OutputPath).values()
            ))

    def testFailedRerunCannotRetainPriorSuccessArtifacts(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Directory = Path(DirectoryValue)
            InputPath = Directory / "Design.sv"
            InputPath.write_text("module Design; endmodule\n", encoding="utf-8")
            OutputPath = Directory / "Design.litematic"
            for ArtifactPath in SuccessArtifactPaths(OutputPath).values():
                ArtifactPath.write_text("prior-success", encoding="utf-8")

            with patch(
                "Compilation.Pipeline.Sv.ParseSvToNetlist",
                side_effect=ValueError("new compile failed"),
            ):
                with self.assertRaisesRegex(ValueError, "new compile failed"):
                    CompileSvToLitematic(
                        InputPath=InputPath,
                        OutputPath=OutputPath,
                        DiagramPath=Directory / "Design.Nand.json",
                        Workdir=Directory / "Frontend",
                    )

            self.assertFalse(any(
                ArtifactPath.exists()
                for ArtifactPath in SuccessArtifactPaths(OutputPath).values()
            ))

    def testFailedSchematicPublicationCannotLeaveSuccessMetadata(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            OutputPath = Path(DirectoryValue) / "Design.litematic"
            for ArtifactPath in SuccessArtifactPaths(OutputPath).values():
                ArtifactPath.write_text("stale", encoding="utf-8")

            def FailWriter(*_Arguments, **_Options):
                self.assertFalse(
                    SuccessArtifactPaths(OutputPath)["PhysicalDesign"].exists()
                )
                raise OSError("schematic write failed")

            with patch(
                "Compilation.Pipeline.SchemWriter.WriteLitematic",
                side_effect=FailWriter,
            ):
                with self.assertRaisesRegex(OSError, "schematic write failed"):
                    PublishSuccessArtifacts(
                        Routed=object(),
                        Rendered=object(),
                        PhysicalDesignDocument={"Status": "success"},
                        OutputPath=OutputPath,
                    )

            self.assertFalse(any(
                ArtifactPath.exists()
                for ArtifactPath in SuccessArtifactPaths(OutputPath).values()
            ))

    def testSuccessArtifactsPublishOnlyAfterStagingCompletes(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            OutputPath = Path(DirectoryValue) / "Design.litematic"
            Events = []

            def WriteSchematic(_Routed, *, OutputPath, Build):
                self.assertIsNotNone(Build)
                self.assertFalse(
                    SuccessArtifactPaths(
                        Path(DirectoryValue) / "Design.litematic"
                    )["PhysicalDesign"].exists()
                )
                Events.append("schematic")
                OutputPath.write_text("schematic", encoding="utf-8")

            with patch(
                "Compilation.Pipeline.SchemWriter.WriteLitematic",
                side_effect=WriteSchematic,
            ):
                PhysicalDesignPath = PublishSuccessArtifacts(
                    Routed=object(),
                    Rendered=SimpleNamespace(),
                    PhysicalDesignDocument={"Status": "success"},
                    OutputPath=OutputPath,
                )

            self.assertEqual(Events, ["schematic"])
            self.assertTrue(OutputPath.exists())
            self.assertEqual(
                json.loads(PhysicalDesignPath.read_text(encoding="utf-8")),
                {"Status": "success"},
            )

    def testPublicationUsesServerUpdatedLitematicForCanonicalOutput(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            OutputPath = Path(DirectoryValue) / "Design.litematic"
            Events = []

            def WriteStatic(_Routed, *, OutputPath, Build):
                self.assertIsNotNone(Build)
                self.assertEqual(OutputPath.name, "Design.Static.litematic")
                Events.append("static")
                OutputPath.write_text("static", encoding="utf-8")

            def Capture(*, Supervisor, Fixture, SourcePath, OutputPath):
                self.assertIs(Supervisor, SnapshotSupervisor)
                self.assertEqual(Fixture, {"Fixture": "design"})
                self.assertTrue(SourcePath.is_file())
                self.assertEqual(
                    SourcePath.read_text(encoding="utf-8"),
                    "static",
                )
                self.assertEqual(OutputPath.name, "Design.litematic")
                Events.append("snapshot")
                OutputPath.write_text("server-updated", encoding="utf-8")
                return SimpleNamespace(
                    RequestedPositionCount=42,
                    ObservedBlockCount=24,
                    WorldReadRequests=3,
                    InputCountSetToZero=2,
                    SnapshotReadPasses=2,
                    InputZeroGameTime=100,
                    FirstObservedGameTime=150,
                    LastObservedGameTime=151,
                )

            def WriteFixture(OutputPath, _Fixture):
                OutputPath.write_text("fixture", encoding="utf-8")

            SnapshotSupervisor = object()
            PhysicalDesignDocument = {"RunSummary": {}}
            with (
                patch(
                    "Compilation.Pipeline.SchemWriter.WriteLitematic",
                    side_effect=WriteStatic,
                ),
                patch(
                    "Compilation.Pipeline.CaptureServerUpdatedLitematic",
                    side_effect=Capture,
                ),
                patch(
                    "Compilation.Pipeline.WritePhysicalFixture",
                    side_effect=WriteFixture,
                ),
            ):
                PhysicalDesignPath = PublishSuccessArtifacts(
                    Routed=object(),
                    Rendered=SimpleNamespace(),
                    PhysicalDesignDocument=PhysicalDesignDocument,
                    PhysicalFixture={"Fixture": "design"},
                    FabricServerSnapshotSupervisor=SnapshotSupervisor,
                    OutputPath=OutputPath,
                )

            Document = json.loads(PhysicalDesignPath.read_text(encoding="utf-8"))
            Snapshot = Document["RunSummary"]["FabricServerSnapshot"]
            self.assertEqual(Events, ["static", "snapshot"])
            self.assertEqual(
                OutputPath.read_text(encoding="utf-8"),
                "server-updated",
            )
            self.assertEqual(
                Snapshot,
                {
                    "Path": str(OutputPath.resolve()),
                    "State": "all-inputs-zero-server-updated",
                    "RequestedPositionCount": 42,
                    "ObservedBlockCount": 24,
                    "WorldReadRequests": 3,
                    "InputCountSetToZero": 2,
                    "SnapshotReadPasses": 2,
                    "InputZeroGameTime": 100,
                    "FirstObservedGameTime": 150,
                    "LastObservedGameTime": 151,
                },
            )

    def testFailedServerSnapshotCannotPublishStaticSchematic(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            OutputPath = Path(DirectoryValue) / "Design.litematic"
            for ArtifactPath in SuccessArtifactPaths(OutputPath).values():
                ArtifactPath.write_text("stale", encoding="utf-8")

            def WriteStatic(_Routed, *, OutputPath, Build):
                self.assertIsNotNone(Build)
                OutputPath.write_text("static", encoding="utf-8")

            with (
                patch(
                    "Compilation.Pipeline.SchemWriter.WriteLitematic",
                    side_effect=WriteStatic,
                ),
                patch(
                    "Compilation.Pipeline.CaptureServerUpdatedLitematic",
                    side_effect=RuntimeError("server snapshot failed"),
                ),
                self.assertRaisesRegex(RuntimeError, "server snapshot failed"),
            ):
                PublishSuccessArtifacts(
                    Routed=object(),
                    Rendered=SimpleNamespace(),
                    PhysicalDesignDocument={"RunSummary": {}},
                    PhysicalFixture={"Fixture": "design"},
                    FabricServerSnapshotSupervisor=object(),
                    OutputPath=OutputPath,
                )

            self.assertFalse(any(
                ArtifactPath.exists()
                for ArtifactPath in SuccessArtifactPaths(OutputPath).values()
            ))


if __name__ == "__main__":
    unittest.main()
