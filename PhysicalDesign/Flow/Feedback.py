"""Routing-failure interpretation and proof-guided placement feedback."""

from __future__ import annotations

from dataclasses import (
    dataclass,
)
from typing import (
    Any,
    Iterable,
    Mapping,
)
from PhysicalDesign.Contracts.Placement import ClusterInterfacePortfolioStateAudit
from PhysicalDesign.Contracts.Failures import RoutingAssignmentCut, RoutingAssignmentCutClassification, RoutingFailure, RoutingFailureReason
from PhysicalDesign.Execution.Reliability import BuildStableFingerprint
from PhysicalDesign.Placement.Core.Clusters import PcbPlacement
from PhysicalDesign.Placement.Core.Constraints import BuildAssignmentCutHigherOrderSignalSet, PlacementAssignmentConstraintSet
from .Portfolios import (
    AssignmentCutHasBoundedExactCore,
)
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .Candidates import (
        PcbPlacementCandidate,
    )


def FailureRequestsPlacementAdvance(Failure: RoutingFailure) -> bool:
    """Return whether a typed failure forbids same-candidate recovery work."""
    Diagnostics = Failure.Diagnostics or {}
    Action = str(Diagnostics.get("Action", ""))
    ConflictGraph = Diagnostics.get("ConflictGraph", {})
    return (
        Action.startswith("advance-placement")
        or Failure.Reason == RoutingFailureReason.NoPinAccessPattern
        or Failure.Reason
        == RoutingFailureReason.RepeaterAccessInfeasible
        or any(
            str(RepairAction).startswith("AdvancePlacement")
            for RepairAction in Failure.RepairActions
        )
        or (
            isinstance(ConflictGraph, dict)
            and ConflictGraph.get("Classification")
            == "mandatory-boundary-capacity-cut"
        )
    )

def FailurePrefersDirectOnlyPlacement(
    Failure: RoutingFailure,
    Candidate: PcbPlacementCandidate,
) -> bool:
    """Prefer fewer pre-owned local claims after one exact higher-order cut."""
    if (
        Candidate.SourceGenerator != "row-beam"
        or not Candidate.Placement.Placed.LocalRouteClaims
        or any((
            Candidate.BoundaryOverflow,
            Candidate.PinScarcityCount,
            Candidate.GuideOverflowPeak,
            Candidate.GuideOverflowCells,
            Candidate.PinEscapeConflictCount,
        ))
        or Failure.Reason != RoutingFailureReason.TrackAssignmentConflict
        or Failure.Stage != "TrackAssignment"
        or not FailureRequestsPlacementAdvance(Failure)
    ):
        return False
    ConflictGraph = (Failure.Diagnostics or {}).get("ConflictGraph", {})
    if not isinstance(ConflictGraph, dict):
        return False
    return (
        ConflictGraph.get("Classification")
        == "higher-order-placement-conflict"
        and not ConflictGraph.get("NoCandidateSignals")
        and not ConflictGraph.get("PairwiseIncompatibleEdges")
        and bool(ExtractCandidateStarvationSignals(Failure))
    )

def ExtractCandidateStarvationSignals(
    Failure: RoutingFailure,
) -> frozenset[str]:
    """Return signals repeatedly proved empty during exact candidate repair."""
    EscalationHistory = (Failure.Diagnostics or {}).get(
        "EscalationHistory",
        (),
    )
    if not isinstance(EscalationHistory, tuple | list):
        return frozenset()
    Signals: set[str] = set()
    for Entry in EscalationHistory:
        if (
            not isinstance(Entry, dict)
            or str(Entry.get("Stage", "")) != "CandidateGeneration"
        ):
            continue
        EntryDiagnostics = Entry.get("Diagnostics", {})
        if (
            not isinstance(EntryDiagnostics, dict)
            or int(EntryDiagnostics.get("Materialized", -1)) != 0
        ):
            continue
        AffectedSignals = Entry.get("AffectedSignals", ())
        if isinstance(AffectedSignals, tuple | list):
            Signals.update(str(Signal) for Signal in AffectedSignals)
    return frozenset(sorted(Signals))

def FailureRequiresPackedAccessRepair(Failure: RoutingFailure) -> bool:
    """Return whether a typed fixed-access cut requires local geometry repair."""
    ConflictGraph = (Failure.Diagnostics or {}).get("ConflictGraph", {})
    Classification = (
        str(ConflictGraph.get("Classification", ""))
        if isinstance(ConflictGraph, dict)
        else ""
    )
    return (
        Failure.Reason in {
            RoutingFailureReason.NoPinAccessPattern,
            RoutingFailureReason.RepeaterAccessInfeasible,
        }
        or Classification in {
            "mandatory-access-self-conflict",
            "mandatory-boundary-capacity-cut",
            "portal-coverage-pair-conflict",
            "relocated-higher-order-conflict",
            "relocated-larger-matching-failure",
            "relocated-multi-pair-conflict",
            "relocated-pairwise-incompatibility",
        }
    )

def ExpandAnalogousMandatoryRepairSignals(
    Module: Any,
    Signals: frozenset[str],
) -> frozenset[str]:
    """Expand one external fixed-access cut across equivalent gate motifs."""
    if len(Signals) < 2:
        return Signals
    ExternalInputs = frozenset(str(Signal) for Signal in Module.Inputs)
    Fanout = {
        Signal: sum(
            Gate.Inputs.count(Signal)
            for Gate in Module.Gates
        )
        for Signal in ExternalInputs
    }
    Patterns: set[tuple[object, int, tuple[int, ...], tuple[int, ...]]] = set()
    for Gate in Module.Gates:
        Positions = tuple(
            Index
            for Index, Signal in enumerate(Gate.Inputs)
            if Signal in Signals
        )
        if len(Positions) < 2:
            continue
        Patterns.add((
            getattr(Gate.Kind, "value", Gate.Kind),
            len(Gate.Inputs),
            Positions,
            tuple(Fanout.get(Gate.Inputs[Index], 0) for Index in Positions),
        ))
    if not Patterns:
        return Signals
    Expanded = set(Signals)
    for Gate in Module.Gates:
        Kind = getattr(Gate.Kind, "value", Gate.Kind)
        for PatternKind, Arity, Positions, PatternFanout in Patterns:
            if Kind != PatternKind or len(Gate.Inputs) != Arity:
                continue
            CandidateSignals = tuple(
                str(Gate.Inputs[Index]) for Index in Positions
            )
            if (
                all(Signal in ExternalInputs for Signal in CandidateSignals)
                and tuple(
                    Fanout.get(Signal, 0)
                    for Signal in CandidateSignals
                )
                == PatternFanout
            ):
                Expanded.update(CandidateSignals)
    return frozenset(Expanded)

def ExtractPlacementRelocationSignals(
    Failure: RoutingFailure,
) -> frozenset[str]:
    """Return typed routing offenders that should alter later placement."""
    # AffectedNets is allowed to describe the larger assignment frontier.  A
    # structured conflict graph is the more precise physical diagnosis, so do
    # not turn a three-net conflict back into a broad cluster move by unioning
    # the whole frontier into it.
    Signals: set[str] = set()
    Diagnostics = Failure.Diagnostics or {}
    ConflictGraph = Diagnostics.get("ConflictGraph", {})
    if isinstance(ConflictGraph, dict):
        RelocationValues = ConflictGraph.get("RelocationSignals", ())
        if isinstance(RelocationValues, tuple | list) and RelocationValues:
            return frozenset(str(Value) for Value in RelocationValues)
        for Key in (
            "ConflictSignals",
            "NativeConflictSignals",
            "NoCandidateSignals",
            "CumulativeConflictSignals",
            "CongestionCutSignals",
            "ConflictCutSignals",
        ):
            Values = ConflictGraph.get(Key, ())
            if isinstance(Values, tuple | list):
                Signals.update(str(Value) for Value in Values)
        Rebalancing = ConflictGraph.get("ConflictResources", ())
        if isinstance(Rebalancing, dict):
            Signals.update(
                str(Signal)
                for SignalsForResource in Rebalancing.values()
                if isinstance(SignalsForResource, tuple | list)
                for Signal in SignalsForResource
            )
        Pairwise = ConflictGraph.get("PairwiseIncompatibleEdges", ())
        if isinstance(Pairwise, tuple | list):
            Signals.update(
                str(Signal)
                for Pair in Pairwise
                if isinstance(Pair, tuple | list)
                for Signal in Pair
            )
    for Key in ("ConflictSignals", "NativeConflictSignals"):
        Values = Diagnostics.get(Key, ())
        if isinstance(Values, tuple | list):
            Signals.update(str(Value) for Value in Values)
    if not Signals:
        Signals.update(str(Value) for Value in Failure.AffectedNets)
    return frozenset(sorted(Signals))

def ExtractCompletedEscalationRelocationSignals(
    Failure: RoutingFailure,
) -> frozenset[str]:
    """Recover the latest exact cut completed before an interrupted escalation."""
    if Failure.Reason not in {
        RoutingFailureReason.RuntimeBudgetExceeded,
        RoutingFailureReason.Stagnated,
    }:
        return frozenset()
    Diagnostics = Failure.Diagnostics or {}
    EscalationHistory = Diagnostics.get("EscalationHistory", ())
    if not isinstance(EscalationHistory, tuple | list):
        return frozenset()
    RelocatedClassifications = {
        "portal-coverage-pair-conflict",
        "relocated-higher-order-conflict",
        "relocated-larger-matching-failure",
        "relocated-multi-pair-conflict",
        "relocated-pairwise-incompatibility",
    }
    for Entry in reversed(EscalationHistory):
        if not isinstance(Entry, dict):
            continue
        if (
            str(Entry.get("Stage", "")) != "TrackAssignment"
            or str(Entry.get("ConflictClassification", ""))
            not in RelocatedClassifications
            or (
                str(Entry.get("Decision", ""))
                != "RegenerateAffectedCandidates"
                and str(Entry.get("Action", ""))
                != "regenerate-affected-candidates"
            )
        ):
            continue
        for Key in (
            "PriorityRelocationSignals",
            "RelocationSignals",
            "AffectedSignals",
        ):
            Values = Entry.get(Key, ())
            if isinstance(Values, tuple | list) and Values:
                return frozenset(str(Signal) for Signal in Values)
    return frozenset()

_HigherOrderAssignmentCutClassifications = frozenset({
    RoutingAssignmentCutClassification.SaturatedBoundaryCut,
    RoutingAssignmentCutClassification.HigherOrderPlacementConflict,
    RoutingAssignmentCutClassification.LargerMatchingFailure,
    RoutingAssignmentCutClassification.MultiPairPlacementConflict,
    RoutingAssignmentCutClassification.RelocatedHigherOrderConflict,
    RoutingAssignmentCutClassification.RelocatedLargerMatchingFailure,
    RoutingAssignmentCutClassification.RelocatedMultiPairConflict,
    RoutingAssignmentCutClassification.RelocatedPairwiseIncompatibility,
})

_ImmediateAssignmentCutRelocationClassifications = frozenset({
    RoutingAssignmentCutClassification.SaturatedBoundaryCut,
    RoutingAssignmentCutClassification.MandatoryAccessSelfConflict,
    RoutingAssignmentCutClassification.MandatoryBoundaryCapacityCut,
    RoutingAssignmentCutClassification.PortalCoveragePairConflict,
    RoutingAssignmentCutClassification.RelocatedPairwiseIncompatibility,
})

def IsHigherOrderAssignmentCut(
    AssignmentCut: RoutingAssignmentCut | None,
) -> bool:
    """Return whether a complete assignment cut requires joint relocation."""
    return (
        AssignmentCut is not None
        and AssignmentCut.Classification
        in _HigherOrderAssignmentCutClassifications
    )

def ShouldUseCurrentAssignmentCutGeometry(
    Requested: bool,
    SourceGenerator: str,
    AssignmentCut: RoutingAssignmentCut | None,
) -> bool:
    """Keep higher-order relocation geometry scoped to its current cut."""
    return bool(
        Requested
        or (
            SourceGenerator == "row-beam-conflict-relocation"
            and IsHigherOrderAssignmentCut(AssignmentCut)
        )
    )

def SelectAssignmentCutGeometrySignals(
    *,
    TopologyRequiresJointPortfolio: bool,
    AssignmentCut: RoutingAssignmentCut | None,
    CompleteCutSignals: Iterable[str],
    PriorityCutSignals: Iterable[str],
) -> frozenset[str]:
    """Select relocation geometry without dropping a topology cut endpoint."""
    CompleteSignals = frozenset(map(str, CompleteCutSignals))
    PrioritySignals = frozenset(map(str, PriorityCutSignals))
    if (
        TopologyRequiresJointPortfolio
        and IsHigherOrderAssignmentCut(AssignmentCut)
    ):
        return CompleteSignals
    return (
        PrioritySignals
        if IsHigherOrderAssignmentCut(AssignmentCut) and PrioritySignals
        else CompleteSignals
    )

def BuildSignalTopologyFingerprints(
    Module: Any,
) -> dict[str, str]:
    """Color signals by anonymous directed topology, independent of names."""
    Gates = tuple(getattr(Module, "Gates", ()))
    InputSignals = frozenset(map(str, getattr(Module, "Inputs", ())))
    OutputSignals = frozenset(map(str, getattr(Module, "Outputs", ())))
    GateInputs = tuple(
        tuple(map(str, getattr(Gate, "Inputs", ())))
        for Gate in Gates
    )
    GateOutputs = tuple(
        tuple(map(str, getattr(Gate, "Outputs", ())))
        for Gate in Gates
    )
    Signals = frozenset((
        *InputSignals,
        *OutputSignals,
        *(
            Signal
            for Inputs in GateInputs
            for Signal in Inputs
        ),
        *(
            Signal
            for Outputs in GateOutputs
            for Signal in Outputs
        ),
    ))
    ProducersBySignal: dict[str, set[int]] = {
        Signal: set()
        for Signal in Signals
    }
    ConsumersBySignal: dict[str, set[int]] = {
        Signal: set()
        for Signal in Signals
    }
    for GateIndex, (Inputs, Outputs) in enumerate(
        zip(GateInputs, GateOutputs, strict=True)
    ):
        for Signal in Inputs:
            ConsumersBySignal[Signal].add(GateIndex)
        for Signal in Outputs:
            ProducersBySignal[Signal].add(GateIndex)

    GateKinds = tuple(
        str(getattr(getattr(Gate, "Kind", "NAND"), "value", getattr(
            Gate,
            "Kind",
            "NAND",
        )))
        for Gate in Gates
    )
    GateColors = {
        GateIndex: BuildStableFingerprint({
            "Kind": GateKinds[GateIndex],
            "InputCount": len(GateInputs[GateIndex]),
            "OutputCount": len(GateOutputs[GateIndex]),
        })
        for GateIndex in range(len(Gates))
    }
    SignalColors = {
        Signal: BuildStableFingerprint({
            "InputTerminal": Signal in InputSignals,
            "OutputTerminal": Signal in OutputSignals,
            "ProducerCount": len(ProducersBySignal[Signal]),
            "ConsumerCount": len(ConsumersBySignal[Signal]),
        })
        for Signal in Signals
    }
    # Fixed-depth color refinement avoids relying on declaration order or
    # generated identifiers, while still distinguishing directed cut roles.
    for _ in range(max(1, len(Gates) + len(Signals))):
        NextGateColors = {
            GateIndex: BuildStableFingerprint({
                "Kind": GateKinds[GateIndex],
                "Inputs": sorted(
                    SignalColors[Signal]
                    for Signal in GateInputs[GateIndex]
                ),
                "Outputs": sorted(
                    SignalColors[Signal]
                    for Signal in GateOutputs[GateIndex]
                ),
            })
            for GateIndex in range(len(Gates))
        }
        NextSignalColors = {
            Signal: BuildStableFingerprint({
                "InputTerminal": Signal in InputSignals,
                "OutputTerminal": Signal in OutputSignals,
                "Producers": sorted(
                    GateColors[GateIndex]
                    for GateIndex in ProducersBySignal[Signal]
                ),
                "Consumers": sorted(
                    GateColors[GateIndex]
                    for GateIndex in ConsumersBySignal[Signal]
                ),
            })
            for Signal in Signals
        }
        GateColors = NextGateColors
        SignalColors = NextSignalColors
    return SignalColors


def BuildSignalLocalIncidenceFingerprints(
    Module: Any,
) -> dict[str, str]:
    """Color signals by terminal role and one-hop anonymous gate shape."""
    Gates = tuple(getattr(Module, "Gates", ()))
    InputSignals = frozenset(map(str, getattr(Module, "Inputs", ())))
    OutputSignals = frozenset(map(str, getattr(Module, "Outputs", ())))
    Signals = frozenset((
        *InputSignals,
        *OutputSignals,
        *(
            str(Signal)
            for Gate in Gates
            for Signal in getattr(Gate, "Inputs", ())
        ),
        *(
            str(Signal)
            for Gate in Gates
            for Signal in getattr(Gate, "Outputs", ())
        ),
    ))
    ProducerShapesBySignal = {Signal: [] for Signal in Signals}
    ConsumerShapesBySignal = {Signal: [] for Signal in Signals}
    for Gate in Gates:
        GateShape = (
            str(getattr(
                getattr(Gate, "Kind", "NAND"),
                "value",
                getattr(Gate, "Kind", "NAND"),
            )),
            len(getattr(Gate, "Inputs", ())),
            len(getattr(Gate, "Outputs", ())),
        )
        for Signal in map(str, getattr(Gate, "Inputs", ())):
            ConsumerShapesBySignal[Signal].append(GateShape)
        for Signal in map(str, getattr(Gate, "Outputs", ())):
            ProducerShapesBySignal[Signal].append(GateShape)
    return {
        Signal: BuildStableFingerprint({
            "InputTerminal": Signal in InputSignals,
            "OutputTerminal": Signal in OutputSignals,
            "ProducerShapes": sorted(ProducerShapesBySignal[Signal]),
            "ConsumerShapes": sorted(ConsumerShapesBySignal[Signal]),
        })
        for Signal in Signals
    }

def SelectCutDrivenClusterRefinementSignals(
    AssignmentCut: RoutingAssignmentCut | None,
    SignalTopologyFingerprints: Mapping[str, str],
    MaximumSignals: int = 4,
    Constraints: PlacementAssignmentConstraintSet = (
        PlacementAssignmentConstraintSet()
    ),
) -> frozenset[str]:
    """Select a bounded exact/observed interface neighborhood for reclustering."""
    if MaximumSignals < 2:
        return frozenset()
    RawEdges = (
        AssignmentCut.PairwiseConflictEdges
        if (
            AssignmentCut is not None
            and AssignmentCut.PairwiseConflictEdges
        )
        else tuple((
            *(
                tuple(map(str, Edge))
                for Edge in (
                    AssignmentCut.ConflictGraph.get(
                        "ObservedPatternConflictEdges",
                        (),
                    )
                    if AssignmentCut is not None
                    else ()
                )
                if isinstance(Edge, tuple | list) and len(Edge) == 2
            ),
            *Constraints.PairwiseConflictEdges,
            *Constraints.ActiveObservedInterfaceConflictEdges,
        ))
    )
    Edges = tuple(sorted({
        tuple(sorted((str(First), str(Second))))
        for First, Second in RawEdges
        if str(First) and str(Second) and str(First) != str(Second)
    }))
    if not Edges:
        return frozenset()
    Degree = {
        Signal: sum(Signal in Edge for Edge in Edges)
        for Edge in Edges
        for Signal in Edge
    }

    def StructuralSignalKey(Signal: str) -> tuple[str, str]:
        return (
            SignalTopologyFingerprints.get(Signal, ""),
            Signal,
        )

    OrderedEdges = sorted(
        Edges,
        key=lambda Edge: (
            -max(Degree[Edge[0]], Degree[Edge[1]]),
            -(Degree[Edge[0]] + Degree[Edge[1]]),
            tuple(sorted(map(StructuralSignalKey, Edge))),
        ),
    )
    Selected: set[str] = set()
    for Edge in OrderedEdges:
        NewSignals = set(Edge) - Selected
        if len(Selected) + len(NewSignals) > MaximumSignals:
            continue
        Selected.update(Edge)
        if len(Selected) >= MaximumSignals:
            break
    return frozenset(Selected)

def BuildStructuralHigherOrderAssignmentCutFingerprint(
    AssignmentCut: RoutingAssignmentCut | None,
    SignalTopologyFingerprints: Mapping[str, str],
) -> str:
    """Fingerprint a higher-order cut without dynamic resources or names."""
    if not IsHigherOrderAssignmentCut(AssignmentCut):
        return ""
    assert AssignmentCut is not None
    ClassificationFamilies = {
        RoutingAssignmentCutClassification.SaturatedBoundaryCut: (
            "saturated-boundary-cut"
        ),
        RoutingAssignmentCutClassification.HigherOrderPlacementConflict: (
            "higher-order-placement-conflict"
        ),
        RoutingAssignmentCutClassification.RelocatedHigherOrderConflict: (
            "higher-order-placement-conflict"
        ),
        RoutingAssignmentCutClassification.LargerMatchingFailure: (
            "larger-matching-failure"
        ),
        RoutingAssignmentCutClassification.RelocatedLargerMatchingFailure: (
            "larger-matching-failure"
        ),
        RoutingAssignmentCutClassification.MultiPairPlacementConflict: (
            "multi-pair-placement-conflict"
        ),
        RoutingAssignmentCutClassification.RelocatedMultiPairConflict: (
            "multi-pair-placement-conflict"
        ),
        RoutingAssignmentCutClassification
        .RelocatedPairwiseIncompatibility: "pairwise-incompatibility",
    }
    UnknownSignalFingerprint = BuildStableFingerprint({
        "AnonymousSignalRole": "unknown",
    })

    def SignalFingerprint(Signal: str) -> str:
        return SignalTopologyFingerprints.get(
            str(Signal),
            UnknownSignalFingerprint,
        )

    ConflictSignals = (
        AssignmentCut.ConflictSignals
        or AssignmentCut.NoCandidateSignals
        or AssignmentCut.RelocationSignals
    )
    return BuildStableFingerprint({
        "Classification": ClassificationFamilies[
            AssignmentCut.Classification
        ],
        "ConflictSignalTopology": sorted(
            SignalFingerprint(Signal)
            for Signal in ConflictSignals
        ),
        "NoCandidateSignalTopology": sorted(
            SignalFingerprint(Signal)
            for Signal in AssignmentCut.NoCandidateSignals
        ),
        "PairwiseConflictTopology": sorted(
            tuple(sorted((
                SignalFingerprint(First),
                SignalFingerprint(Second),
            )))
            for First, Second in AssignmentCut.PairwiseConflictEdges
        ),
    })

def ShouldDiversifyRepeatedAssignmentCut(
    History: list[RoutingAssignmentCut] | tuple[RoutingAssignmentCut, ...],
    Current: RoutingAssignmentCut | None,
    SignalTopologyFingerprints: Mapping[str, str] | None = None,
) -> bool:
    """Detect one structural cut repeated by access-distinct placements."""
    if (
        not IsHigherOrderAssignmentCut(Current)
        or Current is None
        or not Current.AccessTopologyFingerprint
    ):
        return False
    CurrentFingerprint = (
        BuildStructuralHigherOrderAssignmentCutFingerprint(
            Current,
            SignalTopologyFingerprints,
        )
        if SignalTopologyFingerprints is not None
        else Current.ConflictFingerprint
    )
    if not CurrentFingerprint:
        return False
    return any(
        IsHigherOrderAssignmentCut(Previous)
        and (
            BuildStructuralHigherOrderAssignmentCutFingerprint(
                Previous,
                SignalTopologyFingerprints,
            )
            if SignalTopologyFingerprints is not None
            else Previous.ConflictFingerprint
        )
        == CurrentFingerprint
        and bool(Previous.AccessTopologyFingerprint)
        and Previous.AccessTopologyFingerprint
        != Current.AccessTopologyFingerprint
        for Previous in History
    )

def ShouldDeferTopologyCutForMaterializedSibling(
    *,
    Requested: bool,
    TopologyAccessRepairEligible: bool,
    CommittedHistory: Iterable[RoutingAssignmentCut],
    DeferredCuts: Iterable[RoutingAssignmentCut],
    Current: RoutingAssignmentCut,
    SignalTopologyFingerprints: Mapping[str, str],
    AllowRepeatedCutCommit: bool = True,
) -> bool:
    """Stop a retained portfolio once it proves one repeated exact cut.

    Access-distinct siblings are valuable until two of them return the same
    anonymous higher-order cut, two exact higher-order cuts share a stable
    two-signal core, or two candidates expose the same authoritative access
    domain for the same structural cut. At that point another access-equivalent
    sibling is lower-value than committing both cuts to a fresh geometry
    epoch. The overlap is scheduling evidence only; it is never promoted into
    a fabricated exact pair.
    """
    if Requested and not AllowRepeatedCutCommit:
        return True
    PriorCuts = (
        *tuple(CommittedHistory),
        *tuple(DeferredCuts),
    )
    RepeatedExactCut = ShouldDiversifyRepeatedAssignmentCut(
        PriorCuts,
        Current,
        SignalTopologyFingerprints,
    )
    CurrentStructuralFingerprint = (
        BuildStructuralHigherOrderAssignmentCutFingerprint(
            Current,
            SignalTopologyFingerprints,
        )
    )
    RepeatedEquivalentAccessDomainCut = bool(
        CurrentStructuralFingerprint
        and Current.AuthoritativeAccessDomainFingerprint
        and any(
            BuildStructuralHigherOrderAssignmentCutFingerprint(
                Prior,
                SignalTopologyFingerprints,
            )
            == CurrentStructuralFingerprint
            and Prior.AuthoritativeAccessDomainFingerprint
            == Current.AuthoritativeAccessDomainFingerprint
            for Prior in PriorCuts
        )
    )
    CurrentHigherOrderSignals = frozenset(
        BuildAssignmentCutHigherOrderSignalSet(Current)
    )
    OverlappingAccessDistinctCut = bool(
        len(CurrentHigherOrderSignals) >= 3
        and Current.AccessTopologyFingerprint
        and any(
            len(
                CurrentHigherOrderSignals.intersection(
                    BuildAssignmentCutHigherOrderSignalSet(Prior)
                )
            ) >= 2
            and bool(Prior.AccessTopologyFingerprint)
            and Prior.AccessTopologyFingerprint
            != Current.AccessTopologyFingerprint
            for Prior in PriorCuts
        )
    )
    return bool(
        Requested
        and not (
            TopologyAccessRepairEligible
            and (
                RepeatedExactCut
                or RepeatedEquivalentAccessDomainCut
                or OverlappingAccessDistinctCut
            )
        )
    )

def SelectRefinedAssignmentCutDiversificationSignals(
    History: list[RoutingAssignmentCut] | tuple[RoutingAssignmentCut, ...],
    Current: RoutingAssignmentCut | None,
) -> frozenset[str]:
    """Select one exact pair refined from an access-distinct higher-order cut."""
    if (
        Current is None
        or Current.Classification
        not in {
            RoutingAssignmentCutClassification.MandatoryBoundaryCapacityCut,
            RoutingAssignmentCutClassification.PortalCoveragePairConflict,
            RoutingAssignmentCutClassification.PairwiseIncompatibility,
            RoutingAssignmentCutClassification.RelocatedPairwiseIncompatibility,
        }
        or not Current.MandatoryAccessOwnershipFingerprint
    ):
        return frozenset()

    CurrentPair = frozenset()
    if len(Current.PairwiseConflictEdges) == 1:
        CurrentPair = frozenset(Current.PairwiseConflictEdges[0])
    if len(CurrentPair) != 2:
        for ReportedSignals in (
            Current.ConflictSignals,
            Current.RelocationSignals,
            Current.PriorityRelocationSignals,
        ):
            CandidatePair = frozenset(ReportedSignals)
            if len(CandidatePair) == 2:
                CurrentPair = CandidatePair
                break
    if len(CurrentPair) != 2:
        return frozenset()

    for Previous in History:
        if (
            not IsHigherOrderAssignmentCut(Previous)
            or not Previous.MandatoryAccessOwnershipFingerprint
            or Previous.MandatoryAccessOwnershipFingerprint
            == Current.MandatoryAccessOwnershipFingerprint
        ):
            continue
        PreviousSignals = frozenset((
            *Previous.ConflictSignals,
            *Previous.RelocationSignals,
            *Previous.PriorityRelocationSignals,
            *Previous.NoCandidateSignals,
            *(
                Signal
                for Edge in Previous.PairwiseConflictEdges
                for Signal in Edge
            ),
        ))
        if CurrentPair.issubset(PreviousSignals):
            return CurrentPair
    return frozenset()

def SelectRepeatedAssignmentSubcutDiversificationSignals(
    History: list[RoutingAssignmentCut] | tuple[RoutingAssignmentCut, ...],
    Current: RoutingAssignmentCut | None,
) -> frozenset[str]:
    """Select exact pair edges repeated by access-distinct placements.

    A repeated physical pair can be embedded in a larger multi-pair cut, so
    comparing only whole cut fingerprints or requiring exactly one current
    edge misses the actionable common subcut.  Preserve pair grouping while
    returning only endpoints whose exact incompatibility survived a changed
    mandatory-access ownership topology.
    """
    PairClassifications = {
        RoutingAssignmentCutClassification.MandatoryBoundaryCapacityCut,
        RoutingAssignmentCutClassification.PortalCoveragePairConflict,
        RoutingAssignmentCutClassification.PairwiseIncompatibility,
        RoutingAssignmentCutClassification.RelocatedPairwiseIncompatibility,
        RoutingAssignmentCutClassification.MultiPairPlacementConflict,
        RoutingAssignmentCutClassification.RelocatedMultiPairConflict,
    }
    if (
        Current is None
        or Current.Classification not in PairClassifications
        or not Current.MandatoryAccessOwnershipFingerprint
        or not Current.PairwiseConflictEdges
    ):
        return frozenset()
    CurrentEdges = frozenset(Current.PairwiseConflictEdges)
    RepeatedEdges: set[tuple[str, str]] = set()
    for Previous in History:
        if (
            Previous.Classification not in PairClassifications
            or not Previous.MandatoryAccessOwnershipFingerprint
            or Previous.MandatoryAccessOwnershipFingerprint
            == Current.MandatoryAccessOwnershipFingerprint
        ):
            continue
        RepeatedEdges.update(
            CurrentEdges.intersection(Previous.PairwiseConflictEdges)
        )
    return frozenset(
        Signal
        for Edge in RepeatedEdges
        for Signal in Edge
    )

def SelectCumulativeRepeatedAssignmentCutDiversificationSignals(
    History: list[RoutingAssignmentCut] | tuple[RoutingAssignmentCut, ...],
    ActiveConstraints: PlacementAssignmentConstraintSet,
) -> frozenset[str]:
    """Retain active pair endpoints repeated across access topologies.

    Placement repair remains focused on the newest exact cut.  Routing-domain
    diversification is cumulative because a later cut must not erase an
    earlier active pair whose incompatibility survived a different mandatory
    access ownership topology.
    """
    ActiveEdges = frozenset(
        tuple(sorted((str(First), str(Second))))
        for First, Second in ActiveConstraints.PairwiseConflictEdges
        if str(First) != str(Second)
    )
    if not ActiveEdges:
        return frozenset()

    OwnershipsByEdge: dict[tuple[str, str], set[str]] = {
        Edge: set() for Edge in ActiveEdges
    }
    for Cut in History:
        OwnershipFingerprint = (
            Cut.MandatoryAccessOwnershipFingerprint
        )
        if not OwnershipFingerprint:
            continue
        for First, Second in Cut.PairwiseConflictEdges:
            Edge = tuple(sorted((str(First), str(Second))))
            if Edge in OwnershipsByEdge:
                OwnershipsByEdge[Edge].add(OwnershipFingerprint)

    return frozenset(
        Signal
        for Edge, OwnershipFingerprints in OwnershipsByEdge.items()
        if len(OwnershipFingerprints) >= 2
        for Signal in Edge
    )

@dataclass(frozen=True)
class CandidateStarvationPlacementEvidence:
    """One completed empty-window observation within an immutable cut epoch."""

    AssignmentCutFingerprint: str
    AssignmentConstraintFingerprint: str
    AssignmentCut: RoutingAssignmentCut

def BuildCandidateStarvationPlacementEvidence(
    AssignmentCut: RoutingAssignmentCut | None,
    *,
    AssignmentCutFingerprint: str,
    AssignmentConstraintFingerprint: str,
) -> CandidateStarvationPlacementEvidence | None:
    """Bind one empty candidate domain to its originating geometry epoch.

    Retained access-distinct siblings may defer their cuts until the bounded
    portfolio finishes. Their starvation evidence is still authoritative
    within the immutable parent cut and constraint epoch.
    """
    if (
        AssignmentCut is None
        or AssignmentCut.Classification
        != RoutingAssignmentCutClassification
        .CandidateStarvationPlacementConflict
        or not AssignmentCutFingerprint
        or not AssignmentConstraintFingerprint
        or not AssignmentCut.MandatoryAccessOwnershipFingerprint
    ):
        return None
    return CandidateStarvationPlacementEvidence(
        AssignmentCutFingerprint=AssignmentCutFingerprint,
        AssignmentConstraintFingerprint=AssignmentConstraintFingerprint,
        AssignmentCut=AssignmentCut,
    )

def SelectRepeatedCandidateStarvationDiversificationSignals(
    History: (
        list[CandidateStarvationPlacementEvidence]
        | tuple[CandidateStarvationPlacementEvidence, ...]
    ),
    Current: RoutingAssignmentCut | None,
    *,
    AssignmentCutFingerprint: str,
    AssignmentConstraintFingerprint: str,
) -> frozenset[str]:
    """Select signals proven empty across access-distinct sibling placements."""
    if (
        Current is None
        or Current.Classification
        != RoutingAssignmentCutClassification
        .CandidateStarvationPlacementConflict
        or not AssignmentCutFingerprint
        or not AssignmentConstraintFingerprint
        or not Current.MandatoryAccessOwnershipFingerprint
    ):
        return frozenset()

    CurrentSignals = (
        frozenset(Current.NoCandidateSignals)
        or frozenset(Current.ConflictSignals)
    )
    if not CurrentSignals:
        return frozenset()

    RepeatedSignals: set[str] = set()
    for Evidence in History:
        Previous = Evidence.AssignmentCut
        if (
            Evidence.AssignmentCutFingerprint
            != AssignmentCutFingerprint
            or Evidence.AssignmentConstraintFingerprint
            != AssignmentConstraintFingerprint
            or Previous.Classification
            != RoutingAssignmentCutClassification
            .CandidateStarvationPlacementConflict
            or not Previous.MandatoryAccessOwnershipFingerprint
            or Previous.MandatoryAccessOwnershipFingerprint
            == Current.MandatoryAccessOwnershipFingerprint
        ):
            continue
        PreviousSignals = (
            frozenset(Previous.NoCandidateSignals)
            or frozenset(Previous.ConflictSignals)
        )
        RepeatedSignals.update(CurrentSignals.intersection(PreviousSignals))
    return frozenset(RepeatedSignals)

def RequiresImmediateAssignmentCutRelocation(
    AssignmentCut: RoutingAssignmentCut | None,
) -> bool:
    """Return whether exact cut feedback should preempt stale placements."""
    return (
        IsHigherOrderAssignmentCut(AssignmentCut)
        or (
            AssignmentCut is not None
            and AssignmentCut.Classification
            in _ImmediateAssignmentCutRelocationClassifications
        )
    )

def ShouldPreserveCurrentStructuredAssignmentCut(
    Current: RoutingAssignmentCut | None,
    Constraints: PlacementAssignmentConstraintSet,
    Reported: RoutingAssignmentCut | None,
) -> bool:
    """Keep a live exact cut when starvation adds no placement constraint."""
    return (
        Current is not None
        and Reported is not None
        and RequiresImmediateAssignmentCutRelocation(Current)
        and Reported.Classification
        == RoutingAssignmentCutClassification
        .CandidateStarvationPlacementConflict
        and Constraints.WithCut(Reported) == Constraints
    )

def BuildStructuredPlacementRelocationSignals(
    AssignmentCut: RoutingAssignmentCut | None,
    Constraints: PlacementAssignmentConstraintSet,
) -> frozenset[str]:
    """Project immutable cut evidence into the complete relocation cover."""
    CutSignals = (
        (
            *AssignmentCut.ConflictSignals,
            *AssignmentCut.RelocationSignals,
            *AssignmentCut.PriorityRelocationSignals,
            *AssignmentCut.NoCandidateSignals,
            *(
                Signal
                for Edge in AssignmentCut.PairwiseConflictEdges
                for Signal in Edge
            ),
        )
        if AssignmentCut is not None
        else ()
    )
    return frozenset((
        *CutSignals,
        *(
            Signal
            for Signals in Constraints.HigherOrderSignalSets
            for Signal in Signals
        ),
        *(
            Signal
            for Edge in Constraints.PairwiseConflictEdges
            for Signal in Edge
        ),
    ))

def BuildCurrentAssignmentCutRelocationSignals(
    AssignmentCut: RoutingAssignmentCut | None,
) -> frozenset[str]:
    """Return the complete signal cover reported by one exact cut."""
    if AssignmentCut is None:
        return frozenset()
    return frozenset((
        *AssignmentCut.ConflictSignals,
        *AssignmentCut.RelocationSignals,
        *AssignmentCut.PriorityRelocationSignals,
        *AssignmentCut.NoCandidateSignals,
        *(
            Signal
            for Edge in AssignmentCut.PairwiseConflictEdges
            for Signal in Edge
        ),
    ))

def BuildTopologyCutEpochGeometryRelocationSignals(
    AssignmentCut: RoutingAssignmentCut | None,
    ProvenLeaseGeometrySignals: Iterable[str] = (),
) -> frozenset[str]:
    """Combine the current cut with its proved unrealizable lease endpoints."""
    return frozenset((
        *BuildCurrentAssignmentCutRelocationSignals(AssignmentCut),
        *map(str, ProvenLeaseGeometrySignals),
    ))

def SelectRepeatedLeaseRealizabilityGeometrySignals(
    Failure: RoutingFailure,
    MinimumDistinctPatterns: int = 2,
    CompletePortfolioPatternCount: int = 2,
) -> frozenset[str]:
    """Promote the complete exhausted lease endpoint set to geometry repair."""
    if (
        MinimumDistinctPatterns < 1
        or CompletePortfolioPatternCount < 1
        or Failure.Reason != RoutingFailureReason.TrackAssignmentConflict
    ):
        return frozenset()
    Diagnostics = (
        Failure.Diagnostics
        if isinstance(Failure.Diagnostics, dict)
        else {}
    )
    ConflictGraph = Diagnostics.get("ConflictGraph", {})
    if (
        not isinstance(ConflictGraph, dict)
        or ConflictGraph.get("Classification")
        != "candidate-starvation-placement-conflict"
        or Diagnostics.get("Action")
        != "advance-placement-after-complete-cluster-lease-portfolio"
    ):
        return frozenset()
    CutSignals = frozenset(map(str, (
        *ConflictGraph.get("ConflictSignals", ()),
        *ConflictGraph.get("NoCandidateSignals", ()),
    )))
    PatternsBySignal: dict[str, set[str]] = {}
    DistinctPatternEntries: set[tuple[str, str]] = set()
    for RawNogood in Diagnostics.get(
        "CandidateRealizabilityNogoods",
        (),
    ):
        if not isinstance(RawNogood, dict):
            continue
        Signal = str(RawNogood.get("Signal", ""))
        Pattern = str(RawNogood.get("PatternFingerprint", ""))
        if Signal and Pattern:
            PatternsBySignal.setdefault(Signal, set()).add(Pattern)
            DistinctPatternEntries.add((Signal, Pattern))
    RepeatedCutSignals = frozenset(
        Signal
        for Signal, Patterns in PatternsBySignal.items()
        if Signal in CutSignals
        if len(Patterns) >= MinimumDistinctPatterns
    )
    CompletePortfolioExhausted = (
        len(DistinctPatternEntries) >= CompletePortfolioPatternCount
    )
    if not RepeatedCutSignals and not CompletePortfolioExhausted:
        return frozenset()
    return frozenset((
        *CutSignals,
        *PatternsBySignal,
    ))

def BuildTopologyCutEpochPinBankRelocationSignals(
    BaseSignals: Iterable[str],
    PinBankRepairSignals: Iterable[str],
    EnableInternalPinBankGeometryRepair: bool,
) -> frozenset[str]:
    """Add only the current pin-bank endpoints to a targeted cut epoch.

    The structured assignment cut remains the cumulative legality/scoring
    basis.  A proven internal pin-bank retry must nevertheless put its newly
    starved endpoints into the physical relocation identity; otherwise the
    epoch can reproduce the same placed geometry while changing only routing
    candidate domains.
    """
    Signals = frozenset(map(str, BaseSignals))
    if not EnableInternalPinBankGeometryRepair:
        return Signals
    return frozenset((
        *Signals,
        *map(str, PinBankRepairSignals),
    ))

def BuildTopologyCutEpochGeometryConstraints(
    AssignmentCut: RoutingAssignmentCut | None,
    CumulativeConstraints: PlacementAssignmentConstraintSet = (
        PlacementAssignmentConstraintSet()
    ),
) -> PlacementAssignmentConstraintSet:
    """Score the current cut while retaining recurrent placement evidence.

    Relocation remains scoped to the current cut's endpoints. The immutable
    cumulative constraints still govern legality and scoring so repairing one
    interface cannot recreate a repeatedly observed conflict elsewhere.
    """
    return CumulativeConstraints.WithCut(AssignmentCut)

def SelectTopologyCutFrontier(
    CurrentCut: RoutingAssignmentCut | None,
    CutHistory: Iterable[RoutingAssignmentCut],
    Enabled: bool,
    MaximumCuts: int = 2,
) -> tuple[RoutingAssignmentCut, ...]:
    """Select the current cut and one distinct recent bounded predecessor."""
    if not Enabled or CurrentCut is None or MaximumCuts <= 0:
        return ()

    def CutIdentity(Cut: RoutingAssignmentCut) -> tuple[str, str]:
        PublishedIdentity = (
            Cut.ConflictFingerprint or Cut.EffectiveWorkFingerprint
        )
        return (
            Cut.Classification.value,
            PublishedIdentity or BuildStableFingerprint(Cut.ToDictionary()),
        )

    Selected = [CurrentCut]
    Seen = {CutIdentity(CurrentCut)}
    for PriorCut in reversed(tuple(CutHistory)):
        Identity = CutIdentity(PriorCut)
        if Identity in Seen or not AssignmentCutHasBoundedExactCore(PriorCut):
            continue
        Selected.append(PriorCut)
        Seen.add(Identity)
        if len(Selected) >= MaximumCuts:
            break
    return tuple(Selected)

def BuildPlacementFingerprint(
    Placement: PcbPlacement,
    MandatoryAccessOwnershipFingerprint: str = "",
    IncludeLocalClaims: bool = True,
) -> str:
    """Fingerprint exact geometry and claims for provenance and artifacts."""
    return BuildStableFingerprint({
        "Gates": [
            (
                Gate.Name,
                Gate.Kind,
                Gate.X,
                Gate.Y,
                Gate.Z,
                Gate.Rotation,
                getattr(Gate, "MirrorX", False),
            )
            for Gate in sorted(
                Placement.Placed.PlacedGates,
                key=lambda Value: Value.Name,
            )
        ],
        "LocalClaims": [
            (
                Claim.Signal,
                Claim.ClusterId,
                tuple(sorted(Claim.Nodes)),
            )
            for Claim in sorted(
                (
                    Placement.Placed.LocalRouteClaims or ()
                    if IncludeLocalClaims
                    else ()
                ),
                key=lambda Value: (Value.Signal, Value.ClusterId),
            )
        ],
        "MandatoryAccessOwnershipFingerprint": (
            MandatoryAccessOwnershipFingerprint
        ),
        "InterClusterChannelFingerprint": (
            getattr(
                getattr(
                    Placement,
                    "InterClusterRoutingChannel",
                    None,
                ),
                "ChannelFingerprint",
                "",
            )
        ),
    })

def SelectInterfaceDiversePlacementStates(
    Candidates: Iterable[PcbPlacementCandidate],
    MaximumStates: int = 6,
) -> tuple[
    tuple[PcbPlacementCandidate, ...],
    tuple[ClusterInterfacePortfolioStateAudit, ...],
]:
    """Retain legal states by actual boundary topology, then stable score."""
    if MaximumStates <= 0:
        raise ValueError("interface placement state bound must be positive")
    Ordered = tuple(Candidates)
    Selected: list[PcbPlacementCandidate] = []
    Audits: list[ClusterInterfacePortfolioStateAudit] = []
    SeenTopologies: dict[str, PcbPlacementCandidate] = {}
    LegalDistinct: list[
        tuple[int, int, PcbPlacementCandidate]
    ] = []
    for CandidateOrdinal, Candidate in enumerate(Ordered):
        CandidateIndex = int(getattr(
            Candidate.JointPlacementState,
            "CandidateIndex",
            CandidateOrdinal,
        ))
        TopologyFingerprint = Candidate.InterfaceTopologyFingerprint
        if (
            Candidate.TopologyDemand is not None
            and Candidate.TopologyDemand
            .MandatoryAccessConflictResources > 0
        ):
            Audits.append(ClusterInterfacePortfolioStateAudit(
                StateIndex=CandidateIndex,
                Classification="mandatory-access-unsat",
                PlacementStateFingerprint=(
                    Candidate.PlacementFingerprint
                ),
                InterfaceTopologyFingerprint=TopologyFingerprint,
                Detail="mandatory capacity-one prescreen rejected state",
            ))
            continue
        if TopologyFingerprint in SeenTopologies:
            Audits.append(ClusterInterfacePortfolioStateAudit(
                StateIndex=CandidateIndex,
                Classification="duplicate-access-topology",
                PlacementStateFingerprint=(
                    Candidate.PlacementFingerprint
                ),
                InterfaceTopologyFingerprint=TopologyFingerprint,
                Detail=(
                    "same structural boundary ownership topology as "
                    f"{SeenTopologies[TopologyFingerprint].CandidateId}"
                ),
            ))
            continue
        SeenTopologies[TopologyFingerprint] = Candidate
        LegalDistinct.append((
            CandidateOrdinal,
            CandidateIndex,
            Candidate,
        ))
    if len(LegalDistinct) <= MaximumStates:
        SelectedPositions = tuple(range(len(LegalDistinct)))
    elif MaximumStates == 1:
        SelectedPositions = (0,)
    else:
        # The input is already score-ranked. Span the complete bounded search
        # result instead of truncating to six near-identical leading states;
        # this preserves the best state while sampling progressively broader
        # slot/transform choices under the unchanged six-state solve bound.
        SelectedPositions = tuple(
            Index * (len(LegalDistinct) - 1) // (MaximumStates - 1)
            for Index in range(MaximumStates)
        )
    SelectedPositionSet = frozenset(SelectedPositions)
    for Position, (
        _CandidateOrdinal,
        CandidateIndex,
        Candidate,
    ) in enumerate(LegalDistinct):
        TopologyFingerprint = Candidate.InterfaceTopologyFingerprint
        if Position not in SelectedPositionSet:
            Audits.append(ClusterInterfacePortfolioStateAudit(
                StateIndex=CandidateIndex,
                Classification="pruned-by-scoring-budget",
                PlacementStateFingerprint=(
                    Candidate.PlacementFingerprint
                ),
                InterfaceTopologyFingerprint=TopologyFingerprint,
                Detail="legal interface-distinct state exceeded fixed bound",
            ))
            continue
        Selected.append(Candidate)
        Audits.append(ClusterInterfacePortfolioStateAudit(
            StateIndex=CandidateIndex,
            Classification="retained-interface-distinct",
            PlacementStateFingerprint=Candidate.PlacementFingerprint,
            InterfaceTopologyFingerprint=TopologyFingerprint,
            Detail="retained for joint placement/interface solve",
        ))
    Audits.sort(key=lambda Audit: Audit.StateIndex)
    ClassifiedIndexes = {Audit.StateIndex for Audit in Audits}
    for MissingIndex in range(MaximumStates):
        if MissingIndex in ClassifiedIndexes:
            continue
        Audits.append(ClusterInterfacePortfolioStateAudit(
            StateIndex=MissingIndex,
            Classification="pruned-by-scoring-budget",
            Detail=(
                "candidate generator exhausted or was deadline-pruned "
                "before producing this bounded state"
            ),
        ))
    Audits.sort(key=lambda Audit: Audit.StateIndex)
    return tuple(Selected), tuple(Audits)

def SelectReleasableLocalClaimSignals(
    AffectedSignals: frozenset[str],
    Claims: tuple[Any, ...],
) -> frozenset[str]:
    """Return only affected signals that actually own local claims."""
    AvailableSignals = frozenset(Claim.Signal for Claim in Claims)
    return AffectedSignals & AvailableSignals
