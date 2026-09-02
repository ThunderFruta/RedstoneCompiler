import os
import socket
import sys
import unittest
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch
from pathlib import Path

from Compiler.FabricServer import (
    BuildExpectedVectors,
    BuildFabricFailureTrace,
    BuildFabricFixture,
    BuildImportedSchematicVectors,
    BuildValidationVectors,
    DefaultFabricServerRoot,
    FabricServerConfiguration,
    FabricServerSupervisor,
    FabricServerValidationResult,
    FabricValidationProgress,
    ReadFabricFixture,
    ReadNandModule,
    ReadSvModule,
    ResolveFabricServerRoot,
)
from Compiler.FabricServer.SchemImport import InferLitematicPorts
from Compiler.Ir.Models import Gate, GateKind, ModuleIR
from Compiler.Pipeline import RequirePhysicalValidation
from SchemEncoder.SchemWriter import CellTemplate, BuildLitematicBlockMap, NeutralDynamicState


def WriteMinimalFabricFixture(PathValue: Path) -> None:
    """Write one fixture with a deterministic one-block clear bound."""
    PathValue.write_text(
        '{"Arena":{"Origin":[0,64,0]},"Blocks":[{"Position":[0,0,0]}]}',
        encoding="utf-8",
    )


class FabricServerBoundaryTests(unittest.TestCase):
    def testEnvironmentUsesTheRepositoryCanonicalServerRootByDefault(self) -> None:
        with patch.dict(os.environ, {"RC_FABRIC_SERVER_ROOT": ""}):
            Configuration = FabricServerConfiguration.FromEnvironment()

        self.assertEqual(Configuration.Root, DefaultFabricServerRoot())

    def testEnvironmentHonorsAnExplicitServerRootOverride(self) -> None:
        with TemporaryDirectory() as TemporaryDirectoryPath, patch.dict(
            os.environ,
            {"RC_FABRIC_SERVER_ROOT": TemporaryDirectoryPath},
        ):
            Configuration = FabricServerConfiguration.FromEnvironment()

        self.assertEqual(Configuration.Root, Path(TemporaryDirectoryPath).resolve())
        self.assertEqual(ResolveFabricServerRoot(), DefaultFabricServerRoot())

    def testEnvironmentConfiguresTheLongRunningValidationResponseTimeout(self) -> None:
        with patch.dict(
            os.environ,
            {"RC_FABRIC_VALIDATION_TIMEOUT": "123.5"},
        ):
            Configuration = FabricServerConfiguration.FromEnvironment()

        self.assertEqual(Configuration.ValidationTimeoutSeconds, 123.5)

    def testValidationResponseTimeoutDoesNotRetryTheSubmittedRequest(self) -> None:
        Supervisor = FabricServerSupervisor(FabricServerConfiguration(
            Root=None,
            StartupTimeoutSeconds=1.0,
            ValidationTimeoutSeconds=12.5,
        ))
        Connection = MagicMock()
        Connection.__enter__.return_value = Connection
        Stream = MagicMock()
        Stream.readline.side_effect = socket.timeout("response deadline")
        Connection.makefile.return_value = Stream
        with patch(
            "Compiler.FabricServer.Validation.socket.create_connection",
            return_value=Connection,
        ) as CreateConnection, self.assertRaisesRegex(
            RuntimeError,
            "validation request exceeded its response timeout",
        ):
            Supervisor._RequestWhenReady(
                "token",
                {"Action": "Validate"},
                Port=25566,
            )

        CreateConnection.assert_called_once()
        Connection.settimeout.assert_called_once_with(12.5)

    def testValidationProgressComesFromStreamedTruthTableRows(self) -> None:
        Supervisor = FabricServerSupervisor(FabricServerConfiguration(
            Root=None,
            StartupTimeoutSeconds=1.0,
            ValidationTimeoutSeconds=12.5,
        ))
        Connection = MagicMock()
        Connection.__enter__.return_value = Connection
        Stream = MagicMock()
        Stream.readline.side_effect = [
            b'{"Status":"progress","Completed":0,"Total":2,'
            b'"Stage":"authoritative Fabric truth-table validation"}\n',
            b'{"Status":"progress","Completed":1,"Total":2,'
            b'"Stage":"authoritative Fabric truth-table validation"}\n',
            b'{"Status":"progress","Completed":2,"Total":2,'
            b'"Stage":"authoritative Fabric truth-table validation"}\n',
            b'{"Status":"passed","Diagnostics":{"TestedVectors":2}}\n',
        ]
        Connection.makefile.return_value = Stream
        Progress = []
        with patch(
            "Compiler.FabricServer.Validation.socket.create_connection",
            return_value=Connection,
        ):
            Response = Supervisor._RequestWhenReady(
                "token",
                {"Action": "Validate"},
                Port=25566,
                ProgressCallback=Progress.append,
            )

        self.assertEqual(Response["Status"], "passed")
        self.assertEqual(
            Progress,
            [
                FabricValidationProgress(
                    Completed=0,
                    Total=2,
                    Stage="authoritative Fabric truth-table validation",
                    Backend="fabric-26.2-canary",
                ),
                FabricValidationProgress(
                    Completed=1,
                    Total=2,
                    Stage="authoritative Fabric truth-table validation",
                    Backend="fabric-26.2-canary",
                ),
                FabricValidationProgress(
                    Completed=2,
                    Total=2,
                    Stage="authoritative Fabric truth-table validation",
                    Backend="fabric-26.2-canary",
                ),
            ],
        )

    def testMissingServerIsAnInfrastructureFailure(self) -> None:
        Result = FabricServerSupervisor(
            FabricServerConfiguration(Root=None),
        ).Validate(
            Fixture=SimpleNamespace(Path=None, Sha256="", BlockCount=0),
            Vectors=[],
        )

        self.assertEqual(Result.Status, "infrastructure-failure")
        self.assertEqual(Result.Diagnostics["Reason"], "server-root-not-configured")

    def testValidationClearsTheEntireManagedWorldBeforePasting(self) -> None:
        with TemporaryDirectory() as TemporaryDirectoryPath:
            Root = Path(TemporaryDirectoryPath)
            (Root / "mods").mkdir()
            (Root / "config").mkdir()
            (Root / "PyScripts").mkdir()
            (Root / "fabric-server-launch.jar").write_bytes(b"launcher")
            (Root / "mods" / "redstonecompiler-harness.jar").write_bytes(b"harness")
            (Root / "PyScripts" / "Main.py").write_text("", encoding="utf-8")
            (Root / "config" / "redstonecompiler-harness.json").write_text(
                '{"Token":"manager-token","Port":25566}',
                encoding="utf-8",
            )
            FixturePath = Root / "fixture.FabricFixture.json"
            WriteMinimalFabricFixture(FixturePath)
            Supervisor = FabricServerSupervisor(FabricServerConfiguration(Root=Root))
            WorldClear = {
                "PrePasteWorldClearMode": "live-persisted-overworld-blocks",
                "PrePasteWorldClearChunkCount": 5,
                "PrePasteWorldClearNonAirBlockCount": 4096,
                "PrePasteWorldScannedChunkCount": 8,
                "PrePasteWorldScannedRegionFileCount": 2,
            }
            with patch.object(
                Supervisor,
                "_ClearCanonicalSimulationWorld",
                return_value=WorldClear,
            ) as Clear, patch.object(
                Supervisor,
                "_GetRunningControl",
                return_value=("fresh-manager-token", 25566),
            ) as Control, patch.object(
                Supervisor,
                "_RequestWhenReady",
                return_value={"Status": "passed", "Diagnostics": {}},
            ) as Ready:
                Result = Supervisor.Validate(
                    Fixture=SimpleNamespace(Path=FixturePath, Sha256="fixture-sha"),
                    Vectors=[],
                )

        self.assertEqual(Result.Status, "passed")
        Clear.assert_called_once_with(Root, Root / "PyScripts" / "Main.py")
        Control.assert_called_once_with(Root)
        self.assertEqual(
            Ready.call_args.args,
            (
                "fresh-manager-token",
                {
                    "Action": "Validate",
                    "FixturePath": str(FixturePath.resolve()),
                    "FixtureSha256": "fixture-sha",
                    "Vectors": [],
                },
            ),
        )
        self.assertEqual(Ready.call_args.kwargs["Port"], 25566)
        self.assertEqual(Result.Diagnostics, WorldClear)

    def testExistingWorldValidationDoesNotClearOrPasteTheFixture(self) -> None:
        with TemporaryDirectory() as TemporaryDirectoryPath:
            Root = Path(TemporaryDirectoryPath)
            (Root / "config").mkdir()
            (Root / "config" / "redstonecompiler-harness.json").write_text(
                '{"Token":"live-token","Port":25566}',
                encoding="utf-8",
            )
            FixturePath = Root / "fixture.FabricFixture.json"
            WriteMinimalFabricFixture(FixturePath)
            Supervisor = FabricServerSupervisor(FabricServerConfiguration(Root=Root))
            with patch.object(
                Supervisor,
                "_ClearCanonicalSimulationWorld",
            ) as Clear, patch.object(
                Supervisor,
                "_RequestWhenReady",
                return_value={
                    "Status": "passed",
                    "Diagnostics": {
                        "WorldStateMode": "existing",
                        "FixturePasted": False,
                    },
                },
            ) as Ready:
                Result = Supervisor.ValidateExisting(
                    Fixture=SimpleNamespace(Path=FixturePath, Sha256="fixture-sha"),
                    Vectors=[{"Inputs": {"a": False}, "Expected": {"y": False}}],
                )

        self.assertEqual(Result.Status, "passed")
        self.assertFalse(Result.Diagnostics["WorldCleared"])
        self.assertFalse(Result.Diagnostics["FixturePasted"])
        Clear.assert_not_called()
        self.assertEqual(
            Ready.call_args.args,
            (
                "live-token",
                {
                    "Action": "ValidateExisting",
                    "FixturePath": str(FixturePath.resolve()),
                    "FixtureSha256": "fixture-sha",
                    "Vectors": [
                        {"Inputs": {"a": False}, "Expected": {"y": False}},
                    ],
                },
            ),
        )
        self.assertEqual(
            Ready.call_args.kwargs,
            {"Port": 25566, "ProgressCallback": None},
        )

    def testFullWorldClearInvokesTheManagerClearActionWithoutRestarting(self) -> None:
        with TemporaryDirectory() as TemporaryDirectoryPath:
            Root = Path(TemporaryDirectoryPath)
            Manager = Root / "PyScripts" / "Main.py"
            Manager.parent.mkdir(parents=True)
            Manager.write_text("", encoding="utf-8")
            Supervisor = FabricServerSupervisor(FabricServerConfiguration(Root=Root))
            with patch(
                "Compiler.FabricServer.Validation.subprocess.run",
            ) as Run:
                Run.return_value.returncode = 0
                Run.return_value.stdout = (
                    '{"Status":"running","Cleared":true,'
                    '"ClearMode":"live-persisted-overworld-blocks",'
                    '"ClearedChunkCount":4,'
                    '"ClearedNonAirBlocks":8192,'
                    '"ScannedChunkCount":7,'
                    '"ScannedRegionFileCount":2}'
                )
                Result = Supervisor._ClearCanonicalSimulationWorld(Root, Manager)

        self.assertEqual(
            Run.call_args.args[0],
            [
                sys.executable,
                str(Manager),
                "clear",
            ],
        )
        self.assertEqual(
            Result,
            {
                "PrePasteWorldClearMode": "live-persisted-overworld-blocks",
                "PrePasteWorldClearChunkCount": 4,
                "PrePasteWorldClearNonAirBlockCount": 8192,
                "PrePasteWorldScannedChunkCount": 7,
                "PrePasteWorldScannedRegionFileCount": 2,
            },
        )

    def testValidationFailsClosedWhenFullWorldClearIsRejected(self) -> None:
        with TemporaryDirectory() as TemporaryDirectoryPath:
            Root = Path(TemporaryDirectoryPath)
            (Root / "mods").mkdir()
            (Root / "config").mkdir()
            (Root / "PyScripts").mkdir()
            (Root / "fabric-server-launch.jar").write_bytes(b"launcher")
            (Root / "mods" / "redstonecompiler-harness.jar").write_bytes(b"harness")
            (Root / "PyScripts" / "Main.py").write_text("", encoding="utf-8")
            (Root / "config" / "redstonecompiler-harness.json").write_text(
                '{"Token":"manager-token","Port":25566}',
                encoding="utf-8",
            )
            FixturePath = Root / "fixture.FabricFixture.json"
            WriteMinimalFabricFixture(FixturePath)
            Supervisor = FabricServerSupervisor(FabricServerConfiguration(Root=Root))
            with patch.object(
                Supervisor,
                "_ClearCanonicalSimulationWorld",
                side_effect=RuntimeError("clear-rejected"),
            ) as Clear, patch.object(Supervisor, "_RequestWhenReady") as Ready:
                Result = Supervisor.Validate(
                    Fixture=SimpleNamespace(Path=FixturePath, Sha256="fixture-sha"),
                    Vectors=[],
                )

        self.assertEqual(Result.Status, "infrastructure-failure")
        self.assertEqual(Result.Diagnostics["Reason"], "server-protocol-failure")
        self.assertIn("clear-rejected", Result.Diagnostics["Error"])
        Clear.assert_called_once()
        Ready.assert_not_called()

    def testSupervisorAttachesTheSourceLinkedTraceToAMismatch(self) -> None:
        with TemporaryDirectory() as TemporaryDirectoryPath:
            Root = Path(TemporaryDirectoryPath)
            (Root / "mods").mkdir()
            (Root / "config").mkdir()
            (Root / "PyScripts").mkdir()
            (Root / "fabric-server-launch.jar").write_bytes(b"launcher")
            (Root / "mods" / "redstonecompiler-harness.jar").write_bytes(b"harness")
            (Root / "PyScripts" / "Main.py").write_text("", encoding="utf-8")
            FixturePath = Root / "fixture.FabricFixture.json"
            FixturePath.write_text(
                '{"Trace":{"Circuit":"Top","Gates":[],"Signals":[]}}',
                encoding="utf-8",
            )
            Supervisor = FabricServerSupervisor(FabricServerConfiguration(Root=Root))
            with patch.object(
                Supervisor,
                "_ClearCanonicalSimulationWorld",
                return_value={},
            ), patch.object(
                Supervisor,
                "_GetRunningControl",
                return_value=("token", 25566),
            ), patch.object(
                Supervisor,
                "_RequestWhenReady",
                return_value={
                    "Status": "mismatch",
                    "Diagnostics": {"Mismatch": {"Output": "y", "Expected": True}},
                },
            ), patch(
                "Compiler.FabricServer.Validation.BuildFabricFailureTrace",
                return_value={"FirstFailingBlock": {"WorldPosition": [4, 64, 1]}},
            ) as BuildTrace:
                Result = Supervisor.Validate(
                    Fixture=SimpleNamespace(Path=FixturePath, Sha256="fixture-sha"),
                    Vectors=[],
                )

        self.assertEqual(Result.Status, "mismatch")
        self.assertEqual(
            Result.Diagnostics["FailureTrace"]["FirstFailingBlock"]["WorldPosition"],
            [4, 64, 1],
        )
        BuildTrace.assert_called_once()

    def testValidationRequiresTheRuntimeManagerForAFullWorldClear(self) -> None:
        with TemporaryDirectory() as TemporaryDirectoryPath:
            Root = Path(TemporaryDirectoryPath)
            (Root / "mods").mkdir()
            (Root / "fabric-server-launch.jar").write_bytes(b"launcher")
            (Root / "mods" / "redstonecompiler-harness.jar").write_bytes(b"harness")
            FixturePath = Root / "fixture.FabricFixture.json"
            WriteMinimalFabricFixture(FixturePath)

            Result = FabricServerSupervisor(
                FabricServerConfiguration(Root=Root),
            ).Validate(
                Fixture=SimpleNamespace(Path=FixturePath, Sha256="fixture-sha"),
                Vectors=[],
            )

        self.assertEqual(Result.Status, "infrastructure-failure")
        self.assertEqual(
            Result.Diagnostics["Reason"],
            "full-simulation-world-clear-requires-runtime-manager",
        )

    def testValidationFailsClosedWhenTheClearDoesNotRestoreControl(self) -> None:
        with TemporaryDirectory() as TemporaryDirectoryPath:
            Root = Path(TemporaryDirectoryPath)
            (Root / "mods").mkdir()
            (Root / "config").mkdir()
            (Root / "PyScripts").mkdir()
            (Root / "fabric-server-launch.jar").write_bytes(b"launcher")
            (Root / "mods" / "redstonecompiler-harness.jar").write_bytes(b"harness")
            (Root / "PyScripts" / "Main.py").write_text("", encoding="utf-8")
            (Root / "config" / "redstonecompiler-harness.json").write_text(
                '{"Token":"stale-token","Port":25566}',
                encoding="utf-8",
            )
            FixturePath = Root / "fixture.FabricFixture.json"
            WriteMinimalFabricFixture(FixturePath)
            Supervisor = FabricServerSupervisor(FabricServerConfiguration(Root=Root))
            with patch.object(
                Supervisor,
                "_ClearCanonicalSimulationWorld",
                return_value={
                    "PrePasteWorldClearMode": "live-persisted-overworld-blocks",
                    "PrePasteWorldClearChunkCount": 0,
                    "PrePasteWorldClearNonAirBlockCount": 0,
                    "PrePasteWorldScannedChunkCount": 0,
                    "PrePasteWorldScannedRegionFileCount": 0,
                },
            ) as Clear, patch.object(
                Supervisor,
                "_GetRunningControl",
                return_value=None,
            ) as Control, patch.object(Supervisor, "_RequestWhenReady") as Ready:
                Result = Supervisor.Validate(
                    Fixture=SimpleNamespace(Path=FixturePath, Sha256="fixture-sha"),
                    Vectors=[],
                )

        self.assertEqual(Result.Status, "infrastructure-failure")
        self.assertEqual(Result.Diagnostics["Reason"], "server-protocol-failure")
        self.assertIn("after the full simulation-world clear", Result.Diagnostics["Error"])
        Clear.assert_called_once()
        Control.assert_called_once_with(Root)
        Ready.assert_not_called()

    def testOnlyAnObservedFabricPassCanCompleteThePipeline(self) -> None:
        RequirePhysicalValidation(
            FabricServerValidationResult(Status="passed"),
            "FabricFinalCheck",
        )
        with self.assertRaisesRegex(
            ValueError,
            "FabricFinalCheck:mismatch:output-mismatch:y",
        ):
            RequirePhysicalValidation(
                FabricServerValidationResult(
                    Status="mismatch",
                    Diagnostics={"Error": "output-mismatch:y"},
                ),
                "FabricFinalCheck",
            )

    def testVectorPolicyIsExhaustiveThenDeterministic(self) -> None:
        self.assertEqual(len(BuildValidationVectors(("a", "b"))), 4)
        First = BuildValidationVectors(tuple(f"a{Index}" for Index in range(17)))
        Second = BuildValidationVectors(tuple(f"a{Index}" for Index in range(17)))
        self.assertEqual(len(First), 2 + 34 + 4096)
        self.assertEqual(First, Second)

    def testFixtureUsesIOTemplateBlocksRatherThanSigns(self) -> None:
        Routed = SimpleNamespace(PlacedGates=[
            SimpleNamespace(Name="InputA", Kind="INPUT", Outputs=["a"], X=0, Y=0, Z=0, Rotation=0, MirrorX=False),
            SimpleNamespace(Name="OutputY", Kind="OUTPUT", Outputs=["y$Output"], X=4, Y=0, Z=0, Rotation=0, MirrorX=False),
        ])
        Rendered = SimpleNamespace(Blocks={
            (0, 0, 0): {"Name": "minecraft:lever", "Properties": {"powered": "false"}},
            (4, 0, 1): {"Name": "minecraft:redstone_lamp", "Properties": {"lit": "false"}},
        })
        Fixture = BuildFabricFixture(
            RoutedDesign=Routed,
            Rendered=Rendered,
            Module=ModuleIR(Name="Top"),
        )

        self.assertEqual(Fixture["Inputs"][0]["LeverPosition"], [0, 0, 0])
        self.assertEqual(Fixture["Outputs"][0]["LampPosition"], [4, 0, 1])
        self.assertEqual(Fixture["SchemaVersion"], 2)
        self.assertEqual(Fixture["Trace"]["Circuit"], "Top")
        self.assertEqual(
            [Gate["Name"] for Gate in Fixture["Trace"]["Gates"]],
            ["InputA", "OutputY"],
        )

    def testFixtureCarriesRenderedSignTextIntoTheServerPaste(self) -> None:
        Routed = SimpleNamespace(PlacedGates=[])
        Rendered = SimpleNamespace(
            Blocks={(2, 3, 4): {"Name": "minecraft:oak_sign"}},
            Signs=[((2, 3, 4), "OUT result")],
        )

        Fixture = BuildFabricFixture(
            RoutedDesign=Routed,
            Rendered=Rendered,
            Module=ModuleIR(Name="Top"),
        )

        self.assertEqual(Fixture["Signs"], [{
            "Position": [2, 3, 4],
            "FrontText": ["OUT result", "", "", ""],
            "BackText": ["OUT result", "", "", ""],
        }])

    def testExpectedVectorsUseLogicOnlyAsAnOracle(self) -> None:
        Module = ModuleIR(
            Name="Top",
            Inputs=["a"],
            Outputs=["y$Output"],
            Gates=[
                Gate("InputA", GateKind.INPUT, ["a"]),
                Gate("OutputY", GateKind.OUTPUT, ["y$Output"], ["a"]),
            ],
        )
        Vectors = BuildExpectedVectors(Module, ["a"], ["y$Output"])
        self.assertEqual(Vectors[0]["Expected"], {"y$Output": False})
        self.assertEqual(Vectors[1]["Expected"], {"y$Output": True})

    def testExpectedVectorsMaterializeOneShotPortIterables(self) -> None:
        Module = ModuleIR(
            Name="Top",
            Inputs=["a"],
            Outputs=["y$Output"],
            Gates=[
                Gate("InputA", GateKind.INPUT, ["a"]),
                Gate("OutputY", GateKind.OUTPUT, ["y$Output"], ["a"]),
            ],
        )
        Vectors = BuildExpectedVectors(
            Module,
            (Name for Name in ["a"]),
            (Name for Name in ["y$Output"]),
        )
        self.assertEqual(
            [Vector["Expected"] for Vector in Vectors],
            [{"y$Output": False}, {"y$Output": True}],
        )

    def testExpectedVectorsCanRetainEveryInternalSignalForFailureTracing(self) -> None:
        Module = ModuleIR(
            Name="Top",
            Inputs=["a", "b"],
            Outputs=["y"],
            Gates=[
                Gate("InputA", GateKind.INPUT, ["a"]),
                Gate("InputB", GateKind.INPUT, ["b"]),
                Gate("Nand0", GateKind.NAND, ["n1"], ["a", "b"]),
                Gate("OutputY", GateKind.OUTPUT, ["y"], ["n1"]),
            ],
        )

        Vectors = BuildExpectedVectors(
            Module,
            ["a", "b"],
            ["y"],
            IncludeTraceValues=True,
        )

        self.assertEqual(
            Vectors[0]["ExpectedSignals"],
            {"a": False, "b": False, "n1": True, "y": True},
        )

    def testFailureTraceFindsTheCausalSubcircuitAndExactWorldBlock(self) -> None:
        Fixture = {
            "TopModule": "Top",
            "Trace": {
                "Circuit": "Top",
                "Gates": [
                    {
                        "Name": "OutputY",
                        "Kind": "OUTPUT",
                        "CircuitPath": ["Top", "OutputY"],
                        "Inputs": ["n1"],
                        "Outputs": ["y"],
                        "OutputProbePosition": [4, 0, 1],
                    },
                    {
                        "Name": "Nand0",
                        "Kind": "NAND",
                        "CircuitPath": ["Top", "Nand0"],
                        "Inputs": ["a", "b"],
                        "Outputs": ["n1"],
                        "ProbePositions": [[2, 0, 0]],
                        "OutputProbePosition": [2, 0, 0],
                    },
                ],
                "Signals": [
                    {"Name": "a", "ProbePositions": [[0, 0, 0]]},
                    {"Name": "b", "ProbePositions": [[1, 0, 0]]},
                    {"Name": "n1", "ProbePositions": [[2, 0, 0], [3, 0, 0]]},
                ],
            },
        }
        Diagnostics = {
            "Mismatch": {
                "Output": "y",
                "Expected": True,
                "Actual": False,
                "Inputs": {"a": False, "b": False},
                "ExpectedSignals": {
                    "a": False,
                    "b": False,
                    "n1": True,
                    "y": True,
                },
                "TestedVectorsBeforeFailure": 0,
            },
            "TraceBlocks": [
                {
                    "Position": [0, 0, 0],
                    "WorldPosition": [0, 64, 0],
                    "State": {"Name": "minecraft:lever", "Properties": {"powered": "false"}},
                },
                {
                    "Position": [1, 0, 0],
                    "WorldPosition": [1, 64, 0],
                    "State": {"Name": "minecraft:lever", "Properties": {"powered": "false"}},
                },
                {
                    "Position": [2, 0, 0],
                    "WorldPosition": [2, 64, 0],
                    "State": {"Name": "minecraft:redstone_wire", "Properties": {"power": "0"}},
                },
                {
                    "Position": [3, 0, 0],
                    "WorldPosition": [3, 64, 0],
                    "State": {"Name": "minecraft:repeater", "Properties": {"powered": "false"}},
                },
                {
                    "Position": [4, 0, 1],
                    "WorldPosition": [4, 64, 1],
                    "State": {"Name": "minecraft:redstone_lamp", "Properties": {"lit": "false"}},
                },
            ],
        }

        Trace = BuildFabricFailureTrace(Fixture, Diagnostics)

        self.assertEqual(
            [Entry["Gate"] for Entry in Trace["SubcircuitTrace"]],
            ["OutputY", "Nand0"],
        )
        self.assertEqual(Trace["FirstFailingSubcircuit"]["Gate"], "Nand0")
        self.assertEqual(Trace["FirstFailingSubcircuit"]["Signal"], "n1")
        self.assertEqual(
            Trace["SubcircuitTrace"][1]["PhysicalBlocks"][0]["Powered"],
            False,
        )
        self.assertEqual(Trace["FirstFailingBlock"]["FixturePosition"], [2, 0, 0])
        self.assertEqual(Trace["FirstFailingBlock"]["WorldPosition"], [2, 64, 0])

    def testFailureTraceRetainsSettleTimeoutIdentityAndBlockEvidence(self) -> None:
        Fixture = {
            "TopModule": "Top",
            "Trace": {
                "Circuit": "Top",
                "Gates": [{
                    "Name": "OutputY",
                    "Kind": "OUTPUT",
                    "CircuitPath": ["Top", "OutputY"],
                    "Inputs": ["n1"],
                    "Outputs": ["y"],
                    "ProbePositions": [[4, 0, 1]],
                    "OutputProbePosition": [4, 0, 1],
                }],
                "Signals": [{
                    "Name": "y",
                    "ProducerGate": "OutputY",
                    "ConsumerGates": [],
                    "ProbePositions": [[4, 0, 1]],
                }],
            },
        }
        Diagnostics = {
            "Timeout": {
                "Reason": "redstone-network-did-not-settle",
                "Output": "y",
                "Expected": True,
                "Actual": False,
                "Inputs": {"a": True},
                "ExpectedSignals": {"n1": True, "y": True},
                "TestedVectorsBeforeFailure": 3,
                "GlobalVectorIndex": 3,
                "ElapsedTicks": 200,
                "ObservedUnchangedTicks": 0,
            },
            "TraceBlocks": [{
                "Position": [4, 0, 1],
                "WorldPosition": [4, 64, 1],
                "State": {
                    "Name": "minecraft:redstone_lamp",
                    "Properties": {"lit": "false"},
                },
            }],
        }

        Trace = BuildFabricFailureTrace(Fixture, Diagnostics)

        self.assertEqual(Trace["FailureKind"], "timeout")
        self.assertEqual(Trace["FailedOutput"], "y")
        self.assertEqual(Trace["TestedVectorsBeforeFailure"], 3)
        self.assertEqual(Trace["GlobalVectorIndex"], 3)
        self.assertNotIn("ValidationLaneIndex", Trace)
        self.assertNotIn("ValidationStackIndex", Trace)
        self.assertNotIn("ValidationVerticalIndex", Trace)
        self.assertNotIn("ValidationLaneOrigin", Trace)
        self.assertEqual(Trace["FirstFailingSubcircuit"]["Gate"], "OutputY")
        self.assertEqual(Trace["FirstFailingBlock"]["FixturePosition"], [4, 0, 1])
        self.assertEqual(Trace["FirstFailingBlock"]["WorldPosition"], [4, 64, 1])

    def testImportedLitematicLabelsRecoverPhysicalTestPorts(self) -> None:
        Template = CellTemplate(
            Size=(16, 3, 16),
            Blocks={
                (1, 1, 1): {"Name": "minecraft:lever"},
                (10, 1, 1): {"Name": "minecraft:lever"},
                (4, 1, 12): {"Name": "minecraft:redstone_lamp"},
                (12, 1, 12): {"Name": "minecraft:redstone_lamp"},
            },
        )

        Inputs, Outputs = InferLitematicPorts(Template, [
            ((2, 1, 1), "IN", "A"),
            ((9, 1, 1), "IN", "B"),
            ((3, 1, 12), "OUT", "Sum"),
            ((11, 1, 12), "OUT", "Carry"),
        ])

        self.assertEqual(Inputs, [
            {"Name": "A", "LeverPosition": [1, 1, 1]},
            {"Name": "B", "LeverPosition": [10, 1, 1]},
        ])
        self.assertEqual(Outputs, [
            {"Name": "Carry", "LampPosition": [12, 1, 12]},
            {"Name": "Sum", "LampPosition": [4, 1, 12]},
        ])

    def testImportedLitematicPortInferenceRejectsAmbiguousAndDuplicateLabels(self) -> None:
        Template = CellTemplate(
            Size=(4, 2, 4),
            Blocks={
                (0, 1, 1): {"Name": "minecraft:lever"},
                (2, 1, 1): {"Name": "minecraft:lever"},
            },
        )

        with self.subTest(Case="ambiguous"):
            with self.assertRaisesRegex(ValueError, "ambiguous"):
                InferLitematicPorts(Template, [((1, 1, 1), "IN", "A")])
        with self.subTest(Case="duplicate"):
            with self.assertRaisesRegex(ValueError, "duplicate IN label"):
                InferLitematicPorts(Template, [
                    ((0, 1, 1), "IN", "A"),
                    ((2, 1, 1), "IN", "A"),
                ])

    def testImportedSchematicVectorsRequireRealPortsAndNandOracle(self) -> None:
        with TemporaryDirectory() as TemporaryDirectoryPath:
            Root = Path(TemporaryDirectoryPath)
            FixturePath = Root / "Top.FabricFixture.json"
            FixturePath.write_text(
                '{"Blocks":[],"Inputs":[{"Name":"a","LeverPosition":[0,0,0]}],"Outputs":[{"Name":"y","LampPosition":[1,0,0]}]}',
                encoding="utf-8",
            )
            NandPath = Root / "Top.Nand.json"
            NandPath.write_text(
                '{"Module":"Top","Inputs":["a"],"Outputs":["y"],"Gates":[{"Name":"InputA","Kind":"INPUT","Inputs":[],"Outputs":["a"]},{"Name":"OutputY","Kind":"OUTPUT","Inputs":["a"],"Outputs":["y"]}]}',
                encoding="utf-8",
            )

            Artifact, Fixture = ReadFabricFixture(FixturePath)
            Module = ReadNandModule(NandPath)
            Vectors = BuildImportedSchematicVectors(Fixture, Module)

        self.assertEqual(Artifact.InputCount, 1)
        self.assertEqual(Artifact.OutputCount, 1)
        self.assertEqual(
            Vectors,
            [
                {"Inputs": {"a": False}, "Expected": {"y": False}},
                {"Inputs": {"a": True}, "Expected": {"y": True}},
            ],
        )

    def testSystemVerilogSourceCanBeTheExistingWorldOracle(self) -> None:
        with TemporaryDirectory() as TemporaryDirectoryPath:
            SourcePath = Path(TemporaryDirectoryPath) / "Top.sv"
            SourcePath.write_text(
                "module Top(input logic a, output logic y); assign y = ~a; endmodule\n",
                encoding="utf-8",
            )

            Module = ReadSvModule(SourcePath)
            Vectors = BuildImportedSchematicVectors({
                "Blocks": [],
                "Inputs": [{"Name": "a", "LeverPosition": [0, 0, 0]}],
                "Outputs": [{"Name": "y", "LampPosition": [1, 0, 0]}],
            }, Module)

        self.assertEqual(
            [Vector["Expected"] for Vector in Vectors],
            [{"y": True}, {"y": False}],
        )

    def testImportedSchematicVectorsRejectAPortlessFixture(self) -> None:
        Module = ModuleIR(Name="Top", Inputs=["a"], Outputs=["y"])

        with self.assertRaisesRegex(ValueError, "no testable inputs"):
            BuildImportedSchematicVectors(
                {"Blocks": [], "Inputs": [], "Outputs": []},
                Module,
            )

    def testImportedSchematicVectorsRejectAnOraclePortMismatch(self) -> None:
        Module = ModuleIR(Name="Top", Inputs=["a"], Outputs=["y"])

        with self.subTest(Case="input"):
            with self.assertRaisesRegex(ValueError, "fixture inputs do not match"):
                BuildImportedSchematicVectors({
                    "Blocks": [],
                    "Inputs": [{"Name": "wrong", "LeverPosition": [0, 0, 0]}],
                    "Outputs": [{"Name": "y", "LampPosition": [1, 0, 0]}],
                }, Module)
        with self.subTest(Case="output"):
            with self.assertRaisesRegex(ValueError, "not produced by the logic oracle"):
                BuildImportedSchematicVectors({
                    "Blocks": [],
                    "Inputs": [{"Name": "a", "LeverPosition": [0, 0, 0]}],
                    "Outputs": [{"Name": "wrong", "LampPosition": [1, 0, 0]}],
                }, Module)

    def testInputLeverUsesItsIndicatorLampWithoutAFloatingBackingBlock(self) -> None:
        Routed = SimpleNamespace(
            PlacedGates=[SimpleNamespace(
                Name="InputA",
                Kind="INPUT",
                Outputs=["a"],
                OutputPin=(0, 0, 2),
                OutputDirection=(0, 0, 1),
                X=0,
                Y=0,
                Z=0,
                Rotation=0,
                MirrorX=False,
            )],
            NetWires={},
            Supports=[],
            Repeaters={},
            RepeaterInputFacings={},
        )
        Rendered = BuildLitematicBlockMap(Routed)

        self.assertNotIn((0, 0, -1), Rendered.Blocks)
        self.assertEqual(
            Rendered.Blocks[(0, 0, 0)]["Properties"]["facing"],
            "north",
        )
        self.assertEqual(
            Rendered.Blocks[(0, 0, 1)]["Name"],
            "minecraft:redstone_lamp",
        )

    def testDynamicBlocksAreEmittedWithoutPredictedPower(self) -> None:
        Cases = (
            (
                {
                    "Name": "minecraft:repeater",
                    "Properties": {"powered": "true", "facing": "north"},
                },
                "powered",
                "false",
            ),
            (
                {
                    "Name": "minecraft:redstone_wall_torch",
                    "Properties": {"lit": "true", "facing": "south"},
                },
                "lit",
                "false",
            ),
            (
                {
                    "Name": "minecraft:redstone_wire",
                    "Properties": {"power": "15", "north": "side"},
                },
                "power",
                "0",
            ),
        )

        for State, Property, Expected in Cases:
            with self.subTest(State=State["Name"]):
                Neutral = NeutralDynamicState(State)
                self.assertEqual(Neutral["Properties"][Property], Expected)

    def testWorldControlForwardsBlockStateRequestsAndReturnsObservations(self) -> None:
        with TemporaryDirectory() as TemporaryDirectoryPath:
            Root = Path(TemporaryDirectoryPath)
            (Root / "config").mkdir()
            (Root / "config" / "redstonecompiler-harness.json").write_text(
                '{"Token":"fixture-token","Port":25566}',
                encoding="utf-8",
            )
            Supervisor = FabricServerSupervisor(FabricServerConfiguration(Root=Root))
            with patch.object(
                Supervisor,
                "_Request",
                return_value={
                    "Status": "observed",
                    "Blocks": [{
                        "Position": [1, 64, 2],
                        "State": {"Name": "minecraft:redstone_block"},
                    }],
                    "Diagnostics": {"ObservedBlockCount": 1},
                },
            ) as Request:
                Result = Supervisor.ControlRunningServer(
                    Action="WorldReadBlocks",
                    WorldPositions=[[1, 64, 2]],
                )

        self.assertEqual(Result.Status, "observed")
        self.assertEqual(Result.Diagnostics["ObservedBlockCount"], 1)
        self.assertEqual(Result.Diagnostics["Blocks"][0]["State"]["Name"], "minecraft:redstone_block")
        self.assertEqual(
            Request.call_args.args[2],
            {"Action": "WorldReadBlocks", "Positions": [[1, 64, 2]]},
        )


if __name__ == "__main__":
    unittest.main()
