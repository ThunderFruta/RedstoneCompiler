#!/usr/bin/env python3
"""Build four isolated Java lever -> repeater -> lamp orientation lanes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from types import SimpleNamespace

RepositoryRoot = Path(__file__).resolve().parents[2]
if str(RepositoryRoot) not in sys.path:
    sys.path.insert(0, str(RepositoryRoot))

from PhysicalDesign.Redstone.Technology import OppositeHorizontalFacing, RepeaterInputDelta, RepeaterOutputDelta
from PhysicalDesign.Rendering.SchemWriter import BuildRepeaterOrientationAudit, WriteLitematic


Position = tuple[int, int, int]


def Add(First: Position, Second: Position) -> Position:
    return tuple(First[Axis] + Second[Axis] for Axis in range(3))


def BuildSmokeBlockMap() -> tuple[
    dict[Position, dict[str, object]],
    dict[Position, dict[str, object]],
    dict[str, dict[str, Position]],
]:
    """Return four separated lanes and their orientation intents."""
    Blocks: dict[Position, dict[str, object]] = {}
    Expected: dict[Position, dict[str, object]] = {}
    Lanes: dict[str, dict[str, Position]] = {}
    for Index, InputFacing in enumerate(
        ("west", "east", "north", "south")
    ):
        Repeater = (Index * 8, 1, 0)
        Lever = Add(Repeater, RepeaterInputDelta(InputFacing))
        Lamp = Add(Repeater, RepeaterOutputDelta(InputFacing))
        OutputFacing = OppositeHorizontalFacing(InputFacing)
        Blocks[Lever] = {
            "Name": "minecraft:lever",
            "Properties": {
                "face": "floor",
                "facing": OutputFacing,
                "powered": "false",
            },
        }
        Blocks[Repeater] = {
            "Name": "minecraft:repeater",
            "Properties": {
                "delay": "1",
                "facing": InputFacing,
                "locked": "false",
                "powered": "false",
            },
        }
        Blocks[Lamp] = {
            "Name": "minecraft:redstone_lamp",
            "Properties": {"lit": "false"},
        }
        for PositionValue in (Lever, Repeater, Lamp):
            Blocks[(PositionValue[0], 0, PositionValue[2])] = {
                "Name": "minecraft:smooth_stone"
            }
        LaneName = f"Flow{OutputFacing.title()}"
        Expected[Repeater] = {
            "Source": "Route",
            "Provenance": "RouteRefresh",
            "Gate": None,
            "Role": "FourDirectionSmokeLane",
            "Signal": LaneName,
            "InputFacing": InputFacing,
        }
        Lanes[LaneName] = {
            "Lever": Lever,
            "Repeater": Repeater,
            "Lamp": Lamp,
        }
    return Blocks, Expected, Lanes


def ValidateSmokeLanes(
    Blocks: dict[Position, dict[str, object]],
    Lanes: dict[str, dict[str, Position]],
) -> None:
    """Verify each lane's physical input-to-output repeater orientation."""
    for LaneName, Lane in Lanes.items():
        Repeater = Blocks[Lane["Repeater"]]
        InputFacing = str(Repeater["Properties"]["facing"])
        if (
            Add(Lane["Repeater"], RepeaterInputDelta(InputFacing))
            != Lane["Lever"]
            or Add(Lane["Repeater"], RepeaterOutputDelta(InputFacing))
            != Lane["Lamp"]
        ):
            raise ValueError(
                f"orientation smoke lane {LaneName} failed: "
                f"input-facing={InputFacing}"
            )


def BuildSmokeLitematic(OutputPath: Path) -> dict[str, object]:
    """Validate and write the four-direction smoke litematic."""
    Blocks, Expected, Lanes = BuildSmokeBlockMap()
    ValidateSmokeLanes(Blocks, Lanes)
    Orientation = BuildRepeaterOrientationAudit(Blocks, Expected)
    Build = SimpleNamespace(
        Blocks=Blocks,
        Signs=[],
        RepeaterOrientation=Orientation,
    )
    WriteLitematic(None, OutputPath, Build=Build)
    return {
        "Artifact": str(OutputPath.resolve()),
        "Contract": Orientation["Contract"],
        "LaneCount": len(Lanes),
        "Lanes": {
            Name: {
                Key: list(Value)
                for Key, Value in Lane.items()
            }
            for Name, Lane in Lanes.items()
        },
        "RepeaterOrientation": Orientation,
        "AutomatedSubsetValidationPassed": True,
        "JavaEditionManualValidationRequired": True,
    }


def Main() -> int:
    Parser = argparse.ArgumentParser(description=__doc__)
    Parser.add_argument(
        "--output",
        type=Path,
        default=Path("Output/RepeaterOrientationSmoke.litematic"),
    )
    Arguments = Parser.parse_args()
    Report = BuildSmokeLitematic(Arguments.output)
    print(json.dumps(Report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(Main())
