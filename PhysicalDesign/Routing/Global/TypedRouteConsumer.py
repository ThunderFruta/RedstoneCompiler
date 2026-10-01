"""Immutable Joint authority records for typed native route consumption."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from functools import cached_property
from hashlib import sha256
import json
from math import isfinite
from pathlib import PurePath
from types import MappingProxyType


CALLER_BINDING_NAMES = (
    "ModelIdentity",
    "TechnologyIdentity",
    "ResourceIdentity",
    "PlacementIdentity",
    "SelectedAccessIdentity",
    "PolicyIdentity",
    "DependencySnapshotIdentity",
)

TERMINAL_KINDS = (
    "Routed",
    "CompleteScopedNoPath",
    "SearchLimitIncomplete",
    "DeadlineIncomplete",
    "CancellationIncomplete",
    "NativeFailure",
    "PreNativeIncomplete",
)


class _FrozenAuthorityList(tuple):
    """Own a canonical JSON array without exposing mutable nested contents."""

    __slots__ = ()


def TypedRouteTerminalCompletesRequest(Kind: str) -> bool:
    """Return request completion without promoting scope or incompleteness."""
    if Kind not in TERMINAL_KINDS:
        raise ValueError("unsupported typed route terminal kind")
    return Kind in ("Routed", "CompleteScopedNoPath")


def CanonicalAuthority(Value: object) -> object:
    """Project immutable authority to canonical JSON-compatible named fields."""
    Kind = type(Value)
    if Kind is float and not isfinite(Value):
        raise TypeError("authority float must be finite")
    if Value is None or Kind in (bool, int, float, str):
        return Value
    if Kind in (tuple, list, _FrozenAuthorityList):
        # Primitive leaves are already canonical. Project every other value,
        # including floats, while retaining fresh arrays and source order.
        return [
            Item if Item is None or type(Item) is int or type(Item) is str or type(Item) is bool
            else CanonicalAuthority(Item)
            for Item in Value
        ]
    if Kind is dict:
        # Exact dicts have no producer hooks. Project their entries in source
        # order, then inspect current key types, as for general mappings below.
        # A descendant producer may mutate the source during its projection.
        Pairs = [
            (Key if type(Key) is str else CanonicalAuthority(Key), CanonicalAuthority(Item))
            for Key, Item in Value.items()
        ]
        if all(type(Key) is str for Key in Value):
            return {Key: Item for Key, Item in sorted(Pairs, key=lambda Pair: Pair[0])}
        return {
            "SchemaVersion": "joint-canonical-authority-map-v1",
            "Entries": [
                {"Key": Key, "Value": Item}
                for Key, Item in sorted(
                    Pairs,
                    key=lambda Pair: json.dumps(
                        Pair[0], sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                    ),
                )
            ],
        }
    if isinstance(Value, Enum):
        return Value.value
    if isinstance(Value, PurePath):
        return {
            "SchemaVersion": "joint-canonical-source-path-v1",
            "Value": str(Value),
        }
    if hasattr(Value, "ToDictionary"):
        return CanonicalAuthority(Value.ToDictionary())
    if is_dataclass(Value) and not isinstance(Value, type):
        return {
            Field.name: CanonicalAuthority(getattr(Value, Field.name))
            for Field in fields(Value)
        }
    if isinstance(Value, Mapping):
        Entries = [
            {
                "Key": CanonicalAuthority(Key),
                "Value": CanonicalAuthority(Item),
            }
            for Key, Item in Value.items()
        ]
        if all(type(Key) is str for Key in Value):
            return {
                Entry["Key"]: Entry["Value"]
                for Entry in sorted(Entries, key=lambda Entry: Entry["Key"])
            }
        return {
            "SchemaVersion": "joint-canonical-authority-map-v1",
            "Entries": sorted(
                Entries,
                key=lambda Entry: json.dumps(
                    Entry["Key"],
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ),
            ),
        }
    if type(Value) in (set, frozenset):
        Items = [CanonicalAuthority(Item) for Item in Value]
        return sorted(
            Items,
            key=lambda Item: json.dumps(
                Item,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ),
        )
    raise TypeError("authority has no canonical producer projection")


def _EncodeCanonicalDocument(Value: object) -> str:
    """Encode a document whose owner has already established its projection."""
    return json.dumps(
        Value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def CanonicalJson(Value: object) -> str:
    """Return the canonical JSON representation of an authority document."""
    return _EncodeCanonicalDocument(CanonicalAuthority(Value))


def AuthorityIdentity(Value: object) -> str:
    """Return the full SHA-256 identity for an immutable authority document."""
    return sha256(CanonicalJson(Value).encode("utf-8")).hexdigest()


def _RequireSha256(Value: object, Name: str, *, AllowNone: bool = False) -> str | None:
    if Value is None and AllowNone:
        return None
    if (
        type(Value) is not str
        or len(Value) != 64
        or any(Character not in "0123456789abcdef" for Character in Value)
    ):
        raise TypeError(f"{Name} must be a lowercase SHA-256 identity")
    return Value


def _RequireCount(Value: object, Name: str) -> int:
    if type(Value) is not int or Value < 0:
        raise TypeError(f"{Name} must be a non-negative exact integer")
    return Value


def _RequireExactInt(Value: object, Name: str) -> int:
    if type(Value) is not int:
        raise TypeError(f"{Name} must be an exact integer")
    return Value


def _RequireExactBool(Value: object, Name: str) -> bool:
    if type(Value) is not bool:
        raise TypeError(f"{Name} must be an exact bool")
    return Value


def _RequireNonEmptyString(Value: object, Name: str) -> str:
    if type(Value) is not str or not Value:
        raise TypeError(f"{Name} must be a non-empty exact string")
    return Value


def _DocumentWithoutFrozenContainers(Value: object) -> object:
    if isinstance(Value, Mapping):
        return {
            Key: _DocumentWithoutFrozenContainers(Item)
            for Key, Item in Value.items()
        }
    if type(Value) in (list, _FrozenAuthorityList):
        return [_DocumentWithoutFrozenContainers(Item) for Item in Value]
    return Value


def _FreezeCanonicalDocument(Value: object, Certificate: list[bool] | None = None) -> object:
    if type(Value) is dict:
        if Certificate is not None and any(type(Key) is not str for Key in Value):
            Certificate[0] = False
        return MappingProxyType({
            Key: _FreezeCanonicalDocument(Item, Certificate)
            for Key, Item in Value.items()
        })
    if type(Value) is list:
        return _FrozenAuthorityList(_FreezeCanonicalDocument(Item, Certificate) for Item in Value)
    if Certificate is not None and not (
        Value is None or type(Value) is bool or type(Value) is int or type(Value) is str
        or (type(Value) is float and isfinite(Value))
    ):
        # The public projection accepts some enum-backed documents which need
        # another producer pass. Preserve them, but never certify the fast path.
        Certificate[0] = False
    return Value


_UncertifiedCanonicalDocument = object()
_MaximumFastCanonicalDocumentDepth = 64


def _TryOwnPlainCanonicalDocument(Value: object, Active: set[int]) -> object:
    """Own a plain canonical tree without evaluating any producer or equality hook."""
    Kind = type(Value)
    if Value is None or Kind is bool or Kind is int or Kind is str:
        return Value
    if Kind is float:
        return Value if isfinite(Value) else _UncertifiedCanonicalDocument
    if ((Kind is not dict and Kind is not list)
            or id(Value) in Active or len(Active) >= _MaximumFastCanonicalDocumentDepth):
        return _UncertifiedCanonicalDocument
    Active.add(id(Value))
    try:
        if Kind is dict:
            if any(type(Key) is not str for Key in Value):
                return _UncertifiedCanonicalDocument
            Owned = {}
            for Key in sorted(Value):
                Item = _TryOwnPlainCanonicalDocument(Value[Key], Active)
                if Item is _UncertifiedCanonicalDocument:
                    return _UncertifiedCanonicalDocument
                Owned[Key] = Item
            return MappingProxyType(Owned)
        Owned = []
        for ValueItem in Value:
            Item = _TryOwnPlainCanonicalDocument(ValueItem, Active)
            if Item is _UncertifiedCanonicalDocument:
                return _UncertifiedCanonicalDocument
            Owned.append(Item)
        return _FrozenAuthorityList(Owned)
    finally:
        Active.remove(id(Value))


def _RequireCanonicalDocument(
    Value: object, Name: str, Certificate: list[bool] | None = None,
) -> object:
    Owned = _TryOwnPlainCanonicalDocument(Value, set())
    if Owned is not _UncertifiedCanonicalDocument:
        # These exact finite JSON trees are already their canonical projection.
        # This proves equality while owning them; no second projection is needed.
        return Owned
    Canonical = CanonicalAuthority(Value)
    if Canonical != _DocumentWithoutFrozenContainers(Value):
        raise ValueError(f"{Name} is not a canonical authority document")
    return _FreezeCanonicalDocument(Canonical, Certificate)


def BuildDisabledSelectedAccessAuthority() -> dict[str, object]:
    """Return the only selected-access authority allowed without a witness."""
    return {
        "SchemaVersion": "joint-typed-route-selected-access-disabled-v1",
        "Enabled": False,
    }


@dataclass(frozen=True)
class TypedRouteCallerSnapshot:
    """Exact current authority passed through the native caller echo boundary."""

    Bindings: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if type(self.Bindings) is not tuple:
            raise TypeError("typed caller bindings must be an exact tuple")
        if any(type(Binding) is not tuple or len(Binding) != 2 for Binding in self.Bindings):
            raise TypeError("typed caller bindings must contain exact name/identity pairs")
        if tuple(Name for Name, _Identity in self.Bindings) != CALLER_BINDING_NAMES:
            raise ValueError("typed caller bindings are missing, reordered, or extra")
        for Name, Identity in self.Bindings:
            if type(Name) is not str:
                raise TypeError("typed caller binding name must be an exact string")
            _RequireSha256(Identity, Name)

    @classmethod
    def FromAuthorities(
        cls,
        Authorities: Mapping[str, object],
    ) -> "TypedRouteCallerSnapshot":
        if set(Authorities) != set(CALLER_BINDING_NAMES):
            raise ValueError("typed caller authority document set is incomplete")
        return cls(tuple(
            (Name, AuthorityIdentity(Authorities[Name]))
            for Name in CALLER_BINDING_NAMES
        ))

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SchemaVersion": "joint-typed-route-caller-snapshot-v1",
            "Bindings": [list(Value) for Value in self.Bindings],
        }

    @property
    def Identity(self) -> str:
        return AuthorityIdentity(self.ToDictionary())


@dataclass(frozen=True)
class TypedRouteContextScope:
    """Exact construction inputs for the native RoutingContext used in a batch."""

    Bounds: tuple[int, int, int, int, int, int]
    PlacementBounds: tuple[int, int, int, int]
    ContextGraphIdentity: str
    NodeIdentity: str
    EdgeIdentity: str

    def __post_init__(self) -> None:
        if type(self.Bounds) is not tuple or len(self.Bounds) != 6:
            raise TypeError("typed route bounds must be an exact six-tuple")
        if type(self.PlacementBounds) is not tuple or len(self.PlacementBounds) != 4:
            raise TypeError("typed route placement bounds must be an exact four-tuple")
        if any(type(Value) is not int for Value in (*self.Bounds, *self.PlacementBounds)):
            raise TypeError("typed route bounds require exact integer coordinates")
        _RequireSha256(self.ContextGraphIdentity, "ContextGraphIdentity")
        _RequireSha256(self.NodeIdentity, "NodeIdentity")
        _RequireSha256(self.EdgeIdentity, "EdgeIdentity")

    @classmethod
    def FromConstruction(
        cls,
        Bounds: tuple[int, int, int, int, int, int],
        PlacementBounds: tuple[int, int, int, int],
        Nodes: tuple[tuple[int, int, int], ...],
        Edges: tuple[tuple[tuple[int, int, int], tuple[int, int, int]], ...],
        ContextGraphIdentity: str,
    ) -> "TypedRouteContextScope":
        if Nodes != tuple(sorted(Nodes)):
            raise ValueError("typed route context nodes are not canonical")
        if Edges != tuple(sorted(Edges)):
            raise ValueError("typed route context edges are not canonical")
        return cls(
            Bounds=Bounds,
            PlacementBounds=PlacementBounds,
            ContextGraphIdentity=ContextGraphIdentity,
            NodeIdentity=AuthorityIdentity({"Nodes": Nodes}),
            EdgeIdentity=AuthorityIdentity({"Edges": Edges}),
        )

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SchemaVersion": "joint-typed-route-context-v2",
            "Bounds": list(self.Bounds),
            "PlacementBounds": list(self.PlacementBounds),
            "ContextGraphIdentity": self.ContextGraphIdentity,
            "NodeIdentity": self.NodeIdentity,
            "EdgeIdentity": self.EdgeIdentity,
        }

    @property
    def Identity(self) -> str:
        return AuthorityIdentity(self.ToDictionary())


@dataclass(frozen=True)
class TypedRouteExecutionScope:
    """One invocation identity plus its non-shareable absolute deadline scope."""

    InvocationIdentity: str
    DeadlineAtMonotonicSeconds: float
    Context: TypedRouteContextScope
    CallerSnapshot: TypedRouteCallerSnapshot

    def __post_init__(self) -> None:
        _RequireSha256(self.InvocationIdentity, "InvocationIdentity")
        if type(self.Context) is not TypedRouteContextScope:
            raise TypeError("Context must be exact typed context authority")
        if type(self.CallerSnapshot) is not TypedRouteCallerSnapshot:
            raise TypeError("CallerSnapshot must be exact typed caller authority")
        if (
            type(self.DeadlineAtMonotonicSeconds) is not float
            or not isfinite(self.DeadlineAtMonotonicSeconds)
            or self.DeadlineAtMonotonicSeconds < 0.0
        ):
            raise TypeError("typed route deadline is invalid")

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SchemaVersion": "joint-typed-route-execution-scope-v1",
            "InvocationIdentity": self.InvocationIdentity,
            "DeadlineAtMonotonicSeconds": self.DeadlineAtMonotonicSeconds,
            "Context": self.Context.ToDictionary(),
            "CallerSnapshot": self.CallerSnapshot.ToDictionary(),
        }

    @property
    def Identity(self) -> str:
        return AuthorityIdentity(self.ToDictionary())


@dataclass(frozen=True)
class TypedRouteOriginDescriptor:
    """Immutable Joint geometry and restored-fragment scope for one origin."""

    Signal: str
    SourcePortal: object
    TargetPortals: tuple[object, ...]
    Guide: tuple[tuple[int, int], ...]
    Layer: int
    Axis: str
    Lane: int
    Variant: int
    ImmutableFragments: object

    def __post_init__(self) -> None:
        _RequireNonEmptyString(self.Signal, "Signal")
        CanonicalProjection = [True]
        object.__setattr__(self, "SourcePortal", _RequireCanonicalDocument(
            self.SourcePortal, "SourcePortal", CanonicalProjection,
        ))
        if type(self.TargetPortals) is not tuple:
            raise TypeError("TargetPortals must be an exact tuple")
        object.__setattr__(self, "TargetPortals", tuple(
            _RequireCanonicalDocument(Portal, f"TargetPortals[{Index}]", CanonicalProjection)
            for Index, Portal in enumerate(self.TargetPortals)
        ))
        if type(self.Guide) is not tuple or self.Guide != tuple(sorted(self.Guide)):
            raise ValueError("origin guide must be an exact sorted tuple")
        if any(
            type(Position) is not tuple
            or len(Position) != 2
            or any(type(Value) is not int for Value in Position)
            for Position in self.Guide
        ):
            raise TypeError("origin guide positions must be exact integer pairs")
        for Name in ("Layer", "Variant"):
            _RequireCount(getattr(self, Name), Name)
        _RequireExactInt(self.Lane, "Lane")
        if self.Axis not in ("X", "Z"):
            raise ValueError("origin axis must be X or Z")
        object.__setattr__(self, "ImmutableFragments", _RequireCanonicalDocument(
            self.ImmutableFragments, "ImmutableFragments", CanonicalProjection,
        ))
        object.__setattr__(self, "_CanonicalProjectionCertified", CanonicalProjection[0])

    @cached_property
    def ImmutableFragmentIdentity(self) -> str:
        return AuthorityIdentity({
            "SchemaVersion": "joint-typed-route-immutable-fragments-v1",
            "Fragments": self.ImmutableFragments,
        })

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SchemaVersion": "joint-typed-route-origin-descriptor-v1",
            "Signal": self.Signal,
            "SourcePortal": CanonicalAuthority(self.SourcePortal),
            "TargetPortals": CanonicalAuthority(self.TargetPortals),
            "Guide": [list(Position) for Position in self.Guide],
            "Layer": self.Layer,
            "Axis": self.Axis,
            "Lane": self.Lane,
            "Variant": self.Variant,
            "ImmutableFragments": CanonicalAuthority(self.ImmutableFragments),
            "ImmutableFragmentIdentity": self.ImmutableFragmentIdentity,
        }

    @cached_property
    def Identity(self) -> str:
        Document = self.ToDictionary()
        if (type(self) is TypedRouteOriginDescriptor and type(self.Axis) is str
                and getattr(self, "_CanonicalProjectionCertified", False) is True):
            # This exact owner exports only validated primitive fields and
            # freshly projected, deeply owned canonical portal/fragment data.
            # Avoid projecting the same document a second time. Subclasses and
            # accepted custom axis projections retain the public producer path.
            return sha256(_EncodeCanonicalDocument(Document).encode("utf-8")).hexdigest()
        return AuthorityIdentity(Document)


@dataclass(frozen=True)
class TypedRoutePlannedOrigin:
    """Invocation-local execution-equivalence plan for one Joint origin."""

    OriginIdentity: str
    GeometryIdentity: str
    ExecutionIdentity: str
    OriginalOrdinal: int
    CanonicalOriginalOrdinal: int
    RepresentativeIndex: int

    def __post_init__(self) -> None:
        for Name in (
            "OriginIdentity",
            "GeometryIdentity",
            "ExecutionIdentity",
        ):
            _RequireSha256(getattr(self, Name), Name)
        for Name in (
            "OriginalOrdinal",
            "CanonicalOriginalOrdinal",
            "RepresentativeIndex",
        ):
            _RequireCount(getattr(self, Name), Name)
        if self.CanonicalOriginalOrdinal > self.OriginalOrdinal:
            raise ValueError("planned canonical origin occurs after its equivalent")

    @property
    def Canonical(self) -> bool:
        return self.OriginalOrdinal == self.CanonicalOriginalOrdinal

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SchemaVersion": "joint-typed-route-planned-origin-v1",
            "OriginIdentity": self.OriginIdentity,
            "GeometryIdentity": self.GeometryIdentity,
            "ExecutionIdentity": self.ExecutionIdentity,
            "OriginalOrdinal": self.OriginalOrdinal,
            "CanonicalOriginalOrdinal": self.CanonicalOriginalOrdinal,
            "RepresentativeIndex": self.RepresentativeIndex,
            "Canonical": self.Canonical,
        }


def SealTypedRouteLegacyRequests(Requests):
    """Own exact legacy request values once for plan and native conversion.

    Only finite primitive sequence trees are supported. The source collections
    stay live freshness bindings, but cannot alter a sealed invocation midway
    through native request conversion.
    """
    def Freeze(Value):
        if Value is None or type(Value) in (bool, int, str):
            return Value
        if type(Value) is float and isfinite(Value):
            return Value
        if type(Value) in (tuple, list):
            return tuple(Freeze(Item) for Item in Value)
        raise TypeError("typed legacy requests require finite primitive sequences")

    Values = tuple(Freeze(Request) for Request in Requests)
    if any(type(Request) is not tuple or len(Request) != 11 for Request in Values):
        raise TypeError("typed legacy route request must contain eleven fields")
    return Values


def BuildTypedRouteExecutionPlan(
    InvocationIdentity: str,
    ContextIdentity: str,
    CallerIdentity: str,
    Origins: tuple[tuple[tuple[object, ...], TypedRouteOriginDescriptor], ...],
    *,
    CancellationRequestedBeforeStart: bool = False,
) -> tuple[TypedRoutePlannedOrigin, ...]:
    """Deduplicate exact execution scope while retaining every origin identity."""
    _RequireSha256(InvocationIdentity, "InvocationIdentity")
    _RequireSha256(ContextIdentity, "ContextIdentity")
    _RequireSha256(CallerIdentity, "CallerIdentity")
    _RequireExactBool(
        CancellationRequestedBeforeStart,
        "CancellationRequestedBeforeStart",
    )
    if type(Origins) is not tuple:
        raise TypeError("Origins must be an exact tuple")
    RepresentativeByExecution: dict[str, tuple[int, int]] = {}
    Result = []
    for OriginalOrdinal, Origin in enumerate(Origins):
        if type(Origin) is not tuple or len(Origin) != 2:
            raise TypeError("planned origin must be an exact request/descriptor pair")
        Request, Descriptor = Origin
        if type(Request) is not tuple or len(Request) != 11:
            raise TypeError("planned legacy route request must be an exact 11-tuple")
        if type(Descriptor) is not TypedRouteOriginDescriptor:
            raise TypeError("planned origin descriptor has the wrong exact type")
        GeometryIdentity = AuthorityIdentity({
            "SchemaVersion": "joint-typed-route-legacy-request-v1",
            "LegacyRequest": Request,
        })
        OriginIdentity = AuthorityIdentity({
            "SchemaVersion": "joint-typed-route-origin-v1",
            "InvocationIdentity": InvocationIdentity,
            "OriginalOrdinal": OriginalOrdinal,
            "OriginDescriptorIdentity": Descriptor.Identity,
            "GeometryIdentity": GeometryIdentity,
        })
        ExecutionIdentity = AuthorityIdentity({
            "SchemaVersion": "joint-typed-route-request-execution-v1",
            "InvocationIdentity": InvocationIdentity,
            "ContextIdentity": ContextIdentity,
            "CallerIdentity": CallerIdentity,
            "LegacyRequest": Request,
            "CancellationRequestedBeforeStart": (
                CancellationRequestedBeforeStart
            ),
        })
        Representative = RepresentativeByExecution.get(ExecutionIdentity)
        if Representative is None:
            Representative = (len(RepresentativeByExecution), OriginalOrdinal)
            RepresentativeByExecution[ExecutionIdentity] = Representative
        Result.append(TypedRoutePlannedOrigin(
            OriginIdentity=OriginIdentity,
            GeometryIdentity=GeometryIdentity,
            ExecutionIdentity=ExecutionIdentity,
            OriginalOrdinal=OriginalOrdinal,
            CanonicalOriginalOrdinal=Representative[1],
            RepresentativeIndex=Representative[0],
        ))
    return tuple(Result)


def BuildTypedNativeCoarseRequest(
    RequestId: str,
    CallerBindings: tuple[tuple[str, str], ...],
    Bounds: tuple[int, int, int, int, int, int],
    PlacementBounds: tuple[int, int, int, int],
    LegacyRequest: tuple[object, ...],
    *,
    CancellationRequestedBeforeStart: bool = False,
):
    """Project exactly one legacy 11-field request to its typed intent."""
    from RedstoneCompiler import RustRouting

    _RequireNonEmptyString(RequestId, "RequestId")
    TypedRouteCallerSnapshot(CallerBindings)
    _RequireExactBool(
        CancellationRequestedBeforeStart,
        "CancellationRequestedBeforeStart",
    )
    if type(LegacyRequest) is not tuple or len(LegacyRequest) != 11:
        raise TypeError("legacy coarse route request must be an exact 11-tuple")
    (
        Starts,
        Targets,
        Columns,
        Required,
        Blocked,
        Guide,
        RoutingY,
        GuidePenalty,
        BendPenalty,
        ViaPenalty,
        ExpansionCap,
    ) = LegacyRequest
    if not Targets:
        return RustRouting.RouteTreeCoarseRequestV1.ConnectStartsOnlyV1(
            RequestId,
            CallerBindings,
            Bounds,
            PlacementBounds,
            CancellationRequestedBeforeStart,
            Starts,
            Columns,
            Required,
            Blocked,
            Guide,
            RoutingY,
            GuidePenalty,
            BendPenalty,
            ViaPenalty,
            ExpansionCap,
        )
    return RustRouting.RouteTreeCoarseRequestV1(
        RequestId,
        CallerBindings,
        Bounds,
        PlacementBounds,
        CancellationRequestedBeforeStart,
        Starts,
        Targets,
        Columns,
        Required,
        Blocked,
        Guide,
        RoutingY,
        GuidePenalty,
        BendPenalty,
        ViaPenalty,
        ExpansionCap,
    )


@dataclass(frozen=True)
class TypedRouteOriginRecord:
    """Joint record linking one materialized origin to one native execution."""

    OriginIdentity: str
    OriginDescriptor: TypedRouteOriginDescriptor
    OriginalOrdinal: int
    CanonicalOriginalOrdinal: int
    CanonicalOriginIdentity: str
    CanonicalRequestId: str | None
    CanonicalReceiptIdentity: str | None
    ExecutionScopeIdentity: str | None
    GeometryIdentity: str
    NativePayloadIdentity: str | None
    RouteDomainIdentity: str | None
    NativeOrdinal: int | None
    NativeKind: str
    PreNativeReason: str | None
    ExpansionCap: int
    ActualExpansionCount: int
    CancellationRequestedBeforeStart: bool
    DeadlineAtMonotonicSeconds: float | None

    def __post_init__(self) -> None:
        _RequireSha256(self.OriginIdentity, "OriginIdentity")
        if type(self.OriginDescriptor) is not TypedRouteOriginDescriptor:
            raise TypeError("OriginDescriptor must be exact typed origin authority")
        _RequireCount(self.OriginalOrdinal, "OriginalOrdinal")
        _RequireCount(self.CanonicalOriginalOrdinal, "CanonicalOriginalOrdinal")
        if self.CanonicalOriginalOrdinal > self.OriginalOrdinal:
            raise ValueError("canonical origin cannot occur after its equivalent")
        _RequireSha256(self.CanonicalOriginIdentity, "CanonicalOriginIdentity")
        OrdinalEquivalent = (
            self.OriginalOrdinal != self.CanonicalOriginalOrdinal
        )
        IdentityEquivalent = (
            self.OriginIdentity != self.CanonicalOriginIdentity
        )
        if OrdinalEquivalent != IdentityEquivalent:
            raise ValueError("canonical origin identity contradicts origin ordinals")
        if self.CanonicalRequestId is not None:
            _RequireNonEmptyString(self.CanonicalRequestId, "CanonicalRequestId")
        _RequireSha256(self.CanonicalReceiptIdentity, "CanonicalReceiptIdentity", AllowNone=True)
        _RequireSha256(self.ExecutionScopeIdentity, "ExecutionScopeIdentity", AllowNone=True)
        _RequireSha256(self.GeometryIdentity, "GeometryIdentity")
        _RequireSha256(self.NativePayloadIdentity, "NativePayloadIdentity", AllowNone=True)
        _RequireSha256(self.RouteDomainIdentity, "RouteDomainIdentity", AllowNone=True)
        if self.NativeOrdinal is not None:
            _RequireCount(self.NativeOrdinal, "NativeOrdinal")
        if self.NativeKind not in TERMINAL_KINDS:
            raise ValueError("origin record has an unsupported native terminal kind")
        _RequireCount(self.ExpansionCap, "ExpansionCap")
        _RequireCount(self.ActualExpansionCount, "ActualExpansionCount")
        if self.ActualExpansionCount > self.ExpansionCap:
            raise ValueError("origin expansion count exceeds its exact cap")
        _RequireExactBool(
            self.CancellationRequestedBeforeStart,
            "CancellationRequestedBeforeStart",
        )
        if self.DeadlineAtMonotonicSeconds is not None and (
            type(self.DeadlineAtMonotonicSeconds) is not float
            or not isfinite(self.DeadlineAtMonotonicSeconds)
            or self.DeadlineAtMonotonicSeconds < 0.0
        ):
            raise TypeError("origin deadline is invalid")
        if self.NativeKind == "PreNativeIncomplete":
            _RequireNonEmptyString(self.PreNativeReason, "PreNativeReason")
            if any(Value is not None for Value in (
                self.CanonicalRequestId,
                self.CanonicalReceiptIdentity,
                self.ExecutionScopeIdentity,
                self.NativePayloadIdentity,
                self.RouteDomainIdentity,
                self.NativeOrdinal,
                self.DeadlineAtMonotonicSeconds,
            )):
                raise ValueError("pre-native origin cannot claim native execution scope")
            if self.ActualExpansionCount != 0:
                raise ValueError("pre-native origin cannot claim native expansions")
        else:
            if self.PreNativeReason is not None:
                raise ValueError("executed origin cannot carry a pre-native reason")
            if (
                self.CanonicalRequestId is None
                or self.ExecutionScopeIdentity is None
                or self.NativePayloadIdentity is None
                or self.DeadlineAtMonotonicSeconds is None
            ):
                raise ValueError("executed origin is missing native execution authority")
            if not OrdinalEquivalent and self.NativeOrdinal is None:
                raise ValueError("canonical origin must own its native ordinal")
            if OrdinalEquivalent and self.NativeOrdinal is not None:
                raise ValueError("equivalent origin cannot copy a native ordinal")
            if self.NativeKind == "Routed" and self.CanonicalReceiptIdentity is None:
                raise ValueError("routed origin requires canonical receipt authority")

    @property
    def CanonicalExecuted(self) -> bool:
        """Canonical native submission with a receipt, not proof search started."""
        return self.NativeOrdinal is not None

    @property
    def EquivalentReused(self) -> bool:
        return self.OriginIdentity != self.CanonicalOriginIdentity

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SchemaVersion": "joint-typed-route-origin-equivalence-v2",
            "OriginIdentity": self.OriginIdentity,
            "OriginDescriptor": self.OriginDescriptor.ToDictionary(),
            "OriginDescriptorIdentity": self.OriginDescriptor.Identity,
            "ImmutableFragmentIdentity": (
                self.OriginDescriptor.ImmutableFragmentIdentity
            ),
            "OriginalOrdinal": self.OriginalOrdinal,
            "CanonicalOriginalOrdinal": self.CanonicalOriginalOrdinal,
            "CanonicalOriginIdentity": self.CanonicalOriginIdentity,
            "CanonicalRequestId": self.CanonicalRequestId,
            "CanonicalReceiptIdentity": self.CanonicalReceiptIdentity,
            "ExecutionScopeIdentity": self.ExecutionScopeIdentity,
            "GeometryIdentity": self.GeometryIdentity,
            "NativePayloadIdentity": self.NativePayloadIdentity,
            "RouteDomainIdentity": self.RouteDomainIdentity,
            "NativeOrdinal": self.NativeOrdinal,
            "NativeKind": self.NativeKind,
            "PreNativeReason": self.PreNativeReason,
            "ExpansionCap": self.ExpansionCap,
            "ActualExpansionCount": self.ActualExpansionCount,
            "CancellationRequestedBeforeStart": (
                self.CancellationRequestedBeforeStart
            ),
            "DeadlineAtMonotonicSeconds": self.DeadlineAtMonotonicSeconds,
            "CanonicalExecuted": self.CanonicalExecuted,
            "EquivalentReused": self.EquivalentReused,
        }


def BuildTypedRouteOriginRecordsFromResults(
    Plan: tuple[TypedRoutePlannedOrigin, ...],
    OriginDescriptors: tuple[TypedRouteOriginDescriptor, ...],
    NativeResults: tuple[object, ...],
    ExecutionScope: TypedRouteExecutionScope,
) -> tuple[TypedRouteOriginRecord, ...]:
    """Bind every origin to one validated canonical native result reference."""
    from PhysicalDesign.Runtime.NativeRouting import NativeRouteRequestResultV1

    if type(Plan) is not tuple or any(
        type(Value) is not TypedRoutePlannedOrigin for Value in Plan
    ):
        raise TypeError("Plan must contain exact planned origin records")
    if type(OriginDescriptors) is not tuple or any(
        type(Value) is not TypedRouteOriginDescriptor
        for Value in OriginDescriptors
    ):
        raise TypeError("OriginDescriptors must contain exact typed descriptors")
    if len(Plan) != len(OriginDescriptors):
        raise ValueError("origin plan and descriptor counts differ")
    if type(NativeResults) is not tuple or any(
        type(Value) is not NativeRouteRequestResultV1 for Value in NativeResults
    ):
        raise TypeError("NativeResults must contain exact native route results")
    if type(ExecutionScope) is not TypedRouteExecutionScope:
        raise TypeError("ExecutionScope must be exact typed authority")
    CanonicalByExecution: dict[str, TypedRoutePlannedOrigin] = {}
    for Ordinal, (Planned, Descriptor) in enumerate(zip(Plan, OriginDescriptors)):
        if Planned.OriginalOrdinal != Ordinal:
            raise ValueError("origin plan is not in sequential original order")
        CanonicalPlan = CanonicalByExecution.get(Planned.ExecutionIdentity)
        if CanonicalPlan is None:
            if (
                not Planned.Canonical
                or Planned.RepresentativeIndex != len(CanonicalByExecution)
            ):
                raise ValueError("planned canonical representative is inconsistent")
            CanonicalByExecution[Planned.ExecutionIdentity] = Planned
        elif (
            Planned.CanonicalOriginalOrdinal != CanonicalPlan.OriginalOrdinal
            or Planned.RepresentativeIndex != CanonicalPlan.RepresentativeIndex
            or Planned.GeometryIdentity != CanonicalPlan.GeometryIdentity
        ):
            raise ValueError("planned equivalent does not match its canonical representative")
        ExpectedOriginIdentity = AuthorityIdentity({
            "SchemaVersion": "joint-typed-route-origin-v1",
            "InvocationIdentity": ExecutionScope.InvocationIdentity,
            "OriginalOrdinal": Ordinal,
            "OriginDescriptorIdentity": Descriptor.Identity,
            "GeometryIdentity": Planned.GeometryIdentity,
        })
        if Planned.OriginIdentity != ExpectedOriginIdentity:
            raise ValueError("planned origin does not bind its descriptor and invocation")
    if len(NativeResults) != len(CanonicalByExecution):
        raise ValueError("native result set does not match canonical representatives")
    for Index, Result in enumerate(NativeResults):
        if Result.OriginalOrdinal != Index:
            raise ValueError("native result set is not in canonical representative order")
        if (
            Result.ExecutionScope.ContextGraphIdentity
            != ExecutionScope.Context.ContextGraphIdentity
            or Result.ExecutionScope.DeadlineAtMonotonicSeconds
            != ExecutionScope.DeadlineAtMonotonicSeconds
        ):
            raise ValueError("native result does not match current execution scope")
    # The scope owns only exact immutable typed fields. Hash it once for this
    # batch while preserving the empty-plan path's lack of identity evaluation.
    ScopeIdentity = ExecutionScope.Identity if Plan else None
    Records = []
    for Planned, Descriptor in zip(Plan, OriginDescriptors):
        if Planned.RepresentativeIndex >= len(NativeResults):
            raise ValueError("planned representative has no native result")
        Result = NativeResults[Planned.RepresentativeIndex]
        CanonicalPlan = Plan[Planned.CanonicalOriginalOrdinal]
        Records.append(TypedRouteOriginRecord(
            OriginIdentity=Planned.OriginIdentity,
            OriginDescriptor=Descriptor,
            OriginalOrdinal=Planned.OriginalOrdinal,
            CanonicalOriginalOrdinal=Planned.CanonicalOriginalOrdinal,
            CanonicalOriginIdentity=CanonicalPlan.OriginIdentity,
            CanonicalRequestId=Result.RequestIdentity,
            CanonicalReceiptIdentity=Result.ExecutionScope.ReceiptIdentity,
            ExecutionScopeIdentity=(
                ScopeIdentity if ScopeIdentity is not None else ExecutionScope.Identity
            ),
            GeometryIdentity=Planned.GeometryIdentity,
            NativePayloadIdentity=Result.NativePayloadIdentity,
            RouteDomainIdentity=Result.ExecutionScope.RouteDomainIdentity,
            NativeOrdinal=(
                Result.OriginalOrdinal if Planned.Canonical else None
            ),
            NativeKind=Result.Kind.value,
            PreNativeReason=None,
            ExpansionCap=Result.ExpansionCap,
            ActualExpansionCount=Result.ActualExpansionCount,
            CancellationRequestedBeforeStart=(
                Result.NativeReceipt.CancellationRequested
            ),
            DeadlineAtMonotonicSeconds=(
                ExecutionScope.DeadlineAtMonotonicSeconds
            ),
        ))
    return tuple(Records)


@dataclass(frozen=True)
class TypedRoutePhysicalEvidence:
    """Exact P1 admission evidence retained for one routed origin."""

    Status: str
    Reason: str
    Signal: str
    ConflictResources: tuple[object, ...]
    ConflictingOwners: tuple[str, ...]
    ResourceEvidenceAvailable: bool
    OwnerEvidenceAvailable: bool
    ProvenanceEvidenceAvailable: bool
    OriginDescriptorIdentity: str
    ImmutableFragmentIdentity: str
    MaterializationDiagnostics: object

    def __post_init__(self) -> None:
        if self.Status not in ("Accepted", "Rejected"):
            raise ValueError("physical evidence status must be Accepted or Rejected")
        _RequireNonEmptyString(self.Reason, "Reason")
        _RequireNonEmptyString(self.Signal, "Signal")
        if type(self.ConflictResources) is not tuple:
            raise TypeError("ConflictResources must be an exact tuple")
        object.__setattr__(self, "ConflictResources", tuple(
            _RequireCanonicalDocument(Resource, f"ConflictResources[{Index}]")
            for Index, Resource in enumerate(self.ConflictResources)
        ))
        if type(self.ConflictingOwners) is not tuple or self.ConflictingOwners != tuple(
            sorted(self.ConflictingOwners)
        ):
            raise ValueError("ConflictingOwners must be an exact sorted tuple")
        if any(type(Owner) is not str or not Owner for Owner in self.ConflictingOwners):
            raise TypeError("conflicting owners must be non-empty exact strings")
        for Name in (
            "ResourceEvidenceAvailable",
            "OwnerEvidenceAvailable",
            "ProvenanceEvidenceAvailable",
        ):
            _RequireExactBool(getattr(self, Name), Name)
        if bool(self.ConflictResources) != self.ResourceEvidenceAvailable:
            raise ValueError("resource availability contradicts retained conflicts")
        if bool(self.ConflictingOwners) != self.OwnerEvidenceAvailable:
            raise ValueError("owner availability contradicts retained owners")
        if self.Status == "Accepted" and (
            self.ConflictResources or self.ConflictingOwners
        ):
            raise ValueError("accepted physical evidence cannot retain conflicts")
        if self.Reason == "P1SelfClaimConflict" and (
            not self.ConflictResources
            or self.ConflictingOwners != (self.Signal,)
            or not self.ResourceEvidenceAvailable
            or not self.OwnerEvidenceAvailable
        ):
            raise ValueError("P1 self-claim evidence lacks exact resource ownership")
        _RequireSha256(self.OriginDescriptorIdentity, "OriginDescriptorIdentity")
        _RequireSha256(self.ImmutableFragmentIdentity, "ImmutableFragmentIdentity")
        object.__setattr__(self, "MaterializationDiagnostics", _RequireCanonicalDocument(
            self.MaterializationDiagnostics, "MaterializationDiagnostics"
        ))

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SchemaVersion": "joint-typed-route-p1-evidence-v1",
            "Status": self.Status,
            "Reason": self.Reason,
            "Signal": self.Signal,
            "ConflictResources": CanonicalAuthority(self.ConflictResources),
            "ConflictingOwners": list(self.ConflictingOwners),
            "ResourceEvidenceAvailable": self.ResourceEvidenceAvailable,
            "OwnerEvidenceAvailable": self.OwnerEvidenceAvailable,
            "ProvenanceEvidenceAvailable": self.ProvenanceEvidenceAvailable,
            "OriginDescriptorIdentity": self.OriginDescriptorIdentity,
            "ImmutableFragmentIdentity": self.ImmutableFragmentIdentity,
            "MaterializationDiagnostics": CanonicalAuthority(self.MaterializationDiagnostics),
        }

    @cached_property
    def Identity(self) -> str:
        return AuthorityIdentity(self.ToDictionary())


@dataclass(frozen=True)
class TypedRouteRecoveryAttribution:
    """A real recovery action causally attributed to exact P1 evidence."""

    Action: str
    EvidenceIdentity: str
    Owner: str
    ScopeIdentity: str

    def __post_init__(self) -> None:
        _RequireNonEmptyString(self.Action, "Action")
        _RequireSha256(self.EvidenceIdentity, "EvidenceIdentity")
        _RequireNonEmptyString(self.Owner, "Owner")
        _RequireSha256(self.ScopeIdentity, "ScopeIdentity")

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SchemaVersion": "joint-typed-route-recovery-attribution-v1",
            "Action": self.Action,
            "EvidenceIdentity": self.EvidenceIdentity,
            "Owner": self.Owner,
            "ScopeIdentity": self.ScopeIdentity,
        }

    @property
    def Identity(self) -> str:
        return AuthorityIdentity(self.ToDictionary())


@dataclass(frozen=True)
class TypedRouteAdmissionRecord:
    """Per-origin physical admission, P1 evidence, and real recovery record."""

    OriginIdentity: str
    NativeKind: str
    Admitted: bool | None
    PhysicalEvidence: TypedRoutePhysicalEvidence | None
    RecoveryAttribution: TypedRouteRecoveryAttribution | None

    def __post_init__(self) -> None:
        _RequireSha256(self.OriginIdentity, "OriginIdentity")
        if self.NativeKind not in TERMINAL_KINDS:
            raise ValueError("admission record has an unsupported native terminal kind")
        if self.NativeKind != "Routed":
            if self.Admitted is not None or self.PhysicalEvidence is not None:
                raise ValueError("only routed origins may reach physical admission")
        else:
            if type(self.Admitted) is not bool:
                raise TypeError("routed origin requires an exact admission result")
            if type(self.PhysicalEvidence) is not TypedRoutePhysicalEvidence:
                raise TypeError("routed origin requires exact P1 physical evidence")
            ExpectedStatus = "Accepted" if self.Admitted else "Rejected"
            if self.PhysicalEvidence.Status != ExpectedStatus:
                raise ValueError("physical evidence contradicts admission result")
        if self.RecoveryAttribution is not None:
            if type(self.RecoveryAttribution) is not TypedRouteRecoveryAttribution:
                raise TypeError("recovery attribution must be exact typed authority")
            if self.PhysicalEvidence is None:
                raise ValueError("recovery attribution requires physical evidence")
            if (
                self.RecoveryAttribution.EvidenceIdentity
                != self.PhysicalEvidence.Identity
            ):
                raise ValueError("recovery attribution references different evidence")
            if not (
                self.PhysicalEvidence.OwnerEvidenceAvailable
                and self.PhysicalEvidence.ProvenanceEvidenceAvailable
            ):
                raise ValueError("recovery attribution lacks owner/provenance proof")

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SchemaVersion": "joint-typed-route-admission-v2",
            "OriginIdentity": self.OriginIdentity,
            "NativeKind": self.NativeKind,
            "Admitted": self.Admitted,
            "PhysicalEvidence": (
                None
                if self.PhysicalEvidence is None
                else self.PhysicalEvidence.ToDictionary()
            ),
            "PhysicalEvidenceIdentity": (
                None
                if self.PhysicalEvidence is None
                else self.PhysicalEvidence.Identity
            ),
            "RecoveryAttribution": (
                None
                if self.RecoveryAttribution is None
                else self.RecoveryAttribution.ToDictionary()
            ),
            "RecoveryAttributionIdentity": (
                None
                if self.RecoveryAttribution is None
                else self.RecoveryAttribution.Identity
            ),
        }


@dataclass(frozen=True)
class TypedRouteBatchCounters:
    """Cross-boundary counts; CanonicalExecuted means submitted with a receipt.

    Native Started remains a separate fact: a pre-cancelled canonical request
    has an ordinal and receipt but never starts route search.
    """

    Configured: int
    Materialized: int
    Filtered: int
    CanonicalExecuted: int
    EquivalentReused: int
    Routed: int
    CompleteScopedNoPath: int
    SearchLimitIncomplete: int
    DeadlineIncomplete: int
    CancellationIncomplete: int
    NativeFailure: int
    PreNativeIncomplete: int
    CandidateProduced: int
    PhysicallyAccepted: int
    PhysicallyRejected: int

    @classmethod
    def FromDictionary(
        cls,
        Value: Mapping[str, object],
    ) -> "TypedRouteBatchCounters":
        Expected = {
            "SchemaVersion",
            *(Field.name for Field in fields(cls)),
        }
        if set(Value) != Expected:
            raise ValueError("typed route counter document is missing or extra")
        if Value["SchemaVersion"] != "joint-typed-route-batch-counters-v2":
            raise ValueError("typed route counter schema is unsupported")
        return cls(**{
            Field.name: Value[Field.name]
            for Field in fields(cls)
        })

    def __post_init__(self) -> None:
        Names = (
            "Configured", "Materialized", "Filtered", "CanonicalExecuted",
            "EquivalentReused", "Routed", "CompleteScopedNoPath",
            "SearchLimitIncomplete", "DeadlineIncomplete",
            "CancellationIncomplete", "NativeFailure", "PreNativeIncomplete",
            "CandidateProduced",
            "PhysicallyAccepted", "PhysicallyRejected",
        )
        for Name in Names:
            _RequireCount(getattr(self, Name), Name)
        if self.Materialized + self.Filtered != self.Configured:
            raise ValueError("configured route count does not reconcile")
        if (
            self.CanonicalExecuted
            + self.EquivalentReused
            + self.PreNativeIncomplete
            != self.Materialized
        ):
            raise ValueError("execution route count does not reconcile")
        if (
            self.Routed + self.CompleteScopedNoPath + self.SearchLimitIncomplete
            + self.DeadlineIncomplete + self.CancellationIncomplete
            + self.NativeFailure + self.PreNativeIncomplete
            != self.Materialized
        ):
            raise ValueError("native terminal route count does not reconcile")
        if self.CandidateProduced > self.Routed:
            raise ValueError("materialized candidate count exceeds routed origins")
        if self.PhysicallyAccepted + self.PhysicallyRejected > self.Routed:
            raise ValueError("physical admission exceeds routed origins")
        if self.PhysicallyAccepted > self.CandidateProduced:
            raise ValueError("accepted origin lacks a materialized candidate")

    def ToDictionary(self) -> dict[str, int | str]:
        return {
            "SchemaVersion": "joint-typed-route-batch-counters-v2",
            "Configured": self.Configured,
            "Materialized": self.Materialized,
            "Filtered": self.Filtered,
            "CanonicalExecuted": self.CanonicalExecuted,
            "EquivalentReused": self.EquivalentReused,
            "Routed": self.Routed,
            "CompleteScopedNoPath": self.CompleteScopedNoPath,
            "SearchLimitIncomplete": self.SearchLimitIncomplete,
            "DeadlineIncomplete": self.DeadlineIncomplete,
            "CancellationIncomplete": self.CancellationIncomplete,
            "NativeFailure": self.NativeFailure,
            "PreNativeIncomplete": self.PreNativeIncomplete,
            "CandidateProduced": self.CandidateProduced,
            "PhysicallyAccepted": self.PhysicallyAccepted,
            "PhysicallyRejected": self.PhysicallyRejected,
        }


def _ProjectTypedRouteOriginFields(
    Signal: str,
    Profile,
    Metadata: tuple[object, ...],
) -> dict[str, object]:
    """Capture each origin producer once, in its established evaluation order."""
    SourcePortal, TargetPortals, Guide, Layer, Axis, Lane, Variant = Metadata
    TargetFragments = [
        {
            "Target": CanonicalAuthority(Target),
            "AccessPath": CanonicalAuthority(Profile.TargetAccessPaths[Target]),
            "Portal": CanonicalAuthority(Portal),
        }
        for Target, Portal in zip(Profile.Targets, TargetPortals)
    ]
    ImmutableFragments = {
        "SchemaVersion": "joint-typed-route-immutable-fragments-v1",
        "SeedLocalClaims": CanonicalAuthority(
            () if Profile.Seed is None else Profile.Seed.LocalClaims
        ),
        "SourceAccessPath": CanonicalAuthority(Profile.SourceAccessPath),
        "SourcePortalPath": CanonicalAuthority(SourcePortal.Path),
        "TargetFragments": TargetFragments,
    }
    return {
        "Signal": Signal,
        "SourcePortal": CanonicalAuthority(SourcePortal),
        "TargetPortals": tuple(
            CanonicalAuthority(Portal)
            for Portal in TargetPortals
        ),
        "Guide": tuple(sorted(Guide)),
        "Layer": Layer,
        "Axis": Axis,
        "Lane": Lane,
        "Variant": Variant,
        "ImmutableFragments": ImmutableFragments,
    }


def BuildTypedRouteOriginDescriptor(
    Signal: str,
    Profile,
    Metadata: tuple[object, ...],
) -> TypedRouteOriginDescriptor:
    """Bind native-equivalent geometry to its distinct Joint origin fragments."""
    return TypedRouteOriginDescriptor(**_ProjectTypedRouteOriginFields(Signal, Profile, Metadata))


def _MatchesOwnedCanonicalDocument(Owned: object, Current: object) -> bool:
    """Prove equal canonical bytes only for exact builtin document values."""
    Kind = type(Current)
    if Current is None:
        return Owned is None
    if Kind is bool or Kind is int or Kind is str:
        return type(Owned) is Kind and Owned == Current
    if Kind is float:
        return type(Owned) is float and isfinite(Current) and Owned.hex() == Current.hex()
    if Kind is list:
        return (
            type(Owned) is _FrozenAuthorityList and len(Owned) == len(Current)
            and all(_MatchesOwnedCanonicalDocument(Left, Right) for Left, Right in zip(Owned, Current))
        )
    if Kind is dict:
        return (
            type(Owned) is MappingProxyType
            and all(type(Key) is str for Key in Current)
            and Owned.keys() == Current.keys()
            and all(_MatchesOwnedCanonicalDocument(Owned[Key], Item) for Key, Item in Current.items())
        )
    return False


def TypedRouteOriginMatchesCurrentInputs(
    Descriptor: TypedRouteOriginDescriptor,
    Signal: str,
    Profile,
    Metadata: tuple[object, ...],
) -> bool:
    """Recheck fresh origin fields without refreezing an equal owned descriptor.

    Only a proved positive uses the shortcut. Every mismatch, invalid value or
    unusual producer takes the complete constructor from the captured fields,
    preserving validation order and avoiding a second producer evaluation.
    """
    Current = _ProjectTypedRouteOriginFields(Signal, Profile, Metadata)
    Guide = Current["Guide"]
    if (
        type(Descriptor) is TypedRouteOriginDescriptor
        and getattr(Descriptor, "_CanonicalProjectionCertified", False) is True
        and type(Descriptor.Axis) is str
        and type(Signal) is str and Signal == Descriptor.Signal
        and type(Current["Axis"]) is str and Current["Axis"] == Descriptor.Axis
        and all(type(Current[Name]) is int and Current[Name] == getattr(Descriptor, Name)
                for Name in ("Layer", "Lane", "Variant"))
        and all(type(Position) is tuple and len(Position) == 2
                and all(type(Value) is int for Value in Position) for Position in Guide)
        and Guide == Descriptor.Guide
        and _MatchesOwnedCanonicalDocument(Descriptor.SourcePortal, Current["SourcePortal"])
        and len(Descriptor.TargetPortals) == len(Current["TargetPortals"])
        and all(_MatchesOwnedCanonicalDocument(Owned, Projected)
                for Owned, Projected in zip(Descriptor.TargetPortals, Current["TargetPortals"]))
        and _MatchesOwnedCanonicalDocument(Descriptor.ImmutableFragments, Current["ImmutableFragments"])
    ):
        return True
    return TypedRouteOriginDescriptor(**Current).Identity == Descriptor.Identity
