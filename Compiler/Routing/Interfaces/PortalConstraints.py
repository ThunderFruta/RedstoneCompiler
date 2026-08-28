"""Exact finite-domain portal-factor extraction and projection.

This module is deliberately below component and authoritative routing.  It
operates only on neutral resource-graph primitives, so either routing layer
can compile or consume an exact portal relation without importing the other.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations, product
from typing import Any, Callable, Iterable, Mapping

from ..Contracts.Core import Position3
from ..Reliability import BuildStableFingerprint
from ..ResourceGraph import (
    FindClaimConflicts,
    FindSelfClaimConflicts,
    LocalRouteClaim,
    RoutingResourceGraph,
)


@dataclass(frozen=True)
class ExactPortalConstraintChoice:
    """One discrete portal choice represented by its complete wire nodes."""

    ChoiceId: str
    Nodes: frozenset[Position3]


@dataclass(frozen=True)
class ExactPortalConstraintVariableDomain:
    """One signal-owned portal variable in an exact resource factor graph."""

    Variable: str
    Signal: str
    Choices: tuple[ExactPortalConstraintChoice, ...]


@dataclass(frozen=True)
class ExactPortalConstraintForbiddenTuple:
    """One inclusion-minimal illegal unary, binary, or ternary assignment."""

    Assignments: tuple[tuple[str, str], ...]
    ConflictPositions: frozenset[Position3] = frozenset()
    DependencySignals: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExactPortalConstraintFactorExtraction:
    """Reference factorization certificate derived from full route claims."""

    DomainFingerprint: str
    Complete: bool
    Variables: tuple[str, ...]
    ForbiddenTuples: tuple[ExactPortalConstraintForbiddenTuple, ...]
    EvaluatedPartialAssignmentCount: int
    EvaluatedFullAssignmentCount: int
    MaximumForbiddenTupleArity: int


@dataclass(frozen=True)
class ExactPortalConstraintProjection:
    """Exact supported relation retained after eliminating portal variables."""

    ProjectionFingerprint: str
    Complete: bool
    RetainedVariables: tuple[str, str]
    SupportedChoicePairs: tuple[tuple[str, str], ...]
    Witnesses: tuple[
        tuple[tuple[str, str], tuple[tuple[str, str], ...]], ...
    ]
    ExpansionCount: int = 0
    FailedStateMemoHitCount: int = 0


def ProjectExactPortalConstraintFactors(
    Extraction: ExactPortalConstraintFactorExtraction,
    VariableDomains: Iterable[ExactPortalConstraintVariableDomain],
    RetainedVariables: tuple[str, str],
    ShouldStop: Callable[[], bool] | None = None,
) -> ExactPortalConstraintProjection:
    """Eliminate internal variables with exact forbidden-tuple propagation."""
    Domains = tuple(sorted(
        VariableDomains,
        key=lambda Value: Value.Variable,
    ))
    DomainChoices = {
        Domain.Variable: tuple(sorted(
            (Choice.ChoiceId for Choice in Domain.Choices),
        ))
        for Domain in Domains
    }
    Retained = tuple(map(str, RetainedVariables))
    if (
        not Extraction.Complete
        or len(Retained) != 2
        or Retained[0] == Retained[1]
        or any(Variable not in DomainChoices for Variable in Retained)
    ):
        return ExactPortalConstraintProjection(
            ProjectionFingerprint=BuildStableFingerprint((
                "exact-portal-constraint-projection-incomplete-v1",
                Extraction.DomainFingerprint,
                Retained,
            )),
            Complete=False,
            RetainedVariables=Retained,
            SupportedChoicePairs=(),
            Witnesses=(),
        )
    ForbiddenAssignments = tuple(
        dict(Value.Assignments) for Value in Extraction.ForbiddenTuples
    )
    InternalVariables = tuple(
        Variable for Variable in DomainChoices
        if Variable not in Retained
    )
    Supported = []
    Witnesses = []
    ExpansionCount = 0
    FailedStateMemoHitCount = 0
    Incomplete = False

    def Solve(
        RetainedAssignment: dict[str, str],
    ) -> dict[str, str] | None:
        nonlocal ExpansionCount, FailedStateMemoHitCount, Incomplete
        FailedStates = set()

        def Search(
            Assignment: dict[str, str],
            RemainingDomains: dict[str, frozenset[str]],
        ) -> dict[str, str] | None:
            nonlocal ExpansionCount, FailedStateMemoHitCount, Incomplete
            if ShouldStop is not None and ShouldStop():
                Incomplete = True
                return None
            MutableAssignment = dict(Assignment)
            MutableDomains = dict(RemainingDomains)
            Changed = True
            while Changed:
                Changed = False
                for Forbidden in ForbiddenAssignments:
                    if any(
                        Variable in MutableAssignment
                        and MutableAssignment[Variable] != Choice
                        for Variable, Choice in Forbidden.items()
                    ):
                        continue
                    Pending = tuple(
                        (Variable, Choice)
                        for Variable, Choice in Forbidden.items()
                        if Variable not in MutableAssignment
                    )
                    if not Pending:
                        return None
                    if len(Pending) != 1:
                        continue
                    Variable, Choice = Pending[0]
                    Domain = MutableDomains.get(Variable)
                    if Domain is None or Choice not in Domain:
                        continue
                    Reduced = Domain - frozenset((Choice,))
                    if not Reduced:
                        return None
                    MutableDomains[Variable] = Reduced
                    Changed = True
                SingletonVariables = tuple(sorted(
                    Variable for Variable, Domain in MutableDomains.items()
                    if len(Domain) == 1
                ))
                for Variable in SingletonVariables:
                    MutableAssignment[Variable] = next(iter(
                        MutableDomains.pop(Variable)
                    ))
                    Changed = True
            if not MutableDomains:
                return MutableAssignment
            StateKey = (
                tuple(sorted(MutableAssignment.items())),
                tuple(
                    (Variable, tuple(sorted(Domain)))
                    for Variable, Domain in sorted(MutableDomains.items())
                ),
            )
            if StateKey in FailedStates:
                FailedStateMemoHitCount += 1
                return None
            Variable = min(
                MutableDomains,
                key=lambda Value: (
                    len(MutableDomains[Value]),
                    Value,
                ),
            )
            Domain = MutableDomains.pop(Variable)
            for Choice in sorted(Domain):
                ExpansionCount += 1
                Result = Search(
                    {**MutableAssignment, Variable: Choice},
                    MutableDomains,
                )
                if Result is not None or Incomplete:
                    return Result
            FailedStates.add(StateKey)
            return None

        return Search(
            RetainedAssignment,
            {
                Variable: frozenset(DomainChoices[Variable])
                for Variable in InternalVariables
            },
        )

    for FirstChoice in DomainChoices[Retained[0]]:
        for SecondChoice in DomainChoices[Retained[1]]:
            Witness = Solve({
                Retained[0]: FirstChoice,
                Retained[1]: SecondChoice,
            })
            if Incomplete:
                break
            if Witness is not None:
                Pair = (FirstChoice, SecondChoice)
                Supported.append(Pair)
                Witnesses.append((Pair, tuple(sorted(Witness.items()))))
        if Incomplete:
            break
    ProjectionFingerprint = BuildStableFingerprint((
        "exact-portal-constraint-projection-v1",
        Extraction.DomainFingerprint,
        Retained,
        tuple(Supported),
        not Incomplete,
    ))
    return ExactPortalConstraintProjection(
        ProjectionFingerprint=ProjectionFingerprint,
        Complete=not Incomplete,
        RetainedVariables=Retained,
        SupportedChoicePairs=tuple(Supported) if not Incomplete else (),
        Witnesses=tuple(Witnesses) if not Incomplete else (),
        ExpansionCount=ExpansionCount,
        FailedStateMemoHitCount=FailedStateMemoHitCount,
    )


def ExactPortalConstraintAssignmentSatisfiesFactors(
    Assignment: Mapping[str, str],
    ForbiddenTuples: Iterable[ExactPortalConstraintForbiddenTuple],
) -> bool:
    """Test a discrete assignment against an extracted forbidden relation."""
    return not any(all(
        Assignment.get(Variable) == Choice
        for Variable, Choice in Factor.Assignments
    ) for Factor in ForbiddenTuples)


def ExtractExactPortalConstraintFactors(
    VariableDomains: Iterable[ExactPortalConstraintVariableDomain],
    ResourceGraph: Any,
    BaseNodesBySignal: Mapping[str, Iterable[Position3]] | None = None,
) -> ExactPortalConstraintFactorExtraction:
    """Extract exact minimal route-claim conflicts of arity at most three.

    This deliberately recomputes ``BuildRouteClaims`` over every selected
    node union.  Componentwise claim union is not equivalent: a primitive
    joining nodes owned by two different choices can introduce required-air
    cells, and a third choice can conflict with that newly created air.

    The implementation is a small reference compiler for tests and future
    tree-DP integration.  It certifies completeness by comparing the emitted
    factors with every full assignment; a domain containing a genuinely
    higher-order conflict returns ``Complete=False`` and must not be used for
    negative pruning.
    """
    Domains = tuple(sorted(
        VariableDomains,
        key=lambda Value: (str(Value.Variable), str(Value.Signal)),
    ))
    if not Domains:
        raise ValueError("exact portal factor extraction has no variables")
    if len({Value.Variable for Value in Domains}) != len(Domains):
        raise ValueError("exact portal factor variables are not unique")
    for Domain in Domains:
        if not Domain.Choices:
            raise ValueError(
                f"exact portal factor variable {Domain.Variable} is empty"
            )
        ChoiceIds = tuple(Value.ChoiceId for Value in Domain.Choices)
        if len(set(ChoiceIds)) != len(ChoiceIds):
            raise ValueError(
                f"exact portal factor choices for {Domain.Variable} "
                "are not unique"
            )
    BaseNodes = {
        str(Signal): frozenset(Nodes)
        for Signal, Nodes in (BaseNodesBySignal or {}).items()
    }
    DomainByVariable = {Value.Variable: Value for Value in Domains}

    def Evaluate(
        Assignments: tuple[tuple[str, str], ...],
    ) -> tuple[bool, frozenset[Position3]]:
        NodesBySignal = dict(BaseNodes)
        for Variable, ChoiceId in Assignments:
            Domain = DomainByVariable[Variable]
            Choice = next(
                Value for Value in Domain.Choices
                if Value.ChoiceId == ChoiceId
            )
            NodesBySignal[Domain.Signal] = frozenset((
                *NodesBySignal.get(Domain.Signal, frozenset()),
                *Choice.Nodes,
            ))
        ClaimsBySignal = {
            Signal: ResourceGraph.BuildRouteClaims(Nodes)
            for Signal, Nodes in NodesBySignal.items()
        }
        SelfConflicts = FindSelfClaimConflicts(ClaimsBySignal)
        ForeignConflicts = FindClaimConflicts(ClaimsBySignal)
        ConflictPositions = frozenset(
            Resource.Position
            for Resource in (*SelfConflicts, *ForeignConflicts)
        )
        return not SelfConflicts and not ForeignConflicts, ConflictPositions

    BaseLegal, _BaseConflictPositions = Evaluate(())
    if not BaseLegal:
        raise ValueError("exact portal factor base claims are already illegal")
    Factors = []
    FactorAssignmentSets: list[frozenset[tuple[str, str]]] = []
    EvaluatedPartialAssignmentCount = 0
    for Arity in range(1, min(3, len(Domains)) + 1):
        for SelectedDomains in combinations(Domains, Arity):
            for Choices in product(*(
                tuple(sorted(
                    Domain.Choices,
                    key=lambda Value: Value.ChoiceId,
                ))
                for Domain in SelectedDomains
            )):
                Assignments = tuple(sorted(
                    (
                        Domain.Variable,
                        Choice.ChoiceId,
                    )
                    for Domain, Choice in zip(SelectedDomains, Choices)
                ))
                AssignmentSet = frozenset(Assignments)
                if any(
                    Existing <= AssignmentSet
                    for Existing in FactorAssignmentSets
                ):
                    continue
                EvaluatedPartialAssignmentCount += 1
                Legal, ConflictPositions = Evaluate(Assignments)
                if Legal:
                    continue
                Factors.append(ExactPortalConstraintForbiddenTuple(
                    Assignments=Assignments,
                    ConflictPositions=ConflictPositions,
                ))
                FactorAssignmentSets.append(AssignmentSet)
    Factors = sorted(
        Factors,
        key=lambda Value: (len(Value.Assignments), Value.Assignments),
    )
    Complete = True
    EvaluatedFullAssignmentCount = 0
    for Choices in product(*(
        tuple(sorted(
            Domain.Choices,
            key=lambda Value: Value.ChoiceId,
        ))
        for Domain in Domains
    )):
        Assignment = {
            Domain.Variable: Choice.ChoiceId
            for Domain, Choice in zip(Domains, Choices)
        }
        OrderedAssignment = tuple(sorted(Assignment.items()))
        ExactLegal, _ConflictPositions = Evaluate(OrderedAssignment)
        FactorLegal = ExactPortalConstraintAssignmentSatisfiesFactors(
            Assignment,
            Factors,
        )
        EvaluatedFullAssignmentCount += 1
        if ExactLegal != FactorLegal:
            Complete = False
    DomainFingerprint = BuildStableFingerprint((
        "exact-portal-constraint-factors-v1",
        tuple(sorted(
            (Signal, tuple(sorted(Nodes)))
            for Signal, Nodes in BaseNodes.items()
        )),
        tuple(
            (
                Domain.Variable,
                Domain.Signal,
                tuple(
                    (Choice.ChoiceId, tuple(sorted(Choice.Nodes)))
                    for Choice in sorted(
                        Domain.Choices,
                        key=lambda Value: Value.ChoiceId,
                    )
                ),
            )
            for Domain in Domains
        ),
        tuple(
            (Value.Assignments, tuple(sorted(Value.ConflictPositions)))
            for Value in Factors
        ),
        Complete,
    ))
    return ExactPortalConstraintFactorExtraction(
        DomainFingerprint=DomainFingerprint,
        Complete=Complete,
        Variables=tuple(Value.Variable for Value in Domains),
        ForbiddenTuples=tuple(Factors),
        EvaluatedPartialAssignmentCount=EvaluatedPartialAssignmentCount,
        EvaluatedFullAssignmentCount=EvaluatedFullAssignmentCount,
        MaximumForbiddenTupleArity=max(
            (len(Value.Assignments) for Value in Factors),
            default=0,
        ),
    )


def ExtractSparseExactPortalConstraintFactors(
    VariableDomains: Iterable[ExactPortalConstraintVariableDomain],
    ResourceGraph: RoutingResourceGraph,
    BaseNodesBySignal: Mapping[str, Iterable[Position3]] | None = None,
    FrozenComponentClaims: Iterable[LocalRouteClaim] = (),
) -> ExactPortalConstraintFactorExtraction:
    """Analytically extract the exact sparse arity-three claim relation.

    Wire, support, and electrical ownership is unary.  Required-air ownership
    is activated by a legal primitive edge and therefore has at most two
    endpoint owners.  Every routing conflict compares two such resources, one
    side of which is always unary, so every minimal forbidden assignment has
    arity at most three.  This production form indexes those conditional
    resource owners directly and never enumerates the full assignment domain.
    """
    if not isinstance(ResourceGraph, RoutingResourceGraph):
        raise TypeError(
            "sparse exact portal factors require RoutingResourceGraph"
        )
    Domains = tuple(sorted(
        VariableDomains,
        key=lambda Value: (str(Value.Variable), str(Value.Signal)),
    ))
    if not Domains:
        raise ValueError("sparse exact portal factor extraction has no variables")
    if len({Value.Variable for Value in Domains}) != len(Domains):
        raise ValueError("sparse exact portal factor variables are not unique")
    for Domain in Domains:
        if not Domain.Choices:
            raise ValueError(
                f"sparse exact portal factor variable {Domain.Variable} is empty"
            )
        if len({Value.ChoiceId for Value in Domain.Choices}) != len(
            Domain.Choices
        ):
            raise ValueError(
                f"sparse exact portal factor choices for {Domain.Variable} "
                "are not unique"
            )
    BaseNodes = {
        str(Signal): frozenset(Nodes)
        for Signal, Nodes in (BaseNodesBySignal or {}).items()
    }
    Assignment = tuple[str, str]
    Term = frozenset[Assignment]
    ResourceTerms: dict[
        tuple[str, str, Position3], set[Term]
    ] = defaultdict(set)
    NodeTerms: dict[
        str, dict[Position3, set[Term]]
    ] = defaultdict(lambda: defaultdict(set))

    def TermsAreCompatible(Values: Iterable[Assignment]) -> bool:
        Selected = {}
        for Variable, Choice in Values:
            Prior = Selected.get(Variable)
            if Prior is not None and Prior != Choice:
                return False
            Selected[Variable] = Choice
        return True

    def AddUnaryClaims(
        Signal: str,
        Nodes: Iterable[Position3],
        Owner: Term,
    ) -> None:
        for Node in frozenset(Nodes):
            NodeTerms[Signal][Node].add(Owner)
            ResourceTerms[(Signal, "Wire", Node)].add(Owner)
            ResourceTerms[(
                Signal,
                "Support",
                (Node[0], Node[1] - 1, Node[2]),
            )].add(Owner)
            ResourceTerms[(Signal, "Electrical", Node)].add(Owner)
            for Neighbor in ResourceGraph.Technology.NeighborPositions(Node):
                ResourceTerms[(
                    Signal,
                    "Electrical",
                    Neighbor,
                )].add(Owner)

    for Signal, Nodes in BaseNodes.items():
        AddUnaryClaims(Signal, Nodes, frozenset())
    for Domain in Domains:
        for Choice in Domain.Choices:
            AddUnaryClaims(
                Domain.Signal,
                Choice.Nodes,
                frozenset(((Domain.Variable, Choice.ChoiceId),)),
            )

    # Required-air claims are conditional on both primitive endpoints being
    # selected.  Endpoint terms may be fixed, unary, or the same choice.
    for Signal, OwnersByNode in NodeTerms.items():
        Nodes = frozenset(OwnersByNode)
        for First in sorted(Nodes):
            for Second in ResourceGraph.Technology.NeighborPositions(First):
                if Second not in Nodes or Second <= First:
                    continue
                Primitive = ResourceGraph.BuildPrimitive(First, Second)
                if Primitive is None:
                    continue
                for FirstTerm in OwnersByNode[First]:
                    for SecondTerm in OwnersByNode[Second]:
                        Combined = frozenset((*FirstTerm, *SecondTerm))
                        if not TermsAreCompatible(Combined):
                            continue
                        for Position in Primitive.Claims.RequiredAirCells:
                            ResourceTerms[(
                                Signal,
                                "Air",
                                Position,
                            )].add(Combined)

    ConflictsByAssignments: dict[
        frozenset[Assignment], set[Position3]
    ] = defaultdict(set)
    DependenciesByAssignments: dict[
        frozenset[Assignment], set[str]
    ] = defaultdict(set)
    LocalConflictAssignments: set[frozenset[Assignment]] = set()

    def AddResourceConflicts(
        FirstSignal: str,
        FirstKind: str,
        SecondSignal: str,
        SecondKind: str,
    ) -> None:
        FirstByPosition = {
            Position: Terms
            for (Signal, Kind, Position), Terms in ResourceTerms.items()
            if Signal == FirstSignal and Kind == FirstKind
        }
        SecondByPosition = {
            Position: Terms
            for (Signal, Kind, Position), Terms in ResourceTerms.items()
            if Signal == SecondSignal and Kind == SecondKind
        }
        for Position in FirstByPosition.keys() & SecondByPosition.keys():
            for FirstTerm in FirstByPosition[Position]:
                for SecondTerm in SecondByPosition[Position]:
                    Combined = frozenset((*FirstTerm, *SecondTerm))
                    if (
                        len(Combined) <= 3
                        and TermsAreCompatible(Combined)
                    ):
                        ConflictsByAssignments[Combined].add(Position)
                        LocalConflictAssignments.add(Combined)

    Signals = tuple(sorted({
        *BaseNodes,
        *(Domain.Signal for Domain in Domains),
    }))
    for Signal in Signals:
        AddResourceConflicts(Signal, "Air", Signal, "Wire")
        AddResourceConflicts(Signal, "Support", Signal, "Wire")
        AddResourceConflicts(Signal, "Support", Signal, "Air")
    for SignalIndex, FirstSignal in enumerate(Signals):
        for SecondSignal in Signals[SignalIndex + 1:]:
            AddResourceConflicts(
                FirstSignal, "Wire", SecondSignal, "Electrical"
            )
            AddResourceConflicts(
                SecondSignal, "Wire", FirstSignal, "Electrical"
            )
            AddResourceConflicts(
                FirstSignal, "Support", SecondSignal, "Wire"
            )
            AddResourceConflicts(
                FirstSignal, "Support", SecondSignal, "Air"
            )
            AddResourceConflicts(
                SecondSignal, "Support", FirstSignal, "Wire"
            )
            AddResourceConflicts(
                SecondSignal, "Support", FirstSignal, "Air"
            )
            AddResourceConflicts(
                FirstSignal, "Air", SecondSignal, "Wire"
            )
            AddResourceConflicts(
                SecondSignal, "Air", FirstSignal, "Wire"
            )
    FrozenClaims = tuple(FrozenComponentClaims)
    for (Signal, Kind, Position), Terms in ResourceTerms.items():
        for Claim in FrozenClaims:
            if Claim.Signal == Signal:
                continue
            Frozen = Claim.Claims
            Conflicts = bool(
                (Kind == "Wire" and Position in (
                    Frozen.WireCells
                    | Frozen.SupportCells
                    | Frozen.RequiredAirCells
                    | Frozen.ElectricalCells
                ))
                or (Kind == "Support" and Position in (
                    Frozen.WireCells | Frozen.RequiredAirCells
                ))
                or (Kind == "Air" and Position in (
                    Frozen.WireCells | Frozen.SupportCells
                ))
                or (Kind == "Electrical" and Position in Frozen.WireCells)
            )
            if not Conflicts:
                continue
            for TermValue in Terms:
                ConflictsByAssignments[TermValue].add(Position)
                DependenciesByAssignments[TermValue].add(str(Claim.Signal))
    if frozenset() in ConflictsByAssignments:
        if not DependenciesByAssignments.get(frozenset()):
            raise ValueError(
                "sparse exact portal factor base claims are already illegal"
            )
    LocalMinimalAssignments = []
    ForeignMinimalAssignments = []
    for Values in sorted(
        ConflictsByAssignments,
        key=lambda Value: (len(Value), tuple(sorted(Value))),
    ):
        Dependencies = DependenciesByAssignments.get(Values, set())
        if Values in LocalConflictAssignments:
            if any(Prior <= Values for Prior in LocalMinimalAssignments):
                continue
            LocalMinimalAssignments.append(Values)
            continue
        if any(Prior <= Values for Prior in LocalMinimalAssignments):
            continue
        if any(Prior <= Values for Prior in ForeignMinimalAssignments):
            continue
        ForeignMinimalAssignments.append(Values)
    Factors = tuple(sorted((
        ExactPortalConstraintForbiddenTuple(
            Assignments=tuple(sorted(Values)),
            ConflictPositions=frozenset(ConflictsByAssignments[Values]),
            DependencySignals=tuple(sorted(
                DependenciesByAssignments.get(Values, set())
            )),
        )
        for Values in (
            *LocalMinimalAssignments,
            *ForeignMinimalAssignments,
        )
    ), key=lambda Value: (
        len(Value.Assignments),
        Value.Assignments,
        Value.DependencySignals,
    )))
    DomainFingerprint = BuildStableFingerprint((
        "sparse-exact-portal-constraint-factors-v1",
        tuple(sorted(
            (Signal, tuple(sorted(Nodes)))
            for Signal, Nodes in BaseNodes.items()
        )),
        tuple(
            (
                Domain.Variable,
                Domain.Signal,
                tuple(
                    (Choice.ChoiceId, tuple(sorted(Choice.Nodes)))
                    for Choice in sorted(
                        Domain.Choices,
                        key=lambda Value: Value.ChoiceId,
                    )
                ),
            )
            for Domain in Domains
        ),
        tuple(
            (
                Value.Assignments,
                tuple(sorted(Value.ConflictPositions)),
                Value.DependencySignals,
            )
            for Value in Factors
        ),
    ))
    return ExactPortalConstraintFactorExtraction(
        DomainFingerprint=DomainFingerprint,
        Complete=True,
        Variables=tuple(Value.Variable for Value in Domains),
        ForbiddenTuples=Factors,
        EvaluatedPartialAssignmentCount=0,
        EvaluatedFullAssignmentCount=0,
        MaximumForbiddenTupleArity=max(
            (len(Value.Assignments) for Value in Factors),
            default=0,
        ),
    )
