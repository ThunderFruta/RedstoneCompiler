import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
from time import monotonic
import unittest
from unittest.mock import patch

from Compiler.Pipeline import (
    BuildRoutingFailureArtifactSnapshot,
    BuildSuccessRouterReliability,
    ClearStaleSuccessArtifacts,
    CompileSvToLitematic,
    ObsoleteArtifactPaths,
    PublishSuccessArtifacts,
    SuccessArtifactPaths,
    WriteRoutingFailureArtifact,
)
from Compiler.Routing.Failures import (
    RoutingFailure,
    RoutingFailureReason,
    RoutingStageError,
)
from Compiler.Routing.Policy import RoutingStrategy


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
            DotPath = Directory / "Design.Nand.dot"
            DotPath.write_text("digraph Design {}\n", encoding="utf-8")
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
                DotPath=DotPath,
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
        self.assertIn("Dot", FailureDocument["PartialArtifactPaths"])
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
            "physical-design-v16-reconvergent-access",
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
                "Compiler.Pipeline.PlaceAndRoutePcb",
                side_effect=RoutingStageError(Failure),
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "PortalGeneration:NoBoundaryEscape",
                ):
                    CompileSvToLitematic(
                        InputPath=Path("Examples/FullAdder.sv"),
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

            self.assertEqual(len(Removed), 3)
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
                "Compiler.Pipeline.Sv.ParseSvToNetlist",
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
                "Compiler.Pipeline.SchemWriter.WriteLitematic",
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
                "Compiler.Pipeline.SchemWriter.WriteLitematic",
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


if __name__ == "__main__":
    unittest.main()
