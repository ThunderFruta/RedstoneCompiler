from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
from contextlib import redirect_stderr
from io import StringIO
import tempfile
import unittest
from unittest.mock import patch
from typing import Any

from SVDecoder import Sv
from Compiler.Main import BuildParser, Main
from Compiler.Pipeline import CompileSvToLitematic
from Compiler.Placement.PcbFlow import (
    BuildPlacementGenerationPlan,
    PlaceAndRoutePcb,
    PlacementNeedsDemandDiversity,
    _PlaceAndRoutePcbWithPolicy,
)
from Compiler.Placement.Pcb import (
    BuildTopologicalLevels,
    FindIsomorphicNandClusterMapping,
    OptimizeClusterSlots,
    PcbGatesConflict,
    PlacePcbGraph,
)
from Compiler.Placement.Geometry import BuildPlacedGate
from Compiler.Placement.Rotation import RotatedCellSize
from Compiler.Ir.Models import Gate, GateKind, ModuleIR, NetlistIR
from Compiler.Routing.LocalFirst import (
    BuildCapacityAwareGuidePlan,
    BuildPlacementSolution,
    BuildRipupPlan,
    DeriveRoutingBudget,
    RoutingDemandEstimate,
)
from Compiler.Routing.ChannelPlanner import BuildNetRoutingProfiles
from Compiler.Routing.Actions.Geometry import ValidatePlacedCellElectricalIsolation
from Compiler.Routing.Policy import (
    AdaptiveRoutingPolicy,
    GlobalRoutingPolicy,
    LocalFirstPhysicalDesignPolicy,
    NandPackingPolicy,
    RoutingAcceptanceProfiles,
    RoutingStrategy,
)
from Compiler.Routing.Technology import DefaultRedstoneRoutingTechnology
from Compiler.Routing.Failures import (
    RoutingFailure,
    RoutingFailureReason,
    RoutingStageError,
)
from Compiler.Routing.Models import RoutedDesign
from Compiler.Routing.Reliability import RoutingDeadline
from Compiler.Synthesis.Validation import ValidateNandOnlyDesign
from Compiler.Synthesis.LogicOptimization import OptimizeLogic
from Compiler.Synthesis.NandTransform import ToNandOnly
from SchemEncoder.Writer262 import LoadTemplate


class LocalFirstRouterTests(unittest.TestCase):
    @staticmethod
    def _BuildPlacementFlowFixture(Label: str) -> Any:
        Module = SimpleNamespace(Gates=[SimpleNamespace(Name="Gate")])
        Placed = SimpleNamespace(
            Module=Module,
            PlacedGates=[],
            LocalRouteClaims=(),
            LocalRouteDiagnostics={"Fixture": Label},
            FrozenNetWires={},
            LocalNetBranches={},
            LocalNetTargets={},
        )
        return SimpleNamespace(
            Placed=Placed,
            Clusters=(),
            SignalOrder=(),
            LayerCount=1,
            PackedClusters=(),
        )

    @staticmethod
    def _BuildPlacementFeedbackFixture() -> Any:
        return SimpleNamespace(
            Score=(0,),
            BoundaryOverflow=0,
            PinScarcityCount=0,
            GuideOverflowPeak=0,
            GuideOverflowCells=0,
            PinEscapeConflictCount=0,
            EstimatedGlobalExtensionNodes=0,
            EstimatedGlobalExtensionNets=0,
            PreOwnedNodeCount=0,
        )

    def _RunMockedPlacementFlow(
        self,
        PlaceSideEffect: Any,
        Fingerprints: list[str],
        RouteSideEffect: Any,
        FeedbackSideEffect: Any = None,
        OptimizeRoutingPercentage: bool = False,
    ) -> tuple[Any, Any, Any]:
        Module = SimpleNamespace(Gates=[SimpleNamespace(Name="Gate")])
        Netlist = SimpleNamespace(Top="Fixture", Modules={"Fixture": Module})
        Routed = RoutedDesign(
            Module=Module,
            PlacedGates=[],
            Wires=[],
            Supports=[],
            Repeaters={},
            NetWires={},
        )
        EffectiveRouteSideEffect = RouteSideEffect or Routed
        with (
            patch(
                "Compiler.Placement.PcbFlow.RoutingDeadline.Start",
                wraps=RoutingDeadline.Start,
            ) as StartDeadline,
            patch(
                "Compiler.Placement.PcbFlow.PlacePcbGraph",
                side_effect=PlaceSideEffect,
            ) as PlaceGraph,
            patch(
                "Compiler.Placement.PcbFlow.BuildPlacementFingerprint",
                side_effect=Fingerprints,
            ),
            patch(
                "Compiler.Placement.PcbFlow.BuildPlacementRetentionFingerprint",
                side_effect=Fingerprints,
            ),
            patch(
                "Compiler.Placement.PcbFlow.MeasurePlacementRoutingFeedback",
                side_effect=FeedbackSideEffect,
                return_value=self._BuildPlacementFeedbackFixture(),
            ),
            patch(
                "Compiler.Placement.PcbFlow.RoutePcbDesign",
                side_effect=(
                    EffectiveRouteSideEffect
                    if isinstance(EffectiveRouteSideEffect, list)
                    else None
                ),
                return_value=(
                    Routed
                    if not isinstance(EffectiveRouteSideEffect, list)
                    else None
                ),
            ) as RouteDesign,
            patch(
                "Compiler.Placement.PcbFlow.ValidateNandOnlyDesign",
            ),
            patch(
                "Compiler.Placement.PcbFlow.ValidatePlacedCellElectricalIsolation",
            ),
            patch("Compiler.Placement.PcbFlow.BuildRoutingResources"),
            patch(
                "Compiler.Placement.PcbFlow.MeasurePcbDesign",
                return_value=(1, 1, 1, 1),
            ),
            patch(
                "Compiler.Placement.PcbFlow.BuildLocalFirstSnapshot",
                return_value=SimpleNamespace(ToDictionary=lambda: {}),
            ),
        ):
            Result = _PlaceAndRoutePcbWithPolicy(
                Netlist,
                ProgressCallback=None,
                Policy=replace(
                    LocalFirstPhysicalDesignPolicy,
                    RuntimeBudgetSeconds=5.0,
                    NandPacking=replace(
                        LocalFirstPhysicalDesignPolicy.NandPacking,
                        EnableProactiveInterClusterRelocation=False,
                        DeferUnpackedOracle=False,
                    ),
                    MaterialObjective=replace(
                        LocalFirstPhysicalDesignPolicy.MaterialObjective,
                        OptimizeRoutingPercentage=OptimizeRoutingPercentage,
                        MinimumRemainingRoutingPercentageSearchSeconds=0.001,
                        MinimumRoutingPercentageSelectionNandCount=1,
                    ),
                ),
                Technology=DefaultRedstoneRoutingTechnology,
                RequestedStrategy=RoutingStrategy.Default,
                UsedStrategy=RoutingStrategy.Default,
            )
        return Result, PlaceGraph, (StartDeadline, RouteDesign)

    def testNandOnlyValidationRejectsNonNandLogic(self) -> None:
        Module = ModuleIR(
            Name="Bad",
            Gates=[
                Gate("BadAnd", GateKind.AND, ["Y"], ["A", "B"]),
            ],
        )
        with self.assertRaisesRegex(ValueError, "BadAnd:AND"):
            ValidateNandOnlyDesign(NetlistIR(Top="Bad", Modules={"Bad": Module}))

    def testNandOnlyValidationRequiresOnePlacedCellPerNand(self) -> None:
        Module = ModuleIR(
            Name="Nands",
            Gates=[Gate("N0", GateKind.NAND, ["Y"], ["A", "B"])],
        )
        Reference = NetlistIR(Top="Nands", Modules={"Nands": Module})
        with self.assertRaisesRegex(ValueError, r"missing=\['N0'\]"):
            ValidateNandOnlyDesign(SimpleNamespace(PlacedGates=[]), Reference)

    def testCliAliasesAndDefaultRoutingStrategyParse(self) -> None:
        Parsed = BuildParser().parse_args(
            [
                "--example", "Examples/FullAdder.sv",
                "--topmodule", "FullAdder",
                "--output", "Output/Test",
                "--outputname", "Test",
                "--routing-strategy", "default",
            ]
        )
        self.assertEqual(Parsed.input, Path("Examples/FullAdder.sv"))
        self.assertEqual(Parsed.top, "FullAdder")
        self.assertEqual(Parsed.outputname, "Test")
        self.assertEqual(Parsed.routing_strategy, "default")
        self.assertIsNone(Parsed.routing_deadline_seconds)
        self.assertEqual(
            BuildParser().parse_args([
                "--routing-deadline-seconds", "2.5",
            ]).routing_deadline_seconds,
            2.5,
        )
        for RemovedStrategy in (
            "compatibility",
            "hybrid",
            "authoritative-only",
        ):
            with (
                self.subTest(RemovedStrategy=RemovedStrategy),
                redirect_stderr(StringIO()),
                self.assertRaises(SystemExit),
            ):
                BuildParser().parse_args([
                    "--routing-strategy",
                    RemovedStrategy,
                ])
            with self.assertRaises(ValueError):
                RoutingStrategy.Parse(RemovedStrategy)
        self.assertEqual(
            BuildParser().parse_args([]).routing_strategy,
            "default",
        )
        for InvalidDeadline in ("0", "-1", "nan", "inf"):
            with (
                self.subTest(InvalidDeadline=InvalidDeadline),
                redirect_stderr(StringIO()),
                self.assertRaises(SystemExit),
            ):
                BuildParser().parse_args([
                    "--routing-deadline-seconds",
                    InvalidDeadline,
                ])

    def testCliRoutingFailureReturnsNonzero(self) -> None:
        Failure = RoutingStageError(
            RoutingFailure(
                Reason=RoutingFailureReason.NoBoundaryEscape,
                Stage="PortalGeneration",
                Detail="controlled routing failure",
            )
        )
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Directory = Path(DirectoryValue)
            ErrorOutput = StringIO()
            with (
                patch(
                    "Compiler.Main.CompileSvToLitematic",
                    side_effect=Failure,
                ) as Compile,
                redirect_stderr(ErrorOutput),
            ):
                ReturnCode = Main([
                    "--input",
                    "Examples/FullAdder.sv",
                    "--output",
                    str(Directory / "Failed.litematic"),
                    "--routing-strategy",
                    "default",
                    "--routing-deadline-seconds",
                    "3.5",
                ])

        self.assertEqual(ReturnCode, 1)
        self.assertIn("PortalGeneration:NoBoundaryEscape", ErrorOutput.getvalue())
        self.assertEqual(
            Compile.call_args.kwargs["RoutingDeadlineSeconds"],
            3.5,
        )

    def testDefaultPolicyEnablesAuthoritativeRoutingFeatures(self) -> None:
        self.assertTrue(
            LocalFirstPhysicalDesignPolicy
            .Placement.EnableDemandAwareInterClusterSpacing
        )
        self.assertEqual(LocalFirstPhysicalDesignPolicy.Placement.RoutingSpacing, 5)
        self.assertEqual(LocalFirstPhysicalDesignPolicy.Placement.PinEscapeLength, 1)
        Snapshot = LocalFirstPhysicalDesignPolicy.ToDictionary()
        self.assertFalse(Snapshot["QualityGate"]["Enabled"])
        self.assertEqual(Snapshot["Placement"]["RoutingFeedbackIterations"], 1)
        self.assertTrue(Snapshot["Placement"]["EnableRoutingFeedback"])
        self.assertTrue(
            Snapshot["Placement"]["EnableDemandAwareInterClusterSpacing"]
        )
        self.assertTrue(Snapshot["GlobalRouting"]["EnableCapacityAwareGuides"])
        self.assertEqual(
            LocalFirstPhysicalDesignPolicy.PolicyVersion,
            "physical-design-v16-reconvergent-access",
        )
        self.assertTrue(Snapshot["NandPacking"]["Enabled"])
        self.assertTrue(Snapshot["NandPacking"]["EnableStructuralReuse"])
        self.assertGreaterEqual(
            Snapshot["NandPacking"]["MaximumStructuralReuseMappings"],
            1,
        )
        with self.assertRaises(ValueError):
            NandPackingPolicy(MaximumStructuralReuseMappings=0)
        self.assertTrue(Snapshot["Organization"]["Enabled"])
        self.assertEqual(
            Snapshot["MaterialObjective"]["MinimumComponentFunctionalShare"],
            0.60,
        )

    def testCapacityAwareGuidesAreDeterministicAndBounded(self) -> None:
        def Profile(Source, Target):
            return SimpleNamespace(
                SourceAccessPath=(Source,),
                TargetAccessPaths={Target: (Target,)},
                Span=abs(Target[0] - Source[0]) + abs(Target[2] - Source[2]),
                Fanout=1,
                Criticality=1,
            )

        Profiles = {
            "A": Profile((0, 2, 0), (8, 2, 8)),
            "B": Profile((0, 2, 8), (8, 2, 0)),
        }
        Arguments = (
            Profiles,
            2,
            0,
            0,
            GlobalRoutingPolicy(MaximumRipupPasses=2),
            DefaultRedstoneRoutingTechnology,
            8,
        )
        First = BuildCapacityAwareGuidePlan(*Arguments)
        Second = BuildCapacityAwareGuidePlan(*Arguments)
        self.assertEqual(First.ToDictionary(), Second.ToDictionary())
        self.assertLessEqual(First.OverflowPeak, 1)
        self.assertEqual(set(First.Guides), set(Profiles))

    def testCapacityAwareGuidePlanReusesValidPlacementSeed(self) -> None:
        def Profile(Source, Target):
            return SimpleNamespace(
                SourceAccessPath=(Source,),
                TargetAccessPaths={Target: (Target,)},
                Span=(
                    abs(Target[0] - Source[0])
                    + abs(Target[2] - Source[2])
                ),
                Fanout=1,
                Criticality=1,
            )

        Profiles = {
            "A": Profile((0, 2, 0), (8, 2, 8)),
            "B": Profile((0, 2, 8), (8, 2, 0)),
        }
        Arguments = (
            Profiles,
            2,
            0,
            0,
            GlobalRoutingPolicy(MaximumRipupPasses=2),
            DefaultRedstoneRoutingTechnology,
            8,
        )
        Seed = BuildCapacityAwareGuidePlan(*Arguments)
        Events = []

        Reused = BuildCapacityAwareGuidePlan(
            *Arguments,
            SeedPlan=Seed,
            WorkCheck=Events.append,
        )

        self.assertEqual(Seed.ToDictionary(), Reused.ToDictionary())
        self.assertEqual(
            {
                Event["Signal"]
                for Event in Events
                if Event["Phase"] == "capacity-guide-seed-reuse"
            },
            set(Profiles),
        )

    def testCapacityAwareGuidePlanningPublishesStoppableInnerWork(self) -> None:
        Profile = SimpleNamespace(
            SourceAccessPath=((0, 2, 0),),
            TargetAccessPaths={(8, 2, 8): ((8, 2, 8),)},
            Span=16,
            Fanout=1,
            Criticality=1,
        )
        Phases = []

        def StopDuringSelection(Diagnostics):
            Phases.append(Diagnostics["Phase"])
            if Diagnostics["Phase"] == "capacity-guide-selection":
                raise RuntimeError("feedback slice expired")

        with self.assertRaisesRegex(RuntimeError, "feedback slice expired"):
            BuildCapacityAwareGuidePlan(
                {"A": Profile},
                2,
                0,
                0,
                GlobalRoutingPolicy(MaximumRipupPasses=2),
                DefaultRedstoneRoutingTechnology,
                8,
                WorkCheck=StopDuringSelection,
            )

        self.assertIn("capacity-guide-profile", Phases)
        self.assertIn("capacity-guide-lane", Phases)
        self.assertEqual(Phases[-1], "capacity-guide-selection")

    def testCapacityGuideTreatsComponentInteriorAsObstacle(self) -> None:
        Profile = SimpleNamespace(
            SourceAccessPath=((0, 2, 0),),
            TargetAccessPaths={(20, 2, 0): ((20, 2, 0),)},
            Span=20,
            Fanout=1,
            Criticality=1,
        )
        Plan = BuildCapacityAwareGuidePlan(
            {"Global": Profile},
            2,
            0,
            0,
            GlobalRoutingPolicy(CandidateLaneCount=5),
            DefaultRedstoneRoutingTechnology,
            8,
            ComponentObstacleBounds=(5, 15, -1, 1),
        )

        self.assertFalse(any(
            5 <= X <= 15 and -1 <= Z <= 1
            for X, Z in Plan.Guides["Global"]
        ))

    def testCapacityGuideConstructionCanStopInsideLongSegment(self) -> None:
        Profile = SimpleNamespace(
            SourceAccessPath=((0, 2, 0),),
            TargetAccessPaths={(1024, 2, 0): ((1024, 2, 0),)},
            Span=1024,
            Fanout=1,
            Criticality=1,
        )
        Observed = []

        def StopDuringSegment(Diagnostics):
            Observed.append(Diagnostics)
            if Diagnostics["Phase"] == "capacity-guide-segment":
                raise RuntimeError("guide deadline expired")

        with self.assertRaisesRegex(RuntimeError, "guide deadline expired"):
            BuildCapacityAwareGuidePlan(
                {"A": Profile},
                1,
                0,
                0,
                GlobalRoutingPolicy(CandidateLaneCount=1),
                DefaultRedstoneRoutingTechnology,
                8,
                WorkCheck=StopDuringSegment,
            )

        self.assertEqual(Observed[-1]["ProcessedSegmentPositions"], 256)

    def testCapacityGuideRipupCanStopInsideOverflowScan(self) -> None:
        def Profile():
            return SimpleNamespace(
                SourceAccessPath=((0, 2, 0),),
                TargetAccessPaths={(600, 2, 0): ((600, 2, 0),)},
                Span=600,
                Fanout=1,
                Criticality=1,
            )

        Observed = []

        def StopDuringOverflow(Diagnostics):
            Observed.append(Diagnostics)
            if Diagnostics["Phase"] == "capacity-guide-overflow":
                raise RuntimeError("ripup deadline expired")

        with self.assertRaisesRegex(RuntimeError, "ripup deadline expired"):
            BuildCapacityAwareGuidePlan(
                {"A": Profile(), "B": Profile()},
                1,
                0,
                0,
                GlobalRoutingPolicy(
                    CandidateLaneCount=1,
                    CorridorCapacity=1,
                    MaximumRipupPasses=2,
                ),
                DefaultRedstoneRoutingTechnology,
                8,
                WorkCheck=StopDuringOverflow,
            )

        self.assertEqual(Observed[-1]["ProcessedResources"], 256)

    def testAdaptiveBudgetsChangeSmoothlyAcrossFormerProfileThreshold(self) -> None:
        Budgets = []
        for NetCount in (15, 16, 17):
            Demand = RoutingDemandEstimate(
                NandCount=32,
                RoutableNetCount=NetCount,
                TerminalCount=2 * NetCount,
                MaximumFanout=2,
                TotalHpwl=10 * NetCount,
                BoundaryDemand=NetCount,
                PinScarcityCount=0,
                ProjectedCorridorDemand=20 * NetCount,
                CongestionEstimate=0.5,
            )
            Budgets.append(DeriveRoutingBudget(
                Demand,
                LocalFirstPhysicalDesignPolicy,
                DefaultRedstoneRoutingTechnology,
            ))
        self.assertEqual(len({Value.PortalsPerTerminal for Value in Budgets}), 1)
        self.assertLessEqual(
            Budgets[0].CandidatesPerNet,
            Budgets[1].CandidatesPerNet,
        )
        self.assertLessEqual(
            Budgets[1].CandidatesPerNet,
            Budgets[2].CandidatesPerNet,
        )
        self.assertLessEqual(
            Budgets[0].AssignmentExpansions,
            Budgets[-1].AssignmentExpansions,
        )

    def testAdaptivePolicyRejectsNonGrowingOrUnboundedControls(self) -> None:
        with self.assertRaisesRegex(ValueError, "PortalGrowthFactor"):
            AdaptiveRoutingPolicy(PortalGrowthFactor=1)
        with self.assertRaisesRegex(ValueError, "cannot exceed"):
            AdaptiveRoutingPolicy(
                InitialAssignmentExpansions=100,
                MaximumAssignmentExpansions=99,
            )

    def testPlacementGenerationPlanBoundsAndDeduplicatesRecipes(self) -> None:
        Plan = BuildPlacementGenerationPlan(LocalFirstPhysicalDesignPolicy)
        self.assertEqual(
            [Request.SourceGenerator for Request in Plan.PrimaryRequests],
            [
                "row-beam",
                "unpacked",
            ],
        )
        self.assertEqual(
            [Request.SourceGenerator for Request in Plan.DeferredRequests],
            [
                "row-beam-conflict-relocation",
                "row-beam-direct-only",
                "unpacked-spacing-6",
                "unpacked-configured-spacing",
                "configured-packing",
                "graph-beam-direct-only",
                "spacing-4",
                "spacing-6",
            ],
        )
        self.assertEqual(Plan.MaximumAttempts, 10)
        self.assertEqual(Plan.PrimaryRequests[1].RoutingSpacing, 4)
        NonRelocationRecipeKeys = [
            (Request.RoutingSpacing, Request.PackingPolicy)
            for Request in Plan.PrimaryRequests + Plan.DeferredRequests
            if Request.SourceGenerator != "row-beam-conflict-relocation"
        ]
        self.assertEqual(
            len(NonRelocationRecipeKeys),
            len(set(NonRelocationRecipeKeys)),
        )
        self.assertEqual(
            (
                Plan.DeferredRequests[0].RoutingSpacing,
                Plan.DeferredRequests[0].PackingPolicy,
            ),
            (
                Plan.PrimaryRequests[0].RoutingSpacing,
                Plan.PrimaryRequests[0].PackingPolicy,
            ),
        )

        PackedFirstPlan = BuildPlacementGenerationPlan(
            LocalFirstPhysicalDesignPolicy,
            PreferPackedPlacements=True,
        )
        self.assertEqual(
            [
                Request.SourceGenerator
                for Request in PackedFirstPlan.PrimaryRequests
            ],
            ["row-beam"],
        )
        self.assertEqual(
            [
                Request.SourceGenerator
                for Request in PackedFirstPlan.DeferredRequests[:4]
            ],
            [
                "row-beam-conflict-relocation",
                "row-beam-direct-only",
                "unpacked",
                "unpacked-configured-spacing",
            ],
        )

        DirectOnlyPolicy = replace(
            LocalFirstPhysicalDesignPolicy,
            NandPacking=replace(
                LocalFirstPhysicalDesignPolicy.NandPacking,
                MaximumLocalRouteLength=(
                    LocalFirstPhysicalDesignPolicy.NandPacking
                    .DirectConnectMaximumLength
                ),
            ),
        )
        DirectOnlyPlan = BuildPlacementGenerationPlan(DirectOnlyPolicy)
        self.assertEqual(
            [
                Request.SourceGenerator
                for Request in DirectOnlyPlan.PrimaryRequests
            ],
            ["row-beam", "unpacked"],
        )
        self.assertEqual(
            DirectOnlyPlan.DeferredRequests[0].SourceGenerator,
            "row-beam-conflict-relocation",
        )

    def testGraphBeamIsNotConstructedAfterPrimaryCandidateRoutes(self) -> None:
        Placements = [
            self._BuildPlacementFlowFixture("row-beam"),
            self._BuildPlacementFlowFixture("unpacked"),
        ]
        _, PlaceGraph, _ = self._RunMockedPlacementFlow(
            Placements,
            ["a-primary", "b-primary"],
            None,
        )

        self.assertEqual(PlaceGraph.call_count, 2)
        PackingPolicies = [
            Call.kwargs["PackingPolicy"] for Call in PlaceGraph.call_args_list
        ]
        self.assertTrue(PackingPolicies[0].Enabled)
        self.assertFalse(PackingPolicies[0].GraphBeamEnabled)
        self.assertFalse(PackingPolicies[1].Enabled)
        self.assertFalse(any(
            Value.Enabled and Value.GraphBeamEnabled
            for Value in PackingPolicies
        ))

    def testFullFootprintSelectionUsesFinalRenderedComposition(self) -> None:
        Placements = [
            self._BuildPlacementFlowFixture("row-beam"),
            self._BuildPlacementFlowFixture("unpacked"),
        ]
        Composition = lambda Share, RouteBlocks, Footprint: SimpleNamespace(
            RoutingFunctionalShare=Share,
            RoutingOwnedFunctionalBlocks=RouteBlocks,
            Footprint=Footprint,
            NonAirBlocks=200,
            Width=20,
            Height=5,
            Depth=10,
            XYFootprint=100,
            FullFootprint=Footprint * 5,
        )
        with patch(
            "SchemEncoder.Writer262.BuildLitematicBlockMap",
            side_effect=[
                SimpleNamespace(Composition=Composition(0.70, 140, 200)),
                SimpleNamespace(Composition=Composition(0.55, 150, 210)),
            ],
        ):
            Result, _PlaceGraph, RouteDesign = self._RunMockedPlacementFlow(
                Placements,
                ["first", "second"],
                None,
                OptimizeRoutingPercentage=True,
            )

        Selection = Result.Routed.RoutingControlEffectiveness[
            "RoutingPercentageSelection"
        ]
        self.assertEqual(RouteDesign[1].call_count, 2)
        self.assertEqual(Selection["CandidateCount"], 2)
        self.assertEqual(Selection["Selected"]["FullFootprint"], 1000)
        self.assertEqual(
            Result.Routed.RoutingControlEffectiveness[
                "SelectedPlacementCandidate"
            ]["PlacementFingerprint"],
            "first",
        )

    def testConfiguredGraphBeamRunsAfterEveryPrimaryPlacementFails(self) -> None:
        GraphPlacement = self._BuildPlacementFlowFixture("graph-beam")
        PlacementResults = [
            ValueError("row beam rejected"),
            ValueError("unpacked placement rejected"),
            ValueError("direct-only row beam rejected"),
            ValueError("configured-spacing unpacked rejected"),
            ValueError("wider-spacing unpacked rejected"),
            ValueError("configured packing rejected"),
            GraphPlacement,
        ]
        _, PlaceGraph, _ = self._RunMockedPlacementFlow(
            PlacementResults,
            ["configured-graph"],
            None,
        )

        self.assertEqual(PlaceGraph.call_count, 7)
        PackingPolicies = [
            Call.kwargs["PackingPolicy"] for Call in PlaceGraph.call_args_list
        ]
        self.assertTrue(PackingPolicies[0].Enabled)
        self.assertFalse(PackingPolicies[0].GraphBeamEnabled)
        self.assertFalse(PackingPolicies[1].Enabled)
        self.assertTrue(PackingPolicies[2].Enabled)
        self.assertFalse(PackingPolicies[2].GraphBeamEnabled)
        self.assertFalse(PackingPolicies[3].Enabled)
        self.assertFalse(PackingPolicies[4].Enabled)
        self.assertTrue(PackingPolicies[5].Enabled)
        self.assertTrue(PackingPolicies[5].GraphBeamEnabled)
        self.assertTrue(PackingPolicies[6].Enabled)
        self.assertTrue(PackingPolicies[6].GraphBeamEnabled)

    def testRetainedCandidatesUseSlicesOfOneAbsoluteRoutingDeadline(self) -> None:
        Placements = [
            self._BuildPlacementFlowFixture("first"),
            self._BuildPlacementFlowFixture("second"),
        ]
        Failure = RoutingStageError(
            RoutingFailure(
                Reason=RoutingFailureReason.TrackAssignmentConflict,
                Stage="TrackAssignment",
                Detail="force the next retained placement",
            )
        )
        Routed = RoutedDesign(
            Module=SimpleNamespace(Gates=[]),
            PlacedGates=[],
            Wires=[],
            Supports=[],
            Repeaters={},
            NetWires={},
        )
        _, _, (StartDeadline, RouteDesign) = self._RunMockedPlacementFlow(
            Placements,
            ["a-first", "b-second"],
            [Failure, Routed],
        )

        self.assertEqual(StartDeadline.call_count, 1)
        self.assertEqual(RouteDesign.call_count, 2)
        FirstDeadline = RouteDesign.call_args_list[0].kwargs["Deadline"]
        SecondDeadline = RouteDesign.call_args_list[1].kwargs["Deadline"]
        self.assertIsNot(FirstDeadline, SecondDeadline)
        self.assertEqual(FirstDeadline.StartedAt, SecondDeadline.StartedAt)
        self.assertLess(FirstDeadline.ExpiresAt, SecondDeadline.ExpiresAt)
        FirstPolicy = RouteDesign.call_args_list[0].kwargs["Policy"]
        self.assertLessEqual(
            FirstPolicy.AdaptiveRouting.MaximumRuntimeSeconds,
            2.6,
        )

    def testDeferredPlacementAlternativesAreDemandAware(self) -> None:
        def Candidate(
            Fingerprint: str,
            Score: tuple[int, ...],
            BoundaryOverflow: int,
        ) -> SimpleNamespace:
            return SimpleNamespace(
                FeedbackScore=Score,
                JointExactScore=(),
                Placement=SimpleNamespace(
                    PackedClusters=(1,),
                    Placed=SimpleNamespace(LocalRouteClaims=(1,)),
                ),
                RoutingSpacing=6,
                PlacementFingerprint=Fingerprint,
                BoundaryOverflow=BoundaryOverflow,
                PinScarcityCount=0,
                GuideOverflowPeak=0,
                GuideOverflowCells=0,
                PinEscapeConflictCount=0,
            )

        Clear = Candidate("clear", (0,), 0)
        Pressured = Candidate("pressured", (1,), 2)
        self.assertFalse(PlacementNeedsDemandDiversity([Clear], 6))
        self.assertTrue(PlacementNeedsDemandDiversity([Pressured], 6))
        self.assertFalse(
            PlacementNeedsDemandDiversity([Pressured, Clear], 6)
        )

    def testPressuredRetainedPlacementRoutesBeforeDeferredAlternative(self) -> None:
        Placements = [
            self._BuildPlacementFlowFixture("row-beam"),
            self._BuildPlacementFlowFixture("unpacked"),
            self._BuildPlacementFlowFixture("deferred-clear"),
        ]
        Failure = RoutingStageError(
            RoutingFailure(
                Reason=RoutingFailureReason.TrackAssignmentConflict,
                Stage="TrackAssignment",
                Detail="force placement failover",
            )
        )
        Routed = RoutedDesign(
            Module=SimpleNamespace(Gates=[]),
            PlacedGates=[],
            Wires=[],
            Supports=[],
            Repeaters={},
            NetWires={},
        )

        def Feedback(Placement, *_Arguments):
            Label = Placement.Placed.LocalRouteDiagnostics["Fixture"]
            Value = self._BuildPlacementFeedbackFixture()
            return SimpleNamespace(
                **{
                    **vars(Value),
                    "Score": ((1,) if Label == "row-beam" else (0,)),
                    "BoundaryOverflow": (1 if Label == "row-beam" else 0),
                }
            )

        _Result, PlaceGraph, (_StartDeadline, RouteDesign) = (
            self._RunMockedPlacementFlow(
                Placements,
                ["row-beam", "unpacked", "deferred-clear"],
                [Failure, Routed],
                FeedbackSideEffect=Feedback,
            )
        )

        self.assertEqual(PlaceGraph.call_count, 2)
        self.assertEqual(RouteDesign.call_count, 2)
        self.assertEqual(
            RouteDesign.call_args_list[1].args[0]
            .Placed.LocalRouteDiagnostics["Fixture"],
            "row-beam",
        )

    def testCyclicContractedClusterGraphUsesCompactPlacement(self) -> None:
        Module = ModuleIR(
            Name="CyclicClusters",
            Gates=[
                Gate("InputA", GateKind.INPUT, ["A"], []),
                Gate("N0", GateKind.NAND, ["N0Out"], ["A", "A"]),
                Gate("N1", GateKind.NAND, ["N1Out"], ["N0Out", "N0Out"]),
                Gate("N2", GateKind.NAND, ["Y"], ["N1Out", "N1Out"]),
            ],
            Inputs=["A"],
            Outputs=["Y"],
        )
        Assignment, Columns, Rows = OptimizeClusterSlots(
            Module,
            (("N0", "N2"), ("N1",)),
            BuildTopologicalLevels(Module),
        )
        self.assertEqual(set(Assignment), {0, 1})
        self.assertLessEqual(Columns * Rows, 2)

    def testAbsoluteMaterialGatesAreBenchmarkOnly(self) -> None:
        self.assertTrue(LocalFirstPhysicalDesignPolicy.MaterialObjective.Enabled)
        self.assertFalse(
            LocalFirstPhysicalDesignPolicy
            .MaterialObjective.OptimizeRoutingPercentage
        )
        self.assertEqual(
            RoutingAcceptanceProfiles["FullAdder"].MaximumFootprint,
            600,
        )
        self.assertIsNone(
            RoutingAcceptanceProfiles["RippleCarryAdder4"].MaximumFootprint
        )
        self.assertEqual(
            RoutingAcceptanceProfiles[
                "RippleCarryAdder8"
            ].MaximumRuntimeSeconds,
            30.0,
        )

    def testRepeatedNandStructureIsDetectedWithoutCircuitNames(self) -> None:
        Module = ModuleIR(
            Name="ArbitraryRepeatedGraph",
            Inputs=["p", "q", "r", "s"],
            Outputs=["left", "right"],
            Gates=[
                Gate("Alpha", GateKind.NAND, ["u"], ["p", "q"]),
                Gate("Beta", GateKind.NAND, ["left"], ["u", "p"]),
                Gate("Gamma", GateKind.NAND, ["v"], ["r", "s"]),
                Gate("Delta", GateKind.NAND, ["right"], ["v", "r"]),
            ],
        )
        Match = FindIsomorphicNandClusterMapping(
            Module,
            ("Alpha", "Beta"),
            ("Gamma", "Delta"),
        )
        self.assertIsNotNone(Match)
        self.assertEqual(
            Match[1],
            {"Alpha": "Gamma", "Beta": "Delta"},
        )
        Module.Gates[-1].Inputs = ["r", "v"]
        self.assertIsNone(
            FindIsomorphicNandClusterMapping(
                Module,
                ("Alpha", "Beta"),
                ("Gamma", "Delta"),
            )
        )

    def testVerticalDeckEscapesDoNotCreatePlanarFeedbackConflicts(self) -> None:
        Lower = BuildPlacedGate(
            Gate("Lower", GateKind.NAND, ["LowerOut"], ["A", "A"]),
            0,
            1,
            0,
            0,
        )
        Upper = BuildPlacedGate(
            Gate("Upper", GateKind.NAND, ["UpperOut"], ["B", "B"]),
            0,
            7,
            0,
            0,
        )
        Placement = SimpleNamespace(PlacedGates=[Lower, Upper])

        Metrics = BuildPlacementSolution(
            Placement,
            LocalFanoutDistance=8,
        )

        self.assertEqual(Metrics.PinEscapeConflictCount, 0)

    def testPackedNandsRemainIndependentAndDeterministic(self) -> None:
        with tempfile.TemporaryDirectory() as Directory:
            Netlist = ToNandOnly(
                OptimizeLogic(
                    Sv.ParseSvToNetlist(
                        InputPath=Path("Examples/FullAdder.sv"),
                        TopModule="FullAdder",
                        Workdir=Path(Directory),
                    )
                )
            )
        Arguments = dict(
            RoutingSpacing=LocalFirstPhysicalDesignPolicy.Placement.RoutingSpacing,
            PlacementPolicy=LocalFirstPhysicalDesignPolicy.Placement,
            PackingPolicy=replace(
                LocalFirstPhysicalDesignPolicy.NandPacking,
                RequireCompleteLocalFanoutClaims=False,
            ),
        )
        First = PlacePcbGraph(Netlist, **Arguments)
        Second = PlacePcbGraph(Netlist, **Arguments)
        FirstPlacement = [
            (Gate.Name, Gate.X, Gate.Y, Gate.Z, Gate.Rotation, Gate.MirrorX)
            for Gate in First.Placed.PlacedGates
        ]
        SecondPlacement = [
            (Gate.Name, Gate.X, Gate.Y, Gate.Z, Gate.Rotation, Gate.MirrorX)
            for Gate in Second.Placed.PlacedGates
        ]
        self.assertEqual(FirstPlacement, SecondPlacement)
        self.assertEqual(len(First.PackedClusters), 1)
        LogicalNands = {
            Gate.Name
            for Gate in Netlist.Modules[Netlist.Top].Gates
            if Gate.Kind == GateKind.NAND
        }
        self.assertEqual(set(First.PackedClusters[0].MemberNands), LogicalNands)
        self.assertTrue(First.Placed.FrozenNetWires)
        self.assertTrue(First.Placed.LocalRouteClaims)
        Profiles = BuildNetRoutingProfiles(First.Placed, AccessLength=1)
        Seeded = [
            Profile for Profile in Profiles.values()
            if Profile.Seed is not None and Profile.Seed.LocalClaims
        ]
        self.assertTrue(Seeded)
        self.assertTrue(any(Profile.Seed.ConnectedTargets for Profile in Seeded))
        AccessClaims = {
            Profile.Signal: Profile.Seed.PreOwnedResources
            for Profile in Seeded
        }
        self.assertTrue(all(AccessClaims.values()))
        self.assertFalse(any(
            PcbGatesConflict(FirstGate, SecondGate)
            for GateIndex, FirstGate in enumerate(First.Placed.PlacedGates)
            for SecondGate in First.Placed.PlacedGates[GateIndex + 1 :]
        ))
        ValidatePlacedCellElectricalIsolation(First.Placed)

    def testRowBeamPackedPlacementIsExactLegal(self) -> None:
        with tempfile.TemporaryDirectory() as Directory:
            Netlist = ToNandOnly(
                OptimizeLogic(
                    Sv.ParseSvToNetlist(
                        InputPath=Path("Examples/FullAdder.sv"),
                        TopModule="FullAdder",
                        Workdir=Path(Directory),
                    )
                )
            )
        DemandAwarePlacementPolicy = replace(
            LocalFirstPhysicalDesignPolicy.Placement,
            EnableDemandAwareInterClusterSpacing=True,
        )
        Placement = PlacePcbGraph(
            Netlist,
            RoutingSpacing=LocalFirstPhysicalDesignPolicy.Placement.RoutingSpacing,
            PlacementPolicy=DemandAwarePlacementPolicy,
            PackingPolicy=replace(
                LocalFirstPhysicalDesignPolicy.NandPacking,
                GraphBeamEnabled=False,
            ),
        )
        self.assertFalse(any(
            PcbGatesConflict(FirstGate, SecondGate)
            for GateIndex, FirstGate in enumerate(Placement.Placed.PlacedGates)
            for SecondGate in Placement.Placed.PlacedGates[GateIndex + 1 :]
        ))
        ValidatePlacedCellElectricalIsolation(Placement.Placed)

    def testPackedTerminalPlacementGroupsInputsByNandCluster(self) -> None:
        with tempfile.TemporaryDirectory() as Directory:
            Netlist = ToNandOnly(
                OptimizeLogic(
                    Sv.ParseSvToNetlist(
                        InputPath=Path("Examples/RippleCarryAdder4.sv"),
                        TopModule="RippleCarryAdder4",
                        Workdir=Path(Directory),
                    )
                )
            )
        DemandAwarePlacementPolicy = replace(
            LocalFirstPhysicalDesignPolicy.Placement,
            EnableDemandAwareInterClusterSpacing=True,
        )
        Placement = PlacePcbGraph(
            Netlist,
            RoutingSpacing=LocalFirstPhysicalDesignPolicy.Placement.RoutingSpacing,
            PlacementPolicy=DemandAwarePlacementPolicy,
            PackingPolicy=LocalFirstPhysicalDesignPolicy.NandPacking,
        )
        Inputs = [
            Gate
            for Gate in Placement.Placed.PlacedGates
            if Gate.Kind == "INPUT"
        ]
        self.assertEqual(
            {(Gate.Name, Gate.Outputs[0]) for Gate in Inputs},
            {
                ("InputA0", "A0"),
                ("InputB0", "B0"),
                ("InputCarryIn", "CarryIn"),
                ("InputA1", "A1"),
                ("InputB1", "B1"),
                ("InputA2", "A2"),
                ("InputB2", "B2"),
                ("InputA3", "A3"),
                ("InputB3", "B3"),
            },
        )
        InternalGates = [
            Gate
            for Gate in Placement.Placed.PlacedGates
            if Gate.Kind == "NAND"
        ]
        CoreMinimumX = min(Gate.X for Gate in InternalGates)
        CoreMaximumX = max(
            Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0] - 1
            for Gate in InternalGates
        )
        CoreMinimumZ = min(Gate.Z for Gate in InternalGates)
        CoreMaximumZ = max(
            Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1] - 1
            for Gate in InternalGates
        )
        for Terminal in (
            Gate
            for Gate in Placement.Placed.PlacedGates
            if Gate.Kind in {"INPUT", "OUTPUT"}
        ):
            TerminalWidth, TerminalDepth = RotatedCellSize(
                Terminal.Kind,
                Terminal.Rotation,
            )
            self.assertTrue(
                Terminal.X + TerminalWidth - 1 < CoreMinimumX
                or Terminal.X > CoreMaximumX
                or Terminal.Z + TerminalDepth - 1 < CoreMinimumZ
                or Terminal.Z > CoreMaximumZ
            )
        GapPlan = Placement.Placed.LocalRouteDiagnostics[
            "__InterClusterGaps__"
        ]
        self.assertTrue(GapPlan["Enabled"])
        self.assertTrue(GapPlan["BoundaryDemand"])
        for Demand in GapPlan["BoundaryDemand"]:
            SpacingByBoundary = (
                GapPlan["ColumnExtraSpacing"]
                if Demand["Axis"] == "X"
                else GapPlan["RowExtraSpacing"]
            )
            self.assertEqual(
                SpacingByBoundary[str(Demand["BoundaryIndex"])],
                min(
                    LocalFirstPhysicalDesignPolicy.Placement.RoutingSpacing,
                    Demand["RequiredCorridorLanes"]
                    * LocalFirstPhysicalDesignPolicy
                    .Placement.DemandAwareBoundaryTrackPitch,
                ),
            )
        self.assertEqual(
            Placement.Clusters[0],
            (
                "NandGate0",
                "NandGate1",
                "NandGate2",
                "NandGate3",
                "NandGate4",
                "NandGate5",
                "NandGate6",
                "NandGate7",
                "NandGate8",
            ),
        )

    def testPackedLocalClaimsNeverFreezeUnrefreshedLongBranches(self) -> None:
        with tempfile.TemporaryDirectory() as Directory:
            Netlist = ToNandOnly(
                OptimizeLogic(
                    Sv.ParseSvToNetlist(
                        InputPath=Path("Examples/RippleCarryAdder4.sv"),
                        TopModule="RippleCarryAdder4",
                        Workdir=Path(Directory),
                    )
                )
            )
        Placement = PlacePcbGraph(
            Netlist,
            RoutingSpacing=LocalFirstPhysicalDesignPolicy.Placement.RoutingSpacing,
            PlacementPolicy=LocalFirstPhysicalDesignPolicy.Placement,
            PackingPolicy=LocalFirstPhysicalDesignPolicy.NandPacking,
        )
        self.assertEqual(len(Placement.PackedClusters), 4)
        self.assertEqual(
            [
                Cluster.ReusedFromClusterId
                for Cluster in Placement.PackedClusters
            ],
            [None, 0, 0, 0],
        )
        self.assertEqual(
            len({
                Cluster.StructuralSignature
                for Cluster in Placement.PackedClusters
            }),
            1,
        )
        self.assertFalse(any(
            PcbGatesConflict(FirstGate, SecondGate)
            for GateIndex, FirstGate in enumerate(Placement.Placed.PlacedGates)
            for SecondGate in Placement.Placed.PlacedGates[GateIndex + 1 :]
        ))
        ValidatePlacedCellElectricalIsolation(Placement.Placed)
        self.assertTrue(all(
            len(Cluster.StructuralMapping or {}) == 9
            for Cluster in Placement.PackedClusters[1:]
        ))
        WithoutReuse = PlacePcbGraph(
            Netlist,
            RoutingSpacing=LocalFirstPhysicalDesignPolicy.Placement.RoutingSpacing,
            PlacementPolicy=LocalFirstPhysicalDesignPolicy.Placement,
            PackingPolicy=replace(
                LocalFirstPhysicalDesignPolicy.NandPacking,
                EnableStructuralReuse=False,
            ),
        )
        self.assertTrue(all(
            Cluster.ReusedFromClusterId is None
            for Cluster in WithoutReuse.PackedClusters
        ))
        for Claim in Placement.Placed.LocalRouteClaims:
            if Claim.RepeaterReservations:
                continue
            Adjacency = {Position: set() for Position in Claim.Nodes}
            for First, Second in Claim.Edges:
                Adjacency[First].add(Second)
                Adjacency[Second].add(First)
            Distances = {Claim.Root: 0}
            Pending = [Claim.Root]
            for Current in Pending:
                for Neighbor in Adjacency.get(Current, ()):
                    if Neighbor in Distances:
                        continue
                    Distances[Neighbor] = Distances[Current] + 1
                    Pending.append(Neighbor)
            self.assertTrue(set(Claim.ConnectedTargets).issubset(Distances))
            self.assertLess(
                max(
                    (Distances[Target] for Target in Claim.ConnectedTargets),
                    default=0,
                ),
                DefaultRedstoneRoutingTechnology.MaximumUnrefreshedDustLength,
            )

    def testPlacementLocalityAndRipupContractsAreDeterministic(self) -> None:
        Placed = SimpleNamespace(
            PlacedGates=[
                SimpleNamespace(
                    Name="Source", Kind="INPUT", X=0, Z=0,
                    Inputs=[], Outputs=["A"], OutputPin=(0, 1, 0),
                ),
                SimpleNamespace(
                    Name="Target", Kind="OUTPUT", X=12, Z=0,
                    Inputs=["A"], Outputs=[], OutputPin=None,
                    InputPins=[(12, 1, 0)], InputDirections=[(-1, 0, 0)],
                ),
            ]
        )
        Solution = BuildPlacementSolution(Placed, LocalFanoutDistance=8)
        self.assertEqual(Solution.Hpwl, 12)
        self.assertEqual(Solution.LocalFanoutPenalty, 4)
        First = BuildRipupPlan(("B", "A"), {(2, 3): 4}, 2)
        Second = BuildRipupPlan(("A", "B"), {(2, 3): 4}, 3, First.Signals)
        self.assertEqual(First.Signals, ("A", "B"))
        self.assertTrue(Second.Stagnated)

    def testRemovedHybridStrategyIsRejectedBeforeRouting(self) -> None:
        with patch(
            "Compiler.Placement.PcbFlow._PlaceAndRoutePcbWithPolicy",
        ) as Execute:
            with self.assertRaises(ValueError):
                PlaceAndRoutePcb(
                    SimpleNamespace(),
                    Strategy="hybrid",
                )
        Execute.assert_not_called()

    def testRemovedCompatibilityStrategyIsRejectedBeforeRouting(self) -> None:
        with patch(
            "Compiler.Placement.PcbFlow._PlaceAndRoutePcbWithPolicy",
        ) as Execute:
            with self.assertRaises(ValueError):
                PlaceAndRoutePcb(
                    SimpleNamespace(),
                    Strategy="compatibility",
                )
        Execute.assert_not_called()

    def testCliDeadlineOverridesEffectivePolicyWithoutMutatingCanonicalPolicy(
        self,
    ) -> None:
        Expected = object()
        with patch(
            "Compiler.Placement.PcbFlow._PlaceAndRoutePcbWithPolicy",
            return_value=Expected,
        ) as Execute:
            Result = PlaceAndRoutePcb(
                SimpleNamespace(),
                Strategy=RoutingStrategy.Default,
                RoutingDeadlineSeconds=4.25,
            )

        self.assertIs(Result, Expected)
        EffectivePolicy = Execute.call_args.kwargs["Policy"]
        self.assertEqual(EffectivePolicy.RuntimeBudgetSeconds, 4.25)
        self.assertEqual(
            EffectivePolicy.AdaptiveRouting.MaximumRuntimeSeconds,
            4.25,
        )
        self.assertEqual(
            LocalFirstPhysicalDesignPolicy.RuntimeBudgetSeconds,
            120.0,
        )
        with self.assertRaisesRegex(ValueError, "finite and positive"):
            PlaceAndRoutePcb(
                SimpleNamespace(),
                RoutingDeadlineSeconds=0,
            )

    def testNewRouterFirstSurfacesLocalRoutingFailure(self) -> None:
        with patch(
            "Compiler.Placement.PcbFlow._PlaceAndRoutePcbWithPolicy",
            side_effect=ValueError("forced local failure"),
        ) as Execute:
            with self.assertRaisesRegex(ValueError, "forced local failure"):
                PlaceAndRoutePcb(
                    SimpleNamespace(),
                    Strategy=RoutingStrategy.Default,
                )
        self.assertEqual(Execute.call_count, 1)

    def testNewRouterFullAdderWritesCompleteDiagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as Directory:
            Root = Path(Directory)
            Result = CompileSvToLitematic(
                InputPath=Path("Examples/FullAdder.sv"),
                TopModule="FullAdder",
                OutputPath=Root / "FullAdder.litematic",
                DiagramPath=Root / "FullAdder.Nand.json",
                Workdir=Root / "Frontend",
                RoutingStrategyValue=RoutingStrategy.Default,
            )
            Diagnostics = Result.PhysicalDesignPath.read_text(encoding="utf-8")
            EmittedBlockCount = len(LoadTemplate(Result.OutputPath).Blocks)
        self.assertTrue(Result.TruthTablePassed)
        self.assertEqual(Result.UsedStrategy, "default")
        self.assertIn('"UnresolvedClaimCount": 0', Diagnostics)
        self.assertIn('"PlanningContracts"', Diagnostics)
        self.assertIn('"BlockComposition"', Diagnostics)
        self.assertIn('"RoutingFootprint"', Diagnostics)
        self.assertIn('"RoutingDemandEstimate"', Diagnostics)
        self.assertIn('"DerivedRoutingBudget"', Diagnostics)
        self.assertIn('"NormalizedQuality"', Diagnostics)
        self.assertEqual(
            Result.BlockComposition.NonAirBlocks,
            EmittedBlockCount,
        )
        self.assertAlmostEqual(
            Result.BlockComposition.ComponentFunctionalShare
            + Result.BlockComposition.RoutingFunctionalShare,
            1.0,
        )


if __name__ == "__main__":
    unittest.main()
