"""Placement-flow result contracts and physical feedback cores."""

from __future__ import annotations

from dataclasses import (
    dataclass,
    replace,
)
from typing import (
    Any,
    Iterable,
    Mapping,
)
from Compiler.Cells.Library import (
    GetCellMacro,
)
from Compiler.Routing.Contracts.Placement import (
    ComponentRoutabilityCore,
)
from Compiler.Routing.Contracts.Results import (
    RoutedDesign,
)
from Compiler.Routing.Failures import (
    RoutingFailure,
    RoutingFailureReason,
    RoutingStageError,
)
from Compiler.Routing.Reliability import (
    BuildStableFingerprint,
)
from Compiler.Routing.ResourceGraph import (
    LocalRouteClaim,
)
from Compiler.Routing.Policy import (
    DefaultPhysicalDesignPolicy,
    PhysicalDesignPolicy,
)
from Compiler.Routing.Policy import (
    RoutingStrategy,
)
from Compiler.Routing.Technology import (
    DefaultRedstoneRoutingTechnology,
    RedstoneRoutingTechnology,
)
from Compiler.Placement.Rotation import (
    RotatedCellSize,
)
from Compiler.Placement.Geometry import (
    PlacedDesign,
)
from Compiler.Routing.Components.PhysicalPlanning import (
    SelectPhysicalAssemblyGlobalBoundaryPorts,
    SelectPhysicalComponentExactGlobalChannelSignals,
)
from .Preparation import (
    BuildClusterInterfacePlacementTopologyFingerprint,
    SummarizePrePlacementCapacityResults,
)
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .Candidates import (
        PcbPlacementCandidate,
    )


@dataclass(frozen=True)
class PcbProgress:
    Completed: int
    Total: int
    Workers: int
    Valid: int
    BestBlocks: int | None
    BestWidth: int | None
    BestDepth: int | None
    BestFootprint: int | None
    Failed: int
    Stage: str = "preparing routing"
    Unit: str = "routing passes"

@dataclass
class PcbResult:
    Placed: PlacedDesign
    Routed: RoutedDesign
    Footprint: int
    EstimatedBlocks: int
    Width: int
    Depth: int
    Policy: PhysicalDesignPolicy = DefaultPhysicalDesignPolicy
    Technology: RedstoneRoutingTechnology = DefaultRedstoneRoutingTechnology
    RequestedStrategy: str = RoutingStrategy.Default.value
    UsedStrategy: str = RoutingStrategy.Default.value
    FallbackUsed: bool = False
    FallbackReason: str | None = None
    PlanningContracts: dict[str, object] | None = None
    RejectedRewriteDiagnostics: dict[str, object] | None = None

def FreezePhysicalAssemblyGlobalChannels(
    Placed: PlacedDesign,
    Plan: Any,
    GlobalChannelDesign: RoutedDesign,
) -> PlacedDesign:
    """Materialize exact exterior routes as completed immutable claims."""
    Assignment = GlobalChannelDesign.RoutingAssignment
    if Assignment is None:
        raise ValueError(
            "physical global-channel handoff requires an assignment"
        )
    PortsBySignal = {
        Port.Signal: Port
        for Port in SelectPhysicalAssemblyGlobalBoundaryPorts(Plan)
    }
    SelectedCandidates = dict(Assignment.SelectedCandidates)
    ExpectedSignals = SelectPhysicalComponentExactGlobalChannelSignals(Plan)
    if frozenset(SelectedCandidates) != ExpectedSignals:
        raise ValueError(
            "physical global-channel claim identity mismatch"
        )
    FrozenClaims = tuple(
        LocalRouteClaim(
            Signal=Signal,
            ClusterId=-5,
            Root=PortsBySignal[Signal].Attachment,
            ConnectedTargets=tuple(sorted(Candidate.TargetPaths)),
            BoundaryNodes=tuple(sorted(Candidate.Nodes)),
            Nodes=Candidate.Nodes,
            Edges=Candidate.Edges,
            Claims=Candidate.Claims,
            RepeaterReservations=Candidate.RepeaterReservations,
            ExactRouteSignalBlocks=len(Candidate.Claims.WireCells),
            ExactRouteRefreshBlocks=len(
                Candidate.RepeaterReservations
            ),
            ExactRouteSupportBlocks=len(
                Candidate.Claims.SupportCells
            ),
        )
        for Signal, Candidate in sorted(SelectedCandidates.items())
    )
    return replace(
        Placed,
        LocalRouteClaims=tuple((
            *(Placed.LocalRouteClaims or ()),
            *FrozenClaims,
        )),
    )

@dataclass(frozen=True)
class PhysicalComponentPlacementFeedback:
    """Placement guidance derived from one complete physical port proof."""

    ProofFingerprint: str
    RelocationSignals: tuple[str, ...]
    SourcePlanFingerprint: str = ""
    DomainFingerprint: str = ""

    def ToDictionary(self) -> dict[str, object]:
        return {
            "ProofFingerprint": self.ProofFingerprint,
            "RelocationSignals": list(self.RelocationSignals),
            "SourcePlanFingerprint": self.SourcePlanFingerprint,
            "DomainFingerprint": self.DomainFingerprint,
        }

@dataclass(frozen=True)
class PhysicalInterfaceRepairCore:
    """Immutable, proof-qualified local-assembly or channel repair core."""

    RepairLevel: str
    Signals: tuple[str, ...]
    ClusterIds: tuple[int, ...]
    BoundaryClasses: tuple[str, ...]
    ForcedSeamClasses: tuple[tuple[str, str], ...]
    ProofKind: str
    SourceProofFingerprint: str
    EquivalentGeometryFingerprint: str
    RepairDomainFingerprint: str = ""
    AvailableSeamClassesBySignal: tuple[tuple[str, tuple[str, ...]], ...] = ()
    SelectedSeamAssignment: tuple[tuple[str, str], ...] = ()

    def ToDictionary(self) -> dict[str, object]:
        return {
            "RepairLevel": self.RepairLevel,
            "Signals": list(self.Signals),
            "ClusterIds": list(self.ClusterIds),
            "BoundaryClasses": list(self.BoundaryClasses),
            "ForcedSeamClasses": [list(Value) for Value in self.ForcedSeamClasses],
            "ProofKind": self.ProofKind,
            "SourceProofFingerprint": self.SourceProofFingerprint,
            "EquivalentGeometryFingerprint": self.EquivalentGeometryFingerprint,
            "RepairDomainFingerprint": self.RepairDomainFingerprint,
            "AvailableSeamClassesBySignal": [
                [Signal, list(Seams)]
                for Signal, Seams in self.AvailableSeamClassesBySignal
            ],
            "SelectedSeamAssignment": [
                list(Value) for Value in self.SelectedSeamAssignment
            ],
        }

@dataclass(frozen=True)
class PhysicalLocalFactorDiversificationCore:
    """One complete singleton assembly core eligible for a local ECO."""

    Signal: str
    SourceProofFingerprint: str
    LocalFactorIdentityFingerprint: str
    LocalGeometryFingerprint: str
    ClusterIds: tuple[int, ...]
    CoreFingerprint: str

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Signal": self.Signal,
            "SourceProofFingerprint": self.SourceProofFingerprint,
            "LocalFactorIdentityFingerprint": (
                self.LocalFactorIdentityFingerprint
            ),
            "LocalGeometryFingerprint": self.LocalGeometryFingerprint,
            "ClusterIds": list(self.ClusterIds),
            "CoreFingerprint": self.CoreFingerprint,
        }

@dataclass(frozen=True)
class PhysicalOwnedFrontierTopologyRepairCore:
    """A complete ownership proof that requires a component topology change.

    Unlike a port or seam core, this is admitted only when the unbound
    component forest has already proved that a signal has no owned frontier.
    Binding a different port cannot repair that contradiction.
    """

    Signals: tuple[str, ...]
    ProducerGateNames: tuple[str, ...]
    ConsumerGateNames: tuple[str, ...]
    ClusterIds: tuple[int, ...]
    TerminalPositions: tuple[tuple[int, int, int], ...]
    SourceProofFingerprint: str
    SourceTopologyFingerprint: str
    EquivalentTopologyFingerprint: str
    CoreFingerprint: str

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Signals": list(self.Signals),
            "ProducerGateNames": list(self.ProducerGateNames),
            "ConsumerGateNames": list(self.ConsumerGateNames),
            "ClusterIds": list(self.ClusterIds),
            "TerminalPositions": [list(Value) for Value in self.TerminalPositions],
            "SourceProofFingerprint": self.SourceProofFingerprint,
            "SourceTopologyFingerprint": self.SourceTopologyFingerprint,
            "EquivalentTopologyFingerprint": (
                self.EquivalentTopologyFingerprint
            ),
            "CoreFingerprint": self.CoreFingerprint,
        }

def BuildPhysicalOwnedFrontierTopologyRepairCore(
    Failure: RoutingFailure,
    SourceCandidate: PcbPlacementCandidate,
) -> PhysicalOwnedFrontierTopologyRepairCore | None:
    """Lift only a complete, port-independent owned-frontier contradiction."""
    Diagnostics = (
        Failure.Diagnostics
        if isinstance(Failure.Diagnostics, Mapping)
        else {}
    )
    Signals = tuple(sorted({
        str(Value)
        for Value in Diagnostics.get("LocalUnsatCoreSignals", ())
        if str(Value)
    }))
    SignalDiagnostics = Diagnostics.get("SignalDiagnostics", {})
    if not (
        Failure.Reason == RoutingFailureReason.ComponentPortAssignmentUnsatisfiable
        and Failure.Stage == "PhysicalComponentLocalEligibility"
        and Signals
        and Diagnostics.get("LocalUnsatCoreComplete", False)
        and Diagnostics.get("LocalUnsatCoreKind", "")
        == "tree-frontier-empty-owned-signal-domain"
        and isinstance(SignalDiagnostics, Mapping)
        and all(
            isinstance(SignalDiagnostics.get(Signal), Mapping)
            and SignalDiagnostics[Signal].get("Complete", False)
            and SignalDiagnostics[Signal].get(
                "OwnedSignalDomainContractIndependent", False
            )
            for Signal in Signals
        )
    ):
        return None
    ProofFingerprint = str(Diagnostics.get("LocalUnsatCoreFingerprint", ""))
    if not ProofFingerprint:
        return None
    Placed = SourceCandidate.Placement.Placed
    GateByName = {str(Gate.Name): Gate for Gate in Placed.PlacedGates}
    ProducerNames = tuple(sorted(
        Gate.Name
        for Gate in Placed.PlacedGates
        if set(map(str, Gate.Outputs)).intersection(Signals)
    ))
    ConsumerNames = tuple(sorted(
        Gate.Name
        for Gate in Placed.PlacedGates
        if set(map(str, Gate.Inputs)).intersection(Signals)
    ))
    if not ProducerNames or not ConsumerNames:
        return None
    ClusterByGate = {
        str(Name): ClusterId
        for ClusterId, Names in enumerate(SourceCandidate.Placement.Clusters)
        for Name in Names
    }
    ClusterIds = tuple(sorted({
        ClusterByGate[Name]
        for Name in (*ProducerNames, *ConsumerNames)
        if Name in ClusterByGate
    }))
    TerminalPositions = tuple(sorted({
        tuple(GateByName[Name].OutputPin)
        for Name in ProducerNames
        if GateByName[Name].OutputPin is not None
    }.union({
        tuple(GateByName[Name].InputPins[Index])
        for Name in ConsumerNames
        for Index, Signal in enumerate(GateByName[Name].Inputs)
        if str(Signal) in Signals
    })))
    TopologyFingerprint = str(
        SourceCandidate.InterfaceTopologyFingerprint
        or BuildClusterInterfacePlacementTopologyFingerprint(
            SourceCandidate.Placement,
            {},
        )
    )
    EquivalentTopologyFingerprint = BuildStableFingerprint((
        "owned-frontier-topology-equivalence-v1",
        Signals,
        ProducerNames,
        ConsumerNames,
        ClusterIds,
        TerminalPositions,
        TopologyFingerprint,
    ))
    return PhysicalOwnedFrontierTopologyRepairCore(
        Signals=Signals,
        ProducerGateNames=ProducerNames,
        ConsumerGateNames=ConsumerNames,
        ClusterIds=ClusterIds,
        TerminalPositions=TerminalPositions,
        SourceProofFingerprint=ProofFingerprint,
        SourceTopologyFingerprint=TopologyFingerprint,
        EquivalentTopologyFingerprint=EquivalentTopologyFingerprint,
        CoreFingerprint=BuildStableFingerprint((
            "physical-owned-frontier-topology-repair-core-v1",
            ProofFingerprint,
            EquivalentTopologyFingerprint,
        )),
    )

def BuildPhysicalLocalFactorDiversificationCore(
    Failure: RoutingFailure,
    SourceCandidate: PcbPlacementCandidate,
) -> PhysicalLocalFactorDiversificationCore | None:
    """Admit one singleton ECO only from a complete minimal assembly proof."""
    Diagnostics = (
        Failure.Diagnostics
        if isinstance(Failure.Diagnostics, Mapping)
        else {}
    )
    Signals = tuple(sorted({
        str(Value)
        for Value in Diagnostics.get("PortAssignmentUnsatCoreSignals", ())
        if str(Value)
    }))
    if not (
        Failure.Reason == RoutingFailureReason.ComponentPortAssignmentUnsatisfiable
        and Diagnostics.get("PortAssignmentProofComplete", False)
        and Diagnostics.get("PortAssignmentUnsatCoreMinimal", False)
        and len(Signals) == 1
    ):
        return None
    Signal = Signals[0]
    ProofFingerprint = str(
        Diagnostics.get("PortAssignmentUnsatCoreFingerprint", "")
    )
    DomainFingerprint = str(Diagnostics.get("DomainFingerprint", ""))
    if not ProofFingerprint or not DomainFingerprint:
        return None
    Claims = getattr(SourceCandidate.Placement.Placed, "LocalRouteClaims", ()) or ()
    ClusterIds = tuple(sorted({
        int(getattr(Claim, "ClusterId", -1))
        for Claim in Claims
        if str(getattr(Claim, "Signal", "")) == Signal
        and int(getattr(Claim, "ClusterId", -1)) >= 0
    }))
    LocalGeometryFingerprint = BuildCapacityRepairGeometryFingerprint(
        SourceCandidate,
        (Signal,),
    )
    return PhysicalLocalFactorDiversificationCore(
        Signal=Signal,
        SourceProofFingerprint=ProofFingerprint,
        LocalFactorIdentityFingerprint=BuildStableFingerprint((
            "singleton-local-factor-domain-v1",
            Signal,
            DomainFingerprint,
            LocalGeometryFingerprint,
        )),
        LocalGeometryFingerprint=LocalGeometryFingerprint,
        ClusterIds=ClusterIds,
        CoreFingerprint=BuildStableFingerprint((
            "singleton-local-factor-diversification-core-v1",
            Signal,
            ProofFingerprint,
            DomainFingerprint,
            LocalGeometryFingerprint,
            ClusterIds,
        )),
    )

def BuildCapacityRepairGeometryFingerprint(
    Candidate: PcbPlacementCandidate,
    Signals: Iterable[str],
) -> str:
    """Identify only the implicated local geometry, never a whole placement."""
    SignalSet = frozenset(map(str, Signals))
    Placed = Candidate.Placement.Placed
    Claims = getattr(Placed, "LocalRouteClaims", ()) or ()
    OutputPins = tuple(
        (
            str(Gate.Name),
            str(Signal),
            "output",
            tuple(Gate.OutputPin),
        )
        for Gate in getattr(Placed, "PlacedGates", ())
        if getattr(Gate, "OutputPin", None) is not None
        for Signal in getattr(Gate, "Outputs", ())
        if str(Signal) in SignalSet
    )
    InputPins = tuple(
        (
            str(Gate.Name),
            str(Signal),
            "input",
            tuple(Gate.InputPins[Index]),
        )
        for Gate in getattr(Placed, "PlacedGates", ())
        for Index, Signal in enumerate(getattr(Gate, "Inputs", ()))
        if str(Signal) in SignalSet
    )
    SignalPins = tuple(sorted((*OutputPins, *InputPins)))
    return BuildStableFingerprint((
        "physical-capacity-repair-geometry-v2",
        tuple(sorted(
            (
                str(Claim.Signal),
                int(getattr(Claim, "ClusterId", -1)),
                tuple(sorted(map(
                    tuple,
                    getattr(Claim, "Nodes", ()),
                ))),
            )
            for Claim in Claims
            if str(Claim.Signal) in SignalSet
        )),
        SignalPins,
    ))

def BuildPhysicalInterfaceRepairCore(
    Failure: RoutingFailure,
    SourceCandidate: PcbPlacementCandidate,
) -> PhysicalInterfaceRepairCore | None:
    """Lift only complete assembly/channel proofs into geometry guidance."""
    Diagnostics = Failure.Diagnostics if isinstance(Failure.Diagnostics, Mapping) else {}
    IsAssemblyCore = bool(
        Failure.Reason == RoutingFailureReason.ComponentPortAssignmentUnsatisfiable
        and Diagnostics.get("PortAssignmentProofComplete", False)
        and Diagnostics.get("PortAssignmentUnsatCoreMinimal", False)
    )
    IsChannelCore = bool(
        Failure.Reason == RoutingFailureReason.ComponentChannelCapacityUnsatisfiable
        and Diagnostics.get("GlobalPlanDomainComplete", False)
        and Diagnostics.get("CompleteAssignmentCutProof", False)
    )
    IsFeedthroughEndpointCore = bool(
        Failure.Reason == RoutingFailureReason.ComponentChannelCapacityUnsatisfiable
        and Failure.Stage == "PhysicalComponentAssemblyPlanning"
        and Diagnostics.get("FeedthroughCandidateDomainComplete", False)
        and Diagnostics.get("ComponentFabricConstructionComplete", False)
        and Diagnostics.get("OwnershipSearchComplete", False)
        and Diagnostics.get("FeedthroughEndpointPrescreenComplete", True)
    )
    if not (IsAssemblyCore or IsChannelCore or IsFeedthroughEndpointCore):
        return None
    RepairLevel = "local-assembly" if IsAssemblyCore else "channel-capacity"
    Signals = tuple(sorted({
        str(Value) for Value in (
            Diagnostics.get("PortAssignmentUnsatCoreSignals", ())
            if IsAssemblyCore else Failure.AffectedNets
        ) if str(Value)
    }))
    if not Signals:
        return None
    SeamClasses = tuple(sorted(
        (str(Value[0]), str(Value[1]))
        for Value in Diagnostics.get(
            "PortAssignmentUnsatCoreClause" if IsAssemblyCore
            else "LocalCapacityCoreClause",
            (),
        )
        if isinstance(Value, (tuple, list)) and len(Value) == 2
        and str(Value[0]) in Signals
    ))
    # A singleton with any seam choice is a symptom, not a geometry core.
    if IsAssemblyCore and len(Signals) == 1:
        return None
    ProofFingerprint = str(Diagnostics.get(
        "PortAssignmentUnsatCoreFingerprint" if IsAssemblyCore
        else "GlobalPlanDependencyFingerprint",
        "",
    )) or str(Diagnostics.get(
        "FeedthroughEndpointDomainFingerprint",
        "",
    )) or str(Diagnostics.get(
        "PhysicalAssemblyPlanFingerprint", "",
    ))
    if not ProofFingerprint:
        return None
    Claims = getattr(SourceCandidate.Placement.Placed, "LocalRouteClaims", ()) or ()
    ClusterIds = tuple(sorted({
        int(getattr(Claim, "ClusterId", -1))
        for Claim in Claims
        if str(getattr(Claim, "Signal", "")) in Signals
        and int(getattr(Claim, "ClusterId", -1)) >= 0
    }))
    BoundaryClasses = tuple(sorted({
        str(Value[1]) for Value in SeamClasses
    }))
    return PhysicalInterfaceRepairCore(
        RepairLevel=RepairLevel,
        Signals=Signals,
        ClusterIds=ClusterIds,
        BoundaryClasses=BoundaryClasses,
        ForcedSeamClasses=SeamClasses,
        ProofKind=(
            "complete-port-assignment-unsat-core"
            if IsAssemblyCore else (
                "complete-feedthrough-endpoint-domain"
                if IsFeedthroughEndpointCore
                else "complete-channel-capacity-core"
            )
        ),
        SourceProofFingerprint=ProofFingerprint,
        EquivalentGeometryFingerprint=BuildCapacityRepairGeometryFingerprint(
            SourceCandidate, Signals,
        ),
        RepairDomainFingerprint=BuildStableFingerprint((
            "physical-interface-repair-domain-v4",
            RepairLevel,
            Signals,
            ClusterIds,
            BoundaryClasses,
            SeamClasses,
            ProofFingerprint,
        )),
    )

def BuildSymbolicCapacityRepairEvidence(
    NoGoodDiagnostics: Mapping[str, object],
    PressureSignals: Iterable[str],
) -> dict[str, object]:
    """Keep only complete local-capacity proof data safe for placement."""
    Signals = tuple(sorted({
        str(Signal) for Signal in PressureSignals if str(Signal)
    }))
    ProofFingerprint = str(
        NoGoodDiagnostics.get("SymbolicCapacityProofFingerprint", "")
    )
    SeamClause = tuple(sorted(
        (str(Value[0]), str(Value[1]))
        for Value in NoGoodDiagnostics.get("LocalCapacityCoreClause", ())
        if isinstance(Value, (tuple, list)) and len(Value) == 2
        and str(Value[0]) in Signals
    ))
    if (
        not Signals
        or not ProofFingerprint
        or {Signal for Signal, _Seam in SeamClause} != set(Signals)
    ):
        return {}
    return {
        "SymbolicCapacityProofComplete": True,
        "SymbolicCapacityProofFingerprint": ProofFingerprint,
        "LocalCapacityCoreClause": [list(Value) for Value in SeamClause],
    }

def PreparedEligibilityHasDisjointCapacitySeams(
    Preparation: Any,
    Constraint: PhysicalInterfaceRepairCore,
) -> tuple[
    bool,
    str,
    tuple[tuple[str, str], ...],
    tuple[tuple[str, tuple[str, ...]], ...],
]:
    """Return the first complete capacity-one local seam assignment."""
    FactorsBySignal = dict(getattr(Preparation, "LocalAccessFactorsBySignal", ()))
    Domains = {
        Signal: tuple(sorted(
            FactorsBySignal.get(Signal, ()),
            key=lambda Factor: (
                str(Factor.SeamContractFingerprint),
                tuple(sorted(map(str, Factor.LocalClaims.ResourceIds))),
            ),
        ))
        for Signal in Constraint.Signals
    }
    AvailableSeamClasses = tuple(
        (Signal, tuple(
            str(Factor.SeamContractFingerprint)
            for Factor in Domains[Signal]
        ))
        for Signal in Constraint.Signals
    )
    if any(not Domains[Signal] for Signal in Constraint.Signals):
        return False, "", (), AvailableSeamClasses

    def Search(
        Remaining: tuple[str, ...],
        UsedClaims: frozenset[object],
        Selected: tuple[tuple[str, str], ...],
    ) -> tuple[tuple[str, str], ...] | None:
        if not Remaining:
            return Selected
        # Preserve the canonical signal order while trying each canonical
        # seam domain.  A smallest-domain heuristic is deterministic but can
        # return a later assignment, which is not the contract here.
        Signal = Remaining[0]
        NextRemaining = Remaining[1:]
        for Factor in Domains[Signal]:
            Claims = frozenset(Factor.LocalClaims.ResourceIds)
            if Claims.isdisjoint(UsedClaims):
                Result = Search(
                    NextRemaining,
                    UsedClaims | Claims,
                    (*Selected, (Signal, str(Factor.SeamContractFingerprint))),
                )
                if Result is not None:
                    return Result
        return None

    Assignment = Search(tuple(sorted(Constraint.Signals)), frozenset(), ())
    if Assignment is None:
        return False, "", (), AvailableSeamClasses
    OrderedAssignment = tuple(sorted(Assignment))
    return True, BuildStableFingerprint((
        Constraint.RepairDomainFingerprint,
        OrderedAssignment,
    )), OrderedAssignment, AvailableSeamClasses

def BuildComponentRoutabilityCore(
    Failure: RoutingFailure,
    *,
    PlacementStateFingerprint: str,
    ComponentStateFingerprint: str,
    DomainFingerprint: str,
    CoreFingerprint: str,
    Complete: bool,
) -> ComponentRoutabilityCore | None:
    """Freeze a complete ownership proof into placement-safe evidence."""
    if not Complete:
        return None
    Diagnostics = (
        Failure.Diagnostics
        if isinstance(Failure.Diagnostics, Mapping)
        else {}
    )
    PlacementPressureSignals = tuple(sorted({
        str(Signal)
        for Signal in Diagnostics.get(
            "PlacementInterfacePressureSignals",
            (),
        )
        if str(Signal)
    }))
    # A selected-contract capacity proof is narrower than the final
    # ownership UNSAT summary.  Preserve it as the feedback identity so a
    # NandNet28/NandNet29 seam core cannot be rewritten into the older broad
    # NandNet26 ownership core before placement sees it.
    EvidenceFingerprint = (
        BuildStableFingerprint((
            "physical-component-placement-pressure-v1",
            PlacementStateFingerprint,
            ComponentStateFingerprint,
            PlacementPressureSignals,
        ))
        if PlacementPressureSignals
        else str(
            Diagnostics.get("OwnershipUnsatCoreFingerprint", "")
            or Diagnostics.get("PortAssignmentUnsatCoreFingerprint", "")
            or Diagnostics.get("AuthoritativeCutAccessDomainFingerprint", "")
            or CoreFingerprint
        )
    )
    Signals = tuple(sorted({
        str(Signal)
        for Signal in (
            PlacementPressureSignals
            or Diagnostics.get("PortAssignmentUnsatCoreSignals", ())
            or Failure.AffectedNets
        )
        if str(Signal)
    }))
    if not EvidenceFingerprint or not Signals:
        return None
    return ComponentRoutabilityCore(
        CoreFingerprint=EvidenceFingerprint,
        Signals=Signals,
        PlacementStateFingerprint=PlacementStateFingerprint,
        ComponentStateFingerprint=ComponentStateFingerprint,
        DomainFingerprint=DomainFingerprint,
        BlockingResources=tuple(sorted(map(str, Failure.Resources))),
        BlockingPorts=tuple(sorted(Failure.Locations)),
    )

def BuildPhysicalComponentPlacementFeedback(
    Failure: RoutingFailure,
) -> PhysicalComponentPlacementFeedback | None:
    """Project a complete minimal port-unsat core onto placement guidance.

    This is deliberately separate from ``RoutingAssignmentCut``.  A local
    physical port proof says which component terminals need different escape
    geometry; it does not claim that the authoritative global assignment
    domain produced a conflict graph.
    """

    Diagnostics = (
        Failure.Diagnostics
        if isinstance(Failure.Diagnostics, Mapping)
        else {}
    )
    RoutabilityCore = Diagnostics.get("ComponentRoutabilityCore", {})
    CompleteRoutabilityCore = bool(
        isinstance(RoutabilityCore, Mapping)
        and RoutabilityCore.get("Complete", False)
        and RoutabilityCore.get("CoreFingerprint", "")
        and RoutabilityCore.get("Signals", ())
    )
    CompleteSingletonAccessCore = bool(
        Failure.Stage == "ComponentAccessCertification"
        and Diagnostics.get("Complete", False)
        and not Diagnostics.get("Feasible", True)
        and len(frozenset(map(
            str,
            Diagnostics.get("AffectedSignals", ()),
        ))) == 1
        and Diagnostics.get("CertificateFingerprint", "")
    )
    UnderlyingFailure = Diagnostics.get("UnderlyingFailure", {})
    UnderlyingDiagnostics = (
        UnderlyingFailure.get("Diagnostics", {})
        if isinstance(UnderlyingFailure, Mapping)
        else {}
    )
    PortalPreScreen = (
        UnderlyingDiagnostics.get("MandatoryPortalClaimPreScreen", {})
        if isinstance(UnderlyingDiagnostics, Mapping)
        else {}
    )
    AffectedSignals = frozenset(map(str, Failure.AffectedNets))
    CompleteGlobalKeepoutCore = bool(
        Failure.Reason
        == RoutingFailureReason.ComponentDetailedRoutingFailed
        and Failure.Stage == "ComponentGlobalKeepoutAdmission"
        and isinstance(UnderlyingFailure, Mapping)
        and UnderlyingFailure.get("Reason")
        == RoutingFailureReason.NoPinAccessPattern.value
        and UnderlyingFailure.get("Stage")
        == "NegotiatedDetailedRouting"
        and isinstance(PortalPreScreen, Mapping)
        and PortalPreScreen.get("Scope") == "complete-design"
        and AffectedSignals
        and AffectedSignals <= frozenset(map(
            str,
            PortalPreScreen.get("EmptyNetWidePortalTupleSignals", ()),
        ))
        and int(UnderlyingDiagnostics.get("RequestCount", -1)) == 0
        and int(UnderlyingDiagnostics.get("AttemptedRequestCount", -1)) == 0
    )
    if (
        not CompleteRoutabilityCore
        and not CompleteSingletonAccessCore
        and not CompleteGlobalKeepoutCore
        and (
        Failure.Reason
        != RoutingFailureReason.ComponentPortAssignmentUnsatisfiable
        or not Diagnostics.get("PortAssignmentProofComplete", False)
        or not Diagnostics.get("PortAssignmentUnsatCoreMinimal", False)
        )
    ):
        return None
    RelocationSignals = tuple(sorted({
        str(Signal)
        for Signal in (
            (
                RoutabilityCore.get("Signals", ())
                if CompleteRoutabilityCore
                else (
                    Failure.AffectedNets
                    if CompleteGlobalKeepoutCore
                    else Diagnostics.get("AffectedSignals", ())
                )
            )
            if (
                CompleteRoutabilityCore
                or CompleteSingletonAccessCore
                or CompleteGlobalKeepoutCore
            )
            else Diagnostics.get("PortAssignmentUnsatCoreSignals", ())
        )
        if str(Signal)
    }))
    if not RelocationSignals:
        return None
    SourcePlanFingerprint = str(
        Diagnostics.get("PhysicalAssemblyPlanFingerprint", "")
    )
    DomainFingerprint = str(
        RoutabilityCore.get("DomainFingerprint", "")
        if CompleteRoutabilityCore
        else Diagnostics.get("DomainFingerprint", "")
    )
    ProofFingerprint = str(
        RoutabilityCore.get("CoreFingerprint", "")
        if CompleteRoutabilityCore
        else Diagnostics.get(
            (
                "CertificateFingerprint"
                if CompleteSingletonAccessCore
                else (
                    "UnderlyingFailureFingerprint"
                    if CompleteGlobalKeepoutCore
                    else "PortAssignmentUnsatCoreFingerprint"
                )
            ),
            "",
        )
    ) or BuildStableFingerprint((
        (
            "physical-component-routability-core"
            if CompleteRoutabilityCore
            else (
                "physical-component-global-keepout-core"
                if CompleteGlobalKeepoutCore
                else "physical-component-port-unsat-core"
            )
        ),
        RelocationSignals,
        SourcePlanFingerprint,
        DomainFingerprint,
        UnderlyingFailure if CompleteGlobalKeepoutCore else (),
    ))
    return PhysicalComponentPlacementFeedback(
        ProofFingerprint=ProofFingerprint,
        RelocationSignals=RelocationSignals,
        SourcePlanFingerprint=SourcePlanFingerprint,
        DomainFingerprint=DomainFingerprint,
    )

def IsCompletePhysicalAssemblyUnsatisfiable(
    FailureReason: RoutingFailureReason,
    Diagnostics: Mapping[str, object],
) -> bool:
    """Return whether diagnostics contain an authoritative assembly proof.

    Proof completion is a property of the search result, not of the clock at
    the instant its caller classifies that result.  A planning deadline may
    expire immediately after the solver produces a complete certificate; it
    must not retroactively turn that certificate into an incomplete result.
    """

    return bool(
        FailureReason
        in {
            RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
            RoutingFailureReason.ComponentChannelCapacityUnsatisfiable,
        }
        and (
            Diagnostics.get("GlobalPlanDomainComplete", False)
            or Diagnostics.get("CompleteAssignmentCutProof", False)
            or Diagnostics.get("PortAssignmentProofComplete", False)
            or Diagnostics.get("ComponentFabricConstructionComplete", False)
        )
    )

def IsClusterInterfaceStateIncomplete(
    *,
    FailureReason: RoutingFailureReason,
    InterfaceDeadlineExpired: bool,
    ComponentSolveStatus: str,
    ExplicitCompleteUnsatProof: bool,
) -> bool:
    """Classify incompleteness without overriding an explicit proof."""

    if ExplicitCompleteUnsatProof:
        return False
    if ComponentSolveStatus:
        return bool(
            ComponentSolveStatus == "incomplete"
            or InterfaceDeadlineExpired
        )
    return bool(
        FailureReason
        in {
            RoutingFailureReason.ClusterInterfaceSolveIncomplete,
            RoutingFailureReason.PhysicalComponentAssemblyIncomplete,
            RoutingFailureReason.RuntimeBudgetExceeded,
        }
        or InterfaceDeadlineExpired
    )

def IsComponentKeepoutGlobalFailure(
    Failure: RoutingFailure,
    PhysicalAssemblyPlan: Any,
) -> bool:
    """Return whether a global net disproves the component envelope itself.

    Port reassignment can change the seam and the local template, but it
    cannot authorize an ordinary global signal to enter the plan's keepout.
    A bounded candidate proof that explicitly attributes starvation to the
    immutable routed component is therefore placement-level feedback, not a
    request to enumerate more port plans for the same envelope.
    """
    AffectedSignals = frozenset(map(str, Failure.AffectedNets))
    PortSignals = frozenset(
        str(Port.Signal)
        for Port in SelectPhysicalAssemblyGlobalBoundaryPorts(
            PhysicalAssemblyPlan
        )
    )
    Diagnostics = dict(Failure.Diagnostics or {})
    CompletePortalPrescreen = Diagnostics.get(
        "MandatoryPortalClaimPreScreen",
        {},
    )
    CompleteOrdinaryPortalCut = bool(
        Failure.Reason == RoutingFailureReason.NoPinAccessPattern
        and Failure.Stage == "NegotiatedDetailedRouting"
        and isinstance(CompletePortalPrescreen, dict)
        and CompletePortalPrescreen.get("Scope") == "complete-design"
        and int(CompletePortalPrescreen.get(
            "PreparedSignalCount",
            0,
        )) > 0
        and AffectedSignals
        <= frozenset(map(str, CompletePortalPrescreen.get(
            "EmptyNetWidePortalTupleSignals",
            (),
        )))
        and int(Diagnostics.get("RequestCount", -1)) == 0
        and int(Diagnostics.get("AttemptedRequestCount", -1)) == 0
        and bool(Diagnostics.get(
            "RoutedComponentGlobalHandoff",
            {},
        ).get("Enabled", False))
    )
    return bool(
        AffectedSignals
        and AffectedSignals.isdisjoint(PortSignals)
        and (
            CompleteOrdinaryPortalCut
            or (
                Failure.Stage == "Candidate"
                and Diagnostics.get("Action")
                == "advance-routed-component-global-starvation"
                and "immutable routed-component state blocked"
                in Failure.Detail
            )
        )
    )

def MeasurePcbDesign(
    Placed: PlacedDesign,
    Routed: RoutedDesign,
) -> tuple[int, int, int, int]:
    """Measure the final PCB footprint and emitted block estimate."""
    Positions = list(Routed.Wires) + list(Routed.Supports)
    for Gate in Placed.PlacedGates:
        Width, Depth = RotatedCellSize(Gate.Kind, Gate.Rotation)
        Positions.append((Gate.X, Gate.Y, Gate.Z))
        Positions.append((Gate.X + Width - 1, Gate.Y, Gate.Z + Depth - 1))
    if not Positions:
        return (0, 0, 0, 0)

    MinimumX = min(Position[0] for Position in Positions)
    MaximumX = max(Position[0] for Position in Positions)
    MinimumZ = min(Position[2] for Position in Positions)
    MaximumZ = max(Position[2] for Position in Positions)
    Width = MaximumX - MinimumX + 1
    Depth = MaximumZ - MinimumZ + 1
    Footprint = Width * Depth
    EstimatedBlocks = len(Routed.Wires) + sum(
        GetCellMacro(Gate.Kind).EstimatedBlocks
        for Gate in Placed.PlacedGates
    )
    return Footprint, EstimatedBlocks, Width, Depth

def PublishPlacementFlowResult(Context):
    if Context.RoutedCandidates:
        Context._Score, Context.SelectedCandidate, Context.Placement, Context.Routed, Context.SelectedCompositionDiagnostics = min(Context.RoutedCandidates, key=lambda Value: Value[0])
        Context.RoutingSpacing = Context.SelectedCandidate.RoutingSpacing
    if Context.Routed is None:
        if Context.LastCompletedAssignmentCutError is not None:
            Context.BaseFailure = Context.LastCompletedAssignmentCutError.Failure
        elif Context.LastStructuredRoutingError is not None:
            Context.BaseFailure = Context.LastStructuredRoutingError.Failure
        else:
            Context.BaseFailure = RoutingFailure(Reason=RoutingFailureReason.DetailedSearchExhausted, Stage='PlacementRouting', Detail=str(Context.LastRoutingError or 'all placement candidates failed'))
        Context.FailureDiagnostics = dict(Context.BaseFailure.Diagnostics or {})
        Context.FailureDiagnostics.update({'PlacementCandidates': Context.PlacementFeedback, 'PlacementGenerationFailures': Context.PlacementGenerationFailures, 'PlacementGenerationDecisions': Context.PlacementGenerationDecisions, 'PlacementAttempts': Context.PlacementAttemptFailures, 'JointPlacementStateEvents': Context.JointPlacementStateEvents, 'AssignmentCutHistory': [AssignmentCut.ToDictionary() for AssignmentCut in Context.PlacementAssignmentCutHistory], 'CurrentAssignmentCut': Context.CurrentPlacementAssignmentCut.ToDictionary() if Context.CurrentPlacementAssignmentCut is not None else None, 'ActivePlacementConstraints': Context.PlacementAssignmentConstraints.ToDictionary(), 'CoordinatedCandidateDiversificationSignals': sorted(Context.PlacementCoordinatedCandidateDiversificationSignals), 'Deadline': Context.Deadline.ToDictionary()})
        raise RoutingStageError(RoutingFailure(Reason=Context.BaseFailure.Reason, Stage=Context.BaseFailure.Stage, AffectedNets=Context.BaseFailure.AffectedNets, Resources=Context.BaseFailure.Resources, Locations=Context.BaseFailure.Locations, RepairActions=Context.BaseFailure.RepairActions, Detail=Context.BaseFailure.Detail, Diagnostics=Context.FailureDiagnostics)) from Context.LastRoutingError
    Context.Services.ValidateNandOnlyDesign(Context.Placement.Placed, Context.Netlist)
    Context.Routed.RoutingControlEffectiveness['PlacementFeedbackCandidates'] = Context.PlacementFeedback
    Context.Routed.RoutingControlEffectiveness['SelectedPlacementCandidate'] = Context.SelectedCandidate.ToDictionary() if Context.SelectedCandidate is not None else None
    Context.Routed.RoutingControlEffectiveness['TopologyDemandProfile'] = Context.TopologyDemand.ToDictionary()
    Context.Routed.RoutingControlEffectiveness['SelectedPlacementTopologyDemand'] = Context.SelectedCandidate.TopologyDemand.ToDictionary() if Context.SelectedCandidate is not None and Context.SelectedCandidate.TopologyDemand is not None else None
    Context.Routed.RoutingControlEffectiveness['SelectedRoutingSpacing'] = Context.RoutingSpacing
    Context.Routed.RoutingControlEffectiveness['RoutingPercentageSelection'] = {'Enabled': Context.RoutingPercentageSelectionEnabled, 'Configured': Context.Policy.MaterialObjective.OptimizeRoutingPercentage, 'MinimumNandCount': Context.Policy.MaterialObjective.MinimumRoutingPercentageSelectionNandCount, 'NandGateCount': Context.NandGateCount, 'CandidateCount': len(Context.RoutedCandidates), 'Selected': Context.SelectedCompositionDiagnostics if Context.RoutedCandidates else None, 'Candidates': [Diagnostics for _Score, _Candidate, _Placement, _Routed, Diagnostics in sorted(Context.RoutedCandidates, key=lambda Value: Value[0])]}
    Context.Routed.RoutingControlEffectiveness['PrePlacementCapacitySelection'] = {'GeometryDomainSize': len({Candidate.PlacementFingerprint for Candidate in Context.CandidateRecords}), 'EnvelopeDomainSize': len(Context.CandidateRecords), 'CapacitySolveCount': 1, 'RouteAttemptCount': 1, 'SelectedCandidateId': Context.SelectedCandidate.CandidateId if Context.SelectedCandidate is not None else '', 'SelectedPlacementFingerprint': Context.SelectedCandidate.PlacementFingerprint if Context.SelectedCandidate is not None else '', 'CandidateResults': SummarizePrePlacementCapacityResults(Context.PrePlacementTrackPreparations), 'RawTrackAssignmentSelection': Context.RawTrackAssignmentResult.ToDictionary() if Context.RawTrackAssignmentResult is not None else None, 'RawTrackAssignmentMaterializations': [Result.ToDictionary() for _CandidateId, Result in sorted(Context.RawTrackAssignmentMaterializations.items())], 'PreRouteFabricPortfolio': [Descriptor.ToDictionary() for _CandidateId, Descriptor in sorted(Context.PreRouteFabricDescriptorsByCandidateId.items())]}
    Context.PreRouteInterfaceSelectionArtifact = Context.PreRouteInterfaceResult.ToDictionary()
    Context.SelectedEnvelope = next((Template.RoutingEnvelope for Template in Context.PreRouteTemplates if Template.TemplateId == (Context.SelectedCandidate.CandidateId if Context.SelectedCandidate is not None else '')), None)
    Context.PreRouteInterfaceSelectionArtifact['SelectedRoutingEnvelope'] = Context.SelectedEnvelope.ToDictionary() if Context.SelectedEnvelope is not None else None
    Context.SelectedRingTemplate = next((Template for Template in Context.PreRouteTemplates if Template.TemplateId == (Context.SelectedCandidate.CandidateId if Context.SelectedCandidate is not None else '')), None)
    Context.PreRouteInterfaceSelectionArtifact['AccessRingTrackCount'] = Context.SelectedRingTemplate.AccessRingTrackCount if Context.SelectedRingTemplate is not None else 0
    Context.PreRouteInterfaceSelectionArtifact['AccessRingFingerprint'] = Context.SelectedRingTemplate.AccessRingFingerprint if Context.SelectedRingTemplate is not None else ''
    Context.Routed.RoutingControlEffectiveness['PreRouteInterfaceSelection'] = Context.PreRouteInterfaceSelectionArtifact
    Context.CandidateFingerprint = Context.RawTrackAssignmentResult.SelectionFingerprint if Context.RawTrackAssignmentResult is not None else Context.PreRouteInterfaceResult.SelectionFingerprint
    if not Context.CandidateFingerprint:
        raise ValueError('authoritative pre-route selection has no deterministic fingerprint')
    Context.Routed.RoutingControlEffectiveness['CandidateFingerprint'] = Context.CandidateFingerprint
    if Context.RawTrackAssignmentResult is not None:
        Context.Routed.RoutingControlEffectiveness['RawTrackAssignmentSelection'] = Context.RawTrackAssignmentResult.ToDictionary()
    Context.Routed.SupportBlock = Context.Technology.DefaultSupportBlock
    Context.Footprint, Context.EstimatedBlocks, Context.Width, Context.Depth = Context.Services.MeasurePcbDesign(Context.Placement.Placed, Context.Routed)
    Context.Snapshot = Context.Services.BuildLocalFirstSnapshot(Context.Placement, Context.Routed, LocalFanoutDistance=Context.Policy.Placement.LocalFanoutDistance, LocalRouteBudget=10)
    Context.PlanningContracts = Context.Snapshot.ToDictionary()
    Context.PlanningContracts['PackedNandClusters'] = [{'ClusterId': Cluster.ClusterId, 'MemberNands': list(Cluster.MemberNands), 'BoundarySignals': list(Cluster.BoundarySignals), 'InternalSignals': list(Cluster.InternalSignals), 'RelativePlacements': {Name: list(Value) for Name, Value in sorted(Cluster.RelativePlacements.items())}, 'DirectConnections': list(Cluster.DirectConnections), 'LocalClaimSignals': list(Cluster.LocalClaimSignals), 'BoundaryTerminals': [list(Position) for Position in Cluster.BoundaryTerminals], 'ExactLocalRoutingBlocks': Cluster.ExactLocalRoutingBlocks, 'GlobalEntrances': Cluster.GlobalEntrances, 'RejectionReasons': list(Cluster.RejectionReasons), 'StructuralSignature': Cluster.StructuralSignature, 'ReusedFromClusterId': Cluster.ReusedFromClusterId, 'StructuralMapping': dict(sorted((Cluster.StructuralMapping or {}).items())), 'StackId': Cluster.StackId, 'StackLevel': Cluster.StackLevel, 'BaseY': Cluster.BaseY, 'BoundaryDemand': dict(sorted((Cluster.BoundaryDemand or {}).items())), 'EstimatedCorridorLanes': Cluster.EstimatedCorridorLanes, 'LocalClaimCoverage': Cluster.LocalClaimCoverage, 'BoundaryDemandRecords': [{'Signal': Record.Signal, 'UnresolvedTargets': Record.UnresolvedTargets, 'RequiredPortalSlots': Record.RequiredPortalSlots, 'RequiredCorridorLanes': Record.RequiredCorridorLanes, 'PreferredBoundarySide': Record.PreferredBoundarySide} for Record in Cluster.BoundaryDemandRecords], 'BoundaryCapacityRecords': [{'BoundarySide': Record.BoundarySide, 'LegalPortalSlots': Record.LegalPortalSlots, 'LegalCorridorLanes': Record.LegalCorridorLanes, 'Overflow': Record.Overflow} for Record in Cluster.BoundaryCapacityRecords], 'BoundaryOverflow': Cluster.BoundaryOverflow, 'PinScarcityCount': Cluster.PinScarcityCount, 'OrientationRotation': Cluster.OrientationRotation, 'OrientationMirrorX': Cluster.OrientationMirrorX} for Cluster in Context.Placement.PackedClusters]
    Context.PlanningContracts['StructuralReuse'] = {'Enabled': Context.Policy.NandPacking.EnableStructuralReuse, 'ReuseScope': 'relative-layout-with-joint-world-transform', 'JointClusterOrientationEnabled': Context.Policy.NandPacking.EnableJointClusterOrientation, 'LocalRoutesRecomputedAndValidated': True, 'UniqueTemplates': len({Cluster.StructuralSignature for Cluster in Context.Placement.PackedClusters if Cluster.StructuralSignature}), 'ReusedClusters': sum((Cluster.ReusedFromClusterId is not None for Cluster in Context.Placement.PackedClusters))}
    Context.PlanningContracts['LocalRouteClaims'] = [{'Signal': Claim.Signal, 'ClusterId': Claim.ClusterId, 'Root': list(Claim.Root), 'ConnectedTargets': [list(Value) for Value in Claim.ConnectedTargets], 'BoundaryNodes': [list(Value) for Value in Claim.BoundaryNodes], 'NodeCount': len(Claim.Nodes), 'EdgeCount': len(Claim.Edges), 'PreOwnedResourceCount': len(Claim.Claims.ResourceIds), 'ExactRouteSignalBlocks': Claim.ExactRouteSignalBlocks, 'ExactRouteRefreshBlocks': Claim.ExactRouteRefreshBlocks, 'ExactRouteSupportBlocks': Claim.ExactRouteSupportBlocks} for Claim in Context.Placement.Placed.LocalRouteClaims]
    Context.PlanningContracts['LocalRouteDiagnostics'] = Context.Placement.Placed.LocalRouteDiagnostics or {}
    Context.PlanningContracts['ClusterBoundaryLeases'] = {'Enabled': bool(getattr(Context.Placement, 'ClusterBoundaryLeaseRequests', ())), 'LeaseExtent': 'terminal-access-plus-first-routing-segment', 'Requests': [Request.ToDictionary() for Request in getattr(Context.Placement, 'ClusterBoundaryLeaseRequests', ())]}
    Context.PlanningContracts['ClusterLocalRouteTemplates'] = {'Enabled': bool(getattr(Context.Placement, 'ClusterLocalRouteTemplates', ())), 'Templates': [Template.ToDictionary() for Template in getattr(Context.Placement, 'ClusterLocalRouteTemplates', ())]}
    Context.PlanningContracts['TopologyDemandProfile'] = Context.TopologyDemand.ToDictionary()
    Context.PlanningContracts['SelectedPlacementTopologyDemand'] = Context.SelectedCandidate.TopologyDemand.ToDictionary() if Context.SelectedCandidate is not None and Context.SelectedCandidate.TopologyDemand is not None else None
    Context.PlanningContracts['RoutingDemandEstimate'] = Context.Routed.RoutingControlEffectiveness.get('RoutingDemandEstimate', {})
    Context.PlanningContracts['DerivedRoutingBudget'] = Context.Routed.RoutingControlEffectiveness.get('DerivedRoutingBudget', {})
    Context.PlanningContracts['PortalReservations'] = Context.Routed.RoutingControlEffectiveness.get('PortalReservations', [])
    if Context.Deadline.IsExpired() and Context.RoutedCandidates:
        Context.Routed.RoutingControlEffectiveness['RoutingPercentageSelection']['DeadlineLimited'] = True
    else:
        Context.Deadline.RaiseIfExpired('RoutingFinalization')
    Context.Routed.RoutingControlEffectiveness['Deadline'] = Context.Deadline.ToDictionary()
    Context.Result = PcbResult(Placed=Context.Placement.Placed, Routed=Context.Routed, Footprint=Context.Footprint, EstimatedBlocks=Context.EstimatedBlocks, Width=Context.Width, Depth=Context.Depth, Policy=Context.Policy, Technology=Context.Technology, RequestedStrategy=Context.RequestedStrategy.value, UsedStrategy=Context.UsedStrategy.value, PlanningContracts=Context.PlanningContracts)
    if Context.ProgressCallback is not None:
        Context.ProgressCallback(PcbProgress(Completed=1, Total=1, Workers=0, Valid=1, BestBlocks=Context.EstimatedBlocks, BestWidth=Context.Width, BestDepth=Context.Depth, BestFootprint=Context.Footprint, Failed=0, Stage='routing complete'))
    return Context.Result
