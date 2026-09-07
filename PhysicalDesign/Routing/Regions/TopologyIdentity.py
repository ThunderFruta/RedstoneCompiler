"""Immutable identity for bounded extracted regions of ordered scalar NANDs.

This is an induced logical graph contract, not a partitioning algorithm or a
physical reuse permission. Every supported node reaches an ordered output root.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import ClassVar, Sequence

from Compilation.Ir.ComponentGraph import ComponentPort, TopologyComponent
from Compilation.Ir.Models import Gate, GateKind, ModuleIR


class RegionTopologyError(ValueError):
    """Unsupported or malformed input; Code is stable, message wording is not."""

    def __init__(self, Code: str, Detail: str):
        self.Code = Code
        super().__init__(f"{Code}: {Detail}")


def _Require(Condition: bool, Code: str, Detail: str) -> None:
    if not Condition:
        raise RegionTopologyError(Code, Detail)


@dataclass(frozen=True, slots=True)
class RegionSignalReference:
    """One input role or the sole output of a canonical NAND node."""

    Source: str
    Index: int

    def __post_init__(self) -> None:
        _Require(
            type(self.Source) is str and self.Source in ("input", "node")
            and type(self.Index) is int and self.Index >= 0,
            "MalformedPayload", "invalid signal reference",
        )


@dataclass(frozen=True, slots=True)
class NormalizedRegionNode:
    """Input tuple positions are semantic Input0 and Input1 roles."""

    Inputs: tuple[RegionSignalReference, RegionSignalReference]
    Kind: str = "NAND"

    def __post_init__(self) -> None:
        _Require(
            type(self.Inputs) is tuple and len(self.Inputs) == 2
            and all(type(Value) is RegionSignalReference for Value in self.Inputs)
            and type(self.Kind) is str and self.Kind == "NAND",
            "MalformedPayload", "expected an immutable two-input NAND",
        )
        for Value in self.Inputs:
            Value.__post_init__()


@dataclass(frozen=True, slots=True)
class NormalizedRegionTopology:
    """Inspectable canonical graph; fingerprints are computed, never supplied.

Nodes use first-visit numbering from ordered output roots, visiting Input0
before Input1. Repeated references retain sharing and terminal multiplicity.
Boundary capacities and external consumer counts are deliberately separate.
"""

    InputCount: int
    Nodes: tuple[NormalizedRegionNode, ...]
    Outputs: tuple[RegionSignalReference, ...]
    SchemaVersion: ClassVar[str] = "ordered-nand-region-v1"
    MaximumGates: ClassVar[int] = 18

    def __post_init__(self) -> None:
        self.Validate()

    def Validate(self) -> None:
        """Validate even a directly constructed payload before comparing it."""
        _Require(
            type(self.InputCount) is int and 1 <= self.InputCount <= 36
            and type(self.Nodes) is tuple and 1 <= len(self.Nodes) <= 18
            and all(type(Node) is NormalizedRegionNode for Node in self.Nodes)
            and type(self.Outputs) is tuple and 1 <= len(self.Outputs) <= 18
            and all(type(Value) is RegionSignalReference for Value in self.Outputs),
            "MalformedPayload", "invalid bounded graph payload",
        )
        Seen: set[int] = set()
        Active: set[int] = set()
        Inputs: set[int] = set()

        def Visit(Value: RegionSignalReference) -> None:
            Value.__post_init__()
            if Value.Source == "input":
                _Require(Value.Index < self.InputCount, "MalformedPayload", "input range")
                Inputs.add(Value.Index)
                return
            _Require(Value.Index < len(self.Nodes), "MalformedPayload", "node range")
            _Require(Value.Index not in Active, "MalformedPayload", "cycle")
            if Value.Index in Seen:
                return
            _Require(Value.Index == len(Seen), "MalformedPayload", "noncanonical node order")
            Seen.add(Value.Index)
            Active.add(Value.Index)
            Node = self.Nodes[Value.Index]
            Node.__post_init__()
            for Input in Node.Inputs:
                Visit(Input)
            Active.remove(Value.Index)

        _Require(
            all(Value.Source == "node" for Value in self.Outputs)
            and len(set(self.Outputs)) == len(self.Outputs),
            "MalformedPayload", "outputs must name distinct node signals",
        )
        for Output in self.Outputs:
            Visit(Output)
        _Require(
            len(Seen) == len(self.Nodes) and len(Inputs) == self.InputCount,
            "MalformedPayload", "unreachable node or unused boundary role",
        )

    def ToDictionary(self) -> dict[str, object]:
        """Return a detached document preserving every ordered edge."""
        return {
            "SchemaVersion": self.SchemaVersion,
            "InputRoles": [["input", Index] for Index in range(self.InputCount)],
            "Nodes": [
                {"Kind": Node.Kind, "Inputs": [[V.Source, V.Index] for V in Node.Inputs]}
                for Node in self.Nodes
            ],
            "OutputRoles": [[V.Source, V.Index] for V in self.Outputs],
        }

    @property
    def Fingerprint(self) -> str:
        return sha256(json.dumps(
            self.ToDictionary(), sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()


def _Names(Values: Sequence[str], Label: str, *, Unique: bool = False) -> tuple[str, ...]:
    _Require(type(Values) in (list, tuple), "MalformedSource", Label)
    Result = tuple(Values)
    _Require(
        all(type(Value) is str and bool(Value.strip()) for Value in Result)
        and (not Unique or len(set(Result)) == len(Result)),
        "MalformedSource", Label,
    )
    return Result


def _InspectRegion(
    Module: ModuleIR,
    Component: TopologyComponent,
    OrderedInputSignals: Sequence[str],
    OrderedOutputSignals: Sequence[str],
) -> tuple[tuple[Gate, ...], tuple[str, ...], tuple[str, ...], tuple[ComponentPort, ...]]:
    """Check source boundary facts; never trust the coarse component hash.

The extractor counts distinct consumer gates, including OUTPUT markers. This
metadata check follows that public boundary convention; graph edges below
retain individual repeated terminal positions. No partition is reconstructed.
"""
    _Require(type(Module) is ModuleIR and type(Component) is TopologyComponent,
             "MalformedSource", "expected module and extracted component")
    Members = _Names(Component.GateNames, "region membership", Unique=True)
    _Require(1 <= len(Members) <= 18, "UnsupportedDomain", "requires 1-18 gates")
    Inputs = _Names(OrderedInputSignals, "input role bindings", Unique=True)
    Outputs = _Names(OrderedOutputSignals, "output role bindings", Unique=True)
    ModuleInputs = set(_Names(Module.Inputs, "module inputs", Unique=True))
    ModuleOutputs = set(_Names(Module.Outputs, "module outputs", Unique=True))
    GateByName: dict[str, Gate] = {}
    Producer: dict[str, str] = {}
    Consumers: dict[str, set[str]] = {}
    _Require(type(Module.Gates) in (list, tuple), "MalformedSource", "gate sequence")
    for Value in Module.Gates:
        _Require(type(Value) is Gate and type(Value.Name) is str and bool(Value.Name.strip())
                 and Value.Name not in GateByName, "MalformedSource", "duplicate/invalid gate")
        GateByName[Value.Name] = Value
        for Signal in _Names(Value.Outputs, "gate outputs", Unique=True):
            _Require(Signal not in Producer, "AmbiguousDriver", Signal)
            Producer[Signal] = Value.Name
        for Signal in _Names(Value.Inputs, "gate inputs"):
            Consumers.setdefault(Signal, set()).add(Value.Name)
    _Require(set(Members) <= GateByName.keys(), "MalformedSource", "unknown member")
    Gates = tuple(GateByName[Name] for Name in Members)
    for Value in Gates:
        _Require(Value.Kind == GateKind.NAND and len(Value.Inputs) == 2
                 and len(Value.Outputs) == 1 and type(Value.Attrs) is dict
                 and not Value.Attrs, "UnsupportedDomain", "only attribute-free scalar NANDs")
    MemberSet = set(Members)
    Signals = {Signal for Value in Gates for Signal in (*Value.Inputs, *Value.Outputs)}
    _Require(type(Module.Nets) is dict and type(Module.Ports) is dict,
             "MalformedSource", "net/port metadata")
    for Signal in sorted(Signals):
        Net = Module.Nets.get(Signal)
        Width = Module.Ports.get(Signal, 1)
        _Require(type(Width) is int and Width == 1, "UnsupportedDomain", "non-scalar port")
        if Net is not None:
            _Require(getattr(Net, "Name", None) == Signal
                     and type(getattr(Net, "Width", None)) is int and Net.Width == 1
                     and getattr(Net, "IsConstant", None) is False
                     and getattr(Net, "Value", None) is None,
                     "UnsupportedDomain", "non-scalar, constant or malformed net")
        if Signal in ModuleInputs and Signal in Producer:
            _Require(GateByName[Producer[Signal]].Kind == GateKind.INPUT,
                     "AmbiguousDriver", "module input driven by logic")

    ExpectedInputs: dict[str, ComponentPort] = {}
    ExpectedOutputs: dict[str, ComponentPort] = {}
    InternalSignals: set[str] = set()
    for Signal in sorted(Signals):
        Inside = Consumers.get(Signal, set()) & MemberSet
        Outside = Consumers.get(Signal, set()) - MemberSet
        DrivenInside = Producer.get(Signal) in MemberSet
        if DrivenInside and Inside and not Outside:
            InternalSignals.add(Signal)
        if Inside and not DrivenInside:
            _Require(Signal in Producer or Signal in ModuleInputs,
                     "MissingDriver", Signal)
            ExpectedInputs[Signal] = ComponentPort(
                Signal, "input", max(1, len(Inside)), len(Inside), max(1, len(Outside)),
            )
        if DrivenInside and (Outside or not Consumers.get(Signal)):
            ExpectedOutputs[Signal] = ComponentPort(
                Signal, "output", max(1, len(Outside)), 1 + len(Inside), max(1, len(Outside)),
            )
        # A module export hidden by extraction would lose a cross-output relation.
        _Require(not (DrivenInside and Signal in ModuleOutputs and Signal not in ExpectedOutputs),
                 "BoundaryMismatch", "module export requires an explicit OUTPUT terminal")

    for Ports, Expected, Direction in (
        (Component.InputPorts, ExpectedInputs, "input"),
        (Component.OutputPorts, ExpectedOutputs, "output"),
    ):
        _Require(type(Ports) is tuple and all(
            type(Port) is ComponentPort and type(Port.Signal) is str for Port in Ports
        ),
                 "BoundaryMismatch", "invalid extracted ports")
        _Require(len(Ports) == len(Expected) and len({P.Signal for P in Ports}) == len(Ports),
                 "BoundaryMismatch", "boundary coverage")
        for Port in Ports:
            _Require(Port.Direction == Direction and Port.Signal in Expected
                     and all(type(V) is int for V in (
                         Port.Capacity, Port.InternalTerminalCount, Port.ExternalTerminalCount,
                     )) and Port == Expected[Port.Signal],
                     "BoundaryMismatch", "stale boundary metadata")
    _Require(set(_Names(Component.InternalSignals, "internal signals", Unique=True)) == InternalSignals,
             "BoundaryMismatch", "stale internal signals")
    _Require(set(Inputs) == set(ExpectedInputs) and set(Outputs) == set(ExpectedOutputs)
             and bool(Outputs), "BoundaryMismatch", "ordered roles must cover boundary exactly")
    return Gates, Inputs, Outputs, tuple(
        [ExpectedInputs[Signal] for Signal in Inputs]
        + [ExpectedOutputs[Signal] for Signal in Outputs]
    )


def NormalizeRegionTopology(
    Module: ModuleIR,
    Component: TopologyComponent,
    *,
    OrderedInputSignals: Sequence[str],
    OrderedOutputSignals: Sequence[str],
) -> NormalizedRegionTopology:
    """Normalize a validated rooted ordered DAG in linear graph traversal work.

Source indexing is linear in module terminals; canonical traversal visits at
most 18 gates and their 36 input edges. There is no factorial search, partial
identity, or claim about the extractor choosing identical partitions.
"""
    Gates, Inputs, Outputs, _Ports = _InspectRegion(
        Module, Component, OrderedInputSignals, OrderedOutputSignals,
    )
    InputRoles = {Signal: Index for Index, Signal in enumerate(Inputs)}
    Producer = {Value.Outputs[0]: Value for Value in Gates}
    IndexBySignal: dict[str, int] = {}
    Active: set[str] = set()
    Nodes: list[NormalizedRegionNode | None] = []

    def Visit(Signal: str) -> RegionSignalReference:
        if Signal in InputRoles:
            return RegionSignalReference("input", InputRoles[Signal])
        _Require(Signal in Producer, "MissingDriver", Signal)
        _Require(Signal not in Active, "UnsupportedDomain", "cyclic region")
        if Signal not in IndexBySignal:
            IndexBySignal[Signal] = len(Nodes)
            Nodes.append(None)
            Active.add(Signal)
            Edges = tuple(Visit(Input) for Input in Producer[Signal].Inputs)
            Nodes[IndexBySignal[Signal]] = NormalizedRegionNode(Edges)
            Active.remove(Signal)
        return RegionSignalReference("node", IndexBySignal[Signal])

    OutputReferences = tuple(Visit(Signal) for Signal in Outputs)
    _Require(len(Nodes) == len(Gates), "UnsupportedDomain", "unreachable region gates")
    return NormalizedRegionTopology(len(Inputs), tuple(Nodes), OutputReferences)
