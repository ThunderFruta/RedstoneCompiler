"""Selected-access dependency equality; fixtures make no physical legality claim."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
import json

import pytest

from Compilation.Ir.ComponentGraph import BuildComponentGraph
from Compilation.Ir.Models import Gate, GateKind
from PhysicalDesign.Routing.Regions.PhysicalDependencies import (
    BuildRegionBoundaryDependencies, CompareRegionPhysicalDependencies,
    RegionBoundaryDependencies, RegionDependencyError, RegionDependencyIdentity,
    RegionPhysicalDependencies, RegionPhysicalDependency,
)
from PhysicalDesign.Routing.Regions.TopologyIdentity import NormalizedRegionTopology, NormalizeRegionTopology
from Tests.PhysicalDesign.Routing.test_region_topology_identity import (
    BuildExtracted, DegreeTwin, MultiOutput, Reconvergent,
)


# Independent checklist frozen from the approved selected-access contract.
Categories = (
    "Technology", "GraphModelVersion", "PlacedModel", "CellTemplates", "Transforms",
    "Policy", "Domain", "AccessDomain", "AccessWitness", "BoundaryBindings", "Bounds",
    "SelectedPaths", "Ownership", "Support", "RequiredAir", "ElectricalInfluence",
    "ForeignFrozenWires", "ExternalReservations",
)


def Identity(Label):
    # Synthetic opaque identifiers exercise equality only, never a producer validator.
    return RegionDependencyIdentity("Physical-Rules", "b65847043c7ba354ea3835486df60840f9510cc2",
                                    "sha256", sha256(Label.encode()).hexdigest())


def Declare(Module, Component):
    Arguments = dict(OrderedInputSignals=Module.Inputs, OrderedOutputSignals=Module.Outputs)
    Topology = NormalizeRegionTopology(Module, Component, **Arguments)
    Boundary = BuildRegionBoundaryDependencies(Module, Component, **Arguments)
    Manifest = RegionPhysicalDependencies(Identity("selected-subject"), Identity("snapshot"), Boundary,
                                         tuple(RegionPhysicalDependency(C, Identity(C)) for C in Categories))
    return Topology, Manifest


def test_complete_equal_declarations_compare_without_granting_physical_authority(tmp_path):
    Topology, Manifest = Declare(*BuildExtracted(MultiOutput))
    Reordered = replace(Manifest, Dependencies=tuple(reversed(Manifest.Dependencies)))
    Result = CompareRegionPhysicalDependencies(Topology, Manifest, deepcopy(Topology), Reordered)
    assert Result.Status == "DependenciesMatch"
    assert Result.ProducerValidation == "NotPerformed"
    assert not Result.Differences and not Result.Unresolved
    assert Manifest.Fingerprint == Reordered.Fingerprint
    (tmp_path / "DependencyEquality.json").write_text(json.dumps({
        "Claim": "complete declared selected-access dependency equality only",
        "Expected": "DependenciesMatch", "Observed": Result.Status,
        "Topology": Topology.ToDictionary(), "Manifest": Manifest.ToDictionary(),
        "PhysicalValidation": "not-run; synthetic opaque identities",
    }, indent=2) + "\n")


def test_well_formed_unverified_declarations_match_only_as_declarations():
    Topology, Manifest = Declare(*BuildExtracted(MultiOutput))
    Unverified = replace(Identity("unknown-content"), Revision="0" * 40, Value="0" * 64)
    Declared = replace(Manifest, Subject=Unverified, Snapshot=Unverified, Dependencies=tuple(
        replace(Value, Identity=Unverified) for Value in Manifest.Dependencies
    ))
    Result = CompareRegionPhysicalDependencies(Topology, Declared, Topology, deepcopy(Declared))
    assert Result.Status == "DependenciesMatch"
    assert Result.ProducerValidation == "NotPerformed"
    DifferentRevision = replace(Declared, Subject=replace(Unverified, Revision="1" * 40))
    Different = CompareRegionPhysicalDependencies(Topology, Declared, Topology, DifferentRevision)
    assert Different.Status == "DependenciesDiffer"
    assert Different.Differences == ("Subject",)
    assert Different.ProducerValidation == "NotPerformed"
    with pytest.raises(TypeError):
        replace(Result, ProducerValidation="Performed")


@pytest.mark.parametrize("Category", Categories)
def test_each_changed_or_missing_required_dependency_prevents_match(Category, tmp_path):
    Topology, Manifest = Declare(*BuildExtracted(MultiOutput))
    Changed = replace(Manifest, Dependencies=tuple(
        replace(Value, Identity=Identity("changed-" + Category)) if Value.Category == Category else Value
        for Value in Manifest.Dependencies
    ))
    Missing = replace(Manifest, Dependencies=tuple(V for V in Manifest.Dependencies if V.Category != Category))
    Difference = CompareRegionPhysicalDependencies(Topology, Manifest, Topology, Changed)
    Incomplete = CompareRegionPhysicalDependencies(Topology, Missing, Topology, Missing)
    assert Difference.Status == "DependenciesDiffer" and Difference.Differences == (Category,)
    assert Incomplete.Status == "Unresolved"
    assert Incomplete.Unresolved == (f"Left.Missing.{Category}", f"Right.Missing.{Category}")
    (tmp_path / "DependencyChallenge.json").write_text(json.dumps({
        "Category": Category, "TopologyEqual": True,
        "ChangedExpected": "DependenciesDiffer", "ChangedObserved": Difference.Status,
        "MissingExpected": "Unresolved", "MissingObserved": Incomplete.Status,
        "Differences": Difference.Differences, "Unresolved": Incomplete.Unresolved,
        "Original": Manifest.ToDictionary(), "Changed": Changed.ToDictionary(),
    }, indent=2) + "\n")


@pytest.mark.parametrize("Signal,AddedConsumers", [("A", 2), ("T", 1)])
def test_actual_external_consumers_change_dependencies_without_changing_induced_topology(Signal, AddedConsumers):
    OriginalModule, OriginalComponent = BuildExtracted(MultiOutput)
    ChangedModule = deepcopy(OriginalModule)
    for Index in range(AddedConsumers):
        ChangedModule.Gates.append(Gate(f"ForeignTerminal{Index}", GateKind.OUTPUT, [], [Signal]))
    ChangedComponent = BuildComponentGraph(ChangedModule).Components[0]
    First, FirstManifest = Declare(OriginalModule, OriginalComponent)
    Second, SecondManifest = Declare(ChangedModule, ChangedComponent)
    assert First == Second and First.Fingerprint == Second.Fingerprint
    assert FirstManifest.Boundary != SecondManifest.Boundary
    Result = CompareRegionPhysicalDependencies(First, FirstManifest, Second, SecondManifest)
    assert Result.Status == "DependenciesDiffer" and Result.Differences == ("Boundary",)
    # A stale extraction cannot be laundered through the boundary snapshot builder.
    with pytest.raises(ValueError):
        BuildRegionBoundaryDependencies(ChangedModule, OriginalComponent,
                                        OrderedInputSignals=ChangedModule.Inputs,
                                        OrderedOutputSignals=ChangedModule.Outputs)


def test_boundary_counts_remain_distinct_from_repeated_input_terminal_edges():
    Topology, Manifest = Declare(*BuildExtracted(MultiOutput))
    Input, FirstOutput, LastOutput = Manifest.Boundary.Ports
    assert (Input.InternalTerminalCount, Input.Capacity) == (1, 1)
    assert (FirstOutput.InternalTerminalCount, FirstOutput.ExternalTerminalCount) == (2, 1)
    assert (LastOutput.InternalTerminalCount, LastOutput.Capacity) == (1, 1)
    assert sum(Edge.Source == "input" for Node in Topology.Nodes for Edge in Node.Inputs) == 2


@pytest.mark.parametrize("Field", ["Capacity", "InternalTerminalCount", "ExternalTerminalCount"])
def test_forged_boundary_values_cannot_hide_behind_equal_topology(Field):
    Topology, Manifest = Declare(*BuildExtracted(MultiOutput))
    Ports = list(Manifest.Boundary.Ports)
    Ports[0] = replace(Ports[0], **{Field: 99})
    Changed = replace(Manifest, Boundary=RegionBoundaryDependencies(tuple(Ports)))
    Result = CompareRegionPhysicalDependencies(Topology, Manifest, Topology, Changed)
    assert Result.Status in ("Unresolved", "DependenciesDiffer")
    if Field != "ExternalTerminalCount":
        assert Result.Status == "Unresolved"


@pytest.mark.parametrize("Change", ["subject", "snapshot", "revision", "producer", "encoding"])
def test_identity_provenance_and_claim_context_are_bound(Change):
    Topology, Manifest = Declare(*BuildExtracted(MultiOutput))
    if Change == "subject": Changed = replace(Manifest, Subject=Identity("other-subject"))
    elif Change == "snapshot": Changed = replace(Manifest, Snapshot=Identity("other-snapshot"))
    else:
        First = Manifest.Dependencies[0]
        if Change == "revision": New = replace(First.Identity, Revision="1" * 40)
        elif Change == "producer": New = replace(First.Identity, Producer="Joint-Physical-Design")
        else: New = replace(First.Identity, Schema="sha256-prefix16", Value=First.Identity.Value[:16])
        Changed = replace(Manifest, Dependencies=(replace(First, Identity=New), *Manifest.Dependencies[1:]))
    assert CompareRegionPhysicalDependencies(Topology, Manifest, Topology, Changed).Status == "DependenciesDiffer"


@pytest.mark.parametrize("Fields", [
    {"Producer": "unknown"}, {"Revision": "moving-tip"}, {"Schema": "unknown"},
    {"Value": ""}, {"Value": "unknown"}, {"Value": "g" * 64}, {"Value": True},
])
def test_unknown_or_malformed_signatures_are_rejected_at_construction(Fields):
    with pytest.raises(RegionDependencyError) as Error:
        replace(Identity("base"), **Fields)
    assert Error.value.Code == "MalformedDependencies"


def test_unknown_scope_empty_coverage_duplicate_categories_and_mutable_payloads_do_not_match():
    Topology, Manifest = Declare(*BuildExtracted(MultiOutput))
    for Changed in (replace(Manifest, Scope="future-physical-work"), replace(Manifest, Dependencies=())):
        assert CompareRegionPhysicalDependencies(Topology, Changed, Topology, Changed).Status == "Unresolved"
    with pytest.raises(RegionDependencyError):
        replace(Manifest, Dependencies=(*Manifest.Dependencies, Manifest.Dependencies[0]))
    with pytest.raises(RegionDependencyError):
        replace(Manifest, Dependencies=list(Manifest.Dependencies))
    with pytest.raises(RegionDependencyError):
        replace(Manifest.Dependencies[0], Category="FutureConstraint")
    with pytest.raises(RegionDependencyError):
        RegionBoundaryDependencies(tuple(reversed(Manifest.Boundary.Ports)))


def test_full_structure_comparison_survives_equal_hashes(monkeypatch):
    First, Left = Declare(*BuildExtracted(Reconvergent))
    Other, Right = Declare(*BuildExtracted(DegreeTwin))
    # Simulate even a genuine digest collision; identity must compare the graph.
    monkeypatch.setattr(NormalizedRegionTopology, "Fingerprint", property(lambda Self: "collision"))
    monkeypatch.setattr(RegionPhysicalDependencies, "Fingerprint", property(lambda Self: "collision"))
    assert First.Fingerprint == Other.Fingerprint
    assert Left.Fingerprint == Right.Fingerprint
    Result = CompareRegionPhysicalDependencies(First, Left, Other, Right)
    assert Result.Status == "DependenciesDiffer" and "Topology" in Result.Differences
    Changed = replace(Left, Dependencies=())
    assert CompareRegionPhysicalDependencies(First, Changed, First, Changed).Status == "Unresolved"


def test_immutable_documents_and_revalidation_reject_corrupted_public_payloads():
    Topology, Manifest = Declare(*BuildExtracted(MultiOutput))
    Before = Manifest.Fingerprint
    Document = Manifest.ToDictionary()
    Document["Dependencies"].clear()
    assert Manifest.Fingerprint == Before
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        Manifest.Dependencies = ()
    with pytest.raises(TypeError):
        replace(Manifest, Fingerprint=Before)
    # Frozen dataclasses are not a security boundary. Even bypassing their normal
    # constructor with Python reflection must not make malformed declarations pass.
    Corrupt = deepcopy(Manifest)
    object.__setattr__(Corrupt.Dependencies[0].Identity, "Value", "unknown")
    assert CompareRegionPhysicalDependencies(Topology, Corrupt, Topology, Corrupt).Status == "Unresolved"
    BrokenTopology = deepcopy(Topology)
    object.__setattr__(BrokenTopology, "InputCount", False)
    assert CompareRegionPhysicalDependencies(BrokenTopology, Manifest, BrokenTopology, Manifest).Status == "Unresolved"
