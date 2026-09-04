"""Deterministic coverage for the synthesis-to-placement component graph."""

from pathlib import Path

from Compilation.Ir.ComponentGraph import BuildComponentGraph
from Compilation.Ir.Models import Gate, ModuleIR
from Compilation.Synthesis.LogicOptimization import OptimizeLogic
from Compilation.Synthesis.NandTransform import ToNandOnly
from Formats.SystemVerilog.Sv import ParseSvToNetlist


def _Example(ModuleName: str) -> ModuleIR:
    Parsed = ParseSvToNetlist(
        InputPath=Path("Assets/Examples") / f"{ModuleName}.sv",
        TopModule=ModuleName,
    )
    NandOnly = ToNandOnly(OptimizeLogic(Parsed))
    return NandOnly.Modules[NandOnly.Top]


def _RenameAndReverse(Module: ModuleIR) -> ModuleIR:
    Signals = sorted({
        str(Signal)
        for GateValue in Module.Gates
        for Signal in (*GateValue.Inputs, *GateValue.Outputs)
    })
    Renames = {
        Signal: f"StructuralSignal{Index}"
        for Index, Signal in enumerate(reversed(Signals))
    }
    Gates = [
        Gate(
            Name=f"StructuralGate{Index}",
            Kind=GateValue.Kind,
            Inputs=[
                Renames[str(Signal)]
                for Signal in GateValue.Inputs
            ],
            Outputs=[
                Renames[str(Signal)]
                for Signal in GateValue.Outputs
            ],
            Attrs=dict(GateValue.Attrs),
        )
        for Index, GateValue in enumerate(reversed(Module.Gates))
    ]
    return ModuleIR(
        Name="RenamedStructuralModule",
        Inputs=[Renames[str(Signal)] for Signal in Module.Inputs],
        Outputs=[Renames[str(Signal)] for Signal in Module.Outputs],
        Gates=Gates,
    )


def test_partition_is_rename_and_gate_order_invariant():
    Original = BuildComponentGraph(_Example("CarryLookaheadAdder4"))
    Renamed = BuildComponentGraph(
        _RenameAndReverse(_Example("CarryLookaheadAdder4"))
    )

    assert Original.StructuralFingerprint == Renamed.StructuralFingerprint
    assert tuple(
        Component.StructuralFingerprint
        for Component in Original.Components
    ) == tuple(
        Component.StructuralFingerprint
        for Component in Renamed.Components
    )
    ChannelKey = lambda Value: (
        -1 if Value[0] is None else Value[0],
        Value[1],
        Value[2],
    )
    assert tuple(sorted((
        (
            Channel.SourceComponentId,
            Channel.TargetComponentIds,
            Channel.Capacity,
        )
        for Channel in Original.Channels
    ), key=ChannelKey)) == tuple(sorted((
        (
            Channel.SourceComponentId,
            Channel.TargetComponentIds,
            Channel.Capacity,
        )
        for Channel in Renamed.Channels
    ), key=ChannelKey))


def test_current_arithmetic_examples_select_hierarchy_structurally():
    Expected = {
        "FullAdder": (False, 0),
        "RippleCarryAdder4": (False, 0),
        "RippleCarryAdder8": (False, 0),
        "CarryLookaheadAdder4": (True, 8),
    }

    for ModuleName, (
        ExpectedHierarchical,
        ExpectedReconvergentCuts,
    ) in Expected.items():
        Graph = BuildComponentGraph(_Example(ModuleName))
        assert Graph.Hierarchical is ExpectedHierarchical
        assert (
            Graph.QualifyingReconvergentCutCount
            == ExpectedReconvergentCuts
        )
        assert all(
            not Channel.FeedthroughComponentIds
            for Channel in Graph.Channels
        )


def test_component_ownership_and_channel_endpoints_are_closed():
    Graph = BuildComponentGraph(_Example("CarryLookaheadAdder4"))
    GateOwners = dict(Graph.GateToComponent)

    assert len(GateOwners) == sum(
        len(Component.GateNames)
        for Component in Graph.Components
    )
    assert set(GateOwners.values()) == set(range(len(Graph.Components)))
    for Channel in Graph.Channels:
        assert Channel.SourceComponentId not in Channel.TargetComponentIds
        assert Channel.Capacity >= max(1, len(Channel.TargetComponentIds))
