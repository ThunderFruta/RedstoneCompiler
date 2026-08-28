from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from RedstoneCompiler.RustRouting import RoutingContext as RustRoutingContext
except ImportError:
    RustRoutingContext = None

from SVDecoder.Sv import ParseSvToNetlist
import Compiler.Placement.Flow.Setup as PcbFlow
import Compiler.Placement.Flow.RoutingAttempts as PcbFlowRaw
from Compiler.Placement.Flow.Runner import PlaceAndRoutePcb
import Compiler.Placement.Flow.Runner as PcbFlowRunner
from Compiler.Routing.Policy import LocalFirstPhysicalDesignPolicy
from Compiler.Simulation.Redstone import (
    SimulateRoutedTruthTable,
    SimulateRoutedTruthTablePython,
)
from Compiler.Synthesis.LogicOptimization import OptimizeLogic
from Compiler.Synthesis.NandTransform import ToNandOnly


class RedstoneSimulationTests(unittest.TestCase):
    def testFullAdderPassesEveryPhysicalTruthTableRow(self) -> None:
        if RustRoutingContext is None:
            self.skipTest("authoritative routing requires Rust router")

        StageCalls: list[str] = []
        RealPrepareRawTrackDomain = PcbFlowRaw.PrepareRawTrackAssignmentDomain
        RealSolveRawTrackPortfolio = (
            PcbFlow.SolveRawTrackAssignmentPortfolioWithContext
        )
        RealPrepareTrackAssignment = PcbFlow.PrepareTrackAssignment
        RealRoutePcbDesign = PcbFlowRunner.RoutePcbDesign

        def PrepareRawTrackDomain(*Arguments, **KeywordArguments):
            StageCalls.append("raw-track-domain")
            return RealPrepareRawTrackDomain(*Arguments, **KeywordArguments)

        def SelectRawTrackProblem(*Arguments, **KeywordArguments):
            StageCalls.append("pre-route-interface-selection")
            return RealSolveRawTrackPortfolio(*Arguments, **KeywordArguments)

        def PrepareSelectedTrackAssignment(*Arguments, **KeywordArguments):
            StageCalls.append("selected-track-preparation")
            return RealPrepareTrackAssignment(*Arguments, **KeywordArguments)

        def RouteSelectedPlacement(*Arguments, **KeywordArguments):
            StageCalls.append("route")
            return RealRoutePcbDesign(*Arguments, **KeywordArguments)

        with tempfile.TemporaryDirectory() as Workdir:
            Netlist = ParseSvToNetlist(
                InputPath=Path("Examples/FullAdder.sv"),
                TopModule=None,
                Workdir=Path(Workdir),
            )
            Optimized = OptimizeLogic(Netlist)
            NandNetlist = ToNandOnly(Optimized)
            ProgressEvents = []
            with (
                patch.object(
                    PcbFlowRaw,
                    "PrepareRawTrackAssignmentDomain",
                    side_effect=PrepareRawTrackDomain,
                ) as PrepareRawDomains,
                patch.object(
                    PcbFlow,
                    "SolveRawTrackAssignmentPortfolioWithContext",
                    side_effect=SelectRawTrackProblem,
                ) as SelectInterface,
                patch.object(
                    PcbFlow,
                    "PrepareTrackAssignment",
                    side_effect=PrepareSelectedTrackAssignment,
                ) as PrepareTracks,
                patch.object(
                    PcbFlowRunner,
                    "RoutePcbDesign",
                    side_effect=RouteSelectedPlacement,
                ) as RouteDesign,
            ):
                Physical = PlaceAndRoutePcb(
                    NandNetlist,
                    ProgressCallback=ProgressEvents.append,
                )
            Report = SimulateRoutedTruthTable(
                Physical.Routed,
                ReferenceModule=Optimized.Modules[Optimized.Top],
            )
            PythonReport = SimulateRoutedTruthTablePython(
                Physical.Routed,
                ReferenceModule=Optimized.Modules[Optimized.Top],
            )

        self.assertEqual(len(Report.Rows), 8)
        self.assertEqual(Report.Rows, PythonReport.Rows)
        self.assertEqual(Report.Backend, "native-parallel")
        self.assertEqual(PythonReport.Backend, "python")
        self.assertTrue(Report.Passed)
        self.assertFalse(Physical.FallbackUsed)
        self.assertTrue(Physical.Routed.ZeroResourceConflicts)
        self.assertEqual(Physical.Routed.AssignmentExpansionCount, 0)
        self.assertNotIn(
            "AdaptiveEscalationHistory",
            Physical.Routed.RoutingControlEffectiveness,
        )
        self.assertNotIn(
            "RoutingEscalationState",
            Physical.Routed.RoutingControlEffectiveness,
        )
        self.assertIn(
            "FixedRoutingControls",
            Physical.Routed.RoutingControlEffectiveness,
        )
        CapacitySelection = Physical.Routed.RoutingControlEffectiveness[
            "PrePlacementCapacitySelection"
        ]
        # FullAdder must expose the fixed incumbent plus at least one compact
        # geometry alternative to the single pre-route selection.  A lone
        # incumbent would be an accidental regression to the old direct path.
        self.assertGreaterEqual(CapacitySelection["GeometryDomainSize"], 2)
        self.assertLessEqual(
            CapacitySelection["GeometryDomainSize"],
            LocalFirstPhysicalDesignPolicy
            .NandPacking.RetainedPlacementCandidates,
        )
        self.assertGreaterEqual(
            CapacitySelection["EnvelopeDomainSize"],
            CapacitySelection["GeometryDomainSize"],
        )
        # The small-design domain has multiple geometry/layer members.  Each
        # first exports its immutable raw domain, then exactly one aggregate
        # authoritative selector freezes the winner before exactly one route.
        self.assertEqual(SelectInterface.call_count, 1)
        self.assertGreaterEqual(PrepareRawDomains.call_count, 1)
        self.assertEqual(PrepareTracks.call_count, 0)
        self.assertEqual(RouteDesign.call_count, 1)
        # Raw domains are materialized lazily *inside* the one aggregate
        # selection call.  That keeps dominated fixed candidates from paying
        # detailed-domain construction, while ensuring no domain is created
        # after the selected frozen contract begins its route.
        self.assertEqual(StageCalls[0], "pre-route-interface-selection")
        self.assertEqual(StageCalls[-1], "route")
        self.assertTrue(
            all(
                Stage == "raw-track-domain"
                for Stage in StageCalls[1:-1]
            )
        )
        self.assertEqual(CapacitySelection["CapacitySolveCount"], 1)
        self.assertEqual(CapacitySelection["RouteAttemptCount"], 1)
        InterfaceSelection = Physical.Routed.RoutingControlEffectiveness[
            "PreRouteInterfaceSelection"
        ]
        self.assertTrue(InterfaceSelection["Success"])
        self.assertTrue(InterfaceSelection["Complete"])
        self.assertFalse(InterfaceSelection["Unsatisfiable"])
        self.assertEqual(len(InterfaceSelection["SelectedTemplateIds"]), 1)
        self.assertTrue(InterfaceSelection["SelectionFingerprint"])
        self.assertEqual(
            Physical.Routed.RoutingControlEffectiveness[
                "LayerCappedAssignmentAttempts"
            ],
            [],
        )
        self.assertEqual(
            Physical.Routed.RoutingControlEffectiveness[
                "LocalizedRepairPasses"
            ],
            0,
        )
        self.assertEqual(
            Physical.Routed.RoutingControlEffectiveness[
                "LocalizedReroutedNetCount"
            ],
            0,
        )
        self.assertTrue(
            any(
                0 < Progress.Completed < Progress.Total
                for Progress in ProgressEvents
            )
        )
        self.assertTrue(
            any(
                "conflicts" in Progress.Stage
                for Progress in ProgressEvents
            )
        )


if __name__ == "__main__":
    unittest.main()
