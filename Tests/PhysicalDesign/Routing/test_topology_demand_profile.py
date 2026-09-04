"""Pure topology-demand trigger coverage for PCB placement."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from Compilation.Ir.Models import Gate, GateKind, ModuleIR
from PhysicalDesign.Geometry.Placement import PlacedDesign
from PhysicalDesign.Placement.Engine.Clusters import PcbPlacement
from PhysicalDesign.Placement.Engine.Constraints import BuildEffectiveAssignmentCutPairwiseEdges, BuildJointPlacementSearchCacheKey, PlacementAssignmentConstraintSet, RequiresStructuredAssignmentCutRelocation
from PhysicalDesign.Placement.Engine.MandatoryAccess import MandatoryAccessConflictProfile, SelectExactInterfaceCommitStates
from PhysicalDesign.Placement.Engine.Repair import ShouldIncludeNearPortalPackedAccessRepair
from PhysicalDesign.Placement.Engine.Search import PrioritizeRelocationClusters, RelocateClusterSlots, ShouldExpandBoundaryEscapeGeometry
from PhysicalDesign.Orchestration.Candidates import BuildComponentAccessFeedbackPlacementScore, BuildClusterInterfaceStageSchedule, BuildLocalComponentCompilationAdmissionFailure, HasDistinctRetainedPhysicalEligibilityState, HasQueuedGeneratedProofGuidedEligibilityState, QueuedPhysicalEligibilityPlacementFingerprints, SelectRetainedPhysicalPlacementForAccessCore, PcbPlacementCandidate
from PhysicalDesign.Orchestration.Demand import ApplyJointPlacementPortfolioTrigger, BuildPlacementGenerationPlan, BuildTopologyDemandPressureProfile, BuildTopologyDemandProfile, MeasurePlacementTopologyDemand, ResolveJointPlacementPortfolioTrigger, TopologyDemandProfile
from PhysicalDesign.Orchestration.Feedback import BuildPlacementFingerprint, BuildStructuralHigherOrderAssignmentCutFingerprint, SelectCumulativeRepeatedAssignmentCutDiversificationSignals, SelectInterfaceDiversePlacementStates, ShouldDeferTopologyCutForMaterializedSibling
from PhysicalDesign.Orchestration.Portfolios import AddMandatoryAccessPortfolioPairwiseConstraints, BuildMandatoryAccessPairwiseEdges, BuildMandatoryAccessPortfolioExpectedCandidateIndices, BuildMandatoryAccessPortfolioRecipeIdentity, BuildPendingJointPlacementPortfolioIdentity, BuildPendingJointPlacementPortfolioFingerprint, BuildPendingJointPlacementStateKey, DeferredActivePortfolioAssignmentCut, EvaluateCompleteMandatoryAccessPortfolio, ExtractAuthoritativeCutAccessDomainFingerprint, HasActiveMaterializedJointPlacementCandidate, HasCurrentMaterializedJointPlacementCandidate, HasCurrentPendingJointPlacementState, MandatoryAccessPortfolioEvidence, MandatoryAccessPortfolioEvaluation, MandatoryAccessPortfolioIdentity, MandatoryAccessPortfolioIdentityMatchesCurrent, MandatoryAccessPortfolioRejection, PendingJointPlacementState, PendingJointPlacementStateMatchesIdentity, PlacementAssignmentConstraintsAreActive, PlacementCandidateMatchesConstraintIdentity, PlacementCandidateMatchesActiveJointPortfolio, PlacementConstraintFingerprintMatchesIdentity, PlacementGenerationRequest, RebindTerminalJointPlacementConstraintEpoch, RetainUnmaterializedJointPlacementStates, SelectNewPendingJointPlacementPortfolioFingerprint, SelectTransactionalRepairClusterCount, ShouldAdmitPostDiversificationOwnershipRepair, TransactionalCutRequiresCoordinatedClusterRepair, TransactionalCutRepairSignals, SelectTransactionalEndpointRepairSignals, ShouldRefreshTerminalActiveJointPlacementConstraintEpoch, ShouldStopTransactionalRepairVariantGeneration, ShouldOpenStrongMandatoryAccessRepair, ShouldDeferTopologyCutForRetainedPortfolioSibling, ShouldUseMandatoryAccessPreScreen, TransactionalCutStrictlyNarrowsParentInterface, TransactionalCutRevisitsAncestorInterface, TransactionalCutMayEscalateRepairClusterCount, TransactionalEndpointRepairIdentityIsFresh
from PhysicalDesign.Orchestration.Preparation import BuildClusterInterfaceUnsatProof, BuildClusterInterfaceComponentStateFingerprint, BuildPlacementRelocationVariant, BuildPlacementRetentionFingerprint, DenseRetainedLeaseProofSliceSeconds, RequiresExactClusterInterfaceSolve
from PhysicalDesign.Contracts.Failures import RoutingAssignmentCut, RoutingFailure, RoutingFailureReason, RoutingStageError
from PhysicalDesign.Policy import LocalFirstPhysicalDesignPolicy
from PhysicalDesign.Runtime.Reliability import RoutingDeadline
from PhysicalDesign.Contracts.Placement import ClusterInterfaceRealizabilityNogood, ClusterInterfaceStateProof
from Compilation.Synthesis.LogicOptimization import OptimizeLogic
from Compilation.Synthesis.NandTransform import ToNandOnly
from Formats.SystemVerilog.Sv import ParseSvToNetlist


def BuildNandModule(
    *,
    Name: str,
    Inputs: tuple[str, ...],
    Outputs: tuple[str, ...],
    Gates: tuple[Gate, ...],
) -> ModuleIR:
    """Build one synthetic NAND DAG without invoking placement or routing."""
    return ModuleIR(
        Name=Name,
        Inputs=list(Inputs),
        Outputs=list(Outputs),
        Gates=list(Gates),
    )


def BuildHighFanoutWithoutReconvergence() -> ModuleIR:
    """Build four independent source branches that never meet again."""
    return BuildNandModule(
        Name="IndependentFanout",
        Inputs=("Source", "Bias0", "Bias1", "Bias2", "Bias3"),
        Outputs=("Result0", "Result1", "Result2", "Result3"),
        Gates=tuple(
            Gate(
                Name=f"BranchGate{Index}",
                Kind=GateKind.NAND,
                Outputs=[f"Result{Index}"],
                Inputs=["Source", f"Bias{Index}"],
            )
            for Index in range(4)
        ),
    )


def BuildReconvergentFanout(BranchCount: int) -> ModuleIR:
    """Build source branches that rejoin through a binary NAND tree."""
    if BranchCount not in {2, 4}:
        raise ValueError("synthetic reconvergence supports two or four branches")
    Inputs = ("Source", *(f"Bias{Index}" for Index in range(BranchCount)))
    Branches = tuple(
        Gate(
            Name=f"BranchGate{Index}",
            Kind=GateKind.NAND,
            Outputs=[f"BranchSignal{Index}"],
            Inputs=["Source", f"Bias{Index}"],
        )
        for Index in range(BranchCount)
    )
    if BranchCount == 2:
        Joins = (
            Gate(
                Name="FinalJoinGate",
                Kind=GateKind.NAND,
                Outputs=["Result"],
                Inputs=["BranchSignal0", "BranchSignal1"],
            ),
        )
    else:
        Joins = (
            Gate(
                Name="LeftJoinGate",
                Kind=GateKind.NAND,
                Outputs=["LeftJoinSignal"],
                Inputs=["BranchSignal0", "BranchSignal1"],
            ),
            Gate(
                Name="RightJoinGate",
                Kind=GateKind.NAND,
                Outputs=["RightJoinSignal"],
                Inputs=["BranchSignal2", "BranchSignal3"],
            ),
            Gate(
                Name="FinalJoinGate",
                Kind=GateKind.NAND,
                Outputs=["Result"],
                Inputs=["LeftJoinSignal", "RightJoinSignal"],
            ),
        )
    return BuildNandModule(
        Name=f"ReconvergentFanout{BranchCount}",
        Inputs=Inputs,
        Outputs=("Result",),
        Gates=(*Branches, *Joins),
    )


def RenameInternalsAndReverseGates(Module: ModuleIR) -> ModuleIR:
    """Rename every internal gate/signal and reverse the gate declaration order."""
    InternalSignals = sorted({
        Signal
        for GateValue in Module.Gates
        for Signal in GateValue.Outputs
        if Signal not in Module.Outputs
    })
    SignalRenames = {
        Signal: f"RenamedInternalSignal{Index}"
        for Index, Signal in enumerate(reversed(InternalSignals))
    }
    RenamedGates = [
        Gate(
            Name=f"RenamedGate{Index}",
            Kind=GateValue.Kind,
            Outputs=[
                SignalRenames.get(Output, Output)
                for Output in GateValue.Outputs
            ],
            Inputs=[
                SignalRenames.get(Input, Input)
                for Input in GateValue.Inputs
            ],
            Attrs=dict(GateValue.Attrs),
        )
        for Index, GateValue in enumerate(reversed(Module.Gates))
    ]
    return BuildNandModule(
        Name="RenamedAndReorderedTopology",
        Inputs=tuple(Module.Inputs),
        Outputs=tuple(Module.Outputs),
        Gates=tuple(RenamedGates),
    )


def NumericProfile(Profile: TopologyDemandProfile) -> dict[str, int | float]:
    """Extract dataclass metrics while excluding Boolean trigger properties."""
    return {
        Field.name: Value
        for Field in fields(Profile)
        if (
            isinstance((Value := getattr(Profile, Field.name)), (int, float))
            and not isinstance(Value, bool)
        )
    }


def BuildExampleProfile(ModuleName: str) -> TopologyDemandProfile:
    """Parse and lower one bundled example before topology-only profiling."""
    Parsed = ParseSvToNetlist(
        InputPath=Path("Assets/Examples") / f"{ModuleName}.sv",
        TopModule=ModuleName,
    )
    NandOnly = ToNandOnly(OptimizeLogic(Parsed))
    return BuildTopologyDemandProfile(
        NandOnly.Modules[NandOnly.Top]
    )


def BuildMandatoryCapacityCut(
    FirstSignal: str,
    SecondSignal: str,
) -> RoutingAssignmentCut:
    """Build the typed two-signal cut emitted by fixed-access assignment."""
    Cut = RoutingAssignmentCut.FromFailure(
        RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="InitialCandidateAssignment",
            Diagnostics={
                "ConflictGraph": {
                    "Classification": "mandatory-boundary-capacity-cut",
                    "ConflictSignals": [FirstSignal, SecondSignal],
                    "PriorityRelocationSignals": [
                        FirstSignal,
                        SecondSignal,
                    ],
                    "RelocationSignals": [FirstSignal, SecondSignal],
                },
            },
        ),
    )
    if Cut is None:
        raise AssertionError("mandatory capacity cut was not typed")
    return Cut


def BuildHigherOrderCapacityCut(
    *,
    PrioritySignals: tuple[str, ...],
    PairEdge: tuple[str, str],
) -> RoutingAssignmentCut:
    """Build one typed higher-order placement cut for constraint tests."""
    Cut = RoutingAssignmentCut.FromFailure(
        RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="TrackAssignment",
            Diagnostics={
                "ConflictGraph": {
                    "Classification": "higher-order-placement-conflict",
                    "ConflictSignals": list(PrioritySignals),
                    "PriorityRelocationSignals": list(PrioritySignals),
                    "RelocationSignals": [
                        *PrioritySignals,
                        *PairEdge,
                    ],
                    "PairwiseIncompatibleEdges": [list(PairEdge)],
                },
            },
        ),
    )
    if Cut is None:
        raise AssertionError("higher-order cut was not typed")
    return Cut


def BuildOwnedPairCapacityCut(
    Edge: tuple[str, str],
    OwnershipFingerprint: str,
) -> RoutingAssignmentCut:
    """Build one exact pair cut with immutable access-ownership evidence."""
    Cut = RoutingAssignmentCut.FromFailure(
        RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="InitialCandidateAssignment",
            Diagnostics={
                "ConflictGraph": {
                    "Classification": "mandatory-boundary-capacity-cut",
                    "ConflictSignals": list(Edge),
                    "PriorityRelocationSignals": list(Edge),
                    "RelocationSignals": list(Edge),
                    "PairwiseIncompatibleEdges": [list(Edge)],
                },
            },
        ),
        MandatoryAccessOwnershipFingerprint=OwnershipFingerprint,
    )
    if Cut is None:
        raise AssertionError("owned pair capacity cut was not typed")
    return Cut


class TopologyDemandProfileTests(unittest.TestCase):
    def testPhysicalAccessFeedbackRanksExactEscapeDomainBeforeDepth(
        self,
    ) -> None:
        def Candidate(
            Fingerprint: str,
            EscapeCount: int,
            SourceX: int,
        ):
            Request = SimpleNamespace(
                Signal="CutSignal",
                SourceCluster=0,
                TargetCluster=1,
                SourceTerminal=(SourceX, 1, 1),
                TargetTerminals=((20, 1, 1),),
                SourceBoundarySide="west",
                TargetBoundarySide="east",
            )
            Placement = SimpleNamespace(
                Placed=SimpleNamespace(
                    PlacedGates=(
                        SimpleNamespace(Name="Left", X=0, Z=0),
                        SimpleNamespace(Name="Right", X=20, Z=0),
                    ),
                    ClusterBoundaryLeaseRequests=(Request,),
                ),
                Clusters=(("Left",), ("Right",)),
                ClusterBoundaryLeaseRequests=(Request,),
                PackedClusters=(
                    SimpleNamespace(
                        ClusterId=0,
                        LegalEscapeCandidateCounts=((
                            "CutSignal", EscapeCount,
                        ),),
                    ),
                    SimpleNamespace(
                        ClusterId=1,
                        LegalEscapeCandidateCounts=((
                            "CutSignal", EscapeCount,
                        ),),
                    ),
                ),
            )
            return SimpleNamespace(
                PlacementFingerprint=Fingerprint,
                CandidateId="Placement-" + Fingerprint,
                JointPlacementState=object(),
                TopologyDemand=object(),
                Placement=Placement,
            )

        CompactDomain = Candidate("compact", 2, 1)
        BroadDomain = Candidate("broad", 9, 0)
        self.assertLess(
            BuildComponentAccessFeedbackPlacementScore(
                CompactDomain,
                ("CutSignal",),
            ),
            BuildComponentAccessFeedbackPlacementScore(
                BroadDomain,
                ("CutSignal",),
            ),
        )
        self.assertEqual(
            SelectRetainedPhysicalPlacementForAccessCore(
                (BroadDomain, CompactDomain),
                (),
                ("CutSignal",),
            ).PlacementFingerprint,
            "compact",
        )

    def testPhysicalAccessFeedbackScoreIsRenameAndOrderInvariant(
        self,
    ) -> None:
        def Build(Signals: tuple[str, str], Reverse: bool):
            Requests = [
                SimpleNamespace(
                    Signal=Signal,
                    SourceCluster=0,
                    TargetCluster=1,
                    SourceTerminal=(0, 1, Index),
                    TargetTerminals=((10, 1, Index),),
                    SourceBoundarySide="west",
                    TargetBoundarySide="east",
                )
                for Index, Signal in enumerate(Signals)
            ]
            Counts = [(Signal, Index + 2) for Index, Signal in enumerate(Signals)]
            if Reverse:
                Requests.reverse()
                Counts.reverse()
            return SimpleNamespace(Placement=SimpleNamespace(
                Placed=SimpleNamespace(
                    PlacedGates=(
                        SimpleNamespace(Name="Left", X=0, Z=0),
                        SimpleNamespace(Name="Right", X=10, Z=0),
                    ),
                    ClusterBoundaryLeaseRequests=tuple(Requests),
                ),
                Clusters=(("Left",), ("Right",)),
                ClusterBoundaryLeaseRequests=tuple(Requests),
                PackedClusters=tuple(
                    SimpleNamespace(
                        ClusterId=ClusterId,
                        LegalEscapeCandidateCounts=tuple(Counts),
                    )
                    for ClusterId in (0, 1)
                ),
            ))

        self.assertEqual(
            BuildComponentAccessFeedbackPlacementScore(
                Build(("Alpha", "Beta"), False),
                ("Alpha", "Beta"),
            ),
            BuildComponentAccessFeedbackPlacementScore(
                Build(("Renamed0", "Renamed1"), True),
                ("Renamed1", "Renamed0"),
            ),
        )

    def testPhysicalProofConsumesRetainedPlacementBeforeGeneration(
        self,
    ) -> None:
        def Candidate(Fingerprint: str):
            return SimpleNamespace(
                PlacementFingerprint=Fingerprint,
                CandidateId="Placement-" + Fingerprint,
                JointPlacementState=object(),
                TopologyDemand=object(),
            )

        Candidates = tuple(map(Candidate, ("known", "wide", "best")))
        with patch(
            "PhysicalDesign.Orchestration.Candidates."
            "BuildComponentAccessFeedbackPlacementScore",
            side_effect=lambda CandidateValue, _Signals: {
                "known": (0,),
                "best": (1,),
                "wide": (2,),
            }[CandidateValue.PlacementFingerprint],
        ):
            Selected = SelectRetainedPhysicalPlacementForAccessCore(
                Candidates,
                ("known",),
                ("NandNet28", "NandNet29"),
            )

        self.assertIsNotNone(Selected)
        self.assertEqual(Selected.PlacementFingerprint, "best")

    def testPhysicalProofGuidedPlacementWaitsForRetainedSibling(
        self,
    ) -> None:
        First = SimpleNamespace(PlacementFingerprint="first")
        Second = SimpleNamespace(PlacementFingerprint="second")
        Queue = [
            ("solve-prepared-eligibility", 0, First, 0, 0),
            ("prepare-eligibility", 1, Second, 0, 0),
            ("prepare-eligibility", 2, First, 0, 1),
        ]

        self.assertTrue(HasDistinctRetainedPhysicalEligibilityState(
            Queue,
            ComponentVariant=0,
            PlacementFingerprint="first",
        ))
        self.assertTrue(HasDistinctRetainedPhysicalEligibilityState(
            Queue,
            ComponentVariant=1,
            PlacementFingerprint="first",
        ))
        self.assertFalse(HasDistinctRetainedPhysicalEligibilityState(
            Queue[:1],
            ComponentVariant=0,
            PlacementFingerprint="first",
        ))

        self.assertTrue(HasQueuedGeneratedProofGuidedEligibilityState(
            Queue,
            {"second"},
        ))
        self.assertFalse(HasQueuedGeneratedProofGuidedEligibilityState(
            Queue,
            {"missing"},
        ))
        self.assertEqual(
            QueuedPhysicalEligibilityPlacementFingerprints(Queue),
            frozenset(("second", "first")),
        )

    def testAuthoritativeCutAccessDomainFingerprintSurvivesScheduler(
        self,
    ) -> None:
        Direct = RoutingFailure(
            Reason=RoutingFailureReason.BoundaryEscapeInfeasible,
            Stage="ClusterBoundaryLease",
            Diagnostics={
                "ClusterInterfacePatternSearch": {
                    "AuthoritativeCutAccessDomainFingerprint": (
                        "direct-domain"
                    ),
                },
            },
        )
        Scheduled = RoutingFailure(
            Reason=RoutingFailureReason.BoundaryEscapeInfeasible,
            Stage="ClusterBoundaryLease",
            Diagnostics={
                "ClusterBoundaryLeaseScheduler": {
                    "Attempts": [
                        {
                            "AuthoritativeCutAccessDomainFingerprint": (
                                "first-domain"
                            ),
                        },
                        {
                            "AuthoritativeCutAccessDomainFingerprint": (
                                "latest-domain"
                            ),
                        },
                    ],
                },
            },
        )

        self.assertEqual(
            ExtractAuthoritativeCutAccessDomainFingerprint(Direct),
            "direct-domain",
        )
        self.assertEqual(
            ExtractAuthoritativeCutAccessDomainFingerprint(Scheduled),
            "latest-domain",
        )

    def testEquivalentAuthoritativeAccessDomainEndsStalePortfolio(
        self,
    ) -> None:
        def Cut(Signals, Ownership):
            Value = RoutingAssignmentCut.FromFailure(
                RoutingFailure(
                    Reason=(
                        RoutingFailureReason.BoundaryEscapeInfeasible
                    ),
                    Stage="ClusterBoundaryLease",
                    AffectedNets=tuple(Signals),
                    Diagnostics={
                        "AuthoritativeCutAccessDomainFingerprint": (
                            Ownership
                        ),
                        "ConflictGraph": {
                            "Classification": "saturated-boundary-cut",
                            "ConflictSignals": list(Signals),
                            "RelocationSignals": list(Signals),
                            "PriorityRelocationSignals": list(Signals),
                        },
                    },
                ),
                MandatoryAccessOwnershipFingerprint=(
                    f"placement-{Signals[0]}"
                ),
            )
            self.assertIsNotNone(Value)
            return Value

        Prior = Cut(("OldA", "OldB"), "same-exact-domain")
        Current = Cut(("NewA", "NewB"), "same-exact-domain")
        Other = Cut(("NewA", "Other"), "same-exact-domain")
        Topology = {
            "OldA": "role-a",
            "OldB": "role-b",
            "NewA": "role-a",
            "NewB": "role-b",
            "Other": "role-c",
        }

        self.assertFalse(
            ShouldDeferTopologyCutForMaterializedSibling(
                Requested=True,
                TopologyAccessRepairEligible=True,
                CommittedHistory=(),
                DeferredCuts=(Prior,),
                Current=Current,
                SignalTopologyFingerprints=Topology,
            )
        )
        self.assertTrue(
            ShouldDeferTopologyCutForMaterializedSibling(
                Requested=True,
                TopologyAccessRepairEligible=True,
                CommittedHistory=(),
                DeferredCuts=(Prior,),
                Current=Current,
                SignalTopologyFingerprints=Topology,
                AllowRepeatedCutCommit=False,
            )
        )
        self.assertTrue(
            ShouldDeferTopologyCutForMaterializedSibling(
                Requested=True,
                TopologyAccessRepairEligible=True,
                CommittedHistory=(),
                DeferredCuts=(Prior,),
                Current=Other,
                SignalTopologyFingerprints=Topology,
            )
        )

    def testTopologyLeaseCutWaitsForScreenedMaterializedSibling(
        self,
    ) -> None:
        Failure = RoutingFailure(
            Reason=RoutingFailureReason.BoundaryEscapeInfeasible,
            Stage="ClusterBoundaryLease",
        )
        self.assertTrue(
            ShouldDeferTopologyCutForRetainedPortfolioSibling(
                TopologyRequiresJointPortfolio=True,
                ActiveRelocatedPortfolioCandidate=True,
                RemainingRetainedActiveCandidates=2,
                Failure=Failure,
            )
        )
        for Overrides in (
            {"TopologyRequiresJointPortfolio": False},
            {"ActiveRelocatedPortfolioCandidate": False},
            {"RemainingRetainedActiveCandidates": 1},
            {
                "Failure": RoutingFailure(
                    Reason=RoutingFailureReason.TrackAssignmentConflict,
                    Stage="TrackAssignment",
                )
            },
        ):
            Arguments = {
                "TopologyRequiresJointPortfolio": True,
                "ActiveRelocatedPortfolioCandidate": True,
                "RemainingRetainedActiveCandidates": 2,
                "Failure": Failure,
                **Overrides,
            }
            self.assertFalse(
                ShouldDeferTopologyCutForRetainedPortfolioSibling(
                    **Arguments
                )
            )
        ExhaustivePairFailure = RoutingFailure(
            Reason=RoutingFailureReason.BoundaryEscapeInfeasible,
            Stage="ClusterBoundaryLease",
            AffectedNets=("Left", "Right"),
            Diagnostics={
                "ConflictGraph": {
                    "Classification": "saturated-boundary-cut",
                    "ConflictSignals": ["Left", "Right"],
                    "RelocationSignals": ["Left", "Right"],
                    "PairwiseIncompatibleEdges": [["Left", "Right"]],
                },
                "ClusterInterfacePatternSearch": {
                    "Applied": True,
                    "CoreShrinkComplete": True,
                    "UnavoidablePairEdges": [["Left", "Right"]],
                    "CutLocalJointSearches": [{
                        "CutSignals": ["Right", "Left"],
                        "CutEdges": [["Right", "Left"]],
                        "BudgetExhausted": False,
                        "SearchVariantCount": 16,
                        "ExpansionCount": 8,
                        "SolutionCount": 0,
                        "FailedStateCount": 4,
                    }],
                },
            },
        )
        self.assertFalse(
            ShouldDeferTopologyCutForRetainedPortfolioSibling(
                TopologyRequiresJointPortfolio=True,
                ActiveRelocatedPortfolioCandidate=True,
                RemainingRetainedActiveCandidates=2,
                Failure=ExhaustivePairFailure,
            )
        )
        self.assertFalse(
            ShouldDeferTopologyCutForRetainedPortfolioSibling(
                TopologyRequiresJointPortfolio=True,
                ActiveRelocatedPortfolioCandidate=False,
                RemainingRetainedActiveCandidates=6,
                Failure=ExhaustivePairFailure,
                ActiveTransactionalEndpointPortfolioCandidate=True,
            )
        )
        TransactionalAdvance = RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="Candidate",
            RepairActions=("AdvancePlacementCandidate",),
        )
        self.assertTrue(
            ShouldDeferTopologyCutForRetainedPortfolioSibling(
                TopologyRequiresJointPortfolio=True,
                ActiveRelocatedPortfolioCandidate=False,
                RemainingRetainedActiveCandidates=6,
                Failure=TransactionalAdvance,
                ActiveTransactionalEndpointPortfolioCandidate=True,
            )
        )
        BoundedTransactionalCut = RoutingFailure(
            Reason=RoutingFailureReason.BoundaryEscapeInfeasible,
            Stage="ClusterBoundaryLease",
            AffectedNets=("One", "Two"),
            Diagnostics={
                "ConflictGraph": {
                    "Classification": "saturated-boundary-cut",
                    "ConflictSignals": ["One", "Two"],
                    "PriorityRelocationSignals": ["One", "Two"],
                    "RelocationSignals": ["One", "Two"],
                    "PairwiseIncompatibleEdges": [["One", "Two"]],
                },
            },
        )
        self.assertFalse(
            ShouldDeferTopologyCutForRetainedPortfolioSibling(
                TopologyRequiresJointPortfolio=True,
                ActiveRelocatedPortfolioCandidate=True,
                RemainingRetainedActiveCandidates=6,
                Failure=BoundedTransactionalCut,
                ActiveTransactionalEndpointPortfolioCandidate=True,
                TransactionalCutStrictlyNarrowsParentInterface=True,
            )
        )
        self.assertTrue(
            ShouldDeferTopologyCutForRetainedPortfolioSibling(
                TopologyRequiresJointPortfolio=True,
                ActiveRelocatedPortfolioCandidate=True,
                RemainingRetainedActiveCandidates=6,
                Failure=BoundedTransactionalCut,
                ActiveTransactionalEndpointPortfolioCandidate=True,
                TransactionalCutStrictlyNarrowsParentInterface=False,
            )
        )
        self.assertFalse(
            ShouldDeferTopologyCutForRetainedPortfolioSibling(
                TopologyRequiresJointPortfolio=True,
                ActiveRelocatedPortfolioCandidate=True,
                RemainingRetainedActiveCandidates=6,
                Failure=BoundedTransactionalCut,
                ActiveTransactionalEndpointPortfolioCandidate=True,
                TransactionalCutStrictlyNarrowsParentInterface=False,
                TransactionalCutRepeatedAcrossAccessDistinctPlacements=True,
            )
        )
        self.assertTrue(
            ShouldDeferTopologyCutForRetainedPortfolioSibling(
                TopologyRequiresJointPortfolio=True,
                ActiveRelocatedPortfolioCandidate=True,
                RemainingRetainedActiveCandidates=2,
                Failure=BoundedTransactionalCut,
                ActiveTransactionalEndpointPortfolioCandidate=True,
                TransactionalCutRepeatedAcrossAccessDistinctPlacements=True,
                TransactionalExactPairAfterCoordinatedRepair=True,
            )
        )
        self.assertTrue(
            ShouldDeferTopologyCutForRetainedPortfolioSibling(
                TopologyRequiresJointPortfolio=True,
                ActiveRelocatedPortfolioCandidate=True,
                RemainingRetainedActiveCandidates=2,
                Failure=ExhaustivePairFailure,
                ActiveTransactionalEndpointPortfolioCandidate=True,
                TransactionalCutRepeatedAcrossAccessDistinctPlacements=True,
                TransactionalExactPairAfterCoordinatedRepair=True,
            )
        )
        self.assertFalse(
            ShouldDeferTopologyCutForRetainedPortfolioSibling(
                TopologyRequiresJointPortfolio=True,
                ActiveRelocatedPortfolioCandidate=True,
                RemainingRetainedActiveCandidates=2,
                Failure=BoundedTransactionalCut,
                ActiveTransactionalEndpointPortfolioCandidate=True,
                TransactionalCutRevisitsAncestorInterface=True,
            )
        )
        LargeRepeatedTransactionalCut = RoutingFailure(
            Reason=RoutingFailureReason.BoundaryEscapeInfeasible,
            Stage="ClusterBoundaryLease",
            AffectedNets=tuple(f"Signal{Index}" for Index in range(6)),
            Diagnostics={
                "ConflictGraph": {
                    "Classification": "saturated-boundary-cut",
                    "ConflictSignals": [
                        f"Signal{Index}" for Index in range(6)
                    ],
                    "RelocationSignals": [
                        f"Signal{Index}" for Index in range(6)
                    ],
                },
            },
        )
        self.assertFalse(
            ShouldDeferTopologyCutForRetainedPortfolioSibling(
                TopologyRequiresJointPortfolio=True,
                ActiveRelocatedPortfolioCandidate=True,
                RemainingRetainedActiveCandidates=2,
                Failure=LargeRepeatedTransactionalCut,
                ActiveTransactionalEndpointPortfolioCandidate=True,
                TransactionalCutRepeatedAcrossAccessDistinctPlacements=True,
            )
        )

    def testTransactionalCutFrontierAdvancesOnlyForStrictSubset(
        self,
    ) -> None:
        self.assertTrue(
            TransactionalCutStrictlyNarrowsParentInterface(
                frozenset({"Carry", "Generate", "Propagate"}),
                frozenset({"Carry", "Propagate"}),
            )
        )
        for Child in (
            frozenset({"Carry", "Generate", "Propagate"}),
            frozenset({"Unrelated"}),
            frozenset({"Carry", "Generate", "Propagate", "Extra"}),
            frozenset(),
        ):
            self.assertFalse(
                TransactionalCutStrictlyNarrowsParentInterface(
                    frozenset({"Carry", "Generate", "Propagate"}),
                    Child,
                )
            )

        NarrowCut = RoutingAssignmentCut.FromFailure(RoutingFailure(
            Reason=RoutingFailureReason.BoundaryEscapeInfeasible,
            Stage="ClusterBoundaryLease",
            AffectedNets=("Carry", "Propagate"),
            Diagnostics={
                "ConflictGraph": {
                    "Classification": "saturated-boundary-cut",
                    "ConflictSignals": ["Carry", "Propagate"],
                    "PriorityRelocationSignals": [
                        "Carry",
                        "Propagate",
                    ],
                    "RelocationSignals": ["Carry", "Propagate"],
                    "PairwiseIncompatibleEdges": [
                        ["Carry", "Propagate"],
                    ],
                },
            },
        ))
        self.assertEqual(
            SelectTransactionalEndpointRepairSignals(
                NarrowCut,
                InternalPinBankGeometryRepairActive=False,
                PinBankRepairSignals=frozenset(),
                CandidateIsTransactionalEndpointRepair=True,
                ParentTransactionalRepairSignals=frozenset({
                    "Carry",
                    "Generate",
                    "Propagate",
                }),
            ),
            frozenset({"Carry", "Propagate"}),
        )
        self.assertFalse(
            SelectTransactionalEndpointRepairSignals(
                NarrowCut,
                InternalPinBankGeometryRepairActive=False,
                PinBankRepairSignals=frozenset(),
                CandidateIsTransactionalEndpointRepair=True,
                ParentTransactionalRepairSignals=frozenset({
                    "Carry",
                    "Propagate",
                }),
            )
        )
        self.assertEqual(
            SelectTransactionalEndpointRepairSignals(
                NarrowCut,
                InternalPinBankGeometryRepairActive=False,
                PinBankRepairSignals=frozenset(),
                CandidateIsTransactionalEndpointRepair=True,
                ParentTransactionalRepairSignals=frozenset({
                    "Carry",
                    "Generate",
                }),
                AncestorTransactionalRepairSignalSets=(
                    frozenset({"Carry", "Propagate"}),
                ),
                AllowAncestorCutLocalRepair=True,
            ),
            frozenset({"Carry", "Propagate"}),
        )
        self.assertTrue(
            TransactionalCutRevisitsAncestorInterface(
                (
                    frozenset({"First", "Second"}),
                    frozenset({"Carry", "Propagate"}),
                ),
                frozenset({"Carry", "Propagate"}),
            )
        )
        self.assertTrue(
            TransactionalCutRevisitsAncestorInterface(
                tuple(
                    TransactionalCutRepairSignals(Cut)
                    for Cut in (NarrowCut,)
                ),
                frozenset({"Carry", "Propagate"}),
            )
        )
        self.assertFalse(
            TransactionalCutRevisitsAncestorInterface(
                (frozenset({"Carry", "Propagate"}),),
                frozenset({"Carry"}),
            )
        )
        self.assertTrue(
            TransactionalCutMayEscalateRepairClusterCount(
                frozenset({"Carry", "Propagate"}),
                frozenset({"Carry", "Propagate"}),
                1,
            )
        )
        self.assertFalse(
            TransactionalCutMayEscalateRepairClusterCount(
                frozenset({"Carry", "Propagate"}),
                frozenset({"Carry", "Propagate"}),
                2,
            )
        )
        self.assertEqual(
            SelectTransactionalRepairClusterCount(
                CandidateIsTransactionalEndpointRepair=False,
                RepeatedAccessDistinctTransactionalCut=True,
            ),
            1,
        )
        self.assertEqual(
            SelectTransactionalRepairClusterCount(
                CandidateIsTransactionalEndpointRepair=True,
                RepeatedAccessDistinctTransactionalCut=False,
            ),
            1,
        )
        self.assertEqual(
            SelectTransactionalRepairClusterCount(
                CandidateIsTransactionalEndpointRepair=False,
                RepeatedAccessDistinctTransactionalCut=False,
                ExactBoundaryPairCut=True,
                AllowInitialExactBoundaryCutRepair=True,
            ),
            2,
        )
        self.assertEqual(
            SelectTransactionalRepairClusterCount(
                CandidateIsTransactionalEndpointRepair=True,
                RepeatedAccessDistinctTransactionalCut=True,
            ),
            2,
        )
        self.assertEqual(
            SelectTransactionalRepairClusterCount(
                CandidateIsTransactionalEndpointRepair=True,
                RepeatedAccessDistinctTransactionalCut=True,
                CutStrictlyNarrowsParentInterface=True,
            ),
            1,
        )
        self.assertTrue(
            TransactionalCutRequiresCoordinatedClusterRepair(NarrowCut)
        )
        self.assertEqual(
            SelectTransactionalRepairClusterCount(
                CandidateIsTransactionalEndpointRepair=True,
                RepeatedAccessDistinctTransactionalCut=False,
                CutStrictlyNarrowsParentInterface=True,
                ExactBoundaryPairCut=True,
            ),
            2,
        )
        self.assertTrue(
            ShouldStopTransactionalRepairVariantGeneration(
                CandidateIsTransactionalEndpointRepair=True,
                RepairClusterCount=1,
                VariantPublished=True,
            )
        )
        self.assertFalse(
            ShouldStopTransactionalRepairVariantGeneration(
                CandidateIsTransactionalEndpointRepair=True,
                RepairClusterCount=2,
                VariantPublished=True,
            )
        )
        FourSignalCut = RoutingAssignmentCut.FromFailure(
            RoutingFailure(
                Reason=RoutingFailureReason.BoundaryEscapeInfeasible,
                Stage="ClusterBoundaryLease",
                AffectedNets=("Fourth", "First", "Third", "Second"),
                Diagnostics={
                    "ConflictGraph": {
                        "Classification": "saturated-boundary-cut",
                        "ConflictSignals": [
                            "Fourth",
                            "First",
                            "Third",
                            "Second",
                        ],
                        "PriorityRelocationSignals": [
                            "Fourth",
                            "First",
                            "Third",
                            "Second",
                        ],
                        "RelocationSignals": [
                            "Fourth",
                            "First",
                            "Third",
                            "Second",
                        ],
                    },
                },
            ),
            SourceCandidateId="transactional",
            MandatoryAccessOwnershipFingerprint="ownership-b",
        )
        self.assertTrue(
            ShouldAdmitPostDiversificationOwnershipRepair(
                FourSignalCut,
                TopologyRequiresJointPortfolio=True,
                CandidateIsTransactionalEndpointRepair=True,
                ParentTransactionalRepairClusterCount=2,
                CandidateDiversificationFixedLevel=1,
                ParentTransactionalRepairSignals=frozenset({
                    "ParentLeft",
                    "ParentRight",
                }),
                TransactionalRepairSignalHistory=(
                    frozenset({"ParentLeft", "ParentRight"}),
                ),
            )
        )
        self.assertEqual(
            SelectTransactionalEndpointRepairSignals(
                FourSignalCut,
                InternalPinBankGeometryRepairActive=False,
                PinBankRepairSignals=frozenset(),
                CandidateIsTransactionalEndpointRepair=True,
                ParentTransactionalRepairSignals=frozenset({
                    "ParentLeft",
                    "ParentRight",
                }),
                ParentTransactionalRepairClusterCount=2,
                AllowPostDiversificationOwnershipRepair=True,
            ),
            frozenset({"First", "Second", "Third", "Fourth"}),
        )
        for ChangedArguments in (
            {"TopologyRequiresJointPortfolio": False},
            {"CandidateDiversificationFixedLevel": 0},
            {"ParentTransactionalRepairClusterCount": 1},
            {
                "TransactionalRepairSignalHistory": (
                    frozenset({"First", "Second", "Third", "Fourth"}),
                ),
            },
        ):
            Arguments = {
                "TopologyRequiresJointPortfolio": True,
                "CandidateIsTransactionalEndpointRepair": True,
                "ParentTransactionalRepairClusterCount": 2,
                "CandidateDiversificationFixedLevel": 1,
                "ParentTransactionalRepairSignals": frozenset({
                    "ParentLeft",
                    "ParentRight",
                }),
                "TransactionalRepairSignalHistory": (
                    frozenset({"ParentLeft", "ParentRight"}),
                ),
                **ChangedArguments,
            }
            self.assertFalse(
                ShouldAdmitPostDiversificationOwnershipRepair(
                    FourSignalCut,
                    **Arguments,
                )
            )

    def testDenseRetainedLeaseProofSliceFundsUsefulProofAndPublication(
        self,
    ) -> None:
        self.assertEqual(
            DenseRetainedLeaseProofSliceSeconds(
                RemainingSeconds=37.0,
                RemainingRetainedCandidates=6,
            ),
            10.0,
        )
        self.assertEqual(
            DenseRetainedLeaseProofSliceSeconds(
                RemainingSeconds=7.0,
                RemainingRetainedCandidates=3,
            ),
            5.0,
        )
        self.assertEqual(
            DenseRetainedLeaseProofSliceSeconds(
                RemainingSeconds=30.0,
                RemainingRetainedCandidates=2,
            ),
            15.0,
        )
        self.assertEqual(
            DenseRetainedLeaseProofSliceSeconds(
                RemainingSeconds=90.0,
                RemainingRetainedCandidates=6,
                PrioritizeHigherOrderCutProof=True,
            ),
            40.0,
        )
        self.assertEqual(
            DenseRetainedLeaseProofSliceSeconds(
                RemainingSeconds=37.0,
                RemainingRetainedCandidates=6,
                PrioritizeHigherOrderCutProof=True,
            ),
            20.0,
        )
        with self.assertRaises(ValueError):
            DenseRetainedLeaseProofSliceSeconds(
                RemainingSeconds=30.0,
                RemainingRetainedCandidates=0,
            )

    def testDeferredPortfolioCutEvidenceIsTypedAndImmutable(self) -> None:
        Cut = BuildMandatoryCapacityCut("First", "Second")
        Evidence = DeferredActivePortfolioAssignmentCut(
            AssignmentCut=Cut,
            SourceCandidateId="Placement-source",
            FailureStage="ClusterBoundaryLease",
            Error=RoutingStageError(RoutingFailure(
                Reason=RoutingFailureReason.BoundaryEscapeInfeasible,
                Stage="ClusterBoundaryLease",
            )),
            Candidate=SimpleNamespace(),
        )
        self.assertEqual(
            Evidence.ToDictionary(),
            {
                "AssignmentCut": Cut.ToDictionary(),
                "SourceCandidateId": "Placement-source",
                "FailureStage": "ClusterBoundaryLease",
            },
        )
        with self.assertRaises(FrozenInstanceError):
            Evidence.SourceCandidateId = "changed"

    @staticmethod
    def BuildPortfolioRejection(
        CandidateIndex: int,
        OwnershipFingerprint: str,
        *Edges: tuple[str, str],
    ) -> MandatoryAccessPortfolioRejection:
        return MandatoryAccessPortfolioRejection(
            CandidateIndex=CandidateIndex,
            OwnershipFingerprint=OwnershipFingerprint,
            ConflictFingerprint=f"conflict-{CandidateIndex}",
            PairwiseConflictEdges=tuple(sorted(
                tuple(sorted(Edge)) for Edge in Edges
            )),
        )

    def testMandatoryAccessPairProjectionIsCanonicalAndNameAgnostic(
        self,
    ) -> None:
        Profile = SimpleNamespace(
            CrossConflicts=(
                (object(), ("Zulu", "Alpha", "Middle", "Alpha")),
                (object(), ("Middle", "Alpha")),
            ),
            SelfConflicts=((object(), ("Ignored",)),),
        )
        RenamedProfile = SimpleNamespace(
            CrossConflicts=(
                (object(), ("Net2", "Net0")),
                (object(), ("Net1", "Net2", "Net0", "Net0")),
            ),
            SelfConflicts=((object(), ("AlsoIgnored",)),),
        )

        self.assertEqual(
            BuildMandatoryAccessPairwiseEdges(Profile),
            (
                ("Alpha", "Middle"),
                ("Alpha", "Zulu"),
                ("Middle", "Zulu"),
            ),
        )
        self.assertEqual(
            BuildMandatoryAccessPairwiseEdges(RenamedProfile),
            (
                ("Net0", "Net1"),
                ("Net0", "Net2"),
                ("Net1", "Net2"),
            ),
        )

    def testCumulativeRepeatedCutRoutingFrontierRequiresActiveDistinctOwners(
        self,
    ) -> None:
        PersistentEdge = ("PersistentLeft", "PersistentRight")
        SameOwnerEdge = ("SameOwnerLeft", "SameOwnerRight")
        MissingOwnerEdge = ("MissingOwnerLeft", "MissingOwnerRight")
        InactiveEdge = ("InactiveLeft", "InactiveRight")
        History = (
            BuildOwnedPairCapacityCut(PersistentEdge, "owner-before"),
            BuildOwnedPairCapacityCut(
                tuple(reversed(PersistentEdge)),
                "owner-after",
            ),
            BuildOwnedPairCapacityCut(SameOwnerEdge, "same-owner"),
            BuildOwnedPairCapacityCut(
                tuple(reversed(SameOwnerEdge)),
                "same-owner",
            ),
            BuildOwnedPairCapacityCut(MissingOwnerEdge, ""),
            BuildOwnedPairCapacityCut(
                tuple(reversed(MissingOwnerEdge)),
                "known-owner",
            ),
            BuildOwnedPairCapacityCut(InactiveEdge, "inactive-before"),
            BuildOwnedPairCapacityCut(
                tuple(reversed(InactiveEdge)),
                "inactive-after",
            ),
        )
        ActiveConstraints = PlacementAssignmentConstraintSet(
            PairwiseConflictEdges=(
                tuple(reversed(PersistentEdge)),
                SameOwnerEdge,
                MissingOwnerEdge,
            ),
        )

        self.assertEqual(
            SelectCumulativeRepeatedAssignmentCutDiversificationSignals(
                History,
                ActiveConstraints,
            ),
            frozenset(PersistentEdge),
        )

    def testCumulativeRepeatedCutRoutingFrontierIsAdditiveAndRenamable(
        self,
    ) -> None:
        OlderEdge = ("OlderLeft", "OlderRight")
        NewestEdge = ("NewestLeft", "NewestRight")
        History = (
            BuildOwnedPairCapacityCut(OlderEdge, "older-owner-0"),
            BuildOwnedPairCapacityCut(NewestEdge, "newest-owner-0"),
            BuildOwnedPairCapacityCut(
                tuple(reversed(OlderEdge)),
                "older-owner-1",
            ),
            BuildOwnedPairCapacityCut(
                tuple(reversed(NewestEdge)),
                "newest-owner-1",
            ),
        )
        Constraints = PlacementAssignmentConstraintSet(
            PairwiseConflictEdges=(
                NewestEdge,
                OlderEdge,
            ),
        )
        Expected = frozenset((*OlderEdge, *NewestEdge))
        self.assertEqual(
            SelectCumulativeRepeatedAssignmentCutDiversificationSignals(
                tuple(reversed(History)),
                Constraints,
            ),
            Expected,
        )

        Rename = {
            Signal: f"Renamed{Index}"
            for Index, Signal in enumerate(sorted(Expected))
        }
        RenamedHistory = tuple(
            BuildOwnedPairCapacityCut(
                tuple(
                    Rename[Signal]
                    for Signal in Cut.PairwiseConflictEdges[0]
                ),
                Cut.MandatoryAccessOwnershipFingerprint,
            )
            for Cut in History
        )
        RenamedConstraints = PlacementAssignmentConstraintSet(
            PairwiseConflictEdges=tuple(
                tuple(Rename[Signal] for Signal in Edge)
                for Edge in reversed(Constraints.PairwiseConflictEdges)
            ),
        )
        self.assertEqual(
            SelectCumulativeRepeatedAssignmentCutDiversificationSignals(
                RenamedHistory,
                RenamedConstraints,
            ),
            frozenset(Rename[Signal] for Signal in Expected),
        )

    def testMandatoryAccessExpectedPortfolioIsExactOrderedAndBounded(
        self,
    ) -> None:
        Diagnostics = {
            "ExactLegalRetainedStates": [
                {"CandidateIndex": 5},
                {"CandidateIndex": 1},
                {"CandidateIndex": 3},
                {"CandidateIndex": 0},
                {"CandidateIndex": 2},
                {"CandidateIndex": 4},
                {"CandidateIndex": 6},
            ],
        }

        self.assertEqual(
            BuildMandatoryAccessPortfolioExpectedCandidateIndices(
                Diagnostics,
                SelectedCandidateIndex=0,
                RetainedCandidateLimit=6,
            ),
            (0, 1, 2, 3, 4, 5),
        )
        self.assertEqual(
            BuildMandatoryAccessPortfolioExpectedCandidateIndices(
                {
                    "ExactLegalRetainedStates": list(reversed(
                        Diagnostics["ExactLegalRetainedStates"]
                    ))
                },
                SelectedCandidateIndex=0,
                RetainedCandidateLimit=6,
            ),
            (0, 1, 2, 3, 4, 5),
        )
        self.assertEqual(
            BuildMandatoryAccessPortfolioExpectedCandidateIndices(
                Diagnostics,
                SelectedCandidateIndex=99,
                RetainedCandidateLimit=6,
            ),
            (),
        )

    def testMandatoryAccessPortfolioRejectsIncompleteAndDuplicateProof(
        self,
    ) -> None:
        Constraints = PlacementAssignmentConstraintSet()
        Incomplete = MandatoryAccessPortfolioEvidence(
            ExpectedCandidateIndices=(0, 1, 2),
            RejectionsByCandidateIndex={
                0: self.BuildPortfolioRejection(
                    0, "owner-0", ("A", "B")
                ),
                2: self.BuildPortfolioRejection(
                    2, "owner-2", ("C", "D")
                ),
            },
        )
        Duplicate = MandatoryAccessPortfolioEvidence(
            ExpectedCandidateIndices=(0, 1),
            RejectionsByCandidateIndex={
                0: self.BuildPortfolioRejection(
                    0, "same-owner", ("A", "B")
                ),
                1: self.BuildPortfolioRejection(
                    1, "same-owner", ("C", "D")
                ),
            },
        )
        Singleton = MandatoryAccessPortfolioEvidence(
            ExpectedCandidateIndices=(0,),
            RejectionsByCandidateIndex={
                0: self.BuildPortfolioRejection(
                    0, "only-owner", ("A", "B")
                ),
            },
        )

        IncompleteEvaluation = EvaluateCompleteMandatoryAccessPortfolio(
            Incomplete,
            Constraints,
        )
        DuplicateEvaluation = EvaluateCompleteMandatoryAccessPortfolio(
            Duplicate,
            Constraints,
        )
        SingletonEvaluation = EvaluateCompleteMandatoryAccessPortfolio(
            Singleton,
            Constraints,
        )

        self.assertEqual(IncompleteEvaluation.Verdict, "incomplete")
        self.assertEqual(
            IncompleteEvaluation.MissingCandidateIndices,
            (1,),
        )
        self.assertEqual(
            DuplicateEvaluation.Verdict,
            "access-not-distinct",
        )
        self.assertEqual(
            DuplicateEvaluation.DuplicateOwnershipFingerprints,
            ("same-owner",),
        )
        self.assertEqual(
            SingletonEvaluation.Verdict,
            "access-not-distinct",
        )

    def testMandatoryAccessPortfolioPromotionIsAdditiveAndOrderAgnostic(
        self,
    ) -> None:
        LiveCut = BuildHigherOrderCapacityCut(
            PrioritySignals=("Capacity0", "Capacity1", "Capacity2"),
            PairEdge=("Existing0", "Existing1"),
        )
        AssignmentCutHistory = [LiveCut]
        Constraints = PlacementAssignmentConstraintSet(
            PairwiseConflictEdges=(("Existing0", "Existing1"),),
            HigherOrderSignalSets=(
                ("Capacity0", "Capacity1", "Capacity2"),
            ),
        )
        Evidence = MandatoryAccessPortfolioEvidence(
            ExpectedCandidateIndices=(0, 1, 2),
            RejectionsByCandidateIndex={
                2: self.BuildPortfolioRejection(
                    2, "owner-2", ("Net3", "Net2")
                ),
                0: self.BuildPortfolioRejection(
                    0, "owner-0", ("Net1", "Net0")
                ),
                1: self.BuildPortfolioRejection(
                    1, "owner-1", ("Net2", "Net0")
                ),
            },
        )

        Evaluation = EvaluateCompleteMandatoryAccessPortfolio(
            Evidence,
            Constraints,
        )
        Promoted = AddMandatoryAccessPortfolioPairwiseConstraints(
            Constraints,
            Evaluation,
        )

        self.assertTrue(Evaluation.ShouldPromote)
        self.assertEqual(
            Evaluation.NewPairwiseConflictEdges,
            (
                ("Net0", "Net1"),
                ("Net0", "Net2"),
                ("Net2", "Net3"),
            ),
        )
        self.assertEqual(
            Promoted.PairwiseConflictEdges,
            (
                ("Existing0", "Existing1"),
                ("Net0", "Net1"),
                ("Net0", "Net2"),
                ("Net2", "Net3"),
            ),
        )
        self.assertEqual(
            Promoted.HigherOrderSignalSets,
            Constraints.HigherOrderSignalSets,
        )
        self.assertEqual(len(AssignmentCutHistory), 1)
        self.assertIs(AssignmentCutHistory[0], LiveCut)

    def testMandatoryAccessPortfolioDoesNotRelearnRepresentedPairs(
        self,
    ) -> None:
        Constraints = PlacementAssignmentConstraintSet(
            PairwiseConflictEdges=(
                ("A0", "B0"),
                ("B3", "NandNet4"),
            ),
            HigherOrderSignalSets=(("Cut0", "Cut1", "Cut2"),),
        )
        Evidence = MandatoryAccessPortfolioEvidence(
            ExpectedCandidateIndices=(0, 1),
            RejectionsByCandidateIndex={
                0: self.BuildPortfolioRejection(
                    0, "owner-0", ("B0", "A0")
                ),
                1: self.BuildPortfolioRejection(
                    1, "owner-1", ("NandNet4", "B3")
                ),
            },
        )

        Evaluation = EvaluateCompleteMandatoryAccessPortfolio(
            Evidence,
            Constraints,
        )

        self.assertEqual(Evaluation.Verdict, "already-represented")
        self.assertIs(
            AddMandatoryAccessPortfolioPairwiseConstraints(
                Constraints,
                Evaluation,
            ),
            Constraints,
        )

    def testMandatoryAccessPortfolioIdentitySeparatesMixedEpochs(
        self,
    ) -> None:
        Base = MandatoryAccessPortfolioIdentity(
            ExactScreenFingerprint="screen",
            SourceGenerator="row-beam-conflict-relocation",
            RoutingSpacing=5,
            RelocationVariant=0,
            AssignmentCutFingerprint="cut",
            AssignmentConstraintFingerprint="constraints",
            CoordinatedSignals=("A0", "B0"),
        )

        self.assertEqual(Base, replace(Base))
        for Different in (
            replace(Base, ExactScreenFingerprint="other-screen"),
            replace(Base, AssignmentCutFingerprint="other-cut"),
            replace(
                Base,
                AssignmentConstraintFingerprint="other-constraints",
            ),
            replace(Base, CoordinatedSignals=("A1", "B1")),
        ):
            self.assertNotEqual(Base, Different)

    def testMandatoryAccessPortfolioRecipeIdentityJoinsRetainedScreens(
        self,
    ) -> None:
        Base = MandatoryAccessPortfolioIdentity(
            ExactScreenFingerprint="primary-screen",
            SourceGenerator="row-beam-conflict-relocation",
            RoutingSpacing=5,
            RelocationVariant=2,
            AssignmentCutFingerprint="cut",
            AssignmentConstraintFingerprint="constraints",
            CoordinatedSignals=("Left", "Right"),
        )
        Sibling = replace(
            Base,
            ExactScreenFingerprint="sibling-materialization-screen",
        )
        self.assertEqual(
            BuildMandatoryAccessPortfolioRecipeIdentity(Base),
            BuildMandatoryAccessPortfolioRecipeIdentity(Sibling),
        )
        self.assertEqual(
            BuildMandatoryAccessPortfolioRecipeIdentity(
                Base,
                AssignmentConstraintFingerprint="promoted",
            ).AssignmentConstraintFingerprint,
            "promoted",
        )

    def testStrongMandatoryAccessRepairIsOneShotAndEvidenceGated(
        self,
    ) -> None:
        Promoted = MandatoryAccessPortfolioEvaluation(Verdict="promote")
        Represented = MandatoryAccessPortfolioEvaluation(
            Verdict="already-represented"
        )
        Incomplete = MandatoryAccessPortfolioEvaluation(
            Verdict="incomplete"
        )
        self.assertTrue(ShouldOpenStrongMandatoryAccessRepair(
            Promoted,
            IdentityStillCurrent=True,
            AlreadyConsumed=False,
        ))
        self.assertTrue(ShouldOpenStrongMandatoryAccessRepair(
            Represented,
            IdentityStillCurrent=True,
            AlreadyConsumed=False,
        ))
        self.assertFalse(ShouldOpenStrongMandatoryAccessRepair(
            Incomplete,
            IdentityStillCurrent=True,
            AlreadyConsumed=False,
        ))
        self.assertFalse(ShouldOpenStrongMandatoryAccessRepair(
            Promoted,
            IdentityStillCurrent=False,
            AlreadyConsumed=False,
        ))
        self.assertFalse(ShouldOpenStrongMandatoryAccessRepair(
            Promoted,
            IdentityStillCurrent=True,
            AlreadyConsumed=True,
        ))

    def testMandatoryAccessPortfolioIdentityDriftCannotPromote(
        self,
    ) -> None:
        CurrentCut = BuildMandatoryCapacityCut("A0", "B0")
        CurrentConstraints = (
            PlacementAssignmentConstraintSet().WithCut(CurrentCut)
        )
        Identity = MandatoryAccessPortfolioIdentity(
            ExactScreenFingerprint="screen",
            SourceGenerator="row-beam-conflict-relocation",
            RoutingSpacing=5,
            RelocationVariant=0,
            AssignmentCutFingerprint=CurrentCut.ConflictFingerprint,
            AssignmentConstraintFingerprint=(
                CurrentConstraints.Fingerprint
            ),
        )
        Evaluation = MandatoryAccessPortfolioEvaluation(
            Verdict="promote",
            PairwiseConflictEdges=(("C0", "D0"),),
            NewPairwiseConflictEdges=(("C0", "D0"),),
        )

        self.assertTrue(MandatoryAccessPortfolioIdentityMatchesCurrent(
            Identity,
            CurrentCut,
            CurrentConstraints,
        ))
        for StaleIdentity in (
            replace(Identity, AssignmentCutFingerprint="stale-cut"),
            replace(
                Identity,
                AssignmentConstraintFingerprint="stale-constraints",
            ),
        ):
            self.assertFalse(
                MandatoryAccessPortfolioIdentityMatchesCurrent(
                    StaleIdentity,
                    CurrentCut,
                    CurrentConstraints,
                )
            )
            CandidateConstraints = (
                AddMandatoryAccessPortfolioPairwiseConstraints(
                    CurrentConstraints,
                    Evaluation,
                )
                if MandatoryAccessPortfolioIdentityMatchesCurrent(
                    StaleIdentity,
                    CurrentCut,
                    CurrentConstraints,
                )
                else CurrentConstraints
            )
            self.assertIs(CandidateConstraints, CurrentConstraints)

    def testMandatoryAccessPlacementOnlyPortfolioIdentityIsCurrent(
        self,
    ) -> None:
        CurrentConstraints = PlacementAssignmentConstraintSet()
        Identity = MandatoryAccessPortfolioIdentity(
            ExactScreenFingerprint="screen",
            SourceGenerator="row-beam",
            RoutingSpacing=5,
            RelocationVariant=0,
            AssignmentCutFingerprint="",
            AssignmentConstraintFingerprint=(
                CurrentConstraints.Fingerprint
            ),
        )

        self.assertTrue(MandatoryAccessPortfolioIdentityMatchesCurrent(
            Identity,
            None,
            CurrentConstraints,
        ))
        self.assertFalse(MandatoryAccessPortfolioIdentityMatchesCurrent(
            replace(Identity, AssignmentCutFingerprint="stale-cut"),
            None,
            CurrentConstraints,
        ))
        self.assertFalse(MandatoryAccessPortfolioIdentityMatchesCurrent(
            replace(
                Identity,
                AssignmentConstraintFingerprint="stale-constraints",
            ),
            None,
            CurrentConstraints,
        ))

    """Verify that joint-orientation demand depends on topology, not scale."""

    def testHighFanoutWithoutReconvergenceDoesNotTrigger(self) -> None:
        Profile = BuildTopologyDemandProfile(
            BuildHighFanoutWithoutReconvergence()
        )

        self.assertIsInstance(Profile, TopologyDemandProfile)
        self.assertEqual(Profile.MaximumFanout, 4)
        self.assertEqual(Profile.MaximumReconvergentFanout, 0)
        self.assertEqual(Profile.ReconvergentCutCount, 0)
        self.assertEqual(Profile.QualifyingReconvergentCutCount, 0)
        self.assertFalse(Profile.EnableInitialJointOrientation)

    def testLowFanoutReconvergenceDoesNotTrigger(self) -> None:
        Profile = BuildTopologyDemandProfile(
            BuildReconvergentFanout(BranchCount=2)
        )

        self.assertEqual(Profile.MaximumFanout, 2)
        self.assertEqual(Profile.MaximumReconvergentFanout, 2)
        self.assertEqual(Profile.ReconvergentCutCount, 1)
        self.assertEqual(Profile.QualifyingReconvergentCutCount, 0)
        self.assertFalse(Profile.EnableInitialJointOrientation)
        self.assertEqual(
            Profile.ToDictionary()["QualifyingReconvergentCutCount"],
            0,
        )

    def testFanoutFourReconvergenceTriggersJointOrientation(self) -> None:
        Profile = BuildTopologyDemandProfile(
            BuildReconvergentFanout(BranchCount=4)
        )

        self.assertEqual(Profile.MaximumFanout, 4)
        self.assertEqual(Profile.MaximumReconvergentFanout, 4)
        self.assertEqual(Profile.ReconvergentCutCount, 1)
        self.assertEqual(Profile.QualifyingReconvergentCutCount, 1)
        self.assertTrue(Profile.EnableInitialJointOrientation)

    def testGateOrderAndInternalNamesDoNotChangeProfile(self) -> None:
        Original = BuildReconvergentFanout(BranchCount=4)
        Renamed = RenameInternalsAndReverseGates(Original)

        OriginalProfile = BuildTopologyDemandProfile(Original)
        RenamedProfile = BuildTopologyDemandProfile(Renamed)

        self.assertEqual(
            NumericProfile(OriginalProfile),
            NumericProfile(RenamedProfile),
        )
        self.assertEqual(
            OriginalProfile.EnableInitialJointOrientation,
            RenamedProfile.EnableInitialJointOrientation,
        )

    def testCurrentExamplesHaveExactTopologyDemandProfiles(self) -> None:
        Expected = {
            "FullAdder": (3, 2, 3, 3, 3, 6, 0, 4),
            "RippleCarryAdder4": (9, 5, 9, 3, 3, 24, 0, 13),
            "RippleCarryAdder8": (17, 9, 17, 3, 3, 48, 0, 25),
            "CarryLookaheadAdder4": (9, 5, 9, 6, 6, 27, 8, 18),
        }

        for ModuleName, ExpectedProfile in Expected.items():
            with self.subTest(ModuleName=ModuleName):
                Profile = BuildExampleProfile(ModuleName)
                self.assertEqual(
                    (
                        Profile.InputTerminalCount,
                        Profile.OutputTerminalCount,
                        Profile.MaximumTerminalBankDemand,
                        Profile.MaximumFanout,
                        Profile.MaximumReconvergentFanout,
                        Profile.ReconvergentCutCount,
                        Profile.QualifyingReconvergentCutCount,
                        Profile.PeakBoundaryDemand,
                    ),
                    ExpectedProfile,
                )
                self.assertEqual(
                    Profile.EnableInitialJointOrientation,
                    ModuleName == "CarryLookaheadAdder4",
                )

    def testCurrentExamplesSelectDistinctTypedPolicyPressure(self) -> None:
        Policy = LocalFirstPhysicalDesignPolicy
        Capacity = Policy.Organization.MaximumClusterEntrances
        PressureByModule = {
            ModuleName: BuildTopologyDemandPressureProfile(
                BuildExampleProfile(ModuleName),
                Capacity,
            )
            for ModuleName in (
                "FullAdder",
                "RippleCarryAdder4",
                "RippleCarryAdder8",
                "CarryLookaheadAdder4",
            )
        }
        Cla4Pressure = PressureByModule["CarryLookaheadAdder4"]
        Rca8Pressure = PressureByModule["RippleCarryAdder8"]

        self.assertTrue(Cla4Pressure.ReconvergentAccessPressure)
        self.assertFalse(Cla4Pressure.TerminalBankPressure)
        self.assertFalse(Cla4Pressure.DistributedBoundaryPressure)
        self.assertFalse(Cla4Pressure.ScaleGeometryPressure)
        self.assertFalse(Rca8Pressure.ReconvergentAccessPressure)
        self.assertTrue(Rca8Pressure.TerminalBankPressure)
        self.assertTrue(Rca8Pressure.DistributedBoundaryPressure)
        self.assertTrue(Rca8Pressure.ScaleGeometryPressure)
        for ModuleName in ("FullAdder", "RippleCarryAdder4"):
            with self.subTest(ModuleName=ModuleName):
                self.assertEqual(
                    PressureByModule[ModuleName].ToDictionary(),
                    {
                        "BoundaryCapacity": Capacity,
                        "ReconvergentAccessPressure": False,
                        "TerminalBankPressure": False,
                        "DistributedBoundaryPressure": False,
                        "ScaleGeometryPressure": False,
                    },
                )

    def testMandatoryCapacityCutInfersStructuredPair(self) -> None:
        Cut = BuildMandatoryCapacityCut("Left", "Right")

        self.assertEqual(
            BuildEffectiveAssignmentCutPairwiseEdges(Cut),
            (("Left", "Right"),),
        )
        self.assertTrue(
            RequiresStructuredAssignmentCutRelocation(Cut)
        )

    def testCumulativeConstraintsRetainHigherOrderAndLaterPair(
        self,
    ) -> None:
        HigherOrder = BuildHigherOrderCapacityCut(
            PrioritySignals=("A0", "B1", "B2"),
            PairEdge=("Generate0", "Propagate1"),
        )
        MandatoryPair = BuildMandatoryCapacityCut("A2", "Generate1")
        First = PlacementAssignmentConstraintSet().WithCut(HigherOrder)
        Combined = First.WithCut(MandatoryPair)

        self.assertEqual(
            Combined.PairwiseConflictEdges,
            (
                ("A2", "Generate1"),
                ("Generate0", "Propagate1"),
            ),
        )
        self.assertEqual(
            Combined.HigherOrderSignalSets,
            (("A0", "B1", "B2"),),
        )
        self.assertNotEqual(First.Fingerprint, Combined.Fingerprint)
        self.assertEqual(
            Combined.WithCut(HigherOrder),
            Combined,
            "replaying one exact cut must not duplicate its constraints",
        )
        self.assertEqual(
            (
                PlacementAssignmentConstraintSet()
                .WithCut(MandatoryPair)
                .WithCut(HigherOrder)
            ),
            Combined,
            "constraint identity must not depend on discovery order",
        )

    def testStructuredRepairIsNameAndOrderIndependent(self) -> None:
        Original = BuildNandModule(
            Name="Original",
            Inputs=("P", "Q", "R"),
            Outputs=("Y",),
            Gates=(
                Gate("Producer", GateKind.NAND, ["Shared"], ["P", "Q"]),
                Gate("Left", GateKind.NAND, ["LeftNet"], ["Shared", "R"]),
                Gate("Right", GateKind.NAND, ["RightNet"], ["Shared", "Q"]),
                Gate("Join", GateKind.NAND, ["Y"], ["LeftNet", "RightNet"]),
            ),
        )
        Renamed = BuildNandModule(
            Name="Renamed",
            Inputs=("Input0", "Input1", "Input2"),
            Outputs=("Output",),
            Gates=(
                Gate(
                    "Fourth",
                    GateKind.NAND,
                    ["Output"],
                    ["Branch0", "Branch1"],
                ),
                Gate(
                    "Third",
                    GateKind.NAND,
                    ["Branch1"],
                    ["Trunk", "Input1"],
                ),
                Gate(
                    "Second",
                    GateKind.NAND,
                    ["Branch0"],
                    ["Trunk", "Input2"],
                ),
                Gate(
                    "First",
                    GateKind.NAND,
                    ["Trunk"],
                    ["Input0", "Input1"],
                ),
            ),
        )
        OriginalClusters = (
            ("Producer",),
            ("Left",),
            ("Right",),
            ("Join",),
        )
        RenamedClusters = (
            ("First",),
            ("Second",),
            ("Third",),
            ("Fourth",),
        )
        OriginalCut = BuildMandatoryCapacityCut("Shared", "LeftNet")
        RenamedCut = BuildMandatoryCapacityCut("Trunk", "Branch0")
        OriginalConstraints = (
            PlacementAssignmentConstraintSet()
            .WithCut(BuildHigherOrderCapacityCut(
                PrioritySignals=("Shared", "LeftNet", "RightNet"),
                PairEdge=("LeftNet", "RightNet"),
            ))
            .WithCut(OriginalCut)
        )
        RenamedConstraints = (
            PlacementAssignmentConstraintSet()
            .WithCut(BuildHigherOrderCapacityCut(
                PrioritySignals=("Trunk", "Branch0", "Branch1"),
                PairEdge=("Branch0", "Branch1"),
            ))
            .WithCut(RenamedCut)
        )
        OriginalPriority = PrioritizeRelocationClusters(
            Original,
            OriginalClusters,
            frozenset(
                Signal
                for Signals in (
                    *OriginalConstraints.HigherOrderSignalSets,
                    *OriginalConstraints.PairwiseConflictEdges,
                )
                for Signal in Signals
            ),
        )
        RenamedPriority = PrioritizeRelocationClusters(
            Renamed,
            RenamedClusters,
            frozenset(
                Signal
                for Signals in (
                    *RenamedConstraints.HigherOrderSignalSets,
                    *RenamedConstraints.PairwiseConflictEdges,
                )
                for Signal in Signals
            ),
        )
        BaseSlots = {
            0: (0, 0),
            1: (1, 0),
            2: (0, 1),
            3: (1, 1),
        }

        self.assertEqual(OriginalPriority, RenamedPriority)
        OriginalRepair = RelocateClusterSlots(
            BaseSlots,
            2,
            OriginalPriority[:2],
        )
        RenamedRepair = RelocateClusterSlots(
            BaseSlots,
            2,
            RenamedPriority[:2],
        )
        self.assertEqual(OriginalRepair, RenamedRepair)
        self.assertNotEqual(OriginalRepair[0], BaseSlots)
        self.assertEqual(OriginalRepair[1], 2)

    def testRelocationVariantRestartsForEachExactCut(self) -> None:
        self.assertEqual(
            BuildPlacementRelocationVariant(
                RelocationGenerationCount=0,
                ReconvergentAccessPressure=True,
            ),
            2,
        )
        self.assertEqual(
            BuildPlacementRelocationVariant(
                RelocationGenerationCount=1,
                ReconvergentAccessPressure=True,
            ),
            12,
        )
        self.assertEqual(
            BuildPlacementRelocationVariant(
                RelocationGenerationCount=1,
                ReconvergentAccessPressure=False,
            ),
            3,
        )

    def testStructuredCutDisablesBroadBoundaryShell(self) -> None:
        Common = {
            "PackedMode": True,
            "ClusterIndex": 3,
            "BoundaryEscapeRelocationClusters": frozenset({3}),
            "PackedAccessRepairClusters": frozenset(),
            "RequiredRelocationSignals": frozenset({"Left", "Right"}),
            "RelocationVariant": 12,
            "RelocationPrioritySignalCount": 2,
            "LocalGeometryRepairClusters": frozenset({3}),
        }

        self.assertTrue(
            ShouldExpandBoundaryEscapeGeometry(
                **Common,
                StructuredAssignmentCutRelocation=False,
            )
        )
        self.assertFalse(
            ShouldExpandBoundaryEscapeGeometry(
                **Common,
                StructuredAssignmentCutRelocation=True,
            )
        )
        self.assertTrue(ShouldIncludeNearPortalPackedAccessRepair(
            RelocationVariant=0,
            EnableInternalPinBankGeometryRepair=True,
        ))
        self.assertTrue(ShouldIncludeNearPortalPackedAccessRepair(
            RelocationVariant=12,
            EnableInternalPinBankGeometryRepair=False,
        ))
        self.assertFalse(ShouldIncludeNearPortalPackedAccessRepair(
            RelocationVariant=3,
            EnableInternalPinBankGeometryRepair=False,
        ))

    def testJointOrderIsExactLexicographicDemandOrder(self) -> None:
        Common = {
            "MaximumFanout": 4,
            "ReconvergentCutCount": 1,
            "QualifyingReconvergentCutCount": 1,
            "MaximumReconvergentFanout": 4,
        }
        Profiles = (
            TopologyDemandProfile(
                **Common,
                PeakBoundaryDemand=1,
                MandatoryAccessConflictResources=1,
                GateFootprint=1,
                Hpwl=1,
                MandatoryAccessOwnershipFingerprint="conflict",
            ),
            TopologyDemandProfile(
                **Common,
                PeakBoundaryDemand=1,
                SingleCandidateBoundarySignals=((0, "First"),),
                GateFootprint=1,
                Hpwl=1,
                MandatoryAccessOwnershipFingerprint="scarce",
            ),
            TopologyDemandProfile(
                **Common,
                PeakBoundaryDemand=3,
                GateFootprint=1,
                Hpwl=1,
                MandatoryAccessOwnershipFingerprint="demand",
            ),
            TopologyDemandProfile(
                **Common,
                PeakBoundaryDemand=2,
                GateFootprint=20,
                Hpwl=1,
                MandatoryAccessOwnershipFingerprint="footprint",
            ),
            TopologyDemandProfile(
                **Common,
                PeakBoundaryDemand=2,
                GateFootprint=10,
                Hpwl=20,
                MandatoryAccessOwnershipFingerprint="hpwl",
            ),
            TopologyDemandProfile(
                **Common,
                PeakBoundaryDemand=2,
                GateFootprint=10,
                Hpwl=10,
                MandatoryAccessOwnershipFingerprint="best",
            ),
        )

        self.assertEqual(
            [
                Profile.MandatoryAccessOwnershipFingerprint
                for Profile in sorted(
                    Profiles,
                    key=lambda Profile: Profile.JointOrderKey,
                )
            ],
            [
                "best",
                "hpwl",
                "footprint",
                "demand",
                "scarce",
                "conflict",
            ],
        )

    def testMandatoryAccessTriggerLatchesAcrossRelocationRequests(
        self,
    ) -> None:
        Demand = TopologyDemandProfile(
            MaximumFanout=0,
            ReconvergentCutCount=0,
            QualifyingReconvergentCutCount=0,
            MaximumReconvergentFanout=0,
            PeakBoundaryDemand=0,
        )
        Request = PlacementGenerationRequest(
            SourceGenerator="row-beam-conflict-relocation",
            RoutingSpacing=3,
            PackingPolicy=replace(
                LocalFirstPhysicalDesignPolicy.NandPacking,
                EnableJointClusterOrientation=False,
            ),
        )

        Triggered = ResolveJointPlacementPortfolioTrigger(
            False,
            Demand,
            MandatoryAccessConflictObserved=True,
        )
        Relocation = ApplyJointPlacementPortfolioTrigger(
            Request,
            Triggered,
        )

        self.assertTrue(Triggered)
        self.assertTrue(
            Relocation.PackingPolicy.EnableJointClusterOrientation
        )
        self.assertTrue(
            ResolveJointPlacementPortfolioTrigger(Triggered, Demand)
        )

    def testStructuralHigherOrderCutFingerprintIsRenameIndependent(
        self,
    ) -> None:
        def Cut(Left: str, Right: str) -> RoutingAssignmentCut:
            Value = RoutingAssignmentCut.FromFailure(
                RoutingFailure(
                    Reason=RoutingFailureReason.TrackAssignmentConflict,
                    Stage="TrackAssignment",
                    Diagnostics={
                        "ConflictFingerprint": "dynamic-resource-state",
                        "ConflictGraph": {
                            "Classification": (
                                "higher-order-placement-conflict"
                            ),
                            "ConflictSignals": [Left, Right],
                            "NoCandidateSignals": [],
                            "PairwiseIncompatibleEdges": [],
                        },
                    },
                ),
                SourceCandidateId="Placement",
                MandatoryAccessOwnershipFingerprint="access-topology",
            )
            self.assertIsNotNone(Value)
            assert Value is not None
            return Value

        Original = Cut("OriginalLeft", "OriginalRight")
        Renamed = Cut("RenamedLeft", "RenamedRight")

        self.assertEqual(
            BuildStructuralHigherOrderAssignmentCutFingerprint(
                Original,
                {
                    "OriginalLeft": "topology-left",
                    "OriginalRight": "topology-right",
                },
            ),
            BuildStructuralHigherOrderAssignmentCutFingerprint(
                Renamed,
                {
                    "RenamedLeft": "topology-left",
                    "RenamedRight": "topology-right",
                },
            ),
        )

    def testPendingJointPlacementStateIsTypedAndImmutable(self) -> None:
        Request = PlacementGenerationRequest(
            SourceGenerator="row-beam",
            RoutingSpacing=2,
            PackingPolicy=object(),
        )
        AssignmentCut = RoutingAssignmentCut.FromFailure(
            RoutingFailure(
                Reason=RoutingFailureReason.TrackAssignmentConflict,
                Stage="TrackAssignment",
                Diagnostics={
                    "ConflictFingerprint": "cut-fingerprint",
                    "ConflictGraph": {
                        "Classification": "higher-order-placement-conflict",
                        "ConflictSignals": ["First", "Second"],
                        "PairwiseIncompatibleEdges": [
                            ["First", "Second"],
                        ],
                    },
                },
            ),
            SourceCandidateId="Placement-source",
            MandatoryAccessOwnershipFingerprint="access-source",
        )
        self.assertIsNotNone(AssignmentCut)
        State = PendingJointPlacementState(
            Request=Request,
            CandidateIndex=3,
            RelocationVariant=4,
            RoutingSpacing=5,
            RelocationSignals=frozenset({"Second", "First"}),
            RelocationPrioritySignals=frozenset({"Second"}),
            RequiredRelocationSignals=frozenset({"First"}),
            AssignmentCut=AssignmentCut,
        )

        self.assertEqual(State.CandidateIndex, 3)
        self.assertEqual(
            State.ToDictionary(),
            {
                "SourceGenerator": "row-beam",
                "CandidateIndex": 3,
                "RelocationVariant": 4,
                "RoutingSpacing": 5,
                "RelocationSignals": ["First", "Second"],
                "RelocationPrioritySignals": ["Second"],
                "RequiredRelocationSignals": ["First"],
            "AssignmentCut": AssignmentCut.ToDictionary(),
                "AssignmentConstraints": (
                    PlacementAssignmentConstraintSet().ToDictionary()
                ),
                "CoordinatedCandidateDiversificationSignals": [],
                "EnableClusterLocalRouteReuse": False,
                "IsPostPinBankRepairEpoch": False,
                "EnableInternalPinBankGeometryRepair": False,
            },
        )
        with self.assertRaises(FrozenInstanceError):
            State.CandidateIndex = 4

    def testPendingJointPortfolioIdentityPartitionsEpochsAndDeduplicatesStates(
        self,
    ) -> None:
        Request = PlacementGenerationRequest(
            SourceGenerator="row-beam-conflict-relocation",
            RoutingSpacing=5,
            PackingPolicy="packing-profile",
        )
        FirstCut = BuildMandatoryCapacityCut("A2", "Generate1")
        SecondCut = BuildMandatoryCapacityCut("B1", "NandNet3")
        FirstConstraints = (
            PlacementAssignmentConstraintSet().WithCut(FirstCut)
        )
        SecondConstraints = FirstConstraints.WithCut(SecondCut)
        First = PendingJointPlacementState(
            Request=Request,
            CandidateIndex=1,
            RelocationVariant=2,
            RoutingSpacing=5,
            RelocationSignals=frozenset({"A2", "Generate1"}),
            RelocationPrioritySignals=frozenset({"A2"}),
            RequiredRelocationSignals=frozenset({"Generate1"}),
            AssignmentCut=FirstCut,
            AssignmentConstraints=FirstConstraints,
        )
        Sibling = replace(First, CandidateIndex=5)
        Duplicate = replace(First)
        NewConstraintEpoch = replace(
            First,
            AssignmentConstraints=SecondConstraints,
        )
        NewRelocationVariant = replace(
            First,
            RelocationVariant=3,
        )

        FirstIdentity = BuildPendingJointPlacementPortfolioIdentity(First)
        FirstFingerprint = (
            BuildPendingJointPlacementPortfolioFingerprint(First)
        )
        self.assertEqual(
            FirstIdentity,
            BuildPendingJointPlacementPortfolioIdentity(Sibling),
        )
        self.assertEqual(
            FirstFingerprint,
            BuildPendingJointPlacementPortfolioFingerprint(Sibling),
        )
        self.assertEqual(
            BuildPendingJointPlacementStateKey(First),
            BuildPendingJointPlacementStateKey(Duplicate),
        )
        self.assertNotEqual(
            BuildPendingJointPlacementStateKey(First),
            BuildPendingJointPlacementStateKey(Sibling),
        )
        self.assertNotEqual(
            FirstIdentity,
            BuildPendingJointPlacementPortfolioIdentity(
                NewConstraintEpoch
            ),
        )
        self.assertNotEqual(
            FirstFingerprint,
            BuildPendingJointPlacementPortfolioFingerprint(
                NewConstraintEpoch
            ),
        )
        self.assertNotEqual(
            FirstIdentity,
            BuildPendingJointPlacementPortfolioIdentity(
                NewRelocationVariant
            ),
        )

    def testRoutingFloorRetainsUntouchedExactPortfolioSuffixFirst(
        self,
    ) -> None:
        Request = PlacementGenerationRequest(
            SourceGenerator="row-beam-conflict-relocation",
            RoutingSpacing=5,
            PackingPolicy="packing-profile",
        )
        First = PendingJointPlacementState(
            Request=Request,
            CandidateIndex=0,
            RelocationVariant=1,
            RoutingSpacing=5,
            RelocationSignals=frozenset(),
            RelocationPrioritySignals=frozenset(),
            RequiredRelocationSignals=frozenset(),
        )
        DeferredFirst = replace(First, CandidateIndex=2)
        DeferredSecond = replace(First, CandidateIndex=3)
        UnrelatedExisting = replace(
            First,
            CandidateIndex=4,
            RelocationVariant=2,
        )

        Retained = RetainUnmaterializedJointPlacementStates(
            (
                DeferredSecond,
                UnrelatedExisting,
            ),
            (
                DeferredFirst,
                DeferredSecond,
            ),
            MaterializedStateKeys=(
                BuildPendingJointPlacementStateKey(First),
            ),
        )

        self.assertEqual(
            [
                (State.RelocationVariant, State.CandidateIndex)
                for State in Retained
            ],
            [
                (1, 2),
                (1, 3),
                (2, 4),
            ],
        )

    def testMandatoryAccessPreScreenTargetsOnlyMonotonePackedEpochs(
        self,
    ) -> None:
        Common = {
            "SourceGenerator": "row-beam-conflict-relocation",
            "PackingEnabled": True,
            "TopologyRequiresJointPortfolio": True,
        }
        self.assertTrue(ShouldUseMandatoryAccessPreScreen(
            **Common,
            JointOrientationEnabled=True,
            HasRelocationSignals=True,
            HasAssignmentCut=True,
            AssignmentConstraintsActive=True,
        ))
        self.assertFalse(ShouldUseMandatoryAccessPreScreen(
            **Common,
            JointOrientationEnabled=True,
            HasRelocationSignals=False,
            HasAssignmentCut=False,
            AssignmentConstraintsActive=False,
        ))
        self.assertTrue(ShouldUseMandatoryAccessPreScreen(
            **Common,
            JointOrientationEnabled=False,
            HasRelocationSignals=False,
            HasAssignmentCut=False,
            AssignmentConstraintsActive=False,
        ))
        self.assertFalse(ShouldUseMandatoryAccessPreScreen(
            **{
                **Common,
                "SourceGenerator": "unpacked",
            },
            JointOrientationEnabled=True,
            HasRelocationSignals=True,
            HasAssignmentCut=True,
            AssignmentConstraintsActive=True,
        ))

    def testPlacementDedupIncludesMandatoryAccessOwnership(self) -> None:
        Placement = PcbPlacement(
            Placed=PlacedDesign(Module=None, PlacedGates=[]),
            Clusters=(),
            SignalOrder=(),
            LayerCount=0,
        )

        self.assertNotEqual(
            BuildPlacementFingerprint(Placement, "access-topology-a"),
            BuildPlacementFingerprint(Placement, "access-topology-b"),
        )
        self.assertEqual(
            BuildPlacementFingerprint(Placement, "access-topology-a"),
            BuildPlacementFingerprint(Placement, "access-topology-a"),
        )
        self.assertNotEqual(
            BuildPlacementRetentionFingerprint(
                Placement,
                "access-topology-a",
            ),
            BuildPlacementRetentionFingerprint(
                Placement,
                "access-topology-b",
            ),
        )
        self.assertTrue(TransactionalEndpointRepairIdentityIsFresh(
            "new-placement",
            "new-retention",
            {"old-placement"},
            {"old-retention"},
        ))
        self.assertFalse(TransactionalEndpointRepairIdentityIsFresh(
            "old-placement",
            "new-retention",
            {"old-placement"},
            {"old-retention"},
        ))
        self.assertFalse(TransactionalEndpointRepairIdentityIsFresh(
            "new-placement",
            "old-retention",
            {"old-placement"},
            {"old-retention"},
        ))

    def testPlacementRetentionDedupNormalizesOnlyWholeTranslation(
        self,
    ) -> None:
        def Placement(
            *,
            Offset: tuple[int, int, int] = (0, 0, 0),
            SecondRelativeX: int = 6,
            SecondRotation: int = 0,
            SecondMirrorX: bool = False,
            ClaimRelativeX: int = 1,
            Rename: bool = False,
            Reverse: bool = False,
        ) -> PcbPlacement:
            OffsetX, OffsetY, OffsetZ = Offset
            Gates = [
                SimpleNamespace(
                    Name="RenamedSecond" if Rename else "Second",
                    Kind="NAND",
                    X=OffsetX + SecondRelativeX,
                    Y=OffsetY + 1,
                    Z=OffsetZ + 2,
                    Rotation=SecondRotation,
                    MirrorX=SecondMirrorX,
                ),
                SimpleNamespace(
                    Name="RenamedFirst" if Rename else "First",
                    Kind="NAND",
                    X=OffsetX,
                    Y=OffsetY + 1,
                    Z=OffsetZ,
                    Rotation=0,
                    MirrorX=False,
                ),
            ]
            if Reverse:
                Gates.reverse()
            Claim = SimpleNamespace(
                Signal="RenamedClaim" if Rename else "Claim",
                ClusterId=99 if Rename else 0,
                Nodes=frozenset({
                    (
                        OffsetX + ClaimRelativeX,
                        OffsetY + 1,
                        OffsetZ,
                    ),
                    (
                        OffsetX + ClaimRelativeX + 1,
                        OffsetY + 1,
                        OffsetZ,
                    ),
                }),
            )
            return PcbPlacement(
                Placed=PlacedDesign(
                    Module=None,
                    PlacedGates=Gates,
                    LocalRouteClaims=(Claim,),
                ),
                Clusters=(),
                SignalOrder=(),
                LayerCount=2,
            )

        Baseline = Placement()
        TranslatedRenamed = Placement(
            Offset=(11, 3, 7),
            Rename=True,
            Reverse=True,
        )
        BaselineRetention = BuildPlacementRetentionFingerprint(
            Baseline,
            "anonymous-access-topology",
        )

        self.assertNotEqual(
            BuildPlacementFingerprint(Baseline),
            BuildPlacementFingerprint(TranslatedRenamed),
        )
        self.assertEqual(
            BaselineRetention,
            BuildPlacementRetentionFingerprint(
                TranslatedRenamed,
                "anonymous-access-topology",
            ),
        )
        EmptyTopology = TopologyDemandProfile(
            MaximumFanout=0,
            ReconvergentCutCount=0,
            QualifyingReconvergentCutCount=0,
            MaximumReconvergentFanout=0,
            PeakBoundaryDemand=0,
        )
        self.assertEqual(
            MeasurePlacementTopologyDemand(
                EmptyTopology,
                Baseline,
            ).MandatoryAccessOwnershipFingerprint,
            MeasurePlacementTopologyDemand(
                EmptyTopology,
                TranslatedRenamed,
            ).MandatoryAccessOwnershipFingerprint,
        )
        for Distinct in (
            Placement(SecondRelativeX=7),
            Placement(SecondRotation=90),
            Placement(SecondMirrorX=True),
            Placement(ClaimRelativeX=2),
        ):
            self.assertNotEqual(
                BaselineRetention,
                BuildPlacementRetentionFingerprint(
                    Distinct,
                    "anonymous-access-topology",
                ),
            )
        ClaimDistinct = Placement(ClaimRelativeX=2)
        self.assertEqual(
            BuildPlacementFingerprint(
                Baseline,
                "anonymous-access-topology",
                IncludeLocalClaims=False,
            ),
            BuildPlacementFingerprint(
                ClaimDistinct,
                "anonymous-access-topology",
                IncludeLocalClaims=False,
            ),
        )
        self.assertEqual(
            BuildPlacementRetentionFingerprint(
                Baseline,
                "anonymous-access-topology",
                IncludeLocalClaims=False,
            ),
            BuildPlacementRetentionFingerprint(
                ClaimDistinct,
                "anonymous-access-topology",
                IncludeLocalClaims=False,
            ),
        )
        self.assertNotEqual(
            BuildPlacementRetentionFingerprint(
                Baseline,
                "anonymous-access-topology",
                IncludeLocalClaims=False,
            ),
            BuildPlacementRetentionFingerprint(
                Placement(SecondRotation=90),
                "anonymous-access-topology",
                IncludeLocalClaims=False,
            ),
        )
        self.assertNotEqual(
            BaselineRetention,
            BuildPlacementRetentionFingerprint(
                Baseline,
                "different-access-topology",
            ),
        )

    def testJointSearchCacheDistinguishesCompleteAssignmentCuts(self) -> None:
        def AssignmentCutFor(Edge: tuple[str, str]):
            Cut = RoutingAssignmentCut.FromFailure(
                RoutingFailure(
                    Reason=RoutingFailureReason.TrackAssignmentConflict,
                    Stage="TrackAssignment",
                    Diagnostics={
                        "ConflictGraph": {
                            "Classification": (
                                "higher-order-placement-conflict"
                            ),
                            "ConflictSignals": ["A", "B", "C"],
                            "RelocationSignals": ["A", "B", "C"],
                            "PairwiseIncompatibleEdges": [list(Edge)],
                        },
                    },
                ),
                MandatoryAccessOwnershipFingerprint="same-access",
            )
            self.assertIsNotNone(Cut)
            return Cut

        Module = object()
        Common = (
            Module,
            (("GateA",), ("GateB",)),
            {0: (0, 0), 1: (1, 0)},
            8,
            4,
            2,
        )
        First = AssignmentCutFor(("A", "B"))
        Second = AssignmentCutFor(("A", "C"))

        self.assertEqual(
            First.RelocationSignals,
            Second.RelocationSignals,
        )
        self.assertNotEqual(
            First.ConflictFingerprint,
            Second.ConflictFingerprint,
        )
        self.assertNotEqual(
            BuildJointPlacementSearchCacheKey(*Common, First),
            BuildJointPlacementSearchCacheKey(*Common, Second),
        )
        self.assertEqual(
            BuildJointPlacementSearchCacheKey(*Common, First),
            BuildJointPlacementSearchCacheKey(*Common, First),
        )
        Constraints = (
            PlacementAssignmentConstraintSet().WithCut(Second)
        )
        self.assertNotEqual(
            BuildJointPlacementSearchCacheKey(*Common, First),
            BuildJointPlacementSearchCacheKey(
                *Common,
                First,
                Constraints,
            ),
        )
        self.assertEqual(
            First.ConflictFingerprint,
            First.ConflictFingerprint,
            "cumulative cache identity must not rewrite the current cut",
        )
        self.assertNotEqual(
            BuildJointPlacementSearchCacheKey(
                *Common,
                First,
                FocusedOptimizationClusters=frozenset({0}),
            ),
            BuildJointPlacementSearchCacheKey(
                *Common,
                First,
                FocusedOptimizationClusters=frozenset({1}),
            ),
        )

    def testPendingStateRequiresCurrentCutAndConstraintIdentity(
        self,
    ) -> None:
        Request = PlacementGenerationRequest(
            SourceGenerator="row-beam-conflict-relocation",
            RoutingSpacing=5,
            PackingPolicy=object(),
        )
        CurrentCut = BuildMandatoryCapacityCut("A2", "Generate1")
        PriorCut = BuildHigherOrderCapacityCut(
            PrioritySignals=("A0", "B1", "B2"),
            PairEdge=("Generate0", "Propagate1"),
        )
        OldConstraints = PlacementAssignmentConstraintSet().WithCut(
            CurrentCut
        )
        CurrentConstraints = OldConstraints.WithCut(PriorCut)
        OldState = PendingJointPlacementState(
            Request=Request,
            CandidateIndex=1,
            RelocationVariant=2,
            RoutingSpacing=5,
            RelocationSignals=frozenset({"A2", "Generate1"}),
            RelocationPrioritySignals=frozenset({"A2", "Generate1"}),
            RequiredRelocationSignals=frozenset({"A2", "Generate1"}),
            AssignmentCut=CurrentCut,
            AssignmentConstraints=OldConstraints,
        )
        CurrentState = replace(
            OldState,
            AssignmentConstraints=CurrentConstraints,
        )

        self.assertFalse(PendingJointPlacementStateMatchesIdentity(
            OldState,
            CurrentCut.ConflictFingerprint,
            CurrentConstraints.Fingerprint,
        ))
        self.assertTrue(PendingJointPlacementStateMatchesIdentity(
            CurrentState,
            CurrentCut.ConflictFingerprint,
            CurrentConstraints.Fingerprint,
        ))
        self.assertTrue(HasCurrentPendingJointPlacementState(
            (OldState, CurrentState),
            CurrentCut.ConflictFingerprint,
            CurrentConstraints.Fingerprint,
        ))
        self.assertFalse(HasCurrentPendingJointPlacementState(
            (OldState,),
            CurrentCut.ConflictFingerprint,
            CurrentConstraints.Fingerprint,
        ))
        self.assertFalse(HasCurrentPendingJointPlacementState(
            (CurrentState,),
            "",
            CurrentConstraints.Fingerprint,
        ))

    def testStaleMaterializedCandidateIsExcludedByConstraintEpoch(
        self,
    ) -> None:
        CurrentCut = BuildMandatoryCapacityCut("A2", "Generate1")
        EmptyConstraints = PlacementAssignmentConstraintSet()
        CurrentConstraints = EmptyConstraints.WithCut(CurrentCut)

        def Candidate(
            ConstraintFingerprint: str,
            CutFingerprint: str,
        ) -> PcbPlacementCandidate:
            return PcbPlacementCandidate(
                CandidateId="Placement-test",
                SourceGenerator="row-beam",
                RoutingSpacing=5,
                PlacementFingerprint="placement-fingerprint",
                FeedbackScore=(),
                BoundaryOverflow=0,
                PinScarcityCount=0,
                GuideOverflowPeak=0,
                GuideOverflowCells=0,
                PinEscapeConflictCount=0,
                EstimatedGlobalExtensionNodes=0,
                EstimatedGlobalExtensionNets=0,
                PreOwnedNodeCount=0,
                Placement=object(),
                JointPortfolioCandidate=True,
                AssignmentCutFingerprint=CutFingerprint,
                AssignmentConstraintFingerprint=ConstraintFingerprint,
            )

        InitialCandidate = Candidate(
            EmptyConstraints.Fingerprint,
            "",
        )
        CurrentCandidate = Candidate(
            CurrentConstraints.Fingerprint,
            CurrentCut.ConflictFingerprint,
        )

        self.assertFalse(
            PlacementAssignmentConstraintsAreActive(EmptyConstraints)
        )
        self.assertTrue(PlacementCandidateMatchesConstraintIdentity(
            InitialCandidate,
            EmptyConstraints.Fingerprint,
            False,
        ))
        self.assertTrue(PlacementCandidateMatchesConstraintIdentity(
            CurrentCandidate,
            EmptyConstraints.Fingerprint,
            False,
        ))
        self.assertTrue(
            PlacementAssignmentConstraintsAreActive(CurrentConstraints)
        )
        self.assertFalse(PlacementCandidateMatchesConstraintIdentity(
            InitialCandidate,
            CurrentConstraints.Fingerprint,
            True,
        ))
        self.assertTrue(PlacementCandidateMatchesConstraintIdentity(
            replace(
                InitialCandidate,
                SourceGenerator="primary-unpacked",
                JointPortfolioCandidate=False,
            ),
            CurrentConstraints.Fingerprint,
            True,
        ))
        self.assertTrue(PlacementCandidateMatchesConstraintIdentity(
            CurrentCandidate,
            CurrentConstraints.Fingerprint,
            True,
        ))

    def testCurrentMaterializedJointSiblingPrecedesRegeneration(
        self,
    ) -> None:
        CurrentCut = BuildMandatoryCapacityCut("A2", "Generate1")
        CurrentConstraints = (
            PlacementAssignmentConstraintSet().WithCut(CurrentCut)
        )

        def Candidate(
            PlacementFingerprint: str,
            CutFingerprint: str,
            ConstraintFingerprint: str,
            *,
            Joint: bool = True,
        ) -> PcbPlacementCandidate:
            return PcbPlacementCandidate(
                CandidateId=f"Placement-{PlacementFingerprint}",
                SourceGenerator="row-beam-conflict-relocation",
                RoutingSpacing=5,
                PlacementFingerprint=PlacementFingerprint,
                FeedbackScore=(),
                BoundaryOverflow=0,
                PinScarcityCount=0,
                GuideOverflowPeak=0,
                GuideOverflowCells=0,
                PinEscapeConflictCount=0,
                EstimatedGlobalExtensionNodes=0,
                EstimatedGlobalExtensionNets=0,
                PreOwnedNodeCount=0,
                Placement=object(),
                JointPortfolioCandidate=Joint,
                AssignmentCutFingerprint=CutFingerprint,
                AssignmentConstraintFingerprint=ConstraintFingerprint,
            )

        Current = Candidate(
            "current",
            CurrentCut.ConflictFingerprint,
            CurrentConstraints.Fingerprint,
        )
        StaleCut = Candidate(
            "stale-cut",
            "stale-cut",
            CurrentConstraints.Fingerprint,
        )
        StaleConstraints = Candidate(
            "stale-constraints",
            CurrentCut.ConflictFingerprint,
            "stale-constraints",
        )
        Broad = Candidate(
            "broad",
            CurrentCut.ConflictFingerprint,
            CurrentConstraints.Fingerprint,
            Joint=False,
        )

        self.assertTrue(HasCurrentMaterializedJointPlacementCandidate(
            (StaleCut, Current, StaleConstraints, Broad),
            set(),
            CurrentCut.ConflictFingerprint,
            CurrentConstraints.Fingerprint,
        ))
        self.assertFalse(HasCurrentMaterializedJointPlacementCandidate(
            (StaleCut, Current, StaleConstraints, Broad),
            {"current"},
            CurrentCut.ConflictFingerprint,
            CurrentConstraints.Fingerprint,
        ))
        self.assertFalse(HasCurrentMaterializedJointPlacementCandidate(
            (Current,),
            set(),
            "",
            CurrentConstraints.Fingerprint,
        ))

    def testActivePortfolioIdentitySurvivesLaterConstraintEpoch(
        self,
    ) -> None:
        Active = replace(
            PcbPlacementCandidate(
                CandidateId="Placement-active",
                SourceGenerator="row-beam-conflict-relocation",
                RoutingSpacing=5,
                PlacementFingerprint="active",
                FeedbackScore=(),
                BoundaryOverflow=0,
                PinScarcityCount=0,
                GuideOverflowPeak=0,
                GuideOverflowCells=0,
                PinEscapeConflictCount=0,
                EstimatedGlobalExtensionNodes=0,
                EstimatedGlobalExtensionNets=0,
                PreOwnedNodeCount=0,
                Placement=object(),
                JointPortfolioCandidate=True,
            ),
            AssignmentConstraintFingerprint="prior-constraints",
            JointPortfolioIdentityFingerprint="active-portfolio",
        )
        Unrelated = replace(
            Active,
            CandidateId="Placement-unrelated",
            PlacementFingerprint="unrelated",
            JointPortfolioIdentityFingerprint="other-portfolio",
        )

        self.assertTrue(PlacementCandidateMatchesActiveJointPortfolio(
            Active,
            "active-portfolio",
        ))
        self.assertTrue(PlacementCandidateMatchesActiveJointPortfolio(
            replace(
                Active,
                SourceGenerator=(
                    "transactional-cluster-endpoint-repair"
                ),
            ),
            "active-portfolio",
        ))
        self.assertFalse(PlacementCandidateMatchesActiveJointPortfolio(
            Unrelated,
            "active-portfolio",
        ))
        self.assertTrue(HasActiveMaterializedJointPlacementCandidate(
            (Unrelated, Active),
            set(),
            "active-portfolio",
        ))
        self.assertFalse(HasActiveMaterializedJointPlacementCandidate(
            (Unrelated, Active),
            {"active"},
            "active-portfolio",
        ))

    def testTerminalActiveConstraintRefreshRequiresOneStaleSibling(
        self,
    ) -> None:
        Arguments = {
            "ActivePendingCount": 1,
            "CandidateSourceGenerator": (
                "row-beam-conflict-relocation"
            ),
            "CandidateMatchesActivePortfolio": True,
            "CandidateConstraintFingerprint": "prior",
            "CurrentConstraintFingerprint": "current",
            "RefreshAlreadyPerformed": False,
        }

        self.assertTrue(
            ShouldRefreshTerminalActiveJointPlacementConstraintEpoch(
                **Arguments
            )
        )
        for Override in (
            {"ActivePendingCount": 2},
            {"CandidateSourceGenerator": "row-beam"},
            {"CandidateMatchesActivePortfolio": False},
            {"CandidateConstraintFingerprint": "current"},
            {"CurrentConstraintFingerprint": ""},
            {"RefreshAlreadyPerformed": True},
        ):
            with self.subTest(Override=Override):
                self.assertFalse(
                    ShouldRefreshTerminalActiveJointPlacementConstraintEpoch(
                        **{**Arguments, **Override}
                    )
                )

    def testCurrentPendingConstraintPrecedesOldMaterializedEpoch(
        self,
    ) -> None:
        PriorCut = BuildHigherOrderCapacityCut(
            PrioritySignals=("A0", "B1", "B2"),
            PairEdge=("Generate0", "Propagate1"),
        )
        CurrentCut = BuildMandatoryCapacityCut("A2", "Generate1")
        PriorConstraints = PlacementAssignmentConstraintSet().WithCut(
            PriorCut
        )
        CurrentConstraints = PriorConstraints.WithCut(CurrentCut)
        Pending = PendingJointPlacementState(
            Request=PlacementGenerationRequest(
                SourceGenerator="row-beam-conflict-relocation",
                RoutingSpacing=5,
                PackingPolicy=object(),
            ),
            CandidateIndex=3,
            RelocationVariant=2,
            RoutingSpacing=5,
            RelocationSignals=frozenset({"A2", "Generate1"}),
            RelocationPrioritySignals=frozenset({"A2", "Generate1"}),
            RequiredRelocationSignals=frozenset({"A2", "Generate1"}),
            AssignmentCut=CurrentCut,
            AssignmentConstraints=CurrentConstraints,
        )

        self.assertTrue(PendingJointPlacementStateMatchesIdentity(
            Pending,
            CurrentCut.ConflictFingerprint,
            CurrentConstraints.Fingerprint,
        ))
        self.assertFalse(PlacementConstraintFingerprintMatchesIdentity(
            PriorConstraints.Fingerprint,
            CurrentConstraints.Fingerprint,
            True,
        ))
        self.assertTrue(PlacementConstraintFingerprintMatchesIdentity(
            Pending.AssignmentConstraints.Fingerprint,
            CurrentConstraints.Fingerprint,
            True,
        ))

    def testTerminalConstraintReboundReranksCurrentEpochLead(
        self,
    ) -> None:
        PriorCut = BuildHigherOrderCapacityCut(
            PrioritySignals=("Input0", "Operand1", "Operand2"),
            PairEdge=("CarryGenerate", "CarryPropagate"),
        )
        CurrentCut = BuildMandatoryCapacityCut(
            "Input2",
            "CarryGenerate1",
        )
        PriorConstraints = PlacementAssignmentConstraintSet().WithCut(
            PriorCut
        )
        CurrentConstraints = PriorConstraints.WithCut(CurrentCut)
        Original = PendingJointPlacementState(
            Request=PlacementGenerationRequest(
                SourceGenerator="row-beam-conflict-relocation",
                RoutingSpacing=5,
                PackingPolicy=object(),
            ),
            CandidateIndex=4,
            RelocationVariant=2,
            RoutingSpacing=5,
            RelocationSignals=frozenset({
                "Input0",
                "Operand1",
                "Operand2",
            }),
            RelocationPrioritySignals=frozenset({
                "Input0",
                "Operand1",
                "Operand2",
            }),
            RequiredRelocationSignals=frozenset({"Input0"}),
            AssignmentCut=PriorCut,
            AssignmentConstraints=PriorConstraints,
            CoordinatedCandidateDiversificationSignals=frozenset({
                "CarryGenerate",
                "CarryPropagate",
            }),
        )
        Rebound = RebindTerminalJointPlacementConstraintEpoch(
            Original,
            CurrentCut,
            CurrentConstraints,
        )

        self.assertEqual(Rebound.Request, Original.Request)
        self.assertEqual(Rebound.CandidateIndex, 0)
        self.assertEqual(
            Rebound.RelocationVariant,
            Original.RelocationVariant,
        )
        self.assertEqual(Rebound.RoutingSpacing, Original.RoutingSpacing)
        self.assertEqual(
            Rebound.RelocationSignals,
            Original.RelocationSignals,
        )
        self.assertEqual(
            Rebound.CoordinatedCandidateDiversificationSignals,
            Original.CoordinatedCandidateDiversificationSignals,
        )
        self.assertEqual(
            Rebound.AssignmentConstraints,
            CurrentConstraints,
        )
        self.assertNotEqual(
            BuildPendingJointPlacementPortfolioFingerprint(Original),
            BuildPendingJointPlacementPortfolioFingerprint(Rebound),
        )
        Sibling = replace(
            Rebound,
            CandidateIndex=3,
            RelocationSignals=frozenset({
                *Rebound.RelocationSignals,
                "CurrentCutSignal",
            }),
        )
        OtherEpoch = replace(
            Sibling,
            AssignmentConstraints=PriorConstraints,
        )
        self.assertEqual(
            SelectNewPendingJointPlacementPortfolioFingerprint(
            (Sibling, OtherEpoch),
                frozenset(),
                CurrentConstraints.Fingerprint,
            ),
            BuildPendingJointPlacementPortfolioFingerprint(Sibling),
        )
        self.assertIsNone(
            SelectNewPendingJointPlacementPortfolioFingerprint(
                (OtherEpoch,),
                frozenset(),
                CurrentConstraints.Fingerprint,
            )
        )
        self.assertIsNone(
            SelectNewPendingJointPlacementPortfolioFingerprint(
                (
                    Sibling,
                    replace(
                        Sibling,
                        CandidateIndex=4,
                        RelocationSignals=frozenset({
                            *Sibling.RelocationSignals,
                            "AnotherSignal",
                        }),
                    ),
                ),
                frozenset(),
                CurrentConstraints.Fingerprint,
            )
        )
        self.assertIsNone(
            SelectNewPendingJointPlacementPortfolioFingerprint(
                (Sibling,),
                frozenset({
                    BuildPendingJointPlacementStateKey(Sibling),
                }),
                CurrentConstraints.Fingerprint,
            )
        )

    def testConstraintEpochMatchingIsRenameAndCutOrderInvariant(
        self,
    ) -> None:
        First = BuildHigherOrderCapacityCut(
            PrioritySignals=("A0", "B1", "B2"),
            PairEdge=("Generate0", "Propagate1"),
        )
        Second = BuildMandatoryCapacityCut("A2", "Generate1")
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
        RenamedFirst = BuildHigherOrderCapacityCut(
            PrioritySignals=("Input0", "Operand1", "Operand2"),
            PairEdge=("CarryGenerate", "CarryPropagate"),
        )
        RenamedSecond = BuildMandatoryCapacityCut(
            "Input2",
            "CarryGenerate1",
        )
        Renamed = (
            PlacementAssignmentConstraintSet()
            .WithCut(RenamedSecond)
            .WithCut(RenamedFirst)
        )
        Prior = PlacementAssignmentConstraintSet().WithCut(First)
        RenamedPrior = PlacementAssignmentConstraintSet().WithCut(
            RenamedFirst
        )

        self.assertEqual(Forward.Fingerprint, Reverse.Fingerprint)
        self.assertEqual(
            [
                PlacementConstraintFingerprintMatchesIdentity(
                    Fingerprint,
                    Forward.Fingerprint,
                    True,
                )
                for Fingerprint in (
                    Prior.Fingerprint,
                    Forward.Fingerprint,
                )
            ],
            [
                PlacementConstraintFingerprintMatchesIdentity(
                    Fingerprint,
                    Renamed.Fingerprint,
                    True,
                )
                for Fingerprint in (
                    RenamedPrior.Fingerprint,
                    Renamed.Fingerprint,
                )
            ],
        )

    def testTopologyTriggerPropagatesToEveryPackedRecipe(self) -> None:
        for Triggered in (False, True):
            with self.subTest(Triggered=Triggered):
                Plan = BuildPlacementGenerationPlan(
                    LocalFirstPhysicalDesignPolicy,
                    PreferPackedPlacements=True,
                    EnableInitialJointOrientation=Triggered,
                )
                PackedRequests = [
                    Request
                    for Request in (
                        *Plan.PrimaryRequests,
                        *Plan.DeferredRequests,
                    )
                    if Request.PackingPolicy.Enabled
                ]

                self.assertTrue(PackedRequests)
                self.assertEqual(
                    {
                        Request.PackingPolicy
                        .EnableJointClusterOrientation
                        for Request in PackedRequests
                    },
                    {Triggered},
                )

    def testCompactDirectOnlyOrientationStaysOutOfPrimaryPortfolio(
        self,
    ) -> None:
        Plan = BuildPlacementGenerationPlan(
            LocalFirstPhysicalDesignPolicy,
            PreferPackedPlacements=True,
            EnableInitialJointOrientation=False,
            EnableCompactDirectOnlyOrientation=True,
        )
        DirectOnly = next(
            Request
            for Request in Plan.DeferredRequests
            if Request.SourceGenerator == "row-beam-direct-only"
        )
        PrimaryPacked = next(
            Request
            for Request in Plan.PrimaryRequests
            if Request.PackingPolicy.Enabled
        )

        self.assertTrue(
            DirectOnly.PackingPolicy.EnableJointClusterOrientation
        )
        self.assertEqual(
            DirectOnly.PackingPolicy.RetainedJointPlacementCandidates,
            1,
        )
        self.assertFalse(
            PrimaryPacked.PackingPolicy.EnableJointClusterOrientation
        )

    def testExactInterfaceGateRequiresMeasuredPressureAndContract(
        self,
    ) -> None:
        Reconvergent = BuildTopologyDemandProfile(
            BuildReconvergentFanout(4)
        )
        Ordinary = replace(
            Reconvergent,
            QualifyingReconvergentCutCount=0,
            MandatoryAccessConflictResources=0,
        )
        CompletePlaced = SimpleNamespace(
            CompleteClusterInterfaceAccess=True,
            ClusterBoundaryLeaseRequests=(),
        )
        LegacyPlaced = SimpleNamespace(
            CompleteClusterInterfaceAccess=False,
            ClusterBoundaryLeaseRequests=(),
        )

        self.assertTrue(RequiresExactClusterInterfaceSolve(
            Reconvergent,
            CompletePlaced,
            LocalFirstPhysicalDesignPolicy,
        ))
        self.assertFalse(RequiresExactClusterInterfaceSolve(
            Reconvergent,
            LegacyPlaced,
            LocalFirstPhysicalDesignPolicy,
        ))
        self.assertFalse(RequiresExactClusterInterfaceSolve(
            Ordinary,
            CompletePlaced,
            LocalFirstPhysicalDesignPolicy,
        ))

    def testExactInterfaceFinalProofCombinesThreeRetainedStates(
        self,
    ) -> None:
        Nogood = ClusterInterfaceRealizabilityNogood(
            PlacementStateFingerprint="state-2",
            ComponentFingerprint="component",
            Signal="Propagate",
            TerminalPatternFingerprint="pattern",
            CandidateDomainFingerprint="domain",
            RouteFailureFingerprint="failure",
            RejectedAssignmentFingerprint="assignment",
        )
        Proofs = (
            ClusterInterfaceStateProof(
                PlacementStateFingerprint="state-1",
                Status="ownership-unsatisfiable",
                OwnershipUnsatCoreFingerprint="core-1",
                DomainComplete=True,
                OwnershipComplete=True,
                RealizabilityComplete=True,
                Exhaustive=True,
            ),
            ClusterInterfaceStateProof(
                PlacementStateFingerprint="state-2",
                Status="realizability-unsatisfiable",
                AssignmentFingerprints=("assignment",),
                RealizabilityNogoods=(Nogood,),
                DomainComplete=True,
                OwnershipComplete=True,
                RealizabilityComplete=True,
                Exhaustive=True,
            ),
            ClusterInterfaceStateProof(
                PlacementStateFingerprint="state-3",
                Status="ownership-unsatisfiable",
                OwnershipUnsatCoreFingerprint="core-3",
                DomainComplete=True,
                OwnershipComplete=True,
                RealizabilityComplete=True,
                Exhaustive=True,
            ),
        )

        Result = BuildClusterInterfaceUnsatProof(Proofs)
        Renamed = BuildClusterInterfaceUnsatProof((
            Proofs[0],
            replace(
                Proofs[1],
                RealizabilityNogoods=(
                    replace(Nogood, Signal="Renamed"),
                ),
            ),
            Proofs[2],
        ))

        self.assertTrue(Result["Complete"])
        self.assertFalse(Result["BroadFallbackAllowed"])
        self.assertFalse(Result["ExecutableRepairAllowed"])
        self.assertEqual(Result["AttemptedStateCount"], 3)
        self.assertEqual(
            Result["ProofFingerprint"],
            Renamed["ProofFingerprint"],
        )
        with self.assertRaises(ValueError):
            BuildClusterInterfaceUnsatProof((
                Proofs[0],
                Proofs[0],
            ))
        Incomplete = BuildClusterInterfaceUnsatProof((
            replace(Proofs[0], DomainComplete=False),
            Proofs[1],
            Proofs[2],
        ))
        self.assertFalse(Incomplete["Complete"])

        BoundedPlacementPortfolio = BuildClusterInterfaceUnsatProof(
            Proofs,
            PlacementPortfolioDomainComplete=False,
        )
        self.assertFalse(BoundedPlacementPortfolio["Complete"])
        self.assertTrue(
            BoundedPlacementPortfolio[
                "NamedComponentStateProofComplete"
            ]
        )
        self.assertFalse(
            BoundedPlacementPortfolio[
                "ArchitecturalUnsatisfiabilityProven"
            ]
        )
        self.assertEqual(
            BoundedPlacementPortfolio["ProofScope"],
            "named-placement-component-states",
        )

    def testExactInterfaceProofFingerprintIsOrderStable(self) -> None:
        Proofs = (
            ClusterInterfaceStateProof(
                PlacementStateFingerprint="state-1",
                Status="ownership-unsatisfiable",
                OwnershipUnsatCoreFingerprint="core-1",
                DomainComplete=True,
                OwnershipComplete=True,
                RealizabilityComplete=True,
                Exhaustive=True,
            ),
            ClusterInterfaceStateProof(
                PlacementStateFingerprint="state-2",
                Status="realizability-unsatisfiable",
                DomainComplete=True,
                OwnershipComplete=True,
                RealizabilityComplete=True,
                Exhaustive=True,
                AssignmentFingerprints=(
                    "assignment-b",
                    "assignment-a",
                ),
            ),
            ClusterInterfaceStateProof(
                PlacementStateFingerprint="state-3",
                Status="ownership-unsatisfiable",
                DomainComplete=True,
                OwnershipComplete=True,
                RealizabilityComplete=True,
                Exhaustive=True,
            ),
        )
        Expected = (
            "state-3",
            "state-1",
            "state-2",
        )

        Result = BuildClusterInterfaceUnsatProof(Proofs)
        Permuted = BuildClusterInterfaceUnsatProof(
            (
                Proofs[2],
                Proofs[0],
                Proofs[1],
            ),
            ExpectedComponentStateFingerprints=Expected,
        )

        self.assertEqual(
            Result["ProofFingerprint"],
            Permuted["ProofFingerprint"],
        )
        self.assertTrue(Permuted["Complete"])
        self.assertTrue(Permuted["ComponentStateDomainComplete"])
        self.assertEqual(
            Permuted["ExpectedComponentStateCount"],
            len(Proofs),
        )

    def testExactInterfaceProofRequiresEveryComponentSearchState(
        self,
    ) -> None:
        Placement = "shared-placement"
        FirstState = BuildClusterInterfaceComponentStateFingerprint(
            Placement,
            0,
        )
        SecondState = BuildClusterInterfaceComponentStateFingerprint(
            Placement,
            1,
        )
        FirstProof = ClusterInterfaceStateProof(
            PlacementStateFingerprint=Placement,
            ComponentStateFingerprint=FirstState,
            ComponentVariant=0,
            ComponentSelectionFingerprint="component-a",
            Status="ownership-unsatisfiable",
            DomainComplete=True,
            OwnershipComplete=True,
            RealizabilityComplete=True,
            Exhaustive=True,
        )

        Missing = BuildClusterInterfaceUnsatProof(
            (FirstProof,),
            ExpectedComponentStateFingerprints=(
                FirstState,
                SecondState,
            ),
        )
        Complete = BuildClusterInterfaceUnsatProof(
            (
                FirstProof,
                replace(
                    FirstProof,
                    ComponentStateFingerprint=SecondState,
                    ComponentVariant=1,
                    ComponentSelectionFingerprint="component-b",
                ),
            ),
            ExpectedComponentStateFingerprints=(
                FirstState,
                SecondState,
            ),
        )

        self.assertFalse(Missing["Complete"])
        self.assertFalse(Missing["ComponentStateDomainComplete"])
        self.assertEqual(
            Missing["MissingComponentStateFingerprints"],
            [SecondState],
        )
        self.assertTrue(Complete["Complete"])
        self.assertEqual(Complete["ProvenComponentStateCount"], 2)

    def testExactInterfaceProofRejectsOnlyRepeatedComponentState(
        self,
    ) -> None:
        Placement = "shared-placement"
        State = BuildClusterInterfaceComponentStateFingerprint(
            Placement,
            0,
        )
        Proof = ClusterInterfaceStateProof(
            PlacementStateFingerprint=Placement,
            ComponentStateFingerprint=State,
            ComponentVariant=0,
            ComponentSelectionFingerprint="component-a",
            Status="ownership-unsatisfiable",
            DomainComplete=True,
            OwnershipComplete=True,
            RealizabilityComplete=True,
            Exhaustive=True,
        )

        with self.assertRaisesRegex(ValueError, "repeated component state"):
            BuildClusterInterfaceUnsatProof((Proof, Proof))

    def testClusterInterfaceStageUsesOneSharedReservedDeadline(self) -> None:
        Deadline = RoutingDeadline.Start(118.0)
        Schedule = BuildClusterInterfaceStageSchedule(
            Deadline,
            ("state-b", "state-a"),
            LocalCompilationReserveSeconds=5.0,
            GlobalRoutingReserveSeconds=15.0,
            PublicationReserveSeconds=2.0,
        )
        self.assertEqual(
            Schedule.StateFingerprints,
            ("state-b", "state-a"),
        )
        self.assertLessEqual(
            Schedule.ExpiresAt,
            Deadline.ExpiresAt - 17.0 + 0.001,
        )
        self.assertLessEqual(
            Schedule.PlanningExpiresAt,
            Deadline.ExpiresAt - 22.0 + 0.001,
        )
        self.assertLessEqual(
            Schedule.ProofGuidedPlanningExpiresAt,
            Deadline.ExpiresAt - 19.0 + 0.001,
        )
        self.assertLessEqual(
            Schedule.AccessRepairPlanningExpiresAt,
            Deadline.ExpiresAt - 14.0 + 0.001,
        )
        self.assertLessEqual(
            Schedule.AccessRepairExpiresAt,
            Deadline.ExpiresAt - 12.0 + 0.001,
        )
        self.assertEqual(Schedule.LocalCompilationReserveSeconds, 5.0)
        self.assertEqual(
            Schedule.ProofGuidedLocalCompilationReserveSeconds,
            2.0,
        )
        self.assertEqual(
            Schedule.AccessRepairGlobalRoutingReserveSeconds,
            10.0,
        )
        self.assertEqual(
            Schedule.ToDictionary()["LocalCompilationReserveSeconds"],
            5.0,
        )
        self.assertEqual(
            Schedule.ToDictionary()["Scheduling"],
            "sequential-shared-budget",
        )

    def testProofGuidedPlanningCannotConsumeGlobalRoutingReserve(
        self,
    ) -> None:
        Deadline = RoutingDeadline.Start(118.0)
        Schedule = BuildClusterInterfaceStageSchedule(
            Deadline,
            ("repaired-state",),
            LocalCompilationReserveSeconds=5.0,
            GlobalRoutingReserveSeconds=15.0,
            PublicationReserveSeconds=0.0,
        )

        self.assertGreater(
            Schedule.ProofGuidedPlanningExpiresAt,
            Schedule.PlanningExpiresAt,
        )
        self.assertLessEqual(
            Schedule.ProofGuidedPlanningExpiresAt,
            Schedule.ExpiresAt - 2.0 + 0.001,
        )
        self.assertLessEqual(
            Schedule.ExpiresAt,
            Deadline.ExpiresAt - 15.0 + 0.001,
        )
        self.assertGreater(
            Schedule.AccessRepairPlanningExpiresAt,
            Schedule.ProofGuidedPlanningExpiresAt,
        )
        self.assertLessEqual(
            Schedule.AccessRepairPlanningExpiresAt,
            Deadline.ExpiresAt - 12.0 + 0.001,
        )

    def testLocalCompilationAdmissionFailureReportsZeroSolverWork(self) -> None:
        Deadline = RoutingDeadline.Start(30.0)
        Schedule = BuildClusterInterfaceStageSchedule(
            Deadline,
            ("state",),
            LocalCompilationReserveSeconds=5.0,
            GlobalRoutingReserveSeconds=4.0,
            PublicationReserveSeconds=2.0,
        )

        Failure = BuildLocalComponentCompilationAdmissionFailure(
            Schedule,
            RemainingSeconds=-0.25,
        )

        self.assertEqual(
            Failure.Reason,
            RoutingFailureReason.PhysicalComponentAssemblyIncomplete,
        )
        self.assertEqual(
            Failure.Stage,
            "ClosedComponentCompilationIncomplete",
        )
        Solve = Failure.Diagnostics["ComponentRoutingSolve"]
        self.assertEqual(Solve["ExpansionCount"], 0)
        self.assertTrue(Solve["Diagnostics"]["DeadlineExceeded"])
        self.assertFalse(Solve["Diagnostics"]["WorkCapReached"])
        self.assertFalse(Solve["Diagnostics"]["LocalCompilationEntered"])

    def test_interface_portfolio_retains_topology_not_orientation_labels(self):
        def Candidate(
            Index: int,
            Topology: str,
            MandatoryConflicts: int = 0,
        ) -> PcbPlacementCandidate:
            return PcbPlacementCandidate(
                CandidateId=f"Candidate-{Index}",
                SourceGenerator="joint",
                RoutingSpacing=5,
                PlacementFingerprint=f"placement-{Index}",
                FeedbackScore=(Index,),
                BoundaryOverflow=0,
                PinScarcityCount=0,
                GuideOverflowPeak=0,
                GuideOverflowCells=0,
                PinEscapeConflictCount=0,
                EstimatedGlobalExtensionNodes=0,
                EstimatedGlobalExtensionNets=0,
                PreOwnedNodeCount=0,
                Placement=None,
                TopologyDemand=SimpleNamespace(
                    MandatoryAccessConflictResources=(
                        MandatoryConflicts
                    ),
                ),
                InterfaceTopologyFingerprint=Topology,
            )

        Selected, Audits = SelectInterfaceDiversePlacementStates((
            Candidate(0, "topology-a"),
            Candidate(1, "topology-a"),
            Candidate(2, "topology-b", MandatoryConflicts=1),
            Candidate(3, "topology-c"),
        ))

        self.assertEqual(
            [Value.CandidateId for Value in Selected],
            ["Candidate-0", "Candidate-3"],
        )
        self.assertEqual(
            [Value.Classification for Value in Audits[:4]],
            [
                "retained-interface-distinct",
                "duplicate-access-topology",
                "mandatory-access-unsat",
                "retained-interface-distinct",
            ],
        )
        self.assertEqual(len(Audits), 6)
        self.assertTrue(all(
            Value.Classification == "pruned-by-scoring-budget"
            for Value in Audits[4:]
        ))

        Stratified, StratifiedAudits = (
            SelectInterfaceDiversePlacementStates(
                tuple(
                    Candidate(Index, f"topology-{Index}")
                    for Index in range(12)
                ),
                MaximumStates=6,
            )
        )
        self.assertEqual(
            [Value.CandidateId for Value in Stratified],
            [
                "Candidate-0",
                "Candidate-2",
                "Candidate-4",
                "Candidate-6",
                "Candidate-8",
                "Candidate-11",
            ],
        )
        self.assertEqual(
            sum(
                Audit.Classification
                == "retained-interface-distinct"
                for Audit in StratifiedAudits
            ),
            6,
        )

    def test_exact_interface_commit_refills_rejected_states(self):
        def State(Index: int, Ownership: str) -> dict[str, object]:
            return {
                "CandidateIndex": Index,
                "ExactLegal": True,
                "ClusterInterfacePlacement": {
                    "OwnershipFingerprint": Ownership,
                },
            }

        def Profile(
            Index: int,
            HasConflict: bool,
        ) -> MandatoryAccessConflictProfile:
            Conflict = (
                (
                    SimpleNamespace(
                        Kind=SimpleNamespace(value="wire"),
                        Position=(Index, 0, 0),
                    ),
                    ("owner",),
                ),
            ) if HasConflict else ()
            return MandatoryAccessConflictProfile(
                OwnershipFingerprint=f"mandatory-{Index}",
                ConflictFingerprint=f"conflict-{Index}",
                OwnershipRecords=(),
                CrossConflicts=Conflict,
                SelfConflicts=(),
            )

        States = (
            State(0, "a"),
            State(1, "b"),
            State(2, "b"),
            State(3, "c"),
        )
        Selected, Attrition = SelectExactInterfaceCommitStates(
            States,
            {
                0: Profile(0, True),
                1: Profile(1, False),
                2: Profile(2, False),
                3: Profile(3, False),
            },
            MaximumStates=2,
        )

        self.assertEqual(
            [State["SearchCandidateIndex"] for State in Selected],
            [1, 3],
        )
        self.assertEqual(
            [State["CandidateIndex"] for State in Selected],
            [0, 1],
        )
        self.assertEqual(
            [Entry["Classification"] for Entry in Attrition],
            [
                "mandatory-access-unsat",
                "duplicate-access-topology",
            ],
        )


if __name__ == "__main__":
    unittest.main()
