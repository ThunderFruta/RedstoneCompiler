"""Focused contract tests for the external Freerouting benchmark adapter."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import runpy
import sys
from tempfile import TemporaryDirectory
import unittest


RepositoryRoot = Path(__file__).resolve().parents[2]
ScriptPath = RepositoryRoot / "Scripts/RunFreeroutingBenchmark.py"
ModuleSpec = importlib.util.spec_from_file_location(
    "RunFreeroutingBenchmark",
    ScriptPath,
)
if ModuleSpec is None or ModuleSpec.loader is None:
    raise RuntimeError("could not load Freerouting benchmark module")
Benchmark = importlib.util.module_from_spec(ModuleSpec)
sys.modules[ModuleSpec.name] = Benchmark
ModuleSpec.loader.exec_module(Benchmark)


class FreeroutingBenchmarkTests(unittest.TestCase):
    """Protect topology preservation, evidence parsing, and matrix parity."""

    def testBenchmarkMatrixMatchesNativeAcceptanceCases(self) -> None:
        NativeNamespace = runpy.run_path(
            str(RepositoryRoot / "Scripts/RunRouterAcceptance.py")
        )
        NativeCases = NativeNamespace["AcceptanceCases"]
        NativeShape = [
            (
                Case.Name,
                Case.ExamplePath,
                Case.TopModule,
                Case.RequiredRuns,
                Case.TruthTableRows,
                Case.RuntimeCeilingSeconds,
            )
            for Case in NativeCases
        ]
        ExternalShape = [
            (
                Case.Name,
                Case.ExamplePath,
                Case.TopModule,
                Case.RequiredRuns,
                Case.TruthTableRows,
                Case.RuntimeCeilingSeconds,
            )
            for Case in Benchmark.BenchmarkCases
        ]
        self.assertEqual(ExternalShape, NativeShape)

    def testAdapterPreservesEveryGateAndSignalHyperedge(self) -> None:
        Payload = {
            "Module": "RepeatedInput",
            "Inputs": ["A", "B"],
            "Outputs": ["Y"],
            "Gates": [
                {
                    "Name": "InputA",
                    "Kind": "INPUT",
                    "Inputs": [],
                    "Outputs": ["A"],
                },
                {
                    "Name": "InputB",
                    "Kind": "INPUT",
                    "Inputs": [],
                    "Outputs": ["B"],
                },
                {
                    "Name": "NandGate0",
                    "Kind": "NAND",
                    "Inputs": ["A", "A"],
                    "Outputs": ["X"],
                },
                {
                    "Name": "NandGate1",
                    "Kind": "NAND",
                    "Inputs": ["X", "B"],
                    "Outputs": ["Y"],
                },
                {
                    "Name": "OutputY",
                    "Kind": "OUTPUT",
                    "Inputs": ["Y"],
                    "Outputs": ["Y$Output"],
                },
            ],
        }

        Problem = Benchmark.BuildExternalProblem(Payload)

        self.assertEqual(len(Problem.Components), 5)
        self.assertEqual(len(Problem.Nets), 4)
        self.assertEqual(sum(Net.SinkCount for Net in Problem.Nets), 5)
        SignalA = next(Net for Net in Problem.Nets if Net.Signal == "A")
        self.assertEqual(SignalA.SinkCount, 2)
        self.assertEqual(
            SignalA.Pins,
            ("U0001-1", "U0003-1", "U0003-2"),
        )
        self.assertEqual(
            {Component.GateName for Component in Problem.Components},
            {Gate["Name"] for Gate in Payload["Gates"]},
        )

        DsnText = Benchmark.BuildDsn(Problem)
        self.assertEqual(DsnText.count("    (net N"), 4)
        self.assertIn("(snap_angle ninety_degree)", DsnText)
        self.assertIn("(use_layer L0 L1 L2 L3)", DsnText)
        self.assertIn("(pins U0001-1 U0003-1 U0003-2)", DsnText)

    def testSesParserMeasuresNormalizedRouteGeometry(self) -> None:
        SesText = """(session Demo
  (routes
    (resolution um 10)
    (network_out
      (net N0001
        (wire (path L1 2000 0 0 10000 0 10000 10000))
        (via Via_Default 10000 10000))
      (net N0002
        (wire (path L2 2000 30000 0 20000 0))))))
"""
        with TemporaryDirectory() as Directory:
            SesPath = Path(Directory) / "Demo.ses"
            SesPath.write_text(SesText)
            Metrics = Benchmark.ParseSesMetrics(SesPath)

        self.assertEqual(Metrics["WireCount"], 2)
        self.assertEqual(Metrics["SegmentCount"], 3)
        self.assertEqual(Metrics["BendCount"], 1)
        self.assertEqual(Metrics["ViaCount"], 1)
        self.assertEqual(Metrics["LayersUsed"], ["L1", "L2"])
        self.assertEqual(Metrics["RoutedNetCount"], 2)
        self.assertAlmostEqual(Metrics["TotalLengthMillimeters"], 3.0)

    def testDrcCanonicalHashIgnoresTimestampButNotFailures(self) -> None:
        CleanPayload = {
            "$schema": "https://schemas.kicad.org/drc.v1.json",
            "coordinate_units": "mm",
            "date": "first",
            "freerouting_version": "Freerouting 2.3.0",
            "source": "Demo.dsn",
            "unconnected_items": [],
            "violations": [],
            "schematic_parity": [],
            "quality_score": 1000.0,
        }
        ImportLog = "SES file import complete: 7 wires, 2 vias imported"
        with TemporaryDirectory() as Directory:
            FirstPath = Path(Directory) / "First.json"
            SecondPath = Path(Directory) / "Second.json"
            FirstPath.write_text(json.dumps(CleanPayload))
            CleanPayload["date"] = "second"
            SecondPath.write_text(json.dumps(CleanPayload))
            First = Benchmark.ParseDrcReport(FirstPath, ImportLog)
            Second = Benchmark.ParseDrcReport(SecondPath, ImportLog)

        self.assertEqual(
            First["CanonicalReportSha256"],
            Second["CanonicalReportSha256"],
        )
        self.assertTrue(First["SessionImportVerified"])
        self.assertEqual(First["ImportedWireCount"], 7)
        self.assertEqual(First["ImportedViaCount"], 2)

    def testClassificationRequiresDrcJsonRatherThanExitZero(self) -> None:
        RouterMetrics = {
            "FinalStatusFound": True,
            "JobState": "COMPLETED",
            "FinalUnroutedItems": 0,
            "RouterReportedViolations": 0,
        }
        DrcMetrics = {
            "ReportFound": True,
            "SessionImportVerified": True,
            "UnconnectedItemGroups": 0,
            "ViolationCount": 1,
            "SchematicParityCount": 0,
        }
        with TemporaryDirectory() as Directory:
            SesPath = Path(Directory) / "Demo.ses"
            SesPath.write_text("(session Demo)")
            Status = Benchmark.ClassifyRun(
                ExitCode=0,
                TimedOut=False,
                SesPath=SesPath,
                RouterMetrics=RouterMetrics,
                DrcExitCode=0,
                DrcTimedOut=False,
                DrcMetrics=DrcMetrics,
            )

        self.assertEqual(Status, "PCB_DRC_VIOLATION")

    def testCleanStatusRequiresSesAndDrcImportCountsToAgree(self) -> None:
        ProblemMetrics = {"RoutableNetCount": 2}
        RouteMetrics = {
            "RoutedNetCount": 2,
            "WireCount": 7,
            "ViaCount": 2,
        }
        DrcMetrics = {
            "ImportedWireCount": 6,
            "ImportedViaCount": 2,
            "FreeroutingVersion": "Freerouting 2.3.0",
        }

        Status = Benchmark.ValidateCrossArtifactEvidence(
            Status="PCB_DRC_CLEAN",
            ProblemMetricsValue=ProblemMetrics,
            RouteMetrics=RouteMetrics,
            DrcMetrics=DrcMetrics,
        )

        self.assertEqual(Status, "DRC_IMPORTED_WIRE_COUNT_MISMATCH")


if __name__ == "__main__":
    unittest.main()
