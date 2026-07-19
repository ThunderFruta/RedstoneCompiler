from pathlib import Path
from types import SimpleNamespace
from contextlib import redirect_stderr
from io import StringIO
import json
import tempfile
import unittest
from unittest.mock import patch

from SVDecoder import Sv
from Compiler.Main import BuildParser
from Compiler.Pipeline import CompileSvToLitematic
from Compiler.Placement.PcbFlow import PlaceAndRoutePcb
from Compiler.Placement.Pcb import (
    BuildTopologicalLevels,
    OptimizeClusterSlots,
    PlacePcbGraph,
)
from Compiler.Ir.Models import Gate, GateKind, ModuleIR, NetlistIR
from Compiler.Routing.LocalFirst import (
    BuildCapacityAwareGuidePlan,
    BuildPlacementSolution,
    BuildRipupPlan,
    DeriveRoutingBudget,
    RoutingDemandEstimate,
)
from Compiler.Routing.ChannelPlanner import BuildNetRoutingProfiles
from Compiler.Routing.Policy import (
    AdaptiveRoutingPolicy,
    CompatibilityPhysicalDesignPolicy,
    GlobalRoutingPolicy,
    LocalFirstPhysicalDesignPolicy,
    RoutingAcceptanceProfiles,
    RoutingStrategy,
)
from Compiler.Routing.Technology import DefaultRedstoneRoutingTechnology
from Compiler.Synthesis.Validation import ValidateNandOnlyDesign
from Compiler.Synthesis.LogicOptimization import OptimizeLogic
from Compiler.Synthesis.NandTransform import ToNandOnly
from SchemEncoder.Writer262 import LoadTemplate


class LocalFirstRouterTests(unittest.TestCase):
    def testCompatibilityRegressionFixtureMatchesFrozenPolicy(self) -> None:
        Fixture = json.loads(
            Path("Tests/Fixtures/FullAdderCompatibility.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            Fixture["PolicyVersion"],
            CompatibilityPhysicalDesignPolicy.PolicyVersion,
        )
        self.assertEqual(Fixture["GateKinds"], {"INPUT": 3, "NAND": 9, "OUTPUT": 2})
        self.assertEqual(Fixture["RunSummary"]["Conflicts"], 0)
        self.assertEqual(Fixture["FinalValidation"]["UnresolvedClaimCount"], 0)
        self.assertEqual(len(Fixture["TruthTableOutputs"]), 8)

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

    def testCliAliasesAndRoutingStrategiesParse(self) -> None:
        Parsed = BuildParser().parse_args(
            [
                "--example", "Examples/FullAdder.sv",
                "--topmodule", "FullAdder",
                "--output", "Output/Test",
                "--outputname", "Test",
                "--routing-strategy", "new-router-first",
            ]
        )
        self.assertEqual(Parsed.input, Path("Examples/FullAdder.sv"))
        self.assertEqual(Parsed.top, "FullAdder")
        self.assertEqual(Parsed.outputname, "Test")
        self.assertEqual(Parsed.routing_strategy, "new-router-first")
        self.assertEqual(
            RoutingStrategy.Parse("authoritative-only"),
            RoutingStrategy.Compatibility,
        )
        self.assertEqual(
            BuildParser().parse_args([]).routing_strategy,
            "new-router-first",
        )
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            BuildParser().parse_args(["--routing-strategy", "compatibility"])

    def testCompatibilityPolicyIsFrozenBesideLocalFirstPolicy(self) -> None:
        self.assertEqual(CompatibilityPhysicalDesignPolicy.Placement.RoutingSpacing, 6)
        self.assertEqual(LocalFirstPhysicalDesignPolicy.Placement.RoutingSpacing, 4)
        self.assertEqual(LocalFirstPhysicalDesignPolicy.Placement.PinEscapeLength, 1)
        self.assertNotEqual(
            CompatibilityPhysicalDesignPolicy.PolicyVersion,
            LocalFirstPhysicalDesignPolicy.PolicyVersion,
        )
        Snapshot = LocalFirstPhysicalDesignPolicy.ToDictionary()
        self.assertTrue(Snapshot["QualityGate"]["Enabled"])
        self.assertEqual(Snapshot["Placement"]["RoutingFeedbackIterations"], 2)
        self.assertEqual(
            LocalFirstPhysicalDesignPolicy.PolicyVersion,
            "physical-design-v5-adaptive-nand",
        )
        self.assertTrue(Snapshot["NandPacking"]["Enabled"])
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
        self.assertEqual(len({Value.CandidatesPerNet for Value in Budgets}), 1)
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
        self.assertFalse(LocalFirstPhysicalDesignPolicy.MaterialObjective.Enabled)
        self.assertEqual(
            RoutingAcceptanceProfiles["FullAdder"].MaximumFootprint,
            600,
        )
        self.assertIsNone(
            RoutingAcceptanceProfiles["RippleCarryAdder4"].MaximumFootprint
        )

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
            PackingPolicy=LocalFirstPhysicalDesignPolicy.NandPacking,
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

    def testHybridFallsBackToCompatibilityOnLocalRoutingFailure(self) -> None:
        Fallback = SimpleNamespace(FallbackUsed=False, FallbackReason=None)
        with patch(
            "Compiler.Placement.PcbFlow._PlaceAndRoutePcbWithPolicy",
            side_effect=[ValueError("forced local failure"), Fallback],
        ) as Execute:
            Result = PlaceAndRoutePcb(SimpleNamespace(), Strategy=RoutingStrategy.Hybrid)
        self.assertEqual(Execute.call_count, 2)
        self.assertTrue(Result.FallbackUsed)
        self.assertIn("forced local failure", Result.FallbackReason)

    def testNewRouterFullAdderWritesCompleteDiagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as Directory:
            Root = Path(Directory)
            Result = CompileSvToLitematic(
                InputPath=Path("Examples/FullAdder.sv"),
                TopModule="FullAdder",
                OutputPath=Root / "FullAdder.litematic",
                DiagramPath=Root / "FullAdder.Nand.json",
                Workdir=Root / "Frontend",
                RoutingStrategyValue=RoutingStrategy.NewRouterFirst,
            )
            Diagnostics = Result.PhysicalDesignPath.read_text(encoding="utf-8")
            EmittedBlockCount = len(LoadTemplate(Result.OutputPath).Blocks)
        self.assertTrue(Result.TruthTablePassed)
        self.assertEqual(Result.UsedStrategy, "new-router-first")
        self.assertIn('"UnresolvedClaimCount": 0', Diagnostics)
        self.assertIn('"PlanningContracts"', Diagnostics)
        self.assertIn('"BlockComposition"', Diagnostics)
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
