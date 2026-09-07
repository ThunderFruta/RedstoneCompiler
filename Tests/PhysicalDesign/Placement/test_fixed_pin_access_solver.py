from dataclasses import replace
from itertools import product

import pytest

from PhysicalDesign.Contracts.PlacementAccess import (
    PlacedPinAccessOption,
    PlacedPinAccessOptionDomain,
    PlacementAccessSolveStatus,
)
from PhysicalDesign.Placement.Access.Capacity import FixedPlacementPinAccessDomain, FixedPlacementPinAccessStatus, ReplayFixedPlacementPinAccessUnsatisfiableCore, ReplayPlacedPinAccessConflictCore, SolveFixedPlacementPinAccessDomains, SolvePlacedPinAccessOptionDomains
from PhysicalDesign.Geometry.Placement import PlacementPinAccessSelection
from PhysicalDesign.Resources.ResourceGraph import FindClaimConflicts, RoutingReservation, RoutingResourceClaims, RoutingResourceGraph, RoutingResourceId, RoutingResourceKind


def BuildSelection(
    Signal: str,
    DomainId: str,
    OptionId: str,
    TerminalX: int,
    DirectionX: int,
) -> PlacementPinAccessSelection:
    Terminal = (TerminalX, 1, 0)
    return PlacementPinAccessSelection(
        Signal=Signal,
        GateName=f"Gate{DomainId}",
        GateKind="NAND",
        Role="Source",
        PinId=DomainId,
        PatternId=OptionId,
        Terminal=Terminal,
        ApproachDirection=(DirectionX, 0, 0),
        Path=tuple(
            (TerminalX + DirectionX * Offset, 1, 0)
            for Offset in range(3)
        ),
        CatalogAccessLength=3,
        CatalogMatched=True,
    )


def BuildDomain(
    DomainId: str,
    TerminalX: int,
    Directions: tuple[int, ...],
    *,
    Complete: bool = True,
    IncompleteReason: str = "",
) -> FixedPlacementPinAccessDomain:
    Signal = f"Signal{DomainId}"
    return FixedPlacementPinAccessDomain(
        DomainId=DomainId,
        Signal=Signal,
        Terminal=(TerminalX, 1, 0),
        Options=tuple(
            BuildSelection(
                Signal,
                DomainId,
                f"Option{Index}",
                TerminalX,
                Direction,
            )
            for Index, Direction in enumerate(Directions)
        ),
        Complete=Complete,
        IncompleteReason=IncompleteReason,
    )


def BuildDomainWithSelections(
    DomainId: str,
    Selections: tuple[PlacementPinAccessSelection, ...],
) -> FixedPlacementPinAccessDomain:
    return FixedPlacementPinAccessDomain(
        DomainId=DomainId,
        Signal=Selections[0].Signal,
        Terminal=Selections[0].Terminal,
        Options=Selections,
        Complete=True,
    )


def BruteForceFeasible(
    Domains: tuple[FixedPlacementPinAccessDomain, ...],
    ResourceGraph: RoutingResourceGraph,
) -> bool:
    for Options in product(*(Domain.Options for Domain in Domains)):
        Claims = {
            Domain.DomainId: ResourceGraph.BuildRouteClaims(Option.Path)
            for Domain, Option in zip(Domains, Options, strict=True)
        }
        if not FindClaimConflicts(Claims):
            return True
    return False


def BuildExactOption(
    DomainId: str,
    Nodes: tuple[tuple[int, int, int], ...],
    FirstTrackNode: tuple[int, int, int],
    *,
    PatternFamily: str = "straight",
    RepeaterIndex: int = 1,
    RepeaterFacing: str = "west",
    Claims: RoutingResourceClaims | None = None,
    CatalogVersion: str = "catalog-v1",
    TechnologyFingerprint: str = "technology-v1",
    ResourceModelFingerprint: str = "resources-v1",
) -> PlacedPinAccessOption:
    Signal = f"Signal{DomainId}"
    RepeaterPosition = Nodes[RepeaterIndex]
    ExactClaims = Claims or RoutingResourceClaims(
        WireCells=frozenset(Nodes),
    )
    return PlacedPinAccessOption(
        Signal=Signal,
        GateName=f"Gate{DomainId}",
        GateKind="NAND",
        Role="Source",
        PinId="Output0",
        CatalogVersion=CatalogVersion,
        TemplateId=f"NAND:{DomainId}:{PatternFamily}",
        PatternFamily=PatternFamily,
        TemplateFingerprint=f"template:{DomainId}:{PatternFamily}",
        TemplateProofFingerprint=f"proof:{DomainId}:{PatternFamily}",
        TechnologyFingerprint=TechnologyFingerprint,
        ResourceModelFingerprint=ResourceModelFingerprint,
        Terminal=Nodes[0],
        Face=(
            Nodes[1][0] - Nodes[0][0],
            0,
            Nodes[1][2] - Nodes[0][2],
        ),
        Layer=0,
        FirstLegNodes=Nodes,
        FirstTrackNode=FirstTrackNode,
        BlockRoles=tuple(
            (
                Position,
                "repeater" if Index == RepeaterIndex else "dust",
            )
            for Index, Position in enumerate(Nodes)
        ),
        Claims=ExactClaims,
        RepeaterReservations=(RoutingReservation(
            Signal=Signal,
            Resource=RoutingResourceId(
                RoutingResourceKind.Wire,
                RepeaterPosition,
            ),
            Position=RepeaterPosition,
            Purpose="PinAccessRepeater",
            InputFacing=RepeaterFacing,
        ),),
    )


def BuildExactDomain(
    DomainId: str,
    Options: tuple[PlacedPinAccessOption, ...],
    *,
    Complete: bool = True,
    IncompleteReason: str = "",
    CatalogVersion: str = "catalog-v1",
    TechnologyFingerprint: str = "technology-v1",
    ResourceModelFingerprint: str = "resources-v1",
) -> PlacedPinAccessOptionDomain:
    OrderedOptions = tuple(sorted(
        Options,
        key=lambda Value: Value.RankKey(),
    ))
    Terminal = OrderedOptions[0].Terminal if OrderedOptions else (0, 1, 0)
    return PlacedPinAccessOptionDomain(
        DomainId=DomainId,
        Signal=f"Signal{DomainId}",
        GateName=f"Gate{DomainId}",
        Role="Source",
        PinId="Output0",
        Terminal=Terminal,
        Options=OrderedOptions,
        Complete=Complete,
        IncompleteReason=IncompleteReason,
        CatalogVersion=CatalogVersion,
        TechnologyFingerprint=TechnologyFingerprint,
        ResourceModelFingerprint=ResourceModelFingerprint,
        GeneratedOptionCount=len(OrderedOptions),
        RejectedOptionCount=0,
        DeduplicatedOptionCount=0,
        MaximumGenerationWork=100,
    )


def testFixedPlacementPinAccessSolverReturnsStableFeasibleAssignment() -> None:
    Domains = (
        BuildDomain("Alpha", 0, (1, -1)),
        BuildDomain("Beta", 4, (-1, 1)),
    )

    First = SolveFixedPlacementPinAccessDomains(Domains)
    Second = SolveFixedPlacementPinAccessDomains(reversed(Domains))

    assert First.Status is FixedPlacementPinAccessStatus.Feasible
    assert First.Complete
    assert First.Success
    assert First.AssignmentFingerprint == Second.AssignmentFingerprint
    assert First.SelectedOptionFingerprints == Second.SelectedOptionFingerprints
    assert len(First.SelectedOptionFingerprints) == 2


def testFixedPlacementPinAccessSolverPublishesReplayableUnsatCore() -> None:
    Alpha = BuildSelection("SignalAlpha", "Alpha", "Only", 0, 1)
    Beta = BuildSelection("SignalBeta", "Beta", "Only", 4, -1)
    Domains = (
        BuildDomainWithSelections("Alpha", (Alpha,)),
        BuildDomainWithSelections("Beta", (Beta,)),
    )

    Result = SolveFixedPlacementPinAccessDomains(Domains)

    assert Result.Status is FixedPlacementPinAccessStatus.Unsatisfiable
    assert Result.Complete
    assert not Result.Success
    assert Result.UnsatisfiableCore is not None
    assert Result.UnsatisfiableCore.Signals == (
        "SignalAlpha",
        "SignalBeta",
    )
    assert len(Result.UnsatisfiableCore.Domains) == 2
    assert Result.UnsatisfiableCore.Conflicts
    Replayed = ReplayFixedPlacementPinAccessUnsatisfiableCore(
        Result.UnsatisfiableCore
    )
    assert Replayed.Status is FixedPlacementPinAccessStatus.Unsatisfiable
    assert Replayed.UnsatisfiableCore is not None
    assert (
        Replayed.UnsatisfiableCore.CoreFingerprint
        == Result.UnsatisfiableCore.CoreFingerprint
    )


def testFixedPlacementPinAccessSolverPreservesIncompleteDomainStatus() -> None:
    Domain = BuildDomain(
        "Alpha",
        0,
        (1,),
        Complete=False,
        IncompleteReason="catalog-domain-truncated",
    )

    Result = SolveFixedPlacementPinAccessDomains((Domain,))

    assert Result.Status is FixedPlacementPinAccessStatus.Incomplete
    assert not Result.Complete
    assert not Result.Success
    assert Result.IncompleteReason == "catalog-domain-truncated"
    assert Result.UnsatisfiableCore is None


def testFixedPlacementPinAccessSolverReportsWorkCapAsIncomplete() -> None:
    Domains = (
        BuildDomain("Alpha", 0, (1, -1)),
        BuildDomain("Beta", 4, (-1, 1)),
    )

    Result = SolveFixedPlacementPinAccessDomains(
        Domains,
        MaximumExpansions=1,
    )

    assert Result.Status is FixedPlacementPinAccessStatus.Incomplete
    assert Result.IncompleteReason == "assignment-work-cap"
    assert Result.ExpansionCount == 1
    assert Result.UnsatisfiableCore is None


def testFixedPlacementPinAccessSolverMatchesExhaustiveOracle() -> None:
    ResourceGraph = RoutingResourceGraph(
        ActualBlocks=frozenset(),
        ElectricalBlocks=frozenset(),
        SolidBlocks=frozenset(),
    )
    BaseSelections = {
        "Alpha": (
            BuildSelection("SignalAlpha", "Alpha", "Right", 0, 1),
            BuildSelection("SignalAlpha", "Alpha", "Left", 0, -1),
        ),
        "Beta": (
            BuildSelection("SignalBeta", "Beta", "Left", 4, -1),
            BuildSelection("SignalBeta", "Beta", "Right", 4, 1),
        ),
        "Gamma": (
            BuildSelection("SignalGamma", "Gamma", "Left", 8, -1),
            BuildSelection("SignalGamma", "Gamma", "Right", 8, 1),
        ),
    }
    CaseCount = 0
    FeasibleCount = 0
    UnsatisfiableCount = 0
    for Masks in product((1, 2, 3), repeat=3):
        Domains = tuple(
            BuildDomainWithSelections(
                DomainId,
                tuple(
                    Option
                    for OptionIndex, Option in enumerate(
                        BaseSelections[DomainId]
                    )
                    if Mask & (1 << OptionIndex)
                ),
            )
            for DomainId, Mask in zip(
                ("Alpha", "Beta", "Gamma"),
                Masks,
                strict=True,
            )
        )
        ExpectedFeasible = BruteForceFeasible(Domains, ResourceGraph)
        Result = SolveFixedPlacementPinAccessDomains(
            Domains,
            ResourceGraph=ResourceGraph,
            MaximumExpansions=1_000,
        )
        assert Result.Complete
        assert Result.Success is ExpectedFeasible
        CaseCount += 1
        FeasibleCount += int(ExpectedFeasible)
        UnsatisfiableCount += int(not ExpectedFeasible)

    assert CaseCount == 27
    assert FeasibleCount > 0
    assert UnsatisfiableCount > 0


def testExactPinAccessSolverConsumesPrecompiledClaims() -> None:
    AlphaNodes = ((0, 1, 0), (1, 1, 0), (2, 1, 0))
    BetaNodes = ((10, 1, 0), (11, 1, 0), (12, 1, 0))
    Alpha = BuildExactOption(
        "Alpha",
        AlphaNodes,
        (3, 1, 0),
        Claims=RoutingResourceClaims(
            WireCells=frozenset(AlphaNodes),
            RequiredAirCells=frozenset({BetaNodes[0]}),
        ),
    )
    Beta = BuildExactOption("Beta", BetaNodes, (13, 1, 0))

    Result = SolvePlacedPinAccessOptionDomains((
        BuildExactDomain("Alpha", (Alpha,)),
        BuildExactDomain("Beta", (Beta,)),
    ))

    assert Result.Status is PlacementAccessSolveStatus.Unsatisfiable
    assert Result.SearchComplete
    assert not Result.OptimalityProven
    assert Result.ConflictCore is not None
    assert "Air:10,1,0" in Result.ConflictCore.BlockingResources

    Replayed = ReplayPlacedPinAccessConflictCore(
        (
            BuildExactDomain("Alpha", (Alpha,)),
            BuildExactDomain("Beta", (Beta,)),
        ),
        Result.ConflictCore,
    )
    assert Replayed == Result


@pytest.mark.parametrize(
    "AlphaRepeaterIndex, BetaFacing, ExpectedPrefix",
    (
        (0, "north", "BlockRole:1,1,0:"),
        (1, "north", "RepeaterFacing:1,1,0:"),
    ),
)
def testExactPinAccessCompatibilityIncludesBlockStateConflicts(
    AlphaRepeaterIndex: int,
    BetaFacing: str,
    ExpectedPrefix: str,
) -> None:
    Alpha = BuildExactOption(
        "Alpha",
        ((0, 1, 0), (1, 1, 0), (2, 1, 0)),
        (3, 1, 0),
        RepeaterIndex=AlphaRepeaterIndex,
        RepeaterFacing="west",
    )
    Beta = BuildExactOption(
        "Beta",
        ((1, 1, -1), (1, 1, 0), (1, 1, 1)),
        (1, 1, 2),
        RepeaterIndex=1,
        RepeaterFacing=BetaFacing,
    )

    Result = SolvePlacedPinAccessOptionDomains((
        BuildExactDomain("Alpha", (Alpha,)),
        BuildExactDomain("Beta", (Beta,)),
    ))

    assert Result.Status is PlacementAccessSolveStatus.Unsatisfiable
    assert Result.ConflictCore is not None
    assert any(
        Value.startswith(ExpectedPrefix)
        for Value in Result.ConflictCore.BlockingResources
    )


def testExactPinAccessAdapterPrefersStraightAndFreezesWitness() -> None:
    Straight = BuildExactOption(
        "Alpha",
        ((0, 1, 0), (1, 1, 0), (2, 1, 0)),
        (3, 1, 0),
    )
    Dogleg = BuildExactOption(
        "Alpha",
        ((0, 1, 0), (1, 1, 0), (1, 1, 1)),
        (1, 1, 2),
        PatternFamily="planar-jog",
        RepeaterIndex=0,
    )
    Domain = BuildExactDomain("Alpha", (Dogleg, Straight))

    Result = SolvePlacedPinAccessOptionDomains((Domain,))

    assert Result.Status is PlacementAccessSolveStatus.Feasible
    assert Result.SearchComplete
    assert not Result.OptimalityProven
    assert not Result.IncompleteReason
    assert Result.SelectedWitness is not None
    assert Result.SelectedWitness.Selections == (Straight,)
    assert Result.SelectedWitness.DomainFingerprints == (
        Domain.DomainFingerprint,
    )
    assert Result.SelectedWitness.ClaimsBySignal == (
        (Straight.Signal, Straight.Claims),
    )
    assert Result.SelectedWitness.RepeaterReservations == (
        Straight.RepeaterReservations[0],
    )


@pytest.mark.parametrize(
    "ChangedField, ChangedValue",
    (
        ("CatalogVersion", "catalog-v2"),
        ("TechnologyFingerprint", "technology-v2"),
        ("ResourceModelFingerprint", "resources-v2"),
    ),
)
def testExactPinAccessProblemIdentityIncludesEveryDependency(
    ChangedField: str,
    ChangedValue: str,
) -> None:
    BaseOption = BuildExactOption(
        "Alpha",
        ((0, 1, 0), (1, 1, 0), (2, 1, 0)),
        (3, 1, 0),
    )
    BaseDomain = BuildExactDomain("Alpha", (BaseOption,))
    ChangedOption = replace(BaseOption, **{ChangedField: ChangedValue})
    ChangedDomain = BuildExactDomain(
        "Alpha",
        (ChangedOption,),
        **{ChangedField: ChangedValue},
    )

    Base = SolvePlacedPinAccessOptionDomains((BaseDomain,))
    Changed = SolvePlacedPinAccessOptionDomains((ChangedDomain,))

    assert Base.ProblemFingerprint != Changed.ProblemFingerprint


def testExactPinAccessAdapterPreservesIncompleteWithoutAFalseCore() -> None:
    Domain = BuildExactDomain(
        "Alpha",
        (),
        Complete=False,
        IncompleteReason="catalog-domain-generation-work-cap",
    )

    Result = SolvePlacedPinAccessOptionDomains((Domain,))

    assert Result.Status is PlacementAccessSolveStatus.Incomplete
    assert not Result.SearchComplete
    assert not Result.OptimalityProven
    assert Result.IncompleteReason == "catalog-domain-generation-work-cap"
    assert Result.SelectedWitness is None
    assert Result.ConflictCore is None


def testExactPinAccessAdapterMapsAssignmentCapToIncomplete() -> None:
    Alpha = BuildExactOption(
        "Alpha",
        ((0, 1, 0), (1, 1, 0), (2, 1, 0)),
        (3, 1, 0),
    )
    Beta = BuildExactOption(
        "Beta",
        ((10, 1, 0), (11, 1, 0), (12, 1, 0)),
        (13, 1, 0),
    )

    Result = SolvePlacedPinAccessOptionDomains(
        (
            BuildExactDomain("Alpha", (Alpha,)),
            BuildExactDomain("Beta", (Beta,)),
        ),
        MaximumExpansions=1,
    )

    assert Result.Status is PlacementAccessSolveStatus.Incomplete
    assert Result.IncompleteReason == "assignment-work-cap"
    assert Result.ExpansionCount == 1
    assert Result.ConflictCore is None
