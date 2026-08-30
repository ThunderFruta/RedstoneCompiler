"""No-flag prompt contracts for every executable script entry point."""

from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from Scripts.Fabric import ControlFabricServer, ImportSchemToFabricServer
from Scripts.Routing import (
    CaptureRoutingDesignSnapshot,
    RunFreeroutingBenchmark,
    RunRouterAcceptance,
)


class ScriptCliGuidanceTests(unittest.TestCase):
    def testFabricControlGuidesToExplicitAction(self) -> None:
        with patch("builtins.input", side_effect=["", "4", "12"]):
            Arguments = ControlFabricServer.GuidedArguments()
        self.assertEqual(
            Arguments,
            ["--server-root", ".runtime/fabric-26.2", "--step", "12"],
        )

    def testClearCollectsEveryImportedFixtureBound(self) -> None:
        with TemporaryDirectory() as TemporaryDirectoryPath:
            Fixtures = Path(TemporaryDirectoryPath) / "fixtures"
            Fixtures.mkdir()
            (Fixtures / "bounded.FabricFixture.json").write_text(json.dumps({
                "Arena": {
                    "Origin": [10, 64, -2],
                    "Bounds": {"Minimum": [0, 0, 0], "Maximum": [2, 1, 4]},
                },
                "Blocks": [],
            }))
            (Fixtures / "legacy.FabricFixture.json").write_text(json.dumps({
                "Arena": {"Origin": [0, 0, 0]},
                "Blocks": [
                    {"Position": [-1, 2, 3]},
                    {"Position": [4, 0, 9]},
                ],
            }))
            Regions = ControlFabricServer.BuildClearRegions(Path(TemporaryDirectoryPath))

        self.assertEqual(Regions[0]["Minimum"], [10, 64, -2])
        self.assertEqual(Regions[0]["Maximum"], [12, 65, 2])
        self.assertEqual(Regions[1]["Minimum"], [-1, 0, 3])
        self.assertEqual(Regions[1]["Maximum"], [4, 2, 9])

    def testFabricImportGuidesToHotReload(self) -> None:
        with patch("builtins.input", side_effect=["build.schem", "", "", ""]):
            Arguments = ImportSchemToFabricServer.GuidedArguments()
        self.assertEqual(
            Arguments,
            [
                "build.schem", "--server-root", ".runtime/fabric-26.2",
                "--origin", "0", "64", "0", "--replace",
            ],
        )

    def testRoutingGuidesDefaultToSafePreviews(self) -> None:
        with patch("builtins.input", side_effect=[""]):
            self.assertEqual(RunRouterAcceptance.GuidedArguments(), ["--dry-run"])
        with patch("builtins.input", side_effect=[""]), redirect_stdout(StringIO()):
            self.assertEqual(RunFreeroutingBenchmark.Main([]), 0)

    def testSnapshotGuideCollectsExplicitInputPaths(self) -> None:
        with patch("builtins.input", side_effect=["failure.json", "", "manifest.json", "diagram.json", ""]):
            Arguments = CaptureRoutingDesignSnapshot.GuidedArguments()
        self.assertEqual(
            Arguments,
            [
                "--cla4-failure", "failure.json",
                "--output-root", "Output/DesignSnapshots/RoutingAwarePlacementAccess",
                "--acceptance-manifest", "manifest.json",
                "--artifact", "diagram.json",
            ],
        )


if __name__ == "__main__":
    unittest.main()
