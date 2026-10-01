"""Current preparation re-attestation for the typed coarse routing consumer."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum
from math import isfinite
from pathlib import PurePath

from ..TypedRouteConsumer import (
    AuthorityIdentity,
    BuildDisabledSelectedAccessAuthority,
    CALLER_BINDING_NAMES,
    CanonicalJson,
    TypedRouteCallerSnapshot,
    TypedRouteContextScope,
)
from .RunState import AuthoritativeRoutingServices, AuthoritativeRoutingState
import PhysicalDesign.Contracts.PlacementAccess as AccessContracts
from PhysicalDesign.Policy import PhysicalDesignPolicy, RuntimeExecutionPolicy


# Explicit audited producers: their public documents derive only from declared
# fields. New custom producers must retain full ToDictionary observation until
# their projection has been reviewed; module membership alone grants no shortcut.
_FieldProjectedProducers = frozenset((
    AccessContracts.PhysicalPinAccessTemplate,
    AccessContracts.PlacedPinAccessOption,
    AccessContracts.PlacedPinAccessRejectionOwner,
    AccessContracts.PlacedPinAccessRejectionFact,
    AccessContracts.PlacedPinAccessRejectionEvidence,
    AccessContracts.PlacementAccessEvaluationControls,
    AccessContracts.PlacedPinAccessPatternRequirement,
    AccessContracts.PlacedPinAccessPatternAttempt,
    AccessContracts.PlacedPinAccessOptionDomain,
    AccessContracts.SelectedPlacementPinAccessWitness,
    AccessContracts.CurrentSelectedPlacementAccessInputIdentity,
    AccessContracts.CurrentSelectedPlacementAccessValidation,
    AccessContracts.PlacementAccessConflictCore,
    AccessContracts.PlacementAccessSolveResult,
    AccessContracts.PlacementAccessCellTransform,
    AccessContracts.PlacementAccessPinMapping,
    AccessContracts.PlacementAccessBoundaryLease,
    AccessContracts.PlacementAccessChannelReservation,
    AccessContracts.PlacementAccessEnvelope,
    AccessContracts.FrozenPhysicalPlacementContract,
    RuntimeExecutionPolicy,
    PhysicalDesignPolicy,
))


def ObserveTypedRouteAuthorityValue(Value, ImmutableCache=None):
    """Observe current mutable values; reuse only certified immutable subtrees.

    Tuple/frozenset entries are cached only when every descendant is an exact
    immutable builtin. No dataclass, enum, path, custom producer, mapping proxy,
    dict, list or set is trusted by object identity across observations.
    """
    ImmutableCache = {} if ImmutableCache is None else ImmutableCache
    Memo = {}

    def Observe(Item):
        Kind = type(Item)
        if Kind is float:
            if not isfinite(Item):
                raise TypeError("authority float must be finite")
            return (Kind, Item.hex()), True
        if Item is None or Kind in (bool, int, str):
            return (Kind, Item), True
        Identity = id(Item)
        Cached = ImmutableCache.get(Identity)
        if Cached is not None and Cached[0] is Item:
            return Cached[1], True
        if Identity in Memo:
            return Memo[Identity][1:]
        Immutable = False
        if Kind is tuple and all(type(Child) is int for Child in Item):
            # Coordinate tuples are already exact immutable typed values.
            Result, Immutable = (Kind, Item), True
        elif Kind in (tuple, list):
            Children = tuple(Observe(Child) for Child in Item)
            Result = (Kind, tuple(Child[0] for Child in Children))
            Immutable = Kind is tuple and all(Child[1] for Child in Children)
        elif isinstance(Item, Enum):
            Result = (Kind, Observe(Item.value)[0])
        elif isinstance(Item, PurePath):
            Result = (Kind, str(Item))
        elif hasattr(Item, "ToDictionary") and Kind not in _FieldProjectedProducers:
            Result = (Kind, CanonicalJson(Item))
        elif is_dataclass(Item) and not isinstance(Item, type):
            Result = (Kind, tuple(
                (Field.name, Observe(getattr(Item, Field.name))[0])
                for Field in fields(Item)
            ))
        elif isinstance(Item, Mapping):
            Result = (Kind, tuple(
                (Observe(Key)[0], Observe(Child)[0])
                for Key, Child in sorted(
                    Item.items(), key=lambda Pair: CanonicalJson(Pair[0]),
                )
            ))
        elif Kind in (set, frozenset):
            Children = tuple(Observe(Child) for Child in Item)
            Result = (Kind, frozenset(Child[0] for Child in Children))
            Immutable = Kind is frozenset and all(Child[1] for Child in Children)
        else:
            Result = (Kind, CanonicalJson(Item))
        Memo[Identity] = (Item, Result, Immutable)
        if Immutable and len(Item) > 3:
            # Retain larger forests, rather than an entry for every coordinate.
            # Strong references prevent id reuse during this preparation scope.
            ImmutableCache[Identity] = (Item, Result)
        return Result, Immutable

    return Observe(Value)[0]


def ResolveTypedRouteCurrentContext(
    State: AuthoritativeRoutingState,
    Services: AuthoritativeRoutingServices,
):
    """Build once for one exact immutable region and reconstruct on scope drift."""
    if (
        type(State.Region.Nodes) is not frozenset
        or type(State.Region.Edges) is not frozenset
    ):
        raise TypeError(
            "typed route context sealing requires an immutable graph region"
        )
    Bounds = tuple(State.Bounds)
    PlacementBounds = (
        State.MinimumX,
        State.MaximumX,
        State.MinimumZ,
        State.MaximumZ,
    )
    SourceContextGraphIdentity = (
        State.Context.AuthoritativeContextGraphSha256
    )
    ConstructionAuthority = (
        State.Region,
        Bounds,
        PlacementBounds,
        SourceContextGraphIdentity,
    )
    Reused = bool(
        State.TypedNativeCurrentContext is not None
        and State.TypedNativeCurrentContextScope is not None
        and State.TypedNativeCurrentContextConstructionAuthority
        == ConstructionAuthority
    )
    if Reused:
        return (
            State.TypedNativeCurrentContext,
            State.TypedNativeCurrentContextScope,
            True,
        )

    Nodes = tuple(sorted(State.Region.Nodes))
    Edges = tuple(sorted(State.Region.Edges))
    Scope = TypedRouteContextScope.FromConstruction(
        Bounds,
        PlacementBounds,
        Nodes,
        Edges,
        SourceContextGraphIdentity,
    )
    Context = Services.RustRoutingContext(
        Bounds,
        PlacementBounds,
        list(Nodes),
        list(Edges),
    )
    if (
        Context.AuthoritativeContextGraphSha256
        != Scope.ContextGraphIdentity
    ):
        raise ValueError(
            "current native context graph identity changed "
            "during exact construction"
        )
    State.TypedNativeCurrentContext = Context
    State.TypedNativeCurrentContextScope = Scope
    State.TypedNativeCurrentContextConstructionAuthority = (
        ConstructionAuthority
    )
    return Context, Scope, False

def BuildTypedRouteCurrentCallerSnapshot(State, ContextScope, OriginDescriptors):
    """Seal current inputs once, including occupancy, excluding derived caches.

    FromAuthorities owns canonicalization. Pre-projecting these same source
    documents here would clone and walk every placement/access value twice.
    """
    def RequireAuthority(Name, Value):
        if Value is None or (type(Value) is str and not Value):
            raise ValueError(f"{Name} authority is unavailable")
        return Value

    ResourceGraphVersion = RequireAuthority(
        "ResourceGraph.GraphVersion",
        State.Resources.ResourceGraph.GraphVersion,
    )
    Profiles = RequireAuthority("Profiles", State.Profiles)
    Technology = RequireAuthority("Technology", State.Technology)
    TechnologyVersion = RequireAuthority(
        "Technology.TechnologyVersion",
        State.Technology.TechnologyVersion,
    )
    Placed = RequireAuthority("Placed", State.Placed)
    Policy = RequireAuthority("Policy", State.Policy)
    PlacementFingerprint = (
        {
            "Availability": "Provided",
            "Value": RequireAuthority(
                "ClusterInterfaceStateFingerprint",
                State.ClusterInterfaceStateFingerprint,
            ),
        }
        if State.ClusterInterfaceStateFingerprint
        else {
            "Availability": "NotApplicable",
            "Reason": "NoClusterInterfaceState",
        }
    )
    PortalAccessGeometryFingerprint = (
        {
            "Availability": "Provided",
            "Value": RequireAuthority(
                "PortalAccessGeometryFingerprint",
                State.PortalAccessGeometryFingerprint,
            ),
        }
        if State.PortalAccessGeometryFingerprint
        else {
            "Availability": "NotApplicable",
            "Reason": "LegacyPlacementAccessPolicyDisabled",
        }
        if not State.Policy.PlacementAccess.Enabled
        else RequireAuthority(
            "PortalAccessGeometryFingerprint",
            State.PortalAccessGeometryFingerprint,
        )
    )
    GuideInputFingerprint = (
        {
            "Availability": "Provided",
            "Value": RequireAuthority(
                "GuideInputFingerprint",
                State.GuideInputFingerprint,
            ),
        }
        if State.GuideInputFingerprint
        else {
            "Availability": "NotApplicable",
            "Reason": "NoGlobalGuidePlan",
        }
        if State.CoarsePlan is None
        else RequireAuthority(
            "GuideInputFingerprint",
            State.GuideInputFingerprint,
        )
    )
    PortalReservations = RequireAuthority(
        "PortalReservations",
        State.PortalReservations,
    )
    FrozenComponentClaims = RequireAuthority(
        "FrozenComponentClaims",
        State.FrozenComponentClaims,
    )
    PhysicalRequestDomains = RequireAuthority(
        "CandidateRequestDependencyComponentsBySignal",
        State.CandidateRequestDependencyComponentsBySignal,
    )
    if State.Policy.PlacementAccess.Enabled:
        Witness = State.PlacementPinAccessWitness
        WitnessFingerprint = getattr(
            Witness,
            "WitnessFingerprint",
            None,
        )
        RequireAuthority(
            "PlacementPinAccessWitnessFingerprint",
            WitnessFingerprint,
        )
        SelectedAccessAuthority = {
            "SchemaVersion": "joint-typed-route-selected-access-v1",
            "Enabled": True,
            "WitnessFingerprint": WitnessFingerprint,
            "Witness": RequireAuthority(
                "PlacementPinAccessWitness",
                Witness,
            ),
        }
    else:
        SelectedAccessAuthority = (
            BuildDisabledSelectedAccessAuthority()
        )
    ModelAuthority = {
        "SchemaVersion": "joint-typed-route-model-v1",
        "Profiles": Profiles,
        "ResourceGraphVersion": ResourceGraphVersion,
        "TechnologyVersion": TechnologyVersion,
    }
    TechnologyAuthority = {
        "SchemaVersion": "joint-typed-route-technology-v1",
        "Technology": Technology,
    }
    ResourceAuthority = {
        "SchemaVersion": "joint-typed-route-resource-v1",
        "ResourceGraphVersion": ResourceGraphVersion,
        "Context": ContextScope.ToDictionary(),
        "ActualBlocks": State.Resources.ResourceGraph.ActualBlocks,
        "ElectricalBlocks": State.Resources.ResourceGraph.ElectricalBlocks,
        "SolidBlocks": State.Resources.ResourceGraph.SolidBlocks,
        "StaticKeepOutBlocks": State.Resources.ResourceGraph.StaticKeepOutBlocks,
        "BlockStates": State.Resources.ResourceGraph.BlockStates,
        "Technology": State.Resources.ResourceGraph.Technology,
    }
    PlacementAuthority = {
        "SchemaVersion": "joint-typed-route-placement-v1",
        "Placed": Placed,
        "PlacementFingerprint": PlacementFingerprint,
    }
    PolicyAuthority = {
        "SchemaVersion": "joint-typed-route-policy-v1",
        "Policy": Policy,
    }
    # Bind only this invocation's origins. Prepared rows outside the dispatched
    # scope cannot contribute a receipt or negative proof to this invocation.
    # Rescan actual rows, rather than reusing the old request/metadata map: a
    # removed, replaced or reassociated request must change current authority.
    OriginIdentities = {Descriptor.Identity for Descriptor in OriginDescriptors}
    CurrentOriginRows = []
    for Signal, Requests in sorted(State.RouteRequestsBySignal.items()):
        MetadataValues = State.RouteMetadataBySignal.get(Signal, ())
        for Index, Request in enumerate(Requests):
            Descriptor = State.TypedNativeRouteRequestOriginDescriptorsById.get(id(Request))
            if Descriptor is not None and Descriptor.Identity in OriginIdentities:
                CurrentOriginRows.append((
                    Signal, Index, Descriptor.Identity, Request,
                    MetadataValues[Index] if Index < len(MetadataValues) else None,
                ))
    DependencyAuthority = {
        "SchemaVersion": "joint-typed-route-dependency-snapshot-v1",
        "PortalAccessGeometryFingerprint": (
            PortalAccessGeometryFingerprint
        ),
        "GuideInputFingerprint": GuideInputFingerprint,
        "CoarsePlan": State.CoarsePlan,
        "CandidateExpansionLimits": State.CandidateExpansionLimits,
        "AdaptiveBudget": State.AdaptiveBudget,
        "LayerCount": State.LayerCount,
        "UnreservedPortalMode": State.UnreservedPortalMode,
        "PreparingPhysicalComponentGlobalChannels": State.Resources.PreparingPhysicalComponentGlobalChannels,
        "AvoidRoutingPositions": State.AvoidRoutingPositions,
        "EffectiveAvoidRoutingPositionsBySignal": State.EffectiveAvoidRoutingPositionsBySignal,
        "ForeignBlockedNodesBySignal": State.ForeignBlockedNodesBySignal,
        "FrozenComponentBlockedWireNodesBySignal": State.FrozenComponentBlockedWireNodesBySignal,
        "PortalReservations": PortalReservations,
        "FrozenComponentClaims": FrozenComponentClaims,
        "ForeignSelectedAccessClaims": State.ForeignSelectedPinAccessClaimsBySignal,
        "SiblingApertures": State.AssemblySpecificSiblingAperturesBySignal,
        "PhysicalRequestDomains": PhysicalRequestDomains,
        "CurrentOriginRows": tuple(CurrentOriginRows),
        "OriginDescriptorIdentities": [
            Descriptor.Identity
            for Descriptor in OriginDescriptors
        ],
    }
    Authorities = {
        "ModelIdentity": ModelAuthority,
        "TechnologyIdentity": TechnologyAuthority,
        "ResourceIdentity": ResourceAuthority,
        "PlacementIdentity": PlacementAuthority,
        "SelectedAccessIdentity": SelectedAccessAuthority,
        "PolicyIdentity": PolicyAuthority,
        "DependencySnapshotIdentity": DependencyAuthority,
    }
    Observation = ObserveTypedRouteAuthorityValue(
        Authorities, State.TypedNativeImmutableObservationCache,
    )
    Cached = State.TypedNativeCallerSnapshotObservationCache
    if Cached is not None and Cached[0] == Observation:
        return Cached[1]
    OriginKey = tuple(Descriptor.Identity for Descriptor in OriginDescriptors)
    ScopedCache = State.TypedNativeCallerSnapshotsByOrigins
    PriorScope = ScopedCache.get(OriginKey)
    if PriorScope is not None and PriorScope[0] == Observation:
        State.TypedNativeCallerSnapshotObservationCache = PriorScope
        return PriorScope[1]
    # The outer authority is an exact local dict, so its observation contains
    # exact string-key/value pairs. Each value is freshly observed above even
    # when another invocation changed only its descriptor dependency binding.
    # Keep one value/digest per named authority, never a mutable source object
    # identity. This bounded cache avoids serializing six unchanged authorities
    # again when publication revisits a different invocation's descriptor set.
    ObservedAuthorities = dict(Observation[1])
    PriorBindings = State.TypedNativeCallerAuthorityObservationCache or {}
    CurrentBindings = {}
    for Name in CALLER_BINDING_NAMES:
        CurrentObservation = ObservedAuthorities[(str, Name)]
        Prior = PriorBindings.get(Name)
        Identity = (
            Prior[1] if Prior is not None and Prior[0] == CurrentObservation
            else AuthorityIdentity(Authorities[Name])
        )
        CurrentBindings[Name] = (CurrentObservation, Identity)
    Snapshot = TypedRouteCallerSnapshot(tuple(
        (Name, CurrentBindings[Name][1]) for Name in CALLER_BINDING_NAMES
    ))
    State.TypedNativeCallerAuthorityObservationCache = CurrentBindings
    State.TypedNativeCallerSnapshotObservationCache = (Observation, Snapshot)
    # Revisiting an earlier invocation at final publication must still observe
    # every current value above. Retain only a bounded set of owned observations
    # and immutable digests, so an equal observation avoids reserializing it.
    ScopedCache[OriginKey] = (Observation, Snapshot)
    while len(ScopedCache) > 32:
        del ScopedCache[next(iter(ScopedCache))]
    return Snapshot


def ResolveTypedRouteCurrentOriginMetadata(State, Requests, Descriptors):
    """Resolve dispatched origins from the current signal/request/metadata tables.

    Object identity selects an existing request row, never establishes semantic
    equality. Exact descriptor reconstruction from these current values follows
    before native sealing; later full bindings attest the actual table contents.
    """
    Rows = {}
    for Signal, SignalRequests in State.RouteRequestsBySignal.items():
        MetadataValues = State.RouteMetadataBySignal.get(Signal, ())
        if len(SignalRequests) != len(MetadataValues):
            raise ValueError("typed current request/metadata table cardinality changed")
        for Request, Metadata in zip(SignalRequests, MetadataValues):
            Key = id(Request)
            if Key in Rows:
                raise ValueError("typed current request has an ambiguous origin association")
            Rows[Key] = (Request, Signal, Metadata)
    Values = []
    for Request, Descriptor in zip(Requests, Descriptors):
        Row = Rows.get(id(Request))
        if Row is None or Row[0] is not Request or Row[1] != Descriptor.Signal:
            raise ValueError("typed current request escaped its signal association")
        Values.append(Row[2])
    return tuple(Values)


def ValidateTypedRouteExecutionScope(
    State, Services, Scope, Descriptors, *, Stage="TypedRouteReceiptPublication",
    OriginIdentity=None, NodesMatch=True,
):
    """Reject stale completed work before it can supply geometry or a negative proof."""
    State.CheckRuntimeBudget(Stage)
    try:
        _Context, CurrentContextScope, _Reused = ResolveTypedRouteCurrentContext(State, Services)
        CurrentCaller = BuildTypedRouteCurrentCallerSnapshot(
            State, CurrentContextScope, Descriptors,
        )
        CurrentDeadline = float(min(State.Deadline.ExpiresAt, State.AdaptiveExpiresAt))
        Matches = (
            CurrentContextScope == Scope.Context
            and CurrentCaller == Scope.CallerSnapshot
            and CurrentDeadline == Scope.DeadlineAtMonotonicSeconds
            and NodesMatch
        )
    except (AttributeError, TypeError, ValueError):
        Matches = False
    State.CheckRuntimeBudget(Stage)
    if not Matches:
        raise Services.RoutingStageError(Services.RoutingFailure(
            Reason=Services.RoutingFailureReason.ClusterInterfaceSolveIncomplete,
            Stage=Stage,
            AffectedNets=tuple(sorted({Descriptor.Signal for Descriptor in Descriptors})),
            Detail="typed route preparation changed before completed work was consumed",
            Diagnostics={
                "Action": "reject-stale-typed-route-preparation",
                "Complete": False,
                "OriginIdentity": OriginIdentity,
                "ExecutionScopeIdentity": Scope.Identity,
            },
        ))


def ValidateTypedRouteMaterializationPublication(State, Services):
    """Re-attest each completed invocation before any physical/proof publication."""
    for Scope, Descriptors, _Epoch in State.TypedNativeMaterializationEpochs or ():
        ValidateTypedRouteExecutionScope(
            State, Services, Scope, Descriptors,
            Stage="TypedRouteMaterializationPublication",
        )


def ValidateTypedRouteOriginBeforeMaterialization(State, Services, RoutedTree):
    """Check this exact receipt and live deadline; consume only its frozen epoch.

    Mutable caller authority is checked at native return and phase publication,
    rather than being reserialized for every origin. The epoch owns all physical
    materialization inputs; provisional physical outcomes cannot escape the gate.
    """
    Origin = State.TypedNativeRouteNodeOriginRecords.get(id(RoutedTree))
    if Origin is None:
        return None
    Scope, Descriptors, Nodes, Epoch = State.TypedNativeRouteOriginAuthorities[Origin.OriginIdentity]
    State.CheckRuntimeBudget("TypedRoutePhysicalAdmission")
    CurrentDeadline = float(min(State.Deadline.ExpiresAt, State.AdaptiveExpiresAt))
    if (RoutedTree is not Nodes or tuple(RoutedTree) != Nodes
            or CurrentDeadline != Scope.DeadlineAtMonotonicSeconds
            or Descriptors[Origin.OriginalOrdinal] != Origin.OriginDescriptor):
        raise Services.RoutingStageError(Services.RoutingFailure(
            Reason=Services.RoutingFailureReason.ClusterInterfaceSolveIncomplete,
            Stage="TypedRoutePhysicalAdmission",
            AffectedNets=(Origin.OriginDescriptor.Signal,),
            Detail="typed route origin escaped its immutable materialization epoch",
            Diagnostics={"Action": "reject-stale-typed-route-preparation", "Complete": False,
                         "OriginIdentity": Origin.OriginIdentity},
        ))
    return Epoch
