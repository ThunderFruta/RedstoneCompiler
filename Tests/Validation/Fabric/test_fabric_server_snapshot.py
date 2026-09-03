"""Authoritative server-state litematic snapshot contracts."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

from Validation.Fabric.ServerSnapshot import CaptureServerUpdatedLitematic, IterFixtureWorldPositionBatches
from Validation.Fabric.SchemImport import ReadLitematicIoLabels
from PhysicalDesign.Rendering.SchemWriter import LoadTemplate


class FabricServerSnapshotTests(unittest.TestCase):
    def testSnapshotUsesLiveDynamicStatesAndPreservesFixtureBounds(self) -> None:
        Fixture = {
            "Blocks": [],
            "Inputs": [],
            "Arena": {
                "Origin": [10, 70, -4],
                "Bounds": {"Minimum": [0, 0, 0], "Maximum": [3, 0, 0]},
            },
        }
        Supervisor = Mock()
        Supervisor.ControlRunningServer.return_value = SimpleNamespace(
            Status="observed",
            Diagnostics={
                "ObservedGameTime": 41,
                "Blocks": [
                    {
                        "Position": [10, 70, -4],
                        "State": {"Name": "minecraft:oak_sign"},
                    },
                    {
                        "Position": [11, 70, -4],
                        "State": {
                            "Name": "minecraft:redstone_wire",
                            "Properties": {
                                "power": "15",
                                "north": "side",
                                "south": "none",
                            },
                        },
                    },
                    {
                        "Position": [12, 70, -4],
                        "State": {
                            "Name": "minecraft:redstone_lamp",
                            "Properties": {"lit": "true"},
                        },
                    },
                    {
                        "Position": [13, 70, -4],
                        "State": {"Name": "minecraft:air"},
                    },
                ],
            },
        )
        with TemporaryDirectory() as TemporaryDirectoryPath:
            OutputPath = Path(TemporaryDirectoryPath) / "Top.ServerUpdated.litematic"
            with patch(
                "Validation.Fabric.ServerSnapshot.ReadLitematicIoLabels",
                return_value=[((0, 0, 0), "IN", "a")],
            ):
                Artifact = CaptureServerUpdatedLitematic(
                    Supervisor=Supervisor,
                    Fixture=Fixture,
                    SourcePath=Path(TemporaryDirectoryPath) / "Top.litematic",
                    OutputPath=OutputPath,
                )

            Template = LoadTemplate(OutputPath)
            Labels = ReadLitematicIoLabels(OutputPath)

        self.assertEqual(Artifact.RequestedPositionCount, 4)
        self.assertEqual(Artifact.ObservedBlockCount, 3)
        self.assertEqual(Artifact.WorldReadRequests, 2)
        self.assertEqual(Artifact.InputCountSetToZero, 0)
        self.assertEqual(Artifact.SnapshotReadPasses, 2)
        self.assertIsNone(Artifact.InputZeroGameTime)
        self.assertEqual(Artifact.FirstObservedGameTime, 41)
        self.assertEqual(Artifact.LastObservedGameTime, 41)
        self.assertEqual(Template.Size, (4, 1, 1))
        self.assertNotIn((3, 0, 0), Template.Blocks)
        self.assertEqual(
            Template.Blocks[(1, 0, 0)]["Properties"]["power"],
            "15",
        )
        self.assertEqual(
            Template.Blocks[(2, 0, 0)]["Properties"]["lit"],
            "true",
        )
        self.assertEqual(Labels, [((0, 0, 0), "IN", "a")])
        self.assertEqual(
            Supervisor.ControlRunningServer.call_args.kwargs,
            {
                "Action": "WorldReadBlocks",
                "WorldPositions": [
                    [10, 70, -4],
                    [11, 70, -4],
                    [12, 70, -4],
                    [13, 70, -4],
                ],
            },
        )

    def testSnapshotRestoresNormalizedSignsToNegativeFixtureCoordinates(self) -> None:
        Fixture = {
            "Blocks": [],
            "Inputs": [],
            "Arena": {
                "Origin": [10, 70, -4],
                "Bounds": {
                    "Minimum": [-2, 0, -5],
                    "Maximum": [-1, 0, -4],
                },
            },
        }
        ObservedBlocks = [
            {
                "Position": [8, 70, -9],
                "State": {"Name": "minecraft:oak_sign"},
            },
            {
                "Position": [9, 70, -9],
                "State": {"Name": "minecraft:stone"},
            },
            {
                "Position": [8, 70, -8],
                "State": {"Name": "minecraft:air"},
            },
            {
                "Position": [9, 70, -8],
                "State": {"Name": "minecraft:air"},
            },
        ]
        Supervisor = Mock()
        Supervisor.ControlRunningServer.return_value = SimpleNamespace(
            Status="observed",
            Diagnostics={"Blocks": ObservedBlocks, "ObservedGameTime": 41},
        )
        with TemporaryDirectory() as TemporaryDirectoryPath:
            OutputPath = Path(TemporaryDirectoryPath) / "Top.ServerUpdated.litematic"
            with patch(
                "Validation.Fabric.ServerSnapshot.ReadLitematicIoLabels",
                return_value=[((0, 0, 0), "IN", "a")],
            ):
                CaptureServerUpdatedLitematic(
                    Supervisor=Supervisor,
                    Fixture=Fixture,
                    SourcePath=Path(TemporaryDirectoryPath) / "Top.litematic",
                    OutputPath=OutputPath,
                )

            Labels = ReadLitematicIoLabels(OutputPath)

        self.assertEqual(Labels, [((0, 0, 0), "IN", "a")])

    def testSnapshotFailsClosedWhenAWorldReadOmitsAPosition(self) -> None:
        Fixture = {
            "Blocks": [],
            "Inputs": [],
            "Arena": {
                "Origin": [0, 64, 0],
                "Bounds": {"Minimum": [0, 0, 0], "Maximum": [1, 0, 0]},
            },
        }
        Supervisor = Mock()
        Supervisor.ControlRunningServer.return_value = SimpleNamespace(
            Status="observed",
            Diagnostics={
                "Blocks": [{
                    "Position": [0, 64, 0],
                    "State": {"Name": "minecraft:stone"},
                }],
            },
        )
        with TemporaryDirectory() as TemporaryDirectoryPath:
            with self.assertRaisesRegex(RuntimeError, "omitted requested"):
                CaptureServerUpdatedLitematic(
                    Supervisor=Supervisor,
                    Fixture=Fixture,
                    SourcePath=Path(TemporaryDirectoryPath) / "Top.schem",
                    OutputPath=Path(TemporaryDirectoryPath) / "Top.ServerUpdated.litematic",
                )

    def testSnapshotForcesEveryInputLeverLowBeforeCapturing(self) -> None:
        Fixture = {
            "Blocks": [],
            "Inputs": [
                {"Name": "a", "LeverPosition": [0, 0, 0]},
                {"Name": "b", "LeverPosition": [1, 0, 0]},
            ],
            "Arena": {
                "Origin": [10, 70, -4],
                "Bounds": {"Minimum": [0, 0, 0], "Maximum": [1, 0, 0]},
            },
        }
        PoweredStates = [
            {
                "Position": [10, 70, -4],
                "State": {
                    "Name": "minecraft:lever",
                    "Properties": {
                        "face": "wall",
                        "facing": "north",
                        "powered": "true",
                    },
                },
            },
            {
                "Position": [11, 70, -4],
                "State": {
                    "Name": "minecraft:lever",
                    "Properties": {
                        "face": "wall",
                        "facing": "north",
                        "powered": "false",
                    },
                },
            },
        ]
        ZeroedStates = [
            {
                **Block,
                "State": {
                    **Block["State"],
                    "Properties": {
                        **Block["State"]["Properties"],
                        "powered": "false",
                    },
                },
            }
            for Block in PoweredStates
        ]
        Supervisor = Mock()
        Supervisor.ControlRunningServer.side_effect = [
            SimpleNamespace(
                Status="observed",
                Diagnostics={"Blocks": PoweredStates, "ObservedGameTime": 10},
            ),
            SimpleNamespace(
                Status="updated",
                Diagnostics={"UpdatedBlockCount": 2, "ObservedGameTime": 10},
            ),
            SimpleNamespace(
                Status="observed",
                Diagnostics={"Blocks": ZeroedStates, "ObservedGameTime": 60},
            ),
            SimpleNamespace(
                Status="observed",
                Diagnostics={"Blocks": ZeroedStates, "ObservedGameTime": 61},
            ),
        ]
        with TemporaryDirectory() as TemporaryDirectoryPath:
            OutputPath = Path(TemporaryDirectoryPath) / "Top.ServerUpdated.litematic"
            Artifact = CaptureServerUpdatedLitematic(
                Supervisor=Supervisor,
                Fixture=Fixture,
                SourcePath=Path(TemporaryDirectoryPath) / "Top.schem",
                OutputPath=OutputPath,
            )
            Template = LoadTemplate(OutputPath)

        self.assertEqual(Artifact.InputCountSetToZero, 2)
        self.assertEqual(Artifact.SnapshotReadPasses, 2)
        self.assertEqual(Artifact.WorldReadRequests, 3)
        self.assertEqual(Artifact.InputZeroGameTime, 10)
        self.assertEqual(
            Template.Blocks[(0, 0, 0)]["Properties"]["powered"],
            "false",
        )
        self.assertEqual(
            Template.Blocks[(1, 0, 0)]["Properties"]["powered"],
            "false",
        )
        self.assertEqual(
            Supervisor.ControlRunningServer.call_args_list[1].kwargs,
            {
                "Action": "WorldSetBlocks",
                "WorldBlocks": [
                    {
                        "Position": [10, 70, -4],
                        "State": "minecraft:lever[face=wall,facing=north,powered=false]",
                    },
                    {
                        "Position": [11, 70, -4],
                        "State": "minecraft:lever[face=wall,facing=north,powered=false]",
                    },
                ],
            },
        )

    def testSnapshotSplitsWorldReadsAtTheHarnessLimit(self) -> None:
        Batches = list(IterFixtureWorldPositionBatches(
            (0, 64, 0),
            ((0, 0, 0), (10_000, 0, 0)),
        ))

        self.assertEqual([len(Batch) for Batch in Batches], [10_000, 1])
        self.assertEqual(Batches[0][0], [0, 64, 0])
        self.assertEqual(Batches[1][0], [10_000, 64, 0])


if __name__ == "__main__":
    unittest.main()
