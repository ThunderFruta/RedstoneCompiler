"""R5 graph identity checked against independently written ordered adjacency."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from itertools import permutations
import json

import pytest

from Compilation.Ir.ComponentGraph import BuildComponentGraph
from Compilation.Ir.Models import Gate, GateKind, ModuleIR, NetIR
from PhysicalDesign.Routing.Regions.TopologyIdentity import (
    NormalizedRegionNode, NormalizedRegionTopology, NormalizeRegionTopology,
    RegionSignalReference, RegionTopologyError,
)


# Facts are (ordered external inputs, ordered gate/input-edge rows, outputs).
# These tables predate normalization and define each graph independently.
Reconvergent = (("A", "B", "C"), (
    ("N0", ("A", "B")), ("N1", ("A", "C")), ("Y", ("N0", "N1")),
), ("Y",))
MultiOutput = (("A",), (("T", ("A", "A")), ("Z", ("T", "T"))), ("T", "Z"))


def BuildExtracted(Facts, *, Rename=None, Reverse=False, Maximum=18):
    """Instantiate public IR, then select the exact real extracted membership."""
    Inputs, Rows, Outputs = Facts
    Rename = Rename or {Signal: Signal for Signal in (*Inputs, *(Name for Name, _ in Rows))}
    Gates = [Gate(f"Gate{Index}", GateKind.NAND, [Rename[Name]], [Rename[S] for S in Edges])
             for Index, (Name, Edges) in enumerate(Rows)]
    Members = frozenset(G.Name for G in Gates)
    Gates += [Gate(f"Export{Index}", GateKind.OUTPUT, [], [Rename[Signal]])
              for Index, Signal in enumerate(Outputs)]
    if Reverse:
        for Index, Value in enumerate(Gates):
            Value.Name = f"RenamedGate{len(Gates) - Index}"
        Members = frozenset(Value.Name for Value in Gates if Value.Kind == GateKind.NAND)
        Gates.reverse()
    Module = ModuleIR("IndependentGraph", Inputs=[Rename[S] for S in Inputs],
                      Outputs=[Rename[S] for S in Outputs], Gates=Gates)
    Graph = BuildComponentGraph(Module, MaximumComponentGates=Maximum)
    Matches = [C for C in Graph.Components if frozenset(C.GateNames) == Members]
    assert len(Matches) == 1, "fixture must exercise its intended real extracted region"
    return Module, Matches[0]


def NormalizeFacts(Facts, **Options):
    Module, Component = BuildExtracted(Facts, **Options)
    return NormalizeRegionTopology(Module, Component, OrderedInputSignals=Module.Inputs,
                                   OrderedOutputSignals=Module.Outputs)


def EquivalentByBijection(Left, Right):
    """Independent exhaustive isomorphism oracle for small labelled graphs.

This neither traverses roots to assign canonical numbers nor hashes anything.
It tries every node bijection and checks every ordered edge and external role.
"""
    LeftInputs, LeftRows, LeftOutputs = Left
    RightInputs, RightRows, RightOutputs = Right
    if (len(LeftInputs), len(LeftRows), len(LeftOutputs)) != (
        len(RightInputs), len(RightRows), len(RightOutputs),
    ):
        return False
    assert len(LeftRows) <= 6, "oracle itself has an explicit small domain"
    LeftNodes = dict(LeftRows)
    RightNodes = dict(RightRows)
    for Targets in permutations(RightNodes):
        Mapping = dict(zip(LeftInputs, RightInputs))
        Mapping.update(zip(LeftNodes, Targets))
        if tuple(Mapping[S] for S in LeftOutputs) != tuple(RightOutputs):
            continue
        if all(tuple(Mapping[S] for S in Edges) == tuple(RightNodes[Mapping[Name]])
               for Name, Edges in LeftNodes.items()):
            return True
    return False


def PayloadFacts(Value):
    """Read inspectable public edges, independent of fingerprint/implementation."""
    def Signal(Reference):
        return ("External" if Reference.Source == "input" else "Vertex") + str(Reference.Index)
    return (
        tuple(f"External{Index}" for Index in range(Value.InputCount)),
        tuple((f"Vertex{Index}", tuple(Signal(Edge) for Edge in Node.Inputs))
              for Index, Node in enumerate(Value.Nodes)),
        tuple(Signal(Output) for Output in Value.Outputs),
    )


# Same kind/arity counts and same signal fanouts can still have different roles.
DegreeTwin = (("A", "B", "C"), (
    ("N0", ("B", "A")), ("N1", ("A", "C")), ("Y", ("N0", "N1")),
), ("Y",))
NoSharedBranch = (("A", "B", "C"), (
    ("N0", ("A", "B")), ("N1", ("A", "C")), ("Y", ("N0", "N0")),
), ("Y", "N1"))
SameDegreesSeparate = (("A", "B", "C"), (
    ("N0", ("A", "B")), ("N1", ("A", "C")),
    ("N2", ("N0", "N0")), ("N3", ("N1", "N1")), ("Y", ("N2", "N3")),
), ("Y",))
SameDegreesCrossed = (("A", "B", "C"), (
    ("N0", ("A", "B")), ("N1", ("A", "C")),
    ("N2", ("N0", "N1")), ("N3", ("N0", "N1")), ("Y", ("N2", "N3")),
), ("Y",))


@pytest.mark.parametrize("Left,Right,Expected", [
    (Reconvergent, Reconvergent, True),
    (Reconvergent, (tuple(reversed(Reconvergent[0])), Reconvergent[1], Reconvergent[2]), False),
    (Reconvergent, DegreeTwin, False),
    (SameDegreesSeparate, SameDegreesCrossed, False),
    (MultiOutput, (MultiOutput[0], MultiOutput[1], ("Z", "T")), False),
    (MultiOutput, (("A",), (("T", ("A", "A")), ("Z", ("A", "A"))), ("T", "Z")), False),
    (NoSharedBranch, (NoSharedBranch[0], (
        ("N0", ("A", "B")), ("N1", ("A", "C")), ("Y", ("N0", "N1")),
    ), NoSharedBranch[2]), False),
])
def test_identity_agrees_with_independent_role_and_adjacency_oracle(Left, Right, Expected, tmp_path):
    Oracle = EquivalentByBijection(Left, Right)
    assert Oracle is Expected
    First, Second = NormalizeFacts(Left), NormalizeFacts(Right)
    Observed = First == Second
    Record = {"Left": Left, "Right": Right, "Expected": Expected, "Oracle": Oracle,
              "Observed": Observed, "LeftPayload": First.ToDictionary(),
              "RightPayload": Second.ToDictionary(), "LeftFingerprint": First.Fingerprint,
              "RightFingerprint": Second.Fingerprint}
    (tmp_path / "Oracle.json").write_text(json.dumps(Record, indent=2) + "\n")
    assert EquivalentByBijection(Left, PayloadFacts(First))
    assert EquivalentByBijection(Right, PayloadFacts(Second))
    assert Observed is Oracle
    assert (First.Fingerprint == Second.Fingerprint) is Oracle


def test_name_and_enumeration_permutations_preserve_roles_and_complete_graph(tmp_path):
    First = NormalizeFacts(Reconvergent)
    Signals = (*Reconvergent[0], *(Name for Name, _ in Reconvergent[1]))
    Records = []
    for Offset in range(len(Signals)):
        Renames = {Signal: f"Wire{(len(Signals) - Index + Offset) % len(Signals)}"
                   for Index, Signal in enumerate(Signals)}
        Second = NormalizeFacts(Reconvergent, Rename=Renames, Reverse=bool(Offset % 2))
        assert EquivalentByBijection(Reconvergent, PayloadFacts(Second))
        assert Second == First and Second.Fingerprint == First.Fingerprint
        Records.append({"Rename": Renames, "Reverse": bool(Offset % 2),
                        "ExpectedEquivalent": True, "ObservedEquivalent": Second == First,
                        "Payload": Second.ToDictionary()})
    (tmp_path / "Permutations.json").write_text(json.dumps(Records, indent=2) + "\n")


def test_repeated_edges_and_exported_internal_signal_are_observable():
    Value = NormalizeFacts(MultiOutput)
    # A is used at both input roles of T. T is exported and used twice by Z.
    assert EquivalentByBijection(MultiOutput, PayloadFacts(Value))
    assert Value.Nodes[0].Inputs == (RegionSignalReference("input", 0),) * 2
    assert Value.Nodes[1].Inputs == (Value.Outputs[0],) * 2
    assert Value.Outputs == (RegionSignalReference("node", 0), RegionSignalReference("node", 1))


def test_minimum_maximum_and_overmaximum_use_real_extraction():
    for Count in (1, 18, 19):
        Facts = (("A",), tuple((f"N{Index}", (("A" if Index == 0 else f"N{Index-1}"),) * 2)
                              for Index in range(Count)), (f"N{Count-1}",))
        Module, Component = BuildExtracted(Facts, Maximum=20)
        if Count == 19:
            with pytest.raises(RegionTopologyError) as Error:
                NormalizeRegionTopology(Module, Component, OrderedInputSignals=Module.Inputs,
                                        OrderedOutputSignals=Module.Outputs)
            assert Error.value.Code == "UnsupportedDomain"
        else:
            Value = NormalizeRegionTopology(Module, Component, OrderedInputSignals=Module.Inputs,
                                            OrderedOutputSignals=Module.Outputs)
            # Independent chain walk checks all 18 gates; no factorial oracle here.
            Current = Value.Outputs[0]
            Visited = set()
            while Current.Source == "node":
                assert Current.Index not in Visited
                Visited.add(Current.Index)
                Edges = Value.Nodes[Current.Index].Inputs
                assert Edges[0] == Edges[1]
                Current = Edges[0]
            assert Current == RegionSignalReference("input", 0)
            assert len(Visited) == Count


@pytest.mark.parametrize("Change,Code", [
    ("kind", "UnsupportedDomain"), ("arity", "UnsupportedDomain"),
    ("attrs", "UnsupportedDomain"), ("multi_gate_output", "UnsupportedDomain"),
    ("bus", "UnsupportedDomain"), ("constant", "UnsupportedDomain"),
    ("duplicate_gate", "MalformedSource"), ("duplicate_driver", "AmbiguousDriver"),
    ("missing_driver", "MissingDriver"), ("missing_role", "BoundaryMismatch"),
    ("duplicate_role", "MalformedSource"), ("direction", "BoundaryMismatch"),
    ("count", "BoundaryMismatch"), ("capacity", "BoundaryMismatch"),
    ("internal_signals", "BoundaryMismatch"), ("hidden_export", "BoundaryMismatch"),
])
def test_malformed_source_or_boundary_cannot_produce_identity(Change, Code):
    Module, Component = BuildExtracted(MultiOutput)
    Inputs, Outputs = Module.Inputs, Module.Outputs
    First = Module.Gates[0]
    if Change == "kind": First.Kind = GateKind.OR
    elif Change == "arity": First.Inputs.pop()
    elif Change == "attrs": First.Attrs["SemanticMode"] = "unknown"
    elif Change == "multi_gate_output": First.Outputs.append("Another")
    elif Change == "bus": Module.Nets["A"] = NetIR("A", Width=2)
    elif Change == "constant": Module.Nets["A"] = NetIR("A", IsConstant=True, Value=1)
    elif Change == "duplicate_gate": Module.Gates.append(deepcopy(First))
    elif Change == "duplicate_driver": Module.Gates.append(Gate("Other", GateKind.NAND, ["T"], ["A", "A"]))
    elif Change == "missing_driver": Module.Inputs = []
    elif Change == "missing_role": Inputs = []
    elif Change == "duplicate_role": Inputs = ["A", "A"]
    elif Change == "direction": Component = replace(Component, InputPorts=(replace(Component.InputPorts[0], Direction="output"),))
    elif Change == "count": Component = replace(Component, InputPorts=(replace(Component.InputPorts[0], ExternalTerminalCount=99),))
    elif Change == "capacity": Component = replace(Component, OutputPorts=(replace(Component.OutputPorts[0], Capacity=99), *Component.OutputPorts[1:]))
    elif Change == "internal_signals": Component = replace(Component, InternalSignals=("bogus",))
    elif Change == "hidden_export": Module.Gates = [G for G in Module.Gates if G.Name != "Export0"]
    with pytest.raises(RegionTopologyError) as Error:
        NormalizeRegionTopology(Module, Component, OrderedInputSignals=Inputs, OrderedOutputSignals=Outputs)
    assert Error.value.Code == Code


def test_cycles_are_rejected_even_if_extractor_returns_a_component():
    Facts = (("A",), (("T", ("Z", "A")), ("Z", ("T", "A"))), ("Z",))
    Module, Component = BuildExtracted(Facts)
    with pytest.raises(RegionTopologyError) as Error:
        NormalizeRegionTopology(Module, Component, OrderedInputSignals=Module.Inputs,
                                OrderedOutputSignals=Module.Outputs)
    assert Error.value.Code == "UnsupportedDomain"


def test_payload_and_fingerprint_cannot_alias_mutable_inputs_or_accept_forged_hashes():
    Module, Component = BuildExtracted(MultiOutput)
    Value = NormalizeRegionTopology(Module, Component, OrderedInputSignals=Module.Inputs,
                                    OrderedOutputSignals=Module.Outputs)
    Before = Value.ToDictionary()
    Module.Gates[0].Inputs.reverse()
    Module.Gates[0].Inputs[0] = "Modified"
    Document = Value.ToDictionary()
    Document["Nodes"][0]["Inputs"].clear()
    assert Value.ToDictionary() == Before
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        Value.Fingerprint = "0" * 64
    with pytest.raises(TypeError):
        replace(Value, Fingerprint="0" * 64)
    with pytest.raises(RegionTopologyError):
        NormalizedRegionNode([RegionSignalReference("input", 0)] * 2)
    with pytest.raises(RegionTopologyError):
        replace(Value, Nodes=tuple(reversed(Value.Nodes)))
    with pytest.raises(RegionTopologyError):
        replace(Value, Outputs=(RegionSignalReference("node", 99),))
    with pytest.raises(RegionTopologyError):
        replace(Value, InputCount=True)
    class MutableLabel:
        def __eq__(self, Other):
            return True
    with pytest.raises(RegionTopologyError):
        RegionSignalReference(MutableLabel(), 0)
    with pytest.raises(RegionTopologyError):
        replace(Value.Nodes[0], Kind=MutableLabel())


def test_coarse_component_fingerprint_is_not_an_equivalence_or_boundary_authority():
    FirstModule, FirstComponent = BuildExtracted(Reconvergent)
    OtherModule, OtherComponent = BuildExtracted(DegreeTwin)
    OtherComponent = replace(OtherComponent, StructuralFingerprint=FirstComponent.StructuralFingerprint)
    First = NormalizeRegionTopology(FirstModule, FirstComponent, OrderedInputSignals=FirstModule.Inputs,
                                    OrderedOutputSignals=FirstModule.Outputs)
    Other = NormalizeRegionTopology(OtherModule, OtherComponent, OrderedInputSignals=OtherModule.Inputs,
                                    OrderedOutputSignals=OtherModule.Outputs)
    assert not EquivalentByBijection(Reconvergent, DegreeTwin)
    assert First != Other


def test_equal_coarse_hash_and_edge_degree_multisets_do_not_hide_changed_reconvergence():
    LeftModule, LeftComponent = BuildExtracted(SameDegreesSeparate)
    RightModule, RightComponent = BuildExtracted(SameDegreesCrossed)
    assert LeftComponent.StructuralFingerprint == RightComponent.StructuralFingerprint
    def TerminalDegrees(Facts):
        return sorted(sum(Signal == Edge for _, Edges in Facts[1] for Edge in Edges)
                      for Signal in (*Facts[0], *(Name for Name, _ in Facts[1])))
    assert TerminalDegrees(SameDegreesSeparate) == TerminalDegrees(SameDegreesCrossed)
    assert not EquivalentByBijection(SameDegreesSeparate, SameDegreesCrossed)
    Left = NormalizeRegionTopology(LeftModule, LeftComponent, OrderedInputSignals=LeftModule.Inputs,
                                   OrderedOutputSignals=LeftModule.Outputs)
    Right = NormalizeRegionTopology(RightModule, RightComponent, OrderedInputSignals=RightModule.Inputs,
                                    OrderedOutputSignals=RightModule.Outputs)
    assert Left != Right
