from hashlib import sha256
from io import StringIO
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from App.RunReporting import CaptureTerminalOutput, FormatResultLines, PromoteRunArtifacts, WriteRunReport


class RunReportingTests(unittest.TestCase):
    def testResultLinesAlwaysStartWithResultThenTime(self) -> None:
        Lines = FormatResultLines(
            IncludeRoutingDetails=True,
            Result="FAILURE",
            WallSeconds=7.385,
            CpuSeconds=8.95,
            Summary="fixture-has-no-trace-probes",
            RawReportPath=Path("/tmp/RawDump.txt"),
            FailureType="FabricServerValidation: infrastructure-failure",
            CpuDetails={
                "UserSeconds": 8.0,
                "SystemSeconds": 0.5,
                "ChildCpuSeconds": 0.45,
                "OsPeak": 11,
                "PythonPeak": 3,
                "LogicalCpus": 32,
                "NativeRoutingLimit": "auto",
            },
            TimingDetails={
                "Intervals": {
                    "Routing": {"WallSeconds": 2.5, "CpuSeconds": 4.0},
                    "Validation": {"WallSeconds": 3.0, "CpuSeconds": 0.3},
                },
                "RoutingStages": [{
                    "Stage": "authoritative resource graph",
                    "WallSeconds": 0.75,
                    "CpuSeconds": 1.0,
                    "Events": 1,
                }],
            },
        )

        self.assertEqual(
            Lines[0],
            "RESULT: FAILURE — FabricServerValidation: infrastructure-failure",
        )
        self.assertEqual(
            Lines[1],
            "TIME: total wall=7.385s cpu=8.950s utilization=121.2%",
        )
        self.assertEqual(
            Lines[2],
            "TIME: routing wall=2.500s cpu=4.000s utilization=160.0%",
        )
        self.assertEqual(
            Lines[3],
            "  authoritative resource graph: wall=0.750s cpu=1.000s average_cores=1.33",
        )
        self.assertEqual(
            len([Line for Line in Lines if Line.startswith("TIME: routing")]),
            1,
        )
        self.assertEqual(
            Lines[4],
            "TIME: validation wall=3.000s cpu=0.300s utilization=10.0%",
        )
        self.assertEqual(
            Lines[5],
            "CPU: user=8.000s system=0.500s child=0.450s "
            "average_cores=1.21 logical_cpus=32 routing_limit=auto",
        )
        self.assertNotIn("os_peak", "\n".join(Lines))
        self.assertNotIn("python_peak", "\n".join(Lines))
        self.assertTrue(Lines[6].startswith("OUTPUT: "))
        self.assertTrue(Lines[7].startswith("RAW REPORT: "))

    def testDetailedTelemetryIsSavedWithoutBeingPrinted(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            Timing = {
                "Intervals": {"Routing": {"WallSeconds": 1.0, "CpuSeconds": 1.5}},
                "RoutingStages": [{"Stage": "expensive interface preparation", "WallSeconds": 1.0, "CpuSeconds": 1.5}],
                "Detailed": {"Status": "complete", "SampleCount": 4},
            }
            with patch("App.RunReporting.BuildGitIdentity", return_value={}):
                Report = WriteRunReport(
                    RunDirectory=Root, Result="SUCCESS", WallSeconds=1.0,
                    CpuSeconds=1.5, Summary="done", RepositoryRoot=Root,
                    StartedAtUtc="start", CompletedAtUtc="finish",
                    Command=["compiler"], WorkingDirectory=Root,
                    TimingDetails=Timing, Details={"CpuTelemetry": Timing},
                )
            Terminal = "\n".join(Report.ResultLines)
            self.assertIn("TIME: routing wall=1.000s", Terminal)
            self.assertIn("CPU: average_cores=1.50", Terminal)
            self.assertIn("TIME: validation not-run", Terminal)
            self.assertNotIn("expensive interface preparation", Terminal)
            self.assertNotIn("TELEMETRY:", Terminal)
            self.assertNotIn("SampleCount", Terminal)
            Saved = Report.SummaryPath.read_text()
            self.assertIn("expensive interface preparation", Saved)
            self.assertIn("TELEMETRY: complete samples=4", Saved)
            self.assertIn('"SampleCount": 4', Report.RawReportPath.read_text())

    def testWriteRunReportKeepsCompleteEvidenceAndSafeEnvironment(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            Artifact = Root / "Circuit.litematic"
            Artifact.write_bytes(b"schematic")
            with (
                patch.dict(
                    os.environ,
                    {
                        "RC_ROUTING_THREADS": "16",
                        "DO_NOT_REPORT_SECRET": "hidden-value",
                    },
                ),
                patch(
                    "App.RunReporting.BuildGitIdentity",
                    return_value={"Branch": "main", "Head": "abc"},
                ),
            ):
                Result = WriteRunReport(
                    RunDirectory=Root,
                    Result="SUCCESS",
                    WallSeconds=1.25,
                    CpuSeconds=2.5,
                    Summary="Circuit compiled.",
                    RepositoryRoot=Root,
                    StartedAtUtc="2026-08-31T00:00:00+00:00",
                    CompletedAtUtc="2026-08-31T00:00:01+00:00",
                    Command=["python", "Main.py"],
                    WorkingDirectory=Root,
                    Stdout="complete stdout line",
                    Stderr="complete stderr line",
                    ExceptionText="traceback text",
                    Details={"StageEvents": ["one", "two"]},
                )

            SummaryLines = Result.SummaryPath.read_text().splitlines()
            self.assertEqual(SummaryLines[0], "RESULT: SUCCESS")
            self.assertTrue(SummaryLines[1].startswith("TIME: "))
            self.assertIn("OUTPUT: Circuit compiled.", SummaryLines)
            RawText = Result.RawReportPath.read_text()
            self.assertIn("complete stdout line", RawText)
            self.assertIn("complete stderr line", RawText)
            self.assertIn("traceback text", RawText)
            self.assertIn("StageEvents", RawText)
            self.assertIn("RC_ROUTING_THREADS", RawText)
            self.assertNotIn("DO_NOT_REPORT_SECRET", RawText)
            self.assertNotIn("hidden-value", RawText)
            self.assertIn(sha256(b"schematic").hexdigest(), RawText)

    def testCaptureTerminalOutputTeesAndRetainsStreams(self) -> None:
        Stdout = StringIO()
        Stderr = StringIO()
        with patch("sys.stdout", Stdout), patch("sys.stderr", Stderr):
            Capture = CaptureTerminalOutput()
            with Capture:
                print("stdout evidence")
                print("stderr evidence", file=__import__("sys").stderr)

        self.assertIn("stdout evidence", Stdout.getvalue())
        self.assertIn("stderr evidence", Stderr.getvalue())
        self.assertIn("stdout evidence", Capture.StdoutText)
        self.assertIn("stderr evidence", Capture.StderrText)

    def testPromoteRunArtifactsAtomicallyKeepsStableNames(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            RunDirectory = Root / "Circuit" / "Runs" / "run"
            RunDirectory.mkdir(parents=True)
            (RunDirectory / "Circuit.litematic").write_bytes(b"new")
            (RunDirectory / "Circuit.Nand.json").write_text("{}")
            StableOutput = Root / "Circuit" / "Circuit.litematic"
            StableOutput.write_bytes(b"old")

            Promoted = PromoteRunArtifacts(
                RunDirectory=RunDirectory,
                RunBaseName="Circuit",
                StableOutputPath=StableOutput,
            )

            self.assertEqual(StableOutput.read_bytes(), b"new")
            self.assertEqual(
                (StableOutput.parent / "Circuit.Nand.json").read_text(),
                "{}",
            )
            self.assertIn(StableOutput, Promoted)


if __name__ == "__main__":
    unittest.main()
