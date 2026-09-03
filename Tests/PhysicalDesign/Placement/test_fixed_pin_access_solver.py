from itertools import product

from PhysicalDesign.Placement.Access.Capacity import FixedPlacementPinAccessDomain, FixedPlacementPinAccessStatus, ReplayFixedPlacementPinAccessUnsatisfiableCore, SolveFixedPlacementPinAccessDomains
from PhysicalDesign.Geometry.Placement import PlacementPinAccessSelection
from PhysicalDesign.Resources.ResourceGraph import FindClaimConflicts, RoutingResourceGraph


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
