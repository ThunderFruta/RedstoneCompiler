"""Renderer projection tests for the shared dust-stair geometry decision."""

from PhysicalDesign.Rendering.Renderer import BuildWireState


Lower = (0, 0, 0)
Upper = (1, 1, 0)
LowerSupport = (0, -1, 0)
UpperSupport = (1, 0, 0)
Headroom = (0, 1, 0)
Backing = (-1, 1, 0)
NetCells = {Lower, Upper}


def _ClearBlocks():
    return {
        LowerSupport: {"Name": "minecraft:smooth_stone"},
        UpperSupport: {"Name": "minecraft:smooth_stone"},
        Backing: {"Name": "minecraft:smooth_stone"},
    }


def _Arms(Blocks):
    LowerState = BuildWireState(Lower, NetCells, Blocks, 0)
    UpperState = BuildWireState(Upper, NetCells, Blocks, 0)
    return (
        LowerState["Properties"]["east"],
        UpperState["Properties"]["west"],
    )


def test_renderer_preserves_p0_and_w1_arms_but_blocks_n0():
    assert _Arms(_ClearBlocks()) == ("up", "side")

    W1Blocks = {
        **_ClearBlocks(),
        Headroom: {
            "Name": "minecraft:redstone_wall_torch",
            "Properties": {"facing": "east", "lit": "true"},
        },
    }
    assert _Arms(W1Blocks) == ("up", "side")

    N0Blocks = {
        **_ClearBlocks(),
        Headroom: {"Name": "minecraft:smooth_stone"},
    }
    assert _Arms(N0Blocks) == ("none", "none")


def test_renderer_does_not_generalize_novel_electrical_headroom():
    Unsupported = {
        **_ClearBlocks(),
        Headroom: {
            "Name": "minecraft:redstone_wall_torch",
            "Properties": {"facing": "west", "lit": "true"},
        },
    }
    assert _Arms(Unsupported) == ("none", "none")
