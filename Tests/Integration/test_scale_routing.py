"""Opt-in public-outcome routing checks for representative scale designs."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile

import pytest

from Formats.SystemVerilog.Sv import ParseSvToNetlist
from PhysicalDesign.Orchestration.Runner import PlaceAndRoutePcb
from Compilation.Synthesis.LogicOptimization import OptimizeLogic
from Compilation.Synthesis.NandTransform import ToNandOnly
from PhysicalDesign.Policy import RoutingStrategy


RUN_SCALE_TESTS = os.environ.get("RC_RUN_SCALE_TESTS", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


@pytest.mark.skipif(
    not RUN_SCALE_TESTS,
    reason="set RC_RUN_SCALE_TESTS=1 to run the routed scale tests",
)
@pytest.mark.parametrize(
    "ExampleName",
    (
        "RippleCarryAdder4.sv",
        "RippleCarryAdder8.sv",
        "CarryLookaheadAdder4.sv",
    ),
)
def test_example_routes_with_final_physical_legality(ExampleName: str) -> None:
    with tempfile.TemporaryDirectory() as Workdir:
        Netlist = ParseSvToNetlist(
            InputPath=Path("Assets/Examples") / ExampleName,
            TopModule=None,
            Workdir=Path(Workdir),
        )
        Physical = PlaceAndRoutePcb(
            ToNandOnly(OptimizeLogic(Netlist)),
            Strategy=RoutingStrategy.Default,
        )

    assert Physical.Routed.GlobalPlan is not None
    assert not Physical.Routed.GlobalPlan.ResourceOverflow
    assert Physical.Routed.ZeroResourceConflicts
    assert Physical.Routed.RoutingAssignment is not None
    assert Physical.Routed.NetWires
