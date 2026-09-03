from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from io import StringIO
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import App.CompilerCli as CompilerMainModule
import App.Main as RootMain
from Compiler.FabricServer import FabricValidationProgress
from App.CompilerCli import CpuRunTelemetry, Main, ParsePromptPath, RunPytest, TerminalValidationProgressReporter


class MainPathTests(unittest.TestCase):
    def testDetailedTelemetryDefaultsOnWithExplicitOptOut(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            Parser = CompilerMainModule.BuildParser()
            self.assertTrue(Parser.parse_args([]).routing_telemetry)
            self.assertFalse(Parser.parse_args(["--no-routing-telemetry"]).routing_telemetry)
        with patch.dict(os.environ, {"RC_ROUTING_TELEMETRY": "0"}):
            Parser = CompilerMainModule.BuildParser()
            self.assertFalse(Parser.parse_args([]).routing_telemetry)
            self.assertTrue(Parser.parse_args(["--routing-telemetry"]).routing_telemetry)

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
            "Tools.Routing.RunRouterAcceptance.Main",
            return_value=9,
        ) as AcceptanceMain:
            self.assertEqual(RootMain.RunBenchmark([]), 9)
        AcceptanceMain.assert_called_once_with(["--matrix", "default"])

    def testRootFlagCliDelegatesWithoutOpeningGuidedMenu(self) -> None:
        Arguments = ["--input", "Examples/FullAdder.sv"]
        with (
            patch("App.Main.GuidedMenu") as Guided,
            patch("App.Main.CompilerCli.Main", return_value=7) as FlagMain,
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

    @patch("App.CompilerCli.WriteRunReport")
    @patch("App.CompilerCli.subprocess.Popen")
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
                "TIME: total wall=0.010s cpu=0.010s utilization=100.0%",
                "CPU: average_cores=1.00",
                "OUTPUT: 1 passed in 0.01s",
                "RAW REPORT: /tmp/RawDump.txt",
            )
        )

        with patch.dict(os.environ, {"RC_RUN_SCALE_TESTS": "1"}):
            self.assertEqual(RunPytest(), 0)
        Environment = Popen.call_args.kwargs["env"]
        self.assertEqual(Environment["RC_RUN_SCALE_TESTS"], "0")
        Popen.assert_called_once_with(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "Tests",
            ],
            cwd=Path(__file__).resolve().parents[2],
            env=Environment,
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
                TimingCallback = Options["TimingCallback"]
                TimingCallback("Routing", "begin")
                TimingCallback(
                    "RoutingStage",
                    "physical component interface planning",
                )
                TimingCallback("Routing", "finish")
                TimingCallback("Validation", "begin")
                ValidationProgressCallback = Options[
                    "ValidationProgressCallback"
                ]
                ValidationProgressCallback(FabricValidationProgress(
                    Completed=0,
                    Total=8,
                    Stage="waiting for authoritative Fabric server",
                ))
                ValidationProgressCallback(FabricValidationProgress(
                    Completed=8,
                    Total=8,
                    Stage="authoritative Fabric validation complete",
                    Status="passed",
                ))
                TimingCallback("Validation", "finish")
                OutputPath.parent.mkdir(parents=True, exist_ok=True)
                OutputPath.write_bytes(b"litematic")
                DiagramPath.write_text("{}")
                PhysicalDesignPath = OutputPath.with_suffix(
                    ".PhysicalDesign.json"
                )
                PhysicalDesignPath.write_text("{}")
                OutputPath.with_suffix(".PhysicalFixture.json").write_text("{}")
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
                    MchprsValidation=SimpleNamespace(
                        Status="passed",
                        Backend="mchprs",
                        RuntimeSeconds=0.01,
                        Diagnostics={},
                    ),
                    FabricFinalCheck=SimpleNamespace(
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
                patch("App.CompilerCli.CompileSvToLitematic", side_effect=Compile),
                patch("App.CompilerCli.BuildRunId", return_value="run-id"),
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
            self.assertTrue(ResultLines[1].startswith("TIME: total wall="))
            self.assertTrue(ResultLines[2].startswith("TIME: routing wall="))
            self.assertFalse(any(
                Line.startswith("  physical component interface planning:")
                for Line in ResultLines
            ))
            self.assertIn(
                "physical component interface planning:",
                (RunDirectory / "Summary.txt").read_text(),
            )
            self.assertEqual(
                len([
                    Line for Line in ResultLines
                    if Line.startswith("TIME: routing")
                ]),
                1,
            )
            self.assertTrue(any(
                Line.startswith("TIME: validation wall=")
                for Line in ResultLines
            ))
            self.assertIn("VALIDATION [", StandardError.getvalue())
            self.assertIn("8/8 vectors", StandardError.getvalue())
            self.assertIn("PASSED", StandardError.getvalue())

    def testValidationProgressStartsAtZeroAndUsesActualVectorCounts(self) -> None:
        StandardError = StringIO()
        with redirect_stderr(StandardError):
            Reporter = TerminalValidationProgressReporter()
            Reporter(FabricValidationProgress(
                Completed=0,
                Total=512,
                Stage="waiting for authoritative Fabric server",
            ))
            Reporter(FabricValidationProgress(
                Completed=128,
                Total=512,
                Stage="authoritative Fabric truth-table validation",
            ))
            Reporter(FabricValidationProgress(
                Completed=512,
                Total=512,
                Stage="authoritative Fabric validation complete",
                Status="passed",
            ))
            Reporter.Finish()

        Lines = StandardError.getvalue().splitlines()
        self.assertEqual(len(Lines), 3)
        self.assertTrue(all(Line.startswith("VALIDATION [") for Line in Lines))
        self.assertIn("0% 0/512 vectors", Lines[0])
        self.assertIn("25% 128/512 vectors", Lines[1])
        self.assertIn("100% 512/512 vectors", Lines[2])
        self.assertIn("PASSED", Lines[2])

    def testCpuTelemetrySeparatesRoutingStagesFromValidation(self) -> None:
        Telemetry = CpuRunTelemetry()
        Telemetry.RecordPipelineTimingEvent("Routing", "begin")
        Telemetry.RecordPipelineTimingEvent(
            "RoutingStage",
            "physical component interface planning",
        )
        Telemetry.RecordRoutingProgress(SimpleNamespace(
            Stage="spacing 3 | negotiated route-tree construction | 2 conflicts",
        ))
        Telemetry.RecordPipelineTimingEvent("Routing", "finish")
        Telemetry.RecordPipelineTimingEvent("Validation", "begin")
        Telemetry.RecordPipelineTimingEvent("Validation", "finish")

        Summary = Telemetry.BuildSummary()

        self.assertIn("Routing", Summary["Intervals"])
        self.assertIn("Validation", Summary["Intervals"])
        self.assertEqual(
            [Stage["Stage"] for Stage in Summary["RoutingStages"]],
            [
                "routing setup",
                "physical component interface planning",
                "negotiated route-tree construction",
            ],
        )

    def testReportWriteFailureReturnsFailureWithTypedTerminalFallback(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            StandardError = StringIO()
            with (
                patch(
                    "App.CompilerCli.CompileSvToLitematic",
                    side_effect=ValueError("controlled compile failure"),
                ),
                patch(
                    "App.CompilerCli.WriteRunReport",
                    side_effect=PermissionError("denied"),
                ),
                patch("App.CompilerCli.BuildRunId", return_value="run-id"),
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
