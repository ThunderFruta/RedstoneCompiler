"""Placement constraints and exact joint-state contracts."""

from __future__ import annotations

from dataclasses import (
    dataclass,
)
from hashlib import (
    sha256,
)
from typing import (
    Any,
    Iterable,
)
from Compiler.Placement.Geometry import (
    BuildPlacedGate,
    PlacedGate,
)
from Compiler.Routing.Failures import (
    RoutingAssignmentCut,
)


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
