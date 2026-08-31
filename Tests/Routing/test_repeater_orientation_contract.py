import unittest

from Compiler.Routing.Technology import (
    OppositeHorizontalFacing,
    RepeaterInputDelta,
    RepeaterInputFacingForStep,
    RepeaterOutputDelta,
    ValidateRepeaterInputFacing,
)


class RepeaterOrientationContractTests(unittest.TestCase):
    def testCardinalRouteStepsUseTheJavaInputSide(self) -> None:
        Cases = {
            (1, 0, 0): "west",
            (-1, 0, 0): "east",
            (0, 0, 1): "north",
            (0, 0, -1): "south",
        }
        for Step, ExpectedInputFacing in Cases.items():
            with self.subTest(Step=Step):
                InputFacing = RepeaterInputFacingForStep((0, 0, 0), Step)
                self.assertEqual(InputFacing, ExpectedInputFacing)
                self.assertEqual(RepeaterOutputDelta(InputFacing), Step)
                self.assertEqual(
                    RepeaterInputDelta(InputFacing),
                    tuple(-Value for Value in Step),
                )
                self.assertEqual(
                    OppositeHorizontalFacing(InputFacing),
                    RepeaterInputFacingForStep(Step, (0, 0, 0)),
                )

    def testInvalidFacingsAndStepsFailClosed(self) -> None:
        for Facing in ("", "up", "down", "sideways"):
            with self.subTest(Facing=Facing):
                with self.assertRaises(ValueError):
                    ValidateRepeaterInputFacing(Facing)
        for Next in ((0, 1, 0), (2, 0, 0), (1, 0, 1)):
            with self.subTest(Next=Next):
                with self.assertRaises(ValueError):
                    RepeaterInputFacingForStep((0, 0, 0), Next)


if __name__ == "__main__":
    unittest.main()
