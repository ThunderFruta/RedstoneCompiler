from hashlib import sha256
from io import StringIO
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from Compiler.RunReporting import (
    CaptureTerminalOutput,
    FormatResultLines,
    PromoteRunArtifacts,
    WriteRunReport,
)


class RunReportingTests(unittest.TestCase):
    def testResultLinesAlwaysStartWithResultThenTime(self) -> None:
        Lines = FormatResultLines(
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
        )

        self.assertEqual(
            Lines[0],
            "RESULT: FAILURE — FabricServerValidation: infrastructure-failure",
        )
        self.assertEqual(
            Lines[1],
            "TIME: wall=7.385s cpu=8.950s utilization=121.2%",
        )
        self.assertEqual(
            Lines[2],
            "CPU: user=8.000s system=0.500s child=0.450s "
            "average_cores=1.21 logical_cpus=32 routing_limit=auto",
        )
        self.assertNotIn("os_peak", "\n".join(Lines))
        self.assertNotIn("python_peak", "\n".join(Lines))
        self.assertTrue(Lines[3].startswith("OUTPUT: "))
        self.assertTrue(Lines[4].startswith("RAW REPORT: "))

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
                    "Compiler.RunReporting.BuildGitIdentity",
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
