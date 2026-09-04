from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
from time import monotonic
import unittest
from unittest.mock import patch

from Compilation.Pipeline import (
    TryWriteRoutingFailureArtifact,
    WriteRoutingFailureArtifact,
)
from PhysicalDesign.Geometry.Placement import PlacedDesign
from PhysicalDesign.Placement.Engine.Clusters import PcbPlacement
from PhysicalDesign.Placement.Engine.Constraints import PlacementConstraintObservation, PlacementAssignmentConstraintSet
from PhysicalDesign.Placement.Engine.MandatoryAccess import MeasureMandatoryAccessConflictProfile
from PhysicalDesign.Routing.Pcb import ClusterBoundaryLeaseEndgameReserveSeconds, ClusterBoundaryLeaseStateCount, ClusterBoundaryLeaseStateSliceSeconds
from PhysicalDesign.Orchestration.Demand import BuildPlacementFailureHistorySnapshot, PlacementGenerationPlan, MeasurePlacementTopologyDemand, TopologyDemandProfile
from PhysicalDesign.Orchestration.Feedback import BuildCandidateStarvationPlacementEvidence, BuildCurrentAssignmentCutRelocationSignals, BuildTopologyCutEpochPinBankRelocationSignals, BuildTopologyCutEpochGeometryRelocationSignals, BuildTopologyCutEpochGeometryConstraints, SelectTopologyCutFrontier, SelectRepeatedLeaseRealizabilityGeometrySignals, BuildPlacementFingerprint, BuildStructuredPlacementRelocationSignals, CandidateStarvationPlacementEvidence, ExtractPlacementRelocationSignals, ExtractCompletedEscalationRelocationSignals, ExpandAnalogousMandatoryRepairSignals, FailureRequestsPlacementAdvance, FailureRequiresPackedAccessRepair, SelectReleasableLocalClaimSignals, SelectRefinedAssignmentCutDiversificationSignals, SelectRepeatedAssignmentSubcutDiversificationSignals, SelectAssignmentCutGeometrySignals, SelectRepeatedCandidateStarvationDiversificationSignals, SelectCutDrivenClusterRefinementSignals, ShouldDiversifyRepeatedAssignmentCut, ShouldDeferTopologyCutForMaterializedSibling, ShouldPreserveCurrentStructuredAssignmentCut, ShouldUseCurrentAssignmentCutGeometry
from PhysicalDesign.Orchestration.Portfolios import AccessDistinctAssignmentCutDiversificationEvidence, ApplyCoordinatedCandidateDiversificationProfile, ApplyActivePlacementAssignmentConstraints, ApplyRemainingExactLegalJointStateCount, AssignmentCutHasBoundedExactCore, AssignmentCutRepeatsAcrossDistinctPlacementOwnership, BoundedAssignmentCutRepeatsAcrossDistinctOwnership, BoundedAssignmentSignalCutRepeatsAcrossDistinctOwnership, CompleteAssignmentCutSupersedesLeasePairRetry, SelectTransactionalEndpointRepairSignals, ShouldBoundClusterPinBankRepairProbe, BuildCoordinatedCandidateDiversificationProfile, BuildTopologyCutEpochIdentity, BuildTargetedPinBankPackingPolicy, BuildSamePlacementRoutingControlRetryState, ExtractAccessDistinctLeaseOwnershipFingerprints, HasDenseBoundaryLeaseRepairEligibility, IsExactPairedLeaseCut, PlacementGenerationRequest, PlacementGenerationRoutingReserveSeconds, HasTopologyCutEpochRoutingReserve, TopologyCutEpochRoutingReserveSeconds, TopologyCutEpochAdmissionReserveSeconds, PlacementMatchesTopologyCutEpoch, PinBankRepairOwnershipIsDistinct, RoutingControlAttemptIdentity, SelectRepeatedPairedLeaseSubcutSignals, SelectRepeatedHigherOrderPinBankRepairSignals, SelectExhaustiveExactPairPinBankRepairSignals, SelectTopologyCoordinatedCandidateDiversificationSignals, SelectImmediateTopologyPinBankRepairSignals, SelectExhaustedRepeaterAccessCutSignals, SerializedPlacementAssignmentConstraintsAreActive, ShouldPrioritizeCurrentExactCutBeforeBroad, ShouldPrioritizePlacementConflictRelocation, ShouldPrioritizeTopologyCutEpochRelocation, ShouldOpenTopologyCutEpoch, ShouldWidenTopologyCutTerminalShell, ShouldRetrySamePlacementRoutingControl, ShouldContinuePostPinBankRepairEpoch, ShouldDeferSamePlacementRoutingControlRetry, TopologyCutEpochIdentity
from PhysicalDesign.Orchestration.Preparation import IsAuthoritativeMandatoryAccessConflict, RequiresDenseBoundaryLeaseRouting, ShouldEnableClusterBoundaryLeaseInterface, PlacementCandidateIsExactAccessLegal, PlacementPortfolioGenerationNotAfter, PlacementFeedbackRoutingSlotCount, PromoteAuthoritativeMandatoryAccessConflict, RetainedPlacementRoutingSlotCount, TopologyPortfolioRoutingFraction, ShouldGiveRankedJointPortfolioLeadSlice
from PhysicalDesign.Orchestration.Runner import _PlaceAndRoutePcbWithPolicy
from PhysicalDesign.Contracts.Failures import RoutingAssignmentCut, RoutingAssignmentCutClassification, RoutingFailure, RoutingFailureReason, RoutingStageError
from PhysicalDesign.Policy import BuildRoutingAttemptPolicies, LocalFirstPhysicalDesignPolicy, RoutingStrategy
from PhysicalDesign.Contracts.Results import RoutedDesign
from PhysicalDesign.Routing.Pcb import CompactRoutedTrees, RoutePcbAttempt
from PhysicalDesign.Routing.Planning.LocalFirst import PlacementRoutingFeedback
from PhysicalDesign.Runtime.Reliability import BuildRoutingDeadlineDiagnostics, BuildStableFingerprint, ChooseRoutingEscalationAction, EnforceRoutingRuntimeLimit, HasAdaptiveEscalationBudget, RemainingRoutingRuntimeMilliseconds, RetainUnaffectedCandidateCache, SelectBoundedDiverseCandidatePool, RoutingDeadline, RoutingEscalationState
from PhysicalDesign.Redstone.Technology import DefaultRedstoneRoutingTechnology

class RouterReliabilityTests(unittest.TestCase):
    @staticmethod
    def BuildReconvergentTopologyModule():
        """Build a name-agnostic fanout-four graph for joint-flow tests."""
        Gates = [
            SimpleNamespace(
                Kind="NAND",
                Inputs=(),
                Outputs=("Root",),
            ),
            *(
                SimpleNamespace(
                    Kind="NAND",
                    Inputs=("Root",),
                    Outputs=(f"Branch{Index}",),
                )
                for Index in range(4)
            ),
            SimpleNamespace(
                Kind="NAND",
                Inputs=tuple(f"Branch{Index}" for Index in range(4)),
                Outputs=("Result",),
            ),
        ]
        return SimpleNamespace(
            Gates=Gates,
            Inputs=(),
            Outputs=("Result",),
        )

    @staticmethod
    def BuildHigherOrderAssignmentFailure(
        ConflictFingerprint: str = "higher-order-cut",
    ) -> RoutingFailure:
        """Build one complete authoritative capacity-one assignment cut."""
        return RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="TrackAssignment",
            AffectedNets=("A0", "B1", "Generate0", "Propagate1"),
            RepairActions=("AdvancePlacementCandidate",),
            Diagnostics={
                "Action": "advance-placement-conflict-relocation",
                "ConflictFingerprint": ConflictFingerprint,
                "CandidateFingerprint": "candidate-set",
                "EffectiveWorkFingerprint": "effective-work",
                "ConflictGraph": {
                    "Classification": "higher-order-placement-conflict",
                    "ConflictSignals": [
                        "A0",
                        "B1",
                        "Generate0",
                        "Propagate1",
                    ],
                    "RelocationSignals": [
                        "A0",
                        "B1",
                        "Generate0",
                        "Propagate1",
                    ],
                    "PriorityRelocationSignals": ["A0", "B1"],
                    "NoCandidateSignals": [],
                    "PairwiseIncompatibleEdges": [
                        ["Generate0", "Propagate1"],
                    ],
                    "CandidateCounts": {
                        "Generate0": 26,
                        "Propagate1": 4,
                    },
                    "ResourceHotspots": [[12, 2, 5]],
                    "CapacityOnePlannerWitness": {
                        "Owner": "Generate0",
                        "Claimant": "Propagate1",
                    },
                },
            },
        )

    def testPromotedAccessConstraintsPreemptDeferredUnpacked(
        self,
    ) -> None:
        """A new constraint epoch re-enters packed repair before fallback."""
        CurrentCut = RoutingAssignmentCut.FromFailure(
            self.BuildHigherOrderAssignmentFailure()
        )
        self.assertIsNotNone(CurrentCut)
        assert CurrentCut is not None
        AssignmentCutHistory = [CurrentCut]
        PriorConstraints = (
            PlacementAssignmentConstraintSet().WithCut(CurrentCut)
        )
        PromotedConstraints = PlacementAssignmentConstraintSet(
            PairwiseConflictEdges=(
                *PriorConstraints.PairwiseConflictEdges,
                ("A3", "NandNet3"),
            ),
            HigherOrderSignalSets=(
                PriorConstraints.HigherOrderSignalSets
            ),
        )
        RelocationSignals = frozenset(
            CurrentCut.PriorityRelocationSignals
        )

        self.assertTrue(ShouldPrioritizePlacementConflictRelocation(
            PreferRelocation=True,
            RelocationSignals=RelocationSignals,
            TotalRelocationGenerationCount=1,
            MaximumFeedbackRounds=5,
            RelocationPrioritySignals=RelocationSignals,
            LastRelocationPrioritySignalsUsed=RelocationSignals,
            RequiredRelocationSignals=RelocationSignals,
            LastRequiredRelocationSignalsUsed=RelocationSignals,
            CurrentAssignmentCutFingerprint=(
                CurrentCut.ConflictFingerprint
            ),
            LastAssignmentCutFingerprintUsed=(
                CurrentCut.ConflictFingerprint
            ),
            CurrentAssignmentConstraintFingerprint=(
                PromotedConstraints.Fingerprint
            ),
            LastAssignmentConstraintFingerprintUsed=(
                PriorConstraints.Fingerprint
            ),
        ))
        self.assertFalse(ShouldPrioritizePlacementConflictRelocation(
            PreferRelocation=True,
            RelocationSignals=RelocationSignals,
            TotalRelocationGenerationCount=1,
            MaximumFeedbackRounds=5,
            RelocationPrioritySignals=RelocationSignals,
            LastRelocationPrioritySignalsUsed=RelocationSignals,
            RequiredRelocationSignals=RelocationSignals,
            LastRequiredRelocationSignalsUsed=RelocationSignals,
            CurrentAssignmentCutFingerprint=(
                CurrentCut.ConflictFingerprint
            ),
            LastAssignmentCutFingerprintUsed=(
                CurrentCut.ConflictFingerprint
            ),
            CurrentAssignmentConstraintFingerprint=(
                PriorConstraints.Fingerprint
            ),
            LastAssignmentConstraintFingerprintUsed=(
                PriorConstraints.Fingerprint
            ),
        ))
        self.assertFalse(ShouldPrioritizePlacementConflictRelocation(
            PreferRelocation=True,
            RelocationSignals=RelocationSignals,
            TotalRelocationGenerationCount=5,
            MaximumFeedbackRounds=5,
            RelocationPrioritySignals=RelocationSignals,
            LastRelocationPrioritySignalsUsed=RelocationSignals,
            RequiredRelocationSignals=RelocationSignals,
            LastRequiredRelocationSignalsUsed=RelocationSignals,
            CurrentAssignmentCutFingerprint=(
                CurrentCut.ConflictFingerprint
            ),
            LastAssignmentCutFingerprintUsed=(
                CurrentCut.ConflictFingerprint
            ),
            CurrentAssignmentConstraintFingerprint=(
                PromotedConstraints.Fingerprint
            ),
            LastAssignmentConstraintFingerprintUsed=(
                PriorConstraints.Fingerprint
            ),
        ))
        self.assertTrue(ShouldPrioritizeCurrentExactCutBeforeBroad(
            Required=True,
            PreferRelocation=True,
            HasCurrentAssignmentCut=True,
            HasRelocationSignals=True,
            TotalRelocationGenerationCount=1,
            MaximumFeedbackRounds=5,
        ))
        self.assertFalse(ShouldPrioritizeCurrentExactCutBeforeBroad(
            Required=True,
            PreferRelocation=True,
            HasCurrentAssignmentCut=True,
            HasRelocationSignals=True,
            TotalRelocationGenerationCount=5,
            MaximumFeedbackRounds=5,
        ))
        self.assertEqual(len(AssignmentCutHistory), 1)
        self.assertIs(AssignmentCutHistory[0], CurrentCut)
        self.assertEqual(
            AssignmentCutHistory[0].ConflictFingerprint,
            CurrentCut.ConflictFingerprint,
        )

    def testTopologyCutEpochRelocationConsumesOneExhaustedEpoch(
        self,
    ) -> None:
        """A new topology-pressure cut earns one geometry-changing retry."""
        self.assertTrue(ShouldPrioritizeTopologyCutEpochRelocation(
            TopologyRequiresJointPortfolio=True,
            HasRelocationSignals=True,
            TotalRelocationGenerationCount=1,
            MaximumFeedbackRounds=1,
            CurrentAssignmentCutFingerprint="current-cut",
            LastAssignmentCutFingerprintUsed="prior-cut",
        ))
        for Arguments in (
            {
                "TopologyRequiresJointPortfolio": False,
                "HasRelocationSignals": True,
                "TotalRelocationGenerationCount": 1,
                "MaximumFeedbackRounds": 1,
                "CurrentAssignmentCutFingerprint": "current-cut",
                "LastAssignmentCutFingerprintUsed": "prior-cut",
            },
            {
                "TopologyRequiresJointPortfolio": True,
                "HasRelocationSignals": True,
                "TotalRelocationGenerationCount": 0,
                "MaximumFeedbackRounds": 1,
                "CurrentAssignmentCutFingerprint": "current-cut",
                "LastAssignmentCutFingerprintUsed": "prior-cut",
            },
            {
                "TopologyRequiresJointPortfolio": True,
                "HasRelocationSignals": True,
                "TotalRelocationGenerationCount": 2,
                "MaximumFeedbackRounds": 1,
                "CurrentAssignmentCutFingerprint": "current-cut",
                "LastAssignmentCutFingerprintUsed": "prior-cut",
            },
            {
                "TopologyRequiresJointPortfolio": True,
                "HasRelocationSignals": True,
                "TotalRelocationGenerationCount": 1,
                "MaximumFeedbackRounds": 1,
                "CurrentAssignmentCutFingerprint": "current-cut",
                "LastAssignmentCutFingerprintUsed": "current-cut",
            },
        ):
            self.assertFalse(
                ShouldPrioritizeTopologyCutEpochRelocation(**Arguments)
            )

    def testCurrentAssignmentCutRelocationSignalsExcludeHistory(
        self,
    ) -> None:
        """The topology epoch moves exactly the newly reported cut cover."""
        Cut = RoutingAssignmentCut(
            Classification=(
                RoutingAssignmentCutClassification
                .MandatoryBoundaryCapacityCut
            ),
            ConflictGraphJson="{}",
            ConflictSignals=("A1", "Generate0"),
            RelocationSignals=("A1",),
            PriorityRelocationSignals=("Generate0",),
            NoCandidateSignals=(),
            PairwiseConflictEdges=(("A1", "Generate0"),),
        )
        self.assertEqual(
            BuildCurrentAssignmentCutRelocationSignals(Cut),
            frozenset({"A1", "Generate0"}),
        )
        self.assertEqual(
            BuildTopologyCutEpochGeometryRelocationSignals(
                Cut,
                ("PriorLeaseEndpoint",),
            ),
            frozenset({"A1", "Generate0", "PriorLeaseEndpoint"}),
        )
        self.assertEqual(
            BuildTopologyCutEpochGeometryConstraints(Cut)
            .PairwiseConflictEdges,
            (("A1", "Generate0"),),
        )
        Cumulative = PlacementAssignmentConstraintSet(
            PairwiseConflictEdges=(("PriorExactA", "PriorExactB"),),
            HigherOrderSignalEvidence=(
                PlacementConstraintObservation(
                    Signals=(
                        "PriorHigherA",
                        "PriorHigherB",
                        "PriorHigherC",
                    ),
                    ObservationCount=2,
                    ObservationFingerprints=("higher-a", "higher-b"),
                ),
            ),
            ObservedInterfaceConflictEvidence=(
                PlacementConstraintObservation(
                    Signals=("PriorObservedA", "PriorObservedB"),
                    ObservationCount=2,
                    ObservationFingerprints=("observed-a", "observed-b"),
                ),
            ),
            ActiveHigherOrderSignalSets=((
                "PriorHigherA",
                "PriorHigherB",
                "PriorHigherC",
            ),),
            ActiveObservedInterfaceConflictEdges=((
                "PriorObservedA",
                "PriorObservedB",
            ),),
        )
        EpochConstraints = BuildTopologyCutEpochGeometryConstraints(
            Cut,
            Cumulative,
        )
        self.assertEqual(
            EpochConstraints.PairwiseConflictEdges,
            (
                ("A1", "Generate0"),
                ("PriorExactA", "PriorExactB"),
            ),
        )
        self.assertEqual(
            EpochConstraints.ActiveHigherOrderSignalSets,
            Cumulative.ActiveHigherOrderSignalSets,
        )
        self.assertEqual(
            EpochConstraints.ActiveObservedInterfaceConflictEdges,
            Cumulative.ActiveObservedInterfaceConflictEdges,
        )

    def testTopologyCutFrontierKeepsCurrentAndOneRecentBoundedCut(
        self,
    ) -> None:
        def Cut(
            Prefix: str,
            Classification: RoutingAssignmentCutClassification,
        ) -> RoutingAssignmentCut:
            return RoutingAssignmentCut(
                Classification=Classification,
                ConflictGraphJson="{}",
                ConflictSignals=(f"{Prefix}0", f"{Prefix}1"),
                PairwiseConflictEdges=((f"{Prefix}0", f"{Prefix}1"),),
            )

        Older = Cut(
            "Older",
            RoutingAssignmentCutClassification.SaturatedBoundaryCut,
        )
        Previous = Cut(
            "Previous",
            RoutingAssignmentCutClassification.MandatoryBoundaryCapacityCut,
        )
        Current = Cut(
            "Current",
            RoutingAssignmentCutClassification.SparseRegionRouteCut,
        )
        Frontier = SelectTopologyCutFrontier(
            Current,
            (Older, Previous, Current),
            Enabled=True,
        )

        self.assertEqual(Frontier, (Current, Previous))
        self.assertEqual(
            SelectTopologyCutFrontier(
                Current,
                (Older, Previous, Current),
                Enabled=False,
            ),
            (),
        )

        RenamedPrevious = Cut(
            "RenamedPrevious",
            RoutingAssignmentCutClassification.MandatoryBoundaryCapacityCut,
        )
        RenamedCurrent = Cut(
            "RenamedCurrent",
            RoutingAssignmentCutClassification.SparseRegionRouteCut,
        )
        RenamedFrontier = SelectTopologyCutFrontier(
            RenamedCurrent,
            (Older, RenamedPrevious, RenamedCurrent),
            Enabled=True,
        )
        self.assertEqual(
            tuple(CutValue.Classification for CutValue in RenamedFrontier),
            tuple(CutValue.Classification for CutValue in Frontier),
        )
        self.assertEqual(len(RenamedFrontier), 2)

    def testRepeatedLeaseRealizabilityPromotesOnlyProvedEndpoint(
        self,
    ) -> None:
        Failure = RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="Candidate",
            AffectedNets=("RenamedEndpoint",),
            Diagnostics={
                "Action": (
                    "advance-placement-after-complete-cluster-lease-portfolio"
                ),
                "ConflictGraph": {
                    "Classification": (
                        "candidate-starvation-placement-conflict"
                    ),
                    "ConflictSignals": ["RenamedEndpoint"],
                    "NoCandidateSignals": ["RenamedEndpoint"],
                },
                "CandidateRealizabilityNogoods": [
                    {
                        "Signal": "OtherEndpoint",
                        "PatternFingerprint": "other-pattern",
                    },
                    {
                        "Signal": "RenamedEndpoint",
                        "PatternFingerprint": "first-pattern",
                    },
                    {
                        "Signal": "RenamedEndpoint",
                        "PatternFingerprint": "second-pattern",
                    },
                ],
            },
        )

        self.assertEqual(
            SelectRepeatedLeaseRealizabilityGeometrySignals(Failure),
            frozenset({"OtherEndpoint", "RenamedEndpoint"}),
        )
        OnePattern = replace(
            Failure,
            Diagnostics={
                **Failure.Diagnostics,
                "CandidateRealizabilityNogoods": [
                    {
                        "Signal": "RenamedEndpoint",
                        "PatternFingerprint": "first-pattern",
                    },
                ],
            },
        )
        self.assertEqual(
            SelectRepeatedLeaseRealizabilityGeometrySignals(OnePattern),
            frozenset(),
        )

    def testTargetedPinBankEpochAddsEndpointToGeometryIdentity(
        self,
    ) -> None:
        """The retry endpoint must move even while an older cut is retained."""
        CurrentCutSignals = frozenset((
            "CarryIn",
            "CarryOutPropagate32",
            "Propagate0",
        ))
        self.assertEqual(
            BuildTopologyCutEpochPinBankRelocationSignals(
                CurrentCutSignals,
                ("A2",),
                True,
            ),
            frozenset((
                "A2",
                "CarryIn",
                "CarryOutPropagate32",
                "Propagate0",
            )),
        )
        self.assertEqual(
            BuildTopologyCutEpochPinBankRelocationSignals(
                CurrentCutSignals,
                ("A2",),
                False,
            ),
            CurrentCutSignals,
        )
        self.assertEqual(
            BuildTopologyCutEpochPinBankRelocationSignals(
                reversed(tuple(CurrentCutSignals)),
                ("RenamedEndpoint",),
                True,
            ),
            frozenset((*CurrentCutSignals, "RenamedEndpoint")),
        )

    def testTopologyCutEpochIsOwnershipAwareAndDeduplicated(self) -> None:
        Cut = RoutingAssignmentCut(
            Classification=(
                RoutingAssignmentCutClassification
                .MandatoryBoundaryCapacityCut
            ),
            ConflictGraphJson="{}",
            ConflictFingerprint="capacity-cut",
            ConflictSignals=("A3", "B3"),
            PairwiseConflictEdges=(("A3", "B3"),),
            MandatoryAccessOwnershipFingerprint="ownership-a",
        )
        Constraints = PlacementAssignmentConstraintSet().WithCut(Cut)
        Epoch = BuildTopologyCutEpochIdentity(Cut, Constraints)
        self.assertTrue(ShouldOpenTopologyCutEpoch(
            TopologyRequiresJointPortfolio=True,
            AssignmentCut=Cut,
            Epoch=Epoch,
            OpenedEpochs=(),
        ))
        self.assertFalse(ShouldOpenTopologyCutEpoch(
            TopologyRequiresJointPortfolio=False,
            AssignmentCut=Cut,
            Epoch=Epoch,
            OpenedEpochs=(),
        ))
        self.assertFalse(ShouldOpenTopologyCutEpoch(
            TopologyRequiresJointPortfolio=True,
            AssignmentCut=Cut,
            Epoch=Epoch,
            OpenedEpochs=(Epoch,),
        ))

        AccessDistinctEpoch = TopologyCutEpochIdentity(
            AssignmentCutFingerprint=Epoch.AssignmentCutFingerprint,
            AssignmentConstraintFingerprint=Epoch.AssignmentConstraintFingerprint,
            MandatoryAccessOwnershipFingerprint="ownership-b",
        )
        self.assertTrue(ShouldOpenTopologyCutEpoch(
            TopologyRequiresJointPortfolio=True,
            AssignmentCut=Cut,
            Epoch=AccessDistinctEpoch,
            OpenedEpochs=(Epoch,),
        ))
        self.assertTrue(ShouldOpenTopologyCutEpoch(
            TopologyRequiresJointPortfolio=True,
            AssignmentCut=replace(
                Cut,
                Classification=(
                    RoutingAssignmentCutClassification
                    .PortalCoveragePairConflict
                ),
            ),
            Epoch=AccessDistinctEpoch,
            OpenedEpochs=(Epoch,),
        ))
        SaturatedCut = replace(
            Cut,
            Classification=(
                RoutingAssignmentCutClassification.SaturatedBoundaryCut
            ),
            ConflictFingerprint="interface-cut",
            MandatoryAccessOwnershipFingerprint="",
        )
        SaturatedEpoch = BuildTopologyCutEpochIdentity(
            SaturatedCut,
            PlacementAssignmentConstraintSet().WithCut(SaturatedCut),
        )
        self.assertTrue(ShouldOpenTopologyCutEpoch(
            TopologyRequiresJointPortfolio=True,
            AssignmentCut=SaturatedCut,
            Epoch=SaturatedEpoch,
            OpenedEpochs=(),
        ))
        self.assertFalse(ShouldOpenTopologyCutEpoch(
            TopologyRequiresJointPortfolio=True,
            AssignmentCut=SaturatedCut,
            Epoch=SaturatedEpoch,
            OpenedEpochs=(SaturatedEpoch,),
        ))
        SparseCut = replace(
            SaturatedCut,
            Classification=(
                RoutingAssignmentCutClassification.SparseRegionRouteCut
            ),
            ConflictFingerprint="sparse-cut",
            ConflictSignals=("Endpoint",),
            RelocationSignals=("Endpoint",),
        )
        SparseEpoch = BuildTopologyCutEpochIdentity(
            SparseCut,
            PlacementAssignmentConstraintSet().WithCut(SparseCut),
        )
        self.assertTrue(AssignmentCutHasBoundedExactCore(SparseCut))
        self.assertTrue(ShouldOpenTopologyCutEpoch(
            TopologyRequiresJointPortfolio=True,
            AssignmentCut=SparseCut,
            Epoch=SparseEpoch,
            OpenedEpochs=(),
        ))

    def testMaterializedPlacementMatchesOnlyItsTopologyCutEpoch(
        self,
    ) -> None:
        Epoch = TopologyCutEpochIdentity(
            AssignmentCutFingerprint="cut-current",
            AssignmentConstraintFingerprint="constraints-current",
            MandatoryAccessOwnershipFingerprint="ownership-current",
        )

        def Placement(CutFingerprint: str) -> PcbPlacement:
            return PcbPlacement(
                Placed=PlacedDesign(
                    Module=SimpleNamespace(Gates=[]),
                    PlacedGates=[],
                    LocalRouteDiagnostics={
                        "__JointClusterPlacement__": {"Enabled": True},
                        "__PlacementRecipe__": {
                            "AssignmentCutFingerprint": CutFingerprint,
                            "AssignmentConstraintFingerprint": (
                                "constraints-current"
                            ),
                        },
                    },
                ),
                Clusters=(),
                SignalOrder=(),
                LayerCount=1,
            )

        self.assertTrue(PlacementMatchesTopologyCutEpoch(
            Placement("cut-current"),
            Epoch,
        ))
        self.assertFalse(PlacementMatchesTopologyCutEpoch(
            Placement("cut-stale"),
            Epoch,
        ))

    def testTopologyCutEpochRequiresCompleteDenseRoutingReserve(self) -> None:
        Policy = LocalFirstPhysicalDesignPolicy
        RequiredSeconds = TopologyCutEpochAdmissionReserveSeconds(
            Policy,
            RequiresDenseBoundaryRouting=True,
        )
        self.assertTrue(HasTopologyCutEpochRoutingReserve(
            RemainingSeconds=RequiredSeconds,
            Policy=Policy,
            RequiresDenseBoundaryRouting=True,
        ))
        self.assertFalse(HasTopologyCutEpochRoutingReserve(
            RemainingSeconds=RequiredSeconds - 0.001,
            Policy=Policy,
            RequiresDenseBoundaryRouting=True,
        ))

    def testTopologyCutEpochKeepsViableExactSliceForNonDenseTopology(self) -> None:
        Policy = replace(
            LocalFirstPhysicalDesignPolicy,
            RuntimeBudgetSeconds=118.0,
            AdaptiveRouting=replace(
                LocalFirstPhysicalDesignPolicy.AdaptiveRouting,
                MaximumRuntimeSeconds=118.0,
            ),
        )
        RequiredSeconds = TopologyCutEpochAdmissionReserveSeconds(
            Policy,
            RequiresDenseBoundaryRouting=False,
        )
        self.assertEqual(RequiredSeconds, 41.3)
        self.assertFalse(HasTopologyCutEpochRoutingReserve(
            RemainingSeconds=18.0,
            Policy=Policy,
            RequiresDenseBoundaryRouting=False,
        ))
        self.assertTrue(HasTopologyCutEpochRoutingReserve(
            RemainingSeconds=RequiredSeconds,
            Policy=Policy,
            RequiresDenseBoundaryRouting=False,
        ))

    def testExactPairCutUsesBoundedNonDenseRepairReserve(self) -> None:
        Policy = replace(
            LocalFirstPhysicalDesignPolicy,
            RuntimeBudgetSeconds=118.0,
            AdaptiveRouting=replace(
                LocalFirstPhysicalDesignPolicy.AdaptiveRouting,
                MaximumRuntimeSeconds=118.0,
            ),
        )
        RequiredSeconds = TopologyCutEpochAdmissionReserveSeconds(
            Policy,
            RequiresDenseBoundaryRouting=False,
            HasBoundedExactCutEvidence=True,
        )
        self.assertEqual(RequiredSeconds, 12.0)
        self.assertFalse(HasTopologyCutEpochRoutingReserve(
            RemainingSeconds=RequiredSeconds - 0.001,
            Policy=Policy,
            RequiresDenseBoundaryRouting=False,
            HasBoundedExactCutEvidence=True,
        ))
        self.assertTrue(HasTopologyCutEpochRoutingReserve(
            RemainingSeconds=RequiredSeconds,
            Policy=Policy,
            RequiresDenseBoundaryRouting=False,
            HasBoundedExactCutEvidence=True,
        ))
        self.assertEqual(
            TopologyCutEpochAdmissionReserveSeconds(
                Policy,
                RequiresDenseBoundaryRouting=True,
                HasBoundedExactCutEvidence=True,
            ),
            TopologyCutEpochAdmissionReserveSeconds(
                Policy,
                RequiresDenseBoundaryRouting=True,
            ),
        )

    def testSmallSaturatedCoreUsesSameBoundedRepairAdmission(self) -> None:
        SmallCore = RoutingAssignmentCut(
            Classification=(
                RoutingAssignmentCutClassification.SaturatedBoundaryCut
            ),
            ConflictGraphJson="{}",
            ConflictSignals=("One", "Two", "Three", "Four"),
        )
        LargeCore = replace(
            SmallCore,
            ConflictSignals=("One", "Two", "Three", "Four", "Five"),
            ConflictFingerprint="large-cut",
            MandatoryAccessOwnershipFingerprint="first-owner",
        )
        PairCore = replace(
            LargeCore,
            PairwiseConflictEdges=(("One", "Five"),),
        )
        StarvationCore = replace(
            LargeCore,
            Classification=(
                RoutingAssignmentCutClassification
                .CandidateStarvationPlacementConflict
            ),
            ConflictSignals=("Starved",),
            NoCandidateSignals=("Starved",),
        )
        ObservedCore = replace(
            LargeCore,
            ConflictGraphJson=json.dumps({
                "ObservedPatternConflictEdges": [["One", "Two"]],
            }),
        )
        self.assertTrue(AssignmentCutHasBoundedExactCore(SmallCore))
        self.assertFalse(AssignmentCutHasBoundedExactCore(LargeCore))
        self.assertTrue(AssignmentCutHasBoundedExactCore(PairCore))
        self.assertTrue(AssignmentCutHasBoundedExactCore(StarvationCore))
        self.assertTrue(AssignmentCutHasBoundedExactCore(ObservedCore))
        PriorBoundedOwnership = replace(
            SmallCore,
            ConflictFingerprint="bounded-repeat",
            MandatoryAccessOwnershipFingerprint="ownership-a",
        )
        CurrentBoundedOwnership = replace(
            SmallCore,
            ConflictFingerprint="bounded-repeat",
            MandatoryAccessOwnershipFingerprint="ownership-b",
        )
        self.assertTrue(
            BoundedAssignmentCutRepeatsAcrossDistinctOwnership(
                (PriorBoundedOwnership,),
                CurrentBoundedOwnership,
            )
        )
        self.assertFalse(
            BoundedAssignmentCutRepeatsAcrossDistinctOwnership(
                (PriorBoundedOwnership,),
                replace(
                    CurrentBoundedOwnership,
                    MandatoryAccessOwnershipFingerprint="ownership-a",
                ),
            )
        )
        PriorStableSignals = replace(
            ObservedCore,
            ConflictFingerprint="pattern-a",
            MandatoryAccessOwnershipFingerprint="ownership-a",
        )
        CurrentStableSignals = replace(
            ObservedCore,
            ConflictFingerprint="pattern-b",
            MandatoryAccessOwnershipFingerprint="ownership-b",
        )
        self.assertTrue(
            BoundedAssignmentSignalCutRepeatsAcrossDistinctOwnership(
                (PriorStableSignals,),
                CurrentStableSignals,
            )
        )
        self.assertFalse(
            BoundedAssignmentSignalCutRepeatsAcrossDistinctOwnership(
                (PriorStableSignals,),
                replace(
                    CurrentStableSignals,
                    ConflictSignals=(
                        *CurrentStableSignals.ConflictSignals,
                        "Different",
                    ),
                ),
            )
        )
        CompleteProof = replace(
            LargeCore,
            CompleteAssignmentCutProof=True,
            PriorityRelocationSignals=("One", "Five"),
        )
        self.assertTrue(AssignmentCutHasBoundedExactCore(CompleteProof))
        self.assertTrue(
            CompleteAssignmentCutSupersedesLeasePairRetry(CompleteProof)
        )
        self.assertFalse(
            CompleteAssignmentCutSupersedesLeasePairRetry(LargeCore)
        )
        SameLargeCutDifferentOwner = replace(
            LargeCore,
            MandatoryAccessOwnershipFingerprint="other-owner",
        )
        self.assertTrue(
            AssignmentCutRepeatsAcrossDistinctPlacementOwnership(
                (LargeCore,),
                SameLargeCutDifferentOwner,
            )
        )
        self.assertFalse(
            AssignmentCutRepeatsAcrossDistinctPlacementOwnership(
                (LargeCore,),
                replace(
                    SameLargeCutDifferentOwner,
                    MandatoryAccessOwnershipFingerprint=(
                        LargeCore.MandatoryAccessOwnershipFingerprint
                    ),
                ),
            )
        )
        self.assertEqual(
            SelectTransactionalEndpointRepairSignals(
                CompleteProof,
                InternalPinBankGeometryRepairActive=False,
                PinBankRepairSignals=frozenset({"Fallback"}),
            ),
            frozenset(CompleteProof.PriorityRelocationSignals),
        )
        self.assertEqual(
            SelectTransactionalEndpointRepairSignals(
                LargeCore,
                InternalPinBankGeometryRepairActive=True,
                PinBankRepairSignals=frozenset({"Fallback"}),
            ),
            frozenset({"Fallback"}),
        )
        self.assertEqual(
            SelectTransactionalEndpointRepairSignals(
                SmallCore,
                InternalPinBankGeometryRepairActive=False,
                PinBankRepairSignals=frozenset(),
                CandidateIsTransactionalEndpointRepair=True,
                ParentTransactionalRepairSignals=frozenset({
                    *SmallCore.ConflictSignals,
                    "ParentOnly",
                }),
            ),
            frozenset(
                SmallCore.PriorityRelocationSignals
                or SmallCore.NoCandidateSignals
                or SmallCore.ConflictSignals
            ),
        )
        SameInterface = frozenset(
            SmallCore.PriorityRelocationSignals
            or SmallCore.NoCandidateSignals
            or SmallCore.ConflictSignals
        )
        self.assertFalse(SelectTransactionalEndpointRepairSignals(
            SmallCore,
            InternalPinBankGeometryRepairActive=False,
            PinBankRepairSignals=frozenset(),
            CandidateIsTransactionalEndpointRepair=True,
            ParentTransactionalRepairSignals=SameInterface,
        ))
        self.assertEqual(
            SelectTransactionalEndpointRepairSignals(
                SmallCore,
                InternalPinBankGeometryRepairActive=False,
                PinBankRepairSignals=frozenset(),
                CandidateIsTransactionalEndpointRepair=True,
                ParentTransactionalRepairSignals=SameInterface,
                RepeatedAccessDistinctTransactionalCut=True,
            ),
            SameInterface,
        )
        self.assertTrue(
            ShouldBoundClusterPinBankRepairProbe(True, False)
        )
        self.assertFalse(
            ShouldBoundClusterPinBankRepairProbe(True, True)
        )

    def testCompleteAssignmentProofOpensOneFundedTopologyEpoch(
        self,
    ) -> None:
        Failure = RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="TrackAssignment",
            AffectedNets=("AnonymousFirst", "AnonymousSecond"),
            Diagnostics={
                "CompleteAssignmentCutProof": True,
                "ConflictFingerprint": "complete-cut-proof",
                "ConflictGraph": {
                    "Classification": "relocated-multi-pair-conflict",
                    "ConflictSignals": [
                        "AnonymousFirst",
                        "AnonymousSecond",
                    ],
                    "PriorityRelocationSignals": [
                        "AnonymousFirst",
                        "AnonymousSecond",
                    ],
                },
            },
        )
        Cut = RoutingAssignmentCut.FromFailure(Failure)
        self.assertIsNotNone(Cut)
        assert Cut is not None
        self.assertTrue(Cut.CompleteAssignmentCutProof)
        self.assertTrue(AssignmentCutHasBoundedExactCore(Cut))
        Constraints = PlacementAssignmentConstraintSet().WithCut(Cut)
        Epoch = BuildTopologyCutEpochIdentity(Cut, Constraints)
        self.assertTrue(ShouldOpenTopologyCutEpoch(
            TopologyRequiresJointPortfolio=True,
            AssignmentCut=Cut,
            Epoch=Epoch,
            OpenedEpochs=(),
        ))

    def testTopologyCutTerminalShellIsExternalAndTriggerScoped(self) -> None:
        Cut = RoutingAssignmentCut(
            Classification=(
                RoutingAssignmentCutClassification
                .MandatoryBoundaryCapacityCut
            ),
            ConflictGraphJson="{}",
            ConflictSignals=("A3", "B3"),
        )
        self.assertTrue(ShouldWidenTopologyCutTerminalShell(
            TopologyRequiresJointPortfolio=True,
            AssignmentCut=Cut,
            ExternalSignals=("A0", "A3", "Result"),
        ))
        self.assertFalse(ShouldWidenTopologyCutTerminalShell(
            TopologyRequiresJointPortfolio=False,
            AssignmentCut=Cut,
            ExternalSignals=("A0", "A3", "Result"),
        ))
        self.assertFalse(ShouldWidenTopologyCutTerminalShell(
            TopologyRequiresJointPortfolio=True,
            AssignmentCut=Cut,
            ExternalSignals=("A0", "Result"),
        ))

    def testHigherOrderRelocationUsesCurrentCutGeometryForTopologyPortfolio(
        self,
    ) -> None:
        """A CLA-style joint epoch moves its current cut, not history."""
        Cut = RoutingAssignmentCut(
            Classification=(
                RoutingAssignmentCutClassification
                .HigherOrderPlacementConflict
            ),
            ConflictGraphJson="{}",
            ConflictSignals=("A1", "Generate0"),
        )
        self.assertTrue(ShouldUseCurrentAssignmentCutGeometry(
            False,
            "row-beam-conflict-relocation",
            Cut,
        ))
        self.assertFalse(ShouldUseCurrentAssignmentCutGeometry(
            False,
            "row-beam",
            Cut,
        ))
        self.assertFalse(ShouldUseCurrentAssignmentCutGeometry(
            False,
            "row-beam-conflict-relocation",
            None,
        ))
        self.assertEqual(
            SelectAssignmentCutGeometrySignals(
                TopologyRequiresJointPortfolio=True,
                AssignmentCut=Cut,
                CompleteCutSignals=("A1", "Generate0"),
                PriorityCutSignals=("Generate0",),
            ),
            frozenset(("A1", "Generate0")),
        )
        self.assertEqual(
            SelectAssignmentCutGeometrySignals(
                TopologyRequiresJointPortfolio=False,
                AssignmentCut=Cut,
                CompleteCutSignals=("A1", "Generate0"),
                PriorityCutSignals=("Generate0",),
            ),
            frozenset(("Generate0",)),
        )

    def testMandatoryRepairExpandsEquivalentExternalInputMotifs(self) -> None:
        Module = SimpleNamespace(
            Inputs=("A0", "B0", "A1", "B1", "Other"),
            Gates=(
                SimpleNamespace(
                    Kind="INPUT",
                    Inputs=(),
                ),
                SimpleNamespace(
                    Kind="NAND",
                    Inputs=("A0", "B0"),
                ),
                SimpleNamespace(
                    Kind="NAND",
                    Inputs=("A0", "Other"),
                ),
                SimpleNamespace(
                    Kind="NAND",
                    Inputs=("B0", "Other"),
                ),
                SimpleNamespace(
                    Kind="NAND",
                    Inputs=("A1", "B1"),
                ),
                SimpleNamespace(
                    Kind="NAND",
                    Inputs=("A1", "Other"),
                ),
                SimpleNamespace(
                    Kind="NAND",
                    Inputs=("B1", "Other"),
                ),
            ),
        )

        self.assertEqual(
            ExpandAnalogousMandatoryRepairSignals(
                Module,
                frozenset({"A0", "B0"}),
            ),
            frozenset({"A0", "B0", "A1", "B1"}),
        )

    def RunTwoPlacementFlow(
        self,
        *,
        RouteBehavior=None,
        RoutedValidationCallback=None,
        RuntimeBudgetSeconds: float = 5.0,
        RouteOrder: list[int] | None = None,
        DeadlineIdentities: list[int] | None = None,
        ProgressCallback=None,
        LocalClaimsByX: dict[int, tuple[object, ...]] | None = None,
        PlacementCallBehavior=None,
        FeedbackBoundaryOverflowByX: dict[int, int] | None = None,
        FeedbackBehavior=None,
        NetlistModule=None,
        IsolationBehavior=None,
    ):
        """Run a deterministic two-placement flow through mocked heavy stages."""
        if RouteOrder is None:
            RouteOrder = []
        if DeadlineIdentities is None:
            DeadlineIdentities = []

        def PlacementAt(X: int) -> PcbPlacement:
            Gate = SimpleNamespace(
                Name=f"Gate{X}",
                Kind="NAND",
                X=X,
                Y=1,
                Z=0,
                Rotation=False,
                MirrorX=False,
                Inputs=[],
                Outputs=[],
                InputPins=[],
                OutputPin=None,
                InputDirections=[],
                OutputDirection=None,
            )
            Placed = PlacedDesign(
                Module=SimpleNamespace(Gates=[]),
                PlacedGates=[Gate],
                LocalRouteClaims=(LocalClaimsByX or {}).get(X, ()),
                FrozenNetWires={},
                LocalNetBranches={},
                LocalNetTargets={},
                LocalRouteDiagnostics={},
            )
            return PcbPlacement(
                Placed=Placed,
                Clusters=(),
                SignalOrder=(),
                LayerCount=2,
            )

        FirstPlacement = PlacementAt(0)
        SecondPlacement = PlacementAt(10)

        PlacementCallCount = 0

        def PlaceGraph(*_Arguments, PackingPolicy, **Options):
            nonlocal PlacementCallCount
            PlacementCallCount += 1
            if PlacementCallBehavior is not None:
                return PlacementCallBehavior(
                    PlacementCallCount,
                    FirstPlacement,
                    SecondPlacement,
                    PackingPolicy,
                    Options,
                )
            return FirstPlacement if PackingPolicy.Enabled else SecondPlacement

        def Feedback(Placement, RoutingSpacing, *_Arguments):
            IsFirst = Placement.Placed.PlacedGates[0].X == 0
            X = Placement.Placed.PlacedGates[0].X
            DefaultFeedback = SimpleNamespace(
                Score=((0,) if IsFirst else (1,)),
                BoundaryOverflow=(
                    FeedbackBoundaryOverflowByX or {}
                ).get(X, 0),
                PinScarcityCount=0,
                GuideOverflowPeak=0,
                GuideOverflowCells=0,
                PinEscapeConflictCount=0,
                EstimatedGlobalExtensionNodes=0,
                EstimatedGlobalExtensionNets=0,
                PreOwnedNodeCount=0,
                RoutingSpacing=RoutingSpacing,
            )
            if FeedbackBehavior is not None:
                return FeedbackBehavior(
                    Placement,
                    RoutingSpacing,
                    _Arguments[-1] if _Arguments else None,
                    DefaultFeedback,
                )
            return DefaultFeedback

        SuccessfulRoutes = {
            X: SimpleNamespace(
                CandidateX=X,
                Wires=[],
                Supports=[],
                RoutingControlEffectiveness={},
            )
            for X in (0, 10)
        }

        def Route(Placement, **Options):
            X = Placement.Placed.PlacedGates[0].X
            RouteOrder.append(X)
            DeadlineIdentities.append(id(Options["Deadline"]))
            if RouteBehavior is not None:
                return RouteBehavior(X, SuccessfulRoutes[X], Options)
            return SuccessfulRoutes[X]

        Snapshot = SimpleNamespace(ToDictionary=lambda: {})
        Policy = replace(
            LocalFirstPhysicalDesignPolicy,
            RuntimeBudgetSeconds=RuntimeBudgetSeconds,
            Placement=replace(
                LocalFirstPhysicalDesignPolicy.Placement,
                RoutingFeedbackIterations=0,
                EnableRoutingFeedback=True,
            ),
            NandPacking=replace(
                LocalFirstPhysicalDesignPolicy.NandPacking,
                RetainedPlacementCandidates=2,
            ),
        )
        Netlist = SimpleNamespace(
            Top="Top",
            Modules={
                "Top": (
                    NetlistModule
                    if NetlistModule is not None
                    else SimpleNamespace(Gates=[object()])
                )
            },
        )

        with (
            patch("PhysicalDesign.Orchestration.Runner.ValidateNandOnlyDesign"),
            patch("PhysicalDesign.Orchestration.Runner.PlacePcbGraph", side_effect=PlaceGraph),
            patch(
                "PhysicalDesign.Orchestration.Runner.ValidatePlacedCellElectricalIsolation",
                side_effect=IsolationBehavior,
            ),
            patch("PhysicalDesign.Orchestration.Runner.BuildRoutingResources"),
            patch(
                "PhysicalDesign.Orchestration.Runner.MeasurePlacementRoutingFeedback",
                side_effect=Feedback,
            ),
            patch("PhysicalDesign.Orchestration.Runner.RoutePcbDesign", side_effect=Route),
            patch(
                "PhysicalDesign.Orchestration.Runner.BuildLocalFirstSnapshot",
                return_value=Snapshot,
            ),
            patch(
                "PhysicalDesign.Orchestration.Runner.MeasurePcbDesign",
                return_value=(1, 1, 1, 1),
            ),
        ):
            Result = _PlaceAndRoutePcbWithPolicy(
                Netlist,
                ProgressCallback=ProgressCallback,
                Policy=Policy,
                Technology=DefaultRedstoneRoutingTechnology,
                RequestedStrategy=RoutingStrategy.Default,
                UsedStrategy=RoutingStrategy.Default,
                RoutedValidationCallback=RoutedValidationCallback,
            )

        return Result, SuccessfulRoutes, DeadlineIdentities

    def testFailureArtifactWriteErrorIsNonFatal(self) -> None:
        with patch(
            "Compilation.Pipeline.WriteRoutingFailureArtifact",
            side_effect=OSError("diagnostic disk failure"),
        ):
            self.assertIsNone(TryWriteRoutingFailureArtifact())

    def testPlacementFailureHistorySnapshotPreservesActionableFields(
        self,
    ) -> None:
        Failure = RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="InitialCandidateAssignment",
            AffectedNets=("A", "B"),
            RepairActions=("RelocateAffectedClusters",),
            Detail="complete bounded domains conflict",
            Diagnostics={
                "Action": "advance-placement",
                "ConflictGraph": {
                    "Classification": "portal-coverage-pair-conflict",
                    "PairwiseIncompatibleEdges": [["A", "B"]],
                },
                "Deadline": {"Expired": False},
                "PlacementGenerationFailures": [{"Large": "x" * 10_000}],
                "PlacementGenerationDecisions": [{"Large": "y" * 10_000}],
                "PlacementAttempts": [{"Large": "z" * 10_000}],
                "JointPlacementStateEvents": [{"Large": "w" * 10_000}],
                "AssignmentCutHistory": [{"Large": "v" * 10_000}],
                "CurrentAssignmentCut": {"Large": "u" * 10_000},
                "ActivePlacementConstraints": {"Large": "t" * 10_000},
            },
        )

        Snapshot = BuildPlacementFailureHistorySnapshot(Failure)

        self.assertEqual(Snapshot["Reason"], Failure.Reason)
        self.assertEqual(Snapshot["AffectedNets"], ("A", "B"))
        Diagnostics = Snapshot["Diagnostics"]
        self.assertEqual(Diagnostics["Action"], "advance-placement")
        self.assertEqual(
            Diagnostics["ConflictGraph"]["PairwiseIncompatibleEdges"],
            [["A", "B"]],
        )
        self.assertEqual(Diagnostics["Deadline"], {"Expired": False})
        self.assertNotIn("PlacementGenerationFailures", Diagnostics)
        self.assertNotIn("PlacementGenerationDecisions", Diagnostics)
        self.assertNotIn("PlacementAttempts", Diagnostics)
        self.assertNotIn("JointPlacementStateEvents", Diagnostics)
        self.assertNotIn("AssignmentCutHistory", Diagnostics)
        self.assertNotIn("CurrentAssignmentCut", Diagnostics)
        self.assertNotIn("ActivePlacementConstraints", Diagnostics)

    def testRepeatedPlacementFailureSnapshotsRemainLinear(self) -> None:
        History = []
        SnapshotSizes = []
        for Index in range(12):
            Failure = RoutingFailure(
                Reason=RoutingFailureReason.RuntimeBudgetExceeded,
                Stage="PlacementGeneration",
                AffectedNets=(f"Signal{Index}",),
                Detail="bounded placement slice expired",
                Diagnostics={
                    "Action": "advance-placement",
                    "ConflictGraph": {
                        "Classification": "candidate-starvation-placement-conflict",
                        "NoCandidateSignals": [f"Signal{Index}"],
                    },
                    "PlacementGenerationFailures": list(History),
                    "JointPlacementStateEvents": [
                        {
                            "Failure": {
                                "PlacementGenerationFailures": list(History),
                            },
                        },
                    ],
                },
            )
            Snapshot = BuildPlacementFailureHistorySnapshot(Failure)
            SnapshotSizes.append(len(json.dumps(Snapshot, default=str)))
            History.append({
                "Attempt": Index,
                "Diagnostics": Snapshot,
            })

        OuterDiagnostics = {
            "PlacementGenerationFailures": History,
        }
        Serialized = json.dumps(OuterDiagnostics, default=str)

        self.assertEqual(len(History), 12)
        self.assertLess(max(SnapshotSizes) - min(SnapshotSizes), 16)
        self.assertLess(len(Serialized), 12 * 1_000)
        self.assertTrue(all(
            "PlacementGenerationFailures"
            not in Entry["Diagnostics"]["Diagnostics"]
            for Entry in History
        ))

    def testLocalizedRegenerationRetainsOnlyUnaffectedCandidates(self) -> None:
        CandidateA = object()
        CandidateB = object()
        Retained, Metadata = RetainUnaffectedCandidateCache(
            {"A": [CandidateA], "B": [CandidateB], "Empty": []},
            {"A": {"a": 1}, "B": {"b": 2}},
            frozenset({"B"}),
        )
        self.assertEqual(Retained, {"A": (CandidateA,)})
        self.assertEqual(Metadata, {"A": {"a": 1}})

    def testHighFanoutRoutingReserveEndsAfterTwoCandidates(self) -> None:
        Common = {
            "HasRemainingPlacementAlternative": True,
            "ReconvergentAccessPressure": True,
        }
        self.assertEqual(
            PlacementFeedbackRoutingSlotCount(
                **Common,
                AttemptedCandidateCount=0,
            ),
            2,
        )
        self.assertEqual(
            PlacementFeedbackRoutingSlotCount(
                **Common,
                AttemptedCandidateCount=1,
            ),
            2,
        )
        self.assertEqual(
            PlacementFeedbackRoutingSlotCount(
                **Common,
                AttemptedCandidateCount=2,
            ),
            1,
        )

    def testRankedTopologyPortfolioReservesOneLaterAttempt(self) -> None:
        self.assertEqual(
            TopologyPortfolioRoutingFraction(
                HasRemainingPlacementAlternative=True,
                AttemptedCandidateCount=0,
            ),
            0.75,
        )
        self.assertEqual(
            TopologyPortfolioRoutingFraction(
                HasRemainingPlacementAlternative=True,
                AttemptedCandidateCount=1,
            ),
            1.0,
        )
        self.assertEqual(
            TopologyPortfolioRoutingFraction(
                HasRemainingPlacementAlternative=False,
                AttemptedCandidateCount=0,
            ),
            1.0,
        )
        self.assertEqual(
            TopologyPortfolioRoutingFraction(
                HasRemainingPlacementAlternative=True,
                AttemptedCandidateCount=1,
                AuthoritativeMandatoryAccessConflictObserved=True,
            ),
            1.0,
        )
        self.assertEqual(
            PlacementFeedbackRoutingSlotCount(
                HasRemainingPlacementAlternative=True,
                ReconvergentAccessPressure=False,
                AttemptedCandidateCount=0,
            ),
            1,
        )
        self.assertTrue(
            ShouldGiveRankedJointPortfolioLeadSlice(
                ActiveRelocatedPortfolioCandidate=True,
                CandidateId="ranked-lead",
                PrimaryCandidateId="ranked-lead",
            )
        )
        for Override in (
            {
                "ActiveRelocatedPortfolioCandidate": False,
            },
            {
                "CandidateId": "sibling",
            },
            {
                "PrimaryCandidateId": None,
            },
        ):
            with self.subTest(Override=Override):
                self.assertFalse(
                    ShouldGiveRankedJointPortfolioLeadSlice(
                        **{
                            "ActiveRelocatedPortfolioCandidate": True,
                            "CandidateId": "ranked-lead",
                            "PrimaryCandidateId": "ranked-lead",
                            **Override,
                        }
                    )
                )

    def testAuthoritativePortalConflictPromotesRenameIndependentScore(
        self,
    ) -> None:
        Profile = TopologyDemandProfile(
            MaximumFanout=4,
            ReconvergentCutCount=1,
            QualifyingReconvergentCutCount=1,
            MaximumReconvergentFanout=4,
            PeakBoundaryDemand=2,
        )

        def FailureFor(First: str, Second: str) -> RoutingFailure:
            return RoutingFailure(
                Reason=RoutingFailureReason.TrackAssignmentConflict,
                Stage="InitialCandidateAssignment",
                AffectedNets=(First, Second),
                Locations=((10, 3, 20), (11, 3, 20)),
                Diagnostics={
                    "MandatoryConflictPositionCount": 2,
                    "MandatoryAccessProof": {
                        "Kind": (
                            "generated-fixed-portal-domain-exhausted"
                        ),
                        "Complete": True,
                        "BudgetExhausted": False,
                        "DeadlineExceeded": False,
                        "ConflictPositionCount": 2,
                        "ConflictFingerprint": "anonymous-proof",
                    },
                    "ConflictGraph": {
                        "Classification": (
                            "mandatory-boundary-capacity-cut"
                        ),
                        "PairwiseIncompatibleEdges": [
                            [First, Second],
                        ],
                    },
                },
            )

        FirstFailure = FailureFor("First", "Second")
        RenamedFailure = FailureFor("RenamedA", "RenamedB")
        self.assertTrue(
            IsAuthoritativeMandatoryAccessConflict(FirstFailure)
        )
        First = PromoteAuthoritativeMandatoryAccessConflict(
            Profile,
            FirstFailure,
        )
        Renamed = PromoteAuthoritativeMandatoryAccessConflict(
            Profile,
            RenamedFailure,
        )
        self.assertEqual(First.MandatoryAccessConflictResources, 2)
        self.assertEqual(First.JointOrderKey[0], 1)
        self.assertEqual(
            First.MandatoryAccessConflictFingerprint,
            Renamed.MandatoryAccessConflictFingerprint,
        )
        MissingProof = replace(
            FirstFailure,
            Diagnostics={
                "ConflictGraph": {
                    "Classification": (
                        "mandatory-boundary-capacity-cut"
                    ),
                },
            },
        )
        self.assertFalse(
            IsAuthoritativeMandatoryAccessConflict(MissingProof)
        )
        self.assertIs(
            PromoteAuthoritativeMandatoryAccessConflict(
                Profile,
                MissingProof,
            ),
            Profile,
        )
        BudgetExhausted = replace(
            FirstFailure,
            Diagnostics={
                **FirstFailure.Diagnostics,
                "MandatoryAccessProof": {
                    **FirstFailure.Diagnostics["MandatoryAccessProof"],
                    "BudgetExhausted": True,
                },
            },
        )
        self.assertFalse(
            IsAuthoritativeMandatoryAccessConflict(BudgetExhausted)
        )

    def testObservedInterfaceGraphSelectsBoundedStructuralRefinement(
        self,
    ) -> None:
        def BuildCut(Prefix: str) -> RoutingAssignmentCut:
            Cut = RoutingAssignmentCut.FromFailure(RoutingFailure(
                Reason=RoutingFailureReason.BoundaryEscapeInfeasible,
                Stage="ClusterBoundaryLease",
                Diagnostics={
                    "ConflictGraph": {
                        "Classification": "saturated-boundary-cut",
                        "ConflictSignals": [
                            f"{Prefix}{Index}" for Index in range(5)
                        ],
                        "RelocationSignals": [
                            f"{Prefix}{Index}" for Index in range(5)
                        ],
                        "ObservedPatternConflictEdges": [
                            [f"{Prefix}0", f"{Prefix}1"],
                            [f"{Prefix}0", f"{Prefix}2"],
                            [f"{Prefix}3", f"{Prefix}4"],
                        ],
                    },
                },
            ))
            assert Cut is not None
            return Cut

        OriginalFingerprints = {
            f"Net{Index}": f"Topology{Index}"
            for Index in range(5)
        }
        RenamedFingerprints = {
            f"Renamed{Index}": f"Topology{Index}"
            for Index in range(5)
        }
        Original = SelectCutDrivenClusterRefinementSignals(
            BuildCut("Net"),
            OriginalFingerprints,
            MaximumSignals=4,
        )
        Renamed = SelectCutDrivenClusterRefinementSignals(
            BuildCut("Renamed"),
            RenamedFingerprints,
            MaximumSignals=4,
        )
        self.assertLessEqual(len(Original), 4)
        self.assertEqual(
            sorted(OriginalFingerprints[Signal] for Signal in Original),
            sorted(RenamedFingerprints[Signal] for Signal in Renamed),
        )

    def testPlacementOnlyConstraintSelectsStructuralRefinement(self) -> None:
        Constraints = PlacementAssignmentConstraintSet(
            PairwiseConflictEdges=(
                ("CarryIn", "NandNet0"),
                ("NandNet0", "Propagate0"),
            ),
        )
        self.assertEqual(
            SelectCutDrivenClusterRefinementSignals(
                None,
                {
                    "CarryIn": "Input",
                    "NandNet0": "Internal",
                    "Propagate0": "Propagate",
                },
                Constraints=Constraints,
            ),
            frozenset(("CarryIn", "NandNet0", "Propagate0")),
        )

    def testStructuredTopologyStarvationOpensOnePinBankRepair(self) -> None:
        Cut = RoutingAssignmentCut(
            Classification=(
                RoutingAssignmentCutClassification
                .CandidateStarvationPlacementConflict
            ),
            ConflictGraphJson="{}",
            ConflictSignals=("Starved",),
            NoCandidateSignals=("Starved",),
        )
        Active = PlacementAssignmentConstraintSet(
            HigherOrderSignalSets=(("PriorLeft", "PriorRight"),),
        )
        self.assertEqual(
            SelectImmediateTopologyPinBankRepairSignals(
                TopologyAccessRepairEligible=True,
                AssignmentCut=Cut,
                Constraints=Active,
            ),
            frozenset(("Starved",)),
        )
        self.assertFalse(SelectImmediateTopologyPinBankRepairSignals(
            TopologyAccessRepairEligible=False,
            AssignmentCut=Cut,
            Constraints=Active,
        ))
        self.assertFalse(SelectImmediateTopologyPinBankRepairSignals(
            TopologyAccessRepairEligible=True,
            AssignmentCut=Cut,
            Constraints=PlacementAssignmentConstraintSet(),
        ))
        MandatorySelfCut = replace(
            Cut,
            Classification=(
                RoutingAssignmentCutClassification
                .MandatoryAccessSelfConflict
            ),
        )
        self.assertEqual(
            SelectImmediateTopologyPinBankRepairSignals(
                TopologyAccessRepairEligible=True,
                TopologyRequiresJointPortfolio=True,
                AssignmentCut=MandatorySelfCut,
                Constraints=Active,
            ),
            frozenset(("Starved",)),
        )
        self.assertFalse(SelectImmediateTopologyPinBankRepairSignals(
            TopologyAccessRepairEligible=True,
            TopologyRequiresJointPortfolio=False,
            AssignmentCut=MandatorySelfCut,
            Constraints=Active,
        ))

    def testPostPinBankRepairContinuesOnlyForNewImmediateProof(
        self,
    ) -> None:
        self.assertTrue(ShouldContinuePostPinBankRepairEpoch(
            CandidatePostPinBankRepairEpoch=True,
            InternalPinBankRetryPending=True,
            ImmediateTopologyStarvationSignals=("NewEndpoint",),
        ))
        for Overrides in (
            {"CandidatePostPinBankRepairEpoch": False},
            {"InternalPinBankRetryPending": False},
            {"ImmediateTopologyStarvationSignals": ()},
        ):
            Arguments = {
                "CandidatePostPinBankRepairEpoch": True,
                "InternalPinBankRetryPending": True,
                "ImmediateTopologyStarvationSignals": ("NewEndpoint",),
                **Overrides,
            }
            self.assertFalse(ShouldContinuePostPinBankRepairEpoch(
                **Arguments
            ))

    def testTargetedPinBankPackingUsesOneBoundedHalfPortfolio(self) -> None:
        Base = LocalFirstPhysicalDesignPolicy.NandPacking
        Targeted = BuildTargetedPinBankPackingPolicy(Base)
        self.assertFalse(Targeted.GraphBeamEnabled)
        self.assertTrue(Targeted.EnableJointClusterOrientation)
        self.assertEqual(Targeted.RetainedJointPlacementCandidates, 1)
        self.assertEqual(
            Targeted.JointPlacementBeamWidth,
            max(16, Base.JointPlacementBeamWidth // 2),
        )
        self.assertEqual(
            Targeted.JointPlacementPassLimit,
            max(4, Base.JointPlacementPassLimit // 2),
        )
        AlreadySmall = replace(
            Base,
            JointPlacementBeamWidth=8,
            JointPlacementPassLimit=3,
        )
        SmallTargeted = BuildTargetedPinBankPackingPolicy(AlreadySmall)
        self.assertEqual(SmallTargeted.JointPlacementBeamWidth, 8)
        self.assertEqual(SmallTargeted.JointPlacementPassLimit, 3)

    def testExhaustedRepeaterAccessCutOpensOneRouteOnlyRepair(
        self,
    ) -> None:
        def BuildFailure(Signal: str) -> RoutingFailure:
            return RoutingFailure(
                Reason=RoutingFailureReason.RepeaterAccessInfeasible,
                Stage="NegotiatedDetailedRouting",
                AffectedNets=(Signal,),
                Diagnostics={
                    "ConflictGraph": {
                        "Classification": "sparse-region-route-cut",
                    },
                    "SearchExpansionEscalations": {Signal: 90_000},
                    "Region": {
                        "ExpandedSides": ["MaximumX"],
                        "NativeSearch": [
                            {
                                "Status": "NoPath",
                                "NoPathReason": "SearchLimitReached",
                                "BoundaryFrontierNodes": [],
                                "RepeaterRejectedCount": 500,
                                "RepeaterReservationCount": 0,
                            },
                        ],
                    },
                },
            )

        self.assertEqual(
            SelectExhaustedRepeaterAccessCutSignals(
                BuildFailure("Original"),
            ),
            frozenset(("Original",)),
        )
        self.assertEqual(
            SelectExhaustedRepeaterAccessCutSignals(
                BuildFailure("Renamed"),
            ),
            frozenset(("Renamed",)),
        )
        NotExpanded = BuildFailure("Original")
        NotExpanded.Diagnostics["Region"]["ExpandedSides"] = []
        self.assertFalse(
            SelectExhaustedRepeaterAccessCutSignals(NotExpanded)
        )
        Reserved = BuildFailure("Original")
        Reserved.Diagnostics["Region"]["NativeSearch"][0][
            "RepeaterReservationCount"
        ] = 1
        self.assertFalse(
            SelectExhaustedRepeaterAccessCutSignals(Reserved)
        )
        MixedProof = BuildFailure("Original")
        MixedProof.Diagnostics["Region"]["NativeSearch"].append({
            "Status": "NoPath",
            "NoPathReason": "NoRepeaterRepairPath",
            "BoundaryFrontierNodes": [],
            "RepeaterRejectedCount": 1,
            "RepeaterReservationCount": 0,
        })
        self.assertEqual(
            SelectExhaustedRepeaterAccessCutSignals(MixedProof),
            frozenset(("Original",)),
        )

    def testRetainedExactPortfolioUsesOneRoutingSlotPerSibling(
        self,
    ) -> None:
        self.assertEqual(
            RetainedPlacementRoutingSlotCount(
                RemainingRetainedCandidates=5,
                HighFanoutFeedbackRoutingSlots=2,
                HasRemainingPlacementAlternative=True,
                TopologyPortfolioTriggered=True,
                AttemptedCandidateCount=1,
            ),
            5,
        )
        self.assertEqual(
            RetainedPlacementRoutingSlotCount(
                RemainingRetainedCandidates=1,
                HighFanoutFeedbackRoutingSlots=1,
                HasRemainingPlacementAlternative=True,
                TopologyPortfolioTriggered=True,
                AttemptedCandidateCount=0,
            ),
            2,
        )

    def testExactMandatoryAccessConflictIsNotRoutingEligible(
        self,
    ) -> None:
        Legal = SimpleNamespace(
            TopologyDemand=TopologyDemandProfile(
                MaximumFanout=4,
                ReconvergentCutCount=1,
                QualifyingReconvergentCutCount=1,
                MaximumReconvergentFanout=4,
                PeakBoundaryDemand=3,
            ),
        )
        Illegal = SimpleNamespace(
            TopologyDemand=replace(
                Legal.TopologyDemand,
                MandatoryAccessConflictResources=1,
                MandatoryAccessConflictSignals=("Left", "Right"),
            ),
        )

        self.assertTrue(PlacementCandidateIsExactAccessLegal(Legal))
        self.assertFalse(PlacementCandidateIsExactAccessLegal(Illegal))

    def testBoundedRetryPoolRetainsOldAndNewGeometry(self) -> None:
        Candidates = [
            SimpleNamespace(CandidateId=CandidateId)
            for CandidateId in ("old-0", "old-1", "new-0", "new-1")
        ]

        Selected = SelectBoundedDiverseCandidatePool(
            Candidates,
            2,
            frozenset({"old-0", "old-1"}),
        )

        self.assertEqual(
            [Candidate.CandidateId for Candidate in Selected],
            ["old-0", "new-0"],
        )

    def testEmptyLocalClaimIntersectionReleasesNothing(self) -> None:
        Claims = (
            SimpleNamespace(Signal="OwnedA"),
            SimpleNamespace(Signal="OwnedB"),
        )
        self.assertEqual(
            SelectReleasableLocalClaimSignals(
                frozenset({"Unrelated"}),
                Claims,
            ),
            frozenset(),
        )
        self.assertEqual(
            SelectReleasableLocalClaimSignals(
                frozenset({"OwnedB", "Unrelated"}),
                Claims,
            ),
            frozenset({"OwnedB"}),
        )

    def testMandatoryBoundaryCutRequiresPackedAccessRepair(self) -> None:
        Failure = RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="InitialCandidateAssignment",
            AffectedNets=("First", "Second"),
            Diagnostics={
                "ConflictGraph": {
                    "Classification": "mandatory-boundary-capacity-cut",
                },
            },
        )

        self.assertTrue(FailureRequiresPackedAccessRepair(Failure))
        self.assertFalse(FailureRequiresPackedAccessRepair(
            RoutingFailure(
                Reason=RoutingFailureReason.TrackAssignmentConflict,
                Stage="InitialCandidateAssignment",
            )
        ))
        self.assertTrue(FailureRequiresPackedAccessRepair(
            RoutingFailure(
                Reason=RoutingFailureReason.RepeaterAccessInfeasible,
                Stage="NegotiatedDetailedRouting",
            )
        ))
        for Classification in (
            "mandatory-access-self-conflict",
            "portal-coverage-pair-conflict",
            "relocated-higher-order-conflict",
            "relocated-larger-matching-failure",
            "relocated-multi-pair-conflict",
            "relocated-pairwise-incompatibility",
        ):
            self.assertTrue(FailureRequiresPackedAccessRepair(
                RoutingFailure(
                    Reason=RoutingFailureReason.TrackAssignmentConflict,
                    Stage="TrackAssignment",
                    Diagnostics={
                        "ConflictGraph": {
                            "Classification": Classification,
                        },
                    },
                )
            ))

    def testMandatoryAccessSelfConflictOpensOneExactTopologyEpoch(
        self,
    ) -> None:
        Failure = RoutingFailure(
            Reason=RoutingFailureReason.NoPinAccessPattern,
            Stage="InitialCandidateAssignment",
            AffectedNets=("OpaqueEndpoint",),
            Diagnostics={
                "ConflictFingerprint": "anonymous-portal-tuple-proof",
                "ConflictGraph": {
                    "Classification": "mandatory-access-self-conflict",
                    "ConflictSignals": ["OpaqueEndpoint"],
                    "NoCandidateSignals": ["OpaqueEndpoint"],
                    "RelocationSignals": ["OpaqueEndpoint"],
                },
            },
        )
        Cut = RoutingAssignmentCut.FromFailure(Failure)
        self.assertIsNotNone(Cut)
        assert Cut is not None
        Constraints = PlacementAssignmentConstraintSet().WithCut(Cut)
        Epoch = BuildTopologyCutEpochIdentity(Cut, Constraints)

        self.assertTrue(FailureRequestsPlacementAdvance(Failure))
        self.assertTrue(FailureRequiresPackedAccessRepair(Failure))
        self.assertTrue(AssignmentCutHasBoundedExactCore(Cut))
        self.assertTrue(ShouldOpenTopologyCutEpoch(
            TopologyRequiresJointPortfolio=True,
            AssignmentCut=Cut,
            Epoch=Epoch,
            OpenedEpochs=(),
        ))
        self.assertFalse(ShouldOpenTopologyCutEpoch(
            TopologyRequiresJointPortfolio=True,
            AssignmentCut=Cut,
            Epoch=Epoch,
            OpenedEpochs=(Epoch,),
        ))

    def testPortfolioGenerationFloorIsOneAbsoluteRoutingReserve(
        self,
    ) -> None:
        Policy = replace(
            LocalFirstPhysicalDesignPolicy,
            RuntimeBudgetSeconds=100.0,
        )

        GenerationNotAfter = PlacementPortfolioGenerationNotAfter(
            Policy,
            DeadlineExpiresAt=100.0,
            CurrentTime=10.0,
        )

        self.assertEqual(GenerationNotAfter, 80.0)
        self.assertEqual(
            100.0 - GenerationNotAfter,
            PlacementGenerationRoutingReserveSeconds(Policy),
        )

    def testDenseBoundaryInterfaceReservesHalfForRouting(self) -> None:
        Policy = replace(
            LocalFirstPhysicalDesignPolicy,
            RuntimeBudgetSeconds=30.0,
        )
        self.assertEqual(
            PlacementGenerationRoutingReserveSeconds(
                Policy,
                RequiresDenseBoundaryRouting=True,
            ),
            15.0,
        )

    def testDenseBoundaryLeaseOwnsOneOuterRoutingSlot(self) -> None:
        Policy = LocalFirstPhysicalDesignPolicy
        Request = SimpleNamespace(TargetTerminals=((1, 1, 1), (2, 1, 1)))
        DensePlaced = SimpleNamespace(
            ClusterBoundaryLeaseRequests=(
                Request,
                Request,
                Request,
                Request,
                Request,
                Request,
            ),
        )
        SparsePlaced = SimpleNamespace(
            ClusterBoundaryLeaseRequests=(Request,),
        )
        self.assertTrue(
            RequiresDenseBoundaryLeaseRouting(DensePlaced, Policy)
        )
        self.assertFalse(
            RequiresDenseBoundaryLeaseRouting(SparsePlaced, Policy)
        )
        self.assertEqual(
            PlacementPortfolioGenerationNotAfter(
                Policy,
                DeadlineExpiresAt=30.0,
                CurrentTime=0.0,
                RequiresDenseBoundaryRouting=True,
            ),
            15.0,
        )

    def testCompactTopologyPortfolioCarriesBoundaryLeaseInterface(
        self,
    ) -> None:
        self.assertTrue(ShouldEnableClusterBoundaryLeaseInterface(
            ScaleGeometryPressure=False,
            TopologyRequiresJointPortfolio=True,
        ))
        self.assertTrue(ShouldEnableClusterBoundaryLeaseInterface(
            ScaleGeometryPressure=False,
            TopologyRequiresJointPortfolio=False,
        ))
        self.assertFalse(ShouldEnableClusterBoundaryLeaseInterface(
            ScaleGeometryPressure=True,
            TopologyRequiresJointPortfolio=True,
            IsPostPinBankRepairEpoch=True,
        ))

    def testStructuredJointRepairUsesOneLeaseStateForNewGeometry(
        self,
    ) -> None:
        Request = SimpleNamespace(TargetTerminals=((1, 1, 1),) * 3)
        DensePlacement = SimpleNamespace(
            Placed=SimpleNamespace(
                ClusterBoundaryLeaseRequests=(Request,) * 6,
                LocalRouteDiagnostics={},
            ),
        )
        self.assertEqual(ClusterBoundaryLeaseStateCount(DensePlacement), 3)
        DensePlacement.Placed.LocalRouteDiagnostics = {
            "__JointClusterPlacement__": {
                "ActiveAssignmentConstraints": {
                    "Fingerprint": "cut",
                    "PairwiseConflictEdges": [["First", "Second"]],
                    "HigherOrderSignalSets": [],
                },
            },
        }
        self.assertEqual(ClusterBoundaryLeaseStateCount(DensePlacement), 1)

    def testRoutedComponentHandoffUsesOneOrdinaryGlobalLeaseState(
        self,
    ) -> None:
        Request = SimpleNamespace(TargetTerminals=((1, 1, 1),) * 3)
        Placement = SimpleNamespace(
            Placed=SimpleNamespace(
                ClusterBoundaryLeaseRequests=(Request,) * 6,
                LocalRouteDiagnostics={},
                RoutedComponentTemplates=(SimpleNamespace(),),
            ),
        )
        self.assertEqual(ClusterBoundaryLeaseStateCount(Placement), 1)

    def testFrozenPrePlacementTrackWitnessUsesOneLeaseState(
        self,
    ) -> None:
        """A frozen capacity witness must not reopen lease alternatives."""
        Request = SimpleNamespace(TargetTerminals=((1, 1, 1),) * 3)
        Placement = SimpleNamespace(
            Placed=SimpleNamespace(
                ClusterBoundaryLeaseRequests=(Request,) * 6,
                LocalRouteDiagnostics={},
                RoutedComponentTemplates=(),
            ),
        )
        self.assertEqual(ClusterBoundaryLeaseStateCount(Placement), 3)
        self.assertEqual(
            ClusterBoundaryLeaseStateCount(
                Placement,
                HasFrozenTrackAssignment=True,
            ),
            1,
        )

    def testEmptySerializedConstraintManifestDoesNotSuppressLeaseStates(
        self,
    ) -> None:
        Empty = {
            "Fingerprint": "empty",
            "PairwiseConflictEdges": [],
            "HigherOrderSignalSets": [],
        }
        self.assertFalse(
            SerializedPlacementAssignmentConstraintsAreActive(Empty)
        )
        Request = SimpleNamespace(TargetTerminals=((1, 1, 1),) * 3)
        Placement = SimpleNamespace(
            Placed=SimpleNamespace(
                ClusterBoundaryLeaseRequests=(Request,) * 6,
                LocalRouteDiagnostics={
                    "__JointClusterPlacement__": {
                        "ActiveAssignmentConstraints": Empty,
                    },
                },
            ),
        )
        self.assertEqual(ClusterBoundaryLeaseStateCount(Placement), 3)

    def testDenseLeaseProofSlicesReserveRepairTime(self) -> None:
        self.assertEqual(ClusterBoundaryLeaseStateSliceSeconds(3, 0), 8.0)
        self.assertEqual(ClusterBoundaryLeaseStateSliceSeconds(3, 1), 2.5)
        self.assertEqual(ClusterBoundaryLeaseStateSliceSeconds(3, 2), 2.5)
        self.assertIsNone(ClusterBoundaryLeaseStateSliceSeconds(1, 0))
        self.assertEqual(ClusterBoundaryLeaseEndgameReserveSeconds(3), 12.0)
        self.assertEqual(ClusterBoundaryLeaseEndgameReserveSeconds(1), 0.0)

    def testDenseLeaseEvidenceIsRequiredForGenericPairRepair(self) -> None:
        Policy = LocalFirstPhysicalDesignPolicy
        Request = SimpleNamespace(TargetTerminals=((1, 1, 1),) * 3)
        DenseCandidate = SimpleNamespace(
            TopologyDemand=TopologyDemandProfile(
                MaximumFanout=0,
                ReconvergentCutCount=0,
                QualifyingReconvergentCutCount=0,
                MaximumReconvergentFanout=0,
                PeakBoundaryDemand=0,
                MaximumTerminalBankDemand=(
                    Policy.Organization.MaximumClusterEntrances
                ),
            ),
            Placement=SimpleNamespace(
                Placed=SimpleNamespace(
                    ClusterBoundaryLeaseRequests=(Request,) * 6,
                ),
            ),
        )
        SparseCandidate = SimpleNamespace(
            TopologyDemand=TopologyDemandProfile(
                MaximumFanout=0,
                ReconvergentCutCount=0,
                QualifyingReconvergentCutCount=0,
                MaximumReconvergentFanout=0,
                PeakBoundaryDemand=0,
            ),
            Placement=SimpleNamespace(
                Placed=SimpleNamespace(
                    ClusterBoundaryLeaseRequests=(Request,),
                ),
            ),
        )
        self.assertTrue(
            HasDenseBoundaryLeaseRepairEligibility(DenseCandidate, Policy)
        )
        self.assertFalse(
            HasDenseBoundaryLeaseRepairEligibility(SparseCandidate, Policy)
        )
        Failure = RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="TrackAssignment",
            Diagnostics={
                "ClusterBoundaryLeaseScheduler": {
                    "Attempts": [
                        {"OwnershipFingerprint": "owner-b"},
                        {"OwnershipFingerprint": "owner-a"},
                        {"OwnershipFingerprint": "owner-a"},
                        {"OwnershipFingerprint": ""},
                    ],
                },
            },
        )
        self.assertEqual(
            ExtractAccessDistinctLeaseOwnershipFingerprints(Failure),
            ("owner-a", "owner-b"),
        )

    def testPairedLeaseRepairRequiresExactlyTwoDisjointPairs(self) -> None:
        def BuildCut(Edges: tuple[tuple[str, str], ...]) -> RoutingAssignmentCut:
            return RoutingAssignmentCut(
                ConflictFingerprint="cut",
                Classification=(
                    RoutingAssignmentCutClassification.PairwiseIncompatibility
                ),
                ConflictGraphJson="{}",
                ConflictSignals=tuple(sorted(
                    Signal for Edge in Edges for Signal in Edge
                )),
                PairwiseConflictEdges=Edges,
            )

        self.assertTrue(IsExactPairedLeaseCut(BuildCut((
            ("Left0", "Right0"),
            ("Left1", "Right1"),
        ))))
        self.assertFalse(IsExactPairedLeaseCut(BuildCut((
            ("Left0", "Right0"),
            ("Left1", "Right1"),
            ("Left2", "Right2"),
        ))))
        self.assertFalse(IsExactPairedLeaseCut(BuildCut((
            ("Left", "Right0"),
            ("Left", "Right1"),
        ))))

    def testRepeatedBroadLeaseCutProjectsOneStructuralPairRepair(self) -> None:
        def BuildCut(
            Ownership: str,
            Edges: tuple[tuple[str, str], ...],
        ) -> RoutingAssignmentCut:
            return RoutingAssignmentCut(
                ConflictFingerprint=Ownership,
                Classification=(
                    RoutingAssignmentCutClassification
                    .PortalCoveragePairConflict
                ),
                ConflictGraphJson="{}",
                PairwiseConflictEdges=Edges,
                MandatoryAccessOwnershipFingerprint=Ownership,
            )

        Previous = BuildCut("access-a", (
            ("Left0", "Right0"),
            ("Left1", "Right1"),
            ("Shared0", "Shared1"),
        ))
        Current = BuildCut("access-b", (
            ("Left0", "Right0"),
            ("Left1", "Right1"),
            ("New0", "New1"),
        ))
        Fingerprints = {
            "Left0": "a0", "Right0": "b0",
            "Left1": "a1", "Right1": "b1",
            "Shared0": "s0", "Shared1": "s1",
            "New0": "n0", "New1": "n1",
        }
        self.assertEqual(
            SelectRepeatedPairedLeaseSubcutSignals(
                [Previous], Current, Fingerprints,
            ),
            frozenset({"Left0", "Right0", "Left1", "Right1"}),
        )
        self.assertEqual(
            SelectRepeatedPairedLeaseSubcutSignals(
                [Previous],
                BuildCut("access-a", Current.PairwiseConflictEdges),
                Fingerprints,
            ),
            frozenset(),
        )

    def testConflictFeedbackCarriesCumulativeCutSignals(self) -> None:
        self.assertEqual(
            ExtractPlacementRelocationSignals(
                RoutingFailure(
                    Reason=RoutingFailureReason.TrackAssignmentConflict,
                    Stage="NegotiatedDetailedRouting",
                    Diagnostics={
                        "ConflictGraph": {
                            "CumulativeConflictSignals": [
                                "A",
                                "B",
                                "C",
                            ],
                            "CongestionCutSignals": ["D", "E"],
                        },
                    },
                )
            ),
            frozenset({"A", "B", "C", "D", "E"}),
        )

    def testRepeatedCutDiversifiesOnlyAcrossDistinctAccessTopologies(
        self,
    ) -> None:
        Failure = self.BuildHigherOrderAssignmentFailure()
        Prior = RoutingAssignmentCut.FromFailure(
            Failure,
            SourceCandidateId="Placement-first",
            MandatoryAccessOwnershipFingerprint="access-first",
        )
        AccessDistinct = RoutingAssignmentCut.FromFailure(
            Failure,
            SourceCandidateId="Placement-second",
            MandatoryAccessOwnershipFingerprint="access-second",
        )
        SameAccess = RoutingAssignmentCut.FromFailure(
            Failure,
            SourceCandidateId="Placement-third",
            MandatoryAccessOwnershipFingerprint="access-first",
        )
        DifferentCut = RoutingAssignmentCut.FromFailure(
            self.BuildHigherOrderAssignmentFailure("different-cut"),
            SourceCandidateId="Placement-fourth",
            MandatoryAccessOwnershipFingerprint="access-third",
        )
        self.assertIsNotNone(Prior)
        self.assertIsNotNone(AccessDistinct)
        self.assertIsNotNone(SameAccess)
        self.assertIsNotNone(DifferentCut)

        self.assertTrue(
            ShouldDiversifyRepeatedAssignmentCut((Prior,), AccessDistinct)
        )
        self.assertFalse(
            ShouldDiversifyRepeatedAssignmentCut((Prior,), SameAccess)
        )
        self.assertFalse(
            ShouldDiversifyRepeatedAssignmentCut((Prior,), DifferentCut)
        )

    def testRepeatedAccessDistinctPortfolioCutCommitsBeforeNextSibling(
        self,
    ) -> None:
        Failure = self.BuildHigherOrderAssignmentFailure()
        Prior = RoutingAssignmentCut.FromFailure(
            Failure,
            SourceCandidateId="Placement-first",
            MandatoryAccessOwnershipFingerprint="access-first",
        )
        Repeated = RoutingAssignmentCut.FromFailure(
            Failure,
            SourceCandidateId="Placement-second",
            MandatoryAccessOwnershipFingerprint="access-second",
        )
        SameOwnership = RoutingAssignmentCut.FromFailure(
            Failure,
            SourceCandidateId="Placement-third",
            MandatoryAccessOwnershipFingerprint="access-first",
        )
        assert Prior is not None
        assert Repeated is not None
        assert SameOwnership is not None
        Topology = {
            Signal: Signal
            for Signal in Prior.ConflictSignals
        }
        self.assertFalse(ShouldDeferTopologyCutForMaterializedSibling(
            Requested=True,
            TopologyAccessRepairEligible=True,
            CommittedHistory=(),
            DeferredCuts=(Prior,),
            Current=Repeated,
            SignalTopologyFingerprints=Topology,
        ))
        self.assertTrue(ShouldDeferTopologyCutForMaterializedSibling(
            Requested=True,
            TopologyAccessRepairEligible=True,
            CommittedHistory=(),
            DeferredCuts=(Prior,),
            Current=SameOwnership,
            SignalTopologyFingerprints=Topology,
        ))
        self.assertTrue(ShouldDeferTopologyCutForMaterializedSibling(
            Requested=True,
            TopologyAccessRepairEligible=False,
            CommittedHistory=(),
            DeferredCuts=(Prior,),
            Current=Repeated,
            SignalTopologyFingerprints=Topology,
        ))

    def testOverlappingHigherOrderPortfolioCutsOpenGeometryEpoch(
        self,
    ) -> None:
        First = RoutingAssignmentCut(
            Classification=(
                RoutingAssignmentCutClassification.SaturatedBoundaryCut
            ),
            ConflictGraphJson="{}",
            ConflictFingerprint="first",
            RelocationSignals=("Shared0", "Shared1", "Left"),
            ConflictSignals=("Shared0", "Shared1", "Left"),
            MandatoryAccessOwnershipFingerprint="ownership-first",
        )
        Second = RoutingAssignmentCut(
            Classification=(
                RoutingAssignmentCutClassification.SaturatedBoundaryCut
            ),
            ConflictGraphJson="{}",
            ConflictFingerprint="second",
            RelocationSignals=("Shared0", "Shared1", "Right"),
            ConflictSignals=("Shared0", "Shared1", "Right"),
            MandatoryAccessOwnershipFingerprint="ownership-second",
        )
        Disjoint = replace(
            Second,
            ConflictFingerprint="disjoint",
            RelocationSignals=("Other0", "Other1", "Other2"),
            ConflictSignals=("Other0", "Other1", "Other2"),
        )
        Topology = {
            Signal: Signal
            for Signal in (
                "Shared0",
                "Shared1",
                "Left",
                "Right",
                "Other0",
                "Other1",
                "Other2",
            )
        }
        self.assertFalse(ShouldDeferTopologyCutForMaterializedSibling(
            Requested=True,
            TopologyAccessRepairEligible=True,
            CommittedHistory=(),
            DeferredCuts=(First,),
            Current=Second,
            SignalTopologyFingerprints=Topology,
        ))
        self.assertTrue(ShouldDeferTopologyCutForMaterializedSibling(
            Requested=True,
            TopologyAccessRepairEligible=True,
            CommittedHistory=(),
            DeferredCuts=(First,),
            Current=Disjoint,
            SignalTopologyFingerprints=Topology,
        ))

    def testRepeatedHigherOrderCutRepairsInternalPinBankExactlyOnce(
        self,
    ) -> None:
        Cut = RoutingAssignmentCut(
            Classification=(
                RoutingAssignmentCutClassification.SaturatedBoundaryCut
            ),
            ConflictGraphJson="{}",
            ConflictFingerprint="higher",
            RelocationSignals=("First", "Second", "Third"),
            ConflictSignals=("First", "Second", "Third"),
            MandatoryAccessOwnershipFingerprint="ownership",
        )
        self.assertEqual(
            SelectRepeatedHigherOrderPinBankRepairSignals(
                TopologyAccessRepairEligible=True,
                RepeatedAcrossAccessDistinctPlacements=True,
                CandidatePostPinBankRepairEpoch=False,
                AssignmentCut=Cut,
            ),
            frozenset(("First", "Second", "Third")),
        )
        for Overrides in (
            {"TopologyAccessRepairEligible": False},
            {"RepeatedAcrossAccessDistinctPlacements": False},
            {"CandidatePostPinBankRepairEpoch": True},
        ):
            Arguments = {
                "TopologyAccessRepairEligible": True,
                "RepeatedAcrossAccessDistinctPlacements": True,
                "CandidatePostPinBankRepairEpoch": False,
                "AssignmentCut": Cut,
                **Overrides,
            }
            self.assertEqual(
                SelectRepeatedHigherOrderPinBankRepairSignals(
                    **Arguments
                ),
                frozenset(),
            )
        Renamed = replace(
            Cut,
            RelocationSignals=("One", "Two", "Three"),
            ConflictSignals=("One", "Two", "Three"),
        )
        self.assertEqual(
            len(SelectRepeatedHigherOrderPinBankRepairSignals(
                TopologyAccessRepairEligible=True,
                RepeatedAcrossAccessDistinctPlacements=True,
                CandidatePostPinBankRepairEpoch=False,
                AssignmentCut=Renamed,
            )),
            3,
        )

    def testExhaustiveExactPairRepairsInternalPinBankDirectly(
        self,
    ) -> None:
        def BuildEvidence(
            First: str,
            Second: str,
            *,
            BudgetExhausted: bool = False,
            SolutionCount: int = 0,
            Reverse: bool = False,
        ) -> tuple[RoutingAssignmentCut, RoutingFailure]:
            Pair = (
                (Second, First)
                if Reverse
                else (First, Second)
            )
            Cut = RoutingAssignmentCut(
                Classification=(
                    RoutingAssignmentCutClassification
                    .PairwiseIncompatibility
                ),
                ConflictGraphJson="{}",
                ConflictFingerprint="pair",
                ConflictSignals=tuple(Pair),
                RelocationSignals=tuple(Pair),
                PairwiseConflictEdges=(Pair,),
            )
            Failure = RoutingFailure(
                Reason=RoutingFailureReason.BoundaryEscapeInfeasible,
                Stage="ClusterBoundaryLease",
                AffectedNets=tuple(Pair),
                Diagnostics={
                    "ClusterInterfacePatternSearch": {
                        "Applied": True,
                        "CoreShrinkComplete": True,
                        "UnavoidablePairEdges": [list(Pair)],
                        "CutLocalJointSearches": [{
                            "CutSignals": list(reversed(Pair))
                            if Reverse
                            else list(Pair),
                            "CutEdges": [list(Pair)],
                            "BudgetExhausted": BudgetExhausted,
                            "SearchVariantCount": 16,
                            "ExpansionCount": 109,
                            "SolutionCount": SolutionCount,
                            "FailedStateCount": 26,
                        }],
                    },
                },
            )
            return Cut, Failure

        Cut, Failure = BuildEvidence("Left", "Right")
        self.assertEqual(
            SelectExhaustiveExactPairPinBankRepairSignals(
                TopologyRequiresJointPortfolio=True,
                CandidatePostPinBankRepairEpoch=False,
                AssignmentCut=Cut,
                Failure=Failure,
            ),
            frozenset(("Left", "Right")),
        )
        RenamedCut, RenamedFailure = BuildEvidence(
            "RenamedSecond",
            "RenamedFirst",
            Reverse=True,
        )
        self.assertEqual(
            len(SelectExhaustiveExactPairPinBankRepairSignals(
                TopologyRequiresJointPortfolio=True,
                CandidatePostPinBankRepairEpoch=False,
                AssignmentCut=RenamedCut,
                Failure=RenamedFailure,
            )),
            2,
        )
        for Overrides in (
            {"TopologyRequiresJointPortfolio": False},
            {"CandidatePostPinBankRepairEpoch": True},
        ):
            Arguments = {
                "TopologyRequiresJointPortfolio": True,
                "CandidatePostPinBankRepairEpoch": False,
                "AssignmentCut": Cut,
                "Failure": Failure,
                **Overrides,
            }
            self.assertFalse(
                SelectExhaustiveExactPairPinBankRepairSignals(
                    **Arguments
                )
            )
        for BudgetExhausted, SolutionCount in ((True, 0), (False, 1)):
            IncompleteCut, IncompleteFailure = BuildEvidence(
                "Left",
                "Right",
                BudgetExhausted=BudgetExhausted,
                SolutionCount=SolutionCount,
            )
            self.assertFalse(
                SelectExhaustiveExactPairPinBankRepairSignals(
                    TopologyRequiresJointPortfolio=True,
                    CandidatePostPinBankRepairEpoch=False,
                    AssignmentCut=IncompleteCut,
                    Failure=IncompleteFailure,
                )
            )

    def testPinBankRepairRejectsOnlyUnchangedRequiredOwnership(self) -> None:
        self.assertFalse(PinBankRepairOwnershipIsDistinct(
            "ownership-before",
            "ownership-before",
        ))
        self.assertTrue(PinBankRepairOwnershipIsDistinct(
            "ownership-before",
            "ownership-after",
        ))
        self.assertTrue(PinBankRepairOwnershipIsDistinct(
            "",
            "ordinary-placement",
        ))

    def testExactPairRefinementDiversifiesOnlyAcrossDistinctAccess(
        self,
    ) -> None:
        Prior = RoutingAssignmentCut.FromFailure(
            self.BuildHigherOrderAssignmentFailure(),
            SourceCandidateId="Placement-prior",
            MandatoryAccessOwnershipFingerprint="access-prior",
        )
        PairFailure = RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="InitialCandidateAssignment",
            AffectedNets=("B1", "Generate0"),
            Diagnostics={
                "ConflictGraph": {
                    "Classification": "mandatory-boundary-capacity-cut",
                    "ConflictSignals": ["B1", "Generate0"],
                    "RelocationSignals": ["B1", "Generate0"],
                    "PriorityRelocationSignals": ["B1", "Generate0"],
                },
            },
        )
        AccessDistinct = RoutingAssignmentCut.FromFailure(
            PairFailure,
            SourceCandidateId="Placement-current",
            MandatoryAccessOwnershipFingerprint="access-current",
        )
        SameAccess = RoutingAssignmentCut.FromFailure(
            PairFailure,
            SourceCandidateId="Placement-same",
            MandatoryAccessOwnershipFingerprint="access-prior",
        )
        OutsidePrior = RoutingAssignmentCut.FromFailure(
            replace(
                PairFailure,
                AffectedNets=("B1", "Outside"),
                Diagnostics={
                    "ConflictGraph": {
                        "Classification": (
                            "mandatory-boundary-capacity-cut"
                        ),
                        "ConflictSignals": ["B1", "Outside"],
                    },
                },
            ),
            SourceCandidateId="Placement-outside",
            MandatoryAccessOwnershipFingerprint="access-outside",
        )
        self.assertIsNotNone(Prior)
        self.assertIsNotNone(AccessDistinct)
        self.assertIsNotNone(SameAccess)
        self.assertIsNotNone(OutsidePrior)

        self.assertEqual(
            SelectRefinedAssignmentCutDiversificationSignals(
                (Prior,),
                AccessDistinct,
            ),
            frozenset({"B1", "Generate0"}),
        )
        self.assertEqual(
            SelectRefinedAssignmentCutDiversificationSignals(
                (Prior,),
                SameAccess,
            ),
            frozenset(),
        )
        self.assertEqual(
            SelectRefinedAssignmentCutDiversificationSignals(
                (Prior,),
                OutsidePrior,
            ),
            frozenset(),
        )

    def testRepeatedPairSubcutDiversifiesOnlyDistinctAccessEndpoints(
        self,
    ) -> None:
        def Cut(
            Edges: tuple[tuple[str, str], ...],
            Ownership: str,
        ) -> RoutingAssignmentCut:
            Signals = tuple(sorted({
                Signal for Edge in Edges for Signal in Edge
            }))
            Result = RoutingAssignmentCut.FromFailure(
                RoutingFailure(
                    Reason=RoutingFailureReason.TrackAssignmentConflict,
                    Stage="InitialCandidateAssignment",
                    AffectedNets=Signals,
                    Diagnostics={
                        "ConflictGraph": {
                            "Classification": (
                                "mandatory-boundary-capacity-cut"
                            ),
                            "ConflictSignals": list(Signals),
                            "RelocationSignals": list(Signals),
                            "PriorityRelocationSignals": list(Signals),
                            "PairwiseIncompatibleEdges": [
                                list(Edge) for Edge in reversed(Edges)
                            ],
                        },
                    },
                ),
                MandatoryAccessOwnershipFingerprint=Ownership,
            )
            self.assertIsNotNone(Result)
            return Result

        Prior = Cut(
            (("RepeatedLeft", "RepeatedRight"), ("Old0", "Old1")),
            "access-before",
        )
        Current = Cut(
            (("New0", "New1"), ("RepeatedRight", "RepeatedLeft")),
            "access-after",
        )
        SameAccess = Cut(
            (("RepeatedLeft", "RepeatedRight"),),
            "access-before",
        )

        self.assertEqual(
            SelectRepeatedAssignmentSubcutDiversificationSignals(
                (Prior,),
                Current,
            ),
            frozenset(("RepeatedLeft", "RepeatedRight")),
        )
        self.assertFalse(
            SelectRepeatedAssignmentSubcutDiversificationSignals(
                (Prior,),
                SameAccess,
            )
        )
        self.assertEqual(
            SelectRepeatedAssignmentSubcutDiversificationSignals(
                (Prior,),
                Current,
            ),
            SelectRepeatedAssignmentSubcutDiversificationSignals(
                (Prior,),
                Cut(
                    (
                        ("RepeatedLeft", "RepeatedRight"),
                        ("RenamedNew0", "RenamedNew1"),
                    ),
                    "access-renamed",
                ),
            ),
        )

    def testTopologyDiversificationKeepsRepeatedSubcutScopedAndGated(
        self,
    ) -> None:
        CompleteSignals = ("Left", "Right", "Other")
        RepeatedSubcutSignals = ("Right", "Left")
        self.assertEqual(
            SelectTopologyCoordinatedCandidateDiversificationSignals(
                TopologyRequiresJointPortfolio=False,
                RepeatedExactCut=True,
                CompleteCutSignals=CompleteSignals,
                RepeatedSubcutSignals=RepeatedSubcutSignals,
            ),
            frozenset(),
        )
        self.assertEqual(
            SelectTopologyCoordinatedCandidateDiversificationSignals(
                TopologyRequiresJointPortfolio=True,
                RepeatedExactCut=False,
                CompleteCutSignals=CompleteSignals,
                RepeatedSubcutSignals=RepeatedSubcutSignals,
            ),
            frozenset(("Left", "Right")),
        )
        self.assertEqual(
            SelectTopologyCoordinatedCandidateDiversificationSignals(
                TopologyRequiresJointPortfolio=True,
                RepeatedExactCut=True,
                CompleteCutSignals=CompleteSignals,
                RepeatedSubcutSignals=RepeatedSubcutSignals,
            ),
            frozenset(CompleteSignals),
        )

    def testRepeatedCandidateStarvationDiversifiesWithinOneCutEpoch(
        self,
    ) -> None:
        def BuildStarvationCut(
            Signals: tuple[str, ...],
            OwnershipFingerprint: str,
        ) -> RoutingAssignmentCut:
            Failure = RoutingFailure(
                Reason=RoutingFailureReason.TrackAssignmentConflict,
                Stage="Candidate",
                AffectedNets=Signals,
                Diagnostics={
                    "ConflictGraph": {
                        "Classification": (
                            "candidate-starvation-placement-conflict"
                        ),
                        "ConflictSignals": list(reversed(Signals)),
                        "NoCandidateSignals": list(Signals),
                        "RelocationSignals": list(Signals),
                        "PriorityRelocationSignals": list(Signals),
                    },
                },
            )
            Result = RoutingAssignmentCut.FromFailure(
                Failure,
                MandatoryAccessOwnershipFingerprint=(
                    OwnershipFingerprint
                ),
            )
            self.assertIsNotNone(Result)
            return Result

        Prior = BuildStarvationCut(
            ("OneOff", "Repeated"),
            "access-first",
        )
        Current = BuildStarvationCut(
            ("Repeated", "CurrentOnly"),
            "access-second",
        )
        History = (
            CandidateStarvationPlacementEvidence(
                AssignmentCutFingerprint="active-cut",
                AssignmentConstraintFingerprint="active-constraints",
                AssignmentCut=Prior,
            ),
        )

        self.assertEqual(
            SelectRepeatedCandidateStarvationDiversificationSignals(
                History,
                Current,
                AssignmentCutFingerprint="active-cut",
                AssignmentConstraintFingerprint="active-constraints",
            ),
            frozenset({"Repeated"}),
        )
        for CutFingerprint, ConstraintFingerprint in (
            ("different-cut", "active-constraints"),
            ("active-cut", "different-constraints"),
        ):
            with self.subTest(
                CutFingerprint=CutFingerprint,
                ConstraintFingerprint=ConstraintFingerprint,
            ):
                self.assertFalse(
                    SelectRepeatedCandidateStarvationDiversificationSignals(
                        History,
                        Current,
                        AssignmentCutFingerprint=CutFingerprint,
                        AssignmentConstraintFingerprint=(
                            ConstraintFingerprint
                        ),
                    )
                )
        SameAccess = BuildStarvationCut(
            ("Repeated",),
            "access-first",
        )
        self.assertFalse(
            SelectRepeatedCandidateStarvationDiversificationSignals(
                History,
                SameAccess,
                AssignmentCutFingerprint="active-cut",
                AssignmentConstraintFingerprint="active-constraints",
            )
        )

    def testDeferredCandidateStarvationEvidenceRetainsParentEpoch(
        self,
    ) -> None:
        Cut = RoutingAssignmentCut.FromFailure(
            RoutingFailure(
                Reason=RoutingFailureReason.TrackAssignmentConflict,
                Stage="Candidate",
                AffectedNets=("Boundary",),
                Diagnostics={
                    "ConflictGraph": {
                        "Classification": (
                            "candidate-starvation-placement-conflict"
                        ),
                        "NoCandidateSignals": ["Boundary"],
                    },
                },
            ),
            MandatoryAccessOwnershipFingerprint="access-sibling",
        )
        self.assertIsNotNone(Cut)
        Evidence = BuildCandidateStarvationPlacementEvidence(
            Cut,
            AssignmentCutFingerprint="parent-cut",
            AssignmentConstraintFingerprint="parent-constraints",
        )
        self.assertIsNotNone(Evidence)
        self.assertEqual(
            Evidence.AssignmentCutFingerprint,
            "parent-cut",
        )
        self.assertEqual(
            Evidence.AssignmentConstraintFingerprint,
            "parent-constraints",
        )
        self.assertIsNone(BuildCandidateStarvationPlacementEvidence(
            Cut,
            AssignmentCutFingerprint="",
            AssignmentConstraintFingerprint="parent-constraints",
        ))

    def testRepeatedSiblingStarvationAdmitsDisjointTransactionalChild(
        self,
    ) -> None:
        Child = RoutingAssignmentCut.FromFailure(
            RoutingFailure(
                Reason=RoutingFailureReason.TrackAssignmentConflict,
                Stage="Candidate",
                AffectedNets=("SharedBoundary", "CurrentOnly"),
                Diagnostics={
                    "ConflictGraph": {
                        "Classification": (
                            "candidate-starvation-placement-conflict"
                        ),
                        "NoCandidateSignals": [
                            "CurrentOnly",
                            "SharedBoundary",
                        ],
                    },
                },
            )
        )
        self.assertIsNotNone(Child)
        self.assertEqual(
            SelectTransactionalEndpointRepairSignals(
                Child,
                InternalPinBankGeometryRepairActive=False,
                PinBankRepairSignals=frozenset(),
                CandidateIsTransactionalEndpointRepair=True,
                ParentTransactionalRepairSignals=frozenset({
                    "ParentA",
                    "ParentB",
                }),
                ProvenSiblingStarvationSignals=frozenset({
                    "SharedBoundary",
                }),
            ),
            frozenset({"SharedBoundary"}),
        )
        self.assertFalse(SelectTransactionalEndpointRepairSignals(
            Child,
            InternalPinBankGeometryRepairActive=False,
            PinBankRepairSignals=frozenset(),
            CandidateIsTransactionalEndpointRepair=True,
            ParentTransactionalRepairSignals=frozenset({
                "ParentA",
                "ParentB",
            }),
            ProvenSiblingStarvationSignals=frozenset({
                "Unrelated",
            }),
        ))

    def testRepeatedCandidateStarvationSelectionIsRenameInvariant(
        self,
    ) -> None:
        def SelectWithNames(
            RepeatedName: str,
            PriorOnlyName: str,
            CurrentOnlyName: str,
        ) -> frozenset[str]:
            def Cut(
                Signals: tuple[str, ...],
                Ownership: str,
            ) -> RoutingAssignmentCut:
                Result = RoutingAssignmentCut.FromFailure(
                    RoutingFailure(
                        Reason=(
                            RoutingFailureReason.TrackAssignmentConflict
                        ),
                        Stage="Candidate",
                        AffectedNets=tuple(reversed(Signals)),
                        Diagnostics={
                            "ConflictGraph": {
                                "Classification": (
                                    "candidate-starvation-placement-conflict"
                                ),
                                "NoCandidateSignals": list(Signals),
                            },
                        },
                    ),
                    MandatoryAccessOwnershipFingerprint=Ownership,
                )
                self.assertIsNotNone(Result)
                return Result

            Prior = Cut(
                (PriorOnlyName, RepeatedName),
                "first-access",
            )
            Current = Cut(
                (CurrentOnlyName, RepeatedName),
                "second-access",
            )
            return SelectRepeatedCandidateStarvationDiversificationSignals(
                (
                    CandidateStarvationPlacementEvidence(
                        AssignmentCutFingerprint="cut",
                        AssignmentConstraintFingerprint="constraints",
                        AssignmentCut=Prior,
                    ),
                ),
                Current,
                AssignmentCutFingerprint="cut",
                AssignmentConstraintFingerprint="constraints",
            )

        self.assertEqual(
            SelectWithNames("Shared", "Old", "New"),
            frozenset({"Shared"}),
        )
        self.assertEqual(
            SelectWithNames("Renamed", "Before", "After"),
            frozenset({"Renamed"}),
        )

    def testRoutingControlProfileRebindsOnlyNamedSignals(self) -> None:
        Placement = PcbPlacement(
            Placed=SimpleNamespace(
                LocalRouteDiagnostics={
                    "__PlacementRelocation__": {
                        "Signals": ["GeometrySignal"],
                        "CoordinatedCandidateDiversificationSignals": [],
                        "CoordinatedCandidateDiversityLevel": 0,
                    },
                },
            ),
            Clusters=(),
            SignalOrder=(),
            LayerCount=2,
        )

        Changed, Fingerprint = (
            ApplyCoordinatedCandidateDiversificationProfile(
                Placement,
                frozenset({"Repeated", "Other"}),
            )
        )
        self.assertTrue(Changed)
        self.assertTrue(Fingerprint)
        Diagnostics = Placement.Placed.LocalRouteDiagnostics[
            "__PlacementRelocation__"
        ]
        self.assertEqual(
            Diagnostics["CoordinatedCandidateDiversificationSignals"],
            ["Other", "Repeated"],
        )
        self.assertEqual(
            Diagnostics["CoordinatedCandidateDiversityLevel"],
            1,
        )
        self.assertEqual(
            Diagnostics["CoordinatedCandidateDiversificationFixedLevel"],
            1,
        )
        self.assertEqual(Diagnostics["Signals"], ["GeometrySignal"])
        self.assertEqual(
            ApplyCoordinatedCandidateDiversificationProfile(
                Placement,
                frozenset({"Other", "Repeated"}),
            ),
            (False, Fingerprint),
        )

    def testPairedLeasePinBankRepairIsExplicitAndCutScoped(self) -> None:
        Placement = PcbPlacement(
            Placed=SimpleNamespace(
                LocalRouteDiagnostics={
                    "__PlacementRelocation__": {
                        "Signals": ["GeometrySignal"],
                        "CoordinatedCandidateDiversificationSignals": [],
                        "CoordinatedCandidateDiversityLevel": 0,
                    },
                },
            ),
            Clusters=(),
            SignalOrder=(),
            LayerCount=2,
        )
        Signals = frozenset({"PairLeft", "PairRight"})
        Changed, _Fingerprint = (
            ApplyCoordinatedCandidateDiversificationProfile(
                Placement,
                Signals,
                EnableClusterPinBankRepair=True,
            )
        )
        self.assertTrue(Changed)
        Repair = Placement.Placed.LocalRouteDiagnostics[
            "__ClusterPinBankRepair__"
        ]
        self.assertEqual(Repair["Signals"], ["PairLeft", "PairRight"])
        self.assertEqual(Repair["CandidateDomainOffset"], 1)
        self.assertEqual(Repair["VariantCount"], 3)

        # The generic profile remains unchanged unless this exact repair is
        # explicitly authorized by the repeated paired-cut proof.
        Ordinary = PcbPlacement(
            Placed=SimpleNamespace(
                LocalRouteDiagnostics={
                    "__PlacementRelocation__": {
                        "Signals": ["GeometrySignal"],
                        "CoordinatedCandidateDiversificationSignals": [],
                        "CoordinatedCandidateDiversityLevel": 0,
                    },
                },
            ),
            Clusters=(),
            SignalOrder=(),
            LayerCount=2,
        )
        ApplyCoordinatedCandidateDiversificationProfile(Ordinary, Signals)
        self.assertNotIn(
            "__ClusterPinBankRepair__",
            Ordinary.Placed.LocalRouteDiagnostics,
        )

    def testRepeaterReadyPortalRepairHasDistinctOneShotIdentity(
        self,
    ) -> None:
        Placement = PcbPlacement(
            Placed=SimpleNamespace(
                LocalRouteDiagnostics={
                    "__PlacementRelocation__": {
                        "Signals": ["GeometrySignal"],
                        "CoordinatedCandidateDiversificationSignals": [],
                        "CoordinatedCandidateDiversityLevel": 0,
                    },
                },
            ),
            Clusters=(),
            SignalOrder=(),
            LayerCount=2,
        )
        Evidence = AccessDistinctAssignmentCutDiversificationEvidence(
            ExhaustedRepeaterAccessCut=True,
        )
        State = BuildSamePlacementRoutingControlRetryState(
            PlacementFingerprint="placement",
            AssignmentCutFingerprint="cut",
            Signals=("PowerCut",),
            Evidence=Evidence,
        )
        assert State is not None
        Changed, Fingerprint = (
            ApplyCoordinatedCandidateDiversificationProfile(
                Placement,
                frozenset(("PowerCut",)),
                EnableRepeaterReadyPortalRepair=True,
            )
        )

        self.assertTrue(Changed)
        self.assertEqual(
            Fingerprint,
            State.AttemptIdentity.RoutingControlProfileFingerprint,
        )
        self.assertNotEqual(Fingerprint, State.Profile.Fingerprint)
        Repair = Placement.Placed.LocalRouteDiagnostics[
            "__RepeaterReadyPortalRepair__"
        ]
        self.assertEqual(Repair["Signals"], ["PowerCut"])
        self.assertEqual(Repair["ExtensionLength"], 3)
        self.assertEqual(Repair["MaximumExtensionsPerPortal"], 2)
        self.assertEqual(
            ApplyCoordinatedCandidateDiversificationProfile(
                Placement,
                frozenset(("PowerCut",)),
                EnableRepeaterReadyPortalRepair=True,
            ),
            (False, Fingerprint),
        )

    def testSamePlacementRoutingControlRetryIdentityIsProfileScoped(
        self,
    ) -> None:
        Evidence = AccessDistinctAssignmentCutDiversificationEvidence(
            RepeatedExactCut=True,
        )
        State = BuildSamePlacementRoutingControlRetryState(
            PlacementFingerprint="placement",
            AssignmentCutFingerprint="assignment-cut",
            Signals=("Right", "Left", "Right"),
            Evidence=Evidence,
        )
        ReorderedState = BuildSamePlacementRoutingControlRetryState(
            PlacementFingerprint="placement",
            AssignmentCutFingerprint="assignment-cut",
            Signals=("Left", "Right"),
            Evidence=Evidence,
        )
        self.assertIsNotNone(State)
        self.assertEqual(State, ReorderedState)
        assert State is not None
        self.assertEqual(State.Profile.Signals, ("Left", "Right"))
        self.assertEqual(
            State.AttemptIdentity,
            RoutingControlAttemptIdentity(
                PlacementFingerprint="placement",
                RoutingControlProfileFingerprint=(
                    State.Profile.Fingerprint
                ),
            ),
        )

        PreviousProfile = (
            BuildCoordinatedCandidateDiversificationProfile(("Old",))
        )
        Attempted = {
            RoutingControlAttemptIdentity(
                PlacementFingerprint="placement",
                RoutingControlProfileFingerprint=(
                    PreviousProfile.Fingerprint
                ),
            ),
        }
        self.assertTrue(ShouldRetrySamePlacementRoutingControl(
            State,
            "placement",
            Attempted,
        ))
        self.assertFalse(ShouldRetrySamePlacementRoutingControl(
            State,
            "different-placement",
            Attempted,
        ))
        Attempted.add(State.AttemptIdentity)
        self.assertFalse(ShouldRetrySamePlacementRoutingControl(
            State,
            "placement",
            Attempted,
        ))
        self.assertTrue(ShouldDeferSamePlacementRoutingControlRetry(
            State,
            HasRemainingActivePortfolioSibling=True,
        ))
        self.assertFalse(ShouldDeferSamePlacementRoutingControlRetry(
            State,
            HasRemainingActivePortfolioSibling=False,
        ))
        self.assertFalse(ShouldDeferSamePlacementRoutingControlRetry(
            None,
            HasRemainingActivePortfolioSibling=True,
        ))
        self.assertIsNone(BuildSamePlacementRoutingControlRetryState(
            PlacementFingerprint="placement",
            AssignmentCutFingerprint="assignment-cut",
            Signals=(),
            Evidence=Evidence,
        ))
        self.assertIsNone(BuildSamePlacementRoutingControlRetryState(
            PlacementFingerprint="placement",
            AssignmentCutFingerprint="assignment-cut",
            Signals=("Left", "Right"),
            Evidence=(
                AccessDistinctAssignmentCutDiversificationEvidence()
            ),
        ))
        RepeatedSubcutState = BuildSamePlacementRoutingControlRetryState(
            PlacementFingerprint="placement",
            AssignmentCutFingerprint="assignment-cut",
            Signals=("Left", "Right"),
            Evidence=(
                AccessDistinctAssignmentCutDiversificationEvidence(
                    RefinedExactCut=True,
                    RepeatedExactSubcut=True,
                )
            ),
        )
        self.assertIsNotNone(RepeatedSubcutState)

    def testActiveAssignmentConstraintsRebindWithoutChangingRecipe(
        self,
    ) -> None:
        Placement = PcbPlacement(
            Placed=SimpleNamespace(
                LocalRouteDiagnostics={
                    "__JointClusterPlacement__": {
                        "AssignmentConstraints": {
                            "HigherOrderSignalSets": [
                                ["Original", "Cut"]
                            ],
                            "PairwiseConflictEdges": [],
                        },
                    },
                    "__PlacementRecipe__": {
                        "AssignmentConstraintFingerprint": "generated",
                    },
                },
            ),
            Clusters=(),
            SignalOrder=(),
            LayerCount=2,
        )
        Constraints = PlacementAssignmentConstraintSet(
            HigherOrderSignalSets=(("Original", "Cut"),),
            PairwiseConflictEdges=(("PairA", "PairB"),),
        )

        Changed, Fingerprint = (
            ApplyActivePlacementAssignmentConstraints(
                Placement,
                Constraints,
            )
        )

        self.assertTrue(Changed)
        self.assertEqual(Fingerprint, Constraints.Fingerprint)
        Diagnostics = Placement.Placed.LocalRouteDiagnostics
        self.assertEqual(
            Diagnostics["__JointClusterPlacement__"][
                "ActiveAssignmentConstraints"
            ],
            Constraints.ToDictionary(),
        )
        self.assertEqual(
            Diagnostics["__PlacementRecipe__"][
                "AssignmentConstraintFingerprint"
            ],
            "generated",
        )
        self.assertEqual(
            ApplyActivePlacementAssignmentConstraints(
                Placement,
                Constraints,
            ),
            (False, Constraints.Fingerprint),
        )
        self.assertTrue(ApplyRemainingExactLegalJointStateCount(
            Placement,
            3,
        ))
        self.assertEqual(
            Placement.Placed.LocalRouteDiagnostics[
                "__JointClusterPlacement__"
            ]["RemainingExactLegalRetainedStateCount"],
            3,
        )
        self.assertFalse(ApplyRemainingExactLegalJointStateCount(
            Placement,
            3,
        ))
        with self.assertRaises(ValueError):
            ApplyRemainingExactLegalJointStateCount(Placement, 0)

    def testExactPairRefinementIsRenameAndHistoryOrderInvariant(
        self,
    ) -> None:
        def Cut(
            Classification: str,
            Signals: tuple[str, ...],
            Access: str,
        ) -> RoutingAssignmentCut:
            Failure = RoutingFailure(
                Reason=RoutingFailureReason.TrackAssignmentConflict,
                Stage="TrackAssignment",
                AffectedNets=Signals,
                Diagnostics={
                    "ConflictGraph": {
                        "Classification": Classification,
                        "ConflictSignals": list(Signals),
                        "RelocationSignals": list(Signals),
                    },
                },
            )
            Result = RoutingAssignmentCut.FromFailure(
                Failure,
                SourceCandidateId=f"Placement-{Access}",
                MandatoryAccessOwnershipFingerprint=Access,
            )
            self.assertIsNotNone(Result)
            return Result

        OriginalPrior = Cut(
            "higher-order-placement-conflict",
            ("A0", "A1", "Generate0", "Propagate1"),
            "original-prior",
        )
        OriginalUnrelated = Cut(
            "higher-order-placement-conflict",
            ("Other0", "Other1", "Other2"),
            "original-unrelated",
        )
        OriginalCurrent = Cut(
            "mandatory-boundary-capacity-cut",
            ("A1", "Generate0"),
            "original-current",
        )
        RenamedPrior = Cut(
            "higher-order-placement-conflict",
            ("Input0", "Input1", "CarryGenerate", "CarryPropagate"),
            "renamed-prior",
        )
        RenamedUnrelated = Cut(
            "higher-order-placement-conflict",
            ("Else0", "Else1", "Else2"),
            "renamed-unrelated",
        )
        RenamedCurrent = Cut(
            "mandatory-boundary-capacity-cut",
            ("Input1", "CarryGenerate"),
            "renamed-current",
        )

        self.assertEqual(
            SelectRefinedAssignmentCutDiversificationSignals(
                (OriginalUnrelated, OriginalPrior),
                OriginalCurrent,
            ),
            SelectRefinedAssignmentCutDiversificationSignals(
                (OriginalPrior, OriginalUnrelated),
                OriginalCurrent,
            ),
        )
        self.assertEqual(
            SelectRefinedAssignmentCutDiversificationSignals(
                (OriginalPrior, OriginalUnrelated),
                OriginalCurrent,
            ),
            frozenset({"A1", "Generate0"}),
        )
        self.assertEqual(
            SelectRefinedAssignmentCutDiversificationSignals(
                (RenamedUnrelated, RenamedPrior),
                RenamedCurrent,
            ),
            frozenset({"Input1", "CarryGenerate"}),
        )

    def testUnrelatedStarvationPreservesCurrentStructuredCut(
        self,
    ) -> None:
        Current = RoutingAssignmentCut.FromFailure(
            self.BuildHigherOrderAssignmentFailure(),
            SourceCandidateId="Placement-current",
            MandatoryAccessOwnershipFingerprint="access-current",
        )
        Constraints = PlacementAssignmentConstraintSet().WithCut(Current)
        Starvation = RoutingAssignmentCut.FromFailure(
            RoutingFailure(
                Reason=RoutingFailureReason.TrackAssignmentConflict,
                Stage="Candidate",
                AffectedNets=("Unrelated",),
                Diagnostics={
                    "Action": "advance-placement-candidate-starvation",
                    "ConflictGraph": {
                        "Classification": (
                            "candidate-starvation-placement-conflict"
                        ),
                        "ConflictSignals": ["Unrelated"],
                        "RelocationSignals": ["Unrelated"],
                        "PriorityRelocationSignals": ["Unrelated"],
                        "NoCandidateSignals": ["Unrelated"],
                    },
                },
            ),
            SourceCandidateId="Placement-starved",
            MandatoryAccessOwnershipFingerprint="access-starved",
        )
        self.assertIsNotNone(Current)
        self.assertIsNotNone(Starvation)

        self.assertTrue(
            ShouldPreserveCurrentStructuredAssignmentCut(
                Current,
                Constraints,
                Starvation,
            )
        )
        self.assertEqual(Constraints.WithCut(Starvation), Constraints)

    def testConstraintPromotingPairReplacesCurrentStructuredCut(
        self,
    ) -> None:
        Current = RoutingAssignmentCut.FromFailure(
            self.BuildHigherOrderAssignmentFailure(),
            SourceCandidateId="Placement-current",
            MandatoryAccessOwnershipFingerprint="access-current",
        )
        Constraints = PlacementAssignmentConstraintSet().WithCut(Current)
        Pair = RoutingAssignmentCut.FromFailure(
            RoutingFailure(
                Reason=RoutingFailureReason.TrackAssignmentConflict,
                Stage="InitialCandidateAssignment",
                AffectedNets=("First", "Second"),
                Diagnostics={
                    "ConflictGraph": {
                        "Classification": (
                            "mandatory-boundary-capacity-cut"
                        ),
                        "ConflictSignals": ["First", "Second"],
                        "RelocationSignals": ["First", "Second"],
                        "PriorityRelocationSignals": ["First", "Second"],
                    },
                },
            ),
            SourceCandidateId="Placement-pair",
            MandatoryAccessOwnershipFingerprint="access-pair",
        )
        self.assertIsNotNone(Current)
        self.assertIsNotNone(Pair)

        self.assertFalse(
            ShouldPreserveCurrentStructuredAssignmentCut(
                Current,
                Constraints,
                Pair,
            )
        )
        self.assertNotEqual(Constraints.WithCut(Pair), Constraints)

    def testStructuredRelocationCoverIncludesCutAndAllConstraints(
        self,
    ) -> None:
        Current = RoutingAssignmentCut.FromFailure(
            RoutingFailure(
                Reason=RoutingFailureReason.TrackAssignmentConflict,
                Stage="InitialCandidateAssignment",
                AffectedNets=("PairLeft", "PairRight"),
                Diagnostics={
                    "ConflictGraph": {
                        "Classification": (
                            "mandatory-boundary-capacity-cut"
                        ),
                        "ConflictSignals": ["PairLeft", "PairRight"],
                        "RelocationSignals": ["PairLeft", "PairRight"],
                        "PriorityRelocationSignals": [
                            "PairLeft",
                            "PairRight",
                        ],
                        "PairwiseIncompatibleEdges": [
                            ["PairRight", "PairLeft"],
                        ],
                    },
                },
            ),
        )
        Constraints = PlacementAssignmentConstraintSet(
            PairwiseConflictEdges=(("EdgeRight", "EdgeLeft"),),
            HigherOrderSignalSets=(
                ("High2", "High0", "High1"),
            ),
        )
        self.assertIsNotNone(Current)

        self.assertEqual(
            BuildStructuredPlacementRelocationSignals(
                Current,
                Constraints,
            ),
            frozenset({
                "PairLeft",
                "PairRight",
                "EdgeLeft",
                "EdgeRight",
                "High0",
                "High1",
                "High2",
            }),
        )
        self.assertEqual(
            BuildStructuredPlacementRelocationSignals(
                Current,
                PlacementAssignmentConstraintSet(
                    PairwiseConflictEdges=(("EdgeLeft", "EdgeRight"),),
                    HigherOrderSignalSets=(
                        ("High1", "High2", "High0"),
                    ),
                ),
            ),
            BuildStructuredPlacementRelocationSignals(
                Current,
                Constraints,
            ),
        )

    def testAllRejectedPlacementsPreserveTypedBoundaryFailure(self) -> None:
        BoundaryFailure = RoutingFailure(
            Reason=RoutingFailureReason.NoBoundaryEscape,
            Stage="PlacementBoundaryFeasibility",
            AffectedNets=("Blocked",),
            Detail="no legal terminal escape",
        )
        Policy = replace(
            LocalFirstPhysicalDesignPolicy,
            Placement=replace(
                LocalFirstPhysicalDesignPolicy.Placement,
                RoutingFeedbackIterations=0,
            ),
        )
        Netlist = SimpleNamespace(
            Top="Top",
            Modules={"Top": SimpleNamespace(Gates=[object()])},
        )

        with (
            patch("PhysicalDesign.Orchestration.Runner.ValidateNandOnlyDesign"),
            patch(
                "PhysicalDesign.Orchestration.Runner.PlacePcbGraph",
                side_effect=RoutingStageError(BoundaryFailure),
            ),
        ):
            with self.assertRaises(RoutingStageError) as Context:
                _PlaceAndRoutePcbWithPolicy(
                    Netlist,
                    ProgressCallback=None,
                    Policy=Policy,
                    Technology=DefaultRedstoneRoutingTechnology,
                    RequestedStrategy=RoutingStrategy.Default,
                    UsedStrategy=RoutingStrategy.Default,
                )

        self.assertEqual(
            Context.exception.Failure.Reason,
            RoutingFailureReason.NoBoundaryEscape,
        )
        self.assertEqual(Context.exception.Failure.AffectedNets, ("Blocked",))
        self.assertTrue(
            Context.exception.Failure.Diagnostics[
                "PlacementGenerationFailures"
            ]
        )
    def testFailureClassesChooseOnlyMeaningfulEscalations(self) -> None:
        State = RoutingEscalationState(
            PortalMode="reserved",
            ReservationVariant=0,
            LaneDiversityLevel=0,
            CandidateDiversityLevel=0,
            EffectiveRoutingLayers=2,
            AssignmentBudget=100,
        )

        def Decide(Classification: str, BudgetExhausted: bool = False, **Changes):
            return ChooseRoutingEscalationAction(
                Classification=Classification,
                BudgetExhausted=BudgetExhausted,
                State=replace(State, **Changes),
                MaximumAssignmentBudget=200,
                MaximumReservationVariants=2,
                MaximumLaneDiversityLevels=2,
                MaximumCandidateDiversityLevels=2,
                MaximumEffectiveRoutingLayers=3,
            ).Action

        self.assertEqual(
            Decide("work-budget-exhaustion", BudgetExhausted=True),
            "GrowAssignmentBudget",
        )
        self.assertEqual(
            Decide("higher-order-placement-conflict"),
            "AdvancePlacement",
        )
        self.assertEqual(
            Decide("stacked-placement-conflict"),
            "AdvancePlacement",
        )
        self.assertEqual(
            Decide("mandatory-boundary-capacity-cut"),
            "AdvancePlacement",
        )
        self.assertEqual(
            Decide("portal-coverage-pair-conflict"),
            "RegenerateAffectedCandidates",
        )
        self.assertEqual(
            Decide("relocated-higher-order-conflict"),
            "AddRoutingLayer",
        )
        self.assertEqual(
            ChooseRoutingEscalationAction(
                Classification="relocated-higher-order-conflict",
                BudgetExhausted=False,
                State=replace(State, EffectiveRoutingLayers=4),
                MaximumAssignmentBudget=200,
                MaximumReservationVariants=2,
                MaximumLaneDiversityLevels=2,
                MaximumCandidateDiversityLevels=2,
                MaximumEffectiveRoutingLayers=8,
            ).Action,
            "AddRoutingLayer",
        )
        self.assertEqual(
            ChooseRoutingEscalationAction(
                Classification="relocated-higher-order-conflict",
                BudgetExhausted=False,
                State=replace(
                    State,
                    CandidateDiversityLevel=1,
                    EffectiveRoutingLayers=8,
                ),
                MaximumAssignmentBudget=200,
                MaximumReservationVariants=2,
                MaximumLaneDiversityLevels=2,
                MaximumCandidateDiversityLevels=3,
                MaximumEffectiveRoutingLayers=8,
            ).Action,
            "RegenerateAffectedCandidates",
        )
        self.assertEqual(
            ChooseRoutingEscalationAction(
                Classification="relocated-pairwise-incompatibility",
                BudgetExhausted=False,
                State=replace(State, EffectiveRoutingLayers=6),
                MaximumAssignmentBudget=200,
                MaximumReservationVariants=2,
                MaximumLaneDiversityLevels=2,
                MaximumCandidateDiversityLevels=2,
                MaximumEffectiveRoutingLayers=8,
            ).Action,
            "AddRoutingLayer",
        )
        self.assertEqual(
            ChooseRoutingEscalationAction(
                Classification="relocated-pairwise-incompatibility",
                BudgetExhausted=False,
                State=replace(State, EffectiveRoutingLayers=8),
                MaximumAssignmentBudget=200,
                MaximumReservationVariants=2,
                MaximumLaneDiversityLevels=2,
                MaximumCandidateDiversityLevels=2,
                MaximumEffectiveRoutingLayers=8,
            ).Action,
            "IncreaseLaneDiversity",
        )
        self.assertEqual(
            ChooseRoutingEscalationAction(
                Classification="relocated-larger-matching-failure",
                BudgetExhausted=False,
                State=replace(
                    State,
                    EffectiveRoutingLayers=8,
                    LaneDiversityLevel=1,
                ),
                MaximumAssignmentBudget=200,
                MaximumReservationVariants=2,
                MaximumLaneDiversityLevels=2,
                MaximumCandidateDiversityLevels=2,
                MaximumEffectiveRoutingLayers=8,
            ).Action,
            "AdvancePlacement",
        )
        self.assertEqual(
            Decide("multi-pair-placement-conflict"),
            "RegenerateAffectedCandidates",
        )
        self.assertEqual(
            Decide(
                "multi-pair-placement-conflict",
                CandidateDiversityLevel=1,
            ),
            "AdvancePlacement",
        )
        self.assertEqual(
            Decide(
                "relocated-multi-pair-conflict",
                CandidateDiversityLevel=1,
            ),
            "AddRoutingLayer",
        )
        self.assertEqual(
            ChooseRoutingEscalationAction(
                Classification="relocated-multi-pair-conflict",
                BudgetExhausted=False,
                State=replace(
                    State,
                    CandidateDiversityLevel=1,
                    EffectiveRoutingLayers=8,
                ),
                MaximumAssignmentBudget=200,
                MaximumReservationVariants=2,
                MaximumLaneDiversityLevels=2,
                MaximumCandidateDiversityLevels=2,
                MaximumEffectiveRoutingLayers=8,
            ).Action,
            "AdvancePlacement",
        )
        self.assertEqual(
            ChooseRoutingEscalationAction(
                Classification="relocated-multi-pair-conflict",
                BudgetExhausted=False,
                State=replace(
                    State,
                    CandidateDiversityLevel=1,
                    EffectiveRoutingLayers=8,
                ),
                MaximumAssignmentBudget=200,
                MaximumReservationVariants=2,
                MaximumLaneDiversityLevels=2,
                MaximumCandidateDiversityLevels=3,
                MaximumEffectiveRoutingLayers=8,
            ).Action,
            "RegenerateAffectedCandidates",
        )
        self.assertEqual(
            ChooseRoutingEscalationAction(
                Classification="relocated-multi-pair-conflict",
                BudgetExhausted=False,
                State=replace(
                    State,
                    CandidateDiversityLevel=0,
                    EffectiveRoutingLayers=8,
                ),
                MaximumAssignmentBudget=200,
                MaximumReservationVariants=2,
                MaximumLaneDiversityLevels=2,
                MaximumCandidateDiversityLevels=2,
                MaximumEffectiveRoutingLayers=8,
            ).Action,
            "RegenerateAffectedCandidates",
        )
        self.assertEqual(Decide("no-candidate"), "RegenerateAffectedCandidates")
        self.assertEqual(
            Decide("pairwise-incompatibility"),
            "ChangePortalReservation",
        )
        self.assertEqual(
            Decide("pairwise-incompatibility", ReservationVariant=1),
            "TryUnreservedPortals",
        )
        self.assertEqual(
            Decide(
                "pairwise-incompatibility",
                PortalMode="unreserved",
                ReservationVariant=1,
            ),
            "IncreaseLaneDiversity",
        )
        self.assertEqual(
            Decide(
                "larger-matching-failure",
                PortalMode="unreserved",
                ReservationVariant=1,
                LaneDiversityLevel=1,
            ),
            "AddRoutingLayer",
        )
        self.assertEqual(
            Decide(
                "larger-matching-failure",
                PortalMode="unreserved",
                ReservationVariant=1,
                LaneDiversityLevel=1,
                EffectiveRoutingLayers=3,
            ),
            "AdvancePlacement",
        )

    def testBoundaryOverflowRanksBeforeDenseFootprint(self) -> None:
        def Feedback(BoundaryOverflow: int, GateFootprint: int):
            return PlacementRoutingFeedback(
                RoutingSpacing=6,
                BoundaryOverflow=BoundaryOverflow,
                PinScarcityCount=0,
                GuideOverflowPeak=0,
                GuideOverflowCells=0,
                PinEscapeConflictCount=0,
                LocalClaimCoverageRatio=0.0,
                LocalRouteTargets=0,
                LocalDirectConnectionCount=0,
                EstimatedGlobalExtensionNodes=1,
                EstimatedGlobalExtensionNets=1,
                RoutingDominanceProxy=0.0,
                FrozenLocalNetCount=0,
                PreOwnedNodeCount=0,
                Hpwl=1,
                LocalFanoutPenalty=0,
                WeightedLocalityCost=1,
                GateFootprint=GateFootprint,
            )

        CongestedDense = Feedback(BoundaryOverflow=1, GateFootprint=10)
        RoutableWide = Feedback(BoundaryOverflow=0, GateFootprint=20)
        self.assertLess(RoutableWide.Score, CongestedDense.Score)

    def testBoundaryPressureRanksBeforeExactAssignmentDimension(self) -> None:
        def Feedback(
            ExtensionNets: int,
            PreOwnedNodes: int,
            BoundaryOverflow: int,
            ExtensionNodes: int,
        ) -> PlacementRoutingFeedback:
            return PlacementRoutingFeedback(
                RoutingSpacing=6,
                BoundaryOverflow=BoundaryOverflow,
                PinScarcityCount=BoundaryOverflow,
                GuideOverflowPeak=0,
                GuideOverflowCells=0,
                PinEscapeConflictCount=0,
                LocalClaimCoverageRatio=0.0,
                LocalRouteTargets=0,
                LocalDirectConnectionCount=0,
                EstimatedGlobalExtensionNodes=ExtensionNodes,
                EstimatedGlobalExtensionNets=ExtensionNets,
                RoutingDominanceProxy=0.0,
                FrozenLocalNetCount=0,
                PreOwnedNodeCount=PreOwnedNodes,
                Hpwl=1,
                LocalFanoutPenalty=0,
                WeightedLocalityCost=1,
                GateFootprint=20,
            )

        LargeGlobalProblem = Feedback(12, 0, 0, 20)
        ConstrainedLocalOwnership = Feedback(4, 53, 0, 4)
        FlexibleLocalOwnership = Feedback(4, 45, 0, 7)
        BoundaryConstrained = Feedback(4, 45, 2, 7)

        self.assertLess(
            FlexibleLocalOwnership.Score,
            ConstrainedLocalOwnership.Score,
        )
        self.assertLess(ConstrainedLocalOwnership.Score, LargeGlobalProblem.Score)
        self.assertLess(FlexibleLocalOwnership.Score, BoundaryConstrained.Score)

    def testRoutabilityWorkBalancesLocalReuseAgainstSeverePressure(self) -> None:
        def Feedback(
            ExtensionNets: int,
            PreOwnedNodes: int,
            ExtensionNodes: int,
            BoundaryOverflow: int,
            PinScarcity: int,
        ) -> PlacementRoutingFeedback:
            return PlacementRoutingFeedback(
                RoutingSpacing=6,
                BoundaryOverflow=BoundaryOverflow,
                PinScarcityCount=PinScarcity,
                GuideOverflowPeak=0,
                GuideOverflowCells=0,
                PinEscapeConflictCount=0,
                LocalClaimCoverageRatio=0.0,
                LocalRouteTargets=0,
                LocalDirectConnectionCount=0,
                EstimatedGlobalExtensionNodes=ExtensionNodes,
                EstimatedGlobalExtensionNets=ExtensionNets,
                RoutingDominanceProxy=0.0,
                FrozenLocalNetCount=0,
                PreOwnedNodeCount=PreOwnedNodes,
                Hpwl=1,
                LocalFanoutPenalty=0,
                WeightedLocalityCost=1,
                GateFootprint=20,
            )

        SmallLocalReuse = Feedback(4, 52, 0, 2, 4)
        SmallUnpacked = Feedback(12, 0, 20, 0, 0)
        CongestedLocalReuse = Feedback(13, 189, 28, 8, 72)
        ScaleUnpacked = Feedback(45, 0, 77, 0, 0)

        self.assertLess(SmallLocalReuse.Score, SmallUnpacked.Score)
        self.assertLess(ScaleUnpacked.Score, CongestedLocalReuse.Score)

    def testStableFingerprintIgnoresDictionaryInsertionOrder(self) -> None:
        self.assertEqual(
            BuildStableFingerprint({"A": 1, "B": [2, 3]}),
            BuildStableFingerprint({"B": [2, 3], "A": 1}),
        )

    def testExpiredDeadlineRaisesTypedFailure(self) -> None:
        Deadline = RoutingDeadline(
            StartedAt=monotonic() - 2.0,
            ExpiresAt=monotonic() - 1.0,
        )
        with self.assertRaises(RoutingStageError) as Context:
            Deadline.RaiseIfExpired("TinyDeadline", {"CompletedWork": 4})
        self.assertEqual(
            Context.exception.Failure.Reason,
            RoutingFailureReason.RuntimeBudgetExceeded,
        )
        self.assertEqual(Context.exception.Failure.Stage, "TinyDeadline")
        self.assertTrue(Context.exception.Failure.Diagnostics["Deadline"]["Expired"])

    def testAdaptiveRuntimeLimitUsesTheTighterBoundWithoutResettingDeadline(self) -> None:
        Deadline = RoutingDeadline(StartedAt=0.0, ExpiresAt=100.0)

        with patch(
            "PhysicalDesign.Runtime.Reliability.monotonic",
            return_value=10.0,
        ):
            self.assertEqual(
                RemainingRoutingRuntimeMilliseconds(Deadline, 12.0),
                2_000,
            )

        with patch(
            "PhysicalDesign.Runtime.Reliability.monotonic",
            return_value=12.1,
        ):
            with self.assertRaises(RoutingStageError) as Context:
                EnforceRoutingRuntimeLimit(
                    Deadline=Deadline,
                    AdaptiveStartedAt=5.0,
                    AdaptiveExpiresAt=12.0,
                    Stage="ResourceGraph",
                    Diagnostics={"CompletedWork": 7},
                )

        Failure = Context.exception.Failure
        self.assertEqual(
            Failure.Reason,
            RoutingFailureReason.TrackAssignmentConflict,
        )
        self.assertEqual(Failure.Stage, "ResourceGraph")
        self.assertEqual(
            Failure.Diagnostics["Action"],
            "advance-placement-adaptive-slice-expired",
        )
        self.assertFalse(Failure.Diagnostics["Deadline"]["Expired"])

    def testNativeAdaptiveExpiryAdvancesPlacementBeforeWallClockRounding(self) -> None:
        Deadline = RoutingDeadline(StartedAt=0.0, ExpiresAt=100.0)

        with patch(
            "PhysicalDesign.Runtime.Reliability.monotonic",
            return_value=11.9995,
        ):
            with self.assertRaises(RoutingStageError) as Context:
                EnforceRoutingRuntimeLimit(
                    Deadline=Deadline,
                    AdaptiveStartedAt=5.0,
                    AdaptiveExpiresAt=12.0,
                    Stage="TrackAssignment",
                    NativeDeadlineExceeded=True,
                )

        self.assertEqual(
            Context.exception.Failure.Reason,
            RoutingFailureReason.TrackAssignmentConflict,
        )
        self.assertTrue(
            Context.exception.Failure.Diagnostics["NativeDeadlineExceeded"]
        )

    def testDeadlineFailurePreservesEscalationStateAndHistory(self) -> None:
        Deadline = RoutingDeadline(
            StartedAt=monotonic() - 2.0,
            ExpiresAt=monotonic() - 1.0,
        )
        State = RoutingEscalationState(
            PortalMode="unreserved",
            ReservationVariant=1,
            LaneDiversityLevel=2,
            CandidateDiversityLevel=3,
            EffectiveRoutingLayers=4,
            AssignmentBudget=512,
            CandidateFingerprint="candidate-fingerprint",
            ConflictFingerprint="conflict-fingerprint",
        )
        Diagnostics = BuildRoutingDeadlineDiagnostics(
            Deadline=Deadline,
            WorkTelemetry={"RouteTreeCompletedWork": 7},
            EscalationHistory=({"Action": "increase-guide-lane-diversity"},),
            EscalationState=State,
            StageTimingsSeconds={"PortalGeneration": 0.1234567},
            AdditionalDiagnostics={"CompletedWork": 9},
        )

        with self.assertRaises(RoutingStageError) as Context:
            Deadline.RaiseIfExpired("Candidate", Diagnostics)

        FailureDiagnostics = Context.exception.Failure.Diagnostics
        self.assertEqual(
            FailureDiagnostics["EscalationHistory"],
            ({"Action": "increase-guide-lane-diversity"},),
        )
        self.assertEqual(
            FailureDiagnostics["RoutingEscalationState"],
            State.ToDictionary(),
        )
        self.assertEqual(FailureDiagnostics["CompletedWork"], 9)
        self.assertEqual(FailureDiagnostics["RouteTreeCompletedWork"], 7)
        self.assertEqual(
            FailureDiagnostics["StageTimingsSeconds"],
            {"PortalGeneration": 0.123457},
        )
        self.assertTrue(FailureDiagnostics["Deadline"]["Expired"])

    def testTerminalDeadlineDiagnosticsOverrideStaleClosureDefaults(
        self,
    ) -> None:
        Deadline = RoutingDeadline(
            StartedAt=monotonic() - 2.0,
            ExpiresAt=monotonic() + 10.0,
        )
        ClosureHistory = (
            {"Action": "regenerate-affected-candidates"},
        )
        TerminalHistory = (
            *ClosureHistory,
            {
                "Action": (
                    "advance-placement-insufficient-adaptive-slice"
                )
            },
        )
        ClosureState = RoutingEscalationState(
            PortalMode="unreserved",
            ReservationVariant=0,
            LaneDiversityLevel=0,
            CandidateDiversityLevel=2,
            EffectiveRoutingLayers=8,
            AssignmentBudget=2048,
        )
        TerminalState = {
            **ClosureState.ToDictionary(),
            "CandidateFingerprint": "terminal-candidate",
            "ConflictFingerprint": "terminal-conflict",
        }
        TerminalDeadline = {
            "ElapsedSeconds": 11.5,
            "Expired": False,
            "RemainingMilliseconds": 500,
        }

        Diagnostics = BuildRoutingDeadlineDiagnostics(
            Deadline=Deadline,
            WorkTelemetry={"RouteTreeCompletedWork": 32},
            EscalationHistory=ClosureHistory,
            EscalationState=ClosureState,
            StageTimingsSeconds={"CandidateGeneration": 0.25},
            AdditionalDiagnostics={
                "EscalationHistory": TerminalHistory,
                "RoutingEscalationState": TerminalState,
                "Deadline": TerminalDeadline,
            },
        )

        self.assertEqual(
            Diagnostics["EscalationHistory"],
            TerminalHistory,
        )
        self.assertEqual(
            Diagnostics["RoutingEscalationState"],
            TerminalState,
        )
        self.assertEqual(Diagnostics["Deadline"], TerminalDeadline)
        self.assertEqual(Diagnostics["RouteTreeCompletedWork"], 32)
        self.assertEqual(
            Diagnostics["StageTimingsSeconds"],
            {"CandidateGeneration": 0.25},
        )

    def testAdaptiveEscalationRequiresRoomForObservedControlPass(self) -> None:
        self.assertTrue(HasAdaptiveEscalationBudget(0.1, 3.0, False))
        self.assertTrue(HasAdaptiveEscalationBudget(2.0, 1.5, True))
        self.assertFalse(HasAdaptiveEscalationBudget(1.0, 1.5, True))
        self.assertTrue(HasAdaptiveEscalationBudget(5.0, 8.0, True))

    def testExpiredDeadlineStopsCompactionBeforeReadingPlacement(self) -> None:
        Deadline = RoutingDeadline(
            StartedAt=monotonic() - 2.0,
            ExpiresAt=monotonic() - 1.0,
        )
        with self.assertRaises(RoutingStageError) as Context:
            CompactRoutedTrees(
                object(),
                object(),
                Deadline=Deadline,
            )
        self.assertEqual(
            Context.exception.Failure.Reason,
            RoutingFailureReason.RuntimeBudgetExceeded,
        )
        self.assertEqual(Context.exception.Failure.Stage, "RouteCompaction")
        self.assertEqual(
            Context.exception.Failure.Diagnostics["Phase"],
            "start",
        )

    def testRouteAttemptReportsCompletionOnlyAfterCompaction(self) -> None:
        Events = []
        Module = SimpleNamespace(Gates=[])
        Placed = SimpleNamespace(
            Module=Module,
            PlacedGates=[],
            RouteLayers={},
            FrozenNetWires={},
            LocalRouteClaims=(),
            LocalNetBranches={},
        )
        Placement = PcbPlacement(
            Placed=Placed,
            Clusters=(),
            SignalOrder=(),
            LayerCount=1,
        )
        Routed = RoutedDesign(
            Module=Module,
            PlacedGates=[],
            Wires=[],
            Supports=[],
            RepeaterInputFacings={},
            NetWires={},
        )
        Deadline = RoutingDeadline.Start(5.0)

        def Route(*_Arguments, **Options):
            Options["IterationProgressCallback"](0, 6)
            Options["IterationProgressCallback"](5, 6)
            Options["IterationProgressCallback"](6, 6)
            return Routed

        def Compact(*_Arguments, **Options):
            Events.append(("compaction", Options["Deadline"]))
            return Routed

        def Progress(Completed, Total):
            Events.append(("progress", Completed, Total))

        with (
            patch("PhysicalDesign.Routing.Pcb.RoutePcbNets", side_effect=Route),
            patch("PhysicalDesign.Routing.Pcb.CompactRoutedTrees", side_effect=Compact),
        ):
            Result = RoutePcbAttempt(
                Placement,
                BuildRoutingAttemptPolicies()[0],
                Resources=object(),
                ProgressCallback=Progress,
                Policy=LocalFirstPhysicalDesignPolicy,
                Deadline=Deadline,
            )

        self.assertIs(Result, Routed)
        CompactionIndex = next(
            Index for Index, Event in enumerate(Events)
            if Event[0] == "compaction"
        )
        CompletionIndices = [
            Index for Index, Event in enumerate(Events)
            if Event[0] == "progress" and Event[1] == Event[2]
        ]
        self.assertEqual(len(CompletionIndices), 1)
        self.assertGreater(CompletionIndices[0], CompactionIndex)
        self.assertIs(Events[CompactionIndex][1], Deadline)

    def testEscalationStateIncludesEffectivePhysicalControls(self) -> None:
        First = RoutingEscalationState(
            PortalMode="reserved",
            ReservationVariant=0,
            LaneDiversityLevel=0,
            CandidateDiversityLevel=0,
            EffectiveRoutingLayers=2,
            AssignmentBudget=100,
            CandidateFingerprint="candidates",
            ConflictFingerprint="conflicts",
        )
        Second = replace(First, EffectiveRoutingLayers=3)
        self.assertNotEqual(First.EffectiveKey, Second.EffectiveKey)
        self.assertEqual(First.ToDictionary()["PortalMode"], "reserved")

    def testRoutingFailureArtifactUsesStableSchema(self) -> None:
        with tempfile.TemporaryDirectory() as Directory:
            OutputPath = Path(Directory) / "Failure.litematic"
            FailurePath = WriteRoutingFailureArtifact(
                OutputPath=OutputPath,
                RequestedStrategy=RoutingStrategy.Default,
                Failure=RoutingFailure(
                    Reason=RoutingFailureReason.TrackAssignmentConflict,
                    Stage="TrackAssignment",
                    AffectedNets=("N0",),
                    Diagnostics={
                        "PlacementAttempts": [{"CandidateId": "Placement-001"}],
                        "CandidateFingerprint": "candidate-fingerprint",
                        "ConflictFingerprint": "conflict-fingerprint",
                    },
                ),
                StartedAt=monotonic(),
            )
            Value = json.loads(FailurePath.read_text(encoding="utf-8"))
        self.assertEqual(Value["SchemaVersion"], "routing-failure-v1")
        self.assertEqual(Value["Failure"]["Reason"], "TrackAssignmentConflict")
        self.assertFalse(Value["Strategy"]["FallbackUsed"])
        self.assertEqual(
            Value["Fingerprints"]["Candidate"],
            "candidate-fingerprint",
        )

    def testPlacementFingerprintChangesWithGeometry(self) -> None:
        def Value(X: int, MirrorX: bool = False) -> PcbPlacement:
            return PcbPlacement(
                Placed=SimpleNamespace(
                    PlacedGates=[SimpleNamespace(
                        Name="N0",
                        Kind="NAND",
                        X=X,
                        Y=1,
                        Z=0,
                        Rotation=False,
                        MirrorX=MirrorX,
                    )],
                    LocalRouteClaims=(),
                ),
                Clusters=(),
                SignalOrder=(),
                LayerCount=2,
            )

        self.assertNotEqual(
            BuildPlacementFingerprint(Value(0)),
            BuildPlacementFingerprint(Value(1)),
        )
        self.assertNotEqual(
            BuildPlacementFingerprint(Value(0)),
            BuildPlacementFingerprint(Value(0, MirrorX=True)),
        )

if __name__ == "__main__":
    unittest.main()
