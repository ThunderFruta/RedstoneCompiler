from types import SimpleNamespace
import unittest

from Compiler.Placement.Geometry import ValidatePlacedGateContract
from Compiler.Routing.Failures import RoutingFailureReason
from Compiler.Routing.Policy import (
    DefaultPhysicalDesignPolicy,
    RoutingAttemptPolicy,
)
from Compiler.Routing.Technology import (
    DefaultRedstoneRoutingTechnology,
)
from Compiler.Routing.Pcb import BuildPcbRoutingConfigurations


def BuildGate(
    Name,
    X,
    Z,
    *,
    Inputs=(),
    Outputs=(),
    InputPins=(),
    OutputPin=None,
):
    return SimpleNamespace(
        Name=Name,
        Kind="NAND",
        X=X,
        Y=0,
        Z=Z,
        Inputs=list(Inputs),
        Outputs=list(Outputs),
        InputPins=list(InputPins),
        InputDirections=[(0, 0, -1) for _ in Inputs],
        OutputPin=OutputPin,
        OutputDirection=((0, 0, 1) if OutputPin is not None else None),
    )


class RoutingArchitectureTests(unittest.TestCase):
    def BuildPlaced(self, FirstSignal="A", SecondSignal="B"):
        return SimpleNamespace(
            PlacedGates=[
                BuildGate(
                    "SourceFirst",
                    0,
                    0,
                    Outputs=(FirstSignal,),
                    OutputPin=(0, 0, 0),
                ),
                BuildGate(
                    "SinkFirst",
                    15,
                    0,
                    Inputs=(FirstSignal,),
                    InputPins=((15, 0, 0),),
                ),
                BuildGate(
                    "SourceSecond",
                    0,
                    3,
                    Outputs=(SecondSignal,),
                    OutputPin=(0, 0, 3),
                ),
                BuildGate(
                    "SinkSecond",
                    15,
                    3,
                    Inputs=(SecondSignal,),
                    InputPins=((15, 0, 3),),
                ),
            ]
        )

    def testTechnologyOwnsConnectivityPitchAndLayerMapping(self) -> None:
        Technology = DefaultRedstoneRoutingTechnology

        self.assertGreaterEqual(Technology.TrackPitch, 2)
        self.assertTrue(Technology.AreConnected((0, 1, 0), (1, 2, 0)))
        self.assertFalse(Technology.AreConnected((0, 1, 0), (2, 1, 0)))
        self.assertEqual(
            Technology.RoutingY(4, 2),
            4 + 1 + 2 * Technology.RoutingLayerPitch,
        )

    def testPlacedCellContractRejectsMissingPhysicalInput(self) -> None:
        Invalid = BuildGate("Invalid", 0, 0, Inputs=("A",), InputPins=())

        with self.assertRaisesRegex(ValueError, "logical inputs"):
            ValidatePlacedGateContract(Invalid)

    def testAuthoritativeRoutingUsesSingleConfiguredAttempt(self) -> None:
        Configurations = BuildPcbRoutingConfigurations(SimpleNamespace(PlacedGates=[]))
        self.assertEqual(len(Configurations), 1)
        self.assertEqual(Configurations[0].AttemptId, "Authoritative")
        self.assertEqual(Configurations[0].OrderMode, "Natural")
        self.assertEqual(Configurations[0].SearchMargin, 20)
        self.assertEqual(Configurations[0].GuidePenalty, 6)

    def testPolicySnapshotIsTypedAndRejectsGuideFreeAttempts(self) -> None:
        Snapshot = DefaultPhysicalDesignPolicy.ToDictionary()

        self.assertEqual(Snapshot["PolicyVersion"], "physical-design-v1")
        self.assertIn("DetailedRouting", Snapshot)
        with self.assertRaisesRegex(ValueError, "authoritative"):
            RoutingAttemptPolicy("Invalid", 1, 0, 1.0, 1, 1)


if __name__ == "__main__":
    unittest.main()
