"""Safety tests for the ignored canonical Fabric runtime manager."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, call, patch
import unittest

from Compiler.FabricServer import DefaultFabricServerRoot
from PhysicalDesign.Rendering.SchemWriter import EncodePayload, EncodeString, NbtValue


RuntimeScripts = DefaultFabricServerRoot() / "PyScripts"
RuntimeProcessPath = RuntimeScripts / "Process.py"
RuntimeAnvilPath = RuntimeScripts / "Anvil.py"


def LoadRuntimeModule(Name: str, PathValue: Path) -> ModuleType:
    """Load one local runtime module without invoking server lifecycle actions."""
    ModuleSpecification = importlib.util.spec_from_file_location(
        Name,
        PathValue,
    )
    if ModuleSpecification is None or ModuleSpecification.loader is None:
        raise RuntimeError(f"could not load runtime module: {PathValue}")
    Module = importlib.util.module_from_spec(ModuleSpecification)
    PreviousModule = sys.modules.get(Name)
    sys.modules[Name] = Module
    sys.path.insert(0, str(RuntimeScripts))
    try:
        ModuleSpecification.loader.exec_module(Module)
    finally:
        sys.path.remove(str(RuntimeScripts))
        if PreviousModule is None:
            sys.modules.pop(Name, None)
        else:
            sys.modules[Name] = PreviousModule
    return Module


def PackAnvilSectionStates(States: list[int], PaletteSize: int) -> list[int]:
    """Encode states with Minecraft 26.2's padded SimpleBitStorage layout."""
    BitsPerValue = max(4, (PaletteSize - 1).bit_length())
    ValuesPerLong = 64 // BitsPerValue
    Packed = []
    for StartIndex in range(0, len(States), ValuesPerLong):
        Value = 0
        for LocalIndex, State in enumerate(
            States[StartIndex:StartIndex + ValuesPerLong],
        ):
            Value |= State << (LocalIndex * BitsPerValue)
        Packed.append(Value if Value < (1 << 63) else Value - (1 << 64))
    return Packed


def WriteSyntheticRegionChunk(
    RegionPath: Path,
    *,
    RegionX: int,
    RegionZ: int,
    LocalIndex: int,
    SectionY: int,
    PaletteNames: list[str],
    States: list[int],
) -> None:
    """Write one compressed synthetic Anvil chunk for scanner unit tests only."""
    import zlib

    ChunkX = RegionX * 32 + LocalIndex % 32
    ChunkZ = RegionZ * 32 + LocalIndex // 32
    BlockStates: dict[str, NbtValue] = {
        "palette": NbtValue(9, (10, [
            {"Name": NbtValue(8, Name)}
            for Name in PaletteNames
        ])),
    }
    if len(PaletteNames) > 1:
        BlockStates["data"] = NbtValue(
            12,
            PackAnvilSectionStates(States, len(PaletteNames)),
        )
    Root = {
        "xPos": NbtValue(3, ChunkX),
        "zPos": NbtValue(3, ChunkZ),
        "sections": NbtValue(9, (10, [{
            "Y": NbtValue(1, SectionY),
            "block_states": NbtValue(10, BlockStates),
        }])),
    }
    NbtData = b"\x0a" + EncodeString("") + EncodePayload(10, Root)
    Compressed = zlib.compress(NbtData)
    ChunkPayload = (len(Compressed) + 1).to_bytes(4, "big") + b"\x02" + Compressed
    SectorCount = (len(ChunkPayload) + 4095) // 4096
    Region = bytearray((2 + SectorCount) * 4096)
    Region[LocalIndex * 4:(LocalIndex + 1) * 4] = (
        (2 << 8) | SectorCount
    ).to_bytes(4, "big")
    Start = 2 * 4096
    Region[Start:Start + len(ChunkPayload)] = ChunkPayload
    RegionPath.parent.mkdir(parents=True, exist_ok=True)
    RegionPath.write_bytes(Region)


@unittest.skipUnless(
    RuntimeProcessPath.is_file() and RuntimeAnvilPath.is_file(),
    "the ignored canonical Fabric runtime manager is not installed",
)
class FabricServerRuntimeManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.Process = LoadRuntimeModule(
            "RedstoneCompilerFabricRuntimeProcessTests",
            RuntimeProcessPath,
        )
        cls.Anvil = LoadRuntimeModule(
            "RedstoneCompilerFabricRuntimeAnvilTests",
            RuntimeAnvilPath,
        )

    def testHarnessConfigurationDefaultsToOneThousandTps(self) -> None:
        with TemporaryDirectory() as TemporaryDirectoryPath:
            ConfigurationPath = (
                Path(TemporaryDirectoryPath) / "redstonecompiler-harness.json"
            )
            with patch.object(
                self.Process,
                "HarnessConfigurationPath",
                ConfigurationPath,
            ), patch.object(
                self.Process.secrets,
                "token_hex",
                return_value="private-token",
            ):
                self.Process.WriteHarnessConfiguration(25566)

            Configuration = json.loads(
                ConfigurationPath.read_text(encoding="utf-8"),
            )

        self.assertEqual(Configuration["RequestedTickRate"], 1000.0)
        self.assertEqual(Configuration["SettleTimeoutTicks"], 200)
        self.assertNotIn("ValidationLanesPerStack", Configuration)
        self.assertNotIn("MaximumValidationStackCount", Configuration)

    def testCurrentStatusRecoversAUserServiceOwner(self) -> None:
        WriteManagerState = Mock()
        with patch.object(
            self.Process,
            "ReadManagerState",
            return_value=None,
        ), patch.object(
            self.Process,
            "ProcessIsOwned",
            return_value=False,
        ), patch.object(
            self.Process,
            "FindOwnedServerPid",
            return_value=3456,
        ), patch.object(
            self.Process,
            "ServiceMainPid",
            return_value=3456,
        ), patch.object(
            self.Process,
            "WriteManagerState",
            WriteManagerState,
        ):
            Status = self.Process.CurrentStatus()

        self.assertEqual(Status["Status"], "running")
        self.assertEqual(Status["Pid"], 3456)
        RecoveredState = WriteManagerState.call_args.args[0]
        self.assertEqual(RecoveredState["LaunchMethod"], "user-service")

    def testCurrentStatusKeepsAnUnmanagedRecoveredProcessDistinct(self) -> None:
        WriteManagerState = Mock()
        with patch.object(
            self.Process,
            "ReadManagerState",
            return_value=None,
        ), patch.object(
            self.Process,
            "ProcessIsOwned",
            return_value=False,
        ), patch.object(
            self.Process,
            "FindOwnedServerPid",
            return_value=3456,
        ), patch.object(
            self.Process,
            "ServiceMainPid",
            return_value=7890,
        ), patch.object(
            self.Process,
            "WriteManagerState",
            WriteManagerState,
        ):
            self.Process.CurrentStatus()

        RecoveredState = WriteManagerState.call_args.args[0]
        self.assertEqual(RecoveredState["LaunchMethod"], "recovered")

    def testAnvilScannerFindsOnlyNonAirBlocksUsingPaddedStorage(self) -> None:
        with TemporaryDirectory() as TemporaryDirectoryPath:
            RegionPath = Path(TemporaryDirectoryPath) / "r.-1.2.mca"
            States = [0] * 4096
            for Index, State in {
                0: 1,
                15: 2,
                16: 1,
                255: 2,
                256: 1,
                4095: 2,
            }.items():
                States[Index] = State
            WriteSyntheticRegionChunk(
                RegionPath,
                RegionX=-1,
                RegionZ=2,
                LocalIndex=33,
                SectionY=4,
                PaletteNames=[
                    "minecraft:air",
                    "minecraft:stone",
                    "minecraft:redstone_wire",
                ],
                States=States,
            )

            Chunks = list(self.Anvil.ReadRegionNonAirBlocks(RegionPath))

        self.assertEqual(len(Chunks), 1)
        self.assertEqual((Chunks[0].ChunkX, Chunks[0].ChunkZ), (-31, 65))
        self.assertEqual(
            Chunks[0].Positions,
            (
                (-496, 64, 1040),
                (-481, 64, 1040),
                (-496, 64, 1041),
                (-481, 64, 1055),
                (-496, 65, 1040),
                (-481, 79, 1055),
            ),
        )

    def testClearServerWorldKeepsTheRunningServerAndClearsOnlySavedNonAirBlocks(self) -> None:
        with TemporaryDirectory() as TemporaryDirectoryPath:
            RuntimeRoot = Path(TemporaryDirectoryPath)
            WorldPath = RuntimeRoot / "world"
            WorldPath.mkdir()
            LevelPath = WorldPath / "level.dat"
            LevelPath.write_bytes(b"same-world")
            RegionPath = (
                WorldPath
                / "dimensions"
                / "minecraft"
                / "overworld"
                / "region"
                / "r.0.-1.mca"
            )
            RegionPath.parent.mkdir(parents=True)
            Header = bytearray(4096)
            for Index in (0, 33):
                Header[Index * 4:(Index + 1) * 4] = (0x101).to_bytes(4, "big")
            RegionPath.write_bytes(Header)
            OriginalRegionBytes = RegionPath.read_bytes()
            Current = Mock(return_value={
                "Status": "running",
                "Pid": 1234,
                "ServerRoot": str(RuntimeRoot),
            })
            Ready = Mock(return_value=(True, None))
            Start = Mock()
            Send = Mock(side_effect=[
                {"Status": "paused"},
                {"Status": "command-complete"},
                {"Status": "command-complete"},
                {
                    "Status": "updated",
                    "Diagnostics": {"UpdatedBlockCount": 2},
                },
                {"Status": "command-complete"},
                {"Status": "command-complete"},
                {"Status": "resumed"},
            ])
            ReadNonAirBlocks = Mock(return_value=iter([
                SimpleNamespace(
                    ChunkX=0,
                    ChunkZ=-32,
                    Positions=((0, 64, -512), (1, 64, -512)),
                ),
                SimpleNamespace(ChunkX=1, ChunkZ=-31, Positions=()),
            ]))

            with patch.object(self.Process, "RuntimeRoot", RuntimeRoot), patch.object(
                self.Process,
                "WorldPath",
                WorldPath,
            ), patch.object(
                self.Process,
                "CurrentStatus",
                Current,
            ), patch.object(
                self.Process,
                "RunningHarnessReady",
                Ready,
            ), patch.object(
                self.Process,
                "StartServer",
                Start,
            ), patch.object(
                self.Process,
                "SendRequest",
                Send,
            ), patch.object(
                self.Process,
                "ReadRegionNonAirBlocks",
                ReadNonAirBlocks,
            ):
                Result = self.Process.ClearServerWorld(StartupTimeoutSeconds=12.5)

            self.assertEqual(LevelPath.read_bytes(), b"same-world")
            self.assertEqual(RegionPath.read_bytes(), OriginalRegionBytes)
            Current.assert_called_once_with()
            Ready.assert_called_once_with()
            Start.assert_not_called()
            ReadNonAirBlocks.assert_called_once_with(RegionPath)
            self.assertEqual(Send.call_args_list, [
                call({
                    "Action": "PauseTicks",
                }, TimeoutSeconds=60.0),
                call({
                    "Action": "WorldRunCommand",
                    "Command": "save-off",
                }, TimeoutSeconds=60.0),
                call({
                    "Action": "WorldRunCommand",
                    "Command": "save-all flush",
                }, TimeoutSeconds=60.0),
                call({
                    "Action": "WorldSetBlocks",
                    "Blocks": [
                        {"Position": [0, 64, -512], "State": "minecraft:air"},
                        {"Position": [1, 64, -512], "State": "minecraft:air"},
                    ],
                }, TimeoutSeconds=60.0),
                call({
                    "Action": "WorldRunCommand",
                    "Command": "save-on",
                }, TimeoutSeconds=60.0),
                call({
                    "Action": "WorldRunCommand",
                    "Command": "save-all flush",
                }, TimeoutSeconds=60.0),
                call({
                    "Action": "ResumeTicks",
                }, TimeoutSeconds=60.0),
            ])
            self.assertEqual(Result["Status"], "running")
            self.assertTrue(Result["Cleared"])
            self.assertEqual(Result["ClearMode"], "live-persisted-overworld-blocks")
            self.assertEqual(Result["ClearedChunkCount"], 1)
            self.assertEqual(Result["ClearedNonAirBlocks"], 2)
            self.assertEqual(Result["ClearRequestCount"], 1)
            self.assertEqual(Result["ScannedChunkCount"], 2)
            self.assertEqual(Result["ScannedRegionFileCount"], 1)
            self.assertTrue(Result["TicksPausedDuringClear"])
            self.assertTrue(Result["WorldSavingSuppressedDuringScan"])
            self.assertTrue(Result["ClearedStateFlushed"])
            self.assertFalse(Result["Restarted"])

    def testClearPersistedWorldBlocksRestoresServerStateAfterScannerFailure(self) -> None:
        RegionPath = Path("/synthetic/world/region/r.-4.22.mca")
        Send = Mock(side_effect=[
            {"Status": "paused"},
            {"Status": "command-complete"},
            {"Status": "command-complete"},
            {"Status": "command-complete"},
            {"Status": "command-complete"},
            {"Status": "resumed"},
        ])

        with patch.object(
            self.Process,
            "PersistedWorldRegionsByDimension",
            return_value={"minecraft:overworld": [RegionPath]},
        ), patch.object(
            self.Process,
            "ReadRegionNonAirBlocks",
            side_effect=RuntimeError("header/NBT race"),
        ), patch.object(
            self.Process,
            "SendRequest",
            Send,
        ):
            with self.assertRaisesRegex(RuntimeError, "header/NBT race"):
                self.Process.ClearPersistedWorldBlocks()

        self.assertEqual(Send.call_args_list, [
            call({"Action": "PauseTicks"}, TimeoutSeconds=60.0),
            call({
                "Action": "WorldRunCommand",
                "Command": "save-off",
            }, TimeoutSeconds=60.0),
            call({
                "Action": "WorldRunCommand",
                "Command": "save-all flush",
            }, TimeoutSeconds=60.0),
            call({
                "Action": "WorldRunCommand",
                "Command": "save-on",
            }, TimeoutSeconds=60.0),
            call({
                "Action": "WorldRunCommand",
                "Command": "save-all flush",
            }, TimeoutSeconds=60.0),
            call({"Action": "ResumeTicks"}, TimeoutSeconds=60.0),
        ])

    def testClearServerWorldRefusesToCreateAMissingWorld(self) -> None:
        with TemporaryDirectory() as TemporaryDirectoryPath:
            RuntimeRoot = Path(TemporaryDirectoryPath)
            WorldPath = RuntimeRoot / "world"
            (RuntimeRoot / "fixtures").mkdir()
            Start = Mock()

            with patch.object(self.Process, "RuntimeRoot", RuntimeRoot), patch.object(
                self.Process,
                "WorldPath",
                WorldPath,
            ), patch.object(
                self.Process,
                "StartServer",
                Start,
            ):
                with self.assertRaisesRegex(RuntimeError, "create or replace world path"):
                    self.Process.ClearServerWorld()

            Start.assert_not_called()

    def testClearServerWorldRejectsAnyOtherRuntimePath(self) -> None:
        with TemporaryDirectory() as TemporaryDirectoryPath:
            RuntimeRoot = Path(TemporaryDirectoryPath)
            OtherPath = RuntimeRoot / "fixtures"
            OtherPath.mkdir()
            MarkerPath = OtherPath / "keep.json"
            MarkerPath.write_text("keep", encoding="utf-8")

            with patch.object(self.Process, "RuntimeRoot", RuntimeRoot), patch.object(
                self.Process,
                "WorldPath",
                OtherPath,
            ):
                with self.assertRaisesRegex(RuntimeError, "unexpected world path"):
                    self.Process.ClearServerWorld()

            self.assertEqual(MarkerPath.read_text(encoding="utf-8"), "keep")
