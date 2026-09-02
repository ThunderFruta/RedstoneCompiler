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
    def testCanonicalConflictReplayMatchesSemanticEvidence(self) -> None:
        Result = ReplayModule.RunCla4AccessReplay()

        self.assertEqual(Result.Status.value, "Conflict")
        self.assertTrue(Result.SourceArtifactPresent)
        self.assertTrue(Result.SourceArtifactSha256)
        self.assertTrue(Result.ExpectedSourceArtifactSha256)
        self.assertIsInstance(Result.SourceArtifactSha256Matched, bool)
        self.assertIsInstance(Result.RuntimeWithinTarget, bool)
        self.assertTrue(Result.SourceCandidateMatched)
        self.assertTrue(Result.ExpectedProfileMatched)
        self.assertTrue(Result.RepeatedProfileMatched)
        self.assertTrue(Result.FixedPlacementSolveMatched)
        self.assertTrue(Result.UnsatisfiableCoreReplayed)
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
        self.assertTrue(Result.Passed)


if __name__ == "__main__":
    unittest.main()
