from dataclasses import replace
import unittest
from unittest.mock import patch

from Compiler.Ir.Models import Gate, GateKind, ModuleIR, NetlistIR
from Compiler.Placement.Pcb import (
    AssignBoundaryDemandSides,
    BoundaryDemandRecord,
    BuildBoundaryCapacityRecords,
    BuildConnectivityClusters,
    BuildRelocationClusterSet,
    BuildLegalBoundaryEscapeSlots,
    BuildPinAlignedPackedCluster,
    BuildTopologicalLevels,
    EvaluateHardBoundaryFeasibility,
    HardBoundaryFeasibility,
    OptimizeClusterSlots,
    PlacePcbGraph,
    PrioritizeRelocationClusters,
    RelocateClusterSlots,
    ValidateHardBoundaryFeasibility,
)
from Compiler.Placement.PcbFlow import BuildPlacementGenerationPlan
from Compiler.Routing.Policy import LocalFirstPhysicalDesignPolicy
from Compiler.Routing.Failures import (
    RoutingFailureReason,
    RoutingStageError,
)
from Compiler.Routing.ResourceGraph import RoutingResourceGraph
from Compiler.Routing.Technology import DefaultRedstoneRoutingTechnology


class PlacementBoundaryFeasibilityTests(unittest.TestCase):
    def Demand(
        self,
        Signal: str,
        *,
        Lanes: int = 1,
        Side: str = "East",
    ) -> BoundaryDemandRecord:
        return BoundaryDemandRecord(
            Signal=Signal,
            UnresolvedTargets=Lanes,
            RequiredPortalSlots=Lanes,
            RequiredCorridorLanes=Lanes,
            PreferredBoundarySide=Side,
        )

    def SyntheticNetlist(self) -> NetlistIR:
        Module = ModuleIR(
            Name="SyntheticBoundaryGraph",
            Inputs=["Left", "Right"],
            Outputs=["Result"],
            Gates=[
                Gate("InputLeft", GateKind.INPUT, ["Left"], []),
                Gate("InputRight", GateKind.INPUT, ["Right"], []),
                Gate(
                    "LogicNode",
                    GateKind.NAND,
                    ["Result"],
                    ["Left", "Right"],
                ),
                Gate("OutputResult", GateKind.OUTPUT, [], ["Result"]),
            ],
        )
        return NetlistIR(
            Top=Module.Name,
            Modules={Module.Name: Module},
        )

    def ClusteredNetlist(self) -> NetlistIR:
        Module = ModuleIR(
            Name="ClusteredBoundaryGraph",
            Inputs=["Left", "Right"],
            Outputs=["Result"],
            Gates=[
                Gate("InputLeft", GateKind.INPUT, ["Left"], []),
                Gate("InputRight", GateKind.INPUT, ["Right"], []),
                Gate("N0", GateKind.NAND, ["S0"], ["Left", "Right"]),
                Gate("N1", GateKind.NAND, ["S1"], ["S0", "Left"]),
                Gate("N2", GateKind.NAND, ["Result"], ["S1", "Right"]),
                Gate("OutputResult", GateKind.OUTPUT, [], ["Result"]),
            ],
        )
        return NetlistIR(
            Top=Module.Name,
            Modules={Module.Name: Module},
        )

    def testPlacementConstructionPublishesPeriodicWorkChecks(self) -> None:
        Phases = []

        def StopAtClusterPlacement(Diagnostics):
            Phases.append(Diagnostics["Phase"])
            if Diagnostics["Phase"] == "cluster-placement":
                raise RuntimeError("placement slice expired")

        with self.assertRaisesRegex(RuntimeError, "placement slice expired"):
            PlacePcbGraph(
                self.SyntheticNetlist(),
                RoutingSpacing=6,
                PlacementPolicy=LocalFirstPhysicalDesignPolicy.Placement,
                PackingPolicy=replace(
                    LocalFirstPhysicalDesignPolicy.NandPacking,
                    Enabled=False,
                ),
                ClusterPolicy=LocalFirstPhysicalDesignPolicy.Clustering,
                WorkCheck=StopAtClusterPlacement,
            )

        self.assertEqual(Phases[0], "start")
        self.assertIn("connectivity-clusters", Phases)
        self.assertEqual(Phases[-1], "cluster-placement")

    def testExpensivePlacementHelpersPublishStoppableInnerWork(self) -> None:
        Module = self.ClusteredNetlist().Modules["ClusteredBoundaryGraph"]

        def StopAt(ExpectedPhase):
            def Check(Diagnostics):
                if Diagnostics["Phase"] == ExpectedPhase:
                    raise RuntimeError(f"stopped at {ExpectedPhase}")

            return Check

        with self.assertRaisesRegex(
            RuntimeError,
            "connectivity-cluster-pair",
        ):
            BuildConnectivityClusters(
                Module,
                WorkCheck=StopAt("connectivity-cluster-pair"),
            )

        Clusters = (("N0",), ("N1",), ("N2",))
        with self.assertRaisesRegex(
            RuntimeError,
            "cluster-slot-optimization",
        ):
            OptimizeClusterSlots(
                Module,
                Clusters,
                BuildTopologicalLevels(Module),
                WorkCheck=StopAt("cluster-slot-optimization"),
            )

        InternalByName = {
            GateValue.Name: GateValue
            for GateValue in Module.Gates
            if GateValue.Kind == GateKind.NAND
        }
        with self.assertRaisesRegex(RuntimeError, "graph-beam-gate"):
            BuildPinAlignedPackedCluster(
                ("N0", "N1", "N2"),
                InternalByName,
                BeamWidth=4,
                WorkCheck=StopAt("graph-beam-gate"),
            )

    def testRequiredSignalWithoutEscapeIsRejected(self) -> None:
        Result = EvaluateHardBoundaryFeasibility(
            4,
            (self.Demand("Blocked"), self.Demand("Open")),
            {
                "Blocked": set(),
                "Open": {(8, 1, 2)},
            },
        )

        self.assertFalse(Result.IsFeasible)
        self.assertEqual(
            Result.RejectionReasons,
            ("NoBoundaryEscape:Cluster=4:Signal=Blocked",),
        )
        with self.assertRaisesRegex(
            RoutingStageError,
            "NoBoundaryEscape",
        ) as Context:
            ValidateHardBoundaryFeasibility(Result)
        self.assertEqual(
            Context.exception.Failure.Reason,
            RoutingFailureReason.NoBoundaryEscape,
        )
        self.assertEqual(Context.exception.Failure.AffectedNets, ("Blocked",))

    def testPhysicalAccessWithNoLegalPrimitiveHasNoEscapeSlot(self) -> None:
        Anchor = (0, 1, 0)
        BlockedNeighbors = frozenset(
            DefaultRedstoneRoutingTechnology.NeighborPositions(Anchor)
        )
        ResourceGraph = RoutingResourceGraph(
            ActualBlocks=BlockedNeighbors,
            ElectricalBlocks=frozenset(),
            SolidBlocks=BlockedNeighbors,
        )

        Slots = BuildLegalBoundaryEscapeSlots(
            {"Required"},
            {"Required": {Anchor}},
            ResourceGraph,
            {},
        )
        Result = EvaluateHardBoundaryFeasibility(
            1,
            (self.Demand("Required"),),
            Slots,
        )

        self.assertEqual(Slots, {"Required": set()})
        self.assertFalse(Result.IsFeasible)

    def testSharedOnlyEscapeProvesHardEntranceCapacityFailure(self) -> None:
        SharedSlot = (3, 1, 7)
        Result = EvaluateHardBoundaryFeasibility(
            2,
            (self.Demand("First"), self.Demand("Second")),
            {
                "First": {SharedSlot},
                "Second": {SharedSlot},
            },
        )

        self.assertFalse(Result.IsFeasible)
        self.assertEqual(Result.UniqueLegalSlotCount, 1)
        self.assertTrue(any(
            Reason.startswith("HardEntranceCapacityExceeded:")
            for Reason in Result.RejectionReasons
        ))
        with self.assertRaises(RoutingStageError) as Context:
            ValidateHardBoundaryFeasibility(Result)
        self.assertEqual(
            Context.exception.Failure.Reason,
            RoutingFailureReason.ClusterEntranceBudgetExceeded,
        )

    def testSoftPreferredSideOverflowRemainsFeasibleAndRankable(self) -> None:
        Demands = (
            self.Demand("First", Side="East"),
            self.Demand("Second", Side="East"),
        )
        Capacity = BuildBoundaryCapacityRecords(
            Demands,
            {
                "West": 1,
                "East": 1,
                "North": 1,
                "South": 1,
            },
            {
                "West": 0,
                "East": 2,
                "North": 0,
                "South": 0,
            },
        )
        Result = EvaluateHardBoundaryFeasibility(
            0,
            Demands,
            {
                "First": {(5, 1, 0)},
                "Second": {(5, 1, 3)},
            },
        )

        ValidateHardBoundaryFeasibility(Result)
        self.assertTrue(Result.IsFeasible)
        self.assertEqual(sum(Record.Overflow for Record in Capacity), 1)
        self.assertEqual(
            next(
                Record.LegalPortalSlots
                for Record in Capacity
                if Record.BoundarySide == "East"
            ),
            2,
        )

    def testBoundarySideAssignmentUsesLegalCapacityBeforeOverflow(self) -> None:
        Assigned = AssignBoundaryDemandSides(
            (
                self.Demand("First", Side="East"),
                self.Demand("Second", Side="East"),
            ),
            {
                "First": {(0, 1, 5), (10, 1, 5)},
                "Second": {(10, 1, 6)},
            },
            (0, 10, 0, 10),
            {"West": 1, "East": 1, "North": 0, "South": 0},
        )

        self.assertEqual(
            {Record.Signal: Record.PreferredBoundarySide for Record in Assigned},
            {"First": "West", "Second": "East"},
        )
        Capacity = BuildBoundaryCapacityRecords(
            Assigned,
            {"West": 1, "East": 1, "North": 0, "South": 0},
            {"West": 1, "East": 1, "North": 0, "South": 0},
        )
        self.assertEqual(sum(Record.Overflow for Record in Capacity), 0)

    def testRoutingConflictMapsToTouchedClusters(self) -> None:
        Module = self.ClusteredNetlist().Modules["ClusteredBoundaryGraph"]
        self.assertEqual(
            BuildRelocationClusterSet(
                Module,
                (("N0",), ("N1",), ("N2",)),
                frozenset({"S0"}),
            ),
            frozenset({0, 1}),
        )

    def testNonstackedConflictClustersMoveToDedicatedColumns(self) -> None:
        Assignment, ColumnCount = RelocateClusterSlots(
            {0: (0, 0), 1: (1, 0), 2: (0, 1)},
            2,
            frozenset({0, 2}),
            frozenset({0}),
        )

        self.assertEqual(Assignment[0], (0, 0))
        self.assertEqual(Assignment[1], (1, 0))
        self.assertEqual(Assignment[2], (2, 0))
        self.assertEqual(ColumnCount, 3)

    def testRelocationClustersAreRankedByConflictSignalCoverage(self) -> None:
        Module = self.ClusteredNetlist().Modules["ClusteredBoundaryGraph"]
        self.assertEqual(
            PrioritizeRelocationClusters(
                Module,
                (("N0",), ("N1",), ("N2",)),
                frozenset({"S0", "S1"}),
            ),
            (1, 0, 2),
        )

    def testLegalPortalScarcityLimitsSoftCorridorCapacity(self) -> None:
        Capacity = BuildBoundaryCapacityRecords(
            (
                self.Demand("First", Side="East"),
                self.Demand("Second", Side="East"),
            ),
            {
                "West": 2,
                "East": 2,
                "North": 2,
                "South": 2,
            },
            {
                "West": 0,
                "East": 1,
                "North": 0,
                "South": 0,
            },
        )
        East = next(
            Record
            for Record in Capacity
            if Record.BoundarySide == "East"
        )

        self.assertEqual(East.LegalPortalSlots, 1)
        self.assertEqual(East.LegalCorridorLanes, 1)
        self.assertEqual(East.Overflow, 1)

    def testRejectedConstructionDoesNotLeakPlacementOrLocalClaims(self) -> None:
        Netlist = self.SyntheticNetlist()
        GateSnapshot = tuple(
            (
                GateValue.Name,
                GateValue.Kind,
                tuple(GateValue.Inputs),
                tuple(GateValue.Outputs),
            )
            for GateValue in Netlist.Modules[Netlist.Top].Gates
        )
        Rejected = HardBoundaryFeasibility(
            ClusterId=0,
            RequiredSignals=("Left",),
            LegalEscapeSlotsBySignal=(("Left", ()),),
            MatchedEntrances=(),
            UniqueLegalSlotCount=0,
            RejectionReasons=(
                "NoBoundaryEscape:Cluster=0:Signal=Left",
            ),
        )
        Arguments = {
            "RoutingSpacing": 6,
            "PlacementPolicy": LocalFirstPhysicalDesignPolicy.Placement,
            "PackingPolicy": replace(
                LocalFirstPhysicalDesignPolicy.NandPacking,
                GraphBeamEnabled=False,
                EnableVerticalClusterStacking=False,
            ),
            "ClusterPolicy": LocalFirstPhysicalDesignPolicy.Clustering,
            "MaximumBoundaryTerminals": 16,
            "MaximumEntrancesPerSignal": 2,
        }

        with patch(
            "Compiler.Placement.Pcb.EvaluateHardBoundaryFeasibility",
            return_value=Rejected,
        ):
            with self.assertRaisesRegex(ValueError, "NoBoundaryEscape"):
                PlacePcbGraph(Netlist, **Arguments)

        self.assertEqual(
            tuple(
                (
                    GateValue.Name,
                    GateValue.Kind,
                    tuple(GateValue.Inputs),
                    tuple(GateValue.Outputs),
                )
                for GateValue in Netlist.Modules[Netlist.Top].Gates
            ),
            GateSnapshot,
        )
        First = PlacePcbGraph(Netlist, **Arguments)
        Second = PlacePcbGraph(Netlist, **Arguments)
        self.assertEqual(
            tuple(
                (GateValue.Name, GateValue.X, GateValue.Y, GateValue.Z)
                for GateValue in First.Placed.PlacedGates
            ),
            tuple(
                (GateValue.Name, GateValue.X, GateValue.Y, GateValue.Z)
                for GateValue in Second.Placed.PlacedGates
            ),
        )
        self.assertEqual(
            tuple(
                (Claim.Signal, Claim.ClusterId, tuple(sorted(Claim.Nodes)))
                for Claim in First.Placed.LocalRouteClaims
            ),
            tuple(
                (Claim.Signal, Claim.ClusterId, tuple(sorted(Claim.Nodes)))
                for Claim in Second.Placed.LocalRouteClaims
            ),
        )

    def testEveryPlacementRecipeRollsBackRejectedCandidateState(self) -> None:
        Netlist = self.SyntheticNetlist()
        GateSnapshot = tuple(
            (
                GateValue.Name,
                GateValue.Kind,
                tuple(GateValue.Inputs),
                tuple(GateValue.Outputs),
            )
            for GateValue in Netlist.Modules[Netlist.Top].Gates
        )
        Plan = BuildPlacementGenerationPlan(LocalFirstPhysicalDesignPolicy)
        Requests = (*Plan.PrimaryRequests, *Plan.DeferredRequests)

        self.assertEqual(
            {Request.SourceGenerator for Request in Requests},
            {
                "row-beam",
                "row-beam-conflict-relocation",
                "unpacked",
                "row-beam-direct-only",
                "unpacked-spacing-7",
                "unpacked-spacing-8",
                "unpacked-configured-spacing",
                "configured-packing",
                "graph-beam-direct-only",
                "spacing-4",
                "spacing-5",
                "spacing-7",
                "spacing-8",
            },
        )
        for Request in Requests:
            with self.subTest(SourceGenerator=Request.SourceGenerator):
                Arguments = {
                    "RoutingSpacing": Request.RoutingSpacing,
                    "PlacementPolicy": LocalFirstPhysicalDesignPolicy.Placement,
                    "PackingPolicy": Request.PackingPolicy,
                    "ClusterPolicy": LocalFirstPhysicalDesignPolicy.Clustering,
                    "MaximumBoundaryTerminals": 16,
                    "MaximumEntrancesPerSignal": 2,
                }
                with patch(
                    "Compiler.Placement.Pcb.BuildBoundaryCapacityRecords",
                    side_effect=ValueError("forced transactional rejection"),
                ):
                    with self.assertRaisesRegex(
                        ValueError,
                        "forced transactional rejection",
                    ):
                        PlacePcbGraph(Netlist, **Arguments)

                self.assertEqual(
                    tuple(
                        (
                            GateValue.Name,
                            GateValue.Kind,
                            tuple(GateValue.Inputs),
                            tuple(GateValue.Outputs),
                        )
                        for GateValue in Netlist.Modules[Netlist.Top].Gates
                    ),
                    GateSnapshot,
                )
                First = PlacePcbGraph(Netlist, **Arguments)
                Second = PlacePcbGraph(Netlist, **Arguments)
                self.assertEqual(
                    tuple(
                        (
                            GateValue.Name,
                            GateValue.X,
                            GateValue.Y,
                            GateValue.Z,
                        )
                        for GateValue in First.Placed.PlacedGates
                    ),
                    tuple(
                        (
                            GateValue.Name,
                            GateValue.X,
                            GateValue.Y,
                            GateValue.Z,
                        )
                        for GateValue in Second.Placed.PlacedGates
                    ),
                )
                self.assertEqual(
                    tuple(
                        (
                            Claim.Signal,
                            Claim.ClusterId,
                            tuple(sorted(Claim.Nodes)),
                        )
                        for Claim in First.Placed.LocalRouteClaims
                    ),
                    tuple(
                        (
                            Claim.Signal,
                            Claim.ClusterId,
                            tuple(sorted(Claim.Nodes)),
                        )
                        for Claim in Second.Placed.LocalRouteClaims
                    ),
                )


if __name__ == "__main__":
    unittest.main()
