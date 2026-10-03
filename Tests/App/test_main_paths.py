from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from io import StringIO
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import App.CompilerCli as CompilerMainModule
import App.Main as RootMain
from Validation.Fabric import FabricValidationProgress
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

    def testRootBenchmarkUsesCanonicalDefaultAcceptanceMatrix(self) -> None:
        with patch(
            "Tools.Routing.RunRouterAcceptance.Main",
            return_value=9,
        ) as AcceptanceMain:
            self.assertEqual(RootMain.RunBenchmark([]), 9)
        AcceptanceMain.assert_called_once_with(["--matrix", "default"])

    def testParsePromptPathAcceptsQuotedAbsolutePath(self) -> None:
        Expected = Path("/mnt/Projects/RedstoneCompiler/Assets/Examples/RippleCarryAdder4.sv")

        self.assertEqual(ParsePromptPath(f"'{Expected}'"), Expected)
        self.assertEqual(ParsePromptPath(f'"{Expected}"'), Expected)

    def testParsePromptPathPreservesUnquotedPath(self) -> None:
        Expected = Path("Assets/Examples/FullAdder.sv")

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
                "PERF: average_cores=1.00",
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
                    "--input", "Assets/Examples/FullAdder.sv",
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
            self.assertTrue(ResultLines[2].startswith("PERF: "))
            self.assertIn("routing wall=", ResultLines[1])
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
                    if "routing wall=" in Line
                ]),
                1,
            )
            self.assertTrue(any(
                "validation wall=" in Line
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
                    "--input", "Assets/Examples/FullAdder.sv",
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


def test_guided_pytest_report_failure_uses_the_shared_terminal_contract(monkeypatch, capsys):
    Process = SimpleNamespace(stdout=StringIO("1 passed\n"), stderr=StringIO(), wait=lambda: 0)
    monkeypatch.setattr(CompilerMainModule.subprocess, "Popen", lambda *Args, **Keywords: Process)
    def FailReport(**Arguments):
        raise OSError("controlled report failure")
    monkeypatch.setattr(CompilerMainModule, "WriteRunReport", FailReport)
    assert RunPytest() == 1
    Text = capsys.readouterr().err
    assert [Line.split(":", 1)[0] for Line in Text.splitlines()[:3]] == ["RESULT", "TIME", "PERF"]
    assert "Reporting: write-failed" in Text
    assert "stage history unavailable" in Text
    assert "controlled report failure" in Text


@pytest.mark.parametrize("Flags,Selected", [([], None), (["--compiler-hooks"], ()),
    (["--compiler-hook-stage", "physical"], ("physical",)),
    (["--compiler-hooks", "--compiler-hook-stage", "synthesis", "--compiler-hook-stage", "physical"], ("physical", "synthesis"))])
def test_cli_hooks_are_explicit_and_preserve_the_original_failure(tmp_path, monkeypatch, capsys, Flags, Selected):
    from Compilation.Hooks import CompilerHooks
    from PhysicalDesign.Contracts.Failures import RoutingFailure, RoutingFailureReason, RoutingStageError
    Seen = {}
    Failure = RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.NoBoundaryEscape,
        Stage="PortalGeneration", Detail="controlled observer wiring failure"))
    def Compile(**Arguments):
        Seen.update(Arguments)
        raise Failure
    monkeypatch.setattr(CompilerMainModule, "CompileSvToLitematic", Compile)
    assert Main(["--input", "Assets/Examples/FullAdder.sv", "--output", str(tmp_path / "Design.litematic"),
        "--defaults-file", str(tmp_path / "Defaults.json"), "--no-routing-telemetry", *Flags]) == 1
    if Selected is None:
        assert "Hooks" not in Seen
    else:
        assert isinstance(Seen["Hooks"], CompilerHooks)
        assert Seen["Hooks"].Stages == Selected
    Output = capsys.readouterr().out
    assert "RESULT: FAILURE" in Output and "PortalGeneration: NoBoundaryEscape" in Output
    assert "controlled observer wiring failure" in Output


@pytest.mark.parametrize("Selector", ["", "x" * 129])
def test_cli_rejects_invalid_hook_selectors_before_compiling(monkeypatch, Selector):
    with pytest.raises(SystemExit) as Error:
        CompilerMainModule.BuildParser().parse_args(["--compiler-hook-stage", Selector])
    assert Error.value.code == 2


@pytest.mark.parametrize("Flags,ExpectedStages", [(["--compiler-hooks"], {"compile", "frontend.parse", "physical.test"}),
    (["--compiler-hook-stage", "physical"], {"physical.test"})])
def test_cli_exposes_the_published_stage_files_and_preserves_typed_failure(tmp_path, monkeypatch, capsys, Flags, ExpectedStages):
    import json
    from Compilation.Hooks import ObserveCompilerRun, RunCompilerOperation, ReadCompilerTrace
    from PhysicalDesign.Contracts.Failures import RoutingFailure, RoutingFailureReason, RoutingStageError
    Failure = RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.NoBoundaryEscape,
        Stage="PortalGeneration", Detail="controlled split trace failure"))
    @ObserveCompilerRun
    def Compile(**Arguments):
        RunCompilerOperation("frontend.parse", lambda: None)
        def Fail():
            raise Failure
        return RunCompilerOperation("physical.test", Fail)
    monkeypatch.setattr(CompilerMainModule, "CompileSvToLitematic", Compile)
    monkeypatch.setattr(CompilerMainModule, "BuildRunId", lambda: "split-hook-run")
    assert Main(["--input", "Assets/Examples/FullAdder.sv", "--output", str(tmp_path / "Design.litematic"),
        "--defaults-file", str(tmp_path / "Defaults.json"), "--no-routing-telemetry", *Flags]) == 1
    Root = tmp_path / "Runs/split-hook-run"
    Directory = Root / "Design.CompilerHooks"
    Index = json.loads((Directory / "Index.json").read_text())
    assert {Entry["Stage"] for Entry in Index["Files"]} == ExpectedStages
    for Entry in Index["Files"]:
        Member = json.loads((Directory / Entry["Name"]).read_text())
        assert all(Event["Stage"] == Entry["Stage"] for Event in Member["Events"])
    Trace = ReadCompilerTrace(Directory)
    assert Trace["Outcome"] == "failed" and Trace["Failure"]["Stage"] == "PortalGeneration"
    assert not (Root / "Design.CompilerTrace.json").exists()
    Output = capsys.readouterr().out
    assert "RESULT: FAILURE — PortalGeneration: NoBoundaryEscape" in Output
    assert "HOOKS:" in Output and str(Directory / "Index.json") in Output
    assert "HOOKS:" in (Root / "Summary.txt").read_text()
    assert str(Directory / "Index.json") in (Root / "RawDump.txt").read_text()


def test_hook_publication_metadata_error_cannot_replace_the_original_compile_failure(tmp_path, monkeypatch, capsys):
    from Compilation.Hooks import CompilerHooks
    from PhysicalDesign.Contracts.Failures import RoutingFailure, RoutingFailureReason, RoutingStageError
    def BrokenPublication(Self):
        raise RuntimeError("controlled observation metadata failure")
    monkeypatch.setattr(CompilerHooks, "Publication", property(BrokenPublication))
    def Compile(**Arguments):
        raise RoutingStageError(RoutingFailure(Reason=RoutingFailureReason.NoBoundaryEscape,
            Stage="PortalGeneration", Detail="original compile failure"))
    monkeypatch.setattr(CompilerMainModule, "CompileSvToLitematic", Compile)
    assert Main(["--input", "Assets/Examples/FullAdder.sv", "--output", str(tmp_path / "Design.litematic"),
        "--defaults-file", str(tmp_path / "Defaults.json"), "--no-routing-telemetry", "--compiler-hooks"]) == 1
    Output = capsys.readouterr().out
    assert "RESULT: FAILURE — PortalGeneration: NoBoundaryEscape" in Output
    assert "original compile failure" in Output
    assert "HOOKS: unavailable - RuntimeError" in Output
