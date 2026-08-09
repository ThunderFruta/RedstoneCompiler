from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from RedstoneCompiler.RustRouting import RoutingContext as RustRoutingContext
except ImportError:
    RustRoutingContext = None

from SVDecoder.Sv import ParseSvToNetlist
import Compiler.Placement.PcbFlow as PcbFlow
from Compiler.Placement.PcbFlow import PlaceAndRoutePcb
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
        RealPrepareRouteGuideFactors = (
            PcbFlow.PrepareRawRouteGuideFactorDomain
        )
        RealSolveRawTrackProblem = (
            PcbFlow.SolveRawTrackAssignmentProblemWithContext
        )
        RealPrepareTrackAssignment = PcbFlow.PrepareTrackAssignment
        RealRoutePcbDesign = PcbFlow.RoutePcbDesign

        def PrepareRouteGuideFactors(*Arguments, **KeywordArguments):
            StageCalls.append("route-guide-factors")
            return RealPrepareRouteGuideFactors(
                *Arguments,
                **KeywordArguments,
            )

        def SelectRawTrackProblem(*Arguments, **KeywordArguments):
            StageCalls.append("pre-route-interface-selection")
            return RealSolveRawTrackProblem(*Arguments, **KeywordArguments)

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
                    PcbFlow,
                    "PrepareRawRouteGuideFactorDomain",
                    side_effect=PrepareRouteGuideFactors,
                ) as PrepareGuideFactors,
                patch.object(
                    PcbFlow,
                    "SolveRawTrackAssignmentProblemWithContext",
                    side_effect=SelectRawTrackProblem,
                ) as SelectInterface,
                patch.object(
                    PcbFlow,
                    "PrepareTrackAssignment",
                    side_effect=PrepareSelectedTrackAssignment,
                ) as PrepareTracks,
                patch.object(
                    PcbFlow,
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
        self.assertGreaterEqual(PrepareGuideFactors.call_count, 1)
        self.assertEqual(PrepareTracks.call_count, 0)
        self.assertEqual(RouteDesign.call_count, 1)
        # Every declared compact member publishes only route-guide factors
        # before the one aggregate native selection.  No domain is created
        # after that selected frozen contract begins its route.
        self.assertEqual(StageCalls[-2], "pre-route-interface-selection")
        self.assertEqual(StageCalls[-1], "route")
        self.assertTrue(
            all(
                Stage == "route-guide-factors"
                for Stage in StageCalls[:-2]
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
