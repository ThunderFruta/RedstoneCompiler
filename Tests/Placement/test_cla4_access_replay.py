import importlib.util
from pathlib import Path
import sys
import unittest


RepositoryRoot = Path(__file__).resolve().parents[2]
ScriptPath = RepositoryRoot / "Scripts/RunCla4AccessReplay.py"
Spec = importlib.util.spec_from_file_location("RunCla4AccessReplay", ScriptPath)
assert Spec is not None and Spec.loader is not None
ReplayModule = importlib.util.module_from_spec(Spec)
sys.modules[Spec.name] = ReplayModule
Spec.loader.exec_module(ReplayModule)


class Cla4AccessReplayTests(unittest.TestCase):
    def testCanonicalConflictReplayIsDeterministicAndFast(self) -> None:
        Result = ReplayModule.RunCla4AccessReplay()

        self.assertTrue(Result.Passed)
        self.assertEqual(Result.Status.value, "Conflict")
        self.assertTrue(Result.SourceArtifactMatched)
        self.assertTrue(Result.SourceCandidateMatched)
        self.assertTrue(Result.ExpectedProfileMatched)
        self.assertTrue(Result.RepeatedProfileMatched)
        self.assertTrue(Result.FixedPlacementSolveMatched)
        self.assertTrue(Result.UnsatisfiableCoreReplayed)
        self.assertLessEqual(
            Result.RuntimeSeconds,
            Result.MaximumRuntimeSeconds,
        )
        self.assertEqual(Result.Profile["ExactConflictCount"], 2)
        self.assertEqual(
            Result.Profile["ConflictSignals"],
            ["NandNet0", "Propagate0"],
        )
        self.assertEqual(
            [
                Conflict["Position"]
                for Conflict in Result.Profile["CrossConflicts"]
            ],
            [[16, 1, 5], [17, 1, 5]],
        )
        self.assertEqual(
            Result.FixedPlacementSolve["Status"],
            "Unsatisfiable",
        )
        self.assertEqual(
            Result.FixedPlacementSolve["UnsatisfiableCore"]["Signals"],
            ["NandNet0", "Propagate0"],
        )


if __name__ == "__main__":
    unittest.main()
