from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from io import StringIO
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import Compiler.Main as CompilerMainModule
import Main as RootMain
from Compiler.Main import Main, ParsePromptPath, RunPytest


class MainPathTests(unittest.TestCase):
    def testRootEntrypointExclusivelyOwnsGuidedCli(self) -> None:
        self.assertFalse(hasattr(CompilerMainModule, "GuidedMenu"))
        self.assertNotIn(
            "--guided",
            CompilerMainModule.BuildParser().format_help(),
        )
        StandardOutput = StringIO()
        with (
            patch("builtins.input", side_effect=["5"]),
            redirect_stdout(StandardOutput),
        ):
            self.assertEqual(RootMain.Main([]), 0)
        Text = StandardOutput.getvalue()
        self.assertIn("RedstoneCompiler", Text)
        self.assertIn("1. Compile SystemVerilog", Text)
        self.assertIn("2. PyTest", Text)
        self.assertIn("3. Benchmark", Text)
        self.assertIn("4. More options", Text)
        self.assertNotIn("2. Configure defaults", Text)

    def testRootGuidedCliNestsDefaultsAndPushUnderMoreOptions(self) -> None:
        StandardOutput = StringIO()
        with (
            patch("builtins.input", side_effect=["4", "4", "5"]),
            redirect_stdout(StandardOutput),
        ):
            self.assertEqual(RootMain.Main([]), 0)
        Text = StandardOutput.getvalue()
        self.assertIn("More options", Text)
        self.assertIn("1. Configure defaults", Text)
        self.assertIn("2. Show defaults", Text)
        self.assertIn("3. Push an existing litematic to Minecraft", Text)
        self.assertIn("4. Back", Text)

    def testRootBenchmarkUsesCanonicalDefaultAcceptanceMatrix(self) -> None:
        with patch(
            "Scripts.Routing.RunRouterAcceptance.Main",
            return_value=9,
        ) as AcceptanceMain:
            self.assertEqual(RootMain.RunBenchmark([]), 9)
        AcceptanceMain.assert_called_once_with(["--matrix", "default"])

    def testRootFlagCliDelegatesWithoutOpeningGuidedMenu(self) -> None:
        Arguments = ["--input", "Examples/FullAdder.sv"]
        with (
            patch("Main.GuidedMenu") as Guided,
            patch("Main.CompilerCli.Main", return_value=7) as FlagMain,
        ):
            self.assertEqual(RootMain.Main(Arguments), 7)
        Guided.assert_not_called()
        FlagMain.assert_called_once_with(Arguments)

    def testParsePromptPathAcceptsQuotedAbsolutePath(self) -> None:
        Expected = Path("/mnt/Projects/RedstoneCompiler/Examples/RippleCarryAdder4.sv")

        self.assertEqual(ParsePromptPath(f"'{Expected}'"), Expected)
        self.assertEqual(ParsePromptPath(f'"{Expected}"'), Expected)

    def testParsePromptPathPreservesUnquotedPath(self) -> None:
        Expected = Path("Examples/FullAdder.sv")

        self.assertEqual(ParsePromptPath(str(Expected)), Expected)

    @patch("Compiler.Main.WriteRunReport")
    @patch("Compiler.Main.subprocess.Popen")
    def testRunPytestUsesActiveInterpreterAndRepositoryRoot(
        self,
        Popen,
        WriteReport,
    ) -> None:
        Process = Popen.return_value
        Process.stdout = StringIO("1 passed in 0.01s\n")
        Process.stderr = StringIO()
        Process.wait.return_value = 0
        WriteReport.return_value = SimpleNamespace(
            ResultLines=(
                "RESULT: SUCCESS",
                "TIME: wall=0.010s cpu=0.010s utilization=100.0%",
                "CPU: average_cores=1.00",
                "OUTPUT: 1 passed in 0.01s",
                "RAW REPORT: /tmp/RawDump.txt",
            )
        )

        self.assertEqual(RunPytest(), 0)
        Popen.assert_called_once_with(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "Tests",
            ],
            cwd=Path(__file__).resolve().parents[2],
            stdout=-1,
            stderr=-1,
            text=True,
        )
        self.assertTrue(WriteReport.called)

    def testSuccessfulCompileUsesImmutableRunAndPromotesStableArtifacts(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            StableOutput = Root / "FullAdder" / "FullAdder.litematic"

            def Compile(**Options):
                OutputPath = Options["OutputPath"]
                DiagramPath = Options["DiagramPath"]
                OutputPath.parent.mkdir(parents=True, exist_ok=True)
                OutputPath.write_bytes(b"litematic")
                DiagramPath.write_text("{}")
                PhysicalDesignPath = OutputPath.with_suffix(
                    ".PhysicalDesign.json"
                )
                PhysicalDesignPath.write_text("{}")
                OutputPath.with_suffix(".FabricFixture.json").write_text("{}")
                Composition = SimpleNamespace(
                    Footprint=10,
                    XYFootprint=20,
                    FullFootprint=30,
                    ComponentOwnedFunctionalBlocks=4,
                    ComponentFunctionalShare=0.4,
                    RoutingOwnedFunctionalBlocks=6,
                    RoutingFunctionalShare=0.6,
                    RawDustBlocks=2,
                    RawDustFunctionalShare=0.2,
                    SupportBlocks=3,
                    AnnotationBlocks=1,
                )
                return SimpleNamespace(
                    OutputPath=OutputPath,
                    DiagramPath=DiagramPath,
                    NandGateCount=1,
                    EstimatedBlocks=10,
                    Width=5,
                    Depth=6,
                    OriginalLogicGateCount=2,
                    OptimizedLogicGateCount=1,
                    FabricServerValidation=SimpleNamespace(
                        Status="passed",
                        Backend="fabric",
                        RuntimeSeconds=0.01,
                        Diagnostics={},
                    ),
                    RoutingMetrics=None,
                    PhysicalDesignPath=PhysicalDesignPath,
                    RequestedStrategy="default",
                    UsedStrategy="default",
                    FallbackUsed=False,
                    FallbackReason=None,
                    RuntimeSeconds=0.05,
                    MaximumNetLengthShare=0.5,
                    BlockComposition=Composition,
                )

            StandardOutput = StringIO()
            StandardError = StringIO()
            with (
                patch("Compiler.Main.CompileSvToLitematic", side_effect=Compile),
                patch("Compiler.Main.BuildRunId", return_value="run-id"),
                redirect_stdout(StandardOutput),
                redirect_stderr(StandardError),
            ):
                ReturnCode = Main([
                    "--input", "Examples/FullAdder.sv",
                    "--output", str(StableOutput),
                    "--defaults-file", str(Root / "Defaults.json"),
                ])

            RunDirectory = StableOutput.parent / "Runs" / "run-id"
            self.assertEqual(ReturnCode, 0)
            self.assertEqual(StableOutput.read_bytes(), b"litematic")
            self.assertTrue((RunDirectory / "FullAdder.litematic").is_file())
            self.assertTrue((RunDirectory / "Summary.txt").is_file())
            self.assertTrue((RunDirectory / "RawDump.txt").is_file())
            ResultLines = StandardOutput.getvalue().splitlines()
            self.assertEqual(ResultLines[0], "RESULT: SUCCESS")
            self.assertTrue(ResultLines[1].startswith("TIME: wall="))

    def testReportWriteFailureReturnsFailureWithTypedTerminalFallback(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            StandardError = StringIO()
            with (
                patch(
                    "Compiler.Main.CompileSvToLitematic",
                    side_effect=ValueError("controlled compile failure"),
                ),
                patch(
                    "Compiler.Main.WriteRunReport",
                    side_effect=PermissionError("denied"),
                ),
                patch("Compiler.Main.BuildRunId", return_value="run-id"),
                redirect_stderr(StandardError),
            ):
                ReturnCode = Main([
                    "--input", "Examples/FullAdder.sv",
                    "--output", str(Root / "Failed.litematic"),
                    "--defaults-file", str(Root / "Defaults.json"),
                ])

            self.assertEqual(ReturnCode, 1)
            Text = StandardError.getvalue()
            self.assertIn("RESULT: FAILURE — Reporting: write-failed", Text)
            self.assertIn("OUTPUT:", Text)
            self.assertIn("RAW REPORT:", Text)
            self.assertIn(
                "Operation failed: controlled compile failure",
                Text,
            )
