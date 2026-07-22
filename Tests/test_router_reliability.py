from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
from time import monotonic, sleep
import unittest
from unittest.mock import patch

from Compiler.Pipeline import (
    TryWriteRoutingFailureArtifact,
    WriteRoutingFailureArtifact,
)
from Compiler.Placement.Geometry import PlacedDesign
from Compiler.Placement.Pcb import PcbPlacement
from Compiler.Placement.PcbFlow import (
    BuildPlacementFingerprint,
    ExtractPlacementRelocationSignals,
    FailureRequestsPlacementAdvance,
    PlacementGenerationPlan,
    PlacementGenerationRequest,
    PlacementGenerationRoutingReserveSeconds,
    SelectReleasableLocalClaimSignals,
    _PlaceAndRoutePcbWithPolicy,
)
from Compiler.Routing.Failures import (
    RoutingFailure,
    RoutingFailureReason,
    RoutingStageError,
)
from Compiler.Routing.Policy import (
    BuildRoutingAttemptPolicies,
    LocalFirstPhysicalDesignPolicy,
    RoutingStrategy,
)
from Compiler.Routing.Models import RoutedDesign
from Compiler.Routing.Pcb import CompactRoutedTrees, RoutePcbAttempt
from Compiler.Routing.LocalFirst import PlacementRoutingFeedback
from Compiler.Routing.Reliability import (
    BuildRoutingDeadlineDiagnostics,
    BuildStableFingerprint,
    ChooseRoutingEscalationAction,
    EnforceRoutingRuntimeLimit,
    HasAdaptiveEscalationBudget,
    RemainingRoutingRuntimeMilliseconds,
    RetainUnaffectedCandidateCache,
    SelectBoundedDiverseCandidatePool,
    RoutingDeadline,
    RoutingEscalationState,
)
from Compiler.Routing.Technology import DefaultRedstoneRoutingTechnology


class RouterReliabilityTests(unittest.TestCase):
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
            Modules={"Top": SimpleNamespace(Gates=[object()])},
        )

        with (
            patch("Compiler.Placement.PcbFlow.ValidateNandOnlyDesign"),
            patch("Compiler.Placement.PcbFlow.PlacePcbGraph", side_effect=PlaceGraph),
            patch("Compiler.Placement.PcbFlow.ValidatePlacedCellElectricalIsolation"),
            patch("Compiler.Placement.PcbFlow.BuildRoutingResources"),
            patch(
                "Compiler.Placement.PcbFlow.MeasurePlacementRoutingFeedback",
                side_effect=Feedback,
            ),
            patch("Compiler.Placement.PcbFlow.RoutePcbDesign", side_effect=Route),
            patch(
                "Compiler.Placement.PcbFlow.BuildLocalFirstSnapshot",
                return_value=Snapshot,
            ),
            patch(
                "Compiler.Placement.PcbFlow.MeasurePcbDesign",
                return_value=(1, 1, 1, 1),
            ),
        ):
            Result = _PlaceAndRoutePcbWithPolicy(
                Netlist,
                ProgressCallback=ProgressCallback,
                Policy=Policy,
                Technology=DefaultRedstoneRoutingTechnology,
                RequestedStrategy=RoutingStrategy.NewRouterFirst,
                UsedStrategy=RoutingStrategy.NewRouterFirst,
                RoutedValidationCallback=RoutedValidationCallback,
            )

        return Result, SuccessfulRoutes, DeadlineIdentities

    def testFailureArtifactWriteErrorIsNonFatal(self) -> None:
        with patch(
            "Compiler.Pipeline.WriteRoutingFailureArtifact",
            side_effect=OSError("diagnostic disk failure"),
        ):
            self.assertIsNone(TryWriteRoutingFailureArtifact())

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

    def testPlacementAdvanceFailureSkipsSameCandidateLocalClaimRecovery(self) -> None:
        RouteOrder = []
        OwnedClaim = SimpleNamespace(Signal="Owned", ClusterId=0, Nodes=())

        def RouteBehavior(X, SuccessfulRoute, _Options):
            if X == 0:
                raise RoutingStageError(
                    RoutingFailure(
                        Reason=RoutingFailureReason.TrackAssignmentConflict,
                        Stage="TrackAssignment",
                        AffectedNets=("Owned",),
                        Detail="adaptive slice intentionally expired",
                        RepairActions=("AdvancePlacementCandidate",),
                        Diagnostics={
                            "Action": (
                                "advance-placement-adaptive-slice-expired"
                            ),
                        },
                    )
                )
            return SuccessfulRoute

        Result, SuccessfulRoutes, _DeadlineIdentities = self.RunTwoPlacementFlow(
            RouteBehavior=RouteBehavior,
            RouteOrder=RouteOrder,
            LocalClaimsByX={0: (OwnedClaim,)},
        )

        self.assertEqual(RouteOrder, [0, 10])
        self.assertIs(Result.Routed, SuccessfulRoutes[10])
        self.assertTrue(FailureRequestsPlacementAdvance(
            RoutingFailure(
                Reason=RoutingFailureReason.TrackAssignmentConflict,
                Stage="TrackAssignment",
                Diagnostics={
                    "Action": "advance-placement-adaptive-slice-expired",
                },
            )
        ))

    def testPermittedLocalClaimRecoveryUsesRemainingAdaptiveSlice(self) -> None:
        RouteOrder = []
        RecoveryBudgets = []
        OwnedClaim = SimpleNamespace(Signal="Owned", ClusterId=0, Nodes=())

        def RouteBehavior(X, SuccessfulRoute, Options):
            RecoveryBudgets.append(
                Options["Policy"].AdaptiveRouting.MaximumRuntimeSeconds
            )
            if X == 0 and RouteOrder.count(0) == 1:
                raise RoutingStageError(
                    RoutingFailure(
                        Reason=RoutingFailureReason.NoBoundaryEscape,
                        Stage="PortalGeneration",
                        AffectedNets=("Owned",),
                        Detail="release this local claim",
                    )
                )
            return SuccessfulRoute

        Result, SuccessfulRoutes, DeadlineIdentities = self.RunTwoPlacementFlow(
            RouteBehavior=RouteBehavior,
            RouteOrder=RouteOrder,
            LocalClaimsByX={0: (OwnedClaim,)},
        )

        self.assertEqual(RouteOrder, [0, 0])
        self.assertIs(Result.Routed, SuccessfulRoutes[0])
        self.assertEqual(len(set(DeadlineIdentities)), 1)
        self.assertEqual(len(RecoveryBudgets), 2)
        self.assertLess(RecoveryBudgets[1], RecoveryBudgets[0])
        Attempt = Result.Routed.RoutingControlEffectiveness[
            "PlacementAttempts"
        ][0]
        self.assertEqual(
            Attempt["Recovery"],
            "released-affected-local-claims",
        )
        self.assertLessEqual(
            Attempt["AdaptiveAttemptStartedAt"],
            Attempt["AdaptiveAttemptExpiresAt"],
        )

    def testSlowDeferredGeneratorCannotConsumeRoutingReserve(self) -> None:
        RouteOrder = []
        PlacementOrder = []
        Policy = LocalFirstPhysicalDesignPolicy
        Plan = PlacementGenerationPlan(
            PrimaryRequests=(
                PlacementGenerationRequest(
                    "primary-packed",
                    Policy.Placement.RoutingSpacing,
                    Policy.NandPacking,
                ),
                PlacementGenerationRequest(
                    "primary-unpacked",
                    Policy.Placement.RoutingSpacing,
                    replace(Policy.NandPacking, Enabled=False),
                ),
            ),
            DeferredRequests=(
                PlacementGenerationRequest(
                    "slow-final-generator",
                    Policy.Placement.RoutingSpacing + 1,
                    Policy.NandPacking,
                ),
            ),
            MaximumAttempts=3,
        )

        def PlaceBehavior(
            CallIndex,
            FirstPlacement,
            SecondPlacement,
            _PackingPolicy,
            Options,
        ):
            PlacementOrder.append(CallIndex)
            if CallIndex == 1:
                return FirstPlacement
            if CallIndex == 2:
                return SecondPlacement
            sleep(0.41)
            Options["WorkCheck"]({"Phase": "slow-final-generator"})
            return FirstPlacement

        def RouteBehavior(X, SuccessfulRoute, _Options):
            if X == 0:
                raise RoutingStageError(
                    RoutingFailure(
                        Reason=RoutingFailureReason.TrackAssignmentConflict,
                        Stage="TrackAssignment",
                        Detail="advance to the retained alternative",
                    )
                )
            return SuccessfulRoute

        with patch(
            "Compiler.Placement.PcbFlow.BuildPlacementGenerationPlan",
            return_value=Plan,
        ):
            Result, SuccessfulRoutes, _DeadlineIdentities = (
                self.RunTwoPlacementFlow(
                    RouteBehavior=RouteBehavior,
                    RouteOrder=RouteOrder,
                    RuntimeBudgetSeconds=0.5,
                    PlacementCallBehavior=PlaceBehavior,
                    FeedbackBoundaryOverflowByX={10: 1},
                )
            )

        self.assertEqual(
            PlacementGenerationRoutingReserveSeconds(
                replace(Policy, RuntimeBudgetSeconds=0.5)
            ),
            0.1,
        )
        self.assertEqual(RouteOrder, [0, 10])
        self.assertEqual(PlacementOrder, [1, 2])
        self.assertIs(Result.Routed, SuccessfulRoutes[10])
        GenerationFailures = Result.Routed.RoutingControlEffectiveness[
            "PlacementGenerationFailures"
        ]
        self.assertFalse(any(
            Entry["SourceGenerator"] == "slow-final-generator"
            for Entry in GenerationFailures
        ))

    def testRetainedCandidateStartsWithPositiveDeadlineRemainder(self) -> None:
        RouteOrder = []

        def RouteBehavior(X, SuccessfulRoute, _Options):
            if X == 0:
                sleep(0.06)
                raise RoutingStageError(
                    RoutingFailure(
                        Reason=RoutingFailureReason.TrackAssignmentConflict,
                        Stage="TrackAssignment",
                        AffectedNets=("A", "B", "C"),
                        RepairActions=("AdvancePlacementCandidate",),
                        Diagnostics={"Action": "advance-placement-conflict-relocation"},
                    )
                )
            return SuccessfulRoute

        Result, SuccessfulRoutes, _DeadlineIdentities = self.RunTwoPlacementFlow(
            RouteBehavior=RouteBehavior,
            RouteOrder=RouteOrder,
            RuntimeBudgetSeconds=0.11,
        )

        self.assertEqual(RouteOrder, [0, 10])
        self.assertIs(Result.Routed, SuccessfulRoutes[10])

    def testHigherOrderConflictFeedsDeferredPlacementRelocation(self) -> None:
        Policy = LocalFirstPhysicalDesignPolicy
        Plan = PlacementGenerationPlan(
            PrimaryRequests=(
                PlacementGenerationRequest(
                    "primary-packed",
                    Policy.Placement.RoutingSpacing,
                    Policy.NandPacking,
                ),
                PlacementGenerationRequest(
                    "primary-unpacked",
                    Policy.Placement.RoutingSpacing,
                    replace(Policy.NandPacking, Enabled=False),
                ),
            ),
            DeferredRequests=(
                PlacementGenerationRequest(
                    "relocated-packed",
                    Policy.Placement.RoutingSpacing,
                    Policy.NandPacking,
                ),
            ),
            MaximumAttempts=3,
        )
        RelocationInputs = []

        def PlaceBehavior(
            CallIndex,
            FirstPlacement,
            SecondPlacement,
            _PackingPolicy,
            Options,
        ):
            RelocationInputs.append(Options["RelocationSignals"])
            return FirstPlacement if CallIndex != 2 else SecondPlacement

        def RouteBehavior(_X, _SuccessfulRoute, _Options):
            raise RoutingStageError(
                RoutingFailure(
                    Reason=RoutingFailureReason.TrackAssignmentConflict,
                    Stage="TrackAssignment",
                    AffectedNets=("A", "B", "C"),
                    RepairActions=("RelocateAffectedClusters",),
                    Diagnostics={
                        "Action": "advance-placement-conflict-relocation",
                        "ConflictGraph": {
                            "Classification": "higher-order-placement-conflict",
                            "NativeConflictSignals": ["C", "A", "B"],
                            "ConflictSignals": ["A", "B", "C"],
                        },
                    },
                )
            )

        with patch(
            "Compiler.Placement.PcbFlow.BuildPlacementGenerationPlan",
            return_value=Plan,
        ):
            with self.assertRaises(RoutingStageError):
                self.RunTwoPlacementFlow(
                    RouteBehavior=RouteBehavior,
                    PlacementCallBehavior=PlaceBehavior,
                )

        self.assertEqual(RelocationInputs[:2], [frozenset(), frozenset()])
        self.assertEqual(RelocationInputs[2], frozenset({"A", "B", "C"}))
        self.assertEqual(
            ExtractPlacementRelocationSignals(
                RoutingFailure(
                    Reason=RoutingFailureReason.TrackAssignmentConflict,
                    Stage="TrackAssignment",
                    Diagnostics={
                        "ConflictGraph": {
                            "NativeConflictSignals": ["C", "A", "B"],
                        }
                    },
                )
            ),
            frozenset({"A", "B", "C"}),
        )
        self.assertEqual(
            ExtractPlacementRelocationSignals(
                RoutingFailure(
                    Reason=RoutingFailureReason.TrackAssignmentConflict,
                    Stage="TrackAssignment",
                    AffectedNets=("A", "B", "C", "D"),
                    Diagnostics={
                        "ConflictGraph": {
                            "ConflictSignals": ["A", "B", "C", "D"],
                            "RelocationSignals": ["B", "C", "D"],
                        }
                    },
                )
            ),
            frozenset({"B", "C", "D"}),
        )
        self.assertEqual(
            ExtractPlacementRelocationSignals(
                RoutingFailure(
                    Reason=RoutingFailureReason.TrackAssignmentConflict,
                    Stage="TrackAssignment",
                    AffectedNets=("A", "B", "C", "D", "E"),
                    Diagnostics={
                        "ConflictGraph": {
                            "ConflictSignals": ["A", "B", "C"],
                        }
                    },
                )
            ),
            frozenset({"A", "B", "C"}),
        )

    def testFeedbackFailureCannotPublishPartialPlacementCandidate(self) -> None:
        RouteOrder = []
        FeedbackCalls = []

        def FeedbackBehavior(
            Placement,
            _RoutingSpacing,
            _WorkCheck,
            DefaultFeedback,
        ):
            X = Placement.Placed.PlacedGates[0].X
            FeedbackCalls.append(X)
            if X == 0:
                raise RoutingStageError(
                    RoutingFailure(
                        Reason=RoutingFailureReason.Stagnated,
                        Stage="PlacementFeedback",
                        Detail="feedback slice intentionally rejected",
                        RepairActions=("AdvancePlacementGenerator",),
                    )
                )
            return DefaultFeedback

        Result, SuccessfulRoutes, _DeadlineIdentities = self.RunTwoPlacementFlow(
            RouteOrder=RouteOrder,
            FeedbackBehavior=FeedbackBehavior,
        )

        self.assertEqual(FeedbackCalls[:2], [0, 10])
        self.assertEqual(RouteOrder, [10])
        self.assertIs(Result.Routed, SuccessfulRoutes[10])
        Candidates = Result.Routed.RoutingControlEffectiveness[
            "PlacementFeedbackCandidates"
        ]
        self.assertEqual(len(Candidates), 1)
        self.assertFalse(Candidates[0]["PackedNandPlacement"])

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
            patch("Compiler.Placement.PcbFlow.ValidateNandOnlyDesign"),
            patch(
                "Compiler.Placement.PcbFlow.PlacePcbGraph",
                side_effect=RoutingStageError(BoundaryFailure),
            ),
        ):
            with self.assertRaises(RoutingStageError) as Context:
                _PlaceAndRoutePcbWithPolicy(
                    Netlist,
                    ProgressCallback=None,
                    Policy=Policy,
                    Technology=DefaultRedstoneRoutingTechnology,
                    RequestedStrategy=RoutingStrategy.NewRouterFirst,
                    UsedStrategy=RoutingStrategy.NewRouterFirst,
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
            "IncreaseLaneDiversity",
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
            "IncreaseLaneDiversity",
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
            "Compiler.Routing.Reliability.monotonic",
            return_value=10.0,
        ):
            self.assertEqual(
                RemainingRoutingRuntimeMilliseconds(Deadline, 12.0),
                2_000,
            )

        with patch(
            "Compiler.Routing.Reliability.monotonic",
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
            "Compiler.Routing.Reliability.monotonic",
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
            Repeaters={},
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
            patch("Compiler.Routing.Pcb.RoutePcbNets", side_effect=Route),
            patch("Compiler.Routing.Pcb.CompactRoutedTrees", side_effect=Compact),
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
                RequestedStrategy=RoutingStrategy.NewRouterFirst,
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

    def testSecondRetainedPlacementRunsAfterFirstRoutingFailure(self) -> None:
        RouteOrder = []

        def RouteBehavior(X, SuccessfulRoute, _Options):
            if X == 0:
                raise RoutingStageError(
                    RoutingFailure(
                        Reason=RoutingFailureReason.TrackAssignmentConflict,
                        Stage="TrackAssignment",
                        Detail="first candidate intentionally fails",
                    )
                )
            return SuccessfulRoute

        Result, SuccessfulRoutes, DeadlineIdentities = self.RunTwoPlacementFlow(
            RouteBehavior=RouteBehavior,
            RouteOrder=RouteOrder,
        )

        self.assertEqual(RouteOrder, [0, 10])
        self.assertIs(Result.Routed, SuccessfulRoutes[10])
        self.assertEqual(len(set(DeadlineIdentities)), 1)
        Attempts = SuccessfulRoutes[10].RoutingControlEffectiveness[
            "PlacementAttempts"
        ]
        self.assertEqual(len(Attempts), 2)
        self.assertEqual(Attempts[-1]["Result"], "routed")

    def testValidationFailureAdvancesToSecondPlacement(self) -> None:
        RouteOrder = []
        ValidationOrder = []

        def Validate(Routed) -> None:
            ValidationOrder.append(Routed.CandidateX)
            if Routed.CandidateX == 0:
                raise RoutingStageError(
                    RoutingFailure(
                        Reason=RoutingFailureReason.ElectricalConflict,
                        Stage="PhysicalSimulation",
                        Detail="first candidate intentionally fails validation",
                    )
                )

        Result, SuccessfulRoutes, DeadlineIdentities = self.RunTwoPlacementFlow(
            RoutedValidationCallback=Validate,
            RouteOrder=RouteOrder,
        )

        self.assertEqual(RouteOrder, [0, 10])
        self.assertEqual(ValidationOrder, [0, 10])
        self.assertIs(Result.Routed, SuccessfulRoutes[10])
        self.assertEqual(len(set(DeadlineIdentities)), 1)

    def testCandidateCompletionRemainsPendingUntilValidationPasses(self) -> None:
        ProgressEvents = []

        def RouteBehavior(X, SuccessfulRoute, Options):
            Options["ProgressCallback"](
                6,
                6,
                4,
                1,
                0,
                SuccessfulRoute,
                "complete",
            )
            return SuccessfulRoute

        def Validate(Routed) -> None:
            if Routed.CandidateX == 0:
                raise RoutingStageError(
                    RoutingFailure(
                        Reason=RoutingFailureReason.ElectricalConflict,
                        Stage="PhysicalSimulation",
                        Detail="first candidate intentionally fails validation",
                    )
                )

        self.RunTwoPlacementFlow(
            RouteBehavior=RouteBehavior,
            RoutedValidationCallback=Validate,
            ProgressCallback=ProgressEvents.append,
        )

        SuccessfulCompletions = [
            Progress
            for Progress in ProgressEvents
            if Progress.Completed >= Progress.Total and Progress.Valid > 0
        ]
        self.assertEqual(len(SuccessfulCompletions), 1)
        self.assertIs(SuccessfulCompletions[0], ProgressEvents[-1])
        self.assertTrue(any(
            Progress.Completed < Progress.Total
            and Progress.Valid == 0
            and "awaiting validation" in Progress.Stage
            for Progress in ProgressEvents
        ))

    def testDeadlineIsCheckedImmediatelyBeforeRoutedValidation(self) -> None:
        Events = []

        def CheckDeadline(
            _Deadline,
            Stage: str,
            Diagnostics: dict[str, object] | None = None,
        ) -> None:
            Events.append(
                ("deadline", Stage, (Diagnostics or {}).get("Phase"))
            )

        def Validate(_Routed) -> None:
            Events.append(("validation", None, None))

        with patch.object(
            RoutingDeadline,
            "RaiseIfExpired",
            autospec=True,
            side_effect=CheckDeadline,
        ):
            self.RunTwoPlacementFlow(RoutedValidationCallback=Validate)

        ValidationIndex = Events.index(("validation", None, None))
        self.assertEqual(
            Events[ValidationIndex - 1],
            ("deadline", "RoutedValidation", "before"),
        )

    def testFirstFullyValidPlacementStopsLaterCandidates(self) -> None:
        RouteOrder = []
        ValidationOrder = []

        def Validate(Routed) -> None:
            ValidationOrder.append(Routed.CandidateX)

        Result, SuccessfulRoutes, DeadlineIdentities = self.RunTwoPlacementFlow(
            RoutedValidationCallback=Validate,
            RouteOrder=RouteOrder,
        )

        self.assertEqual(RouteOrder, [0])
        self.assertEqual(ValidationOrder, [0])
        self.assertIs(Result.Routed, SuccessfulRoutes[0])
        self.assertEqual(len(set(DeadlineIdentities)), 1)

    def testTypedTimeoutStopsBeforeSecondPlacementWithClockRemaining(self) -> None:
        RouteOrder = []

        def RouteBehavior(X, SuccessfulRoute, _Options):
            if X == 0:
                raise RoutingStageError(
                    RoutingFailure(
                        Reason=RoutingFailureReason.RuntimeBudgetExceeded,
                        Stage="Candidate",
                        Detail="native deadline expired before Python clock",
                    )
                )
            return SuccessfulRoute

        with self.assertRaises(RoutingStageError) as Context:
            self.RunTwoPlacementFlow(
                RouteBehavior=RouteBehavior,
                RouteOrder=RouteOrder,
            )

        self.assertEqual(
            Context.exception.Failure.Reason,
            RoutingFailureReason.RuntimeBudgetExceeded,
        )
        self.assertFalse(Context.exception.Failure.Diagnostics["Deadline"]["Expired"])
        self.assertEqual(RouteOrder, [0])

    def testTinyDeadlineStopsAfterRouteWithoutLaterWork(self) -> None:
        RouteOrder = []
        ValidationOrder = []

        def RouteBehavior(_X, SuccessfulRoute, _Options):
            sleep(0.075)
            return SuccessfulRoute

        def Validate(Routed) -> None:
            ValidationOrder.append(Routed.CandidateX)

        Started = monotonic()
        with self.assertRaises(RoutingStageError) as Context:
            self.RunTwoPlacementFlow(
                RouteBehavior=RouteBehavior,
                RoutedValidationCallback=Validate,
                RuntimeBudgetSeconds=0.05,
                RouteOrder=RouteOrder,
            )
        Elapsed = monotonic() - Started

        self.assertEqual(
            Context.exception.Failure.Reason,
            RoutingFailureReason.RuntimeBudgetExceeded,
        )
        self.assertEqual(Context.exception.Failure.Stage, "Routing")
        self.assertEqual(RouteOrder, [0])
        self.assertEqual(ValidationOrder, [])
        self.assertLess(Elapsed, 1.0)

    def testTinyDeadlineStopsAfterValidationWithoutLaterCandidate(self) -> None:
        RouteOrder = []
        ValidationOrder = []

        def Validate(Routed) -> None:
            ValidationOrder.append(Routed.CandidateX)
            sleep(0.075)

        Started = monotonic()
        with self.assertRaises(RoutingStageError) as Context:
            self.RunTwoPlacementFlow(
                RoutedValidationCallback=Validate,
                RuntimeBudgetSeconds=0.05,
                RouteOrder=RouteOrder,
            )
        Elapsed = monotonic() - Started

        self.assertEqual(
            Context.exception.Failure.Reason,
            RoutingFailureReason.RuntimeBudgetExceeded,
        )
        self.assertEqual(Context.exception.Failure.Stage, "RoutedValidation")
        self.assertEqual(RouteOrder, [0])
        self.assertEqual(ValidationOrder, [0])
        self.assertLess(Elapsed, 1.0)

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
