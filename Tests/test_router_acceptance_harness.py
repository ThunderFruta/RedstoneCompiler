from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from Scripts.RunRouterAcceptance import (
    AcceptanceCase,
    AcceptanceCases,
    AcceptanceCommandResult,
    AcceptanceConfiguration,
    AcceptedPolicyVersion,
    BuildRunArtifacts,
    DefaultRoutingPublicationReserveSeconds,
    EvaluateRun,
    MaximumDeadlineOverrunSeconds,
    RunAcceptance,
    SubprocessDeadlineGraceSeconds,
)


def BuildPhysicalDesign(
    Case,
    *,
    PlacementFingerprint: str = "placement-stable",
    UsedStrategy: str = "new-router-first",
    FallbackUsed: bool = False,
    RuntimeSeconds: float = 1.0,
    TruthTablePassed: bool = True,
    TruthTableRows: int | None = None,
    Conflicts: int = 0,
    OverflowPeak: int = 0,
    UnresolvedClaims: list[str] | None = None,
    PolicyVersion: str = AcceptedPolicyVersion,
    SimulationBackend: str = "native-parallel",
    ValidationMode: str = "authoritative-exact",
    RouterReliability: dict[str, object] | None = None,
) -> dict[str, object]:
    if TruthTableRows is None:
        TruthTableRows = Case.TruthTableRows
    if UnresolvedClaims is None:
        UnresolvedClaims = []
    return {
        "Strategy": {
            "Requested": "new-router-first",
            "Used": UsedStrategy,
            "FallbackUsed": FallbackUsed,
        },
        "Policy": {
            "PolicyVersion": PolicyVersion,
            "Seed": 0,
        },
        "RouterReliability": {
            "SchemaVersion": "router-reliability-v1",
            "RunVerdict": "ROUTED_AND_SIMULATED",
            "Fingerprints": {
                "Placement": PlacementFingerprint,
                "ResourceGraph": "resource-graph-stable",
            },
            **(RouterReliability or {}),
        },
        "RunSummary": {
            "RuntimeSeconds": RuntimeSeconds,
            "TruthTablePassed": TruthTablePassed,
            "TruthTableRows": TruthTableRows,
            "SimulationBackend": SimulationBackend,
            "Length": 120,
            "Bends": 12,
            "Vias": 3,
            "ReroutedNets": 0,
            "RoutingPasses": 1,
            "Conflicts": Conflicts,
            "OverflowPeak": OverflowPeak,
            "AccessOverflowPeak": 0,
            "AccessOverflowCells": 0,
            "PerNetLength": {"A": 60, "B": 60},
            "MaximumNetLengthShare": 0.5,
        },
        "FinalValidation": {
            "ValidationMode": ValidationMode,
            "ZeroConflicts": Conflicts == 0,
            "ConflictCount": Conflicts,
            "UnresolvedClaims": UnresolvedClaims,
            "UnresolvedClaimCount": len(UnresolvedClaims),
        },
        "RoutingResourceGraph": {
            "OwnershipCounts": {
                "Portal": 4,
                "Route": 120,
            },
        },
    }


def WriteSuccessfulArtifacts(
    Case,
    Artifacts: dict[str, Path],
    **PhysicalChanges,
) -> None:
    Artifacts["RunDirectory"].mkdir(parents=True, exist_ok=True)
    Artifacts["Schematic"].write_bytes(f"design:{Case.Name}".encode("utf-8"))
    Artifacts["TruthTable"].write_text("PASS\n", encoding="utf-8")
    Artifacts["PhysicalDesign"].write_text(
        json.dumps(BuildPhysicalDesign(Case, **PhysicalChanges)) + "\n",
        encoding="utf-8",
    )


def DigestFixture(Value: Path) -> str:
    return sha256(Value.read_bytes()).hexdigest()


class RouterAcceptanceHarnessTests(unittest.TestCase):
    def Configuration(
        self,
        Root: Path,
        *,
        DryRun: bool = False,
    ) -> AcceptanceConfiguration:
        return AcceptanceConfiguration(
            RepositoryRoot=Path(__file__).resolve().parents[1],
            OutputRoot=Root,
            DateLabel="2026-07-21",
            PythonExecutable=Path("/test/python"),
            DryRun=DryRun,
            RoutingThreads=3,
        )

    def testDryRunPlansExactSequentialMatrixWithoutLaunching(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            Calls = []

            def FailIfCalled(**Options):
                Calls.append(Options)
                raise AssertionError("dry-run launched a compiler")

            Configuration = self.Configuration(Root, DryRun=True)
            Manifest = RunAcceptance(
                Configuration,
                CommandRunner=FailIfCalled,
                SourceStateProvider=lambda _Root: {
                    "Revision": "revision",
                    "Dirty": True,
                },
                UtcNowProvider=lambda: "2026-07-21T12:00:00+00:00",
            )

            self.assertEqual(Calls, [])
            self.assertEqual(Manifest["Status"], "DRY_RUN")
            self.assertFalse(Manifest["Accepted"])
            self.assertEqual(Manifest["ExecutionMode"], "sequential")
            self.assertEqual(
                [Run["Circuit"] for Run in Manifest["Runs"]],
                [
                    *("FullAdder" for _ in range(5)),
                    *("RippleCarryAdder4" for _ in range(2)),
                    *("CarryLookaheadAdder4" for _ in range(2)),
                ],
            )
            self.assertEqual(len(Manifest["Runs"]), 9)
            self.assertTrue(all(
                "new-router-first" in Run["Command"]
                for Run in Manifest["Runs"]
            ))
            ExpectedRoutingDeadlines = {
                "FullAdder": 8.0,
                "RippleCarryAdder4": 23.0,
                "CarryLookaheadAdder4": 118.0,
            }
            for Run in Manifest["Runs"]:
                Command = Run["Command"]
                DeadlineIndex = Command.index("--routing-deadline-seconds")
                Requirements = Run["Requirements"]
                self.assertEqual(
                    float(Command[DeadlineIndex + 1]),
                    ExpectedRoutingDeadlines[Run["Circuit"]],
                )
                self.assertEqual(
                    float(Command[DeadlineIndex + 1]),
                    Requirements["RoutingDeadlineSeconds"],
                )
                self.assertEqual(
                    Run["RequestedRoutingDeadlineSeconds"],
                    Requirements["RoutingDeadlineSeconds"],
                )
                self.assertEqual(
                    Run["PublicationReserveSeconds"],
                    Requirements["PublicationReserveSeconds"],
                )
                self.assertLess(
                    Requirements["RoutingDeadlineSeconds"],
                    Requirements["RuntimeCeilingSeconds"],
                )
                self.assertEqual(
                    Requirements["RoutingDeadlineSeconds"]
                    + Requirements["PublicationReserveSeconds"],
                    Requirements["RuntimeCeilingSeconds"],
                )
            self.assertEqual(
                Manifest["RoutingDeadlinePolicy"],
                {
                    "Mode": "wall-ceiling-minus-publication-reserve",
                    "DefaultPublicationReserveSeconds": (
                        DefaultRoutingPublicationReserveSeconds
                    ),
                    "WallRuntimeCeilingsUnchanged": True,
                },
            )
            self.assertEqual(
                [Case["RuntimeCeilingSeconds"] for Case in Manifest["Cases"]],
                [10.0, 25.0, 120.0],
            )
            self.assertEqual(
                [Case["RoutingDeadlineSeconds"] for Case in Manifest["Cases"]],
                [8.0, 23.0, 118.0],
            )
            self.assertTrue(all(
                Case["PublicationReserveSeconds"]
                == DefaultRoutingPublicationReserveSeconds
                for Case in Manifest["Cases"]
            ))
            self.assertEqual(
                Manifest["SubprocessDeadlineGraceSeconds"],
                SubprocessDeadlineGraceSeconds,
            )
            self.assertEqual(
                Manifest["MaximumDeadlineOverrunSeconds"],
                MaximumDeadlineOverrunSeconds,
            )
            self.assertTrue(Configuration.ManifestPath.is_file())

    def testPassingRunsAreSequentialAndDeterministic(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            Calls = []
            Active = 0
            MaximumActive = 0

            def Runner(**Options):
                nonlocal Active, MaximumActive
                Active += 1
                MaximumActive = max(MaximumActive, Active)
                Command = Options["Command"]
                RunDirectory = Path(
                    Command[Command.index("--output") + 1]
                )
                RunName = Command[Command.index("--outputname") + 1]
                Case = next(
                    Value for Value in AcceptanceCases
                    if RunName.startswith(Value.Name)
                )
                Artifacts = BuildRunArtifacts(RunDirectory, RunName)
                WriteSuccessfulArtifacts(Case, Artifacts)
                Calls.append((RunName, Options["TimeoutSeconds"], tuple(Command)))
                Active -= 1
                return AcceptanceCommandResult(
                    ReturnCode=0,
                    Stdout=f"{RunName} stdout\n",
                    Stderr=f"{RunName} stderr\n",
                    RuntimeSeconds=1.0,
                )

            Configuration = self.Configuration(Root)
            Manifest = RunAcceptance(
                Configuration,
                CommandRunner=Runner,
                SourceStateProvider=lambda _Root: {
                    "Revision": "revision",
                    "Dirty": True,
                },
                DesignDigestBuilder=DigestFixture,
                UtcNowProvider=lambda: "2026-07-21T12:00:00+00:00",
            )

            self.assertEqual(MaximumActive, 1)
            self.assertEqual(len(Calls), 9)
            ExpectedTimeouts = [
                Case.RuntimeCeilingSeconds + SubprocessDeadlineGraceSeconds
                for Case in AcceptanceCases
                for _RunIndex in range(Case.RequiredRuns)
            ]
            self.assertEqual(
                [Timeout for _Name, Timeout, _Command in Calls],
                ExpectedTimeouts,
            )
            self.assertEqual(
                [Name for Name, _Timeout, _Command in Calls],
                [
                    *(f"FullAdderRun{Index}" for Index in range(1, 6)),
                    *(f"RippleCarryAdder4Run{Index}" for Index in range(1, 3)),
                    *(f"CarryLookaheadAdder4Run{Index}" for Index in range(1, 3)),
                ],
            )
            self.assertTrue(Manifest["Accepted"])
            self.assertEqual(Manifest["Status"], "PASSED")
            self.assertTrue(all(Run["Accepted"] for Run in Manifest["Runs"]))
            self.assertTrue(all(
                Run["Determinism"]["MatchesBaseline"]
                for Run in Manifest["Runs"]
            ))
            self.assertTrue(all(
                Run["Evaluation"]["Artifacts"]["Stdout"]["Exists"]
                and Run["Evaluation"]["Artifacts"]["Stderr"]["Exists"]
                for Run in Manifest["Runs"]
            ))
            self.assertEqual(
                Manifest["Environment"]["RoutingEnvironment"][
                    "PYTHONHASHSEED"
                ],
                "0",
            )
            self.assertEqual(
                json.loads(Configuration.ManifestPath.read_text(encoding="utf-8"))[
                    "Status"
                ],
                "PASSED",
            )

    def testEvaluatorCapturesRouterReliabilityPerformanceTelemetry(self) -> None:
        Case = AcceptanceCases[0]
        with tempfile.TemporaryDirectory() as DirectoryValue:
            RunDirectory = Path(DirectoryValue) / "FullAdderRun1"
            Artifacts = BuildRunArtifacts(RunDirectory, "FullAdderRun1")
            WriteSuccessfulArtifacts(
                Case,
                Artifacts,
                RouterReliability={
                    "StageTimingsSeconds": {
                        "GlobalGuidePlanning": 0.11,
                        "ResourceGraph": 0.22,
                        "PortalGeneration": 0.33,
                        "RouteTree": 0.44,
                        "CandidateGeneration": 0.55,
                        "Assignment": 0.66,
                        "Total": 2.21,
                    },
                    "NativeWork": {
                        "Batching": {
                            "PortalRequestCount": 120,
                            "PortalTargetCount": 90,
                            "RouteTreeRequestCount": 40,
                            "PortalBatchCount": 12,
                            "PortalCacheHit": 0.72,
                            "RouteTreeBatchCount": 7,
                            "CandidateDiagnostics": {
                                "A": {"DeferredRequests": 1, "RoutedTrees": 2},
                                "B": {"DeferredRequests": 0, "RoutedTrees": 1},
                            },
                        },
                        "RequestCounts": {
                            "PortalRequestCount": 120,
                            "RouteTreeRequestCount": 40,
                        },
                        "CompletedWork": {
                            "PortalCompletedWork": 120,
                            "RouteTreeCompletedWork": 40,
                        },
                    },
                    "Deadline": {
                        "Expired": False,
                        "RemainingMilliseconds": 0,
                    },
                },
            )

            Evaluation, _Evidence = EvaluateRun(
                Case=Case,
                Process=AcceptanceCommandResult(0, "", "", 1.0),
                Artifacts=Artifacts,
                ExpectedSeed=0,
                DesignDigestBuilder=DigestFixture,
            )

            self.assertTrue(Evaluation["Accepted"])
            self.assertEqual(
                Evaluation["Perf"].get("SchemaVersion"),
                "router-performance-v1",
            )
            self.assertEqual(
                Evaluation["Perf"]["StageTimingsSeconds"],
                {
                    "Assignment": 0.66,
                    "CandidateGeneration": 0.55,
                    "GlobalGuidePlanning": 0.11,
                    "PortalGeneration": 0.33,
                    "ResourceGraph": 0.22,
                    "RouteTree": 0.44,
                    "Total": 2.21,
                },
            )
            self.assertEqual(
                Evaluation["Perf"]["NativeWork"]["Batching"],
                {
                    "CandidateDiagnostics": {
                        "A": {
                            "DeferredRequests": 1,
                            "RoutedTrees": 2,
                        },
                        "B": {
                            "DeferredRequests": 0,
                            "RoutedTrees": 1,
                        },
                    },
                    "PortalBatchCount": 12,
                    "PortalCacheHit": 0.72,
                    "PortalRequestCount": 120,
                    "PortalTargetCount": 90,
                    "RouteTreeBatchCount": 7,
                    "RouteTreeRequestCount": 40,
                },
            )
            self.assertEqual(
                Evaluation["Perf"]["NativeWork"][
                    "CandidateDiagnosticsSummary"
                ],
                {
                    "SignalCount": 2,
                    "SignalsWithDeferredRequests": 1,
                },
            )
            self.assertEqual(
                Evaluation["Perf"]["Deadline"],
                {"Expired": False, "RemainingMilliseconds": 0},
            )

    def testPerfTelemetrySchemaSurvivesMissingFields(self) -> None:
        Case = AcceptanceCases[0]
        with tempfile.TemporaryDirectory() as DirectoryValue:
            RunDirectory = Path(DirectoryValue) / "FullAdderRun1"
            Artifacts = BuildRunArtifacts(RunDirectory, "FullAdderRun1")
            WriteSuccessfulArtifacts(Case, Artifacts)
            Evaluation, _Evidence = EvaluateRun(
                Case=Case,
                Process=AcceptanceCommandResult(0, "", "", 1.0),
                Artifacts=Artifacts,
                ExpectedSeed=0,
                DesignDigestBuilder=DigestFixture,
            )

            self.assertTrue(Evaluation["Accepted"])
            self.assertEqual(
                Evaluation["Perf"].get("SchemaVersion"),
                "router-performance-v1",
            )
            self.assertIsInstance(Evaluation["Perf"], dict)
            self.assertIn("StageTimingsSeconds", Evaluation["Perf"])
            self.assertIn("NativeWork", Evaluation["Perf"])
            self.assertIn("Deadline", Evaluation["Perf"])

    def testEvaluatorRejectsEveryDisallowedSuccessShape(self) -> None:
        Case = AcceptanceCases[0]
        Scenarios = (
            (
                "timeout",
                {},
                AcceptanceCommandResult(124, "", "", 10.0, TimedOut=True),
                None,
                "process timed out",
            ),
            (
                "fallback",
                {"FallbackUsed": True},
                AcceptanceCommandResult(0, "", "", 1.0),
                None,
                "fallback was used",
            ),
            (
                "compatibility",
                {"UsedStrategy": "compatibility"},
                AcceptanceCommandResult(0, "", "", 1.0),
                None,
                "used strategy is not new-router-first",
            ),
            (
                "missing-artifact",
                {},
                AcceptanceCommandResult(0, "", "", 1.0),
                "TruthTable",
                "missing required artifact: TruthTable",
            ),
            (
                "reported-runtime",
                {"RuntimeSeconds": 11.0},
                AcceptanceCommandResult(0, "", "", 1.0),
                None,
                "reported runtime exceeded ceiling",
            ),
            (
                "conflict",
                {"Conflicts": 1},
                AcceptanceCommandResult(0, "", "", 1.0),
                None,
                "RunSummary.Conflicts is not zero",
            ),
            (
                "unresolved",
                {"UnresolvedClaims": ["N0"]},
                AcceptanceCommandResult(0, "", "", 1.0),
                None,
                "unresolved claim count is not zero",
            ),
            (
                "overflow",
                {"OverflowPeak": 2},
                AcceptanceCommandResult(0, "", "", 1.0),
                None,
                "overflow peak exceeded 1",
            ),
            (
                "wrong-policy",
                {"PolicyVersion": "physical-design-v9-local-first"},
                AcceptanceCommandResult(0, "", "", 1.0),
                None,
                f"policy version is not {AcceptedPolicyVersion}",
            ),
            (
                "relaxed-simulation",
                {"SimulationBackend": "projected"},
                AcceptanceCommandResult(0, "", "", 1.0),
                None,
                "simulation backend is missing or non-authoritative",
            ),
            (
                "relaxed-validation",
                {"ValidationMode": "projected"},
                AcceptanceCommandResult(0, "", "", 1.0),
                None,
                "final validation mode is not authoritative-exact",
            ),
        )
        for Name, Changes, Process, RemoveArtifact, ExpectedFailure in Scenarios:
            with self.subTest(Name=Name):
                with tempfile.TemporaryDirectory() as DirectoryValue:
                    RunDirectory = Path(DirectoryValue) / "FullAdderRun1"
                    Artifacts = BuildRunArtifacts(
                        RunDirectory,
                        "FullAdderRun1",
                    )
                    WriteSuccessfulArtifacts(Case, Artifacts, **Changes)
                    if RemoveArtifact is not None:
                        Artifacts[RemoveArtifact].unlink()
                    Evaluation, _Evidence = EvaluateRun(
                        Case=Case,
                        Process=Process,
                        Artifacts=Artifacts,
                        ExpectedSeed=0,
                        DesignDigestBuilder=DigestFixture,
                    )

                    self.assertFalse(Evaluation["Accepted"])
                    self.assertTrue(any(
                        ExpectedFailure in Failure
                        for Failure in Evaluation["Failures"]
                    ))

    def testEvaluatorRecordsAndEnforcesSubsecondDeadlineOverrun(self) -> None:
        Case = AcceptanceCases[0]
        with tempfile.TemporaryDirectory() as DirectoryValue:
            RunDirectory = Path(DirectoryValue) / "FullAdderRun1"
            Artifacts = BuildRunArtifacts(RunDirectory, "FullAdderRun1")
            WriteSuccessfulArtifacts(Case, Artifacts)

            WithinEvaluation, _Evidence = EvaluateRun(
                Case=Case,
                Process=AcceptanceCommandResult(
                    0,
                    "",
                    "",
                    Case.RuntimeCeilingSeconds + 0.999,
                ),
                Artifacts=Artifacts,
                ExpectedSeed=0,
                DesignDigestBuilder=DigestFixture,
            )
            BoundaryEvaluation, _Evidence = EvaluateRun(
                Case=Case,
                Process=AcceptanceCommandResult(
                    0,
                    "",
                    "",
                    Case.RuntimeCeilingSeconds + MaximumDeadlineOverrunSeconds,
                ),
                Artifacts=Artifacts,
                ExpectedSeed=0,
                DesignDigestBuilder=DigestFixture,
            )

            self.assertAlmostEqual(
                WithinEvaluation["Process"]["DeadlineOverrunSeconds"],
                0.999,
            )
            self.assertTrue(
                WithinEvaluation["Process"]["DeadlineOverrunWithinLimit"]
            )
            self.assertFalse(
                any(
                    "deadline overrun did not stay below" in Failure
                    for Failure in WithinEvaluation["Failures"]
                )
            )
            self.assertEqual(
                BoundaryEvaluation["Process"]["DeadlineOverrunSeconds"],
                MaximumDeadlineOverrunSeconds,
            )
            self.assertFalse(
                BoundaryEvaluation["Process"]["DeadlineOverrunWithinLimit"]
            )
            self.assertTrue(
                any(
                    "deadline overrun did not stay below" in Failure
                    for Failure in BoundaryEvaluation["Failures"]
                )
            )

    def testEvaluatorKeepsReserveInsideUnchangedWallCeiling(self) -> None:
        Case = AcceptanceCases[0]
        with tempfile.TemporaryDirectory() as DirectoryValue:
            RunDirectory = Path(DirectoryValue) / "FullAdderRun1"
            Artifacts = BuildRunArtifacts(RunDirectory, "FullAdderRun1")
            WriteSuccessfulArtifacts(Case, Artifacts)

            PublicationEvaluation, _Evidence = EvaluateRun(
                Case=Case,
                Process=AcceptanceCommandResult(
                    0,
                    "",
                    "",
                    Case.RoutingDeadlineSeconds
                    + Case.PublicationReserveSeconds / 2.0,
                ),
                Artifacts=Artifacts,
                ExpectedSeed=0,
                DesignDigestBuilder=DigestFixture,
            )
            WallExceededEvaluation, _Evidence = EvaluateRun(
                Case=Case,
                Process=AcceptanceCommandResult(
                    0,
                    "",
                    "",
                    Case.RuntimeCeilingSeconds + 0.000001,
                ),
                Artifacts=Artifacts,
                ExpectedSeed=0,
                DesignDigestBuilder=DigestFixture,
            )

            ProcessRecord = PublicationEvaluation["Process"]
            self.assertTrue(PublicationEvaluation["Accepted"])
            self.assertEqual(
                ProcessRecord["RequestedRoutingDeadlineSeconds"],
                8.0,
            )
            self.assertEqual(ProcessRecord["PublicationReserveSeconds"], 2.0)
            self.assertEqual(ProcessRecord["ProcessEnvelopeSeconds"], 10.0)
            self.assertEqual(ProcessRecord["RuntimeCeilingSeconds"], 10.0)
            self.assertTrue(ProcessRecord["ProcessEnvelopeValid"])
            self.assertFalse(WallExceededEvaluation["Accepted"])
            self.assertTrue(any(
                "wall runtime exceeded ceiling" in Failure
                for Failure in WallExceededEvaluation["Failures"]
            ))

    def testAcceptanceManifestIncludesPerformanceTelemetrySchema(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)

            def Runner(**Options):
                Command = Options["Command"]
                RunDirectory = Path(Command[Command.index("--output") + 1])
                RunName = Command[Command.index("--outputname") + 1]
                Case = next(
                    Value for Value in AcceptanceCases
                    if RunName.startswith(Value.Name)
                )
                BuildArtifacts = BuildRunArtifacts(RunDirectory, RunName)
                WriteSuccessfulArtifacts(
                    Case,
                    BuildArtifacts,
                    RouterReliability={
                        "StageTimingsSeconds": {
                            "GlobalGuidePlanning": 0.10,
                            "Total": 1.10,
                        },
                        "NativeWork": {
                            "Batching": {
                                "PortalRequestCount": 8,
                                "RouteTreeRequestCount": 4,
                                "CandidateDiagnostics": {
                                    "A": {"DeferredRequests": 1}
                                },
                            }
                        },
                    },
                )
                return AcceptanceCommandResult(0, "", "", 1.0)

            Manifest = RunAcceptance(
                self.Configuration(Root),
                CommandRunner=Runner,
                SourceStateProvider=lambda _Root: {
                    "Revision": "revision",
                    "Dirty": False,
                },
                DesignDigestBuilder=DigestFixture,
                UtcNowProvider=lambda: "2026-07-21T12:00:00+00:00",
            )

            for Run in Manifest["Runs"]:
                Perf = Run["Evaluation"].get("Perf")
                self.assertIsInstance(Perf, dict)
                self.assertEqual(
                    Perf.get("SchemaVersion"),
                    "router-performance-v1",
                )
                self.assertIn("StageTimingsSeconds", Perf)
                self.assertIn("NativeWork", Perf)
                self.assertIn("Deadline", Perf)

    def testAcceptanceCaseRejectsInvalidDeadlineEnvelopes(self) -> None:
        BaseOptions = {
            "Name": "Fixture",
            "ExamplePath": Path("Examples/Fixture.sv"),
            "TopModule": "Fixture",
            "RequiredRuns": 1,
            "TruthTableRows": 1,
        }
        with self.assertRaisesRegex(ValueError, "runtime ceiling"):
            AcceptanceCase(
                **BaseOptions,
                RuntimeCeilingSeconds=float("nan"),
            )
        for Reserve in (0.0, float("inf")):
            with self.subTest(Reserve=Reserve):
                with self.assertRaisesRegex(ValueError, "publication reserve"):
                    AcceptanceCase(
                        **BaseOptions,
                        RuntimeCeilingSeconds=10.0,
                        PublicationReserveSeconds=Reserve,
                    )
        with self.assertRaisesRegex(ValueError, "below the runtime ceiling"):
            AcceptanceCase(
                **BaseOptions,
                RuntimeCeilingSeconds=10.0,
                PublicationReserveSeconds=10.0,
            )

    def testRepeatedRunMismatchFailsWholeManifest(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)

            def Runner(**Options):
                Command = Options["Command"]
                RunDirectory = Path(
                    Command[Command.index("--output") + 1]
                )
                RunName = Command[Command.index("--outputname") + 1]
                Case = next(
                    Value for Value in AcceptanceCases
                    if RunName.startswith(Value.Name)
                )
                Fingerprint = (
                    "placement-changed"
                    if RunName == "FullAdderRun2"
                    else "placement-stable"
                )
                WriteSuccessfulArtifacts(
                    Case,
                    BuildRunArtifacts(RunDirectory, RunName),
                    PlacementFingerprint=Fingerprint,
                )
                return AcceptanceCommandResult(0, "", "", 1.0)

            Manifest = RunAcceptance(
                self.Configuration(Root),
                CommandRunner=Runner,
                SourceStateProvider=lambda _Root: {
                    "Revision": "revision",
                    "Dirty": False,
                },
                DesignDigestBuilder=DigestFixture,
                UtcNowProvider=lambda: "2026-07-21T12:00:00+00:00",
            )

            SecondRun = Manifest["Runs"][1]
            self.assertFalse(Manifest["Accepted"])
            self.assertEqual(Manifest["Status"], "FAILED")
            self.assertEqual(
                SecondRun["Determinism"]["MismatchFields"],
                ["PlacementFingerprint"],
            )
            self.assertFalse(SecondRun["Accepted"])

    def testNonzeroDeadlineFailuresRemainInManifestWithArtifacts(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)

            def Runner(**Options):
                Command = Options["Command"]
                RunDirectory = Path(Command[Command.index("--output") + 1])
                RunName = Command[Command.index("--outputname") + 1]
                Artifacts = BuildRunArtifacts(RunDirectory, RunName)
                Artifacts["RunDirectory"].mkdir(parents=True, exist_ok=True)
                Artifacts["RoutingFailure"].write_text(
                    json.dumps({
                        "SchemaVersion": "routing-failure-v1",
                        "Failure": {"Reason": "RuntimeBudgetExceeded"},
                    }) + "\n",
                    encoding="utf-8",
                )
                return AcceptanceCommandResult(
                    ReturnCode=1,
                    Stdout="",
                    Stderr="routing deadline expired\n",
                    RuntimeSeconds=1.0,
                )

            Configuration = self.Configuration(Root)
            Manifest = RunAcceptance(
                Configuration,
                CommandRunner=Runner,
                SourceStateProvider=lambda _Root: {
                    "Revision": "revision",
                    "Dirty": False,
                },
                UtcNowProvider=lambda: "2026-07-21T12:00:00+00:00",
            )

            self.assertEqual(len(Manifest["Runs"]), 1)
            self.assertEqual(Manifest["Status"], "FAILED")
            self.assertFalse(Manifest["Accepted"])
            self.assertEqual(
                Manifest["Runs"][0]["Evaluation"]["Process"]["ReturnCode"],
                1,
            )
            self.assertTrue(
                Manifest["Runs"][0]["Evaluation"]["Artifacts"]
                ["RoutingFailure"]["Exists"]
            )


if __name__ == "__main__":
    unittest.main()
