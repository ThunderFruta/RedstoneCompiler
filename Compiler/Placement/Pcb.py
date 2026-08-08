"""Circuit-agnostic PCB-style clustering and weighted gate placement."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from collections import deque
from functools import lru_cache
from hashlib import sha256
from itertools import combinations, permutations, product
from math import ceil, sqrt
from statistics import median
from typing import Any, Callable, Iterable, Mapping

from ..Ir.ComponentGraph import BuildComponentGraph
from .Rotation import (
    NormalizeRotation,
    RotatedCellSize,
    TransformDirection,
    TransformLocalPosition,
)
from .Geometry import (
    BuildPlacedGate,
    GateAccessPositions,
    GetGateInputAccess,
    PlacedGate,
    PlacedDesign,
    RectanglesOverlap,
)
from .PreRouteInterface import (
    DerivedPerimeterSlotAssignment,
    DerivedPerimeterSlotDomain,
    DerivedPerimeterTerminalSlot,
    SolveDerivedPerimeterSlotDomain,
)
from ..Routing.Technology import DefaultRedstoneRoutingTechnology
from ..Routing.Reliability import BuildStableFingerprint
from ..Routing.Models import (
    InterClusterChannelLane,
    InterClusterRoutingChannel,
)
from ..Routing.Policy import ClusteringPolicy, NandPackingPolicy, PlacementPolicy
from ..Routing.Actions.Geometry import BuildPlacedCellGeometry
from ..Routing.Actions.Geometry import ValidatePlacedCellElectricalIsolation
from ..Routing.Actions.Validation import (
    BuildPhysicalGraphs,
    ValidatePhysicalRoutes,
    ValidateTemplateIsolation,
)
from ..Routing.Failures import (
    RoutingAssignmentCut,
    RoutingFailure,
    RoutingFailureReason,
    RoutingStageError,
)
from ..Routing.ResourceGraph import (
    BuildRoutingEnvelope,
    FindClaimConflicts,
    FindClaimConflictsByResourceIndex,
    FindSelfClaimConflicts,
    LocalRouteClaim,
    NormalizeRoutingEdge,
    RoutingResourceClaims,
    RoutingResourceGraph,
    RoutingResourceId,
    RoutingReservation,
    ValidateLocalRouteClaims,
)


# A joint portfolio materializes several retained states of the same completed
# local cluster layouts.  Retain the expensive slot/orientation beam result
# within one compiler process; final gate commit, local routing, and exact
# candidate screening still run independently for every state.
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


@dataclass(frozen=True)
class ExactStatePlacedGateGeometry:
    """Immutable exact-state gate geometry reconstructed from the module."""

    Name: str
    X: int
    Y: int
    Z: int
    Rotation: int
    MirrorX: bool

    @classmethod
    def FromPlacedGate(
        cls,
        Gate: PlacedGate,
    ) -> "ExactStatePlacedGateGeometry":
        return cls(
            Name=Gate.Name,
            X=Gate.X,
            Y=Gate.Y,
            Z=Gate.Z,
            Rotation=Gate.Rotation,
            MirrorX=Gate.MirrorX,
        )

    def BuildPlacedGate(
        self,
        ModuleGate: Any,
    ) -> PlacedGate:
        """Return a fresh mutable placed gate for one cache consumer."""
        return BuildPlacedGate(
            ModuleGate,
            self.X,
            self.Y,
            self.Z,
            self.Rotation,
            self.MirrorX,
        )


@dataclass(frozen=True)
class ExactJointPlacementScreen:
    """Transactional retained-state diagnostics plus reusable core geometry."""

    RetainedStates: tuple[dict[str, object], ...]
    CoreGeometryByCandidate: tuple[
        tuple[int, tuple[ExactStatePlacedGateGeometry, ...]],
        ...,
    ]
    MandatoryProfileByCandidate: tuple[
        tuple[int, "MandatoryAccessConflictProfile"],
        ...,
    ] = ()

    def CoreGeometry(
        self,
        CandidateIndex: int,
    ) -> tuple[ExactStatePlacedGateGeometry, ...] | None:
        """Return one retained state's immutable NAND-only geometry."""
        return next(
            (
                Geometry
                for Index, Geometry in self.CoreGeometryByCandidate
                if Index == CandidateIndex
            ),
            None,
        )

    def MandatoryProfile(
        self,
        CandidateIndex: int,
    ) -> "MandatoryAccessConflictProfile | None":
        """Return the cached NAND-only mandatory-access screen."""
        return next(
            (
                Profile
                for Index, Profile in self.MandatoryProfileByCandidate
                if Index == CandidateIndex
            ),
            None,
        )


@dataclass(frozen=True)
class PinAlignedPackedClusterState:
    """One immutable retained graph-core placement state.

    ``CandidateIndex`` is the stable index accepted by the legacy indexed
    builder.  It is intentionally retained even when a portfolio filters a
    dominated state, so a caller can materialize a validated state without
    guessing a dense index later in the placement flow.
    """

    CandidateIndex: int
    Positions: tuple[tuple[str, tuple[int, int]], ...]
    Rotations: tuple[tuple[str, int], ...]
    Mirrors: tuple[tuple[str, bool], ...]
    Objective: tuple[int, int, int, int, int]
    Fingerprint: str

    @classmethod
    def FromMutableCandidate(
        cls,
        CandidateIndex: int,
        Candidate: tuple[
            dict[str, tuple[int, int]],
            dict[str, int],
            dict[str, bool],
        ],
        Objective: tuple[int, int, int, int, int],
    ) -> "PinAlignedPackedClusterState":
        """Freeze one normalized graph-core candidate for cache sharing."""
        Positions, Rotations, Mirrors = Candidate
        CanonicalPositions = tuple(sorted(
            (str(Name), (int(Position[0]), int(Position[1])))
            for Name, Position in Positions.items()
        ))
        CanonicalRotations = tuple(sorted(
            (str(Name), int(Rotation))
            for Name, Rotation in Rotations.items()
        ))
        CanonicalMirrors = tuple(sorted(
            (str(Name), bool(MirrorX))
            for Name, MirrorX in Mirrors.items()
        ))
        Fingerprint = sha256(repr((
            CanonicalPositions,
            CanonicalRotations,
            CanonicalMirrors,
        )).encode("utf-8")).hexdigest()[:16]
        return cls(
            CandidateIndex=CandidateIndex,
            Positions=CanonicalPositions,
            Rotations=CanonicalRotations,
            Mirrors=CanonicalMirrors,
            Objective=Objective,
            Fingerprint=Fingerprint,
        )

    def Materialize(
        self,
    ) -> tuple[
        dict[str, tuple[int, int]],
        dict[str, int],
        dict[str, bool],
    ]:
        """Return fresh mutable placement maps for one placement consumer."""
        return (
            dict(self.Positions),
            dict(self.Rotations),
            dict(self.Mirrors),
        )


@dataclass(frozen=True)
class PinAlignedPackedClusterPortfolio:
    """Finite non-dominated graph-core states retained by one bounded beam.

    The portfolio is not an exhaustive placement proof: the graph beam itself
    is deliberately bounded.  It is a fixed, materialized domain that callers
    can inspect before selecting a state, rather than an implicit sequence of
    indexed placement attempts.
    """

    States: tuple[PinAlignedPackedClusterState, ...]
    RawCandidateCount: int

    def __post_init__(self) -> None:
        CandidateIndexes = tuple(State.CandidateIndex for State in self.States)
        if len(set(CandidateIndexes)) != len(CandidateIndexes):
            raise ValueError("graph-core portfolio candidate indexes must be unique")
        if self.RawCandidateCount < len(self.States):
            raise ValueError(
                "graph-core portfolio raw candidate count cannot be smaller "
                "than retained state count"
            )

    @property
    def CandidateCount(self) -> int:
        """Return the number of valid materializable portfolio states."""
        return len(self.States)


@dataclass(frozen=True)
class PlacementConstraintObservation:
    """Name-bearing diagnostic evidence for one heuristic interface cut."""

    Signals: tuple[str, ...]
    ObservationCount: int = 1
    ObservationFingerprints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        CanonicalSignals = tuple(sorted(set(map(str, self.Signals))))
        if len(CanonicalSignals) < 2:
            raise ValueError(
                "PlacementConstraintObservation requires two signals"
            )
        if self.ObservationCount < 1:
            raise ValueError("ObservationCount must be positive")
        object.__setattr__(self, "Signals", CanonicalSignals)
        object.__setattr__(
            self,
            "ObservationFingerprints",
            tuple(sorted(set(map(str, self.ObservationFingerprints)))),
        )

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Signals": list(self.Signals),
            "ObservationCount": self.ObservationCount,
            "ObservationFingerprints": list(
                self.ObservationFingerprints
            ),
        }


@dataclass(frozen=True)
class PlacementAssignmentConstraintSet:
    """Canonical placement-scoring constraints learned from exact cuts."""

    PairwiseConflictEdges: tuple[tuple[str, str], ...] = ()
    HigherOrderSignalSets: tuple[tuple[str, ...], ...] = ()
    ObservedInterfaceConflictEdges: tuple[tuple[str, str], ...] = ()
    HigherOrderSignalEvidence: tuple[
        PlacementConstraintObservation, ...
    ] = ()
    ObservedInterfaceConflictEvidence: tuple[
        PlacementConstraintObservation, ...
    ] = ()
    ActiveHigherOrderSignalSets: tuple[tuple[str, ...], ...] = ()
    ActiveObservedInterfaceConflictEdges: tuple[
        tuple[str, str], ...
    ] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "PairwiseConflictEdges",
            tuple(sorted({
                tuple(sorted((str(First), str(Second))))
                for First, Second in self.PairwiseConflictEdges
                if str(First) != str(Second)
            })),
        )
        object.__setattr__(
            self,
            "HigherOrderSignalSets",
            tuple(sorted({
                tuple(sorted({str(Signal) for Signal in Signals}))
                for Signals in self.HigherOrderSignalSets
                if len(set(map(str, Signals))) >= 2
            })),
        )
        object.__setattr__(
            self,
            "ObservedInterfaceConflictEdges",
            tuple(sorted({
                tuple(sorted((str(First), str(Second))))
                for First, Second in self.ObservedInterfaceConflictEdges
                if str(First) != str(Second)
            })),
        )
        object.__setattr__(
            self,
            "HigherOrderSignalEvidence",
            tuple(sorted(
                {
                    Observation.Signals: Observation
                    for Observation in self.HigherOrderSignalEvidence
                }.values(),
                key=lambda Observation: Observation.Signals,
            )),
        )
        object.__setattr__(
            self,
            "ObservedInterfaceConflictEvidence",
            tuple(sorted(
                {
                    Observation.Signals: Observation
                    for Observation
                    in self.ObservedInterfaceConflictEvidence
                }.values(),
                key=lambda Observation: Observation.Signals,
            )),
        )
        object.__setattr__(
            self,
            "ActiveHigherOrderSignalSets",
            tuple(sorted({
                tuple(sorted(set(map(str, Signals))))
                for Signals in self.ActiveHigherOrderSignalSets
                if len(set(map(str, Signals))) >= 2
            })),
        )
        object.__setattr__(
            self,
            "ActiveObservedInterfaceConflictEdges",
            tuple(sorted({
                tuple(sorted((str(First), str(Second))))
                for First, Second
                in self.ActiveObservedInterfaceConflictEdges
                if str(First) != str(Second)
            })),
        )

    @property
    def Fingerprint(self) -> str:
        return sha256(repr((
            self.PairwiseConflictEdges,
            self.HigherOrderSignalSets,
            self.ObservedInterfaceConflictEdges,
            tuple(
                (
                    Observation.Signals,
                    Observation.ObservationCount,
                )
                for Observation in self.HigherOrderSignalEvidence
            ),
            tuple(
                (
                    Observation.Signals,
                    Observation.ObservationCount,
                )
                for Observation
                in self.ObservedInterfaceConflictEvidence
            ),
        )).encode("utf-8")).hexdigest()

    @property
    def HasActivePlacementConstraints(self) -> bool:
        """Return whether hard or recurrent evidence changes placement."""
        return bool(
            self.PairwiseConflictEdges
            or self.ActiveHigherOrderSignalSets
            or self.ActiveObservedInterfaceConflictEdges
        )

    def WithCut(
        self,
        AssignmentCut: RoutingAssignmentCut | None,
    ) -> "PlacementAssignmentConstraintSet":
        """Return this immutable set plus one exact cut's placement demands."""
        if AssignmentCut is None:
            return self
        PairwiseEdges = BuildEffectiveAssignmentCutPairwiseEdges(
            AssignmentCut
        )
        CurrentObservedEdges = tuple(sorted({
            tuple(sorted(map(str, Edge)))
            for Edge in AssignmentCut.ConflictGraph.get(
                "ObservedPatternConflictEdges",
                (),
            )
            if (
                isinstance(Edge, tuple | list)
                and len(Edge) == 2
                and str(Edge[0]) != str(Edge[1])
            )
        }))
        CurrentHigherOrderSignalSets: tuple[tuple[str, ...], ...] = ()
        if AssignmentCut.Classification.value in {
            "saturated-boundary-cut",
            "higher-order-placement-conflict",
            "larger-matching-failure",
            "multi-pair-placement-conflict",
            "relocated-higher-order-conflict",
            "relocated-larger-matching-failure",
            "relocated-multi-pair-conflict",
            "relocated-pairwise-incompatibility",
        }:
            Signals = (
                AssignmentCut.PriorityRelocationSignals
                or AssignmentCut.RelocationSignals
                or AssignmentCut.ConflictSignals
            )
            if len(Signals) >= 2:
                CurrentHigherOrderSignalSets = (
                    tuple(sorted(set(map(str, Signals)))),
                )

        ObservationFingerprint = sha256(repr((
            AssignmentCut.Classification.value,
            AssignmentCut.ConflictFingerprint,
            AssignmentCut.MandatoryAccessOwnershipFingerprint,
            AssignmentCut.SourceCandidateId,
            AssignmentCut.ConflictGraphJson,
        )).encode("utf-8")).hexdigest()

        def UpdateEvidence(
            Existing: tuple[PlacementConstraintObservation, ...],
            ExistingActive: tuple[tuple[str, ...], ...],
            Current: tuple[tuple[str, ...], ...],
        ) -> tuple[PlacementConstraintObservation, ...]:
            EvidenceBySignals = {
                Observation.Signals: Observation
                for Observation in Existing
            }
            for Signals in ExistingActive:
                Canonical = tuple(sorted(set(map(str, Signals))))
                if len(Canonical) < 2 or Canonical in EvidenceBySignals:
                    continue
                EvidenceBySignals[Canonical] = (
                    PlacementConstraintObservation(
                        Signals=Canonical,
                        ObservationCount=1,
                        ObservationFingerprints=(),
                    )
                )
            for Signals in Current:
                Prior = EvidenceBySignals.get(Signals)
                PriorFingerprints = (
                    Prior.ObservationFingerprints
                    if Prior is not None
                    else ()
                )
                IsNewObservation = (
                    ObservationFingerprint not in PriorFingerprints
                )
                EvidenceBySignals[Signals] = (
                    PlacementConstraintObservation(
                        Signals=Signals,
                        ObservationCount=(
                            1
                            if Prior is None
                            else (
                                Prior.ObservationCount + 1
                                if IsNewObservation
                                else Prior.ObservationCount
                            )
                        ),
                        ObservationFingerprints=(
                            *PriorFingerprints,
                            ObservationFingerprint,
                        ),
                    )
                )
            return tuple(sorted(
                EvidenceBySignals.values(),
                key=lambda Observation: Observation.Signals,
            ))

        HigherOrderEvidence = UpdateEvidence(
            self.HigherOrderSignalEvidence,
            self.HigherOrderSignalSets,
            CurrentHigherOrderSignalSets,
        )
        if CurrentHigherOrderSignalSets:
            # Exact higher-order cores can change by one peripheral signal
            # after each access-distinct relocation while retaining the same
            # congested interface nucleus.  Learn one bounded recurrent
            # intersection from distinct authoritative observations instead
            # of requiring the complete name-bearing set to repeat exactly.
            # Three signals are required so an overlap remains a hyperedge,
            # never an inferred capacity-one pair.
            OverlapCandidates: list[
                tuple[
                    int,
                    int,
                    tuple[str, ...],
                    tuple[str, ...],
                ]
            ] = []
            for CurrentSignals in CurrentHigherOrderSignalSets:
                CurrentSet = frozenset(CurrentSignals)
                for Prior in self.HigherOrderSignalEvidence:
                    SupportingFingerprints = tuple(sorted({
                        *Prior.ObservationFingerprints,
                        ObservationFingerprint,
                    }))
                    if len(SupportingFingerprints) < 2:
                        continue
                    Overlap = tuple(sorted(
                        CurrentSet.intersection(Prior.Signals)
                    ))
                    if len(Overlap) < 3:
                        continue
                    OverlapCandidates.append((
                        -len(SupportingFingerprints),
                        -len(Overlap),
                        Overlap,
                        SupportingFingerprints,
                    ))
            if OverlapCandidates:
                (
                    _NegativeSupport,
                    _NegativeSize,
                    RecurrentOverlap,
                    SupportingFingerprints,
                ) = min(OverlapCandidates)
                EvidenceBySignals = {
                    Observation.Signals: Observation
                    for Observation in HigherOrderEvidence
                }
                PriorOverlap = EvidenceBySignals.get(
                    RecurrentOverlap
                )
                CombinedFingerprints = tuple(sorted({
                    *(
                        PriorOverlap.ObservationFingerprints
                        if PriorOverlap is not None
                        else ()
                    ),
                    *SupportingFingerprints,
                }))
                EvidenceBySignals[RecurrentOverlap] = (
                    PlacementConstraintObservation(
                        Signals=RecurrentOverlap,
                        ObservationCount=max(
                            (
                                PriorOverlap.ObservationCount
                                if PriorOverlap is not None
                                else 0
                            ),
                            len(CombinedFingerprints),
                        ),
                        ObservationFingerprints=CombinedFingerprints,
                    )
                )
                HigherOrderEvidence = tuple(sorted(
                    EvidenceBySignals.values(),
                    key=lambda Observation: Observation.Signals,
                ))
        ObservedInterfaceEvidence = UpdateEvidence(
            self.ObservedInterfaceConflictEvidence,
            self.ObservedInterfaceConflictEdges,
            CurrentObservedEdges,
        )
        def RecurrentWorkingSet(
            Evidence: tuple[PlacementConstraintObservation, ...],
        ) -> tuple[tuple[str, ...], ...]:
            return tuple(sorted({
                Observation.Signals
                for Observation in Evidence
                if Observation.ObservationCount >= 2
            }))

        ActiveHigherOrderSignalSets = RecurrentWorkingSet(
            HigherOrderEvidence,
        )
        ActiveObservedInterfaceConflictEdges = RecurrentWorkingSet(
            ObservedInterfaceEvidence,
        )
        return PlacementAssignmentConstraintSet(
            PairwiseConflictEdges=(
                *self.PairwiseConflictEdges,
                *PairwiseEdges,
            ),
            HigherOrderSignalSets=(
                *self.HigherOrderSignalSets,
                *CurrentHigherOrderSignalSets,
            ),
            ObservedInterfaceConflictEdges=(
                *self.ObservedInterfaceConflictEdges,
                *CurrentObservedEdges,
            ),
            HigherOrderSignalEvidence=HigherOrderEvidence,
            ObservedInterfaceConflictEvidence=(
                ObservedInterfaceEvidence
            ),
            ActiveHigherOrderSignalSets=(
                ActiveHigherOrderSignalSets
            ),
            ActiveObservedInterfaceConflictEdges=(
                ActiveObservedInterfaceConflictEdges
            ),
        )

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Fingerprint": self.Fingerprint,
            "PairwiseConflictEdges": [
                list(Edge) for Edge in self.PairwiseConflictEdges
            ],
            "HigherOrderSignalSets": [
                list(Signals) for Signals in self.HigherOrderSignalSets
            ],
            "ObservedInterfaceConflictEdges": [
                list(Edge)
                for Edge in self.ObservedInterfaceConflictEdges
            ],
            "HigherOrderSignalEvidence": [
                Observation.ToDictionary()
                for Observation in self.HigherOrderSignalEvidence
            ],
            "ObservedInterfaceConflictEvidence": [
                Observation.ToDictionary()
                for Observation
                in self.ObservedInterfaceConflictEvidence
            ],
            "ActiveHigherOrderSignalSets": [
                list(Signals)
                for Signals in self.ActiveHigherOrderSignalSets
            ],
            "ActiveObservedInterfaceConflictEdges": [
                list(Edge)
                for Edge in self.ActiveObservedInterfaceConflictEdges
            ],
        }


@dataclass(frozen=True)
class PlacementConstraintWorkingSet:
    """Recurrent constraints that can interact with the current cut."""

    PairwiseConflictEdges: tuple[tuple[str, str], ...] = ()
    HigherOrderSignalSets: tuple[tuple[str, ...], ...] = ()
    ObservedInterfaceConflictEdges: tuple[tuple[str, str], ...] = ()

    def ToDictionary(self) -> dict[str, object]:
        return {
            "PairwiseConflictEdges": [
                list(Edge) for Edge in self.PairwiseConflictEdges
            ],
            "HigherOrderSignalSets": [
                list(Signals) for Signals in self.HigherOrderSignalSets
            ],
            "ObservedInterfaceConflictEdges": [
                list(Edge) for Edge in self.ObservedInterfaceConflictEdges
            ],
        }


def SelectPlacementConstraintWorkingSet(
    AssignmentCut: RoutingAssignmentCut | None,
    Constraints: PlacementAssignmentConstraintSet,
    FrontierAssignmentCuts: Iterable[RoutingAssignmentCut] = (),
    *,
    ExpandConnectedComponent: bool = False,
) -> PlacementConstraintWorkingSet:
    """Select bounded recurrent pressure for a cut-scoped move.

    Exact pair constraints remain globally hard elsewhere. Recurrent heuristic
    constraints enter this working set only when they share a signal with the
    current authoritative cut or its explicitly bounded frontier. A cut-scoped
    move can therefore avoid reopening the immediately preceding interface
    without turning every historical observation into a design-wide search.
    """
    BoundedCompleteProofSignals = (
        frozenset(AssignmentCut.PriorityRelocationSignals)
        if (
            AssignmentCut is not None
            and AssignmentCut.CompleteAssignmentCutProof
            and AssignmentCut.PriorityRelocationSignals
        )
        else frozenset()
    )
    ActiveCuts = tuple(
        Cut
        for Cut in (
            *((AssignmentCut,) if AssignmentCut is not None else ()),
            *(
                ()
                if BoundedCompleteProofSignals
                else FrontierAssignmentCuts
            ),
        )
        if Cut is not None
    )
    CurrentSignals = (
        BoundedCompleteProofSignals
        or frozenset(
            Signal
            for Cut in ActiveCuts
            for Signal in (
                *Cut.ConflictSignals,
                *Cut.RelocationSignals,
                *Cut.PriorityRelocationSignals,
                *Cut.NoCandidateSignals,
                *(
                    Signal
                    for Edge in Cut.PairwiseConflictEdges
                    for Signal in Edge
                ),
            )
        )
    )
    if not CurrentSignals:
        return PlacementConstraintWorkingSet(
            PairwiseConflictEdges=Constraints.PairwiseConflictEdges,
            HigherOrderSignalSets=(
                Constraints.ActiveHigherOrderSignalSets
            ),
            ObservedInterfaceConflictEdges=(
                Constraints.ActiveObservedInterfaceConflictEdges
            ),
        )
    WorkingSignals = set(CurrentSignals)
    if ExpandConnectedComponent and not BoundedCompleteProofSignals:
        ConstraintSignalSets = (
            *Constraints.PairwiseConflictEdges,
            *Constraints.ActiveHigherOrderSignalSets,
            *Constraints.ActiveObservedInterfaceConflictEdges,
        )
        Changed = True
        while Changed:
            Changed = False
            for Signals in ConstraintSignalSets:
                SignalSet = set(map(str, Signals))
                if (
                    WorkingSignals.intersection(SignalSet)
                    and not SignalSet.issubset(WorkingSignals)
                ):
                    WorkingSignals.update(SignalSet)
                    Changed = True
    return PlacementConstraintWorkingSet(
        PairwiseConflictEdges=tuple(
            Edge
            for Edge in Constraints.PairwiseConflictEdges
            if (
                set(Edge).issubset(WorkingSignals)
                if BoundedCompleteProofSignals
                else WorkingSignals.intersection(Edge)
            )
        ),
        HigherOrderSignalSets=tuple(
            Signals
            for Signals in Constraints.ActiveHigherOrderSignalSets
            if (
                set(Signals).issubset(WorkingSignals)
                if BoundedCompleteProofSignals
                else WorkingSignals.intersection(Signals)
            )
        ),
        ObservedInterfaceConflictEdges=tuple(
            Edge
            for Edge in Constraints.ActiveObservedInterfaceConflictEdges
            if (
                set(Edge).issubset(WorkingSignals)
                if BoundedCompleteProofSignals
                else WorkingSignals.intersection(Edge)
            )
        ),
    )


def BuildAssignmentCutHigherOrderSignalSet(
    AssignmentCut: RoutingAssignmentCut | None,
) -> tuple[str, ...]:
    """Project one non-pair authoritative cut into a stable signal set."""
    if AssignmentCut is None:
        return ()
    PairwiseEdges = BuildEffectiveAssignmentCutPairwiseEdges(AssignmentCut)
    Signals = tuple(sorted(set(map(str, (
        AssignmentCut.PriorityRelocationSignals
        or AssignmentCut.RelocationSignals
        or AssignmentCut.ConflictSignals
    )))))
    return Signals if not PairwiseEdges and len(Signals) >= 2 else ()


def BuildJointPlacementSearchCacheKey(
    Module: Any,
    Clusters: list[list[str]] | tuple[tuple[str, ...], ...],
    BaseAssignment: dict[int, tuple[int, int]],
    BeamWidth: int,
    PassLimit: int,
    RetainedCandidateCount: int,
    AssignmentCut: RoutingAssignmentCut | None = None,
    AssignmentConstraints: PlacementAssignmentConstraintSet = (
        PlacementAssignmentConstraintSet()
    ),
    EnableClusterInterfacePlacementFeasibility: bool = False,
    FocusedOptimizationClusters: frozenset[int] | None = None,
    FrontierAssignmentCuts: Iterable[RoutingAssignmentCut] = (),
) -> tuple[object, ...]:
    """Build the immutable joint-search identity, including exact cut work."""
    AssignmentCutKey = (
        (
            AssignmentCut.ConflictFingerprint,
            AssignmentCut.EffectiveWorkFingerprint,
            AssignmentCut.MandatoryAccessOwnershipFingerprint,
            AssignmentCut.AuthoritativeAccessDomainFingerprint,
            sha256(
                AssignmentCut.ConflictGraphJson.encode("utf-8")
            ).hexdigest()[:16],
        )
        if AssignmentCut is not None
        else ("", "", "", "", "")
    )
    return (
        id(Module),
        tuple(tuple(Names) for Names in Clusters),
        tuple(sorted(BaseAssignment.items())),
        BeamWidth,
        PassLimit,
        RetainedCandidateCount,
        EnableClusterInterfacePlacementFeasibility,
        (
            tuple(sorted(FocusedOptimizationClusters))
            if FocusedOptimizationClusters
            else ()
        ),
        tuple(
            (
                Cut.ConflictFingerprint,
                Cut.EffectiveWorkFingerprint,
            )
            for Cut in FrontierAssignmentCuts
        ),
        AssignmentCutKey,
        AssignmentConstraints.Fingerprint,
    )


@dataclass(frozen=True)
class LocalClusterRouteCandidate:
    """One legal placement-owned tree considered by the cluster optimizer."""

    CandidateId: str
    Claim: LocalRouteClaim

    @property
    def LocalizedTargetCount(self) -> int:
        return len(set(self.Claim.ConnectedTargets))

    @property
    def FullVolume(self) -> int:
        Envelope = BuildRoutingEnvelope(
            self.Claim.Nodes,
            self.Claim.Claims.SupportCells,
            (Reservation.Position for Reservation in self.Claim.RepeaterReservations),
        )
        return Envelope.Width * Envelope.Height * Envelope.Depth

    @property
    def RepeaterCount(self) -> int:
        return len(self.Claim.RepeaterReservations)

    @property
    def RouteAndSupportBlocks(self) -> int:
        return (
            self.Claim.ExactRouteSignalBlocks
            + self.Claim.ExactRouteRefreshBlocks
            + self.Claim.ExactRouteSupportBlocks
        )


@dataclass(frozen=True)
class LocalClusterRoutingSelection:
    """Result of bounded exact candidate selection for one packed cluster."""

    Candidates: tuple[LocalClusterRouteCandidate, ...]
    AssignmentExpansions: int
    BudgetExhausted: bool
    RejectionCounts: dict[str, int]


def SelectJointLocalClusterCandidates(
    ResourceGraph: RoutingResourceGraph,
    BaseClaims: tuple[LocalRouteClaim, ...],
    CandidatesBySignal: dict[str, tuple[LocalClusterRouteCandidate, ...]],
    MaximumExpansions: int,
) -> LocalClusterRoutingSelection:
    """Select compatible local trees with deterministic volume-first tie breaks.

    An omitted signal is an explicit option: it remains entirely with the
    authoritative global router.  Every trial is checked against the same
    resource graph used by detailed routing, including support, air and
    electrical-clearance claims.
    """
    Signals = tuple(sorted(CandidatesBySignal))
    Best: tuple[LocalClusterRouteCandidate, ...] = ()
    BestScore: tuple[object, ...] | None = None
    Expansions = 0
    BudgetExhausted = False
    Rejections: dict[str, int] = {}

    def Score(Selected: tuple[LocalClusterRouteCandidate, ...]) -> tuple[object, ...]:
        return (
            -sum(Candidate.LocalizedTargetCount for Candidate in Selected),
            sum(Candidate.FullVolume for Candidate in Selected),
            sum(Candidate.RepeaterCount for Candidate in Selected),
            sum(Candidate.RouteAndSupportBlocks for Candidate in Selected),
            tuple(Candidate.CandidateId for Candidate in Selected),
        )

    def Search(Index: int, Selected: tuple[LocalClusterRouteCandidate, ...]) -> None:
        nonlocal Best, BestScore, Expansions, BudgetExhausted
        if Expansions >= MaximumExpansions:
            BudgetExhausted = True
            return
        Expansions += 1
        if Index == len(Signals):
            CandidateScore = Score(Selected)
            if BestScore is None or CandidateScore < BestScore:
                Best = Selected
                BestScore = CandidateScore
            return
        Signal = Signals[Index]
        # Skip is always legal and preserves global-router authority.
        Search(Index + 1, Selected)
        for Candidate in CandidatesBySignal[Signal]:
            if BudgetExhausted:
                return
            Trial = (*Selected, Candidate)
            try:
                ValidateLocalRouteClaims(
                    ResourceGraph,
                    (*BaseClaims, *(Item.Claim for Item in Trial)),
                )
            except ValueError as Error:
                Reason = str(Error).split(":", 1)[0]
                Rejections[Reason] = Rejections.get(Reason, 0) + 1
                continue
            Search(Index + 1, Trial)

    Search(0, ())
    return LocalClusterRoutingSelection(
        Candidates=Best,
        AssignmentExpansions=Expansions,
        BudgetExhausted=BudgetExhausted,
        RejectionCounts=dict(sorted(Rejections.items())),
    )


@dataclass(frozen=True)
class BoundaryDemandRecord:
    """Required cluster-boundary resources for one unresolved signal."""

    Signal: str
    UnresolvedTargets: int
    RequiredPortalSlots: int
    RequiredCorridorLanes: int
    PreferredBoundarySide: str


@dataclass(frozen=True)
class BoundaryCapacityRecord:
    """Physically available escape capacity on one cluster boundary."""

    BoundarySide: str
    LegalPortalSlots: int
    LegalCorridorLanes: int
    Overflow: int


@dataclass(frozen=True)
class InterClusterBoundaryDemand:
    """Distinct signals that must cross one logical cluster boundary."""

    Axis: str
    BoundaryIndex: int
    Signals: tuple[str, ...]

    @property
    def RequiredCorridorLanes(self) -> int:
        """Return one independently routable lane per distinct signal."""
        return len(self.Signals)

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Axis": self.Axis,
            "BoundaryIndex": self.BoundaryIndex,
            "Signals": list(self.Signals),
            "RequiredCorridorLanes": self.RequiredCorridorLanes,
        }


@dataclass(frozen=True)
class ClusterBoundaryBundle:
    """One logical producer/consumer cluster interface contract."""

    SourceCluster: int
    TargetCluster: int
    Signals: tuple[str, ...]
    FanoutEndpoints: int

    @property
    def RequiredCorridorLanes(self) -> int:
        """Reserve one lane for each distinct crossing signal."""
        return len(self.Signals)

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SourceCluster": self.SourceCluster,
            "TargetCluster": self.TargetCluster,
            "Signals": list(self.Signals),
            "FanoutEndpoints": self.FanoutEndpoints,
            "RequiredCorridorLanes": self.RequiredCorridorLanes,
        }


@dataclass(frozen=True)
class CutDrivenClusterRefinementProfile:
    """Bounded structural cohesion applied only to reported exact-cut nets."""

    Signals: tuple[str, ...]
    EdgeWeight: int

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SignalCount": len(self.Signals),
            "EdgeWeight": self.EdgeWeight,
            "StructuralFingerprint": sha256(repr((
                len(self.Signals),
                self.EdgeWeight,
            )).encode("utf-8")).hexdigest(),
        }


@dataclass(frozen=True)
class ClusterBoundaryContractScore:
    """Topology-only capacity score for a tentative cluster-slot assignment."""

    PeakBoundaryDemand: int
    TotalBoundaryDemand: int
    OverflowLanes: int

    def ToDictionary(self) -> dict[str, int]:
        return {
            "PeakBoundaryDemand": self.PeakBoundaryDemand,
            "TotalBoundaryDemand": self.TotalBoundaryDemand,
            "OverflowLanes": self.OverflowLanes,
        }


@dataclass(frozen=True)
class ClusterInterfacePlacementPattern:
    """Topology-only boundary-bank ownership for one tentative placement."""

    SignalBanks: tuple[
        tuple[str, tuple[tuple[int, int, str, str, int, bool], ...]],
        ...,
    ]
    OwnershipFingerprint: str

    def BanksBySignal(
        self,
    ) -> dict[str, frozenset[tuple[int, int, str]]]:
        """Return physical slot-side banks while omitting diagnostic traits."""
        return {
            Signal: frozenset(
                (Column, Row, Side)
                for Column, Row, Side, _Role, _Rotation, _MirrorX in Banks
            )
            for Signal, Banks in self.SignalBanks
        }


@dataclass(frozen=True)
class ClusterInterfaceTopology:
    """Immutable signal-to-cluster interface model reused by a placement beam."""

    SignalEndpoints: tuple[
        tuple[str, int | None, tuple[int, ...], bool],
        ...,
    ]


def BuildClusterInterfaceTopology(
    Module: Any,
    Clusters: tuple[tuple[str, ...], ...],
    Signals: Iterable[str] | None = None,
) -> ClusterInterfaceTopology:
    """Build endpoint topology for selected signals or every interface."""
    SelectedSignals = (
        frozenset(map(str, Signals))
        if Signals is not None
        else None
    )
    ClusterByGate = {
        GateName: ClusterIndex
        for ClusterIndex, Names in enumerate(Clusters)
        for GateName in Names
    }
    ProducerBySignal = {
        Signal: Gate
        for Gate in Module.Gates
        for Signal in Gate.Outputs
        if SelectedSignals is None or Signal in SelectedSignals
    }
    ConsumersBySignal: dict[str, list[Any]] = {}
    for Gate in Module.Gates:
        for Signal in Gate.Inputs:
            if SelectedSignals is not None and Signal not in SelectedSignals:
                continue
            ConsumersBySignal.setdefault(Signal, []).append(Gate)
    SignalNames = tuple(sorted({
        *ProducerBySignal,
        *ConsumersBySignal,
    }))
    return ClusterInterfaceTopology(SignalEndpoints=tuple(
        (
            Signal,
            (
                ClusterByGate.get(ProducerBySignal[Signal].Name)
                if Signal in ProducerBySignal
                else None
            ),
            tuple(sorted({
                ClusterByGate[Consumer.Name]
                for Consumer in ConsumersBySignal.get(Signal, ())
                if Consumer.Name in ClusterByGate
            })),
            any(
                (
                    Consumer.Kind.value
                    if hasattr(Consumer.Kind, "value")
                    else str(Consumer.Kind)
                ) == "OUTPUT"
                for Consumer in ConsumersBySignal.get(Signal, ())
            ),
        )
        for Signal in SignalNames
    ))


@dataclass(frozen=True)
class ClusterInterfacePlacementScore:
    """Exact-cut score derived from tentative cluster boundary ownership."""

    PairBankConflicts: int
    HigherOrderBankPressure: int
    HigherOrderPeakBankDemand: int
    HigherOrderBankExcessDemand: int
    HigherOrderOverloadedBankCount: int
    FacingMismatches: int
    Pattern: ClusterInterfacePlacementPattern

    def ToDictionary(self) -> dict[str, object]:
        return {
            "PairBankConflicts": self.PairBankConflicts,
            "HigherOrderBankPressure": self.HigherOrderBankPressure,
            "HigherOrderPeakBankDemand": (
                self.HigherOrderPeakBankDemand
            ),
            "HigherOrderBankExcessDemand": (
                self.HigherOrderBankExcessDemand
            ),
            "HigherOrderOverloadedBankCount": (
                self.HigherOrderOverloadedBankCount
            ),
            "FacingMismatches": self.FacingMismatches,
            "SignalCount": len(self.Pattern.SignalBanks),
            "OwnershipFingerprint": self.Pattern.OwnershipFingerprint,
        }


@dataclass(frozen=True)
class HigherOrderPhysicalBankDemandScore:
    """Aggregate capacity pressure for reported higher-order interface cuts."""

    CollisionPairs: int = 0
    PeakDemand: int = 0
    ExcessDemand: int = 0
    OverloadedBankCount: int = 0


def ClusterBoundaryCorridorKey(
    Bank: tuple[int, int, str],
) -> tuple[int, int, str]:
    """Return the shared grid boundary reached through one cluster-side bank."""
    Column, Row, Side = Bank
    if Side == "East":
        return Column, Row, "Vertical"
    if Side == "West":
        return Column - 1, Row, "Vertical"
    if Side == "South":
        return Column, Row, "Horizontal"
    if Side == "North":
        return Column, Row - 1, "Horizontal"
    raise ValueError(f"Unknown cluster boundary side: {Side}")


def ScoreHigherOrderPhysicalBankDemand(
    PhysicalBanksBySignal: dict[
        str,
        frozenset[tuple[int, int, str]],
    ],
    HigherOrderConflictSets: Iterable[Iterable[str]],
) -> HigherOrderPhysicalBankDemandScore:
    """Measure total cut concentration on topology-derived physical banks.

    A maximum-only score cannot distinguish one shared bank from several
    simultaneously shared banks.  Sum pair collisions so concentration grows
    quadratically, then retain peak/excess/overloaded-bank diagnostics for
    deterministic lexicographic placement ranking.
    """
    CollisionPairs = 0
    PeakDemand = 0
    ExcessDemand = 0
    OverloadedBankCount = 0
    CanonicalConflictSets = tuple(sorted({
        tuple(sorted(set(map(str, Signals))))
        for Signals in HigherOrderConflictSets
        if len(set(map(str, Signals))) >= 3
    }))
    for Signals in CanonicalConflictSets:
        CandidateBanks = {
            Bank
            for Signal in Signals
            for Bank in PhysicalBanksBySignal.get(
                Signal,
                frozenset(),
            )
        }
        for Bank in CandidateBanks:
            Demand = sum(
                Bank
                in PhysicalBanksBySignal.get(
                    Signal,
                    frozenset(),
                )
                for Signal in Signals
            )
            PeakDemand = max(PeakDemand, Demand)
            if Demand <= 1:
                continue
            CollisionPairs += Demand * (Demand - 1) // 2
            ExcessDemand += Demand - 1
            OverloadedBankCount += 1
    return HigherOrderPhysicalBankDemandScore(
        CollisionPairs=CollisionPairs,
        PeakDemand=PeakDemand,
        ExcessDemand=ExcessDemand,
        OverloadedBankCount=OverloadedBankCount,
    )


def ScoreClusterInterfaceFacingMismatches(
    Topology: ClusterInterfaceTopology,
    Assignment: Mapping[int, tuple[int, int]],
    Variants: Mapping[int, ClusterLayoutVariant],
) -> int:
    """Count all interface pins that face away from their destination bank.

    This is the hot-path component of cluster-interface scoring.  It avoids
    constructing signal ownership sets and fingerprints for every beam state;
    the complete pattern is still built for the retained diagnostic states.
    """
    def BoundarySide(
        FromSlot: tuple[int, int],
        ToSlot: tuple[int, int],
    ) -> str:
        DeltaColumn = ToSlot[0] - FromSlot[0]
        DeltaRow = ToSlot[1] - FromSlot[1]
        if abs(DeltaColumn) >= abs(DeltaRow):
            return "East" if DeltaColumn >= 0 else "West"
        return "South" if DeltaRow >= 0 else "North"

    def OppositeBoundarySide(Side: str) -> str:
        return {
            "East": "West",
            "West": "East",
            "North": "South",
            "South": "North",
        }[Side]

    Directions = {
        "East": (1, 0, 0),
        "West": (-1, 0, 0),
        "North": (0, 0, -1),
        "South": (0, 0, 1),
    }
    Mismatches = 0

    def AddInterface(
        ClusterIndex: int,
        Side: str,
        Role: str,
    ) -> None:
        nonlocal Mismatches
        Variant = Variants[ClusterIndex]
        PinDirection = TransformDirection(
            (0, 0, 1) if Role == "Source" else (0, 0, -1),
            Variant.Rotation,
            Variant.MirrorX,
        )
        BoundaryDirection = Directions[Side]
        if (
            PinDirection[0] * BoundaryDirection[0]
            + PinDirection[2] * BoundaryDirection[2]
            <= 0
        ):
            Mismatches += 1

    for (
        _Signal,
        SourceCluster,
        TargetClusters,
        HasExternalTarget,
    ) in Topology.SignalEndpoints:
        if SourceCluster is not None:
            for TargetCluster in TargetClusters:
                if TargetCluster == SourceCluster:
                    continue
                SourceSide = BoundarySide(
                    Assignment[SourceCluster],
                    Assignment[TargetCluster],
                )
                AddInterface(SourceCluster, SourceSide, "Source")
                AddInterface(
                    TargetCluster,
                    OppositeBoundarySide(SourceSide),
                    "Target",
                )
            if HasExternalTarget:
                AddInterface(SourceCluster, "East", "Source")
        else:
            for TargetCluster in TargetClusters:
                AddInterface(TargetCluster, "West", "Target")
    return Mismatches


def ScoreClusterInterfaceFacingMismatchesForOrientations(
    Topology: ClusterInterfaceTopology,
    Assignment: Mapping[int, tuple[int, int]],
    Orientations: tuple[int, ...],
    SourceFaces: Mapping[int, tuple[tuple[int, int, int], ...]],
    TargetFaces: Mapping[int, tuple[tuple[int, int, int], ...]],
) -> int:
    """Count facing mismatches using precomputed rigid-transform faces."""
    Directions = {
        "East": (1, 0, 0),
        "West": (-1, 0, 0),
        "North": (0, 0, -1),
        "South": (0, 0, 1),
    }

    def BoundarySide(
        FromSlot: tuple[int, int],
        ToSlot: tuple[int, int],
    ) -> str:
        DeltaColumn = ToSlot[0] - FromSlot[0]
        DeltaRow = ToSlot[1] - FromSlot[1]
        if abs(DeltaColumn) >= abs(DeltaRow):
            return "East" if DeltaColumn >= 0 else "West"
        return "South" if DeltaRow >= 0 else "North"

    Mismatches = 0

    def AddInterface(ClusterIndex: int, Side: str, Role: str) -> None:
        nonlocal Mismatches
        Faces = SourceFaces if Role == "Source" else TargetFaces
        PinDirection = Faces[ClusterIndex][Orientations[ClusterIndex]]
        BoundaryDirection = Directions[Side]
        if (
            PinDirection[0] * BoundaryDirection[0]
            + PinDirection[2] * BoundaryDirection[2]
            <= 0
        ):
            Mismatches += 1

    for (
        _Signal,
        SourceCluster,
        TargetClusters,
        HasExternalTarget,
    ) in Topology.SignalEndpoints:
        if SourceCluster is not None:
            for TargetCluster in TargetClusters:
                if TargetCluster == SourceCluster:
                    continue
                SourceSide = BoundarySide(
                    Assignment[SourceCluster],
                    Assignment[TargetCluster],
                )
                AddInterface(SourceCluster, SourceSide, "Source")
                AddInterface(
                    TargetCluster,
                    {
                        "East": "West",
                        "West": "East",
                        "North": "South",
                        "South": "North",
                    }[SourceSide],
                    "Target",
                )
            if HasExternalTarget:
                AddInterface(SourceCluster, "East", "Source")
        else:
            for TargetCluster in TargetClusters:
                AddInterface(TargetCluster, "West", "Target")
    return Mismatches


def ScoreClusterInterfacePlacement(
    Module: Any,
    Clusters: tuple[tuple[str, ...], ...],
    Assignment: dict[int, tuple[int, int]],
    Variants: dict[int, ClusterLayoutVariant],
    PairwiseConflictEdges: Iterable[tuple[str, str]] = (),
    HigherOrderConflictSets: Iterable[Iterable[str]] = (),
    Topology: ClusterInterfaceTopology | None = None,
) -> ClusterInterfacePlacementScore:
    """Score cut-signal ownership of topology-derived cluster pin banks.

    This is deliberately a placement model rather than a portal generator.
    It proves whether a reported capacity-one pair is still being presented
    to the same cluster boundary bank.  The authoritative router remains the
    sole judge of concrete portal and electrical legality.
    """
    PairwiseConflictEdges = tuple({
        tuple(sorted((str(First), str(Second))))
        for First, Second in PairwiseConflictEdges
        if str(First) != str(Second)
    })
    HigherOrderConflictSets = tuple(sorted({
        tuple(sorted(set(map(str, Signals))))
        for Signals in HigherOrderConflictSets
        if len(set(map(str, Signals))) >= 3
    }))
    if Topology is None:
        Topology = BuildClusterInterfaceTopology(
            Module,
            Clusters,
            ({
                Signal
                for Edge in PairwiseConflictEdges
                for Signal in Edge
            } | {
                Signal
                for Signals in HigherOrderConflictSets
                for Signal in Signals
            }) or None,
        )

    def BoundarySide(
        FromSlot: tuple[int, int],
        ToSlot: tuple[int, int],
    ) -> str:
        DeltaColumn = ToSlot[0] - FromSlot[0]
        DeltaRow = ToSlot[1] - FromSlot[1]
        if abs(DeltaColumn) >= abs(DeltaRow):
            return "East" if DeltaColumn >= 0 else "West"
        return "South" if DeltaRow >= 0 else "North"

    def OppositeBoundarySide(Side: str) -> str:
        return {
            "East": "West",
            "West": "East",
            "North": "South",
            "South": "North",
        }[Side]

    def DirectionForSide(Side: str) -> tuple[int, int, int]:
        return {
            "East": (1, 0, 0),
            "West": (-1, 0, 0),
            "North": (0, 0, -1),
            "South": (0, 0, 1),
        }[Side]

    BanksBySignal: dict[
        str,
        set[tuple[int, int, str, str, int, bool]],
    ] = {}
    FacingMismatches = 0
    for (
        Signal,
        SourceCluster,
        TargetClusters,
        HasExternalTarget,
    ) in Topology.SignalEndpoints:
        HasExternalSource = SourceCluster is None
        Banks = BanksBySignal.setdefault(Signal, set())

        def AddBank(
            ClusterIndex: int,
            Side: str,
            Role: str,
        ) -> None:
            nonlocal FacingMismatches
            Slot = Assignment[ClusterIndex]
            Variant = Variants[ClusterIndex]
            Banks.add((
                Slot[0],
                Slot[1],
                Side,
                Role,
                Variant.Rotation,
                Variant.MirrorX,
            ))
            PinDirection = TransformDirection(
                (0, 0, 1) if Role == "Source" else (0, 0, -1),
                Variant.Rotation,
                Variant.MirrorX,
            )
            BoundaryDirection = DirectionForSide(Side)
            if (
                PinDirection[0] * BoundaryDirection[0]
                + PinDirection[2] * BoundaryDirection[2]
                <= 0
            ):
                FacingMismatches += 1

        if SourceCluster is not None:
            for TargetCluster in TargetClusters:
                if TargetCluster == SourceCluster:
                    continue
                SourceSide = BoundarySide(
                    Assignment[SourceCluster],
                    Assignment[TargetCluster],
                )
                AddBank(SourceCluster, SourceSide, "Source")
                AddBank(
                    TargetCluster,
                    OppositeBoundarySide(SourceSide),
                    "Target",
                )
            if HasExternalTarget:
                AddBank(SourceCluster, "East", "Source")
        elif HasExternalSource:
            for TargetCluster in TargetClusters:
                AddBank(TargetCluster, "West", "Target")

    SignalBanks = tuple(
        (Signal, tuple(sorted(Banks)))
        for Signal, Banks in sorted(BanksBySignal.items())
        if Banks
    )
    # Names select the cut endpoints, but never enter the topology identity.
    # Slot coordinates plus rigid transforms make renamed/reordered modules
    # produce the same ownership fingerprint.
    StructuralOwnership = tuple(sorted(
        Banks for _Signal, Banks in SignalBanks
    ))
    Pattern = ClusterInterfacePlacementPattern(
        SignalBanks=SignalBanks,
        OwnershipFingerprint=sha256(
            repr(StructuralOwnership).encode("utf-8")
        ).hexdigest(),
    )
    OwnershipBanksBySignal = Pattern.BanksBySignal()
    # Role and transform describe ownership identity, but the physical side of
    # one occupied cluster slot is the shared capacity resource.  Collapse
    # source/target roles before scoring so opposite endpoint roles cannot
    # hide competition for the same pin bank.
    PhysicalBanksBySignal = {
        Signal: frozenset(
            (Bank[0], Bank[1], Bank[2])
            for Bank in Banks
        )
        for Signal, Banks in OwnershipBanksBySignal.items()
    }
    # Facing banks of adjacent clusters feed the same capacity-one boundary
    # corridor.  Score that shared resource rather than treating the two sides
    # as independent merely because their owner slots differ.
    BoundaryCorridorsBySignal = {
        Signal: frozenset(
            ClusterBoundaryCorridorKey(Bank)
            for Bank in Banks
        )
        for Signal, Banks in PhysicalBanksBySignal.items()
    }
    PairBankConflicts = sum(
        bool(
            BoundaryCorridorsBySignal.get(str(First), frozenset())
            .intersection(
                BoundaryCorridorsBySignal.get(str(Second), frozenset())
            )
        )
        for First, Second in PairwiseConflictEdges
    )
    HigherOrderDemand = ScoreHigherOrderPhysicalBankDemand(
        BoundaryCorridorsBySignal,
        HigherOrderConflictSets,
    )
    return ClusterInterfacePlacementScore(
        PairBankConflicts=PairBankConflicts,
        HigherOrderBankPressure=HigherOrderDemand.CollisionPairs,
        HigherOrderPeakBankDemand=HigherOrderDemand.PeakDemand,
        HigherOrderBankExcessDemand=HigherOrderDemand.ExcessDemand,
        HigherOrderOverloadedBankCount=(
            HigherOrderDemand.OverloadedBankCount
        ),
        FacingMismatches=FacingMismatches,
        Pattern=Pattern,
    )


@dataclass(frozen=True)
class ClusterBoundaryLeaseRequest:
    """One packed interface signal requiring an owned pin-access portal."""

    SourceCluster: int
    TargetCluster: int
    Signal: str
    SourceBoundarySide: str
    TargetBoundarySide: str
    SourceTerminal: tuple[int, int, int] | None = None
    TargetTerminals: tuple[tuple[int, int, int], ...] = ()
    CompletePinAccess: bool = False

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SourceCluster": self.SourceCluster,
            "TargetCluster": self.TargetCluster,
            "Signal": self.Signal,
            "SourceBoundarySide": self.SourceBoundarySide,
            "TargetBoundarySide": self.TargetBoundarySide,
            "SourceTerminal": (
                list(self.SourceTerminal)
                if self.SourceTerminal is not None
                else None
            ),
            "TargetTerminals": [list(Value) for Value in self.TargetTerminals],
            "LeaseExtent": (
                "complete-pin-access-to-routing-track"
                if self.CompletePinAccess
                else "first-segment"
            ),
        }


def BuildClusterBoundaryBundles(
    Module: Any,
    Clusters: tuple[tuple[str, ...], ...],
) -> tuple[ClusterBoundaryBundle, ...]:
    """Build name-independent logical interfaces before physical placement."""
    ClusterByGate = {
        GateName: ClusterIndex
        for ClusterIndex, Names in enumerate(Clusters)
        for GateName in Names
    }
    ProducerClusterBySignal = {
        Signal: ClusterByGate[Gate.Name]
        for Gate in Module.Gates
        if Gate.Name in ClusterByGate
        for Signal in Gate.Outputs
    }
    SignalsByInterface: dict[tuple[int, int], set[str]] = {}
    EndpointsByInterface: dict[tuple[int, int], int] = {}
    for Gate in Module.Gates:
        TargetCluster = ClusterByGate.get(Gate.Name)
        if TargetCluster is None:
            continue
        for Signal in Gate.Inputs:
            SourceCluster = ProducerClusterBySignal.get(Signal)
            if SourceCluster is None or SourceCluster == TargetCluster:
                continue
            Interface = SourceCluster, TargetCluster
            SignalsByInterface.setdefault(Interface, set()).add(Signal)
            EndpointsByInterface[Interface] = (
                EndpointsByInterface.get(Interface, 0) + 1
            )
    return tuple(
        ClusterBoundaryBundle(
            SourceCluster=Source,
            TargetCluster=Target,
            Signals=tuple(sorted(Signals)),
            FanoutEndpoints=EndpointsByInterface[(Source, Target)],
        )
        for (Source, Target), Signals in sorted(SignalsByInterface.items())
    )


def BuildClusterBoundaryLeaseRequests(
    Bundles: tuple[ClusterBoundaryBundle, ...],
    Assignment: dict[int, tuple[int, int]],
    Module: Any | None = None,
    Clusters: tuple[tuple[str, ...], ...] = (),
    PlacedGates: Iterable[PlacedGate] = (),
    IncludePrimaryTerminals: bool = False,
) -> tuple[ClusterBoundaryLeaseRequest, ...]:
    """Materialize deterministic packed-boundary lease demand from slots.

    The router resolves the actual portal and its first segment against the
    authoritative resource graph.  Placement owns the invariant that every
    inter-cluster signal has one directional request, independent of names or
    synthesis order.
    """
    def BoundarySide(
        FromSlot: tuple[int, int],
        ToSlot: tuple[int, int],
    ) -> str:
        DeltaColumn = ToSlot[0] - FromSlot[0]
        DeltaRow = ToSlot[1] - FromSlot[1]
        if abs(DeltaColumn) >= abs(DeltaRow):
            return "East" if DeltaColumn >= 0 else "West"
        return "South" if DeltaRow >= 0 else "North"

    def PhysicalBoundarySide(
        FromTerminal: tuple[int, int, int],
        ToTerminals: Iterable[tuple[int, int, int]],
    ) -> str:
        Values = tuple(ToTerminals)
        if not Values:
            return "East"
        ToX = sum(Value[0] for Value in Values) / len(Values)
        ToZ = sum(Value[2] for Value in Values) / len(Values)
        DeltaX = ToX - FromTerminal[0]
        DeltaZ = ToZ - FromTerminal[2]
        if abs(DeltaX) >= abs(DeltaZ):
            return "East" if DeltaX >= 0 else "West"
        return "South" if DeltaZ >= 0 else "North"

    def OppositeBoundarySide(Side: str) -> str:
        return {
            "East": "West",
            "West": "East",
            "North": "South",
            "South": "North",
        }[Side]

    GateByName = {Gate.Name: Gate for Gate in PlacedGates}
    ClusterByGate = {
        GateName: ClusterIndex
        for ClusterIndex, Names in enumerate(Clusters)
        for GateName in Names
    }
    ProducerBySignal = {
        Signal: Gate
        for Gate in (Module.Gates if Module is not None else ())
        for Signal in Gate.Outputs
    }
    ConsumersBySignal: dict[str, list[Any]] = {}
    for Gate in (Module.Gates if Module is not None else ()):
        for Signal in Gate.Inputs:
            ConsumersBySignal.setdefault(Signal, []).append(Gate)

    def SignalTerminals(
        Signal: str,
        SourceCluster: int,
        TargetCluster: int,
    ) -> tuple[tuple[int, int, int] | None, tuple[tuple[int, int, int], ...]]:
        # Gate identity is used only to carry placement geometry across the
        # stage boundary.  Selection remains entirely physical and topology
        # driven; names never participate in routing policy.
        SourceGate = ProducerBySignal.get(Signal)
        SourcePlaced = (
            GateByName.get(SourceGate.Name) if SourceGate is not None else None
        )
        SourceTerminal = (
            SourcePlaced.OutputPin
            if SourcePlaced is not None else None
        )
        Targets = []
        for Consumer in ConsumersBySignal.get(Signal, ()):
            TargetPlaced = GateByName.get(Consumer.Name)
            if (
                TargetPlaced is None
                or ClusterByGate.get(Consumer.Name) != TargetCluster
            ):
                continue
            Targets.extend(
                TargetPlaced.InputPins[Index]
                for Index, InputSignal in enumerate(TargetPlaced.Inputs)
                if InputSignal == Signal
            )
        return SourceTerminal, tuple(sorted(set(Targets)))

    Requests: list[ClusterBoundaryLeaseRequest] = []
    for Bundle in Bundles:
        SourceSlot = Assignment.get(Bundle.SourceCluster)
        TargetSlot = Assignment.get(Bundle.TargetCluster)
        if SourceSlot is None or TargetSlot is None:
            continue
        SourceSide = BoundarySide(SourceSlot, TargetSlot)
        TargetSide = BoundarySide(TargetSlot, SourceSlot)
        for Signal in Bundle.Signals:
            SourceTerminal, TargetTerminals = SignalTerminals(
                Signal,
                Bundle.SourceCluster,
                Bundle.TargetCluster,
            )
            Requests.append(ClusterBoundaryLeaseRequest(
                SourceCluster=Bundle.SourceCluster,
                TargetCluster=Bundle.TargetCluster,
                Signal=Signal,
                SourceBoundarySide=SourceSide,
                TargetBoundarySide=TargetSide,
                SourceTerminal=SourceTerminal,
                TargetTerminals=TargetTerminals,
                CompletePinAccess=IncludePrimaryTerminals,
            ))

    # Primary terminals are cluster interfaces too.  Omitting them allowed a
    # capacity-legal cluster-to-cluster lease pattern to collide later with an
    # input/output pin bank during complete portal assignment.
    if Module is not None and IncludePrimaryTerminals:
        for Gate in Module.Gates:
            GateKind = (
                Gate.Kind.value
                if hasattr(Gate.Kind, "value")
                else str(Gate.Kind)
            )
            PlacedGate = GateByName.get(Gate.Name)
            if PlacedGate is None:
                continue
            if GateKind == "INPUT" and PlacedGate.OutputPin is not None:
                for Signal in Gate.Outputs:
                    TargetsByCluster: dict[
                        int, list[tuple[int, int, int]]
                    ] = {}
                    for Consumer in ConsumersBySignal.get(Signal, ()):
                        TargetCluster = ClusterByGate.get(Consumer.Name)
                        TargetPlaced = GateByName.get(Consumer.Name)
                        if TargetCluster is None or TargetPlaced is None:
                            continue
                        TargetsByCluster.setdefault(
                            TargetCluster,
                            [],
                        ).extend(
                            TargetPlaced.InputPins[Index]
                            for Index, InputSignal
                            in enumerate(TargetPlaced.Inputs)
                            if InputSignal == Signal
                        )
                    for TargetCluster, TargetTerminals in sorted(
                        TargetsByCluster.items()
                    ):
                        SourceSide = PhysicalBoundarySide(
                            PlacedGate.OutputPin,
                            TargetTerminals,
                        )
                        Requests.append(ClusterBoundaryLeaseRequest(
                            SourceCluster=-1,
                            TargetCluster=TargetCluster,
                            Signal=Signal,
                            SourceBoundarySide=SourceSide,
                            TargetBoundarySide=OppositeBoundarySide(
                                SourceSide
                            ),
                            SourceTerminal=PlacedGate.OutputPin,
                            TargetTerminals=tuple(sorted(set(
                                TargetTerminals
                            ))),
                            CompletePinAccess=True,
                        ))
            elif GateKind == "OUTPUT":
                for InputIndex, Signal in enumerate(Gate.Inputs):
                    SourceGate = ProducerBySignal.get(Signal)
                    SourceCluster = (
                        ClusterByGate.get(SourceGate.Name)
                        if SourceGate is not None
                        else None
                    )
                    SourcePlaced = (
                        GateByName.get(SourceGate.Name)
                        if SourceGate is not None
                        else None
                    )
                    if (
                        SourceCluster is None
                        or SourcePlaced is None
                        or SourcePlaced.OutputPin is None
                        or InputIndex >= len(PlacedGate.InputPins)
                    ):
                        continue
                    TargetTerminal = PlacedGate.InputPins[InputIndex]
                    SourceSide = PhysicalBoundarySide(
                        SourcePlaced.OutputPin,
                        (TargetTerminal,),
                    )
                    Requests.append(ClusterBoundaryLeaseRequest(
                        SourceCluster=SourceCluster,
                        TargetCluster=-1,
                        Signal=Signal,
                        SourceBoundarySide=SourceSide,
                        TargetBoundarySide=OppositeBoundarySide(SourceSide),
                        SourceTerminal=SourcePlaced.OutputPin,
                        TargetTerminals=(TargetTerminal,),
                        CompletePinAccess=True,
                    ))
    return tuple(sorted(
        Requests,
        key=lambda Value: (
            Value.SourceCluster,
            Value.TargetCluster,
            Value.Signal,
        ),
    ))


def ScoreClusterBoundaryContracts(
    Bundles: tuple[ClusterBoundaryBundle, ...],
    Assignment: dict[int, tuple[int, int]],
    BoundaryCapacity: int,
) -> ClusterBoundaryContractScore:
    """Score whether all logical bundles fit the tentative grid cuts."""
    if BoundaryCapacity < 1:
        raise ValueError("BoundaryCapacity must be positive")
    SignalsByBoundary: dict[tuple[str, int], set[str]] = {}
    for Bundle in Bundles:
        SourceSlot = Assignment.get(Bundle.SourceCluster)
        TargetSlot = Assignment.get(Bundle.TargetCluster)
        if SourceSlot is None or TargetSlot is None:
            continue
        SourceColumn, SourceRow = SourceSlot
        TargetColumn, TargetRow = TargetSlot
        for Boundary in range(
            min(SourceColumn, TargetColumn),
            max(SourceColumn, TargetColumn),
        ):
            SignalsByBoundary.setdefault(("X", Boundary), set()).update(
                Bundle.Signals
            )
        for Boundary in range(
            min(SourceRow, TargetRow),
            max(SourceRow, TargetRow),
        ):
            SignalsByBoundary.setdefault(("Z", Boundary), set()).update(
                Bundle.Signals
            )
    Demands = [len(Signals) for Signals in SignalsByBoundary.values()]
    return ClusterBoundaryContractScore(
        PeakBoundaryDemand=max(Demands, default=0),
        TotalBoundaryDemand=sum(Demands),
        OverflowLanes=sum(
            max(0, Demand - BoundaryCapacity)
            for Demand in Demands
        ),
    )


@dataclass(frozen=True)
class InterClusterGapPlan:
    """Optional corridor spacing assigned to each cluster-grid boundary."""

    Enabled: bool
    RoutingSpacing: int
    TrackPitch: int
    ColumnExtraSpacing: tuple[tuple[int, int], ...]
    RowExtraSpacing: tuple[tuple[int, int], ...]
    BoundaryDemand: tuple[InterClusterBoundaryDemand, ...]

    def ColumnSpacingByBoundary(self) -> dict[int, int]:
        return dict(self.ColumnExtraSpacing)

    def RowSpacingByBoundary(self) -> dict[int, int]:
        return dict(self.RowExtraSpacing)

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Enabled": self.Enabled,
            "RoutingSpacing": self.RoutingSpacing,
            "TrackPitch": self.TrackPitch,
            "ColumnExtraSpacing": {
                str(Boundary): Spacing
                for Boundary, Spacing in self.ColumnExtraSpacing
            },
            "RowExtraSpacing": {
                str(Boundary): Spacing
                for Boundary, Spacing in self.RowExtraSpacing
            },
            "BoundaryDemand": [
                Record.ToDictionary() for Record in self.BoundaryDemand
            ],
        }


@dataclass(frozen=True)
class HardBoundaryFeasibility:
    """Exact necessary conditions for retaining one placement boundary."""

    ClusterId: int
    RequiredSignals: tuple[str, ...]
    LegalEscapeSlotsBySignal: tuple[
        tuple[str, tuple[tuple[int, int, int], ...]], ...
    ]
    MatchedEntrances: tuple[
        tuple[str, tuple[int, int, int]], ...
    ]
    UniqueLegalSlotCount: int
    RejectionReasons: tuple[str, ...]

    @property
    def IsFeasible(self) -> bool:
        return not self.RejectionReasons

    @property
    def LegalEscapeCandidateCounts(self) -> tuple[tuple[str, int], ...]:
        """Expose exact per-signal escape scarcity without mutable slot sets."""
        return tuple(
            (Signal, len(Slots))
            for Signal, Slots in self.LegalEscapeSlotsBySignal
        )

    @property
    def SingleCandidateBoundarySignals(self) -> tuple[str, ...]:
        """Return signals whose boundary access has exactly one legal choice."""
        return tuple(
            Signal
            for Signal, Count in self.LegalEscapeCandidateCounts
            if Count == 1
        )


def EvaluateHardBoundaryFeasibility(
    ClusterId: int,
    DemandRecords: tuple[BoundaryDemandRecord, ...],
    LegalEscapeSlotsBySignal: dict[
        str, set[tuple[int, int, int]] | tuple[tuple[int, int, int], ...]
    ],
) -> HardBoundaryFeasibility:
    """Prove only no-escape and capacity-one entrance impossibility.

    Fanout and preferred-side overflow are deliberately excluded. One global
    tree may branch after entering a cluster, so they remain ranking signals
    rather than hard rejection conditions.
    """
    RequiredSignals = tuple(sorted({
        Record.Signal
        for Record in DemandRecords
        if Record.RequiredPortalSlots > 0
    }))
    NormalizedSlots = {
        Signal: tuple(sorted(set(LegalEscapeSlotsBySignal.get(Signal, ()))))
        for Signal in RequiredSignals
    }
    SlotOwner: dict[tuple[int, int, int], str] = {}
    MatchedSlotBySignal: dict[str, tuple[int, int, int]] = {}

    def TryAssign(
        Signal: str,
        SeenSlots: set[tuple[int, int, int]],
    ) -> bool:
        for Slot in NormalizedSlots[Signal]:
            if Slot in SeenSlots:
                continue
            SeenSlots.add(Slot)
            ExistingSignal = SlotOwner.get(Slot)
            if ExistingSignal is not None and not TryAssign(
                ExistingSignal,
                SeenSlots,
            ):
                continue
            SlotOwner[Slot] = Signal
            MatchedSlotBySignal[Signal] = Slot
            return True
        return False

    for Signal in sorted(
        RequiredSignals,
        key=lambda Value: (len(NormalizedSlots[Value]), Value),
    ):
        TryAssign(Signal, set())

    NoEscapeSignals = tuple(
        Signal for Signal in RequiredSignals if not NormalizedSlots[Signal]
    )
    RejectionReasons = [
        f"NoBoundaryEscape:Cluster={ClusterId}:Signal={Signal}"
        for Signal in NoEscapeSignals
    ]
    UnmatchedSignals = tuple(
        Signal
        for Signal in RequiredSignals
        if Signal not in MatchedSlotBySignal
    )
    UniqueLegalSlots = {
        Slot
        for Signal in RequiredSignals
        for Slot in NormalizedSlots[Signal]
    }
    if UnmatchedSignals and not NoEscapeSignals:
        RejectionReasons.append(
            "HardEntranceCapacityExceeded:"
            f"Cluster={ClusterId}:Required={len(RequiredSignals)}:"
            f"Matched={len(MatchedSlotBySignal)}:"
            f"UniqueSlots={len(UniqueLegalSlots)}:"
            f"Unmatched={','.join(UnmatchedSignals)}"
        )
    return HardBoundaryFeasibility(
        ClusterId=ClusterId,
        RequiredSignals=RequiredSignals,
        LegalEscapeSlotsBySignal=tuple(
            (Signal, NormalizedSlots[Signal])
            for Signal in RequiredSignals
        ),
        MatchedEntrances=tuple(sorted(MatchedSlotBySignal.items())),
        UniqueLegalSlotCount=len(UniqueLegalSlots),
        RejectionReasons=tuple(RejectionReasons),
    )


def ValidateHardBoundaryFeasibility(
    Result: HardBoundaryFeasibility,
) -> None:
    """Reject before a staged placement or local claim can be retained."""
    if Result.IsFeasible:
        return
    SlotsBySignal = dict(Result.LegalEscapeSlotsBySignal)
    NoEscapeSignals = tuple(
        Signal
        for Signal in Result.RequiredSignals
        if not SlotsBySignal.get(Signal)
    )
    MatchedSignals = {
        Signal for Signal, _Slot in Result.MatchedEntrances
    }
    AffectedSignals = NoEscapeSignals or tuple(
        Signal
        for Signal in Result.RequiredSignals
        if Signal not in MatchedSignals
    )
    Reason = (
        RoutingFailureReason.NoBoundaryEscape
        if NoEscapeSignals
        else RoutingFailureReason.ClusterEntranceBudgetExceeded
    )
    raise RoutingStageError(
        RoutingFailure(
            Reason=Reason,
            Stage="PlacementBoundaryFeasibility",
            AffectedNets=AffectedSignals,
            Detail=(
                "Hard boundary infeasible: "
                + "; ".join(Result.RejectionReasons)
            ),
            Diagnostics={
                "ClusterId": Result.ClusterId,
                "RequiredSignals": list(Result.RequiredSignals),
                "MatchedEntrances": [
                    [Signal, list(Slot)]
                    for Signal, Slot in Result.MatchedEntrances
                ],
                "UniqueLegalSlotCount": Result.UniqueLegalSlotCount,
                "RejectionReasons": list(Result.RejectionReasons),
            },
        )
    )


def BuildBoundaryCapacityRecords(
    DemandRecords: tuple[BoundaryDemandRecord, ...],
    GeometricCapacityBySide: dict[str, int],
    LegalPortalSlotsBySide: dict[str, int],
) -> tuple[BoundaryCapacityRecord, ...]:
    """Measure soft corridor capacity through physically legal portal slots."""
    RequiredBySide = {
        Side: sum(
            Record.RequiredCorridorLanes
            for Record in DemandRecords
            if Record.PreferredBoundarySide == Side
        )
        for Side in ("West", "East", "North", "South")
    }
    Records = []
    for Side in ("West", "East", "North", "South"):
        LegalPortalSlots = max(0, LegalPortalSlotsBySide.get(Side, 0))
        GeometricCorridorLanes = max(
            0,
            GeometricCapacityBySide.get(Side, 0),
        )
        LegalCorridorLanes = min(
            GeometricCorridorLanes,
            LegalPortalSlots,
        )
        Records.append(
            BoundaryCapacityRecord(
                BoundarySide=Side,
                LegalPortalSlots=LegalPortalSlots,
                LegalCorridorLanes=LegalCorridorLanes,
                Overflow=max(
                    0,
                    RequiredBySide[Side] - LegalCorridorLanes,
                ),
            )
        )
    return tuple(Records)


def AssignBoundaryDemandSides(
    DemandRecords: tuple[BoundaryDemandRecord, ...],
    LegalEscapeSlotsBySignal: dict[
        str, set[tuple[int, int, int]] | tuple[tuple[int, int, int], ...]
    ],
    Bounds: tuple[int, int, int, int],
    CorridorCapacityBySide: dict[str, int],
) -> tuple[BoundaryDemandRecord, ...]:
    """Assign packed boundary signals to legal sides without lane overflow."""
    if not DemandRecords:
        return ()
    MinimumX, MaximumX, MinimumZ, MaximumZ = Bounds
    SideOrder = ("West", "East", "North", "South")

    def SlotSide(Position: tuple[int, int, int]) -> str:
        X, _Y, Z = Position
        return min(
            (
                (abs(X - MinimumX), 0, "West"),
                (abs(X - MaximumX), 1, "East"),
                (abs(Z - MinimumZ), 2, "North"),
                (abs(Z - MaximumZ), 3, "South"),
            )
        )[2]

    AvailableSides = {
        Record.Signal: tuple(
            Side
            for Side in SideOrder
            if Side in {
                SlotSide(Position)
                for Position in LegalEscapeSlotsBySignal.get(Record.Signal, ())
            }
            and CorridorCapacityBySide.get(Side, 0) > 0
        )
        for Record in DemandRecords
    }
    PreferredUsage = {
        Side: sum(
            Record.PreferredBoundarySide == Side
            for Record in DemandRecords
        )
        for Side in SideOrder
    }
    if all(
        Record.PreferredBoundarySide in AvailableSides[Record.Signal]
        for Record in DemandRecords
    ) and all(
        PreferredUsage[Side] <= CorridorCapacityBySide.get(Side, 0)
        for Side in SideOrder
    ):
        return DemandRecords
    OrderedRecords = sorted(
        DemandRecords,
        key=lambda Record: (
            len(AvailableSides[Record.Signal]),
            -Record.UnresolvedTargets,
            Record.Signal,
        ),
    )
    Usage = {Side: 0 for Side in SideOrder}
    Assignment: dict[str, str] = {}
    Best: tuple[int, tuple[str, ...], dict[str, str]] | None = None
    SeenCostByState: dict[tuple[int, tuple[int, ...]], int] = {}

    def Search(Index: int, PreferenceMisses: int) -> None:
        nonlocal Best
        State = (Index, tuple(Usage[Side] for Side in SideOrder))
        PriorCost = SeenCostByState.get(State)
        if PriorCost is not None and PreferenceMisses > PriorCost:
            return
        SeenCostByState[State] = PreferenceMisses
        if Best is not None and PreferenceMisses > Best[0]:
            return
        if Index == len(OrderedRecords):
            StableSides = tuple(
                Assignment[Record.Signal]
                for Record in sorted(DemandRecords, key=lambda Value: Value.Signal)
            )
            Candidate = (PreferenceMisses, StableSides, dict(Assignment))
            if Best is None or Candidate[:2] < Best[:2]:
                Best = Candidate
            return
        Record = OrderedRecords[Index]
        Options = sorted(
            AvailableSides[Record.Signal],
            key=lambda Side: (
                Side != Record.PreferredBoundarySide,
                Usage[Side],
                SideOrder.index(Side),
            ),
        )
        for Side in Options:
            if Usage[Side] >= CorridorCapacityBySide.get(Side, 0):
                continue
            Assignment[Record.Signal] = Side
            Usage[Side] += 1
            Search(
                Index + 1,
                PreferenceMisses + (Side != Record.PreferredBoundarySide),
            )
            Usage[Side] -= 1
            del Assignment[Record.Signal]

    Search(0, 0)
    if Best is None:
        return DemandRecords
    Selected = Best[2]
    return tuple(
        BoundaryDemandRecord(
            Signal=Record.Signal,
            UnresolvedTargets=Record.UnresolvedTargets,
            RequiredPortalSlots=Record.RequiredPortalSlots,
            RequiredCorridorLanes=Record.RequiredCorridorLanes,
            PreferredBoundarySide=Selected[Record.Signal],
        )
        for Record in DemandRecords
    )


def BuildLegalBoundaryEscapeSlots(
    Signals: set[str],
    AccessPositionsBySignal: dict[str, set[tuple[int, int, int]]],
    ResourceGraph: RoutingResourceGraph,
    FixedAccessClaimsBySignal: dict[str, RoutingResourceClaims],
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    CandidateClaimsBySignal: (
        dict[str, list["BoundaryEscapeCandidate"]] | None
    ) = None,
) -> dict[str, set[tuple[int, int, int]]]:
    """Enumerate exact one-primitive exits from immutable terminal access."""
    Result: dict[str, set[tuple[int, int, int]]] = {}
    OrderedSignals = sorted(Signals)
    for SignalIndex, Signal in enumerate(OrderedSignals):
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "boundary-escape-signal",
                "CompletedSignals": SignalIndex,
                "TotalSignals": len(OrderedSignals),
                "Signal": Signal,
            })
        AllowedAccess = frozenset(AccessPositionsBySignal.get(Signal, ()))
        LegalSlots: set[tuple[int, int, int]] = set()
        for AnchorIndex, Anchor in enumerate(sorted(AllowedAccess)):
            if WorkCheck is not None:
                WorkCheck({
                    "Phase": "boundary-escape-anchor",
                    "Signal": Signal,
                    "CompletedAnchors": AnchorIndex,
                    "TotalAnchors": len(AllowedAccess),
                })
            if not ResourceGraph.IsLegalNode(Anchor, AllowedAccess):
                continue
            for Neighbor in sorted(
                DefaultRedstoneRoutingTechnology.NeighborPositions(Anchor)
            ):
                if not ResourceGraph.IsLegalNode(Neighbor, AllowedAccess):
                    continue
                Primitive = ResourceGraph.BuildPrimitive(Anchor, Neighbor)
                if Primitive is None:
                    continue
                CandidateClaims = ResourceGraph.BuildRouteClaims(
                    (Anchor, Neighbor)
                )
                if any(
                    FindClaimConflicts({
                        Signal: CandidateClaims,
                        OtherSignal: OtherClaims,
                    })
                    for OtherSignal, OtherClaims in (
                        FixedAccessClaimsBySignal.items()
                    )
                    if OtherSignal != Signal
                ):
                    continue
                LegalSlots.add(Neighbor)
                if CandidateClaimsBySignal is not None:
                    CandidateClaimsBySignal.setdefault(
                        Signal,
                        [],
                    ).append(BoundaryEscapeCandidate(
                        Signal=Signal,
                        Anchor=Anchor,
                        Entrance=Neighbor,
                        Claims=CandidateClaims,
                    ))
        Result[Signal] = LegalSlots
    return Result


@dataclass(frozen=True)
class BoundaryEscapeCandidate:
    """One exact first primitive from a fixed packed pin-access envelope."""

    Signal: str
    Anchor: tuple[int, int, int]
    Entrance: tuple[int, int, int]
    Claims: RoutingResourceClaims

    @property
    def StructuralKey(self) -> tuple[object, ...]:
        """Identify physical ownership without depending on a signal name."""
        return (
            self.Anchor,
            self.Entrance,
            tuple(sorted(map(str, self.Claims.ResourceIds))),
        )


@dataclass(frozen=True)
class CutBoundaryEscapeFeasibility:
    """Exact necessary-condition proof for one higher-order placement cut."""

    Verdict: str
    VariableCount: int
    SignalCount: int
    DomainCounts: tuple[tuple[int, str, int], ...]
    Assignment: tuple[
        tuple[int, str, tuple[int, int, int], tuple[int, int, int]],
        ...,
    ]
    ExpansionCount: int
    MaximumExpansions: int
    MaximumAssignedVariables: int
    ConflictSignals: tuple[str, ...]
    StructuralFingerprint: str

    @property
    def IsInfeasible(self) -> bool:
        return self.Verdict == "infeasible"

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Verdict": self.Verdict,
            "VariableCount": self.VariableCount,
            "SignalCount": self.SignalCount,
            "DomainCounts": [
                {
                    "ClusterId": ClusterId,
                    "Signal": Signal,
                    "CandidateCount": CandidateCount,
                }
                for ClusterId, Signal, CandidateCount in self.DomainCounts
            ],
            "Assignment": [
                {
                    "ClusterId": ClusterId,
                    "Signal": Signal,
                    "Anchor": list(Anchor),
                    "Entrance": list(Entrance),
                }
                for ClusterId, Signal, Anchor, Entrance in self.Assignment
            ],
            "ExpansionCount": self.ExpansionCount,
            "MaximumExpansions": self.MaximumExpansions,
            "MaximumAssignedVariables": self.MaximumAssignedVariables,
            "Deficit": max(
                0,
                self.VariableCount - self.MaximumAssignedVariables,
            ),
            "ConflictSignals": list(self.ConflictSignals),
            "StructuralFingerprint": self.StructuralFingerprint,
        }


def EvaluateCutBoundaryEscapeFeasibility(
    Domains: Mapping[
        tuple[int, str],
        Iterable[BoundaryEscapeCandidate],
    ],
    CutSignals: Iterable[str],
    MaximumExpansions: int = 4_096,
) -> CutBoundaryEscapeFeasibility:
    """Prove a cut's fixed first escapes are jointly capacity-one legal.

    This is a conservative placement prescreen, not a substitute for portal
    generation.  Every retained domain contains all exact one-primitive
    escapes found around the committed local geometry.  An exhaustive failed
    search is therefore a hard placement result; an exhausted search budget
    remains unknown and is left to the authoritative router.
    """
    if MaximumExpansions < 1:
        raise ValueError("MaximumExpansions must be positive")
    SelectedSignals = frozenset(map(str, CutSignals))
    NormalizedDomains = {
        (int(ClusterId), str(Signal)): tuple(sorted(
            {
                Candidate.StructuralKey: Candidate
                for Candidate in Candidates
                if str(Signal) in SelectedSignals
                and Candidate.Signal == str(Signal)
            }.values(),
            key=lambda Candidate: Candidate.StructuralKey,
        ))
        for (ClusterId, Signal), Candidates in Domains.items()
        if str(Signal) in SelectedSignals
    }
    Variables = tuple(sorted(
        NormalizedDomains,
        key=lambda Key: (
            len(NormalizedDomains[Key]),
            Key[0],
            Key[1],
        ),
    ))
    DomainCounts = tuple(
        (ClusterId, Signal, len(NormalizedDomains[(ClusterId, Signal)]))
        for ClusterId, Signal in sorted(NormalizedDomains)
    )

    def ClaimsConflict(
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

    Selected: dict[tuple[int, str], BoundaryEscapeCandidate] = {}
    BestAssignment: tuple[
        tuple[int, str, tuple[int, int, int], tuple[int, int, int]],
        ...,
    ] = ()
    ExpansionCount = 0
    BudgetExhausted = False

    def RecordBest() -> None:
        nonlocal BestAssignment
        Candidate = tuple(sorted(
            (
                ClusterId,
                Signal,
                Choice.Anchor,
                Choice.Entrance,
            )
            for (ClusterId, Signal), Choice in Selected.items()
        ))
        if len(Candidate) > len(BestAssignment):
            BestAssignment = Candidate

    def Search(Index: int) -> bool:
        nonlocal ExpansionCount, BudgetExhausted
        RecordBest()
        if Index == len(Variables):
            return True
        if ExpansionCount >= MaximumExpansions:
            BudgetExhausted = True
            return False
        Variable = Variables[Index]
        Signal = Variable[1]
        for Candidate in NormalizedDomains[Variable]:
            ExpansionCount += 1
            if ExpansionCount > MaximumExpansions:
                BudgetExhausted = True
                return False
            if any(
                OtherSignal != Signal
                and ClaimsConflict(Candidate.Claims, Other.Claims)
                for (_OtherCluster, OtherSignal), Other
                in Selected.items()
            ):
                continue
            Selected[Variable] = Candidate
            if Search(Index + 1):
                return True
            del Selected[Variable]
        return False

    Complete = Search(0)
    Verdict = (
        "feasible"
        if Complete
        else "budget-exhausted"
        if BudgetExhausted
        else "infeasible"
    )
    # Signal identifiers are diagnostics.  The fingerprint represents only
    # physical domain structure so renamed/reordered equivalent cuts agree.
    StructuralDomains = tuple(sorted(
        tuple(
            Candidate.StructuralKey
            for Candidate in NormalizedDomains[Variable]
        )
        for Variable in Variables
    ))
    return CutBoundaryEscapeFeasibility(
        Verdict=Verdict,
        VariableCount=len(Variables),
        SignalCount=len({
            Signal for _ClusterId, Signal in Variables
        }),
        DomainCounts=DomainCounts,
        Assignment=BestAssignment,
        ExpansionCount=ExpansionCount,
        MaximumExpansions=MaximumExpansions,
        MaximumAssignedVariables=len(BestAssignment),
        ConflictSignals=(
            tuple(sorted(SelectedSignals))
            if Verdict == "infeasible"
            else ()
        ),
        StructuralFingerprint=sha256(
            repr(StructuralDomains).encode("utf-8")
        ).hexdigest(),
    )


@dataclass(frozen=True)
class PackedNandCluster:
    """Physical packing metadata; members remain independent NAND cells."""

    ClusterId: int
    MemberNands: tuple[str, ...]
    BoundarySignals: tuple[str, ...]
    InternalSignals: tuple[str, ...]
    RelativePlacements: dict[str, tuple[int, int, int, bool]]
    DirectConnections: tuple[str, ...]
    LocalClaimSignals: tuple[str, ...] = ()
    BoundaryTerminals: tuple[tuple[int, int, int], ...] = ()
    ExactLocalRoutingBlocks: int = 0
    GlobalEntrances: int = 0
    RejectionReasons: tuple[str, ...] = ()
    StructuralSignature: str = ""
    ReusedFromClusterId: int | None = None
    StructuralMapping: dict[str, str] | None = None
    StackId: int | None = None
    StackLevel: int = 0
    BaseY: int = 1
    BoundaryDemand: dict[str, int] | None = None
    EstimatedCorridorLanes: int = 0
    LocalClaimCoverage: float = 0.0
    BoundaryDemandRecords: tuple[BoundaryDemandRecord, ...] = ()
    BoundaryCapacityRecords: tuple[BoundaryCapacityRecord, ...] = ()
    BoundaryOverflow: int = 0
    PinScarcityCount: int = 0
    LegalEscapeCandidateCounts: tuple[tuple[str, int], ...] = ()
    OrientationRotation: int = 0
    OrientationMirrorX: bool = False

    @property
    def SingleCandidateBoundarySignals(self) -> tuple[str, ...]:
        """Return boundary signals with only one exact legal escape slot."""
        return tuple(
            Signal
            for Signal, Count in self.LegalEscapeCandidateCounts
            if Count == 1
        )


@dataclass(frozen=True)
class ClusterLayoutVariant:
    """One exact rigid transform of a packed-cluster layout."""

    Rotation: int
    MirrorX: bool
    Positions: dict[str, tuple[int, int]]
    Rotations: dict[str, int]
    Mirrors: dict[str, bool]
    Width: int
    Depth: int
    ActualGeometry: dict[str, frozenset[tuple[int, int, int]]]
    ElectricalGeometry: dict[str, frozenset[tuple[int, int, int]]]
    RejectionReason: str | None = None

    @property
    def IsLegal(self) -> bool:
        return self.RejectionReason is None


@dataclass(frozen=True)
class PackedNandClusterCandidate:
    """Transactional packed placement with locally owned route material."""

    ClusterId: int
    Placements: dict[str, tuple[int, int, int, bool]]
    LocalClaims: tuple[LocalRouteClaim, ...]
    BoundaryTerminals: tuple[tuple[int, int, int], ...]
    RoutingOwnedBlocks: int
    RawDustBlocks: int
    SupportBlocks: int
    Footprint: int
    RejectionReasons: tuple[str, ...] = ()
    BoundaryDemandRecords: tuple[BoundaryDemandRecord, ...] = ()
    BoundaryCapacityRecords: tuple[BoundaryCapacityRecord, ...] = ()
    BoundaryOverflow: int = 0
    LocalClaimCoverage: float = 0.0


@dataclass(frozen=True)
class ClusterLocalRouteTemplate:
    """Immutable cluster-relative local routing retained across cut epochs."""

    ClusterId: int
    StructuralSignature: str
    Rotation: int
    MirrorX: bool
    Origin: tuple[int, int, int]
    LocalClaimFingerprint: str
    BoundaryTerminalFingerprint: str
    ClaimCount: int
    BoundaryTerminalCount: int

    def ToDictionary(self) -> dict[str, object]:
        return {
            "ClusterId": self.ClusterId,
            "StructuralSignature": self.StructuralSignature,
            "Rotation": self.Rotation,
            "MirrorX": self.MirrorX,
            "Origin": list(self.Origin),
            "LocalClaimFingerprint": self.LocalClaimFingerprint,
            "BoundaryTerminalFingerprint": self.BoundaryTerminalFingerprint,
            "ClaimCount": self.ClaimCount,
            "BoundaryTerminalCount": self.BoundaryTerminalCount,
        }


@dataclass(frozen=True)
class ClusterLocalRouteTemplateCacheEntry:
    """One immutable, translation-safe internal cluster route template."""

    CacheKey: tuple[object, ...]
    Origin: tuple[int, int, int]
    Claims: tuple[LocalRouteClaim, ...]
    LocalClaimFingerprint: str


def TranslateClusterLocalRouteClaim(
    Claim: LocalRouteClaim,
    Delta: tuple[int, int, int],
) -> LocalRouteClaim:
    """Translate an immutable internal claim without changing its topology."""
    def Translate(Position: tuple[int, int, int]) -> tuple[int, int, int]:
        return tuple(
            Position[Index] + Delta[Index]
            for Index in range(3)
        )

    def TranslateClaims(Claims: RoutingResourceClaims) -> RoutingResourceClaims:
        return RoutingResourceClaims(
            WireCells=frozenset(map(Translate, Claims.WireCells)),
            SupportCells=frozenset(map(Translate, Claims.SupportCells)),
            RequiredAirCells=frozenset(map(Translate, Claims.RequiredAirCells)),
            ElectricalCells=frozenset(map(Translate, Claims.ElectricalCells)),
        )

    return LocalRouteClaim(
        Signal=Claim.Signal,
        ClusterId=Claim.ClusterId,
        Root=Translate(Claim.Root),
        ConnectedTargets=tuple(map(Translate, Claim.ConnectedTargets)),
        BoundaryNodes=tuple(map(Translate, Claim.BoundaryNodes)),
        Nodes=frozenset(map(Translate, Claim.Nodes)),
        Edges=frozenset(
            NormalizeRoutingEdge(Translate(First), Translate(Second))
            for First, Second in Claim.Edges
        ),
        Claims=TranslateClaims(Claim.Claims),
        RepeaterReservations=tuple(
            RoutingReservation(
                Signal=Reservation.Signal,
                Resource=RoutingResourceId(
                    Reservation.Resource.Kind,
                    Translate(Reservation.Resource.Position),
                ),
                Position=Translate(Reservation.Position),
                Purpose=Reservation.Purpose,
                Facing=Reservation.Facing,
            )
            for Reservation in Claim.RepeaterReservations
        ),
        ExactRouteSignalBlocks=Claim.ExactRouteSignalBlocks,
        ExactRouteRefreshBlocks=Claim.ExactRouteRefreshBlocks,
        ExactRouteSupportBlocks=Claim.ExactRouteSupportBlocks,
    )


@dataclass(frozen=True)
class PcbPlacement:
    """Weighted placement plus global routing metadata."""

    Placed: PlacedDesign
    Clusters: tuple[tuple[str, ...], ...]
    SignalOrder: tuple[str, ...]
    LayerCount: int
    PackedClusters: tuple[PackedNandCluster, ...] = ()
    ClusterBoundaryLeaseRequests: tuple[ClusterBoundaryLeaseRequest, ...] = ()
    ClusterLocalRouteTemplates: tuple[ClusterLocalRouteTemplate, ...] = ()
    ClusterBoundaryLeaseVariant: int = 0
    CompleteClusterInterfaceAccess: bool = False
    MandatoryAccessPreScreenProfile: (
        "MandatoryAccessConflictProfile | None"
    ) = None
    InterClusterRoutingChannel: InterClusterRoutingChannel | None = None
    PlacementAccessFabric: Any | None = None
    PlacementAccessAssignment: Any | None = None
    DerivedPerimeterSlotDomain: Any | None = None
    DerivedPerimeterSlotAssignment: Any | None = None
    # A compact single-component candidate may publish previously materialized
    # complete local trees as alternatives of its immutable pre-route access
    # problem.  They are not live placement ownership until that problem
    # selects them; keeping the source claims here avoids a post-failure
    # release/rebuild cycle.
    DerivedLocalRouteClaims: tuple[Any, ...] = ()
    ComponentGraph: Any | None = None


def BuildPhysicalClusterBoundaryLeaseRequests(
    Source: PcbPlacement,
) -> tuple[ClusterBoundaryLeaseRequest, ...]:
    """Materialize explicit interfaces from committed physical clusters."""
    Existing = tuple(Source.ClusterBoundaryLeaseRequests)
    GateByName = {
        Gate.Name: Gate for Gate in Source.Placed.PlacedGates
    }
    PhysicalOrigins = {
        ClusterIndex: (
            min(GateByName[Name].X for Name in Names if Name in GateByName),
            min(GateByName[Name].Z for Name in Names if Name in GateByName),
        )
        for ClusterIndex, Names in enumerate(Source.Clusters)
        if any(Name in GateByName for Name in Names)
    }
    Generated = BuildClusterBoundaryLeaseRequests(
        BuildClusterBoundaryBundles(
            Source.Placed.Module,
            Source.Clusters,
        ),
        PhysicalOrigins,
        Module=Source.Placed.Module,
        Clusters=Source.Clusters,
        PlacedGates=Source.Placed.PlacedGates,
        IncludePrimaryTerminals=True,
    )
    RequestsByIdentity = {
        (
            str(Value.Signal),
            int(Value.SourceCluster),
            int(Value.TargetCluster),
            Value.SourceTerminal,
            tuple(Value.TargetTerminals),
        ): Value
        for Value in (*Existing, *Generated)
    }
    return tuple(
        RequestsByIdentity[Key]
        for Key in sorted(
            RequestsByIdentity,
            key=repr,
        )
    )


def BuildBoundedInterClusterRoutingChannel(
    Source: PcbPlacement,
    *,
    TrackPitch: int | None = None,
    MaximumAffectedClusters: int = 3,
    MaximumBoundaryStrips: int = 2,
    RoutingLayerCount: int = 3,
    ForcedAffectedClusters: tuple[int, ...] | None = None,
    ForcedRoot: int | None = None,
    ForcedAxisPattern: tuple[str, ...] | None = None,
    ChannelClearanceTracks: int = 0,
    ChannelTopologyVariant: int = 0,
) -> PcbPlacement:
    """Insert one bounded physical channel through the densest interface.

    This is an architectural placement transform, not router feedback.  It
    shifts only a connected component of at most three packed clusters,
    translates their immutable local claims, and exposes ordinary
    capacity-one lane cells on the existing routing layers.
    """
    TrackPitch = (
        DefaultRedstoneRoutingTechnology.TrackPitch
        if TrackPitch is None
        else int(TrackPitch)
    )
    if TrackPitch < 1:
        raise ValueError("inter-cluster channel pitch must be positive")
    if MaximumAffectedClusters < 2 or MaximumAffectedClusters > 3:
        raise ValueError("inter-cluster channel supports two or three clusters")
    if MaximumBoundaryStrips < 1 or MaximumBoundaryStrips > 2:
        raise ValueError("inter-cluster channel supports one or two strips")
    if RoutingLayerCount < 1:
        raise ValueError("inter-cluster channel requires routing layers")
    if ChannelClearanceTracks < 0 or ChannelClearanceTracks > 1:
        raise ValueError("channel clearance tracks must be zero or one")
    if ChannelTopologyVariant < 0:
        raise ValueError("channel topology variant must be non-negative")
    if Source.InterClusterRoutingChannel is not None:
        return Source

    GateByName = {
        Gate.Name: Gate for Gate in Source.Placed.PlacedGates
    }
    ClusterGates = {
        ClusterIndex: tuple(
            GateByName[Name]
            for Name in Names
            if Name in GateByName
        )
        for ClusterIndex, Names in enumerate(Source.Clusters)
    }
    ClusterGates = {
        ClusterIndex: Gates
        for ClusterIndex, Gates in ClusterGates.items()
        if Gates
    }
    AllBoundaryRequests = (
        BuildPhysicalClusterBoundaryLeaseRequests(Source)
    )
    BoundaryRequests = tuple(
        Request
        for Request in AllBoundaryRequests
        if (
            Request.SourceCluster in ClusterGates
            and Request.TargetCluster in ClusterGates
            and Request.SourceCluster != Request.TargetCluster
        )
    )
    if not BoundaryRequests:
        raise ValueError(
            "dense interface has no inter-cluster boundary requests"
        )

    EdgeDemand: dict[tuple[int, int], int] = {}
    for Request in BoundaryRequests:
        Edge = tuple(sorted((
            int(Request.SourceCluster),
            int(Request.TargetCluster),
        )))
        EdgeDemand[Edge] = EdgeDemand.get(Edge, 0) + (
            1 + len(Request.TargetTerminals)
        )
    LogicalComponentGraph = (
        Source.ComponentGraph or Source.Placed.ComponentGraph
    )
    LogicalComponentByGate = (
        dict(LogicalComponentGraph.GateToComponent)
        if (
            LogicalComponentGraph is not None
            and LogicalComponentGraph.Hierarchical
        )
        else {}
    )
    LogicalClustersByComponent: dict[int, set[int]] = {}
    MixedLogicalClusters: set[int] = set()
    LogicalComponentByCluster: dict[int, int] = {}
    for ClusterIndex, Gates in ClusterGates.items():
        ComponentIds = {
            LogicalComponentByGate[Gate.Name]
            for Gate in Gates
            if Gate.Name in LogicalComponentByGate
        }
        if len(ComponentIds) != 1:
            MixedLogicalClusters.add(ClusterIndex)
            continue
        ComponentId = next(iter(ComponentIds))
        LogicalComponentByCluster[ClusterIndex] = ComponentId
        LogicalClustersByComponent.setdefault(
            ComponentId, set()
        ).add(ClusterIndex)
    AlignedLogicalComponents = {
        frozenset(ClusterIndexes): ComponentId
        for ComponentId, ClusterIndexes
        in LogicalClustersByComponent.items()
        if (
            2 <= len(ClusterIndexes) <= MaximumAffectedClusters
            and not (ClusterIndexes & MixedLogicalClusters)
        )
    }
    def LogicalComponentForClusterSet(
        ClusterSet: frozenset[int],
    ) -> int | None:
        ComponentIds = {
            LogicalComponentByCluster[Cluster]
            for Cluster in ClusterSet
            if Cluster in LogicalComponentByCluster
        }
        return (
            next(iter(ComponentIds))
            if (
                len(ComponentIds) == 1
                and ClusterSet
                == frozenset(LogicalClustersByComponent.get(
                    next(iter(ComponentIds)),
                    (),
                ))
                and frozenset(
                    Gate.Name
                    for Cluster in ClusterSet
                    for Gate in ClusterGates[Cluster]
                    if Gate.Name in LogicalComponentByGate
                )
                == LogicalGateNamesByComponent.get(
                    next(iter(ComponentIds)),
                    frozenset(),
                )
                and len(ComponentIds) == len({
                    LogicalComponentByCluster.get(Cluster)
                    for Cluster in ClusterSet
                })
                and not (ClusterSet & MixedLogicalClusters)
            )
            else None
        )
    LogicalComponentBenefits = {
        Value.ComponentId: (
            len(Value.BoundarySignals),
            -Value.QualifyingReconvergentCutCount,
            -len(Value.GateNames),
        )
        for Value in getattr(
            LogicalComponentGraph,
            "Components",
            (),
        )
    }
    LogicalGateNamesByComponent = {
        Value.ComponentId: frozenset(Value.GateNames)
        for Value in getattr(
            LogicalComponentGraph,
            "Components",
            (),
        )
    }
    CandidateComponents: list[
        tuple[tuple[object, ...], tuple[int, ...]]
    ] = []
    ClusterIndexes = tuple(sorted(ClusterGates))
    for ComponentSize in range(
        min(MaximumAffectedClusters, len(ClusterIndexes)),
        1,
        -1,
    ):
        for Component in combinations(ClusterIndexes, ComponentSize):
            ComponentSet = frozenset(Component)
            ComponentEdges = tuple(
                Edge
                for Edge in EdgeDemand
                if set(Edge) <= ComponentSet
            )
            if len(ComponentEdges) < ComponentSize - 1:
                continue
            Visited = {Component[0]}
            while True:
                Expanded = {
                    Value
                    for Edge in ComponentEdges
                    if set(Edge) & Visited
                    for Value in Edge
                }
                if Expanded <= Visited:
                    break
                Visited.update(Expanded)
            if Visited != set(Component):
                continue
            if (
                LogicalComponentGraph is not None
                and LogicalComponentGraph.Hierarchical
                and LogicalComponentForClusterSet(ComponentSet) is None
            ):
                continue
            StructuralSignatures = tuple(sorted(
                (
                    Source.PackedClusters[Index].StructuralSignature
                    if Index < len(Source.PackedClusters)
                    else str(len(Source.Clusters[Index]))
                )
                for Index in Component
            ))
            CandidateComponents.append((
                (
                    0
                    if LogicalComponentForClusterSet(ComponentSet)
                    is not None
                    else 1,
                    LogicalComponentBenefits.get(
                        LogicalComponentForClusterSet(ComponentSet),
                        (1 << 30, 0, 0),
                    ),
                    -sum(EdgeDemand[Edge] for Edge in ComponentEdges),
                    -ComponentSize,
                    StructuralSignatures,
                    Component,
                ),
                Component,
            ))
    if not CandidateComponents:
        raise ValueError(
            "no connected two-or-three-cluster channel component"
        )
    RankedComponents = tuple(sorted(CandidateComponents))
    if ForcedAffectedClusters is None:
        _ComponentScore, AffectedClusters = RankedComponents[0]
    else:
        ForcedClusterSet = frozenset(map(int, ForcedAffectedClusters))
        ForcedMatches = tuple(
            Value
            for Value in RankedComponents
            if frozenset(Value[1]) == ForcedClusterSet
        )
        if not ForcedMatches:
            raise ValueError(
                "forced channel component is not a legal connected component"
            )
        _ComponentScore, AffectedClusters = ForcedMatches[0]
    AffectedSet = frozenset(AffectedClusters)
    ComponentId = LogicalComponentForClusterSet(AffectedSet)
    TopologyComponent = (
        next(
            (
                Value
                for Value in LogicalComponentGraph.Components
                if Value.ComponentId == ComponentId
            ),
            None,
        )
        if (
            LogicalComponentGraph is not None
            and ComponentId is not None
        )
        else None
    )

    def Envelope(
        Gates: Iterable[PlacedGate],
    ) -> tuple[int, int, int, int]:
        Values = tuple(Gates)
        return (
            min(Gate.X for Gate in Values),
            min(Gate.Z for Gate in Values),
            max(
                Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0]
                for Gate in Values
            ),
            max(
                Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1]
                for Gate in Values
            ),
        )

    Envelopes = {
        Cluster: Envelope(ClusterGates[Cluster])
        for Cluster in AffectedClusters
    }
    Centers = {
        Cluster: (
            (Bounds[0] + Bounds[2]) // 2,
            (Bounds[1] + Bounds[3]) // 2,
        )
        for Cluster, Bounds in Envelopes.items()
    }
    ComponentEdges = tuple(
        Edge
        for Edge in EdgeDemand
        if set(Edge) <= AffectedSet
    )
    RankedEdges = sorted(
        ComponentEdges,
        key=lambda Edge: (
            -EdgeDemand[Edge],
            abs(Centers[Edge[0]][0] - Centers[Edge[1]][0])
            + abs(Centers[Edge[0]][1] - Centers[Edge[1]][1]),
            Edge,
        ),
    )
    Parents = {Cluster: Cluster for Cluster in AffectedClusters}

    def Find(Value: int) -> int:
        while Parents[Value] != Value:
            Parents[Value] = Parents[Parents[Value]]
            Value = Parents[Value]
        return Value

    TreeEdges: list[tuple[int, int]] = []
    for First, Second in RankedEdges:
        FirstRoot = Find(First)
        SecondRoot = Find(Second)
        if FirstRoot == SecondRoot:
            continue
        Parents[max(FirstRoot, SecondRoot)] = min(FirstRoot, SecondRoot)
        TreeEdges.append((First, Second))
        if len(TreeEdges) == len(AffectedClusters) - 1:
            break
    if len(TreeEdges) != len(AffectedClusters) - 1:
        raise ValueError("channel component spanning tree is incomplete")
    TreeEdges = TreeEdges[:MaximumBoundaryStrips]

    Adjacency: dict[int, list[int]] = {
        Cluster: [] for Cluster in AffectedClusters
    }
    for First, Second in TreeEdges:
        Adjacency[First].append(Second)
        Adjacency[Second].append(First)
    RankedRoots = tuple(sorted(
        AffectedClusters,
        key=lambda Cluster: (
            Source.PackedClusters[Cluster].StructuralSignature
            if Cluster < len(Source.PackedClusters)
            else "",
            Centers[Cluster],
            Cluster,
        ),
    ))
    if ForcedRoot is None:
        RootFailures = []
        RemainingTopologyVariants = ChannelTopologyVariant
        PreferredAxes = tuple(
            (
                "X"
                if abs(
                    Centers[Edge[0]][0] - Centers[Edge[1]][0]
                ) >= abs(
                    Centers[Edge[0]][1] - Centers[Edge[1]][1]
                )
                else "Z"
            )
            for Edge in TreeEdges
        )
        AxisPatterns = tuple(sorted(
            product(("X", "Z"), repeat=len(TreeEdges)),
            key=lambda Pattern: (
                sum(
                    Axis != PreferredAxes[Index]
                    for Index, Axis in enumerate(Pattern)
                ),
                Pattern,
            ),
        ))
        for RootCandidate in RankedRoots:
            for AxisPattern in AxisPatterns:
                try:
                    Candidate = BuildBoundedInterClusterRoutingChannel(
                        Source,
                        TrackPitch=TrackPitch,
                        MaximumAffectedClusters=MaximumAffectedClusters,
                        MaximumBoundaryStrips=MaximumBoundaryStrips,
                        RoutingLayerCount=RoutingLayerCount,
                        ForcedAffectedClusters=tuple(AffectedClusters),
                        ForcedRoot=RootCandidate,
                        ForcedAxisPattern=tuple(AxisPattern),
                        ChannelClearanceTracks=ChannelClearanceTracks,
                    )
                    if RemainingTopologyVariants == 0:
                        return Candidate
                    RemainingTopologyVariants -= 1
                except ValueError as Error:
                    RootFailures.append(
                        "root-"
                        f"{RankedRoots.index(RootCandidate)}"
                        f"-axes-{''.join(AxisPattern)}:{Error}"
                    )
        raise ValueError(
            "no legal bounded channel root: "
            + "; ".join(RootFailures)
            + (
                ""
                if not RootFailures else
                f"; requested topology variant {ChannelTopologyVariant} "
                "exceeds legal channel alternatives"
            )
        )
    if ForcedRoot not in AffectedSet:
        raise ValueError("forced channel root is outside the component")
    if (
        ForcedAxisPattern is None
        or len(ForcedAxisPattern) != len(TreeEdges)
        or any(Axis not in {"X", "Z"} for Axis in ForcedAxisPattern)
    ):
        raise ValueError("forced channel axis pattern is invalid")
    Root = ForcedRoot
    AxisByEdge = {
        tuple(sorted(Edge)): ForcedAxisPattern[Index]
        for Index, Edge in enumerate(TreeEdges)
    }
    ClusterTranslations: dict[int, tuple[int, int, int]] = {
        Root: (0, 0, 0)
    }
    StripSeeds: list[tuple[str, int, int, int]] = []
    Queue = deque([(Root, -1)])
    while Queue:
        Parent, GrandParent = Queue.popleft()
        for Child in sorted(Adjacency[Parent]):
            if Child == GrandParent:
                continue
            DeltaX = Centers[Child][0] - Centers[Parent][0]
            DeltaZ = Centers[Child][1] - Centers[Parent][1]
            Axis = AxisByEdge[tuple(sorted((Parent, Child)))]
            Direction = (
                1
                if (DeltaX if Axis == "X" else DeltaZ) >= 0
                else -1
            )
            ParentDelta = ClusterTranslations[Parent]
            ShiftDistance = TrackPitch * (1 + ChannelClearanceTracks)
            Shift = (
                Direction * ShiftDistance,
                0,
                0,
            ) if Axis == "X" else (
                0,
                0,
                Direction * ShiftDistance,
            )
            ClusterTranslations[Child] = tuple(
                ParentDelta[Index] + Shift[Index]
                for Index in range(3)
            )
            if any(
                abs(ClusterTranslations[Child][Index]) > ShiftDistance
                for Index in (0, 2)
            ):
                raise ValueError(
                    "inter-cluster channel exceeds per-axis growth bound"
                )
            # The lane belongs in the newly opened translated gap, not at
            # the child's source boundary.  Using the source coordinate
            # made a clearance transform move the cluster while leaving its
            # channel lane inside the old placed geometry.
            ParentBounds = Envelopes[Parent]
            ChildBounds = Envelopes[Child]
            if Axis == "X":
                GapMinimum, GapMaximum = (
                    (
                        ParentBounds[2] + ParentDelta[0] + 1,
                        ChildBounds[0] + ClusterTranslations[Child][0] - 1,
                    )
                    if Direction > 0 else (
                        ChildBounds[2] + ClusterTranslations[Child][0] + 1,
                        ParentBounds[0] + ParentDelta[0] - 1,
                    )
                )
            else:
                GapMinimum, GapMaximum = (
                    (
                        ParentBounds[3] + ParentDelta[2] + 1,
                        ChildBounds[1] + ClusterTranslations[Child][2] - 1,
                    )
                    if Direction > 0 else (
                        ChildBounds[3] + ClusterTranslations[Child][2] + 1,
                        ParentBounds[1] + ParentDelta[2] - 1,
                    )
                )
            if GapMinimum > GapMaximum:
                raise ValueError("translated channel has no physical gap")
            Coordinate = (GapMinimum + GapMaximum) // 2
            StripSeeds.append((Axis, Coordinate, Parent, Child))
            Queue.append((Child, Parent))

    ModuleGateByName = {
        Gate.Name: Gate for Gate in Source.Placed.Module.Gates
    }
    ClusterByGate = {
        Name: ClusterIndex
        for ClusterIndex, Names in enumerate(Source.Clusters)
        for Name in Names
    }

    def TranslatePosition(
        Position: tuple[int, int, int],
        Delta: tuple[int, int, int],
    ) -> tuple[int, int, int]:
        return tuple(
            int(Position[Index]) + int(Delta[Index])
            for Index in range(3)
        )

    CandidateGates = []
    for Gate in Source.Placed.PlacedGates:
        Cluster = ClusterByGate.get(Gate.Name)
        Delta = ClusterTranslations.get(Cluster, (0, 0, 0))
        if Delta == (0, 0, 0) or Gate.Name not in ModuleGateByName:
            CandidateGates.append(Gate)
            continue
        CandidateGates.append(BuildPlacedGate(
            ModuleGateByName[Gate.Name],
            Gate.X + Delta[0],
            Gate.Y + Delta[1],
            Gate.Z + Delta[2],
            Gate.Rotation,
            Gate.MirrorX,
        ))
    CandidatePlacedForValidation = PlacedDesign(
        Module=Source.Placed.Module,
        PlacedGates=CandidateGates,
    )
    Occupied, _Electrical, _Solid = BuildPlacedCellGeometry(
        CandidatePlacedForValidation
    )
    ValidatePlacedCellElectricalIsolation(
        CandidatePlacedForValidation
    )

    def InterfaceStripExtent(
        Axis: str,
        FirstCluster: int,
        SecondCluster: int,
    ) -> tuple[int, int]:
        PerpendicularCoordinates = []
        Edge = frozenset((FirstCluster, SecondCluster))
        for Request in BoundaryRequests:
            if frozenset((
                Request.SourceCluster,
                Request.TargetCluster,
            )) != Edge:
                continue
            Terminals = (
                *((Request.SourceTerminal,)
                  if Request.SourceTerminal is not None else ()),
                *Request.TargetTerminals,
            )
            for Terminal in Terminals:
                Cluster = (
                    Request.SourceCluster
                    if Terminal == Request.SourceTerminal
                    else Request.TargetCluster
                )
                Delta = ClusterTranslations.get(
                    Cluster,
                    (0, 0, 0),
                )
                PerpendicularCoordinates.append(
                    Terminal[2] + Delta[2]
                    if Axis == "X"
                    else Terminal[0] + Delta[0]
                )
        if not PerpendicularCoordinates:
            PerpendicularCoordinates.extend((
                Centers[FirstCluster][1]
                if Axis == "X"
                else Centers[FirstCluster][0],
                Centers[SecondCluster][1]
                if Axis == "X"
                else Centers[SecondCluster][0],
            ))
        Margin = TrackPitch // 2
        return (
            min(PerpendicularCoordinates) - Margin,
            max(PerpendicularCoordinates) + Margin,
        )

    InsertedBoundaryStrips = tuple(
        (
            Axis,
            Coordinate,
            *InterfaceStripExtent(Axis, First, Second),
        )
        for Axis, Coordinate, First, Second in StripSeeds
    )
    LaneRecords = []
    for Axis, Coordinate, Minimum, Maximum in InsertedBoundaryStrips:
        Direction = "Z" if Axis == "X" else "X"
        for Layer in range(min(
            RoutingLayerCount,
            max(1, Source.LayerCount),
        )):
            RoutingY = DefaultRedstoneRoutingTechnology.RoutingY(
                0,
                Layer,
            )
            Cells = tuple(
                (
                    (Coordinate, RoutingY, Offset)
                    if Axis == "X"
                    else (Offset, RoutingY, Coordinate)
                )
                for Offset in range(Minimum, Maximum + 1)
            )
            if any(Cell in Occupied for Cell in Cells):
                raise ValueError(
                    "inter-cluster channel lane overlaps placed geometry"
                )
            IngressNodes = tuple(
                Cells[Index]
                for Index in range(0, len(Cells), TrackPitch)
            )
            PhysicalClaims = RoutingResourceClaims(
                WireCells=frozenset(Cells),
                SupportCells=frozenset(
                    (X, Y - 1, Z) for X, Y, Z in Cells
                ),
                RequiredAirCells=frozenset(),
                ElectricalCells=frozenset(
                    DefaultRedstoneRoutingTechnology
                    .BuildElectricalExclusions(set(Cells))
                ),
            )
            ClaimsFingerprint = sha256(repr((
                Layer,
                Direction,
                tuple(sorted(PhysicalClaims.ResourceIds, key=repr)),
            )).encode("utf-8")).hexdigest()[:16]
            LaneRecords.append(InterClusterChannelLane(
                Layer=Layer,
                Direction=Direction,
                Cells=Cells,
                IngressNodes=IngressNodes,
                PhysicalClaims=PhysicalClaims,
                ClaimsFingerprint=ClaimsFingerprint,
            ))

    CandidateAffectedSignals = {
        Request.Signal
        for Request in AllBoundaryRequests
        if (
            Request.SourceCluster in AffectedSet
            or Request.TargetCluster in AffectedSet
        )
    }
    if TopologyComponent is not None:
        ClosedSignals = frozenset((
            *TopologyComponent.InternalSignals,
            *TopologyComponent.BoundarySignals,
        ))
        CandidateAffectedSignals.intersection_update(ClosedSignals)
    AffectedSignals = tuple(sorted(CandidateAffectedSignals))
    MinimumX = min(
        (Cell[0] for Lane in LaneRecords for Cell in Lane.Cells),
        default=0,
    )
    MinimumZ = min(
        (Cell[2] for Lane in LaneRecords for Cell in Lane.Cells),
        default=0,
    )
    StructuralClusters = tuple(sorted(
        Source.PackedClusters[Cluster].StructuralSignature
        if Cluster < len(Source.PackedClusters)
        else str(len(Source.Clusters[Cluster]))
        for Cluster in AffectedClusters
    ))
    ChannelFingerprint = sha256(repr((
        StructuralClusters,
        ChannelClearanceTracks,
        tuple(
            (
                Axis,
                Coordinate - (MinimumX if Axis == "X" else MinimumZ),
                Minimum - (MinimumZ if Axis == "X" else MinimumX),
                Maximum - (MinimumZ if Axis == "X" else MinimumX),
            )
            for Axis, Coordinate, Minimum, Maximum
            in InsertedBoundaryStrips
        ),
        tuple(
            (
                Lane.Layer,
                Lane.Direction,
                tuple(
                    (
                        Cell[0] - MinimumX,
                        Cell[1],
                        Cell[2] - MinimumZ,
                    )
                    for Cell in Lane.Cells
                ),
            )
            for Lane in LaneRecords
        ),
    )).encode("utf-8")).hexdigest()[:16]
    Channel = InterClusterRoutingChannel(
        ChannelFingerprint=ChannelFingerprint,
        AffectedClusters=tuple(sorted(AffectedClusters)),
        AffectedSignals=AffectedSignals,
        InsertedBoundaryStrips=InsertedBoundaryStrips,
        ClusterTranslations=tuple(sorted(
            ClusterTranslations.items()
        )),
        Lanes=tuple(LaneRecords),
        TrackPitch=TrackPitch,
        ChannelClearanceTracks=ChannelClearanceTracks,
        MaximumAffectedClusters=MaximumAffectedClusters,
        MaximumBoundaryStrips=MaximumBoundaryStrips,
        ComponentId=ComponentId,
        InterfaceFingerprint=(
            TopologyComponent.StructuralFingerprint
            if TopologyComponent is not None
            else ""
        ),
        DeclaredFeedthroughSignals=(),
    )

    CandidateClaims = tuple(
        TranslateClusterLocalRouteClaim(
            Claim,
            ClusterTranslations.get(Claim.ClusterId, (0, 0, 0)),
        )
        for Claim in Source.Placed.LocalRouteClaims or ()
    )
    CandidateLeaseRequests = tuple(
        replace(
            Request,
            SourceTerminal=(
                TranslatePosition(
                    Request.SourceTerminal,
                    ClusterTranslations.get(
                        Request.SourceCluster,
                        (0, 0, 0),
                    ),
                )
                if Request.SourceTerminal is not None
                else None
            ),
            TargetTerminals=tuple(
                TranslatePosition(
                    Terminal,
                    ClusterTranslations.get(
                        Request.TargetCluster,
                        (0, 0, 0),
                    ),
                )
                for Terminal in Request.TargetTerminals
            ),
        )
        for Request in AllBoundaryRequests
    )
    Diagnostics = dict(Source.Placed.LocalRouteDiagnostics or {})
    DeferredDiagnostics = Diagnostics.get(
        "__DeferredLocalRouting__",
        {},
    )
    if isinstance(DeferredDiagnostics, dict):
        Diagnostics["__DeferredLocalRouting__"] = {
            **DeferredDiagnostics,
            "ScoringOnly": False,
            "Channelized": True,
        }
    Diagnostics["__InterClusterRoutingChannel__"] = (
        Channel.ToDictionary()
    )
    CandidatePlaced = PlacedDesign(
        Module=Source.Placed.Module,
        PlacedGates=CandidateGates,
        RouteGuides=None,
        RouteLayers=None,
        FrozenNetWires=None,
        LocalNetBranches=None,
        LocalNetTargets=None,
        LocalRouteClaims=CandidateClaims,
        LocalRouteDiagnostics=Diagnostics,
        ClusterBoundaryLeaseRequests=CandidateLeaseRequests,
        CompleteClusterInterfaceAccess=True,
        InterClusterRoutingChannel=Channel,
        PackedClusters=Source.PackedClusters,
        ComponentGraph=LogicalComponentGraph,
    )
    return replace(
        Source,
        Placed=CandidatePlaced,
        PackedClusters=tuple(
            replace(
                Cluster,
                BoundaryTerminals=tuple(
                    TranslatePosition(
                        Terminal,
                        ClusterTranslations.get(
                            Cluster.ClusterId,
                            (0, 0, 0),
                        ),
                    )
                    for Terminal in Cluster.BoundaryTerminals
                ),
            )
            for Cluster in Source.PackedClusters
        ),
        ClusterBoundaryLeaseRequests=CandidateLeaseRequests,
        ClusterLocalRouteTemplates=tuple(
            replace(
                Template,
                Origin=TranslatePosition(
                    Template.Origin,
                    ClusterTranslations.get(
                        Template.ClusterId,
                        (0, 0, 0),
                    ),
                ),
            )
            for Template in Source.ClusterLocalRouteTemplates
        ),
        CompleteClusterInterfaceAccess=True,
        MandatoryAccessPreScreenProfile=None,
        InterClusterRoutingChannel=Channel,
        ComponentGraph=LogicalComponentGraph,
    )


def BuildBoundedInterClusterRoutingDeck(
    Source: PcbPlacement,
    *,
    TrackPitch: int | None = None,
    MaximumAffectedClusters: int = 3,
    MaximumDeckLanes: int = 2,
    InterfaceDeckLayer: int = 3,
    ComponentVariant: int = 0,
    PreferredSignals: Iterable[str] = (),
    ForcedAffectedClusters: tuple[int, ...] | None = None,
) -> PcbPlacement:
    """Add one component-owned routing deck above the compact three layers."""
    TrackPitch = (
        DefaultRedstoneRoutingTechnology.TrackPitch
        if TrackPitch is None
        else int(TrackPitch)
    )
    if TrackPitch < 1:
        raise ValueError("interface deck pitch must be positive")
    if MaximumAffectedClusters not in {2, 3}:
        raise ValueError("interface deck supports two or three clusters")
    if not 1 <= MaximumDeckLanes <= 12:
        raise ValueError("interface deck supports one through twelve lanes")
    if InterfaceDeckLayer != 3:
        raise ValueError(
            "the bounded interface deck must follow three compact layers"
        )
    if ComponentVariant < 0:
        raise ValueError("component variant cannot be negative")
    PreferredSignalSet = frozenset(
        str(Signal) for Signal in PreferredSignals
    )

    GateByName = {
        Gate.Name: Gate for Gate in Source.Placed.PlacedGates
    }
    ClusterGates = {
        ClusterIndex: tuple(
            GateByName[Name]
            for Name in Names
            if Name in GateByName
        )
        for ClusterIndex, Names in enumerate(Source.Clusters)
    }
    ClusterGates = {
        ClusterIndex: Gates
        for ClusterIndex, Gates in ClusterGates.items()
        if Gates
    }
    AllBoundaryRequests = (
        BuildPhysicalClusterBoundaryLeaseRequests(Source)
    )
    BoundaryRequests = tuple(
        Request
        for Request in AllBoundaryRequests
        if (
            Request.SourceCluster in ClusterGates
            and Request.TargetCluster in ClusterGates
            and Request.SourceCluster != Request.TargetCluster
        )
    )
    EdgeDemand: dict[tuple[int, int], int] = {}
    for Request in BoundaryRequests:
        Edge = tuple(sorted((
            int(Request.SourceCluster),
            int(Request.TargetCluster),
        )))
        EdgeDemand[Edge] = EdgeDemand.get(Edge, 0) + (
            1 + len(Request.TargetTerminals)
        )
    LogicalComponentGraph = (
        Source.ComponentGraph or Source.Placed.ComponentGraph
    )
    LogicalComponentByGate = (
        dict(LogicalComponentGraph.GateToComponent)
        if (
            LogicalComponentGraph is not None
            and LogicalComponentGraph.Hierarchical
        )
        else {}
    )
    LogicalClustersByComponent: dict[int, set[int]] = {}
    LogicalComponentByCluster: dict[int, int] = {}
    for ClusterIndex, Gates in ClusterGates.items():
        ComponentIds = {
            LogicalComponentByGate[Gate.Name]
            for Gate in Gates
            if Gate.Name in LogicalComponentByGate
        }
        if len(ComponentIds) == 1:
            ComponentId = next(iter(ComponentIds))
            LogicalComponentByCluster[ClusterIndex] = ComponentId
            LogicalClustersByComponent.setdefault(
                ComponentId,
                set(),
            ).add(ClusterIndex)
    AlignedLogicalComponents = {
        frozenset(ClusterIndexes): ComponentId
        for ComponentId, ClusterIndexes
        in LogicalClustersByComponent.items()
        if 2 <= len(ClusterIndexes) <= MaximumAffectedClusters
    }
    def LogicalComponentForClusterSet(
        ClusterSet: frozenset[int],
    ) -> int | None:
        ComponentIds = {
            LogicalComponentByCluster[Cluster]
            for Cluster in ClusterSet
            if Cluster in LogicalComponentByCluster
        }
        return (
            next(iter(ComponentIds))
            if (
                len(ComponentIds) == 1
                and ClusterSet
                == frozenset(LogicalClustersByComponent.get(
                    next(iter(ComponentIds)),
                    (),
                ))
                and frozenset(
                    Gate.Name
                    for Cluster in ClusterSet
                    for Gate in ClusterGates[Cluster]
                    if Gate.Name in LogicalComponentByGate
                )
                == LogicalGateNamesByComponent.get(
                    next(iter(ComponentIds)),
                    frozenset(),
                )
                and all(
                    Cluster in LogicalComponentByCluster
                    for Cluster in ClusterSet
                )
            )
            else None
        )
    LogicalComponentBenefits = {
        Value.ComponentId: (
            len(Value.BoundarySignals),
            -Value.QualifyingReconvergentCutCount,
            -len(Value.GateNames),
        )
        for Value in getattr(
            LogicalComponentGraph,
            "Components",
            (),
        )
    }
    LogicalGateNamesByComponent = {
        Value.ComponentId: frozenset(Value.GateNames)
        for Value in getattr(
            LogicalComponentGraph,
            "Components",
            (),
        )
    }
    CandidateComponents = []
    ClusterIndexes = tuple(sorted(ClusterGates))
    for ComponentSize in range(
        min(MaximumAffectedClusters, len(ClusterIndexes)),
        1,
        -1,
    ):
        for Component in combinations(ClusterIndexes, ComponentSize):
            ComponentSet = frozenset(Component)
            Edges = tuple(
                Edge for Edge in EdgeDemand
                if set(Edge) <= ComponentSet
            )
            if len(Edges) < ComponentSize - 1:
                continue
            Visited = {Component[0]}
            while True:
                Expanded = {
                    Value
                    for Edge in Edges
                    if set(Edge) & Visited
                    for Value in Edge
                }
                if Expanded <= Visited:
                    break
                Visited.update(Expanded)
            if Visited != set(Component):
                continue
            if (
                LogicalComponentGraph is not None
                and LogicalComponentGraph.Hierarchical
                and LogicalComponentForClusterSet(ComponentSet) is None
            ):
                continue
            Signatures = tuple(sorted(
                Source.PackedClusters[Index].StructuralSignature
                if Index < len(Source.PackedClusters)
                else str(len(Source.Clusters[Index]))
                for Index in Component
            ))
            CrossingOwnedTerminalDemand = 0
            CrossingSignals = set()
            IncidentSignals = set()
            InternallyOwnedSignals = set()
            InternallyOwnedTerminalDemand = 0
            PreferredCoveredSignals = set()
            PreferredOwnedTerminalCoverage = 0
            PreferredFullyOwnedRequestCount = 0
            ComponentGates = tuple(
                Gate
                for Cluster in ComponentSet
                for Gate in ClusterGates[Cluster]
            )
            ComponentMinimumX = min(Gate.X for Gate in ComponentGates)
            ComponentMaximumX = max(Gate.X for Gate in ComponentGates)
            ComponentMinimumZ = min(Gate.Z for Gate in ComponentGates)
            ComponentMaximumZ = max(Gate.Z for Gate in ComponentGates)
            PerimeterDepths = []
            DirectedPerimeterPenalties = []

            def RecordPerimeterDepth(
                Terminal: tuple[int, int, int] | None,
                Side: str,
            ) -> None:
                if Terminal is None:
                    return
                X, _Y, Z = Terminal
                SideDepths = {
                    "west": abs(X - ComponentMinimumX),
                    "east": abs(ComponentMaximumX - X),
                    "north": abs(Z - ComponentMinimumZ),
                    "south": abs(ComponentMaximumZ - Z),
                }
                MinimumDepth = min(SideDepths.values())
                DirectedDepth = SideDepths.get(
                    str(Side).lower(),
                    MinimumDepth,
                )
                PerimeterDepths.append(MinimumDepth)
                DirectedPerimeterPenalties.append(
                    max(0, DirectedDepth - MinimumDepth)
                )

            for Request in AllBoundaryRequests:
                SourceSelected = (
                    int(Request.SourceCluster) in ComponentSet
                )
                TargetSelected = (
                    int(Request.TargetCluster) in ComponentSet
                )
                if not (SourceSelected or TargetSelected):
                    continue
                IncidentSignals.add(str(Request.Signal))
                if str(Request.Signal) in PreferredSignalSet:
                    PreferredCoveredSignals.add(
                        str(Request.Signal)
                    )
                    PreferredOwnedTerminalCoverage += (
                        int(
                            SourceSelected
                            and Request.SourceTerminal is not None
                        )
                        + (
                            len(Request.TargetTerminals)
                            if TargetSelected
                            else 0
                        )
                    )
                    PreferredFullyOwnedRequestCount += int(
                        SourceSelected and TargetSelected
                    )
                if SourceSelected and TargetSelected:
                    InternallyOwnedSignals.add(str(Request.Signal))
                    InternallyOwnedTerminalDemand += (
                        int(Request.SourceTerminal is not None)
                        + len(Request.TargetTerminals)
                    )
                if SourceSelected == TargetSelected:
                    continue
                CrossingSignals.add(str(Request.Signal))
                if SourceSelected:
                    RecordPerimeterDepth(
                        Request.SourceTerminal,
                        Request.SourceBoundarySide,
                    )
                else:
                    for Terminal in Request.TargetTerminals:
                        RecordPerimeterDepth(
                            Terminal,
                            Request.TargetBoundarySide,
                        )
                CrossingOwnedTerminalDemand += (
                    int(Request.SourceTerminal is not None)
                    if SourceSelected
                    else len(Request.TargetTerminals)
                )
            PeakInternalDemand = max(
                (EdgeDemand[Edge] for Edge in Edges),
                default=0,
            )
            TotalInternalDemand = sum(
                EdgeDemand[Edge] for Edge in Edges
            )
            PeakInternalSignalCount = max(
                (
                    len({
                        str(Request.Signal)
                        for Request in AllBoundaryRequests
                        if frozenset((
                            int(Request.SourceCluster),
                            int(Request.TargetCluster),
                        )) == frozenset(Edge)
                    })
                    for Edge in Edges
                ),
                default=0,
            )
            if PreferredSignalSet:
                # A learned global cut identifies work that must move inside
                # the routed component.  First maximize exact ownership of
                # that cut; only then prefer the least-demanding residual
                # interface.  Reversing these priorities can select a quiet
                # component that owns one cut terminal and exports the
                # original high-fanout net back to the global router.
                Score = (
                    -len(PreferredCoveredSignals),
                    -PreferredFullyOwnedRequestCount,
                    -PreferredOwnedTerminalCoverage,
                    0
                    if LogicalComponentForClusterSet(ComponentSet)
                    is not None
                    else 1,
                    len(CrossingSignals),
                    CrossingOwnedTerminalDemand,
                    max(DirectedPerimeterPenalties, default=0),
                    sum(DirectedPerimeterPenalties),
                    max(PerimeterDepths, default=0),
                    sum(PerimeterDepths),
                    LogicalComponentBenefits.get(
                        LogicalComponentForClusterSet(ComponentSet),
                        (1 << 30, 0, 0),
                    ),
                    PeakInternalDemand,
                    TotalInternalDemand,
                    len(IncidentSignals),
                    PeakInternalSignalCount,
                    -len(InternallyOwnedSignals),
                    -InternallyOwnedTerminalDemand,
                    -ComponentSize,
                    Signatures,
                    Component,
                )
            else:
                Score = (
                    0,
                    0,
                    0,
                    0
                    if LogicalComponentForClusterSet(ComponentSet)
                    is not None
                    else 1,
                    len(CrossingSignals),
                    CrossingOwnedTerminalDemand,
                    max(DirectedPerimeterPenalties, default=0),
                    sum(DirectedPerimeterPenalties),
                    max(PerimeterDepths, default=0),
                    sum(PerimeterDepths),
                    LogicalComponentBenefits.get(
                        LogicalComponentForClusterSet(ComponentSet),
                        (1 << 30, 0, 0),
                    ),
                    len(IncidentSignals),
                    PeakInternalSignalCount,
                    PeakInternalDemand,
                    TotalInternalDemand,
                    -len(InternallyOwnedSignals),
                    -InternallyOwnedTerminalDemand,
                    -ComponentSize,
                    Signatures,
                    Component,
                )
            CandidateComponents.append((
                Score,
                Component,
                (
                    len(CrossingSignals),
                    CrossingOwnedTerminalDemand,
                    max(DirectedPerimeterPenalties, default=0),
                    sum(DirectedPerimeterPenalties),
                    max(PerimeterDepths, default=0),
                    sum(PerimeterDepths),
                ),
            ))
    if not CandidateComponents:
        raise ValueError(
            "no connected two-or-three-cluster interface deck component"
        )
    RankedComponents = tuple(sorted(CandidateComponents))
    if len(ClusterIndexes) <= MaximumAffectedClusters:
        WholePlacementComponents = tuple(
            Value
            for Value in RankedComponents
            if frozenset(Value[1]) == frozenset(ClusterIndexes)
        )
        if WholePlacementComponents:
            RankedComponents = WholePlacementComponents
    if ForcedAffectedClusters is None:
        (
            _Score,
            AffectedClusters,
            SelectedPerimeterAccessScore,
        ) = RankedComponents[ComponentVariant % len(RankedComponents)]
    else:
        ForcedClusterSet = frozenset(map(int, ForcedAffectedClusters))
        ForcedMatches = tuple(
            Value
            for Value in RankedComponents
            if frozenset(Value[1]) == ForcedClusterSet
        )
        if not ForcedMatches:
            raise ValueError(
                "forced deck component is not a legal connected component"
            )
        (
            _Score,
            AffectedClusters,
            SelectedPerimeterAccessScore,
        ) = ForcedMatches[0]
    AffectedSet = frozenset(AffectedClusters)
    ComponentId = LogicalComponentForClusterSet(AffectedSet)
    TopologyComponent = (
        next(
            (
                Value
                for Value in LogicalComponentGraph.Components
                if Value.ComponentId == ComponentId
            ),
            None,
        )
        if (
            LogicalComponentGraph is not None
            and ComponentId is not None
        )
        else None
    )

    def Center(Cluster: int) -> tuple[int, int]:
        Gates = ClusterGates[Cluster]
        return (
            sum(Gate.X for Gate in Gates) // len(Gates),
            sum(Gate.Z for Gate in Gates) // len(Gates),
        )

    Centers = {
        Cluster: Center(Cluster)
        for Cluster in AffectedClusters
    }
    RankedEdges = sorted(
        (
            Edge for Edge in EdgeDemand
            if set(Edge) <= AffectedSet
        ),
        key=lambda Edge: (
            -EdgeDemand[Edge],
            abs(Centers[Edge[0]][0] - Centers[Edge[1]][0])
            + abs(Centers[Edge[0]][1] - Centers[Edge[1]][1]),
            Edge,
        ),
    )
    Parents = {Cluster: Cluster for Cluster in AffectedClusters}

    def Find(Value: int) -> int:
        while Parents[Value] != Value:
            Parents[Value] = Parents[Parents[Value]]
            Value = Parents[Value]
        return Value

    TreeEdges = []
    for First, Second in RankedEdges:
        FirstRoot, SecondRoot = Find(First), Find(Second)
        if FirstRoot == SecondRoot:
            continue
        Parents[max(FirstRoot, SecondRoot)] = min(
            FirstRoot,
            SecondRoot,
        )
        TreeEdges.append((First, Second))
        if len(TreeEdges) == len(AffectedClusters) - 1:
            break
    if len(TreeEdges) != len(AffectedClusters) - 1:
        raise ValueError("interface deck spanning tree is incomplete")
    TreeEdges = TreeEdges[:MaximumDeckLanes]

    PlacementBaseY = min(
        Gate.Y for Gate in Source.Placed.PlacedGates
    )
    RoutingY = DefaultRedstoneRoutingTechnology.RoutingY(
        PlacementBaseY,
        InterfaceDeckLayer,
    )
    Occupied, _Electrical, _Solid = BuildPlacedCellGeometry(Source.Placed)

    def BuildTreeLane(
        DeltaX: int,
        DeltaZ: int,
        XFirst: bool,
    ) -> tuple[tuple[tuple[int, int, int], ...], ...] | None:
        def InclusiveRange(Start: int, End: int) -> range:
            return range(
                Start,
                End + (1 if End >= Start else -1),
                1 if End >= Start else -1,
            )

        EdgePaths = []
        for First, Second in TreeEdges:
            FirstX, FirstZ = Centers[First]
            SecondX, SecondZ = Centers[Second]
            StartX, StartZ = FirstX + DeltaX, FirstZ + DeltaZ
            EndX, EndZ = SecondX + DeltaX, SecondZ + DeltaZ
            if XFirst:
                Cells = tuple((
                    *((X, RoutingY, StartZ)
                      for X in InclusiveRange(StartX, EndX)),
                    *((EndX, RoutingY, Z)
                      for Z in tuple(InclusiveRange(StartZ, EndZ))[1:]),
                ))
            else:
                Cells = tuple((
                    *((StartX, RoutingY, Z)
                      for Z in InclusiveRange(StartZ, EndZ)),
                    *((X, RoutingY, EndZ)
                      for X in tuple(InclusiveRange(StartX, EndX))[1:]),
                ))
            if not Cells or any(
                Position in Occupied
                for X, Y, Z in Cells
                for Position in (
                    (X, Y, Z),
                    (X, Y - 1, Z),
                )
            ):
                return None
            EdgePaths.append(Cells)
        return tuple(EdgePaths)

    LaneCandidates = []
    LaneOffsets = (
        (0, 0),
        *(
            Value
            for Distance in range(1, 7)
            for Value in (
                (-Distance * TrackPitch, 0),
                (Distance * TrackPitch, 0),
                (0, -Distance * TrackPitch),
                (0, Distance * TrackPitch),
            )
        ),
    )
    for DeltaX, DeltaZ in LaneOffsets:
        for XFirst in (True, False):
            EdgePaths = BuildTreeLane(
                DeltaX,
                DeltaZ,
                XFirst,
            )
            if EdgePaths is None:
                continue
            Cells = frozenset(
                Position
                for Path in EdgePaths
                for Position in Path
            )
            LaneCandidates.append((
                (
                    abs(DeltaX) + abs(DeltaZ),
                    DeltaX,
                    DeltaZ,
                    not XFirst,
                ),
                EdgePaths,
                Cells,
            ))
    def LanesAreSeparated(
        First: tuple[object, ...],
        Second: tuple[object, ...],
    ) -> bool:
        return not any(
            abs(FirstCell[0] - SecondCell[0])
            + abs(FirstCell[2] - SecondCell[2])
            < TrackPitch
            for FirstCell in First[2]
            for SecondCell in Second[2]
        )

    SelectedTreeLaneValues = []
    for Candidate in sorted(
        LaneCandidates,
        key=lambda Value: Value[0],
    ):
        if all(
            LanesAreSeparated(Candidate, Existing)
            for Existing in SelectedTreeLaneValues
        ):
            SelectedTreeLaneValues.append(Candidate)
            if len(SelectedTreeLaneValues) >= MaximumDeckLanes:
                break
    SelectedTreeLanes = tuple(SelectedTreeLaneValues)
    if not SelectedTreeLanes:
        raise ValueError(
            "no electrically separated component-tree deck lane"
        )

    LaneRecords = []
    for ParallelLaneIndex, LaneCandidate in enumerate(
        SelectedTreeLanes[:MaximumDeckLanes]
    ):
        _LaneScore, EdgePaths, _LaneCells = LaneCandidate
        for EdgeIndex, Cells in enumerate(EdgePaths):
            First, Second = TreeEdges[EdgeIndex]
            IngressNodes = tuple(dict.fromkeys((
                Cells[0],
                Cells[-1],
                *Cells[::TrackPitch],
            )))
            PhysicalClaims = RoutingResourceClaims(
                WireCells=frozenset(Cells),
                SupportCells=frozenset(
                    (X, Y - 1, Z) for X, Y, Z in Cells
                ),
                RequiredAirCells=frozenset(),
                ElectricalCells=frozenset(
                    DefaultRedstoneRoutingTechnology
                    .BuildElectricalExclusions(set(Cells))
                ),
            )
            ClaimsFingerprint = sha256(repr((
                InterfaceDeckLayer,
                tuple(sorted(
                    PhysicalClaims.ResourceIds,
                    key=repr,
                )),
            )).encode("utf-8")).hexdigest()[:16]
            LaneRecords.append(InterClusterChannelLane(
                Layer=InterfaceDeckLayer,
                Direction=f"XZ-Lane{ParallelLaneIndex}",
                Cells=Cells,
                IngressNodes=IngressNodes,
                PhysicalClaims=PhysicalClaims,
                ClaimsFingerprint=ClaimsFingerprint,
            ))

    MinimumX = min(Cell[0] for Lane in LaneRecords for Cell in Lane.Cells)
    MinimumZ = min(Cell[2] for Lane in LaneRecords for Cell in Lane.Cells)
    StructuralClusters = tuple(sorted(
        Source.PackedClusters[Cluster].StructuralSignature
        if Cluster < len(Source.PackedClusters)
        else str(len(Source.Clusters[Cluster]))
        for Cluster in AffectedClusters
    ))
    ChannelFingerprint = sha256(repr((
        "parallel-tree-cluster-interface-deck-v1",
        StructuralClusters,
        InterfaceDeckLayer,
        tuple(
            (
                Lane.Layer,
                tuple(
                    (
                        Cell[0] - MinimumX,
                        Cell[1] - PlacementBaseY,
                        Cell[2] - MinimumZ,
                    )
                    for Cell in Lane.Cells
                ),
            )
            for Lane in LaneRecords
        ),
    )).encode("utf-8")).hexdigest()[:16]
    CandidateAffectedSignals = {
        Request.Signal
        for Request in AllBoundaryRequests
        if (
            (
                Request.SourceCluster in AffectedSet
                and Request.TargetCluster in AffectedSet
            )
            or (
                str(Request.Signal) in PreferredSignalSet
                and (
                    Request.SourceCluster in AffectedSet
                    or Request.TargetCluster in AffectedSet
                )
            )
        )
    }
    if TopologyComponent is not None:
        CandidateAffectedSignals.intersection_update(frozenset((
            *TopologyComponent.InternalSignals,
            *TopologyComponent.BoundarySignals,
        )))
    AffectedSignals = tuple(sorted(CandidateAffectedSignals))
    Deck = InterClusterRoutingChannel(
        ChannelFingerprint=ChannelFingerprint,
        AffectedClusters=tuple(sorted(AffectedClusters)),
        AffectedSignals=AffectedSignals,
        InsertedBoundaryStrips=(),
        ClusterTranslations=tuple(
            (Cluster, (0, 0, 0))
            for Cluster in sorted(AffectedClusters)
        ),
        Lanes=tuple(LaneRecords),
        PhysicalModel="parallel-tree-cluster-interface-deck-v1",
        InterfaceDeckLayer=InterfaceDeckLayer,
        TrackPitch=TrackPitch,
        MaximumAffectedClusters=MaximumAffectedClusters,
        MaximumBoundaryStrips=0,
        ComponentId=ComponentId,
        InterfaceFingerprint=(
            TopologyComponent.StructuralFingerprint
            if TopologyComponent is not None
            else ""
        ),
        DeclaredFeedthroughSignals=(),
    )
    Diagnostics = dict(Source.Placed.LocalRouteDiagnostics or {})
    DeferredDiagnostics = Diagnostics.get(
        "__DeferredLocalRouting__",
        {},
    )
    if isinstance(DeferredDiagnostics, dict):
        Diagnostics["__DeferredLocalRouting__"] = {
            **DeferredDiagnostics,
            "ScoringOnly": False,
            "InterfaceDeck": True,
        }
    Diagnostics["__InterClusterRoutingChannel__"] = Deck.ToDictionary()
    Diagnostics["__InterClusterRoutingDeckSelection__"] = {
        "PreferredSignals": sorted(PreferredSignalSet),
        "SelectedPreferredSignalCount": -_Score[0],
        "SelectedPeakInternalEdgeDemand": max(
            (
                EdgeDemand[Edge]
                for Edge in EdgeDemand
                if set(Edge) <= AffectedSet
            ),
            default=0,
        ),
        "SelectedTotalInternalEdgeDemand": sum(
            EdgeDemand[Edge]
            for Edge in EdgeDemand
            if set(Edge) <= AffectedSet
        ),
        "SelectedPreferredFullyOwnedRequestCount": (
            -_Score[1] if PreferredSignalSet else 0
        ),
        "SelectedPreferredOwnedTerminalCoverage": (
            -_Score[2] if PreferredSignalSet else 0
        ),
        "SelectedClusterCount": len(AffectedClusters),
        "SelectedPerimeterAccessScore": list(
            SelectedPerimeterAccessScore[2:]
        ),
        "SelectedBoundaryInterfaceSignalCount": (
            SelectedPerimeterAccessScore[0]
        ),
        "SelectedBoundaryInterfaceTerminalDemand": (
            SelectedPerimeterAccessScore[1]
        ),
        "ComponentVariant": ComponentVariant,
    }
    CandidatePlaced = replace(
        Source.Placed,
        RouteGuides=None,
        RouteLayers=None,
        FrozenNetWires=None,
        LocalNetBranches=None,
        LocalNetTargets=None,
        LocalRouteDiagnostics=Diagnostics,
        ClusterBoundaryLeaseRequests=AllBoundaryRequests,
        CompleteClusterInterfaceAccess=True,
        InterClusterRoutingChannel=Deck,
        PackedClusters=Source.PackedClusters,
    )
    return replace(
        Source,
        Placed=CandidatePlaced,
        ClusterBoundaryLeaseRequests=AllBoundaryRequests,
        CompleteClusterInterfaceAccess=True,
        MandatoryAccessPreScreenProfile=None,
        InterClusterRoutingChannel=Deck,
    )


def BuildTopologicalLevels(
    Module: Any,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, int]:
    """Assign every gate to a left-to-right combinational depth."""
    Producers = {
        Signal: Gate
        for Gate in Module.Gates
        for Signal in Gate.Outputs
    }
    Levels = {
        Gate.Name: 0
        for Gate in Module.Gates
        if Gate.Kind.value == "INPUT"
    }
    Pending = [
        Gate
        for Gate in Module.Gates
        if Gate.Kind.value not in ("INPUT", "OUTPUT")
    ]
    PassIndex = 0
    while Pending:
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "topological-levels",
                "PassIndex": PassIndex,
                "PendingGates": len(Pending),
            })
        Remaining = []
        for GateIndex, Gate in enumerate(Pending):
            if WorkCheck is not None and GateIndex % 32 == 0:
                WorkCheck({
                    "Phase": "topological-level-gate",
                    "PassIndex": PassIndex,
                    "CompletedGates": GateIndex,
                    "TotalGates": len(Pending),
                })
            ProducerNames = [
                Producers[Signal].Name
                for Signal in Gate.Inputs
            ]
            if any(Name not in Levels for Name in ProducerNames):
                Remaining.append(Gate)
                continue
            Levels[Gate.Name] = 1 + max(
                Levels[Name]
                for Name in ProducerNames
            )
        if len(Remaining) == len(Pending):
            Names = ", ".join(Gate.Name for Gate in Remaining)
            raise ValueError(
                f"PCB placement found a combinational cycle: {Names}"
            )
        Pending = Remaining
        PassIndex += 1

    OutputLevel = max(Levels.values(), default=0) + 1
    for Gate in Module.Gates:
        if Gate.Kind.value == "OUTPUT":
            Levels[Gate.Name] = OutputLevel
    return Levels


def BuildConnectivityClusters(
    Module: Any,
    MaximumClusterSize: int = 32,
    Policy: ClusteringPolicy | None = None,
    MaximumBoundaryTerminals: int | None = None,
    RefinementProfile: CutDrivenClusterRefinementProfile | None = None,
    LogicalComponentByGate: Mapping[str, int] | None = None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> tuple[tuple[str, ...], ...]:
    """Agglomerate strongly connected NAND gates without circuit recognition."""
    if WorkCheck is not None:
        WorkCheck({"Phase": "connectivity-clusters-start"})
    Internal = [Gate for Gate in Module.Gates if Gate.Kind.value == "NAND"]
    if not Internal:
        return ()
    InternalNames = {Gate.Name for Gate in Internal}
    LogicalComponentByGate = dict(
        LogicalComponentByGate or {}
    )
    Producers = {
        Signal: Gate.Name
        for Gate in Module.Gates
        for Signal in Gate.Outputs
    }
    EdgeWeights: dict[frozenset[str], int] = {}
    for GateIndex, Gate in enumerate(Internal):
        if WorkCheck is not None and GateIndex % 32 == 0:
            WorkCheck({
                "Phase": "connectivity-cluster-edges",
                "CompletedGates": GateIndex,
                "TotalGates": len(Internal),
            })
        for Signal in Gate.Inputs:
            Producer = Producers.get(Signal)
            if Producer not in InternalNames or Producer == Gate.Name:
                continue
            Key = frozenset((Producer, Gate.Name))
            EdgeWeights[Key] = (
                EdgeWeights.get(Key, 0)
                + 1
                + (
                    RefinementProfile.EdgeWeight
                    if (
                        RefinementProfile is not None
                        and Signal in RefinementProfile.Signals
                    )
                    else 0
                )
            )
    Levels = BuildTopologicalLevels(Module, WorkCheck=WorkCheck)
    BoundaryEvaluationCount = 0

    def BoundaryCount(Names: set[str]) -> int:
        nonlocal BoundaryEvaluationCount
        BoundaryEvaluationCount += 1
        if WorkCheck is not None and BoundaryEvaluationCount % 16 == 1:
            WorkCheck({
                "Phase": "connectivity-boundary-count",
                "BoundaryEvaluationCount": BoundaryEvaluationCount,
                "ClusterGateCount": len(Names),
            })
        Result = 0
        for Gate in Module.Gates:
            GateInside = Gate.Name in Names
            for Signal in Gate.Inputs:
                Producer = Producers.get(Signal)
                ProducerInside = Producer in Names
                if GateInside != ProducerInside:
                    Result += 1
            if GateInside and any(
                Signal in Module.Outputs for Signal in Gate.Outputs
            ):
                Result += 1
        return Result

    Clusters = {Index: {Gate.Name} for Index, Gate in enumerate(Internal)}
    MergePass = 0
    while True:
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "connectivity-cluster-merge",
                "MergePass": MergePass,
                "ClusterCount": len(Clusters),
            })
        BestPair = None
        BestScore = None
        ClusterIds = sorted(Clusters)
        PairCount = 0
        for FirstIndex, FirstId in enumerate(ClusterIds):
            for SecondId in ClusterIds[FirstIndex + 1 :]:
                PairCount += 1
                if WorkCheck is not None and PairCount % 32 == 1:
                    WorkCheck({
                        "Phase": "connectivity-cluster-pair",
                        "MergePass": MergePass,
                        "CompletedPairs": PairCount - 1,
                    })
                First = Clusters[FirstId]
                Second = Clusters[SecondId]
                if len(First) + len(Second) > MaximumClusterSize:
                    continue
                if LogicalComponentByGate and len({
                    LogicalComponentByGate[Name]
                    for Name in (*First, *Second)
                    if Name in LogicalComponentByGate
                }) > 1:
                    continue
                CrossWeight = sum(
                    Weight
                    for Pair, Weight in EdgeWeights.items()
                    if len(Pair & First) == 1 and len(Pair & Second) == 1
                )
                if CrossWeight <= 0:
                    continue
                Combined = First | Second
                CombinedBoundary = BoundaryCount(Combined)
                if (
                    MaximumBoundaryTerminals is not None
                    and CombinedBoundary > MaximumBoundaryTerminals
                ):
                    continue
                Diameter = (
                    max(Levels[Name] for Name in Combined)
                    - min(Levels[Name] for Name in Combined)
                )
                CutReduction = (
                    BoundaryCount(First)
                    + BoundaryCount(Second)
                    - CombinedBoundary
                )
                AdaptiveScore = (
                    CutReduction * Policy.CutWeight
                    - max(0, Diameter - 4) * Policy.BalanceWeight
                    if Policy is not None
                    else CrossWeight
                )
                Score = (
                    CrossWeight,
                    -(len(First) + len(Second)),
                    -FirstId,
                    -SecondId,
                    AdaptiveScore,
                    -CombinedBoundary,
                    -Diameter,
                )
                if BestScore is None or Score > BestScore:
                    BestScore = Score
                    BestPair = FirstId, SecondId
        if BestPair is None:
            break
        FirstId, SecondId = BestPair
        Clusters[FirstId].update(Clusters.pop(SecondId))
        MergePass += 1

    OriginalOrder = {
        Gate.Name: Index
        for Index, Gate in enumerate(Module.Gates)
    }
    return tuple(
        tuple(sorted(Names, key=OriginalOrder.__getitem__))
        for _ClusterId, Names in sorted(Clusters.items())
    )


def AnalyzeNandClusterStructure(
    Module: Any,
    Names: tuple[str, ...],
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> tuple[
    str,
    tuple[str, ...],
    dict[str, int],
    dict[str, tuple[Any, ...]],
    frozenset[tuple[str, str, int]],
]:
    """Build a name-independent directed signature for one NAND island."""
    NameSet = set(Names)
    GateByName = {Gate.Name: Gate for Gate in Module.Gates}
    Producers = {
        Signal: Gate.Name
        for Gate in Module.Gates
        for Signal in Gate.Outputs
    }
    Consumers: dict[str, list[Any]] = {}
    for GateIndex, Gate in enumerate(Module.Gates):
        if WorkCheck is not None and GateIndex % 32 == 0:
            WorkCheck({
                "Phase": "structural-analysis-consumers",
                "CompletedGates": GateIndex,
                "TotalGates": len(Module.Gates),
            })
        for Signal in Gate.Inputs:
            Consumers.setdefault(Signal, []).append(Gate)
    BoundarySignals = sorted({
        Signal
        for Name in Names
        for Signal in GateByName[Name].Inputs
        if Producers.get(Signal) not in NameSet
    })
    Nodes = tuple(
        [*(f"B:{Signal}" for Signal in BoundarySignals)]
        + [*(f"G:{Name}" for Name in Names)]
    )
    Edges = set()
    for NameIndex, Name in enumerate(Names):
        if WorkCheck is not None and NameIndex % 32 == 0:
            WorkCheck({
                "Phase": "structural-analysis-edges",
                "CompletedNodes": NameIndex,
                "TotalNodes": len(Names),
            })
        Gate = GateByName[Name]
        for InputIndex, Signal in enumerate(Gate.Inputs):
            Producer = Producers.get(Signal)
            Source = (
                f"G:{Producer}"
                if Producer in NameSet
                else f"B:{Signal}"
            )
            Edges.add((Source, f"G:{Name}", InputIndex))
    Incoming: dict[str, list[tuple[str, int]]] = {Node: [] for Node in Nodes}
    Outgoing: dict[str, list[tuple[str, int]]] = {Node: [] for Node in Nodes}
    for Source, Target, InputIndex in Edges:
        Incoming[Target].append((Source, InputIndex))
        Outgoing[Source].append((Target, InputIndex))
    Initial: dict[str, tuple[Any, ...]] = {}
    for Node in Nodes:
        if Node.startswith("B:"):
            Initial[Node] = ("BoundaryInput", len(Outgoing[Node]))
            continue
        Name = Node[2:]
        Gate = GateByName[Name]
        InternalInputs = sum(Source.startswith("G:") for Source, _ in Incoming[Node])
        HasExternalFanout = any(
            Consumer.Name not in NameSet
            for Signal in Gate.Outputs
            for Consumer in Consumers.get(Signal, ())
        )
        Initial[Node] = (
            "NAND",
            InternalInputs,
            len(Incoming[Node]) - InternalInputs,
            len(Outgoing[Node]),
            HasExternalFanout,
        )
    InitialValues = {Value for Value in Initial.values()}
    InitialIds = {Value: Index for Index, Value in enumerate(sorted(InitialValues))}
    Colors = {Node: InitialIds[Initial[Node]] for Node in Nodes}
    for _Pass in range(len(Nodes) + 1):
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "structural-analysis-coloring",
                "PassIndex": _Pass,
                "NodeCount": len(Nodes),
            })
        Descriptors = {
            Node: (
                Initial[Node],
                tuple(sorted((InputIndex, Colors[Source]) for Source, InputIndex in Incoming[Node])),
                tuple(sorted((InputIndex, Colors[Target]) for Target, InputIndex in Outgoing[Node])),
            )
            for Node in Nodes
        }
        DescriptorIds = {
            Value: Index
            for Index, Value in enumerate(sorted(set(Descriptors.values())))
        }
        NextColors = {Node: DescriptorIds[Descriptors[Node]] for Node in Nodes}
        if NextColors == Colors:
            break
        Colors = NextColors
    Canonical = (
        tuple(sorted((Initial[Node], Colors[Node]) for Node in Nodes)),
        tuple(sorted(
            (Colors[Source], Colors[Target], InputIndex)
            for Source, Target, InputIndex in Edges
        )),
    )
    Signature = sha256(repr(Canonical).encode("utf-8")).hexdigest()[:16]
    return Signature, Nodes, Colors, Initial, frozenset(Edges)


def FindIsomorphicNandClusterMapping(
    Module: Any,
    ReferenceNames: tuple[str, ...],
    CandidateNames: tuple[str, ...],
    MaximumMappings: int = 4096,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> tuple[str, dict[str, str]] | None:
    """Return an exact structural gate mapping without circuit recognition."""
    Reference = AnalyzeNandClusterStructure(
        Module,
        ReferenceNames,
        WorkCheck=WorkCheck,
    )
    Candidate = AnalyzeNandClusterStructure(
        Module,
        CandidateNames,
        WorkCheck=WorkCheck,
    )
    ReferenceSignature, ReferenceNodes, ReferenceColors, ReferenceInitial, ReferenceEdges = Reference
    CandidateSignature, CandidateNodes, CandidateColors, CandidateInitial, CandidateEdges = Candidate
    if ReferenceSignature != CandidateSignature or len(ReferenceNodes) != len(CandidateNodes):
        return None
    ReferenceGroups: dict[tuple[Any, ...], list[str]] = {}
    CandidateGroups: dict[tuple[Any, ...], list[str]] = {}
    for Node in ReferenceNodes:
        ReferenceGroups.setdefault(
            (ReferenceInitial[Node], ReferenceColors[Node]), []
        ).append(Node)
    for Node in CandidateNodes:
        CandidateGroups.setdefault(
            (CandidateInitial[Node], CandidateColors[Node]), []
        ).append(Node)
    if {
        Key: len(Values) for Key, Values in ReferenceGroups.items()
    } != {
        Key: len(Values) for Key, Values in CandidateGroups.items()
    }:
        return None
    Groups = sorted(
        ReferenceGroups,
        key=lambda Key: (len(ReferenceGroups[Key]), repr(Key)),
    )
    AttemptCount = 0
    Mapping: dict[str, str] = {}

    def IsConsistent() -> bool:
        MappedEdges = {
            (Mapping[Source], Mapping[Target], InputIndex)
            for Source, Target, InputIndex in ReferenceEdges
            if Source in Mapping and Target in Mapping
        }
        return MappedEdges.issubset(CandidateEdges)

    def Search(GroupIndex: int) -> bool:
        nonlocal AttemptCount
        if GroupIndex == len(Groups):
            return {
                (Mapping[Source], Mapping[Target], InputIndex)
                for Source, Target, InputIndex in ReferenceEdges
            } == CandidateEdges
        Key = Groups[GroupIndex]
        ReferenceValues = sorted(ReferenceGroups[Key])
        CandidateValues = sorted(CandidateGroups[Key])
        for Permutation in permutations(CandidateValues):
            AttemptCount += 1
            if WorkCheck is not None and AttemptCount % 32 == 1:
                WorkCheck({
                    "Phase": "structural-mapping-search",
                    "AttemptCount": AttemptCount,
                    "MaximumMappings": MaximumMappings,
                    "GroupIndex": GroupIndex,
                })
            if AttemptCount > MaximumMappings:
                return False
            Mapping.update(zip(ReferenceValues, Permutation))
            if IsConsistent() and Search(GroupIndex + 1):
                return True
            for Node in ReferenceValues:
                Mapping.pop(Node, None)
        return False

    if not Search(0):
        return None
    return ReferenceSignature, {
        ReferenceNode[2:]: CandidateNode[2:]
        for ReferenceNode, CandidateNode in Mapping.items()
        if ReferenceNode.startswith("G:")
    }


def ComposeCellTransform(
    CellRotation: int,
    CellMirrorX: bool,
    ClusterRotation: int,
    ClusterMirrorX: bool,
) -> tuple[int, bool]:
    """Compose a cell transform with one enclosing cluster transform."""
    TargetX = TransformDirection(
        TransformDirection((1, 0, 0), CellRotation, CellMirrorX),
        ClusterRotation,
        ClusterMirrorX,
    )
    TargetZ = TransformDirection(
        TransformDirection((0, 0, 1), CellRotation, CellMirrorX),
        ClusterRotation,
        ClusterMirrorX,
    )
    for Rotation in (0, 90, 180, 270):
        for MirrorX in (False, True):
            if (
                TransformDirection((1, 0, 0), Rotation, MirrorX) == TargetX
                and TransformDirection((0, 0, 1), Rotation, MirrorX) == TargetZ
            ):
                return Rotation, MirrorX
    raise ValueError("Could not compose packed-cell transforms")


def TransformPackedClusterLayout(
    Names: tuple[str, ...],
    LocalPositions: dict[str, tuple[int, int]],
    LocalRotations: dict[str, int],
    LocalMirrors: dict[str, bool],
    Rotation: int,
    MirrorX: bool,
    GatesByName: dict[str, Any] | None = None,
) -> ClusterLayoutVariant:
    """Rigidly transform a local layout using exact template geometry.

    A NAND's placement origin is not a rigid physical anchor: its template
    extends beyond the nominal footprint through pins, electrical exclusions,
    and directional supports.  When logical gates are available, derive each
    transformed origin by matching the transformed actual/electrical template
    sets to a composed target template.  This prevents a nominally mirrored
    rectangle from committing as an electrically overlapping NAND placement.
    """
    Rotation = NormalizeRotation(Rotation)
    BaseWidth = max(
        LocalPositions[Name][0]
        + RotatedCellSize("NAND", LocalRotations[Name])[0]
        for Name in Names
    )
    BaseDepth = max(
        LocalPositions[Name][1]
        + RotatedCellSize("NAND", LocalRotations[Name])[1]
        for Name in Names
    )
    Positions: dict[str, tuple[int, int]] = {}
    Rotations: dict[str, int] = {}
    Mirrors: dict[str, bool] = {}
    ActualGeometry: dict[str, frozenset[tuple[int, int, int]]] = {}
    ElectricalGeometry: dict[str, frozenset[tuple[int, int, int]]] = {}

    def TransformPosition(
        Position: tuple[int, int, int],
    ) -> tuple[int, int, int]:
        return TransformLocalPosition(
            Position,
            (BaseWidth, BaseDepth),
            Rotation,
            MirrorX,
        )

    def TranslateGeometry(
        Geometry: frozenset[tuple[int, int, int]],
        DeltaX: int,
        DeltaZ: int,
    ) -> frozenset[tuple[int, int, int]]:
        return frozenset(
            (X + DeltaX, Y, Z + DeltaZ)
            for X, Y, Z in Geometry
        )

    for Name in Names:
        X, Z = LocalPositions[Name]
        Width, Depth = RotatedCellSize("NAND", LocalRotations[Name])
        Rotations[Name], Mirrors[Name] = ComposeCellTransform(
            LocalRotations[Name],
            LocalMirrors.get(Name, False),
            Rotation,
            MirrorX,
        )
        if GatesByName is None:
            Corners = (
                TransformPosition((X, 0, Z)),
                TransformPosition((X + Width - 1, 0, Z)),
                TransformPosition((X, 0, Z + Depth - 1)),
                TransformPosition((X + Width - 1, 0, Z + Depth - 1)),
            )
            Positions[Name] = (
                min(Value[0] for Value in Corners),
                min(Value[2] for Value in Corners),
            )
            continue
        SourceActual, SourceElectrical = _PhysicalGateGeometry(
            "NAND",
            X,
            1,
            Z,
            LocalRotations[Name],
            LocalMirrors.get(Name, False),
        )
        TransformedActual = frozenset(
            TransformPosition(Position) for Position in SourceActual
        )
        TransformedElectrical = frozenset(
            TransformPosition(Position) for Position in SourceElectrical
        )
        TargetActual, TargetElectrical = _PhysicalGateGeometry(
            "NAND",
            0,
            1,
            0,
            Rotations[Name],
            Mirrors[Name],
        )
        SourceAnchor = min(TransformedActual)
        Match = next(
            (
                (SourceAnchor[0] - TargetAnchor[0], SourceAnchor[2] - TargetAnchor[2])
                for TargetAnchor in sorted(TargetActual)
                if TranslateGeometry(
                    TargetActual,
                    SourceAnchor[0] - TargetAnchor[0],
                    SourceAnchor[2] - TargetAnchor[2],
                ) == TransformedActual
                and TranslateGeometry(
                    TargetElectrical,
                    SourceAnchor[0] - TargetAnchor[0],
                    SourceAnchor[2] - TargetAnchor[2],
                ) == TransformedElectrical
            ),
            None,
        )
        if Match is None:
            return ClusterLayoutVariant(
                Rotation=Rotation,
                MirrorX=MirrorX,
                Positions={},
                Rotations={},
                Mirrors={},
                Width=0,
                Depth=0,
                ActualGeometry={},
                ElectricalGeometry={},
                RejectionReason=(
                    f"TemplateTransformMismatch:Member={Name}:"
                    f"Rotation={Rotation}:MirrorX={MirrorX}"
                ),
            )
        Positions[Name] = Match
        ActualGeometry[Name] = TransformedActual
        ElectricalGeometry[Name] = TransformedElectrical
    MinimumX = min(Value[0] for Value in Positions.values())
    MinimumZ = min(Value[1] for Value in Positions.values())
    Positions = {
        Name: (X - MinimumX, Z - MinimumZ)
        for Name, (X, Z) in Positions.items()
    }
    ActualGeometry = {
        Name: TranslateGeometry(Geometry, -MinimumX, -MinimumZ)
        for Name, Geometry in ActualGeometry.items()
    }
    ElectricalGeometry = {
        Name: TranslateGeometry(Geometry, -MinimumX, -MinimumZ)
        for Name, Geometry in ElectricalGeometry.items()
    }
    Width = max(
        Positions[Name][0] + RotatedCellSize("NAND", Rotations[Name])[0]
        for Name in Names
    )
    Depth = max(
        Positions[Name][1] + RotatedCellSize("NAND", Rotations[Name])[1]
        for Name in Names
    )
    if GatesByName is not None:
        CandidateGates = [
            BuildPlacedGate(
                GatesByName[Name],
                Positions[Name][0],
                1,
                Positions[Name][1],
                Rotations[Name],
                Mirrors[Name],
            )
            for Name in Names
        ]
        Conflict = next(
            (
                (First.Name, Second.Name)
                for Index, First in enumerate(CandidateGates)
                for Second in CandidateGates[Index + 1 :]
                if PcbGatesConflict(First, Second)
            ),
            None,
        )
        if Conflict is not None:
            return ClusterLayoutVariant(
                Rotation=Rotation,
                MirrorX=MirrorX,
                Positions=Positions,
                Rotations=Rotations,
                Mirrors=Mirrors,
                Width=Width,
                Depth=Depth,
                ActualGeometry=ActualGeometry,
                ElectricalGeometry=ElectricalGeometry,
                RejectionReason=(
                    "TemplateConflict:Members="
                    f"{Conflict[0]},{Conflict[1]}:Rotation={Rotation}:"
                    f"MirrorX={MirrorX}"
                ),
            )
    return ClusterLayoutVariant(
        Rotation=Rotation,
        MirrorX=MirrorX,
        Positions=Positions,
        Rotations=Rotations,
        Mirrors=Mirrors,
        Width=Width,
        Depth=Depth,
        ActualGeometry=ActualGeometry,
        ElectricalGeometry=ElectricalGeometry,
    )


def OptimizeClusterSlots(
    Module: Any,
    Clusters: tuple[tuple[str, ...], ...],
    Levels: dict[str, int],
    LogicalComponentByGate: Mapping[str, int] | None = None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> tuple[dict[int, tuple[int, int]], int, int]:
    """Place clusters on a compact grid using weighted net length."""
    Count = len(Clusters)
    if Count == 0:
        return {}, 0, 0
    ClusterByGate = {
        GateName: ClusterIndex
        for ClusterIndex, Names in enumerate(Clusters)
        for GateName in Names
    }
    Producers = {
        Signal: Gate
        for Gate in Module.Gates
        for Signal in Gate.Outputs
    }
    LogicalComponentByGate = dict(
        LogicalComponentByGate or {}
    )
    DirectedWeights: dict[tuple[int, int], int] = {}
    InputWeights = {Index: 0 for Index in range(Count)}
    OutputWeights = {Index: 0 for Index in range(Count)}
    for GateIndex, Gate in enumerate(Module.Gates):
        if WorkCheck is not None and GateIndex % 32 == 0:
            WorkCheck({
                "Phase": "cluster-slot-net-weights",
                "CompletedGates": GateIndex,
                "TotalGates": len(Module.Gates),
            })
        TargetCluster = ClusterByGate.get(Gate.Name)
        for Signal in Gate.Inputs:
            Producer = Producers[Signal]
            SourceCluster = ClusterByGate.get(Producer.Name)
            if SourceCluster is None and TargetCluster is not None:
                InputWeights[TargetCluster] += 1
            elif (
                SourceCluster is not None
                and TargetCluster is not None
                and SourceCluster != TargetCluster
            ):
                Key = SourceCluster, TargetCluster
                SharedComponentWeight = (
                    16
                    if (
                        LogicalComponentByGate
                        and LogicalComponentByGate.get(Producer.Name)
                        == LogicalComponentByGate.get(Gate.Name)
                        and Producer.Name in LogicalComponentByGate
                        and Gate.Name in LogicalComponentByGate
                    )
                    else 1
                )
                DirectedWeights[Key] = (
                    DirectedWeights.get(Key, 0)
                    + SharedComponentWeight
                )
            elif SourceCluster is not None and Gate.Kind.value == "OUTPUT":
                OutputWeights[SourceCluster] += 1

    Incoming = {Index: 0 for Index in range(Count)}
    Outgoing: dict[int, set[int]] = {Index: set() for Index in range(Count)}
    for Source, Target in DirectedWeights:
        if Target not in Outgoing[Source]:
            Outgoing[Source].add(Target)
            Incoming[Target] += 1
    PendingClusters = sorted(Index for Index, Degree in Incoming.items() if Degree == 0)
    TopologicalClusters = []
    while PendingClusters:
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "cluster-slot-topology",
                "CompletedClusters": len(TopologicalClusters),
                "TotalClusters": Count,
            })
        Current = PendingClusters.pop(0)
        TopologicalClusters.append(Current)
        for Target in sorted(Outgoing[Current]):
            Incoming[Target] -= 1
            if Incoming[Target] == 0:
                PendingClusters.append(Target)
                PendingClusters.sort()
    IsAcyclic = len(TopologicalClusters) == Count
    if IsAcyclic:
        ClusterLevels = {Index: 0 for Index in range(Count)}
        for Source in TopologicalClusters:
            for Target in Outgoing[Source]:
                ClusterLevels[Target] = max(
                    ClusterLevels[Target], ClusterLevels[Source] + 1
                )
        Columns = max(ClusterLevels.values(), default=0) + 1
        ClustersByColumn: dict[int, list[int]] = {}
        for ClusterIndex, Column in ClusterLevels.items():
            ClustersByColumn.setdefault(Column, []).append(ClusterIndex)
        Rows = max((len(Values) for Values in ClustersByColumn.values()), default=1)
    else:
        # Contracting an acyclic gate graph can create cycles between clusters.
        # Cyclic contracted graphs use a compact weighted layout instead of
        # repeatedly drifting right during precedence relaxation.
        Columns = max(1, ceil(sqrt(2 * Count)))
        Rows = max(1, ceil(Count / Columns))
    Slots = [
        (Column, Row)
        for Column in range(Columns)
        for Row in range(Rows)
    ]
    if IsAcyclic:
        Assignment = {}
        for Column in range(Columns):
            OrderedColumn = sorted(
                ClustersByColumn.get(Column, []),
                key=lambda Index: (
                    min(Levels[Name] for Name in Clusters[Index]),
                    Index,
                ),
            )
            for Row, ClusterIndex in enumerate(OrderedColumn):
                Assignment[ClusterIndex] = Column, Row
    else:
        OrderedClusters = sorted(
            range(Count),
            key=lambda Index: (
                median(Levels[Name] for Name in Clusters[Index]),
                min(Levels[Name] for Name in Clusters[Index]),
                Index,
            ),
        )
        Assignment = {
            ClusterIndex: Slots[Position]
            for Position, ClusterIndex in enumerate(OrderedClusters)
        }

    def PlacementCost(Values: dict[int, tuple[int, int]]) -> int:
        Cost = 0
        for (Source, Target), Weight in DirectedWeights.items():
            SourceX, SourceZ = Values[Source]
            TargetX, TargetZ = Values[Target]
            Cost += Weight * (
                10 * (abs(SourceX - TargetX) + abs(SourceZ - TargetZ))
                + (4 if TargetX < SourceX else 0)
            )
        for ClusterIndex, (Column, _Row) in Values.items():
            Cost += InputWeights[ClusterIndex] * Column * 5
            Cost += OutputWeights[ClusterIndex] * (Columns - 1 - Column) * 5
        return Cost

    for _Pass in range(12):
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "cluster-slot-optimization",
                "PassIndex": _Pass,
                "ClusterCount": Count,
                "SlotCount": len(Slots),
            })
        CurrentCost = PlacementCost(Assignment)
        Best = None
        BestCost = CurrentCost
        Occupied = {Slot: Index for Index, Slot in Assignment.items()}
        for ClusterIndex in range(Count):
            CurrentSlot = Assignment[ClusterIndex]
            for SlotIndex, Slot in enumerate(Slots):
                if WorkCheck is not None and SlotIndex % 32 == 0:
                    WorkCheck({
                        "Phase": "cluster-slot-candidate",
                        "PassIndex": _Pass,
                        "ClusterIndex": ClusterIndex,
                        "CompletedSlots": SlotIndex,
                        "TotalSlots": len(Slots),
                    })
                if IsAcyclic and Slot[0] != CurrentSlot[0]:
                    continue
                Other = Occupied.get(Slot)
                if Other == ClusterIndex:
                    continue
                Candidate = dict(Assignment)
                OldSlot = Candidate[ClusterIndex]
                Candidate[ClusterIndex] = Slot
                if Other is not None:
                    Candidate[Other] = OldSlot
                CandidateCost = PlacementCost(Candidate)
                if CandidateCost < BestCost:
                    BestCost = CandidateCost
                    Best = Candidate
        if Best is None:
            break
        Assignment = Best
    return Assignment, Columns, Rows


def BuildEffectiveAssignmentCutPairwiseEdges(
    AssignmentCut: RoutingAssignmentCut | None,
) -> tuple[tuple[str, str], ...]:
    """Preserve an explicit assignment edge or infer one exact two-net cut."""
    if AssignmentCut is None:
        return ()
    if AssignmentCut.PairwiseConflictEdges:
        return AssignmentCut.PairwiseConflictEdges
    if (
        AssignmentCut.Classification.value
        in {
            "mandatory-boundary-capacity-cut",
            "portal-coverage-pair-conflict",
        }
        and len(AssignmentCut.PriorityRelocationSignals) == 2
    ):
        return (
            tuple(sorted(AssignmentCut.PriorityRelocationSignals)),
        )
    return ()


def RequiresStructuredAssignmentCutRelocation(
    AssignmentCut: RoutingAssignmentCut | None,
) -> bool:
    """Return whether a complete cut should use footprint-neutral joint repair."""
    return (
        AssignmentCut is not None
        and AssignmentCut.Classification.value
        in {
            "saturated-boundary-cut",
            "higher-order-placement-conflict",
            "larger-matching-failure",
            "multi-pair-placement-conflict",
            "mandatory-boundary-capacity-cut",
            "portal-coverage-pair-conflict",
            "relocated-higher-order-conflict",
            "relocated-larger-matching-failure",
            "relocated-multi-pair-conflict",
            "relocated-pairwise-incompatibility",
        }
    )


def BuildEffectiveStructuredRelocationFocus(
    AssignmentCut: RoutingAssignmentCut | None,
    AssignmentConstraints: PlacementAssignmentConstraintSet,
    RelocationPrioritySignals: frozenset[str],
    RequiredRelocationSignals: frozenset[str],
) -> tuple[frozenset[str], frozenset[str]]:
    """Resolve the latest exact repair focus without reviving stale cuts.

    Pairwise assignment constraints remain cumulative placement evidence, but
    a caller-provided priority/required set names the latest exact geometry
    repair.  Re-unioning every historical pair endpoint here makes a newly
    promoted mandatory-access conflict compete with older cuts and can move
    clusters unrelated to the resource collision.  Only derive a focus when
    the caller did not provide one.
    """
    PairwiseSignals = frozenset(
        Signal
        for Edge in (
            *BuildEffectiveAssignmentCutPairwiseEdges(AssignmentCut),
            *AssignmentConstraints.PairwiseConflictEdges,
        )
        for Signal in Edge
    )
    CutPrioritySignals = (
        frozenset(AssignmentCut.PriorityRelocationSignals)
        if AssignmentCut is not None
        else frozenset()
    )
    DerivedFocus = CutPrioritySignals or PairwiseSignals
    EffectivePrioritySignals = (
        RelocationPrioritySignals or DerivedFocus
    )
    EffectiveRequiredSignals = RequiredRelocationSignals
    if (
        not EffectiveRequiredSignals
        and RequiresStructuredAssignmentCutRelocation(AssignmentCut)
    ):
        EffectiveRequiredSignals = DerivedFocus
    return EffectivePrioritySignals, EffectiveRequiredSignals


def ShouldExpandBoundaryEscapeGeometry(
    *,
    PackedMode: bool,
    ClusterIndex: int,
    BoundaryEscapeRelocationClusters: frozenset[int],
    PackedAccessRepairClusters: frozenset[int],
    RequiredRelocationSignals: frozenset[str],
    RelocationVariant: int,
    RelocationPrioritySignalCount: int,
    LocalGeometryRepairClusters: frozenset[int],
    StructuredAssignmentCutRelocation: bool,
) -> bool:
    """Gate the broad boundary shell behind exhausted non-structured repair."""
    return (
        PackedMode
        and ClusterIndex in BoundaryEscapeRelocationClusters
        and ClusterIndex not in PackedAccessRepairClusters
        and (
            not RequiredRelocationSignals
            or RelocationVariant >= 12
        )
        and (
            RelocationPrioritySignalCount > 1
            or ClusterIndex in LocalGeometryRepairClusters
        )
        and not StructuredAssignmentCutRelocation
    )


def ShouldReleasePartialLocalTreeBeforeSearch(
    *,
    ClusterCount: int,
    HasRelocationSignals: bool,
    LocalTargetCount: int,
    TotalTargetCount: int,
) -> bool:
    """Skip a local tree whose final feedback policy must release it."""
    return (
        ClusterCount > 4
        and HasRelocationSignals
        and LocalTargetCount != TotalTargetCount
    )


def SelectFocusedCutEpochClusters(
    RankedRelocationClusters: Iterable[int],
    Enabled: bool,
    MaximumClusters: int = 2,
) -> frozenset[int]:
    """Select a bounded cluster-local ECO focus from structural cut ranking."""
    if not Enabled or MaximumClusters <= 0:
        return frozenset()
    return frozenset(
        int(ClusterIndex)
        for ClusterIndex in tuple(RankedRelocationClusters)[
            :MaximumClusters
        ]
    )


def SelectFocusedTopologyFrontierClusters(
    CurrentRankedClusters: Iterable[int],
    PreviousRankedClusters: Iterable[int],
    Enabled: bool,
    MaximumClusters: int = 3,
) -> frozenset[int]:
    """Retain current-cut clusters plus one bounded prior-cut representative."""
    if not Enabled or MaximumClusters <= 0:
        return frozenset()
    Selected: list[int] = []
    for ClusterIndex in CurrentRankedClusters:
        Normalized = int(ClusterIndex)
        if Normalized not in Selected:
            Selected.append(Normalized)
        if len(Selected) >= min(2, MaximumClusters):
            break
    if len(Selected) < MaximumClusters:
        for ClusterIndex in PreviousRankedClusters:
            Normalized = int(ClusterIndex)
            if Normalized not in Selected:
                Selected.append(Normalized)
                break
    return frozenset(Selected[:MaximumClusters])


def SelectFocusedConstraintComponentClusters(
    CurrentFocusedClusters: Iterable[int],
    RankedConstraintClusters: Iterable[int],
    Enabled: bool,
    MaximumClusters: int = 6,
) -> frozenset[int]:
    """Extend one cut ECO across its bounded recurrent cluster component."""
    if not Enabled or MaximumClusters <= 0:
        return frozenset(map(int, CurrentFocusedClusters))
    Selected: list[int] = []
    for Values in (CurrentFocusedClusters, RankedConstraintClusters):
        for ClusterIndex in Values:
            Normalized = int(ClusterIndex)
            if Normalized not in Selected:
                Selected.append(Normalized)
            if len(Selected) >= MaximumClusters:
                return frozenset(Selected)
    return frozenset(Selected)


def SelectInternalPinBankGeometrySignals(
    *,
    Enabled: bool,
    RepairSignals: Iterable[str],
    CoordinatedCandidateDiversificationSignals: Iterable[str],
) -> frozenset[str]:
    """Keep physical ECO focus narrower than cumulative routing diversity."""
    if not Enabled:
        return frozenset()
    ExactRepairSignals = frozenset(map(str, RepairSignals))
    if ExactRepairSignals:
        return ExactRepairSignals
    return frozenset(map(
        str,
        CoordinatedCandidateDiversificationSignals,
    ))


def BuildJointPortfolioBaseRelocationControls(
    *,
    RelocationVariant: int,
    JointPlacementCandidateIndex: int,
    RequiresStructuredJointRelocation: bool,
    PreservePortfolioBaseAssignment: bool,
) -> tuple[int, bool]:
    """Keep one retained portfolio on one immutable base slot assignment."""
    CandidateOffset = (
        JointPlacementCandidateIndex
        if (
            RequiresStructuredJointRelocation
            and not PreservePortfolioBaseAssignment
        )
        else 0
    )
    return (
        max(0, RelocationVariant - 1) + CandidateOffset,
        bool(
            RequiresStructuredJointRelocation
            and JointPlacementCandidateIndex > 0
            and not PreservePortfolioBaseAssignment
        ),
    )


def OptimizeJointClusterPlacement(
    Module: Any,
    Clusters: tuple[tuple[str, ...], ...],
    Levels: dict[str, int],
    VariantsByCluster: dict[int, tuple[ClusterLayoutVariant, ...]],
    BeamWidth: int,
    PassLimit: int,
    RetainedCandidates: int = 1,
    CandidateIndex: int = 0,
    InitialAssignment: dict[int, tuple[int, int]] | None = None,
    FixedSlotClusters: frozenset[int] = frozenset(),
    AssignmentCut: RoutingAssignmentCut | None = None,
    AssignmentConstraints: PlacementAssignmentConstraintSet = (
        PlacementAssignmentConstraintSet()
    ),
    BoundaryContractCapacity: int = 0,
    EnableClusterInterfacePlacementFeasibility: bool = False,
    FocusedOptimizationClusters: frozenset[int] | None = None,
    FrontierAssignmentCuts: tuple[RoutingAssignmentCut, ...] = (),
    LogicalComponentByGate: Mapping[str, int] | None = None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> tuple[
    dict[int, tuple[int, int]],
    dict[int, ClusterLayoutVariant],
    dict[str, object],
]:
    """Jointly optimize packed-cluster grid slots and rigid transforms."""
    Assignment, Columns, Rows = OptimizeClusterSlots(
        Module,
        Clusters,
        Levels,
        LogicalComponentByGate=LogicalComponentByGate,
        WorkCheck=WorkCheck,
    )
    if InitialAssignment is not None:
        Assignment = dict(InitialAssignment)
        Columns = max((Slot[0] for Slot in Assignment.values()), default=-1) + 1
        Rows = max((Slot[1] for Slot in Assignment.values()), default=-1) + 1
    Count = len(Clusters)
    if Count == 0:
        return Assignment, {}, {"Enabled": True, "CandidateCount": 0}
    BoundaryBundles = BuildClusterBoundaryBundles(Module, Clusters)
    ClusterByGate = {
        GateName: ClusterIndex
        for ClusterIndex, Names in enumerate(Clusters)
        for GateName in Names
    }
    Producers = {
        Signal: Gate
        for Gate in Module.Gates
        for Signal in Gate.Outputs
    }
    LogicalComponentByGate = dict(
        LogicalComponentByGate or {}
    )
    DirectedWeights: dict[tuple[int, int], int] = {}
    InputWeights = {Index: 0 for Index in range(Count)}
    OutputWeights = {Index: 0 for Index in range(Count)}
    for Gate in Module.Gates:
        Target = ClusterByGate.get(Gate.Name)
        for Signal in Gate.Inputs:
            Source = ClusterByGate.get(Producers[Signal].Name)
            if Source is None and Target is not None:
                InputWeights[Target] += 1
            elif Source is not None and Target is not None and Source != Target:
                DirectedWeights[Source, Target] = (
                    DirectedWeights.get((Source, Target), 0)
                    + (
                        16
                        if (
                            LogicalComponentByGate
                            and LogicalComponentByGate.get(
                                Producers[Signal].Name
                            )
                            == LogicalComponentByGate.get(Gate.Name)
                            and Producers[Signal].Name
                            in LogicalComponentByGate
                            and Gate.Name in LogicalComponentByGate
                        )
                        else 1
                    )
                )
            elif Source is not None and Gate.Kind.value == "OUTPUT":
                OutputWeights[Source] += 1
    ActiveConstraintWorkingSet = SelectPlacementConstraintWorkingSet(
        AssignmentCut,
        AssignmentConstraints,
        FrontierAssignmentCuts,
        ExpandConnectedComponent=bool(FocusedOptimizationClusters),
    )
    BoundedCompleteProofSignals = (
        frozenset(AssignmentCut.PriorityRelocationSignals)
        if (
            AssignmentCut is not None
            and AssignmentCut.CompleteAssignmentCutProof
            and AssignmentCut.PriorityRelocationSignals
        )
        else frozenset()
    )
    CurrentCutPairwiseEdges = tuple(
        Edge
        for Edge in BuildEffectiveAssignmentCutPairwiseEdges(
            AssignmentCut
        )
        if (
            not BoundedCompleteProofSignals
            or set(Edge).issubset(BoundedCompleteProofSignals)
        )
    )
    FrontierPairwiseEdges = () if BoundedCompleteProofSignals else tuple(
        Edge
        for Cut in FrontierAssignmentCuts
        for Edge in BuildEffectiveAssignmentCutPairwiseEdges(Cut)
    )
    EffectivePairwiseConflictEdges = tuple(sorted({
        *CurrentCutPairwiseEdges,
        *FrontierPairwiseEdges,
        *ActiveConstraintWorkingSet.PairwiseConflictEdges,
    }))
    EffectiveObservedInterfaceConflictEdges = tuple(sorted(
        ActiveConstraintWorkingSet.ObservedInterfaceConflictEdges
    ))
    CurrentCutHigherOrderSignals = (
        BuildAssignmentCutHigherOrderSignalSet(AssignmentCut)
    )
    FrontierHigherOrderSignalSets = () if BoundedCompleteProofSignals else tuple(
        Signals
        for Cut in FrontierAssignmentCuts
        if (
            Signals := BuildAssignmentCutHigherOrderSignalSet(Cut)
        )
    )
    EffectiveHigherOrderConflictSets = tuple(sorted({
        *ActiveConstraintWorkingSet.HigherOrderSignalSets,
        *FrontierHigherOrderSignalSets,
        *((CurrentCutHigherOrderSignals,)
          if CurrentCutHigherOrderSignals else ()),
    }))
    InterfaceConstraintSignals = {
        Signal
        for Edge in EffectivePairwiseConflictEdges
        for Signal in Edge
    } | {
        Signal
        for Edge in EffectiveObservedInterfaceConflictEdges
        for Signal in Edge
    } | {
        Signal
        for Signals in EffectiveHigherOrderConflictSets
        for Signal in Signals
    }
    FocusedConstraintComponentClusters = (
        SelectFocusedConstraintComponentClusters(
            FocusedOptimizationClusters or (),
            PrioritizeRelocationClusters(
                Module,
                Clusters,
                frozenset(InterfaceConstraintSignals),
            ),
            Enabled=bool(FocusedOptimizationClusters),
        )
    )
    ClusterInterfaceTopologyModel = (
        BuildClusterInterfaceTopology(
            Module,
            Clusters,
            None,
        )
        if EnableClusterInterfacePlacementFeasibility
        else None
    )
    CutClusterInterfaceTopologyModel = (
        BuildClusterInterfaceTopology(
            Module,
            Clusters,
            InterfaceConstraintSignals,
        )
        if (
            EnableClusterInterfacePlacementFeasibility
            and InterfaceConstraintSignals
        )
        else None
    )
    ObservedClusterInterfaceTopologyModel = (
        BuildClusterInterfaceTopology(
            Module,
            Clusters,
            {
                Signal
                for Edge in EffectiveObservedInterfaceConflictEdges
                for Signal in Edge
            },
        )
        if (
            EnableClusterInterfacePlacementFeasibility
            and EffectiveObservedInterfaceConflictEdges
        )
        else None
    )
    ExactPairClusterEdges: set[tuple[int, int]] = set()
    if EffectivePairwiseConflictEdges:
        for FirstSignal, SecondSignal in EffectivePairwiseConflictEdges:
            # A capacity-one signal pair proves one competing access
            # interface, not that every producer/consumer endpoint of the
            # two fanout nets must be mutually separated.  Project each
            # signal to its deterministic topology-ranked interface cluster
            # and keep one distinct representative pair.  The former cross
            # product over-constrained high-fanout reconvergent cuts until no
            # exact-legal orientation remained.
            FirstClusters = PrioritizeRelocationClusters(
                Module,
                Clusters,
                frozenset((FirstSignal,)),
            )
            SecondClusters = PrioritizeRelocationClusters(
                Module,
                Clusters,
                frozenset((SecondSignal,)),
            )
            RepresentativePair = next(
                (
                    tuple(sorted((FirstCluster, SecondCluster)))
                    for FirstCluster in FirstClusters
                    for SecondCluster in SecondClusters
                    if FirstCluster != SecondCluster
                ),
                None,
            )
            if RepresentativePair is not None:
                ExactPairClusterEdges.add(RepresentativePair)
    HigherOrderProjectedClusterEdges: set[tuple[int, int]] = set()
    HigherOrderRepresentativeClusters: set[int] = set()
    for Signals in EffectiveHigherOrderConflictSets:
        for Signal in Signals:
            RankedSignalClusters = PrioritizeRelocationClusters(
                Module,
                Clusters,
                frozenset((Signal,)),
            )
            if RankedSignalClusters:
                HigherOrderRepresentativeClusters.add(
                    RankedSignalClusters[0]
                )
        RankedClusters = PrioritizeRelocationClusters(
            Module,
            Clusters,
            frozenset(Signals),
        )
        if len(RankedClusters) >= 2:
            HigherOrderProjectedClusterEdges.add(
                tuple(sorted(RankedClusters[:2]))
            )
    HigherOrderProjectedClusterEdges.difference_update(
        ExactPairClusterEdges
    )
    ExactPairClusterEdgesTuple = tuple(sorted(ExactPairClusterEdges))
    HigherOrderProjectedClusterEdgesTuple = tuple(
        sorted(HigherOrderProjectedClusterEdges)
    )
    CutPairClusterEdges = tuple(sorted({
        *ExactPairClusterEdges,
        *HigherOrderProjectedClusterEdges,
    }))
    ExactPairClusters = frozenset(
        ClusterIndex
        for Edge in ExactPairClusterEdgesTuple
        for ClusterIndex in Edge
    )
    EffectiveFixedSlotClusters = (
        FixedSlotClusters - ExactPairClusters
    )
    CutPairSignals = frozenset(
        Signal
        for Edge in EffectivePairwiseConflictEdges
        for Signal in Edge
    )
    ConstraintSignals = frozenset(
        Signal
        for Signals in EffectiveHigherOrderConflictSets
        for Signal in Signals
    )
    CurrentCutSignals = (
        BoundedCompleteProofSignals
        or frozenset((
            *(
                AssignmentCut.PriorityRelocationSignals
                if AssignmentCut is not None
                else ()
            ),
            *(
                AssignmentCut.RelocationSignals
                if AssignmentCut is not None
                else ()
            ),
            *(
                AssignmentCut.ConflictSignals
                if AssignmentCut is not None
                else ()
            ),
            *(
                AssignmentCut.NoCandidateSignals
                if AssignmentCut is not None
                else ()
            ),
        ))
    )
    CurrentPairSignals = frozenset(
        Signal
        for Edge in CurrentCutPairwiseEdges
        for Signal in Edge
    )
    ResidualCurrentCutSignals = (
        CurrentCutSignals
        - CurrentPairSignals
        - frozenset(CurrentCutHigherOrderSignals)
    )
    StructuredCutClusters = (
        frozenset((
            *ExactPairClusters,
            *HigherOrderRepresentativeClusters,
            *BuildRelocationClusterSet(
                Module,
                Clusters,
                ResidualCurrentCutSignals,
            ),
        ))
        if (
            AssignmentCut is not None
            or AssignmentConstraints.HasActivePlacementConstraints
        )
        else frozenset()
    )
    JointOptimizationClusterIndices = (
        tuple(sorted(
            ClusterIndex
            for ClusterIndex in FocusedConstraintComponentClusters
            if 0 <= ClusterIndex < Count
        ))
        if FocusedConstraintComponentClusters
        else (
            tuple(sorted(StructuredCutClusters))
            if StructuredCutClusters
            else tuple(range(Count))
        )
    )
    if not JointOptimizationClusterIndices:
        JointOptimizationClusterIndices = tuple(range(Count))
    HasStructuredCut = (
        AssignmentCut is not None
        or AssignmentConstraints.HasActivePlacementConstraints
    )
    EffectivePassLimit = (
        min(PassLimit, 2)
        if HasStructuredCut
        else (
            min(PassLimit, 4)
            if EnableClusterInterfacePlacementFeasibility
            else PassLimit
        )
    )
    InitialSlots = tuple(Assignment[Index] for Index in range(Count))
    InitialOrientations = tuple(0 for _ in range(Count))
    Slots = tuple(
        (Column, Row)
        for Column in range(Columns)
        for Row in range(Rows)
    )
    VariantWidths = {
        Index: tuple(Variant.Width for Variant in Variants)
        for Index, Variants in VariantsByCluster.items()
    }
    VariantDepths = {
        Index: tuple(Variant.Depth for Variant in Variants)
        for Index, Variants in VariantsByCluster.items()
    }
    SourceFaces = {
        Index: tuple(
            TransformDirection(
                (0, 0, 1),
                Variant.Rotation,
                Variant.MirrorX,
            )
            for Variant in Variants
        )
        for Index, Variants in VariantsByCluster.items()
    }
    TargetFaces = {
        Index: tuple(
            TransformDirection(
                (0, 0, -1),
                Variant.Rotation,
                Variant.MirrorX,
            )
            for Variant in Variants
        )
        for Index, Variants in VariantsByCluster.items()
    }

    def Centers(
        SlotsByCluster: tuple[tuple[int, int], ...],
        Orientations: tuple[int, ...],
    ) -> dict[int, tuple[float, float]]:
        ColumnWidths = [1] * Columns
        RowDepths = [1] * Rows
        for Index, (Column, Row) in enumerate(SlotsByCluster):
            ColumnWidths[Column] = max(
                ColumnWidths[Column],
                VariantWidths[Index][Orientations[Index]],
            )
            RowDepths[Row] = max(
                RowDepths[Row],
                VariantDepths[Index][Orientations[Index]],
            )
        ColumnOrigins: dict[int, int] = {}
        NextX = 0
        for Column in range(Columns):
            ColumnOrigins[Column] = NextX
            NextX += ColumnWidths[Column] + 2
        RowOrigins: dict[int, int] = {}
        NextZ = 0
        for Row in range(Rows):
            RowOrigins[Row] = NextZ
            NextZ += RowDepths[Row] + 1
        return {
            Index: (
                ColumnOrigins[Slot[0]]
                + VariantWidths[Index][Orientations[Index]] / 2,
                RowOrigins[Slot[1]]
                + VariantDepths[Index][Orientations[Index]] / 2,
            )
            for Index, Slot in enumerate(SlotsByCluster)
        }

    def Score(
        SlotsByCluster: tuple[tuple[int, int], ...],
        Orientations: tuple[int, ...],
    ) -> tuple[object, ...]:
        StateAssignment = {
            ClusterIndex: Slot
            for ClusterIndex, Slot in enumerate(SlotsByCluster)
        }
        StateVariants = (
            {
                ClusterIndex: VariantsByCluster[ClusterIndex][
                    Orientations[ClusterIndex]
                ]
                for ClusterIndex in range(Count)
            }
            if (
                CutClusterInterfaceTopologyModel is not None
                or ObservedClusterInterfaceTopologyModel is not None
            )
            else {}
        )
        CenterByCluster = Centers(SlotsByCluster, Orientations)
        BoundaryContract = (
            ScoreClusterBoundaryContracts(
                BoundaryBundles,
                StateAssignment,
                BoundaryContractCapacity,
            )
            if BoundaryContractCapacity > 0
            else ClusterBoundaryContractScore(0, 0, 0)
        )
        Cost = 0
        for (Source, Target), Weight in DirectedWeights.items():
            SourceX, SourceZ = CenterByCluster[Source]
            TargetX, TargetZ = CenterByCluster[Target]
            DeltaX = TargetX - SourceX
            DeltaZ = TargetZ - SourceZ
            Cost += Weight * int(10 * (abs(DeltaX) + abs(DeltaZ)))
            if DeltaX < 0:
                Cost += Weight * 4
            Direction = (
                (1 if DeltaX >= 0 else -1, 0, 0)
                if abs(DeltaX) >= abs(DeltaZ)
                else (0, 0, 1 if DeltaZ >= 0 else -1)
            )
            SourceFace = SourceFaces[Source][Orientations[Source]]
            TargetFace = TargetFaces[Target][Orientations[Target]]
            if SourceFace[0] * Direction[0] + SourceFace[2] * Direction[2] <= 0:
                Cost += Weight * 8
            if TargetFace[0] * Direction[0] + TargetFace[2] * Direction[2] >= 0:
                Cost += Weight * 8
        MaximumCenterX = max(
            Value[0] for Value in CenterByCluster.values()
        )
        Cost += sum(
            InputWeights[Index] * int(CenterByCluster[Index][0]) * 5
            + OutputWeights[Index]
            * int(MaximumCenterX - CenterByCluster[Index][0])
            * 5
            for Index in range(Count)
        )
        ExactPairAdjacencyViolations = 0
        # A proven capacity-one pair is stronger evidence than the projected
        # leading edge of a higher-order cut.  Order the bounded beam by exact
        # pair separation first, while retaining the original placement cost
        # as the public SearchScore/SelectedScore.
        for FirstCluster, SecondCluster in ExactPairClusterEdgesTuple:
            FirstSlot = SlotsByCluster[FirstCluster]
            SecondSlot = SlotsByCluster[SecondCluster]
            SlotDistance = (
                abs(FirstSlot[0] - SecondSlot[0])
                + abs(FirstSlot[1] - SecondSlot[1])
            )
            if SlotDistance <= 1:
                ExactPairAdjacencyViolations += 1
                Cost += 200
            elif (
                FirstSlot[0] == SecondSlot[0]
                or FirstSlot[1] == SecondSlot[1]
            ):
                Cost += 40
        # Higher-order projections remain a soft distance hint: they summarize
        # one reported cut, but are not themselves proven pair incompatibility.
        # Keep them out of the lexicographic exact-pair objective.
        for FirstCluster, SecondCluster in (
            HigherOrderProjectedClusterEdgesTuple
        ):
            FirstSlot = SlotsByCluster[FirstCluster]
            SecondSlot = SlotsByCluster[SecondCluster]
            SlotDistance = (
                abs(FirstSlot[0] - SecondSlot[0])
                + abs(FirstSlot[1] - SecondSlot[1])
            )
            if SlotDistance <= 1:
                Cost += 200
            elif (
                FirstSlot[0] == SecondSlot[0]
                or FirstSlot[1] == SecondSlot[1]
            ):
                Cost += 40
        InterfaceScore = (
            ScoreClusterInterfacePlacement(
                Module,
                Clusters,
                StateAssignment,
                StateVariants,
                EffectivePairwiseConflictEdges,
                EffectiveHigherOrderConflictSets,
                Topology=CutClusterInterfaceTopologyModel,
            )
            if CutClusterInterfaceTopologyModel is not None
            else None
        )
        ObservedInterfaceBankConflicts = (
            ScoreClusterInterfacePlacement(
                Module,
                Clusters,
                StateAssignment,
                StateVariants,
                EffectiveObservedInterfaceConflictEdges,
                Topology=ObservedClusterInterfaceTopologyModel,
            ).PairBankConflicts
            if ObservedClusterInterfaceTopologyModel is not None
            else 0
        )
        AllInterfaceFacingMismatches = (
            ScoreClusterInterfaceFacingMismatchesForOrientations(
                ClusterInterfaceTopologyModel,
                StateAssignment,
                Orientations,
                SourceFaces,
                TargetFaces,
            )
            if ClusterInterfaceTopologyModel is not None
            else 0
        )
        return (
            (
                (
                    InterfaceScore.PairBankConflicts
                    if InterfaceScore is not None
                    else 0
                ),
                (
                    InterfaceScore.HigherOrderBankPressure
                    if InterfaceScore is not None
                    else 0
                ),
                (
                    InterfaceScore.HigherOrderPeakBankDemand
                    if InterfaceScore is not None
                    else 0
                ),
                (
                    InterfaceScore.HigherOrderBankExcessDemand
                    if InterfaceScore is not None
                    else 0
                ),
                (
                    InterfaceScore.HigherOrderOverloadedBankCount
                    if InterfaceScore is not None
                    else 0
                ),
                ExactPairAdjacencyViolations,
                AllInterfaceFacingMismatches,
                ObservedInterfaceBankConflicts,
            ),
            BoundaryContract.OverflowLanes,
            BoundaryContract.PeakBoundaryDemand,
            Cost,
            SlotsByCluster,
            Orientations,
        )

    Beam = [(Score(InitialSlots, InitialOrientations), InitialSlots, InitialOrientations)]

    def SelectDiverseBeam(
        OrderedStates: list[
            tuple[tuple[object, ...], tuple[tuple[int, int], ...], tuple[int, ...]]
        ],
        Limit: int = BeamWidth,
    ) -> list[
        tuple[tuple[object, ...], tuple[tuple[int, int], ...], tuple[int, ...]]
    ]:
        """Keep score-competitive orientation representatives at every pass.

        A plain score prefix makes a chain of identical clusters retain only
        changes to its final members: changing an early member temporarily
        increases directed-distance cost and is pruned before the beam can
        discover its access benefit.  Reserve one best representative for
        every cluster whose rigid transform differs from the primary state,
        then fill the remaining beam by maximum transform separation.
        """
        if not OrderedStates:
            return []
        Retained = [OrderedStates[0]]
        Pending = list(OrderedStates[1:])
        PrimaryOrientations = Retained[0][2]
        DiversityClusterIndices = JointOptimizationClusterIndices
        DiversityScanCount = 0

        def FindRepresentativeIndex(
            Predicate: Callable[
                [tuple[
                    tuple[object, ...],
                    tuple[tuple[int, int], ...],
                    tuple[int, ...],
                ]],
                bool,
            ],
            ScanPhase: str,
        ) -> int | None:
            nonlocal DiversityScanCount
            for Index, State in enumerate(Pending):
                DiversityScanCount += 1
                if (
                    WorkCheck is not None
                    and DiversityScanCount % 512 == 0
                ):
                    WorkCheck({
                        "Phase": "joint-cluster-placement-diversity",
                        "ScanPhase": ScanPhase,
                        "ScannedStates": DiversityScanCount,
                        "PendingStates": len(Pending),
                    })
                if Predicate(State):
                    return Index
            return None

        for ClusterIndex in DiversityClusterIndices:
            RepresentativeIndex = FindRepresentativeIndex(
                lambda State, CurrentCluster=ClusterIndex: (
                    State[2][CurrentCluster]
                    != PrimaryOrientations[CurrentCluster]
                ),
                "orientation",
            )
            if RepresentativeIndex is not None:
                Retained.append(Pending.pop(RepresentativeIndex))
        # A slot permutation is a real placement alternative even when every
        # cluster keeps the same local template.  Keep a representative that
        # changes each cluster's slot as well, preferring one that also has a
        # new rigid-transform vector so the retained portfolio never spends a
        # candidate on a duplicate orientation state.
        for ClusterIndex in DiversityClusterIndices:
            ExistingTransforms = {
                Existing[2] for Existing in Retained
            }
            RepresentativeIndex = FindRepresentativeIndex(
                lambda State, CurrentCluster=ClusterIndex: (
                    State[1][CurrentCluster] != InitialSlots[CurrentCluster]
                    and State[2] not in ExistingTransforms
                ),
                "slot",
            )
            if RepresentativeIndex is not None:
                Retained.append(Pending.pop(RepresentativeIndex))
        # The frontier can contain tens of thousands of states.  The anchor
        # representatives above are the required diversity guarantee; fill
        # the remainder in already-sorted score order rather than repeatedly
        # scanning the complete frontier for a maximum-Hamming-distance tie.
        # That quadratic selection consumed the entire shared RCA8 deadline
        # during placement before a single exact routing candidate ran.
        Retained.extend(Pending[:max(0, Limit - len(Retained))])
        return Retained

    CandidateCount = 1
    CandidateEvaluationCount = 0
    for PassIndex in range(EffectivePassLimit):
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "joint-cluster-placement",
                "PassIndex": PassIndex,
                "BeamStates": len(Beam),
                "ClusterCount": Count,
            })
        Candidates: dict[
            tuple[tuple[tuple[int, int], ...], tuple[int, ...]],
            tuple[object, ...],
        ] = {}
        for _Score, PreviousSlots, PreviousOrientations in Beam:
            Candidates[PreviousSlots, PreviousOrientations] = _Score
            OccupantBySlot = {
                Slot: Index
                for Index, Slot in enumerate(PreviousSlots)
            }
            for ClusterIndex in JointOptimizationClusterIndices:
                CandidateSlotsForCluster = (
                    (PreviousSlots[ClusterIndex],)
                    if ClusterIndex in EffectiveFixedSlotClusters
                    else Slots
                )
                for Slot in CandidateSlotsForCluster:
                    Occupant = OccupantBySlot.get(Slot)
                    if (
                        Occupant is not None
                        and Occupant != ClusterIndex
                        and Occupant in EffectiveFixedSlotClusters
                    ):
                        continue
                    for OrientationIndex in range(len(VariantsByCluster[ClusterIndex])):
                        CandidateSlots = list(PreviousSlots)
                        CandidateSlots[ClusterIndex] = Slot
                        if Occupant is not None and Occupant != ClusterIndex:
                            CandidateSlots[Occupant] = PreviousSlots[ClusterIndex]
                        CandidateOrientations = list(PreviousOrientations)
                        CandidateOrientations[ClusterIndex] = OrientationIndex
                        Key = tuple(CandidateSlots), tuple(CandidateOrientations)
                        if Key not in Candidates:
                            CandidateEvaluationCount += 1
                            if (
                                WorkCheck is not None
                                and CandidateEvaluationCount % 256 == 0
                            ):
                                WorkCheck({
                                    "Phase": (
                                        "joint-cluster-placement-candidate"
                                    ),
                                    "PassIndex": PassIndex,
                                    "EvaluatedCandidates": (
                                        CandidateEvaluationCount
                                    ),
                                    "CurrentFrontier": len(Candidates),
                                })
                            Candidates[Key] = Score(*Key)
        CandidateCount += len(Candidates)
        Ordered = sorted(
            (ScoreValue, SlotsValue, OrientationValue)
            for (SlotsValue, OrientationValue), ScoreValue in Candidates.items()
        )
        NextBeam = SelectDiverseBeam(Ordered)
        if [(Value[1], Value[2]) for Value in NextBeam] == [
            (Value[1], Value[2]) for Value in Beam
        ]:
            Beam = NextBeam
            break
        Beam = NextBeam
    if CandidateIndex < 0:
        raise ValueError("Joint placement candidate index cannot be negative")
    if RetainedCandidates < 1:
        raise ValueError("Joint placement must retain at least one candidate")

    # The beam is intentionally retained rather than collapsing to one
    # center-score winner.  Final placement materializes every retained state
    # and measures exact access/escape legality before the router receives it.
    # Prefer states that differ in the actual boundary-bank ownership pattern.
    # Rotation labels and slot distance are only tie-breakers: they are not
    # useful diversity when the same terminals still contend for the same
    # capacity-one interface.
    OrderedBeam = sorted(Beam)
    InterfaceFeasibleBeam = [
        State for State in OrderedBeam
        if State[0][0][0] == 0
    ]
    InterfaceRejectedStateCount = 0
    if (
        EnableClusterInterfacePlacementFeasibility
        and EffectivePairwiseConflictEdges
        and InterfaceFeasibleBeam
    ):
        InterfaceRejectedStateCount = (
            len(OrderedBeam) - len(InterfaceFeasibleBeam)
        )
        OrderedBeam = InterfaceFeasibleBeam
    RetainedBeam = [OrderedBeam[0]]
    PendingBeam = list(OrderedBeam[1:])
    SearchRetentionLimit = min(
        len(OrderedBeam),
        (
            RetainedCandidates * 2
            if EnableClusterInterfacePlacementFeasibility
            else RetainedCandidates
        ),
    )

    def JointStateDistance(
        First: tuple[
            tuple[object, ...], tuple[tuple[int, int], ...], tuple[int, ...]
        ],
        Second: tuple[
            tuple[object, ...], tuple[tuple[int, int], ...], tuple[int, ...]
        ],
    ) -> int:
        DiversityClusterIndices = JointOptimizationClusterIndices
        return sum(
            First[1][ClusterIndex] != Second[1][ClusterIndex]
            for ClusterIndex in DiversityClusterIndices
        ) + sum(
            First[2][ClusterIndex] != Second[2][ClusterIndex]
            for ClusterIndex in DiversityClusterIndices
        )

    def InterfaceOwnershipFingerprint(
        State: tuple[
            tuple[object, ...], tuple[tuple[int, int], ...], tuple[int, ...]
        ],
    ) -> str:
        if ClusterInterfaceTopologyModel is None:
            return ""
        _Score, StateSlots, StateOrientations = State
        return ScoreClusterInterfacePlacement(
            Module,
            Clusters,
            {
                ClusterIndex: StateSlots[ClusterIndex]
                for ClusterIndex in range(Count)
            },
            {
                ClusterIndex: VariantsByCluster[ClusterIndex][
                    StateOrientations[ClusterIndex]
                ]
                for ClusterIndex in range(Count)
            },
            EffectivePairwiseConflictEdges,
            EffectiveHigherOrderConflictSets,
            Topology=ClusterInterfaceTopologyModel,
        ).Pattern.OwnershipFingerprint

    while (
        PendingBeam
        and len(RetainedBeam) < SearchRetentionLimit
    ):
        ExistingOwnershipFingerprints = {
            InterfaceOwnershipFingerprint(State)
            for State in RetainedBeam
        }
        DistinctInterfaceIndices = []
        for Index, State in enumerate(PendingBeam):
            if WorkCheck is not None and Index % 512 == 0:
                WorkCheck({
                    "Phase": "joint-cluster-placement-retained-scan",
                    "ScannedStates": Index,
                    "PendingStates": len(PendingBeam),
                    "RetainedStates": len(RetainedBeam),
                })
            if (
                InterfaceOwnershipFingerprint(State)
                not in ExistingOwnershipFingerprints
            ):
                DistinctInterfaceIndices.append(Index)
        CandidateIndices = (
            DistinctInterfaceIndices
            if DistinctInterfaceIndices
            else list(range(len(PendingBeam)))
        )
        BestIndex = CandidateIndices[0]
        BestKey: tuple[object, ...] | None = None
        for ScanIndex, Index in enumerate(CandidateIndices, start=1):
            if WorkCheck is not None and ScanIndex % 512 == 0:
                WorkCheck({
                    "Phase": "joint-cluster-placement-retained-rank",
                    "ScannedStates": ScanIndex,
                    "CandidateStates": len(CandidateIndices),
                    "RetainedStates": len(RetainedBeam),
                })
            CandidateKey = (
                -min(
                    JointStateDistance(PendingBeam[Index], Existing)
                    for Existing in RetainedBeam
                ),
                PendingBeam[Index][0],
                PendingBeam[Index][1],
                PendingBeam[Index][2],
            )
            if BestKey is None or CandidateKey < BestKey:
                BestKey = CandidateKey
                BestIndex = Index
        RetainedBeam.append(PendingBeam.pop(BestIndex))
    if CandidateIndex >= len(RetainedBeam):
        raise ValueError(
            "Joint placement candidate index exceeds retained state count "
            f"({CandidateIndex} >= {len(RetainedBeam)})"
        )
    BestScore, BestSlots, BestOrientations = RetainedBeam[CandidateIndex]
    BestAssignment = {
        Index: BestSlots[Index] for Index in range(Count)
    }
    BestVariants = {
        Index: VariantsByCluster[Index][BestOrientations[Index]]
        for Index in range(Count)
    }
    return BestAssignment, BestVariants, {
        "Enabled": True,
        "BeamWidth": BeamWidth,
        "PassLimit": PassLimit,
        "EffectivePassLimit": EffectivePassLimit,
        "StructuredCutClusters": sorted(StructuredCutClusters),
        "FocusedOptimizationClusters": (
            sorted(FocusedOptimizationClusters)
            if FocusedOptimizationClusters
            else []
        ),
        "JointOptimizationClusters": list(
            JointOptimizationClusterIndices
        ),
        "RequestedFixedSlotClusters": sorted(FixedSlotClusters),
        "FixedSlotClusters": sorted(EffectiveFixedSlotClusters),
        "CandidateCount": CandidateCount,
        "SearchRetentionLimit": SearchRetentionLimit,
        "PublishedRetentionLimit": RetainedCandidates,
        "CutPairClusterEdges": [
            list(Edge) for Edge in CutPairClusterEdges
        ],
        "ExactPairClusterEdges": [
            list(Edge) for Edge in ExactPairClusterEdgesTuple
        ],
        "HigherOrderProjectedClusterEdges": [
            list(Edge) for Edge in HigherOrderProjectedClusterEdgesTuple
        ],
        "AssignmentConstraints": AssignmentConstraints.ToDictionary(),
        "ActiveConstraintWorkingSet": (
            ActiveConstraintWorkingSet.ToDictionary()
        ),
        "EffectivePairwiseConflictEdges": [
            list(Edge) for Edge in EffectivePairwiseConflictEdges
        ],
        "EffectiveObservedInterfaceConflictEdges": [
            list(Edge)
            for Edge in EffectiveObservedInterfaceConflictEdges
        ],
        "EffectiveHigherOrderConflictSets": [
            list(Signals)
            for Signals in EffectiveHigherOrderConflictSets
        ],
        "ClusterInterfacePlacementFeasibility": {
            "Enabled": EnableClusterInterfacePlacementFeasibility,
            "ExactPairCount": len(EffectivePairwiseConflictEdges),
            "FeasibleStateCount": len(InterfaceFeasibleBeam),
            "RejectedStateCount": InterfaceRejectedStateCount,
            "AppliedAsHardFilter": bool(
                EnableClusterInterfacePlacementFeasibility
                and EffectivePairwiseConflictEdges
                and InterfaceFeasibleBeam
            ),
        },
        "SelectedCandidateIndex": CandidateIndex,
        "SelectedScore": BestScore[3],
        "SelectedInterfacePairBankConflicts": BestScore[0][0],
        "SelectedHigherOrderBankPressure": BestScore[0][1],
        "SelectedHigherOrderPeakBankDemand": BestScore[0][2],
        "SelectedHigherOrderBankExcessDemand": BestScore[0][3],
        "SelectedHigherOrderOverloadedBankCount": BestScore[0][4],
        "SelectedExactPairAdjacencyViolations": BestScore[0][5],
        "SelectedInterfaceFacingMismatches": BestScore[0][6],
        "SelectedObservedInterfaceBankConflicts": BestScore[0][7],
        "SelectedClusterInterfacePlacement": (
            ScoreClusterInterfacePlacement(
                Module,
                Clusters,
                BestAssignment,
                BestVariants,
                EffectivePairwiseConflictEdges,
                EffectiveHigherOrderConflictSets,
                Topology=ClusterInterfaceTopologyModel,
            ).ToDictionary()
            if ClusterInterfaceTopologyModel is not None
            else None
        ),
        "SelectedBoundaryContract": ScoreClusterBoundaryContracts(
            BoundaryBundles,
            BestAssignment,
            BoundaryContractCapacity,
        ).ToDictionary()
        if BoundaryContractCapacity > 0
        else None,
        "SelectedTransforms": {
            str(Index): {
                "Rotation": BestVariants[Index].Rotation,
                "MirrorX": BestVariants[Index].MirrorX,
            }
            for Index in range(Count)
        },
        "RetainedStates": [
            {
                "CandidateIndex": Index,
                "SearchScore": StateScore[3],
                "InterfaceOwnershipFingerprint": (
                    InterfaceOwnershipFingerprint((
                        StateScore,
                        StateSlots,
                        StateOrientations,
                    ))
                ),
                "InterfacePairBankConflicts": StateScore[0][0],
                "HigherOrderBankPressure": StateScore[0][1],
                "HigherOrderPeakBankDemand": StateScore[0][2],
                "HigherOrderBankExcessDemand": StateScore[0][3],
                "HigherOrderOverloadedBankCount": StateScore[0][4],
                "ExactPairAdjacencyViolations": StateScore[0][5],
                "InterfaceFacingMismatches": StateScore[0][6],
                "ObservedInterfaceBankConflicts": StateScore[0][7],
                "ClusterInterfacePlacement": (
                    ScoreClusterInterfacePlacement(
                        Module,
                        Clusters,
                        {
                            ClusterIndex: StateSlots[ClusterIndex]
                            for ClusterIndex in range(Count)
                        },
                        {
                            ClusterIndex: VariantsByCluster[ClusterIndex][
                                StateOrientations[ClusterIndex]
                            ]
                            for ClusterIndex in range(Count)
                        },
                        EffectivePairwiseConflictEdges,
                        EffectiveHigherOrderConflictSets,
                        Topology=ClusterInterfaceTopologyModel,
                    ).ToDictionary()
                    if ClusterInterfaceTopologyModel is not None
                    else None
                ),
                "BoundaryContract": ScoreClusterBoundaryContracts(
                    BoundaryBundles,
                    {
                        ClusterIndex: StateSlots[ClusterIndex]
                        for ClusterIndex in range(Count)
                    },
                    BoundaryContractCapacity,
                ).ToDictionary()
                if BoundaryContractCapacity > 0
                else None,
                "Slots": {
                    str(ClusterIndex): list(StateSlots[ClusterIndex])
                    for ClusterIndex in range(Count)
                },
                "Transforms": {
                    str(ClusterIndex): {
                        "Rotation": VariantsByCluster[ClusterIndex][
                            StateOrientations[ClusterIndex]
                        ].Rotation,
                        "MirrorX": VariantsByCluster[ClusterIndex][
                            StateOrientations[ClusterIndex]
                        ].MirrorX,
                    }
                    for ClusterIndex in range(Count)
                },
            }
            for Index, (StateScore, StateSlots, StateOrientations) in enumerate(
                RetainedBeam
            )
        ],
    }


def BuildRelocationClusterSet(
    Module: Any,
    Clusters: tuple[tuple[str, ...], ...],
    RelocationSignals: frozenset[str] = frozenset(),
) -> frozenset[int]:
    """Map routing offenders to every producer/consumer cluster they touch."""
    if not RelocationSignals:
        return frozenset()
    ClusterByGate = {
        GateName: ClusterIndex
        for ClusterIndex, Names in enumerate(Clusters)
        for GateName in Names
    }
    ProducerBySignal = {
        Signal: Gate.Name
        for Gate in Module.Gates
        for Signal in Gate.Outputs
    }
    Result: set[int] = set()
    for Signal in RelocationSignals:
        ProducerCluster = ClusterByGate.get(ProducerBySignal.get(Signal, ""))
        if ProducerCluster is not None:
            Result.add(ProducerCluster)
        Result.update(
            ClusterByGate[Gate.Name]
            for Gate in Module.Gates
            if Signal in Gate.Inputs and Gate.Name in ClusterByGate
        )
    return frozenset(Result)


def PrioritizeRelocationClusters(
    Module: Any,
    Clusters: tuple[tuple[str, ...], ...],
    RelocationSignals: frozenset[str] = frozenset(),
) -> tuple[int, ...]:
    """Rank clusters by how many reported conflict signals touch them."""
    ClusterByGate = {
        GateName: ClusterIndex
        for ClusterIndex, Names in enumerate(Clusters)
        for GateName in Names
    }
    ProducerBySignal = {
        Signal: Gate.Name
        for Gate in Module.Gates
        for Signal in Gate.Outputs
    }
    Scores: dict[int, int] = {}
    for Signal in RelocationSignals:
        Touched = set()
        ProducerCluster = ClusterByGate.get(ProducerBySignal.get(Signal, ""))
        if ProducerCluster is not None:
            Touched.add(ProducerCluster)
        Touched.update(
            ClusterByGate[Gate.Name]
            for Gate in Module.Gates
            if Signal in Gate.Inputs and Gate.Name in ClusterByGate
        )
        for ClusterIndex in Touched:
            Scores[ClusterIndex] = Scores.get(ClusterIndex, 0) + 1
    return tuple(sorted(Scores, key=lambda Value: (-Scores[Value], Value)))


def RelocateClusterSlots(
    Assignment: dict[int, tuple[int, int]],
    ColumnCount: int,
    RelocationClusters: Iterable[int],
    StackSuppressedClusters: frozenset[int] = frozenset(),
    RelocationOffset: int = 0,
    RotateExactPortfolioSlots: bool = False,
    ForceDedicatedColumns: bool = False,
) -> tuple[dict[int, tuple[int, int]], int]:
    """Move a congestion cut into deterministic unoccupied placement rows."""
    Result = dict(Assignment)
    OrderedClusters = (
        tuple(sorted(RelocationClusters))
        if isinstance(RelocationClusters, set | frozenset)
        else tuple(dict.fromkeys(RelocationClusters))
    )
    Pending = [
        ClusterIndex
        for ClusterIndex in OrderedClusters
        if ClusterIndex not in StackSuppressedClusters
    ]
    if not Pending:
        return Result, ColumnCount
    if ForceDedicatedColumns:
        # A complete multi-pair access cut can remain infeasible while its
        # owners merely trade the same compact slots.  Give only the reported
        # clusters independent columns; this is a bounded cut-local geometry
        # repair, not a global spacing or routing-limit increase.
        NextColumn = max(
            (Column for Column, _Row in Result.values()),
            default=-1,
        ) + 1 + RelocationOffset
        for Offset, ClusterIndex in enumerate(Pending):
            Result[ClusterIndex] = (NextColumn + Offset, 0)
        return Result, max(ColumnCount, NextColumn + len(Pending))
    if len(Pending) > 1:
        ExistingSlots = tuple(Result[ClusterIndex] for ClusterIndex in Pending)
        if len(set(ExistingSlots)) == len(ExistingSlots):
            if len(Pending) % 2 == 0:
                if RotateExactPortfolioSlots and RelocationOffset:
                    # A retained structured portfolio must vary slot
                    # ownership as well as orientation. Keep the established
                    # adjacent-pair swap at offset zero, but rotate all
                    # measured owners for later exact states.
                    Shift = RelocationOffset % len(Pending)
                    for Offset, ClusterIndex in enumerate(Pending):
                        Result[ClusterIndex] = ExistingSlots[
                            (Offset + Shift) % len(Pending)
                        ]
                    return Result, ColumnCount
                # Compose independent exact-cut repairs without replacing the
                # strongest pair geometry.  Each adjacent ranked pair swaps
                # its established slots, so four selected owners express two
                # measured repairs with no footprint growth.
                for Offset in range(0, len(Pending), 2):
                    First = Pending[Offset]
                    Second = Pending[Offset + 1]
                    Result[First] = ExistingSlots[Offset + 1]
                    Result[Second] = ExistingSlots[Offset]
                return Result, ColumnCount
            # Keep a multi-cluster feedback repair within the established
            # footprint.  RelocationOffset already chooses which ranked
            # clusters participate; using it to move only Pending[0] into a
            # distant column silently discarded the remaining exact cut.
            Shift = 1 + RelocationOffset % (len(Pending) - 1)
            for Offset, ClusterIndex in enumerate(Pending):
                Result[ClusterIndex] = ExistingSlots[
                    (Offset + Shift) % len(Pending)
                ]
            return Result, ColumnCount
        # A suppressed vertical stack leaves multiple clusters in the same
        # logical slot.  Rotating identical slots is a no-op, which would
        # later commit physically overlapping NANDs.  Give each member a
        # deterministic dedicated column so the suppression is real geometry
        # rather than a bookkeeping-only placement variant.
        NextColumn = max(
            (Column for Column, _Row in Result.values()),
            default=-1,
        ) + 1 + RelocationOffset
        for Offset, ClusterIndex in enumerate(Pending):
            Result[ClusterIndex] = (NextColumn + Offset, 0)
        return Result, max(ColumnCount, NextColumn + len(Pending))
    NextColumn = max(
        (Column for Column, _Row in Result.values()),
        default=-1,
    ) + 1 + RelocationOffset
    Result[Pending[0]] = (NextColumn, 0)
    return Result, max(ColumnCount, NextColumn + 1)


def BuildInterClusterBoundaryDemand(
    Module: Any,
    Clusters: tuple[tuple[str, ...], ...],
    Assignment: dict[int, tuple[int, int]],
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> tuple[InterClusterBoundaryDemand, ...]:
    """Derive topology-only lane demand across final cluster-grid cuts.

    A signal is counted once per crossed X/Z boundary even when it has several
    consumers beyond that cut.  This lets placement reserve one physical lane
    for a shared routed tree instead of scaling spacing with fanout.
    """
    ClusterByGate = {
        GateName: ClusterIndex
        for ClusterIndex, Names in enumerate(Clusters)
        for GateName in Names
    }
    ProducerClusterBySignal = {
        Signal: ClusterByGate[Gate.Name]
        for Gate in Module.Gates
        if Gate.Name in ClusterByGate
        for Signal in Gate.Outputs
    }
    SignalsByBoundary: dict[tuple[str, int], set[str]] = {}
    for GateIndex, Gate in enumerate(Module.Gates):
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "inter-cluster-boundary-demand",
                "CompletedGates": GateIndex,
                "TotalGates": len(Module.Gates),
                "GateName": Gate.Name,
            })
        TargetCluster = ClusterByGate.get(Gate.Name)
        if TargetCluster is None:
            continue
        TargetSlot = Assignment.get(TargetCluster)
        if TargetSlot is None:
            continue
        for Signal in Gate.Inputs:
            SourceCluster = ProducerClusterBySignal.get(Signal)
            if SourceCluster is None or SourceCluster == TargetCluster:
                continue
            SourceSlot = Assignment.get(SourceCluster)
            if SourceSlot is None:
                continue
            SourceColumn, SourceRow = SourceSlot
            TargetColumn, TargetRow = TargetSlot
            for BoundaryIndex in range(
                min(SourceColumn, TargetColumn),
                max(SourceColumn, TargetColumn),
            ):
                SignalsByBoundary.setdefault(("X", BoundaryIndex), set()).add(
                    Signal
                )
            for BoundaryIndex in range(
                min(SourceRow, TargetRow),
                max(SourceRow, TargetRow),
            ):
                SignalsByBoundary.setdefault(("Z", BoundaryIndex), set()).add(
                    Signal
                )
    return tuple(
        InterClusterBoundaryDemand(
            Axis=Axis,
            BoundaryIndex=BoundaryIndex,
            Signals=tuple(sorted(Signals)),
        )
        for (Axis, BoundaryIndex), Signals in sorted(SignalsByBoundary.items())
    )


def BuildInterClusterGapPlan(
    BoundaryDemand: tuple[InterClusterBoundaryDemand, ...],
    ColumnCount: int,
    RowCount: int,
    RoutingSpacing: int,
    TrackPitch: int,
    Enabled: bool,
) -> InterClusterGapPlan:
    """Allocate optional spacing without exceeding the configured corridor.

    The fixed placement gap remains outside this plan.  When disabled every
    boundary retains the uniform configured spacing, which exactly preserves
    the previous coordinate construction.
    """
    if RoutingSpacing < 0:
        raise ValueError("RoutingSpacing cannot be negative")
    if TrackPitch < 1:
        raise ValueError("TrackPitch must be positive")
    LanesByBoundary = {
        (Record.Axis, Record.BoundaryIndex): Record.RequiredCorridorLanes
        for Record in BoundaryDemand
    }

    def OptionalSpacing(Axis: str, BoundaryIndex: int) -> int:
        if not Enabled:
            return RoutingSpacing
        return min(
            RoutingSpacing,
            LanesByBoundary.get((Axis, BoundaryIndex), 0) * TrackPitch,
        )

    return InterClusterGapPlan(
        Enabled=Enabled,
        RoutingSpacing=RoutingSpacing,
        TrackPitch=TrackPitch,
        ColumnExtraSpacing=tuple(
            (BoundaryIndex, OptionalSpacing("X", BoundaryIndex))
            for BoundaryIndex in range(max(0, ColumnCount - 1))
        ),
        RowExtraSpacing=tuple(
            (BoundaryIndex, OptionalSpacing("Z", BoundaryIndex))
            for BoundaryIndex in range(max(0, RowCount - 1))
        ),
        BoundaryDemand=BoundaryDemand,
    )


def PlacementWireCost(Placed: PlacedDesign) -> int:
    """Return weighted center-to-center wire length."""
    Producers = {
        Signal: Gate
        for Gate in Placed.PlacedGates
        for Signal in Gate.Outputs
    }
    Fanout: dict[str, int] = {}
    for Gate in Placed.PlacedGates:
        for Signal in Gate.Inputs:
            Fanout[Signal] = Fanout.get(Signal, 0) + 1
    Cost = 0
    for Gate in Placed.PlacedGates:
        TargetWidth, TargetDepth = RotatedCellSize(Gate.Kind, Gate.Rotation)
        TargetCenter = (
            Gate.X + TargetWidth / 2,
            Gate.Z + TargetDepth / 2,
        )
        for Signal in Gate.Inputs:
            Producer = Producers.get(Signal)
            if Producer is None:
                continue
            SourceWidth, SourceDepth = RotatedCellSize(
                Producer.Kind,
                Producer.Rotation,
            )
            SourceCenter = (
                Producer.X + SourceWidth / 2,
                Producer.Z + SourceDepth / 2,
            )
            Cost += max(1, Fanout.get(Signal, 1)) * round(
                abs(SourceCenter[0] - TargetCenter[0])
                + abs(SourceCenter[1] - TargetCenter[1])
            )
    return Cost


def EstimatePlacementRoutingCost(
    Placed: PlacedDesign,
    MaximumLayerCount: int = 4,
) -> tuple[int, int, int]:
    """Sketch cheap multilayer routes and estimate blockage and congestion."""
    Footprints: set[tuple[int, int]] = set()
    for Gate in Placed.PlacedGates:
        Width, Depth = RotatedCellSize(Gate.Kind, Gate.Rotation)
        Footprints.update(
            (X, Z)
            for X in range(Gate.X, Gate.X + Width)
            for Z in range(Gate.Z, Gate.Z + Depth)
        )

    Producers: dict[str, tuple[int, int]] = {}
    Targets: dict[str, list[tuple[int, int]]] = {}
    for Gate in Placed.PlacedGates:
        if Gate.OutputPin is not None and Gate.OutputDirection is not None:
            PinX, _PinY, PinZ = Gate.OutputPin
            DirectionX, _DirectionY, DirectionZ = Gate.OutputDirection
            Endpoint = (
                PinX + DirectionX * 2,
                PinZ + DirectionZ * 2,
            )
            for Signal in Gate.Outputs:
                Producers[Signal] = Endpoint
        for InputIndex, Signal in enumerate(Gate.Inputs):
            Pin, Direction = GetGateInputAccess(Gate, InputIndex)
            PinX, _PinY, PinZ = Pin
            DirectionX, _DirectionY, DirectionZ = Direction
            Targets.setdefault(Signal, []).append(
                (
                    PinX + DirectionX * 2,
                    PinZ + DirectionZ * 2,
                )
            )

    Signals = [Signal for Signal in Producers if Targets.get(Signal)]
    if not Signals:
        return (0, 0, 0)
    LayerCount = min(
        MaximumLayerCount,
        max(2, ceil(sqrt(len(Signals)))),
    )
    Occupied = [set() for _Layer in range(LayerCount)]
    ObstaclePressure = 0
    CongestionPressure = 0
    RouteLength = 0
    OrderedSignals = sorted(
        Signals,
        key=lambda Signal: (
            -len(Targets[Signal]),
            -max(
                abs(Producers[Signal][0] - Target[0])
                + abs(Producers[Signal][1] - Target[1])
                for Target in Targets[Signal]
            ),
            Signal,
        ),
    )
    for Signal in OrderedSignals:
        Options = []
        for Layer in range(LayerCount):
            for XFirst in (True, False):
                Guide = BuildSignalGuide(
                    Producers[Signal],
                    Targets[Signal],
                    XFirst,
                )
                ObstacleHits = sum(
                    Position in Footprints
                    for Position in Guide
                )
                Congestion = sum(
                    4 * (Position in Occupied[Layer])
                    + sum(
                        Neighbor in Occupied[Layer]
                        for Neighbor in (
                            (Position[0] + 1, Position[1]),
                            (Position[0] - 1, Position[1]),
                            (Position[0], Position[1] + 1),
                            (Position[0], Position[1] - 1),
                        )
                    )
                    for Position in Guide
                )
                VerticalLength = Layer * 2 * (1 + len(Targets[Signal]))
                Options.append(
                    (
                        ObstacleHits * (LayerCount - Layer),
                        Congestion,
                        len(Guide) + VerticalLength,
                        Layer,
                        Guide,
                    )
                )
        ObstacleCost, Congestion, Length, Layer, Guide = min(Options)
        ObstaclePressure += ObstacleCost
        CongestionPressure += Congestion
        RouteLength += Length
        Occupied[Layer].update(Guide)
    return ObstaclePressure, CongestionPressure, RouteLength


def PlacementCompactKey(
    Placed: PlacedDesign,
) -> tuple[int, int, int, int, int, int]:
    """Score legal placement by routability before occupied bounds."""
    if not Placed.PlacedGates:
        return (0, 0, 0, 0, 0, 0)
    MinimumX = min(Gate.X for Gate in Placed.PlacedGates)
    MinimumZ = min(Gate.Z for Gate in Placed.PlacedGates)
    MaximumX = max(
        Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0]
        for Gate in Placed.PlacedGates
    )
    MaximumZ = max(
        Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1]
        for Gate in Placed.PlacedGates
    )
    Width = MaximumX - MinimumX
    Depth = MaximumZ - MinimumZ
    WireCost = PlacementWireCost(Placed)
    ObstaclePressure, CongestionPressure, RouteLength = (
        EstimatePlacementRoutingCost(Placed)
    )
    Footprint = Width * Depth
    return (
        ObstaclePressure,
        CongestionPressure * 4 + RouteLength,
        Footprint,
        max(Width, Depth),
        Width + Depth,
        WireCost,
    )


@lru_cache(maxsize=4096)
def _PhysicalGateGeometry(
    Kind: str,
    X: int,
    Y: int,
    Z: int,
    Rotation: int,
    MirrorX: bool,
) -> tuple[frozenset[tuple[int, int, int]], frozenset[tuple[int, int, int]]]:
    """Cache exact template occupancy used by packed-cell legalization."""
    Gate = type(
        "CachedPlacedGate",
        (),
        {
            "Name": "CachedCell",
            "Kind": Kind,
            "X": X,
            "Y": Y,
            "Z": Z,
            "Rotation": Rotation,
            "MirrorX": MirrorX,
        },
    )()
    Actual, Electrical, _Solid = BuildPlacedCellGeometry(
        type("CachedPlacement", (), {"PlacedGates": [Gate]})()
    )
    return frozenset(Actual), frozenset(Electrical)


@lru_cache(maxsize=4096)
def _PhysicalGateElectricalExclusions(
    Kind: str,
    X: int,
    Y: int,
    Z: int,
    Rotation: int,
    MirrorX: bool,
) -> frozenset[tuple[int, int, int]]:
    """Cache the exact electrical keep-out for one transformed macro.

    Packed graph states revisit the same physical macro transforms many times.
    The routing technology owns the keep-out rule, so caching its immutable
    result here changes neither the rule nor a conflict decision.
    """
    _Actual, Electrical = _PhysicalGateGeometry(
        Kind,
        X,
        Y,
        Z,
        Rotation,
        MirrorX,
    )
    return frozenset(
        DefaultRedstoneRoutingTechnology.BuildElectricalExclusions(
            set(Electrical)
        )
    )


def PcbGatesConflict(First: Any, Second: Any) -> bool:
    """Reject footprint, pin-access, and template electrical conflicts."""

    def AccessSignals(Gate: Any) -> list[tuple[tuple[int, int, int], str]]:
        Values = []
        if Gate.OutputPin is not None and Gate.OutputDirection is not None:
            X, Y, Z = Gate.OutputPin
            DeltaX, DeltaY, DeltaZ = Gate.OutputDirection
            for Signal in Gate.Outputs:
                Values.extend(
                    (
                        (
                            X + DeltaX * Offset,
                            Y + DeltaY * Offset,
                            Z + DeltaZ * Offset,
                        ),
                        Signal,
                    )
                    for Offset in range(
                        DefaultRedstoneRoutingTechnology.AccessLength
                    )
                )
        for Signal, Pin, Direction in zip(Gate.Inputs, Gate.InputPins, Gate.InputDirections):
            X, Y, Z = Pin
            DeltaX, DeltaY, DeltaZ = Direction
            Values.extend(
                (
                    (
                        X + DeltaX * Offset,
                        Y + DeltaY * Offset,
                        Z + DeltaZ * Offset,
                    ),
                    Signal,
                )
                for Offset in range(
                    DefaultRedstoneRoutingTechnology.AccessLength
                )
            )
        return Values

    if RectanglesOverlap(First, Second):
        return True
    FirstWidth, FirstDepth = RotatedCellSize(First.Kind, First.Rotation)
    SecondWidth, SecondDepth = RotatedCellSize(Second.Kind, Second.Rotation)
    # Gate access clearance is a routing-technology fact.  Keeping it tied
    # to the same derived access length used by terminal and fabric
    # construction prevents a hidden literal from changing legal compact
    # slot geometry when the technology changes.
    BroadPhaseMargin = DefaultRedstoneRoutingTechnology.AccessLength
    if (
        First.X + FirstWidth - 1 + BroadPhaseMargin
        < Second.X - BroadPhaseMargin
        or Second.X + SecondWidth - 1 + BroadPhaseMargin
        < First.X - BroadPhaseMargin
        or First.Z + FirstDepth - 1 + BroadPhaseMargin
        < Second.Z - BroadPhaseMargin
        or Second.Z + SecondDepth - 1 + BroadPhaseMargin
        < First.Z - BroadPhaseMargin
    ):
        return False
    FirstActual, _FirstElectrical = _PhysicalGateGeometry(
        First.Kind,
        First.X,
        First.Y,
        First.Z,
        First.Rotation,
        First.MirrorX,
    )
    SecondActual, _SecondElectrical = _PhysicalGateGeometry(
        Second.Kind,
        Second.X,
        Second.Y,
        Second.Z,
        Second.Rotation,
        Second.MirrorX,
    )
    if (
        _PhysicalGateElectricalExclusions(
            First.Kind,
            First.X,
            First.Y,
            First.Z,
            First.Rotation,
            First.MirrorX,
        )
        & SecondActual
    ) or (
        _PhysicalGateElectricalExclusions(
            Second.Kind,
            Second.X,
            Second.Y,
            Second.Z,
            Second.Rotation,
            Second.MirrorX,
        )
        & FirstActual
    ):
        return True
    if abs(First.Y - Second.Y) >= 3:
        return False
    FirstWidth, FirstDepth = RotatedCellSize(First.Kind, First.Rotation)
    SecondWidth, SecondDepth = RotatedCellSize(Second.Kind, Second.Rotation)
    FirstAccess = AccessSignals(First)
    SecondAccess = AccessSignals(Second)
    FirstSignalsByPosition: dict[tuple[int, int, int], set[str]] = {}
    SecondSignalsByPosition: dict[tuple[int, int, int], set[str]] = {}
    for Position, Signal in FirstAccess:
        FirstSignalsByPosition.setdefault(Position, set()).add(Signal)
    for Position, Signal in SecondAccess:
        SecondSignalsByPosition.setdefault(Position, set()).add(Signal)
    if any(
        Second.X <= Position[0] < Second.X + SecondWidth
        and Second.Z <= Position[2] < Second.Z + SecondDepth
        and not (
            FirstSignalsByPosition[Position]
            & SecondSignalsByPosition.get(Position, set())
        )
        for Position in FirstSignalsByPosition
    ) or any(
        First.X <= Position[0] < First.X + FirstWidth
        and First.Z <= Position[2] < First.Z + FirstDepth
        and not (
            SecondSignalsByPosition[Position]
            & FirstSignalsByPosition.get(Position, set())
        )
        for Position in SecondSignalsByPosition
    ):
        return True

    for FirstPosition, FirstSignal in FirstAccess:
        for SecondPosition, SecondSignal in SecondAccess:
            if FirstSignal == SecondSignal:
                continue
            DeltaX = abs(FirstPosition[0] - SecondPosition[0])
            DeltaY = abs(FirstPosition[1] - SecondPosition[1])
            DeltaZ = abs(FirstPosition[2] - SecondPosition[2])
            HorizontalDistance = DeltaX + DeltaZ
            if (
                (DeltaY == 0 and HorizontalDistance <= 1)
                or (DeltaY == 1 and HorizontalDistance == 1)
            ):
                return True
    return False


def BuildDerivedPerimeterTerminalSlotDomain(
    TerminalGates: Iterable[Any],
    CoreGates: Iterable[PlacedGate],
    DesiredPinsByTerminal: Mapping[
        str,
        Iterable[tuple[int, int, int]],
    ],
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> DerivedPerimeterSlotDomain:
    """Materialize the finite outward-facing I/O slot domain once.

    This deliberately derives every coordinate from the committed NAND hull,
    terminal macro pin geometry, and the pins served by that terminal.  It
    contains no terminal-bank spacing, lateral-radius, or setback policy.  A
    later access-capacity solver may consume the complete domain, but this
    placement step only proves macro/electrical legality and freezes one
    deterministic compact member.
    """
    Core = tuple(CoreGates)
    Terminals = tuple(TerminalGates)
    if not Core:
        raise ValueError("derived perimeter terminals require a placed core")
    MinimumX = min(Gate.X for Gate in Core)
    MaximumX = max(
        Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0] - 1
        for Gate in Core
    )
    MinimumZ = min(Gate.Z for Gate in Core)
    MaximumZ = max(
        Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1] - 1
        for Gate in Core
    )
    CoreBounds = (MinimumX, MinimumZ, MaximumX, MaximumZ)
    TerminalDeckY = min(Gate.Y for Gate in Core)
    FaceDirections = {
        "north": (0, 0, -1),
        "south": (0, 0, 1),
        "west": (-1, 0, 0),
        "east": (1, 0, 0),
    }

    def TerminalKind(Gate: Any) -> str:
        Kind = getattr(Gate, "Kind", "")
        return str(getattr(Kind, "value", Kind))

    def TerminalSignal(Gate: Any) -> str:
        return str(
            Gate.Outputs[0]
            if TerminalKind(Gate) == "INPUT"
            else Gate.Inputs[0]
        )

    # First inspect every supported terminal transform.  Pin-plane distances
    # are derived from the actual macro shape, then shared per face so an
    # access fabric receives one coherent aperture plane rather than a
    # terminal-kind-specific shell offset.
    PrototypesByTerminal: dict[str, list[tuple[
        Any,
        str,
        int,
        bool,
        tuple[int, int, int],
        tuple[int, int, int],
        int,
        int,
    ]]] = {}
    FaceReach = {Face: 1 for Face in FaceDirections}
    for Gate in sorted(Terminals, key=lambda Value: Value.Name):
        TerminalPrototypes = []
        Seen = set()
        for Rotation in (0, 90, 180, 270):
            for MirrorX in (False, True):
                Prototype = BuildPlacedGate(
                    Gate,
                    0,
                    TerminalDeckY,
                    0,
                    Rotation,
                    MirrorX,
                )
                if TerminalKind(Gate) == "INPUT":
                    Pin = Prototype.OutputPin
                    Direction = Prototype.OutputDirection
                else:
                    Pin = Prototype.InputPins[0]
                    Direction = Prototype.InputDirections[0]
                if Pin is None or Direction not in FaceDirections.values():
                    continue
                Face = next(
                    Name
                    for Name, FaceDirection in FaceDirections.items()
                    if Direction == FaceDirection
                )
                Width, Depth = RotatedCellSize(
                    Prototype.Kind,
                    Prototype.Rotation,
                )
                Identity = (
                    Face,
                    Prototype.Rotation,
                    Prototype.MirrorX,
                    Pin,
                    Direction,
                    Width,
                    Depth,
                )
                if Identity in Seen:
                    continue
                Seen.add(Identity)
                TerminalPrototypes.append((
                    Gate,
                    Face,
                    Prototype.Rotation,
                    Prototype.MirrorX,
                    Pin,
                    Direction,
                    Width,
                    Depth,
                ))
                Reach = (
                    Depth - Pin[2]
                    if Face == "north"
                    else Pin[2] + 1
                    if Face == "south"
                    else Width - Pin[0]
                    if Face == "west"
                    else Pin[0] + 1
                )
                FaceReach[Face] = max(FaceReach[Face], Reach)
        PrototypesByTerminal[Gate.Name] = TerminalPrototypes

    FaceNormalCoordinate = {
        "north": MinimumZ - FaceReach["north"],
        "south": MaximumZ + FaceReach["south"],
        "west": MinimumX - FaceReach["west"],
        "east": MaximumX + FaceReach["east"],
    }

    def TangentialOrigins(
        Face: str,
        Pin: tuple[int, int, int],
        Width: int,
        Depth: int,
        Targets: tuple[tuple[int, int, int], ...],
    ) -> tuple[int, ...]:
        if Face in {"north", "south"}:
            Lower = MinimumX
            Upper = MaximumX - Width + 1
            Raw = {Target[0] - Pin[0] for Target in Targets}
        else:
            Lower = MinimumZ
            Upper = MaximumZ - Depth + 1
            Raw = {Target[2] - Pin[2] for Target in Targets}
        # If a terminal is wider than its core projection, its two exact
        # hull-boundary alignments remain the finite derived possibilities.
        if Lower > Upper:
            return tuple(sorted({Lower, Upper}))
        return tuple(sorted({
            Lower,
            Upper,
            *(min(Upper, max(Lower, Value)) for Value in Raw),
        }))

    TerminalSlots = []
    WorkCount = 0
    Complete = True
    IncompleteReason = ""
    for TerminalName, Prototypes in sorted(PrototypesByTerminal.items()):
        Targets = tuple(sorted(
            DesiredPinsByTerminal.get(TerminalName, ())
        ))
        Slots = []
        if not Targets:
            Complete = False
            IncompleteReason = (
                IncompleteReason
                or f"missing-served-pin:{TerminalName}"
            )
        for (
            Gate,
            Face,
            Rotation,
            MirrorX,
            LocalPin,
            Direction,
            Width,
            Depth,
        ) in Prototypes:
            for TangentialOrigin in TangentialOrigins(
                Face,
                LocalPin,
                Width,
                Depth,
                Targets,
            ) if Targets else ():
                WorkCount += 1
                if WorkCheck is not None:
                    WorkCheck({
                        "Phase": "derived-perimeter-slot-domain",
                        "TerminalName": TerminalName,
                        "Face": Face,
                        "WorkCount": WorkCount,
                    })
                OriginX = (
                    TangentialOrigin
                    if Face in {"north", "south"}
                    else FaceNormalCoordinate[Face] - LocalPin[0]
                )
                OriginZ = (
                    FaceNormalCoordinate[Face] - LocalPin[2]
                    if Face in {"north", "south"}
                    else TangentialOrigin
                )
                Candidate = BuildPlacedGate(
                    Gate,
                    OriginX,
                    TerminalDeckY,
                    OriginZ,
                    Rotation,
                    MirrorX,
                )
                ConnectionPin = (
                    Candidate.OutputPin
                    if TerminalKind(Gate) == "INPUT"
                    else Candidate.InputPins[0]
                )
                ConnectionDirection = (
                    Candidate.OutputDirection
                    if TerminalKind(Gate) == "INPUT"
                    else Candidate.InputDirections[0]
                )
                if ConnectionPin is None or ConnectionDirection != Direction:
                    raise ValueError("derived perimeter terminal transform drifted")
                if any(
                    PcbGatesConflict(Candidate, Existing)
                    for Existing in Core
                ):
                    continue
                MaximumSlotX = Candidate.X + Width - 1
                MaximumSlotZ = Candidate.Z + Depth - 1
                InteriorSpan = sum(
                    abs(ConnectionPin[0] - Target[0])
                    + abs(ConnectionPin[2] - Target[2])
                    for Target in Targets
                )
                SlotId = BuildStableFingerprint({
                    "TerminalName": TerminalName,
                    "Signal": TerminalSignal(Gate),
                    "Face": Face,
                    "Origin": (Candidate.X, Candidate.Y, Candidate.Z),
                    "Rotation": Candidate.Rotation,
                    "MirrorX": Candidate.MirrorX,
                    "ConnectionPin": ConnectionPin,
                    "ConnectionDirection": ConnectionDirection,
                })
                Slots.append(DerivedPerimeterTerminalSlot(
                    SlotId=SlotId,
                    TerminalName=TerminalName,
                    Signal=TerminalSignal(Gate),
                    Face=Face,
                    Origin=(Candidate.X, Candidate.Y, Candidate.Z),
                    Rotation=Candidate.Rotation,
                    MirrorX=Candidate.MirrorX,
                    MacroBounds=(
                        Candidate.X,
                        Candidate.Z,
                        MaximumSlotX,
                        MaximumSlotZ,
                    ),
                    ConnectionPin=ConnectionPin,
                    ConnectionDirection=ConnectionDirection,
                    InteriorSpan=InteriorSpan,
                ))
        Slots = sorted(
            Slots,
            key=lambda Slot: (
                Slot.Face,
                Slot.MacroBounds,
                Slot.Rotation,
                Slot.MirrorX,
                Slot.SlotId,
            ),
        )
        if not Slots:
            Complete = False
            IncompleteReason = (
                IncompleteReason
                or f"no-legal-outward-slot:{TerminalName}"
            )
        TerminalSlots.append((TerminalName, tuple(Slots)))

    TerminalGateByName = {Gate.Name: Gate for Gate in Terminals}
    AllSlots = tuple(
        Slot
        for _TerminalName, Slots in TerminalSlots
        for Slot in Slots
    )
    IncompatibleSlotPairs = tuple(sorted({
        tuple(sorted((First.SlotId, Second.SlotId)))
        for FirstIndex, First in enumerate(AllSlots)
        for Second in AllSlots[FirstIndex + 1:]
        if First.TerminalName != Second.TerminalName
        and (
            (
                First.Face == Second.Face
                and (
                    First.ConnectionPin[2]
                    if First.Face in {"north", "south"}
                    else First.ConnectionPin[0]
                ) != (
                    Second.ConnectionPin[2]
                    if Second.Face in {"north", "south"}
                    else Second.ConnectionPin[0]
                )
            )
            or PcbGatesConflict(
                BuildPlacedGate(
                    TerminalGateByName[First.TerminalName],
                    *First.Origin,
                    First.Rotation,
                    First.MirrorX,
                ),
                BuildPlacedGate(
                    TerminalGateByName[Second.TerminalName],
                    *Second.Origin,
                    Second.Rotation,
                    Second.MirrorX,
                ),
            )
        )
    }))
    return DerivedPerimeterSlotDomain(
        CoreBounds=CoreBounds,
        TerminalSlots=tuple(TerminalSlots),
        IncompatibleSlotPairs=IncompatibleSlotPairs,
        Complete=Complete,
        WorkCount=WorkCount,
        IncompleteReason=IncompleteReason,
    )


@dataclass(frozen=True)
class MandatoryAccessConflictProfile:
    """Immutable, routing-grade ownership and conflict summary for pin access."""

    OwnershipFingerprint: str
    ConflictFingerprint: str
    OwnershipRecords: tuple[
        tuple[str, tuple[RoutingResourceId, ...]], ...
    ]
    CrossConflicts: tuple[
        tuple[RoutingResourceId, tuple[str, ...]], ...
    ]
    SelfConflicts: tuple[
        tuple[RoutingResourceId, tuple[str, ...]], ...
    ]

    @property
    def SignalCount(self) -> int:
        return len(self.OwnershipRecords)

    @property
    def ClaimCount(self) -> int:
        return sum(
            len(Resources) for _Signal, Resources in self.OwnershipRecords
        )

    @property
    def CrossConflictCount(self) -> int:
        return len(self.CrossConflicts)

    @property
    def SelfConflictCount(self) -> int:
        return len(self.SelfConflicts)

    @property
    def ExactConflictCount(self) -> int:
        """Preserve the existing self-plus-cross mandatory conflict metric."""
        return self.CrossConflictCount + self.SelfConflictCount

    @property
    def ConflictResourceCount(self) -> int:
        return len({
            Resource
            for Resource, _Owners in (
                *self.CrossConflicts,
                *self.SelfConflicts,
            )
        })

    @property
    def ConflictSignals(self) -> tuple[str, ...]:
        return tuple(sorted({
            Signal
            for _Resource, Owners in (
                *self.CrossConflicts,
                *self.SelfConflicts,
            )
            for Signal in Owners
        }))

    @property
    def HasConflicts(self) -> bool:
        return self.ExactConflictCount > 0

    def ToDictionary(self) -> dict[str, object]:
        def ConflictRecords(
            Values: tuple[
                tuple[RoutingResourceId, tuple[str, ...]], ...
            ],
        ) -> list[dict[str, object]]:
            return [
                {
                    "Resource": str(Resource),
                    "Kind": Resource.Kind.value,
                    "Position": list(Resource.Position),
                    "Owners": list(Owners),
                }
                for Resource, Owners in Values
            ]

        return {
            "OwnershipFingerprint": self.OwnershipFingerprint,
            "ConflictFingerprint": self.ConflictFingerprint,
            "SignalCount": self.SignalCount,
            "ClaimCount": self.ClaimCount,
            "ClaimCountsBySignal": {
                Signal: len(Resources)
                for Signal, Resources in self.OwnershipRecords
            },
            "CrossConflictCount": self.CrossConflictCount,
            "SelfConflictCount": self.SelfConflictCount,
            "ExactConflictCount": self.ExactConflictCount,
            "ConflictResourceCount": self.ConflictResourceCount,
            "ConflictSignals": list(self.ConflictSignals),
            "HasConflicts": self.HasConflicts,
            "CrossConflicts": ConflictRecords(self.CrossConflicts),
            "SelfConflicts": ConflictRecords(self.SelfConflicts),
        }


def OrderExactStatesForMandatoryAccessCommit(
    States: Iterable[dict[str, object]],
    ProfilesBySearchCandidate: Mapping[
        int,
        MandatoryAccessConflictProfile,
    ],
) -> tuple[dict[str, object], ...]:
    """Promote the first exact zero-conflict state without losing identity.

    The joint beam's candidate index is a search-order identity.  A topology
    cut epoch additionally needs a commit order whose first state is known to
    have realizable mandatory pin access.  Preserve both identities so cached
    geometry and failure evidence remain attributable to the state that
    produced them.
    """
    CopiedStates = [deepcopy(State) for State in States]
    LegalStates = [
        State for State in CopiedStates
        if bool(State.get("ExactLegal"))
    ]
    RejectedStates = [
        State for State in CopiedStates
        if not bool(State.get("ExactLegal"))
    ]
    FirstZeroConflictState = next(
        (
            State
            for State in LegalStates
            if (
                int(State["CandidateIndex"])
                in ProfilesBySearchCandidate
                and not ProfilesBySearchCandidate[
                    int(State["CandidateIndex"])
                ].HasConflicts
            )
        ),
        None,
    )
    if FirstZeroConflictState is not None:
        LegalStates = [
            FirstZeroConflictState,
            *(
                State for State in LegalStates
                if State is not FirstZeroConflictState
            ),
        ]
    OrderedStates = [*LegalStates, *RejectedStates]
    for CommitCandidateIndex, State in enumerate(OrderedStates):
        SearchCandidateIndex = int(State["CandidateIndex"])
        State["SearchCandidateIndex"] = SearchCandidateIndex
        State["CandidateIndex"] = CommitCandidateIndex
        Profile = ProfilesBySearchCandidate.get(SearchCandidateIndex)
        State["ExactMandatoryAccessScreened"] = Profile is not None
        if Profile is not None:
            State["ExactMandatoryAccessConflictResources"] = (
                Profile.ConflictResourceCount
            )
            State["ExactMandatoryAccessConflictSignals"] = list(
                Profile.ConflictSignals
            )
            State["ExactMandatoryAccessOwnershipFingerprint"] = (
                Profile.OwnershipFingerprint
            )
            State["ExactMandatoryAccessConflictFingerprint"] = (
                Profile.ConflictFingerprint
            )
    return tuple(OrderedStates)


def SelectExactInterfaceCommitStates(
    States: Iterable[dict[str, object]],
    ProfilesBySearchCandidate: Mapping[
        int,
        MandatoryAccessConflictProfile,
    ],
    MaximumStates: int,
) -> tuple[
    tuple[dict[str, object], ...],
    tuple[dict[str, object], ...],
]:
    """Commit up to the bound using exact legality and interface diversity."""
    if MaximumStates <= 0:
        raise ValueError("exact interface commit bound must be positive")
    Selected: list[dict[str, object]] = []
    Attrition: list[dict[str, object]] = []
    SeenOwnership: set[str] = set()
    for State in States:
        SearchCandidateIndex = int(State["CandidateIndex"])
        Profile = ProfilesBySearchCandidate.get(SearchCandidateIndex)
        Pattern = State.get("ClusterInterfacePlacement", {})
        OwnershipFingerprint = (
            str(Pattern.get("OwnershipFingerprint", ""))
            if isinstance(Pattern, dict)
            else ""
        )
        if not bool(State.get("ExactLegal")):
            Attrition.append({
                "SearchCandidateIndex": SearchCandidateIndex,
                "Classification": (
                    "geometric-overlap-illegal-placement"
                ),
                "InterfaceOwnershipFingerprint": OwnershipFingerprint,
            })
            continue
        if Profile is None or Profile.HasConflicts:
            Attrition.append({
                "SearchCandidateIndex": SearchCandidateIndex,
                "Classification": "mandatory-access-unsat",
                "InterfaceOwnershipFingerprint": OwnershipFingerprint,
                "MandatoryAccessOwnershipFingerprint": (
                    Profile.OwnershipFingerprint
                    if Profile is not None
                    else ""
                ),
                "MandatoryAccessConflictFingerprint": (
                    Profile.ConflictFingerprint
                    if Profile is not None
                    else ""
                ),
            })
            continue
        if OwnershipFingerprint in SeenOwnership:
            Attrition.append({
                "SearchCandidateIndex": SearchCandidateIndex,
                "Classification": "duplicate-access-topology",
                "InterfaceOwnershipFingerprint": OwnershipFingerprint,
            })
            continue
        if len(Selected) >= MaximumStates:
            Attrition.append({
                "SearchCandidateIndex": SearchCandidateIndex,
                "Classification": "pruned-by-scoring-budget",
                "InterfaceOwnershipFingerprint": OwnershipFingerprint,
            })
            continue
        SeenOwnership.add(OwnershipFingerprint)
        Selected.append(deepcopy(State))
    for CommitCandidateIndex, State in enumerate(Selected):
        State["SearchCandidateIndex"] = int(State["CandidateIndex"])
        State["CandidateIndex"] = CommitCandidateIndex
    return tuple(Selected), tuple(Attrition)


def BuildMandatoryAccessClaims(
    PlacedGates: Iterable[Any],
    Signals: Iterable[str],
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, RoutingResourceClaims]:
    """Build the fixed pin-access claims that every detailed route must own."""
    Gates = (
        PlacedGates
        if isinstance(PlacedGates, (list, tuple))
        else tuple(PlacedGates)
    )
    RequiredSignals = frozenset(Signals)
    if WorkCheck is not None:
        WorkCheck({
            "Phase": "mandatory-access-claims-start",
            "GateCount": len(Gates),
            "SignalCount": len(RequiredSignals),
        })
    NodesBySignal: dict[str, set[tuple[int, int, int]]] = {
        Signal: set() for Signal in RequiredSignals
    }
    for GateIndex, Gate in enumerate(Gates, start=1):
        if WorkCheck is not None and GateIndex % 32 == 0:
            WorkCheck({
                "Phase": "mandatory-access-claims-gates",
                "ProcessedGates": GateIndex,
                "GateCount": len(Gates),
                "SignalCount": len(RequiredSignals),
            })
        if Gate.OutputPin is not None and Gate.OutputDirection is not None:
            for Signal in Gate.Outputs:
                if Signal not in RequiredSignals:
                    continue
                NodesBySignal[Signal].update(
                    (
                        Gate.OutputPin[0] + Gate.OutputDirection[0] * Offset,
                        Gate.OutputPin[1] + Gate.OutputDirection[1] * Offset,
                        Gate.OutputPin[2] + Gate.OutputDirection[2] * Offset,
                    )
                    for Offset in range(
                        DefaultRedstoneRoutingTechnology.AccessLength
                    )
                )
        for InputIndex, Signal in enumerate(Gate.Inputs):
            if Signal not in RequiredSignals:
                continue
            Pin, Direction = GetGateInputAccess(Gate, InputIndex)
            NodesBySignal[Signal].update(
                (
                    Pin[0] + Direction[0] * Offset,
                    Pin[1] + Direction[1] * Offset,
                    Pin[2] + Direction[2] * Offset,
                )
                for Offset in range(
                    DefaultRedstoneRoutingTechnology.AccessLength
                )
            )
    ClaimBuilder = RoutingResourceGraph(
        ActualBlocks=frozenset(),
        ElectricalBlocks=frozenset(),
        SolidBlocks=frozenset(),
    )
    Claims: dict[str, RoutingResourceClaims] = {}
    NonEmptySignals = tuple(
        (Signal, Nodes)
        for Signal, Nodes in sorted(NodesBySignal.items())
        if Nodes
    )
    for SignalIndex, (Signal, Nodes) in enumerate(
        NonEmptySignals,
        start=1,
    ):
        if WorkCheck is not None and (
            SignalIndex == 1 or SignalIndex % 16 == 0
        ):
            WorkCheck({
                "Phase": "mandatory-access-claims-signals",
                "Signal": Signal,
                "ProcessedSignals": SignalIndex - 1,
                "SignalCount": len(NonEmptySignals),
            })
        if WorkCheck is None:
            Claims[Signal] = ClaimBuilder.BuildRouteClaims(Nodes)
            continue

        def CheckRouteClaims(
            Diagnostics: dict[str, object],
            *,
            CurrentSignal: str = Signal,
        ) -> None:
            if WorkCheck is not None:
                WorkCheck({
                    "Phase": "mandatory-access-route-claims",
                    "Signal": CurrentSignal,
                    "ClaimPhase": Diagnostics.get("Phase"),
                    **{
                        Key: Value
                        for Key, Value in Diagnostics.items()
                        if Key != "Phase"
                    },
                })

        Claims[Signal] = ClaimBuilder.BuildRouteClaims(
            Nodes,
            WorkCheck=CheckRouteClaims,
        )
    if WorkCheck is not None:
        WorkCheck({
            "Phase": "mandatory-access-claims-complete",
            "GateCount": len(Gates),
            "SignalCount": len(Claims),
            "ClaimCount": sum(
                len(Claim.ResourceIds) for Claim in Claims.values()
            ),
        })
    return Claims


def MeasureMandatoryAccessConflictProfile(
    PlacedGates: Iterable[Any],
    Signals: Iterable[str],
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> MandatoryAccessConflictProfile:
    """Measure fixed access ownership with rename-independent fingerprints."""
    Claims = BuildMandatoryAccessClaims(
        PlacedGates,
        Signals,
        WorkCheck=WorkCheck,
    )
    OwnershipRecords = tuple(
        (
            Signal,
            tuple(sorted(Claim.ResourceIds, key=str)),
        )
        for Signal, Claim in sorted(Claims.items())
    )
    AllResources = tuple(
        Resource
        for _Signal, Resources in OwnershipRecords
        for Resource in Resources
    )
    MinimumX = min(
        (Resource.Position[0] for Resource in AllResources),
        default=0,
    )
    MinimumY = min(
        (Resource.Position[1] for Resource in AllResources),
        default=0,
    )
    MinimumZ = min(
        (Resource.Position[2] for Resource in AllResources),
        default=0,
    )

    def NormalizeResource(
        Resource: RoutingResourceId,
    ) -> tuple[str, int, int, int]:
        X, Y, Z = Resource.Position
        return (
            Resource.Kind.value,
            X - MinimumX,
            Y - MinimumY,
            Z - MinimumZ,
        )

    NormalizedOwnershipBySignal = {
        Signal: tuple(sorted(
            NormalizeResource(Resource) for Resource in Resources
        ))
        for Signal, Resources in OwnershipRecords
    }

    def StableFingerprint(Value: object) -> str:
        return sha256(repr(Value).encode("utf-8")).hexdigest()

    OwnershipFingerprint = StableFingerprint(tuple(sorted(
        NormalizedOwnershipBySignal.values()
    )))
    if WorkCheck is not None:
        WorkCheck({
            "Phase": "mandatory-access-conflicts-start",
            "SignalCount": len(Claims),
        })

    def CheckCrossConflicts(Diagnostics: dict[str, object]) -> None:
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "mandatory-access-cross-conflicts",
                "ConflictPhase": Diagnostics.get("Phase"),
                **{
                    Key: Value
                    for Key, Value in Diagnostics.items()
                    if Key != "Phase"
                },
            })

    CrossConflictMap = FindClaimConflictsByResourceIndex(
        Claims,
        WorkCheck=CheckCrossConflicts if WorkCheck is not None else None,
    )
    SelfConflictMap = FindSelfClaimConflicts(Claims)
    CrossConflicts = tuple(sorted(
        (
            (Resource, tuple(sorted(Owners)))
            for Resource, Owners in CrossConflictMap.items()
        ),
        key=lambda Value: str(Value[0]),
    ))
    SelfConflicts = tuple(sorted(
        (
            (Resource, tuple(sorted(Owners)))
            for Resource, Owners in SelfConflictMap.items()
        ),
        key=lambda Value: str(Value[0]),
    ))
    AnonymousConflictRecords = tuple(sorted(
        (
            ConflictKind,
            NormalizeResource(Resource),
            tuple(sorted(
                StableFingerprint(
                    NormalizedOwnershipBySignal.get(Owner, ())
                )
                for Owner in Owners
            )),
        )
        for ConflictKind, Conflicts in (
            ("Cross", CrossConflicts),
            ("Self", SelfConflicts),
        )
        for Resource, Owners in Conflicts
    ))
    Result = MandatoryAccessConflictProfile(
        OwnershipFingerprint=OwnershipFingerprint,
        ConflictFingerprint=StableFingerprint(AnonymousConflictRecords),
        OwnershipRecords=OwnershipRecords,
        CrossConflicts=CrossConflicts,
        SelfConflicts=SelfConflicts,
    )
    if WorkCheck is not None:
        WorkCheck({
            "Phase": "mandatory-access-conflicts-complete",
            "SignalCount": Result.SignalCount,
            "ClaimCount": Result.ClaimCount,
            "ExactConflictCount": Result.ExactConflictCount,
        })
    return Result


def CountMandatoryAccessSelfConflicts(
    PlacedGates: Iterable[Any],
    Signals: Iterable[str],
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> int:
    """Count exact support/headroom aliases within mandatory pin accesses."""
    return len(FindSelfClaimConflicts(BuildMandatoryAccessClaims(
        PlacedGates,
        Signals,
        WorkCheck=WorkCheck,
    )))


def CountMandatoryAccessConflicts(
    PlacedGates: Iterable[Any],
    Signals: Iterable[str],
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> int:
    """Count all fixed pin-access conflicts for the affected signal cut.

    A packed cluster can be individually legal for every signal while two
    different signals still claim the same electrical, support, or headroom
    resource.  Those conflicts are immutable to detailed routing and must be
    removed by the local placement repair before a candidate is published.
    """
    Claims = BuildMandatoryAccessClaims(
        PlacedGates,
        Signals,
        WorkCheck=WorkCheck,
    )

    def CheckConflicts(Diagnostics: dict[str, object]) -> None:
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "mandatory-access-cross-conflicts",
                "ConflictPhase": Diagnostics.get("Phase"),
                **{
                    Key: Value
                    for Key, Value in Diagnostics.items()
                    if Key != "Phase"
                },
            })

    return (
        len(FindSelfClaimConflicts(Claims))
        + len(FindClaimConflicts(
            Claims,
            WorkCheck=CheckConflicts if WorkCheck is not None else None,
        ))
    )


def CountPackedAccessEscapeConflicts(
    PlacedGates: Iterable[Any],
    RepairSignals: Iterable[str],
) -> int:
    """Count pin pairs too close to expose independent routing portals.

    Exact mandatory claims cover the fixed access cells themselves, but two
    otherwise legal pins separated by less than one routing track can still
    leave no pair of electrically independent first portals.  Restrict this
    stronger placement metric to pairs involving the current typed repair cut
    so ordinary compact clusters retain their established geometry.
    """
    Gates = tuple(PlacedGates)
    Required = frozenset(RepairSignals)
    PinOwners: list[tuple[tuple[int, int, int], str, str]] = []
    for Gate in Gates:
        if Gate.OutputPin is not None:
            PinOwners.extend(
                (Gate.OutputPin, Signal, Gate.Name)
                for Signal in Gate.Outputs
            )
        PinOwners.extend(
            (Pin, Signal, Gate.Name)
            for Pin, Signal in zip(Gate.InputPins, Gate.Inputs)
        )
    NearConflicts = sum(
        1
        for Index, (FirstPin, FirstSignal, FirstGate) in enumerate(PinOwners)
        for SecondPin, SecondSignal, SecondGate in PinOwners[Index + 1 :]
        if FirstGate != SecondGate
        and FirstSignal != SecondSignal
        and (
            FirstSignal in Required
            or SecondSignal in Required
        )
        and abs(FirstPin[1] - SecondPin[1]) <= 1
        and (
            abs(FirstPin[0] - SecondPin[0])
            + abs(FirstPin[2] - SecondPin[2])
            < DefaultRedstoneRoutingTechnology.TrackPitch
        )
    )
    return CountMandatoryAccessConflicts(Gates, Required) + NearConflicts


def FindMandatoryAccessConflictSignals(
    PlacedGates: Iterable[Any],
    Signals: Iterable[str],
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> dict[object, tuple[str, ...]]:
    """Return immutable cross-signal access conflicts before routing begins.

    This is deliberately the same resource-claim model used by detailed
    routing.  It lets placement advance directly to a relocation recipe when
    no router can resolve a fixed pin-access collision.
    """
    return {
        Resource: Owners
        for Resource, Owners in MeasureMandatoryAccessConflictProfile(
            PlacedGates,
            Signals,
            WorkCheck=WorkCheck,
        ).CrossConflicts
    }


def RepairPackedClusterAccess(
    Names: tuple[str, ...] | list[str],
    InternalByName: dict[str, Any],
    LocalPositions: dict[str, tuple[int, int]],
    LocalRotations: dict[str, int],
    LocalMirrors: dict[str, bool],
    RequiredSignals: frozenset[str],
    BeamWidth: int,
    IncludeNearPortalConflicts: bool = False,
    NormalizeOrigin: bool = True,
    RequireAccessDistinctGeometry: bool = False,
    AccessDistinctVariant: int = 0,
    PriorityTerminalPositions: frozenset[
        tuple[int, int, int]
    ] = frozenset(),
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
    ) -> tuple[dict[str, tuple[int, int]], dict[str, bool], dict[str, int]]:
    """Repair fixed access claims inside one cluster without global spreading."""
    ClusterSignals = frozenset(
        Signal
        for Name in Names
        for Signal in (
            *InternalByName[Name].Inputs,
            *InternalByName[Name].Outputs,
        )
        if Signal in RequiredSignals
    )
    if not ClusterSignals:
        return LocalPositions, LocalMirrors, {}

    def BuildGates(
        State: tuple[tuple[str, int, int, bool], ...],
    ) -> list[Any]:
        Values = {
            Name: (X, Z, MirrorX)
            for Name, X, Z, MirrorX in State
        }
        return [
            BuildPlacedGate(
                InternalByName[Name],
                Values[Name][0],
                1,
                Values[Name][1],
                LocalRotations[Name],
                Values[Name][2],
            )
            for Name in Names
        ]

    BaselineState = tuple(
        (
            Name,
            LocalPositions[Name][0],
            LocalPositions[Name][1],
            LocalMirrors.get(Name, False),
        )
        for Name in Names
    )
    BaselineGates = BuildGates(BaselineState)
    BaselineMandatoryConflicts = CountMandatoryAccessConflicts(
        BaselineGates, ClusterSignals
    )
    BaselineConflicts = (
        CountPackedAccessEscapeConflicts(BaselineGates, ClusterSignals)
        if IncludeNearPortalConflicts
        else BaselineMandatoryConflicts
    )
    if BaselineConflicts == 0 and not RequireAccessDistinctGeometry:
        return LocalPositions, LocalMirrors, {}

    BaselineMinimumX = min(Gate.X for Gate in BaselineGates)
    BaselineMaximumX = max(
        Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0]
        for Gate in BaselineGates
    )
    BaselineWidth = BaselineMaximumX - BaselineMinimumX
    MaximumWidth = max(BaselineWidth, 2 * BaselineWidth)
    SearchMinimumX = BaselineMinimumX - BaselineWidth
    SearchMaximumX = BaselineMaximumX + BaselineWidth
    BaselineMinimumZ = min(Gate.Z for Gate in BaselineGates)
    BaselineMaximumZ = max(
        Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1]
        for Gate in BaselineGates
    )
    BaselineDepth = BaselineMaximumZ - BaselineMinimumZ
    MaximumDepth = max(BaselineDepth, 2 * BaselineDepth)
    SearchMinimumZ = BaselineMinimumZ - BaselineDepth
    SearchMaximumZ = BaselineMaximumZ + BaselineDepth
    BaselinePosition = {
        Name: LocalPositions[Name] for Name in Names
    }
    EndpointNames = {
        Name
        for Name in Names
        if set((
            *InternalByName[Name].Inputs,
            *InternalByName[Name].Outputs,
        )) & ClusterSignals
    }
    PriorityEndpointNames = frozenset(
        Gate.Name
        for Gate in BaselineGates
        if (
            (
                Gate.OutputPin is not None
                and Gate.OutputPin in PriorityTerminalPositions
                and bool(set(Gate.Outputs) & ClusterSignals)
            )
            or any(
                Pin in PriorityTerminalPositions
                and Signal in ClusterSignals
                for Signal, Pin in zip(
                    Gate.Inputs,
                    Gate.InputPins,
                )
            )
        )
    )
    SearchOrder = tuple(sorted(
        EndpointNames,
        key=lambda Name: (
            Name not in PriorityEndpointNames,
            LocalPositions[Name][1],
            Name,
        ),
    ))

    def Score(
        State: tuple[tuple[str, int, int, bool], ...],
        Gates: list[Any] | None = None,
    ) -> tuple[object, ...]:
        Gates = BuildGates(State) if Gates is None else Gates
        MinimumX = min(Gate.X for Gate in Gates)
        MaximumX = max(
            Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0]
            for Gate in Gates
        )
        MinimumZ = min(Gate.Z for Gate in Gates)
        MaximumZ = max(
            Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1]
            for Gate in Gates
        )
        Width = MaximumX - MinimumX
        Depth = MaximumZ - MinimumZ
        Displacement = sum(
            abs(Gate.X - BaselinePosition[Gate.Name][0])
            + abs(Gate.Z - BaselinePosition[Gate.Name][1])
            for Gate in Gates
        )
        ConflictCount = (
            CountPackedAccessEscapeConflicts(Gates, ClusterSignals)
            if IncludeNearPortalConflicts
            else CountMandatoryAccessConflicts(Gates, ClusterSignals)
        )
        StateByName = {
            Name: (X, Z, MirrorX)
            for Name, X, Z, MirrorX in State
        }
        PriorityEndpointUnchangedCount = sum(
            StateByName[Name]
            == (
                BaselinePosition[Name][0],
                BaselinePosition[Name][1],
                LocalMirrors.get(Name, False),
            )
            for Name in PriorityEndpointNames
        )
        GeometryChangePenalty = (
            PriorityEndpointUnchangedCount
            if PriorityEndpointNames
            else int(
                RequireAccessDistinctGeometry
                and State == BaselineState
            )
        )
        return (
            (
                ConflictCount,
                GeometryChangePenalty,
                Width * Depth,
                max(Width, Depth),
                Displacement,
                State,
            )
            if IncludeNearPortalConflicts
            else (
                ConflictCount,
                GeometryChangePenalty,
                Width,
                Displacement,
                State,
            )
        )

    Beam: list[tuple[tuple[object, ...], tuple[tuple[str, int, int, bool], ...]]] = [
        (Score(BaselineState, BaselineGates), BaselineState)
    ]
    CandidateEvaluationCount = 0
    for PassIndex in range(2):
        for GateIndex, Name in enumerate(SearchOrder):
            if WorkCheck is not None:
                WorkCheck({
                    "Phase": "packed-access-repair",
                    "Pass": PassIndex,
                    "GateIndex": GateIndex,
                    "GateCount": len(SearchOrder),
                    "BaselineConflictCount": BaselineConflicts,
                })
            Candidates: dict[
                tuple[tuple[str, int, int, bool], ...],
                tuple[object, ...],
            ] = {}
            for _PreviousScore, PreviousState in Beam:
                Previous = {
                    GateName: (X, Z, MirrorX)
                    for GateName, X, Z, MirrorX in PreviousState
                }
                GateWidth = RotatedCellSize(
                    InternalByName[Name].Kind.value,
                    LocalRotations[Name],
                )[0]
                GateDepth = RotatedCellSize(
                    InternalByName[Name].Kind.value,
                    LocalRotations[Name],
                )[1]
                CandidateXValues = sorted(
                    range(
                        SearchMinimumX,
                        SearchMaximumX - GateWidth + 1,
                    ),
                    key=lambda CandidateX: (
                        abs(
                            CandidateX
                            - BaselinePosition[Name][0]
                        ),
                        CandidateX,
                    ),
                )[:33]
                CandidatePositions = [
                    (CandidateX, BaselinePosition[Name][1])
                    for CandidateX in CandidateXValues
                ]
                if IncludeNearPortalConflicts:
                    CandidateZValues = sorted(
                        range(
                            SearchMinimumZ,
                            SearchMaximumZ - GateDepth + 1,
                        ),
                        key=lambda CandidateZ: (
                            abs(
                                CandidateZ
                                - BaselinePosition[Name][1]
                            ),
                            CandidateZ,
                        ),
                    )[:33]
                    CandidatePositions.extend(
                        (BaselinePosition[Name][0], CandidateZ)
                        for CandidateZ in CandidateZValues
                    )
                    CandidatePositions.extend(
                        (
                            BaselinePosition[Name][0] + DeltaX,
                            BaselinePosition[Name][1] + DeltaZ,
                        )
                        for DeltaX in range(-6, 7)
                        for DeltaZ in range(-6, 7)
                        if abs(DeltaX) + abs(DeltaZ) <= 6
                    )
                for CandidateX, CandidateZ in dict.fromkeys(
                    CandidatePositions
                ):
                    for CandidateMirror in (False, True):
                        Candidate = dict(Previous)
                        Candidate[Name] = (
                            CandidateX,
                            CandidateZ,
                            CandidateMirror,
                        )
                        State = tuple(
                            (
                                GateName,
                                Candidate[GateName][0],
                                Candidate[GateName][1],
                                Candidate[GateName][2],
                            )
                            for GateName in Names
                        )
                        if State in Candidates:
                            continue
                        CandidateEvaluationCount += 1
                        if (
                            WorkCheck is not None
                            and CandidateEvaluationCount % 128 == 0
                        ):
                            WorkCheck({
                                "Phase": "packed-access-repair-candidate",
                                "Pass": PassIndex,
                                "GateIndex": GateIndex,
                                "GateCount": len(SearchOrder),
                                "EvaluatedCandidates": (
                                    CandidateEvaluationCount
                                ),
                                "CurrentFrontier": len(Candidates),
                            })
                        Gates = BuildGates(State)
                        if any(
                            PcbGatesConflict(First, Second)
                            for FirstIndex, First in enumerate(Gates)
                            for Second in Gates[FirstIndex + 1 :]
                        ):
                            continue
                        MinimumX = min(Gate.X for Gate in Gates)
                        MaximumX = max(
                            Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0]
                            for Gate in Gates
                        )
                        MinimumZ = min(Gate.Z for Gate in Gates)
                        MaximumZ = max(
                            Gate.Z
                            + RotatedCellSize(Gate.Kind, Gate.Rotation)[1]
                            for Gate in Gates
                        )
                        if (
                            MaximumX - MinimumX > MaximumWidth
                            or MaximumZ - MinimumZ > MaximumDepth
                        ):
                            continue
                        Candidates[State] = Score(State, Gates)
            Beam = [
                (CandidateScore, State)
                for State, CandidateScore in sorted(
                    Candidates.items(), key=lambda Value: Value[1]
                )[:BeamWidth]
            ]
            if not Beam:
                break
            if (
                Beam[0][0][0] == 0
                and Beam[0][0][1] == 0
                and (
                    not RequireAccessDistinctGeometry
                    or Beam[0][1] != BaselineState
                )
            ):
                break
        if (
            Beam
            and Beam[0][0][0] == 0
            and Beam[0][0][1] == 0
            and (
                not RequireAccessDistinctGeometry
                or Beam[0][1] != BaselineState
            )
        ):
            break
    ExactLegalChangedBeam = [
        (CandidateScore, State)
        for CandidateScore, State in Beam
        if CandidateScore[0] == 0
        and CandidateScore[1] == 0
        and (
            not RequireAccessDistinctGeometry
            or State != BaselineState
        )
    ]
    SelectedBeam = (
        ExactLegalChangedBeam[
            AccessDistinctVariant % len(ExactLegalChangedBeam)
        ]
        if ExactLegalChangedBeam
        else (Beam[0] if Beam else None)
    )
    BestMandatoryConflicts = (
        CountMandatoryAccessConflicts(
            BuildGates(SelectedBeam[1]),
            ClusterSignals,
        )
        if SelectedBeam is not None
        else BaselineMandatoryConflicts
    )
    if SelectedBeam is None or BestMandatoryConflicts != 0:
        BestConflictCount = (
            SelectedBeam[0][0]
            if SelectedBeam is not None
            else BaselineConflicts
        )
        raise ValueError(
            "Could not legalize mandatory packed access claims "
            f"within width envelope: signals={','.join(sorted(ClusterSignals))}:"
            f"baseline={BaselineConflicts}:best={BestConflictCount}:"
            f"maximum_width={MaximumWidth}"
        )
    BestScore, BestState = SelectedBeam
    Best = {
        Name: (X, Z, MirrorX)
        for Name, X, Z, MirrorX in BestState
    }
    MinimumX = (
        min(X for X, _Z, _MirrorX in Best.values())
        if NormalizeOrigin
        else 0
    )
    MinimumZ = (
        min(Z for _X, Z, _MirrorX in Best.values())
        if NormalizeOrigin
        else 0
    )
    RepairedPositions = dict(LocalPositions)
    RepairedMirrors = dict(LocalMirrors)
    for Name in Names:
        RepairedPositions[Name] = (
            Best[Name][0] - MinimumX,
            Best[Name][1] - MinimumZ,
        )
        RepairedMirrors[Name] = Best[Name][2]
    BestGates = BuildGates(BestState)
    FinalWidth = (
        max(
            Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0]
            for Gate in BestGates
        )
        - min(Gate.X for Gate in BestGates)
    )
    FinalDepth = (
        max(
            Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1]
            for Gate in BestGates
        )
        - min(Gate.Z for Gate in BestGates)
    )
    return RepairedPositions, RepairedMirrors, {
        "BaselineConflictCount": BaselineConflicts,
        "FinalConflictCount": BestScore[0],
        "BaselineMandatoryConflictCount": BaselineMandatoryConflicts,
        "FinalMandatoryConflictCount": BestMandatoryConflicts,
        "BaselineWidth": BaselineWidth,
        "FinalWidth": FinalWidth,
        "MaximumWidth": MaximumWidth,
        "BaselineDepth": BaselineDepth,
        "FinalDepth": FinalDepth,
        "MaximumDepth": MaximumDepth,
        "NormalizedOrigin": NormalizeOrigin,
        "AccessDistinctVariant": AccessDistinctVariant,
        "AccessDistinctVariantCount": len(ExactLegalChangedBeam),
        "PriorityEndpointNames": sorted(PriorityEndpointNames),
        "PriorityTerminalPositions": [
            list(Position)
            for Position in sorted(PriorityTerminalPositions)
        ],
    }


@dataclass(frozen=True)
class TransactionalClusterEndpointRepairResult:
    """One committed cluster-local ECO or a diagnostic rejection."""

    Placement: PcbPlacement | None
    Diagnostics: dict[str, object]

    @property
    def Accepted(self) -> bool:
        return self.Placement is not None


def RankTransactionalRepairClusterSelections(
    EligibleClusterSignals: Iterable[
        tuple[int, tuple[str, ...], frozenset[str]]
    ],
    RepairClusterCount: int,
) -> tuple[tuple[int, ...], ...]:
    """Rank bounded cluster combinations by reported-cut coverage."""
    Eligible = tuple(EligibleClusterSignals)
    if not Eligible:
        return ()
    EffectiveCount = min(
        max(1, RepairClusterCount),
        len(Eligible),
    )

    def SelectionKey(
        Selection: tuple[int, ...],
    ) -> tuple[object, ...]:
        SignalSets = tuple(
            Eligible[Ordinal][2]
            for Ordinal in Selection
        )
        CoveredSignals = frozenset(
            Signal
            for Signals in SignalSets
            for Signal in Signals
        )
        return (
            -len(CoveredSignals),
            -sum(len(Signals) for Signals in SignalSets),
            -min(len(Signals) for Signals in SignalSets),
            tuple(Eligible[Ordinal][0] for Ordinal in Selection),
        )

    return tuple(sorted(
        combinations(range(len(Eligible)), EffectiveCount),
        key=SelectionKey,
    ))


def SelectTransactionalRepairClusterSelections(
    EligibleClusterSignals: Iterable[
        tuple[int, tuple[str, ...], frozenset[str]]
    ],
    RepairClusterCount: int,
    RepairSignals: frozenset[str],
) -> tuple[tuple[int, ...], ...]:
    """Keep complete-cut cluster selections when the bound admits them."""
    Eligible = tuple(EligibleClusterSignals)
    # A small exact capacity cut may span three owners even when the normal
    # coordinated repair starts at two.  Escalate only when every two-owner
    # combination omits a reported endpoint; this remains a structural,
    # bounded ownership decision rather than a benchmark rule.
    MaximumClusterCount = min(3, len(Eligible))
    Ranked: tuple[tuple[int, ...], ...] = ()
    Complete: tuple[tuple[int, ...], ...] = ()
    for CandidateCount in range(
        min(max(1, RepairClusterCount), MaximumClusterCount),
        MaximumClusterCount + 1,
    ):
        Ranked = RankTransactionalRepairClusterSelections(
            Eligible,
            CandidateCount,
        )
        Complete = tuple(
            Selection
            for Selection in Ranked
            if RepairSignals <= frozenset(
                Signal
                for Ordinal in Selection
                for Signal in Eligible[Ordinal][2]
            )
        )
        if Complete:
            return Complete
    # Some cuts include top-level terminals with no packed-cluster owner. In
    # that case retain the ordinary maximum-coverage ranking; otherwise a
    # state that omits a reported cut signal cannot be a coordinated repair
    # and only consumes one of the fixed geometry variants.
    return Ranked


def BuildTransactionalClusterEndpointRepair(
    Source: PcbPlacement,
    RepairSignals: frozenset[str],
    BeamWidth: int = 16,
    RepairVariant: int = 0,
    RepairClusterCount: int = 1,
    RepairTerminalPositions: frozenset[
        tuple[int, int, int]
    ] = frozenset(),
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> TransactionalClusterEndpointRepairResult:
    """Repair endpoint access without reopening clustering or global slots.

    This is a physical-design ECO transaction.  It may translate or mirror
    only NAND gates touching the reported signals inside their current
    clusters.  Every unrelated gate, the global XZ envelope, and unaffected
    local routes remain immutable.  Claims incident to a moved gate are
    deliberately released so authoritative routing regenerates them against
    the new pin geometry.
    """
    Signals = frozenset(map(str, RepairSignals))
    Diagnostics: dict[str, object] = {
        "Enabled": True,
        "Signals": sorted(Signals),
        "Accepted": False,
        "RepairVariant": RepairVariant,
    }
    if not Signals or not Source.Clusters:
        Diagnostics["Reason"] = "missing-signals-or-clusters"
        return TransactionalClusterEndpointRepairResult(None, Diagnostics)

    Module = Source.Placed.Module
    ModuleGateByName = {
        Gate.Name: Gate
        for Gate in Module.Gates
    }
    SourceGateByName = {
        Gate.Name: Gate
        for Gate in Source.Placed.PlacedGates
    }
    InternalByName = {
        Name: ModuleGateByName[Name]
        for Names in Source.Clusters
        for Name in Names
        if (
            Name in ModuleGateByName
            and str(getattr(
                ModuleGateByName[Name].Kind,
                "value",
                ModuleGateByName[Name].Kind,
            )) == "NAND"
        )
    }
    if not InternalByName:
        Diagnostics["Reason"] = "no-packed-nand-clusters"
        return TransactionalClusterEndpointRepairResult(None, Diagnostics)

    def GateGeometry(Gate: PlacedGate) -> tuple[object, ...]:
        return (
            Gate.X,
            Gate.Y,
            Gate.Z,
            Gate.Rotation,
            bool(Gate.MirrorX),
        )

    def GateEnvelope(
        Gates: Iterable[PlacedGate],
    ) -> tuple[int, int, int, int]:
        Values = tuple(Gates)
        return (
            min(Gate.X for Gate in Values),
            min(Gate.Z for Gate in Values),
            max(
                Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0]
                for Gate in Values
            ),
            max(
                Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1]
                for Gate in Values
            ),
        )

    SourceEnvelope = GateEnvelope(Source.Placed.PlacedGates)
    EligibleClusterSignals = tuple(
        (
            ClusterIndex,
            tuple(
                Name
                for Name in Names
                if Name in InternalByName and Name in SourceGateByName
            ),
            frozenset(
                Signal
                for Name in Names
                if Name in InternalByName
                for Signal in (
                    *InternalByName[Name].Inputs,
                    *InternalByName[Name].Outputs,
                )
                if Signal in Signals
            ),
        )
        for ClusterIndex, Names in enumerate(Source.Clusters)
        if any(
            Signal in Signals
            for Name in Names
            if Name in InternalByName
            for Signal in (
                *InternalByName[Name].Inputs,
                *InternalByName[Name].Outputs,
            )
        )
    )
    if not EligibleClusterSignals:
        Diagnostics["Reason"] = "repair-signals-have-no-cluster-endpoints"
        return TransactionalClusterEndpointRepairResult(None, Diagnostics)
    EffectiveRepairClusterCount = min(
        max(1, RepairClusterCount),
        len(EligibleClusterSignals),
    )
    ClusterSelections = SelectTransactionalRepairClusterSelections(
        EligibleClusterSignals,
        EffectiveRepairClusterCount,
        Signals,
    )
    EffectiveRepairClusterCount = len(ClusterSelections[0])
    PriorityTerminalOwnerClusters = frozenset(
        ClusterIndex
        for ClusterIndex, Names in enumerate(Source.Clusters)
        if any(
            (
                Gate.OutputPin in RepairTerminalPositions
                and bool(set(Gate.Outputs) & Signals)
            )
            or any(
                Pin in RepairTerminalPositions
                and Signal in Signals
                for Signal, Pin in zip(
                    Gate.Inputs,
                    Gate.InputPins,
                )
            )
            for Name in Names
            for Gate in (SourceGateByName.get(Name),)
            if Gate is not None
        )
    )
    PriorityOwnerSelections = tuple(
        Selection
        for Selection in ClusterSelections
        if PriorityTerminalOwnerClusters <= frozenset(
            EligibleClusterSignals[Ordinal][0]
            for Ordinal in Selection
        )
    )
    if PriorityOwnerSelections:
        ClusterSelections = PriorityOwnerSelections
    SelectedClusterOrdinals = ClusterSelections[
        RepairVariant % len(ClusterSelections)
    ]
    SelectedClusterIndices = frozenset(
        EligibleClusterSignals[Ordinal][0]
        for Ordinal in SelectedClusterOrdinals
    )
    ClusterRepairVariant = RepairVariant // len(ClusterSelections)
    Diagnostics.update({
        "EligibleClusterCount": len(EligibleClusterSignals),
        "RequestedRepairClusterCount": RepairClusterCount,
        "RepairClusterCount": EffectiveRepairClusterCount,
        "SelectedClusterIndices": sorted(SelectedClusterIndices),
        "SelectedClusterIndex": (
            next(iter(SelectedClusterIndices))
            if len(SelectedClusterIndices) == 1
            else None
        ),
        "ClusterRepairVariant": ClusterRepairVariant,
        "SelectedCutSignalCoverage": len(frozenset(
            Signal
            for Ordinal in SelectedClusterOrdinals
            for Signal in EligibleClusterSignals[Ordinal][2]
        )),
        "PriorityTerminalOwnerClusters": sorted(
            PriorityTerminalOwnerClusters
        ),
        "PriorityTerminalOwnerCoverageApplied": bool(
            PriorityOwnerSelections
        ),
    })

    RepairedGateByName = dict(SourceGateByName)
    RepairedRotationByName = {
        Name: Gate.Rotation
        for Name, Gate in SourceGateByName.items()
    }
    RepairByCluster: dict[str, dict[str, object]] = {}
    TouchedClusters: set[int] = set()
    for ClusterIndex, ClusterNames, ClusterSignals in (
        EligibleClusterSignals
    ):
        if ClusterIndex not in SelectedClusterIndices:
            continue
        TouchedClusters.add(ClusterIndex)
        LocalPositions = {
            Name: (
                SourceGateByName[Name].X,
                SourceGateByName[Name].Z,
            )
            for Name in ClusterNames
        }
        LocalRotations = {
            Name: SourceGateByName[Name].Rotation
            for Name in ClusterNames
        }
        LocalMirrors = {
            Name: bool(SourceGateByName[Name].MirrorX)
            for Name in ClusterNames
        }
        try:
            (
                RepairedPositions,
                RepairedMirrors,
                ClusterDiagnostics,
            ) = RepairPackedClusterAccess(
                ClusterNames,
                InternalByName,
                LocalPositions,
                LocalRotations,
                LocalMirrors,
                ClusterSignals,
                BeamWidth,
                IncludeNearPortalConflicts=True,
                NormalizeOrigin=False,
                RequireAccessDistinctGeometry=True,
                AccessDistinctVariant=ClusterRepairVariant,
                PriorityTerminalPositions=RepairTerminalPositions,
                WorkCheck=WorkCheck,
            )
        except ValueError as Error:
            Diagnostics.update({
                "Reason": "cluster-local-search-rejected",
                "RejectedCluster": ClusterIndex,
                "Validation": str(Error),
            })
            return TransactionalClusterEndpointRepairResult(
                None,
                Diagnostics,
            )
        PriorityEndpointNames = tuple(sorted(
            Name
            for Name in ClusterNames
            for Gate in (SourceGateByName[Name],)
            if (
                (
                    Gate.OutputPin in RepairTerminalPositions
                    and bool(set(Gate.Outputs) & Signals)
                )
                or any(
                    Pin in RepairTerminalPositions
                    and Signal in Signals
                    for Signal, Pin in zip(
                        Gate.Inputs,
                        Gate.InputPins,
                    )
                )
            )
        ))
        if PriorityEndpointNames:
            # Translation and mirroring preserve a macro's relative pin-bank
            # geometry. A witnessed same-macro access collision needs one
            # bounded rigid orientation alternative instead.
            RotationDelta = (90, 180, 270)[
                ClusterRepairVariant % 3
            ]
            CandidateRotations = {
                Name: (
                    (LocalRotations[Name] + RotationDelta) % 360
                    if Name in PriorityEndpointNames
                    else LocalRotations[Name]
                )
                for Name in ClusterNames
            }
            CandidateClusterGates = [
                BuildPlacedGate(
                    InternalByName[Name],
                    RepairedPositions[Name][0],
                    SourceGateByName[Name].Y,
                    RepairedPositions[Name][1],
                    CandidateRotations[Name],
                    RepairedMirrors[Name],
                )
                for Name in ClusterNames
            ]
            if (
                not any(
                    PcbGatesConflict(First, Second)
                    for GateIndex, First in enumerate(CandidateClusterGates)
                    for Second in CandidateClusterGates[GateIndex + 1 :]
                )
                and CountMandatoryAccessConflicts(
                    CandidateClusterGates,
                    ClusterSignals,
                ) == 0
            ):
                for Name in PriorityEndpointNames:
                    RepairedRotationByName[Name] = (
                        CandidateRotations[Name]
                    )
                ClusterDiagnostics["PriorityEndpointRotationDelta"] = (
                    RotationDelta
                )
                ClusterDiagnostics["PriorityEndpointRotationNames"] = (
                    list(PriorityEndpointNames)
                )
            else:
                ClusterDiagnostics["PriorityEndpointRotationRejected"] = (
                    True
                )
        RepairByCluster[str(ClusterIndex)] = {
            **ClusterDiagnostics,
            "Signals": sorted(ClusterSignals),
            "PortfolioRepairVariant": RepairVariant,
            "ClusterRepairVariant": ClusterRepairVariant,
        }
        for Name in ClusterNames:
            SourceGate = SourceGateByName[Name]
            RepairedGateByName[Name] = BuildPlacedGate(
                InternalByName[Name],
                RepairedPositions[Name][0],
                SourceGate.Y,
                RepairedPositions[Name][1],
                RepairedRotationByName[Name],
                RepairedMirrors[Name],
            )

    CandidateGates = [
        RepairedGateByName.get(Gate.Name, Gate)
        for Gate in Source.Placed.PlacedGates
    ]
    ChangedGateNames = frozenset(
        Name
        for Name, SourceGate in SourceGateByName.items()
        if (
            Name in RepairedGateByName
            and GateGeometry(RepairedGateByName[Name])
            != GateGeometry(SourceGate)
        )
    )
    if not ChangedGateNames:
        Diagnostics.update({
            "Reason": "no-endpoint-geometry-change",
            "Clusters": RepairByCluster,
        })
        return TransactionalClusterEndpointRepairResult(None, Diagnostics)

    AllowedGateNames = frozenset(
        Name
        for Name in InternalByName
        if set((
            *InternalByName[Name].Inputs,
            *InternalByName[Name].Outputs,
        )) & Signals
    )
    UnexpectedChanges = ChangedGateNames - AllowedGateNames
    if UnexpectedChanges:
        Diagnostics.update({
            "Reason": "unrelated-gate-geometry-changed",
            "UnexpectedChangedGateCount": len(UnexpectedChanges),
        })
        return TransactionalClusterEndpointRepairResult(None, Diagnostics)

    CandidateEnvelope = GateEnvelope(CandidateGates)
    if (
        CandidateEnvelope[0] < SourceEnvelope[0]
        or CandidateEnvelope[1] < SourceEnvelope[1]
        or CandidateEnvelope[2] > SourceEnvelope[2]
        or CandidateEnvelope[3] > SourceEnvelope[3]
    ):
        Diagnostics.update({
            "Reason": "global-envelope-growth",
            "SourceEnvelope": list(SourceEnvelope),
            "CandidateEnvelope": list(CandidateEnvelope),
        })
        return TransactionalClusterEndpointRepairResult(None, Diagnostics)

    CandidatePlacedForValidation = PlacedDesign(
        Module=Module,
        PlacedGates=CandidateGates,
    )
    try:
        if any(
            PcbGatesConflict(First, Second)
            for GateIndex, First in enumerate(CandidateGates)
            for Second in CandidateGates[GateIndex + 1 :]
        ):
            raise ValueError("repaired gate geometry overlaps")
        BuildPlacedCellGeometry(CandidatePlacedForValidation)
    except ValueError as Error:
        Diagnostics.update({
            "Reason": "exact-electrical-validation-rejected",
            "Validation": str(Error),
        })
        return TransactionalClusterEndpointRepairResult(None, Diagnostics)

    SourceProfile = (
        Source.MandatoryAccessPreScreenProfile
        or MeasureMandatoryAccessConflictProfile(
            Source.Placed.PlacedGates,
            Source.SignalOrder,
            WorkCheck=WorkCheck,
        )
    )
    CandidateProfile = MeasureMandatoryAccessConflictProfile(
        CandidateGates,
        Source.SignalOrder,
        WorkCheck=WorkCheck,
    )
    CandidateConflictCount = (
        len(CandidateProfile.CrossConflicts)
        + len(CandidateProfile.SelfConflicts)
    )
    if CandidateConflictCount:
        Diagnostics.update({
            "Reason": "mandatory-access-conflict",
            "MandatoryAccessConflictResourceCount": CandidateConflictCount,
        })
        return TransactionalClusterEndpointRepairResult(None, Diagnostics)
    if (
        CandidateProfile.OwnershipFingerprint
        == SourceProfile.OwnershipFingerprint
    ):
        Diagnostics.update({
            "Reason": "unchanged-mandatory-access-ownership",
            "MandatoryAccessOwnershipFingerprint": (
                CandidateProfile.OwnershipFingerprint
            ),
        })
        return TransactionalClusterEndpointRepairResult(None, Diagnostics)

    InvalidatedSignals = frozenset(
        Signal
        for Name in ChangedGateNames
        for Signal in (
            *ModuleGateByName[Name].Inputs,
            *ModuleGateByName[Name].Outputs,
        )
    )

    def RetainUnchangedSignalEntries(
        Values: Mapping[str, Any] | None,
    ) -> dict[str, Any] | None:
        if Values is None:
            return None
        return {
            Signal: Value
            for Signal, Value in Values.items()
            if Signal not in InvalidatedSignals
        }

    ClusterByGate = {
        Name: ClusterIndex
        for ClusterIndex, Names in enumerate(Source.Clusters)
        for Name in Names
    }
    CandidateGateByName = {
        Gate.Name: Gate for Gate in CandidateGates
    }
    ProducerBySignal = {
        Signal: Gate
        for Gate in Module.Gates
        for Signal in Gate.Outputs
    }
    ConsumersBySignal: dict[str, list[Any]] = {}
    for Gate in Module.Gates:
        for Signal in Gate.Inputs:
            ConsumersBySignal.setdefault(Signal, []).append(Gate)

    def RefreshLease(
        Request: ClusterBoundaryLeaseRequest,
    ) -> ClusterBoundaryLeaseRequest:
        Producer = ProducerBySignal.get(Request.Signal)
        ProducerPlaced = (
            CandidateGateByName.get(Producer.Name)
            if Producer is not None
            else None
        )
        SourceTerminal = (
            ProducerPlaced.OutputPin
            if ProducerPlaced is not None
            else Request.SourceTerminal
        )
        TargetTerminals = tuple(sorted({
            TargetPlaced.InputPins[InputIndex]
            for Consumer in ConsumersBySignal.get(Request.Signal, ())
            if (
                (
                    Request.TargetCluster < 0
                    and Consumer.Name not in ClusterByGate
                )
                or ClusterByGate.get(Consumer.Name)
                == Request.TargetCluster
            )
            if (TargetPlaced := CandidateGateByName.get(Consumer.Name))
            is not None
            for InputIndex, InputSignal in enumerate(Consumer.Inputs)
            if InputSignal == Request.Signal
        }))
        return replace(
            Request,
            SourceTerminal=SourceTerminal,
            TargetTerminals=TargetTerminals,
        )

    SourceDiagnostics = dict(
        Source.Placed.LocalRouteDiagnostics or {}
    )
    Diagnostics.update({
        "Accepted": True,
        "Reason": "access-distinct-local-eco",
        "Clusters": RepairByCluster,
        "TouchedClusterCount": len(TouchedClusters),
        "ChangedGateCount": len(ChangedGateNames),
        "InvalidatedSignals": sorted(InvalidatedSignals),
        "SourceEnvelope": list(SourceEnvelope),
        "CandidateEnvelope": list(CandidateEnvelope),
        "SourceMandatoryAccessOwnershipFingerprint": (
            SourceProfile.OwnershipFingerprint
        ),
        "CandidateMandatoryAccessOwnershipFingerprint": (
            CandidateProfile.OwnershipFingerprint
        ),
        "PreservedLocalClaimCount": sum(
            Claim.Signal not in InvalidatedSignals
            for Claim in Source.Placed.LocalRouteClaims or ()
        ),
        "InvalidatedLocalClaimCount": sum(
            Claim.Signal in InvalidatedSignals
            for Claim in Source.Placed.LocalRouteClaims or ()
        ),
    })
    SourceDiagnostics["__TransactionalClusterEndpointRepair__"] = (
        Diagnostics
    )
    CandidateLeaseRequests = tuple(
        RefreshLease(Request)
        for Request in Source.ClusterBoundaryLeaseRequests
    )
    CandidatePlaced = PlacedDesign(
        Module=Module,
        PlacedGates=CandidateGates,
        RouteGuides=RetainUnchangedSignalEntries(
            Source.Placed.RouteGuides
        ),
        RouteLayers=RetainUnchangedSignalEntries(
            Source.Placed.RouteLayers
        ),
        FrozenNetWires=RetainUnchangedSignalEntries(
            Source.Placed.FrozenNetWires
        ),
        LocalNetBranches=RetainUnchangedSignalEntries(
            Source.Placed.LocalNetBranches
        ),
        LocalNetTargets=RetainUnchangedSignalEntries(
            Source.Placed.LocalNetTargets
        ),
        LocalRouteClaims=tuple(
            Claim
            for Claim in Source.Placed.LocalRouteClaims or ()
            if Claim.Signal not in InvalidatedSignals
        ),
        LocalRouteDiagnostics=SourceDiagnostics,
        ClusterBoundaryLeaseRequests=CandidateLeaseRequests,
        CompleteClusterInterfaceAccess=(
            Source.CompleteClusterInterfaceAccess
        ),
    )
    return TransactionalClusterEndpointRepairResult(
        PcbPlacement(
            Placed=CandidatePlaced,
            Clusters=Source.Clusters,
            SignalOrder=Source.SignalOrder,
            LayerCount=Source.LayerCount,
            PackedClusters=Source.PackedClusters,
            ClusterBoundaryLeaseRequests=CandidateLeaseRequests,
            ClusterLocalRouteTemplates=tuple(
                Template
                for Template in Source.ClusterLocalRouteTemplates
                if Template.ClusterId not in TouchedClusters
            ),
            ClusterBoundaryLeaseVariant=(
                Source.ClusterBoundaryLeaseVariant
            ),
            CompleteClusterInterfaceAccess=(
                Source.CompleteClusterInterfaceAccess
            ),
            MandatoryAccessPreScreenProfile=CandidateProfile,
        ),
        Diagnostics,
    )


def ShouldIncludeNearPortalPackedAccessRepair(
    *,
    RelocationVariant: int,
    EnableInternalPinBankGeometryRepair: bool,
) -> bool:
    """Enable the stronger local search for typed internal pin-bank work."""
    return (
        RelocationVariant >= 12
        or EnableInternalPinBankGeometryRepair
    )


def BuildDerivedPinAlignmentOffsets(
    Technology: Any = DefaultRedstoneRoutingTechnology,
) -> tuple[tuple[int, int], ...]:
    """Derive compact pin offsets from connectivity and track geometry."""
    Origin = (0, 0, 0)
    PlanarNeighborOffsets = {
        (Position[0], Position[2])
        for Position in Technology.NeighborPositions(Origin)
        if Position[1] == Origin[1]
    }
    CardinalX = sorted({
        DeltaX for DeltaX, DeltaZ in PlanarNeighborOffsets
        if DeltaX and not DeltaZ
    })
    CardinalZ = sorted({
        DeltaZ for DeltaX, DeltaZ in PlanarNeighborOffsets
        if DeltaZ and not DeltaX
    })
    TrackLandingDistance = max(1, Technology.TrackPitch - 1)
    Offsets = {
        (0, 0),
        *PlanarNeighborOffsets,
        *((DeltaX, DeltaZ) for DeltaX in CardinalX for DeltaZ in CardinalZ),
        *((Sign * TrackLandingDistance, 0) for Sign in (-1, 1)),
        *((0, Sign * TrackLandingDistance) for Sign in (-1, 1)),
    }
    return tuple(sorted(Offsets))


def BuildPinAlignedPackedCluster(
    Names: tuple[str, ...],
    InternalByName: dict[str, Any],
    BeamWidth: int,
    CandidateIndex: int = 0,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> tuple[
    dict[str, tuple[int, int]],
    dict[str, int],
    dict[str, bool],
] | None:
    """Materialize one explicitly selected retained graph-core state.

    ``None`` continues to mean that the bounded graph beam could not form any
    legal state.  An index beyond a non-empty retained beam is a caller error,
    not a request to quietly use a row-beam placement instead.
    """
    if CandidateIndex < 0:
        raise ValueError("packed cluster candidate index cannot be negative")
    States = _BuildPinAlignedPackedClusterStates(
        Names,
        InternalByName,
        BeamWidth,
        WorkCheck=WorkCheck,
    )
    if not States:
        return None
    if CandidateIndex >= len(States):
        raise ValueError(
            "packed cluster candidate index exceeds retained state count "
            f"({CandidateIndex} >= {len(States)})"
        )
    return States[CandidateIndex].Materialize()


def BuildPinAlignedPackedClusterPortfolio(
    Names: tuple[str, ...],
    InternalByName: dict[str, Any],
    BeamWidth: int,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> PinAlignedPackedClusterPortfolio:
    """Materialize the finite non-dominated graph-core domain up front.

    States retain their legacy ``CandidateIndex`` values.  A caller that needs
    to invoke :func:`BuildPinAlignedPackedCluster` later must use that explicit
    index, rather than enumerating a guessed contiguous range.
    """
    States = _BuildPinAlignedPackedClusterStates(
        Names,
        InternalByName,
        BeamWidth,
        WorkCheck=WorkCheck,
    )
    NonDominatedStates = tuple(
        State
        for State in States
        if not any(
            Other is not State
            and _PinAlignedPackedClusterStateDominates(Other, State)
            for Other in States
        )
    )
    return PinAlignedPackedClusterPortfolio(
        States=NonDominatedStates,
        RawCandidateCount=len(States),
    )


def CountPinAlignedPackedClusterPortfolio(
    Names: tuple[str, ...],
    InternalByName: dict[str, Any],
    BeamWidth: int,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> int:
    """Return the exact finite count of valid graph-core portfolio states."""
    return BuildPinAlignedPackedClusterPortfolio(
        Names,
        InternalByName,
        BeamWidth,
        WorkCheck=WorkCheck,
    ).CandidateCount


def _PinAlignedPackedClusterStateDominates(
    First: PinAlignedPackedClusterState,
    Second: PinAlignedPackedClusterState,
) -> bool:
    """Compare graph states on physical objectives, excluding tie-breakers."""
    return (
        all(
            FirstValue <= SecondValue
            for FirstValue, SecondValue in zip(First.Objective, Second.Objective)
        )
        and any(
            FirstValue < SecondValue
            for FirstValue, SecondValue in zip(First.Objective, Second.Objective)
        )
    )


def _BuildPinAlignedPackedClusterStates(
    Names: tuple[str, ...],
    InternalByName: dict[str, Any],
    BeamWidth: int,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> tuple[PinAlignedPackedClusterState, ...]:
    """Build and cache the raw bounded graph beam without selecting a state."""
    if BeamWidth < 1:
        raise ValueError("packed cluster beam width must be positive")
    PortfolioKey = (
        tuple(Names),
        tuple(
            (
                Name,
                tuple(map(str, InternalByName[Name].Inputs)),
                tuple(map(str, InternalByName[Name].Outputs)),
            )
            for Name in sorted(Names)
        ),
        BeamWidth,
        repr(DefaultRedstoneRoutingTechnology),
    )
    CachedPortfolio = _PinAlignedPackedClusterPortfolioCache.get(
        PortfolioKey
    )
    if CachedPortfolio is not None:
        return CachedPortfolio
    NameSet = set(Names)
    ProducerBySignal = {
        Signal: Gate.Name
        for Gate in InternalByName.values()
        for Signal in Gate.Outputs
    }
    ConsumersBySignal: dict[str, list[tuple[str, int]]] = {}
    for Gate in InternalByName.values():
        for InputIndex, Signal in enumerate(Gate.Inputs):
            ConsumersBySignal.setdefault(Signal, []).append((Gate.Name, InputIndex))
    Adjacency = {Name: set() for Name in Names}
    for Signal, Producer in ProducerBySignal.items():
        if Producer not in NameSet:
            continue
        for Consumer, _InputIndex in ConsumersBySignal.get(Signal, ()):
            if Consumer in NameSet and Consumer != Producer:
                Adjacency[Producer].add(Consumer)
                Adjacency[Consumer].add(Producer)
    Start = min(Names, key=lambda Name: (-len(Adjacency[Name]), Name))
    StartGate = BuildPlacedGate(InternalByName[Start], 0, 1, 0, 0, False)
    Beam: list[dict[str, Any]] = [{Start: StartGate}]
    PlacedNames = {Start}
    # A bounded graph beam revisits the same transformed macro pairs across
    # many partial states.  Exact physical conflict checking is authoritative,
    # but recomputing its electrical footprints for every revisit made the
    # finite upfront portfolio dominate the entire small-design deadline.
    # Cache only immutable geometry/signals of this one module; it cannot
    # create a candidate or relax a conflict.
    ConflictCache: dict[
        tuple[
            tuple[str, int, int, int, int, bool],
            tuple[str, int, int, int, int, bool],
        ],
        bool,
    ] = {}

    def ConflictKey(Gate: Any) -> tuple[str, int, int, int, int, bool]:
        return (
            str(Gate.Name),
            int(Gate.X),
            int(Gate.Y),
            int(Gate.Z),
            int(Gate.Rotation),
            bool(Gate.MirrorX),
        )

    def CachedPcbGatesConflict(First: Any, Second: Any) -> bool:
        Key = (ConflictKey(First), ConflictKey(Second))
        Cached = ConflictCache.get(Key)
        if Cached is None:
            Cached = PcbGatesConflict(First, Second)
            ConflictCache[Key] = Cached
        return Cached

    def ChooseNext() -> str:
        return min(
            NameSet - PlacedNames,
            key=lambda Name: (
                -len(Adjacency[Name] & PlacedNames),
                -len(Adjacency[Name]),
                Name,
            ),
        )

    def Score(State: dict[str, Any]) -> tuple[Any, ...]:
        Endpoints: dict[str, list[tuple[int, int]]] = {}
        PinOwners: list[tuple[tuple[int, int, int], str]] = []
        AccessPositionsBySignal: dict[
            str,
            set[tuple[int, int, int]],
        ] = {}
        for Gate in State.values():
            if Gate.OutputPin is not None:
                for Signal in Gate.Outputs:
                    Endpoints.setdefault(Signal, []).append(
                        (Gate.OutputPin[0], Gate.OutputPin[2])
                    )
                    PinOwners.append((Gate.OutputPin, Signal))
                    if Gate.OutputDirection is not None:
                        AccessPositionsBySignal.setdefault(
                            str(Signal),
                            set(),
                        ).update(
                            (
                                Gate.OutputPin[0]
                                + Gate.OutputDirection[0] * Offset,
                                Gate.OutputPin[1]
                                + Gate.OutputDirection[1] * Offset,
                                Gate.OutputPin[2]
                                + Gate.OutputDirection[2] * Offset,
                            )
                            for Offset in range(
                                DefaultRedstoneRoutingTechnology.AccessLength
                            )
                        )
            for InputIndex, Signal in enumerate(Gate.Inputs):
                Pin = Gate.InputPins[InputIndex]
                Endpoints.setdefault(Signal, []).append((Pin[0], Pin[2]))
                PinOwners.append((Pin, Signal))
                Direction = Gate.InputDirections[InputIndex]
                AccessPositionsBySignal.setdefault(
                    str(Signal),
                    set(),
                ).update(
                    (
                        Pin[0] + Direction[0] * Offset,
                        Pin[1] + Direction[1] * Offset,
                        Pin[2] + Direction[2] * Offset,
                    )
                    for Offset in range(
                        DefaultRedstoneRoutingTechnology.AccessLength
                    )
                )
        CrossElectricalPenalty = sum(
            1
            for Index, (FirstPin, FirstSignal) in enumerate(PinOwners)
            for SecondPin, SecondSignal in PinOwners[Index + 1 :]
            if FirstSignal != SecondSignal
            and abs(FirstPin[1] - SecondPin[1]) <= 1
            and (
                abs(FirstPin[0] - SecondPin[0])
                + abs(FirstPin[2] - SecondPin[2])
                <= 1
            )
        )
        Signals = tuple(sorted(AccessPositionsBySignal))
        ElectricalExclusionsBySignal = {
            Signal: DefaultRedstoneRoutingTechnology.BuildElectricalExclusions(
                AccessPositionsBySignal[Signal]
            )
            for Signal in Signals
        }
        AccessConflictPenalty = sum(
            1
            for FirstIndex, FirstSignal in enumerate(Signals)
            for SecondSignal in Signals[FirstIndex + 1 :]
            if (
                AccessPositionsBySignal[FirstSignal]
                & ElectricalExclusionsBySignal[SecondSignal]
            )
            or (
                AccessPositionsBySignal[SecondSignal]
                & ElectricalExclusionsBySignal[FirstSignal]
            )
        )
        Hpwl = sum(
            max(X for X, _Z in Values)
            - min(X for X, _Z in Values)
            + max(Z for _X, Z in Values)
            - min(Z for _X, Z in Values)
            for Values in Endpoints.values()
            if len(Values) > 1
        )
        MinimumX = min(Gate.X for Gate in State.values())
        MinimumZ = min(Gate.Z for Gate in State.values())
        MaximumX = max(
            Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0]
            for Gate in State.values()
        )
        MaximumZ = max(
            Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1]
            for Gate in State.values()
        )
        Width = MaximumX - MinimumX
        Depth = MaximumZ - MinimumZ
        Stable = tuple(
            (Name, State[Name].X, State[Name].Z, State[Name].Rotation, State[Name].MirrorX)
            for Name in sorted(State)
        )
        return (
            AccessConflictPenalty,
            CrossElectricalPenalty,
            Width * Depth,
            max(Width, Depth),
            Hpwl,
            Stable,
        )

    while PlacedNames != NameSet:
        Name = ChooseNext()
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "graph-beam-gate",
                "GateName": Name,
                "CompletedGates": len(PlacedNames),
                "TotalGates": len(NameSet),
                "BeamStates": len(Beam),
            })
        NextBeam = []
        for StateIndex, State in enumerate(Beam):
            if WorkCheck is not None:
                WorkCheck({
                    "Phase": "graph-beam-state",
                    "GateName": Name,
                    "CompletedStates": StateIndex,
                    "TotalStates": len(Beam),
                })
            Connections = []
            GateValue = InternalByName[Name]
            for InputIndex, Signal in enumerate(GateValue.Inputs):
                ProducerName = ProducerBySignal.get(Signal)
                if ProducerName in State:
                    Connections.append(
                        (State[ProducerName].OutputPin, "Input", InputIndex)
                    )
            for Signal in GateValue.Outputs:
                for ConsumerName, InputIndex in ConsumersBySignal.get(Signal, ()):
                    if ConsumerName in State:
                        Connections.append(
                            (State[ConsumerName].InputPins[InputIndex], "Output", 0)
                        )
            CandidateKeys = set()
            for ExistingPin, PinKind, PinIndex in Connections:
                for Rotation in (0, 90, 180, 270):
                    for MirrorX in (False, True):
                        Origin = BuildPlacedGate(
                            GateValue, 0, 1, 0, Rotation, MirrorX
                        )
                        LocalPin = (
                            Origin.InputPins[PinIndex]
                            if PinKind == "Input"
                            else Origin.OutputPin
                        )
                        for DeltaX, DeltaZ in BuildDerivedPinAlignmentOffsets():
                            CandidateKeys.add(
                                (
                                    ExistingPin[0] + DeltaX - LocalPin[0],
                                    ExistingPin[2] + DeltaZ - LocalPin[2],
                                    Rotation,
                                    MirrorX,
                                )
                            )
            OrderedCandidateKeys = sorted(CandidateKeys)
            for CandidateKeyIndex, (X, Z, Rotation, MirrorX) in enumerate(
                OrderedCandidateKeys
            ):
                if WorkCheck is not None and CandidateKeyIndex % 32 == 0:
                    WorkCheck({
                        "Phase": "graph-beam-candidate",
                        "GateName": Name,
                        "CompletedCandidates": CandidateKeyIndex,
                        "TotalCandidates": len(OrderedCandidateKeys),
                    })
                Candidate = BuildPlacedGate(
                    GateValue, X, 1, Z, Rotation, MirrorX
                )
                if any(
                    CachedPcbGatesConflict(Candidate, Existing)
                    for Existing in State.values()
                ):
                    continue
                CandidateState = dict(State)
                CandidateState[Name] = Candidate
                NextBeam.append((Score(CandidateState), CandidateState))
        if not NextBeam:
            return None
        NextBeam.sort(key=lambda Value: Value[0])
        Beam = [State for _Key, State in NextBeam[:BeamWidth]]
        PlacedNames.add(Name)

    Portfolio: list[PinAlignedPackedClusterState] = []
    SeenGeometry = set()
    for State in sorted(Beam, key=Score):
        MinimumX = min(Gate.X for Gate in State.values())
        MinimumZ = min(Gate.Z for Gate in State.values())
        Candidate = (
            {
                Name: (Gate.X - MinimumX, Gate.Z - MinimumZ)
                for Name, Gate in State.items()
            },
            {Name: Gate.Rotation for Name, Gate in State.items()},
            {Name: Gate.MirrorX for Name, Gate in State.items()},
        )
        Identity = tuple(
            (
                Name,
                Candidate[0][Name],
                Candidate[1][Name],
                Candidate[2][Name],
            )
            for Name in sorted(State)
        )
        if Identity in SeenGeometry:
            continue
        SeenGeometry.add(Identity)
        CandidateScore = Score(State)
        Portfolio.append(
            PinAlignedPackedClusterState.FromMutableCandidate(
                CandidateIndex=len(Portfolio),
                Candidate=Candidate,
                Objective=tuple(
                    int(Value)
                    for Value in CandidateScore[:5]
                ),
            )
        )
    CachedPortfolio = tuple(Portfolio)
    _PinAlignedPackedClusterPortfolioCache[PortfolioKey] = CachedPortfolio
    return CachedPortfolio


def CompactWeightedPlacement(
    Module: Any,
    Placed: PlacedDesign,
    MaximumPasses: int = 12,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> PlacedDesign:
    """Pull every template toward its nets and compact bounds legally."""
    SourceByName = {Gate.Name: Gate for Gate in Module.Gates}
    Current = Placed
    for _Pass in range(MaximumPasses):
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "placement-compaction-pass",
                "PassIndex": _Pass,
                "MaximumPasses": MaximumPasses,
            })
        Improved = False
        Producers = {
            Signal: Gate
            for Gate in Current.PlacedGates
            for Signal in Gate.Outputs
        }
        Consumers: dict[str, list[Any]] = {}
        for Gate in Current.PlacedGates:
            for Signal in Gate.Inputs:
                Consumers.setdefault(Signal, []).append(Gate)
        CurrentGates = list(Current.PlacedGates)
        for GateIndex, Gate in enumerate(CurrentGates):
            if WorkCheck is not None:
                WorkCheck({
                    "Phase": "placement-compaction-gate",
                    "PassIndex": _Pass,
                    "CompletedGates": GateIndex,
                    "TotalGates": len(CurrentGates),
                })
            if Gate.Kind != "NAND":
                continue
            Connected = []
            for Signal in Gate.Inputs:
                Producer = Producers.get(Signal)
                if Producer is not None:
                    Connected.append((Producer.X, Producer.Z))
            for Signal in Gate.Outputs:
                Connected.extend(
                    (Consumer.X, Consumer.Z)
                    for Consumer in Consumers.get(Signal, ())
                )
            if not Connected:
                continue
            TargetX = median(Value[0] for Value in Connected)
            TargetZ = median(Value[1] for Value in Connected)
            Directions = []
            if TargetX != Gate.X:
                Directions.append((1 if TargetX > Gate.X else -1, 0))
            if TargetZ != Gate.Z:
                Directions.append((0, 1 if TargetZ > Gate.Z else -1))
            for Direction in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                if Direction not in Directions:
                    Directions.append(Direction)
            CurrentKey = PlacementCompactKey(Current)
            BestCandidate = None
            BestKey = CurrentKey
            for DirectionIndex, (DeltaX, DeltaZ) in enumerate(Directions):
                if WorkCheck is not None:
                    WorkCheck({
                        "Phase": "placement-compaction-candidate",
                        "PassIndex": _Pass,
                        "GateName": Gate.Name,
                        "CompletedDirections": DirectionIndex,
                        "TotalDirections": len(Directions),
                    })
                ShiftedGates = [
                    BuildPlacedGate(
                        SourceByName[Other.Name],
                        Other.X + (DeltaX if Other.Name == Gate.Name else 0),
                        Other.Y,
                        Other.Z + (DeltaZ if Other.Name == Gate.Name else 0),
                        Other.Rotation,
                        Other.MirrorX,
                    )
                    for Other in Current.PlacedGates
                ]
                Candidate = PlacedDesign(Module=Module, PlacedGates=ShiftedGates)
                if any(
                    PcbGatesConflict(First, Second)
                    for Index, First in enumerate(ShiftedGates)
                    for Second in ShiftedGates[Index + 1 :]
                ):
                    continue
                CandidateKey = PlacementCompactKey(Candidate)
                if CandidateKey >= BestKey:
                    continue
                BestCandidate = Candidate
                BestKey = CandidateKey
            if BestCandidate is not None:
                Current = BestCandidate
                Improved = True
        if not Improved:
            break
    return Current


def AddGuideLine(
    Values: set[tuple[int, int]],
    Start: tuple[int, int],
    End: tuple[int, int],
) -> None:
    """Rasterize one orthogonal guide segment."""
    if Start[0] == End[0]:
        for Z in range(min(Start[1], End[1]), max(Start[1], End[1]) + 1):
            Values.add((Start[0], Z))
        return
    for X in range(min(Start[0], End[0]), max(Start[0], End[0]) + 1):
        Values.add((X, Start[1]))


def BuildSignalGuide(
    Source: tuple[int, int],
    Targets: list[tuple[int, int]],
    XFirst: bool,
) -> frozenset[tuple[int, int]]:
    """Build a rectilinear fanout tree biased to one preferred direction."""
    Guide = {Source}
    Remaining = list(Targets)
    while Remaining:
        Target = min(
            Remaining,
            key=lambda Value: min(
                abs(Value[0] - Existing[0]) + abs(Value[1] - Existing[1])
                for Existing in Guide
            ),
        )
        Anchor = min(
            Guide,
            key=lambda Value: (
                abs(Target[0] - Value[0]) + abs(Target[1] - Value[1]),
                Value,
            ),
        )
        Corner = (
            (Target[0], Anchor[1])
            if XFirst
            else (Anchor[0], Target[1])
        )
        AddGuideLine(Guide, Anchor, Corner)
        AddGuideLine(Guide, Corner, Target)
        Remaining.remove(Target)
    return frozenset(Guide)


def AddPcbRoutingGuides(
    Placed: PlacedDesign,
    MaximumLayerCount: int = 0,
) -> PcbPlacement:
    """Attach deterministic routing metadata without performing route planning."""
    Signals = {
        Signal
        for Gate in Placed.PlacedGates
        if Gate.OutputPin is not None
        for Signal in Gate.Outputs
    } & {
        Signal
        for Gate in Placed.PlacedGates
        for Signal in Gate.Inputs
    }
    # A capped three-layer guide plane is sufficient for small circuits, but
    # cannot assign disjoint portal ownership for the 64+ signal arithmetic
    # region. Let only that scale use the technology's full routing ladder.
    EffectiveMaximumLayerCount = (
        DefaultRedstoneRoutingTechnology.MaximumRoutableLayerCount
        if (
            MaximumLayerCount > 0
            and len(Signals) >= 64
        )
        else MaximumLayerCount
    )
    LayerCount = min(
        (
            EffectiveMaximumLayerCount
            if EffectiveMaximumLayerCount > 0
            else DefaultRedstoneRoutingTechnology.MaximumRoutableLayerCount
        ),
        max(
            # An explicitly selected pre-route envelope is a proof-backed
            # routing contract.  Preserve the legacy technology floor only
            # when no finite layer envelope was requested.
            (
                1
                if MaximumLayerCount > 0
                else DefaultRedstoneRoutingTechnology.MinimumRoutingLayerCount
            ),
            ceil(sqrt(max(1, len(Signals)))),
        ),
    )
    Guided = PlacedDesign(
        Module=Placed.Module,
        PlacedGates=Placed.PlacedGates,
        RouteGuides={},
        RouteLayers={},
        FrozenNetWires=Placed.FrozenNetWires,
        LocalNetBranches=Placed.LocalNetBranches,
        LocalNetTargets=Placed.LocalNetTargets,
        LocalRouteClaims=Placed.LocalRouteClaims,
        LocalRouteDiagnostics=Placed.LocalRouteDiagnostics,
        DerivedPerimeterSlotDomain=Placed.DerivedPerimeterSlotDomain,
        DerivedPerimeterSlotAssignment=(
            Placed.DerivedPerimeterSlotAssignment
        ),
    )
    return PcbPlacement(
        Placed=Guided,
        Clusters=(),
        SignalOrder=tuple(sorted(Signals)),
        LayerCount=LayerCount,
        DerivedPerimeterSlotDomain=Guided.DerivedPerimeterSlotDomain,
        DerivedPerimeterSlotAssignment=(
            Guided.DerivedPerimeterSlotAssignment
        ),
    )


def PlacePcbGraph(
    Netlist: Any,
    RoutingSpacing: int = 0,
    PlacementPolicy: PlacementPolicy | None = None,
    PackingPolicy: NandPackingPolicy | None = None,
    ClusterPolicy: ClusteringPolicy | None = None,
    MaximumBoundaryTerminals: int | None = None,
    MaximumEntrancesPerSignal: int | None = None,
    RelocationSignals: frozenset[str] = frozenset(),
    RelocationPrioritySignals: frozenset[str] = frozenset(),
    RequiredRelocationSignals: frozenset[str] = frozenset(),
    RelocationVariant: int = 0,
    JointPlacementCandidateIndex: int = 0,
    AssignmentCut: RoutingAssignmentCut | None = None,
    AssignmentConstraints: PlacementAssignmentConstraintSet = (
        PlacementAssignmentConstraintSet()
    ),
    CoordinatedCandidateDiversificationSignals: frozenset[str] = frozenset(),
    EnableClusterLocalRouteReuse: bool = False,
    EnableClusterBoundaryLeases: bool = False,
    EnableClusterInterfacePlacementFeasibility: bool = False,
    CutDrivenClusterRefinementSignals: frozenset[str] | None = None,
    EnableInternalPinBankGeometryRepair: bool = False,
    InternalPinBankGeometryRepairSignals: frozenset[str] = frozenset(),
    FocusedCutEpochPlacement: bool = False,
    TopologyCutFrontier: tuple[RoutingAssignmentCut, ...] = (),
    MandatoryAccessPreScreenOnly: bool = False,
    PlacementScoringOnly: bool = False,
    PreferAccessRingTerminals: bool = False,
    UseDerivedPerimeterTerminals: bool = False,
    DerivedTerminalLayoutVariantIndex: int = 0,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> PcbPlacement:
    """Cluster, optimize, legalize, and guide a generic NAND graph."""
    def CheckWork(Phase: str, **Diagnostics: object) -> None:
        if WorkCheck is not None:
            WorkCheck({"Phase": Phase, **Diagnostics})

    CheckWork("start")
    if RoutingSpacing < 0:
        raise ValueError("RoutingSpacing cannot be negative")
    if DerivedTerminalLayoutVariantIndex < 0:
        raise ValueError(
            "derived terminal layout variant index cannot be negative"
        )
    if (
        DerivedTerminalLayoutVariantIndex
        and not UseDerivedPerimeterTerminals
    ):
        raise ValueError(
            "terminal layout variants require derived perimeter terminals"
        )
    Module = Netlist.Modules[Netlist.Top]
    ModuleLayoutFingerprint = tuple(
        (
            Gate.Name,
            (
                Gate.Kind.value
                if hasattr(Gate.Kind, "value")
                else str(Gate.Kind)
            ),
            tuple(Gate.Inputs),
            tuple(Gate.Outputs),
        )
        for Gate in Module.Gates
    )
    PackedMode = bool(PackingPolicy is not None and PackingPolicy.Enabled)
    NandCount = sum(Gate.Kind.value == "NAND" for Gate in Module.Gates)
    TerminalPlacementPolicy = (
        PackingPolicy
        if PackingPolicy is not None
        else NandPackingPolicy()
    )
    AdaptiveClusterSize = (
        min(
            PackingPolicy.MaximumClusterCells,
            max(
                ClusterPolicy.MinimumCohesiveCells,
                ceil(ClusterPolicy.CohesiveCellScale * sqrt(max(1, NandCount))),
            ),
        )
        if PackedMode and ClusterPolicy is not None
        else (
            PackingPolicy.MaximumClusterCells
            if PackedMode
            else 32
        )
    )
    if (
        PackedMode
        and NandCount > 3 * PackingPolicy.MaximumClusterCells
    ):
        AdaptiveClusterSize = min(
            AdaptiveClusterSize,
            max(
                4,
                PackingPolicy.MaximumClusterCells
                - PackingPolicy.MaximumClusterCells // 8,
            ),
        )
    LogicalComponentGraph = BuildComponentGraph(
        Module,
        MaximumComponentGates=max(4, AdaptiveClusterSize + 4),
    )
    LogicalComponentByGate = (
        dict(LogicalComponentGraph.GateToComponent)
        if LogicalComponentGraph.Hierarchical
        else {}
    )
    ClusterRefinementSignals = tuple(sorted(
        CutDrivenClusterRefinementSignals
        if CutDrivenClusterRefinementSignals is not None
        else {
            Signal
            for Edge in BuildEffectiveAssignmentCutPairwiseEdges(
                AssignmentCut
            )
            for Signal in Edge
        }
    ))
    ClusterRefinementProfile = (
        CutDrivenClusterRefinementProfile(
            Signals=ClusterRefinementSignals,
            EdgeWeight=max(4, AdaptiveClusterSize),
        )
        if (
            EnableClusterInterfacePlacementFeasibility
            and ClusterRefinementSignals
        )
        else None
    )
    PlacementTopologyCacheKey = (
        ModuleLayoutFingerprint,
        PackedMode,
        AdaptiveClusterSize,
        repr(ClusterPolicy if PackedMode else None),
        MaximumBoundaryTerminals if PackedMode else None,
        (
            ClusterRefinementProfile.Signals
            if ClusterRefinementProfile is not None
            else ()
        ),
        (
            ClusterRefinementProfile.EdgeWeight
            if ClusterRefinementProfile is not None
            else 0
        ),
        LogicalComponentGraph.StructuralFingerprint,
    )
    CachedPlacementTopology = _PlacementTopologyCache.get(
        PlacementTopologyCacheKey
    )
    if CachedPlacementTopology is None:
        Levels = BuildTopologicalLevels(Module, WorkCheck=WorkCheck)
        Clusters = BuildConnectivityClusters(
            Module,
            MaximumClusterSize=AdaptiveClusterSize,
            Policy=ClusterPolicy if PackedMode else None,
            MaximumBoundaryTerminals=(
                MaximumBoundaryTerminals if PackedMode else None
            ),
            RefinementProfile=ClusterRefinementProfile,
            LogicalComponentByGate=LogicalComponentByGate,
            WorkCheck=WorkCheck,
        )
        # Publish only after both topology passes complete. A deadline cannot
        # leak a partial cluster decomposition into a later retained state.
        _PlacementTopologyCache[PlacementTopologyCacheKey] = (
            tuple(sorted(Levels.items())),
            tuple(tuple(Names) for Names in Clusters),
        )
    else:
        CachedLevels, CachedClusters = CachedPlacementTopology
        Levels = dict(CachedLevels)
        Clusters = tuple(tuple(Names) for Names in CachedClusters)
        CheckWork(
            "placement-topology-cache-hit",
            GateCount=len(Module.Gates),
            ClusterCount=len(Clusters),
        )
    ActiveConstraintWorkingSet = SelectPlacementConstraintWorkingSet(
        AssignmentCut,
        AssignmentConstraints,
        TopologyCutFrontier,
        ExpandConnectedComponent=FocusedCutEpochPlacement,
    )
    EffectivePairwiseConflictEdges = tuple(sorted({
        *BuildEffectiveAssignmentCutPairwiseEdges(AssignmentCut),
        *(
            Edge
            for Cut in TopologyCutFrontier
            for Edge in BuildEffectiveAssignmentCutPairwiseEdges(Cut)
        ),
        *ActiveConstraintWorkingSet.PairwiseConflictEdges,
    }))
    StructuredPairwiseSignals = frozenset(
        Signal
        for Edge in EffectivePairwiseConflictEdges
        for Signal in Edge
    )
    FrontierHigherOrderSignalSets = tuple(
        Signals
        for Cut in TopologyCutFrontier
        if (
            Signals := BuildAssignmentCutHigherOrderSignalSet(Cut)
        )
    )
    StructuredConstraintSignals = frozenset(
        Signal
        for Signals in (
            *ActiveConstraintWorkingSet.HigherOrderSignalSets,
            *FrontierHigherOrderSignalSets,
        )
        for Signal in Signals
    ) | frozenset(
        Signal
        for Edge in ActiveConstraintWorkingSet.ObservedInterfaceConflictEdges
        for Signal in Edge
    )
    RequiresStructuredJointRelocation = (
        RequiresStructuredAssignmentCutRelocation(AssignmentCut)
    )
    if AssignmentCut is not None:
        RelocationSignals = frozenset((
            *RelocationSignals,
            *AssignmentCut.RelocationSignals,
            *AssignmentCut.ConflictSignals,
            *AssignmentCut.NoCandidateSignals,
            *StructuredPairwiseSignals,
            *StructuredConstraintSignals,
        ))
    # A repeated candidate-starvation cut across access-distinct ownership
    # states proves that changing portal domains alone cannot make progress.
    # The scheduler supplies this flag only for that bounded repair epoch.
    # Include exactly the reported endpoints in the physical relocation focus
    # so a fresh state has a genuinely different pin-bank topology.
    InternalPinBankGeometrySignals = (
        SelectInternalPinBankGeometrySignals(
            Enabled=EnableInternalPinBankGeometryRepair,
            RepairSignals=InternalPinBankGeometryRepairSignals,
            CoordinatedCandidateDiversificationSignals=(
                CoordinatedCandidateDiversificationSignals
            ),
        )
    )
    if InternalPinBankGeometrySignals:
        RelocationSignals = frozenset((
            *RelocationSignals,
            *InternalPinBankGeometrySignals,
        ))
        RelocationPrioritySignals = frozenset((
            *RelocationPrioritySignals,
            *InternalPinBankGeometrySignals,
        ))
        RequiredRelocationSignals = frozenset((
            *RequiredRelocationSignals,
            *InternalPinBankGeometrySignals,
        ))
    (
        RelocationPrioritySignals,
        RequiredRelocationSignals,
    ) = BuildEffectiveStructuredRelocationFocus(
        AssignmentCut,
        AssignmentConstraints,
        RelocationPrioritySignals,
        RequiredRelocationSignals,
    )
    RelocationClusters = BuildRelocationClusterSet(
        Module,
        Clusters,
        RelocationSignals,
    )
    RequiredRelocationClusters = BuildRelocationClusterSet(
        Module,
        Clusters,
        RequiredRelocationSignals,
    )
    BoundaryEscapeRelocationClusters = BuildRelocationClusterSet(
        Module,
        Clusters,
        RelocationPrioritySignals or RelocationSignals,
    )
    RankedRequiredGeometryClusters = PrioritizeRelocationClusters(
        Module,
        Clusters,
        RelocationPrioritySignals or RequiredRelocationSignals,
    )
    LocalGeometryRepairClusters = frozenset(
        (
            RankedRequiredGeometryClusters[
                min(RelocationVariant, 1)
                % len(RankedRequiredGeometryClusters)
            ],
        )
        if (
            PackedMode
            and PackingPolicy.EnableLocalGeometryRepair
            and not RequiresStructuredJointRelocation
            and RankedRequiredGeometryClusters
        )
        else ()
    )
    CheckWork("connectivity-clusters", ClusterCount=len(Clusters))
    CheckWork("cluster-slots", ClusterCount=len(Clusters))
    # Establish the topology-only seed before optional vertical-stack planning.
    # The joint orientation search below replaces it with the scored final
    # placement after all local cluster layouts are known.
    Assignment, ColumnCount, _RowCount = OptimizeClusterSlots(
        Module,
        Clusters,
        Levels,
        LogicalComponentByGate=LogicalComponentByGate,
        WorkCheck=WorkCheck,
    )
    InternalByName = {
        Gate.Name: Gate
        for Gate in Module.Gates
        if Gate.Kind.value == "NAND"
    }
    PackedRotation = 0
    DefaultRotation = PackedRotation if PackedMode else 270
    NandWidth, NandDepth = RotatedCellSize("NAND", DefaultRotation)
    CellPitchX = (
        NandWidth + 2
        if PackedMode
        else NandWidth + 3 + RoutingSpacing
    )
    CellPitchZ = (
        NandDepth + 1
        if PackedMode
        else NandDepth + 2 + RoutingSpacing
    )
    LocalPositions: dict[str, tuple[int, int]] = {}
    LocalRotations: dict[str, int] = {}
    LocalMirrors: dict[str, bool] = {}
    ClusterSizes: dict[int, tuple[int, int]] = {}
    ClusterStructuralSignatures: dict[int, str] = {}
    ClusterReuseSources: dict[int, int | None] = {}
    ClusterStructuralMappings: dict[int, dict[str, str]] = {}
    ClusterStackIds: dict[int, int | None] = {}
    ClusterStackLevels: dict[int, int] = {}
    SelectedClusterVariants: dict[int, ClusterLayoutVariant] = {}
    JointPlacementDiagnostics: dict[str, object] = {}
    PackedAccessRepairByCluster: dict[int, dict[str, int]] = {}
    StackSuppressedRelocationClusters: set[int] = set()
    PhysicallyRelocatedClusters: frozenset[int] = frozenset()
    SignalProducerNames = {
        Signal: Gate.Name
        for Gate in Module.Gates
        for Signal in Gate.Outputs
    }
    for ClusterIndex, Names in enumerate(Clusters):
        CheckWork(
            "cluster-placement",
            CompletedClusters=ClusterIndex,
            TotalClusters=len(Clusters),
        )
        ClusterNames = set(Names)
        CutDrivenRefinementCluster = bool(
            ClusterRefinementProfile is not None
            and any(
                Signal in ClusterRefinementProfile.Signals
                for Name in Names
                for Signal in (
                    *InternalByName[Name].Inputs,
                    *InternalByName[Name].Outputs,
                )
            )
        )
        ReuseAccepted = False
        BaseLayoutCacheKey = (
            ModuleLayoutFingerprint,
            tuple(Names),
            RoutingSpacing,
            PackingPolicy.BeamWidth if PackedMode else 0,
            PackingPolicy.GraphBeamEnabled if PackedMode else False,
            (
                JointPlacementCandidateIndex
                if PackedMode and PackingPolicy.GraphBeamEnabled
                else 0
            ),
            PackingPolicy.EnableStructuralReuse if PackedMode else False,
            (
                PackingPolicy.MaximumStructuralReuseMappings
                if PackedMode
                else 0
            ),
        )
        CachedBaseLayout = (
            _PackedClusterBaseLayoutCache.get(BaseLayoutCacheKey)
            if PackedMode
            else None
        )
        if CachedBaseLayout is not None:
            (
                StructuralSignature,
                ReuseSource,
                StructuralMapping,
                CachedPositions,
                CachedRotations,
                CachedMirrors,
                CachedWidth,
                CachedDepth,
            ) = CachedBaseLayout
            ClusterStructuralSignatures[ClusterIndex] = StructuralSignature
            ClusterReuseSources[ClusterIndex] = ReuseSource
            if StructuralMapping:
                ClusterStructuralMappings[ClusterIndex] = dict(
                    StructuralMapping
                )
            LocalPositions.update(CachedPositions)
            LocalRotations.update(CachedRotations)
            LocalMirrors.update(CachedMirrors)
        elif PackedMode:
            StructuralSignature = AnalyzeNandClusterStructure(
                Module,
                Names,
                WorkCheck=WorkCheck,
            )[0]
            ClusterStructuralSignatures[ClusterIndex] = StructuralSignature
            ClusterReuseSources[ClusterIndex] = None
            if PackingPolicy.EnableStructuralReuse:
                for ReferenceIndex in range(ClusterIndex):
                    if (
                        ClusterStructuralSignatures.get(ReferenceIndex)
                        != StructuralSignature
                    ):
                        continue
                    Match = FindIsomorphicNandClusterMapping(
                        Module,
                        Clusters[ReferenceIndex],
                        Names,
                        PackingPolicy.MaximumStructuralReuseMappings,
                        WorkCheck=WorkCheck,
                    )
                    if Match is None:
                        continue
                    _Signature, Mapping = Match
                    CandidatePositions = {
                        CandidateName: LocalPositions[ReferenceName]
                        for ReferenceName, CandidateName in Mapping.items()
                    }
                    CandidateRotations = {
                        CandidateName: LocalRotations[ReferenceName]
                        for ReferenceName, CandidateName in Mapping.items()
                    }
                    CandidateMirrors = {
                        CandidateName: LocalMirrors.get(
                            ReferenceName, False
                        )
                        for ReferenceName, CandidateName in Mapping.items()
                    }
                    CandidateGates = [
                        BuildPlacedGate(
                            InternalByName[Name],
                            CandidatePositions[Name][0],
                            1,
                            CandidatePositions[Name][1],
                            CandidateRotations[Name],
                            CandidateMirrors[Name],
                        )
                        for Name in Names
                    ]
                    CandidatePlaced = PlacedDesign(
                        Module=Module,
                        PlacedGates=CandidateGates,
                    )
                    try:
                        if any(
                            PcbGatesConflict(First, Second)
                            for Index, First in enumerate(CandidateGates)
                            for Second in CandidateGates[Index + 1 :]
                        ):
                            raise ValueError("reused NAND placement conflicts")
                        BuildPlacedCellGeometry(CandidatePlaced)
                    except ValueError:
                        continue
                    LocalPositions.update(CandidatePositions)
                    LocalRotations.update(CandidateRotations)
                    LocalMirrors.update(CandidateMirrors)
                    ClusterReuseSources[ClusterIndex] = ReferenceIndex
                    ClusterStructuralMappings[ClusterIndex] = Mapping
                    ReuseAccepted = True
                    break
        LocalLevels: dict[str, int] = {}
        Remaining = set(Names)
        while Remaining:
            CheckWork(
                "cluster-ordering",
                ClusterIndex=ClusterIndex,
                RemainingGates=len(Remaining),
            )
            Progress = False
            for Name in sorted(Remaining):
                Gate = InternalByName[Name]
                Dependencies = {
                    ProducerName
                    for Signal in Gate.Inputs
                    if (ProducerName := SignalProducerNames.get(Signal))
                    in ClusterNames
                }
                if not Dependencies.issubset(LocalLevels):
                    continue
                LocalLevels[Name] = 1 + max(
                    (LocalLevels[Dependency] for Dependency in Dependencies),
                    default=-1,
                )
                Remaining.remove(Name)
                Progress = True
            if Progress:
                continue
            for Name in sorted(Remaining):
                LocalLevels[Name] = 0
            break

        OrderedNames = sorted(
            Names,
            key=lambda Name: (LocalLevels[Name], Name),
        )
        if PackedMode and CachedBaseLayout is not None:
            FoldColumns = 1
            FoldRows = 1
            PackedWidth = CachedWidth
            PackedDepth = CachedDepth
        elif PackedMode and ReuseAccepted:
            FoldColumns = 1
            FoldRows = 1
            PackedWidth = max(
                LocalPositions[Name][0]
                + RotatedCellSize("NAND", LocalRotations[Name])[0]
                for Name in Names
            )
            PackedDepth = max(
                LocalPositions[Name][1]
                + RotatedCellSize("NAND", LocalRotations[Name])[1]
                for Name in Names
            )
        elif PackedMode:
            NamesByLevel: dict[int, list[str]] = {}
            for Name in OrderedNames:
                NamesByLevel.setdefault(LocalLevels[Name], []).append(Name)
            FoldRows = max(NamesByLevel) + 1
            PackedXByName: dict[str, int] = {}
            PackedMirrorByName: dict[str, bool] = {}
            RefinedClusterFallback: tuple[
                dict[str, tuple[int, int]],
                dict[str, int],
                dict[str, bool],
            ] | None = None
            ClusterRowPitchZ = (
                CellPitchZ + 1
                if ClusterRefinementProfile is not None
                else CellPitchZ
            )
            for Row in range(FoldRows):
                CheckWork(
                    "row-beam",
                    ClusterIndex=ClusterIndex,
                    CompletedRows=Row,
                    TotalRows=FoldRows,
                )
                RowNames = NamesByLevel.get(Row, [])
                RowNames.sort(
                    key=lambda Name: (
                        median(
                            [
                                PackedXByName[Producer] + 1
                                for Signal in InternalByName[Name].Inputs
                                if (Producer := SignalProducerNames.get(Signal))
                                in PackedXByName
                            ]
                            or [0]
                        ),
                        Name,
                    )
                )
                RowBeam: list[
                    tuple[
                        tuple[int, int, tuple[int, ...], int, tuple[int, ...]],
                        dict[str, tuple[int, bool]],
                    ]
                ] = [
                    ((0, 0, (), 0, ()), {})
                ]
                for Name in RowNames:
                    CheckWork(
                        "row-beam-gate",
                        ClusterIndex=ClusterIndex,
                        GateName=Name,
                    )
                    ParentItems = [
                        (
                            InputIndex,
                            PackedXByName[Producer] + 1,
                            LocalLevels[Producer]
                            * ClusterRowPitchZ
                            + NandDepth,
                        )
                        for InputIndex, Signal in enumerate(InternalByName[Name].Inputs)
                        if (Producer := SignalProducerNames.get(Signal))
                        in PackedXByName
                    ]
                    ParentOutputs = [Value[1] for Value in ParentItems]
                    CandidateXs = {
                        OutputX + InputAlignment
                        for OutputX in (ParentOutputs or [0])
                        for InputAlignment in (0, -2)
                    }
                    CandidateXs.update(
                        Value + Shift
                        for Value in tuple(CandidateXs)
                        for Shift in (-10, -5, 5, 10)
                    )
                    if CutDrivenRefinementCluster:
                        # A cut-cohesive cluster can place several formerly
                        # separate interface consumers on one topological
                        # level.  Give that row one deterministic pin-bank
                        # lane per member instead of failing before the graph
                        # packer can materialize the refined topology.
                        RowCenter = int(median(ParentOutputs or [0]))
                        CandidateXs.update(
                            RowCenter + Lane * (NandWidth + 1)
                            for Lane in range(
                                -len(RowNames),
                                len(RowNames) + 1,
                            )
                        )
                    NextBeam = []
                    for PreviousKey, Assigned in RowBeam:
                        for CandidateX in sorted(CandidateXs):
                            if any(
                                abs(CandidateX - ExistingX) < 4
                                for ExistingX, _ExistingMirror in Assigned.values()
                            ):
                                continue
                            ExistingGates = [
                                BuildPlacedGate(
                                    InternalByName[ExistingName],
                                    ExistingX,
                                    1,
                                    LocalLevels[ExistingName]
                                    * ClusterRowPitchZ,
                                    PackedRotation,
                                    PackedMirrorByName[ExistingName],
                                )
                                for ExistingName, ExistingX in PackedXByName.items()
                            ]
                            ExistingGates.extend(
                                BuildPlacedGate(
                                    InternalByName[ExistingName],
                                    ExistingX,
                                    1,
                                    Row * ClusterRowPitchZ,
                                    PackedRotation,
                                    ExistingMirror,
                                )
                                for ExistingName, (
                                    ExistingX,
                                    ExistingMirror,
                                ) in Assigned.items()
                            )
                            OrientationOptions = []
                            for MirrorX in (False, True):
                                CandidateGate = BuildPlacedGate(
                                    InternalByName[Name],
                                    CandidateX,
                                    1,
                                    Row * ClusterRowPitchZ,
                                    PackedRotation,
                                    MirrorX,
                                )
                                if any(
                                    PcbGatesConflict(CandidateGate, ExistingGate)
                                    for ExistingGate in ExistingGates
                                ):
                                    continue
                                Pins = (
                                    (CandidateX, CandidateX + 2)
                                    if not MirrorX
                                    else (CandidateX + 2, CandidateX)
                                )
                                Misses = tuple(
                                    abs(OutputX - Pins[InputIndex])
                                    for InputIndex, OutputX, _OutputZ in ParentItems
                                )
                                InputZ = Row * ClusterRowPitchZ - 1
                                CrossPenalty = sum(
                                    1
                                    for InputIndex, OutputX, OutputZ in ParentItems
                                    for OtherIndex, OtherPinX in enumerate(Pins)
                                    if OtherIndex != InputIndex
                                    and OutputZ == InputZ
                                    and abs(OutputX - OtherPinX) <= 1
                                )
                                OrientationOptions.append(
                                    (CrossPenalty, sum(Misses), Misses, MirrorX)
                                )
                            if not OrientationOptions:
                                continue
                            CrossPenalty, Miss, Misses, CandidateMirror = min(
                                OrientationOptions
                            )
                            Candidate = dict(Assigned)
                            Candidate[Name] = (CandidateX, CandidateMirror)
                            Values = tuple(sorted(
                                ExistingX
                                for ExistingX, _MirrorX in Candidate.values()
                            ))
                            Span = max(Values) - min(Values) + NandWidth
                            NextBeam.append(
                                (
                                    (
                                        PreviousKey[0] + CrossPenalty,
                                        PreviousKey[1] + Miss,
                                        PreviousKey[2] + Misses,
                                        Span,
                                        Values,
                                    ),
                                    Candidate,
                                )
                            )
                    NextBeam.sort(key=lambda Value: Value[0])
                    RowBeam = NextBeam[: PackingPolicy.BeamWidth]
                if not RowBeam:
                    if CutDrivenRefinementCluster:
                        RefinedClusterFallback = (
                            BuildPinAlignedPackedCluster(
                                Names,
                                InternalByName,
                                PackingPolicy.BeamWidth,
                                CandidateIndex=JointPlacementCandidateIndex,
                                WorkCheck=WorkCheck,
                            )
                        )
                        if RefinedClusterFallback is not None:
                            break
                    raise ValueError(
                        f"Could not pack NAND cluster row {ClusterIndex}:{Row}"
                    )
                PackedXByName.update({
                    Name: X
                    for Name, (X, _MirrorX) in RowBeam[0][1].items()
                })
                PackedMirrorByName.update({
                    Name: MirrorX
                    for Name, (_X, MirrorX) in RowBeam[0][1].items()
                })
            if RefinedClusterFallback is None:
                MinimumPackedX = min(PackedXByName.values())
                for Name in OrderedNames:
                    LocalPositions[Name] = (
                        PackedXByName[Name] - MinimumPackedX,
                        LocalLevels[Name] * ClusterRowPitchZ,
                    )
                    LocalRotations[Name] = PackedRotation
                    LocalMirrors[Name] = PackedMirrorByName[Name]
            BeamPacked = (
                RefinedClusterFallback
                or (
                    BuildPinAlignedPackedCluster(
                        Names,
                        InternalByName,
                        PackingPolicy.BeamWidth,
                        CandidateIndex=JointPlacementCandidateIndex,
                        WorkCheck=WorkCheck,
                    )
                    if PackingPolicy.GraphBeamEnabled
                    else None
                )
            )
            if BeamPacked is not None:
                BeamPositions, BeamRotations, BeamMirrors = BeamPacked
                LocalPositions.update(BeamPositions)
                LocalRotations.update(BeamRotations)
                LocalMirrors.update(BeamMirrors)
            FoldColumns = 1
            PackedWidth = max(
                LocalPositions[Name][0]
                + RotatedCellSize("NAND", LocalRotations[Name])[0]
                for Name in Names
            )
            PackedDepth = max(
                LocalPositions[Name][1]
                + RotatedCellSize("NAND", LocalRotations[Name])[1]
                for Name in Names
            )
        else:
            FoldColumns = max(1, ceil(sqrt(len(OrderedNames))))
            FoldRows = ceil(len(OrderedNames) / FoldColumns)
            for PositionIndex, Name in enumerate(OrderedNames):
                Row = PositionIndex // FoldColumns
                Offset = PositionIndex % FoldColumns
                Column = (
                    Offset
                    if Row % 2 == 0
                    else FoldColumns - 1 - Offset
                )
                LocalPositions[Name] = (
                    Column * CellPitchX,
                    Row * CellPitchZ,
                )
                LocalRotations[Name] = 270 if Row % 2 == 0 else 90
        if PackedMode and CachedBaseLayout is None:
            _PackedClusterBaseLayoutCache[BaseLayoutCacheKey] = (
                ClusterStructuralSignatures[ClusterIndex],
                ClusterReuseSources.get(ClusterIndex),
                dict(ClusterStructuralMappings.get(ClusterIndex, {})),
                {
                    Name: LocalPositions[Name]
                    for Name in Names
                },
                {
                    Name: LocalRotations[Name]
                    for Name in Names
                },
                {
                    Name: LocalMirrors.get(Name, False)
                    for Name in Names
                },
                PackedWidth,
                PackedDepth,
            )
        if PackedMode and RequiredRelocationSignals:
            (
                LocalPositions,
                LocalMirrors,
                AccessRepairDiagnostics,
            ) = RepairPackedClusterAccess(
                Names,
                InternalByName,
                LocalPositions,
                LocalRotations,
                LocalMirrors,
                RequiredRelocationSignals,
                min(PackingPolicy.BeamWidth, 16),
                IncludeNearPortalConflicts=(
                    ShouldIncludeNearPortalPackedAccessRepair(
                        RelocationVariant=RelocationVariant,
                        EnableInternalPinBankGeometryRepair=(
                            EnableInternalPinBankGeometryRepair
                        ),
                    )
                ),
                WorkCheck=WorkCheck,
            )
            if AccessRepairDiagnostics:
                PackedAccessRepairByCluster[ClusterIndex] = (
                    AccessRepairDiagnostics
                )
                PackedWidth = max(
                    LocalPositions[Name][0]
                    + RotatedCellSize(
                        "NAND", LocalRotations[Name]
                    )[0]
                    for Name in Names
                )
                PackedDepth = max(
                    LocalPositions[Name][1]
                    + RotatedCellSize(
                        "NAND", LocalRotations[Name]
                    )[1]
                    for Name in Names
                )
        # The first mandatory-access repair is deliberately local. A signal
        # can touch several clusters even when its immutable collision is
        # wholly inside one cluster; spreading every touched cluster turns a
        # pin repair into a placement-wide footprint expansion. Variant 12
        # and a typed internal pin-bank epoch use the stronger near-portal
        # repair; other structured cuts remain in the footprint-neutral joint
        # slot/orientation search.
        if ShouldExpandBoundaryEscapeGeometry(
            PackedMode=PackedMode,
            ClusterIndex=ClusterIndex,
            BoundaryEscapeRelocationClusters=(
                BoundaryEscapeRelocationClusters
            ),
            PackedAccessRepairClusters=frozenset(
                PackedAccessRepairByCluster
            ),
            RequiredRelocationSignals=RequiredRelocationSignals,
            RelocationVariant=RelocationVariant,
            RelocationPrioritySignalCount=len(
                RelocationPrioritySignals
            ),
            LocalGeometryRepairClusters=LocalGeometryRepairClusters,
            StructuredAssignmentCutRelocation=(
                RequiresStructuredJointRelocation
            ),
        ):
            # A fixed-access cut inside one dense cluster needs actual
            # corridor geometry, not another mirror of the same pin layout.
            # Keep the single-cluster perturbation to one routing tile; the
            # flow's existing packed-area ceiling remains the hard limit.
            BoundaryEscapeGap = (
                max(
                    PackingPolicy.LocalGeometryRepairColumnGap,
                    RoutingSpacing,
                )
                if len(Clusters) > 4
                else PackingPolicy.LocalGeometryRepairColumnGap
            )
            if RelocationVariant % 2:
                DistinctZ = sorted({
                    LocalPositions[Name][1] for Name in Names
                })
                ZOffset = {
                    Value: Index * BoundaryEscapeGap
                    for Index, Value in enumerate(DistinctZ)
                }
                for Name in Names:
                    LocalX, LocalZ = LocalPositions[Name]
                    LocalPositions[Name] = (
                        LocalX,
                        LocalZ + ZOffset[LocalZ],
                    )
            else:
                DistinctX = sorted({
                    LocalPositions[Name][0] for Name in Names
                })
                XOffset = {
                    Value: Index * BoundaryEscapeGap
                    for Index, Value in enumerate(DistinctX)
                }
                for Name in Names:
                    LocalX, LocalZ = LocalPositions[Name]
                    LocalPositions[Name] = (
                        LocalX + XOffset[LocalX],
                        LocalZ,
                    )
            PackedWidth = max(
                LocalPositions[Name][0]
                + RotatedCellSize("NAND", LocalRotations[Name])[0]
                for Name in Names
            )
            PackedDepth = max(
                LocalPositions[Name][1]
                + RotatedCellSize("NAND", LocalRotations[Name])[1]
                for Name in Names
            )
        if PackedMode:
            CandidateGates = [
                BuildPlacedGate(
                    InternalByName[Name],
                    LocalPositions[Name][0],
                    1,
                    LocalPositions[Name][1],
                    LocalRotations[Name],
                    LocalMirrors.get(Name, False),
                )
                for Name in Names
            ]
            if any(
                PcbGatesConflict(First, Second)
                for Index, First in enumerate(CandidateGates)
                for Second in CandidateGates[Index + 1 :]
            ):
                raise ValueError(
                    f"Could not pack NAND cluster {ClusterIndex} legally"
                )
        ClusterSizes[ClusterIndex] = (
            PackedWidth if PackedMode else (FoldColumns - 1) * CellPitchX + NandWidth,
            PackedDepth if PackedMode else (FoldRows - 1) * CellPitchZ + NandDepth,
        )

    if PackedMode and PackingPolicy.EnableVerticalClusterStacking:
        CheckWork("vertical-stacking-start")
        # Feedback identifies every cluster participating in the cut, but it
        # does not make every stacked cluster geometrically illegal.  Breaking
        # the full stack for an unmodified contributor expands a local repair
        # into a placement-wide footprint.  Only a concrete local repair is
        # allowed to alter stack geometry below.
        UnrepairedRequiredRelocationClusters: frozenset[int] = frozenset()
        ClusterByGate = {
            Name: ClusterIndex
            for ClusterIndex, Names in enumerate(Clusters)
            for Name in Names
        }
        InterClusterWeights: dict[tuple[int, int], int] = {}
        for Gate in Module.Gates:
            TargetCluster = ClusterByGate.get(Gate.Name)
            if TargetCluster is None:
                continue
            for Signal in Gate.Inputs:
                SourceCluster = ClusterByGate.get(
                    SignalProducerNames.get(Signal, "")
                )
                if SourceCluster is None or SourceCluster == TargetCluster:
                    continue
                Edge = SourceCluster, TargetCluster
                InterClusterWeights[Edge] = InterClusterWeights.get(Edge, 0) + 1

        MaximumClusterStack = PackingPolicy.MaximumClustersPerStack
        StackByCluster: dict[int, int] = {}
        StackMembers: dict[int, list[int]] = {}
        NextStackId = 0
        RepeatedStructuralClusters = (
            len(Clusters) >= 4
            and len({
                ClusterStructuralSignatures.get(ClusterIndex)
                for ClusterIndex in range(len(Clusters))
            }) == 1
        )
        WeakInterClusterChain = bool(InterClusterWeights) and max(
            InterClusterWeights.values()
        ) <= 2
        PlanarRepeatedClusterPlacement = (
            RepeatedStructuralClusters
            and WeakInterClusterChain
        )
        if (
            PlanarRepeatedClusterPlacement
            and not PackingPolicy.EnableRepeatedStructuralVerticalStacking
        ):
            # Repeated dense clusters connected by only a small boundary cut
            # are not stack-compatible routing resources.  A shared X/Z deck
            # turns their independent pin escapes into mandatory electrical
            # claims.  Make the initial placement planar so its fixed area
            # ceiling is measured against routable geometry, not a stack that
            # can only be repaired by exceeding that ceiling.
            # Keep the optimizer's two-dimensional placement.  The routing
            # defect is overlapping vertical stacks, not adjacency itself;
            # serializing every repeated cluster into a fresh column makes
            # the cut longer without creating a new independent escape.
            # MaximumClusterStack below is the ownership boundary that
            # prevents later stack merging from collapsing this grid.
            MaximumClusterStack = 1

        def StackEndpoints(StackId: int) -> tuple[int, int]:
            Values = StackMembers[StackId]
            return Values[0], Values[-1]

        def AddCluster(
            StackId: int,
            Endpoint: int,
            Candidate: int,
        ) -> None:
            Members = StackMembers[StackId]
            if len(Members) >= MaximumClusterStack:
                return
            if Endpoint == Members[0]:
                Members.insert(0, Candidate)
                Assignment[Candidate] = Assignment[Members[1]]
            elif Endpoint == Members[-1]:
                Members.append(Candidate)
                Assignment[Candidate] = Assignment[Members[-2]]
            else:
                raise ValueError(
                    "Cannot stack cluster on a non-endpoint"
                )
            StackByCluster[Candidate] = StackId

        def MergeStacks(
            SourceStack: int,
            SourceEndpoint: int,
            RightStack: int,
            TargetEndpoint: int,
        ) -> None:
            SourceMembers = StackMembers[SourceStack]
            TargetMembers = StackMembers[RightStack]
            if len(SourceMembers) + len(TargetMembers) > MaximumClusterStack:
                return
            BestMerge: tuple[int, ...] | None = None
            for OrientedSource in (tuple(SourceMembers), tuple(reversed(SourceMembers))):
                if SourceEndpoint not in OrientedSource:
                    continue
                if OrientedSource[-1] != SourceEndpoint:
                    continue
                for OrientedTarget in (tuple(TargetMembers), tuple(reversed(TargetMembers))):
                    if TargetEndpoint not in OrientedTarget:
                        continue
                    if OrientedTarget[0] != TargetEndpoint:
                        continue
                    CandidateStack = OrientedSource + OrientedTarget[1:]
                    if len(set(CandidateStack)) != len(CandidateStack):
                        continue
                    if BestMerge is None or CandidateStack < BestMerge:
                        BestMerge = CandidateStack
            if BestMerge is None:
                return
            StackMembers[SourceStack] = list(BestMerge)
            for Member in BestMerge:
                StackByCluster[Member] = SourceStack
                Assignment[Member] = Assignment[SourceMembers[0]]
            del StackMembers[RightStack]

        OrderedInterClusterWeights = sorted(
            InterClusterWeights.items(),
            key=lambda Value: (-Value[1], Value[0]),
        )
        for EdgeIndex, ((Source, Target), Weight) in enumerate(
            OrderedInterClusterWeights
        ):
            CheckWork(
                "vertical-stacking",
                CompletedEdges=EdgeIndex,
                TotalEdges=len(OrderedInterClusterWeights),
            )
            if (
                Source in UnrepairedRequiredRelocationClusters
                or Target in UnrepairedRequiredRelocationClusters
            ):
                if (
                    ClusterStructuralSignatures.get(Source)
                    == ClusterStructuralSignatures.get(Target)
                ):
                    StackSuppressedRelocationClusters.update((Source, Target))
                continue
            if (
                Weight < 1
                or MaximumClusterStack < 2
                or (
                    ClusterStructuralSignatures.get(Source)
                    != ClusterStructuralSignatures.get(Target)
                )
            ):
                continue
            SourceStack = StackByCluster.get(Source)
            TargetStack = StackByCluster.get(Target)
            if SourceStack is None and TargetStack is None:
                StackId = NextStackId
                NextStackId += 1
                StackMembers[StackId] = [Source, Target]
                StackByCluster[Source] = StackId
                StackByCluster[Target] = StackId
                Assignment[Target] = Assignment[Source]
                continue
            if SourceStack is not None and TargetStack is not None:
                if SourceStack == TargetStack:
                    continue
                SourceFirst, SourceLast = StackEndpoints(SourceStack)
                TargetFirst, TargetLast = StackEndpoints(TargetStack)
                if Source not in (SourceFirst, SourceLast):
                    continue
                if Target not in (TargetFirst, TargetLast):
                    continue
                MergeStacks(
                    SourceStack=SourceStack,
                    SourceEndpoint=Source,
                    RightStack=TargetStack,
                    TargetEndpoint=Target,
                )
                continue
            ActiveStack = SourceStack if SourceStack is not None else TargetStack
            Candidate = Target if SourceStack is not None else Source
            Endpoint = Source if SourceStack is not None else Target
            if len(StackMembers[ActiveStack]) >= MaximumClusterStack:
                continue
            FirstEndpoint, LastEndpoint = StackEndpoints(ActiveStack)
            if Endpoint not in (FirstEndpoint, LastEndpoint):
                continue
            AddCluster(ActiveStack, Endpoint, Candidate)

        for ClusterIndex in range(len(Clusters)):
            StackId = StackByCluster.get(ClusterIndex)
            if StackId is None:
                ClusterStackIds[ClusterIndex] = None
                ClusterStackLevels[ClusterIndex] = 0
            else:
                Members = StackMembers[StackId]
                ClusterStackIds[ClusterIndex] = StackId
                ClusterStackLevels[ClusterIndex] = Members.index(ClusterIndex)

        for ClusterIndex in range(len(Clusters)):
            ClusterStackIds.setdefault(ClusterIndex, None)
            ClusterStackLevels.setdefault(ClusterIndex, 0)

        UsedColumns = sorted({Slot[0] for Slot in Assignment.values()})
        CompactColumn = {Column: Index for Index, Column in enumerate(UsedColumns)}
        Assignment = {
            ClusterIndex: (CompactColumn[Column], Row)
            for ClusterIndex, (Column, Row) in Assignment.items()
        }
        ColumnCount = len(UsedColumns)
    else:
        ClusterStackIds = {Index: None for Index in range(len(Clusters))}
        ClusterStackLevels = {Index: 0 for Index in range(len(Clusters))}
    # A routing conflict between clusters that were never stack-compatible
    # still needs to change physical geometry. Move those clusters into
    # dedicated columns; merely replaying the same slot assignment would turn
    # conflict feedback into a duplicate placement.
    RankedRequiredRelocationClusters = tuple(
        ClusterIndex
        for ClusterIndex in PrioritizeRelocationClusters(
            Module,
            Clusters,
            RequiredRelocationSignals,
        )
        if ClusterIndex not in PackedAccessRepairByCluster
        and ClusterIndex not in LocalGeometryRepairClusters
    )
    RequiredRelocationLimit = (
        (
            min(6, len(RankedRequiredRelocationClusters))
        )
        if (
            RequiresStructuredJointRelocation
            and len(RequiredRelocationSignals) > 2
        )
        else (
            2
            if RequiresStructuredJointRelocation
            else 1
        )
    )
    RequiredRelocationPriority = RankedRequiredRelocationClusters[
        :RequiredRelocationLimit
    ]
    if LocalGeometryRepairClusters:
        RequiredRelocationPriority = ()
    CurrentRelocationPriority = PrioritizeRelocationClusters(
        Module,
        Clusters,
        RelocationPrioritySignals or RelocationSignals,
    )
    PreviousFrontierSignals = frozenset(
        Signal
        for Cut in TopologyCutFrontier[1:]
        for Signal in (
            *Cut.ConflictSignals,
            *Cut.RelocationSignals,
            *Cut.PriorityRelocationSignals,
            *Cut.NoCandidateSignals,
            *(
                Signal
                for Edge in Cut.PairwiseConflictEdges
                for Signal in Edge
            ),
        )
    )
    PreviousFrontierPriority = PrioritizeRelocationClusters(
        Module,
        Clusters,
        PreviousFrontierSignals,
    )
    FocusedCutEpochClusters = SelectFocusedTopologyFrontierClusters(
        CurrentRelocationPriority,
        PreviousFrontierPriority,
        FocusedCutEpochPlacement,
    )
    FocusedInternalPinBankClusters = SelectFocusedCutEpochClusters(
        PrioritizeRelocationClusters(
            Module,
            Clusters,
            InternalPinBankGeometrySignals,
        ),
        bool(InternalPinBankGeometrySignals),
    )
    FocusedJointOptimizationClusters = (
        FocusedInternalPinBankClusters
        or FocusedCutEpochClusters
    )
    OptionalRelocationPriority = tuple(
        ClusterIndex
        for ClusterIndex in CurrentRelocationPriority
        if ClusterIndex not in StackSuppressedRelocationClusters
        and ClusterIndex not in RequiredRelocationPriority
    )
    if not OptionalRelocationPriority:
        OptionalRelocationPriority = tuple(
            ClusterIndex
            for ClusterIndex in CurrentRelocationPriority
            if ClusterIndex not in RequiredRelocationPriority
        )[:1]
    # Preserve every congestion-cut contributor in feedback, but perturb one
    # bounded pair set per deterministic round.  Swapping disjoint ranked
    # pairs changes every measured cut without expanding the packed
    # footprint.  Variants rotate through the complete ranked cut across the
    # existing placement-feedback rounds.
    MaximumOptionalRelocations = (
        min(2, len(OptionalRelocationPriority))
        if (
            RelocationVariant > 2
            and len(RelocationPrioritySignals) > 2
        )
        else 0
    )
    OptionalRelocationClusters = (
        tuple(
            OptionalRelocationPriority[
                (RelocationVariant + Offset)
                % len(OptionalRelocationPriority)
            ]
            for Offset in range(MaximumOptionalRelocations)
        )
        if OptionalRelocationPriority
        else ()
    )
    RelocationPriority = (
        *RequiredRelocationPriority,
        *OptionalRelocationClusters,
    )
    PhysicallyRelocatedClusters = frozenset(RelocationPriority)
    (
        JointPortfolioRelocationOffset,
        RotateExactPortfolioSlots,
    ) = BuildJointPortfolioBaseRelocationControls(
        RelocationVariant=RelocationVariant,
        JointPlacementCandidateIndex=JointPlacementCandidateIndex,
        RequiresStructuredJointRelocation=(
            RequiresStructuredJointRelocation
        ),
        PreservePortfolioBaseAssignment=FocusedCutEpochPlacement,
    )
    BaseAssignment, ColumnCount = RelocateClusterSlots(
        Assignment,
        ColumnCount,
        RelocationPriority,
        RelocationOffset=JointPortfolioRelocationOffset,
        RotateExactPortfolioSlots=RotateExactPortfolioSlots,
        ForceDedicatedColumns=(
            RequiresStructuredJointRelocation
            and len(RequiredRelocationSignals) > 2
            and (
                RelocationVariant > 0
                or JointPlacementCandidateIndex > 0
            )
        ),
    )
    AllVariantsByCluster = {
        ClusterIndex: tuple(
            TransformPackedClusterLayout(
                Names,
                LocalPositions,
                LocalRotations,
                LocalMirrors,
                Rotation,
                MirrorX,
                GatesByName=InternalByName,
            )
            for Rotation, MirrorX in (
                (0, False),
                (0, True),
                (90, False),
                (90, True),
                (180, False),
                (180, True),
                (270, False),
                (270, True),
            )
        )
        for ClusterIndex, Names in enumerate(Clusters)
    }
    JointVariantDiagnostics = {
        str(ClusterIndex): [
            {
                "Rotation": Variant.Rotation,
                "MirrorX": Variant.MirrorX,
                "Legal": Variant.IsLegal,
                "RejectionReason": Variant.RejectionReason,
            }
            for Variant in Variants
        ]
        for ClusterIndex, Variants in AllVariantsByCluster.items()
    }
    VariantsByCluster = {
        ClusterIndex: tuple(
            Variant for Variant in Variants if Variant.IsLegal
        )
        for ClusterIndex, Variants in AllVariantsByCluster.items()
    }
    MissingLegalVariants = [
        ClusterIndex
        for ClusterIndex, Variants in VariantsByCluster.items()
        if not Variants
    ]
    if MissingLegalVariants:
        raise ValueError(
            "No exact-legal rigid transform for packed NAND cluster(s): "
            + ",".join(str(Value) for Value in MissingLegalVariants)
        )
    if PackedMode and PackingPolicy.EnableJointClusterOrientation:
        JointCacheKey = BuildJointPlacementSearchCacheKey(
            Module,
            Clusters,
            BaseAssignment,
            PackingPolicy.JointPlacementBeamWidth,
            PackingPolicy.JointPlacementPassLimit,
            PackingPolicy.RetainedJointPlacementCandidates,
            AssignmentCut,
            AssignmentConstraints,
            EnableClusterInterfacePlacementFeasibility,
            FocusedJointOptimizationClusters,
            TopologyCutFrontier,
        )
        CachedJointSearch = _JointPlacementSearchCache.get(JointCacheKey)
        CachedState = (
            next(
                (
                    State
                    for State in CachedJointSearch["RetainedStates"]
                    if int(State["CandidateIndex"])
                    == JointPlacementCandidateIndex
                ),
                None,
            )
            if CachedJointSearch is not None
            else None
        )
        if CachedState is None:
            (
                Assignment,
                SelectedClusterVariants,
                JointPlacementDiagnostics,
            ) = OptimizeJointClusterPlacement(
                Module,
                Clusters,
                Levels,
                VariantsByCluster,
                PackingPolicy.JointPlacementBeamWidth,
                PackingPolicy.JointPlacementPassLimit,
                PackingPolicy.RetainedJointPlacementCandidates,
                JointPlacementCandidateIndex,
                InitialAssignment=BaseAssignment,
                FixedSlotClusters=frozenset(
                    RequiredRelocationPriority
                ),
                AssignmentCut=AssignmentCut,
                AssignmentConstraints=AssignmentConstraints,
                BoundaryContractCapacity=(
                    max(1, MaximumBoundaryTerminals or len(Clusters))
                    if RequiresStructuredJointRelocation
                    else 0
                ),
                EnableClusterInterfacePlacementFeasibility=(
                    EnableClusterInterfacePlacementFeasibility
                ),
                FocusedOptimizationClusters=(
                    FocusedJointOptimizationClusters
                    if FocusedJointOptimizationClusters
                    else None
                ),
                FrontierAssignmentCuts=TopologyCutFrontier,
                LogicalComponentByGate=LogicalComponentByGate,
                WorkCheck=WorkCheck,
            )
            _JointPlacementSearchCache[JointCacheKey] = deepcopy(
                JointPlacementDiagnostics
            )
        else:
            Assignment = {
                int(ClusterIndex): tuple(Slot)
                for ClusterIndex, Slot in dict(CachedState["Slots"]).items()
            }
            SelectedClusterVariants = {
                ClusterIndex: next(
                    Variant
                    for Variant in VariantsByCluster[ClusterIndex]
                    if (
                        Variant.Rotation,
                        Variant.MirrorX,
                    ) == (
                        int(
                            dict(CachedState["Transforms"])[
                                str(ClusterIndex)
                            ]["Rotation"]
                        ),
                        bool(
                            dict(CachedState["Transforms"])[
                                str(ClusterIndex)
                            ]["MirrorX"]
                        ),
                    )
                )
                for ClusterIndex in range(len(Clusters))
            }
            JointPlacementDiagnostics = deepcopy(CachedJointSearch)
            JointPlacementDiagnostics["SearchCacheHit"] = True
            JointPlacementDiagnostics["SelectedCandidateIndex"] = (
                JointPlacementCandidateIndex
            )
            JointPlacementDiagnostics["SelectedTransforms"] = deepcopy(
                CachedState["Transforms"]
            )
            JointPlacementDiagnostics["SelectedScore"] = CachedState.get(
                "SearchScore",
                JointPlacementDiagnostics.get("SelectedScore"),
            )
            JointPlacementDiagnostics[
                "SelectedExactPairAdjacencyViolations"
            ] = CachedState.get(
                "ExactPairAdjacencyViolations",
                JointPlacementDiagnostics.get(
                    "SelectedExactPairAdjacencyViolations",
                    0,
                ),
            )
            JointPlacementDiagnostics[
                "SelectedInterfacePairBankConflicts"
            ] = CachedState.get(
                "InterfacePairBankConflicts",
                JointPlacementDiagnostics.get(
                    "SelectedInterfacePairBankConflicts",
                    0,
                ),
            )
            JointPlacementDiagnostics[
                "SelectedHigherOrderBankPressure"
            ] = CachedState.get(
                "HigherOrderBankPressure",
                JointPlacementDiagnostics.get(
                    "SelectedHigherOrderBankPressure",
                    0,
                ),
            )
            for MetricName in (
                "HigherOrderPeakBankDemand",
                "HigherOrderBankExcessDemand",
                "HigherOrderOverloadedBankCount",
            ):
                JointPlacementDiagnostics[
                    f"Selected{MetricName}"
                ] = CachedState.get(
                    MetricName,
                    JointPlacementDiagnostics.get(
                        f"Selected{MetricName}",
                        0,
                    ),
                )
            JointPlacementDiagnostics[
                "SelectedObservedInterfaceBankConflicts"
            ] = CachedState.get(
                "ObservedInterfaceBankConflicts",
                JointPlacementDiagnostics.get(
                    "SelectedObservedInterfaceBankConflicts",
                    0,
                ),
            )
            JointPlacementDiagnostics[
                "SelectedInterfaceFacingMismatches"
            ] = CachedState.get(
                "InterfaceFacingMismatches",
                JointPlacementDiagnostics.get(
                    "SelectedInterfaceFacingMismatches",
                    0,
                ),
            )
            JointPlacementDiagnostics[
                "SelectedClusterInterfacePlacement"
            ] = deepcopy(CachedState.get(
                "ClusterInterfacePlacement",
                JointPlacementDiagnostics.get(
                    "SelectedClusterInterfacePlacement"
                ),
            ))
        JointPlacementDiagnostics["ClusterTransformVariants"] = (
            JointVariantDiagnostics
        )
        JointPlacementDiagnostics["AssignmentCut"] = (
            AssignmentCut.ToDictionary()
            if AssignmentCut is not None
            else None
        )
        JointPlacementDiagnostics["AssignmentConstraints"] = (
            AssignmentConstraints.ToDictionary()
        )
        JointPlacementDiagnostics["TopologyCutFrontier"] = [
            {
                "AssignmentCutFingerprint": Cut.ConflictFingerprint,
                "AssignmentCutWorkFingerprint": Cut.EffectiveWorkFingerprint,
            }
            for Cut in TopologyCutFrontier
        ]
        JointPlacementDiagnostics["FocusedOptimizationClusters"] = sorted(
            FocusedJointOptimizationClusters
        )
        ColumnCount = max(
            (Column for Column, _Row in Assignment.values()),
            default=-1,
        ) + 1
        for ClusterIndex, Variant in SelectedClusterVariants.items():
            LocalPositions.update(Variant.Positions)
            LocalRotations.update(Variant.Rotations)
            LocalMirrors.update(Variant.Mirrors)
            ClusterSizes[ClusterIndex] = (Variant.Width, Variant.Depth)
        # A feedback rerun is a new joint placement search. Do not mutate the
        # committed layout with an independent post-placement mirror.
        MirroredRelocationClusters = frozenset()
    else:
        Assignment = BaseAssignment
        SelectedClusterVariants = {
            ClusterIndex: Variants[0]
            for ClusterIndex, Variants in VariantsByCluster.items()
        }
        MirroredRelocationClusters = (
        frozenset(
            ClusterIndex
            for ClusterIndex in RelocationPriority
            if ClusterIndex not in PackedAccessRepairByCluster
        )
        if RelocationVariant > 0 and RelocationPriority
        else frozenset()
    )
    for ClusterIndex in RequiredRelocationPriority:
        ClusterStackIds[ClusterIndex] = None
        ClusterStackLevels[ClusterIndex] = 0
    ColumnWidths = {
        Column: max(
            (
                ClusterSizes[Index][0]
                for Index, Slot in Assignment.items()
                if Slot[0] == Column
            ),
            default=1,
        )
        for Column in range(ColumnCount)
    }
    RowDepths = {
        Row: max(
            (
                ClusterSizes[Index][1]
                for Index, Slot in Assignment.items()
                if Slot[1] == Row
            ),
            default=1,
        )
        for Row in range(max((Slot[1] for Slot in Assignment.values()), default=0) + 1)
    }
    GapPlan = BuildInterClusterGapPlan(
        BuildInterClusterBoundaryDemand(
            Module,
            Clusters,
            Assignment,
            WorkCheck=WorkCheck,
        ),
        ColumnCount=ColumnCount,
        RowCount=len(RowDepths),
        RoutingSpacing=RoutingSpacing,
        TrackPitch=(
            PlacementPolicy.DemandAwareBoundaryTrackPitch
            if (
                PlacementPolicy is not None
                and PlacementPolicy.DemandAwareBoundaryTrackPitch > 0
            )
            else DefaultRedstoneRoutingTechnology.TrackPitch
        ),
        Enabled=bool(
            PlacementPolicy is not None
            and PlacementPolicy.EnableDemandAwareInterClusterSpacing
        ),
    )
    ColumnExtraSpacing = GapPlan.ColumnSpacingByBoundary()
    RowExtraSpacing = GapPlan.RowSpacingByBoundary()
    ColumnOrigins: dict[int, int] = {}
    NextX = 0
    ColumnGap = 2 if PackedMode else 3
    RowGap = 1 if PackedMode else 2
    for Column in range(ColumnCount):
        ColumnOrigins[Column] = NextX
        NextX += ColumnWidths[Column]
        if Column + 1 < ColumnCount:
            NextX += ColumnGap + ColumnExtraSpacing[Column]
    RowOrigins: dict[int, int] = {}
    NextZ = 0
    for Row in sorted(RowDepths):
        RowOrigins[Row] = NextZ
        NextZ += RowDepths[Row]
        if Row + 1 < len(RowDepths):
            NextZ += RowGap + RowExtraSpacing[Row]
    ExactStatePlacementCacheKey: tuple[object, ...] | None = None
    ExactStatePlacementCacheFingerprint = ""
    CachedExactStateGeometry: tuple[
        ExactStatePlacedGateGeometry, ...
    ] | None = None
    CachedExactStateCoreGeometry: tuple[
        ExactStatePlacedGateGeometry, ...
    ] | None = None
    SelectedExactMandatoryAccessProfile: (
        MandatoryAccessConflictProfile | None
    ) = None
    if PackedMode and PackingPolicy.EnableJointClusterOrientation:
        # The beam's local template test is necessary but not sufficient: two
        # exact variants can still contend for an electrical/access resource
        # after their selected slots are materialized.  Screen every retained
        # joint state with the same placed-gate conflict predicate used by the
        # final commit.  Cache the completed six-state screen transactionally,
        # but retain both legal and rejected states for failure diagnostics.
        VariantByTransform = {
            ClusterIndex: {
                (Variant.Rotation, Variant.MirrorX): Variant
                for Variant in Variants
            }
            for ClusterIndex, Variants in VariantsByCluster.items()
        }
        ExactScreenTrackPitch = (
            PlacementPolicy.DemandAwareBoundaryTrackPitch
            if (
                PlacementPolicy is not None
                and PlacementPolicy.DemandAwareBoundaryTrackPitch > 0
            )
            else DefaultRedstoneRoutingTechnology.TrackPitch
        )
        ExactScreenDemandSpacing = bool(
            PlacementPolicy is not None
            and PlacementPolicy.EnableDemandAwareInterClusterSpacing
        )
        RawRetainedStates = tuple(
            deepcopy(State)
            for State in JointPlacementDiagnostics.get("RetainedStates", ())
        )
        ExactScreenCacheKey = (
            ModuleLayoutFingerprint,
            tuple(tuple(Names) for Names in Clusters),
            RoutingSpacing,
            ColumnGap,
            RowGap,
            PackingPolicy.ClusterDeckPitch,
            ExactScreenTrackPitch,
            ExactScreenDemandSpacing,
            tuple(sorted(ClusterStackLevels.items())),
            tuple(
                (
                    ClusterIndex,
                    tuple(
                        (
                            Variant.Rotation,
                            Variant.MirrorX,
                            Variant.Width,
                            Variant.Depth,
                            tuple(sorted(Variant.Positions.items())),
                            tuple(sorted(Variant.Rotations.items())),
                            tuple(sorted(Variant.Mirrors.items())),
                        )
                        for Variant in Variants
                    ),
                )
                for ClusterIndex, Variants in sorted(
                    VariantsByCluster.items()
                )
            ),
            tuple(
                (
                    int(State["CandidateIndex"]),
                    State.get("SearchScore"),
                    tuple(sorted(
                        (
                            int(ClusterIndex),
                            tuple(Slot),
                        )
                        for ClusterIndex, Slot in dict(
                            State["Slots"]
                        ).items()
                    )),
                    tuple(sorted(
                        (
                            int(ClusterIndex),
                            int(Transform["Rotation"]),
                            bool(Transform["MirrorX"]),
                        )
                        for ClusterIndex, Transform in dict(
                            State["Transforms"]
                        ).items()
                    )),
                )
                for State in RawRetainedStates
            ),
        )

        def FindExactStateConflict(
            State: dict[str, object],
            StateOrdinal: int,
            StateCount: int,
        ) -> tuple[
            dict[int, ClusterLayoutVariant],
            tuple[Any, Any] | None,
            dict[str, dict[int, int]],
            dict[int, tuple[int, int]],
            tuple[dict[str, object], ...],
            tuple[ExactStatePlacedGateGeometry, ...],
        ]:
            StateSlots = {
                int(Index): tuple(Slot)
                for Index, Slot in dict(State["Slots"]).items()
            }
            StateVariants = {
                ClusterIndex: VariantByTransform[ClusterIndex][(
                    int(dict(State["Transforms"])[str(ClusterIndex)]["Rotation"]),
                    bool(dict(State["Transforms"])[str(ClusterIndex)]["MirrorX"]),
                )]
                for ClusterIndex in range(len(Clusters))
            }

            def BuildStateGeometry() -> tuple[
                int,
                int,
                dict[int, int],
                dict[int, int],
                dict[int, int],
                dict[int, int],
            ]:
                StateColumnCount = max(
                    (Slot[0] for Slot in StateSlots.values()),
                    default=-1,
                ) + 1
                StateRowCount = max(
                    (Slot[1] for Slot in StateSlots.values()),
                    default=-1,
                ) + 1
                StateColumnWidths = {
                    Column: max(
                        (
                            StateVariants[Index].Width
                            for Index, Slot in StateSlots.items()
                            if Slot[0] == Column
                        ),
                        default=1,
                    )
                    for Column in range(StateColumnCount)
                }
                StateRowDepths = {
                    Row: max(
                        (
                            StateVariants[Index].Depth
                            for Index, Slot in StateSlots.items()
                            if Slot[1] == Row
                        ),
                        default=1,
                    )
                    for Row in range(StateRowCount)
                }
                StateGapPlan = BuildInterClusterGapPlan(
                    BuildInterClusterBoundaryDemand(
                        Module,
                        Clusters,
                        StateSlots,
                        WorkCheck=WorkCheck,
                    ),
                    ColumnCount=StateColumnCount,
                    RowCount=StateRowCount,
                    RoutingSpacing=RoutingSpacing,
                    TrackPitch=ExactScreenTrackPitch,
                    Enabled=ExactScreenDemandSpacing,
                )
                return (
                    StateColumnCount,
                    StateRowCount,
                    StateColumnWidths,
                    StateRowDepths,
                    StateGapPlan.ColumnSpacingByBoundary(),
                    StateGapPlan.RowSpacingByBoundary(),
                )

            (
                StateColumnCount,
                StateRowCount,
                StateColumnWidths,
                StateRowDepths,
                StateColumnExtra,
                StateRowExtra,
            ) = BuildStateGeometry()

            def BuildStateGates() -> list[tuple[int, Any]]:
                StateColumnOrigins: dict[int, int] = {}
                NextStateX = 0
                for Column in range(StateColumnCount):
                    StateColumnOrigins[Column] = NextStateX
                    NextStateX += StateColumnWidths[Column]
                    if Column + 1 < StateColumnCount:
                        NextStateX += ColumnGap + StateColumnExtra[Column]
                StateRowOrigins: dict[int, int] = {}
                NextStateZ = 0
                for Row in range(StateRowCount):
                    StateRowOrigins[Row] = NextStateZ
                    NextStateZ += StateRowDepths[Row]
                    if Row + 1 < StateRowCount:
                        NextStateZ += RowGap + StateRowExtra[Row]
                StateGates: list[tuple[int, Any]] = []
                for ClusterIndex, Names in enumerate(Clusters):
                    SlotX, SlotZ = StateSlots[ClusterIndex]
                    Variant = StateVariants[ClusterIndex]
                    for Name in Names:
                        LocalX, LocalZ = Variant.Positions[Name]
                        StateGates.append((ClusterIndex, BuildPlacedGate(
                            InternalByName[Name],
                            StateColumnOrigins[SlotX] + LocalX,
                            1 + ClusterStackLevels[ClusterIndex]
                            * PackingPolicy.ClusterDeckPitch,
                            StateRowOrigins[SlotZ] + LocalZ,
                            Variant.Rotations[Name],
                            Variant.Mirrors[Name],
                        )))
                return StateGates

            # Add only the boundary clearance required by a concrete conflict.
            # This is part of the candidate's joint slot/orientation state;
            # it never changes a NAND's template geometry or rotates it after
            # placement.  A hard bound keeps pre-screening deterministic.
            PairChecks = 0
            LastConflict: tuple[int, Any, int, Any] | None = None
            ExactSlotRepairs: list[dict[str, object]] = []
            for Attempt in range(32):
                CheckWork(
                    "joint-exact-screen-clearance",
                    CandidateIndex=State["CandidateIndex"],
                    CandidateOrdinal=StateOrdinal,
                    CandidateCount=StateCount,
                    Attempt=Attempt,
                    PairChecks=PairChecks,
                )
                StateGates = BuildStateGates()
                Conflict = None
                for GateIndex, (FirstCluster, First) in enumerate(StateGates):
                    for SecondCluster, Second in StateGates[GateIndex + 1 :]:
                        if FirstCluster == SecondCluster:
                            continue
                        PairChecks += 1
                        if PairChecks % 128 == 0:
                            CheckWork(
                                "joint-exact-screen-pairs",
                                CandidateIndex=State["CandidateIndex"],
                                CandidateOrdinal=StateOrdinal,
                                CandidateCount=StateCount,
                                Attempt=Attempt,
                                PairChecks=PairChecks,
                            )
                        if PcbGatesConflict(First, Second):
                            Conflict = (
                                FirstCluster,
                                First,
                                SecondCluster,
                                Second,
                            )
                            break
                    if Conflict is not None:
                        break
                if Conflict is None:
                    return (
                        StateVariants,
                        None,
                        {
                            "Columns": dict(StateColumnExtra),
                            "Rows": dict(StateRowExtra),
                        },
                        dict(StateSlots),
                        tuple(ExactSlotRepairs),
                        tuple(
                            ExactStatePlacedGateGeometry.FromPlacedGate(Gate)
                            for _ClusterIndex, Gate in StateGates
                        ),
                    )
                LastConflict = Conflict
                FirstCluster, First, SecondCluster, Second = Conflict
                FirstSlot = StateSlots[FirstCluster]
                SecondSlot = StateSlots[SecondCluster]
                if FirstSlot[0] != SecondSlot[0]:
                    StateColumnExtra[min(FirstSlot[0], SecondSlot[0])] += 1
                    continue
                if FirstSlot[1] != SecondSlot[1]:
                    StateRowExtra[min(FirstSlot[1], SecondSlot[1])] += 1
                    continue
                # The retained beam state can stack two clusters in one slot
                # even though their final NAND templates contend on the same
                # deck.  Diversify only that exact conflicting pair by moving
                # one cluster into a bounded dedicated column.  This preserves
                # every transform and leaves unrelated cluster slots intact.
                RelocatedCluster = max(FirstCluster, SecondCluster)
                PreviousSlot = StateSlots[RelocatedCluster]
                NewSlot = (
                    max(
                        (Slot[0] for Slot in StateSlots.values()),
                        default=-1,
                    ) + 1,
                    PreviousSlot[1],
                )
                StateSlots[RelocatedCluster] = NewSlot
                ExactSlotRepairs.append({
                    "Attempt": Attempt,
                    "RelocatedCluster": RelocatedCluster,
                    "FromSlot": list(PreviousSlot),
                    "ToSlot": list(NewSlot),
                    "ConflictClusters": sorted(
                        (FirstCluster, SecondCluster)
                    ),
                    "ConflictMembers": [First.Name, Second.Name],
                })
                (
                    StateColumnCount,
                    StateRowCount,
                    StateColumnWidths,
                    StateRowDepths,
                    StateColumnExtra,
                    StateRowExtra,
                ) = BuildStateGeometry()
                continue
            if LastConflict is None:
                raise AssertionError(
                    "Exact joint screen exhausted without a conflict record"
                )
            _FirstCluster, First, _SecondCluster, Second = LastConflict
            return (
                StateVariants,
                (First, Second),
                {
                    "Columns": dict(StateColumnExtra),
                    "Rows": dict(StateRowExtra),
                },
                dict(StateSlots),
                tuple(ExactSlotRepairs),
                (),
            )

        CachedExactScreen = _JointPlacementExactScreenCache.get(
            ExactScreenCacheKey
        )
        ExactScreenCacheHit = CachedExactScreen is not None
        if CachedExactScreen is None:
            ScreenedRetainedStates: list[dict[str, object]] = []
            CoreGeometryByCandidate: list[
                tuple[int, tuple[ExactStatePlacedGateGeometry, ...]]
            ] = []
            for StateOrdinal, State in enumerate(
                RawRetainedStates,
                start=1,
            ):
                CheckWork(
                    "joint-exact-screen-state",
                    CandidateIndex=State["CandidateIndex"],
                    CandidateOrdinal=StateOrdinal,
                    CandidateCount=len(RawRetainedStates),
                )
                (
                    _StateVariants,
                    Conflict,
                    ExactSpacing,
                    ExactSlots,
                    ExactSlotRepairs,
                    ExactCoreGeometry,
                ) = (
                    FindExactStateConflict(
                        State,
                        StateOrdinal,
                        len(RawRetainedStates),
                    )
                )
                if Conflict is None:
                    CoreGeometryByCandidate.append((
                        int(State["CandidateIndex"]),
                        ExactCoreGeometry,
                    ))
                    ScreenedRetainedStates.append({
                        **State,
                        "Slots": ExactSlots,
                        "ExactLegal": True,
                        "ExactSpacing": ExactSpacing,
                        "ExactSlotRepairs": ExactSlotRepairs,
                    })
                    continue
                First, Second = Conflict
                Rejection = {
                    "CandidateIndex": State["CandidateIndex"],
                    "Reason": "PcbGatesConflict",
                    "Members": [First.Name, Second.Name],
                    "Resource": [First.X, First.Y, First.Z],
                    "Transforms": State["Transforms"],
                    "Slots": ExactSlots,
                    "ExactSlotRepairs": ExactSlotRepairs,
                }
                ScreenedRetainedStates.append({
                    **State,
                    "Slots": ExactSlots,
                    "ExactLegal": False,
                    "ExactSpacing": ExactSpacing,
                    "ExactSlotRepairs": ExactSlotRepairs,
                    "ExactRejection": Rejection,
                })
            # Publish only after every retained state completed. A deadline or
            # other exception cannot leak a partial exact-screen cache entry.
            CachedExactScreen = ExactJointPlacementScreen(
                RetainedStates=tuple(deepcopy(ScreenedRetainedStates)),
                CoreGeometryByCandidate=tuple(
                    sorted(CoreGeometryByCandidate)
                ),
            )
            _JointPlacementExactScreenCache[
                ExactScreenCacheKey
            ] = CachedExactScreen
        else:
            ScreenedRetainedStates = list(
                deepcopy(CachedExactScreen.RetainedStates)
            )
            CheckWork(
                "joint-exact-screen-cache-hit",
                CandidateCount=len(ScreenedRetainedStates),
            )
        if (
            MandatoryAccessPreScreenOnly
            and not CachedExactScreen.MandatoryProfileByCandidate
        ):
            ModuleGateByNameForMandatoryScreen = {
                Gate.Name: Gate for Gate in Module.Gates
            }
            MandatoryScreenSignalOrder = tuple(sorted({
                *Module.Inputs,
                *Module.Outputs,
                *(
                    Signal
                    for Gate in Module.Gates
                    for Signal in (*Gate.Inputs, *Gate.Outputs)
                ),
            }))
            ProfilesBySearchCandidate: dict[
                int,
                MandatoryAccessConflictProfile,
            ] = {}
            for State in ScreenedRetainedStates:
                if not bool(State.get("ExactLegal")):
                    continue
                SearchCandidateIndex = int(State["CandidateIndex"])
                ExactCoreGeometry = CachedExactScreen.CoreGeometry(
                    SearchCandidateIndex
                )
                if ExactCoreGeometry is None:
                    continue
                CheckWork(
                    "joint-exact-mandatory-access-screen",
                    CandidateIndex=SearchCandidateIndex,
                    ScreenedCandidateCount=len(
                        ProfilesBySearchCandidate
                    ),
                )

                def CheckMandatoryAccessScreen(
                    Diagnostics: dict[str, object],
                ) -> None:
                    CheckWork(
                        str(Diagnostics.get(
                            "Phase",
                            "joint-exact-mandatory-access-profile",
                        )),
                        **{
                            Key: Value
                            for Key, Value in Diagnostics.items()
                            if Key != "Phase"
                        },
                    )

                Profile = MeasureMandatoryAccessConflictProfile(
                    (
                        Geometry.BuildPlacedGate(
                            ModuleGateByNameForMandatoryScreen[Geometry.Name]
                        )
                        for Geometry in ExactCoreGeometry
                    ),
                    MandatoryScreenSignalOrder,
                    WorkCheck=CheckMandatoryAccessScreen,
                )
                ProfilesBySearchCandidate[
                    SearchCandidateIndex
                ] = Profile
                if (
                    not EnableClusterInterfacePlacementFeasibility
                    and not Profile.HasConflicts
                ):
                    break
            if EnableClusterInterfacePlacementFeasibility:
                (
                    OrderedRetainedStates,
                    InterfacePortfolioAttrition,
                ) = SelectExactInterfaceCommitStates(
                    ScreenedRetainedStates,
                    ProfilesBySearchCandidate,
                    min(
                        len(ScreenedRetainedStates),
                        PackingPolicy.RetainedJointPlacementCandidates * 2,
                    ),
                )
            else:
                OrderedRetainedStates = (
                    OrderExactStatesForMandatoryAccessCommit(
                        ScreenedRetainedStates,
                        ProfilesBySearchCandidate,
                    )
                )
                InterfacePortfolioAttrition = ()
            OrderedCoreGeometryByCandidate = tuple(
                (
                    int(State["CandidateIndex"]),
                    Geometry,
                )
                for State in OrderedRetainedStates
                if (
                    Geometry := CachedExactScreen.CoreGeometry(
                        int(State["SearchCandidateIndex"])
                    )
                ) is not None
            )
            OrderedMandatoryProfilesByCandidate = tuple(
                (
                    int(State["CandidateIndex"]),
                    ProfilesBySearchCandidate[
                        int(State["SearchCandidateIndex"])
                    ],
                )
                for State in OrderedRetainedStates
                if int(State["SearchCandidateIndex"])
                in ProfilesBySearchCandidate
            )
            CachedExactScreen = ExactJointPlacementScreen(
                RetainedStates=tuple(deepcopy(OrderedRetainedStates)),
                CoreGeometryByCandidate=(
                    OrderedCoreGeometryByCandidate
                ),
                MandatoryProfileByCandidate=(
                    OrderedMandatoryProfilesByCandidate
                ),
            )
            _JointPlacementExactScreenCache[
                ExactScreenCacheKey
            ] = CachedExactScreen
            ScreenedRetainedStates = list(
                deepcopy(CachedExactScreen.RetainedStates)
            )
            JointPlacementDiagnostics[
                "InterfacePortfolioAttrition"
            ] = list(InterfacePortfolioAttrition)
            CheckWork(
                "joint-exact-mandatory-access-order-complete",
                ScreenedCandidateCount=len(
                    ProfilesBySearchCandidate
                ),
                PromotedSearchCandidateIndex=(
                    int(ScreenedRetainedStates[0].get(
                        "SearchCandidateIndex",
                        ScreenedRetainedStates[0]["CandidateIndex"],
                    ))
                    if ScreenedRetainedStates
                    else -1
                ),
            )
        ExactLegalRetainedStates = [
            State
            for State in ScreenedRetainedStates
            if bool(State.get("ExactLegal"))
        ]
        ExactRejections = [
            deepcopy(State["ExactRejection"])
            for State in ScreenedRetainedStates
            if not bool(State.get("ExactLegal"))
            and "ExactRejection" in State
        ]
        JointPlacementDiagnostics["RetainedStates"] = (
            ScreenedRetainedStates
        )
        JointPlacementDiagnostics["ExactLegalRetainedStates"] = (
            ExactLegalRetainedStates
        )
        JointPlacementDiagnostics["ExactCandidateRejections"] = (
            ExactRejections
        )
        JointPlacementDiagnostics["ExactScreenCacheHit"] = (
            ExactScreenCacheHit
        )
        ExactScreenFingerprint = sha256(
            repr(ExactScreenCacheKey).encode("utf-8")
        ).hexdigest()
        JointPlacementDiagnostics["ExactScreenFingerprint"] = (
            ExactScreenFingerprint
        )
        SelectedExactState = next(
            (
                State for State in ScreenedRetainedStates
                if int(State["CandidateIndex"])
                == JointPlacementCandidateIndex
            ),
            None,
        )
        if (
            SelectedExactState is not None
            and bool(SelectedExactState.get("ExactLegal"))
        ):
            SelectedExactMandatoryAccessProfile = (
                CachedExactScreen.MandatoryProfile(
                    JointPlacementCandidateIndex
                )
            )
            CachedExactStateCoreGeometry = (
                CachedExactScreen.CoreGeometry(
                    JointPlacementCandidateIndex
                )
            )
            JointPlacementDiagnostics[
                "SelectedSearchCandidateIndex"
            ] = int(SelectedExactState.get(
                "SearchCandidateIndex",
                JointPlacementCandidateIndex,
            ))
            JointPlacementDiagnostics["SelectedScore"] = (
                SelectedExactState.get(
                    "SearchScore",
                    JointPlacementDiagnostics.get("SelectedScore"),
                )
            )
            JointPlacementDiagnostics["SelectedTransforms"] = deepcopy(
                SelectedExactState.get("Transforms", {})
            )
            SelectedExactStateFingerprint = sha256(
                repr((
                    JointPlacementCandidateIndex,
                    SelectedExactState,
                )).encode("utf-8")
            ).hexdigest()
            ExactStatePlacementCacheKey = (
                ExactScreenFingerprint,
                JointPlacementCandidateIndex,
                SelectedExactStateFingerprint,
                repr(PlacementPolicy),
                repr(PackingPolicy),
                bool(MandatoryAccessPreScreenOnly),
                bool(PreferAccessRingTerminals),
                bool(UseDerivedPerimeterTerminals),
                int(DerivedTerminalLayoutVariantIndex),
                RelocationVariant,
                tuple(sorted(RelocationSignals)),
                tuple(sorted(RelocationPrioritySignals)),
                tuple(sorted(RequiredRelocationSignals)),
                tuple(sorted(
                    CoordinatedCandidateDiversificationSignals
                )),
                (
                    (
                        AssignmentCut.ConflictFingerprint,
                        AssignmentCut.EffectiveWorkFingerprint,
                    )
                    if AssignmentCut is not None
                    else ("", "")
                ),
                AssignmentConstraints.Fingerprint,
            )
            ExactStatePlacementCacheFingerprint = sha256(
                repr(ExactStatePlacementCacheKey).encode("utf-8")
            ).hexdigest()
            # A cached full placement includes its historical terminal bank.
            # Derived perimeter slots must instead be rebuilt from the cached
            # NAND core so their immutable pre-route domain is visible on
            # every placement construction.
            CachedExactStateGeometry = (
                None
                if UseDerivedPerimeterTerminals
                else _ExactStatePlacementGeometryCache.get(
                    ExactStatePlacementCacheKey
                )
            )
            JointPlacementDiagnostics[
                "ExactStatePlacementCache"
            ] = {
                "Key": ExactStatePlacementCacheFingerprint,
                "Hit": CachedExactStateGeometry is not None,
                "CandidateIndex": JointPlacementCandidateIndex,
                "StateFingerprint": SelectedExactStateFingerprint,
                "CachedGateCount": (
                    len(CachedExactStateGeometry)
                    if CachedExactStateGeometry is not None
                    else 0
                ),
                "CoreGeometryAvailable": (
                    CachedExactStateCoreGeometry is not None
                ),
                "CoreGeometryCacheHit": (
                    ExactScreenCacheHit
                    and CachedExactStateCoreGeometry is not None
                ),
                "CoreGateCount": (
                    len(CachedExactStateCoreGeometry)
                    if CachedExactStateCoreGeometry is not None
                    else 0
                ),
            }
            Assignment = {
                int(ClusterIndex): tuple(Slot)
                for ClusterIndex, Slot in dict(
                    SelectedExactState["Slots"]
                ).items()
            }
            SelectedTransforms = dict(
                SelectedExactState["Transforms"]
            )
            SelectedClusterVariants = {
                ClusterIndex: VariantByTransform[ClusterIndex][(
                    int(
                        SelectedTransforms[str(ClusterIndex)][
                            "Rotation"
                        ]
                    ),
                    bool(
                        SelectedTransforms[str(ClusterIndex)][
                            "MirrorX"
                        ]
                    ),
                )]
                for ClusterIndex in range(len(Clusters))
            }
            for ClusterIndex, Variant in (
                SelectedClusterVariants.items()
            ):
                LocalPositions.update(Variant.Positions)
                LocalRotations.update(Variant.Rotations)
                LocalMirrors.update(Variant.Mirrors)
                ClusterSizes[ClusterIndex] = (
                    Variant.Width,
                    Variant.Depth,
                )
            ColumnCount = max(
                (Column for Column, _Row in Assignment.values()),
                default=-1,
            ) + 1
            RowCount = max(
                (Row for _Column, Row in Assignment.values()),
                default=-1,
            ) + 1
            ColumnWidths = {
                Column: max(
                    (
                        ClusterSizes[Index][0]
                        for Index, Slot in Assignment.items()
                        if Slot[0] == Column
                    ),
                    default=1,
                )
                for Column in range(ColumnCount)
            }
            RowDepths = {
                Row: max(
                    (
                        ClusterSizes[Index][1]
                        for Index, Slot in Assignment.items()
                        if Slot[1] == Row
                    ),
                    default=1,
                )
                for Row in range(RowCount)
            }
            SelectedExactSpacing = dict(SelectedExactState["ExactSpacing"])
            ColumnExtraSpacing = dict(SelectedExactSpacing["Columns"])
            RowExtraSpacing = dict(SelectedExactSpacing["Rows"])
            GapPlan = InterClusterGapPlan(
                Enabled=ExactScreenDemandSpacing,
                RoutingSpacing=RoutingSpacing,
                TrackPitch=ExactScreenTrackPitch,
                ColumnExtraSpacing=tuple(sorted(
                    ColumnExtraSpacing.items()
                )),
                RowExtraSpacing=tuple(sorted(
                    RowExtraSpacing.items()
                )),
                BoundaryDemand=BuildInterClusterBoundaryDemand(
                    Module,
                    Clusters,
                    Assignment,
                    WorkCheck=WorkCheck,
                ),
            )
            JointPlacementDiagnostics["SelectedSlots"] = {
                str(ClusterIndex): list(Slot)
                for ClusterIndex, Slot in sorted(Assignment.items())
            }
            JointPlacementDiagnostics["SelectedExactSlotRepairs"] = (
                deepcopy(
                    SelectedExactState.get(
                        "ExactSlotRepairs",
                        (),
                    )
                )
            )
            ColumnOrigins = {}
            NextX = 0
            for Column in range(ColumnCount):
                ColumnOrigins[Column] = NextX
                NextX += ColumnWidths[Column]
                if Column + 1 < ColumnCount:
                    NextX += ColumnGap + ColumnExtraSpacing[Column]
            RowOrigins = {}
            NextZ = 0
            for Row in sorted(RowDepths):
                RowOrigins[Row] = NextZ
                NextZ += RowDepths[Row]
                if Row + 1 < len(RowDepths):
                    NextZ += RowGap + RowExtraSpacing[Row]
        if (
            SelectedExactState is None
            or not bool(SelectedExactState.get("ExactLegal"))
        ):
            Rejection = (
                SelectedExactState.get(
                    "ExactRejection",
                    {"Reason": "not retained"},
                )
                if SelectedExactState is not None
                else {"Reason": "not retained"}
            )
            raise ValueError(
                "Exact joint placement candidate rejected: "
                f"{Rejection}"
            )
    InputMargin = 0
    ModuleGateByName = {
        Gate.Name: Gate for Gate in Module.Gates
    }
    if CachedExactStateGeometry is not None:
        CheckWork(
            "exact-state-placement-cache-hit",
            CandidateIndex=JointPlacementCandidateIndex,
            CachedGateCount=len(CachedExactStateGeometry),
        )
    elif CachedExactStateCoreGeometry is not None:
        CheckWork(
            "exact-state-core-geometry-reused",
            CandidateIndex=JointPlacementCandidateIndex,
            CachedGateCount=len(CachedExactStateCoreGeometry),
            ExactScreenCacheHit=ExactScreenCacheHit,
        )
    SelectedPlacedGateGeometry = (
        CachedExactStateGeometry
        if CachedExactStateGeometry is not None
        else CachedExactStateCoreGeometry
    )
    PlacedGates = (
        [
            Geometry.BuildPlacedGate(
                ModuleGateByName[Geometry.Name]
            )
            for Geometry in SelectedPlacedGateGeometry
        ]
        if SelectedPlacedGateGeometry is not None
        else []
    )
    if SelectedPlacedGateGeometry is None:
        for ClusterIndex, Names in enumerate(Clusters):
            CheckWork(
                "placement-commit",
                CompletedClusters=ClusterIndex,
                TotalClusters=len(Clusters),
            )
            SlotX, SlotZ = Assignment[ClusterIndex]
            BaseX = InputMargin + ColumnOrigins[SlotX]
            BaseZ = RowOrigins[SlotZ]
            BaseY = 1 + (
                ClusterStackLevels[ClusterIndex]
                * PackingPolicy.ClusterDeckPitch
                if PackedMode
                else 0
            )
            CandidateClusterGates = []
            for Name in Names:
                LocalX, LocalZ = LocalPositions[Name]
                Rotation = LocalRotations[Name]
                MirrorX = LocalMirrors.get(Name, False)
                if ClusterIndex in MirroredRelocationClusters:
                    GateWidth = RotatedCellSize(
                        InternalByName[Name].Kind.value,
                        Rotation,
                    )[0]
                    LocalX = (
                        ClusterSizes[ClusterIndex][0]
                        - LocalX
                        - GateWidth
                    )
                    MirrorX = not MirrorX
                CandidateClusterGates.append(
                    BuildPlacedGate(
                        InternalByName[Name],
                        BaseX + LocalX,
                        BaseY,
                        BaseZ + LocalZ,
                        Rotation,
                        MirrorX,
                    )
                )
            if PackedMode and (
                any(
                    PcbGatesConflict(Candidate, Existing)
                    for Candidate in CandidateClusterGates
                    for Existing in PlacedGates
                )
                or any(
                    PcbGatesConflict(First, Second)
                    for Index, First in enumerate(
                        CandidateClusterGates
                    )
                    for Second in CandidateClusterGates[Index + 1 :]
                )
            ):
                raise ValueError(
                    "Packed NAND cluster "
                    f"{ClusterIndex} conflicts at placement commit"
                )
            PlacedGates.extend(CandidateClusterGates)

    InputGates = [Gate for Gate in Module.Gates if Gate.Kind.value == "INPUT"]
    OutputGates = [Gate for Gate in Module.Gates if Gate.Kind.value == "OUTPUT"]

    ClusterByGate = (
        {
            Name: ClusterIndex
            for ClusterIndex, Names in enumerate(Clusters)
            for Name in Names
        }
        if PackedMode
        else {}
    )
    TerminalConsumers: dict[str, list[Any]] = {}
    for ModuleGate in Module.Gates:
        for Signal in ModuleGate.Inputs:
            TerminalConsumers.setdefault(Signal, []).append(ModuleGate)

    InternalMinimumX = min(Gate.X for Gate in PlacedGates)
    InternalMaximumX = max(
        Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0] - 1
        for Gate in PlacedGates
    )
    InternalMinimumZ = min(Gate.Z for Gate in PlacedGates)
    InternalMaximumZ = max(
        Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1] - 1
        for Gate in PlacedGates
    )
    UseDerivedSingleComponentPlacement = (
        PackedMode
        and len(Clusters) == 1
        and UseDerivedPerimeterTerminals
    )
    DerivedPerimeterSlotDomainValue: DerivedPerimeterSlotDomain | None = None
    DerivedPerimeterSlotAssignmentValue: (
        DerivedPerimeterSlotAssignment | None
    ) = None
    def PlaceTerminalBank(
        Gates: list[Any],
        BankZ: int,
        OutwardStep: int,
        PortNames: list[str],
        LocalizeByInternalPins: bool = False,
    ) -> None:
        """Place a legal terminal bank, optionally ordered by internal pins."""
        PortIndexes = {
            Signal: Index
            for Index, Signal in enumerate(PortNames)
        }

        def TerminalSignal(Gate: Any) -> str:
            return (
                Gate.Outputs[0]
                if Gate.Kind.value == "INPUT"
                else Gate.Inputs[0]
            )

        InternalPinsBySignal: dict[str, list[tuple[int, int, int]]] = {}
        InternalOutputsBySignal: dict[str, tuple[int, int, int]] = {}
        if LocalizeByInternalPins:
            for Existing in PlacedGates:
                for InputIndex, Signal in enumerate(Existing.Inputs):
                    InternalPinsBySignal.setdefault(Signal, []).append(
                        Existing.InputPins[InputIndex]
                    )
                if Existing.OutputPin is not None:
                    for Signal in Existing.Outputs:
                        InternalOutputsBySignal[Signal] = Existing.OutputPin

        def TerminalAnchorX(Gate: Any) -> int:
            Signal = TerminalSignal(Gate)
            Pins = (
                InternalPinsBySignal.get(Signal, ())
                if Gate.Kind.value == "INPUT"
                else (
                    (InternalOutputsBySignal[Signal],)
                    if Signal in InternalOutputsBySignal
                    else ()
                )
            )
            if not Pins:
                return PortIndexes[Signal]
            Values = sorted(Pin[0] for Pin in Pins)
            return Values[(len(Values) - 1) // 2]

        Ordered = sorted(
            Gates,
            key=lambda Gate: (
                (
                    TerminalAnchorX(Gate)
                    if LocalizeByInternalPins
                    else PortIndexes[TerminalSignal(Gate)]
                ),
                PortIndexes[TerminalSignal(Gate)],
                Gate.Name,
            ),
        )
        if LocalizeByInternalPins and Ordered:
            AnchorXs = [TerminalAnchorX(Gate) for Gate in Ordered]
            CenterX = (min(AnchorXs) + max(AnchorXs)) // 2
            LocalizedSpacing = max(
                3 + RoutingSpacing,
                ceil(
                    (max(AnchorXs) - min(AnchorXs))
                    / max(1, len(Ordered) - 1)
                ),
            )
            TerminalSpacings = (
                LocalizedSpacing,
                LocalizedSpacing + 1,
            )
        else:
            CenterX = (InternalMinimumX + InternalMaximumX) // 2
            TerminalSpacings = (
                (4 + RoutingSpacing, 3 + RoutingSpacing)
                if (
                    PackedMode
                    and PlacementPolicy is not None
                    and PlacementPolicy.PreferWideTerminalBanks
                )
                else (2, 3)
                if PackedMode
                else
                (4 + RoutingSpacing, 3 + RoutingSpacing)
                if PlacementPolicy is not None
                and PlacementPolicy.PreferWideTerminalBanks
                else (3 + RoutingSpacing, 4 + RoutingSpacing)
            )
        for Spacing in TerminalSpacings:
            CheckWork("terminal-bank-spacing", Spacing=Spacing)
            BankWidth = max(1, 1 + Spacing * (len(Ordered) - 1))
            StartX = CenterX - BankWidth // 2 + (
                PlacementPolicy.TerminalBankOffsetX
                if PlacementPolicy is not None
                and Ordered
                and Ordered[0].Kind.value == "INPUT"
                else 0
            )
            for Setback in range(32):
                CheckWork(
                    "terminal-bank-setback",
                    Spacing=Spacing,
                    Setback=Setback,
                )
                CandidateZ = BankZ + Setback * OutwardStep
                Terminals = [
                    BuildPlacedGate(
                        Gate,
                        StartX + Index * Spacing,
                        1,
                        CandidateZ,
                        0,
                        False,
                    )
                    for Index, Gate in enumerate(Ordered)
                ]
                ConflictsWithPlacement = any(
                    PcbGatesConflict(Terminal, Existing)
                    for Terminal in Terminals
                    for Existing in PlacedGates
                )
                ConflictsWithinBank = any(
                    PcbGatesConflict(First, Second)
                    for Index, First in enumerate(Terminals)
                    for Second in Terminals[Index + 1 :]
                )
                if ConflictsWithPlacement or ConflictsWithinBank:
                    continue
                PlacedGates.extend(Terminals)
                return
        raise ValueError("Could not place grouped terminal bank legally")

    def PlaceLocalizedTerminals(
        Gates: list[Any],
        PortIndexes: dict[str, int],
    ) -> list[Any] | None:
        """Place packed-mode I/O on the exterior shell of the NAND fabric."""
        nonlocal DerivedPerimeterSlotDomainValue
        nonlocal DerivedPerimeterSlotAssignmentValue
        PlacedMinimumX = min(Gate.X for Gate in PlacedGates)
        PlacedMaximumX = max(
            Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0]
            for Gate in PlacedGates
        )
        PlacedMinimumZ = min(Gate.Z for Gate in PlacedGates)
        PlacedMaximumZ = max(
            Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1]
            for Gate in PlacedGates
        )
        Producers = {
            Signal: Gate
            for Gate in PlacedGates
            if Gate.OutputPin is not None
            for Signal in Gate.Outputs
        }
        Targets: dict[str, list[tuple[int, int, int]]] = {}
        for Existing in PlacedGates:
            for InputIndex, Signal in enumerate(Existing.Inputs):
                Targets.setdefault(Signal, []).append(Existing.InputPins[InputIndex])

        def TerminalKind(Gate: Any) -> str:
            Kind = getattr(Gate, "Kind", "")
            return str(getattr(Kind, "value", Kind))

        def TerminalSignal(Gate: Any) -> str:
            return (
                Gate.Outputs[0]
                if TerminalKind(Gate) == "INPUT"
                else Gate.Inputs[0]
            )

        if UseDerivedSingleComponentPlacement:
            # This is the sole derived perimeter placement domain.  It is
            # materialized before any access-fabric or global-routing work;
            # `DerivedTerminalLayoutVariantIndex` intentionally has no effect
            # here because it represented a former post-failure portfolio.
            DesiredPinsByTerminal = {
                Gate.Name: tuple(
                    Targets.get(TerminalSignal(Gate), ())
                    if TerminalKind(Gate) == "INPUT"
                    else (
                        (Producers[TerminalSignal(Gate)].OutputPin,)
                        if (
                            TerminalSignal(Gate) in Producers
                            and Producers[TerminalSignal(Gate)].OutputPin
                            is not None
                        )
                        else ()
                    )
                )
                for Gate in Gates
            }
            DerivedPerimeterSlotDomainValue = (
                BuildDerivedPerimeterTerminalSlotDomain(
                    Gates,
                    PlacedGates,
                    DesiredPinsByTerminal,
                    WorkCheck=CheckWork,
                )
            )
            DerivedPerimeterSlotAssignmentValue = (
                SolveDerivedPerimeterSlotDomain(
                    DerivedPerimeterSlotDomainValue,
                    TerminalPlacementPolicy.MaximumTerminalAssignmentExpansions,
                    WorkCheck=CheckWork,
                )
            )
            if not DerivedPerimeterSlotAssignmentValue.Success:
                return None
            TerminalGateByName = {Gate.Name: Gate for Gate in Gates}
            return [
                BuildPlacedGate(
                    TerminalGateByName[Slot.TerminalName],
                    *Slot.Origin,
                    Slot.Rotation,
                    Slot.MirrorX,
                )
                for Slot in DerivedPerimeterSlotAssignmentValue.SelectedSlots
            ]

        def TerminalCluster(Gate: Any) -> int | None:
            Signal = TerminalSignal(Gate)
            if TerminalKind(Gate) == "INPUT":
                CandidateClusters = {
                    ClusterByGate[Consumer.Name]
                    for Consumer in TerminalConsumers.get(Signal, ())
                    if Consumer.Name in ClusterByGate
                }
            else:
                Producer = Producers.get(Signal)
                CandidateClusters: set[int] = set()
                if Producer is not None and Producer.Name in ClusterByGate:
                    CandidateClusters.add(ClusterByGate[Producer.Name])
            return min(CandidateClusters) if CandidateClusters else None

        def TerminalOrderKey(Value: Any) -> tuple[Any, ...]:
            ClusterIndex = TerminalCluster(Value)
            return (
                ClusterIndex is None,
                ClusterIndex if ClusterIndex is not None else 10**6,
                PortIndexes[TerminalSignal(Value)],
                Value.Name,
            )

        def CandidateExteriorFace(Candidate: Any) -> str:
            """Classify the shell face reached by one legal terminal cell."""
            Width, Depth = RotatedCellSize(
                Candidate.Kind,
                Candidate.Rotation,
            )
            MaximumX = Candidate.X + Width - 1
            MaximumZ = Candidate.Z + Depth - 1
            if MaximumZ < InternalMinimumZ:
                return "north"
            if Candidate.Z > InternalMaximumZ:
                return "south"
            if MaximumX < InternalMinimumX:
                return "west"
            return "east"

        PreCutInternalSignalsByTerminal: dict[
            str, frozenset[str]
        ] = {}
        for First, Second in AssignmentConstraints.PairwiseConflictEdges:
            FirstSignal = str(First)
            SecondSignal = str(Second)
            if (
                FirstSignal in PortIndexes
                and SecondSignal not in PortIndexes
                and FirstSignal != SecondSignal
            ):
                PreCutInternalSignalsByTerminal[FirstSignal] = frozenset({
                    *PreCutInternalSignalsByTerminal.get(
                        FirstSignal,
                        frozenset(),
                    ),
                    SecondSignal,
                })
            if (
                SecondSignal in PortIndexes
                and FirstSignal not in PortIndexes
                and FirstSignal != SecondSignal
            ):
                PreCutInternalSignalsByTerminal[SecondSignal] = frozenset({
                    *PreCutInternalSignalsByTerminal.get(
                        SecondSignal,
                        frozenset(),
                    ),
                    FirstSignal,
                })
        PreInternalPinsBySignal: dict[
            str, set[tuple[int, int, int]]
        ] = {}
        for Existing in PlacedGates:
            if Existing.OutputPin is not None:
                for Signal in Existing.Outputs:
                    PreInternalPinsBySignal.setdefault(
                        str(Signal),
                        set(),
                    ).add(Existing.OutputPin)
            for InputIndex, Signal in enumerate(Existing.Inputs):
                PreInternalPinsBySignal.setdefault(
                    str(Signal),
                    set(),
                ).add(Existing.InputPins[InputIndex])
        PreCutTerminalPinSpacing = (
            3 + RoutingSpacing
            if (
                TerminalPlacementPolicy.EnableJointClusterOrientation
                and PreCutInternalSignalsByTerminal
            )
            else 0
        )

        TypedTerminalPlacementPressure = (
            TerminalPlacementPolicy.EnableJointClusterOrientation
            or (
                PlacementPolicy is not None
                and PlacementPolicy.PreferWideTerminalBanks
            )
        )
        PreferTerminalRoutingCost = (
            TypedTerminalPlacementPressure
            and len(RelocationPrioritySignals) >= 3
        )
        OptionsByGate: list[tuple[str, list[tuple[tuple[Any, ...], Any]]]] = []
        for Gate in sorted(
            Gates,
            key=TerminalOrderKey,
        ):
            CheckWork("localized-terminal", GateName=Gate.Name)
            Signal = TerminalSignal(Gate)
            DesiredPins = (
                Targets.get(Signal, [])
                if TerminalKind(Gate) == "INPUT"
                else [Producers[Signal].OutputPin]
            )
            # A high-fanout terminal must not be pinned beside an arbitrary
            # consumer.  In particular, a signal which crosses packed-cluster
            # boundaries needs a balanced escape location: limiting candidates
            # to a radius around every individual sink makes the terminal hug an
            # edge and unnecessarily consumes a tall global routing layer.
            # Keep the search bounded, but include a median anchor for every
            # multi-target terminal so the choice generalizes to any topology.
            TargetXs = sorted(Pin[0] for Pin in DesiredPins)
            TargetZs = sorted(Pin[2] for Pin in DesiredPins)
            TargetMiddle = len(DesiredPins) // 2
            MedianAnchor = (
                (TargetXs[(len(TargetXs) - 1) // 2] + TargetXs[TargetMiddle]) // 2,
                DesiredPins[0][1],
                (TargetZs[(len(TargetZs) - 1) // 2] + TargetZs[TargetMiddle]) // 2,
            )
            if UseDerivedSingleComponentPlacement:
                PinY = DesiredPins[0][1]
                CandidatePinPositions = {
                    *(
                        (X, PinY, InternalMinimumZ - 1)
                        for X in range(InternalMinimumX, InternalMaximumX + 1)
                    ),
                    *(
                        (X, PinY, InternalMaximumZ + 1)
                        for X in range(InternalMinimumX, InternalMaximumX + 1)
                    ),
                    *(
                        (InternalMinimumX - 1, PinY, Z)
                        for Z in range(InternalMinimumZ, InternalMaximumZ + 1)
                    ),
                    *(
                        (InternalMaximumX + 1, PinY, Z)
                        for Z in range(InternalMinimumZ, InternalMaximumZ + 1)
                    ),
                }
            else:
                CandidatePinPositions = {
                    (
                        Pin[0] + DeltaX,
                        Pin[1],
                        Pin[2] + DeltaZ,
                    )
                    for Pin in DesiredPins
                    for DeltaX, DeltaZ in BuildDerivedPinAlignmentOffsets()
                }
            if len(DesiredPins) > 1 and not UseDerivedSingleComponentPlacement:
                CandidatePinPositions.update(
                    (
                        MedianAnchor[0] + DeltaX,
                        MedianAnchor[1],
                        MedianAnchor[2] + DeltaZ,
                    )
                    for DeltaX in range(-3, 4)
                    for DeltaZ in range(-3, 4)
                    if abs(DeltaX) + abs(DeltaZ) <= 3
                )
            # I/O is an external interface, not a component of the logic
            # interior.  Keep the shell compact by assigning inputs and
            # outputs to opposing exterior faces, aligned with the pins they
            # serve.  This avoids four-sided horizontal sprawl while ensuring
            # every terminal is visible and approachable at the edge of an
            # arbitrary packed graph.
            if not UseDerivedSingleComponentPlacement:
                ShellAnchors = (*DesiredPins, MedianAnchor)
                ShellClearance = (
                    TerminalPlacementPolicy.TerminalShellClearance
                )
                ShellLateralSearch = (
                    TerminalPlacementPolicy.TerminalShellLateralSearch
                    + (
                        PreCutTerminalPinSpacing
                        if Signal in PreCutInternalSignalsByTerminal
                        else 0
                    )
                )
                ShellZ = (
                    InternalMinimumZ - ShellClearance
                    if TerminalKind(Gate) == "INPUT"
                    else InternalMaximumZ + ShellClearance
                )
                for Anchor in ShellAnchors:
                    CandidatePinPositions.update(
                        (
                            (
                                Anchor[0] + Delta,
                                Anchor[1],
                                ShellZ,
                            )
                            for Delta in range(
                                -ShellLateralSearch,
                                ShellLateralSearch + 1,
                            )
                        )
                    )
            Options = []
            for Rotation in (0, 90, 180, 270):
                CheckWork(
                    "localized-terminal-rotation",
                    GateName=Gate.Name,
                    Rotation=Rotation,
                )
                Origin = BuildPlacedGate(Gate, 0, 1, 0, Rotation, False)
                LocalPin = (
                    Origin.OutputPin
                    if TerminalKind(Gate) == "INPUT"
                    else Origin.InputPins[0]
                )
                for PinPosition in sorted(CandidatePinPositions):
                    Candidate = BuildPlacedGate(
                        Gate,
                        PinPosition[0] - LocalPin[0],
                        PinPosition[1],
                        PinPosition[2] - LocalPin[2],
                        Rotation,
                        False,
                    )
                    if any(
                        PcbGatesConflict(Candidate, Existing)
                        for Existing in PlacedGates
                    ):
                        continue
                    CandidatePin = (
                        Candidate.OutputPin
                        if TerminalKind(Gate) == "INPUT"
                        else Candidate.InputPins[0]
                    )
                    if (
                        PreCutTerminalPinSpacing > 0
                        and any(
                            abs(CandidatePin[0] - InternalPin[0])
                            + abs(CandidatePin[2] - InternalPin[2])
                            < PreCutTerminalPinSpacing
                            for InternalSignal
                            in PreCutInternalSignalsByTerminal.get(
                                Signal,
                                frozenset(),
                            )
                            for InternalPin in PreInternalPinsBySignal.get(
                                InternalSignal,
                                set(),
                            )
                        )
                    ):
                        continue
                    Distance = sum(
                        abs(CandidatePin[0] - Pin[0])
                        + abs(CandidatePin[2] - Pin[2])
                        for Pin in DesiredPins
                    )
                    MaximumDistance = max(
                        abs(CandidatePin[0] - Pin[0])
                        + abs(CandidatePin[2] - Pin[2])
                        for Pin in DesiredPins
                    )
                    CandidateWidth, CandidateDepth = RotatedCellSize(
                        Candidate.Kind,
                        Candidate.Rotation,
                    )
                    CandidateMaximumX = Candidate.X + CandidateWidth - 1
                    CandidateMaximumZ = Candidate.Z + CandidateDepth - 1
                    IsOutsideCore = (
                        CandidateMaximumX < InternalMinimumX
                        or Candidate.X > InternalMaximumX
                        or CandidateMaximumZ < InternalMinimumZ
                        or Candidate.Z > InternalMaximumZ
                    )
                    if not IsOutsideCore:
                        continue
                    MinimumX = min(PlacedMinimumX, Candidate.X)
                    MaximumX = max(
                        PlacedMaximumX,
                        Candidate.X + CandidateWidth,
                    )
                    MinimumZ = min(PlacedMinimumZ, Candidate.Z)
                    MaximumZ = max(
                        PlacedMaximumZ,
                        Candidate.Z + CandidateDepth,
                    )
                    Width = MaximumX - MinimumX
                    Depth = MaximumZ - MinimumZ
                    Options.append(
                        (
                            (
                                MaximumDistance,
                                Distance,
                                Width * Depth,
                                max(Width, Depth),
                                Candidate.X,
                                Candidate.Z,
                                Rotation,
                            ),
                            Candidate,
                        )
                    )
            if not Options:
                return None
            OrderedOptions = sorted(
                Options,
                key=lambda Value: (
                    (
                        Value[0][0],
                        Value[0][1],
                        Value[0][2],
                        Value[0][3],
                    )
                    if PreferTerminalRoutingCost
                    else (
                        Value[0][2],
                        Value[0][3],
                        Value[0][0],
                        Value[0][1],
                    )
                ) + (
                    Value[0][4:],
                ),
            )

            def ExteriorFace(Option: tuple[tuple[Any, ...], Any]) -> str:
                return CandidateExteriorFace(Option[1])

            # Keep one low-cost representative on every exterior face before
            # truncating the bounded candidate pool.  A terminal that looks
            # marginally worse in isolation can avoid increasing the shared
            # X or Z envelope once neighbouring ports have already occupied a
            # different face.  This is topology-agnostic and leaves the exact
            # joint assignment responsible for the final choice.
            FaceRepresentatives: list[tuple[tuple[Any, ...], Any]] = []
            SeenFaces: set[str] = set()
            for Option in OrderedOptions:
                Face = ExteriorFace(Option)
                if Face in SeenFaces:
                    continue
                SeenFaces.add(Face)
                FaceRepresentatives.append(Option)
            SelectedOptions = list(FaceRepresentatives)
            for Option in OrderedOptions:
                if Option in SelectedOptions:
                    continue
                SelectedOptions.append(Option)
                if (
                    len(SelectedOptions)
                    >= TerminalPlacementPolicy.MaximumTerminalPlacementCandidates
                ):
                    break
            OptionsByGate.append((
                Gate.Name,
                SelectedOptions[
                    :TerminalPlacementPolicy.MaximumTerminalPlacementCandidates
                ],
            ))

        # A derived perimeter placement may publish a bounded set of complete,
        # access-distinct terminal layouts before routing.  The requested
        # variant index is part of the placement recipe; it is never advanced
        # in response to a capacity or routing failure.  Ordinary placements
        # retain their historical one-layout behaviour.
        RequiredTerminalLayoutCount = (
            DerivedTerminalLayoutVariantIndex + 1
            if UseDerivedSingleComponentPlacement
            else 1
        )
        RetainedTerminalSelections: dict[
            tuple[object, ...],
            tuple[
                tuple[Any, ...],
                tuple[tuple[tuple[Any, ...], Any], ...],
            ],
        ] = {}
        AssignmentExpansions = 0
        StopAfterFirstLegalTerminalAssignment = (
            TypedTerminalPlacementPressure
            and RelocationVariant >= 2
            and RequiredTerminalLayoutCount == 1
        )
        TerminalAssignmentExpansionLimit = (
            min(
                TerminalPlacementPolicy.MaximumTerminalAssignmentExpansions,
                4_096,
            )
            if StopAfterFirstLegalTerminalAssignment
            else TerminalPlacementPolicy.MaximumTerminalAssignmentExpansions
        )
        MinimumTerminalPinSpacing = (
            3 + RoutingSpacing
            if (
                TypedTerminalPlacementPressure
            )
            else 0
        )
        CutScopedTerminalPinPairs = frozenset(
            tuple(sorted((str(First), str(Second))))
            for First, Second in AssignmentConstraints.PairwiseConflictEdges
            if str(First) in PortIndexes
            and str(Second) in PortIndexes
            and str(First) != str(Second)
        )
        CutScopedInternalSignalsByTerminal: dict[
            str, frozenset[str]
        ] = {}
        for First, Second in AssignmentConstraints.PairwiseConflictEdges:
            FirstSignal = str(First)
            SecondSignal = str(Second)
            if (
                FirstSignal in PortIndexes
                and SecondSignal not in PortIndexes
                and FirstSignal != SecondSignal
            ):
                CutScopedInternalSignalsByTerminal[FirstSignal] = (
                    frozenset({
                        *CutScopedInternalSignalsByTerminal.get(
                            FirstSignal,
                            frozenset(),
                        ),
                        SecondSignal,
                    })
                )
            if (
                SecondSignal in PortIndexes
                and FirstSignal not in PortIndexes
                and FirstSignal != SecondSignal
            ):
                CutScopedInternalSignalsByTerminal[SecondSignal] = (
                    frozenset({
                        *CutScopedInternalSignalsByTerminal.get(
                            SecondSignal,
                            frozenset(),
                        ),
                        FirstSignal,
                    })
                )
        InternalPinsBySignal: dict[
            str, frozenset[tuple[int, int, int]]
        ] = {}
        MutableInternalPinsBySignal: dict[
            str, set[tuple[int, int, int]]
        ] = {}
        for Existing in PlacedGates:
            if Existing.OutputPin is not None:
                for Signal in Existing.Outputs:
                    MutableInternalPinsBySignal.setdefault(
                        str(Signal),
                        set(),
                    ).add(Existing.OutputPin)
            for InputIndex, Signal in enumerate(Existing.Inputs):
                MutableInternalPinsBySignal.setdefault(
                    str(Signal),
                    set(),
                ).add(Existing.InputPins[InputIndex])
        InternalPinsBySignal = {
            Signal: frozenset(Pins)
            for Signal, Pins in MutableInternalPinsBySignal.items()
        }
        CutScopedTerminalPinSpacing = (
            3 + RoutingSpacing
            if (
                TerminalPlacementPolicy.EnableJointClusterOrientation
                and (
                    CutScopedTerminalPinPairs
                    or CutScopedInternalSignalsByTerminal
                )
            )
            else 0
        )

        def TerminalConnectionPin(Candidate: Any) -> tuple[int, int, int]:
            return (
                Candidate.OutputPin
                if getattr(Candidate.Kind, "value", Candidate.Kind) == "INPUT"
                else Candidate.InputPins[0]
            )

        TerminalCandidates = tuple(
            Candidate
            for _GateName, Options in OptionsByGate
            for _Key, Candidate in Options
        )
        TerminalPinByIdentity = {
            id(Candidate): TerminalConnectionPin(Candidate)
            for Candidate in TerminalCandidates
        }
        TerminalBoundsByIdentity = {
            id(Candidate): (
                Candidate.X,
                Candidate.X
                + RotatedCellSize(Candidate.Kind, Candidate.Rotation)[0],
                Candidate.Z,
                Candidate.Z
                + RotatedCellSize(Candidate.Kind, Candidate.Rotation)[1],
            )
            for Candidate in TerminalCandidates
        }
        TerminalConflictCache: dict[tuple[int, int], bool] = {}
        MinimumRemainingRoutingCosts: list[tuple[int, int]] = [
            (0, 0)
            for _ in range(len(OptionsByGate) + 1)
        ]
        for OptionIndex in range(len(OptionsByGate) - 1, -1, -1):
            _GateName, Options = OptionsByGate[OptionIndex]
            RemainingMaximumDistance, RemainingTotalDistance = (
                MinimumRemainingRoutingCosts[OptionIndex + 1]
            )
            MinimumRemainingRoutingCosts[OptionIndex] = (
                RemainingMaximumDistance
                + min(Key[0] for Key, _Candidate in Options),
                RemainingTotalDistance
                + min(Key[1] for Key, _Candidate in Options),
            )

        def TerminalCandidatesConflict(First: Any, Second: Any) -> bool:
            FirstIdentity = id(First)
            SecondIdentity = id(Second)
            Key = (
                (FirstIdentity, SecondIdentity)
                if FirstIdentity < SecondIdentity
                else (SecondIdentity, FirstIdentity)
            )
            Conflict = TerminalConflictCache.get(Key)
            if Conflict is None:
                Conflict = PcbGatesConflict(First, Second)
                TerminalConflictCache[Key] = Conflict
            return Conflict

        InternalAccessPositionsBySignal: dict[
            str,
            frozenset[tuple[int, int, int]],
        ] = {}
        MutableInternalAccessPositionsBySignal: dict[
            str,
            set[tuple[int, int, int]],
        ] = {}
        for Existing in PlacedGates:
            if Existing.OutputPin is not None and Existing.OutputDirection is not None:
                for Signal in Existing.Outputs:
                    MutableInternalAccessPositionsBySignal.setdefault(
                        str(Signal),
                        set(),
                    ).update(
                        (
                            Existing.OutputPin[0]
                            + Existing.OutputDirection[0] * Offset,
                            Existing.OutputPin[1]
                            + Existing.OutputDirection[1] * Offset,
                            Existing.OutputPin[2]
                            + Existing.OutputDirection[2] * Offset,
                        )
                        for Offset in range(
                            DefaultRedstoneRoutingTechnology.AccessLength
                        )
                    )
            for InputIndex, Signal in enumerate(Existing.Inputs):
                Pin = Existing.InputPins[InputIndex]
                Direction = Existing.InputDirections[InputIndex]
                MutableInternalAccessPositionsBySignal.setdefault(
                    str(Signal),
                    set(),
                ).update(
                    (
                        Pin[0] + Direction[0] * Offset,
                        Pin[1] + Direction[1] * Offset,
                        Pin[2] + Direction[2] * Offset,
                    )
                    for Offset in range(
                        DefaultRedstoneRoutingTechnology.AccessLength
                    )
                )
        InternalAccessPositionsBySignal = {
            Signal: frozenset(Positions)
            for Signal, Positions
            in MutableInternalAccessPositionsBySignal.items()
        }

        def TerminalAccessPositions(
            Candidate: Any,
        ) -> frozenset[tuple[int, int, int]]:
            Pin = TerminalConnectionPin(Candidate)
            Direction = (
                Candidate.OutputDirection
                if getattr(Candidate.Kind, "value", Candidate.Kind) == "INPUT"
                else Candidate.InputDirections[0]
            )
            return frozenset(
                (
                    Pin[0] + Direction[0] * Offset,
                    Pin[1] + Direction[1] * Offset,
                    Pin[2] + Direction[2] * Offset,
                )
                for Offset in range(
                    DefaultRedstoneRoutingTechnology.AccessLength
                )
            )

        TerminalAccessPositionsByIdentity = {
            id(Candidate): TerminalAccessPositions(Candidate)
            for Candidate in TerminalCandidates
        }
        TerminalAccessExclusionsByIdentity = {
            Identity: (
                DefaultRedstoneRoutingTechnology.BuildElectricalExclusions(
                    Positions
                )
            )
            for Identity, Positions
            in TerminalAccessPositionsByIdentity.items()
        }
        InternalAccessExclusionsBySignal = {
            Signal: (
                DefaultRedstoneRoutingTechnology.BuildElectricalExclusions(
                    Positions
                )
            )
            for Signal, Positions
            in InternalAccessPositionsBySignal.items()
        }
        TerminalInternalAccessConflictCountByIdentity = {
            id(Candidate): sum(
                1
                for OtherSignal, OtherPositions
                in InternalAccessPositionsBySignal.items()
                if TerminalSignal(Candidate) != OtherSignal
                and (
                    TerminalAccessPositionsByIdentity[id(Candidate)]
                    & InternalAccessExclusionsBySignal[OtherSignal]
                    or OtherPositions
                    & TerminalAccessExclusionsByIdentity[id(Candidate)]
                )
            )
            for Candidate in TerminalCandidates
        }
        TerminalAccessConflictCache: dict[tuple[int, int], bool] = {}

        def TerminalAccessConflicts(First: Any, Second: Any) -> bool:
            FirstIdentity = id(First)
            SecondIdentity = id(Second)
            Key = (
                (FirstIdentity, SecondIdentity)
                if FirstIdentity < SecondIdentity
                else (SecondIdentity, FirstIdentity)
            )
            Conflict = TerminalAccessConflictCache.get(Key)
            if Conflict is None:
                Conflict = bool(
                    TerminalAccessPositionsByIdentity[FirstIdentity]
                    & TerminalAccessExclusionsByIdentity[SecondIdentity]
                    or TerminalAccessPositionsByIdentity[SecondIdentity]
                    & TerminalAccessExclusionsByIdentity[FirstIdentity]
                )
                TerminalAccessConflictCache[Key] = Conflict
            return Conflict

        def SelectionAccessConflictCount(
            Selected: tuple[tuple[tuple[Any, ...], Any], ...],
        ) -> int:
            Candidates = tuple(
                Candidate for _Key, Candidate in Selected
            )
            return (
                sum(
                    TerminalInternalAccessConflictCountByIdentity[
                        id(Candidate)
                    ]
                    for Candidate in Candidates
                )
                + sum(
                    TerminalSignal(First) != TerminalSignal(Second)
                    and TerminalAccessConflicts(First, Second)
                    for Index, First in enumerate(Candidates)
                    for Second in Candidates[Index + 1:]
                )
            )

        def SelectionAccessIdentity(
            Selected: tuple[tuple[tuple[Any, ...], Any], ...],
        ) -> tuple[object, ...]:
            """Identify a complete terminal layout by physical access shape.

            A rotation-only change on the same side rarely alters the shared
            capacity problem.  Retain the best legal representative for each
            signal-to-perimeter-face pattern, so every fixed member changes
            the topology of its terminal escape domain rather than spending a
            domain slot on cosmetic orientation.
            """
            return tuple(sorted(
                (
                    str(TerminalSignal(Candidate)),
                    str(Candidate.Name),
                    CandidateExteriorFace(Candidate),
                )
                for _Key, Candidate in Selected
            ))

        def WorstRetainedTerminalScore() -> tuple[Any, ...] | None:
            if len(RetainedTerminalSelections) < RequiredTerminalLayoutCount:
                return None
            return max(
                Score
                for Score, _Selection in RetainedTerminalSelections.values()
            )

        def RetainTerminalSelection(
            Selected: tuple[tuple[tuple[Any, ...], Any], ...],
        ) -> None:
            """Retain the best fixed representatives without a retry queue."""
            Identity = SelectionAccessIdentity(Selected)
            Score = SelectionScore(Selected)
            Existing = RetainedTerminalSelections.get(Identity)
            if Existing is not None and Existing[0] <= Score:
                return
            RetainedTerminalSelections[Identity] = (Score, Selected)
            if len(RetainedTerminalSelections) <= RequiredTerminalLayoutCount:
                return
            WorstIdentity = max(
                RetainedTerminalSelections,
                key=lambda Value: (
                    RetainedTerminalSelections[Value][0],
                    Value,
                ),
            )
            del RetainedTerminalSelections[WorstIdentity]

        def SelectionScore(
            Selected: tuple[tuple[tuple[Any, ...], Any], ...],
        ) -> tuple[Any, ...]:
            SelectedBounds = tuple(
                TerminalBoundsByIdentity[id(Candidate)]
                for _Key, Candidate in Selected
            )
            MinimumX = min((
                PlacedMinimumX,
                *(Bounds[0] for Bounds in SelectedBounds),
            ))
            MaximumX = max((
                PlacedMaximumX,
                *(Bounds[1] for Bounds in SelectedBounds),
            ))
            MinimumZ = min((
                PlacedMinimumZ,
                *(Bounds[2] for Bounds in SelectedBounds),
            ))
            MaximumZ = max((
                PlacedMaximumZ,
                *(Bounds[3] for Bounds in SelectedBounds),
            ))
            Width = MaximumX - MinimumX
            Depth = MaximumZ - MinimumZ
            AreaScore = (Width * Depth, max(Width, Depth))
            RoutingScore = (
                sum(Key[0] for Key, _Candidate in Selected),
                sum(Key[1] for Key, _Candidate in Selected),
            )
            FaceCounts = {
                Face: sum(
                    CandidateExteriorFace(Candidate) == Face
                    for _Key, Candidate in Selected
                )
                for Face in ("north", "south", "east", "west")
            }
            RingScore = (
                max(FaceCounts.values(), default=0),
                sum(Count * Count for Count in FaceCounts.values()),
                *AreaScore,
            )
            BaseScore = (
                (*RingScore, *RoutingScore)
                if PreferAccessRingTerminals
                else (*RoutingScore, *AreaScore)
                if PreferTerminalRoutingCost
                else (*AreaScore, *RoutingScore)
            )
            return (
                (
                    (SelectionAccessConflictCount(Selected), *BaseScore)
                    if UseDerivedSingleComponentPlacement
                    else BaseScore
                )
                + (
                tuple(
                    (Candidate.Name, Candidate.X, Candidate.Z, Candidate.Rotation)
                    for _Key, Candidate in Selected
                ),
                )
            )

        def SelectionLowerBound(
            Index: int,
            Selected: tuple[tuple[tuple[Any, ...], Any], ...],
        ) -> tuple[int, int, int, int]:
            """Return a monotone prefix bound for exact terminal assignment."""
            SelectedBounds = tuple(
                TerminalBoundsByIdentity[id(Candidate)]
                for _Key, Candidate in Selected
            )
            MinimumX = min((
                PlacedMinimumX,
                *(Bounds[0] for Bounds in SelectedBounds),
            ))
            MaximumX = max((
                PlacedMaximumX,
                *(Bounds[1] for Bounds in SelectedBounds),
            ))
            MinimumZ = min((
                PlacedMinimumZ,
                *(Bounds[2] for Bounds in SelectedBounds),
            ))
            MaximumZ = max((
                PlacedMaximumZ,
                *(Bounds[3] for Bounds in SelectedBounds),
            ))
            Width = MaximumX - MinimumX
            Depth = MaximumZ - MinimumZ
            AreaScore = (Width * Depth, max(Width, Depth))
            if PreferAccessRingTerminals:
                FaceCounts = {
                    Face: sum(
                        CandidateExteriorFace(Candidate) == Face
                        for _Key, Candidate in Selected
                    )
                    for Face in ("north", "south", "east", "west")
                }
                BaseLowerBound = (
                    max(FaceCounts.values(), default=0),
                    sum(Count * Count for Count in FaceCounts.values()),
                    *AreaScore,
                )
                return (
                    (SelectionAccessConflictCount(Selected), *BaseLowerBound)
                    if UseDerivedSingleComponentPlacement
                    else BaseLowerBound
                )
            RemainingMaximumDistance, RemainingTotalDistance = (
                MinimumRemainingRoutingCosts[Index]
            )
            RoutingScore = (
                sum(Key[0] for Key, _Candidate in Selected)
                + RemainingMaximumDistance,
                sum(Key[1] for Key, _Candidate in Selected)
                + RemainingTotalDistance,
            )
            BaseLowerBound = (
                (*RoutingScore, *AreaScore)
                if PreferTerminalRoutingCost
                else (*AreaScore, *RoutingScore)
            )
            return (
                (SelectionAccessConflictCount(Selected), *BaseLowerBound)
                if UseDerivedSingleComponentPlacement
                else BaseLowerBound
            )

        PrunedAssignmentExpansions = 0

        def SearchTerminalAssignments(
            Index: int,
            Selected: tuple[tuple[tuple[Any, ...], Any], ...],
        ) -> None:
            nonlocal AssignmentExpansions
            nonlocal PrunedAssignmentExpansions
            if AssignmentExpansions >= TerminalAssignmentExpansionLimit:
                return
            AssignmentExpansions += 1
            LowerBound = SelectionLowerBound(Index, Selected)
            WorstScore = WorstRetainedTerminalScore()
            if (
                WorstScore is not None
                and LowerBound > WorstScore[:len(LowerBound)]
            ):
                PrunedAssignmentExpansions += 1
                return
            if Index == len(OptionsByGate):
                RetainTerminalSelection(Selected)
                return
            _GateName, Options = OptionsByGate[Index]
            for Option in Options:
                _Key, Candidate = Option
                if any(
                    TerminalCandidatesConflict(Candidate, Existing)
                    for _SelectedKey, Existing in Selected
                ):
                    continue
                CandidatePin = TerminalPinByIdentity[id(Candidate)]
                if (
                    MinimumTerminalPinSpacing > 0
                    and any(
                        abs(CandidatePin[0] - SelectedPin[0])
                        + abs(CandidatePin[2] - SelectedPin[2])
                        < MinimumTerminalPinSpacing
                        for SelectedPin in (
                            TerminalPinByIdentity[id(Existing)]
                            for _SelectedKey, Existing in Selected
                        )
                    )
                ):
                    continue
                CandidateSignal = TerminalSignal(Candidate)
                if (
                    CutScopedTerminalPinSpacing > 0
                    and any(
                        abs(CandidatePin[0] - InternalPin[0])
                        + abs(CandidatePin[2] - InternalPin[2])
                        < CutScopedTerminalPinSpacing
                        for InternalSignal
                        in CutScopedInternalSignalsByTerminal.get(
                            CandidateSignal,
                            frozenset(),
                        )
                        for InternalPin in InternalPinsBySignal.get(
                            InternalSignal,
                            frozenset(),
                        )
                    )
                ):
                    continue
                if (
                    CutScopedTerminalPinSpacing > 0
                    and any(
                        tuple(sorted((
                            CandidateSignal,
                            TerminalSignal(Existing),
                        ))) in CutScopedTerminalPinPairs
                        and (
                            abs(
                                CandidatePin[0]
                                - TerminalPinByIdentity[id(Existing)][0]
                            )
                            + abs(
                                CandidatePin[2]
                                - TerminalPinByIdentity[id(Existing)][2]
                            )
                            < CutScopedTerminalPinSpacing
                        )
                        for _SelectedKey, Existing in Selected
                    )
                ):
                    continue
                SearchTerminalAssignments(Index + 1, (*Selected, Option))
                if (
                    StopAfterFirstLegalTerminalAssignment
                    and RetainedTerminalSelections
                ):
                    return

        SearchTerminalAssignments(0, ())
        CheckWork(
            "localized-terminal-search-complete",
            AssignmentExpansions=AssignmentExpansions,
            StopAfterFirstLegalTerminalAssignment=(
                StopAfterFirstLegalTerminalAssignment
            ),
            RequestedDerivedTerminalLayoutVariantIndex=(
                DerivedTerminalLayoutVariantIndex
            ),
            RetainedTerminalLayoutCount=len(RetainedTerminalSelections),
            NandCount=NandCount,
            RelocationVariant=RelocationVariant,
            PrunedAssignmentExpansions=PrunedAssignmentExpansions,
        )
        OrderedTerminalSelections = tuple(sorted(
            RetainedTerminalSelections.values(),
            key=lambda Value: Value[0],
        ))
        if not OrderedTerminalSelections:
            return None
        if (
            DerivedTerminalLayoutVariantIndex
            >= len(OrderedTerminalSelections)
        ):
            raise ValueError(
                "derived terminal layout variant exceeds the complete "
                "access-distinct terminal domain"
            )
        _Score, SelectedTerminalLayout = OrderedTerminalSelections[
            DerivedTerminalLayoutVariantIndex
        ]
        return [Candidate for _Key, Candidate in SelectedTerminalLayout]

    if MandatoryAccessPreScreenOnly:
        if (
            ExactStatePlacementCacheKey is not None
            and CachedExactStateGeometry is None
        ):
            CachedExactStateGeometry = tuple(
                ExactStatePlacedGateGeometry.FromPlacedGate(Gate)
                for Gate in PlacedGates
            )
            _ExactStatePlacementGeometryCache[
                ExactStatePlacementCacheKey
            ] = CachedExactStateGeometry
            JointPlacementDiagnostics[
                "ExactStatePlacementCache"
            ]["CachedGateCount"] = len(CachedExactStateGeometry)
        SignalOrder = tuple(sorted({
            *Module.Inputs,
            *Module.Outputs,
            *(
                Signal
                for Gate in Module.Gates
                for Signal in (*Gate.Inputs, *Gate.Outputs)
            ),
        }))
        PreScreenDiagnostics: dict[str, object] = {
            "__InterClusterGaps__": GapPlan.ToDictionary(),
            "__MandatoryAccessPreScreen__": {
                "Enabled": True,
                "TerminalsIncluded": False,
                "JointPlacementCandidateIndex": (
                    JointPlacementCandidateIndex
                ),
                "PlacedGateCount": len(PlacedGates),
                "SignalCount": len(SignalOrder),
            },
        }
        if JointPlacementDiagnostics:
            PreScreenDiagnostics["__JointClusterPlacement__"] = deepcopy(
                JointPlacementDiagnostics
            )
        if PackedAccessRepairByCluster:
            PreScreenDiagnostics["__PackedAccessRepair__"] = {
                str(ClusterIndex): deepcopy(Diagnostics)
                for ClusterIndex, Diagnostics in sorted(
                    PackedAccessRepairByCluster.items()
                )
            }
        return PcbPlacement(
            Placed=PlacedDesign(
                Module=Module,
                PlacedGates=list(PlacedGates),
                LocalRouteDiagnostics=PreScreenDiagnostics,
            ),
            Clusters=Clusters,
            SignalOrder=SignalOrder,
            LayerCount=(
                PlacementPolicy.MaximumRoutingLayers
                if PlacementPolicy is not None
                else 0
            ),
            MandatoryAccessPreScreenProfile=(
                SelectedExactMandatoryAccessProfile
            ),
        )

    # Prefer compact terminals localized to their producer/consumer geometry
    # in both packed and unpacked placements. If any localized choice
    # conflicts, retain the deterministic side banks as a reliability fallback.
    BasePlacement = list(PlacedGates)
    TerminalPortIndexes = {
        Signal: Index
        for Index, Signal in enumerate((*Module.Inputs, *Module.Outputs))
    }
    PlannedTerminals = (
        PlaceLocalizedTerminals(
            [*InputGates, *OutputGates],
            TerminalPortIndexes,
        )
        if (
            PackingPolicy is not None
            and CachedExactStateGeometry is None
        )
        else None
    )
    if UseDerivedSingleComponentPlacement and PlannedTerminals is None:
        raise ValueError(
            "derived single-component terminal domain has no legal assignment"
        )
    if PlannedTerminals is not None:
        CandidatePlacement = BasePlacement + PlannedTerminals
        try:
            if any(
                PcbGatesConflict(First, Second)
                for Index, First in enumerate(CandidatePlacement)
                for Second in CandidatePlacement[Index + 1 :]
            ):
                raise ValueError("localized terminal placement conflicts")
            _ = BuildPlacedCellGeometry(
                PlacedDesign(Module=Module, PlacedGates=CandidatePlacement)
            )
            PlacedGates = CandidatePlacement
        except ValueError:
            PlannedTerminals = None
    PlacedGates = BasePlacement + (PlannedTerminals or [])
    PlannedTerminalNames = {
        Gate.Name for Gate in (PlannedTerminals or [])
    }
    RemainingInputGates = [
        Gate for Gate in InputGates
        if (
            CachedExactStateGeometry is None
            and Gate.Name not in PlannedTerminalNames
        )
    ]
    RemainingOutputGates = [
        Gate for Gate in OutputGates
        if (
            CachedExactStateGeometry is None
            and Gate.Name not in PlannedTerminalNames
        )
    ]
    if RemainingInputGates:
        RemainingInputSignals = {
            Gate.Outputs[0] for Gate in RemainingInputGates
        }
        PlaceTerminalBank(
            RemainingInputGates,
            InternalMinimumZ - 4,
            -1,
            [
                Signal
                for Signal in Module.Inputs
                if Signal in RemainingInputSignals
            ],
            LocalizeByInternalPins=(
                not PackedMode and PackingPolicy is not None
            ),
        )
    if RemainingOutputGates:
        RemainingOutputSignals = {
            Gate.Inputs[0] for Gate in RemainingOutputGates
        }
        PlaceTerminalBank(
            RemainingOutputGates,
            InternalMaximumZ + 2,
            1,
            [
                Signal
                for Signal in Module.Outputs
                if Signal in RemainingOutputSignals
            ],
            LocalizeByInternalPins=(
                not PackedMode and PackingPolicy is not None
            ),
        )

    if PackedMode and any(
        PcbGatesConflict(First, Second)
        for Index, First in enumerate(PlacedGates)
        for Second in PlacedGates[Index + 1 :]
    ):
        raise ValueError("Packed placement conflicts at final commit")
    if (
        ExactStatePlacementCacheKey is not None
        and CachedExactStateGeometry is None
        and not UseDerivedSingleComponentPlacement
    ):
        CachedExactStateGeometry = tuple(
            ExactStatePlacedGateGeometry.FromPlacedGate(Gate)
            for Gate in PlacedGates
        )
        _ExactStatePlacementGeometryCache[
            ExactStatePlacementCacheKey
        ] = CachedExactStateGeometry
        JointPlacementDiagnostics[
            "ExactStatePlacementCache"
        ]["CachedGateCount"] = len(CachedExactStateGeometry)
    CheckWork("terminal-placement-complete", GateCount=len(PlacedGates))
    Placed = PlacedDesign(
        Module=Module,
        PlacedGates=PlacedGates,
        DerivedPerimeterSlotDomain=DerivedPerimeterSlotDomainValue,
        DerivedPerimeterSlotAssignment=(
            DerivedPerimeterSlotAssignmentValue
        ),
    )
    if PackedMode:
        Producers = {
            Signal: Gate
            for Gate in PlacedGates
            if Gate.OutputPin is not None
            for Signal in Gate.Outputs
        }
        TargetsBySignal: dict[str, list[tuple[int, int, int]]] = {}
        for Gate in PlacedGates:
            CheckWork("local-access-geometry", GateName=Gate.Name)
            for InputIndex, Signal in enumerate(Gate.Inputs):
                TargetsBySignal.setdefault(Signal, []).append(
                    Gate.InputPins[InputIndex]
                )
        FrozenNetWires = {}
        LocalNetBranches = {}
        LocalNetTargets = {}
        LocalRouteClaims = []
        LocalRouteDiagnostics = {}
        JointLocalCandidatesByCluster: dict[
            int, dict[str, list[LocalClusterRouteCandidate]]
        ] = {}
        LocalRouteDiagnostics["__InterClusterGaps__"] = (
            GapPlan.ToDictionary()
        )
        if ClusterRefinementProfile is not None:
            LocalRouteDiagnostics["__CutDrivenClusterRefinement__"] = {
                **ClusterRefinementProfile.ToDictionary(),
                "Signals": list(ClusterRefinementProfile.Signals),
                "ClusterCount": len(Clusters),
            }
        if JointPlacementDiagnostics:
            LocalRouteDiagnostics["__JointClusterPlacement__"] = (
                JointPlacementDiagnostics
            )
        if PackedAccessRepairByCluster:
            LocalRouteDiagnostics["__PackedAccessRepair__"] = {
                str(ClusterIndex): Diagnostics
                for ClusterIndex, Diagnostics in sorted(
                    PackedAccessRepairByCluster.items()
                )
            }
        ActualBlocks, ElectricalBlocks, SolidBlocks = BuildPlacedCellGeometry(Placed)
        LocalResourceGraph = RoutingResourceGraph(
            ActualBlocks=frozenset(ActualBlocks),
            ElectricalBlocks=frozenset(ElectricalBlocks),
            SolidBlocks=frozenset(SolidBlocks),
        )
        ClusterByGate = {
            Name: ClusterIndex
            for ClusterIndex, Names in enumerate(Clusters)
            for Name in Names
        }
        GateByInputPin = {
            Pin: Gate.Name
            for Gate in PlacedGates
            for Pin in Gate.InputPins
        }
        MaximumLength = PackingPolicy.DirectConnectMaximumLength
        # This is a geometric search envelope, not an electrical allowance.
        # ValidateLocalSignalStrength below derives the accepted repeater-free
        # distance from the active routing technology.
        MaximumLocalRouteLength = PackingPolicy.MaximumLocalRouteLength
        MinimumRouteX = min(Gate.X for Gate in PlacedGates) - PackingPolicy.LocalRouteEnvelope
        MaximumRouteX = max(
            Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0]
            for Gate in PlacedGates
        ) + PackingPolicy.LocalRouteEnvelope
        MinimumRouteZ = min(Gate.Z for Gate in PlacedGates) - PackingPolicy.LocalRouteEnvelope
        MaximumRouteZ = max(
            Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1]
            for Gate in PlacedGates
        ) + PackingPolicy.LocalRouteEnvelope
        MinimumRouteY = min(Gate.Y for Gate in PlacedGates)
        MaximumRouteY = (
            max(Gate.Y for Gate in PlacedGates)
            + PackingPolicy.LocalRouteEnvelope
        )
        AccessBySignal: dict[str, set[tuple[int, int, int]]] = {}
        AccessByClusterSignal: dict[
            tuple[int, str], set[tuple[int, int, int]]
        ] = {}
        BoundaryAccessBySignal: dict[str, set[tuple[int, int, int]]] = {}
        for Gate in PlacedGates:
            GateCluster = ClusterByGate.get(Gate.Name)
            if Gate.OutputPin is not None and Gate.OutputDirection is not None:
                for Signal in Gate.Outputs:
                    OutputAccess = tuple(
                        (
                            Gate.OutputPin[0] + Gate.OutputDirection[0] * Offset,
                            Gate.OutputPin[1] + Gate.OutputDirection[1] * Offset,
                            Gate.OutputPin[2] + Gate.OutputDirection[2] * Offset,
                        )
                        for Offset in range(3)
                    )
                    AccessBySignal.setdefault(Signal, set()).update(OutputAccess)
                    if GateCluster is not None:
                        AccessByClusterSignal.setdefault(
                            (GateCluster, Signal),
                            set(),
                        ).update(OutputAccess)
                    BoundaryAccessBySignal.setdefault(Signal, set()).update(
                        OutputAccess[:2]
                    )
            for Signal, Pin, Direction in zip(
                Gate.Inputs, Gate.InputPins, Gate.InputDirections
            ):
                InputAccess = tuple(
                    (
                        Pin[0] + Direction[0] * Offset,
                        Pin[1] + Direction[1] * Offset,
                        Pin[2] + Direction[2] * Offset,
                    )
                    for Offset in range(3)
                )
                AccessBySignal.setdefault(Signal, set()).update(InputAccess)
                if GateCluster is not None:
                    AccessByClusterSignal.setdefault(
                        (GateCluster, Signal),
                        set(),
                    ).update(InputAccess)
                BoundaryAccessBySignal.setdefault(Signal, set()).update(
                    InputAccess[:2]
                )
        AccessClaimsBySignal = {
            Signal: LocalResourceGraph.BuildRouteClaims(Positions)
            for Signal, Positions in BoundaryAccessBySignal.items()
            if Positions
        }

        def ValidateBoundaryEscapes(Candidate: LocalRouteClaim) -> None:
            """Keep fixed local trees from consuming another net's pin escape."""
            for OtherSignal, AccessClaims in AccessClaimsBySignal.items():
                if OtherSignal == Candidate.Signal:
                    continue
                Conflicts = FindClaimConflicts(
                    {
                        Candidate.Signal: Candidate.Claims,
                        OtherSignal: AccessClaims,
                    }
                )
                if Conflicts:
                    Resource = min(Conflicts, key=str)
                    raise ValueError(
                        "Local route blocks boundary escape at "
                        f"{Resource}: {Candidate.Signal},{OtherSignal}"
                    )

        def FindLocalPath(
            Starts: set[tuple[int, int, int]],
            Target: tuple[int, int, int],
            Signal: str,
        ) -> tuple[tuple[int, int, int], ...]:
            """Find one bounded component-plane extension from an owned tree."""
            OtherClaims = [
                Claim for Claim in LocalRouteClaims if Claim.Signal != Signal
            ]
            Blocked = set().union(
                *(Claim.Claims.ElectricalCells for Claim in OtherClaims)
            ) if OtherClaims else set()
            Parents: dict[tuple[int, int, int], tuple[int, int, int] | None] = {
                Start: None for Start in sorted(Starts)
            }
            Distances = {Start: 0 for Start in Starts}
            Pending = deque(sorted(Starts))
            CompletedNodes = 0
            while Pending and Target not in Parents:
                CompletedNodes += 1
                if CompletedNodes % 256 == 0:
                    CheckWork(
                        "local-path-search",
                        Signal=Signal,
                        CompletedNodes=CompletedNodes,
                        PendingNodes=len(Pending),
                    )
                Current = Pending.popleft()
                Distance = Distances[Current]
                if Distance >= MaximumLocalRouteLength:
                    continue
                for Neighbor in sorted(
                    DefaultRedstoneRoutingTechnology.NeighborPositions(Current)
                ):
                    if Neighbor in Parents:
                        continue
                    if not (
                        MinimumRouteX <= Neighbor[0] <= MaximumRouteX
                        and MinimumRouteZ <= Neighbor[2] <= MaximumRouteZ
                        and MinimumRouteY <= Neighbor[1] <= MaximumRouteY
                    ):
                        continue
                    if Neighbor in ActualBlocks and Neighbor != Target:
                        continue
                    if (
                        Neighbor in LocalResourceGraph.StaticKeepOut
                        and Neighbor not in AccessBySignal.get(Signal, set())
                        and Neighbor != Target
                    ):
                        continue
                    if Neighbor in Blocked and Neighbor != Target:
                        continue
                    if LocalResourceGraph.BuildPrimitive(Current, Neighbor) is None:
                        continue
                    Support = (Neighbor[0], Neighbor[1] - 1, Neighbor[2])
                    if Support in ActualBlocks and Neighbor != Target:
                        continue
                    Parents[Neighbor] = Current
                    Distances[Neighbor] = Distance + 1
                    Pending.append(Neighbor)
            if Target not in Parents:
                return ()
            Result = []
            Current = Target
            while Current is not None and Current not in Starts:
                Result.append(Current)
                Current = Parents[Current]
            if Current is not None:
                Result.append(Current)
            return tuple(reversed(Result))

        def SelectBoundaryNodes(
            Nodes: frozenset[tuple[int, int, int]],
            AllTargets: list[tuple[int, int, int]],
            ConnectedTargets: list[tuple[int, int, int]],
        ) -> tuple[tuple[int, int, int], ...]:
            """Expose only deterministic continuation points for remote sinks."""
            Unresolved = sorted(set(AllTargets) - set(ConnectedTargets))
            return tuple(sorted({
                min(
                    Nodes,
                    key=lambda Position: (
                        abs(Target[0] - Position[0])
                        + abs(Target[1] - Position[1])
                        + abs(Target[2] - Position[2]),
                        Position,
                    ),
                )
                for Target in Unresolved
            }))

        def ValidateLocalSignalStrength(Candidate: LocalRouteClaim) -> None:
            """Reject local trees that require a repeater not yet reserved."""
            Graph: dict[tuple[int, int, int], set[tuple[int, int, int]]] = {
                Position: set() for Position in Candidate.Nodes
            }
            for First, Second in Candidate.Edges:
                Graph[First].add(Second)
                Graph[Second].add(First)
            Distances = {Candidate.Root: 0}
            Pending = deque((Candidate.Root,))
            while Pending:
                if len(Distances) % 256 == 0:
                    CheckWork(
                        "local-signal-strength",
                        Signal=Candidate.Signal,
                        CompletedNodes=len(Distances),
                        PendingNodes=len(Pending),
                    )
                Current = Pending.popleft()
                for Neighbor in Graph.get(Current, ()):
                    if Neighbor in Distances:
                        continue
                    Distances[Neighbor] = Distances[Current] + 1
                    Pending.append(Neighbor)
            MaximumDistance = max(
                (Distances.get(Target, 10**9) for Target in Candidate.ConnectedTargets),
                default=0,
            )
            if (
                MaximumDistance
                >= DefaultRedstoneRoutingTechnology.MaximumUnrefreshedDustLength
                and not Candidate.RepeaterReservations
            ):
                raise ValueError(
                    "Local route requires a repeater before its farthest sink: "
                    f"{Candidate.Signal} distance={MaximumDistance}"
                )

        def ValidateLocalPhysicalConnectivity(
            Candidate: LocalRouteClaim,
        ) -> None:
            """Reject local claims that are connected only in the abstract graph."""
            CandidateProducer = Producers.get(Candidate.Signal)
            if CandidateProducer is None:
                raise ValueError(
                    f"Local route has no producer: {Candidate.Signal}"
                )
            CandidateSupports = set(Candidate.Claims.SupportCells) - ActualBlocks
            PhysicalGraphs = BuildPhysicalGraphs(
                {Candidate.Signal: set(Candidate.Nodes)},
                ActualBlocks,
                CandidateSupports,
                SolidBlocks,
            )
            ValidatePhysicalRoutes(
                PhysicalGraphs,
                {Candidate.Signal: CandidateProducer},
                {
                    Candidate.Signal: list(Candidate.ConnectedTargets),
                },
            )

        def ValidateContinuationPortal(
            Candidate: LocalRouteClaim,
            AllTargets: list[tuple[int, int, int]],
        ) -> None:
            """Require an unclaimed legal frontier for every partial tree."""
            if set(AllTargets).issubset(Candidate.ConnectedTargets):
                return
            if not Candidate.BoundaryNodes:
                raise ValueError(
                    f"Partial local route has no continuation node: {Candidate.Signal}"
                )
            if (
                MaximumEntrancesPerSignal is not None
                and len(Candidate.BoundaryNodes) > MaximumEntrancesPerSignal
            ):
                raise ValueError(
                    "Partial local route exceeds per-signal entrance budget: "
                    f"{Candidate.Signal} entrances={len(Candidate.BoundaryNodes)}"
                )
            ForeignElectrical = set().union(*(
                Claim.Claims.ElectricalCells
                for Claim in LocalRouteClaims
                if Claim.Signal != Candidate.Signal
            )) if LocalRouteClaims else set()
            for Boundary in Candidate.BoundaryNodes:
                for Neighbor in sorted(
                    DefaultRedstoneRoutingTechnology.NeighborPositions(Boundary)
                ):
                    if Neighbor in Candidate.Nodes or Neighbor in ActualBlocks:
                        continue
                    if Neighbor in ForeignElectrical:
                        continue
                    if LocalResourceGraph.BuildPrimitive(Boundary, Neighbor) is not None:
                        return
            raise ValueError(
                f"Partial local route has no legal continuation portal: {Candidate.Signal}"
            )

        ClusterOrigins = {
            ClusterIndex: (
                min(
                    Gate.X for Gate in PlacedGates
                    if Gate.Name in ClusterNames
                ),
                min(
                    Gate.Y for Gate in PlacedGates
                    if Gate.Name in ClusterNames
                ),
                min(
                    Gate.Z for Gate in PlacedGates
                    if Gate.Name in ClusterNames
                ),
            )
            for ClusterIndex, ClusterNames in enumerate(Clusters)
        }

        def BuildClusterLocalRouteTemplateCacheKey(
            ClusterIndex: int,
        ) -> tuple[object, ...]:
            """Identify reusable internal routing independently of the slot."""
            Variant = SelectedClusterVariants[ClusterIndex]
            return (
                ClusterStructuralSignatures.get(ClusterIndex, ""),
                tuple(sorted(Clusters[ClusterIndex])),
                Variant.Rotation,
                Variant.MirrorX,
                repr(PackingPolicy),
            )

        ReusedLocalRouteSignals: set[str] = set()
        TemplateReuseDiagnostics: dict[str, object] = {
            "Enabled": EnableClusterLocalRouteReuse,
            "Clusters": {},
        }
        if EnableClusterLocalRouteReuse and not PlacementScoringOnly:
            for ClusterIndex, ClusterNames in enumerate(Clusters):
                CacheKey = BuildClusterLocalRouteTemplateCacheKey(ClusterIndex)
                Template = _ClusterLocalRouteTemplateCache.get(CacheKey)
                ClusterDiagnostic: dict[str, object] = {
                    "CacheKey": sha256(repr(CacheKey).encode("utf-8")).hexdigest(),
                }
                TemplateReuseDiagnostics["Clusters"][str(ClusterIndex)] = (
                    ClusterDiagnostic
                )
                if Template is None:
                    ClusterDiagnostic.update({"Cache": "miss"})
                    continue
                Delta = tuple(
                    ClusterOrigins[ClusterIndex][Axis] - Template.Origin[Axis]
                    for Axis in range(3)
                )
                TranslatedClaims = tuple(
                    TranslateClusterLocalRouteClaim(Claim, Delta)
                    for Claim in Template.Claims
                )
                try:
                    if not TranslatedClaims:
                        raise ValueError("template has no internal claims")
                    for Claim in TranslatedClaims:
                        Producer = Producers.get(Claim.Signal)
                        if (
                            Claim.ClusterId != ClusterIndex
                            or Producer is None
                            or Producer.OutputPin != Claim.Root
                            or any(
                                ClusterByGate.get(GateByInputPin.get(Target))
                                != ClusterIndex
                                for Target in Claim.ConnectedTargets
                            )
                        ):
                            raise ValueError("instantiated local topology differs")
                        ValidateLocalSignalStrength(Claim)
                        ValidateLocalPhysicalConnectivity(Claim)
                        ValidateContinuationPortal(
                            Claim,
                            TargetsBySignal.get(Claim.Signal, []),
                        )
                        ValidateBoundaryEscapes(Claim)
                    ValidateLocalRouteClaims(
                        LocalResourceGraph,
                        (*LocalRouteClaims, *TranslatedClaims),
                    )
                except ValueError as Error:
                    ClusterDiagnostic.update({
                        "Cache": "rejected",
                        "Validation": str(Error),
                    })
                    continue
                LocalRouteClaims.extend(TranslatedClaims)
                ReusedLocalRouteSignals.update(
                    Claim.Signal for Claim in TranslatedClaims
                )
                ClusterDiagnostic.update({
                    "Cache": "hit",
                    "Delta": list(Delta),
                    "ReusedLocalClaimCount": len(TranslatedClaims),
                    "RegeneratedBoundarySignals": sorted({
                        Claim.Signal
                        for Claim in TranslatedClaims
                        if not set(TargetsBySignal.get(Claim.Signal, ())).issubset(
                            Claim.ConnectedTargets
                        )
                    }),
                    "Validation": "accepted",
                })
        if EnableClusterLocalRouteReuse:
            LocalRouteDiagnostics["__ClusterLocalRouteTemplates__"] = (
                TemplateReuseDiagnostics
            )
        LocalRouteSignals = (
            ()
            if PlacementScoringOnly
            else sorted(
                TargetsBySignal.items(),
                key=lambda Value: (
                    0
                    if Producers.get(Value[0]) is not None
                    and Producers[Value[0]].Kind == "NAND"
                    else 1,
                    -len(set(Value[1])),
                    Value[0],
                ),
            )
        )
        for Signal, Targets in LocalRouteSignals:
            if Signal in ReusedLocalRouteSignals:
                continue
            CheckWork(
                "local-route-signal",
                Signal=Signal,
                TargetCount=len(Targets),
            )
            Producer = Producers.get(Signal)
            if Producer is None or not Targets:
                continue
            AllTargets = Targets
            ProducerCluster = ClusterByGate.get(Producer.Name)
            # A placement-owned tree is strictly local to its producer's
            # packed cluster.  Remote sinks remain terminal demand for the
            # authoritative global router and are represented only by the
            # continuation portal below.
            if ProducerCluster is not None:
                Targets = [
                    Target for Target in AllTargets
                    if ClusterByGate.get(GateByInputPin.get(Target))
                    == ProducerCluster
                ]
            if not Targets:
                continue
            if ShouldReleasePartialLocalTreeBeforeSearch(
                ClusterCount=len(Clusters),
                HasRelocationSignals=bool(RelocationSignals),
                LocalTargetCount=len(Targets),
                TotalTargetCount=len(AllTargets),
            ):
                # Feedback placements deliberately release every partial
                # inter-cluster tree to the global router below. Searching
                # and validating a local branch cannot change that verdict,
                # so avoid repeating bounded BFS work for every retained
                # slot/orientation state.
                LocalRouteDiagnostics.setdefault(Signal, {}).update({
                    "ReleasedForGlobalRelocation": (
                        ProducerCluster
                        if ProducerCluster is not None
                        else -1
                    ),
                    "ReleasedBeforeLocalSearch": True,
                })
                continue
            Root = Producer.OutputPin
            Paths = []
            LocalTargets = []
            for Target in Targets:
                CheckWork(
                    "local-route-direct-target",
                    Signal=Signal,
                    Target=Target,
                )
                DeltaX = Target[0] - Root[0]
                DeltaY = Target[1] - Root[1]
                DeltaZ = Target[2] - Root[2]
                Distance = abs(DeltaX) + abs(DeltaY) + abs(DeltaZ)
                if (
                    Distance > MaximumLength
                    or sum(Value != 0 for Value in (DeltaX, DeltaY, DeltaZ)) > 1
                ):
                    continue
                Step = (
                    0 if DeltaX == 0 else (1 if DeltaX > 0 else -1),
                    0 if DeltaY == 0 else (1 if DeltaY > 0 else -1),
                    0 if DeltaZ == 0 else (1 if DeltaZ > 0 else -1),
                )
                Paths.append(
                    tuple(
                        (
                            Root[0] + Step[0] * Offset,
                            Root[1] + Step[1] * Offset,
                            Root[2] + Step[2] * Offset,
                        )
                        for Offset in range(Distance + 1)
                    )
                )
                LocalTargets.append(Target)
            DirectPaths = list(Paths)
            DirectTargets = list(LocalTargets)
            OwnedNodes = {Position for Path in Paths for Position in Path} or {Root}
            RemainingTargets = sorted(
                set(Targets) - set(LocalTargets),
                key=lambda Target: (
                    min(
                        abs(Target[0] - Position[0])
                        + abs(Target[1] - Position[1])
                        + abs(Target[2] - Position[2])
                        for Position in OwnedNodes
                    ),
                    Target,
                ),
            )
            for Target in (
                RemainingTargets if MaximumLocalRouteLength > MaximumLength else ()
            ):
                CheckWork(
                    "local-route-search-target",
                    Signal=Signal,
                    Target=Target,
                )
                Distance = min(
                    abs(Target[0] - Position[0])
                    + abs(Target[1] - Position[1])
                    + abs(Target[2] - Position[2])
                    for Position in OwnedNodes
                )
                if Distance > MaximumLocalRouteLength:
                    continue
                Path = FindLocalPath(OwnedNodes, Target, Signal)
                if not Path:
                    continue
                Paths.append(Path)
                OwnedNodes.update(Path)
                LocalTargets.append(Target)
            if not Paths:
                continue
            Nodes = frozenset(Position for Path in Paths for Position in Path)
            Edges = frozenset(
                NormalizeRoutingEdge(First, Second)
                for Path in Paths
                for First, Second in zip(Path, Path[1:])
            )
            ClusterCandidates = [
                ClusterByGate[Name]
                for Target in LocalTargets
                if (Name := GateByInputPin.get(Target)) in ClusterByGate
            ]
            ClusterId = (
                ProducerCluster
                if ProducerCluster is not None
                else min(ClusterCandidates, default=-1)
            )
            CandidateClaim = LocalRouteClaim(
                Signal=Signal,
                ClusterId=ClusterId,
                Root=Root,
                ConnectedTargets=tuple(sorted(set(LocalTargets))),
                BoundaryNodes=SelectBoundaryNodes(
                    Nodes, AllTargets, LocalTargets
                ),
                Nodes=Nodes,
                Edges=Edges,
                Claims=LocalResourceGraph.BuildRouteClaims(Nodes),
                ExactRouteSignalBlocks=len(Nodes),
                ExactRouteSupportBlocks=len({
                    (X, Y - 1, Z) for X, Y, Z in Nodes
                } - ActualBlocks),
            )
            TrialClaims = (*LocalRouteClaims, CandidateClaim)
            try:
                ValidateLocalSignalStrength(CandidateClaim)
                ValidateLocalPhysicalConnectivity(CandidateClaim)
                ValidateContinuationPortal(CandidateClaim, AllTargets)
                ValidateBoundaryEscapes(CandidateClaim)
                ValidateLocalRouteClaims(LocalResourceGraph, TrialClaims)
                if any(len(Path) - 1 > MaximumLength for Path in Paths):
                    ValidateTemplateIsolation(
                        {Signal: set(CandidateClaim.Nodes)},
                        ActualBlocks,
                        ElectricalBlocks,
                        SolidBlocks,
                        Producers,
                        TargetsBySignal,
                        AccessBySignal,
                    )
            except ValueError as Error:
                LocalRouteDiagnostics[Signal] = {
                    "AttemptedTargets": len(set(LocalTargets)),
                    "AttemptedNodes": len(Nodes),
                    "Rejected": str(Error),
                }
                if not DirectPaths or len(DirectPaths) == len(Paths):
                    continue
                Paths = DirectPaths
                LocalTargets = DirectTargets
                Nodes = frozenset(
                    Position for Path in Paths for Position in Path
                )
                Edges = frozenset(
                    NormalizeRoutingEdge(First, Second)
                    for Path in Paths
                    for First, Second in zip(Path, Path[1:])
                )
                CandidateClaim = LocalRouteClaim(
                    Signal=Signal,
                    ClusterId=ClusterId,
                    Root=Root,
                    ConnectedTargets=tuple(sorted(set(LocalTargets))),
                    BoundaryNodes=SelectBoundaryNodes(
                    Nodes, AllTargets, LocalTargets
                    ),
                    Nodes=Nodes,
                    Edges=Edges,
                    Claims=LocalResourceGraph.BuildRouteClaims(Nodes),
                    ExactRouteSignalBlocks=len(Nodes),
                    ExactRouteSupportBlocks=len({
                        (X, Y - 1, Z) for X, Y, Z in Nodes
                    } - ActualBlocks),
                )
                try:
                    ValidateLocalSignalStrength(CandidateClaim)
                    ValidateLocalPhysicalConnectivity(CandidateClaim)
                    ValidateContinuationPortal(CandidateClaim, AllTargets)
                    ValidateBoundaryEscapes(CandidateClaim)
                    ValidateLocalRouteClaims(
                        LocalResourceGraph,
                        (*LocalRouteClaims, CandidateClaim),
                    )
                except ValueError:
                    continue
            if (
                PackingPolicy.RequireCompleteLocalFanoutClaims
                and len(Clusters) == 1
                and len(LocalTargets) != len(AllTargets)
            ):
                LocalRouteDiagnostics.setdefault(Signal, {}).update({
                    "ReleasedForCompleteFanout": ClusterId,
                })
                continue
            if (
                len(Clusters) > 4
                and RelocationSignals
                and len(LocalTargets) != len(AllTargets)
            ):
                LocalRouteDiagnostics.setdefault(Signal, {}).update({
                    "ReleasedForGlobalRelocation": ClusterId,
                })
                continue
            # Do not claim the tree greedily.  Keep the complete local tree
            # and its direct-only baseline for the bounded cluster assignment
            # below; the latter is often the compatible choice when a denser
            # neighbouring net needs the same clearance or support resource.
            CandidateChoices = JointLocalCandidatesByCluster.setdefault(
                ClusterId, {}
            ).setdefault(Signal, [])
            CandidateChoices.append(
                LocalClusterRouteCandidate(
                    CandidateId=(
                        f"cluster{ClusterId}:{Signal}:tree:"
                        f"{len(CandidateChoices)}"
                    ),
                    Claim=CandidateClaim,
                )
            )
            if (
                DirectPaths
                and (tuple(DirectTargets) != tuple(LocalTargets))
                and len(CandidateChoices)
                < PackingPolicy.MaximumLocalRouteCandidatesPerSignal
            ):
                DirectNodes = frozenset(
                    Position for Path in DirectPaths for Position in Path
                )
                DirectEdges = frozenset(
                    NormalizeRoutingEdge(First, Second)
                    for Path in DirectPaths
                    for First, Second in zip(Path, Path[1:])
                )
                DirectClaim = LocalRouteClaim(
                    Signal=Signal,
                    ClusterId=ClusterId,
                    Root=Root,
                    ConnectedTargets=tuple(sorted(set(DirectTargets))),
                    BoundaryNodes=SelectBoundaryNodes(
                        DirectNodes, AllTargets, DirectTargets
                    ),
                    Nodes=DirectNodes,
                    Edges=DirectEdges,
                    Claims=LocalResourceGraph.BuildRouteClaims(DirectNodes),
                    ExactRouteSignalBlocks=len(DirectNodes),
                    ExactRouteSupportBlocks=len({
                        (X, Y - 1, Z) for X, Y, Z in DirectNodes
                    } - ActualBlocks),
                )
                try:
                    ValidateLocalSignalStrength(DirectClaim)
                    ValidateLocalPhysicalConnectivity(DirectClaim)
                    ValidateContinuationPortal(DirectClaim, AllTargets)
                    ValidateBoundaryEscapes(DirectClaim)
                    ValidateLocalRouteClaims(
                        LocalResourceGraph, (DirectClaim,)
                    )
                except ValueError as Error:
                    LocalRouteDiagnostics.setdefault(Signal, {}).setdefault(
                        "DirectCandidateRejected", str(Error)
                    )
                else:
                    CandidateChoices.append(
                        LocalClusterRouteCandidate(
                            CandidateId=(
                                f"cluster{ClusterId}:{Signal}:direct:"
                                f"{len(CandidateChoices)}"
                            ),
                            Claim=DirectClaim,
                        )
                    )
            LocalRouteDiagnostics.setdefault(Signal, {}).update({
                "AcceptedTargets": len(set(LocalTargets)),
                "AcceptedNodes": len(Nodes),
                "UsedLongRoute": any(
                    len(Path) - 1 > MaximumLength for Path in Paths
                ),
            })
        if PlacementScoringOnly:
            LocalRouteDiagnostics["__DeferredLocalRouting__"] = {
                "Enabled": True,
                "ScoringOnly": True,
                "TerminalsIncluded": True,
                "FixedPinAccessClaimsIncluded": True,
                "LocalRouteCandidateSearchDeferred": True,
                "LocalRoutePathSearchDeferred": True,
            }
        elif PackingPolicy.EnableJointLocalRouting:
            JointDiagnostics: dict[str, object] = {
                "Enabled": True,
                "CandidateLimitPerSignal": (
                    PackingPolicy.MaximumLocalRouteCandidatesPerSignal
                ),
                "AssignmentExpansionLimit": (
                    PackingPolicy.MaximumLocalClusterAssignmentExpansions
                ),
                "Clusters": {},
            }
            for ClusterId, CandidateMap in sorted(JointLocalCandidatesByCluster.items()):
                BaseClaims = tuple(LocalRouteClaims)
                LimitedCandidateMap = {
                    Signal: tuple(Candidates[:PackingPolicy.MaximumLocalRouteCandidatesPerSignal])
                    for Signal, Candidates in sorted(CandidateMap.items())
                }
                Selection = SelectJointLocalClusterCandidates(
                    LocalResourceGraph,
                    BaseClaims,
                    LimitedCandidateMap,
                    PackingPolicy.MaximumLocalClusterAssignmentExpansions,
                )
                SelectedClaims = tuple(
                    Candidate.Claim for Candidate in Selection.Candidates
                )
                LocalRouteClaims.extend(SelectedClaims)
                JointDiagnostics["Clusters"][str(ClusterId)] = {
                    "AttemptedSignals": len(LimitedCandidateMap),
                    "AttemptedCandidates": sum(
                        len(Candidates)
                        for Candidates in LimitedCandidateMap.values()
                    ),
                    "SelectedCandidates": len(Selection.Candidates),
                    "LocalizedTargets": sum(
                        Candidate.LocalizedTargetCount
                        for Candidate in Selection.Candidates
                    ),
                    "LocalRepeaters": sum(
                        Candidate.RepeaterCount for Candidate in Selection.Candidates
                    ),
                    "RouteAndSupportBlocks": sum(
                        Candidate.RouteAndSupportBlocks
                        for Candidate in Selection.Candidates
                    ),
                    "AssignmentExpansions": Selection.AssignmentExpansions,
                    "BudgetExhausted": Selection.BudgetExhausted,
                    "RejectionCounts": Selection.RejectionCounts,
                }
            FullyLocalizedSignals = {
                Claim.Signal
                for Claim in LocalRouteClaims
                if set(TargetsBySignal.get(Claim.Signal, ())).issubset(
                    Claim.ConnectedTargets
                )
            }
            JointDiagnostics["Aggregate"] = {
                "CandidateCount": sum(
                    sum(len(Candidates) for Candidates in CandidateMap.values())
                    for CandidateMap in JointLocalCandidatesByCluster.values()
                ),
                "LocalClaimCoverageBefore": 0,
                "LocalClaimCoverageAfter": sum(
                    len(Claim.Claims.ResourceIds) for Claim in LocalRouteClaims
                ),
                "SelectedClaimCount": len(LocalRouteClaims),
                "LocalizedTargetCount": sum(
                    len(Claim.ConnectedTargets) for Claim in LocalRouteClaims
                ),
                "GlobalNetCountBefore": len(TargetsBySignal),
                "GlobalNetCountAfter": len(TargetsBySignal) - len(FullyLocalizedSignals),
                "GlobalNetCountReduction": len(FullyLocalizedSignals),
                "EstimatedLocalVolume": sum(
                    LocalClusterRouteCandidate("selected", Claim).FullVolume
                    for Claim in LocalRouteClaims
                ),
            }
            LocalRouteDiagnostics["__JointLocalRouting__"] = JointDiagnostics
        else:
            # Compatibility mode retains the original deterministic signal
            # ordering when a caller explicitly opts out of joint selection.
            for CandidateMap in JointLocalCandidatesByCluster.values():
                for Candidates in CandidateMap.values():
                    if Candidates:
                        LocalRouteClaims.append(Candidates[0].Claim)
        for CandidateClaim in LocalRouteClaims:
            Signal = CandidateClaim.Signal
            LocalNetBranches[Signal] = tuple(sorted(CandidateClaim.Nodes))
            LocalNetTargets[Signal] = tuple(sorted(CandidateClaim.ConnectedTargets))
            if (
                len(CandidateClaim.ConnectedTargets) == len(TargetsBySignal[Signal])
                and len(CandidateClaim.Nodes)
                <= PackingPolicy.MaximumFrozenLocalNetNodes
                and len(CandidateClaim.ConnectedTargets)
                <= PackingPolicy.MaximumFrozenLocalTargets
            ):
                FrozenNetWires[Signal] = LocalNetBranches[Signal]
        Placed.FrozenNetWires = FrozenNetWires
        Placed.LocalNetBranches = LocalNetBranches
        Placed.LocalNetTargets = LocalNetTargets
        Placed.LocalRouteClaims = tuple(LocalRouteClaims)
        if RelocationSignals or AssignmentCut is not None:
            LocalRouteDiagnostics["__PlacementRelocation__"] = {
                "Signals": sorted(RelocationSignals),
                "PrioritySignals": sorted(RelocationPrioritySignals),
                "RequiredSignals": sorted(RequiredRelocationSignals),
                "Variant": RelocationVariant,
                "Clusters": sorted(PhysicallyRelocatedClusters),
                "MirroredClusters": sorted(MirroredRelocationClusters),
                "AssignmentCut": (
                    AssignmentCut.ToDictionary()
                    if AssignmentCut is not None
                    else None
                ),
                "ActivePlacementConstraints": (
                    AssignmentConstraints.ToDictionary()
                ),
                "CoordinatedCandidateDiversificationSignals": sorted(
                    CoordinatedCandidateDiversificationSignals
                ),
                "CoordinatedCandidateDiversityLevel": (
                    1
                    if CoordinatedCandidateDiversificationSignals
                    else 0
                ),
                "InternalPinBankGeometryRepair": {
                    "Enabled": EnableInternalPinBankGeometryRepair,
                    "Signals": sorted(InternalPinBankGeometrySignals),
                },
            }
        Placed.LocalRouteDiagnostics = LocalRouteDiagnostics
    if RoutingSpacing == 0:
        Placed = CompactWeightedPlacement(
            Module,
            Placed,
            MaximumPasses=(
                PlacementPolicy.CompactPassLimit
                if PlacementPolicy is not None
                else 32
            ),
            WorkCheck=WorkCheck,
        )
    Guided = AddPcbRoutingGuides(
        Placed,
        MaximumLayerCount=(
            PlacementPolicy.MaximumRoutingLayers
            if PlacementPolicy is not None
            else 0
        ),
    )
    GateByName = {Gate.Name: Gate for Gate in PlacedGates}
    ConsumersBySignal: dict[str, list[Any]] = {}
    ProducersBySignal = {
        Signal: Gate
        for Gate in Module.Gates
        for Signal in Gate.Outputs
    }
    for Gate in Module.Gates:
        for Signal in Gate.Inputs:
            ConsumersBySignal.setdefault(Signal, []).append(Gate)
    PackedClusters = []
    ClaimsByCluster: dict[int, list[LocalRouteClaim]] = {}
    for Claim in Placed.LocalRouteClaims:
        ClaimsByCluster.setdefault(Claim.ClusterId, []).append(Claim)
    CutBoundaryEscapeSignals = (
        frozenset(BuildAssignmentCutHigherOrderSignalSet(AssignmentCut))
        if EnableClusterInterfacePlacementFeasibility
        else frozenset()
    )
    CutBoundaryEscapeDomains: dict[
        tuple[int, str],
        tuple[BoundaryEscapeCandidate, ...],
    ] = {}
    for ClusterIndex, Names in enumerate(Clusters):
        CheckWork(
            "boundary-capacity",
            CompletedClusters=ClusterIndex,
            TotalClusters=len(Clusters),
        )
        NameSet = set(Names)
        Produced = {
            Signal
            for Name in Names
            for Signal in InternalByName[Name].Outputs
        }
        InternalSignals = {
            Signal
            for Signal in Produced
            if any(Gate.Name in NameSet for Gate in ConsumersBySignal.get(Signal, ()))
            and all(Gate.Name in NameSet for Gate in ConsumersBySignal.get(Signal, ()))
        }
        BoundarySignals = {
            Signal
            for Name in Names
            for Signal in (*InternalByName[Name].Inputs, *InternalByName[Name].Outputs)
            if Signal not in InternalSignals
        }
        DirectConnections = []
        for Signal in sorted(InternalSignals):
            Producer = next(
                GateByName[Name]
                for Name in Names
                if Signal in GateByName[Name].Outputs
            )
            if any(
                Producer.OutputPin in Consumer.InputPins
                for Consumer in (GateByName[Gate.Name] for Gate in ConsumersBySignal[Signal])
            ):
                DirectConnections.append(Signal)
        BaseX = min(GateByName[Name].X for Name in Names)
        BaseZ = min(GateByName[Name].Z for Name in Names)
        MaximumClusterX = max(
            GateByName[Name].X
            + RotatedCellSize(
                GateByName[Name].Kind,
                GateByName[Name].Rotation,
            )[0]
            - 1
            for Name in Names
        )
        MaximumClusterZ = max(
            GateByName[Name].Z
            + RotatedCellSize(
                GateByName[Name].Kind,
                GateByName[Name].Rotation,
            )[1]
            - 1
            for Name in Names
        )
        ClusterCenterX = (BaseX + MaximumClusterX) / 2
        ClusterCenterZ = (BaseZ + MaximumClusterZ) / 2

        def PreferredBoundarySide(Signal: str) -> str:
            ExternalGates = [
                GateByName[Gate.Name]
                for Gate in ConsumersBySignal.get(Signal, ())
                if Gate.Name not in NameSet and Gate.Name in GateByName
            ]
            Producer = ProducersBySignal.get(Signal)
            if (
                Producer is not None
                and Producer.Name not in NameSet
                and Producer.Name in GateByName
            ):
                ExternalGates.append(GateByName[Producer.Name])
            if not ExternalGates:
                return "East"
            TargetX = sum(Gate.X for Gate in ExternalGates) / len(ExternalGates)
            TargetZ = sum(Gate.Z for Gate in ExternalGates) / len(ExternalGates)
            DeltaX = TargetX - ClusterCenterX
            DeltaZ = TargetZ - ClusterCenterZ
            if abs(DeltaX) >= abs(DeltaZ):
                return "East" if DeltaX >= 0 else "West"
            return "South" if DeltaZ >= 0 else "North"

        BoundaryDemand = {
            Signal: max(
                1,
                sum(
                    Consumer.Name not in NameSet
                    for Consumer in ConsumersBySignal.get(Signal, ())
                ),
            )
            for Signal in sorted(BoundarySignals)
        }
        BoundaryDemandRecords = tuple(
            BoundaryDemandRecord(
                Signal=Signal,
                UnresolvedTargets=BoundaryDemand[Signal],
                RequiredPortalSlots=1,
                RequiredCorridorLanes=1,
                PreferredBoundarySide=PreferredBoundarySide(Signal),
            )
            for Signal in sorted(BoundarySignals)
        )
        BoundaryPitch = (
            PlacementPolicy.DemandAwareBoundaryTrackPitch
            if (
                PlacementPolicy is not None
                and PlacementPolicy.EnableDemandAwareInterClusterSpacing
                and PlacementPolicy.DemandAwareBoundaryTrackPitch > 0
            )
            else DefaultRedstoneRoutingTechnology.TrackPitch
        )
        BoundaryLayerCapacity = (
            PlacementPolicy.MaximumRoutingLayers
            if PlacementPolicy is not None
            and PlacementPolicy.MaximumRoutingLayers > 0
            else DefaultRedstoneRoutingTechnology.MaximumRoutableLayerCount
        )
        GeometricCapacity = {
            "West": max(1, (MaximumClusterZ - BaseZ + 1) // BoundaryPitch)
            * BoundaryLayerCapacity,
            "East": max(1, (MaximumClusterZ - BaseZ + 1) // BoundaryPitch)
            * BoundaryLayerCapacity,
            "North": max(1, (MaximumClusterX - BaseX + 1) // BoundaryPitch)
            * BoundaryLayerCapacity,
            "South": max(1, (MaximumClusterX - BaseX + 1) // BoundaryPitch)
            * BoundaryLayerCapacity,
        }
        LegalPortalSlotsBySide = dict(GeometricCapacity)
        LegalEscapeCandidateCounts: tuple[tuple[str, int], ...] = ()
        if PackedMode:
            AccessPositionsBySignal = {
                Signal: set(
                    AccessByClusterSignal.get((ClusterIndex, Signal), ())
                )
                for Signal in BoundarySignals
            }
            BoundaryEscapeCandidatesBySignal: dict[
                str, list[BoundaryEscapeCandidate]
            ] = {}
            LegalEscapeSlotsBySignal = BuildLegalBoundaryEscapeSlots(
                BoundarySignals,
                AccessPositionsBySignal,
                LocalResourceGraph,
                AccessClaimsBySignal,
                WorkCheck=WorkCheck,
                CandidateClaimsBySignal=(
                    BoundaryEscapeCandidatesBySignal
                    if CutBoundaryEscapeSignals
                    else None
                ),
            )
            for Signal in sorted(
                CutBoundaryEscapeSignals.intersection(BoundarySignals)
            ):
                CutBoundaryEscapeDomains[(ClusterIndex, Signal)] = tuple(
                    BoundaryEscapeCandidatesBySignal.get(Signal, ())
                )
            HardBoundary = EvaluateHardBoundaryFeasibility(
                ClusterIndex,
                BoundaryDemandRecords,
                LegalEscapeSlotsBySignal,
            )
            LegalEscapeCandidateCounts = (
                HardBoundary.LegalEscapeCandidateCounts
            )
            ValidateHardBoundaryFeasibility(HardBoundary)
            SlotsBySide = {
                "West": set(),
                "East": set(),
                "North": set(),
                "South": set(),
            }
            for X, Y, Z in {
                Slot
                for Slots in LegalEscapeSlotsBySignal.values()
                for Slot in Slots
            }:
                Side = min(
                    (
                        (abs(X - BaseX), "West"),
                        (abs(X - MaximumClusterX), "East"),
                        (abs(Z - BaseZ), "North"),
                        (abs(Z - MaximumClusterZ), "South"),
                    )
                )[1]
                SlotsBySide[Side].add((X, Y, Z))
            LegalPortalSlotsBySide = {
                Side: len(Slots) for Side, Slots in SlotsBySide.items()
            }
            BoundaryDemandRecords = AssignBoundaryDemandSides(
                BoundaryDemandRecords,
                LegalEscapeSlotsBySignal,
                (BaseX, MaximumClusterX, BaseZ, MaximumClusterZ),
                {
                    Side: min(
                        GeometricCapacity[Side],
                        LegalPortalSlotsBySide[Side],
                    )
                    for Side in GeometricCapacity
                },
            )
        BoundaryCapacityRecords = BuildBoundaryCapacityRecords(
            BoundaryDemandRecords,
            GeometricCapacity,
            LegalPortalSlotsBySide,
        )
        BoundaryOverflow = sum(
            Record.Overflow for Record in BoundaryCapacityRecords
        )
        ScarceSides = {
            Record.BoundarySide
            for Record in BoundaryCapacityRecords
            if Record.Overflow > 0
        }
        PinScarcityCount = sum(
            Record.PreferredBoundarySide in ScarceSides
            for Record in BoundaryDemandRecords
        )
        LocalClaimTargets = sum(
            len(Claim.ConnectedTargets)
            for Claim in ClaimsByCluster.get(ClusterIndex, ())
        )
        BoundaryTargetCount = sum(BoundaryDemand.values())
        PackedClusters.append(
            PackedNandCluster(
                ClusterId=ClusterIndex,
                MemberNands=tuple(Names),
                BoundarySignals=tuple(sorted(BoundarySignals)),
                InternalSignals=tuple(sorted(InternalSignals)),
                RelativePlacements={
                    Name: (
                        GateByName[Name].X - BaseX,
                        GateByName[Name].Z - BaseZ,
                        GateByName[Name].Rotation,
                        GateByName[Name].MirrorX,
                    )
                    for Name in Names
                },
                DirectConnections=tuple(DirectConnections),
                LocalClaimSignals=tuple(sorted({
                    Claim.Signal for Claim in ClaimsByCluster.get(ClusterIndex, ())
                })),
                BoundaryTerminals=tuple(sorted({
                    Position
                    for Claim in ClaimsByCluster.get(ClusterIndex, ())
                    for Position in Claim.BoundaryNodes
                })),
                ExactLocalRoutingBlocks=sum(
                    Claim.ExactRoutingBlocks
                    for Claim in ClaimsByCluster.get(ClusterIndex, ())
                ),
                GlobalEntrances=len(BoundarySignals),
                StructuralSignature=ClusterStructuralSignatures.get(
                    ClusterIndex, ""
                ),
                ReusedFromClusterId=ClusterReuseSources.get(ClusterIndex),
                StructuralMapping=ClusterStructuralMappings.get(ClusterIndex),
                StackId=ClusterStackIds.get(ClusterIndex),
                StackLevel=ClusterStackLevels.get(ClusterIndex, 0),
                BaseY=(
                    1
                    if not PackedMode
                    else 1
                    + ClusterStackLevels.get(ClusterIndex, 0)
                    * PackingPolicy.ClusterDeckPitch
                ),
                BoundaryDemand=BoundaryDemand,
                EstimatedCorridorLanes=sum(BoundaryDemand.values()),
                LocalClaimCoverage=(
                    LocalClaimTargets / max(1, LocalClaimTargets + BoundaryTargetCount)
                ),
                BoundaryDemandRecords=BoundaryDemandRecords,
                BoundaryCapacityRecords=BoundaryCapacityRecords,
                BoundaryOverflow=BoundaryOverflow,
                PinScarcityCount=PinScarcityCount,
                LegalEscapeCandidateCounts=LegalEscapeCandidateCounts,
                OrientationRotation=SelectedClusterVariants[ClusterIndex].Rotation,
                OrientationMirrorX=SelectedClusterVariants[ClusterIndex].MirrorX,
            )
        )
    if CutBoundaryEscapeSignals:
        CutBoundaryEscapeFeasibility = (
            EvaluateCutBoundaryEscapeFeasibility(
                CutBoundaryEscapeDomains,
                CutBoundaryEscapeSignals,
            )
        )
        Guided.Placed.LocalRouteDiagnostics.setdefault(
            "__CutBoundaryEscapeFeasibility__",
            CutBoundaryEscapeFeasibility.ToDictionary(),
        )
    if Guided.Placed.LocalRouteDiagnostics is None:
        Guided.Placed.LocalRouteDiagnostics = {}
    Guided.Placed.LocalRouteDiagnostics["__ComponentGraph__"] = (
        LogicalComponentGraph.ToDictionary()
    )
    CheckWork("complete", ClusterCount=len(Clusters))
    BoundaryLeaseRequests = (
        BuildClusterBoundaryLeaseRequests(
            BuildClusterBoundaryBundles(Module, Clusters),
            Assignment,
            Module=Module,
            Clusters=Clusters,
            PlacedGates=Guided.Placed.PlacedGates,
            IncludePrimaryTerminals=(
                EnableClusterInterfacePlacementFeasibility
            ),
        )
        if PackedMode and EnableClusterBoundaryLeases
        else ()
    )
    ClusterLocalRouteTemplates = tuple(
        ClusterLocalRouteTemplate(
            ClusterId=Cluster.ClusterId,
            StructuralSignature=Cluster.StructuralSignature,
            Rotation=Cluster.OrientationRotation,
            MirrorX=Cluster.OrientationMirrorX,
            Origin=(
                min(
                    Gate.X for Gate in Guided.Placed.PlacedGates
                    if Gate.Name in Cluster.MemberNands
                ),
                Cluster.BaseY,
                min(
                    Gate.Z for Gate in Guided.Placed.PlacedGates
                    if Gate.Name in Cluster.MemberNands
                ),
            ),
            LocalClaimFingerprint=sha256(repr(tuple(sorted(
                (
                    Claim.Signal,
                    Claim.Root,
                    Claim.ConnectedTargets,
                    Claim.BoundaryNodes,
                    tuple(sorted(Claim.Nodes)),
                    tuple(sorted(Claim.Edges)),
                )
                for Claim in Guided.Placed.LocalRouteClaims
                if Claim.ClusterId == Cluster.ClusterId
            ))).encode("utf-8")).hexdigest(),
            BoundaryTerminalFingerprint=sha256(repr(
                Cluster.BoundaryTerminals
            ).encode("utf-8")).hexdigest(),
            ClaimCount=sum(
                Claim.ClusterId == Cluster.ClusterId
                for Claim in Guided.Placed.LocalRouteClaims
            ),
            BoundaryTerminalCount=len(Cluster.BoundaryTerminals),
        )
        for Cluster in PackedClusters
    ) if PackedMode else ()
    if PackedMode and not PlacementScoringOnly:
        for ClusterIndex, Cluster in enumerate(PackedClusters):
            Claims = tuple(
                Claim
                for Claim in Guided.Placed.LocalRouteClaims
                if Claim.ClusterId == ClusterIndex
                and all(
                    ClusterByGate.get(GateByInputPin.get(Target)) == ClusterIndex
                    for Target in Claim.ConnectedTargets
                )
            )
            if not Claims:
                continue
            CacheKey = BuildClusterLocalRouteTemplateCacheKey(ClusterIndex)
            LocalClaimFingerprint = sha256(repr(tuple(sorted(
                (
                    Claim.Signal,
                    Claim.Root,
                    Claim.ConnectedTargets,
                    Claim.BoundaryNodes,
                    tuple(sorted(Claim.Nodes)),
                    tuple(sorted(Claim.Edges)),
                )
                for Claim in Claims
            ))).encode("utf-8")).hexdigest()
            _ClusterLocalRouteTemplateCache[CacheKey] = (
                ClusterLocalRouteTemplateCacheEntry(
                    CacheKey=CacheKey,
                    Origin=ClusterOrigins[ClusterIndex],
                    Claims=Claims,
                    LocalClaimFingerprint=LocalClaimFingerprint,
                )
            )
        if EnableClusterLocalRouteReuse:
            Guided.Placed.LocalRouteDiagnostics.setdefault(
                "__ClusterLocalRouteTemplates__", {}
            )["CacheEntryCount"] = len(_ClusterLocalRouteTemplateCache)
    return PcbPlacement(
        Placed=PlacedDesign(
            Module=Guided.Placed.Module,
            PlacedGates=Guided.Placed.PlacedGates,
            RouteGuides=Guided.Placed.RouteGuides,
            RouteLayers=Guided.Placed.RouteLayers,
            FrozenNetWires=Guided.Placed.FrozenNetWires,
            LocalNetBranches=Guided.Placed.LocalNetBranches,
            LocalNetTargets=Guided.Placed.LocalNetTargets,
            LocalRouteClaims=Guided.Placed.LocalRouteClaims,
            LocalRouteDiagnostics=Guided.Placed.LocalRouteDiagnostics,
            DerivedPerimeterSlotDomain=(
                Guided.Placed.DerivedPerimeterSlotDomain
            ),
            DerivedPerimeterSlotAssignment=(
                Guided.Placed.DerivedPerimeterSlotAssignment
            ),
            ClusterBoundaryLeaseRequests=BoundaryLeaseRequests,
            CompleteClusterInterfaceAccess=(
                EnableClusterInterfacePlacementFeasibility
            ),
            ComponentGraph=LogicalComponentGraph,
        ),
        Clusters=Clusters,
        SignalOrder=Guided.SignalOrder,
        LayerCount=Guided.LayerCount,
        PackedClusters=tuple(PackedClusters) if PackedMode else (),
        ClusterBoundaryLeaseRequests=BoundaryLeaseRequests,
        ClusterLocalRouteTemplates=ClusterLocalRouteTemplates,
        CompleteClusterInterfaceAccess=(
            EnableClusterInterfacePlacementFeasibility
        ),
        DerivedPerimeterSlotDomain=(
            Guided.Placed.DerivedPerimeterSlotDomain
        ),
        DerivedPerimeterSlotAssignment=(
            Guided.Placed.DerivedPerimeterSlotAssignment
        ),
        ComponentGraph=LogicalComponentGraph,
    )
