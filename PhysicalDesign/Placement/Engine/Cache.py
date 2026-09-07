"""Placement cache storage and portfolio-local geometry reconstruction."""

from __future__ import annotations

from dataclasses import (
    dataclass,
    fields,
    is_dataclass,
)
from enum import Enum
from typing import (
    Any,
    Mapping,
)

from PhysicalDesign.Cells import Library as CellLibrary
from PhysicalDesign.Redstone.Rules import Geometry as GeometryRules


Position3 = tuple[int, int, int]
ORIENTED_CELL_GEOMETRY_CACHE_SCHEMA = (
    "portfolio-oriented-cell-geometry-v1"
)


def _FreezeIdentity(
    Value: Any,
    ActiveObjects: set[int] | None = None,
) -> tuple[object, ...]:
    """Return a complete, deterministic value identity or reject it."""
    if Value is None or isinstance(Value, (bool, int, str, bytes)):
        return (type(Value).__name__, Value)
    if isinstance(Value, float):
        return ("float", Value.hex())
    if isinstance(Value, Enum):
        return (
            "enum",
            type(Value).__module__,
            type(Value).__qualname__,
            _FreezeIdentity(Value.value, ActiveObjects),
        )
    IsStructured = (
        (is_dataclass(Value) and not isinstance(Value, type))
        or isinstance(Value, (Mapping, tuple, list, set, frozenset))
    )
    if not IsStructured:
        raise TypeError(
            "unsupported oriented-geometry identity value "
            f"{type(Value).__module__}.{type(Value).__qualname__}"
        )
    if ActiveObjects is None:
        ActiveObjects = set()
    ObjectIdentity = id(Value)
    if ObjectIdentity in ActiveObjects:
        raise TypeError("recursive oriented-geometry identity value")
    ActiveObjects.add(ObjectIdentity)
    try:
        if is_dataclass(Value) and not isinstance(Value, type):
            return (
                "dataclass",
                type(Value).__module__,
                type(Value).__qualname__,
                tuple(
                    (
                        Field.name,
                        _FreezeIdentity(
                            getattr(Value, Field.name),
                            ActiveObjects,
                        ),
                    )
                    for Field in fields(Value)
                ),
            )
        if isinstance(Value, Mapping):
            Items = [
                (
                    _FreezeIdentity(Key, ActiveObjects),
                    _FreezeIdentity(Item, ActiveObjects),
                )
                for Key, Item in Value.items()
            ]
            Items.sort(key=repr)
            return (
                "mapping",
                type(Value).__module__,
                type(Value).__qualname__,
                tuple(Items),
            )
        if isinstance(Value, tuple):
            return (
                "tuple",
                tuple(
                    _FreezeIdentity(Item, ActiveObjects) for Item in Value
                ),
            )
        if isinstance(Value, list):
            return (
                "list",
                tuple(
                    _FreezeIdentity(Item, ActiveObjects) for Item in Value
                ),
            )
        Items = sorted(
            (
                _FreezeIdentity(Item, ActiveObjects)
                for Item in Value
            ),
            key=repr,
        )
        return (type(Value).__name__, tuple(Items))
    finally:
        ActiveObjects.remove(ObjectIdentity)


def _RequireFrozenTechnology(Technology: Any) -> None:
    Parameters = getattr(type(Technology), "__dataclass_params__", None)
    if Parameters is None or not Parameters.frozen:
        raise TypeError(
            "oriented geometry requires a frozen routing technology"
        )


@dataclass(frozen=True)
class _OrientedGeometryContextSnapshot:
    """Deterministic values plus exact live catalog generations."""

    ValueIdentity: tuple[object, ...]
    MaximumEntryCount: int
    MacroMapping: Mapping[Any, Any]
    TemplateMapping: Mapping[Any, Any]
    MacroObjects: tuple[Any, ...]
    TemplateObjects: tuple[Any, ...]

    def Matches(self, Other: "_OrientedGeometryContextSnapshot") -> bool:
        return (
            self.ValueIdentity == Other.ValueIdentity
            and self.MaximumEntryCount == Other.MaximumEntryCount
            and self.MacroMapping is Other.MacroMapping
            and self.TemplateMapping is Other.TemplateMapping
            and len(self.MacroObjects) == len(Other.MacroObjects)
            and len(self.TemplateObjects) == len(Other.TemplateObjects)
            and all(
                First is Second
                for First, Second in zip(
                    self.MacroObjects,
                    Other.MacroObjects,
                )
            )
            and all(
                First is Second
                for First, Second in zip(
                    self.TemplateObjects,
                    Other.TemplateObjects,
                )
            )
        )


class _IdentityReference:
    """Hash one live producer object by identity while retaining it."""

    __slots__ = ("Target",)

    def __init__(self, Target: Any) -> None:
        self.Target = Target

    def __hash__(self) -> int:
        return id(self.Target)

    def __eq__(self, Other: object) -> bool:
        return (
            isinstance(Other, _IdentityReference)
            and self.Target is Other.Target
        )


def _BuildOrientedGeometryContextSnapshot(
    ConsumerTechnology: Any,
) -> _OrientedGeometryContextSnapshot:
    """Identify every public producer input and its loaded generation."""
    ProducerTechnology = GeometryRules.DefaultRedstoneRoutingTechnology
    _RequireFrozenTechnology(ConsumerTechnology)
    _RequireFrozenTechnology(ProducerTechnology)
    ConsumerTechnologyIdentity = _FreezeIdentity(ConsumerTechnology)
    ProducerTechnologyIdentity = _FreezeIdentity(ProducerTechnology)
    if ConsumerTechnologyIdentity != ProducerTechnologyIdentity:
        raise ValueError("producer and consumer routing technologies differ")

    Macros = CellLibrary.CellMacros
    Templates = GeometryRules.LoadRoutingTemplates()
    if not isinstance(Macros, Mapping) or not isinstance(Templates, Mapping):
        raise TypeError("macro and template catalogs must be mappings")
    if not Macros:
        raise ValueError("macro catalog cannot be empty")

    OrderedMacroItems = tuple(sorted(
        Macros.items(),
        key=lambda Item: repr(Item[0]),
    ))
    OrderedTemplateItems = tuple(sorted(
        Templates.items(),
        key=lambda Item: repr(Item[0]),
    ))
    TemplateEntries = []
    for Kind, Template in OrderedTemplateItems:
        Size = getattr(Template, "Size")
        Blocks = getattr(Template, "Blocks")
        if (
            not isinstance(Size, tuple)
            or len(Size) != 3
            or not all(type(Value) is int for Value in Size)
            or not isinstance(Blocks, Mapping)
        ):
            raise TypeError(f"cell template is malformed for {Kind}")
        TemplateEntries.append((
            _FreezeIdentity(Kind),
            type(Template).__module__,
            type(Template).__qualname__,
            _FreezeIdentity(Size),
            _FreezeIdentity(Blocks),
        ))

    MacroEntries = []
    MaximumEntryCount = 0
    for Kind, Macro in OrderedMacroItems:
        if not isinstance(Kind, str) or Kind != Kind.upper():
            raise ValueError("cell macro keys must be canonical uppercase strings")
        if Kind not in Templates:
            raise ValueError(f"cell template is missing for {Kind}")
        MacroEntries.append((
            Kind,
            _FreezeIdentity(Macro),
        ))
        MaximumEntryCount += 4 * (
            2 if bool(getattr(Macro, "AllowMirror")) else 1
        )

    Identity = (
        ORIENTED_CELL_GEOMETRY_CACHE_SCHEMA,
        ConsumerTechnologyIdentity,
        type(Macros).__module__,
        type(Macros).__qualname__,
        tuple(MacroEntries),
        type(Templates).__module__,
        type(Templates).__qualname__,
        tuple(TemplateEntries),
    )
    return _OrientedGeometryContextSnapshot(
        ValueIdentity=Identity,
        MaximumEntryCount=MaximumEntryCount,
        MacroMapping=Macros,
        TemplateMapping=Templates,
        MacroObjects=tuple(Macro for _Kind, Macro in OrderedMacroItems),
        TemplateObjects=tuple(
            Template for _Kind, Template in OrderedTemplateItems
        ),
    )


def BuildOrientedGeometryPortfolioCacheIdentity(
    Technology: Any,
) -> tuple[object, ...]:
    """Bind a global portfolio entry to exact current producer context."""
    Snapshot = _BuildOrientedGeometryContextSnapshot(Technology)
    return (
        Snapshot.ValueIdentity,
        Snapshot.MaximumEntryCount,
        _IdentityReference(Snapshot.MacroMapping),
        _IdentityReference(Snapshot.TemplateMapping),
        tuple(_IdentityReference(Value) for Value in Snapshot.MacroObjects),
        tuple(
            _IdentityReference(Value) for Value in Snapshot.TemplateObjects
        ),
    )


@dataclass(frozen=True)
class OrientedCellGeometryMasks:
    """The exact four immutable masks stored relative to one cell origin."""

    ActualBlocks: frozenset[Position3]
    ElectricalBlocks: frozenset[Position3]
    SolidBlocks: frozenset[Position3]
    ExplicitKeepOut: frozenset[Position3]


@dataclass(frozen=True)
class ResolvedOrientedCellGeometry:
    """One translated cache result plus its exact electrical exclusions."""

    ActualBlocks: frozenset[Position3]
    ElectricalBlocks: frozenset[Position3]
    SolidBlocks: frozenset[Position3]
    ExplicitKeepOut: frozenset[Position3]
    ElectricalExclusions: frozenset[Position3]


@dataclass(frozen=True)
class OrientedGeometryCacheBuildReceipt:
    """Immutable current-invocation work counts, not portfolio semantics."""

    RequestCount: int = 0
    CacheHitCount: int = 0
    CacheMissCount: int = 0
    EagerFallbackCount: int = 0
    EagerFallbackReasons: tuple[tuple[str, int], ...] = ()
    ProducerCallCount: int = 0
    AvoidedGeometryExpansionCount: int = 0
    TranslationMaterializationCount: int = 0
    RetainedEntryCount: int = 0
    MaximumEntryCount: int = 0
    PortfolioCacheHit: bool = False

    def __post_init__(self) -> None:
        CountValues = (
            self.RequestCount,
            self.CacheHitCount,
            self.CacheMissCount,
            self.EagerFallbackCount,
            self.ProducerCallCount,
            self.AvoidedGeometryExpansionCount,
            self.TranslationMaterializationCount,
            self.RetainedEntryCount,
            self.MaximumEntryCount,
        )
        if any(
            type(Value) is not int or Value < 0
            for Value in CountValues
        ):
            raise ValueError(
                "oriented geometry counts must be nonnegative integers"
            )
        if type(self.PortfolioCacheHit) is not bool:
            raise ValueError("PortfolioCacheHit must be a boolean")
        if type(self.EagerFallbackReasons) is not tuple or any(
            type(Item) is not tuple
            or len(Item) != 2
            or type(Item[0]) is not str
            or not Item[0]
            or Item[0] != Item[0].strip()
            or type(Item[1]) is not int
            or Item[1] <= 0
            for Item in self.EagerFallbackReasons
        ):
            raise ValueError(
                "oriented geometry fallback reasons are malformed"
            )
        if (
            self.EagerFallbackReasons
            != tuple(sorted(self.EagerFallbackReasons))
            or len({
                Reason for Reason, _Count in self.EagerFallbackReasons
            }) != len(self.EagerFallbackReasons)
        ):
            raise ValueError(
                "oriented geometry fallback reasons must be sorted and unique"
            )
        if self.RequestCount != (
            self.CacheHitCount
            + self.CacheMissCount
            + self.EagerFallbackCount
        ):
            raise ValueError("oriented geometry request algebra is invalid")
        if self.ProducerCallCount != (
            self.CacheMissCount + self.EagerFallbackCount
        ):
            raise ValueError("oriented geometry producer algebra is invalid")
        if self.AvoidedGeometryExpansionCount != self.CacheHitCount:
            raise ValueError("oriented geometry avoided-call algebra is invalid")
        if self.TranslationMaterializationCount != (
            self.CacheHitCount + self.CacheMissCount
        ):
            raise ValueError("oriented geometry translation algebra is invalid")
        if sum(Count for _Reason, Count in self.EagerFallbackReasons) != (
            self.EagerFallbackCount
        ):
            raise ValueError("oriented geometry fallback reasons are invalid")
        if not 0 <= self.RetainedEntryCount <= self.MaximumEntryCount:
            raise ValueError("oriented geometry retained-entry bound is invalid")
        if self.PortfolioCacheHit and any((
            self.RequestCount,
            self.CacheHitCount,
            self.CacheMissCount,
            self.EagerFallbackCount,
            self.ProducerCallCount,
            self.AvoidedGeometryExpansionCount,
            self.TranslationMaterializationCount,
            self.RetainedEntryCount,
            self.MaximumEntryCount,
        )):
            raise ValueError(
                "a portfolio cache hit cannot report oriented-cache work"
            )

    @classmethod
    def ForPortfolioCacheHit(cls) -> "OrientedGeometryCacheBuildReceipt":
        return cls(PortfolioCacheHit=True)


class OrientedCellGeometryCacheContext:
    """One finite oriented-mask cache for one uncached portfolio build."""

    def __init__(
        self,
        Technology: Any,
        ExpectedPortfolioIdentity: tuple[object, ...] | None = None,
    ) -> None:
        self._Entries: dict[
            tuple[str, int, bool],
            OrientedCellGeometryMasks,
        ] = {}
        self._RequestCount = 0
        self._CacheHitCount = 0
        self._CacheMissCount = 0
        self._EagerFallbackCount = 0
        self._FallbackReasons: dict[str, int] = {}
        self._ProducerCallCount = 0
        self._TranslationMaterializationCount = 0
        self._CreationFailureReason: str | None = None
        self._Snapshot: _OrientedGeometryContextSnapshot | None = None
        self._MaximumEntryCount = 0
        self._ProducerContextAttested = False
        try:
            self._Snapshot = _BuildOrientedGeometryContextSnapshot(
                Technology
            )
            self._MaximumEntryCount = self._Snapshot.MaximumEntryCount
            CurrentPortfolioIdentity = (
                BuildOrientedGeometryPortfolioCacheIdentity(Technology)
            )
            self._ProducerContextAttested = (
                ExpectedPortfolioIdentity is None
                or CurrentPortfolioIdentity == ExpectedPortfolioIdentity
            )
            if not self._ProducerContextAttested:
                self._CreationFailureReason = "ContextIdentityChanged"
        except (AttributeError, RecursionError, TypeError, ValueError):
            self._CreationFailureReason = "UnsupportedContextIdentity"

    @property
    def MaximumEntryCount(self) -> int:
        return self._MaximumEntryCount

    @property
    def ProducerContextAttested(self) -> bool:
        return self._ProducerContextAttested

    def _RecordFallback(self, Reason: str) -> None:
        self._ProducerContextAttested = False
        self._EagerFallbackCount += 1
        self._FallbackReasons[Reason] = (
            self._FallbackReasons.get(Reason, 0) + 1
        )

    @staticmethod
    def _SingleGatePlacement(Gate: Any) -> Any:
        return type(
            "PortfolioOrientedCellPlacement",
            (),
            {"PlacedGates": [Gate]},
        )()

    def _CallProducer(self, Gate: Any) -> OrientedCellGeometryMasks:
        self._ProducerCallCount += 1
        Actual, Electrical, Solid, ExplicitKeepOut = (
            GeometryRules.BuildPlacedCellGeometryWithKeepOut(
                self._SingleGatePlacement(Gate)
            )
        )
        return OrientedCellGeometryMasks(
            ActualBlocks=frozenset(Actual),
            ElectricalBlocks=frozenset(Electrical),
            SolidBlocks=frozenset(Solid),
            ExplicitKeepOut=frozenset(ExplicitKeepOut),
        )

    def _ResolveEager(
        self,
        Gate: Any,
        Technology: Any,
        Reason: str,
    ) -> ResolvedOrientedCellGeometry:
        self._RecordFallback(Reason)
        Masks = self._CallProducer(Gate)
        return ResolvedOrientedCellGeometry(
            ActualBlocks=Masks.ActualBlocks,
            ElectricalBlocks=Masks.ElectricalBlocks,
            SolidBlocks=Masks.SolidBlocks,
            ExplicitKeepOut=Masks.ExplicitKeepOut,
            ElectricalExclusions=frozenset(
                Technology.BuildElectricalExclusions(
                    set(Masks.ElectricalBlocks)
                )
                | set(Masks.ExplicitKeepOut)
            ),
        )

    @staticmethod
    def _Translate(
        Positions: frozenset[Position3],
        Origin: Position3,
    ) -> frozenset[Position3]:
        return frozenset(
            (
                Position[0] + Origin[0],
                Position[1] + Origin[1],
                Position[2] + Origin[2],
            )
            for Position in Positions
        )

    @staticmethod
    def _Normalize(
        Positions: frozenset[Position3],
        Origin: Position3,
    ) -> frozenset[Position3]:
        return frozenset(
            (
                Position[0] - Origin[0],
                Position[1] - Origin[1],
                Position[2] - Origin[2],
            )
            for Position in Positions
        )

    def _TranslateMasks(
        self,
        Masks: OrientedCellGeometryMasks,
        Origin: Position3,
        Technology: Any,
    ) -> ResolvedOrientedCellGeometry:
        Actual = self._Translate(Masks.ActualBlocks, Origin)
        Electrical = self._Translate(Masks.ElectricalBlocks, Origin)
        Solid = self._Translate(Masks.SolidBlocks, Origin)
        ExplicitKeepOut = self._Translate(Masks.ExplicitKeepOut, Origin)
        self._TranslationMaterializationCount += 1
        return ResolvedOrientedCellGeometry(
            ActualBlocks=Actual,
            ElectricalBlocks=Electrical,
            SolidBlocks=Solid,
            ExplicitKeepOut=ExplicitKeepOut,
            ElectricalExclusions=frozenset(
                Technology.BuildElectricalExclusions(set(Electrical))
                | set(ExplicitKeepOut)
            ),
        )

    def Resolve(
        self,
        Gate: Any,
        Technology: Any,
    ) -> ResolvedOrientedCellGeometry:
        """Resolve one normalized placed gate without weakening eager truth."""
        self._RequestCount += 1
        try:
            Kind = Gate.Kind
            Rotation = Gate.Rotation
            MirrorX = Gate.MirrorX
            Origin = (Gate.X, Gate.Y, Gate.Z)
        except AttributeError:
            return self._ResolveEager(Gate, Technology, "MalformedTransform")
        if (
            not isinstance(Kind, str)
            or Kind != Kind.upper()
            or Kind not in CellLibrary.CellMacros
        ):
            return self._ResolveEager(Gate, Technology, "UnknownCellKind")
        if (
            type(Rotation) is not int
            or Rotation not in (0, 90, 180, 270)
            or type(MirrorX) is not bool
            or not all(type(Value) is int for Value in Origin)
        ):
            return self._ResolveEager(Gate, Technology, "MalformedTransform")

        if self._CreationFailureReason is not None or self._Snapshot is None:
            return self._ResolveEager(
                Gate,
                Technology,
                self._CreationFailureReason or "UnsupportedContextIdentity",
            )
        try:
            CurrentSnapshot = _BuildOrientedGeometryContextSnapshot(Technology)
        except (AttributeError, RecursionError, TypeError, ValueError):
            return self._ResolveEager(
                Gate,
                Technology,
                "UnsupportedContextIdentity",
            )
        if (
            not self._Snapshot.Matches(CurrentSnapshot)
        ):
            return self._ResolveEager(
                Gate,
                Technology,
                "ContextIdentityChanged",
            )

        Macro = CellLibrary.CellMacros[Kind]
        CanonicalMirror = bool(MirrorX and Macro.AllowMirror)
        Key = (Kind, Rotation, CanonicalMirror)
        Masks = self._Entries.get(Key)
        if Masks is not None:
            self._CacheHitCount += 1
            return self._TranslateMasks(Masks, Origin, Technology)

        ProducerGate = Gate
        if CanonicalMirror != MirrorX:
            ProducerGate = type(
                "CanonicalPortfolioOrientedCell",
                (),
                {
                    "Name": Gate.Name,
                    "Kind": Kind,
                    "X": Origin[0],
                    "Y": Origin[1],
                    "Z": Origin[2],
                    "Rotation": Rotation,
                    "MirrorX": CanonicalMirror,
                },
            )()
        self._CacheMissCount += 1
        Produced = self._CallProducer(ProducerGate)
        try:
            SnapshotAfterProducer = _BuildOrientedGeometryContextSnapshot(
                Technology
            )
        except (AttributeError, RecursionError, TypeError, ValueError):
            SnapshotAfterProducer = None
        if (
            SnapshotAfterProducer is None
            or not self._Snapshot.Matches(SnapshotAfterProducer)
            or len(self._Entries) >= self._MaximumEntryCount
        ):
            self._ProducerContextAttested = False
            self._CacheMissCount -= 1
            Reason = (
                "FiniteBoundExceeded"
                if (
                    SnapshotAfterProducer is not None
                    and self._Snapshot.Matches(SnapshotAfterProducer)
                )
                else "ContextIdentityChangedDuringProducer"
            )
            self._RecordFallback(Reason)
            return ResolvedOrientedCellGeometry(
                ActualBlocks=Produced.ActualBlocks,
                ElectricalBlocks=Produced.ElectricalBlocks,
                SolidBlocks=Produced.SolidBlocks,
                ExplicitKeepOut=Produced.ExplicitKeepOut,
                ElectricalExclusions=frozenset(
                    Technology.BuildElectricalExclusions(
                        set(Produced.ElectricalBlocks)
                    )
                    | set(Produced.ExplicitKeepOut)
                ),
            )

        Relative = OrientedCellGeometryMasks(
            ActualBlocks=self._Normalize(Produced.ActualBlocks, Origin),
            ElectricalBlocks=self._Normalize(
                Produced.ElectricalBlocks,
                Origin,
            ),
            SolidBlocks=self._Normalize(Produced.SolidBlocks, Origin),
            ExplicitKeepOut=self._Normalize(
                Produced.ExplicitKeepOut,
                Origin,
            ),
        )
        self._Entries[Key] = Relative
        return self._TranslateMasks(Relative, Origin, Technology)

    def BuildReceipt(self) -> OrientedGeometryCacheBuildReceipt:
        """Freeze deterministic current-context metrics for its caller."""
        return OrientedGeometryCacheBuildReceipt(
            RequestCount=self._RequestCount,
            CacheHitCount=self._CacheHitCount,
            CacheMissCount=self._CacheMissCount,
            EagerFallbackCount=self._EagerFallbackCount,
            EagerFallbackReasons=tuple(sorted(self._FallbackReasons.items())),
            ProducerCallCount=self._ProducerCallCount,
            AvoidedGeometryExpansionCount=self._CacheHitCount,
            TranslationMaterializationCount=(
                self._TranslationMaterializationCount
            ),
            RetainedEntryCount=len(self._Entries),
            MaximumEntryCount=self._MaximumEntryCount,
        )


class EagerCurrentCellGeometryResolver:
    """Resolve exact current geometry without using a geometry cache."""

    def __init__(
        self,
        Technology: Any,
        ExpectedPortfolioIdentity: tuple[object, ...] | None,
    ) -> None:
        self._ExpectedPortfolioIdentity = ExpectedPortfolioIdentity
        self._ProducerContextAttested = self._IdentityMatches(Technology)

    def _IdentityMatches(self, Technology: Any) -> bool:
        if self._ExpectedPortfolioIdentity is None:
            return False
        try:
            return BuildOrientedGeometryPortfolioCacheIdentity(
                Technology
            ) == self._ExpectedPortfolioIdentity
        except (AttributeError, RecursionError, TypeError, ValueError):
            return False

    @property
    def ProducerContextAttested(self) -> bool:
        return self._ProducerContextAttested

    def Resolve(
        self,
        Gate: Any,
        Technology: Any,
    ) -> ResolvedOrientedCellGeometry:
        """Call the public producer once for this exact current request."""
        if not self._IdentityMatches(Technology):
            self._ProducerContextAttested = False
        Placement = type(
            "PortfolioEagerCurrentCellPlacement",
            (),
            {"PlacedGates": [Gate]},
        )()
        try:
            Actual, Electrical, Solid, ExplicitKeepOut = (
                GeometryRules.BuildPlacedCellGeometryWithKeepOut(Placement)
            )
        finally:
            if not self._IdentityMatches(Technology):
                self._ProducerContextAttested = False
        FrozenElectrical = frozenset(Electrical)
        FrozenKeepOut = frozenset(ExplicitKeepOut)
        return ResolvedOrientedCellGeometry(
            ActualBlocks=frozenset(Actual),
            ElectricalBlocks=FrozenElectrical,
            SolidBlocks=frozenset(Solid),
            ExplicitKeepOut=FrozenKeepOut,
            ElectricalExclusions=frozenset(
                Technology.BuildElectricalExclusions(
                    set(FrozenElectrical)
                )
                | set(FrozenKeepOut)
            ),
        )


_JointPlacementSearchCache: dict[
    tuple[object, ...],
    dict[str, object],
] = {}

_JointPlacementExactScreenCache: dict[
    tuple[object, ...],
    "ExactJointPlacementScreen",
] = {}

_ExactStatePlacementGeometryCache: dict[
    tuple[object, ...],
    tuple["ExactStatePlacedGateGeometry", ...],
] = {}

_PackedClusterBaseLayoutCache: dict[
    tuple[object, ...],
    tuple[
        str,
        int | None,
        dict[str, str],
        dict[str, tuple[int, int]],
        dict[str, int],
        dict[str, bool],
        int,
        int,
    ],
] = {}

_PinAlignedPackedClusterPortfolioCache: dict[
    tuple[object, ...],
    tuple["PinAlignedPackedClusterState", ...],
] = {}

_PlacementTopologyCache: dict[
    tuple[object, ...],
    tuple[
        tuple[tuple[str, int], ...],
        tuple[tuple[str, ...], ...],
    ],
] = {}

_ClusterLocalRouteTemplateCache: dict[
    tuple[object, ...],
    "ClusterLocalRouteTemplateCacheEntry",
] = {}
