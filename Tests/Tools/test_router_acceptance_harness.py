from __future__ import annotations

from copy import deepcopy
from contextlib import redirect_stderr
from dataclasses import replace
from hashlib import sha256
from io import StringIO
import json
from math import nextafter
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from Tools.Routing.RunRouterAcceptance import AcceptanceCase, AcceptanceCases, AcceptanceCommandResult, AcceptanceConfiguration, AcceptedPolicyVersion, AuthoritativeServerBackends, BaselinePolicyVersion, BaselineSchemaVersion, BaselineCompatibilityCaseNames, BuildBaselineComparison, BuildComparisonCompatibility, BuildEmittedDesignDigest, BuildParser, BuildLitematicCompositionEvidence, BuildRunArtifacts, BuildResolvedTemplateInputManifest, BuildSourceProvenance, BuildSubprocessTimeoutSeconds, BuildTruthTableSemanticEvidence, CalculateRuntimeStatistics, ExtendedCaseNames, CandidatePolicyVersion, CanonicalArithmeticDigests, CompareCompatibility, CurrentPolicyVersion, DefaultRoutingPublicationReserveSeconds, DefaultPythonExecutable, DeterministicEvidenceFields, EvaluateRun, EvaluateExactInterfaceProofCheckpoint, ExpandedCaseNames, MaximumDeadlineOverrunSeconds, MaximumRuntimeRegressionFraction, MaximumRuntimeSpreadFraction, NormalizeLegacyFullAdderCeilingCompatibility, RegressionCaseNames, ReadBaselineReference, ReadCgroupCpuQuotaProfile, ReadCpuProfile, RequiredRegressionRoutingThreads, RunAcceptance, RunCompilerCommand, SubprocessDeadlineGraceSeconds, SubprocessFinalizationGraceSeconds
from PhysicalDesign.Rendering.SchemWriter import WriteLitematic

FrozenRouterRegressionBaselineSha256 = (
    "40b67e24fd0bcb5fdd42b4f1837ce27f525e63d7f0f0f2e1a5e7e04fe64487a9"
)


def BuildPhysicalDesign(
    Case,
    *,
    PlacementFingerprint: str = "placement-stable",
    CandidateFingerprint: str = "candidate-stable",
    ResourceGraphFingerprint: str = "resource-graph-stable",
    EffectiveWorkFingerprint: str | None = None,
    UsedStrategy: str = "default",
    FallbackUsed: bool = False,
    RuntimeSeconds: float = 1.0,
    TruthTablePassed: bool = True,
    TruthTableRows: int | None = None,
    Conflicts: int = 0,
    OverflowPeak: int = 0,
    UnresolvedClaims: list[str] | None = None,
    PolicyVersion: str = AcceptedPolicyVersion,
    SimulationBackend: str = "fabric-server",
    FabricValidationBackend: str = "fabric-26.2",
    ValidationMode: str = "fabric-server-authoritative",
    RouterReliability: dict[str, object] | None = None,
    Width: int = 10,
    Height: int = 4,
    Depth: int = 20,
    Footprint: int = 200,
    FullFootprint: int = 800,
    ExactNonAirBlocks: int = 300,
) -> dict[str, object]:
    if TruthTableRows is None:
        TruthTableRows = Case.TruthTableRows
    if UnresolvedClaims is None:
        UnresolvedClaims = []
    return {
        "Strategy": {
            "Requested": "default",
            "Used": UsedStrategy,
            "FallbackUsed": FallbackUsed,
        },
        "Policy": {
            "PolicyVersion": PolicyVersion,
            "Seed": 0,
        },
        "RouterReliability": {
            "SchemaVersion": "router-reliability-v1",
            "RunVerdict": "ROUTED_AND_FABRIC_SERVER_VALIDATED",
            "Fingerprints": {
                "Placement": PlacementFingerprint,
                "Candidate": CandidateFingerprint,
                "ResourceGraph": ResourceGraphFingerprint,
                "EffectiveWork": EffectiveWorkFingerprint,
            },
            **(RouterReliability or {}),
        },
        "RunSummary": {
            "RuntimeSeconds": RuntimeSeconds,
            "Width": Width,
            "Height": Height,
            "Depth": Depth,
            "Footprint": Footprint,
            "FullFootprint": FullFootprint,
            "ExactNonAirBlocks": ExactNonAirBlocks,
            "TruthTablePassed": TruthTablePassed,
            "TruthTableRows": TruthTableRows,
            "SimulationBackend": SimulationBackend,
            "FabricServerValidation": {
                "Status": "passed",
                "Backend": FabricValidationBackend,
                "Diagnostics": {
                    "TestedVectors": Case.ExpectedFabricValidationVectorCount,
                },
            },
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
            "FabricServerValidationStatus": "passed",
            "ZeroConflicts": Conflicts == 0,
            "ConflictCount": Conflicts,
            "UnresolvedClaims": UnresolvedClaims,
            "UnresolvedClaimCount": len(UnresolvedClaims),
            "RepeaterOrientationPassed": True,
            "RepeaterOrientationMismatchCount": 0,
            "RepeaterOrientationReadbackRequired": True,
            "RepeaterOrientationReadbackPassed": True,
        },
        "RepeaterOrientation": {
            "SchemaVersion": "repeater-orientation-v1",
            "Contract": "minecraft-java-facing-is-input-side",
            "Passed": True,
            "MismatchCount": 0,
            "ExpectedCount": 0,
            "RenderedCount": 0,
            "RouteCount": 0,
            "TemplateCount": 0,
            "InputFacingCounts": {},
            "ReadbackRequired": True,
            "ReadbackPassed": True,
            "Records": [],
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
    PhysicalDocument = BuildPhysicalDesign(Case, **PhysicalChanges)
    RunSummary = PhysicalDocument["RunSummary"]
    Width = int(RunSummary["Width"])
    Height = int(RunSummary["Height"])
    Depth = int(RunSummary["Depth"])
    NonAirBlocks = int(RunSummary["ExactNonAirBlocks"])
    AllPositions = [
        (X, Y, Z)
        for Y in range(Height)
        for Z in range(Depth)
        for X in range(Width)
    ]
    EnvelopePositions = [
        (0, 0, 0),
        (Width - 1, Height - 1, Depth - 1),
    ]
    SelectedPositions = list(dict.fromkeys(EnvelopePositions))
    SelectedPositions.extend(
        Position
        for Position in AllPositions
        if Position not in SelectedPositions
    )
    SelectedPositions = SelectedPositions[:NonAirBlocks]
    if len(SelectedPositions) != NonAirBlocks:
        raise ValueError("fixture non-air count exceeds its litematic volume")
    WriteLitematic(
        None,
        Artifacts["Schematic"],
        Build=SimpleNamespace(
            Blocks={
                Position: {"Name": "minecraft:stone"}
                for Position in SelectedPositions
            },
            Signs=[],
            RepeaterOrientation=PhysicalDocument[
                "RepeaterOrientation"
            ],
        ),
    )
    FixtureRows = min(Case.TruthTableRows, 8)
    TruthLines = []
    for Index in range(FixtureRows):
        InputValues = [
            int(Value) for Value in format(Index, "03b")
        ]
        Inputs = " ".join(str(Value) for Value in InputValues)
        if Case.Name == "FullAdder":
            A, B, CarryIn = InputValues
            Total = A + B + CarryIn
            Outputs = f"{Total % 2} {Total // 2}"
        else:
            Outputs = str(Index % 2)
        TruthLines.append(
            f"{Inputs} | {Outputs} | {Outputs} | PASS"
        )
    Artifacts["TruthTable"].write_text(
        "\n".join(TruthLines) + "\n",
        encoding="utf-8",
    )
    FabricFixture = {
        "SchemaVersion": 1,
        "TopModule": Case.TopModule,
        "Blocks": [{"Position": [0, 64, 0], "State": {"Name": "minecraft:stone"}}],
        "Inputs": [{"Name": "A", "LeverPosition": [0, 64, 0]}],
        "Outputs": [{"Name": "Y", "LampPosition": [1, 64, 0]}],
        "Arena": {"Origin": [0, 64, 0], "ResetBeforeLoad": True},
    }
    FabricFixtureBytes = (json.dumps(
        FabricFixture,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n").encode("utf-8")
    Artifacts["FabricFixture"].write_bytes(FabricFixtureBytes)
    PhysicalDocument["RunSummary"]["FabricFixture"] = {
        "Sha256": sha256(FabricFixtureBytes).hexdigest(),
        "BlockCount": 1,
        "InputCount": 1,
        "OutputCount": 1,
    }
    Artifacts["PhysicalDesign"].write_text(
        json.dumps(PhysicalDocument) + "\n",
        encoding="utf-8",
    )


def DigestFixture(Value: Path) -> str:
    return BuildEmittedDesignDigest(Value)


def TruthTableEvidenceFixture(Value: Path) -> dict[str, object]:
    Case = next(
        Item
        for Item in sorted(
            AcceptanceCases,
            key=lambda Candidate: len(Candidate.Name),
            reverse=True,
        )
        if Value.name.startswith(Item.Name)
    )
    Digest = CanonicalArithmeticDigests[Case.Name]
    return {
        "RowCount": Case.TruthTableRows,
        "AllRowsPassed": True,
        "ExpectedMatchesSimulated": True,
        "ArithmeticResultSha256": Digest,
        "SimulationResultSha256": Digest,
    }


def SourceProvenanceFixture(
    Configuration: AcceptanceConfiguration,
    SourceState: dict[str, object],
    *,
    SourceDigest: str = "source-digest",
    NativeDigest: str = "native-digest",
) -> dict[str, object]:
    return {
        "SchemaVersion": "router-source-provenance-v1",
        "Git": SourceState,
        "SourceContent": {"AggregateSha256": SourceDigest},
        "BenchmarkInputs": {
            Case.Name: {
                "Path": Case.ExamplePath.as_posix(),
                "Exists": True,
                "SizeBytes": 10,
                "Sha256": f"input-{Case.Name}",
            }
            for Case in AcceptanceCases
        },
        "PhysicalTemplates": {
            "SchemaVersion": "resolved-template-inputs-v1",
            "AggregateSha256": "fixture-template-pack",
            "Templates": {},
        },
        "NativeExtension": {
            "Path": "RedstoneCompiler/RustRouting.fixture.so",
            "Exists": True,
            "SizeBytes": 10,
            "Sha256": NativeDigest,
        },
        "ExpectedPolicyVersion": Configuration.ExpectedPolicyVersion,
    }


class RouterAcceptanceHarnessTests(unittest.TestCase):
    def testOnlyFabricServerIsAnAuthoritativeSimulationBackend(self) -> None:
        self.assertEqual(
            AuthoritativeServerBackends,
            frozenset({"fabric-26.2", "fabric-26.2-canary"}),
        )

    def test_compatibility_exact_interface_checkpoint_accepts_frozen_proof(self):
        FixturePath = (
            Path(__file__).parent.parent
            / "Fixtures"
            / "CompatibilityExactInterfaceProof.json"
        )
        Fixture = json.loads(FixturePath.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as TemporaryDirectory:
            Artifacts = BuildRunArtifacts(
                Path(TemporaryDirectory),
                "CompatibilityProof",
            )
            ExactProof = deepcopy(Fixture["ExactProof"])
            ExactProof.update({
                "Result": "exact-cluster-interface-solve",
                "ExecutableRepairAllowed": False,
            })
            Artifacts["RoutingFailure"].write_text(
                json.dumps({
                    "Failure": {
                        "Diagnostics": {
                            "InterfaceSolve": ExactProof,
                        },
                    },
                }),
                encoding="utf-8",
            )

            Result = EvaluateExactInterfaceProofCheckpoint(
                Artifacts,
                FixturePath,
            )

        self.assertTrue(Result["Accepted"])
        self.assertEqual(Result["Outcome"], "exact-proof")
        self.assertEqual(
            Result["ProofFingerprint"],
            "674e1555d5ec3935",
        )
        self.assertFalse(Result["FallbackOrThrashingObserved"])

    def test_no_hardcoded_circuit_exceptions_in_active_compiler_code(
        self,
    ) -> None:
        RepositoryRoot = Path(__file__).resolve().parents[2]
        OffendingFiles: list[str] = []
        for Candidate in sorted((RepositoryRoot / "Compiler").rglob("*.py")):
            if "CarryLookaheadAdder4" in Candidate.read_text(
                encoding="utf-8"
            ):
                OffendingFiles.append(
                    Candidate.relative_to(RepositoryRoot).as_posix()
                )
        self.assertEqual(OffendingFiles, [])

    def test_compatibility_exact_interface_checkpoint_normalizes_state_order_and_assignments(
        self,
    ):
        FixturePath = (
            Path(__file__).parent.parent
            / "Fixtures"
            / "CompatibilityExactInterfaceProof.json"
        )
        Fixture = json.loads(FixturePath.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as TemporaryDirectory:
            Artifacts = BuildRunArtifacts(
                Path(TemporaryDirectory),
                "CompatibilityProof",
            )
            ExactProof = deepcopy(Fixture["ExactProof"])
            ExactProof.update({
                "Result": "exact-cluster-interface-solve",
                "ExecutableRepairAllowed": False,
            })
            ExactProof["StateProofs"] = [
                {
                    **State,
                    "AssignmentFingerprints": (
                        list(reversed(State["AssignmentFingerprints"]))
                        if isinstance(
                            State.get("AssignmentFingerprints"),
                            list,
                        )
                        else State["AssignmentFingerprints"]
                    ),
                }
                for State in reversed(ExactProof["StateProofs"])
            ]
            Artifacts["RoutingFailure"].write_text(
                json.dumps({
                    "Failure": {
                        "Diagnostics": {
                            "InterfaceSolve": ExactProof,
                        },
                    },
                }),
                encoding="utf-8",
            )

            Result = EvaluateExactInterfaceProofCheckpoint(
                Artifacts,
                FixturePath,
            )

        self.assertTrue(Result["Accepted"])
        self.assertEqual(Result["Outcome"], "exact-proof")
        self.assertEqual(
            Result["ProofFingerprint"],
            "674e1555d5ec3935",
        )
        self.assertEqual(Result["Failures"], [])

    def test_compatibility_exact_interface_checkpoint_rejects_changed_proof_or_retry(self):
        FixturePath = (
            Path(__file__).parent.parent
            / "Fixtures"
            / "CompatibilityExactInterfaceProof.json"
        )
        Fixture = json.loads(FixturePath.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as TemporaryDirectory:
            Artifacts = BuildRunArtifacts(
                Path(TemporaryDirectory),
                "CompatibilityProof",
            )
            ExactProof = deepcopy(Fixture["ExactProof"])
            ExactProof.update({
                "Result": "exact-cluster-interface-solve",
                "ProofFingerprint": "changed",
                "ExecutableRepairAllowed": False,
                "LaterAction": {
                    "Result": "topology-cut-epoch-created",
                },
            })
            Artifacts["RoutingFailure"].write_text(
                json.dumps({"InterfaceSolve": ExactProof}),
                encoding="utf-8",
            )

            Result = EvaluateExactInterfaceProofCheckpoint(
                Artifacts,
                FixturePath,
            )

        self.assertFalse(Result["Accepted"])
        self.assertTrue(Result["FallbackOrThrashingObserved"])
        self.assertIn(
            "exact-interface proof mismatch: ProofFingerprint",
            Result["Failures"],
        )

    def Configuration(
        self,
        Root: Path,
        *,
        DryRun: bool = False,
        BaselineMode: str | None = None,
        BaselinePath: Path | None = None,
        ExpectedPolicyVersion: str | None = None,
        MatrixMode: str = "default",
        IncludeCla4: bool = False,
    ) -> AcceptanceConfiguration:
        if ExpectedPolicyVersion is None:
            ExpectedPolicyVersion = (
                BaselinePolicyVersion
                if BaselineMode == "capture"
                else AcceptedPolicyVersion
            )
        return AcceptanceConfiguration(
            RepositoryRoot=Path(__file__).resolve().parents[2],
            OutputRoot=Root,
            DateLabel="2026-07-21",
            PythonExecutable=Path("/test/python"),
            DryRun=DryRun,
            RoutingThreads=(
                RequiredRegressionRoutingThreads
                if BaselineMode is not None
                else 3
            ),
            BaselineMode=BaselineMode,
            BaselinePath=BaselinePath,
            ExpectedPolicyVersion=ExpectedPolicyVersion,
            MatrixMode=MatrixMode,
            IncludeCla4=IncludeCla4,
        )

    def SyntheticRunner(
        self,
        *,
        PolicyVersion: str,
        Calls: list[str] | None = None,
        FailureRun: str | None = None,
        RuntimeByRun: dict[str, float] | None = None,
        PhysicalChangesByRun: dict[str, dict[str, object]] | None = None,
    ):
        RuntimeByRun = RuntimeByRun or {}
        PhysicalChangesByRun = PhysicalChangesByRun or {}

        def Runner(**Options):
            Command = Options["Command"]
            RunDirectory = Path(Command[Command.index("--output") + 1])
            RunName = Command[Command.index("--outputname") + 1]
            if Calls is not None:
                Calls.append(RunName)
            Artifacts = BuildRunArtifacts(RunDirectory, RunName)
            if RunName == FailureRun:
                Artifacts["RunDirectory"].mkdir(
                    parents=True,
                    exist_ok=True,
                )
                Artifacts["RoutingFailure"].write_text(
                    '{"Failure":{"Reason":"fixture"}}\n',
                    encoding="utf-8",
                )
                return AcceptanceCommandResult(
                    1,
                    "",
                    "fixture failure",
                    RuntimeByRun.get(RunName, 1.0),
                )
            Case = next(
                Value
                for Value in sorted(
                    AcceptanceCases,
                    key=lambda Candidate: len(Candidate.Name),
                    reverse=True,
                )
                if RunName.startswith(Value.Name)
            )
            Runtime = RuntimeByRun.get(RunName, 1.0)
            WriteSuccessfulArtifacts(
                Case,
                Artifacts,
                PolicyVersion=PolicyVersion,
                RuntimeSeconds=Runtime,
                **PhysicalChangesByRun.get(RunName, {}),
            )
            return AcceptanceCommandResult(0, "", "", Runtime)

        return Runner

    def CaptureBaseline(
        self,
        Root: Path,
    ) -> tuple[Path, dict[str, object]]:
        BaselinePath = Root / "RouterRegressionBaseline.json"
        Configuration = self.Configuration(
            Root / "capture",
            BaselineMode="capture",
            BaselinePath=BaselinePath,
        )
        Manifest = RunAcceptance(
            Configuration,
            CommandRunner=self.SyntheticRunner(
                PolicyVersion=BaselinePolicyVersion,
            ),
            SourceStateProvider=lambda _Root: {
                "Revision": "baseline-revision",
                "Dirty": True,
            },
            SourceProvenanceProvider=SourceProvenanceFixture,
            DesignDigestBuilder=DigestFixture,
            TruthTableEvidenceBuilder=TruthTableEvidenceFixture,
            UtcNowProvider=lambda: "2026-07-21T12:00:00+00:00",
        )
        self.assertTrue(Manifest["Accepted"])
        self.assertTrue(BaselinePath.is_file())
        return BaselinePath, Manifest

    def PromoteCompatibilityBaseline(
        self,
        Root: Path,
    ) -> tuple[Path, dict[str, object]]:
        BaselinePath, _CaptureManifest = self.CaptureBaseline(Root)
        Manifest = RunAcceptance(
            self.Configuration(
                Root / "first-candidate",
                BaselineMode="compare",
                BaselinePath=BaselinePath,
                IncludeCla4=True,
            ),
            CommandRunner=self.SyntheticRunner(
                PolicyVersion=CandidatePolicyVersion,
            ),
            SourceStateProvider=lambda _Root: {
                "Revision": "candidate-revision",
                "Dirty": True,
            },
            SourceProvenanceProvider=SourceProvenanceFixture,
            DesignDigestBuilder=DigestFixture,
            TruthTableEvidenceBuilder=TruthTableEvidenceFixture,
            UtcNowProvider=lambda: "2026-07-21T12:00:00+00:00",
        )
        self.assertTrue(Manifest["Accepted"])
        self.assertTrue(
            Manifest["BaselineComparison"]["CompatibilityPromotionPassed"]
        )
        return BaselinePath, Manifest

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
                SourceProvenanceProvider=SourceProvenanceFixture,
                UtcNowProvider=lambda: "2026-07-21T12:00:00+00:00",
            )

            self.assertEqual(Calls, [])
            self.assertEqual(Manifest["Status"], "DRY_RUN")
            self.assertFalse(Manifest["Accepted"])
            self.assertEqual(Manifest["ExecutionMode"], "sequential")
            self.assertEqual(
                [Run["Circuit"] for Run in Manifest["Runs"]],
                [
                    "FullAdder",
                    "RippleCarryAdder4",
                    "RippleCarryAdder8",
                ],
            )
            self.assertEqual(len(Manifest["Runs"]), 3)
            self.assertTrue(all(
                Case["RequiredRuns"] == 1
                for Case in Manifest["Cases"]
            ))
            self.assertTrue(all(
                "default" in Run["Command"]
                for Run in Manifest["Runs"]
            ))
            ExpectedRoutingDeadlines = {
                "FullAdder": 13.0,
                "RippleCarryAdder4": 23.0,
                "RippleCarryAdder8": 28.0,
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
                    "SubprocessFinalizationGraceSeconds": (
                        SubprocessFinalizationGraceSeconds
                    ),
                    "CaptureTimeoutGraceSeconds": 0.0,
                },
            )
            self.assertEqual(
                [Case["RuntimeCeilingSeconds"] for Case in Manifest["Cases"]],
                [15.0, 25.0, 30.0],
            )
            self.assertEqual(
                [Case["RoutingDeadlineSeconds"] for Case in Manifest["Cases"]],
                [13.0, 23.0, 28.0],
            )
            self.assertTrue(all(
                Case["PublicationReserveSeconds"]
                == DefaultRoutingPublicationReserveSeconds
                for Case in Manifest["Cases"]
            ))
            self.assertEqual(
                Manifest["SubprocessDeadlineGraceSeconds"],
                0.0,
            )
            self.assertEqual(
                Manifest["MaximumDeadlineOverrunSeconds"],
                MaximumDeadlineOverrunSeconds,
            )
            self.assertTrue(Configuration.ManifestPath.is_file())

    def testDryRunWithIncludeCla4IncludesExtendedCase(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            Calls = []

            def FailIfCalled(**Options):
                Calls.append(Options)
                raise AssertionError("dry-run launched a compiler")

            Manifest = RunAcceptance(
                self.Configuration(
                    Root,
                    DryRun=True,
                    IncludeCla4=True,
                ),
                CommandRunner=FailIfCalled,
                SourceStateProvider=lambda _Root: {
                    "Revision": "revision",
                    "Dirty": True,
                },
                SourceProvenanceProvider=SourceProvenanceFixture,
                UtcNowProvider=lambda: "2026-07-21T12:00:00+00:00",
            )

            self.assertEqual(Calls, [])
            self.assertEqual(Manifest["Status"], "DRY_RUN")
            self.assertFalse(Manifest["Accepted"])
            self.assertEqual(
                [Run["Circuit"] for Run in Manifest["Runs"]],
                [
                    "FullAdder",
                    "RippleCarryAdder4",
                    "RippleCarryAdder8",
                    "CarryLookaheadAdder4",
                ],
            )
            self.assertEqual(len(Manifest["Runs"]), 4)

    def testExpandedDryRunPlansEveryExampleWithoutFailFast(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            Calls = []

            def FailIfCalled(**Options):
                Calls.append(Options)
                raise AssertionError("dry-run launched a compiler")

            Manifest = RunAcceptance(
                self.Configuration(
                    Root,
                    DryRun=True,
                    MatrixMode="expanded",
                ),
                CommandRunner=FailIfCalled,
                SourceStateProvider=lambda _Root: {
                    "Revision": "revision",
                    "Dirty": True,
                },
                SourceProvenanceProvider=SourceProvenanceFixture,
                UtcNowProvider=lambda: "2026-07-21T12:00:00+00:00",
            )

            self.assertEqual(Calls, [])
            self.assertEqual(Manifest["MatrixMode"], "expanded")
            self.assertFalse(Manifest["FailFast"])
            self.assertEqual(
                [Run["Circuit"] for Run in Manifest["Runs"]],
                [
                    "HalfAdder",
                    "FullAdder",
                    "RippleCarryAdder4",
                    "RippleCarryAdder8",
                    "DecimalToBinary4",
                    "TFlipFlopLatch",
                    "CarryLookaheadAdder4",
                ],
            )
            self.assertEqual(len(Manifest["Runs"]), 7)
            self.assertEqual(
                set(Run["Circuit"] for Run in Manifest["Runs"]),
                set(ExpandedCaseNames),
            )

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
                SourceProvenanceProvider=SourceProvenanceFixture,
                DesignDigestBuilder=DigestFixture,
                TruthTableEvidenceBuilder=TruthTableEvidenceFixture,
                UtcNowProvider=lambda: "2026-07-21T12:00:00+00:00",
            )

            self.assertEqual(MaximumActive, 1)
            self.assertEqual(len(Calls), 3)
            ExpectedTimeouts = [
                Case.RuntimeCeilingSeconds
                + SubprocessFinalizationGraceSeconds
                for Case in AcceptanceCases
                if Case.Name in RegressionCaseNames
            ]
            self.assertEqual(
                [Timeout for _Name, Timeout, _Command in Calls],
                ExpectedTimeouts,
            )
            self.assertEqual(
                [Name for Name, _Timeout, _Command in Calls],
                [
                    "FullAdderRun1",
                    "RippleCarryAdder4Run1",
                    "RippleCarryAdder8Run1",
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
                Manifest["SubprocessDeadlineGraceSeconds"],
                0.0,
            )
            self.assertEqual(
                json.loads(Configuration.ManifestPath.read_text(encoding="utf-8"))[
                    "Status"
                ],
                "PASSED",
            )
            for RunName, _Timeout, _Command in Calls:
                RunDirectory = Configuration.RecoveryRoot / RunName
                SummaryLines = (
                    RunDirectory / "Summary.txt"
                ).read_text(encoding="utf-8").splitlines()
                RawText = (
                    RunDirectory / "RawDump.txt"
                ).read_text(encoding="utf-8")
                self.assertEqual(SummaryLines[0], "RESULT: SUCCESS")
                self.assertTrue(SummaryLines[1].startswith("TIME: total wall="))
                self.assertIn(f"{RunName} stdout", RawText)
                self.assertIn(f"{RunName} stderr", RawText)
                self.assertIn("Evaluation", RawText)

    def testTimeoutGraceIsExplicitAndCaptureOnly(self) -> None:
        Case = next(
            Case for Case in AcceptanceCases if Case.Name == "FullAdder"
        )
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            Normal = self.Configuration(Root / "normal")
            Capture = self.Configuration(
                Root / "capture",
                BaselineMode="capture",
                BaselinePath=Root / "baseline.json",
            )
            Comparison = self.Configuration(
                Root / "compare",
                BaselineMode="compare",
                BaselinePath=Root / "baseline.json",
            )
            ExplicitCapture = replace(
                Capture,
                CaptureTimeoutGraceSeconds=(
                    SubprocessDeadlineGraceSeconds
                ),
            )

            self.assertEqual(
                BuildSubprocessTimeoutSeconds(Case, Normal),
                Case.RuntimeCeilingSeconds
                + SubprocessFinalizationGraceSeconds,
            )
            self.assertEqual(
                BuildSubprocessTimeoutSeconds(Case, Capture),
                Case.RuntimeCeilingSeconds
                + SubprocessFinalizationGraceSeconds,
            )
            self.assertEqual(
                BuildSubprocessTimeoutSeconds(Case, Comparison),
                Case.RuntimeCeilingSeconds
                + SubprocessFinalizationGraceSeconds,
            )
            self.assertEqual(
                BuildSubprocessTimeoutSeconds(Case, ExplicitCapture),
                Case.RuntimeCeilingSeconds
                + SubprocessFinalizationGraceSeconds
                + SubprocessDeadlineGraceSeconds,
            )
            with self.assertRaisesRegex(
                ValueError,
                "only during explicit baseline capture",
            ):
                replace(
                    Normal,
                    CaptureTimeoutGraceSeconds=1.0,
                )

            Timeouts: list[float] = []
            Synthetic = self.SyntheticRunner(
                PolicyVersion=BaselinePolicyVersion,
            )

            def Runner(**Options):
                Timeouts.append(Options["TimeoutSeconds"])
                return Synthetic(**Options)

            Manifest = RunAcceptance(
                ExplicitCapture,
                CommandRunner=Runner,
                SourceStateProvider=lambda _Root: {
                    "Revision": "revision",
                    "Dirty": True,
                },
                SourceProvenanceProvider=SourceProvenanceFixture,
                DesignDigestBuilder=DigestFixture,
                TruthTableEvidenceBuilder=TruthTableEvidenceFixture,
                UtcNowProvider=(
                    lambda: "2026-07-21T12:00:00+00:00"
                ),
            )

            self.assertTrue(Manifest["Accepted"])
            self.assertEqual(
                Manifest["SubprocessDeadlineGraceSeconds"],
                SubprocessDeadlineGraceSeconds,
            )
            self.assertEqual(
                Timeouts,
                [
                    CaseValue.RuntimeCeilingSeconds
                    + SubprocessFinalizationGraceSeconds
                    + SubprocessDeadlineGraceSeconds
                    for CaseValue in AcceptanceCases
                    if CaseValue.Name in RegressionCaseNames
                    for _RunIndex in range(
                        CaseValue.RequiredRuns
                        + (1 if CaseValue.Name == "FullAdder" else 0)
                    )
                ],
            )

    def testCompilerTimeoutKillsTheWorkerProcessGroup(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Result = RunCompilerCommand(
                Command=[
                    sys.executable,
                    "-c",
                    (
                        "import subprocess, sys, time; "
                        "subprocess.Popen([sys.executable, '-c', "
                        "'import time; time.sleep(5)']); "
                        "print('started', flush=True); "
                        "time.sleep(5)"
                    ),
                ],
                WorkingDirectory=Path(DirectoryValue),
                Environment={"PYTHONHASHSEED": "0"},
                TimeoutSeconds=0.05,
            )

        self.assertTrue(Result.TimedOut)
        self.assertEqual(Result.ReturnCode, 124)
        self.assertIn("started", Result.Stdout)
        self.assertIn("wall-clock ceiling", Result.Stderr)
        self.assertLess(Result.RuntimeSeconds, 1.0)

    def testEvaluatorCapturesRouterReliabilityPerformanceTelemetry(self) -> None:
        Case = next(
            Case for Case in AcceptanceCases if Case.Name == "FullAdder"
        )
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
        Case = next(
            Case for Case in AcceptanceCases if Case.Name == "FullAdder"
        )
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

    def testEvaluatorCrossChecksEmittedLitematicComposition(self) -> None:
        Case = next(
            Case for Case in AcceptanceCases if Case.Name == "FullAdder"
        )
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            MatchingArtifacts = BuildRunArtifacts(
                Root / "matching",
                "FullAdderRun1",
            )
            WriteSuccessfulArtifacts(Case, MatchingArtifacts)
            MatchingEvaluation, MatchingEvidence = EvaluateRun(
                Case=Case,
                Process=AcceptanceCommandResult(0, "", "", 1.0),
                Artifacts=MatchingArtifacts,
                ExpectedSeed=0,
                DesignDigestBuilder=DigestFixture,
            )

            ExpectedComposition = {
                "Width": 10,
                "Height": 4,
                "Depth": 20,
                "Footprint": 200,
                "FullFootprint": 800,
                "ExactNonAirBlocks": 300,
            }
            self.assertTrue(MatchingEvaluation["Accepted"])
            self.assertEqual(
                MatchingEvaluation["Observed"]["LitematicComposition"],
                ExpectedComposition,
            )
            self.assertEqual(
                MatchingEvidence["LitematicComposition"],
                ExpectedComposition,
            )
            self.assertEqual(
                BuildLitematicCompositionEvidence(
                    MatchingArtifacts["Schematic"]
                ),
                ExpectedComposition,
            )

            for Name, PhysicalChanges, ExpectedMismatch in (
                (
                    "dimensions",
                    {
                        "Width": 11,
                        "Footprint": 220,
                        "FullFootprint": 880,
                    },
                    "Width 10 != RunSummary 11",
                ),
                (
                    "non-air",
                    {"ExactNonAirBlocks": 299},
                    "ExactNonAirBlocks 300 != RunSummary 299",
                ),
            ):
                with self.subTest(Name=Name):
                    Artifacts = BuildRunArtifacts(
                        Root / Name,
                        "FullAdderRun1",
                    )
                    WriteSuccessfulArtifacts(Case, Artifacts)
                    PhysicalDocument = json.loads(
                        Artifacts["PhysicalDesign"].read_text(
                            encoding="utf-8"
                        )
                    )
                    PhysicalDocument["RunSummary"].update(PhysicalChanges)
                    Artifacts["PhysicalDesign"].write_text(
                        json.dumps(PhysicalDocument) + "\n",
                        encoding="utf-8",
                    )

                    Evaluation, Evidence = EvaluateRun(
                        Case=Case,
                        Process=AcceptanceCommandResult(
                            0,
                            "",
                            "",
                            1.0,
                        ),
                        Artifacts=Artifacts,
                        ExpectedSeed=0,
                        DesignDigestBuilder=DigestFixture,
                    )

                    self.assertFalse(Evaluation["Accepted"])
                    self.assertIsNotNone(Evidence)
                    self.assertTrue(any(
                        "emitted litematic composition mismatch"
                        in Failure
                        and ExpectedMismatch in Failure
                        for Failure in Evaluation["Failures"]
                    ))

    def testEvaluatorRejectsEveryDisallowedSuccessShape(self) -> None:
        Case = next(
            Case for Case in AcceptanceCases if Case.Name == "FullAdder"
        )
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
                "used strategy is not default",
            ),
            (
                "missing-artifact",
                {},
                AcceptanceCommandResult(0, "", "", 1.0),
                "FabricFixture",
                "missing required artifact: FabricFixture",
            ),
            (
                "reported-runtime",
                {"RuntimeSeconds": Case.RuntimeCeilingSeconds + 1.0},
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
                {"FabricValidationBackend": "projected"},
                AcceptanceCommandResult(0, "", "", 1.0),
                None,
                "Fabric server backend is missing or non-authoritative",
            ),
            (
                "relaxed-validation",
                {"ValidationMode": "projected"},
                AcceptanceCommandResult(0, "", "", 1.0),
                None,
                "final validation mode is not a supported physical authority",
            ),
            (
                "missing-candidate-fingerprint",
                {"CandidateFingerprint": ""},
                AcceptanceCommandResult(0, "", "", 1.0),
                None,
                "missing candidate fingerprint",
            ),
            (
                "missing-resource-graph-fingerprint",
                {"ResourceGraphFingerprint": ""},
                AcceptanceCommandResult(0, "", "", 1.0),
                None,
                "missing resource-graph fingerprint",
            ),
            (
                "invalid-effective-work-fingerprint",
                {"EffectiveWorkFingerprint": ""},
                AcceptanceCommandResult(0, "", "", 1.0),
                None,
                "invalid effective-work fingerprint",
            ),
            (
                "invalid-xz-footprint",
                {"Footprint": 199},
                AcceptanceCommandResult(0, "", "", 1.0),
                None,
                "Footprint is not Width * Depth",
            ),
            (
                "invalid-full-footprint",
                {"FullFootprint": 799},
                AcceptanceCommandResult(0, "", "", 1.0),
                None,
                "FullFootprint is not Width * Height * Depth",
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
        Case = next(
            Case for Case in AcceptanceCases if Case.Name == "FullAdder"
        )
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
        Case = next(
            Case for Case in AcceptanceCases if Case.Name == "FullAdder"
        )
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
                13.0,
            )
            self.assertEqual(ProcessRecord["PublicationReserveSeconds"], 2.0)
            self.assertEqual(ProcessRecord["ProcessEnvelopeSeconds"], 15.0)
            self.assertEqual(ProcessRecord["RuntimeCeilingSeconds"], 15.0)
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
                SourceProvenanceProvider=SourceProvenanceFixture,
                DesignDigestBuilder=DigestFixture,
                TruthTableEvidenceBuilder=TruthTableEvidenceFixture,
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
            "ExamplePath": Path("Assets/Examples/Fixture.sv"),
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
                    PolicyVersion=BaselinePolicyVersion,
                )
                return AcceptanceCommandResult(0, "", "", 1.0)

            Manifest = RunAcceptance(
                self.Configuration(
                    Root,
                    BaselineMode="capture",
                    BaselinePath=Root / "baseline.json",
                ),
                CommandRunner=Runner,
                SourceStateProvider=lambda _Root: {
                    "Revision": "revision",
                    "Dirty": False,
                },
                SourceProvenanceProvider=SourceProvenanceFixture,
                DesignDigestBuilder=DigestFixture,
                TruthTableEvidenceBuilder=TruthTableEvidenceFixture,
                UtcNowProvider=lambda: "2026-07-21T12:00:00+00:00",
            )

            SecondRun = next(
                Run for Run in Manifest["Runs"]
                if Run["RunName"] == "FullAdderRun2"
            )
            self.assertFalse(Manifest["Accepted"])
            self.assertEqual(Manifest["Status"], "FAILED")
            self.assertEqual(
                SecondRun["Determinism"]["MismatchFields"],
                ["PlacementFingerprint"],
            )
            self.assertFalse(SecondRun["Accepted"])

    def testRepeatedRunFingerprintsBackendAndStableArtifactsAreDeterministic(
        self,
    ) -> None:
        Scenarios = (
            (
                "candidate",
                {"CandidateFingerprint": "candidate-changed"},
                False,
                "CandidateFingerprint",
            ),
            (
                "resource-graph",
                {"ResourceGraphFingerprint": "resource-changed"},
                False,
                "ResourceGraphFingerprint",
            ),
            (
                "effective-work",
                {"EffectiveWorkFingerprint": "work-changed"},
                False,
                "EffectiveWorkFingerprint",
            ),
            (
                "truth-table-artifact",
                {},
                True,
                "StableArtifactSha256",
            ),
        )
        for Name, Changes, MutateTruthTable, ExpectedField in Scenarios:
            with self.subTest(Name=Name):
                with tempfile.TemporaryDirectory() as DirectoryValue:
                    Root = Path(DirectoryValue)

                    def Runner(**Options):
                        Command = Options["Command"]
                        RunDirectory = Path(
                            Command[Command.index("--output") + 1]
                        )
                        RunName = Command[
                            Command.index("--outputname") + 1
                        ]
                        Case = next(
                            Value
                            for Value in AcceptanceCases
                            if RunName.startswith(Value.Name)
                        )
                        Artifacts = BuildRunArtifacts(
                            RunDirectory,
                            RunName,
                        )
                        RunChanges = (
                            Changes
                            if RunName == "FullAdderRun2"
                            else {}
                        )
                        WriteSuccessfulArtifacts(
                            Case,
                            Artifacts,
                            PolicyVersion=BaselinePolicyVersion,
                            **RunChanges,
                        )
                        if (
                            MutateTruthTable
                            and RunName == "FullAdderRun2"
                        ):
                            FixtureBytes = (
                                Artifacts["FabricFixture"].read_bytes()
                                + b"\n"
                            )
                            Artifacts["FabricFixture"].write_bytes(FixtureBytes)
                            PhysicalDocument = json.loads(
                                Artifacts["PhysicalDesign"].read_text(
                                    encoding="utf-8"
                                )
                            )
                            PhysicalDocument["RunSummary"]["FabricFixture"]["Sha256"] = sha256(FixtureBytes).hexdigest()
                            Artifacts["PhysicalDesign"].write_text(
                                json.dumps(PhysicalDocument) + "\n",
                                encoding="utf-8",
                            )
                        return AcceptanceCommandResult(
                            0,
                            "",
                            "",
                            1.0,
                        )

                    Manifest = RunAcceptance(
                        self.Configuration(
                            Root,
                            BaselineMode="capture",
                            BaselinePath=Root / "baseline.json",
                        ),
                        CommandRunner=Runner,
                        SourceStateProvider=lambda _Root: {
                            "Revision": "revision",
                            "Dirty": False,
                        },
                        SourceProvenanceProvider=SourceProvenanceFixture,
                        DesignDigestBuilder=DigestFixture,
                        TruthTableEvidenceBuilder=(
                            TruthTableEvidenceFixture
                        ),
                        UtcNowProvider=(
                            lambda: "2026-07-21T12:00:00+00:00"
                        ),
                    )

                    SecondRun = next(
                        Run for Run in Manifest["Runs"]
                        if Run["RunName"] == "FullAdderRun2"
                    )
                    self.assertFalse(SecondRun["Accepted"])
                    self.assertIn(
                        ExpectedField,
                        SecondRun["Determinism"]["MismatchFields"],
                    )

    def testCapturePlansOneWarmupAndExactRegressionMatrix(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            BaselinePath, Manifest = self.CaptureBaseline(Root)

            self.assertEqual(
                [Run["RunName"] for Run in Manifest["Runs"]],
                [
                    "FullAdderWarmup",
                    *(f"FullAdderRun{Index}" for Index in range(1, 6)),
                    *(
                        f"RippleCarryAdder4Run{Index}"
                        for Index in range(1, 4)
                    ),
                    *(
                        f"RippleCarryAdder8Run{Index}"
                        for Index in range(1, 4)
                    ),
                ],
            )
            Warmups = [
                Run for Run in Manifest["Runs"] if Run["Warmup"]
            ]
            self.assertEqual(len(Warmups), 1)
            self.assertFalse(Warmups[0]["MeasurementIncluded"])
            Rca8 = next(
                Case
                for Case in Manifest["Cases"]
                if Case["Name"] == "RippleCarryAdder8"
            )
            self.assertEqual(Rca8["RequiredRuns"], 3)
            self.assertEqual(Rca8["TruthTableRows"], 131_072)
            self.assertEqual(Rca8["RuntimeCeilingSeconds"], 30.0)
            Reference = json.loads(
                BaselinePath.read_text(encoding="utf-8")
            )
            self.assertEqual(
                Reference["SchemaVersion"],
                BaselineSchemaVersion,
            )
            self.assertEqual(
                set(Reference["Cases"]),
                set(RegressionCaseNames),
            )
            self.assertNotIn(
                "CarryLookaheadAdder4",
                Reference["Cases"],
            )

    def testSourceProvenanceHashesContentInputsAndLoadedNativeModule(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            RepoRoot = Path(__file__).resolve().parents[2]
            Configuration = AcceptanceConfiguration(
                RepositoryRoot=RepoRoot,
                OutputRoot=Path(DirectoryValue),
                DateLabel="2026-07-21",
                PythonExecutable=Path(sys.executable),
                RoutingThreads=3,
                ExpectedPolicyVersion=AcceptedPolicyVersion,
            )
            Provenance = BuildSourceProvenance(
                Configuration,
                {"Revision": "revision", "Dirty": True},
            )

            self.assertEqual(
                Provenance["Git"],
                {"Revision": "revision", "Dirty": True},
            )
            self.assertGreater(
                Provenance["SourceContent"]["FileCount"],
                0,
            )
            self.assertEqual(
                len(Provenance["SourceContent"]["AggregateSha256"]),
                64,
            )
            self.assertEqual(
                len(
                    Provenance["BenchmarkInputs"][
                        "RippleCarryAdder8"
                    ]["Sha256"]
                ),
                64,
            )
            self.assertEqual(
                set(Provenance["BenchmarkInputs"]),
                {Case.Name for Case in AcceptanceCases},
            )
            self.assertEqual(
                Provenance["Policy"]["PolicyVersion"],
                AcceptedPolicyVersion,
            )
            self.assertEqual(len(Provenance["Policy"]["Sha256"]), 64)
            Native = Provenance["NativeExtension"]
            self.assertTrue(Native["Loaded"])
            self.assertTrue(Native["Exists"])
            self.assertTrue(Native["WithinRepository"])
            self.assertEqual(
                Native["Module"],
                "RedstoneCompiler.RustRouting",
            )
            self.assertEqual(len(Native["Sha256"]), 64)
            self.assertEqual(
                Native["ProbeSource"],
                "configured-child",
            )
            self.assertEqual(
                Path(Native["ProbePythonExecutable"]).resolve(),
                Configuration.PythonExecutable.resolve(),
            )
            SourcePaths = {
                Record["Path"]
                for Record in Provenance["SourceContent"]["Files"]
            }
            self.assertIn("Assets/Templates/__init__.py", SourcePaths)
            self.assertEqual(
                set(Provenance["PhysicalTemplates"]["Templates"]),
                {"Input", "Nand", "Output"},
            )
            self.assertEqual(
                len(Provenance["PhysicalTemplates"]["AggregateSha256"]),
                64,
            )

    def testCpuProfilePrefersDescriptiveProcModel(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            CpuInfoPath = Path(DirectoryValue) / "cpuinfo"
            CpuInfoPath.write_text(
                "processor : 0\n"
                "vendor_id : AuthenticAMD\n"
                "cpu family : 26\n"
                "model : 68\n"
                "model name : AMD Fixture 16-Core Processor\n"
                "stepping : 0\n\n",
                encoding="utf-8",
            )
            with patch(
                "Tools.Routing.RunRouterAcceptance.platform.processor",
                return_value="x86_64",
            ):
                Profile = ReadCpuProfile(CpuInfoPath)

        self.assertEqual(
            Profile["Model"],
            "AMD Fixture 16-Core Processor",
        )
        self.assertEqual(Profile["VendorIdentifier"], "AuthenticAMD")
        self.assertEqual(Profile["CpuFamily"], "26")
        self.assertEqual(Profile["ModelNumber"], "68")

    def testCgroupQuotaWalksInheritedV2Constraints(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            CgroupRoot = Root / "cgroup"
            Leaf = CgroupRoot / "team" / "job"
            Leaf.mkdir(parents=True)
            (CgroupRoot / "cpu.max").write_text(
                "max 100000\n",
                encoding="utf-8",
            )
            (CgroupRoot / "team" / "cpu.max").write_text(
                "200000 100000\n",
                encoding="utf-8",
            )
            ProcCgroup = Root / "self.cgroup"
            ProcCgroup.write_text(
                "0::/team/job\n",
                encoding="utf-8",
            )

            Profile = ReadCgroupCpuQuotaProfile(
                ProcCgroup,
                CgroupRoot,
            )

        self.assertEqual(Profile["Schema"], "cgroup-v2")
        self.assertTrue(Profile["QuotaLimited"])
        self.assertEqual(Profile["EffectiveQuotaCpuCount"], 2.0)

    def testCompatibilityIncludesSchedulerAndLoadProfiles(self) -> None:
        Environment = {
            "Platform": "fixture-platform",
            "CpuProfile": {
                "Architecture": "x86_64",
                "LogicalCpuCount": 32,
                "Model": "Fixture CPU",
            },
            "CpuExecutionProfile": {
                "Affinity": {
                    "Source": "sched_getaffinity",
                    "CpuCount": 2,
                    "CpuIds": [2, 3],
                },
                "Governors": {
                    "GovernorByCpu": {
                        "2": "performance",
                        "3": "performance",
                    },
                    "Governors": ["performance"],
                },
                "CgroupCpuQuota": {
                    "Schema": "cgroup-v2",
                    "QuotaLimited": True,
                    "EffectiveQuotaCpuCount": 2.0,
                },
            },
            "LoadProfile": {
                "EffectiveCpuCapacity": 2.0,
                "CompatibilityClass": "quiet",
                "LoadAverage1Minute": 0.1,
            },
            "PolicySeed": 0,
            "RoutingThreads": RequiredRegressionRoutingThreads,
            "RoutingEnvironment": {
                "PYTHONHASHSEED": "0",
                "RC_ROUTING_THREADS": "16",
            },
        }
        SourceProvenance = {
            "BenchmarkInputs": {},
            "PhysicalTemplates": {
                "AggregateSha256": "template-pack",
                "Templates": {},
            },
        }
        Reference = BuildComparisonCompatibility(
            Environment=Environment,
            SourceProvenance=SourceProvenance,
        )
        Baseline = {"Compatibility": Reference}
        Mutations = {
            "CpuProfile": lambda Value: Value["CpuProfile"].__setitem__(
                "Model",
                "Different CPU",
            ),
            "CpuExecutionProfile": (
                lambda Value: Value["CpuExecutionProfile"]["Affinity"]
                .__setitem__("CpuIds", [0, 1])
            ),
            "CpuGovernor": (
                lambda Value: Value["CpuExecutionProfile"]["Governors"]
                ["GovernorByCpu"].__setitem__("2", "powersave")
            ),
            "CgroupQuota": (
                lambda Value: Value["CpuExecutionProfile"][
                    "CgroupCpuQuota"
                ].__setitem__("EffectiveQuotaCpuCount", 1.0)
            ),
            "LoadProfile": lambda Value: Value["LoadProfile"].__setitem__(
                "CompatibilityClass",
                "busy",
            ),
            "PhysicalTemplates": (
                lambda Value: Value["PhysicalTemplates"].__setitem__(
                    "AggregateSha256",
                    "different-pack",
                )
            ),
            "MissingCpuExecutionProfile": (
                lambda Value: Value.pop("CpuExecutionProfile")
            ),
            "MissingLoadProfile": (
                lambda Value: Value.pop("LoadProfile")
            ),
            "MissingPhysicalTemplates": (
                lambda Value: Value.pop("PhysicalTemplates")
            ),
        }
        for ExpectedField, Mutate in Mutations.items():
            with self.subTest(ExpectedField=ExpectedField):
                Candidate = deepcopy(Reference)
                Mutate(Candidate)
                Comparison = CompareCompatibility(
                    Baseline,
                    Candidate,
                )
                CanonicalField = (
                    "CpuExecutionProfile"
                    if ExpectedField in {
                        "CpuGovernor",
                        "CgroupQuota",
                        "MissingCpuExecutionProfile",
                    }
                    else {
                        "MissingLoadProfile": "LoadProfile",
                        "MissingPhysicalTemplates": "PhysicalTemplates",
                    }.get(ExpectedField, ExpectedField)
                )
                self.assertFalse(Comparison["Compatible"])
                self.assertIn(
                    CanonicalField,
                    Comparison["MismatchFields"],
                )

    def testResolvedTemplateManifestHashesExternalAbsoluteInputs(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            Repository = Root / "repo"
            TemplatesDirectory = Repository / "Assets/Templates"
            TemplatesDirectory.mkdir(parents=True)
            ExternalDirectory = Root / "external-pack"
            ExternalDirectory.mkdir()
            TemplatePaths = {
                Name: ExternalDirectory / f"{Name}.litematic"
                for Name in ("Input", "Nand", "Output")
            }
            for Index, PathValue in enumerate(TemplatePaths.values()):
                PathValue.write_bytes(f"fixture-{Index}".encode("utf-8"))
            MappingLines = ",\n".join(
                f"    {Name!r}: Path({str(PathValue)!r})"
                for Name, PathValue in TemplatePaths.items()
            )
            (TemplatesDirectory / "__init__.py").write_text(
                "from pathlib import Path\n"
                "LitematicTemplates = {\n"
                f"{MappingLines}\n"
                "}\n",
                encoding="utf-8",
            )

            Manifest = BuildResolvedTemplateInputManifest(
                Repository,
                Root / "missing-python",
            )

        for Name, PathValue in TemplatePaths.items():
            Record = Manifest["Templates"][Name]
            self.assertFalse(Record["WithinRepository"])
            self.assertEqual(Record["Path"], str(PathValue.resolve()))
            self.assertEqual(
                Record["Sha256"],
                sha256(f"fixture-{list(TemplatePaths).index(Name)}".encode(
                    "utf-8"
                )).hexdigest(),
            )

    def testFabricFixtureDigestIsAnExplicitHardGate(self) -> None:
        Case = next(
            Case for Case in AcceptanceCases if Case.Name == "FullAdder"
        )
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            FirstArtifacts = BuildRunArtifacts(
                Root / "first",
                "FullAdderRun1",
            )
            WriteSuccessfulArtifacts(Case, FirstArtifacts)
            Original = FirstArtifacts["TruthTable"].read_text(
                encoding="utf-8"
            ).splitlines()
            SecondPath = Root / "second.TruthTable.txt"
            SecondPath.write_text(
                "fixture prose ignored by the semantic parser\n"
                + "\n".join(reversed(Original))
                + "\n",
                encoding="utf-8",
            )

            First = BuildTruthTableSemanticEvidence(
                FirstArtifacts["TruthTable"]
            )
            Second = BuildTruthTableSemanticEvidence(SecondPath)
            self.assertEqual(
                First["ArithmeticResultSha256"],
                CanonicalArithmeticDigests["FullAdder"],
            )
            self.assertEqual(
                First["ArithmeticResultSha256"],
                Second["ArithmeticResultSha256"],
            )
            self.assertEqual(
                CanonicalArithmeticDigests["CarryLookaheadAdder4"],
                CanonicalArithmeticDigests["RippleCarryAdder4"],
            )

            WrongDigest = "0" * 64
            PhysicalDocument = json.loads(
                FirstArtifacts["PhysicalDesign"].read_text(encoding="utf-8")
            )
            PhysicalDocument["RunSummary"]["FabricFixture"]["Sha256"] = WrongDigest
            FirstArtifacts["PhysicalDesign"].write_text(
                json.dumps(PhysicalDocument) + "\n",
                encoding="utf-8",
            )
            Evaluation, _Evidence = EvaluateRun(
                Case=Case,
                Process=AcceptanceCommandResult(0, "", "", 1.0),
                Artifacts=FirstArtifacts,
                ExpectedSeed=0,
                DesignDigestBuilder=DigestFixture,
            )
            self.assertFalse(Evaluation["Accepted"])
            self.assertTrue(any(
                "Fabric fixture hash does not match" in Failure
                for Failure in Evaluation["Failures"]
            ))

    def testBaselineCaptureContinuesAfterFailureAndDoesNotOverwrite(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            BaselinePath = Root / "baseline.json"
            Calls: list[str] = []
            Configuration = self.Configuration(
                Root / "capture",
                BaselineMode="capture",
                BaselinePath=BaselinePath,
            )
            Manifest = RunAcceptance(
                Configuration,
                CommandRunner=self.SyntheticRunner(
                    PolicyVersion=BaselinePolicyVersion,
                    Calls=Calls,
                    FailureRun="FullAdderRun1",
                ),
                SourceStateProvider=lambda _Root: {
                    "Revision": "revision",
                    "Dirty": True,
                },
                SourceProvenanceProvider=SourceProvenanceFixture,
                DesignDigestBuilder=DigestFixture,
                TruthTableEvidenceBuilder=TruthTableEvidenceFixture,
                UtcNowProvider=lambda: "2026-07-21T12:00:00+00:00",
            )

            self.assertEqual(len(Calls), 12)
            self.assertEqual(Calls[-1], "RippleCarryAdder8Run3")
            self.assertFalse(Manifest["Accepted"])
            self.assertFalse(
                Manifest["BaselineComparison"]["ReferenceWritten"]
            )
            self.assertFalse(BaselinePath.exists())

    def testBaselineLoaderRejectsPromotableAccuracyForgeries(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            BaselinePath, _Manifest = self.CaptureBaseline(Root)
            Original = json.loads(BaselinePath.read_text(encoding="utf-8"))

            def SetCaseValue(
                Baseline: dict[str, object],
                Name: str,
                Value: object,
            ) -> None:
                Baseline["Cases"]["FullAdder"][Name] = Value

            Mutations = (
                (
                    "warmup",
                    lambda Baseline: Baseline.__setitem__(
                        "WarmupsPassed",
                        False,
                    ),
                    "warm-up evidence",
                ),
                (
                    "promotable",
                    lambda Baseline: SetCaseValue(
                        Baseline,
                        "Promotable",
                        False,
                    ),
                    "invalid Promotable",
                ),
                (
                    "accuracy",
                    lambda Baseline: SetCaseValue(
                        Baseline,
                        "AccuracyAndDeterminismPassed",
                        False,
                    ),
                    "invalid AccuracyAndDeterminismPassed",
                ),
                (
                    "determinism",
                    lambda Baseline: SetCaseValue(
                        Baseline,
                        "Deterministic",
                        False,
                    ),
                    "invalid Deterministic",
                ),
                (
                    "run-count",
                    lambda Baseline: SetCaseValue(
                        Baseline,
                        "MeasuredRunCount",
                        4,
                    ),
                    "measured-run count",
                ),
                (
                    "fabric-fixture-digest",
                    lambda Baseline: SetCaseValue(
                        Baseline,
                        "FabricFixtureSha256",
                        "forged",
                    ),
                    "invalid FabricFixtureSha256",
                ),
            )
            for Name, Mutate, Message in Mutations:
                with self.subTest(Name=Name):
                    Candidate = deepcopy(Original)
                    Mutate(Candidate)
                    CandidatePath = Root / f"{Name}.json"
                    CandidatePath.write_text(
                        json.dumps(Candidate) + "\n",
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(ValueError, Message):
                        ReadBaselineReference(CandidatePath)

    def testBaselineLoaderRejectsForgedFootprintCeilings(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            BaselinePath, _Manifest = self.CaptureBaseline(Root)
            Original = json.loads(BaselinePath.read_text(encoding="utf-8"))
            Mutations = (
                (
                    "non-positive",
                    lambda Metrics: Metrics.__setitem__("Width", 0),
                    "invalid footprint metrics",
                ),
                (
                    "xz-identity",
                    lambda Metrics: Metrics.__setitem__(
                        "Footprint",
                        Metrics["Footprint"] + 1,
                    ),
                    r"Footprint is not Width \* Depth",
                ),
                (
                    "volume-identity",
                    lambda Metrics: Metrics.__setitem__(
                        "FullFootprint",
                        Metrics["FullFootprint"] + 1,
                    ),
                    r"FullFootprint is not Width \* Height \* Depth",
                ),
                (
                    "exact-block-volume",
                    lambda Metrics: Metrics.__setitem__(
                        "ExactNonAirBlocks",
                        Metrics["FullFootprint"] + 1,
                    ),
                    "exact block count exceeds",
                ),
            )
            for Name, Mutate, Message in Mutations:
                with self.subTest(Name=Name):
                    Candidate = deepcopy(Original)
                    Mutate(
                        Candidate["Cases"]["FullAdder"][
                            "FootprintMetrics"
                        ]
                    )
                    CandidatePath = Root / f"{Name}.json"
                    CandidatePath.write_text(
                        json.dumps(Candidate) + "\n",
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(ValueError, Message):
                        ReadBaselineReference(CandidatePath)

    def testBaselineLoaderRequiresVerifiableRuntimeEvidence(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            BaselinePath, _Manifest = self.CaptureBaseline(Root)
            Original = json.loads(BaselinePath.read_text(encoding="utf-8"))
            Mutations = (
                (
                    "rounded-only",
                    lambda Runtime: Runtime.pop("SamplesSecondsRaw"),
                    "rounded legacy references require declared precision",
                ),
                (
                    "summary-forgery",
                    lambda Runtime: Runtime["SamplesSecondsRaw"].__setitem__(
                        0,
                        Runtime["SamplesSecondsRaw"][0] + 0.01,
                    ),
                    "does not match raw samples",
                ),
                (
                    "non-finite",
                    lambda Runtime: Runtime["SamplesSecondsRaw"].__setitem__(
                        0,
                        float("nan"),
                    ),
                    "invalid raw runtime samples",
                ),
            )
            for Name, Mutate, Message in Mutations:
                with self.subTest(Name=Name):
                    Candidate = deepcopy(Original)
                    Mutate(Candidate["Cases"]["FullAdder"]["Runtime"])
                    CandidatePath = Root / f"{Name}.json"
                    CandidatePath.write_text(
                        json.dumps(Candidate) + "\n",
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(ValueError, Message):
                        ReadBaselineReference(CandidatePath)

            FrozenPath = (
                Path(__file__).resolve().parent.parent
                / "Fixtures"
                / "RouterRegressionBaseline.json"
            )
            Recorded = json.loads(FrozenPath.read_text(encoding="utf-8"))
            Recorded["Cases"]["FullAdder"]["Runtime"][
                "RecordedPrecisionSeconds"
            ] = 1.0
            Recorded["RecordedRuntimeEvidence"][
                "WallRuntimeRecordedPrecisionSeconds"
            ] = 1.0
            RecordedPath = Root / "recorded-precision-forgery.json"
            RecordedPath.write_text(
                json.dumps(Recorded) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ValueError,
                "declared recorded precision",
            ):
                ReadBaselineReference(RecordedPath)

    def testBaselineLoaderVerifiesLocalRawManifestButRemainsPortable(
        self,
    ) -> None:
        FixturePath = (
            Path(__file__).resolve().parent.parent
            / "Fixtures"
            / "RouterRegressionBaseline.json"
        )
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            Baseline = json.loads(
                FixturePath.read_text(encoding="utf-8")
            )
            RawManifestPath = Root / "AcceptanceManifest.json"
            RawManifestPath.write_text(
                '{"SchemaVersion":"fixture-raw"}\n',
                encoding="utf-8",
            )
            RawDigest = sha256(RawManifestPath.read_bytes()).hexdigest()
            Baseline["RecordedRuntimeEvidence"].update({
                "SourceManifestPath": str(RawManifestPath),
                "SourceManifestSha256": RawDigest,
            })
            CandidatePath = Root / "baseline.json"
            CandidatePath.write_text(
                json.dumps(Baseline) + "\n",
                encoding="utf-8",
            )

            self.assertEqual(
                ReadBaselineReference(CandidatePath)["Promotable"],
                True,
            )
            Forged = deepcopy(Baseline)
            Forged["RecordedRuntimeEvidence"][
                "SourceManifestSha256"
            ] = "0" * 64
            CandidatePath.write_text(
                json.dumps(Forged) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ValueError,
                "source manifest hash mismatch",
            ):
                ReadBaselineReference(CandidatePath)

            RawManifestPath.unlink()
            CandidatePath.write_text(
                json.dumps(Baseline) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(
                ReadBaselineReference(CandidatePath)["Promotable"],
                True,
            )

    def testBaselineLoaderRejectsCompatibilityProvenanceForgery(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            BaselinePath, _Manifest = self.CaptureBaseline(Root)
            Original = json.loads(BaselinePath.read_text(encoding="utf-8"))
            Mutations = (
                (
                    "environment",
                    lambda Baseline: Baseline["Environment"].__setitem__(
                        "PythonVersion",
                        "forged",
                    ),
                ),
                (
                    "benchmark-input",
                    lambda Baseline: Baseline["SourceProvenance"][
                        "BenchmarkInputs"
                    ]["FullAdder"].__setitem__("Sha256", "forged"),
                ),
            )
            for Name, Mutate in Mutations:
                with self.subTest(Name=Name):
                    Candidate = deepcopy(Original)
                    Mutate(Candidate)
                    CandidatePath = Root / f"{Name}.json"
                    CandidatePath.write_text(
                        json.dumps(Candidate) + "\n",
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(
                        ValueError,
                        "Compatibility does not match",
                    ):
                        ReadBaselineReference(CandidatePath)

    def testComparisonRejectsRemovedSimulationBackend(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            BaselinePath, _Manifest = self.CaptureBaseline(Root)
            PhysicalChanges = {
                (
                    f"{Case.Name}Warmup"
                    if RunIndex == 0
                    else f"{Case.Name}Run{RunIndex}"
                ): {"FabricValidationBackend": "python"}
                for Case in AcceptanceCases
                if Case.Name in RegressionCaseNames
                for RunIndex in range(
                    0 if Case.Name != "FullAdder" else 0,
                    Case.RequiredRuns + 1,
                )
                if RunIndex != 0 or Case.Name == "FullAdder"
            }
            Manifest = RunAcceptance(
                self.Configuration(
                    Root / "candidate",
                    BaselineMode="compare",
                    BaselinePath=BaselinePath,
                ),
                CommandRunner=self.SyntheticRunner(
                    PolicyVersion=CandidatePolicyVersion,
                    PhysicalChangesByRun=PhysicalChanges,
                ),
                SourceStateProvider=lambda _Root: {
                    "Revision": "candidate-revision",
                    "Dirty": True,
                },
                SourceProvenanceProvider=SourceProvenanceFixture,
                DesignDigestBuilder=DigestFixture,
                TruthTableEvidenceBuilder=TruthTableEvidenceFixture,
                UtcNowProvider=(
                    lambda: "2026-07-21T12:00:00+00:00"
                ),
            )

            self.assertTrue(
                Manifest["BaselineComparison"]["Compatibility"][
                    "Compatible"
                ]
            )
            self.assertFalse(Manifest["Accepted"])
            for CaseName in RegressionCaseNames:
                Circuit = Manifest["BaselineComparison"]["Circuits"][
                    CaseName
                ]
                self.assertFalse(
                    Circuit["SimulationBackendCompatible"]
                )
                self.assertEqual(
                    Circuit["SimulationBackend"],
                    {
                        "Baseline": "fabric-26.2",
                        "Candidate": None,
                    },
                )

    def testMalformedPromotableBaselineFailsBeforeLaunching(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            BaselinePath, _Manifest = self.CaptureBaseline(Root)
            Baseline = json.loads(
                BaselinePath.read_text(encoding="utf-8")
            )
            Baseline["Cases"]["FullAdder"][
                "AccuracyAndDeterminismPassed"
            ] = False
            BaselinePath.write_text(
                json.dumps(Baseline) + "\n",
                encoding="utf-8",
            )
            Calls: list[str] = []
            Manifest = RunAcceptance(
                self.Configuration(
                    Root / "candidate",
                    BaselineMode="compare",
                    BaselinePath=BaselinePath,
                ),
                CommandRunner=self.SyntheticRunner(
                    PolicyVersion=CandidatePolicyVersion,
                    Calls=Calls,
                ),
                SourceStateProvider=lambda _Root: {
                    "Revision": "candidate-revision",
                    "Dirty": True,
                },
                SourceProvenanceProvider=SourceProvenanceFixture,
                UtcNowProvider=lambda: "2026-07-21T12:00:00+00:00",
            )

            self.assertEqual(Calls, [])
            self.assertFalse(Manifest["Accepted"])
            self.assertIn(
                "invalid AccuracyAndDeterminismPassed",
                Manifest["BaselineComparison"]["Failure"],
            )

    def testSuccessfulCaptureRefusesToOverwriteExistingReference(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            BaselinePath = Root / "baseline.json"
            BaselinePath.write_text("preserve-me\n", encoding="utf-8")
            Calls: list[str] = []
            Configuration = self.Configuration(
                Root / "capture",
                BaselineMode="capture",
                BaselinePath=BaselinePath,
            )
            Manifest = RunAcceptance(
                Configuration,
                CommandRunner=self.SyntheticRunner(
                    PolicyVersion=BaselinePolicyVersion,
                    Calls=Calls,
                ),
                SourceStateProvider=lambda _Root: {
                    "Revision": "candidate-revision",
                    "Dirty": True,
                },
                SourceProvenanceProvider=SourceProvenanceFixture,
                DesignDigestBuilder=DigestFixture,
                TruthTableEvidenceBuilder=TruthTableEvidenceFixture,
                UtcNowProvider=lambda: "2026-07-21T12:00:00+00:00",
            )

            Comparison = Manifest["BaselineComparison"]
            self.assertFalse(Comparison["Promotable"])
            self.assertTrue(Comparison["ReferenceAlreadyExists"])
            self.assertTrue(Comparison["OverwriteBlocked"])
            self.assertFalse(Comparison["ReferenceWritten"])
            self.assertFalse(Manifest["Accepted"])
            self.assertEqual(Calls, [])
            self.assertFalse(Configuration.ManifestPath.exists())
            self.assertEqual(
                BaselinePath.read_text(encoding="utf-8"),
                "preserve-me\n",
            )

    def testCaptureRefusesToOverwriteRawEvidenceViaDistinctReference(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            FirstBaselinePath, FirstManifest = self.CaptureBaseline(Root)
            FirstManifestPath = Path(FirstManifest["ManifestPath"])
            FirstManifestBytes = FirstManifestPath.read_bytes()
            FirstRunArtifact = next(
                Path(Run["Evaluation"]["Artifacts"]["Schematic"]["Path"])
                for Run in FirstManifest["Runs"]
                if Run.get("MeasurementIncluded") is True
            )
            FirstRunBytes = FirstRunArtifact.read_bytes()
            SecondBaselinePath = Root / "DifferentBaselineName.json"
            Calls: list[str] = []
            Configuration = self.Configuration(
                Root / "capture",
                BaselineMode="capture",
                BaselinePath=SecondBaselinePath,
            )

            SecondManifest = RunAcceptance(
                Configuration,
                CommandRunner=self.SyntheticRunner(
                    PolicyVersion=BaselinePolicyVersion,
                    Calls=Calls,
                ),
                SourceStateProvider=lambda _Root: {
                    "Revision": "baseline-revision",
                    "Dirty": True,
                },
                SourceProvenanceProvider=SourceProvenanceFixture,
                UtcNowProvider=lambda: "2026-07-21T12:00:00+00:00",
            )

            self.assertTrue(FirstBaselinePath.is_file())
            self.assertFalse(SecondBaselinePath.exists())
            self.assertEqual(Calls, [])
            self.assertFalse(SecondManifest["Accepted"])
            Comparison = SecondManifest["BaselineComparison"]
            self.assertFalse(Comparison["ReferenceAlreadyExists"])
            self.assertTrue(Comparison["RawEvidenceAlreadyExists"])
            self.assertTrue(Comparison["OverwriteBlocked"])
            self.assertEqual(FirstManifestPath.read_bytes(), FirstManifestBytes)
            self.assertEqual(FirstRunArtifact.read_bytes(), FirstRunBytes)

    def testComparisonRefusesToOverwriteOccupiedRawEvidence(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            BaselinePath, _Capture = self.CaptureBaseline(Root)
            OutputRoot = Root / "candidate"
            FirstCalls: list[str] = []
            Configuration = self.Configuration(
                OutputRoot,
                BaselineMode="compare",
                BaselinePath=BaselinePath,
            )
            FirstManifest = RunAcceptance(
                Configuration,
                CommandRunner=self.SyntheticRunner(
                    PolicyVersion=CandidatePolicyVersion,
                    Calls=FirstCalls,
                ),
                SourceStateProvider=lambda _Root: {
                    "Revision": "candidate-revision",
                    "Dirty": True,
                },
                SourceProvenanceProvider=SourceProvenanceFixture,
                DesignDigestBuilder=DigestFixture,
                TruthTableEvidenceBuilder=TruthTableEvidenceFixture,
                UtcNowProvider=lambda: "2026-07-21T12:00:00+00:00",
            )
            self.assertTrue(FirstManifest["Accepted"])
            self.assertEqual(len(FirstCalls), 12)
            ManifestBytes = Configuration.ManifestPath.read_bytes()
            FirstRunArtifact = next(
                Path(Run["Evaluation"]["Artifacts"]["Schematic"]["Path"])
                for Run in FirstManifest["Runs"]
                if Run.get("MeasurementIncluded") is True
            )
            ArtifactBytes = FirstRunArtifact.read_bytes()

            SecondCalls: list[str] = []
            SecondManifest = RunAcceptance(
                Configuration,
                CommandRunner=self.SyntheticRunner(
                    PolicyVersion=CandidatePolicyVersion,
                    Calls=SecondCalls,
                ),
                SourceStateProvider=lambda _Root: {
                    "Revision": "candidate-revision",
                    "Dirty": True,
                },
                SourceProvenanceProvider=SourceProvenanceFixture,
                UtcNowProvider=lambda: "2026-07-21T12:00:00+00:00",
            )

            self.assertEqual(SecondCalls, [])
            self.assertFalse(SecondManifest["Accepted"])
            Comparison = SecondManifest["BaselineComparison"]
            self.assertTrue(Comparison["RawEvidenceAlreadyExists"])
            self.assertTrue(Comparison["OverwriteBlocked"])
            self.assertIn("comparison refused", Comparison["Failure"])
            self.assertTrue(all(
                Run["Status"] == "SKIPPED"
                for Run in SecondManifest["Runs"]
            ))
            self.assertEqual(
                Configuration.ManifestPath.read_bytes(),
                ManifestBytes,
            )
            self.assertEqual(FirstRunArtifact.read_bytes(), ArtifactBytes)

    def testComparisonRejectsEveryFootprintMetricGrowthAndSkipsCompatibilityCircuits(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            BaselinePath, _Capture = self.CaptureBaseline(Root)
            Scenarios = {
                "Footprint": {
                    "Width": 201,
                    "Height": 3,
                    "Depth": 1,
                    "Footprint": 201,
                    "FullFootprint": 603,
                },
                "FullFootprint": {
                    "Height": 5,
                    "FullFootprint": 1000,
                },
                "ExactNonAirBlocks": {
                    "ExactNonAirBlocks": 301,
                },
            }
            for MetricName, PhysicalChanges in Scenarios.items():
                with self.subTest(MetricName=MetricName):
                    Calls: list[str] = []
                    Changes = {
                        f"FullAdderRun{Index}": PhysicalChanges
                        for Index in range(1, 6)
                    }
                    Configuration = self.Configuration(
                        Root / f"compare-{MetricName}",
                        BaselineMode="compare",
                        BaselinePath=BaselinePath,
                    )
                    Manifest = RunAcceptance(
                        Configuration,
                        CommandRunner=self.SyntheticRunner(
                            PolicyVersion=CandidatePolicyVersion,
                            Calls=Calls,
                            PhysicalChangesByRun=Changes,
                        ),
                        SourceStateProvider=lambda _Root: {
                            "Revision": "candidate-revision",
                            "Dirty": True,
                        },
                        SourceProvenanceProvider=SourceProvenanceFixture,
                        DesignDigestBuilder=DigestFixture,
                        TruthTableEvidenceBuilder=TruthTableEvidenceFixture,
                        UtcNowProvider=lambda: (
                            "2026-07-21T12:00:00+00:00"
                        ),
                    )

                    self.assertEqual(len(Calls), 12)
                    self.assertEqual(Calls[-1], "RippleCarryAdder8Run3")
                    self.assertFalse(Manifest["Accepted"])
                    Comparison = Manifest["BaselineComparison"]
                    self.assertFalse(
                        Comparison["Circuits"]["FullAdder"][
                            "FootprintPassed"
                        ]
                    )
                    Metrics = Comparison["Circuits"]["FullAdder"][
                        "Footprint"
                    ][0]["Metrics"]
                    self.assertEqual(Metrics[MetricName]["Delta"], 1 if (
                        MetricName != "FullFootprint"
                    ) else 200)
                    self.assertFalse(Metrics[MetricName]["Passed"])
                    self.assertTrue(all(
                        Value["Passed"]
                        for Name, Value in Metrics.items()
                        if Name != MetricName
                    ))
                    CompatibilityRuns = [
                        Run
                        for Run in Manifest["Runs"]
                        if Run["Circuit"] in ExtendedCaseNames
                    ]
                    self.assertTrue(all(
                        Run["Status"] == "SKIPPED"
                        for Run in CompatibilityRuns
                    ))

    def testSpeedGateAcceptsFivePercentAndRejectsAboveBoundary(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            BaselinePath, _Capture = self.CaptureBaseline(Root)
            RegressionNames = [
                f"{Case.Name}Run{Index}"
                for Case in AcceptanceCases
                if Case.Name in RegressionCaseNames
                for Index in range(1, Case.RequiredRuns + 1)
            ]
            BoundaryRuntimes = {
                Name: 1.0 + MaximumRuntimeRegressionFraction
                for Name in RegressionNames
            }
            BoundaryConfiguration = self.Configuration(
                Root / "boundary",
                BaselineMode="compare",
                BaselinePath=BaselinePath,
                ExpectedPolicyVersion=CandidatePolicyVersion,
                IncludeCla4=True,
            )
            BoundaryManifest = RunAcceptance(
                BoundaryConfiguration,
                CommandRunner=self.SyntheticRunner(
                    PolicyVersion=CandidatePolicyVersion,
                    RuntimeByRun=BoundaryRuntimes,
                ),
                SourceStateProvider=lambda _Root: {
                    "Revision": "candidate-revision",
                    "Dirty": True,
                },
                SourceProvenanceProvider=lambda Configuration, State: (
                    SourceProvenanceFixture(
                        Configuration,
                        State,
                        SourceDigest="changed-source",
                        NativeDigest="changed-native",
                    )
                ),
                DesignDigestBuilder=DigestFixture,
                TruthTableEvidenceBuilder=TruthTableEvidenceFixture,
                UtcNowProvider=lambda: "2026-07-21T12:00:00+00:00",
            )
            self.assertTrue(BoundaryManifest["Accepted"])
            BoundaryComparison = BoundaryManifest[
                "BaselineComparison"
            ]
            self.assertTrue(BoundaryComparison["MeasurementValid"])
            self.assertFalse(BoundaryComparison["QuietRerunRequired"])
            self.assertEqual(
                BoundaryComparison["Circuits"]["FullAdder"][
                    "Runtime"
                ]["DeltaFraction"],
                MaximumRuntimeRegressionFraction,
            )
            self.assertTrue(
                BoundaryComparison["SourceDifferencesAllowed"][
                    "SourceContentChanged"
                ]
            )
            self.assertTrue(
                BoundaryComparison["SourceDifferencesAllowed"][
                    "NativeExtensionChanged"
                ]
            )
            self.assertTrue(
                BoundaryComparison["SourceDifferencesAllowed"][
                    "PolicyVersionChanged"
                ]
            )
            ClaBaseline = BoundaryComparison["FirstValidBaselines"][
                "CarryLookaheadAdder4"
            ]
            self.assertTrue(ClaBaseline["CaseSummary"]["Promotable"])
            self.assertEqual(
                ClaBaseline["CaseSummary"]["MeasuredRunCount"],
                2,
            )
            self.assertEqual(
                BoundaryComparison["UnbaselinedCircuits"],
                [],
            )
            FullAdderComparison = BoundaryComparison["Circuits"]["FullAdder"]
            self.assertEqual(
                FullAdderComparison["Dimensions"]["Width"],
                {
                    "Baseline": 10,
                    "Candidate": 10,
                    "Delta": 0,
                },
            )
            self.assertEqual(
                FullAdderComparison["RouteMetrics"]["Length"],
                {
                    "Baseline": 120,
                    "Candidate": 120,
                    "Delta": 0,
                },
            )

            AboveRuntimes = {
                Name: 1.0 + MaximumRuntimeRegressionFraction + 0.000001
                for Name in RegressionNames
            }
            AboveConfiguration = self.Configuration(
                Root / "above",
                BaselineMode="compare",
                BaselinePath=BaselinePath,
                ExpectedPolicyVersion=CandidatePolicyVersion,
                IncludeCla4=True,
            )
            AboveManifest = RunAcceptance(
                AboveConfiguration,
                CommandRunner=self.SyntheticRunner(
                    PolicyVersion=CandidatePolicyVersion,
                    RuntimeByRun=AboveRuntimes,
                ),
                SourceStateProvider=lambda _Root: {
                    "Revision": "candidate-revision",
                    "Dirty": True,
                },
                SourceProvenanceProvider=SourceProvenanceFixture,
                DesignDigestBuilder=DigestFixture,
                TruthTableEvidenceBuilder=TruthTableEvidenceFixture,
                UtcNowProvider=lambda: "2026-07-21T12:00:00+00:00",
            )
            self.assertFalse(AboveManifest["Accepted"])
            self.assertFalse(
                AboveManifest["BaselineComparison"]["Circuits"][
                    "FullAdder"
                ]["SpeedPassed"]
            )

    def testRawWallRuntimeCannotRoundDownIntoFivePercentPass(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            BaselinePath, _Capture = self.CaptureBaseline(Root)
            RegressionNames = [
                f"{Case.Name}Run{Index}"
                for Case in AcceptanceCases
                if Case.Name in RegressionCaseNames
                for Index in range(1, Case.RequiredRuns + 1)
            ]
            RawAboveBoundary = nextafter(
                1.0 + MaximumRuntimeRegressionFraction,
                float("inf"),
            )
            Manifest = RunAcceptance(
                self.Configuration(
                    Root / "raw-boundary",
                    BaselineMode="compare",
                    BaselinePath=BaselinePath,
                ),
                CommandRunner=self.SyntheticRunner(
                    PolicyVersion=CandidatePolicyVersion,
                    RuntimeByRun={
                        Name: RawAboveBoundary
                        for Name in RegressionNames
                    },
                ),
                SourceStateProvider=lambda _Root: {
                    "Revision": "candidate-revision",
                    "Dirty": True,
                },
                SourceProvenanceProvider=SourceProvenanceFixture,
                DesignDigestBuilder=DigestFixture,
                TruthTableEvidenceBuilder=TruthTableEvidenceFixture,
                UtcNowProvider=lambda: "2026-07-21T12:00:00+00:00",
            )

            Runtime = Manifest["BaselineComparison"]["Circuits"][
                "FullAdder"
            ]["Runtime"]
            self.assertFalse(Manifest["Accepted"])
            self.assertEqual(Runtime["CandidateMedianSeconds"], 1.05)
            self.assertGreater(
                Runtime["CandidateMedianSecondsRaw"],
                1.05,
            )
            self.assertGreater(
                Runtime["DeltaFractionRaw"],
                MaximumRuntimeRegressionFraction,
            )

    def testRuntimeSpreadBoundaryIsStrictlyAboveFivePercent(self) -> None:
        Boundary = CalculateRuntimeStatistics([0.95, 1.0, 1.05])
        Above = CalculateRuntimeStatistics([
            nextafter(0.95, 0.0),
            1.0,
            1.05,
        ])

        self.assertTrue(Boundary["Stable"])
        self.assertEqual(
            Boundary["MaximumSpreadFraction"],
            MaximumRuntimeSpreadFraction,
        )
        self.assertFalse(Above["Stable"])
        self.assertEqual(
            Above["MaximumSpreadFraction"],
            MaximumRuntimeSpreadFraction,
        )
        self.assertGreater(
            Above["MaximumSpreadFractionRaw"],
            MaximumRuntimeSpreadFraction,
        )

    def testUnstableRuntimeExplicitlyRequiresQuietRerun(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            BaselinePath, _Capture = self.CaptureBaseline(Root)
            Manifest = RunAcceptance(
                self.Configuration(
                    Root / "unstable",
                    BaselineMode="compare",
                    BaselinePath=BaselinePath,
                ),
                CommandRunner=self.SyntheticRunner(
                    PolicyVersion=CandidatePolicyVersion,
                    RuntimeByRun={
                        "FullAdderRun1": 0.90,
                        "FullAdderRun2": 1.00,
                        "FullAdderRun3": 1.10,
                        "FullAdderRun4": 1.00,
                        "FullAdderRun5": 1.00,
                    },
                ),
                SourceStateProvider=lambda _Root: {
                    "Revision": "candidate-revision",
                    "Dirty": True,
                },
                SourceProvenanceProvider=SourceProvenanceFixture,
                DesignDigestBuilder=DigestFixture,
                TruthTableEvidenceBuilder=TruthTableEvidenceFixture,
                UtcNowProvider=lambda: "2026-07-21T12:00:00+00:00",
            )

            Comparison = Manifest["BaselineComparison"]
            FullAdder = Comparison["Circuits"]["FullAdder"]
            self.assertFalse(Comparison["MeasurementValid"])
            self.assertTrue(Comparison["QuietRerunRequired"])
            self.assertFalse(FullAdder["MeasurementValid"])
            self.assertTrue(FullAdder["QuietRerunRequired"])
            self.assertTrue(any(
                "quiet rerun required" in Failure
                for Run in Manifest["Runs"]
                if Run["Circuit"] == "FullAdder"
                for Failure in Run["Evaluation"]["Failures"]
            ))

    def testIncompatibleEnvironmentFailsBeforeLaunching(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            BaselinePath, _Capture = self.CaptureBaseline(Root)
            Baseline = json.loads(
                BaselinePath.read_text(encoding="utf-8")
            )
            DifferentCpuProfile = {
                "Architecture": "different",
                "LogicalCpuCount": 1,
                "Model": "different",
            }
            Baseline["Environment"]["CpuProfile"] = DifferentCpuProfile
            Baseline["Compatibility"]["CpuProfile"] = DifferentCpuProfile
            BaselinePath.write_text(
                json.dumps(Baseline) + "\n",
                encoding="utf-8",
            )
            Calls: list[str] = []
            Configuration = self.Configuration(
                Root / "compare",
                BaselineMode="compare",
                BaselinePath=BaselinePath,
                ExpectedPolicyVersion=CandidatePolicyVersion,
            )
            Manifest = RunAcceptance(
                Configuration,
                CommandRunner=self.SyntheticRunner(
                    PolicyVersion=CandidatePolicyVersion,
                    Calls=Calls,
                ),
                SourceStateProvider=lambda _Root: {
                    "Revision": "candidate-revision",
                    "Dirty": True,
                },
                SourceProvenanceProvider=SourceProvenanceFixture,
                UtcNowProvider=lambda: "2026-07-21T12:00:00+00:00",
            )

            self.assertEqual(Calls, [])
            self.assertFalse(Manifest["Accepted"])
            self.assertIn(
                "CpuProfile",
                Manifest["BaselineComparison"]["Compatibility"][
                    "MismatchFields"
                ],
            )
            self.assertTrue(all(
                Run["Status"] == "SKIPPED"
                for Run in Manifest["Runs"]
            ))

    def testBaselineCliModesAreMutuallyExclusive(self) -> None:
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit):
                BuildParser().parse_args([
                    "--capture-baseline",
                    "capture.json",
                    "--compare-baseline",
                    "compare.json",
                ])
        with self.assertRaisesRegex(
            ValueError,
            "exactly 16 routing threads",
        ):
            AcceptanceConfiguration(
                RepositoryRoot=Path("/repo"),
                OutputRoot=Path("/output"),
                DateLabel="2026-07-25",
                PythonExecutable=Path("/python"),
                RoutingThreads=8,
                BaselineMode="capture",
                BaselinePath=Path("/baseline.json"),
            )
        with self.assertRaisesRegex(
            ValueError,
            "--include-cla4 cannot be combined with baseline capture",
        ):
            AcceptanceConfiguration(
                RepositoryRoot=Path("/repo"),
                OutputRoot=Path("/output"),
                DateLabel="2026-07-25",
                PythonExecutable=Path("/python"),
                RoutingThreads=RequiredRegressionRoutingThreads,
                BaselineMode="capture",
                BaselinePath=Path("/baseline.json"),
                IncludeCla4=True,
            )

    def testDefaultPythonExecutablePreservesVenvLauncherPath(self) -> None:
        with tempfile.TemporaryDirectory() as Directory:
            Root = Path(Directory)
            Launcher = Root / ".venv" / "bin" / "python"
            Launcher.parent.mkdir(parents=True)
            Launcher.symlink_to(Path(sys.executable))

            self.assertEqual(
                DefaultPythonExecutable(Root),
                Launcher.absolute(),
            )

    def testCaptureAndComparisonPreserveSeparateRawEvidence(self) -> None:
        BaselinePath = Path("/baseline.json")
        Standalone = AcceptanceConfiguration(
            RepositoryRoot=Path("/repo"),
            OutputRoot=Path("/output/Acceptance"),
            DateLabel="2026-07-25",
            PythonExecutable=Path("/python"),
        )
        Capture = AcceptanceConfiguration(
            RepositoryRoot=Path("/repo"),
            OutputRoot=Path("/output"),
            DateLabel="2026-07-25",
            PythonExecutable=Path("/python"),
            RoutingThreads=RequiredRegressionRoutingThreads,
            BaselineMode="capture",
            BaselinePath=BaselinePath,
        )
        Comparison = AcceptanceConfiguration(
            RepositoryRoot=Path("/repo"),
            OutputRoot=Path("/output"),
            DateLabel="2026-07-25",
            PythonExecutable=Path("/python"),
            RoutingThreads=RequiredRegressionRoutingThreads,
            BaselineMode="compare",
            BaselinePath=BaselinePath,
        )

        self.assertNotEqual(Capture.RecoveryRoot, Comparison.RecoveryRoot)
        self.assertEqual(
            Standalone.RecoveryRoot,
            Path("/output/Acceptance/2026-07-25"),
        )
        self.assertEqual(
            Standalone.ManifestPath,
            Path("/output/Acceptance/2026-07-25/AcceptanceManifest.json"),
        )
        self.assertEqual(Capture.RecoveryRoot.name, "BaselineCapture")
        self.assertEqual(Comparison.RecoveryRoot.name, "CandidateComparison")
        self.assertEqual(
            Capture.RecoveryRoot.parent,
            Path("/output/2026-07-25"),
        )
        self.assertEqual(
            Capture.ExpectedPolicyVersion,
            BaselinePolicyVersion,
        )
        self.assertEqual(
            Comparison.ExpectedPolicyVersion,
            CurrentPolicyVersion,
        )
        with self.assertRaisesRegex(
            ValueError,
            "capture mode requires policy version",
        ):
            AcceptanceConfiguration(
                RepositoryRoot=Path("/repo"),
                OutputRoot=Path("/output"),
                DateLabel="2026-07-25",
                PythonExecutable=Path("/python"),
                RoutingThreads=RequiredRegressionRoutingThreads,
                BaselineMode="capture",
                BaselinePath=BaselinePath,
                ExpectedPolicyVersion=CurrentPolicyVersion,
            )
        with self.assertRaisesRegex(
            ValueError,
            "compare mode requires policy version",
        ):
            AcceptanceConfiguration(
                RepositoryRoot=Path("/repo"),
                OutputRoot=Path("/output"),
                DateLabel="2026-07-25",
                PythonExecutable=Path("/python"),
                RoutingThreads=RequiredRegressionRoutingThreads,
                BaselineMode="compare",
                BaselinePath=BaselinePath,
                ExpectedPolicyVersion=BaselinePolicyVersion,
            )

    def testPreservedV15FixtureAcceptsV16CandidateCompatibility(
        self,
    ) -> None:
        FixturePath = (
            Path(__file__).resolve().parent.parent
            / "Fixtures"
            / "RouterRegressionBaseline.json"
        )
        FixtureDigest = sha256(FixturePath.read_bytes()).hexdigest()
        Baseline = ReadBaselineReference(FixturePath)
        FirstValidBaselines = Baseline.get("FirstValidBaselines", {})
        if FirstValidBaselines:
            self.assertEqual(
                FirstValidBaselines["CarryLookaheadAdder4"][
                    "OriginalReferenceSha256"
                ],
                FrozenRouterRegressionBaselineSha256,
            )
        else:
            self.assertEqual(
                FixtureDigest,
                FrozenRouterRegressionBaselineSha256,
            )
        CandidateProvenance = deepcopy(Baseline["SourceProvenance"])
        CandidateProvenance["ExpectedPolicyVersion"] = CurrentPolicyVersion
        CandidateProvenance["SourceContent"]["AggregateSha256"] = (
            "candidate-source"
        )
        CandidateProvenance["NativeExtension"]["Sha256"] = (
            "candidate-native"
        )
        CandidateProvenance["Policy"]["PolicyVersion"] = (
            CurrentPolicyVersion
        )
        CandidateProvenance["Policy"]["Sha256"] = "candidate-policy"
        CandidateProvenance["PhysicalTemplates"] = {
            "SchemaVersion": "resolved-template-inputs-v1",
            "AggregateSha256": "candidate-template-pack",
            "Templates": {},
        }
        CandidateEnvironment = deepcopy(Baseline["Environment"])
        CandidateEnvironment["CpuProfile"] = {
            "Architecture": "x86_64",
            "LogicalCpuCount": 32,
            "Model": "AMD Ryzen test processor",
            "CpuFamily": "26",
        }
        CandidateEnvironment["CpuExecutionProfile"] = {
            "Affinity": {
                "Source": "sched_getaffinity",
                "CpuCount": 32,
                "CpuIds": list(range(32)),
            },
            "Governors": {
                "GovernorByCpu": {
                    str(CpuId): "performance"
                    for CpuId in range(32)
                },
                "Governors": ["performance"],
            },
            "CgroupCpuQuota": {
                "Schema": "cgroup-v2",
                "QuotaLimited": False,
                "EffectiveQuotaCpuCount": None,
            },
        }
        CandidateEnvironment["LoadProfile"] = {
            "EffectiveCpuCapacity": 32.0,
            "CompatibilityClass": "quiet",
            "LoadAverage1Minute": 0.1,
        }
        CandidateCompatibility = BuildComparisonCompatibility(
            Environment=CandidateEnvironment,
            SourceProvenance=CandidateProvenance,
        )
        Compatibility = CompareCompatibility(
            Baseline,
            CandidateCompatibility,
        )

        self.assertEqual(
            Baseline["SourceProvenance"]["ExpectedPolicyVersion"],
            BaselinePolicyVersion,
        )
        self.assertEqual(
            Baseline["SourceProvenance"]["Policy"]["PolicyVersion"],
            BaselinePolicyVersion,
        )
        self.assertEqual(
            set(Baseline["Cases"]),
            set(RegressionCaseNames),
        )
        self.assertNotIn("CarryLookaheadAdder4", Baseline["Cases"])
        self.assertEqual(
            Baseline["RecordedRuntimeEvidence"],
            {
                "SourceManifestPath": (
                    "Output/Regression/2026-07-25/RouterRegression/"
                    "BaselineCapture/AcceptanceManifest.json"
                ),
                "SourceManifestSha256": (
                    "7719e40ff7836672ce7ef084395bb4b361520d27867bd0366"
                    "d8e0771c6549fad"
                ),
                "WallRuntimeRecordedPrecisionSeconds": 0.000001,
            },
        )
        FullAdderRuntime = Baseline["Cases"]["FullAdder"]["Runtime"]
        self.assertNotIn("SamplesSecondsRaw", FullAdderRuntime)
        self.assertLess(
            FullAdderRuntime["ReferenceMedianLowerBoundSeconds"],
            FullAdderRuntime["MedianSeconds"],
        )
        self.assertTrue(FullAdderRuntime["StableConservative"])
        self.assertEqual(
            set(Baseline["Compatibility"]["BenchmarkInputs"]),
            set(BaselineCompatibilityCaseNames),
        )
        self.assertEqual(
            set(CandidateCompatibility).difference(
                Baseline["Compatibility"]
            ),
            {
                "CpuExecutionProfile",
                "LoadProfile",
                "PhysicalTemplates",
            },
        )
        self.assertTrue(Compatibility["Compatible"])
        self.assertEqual(Compatibility["MismatchFields"], [])
        self.assertTrue(
            Compatibility["LegacyV15AdditiveCompatibilityApplied"]
        )
        self.assertEqual(
            Compatibility["IgnoredLegacyAdditiveFields"],
            [
                "PhysicalTemplates",
                "CpuExecutionProfile",
                "LoadProfile",
            ],
        )
        self.assertEqual(
            sha256(FixturePath.read_bytes()).hexdigest(),
            FixtureDigest,
        )

    def testPreservedV15FixtureReachesModernCandidateComparison(
        self,
    ) -> None:
        FixturePath = (
            Path(__file__).resolve().parent.parent
            / "Fixtures"
            / "RouterRegressionBaseline.json"
        )
        Baseline = ReadBaselineReference(FixturePath)
        CandidateRuns: list[dict[str, object]] = [{
            "RunName": "FullAdderWarmup",
            "Circuit": "FullAdder",
            "Warmup": True,
            "MeasurementIncluded": False,
            "Accepted": True,
        }]
        for Case in AcceptanceCases:
            if Case.Name not in RegressionCaseNames:
                continue
            Summary = Baseline["Cases"][Case.Name]
            Evidence = {
                Name: deepcopy(Summary.get(Name))
                for Name in DeterministicEvidenceFields
            }
            self.assertEqual(
                Evidence["SimulationBackend"],
                "native-parallel",
            )
            for RunIndex, RuntimeSeconds in enumerate(
                Summary["Runtime"]["SamplesSeconds"],
                start=1,
            ):
                CandidateRuns.append({
                    "RunName": f"{Case.Name}Run{RunIndex}",
                    "Circuit": Case.Name,
                    "Warmup": False,
                    "MeasurementIncluded": True,
                    "Accepted": True,
                    "Evaluation": {
                        "Accepted": True,
                        "Failures": [],
                        "Process": {
                            "WallRuntimeSecondsRaw": RuntimeSeconds,
                        },
                    },
                    "Determinism": {
                        "Evidence": deepcopy(Evidence),
                    },
                })

        CandidateProvenance = deepcopy(Baseline["SourceProvenance"])
        CandidateProvenance["ExpectedPolicyVersion"] = (
            CurrentPolicyVersion
        )
        Comparison = BuildBaselineComparison(
            Baseline=Baseline,
            CandidateRuns=CandidateRuns,
            Compatibility={"Compatible": True},
            CandidateSourceProvenance=CandidateProvenance,
        )

        self.assertTrue(Comparison["Passed"])
        self.assertTrue(Comparison["MeasurementValid"])
        self.assertFalse(Comparison["QuietRerunRequired"])
        for CaseName in RegressionCaseNames:
            Circuit = Comparison["Circuits"][CaseName]
            self.assertTrue(Circuit["Passed"])
            self.assertTrue(Circuit["SimulationBackendCompatible"])

    def testLegacyV15CompatibilityStillRequiresCoreProfiles(
        self,
    ) -> None:
        FixturePath = (
            Path(__file__).resolve().parent.parent
            / "Fixtures"
            / "RouterRegressionBaseline.json"
        )
        Baseline = ReadBaselineReference(FixturePath)
        Candidate = deepcopy(Baseline["Compatibility"])
        Candidate.update({
            "PhysicalTemplates": {"AggregateSha256": "new-template-pack"},
            "CpuExecutionProfile": {"Governors": ["performance"]},
            "LoadProfile": {
                "EffectiveCpuCapacity": 32.0,
                "CompatibilityClass": "quiet",
            },
        })
        Mutations = {
            "BenchmarkInputs": lambda Value: Value[
                "BenchmarkInputs"
            ].pop("FullAdder"),
            "Hardware": lambda Value: Value["CpuProfile"].__setitem__(
                "LogicalCpuCount",
                16,
            ),
            "Python": lambda Value: Value.__setitem__(
                "PythonVersion",
                "3.13.0",
            ),
            "Seed": lambda Value: Value.__setitem__("PolicySeed", 1),
            "Threads": lambda Value: Value.__setitem__(
                "RoutingThreads",
                8,
            ),
            "Strategy": lambda Value: Value[
                "BenchmarkProfile"
            ].__setitem__("RoutingStrategy", "legacy"),
            "RoutingEnvironment": lambda Value: Value[
                "RoutingEnvironment"
            ].__setitem__("RC_ROUTING_THREADS", "8"),
        }

        for ExpectedField, Mutate in Mutations.items():
            with self.subTest(ExpectedField=ExpectedField):
                MutatedCandidate = deepcopy(Candidate)
                Mutate(MutatedCandidate)
                Comparison = CompareCompatibility(
                    Baseline,
                    MutatedCandidate,
                )
                CanonicalField = {
                    "Hardware": "CpuProfile",
                    "Python": "PythonVersion",
                    "Seed": "PolicySeed",
                    "Threads": "RoutingThreads",
                    "Strategy": "BenchmarkProfile",
                }.get(ExpectedField, ExpectedField)
                self.assertFalse(Comparison["Compatible"])
                self.assertIn(
                    CanonicalField,
                    Comparison["MismatchFields"],
                )

    def testLegacyV15CeilingMigrationIsLimitedToFullAdder(self) -> None:
        FixturePath = (
            Path(__file__).resolve().parent.parent
            / "Fixtures"
            / "RouterRegressionBaseline.json"
        )
        Baseline = ReadBaselineReference(FixturePath)
        Candidate = deepcopy(Baseline["Compatibility"])
        CandidateFullAdder = Candidate["BenchmarkProfile"]["Cases"][
            "FullAdder"
        ]
        CandidateFullAdder["RuntimeCeilingSeconds"] = 15.0
        CandidateFullAdder["RoutingDeadlineSeconds"] = 13.0

        MigratedComparison = CompareCompatibility(Baseline, Candidate)

        self.assertTrue(MigratedComparison["Compatible"])
        self.assertEqual(MigratedComparison["MismatchFields"], [])
        InvalidCandidates = {
            "different-full-adder-ceiling": deepcopy(Candidate),
            "widened-rca4-ceiling": deepcopy(Candidate),
        }
        InvalidFullAdder = InvalidCandidates[
            "different-full-adder-ceiling"
        ]["BenchmarkProfile"]["Cases"]["FullAdder"]
        InvalidFullAdder["RuntimeCeilingSeconds"] = 20.0
        InvalidFullAdder["RoutingDeadlineSeconds"] = 18.0
        InvalidRca4 = InvalidCandidates["widened-rca4-ceiling"][
            "BenchmarkProfile"
        ]["Cases"]["RippleCarryAdder4"]
        InvalidRca4["RuntimeCeilingSeconds"] = 2_500.0
        InvalidRca4["RoutingDeadlineSeconds"] = 2_498.0
        for Name, InvalidCandidate in InvalidCandidates.items():
            with self.subTest(Name=Name):
                Comparison = CompareCompatibility(
                    Baseline,
                    InvalidCandidate,
                )
                self.assertFalse(Comparison["Compatible"])
                self.assertIn(
                    "BenchmarkProfile",
                    Comparison["MismatchFields"],
                )

    def testLegacyFullAdderCeilingNormalizationIsPureAndIdempotent(
        self,
    ) -> None:
        FixturePath = (
            Path(__file__).resolve().parent.parent
            / "Fixtures"
            / "RouterRegressionBaseline.json"
        )
        Baseline = ReadBaselineReference(FixturePath)
        Reference = deepcopy(Baseline["Compatibility"])
        Candidate = deepcopy(Reference)
        CandidateFullAdder = Candidate["BenchmarkProfile"]["Cases"][
            "FullAdder"
        ]
        CandidateFullAdder["RuntimeCeilingSeconds"] = 15.0
        CandidateFullAdder["RoutingDeadlineSeconds"] = 13.0
        OriginalReference = deepcopy(Reference)
        OriginalCandidate = deepcopy(Candidate)

        First = NormalizeLegacyFullAdderCeilingCompatibility(
            Reference,
            Candidate,
            AllowLegacyMigration=True,
        )
        Second = NormalizeLegacyFullAdderCeilingCompatibility(
            First,
            Candidate,
            AllowLegacyMigration=True,
        )

        self.assertEqual(Reference, OriginalReference)
        self.assertEqual(Candidate, OriginalCandidate)
        self.assertEqual(First, Second)
        self.assertEqual(First, Candidate)
        self.assertEqual(
            NormalizeLegacyFullAdderCeilingCompatibility(
                Reference,
                Candidate,
                AllowLegacyMigration=False,
            ),
            Reference,
        )

    def testAdditiveCompatibilityOmissionsRequireV15Provenance(
        self,
    ) -> None:
        FixturePath = (
            Path(__file__).resolve().parent.parent
            / "Fixtures"
            / "RouterRegressionBaseline.json"
        )
        Baseline = ReadBaselineReference(FixturePath)
        Baseline["SourceProvenance"]["ExpectedPolicyVersion"] = (
            CurrentPolicyVersion
        )
        Candidate = deepcopy(Baseline["Compatibility"])
        Candidate.update({
            "PhysicalTemplates": {"AggregateSha256": "new-template-pack"},
            "CpuExecutionProfile": {"Governors": ["performance"]},
            "LoadProfile": {
                "EffectiveCpuCapacity": 32.0,
                "CompatibilityClass": "quiet",
            },
        })

        Comparison = CompareCompatibility(Baseline, Candidate)

        self.assertFalse(Comparison["Compatible"])
        self.assertEqual(
            Comparison["MismatchFields"],
            [
                "PhysicalTemplates",
                "CpuExecutionProfile",
                "LoadProfile",
            ],
        )
        self.assertFalse(
            Comparison["LegacyV15AdditiveCompatibilityApplied"]
        )
        self.assertEqual(
            Comparison["IgnoredLegacyAdditiveFields"],
            [],
        )

    def testCompatibilityPromotionRequiresFinalStableProvenance(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            BaselinePath, _Capture = self.CaptureBaseline(Root)
            BaselineBytes = BaselinePath.read_bytes()
            ProvenanceCalls = 0

            def CandidateProvenance(
                Configuration: AcceptanceConfiguration,
                SourceState: dict[str, object],
            ) -> dict[str, object]:
                nonlocal ProvenanceCalls
                ProvenanceCalls += 1
                return SourceProvenanceFixture(
                    Configuration,
                    SourceState,
                    SourceDigest=(
                        "changed-during-cla4"
                        if ProvenanceCalls >= 3
                        else "candidate-source"
                    ),
                )

            Manifest = RunAcceptance(
                self.Configuration(
                    Root / "candidate",
                    BaselineMode="compare",
                    BaselinePath=BaselinePath,
                    IncludeCla4=True,
                ),
                CommandRunner=self.SyntheticRunner(
                    PolicyVersion=CandidatePolicyVersion,
                ),
                SourceStateProvider=lambda _Root: {
                    "Revision": "candidate-revision",
                    "Dirty": True,
                },
                SourceProvenanceProvider=CandidateProvenance,
                DesignDigestBuilder=DigestFixture,
                TruthTableEvidenceBuilder=TruthTableEvidenceFixture,
                UtcNowProvider=lambda: "2026-07-21T12:00:00+00:00",
            )

            self.assertEqual(ProvenanceCalls, 3)
            Comparison = Manifest["BaselineComparison"]
            self.assertFalse(Manifest["Accepted"])
            self.assertFalse(Comparison["Passed"])
            self.assertFalse(Comparison["ProvenanceStable"])
            self.assertFalse(Comparison["CompatibilityPromotionPassed"])
            self.assertEqual(Comparison["FirstValidBaselines"], {})
            self.assertEqual(
                Comparison["UnbaselinedCircuits"],
                ["CarryLookaheadAdder4"],
            )
            self.assertTrue(
                Comparison["CompatibilityCandidateBaseline"]["Promotable"]
            )
            self.assertIn(
                "source/native/policy provenance changed during extended",
                Comparison["Failure"],
            )
            self.assertEqual(BaselinePath.read_bytes(), BaselineBytes)

    def testCompatibilityFirstValidBaselinePersistsWithoutFabricatedComparison(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            BaselinePath, _Capture = self.CaptureBaseline(Root)
            BaselinePath.write_bytes(
                b" \n" + BaselinePath.read_bytes()
            )
            OriginalBytes = BaselinePath.read_bytes()
            OriginalReference = json.loads(OriginalBytes)
            OriginalCases = deepcopy(OriginalReference["Cases"])

            Manifest = RunAcceptance(
                self.Configuration(
                    Root / "candidate",
                    BaselineMode="compare",
                    BaselinePath=BaselinePath,
                    IncludeCla4=True,
                ),
                CommandRunner=self.SyntheticRunner(
                    PolicyVersion=CandidatePolicyVersion,
                ),
                SourceStateProvider=lambda _Root: {
                    "Revision": "candidate-revision",
                    "Dirty": True,
                },
                SourceProvenanceProvider=SourceProvenanceFixture,
                DesignDigestBuilder=DigestFixture,
                TruthTableEvidenceBuilder=TruthTableEvidenceFixture,
                UtcNowProvider=lambda: (
                    "2026-07-21T12:00:00+00:00"
                ),
            )

            self.assertTrue(Manifest["Accepted"])
            Comparison = Manifest["BaselineComparison"]
            self.assertFalse(Comparison["BaselineAvailableBeforeRun"])
            self.assertTrue(Comparison["CompatibilityPromotionPassed"])
            self.assertIsNone(Comparison["CompatibilityBaselineComparison"])
            self.assertNotIn(
                "CarryLookaheadAdder4",
                Comparison["Circuits"],
            )
            self.assertEqual(Comparison["UnbaselinedCircuits"], [])
            Promotion = Comparison["FirstValidBaselinePromotion"]
            self.assertTrue(Promotion["Attempted"])
            self.assertTrue(Promotion["Written"])
            self.assertFalse(Promotion["ReferenceAlreadyAvailable"])

            Published = ReadBaselineReference(BaselinePath)
            self.assertEqual(Published["Cases"], OriginalCases)
            self.assertEqual(set(Published["Cases"]), set(RegressionCaseNames))
            self.assertNotIn(
                "CarryLookaheadAdder4",
                Published["Cases"],
            )
            Entry = Published["FirstValidBaselines"][
                "CarryLookaheadAdder4"
            ]
            self.assertEqual(
                Entry["OriginalReferenceSha256"],
                sha256(OriginalBytes).hexdigest(),
            )
            self.assertEqual(
                Entry["PromotedAtUtc"],
                "2026-07-21T12:00:00+00:00",
            )
            self.assertEqual(
                Entry["Compatibility"],
                Manifest["ComparisonCompatibility"],
            )
            self.assertEqual(Entry["Environment"], Manifest["Environment"])
            self.assertEqual(
                Entry["SourceProvenance"],
                Manifest["SourceProvenance"],
            )
            self.assertTrue(Entry["CaseSummary"]["Promotable"])
            self.assertEqual(
                Entry["CaseSummary"]["MeasuredRunCount"],
                2,
            )

    def testCompatibilityFailedEvidenceDoesNotWriteFirstValidBaseline(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            BaselinePath, _Capture = self.CaptureBaseline(Root)
            BaselineBytes = BaselinePath.read_bytes()

            Manifest = RunAcceptance(
                self.Configuration(
                    Root / "candidate",
                    BaselineMode="compare",
                    BaselinePath=BaselinePath,
                    IncludeCla4=True,
                ),
                CommandRunner=self.SyntheticRunner(
                    PolicyVersion=CandidatePolicyVersion,
                    FailureRun="CarryLookaheadAdder4Run1",
                ),
                SourceStateProvider=lambda _Root: {
                    "Revision": "candidate-revision",
                    "Dirty": True,
                },
                SourceProvenanceProvider=SourceProvenanceFixture,
                DesignDigestBuilder=DigestFixture,
                TruthTableEvidenceBuilder=TruthTableEvidenceFixture,
                UtcNowProvider=lambda: (
                    "2026-07-21T12:00:00+00:00"
                ),
            )

            self.assertFalse(Manifest["Accepted"])
            Comparison = Manifest["BaselineComparison"]
            self.assertFalse(Comparison["CompatibilityPromotionPassed"])
            self.assertEqual(Comparison["FirstValidBaselines"], {})
            self.assertEqual(
                Comparison["UnbaselinedCircuits"],
                ["CarryLookaheadAdder4"],
            )
            self.assertEqual(BaselinePath.read_bytes(), BaselineBytes)
            self.assertNotIn(
                "FirstValidBaselines",
                ReadBaselineReference(BaselinePath),
            )

    def testCompatibilityPromotionWriteFailureLeavesReferenceUnchanged(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            BaselinePath, _Capture = self.CaptureBaseline(Root)
            BaselineBytes = BaselinePath.read_bytes()

            def FailReferenceWrite(
                _PathValue: Path,
                _Manifest: dict[str, object],
            ) -> None:
                raise OSError("fixture promotion write failure")

            Manifest = RunAcceptance(
                self.Configuration(
                    Root / "candidate",
                    BaselineMode="compare",
                    BaselinePath=BaselinePath,
                    IncludeCla4=True,
                ),
                CommandRunner=self.SyntheticRunner(
                    PolicyVersion=CandidatePolicyVersion,
                ),
                SourceStateProvider=lambda _Root: {
                    "Revision": "candidate-revision",
                    "Dirty": True,
                },
                SourceProvenanceProvider=SourceProvenanceFixture,
                DesignDigestBuilder=DigestFixture,
                TruthTableEvidenceBuilder=TruthTableEvidenceFixture,
                UtcNowProvider=lambda: (
                    "2026-07-21T12:00:00+00:00"
                ),
                BaselineReferenceWriter=FailReferenceWrite,
            )

            self.assertFalse(Manifest["Accepted"])
            Promotion = Manifest["BaselineComparison"][
                "FirstValidBaselinePromotion"
            ]
            self.assertTrue(Promotion["Attempted"])
            self.assertFalse(Promotion["Written"])
            self.assertIn("write failure", Promotion["Failure"])
            self.assertEqual(BaselinePath.read_bytes(), BaselineBytes)
            self.assertNotIn(
                "FirstValidBaselines",
                ReadBaselineReference(BaselinePath),
            )

    def testCompatibilityPromotionFinalReadFailureRollsBackReference(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            BaselinePath, _Capture = self.CaptureBaseline(Root)
            BaselineBytes = BaselinePath.read_bytes()
            ReadCount = 0

            def FailFinalReferenceRead(
                PathValue: Path,
            ) -> dict[str, object]:
                nonlocal ReadCount
                ReadCount += 1
                if ReadCount == 3:
                    raise OSError("fixture final reread failure")
                return ReadBaselineReference(PathValue)

            Manifest = RunAcceptance(
                self.Configuration(
                    Root / "candidate",
                    BaselineMode="compare",
                    BaselinePath=BaselinePath,
                    IncludeCla4=True,
                ),
                CommandRunner=self.SyntheticRunner(
                    PolicyVersion=CandidatePolicyVersion,
                ),
                SourceStateProvider=lambda _Root: {
                    "Revision": "candidate-revision",
                    "Dirty": True,
                },
                SourceProvenanceProvider=SourceProvenanceFixture,
                DesignDigestBuilder=DigestFixture,
                TruthTableEvidenceBuilder=TruthTableEvidenceFixture,
                UtcNowProvider=lambda: (
                    "2026-07-21T12:00:00+00:00"
                ),
                BaselineReferenceReader=FailFinalReferenceRead,
            )

            self.assertEqual(ReadCount, 3)
            self.assertFalse(Manifest["Accepted"])
            Promotion = Manifest["BaselineComparison"][
                "FirstValidBaselinePromotion"
            ]
            self.assertFalse(Promotion["Written"])
            self.assertIn("reread failure", Promotion["Failure"])
            self.assertEqual(BaselinePath.read_bytes(), BaselineBytes)
            self.assertNotIn(
                "FirstValidBaselines",
                ReadBaselineReference(BaselinePath),
            )

    def testForgedCompatibilityFirstValidBaselineIsRejected(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            BaselinePath, _Manifest = self.PromoteCompatibilityBaseline(Root)
            Baseline = json.loads(
                BaselinePath.read_text(encoding="utf-8")
            )
            Summary = Baseline["FirstValidBaselines"][
                "CarryLookaheadAdder4"
            ]["CaseSummary"]
            Summary["FootprintMetrics"]["Footprint"] += 1
            BaselinePath.write_text(
                json.dumps(Baseline, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                "Footprint is not Width \\* Depth",
            ):
                ReadBaselineReference(BaselinePath)

    def testExistingCompatibilityFirstValidBaselineIsNeverReplaced(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            BaselinePath, _FirstManifest = self.PromoteCompatibilityBaseline(Root)
            BaselineBytes = BaselinePath.read_bytes()

            Manifest = RunAcceptance(
                self.Configuration(
                    Root / "second-candidate",
                    BaselineMode="compare",
                    BaselinePath=BaselinePath,
                    IncludeCla4=True,
                ),
                CommandRunner=self.SyntheticRunner(
                    PolicyVersion=CandidatePolicyVersion,
                ),
                SourceStateProvider=lambda _Root: {
                    "Revision": "second-candidate-revision",
                    "Dirty": True,
                },
                SourceProvenanceProvider=SourceProvenanceFixture,
                DesignDigestBuilder=DigestFixture,
                TruthTableEvidenceBuilder=TruthTableEvidenceFixture,
                UtcNowProvider=lambda: (
                    "2026-07-22T12:00:00+00:00"
                ),
            )

            self.assertTrue(Manifest["Accepted"])
            Comparison = Manifest["BaselineComparison"]
            self.assertTrue(Comparison["BaselineAvailableBeforeRun"])
            self.assertFalse(Comparison["CompatibilityPromotionPassed"])
            self.assertTrue(
                Comparison["FirstValidBaselinePromotion"][
                    "OverwriteBlocked"
                ]
            )
            self.assertTrue(
                Comparison["CompatibilityBaselineComparison"]["Passed"]
            )
            self.assertIn(
                "CarryLookaheadAdder4",
                Comparison["Circuits"],
            )
            self.assertEqual(BaselinePath.read_bytes(), BaselineBytes)

    def testStoredCompatibilityBaselineGatesLaterRegressions(self) -> None:
        with tempfile.TemporaryDirectory() as DirectoryValue:
            Root = Path(DirectoryValue)
            BaselinePath, _FirstManifest = self.PromoteCompatibilityBaseline(Root)
            BaselineBytes = BaselinePath.read_bytes()
            Scenarios = {
                "footprint": {
                    "Changes": {
                        "CarryLookaheadAdder4Run1": {
                            "ExactNonAirBlocks": 301,
                        },
                        "CarryLookaheadAdder4Run2": {
                            "ExactNonAirBlocks": 301,
                        },
                    },
                    "Gate": "FootprintPassed",
                },
                "backend": {
                    "Changes": {
                        "CarryLookaheadAdder4Run1": {
                            "FabricValidationBackend": "python",
                        },
                        "CarryLookaheadAdder4Run2": {
                            "FabricValidationBackend": "python",
                        },
                    },
                    "Gate": "SimulationBackendCompatible",
                },
                "speed": {
                    "Runtimes": {
                        "CarryLookaheadAdder4Run1": (
                            1.0 + MaximumRuntimeRegressionFraction + 0.000001
                        ),
                        "CarryLookaheadAdder4Run2": (
                            1.0 + MaximumRuntimeRegressionFraction + 0.000001
                        ),
                    },
                    "Gate": "SpeedPassed",
                },
                "determinism": {
                    "Changes": {
                        "CarryLookaheadAdder4Run2": {
                            "CandidateFingerprint": "changed-candidate",
                        },
                    },
                    "Gate": "AccuracyAndDeterminismPassed",
                },
            }
            for Name, Scenario in Scenarios.items():
                with self.subTest(Name=Name):
                    Manifest = RunAcceptance(
                        self.Configuration(
                            Root / f"candidate-{Name}",
                            BaselineMode="compare",
                            BaselinePath=BaselinePath,
                            IncludeCla4=True,
                        ),
                        CommandRunner=self.SyntheticRunner(
                            PolicyVersion=CandidatePolicyVersion,
                            RuntimeByRun=Scenario.get("Runtimes"),
                            PhysicalChangesByRun=Scenario.get("Changes"),
                        ),
                        SourceStateProvider=lambda _Root: {
                            "Revision": "later-candidate-revision",
                            "Dirty": True,
                        },
                        SourceProvenanceProvider=SourceProvenanceFixture,
                        DesignDigestBuilder=DigestFixture,
                        TruthTableEvidenceBuilder=(
                            TruthTableEvidenceFixture
                        ),
                        UtcNowProvider=lambda: (
                            "2026-07-22T12:00:00+00:00"
                        ),
                    )

                    self.assertFalse(Manifest["Accepted"])
                    Comparison = Manifest["BaselineComparison"]
                    self.assertTrue(
                        Comparison["BaselineAvailableBeforeRun"]
                    )
                    self.assertFalse(
                        Comparison["CompatibilityBaselineComparison"]["Passed"]
                    )
                    Circuit = Comparison["Circuits"][
                        "CarryLookaheadAdder4"
                    ]
                    self.assertFalse(Circuit[Scenario["Gate"]])
                    self.assertEqual(
                        BaselinePath.read_bytes(),
                        BaselineBytes,
                    )

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

            for MatrixMode, ExpectedRuns in (
                ("default", 3),
                ("expanded", 7),
            ):
                with self.subTest(MatrixMode=MatrixMode):
                    Configuration = self.Configuration(
                        Root / MatrixMode,
                        MatrixMode=MatrixMode,
                    )
                    Manifest = RunAcceptance(
                        Configuration,
                        CommandRunner=Runner,
                        SourceStateProvider=lambda _Root: {
                            "Revision": "revision",
                            "Dirty": False,
                        },
                        SourceProvenanceProvider=SourceProvenanceFixture,
                        UtcNowProvider=lambda: (
                            "2026-07-21T12:00:00+00:00"
                        ),
                    )

                    self.assertEqual(len(Manifest["Runs"]), ExpectedRuns)
                    self.assertEqual(Manifest["Status"], "FAILED")
                    self.assertFalse(Manifest["Accepted"])
                    self.assertFalse(Manifest["FailFast"])
                    self.assertTrue(all(
                        Run["Status"] == "FAILED"
                        for Run in Manifest["Runs"]
                    ))
                    self.assertEqual(
                        Manifest["Runs"][0]["Evaluation"]["Process"]
                        ["ReturnCode"],
                        1,
                    )
                    self.assertTrue(all(
                        Run["Evaluation"]["Artifacts"]
                        ["RoutingFailure"]["Exists"]
                        for Run in Manifest["Runs"]
                    ))


if __name__ == "__main__":
    unittest.main()
