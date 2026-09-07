"""Standalone exact capacity oracle for an immutable placement access fabric."""

from __future__ import annotations

from collections import (
    deque,
)
from dataclasses import (
    dataclass,
)
from enum import Enum
from hashlib import (
    sha256,
)
from typing import (
    Any,
    Callable,
    Iterable,
    TypeAlias,
)
from PhysicalDesign.Contracts.Placement import PlacementAccessAssignment, PlacementAccessFabric
from PhysicalDesign.Contracts.PlacementAccess import (
    PlacedPinAccessOption,
    PlacedPinAccessOptionDomain,
    PlacementAccessConflictCore,
    PlacementAccessSolveResult,
    PlacementAccessSolveStatus,
)
from PhysicalDesign.Contracts.Core import Position3
from PhysicalDesign.Resources.ResourceGraph import FindClaimConflicts, FindSelfClaimConflicts, RoutingResourceClaims, RoutingResourceGraph
from PhysicalDesign.Runtime.Reliability import BuildStableFingerprint
from PhysicalDesign.Redstone.Technology import DefaultRedstoneRoutingTechnology
from .EscapePaths import (
    _BuildDerivedPerimeterCycleRouteNodeSets,
)
from .Catalog import FreezeSelectedPlacementPinAccessWitness
from PhysicalDesign.Geometry.Placement import PlacementPinAccessSelection


FixedPlacementPinAccessOption: TypeAlias = (
    PlacementPinAccessSelection | PlacedPinAccessOption
)


def _MergePlacementAccessClaims(
    First: RoutingResourceClaims,
    Second: RoutingResourceClaims,
) -> RoutingResourceClaims:
    return RoutingResourceClaims(
        WireCells=First.WireCells | Second.WireCells,
        SupportCells=First.SupportCells | Second.SupportCells,
        RequiredAirCells=(
            First.RequiredAirCells | Second.RequiredAirCells
        ),
        ElectricalCells=First.ElectricalCells | Second.ElectricalCells,
    )

def _PlacementAccessClaimsConflict(
    First: RoutingResourceClaims,
    Second: RoutingResourceClaims,
) -> bool:
    return bool(
        (First.WireCells & Second.ElectricalCells)
        or (Second.WireCells & First.ElectricalCells)
        or (
            First.SupportCells
            & (Second.WireCells | Second.RequiredAirCells)
        )
        or (
            Second.SupportCells
            & (First.WireCells | First.RequiredAirCells)
        )
        or (First.RequiredAirCells & Second.WireCells)
        or (Second.RequiredAirCells & First.WireCells)
    )


class FixedPlacementPinAccessStatus(str, Enum):
    """Terminal result of one complete-or-bounded fixed-placement solve."""

    Feasible = "Feasible"
    Unsatisfiable = "Unsatisfiable"
    Incomplete = "Incomplete"


@dataclass(frozen=True)
class FixedPlacementPinAccessDomain:
    """Finite pattern domain for one logical terminal on a fixed placement."""

    DomainId: str
    Signal: str
    Terminal: Position3
    Options: tuple[FixedPlacementPinAccessOption, ...]
    Complete: bool = True
    IncompleteReason: str = ""
    SourceDomainFingerprint: str = ""
    CatalogVersion: str = ""
    TechnologyFingerprint: str = ""
    ResourceModelFingerprint: str = ""

    def __post_init__(self) -> None:
        if not self.DomainId or not self.Signal:
            raise ValueError("fixed pin-access domain requires identities")
        if self.Complete == bool(self.IncompleteReason):
            raise ValueError("fixed pin-access domain completeness disagrees")
        OptionFingerprints = tuple(
            Value.SelectionFingerprint for Value in self.Options
        )
        if len(OptionFingerprints) != len(set(OptionFingerprints)):
            raise ValueError("fixed pin-access domain repeats an option")
        PlacedOptions = tuple(
            Value
            for Value in self.Options
            if isinstance(Value, PlacedPinAccessOption)
        )
        if PlacedOptions and len(PlacedOptions) != len(self.Options):
            raise ValueError(
                "fixed pin-access domain cannot mix legacy and exact options"
            )
        for Option in self.Options:
            if Option.Signal != self.Signal or Option.Terminal != self.Terminal:
                raise ValueError(
                    "fixed pin-access option does not belong to its domain"
                )
        if PlacedOptions:
            ExpectedDependencies = (
                PlacedOptions[0].CatalogVersion,
                PlacedOptions[0].TechnologyFingerprint,
                PlacedOptions[0].ResourceModelFingerprint,
            )
            if any(
                (
                    Value.CatalogVersion,
                    Value.TechnologyFingerprint,
                    Value.ResourceModelFingerprint,
                )
                != ExpectedDependencies
                for Value in PlacedOptions
            ):
                raise ValueError(
                    "fixed pin-access exact options use mixed dependencies"
                )
            DeclaredDependencies = (
                self.CatalogVersion,
                self.TechnologyFingerprint,
                self.ResourceModelFingerprint,
            )
            if any(DeclaredDependencies) and (
                DeclaredDependencies != ExpectedDependencies
            ):
                raise ValueError(
                    "fixed pin-access domain dependency identity mismatch"
                )
        else:
            DeclaredExactIdentity = (
                self.SourceDomainFingerprint,
                self.CatalogVersion,
                self.TechnologyFingerprint,
                self.ResourceModelFingerprint,
            )
            if any(DeclaredExactIdentity) and not all(DeclaredExactIdentity):
                raise ValueError(
                    "an empty exact pin-access domain requires all identities"
                )

    @property
    def UsesExactOptions(self) -> bool:
        return bool(
            self.SourceDomainFingerprint
            or self.CatalogVersion
            or self.TechnologyFingerprint
            or self.ResourceModelFingerprint
            or (
                self.Options
                and isinstance(self.Options[0], PlacedPinAccessOption)
            )
        )

    @property
    def CanonicalOptions(self) -> tuple[FixedPlacementPinAccessOption, ...]:
        return (
            tuple(sorted(self.Options, key=_FixedPinAccessOptionRank))
            if self.UsesExactOptions
            else self.Options
        )

    @property
    def EffectiveCatalogVersion(self) -> str:
        return (
            self.CatalogVersion
            or (
                self.Options[0].CatalogVersion
                if self.UsesExactOptions
                else ""
            )
        )

    @property
    def EffectiveTechnologyFingerprint(self) -> str:
        return (
            self.TechnologyFingerprint
            or (
                self.Options[0].TechnologyFingerprint
                if self.UsesExactOptions
                else ""
            )
        )

    @property
    def EffectiveResourceModelFingerprint(self) -> str:
        return (
            self.ResourceModelFingerprint
            or (
                self.Options[0].ResourceModelFingerprint
                if self.UsesExactOptions
                else ""
            )
        )

    @property
    def DomainFingerprint(self) -> str:
        Identity = {
            "Kind": "fixed-placement-pin-access-domain-v1",
            "DomainId": self.DomainId,
            "Signal": self.Signal,
            "Terminal": self.Terminal,
            "Options": [
                Value.ToIdentityDictionary() if isinstance(Value, PlacedPinAccessOption) else Value.ToDictionary() for Value in self.CanonicalOptions
            ],
            "Complete": self.Complete,
            "IncompleteReason": self.IncompleteReason,
        }
        if self.UsesExactOptions:
            Identity.update({
                "SourceDomainFingerprint": self.SourceDomainFingerprint,
                "CatalogVersion": self.EffectiveCatalogVersion,
                "TechnologyFingerprint": (
                    self.EffectiveTechnologyFingerprint
                ),
                "ResourceModelFingerprint": (
                    self.EffectiveResourceModelFingerprint
                ),
            })
        return BuildStableFingerprint(Identity)

    def ToDictionary(self) -> dict[str, object]:
        Result = {
            "DomainId": self.DomainId,
            "DomainFingerprint": self.DomainFingerprint,
            "Signal": self.Signal,
            "Terminal": list(self.Terminal),
            "OptionCount": len(self.Options),
            "Options": [
                Value.ToIdentityDictionary() if isinstance(Value, PlacedPinAccessOption) else Value.ToDictionary() for Value in self.CanonicalOptions
            ],
            "Complete": self.Complete,
            "IncompleteReason": self.IncompleteReason,
        }
        if self.UsesExactOptions:
            Result.update({
                "SourceDomainFingerprint": self.SourceDomainFingerprint,
                "CatalogVersion": self.EffectiveCatalogVersion,
                "TechnologyFingerprint": (
                    self.EffectiveTechnologyFingerprint
                ),
                "ResourceModelFingerprint": (
                    self.EffectiveResourceModelFingerprint
                ),
            })
        return Result


@dataclass(frozen=True)
class FixedPlacementPinAccessConflict:
    """One exact incompatible option pair inside a replayable core."""

    FirstDomainId: str
    FirstOptionFingerprint: str
    SecondDomainId: str
    SecondOptionFingerprint: str
    ResourceIds: tuple[str, ...]

    def ToDictionary(self) -> dict[str, object]:
        return {
            "FirstDomainId": self.FirstDomainId,
            "FirstOptionFingerprint": self.FirstOptionFingerprint,
            "SecondDomainId": self.SecondDomainId,
            "SecondOptionFingerprint": self.SecondOptionFingerprint,
            "ResourceIds": list(self.ResourceIds),
        }


@dataclass(frozen=True)
class FixedPlacementPinAccessUnsatisfiableCore:
    """Complete independent conflict component that can be replayed alone."""

    CoreFingerprint: str
    ProblemFingerprint: str
    Domains: tuple[FixedPlacementPinAccessDomain, ...]
    Conflicts: tuple[FixedPlacementPinAccessConflict, ...]

    @property
    def Signals(self) -> tuple[str, ...]:
        return tuple(sorted({Value.Signal for Value in self.Domains}))

    def ToDictionary(self) -> dict[str, object]:
        return {
            "CoreFingerprint": self.CoreFingerprint,
            "ProblemFingerprint": self.ProblemFingerprint,
            "Signals": list(self.Signals),
            "Domains": [Value.ToDictionary() for Value in self.Domains],
            "Conflicts": [Value.ToDictionary() for Value in self.Conflicts],
            "Complete": True,
        }


@dataclass(frozen=True)
class FixedPlacementPinAccessSolveResult:
    """Typed exact result for one fixed finite cell-pattern problem."""

    Status: FixedPlacementPinAccessStatus
    ProblemFingerprint: str
    AssignmentFingerprint: str
    SelectedOptionFingerprints: tuple[tuple[str, str], ...]
    ExpansionCount: int
    MaximumExpansions: int
    UnsatisfiableCore: FixedPlacementPinAccessUnsatisfiableCore | None = None
    IncompleteReason: str = ""

    def __post_init__(self) -> None:
        if self.ExpansionCount < 0 or self.MaximumExpansions < 1:
            raise ValueError("fixed pin-access solve work values are invalid")
        if self.Status is FixedPlacementPinAccessStatus.Feasible:
            if not self.AssignmentFingerprint or self.UnsatisfiableCore is not None:
                raise ValueError("feasible pin-access result is malformed")
        elif self.Status is FixedPlacementPinAccessStatus.Unsatisfiable:
            if self.UnsatisfiableCore is None or self.IncompleteReason:
                raise ValueError("unsatisfiable pin-access result is malformed")
        elif not self.IncompleteReason or self.UnsatisfiableCore is not None:
            raise ValueError("incomplete pin-access result is malformed")

    @property
    def Complete(self) -> bool:
        return self.Status is not FixedPlacementPinAccessStatus.Incomplete

    @property
    def Success(self) -> bool:
        return self.Status is FixedPlacementPinAccessStatus.Feasible

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Status": self.Status.value,
            "ProblemFingerprint": self.ProblemFingerprint,
            "AssignmentFingerprint": self.AssignmentFingerprint,
            "SelectedOptionFingerprints": [
                list(Value) for Value in self.SelectedOptionFingerprints
            ],
            "ExpansionCount": self.ExpansionCount,
            "MaximumExpansions": self.MaximumExpansions,
            "Success": self.Success,
            "Complete": self.Complete,
            "UnsatisfiableCore": (
                self.UnsatisfiableCore.ToDictionary()
                if self.UnsatisfiableCore is not None
                else None
            ),
            "IncompleteReason": self.IncompleteReason,
        }


def _BuildFixedPinAccessOptionClaims(
    Domains: tuple[FixedPlacementPinAccessDomain, ...],
    ResourceGraph: RoutingResourceGraph,
) -> dict[tuple[str, str], RoutingResourceClaims]:
    return {
        (Domain.DomainId, Option.SelectionFingerprint): (
            Option.Claims
            if isinstance(Option, PlacedPinAccessOption)
            else ResourceGraph.BuildRouteClaims(Option.Path)
        )
        for Domain in Domains
        for Option in Domain.Options
    }


def _FixedPinAccessOptionRank(
    Option: FixedPlacementPinAccessOption,
) -> tuple[object, ...]:
    if isinstance(Option, PlacedPinAccessOption):
        return Option.RankKey()
    return (0, 0, len(Option.Path), Option.SelectionFingerprint)


def _FixedPinAccessPhysicalRoleConflictResources(
    FirstOption: FixedPlacementPinAccessOption,
    SecondOption: FixedPlacementPinAccessOption,
) -> tuple[str, ...]:
    """Return exact block-state conflicts omitted by resource claims."""
    if not isinstance(FirstOption, PlacedPinAccessOption) or not isinstance(
        SecondOption,
        PlacedPinAccessOption,
    ):
        return ()
    Conflicts = set()
    FirstRoles = dict(FirstOption.BlockRoles)
    SecondRoles = dict(SecondOption.BlockRoles)
    for Position in sorted(set(FirstRoles) & set(SecondRoles)):
        FirstRole = FirstRoles[Position]
        SecondRole = SecondRoles[Position]
        if FirstRole == SecondRole:
            continue
        Roles = tuple(sorted((FirstRole, SecondRole)))
        Conflicts.add(
            "BlockRole:"
            f"{Position[0]},{Position[1]},{Position[2]}:"
            f"{Roles[0]}!={Roles[1]}"
        )
    FirstRepeaters = {
        Value.Position: str(Value.InputFacing)
        for Value in FirstOption.RepeaterReservations
    }
    SecondRepeaters = {
        Value.Position: str(Value.InputFacing)
        for Value in SecondOption.RepeaterReservations
    }
    for Position in sorted(set(FirstRepeaters) & set(SecondRepeaters)):
        FirstFacing = FirstRepeaters[Position]
        SecondFacing = SecondRepeaters[Position]
        if FirstFacing == SecondFacing:
            continue
        Facings = tuple(sorted((FirstFacing, SecondFacing)))
        Conflicts.add(
            "RepeaterFacing:"
            f"{Position[0]},{Position[1]},{Position[2]}:"
            f"{Facings[0]}!={Facings[1]}"
        )
    return tuple(sorted(Conflicts))


def _FixedPinAccessConflictResources(
    FirstDomain: FixedPlacementPinAccessDomain,
    FirstOption: FixedPlacementPinAccessOption,
    SecondDomain: FixedPlacementPinAccessDomain,
    SecondOption: FixedPlacementPinAccessOption,
    ClaimsByOption: dict[tuple[str, str], RoutingResourceClaims],
) -> tuple[str, ...]:
    FirstClaims = ClaimsByOption[
        (FirstDomain.DomainId, FirstOption.SelectionFingerprint)
    ]
    SecondClaims = ClaimsByOption[
        (SecondDomain.DomainId, SecondOption.SelectionFingerprint)
    ]
    if FirstDomain.Signal == SecondDomain.Signal:
        Conflicts = FindSelfClaimConflicts({
            FirstDomain.Signal: _MergePlacementAccessClaims(
                FirstClaims,
                SecondClaims,
            )
        })
    else:
        Conflicts = FindClaimConflicts({
            "First": FirstClaims,
            "Second": SecondClaims,
        })
    return tuple(sorted({
        *map(str, Conflicts),
        *_FixedPinAccessPhysicalRoleConflictResources(
            FirstOption,
            SecondOption,
        ),
    }))


def _BuildFixedPinAccessConflictDomainComponents(
    Domains: tuple[FixedPlacementPinAccessDomain, ...],
    Conflicts: tuple[FixedPlacementPinAccessConflict, ...],
) -> tuple[tuple[str, ...], ...]:
    Adjacency = {Value.DomainId: set() for Value in Domains}
    for Conflict in Conflicts:
        Adjacency[Conflict.FirstDomainId].add(Conflict.SecondDomainId)
        Adjacency[Conflict.SecondDomainId].add(Conflict.FirstDomainId)
    Components = []
    Remaining = set(Adjacency)
    while Remaining:
        Root = min(Remaining)
        Pending = [Root]
        Component = set()
        while Pending:
            DomainId = Pending.pop()
            if DomainId in Component:
                continue
            Component.add(DomainId)
            Pending.extend(sorted(Adjacency[DomainId] - Component))
        Remaining.difference_update(Component)
        Components.append(tuple(sorted(Component)))
    return tuple(sorted(Components))


def _BuildFixedPinAccessProblemFingerprint(
    OrderedDomains: tuple[FixedPlacementPinAccessDomain, ...],
) -> str:
    UsesExactOptions = any(Value.UsesExactOptions for Value in OrderedDomains)
    if UsesExactOptions and any(
        Value.Options and not Value.UsesExactOptions
        for Value in OrderedDomains
    ):
        raise ValueError(
            "fixed pin-access problem cannot mix legacy and exact domains"
        )
    ProblemIdentity = {
        "Kind": (
            "fixed-placement-pin-access-problem-v2"
            if UsesExactOptions
            else "fixed-placement-pin-access-problem-v1"
        ),
        "Domains": [Value.ToDictionary() for Value in OrderedDomains],
    }
    if UsesExactOptions:
        ProblemIdentity.update({
            "CatalogVersions": sorted({
                Value.EffectiveCatalogVersion
                for Value in OrderedDomains
                if Value.UsesExactOptions
            }),
            "TechnologyFingerprints": sorted({
                Value.EffectiveTechnologyFingerprint
                for Value in OrderedDomains
                if Value.UsesExactOptions
            }),
            "ResourceModelFingerprints": sorted({
                Value.EffectiveResourceModelFingerprint
                for Value in OrderedDomains
                if Value.UsesExactOptions
            }),
            "SourceDomainFingerprints": sorted({
                Value.SourceDomainFingerprint or Value.DomainFingerprint
                for Value in OrderedDomains
            }),
        })
    return BuildStableFingerprint(ProblemIdentity)


def SolveFixedPlacementPinAccessDomains(
    Domains: Iterable[
        FixedPlacementPinAccessDomain | PlacedPinAccessOptionDomain
    ],
    *,
    ResourceGraph: RoutingResourceGraph | None = None,
    MaximumExpansions: int = 100_000,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> FixedPlacementPinAccessSolveResult:
    """Solve one immutable option per terminal with exact pair conflicts."""
    if MaximumExpansions < 1:
        raise ValueError("fixed pin-access solve requires a positive work cap")
    ReceivedDomains = tuple(Domains)
    HasPlacedDomains = any(
        isinstance(Value, PlacedPinAccessOptionDomain)
        for Value in ReceivedDomains
    )
    if HasPlacedDomains:
        if not all(
            isinstance(Value, PlacedPinAccessOptionDomain)
            for Value in ReceivedDomains
        ):
            raise ValueError(
                "fixed pin-access solve cannot mix domain contract families"
            )
        _SourceDomains, OrderedDomains = _ConvertPlacedPinAccessDomains(
            ReceivedDomains
        )
    else:
        OrderedDomains = tuple(sorted(
            ReceivedDomains,
            key=lambda Value: Value.DomainId,
        ))
    if len({Value.DomainId for Value in OrderedDomains}) != len(OrderedDomains):
        raise ValueError("fixed pin-access problem repeats a domain id")
    ProblemFingerprint = _BuildFixedPinAccessProblemFingerprint(
        OrderedDomains
    )
    IncompleteDomain = next(
        (Value for Value in OrderedDomains if not Value.Complete),
        None,
    )
    if IncompleteDomain is not None:
        return FixedPlacementPinAccessSolveResult(
            Status=FixedPlacementPinAccessStatus.Incomplete,
            ProblemFingerprint=ProblemFingerprint,
            AssignmentFingerprint="",
            SelectedOptionFingerprints=(),
            ExpansionCount=0,
            MaximumExpansions=MaximumExpansions,
            IncompleteReason=(
                IncompleteDomain.IncompleteReason or "incomplete-domain"
            ),
        )
    ResourceGraph = ResourceGraph or RoutingResourceGraph(
        ActualBlocks=frozenset(),
        ElectricalBlocks=frozenset(),
        SolidBlocks=frozenset(),
    )
    ClaimsByOption = _BuildFixedPinAccessOptionClaims(
        OrderedDomains,
        ResourceGraph,
    )
    ValidOptionsByDomain = {}
    for Domain in OrderedDomains:
        ValidOptions = tuple(
            Option
            for Option in Domain.Options
            if not FindSelfClaimConflicts({
                Domain.Signal: ClaimsByOption[
                    (Domain.DomainId, Option.SelectionFingerprint)
                ]
            })
        )
        ValidOptionsByDomain[Domain.DomainId] = (
            tuple(sorted(ValidOptions, key=_FixedPinAccessOptionRank))
            if Domain.UsesExactOptions
            else ValidOptions
        )
    IsCompleteSingletonProblem = all(
        len(ValidOptionsByDomain[Domain.DomainId]) == 1
        and len(Domain.Options) == 1
        for Domain in OrderedDomains
    )
    if IsCompleteSingletonProblem:
        ClaimsBySignal: dict[str, RoutingResourceClaims] = {}
        for Domain in OrderedDomains:
            Option = ValidOptionsByDomain[Domain.DomainId][0]
            OptionClaims = ClaimsByOption[
                (Domain.DomainId, Option.SelectionFingerprint)
            ]
            ClaimsBySignal[Domain.Signal] = (
                _MergePlacementAccessClaims(
                    ClaimsBySignal[Domain.Signal],
                    OptionClaims,
                )
                if Domain.Signal in ClaimsBySignal
                else OptionClaims
            )
        SelectedSingletons = tuple(
            ValidOptionsByDomain[Domain.DomainId][0]
            for Domain in OrderedDomains
        )
        HasAggregateConflict = bool(
            FindClaimConflicts(ClaimsBySignal)
            or FindSelfClaimConflicts(ClaimsBySignal)
            or any(
                _FixedPinAccessPhysicalRoleConflictResources(
                    First,
                    Second,
                )
                for FirstIndex, First in enumerate(SelectedSingletons)
                for Second in SelectedSingletons[FirstIndex + 1:]
            )
        )
        if not HasAggregateConflict:
            if len(OrderedDomains) > MaximumExpansions:
                return FixedPlacementPinAccessSolveResult(
                    Status=FixedPlacementPinAccessStatus.Incomplete,
                    ProblemFingerprint=ProblemFingerprint,
                    AssignmentFingerprint="",
                    SelectedOptionFingerprints=(),
                    ExpansionCount=MaximumExpansions,
                    MaximumExpansions=MaximumExpansions,
                    IncompleteReason="assignment-work-cap",
                )
            SelectedOptionFingerprints = tuple(
                (
                    Domain.DomainId,
                    ValidOptionsByDomain[
                        Domain.DomainId
                    ][0].SelectionFingerprint,
                )
                for Domain in OrderedDomains
            )
            return FixedPlacementPinAccessSolveResult(
                Status=FixedPlacementPinAccessStatus.Feasible,
                ProblemFingerprint=ProblemFingerprint,
                AssignmentFingerprint=BuildStableFingerprint({
                    "ProblemFingerprint": ProblemFingerprint,
                    "SelectedOptionFingerprints": (
                        SelectedOptionFingerprints
                    ),
                }),
                SelectedOptionFingerprints=SelectedOptionFingerprints,
                ExpansionCount=len(OrderedDomains),
                MaximumExpansions=MaximumExpansions,
            )
    Conflicts = []
    for FirstIndex, FirstDomain in enumerate(OrderedDomains):
        for SecondDomain in OrderedDomains[FirstIndex + 1:]:
            for FirstOption in ValidOptionsByDomain[FirstDomain.DomainId]:
                for SecondOption in ValidOptionsByDomain[SecondDomain.DomainId]:
                    ResourceIds = _FixedPinAccessConflictResources(
                        FirstDomain,
                        FirstOption,
                        SecondDomain,
                        SecondOption,
                        ClaimsByOption,
                    )
                    if ResourceIds:
                        Conflicts.append(FixedPlacementPinAccessConflict(
                            FirstDomainId=FirstDomain.DomainId,
                            FirstOptionFingerprint=(
                                FirstOption.SelectionFingerprint
                            ),
                            SecondDomainId=SecondDomain.DomainId,
                            SecondOptionFingerprint=(
                                SecondOption.SelectionFingerprint
                            ),
                            ResourceIds=ResourceIds,
                        ))
    OrderedConflicts = tuple(sorted(
        Conflicts,
        key=lambda Value: (
            Value.FirstDomainId,
            Value.FirstOptionFingerprint,
            Value.SecondDomainId,
            Value.SecondOptionFingerprint,
            Value.ResourceIds,
        ),
    ))
    ConflictKeys = frozenset(
        (
            Conflict.FirstDomainId,
            Conflict.FirstOptionFingerprint,
            Conflict.SecondDomainId,
            Conflict.SecondOptionFingerprint,
        )
        for Conflict in OrderedConflicts
    )
    DomainById = {Value.DomainId: Value for Value in OrderedDomains}
    ExpansionCount = 0
    SelectedByDomain: dict[str, FixedPlacementPinAccessOption] = {}

    def OptionsConflict(
        FirstDomainId: str,
        FirstOptionFingerprint: str,
        SecondDomainId: str,
        SecondOptionFingerprint: str,
    ) -> bool:
        if FirstDomainId > SecondDomainId:
            FirstDomainId, SecondDomainId = SecondDomainId, FirstDomainId
            FirstOptionFingerprint, SecondOptionFingerprint = (
                SecondOptionFingerprint,
                FirstOptionFingerprint,
            )
        return (
            FirstDomainId,
            FirstOptionFingerprint,
            SecondDomainId,
            SecondOptionFingerprint,
        ) in ConflictKeys

    def Search(ComponentDomainIds: tuple[str, ...]) -> bool | None:
        nonlocal ExpansionCount
        OrderedComponentDomains = tuple(sorted(
            (DomainById[DomainId] for DomainId in ComponentDomainIds),
            key=lambda Value: (
                len(ValidOptionsByDomain[Value.DomainId]),
                Value.DomainId,
            ),
        ))

        def Visit(DomainIndex: int) -> bool | None:
            nonlocal ExpansionCount
            if DomainIndex == len(OrderedComponentDomains):
                return True
            Domain = OrderedComponentDomains[DomainIndex]
            for Option in ValidOptionsByDomain[Domain.DomainId]:
                if ExpansionCount >= MaximumExpansions:
                    return None
                ExpansionCount += 1
                if WorkCheck is not None and ExpansionCount % 256 == 0:
                    WorkCheck({
                        "Phase": "fixed-placement-pin-access-search",
                        "ExpansionCount": ExpansionCount,
                        "MaximumExpansions": MaximumExpansions,
                        "DomainId": Domain.DomainId,
                    })
                if any(
                    OptionsConflict(
                        Domain.DomainId,
                        Option.SelectionFingerprint,
                        SelectedDomainId,
                        SelectedOption.SelectionFingerprint,
                    )
                    for SelectedDomainId, SelectedOption
                    in SelectedByDomain.items()
                ):
                    continue
                SelectedByDomain[Domain.DomainId] = Option
                Result = Visit(DomainIndex + 1)
                if Result is not False:
                    return Result
                SelectedByDomain.pop(Domain.DomainId, None)
            return False

        return Visit(0)

    for ComponentDomainIds in _BuildFixedPinAccessConflictDomainComponents(
        OrderedDomains,
        OrderedConflicts,
    ):
        ComponentResult = Search(ComponentDomainIds)
        if ComponentResult is None:
            return FixedPlacementPinAccessSolveResult(
                Status=FixedPlacementPinAccessStatus.Incomplete,
                ProblemFingerprint=ProblemFingerprint,
                AssignmentFingerprint="",
                SelectedOptionFingerprints=(),
                ExpansionCount=ExpansionCount,
                MaximumExpansions=MaximumExpansions,
                IncompleteReason="assignment-work-cap",
            )
        if ComponentResult:
            continue
        CoreDomains = tuple(
            DomainById[DomainId] for DomainId in ComponentDomainIds
        )
        CoreConflicts = tuple(
            Value
            for Value in OrderedConflicts
            if (
                Value.FirstDomainId in ComponentDomainIds
                and Value.SecondDomainId in ComponentDomainIds
            )
        )
        CoreFingerprint = BuildStableFingerprint({
            "Kind": "fixed-placement-pin-access-unsat-core-v1",
            "Domains": [Value.ToDictionary() for Value in CoreDomains],
            "Conflicts": [Value.ToDictionary() for Value in CoreConflicts],
        })
        return FixedPlacementPinAccessSolveResult(
            Status=FixedPlacementPinAccessStatus.Unsatisfiable,
            ProblemFingerprint=ProblemFingerprint,
            AssignmentFingerprint="",
            SelectedOptionFingerprints=(),
            ExpansionCount=ExpansionCount,
            MaximumExpansions=MaximumExpansions,
            UnsatisfiableCore=FixedPlacementPinAccessUnsatisfiableCore(
                CoreFingerprint=CoreFingerprint,
                ProblemFingerprint=ProblemFingerprint,
                Domains=CoreDomains,
                Conflicts=CoreConflicts,
            ),
        )
    SelectedOptionFingerprints = tuple(sorted(
        (
            DomainId,
            Option.SelectionFingerprint,
        )
        for DomainId, Option in SelectedByDomain.items()
    ))
    return FixedPlacementPinAccessSolveResult(
        Status=FixedPlacementPinAccessStatus.Feasible,
        ProblemFingerprint=ProblemFingerprint,
        AssignmentFingerprint=BuildStableFingerprint({
            "ProblemFingerprint": ProblemFingerprint,
            "SelectedOptionFingerprints": SelectedOptionFingerprints,
        }),
        SelectedOptionFingerprints=SelectedOptionFingerprints,
        ExpansionCount=ExpansionCount,
        MaximumExpansions=MaximumExpansions,
    )


def _ConvertPlacedPinAccessDomains(
    Domains: Iterable[PlacedPinAccessOptionDomain],
) -> tuple[
    tuple[PlacedPinAccessOptionDomain, ...],
    tuple[FixedPlacementPinAccessDomain, ...],
]:
    OrderedDomains = tuple(sorted(Domains, key=lambda Value: Value.DomainId))
    if not OrderedDomains:
        raise ValueError("placed pin-access solve requires terminal domains")
    if len({Value.DomainId for Value in OrderedDomains}) != len(OrderedDomains):
        raise ValueError("placed pin-access solve repeats a domain id")
    Dependencies = {
        (
            Value.CatalogVersion,
            Value.TechnologyFingerprint,
            Value.ResourceModelFingerprint,
        )
        for Value in OrderedDomains
    }
    if len(Dependencies) != 1:
        raise ValueError("placed pin-access domains use mixed dependencies")
    return OrderedDomains, tuple(
        FixedPlacementPinAccessDomain(
            DomainId=Value.DomainId,
            Signal=Value.Signal,
            Terminal=Value.Terminal,
            Options=Value.Options,
            Complete=Value.Complete,
            IncompleteReason=Value.IncompleteReason,
            SourceDomainFingerprint=Value.DomainFingerprint,
            CatalogVersion=Value.CatalogVersion,
            TechnologyFingerprint=Value.TechnologyFingerprint,
            ResourceModelFingerprint=Value.ResourceModelFingerprint,
        )
        for Value in OrderedDomains
    )


def AdaptFixedPlacementPinAccessSolveResult(
    Domains: Iterable[PlacedPinAccessOptionDomain],
    Result: FixedPlacementPinAccessSolveResult,
) -> PlacementAccessSolveResult:
    """Publish the legacy exact solve through the v17 typed boundary."""
    OrderedDomains, FixedDomains = _ConvertPlacedPinAccessDomains(Domains)
    ExpectedProblemFingerprint = _BuildFixedPinAccessProblemFingerprint(
        FixedDomains
    )
    if Result.ProblemFingerprint != ExpectedProblemFingerprint:
        raise ValueError("fixed pin-access result problem identity mismatch")
    DomainById = {Value.DomainId: Value for Value in OrderedDomains}
    if Result.Status is FixedPlacementPinAccessStatus.Feasible:
        Witness = FreezeSelectedPlacementPinAccessWitness(
            OrderedDomains,
            Result.SelectedOptionFingerprints,
        )
        return PlacementAccessSolveResult(
            Status=PlacementAccessSolveStatus.Feasible,
            ProblemFingerprint=Result.ProblemFingerprint,
            ExpansionCount=Result.ExpansionCount,
            MaximumExpansions=Result.MaximumExpansions,
            SearchComplete=True,
            OptimalityProven=False,
            SelectedWitness=Witness,
            Domains=OrderedDomains,
        )
    if Result.Status is FixedPlacementPinAccessStatus.Incomplete:
        return PlacementAccessSolveResult(
            Status=PlacementAccessSolveStatus.Incomplete,
            ProblemFingerprint=Result.ProblemFingerprint,
            ExpansionCount=Result.ExpansionCount,
            MaximumExpansions=Result.MaximumExpansions,
            SearchComplete=False,
            OptimalityProven=False,
            IncompleteReason=Result.IncompleteReason,
            Domains=OrderedDomains,
        )
    if Result.UnsatisfiableCore is None:
        raise ValueError("unsatisfiable fixed access result has no core")
    CoreDomainIds = tuple(
        Value.DomainId for Value in Result.UnsatisfiableCore.Domains
    )
    if any(Value not in DomainById for Value in CoreDomainIds):
        raise ValueError("fixed access core references an unknown domain")
    CoreDomainFingerprints = tuple(sorted(
        DomainById[Value].DomainFingerprint for Value in CoreDomainIds
    ))
    SelectionLiterals = tuple(sorted(
        (
            Domain.DomainId,
            Option.SelectionFingerprint,
        )
        for Domain in Result.UnsatisfiableCore.Domains
        for Option in Domain.Options
    ))
    BlockingResources = tuple(sorted({
        Resource
        for Conflict in Result.UnsatisfiableCore.Conflicts
        for Resource in Conflict.ResourceIds
    }))
    CoreFingerprint = BuildStableFingerprint({
        "Kind": "placement-access-conflict-core-v1",
        "ProblemFingerprint": Result.ProblemFingerprint,
        "DomainFingerprints": CoreDomainFingerprints,
        "SelectionLiterals": SelectionLiterals,
        "BlockingResources": BlockingResources,
        "Complete": True,
        "Minimal": False,
    })
    return PlacementAccessSolveResult(
        Status=PlacementAccessSolveStatus.Unsatisfiable,
        ProblemFingerprint=Result.ProblemFingerprint,
        ExpansionCount=Result.ExpansionCount,
        MaximumExpansions=Result.MaximumExpansions,
        SearchComplete=True,
        OptimalityProven=False,
        ConflictCore=PlacementAccessConflictCore(
            CoreFingerprint=CoreFingerprint,
            ProblemFingerprint=Result.ProblemFingerprint,
            DomainFingerprints=CoreDomainFingerprints,
            SelectionLiterals=SelectionLiterals,
            BlockingResources=BlockingResources,
            Complete=True,
            Minimal=False,
            ProblemDomains=OrderedDomains,
        ),
        Domains=OrderedDomains,
    )


def SolvePlacedPinAccessOptionDomains(
    Domains: Iterable[PlacedPinAccessOptionDomain],
    *,
    ResourceGraph: RoutingResourceGraph | None = None,
    MaximumExpansions: int = 100_000,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> PlacementAccessSolveResult:
    """Solve exact catalog domains while preserving the legacy solver API."""
    OrderedDomains, FixedDomains = _ConvertPlacedPinAccessDomains(Domains)
    Result = SolveFixedPlacementPinAccessDomains(
        FixedDomains,
        ResourceGraph=ResourceGraph,
        MaximumExpansions=MaximumExpansions,
        WorkCheck=WorkCheck,
    )
    return AdaptFixedPlacementPinAccessSolveResult(
        OrderedDomains,
        Result,
    )


def ReplayPlacedPinAccessConflictCore(
    Domains: Iterable[PlacedPinAccessOptionDomain],
    Core: PlacementAccessConflictCore,
    *,
    ResourceGraph: RoutingResourceGraph | None = None,
    MaximumExpansions: int = 100_000,
) -> PlacementAccessSolveResult:
    """Replay one retained exact-domain problem and verify its complete core."""
    if not Core.Complete:
        raise ValueError("only a complete pin-access core can be replayed")
    OrderedDomains = tuple(sorted(Domains, key=lambda Value: Value.DomainId))
    Result = SolvePlacedPinAccessOptionDomains(
        OrderedDomains,
        ResourceGraph=ResourceGraph,
        MaximumExpansions=MaximumExpansions,
    )
    if (
        Result.Status is not PlacementAccessSolveStatus.Unsatisfiable
        or Result.ProblemFingerprint != Core.ProblemFingerprint
        or Result.ConflictCore != Core
    ):
        raise ValueError(
            "retained placement pin-access core did not replay identically"
        )
    return Result


def ReplayFixedPlacementPinAccessUnsatisfiableCore(
    Core: FixedPlacementPinAccessUnsatisfiableCore,
    *,
    ResourceGraph: RoutingResourceGraph | None = None,
) -> FixedPlacementPinAccessSolveResult:
    """Re-run one published independent core with no omitted domains."""
    Result = SolveFixedPlacementPinAccessDomains(
        Core.Domains,
        ResourceGraph=ResourceGraph,
        MaximumExpansions=max(1, sum(
            max(1, len(Value.Options)) for Value in Core.Domains
        ) * max(1, len(Core.Domains)) * 8),
    )
    if Result.Status is not FixedPlacementPinAccessStatus.Unsatisfiable:
        raise ValueError("published pin-access core did not replay as unsat")
    if (
        Result.UnsatisfiableCore is None
        or Result.UnsatisfiableCore.CoreFingerprint != Core.CoreFingerprint
    ):
        raise ValueError("published pin-access core identity changed on replay")
    return Result

@dataclass(frozen=True)
class _ImmutableStubClaimMask:
    """One immutable stub claim encoded as position bit sets.

    Derived perimeter access factors select only frozen terminal stubs.  Their
    claim-conflict relation is entirely pairwise: every illegal union is a
    wire/air/support/electrical intersection between either one stub or two
    stubs.  Representing the four position sets as integers makes that
    relation cheap to compile once without dropping any legal stub option.
    """

    WireMask: int
    SupportMask: int
    RequiredAirMask: int
    ElectricalMask: int

@dataclass(frozen=True)
class _ImmutableStubCapacityFactor:
    """Precompiled exact compatibility relation for one frozen fabric."""

    ValidOptionMasks: tuple[int, ...]
    ConflictMasksByDomainOption: tuple[
        tuple[tuple[int, ...], ...], ...
    ]

def _ImmutableStubClaimMaskHasSelfConflict(
    Claims: _ImmutableStubClaimMask,
) -> bool:
    """Match ``FindSelfClaimConflicts`` for one bit-encoded claim union."""
    return bool(
        (Claims.RequiredAirMask & Claims.WireMask)
        or (
            Claims.SupportMask
            & (Claims.WireMask | Claims.RequiredAirMask)
        )
    )

def _MergeImmutableStubClaimMasks(
    First: _ImmutableStubClaimMask,
    Second: _ImmutableStubClaimMask,
) -> _ImmutableStubClaimMask:
    """Return the exact union of two immutable stub claim masks."""
    return _ImmutableStubClaimMask(
        WireMask=First.WireMask | Second.WireMask,
        SupportMask=First.SupportMask | Second.SupportMask,
        RequiredAirMask=(
            First.RequiredAirMask | Second.RequiredAirMask
        ),
        ElectricalMask=First.ElectricalMask | Second.ElectricalMask,
    )

def _ImmutableStubClaimMasksConflict(
    First: _ImmutableStubClaimMask,
    Second: _ImmutableStubClaimMask,
) -> bool:
    """Match ``_PlacementAccessClaimsConflict`` for encoded claims."""
    return bool(
        (First.WireMask & Second.ElectricalMask)
        or (Second.WireMask & First.ElectricalMask)
        or (
            First.SupportMask
            & (Second.WireMask | Second.RequiredAirMask)
        )
        or (
            Second.SupportMask
            & (First.WireMask | First.RequiredAirMask)
        )
        or (First.RequiredAirMask & Second.WireMask)
        or (Second.RequiredAirMask & First.WireMask)
    )

def _BuildImmutableStubCapacityFactor(
    Fabric: PlacementAccessFabric,
    *,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> _ImmutableStubCapacityFactor:
    """Compile every frozen-stub compatibility relation once.

    The normal generic solver merges frozensets every time its MRV search
    revisits a partial state.  This factor instead records the exact option
    conflicts as bit masks.  Same-signal pairs are checked against the
    self-conflict predicate for their union; different-signal pairs use the
    ordinary placement-claim conflict predicate.  Since both predicates are
    pairwise set intersections, pairwise validity is equivalent to validity
    of the complete selected union.
    """
    Domains = Fabric.TerminalDomains
    Positions = tuple(sorted({
        Position
        for Domain in Domains
        for Stub in Domain.EscapeStubs
        for Position in (
            *Stub.PhysicalClaims.WireCells,
            *Stub.PhysicalClaims.SupportCells,
            *Stub.PhysicalClaims.RequiredAirCells,
            *Stub.PhysicalClaims.ElectricalCells,
        )
    }))
    PositionIndex = {
        Position: Index
        for Index, Position in enumerate(Positions)
    }

    def BuildMask(ClaimPositions: Iterable[Position3]) -> int:
        Mask = 0
        for Position in ClaimPositions:
            Mask |= 1 << PositionIndex[Position]
        return Mask

    ClaimMasksByDomain = tuple(
        tuple(
            _ImmutableStubClaimMask(
                WireMask=BuildMask(Stub.PhysicalClaims.WireCells),
                SupportMask=BuildMask(Stub.PhysicalClaims.SupportCells),
                RequiredAirMask=BuildMask(
                    Stub.PhysicalClaims.RequiredAirCells
                ),
                ElectricalMask=BuildMask(
                    Stub.PhysicalClaims.ElectricalCells
                ),
            )
            for Stub in Domain.EscapeStubs
        )
        for Domain in Domains
    )
    ValidOptionMasks = tuple(
        sum(
            1 << OptionIndex
            for OptionIndex, Claims in enumerate(DomainMasks)
            if not _ImmutableStubClaimMaskHasSelfConflict(Claims)
        )
        for DomainMasks in ClaimMasksByDomain
    )
    DomainCount = len(Domains)
    MutableConflictMasks: list[list[list[int]]] = [
        [
            [0 for _ in range(DomainCount)]
            for _Option in DomainMasks
        ]
        for DomainMasks in ClaimMasksByDomain
    ]
    TotalPairCount = sum(
        len(ClaimMasksByDomain[FirstDomainIndex])
        * len(ClaimMasksByDomain[SecondDomainIndex])
        for FirstDomainIndex in range(DomainCount)
        for SecondDomainIndex in range(
            FirstDomainIndex + 1,
            DomainCount,
        )
    )
    CompletedPairCount = 0
    if WorkCheck is not None:
        WorkCheck({
            "Phase": "placement-access-immutable-stub-factor",
            "CompletedPairCount": CompletedPairCount,
            "PairCount": TotalPairCount,
            "TerminalCount": DomainCount,
        })
    for FirstDomainIndex in range(DomainCount):
        FirstSignal = Domains[FirstDomainIndex].Signal
        for SecondDomainIndex in range(
            FirstDomainIndex + 1,
            DomainCount,
        ):
            SameSignal = FirstSignal == Domains[SecondDomainIndex].Signal
            for FirstOptionIndex, FirstClaims in enumerate(
                ClaimMasksByDomain[FirstDomainIndex]
            ):
                for SecondOptionIndex, SecondClaims in enumerate(
                    ClaimMasksByDomain[SecondDomainIndex]
                ):
                    CompletedPairCount += 1
                    if (
                        WorkCheck is not None
                        and CompletedPairCount % 256 == 0
                    ):
                        WorkCheck({
                            "Phase": (
                                "placement-access-immutable-stub-factor"
                            ),
                            "CompletedPairCount": CompletedPairCount,
                            "PairCount": TotalPairCount,
                            "TerminalCount": DomainCount,
                        })
                    Conflict = (
                        _ImmutableStubClaimMaskHasSelfConflict(
                            _MergeImmutableStubClaimMasks(
                                FirstClaims,
                                SecondClaims,
                            )
                        )
                        if SameSignal
                        else _ImmutableStubClaimMasksConflict(
                            FirstClaims,
                            SecondClaims,
                        )
                    )
                    if not Conflict:
                        continue
                    MutableConflictMasks[FirstDomainIndex][
                        FirstOptionIndex
                    ][SecondDomainIndex] |= 1 << SecondOptionIndex
                    MutableConflictMasks[SecondDomainIndex][
                        SecondOptionIndex
                    ][FirstDomainIndex] |= 1 << FirstOptionIndex
    return _ImmutableStubCapacityFactor(
        ValidOptionMasks=ValidOptionMasks,
        ConflictMasksByDomainOption=tuple(
            tuple(
                tuple(ConflictMasks)
                for ConflictMasks in DomainConflictMasks
            )
            for DomainConflictMasks in MutableConflictMasks
        ),
    )

def _CanUseImmutableStubCapacityFactor(
    Fabric: PlacementAccessFabric,
    *,
    AssignmentValidator: Callable[[PlacementAccessAssignment], bool] | None,
    RequiredCompleteSignalRoutes: frozenset[str],
    OptionalLocalRouteClaims: tuple[Any, ...],
    RequireCompleteSignalRoutes: bool | None,
) -> bool:
    """Limit the fast factor to terminal-only derived perimeter contracts."""
    return bool(
        Fabric.TopologyKind == "derived-perimeter-access-v1"
        and AssignmentValidator is None
        and not RequiredCompleteSignalRoutes
        and not OptionalLocalRouteClaims
        and RequireCompleteSignalRoutes is False
    )

def _SolveImmutableStubCapacityFactor(
    Fabric: PlacementAccessFabric,
    *,
    MaximumExpansions: int,
    WorkCheck: Callable[[dict[str, object]], None] | None,
) -> PlacementAccessAssignment:
    """Solve one terminal-only immutable-stub factor with bit propagation.

    This has the same MRV order and one-expansion-per-selected-stub contract
    as ``SolvePlacementAccessFabricCapacity``.  It changes only how the
    already-fixed option compatibility is evaluated.
    """
    Factor = _BuildImmutableStubCapacityFactor(
        Fabric,
        WorkCheck=WorkCheck,
    )
    Domains = Fabric.TerminalDomains
    Selected: dict[int, int] = {}
    ExpansionCount = 0
    Exhausted = False
    ConflictSignals: set[str] = set()
    FirstUnroutableSignal = ""

    def CompatibleOptionMask(
        DomainIndex: int,
        *,
        RecordConflicts: bool,
    ) -> int:
        Domain = Domains[DomainIndex]
        ValidMask = Factor.ValidOptionMasks[DomainIndex]
        Mask = ValidMask
        if RecordConflicts and (
            ValidMask != (1 << len(Domain.EscapeStubs)) - 1
        ):
            # The generic path records a signal as soon as one of its own
            # options has an electrical self-conflict, even when a sibling
            # option remains usable.  Preserve that diagnostic behavior from
            # the precompiled validity mask without re-merging claim sets.
            ConflictSignals.add(Domain.Signal)
        for SelectedDomainIndex, SelectedOptionIndex in Selected.items():
            BlockingMask = Factor.ConflictMasksByDomainOption[
                SelectedDomainIndex
            ][SelectedOptionIndex][DomainIndex]
            if RecordConflicts and ValidMask & BlockingMask:
                SelectedSignal = Domains[SelectedDomainIndex].Signal
                if SelectedSignal == Domain.Signal:
                    # Same-signal conflicts are self-conflicts of the merged
                    # claim union, so the generic solver reports only this
                    # signal rather than an inter-signal conflict pair.
                    ConflictSignals.add(Domain.Signal)
                else:
                    ConflictSignals.update((Domain.Signal, SelectedSignal))
            Mask &= ~BlockingMask
        return Mask & ValidMask

    def RecordEmptyDomainConflict(DomainIndex: int) -> None:
        """Retain a small exact signal core when propagation empties a domain."""
        Domain = Domains[DomainIndex]
        ConflictSignals.add(Domain.Signal)
        InitialMask = Factor.ValidOptionMasks[DomainIndex]
        for SelectedDomainIndex, SelectedOptionIndex in Selected.items():
            if Domains[SelectedDomainIndex].Signal == Domain.Signal:
                continue
            BlockingMask = (
                Factor.ConflictMasksByDomainOption[SelectedDomainIndex]
                [SelectedOptionIndex][DomainIndex]
            )
            if InitialMask & BlockingMask:
                ConflictSignals.add(Domains[SelectedDomainIndex].Signal)

    def IterateOptionIndices(Mask: int) -> Iterable[int]:
        while Mask:
            LeastSignificantBit = Mask & -Mask
            yield LeastSignificantBit.bit_length() - 1
            Mask ^= LeastSignificantBit

    def Search() -> bool:
        nonlocal ExpansionCount, Exhausted, FirstUnroutableSignal
        if WorkCheck is not None and ExpansionCount % 256 == 0:
            WorkCheck({
                "Phase": "placement-access-capacity-search",
                "ExpansionCount": ExpansionCount,
                "MaximumExpansions": MaximumExpansions,
                "SelectedTerminalCount": len(Selected),
                "TerminalCount": len(Domains),
                "SelectedLocalRouteCount": 0,
                "OptionalLocalRouteCount": 0,
            })
        if len(Selected) == len(Domains):
            return True
        SelectedSignals = {
            Domains[DomainIndex].Signal
            for DomainIndex in Selected
        }
        RankedDomains: list[tuple[
            int,
            int,
            str,
            Position3,
            int,
            int,
        ]] = []
        for DomainIndex, Domain in enumerate(Domains):
            if DomainIndex in Selected:
                continue
            CompatibleMask = CompatibleOptionMask(
                DomainIndex,
                RecordConflicts=True,
            )
            if not CompatibleMask:
                if not FirstUnroutableSignal:
                    FirstUnroutableSignal = Domain.Signal
                RecordEmptyDomainConflict(DomainIndex)
                return False
            RankedDomains.append((
                0 if Domain.Signal in SelectedSignals else 1,
                CompatibleMask.bit_count(),
                Domain.Signal,
                Domain.Terminal,
                DomainIndex,
                CompatibleMask,
            ))
        (
            _PartiallySelectedRank,
            _CompatibleCount,
            _Signal,
            _Terminal,
            DomainIndex,
            CompatibleMask,
        ) = min(RankedDomains)
        for OptionIndex in IterateOptionIndices(CompatibleMask):
            if ExpansionCount >= MaximumExpansions:
                Exhausted = True
                return False
            ExpansionCount += 1
            Selected[DomainIndex] = OptionIndex
            if Search():
                return True
            Selected.pop(DomainIndex, None)
        return False

    Success = Search()
    SelectedValues = tuple(
        (
            Domains[DomainIndex].Signal,
            Domains[DomainIndex].Terminal,
            Selected[DomainIndex],
        )
        for DomainIndex in sorted(Selected)
    ) if Success else ()
    ClaimsBySignal: dict[str, RoutingResourceClaims] = {}
    if Success:
        for DomainIndex in sorted(Selected):
            Domain = Domains[DomainIndex]
            Stub = Domain.EscapeStubs[Selected[DomainIndex]]
            ClaimsBySignal[Domain.Signal] = _MergePlacementAccessClaims(
                ClaimsBySignal.get(
                    Domain.Signal,
                    RoutingResourceClaims(),
                ),
                Stub.PhysicalClaims,
            )
    CapacityResourceIds = tuple(sorted({
        Resource
        for Claims in ClaimsBySignal.values()
        for Resource in Claims.ResourceIds
    }, key=str)) if Success else ()
    AssignmentFingerprint = (
        sha256(repr((
            Fabric.FabricFingerprint,
            SelectedValues,
            (),
            (),
            CapacityResourceIds,
        )).encode("utf-8")).hexdigest()[:16]
        if Success
        else ""
    )
    return PlacementAccessAssignment(
        FabricFingerprint=Fabric.FabricFingerprint,
        AssignmentFingerprint=AssignmentFingerprint,
        SelectedStubIndices=SelectedValues,
        CapacityResourceIds=CapacityResourceIds,
        ExpansionCount=ExpansionCount,
        Success=Success,
        Complete=not Exhausted,
        ConflictSignals=(
            () if Success else tuple(sorted(ConflictSignals))
        ),
        FirstUnroutableSignal=(
            "" if Success else FirstUnroutableSignal
        ),
        IncompleteReason=(
            "work-cap-exhausted" if Exhausted else ""
        ),
    )

def SolvePlacementAccessFabricCapacity(
    Fabric: PlacementAccessFabric,
    *,
    MaximumExpansions: int = 50_000,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    AssignmentValidator: (
        Callable[[PlacementAccessAssignment], bool] | None
    ) = None,
    RequiredCompleteSignalRoutes: frozenset[str] = frozenset(),
    OptionalLocalRouteClaims: tuple[Any, ...] = (),
    RequireCompleteSignalRoutes: bool | None = None,
) -> PlacementAccessAssignment:
    """Select one immutable local-access contract in one bounded solve.

    A complete placement-local claim is an alternative to fabric escapes for
    its signal, not an obstacle selected before the problem is known.  The
    bounded search therefore chooses to retain or release every supplied
    claim alongside terminal escapes.  An optional validator folds a
    downstream exact-capacity factor into this same search.  A rejected leaf
    is backtracked inside this invocation; it is not a second capacity solve
    or a post-route retry.
    """
    if MaximumExpansions < 1:
        raise ValueError("placement access capacity requires a work cap")
    if not Fabric.Complete:
        return PlacementAccessAssignment(
            FabricFingerprint=Fabric.FabricFingerprint,
            AssignmentFingerprint="",
            SelectedStubIndices=(),
            CapacityResourceIds=(),
            ExpansionCount=0,
            Success=False,
            Complete=False,
            IncompleteReason=Fabric.IncompleteReason,
        )
    if _CanUseImmutableStubCapacityFactor(
        Fabric,
        AssignmentValidator=AssignmentValidator,
        RequiredCompleteSignalRoutes=RequiredCompleteSignalRoutes,
        OptionalLocalRouteClaims=OptionalLocalRouteClaims,
        RequireCompleteSignalRoutes=RequireCompleteSignalRoutes,
    ):
        return _SolveImmutableStubCapacityFactor(
            Fabric,
            MaximumExpansions=MaximumExpansions,
            WorkCheck=WorkCheck,
        )
    Selected: dict[int, int] = {}
    ClaimsBySignal: dict[str, RoutingResourceClaims] = {}
    SelectedSignalRoutes: dict[str, tuple[Position3, ...]] = {}
    ExpansionCount = 0
    Exhausted = False
    ConflictSignals: set[str] = set()
    MaximumRoutedSignalCount = 0
    FrontierSignals: tuple[str, ...] = ()
    FirstUnroutableSignal = ""
    RejectedCompleteAssignmentCount = 0
    IncompleteRouteDomain = False
    FirstIncompleteRouteSignal = ""
    FabricNodeSet = frozenset(Fabric.Nodes)
    FabricEdgeSet = frozenset(
        tuple(sorted((First, Second))) for First, Second in Fabric.Edges
    )
    EffectiveTechnology = (
        Fabric.Technology or DefaultRedstoneRoutingTechnology
    )
    FabricYValues = tuple(sorted({Position[1] for Position in Fabric.Nodes}))
    FabricZValuesByY = {
        FabricY: tuple(sorted({
            Position[2]
            for Position in Fabric.Nodes
            if Position[1] == FabricY
        }))
        for FabricY in FabricYValues
    }
    TrunkCoordinatesByY = {
        FabricY: tuple(sorted(
            X
            for X in {
                Position[0]
                for Position in Fabric.Nodes
                if Position[1] == FabricY
            }
            if all(
                (X, FabricY, Z) in FabricNodeSet
                for Z in FabricZValuesByY[FabricY]
            )
        ))
        for FabricY in FabricYValues
    }
    LaneCoordinatesByY = {
        FabricY: tuple(sorted(
            Z
            for Z in {
                Position[2]
                for Position in Fabric.Nodes
                if Position[1] == FabricY
            }
            if all(
                (X, FabricY, Z) in FabricNodeSet
                for X in {
                    Position[0]
                    for Position in Fabric.Nodes
                    if Position[1] == FabricY
                }
            )
        ))
        for FabricY in FabricYValues
    }
    TerminalDomainCountBySignal: dict[str, int] = {}
    for Domain in Fabric.TerminalDomains:
        TerminalDomainCountBySignal[Domain.Signal] = (
            TerminalDomainCountBySignal.get(Domain.Signal, 0) + 1
        )
    LocalClaimBySignal: dict[str, Any] = {}
    for Claim in OptionalLocalRouteClaims:
        Signal = str(getattr(Claim, "Signal", ""))
        Claims = getattr(Claim, "Claims", None)
        if not Signal or Claims is None:
            raise ValueError(
                "optional local-route claims require signal and claims"
            )
        if Signal not in TerminalDomainCountBySignal:
            # A claim unrelated to this fabric cannot establish a terminal
            # contract, so keeping it would make the factor depend on hidden
            # geometry outside its published domain.
            continue
        if Signal in LocalClaimBySignal:
            raise ValueError(
                "optional local-route claims must be unique per signal"
            )
        LocalClaimBySignal[Signal] = Claim
    LocalClaimChoice: dict[str, bool] = {}
    SelectedLocalRouteSignals: set[str] = set()
    # The default preserves the complete ring-tree contract used by focused
    # access-fabric callers.  The compact placement flow may instead freeze
    # terminal access here and carry one authoritative track-preparation
    # witness as its complete tree proof.  That proof is still built before
    # the sole route attempt; it simply avoids treating a perimeter ring as a
    # second, oversized detailed router.
    RequireAllCompleteSignalRoutes = (
        True
        if RequireCompleteSignalRoutes is None
        else bool(RequireCompleteSignalRoutes)
    )

    def SignalRequiresCompleteRoute(Signal: str) -> bool:
        return (
            RequireAllCompleteSignalRoutes
            or Signal in RequiredCompleteSignalRoutes
        )

    def BuildCurrentAssignment() -> PlacementAccessAssignment:
        SelectedValues = tuple(
            (
                Fabric.TerminalDomains[Index].Signal,
                Fabric.TerminalDomains[Index].Terminal,
                Selected[Index],
            )
            for Index in sorted(Selected)
        )
        CapacityResourceIds = tuple(sorted({
            Resource
            for Claims in ClaimsBySignal.values()
            for Resource in Claims.ResourceIds
        }, key=str))
        AssignmentFingerprint = sha256(repr((
            Fabric.FabricFingerprint,
            SelectedValues,
            tuple(sorted(SelectedLocalRouteSignals)),
            tuple(sorted(SelectedSignalRoutes.items())),
            CapacityResourceIds,
        )).encode("utf-8")).hexdigest()[:16]
        return PlacementAccessAssignment(
            FabricFingerprint=Fabric.FabricFingerprint,
            AssignmentFingerprint=AssignmentFingerprint,
            SelectedStubIndices=SelectedValues,
            CapacityResourceIds=CapacityResourceIds,
            ExpansionCount=ExpansionCount,
            Success=True,
            Complete=True,
            SignalRoutes=tuple(sorted(SelectedSignalRoutes.items())),
            SelectedLocalRouteSignals=tuple(
                sorted(SelectedLocalRouteSignals)
            ),
        )

    def BuildSignalRouteCandidates(
        Signal: str,
        Ingresses: tuple[Position3, ...],
    ) -> tuple[tuple[tuple[Position3, ...], RoutingResourceClaims], ...]:
        nonlocal IncompleteRouteDomain, FirstIncompleteRouteSignal
        if len(Ingresses) <= 1:
            Nodes = tuple(Ingresses)
            return ((Nodes, RoutingResourceClaims()),)
        IngressLayers = {Position[1] for Position in Ingresses}
        if len(IngressLayers) != 1:
            return ()
        FabricY = next(iter(IngressLayers))
        MinimumZ = min(Position[2] for Position in Ingresses)
        MaximumZ = max(Position[2] for Position in Ingresses)
        MinimumX = min(Position[0] for Position in Ingresses)
        MaximumX = max(Position[0] for Position in Ingresses)
        Results = []
        SeenNodeSets: set[frozenset[Position3]] = set()

        def RetainRouteNodes(Nodes: set[Position3]) -> None:
            NodeSet = frozenset(Nodes)
            if NodeSet in SeenNodeSets or not NodeSet <= FabricNodeSet:
                return
            if any(
                tuple(sorted((First, Second))) not in FabricEdgeSet
                for First in NodeSet
                for Second in (
                    (First[0] + 1, First[1], First[2]),
                    (First[0], First[1], First[2] + 1),
                )
                if Second in NodeSet
            ):
                return
            OrderedNodes = tuple(sorted(NodeSet))
            Claims = RoutingResourceClaims(
                WireCells=NodeSet,
                SupportCells=frozenset(
                    (X, Y - 1, Z) for X, Y, Z in NodeSet
                ),
                RequiredAirCells=frozenset(),
                ElectricalCells=frozenset(
                    Position
                    for Node in NodeSet
                    for Position in (
                        Node,
                        *EffectiveTechnology.NeighborPositions(Node),
                    )
                ),
            )
            if FindSelfClaimConflicts({Signal: Claims}):
                return
            SeenNodeSets.add(NodeSet)
            Results.append((OrderedNodes, Claims))

        if Fabric.TopologyKind == "derived-perimeter-access-v1":
            CycleNodeSets = _BuildDerivedPerimeterCycleRouteNodeSets(
                Ingresses,
                FabricY,
                Fabric.Edges,
            )
            if CycleNodeSets is None:
                IncompleteRouteDomain = True
                if not FirstIncompleteRouteSignal:
                    FirstIncompleteRouteSignal = Signal
                return ()
            for Nodes in CycleNodeSets:
                RetainRouteNodes(set(Nodes))
            return tuple(Results)

        if Fabric.TopologyKind == "perimeter-access-ring-v1":
            Adjacency: dict[Position3, list[Position3]] = {}
            for First, Second in Fabric.Edges:
                Adjacency.setdefault(First, []).append(Second)
                Adjacency.setdefault(Second, []).append(First)
            for Values in Adjacency.values():
                Values.sort()
            for Root in Ingresses:
                Tree = {Root}
                Remaining = set(Ingresses) - Tree
                while Remaining:
                    Queue = deque(sorted(Tree))
                    Parent: dict[Position3, Position3 | None] = {
                        Position: None for Position in Tree
                    }
                    Reached = None
                    while Queue and Reached is None:
                        Current = Queue.popleft()
                        if Current in Remaining:
                            Reached = Current
                            break
                        for Next in Adjacency.get(Current, ()):
                            if Next in Parent:
                                continue
                            Parent[Next] = Current
                            Queue.append(Next)
                    if Reached is None:
                        Tree.clear()
                        break
                    Cursor: Position3 | None = Reached
                    while Cursor is not None:
                        Tree.add(Cursor)
                        Cursor = Parent[Cursor]
                    Remaining.remove(Reached)
                if Tree:
                    RetainRouteNodes(Tree)
            return tuple(Results)

        for TrunkX in TrunkCoordinatesByY.get(FabricY, ()):
            Nodes = {
                (TrunkX, FabricY, Z)
                for Z in range(MinimumZ, MaximumZ + 1)
            }
            for IngressX, _IngressY, IngressZ in Ingresses:
                Nodes.update(
                    (X, FabricY, IngressZ)
                    for X in range(
                        min(IngressX, TrunkX),
                        max(IngressX, TrunkX) + 1,
                    )
                )
            RetainRouteNodes(Nodes)
        for TrunkZ in LaneCoordinatesByY.get(FabricY, ()):
            Nodes = {
                (X, FabricY, TrunkZ)
                for X in range(MinimumX, MaximumX + 1)
            }
            for IngressX, _IngressY, IngressZ in Ingresses:
                Nodes.update(
                    (IngressX, FabricY, Z)
                    for Z in range(
                        min(IngressZ, TrunkZ),
                        max(IngressZ, TrunkZ) + 1,
                    )
                )
            RetainRouteNodes(Nodes)
        return tuple(Results)

    def SelectCompleteSignalRoutes() -> bool:
        nonlocal ExpansionCount, Exhausted, SelectedSignalRoutes
        IngressesBySignal: dict[str, list[Position3]] = {}
        for DomainIndex, StubIndex in Selected.items():
            Domain = Fabric.TerminalDomains[DomainIndex]
            IngressesBySignal.setdefault(Domain.Signal, []).append(
                Domain.EscapeStubs[StubIndex].Ingress
            )
        RouteDomains = {
            Signal: BuildSignalRouteCandidates(
                Signal,
                tuple(sorted(set(Ingresses))),
            )
            for Signal, Ingresses in IngressesBySignal.items()
        }
        if any(not Values for Values in RouteDomains.values()):
            ConflictSignals.update(
                Signal for Signal, Values in RouteDomains.items() if not Values
            )
            return False
        RouteClaimsBySignal: dict[str, RoutingResourceClaims] = {}
        RouteNodesBySignal: dict[str, tuple[Position3, ...]] = {}

        def SelectRoute() -> bool:
            nonlocal ExpansionCount, Exhausted, SelectedSignalRoutes
            if len(RouteNodesBySignal) == len(RouteDomains):
                SelectedSignalRoutes = dict(RouteNodesBySignal)
                return True
            Ranked = []
            for Signal, Candidates in RouteDomains.items():
                if Signal in RouteNodesBySignal:
                    continue
                Compatible = []
                for Nodes, RouteClaims in Candidates:
                    CombinedClaims = _MergePlacementAccessClaims(
                        ClaimsBySignal[Signal],
                        RouteClaims,
                    )
                    if FindSelfClaimConflicts({Signal: CombinedClaims}):
                        continue
                    if any(
                        OtherSignal != Signal
                        and _PlacementAccessClaimsConflict(
                            CombinedClaims,
                            _MergePlacementAccessClaims(
                                ClaimsBySignal[OtherSignal],
                                RouteClaimsBySignal.get(
                                    OtherSignal,
                                    RoutingResourceClaims(),
                                ),
                            ),
                        )
                        for OtherSignal in ClaimsBySignal
                    ):
                        continue
                    Compatible.append((Nodes, RouteClaims))
                if not Compatible:
                    ConflictSignals.add(Signal)
                    return False
                Ranked.append((len(Compatible), Signal, Compatible))
            _Count, Signal, Compatible = min(Ranked)
            for Nodes, RouteClaims in Compatible:
                if ExpansionCount >= MaximumExpansions:
                    Exhausted = True
                    return False
                ExpansionCount += 1
                RouteNodesBySignal[Signal] = Nodes
                RouteClaimsBySignal[Signal] = RouteClaims
                if SelectRoute():
                    return True
                RouteNodesBySignal.pop(Signal, None)
                RouteClaimsBySignal.pop(Signal, None)
            return False

        return SelectRoute()

    def CompatibleStubs(
        DomainIndex: int,
    ) -> tuple[tuple[int, RoutingResourceClaims], ...]:
        Domain = Fabric.TerminalDomains[DomainIndex]
        ExistingSignalClaims = ClaimsBySignal.get(
            Domain.Signal,
            RoutingResourceClaims(),
        )
        Compatible = []
        for StubIndex, Stub in enumerate(Domain.EscapeStubs):
            MergedClaims = _MergePlacementAccessClaims(
                ExistingSignalClaims,
                Stub.PhysicalClaims,
            )
            # A terminal domain can contribute more than one escape to the
            # same fanout signal.  Checking each stub in isolation is not
            # sufficient: their union must be electrically self-consistent
            # before it becomes a frozen portal tuple for the authoritative
            # planner.
            if FindSelfClaimConflicts({Domain.Signal: MergedClaims}):
                ConflictSignals.add(Domain.Signal)
                continue
            BlockingSignals = tuple(
                Signal
                for Signal, Claims in ClaimsBySignal.items()
                if (
                    Signal != Domain.Signal
                    and _PlacementAccessClaimsConflict(
                        MergedClaims,
                        Claims,
                    )
                )
            )
            if BlockingSignals:
                ConflictSignals.update((Domain.Signal, *BlockingSignals))
                continue
            Compatible.append((StubIndex, MergedClaims))
        return tuple(Compatible)

    def Search() -> bool:
        nonlocal ExpansionCount, Exhausted
        nonlocal MaximumRoutedSignalCount, FrontierSignals
        nonlocal FirstUnroutableSignal
        nonlocal RejectedCompleteAssignmentCount
        if len(SelectedSignalRoutes) > MaximumRoutedSignalCount:
            MaximumRoutedSignalCount = len(SelectedSignalRoutes)
            FrontierSignals = tuple(sorted(SelectedSignalRoutes))
        if WorkCheck is not None and ExpansionCount % 256 == 0:
            WorkCheck({
                "Phase": "placement-access-capacity-search",
                "ExpansionCount": ExpansionCount,
                "MaximumExpansions": MaximumExpansions,
                "SelectedTerminalCount": len(Selected),
                "TerminalCount": len(Fabric.TerminalDomains),
                "SelectedLocalRouteCount": len(SelectedLocalRouteSignals),
                "OptionalLocalRouteCount": len(LocalClaimBySignal),
            })
        PendingLocalClaimSignals = tuple(sorted(
            (
                Signal
                for Signal in LocalClaimBySignal
                if Signal not in LocalClaimChoice
            ),
            key=lambda Signal: (
                -len(LocalClaimBySignal[Signal].Claims.ResourceIds),
                Signal,
            ),
        ))
        if PendingLocalClaimSignals:
            Signal = PendingLocalClaimSignals[0]
            Claim = LocalClaimBySignal[Signal]
            for KeepClaim in (True, False):
                if ExpansionCount >= MaximumExpansions:
                    Exhausted = True
                    return False
                ExpansionCount += 1
                if KeepClaim:
                    ClaimValues = Claim.Claims
                    if FindSelfClaimConflicts({Signal: ClaimValues}):
                        ConflictSignals.add(Signal)
                        continue
                    BlockingSignals = tuple(
                        OtherSignal
                        for OtherSignal, OtherClaims in ClaimsBySignal.items()
                        if (
                            OtherSignal != Signal
                            and _PlacementAccessClaimsConflict(
                                ClaimValues,
                                OtherClaims,
                            )
                        )
                    )
                    if BlockingSignals:
                        ConflictSignals.update((Signal, *BlockingSignals))
                        continue
                    ClaimsBySignal[Signal] = ClaimValues
                    LocalClaimChoice[Signal] = True
                    SelectedLocalRouteSignals.add(Signal)
                    if Search():
                        return True
                    SelectedLocalRouteSignals.remove(Signal)
                    LocalClaimChoice.pop(Signal, None)
                    ClaimsBySignal.pop(Signal, None)
                    continue
                LocalClaimChoice[Signal] = False
                if Search():
                    return True
                LocalClaimChoice.pop(Signal, None)
            return False
        AllTerminalDomainsResolved = all(
            DomainIndex in Selected
            or LocalClaimChoice.get(Domain.Signal) is True
            for DomainIndex, Domain in enumerate(Fabric.TerminalDomains)
        )
        if AllTerminalDomainsResolved:
            LocallyComplete = (
                all(
                    not SignalRequiresCompleteRoute(Signal)
                    or LocalClaimChoice.get(Signal) is True
                    or Signal in SelectedSignalRoutes
                    for Signal in TerminalDomainCountBySignal
                )
            )
            if not LocallyComplete:
                return False
            if AssignmentValidator is None:
                return True
            if AssignmentValidator(BuildCurrentAssignment()):
                return True
            RejectedCompleteAssignmentCount += 1
            return False
        RankedDomains = []
        for DomainIndex, Domain in enumerate(Fabric.TerminalDomains):
            if (
                DomainIndex in Selected
                or LocalClaimChoice.get(Domain.Signal) is True
            ):
                continue
            Compatible = CompatibleStubs(DomainIndex)
            if not Compatible:
                if not FirstUnroutableSignal:
                    FirstUnroutableSignal = Domain.Signal
                ConflictSignals.add(Domain.Signal)
                return False
            RankedDomains.append((
                0 if Domain.Signal in ClaimsBySignal else 1,
                len(Compatible),
                Domain.Signal,
                Domain.Terminal,
                DomainIndex,
                Compatible,
            ))
        (
            _PartiallySelectedRank,
            _CompatibleCount,
            _Signal,
            _Terminal,
            DomainIndex,
            Compatible,
        ) = min(RankedDomains)
        Domain = Fabric.TerminalDomains[DomainIndex]
        ExistingSignalClaims = ClaimsBySignal.get(
            Domain.Signal,
            RoutingResourceClaims(),
        )
        for StubIndex, MergedClaims in Compatible:
            if ExpansionCount >= MaximumExpansions:
                Exhausted = True
                return False
            ExpansionCount += 1
            Selected[DomainIndex] = StubIndex
            ClaimsBySignal[Domain.Signal] = MergedClaims
            SelectedSignalTerminalCount = sum(
                Fabric.TerminalDomains[Index].Signal == Domain.Signal
                for Index in Selected
            )
            if (
                SignalRequiresCompleteRoute(Domain.Signal)
                and
                SelectedSignalTerminalCount
                == TerminalDomainCountBySignal[Domain.Signal]
            ):
                Ingresses = tuple(sorted({
                    Fabric.TerminalDomains[Index]
                    .EscapeStubs[Selected[Index]].Ingress
                    for Index in Selected
                    if Fabric.TerminalDomains[Index].Signal == Domain.Signal
                }))
                RouteCandidates = BuildSignalRouteCandidates(
                    Domain.Signal,
                    Ingresses,
                )
                RoutedCurrentSignal = False
                for RouteNodes, RouteClaims in RouteCandidates:
                    if ExpansionCount >= MaximumExpansions:
                        Exhausted = True
                        break
                    CompleteClaims = _MergePlacementAccessClaims(
                        MergedClaims,
                        RouteClaims,
                    )
                    if FindSelfClaimConflicts({
                        Domain.Signal: CompleteClaims
                    }):
                        continue
                    BlockingSignals = tuple(
                        OtherSignal
                        for OtherSignal, OtherClaims
                        in ClaimsBySignal.items()
                        if (
                            OtherSignal != Domain.Signal
                            and _PlacementAccessClaimsConflict(
                                CompleteClaims,
                                OtherClaims,
                            )
                        )
                    )
                    if BlockingSignals:
                        ConflictSignals.update((
                            Domain.Signal,
                            *BlockingSignals,
                        ))
                        continue
                    ExpansionCount += 1
                    RoutedCurrentSignal = True
                    ClaimsBySignal[Domain.Signal] = CompleteClaims
                    SelectedSignalRoutes[Domain.Signal] = RouteNodes
                    if Search():
                        return True
                    SelectedSignalRoutes.pop(Domain.Signal, None)
                    ClaimsBySignal[Domain.Signal] = MergedClaims
                if not RoutedCurrentSignal and not FirstUnroutableSignal:
                    FirstUnroutableSignal = Domain.Signal
            elif Search():
                return True
            Selected.pop(DomainIndex, None)
            if ExistingSignalClaims.ResourceIds:
                ClaimsBySignal[Domain.Signal] = ExistingSignalClaims
            else:
                ClaimsBySignal.pop(Domain.Signal, None)
        return False

    Success = Search()
    SelectedValues = tuple(
        (
            Fabric.TerminalDomains[Index].Signal,
            Fabric.TerminalDomains[Index].Terminal,
            Selected[Index],
        )
        for Index in sorted(Selected)
    ) if Success else ()
    CapacityResourceIds = tuple(sorted({
        Resource
        for Signal, SignalClaims in ClaimsBySignal.items()
        for Resource in _MergePlacementAccessClaims(
            SignalClaims,
            (
                RoutingResourceClaims(
                    WireCells=frozenset(SelectedSignalRoutes.get(Signal, ())),
                    SupportCells=frozenset(
                        (X, Y - 1, Z)
                        for X, Y, Z in SelectedSignalRoutes.get(Signal, ())
                    ),
                    RequiredAirCells=frozenset(),
                    ElectricalCells=frozenset(
                        Position
                        for Node in SelectedSignalRoutes.get(Signal, ())
                        for Position in (
                            Node,
                            *EffectiveTechnology.NeighborPositions(Node),
                        )
                    ),
                )
            ),
        ).ResourceIds
    }, key=str)) if Success else ()
    AssignmentFingerprint = (
        sha256(repr((
            Fabric.FabricFingerprint,
            SelectedValues,
            tuple(sorted(SelectedLocalRouteSignals)),
            tuple(sorted(SelectedSignalRoutes.items())),
            CapacityResourceIds,
        )).encode("utf-8")).hexdigest()[:16]
        if Success
        else ""
    )
    return PlacementAccessAssignment(
        FabricFingerprint=Fabric.FabricFingerprint,
        AssignmentFingerprint=AssignmentFingerprint,
        SelectedStubIndices=SelectedValues,
        CapacityResourceIds=CapacityResourceIds,
        ExpansionCount=ExpansionCount,
        Success=Success,
        Complete=not Exhausted and not IncompleteRouteDomain,
        ConflictSignals=(() if Success else tuple(sorted(ConflictSignals))),
        FrontierSignals=(() if Success else FrontierSignals),
        MaximumRoutedSignalCount=MaximumRoutedSignalCount,
        FirstUnroutableSignal=(
            ""
            if Success
            else FirstUnroutableSignal or FirstIncompleteRouteSignal
        ),
        IncompleteReason=(
            "work-cap-exhausted"
            if Exhausted
            else "incomplete-derived-perimeter-route-domain"
            if IncompleteRouteDomain
            else ""
        ),
        SignalRoutes=tuple(sorted(SelectedSignalRoutes.items())) if Success else (),
        SelectedLocalRouteSignals=(
            tuple(sorted(SelectedLocalRouteSignals)) if Success else ()
        ),
    )
