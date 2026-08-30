from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import Compiler.Placement.Core.Channels as ChannelsModule
import Compiler.Placement.Core.Clusters as ClustersModule
import Compiler.Placement.Core.MandatoryAccess as MandatoryAccessModule
import Compiler.Placement.Core.Repair as RepairModule
import Compiler.Placement.Core.Cache as PlacementCache
from Compiler.Ir.Models import Gate, GateKind, ModuleIR, NetlistIR
from Compiler.Ir.ComponentGraph import ComponentGraph, TopologyComponent
from Compiler.Placement.Geometry import BuildPlacedGate, PlacedDesign
from Compiler.Placement.Core.Channels import (
    AssignBoundaryDemandSides,
    BoundaryEscapeCandidate,
    BoundaryDemandRecord,
    BuildBoundaryCapacityRecords,
    BuildClusterBoundaryBundles,
    BuildClusterBoundaryLeaseRequests,
    BuildClusterInterfaceTopology,
    BuildLegalBoundaryEscapeSlots,
    ClusterBoundaryCorridorKey,
    CutDrivenClusterRefinementProfile,
    EvaluateCutBoundaryEscapeFeasibility,
    EvaluateHardBoundaryFeasibility,
    HardBoundaryFeasibility,
    InterClusterBoundaryDemand,
    ScoreClusterBoundaryContracts,
    ScoreClusterInterfacePlacement,
    ScoreClusterInterfaceFacingMismatches,
    ScoreHigherOrderPhysicalBankDemand,
    ValidateHardBoundaryFeasibility,
)
from Compiler.Placement.Core.Clustering import (
    BuildConnectivityClusters,
    BuildTopologicalLevels,
    OptimizeClusterSlots,
)
from Compiler.Placement.Core.Clusters import (
    BuildBoundedInterClusterRoutingChannel,
    BuildBoundedInterClusterRoutingDeck,
    ClusterLayoutVariant,
    PackedNandCluster,
    PcbPlacement,
)
from Compiler.Placement.Core.Commit import PlacePcbGraph
from Compiler.Placement.Core.Compactness import BuildPinAlignedPackedCluster
from Compiler.Placement.Core.Constraints import (
    BuildAssignmentCutHigherOrderSignalSet,
    BuildEffectiveStructuredRelocationFocus,
    SelectPlacementConstraintWorkingSet,
    PlacementAssignmentConstraintSet,
)
from Compiler.Placement.Core.Costs import (
    BuildInterClusterBoundaryDemand,
    BuildInterClusterGapPlan,
)
from Compiler.Placement.Core.MandatoryAccess import (
    MandatoryAccessConflictProfile,
    OrderExactStatesForMandatoryAccessCommit,
    RepairPackedClusterAccess,
)
from Compiler.Placement.Core.Repair import (
    BuildTransactionalClusterEndpointRepair,
    RankTransactionalRepairClusterSelections,
    SelectTransactionalRepairClusterSelections,
)
from Compiler.Placement.Core.Search import (
    BuildJointPortfolioBaseRelocationControls,
    BuildRelocationClusterSet,
    JointPlacementSearchRetentionLimit,
    OptimizeJointClusterPlacement,
    PrioritizeRelocationClusters,
    RelocateClusterSlots,
    SelectFocusedConstraintComponentClusters,
    SelectFocusedCutEpochClusters,
    SelectFocusedTopologyFrontierClusters,
    SelectInternalPinBankGeometrySignals,
    ShouldReleasePartialLocalTreeBeforeSearch,
)
from Compiler.Placement.Flow.Demand import BuildPlacementGenerationPlan
from Compiler.Placement.Flow.Feedback import BuildPlacementFingerprint
from Compiler.Placement.Flow.Portfolios import ShouldRejectCutBoundaryEscapePlacement
from Compiler.Routing.Policy import LocalFirstPhysicalDesignPolicy
from Compiler.Routing.Failures import (
    RoutingAssignmentCut,
    RoutingAssignmentCutClassification,
    RoutingFailure,
    RoutingFailureReason,
    RoutingStageError,
)
from Compiler.Routing.ResourceGraph import (
    RoutingResourceClaims,
    RoutingResourceGraph,
)
from Compiler.Routing.Technology import DefaultRedstoneRoutingTechnology


class PlacementBoundaryFeasibilityTests(unittest.TestCase):
    def testInterfaceFeasibilityDoublesExactScreenBound(self) -> None:
        self.assertEqual(
            JointPlacementSearchRetentionLimit(
                AvailableStateCount=64,
                PublishedCandidateCount=6,
                EnableClusterInterfacePlacementFeasibility=True,
            ),
            12,
        )
        self.assertEqual(
            JointPlacementSearchRetentionLimit(
                AvailableStateCount=64,
                PublishedCandidateCount=6,
                EnableClusterInterfacePlacementFeasibility=False,
            ),
            6,
        )

    @staticmethod
    def BuildChannelPlacement(
        Coordinates: tuple[tuple[int, int], ...],
        *,
        NamePrefix: str = "Gate",
        SignalPrefix: str = "Signal",
    ) -> PcbPlacement:
        Gates = [
            Gate(
                f"{NamePrefix}{Index}",
                GateKind.NAND,
                [f"{SignalPrefix}{Index + 1}"],
                [f"{SignalPrefix}{Index}", f"Bias{Index}"],
            )
            for Index in range(len(Coordinates))
        ]
        Module = ModuleIR(Name="ChannelFixture", Gates=Gates)
        PlacedGates = [
            BuildPlacedGate(
                GateValue,
                Coordinates[Index][0],
                1,
                Coordinates[Index][1],
                0,
                False,
            )
            for Index, GateValue in enumerate(Gates)
        ]
        PackedClusters = tuple(
            PackedNandCluster(
                ClusterId=Index,
                MemberNands=(GateValue.Name,),
                BoundarySignals=(
                    f"{SignalPrefix}{Index}",
                    f"{SignalPrefix}{Index + 1}",
                ),
                InternalSignals=(),
                RelativePlacements={
                    GateValue.Name: (0, 0, 0, False)
                },
                DirectConnections=(),
                StructuralSignature="single-nand",
            )
            for Index, GateValue in enumerate(Gates)
        )
        Requests = tuple(
            ChannelsModule.ClusterBoundaryLeaseRequest(
                SourceCluster=Index,
                TargetCluster=Index + 1,
                Signal=f"{SignalPrefix}{Index + 1}",
                SourceBoundarySide="east",
                TargetBoundarySide="west",
                SourceTerminal=(
                    Coordinates[Index][0] + 1,
                    2,
                    Coordinates[Index][1],
                ),
                TargetTerminals=((
                    Coordinates[Index + 1][0] - 1,
                    2,
                    Coordinates[Index + 1][1],
                ),),
                CompletePinAccess=True,
            )
            for Index in range(len(Coordinates) - 1)
        )
        return PcbPlacement(
            Placed=PlacedDesign(
                Module=Module,
                PlacedGates=PlacedGates,
                ClusterBoundaryLeaseRequests=Requests,
            ),
            Clusters=tuple(
                (GateValue.Name,) for GateValue in Gates
            ),
            SignalOrder=tuple(
                f"{SignalPrefix}{Index}"
                for Index in range(len(Coordinates) + 1)
            ),
            LayerCount=3,
            PackedClusters=PackedClusters,
            ClusterBoundaryLeaseRequests=Requests,
        )

    def testBoundedInterClusterChannelBuildsStraightThreeLayerLane(
        self,
    ) -> None:
        Source = self.BuildChannelPlacement(((0, 0), (12, 0)))
        with patch.object(
            ClustersModule,
            "BuildPlacedCellGeometry",
            return_value=(set(), set(), set()),
        ):
            Result = BuildBoundedInterClusterRoutingChannel(Source)
        Channel = Result.InterClusterRoutingChannel
        self.assertIsNotNone(Channel)
        assert Channel is not None
        self.assertEqual(len(Channel.AffectedClusters), 2)
        self.assertEqual(len(Channel.InsertedBoundaryStrips), 1)
        self.assertEqual(len(Channel.Lanes), 3)
        self.assertEqual(
            {Lane.Layer for Lane in Channel.Lanes},
            {0, 1, 2},
        )
        self.assertEqual(Channel.TrackPitch, 3)
        self.assertTrue(all(Lane.IngressNodes for Lane in Channel.Lanes))
        for Lane in Channel.Lanes:
            self.assertEqual(
                Lane.PhysicalClaims.WireCells,
                frozenset(Lane.Cells),
            )
            self.assertEqual(
                Lane.PhysicalClaims.SupportCells,
                frozenset(
                    (X, Y - 1, Z) for X, Y, Z in Lane.Cells
                ),
            )
            self.assertTrue(Lane.PhysicalClaims.ElectricalCells)

    def testBoundedInterClusterChannelClearanceMovesLaneIntoTranslatedGap(
        self,
    ) -> None:
        Source = self.BuildChannelPlacement(((0, 0), (12, 0)))
        with patch.object(
            ClustersModule,
            "BuildPlacedCellGeometry",
            return_value=(set(), set(), set()),
        ):
            Compact = BuildBoundedInterClusterRoutingChannel(Source)
            Cleared = BuildBoundedInterClusterRoutingChannel(
                Source,
                ChannelClearanceTracks=1,
            )
        CompactChannel = Compact.InterClusterRoutingChannel
        ClearedChannel = Cleared.InterClusterRoutingChannel
        assert CompactChannel is not None
        assert ClearedChannel is not None
        self.assertEqual(ClearedChannel.ChannelClearanceTracks, 1)
        self.assertNotEqual(
            CompactChannel.ChannelFingerprint,
            ClearedChannel.ChannelFingerprint,
        )
        self.assertGreater(
            max(
                abs(Delta[0]) + abs(Delta[2])
                for _Cluster, Delta in ClearedChannel.ClusterTranslations
            ),
            max(
                abs(Delta[0]) + abs(Delta[2])
                for _Cluster, Delta in CompactChannel.ClusterTranslations
            ),
        )
        self.assertTrue(all(
            Cell not in {
                (Gate.X, Gate.Y, Gate.Z)
                for Gate in Cleared.Placed.PlacedGates
            }
            for Lane in ClearedChannel.Lanes
            for Cell in Lane.Cells
        ))

    def testBoundedInterClusterChannelBuildsTwoStripLShape(
        self,
    ) -> None:
        Source = self.BuildChannelPlacement(
            ((0, 0), (12, 0), (12, 12))
        )
        with patch.object(
            ClustersModule,
            "BuildPlacedCellGeometry",
            return_value=(set(), set(), set()),
        ):
            Result = BuildBoundedInterClusterRoutingChannel(Source)
        Channel = Result.InterClusterRoutingChannel
        assert Channel is not None
        self.assertEqual(len(Channel.AffectedClusters), 3)
        self.assertEqual(len(Channel.InsertedBoundaryStrips), 2)
        self.assertEqual(
            {Strip[0] for Strip in Channel.InsertedBoundaryStrips},
            {"X", "Z"},
        )
        self.assertEqual(len(Channel.Lanes), 6)
        self.assertLessEqual(
            max(
                abs(Value)
                for _Cluster, Delta in Channel.ClusterTranslations
                for Value in (Delta[0], Delta[2])
            ),
            3,
        )

    def testBoundedInterClusterChannelFingerprintIgnoresNames(
        self,
    ) -> None:
        First = self.BuildChannelPlacement(((0, 0), (12, 0)))
        Second = self.BuildChannelPlacement(
            ((0, 0), (12, 0)),
            NamePrefix="Renamed",
            SignalPrefix="Wire",
        )
        with patch.object(
            ClustersModule,
            "BuildPlacedCellGeometry",
            return_value=(set(), set(), set()),
        ):
            FirstResult = BuildBoundedInterClusterRoutingChannel(First)
            SecondResult = BuildBoundedInterClusterRoutingChannel(Second)
        assert FirstResult.InterClusterRoutingChannel is not None
        assert SecondResult.InterClusterRoutingChannel is not None
        self.assertEqual(
            FirstResult.InterClusterRoutingChannel.ChannelFingerprint,
            SecondResult.InterClusterRoutingChannel.ChannelFingerprint,
        )

    def testBoundedInterClusterChannelRejectsBlockedLaneGeometry(
        self,
    ) -> None:
        Source = self.BuildChannelPlacement(((0, 0), (12, 0)))
        Blocked = {
            (X, Y, Z)
            for X in range(-20, 30)
            for Y in (1, 3, 5)
            for Z in range(-20, 30)
        }
        with (
            patch.object(
                ClustersModule,
                "BuildPlacedCellGeometry",
                return_value=(Blocked, set(), set()),
            ),
            patch.object(
                ClustersModule,
                "ValidatePlacedCellElectricalIsolation",
            ),
            self.assertRaisesRegex(
                ValueError,
                "no legal bounded channel root",
            ),
        ):
            BuildBoundedInterClusterRoutingChannel(Source)

    def testBoundedInterClusterRoutingDeckUsesDedicatedFourthLayer(
        self,
    ) -> None:
        Source = self.BuildChannelPlacement(
            ((0, 0), (12, 0), (12, 12))
        )
        with patch.object(
            ClustersModule,
            "BuildPlacedCellGeometry",
            return_value=(set(), set(), set()),
        ):
            Result = BuildBoundedInterClusterRoutingDeck(Source)
        Deck = Result.InterClusterRoutingChannel
        assert Deck is not None
        self.assertEqual(
            Deck.PhysicalModel,
            "parallel-tree-cluster-interface-deck-v1",
        )
        self.assertEqual(Deck.InterfaceDeckLayer, 3)
        self.assertEqual(len(Deck.AffectedClusters), 3)
        self.assertEqual(len(Deck.Lanes), 4)
        self.assertEqual(
            {Lane.Direction for Lane in Deck.Lanes},
            {"XZ-Lane0", "XZ-Lane1"},
        )
        self.assertTrue(all(
            all(
                sum(
                    abs(First[Index] - Second[Index])
                    for Index in range(3)
                ) == 1
                for First, Second in zip(
                    Lane.Cells,
                    Lane.Cells[1:],
                )
            )
            for Lane in Deck.Lanes
        ))
        FirstLaneCells = {
            Cell
            for Lane in Deck.Lanes
            if Lane.Direction == "XZ-Lane0"
            for Cell in Lane.Cells
        }
        SecondLaneCells = {
            Cell
            for Lane in Deck.Lanes
            if Lane.Direction == "XZ-Lane1"
            for Cell in Lane.Cells
        }
        self.assertTrue(all(
            abs(First[0] - Second[0])
            + abs(First[2] - Second[2])
            >= Deck.TrackPitch
            for First in FirstLaneCells
            for Second in SecondLaneCells
        ))
        self.assertEqual(
            {Lane.Layer for Lane in Deck.Lanes},
            {3},
        )
        self.assertEqual(Deck.InsertedBoundaryStrips, ())
        self.assertTrue(all(
            Delta == (0, 0, 0)
            for _Cluster, Delta in Deck.ClusterTranslations
        ))

    def testBoundedInterClusterRoutingDeckBuildsAcyclicTreeFabric(
        self,
    ) -> None:
        # This triangle reproduces the Carry1 endpoint-closure geometry.  A
        # single bend order for both spanning-tree edges made the Manhattan
        # paths cross twice, turning the physical fabric into a cycle.
        Source = self.BuildChannelPlacement(
            ((203, 11), (6, 100), (76, 3))
        )
        with patch.object(
            ClustersModule,
            "BuildPlacedCellGeometry",
            return_value=(set(), set(), set()),
        ):
            Result = BuildBoundedInterClusterRoutingDeck(
                Source,
                ForcedAffectedClusters=(0, 1, 2),
            )
        Deck = Result.InterClusterRoutingChannel
        assert Deck is not None

        for Direction in {Lane.Direction for Lane in Deck.Lanes}:
            DirectionLanes = tuple(
                Lane for Lane in Deck.Lanes
                if Lane.Direction == Direction
            )
            Nodes = {
                Cell
                for Lane in DirectionLanes
                for Cell in Lane.Cells
            }
            Edges = {
                tuple(sorted((First, Second)))
                for Lane in DirectionLanes
                for First, Second in zip(Lane.Cells, Lane.Cells[1:])
            }
            self.assertEqual(len(Edges), len(Nodes) - 1)
            Adjacency = {Node: set() for Node in Nodes}
            for First, Second in Edges:
                Adjacency[First].add(Second)
                Adjacency[Second].add(First)
            Visited = {min(Nodes)}
            Pending = list(Visited)
            while Pending:
                Current = Pending.pop()
                for Neighbor in Adjacency[Current]:
                    if Neighbor not in Visited:
                        Visited.add(Neighbor)
                        Pending.append(Neighbor)
            self.assertEqual(Visited, Nodes)

    def testPhysicalComponentEnvelopeAndDeckPreserveForcedSelection(
        self,
    ) -> None:
        Source = self.BuildChannelPlacement(
            ((0, 0), (12, 0), (24, 0), (36, 0))
        )
        with patch.object(
            ClustersModule,
            "BuildPlacedCellGeometry",
            return_value=(set(), set(), set()),
        ):
            Envelope = BuildBoundedInterClusterRoutingChannel(
                Source,
                ForcedAffectedClusters=(1, 2),
            )
            Result = BuildBoundedInterClusterRoutingDeck(
                Envelope,
                ForcedAffectedClusters=(1, 2),
            )

        EnvelopeChannel = Envelope.InterClusterRoutingChannel
        Deck = Result.InterClusterRoutingChannel
        assert EnvelopeChannel is not None
        assert Deck is not None
        self.assertEqual(EnvelopeChannel.AffectedClusters, (1, 2))
        self.assertEqual(Deck.AffectedClusters, (1, 2))
        self.assertTrue(any(
            Delta != (0, 0, 0)
            for _Cluster, Delta in EnvelopeChannel.ClusterTranslations
        ))
        self.assertEqual(
            Deck.PhysicalModel,
            "parallel-tree-cluster-interface-deck-v1",
        )

    def testBoundedDeckOwnsOnlyEdgesInsideSelectedComponent(
        self,
    ) -> None:
        Source = self.BuildChannelPlacement(
            ((0, 0), (12, 0), (24, 0), (36, 0))
        )
        with patch.object(
            ClustersModule,
            "BuildPlacedCellGeometry",
            return_value=(set(), set(), set()),
        ):
            Result = BuildBoundedInterClusterRoutingDeck(Source)
        Deck = Result.InterClusterRoutingChannel
        assert Deck is not None
        AffectedClusters = frozenset(Deck.AffectedClusters)
        ExpectedSignals = {
            Request.Signal
            for Request in Source.ClusterBoundaryLeaseRequests
            if (
                Request.SourceCluster in AffectedClusters
                and Request.TargetCluster in AffectedClusters
            )
        }

        self.assertEqual(
            set(Deck.AffectedSignals),
            ExpectedSignals,
        )
        self.assertTrue(all(
            Request.SourceCluster in AffectedClusters
            and Request.TargetCluster in AffectedClusters
            for Request in Source.ClusterBoundaryLeaseRequests
            if Request.Signal in Deck.AffectedSignals
        ))

    def testBoundedDeckUsesLearnedCutTerminalCoverage(
        self,
    ) -> None:
        Source = self.BuildChannelPlacement(
            ((0, 0), (12, 0), (24, 0), (36, 0))
        )
        with patch.object(
            ClustersModule,
            "BuildPlacedCellGeometry",
            return_value=(set(), set(), set()),
        ):
            Default = BuildBoundedInterClusterRoutingDeck(Source)
            Preferred = BuildBoundedInterClusterRoutingDeck(
                Source,
                PreferredSignals=("Signal3",),
            )
            Alternate = BuildBoundedInterClusterRoutingDeck(
                Source,
                PreferredSignals=("Signal3",),
                ComponentVariant=1,
            )
            Required = BuildBoundedInterClusterRoutingDeck(
                Source,
                RequiredComponentGateNames=tuple(Source.Clusters[0]),
            )
        assert Default.InterClusterRoutingChannel is not None
        assert Preferred.InterClusterRoutingChannel is not None
        assert Required.InterClusterRoutingChannel is not None
        self.assertIn(
            0,
            Required.InterClusterRoutingChannel.AffectedClusters,
        )
        self.assertEqual(
            Default.InterClusterRoutingChannel.AffectedClusters,
            (2, 3),
        )
        self.assertEqual(
            Preferred.InterClusterRoutingChannel.AffectedClusters,
            (2, 3),
        )
        self.assertNotEqual(
            Alternate.InterClusterRoutingChannel.AffectedClusters,
            Preferred.InterClusterRoutingChannel.AffectedClusters,
        )
        self.assertEqual(
            len(
                Default.Placed.LocalRouteDiagnostics[
                    "__InterClusterRoutingDeckSelection__"
                ]["SelectedPerimeterAccessScore"]
            ),
            4,
        )

    def testRequiredGateAuthorizesBoundedPhysicalRepairInsideHierarchy(
        self,
    ) -> None:
        Source = self.BuildChannelPlacement(
            ((0, 0), (12, 0), (24, 0), (36, 0))
        )
        LogicalComponent = TopologyComponent(
            ComponentId=0,
            GateNames=tuple(
                Name for Cluster in Source.Clusters for Name in Cluster
            ),
            InternalSignals=(),
            InputPorts=(),
            OutputPorts=(),
            MinimumLevel=0,
            MaximumLevel=3,
            QualifyingReconvergentCutCount=1,
            StructuralFingerprint="four-cluster-component",
        )
        Source = replace(
            Source,
            ComponentGraph=ComponentGraph(
                Components=(LogicalComponent,),
                Channels=(),
                GateToComponent=tuple(
                    (Name, 0)
                    for Name in LogicalComponent.GateNames
                ),
                StructuralFingerprint="hierarchical-four-cluster-graph",
                Hierarchical=True,
                MaximumComponentGates=4,
                PeakCutwidth=1,
                QualifyingReconvergentCutCount=1,
            ),
        )

        with patch.object(
            ClustersModule,
            "BuildPlacedCellGeometry",
            return_value=(set(), set(), set()),
        ):
            with self.assertRaisesRegex(
                ValueError,
                "no connected two-or-three-cluster interface deck component",
            ):
                BuildBoundedInterClusterRoutingDeck(Source)
            Repaired = BuildBoundedInterClusterRoutingDeck(
                Source,
                RequiredComponentGateNames=(Source.Clusters[0][0],),
            )
            Channelized = BuildBoundedInterClusterRoutingChannel(
                Source,
                RequiredComponentGateNames=(Source.Clusters[0][0],),
                ForcedAffectedClusters=(
                    Repaired.InterClusterRoutingChannel.AffectedClusters
                ),
            )

        Deck = Repaired.InterClusterRoutingChannel
        assert Deck is not None
        self.assertIn(0, Deck.AffectedClusters)
        self.assertEqual(len(Deck.AffectedClusters), 2)
        self.assertEqual(
            Channelized.InterClusterRoutingChannel.AffectedClusters,
            Deck.AffectedClusters,
        )

    def testBoundedDeckMaximizesLearnedCutOwnershipBeforeDemand(
        self,
    ) -> None:
        Source = self.BuildChannelPlacement(
            ((0, 0), (12, 0), (24, 0), (36, 0))
        )
        Requests = (
            ChannelsModule.ClusterBoundaryLeaseRequest(
                SourceCluster=0,
                TargetCluster=1,
                Signal="LearnedCut",
                SourceBoundarySide="east",
                TargetBoundarySide="west",
                SourceTerminal=(1, 2, 0),
                TargetTerminals=tuple(
                    (11, 2, Offset) for Offset in range(4)
                ),
                CompletePinAccess=True,
            ),
            ChannelsModule.ClusterBoundaryLeaseRequest(
                SourceCluster=1,
                TargetCluster=2,
                Signal="Bridge",
                SourceBoundarySide="east",
                TargetBoundarySide="west",
                SourceTerminal=None,
                TargetTerminals=(),
                CompletePinAccess=True,
            ),
            ChannelsModule.ClusterBoundaryLeaseRequest(
                SourceCluster=2,
                TargetCluster=3,
                Signal="LearnedCut",
                SourceBoundarySide="east",
                TargetBoundarySide="west",
                SourceTerminal=(25, 2, 0),
                TargetTerminals=((35, 2, 0),),
                CompletePinAccess=True,
            ),
        )
        Source = replace(
            Source,
            Placed=replace(
                Source.Placed,
                ClusterBoundaryLeaseRequests=Requests,
            ),
            ClusterBoundaryLeaseRequests=Requests,
        )
        with patch.object(
            ClustersModule,
            "BuildPlacedCellGeometry",
            return_value=(set(), set(), set()),
        ):
            Result = BuildBoundedInterClusterRoutingDeck(
                Source,
                PreferredSignals=("LearnedCut",),
            )
        Deck = Result.InterClusterRoutingChannel
        assert Deck is not None
        self.assertEqual(Deck.AffectedClusters, (0, 1, 2))
        Selection = (
            Result.Placed.LocalRouteDiagnostics[
                "__InterClusterRoutingDeckSelection__"
            ]
        )
        self.assertEqual(
            Selection["SelectedPreferredOwnedTerminalCoverage"],
            6,
        )

    def testBoundedDeckRanksCompleteBoundaryInterfaceEvidenceFirst(
        self,
    ) -> None:
        Source = self.BuildChannelPlacement(
            ((0, 0), (12, 0), (24, 0), (36, 0))
        )
        DenseCutRequests = tuple(
            ChannelsModule.ClusterBoundaryLeaseRequest(
                SourceCluster=0,
                TargetCluster=1,
                Signal=f"DenseCut{Index}",
                SourceBoundarySide="east",
                TargetBoundarySide="west",
                SourceTerminal=(1, 2, Index),
                TargetTerminals=((11, 2, Index),),
                CompletePinAccess=True,
            )
            for Index in range(6)
        )
        Requests = Source.ClusterBoundaryLeaseRequests + DenseCutRequests
        Source = replace(
            Source,
            Placed=replace(
                Source.Placed,
                ClusterBoundaryLeaseRequests=Requests,
            ),
            ClusterBoundaryLeaseRequests=Requests,
        )
        Reordered = replace(
            Source,
            Placed=replace(
                Source.Placed,
                ClusterBoundaryLeaseRequests=tuple(reversed(Requests)),
            ),
            ClusterBoundaryLeaseRequests=tuple(reversed(Requests)),
        )
        RenamedRequests = tuple(
            replace(Request, Signal=f"RenamedSignal{Index}")
            for Index, Request in enumerate(Requests)
        )
        Renamed = replace(
            Source,
            Placed=replace(
                Source.Placed,
                ClusterBoundaryLeaseRequests=RenamedRequests,
            ),
            ClusterBoundaryLeaseRequests=RenamedRequests,
        )

        def BuildVariants(Value: PcbPlacement):
            return tuple(
                BuildBoundedInterClusterRoutingDeck(
                    Value,
                    ComponentVariant=Variant,
                )
                for Variant in range(5)
            )

        with patch.object(
            ClustersModule,
            "BuildPlacedCellGeometry",
            return_value=(set(), set(), set()),
        ):
            Results = BuildVariants(Source)
            ReorderedResults = BuildVariants(Reordered)
            RenamedResults = BuildVariants(Renamed)

        BoundaryInterfaceCounts = tuple(
            Result.Placed.LocalRouteDiagnostics[
                "__InterClusterRoutingDeckSelection__"
            ]["SelectedBoundaryInterfaceSignalCount"]
            for Result in Results
        )
        ReorderedClusters = tuple(
            Result.InterClusterRoutingChannel.AffectedClusters
            for Result in ReorderedResults
            if Result.InterClusterRoutingChannel is not None
        )
        SelectedClusters = tuple(
            Result.InterClusterRoutingChannel.AffectedClusters
            for Result in Results
            if Result.InterClusterRoutingChannel is not None
        )
        RenamedClusters = tuple(
            Result.InterClusterRoutingChannel.AffectedClusters
            for Result in RenamedResults
            if Result.InterClusterRoutingChannel is not None
        )
        self.assertEqual(
            BoundaryInterfaceCounts,
            tuple(sorted(BoundaryInterfaceCounts)),
        )
        self.assertEqual(BoundaryInterfaceCounts[0], 1)
        self.assertEqual(BoundaryInterfaceCounts[-1], 8)
        self.assertEqual(SelectedClusters, ReorderedClusters)
        self.assertEqual(SelectedClusters, RenamedClusters)

    def testBoundedInterClusterRoutingDeckFingerprintIgnoresNames(
        self,
    ) -> None:
        First = self.BuildChannelPlacement(((0, 0), (12, 0)))
        Second = self.BuildChannelPlacement(
            ((0, 0), (12, 0)),
            NamePrefix="Renamed",
            SignalPrefix="Wire",
        )
        with patch.object(
            ClustersModule,
            "BuildPlacedCellGeometry",
            return_value=(set(), set(), set()),
        ):
            FirstResult = BuildBoundedInterClusterRoutingDeck(First)
            SecondResult = BuildBoundedInterClusterRoutingDeck(Second)
        assert FirstResult.InterClusterRoutingChannel is not None
        assert SecondResult.InterClusterRoutingChannel is not None
        self.assertEqual(
            (
                FirstResult.InterClusterRoutingChannel
                .ChannelFingerprint
            ),
            (
                SecondResult.InterClusterRoutingChannel
                .ChannelFingerprint
            ),
        )

    def testTransactionalRepairClusterSelectionsMaximizeCutCoverage(
        self,
    ) -> None:
        Eligible = (
            (3, ("GateA",), frozenset({"A"})),
            (7, ("GateB",), frozenset({"B"})),
            (9, ("GateC",), frozenset({"A", "C"})),
            (11, ("GateD",), frozenset({"D", "E"})),
        )
        Ranked = RankTransactionalRepairClusterSelections(
            Eligible,
            2,
        )
        self.assertEqual(Ranked[0], (2, 3))
        self.assertEqual(
            {
                Signal
                for Ordinal in Ranked[0]
                for Signal in Eligible[Ordinal][2]
            },
            {"A", "C", "D", "E"},
        )
        Renamed = tuple(
            (
                Cluster,
                Gates,
                frozenset(f"Renamed{Signal}" for Signal in Signals),
            )
            for Cluster, Gates, Signals in Eligible
        )
        self.assertEqual(
            RankTransactionalRepairClusterSelections(Renamed, 2),
            Ranked,
        )
        ExactPairEligible = (
            (3, ("GateA",), frozenset({"Left"})),
            (5, ("GateB",), frozenset({"Left", "Right"})),
            (7, ("GateC",), frozenset({"Left"})),
        )
        self.assertEqual(
            SelectTransactionalRepairClusterSelections(
                ExactPairEligible,
                2,
                frozenset({"Left", "Right"}),
            ),
            ((0, 1), (1, 2)),
        )
        ThreeOwnerCut = (
            (3, ("GateA",), frozenset({"Carry"})),
            (5, ("GateB",), frozenset({"Nand"})),
            (7, ("GateC",), frozenset({"Propagate", "Aux"})),
        )
        self.assertEqual(
            SelectTransactionalRepairClusterSelections(
                ThreeOwnerCut,
                2,
                frozenset({"Carry", "Nand", "Propagate", "Aux"}),
            ),
            ((0, 1, 2),),
        )

    @staticmethod
    def BoundaryCandidate(
        Signal: str,
        ResourceIndex: int,
    ) -> BoundaryEscapeCandidate:
        Position = (ResourceIndex, 1, 0)
        return BoundaryEscapeCandidate(
            Signal=Signal,
            Anchor=(ResourceIndex, 1, -1),
            Entrance=Position,
            Claims=RoutingResourceClaims(
                WireCells=frozenset((Position,)),
                ElectricalCells=frozenset((Position,)),
            ),
        )

    def testCutBoundaryEscapeProvesHigherOrderCapacityDeficit(
        self,
    ) -> None:
        Domains = {
            (0, Signal): (
                self.BoundaryCandidate(Signal, 0),
                self.BoundaryCandidate(Signal, 1),
            )
            for Signal in ("First", "Second", "Third")
        }
        Result = EvaluateCutBoundaryEscapeFeasibility(
            Domains,
            ("First", "Second", "Third"),
        )
        self.assertEqual(Result.Verdict, "infeasible")
        self.assertEqual(Result.VariableCount, 3)
        self.assertEqual(Result.MaximumAssignedVariables, 2)
        self.assertEqual(
            Result.ConflictSignals,
            ("First", "Second", "Third"),
        )
        for Removed in ("First", "Second", "Third"):
            PairSignals = tuple(
                Signal
                for Signal in ("First", "Second", "Third")
                if Signal != Removed
            )
            Pair = EvaluateCutBoundaryEscapeFeasibility(
                {
                    Key: Value
                    for Key, Value in Domains.items()
                    if Key[1] in PairSignals
                },
                PairSignals,
            )
            self.assertEqual(Pair.Verdict, "feasible")

    def testCutBoundaryEscapeIsRenameAndOrderInvariant(
        self,
    ) -> None:
        Original = EvaluateCutBoundaryEscapeFeasibility(
            {
                (0, Signal): (
                    self.BoundaryCandidate(Signal, 0),
                    self.BoundaryCandidate(Signal, 1),
                )
                for Signal in ("Alpha", "Beta", "Gamma")
            },
            ("Gamma", "Alpha", "Beta"),
        )
        Renamed = EvaluateCutBoundaryEscapeFeasibility(
            {
                (0, Signal): tuple(reversed((
                    self.BoundaryCandidate(Signal, 0),
                    self.BoundaryCandidate(Signal, 1),
                )))
                for Signal in ("One", "Two", "Three")
            },
            ("Two", "Three", "One"),
        )
        self.assertEqual(Original.Verdict, Renamed.Verdict)
        self.assertEqual(
            Original.StructuralFingerprint,
            Renamed.StructuralFingerprint,
        )

    def testCutBoundaryEscapeBudgetExhaustionDoesNotReject(
        self,
    ) -> None:
        Result = EvaluateCutBoundaryEscapeFeasibility(
            {
                (0, Signal): (
                    self.BoundaryCandidate(Signal, 0),
                    self.BoundaryCandidate(Signal, 1),
                )
                for Signal in ("First", "Second", "Third")
            },
            ("First", "Second", "Third"),
            MaximumExpansions=1,
        )
        self.assertEqual(Result.Verdict, "budget-exhausted")
        self.assertFalse(ShouldRejectCutBoundaryEscapePlacement(
            TopologyRequiresJointPortfolio=True,
            Diagnostics=Result.ToDictionary(),
        ))
        self.assertFalse(ShouldRejectCutBoundaryEscapePlacement(
            TopologyRequiresJointPortfolio=False,
            Diagnostics={"Verdict": "infeasible"},
        ))
        self.assertTrue(ShouldRejectCutBoundaryEscapePlacement(
            TopologyRequiresJointPortfolio=True,
            Diagnostics={"Verdict": "infeasible"},
        ))

    def testExactMandatoryAccessCommitPromotesFirstZeroConflictState(
        self,
    ) -> None:
        ConflictProfile = MandatoryAccessConflictProfile(
            OwnershipFingerprint="ownership-conflict",
            ConflictFingerprint="conflict",
            OwnershipRecords=(),
            CrossConflicts=((object(), ("Left", "Right")),),
            SelfConflicts=(),
        )
        LegalProfile = MandatoryAccessConflictProfile(
            OwnershipFingerprint="ownership-legal",
            ConflictFingerprint="legal",
            OwnershipRecords=(),
            CrossConflicts=(),
            SelfConflicts=(),
        )
        Ordered = OrderExactStatesForMandatoryAccessCommit(
            (
                {"CandidateIndex": 0, "ExactLegal": True},
                {"CandidateIndex": 1, "ExactLegal": True},
                {"CandidateIndex": 2, "ExactLegal": True},
                {"CandidateIndex": 3, "ExactLegal": False},
            ),
            {
                0: ConflictProfile,
                1: LegalProfile,
            },
        )
        self.assertEqual(
            [State["CandidateIndex"] for State in Ordered],
            [0, 1, 2, 3],
        )
        self.assertEqual(
            [State["SearchCandidateIndex"] for State in Ordered],
            [1, 0, 2, 3],
        )
        self.assertEqual(
            Ordered[0]["ExactMandatoryAccessConflictResources"],
            0,
        )
        self.assertEqual(
            Ordered[1]["ExactMandatoryAccessConflictResources"],
            1,
        )
        self.assertFalse(Ordered[2]["ExactMandatoryAccessScreened"])
        self.assertFalse(Ordered[3]["ExactLegal"])

    def testExactMandatoryAccessCommitPreservesOrderWithoutLegalProof(
        self,
    ) -> None:
        ConflictProfile = MandatoryAccessConflictProfile(
            OwnershipFingerprint="ownership-conflict",
            ConflictFingerprint="conflict",
            OwnershipRecords=(),
            CrossConflicts=((object(), ("Left", "Right")),),
            SelfConflicts=(),
        )
        Ordered = OrderExactStatesForMandatoryAccessCommit(
            (
                {"CandidateIndex": 0, "ExactLegal": True},
                {"CandidateIndex": 1, "ExactLegal": True},
            ),
            {0: ConflictProfile},
        )
        self.assertEqual(
            [State["SearchCandidateIndex"] for State in Ordered],
            [0, 1],
        )

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

    def testClusterInterfacePlacementSeparatesReportedBankPair(self) -> None:
        def BuildModule(
            InputSignal: str,
            InternalSignal: str,
            ResultSignal: str,
            *,
            ReverseOrder: bool = False,
        ) -> ModuleIR:
            Gates = [
                Gate("InputGate", GateKind.INPUT, [InputSignal], []),
                Gate("OtherInputGate", GateKind.INPUT, ["OtherInput"], []),
                Gate(
                    "Consumer",
                    GateKind.NAND,
                    [ResultSignal],
                    [InputSignal, InternalSignal],
                ),
                Gate(
                    "Producer",
                    GateKind.NAND,
                    [InternalSignal],
                    ["OtherInput", "OtherInput"],
                ),
                Gate(
                    "OutputGate",
                    GateKind.OUTPUT,
                    [],
                    [ResultSignal],
                ),
            ]
            return ModuleIR(
                Name="InterfacePattern",
                Inputs=[InputSignal, "OtherInput"],
                Outputs=[ResultSignal],
                Gates=list(reversed(Gates)) if ReverseOrder else Gates,
            )

        Clusters = (("Consumer",), ("Producer",))
        Variant = ClusterLayoutVariant(
            Rotation=0,
            MirrorX=False,
            Positions={},
            Rotations={},
            Mirrors={},
            Width=1,
            Depth=1,
            ActualGeometry={},
            ElectricalGeometry={},
        )
        Variants = {0: Variant, 1: Variant}
        SharedWestBank = ScoreClusterInterfacePlacement(
            BuildModule("InputA", "InternalA", "ResultA"),
            Clusters,
            {0: (1, 0), 1: (0, 0)},
            Variants,
            (("InputA", "InternalA"),),
        )
        SeparatedBanks = ScoreClusterInterfacePlacement(
            BuildModule("InputA", "InternalA", "ResultA"),
            Clusters,
            {0: (0, 0), 1: (1, 0)},
            Variants,
            (("InputA", "InternalA"),),
        )
        RenamedAndReordered = ScoreClusterInterfacePlacement(
            BuildModule(
                "RenamedInput",
                "RenamedInternal",
                "RenamedResult",
                ReverseOrder=True,
            ),
            Clusters,
            {0: (1, 0), 1: (0, 0)},
            Variants,
            (("RenamedInput", "RenamedInternal"),),
        )
        HigherOrder = ScoreClusterInterfacePlacement(
            BuildModule("InputA", "InternalA", "ResultA"),
            Clusters,
            {0: (1, 0), 1: (0, 0)},
            Variants,
            HigherOrderConflictSets=(
                ("InputA", "InternalA", "ResultA"),
            ),
        )
        RenamedHigherOrder = ScoreClusterInterfacePlacement(
            BuildModule(
                "RenamedInput",
                "RenamedInternal",
                "RenamedResult",
                ReverseOrder=True,
            ),
            Clusters,
            {0: (1, 0), 1: (0, 0)},
            Variants,
            HigherOrderConflictSets=(
                ("RenamedResult", "RenamedInput", "RenamedInternal"),
            ),
        )
        self.assertEqual(SharedWestBank.PairBankConflicts, 1)
        self.assertEqual(SeparatedBanks.PairBankConflicts, 0)
        self.assertEqual(HigherOrder.HigherOrderBankPressure, 1)
        self.assertEqual(
            HigherOrder.HigherOrderBankPressure,
            RenamedHigherOrder.HigherOrderBankPressure,
        )
        self.assertEqual(
            SharedWestBank.Pattern.OwnershipFingerprint,
            RenamedAndReordered.Pattern.OwnershipFingerprint,
        )

    def testHigherOrderBankDemandCountsEveryCongestedBank(self) -> None:
        FirstBank = (0, 0, "East")
        SecondBank = (1, 0, "West")
        SingleCollision = ScoreHigherOrderPhysicalBankDemand(
            {
                "Alpha": frozenset((FirstBank,)),
                "Beta": frozenset((FirstBank,)),
                "Gamma": frozenset((SecondBank,)),
                "Delta": frozenset(),
            },
            (("Alpha", "Beta", "Gamma", "Delta"),),
        )
        TwoCollisions = ScoreHigherOrderPhysicalBankDemand(
            {
                "Alpha": frozenset((FirstBank,)),
                "Beta": frozenset((FirstBank,)),
                "Gamma": frozenset((SecondBank,)),
                "Delta": frozenset((SecondBank,)),
            },
            (("Alpha", "Beta", "Gamma", "Delta"),),
        )
        RenamedAndReordered = ScoreHigherOrderPhysicalBankDemand(
            {
                "RenamedDelta": frozenset((SecondBank,)),
                "RenamedGamma": frozenset((SecondBank,)),
                "RenamedBeta": frozenset((FirstBank,)),
                "RenamedAlpha": frozenset((FirstBank,)),
            },
            ((
                "RenamedGamma",
                "RenamedAlpha",
                "RenamedDelta",
                "RenamedBeta",
            ),),
        )
        self.assertEqual(SingleCollision.CollisionPairs, 1)
        self.assertEqual(TwoCollisions.CollisionPairs, 2)
        self.assertEqual(SingleCollision.PeakDemand, 2)
        self.assertEqual(TwoCollisions.PeakDemand, 2)
        self.assertEqual(TwoCollisions.ExcessDemand, 2)
        self.assertEqual(TwoCollisions.OverloadedBankCount, 2)
        self.assertEqual(TwoCollisions, RenamedAndReordered)

    def testFacingClusterBanksShareOneBoundaryCorridor(self) -> None:
        self.assertEqual(
            ClusterBoundaryCorridorKey((0, 0, "East")),
            ClusterBoundaryCorridorKey((1, 0, "West")),
        )
        self.assertEqual(
            ClusterBoundaryCorridorKey((2, 3, "South")),
            ClusterBoundaryCorridorKey((2, 4, "North")),
        )
        self.assertNotEqual(
            ClusterBoundaryCorridorKey((0, 0, "East")),
            ClusterBoundaryCorridorKey((2, 0, "West")),
        )

    def testJointPlacementRanksExactCutByInterfaceBankOwnership(self) -> None:
        Module = ModuleIR(
            Name="JointInterfacePattern",
            Inputs=["InputA", "OtherInput"],
            Outputs=["ResultA"],
            Gates=[
                Gate("InputGate", GateKind.INPUT, ["InputA"], []),
                Gate(
                    "OtherInputGate",
                    GateKind.INPUT,
                    ["OtherInput"],
                    [],
                ),
                Gate(
                    "Consumer",
                    GateKind.NAND,
                    ["ResultA"],
                    ["InputA", "InternalA"],
                ),
                Gate(
                    "Producer",
                    GateKind.NAND,
                    ["InternalA"],
                    ["OtherInput", "OtherInput"],
                ),
                Gate("OutputGate", GateKind.OUTPUT, [], ["ResultA"]),
            ],
        )
        Clusters = (("Consumer",), ("Producer",))
        VariantsByCluster = {
            ClusterIndex: (
                ClusterLayoutVariant(
                    Rotation=0,
                    MirrorX=False,
                    Positions={Name: (0, 0)},
                    Rotations={Name: 0},
                    Mirrors={Name: False},
                    Width=4,
                    Depth=4,
                    ActualGeometry={},
                    ElectricalGeometry={},
                ),
            )
            for ClusterIndex, (Name,) in enumerate(Clusters)
        }
        Constraints = PlacementAssignmentConstraintSet(
            PairwiseConflictEdges=(("InputA", "InternalA"),),
        )
        Assignment, SelectedVariants, Diagnostics = (
            OptimizeJointClusterPlacement(
            Module,
            Clusters,
            BuildTopologicalLevels(Module),
            VariantsByCluster,
            BeamWidth=8,
            PassLimit=1,
            RetainedCandidates=1,
            InitialAssignment={0: (1, 0), 1: (0, 0)},
            AssignmentConstraints=Constraints,
            EnableClusterInterfacePlacementFeasibility=True,
            )
        )
        self.assertEqual(Assignment, {0: (0, 0), 1: (1, 0)})
        self.assertEqual(
            Diagnostics["SelectedInterfacePairBankConflicts"],
            0,
        )
        self.assertIsNotNone(
            Diagnostics["SelectedClusterInterfacePlacement"]
        )
        self.assertTrue(
            Diagnostics["ClusterInterfacePlacementFeasibility"][
                "AppliedAsHardFilter"
            ]
        )
        self.assertGreater(
            Diagnostics["ClusterInterfacePlacementFeasibility"][
                "RejectedStateCount"
            ],
            0,
        )
        AllInterfaceTopology = BuildClusterInterfaceTopology(
            Module,
            Clusters,
        )
        self.assertEqual(
            ScoreClusterInterfaceFacingMismatches(
                AllInterfaceTopology,
                Assignment,
                SelectedVariants,
            ),
            ScoreClusterInterfacePlacement(
                Module,
                Clusters,
                Assignment,
                SelectedVariants,
                Topology=AllInterfaceTopology,
            ).FacingMismatches,
        )
        RecurrentConstraints = PlacementAssignmentConstraintSet(
            ActiveObservedInterfaceConflictEdges=((
                "InputA",
                "InternalA",
            ),),
        )
        RecurrentAssignment, _Variants, RecurrentDiagnostics = (
            OptimizeJointClusterPlacement(
                Module,
                Clusters,
                BuildTopologicalLevels(Module),
                VariantsByCluster,
                BeamWidth=8,
                PassLimit=1,
                RetainedCandidates=1,
                InitialAssignment={0: (1, 0), 1: (0, 0)},
                AssignmentConstraints=RecurrentConstraints,
                EnableClusterInterfacePlacementFeasibility=True,
            )
        )
        self.assertEqual(
            RecurrentAssignment,
            {0: (0, 0), 1: (1, 0)},
        )
        self.assertEqual(
            RecurrentDiagnostics["SelectedInterfacePairBankConflicts"],
            0,
        )
        self.assertEqual(
            RecurrentDiagnostics["EffectivePairwiseConflictEdges"],
            [],
        )
        self.assertEqual(
            RecurrentDiagnostics[
                "EffectiveObservedInterfaceConflictEdges"
            ],
            [["InputA", "InternalA"]],
        )
        self.assertEqual(
            RecurrentDiagnostics[
                "SelectedObservedInterfaceBankConflicts"
            ],
            0,
        )
        _Assignment, _Variants, InitialDiagnostics = (
            OptimizeJointClusterPlacement(
                Module,
                Clusters,
                BuildTopologicalLevels(Module),
                VariantsByCluster,
                BeamWidth=8,
                PassLimit=8,
                RetainedCandidates=1,
                InitialAssignment={0: (1, 0), 1: (0, 0)},
                EnableClusterInterfacePlacementFeasibility=True,
            )
        )
        self.assertEqual(InitialDiagnostics["EffectivePassLimit"], 4)
        self.assertGreater(
            InitialDiagnostics["SelectedClusterInterfacePlacement"][
                "SignalCount"
            ],
            0,
        )
        _LegacyAssignment, _LegacyVariants, LegacyDiagnostics = (
            OptimizeJointClusterPlacement(
                Module,
                Clusters,
                BuildTopologicalLevels(Module),
                VariantsByCluster,
                BeamWidth=8,
                PassLimit=1,
                RetainedCandidates=1,
                InitialAssignment={0: (1, 0), 1: (0, 0)},
                EnableClusterInterfacePlacementFeasibility=False,
            )
        )
        self.assertIsNone(
            LegacyDiagnostics["SelectedClusterInterfacePlacement"]
        )

    def testExactCutRefinesClusterMembershipWithoutIdentifierPolicy(
        self,
    ) -> None:
        Module = ModuleIR(
            Name="RefinedChain",
            Inputs=["Left", "Right"],
            Outputs=["Result"],
            Gates=[
                Gate("InputLeft", GateKind.INPUT, ["Left"], []),
                Gate("InputRight", GateKind.INPUT, ["Right"], []),
                Gate("First", GateKind.NAND, ["FirstNet"], ["Left", "Right"]),
                Gate(
                    "Middle",
                    GateKind.NAND,
                    ["SecondNet"],
                    ["FirstNet", "Right"],
                ),
                Gate(
                    "Last",
                    GateKind.NAND,
                    ["Result"],
                    ["SecondNet", "Left"],
                ),
                Gate("Output", GateKind.OUTPUT, [], ["Result"]),
            ],
        )
        DefaultClusters = BuildConnectivityClusters(
            Module,
            MaximumClusterSize=2,
        )
        RefinedClusters = BuildConnectivityClusters(
            Module,
            MaximumClusterSize=2,
            RefinementProfile=CutDrivenClusterRefinementProfile(
                Signals=("SecondNet",),
                EdgeWeight=4,
            ),
        )
        self.assertIn(("First", "Middle"), DefaultClusters)
        self.assertIn(("Middle", "Last"), RefinedClusters)

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

    def testFixedConnectivityClustersPreserveCapacityRepairPartition(
        self,
    ) -> None:
        Policy = LocalFirstPhysicalDesignPolicy
        FixedClusters = (("N0",), ("N1", "N2"))

        Placement = PlacePcbGraph(
            self.ClusteredNetlist(),
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
            EnableClusterInterfacePlacementFeasibility=True,
            CutDrivenClusterRefinementSignals=frozenset(("S0",)),
            FixedConnectivityClusters=FixedClusters,
        )

        self.assertEqual(Placement.Clusters, FixedClusters)
        with self.assertRaisesRegex(
            ValueError,
            "must partition every NAND gate exactly once",
        ):
            PlacePcbGraph(
                self.ClusteredNetlist(),
                RoutingSpacing=Policy.Placement.RoutingSpacing,
                PlacementPolicy=Policy.Placement,
                PackingPolicy=Policy.NandPacking,
                ClusterPolicy=Policy.Clustering,
                FixedConnectivityClusters=(("N0",), ("N1",)),
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

    def testClusterBoundaryLeaseRequestsFollowSlotsNotIdentifiers(self) -> None:
        Module = self.ClusteredNetlist().Modules["ClusteredBoundaryGraph"]
        Clusters = (("N0",), ("N1",), ("N2",))
        Requests = BuildClusterBoundaryLeaseRequests(
            BuildClusterBoundaryBundles(Module, Clusters),
            {0: (0, 0), 1: (1, 0), 2: (1, 1)},
        )

        self.assertEqual(
            [(Value.Signal, Value.SourceBoundarySide, Value.TargetBoundarySide)
             for Value in Requests],
            [("S0", "East", "West"), ("S1", "South", "North")],
        )
        self.assertTrue(all(
            Value.ToDictionary()["LeaseExtent"]
            == "first-segment"
            for Value in Requests
        ))

    def testInternalPinBankJointSearchScansOnlyIncidentCluster(
        self,
    ) -> None:
        """A late ECO keeps full scoring while bounding state generation."""
        Module = self.ClusteredNetlist().Modules["ClusteredBoundaryGraph"]
        Clusters = (("N0",), ("N1",), ("N2",))
        VariantsByCluster = {
            ClusterIndex: tuple(
                ClusterLayoutVariant(
                    Rotation=Rotation,
                    MirrorX=False,
                    Positions={Name: (0, 0)},
                    Rotations={Name: Rotation},
                    Mirrors={Name: False},
                    Width=4,
                    Depth=4,
                    ActualGeometry={},
                    ElectricalGeometry={},
                )
                for Rotation in (0, 90)
            )
            for ClusterIndex, (Name,) in enumerate(Clusters)
        }
        Common = {
            "Module": Module,
            "Clusters": Clusters,
            "Levels": BuildTopologicalLevels(Module),
            "VariantsByCluster": VariantsByCluster,
            "BeamWidth": 16,
            "PassLimit": 1,
            "RetainedCandidates": 1,
            "InitialAssignment": {
                0: (0, 0),
                1: (1, 0),
                2: (2, 0),
            },
        }
        _FullAssignment, _FullVariants, FullDiagnostics = (
            OptimizeJointClusterPlacement(**Common)
        )
        FocusedAssignment, _FocusedVariants, FocusedDiagnostics = (
            OptimizeJointClusterPlacement(
                **Common,
                FocusedOptimizationClusters=frozenset({1}),
            )
        )

        self.assertEqual(
            FocusedDiagnostics["FocusedOptimizationClusters"],
            [1],
        )
        self.assertEqual(
            FocusedDiagnostics["JointOptimizationClusters"],
            [1],
        )
        self.assertLess(
            FocusedDiagnostics["CandidateCount"],
            FullDiagnostics["CandidateCount"],
        )
        self.assertLessEqual(
            FocusedDiagnostics["InterfaceOwnershipEvaluationCount"],
            FocusedDiagnostics["BeamWidth"],
        )
        self.assertEqual(set(FocusedAssignment), {0, 1, 2})
        CurrentCut = RoutingAssignmentCut(
            Classification=(
                RoutingAssignmentCutClassification
                .CandidateStarvationPlacementConflict
            ),
            ConflictGraphJson="{}",
            ConflictSignals=("S1",),
        )
        PreviousPairCut = RoutingAssignmentCut(
            Classification=(
                RoutingAssignmentCutClassification
                .PortalCoveragePairConflict
            ),
            ConflictGraphJson="{}",
            ConflictSignals=("S0", "S1"),
            PairwiseConflictEdges=(("S0", "S1"),),
        )
        _FrontierAssignment, _FrontierVariants, FrontierDiagnostics = (
            OptimizeJointClusterPlacement(
                **Common,
                AssignmentCut=CurrentCut,
                FrontierAssignmentCuts=(CurrentCut, PreviousPairCut),
                FocusedOptimizationClusters=frozenset({0, 1}),
            )
        )
        self.assertIn(
            ["S0", "S1"],
            FrontierDiagnostics["EffectivePairwiseConflictEdges"],
        )
        PreviousHigherOrderCut = replace(
            PreviousPairCut,
            Classification=(
                RoutingAssignmentCutClassification.SaturatedBoundaryCut
            ),
            ConflictSignals=("S2", "S0", "S1"),
            PairwiseConflictEdges=(),
        )
        self.assertEqual(
            BuildAssignmentCutHigherOrderSignalSet(
                PreviousHigherOrderCut
            ),
            ("S0", "S1", "S2"),
        )
        self.assertEqual(
            SelectFocusedCutEpochClusters((4, 2, 9, 1), True),
            frozenset({4, 2}),
        )
        self.assertEqual(
            SelectFocusedCutEpochClusters((4, 2, 9, 1), False),
            frozenset(),
        )
        self.assertEqual(
            SelectFocusedTopologyFrontierClusters(
                (4, 2, 9),
                (2, 7, 8),
                True,
            ),
            frozenset({4, 2, 7}),
        )
        self.assertEqual(
            SelectFocusedTopologyFrontierClusters(
                (4, 2, 9),
                (7, 8),
                False,
            ),
            frozenset(),
        )
        self.assertEqual(
            SelectFocusedConstraintComponentClusters(
                (4, 2),
                (2, 7, 8, 9),
                True,
                MaximumClusters=4,
            ),
            frozenset({4, 2, 7, 8}),
        )
        self.assertEqual(
            SelectFocusedConstraintComponentClusters(
                (4, 2),
                (7, 8),
                False,
            ),
            frozenset({4, 2}),
        )
        StablePrimary = BuildJointPortfolioBaseRelocationControls(
            RelocationVariant=2,
            JointPlacementCandidateIndex=0,
            RequiresStructuredJointRelocation=True,
            PreservePortfolioBaseAssignment=True,
        )
        StableRetained = BuildJointPortfolioBaseRelocationControls(
            RelocationVariant=2,
            JointPlacementCandidateIndex=5,
            RequiresStructuredJointRelocation=True,
            PreservePortfolioBaseAssignment=True,
        )
        LegacyRetained = BuildJointPortfolioBaseRelocationControls(
            RelocationVariant=2,
            JointPlacementCandidateIndex=5,
            RequiresStructuredJointRelocation=True,
            PreservePortfolioBaseAssignment=False,
        )
        self.assertEqual(StablePrimary, StableRetained)
        self.assertEqual(StableRetained, (1, False))
        self.assertEqual(LegacyRetained, (6, True))
        self.assertEqual(
            SelectInternalPinBankGeometrySignals(
                Enabled=True,
                RepairSignals=("FreshEndpoint",),
                CoordinatedCandidateDiversificationSignals=(
                    "FreshEndpoint",
                    "PriorEndpointA",
                    "PriorEndpointB",
                ),
            ),
            frozenset({"FreshEndpoint"}),
        )
        self.assertEqual(
            SelectInternalPinBankGeometrySignals(
                Enabled=False,
                RepairSignals=("FreshEndpoint",),
                CoordinatedCandidateDiversificationSignals=(
                    "PriorEndpointA",
                ),
            ),
            frozenset(),
        )
    def testSaturatedInterfaceCutBecomesCumulativePlacementConstraint(
        self,
    ) -> None:
        Cut = RoutingAssignmentCut.FromFailure(RoutingFailure(
            Reason=RoutingFailureReason.BoundaryEscapeInfeasible,
            Stage="ClusterBoundaryLease",
            Diagnostics={
                "ConflictGraph": {
                    "Classification": "saturated-boundary-cut",
                    "ConflictSignals": ["First", "Second", "Third"],
                    "RelocationSignals": ["First", "Second", "Third"],
                    "PriorityRelocationSignals": [
                        "First",
                        "Second",
                        "Third",
                    ],
                },
            },
        ))
        self.assertIsNotNone(Cut)
        Constraints = PlacementAssignmentConstraintSet().WithCut(Cut)
        self.assertEqual(
            Constraints.HigherOrderSignalSets,
            (("First", "Second", "Third"),),
        )

    def testObservedInterfaceEdgesRemainSeparateFromExactConstraints(
        self,
    ) -> None:
        Cut = RoutingAssignmentCut.FromFailure(RoutingFailure(
            Reason=RoutingFailureReason.BoundaryEscapeInfeasible,
            Stage="ClusterBoundaryLease",
            Diagnostics={
                "ConflictGraph": {
                    "Classification": "saturated-boundary-cut",
                    "ConflictSignals": ["First", "Second", "Third"],
                    "RelocationSignals": ["First", "Second", "Third"],
                    "ObservedPatternConflictEdges": [
                        ["Second", "First"],
                    ],
                },
            },
        ))
        self.assertIsNotNone(Cut)
        Constraints = PlacementAssignmentConstraintSet().WithCut(Cut)
        self.assertEqual(Constraints.PairwiseConflictEdges, ())
        self.assertEqual(
            Constraints.ObservedInterfaceConflictEdges,
            (("First", "Second"),),
        )

    def testHeuristicInterfaceConstraintsRequireRecurrenceToPersist(
        self,
    ) -> None:
        def BuildCut(
            Signals: tuple[str, ...],
            Edge: tuple[str, str],
        ) -> RoutingAssignmentCut:
            Cut = RoutingAssignmentCut.FromFailure(RoutingFailure(
                Reason=RoutingFailureReason.BoundaryEscapeInfeasible,
                Stage="ClusterBoundaryLease",
                Diagnostics={
                    "ConflictGraph": {
                        "Classification": "saturated-boundary-cut",
                        "ConflictSignals": list(Signals),
                        "RelocationSignals": list(Signals),
                        "ObservedPatternConflictEdges": [list(Edge)],
                    },
                },
            ))
            assert Cut is not None
            return Cut

        FirstCut = BuildCut(
            ("First", "Second", "Third"),
            ("First", "Second"),
        )
        SecondCut = BuildCut(
            ("Fourth", "Fifth", "Sixth"),
            ("Fourth", "Fifth"),
        )
        Constraints = PlacementAssignmentConstraintSet().WithCut(FirstCut)
        FirstFingerprint = Constraints.Fingerprint
        Replayed = Constraints.WithCut(FirstCut)
        self.assertEqual(Replayed.Fingerprint, FirstFingerprint)
        self.assertEqual(Replayed, Constraints)
        self.assertEqual(
            Replayed.ObservedInterfaceConflictEvidence[0]
            .ObservationCount,
            1,
        )
        RecurringHigher = BuildCut(
            ("First", "Second", "Third"),
            ("Second", "Third"),
        )
        RecurringEdge = BuildCut(
            ("First", "Second", "Fourth"),
            ("First", "Second"),
        )
        Repeated = (
            Replayed
            .WithCut(RecurringHigher)
            .WithCut(RecurringEdge)
        )

        Advanced = Repeated.WithCut(SecondCut)
        self.assertTrue({
            ("First", "Second", "Third"),
            ("Fifth", "Fourth", "Sixth"),
        }.issubset(
            set(Advanced.HigherOrderSignalSets),
        ))
        self.assertTrue({
            ("First", "Second"),
            ("Fifth", "Fourth"),
        }.issubset(
            set(Advanced.ObservedInterfaceConflictEdges),
        ))
        self.assertEqual(
            Advanced.ActiveHigherOrderSignalSets,
            (("First", "Second", "Third"),),
        )
        self.assertEqual(
            Advanced.ActiveObservedInterfaceConflictEdges,
            (("First", "Second"),),
        )

        OneShot = (
            PlacementAssignmentConstraintSet()
            .WithCut(FirstCut)
            .WithCut(SecondCut)
        )
        self.assertEqual(len(OneShot.HigherOrderSignalSets), 2)
        self.assertEqual(len(OneShot.ObservedInterfaceConflictEdges), 2)
        self.assertEqual(OneShot.ActiveHigherOrderSignalSets, ())
        self.assertEqual(
            OneShot.ActiveObservedInterfaceConflictEdges,
            (),
        )

    def testRecurringHeuristicInterfaceConstraintRemainsActive(self) -> None:
        def BuildCut(
            Index: int,
            IncludeSecondaryEdge: bool = False,
        ) -> RoutingAssignmentCut:
            ObservedEdges = [[
                f"Left{Index}",
                f"Right{Index}",
            ]]
            if IncludeSecondaryEdge:
                ObservedEdges.append([
                    f"Middle{Index}",
                    f"Right{Index}",
                ])
            Cut = RoutingAssignmentCut.FromFailure(RoutingFailure(
                Reason=RoutingFailureReason.BoundaryEscapeInfeasible,
                Stage="ClusterBoundaryLease",
                Diagnostics={
                    "ConflictGraph": {
                        "Classification": "saturated-boundary-cut",
                        "ConflictSignals": [
                            f"Left{Index}",
                            f"Middle{Index}",
                            f"Right{Index}",
                        ],
                        "RelocationSignals": [
                            f"Left{Index}",
                            f"Middle{Index}",
                            f"Right{Index}",
                        ],
                        "ObservedPatternConflictEdges": ObservedEdges,
                    },
                },
            ))
            assert Cut is not None
            return Cut

        RecurringCut = BuildCut(0)
        Constraints = (
            PlacementAssignmentConstraintSet()
            .WithCut(RecurringCut)
            .WithCut(BuildCut(0, IncludeSecondaryEdge=True))
        )
        for Index in range(1, 6):
            Constraints = Constraints.WithCut(BuildCut(Index))
        self.assertIn(
            ("Left0", "Right0"),
            Constraints.ActiveObservedInterfaceConflictEdges,
        )
        self.assertIn(
            ("Left0", "Middle0", "Right0"),
            Constraints.ActiveHigherOrderSignalSets,
        )

    def testOverlappingHigherOrderCutsActivateStableThreeSignalCore(
        self,
    ) -> None:
        def BuildCut(
            Signals: tuple[str, ...],
        ) -> RoutingAssignmentCut:
            Cut = RoutingAssignmentCut.FromFailure(RoutingFailure(
                Reason=RoutingFailureReason.BoundaryEscapeInfeasible,
                Stage="ClusterBoundaryLease",
                Diagnostics={
                    "ConflictGraph": {
                        "Classification": "saturated-boundary-cut",
                        "ConflictSignals": list(Signals),
                        "RelocationSignals": list(Signals),
                    },
                },
            ))
            assert Cut is not None
            return Cut

        First = BuildCut(("Core0", "Core1", "Core2", "PeripheralA"))
        Second = BuildCut(("PeripheralB", "Core2", "Core1", "Core0"))
        Forward = (
            PlacementAssignmentConstraintSet()
            .WithCut(First)
            .WithCut(Second)
        )
        Reverse = (
            PlacementAssignmentConstraintSet()
            .WithCut(Second)
            .WithCut(First)
        )
        self.assertIn(
            ("Core0", "Core1", "Core2"),
            Forward.ActiveHigherOrderSignalSets,
        )
        self.assertEqual(Forward, Reverse)
        self.assertEqual(Forward.PairwiseConflictEdges, ())

        Renamed = (
            PlacementAssignmentConstraintSet()
            .WithCut(BuildCut(("A", "B", "C", "X")))
            .WithCut(BuildCut(("Y", "C", "B", "A")))
        )
        self.assertEqual(
            Renamed.ActiveHigherOrderSignalSets,
            (("A", "B", "C"),),
        )

    def testCutWorkingSetKeepsOnlyDirectRecurrentInterfaces(self) -> None:
        Cut = RoutingAssignmentCut(
            Classification=(
                RoutingAssignmentCutClassification
                .SaturatedBoundaryCut
            ),
            ConflictGraphJson="{}",
            ConflictSignals=("Current", "Shared"),
        )
        Constraints = PlacementAssignmentConstraintSet(
            PairwiseConflictEdges=(
                ("PriorExact", "Shared"),
                ("RemoteExactA", "RemoteExactB"),
            ),
            ActiveHigherOrderSignalSets=(
                ("Prior", "Shared", "Third"),
                ("RemoteA", "RemoteB", "RemoteC"),
            ),
            ActiveObservedInterfaceConflictEdges=(
                ("Prior", "Shared"),
                ("RemoteA", "RemoteB"),
            ),
        )

        WorkingSet = SelectPlacementConstraintWorkingSet(
            Cut,
            Constraints,
        )

        self.assertEqual(
            WorkingSet.PairwiseConflictEdges,
            (("PriorExact", "Shared"),),
        )
        self.assertEqual(
            WorkingSet.HigherOrderSignalSets,
            (("Prior", "Shared", "Third"),),
        )
        self.assertEqual(
            WorkingSet.ObservedInterfaceConflictEdges,
            (("Prior", "Shared"),),
        )

    def testCutWorkingSetIncludesOnlyExplicitPreviousFrontier(self) -> None:
        Current = RoutingAssignmentCut(
            Classification=(
                RoutingAssignmentCutClassification.SparseRegionRouteCut
            ),
            ConflictGraphJson="{}",
            ConflictSignals=("Current",),
        )
        Previous = RoutingAssignmentCut(
            Classification=(
                RoutingAssignmentCutClassification.SaturatedBoundaryCut
            ),
            ConflictGraphJson="{}",
            ConflictSignals=("PreviousA", "PreviousB"),
        )
        Older = RoutingAssignmentCut(
            Classification=(
                RoutingAssignmentCutClassification.SaturatedBoundaryCut
            ),
            ConflictGraphJson="{}",
            ConflictSignals=("OlderA", "OlderB"),
        )
        Constraints = PlacementAssignmentConstraintSet(
            PairwiseConflictEdges=(
                ("Current", "CurrentPeer"),
                ("PreviousA", "PreviousB"),
                ("OlderA", "OlderB"),
            ),
            ActiveHigherOrderSignalSets=(
                ("PreviousA", "PreviousB", "PreviousC"),
                ("OlderA", "OlderB", "OlderC"),
            ),
        )

        WorkingSet = SelectPlacementConstraintWorkingSet(
            Current,
            Constraints,
            (Current, Previous),
        )

        self.assertEqual(
            WorkingSet.PairwiseConflictEdges,
            (
                ("Current", "CurrentPeer"),
                ("PreviousA", "PreviousB"),
            ),
        )
        self.assertEqual(
            WorkingSet.HigherOrderSignalSets,
            (("PreviousA", "PreviousB", "PreviousC"),),
        )

    def testCutWorkingSetCanExpandOneRecurrentConnectedComponent(
        self,
    ) -> None:
        Current = RoutingAssignmentCut(
            Classification=(
                RoutingAssignmentCutClassification.SaturatedBoundaryCut
            ),
            ConflictGraphJson="{}",
            ConflictSignals=("Current", "BridgeA"),
        )
        Constraints = PlacementAssignmentConstraintSet(
            PairwiseConflictEdges=(
                ("BridgeA", "BridgeB"),
                ("RemoteA", "RemoteB"),
            ),
            ActiveHigherOrderSignalSets=(
                ("BridgeB", "BridgeC", "BridgeD"),
                ("RemoteA", "RemoteB", "RemoteC"),
            ),
            ActiveObservedInterfaceConflictEdges=(
                ("BridgeD", "Terminal"),
                ("RemoteB", "RemoteD"),
            ),
        )

        Direct = SelectPlacementConstraintWorkingSet(
            Current,
            Constraints,
        )
        Component = SelectPlacementConstraintWorkingSet(
            Current,
            Constraints,
            ExpandConnectedComponent=True,
        )

        self.assertEqual(
            Direct.PairwiseConflictEdges,
            (("BridgeA", "BridgeB"),),
        )
        self.assertEqual(
            Direct.ObservedInterfaceConflictEdges,
            (),
        )
        self.assertEqual(
            Component.PairwiseConflictEdges,
            (("BridgeA", "BridgeB"),),
        )
        self.assertEqual(
            Component.HigherOrderSignalSets,
            (("BridgeB", "BridgeC", "BridgeD"),),
        )
        self.assertEqual(
            Component.ObservedInterfaceConflictEdges,
            (("BridgeD", "Terminal"),),
        )

    def testCompleteProofGeometryWorkingSetKeepsOnlyPriorityPair(
        self,
    ) -> None:
        Current = RoutingAssignmentCut(
            Classification=(
                RoutingAssignmentCutClassification
                .RelocatedMultiPairConflict
            ),
            ConflictGraphJson="{}",
            ConflictSignals=(
                "Failed",
                "SelectedPeer",
                "UnrelatedPeer",
            ),
            PriorityRelocationSignals=("Failed", "SelectedPeer"),
            PairwiseConflictEdges=(
                ("Failed", "SelectedPeer"),
                ("Failed", "UnrelatedPeer"),
            ),
            CompleteAssignmentCutProof=True,
        )
        Previous = RoutingAssignmentCut(
            Classification=(
                RoutingAssignmentCutClassification.SaturatedBoundaryCut
            ),
            ConflictGraphJson="{}",
            ConflictSignals=("PreviousA", "PreviousB"),
        )
        Constraints = PlacementAssignmentConstraintSet(
            PairwiseConflictEdges=(
                ("Failed", "SelectedPeer"),
                ("Failed", "UnrelatedPeer"),
                ("PreviousA", "PreviousB"),
            ),
            ActiveHigherOrderSignalSets=(
                ("Failed", "SelectedPeer", "UnrelatedPeer"),
            ),
            ActiveObservedInterfaceConflictEdges=(
                ("Failed", "SelectedPeer"),
                ("Failed", "UnrelatedPeer"),
            ),
        )

        WorkingSet = SelectPlacementConstraintWorkingSet(
            Current,
            Constraints,
            (Previous,),
            ExpandConnectedComponent=True,
        )

        self.assertEqual(
            WorkingSet.PairwiseConflictEdges,
            (("Failed", "SelectedPeer"),),
        )
        self.assertEqual(WorkingSet.HigherOrderSignalSets, ())
        self.assertEqual(
            WorkingSet.ObservedInterfaceConflictEdges,
            (("Failed", "SelectedPeer"),),
        )

    def testPackedBoundaryLeasesIncludePrimaryTerminalBanks(self) -> None:
        Policy = LocalFirstPhysicalDesignPolicy
        Placement = PlacePcbGraph(
            self.ClusteredNetlist(),
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
            EnableClusterBoundaryLeases=True,
            EnableClusterInterfacePlacementFeasibility=True,
        )

        Requests = Placement.ClusterBoundaryLeaseRequests
        self.assertTrue(any(
            Request.Signal == "Left"
            and Request.SourceCluster == -1
            for Request in Requests
        ))
        self.assertTrue(any(
            Request.Signal == "Right"
            and Request.SourceCluster == -1
            for Request in Requests
        ))
        self.assertTrue(any(
            Request.Signal == "Result"
            and Request.TargetCluster == -1
            for Request in Requests
        ))

        LegacyPlacement = PlacePcbGraph(
            self.ClusteredNetlist(),
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
            EnableClusterBoundaryLeases=True,
            EnableClusterInterfacePlacementFeasibility=False,
        )
        self.assertFalse(any(
            Request.SourceCluster == -1 or Request.TargetCluster == -1
            for Request in LegacyPlacement.ClusterBoundaryLeaseRequests
        ))

    def testExternalInternalCutSeparatesTerminalFromInternalPins(
        self,
    ) -> None:
        Policy = LocalFirstPhysicalDesignPolicy
        Placement = PlacePcbGraph(
            self.ClusteredNetlist(),
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
            AssignmentConstraints=PlacementAssignmentConstraintSet(
                PairwiseConflictEdges=(("Left", "S0"),),
            ),
        )
        PlacedByName = {
            Gate.Name: Gate
            for Gate in Placement.Placed.PlacedGates
        }
        TerminalPin = PlacedByName["InputLeft"].OutputPin
        self.assertIsNotNone(TerminalPin)
        InternalPins = {
            Gate.OutputPin
            for Gate in Placement.Placed.PlacedGates
            if Gate.OutputPin is not None and "S0" in Gate.Outputs
        }
        InternalPins.update(
            Gate.InputPins[Index]
            for Gate in Placement.Placed.PlacedGates
            for Index, Signal in enumerate(Gate.Inputs)
            if Signal == "S0"
        )
        self.assertGreaterEqual(
            min(
                abs(TerminalPin[0] - Pin[0])
                + abs(TerminalPin[2] - Pin[2])
                for Pin in InternalPins
            ),
            3 + Policy.Placement.RoutingSpacing,
        )

    def testJointTopologyTerminalBankEnforcesGlobalPinSpacing(
        self,
    ) -> None:
        Policy = LocalFirstPhysicalDesignPolicy
        Placement = PlacePcbGraph(
            self.ClusteredNetlist(),
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
        )
        TerminalPins = []
        for Gate in Placement.Placed.PlacedGates:
            Kind = getattr(Gate.Kind, "value", Gate.Kind)
            if Kind == "INPUT":
                TerminalPins.append(Gate.OutputPin)
            elif Kind == "OUTPUT":
                TerminalPins.append(Gate.InputPins[0])
        MinimumSpacing = 3 + Policy.Placement.RoutingSpacing
        self.assertTrue(all(
            abs(First[0] - Second[0])
            + abs(First[2] - Second[2])
            >= MinimumSpacing
            for Index, First in enumerate(TerminalPins)
            for Second in TerminalPins[Index + 1:]
        ))

    def testFeedbackPartialTreeReleaseSkipsOnlyDiscardedSearches(
        self,
    ) -> None:
        self.assertTrue(ShouldReleasePartialLocalTreeBeforeSearch(
            ClusterCount=5,
            HasRelocationSignals=True,
            LocalTargetCount=1,
            TotalTargetCount=2,
        ))
        self.assertFalse(ShouldReleasePartialLocalTreeBeforeSearch(
            ClusterCount=4,
            HasRelocationSignals=True,
            LocalTargetCount=1,
            TotalTargetCount=2,
        ))
        self.assertFalse(ShouldReleasePartialLocalTreeBeforeSearch(
            ClusterCount=5,
            HasRelocationSignals=False,
            LocalTargetCount=1,
            TotalTargetCount=2,
        ))
        self.assertFalse(ShouldReleasePartialLocalTreeBeforeSearch(
            ClusterCount=5,
            HasRelocationSignals=True,
            LocalTargetCount=2,
            TotalTargetCount=2,
        ))

    def testExactPairEndpointsMoveBeyondAdjacentSlots(
        self,
    ) -> None:
        Module = ModuleIR(
            Name="ExactPairPlacement",
            Inputs=["Left", "Right", "Aux"],
            Outputs=["LeftResult", "RightResult", "AuxResult"],
            Gates=[
                Gate("InputLeft", GateKind.INPUT, ["Left"], []),
                Gate("InputRight", GateKind.INPUT, ["Right"], []),
                Gate("InputAux", GateKind.INPUT, ["Aux"], []),
                Gate(
                    "LeftNand",
                    GateKind.NAND,
                    ["LeftResult"],
                    ["Left", "Aux"],
                ),
                Gate(
                    "RightNand",
                    GateKind.NAND,
                    ["RightResult"],
                    ["Right", "Aux"],
                ),
                Gate(
                    "AuxNand",
                    GateKind.NAND,
                    ["AuxResult"],
                    ["Aux", "Aux"],
                ),
                Gate(
                    "OutputLeft",
                    GateKind.OUTPUT,
                    [],
                    ["LeftResult"],
                ),
                Gate(
                    "OutputRight",
                    GateKind.OUTPUT,
                    [],
                    ["RightResult"],
                ),
                Gate(
                    "OutputAux",
                    GateKind.OUTPUT,
                    [],
                    ["AuxResult"],
                ),
            ],
        )
        Clusters = (("LeftNand",), ("RightNand",), ("AuxNand",))
        VariantsByCluster = {
            ClusterIndex: (
                ClusterLayoutVariant(
                    Rotation=0,
                    MirrorX=False,
                    Positions={Name: (0, 0)},
                    Rotations={Name: 0},
                    Mirrors={Name: False},
                    Width=4,
                    Depth=4,
                    ActualGeometry={},
                    ElectricalGeometry={},
                ),
            )
            for ClusterIndex, (Name,) in enumerate(Clusters)
        }
        Cut = RoutingAssignmentCut.FromFailure(RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="InitialCandidateAssignment",
            Diagnostics={
                "ConflictGraph": {
                    "Classification": "mandatory-boundary-capacity-cut",
                    "ConflictSignals": ["Left", "Right"],
                    "RelocationSignals": ["Left", "Right"],
                    "PriorityRelocationSignals": ["Left", "Right"],
                    "PairwiseIncompatibleEdges": [["Left", "Right"]],
                },
            },
        ))
        self.assertIsNotNone(Cut)
        HigherOrderCut = RoutingAssignmentCut.FromFailure(RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="TrackAssignment",
            Diagnostics={
                "ConflictGraph": {
                    "Classification": "higher-order-placement-conflict",
                    "ConflictSignals": ["Left", "Right"],
                    "RelocationSignals": ["Left", "Right"],
                    "PriorityRelocationSignals": ["Left", "Right"],
                    "PairwiseIncompatibleEdges": [["Left", "Right"]],
                },
            },
        ))
        self.assertIsNotNone(HigherOrderCut)
        InitialAssignment = {
            0: (0, 0),
            1: (1, 0),
            2: (2, 0),
        }
        CumulativeConstraints = (
            PlacementAssignmentConstraintSet()
            .WithCut(HigherOrderCut)
            .WithCut(Cut)
        )

        Assignment, _Variants, Diagnostics = (
            OptimizeJointClusterPlacement(
                Module,
                Clusters,
                BuildTopologicalLevels(Module),
                VariantsByCluster,
                BeamWidth=16,
                PassLimit=2,
                RetainedCandidates=1,
                InitialAssignment=InitialAssignment,
                FixedSlotClusters=frozenset({0, 1}),
                AssignmentCut=Cut,
                AssignmentConstraints=CumulativeConstraints,
            )
        )

        PairDistance = (
            abs(Assignment[0][0] - Assignment[1][0])
            + abs(Assignment[0][1] - Assignment[1][1])
        )
        self.assertGreater(PairDistance, 1)
        self.assertEqual(Diagnostics["ExactPairClusterEdges"], [[0, 1]])
        self.assertEqual(
            Diagnostics["AssignmentConstraints"]["Fingerprint"],
            CumulativeConstraints.Fingerprint,
        )
        self.assertEqual(
            Diagnostics["EffectivePairwiseConflictEdges"],
            [["Left", "Right"]],
        )
        self.assertEqual(
            Diagnostics["HigherOrderProjectedClusterEdges"],
            [],
        )
        self.assertEqual(
            Diagnostics["RequestedFixedSlotClusters"],
            [0, 1],
        )
        self.assertEqual(Diagnostics["FixedSlotClusters"], [])
        self.assertEqual(
            Diagnostics["SelectedExactPairAdjacencyViolations"],
            0,
        )
        self.assertEqual(
            Diagnostics["SelectedScore"],
            Diagnostics["RetainedStates"][0]["SearchScore"],
        )

        SoftHigherOrderCut = RoutingAssignmentCut.FromFailure(
            RoutingFailure(
                Reason=RoutingFailureReason.TrackAssignmentConflict,
                Stage="TrackAssignment",
                Diagnostics={
                    "ConflictGraph": {
                        "Classification": "higher-order-placement-conflict",
                        "ConflictSignals": ["Left", "Right"],
                        "RelocationSignals": ["Left", "Right"],
                        "PriorityRelocationSignals": ["Left", "Right"],
                    },
                },
            )
        )
        self.assertIsNotNone(SoftHigherOrderCut)
        SoftAssignment, _SoftVariants, SoftDiagnostics = (
            OptimizeJointClusterPlacement(
                Module,
                Clusters,
                BuildTopologicalLevels(Module),
                VariantsByCluster,
                BeamWidth=16,
                PassLimit=2,
                RetainedCandidates=1,
                InitialAssignment=InitialAssignment,
                FixedSlotClusters=frozenset({0, 1}),
                AssignmentCut=SoftHigherOrderCut,
                AssignmentConstraints=(
                    PlacementAssignmentConstraintSet().WithCut(
                        SoftHigherOrderCut
                    )
                ),
            )
        )
        self.assertEqual(SoftAssignment, InitialAssignment)
        self.assertEqual(SoftDiagnostics["ExactPairClusterEdges"], [])
        self.assertEqual(
            SoftDiagnostics["HigherOrderProjectedClusterEdges"],
            [[0, 1]],
        )
        self.assertEqual(
            SoftDiagnostics["FixedSlotClusters"],
            [0, 1],
        )
        self.assertEqual(
            SoftDiagnostics["SelectedExactPairAdjacencyViolations"],
            0,
        )

    def testExplicitStructuredRepairFocusOverridesCumulativeCutSignals(
        self,
    ) -> None:
        Cut = RoutingAssignmentCut.FromFailure(
            RoutingStageError(
                RoutingFailure(
                    Reason=RoutingFailureReason.TrackAssignmentConflict,
                    Stage="TrackAssignment",
                    AffectedNets=("CurrentLeft", "CurrentRight"),
                    Diagnostics={
                        "ConflictGraph": {
                            "Classification": (
                                "portal-coverage-pair-conflict"
                            ),
                            "ConflictSignals": [
                                "CurrentLeft",
                                "CurrentRight",
                            ],
                            "RelocationSignals": [
                                "CurrentLeft",
                                "CurrentRight",
                            ],
                            "PriorityRelocationSignals": [
                                "CurrentLeft",
                                "CurrentRight",
                            ],
                            "PairwiseIncompatibleEdges": [[
                                "CurrentLeft",
                                "CurrentRight",
                            ]],
                        },
                    },
                )
            ).Failure,
        )
        self.assertIsNotNone(Cut)
        Constraints = PlacementAssignmentConstraintSet(
            PairwiseConflictEdges=(
                ("HistoricalRight", "HistoricalLeft"),
            ),
        )
        Explicit = frozenset(("PromotedLeft", "PromotedRight"))

        self.assertEqual(
            BuildEffectiveStructuredRelocationFocus(
                Cut,
                Constraints,
                Explicit,
                Explicit,
            ),
            (Explicit, Explicit),
        )
        self.assertEqual(
            BuildEffectiveStructuredRelocationFocus(
                Cut,
                Constraints,
                frozenset(),
                frozenset(),
            ),
            (
                frozenset(("CurrentLeft", "CurrentRight")),
                frozenset(("CurrentLeft", "CurrentRight")),
            ),
        )

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

        VariantsByCluster = {
            ClusterIndex: tuple(
                ClusterLayoutVariant(
                    Rotation=Rotation,
                    MirrorX=MirrorX,
                    Positions={Name: (0, 0)},
                    Rotations={Name: Rotation},
                    Mirrors={Name: MirrorX},
                    Width=4,
                    Depth=4,
                    ActualGeometry={},
                    ElectricalGeometry={},
                )
                for Rotation in (0, 90, 180, 270)
                for MirrorX in (False, True)
            )
            for ClusterIndex, (Name,) in enumerate(Clusters)
        }
        with self.assertRaisesRegex(
            RuntimeError,
            "joint-cluster-placement-candidate",
        ):
            OptimizeJointClusterPlacement(
                Module,
                Clusters,
                BuildTopologicalLevels(Module),
                VariantsByCluster,
                BeamWidth=8,
                PassLimit=3,
                RetainedCandidates=6,
                WorkCheck=StopAt(
                    "joint-cluster-placement-candidate"
                ),
            )

        with self.assertRaisesRegex(
            RuntimeError,
            "packed-access-repair-candidate",
        ):
            RepairPackedClusterAccess(
                tuple(InternalByName),
                InternalByName,
                {
                    Name: (0, 0)
                    for Name in InternalByName
                },
                {
                    Name: 0
                    for Name in InternalByName
                },
                {
                    Name: False
                    for Name in InternalByName
                },
                frozenset({
                    Signal
                    for GateValue in InternalByName.values()
                    for Signal in (
                        *GateValue.Inputs,
                        *GateValue.Outputs,
                    )
                }),
                BeamWidth=8,
                IncludeNearPortalConflicts=True,
                WorkCheck=StopAt(
                    "packed-access-repair-candidate"
                ),
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

    def testBoundaryEscapeChecksMergedForeignAccessClaims(self) -> None:
        Anchor = (0, 1, 0)
        ForeignPosition = (1, 1, 0)
        ResourceGraph = RoutingResourceGraph(
            ActualBlocks=frozenset(),
            ElectricalBlocks=frozenset(),
            SolidBlocks=frozenset(),
        )
        ForeignClaims = ResourceGraph.BuildRouteClaims((ForeignPosition,))
        Baseline = BuildLegalBoundaryEscapeSlots(
            {"Required"},
            {"Required": {Anchor}},
            ResourceGraph,
            {},
        )
        OwnClaims = BuildLegalBoundaryEscapeSlots(
            {"Required"},
            {"Required": {Anchor}},
            ResourceGraph,
            {"Required": ForeignClaims},
        )
        WithForeignClaims = BuildLegalBoundaryEscapeSlots(
            {"Required"},
            {"Required": {Anchor}},
            ResourceGraph,
            {"Other": ForeignClaims},
        )

        self.assertEqual(OwnClaims, Baseline)
        self.assertIn(ForeignPosition, Baseline["Required"])
        self.assertNotIn(ForeignPosition, WithForeignClaims["Required"])
        self.assertLess(
            len(WithForeignClaims["Required"]),
            len(Baseline["Required"]),
        )

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
        self.assertEqual(
            Result.LegalEscapeCandidateCounts,
            (("First", 1), ("Second", 1)),
        )
        self.assertEqual(
            Result.SingleCandidateBoundarySignals,
            ("First", "Second"),
        )

    def testPackedClusterRetainsLegalEscapeCandidateScarcity(self) -> None:
        Cluster = PackedNandCluster(
            ClusterId=3,
            MemberNands=("N0",),
            BoundarySignals=("Only", "Several"),
            InternalSignals=(),
            RelativePlacements={"N0": (0, 0, 0, False)},
            DirectConnections=(),
            LegalEscapeCandidateCounts=(
                ("Only", 1),
                ("Several", 4),
            ),
        )

        self.assertEqual(
            Cluster.SingleCandidateBoundarySignals,
            ("Only",),
        )

    def testMandatoryPreScreenRetainsJointDiagnosticsAndCachesStates(
        self,
    ) -> None:
        Netlist = self.SyntheticNetlist()
        Policy = LocalFirstPhysicalDesignPolicy
        PlacementCache._JointPlacementExactScreenCache.clear()
        PlacementCache._ExactStatePlacementGeometryCache.clear()
        PlacementCache._PlacementTopologyCache.clear()
        Phases = []
        SecondPhases = []
        Arguments = {
            "RoutingSpacing": Policy.Placement.RoutingSpacing,
            "PlacementPolicy": Policy.Placement,
            "PackingPolicy": Policy.NandPacking,
            "ClusterPolicy": Policy.Clustering,
            "MaximumBoundaryTerminals": (
                Policy.Organization.MaximumClusterEntrances
            ),
            "MaximumEntrancesPerSignal": (
                Policy.Organization.MaximumClusterEntrancesPerSignal
            ),
            "MandatoryAccessPreScreenOnly": True,
        }

        def StopDuringExactScreen(Diagnostics):
            if (
                Diagnostics["Phase"] == "joint-exact-screen-state"
                and Diagnostics["CandidateOrdinal"] == 2
            ):
                raise RuntimeError("exact screen slice expired")

        with self.assertRaisesRegex(
            RuntimeError,
            "exact screen slice expired",
        ):
            PlacePcbGraph(
                Netlist,
                WorkCheck=StopDuringExactScreen,
                **Arguments,
            )
        self.assertEqual(PlacementCache._JointPlacementExactScreenCache, {})
        PlacementCache._PlacementTopologyCache.clear()

        First = PlacePcbGraph(
            Netlist,
            WorkCheck=lambda Diagnostics: Phases.append(
                Diagnostics["Phase"]
            ),
            **Arguments,
        )
        Second = PlacePcbGraph(
            Netlist,
            WorkCheck=lambda Diagnostics: SecondPhases.append(
                Diagnostics["Phase"]
            ),
            **Arguments,
        )
        FirstDiagnostics = First.Placed.LocalRouteDiagnostics or {}
        SecondDiagnostics = Second.Placed.LocalRouteDiagnostics or {}
        FirstJoint = FirstDiagnostics["__JointClusterPlacement__"]
        SecondJoint = SecondDiagnostics["__JointClusterPlacement__"]
        ExactScreen = next(iter(
            PlacementCache._JointPlacementExactScreenCache.values()
        ))

        self.assertFalse(FirstJoint["ExactScreenCacheHit"])
        self.assertTrue(SecondJoint["ExactScreenCacheHit"])
        self.assertEqual(
            tuple(
                CandidateIndex
                for CandidateIndex, _Geometry
                in ExactScreen.CoreGeometryByCandidate
            ),
            tuple(
                int(State["CandidateIndex"])
                for State in FirstJoint["ExactLegalRetainedStates"]
            ),
        )
        self.assertTrue(all(
            Geometry
            for _CandidateIndex, Geometry
            in ExactScreen.CoreGeometryByCandidate
        ))
        self.assertNotIn("placement-topology-cache-hit", Phases)
        self.assertIn("placement-topology-cache-hit", SecondPhases)
        FirstGeometryCache = FirstJoint["ExactStatePlacementCache"]
        SecondGeometryCache = SecondJoint["ExactStatePlacementCache"]
        self.assertFalse(FirstGeometryCache["Hit"])
        self.assertTrue(SecondGeometryCache["Hit"])
        self.assertTrue(FirstGeometryCache["CoreGeometryAvailable"])
        self.assertFalse(FirstGeometryCache["CoreGeometryCacheHit"])
        self.assertTrue(SecondGeometryCache["CoreGeometryCacheHit"])
        self.assertEqual(
            FirstGeometryCache["Key"],
            SecondGeometryCache["Key"],
        )
        self.assertGreater(SecondGeometryCache["CachedGateCount"], 0)
        self.assertEqual(
            BuildPlacementFingerprint(First),
            BuildPlacementFingerprint(Second),
        )
        self.assertIsNot(
            First.Placed.PlacedGates[0],
            Second.Placed.PlacedGates[0],
        )
        self.assertIsNot(
            First.Placed.LocalRouteDiagnostics,
            Second.Placed.LocalRouteDiagnostics,
        )
        DifferentPolicy = PlacePcbGraph(
            Netlist,
            **{
                **Arguments,
                "PlacementPolicy": replace(
                    Policy.Placement,
                    TerminalBankOffsetX=(
                        Policy.Placement.TerminalBankOffsetX + 1
                    ),
                ),
            },
        )
        DifferentStatePhases = []
        DifferentState = PlacePcbGraph(
            Netlist,
            JointPlacementCandidateIndex=1,
            WorkCheck=lambda Diagnostics: DifferentStatePhases.append(
                Diagnostics["Phase"]
            ),
            **Arguments,
        )
        DifferentCoordinatedProfile = PlacePcbGraph(
            Netlist,
            CoordinatedCandidateDiversificationSignals=frozenset({
                "SyntheticCutSignal",
            }),
            **Arguments,
        )
        for Placement in (
            DifferentPolicy,
            DifferentState,
            DifferentCoordinatedProfile,
        ):
            Cache = Placement.Placed.LocalRouteDiagnostics[
                "__JointClusterPlacement__"
            ]["ExactStatePlacementCache"]
            self.assertFalse(Cache["Hit"])
            self.assertNotEqual(
                Cache["Key"],
                FirstGeometryCache["Key"],
            )
        DifferentStateCache = DifferentState.Placed.LocalRouteDiagnostics[
            "__JointClusterPlacement__"
        ]["ExactStatePlacementCache"]
        self.assertTrue(DifferentStateCache["CoreGeometryCacheHit"])
        self.assertIn(
            "exact-state-core-geometry-reused",
            DifferentStatePhases,
        )
        self.assertNotIn("placement-commit", DifferentStatePhases)
        FullArguments = {
            Key: Value
            for Key, Value in Arguments.items()
            if Key != "MandatoryAccessPreScreenOnly"
        }
        FirstFull = PlacePcbGraph(Netlist, **FullArguments)
        SecondFull = PlacePcbGraph(Netlist, **FullArguments)
        FirstFullCache = FirstFull.Placed.LocalRouteDiagnostics[
            "__JointClusterPlacement__"
        ]["ExactStatePlacementCache"]
        SecondFullCache = SecondFull.Placed.LocalRouteDiagnostics[
            "__JointClusterPlacement__"
        ]["ExactStatePlacementCache"]
        self.assertFalse(FirstFullCache["Hit"])
        self.assertTrue(SecondFullCache["Hit"])
        self.assertEqual(
            SecondFullCache["CachedGateCount"],
            len(SecondFull.Placed.PlacedGates),
        )
        self.assertTrue(any(
            GateValue.Kind in {"INPUT", "OUTPUT"}
            for GateValue in SecondFull.Placed.PlacedGates
        ))
        self.assertEqual(
            BuildPlacementFingerprint(FirstFull),
            BuildPlacementFingerprint(SecondFull),
        )
        self.assertTrue(all(
            FirstGate is not SecondGate
            for FirstGate, SecondGate in zip(
                FirstFull.Placed.PlacedGates,
                SecondFull.Placed.PlacedGates,
            )
        ))
        FirstFull.Placed.LocalRouteDiagnostics["CacheMutation"] = True
        self.assertNotIn(
            "CacheMutation",
            SecondFull.Placed.LocalRouteDiagnostics,
        )
        self.assertEqual(
            FirstJoint["RetainedStates"],
            SecondJoint["RetainedStates"],
        )
        SelectedState = next(
            State
            for State in SecondJoint["RetainedStates"]
            if State["CandidateIndex"] == 0
        )
        self.assertEqual(
            SecondJoint["SelectedScore"],
            SelectedState["SearchScore"],
        )
        self.assertEqual(
            len(FirstJoint["ExactLegalRetainedStates"]),
            len(FirstJoint["RetainedStates"]),
        )
        self.assertIn("joint-exact-screen-state", Phases)
        self.assertIn(
            "exact-state-core-geometry-reused",
            Phases,
        )
        self.assertNotIn("placement-commit", Phases)
        self.assertIn(
            "exact-state-placement-cache-hit",
            SecondPhases,
        )
        self.assertEqual(
            FirstDiagnostics["__MandatoryAccessPreScreen__"][
                "TerminalsIncluded"
            ],
            False,
        )
        self.assertIn("__InterClusterGaps__", FirstDiagnostics)
        self.assertEqual(First.Placed.LocalRouteClaims, ())

    def testPlacementScoringOnlyRetainsTerminalInclusiveGeometry(
        self,
    ) -> None:
        Policy = LocalFirstPhysicalDesignPolicy
        Phases = []
        Placement = PlacePcbGraph(
            self.SyntheticNetlist(),
            RoutingSpacing=6,
            PlacementPolicy=Policy.Placement,
            PackingPolicy=replace(
                Policy.NandPacking,
                GraphBeamEnabled=False,
                EnableVerticalClusterStacking=False,
            ),
            ClusterPolicy=Policy.Clustering,
            MaximumBoundaryTerminals=16,
            MaximumEntrancesPerSignal=2,
            PlacementScoringOnly=True,
            WorkCheck=lambda Diagnostics: Phases.append(
                Diagnostics["Phase"]
            ),
        )
        Diagnostics = Placement.Placed.LocalRouteDiagnostics or {}

        self.assertEqual(
            Diagnostics["__DeferredLocalRouting__"],
            {
                "Enabled": True,
                "ScoringOnly": True,
                "TerminalsIncluded": True,
                "FixedPinAccessClaimsIncluded": True,
                "LocalRouteCandidateSearchDeferred": True,
                "LocalRoutePathSearchDeferred": True,
            },
        )
        self.assertEqual(Placement.Placed.LocalRouteClaims, ())
        self.assertTrue(Placement.PackedClusters)
        self.assertTrue(Placement.SignalOrder)
        self.assertGreater(Placement.LayerCount, 0)
        self.assertTrue(any(
            GateValue.Kind in {"INPUT", "OUTPUT"}
            for GateValue in Placement.Placed.PlacedGates
        ))
        self.assertIn("terminal-placement-complete", Phases)
        self.assertIn("local-access-geometry", Phases)
        self.assertIn("boundary-capacity", Phases)
        self.assertNotIn("local-route-signal", Phases)
        self.assertEqual(Phases[-1], "complete")

    def testPlacementScoringUsesTheSameFixedPinBoundaryScarcity(
        self,
    ) -> None:
        Policy = LocalFirstPhysicalDesignPolicy
        Arguments = {
            "RoutingSpacing": 6,
            "PlacementPolicy": Policy.Placement,
            "PackingPolicy": replace(
                Policy.NandPacking,
                GraphBeamEnabled=False,
                EnableVerticalClusterStacking=False,
            ),
            "ClusterPolicy": Policy.Clustering,
            "MaximumBoundaryTerminals": 16,
            "MaximumEntrancesPerSignal": 2,
        }
        OriginalBuildLegalBoundaryEscapeSlots = (
            ChannelsModule.BuildLegalBoundaryEscapeSlots
        )
        CapturedFixedPinAccess = []

        def CaptureFixedPinAccess(
            BoundarySignals,
            AccessPositionsBySignal,
            ResourceGraph,
            AccessClaimsBySignal,
            **KeywordArguments,
        ):
            CapturedFixedPinAccess.append({
                Signal: tuple(sorted(Positions))
                for Signal, Positions in sorted(
                    AccessPositionsBySignal.items()
                )
            })
            return OriginalBuildLegalBoundaryEscapeSlots(
                BoundarySignals,
                AccessPositionsBySignal,
                ResourceGraph,
                AccessClaimsBySignal,
                **KeywordArguments,
            )

        with patch(
            "Compiler.Placement.Core.CommitRouting.BuildLegalBoundaryEscapeSlots",
            side_effect=CaptureFixedPinAccess,
        ):
            Full = PlacePcbGraph(
                self.SyntheticNetlist(),
                **Arguments,
            )
            FullFixedPinAccess = tuple(CapturedFixedPinAccess)
            CapturedFixedPinAccess.clear()
            Scoring = PlacePcbGraph(
                self.SyntheticNetlist(),
                PlacementScoringOnly=True,
                **Arguments,
            )
            ScoringFixedPinAccess = tuple(CapturedFixedPinAccess)

        def GateGeometry(Placement):
            return tuple(
                (
                    GateValue.Name,
                    GateValue.Kind,
                    GateValue.X,
                    GateValue.Y,
                    GateValue.Z,
                    GateValue.Rotation,
                    GateValue.MirrorX,
                    GateValue.OutputPin,
                    GateValue.InputPins,
                )
                for GateValue in Placement.Placed.PlacedGates
            )

        def BoundaryRanking(Placement):
            return tuple(
                (
                    Cluster.BoundarySignals,
                    Cluster.BoundaryDemandRecords,
                    Cluster.BoundaryCapacityRecords,
                    Cluster.BoundaryOverflow,
                    Cluster.PinScarcityCount,
                    Cluster.LegalEscapeCandidateCounts,
                    Cluster.OrientationRotation,
                    Cluster.OrientationMirrorX,
                )
                for Cluster in Placement.PackedClusters
            )

        self.assertTrue(Full.Placed.LocalRouteClaims)
        self.assertNotIn(
            "__DeferredLocalRouting__",
            Full.Placed.LocalRouteDiagnostics,
        )
        self.assertEqual(Scoring.Placed.LocalRouteClaims, ())
        self.assertEqual(GateGeometry(Full), GateGeometry(Scoring))
        self.assertEqual(Full.SignalOrder, Scoring.SignalOrder)
        self.assertEqual(Full.LayerCount, Scoring.LayerCount)
        self.assertEqual(FullFixedPinAccess, ScoringFixedPinAccess)
        self.assertEqual(BoundaryRanking(Full), BoundaryRanking(Scoring))

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

    def testSuppressedStackMembersReceiveDistinctColumns(self) -> None:
        Assignment, ColumnCount = RelocateClusterSlots(
            {0: (0, 0), 1: (0, 0), 2: (0, 0)},
            1,
            (0, 1, 2),
        )

        self.assertEqual(
            tuple(Assignment[Index] for Index in range(3)),
            ((1, 0), (2, 0), (3, 0)),
        )
        self.assertEqual(ColumnCount, 4)

    def testOffsetMultiClusterRelocationSwapsEverySelectedSlot(self) -> None:
        Assignment, ColumnCount = RelocateClusterSlots(
            {0: (0, 0), 1: (1, 0), 2: (0, 1)},
            2,
            (0, 2),
            RelocationOffset=7,
        )

        self.assertEqual(Assignment[0], (0, 1))
        self.assertEqual(Assignment[1], (1, 0))
        self.assertEqual(Assignment[2], (0, 0))
        self.assertEqual(ColumnCount, 2)

    def testFourClusterRelocationComposesTwoPairSwaps(self) -> None:
        Assignment, ColumnCount = RelocateClusterSlots(
            {
                0: (0, 0),
                1: (1, 0),
                2: (0, 1),
                3: (1, 1),
            },
            2,
            (0, 1, 2, 3),
            RelocationOffset=11,
        )

        self.assertEqual(
            Assignment,
            {
                0: (1, 0),
                1: (0, 0),
                2: (1, 1),
                3: (0, 1),
            },
        )

    def testExactPortfolioSlotRotationChangesEvenClusterOwnership(self) -> None:
        Assignment, ColumnCount = RelocateClusterSlots(
            {
                0: (0, 0),
                1: (1, 0),
                2: (0, 1),
                3: (1, 1),
            },
            2,
            (0, 1, 2, 3),
            RelocationOffset=1,
            RotateExactPortfolioSlots=True,
        )

        self.assertEqual(
            Assignment,
            {
                0: (1, 0),
                1: (0, 1),
                2: (1, 1),
                3: (0, 0),
            },
        )
        self.assertEqual(ColumnCount, 2)
        self.assertEqual(ColumnCount, 2)

    def testInterClusterGapPlanUsesDistinctCrossingSignals(self) -> None:
        Module = self.ClusteredNetlist().Modules["ClusteredBoundaryGraph"]
        Demand = BuildInterClusterBoundaryDemand(
            Module,
            (("N0",), ("N1",), ("N2",)),
            {0: (0, 0), 1: (1, 0), 2: (2, 1)},
        )

        self.assertEqual(
            tuple(
                (Record.Axis, Record.BoundaryIndex, Record.Signals)
                for Record in Demand
            ),
            (
                ("X", 0, ("S0",)),
                ("X", 1, ("S1",)),
                ("Z", 0, ("S1",)),
            ),
        )
        Plan = BuildInterClusterGapPlan(
            Demand,
            ColumnCount=3,
            RowCount=2,
            RoutingSpacing=LocalFirstPhysicalDesignPolicy.Placement.RoutingSpacing,
            TrackPitch=DefaultRedstoneRoutingTechnology.TrackPitch,
            Enabled=True,
        )
        ExpectedSpacing = min(
            LocalFirstPhysicalDesignPolicy.Placement.RoutingSpacing,
            DefaultRedstoneRoutingTechnology.TrackPitch,
        )
        self.assertEqual(
            Plan.ColumnSpacingByBoundary(),
            {0: ExpectedSpacing, 1: ExpectedSpacing},
        )
        self.assertEqual(Plan.RowSpacingByBoundary(), {0: ExpectedSpacing})

    def testInterClusterBoundaryDemandDeduplicatesFanoutAtOneCut(self) -> None:
        Module = ModuleIR(
            Name="FanoutBoundaryGraph",
            Inputs=["Left", "Right"],
            Outputs=["First", "Second"],
            Gates=[
                Gate("Source", GateKind.NAND, ["Shared"], ["Left", "Right"]),
                Gate("FirstConsumer", GateKind.NAND, ["First"], ["Shared", "Left"]),
                Gate("SecondConsumer", GateKind.NAND, ["Second"], ["Shared", "Right"]),
            ],
        )
        Demand = BuildInterClusterBoundaryDemand(
            Module,
            (("Source",), ("FirstConsumer",), ("SecondConsumer",)),
            {0: (0, 0), 1: (1, 0), 2: (1, 1)},
        )

        self.assertEqual(
            next(
                Record.Signals
                for Record in Demand
                if Record.Axis == "X" and Record.BoundaryIndex == 0
            ),
            ("Shared",),
        )

    def testClusterBoundaryContractsScoreSlotCutCapacity(self) -> None:
        Module = ModuleIR(
            Name="ClusterBoundaryContractGraph",
            Inputs=["Left", "Right"],
            Outputs=["First", "Second"],
            Gates=[
                Gate("Source", GateKind.NAND, ["Shared"], ["Left", "Right"]),
                Gate("FirstConsumer", GateKind.NAND, ["First"], ["Shared", "Left"]),
                Gate("SecondConsumer", GateKind.NAND, ["Second"], ["Shared", "Right"]),
            ],
        )
        Bundles = BuildClusterBoundaryBundles(
            Module,
            (("Source",), ("FirstConsumer",), ("SecondConsumer",)),
        )
        self.assertEqual(
            tuple(
                (Bundle.SourceCluster, Bundle.TargetCluster, Bundle.Signals)
                for Bundle in Bundles
            ),
            ((0, 1, ("Shared",)), (0, 2, ("Shared",))),
        )
        Overloaded = ScoreClusterBoundaryContracts(
            Bundles,
            {0: (0, 0), 1: (1, 0), 2: (1, 1)},
            BoundaryCapacity=0 + 1,
        )
        Separated = ScoreClusterBoundaryContracts(
            Bundles,
            {0: (0, 0), 1: (1, 0), 2: (2, 1)},
            BoundaryCapacity=1,
        )
        self.assertEqual(Overloaded.OverflowLanes, 0)
        self.assertGreater(Separated.TotalBoundaryDemand, Overloaded.TotalBoundaryDemand)

    def testInterClusterGapPlanCapsDemandAndPreservesUniformFallback(self) -> None:
        # Use a direct record here to cover a multi-lane cut without tying the
        # spacing policy to any circuit or generated signal naming scheme.
        MultiLaneDemand = (
            InterClusterBoundaryDemand("X", 0, ("First", "Second")),
        )
        Compact = BuildInterClusterGapPlan(
            MultiLaneDemand,
            ColumnCount=2,
            RowCount=1,
            RoutingSpacing=LocalFirstPhysicalDesignPolicy.Placement.RoutingSpacing,
            TrackPitch=DefaultRedstoneRoutingTechnology.TrackPitch,
            Enabled=True,
        )
        Uniform = BuildInterClusterGapPlan(
            MultiLaneDemand,
            ColumnCount=2,
            RowCount=1,
            RoutingSpacing=LocalFirstPhysicalDesignPolicy.Placement.RoutingSpacing,
            TrackPitch=DefaultRedstoneRoutingTechnology.TrackPitch,
            Enabled=False,
        )
        Empty = BuildInterClusterGapPlan(
            (),
            ColumnCount=2,
            RowCount=1,
            RoutingSpacing=LocalFirstPhysicalDesignPolicy.Placement.RoutingSpacing,
            TrackPitch=DefaultRedstoneRoutingTechnology.TrackPitch,
            Enabled=True,
        )
        ExpectedSpacing = LocalFirstPhysicalDesignPolicy.Placement.RoutingSpacing
        self.assertEqual(Compact.ColumnSpacingByBoundary(), {0: ExpectedSpacing})
        self.assertEqual(Uniform.ColumnSpacingByBoundary(), {0: ExpectedSpacing})
        self.assertEqual(Empty.ColumnSpacingByBoundary(), {0: 0})

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
            "Compiler.Placement.Core.CommitRouting.EvaluateHardBoundaryFeasibility",
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
                "unpacked-spacing-6",
                "unpacked-configured-spacing",
                "configured-packing",
                "graph-beam-direct-only",
                "spacing-4",
                "spacing-6",
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
                    "Compiler.Placement.Core.CommitRouting.BuildBoundaryCapacityRecords",
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

    def testTransactionalEndpointRepairPreservesUnrelatedGeometryAndClaims(
        self,
    ) -> None:
        Module = ModuleIR(
            Name="TransactionalRepair",
            Gates=[
                Gate(
                    "Endpoint",
                    GateKind.NAND,
                    ["RepairOutput"],
                    ["RepairInput", "Other"],
                ),
                Gate(
                    "Unrelated",
                    GateKind.NAND,
                    ["Keep"],
                    ["Other", "Other"],
                ),
            ],
        )
        Endpoint = BuildPlacedGate(Module.Gates[0], 0, 1, 0, 0, False)
        Unrelated = BuildPlacedGate(Module.Gates[1], 12, 1, 0, 0, False)
        Claims = (
            SimpleNamespace(Signal="RepairInput", ClusterId=0),
            SimpleNamespace(Signal="Keep", ClusterId=0),
        )
        Source = PcbPlacement(
            Placed=PlacedDesign(
                Module=Module,
                PlacedGates=[Endpoint, Unrelated],
                LocalRouteClaims=Claims,
                LocalNetBranches={
                    "RepairInput": ((0, 1, 0),),
                    "Keep": ((12, 1, 0),),
                },
            ),
            Clusters=(("Endpoint", "Unrelated"),),
            SignalOrder=("Keep", "Other", "RepairInput", "RepairOutput"),
            LayerCount=3,
        )
        SourceProfile = MandatoryAccessConflictProfile(
            OwnershipFingerprint="source-ownership",
            ConflictFingerprint="",
            OwnershipRecords=(),
            CrossConflicts=(),
            SelfConflicts=(),
        )
        CandidateProfile = replace(
            SourceProfile,
            OwnershipFingerprint="candidate-ownership",
        )

        def Repair(
            Names,
            _InternalByName,
            Positions,
            _Rotations,
            Mirrors,
            _RequiredSignals,
            _BeamWidth,
            **_KeywordArguments,
        ):
            RepairedMirrors = dict(Mirrors)
            RepairedMirrors["Endpoint"] = True
            return dict(Positions), RepairedMirrors, {
                "BaselineConflictCount": 1,
                "FinalConflictCount": 0,
            }

        with (
            patch.object(RepairModule, "RepairPackedClusterAccess", Repair),
            patch.object(RepairModule, "PcbGatesConflict", return_value=False),
            patch.object(ClustersModule, "BuildPlacedCellGeometry"),
            patch.object(
                RepairModule,
                "MeasureMandatoryAccessConflictProfile",
                side_effect=(SourceProfile, CandidateProfile),
            ),
        ):
            Result = BuildTransactionalClusterEndpointRepair(
                Source,
                frozenset({"RepairInput"}),
            )

        self.assertTrue(Result.Accepted)
        self.assertIsNotNone(Result.Placement)
        Candidate = Result.Placement
        assert Candidate is not None
        CandidateByName = {
            GateValue.Name: GateValue
            for GateValue in Candidate.Placed.PlacedGates
        }
        self.assertTrue(CandidateByName["Endpoint"].MirrorX)
        self.assertEqual(
            (
                CandidateByName["Unrelated"].X,
                CandidateByName["Unrelated"].Y,
                CandidateByName["Unrelated"].Z,
                CandidateByName["Unrelated"].Rotation,
                CandidateByName["Unrelated"].MirrorX,
            ),
            (
                Unrelated.X,
                Unrelated.Y,
                Unrelated.Z,
                Unrelated.Rotation,
                Unrelated.MirrorX,
            ),
        )
        self.assertEqual(
            tuple(Claim.Signal for Claim in Candidate.Placed.LocalRouteClaims),
            ("Keep",),
        )
        self.assertNotIn(
            "RepairInput",
            Candidate.Placed.LocalNetBranches,
        )
        self.assertEqual(
            Result.Diagnostics["Reason"],
            "access-distinct-local-eco",
        )

    def testTransactionalEndpointRepairRigidlyRotatesPriorityMacro(
        self,
    ) -> None:
        Module = ModuleIR(
            Name="TransactionalRigidRotation",
            Gates=[
                Gate(
                    "FirstEndpoint",
                    GateKind.NAND,
                    ["FirstOutput"],
                    ["RepairInput", "Other"],
                ),
                Gate(
                    "SecondEndpoint",
                    GateKind.NAND,
                    ["SecondOutput"],
                    ["RepairInput", "Other"],
                ),
                Gate(
                    "MacroMember",
                    GateKind.NAND,
                    ["MacroOutput"],
                    ["Other", "Other"],
                ),
                Gate(
                    "EnvelopeGuard",
                    GateKind.NAND,
                    ["GuardOutput"],
                    ["GuardInput", "GuardInput"],
                ),
                Gate(
                    "NegativeEnvelopeGuard",
                    GateKind.NAND,
                    ["NegativeGuardOutput"],
                    ["GuardInput", "GuardInput"],
                ),
            ],
        )
        FirstEndpoint = BuildPlacedGate(
            Module.Gates[0],
            0,
            1,
            0,
            0,
            False,
        )
        SecondEndpoint = BuildPlacedGate(
            Module.Gates[1],
            12,
            1,
            0,
            0,
            False,
        )
        MacroMember = BuildPlacedGate(
            Module.Gates[2],
            24,
            1,
            0,
            0,
            False,
        )
        EnvelopeGuard = BuildPlacedGate(
            Module.Gates[3],
            40,
            1,
            40,
            0,
            False,
        )
        NegativeEnvelopeGuard = BuildPlacedGate(
            Module.Gates[4],
            -20,
            1,
            -20,
            0,
            False,
        )
        Source = PcbPlacement(
            Placed=PlacedDesign(
                Module=Module,
                PlacedGates=[
                    FirstEndpoint,
                    SecondEndpoint,
                    MacroMember,
                    EnvelopeGuard,
                    NegativeEnvelopeGuard,
                ],
            ),
            Clusters=((
                "FirstEndpoint",
                "SecondEndpoint",
                "MacroMember",
            ),),
            SignalOrder=(
                "FirstOutput",
                "GuardInput",
                "GuardOutput",
                "MacroOutput",
                "NegativeGuardOutput",
                "Other",
                "RepairInput",
                "SecondOutput",
            ),
            LayerCount=3,
        )
        SourceProfile = MandatoryAccessConflictProfile(
            OwnershipFingerprint="source-ownership",
            ConflictFingerprint="",
            OwnershipRecords=(),
            CrossConflicts=(),
            SelfConflicts=(),
        )
        CandidateProfile = replace(
            SourceProfile,
            OwnershipFingerprint="candidate-ownership",
        )
        RigidVariant = ClusterLayoutVariant(
            Rotation=180,
            MirrorX=False,
            Positions={
                "FirstEndpoint": (0, 0),
                "SecondEndpoint": (12, 0),
            },
            Rotations={
                "FirstEndpoint": 180,
                "SecondEndpoint": 180,
            },
            Mirrors={
                "FirstEndpoint": False,
                "SecondEndpoint": False,
            },
            Width=15,
            Depth=4,
            ActualGeometry={},
            ElectricalGeometry={},
        )

        def Repair(
            _Names,
            _InternalByName,
            Positions,
            _Rotations,
            Mirrors,
            _RequiredSignals,
            _BeamWidth,
            **_KeywordArguments,
        ):
            return dict(Positions), dict(Mirrors), {
                "BaselineConflictCount": 1,
                "FinalConflictCount": 0,
            }

        with (
            patch.object(RepairModule, "RepairPackedClusterAccess", Repair),
            patch.object(
                RepairModule,
                "TransformPackedClusterLayout",
                return_value=RigidVariant,
            ) as Transform,
            patch.object(RepairModule, "PcbGatesConflict", return_value=False),
            patch.object(
                RepairModule,
                "CountMandatoryAccessConflicts",
                return_value=0,
            ),
            patch.object(RepairModule, "BuildPlacedCellGeometry"),
            patch.object(
                RepairModule,
                "MeasureMandatoryAccessConflictProfile",
                side_effect=(SourceProfile, CandidateProfile),
            ),
        ):
            Result = BuildTransactionalClusterEndpointRepair(
                Source,
                frozenset({"RepairInput"}),
                RepairEndpointGateNames=frozenset({
                    "FirstEndpoint",
                    "SecondEndpoint",
                }),
                RepairVariant=7,
            )

        self.assertTrue(Result.Accepted)
        self.assertIsNotNone(Result.Placement)
        Transform.assert_called_once()
        Candidate = Result.Placement
        assert Candidate is not None
        CandidateByName = {
            GateValue.Name: GateValue
            for GateValue in Candidate.Placed.PlacedGates
        }
        self.assertEqual(
            (
                CandidateByName["FirstEndpoint"].X,
                CandidateByName["FirstEndpoint"].Z,
                CandidateByName["FirstEndpoint"].Rotation,
            ),
            (0, 0, 180),
        )
        self.assertEqual(
            (
                CandidateByName["SecondEndpoint"].X,
                CandidateByName["SecondEndpoint"].Z,
                CandidateByName["SecondEndpoint"].Rotation,
            ),
            (12, 0, 180),
        )
        self.assertEqual(
            (
                CandidateByName["MacroMember"].X,
                CandidateByName["MacroMember"].Z,
                CandidateByName["MacroMember"].Rotation,
            ),
            (24, 0, 0),
        )
        self.assertEqual(
            (
                CandidateByName["NegativeEnvelopeGuard"].X,
                CandidateByName["NegativeEnvelopeGuard"].Z,
                CandidateByName["NegativeEnvelopeGuard"].Rotation,
            ),
            (-20, -20, 0),
        )
        self.assertEqual(
            (
                CandidateByName["EnvelopeGuard"].X,
                CandidateByName["EnvelopeGuard"].Z,
                CandidateByName["EnvelopeGuard"].Rotation,
            ),
            (40, 40, 0),
        )
        ClusterDiagnostics = Result.Diagnostics["Clusters"]["0"]
        self.assertTrue(
            ClusterDiagnostics["PriorityEndpointRigidRotation"]
        )
        self.assertEqual(
            ClusterDiagnostics["PriorityEndpointRotationDelta"],
            180,
        )
        self.assertEqual(
            ClusterDiagnostics["PriorityEndpointRigidAnchorOffset"],
            [0, 0],
        )
        self.assertEqual(
            Result.Diagnostics["RigidMacroGateNames"],
            ["FirstEndpoint", "SecondEndpoint"],
        )

    def testTransactionalEndpointRepairRollsBackEnvelopeGrowth(self) -> None:
        Module = ModuleIR(
            Name="TransactionalRollback",
            Gates=[
                Gate(
                    "Endpoint",
                    GateKind.NAND,
                    ["RepairOutput"],
                    ["RepairInput", "Other"],
                ),
            ],
        )
        Endpoint = BuildPlacedGate(Module.Gates[0], 0, 1, 0, 0, False)
        Source = PcbPlacement(
            Placed=PlacedDesign(Module=Module, PlacedGates=[Endpoint]),
            Clusters=(("Endpoint",),),
            SignalOrder=("Other", "RepairInput", "RepairOutput"),
            LayerCount=3,
        )

        def Repair(
            Names,
            _InternalByName,
            Positions,
            _Rotations,
            Mirrors,
            _RequiredSignals,
            _BeamWidth,
            **_KeywordArguments,
        ):
            RepairedPositions = dict(Positions)
            RepairedPositions["Endpoint"] = (20, 0)
            return RepairedPositions, dict(Mirrors), {
                "BaselineConflictCount": 1,
                "FinalConflictCount": 0,
            }

        with patch.object(
            RepairModule,
            "RepairPackedClusterAccess",
            Repair,
        ):
            Result = BuildTransactionalClusterEndpointRepair(
                Source,
                frozenset({"RepairInput"}),
            )

        self.assertFalse(Result.Accepted)
        self.assertIsNone(Result.Placement)
        self.assertEqual(Result.Diagnostics["Reason"], "global-envelope-growth")
        self.assertEqual(Source.Placed.PlacedGates[0].X, 0)

    def testAccessDistinctRepairMovesEndpointWithoutLocalConflict(self) -> None:
        Endpoint = Gate(
            "Endpoint",
            GateKind.NAND,
            ["RepairOutput"],
            ["RepairInput", "Other"],
        )
        with (
            patch.object(
                MandatoryAccessModule,
                "CountPackedAccessEscapeConflicts",
                return_value=0,
            ),
            patch.object(
                MandatoryAccessModule,
                "CountMandatoryAccessConflicts",
                return_value=0,
            ),
            patch.object(MandatoryAccessModule, "PcbGatesConflict", return_value=False),
        ):
            Positions, Mirrors, Diagnostics = RepairPackedClusterAccess(
                ("Endpoint",),
                {"Endpoint": Endpoint},
                {"Endpoint": (5, 7)},
                {"Endpoint": 0},
                {"Endpoint": False},
                frozenset({"RepairInput"}),
                BeamWidth=4,
                IncludeNearPortalConflicts=True,
                NormalizeOrigin=False,
                RequireAccessDistinctGeometry=True,
            )

        self.assertEqual(Positions["Endpoint"], (5, 7))
        self.assertTrue(Mirrors["Endpoint"])
        self.assertEqual(Diagnostics["BaselineConflictCount"], 0)
        self.assertEqual(Diagnostics["FinalConflictCount"], 0)
        with (
            patch.object(
                MandatoryAccessModule,
                "CountPackedAccessEscapeConflicts",
                return_value=0,
            ),
            patch.object(
                MandatoryAccessModule,
                "CountMandatoryAccessConflicts",
                return_value=0,
            ),
            patch.object(MandatoryAccessModule, "PcbGatesConflict", return_value=False),
        ):
            AlternatePositions, AlternateMirrors, AlternateDiagnostics = (
                RepairPackedClusterAccess(
                    ("Endpoint",),
                    {"Endpoint": Endpoint},
                    {"Endpoint": (5, 7)},
                    {"Endpoint": 0},
                    {"Endpoint": False},
                    frozenset({"RepairInput"}),
                    BeamWidth=4,
                    IncludeNearPortalConflicts=True,
                    NormalizeOrigin=False,
                    RequireAccessDistinctGeometry=True,
                    AccessDistinctVariant=1,
                )
            )
        self.assertNotEqual(
            (AlternatePositions["Endpoint"], AlternateMirrors["Endpoint"]),
            (Positions["Endpoint"], Mirrors["Endpoint"]),
        )
        self.assertGreaterEqual(
            AlternateDiagnostics["AccessDistinctVariantCount"],
            2,
        )

    def testTransactionalPortfolioChangesOneTouchedClusterPerState(
        self,
    ) -> None:
        Module = ModuleIR(
            Name="OneClusterPerRepair",
            Gates=[
                Gate(
                    "First",
                    GateKind.NAND,
                    ["FirstOut"],
                    ["RepairInput", "Other"],
                ),
                Gate(
                    "Second",
                    GateKind.NAND,
                    ["SecondOut"],
                    ["RepairInput", "Other"],
                ),
            ],
        )
        SourceProfile = MandatoryAccessConflictProfile(
            OwnershipFingerprint="source",
            ConflictFingerprint="",
            OwnershipRecords=(),
            CrossConflicts=(),
            SelfConflicts=(),
        )
        Source = PcbPlacement(
            Placed=PlacedDesign(
                Module=Module,
                PlacedGates=[
                    BuildPlacedGate(Module.Gates[0], 0, 1, 0, 0, False),
                    BuildPlacedGate(Module.Gates[1], 12, 1, 0, 0, False),
                ],
            ),
            Clusters=(("First",), ("Second",)),
            SignalOrder=("Other", "RepairInput"),
            LayerCount=3,
            MandatoryAccessPreScreenProfile=SourceProfile,
        )

        def Repair(
            Names,
            _InternalByName,
            Positions,
            _Rotations,
            Mirrors,
            _RequiredSignals,
            _BeamWidth,
            **_KeywordArguments,
        ):
            RepairedMirrors = dict(Mirrors)
            RepairedMirrors[Names[0]] = True
            return dict(Positions), RepairedMirrors, {
                "BaselineConflictCount": 0,
                "FinalConflictCount": 0,
            }

        CandidateProfiles = (
            replace(SourceProfile, OwnershipFingerprint="candidate-zero"),
            replace(SourceProfile, OwnershipFingerprint="candidate-one"),
            replace(SourceProfile, OwnershipFingerprint="candidate-paired"),
        )
        with (
            patch.object(RepairModule, "RepairPackedClusterAccess", Repair),
            patch.object(RepairModule, "PcbGatesConflict", return_value=False),
            patch.object(ClustersModule, "BuildPlacedCellGeometry"),
            patch.object(
                RepairModule,
                "MeasureMandatoryAccessConflictProfile",
                side_effect=CandidateProfiles,
            ),
        ):
            First = BuildTransactionalClusterEndpointRepair(
                Source,
                frozenset({"RepairInput"}),
                RepairVariant=0,
            )
            Second = BuildTransactionalClusterEndpointRepair(
                Source,
                frozenset({"RepairInput"}),
                RepairVariant=1,
            )
            Paired = BuildTransactionalClusterEndpointRepair(
                Source,
                frozenset({"RepairInput"}),
                RepairVariant=0,
                RepairClusterCount=2,
            )

        self.assertTrue(First.Accepted)
        self.assertTrue(Second.Accepted)
        self.assertEqual(First.Diagnostics["TouchedClusterCount"], 1)
        self.assertEqual(Second.Diagnostics["TouchedClusterCount"], 1)
        self.assertEqual(First.Diagnostics["SelectedClusterIndex"], 0)
        self.assertEqual(Second.Diagnostics["SelectedClusterIndex"], 1)
        self.assertTrue(Paired.Accepted)
        self.assertEqual(Paired.Diagnostics["TouchedClusterCount"], 2)
        self.assertEqual(Paired.Diagnostics["SelectedClusterIndices"], [0, 1])


if __name__ == "__main__":
    unittest.main()
