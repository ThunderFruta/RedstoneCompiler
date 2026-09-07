"""Exact declared dependency equality for selected access, without reuse authority.

Producer identities are opaque evidence references, not locally validated
physical claims. A match requires every fixed category, ordered boundary
metadata and the complete normalized topology. No result certifies feasibility,
completeness, non-interference, ranking, cached use or commitment.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import ClassVar, Sequence

from Compilation.Ir.ComponentGraph import TopologyComponent
from Compilation.Ir.Models import ModuleIR

from .TopologyIdentity import NormalizedRegionTopology, NormalizeRegionTopology, _InspectRegion


# These categories describe the full selected-access comparison scope. None is
# optional, including identities of explicitly empty sets. Producers must bind
# all data within a category to the same subject, snapshot and ordered roles.
# PlacedModel includes resource inclusion freshness, exclusions with owners and
# unowned exclusions; GraphModelVersion includes GraphVersion and model version.
RequiredRegionDependencyCategories = (
    "AccessDomain", "AccessWitness", "BoundaryBindings", "Bounds",
    "CellTemplates", "Domain", "ElectricalInfluence", "ExternalReservations",
    "ForeignFrozenWires", "GraphModelVersion", "Ownership", "PlacedModel",
    "Policy", "RequiredAir", "SelectedPaths", "Support", "Technology", "Transforms",
)
RegionPhysicalDependencyScope = "selected-access-dependencies-v1"


class RegionDependencyError(ValueError):
    """Malformed dependency declaration, not a physical infeasibility result."""

    def __init__(self, Code: str, Detail: str):
        self.Code = Code
        super().__init__(f"{Code}: {Detail}")


def _Require(Condition: bool, Detail: str) -> None:
    if not Condition:
        raise RegionDependencyError("MalformedDependencies", Detail)


@dataclass(frozen=True, slots=True)
class RegionDependencyIdentity:
    """Opaque producer fingerprint with a recognized encoding and provenance.

The 16-hex encoding accommodates existing producer SHA-256 prefixes; it does
not increase their collision resistance. Issuance/content authenticity remains
the producer's responsibility, and equality does not validate that content.
"""

    Producer: str
    Revision: str
    Schema: str
    Value: str

    def __post_init__(self) -> None:
        _Require(
            type(self.Producer) is str and self.Producer in ("Physical-Rules", "Joint-Physical-Design")
            and type(self.Revision) is str
            and re.fullmatch(r"[0-9a-f]{40}", self.Revision) is not None
            and type(self.Schema) is str and self.Schema in ("sha256", "sha256-prefix16")
            and type(self.Value) is str,
            "unknown producer, revision or identity encoding",
        )
        Length = 64 if self.Schema == "sha256" else 16
        _Require(re.fullmatch(r"[0-9a-f]{" + str(Length) + r"}", self.Value) is not None,
                 "malformed producer fingerprint")


@dataclass(frozen=True, slots=True)
class RegionPhysicalDependency:
    Category: str
    Identity: RegionDependencyIdentity

    def __post_init__(self) -> None:
        _Require(type(self.Category) is str and self.Category in RequiredRegionDependencyCategories
                 and type(self.Identity) is RegionDependencyIdentity, "unknown dependency category")
        self.Identity.__post_init__()


@dataclass(frozen=True, slots=True)
class RegionBoundaryPort:
    """Counts use extraction's distinct-consumer convention, not edge counts."""

    Direction: str
    Role: int
    Capacity: int
    InternalTerminalCount: int
    ExternalTerminalCount: int

    def __post_init__(self) -> None:
        _Require(type(self.Direction) is str and self.Direction in ("input", "output")
                 and type(self.Role) is int and self.Role >= 0
                 and all(type(Value) is int and Value >= 1 for Value in (
                     self.Capacity, self.InternalTerminalCount, self.ExternalTerminalCount,
                 )), "invalid boundary counts or role")


@dataclass(frozen=True, slots=True)
class RegionBoundaryDependencies:
    Ports: tuple[RegionBoundaryPort, ...]

    def __post_init__(self) -> None:
        _Require(type(self.Ports) is tuple and 2 <= len(self.Ports) <= 54
                 and all(type(Port) is RegionBoundaryPort for Port in self.Ports),
                 "invalid immutable boundary")
        Expected = tuple(
            (Direction, Index)
            for Direction in ("input", "output")
            for Index in range(sum(Port.Direction == Direction for Port in self.Ports))
        )
        _Require(tuple((Port.Direction, Port.Role) for Port in self.Ports) == Expected,
                 "boundary roles must be consecutive and ordered")
        for Port in self.Ports:
            Port.__post_init__()

    def ValidateTopology(self, Topology: NormalizedRegionTopology) -> None:
        """Bind role coverage/internal counts without inventing external facts."""
        self.__post_init__()
        InputPorts = tuple(Port for Port in self.Ports if Port.Direction == "input")
        OutputPorts = tuple(Port for Port in self.Ports if Port.Direction == "output")
        _Require(len(InputPorts) == Topology.InputCount and len(OutputPorts) == len(Topology.Outputs),
                 "topology/boundary role coverage differs")
        for Port in self.Ports:
            if Port.Direction == "input":
                Count = sum(any(V.Source == "input" and V.Index == Port.Role for V in Node.Inputs)
                            for Node in Topology.Nodes)
                Capacity = Count
            else:
                Reference = Topology.Outputs[Port.Role]
                Count = 1 + sum(Reference in Node.Inputs for Node in Topology.Nodes)
                Capacity = Port.ExternalTerminalCount
            _Require(Port.InternalTerminalCount == Count and Port.Capacity == Capacity,
                     "boundary metadata disagrees with graph or extraction convention")


def BuildRegionBoundaryDependencies(
    Module: ModuleIR,
    Component: TopologyComponent,
    *,
    OrderedInputSignals: Sequence[str],
    OrderedOutputSignals: Sequence[str],
) -> RegionBoundaryDependencies:
    """Snapshot exact boundary metadata after checking it against source edges."""
    Topology = NormalizeRegionTopology(
        Module, Component, OrderedInputSignals=OrderedInputSignals,
        OrderedOutputSignals=OrderedOutputSignals,
    )
    _Gates, Inputs, _Outputs, Ports = _InspectRegion(
        Module, Component, OrderedInputSignals, OrderedOutputSignals,
    )
    Result = RegionBoundaryDependencies(tuple(
        RegionBoundaryPort(
            Port.Direction, Index if Index < len(Inputs) else Index - len(Inputs),
            Port.Capacity, Port.InternalTerminalCount, Port.ExternalTerminalCount,
        )
        for Index, Port in enumerate(Ports)
    ))
    Result.ValidateTopology(Topology)
    return Result


@dataclass(frozen=True, slots=True)
class RegionPhysicalDependencies:
    """A declaration, including possibly incomplete coverage, for one subject.

Subject identifies the full selected-access claim being compared, and Snapshot
binds all category identities to its producer context. Neither is relabelled
on comparison. No Complete/Validated boolean can substitute for categories.
"""

    Subject: RegionDependencyIdentity
    Snapshot: RegionDependencyIdentity
    Boundary: RegionBoundaryDependencies
    Dependencies: tuple[RegionPhysicalDependency, ...]
    Scope: str = RegionPhysicalDependencyScope
    SchemaVersion: ClassVar[str] = "region-dependency-manifest-v1"

    def __post_init__(self) -> None:
        self.Validate()

    def Validate(self) -> None:
        _Require(type(self.Subject) is RegionDependencyIdentity
                 and type(self.Snapshot) is RegionDependencyIdentity
                 and type(self.Boundary) is RegionBoundaryDependencies
                 and type(self.Dependencies) is tuple
                 and all(type(Value) is RegionPhysicalDependency for Value in self.Dependencies)
                 and type(self.Scope) is str and bool(self.Scope), "invalid manifest")
        self.Subject.__post_init__()
        self.Snapshot.__post_init__()
        self.Boundary.__post_init__()
        for Value in self.Dependencies:
            Value.__post_init__()
        Categories = [Value.Category for Value in self.Dependencies]
        _Require(len(Categories) == len(set(Categories)), "duplicate dependency category")

    def ToDictionary(self) -> dict[str, object]:
        def Identity(Value: RegionDependencyIdentity) -> list[str]:
            return [Value.Producer, Value.Revision, Value.Schema, Value.Value]

        return {
            "SchemaVersion": self.SchemaVersion, "Scope": self.Scope,
            "Subject": Identity(self.Subject), "Snapshot": Identity(self.Snapshot),
            "Boundary": [[P.Direction, P.Role, P.Capacity, P.InternalTerminalCount,
                          P.ExternalTerminalCount] for P in self.Boundary.Ports],
            "Dependencies": [[Value.Category, Identity(Value.Identity)] for Value in sorted(
                self.Dependencies, key=lambda Value: Value.Category,
            )],
        }

    @property
    def Fingerprint(self) -> str:
        return sha256(json.dumps(
            self.ToDictionary(), sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class RegionDependencyComparison:
    """Equality diagnostics only; this type carries no physical proof/result.

Well-formed matching declarations can be unverified or invented. Revision
syntax is checked, but source existence, producer issuance, identity contents
and current physical compatibility are never established by this operation.
"""

    Status: str
    Differences: tuple[str, ...] = ()
    Unresolved: tuple[str, ...] = ()

    @property
    def ProducerValidation(self) -> str:
        """No caller flag can turn declared equality into producer validation."""
        return "NotPerformed"


def CompareRegionPhysicalDependencies(
    LeftTopology: NormalizedRegionTopology,
    LeftDependencies: RegionPhysicalDependencies,
    RightTopology: NormalizedRegionTopology,
    RightDependencies: RegionPhysicalDependencies,
) -> RegionDependencyComparison:
    """Compare full structures and complete declarations, never hash fields."""
    Unknown: list[str] = []
    for Side, Topology, Manifest in (
        ("Left", LeftTopology, LeftDependencies), ("Right", RightTopology, RightDependencies),
    ):
        if type(Topology) is not NormalizedRegionTopology or type(Manifest) is not RegionPhysicalDependencies:
            Unknown.append(f"{Side}.MalformedPayload")
            continue
        try:
            Topology.Validate()
            Manifest.Validate()
            Manifest.Boundary.ValidateTopology(Topology)
        except (ValueError, TypeError, AttributeError):
            Unknown.append(f"{Side}.MalformedPayload")
            continue
        if Manifest.Scope != RegionPhysicalDependencyScope:
            Unknown.append(f"{Side}.UnsupportedScope")
        Present = {Value.Category for Value in Manifest.Dependencies}
        Unknown.extend(f"{Side}.Missing.{Category}"
                       for Category in RequiredRegionDependencyCategories if Category not in Present)
    if Unknown:
        return RegionDependencyComparison("Unresolved", Unresolved=tuple(sorted(Unknown)))
    Differences: list[str] = []
    if LeftTopology != RightTopology:
        Differences.append("Topology")
    for Field in ("Subject", "Snapshot", "Boundary"):
        if getattr(LeftDependencies, Field) != getattr(RightDependencies, Field):
            Differences.append(Field)
    Left = {Value.Category: Value.Identity for Value in LeftDependencies.Dependencies}
    Right = {Value.Category: Value.Identity for Value in RightDependencies.Dependencies}
    Differences.extend(Category for Category in RequiredRegionDependencyCategories if Left[Category] != Right[Category])
    return RegionDependencyComparison(
        "DependenciesDiffer" if Differences else "DependenciesMatch", tuple(Differences),
    )
