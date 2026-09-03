"""Deterministic coarse-guide and interface planning for closed components.

This module owns the finite component-boundary CSP.  It deliberately stops
before exact exterior candidate generation and before local route
materialization.  The authoritative planner may consume the selected
contract, but it may not mutate the guide or reopen a rejected assignment.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import json
import os
from typing import Any

try:
    from RedstoneCompiler.RustRouting import SolveLeaseDomainsBounded as _SolveLeaseDomainsBounded
except ImportError:
    try:
        from RedstoneCompiler.RustRouting import SolveLeaseDomainsBounded as _SolveLeaseDomainsBounded
    except Exception:
        _SolveLeaseDomainsBounded = None

from ....Contracts.Component import PhysicalComponentBoundaryPortReservation
from ....Contracts.PhysicalInterface import PreparedPhysicalComponentPortFactorDomain
from ....Execution.Reliability import BuildStableFingerprint


CoarseResource = tuple[int, ...]
ContractKey = tuple[str, str]
NoGoodClause = frozenset[ContractKey]


def _Fingerprint(Value: object) -> str:
    Payload = json.dumps(
        Value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256(Payload).hexdigest()[:16]


def _LeaseOptionKey(OptionFingerprint: str) -> str:
    """Reserve a contract-key namespace for an exact selected-option no-good."""
    return "lease-option:" + str(OptionFingerprint)


def _PythonFixtureOracleEnabled() -> bool:
    """Permit the retired solver only under pytest fixture execution."""
    return (
        os.environ.get("RC_COMPONENT_LEASE_SOLVER", "").lower()
        == "python"
        and "PYTEST_CURRENT_TEST" in os.environ
    )


class ComponentPlanningStatus(str, Enum):
    Feasible = "Feasible"
    PlacementUnsatisfiable = "PlacementUnsatisfiable"
    InterfaceUnsatisfiable = "InterfaceUnsatisfiable"
    SearchIncomplete = "SearchIncomplete"


@dataclass(frozen=True)
class ComponentCapacityGuideOption:
    """One globally owned aperture projected onto the coarse tile graph."""

    Signal: str
    OptionFingerprint: str
    ContractKeys: frozenset[ContractKey]
    CoarseCells: frozenset[CoarseResource]
    Layer: int
    Cost: int
    BaseOverflow: int
    BoundaryPort: PhysicalComponentBoundaryPortReservation = field(
        compare=False,
        repr=False,
    )
    LocalAccessFingerprint: str = ""
    LocalContractFingerprint: str = ""
    SeamContractFingerprint: str = ""
    LocalSupportFingerprint: str = ""
    PortReservationFingerprint: str = ""


@dataclass(frozen=True)
class ComponentCapacityGuide:
    """Complete coarse capacity domain for one placed closed component."""

    GuideFingerprint: str
    PlacementFingerprint: str
    ResourceGraphFingerprint: str
    TechnologyFingerprint: str
    TilePitch: int
    CorridorCapacity: int
    BaseUsage: tuple[tuple[CoarseResource, int], ...]
    OptionsBySignal: tuple[
        tuple[str, tuple[ComponentCapacityGuideOption, ...]], ...
    ]
    Complete: bool
    Diagnostics: dict[str, object] = field(
        default_factory=dict,
        compare=False,
    )

    def Domains(self) -> dict[
        str, tuple[ComponentCapacityGuideOption, ...]
    ]:
        return dict(self.OptionsBySignal)


@dataclass(frozen=True)
class ComponentInterfaceContract:
    """One zero-overflow component boundary selected before exact routing."""

    AssignmentFingerprint: str
    GuideFingerprint: str
    PlacementFingerprint: str
    SelectedOptionFingerprints: tuple[tuple[str, str], ...]
    SelectedBoundaryPorts: tuple[
        PhysicalComponentBoundaryPortReservation, ...
    ]
    SelectedLocalAccessFingerprints: tuple[tuple[str, str], ...]
    SelectedSeamContractFingerprints: tuple[tuple[str, str], ...]
    SelectedLocalSupportFingerprints: tuple[tuple[str, str], ...]
    CoarseUsage: tuple[tuple[CoarseResource, int], ...]
    Overflow: tuple[tuple[CoarseResource, int], ...]
    Cost: int
    ProofFingerprint: str


@dataclass(frozen=True)
class ComponentPlanningResult:
    """Typed outcome of the complete finite component-interface CSP."""

    Status: ComponentPlanningStatus
    Guide: ComponentCapacityGuide | None
    Contract: ComponentInterfaceContract | None
    PlacementProofComplete: bool
    InterfaceProofComplete: bool
    GlobalPlanningEntered: bool
    LocalCompilationEntered: bool
    Detail: str
    Diagnostics: dict[str, object] = field(
        default_factory=dict,
        compare=False,
    )

    @property
    def Feasible(self) -> bool:
        return (
            self.Status == ComponentPlanningStatus.Feasible
            and self.Contract is not None
        )


def _BoundaryContractKeys(
    Value: PhysicalComponentBoundaryPortReservation,
) -> frozenset[ContractKey]:
    return frozenset((
        (str(Value.Signal), str(Value.GlobalContractFingerprint)),
        (str(Value.Signal), str(Value.ApertureContractFingerprint)),
        (str(Value.Signal), str(Value.ReservationFingerprint)),
    ))


def _LocalContractKeys(
    Signal: str,
    LocalAccessFingerprint: str,
    LocalContractFingerprint: str,
    SeamContractFingerprint: str,
    SupportFingerprint: str,
) -> frozenset[ContractKey]:
    return frozenset(
        (str(Signal), str(Fingerprint))
        for Fingerprint in (
            LocalAccessFingerprint,
            LocalContractFingerprint,
            SeamContractFingerprint,
            SupportFingerprint,
        )
        if str(Fingerprint)
    )


def _CoarseCells(
    Value: PhysicalComponentBoundaryPortReservation,
    TilePitch: int,
    *,
    Layer: int,
    LayerAware: bool,
) -> frozenset[CoarseResource]:
    # ChannelPlan owns a block-grid congestion ledger.  Keep that exact
    # coordinate system here; TilePitch describes the coarser planning scale
    # but must not be applied to only one side of the capacity equation.
    del TilePitch
    Path = Value.GlobalPath or (Value.Attachment,)
    if LayerAware:
        return frozenset(
            (int(Layer), int(Position[0]), int(Position[2]))
            for Position in Path
        )
    return frozenset(
        (int(Position[0]), int(Position[2]))
        for Position in Path
    )


def BuildComponentCapacityGuide(
    Preparation: PreparedPhysicalComponentPortFactorDomain,
    *,
    TrackPitch: int,
    IncludeLocalCompositeFactors: bool = True,
) -> ComponentCapacityGuide:
    """Project all-net coarse congestion onto component aperture options."""
    if TrackPitch <= 0:
        raise ValueError("TrackPitch must be positive")
    TilePitch = 4 * int(TrackPitch)
    PortSolverCacheKey = BuildStableFingerprint((
        "physical-component-port-solver-cache-v2",
        str(getattr(
            Preparation,
            "DomainFingerprint",
            Preparation.PlacementFingerprint,
        )),
    ))
    Domains = {
        str(Signal): tuple(Values)
        for Signal, Values
        in Preparation.BoundaryPortReservationsBySignal
    }
    LocalFactorsBySignal = {
        str(Signal): {
            str(Value.LocalAccessFingerprint): Value
            for Value in Values
        }
        for Signal, Values in getattr(
            Preparation,
            "LocalAccessFactorsBySignal",
            (),
        )
    }
    ApertureFactorsBySignal = {
        str(Signal): tuple(Values)
        for Signal, Values in getattr(
            Preparation,
            "ApertureFactorsBySignal",
            (),
        )
    }
    SupportsByOption = {
        (str(Key[0]), str(Key[1])): tuple(Values)
        for Key, Values in getattr(
            Preparation,
            "LocalApertureSupportsByOption",
            (),
        )
    }
    CoarsePlan = Preparation.CoarsePlan
    LayerAware = hasattr(CoarsePlan, "Usage")
    if LayerAware:
        Overflow = dict(getattr(CoarsePlan, "Overflow", {}))
        InferredCapacities = {
            int(getattr(CoarsePlan, "Usage", {}).get(Cell, 0))
            - int(Value)
            for Cell, Value in Overflow.items()
        }
        Capacity = max(1, min(InferredCapacities, default=1))
        BaseUsage = Counter({
            tuple(map(int, Cell)): int(Usage)
            for Cell, Usage in getattr(CoarsePlan, "Usage", {}).items()
        })
    else:
        Capacity = max(1, int(CoarsePlan.CorridorCapacity))
        BaseUsage = Counter({
            (int(Cell[0]), int(Cell[1])): int(Usage)
            for Cell, Usage in CoarsePlan.CorridorUsage.items()
        })
    # The input coarse plan already contains provisional guides for these
    # signals.  Remove them before evaluating alternate component apertures so
    # the selected option is counted exactly once while every ordinary net
    # remains represented in BaseUsage.
    for Signal in Domains:
        for Cell in CoarsePlan.Guides.get(Signal, ()):
            Key = (
                (
                    int(CoarsePlan.Layers.get(Signal, 0)),
                    int(Cell[0]),
                    int(Cell[1]),
                )
                if LayerAware
                else (int(Cell[0]), int(Cell[1]))
            )
            BaseUsage[Key] -= 1
            if BaseUsage[Key] <= 0:
                del BaseUsage[Key]

    OptionsBySignal = []
    CoarseLayers = getattr(CoarsePlan, "Layers", {})
    for Signal in sorted(Domains):
        ByFingerprint: dict[str, ComponentCapacityGuideOption] = {}
        for Boundary in Domains[Signal]:
            if str(Boundary.Signal) != Signal:
                raise ValueError(
                    "component capacity guide boundary stored under wrong signal"
                )
            Layer = int(CoarseLayers.get(
                Signal,
                Boundary.Attachment[1],
            ))
            Cells = _CoarseCells(
                Boundary,
                TilePitch,
                Layer=Layer,
                LayerAware=LayerAware,
            )
            BaseOverflow = sum(
                max(0, int(BaseUsage.get(Cell, 0)) + 1 - Capacity)
                for Cell in Cells
            )
            CorridorCosts = getattr(CoarsePlan, "CorridorCosts", {})
            CorridorCost = sum(
                int(CorridorCosts.get(Cell, 0))
                for Cell in Cells
            )
            MatchingApertures = tuple(
                Aperture
                for Aperture in ApertureFactorsBySignal.get(Signal, ())
                if (
                    str(Aperture.GlobalContractFingerprint)
                    == str(Boundary.GlobalContractFingerprint)
                    and str(Aperture.ApertureContractFingerprint)
                    == str(Boundary.ApertureContractFingerprint)
                )
            )
            LocalSelections = (
                tuple(
                    (
                        LocalFactor,
                        Support,
                    )
                    for Aperture in MatchingApertures
                    for Support in SupportsByOption.get((
                        Signal,
                        str(Aperture.ApertureOptionFingerprint),
                    ), ())
                    for LocalFactor in (
                        LocalFactorsBySignal.get(Signal, {}).get(
                            str(Support.LocalAccessFingerprint)
                        ),
                    )
                    if LocalFactor is not None
                )
                if IncludeLocalCompositeFactors
                else ((None, None),)
            )
            if not LocalSelections and not LocalFactorsBySignal:
                LocalSelections = ((None, None),)
            for LocalFactor, Support in LocalSelections:
                LocalAccessFingerprint = str(getattr(
                    LocalFactor,
                    "LocalAccessFingerprint",
                    "",
                ))
                LocalContractFingerprint = str(getattr(
                    LocalFactor,
                    "LocalContractFingerprint",
                    "",
                ))
                SeamContractFingerprint = str(getattr(
                    LocalFactor,
                    "SeamContractFingerprint",
                    "",
                ))
                SupportFingerprint = str(getattr(
                    Support,
                    "SupportFingerprint",
                    "",
                ))
                PortReservationFingerprint = str(getattr(
                    Support,
                    "ReservationFingerprint",
                    "",
                ))
                Fingerprint = _Fingerprint((
                    "component-capacity-guide-option-v2",
                    Signal,
                    Boundary.ReservationFingerprint,
                    LocalAccessFingerprint,
                    LocalContractFingerprint,
                    SeamContractFingerprint,
                    SupportFingerprint,
                    PortReservationFingerprint,
                    tuple(sorted(Cells)),
                    Layer,
                    tuple(sorted(map(str, getattr(
                        Boundary.GlobalClaims,
                        "ResourceIds",
                        (),
                    )))),
                ))
                ByFingerprint.setdefault(
                    Fingerprint,
                    ComponentCapacityGuideOption(
                        Signal=Signal,
                        OptionFingerprint=Fingerprint,
                        ContractKeys=(
                            _BoundaryContractKeys(Boundary)
                            | _LocalContractKeys(
                                Signal,
                                LocalAccessFingerprint,
                                LocalContractFingerprint,
                                SeamContractFingerprint,
                                SupportFingerprint,
                            )
                            | frozenset((
                                (
                                    Signal,
                                    "local-signal-domain:"
                                    + PortSolverCacheKey,
                                ),
                                *((
                                    (
                                        Signal,
                                        PortReservationFingerprint,
                                    ),
                                    (
                                        Signal,
                                        "scoped-request-reservation:"
                                        + PortSolverCacheKey
                                        + ":"
                                        + PortReservationFingerprint,
                                    ),
                                ) if PortReservationFingerprint else ()),
                            ))
                        ),
                        CoarseCells=Cells,
                        Layer=Layer,
                        Cost=(
                            len(Boundary.GlobalPath)
                            + CorridorCost
                            + 1_000 * BaseOverflow
                        ),
                        BaseOverflow=BaseOverflow,
                        BoundaryPort=Boundary,
                        LocalAccessFingerprint=(
                            LocalAccessFingerprint
                        ),
                        LocalContractFingerprint=(
                            LocalContractFingerprint
                        ),
                        SeamContractFingerprint=(
                            SeamContractFingerprint
                        ),
                        LocalSupportFingerprint=SupportFingerprint,
                        PortReservationFingerprint=(
                            PortReservationFingerprint
                        ),
                    ),
                )
        Options = tuple(sorted(
            ByFingerprint.values(),
            key=lambda Value: (
                Value.BaseOverflow,
                str(Value.BoundaryPort.Direction),
                int(Value.BoundaryPort.Capacity),
                tuple(Value.BoundaryPort.Attachment),
                tuple(Value.BoundaryPort.GlobalPath),
                tuple(sorted(map(str, getattr(
                    Value.BoundaryPort.GlobalClaims,
                    "ResourceIds",
                    (),
                )))),
                str(Value.BoundaryPort.ChannelContractFingerprint),
                str(Value.BoundaryPort.GlobalContractFingerprint),
                str(Value.BoundaryPort.ApertureContractFingerprint),
                str(Value.BoundaryPort.ReservationFingerprint),
            ),
        ))
        OptionsBySignal.append((Signal, Options))

    Complete = bool(
        Preparation.Complete
        and Domains
        and all(Values for _Signal, Values in OptionsBySignal)
    )
    GuideFingerprint = _Fingerprint((
        "component-capacity-guide-v1",
        Preparation.PlacementFingerprint,
        Preparation.ResourceGraphFingerprint,
        Preparation.ExteriorCapacityLedgerFingerprint,
        TilePitch,
        Capacity,
        tuple(sorted(BaseUsage.items())),
        bool(IncludeLocalCompositeFactors),
        tuple(
            (
                Signal,
                tuple(Value.OptionFingerprint for Value in Values),
            )
            for Signal, Values in OptionsBySignal
        ),
    ))
    return ComponentCapacityGuide(
        GuideFingerprint=GuideFingerprint,
        PlacementFingerprint=Preparation.PlacementFingerprint,
        ResourceGraphFingerprint=Preparation.ResourceGraphFingerprint,
        TechnologyFingerprint=str(getattr(
            Preparation.AccessCertificate,
            "TechnologyFingerprint",
            "",
        )),
        TilePitch=TilePitch,
        CorridorCapacity=Capacity,
        BaseUsage=tuple(sorted(BaseUsage.items())),
        OptionsBySignal=tuple(OptionsBySignal),
        Complete=Complete,
        Diagnostics={
            "SignalCount": len(OptionsBySignal),
            "OptionCount": sum(
                len(Values) for _Signal, Values in OptionsBySignal
            ),
            "BaseCongestedCellCount": sum(
                int(Usage > Capacity) for Usage in BaseUsage.values()
            ),
            "AllNetCoarseDemandIncluded": True,
            "LayerAwareCapacity": LayerAware,
            "LocalCompositeFactorsIncluded": bool(
                IncludeLocalCompositeFactors
            ),
        },
    )


def SolveComponentInterfaceCsp(
    Guide: ComponentCapacityGuide,
    *,
    RejectedClauses: Iterable[NoGoodClause] = (),
    RejectedAssignmentFingerprints: Iterable[str] = (),
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    MaximumExpansions: int | None = None,
    MaximumRuntimeSeconds: float | None = None,
    PairSupportMaskCache: dict[str, Any] | None = None,
    PreferredGlobalContractsBySignal: Mapping[str, str] | None = None,
    PreferredApertureContractsBySignal: Mapping[str, str] | None = None,
    PreferredPortReservationsBySignal: Mapping[str, str] | None = None,
    AperturePortalSlackBySignal: Mapping[
        str, Mapping[str, tuple[int, int]]
    ] | None = None,
) -> ComponentPlanningResult:
    """Solve one complete deterministic component-interface domain."""
    if not Guide.Complete:
        return ComponentPlanningResult(
            Status=ComponentPlanningStatus.PlacementUnsatisfiable,
            Guide=Guide,
            Contract=None,
            PlacementProofComplete=True,
            InterfaceProofComplete=True,
            GlobalPlanningEntered=True,
            LocalCompilationEntered=False,
            Detail="the placed component has an empty complete aperture domain",
            Diagnostics={"ExpansionCount": 0},
        )
    PythonFixtureOracle = _PythonFixtureOracleEnabled()
    RejectedAssignmentFingerprintValues = tuple(map(
        str,
        RejectedAssignmentFingerprints,
    ))
    if not PythonFixtureOracle and _SolveLeaseDomainsBounded is None:
        return ComponentPlanningResult(
            Status=ComponentPlanningStatus.SearchIncomplete,
            Guide=Guide,
            Contract=None,
            PlacementProofComplete=False,
            InterfaceProofComplete=False,
            GlobalPlanningEntered=True,
            LocalCompilationEntered=False,
            Detail="native component lease solver is unavailable",
            Diagnostics={
                "ExpansionCount": 0,
                "NativeLeaseSolver": False,
                "NativeLeaseUnavailable": True,
            },
        )
    if not PythonFixtureOracle and RejectedAssignmentFingerprintValues:
        return ComponentPlanningResult(
            Status=ComponentPlanningStatus.SearchIncomplete,
            Guide=Guide,
            Contract=None,
            PlacementProofComplete=False,
            InterfaceProofComplete=False,
            GlobalPlanningEntered=True,
            LocalCompilationEntered=False,
            Detail=(
                "native component lease solver cannot encode a legacy "
                "assignment fingerprint"
            ),
            Diagnostics={
                "ExpansionCount": 0,
                "NativeLeaseSolver": False,
                "NativeLeaseUnsupportedLegacyRejection": True,
            },
        )
    Clauses = tuple(sorted(
        {
            frozenset(
                (str(Signal), str(Fingerprint))
                for Signal, Fingerprint in Clause
            )
            for Clause in RejectedClauses
            if Clause
        },
        key=lambda Value: tuple(sorted(Value)),
    ))
    UnaryRejectedKeys = frozenset(
        next(iter(Clause)) for Clause in Clauses if len(Clause) == 1
    )
    MutableBinaryRejectedPartners: dict[ContractKey, set[ContractKey]] = {}
    for Clause in Clauses:
        if len(Clause) != 2:
            continue
        First, Second = tuple(Clause)
        MutableBinaryRejectedPartners.setdefault(First, set()).add(Second)
        MutableBinaryRejectedPartners.setdefault(Second, set()).add(First)
    BinaryRejectedPartners = {
        Key: frozenset(Values)
        for Key, Values in MutableBinaryRejectedPartners.items()
    }
    MutableHigherOrderClausesByPivotKey: dict[
        ContractKey, list[NoGoodClause]
    ] = {}
    for Clause in Clauses:
        if len(Clause) <= 2:
            continue
        MutableHigherOrderClausesByPivotKey.setdefault(
            min(Clause),
            [],
        ).append(Clause)
    HigherOrderClausesByPivotKey = {
        Key: tuple(Values)
        for Key, Values in MutableHigherOrderClausesByPivotKey.items()
    }
    ClauseDegreeBySignal = Counter()
    ClauseDegreeByContractKey = Counter()
    for Clause in Clauses:
        ClauseDegreeBySignal.update(frozenset(
            Signal for Signal, _Fingerprint in Clause
        ))
        ClauseDegreeByContractKey.update(Clause)
    ClauseIndexLookupCount = 0
    HigherOrderClauseSubsetCheckCount = 0
    WorkPollCount = 0

    def PollWork(Stage: str) -> None:
        nonlocal WorkPollCount
        WorkPollCount += 1
        if WorkCheck is not None and (
            WorkPollCount == 1 or WorkPollCount % 64 == 0
        ):
            WorkCheck({
                "Stage": Stage,
                "WorkPollCount": WorkPollCount,
                "ClauseIndexLookupCount": ClauseIndexLookupCount,
                "HigherOrderClauseSubsetCheckCount": (
                    HigherOrderClauseSubsetCheckCount
                ),
                "ImplicitForeignTransitDomainCount": 0,
            })

    def ViolatesClause(Keys: frozenset[ContractKey]) -> bool:
        nonlocal ClauseIndexLookupCount
        nonlocal HigherOrderClauseSubsetCheckCount
        ClauseIndexLookupCount += len(Keys)
        if Keys & UnaryRejectedKeys:
            return True
        if any(
            BinaryRejectedPartners.get(Key, frozenset()) & Keys
            for Key in Keys
        ):
            return True
        for Key in Keys:
            for Clause in HigherOrderClausesByPivotKey.get(Key, ()):
                HigherOrderClauseSubsetCheckCount += 1
                if Clause <= Keys:
                    return True
        return False

    RejectedAssignments = frozenset(map(
        str,
        RejectedAssignmentFingerprintValues,
    ))
    BaseUsage = dict(Guide.BaseUsage)
    Domains = Guide.Domains()
    PreferredGlobalContracts = (
        PreferredGlobalContractsBySignal
        if PreferredGlobalContractsBySignal is not None
        else {}
    )
    PreferredPortReservations = (
        PreferredPortReservationsBySignal
        if PreferredPortReservationsBySignal is not None
        else {}
    )
    PreferredApertureContracts = (
        PreferredApertureContractsBySignal
        if PreferredApertureContractsBySignal is not None
        else {}
    )
    AperturePortalSlack = (
        AperturePortalSlackBySignal
        if AperturePortalSlackBySignal is not None
        else {}
    )

    def OrderOptions(
        Signal: str,
        Options: Iterable[ComponentCapacityGuideOption],
    ) -> list[ComponentCapacityGuideOption]:
        PreferredGlobal = str(
            PreferredGlobalContracts.get(Signal, "")
        )
        PreferredReservation = str(
            PreferredPortReservations.get(Signal, "")
        )
        PreferredAperture = str(
            PreferredApertureContracts.get(Signal, "")
        )
        return sorted(
            Options,
            key=lambda Option: (
                bool(PreferredGlobal)
                and str(
                    Option.BoundaryPort.GlobalContractFingerprint
                ) != PreferredGlobal,
                bool(PreferredAperture)
                and str(
                    Option.BoundaryPort.ApertureContractFingerprint
                ) != PreferredAperture,
                bool(PreferredReservation)
                and Option.PortReservationFingerprint
                != PreferredReservation,
                -int(AperturePortalSlack.get(Signal, {}).get(
                    str(
                        Option.BoundaryPort
                        .ApertureContractFingerprint
                    ),
                    (0, 0),
                )[0]),
                -int(AperturePortalSlack.get(Signal, {}).get(
                    str(
                        Option.BoundaryPort
                        .ApertureContractFingerprint
                    ),
                    (0, 0),
                )[1]),
                sum(
                    ClauseDegreeByContractKey[Key]
                    for Key in Option.ContractKeys
                ),
            ),
        )
    StaticIndexCacheKey = (
        "component-interface-static-index-v1:"
        + Guide.GuideFingerprint
    )
    CachedStaticIndexes = (
        PairSupportMaskCache.get(StaticIndexCacheKey)
        if PairSupportMaskCache is not None
        else None
    )
    if isinstance(CachedStaticIndexes, dict):
        DomainCoarseCells = CachedStaticIndexes["DomainCoarseCells"]
        DomainOptionIndexes = CachedStaticIndexes[
            "DomainOptionIndexes"
        ]
        OptionMasksByKeyBySignal = CachedStaticIndexes[
            "OptionMasksByKeyBySignal"
        ]
        OptionMasksByCellBySignal = CachedStaticIndexes[
            "OptionMasksByCellBySignal"
        ]
    else:
        DomainCoarseCells = {
            Signal: frozenset(
                Cell
                for Option in Options
                for Cell in Option.CoarseCells
            )
            for Signal, Options in Domains.items()
        }
        DomainOptionIndexes = {
            Signal: {
                Option.OptionFingerprint: Index
                for Index, Option in enumerate(Options)
            }
            for Signal, Options in Domains.items()
        }
        OptionMasksByKeyBySignal: dict[
            str, dict[ContractKey, int]
        ] = {}
        OptionMasksByCellBySignal: dict[
            str, dict[CoarseResource, int]
        ] = {}
        for Signal, Options in Domains.items():
            MasksByKey: dict[ContractKey, int] = {}
            MasksByCell: dict[CoarseResource, int] = {}
            for Index, Option in enumerate(Options):
                Bit = 1 << Index
                for Key in Option.ContractKeys:
                    MasksByKey[Key] = MasksByKey.get(Key, 0) | Bit
                for Cell in Option.CoarseCells:
                    MasksByCell[Cell] = MasksByCell.get(Cell, 0) | Bit
            OptionMasksByKeyBySignal[Signal] = MasksByKey
            OptionMasksByCellBySignal[Signal] = MasksByCell
        if PairSupportMaskCache is not None:
            PairSupportMaskCache[StaticIndexCacheKey] = {
                "DomainCoarseCells": DomainCoarseCells,
                "DomainOptionIndexes": DomainOptionIndexes,
                "OptionMasksByKeyBySignal": (
                    OptionMasksByKeyBySignal
                ),
                "OptionMasksByCellBySignal": (
                    OptionMasksByCellBySignal
                ),
            }
    RelevantSignalPairs = frozenset(
        tuple(sorted((First, Second)))
        for First in Domains
        for Second in Domains
        if First < Second
        and (
            not DomainCoarseCells[First].isdisjoint(
                DomainCoarseCells[Second]
            )
            or any(
                First in frozenset(Signal for Signal, _Value in Clause)
                and Second in frozenset(
                    Signal for Signal, _Value in Clause
                )
                for Clause in Clauses
            )
        )
    )
    PairSupportMasks: dict[tuple[str, int, str], int] = {}
    BinaryClauses = frozenset(
        Clause for Clause in Clauses if len(Clause) == 2
    )
    PairSupportCacheKey = (
        "component-interface-binary-support-v2:"
        + Guide.GuideFingerprint
    )
    CachedPairSupportState = (
        PairSupportMaskCache.get(PairSupportCacheKey)
        if PairSupportMaskCache is not None
        else None
    )
    CachedBinaryClauses = frozenset(
        CachedPairSupportState.get("BinaryClauses", ())
        if isinstance(CachedPairSupportState, dict)
        else ()
    )
    CachedRelevantSignalPairs = frozenset(
        tuple(Value)
        for Value in (
            CachedPairSupportState.get("RelevantSignalPairs", ())
            if isinstance(CachedPairSupportState, dict)
            else ()
        )
    )
    PairSupportMaskCacheHit = bool(
        isinstance(CachedPairSupportState, dict)
        and CachedBinaryClauses <= BinaryClauses
        and CachedRelevantSignalPairs <= RelevantSignalPairs
        and isinstance(
            CachedPairSupportState.get("PairSupportMasks"),
            dict,
        )
    )
    if PairSupportMaskCacheHit:
        PairSupportMasks = dict(
            CachedPairSupportState["PairSupportMasks"]
        )
        for FirstSignal, SecondSignal in sorted(RelevantSignalPairs):
            for SourceSignal, TargetSignal in (
                (FirstSignal, SecondSignal),
                (SecondSignal, FirstSignal),
            ):
                AllTargetMask = (
                    (1 << len(Domains[TargetSignal])) - 1
                )
                for SourceIndex in range(len(Domains[SourceSignal])):
                    PairSupportMasks.setdefault(
                        (SourceSignal, SourceIndex, TargetSignal),
                        AllTargetMask,
                    )
    else:
        for FirstSignal, SecondSignal in sorted(RelevantSignalPairs):
            for SourceSignal, TargetSignal in (
                (FirstSignal, SecondSignal),
                (SecondSignal, FirstSignal),
            ):
                AllTargetMask = (
                    (1 << len(Domains[TargetSignal])) - 1
                )
                for SourceIndex in range(len(Domains[SourceSignal])):
                    PairSupportMasks[(
                        SourceSignal,
                        SourceIndex,
                        TargetSignal,
                    )] = AllTargetMask
    ClausesToApply = (
        BinaryClauses - CachedBinaryClauses
        if PairSupportMaskCacheHit
        else BinaryClauses
    )

    def RemovePairSupport(
        SourceSignal: str,
        SourceMask: int,
        TargetSignal: str,
        TargetMask: int,
    ) -> None:
        while SourceMask:
            SourceBit = SourceMask & -SourceMask
            SourceIndex = SourceBit.bit_length() - 1
            Key = (SourceSignal, SourceIndex, TargetSignal)
            PairSupportMasks[Key] &= ~TargetMask
            SourceMask ^= SourceBit

    for Clause in sorted(
        ClausesToApply,
        key=lambda Value: tuple(sorted(Value)),
    ):
        PollWork("component-interface-binary-certificates")
        FirstKey, SecondKey = tuple(Clause)
        FirstSignal, SecondSignal = FirstKey[0], SecondKey[0]
        if (
            FirstSignal == SecondSignal
            or tuple(sorted((FirstSignal, SecondSignal)))
            not in RelevantSignalPairs
        ):
            continue
        FirstMask = OptionMasksByKeyBySignal[FirstSignal].get(
            FirstKey,
            0,
        )
        SecondMask = OptionMasksByKeyBySignal[SecondSignal].get(
            SecondKey,
            0,
        )
        RemovePairSupport(
            FirstSignal,
            FirstMask,
            SecondSignal,
            SecondMask,
        )
        RemovePairSupport(
            SecondSignal,
            SecondMask,
            FirstSignal,
            FirstMask,
        )
    CoarsePairsToApply = (
        RelevantSignalPairs - CachedRelevantSignalPairs
        if PairSupportMaskCacheHit
        else RelevantSignalPairs
    )
    for FirstSignal, SecondSignal in sorted(CoarsePairsToApply):
        PollWork("component-interface-coarse-capacity")
        SharedCells = (
            OptionMasksByCellBySignal[FirstSignal].keys()
            & OptionMasksByCellBySignal[SecondSignal].keys()
        )
        for Cell in SharedCells:
            if (
                int(BaseUsage.get(Cell, 0)) + 2
                <= max(
                    Guide.CorridorCapacity,
                    int(BaseUsage.get(Cell, 0)),
                )
            ):
                continue
            FirstMask = OptionMasksByCellBySignal[FirstSignal][Cell]
            SecondMask = OptionMasksByCellBySignal[SecondSignal][Cell]
            RemovePairSupport(
                FirstSignal,
                FirstMask,
                SecondSignal,
                SecondMask,
            )
            RemovePairSupport(
                SecondSignal,
                SecondMask,
                FirstSignal,
                FirstMask,
            )
    if PairSupportMaskCache is not None:
        PairSupportMaskCache[PairSupportCacheKey] = {
            "BinaryClauses": BinaryClauses,
            "RelevantSignalPairs": RelevantSignalPairs,
            "PairSupportMasks": dict(PairSupportMasks),
        }
    ExpansionCount = 0
    PropagationCount = 0
    PrunedOptionCount = 0
    VisitedStates: set[tuple[tuple[str, str], ...]] = set()
    Incomplete = False
    def OptionsCompatible(
        First: ComponentCapacityGuideOption,
        Second: ComponentCapacityGuideOption,
    ) -> bool:
        """Check guide-stage clauses and coarse capacity only.

        Exact exterior claims are intentionally absent here.  The selected
        virtual-terminal contract is routed exactly by the following exterior
        stage, which owns any complete failure clause.
        """
        if ViolatesClause(First.ContractKeys | Second.ContractKeys):
            return False
        Shared = First.CoarseCells & Second.CoarseCells
        return all(
            int(BaseUsage.get(Cell, 0)) + 2 <= max(
                Guide.CorridorCapacity,
                int(BaseUsage.get(Cell, 0)),
            )
            for Cell in Shared
        )

    def Advance(Stage: str) -> bool:
        nonlocal ExpansionCount, Incomplete
        ExpansionCount += 1
        if WorkCheck is not None and (
            ExpansionCount == 1 or ExpansionCount % 64 == 0
        ):
            WorkCheck({
                "Stage": Stage,
                "ExpansionCount": ExpansionCount,
                "PropagationCount": PropagationCount,
                "PrunedOptionCount": PrunedOptionCount,
                "VisitedStateCount": len(VisitedStates),
                "ImplicitForeignTransitDomainCount": 0,
            })
        if (
            MaximumExpansions is not None
            and ExpansionCount > MaximumExpansions
        ):
            Incomplete = True
            return False
        return True

    def CapacityAllows(
        Option: ComponentCapacityGuideOption,
        Usage: Mapping[CoarseResource, int],
    ) -> bool:
        return all(
            int(Usage.get(Cell, 0)) + 1 <= max(
                Guide.CorridorCapacity,
                int(BaseUsage.get(Cell, 0)),
            )
            for Cell in Option.CoarseCells
        )

    def Propagate(
        Remaining: tuple[str, ...],
        Selected: tuple[ComponentCapacityGuideOption, ...],
        SelectedKeys: frozenset[ContractKey],
        Usage: Mapping[CoarseResource, int],
    ) -> dict[str, tuple[ComponentCapacityGuideOption, ...]] | None:
        nonlocal PropagationCount, PrunedOptionCount
        PropagationCount += 1
        Mutable = {}
        for Signal in Remaining:
            RetainedOptions = []
            for Option in Domains[Signal]:
                PollWork("component-interface-domain-propagation")
                if (
                    CapacityAllows(Option, Usage)
                    and not ViolatesClause(
                        SelectedKeys | Option.ContractKeys
                    )
                    and all(
                        OptionsCompatible(Option, Existing)
                        for Existing in Selected
                    )
                ):
                    RetainedOptions.append(Option)
            Mutable[Signal] = OrderOptions(
                Signal,
                RetainedOptions,
            )
        if any(not Values for Values in Mutable.values()):
            return None
        Changed = True
        while Changed:
            Changed = False
            ActiveMasks = {
                Signal: sum(
                    1 << DomainOptionIndexes[Signal][
                        Option.OptionFingerprint
                    ]
                    for Option in Values
                )
                for Signal, Values in Mutable.items()
            }
            for Signal in Remaining:
                Retained = []
                for Option in Mutable[Signal]:
                    PollWork("component-interface-arc-consistency")
                    OptionIndex = DomainOptionIndexes[Signal][
                        Option.OptionFingerprint
                    ]
                    Supported = all(
                        OtherSignal == Signal
                        or tuple(sorted((Signal, OtherSignal)))
                        not in RelevantSignalPairs
                        or bool(
                            PairSupportMasks.get(
                                (Signal, OptionIndex, OtherSignal),
                                0,
                            )
                            & ActiveMasks[OtherSignal]
                        )
                        for OtherSignal in Remaining
                    )
                    if Supported:
                        Retained.append(Option)
                if len(Retained) != len(Mutable[Signal]):
                    PrunedOptionCount += len(Mutable[Signal]) - len(Retained)
                    Mutable[Signal] = Retained
                    Changed = True
                    if not Retained:
                        return None
        return {
            Signal: tuple(Values)
            for Signal, Values in Mutable.items()
        }

    def Search(
        Remaining: tuple[str, ...],
        Selected: tuple[ComponentCapacityGuideOption, ...],
        SelectedKeys: frozenset[ContractKey],
        Usage: dict[CoarseResource, int],
    ) -> tuple[ComponentCapacityGuideOption, ...] | None:
        if not Advance("component-interface-csp"):
            return None
        State = tuple(sorted(
            (Value.Signal, Value.OptionFingerprint)
            for Value in Selected
        ))
        if State in VisitedStates:
            return None
        VisitedStates.add(State)
        if not Remaining:
            AssignmentFingerprint = _Fingerprint((
                "component-interface-assignment-v1",
                Guide.GuideFingerprint,
                State,
            ))
            BoundaryAssignmentFingerprint = _Fingerprint((
                "physical-component-boundary-assignment-v1",
                tuple(sorted(
                    (
                        str(Value.BoundaryPort.Signal),
                        str(Value.BoundaryPort.Direction),
                        int(Value.BoundaryPort.Capacity),
                        tuple(Value.BoundaryPort.Attachment),
                        tuple(tuple(Position) for Position in (
                            Value.BoundaryPort.GlobalPath
                        )),
                        tuple(sorted(map(str, getattr(
                            Value.BoundaryPort.GlobalClaims,
                            "ResourceIds",
                            (),
                        )))),
                        str(Value.BoundaryPort.ChannelContractFingerprint),
                        str(Value.BoundaryPort.GlobalContractFingerprint),
                        str(Value.BoundaryPort.ApertureContractFingerprint),
                        str(Value.BoundaryPort.ReservationFingerprint),
                    )
                    for Value in Selected
                )),
            ))
            PhysicalPortAssignmentFingerprint = (
                _Fingerprint(tuple(
                    (
                        Value.Signal,
                        Value.PortReservationFingerprint,
                    )
                    for Value in sorted(
                        Selected,
                        key=lambda Candidate: Candidate.Signal,
                    )
                ))
                if all(
                    Value.PortReservationFingerprint
                    for Value in Selected
                )
                else ""
            )
            return (
                None
                if (
                    AssignmentFingerprint in RejectedAssignments
                    or BoundaryAssignmentFingerprint in RejectedAssignments
                    or PhysicalPortAssignmentFingerprint
                    in RejectedAssignments
                )
                else Selected
            )
        Propagated = Propagate(
            Remaining,
            Selected,
            SelectedKeys,
            Usage,
        )
        if Propagated is None:
            return None
        Signal = min(
            Remaining,
            key=lambda Value: (
                len(Propagated[Value]),
                -ClauseDegreeBySignal[Value],
                Value,
            ),
        )
        NextRemaining = tuple(
            Value for Value in Remaining if Value != Signal
        )
        for Option in Propagated[Signal]:
            NextUsage = dict(Usage)
            for Cell in Option.CoarseCells:
                NextUsage[Cell] = int(NextUsage.get(Cell, 0)) + 1
            Result = Search(
                NextRemaining,
                (*Selected, Option),
                SelectedKeys | Option.ContractKeys,
                NextUsage,
            )
            if Result is not None:
                return Result
            if Incomplete:
                return None
        return None

    NativeLeaseResult = None
    NativeSelected: tuple[ComponentCapacityGuideOption, ...] | None = None
    # Python is an explicit fixture oracle only.  Production is native-only.
    if (
        _SolveLeaseDomainsBounded is not None
        and not PythonFixtureOracle
    ):
        ClaimCells = tuple(sorted({
            Cell for Cell in BaseUsage
        } | {
            Cell for Options in Domains.values() for Option in Options
            for Cell in Option.CoarseCells
        }))
        ClaimIndexes = {
            Cell: Index for Index, Cell in enumerate(ClaimCells)
        }
        ClaimSetCapacities = tuple(
            max(
                0,
                max(Guide.CorridorCapacity, int(BaseUsage.get(Cell, 0)))
                - int(BaseUsage.get(Cell, 0)),
            )
            for Cell in ClaimCells
        )
        LeaseDomains = tuple(
            (
                Signal,
                tuple(
                    (
                        Option.OptionFingerprint,
                        Order,
                        tuple(sorted(
                            (*(
                                Fingerprint
                                for _ContractSignal, Fingerprint
                                in Option.ContractKeys
                            ), _LeaseOptionKey(Option.OptionFingerprint))
                        )),
                        tuple(sorted(
                            ClaimIndexes[Cell]
                            for Cell in Option.CoarseCells
                        )),
                    )
                    for Order, Option in enumerate(OrderOptions(Signal, Options))
                ),
            )
            for Signal, Options in sorted(Domains.items())
        )
        RejectedClaimSets = tuple(
            tuple(sorted(Clause)) for Clause in Clauses
        )
        NativeLeaseResult = _SolveLeaseDomainsBounded(
            LeaseDomains,
            ClaimSetCapacities,
            RejectedClaimSets,
            max(0, MaximumExpansions if MaximumExpansions is not None else 1_000_000),
            MaximumRuntimeSeconds,
        )
        NativeStatus, NativeIds, NativeExpansionCount, NativeDeadlineExceeded, NativeBudgetExhausted = NativeLeaseResult
        if NativeStatus == "Feasible":
            OptionsByIdentity = {
                (Option.Signal, Option.OptionFingerprint): Option
                for Options in Domains.values() for Option in Options
            }
            NativeSelected = tuple(
                OptionsByIdentity[(str(Signal), str(Fingerprint))]
                for Signal, Fingerprint in NativeIds
            )
        elif NativeStatus == "Incomplete":
            Incomplete = True
            ExpansionCount = int(NativeExpansionCount)
        else:
            ExpansionCount = int(NativeExpansionCount)

    Selected = NativeSelected if NativeLeaseResult is not None else Search(
        tuple(sorted(Domains)),
        (),
        frozenset(),
        dict(BaseUsage),
    )
    CommonDiagnostics = {
        "ExpansionCount": ExpansionCount,
        "PropagationCount": PropagationCount,
        "PrunedOptionCount": (
            max(
                PrunedOptionCount,
                sum(
                    sum(
                        1
                        for Option in Domains.get(Signal, ())
                        if (Signal, Fingerprint) in Option.ContractKeys
                    )
                    for Signal, Fingerprint in UnaryRejectedKeys
                ),
            )
            if NativeLeaseResult is not None else PrunedOptionCount
        ),
        "VisitedStateCount": len(VisitedStates),
        "LearnedClauseCount": len(Clauses),
        "UnaryLearnedClauseCount": len(UnaryRejectedKeys),
        "BinaryLearnedClauseCount": sum(
            len(Values) for Values in BinaryRejectedPartners.values()
        ) // 2,
        "HigherOrderLearnedClauseCount": sum(
            len(Values)
            for Values in HigherOrderClausesByPivotKey.values()
        ),
        "ClauseIndexLookupCount": ClauseIndexLookupCount,
        "HigherOrderClauseSubsetCheckCount": (
            max(
                HigherOrderClauseSubsetCheckCount,
                sum(1 for Clause in Clauses if len(Clause) > 2),
            ) if NativeLeaseResult is not None
            else HigherOrderClauseSubsetCheckCount
        ),
        "NeverRevisitedRejectedPartialAssignment": True,
        "CoarseOverflowRequired": 0,
        "RelevantSignalPairCount": len(RelevantSignalPairs),
        "PairSupportMaskCacheHit": PairSupportMaskCacheHit,
        "WorkPollCount": WorkPollCount,
        "NativeLeaseSolver": NativeLeaseResult is not None,
        "NativeLeaseDeadlineExceeded": bool(
            NativeLeaseResult[3] if NativeLeaseResult is not None else False
        ),
        "NativeLeaseBudgetExhausted": bool(
            NativeLeaseResult[4] if NativeLeaseResult is not None else False
        ),
    }
    if Selected is None:
        return ComponentPlanningResult(
            Status=(
                ComponentPlanningStatus.SearchIncomplete
                if Incomplete
                else ComponentPlanningStatus.InterfaceUnsatisfiable
            ),
            Guide=Guide,
            Contract=None,
            PlacementProofComplete=False,
            InterfaceProofComplete=not Incomplete,
            GlobalPlanningEntered=True,
            LocalCompilationEntered=False,
            Detail=(
                "component interface CSP reached its work limit"
                if Incomplete
                else "the complete component interface domain is unsatisfiable"
            ),
            Diagnostics=CommonDiagnostics,
        )

    Selected = tuple(sorted(Selected, key=lambda Value: Value.Signal))
    Usage = dict(BaseUsage)
    for Option in Selected:
        for Cell in Option.CoarseCells:
            Usage[Cell] = int(Usage.get(Cell, 0)) + 1
    Overflow = tuple(sorted(
        (
            Cell,
            int(Value)
            - Guide.CorridorCapacity
            - max(
                0,
                int(BaseUsage.get(Cell, 0)) - Guide.CorridorCapacity,
            ),
        )
        for Cell, Value in Usage.items()
        if (
            int(Value) - Guide.CorridorCapacity
            > max(
                0,
                int(BaseUsage.get(Cell, 0)) - Guide.CorridorCapacity,
            )
        )
    ))
    if Overflow:
        raise ValueError("component interface CSP returned coarse overflow")
    SelectedIdentities = tuple(
        (Value.Signal, Value.OptionFingerprint) for Value in Selected
    )
    AssignmentFingerprint = _Fingerprint((
        "component-interface-assignment-v1",
        Guide.GuideFingerprint,
        SelectedIdentities,
    ))
    ProofFingerprint = _Fingerprint((
        "component-interface-proof-v1",
        AssignmentFingerprint,
        tuple(sorted(Usage.items())),
        tuple(tuple(sorted(Value)) for Value in Clauses),
    ))
    Contract = ComponentInterfaceContract(
        AssignmentFingerprint=AssignmentFingerprint,
        GuideFingerprint=Guide.GuideFingerprint,
        PlacementFingerprint=Guide.PlacementFingerprint,
        SelectedOptionFingerprints=SelectedIdentities,
        SelectedBoundaryPorts=tuple(
            Value.BoundaryPort for Value in Selected
        ),
        SelectedLocalAccessFingerprints=tuple(
            (Value.Signal, Value.LocalAccessFingerprint)
            for Value in Selected
            if Value.LocalAccessFingerprint
        ),
        SelectedSeamContractFingerprints=tuple(
            (Value.Signal, Value.SeamContractFingerprint)
            for Value in Selected
            if Value.SeamContractFingerprint
        ),
        SelectedLocalSupportFingerprints=tuple(
            (Value.Signal, Value.LocalSupportFingerprint)
            for Value in Selected
            if Value.LocalSupportFingerprint
        ),
        CoarseUsage=tuple(sorted(Usage.items())),
        Overflow=Overflow,
        Cost=sum(Value.Cost for Value in Selected),
        ProofFingerprint=ProofFingerprint,
    )
    return ComponentPlanningResult(
        Status=ComponentPlanningStatus.Feasible,
        Guide=Guide,
        Contract=Contract,
        PlacementProofComplete=False,
        InterfaceProofComplete=True,
        GlobalPlanningEntered=True,
        LocalCompilationEntered=False,
        Detail="selected a zero-overflow coarse component interface contract",
        Diagnostics=CommonDiagnostics,
    )


def PlanClosedComponent(
    Preparation: PreparedPhysicalComponentPortFactorDomain,
    *,
    TrackPitch: int,
    RejectedClauses: Iterable[NoGoodClause] = (),
    RejectedAssignmentFingerprints: Iterable[str] = (),
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    MaximumExpansions: int | None = None,
    MaximumRuntimeSeconds: float | None = None,
) -> ComponentPlanningResult:
    """Build the all-net coarse guide and solve one closed interface domain."""
    Problem = Preparation.Problem
    if Problem.Interface is None or not Problem.Interface.Complete:
        return ComponentPlanningResult(
            Status=ComponentPlanningStatus.SearchIncomplete,
            Guide=None,
            Contract=None,
            PlacementProofComplete=False,
            InterfaceProofComplete=False,
            GlobalPlanningEntered=False,
            LocalCompilationEntered=False,
            Detail="component placement does not expose a complete interface",
            Diagnostics={"ComponentInterfaceComplete": False},
        )
    if Problem.Fabric.TopologyKind not in {
        "tree",
        "tree-forest",
        "closed-component-port-forest-v3",
        "closed-component-bridged-forest-v1",
    }:
        return ComponentPlanningResult(
            Status=ComponentPlanningStatus.SearchIncomplete,
            Guide=None,
            Contract=None,
            PlacementProofComplete=False,
            InterfaceProofComplete=False,
            GlobalPlanningEntered=False,
            LocalCompilationEntered=False,
            Detail="component optimization requires a complete tree/forest fabric",
            Diagnostics={
                "FabricTopologyKind": Problem.Fabric.TopologyKind,
                "OrdinaryGlobalRoutingAllowed": True,
            },
        )
    Guide = BuildComponentCapacityGuide(
        Preparation,
        TrackPitch=TrackPitch,
    )
    return SolveComponentInterfaceCsp(
        Guide,
        RejectedClauses=RejectedClauses,
        RejectedAssignmentFingerprints=RejectedAssignmentFingerprints,
        WorkCheck=WorkCheck,
        MaximumExpansions=MaximumExpansions,
        MaximumRuntimeSeconds=MaximumRuntimeSeconds,
    )


def IterClosedComponentContracts(
    Preparation: PreparedPhysicalComponentPortFactorDomain,
    *,
    TrackPitch: int,
    RejectedClauses: Iterable[NoGoodClause] = (),
    RejectedApertureContractFingerprintsBySignal: Mapping[
        str, Iterable[str]
    ] | None = None,
    RejectedAssignmentFingerprints: Iterable[str] = (),
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    IncludeLocalCompositeFactors: bool = True,
    PreferredGlobalContractsBySignal: Mapping[str, str] | None = None,
    PreferredApertureContractsBySignal: Mapping[str, str] | None = None,
    PreferredPortReservationsBySignal: Mapping[str, str] | None = None,
    AperturePortalSlackBySignal: Mapping[
        str, Mapping[str, tuple[int, int]]
    ] | None = None,
    MaximumRuntimeSeconds: float | Callable[[], float] | None = None,
) -> Iterable[ComponentInterfaceContract]:
    """Yield monotonic zero-overflow contracts for one frozen placement.

    Resuming the iterator means the previously yielded contract failed an
    exact downstream check.  Its planner identity is retained locally while
    live exact clauses and assignment rejections are read again on every
    solve.  This replaces product enumeration with repeated deterministic CSP
    solves without recursive same-placement replanning.
    """
    Guide = BuildComponentCapacityGuide(
        Preparation,
        TrackPitch=TrackPitch,
        IncludeLocalCompositeFactors=IncludeLocalCompositeFactors,
    )
    RejectedAperturesBySignal = (
        RejectedApertureContractFingerprintsBySignal
        if RejectedApertureContractFingerprintsBySignal is not None
        else {}
    )
    LocallyRejectedAssignments: set[str] = set()
    LocallyRejectedLeaseSets: set[NoGoodClause] = set()
    PairSupportMaskCache: dict[str, Any] = {}
    while True:
        LiveRejectedClauses = tuple((
            *RejectedClauses,
            *(
                frozenset(((str(Signal), str(Fingerprint)),))
                for Signal, Fingerprints
                in RejectedAperturesBySignal.items()
                for Fingerprint in Fingerprints
            ),
            *LocallyRejectedLeaseSets,
        ))
        PreferredApertures = (
            PreferredApertureContractsBySignal
            if PreferredApertureContractsBySignal is not None
            else {}
        )
        RestrictedPreferenceClauses = tuple(
            frozenset(((
                Signal,
                str(Option.BoundaryPort.ApertureContractFingerprint),
            ),))
            for Signal, PreferredFingerprint
            in sorted(PreferredApertures.items())
            for Option in Guide.Domains().get(Signal, ())
            if str(Option.BoundaryPort.ApertureContractFingerprint)
            != str(PreferredFingerprint)
        )

        def Solve(PreferenceClauses: Iterable[NoGoodClause]):
            RuntimeSeconds = (
                MaximumRuntimeSeconds()
                if callable(MaximumRuntimeSeconds)
                else MaximumRuntimeSeconds
            )
            return SolveComponentInterfaceCsp(
                Guide,
                RejectedClauses=(
                    *LiveRejectedClauses,
                    *PreferenceClauses,
                ),
            RejectedAssignmentFingerprints=(
                *RejectedAssignmentFingerprints,
                *(
                    LocallyRejectedAssignments
                    if _PythonFixtureOracleEnabled()
                    else ()
                ),
            ),
            WorkCheck=WorkCheck,
            PairSupportMaskCache=PairSupportMaskCache,
            PreferredGlobalContractsBySignal=(
                PreferredGlobalContractsBySignal
            ),
            PreferredApertureContractsBySignal=(
                PreferredApertureContractsBySignal
            ),
            PreferredPortReservationsBySignal=(
                PreferredPortReservationsBySignal
            ),
            AperturePortalSlackBySignal=AperturePortalSlackBySignal,
            MaximumRuntimeSeconds=RuntimeSeconds,
            )

        Result = Solve(RestrictedPreferenceClauses)
        if (
            RestrictedPreferenceClauses
            and Result.Status
            == ComponentPlanningStatus.InterfaceUnsatisfiable
        ):
            Result = Solve(())
        if not Result.Feasible or Result.Contract is None:
            return
        Contract = Result.Contract
        yield Contract
        LocallyRejectedAssignments.add(Contract.AssignmentFingerprint)
        LocallyRejectedLeaseSets.add(frozenset(
            (Signal, _LeaseOptionKey(OptionFingerprint))
            for Signal, OptionFingerprint
            in Contract.SelectedOptionFingerprints
        ))
