"""No-flag prompt contracts for every executable script entry point."""

from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

from Compiler.FabricServer import DefaultFabricServerRoot, FabricServerValidationResult
from Scripts.Fabric import (
    ControlFabricServer,
    ImportSchemToFabricServer,
    TestSchemInFabricServer,
)
from Scripts.Routing import (
    CaptureRoutingDesignSnapshot,
    RunFreeroutingBenchmark,
    RunRouterAcceptance,
)


CanonicalServerRoot = str(DefaultFabricServerRoot())


class ScriptCliGuidanceTests(unittest.TestCase):
    def testFabricControlUsesTheCanonicalRuntimeManager(self) -> None:
        ExpectedRoot = DefaultFabricServerRoot()

        self.assertEqual(ControlFabricServer.ServerRoot, ExpectedRoot)
        self.assertEqual(
            ControlFabricServer.RuntimeScripts,
            ExpectedRoot / "PyScripts",
        )
        self.assertEqual(
            ControlFabricServer.RuntimeMain,
            ExpectedRoot / "PyScripts" / "Main.py",
        )

    def testFabricControlGuidesLifecycleActions(self) -> None:
        with patch("builtins.input", side_effect=["1"]):
            self.assertEqual(ControlFabricServer.GuidedArguments(), ["start"])
        with patch("builtins.input", side_effect=[""]):
            self.assertEqual(ControlFabricServer.GuidedArguments(), ["status"])
        with patch("builtins.input", side_effect=["4", "CLEAR"]):
            self.assertEqual(ControlFabricServer.GuidedArguments(), ["clear"])
        with patch("builtins.input", side_effect=["4", ""]):
            with self.assertRaises(EOFError):
                ControlFabricServer.GuidedArguments()
        with patch("builtins.input", side_effect=["0"]):
            with self.assertRaises(EOFError):
                ControlFabricServer.GuidedArguments()

    def testFabricImportGuidesToHotReload(self) -> None:
        with patch("builtins.input", side_effect=["build.schem", "", "", ""]):
            Arguments = ImportSchemToFabricServer.GuidedArguments()
        self.assertEqual(
            Arguments,
            [
                "build.schem", "--server-root", CanonicalServerRoot,
                "--origin", "0", "64", "0", "--replace",
            ],
        )

    def testFabricImportDefaultsToTheCanonicalServerRoot(self) -> None:
        Arguments = ImportSchemToFabricServer.BuildParser().parse_args(["build.schem"])

        self.assertEqual(Arguments.server_root, DefaultFabricServerRoot())

    def testFabricImporterHonorsTheSharedRootOverride(self) -> None:
        with TemporaryDirectory() as TemporaryDirectoryPath, patch.dict(
            os.environ,
            {"RC_FABRIC_SERVER_ROOT": TemporaryDirectoryPath},
        ):
            Arguments = ImportSchemToFabricServer.BuildParser().parse_args(["build.schem"])

        self.assertEqual(Arguments.server_root, Path(TemporaryDirectoryPath).resolve())

    def testFabricImportWritesThePostUpdateServerSnapshotByDefault(self) -> None:
        with TemporaryDirectory() as TemporaryDirectoryPath:
            Root = Path(TemporaryDirectoryPath)
            Schem = Root / "Top.litematic"
            FixturePath = Root / "server" / "fixtures" / "Top.FabricFixture.json"
            Fixture = {
                "Blocks": [],
                "Inputs": [],
                "Outputs": [],
                "Arena": {"Origin": [0, 64, 0]},
            }
            FixtureArtifact = SimpleNamespace(
                Path=FixturePath,
                InputCount=0,
                OutputCount=0,
            )
            SnapshotArtifact = SimpleNamespace(
                Path=Root / "Top.ServerUpdated.litematic",
                RequestedPositionCount=12,
                ObservedBlockCount=8,
                WorldReadRequests=1,
                InputCountSetToZero=0,
                SnapshotReadPasses=2,
                InputZeroGameTime=None,
                FirstObservedGameTime=11,
                LastObservedGameTime=11,
            )
            Supervisor = Mock()
            Supervisor.LoadIntoRunningServer.return_value = SimpleNamespace(
                Status="loaded",
                Diagnostics={"LoadedBlocks": 8},
            )
            Output = StringIO()
            with patch(
                "Scripts.Fabric.ImportSchemToFabricServer.BuildFabricFixtureFromSchem",
                return_value=Fixture,
            ), patch(
                "Scripts.Fabric.ImportSchemToFabricServer.WriteFabricFixture",
                return_value=FixtureArtifact,
            ), patch(
                "Scripts.Fabric.ImportSchemToFabricServer.FabricServerSupervisor",
                return_value=Supervisor,
            ), patch(
                "Scripts.Fabric.ImportSchemToFabricServer.CaptureServerUpdatedLitematic",
                return_value=SnapshotArtifact,
            ) as Capture, redirect_stdout(Output):
                ExitCode = ImportSchemToFabricServer.main([
                    str(Schem),
                    "--server-root",
                    str(Root / "server"),
                ])

        self.assertEqual(ExitCode, 0)
        self.assertEqual(
            Capture.call_args.kwargs["OutputPath"],
            Root / "Top.ServerUpdated.litematic",
        )
        self.assertIn("'ObservedBlockCount': 8", Output.getvalue())

    def testFabricTesterGuidesToTheImportedSchematic(self) -> None:
        with patch("builtins.input", side_effect=["build.litematic", "", ""]):
            Arguments = TestSchemInFabricServer.GuidedArguments()

        self.assertEqual(
            Arguments,
            ["build.litematic", "--server-root", CanonicalServerRoot, "--all"],
        )

    def testFabricTesterGuidesToOneTruthTableRow(self) -> None:
        with patch("builtins.input", side_effect=["build.litematic", "", "1", "3"]):
            Arguments = TestSchemInFabricServer.GuidedArguments()

        self.assertEqual(
            Arguments,
            [
                "build.litematic", "--server-root", CanonicalServerRoot,
                "--vector-index", "3",
            ],
        )

    def testFabricTesterGuidesToAllRowsOneAtATime(self) -> None:
        with patch("builtins.input", side_effect=["build.litematic", "", "3"]):
            Arguments = TestSchemInFabricServer.GuidedArguments()

        self.assertEqual(
            Arguments,
            [
                "build.litematic", "--server-root", CanonicalServerRoot,
                "--all-one-at-a-time",
            ],
        )

    def testFabricTesterDefaultsToTheCanonicalServerRoot(self) -> None:
        Arguments = TestSchemInFabricServer.BuildParser().parse_args(["build.litematic"])

        self.assertEqual(Arguments.server_root, DefaultFabricServerRoot())

    def testFabricTesterOffersExplicitOneAndAllModes(self) -> None:
        Parser = TestSchemInFabricServer.BuildParser()

        self.assertTrue(Parser.parse_args(["build.litematic", "--all"]).all)
        self.assertEqual(
            Parser.parse_args(["build.litematic", "--one", "3"]).vector_index,
            3,
        )
        self.assertEqual(
            Parser.parse_args(["build.litematic", "--one-at-a-time", "3"]).vector_index,
            3,
        )
        self.assertTrue(
            Parser.parse_args(["build.litematic", "--all-one-at-a-time"]).all_one_at_a_time,
        )
        with self.assertRaises(SystemExit):
            Parser.parse_args(["build.litematic", "--all", "--vector-index", "3"])

    def testFabricTesterSelectsOneTruthTableRowAndRejectsInvalidRows(self) -> None:
        Vectors = [
            {"Inputs": {"a": False}, "Expected": {"y": False}},
            {"Inputs": {"a": True}, "Expected": {"y": True}},
        ]

        Selected, SelectedIndex = TestSchemInFabricServer.SelectTruthTableVectors(
            Vectors,
            1,
        )

        self.assertEqual(SelectedIndex, 1)
        self.assertEqual(Selected, [Vectors[1]])
        with self.assertRaisesRegex(ValueError, "available rows are 0 through 1"):
            TestSchemInFabricServer.SelectTruthTableVectors(Vectors, 2)

    def testFabricTesterRunsEveryRowIndependentlyInSequentialMode(self) -> None:
        Fixture = object()
        Vectors = [
            {"Inputs": {"a": False}, "Expected": {"y": False}},
            {"Inputs": {"a": True}, "Expected": {"y": True}},
        ]
        Supervisor = Mock()
        Supervisor.Validate.side_effect = [
            FabricServerValidationResult(
                Status="passed",
                Backend="fabric-26.2",
                RuntimeSeconds=0.1,
            ),
            FabricServerValidationResult(
                Status="mismatch",
                Backend="fabric-26.2",
                RuntimeSeconds=0.2,
                Diagnostics={"Error": "output-mismatch:y"},
            ),
        ]
        Reports: list[dict[str, object]] = []
        Pauses: list[tuple[int, int]] = []

        Result, Rows = TestSchemInFabricServer.TestAllVectorsOneAtATime(
            Supervisor,
            Fixture,
            Vectors,
            Reports.append,
            lambda CompletedIndex, VectorCount: Pauses.append(
                (CompletedIndex, VectorCount),
            ),
        )

        self.assertEqual(Result.Status, "mismatch")
        self.assertEqual(Result.RuntimeSeconds, 0.30000000000000004)
        self.assertEqual(Result.Diagnostics["TestedVectors"], 2)
        self.assertEqual(Result.Diagnostics["PassedVectors"], 1)
        self.assertEqual(Result.Diagnostics["FailedVectorIndexes"], [1])
        self.assertEqual(Rows, Reports)
        self.assertEqual([Row["Status"] for Row in Rows], ["passed", "mismatch"])
        self.assertEqual(Pauses, [(0, 2)])
        self.assertEqual(Supervisor.Validate.call_count, 2)
        self.assertEqual(
            Supervisor.Validate.call_args_list[0].kwargs,
            {"Fixture": Fixture, "Vectors": [Vectors[0]]},
        )
        self.assertEqual(
            Supervisor.Validate.call_args_list[1].kwargs,
            {"Fixture": Fixture, "Vectors": [Vectors[1]]},
        )

    def testFabricTesterPromptsForEnterBetweenSequentialRows(self) -> None:
        with patch("builtins.input", return_value="") as Input:
            TestSchemInFabricServer.PauseForNextTruthTableRow(0, 8)

        self.assertEqual(Input.call_count, 1)
        self.assertIn("Row 1/8 complete", Input.call_args.args[0])
        self.assertIn("row 2/8", Input.call_args.args[0])

    def testFabricTesterFailsClosedWhenTheLiveWorldMismatches(self) -> None:
        with TemporaryDirectory() as TemporaryDirectoryPath:
            Root = Path(TemporaryDirectoryPath)
            Schem = Root / "Top.litematic"
            FixtureDirectory = Root / "server" / "fixtures"
            FixtureDirectory.mkdir(parents=True)
            Schem.write_bytes(b"not-read-by-the-tester")
            (FixtureDirectory / "Top.FabricFixture.json").write_text(
                '{"Blocks":[],"Inputs":[{"Name":"a","LeverPosition":[0,0,0]}],"Outputs":[{"Name":"y","LampPosition":[1,0,0]}]}',
                encoding="utf-8",
            )
            Schem.with_suffix(".Nand.json").write_text(
                '{"Module":"Top","Inputs":["a"],"Outputs":["y"],"Gates":[{"Name":"InputA","Kind":"INPUT","Inputs":[],"Outputs":["a"]},{"Name":"OutputY","Kind":"OUTPUT","Inputs":["a"],"Outputs":["y"]}]}',
                encoding="utf-8",
            )
            Result = SimpleNamespace(
                Status="mismatch",
                Backend="fabric-26.2",
                RuntimeSeconds=0.1,
                Diagnostics={"Error": "output-mismatch:y"},
            )
            Output = StringIO()
            with patch(
                "Scripts.Fabric.TestSchemInFabricServer.FabricServerSupervisor",
            ) as Supervisor, redirect_stdout(Output):
                Supervisor.return_value.Validate.return_value = Result
                ExitCode = TestSchemInFabricServer.Main([
                    str(Schem),
                    "--server-root",
                    str(Root / "server"),
                ])

        self.assertEqual(ExitCode, 1)
        self.assertIn("'Status': 'mismatch'", Output.getvalue())
        Vectors = Supervisor.return_value.Validate.call_args.kwargs["Vectors"]
        self.assertEqual(len(Vectors), 2)
        self.assertEqual(
            [Vector["Expected"] for Vector in Vectors],
            [{"y": False}, {"y": True}],
        )

    def testFabricTesterPassesOnlyTheSelectedTruthTableRowToFabric(self) -> None:
        with TemporaryDirectory() as TemporaryDirectoryPath:
            Root = Path(TemporaryDirectoryPath)
            Schem = Root / "Top.litematic"
            FixtureDirectory = Root / "server" / "fixtures"
            FixtureDirectory.mkdir(parents=True)
            Schem.write_bytes(b"not-read-by-the-tester")
            (FixtureDirectory / "Top.FabricFixture.json").write_text(
                '{"Blocks":[],"Inputs":[{"Name":"a","LeverPosition":[0,0,0]}],"Outputs":[{"Name":"y","LampPosition":[1,0,0]}]}',
                encoding="utf-8",
            )
            Schem.with_suffix(".Nand.json").write_text(
                '{"Module":"Top","Inputs":["a"],"Outputs":["y"],"Gates":[{"Name":"InputA","Kind":"INPUT","Inputs":[],"Outputs":["a"]},{"Name":"OutputY","Kind":"OUTPUT","Inputs":["a"],"Outputs":["y"]}]}',
                encoding="utf-8",
            )
            Result = SimpleNamespace(
                Status="passed",
                Backend="fabric-26.2",
                RuntimeSeconds=0.1,
                Diagnostics={"TestedVectors": 1},
            )
            Output = StringIO()
            with patch(
                "Scripts.Fabric.TestSchemInFabricServer.FabricServerSupervisor",
            ) as Supervisor, redirect_stdout(Output):
                Supervisor.return_value.Validate.return_value = Result
                ExitCode = TestSchemInFabricServer.Main([
                    str(Schem),
                    "--server-root",
                    str(Root / "server"),
                    "--vector-index",
                    "1",
                ])

        self.assertEqual(ExitCode, 0)
        self.assertIn("'Vectors': 1", Output.getvalue())
        self.assertIn("'TotalVectors': 2", Output.getvalue())
        self.assertIn("'VectorIndex': 1", Output.getvalue())
        Vectors = Supervisor.return_value.Validate.call_args.kwargs["Vectors"]
        self.assertEqual(
            Vectors,
            [{"Inputs": {"a": True}, "Expected": {"y": True}}],
        )

    def testRoutingGuidesDefaultToSafePreviews(self) -> None:
        with patch("builtins.input", side_effect=[""]):
            self.assertEqual(RunRouterAcceptance.GuidedArguments(), ["--dry-run"])
        with patch("builtins.input", side_effect=[""]), redirect_stdout(StringIO()):
            self.assertEqual(RunFreeroutingBenchmark.Main([]), 0)

    def testSnapshotGuideCollectsExplicitInputPaths(self) -> None:
        with patch("builtins.input", side_effect=["failure.json", "", "manifest.json", "diagram.json", ""]):
            Arguments = CaptureRoutingDesignSnapshot.GuidedArguments()
        self.assertEqual(
            Arguments,
            [
                "--cla4-failure", "failure.json",
                "--output-root", "Output/DesignSnapshots/RoutingAwarePlacementAccess",
                "--acceptance-manifest", "manifest.json",
                "--artifact", "diagram.json",
            ],
        )


if __name__ == "__main__":
    unittest.main()
