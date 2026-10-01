"""Independent invariants for Joint typed-route consumer authority records."""

from dataclasses import FrozenInstanceError, dataclass, replace
from enum import Enum
from pathlib import PurePosixPath
from time import monotonic
from types import MappingProxyType

import pytest
from RedstoneCompiler import RustRouting
from PhysicalDesign.Runtime.NativeRouting import (
    ExecuteNativeRouteBatchOutcomesV1,
    NativeRouteResultKind,
)

from PhysicalDesign.Routing.Global.TypedRouteConsumer import (
    AuthorityIdentity,
    BuildTypedNativeCoarseRequest,
    BuildTypedRouteExecutionPlan,
    BuildTypedRouteOriginRecordsFromResults,
    BuildDisabledSelectedAccessAuthority,
    CALLER_BINDING_NAMES,
    CanonicalAuthority,
    CanonicalJson,
    TypedRouteBatchCounters,
    TypedRouteCallerSnapshot,
    TypedRouteContextScope,
    TypedRouteExecutionScope,
    TypedRouteAdmissionRecord,
    TypedRouteOriginRecord,
    TypedRouteOriginDescriptor,
    TypedRoutePhysicalEvidence,
    TypedRouteRecoveryAttribution,
    TypedRouteTerminalCompletesRequest,
)


def Snapshot():
    return TypedRouteCallerSnapshot(tuple(
        (Name, AuthorityIdentity({"Name": Name}))
        for Name in CALLER_BINDING_NAMES
    ))


def Context():
    return TypedRouteContextScope(
        (0, 1, 0, 1, 0, 1),
        (0, 1, 0, 1),
        AuthorityIdentity({"Graph": "fixture"}),
        AuthorityIdentity({"Nodes": ((0, 0, 0),)}),
        AuthorityIdentity({"Edges": ()}),
    )


def Descriptor(*, Variant=0, Fragment="alpha"):
    return TypedRouteOriginDescriptor(
        Signal="signal",
        SourcePortal={"Path": [[0, 0, 0]], "PortalId": "source"},
        TargetPortals=(
            {"Path": [[1, 0, 0]], "PortalId": "target"},
        ),
        Guide=((0, 0), (1, 0)),
        Layer=0,
        Axis="X",
        Lane=0,
        Variant=Variant,
        ImmutableFragments={
            "SchemaVersion": "fixture-fragments-v1",
            "Value": Fragment,
        },
    )


def LegacyRequest(*, ExpansionCap=32):
    return (
        [(0, 0, 0)],
        [],
        [(0, 0)],
        [(0, 0, 0)],
        [],
        [(0, 0)],
        0,
        1,
        2,
        3,
        ExpansionCap,
    )


def OriginRecord(
    *,
    OriginalOrdinal=0,
    CanonicalOriginalOrdinal=0,
    CanonicalOriginIdentity=None,
    NativeOrdinal=0,
    DescriptorValue=None,
    NativeKind="Routed",
):
    DescriptorValue = DescriptorValue or Descriptor()
    OriginIdentity = AuthorityIdentity({
        "Origin": OriginalOrdinal,
        "Descriptor": DescriptorValue.Identity,
    })
    return TypedRouteOriginRecord(
        OriginIdentity=OriginIdentity,
        OriginDescriptor=DescriptorValue,
        OriginalOrdinal=OriginalOrdinal,
        CanonicalOriginalOrdinal=CanonicalOriginalOrdinal,
        CanonicalOriginIdentity=(
            OriginIdentity
            if CanonicalOriginIdentity is None
            else CanonicalOriginIdentity
        ),
        CanonicalRequestId="request-0",
        CanonicalReceiptIdentity=AuthorityIdentity({"Receipt": "canonical"}),
        ExecutionScopeIdentity=AuthorityIdentity({"Scope": "one"}),
        GeometryIdentity=AuthorityIdentity({"Geometry": "same"}),
        NativePayloadIdentity=AuthorityIdentity({"Payload": "same"}),
        RouteDomainIdentity=AuthorityIdentity({"Domain": "same"}),
        NativeOrdinal=NativeOrdinal,
        NativeKind=NativeKind,
        PreNativeReason=None,
        ExpansionCap=32,
        ActualExpansionCount=3,
        CancellationRequestedBeforeStart=False,
        DeadlineAtMonotonicSeconds=10.0,
    )


def ExecutedOriginFixture(*, CancellationRequestedBeforeStart=False, Targeted=False):
    """Exercise both canonical results and an equivalent through real Runtime."""
    Bounds = (0, 2, 0, 0, 0, 0)
    PlacementBounds = (0, 2, 0, 0)
    Nodes = ((0, 0, 0), (1, 0, 0), (2, 0, 0))
    Edges = (((0, 0, 0), (1, 0, 0)), ((1, 0, 0), (2, 0, 0)))
    NativeContext = RustRouting.RoutingContext(
        Bounds, PlacementBounds, list(Nodes), list(Edges)
    )
    ContextScope = TypedRouteContextScope.FromConstruction(
        Bounds,
        PlacementBounds,
        Nodes,
        Edges,
        NativeContext.AuthoritativeContextGraphSha256,
    )
    Scope = TypedRouteExecutionScope(
        AuthorityIdentity({"Invocation": "corruption-and-cancellation-controls"}),
        monotonic() + 10.0,
        ContextScope,
        Snapshot(),
    )
    First = list(LegacyRequest())
    First[2] = [(0, 0), (1, 0), (2, 0)]
    First[5] = [(0, 0), (1, 0), (2, 0)]
    First[1] = [[(2, 0, 0)]] if Targeted else []
    Second = list(First)
    Second[0] = [(2, 0, 0)]
    Second[3] = [(2, 0, 0)]
    Second[1] = [[(0, 0, 0)]] if Targeted else []
    LegacyRequests = (tuple(First), tuple(First), tuple(Second))
    Descriptors = tuple(Descriptor(Variant=Index) for Index in range(3))
    Plan = BuildTypedRouteExecutionPlan(
        Scope.InvocationIdentity,
        Scope.Context.Identity,
        Scope.CallerSnapshot.Identity,
        tuple(zip(LegacyRequests, Descriptors)),
        CancellationRequestedBeforeStart=CancellationRequestedBeforeStart,
    )
    Requests = tuple(
        BuildTypedNativeCoarseRequest(
            f"native-canonical-{Index}",
            Scope.CallerSnapshot.Bindings,
            Bounds,
            PlacementBounds,
            Request,
            CancellationRequestedBeforeStart=CancellationRequestedBeforeStart,
        )
        for Index, Request in enumerate((LegacyRequests[0], LegacyRequests[2]))
    )
    Batch = ExecuteNativeRouteBatchOutcomesV1(
        NativeContext,
        "corruption-and-cancellation-controls",
        Requests,
        Scope.DeadlineAtMonotonicSeconds,
    )
    return Plan, Descriptors, Batch.Results, Scope


def test_caller_snapshot_rejects_reordered_or_missing_authority():
    with pytest.raises(ValueError):
        TypedRouteCallerSnapshot(tuple(reversed(Snapshot().Bindings)))
    with pytest.raises(ValueError):
        TypedRouteCallerSnapshot(Snapshot().Bindings[:-1])
    with pytest.raises(TypeError, match="lowercase SHA-256"):
        TypedRouteCallerSnapshot((
            (CALLER_BINDING_NAMES[0], "not-a-sha"),
            *Snapshot().Bindings[1:],
        ))


def test_caller_snapshot_rejects_mutable_binding_pairs():
    MutableBindings = tuple(list(Binding) for Binding in Snapshot().Bindings)
    with pytest.raises(TypeError, match="exact name/identity pairs"):
        TypedRouteCallerSnapshot(MutableBindings)


@pytest.mark.parametrize("Field", ("Context", "CallerSnapshot"))
def test_execution_scope_requires_exact_immutable_nested_authority(Field):
    Scope = TypedRouteExecutionScope(
        AuthorityIdentity({"Invocation": "exact-types"}), 10.0, Context(), Snapshot()
    )
    with pytest.raises(TypeError, match="exact typed"):
        replace(Scope, **{Field: getattr(Scope, Field).ToDictionary()})


def test_execution_scope_keeps_invocation_separate_from_deadline():
    InvocationIdentity = AuthorityIdentity({"Invocation": "fixture"})
    First = TypedRouteExecutionScope(InvocationIdentity, 10.0, Context(), Snapshot())
    Later = TypedRouteExecutionScope(InvocationIdentity, 11.0, Context(), Snapshot())
    assert First.InvocationIdentity == Later.InvocationIdentity
    assert First.DeadlineAtMonotonicSeconds != Later.DeadlineAtMonotonicSeconds


def test_equivalent_origin_cannot_claim_missing_canonical_receipt():
    Equivalent = OriginRecord(
        OriginalOrdinal=1,
        CanonicalOriginalOrdinal=0,
        CanonicalOriginIdentity=AuthorityIdentity({"Origin": 0}),
        NativeOrdinal=None,
    )
    with pytest.raises(ValueError):
        replace(Equivalent, CanonicalReceiptIdentity=None)


def test_counters_reject_nonconserving_or_physical_overclaim():
    with pytest.raises(ValueError):
        TypedRouteBatchCounters(2, 1, 0, 1, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    with pytest.raises(ValueError):
        TypedRouteBatchCounters(1, 1, 0, 1, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1, 1)

    Valid = TypedRouteBatchCounters(
        1, 1, 0, 1, 0, 1, 0, 0, 0, 0, 0, 0, 1, 1, 0
    ).ToDictionary()
    Missing = dict(Valid)
    Missing.pop("Routed")
    with pytest.raises(ValueError, match="missing or extra"):
        TypedRouteBatchCounters.FromDictionary(Missing)
    Corrupt = {**Valid, "PhysicallyAccepted": 2}
    with pytest.raises(ValueError, match="physical admission exceeds"):
        TypedRouteBatchCounters.FromDictionary(Corrupt)


def test_authority_identities_change_for_each_bound_authority_document():
    Authorities = {
        Name: {"SchemaVersion": "fixture-v1", "Name": Name, "Value": 1}
        for Name in CALLER_BINDING_NAMES
    }
    Before = TypedRouteCallerSnapshot.FromAuthorities(Authorities)
    for Name in CALLER_BINDING_NAMES:
        Changed = dict(Authorities)
        Changed[Name] = {**Authorities[Name], "Value": 2}
        After = TypedRouteCallerSnapshot.FromAuthorities(Changed)
        assert Before.Bindings != After.Bindings


def test_context_construction_rejects_unsorted_nodes_or_edges():
    GraphIdentity = AuthorityIdentity({"Graph": "fixture"})
    with pytest.raises(ValueError):
        TypedRouteContextScope.FromConstruction(
            (0, 1, 0, 1, 0, 1),
            (0, 1, 0, 1),
            ((1, 0, 0), (0, 0, 0)),
            (),
            GraphIdentity,
        )
    with pytest.raises(ValueError):
        TypedRouteContextScope.FromConstruction(
            (0, 1, 0, 1, 0, 1),
            (0, 1, 0, 1),
            ((0, 0, 0), (1, 0, 0)),
            (((1, 0, 0), (0, 0, 0)), ((0, 0, 0), (1, 0, 0))),
            GraphIdentity,
        )


def test_equivalent_routed_origin_references_canonical_receipt_without_ordinal_copy():
    CanonicalIdentity = AuthorityIdentity({"Origin": 0})
    Equivalent = OriginRecord(
        OriginalOrdinal=1,
        CanonicalOriginalOrdinal=0,
        CanonicalOriginIdentity=CanonicalIdentity,
        NativeOrdinal=None,
    )
    assert Equivalent.CanonicalExecuted is False
    assert Equivalent.EquivalentReused is True
    assert Equivalent.ToDictionary()["OriginalOrdinal"] == 1
    assert Equivalent.ToDictionary()["CanonicalOriginalOrdinal"] == 0


def test_admission_record_preserves_typed_boundary_and_rejection_identity():
    OriginIdentity = AuthorityIdentity({"Origin": "fixture"})
    DescriptorValue = Descriptor()
    Evidence = TypedRoutePhysicalEvidence(
        Status="Rejected",
        Reason="P1SelfClaimConflict",
        Signal="signal",
        ConflictResources=({"Kind": "Air", "Position": [0, 0, 0]},),
        ConflictingOwners=("signal",),
        ResourceEvidenceAvailable=True,
        OwnerEvidenceAvailable=True,
        ProvenanceEvidenceAvailable=True,
        OriginDescriptorIdentity=DescriptorValue.Identity,
        ImmutableFragmentIdentity=DescriptorValue.ImmutableFragmentIdentity,
        MaterializationDiagnostics={"Status": "self-claim-conflict"},
    )
    Record = TypedRouteAdmissionRecord(
        OriginIdentity,
        "Routed",
        False,
        Evidence,
        None,
    )
    assert Record.ToDictionary()["PhysicalEvidenceIdentity"] == Evidence.Identity
    assert Record.ToDictionary()["RecoveryAttribution"] is None
    with pytest.raises(ValueError):
        TypedRouteAdmissionRecord(OriginIdentity, "DeadlineIncomplete", False, None, None)


def test_disabled_selected_access_has_its_own_immutable_authority_document():
    Disabled = BuildDisabledSelectedAccessAuthority()
    assert Disabled == {
        "SchemaVersion": "joint-typed-route-selected-access-disabled-v1",
        "Enabled": False,
    }
    assert AuthorityIdentity(Disabled) != AuthorityIdentity({"Enabled": True})


@pytest.mark.parametrize(
    ("Field", "Value"),
    (
        ("Bounds", (0, 2, 0, 1, 0, 1)),
        ("PlacementBounds", (0, 2, 0, 1)),
        ("ContextGraphIdentity", AuthorityIdentity({"Graph": "changed"})),
        ("NodeIdentity", AuthorityIdentity({"Nodes": "changed"})),
        ("EdgeIdentity", AuthorityIdentity({"Edges": "changed"})),
    ),
)
def test_each_current_context_construction_input_changes_scope_identity(
    Field,
    Value,
):
    assert replace(Context(), **{Field: Value}).Identity != Context().Identity


def test_same_native_graph_with_changed_cached_bounds_gets_new_joint_scope():
    Nodes = ((0, 0, 0), (1, 0, 0))
    Edges = (((0, 0, 0), (1, 0, 0)),)
    FirstBounds = (0, 1, 0, 1, 0, 1)
    ChangedBounds = (0, 2, 0, 1, 0, 1)
    PlacementBounds = (0, 1, 0, 1)
    Cached = RustRouting.RoutingContext(
        FirstBounds,
        PlacementBounds,
        list(Nodes),
        list(Edges),
    )
    Current = RustRouting.RoutingContext(
        ChangedBounds,
        PlacementBounds,
        list(Nodes),
        list(Edges),
    )
    assert (
        Cached.AuthoritativeContextGraphSha256
        == Current.AuthoritativeContextGraphSha256
    )

    CachedScope = TypedRouteContextScope.FromConstruction(
        FirstBounds,
        PlacementBounds,
        Nodes,
        Edges,
        Cached.AuthoritativeContextGraphSha256,
    )
    CurrentScope = TypedRouteContextScope.FromConstruction(
        ChangedBounds,
        PlacementBounds,
        Nodes,
        Edges,
        Current.AuthoritativeContextGraphSha256,
    )
    assert CachedScope.Identity != CurrentScope.Identity


@pytest.mark.parametrize(
    ("Field", "Value"),
    (
        ("SourcePortal", {"Path": [[0, 0, 1]], "PortalId": "source"}),
        (
            "TargetPortals",
            ({"Path": [[2, 0, 0]], "PortalId": "target"},),
        ),
        ("Guide", ((0, 0), (2, 0))),
        ("Layer", 1),
        ("Axis", "Z"),
        ("Lane", 1),
        ("Variant", 1),
        (
            "ImmutableFragments",
            {"SchemaVersion": "fixture-fragments-v1", "Value": "changed"},
        ),
    ),
)
def test_each_origin_metadata_or_fragment_mutation_changes_origin_identity(
    Field,
    Value,
):
    Before = Descriptor()
    After = replace(Before, **{Field: Value})
    assert Before.Identity != After.Identity
    if Field == "ImmutableFragments":
        assert (
            Before.ImmutableFragmentIdentity
            != After.ImmutableFragmentIdentity
        )


def test_origin_descriptor_preserves_signed_existing_lane_value():
    Value = replace(Descriptor(), Lane=-1)
    assert Value.Lane == -1
    assert Value.ToDictionary()["Lane"] == -1


def test_origin_descriptor_owns_and_freezes_every_nested_document():
    Source = {"Path": [[0, 0, 0]], "Metadata": {"Owners": ["source"]}}
    Target = {"Path": [[1, 0, 0]], "Metadata": {"Owners": ["target"]}}
    Fragments = {"Fragments": [{"Path": [[2, 0, 0]]}]}
    Value = replace(
        Descriptor(),
        SourcePortal=Source,
        TargetPortals=(Target,),
        ImmutableFragments=Fragments,
    )
    Expected = Value.ToDictionary()
    Identity = Value.Identity
    FragmentIdentity = Value.ImmutableFragmentIdentity

    Source["Path"][0][0] = 40
    Source["Metadata"]["Owners"].append("changed")
    Target["Metadata"]["Owners"][0] = "changed"
    Fragments["Fragments"][0]["Path"].clear()
    Projection = Value.ToDictionary()
    Projection["SourcePortal"]["Metadata"]["Owners"].clear()
    Projection["TargetPortals"][0]["Path"][0][0] = 50
    Projection["ImmutableFragments"]["Fragments"].append({"Changed": True})

    assert Value.ToDictionary() == Expected
    assert Value.Identity == Identity
    assert Value.ImmutableFragmentIdentity == FragmentIdentity
    assert replace(Value).ToDictionary() == Expected
    with pytest.raises(TypeError):
        Value.SourcePortal["Path"][0][0] = 60
    with pytest.raises(TypeError):
        Value.TargetPortals[0]["Metadata"]["Owners"] = []
    with pytest.raises(TypeError):
        Value.ImmutableFragments["Fragments"][0]["Path"] = []


def test_physical_evidence_owns_and_freezes_conflicts_and_diagnostics():
    DescriptorValue = Descriptor()
    Resource = {"Kind": "Air", "Position": [0, 0, 0]}
    Diagnostics = {"Materialization": [{"Reasons": ["self-claim-conflict"]}]}
    Evidence = TypedRoutePhysicalEvidence(
        Status="Rejected",
        Reason="P1SelfClaimConflict",
        Signal="signal",
        ConflictResources=(Resource,),
        ConflictingOwners=("signal",),
        ResourceEvidenceAvailable=True,
        OwnerEvidenceAvailable=True,
        ProvenanceEvidenceAvailable=True,
        OriginDescriptorIdentity=DescriptorValue.Identity,
        ImmutableFragmentIdentity=DescriptorValue.ImmutableFragmentIdentity,
        MaterializationDiagnostics=Diagnostics,
    )
    Expected = Evidence.ToDictionary()
    Identity = Evidence.Identity

    Resource["Position"][0] = 1
    Diagnostics["Materialization"][0]["Reasons"].clear()
    Projection = Evidence.ToDictionary()
    Projection["ConflictResources"][0]["Position"].append(4)
    Projection["MaterializationDiagnostics"]["Materialization"].clear()

    assert Evidence.Identity == Identity
    assert Evidence.ToDictionary() == Expected
    assert replace(Evidence).ToDictionary() == Expected
    with pytest.raises(TypeError):
        Evidence.ConflictResources[0]["Position"][0] = 10
    with pytest.raises(TypeError):
        Evidence.MaterializationDiagnostics["Materialization"][0]["Reasons"] = []


def test_deeply_immutable_identity_caches_never_rehash_or_escape_into_documents(monkeypatch):
    Value = Descriptor()
    DescriptorDocument = CanonicalJson(Value)
    DescriptorIdentity = Value.Identity
    FragmentIdentity = Value.ImmutableFragmentIdentity
    Evidence = TypedRoutePhysicalEvidence(
        Status="Accepted",
        Reason="P1PhysicalAdmissionAccepted",
        Signal="signal",
        ConflictResources=(),
        ConflictingOwners=(),
        ResourceEvidenceAvailable=False,
        OwnerEvidenceAvailable=False,
        ProvenanceEvidenceAvailable=True,
        OriginDescriptorIdentity=DescriptorIdentity,
        ImmutableFragmentIdentity=FragmentIdentity,
        MaterializationDiagnostics={"Status": "accepted"},
    )
    EvidenceDocument = CanonicalJson(Evidence)
    EvidenceIdentity = Evidence.Identity

    def UnexpectedRehash(_Value):
        pytest.fail("deeply immutable identity was recalculated")

    with monkeypatch.context() as Patch:
        Patch.setattr(
            "PhysicalDesign.Routing.Global.TypedRouteConsumer.AuthorityIdentity",
            UnexpectedRehash,
        )
        Value.ToDictionary()["SourcePortal"]["Path"][0][0] = 10
        Evidence.ToDictionary()["MaterializationDiagnostics"].clear()
        for _ in range(3):
            assert Value.Identity == DescriptorIdentity
            assert Value.ImmutableFragmentIdentity == FragmentIdentity
            assert Evidence.Identity == EvidenceIdentity
        assert CanonicalJson(Value) == DescriptorDocument
        assert CanonicalJson(Evidence) == EvidenceDocument

    with pytest.raises(FrozenInstanceError):
        Value.Identity = "changed"
    with pytest.raises(FrozenInstanceError):
        Value.ImmutableFragmentIdentity = "changed"
    with pytest.raises(FrozenInstanceError):
        Evidence.Identity = "changed"
    assert replace(Value, Variant=1).Identity != DescriptorIdentity
    assert replace(Evidence, Reason="OtherAcceptedReason").Identity != EvidenceIdentity


@pytest.mark.parametrize(
    ("Value", "Expected"),
    (
        (
            {"z": [3, (2, 1)], "a": {"B": True, "A": None}},
            '{"a":{"A":null,"B":true},"z":[3,[2,1]]}',
        ),
        (
            MappingProxyType({"z": (3, [2, 1]), "a": False}),
            '{"a":false,"z":[3,[2,1]]}',
        ),
        (
            {(2, 1): {"v": [9]}, 7: "seven", "name": "label"},
            '{"Entries":[{"Key":"name","Value":"label"},'
            '{"Key":7,"Value":"seven"},{"Key":[2,1],"Value":{"v":[9]}}],'
            '"SchemaVersion":"joint-canonical-authority-map-v1"}',
        ),
        (
            PurePosixPath("/source/model.json"),
            '{"SchemaVersion":"joint-canonical-source-path-v1",'
            '"Value":"/source/model.json"}',
        ),
        ({(2, 1), (0, 3)}, '[[0,3],[2,1]]'),
        (frozenset(("b", "a")), '["a","b"]'),
    ),
)
def test_canonical_projection_optimization_preserves_exact_document_bytes(Value, Expected):
    assert CanonicalJson(Value) == Expected


def test_canonical_projection_keeps_dataclass_and_explicit_projection_precedence():
    class Choice(Enum):
        Selected = "selected"

    @dataclass
    class NamedFields:
        ChoiceValue: Choice
        Coordinates: tuple[int, ...]

    @dataclass
    class ExplicitProjection:
        IgnoredField: str

        def ToDictionary(self):
            return {"Projected": ["authoritative"]}

    assert CanonicalJson(NamedFields(Choice.Selected, (1, 2))) == (
        '{"ChoiceValue":"selected","Coordinates":[1,2]}'
    )
    assert CanonicalJson(ExplicitProjection("not authority")) == (
        '{"Projected":["authoritative"]}'
    )
    Frozen = Descriptor().SourcePortal
    assert CanonicalAuthority(Frozen) == {"Path": [[0, 0, 0]], "PortalId": "source"}
    assert CanonicalJson(Frozen) == '{"Path":[[0,0,0]],"PortalId":"source"}'


def test_mutable_authority_documents_are_never_cached_by_object_identity():
    Value = {"Nested": [1, {"State": "before"}]}
    Before = AuthorityIdentity(Value)
    Value["Nested"][1]["State"] = "after"
    assert CanonicalJson(Value) == '{"Nested":[1,{"State":"after"}]}'
    assert AuthorityIdentity(Value) != Before


@pytest.mark.parametrize("Kind", ("Routed", "CancellationIncomplete", "NativeFailure"))
def test_every_canonical_native_record_requires_its_native_ordinal(Kind):
    with pytest.raises(ValueError, match="must own its native ordinal"):
        OriginRecord(NativeKind=Kind, NativeOrdinal=None)


def test_equal_native_geometry_keeps_distinct_origin_fragments_and_one_ordinal_owner():
    Canonical = OriginRecord(DescriptorValue=Descriptor(Fragment="first"))
    EquivalentDescriptor = Descriptor(Fragment="second")
    EquivalentIdentity = AuthorityIdentity({
        "Origin": 1,
        "Descriptor": EquivalentDescriptor.Identity,
    })
    Equivalent = OriginRecord(
        OriginalOrdinal=1,
        CanonicalOriginalOrdinal=0,
        CanonicalOriginIdentity=Canonical.OriginIdentity,
        NativeOrdinal=None,
        DescriptorValue=EquivalentDescriptor,
    )

    assert Canonical.GeometryIdentity == Equivalent.GeometryIdentity
    assert Canonical.OriginIdentity != EquivalentIdentity
    assert Canonical.NativeOrdinal == 0
    assert Equivalent.NativeOrdinal is None
    assert Equivalent.CanonicalReceiptIdentity == Canonical.CanonicalReceiptIdentity
    assert (
        Equivalent.OriginDescriptor.ImmutableFragmentIdentity
        != Canonical.OriginDescriptor.ImmutableFragmentIdentity
    )
    with pytest.raises(ValueError, match="cannot copy a native ordinal"):
        replace(Equivalent, NativeOrdinal=0)


def test_pre_native_origin_cannot_fabricate_request_receipt_or_scope():
    DescriptorValue = Descriptor()
    Identity = AuthorityIdentity({"PreNative": 0})
    Record = TypedRouteOriginRecord(
        OriginIdentity=Identity,
        OriginDescriptor=DescriptorValue,
        OriginalOrdinal=0,
        CanonicalOriginalOrdinal=0,
        CanonicalOriginIdentity=Identity,
        CanonicalRequestId=None,
        CanonicalReceiptIdentity=None,
        ExecutionScopeIdentity=None,
        GeometryIdentity=AuthorityIdentity({"Geometry": 0}),
        NativePayloadIdentity=None,
        RouteDomainIdentity=None,
        NativeOrdinal=None,
        NativeKind="PreNativeIncomplete",
        PreNativeReason="DeadlineBeforeCurrentContextConstruction",
        ExpansionCap=4,
        ActualExpansionCount=0,
        CancellationRequestedBeforeStart=False,
        DeadlineAtMonotonicSeconds=None,
    )

    assert Record.CanonicalExecuted is False
    assert Record.EquivalentReused is False
    with pytest.raises(ValueError, match="cannot claim native execution scope"):
        replace(Record, CanonicalRequestId="fabricated")
    with pytest.raises(ValueError, match="cannot claim native execution scope"):
        replace(Record, NativeOrdinal=0)


def test_recovery_requires_exact_owner_and_provenance_evidence():
    DescriptorValue = Descriptor()
    Evidence = TypedRoutePhysicalEvidence(
        Status="Rejected",
        Reason="ForeignSelectedAccessConflict",
        Signal="signal",
        ConflictResources=(),
        ConflictingOwners=(),
        ResourceEvidenceAvailable=False,
        OwnerEvidenceAvailable=False,
        ProvenanceEvidenceAvailable=True,
        OriginDescriptorIdentity=DescriptorValue.Identity,
        ImmutableFragmentIdentity=DescriptorValue.ImmutableFragmentIdentity,
        MaterializationDiagnostics={"Status": "candidate-built"},
    )
    Recovery = TypedRouteRecoveryAttribution(
        Action="DirectOnlyRepair",
        EvidenceIdentity=Evidence.Identity,
        Owner="foreign",
        ScopeIdentity=AuthorityIdentity({"Scope": "repair"}),
    )

    with pytest.raises(ValueError, match="lacks owner/provenance proof"):
        TypedRouteAdmissionRecord(
            AuthorityIdentity({"Origin": "recovery"}),
            "Routed",
            False,
            Evidence,
            Recovery,
        )


def test_invocation_local_plan_executes_equal_payload_once_and_keeps_origins():
    Invocation = AuthorityIdentity({"Invocation": 1})
    ContextIdentity = Context().Identity
    CallerIdentity = Snapshot().Identity
    FirstDescriptor = Descriptor(Fragment="first")
    SecondDescriptor = Descriptor(Fragment="second")

    Plan = BuildTypedRouteExecutionPlan(
        Invocation,
        ContextIdentity,
        CallerIdentity,
        (
            (LegacyRequest(), FirstDescriptor),
            (LegacyRequest(), SecondDescriptor),
        ),
    )

    assert tuple(Value.Canonical for Value in Plan) == (True, False)
    assert tuple(Value.RepresentativeIndex for Value in Plan) == (0, 0)
    assert tuple(Value.CanonicalOriginalOrdinal for Value in Plan) == (0, 0)
    assert Plan[0].ExecutionIdentity == Plan[1].ExecutionIdentity
    assert Plan[0].GeometryIdentity == Plan[1].GeometryIdentity
    assert Plan[0].OriginIdentity != Plan[1].OriginIdentity


def test_execution_plan_first_representative_follows_each_input_permutation():
    Invocation = AuthorityIdentity({"Invocation": "permutation"})
    ContextIdentity = Context().Identity
    CallerIdentity = Snapshot().Identity
    Descriptors = (
        Descriptor(Fragment="alpha"),
        Descriptor(Fragment="beta"),
    )
    Results = []
    for Ordered in (Descriptors, tuple(reversed(Descriptors))):
        Plan = BuildTypedRouteExecutionPlan(
            Invocation,
            ContextIdentity,
            CallerIdentity,
            tuple((LegacyRequest(), Value) for Value in Ordered),
        )
        Results.append(Plan)
        assert sum(Value.Canonical for Value in Plan) == 1
        assert Plan[0].Canonical is True
        assert Plan[1].CanonicalOriginalOrdinal == 0
    assert (
        Results[0][0].OriginIdentity
        != Results[1][0].OriginIdentity
    )


def test_cap_and_cancel_change_execution_scope_but_deadline_does_not_change_plan():
    Invocation = AuthorityIdentity({"Invocation": "controls"})
    ContextIdentity = Context().Identity
    CallerIdentity = Snapshot().Identity
    Origins = ((LegacyRequest(), Descriptor()),)
    Base = BuildTypedRouteExecutionPlan(
        Invocation,
        ContextIdentity,
        CallerIdentity,
        Origins,
    )[0]
    ChangedCap = BuildTypedRouteExecutionPlan(
        Invocation,
        ContextIdentity,
        CallerIdentity,
        ((LegacyRequest(ExpansionCap=33), Descriptor()),),
    )[0]
    Cancelled = BuildTypedRouteExecutionPlan(
        Invocation,
        ContextIdentity,
        CallerIdentity,
        Origins,
        CancellationRequestedBeforeStart=True,
    )[0]
    FirstDeadline = TypedRouteExecutionScope(
        Invocation,
        10.0,
        Context(),
        Snapshot(),
    )
    SecondDeadline = replace(
        FirstDeadline,
        DeadlineAtMonotonicSeconds=11.0,
    )

    assert Base.ExecutionIdentity != ChangedCap.ExecutionIdentity
    assert Base.ExecutionIdentity != Cancelled.ExecutionIdentity
    assert FirstDeadline.InvocationIdentity == SecondDeadline.InvocationIdentity
    assert FirstDeadline.Identity != SecondDeadline.Identity


@pytest.mark.parametrize(
    ("Kind", "Completed"),
    (
        ("Routed", True),
        ("CompleteScopedNoPath", True),
        ("SearchLimitIncomplete", False),
        ("DeadlineIncomplete", False),
        ("CancellationIncomplete", False),
        ("NativeFailure", False),
        ("PreNativeIncomplete", False),
    ),
)
def test_only_routed_or_scoped_no_path_completes_one_request(Kind, Completed):
    assert TypedRouteTerminalCompletesRequest(Kind) is Completed


def test_disconnected_start_connection_stays_unresolved_without_no_path_proof():
    Bounds = (0, 2, 0, 0, 0, 0)
    PlacementBounds = (0, 2, 0, 0)
    ContextValue = RustRouting.RoutingContext(
        Bounds,
        PlacementBounds,
        [(0, 0, 0), (2, 0, 0)],
        [],
    )
    Bindings = Snapshot().Bindings
    Request = RustRouting.RouteTreeCoarseRequestV1.ConnectStartsOnlyV1(
        "disconnected-starts",
        Bindings,
        Bounds,
        PlacementBounds,
        False,
        [(0, 0, 0), (2, 0, 0)],
        [(0, 0), (2, 0)],
        [(0, 0, 0), (2, 0, 0)],
        [],
        [(0, 0), (2, 0)],
        0,
        1,
        1,
        1,
        100,
    )

    Result = ExecuteNativeRouteBatchOutcomesV1(
        ContextValue,
        "disconnected-start-connection-consumer",
        (Request,),
        monotonic() + 10.0,
    ).Results[0]

    assert Result.Kind is NativeRouteResultKind.NativeFailure
    assert Result.Reason == "StartConnectionIncomplete"
    assert Result.Candidate is None
    assert Result.CompleteScopedNoPathProof is None
    assert TypedRouteTerminalCompletesRequest(Result.Kind.value) is False


def test_exact_legacy_target_shape_selects_only_its_declared_typed_intent():
    Bounds = (0, 2, 0, 0, 0, 0)
    PlacementBounds = (0, 2, 0, 0)
    Targetless = BuildTypedNativeCoarseRequest(
        "targetless",
        Snapshot().Bindings,
        Bounds,
        PlacementBounds,
        LegacyRequest(),
    )
    TargetedLegacy = list(LegacyRequest())
    TargetedLegacy[1] = [[(2, 0, 0)]]
    TargetedLegacy[2] = [(0, 0), (1, 0), (2, 0)]
    TargetedLegacy[5] = [(0, 0), (1, 0), (2, 0)]
    Targeted = BuildTypedNativeCoarseRequest(
        "targeted",
        Snapshot().Bindings,
        Bounds,
        PlacementBounds,
        tuple(TargetedLegacy),
    )

    assert Targetless.RequestKind == "CoarseStartConnectionV1"
    assert Targetless.ConnectionIntent == "ConnectStartsOnlyV1"
    assert Targetless.CancellationRequestedBeforeStart is False
    assert Targeted.RequestKind == "CoarseColumnsV1"
    assert Targeted.ConnectionIntent == "RequiredTargetBranchesV1"
    assert Targeted.CancellationRequestedBeforeStart is False

    ContextValue = RustRouting.RoutingContext(
        Bounds,
        PlacementBounds,
        [(0, 0, 0), (1, 0, 0), (2, 0, 0)],
        [
            ((0, 0, 0), (1, 0, 0)),
            ((1, 0, 0), (2, 0, 0)),
        ],
    )
    Result = ExecuteNativeRouteBatchOutcomesV1(
        ContextValue,
        "real-targeted-production-construction-control",
        (Targeted,),
        monotonic() + 10.0,
    ).Results[0]
    assert Result.Kind is NativeRouteResultKind.Routed
    assert Result.NativeReceipt.Candidate.Nodes == [
        (0, 0, 0),
        (1, 0, 0),
        (2, 0, 0),
    ]
    assert Result.NativeReceipt.Candidate.TargetPaths == [(
        (2, 0, 0),
        [
            (0, 0, 0),
            (1, 0, 0),
            (2, 0, 0),
        ],
    )]


def test_equivalent_real_routes_execute_once_and_admit_each_origin_separately():
    Bounds = (0, 2, 0, 0, 0, 0)
    PlacementBounds = (0, 2, 0, 0)
    ContextValue = RustRouting.RoutingContext(
        Bounds,
        PlacementBounds,
        [(0, 0, 0), (1, 0, 0), (2, 0, 0)],
        [
            ((0, 0, 0), (1, 0, 0)),
            ((1, 0, 0), (2, 0, 0)),
        ],
    )
    ContextScope = TypedRouteContextScope.FromConstruction(
        Bounds,
        PlacementBounds,
        ((0, 0, 0), (1, 0, 0), (2, 0, 0)),
        (
            ((0, 0, 0), (1, 0, 0)),
            ((1, 0, 0), (2, 0, 0)),
        ),
        ContextValue.AuthoritativeContextGraphSha256,
    )
    Invocation = AuthorityIdentity({"Invocation": "real-equivalent"})
    ExecutionScope = TypedRouteExecutionScope(
        Invocation,
        monotonic() + 10.0,
        ContextScope,
        Snapshot(),
    )
    Request = list(LegacyRequest())
    Request[0] = [(0, 0, 0), (2, 0, 0)]
    Request[2] = [(0, 0), (1, 0), (2, 0)]
    Request[3] = [(0, 0, 0), (2, 0, 0)]
    Request[5] = [(0, 0), (1, 0), (2, 0)]
    Request = tuple(Request)
    Descriptors = (
        Descriptor(Fragment="admitted"),
        Descriptor(Fragment="rejected"),
    )
    Plan = BuildTypedRouteExecutionPlan(
        Invocation,
        ContextScope.Identity,
        Snapshot().Identity,
        tuple((Request, DescriptorValue) for DescriptorValue in Descriptors),
    )
    TypedRequest = BuildTypedNativeCoarseRequest(
        "real-equivalent-canonical",
        Snapshot().Bindings,
        Bounds,
        PlacementBounds,
        Request,
    )
    NativeBatch = ExecuteNativeRouteBatchOutcomesV1(
        ContextValue,
        "real-equivalent-one-execution",
        (TypedRequest,),
        ExecutionScope.DeadlineAtMonotonicSeconds,
    )
    Records = BuildTypedRouteOriginRecordsFromResults(
        Plan,
        Descriptors,
        NativeBatch.Results,
        ExecutionScope,
    )

    assert NativeBatch.NativeBatch.StartedRequestCount == 1
    assert tuple(Record.NativeOrdinal for Record in Records) == (0, None)
    assert Records[0].CanonicalReceiptIdentity == (
        Records[1].CanonicalReceiptIdentity
    )
    assert Records[0].OriginIdentity != Records[1].OriginIdentity
    assert (
        Records[0].OriginDescriptor.ImmutableFragmentIdentity
        != Records[1].OriginDescriptor.ImmutableFragmentIdentity
    )

    AcceptedEvidence = TypedRoutePhysicalEvidence(
        Status="Accepted",
        Reason="P1PhysicalAdmissionAccepted",
        Signal="signal",
        ConflictResources=(),
        ConflictingOwners=(),
        ResourceEvidenceAvailable=False,
        OwnerEvidenceAvailable=False,
        ProvenanceEvidenceAvailable=True,
        OriginDescriptorIdentity=Descriptors[0].Identity,
        ImmutableFragmentIdentity=Descriptors[0].ImmutableFragmentIdentity,
        MaterializationDiagnostics={"Status": "accepted"},
    )
    RejectedEvidence = TypedRoutePhysicalEvidence(
        Status="Rejected",
        Reason="P1SelfClaimConflict",
        Signal="signal",
        ConflictResources=({"Kind": "Air", "Position": [1, 0, 0]},),
        ConflictingOwners=("signal",),
        ResourceEvidenceAvailable=True,
        OwnerEvidenceAvailable=True,
        ProvenanceEvidenceAvailable=True,
        OriginDescriptorIdentity=Descriptors[1].Identity,
        ImmutableFragmentIdentity=Descriptors[1].ImmutableFragmentIdentity,
        MaterializationDiagnostics={"Status": "self-claim-conflict"},
    )
    Admissions = (
        TypedRouteAdmissionRecord(
            Records[0].OriginIdentity,
            "Routed",
            True,
            AcceptedEvidence,
            None,
        ),
        TypedRouteAdmissionRecord(
            Records[1].OriginIdentity,
            "Routed",
            False,
            RejectedEvidence,
            None,
        ),
    )
    assert tuple(Value.Admitted for Value in Admissions) == (True, False)
    assert all(Value.RecoveryAttribution is None for Value in Admissions)


@pytest.mark.parametrize("Targeted", (False, True))
def test_real_precancelled_native_receipts_keep_cancellation_without_geometry_or_proof(
    Targeted,
):
    Plan, Descriptors, Results, Scope = ExecutedOriginFixture(
        CancellationRequestedBeforeStart=True,
        Targeted=Targeted,
    )
    Records = BuildTypedRouteOriginRecordsFromResults(
        Plan, Descriptors, Results, Scope
    )

    assert tuple(Value.NativeOrdinal for Value in Records) == (0, None, 1)
    assert all(Value.CancellationRequestedBeforeStart is True for Value in Records)
    assert all(Value.NativeKind == "CancellationIncomplete" for Value in Records)
    assert all(Value.ActualExpansionCount == 0 for Value in Records)
    assert Records[0].CanonicalReceiptIdentity == Records[1].CanonicalReceiptIdentity
    for Result in Results:
        assert Result.Kind is NativeRouteResultKind.CancellationIncomplete
        assert Result.NativeReceipt.CancellationRequested is True
        assert Result.NativeReceipt.CancellationAcknowledged is True
        assert Result.NativeReceipt.Started is False
        assert Result.NativeReceipt.Candidate is None
        assert Result.NativeReceipt.NoPathProof is None
        assert Result.Candidate is None
        assert Result.CompleteScopedNoPathProof is None
        assert TypedRouteTerminalCompletesRequest(Result.Kind.value) is False


@pytest.mark.parametrize("Targeted", (False, True))
@pytest.mark.parametrize("InvalidCancellation", (None, 0, 1, "true"))
def test_native_request_builder_requires_an_exact_cancellation_bool(
    Targeted,
    InvalidCancellation,
):
    Request = list(LegacyRequest())
    if Targeted:
        Request[1] = [[(1, 0, 0)]]
    with pytest.raises(TypeError, match="CancellationRequestedBeforeStart.*exact bool"):
        BuildTypedNativeCoarseRequest(
            "invalid-cancellation",
            Snapshot().Bindings,
            (0, 2, 0, 0, 0, 0),
            (0, 2, 0, 0),
            tuple(Request),
            CancellationRequestedBeforeStart=InvalidCancellation,
        )


def test_two_canonical_results_bind_their_own_geometry_and_equivalent_origin():
    Plan, Descriptors, Results, Scope = ExecutedOriginFixture()
    Records = BuildTypedRouteOriginRecordsFromResults(
        Plan, Descriptors, Results, Scope
    )

    assert Results[0].Candidate.Nodes == [(0, 0, 0)]
    assert Results[1].Candidate.Nodes == [(2, 0, 0)]
    assert Plan[0].GeometryIdentity != Plan[2].GeometryIdentity
    assert tuple(Value.NativeOrdinal for Value in Records) == (0, None, 1)
    assert tuple(Value.CanonicalRequestId for Value in Records) == (
        "native-canonical-0", "native-canonical-0", "native-canonical-1"
    )
    assert Records[0].CanonicalReceiptIdentity == Results[0].ExecutionScope.ReceiptIdentity
    assert Records[1].CanonicalReceiptIdentity == Results[0].ExecutionScope.ReceiptIdentity
    assert Records[2].CanonicalReceiptIdentity == Results[1].ExecutionScope.ReceiptIdentity
    assert all(Value.CancellationRequestedBeforeStart is False for Value in Records)


@pytest.mark.parametrize(
    "Corruption",
    (
        "reordered-originals",
        "duplicate-original-ordinal",
        "equivalent-wrong-result",
        "equivalent-wrong-canonical-origin",
        "equivalent-wrong-geometry",
        "equivalent-wrong-execution",
        "canonical-duplicate-index",
        "canonical-skipped-index",
        "canonical-wrong-origin",
    ),
)
def test_origin_records_reject_corrupted_canonical_mapping(Corruption):
    Plan, Descriptors, Results, Scope = ExecutedOriginFixture()
    Corrupt = list(Plan)
    if Corruption == "reordered-originals":
        Corrupt[0], Corrupt[1] = Corrupt[1], Corrupt[0]
    elif Corruption == "duplicate-original-ordinal":
        Corrupt[1] = replace(Corrupt[1], OriginalOrdinal=0)
    elif Corruption == "equivalent-wrong-result":
        Corrupt[1] = replace(Corrupt[1], RepresentativeIndex=1)
    elif Corruption == "equivalent-wrong-canonical-origin":
        Corrupt[1] = replace(Corrupt[1], CanonicalOriginalOrdinal=1)
    elif Corruption == "equivalent-wrong-geometry":
        Corrupt[1] = replace(Corrupt[1], GeometryIdentity=Plan[2].GeometryIdentity)
    elif Corruption == "equivalent-wrong-execution":
        Corrupt[1] = replace(Corrupt[1], ExecutionIdentity=Plan[2].ExecutionIdentity)
    elif Corruption == "canonical-duplicate-index":
        Corrupt[2] = replace(Corrupt[2], RepresentativeIndex=0)
    elif Corruption == "canonical-skipped-index":
        Corrupt[2] = replace(Corrupt[2], RepresentativeIndex=2)
    elif Corruption == "canonical-wrong-origin":
        Corrupt[2] = replace(Corrupt[2], CanonicalOriginalOrdinal=0)

    with pytest.raises(ValueError):
        BuildTypedRouteOriginRecordsFromResults(
            tuple(Corrupt), Descriptors, Results, Scope
        )


@pytest.mark.parametrize("Corruption", ("missing", "extra", "reordered", "duplicate"))
def test_origin_records_require_the_exact_canonical_native_result_set(Corruption):
    Plan, Descriptors, Results, Scope = ExecutedOriginFixture()
    if Corruption == "missing":
        Results = Results[:-1]
    elif Corruption == "extra":
        Results = (*Results, Results[0])
    elif Corruption == "reordered":
        Results = tuple(reversed(Results))
    elif Corruption == "duplicate":
        Results = (Results[0], Results[0])

    with pytest.raises(ValueError, match="native result set"):
        BuildTypedRouteOriginRecordsFromResults(Plan, Descriptors, Results, Scope)


@pytest.mark.parametrize("Corruption", ("descriptor", "invocation", "origin-hash"))
def test_origin_records_reject_unbound_descriptor_or_invocation(Corruption):
    Plan, Descriptors, Results, Scope = ExecutedOriginFixture()
    if Corruption == "descriptor":
        Descriptors = (Descriptors[0], Descriptor(Variant=10), Descriptors[2])
    elif Corruption == "invocation":
        Scope = replace(Scope, InvocationIdentity=AuthorityIdentity({"Invocation": "other"}))
    elif Corruption == "origin-hash":
        Plan = (Plan[0], replace(Plan[1], OriginIdentity=Plan[0].OriginIdentity), Plan[2])

    with pytest.raises(ValueError, match="descriptor and invocation"):
        BuildTypedRouteOriginRecordsFromResults(Plan, Descriptors, Results, Scope)


@pytest.mark.parametrize("Corruption", ("context", "deadline"))
def test_origin_records_reject_native_results_from_another_execution_scope(Corruption):
    Plan, Descriptors, Results, Scope = ExecutedOriginFixture()
    if Corruption == "context":
        Scope = replace(Scope, Context=replace(
            Scope.Context,
            ContextGraphIdentity=AuthorityIdentity({"Graph": "other"}),
        ))
    elif Corruption == "deadline":
        Scope = replace(Scope, DeadlineAtMonotonicSeconds=Scope.DeadlineAtMonotonicSeconds + 1.0)

    with pytest.raises(ValueError, match="current execution scope"):
        BuildTypedRouteOriginRecordsFromResults(Plan, Descriptors, Results, Scope)
