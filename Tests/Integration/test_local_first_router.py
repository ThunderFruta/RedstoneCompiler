from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import tempfile
import unittest
from unittest.mock import patch

from Formats.SystemVerilog import Sv
from App.CompilerCli import BuildParser, Main, PrintFabricFailureSummary, PrintRoutingFailureSummary
from Compilation.Pipeline import CompileSvToLitematic
from PhysicalDesign.Orchestration.Demand import BuildPlacementGenerationPlan
from PhysicalDesign.Orchestration.Preparation import PlacementNeedsDemandDiversity
from PhysicalDesign.Orchestration.Runner import PlaceAndRoutePcb
from PhysicalDesign.Placement.Engine.Clustering import BuildTopologicalLevels, FindIsomorphicNandClusterMapping, OptimizeClusterSlots, PcbGatesConflict
from PhysicalDesign.Placement.Engine.Construction.Commit import PlacePcbGraph
from PhysicalDesign.Geometry.Placement import BuildPlacedGate, PlacedGate
from PhysicalDesign.Geometry.Rotation import RotatedCellSize
from Compilation.Ir.Models import Gate, GateKind, ModuleIR, NetlistIR
from Validation.Fabric import FabricServerValidationResult, FabricValidationProgress
from PhysicalDesign.Routing.Planning.LocalFirst import AssignCapacityAwareGuideOptionDomains, BuildCapacityAwareGuidePlan, BuildCapacityAwareGuideOptionDomains, BuildPlacementSolution, BuildRipupPlan, DeriveRoutingBudget, RoutingDemandEstimate
from PhysicalDesign.Routing.Planning.ChannelPlanner import BuildNetRoutingProfiles
from PhysicalDesign.Redstone.Rules.Geometry import ValidatePlacedCellElectricalIsolation
from PhysicalDesign.Policy import AdaptiveRoutingPolicy, GlobalRoutingPolicy, LocalFirstPhysicalDesignPolicy, RoutingStrategy
from PhysicalDesign.Redstone.Technology import DefaultRedstoneRoutingTechnology
from PhysicalDesign.Contracts.Failures import RoutingFailure, RoutingFailureReason, RoutingStageError
from Compilation.Synthesis.Validation import ValidateNandOnlyDesign
from Compilation.Synthesis.LogicOptimization import OptimizeLogic
from Compilation.Synthesis.NandTransform import ToNandOnly
from PhysicalDesign.Rendering.SchemWriter import LoadTemplate


class LocalFirstRouterTests(unittest.TestCase):
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
                "--example", "Assets/Examples/FullAdder.sv",
                "--topmodule", "FullAdder",
                "--output", "Output/Test",
                "--outputname", "Test",
                "--routing-strategy", "default",
            ]
        )
        self.assertEqual(Parsed.input, Path("Assets/Examples/FullAdder.sv"))
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
            StandardOutput = StringIO()
            with (
                patch(
                    "App.CompilerCli.CompileSvToLitematic",
                    side_effect=Failure,
                ) as Compile,
                redirect_stderr(ErrorOutput),
                redirect_stdout(StandardOutput),
            ):
                ReturnCode = Main([
                    "--input",
                    "Assets/Examples/FullAdder.sv",
                    "--output",
                    str(Directory / "Failed.litematic"),
                    "--routing-strategy",
                    "default",
                    "--routing-deadline-seconds",
                    "3.5",
                ])

        self.assertEqual(ReturnCode, 1)
        self.assertIn(
            "PortalGeneration: NoBoundaryEscape",
            StandardOutput.getvalue(),
        )
        self.assertIn("RESULT: FAILURE", StandardOutput.getvalue())
        self.assertIn("Operation failed:", ErrorOutput.getvalue())
        self.assertIn(
            "controlled routing failure",
            ErrorOutput.getvalue(),
        )
        self.assertEqual(
            Compile.call_args.kwargs["RoutingDeadlineSeconds"],
            3.5,
        )

    def testCliRoutingFailureSummaryIncludesComponentAttempts(self) -> None:
        Output = StringIO()
        Failure = RoutingStageError(RoutingFailure(
            Reason=RoutingFailureReason.ClusterInterfaceSolveIncomplete,
            Stage="ClusterInterfaceSolveIncomplete",
            Diagnostics={
                "Deadline": {
                    "ElapsedSeconds": 100.4,
                    "RemainingMilliseconds": 19595,
                    "Expired": False,
                    "ExpirationKind": "StageReserveExpired",
                },
                "CompletedComponentStateAttempts": [{
                    "CandidateId": "ChannelPlacement-test",
                    "Result": "unsatisfiable",
                    "PlacementFingerprint": "placement",
                    "Failure": {
                        "Stage": "PhysicalComponentAssemblyPlanning",
                        "Reason": "ComponentPortAssignmentUnsatisfiable",
                        "AffectedNets": ["NandNet26"],
                        "OwnershipUnsatCoreFingerprint": "core",
                    },
                }],
            },
        ))

        with redirect_stderr(Output):
            PrintRoutingFailureSummary(
                Failure,
                Path("Output/CLA4/CLA4.litematic"),
            )

        Text = Output.getvalue()
        self.assertIn("deadline: elapsed=100.4s", Text)
        self.assertIn("remaining=19595ms expired=false", Text)
        self.assertIn("component placement attempts (1)", Text)
        self.assertIn("NandNet26", Text)
        self.assertIn("CLA4.RoutingFailure.json", Text)

    def testCliFabricFailureSummaryPrintsExactMismatchBlock(self) -> None:
        Output = StringIO()
        Failure = RoutingStageError(RoutingFailure(
            Reason=RoutingFailureReason.FinalDrcViolation,
            Stage="FabricFinalCheck",
            Diagnostics={
                "FabricFinalCheck": {
                    "Diagnostics": {
                        "FailureTrace": {
                            "FailureKind": "mismatch",
                            "FailedOutput": "Sum2$Output",
                            "Expected": True,
                            "Actual": False,
                            "GlobalVectorIndex": 272,
                            "FirstFailingBlock": {
                                "FixturePosition": [12, 1, 9],
                                "WorldPosition": [112, 65, 209],
                                "State": {
                                    "Name": "minecraft:redstone_wire",
                                    "Properties": {
                                        "west": "side",
                                        "power": "0",
                                        "east": "side",
                                    },
                                },
                            },
                            "SubcircuitTrace": [],
                        },
                    },
                },
            },
        ))

        with redirect_stderr(Output):
            PrintFabricFailureSummary(Failure, None)

        Text = Output.getvalue()
        self.assertIn("output: Sum2$Output expected=true actual=false", Text)
        self.assertIn("validation: vector=272", Text)
        self.assertIn(
            "block: minecraft:redstone_wire[east=side,power=0,west=side]",
            Text,
        )
        self.assertIn(
            "coords: fixture=(12, 1, 9) world=(112, 65, 209)",
            Text,
        )
        self.assertIn("evidence: first mismatching block", Text)

    def testCliFabricTimeoutUsesFailedOutputProbeFromArtifact(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            OutputPath = Path(DirectoryValue) / "Rca4.litematic"
            ArtifactPath = OutputPath.with_suffix(".RoutingFailure.json")
            ArtifactPath.write_text(json.dumps({
                "Failure": {
                    "Stage": "FabricFinalCheck",
                    "Diagnostics": {
                        "FabricFinalCheck": {
                            "Diagnostics": {
                                "FailureTrace": {
                                    "FailureKind": "timeout",
                                    "FailedOutput": "Sum3$Output",
                                    "Expected": False,
                                    "Actual": False,
                                    "FirstFailingBlock": None,
                                    "SubcircuitTrace": [{
                                        "Output": {
                                            "Signal": "Sum3$Output",
                                            "Blocks": [{
                                                "FixturePosition": [60, 1, 29],
                                                "WorldPosition": [60, 65, 29],
                                                "State": {
                                                    "Name": "minecraft:redstone_lamp",
                                                    "Properties": {"lit": "false"},
                                                },
                                            }],
                                        },
                                    }],
                                },
                            },
                        },
                    },
                },
            }), encoding="utf-8")
            Output = StringIO()

            with redirect_stderr(Output):
                PrintFabricFailureSummary(
                    ValueError(
                        "FabricFinalCheck:timeout:"
                        "redstone-network-did-not-settle"
                    ),
                    OutputPath,
                )

        Text = Output.getvalue()
        self.assertIn("kind: timeout", Text)
        self.assertIn("block: minecraft:redstone_lamp[lit=false]", Text)
        self.assertIn(
            "coords: fixture=(60, 1, 29) world=(60, 65, 29)",
            Text,
        )
        self.assertIn("failed-output probe", Text)

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

    def testFactorizedGuideAssignmentPreservesPlanIdentity(self) -> None:
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
        Policy = GlobalRoutingPolicy(MaximumRipupPasses=2)
        Domains = BuildCapacityAwareGuideOptionDomains(
            Profiles,
            2,
            0,
            0,
            Policy,
            DefaultRedstoneRoutingTechnology,
            8,
        )
        Factorized = AssignCapacityAwareGuideOptionDomains(
            Profiles,
            Domains,
            2,
            Policy,
            8,
        )
        ExistingEntryPoint = BuildCapacityAwareGuidePlan(
            Profiles,
            2,
            0,
            0,
            Policy,
            DefaultRedstoneRoutingTechnology,
            8,
        )

        self.assertEqual(
            ExistingEntryPoint.ToDictionary(),
            Factorized.ToDictionary(),
        )

    def testGuideDomainInputsInvalidateOnlyChangedSignal(self) -> None:
        def Profile(Source, Target):
            return SimpleNamespace(
                SourceAccessPath=(Source,),
                TargetAccessPaths={Target: (Target,)},
                Span=abs(Target[0] - Source[0]) + abs(Target[2] - Source[2]),
                Fanout=1,
                Criticality=1,
            )

        Policy = GlobalRoutingPolicy(MaximumRipupPasses=2)
        OriginalProfiles = {
            "A": Profile((0, 2, 0), (8, 2, 8)),
            "B": Profile((0, 2, 8), (8, 2, 0)),
        }
        EditedProfiles = {
            **OriginalProfiles,
            "A": Profile((1, 2, 0), (8, 2, 8)),
        }
        Original = BuildCapacityAwareGuideOptionDomains(
            OriginalProfiles,
            2,
            0,
            0,
            Policy,
            DefaultRedstoneRoutingTechnology,
            8,
        )
        Edited = BuildCapacityAwareGuideOptionDomains(
            EditedProfiles,
            2,
            0,
            0,
            Policy,
            DefaultRedstoneRoutingTechnology,
            8,
        )

        self.assertNotEqual(
            Original["A"].LocalInputFingerprint,
            Edited["A"].LocalInputFingerprint,
        )
        self.assertEqual(
            Original["B"].LocalInputFingerprint,
            Edited["B"].LocalInputFingerprint,
        )
        OriginalPlan = AssignCapacityAwareGuideOptionDomains(
            OriginalProfiles,
            Original,
            2,
            Policy,
            8,
        )
        EditedPlan = AssignCapacityAwareGuideOptionDomains(
            EditedProfiles,
            Edited,
            2,
            Policy,
            8,
        )
        self.assertEqual(set(OriginalPlan.Guides), set(EditedPlan.Guides))

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
            1,
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

    def testCapacityGuideUsesLayerExactComponentKeepout(self) -> None:
        Profile = SimpleNamespace(
            SourceAccessPath=((0, 2, 0),),
            TargetAccessPaths={(20, 2, 0): ((20, 2, 0),)},
            Span=20,
            Fanout=1,
            Criticality=1,
        )
        BlockedCells = frozenset((
            (X, 0) for X in range(5, 16)
        ))

        BlockedPlan = BuildCapacityAwareGuidePlan(
            {"Global": Profile},
            1,
            0,
            0,
            GlobalRoutingPolicy(CandidateLaneCount=1),
            DefaultRedstoneRoutingTechnology,
            8,
            ComponentObstacleCellsByLayer={0: BlockedCells},
        )
        OtherLayerPlan = BuildCapacityAwareGuidePlan(
            {"Global": Profile},
            1,
            0,
            0,
            GlobalRoutingPolicy(CandidateLaneCount=1),
            DefaultRedstoneRoutingTechnology,
            8,
            ComponentObstacleCellsByLayer={1: BlockedCells},
        )

        self.assertFalse(
            BlockedCells & BlockedPlan.Guides["Global"]
        )
        self.assertTrue(
            BlockedCells <= OtherLayerPlan.Guides["Global"]
        )

    def testCapacityGuideExemptsOnlyDeclaredSignalPassage(self) -> None:
        Profile = SimpleNamespace(
            SourceAccessPath=((0, 2, 0),),
            TargetAccessPaths={(20, 2, 0): ((20, 2, 0),)},
            Span=20,
            Fanout=1,
            Criticality=1,
        )
        BlockedCells = frozenset((
            (X, 0) for X in range(5, 16)
        ))

        Plan = BuildCapacityAwareGuidePlan(
            {"PortSignal": Profile},
            1,
            0,
            0,
            GlobalRoutingPolicy(CandidateLaneCount=1),
            DefaultRedstoneRoutingTechnology,
            8,
            ComponentObstacleCellsByLayer={0: BlockedCells},
            ComponentObstacleExemptCellsBySignal={
                "PortSignal": {0: BlockedCells},
            },
        )

        self.assertTrue(
            BlockedCells <= Plan.Guides["PortSignal"]
        )

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
                        InputPath=Path("Assets/Examples/FullAdder.sv"),
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
                        InputPath=Path("Assets/Examples/FullAdder.sv"),
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
                        InputPath=Path("Assets/Examples/RippleCarryAdder4.sv"),
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
                        InputPath=Path("Assets/Examples/RippleCarryAdder4.sv"),
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

    def testRemovedStrategiesAreRejected(self) -> None:
        """Unsupported legacy modes cannot enter the public router."""
        for Strategy in ("hybrid", "compatibility"):
            with self.subTest(Strategy=Strategy), self.assertRaises(ValueError):
                PlaceAndRoutePcb(
                    SimpleNamespace(),
                    Strategy=Strategy,
                )

    def testNewRouterFullAdderWritesCompleteDiagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as Directory:
            Root = Path(Directory)
            TimingEvents = []
            ValidationProgressEvents = []
            def CaptureServerSnapshot(*, SourcePath, OutputPath, **_Options):
                OutputPath.write_bytes(SourcePath.read_bytes())
                return SimpleNamespace(
                    RequestedPositionCount=1,
                    ObservedBlockCount=1,
                    WorldReadRequests=1,
                    InputCountSetToZero=3,
                    SnapshotReadPasses=2,
                    InputZeroGameTime=0,
                    FirstObservedGameTime=50,
                    LastObservedGameTime=51,
                )

            with (
                patch("Compilation.Pipeline.FabricServerSupervisor") as Supervisor,
                patch(
                    "Compilation.Pipeline.CaptureServerUpdatedLitematic",
                    side_effect=CaptureServerSnapshot,
                ),
            ):
                def ValidateInFabric(**Options):
                    ProgressCallback = Options["ProgressCallback"]
                    VectorCount = len(Options["Vectors"])
                    ProgressCallback(FabricValidationProgress(
                        Completed=0,
                        Total=VectorCount,
                        Stage="authoritative Fabric truth-table validation",
                    ))
                    ProgressCallback(FabricValidationProgress(
                        Completed=VectorCount // 2,
                        Total=VectorCount,
                        Stage="authoritative Fabric truth-table validation",
                    ))
                    return FabricServerValidationResult(
                        Status="passed",
                        Backend="fabric-26.2",
                        RuntimeSeconds=0.0,
                    )

                Supervisor.return_value.Validate.side_effect = ValidateInFabric
                Result = CompileSvToLitematic(
                    InputPath=Path("Assets/Examples/FullAdder.sv"),
                    TopModule="FullAdder",
                    OutputPath=Root / "FullAdder.litematic",
                    DiagramPath=Root / "FullAdder.Nand.json",
                    Workdir=Root / "Frontend",
                    RoutingStrategyValue=RoutingStrategy.Default,
                    TimingCallback=lambda Name, Event: TimingEvents.append(
                        (Name, Event)
                    ),
                    ValidationProgressCallback=(
                        ValidationProgressEvents.append
                    ),
                )
            Diagnostics = Result.PhysicalDesignPath.read_text(encoding="utf-8")
            EmittedBlockCount = len(LoadTemplate(Result.OutputPath).Blocks)
        self.assertEqual(
            Result.FabricFinalCheck.Status,
            "passed",
        )
        self.assertEqual(Result.UsedStrategy, "default")
        self.assertEqual(TimingEvents[0], ("Routing", "begin"))
        self.assertIn(
            ("RoutingStage", "physical component interface planning"),
            TimingEvents,
        )
        self.assertIn(
            ("RoutingStage", "placement candidate routing"),
            TimingEvents,
        )
        self.assertLess(
            TimingEvents.index(("Routing", "finish")),
            TimingEvents.index(("Validation", "begin")),
        )
        self.assertEqual(TimingEvents[-1], ("Validation", "finish"))
        self.assertEqual(
            ValidationProgressEvents[0].Stage,
            "MCHPRS exhaustive physical validation",
        )
        self.assertEqual(ValidationProgressEvents[0].Completed, 0)
        self.assertEqual(ValidationProgressEvents[0].Total, 8)
        self.assertEqual(
            ValidationProgressEvents[-1].Status,
            "passed",
        )
        self.assertEqual(
            ValidationProgressEvents[-1].Completed,
            ValidationProgressEvents[-1].Total,
        )
        self.assertTrue(any(
            Progress.Completed == 4
            and Progress.Total == 8
            and Progress.Stage == "authoritative Fabric truth-table validation"
            for Progress in ValidationProgressEvents
        ))
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
