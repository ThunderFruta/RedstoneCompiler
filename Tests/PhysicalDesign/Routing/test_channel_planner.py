import unittest
from types import SimpleNamespace

from PhysicalDesign.Routing.Planning.ChannelPlanner import BuildNetRoutingProfiles, MeasureRoutingStage


def BuildGate(
    Name,
    X,
    Z,
    *,
    Inputs=(),
    Outputs=(),
    InputPins=(),
    InputDirections=(),
    OutputPin=None,
    Kind="NAND",
):
    return SimpleNamespace(
        Name=Name,
        Kind=Kind,
        X=X,
        Y=0,
        Z=Z,
        Inputs=list(Inputs),
        Outputs=list(Outputs),
        InputPins=list(InputPins),
        InputDirections=(
            list(InputDirections)
            if InputDirections
            else [(0, 0, -1) for _ in Inputs]
        ),
        OutputPin=OutputPin,
        OutputDirection=((0, 0, 1) if OutputPin is not None else None),
    )


class ChannelPlannerTests(unittest.TestCase):
    def BuildPlaced(self):
        Gates = [
            BuildGate("SourceA", 0, 0, Outputs=("A",), OutputPin=(0, 0, 0)),
            BuildGate("SinkA1", 12, 0, Inputs=("A",), InputPins=((12, 0, 0),)),
            BuildGate("SinkA2", 12, 6, Inputs=("A",), InputPins=((12, 0, 6),)),
            BuildGate("SourceB", 0, 1, Outputs=("B",), OutputPin=(0, 0, 1)),
            BuildGate("SinkB", 10, 1, Inputs=("B",), InputPins=((10, 0, 1),)),
            BuildGate("SourceC", 2, 20, Outputs=("C",), OutputPin=(2, 0, 20)),
            BuildGate("SinkC", 3, 20, Inputs=("C",), InputPins=((3, 0, 20),)),
        ]
        return SimpleNamespace(PlacedGates=Gates)

    def testCriticalTrunksRouteFirstDeterministically(self) -> None:
        Placed = self.BuildPlaced()
        Profiles = BuildNetRoutingProfiles(Placed, {"B": 2})

        self.assertTrue(Profiles["A"].IsTrunk)
        self.assertGreater(Profiles["B"].Criticality, Profiles["A"].Criticality)

    def testNetProfilesEncodeTerminalFanout(self) -> None:
        Profiles = BuildNetRoutingProfiles(self.BuildPlaced(), None)

        self.assertEqual(Profiles["A"].Fanout, 2)
        self.assertEqual(Profiles["B"].Fanout, 1)
        self.assertEqual(Profiles["C"].Span, 1)

    def testRoutingMetricsReportShape(self) -> None:
        Metrics = MeasureRoutingStage(
            "Strict + cleanup",
            {
                "A": {(0, 0, 0), (1, 0, 0), (1, 0, 1)},
                "B": {(0, 1, 0), (1, 1, 0)},
            },
            Plan=SimpleNamespace(CorridorCapacity=2),
            ReroutedNets=1,
        )

        self.assertEqual(Metrics.Stage, "Strict + cleanup")
        self.assertEqual(Metrics.TotalLength, 5)
        self.assertGreaterEqual(Metrics.BendCount, 1)
        self.assertEqual(Metrics.ReroutedNets, 1)
        self.assertEqual(Metrics.CorridorOverflowPeak, 0)


if __name__ == "__main__":
    unittest.main()
