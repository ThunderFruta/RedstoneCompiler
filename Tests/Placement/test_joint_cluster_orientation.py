"""Focused coverage for joint packed-cluster orientation placement."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from SVDecoder import Sv
from Compiler.Placement.Geometry import BuildPlacedGate, PlacedDesign
from Compiler.Placement.Core.Clustering import TransformPackedClusterLayout
from Compiler.Placement.Core.Commit import PlacePcbGraph
from Compiler.Routing.Actions.Geometry import ValidatePlacedCellElectricalIsolation
from Compiler.Routing.Policy import LocalFirstPhysicalDesignPolicy
from Compiler.Synthesis.LogicOptimization import OptimizeLogic
from Compiler.Synthesis.NandTransform import ToNandOnly


class JointClusterOrientationTests(unittest.TestCase):
    def testEightRigidTransformsPreserveLegalClusterGeometry(self) -> None:
        Names = ("First", "Second")
        Positions = {"First": (0, 0), "Second": (8, 5)}
        Rotations = {"First": 0, "Second": 90}
        Mirrors = {"First": False, "Second": True}
        Variants = [
            TransformPackedClusterLayout(
                Names,
                Positions,
                Rotations,
                Mirrors,
                Rotation,
                MirrorX,
            )
            for Rotation in (0, 90, 180, 270)
            for MirrorX in (False, True)
        ]
        self.assertEqual(len({
            tuple(sorted(Variant.Positions.items()))
            + tuple(sorted(Variant.Rotations.items()))
            + tuple(sorted(Variant.Mirrors.items()))
            for Variant in Variants
        }), 8)

    def testExactTemplateTransformsPreserveNandGeometry(self) -> None:
        with TemporaryDirectory() as Directory:
            Netlist = ToNandOnly(
                OptimizeLogic(
                    Sv.ParseSvToNetlist(
                        InputPath=Path("Examples/FullAdder.sv"),
                        TopModule="FullAdder",
                        Workdir=Path(Directory),
                    )
                )
            )
        Module = Netlist.Modules[Netlist.Top]
        Names = tuple(Gate.Name for Gate in Module.Gates if Gate.Kind.value == "NAND")[:2]
        GatesByName = {Gate.Name: Gate for Gate in Module.Gates}
        Positions = {Names[0]: (0, 0), Names[1]: (8, 6)}
        Rotations = {Names[0]: 0, Names[1]: 90}
        Mirrors = {Names[0]: False, Names[1]: True}
        Variants = [
            TransformPackedClusterLayout(
                Names,
                Positions,
                Rotations,
                Mirrors,
                Rotation,
                MirrorX,
                GatesByName=GatesByName,
            )
            for Rotation in (0, 90, 180, 270)
            for MirrorX in (False, True)
        ]
        self.assertTrue(all(Variant.IsLegal for Variant in Variants))
        self.assertTrue(all(Variant.ActualGeometry for Variant in Variants))
        self.assertTrue(all(Variant.ElectricalGeometry for Variant in Variants))

    def testRca4BuildsJointOrientationStatesForReusedClusters(self) -> None:
        with TemporaryDirectory() as Directory:
            Netlist = ToNandOnly(
                OptimizeLogic(
                    Sv.ParseSvToNetlist(
                        InputPath=Path("Examples/RippleCarryAdder4.sv"),
                        TopModule="RippleCarryAdder4",
                        Workdir=Path(Directory),
                    )
                )
            )
        Policy = LocalFirstPhysicalDesignPolicy
        Placement = PlacePcbGraph(
            Netlist,
            RoutingSpacing=Policy.Placement.RoutingSpacing,
            PlacementPolicy=Policy.Placement,
            PackingPolicy=Policy.NandPacking,
            ClusterPolicy=Policy.Clustering,
            MaximumBoundaryTerminals=Policy.Organization.MaximumClusterEntrances,
            MaximumEntrancesPerSignal=(
                Policy.Organization.MaximumClusterEntrancesPerSignal
            ),
        )
        self.assertEqual(Placement.PackedClusters[1].ReusedFromClusterId, 0)
        Diagnostics = Placement.Placed.LocalRouteDiagnostics or {}
        Joint = Diagnostics["__JointClusterPlacement__"]
        self.assertTrue(Joint["Enabled"])
        self.assertGreater(Joint["CandidateCount"], 8)
        self.assertEqual(len(Joint["SelectedTransforms"]), 4)
        self.assertEqual(
            len(Joint["RetainedStates"]),
            Policy.NandPacking.RetainedJointPlacementCandidates,
        )
        ValidatePlacedCellElectricalIsolation(Placement.Placed)

    def testRca4RetainedStatesCanTransformReuseIndependently(self) -> None:
        with TemporaryDirectory() as Directory:
            Netlist = ToNandOnly(
                OptimizeLogic(
                    Sv.ParseSvToNetlist(
                        InputPath=Path("Examples/RippleCarryAdder4.sv"),
                        TopModule="RippleCarryAdder4",
                        Workdir=Path(Directory),
                    )
                )
            )
        Policy = LocalFirstPhysicalDesignPolicy
        Placements = [
            PlacePcbGraph(
                Netlist,
                RoutingSpacing=Policy.Placement.RoutingSpacing,
                PlacementPolicy=Policy.Placement,
                PackingPolicy=Policy.NandPacking,
                ClusterPolicy=Policy.Clustering,
                MaximumBoundaryTerminals=(
                    Policy.Organization.MaximumClusterEntrances
                ),
                MaximumEntrancesPerSignal=(
                    Policy.Organization.MaximumClusterEntrancesPerSignal
                ),
                JointPlacementCandidateIndex=Index,
            )
            for Index in range(
                Policy.NandPacking.RetainedJointPlacementCandidates
            )
        ]
        TransformStates = {
            tuple(
                (Cluster.OrientationRotation, Cluster.OrientationMirrorX)
                for Cluster in Placement.PackedClusters
            )
            for Placement in Placements
        }
        self.assertEqual(
            len(TransformStates),
            Policy.NandPacking.RetainedJointPlacementCandidates,
        )
        self.assertTrue(any(
            Cluster.OrientationRotation != Placement.PackedClusters[0].OrientationRotation
            or Cluster.OrientationMirrorX
            != Placement.PackedClusters[0].OrientationMirrorX
            for Placement in Placements
            for Cluster in Placement.PackedClusters[1:]
        ))
        for Placement in Placements:
            Joint = Placement.Placed.LocalRouteDiagnostics[
                "__JointClusterPlacement__"
            ]
            self.assertIn("RetainedStates", Joint)
            ValidatePlacedCellElectricalIsolation(Placement.Placed)


if __name__ == "__main__":
    unittest.main()
